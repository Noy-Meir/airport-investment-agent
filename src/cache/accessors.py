"""
Cache-through read accessors over data/cache.db.

Every function returns the uniform envelope required by CLAUDE.md:
    {result, method, caveats, source, confidence}
All SQL is parameterized -- no raw user text (e.g. an airport code typed by
a user) is ever interpolated into a query string. Airport codes are
validated against the cached airport universe before use; an unknown code
raises AirportNotFoundError rather than silently returning nothing.

No imputation: any field that is NULL in the cache stays NULL. Sums exclude
NULL rows rather than treating them as zero, and every caveat list says how
many rows were excluded and why.
"""

from datetime import datetime, timezone

from src.cache.config import (
    CARGO_CLASSES,
    HUB_TIER_THRESHOLDS,
    HUB_TIER_YEAR,
    LONG_HAUL_THRESHOLDS_MI,
    NEW_ENGLAND_STATES,
    PASSENGER_CLASSES,
    VOLUME_FLOOR_PAX,
    VOLUME_FLOOR_YEAR,
)

CLASS_GROUPS = {
    "all": PASSENGER_CLASSES | CARGO_CLASSES,
    "passenger": PASSENGER_CLASSES,
    "cargo": CARGO_CLASSES,
}

SOURCE_A_SUM_FIELDS = [
    "total_departures", "total_passengers", "total_seats",
    "domestic_departures", "domestic_passengers", "domestic_seats",
    "intl_departures", "intl_passengers", "intl_seats",
]


class AirportNotFoundError(Exception):
    pass


def _envelope(result, method, caveats, source, confidence):
    return {
        "result": result,
        "method": method,
        "caveats": caveats,
        "source": source,
        "confidence": confidence,
    }


def _source_stamp(cursor_source, cursor_fetched_at):
    if cursor_fetched_at is None:
        return "no cached data"
    return f"{cursor_source}, cached {cursor_fetched_at}"


def _sum_excluding_null(rows, field):
    """Sums `field` over rows, skipping NULLs rather than treating them as 0. Returns (sum_or_None, excluded_count)."""
    present = [r[field] for r in rows if r[field] is not None]
    excluded = len(rows) - len(present)
    return (sum(present) if present else None), excluded


def _ratio(num, denom, mult=1.0, digits=2):
    """num/denom*mult, rounded -- None (not 0) if denom is falsy or num is None, never imputing a missing numerator as 0."""
    if not denom or num is None:
        return None
    return round(mult * num / denom, digits)


def validate_airport_code(conn, code):
    """
    Raises AirportNotFoundError with a clear message if `code` is not a
    known US airport (present in either cached reference table). Returns
    the normalized (uppercased) code on success. Parameterized -- a
    SQL-injection-shaped string is just treated as an unknown code.
    """
    if not code or not isinstance(code, str):
        raise AirportNotFoundError(f"airport code must be a non-empty string, got {code!r}")
    code = code.strip().upper()
    row = conn.execute(
        "SELECT 1 FROM ourairports WHERE iata_code = ? "
        "UNION SELECT 1 FROM source_a_airport_month WHERE origin_airport_code = ? LIMIT 1",
        (code, code),
    ).fetchone()
    if row is None:
        raise AirportNotFoundError(
            f"{code!r} is not a known US airport code in the cache "
            "(checked OurAirports reference and T-100 airport-month data). "
            "Not guessing -- re-check the code or rebuild the cache."
        )
    return code


def get_airport_year_totals(conn, airport_code, year):
    """Sum of source-A airport-month fields for one airport over one calendar year. NULL months are excluded, not zeroed."""
    code = validate_airport_code(conn, airport_code)
    rows = conn.execute(
        f"""
        SELECT {", ".join(SOURCE_A_SUM_FIELDS)}, source, fetched_at
        FROM source_a_airport_month
        WHERE origin_airport_code = ? AND year = ?
        """,
        (code, year),
    ).fetchall()
    if not rows:
        return _envelope(
            None, "sum(source_a_airport_month) over calendar year, excluding NULL months",
            [f"no source-A rows cached for {code} {year}"], "no cached data", "low",
        )
    totals = {"months_present": len(rows)}
    caveats = []
    for field in SOURCE_A_SUM_FIELDS:
        total, excluded = _sum_excluding_null(rows, field)
        totals[field] = total
        if excluded:
            caveats.append(f"{field}: excluded {excluded}/{len(rows)} month(s) with NULL value from the sum")
    if len(rows) < 12:
        caveats.append(f"only {len(rows)}/12 months cached for {year} -- not a full calendar year")
    return _envelope(
        totals, "sum(source_a_airport_month) over calendar year, excluding NULL months", caveats,
        _source_stamp(rows[0]["source"], rows[0]["fetched_at"]), "high" if len(rows) == 12 else "medium",
    )


def get_long_haul_share(conn, origin, year, thresholds=LONG_HAUL_THRESHOLDS_MI, class_group="all"):
    """
    % of DEPARTURES_PERFORMED on routes >= each threshold mile distance,
    weighted by departures, for one origin/year/CLASS-group, from the
    route-level T-100 aggregate cache. Rows with NULL distance (no real
    distance ever recorded for that combo) are excluded from both the
    numerator and the denominator. Rows with distance == 0 where
    origin == dest are real same-airport positioning/sightseeing flights
    and are kept as short-haul; rows with distance == 0 where origin != dest
    are a data error and are excluded, reported separately in caveats.
    """
    code = validate_airport_code(conn, origin)
    if class_group not in CLASS_GROUPS:
        raise ValueError(f"class_group must be one of {sorted(CLASS_GROUPS)}, got {class_group!r}")
    classes = CLASS_GROUPS[class_group]
    placeholders = ",".join("?" for _ in classes)
    rows = conn.execute(
        f"""
        SELECT distance, departures_performed, dest, source, fetched_at
        FROM t100_route_agg
        WHERE origin = ? AND year = ? AND class IN ({placeholders})
        """,
        (code, year, *classes),
    ).fetchall()
    if not rows:
        return _envelope(
            None, "route-level T-100 (source B), departures-weighted share by DISTANCE threshold",
            [f"no route-level T-100 rows cached for {code} {year} class_group={class_group}"],
            "no cached data", "low",
        )

    source_stamp = _source_stamp(rows[0]["source"], rows[0]["fetched_at"])
    null_dep_rows = [r for r in rows if r["departures_performed"] is None]
    rows = [r for r in rows if r["departures_performed"] is not None]

    missing_rows = [r for r in rows if r["distance"] is None]
    zero_error_rows = [r for r in rows if r["distance"] is not None and r["distance"] == 0 and r["dest"] != code]
    valid_rows = [
        r for r in rows
        if r["distance"] is not None and (r["distance"] != 0 or r["dest"] == code)
    ]
    missing_departures = sum(r["departures_performed"] for r in missing_rows)
    zero_error_departures = sum(r["departures_performed"] for r in zero_error_rows)
    same_airport_zero_departures = sum(
        r["departures_performed"] for r in valid_rows if r["distance"] == 0
    )

    total = sum(r["departures_performed"] for r in valid_rows)
    result = {}
    for thr in thresholds:
        if total == 0:
            result[thr] = None
            continue
        long_deps = sum(r["departures_performed"] for r in valid_rows if r["distance"] >= thr)
        result[thr] = round(100.0 * long_deps / total, 1)

    caveats = [f"class_group={class_group} ({sorted(CLASS_GROUPS[class_group])}), weighted by DEPARTURES_PERFORMED"]
    if null_dep_rows:
        caveats.append(
            f"excluded {len(null_dep_rows)} (origin,dest,carrier) combo(s) with missing DEPARTURES_PERFORMED "
            "from both numerator and denominator"
        )
    if missing_rows:
        caveats.append(
            f"excluded {len(missing_rows)} (origin,dest,carrier) combo(s) with missing DISTANCE, "
            f"totaling {missing_departures:,.0f} departures, from both numerator and denominator"
        )
    if zero_error_rows:
        caveats.append(
            f"excluded {len(zero_error_rows)} (origin,dest,carrier) combo(s) with 0-mile DISTANCE between "
            f"different airports (data error), totaling {zero_error_departures:,.0f} departures, from both "
            f"numerator and denominator"
        )
    if same_airport_zero_departures:
        caveats.append(
            f"included {same_airport_zero_departures:,.0f} departures on 0-mile same-airport "
            f"(origin == dest) combos as short-haul"
        )
    return _envelope(
        {"shares_pct": result, "total_departures": total},
        "route-level T-100 (source B), departures-weighted share by DISTANCE threshold",
        caveats, source_stamp, "high",
    )


def list_new_england_airports(conn, floor=VOLUME_FLOOR_PAX, year=VOLUME_FLOOR_YEAR):
    """
    New England = CT/ME/MA/NH/RI/VT (OurAirports iso_region), intersected
    with source-A airports whose CY `year` total_passengers >= `floor`.
    Built in code, not from the scheduled_service flag alone -- see
    docs/DECISIONS.md. Months with NULL total_passengers are excluded from
    the sum, not zeroed.
    """
    pax_by_code, excluded = _pax_by_airport_for_year(conn, year)
    return _region_list(conn, pax_by_code, floor, source_note=f"CY{year} total_passengers (excluded {excluded} NULL month-rows)")


def _pax_by_airport_for_year(conn, year):
    rows = conn.execute(
        "SELECT origin_airport_code, total_passengers FROM source_a_airport_month WHERE year = ?",
        (year,),
    ).fetchall()
    by_code = {}
    excluded = 0
    for r in rows:
        if r["total_passengers"] is None:
            excluded += 1
            continue
        by_code[r["origin_airport_code"]] = by_code.get(r["origin_airport_code"], 0) + r["total_passengers"]
    return by_code, excluded


def _region_list(conn, pax_by_code, floor, source_note):
    regions = [f"US-{st}" for st in NEW_ENGLAND_STATES]
    placeholders = ",".join("?" for _ in regions)
    oa_rows = conn.execute(
        f"SELECT iata_code, name, iso_region, source, fetched_at FROM ourairports WHERE iso_region IN ({placeholders})",
        regions,
    ).fetchall()

    above = []
    for r in oa_rows:
        pax = pax_by_code.get(r["iata_code"])
        if pax is not None and pax >= floor:
            above.append({"code": r["iata_code"], "name": r["name"], "state": r["iso_region"][3:], "passengers": pax})
    above.sort(key=lambda x: x["code"])

    source = "no cached data" if not oa_rows else f"{oa_rows[0]['source']}, cached {oa_rows[0]['fetched_at']} + {source_note}"
    return _envelope(
        above,
        f"OurAirports iso_region in {NEW_ENGLAND_STATES} intersected with {source_note} >= {floor:,}",
        [f"volume floor {floor:,} is provisional pending trailing-12-month confirmation (see docs/DECISIONS.md)"],
        source, "medium",
    )


def compute_hub_tiers(conn, year=HUB_TIER_YEAR, thresholds=HUB_TIER_THRESHOLDS):
    """
    Hub tier by share of national CY `year` total_passengers (source A),
    thresholds as a fraction of the national total. Large/medium/small are
    mutually exclusive, in descending threshold order. Months with NULL
    total_passengers are excluded from the sum, not zeroed.
    """
    pax_by_code, excluded = _pax_by_airport_for_year(conn, year)
    row = conn.execute(
        "SELECT source, fetched_at FROM source_a_airport_month WHERE year = ? LIMIT 1", (year,)
    ).fetchone()
    if row is None:
        return _envelope(None, "share of national CY total_passengers", [f"no source-A rows cached for {year}"], "no cached data", "low")
    caveats = [f"excluded {excluded} month-row(s) with NULL total_passengers from the sums"] if excluded else []
    return _envelope(
        *_hub_tier_result(pax_by_code, thresholds, f"CY{year} total_passengers"),
        caveats, _source_stamp(row["source"], row["fetched_at"]), "high",
    )


def _hub_tier_result(pax_by_code, thresholds, label):
    national = sum(pax_by_code.values())
    tiers = {"large": [], "medium": [], "small": []}
    ordered = sorted(thresholds.items(), key=lambda kv: -kv[1])
    for code, pax in pax_by_code.items():
        share = pax / national if national else 0
        for tier_name, thresh in ordered:
            if share >= thresh:
                tiers[tier_name].append({"code": code, "passengers": pax, "share_pct": round(100 * share, 4)})
                break
    for t in tiers:
        tiers[t].sort(key=lambda x: -x["passengers"])
    result = {
        "national_total_passengers": national, "tiers": tiers,
        "counts": {k: len(v) for k, v in tiers.items()},
    }
    method = f"share of national {label}, thresholds {thresholds}"
    return result, method


# ---------------------------------------------------------------------------
# Trailing-12-month (TTM) accessors
# ---------------------------------------------------------------------------

def get_latest_cached_month(conn):
    """Returns (year, month) of the most recent source-A row in the cache, or None if empty."""
    row = conn.execute("SELECT MAX(year * 100 + month) AS ym FROM source_a_airport_month").fetchone()
    if row is None or row["ym"] is None:
        return None
    ym = int(row["ym"])
    return ym // 100, ym % 100


def _trailing_12_window(end_year, end_month):
    months = []
    y, m = end_year, end_month
    for _ in range(12):
        months.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return sorted(months)


def get_ttm_totals(conn, airport_code, end_month=None):
    """
    Trailing-12-month sums for one airport, ending at `end_month` (an
    (year, month) tuple) or, if not given, at the latest month present in
    the cache for ALL airports (reported in `as_of`). NULL months are
    excluded from each field's sum, not zeroed.
    """
    code = validate_airport_code(conn, airport_code)
    if end_month is None:
        latest = get_latest_cached_month(conn)
        if latest is None:
            return _envelope(None, "sum(source_a_airport_month) over trailing 12 months, excluding NULL months", ["cache is empty"], "no cached data", "low")
        end_year, end_m = latest
    else:
        end_year, end_m = end_month

    window = _trailing_12_window(end_year, end_m)
    ym_values = [y * 100 + m for y, m in window]
    placeholders = ",".join("?" for _ in ym_values)
    rows = conn.execute(
        f"""
        SELECT {", ".join(SOURCE_A_SUM_FIELDS)}, year, month, source, fetched_at
        FROM source_a_airport_month
        WHERE origin_airport_code = ? AND (year * 100 + month) IN ({placeholders})
        """,
        (code, *ym_values),
    ).fetchall()

    as_of = f"{end_year:04d}-{end_m:02d}"
    window_start = f"{window[0][0]:04d}-{window[0][1]:02d}"
    if not rows:
        return _envelope(
            None, "sum(source_a_airport_month) over trailing 12 months, excluding NULL months",
            [f"no source-A rows cached for {code} in the {window_start}..{as_of} window"], "no cached data", "low",
        )

    totals = {"months_present": len(rows), "as_of": as_of, "window_start": window_start}
    caveats = [f"trailing 12 months ending {as_of} (latest month cached for any airport)"]
    for field in SOURCE_A_SUM_FIELDS:
        total, excluded = _sum_excluding_null(rows, field)
        totals[field] = total
        if excluded:
            caveats.append(f"{field}: excluded {excluded}/{len(rows)} month(s) with NULL value from the sum")
    if len(rows) < 12:
        caveats.append(f"only {len(rows)}/12 months cached in this window")
    return _envelope(
        totals, "sum(source_a_airport_month) over trailing 12 months, excluding NULL months", caveats,
        _source_stamp(rows[0]["source"], rows[0]["fetched_at"]), "high" if len(rows) == 12 else "medium",
    )


def _pax_by_airport_ttm(conn, end_month=None):
    if end_month is None:
        latest = get_latest_cached_month(conn)
        if latest is None:
            return {}, 0, None
        end_year, end_m = latest
    else:
        end_year, end_m = end_month
    window = _trailing_12_window(end_year, end_m)
    ym_values = [y * 100 + m for y, m in window]
    placeholders = ",".join("?" for _ in ym_values)
    rows = conn.execute(
        f"SELECT origin_airport_code, total_passengers FROM source_a_airport_month WHERE (year * 100 + month) IN ({placeholders})",
        ym_values,
    ).fetchall()
    by_code = {}
    excluded = 0
    for r in rows:
        if r["total_passengers"] is None:
            excluded += 1
            continue
        by_code[r["origin_airport_code"]] = by_code.get(r["origin_airport_code"], 0) + r["total_passengers"]
    as_of = f"{end_year:04d}-{end_m:02d}"
    return by_code, excluded, as_of


def compute_hub_tiers_ttm(conn, end_month=None, thresholds=HUB_TIER_THRESHOLDS):
    """Same as compute_hub_tiers, but over the trailing 12 months ending at the latest cached month (or `end_month`)."""
    pax_by_code, excluded, as_of = _pax_by_airport_ttm(conn, end_month)
    if as_of is None:
        return _envelope(None, "share of national TTM total_passengers", ["cache is empty"], "no cached data", "low")
    caveats = [f"trailing 12 months ending {as_of}"]
    if excluded:
        caveats.append(f"excluded {excluded} month-row(s) with NULL total_passengers from the sums")
    result, method = _hub_tier_result(pax_by_code, thresholds, f"TTM total_passengers ending {as_of}")
    row = conn.execute("SELECT source, fetched_at FROM source_a_airport_month ORDER BY fetched_at DESC LIMIT 1").fetchone()
    return _envelope(result, method, caveats, _source_stamp(row["source"], row["fetched_at"]) if row else "no cached data", "high")


def list_new_england_airports_ttm(conn, end_month=None, floor=VOLUME_FLOOR_PAX):
    """Same as list_new_england_airports, but over the trailing 12 months ending at the latest cached month (or `end_month`)."""
    pax_by_code, excluded, as_of = _pax_by_airport_ttm(conn, end_month)
    if as_of is None:
        return _envelope(None, "OurAirports intersected with TTM total_passengers", ["cache is empty"], "no cached data", "low")
    return _region_list(conn, pax_by_code, floor, source_note=f"TTM total_passengers ending {as_of} (excluded {excluded} NULL month-rows)")


OTP_SUM_FIELDS = [
    "n_flights", "n_cancelled", "n_diverted",
    "n_taxi_out_obs", "sum_taxi_out",
    "n_dep_delay_obs", "sum_dep_delay_min", "n_dep_del15",
    "n_arr_delay_obs", "sum_arr_delay_min", "n_arr_del15",
]


def get_congestion_month(conn, code, year, month):
    """
    Same metrics as get_congestion_ttm (flights, cancellation rate, mean
    taxi-out, mean dep delay, %dep-delay>=15min) but for a single
    (year, month) rather than a trailing-12-month window. Used for
    reconciliation against single-month spike numbers (see
    scripts/build_cache.py report_otp_reconciliation).
    """
    airport_code = validate_airport_code(conn, code)
    method = "sum/count(otp_airport_month) for a single month"
    row = conn.execute(
        f"SELECT {', '.join(OTP_SUM_FIELDS)}, source, fetched_at FROM otp_airport_month "
        "WHERE origin = ? AND year = ? AND month = ?",
        (airport_code, year, month),
    ).fetchone()
    if row is None:
        return _envelope(None, method, [f"no OTP row cached for {airport_code} {year}-{month:02d}"], "no cached data", "low")
    result = {
        "flights": row["n_flights"],
        "cancellation_rate_pct": round(100.0 * row["n_cancelled"] / row["n_flights"], 2) if row["n_flights"] else None,
        "mean_taxi_out_min": round(row["sum_taxi_out"] / row["n_taxi_out_obs"], 2) if row["n_taxi_out_obs"] else None,
        "mean_dep_delay_min": round(row["sum_dep_delay_min"] / row["n_dep_delay_obs"], 2) if row["n_dep_delay_obs"] else None,
        "pct_dep_delay_ge15min": round(100.0 * row["n_dep_del15"] / row["n_dep_delay_obs"], 2) if row["n_dep_delay_obs"] else None,
    }
    caveats = ["OTP covers domestic reporting carriers only -- no freighters, no international flights"]
    return _envelope(result, method, caveats, _source_stamp(row["source"], row["fetched_at"]), "high")


def get_latest_cached_otp_month(conn):
    """Returns (year, month) of the most recent otp_airport_month row, or None if empty."""
    row = conn.execute("SELECT MAX(year * 100 + month) AS ym FROM otp_airport_month").fetchone()
    if row is None or row["ym"] is None:
        return None
    ym = int(row["ym"])
    return ym // 100, ym % 100


def get_congestion_ttm(conn, code, end_month=None):
    """
    Congestion metrics for one airport over the trailing 12 months ending at
    `end_month` ((year, month) tuple) or, if not given, at the latest month
    present in otp_airport_month for ANY airport (reported in `as_of`).
    Computed from otp_airport_month sums/counts (never from re-derived
    per-flight data): flights, cancellation rate, mean taxi-out, mean
    departure delay, % departures delayed >=15 min.

    Caveats always note OTP's scope (domestic reporting carriers only, no
    freighters, no international). Also reports `coverage`: OTP flight count
    as a % of T-100 (source A) domestic_departures, summed over the months
    where BOTH datasets have a cached row for this airport (the overlap),
    naming those months explicitly -- not assumed to be the full window.
    T-100 domestic_departures includes freighters and carriers that do not
    report to OTP, so this coverage ratio understates how much *passenger*
    traffic OTP actually covers -- see docs/DECISIONS.md.
    """
    airport_code = validate_airport_code(conn, code)
    method = (
        "sum/count(otp_airport_month) over trailing 12 months, "
        "cancellation rate / mean taxi-out / mean dep delay / %dep-delay>=15min "
        "derived from those sums/counts (never re-averaged from per-flight data)"
    )

    if end_month is None:
        latest = get_latest_cached_otp_month(conn)
        if latest is None:
            return _envelope(None, method, ["no OTP data cached"], "no cached data", "low")
        end_year, end_m = latest
    else:
        end_year, end_m = end_month

    window = _trailing_12_window(end_year, end_m)
    ym_values = [y * 100 + m for y, m in window]
    placeholders = ",".join("?" for _ in ym_values)
    rows = conn.execute(
        f"""
        SELECT {", ".join(OTP_SUM_FIELDS)}, year, month, source, fetched_at
        FROM otp_airport_month
        WHERE origin = ? AND (year * 100 + month) IN ({placeholders})
        """,
        (airport_code, *ym_values),
    ).fetchall()

    as_of = f"{end_year:04d}-{end_m:02d}"
    window_start = f"{window[0][0]:04d}-{window[0][1]:02d}"
    if not rows:
        return _envelope(
            None, method,
            [f"no OTP rows cached for {airport_code} in the {window_start}..{as_of} window"],
            "no cached data", "low",
        )

    sums = {}
    null_field_caveats = []
    for f in OTP_SUM_FIELDS:
        total, excluded = _sum_excluding_null(rows, f)
        sums[f] = total
        if excluded:
            null_field_caveats.append(f"{f}: excluded {excluded}/{len(rows)} month(s) with NULL value from the sum")

    result = {
        "months_present": len(rows), "as_of": as_of, "window_start": window_start,
        "flights": sums["n_flights"],
        "cancellation_rate_pct": _ratio(sums["n_cancelled"], sums["n_flights"], 100.0),
        "mean_taxi_out_min": _ratio(sums["sum_taxi_out"], sums["n_taxi_out_obs"]),
        "mean_dep_delay_min": _ratio(sums["sum_dep_delay_min"], sums["n_dep_delay_obs"]),
        "pct_dep_delay_ge15min": _ratio(sums["n_dep_del15"], sums["n_dep_delay_obs"], 100.0),
    }

    caveats = [
        "OTP covers domestic reporting carriers only -- no freighters, no international flights",
        f"trailing 12 months ending {as_of} (latest month cached for any airport)" if end_month is None
        else f"trailing 12 months ending {as_of}",
        *null_field_caveats,
    ]
    if len(rows) < 12:
        caveats.append(f"only {len(rows)}/12 months of OTP cached in this window")

    coverage = _otp_t100_coverage(conn, airport_code, window)
    result["coverage"] = coverage["result"]
    caveats.extend(coverage["caveats"])

    return _envelope(
        result, method, caveats,
        _source_stamp(rows[0]["source"], rows[0]["fetched_at"]), "high" if len(rows) == 12 else "medium",
    )


def _otp_t100_coverage(conn, code, window):
    """
    OTP flights / T-100 (source A) domestic_departures, summed over only the
    months where both an otp_airport_month row and a source_a_airport_month
    row (with non-NULL domestic_departures) exist for `code`. Returns
    {"result": {...}|None, "caveats": [...]} -- not a full envelope, meant to
    be folded into get_congestion_ttm's envelope.

    T-100 domestic_departures includes freighters and carriers that do not
    report to OTP, so this ratio understates how much passenger traffic OTP
    actually covers -- see docs/DECISIONS.md.
    """
    ym_values = [y * 100 + m for y, m in window]
    placeholders = ",".join("?" for _ in ym_values)
    otp_rows = {
        (r["year"], r["month"]): r["n_flights"]
        for r in conn.execute(
            f"SELECT year, month, n_flights FROM otp_airport_month "
            f"WHERE origin = ? AND (year * 100 + month) IN ({placeholders})",
            (code, *ym_values),
        ).fetchall()
    }
    t100_rows = {
        (r["year"], r["month"]): r["domestic_departures"]
        for r in conn.execute(
            f"SELECT year, month, domestic_departures FROM source_a_airport_month "
            f"WHERE origin_airport_code = ? AND (year * 100 + month) IN ({placeholders})",
            (code, *ym_values),
        ).fetchall()
        if r["domestic_departures"] is not None
    }
    overlap_months = sorted(set(otp_rows) & set(t100_rows))
    if not overlap_months:
        return {"result": None, "caveats": ["coverage: no overlapping OTP/T-100 months in this window"]}

    otp_total = sum(otp_rows[ym] for ym in overlap_months)
    t100_total = sum(t100_rows[ym] for ym in overlap_months)
    months_label = [f"{y:04d}-{m:02d}" for y, m in overlap_months]
    coverage_pct = round(100.0 * otp_total / t100_total, 1) if t100_total else None
    return {
        "result": {
            "coverage_pct": coverage_pct,
            "otp_flights": otp_total,
            "t100_domestic_departures": t100_total,
            "overlapping_months": months_label,
        },
        "caveats": [
            f"coverage computed over {len(overlap_months)} overlapping month(s) only: {months_label}, "
            "not the full 12-month window",
            "coverage = OTP flights / T-100 domestic_departures; T-100 domestic_departures includes "
            "freighters and carriers that do not report to OTP, so this ratio understates how much "
            "passenger traffic OTP actually covers",
        ],
    }


def now_stamp():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
