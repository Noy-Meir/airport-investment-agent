"""
Builds data/cache.db from scratch (or refreshes it) from:
  - Source A: T-100 Segment Summary by Origin Airport (paged, typed Socrata pull)
  - Source B: route-level T-100, aggregated by (year, month, origin, dest,
    class, carrier) from whatever data/raw/t100_segment_all_carrier_<year>.zip
    files are present (run `python -m src.clients.t100_routes <year>` first
    to fetch one that's missing -- this script does not auto-download it)
  - OTP: On-Time Performance, aggregated per (year, month, origin) from
    whatever data/raw/otp_<year>_<month>.zip files are present (run
    `python -m src.clients.otp <start_year> <start_month> <end_year> <end_month>`
    first to fetch any that are missing -- this script does not auto-download it)
  - OurAirports reference (US airports with an IATA code)

Then rebuilds data/reference/regions.json and hub_tiers.json from the cache
it just built (data/reference/buildability.json is curated by hand, not
touched here), and reports (does not assert) how the newly-ingested OTP
numbers for July 2026 compare to the earlier spike's SFO/LAX/SNA/ANC numbers.

Never prints or reads the big CSV into this process's stdout/context --
progress goes to data/logs/build_cache.log, and only a short summary is
printed.

Usage:
    python scripts/build_cache.py --source-a-years 2023 2024 2025
"""

import argparse
import glob
import logging
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.cache import accessors, db
from src.cache.config import CACHE_DB_PATH, RAW_DIR, REFERENCE_DIR
from src.cache.otp_aggregate import OTPAggregateError, aggregate_otp
from src.cache.outlook import build_outlook_row
from src.cache.t100_aggregate import T100AggregateError, aggregate_routes, collapse_to_storage_grain
from src.clients import ourairports, runways, source_a
from src.clients.faa_taf import FaaTafError, fetch_enplanements_xlsx_bytes, fetched_at_stamp as taf_fetched_at_stamp
from src.clients.faa_taf import parse_enplanements_rows, rows_by_locid
from src.clients.ourairports import OurAirportsError
from src.clients.runways import RunwaysError
from src.clients.source_a import SourceAError

LOG_DIR = os.path.join(os.path.dirname(RAW_DIR), "logs")
logger = logging.getLogger("build_cache")


def _configure_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, "build_cache.log")
    handler = logging.FileHandler(log_path)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    return log_path


def build_source_a(conn, years):
    summary = []
    for year in years:
        try:
            rows = list(source_a.fetch_years([year]))
        except SourceAError as e:
            summary.append(f"  source A {year}: FAILED -- {e}")
            continue
        fetched_at = source_a.fetched_at_stamp()
        db.upsert_source_a_rows(conn, rows, source_a.SOURCE_NAME, fetched_at)
        summary.append(f"  source A {year}: {len(rows)} airport-months")
    return summary


def build_route_agg(conn, years):
    summary = []
    for year in years:
        zip_path = os.path.join(RAW_DIR, f"t100_segment_all_carrier_{year}.zip")
        if not os.path.exists(zip_path):
            summary.append(
                f"  route-level {year}: SKIPPED -- {zip_path} not found. "
                f"Run: python -m src.clients.t100_routes {year}"
            )
            continue
        try:
            agg, missing_distance = aggregate_routes(zip_path)
        except T100AggregateError as e:
            summary.append(f"  route-level {year}: FAILED -- {e}")
            continue
        collapsed = collapse_to_storage_grain(agg)
        fetched_at = source_a.fetched_at_stamp()
        db.upsert_route_agg(conn, collapsed, "BTS T-100 Segment (All Carriers), route level (FMG)", fetched_at)
        summary.append(
            f"  route-level {year}: {len(collapsed):,} (origin,dest,class) combos "
            f"(from {len(agg):,} origin,dest,class,carrier,month combos) from {zip_path}"
        )
        if missing_distance["rows"]:
            summary.append(
                f"  route-level {year}: {missing_distance['rows']:,} raw rows with missing DISTANCE "
                f"({missing_distance['departures_performed']:,.0f} departures), tracked, not imputed"
            )
    return summary


def build_otp(conn, months):
    summary = []
    for year, month in months:
        zip_path = os.path.join(RAW_DIR, f"otp_{year}_{month:02d}.zip")
        if not os.path.exists(zip_path):
            summary.append(
                f"  OTP {year}-{month:02d}: SKIPPED -- {zip_path} not found. "
                f"Run: python -m src.clients.otp {year} {month} {year} {month}"
            )
            continue
        try:
            agg = aggregate_otp(zip_path)
        except OTPAggregateError as e:
            summary.append(f"  OTP {year}-{month:02d}: FAILED -- {e}")
            continue
        fetched_at = source_a.fetched_at_stamp()
        db.upsert_otp_rows(conn, agg, "BTS On-Time Performance (Reporting Carrier)", fetched_at)
        summary.append(f"  OTP {year}-{month:02d}: {len(agg):,} origin airports from {zip_path}")
    return summary


def build_ourairports(conn):
    try:
        rows = list(ourairports.fetch_us_airports())
    except OurAirportsError as e:
        return [f"  OurAirports: FAILED -- {e}"]
    fetched_at = ourairports.fetched_at_stamp()
    db.upsert_ourairports(conn, rows, ourairports.SOURCE_NAME, fetched_at)
    return [f"  OurAirports: {len(rows)} US airports with an IATA code"]


def build_airport_outlook(conn):
    """
    Builds the context-only airport_outlook table (FAA TAF enplanement
    forecast + OurAirports qualifying-runway count), for every airport
    already in the `ourairports` table. Downloads the TAF zip and
    runways.csv in memory only -- nothing is written to data/raw/ or
    committed. See src/cache/outlook.py for the pure matching/CAGR logic.
    """
    try:
        enplanements_rows = parse_enplanements_rows(fetch_enplanements_xlsx_bytes())
    except FaaTafError as e:
        return [f"  airport_outlook: FAILED -- FAA TAF download/parse: {e}"]
    enplanements_by_lid = rows_by_locid(enplanements_rows)
    lid_set = set(enplanements_by_lid)

    try:
        runways_by_ref = runways.fetch_runways_by_airport_ref()
    except RunwaysError as e:
        return [f"  airport_outlook: FAILED -- OurAirports runways.csv download: {e}"]

    try:
        airport_keys = ourairports.fetch_airport_keys()
    except OurAirportsError as e:
        return [f"  airport_outlook: FAILED -- OurAirports airports.csv key fetch: {e}"]

    our_iata_codes = [r["iata_code"] for r in conn.execute("SELECT iata_code FROM ourairports")]

    out_rows = []
    unmatched = []
    for iata in our_iata_codes:
        keys = airport_keys.get(iata, {})
        row = build_outlook_row(
            iata, keys.get("local_code"), keys.get("ourairports_id"),
            enplanements_by_lid, runways_by_ref, lid_set,
        )
        out_rows.append(row)
        if row["faa_lid"] is None:
            unmatched.append(iata)

    fetched_at = taf_fetched_at_stamp()
    db.upsert_airport_outlook(
        conn, out_rows,
        "FAA TAF 2025 Enplanements + OurAirports runways.csv/airports.csv", fetched_at,
    )
    summary = [f"  airport_outlook: {len(out_rows)} rows ({len(our_iata_codes) - len(unmatched)} matched to a TAF locid)"]
    if unmatched:
        summary.append(f"  airport_outlook: {len(unmatched)} unmatched: {unmatched}")
    return summary


def write_reference_files(conn):
    """
    Writes the CY2024 golden reference files. These are the ones the Phase 1
    golden tests pin exact numbers against -- never changed here based on
    TTM; see report_ttm_diff() for the side-by-side TTM comparison.
    """
    import json

    os.makedirs(REFERENCE_DIR, exist_ok=True)
    summary = []

    ne = accessors.list_new_england_airports(conn)
    regions = {"new_england": ne}
    with open(os.path.join(REFERENCE_DIR, "regions.json"), "w") as f:
        json.dump(regions, f, indent=2, default=str)
    n = len(ne["result"]) if ne["result"] else 0
    summary.append(f"  regions.json: new_england = {n} airports")

    tiers = accessors.compute_hub_tiers(conn)
    with open(os.path.join(REFERENCE_DIR, "hub_tiers.json"), "w") as f:
        json.dump(tiers, f, indent=2, default=str)
    counts = tiers["result"]["counts"] if tiers["result"] else {}
    summary.append(f"  hub_tiers.json: {counts}")

    return summary


TTM_MARKER_START = "<!-- TTM_DIFF_START (auto-generated by scripts/build_cache.py -- do not hand-edit this block) -->"
TTM_MARKER_END = "<!-- TTM_DIFF_END -->"


def _diff_codes(cy_codes, ttm_codes):
    entering = sorted(set(ttm_codes) - set(cy_codes))
    leaving = sorted(set(cy_codes) - set(ttm_codes))
    return entering, leaving


def report_ttm_diff(conn):
    """
    Computes hub tiers + New England list + a general volume-floor list on
    the trailing 12 months (ending at the latest cached month) side by side
    with the CY2024 golden numbers, and writes the diff (airports entering
    vs. leaving each set) into docs/DECISIONS.md between TTM_DIFF markers.
    Does NOT change data/reference/*.json -- those stay CY2024, so the
    golden tests are unaffected.
    """
    from src.cache.accessors import (
        _pax_by_airport_for_year, _pax_by_airport_ttm, compute_hub_tiers,
        compute_hub_tiers_ttm, get_latest_cached_month,
        list_new_england_airports, list_new_england_airports_ttm,
    )
    from src.cache.config import VOLUME_FLOOR_PAX, VOLUME_FLOOR_YEAR

    latest = get_latest_cached_month(conn)
    if latest is None:
        return ["  TTM diff: SKIPPED -- cache is empty"]

    cy_pax, _ = _pax_by_airport_for_year(conn, VOLUME_FLOOR_YEAR)
    ttm_pax, ttm_excluded, as_of = _pax_by_airport_ttm(conn)

    cy_floor = {c for c, p in cy_pax.items() if p >= VOLUME_FLOOR_PAX}
    ttm_floor = {c for c, p in ttm_pax.items() if p >= VOLUME_FLOOR_PAX}
    floor_entering, floor_leaving = _diff_codes(cy_floor, ttm_floor)

    cy_ne = {a["code"] for a in list_new_england_airports(conn)["result"] or []}
    ttm_ne_env = list_new_england_airports_ttm(conn)
    ttm_ne = {a["code"] for a in ttm_ne_env["result"] or []}
    ne_entering, ne_leaving = _diff_codes(cy_ne, ttm_ne)

    cy_tiers = compute_hub_tiers(conn)["result"]["tiers"]
    ttm_tiers_env = compute_hub_tiers_ttm(conn)
    ttm_tiers = ttm_tiers_env["result"]["tiers"]
    tier_diffs = {}
    for tier in ("large", "medium", "small"):
        cy_codes = {a["code"] for a in cy_tiers[tier]}
        ttm_codes = {a["code"] for a in ttm_tiers[tier]}
        tier_diffs[tier] = _diff_codes(cy_codes, ttm_codes)

    lines = [
        TTM_MARKER_START,
        "",
        "## TTM vs CY2024 diff (auto-generated)",
        "",
        f"Trailing 12 months ending {as_of} ({ttm_excluded} NULL total_passengers month-rows excluded from TTM sums).",
        "",
        f"- Volume floor (>= {VOLUME_FLOOR_PAX:,}/yr, all US airports): "
        f"CY2024 n={len(cy_floor)}, TTM n={len(ttm_floor)}. "
        f"Entering: {floor_entering or 'none'}. Leaving: {floor_leaving or 'none'}.",
        f"- New England list: CY2024 n={len(cy_ne)}, TTM n={len(ttm_ne)}. "
        f"Entering: {ne_entering or 'none'}. Leaving: {ne_leaving or 'none'}.",
    ]
    for tier in ("large", "medium", "small"):
        entering, leaving = tier_diffs[tier]
        lines.append(
            f"- Hub tier {tier}: CY2024 n={len(cy_tiers[tier])}, TTM n={len(ttm_tiers[tier])}. "
            f"Entering: {entering or 'none'}. Leaving: {leaving or 'none'}."
        )
    lines += ["", TTM_MARKER_END]
    block = "\n".join(lines)

    decisions_path = os.path.join(os.path.dirname(REFERENCE_DIR), "..", "docs", "DECISIONS.md")
    decisions_path = os.path.normpath(decisions_path)
    with open(decisions_path) as f:
        text = f.read()
    if TTM_MARKER_START in text:
        start = text.index(TTM_MARKER_START)
        end = text.index(TTM_MARKER_END) + len(TTM_MARKER_END)
        text = text[:start] + block + text[end:]
    else:
        text = text.rstrip("\n") + "\n\n" + block + "\n"
    with open(decisions_path, "w") as f:
        f.write(text)

    return [
        f"  TTM diff written to {decisions_path} (as_of={as_of})",
        f"  volume floor: CY2024={len(cy_floor)} TTM={len(ttm_floor)}",
        f"  New England: CY2024={len(cy_ne)} TTM={len(ttm_ne)}",
        f"  hub tiers TTM: {ttm_tiers_env['result']['counts']}",
    ]


# Earlier spike numbers for July 2026 (flights, cancelled, mean taxi-out min,
# mean dep delay min, %dep-delay>=15min), from docs/DECISIONS.md. ANC's spike
# only recorded a flight count, not the other four figures -- see
# docs/DECISIONS.md "OTP (On-Time Performance)": the spike's ANC OTP numbers
# were never reproduced and look inconsistent with T-100, so ANC is compared
# on flights only here.
SPIKE_JULY_2026 = {
    "SFO": (13841, 159, 25.1, 24.7, 34.3),
    "LAX": (17454, 210, 18.8, 18.7, 24.8),
    "SNA": (3992, 45, 16.8, 20.2, 25.0),
    "ANC": (2513, None, None, None, None),
}


def report_otp_reconciliation(conn, spike=SPIKE_JULY_2026, year=2026, month=7):
    """
    Reports (does not assert) how the newly-ingested OTP numbers for July
    2026 compare to the earlier data-source spike's numbers for
    SFO/LAX/SNA/ANC. Purely informational -- CLAUDE.md forbids hardcoded
    verdicts, so this prints the real current numbers and their delta from
    the spike, not a pass/fail judgment.
    """
    from src.cache.accessors import get_congestion_month

    lines = [f"  OTP reconciliation vs. spike, {year}-{month:02d}:"]
    for code, (sp_flights, sp_cancelled, sp_taxi, sp_delay, sp_pct15) in spike.items():
        env = get_congestion_month(conn, code, year, month)
        if env["result"] is None:
            lines.append(f"    {code}: no OTP cache for {year}-{month:02d} -- {env['caveats']}")
            continue
        r = env["result"]
        lines.append(
            f"    {code}: flights {r['flights']} (spike {sp_flights}), "
            f"cancellation_rate_pct {r['cancellation_rate_pct']} (spike n_cancelled={sp_cancelled}), "
            f"mean_taxi_out_min {r['mean_taxi_out_min']} (spike {sp_taxi}), "
            f"mean_dep_delay_min {r['mean_dep_delay_min']} (spike {sp_delay}), "
            f"pct_dep_delay_ge15min {r['pct_dep_delay_ge15min']} (spike {sp_pct15})"
        )
    return lines


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-a-years", nargs="+", type=int, default=[2023, 2024, 2025, 2026],
        help="calendar years to pull from source A (default: 2023 2024 2025 2026 -- data runs through 2026-04)",
    )
    parser.add_argument(
        "--route-years", nargs="+", type=int, default=None,
        help="calendar years to aggregate from data/raw/t100_segment_all_carrier_<year>.zip "
             "(default: only the latest year present in data/raw/ -- get_long_haul_share, the "
             "only consumer of t100_route_agg, always reads MAX(year), so older years are "
             "never used; pass explicit years to store more)",
    )
    parser.add_argument(
        "--otp-months", nargs="+", default=None,
        help="YYYY-M months to aggregate from data/raw/otp_<year>_<month>.zip "
             "(default: every such file already present in data/raw/)",
    )
    parser.add_argument(
        "--outlook-only", action="store_true",
        help="build/refresh only the airport_outlook table (FAA TAF + runways) -- "
             "does not touch any other table, does not require data/raw/ files",
    )
    args = parser.parse_args()

    log_path = _configure_logging()
    os.makedirs(os.path.dirname(CACHE_DB_PATH), exist_ok=True)
    conn = db.connect()
    db.init_schema(conn)

    if args.outlook_only:
        summary = ["Building airport_outlook only ..."]
        summary += build_airport_outlook(conn)
        conn.close()
        summary.append(f"cache.db: {CACHE_DB_PATH}")
        summary.append(f"full log: {log_path}")
        print("\n".join(summary[:40]))
        return

    route_years = args.route_years
    if route_years is None:
        found = glob.glob(os.path.join(RAW_DIR, "t100_segment_all_carrier_*.zip"))
        all_route_years = sorted(int(os.path.basename(p).rsplit("_", 1)[1].split(".")[0]) for p in found)
        # get_long_haul_share always reads MAX(year) in t100_route_agg, so only the
        # latest year found is ever reachable -- don't store/download older ones by default.
        route_years = all_route_years[-1:]

    if args.otp_months is None:
        found = glob.glob(os.path.join(RAW_DIR, "otp_*.zip"))
        otp_months = sorted(
            (int(m.group(1)), int(m.group(2)))
            for m in (re.match(r"otp_(\d{4})_(\d{2})\.zip$", os.path.basename(p)) for p in found)
            if m
        )
    else:
        otp_months = sorted(tuple(int(x) for x in ym.split("-")) for ym in args.otp_months)

    summary = [
        "Building data/cache.db ...", f"source A years: {args.source_a_years}",
        f"route-level years: {route_years}", f"OTP months: {otp_months}",
    ]
    summary += build_source_a(conn, args.source_a_years)
    summary += build_route_agg(conn, route_years)
    summary += build_otp(conn, otp_months)
    summary += build_ourairports(conn)
    summary += build_airport_outlook(conn)
    summary += write_reference_files(conn)
    summary += report_ttm_diff(conn)
    summary += report_otp_reconciliation(conn)
    conn.close()
    summary.append(f"cache.db: {CACHE_DB_PATH}")
    summary.append(f"full log: {log_path}")

    print("\n".join(summary[:40]))


if __name__ == "__main__":
    main()
