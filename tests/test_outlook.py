"""
Unit tests for the context-only airport_outlook feature: CAGR math,
fiscal-year/base-year selection, FAA LID matching (incl. an unmatched
airport), the qualifying-runway filter, and the accessor's NULL-plus-reason
behavior for an unmatched airport. Small in-memory fixtures only -- no
network, no real TAF/runways files.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.cache import accessors, db
from src.cache.outlook import (
    build_outlook_row,
    compute_cagr,
    is_paved,
    match_lid,
    qualifying_runway_count,
    select_base_plus_years,
)

# --- CAGR math --------------------------------------------------------------


def test_compute_cagr_basic():
    # 100 -> 200 over 5 years: (2)^(1/5) - 1
    assert compute_cagr(100.0, 200.0, 5) == round(2 ** (1 / 5) - 1, 4)


def test_compute_cagr_none_when_base_zero_or_negative():
    assert compute_cagr(0.0, 100.0, 5) is None
    assert compute_cagr(-5.0, 100.0, 5) is None


def test_compute_cagr_none_when_values_missing():
    assert compute_cagr(None, 100.0, 5) is None
    assert compute_cagr(100.0, None, 5) is None


# --- fiscal-year / base-year selection --------------------------------------


def test_select_base_plus_years_picks_latest_actual_and_matching_future_years():
    rows = [
        {"scenario": 0, "ayear": 2022, "total": 1000.0},
        {"scenario": 0, "ayear": 2023, "total": 1100.0},
        {"scenario": 0, "ayear": 2024, "total": 1200.0},  # latest actual -> base
        {"scenario": 1, "ayear": 2025, "total": 1300.0},
        {"scenario": 1, "ayear": 2029, "total": 1600.0},  # base + 5
        {"scenario": 1, "ayear": 2034, "total": 2000.0},  # base + 10
    ]
    out = select_base_plus_years(rows)
    assert out["taf_base_fy"] == 2024
    assert out["enplanements_base"] == 1200.0
    assert out["enplanements_plus5"] == 1600.0
    assert out["enplanements_plus10"] == 2000.0
    assert out["cagr_5y"] == compute_cagr(1200.0, 1600.0, 5)
    assert out["cagr_10y"] == compute_cagr(1200.0, 2000.0, 10)


def test_select_base_plus_years_no_actual_rows_all_none():
    rows = [{"scenario": 1, "ayear": 2026, "total": 500.0}]
    out = select_base_plus_years(rows)
    assert out == {
        "taf_base_fy": None, "enplanements_base": None,
        "enplanements_plus5": None, "enplanements_plus10": None,
        "cagr_5y": None, "cagr_10y": None,
    }


def test_select_base_plus_years_missing_target_year_stays_none_not_imputed():
    rows = [{"scenario": 0, "ayear": 2024, "total": 1000.0}]  # no +5/+10 rows at all
    out = select_base_plus_years(rows)
    assert out["taf_base_fy"] == 2024
    assert out["enplanements_base"] == 1000.0
    assert out["enplanements_plus5"] is None
    assert out["enplanements_plus10"] is None
    assert out["cagr_5y"] is None
    assert out["cagr_10y"] is None


# --- LID matching ------------------------------------------------------------


def test_match_lid_via_local_code():
    lid_set = {"ATL", "ORD"}
    assert match_lid("ATL", "ATL", lid_set) == ("ATL", None)


def test_match_lid_falls_back_to_iata_code():
    lid_set = {"ATL"}
    faa_lid, note = match_lid("ATL", "XYZ-NOT-IN-TAF", lid_set)
    assert faa_lid == "ATL"
    assert "fallback" in note


def test_match_lid_unmatched_airport_gets_reason_not_none():
    lid_set = {"ATL", "ORD"}
    faa_lid, note = match_lid("ZZZ", "ZZZ", lid_set)
    assert faa_lid is None
    assert isinstance(note, str) and note


# --- runway filter -----------------------------------------------------------


def test_is_paved_prefixes():
    assert is_paved("ASP") is True
    assert is_paved("ASPH-CONC") is True
    assert is_paved("CON") is True
    assert is_paved("TURF") is False
    assert is_paved(None) is False
    assert is_paved("") is False


def test_qualifying_runway_count_filters_closed_unpaved_and_short():
    rows = [
        {"closed": "0", "surface": "ASP", "length_ft": "10000"},   # qualifies
        {"closed": "1", "surface": "ASP", "length_ft": "10000"},   # closed
        {"closed": "0", "surface": "TURF", "length_ft": "10000"},  # unpaved
        {"closed": "0", "surface": "ASP", "length_ft": "4000"},    # too short
        {"closed": "0", "surface": "CON", "length_ft": None},      # missing length -- not imputed as long enough
        {"closed": "0", "surface": "CON", "length_ft": "5000"},    # qualifies (boundary)
    ]
    assert qualifying_runway_count(rows) == 2


def test_qualifying_runway_count_empty_is_zero():
    assert qualifying_runway_count([]) == 0
    assert qualifying_runway_count(None) == 0


# --- row assembly -------------------------------------------------------------


def test_build_outlook_row_matched():
    enplanements_by_lid = {
        "ATL": [
            {"scenario": 0, "ayear": 2024, "total": 1000.0},
            {"scenario": 1, "ayear": 2029, "total": 1500.0},
        ],
    }
    runways_by_ref = {"1": [{"closed": "0", "surface": "ASP", "length_ft": "10000"}]}
    row = build_outlook_row("ATL", "ATL", "1", enplanements_by_lid, runways_by_ref, {"ATL"})
    assert row["faa_lid"] == "ATL"
    assert row["match_note"] is None
    assert row["qualifying_runways"] == 1
    assert row["enplanements_per_runway"] == 1000.0
    assert row["taf_base_fy"] == 2024


def test_build_outlook_row_unmatched_has_null_fields_and_reason():
    row = build_outlook_row("ZZZ", "ZZZ", "99", {}, {}, {"ATL"})
    assert row["faa_lid"] is None
    assert row["match_note"]
    assert row["taf_base_fy"] is None
    assert row["enplanements_base"] is None
    assert row["qualifying_runways"] == 0
    assert row["enplanements_per_runway"] is None  # 0 runways -> NULL, not 0


# --- accessor ------------------------------------------------------------------


def _tiny_conn():
    conn = db.connect(":memory:")
    db.init_schema(conn)
    conn.execute(
        "INSERT INTO ourairports (iata_code, icao_ident, name, type, iso_country, iso_region, "
        "municipality, latitude_deg, longitude_deg, scheduled_service, source, fetched_at) "
        "VALUES ('ZZZ', 'KZZZ', 'Test Field', 'small_airport', 'US', 'US-ZZ', 'Testville', "
        "0.0, 0.0, 'no', 'test', '2026-01-01T00:00:00Z')"
    )
    conn.commit()
    return conn


def test_get_airport_outlook_unmatched_returns_null_fields_and_reason():
    conn = _tiny_conn()
    row = {
        "iata_code": "ZZZ", "faa_lid": None, "taf_base_fy": None,
        "enplanements_base": None, "enplanements_plus5": None, "enplanements_plus10": None,
        "cagr_5y": None, "cagr_10y": None, "qualifying_runways": 0,
        "enplanements_per_runway": None, "match_note": "neither local_code nor iata_code found in TAF enplanements data",
    }
    db.upsert_airport_outlook(conn, [row], "test source", "2026-01-01T00:00:00Z")

    env = accessors.get_airport_outlook(conn, "ZZZ")
    assert env["result"]["faa_lid"] is None
    assert env["result"]["enplanements_base"] is None
    assert env["result"]["enplanements_per_runway"] is None
    assert any("unmatched to FAA TAF" in c for c in env["caveats"])
    assert any("not used in scoring" in c for c in env["caveats"])
    assert env["confidence"] == "low"
    conn.close()


def test_get_airport_outlook_no_row_cached_yet():
    conn = _tiny_conn()
    env = accessors.get_airport_outlook(conn, "ZZZ")
    assert env["result"] is None
    assert any("no airport_outlook row cached" in c for c in env["caveats"])
    conn.close()


def test_get_airport_outlook_unknown_airport_raises():
    conn = _tiny_conn()
    try:
        accessors.get_airport_outlook(conn, "NOTREAL")
        assert False, "expected AirportNotFoundError"
    except accessors.AirportNotFoundError:
        pass
    conn.close()
