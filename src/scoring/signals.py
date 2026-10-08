"""
Per-airport scoring signals, TTM ending at the latest month cached in
source A (or an explicit `end_month`): growth, load_factor,
demand_supply_gap, congestion.

Every signal is its own envelope (CLAUDE.md: {result, method, caveats,
source, confidence}) -- no imputation. A signal that cannot be computed
(missing prior window, NULL field, low OTP coverage, ...) returns
result=None with a caveat explaining why, never a guessed number.
"""

from src.cache.accessors import get_congestion_ttm, get_ttm_totals, validate_airport_code
from src.cache.config import VOLUME_FLOOR_PAX
from src.reference.envelope import envelope
from src.reference.hub_tiers import compute_hub_tiers_ttm
from src.reference.pax import get_latest_cached_month, pax_by_airport_ttm

CONGESTION_MIN_COVERAGE_PCT = 50.0


def _shift_back_12(year, month):
    """(year, month) of the month 12 months before the given one."""
    y, m = year, month - 12
    while m <= 0:
        m += 12
        y -= 1
    return y, m


def _resolve_end_month(conn, end_month):
    if end_month is not None:
        return end_month
    return get_latest_cached_month(conn)


def _ttm_field_growth(conn, code, field, end_month, label):
    """
    Shared TTM-vs-prior-TTM % change for one source_a_airport_month field
    (total_passengers or total_seats). Returns an envelope whose result is
    {f"{label}_growth_pct", current_value, prior_value, current_as_of,
    prior_as_of} or None if either window is missing/NULL/zero.
    """
    method = f"TTM {field} vs prior TTM {field}, % change"
    end = _resolve_end_month(conn, end_month)
    if end is None:
        return envelope(None, method, ["cache is empty"], "no cached data", "low")
    end_year, end_m = end
    prior_end = _shift_back_12(end_year, end_m)

    current = get_ttm_totals(conn, code, end)
    prior = get_ttm_totals(conn, code, prior_end)

    caveats = [f"current window: {c}" for c in current["caveats"]]
    caveats += [f"prior window: {c}" for c in prior["caveats"]]

    if current["result"] is None or prior["result"] is None:
        caveats.append(f"{label} growth requires both the current and prior TTM windows to have cached data")
        source = current["source"] if current["result"] is not None else prior["source"]
        return envelope(None, method, caveats, source, "low")

    cur_val = current["result"][field]
    prior_val = prior["result"][field]
    if cur_val is None or prior_val is None:
        caveats.append(f"{field} is NULL in the current or prior TTM window -- {label} growth not computable")
        return envelope(None, method, caveats, current["source"], "low")
    if prior_val == 0:
        caveats.append(f"prior TTM {field} is 0 -- {label} growth % undefined")
        return envelope(None, method, caveats, current["source"], "low")

    growth_pct = round(100.0 * (cur_val - prior_val) / prior_val, 2)
    confidence = "high" if current["confidence"] == "high" and prior["confidence"] == "high" else "medium"
    return envelope(
        {
            f"{label}_growth_pct": growth_pct,
            "current_value": cur_val, "prior_value": prior_val,
            "current_as_of": current["result"]["as_of"], "prior_as_of": prior["result"]["as_of"],
        },
        method, caveats, current["source"], confidence,
    )


def get_growth_ttm(conn, code, end_month=None):
    """TTM total_passengers vs. the prior (non-overlapping) TTM window, as a % change."""
    airport_code = validate_airport_code(conn, code)
    g = _ttm_field_growth(conn, airport_code, "total_passengers", end_month, "passenger")
    if g["result"] is None:
        return g
    r = g["result"]
    return envelope(
        {
            "growth_pct": r["passenger_growth_pct"], "as_of": r["current_as_of"],
            "current_ttm_passengers": r["current_value"], "prior_ttm_passengers": r["prior_value"],
            "current_as_of": r["current_as_of"], "prior_as_of": r["prior_as_of"],
        },
        "TTM total_passengers vs prior TTM total_passengers, % change",
        g["caveats"], g["source"], g["confidence"],
    )


def get_load_factor_ttm(conn, code, end_month=None):
    """TTM total_passengers / TTM total_seats (each a NULL-excluding sum)."""
    airport_code = validate_airport_code(conn, code)
    method = "TTM total_passengers / TTM total_seats (NULL-excluding sums)"
    end = _resolve_end_month(conn, end_month)
    if end is None:
        return envelope(None, method, ["cache is empty"], "no cached data", "low")
    ttm = get_ttm_totals(conn, airport_code, end)
    if ttm["result"] is None:
        return envelope(None, method, ttm["caveats"], ttm["source"], "low")

    pax = ttm["result"]["total_passengers"]
    seats = ttm["result"]["total_seats"]
    caveats = list(ttm["caveats"])
    if pax is None or seats is None or seats == 0:
        caveats.append("total_passengers or total_seats is NULL/0 over this TTM window -- load_factor not computable")
        return envelope(None, method, caveats, ttm["source"], "low")

    load_factor = round(pax / seats, 4)
    return envelope(
        {"load_factor": load_factor, "ttm_passengers": pax, "ttm_seats": seats, "as_of": ttm["result"]["as_of"]},
        method, caveats, ttm["source"], ttm["confidence"],
    )


def get_demand_supply_gap_ttm(conn, code, end_month=None):
    """
    TTM passenger growth % minus TTM seat growth % -- a proxy for demand
    outrunning (or lagging) capacity growth, NOT a direct demand measure
    (it says nothing about fares, load factor level, or unmet demand).
    """
    airport_code = validate_airport_code(conn, code)
    method = (
        "demand_supply_gap = TTM passenger growth % - TTM seat growth %; "
        "a proxy for demand outrunning/lagging capacity, not a direct demand measure"
    )
    caveats = ["demand_supply_gap is a proxy (passenger growth minus seat growth), not a direct demand measure"]

    pax_growth = _ttm_field_growth(conn, airport_code, "total_passengers", end_month, "passenger")
    seat_growth = _ttm_field_growth(conn, airport_code, "total_seats", end_month, "seat")
    caveats += pax_growth["caveats"] + seat_growth["caveats"]

    if pax_growth["result"] is None or seat_growth["result"] is None:
        caveats.append("demand_supply_gap requires both passenger growth and seat growth to be computable")
        source = pax_growth["source"] if pax_growth["result"] is not None else seat_growth["source"]
        return envelope(None, method, caveats, source, "low")

    gap_pct = round(
        pax_growth["result"]["passenger_growth_pct"] - seat_growth["result"]["seat_growth_pct"], 2
    )
    confidence = "high" if pax_growth["confidence"] == "high" and seat_growth["confidence"] == "high" else "medium"
    return envelope(
        {
            "demand_supply_gap_pct": gap_pct, "as_of": pax_growth["result"]["current_as_of"],
            "passenger_growth_pct": pax_growth["result"]["passenger_growth_pct"],
            "seat_growth_pct": seat_growth["result"]["seat_growth_pct"],
        },
        method, caveats, pax_growth["source"], confidence,
    )


def get_congestion_signal_ttm(conn, code, end_month=None):
    """
    TTM %dep-delay>=15min (from OTP sums/counts), valid only when
    OTP/T-100 coverage >= CONGESTION_MIN_COVERAGE_PCT; otherwise NULL with
    a "low OTP coverage (x%)" caveat, per CLAUDE.md's no-imputation rule.
    """
    airport_code = validate_airport_code(conn, code)
    method = (
        f"TTM OTP %dep-delay>=15min, valid only if OTP/T-100 coverage >= {CONGESTION_MIN_COVERAGE_PCT:.0f}%"
    )
    congestion = get_congestion_ttm(conn, airport_code, end_month)
    if congestion["result"] is None:
        return envelope(None, method, congestion["caveats"], congestion["source"], "low")

    caveats = list(congestion["caveats"])
    coverage = congestion["result"]["coverage"]
    if coverage is None or coverage["coverage_pct"] is None:
        caveats.append("low OTP coverage (coverage could not be computed -- no overlapping OTP/T-100 months)")
        return envelope(None, method, caveats, congestion["source"], "low")

    coverage_pct = coverage["coverage_pct"]
    if coverage_pct < CONGESTION_MIN_COVERAGE_PCT:
        caveats.append(f"low OTP coverage ({coverage_pct}%)")
        return envelope(None, method, caveats, congestion["source"], "low")

    pct_delayed = congestion["result"]["pct_dep_delay_ge15min"]
    if pct_delayed is None:
        caveats.append("pct_dep_delay_ge15min is NULL despite adequate coverage -- congestion not computable")
        return envelope(None, method, caveats, congestion["source"], "low")

    return envelope(
        {"congestion_pct": pct_delayed, "coverage_pct": coverage_pct, "as_of": congestion["result"]["as_of"]},
        method, caveats, congestion["source"], congestion["confidence"],
    )


def compute_signals(conn, code, end_month=None):
    """
    All four signals for one airport, each its own envelope, never imputed
    across signals. All four are resolved to the SAME as_of window: by
    default that's the latest source-A (T-100) month, not each signal's own
    default (get_congestion_ttm would otherwise independently default to
    OTP's own latest cached month, which can differ from source-A's --
    see docs/DECISIONS.md). Every signal that returns a result exposes that
    window as result["as_of"].
    """
    airport_code = validate_airport_code(conn, code)
    resolved_end = end_month if end_month is not None else get_latest_cached_month(conn)
    return {
        "airport": airport_code,
        "as_of": f"{resolved_end[0]:04d}-{resolved_end[1]:02d}" if resolved_end is not None else None,
        "growth": get_growth_ttm(conn, airport_code, resolved_end),
        "load_factor": get_load_factor_ttm(conn, airport_code, resolved_end),
        "demand_supply_gap": get_demand_supply_gap_ttm(conn, airport_code, resolved_end),
        "congestion": get_congestion_signal_ttm(conn, airport_code, resolved_end),
    }


def eligible_universe_ttm(conn, end_month=None, volume_floor=VOLUME_FLOOR_PAX):
    """Airport codes with TTM total_passengers >= volume_floor, ending at end_month (or the latest cached month)."""
    method = f"TTM total_passengers >= {volume_floor:,} (NULL months excluded from the sums)"
    pax_by_code, excluded, as_of = pax_by_airport_ttm(conn, end_month)
    if as_of is None:
        return envelope(None, method, ["cache is empty"], "no cached data", "low")

    eligible = sorted(code for code, pax in pax_by_code.items() if pax >= volume_floor)
    caveats = [f"trailing 12 months ending {as_of}"]
    if excluded:
        caveats.append(f"excluded {excluded} month-row(s) with NULL total_passengers from the sums")

    row = conn.execute(
        "SELECT source, fetched_at FROM source_a_airport_month ORDER BY fetched_at DESC LIMIT 1"
    ).fetchone()
    source = f"{row['source']}, cached {row['fetched_at']}" if row else "no cached data"
    return envelope(
        {"eligible_codes": eligible, "as_of": as_of, "count": len(eligible)}, method, caveats, source, "high"
    )


def get_peer_group_ttm(conn, code, end_month=None):
    """Peer group for `code` = its TTM hub tier (large/medium/small/micro) membership."""
    airport_code = validate_airport_code(conn, code)
    method = "TTM hub tier (large/medium/small/micro) membership"
    tiers = compute_hub_tiers_ttm(conn, end_month)
    if tiers["result"] is None:
        return envelope(None, method, tiers["caveats"], tiers["source"], "low")

    for tier_name, members in tiers["result"]["tiers"].items():
        codes = [m["code"] for m in members]
        if airport_code in codes:
            return envelope(
                {"tier": tier_name, "peer_codes": sorted(codes)}, method, tiers["caveats"], tiers["source"],
                tiers["confidence"],
            )

    caveats = tiers["caveats"] + [f"{airport_code} not in any TTM hub tier (no TTM source-A data?)"]
    return envelope(None, method, caveats, tiers["source"], "low")
