"""
Tests for src/scoring/normalize.py (pure, hand-computed) and
src/scoring/signals.py's NULL-congestion-on-low-coverage path (synthetic
db, no real cache.db needed).
"""

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.cache import db
from src.scoring.normalize import MIN_PEER_GROUP_SIZE, Z_CLIP, robust_z_scores
from src.scoring.signals import get_congestion_signal_ttm

ENVELOPE_KEYS = {"result", "method", "caveats", "source", "confidence"}


# --- normalize.py -----------------------------------------------------------

def test_robust_z_hand_computed():
    # values: 10, 12, 14, 16, 18 -> median=14, abs devs: 4,2,0,2,4 -> MAD=2.
    # z(A=10) = (10-14)/(1.4826*2) = -4/2.9652 = -1.34890...
    values = {"A": 10.0, "B": 12.0, "C": 14.0, "D": 16.0, "E": 18.0}
    r = robust_z_scores(values)
    assert set(r.keys()) == ENVELOPE_KEYS
    assert r["result"]["A"] == pytest.approx(-1.3489, abs=1e-3)
    assert r["result"]["C"] == pytest.approx(0.0, abs=1e-9)
    assert r["result"]["E"] == pytest.approx(1.3489, abs=1e-3)
    assert r["confidence"] == "high"


def test_robust_z_clipping():
    # one extreme outlier must clip to +3, not exceed it.
    values = {"A": 10.0, "B": 11.0, "C": 10.5, "D": 10.2, "E": 1000.0}
    r = robust_z_scores(values)
    assert r["result"]["E"] == Z_CLIP
    assert any("clipped" in c for c in r["caveats"])


def test_robust_z_mad_zero():
    # four identical values + one different -> median=5, MAD=0.
    values = {"A": 5.0, "B": 5.0, "C": 5.0, "D": 5.0, "E": 9.0}
    r = robust_z_scores(values)
    assert r["result"] == {"A": 0.0, "B": 0.0, "C": 0.0, "D": 0.0, "E": 0.0}
    assert any("MAD is 0" in c for c in r["caveats"])
    assert r["confidence"] == "medium"


def test_robust_z_small_peer_group():
    values = {"A": 1.0, "B": 2.0, "C": 3.0}
    assert len(values) < MIN_PEER_GROUP_SIZE
    r = robust_z_scores(values)
    assert r["result"] is None
    assert any("peer group too small" in c for c in r["caveats"])
    assert r["confidence"] == "low"


def test_robust_z_excludes_null_values():
    values = {"A": 10.0, "B": 12.0, "C": 14.0, "D": 16.0, "E": 18.0, "F": None}
    r = robust_z_scores(values)
    assert "F" not in r["result"]
    assert any("excluded 1 peer" in c for c in r["caveats"])


# --- signals.py: NULL congestion on low coverage ----------------------------

@pytest.fixture
def low_coverage_conn(tmp_path):
    """
    Synthetic db where OTP flight counts are tiny relative to T-100
    domestic_departures (coverage well under 50%), across a full 12-month
    TTM window so get_congestion_ttm doesn't short-circuit on "no cached
    data" first.
    """
    path = str(tmp_path / "low_coverage.db")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    db.init_schema(conn)

    source_a_rows = []
    otp_agg = {}
    months = [(2025, m) for m in range(1, 13)]
    for year, month in months:
        source_a_rows.append({
            "origin_airport_code": "LOW", "year": year, "month": month,
            "reporting_month": f"{year}-{month:02d}-01T00:00:00.000", "origin_airport_name": "Low Coverage Field",
            "total_departures": 1000.0, "total_passengers": 50000.0, "total_seats": 60000.0,
            "total_load_factor": 83.3, "total_distance_flight_sm": 900.0,
            "domestic_departures": 1000.0, "domestic_passengers": 50000.0, "domestic_seats": 60000.0,
            "domestic_load_factor": 83.3, "intl_departures": 0.0, "intl_passengers": 0.0, "intl_seats": 0.0,
        })
        otp_agg[(year, month, "LOW")] = {
            "n_flights": 100, "n_cancelled": 0, "n_diverted": 0,
            "n_taxi_out_obs": 100, "sum_taxi_out": 1500.0,
            "n_dep_delay_obs": 100, "sum_dep_delay_min": 1000.0, "n_dep_del15": 20,
            "n_arr_delay_obs": 100, "sum_arr_delay_min": 900.0, "n_arr_del15": 18,
            "n_carriers": 1,
        }
    db.upsert_source_a_rows(conn, source_a_rows, "synthetic", "2026-01-01T00:00:00Z")
    db.upsert_otp_rows(conn, otp_agg, "synthetic", "2026-01-01T00:00:00Z")
    yield conn
    conn.close()


def test_congestion_null_for_low_otp_coverage(low_coverage_conn):
    # coverage = 100*12 OTP flights / 1000*12 T-100 domestic_departures = 10% < 50% floor.
    r = get_congestion_signal_ttm(low_coverage_conn, "LOW")
    assert set(r.keys()) == ENVELOPE_KEYS
    assert r["result"] is None
    assert any("low OTP coverage" in c and "10.0%" in c for c in r["caveats"])
    assert r["confidence"] == "low"
