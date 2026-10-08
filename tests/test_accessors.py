"""
Accessor-level unit tests. Run entirely against the committed synthetic
fixture db (tests/fixtures/fixture_cache.db, via the fixture_conn fixture)
-- no data/cache.db or data/raw/ required. Tests that need the real cache
use real_conn, which skips loudly (not silently) if it's missing.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.cache import accessors
from src.clients.ourairports import OurAirportsError, parse_us_airports
from src.clients.source_a import _intl

ENVELOPE_KEYS = {"result", "method", "caveats", "source", "confidence"}


# --- item 1: international = total - domestic, computed at ingest --------

def test_intl_synthetic_row():
    assert _intl(100, 60) == 40
    assert _intl(100, None) is None
    assert _intl(None, 60) is None
    assert _intl(None, None) is None


def test_jfk_cy2024_intl_passengers_positive(real_conn):
    r = accessors.get_airport_year_totals(real_conn, "JFK", 2024)
    assert r["result"] is not None, "REAL CACHE: no JFK CY2024 rows -- rebuild data/cache.db"
    assert r["result"]["intl_passengers"] is not None
    assert r["result"]["intl_passengers"] > 0


# --- item 2: no imputation -------------------------------------------------

def test_null_months_excluded_from_sum_not_zeroed(fixture_conn):
    r = accessors.get_airport_year_totals(fixture_conn, "TST", 2025)
    # total_passengers: month 1 = 100, month 2 = NULL -> sum excludes the NULL month, not 100+0.
    assert r["result"]["total_passengers"] == 100.0
    assert any("total_passengers: excluded 1/2" in c for c in r["caveats"])
    # intl_passengers: month 1 = 40, month 2 = NULL -> same.
    assert r["result"]["intl_passengers"] == 40.0
    assert any("intl_passengers: excluded 1/2" in c for c in r["caveats"])
    # a field with no NULLs (total_departures) is not flagged as excluded.
    assert r["result"]["total_departures"] == 22.0
    assert not any(c.startswith("total_departures: excluded") for c in r["caveats"])


def test_long_haul_excludes_null_distance_keeps_same_airport_zero(fixture_conn):
    r = accessors.get_long_haul_share(fixture_conn, "TST", 2025, thresholds=(2000,), class_group="all")
    # valid rows: AAA (5 deps, 2500mi) + BBB (5 deps, 1000mi) + TST->TST (3 deps, 0mi, same-airport) = 13 total.
    # TST->CCC (NULL mi, 2 deps) is excluded from both num and denom; TST->TST (0mi) is kept as short-haul.
    assert r["result"]["total_departures"] == 13.0
    assert r["result"]["shares_pct"][2000] == pytest.approx(5 / 13 * 100, abs=0.05)
    assert any("excluded 1" in c and "2 departures" in c and "missing DISTANCE" in c for c in r["caveats"])
    assert any("included 3 departures" in c and "same-airport" in c for c in r["caveats"])


# --- item 3: OurAirports closed-row drop + duplicate iata_code guard ------

def test_ourairports_drops_closed_rows():
    csv_text = (
        "iata_code,ident,name,type,iso_country,iso_region,municipality,latitude_deg,longitude_deg,scheduled_service\n"
        "XYZ,KXYZ,Open Field,small_airport,US,US-ME,Town,44.0,-70.0,yes\n"
        "ABD,KABD,Closed Field,closed,US,US-ME,Town,44.0,-70.0,no\n"
    )
    rows = parse_us_airports(csv_text)
    codes = {r["iata_code"] for r in rows}
    assert codes == {"XYZ"}


def test_ourairports_duplicate_active_iata_code_raises():
    csv_text = (
        "iata_code,ident,name,type,iso_country,iso_region,municipality,latitude_deg,longitude_deg,scheduled_service\n"
        "DUP,KAAA,Field One,small_airport,US,US-ME,TownA,44.0,-70.0,yes\n"
        "DUP,KBBB,Field Two,small_airport,US,US-NH,TownB,43.0,-71.0,yes\n"
    )
    with pytest.raises(OurAirportsError, match="DUP"):
        parse_us_airports(csv_text)


# --- item 5(c): injection-shaped input, envelope shape -------------------

def test_sql_injection_shaped_code_rejected_not_executed(fixture_conn):
    malicious = "SFO'; DROP TABLE source_a_airport_month;--"
    with pytest.raises(accessors.AirportNotFoundError):
        accessors.validate_airport_code(fixture_conn, malicious)
    # table must still exist and be queryable afterward.
    row = fixture_conn.execute("SELECT COUNT(*) AS n FROM source_a_airport_month").fetchone()
    assert row["n"] == 2


# --- OTP congestion accessors ---------------------------------------------

def test_congestion_ttm_sums_not_means(fixture_conn):
    r = accessors.get_congestion_ttm(fixture_conn, "TST", end_month=(2025, 2))
    assert r["result"]["flights"] == 18
    # mean_taxi_out = (180+160) / (9+8) = 20.0 -- derived from sums/counts, not averaged per-month.
    assert r["result"]["mean_taxi_out_min"] == pytest.approx(340 / 17, abs=0.01)
    assert r["result"]["mean_dep_delay_min"] == pytest.approx(130 / 17, abs=0.01)
    assert r["result"]["pct_dep_delay_ge15min"] == pytest.approx(100 * 2 / 17, abs=0.01)
    # cancellation_rate_pct uses n_flights as the denominator, not n_dep_delay_obs.
    assert r["result"]["cancellation_rate_pct"] == pytest.approx(100 * 1 / 18, abs=0.01)
    assert any("domestic reporting carriers only" in c for c in r["caveats"])


def test_congestion_ttm_coverage_overlap(fixture_conn):
    r = accessors.get_congestion_ttm(fixture_conn, "TST", end_month=(2025, 2))
    coverage = r["result"]["coverage"]
    # both OTP months have a non-NULL source_a domestic_departures row -> both are in the overlap.
    assert coverage["overlapping_months"] == ["2025-01", "2025-02"]
    assert coverage["otp_flights"] == 18
    assert coverage["t100_domestic_departures"] == 17.0
    assert any("overlapping month" in c for c in r["caveats"])


def test_congestion_month_single_month(fixture_conn):
    r = accessors.get_congestion_month(fixture_conn, "TST", 2025, 1)
    assert r["result"]["flights"] == 10
    assert r["result"]["cancellation_rate_pct"] == pytest.approx(10.0, abs=0.01)
    assert r["result"]["mean_taxi_out_min"] == pytest.approx(180 / 9, abs=0.01)


def test_congestion_month_missing_returns_none(fixture_conn):
    r = accessors.get_congestion_month(fixture_conn, "TST", 1999, 1)
    assert r["result"] is None
    assert r["source"] == "no cached data"


@pytest.mark.parametrize("call", [
    lambda c: accessors.get_airport_year_totals(c, "TST", 2025),
    lambda c: accessors.get_long_haul_share(c, "TST", 2025),
    lambda c: accessors.list_new_england_airports(c),
    lambda c: accessors.compute_hub_tiers(c),
    lambda c: accessors.get_ttm_totals(c, "TST", end_month=(2025, 2)),
    lambda c: accessors.get_congestion_ttm(c, "TST", end_month=(2025, 2)),
    lambda c: accessors.get_congestion_month(c, "TST", 2025, 1),
])
def test_every_accessor_returns_full_envelope(fixture_conn, call):
    r = call(fixture_conn)
    assert set(r.keys()) == ENVELOPE_KEYS


def test_unknown_code_raises_not_guesses(fixture_conn):
    with pytest.raises(accessors.AirportNotFoundError, match="ZZZ"):
        accessors.validate_airport_code(fixture_conn, "ZZZ")
