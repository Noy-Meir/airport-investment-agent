"""
Tests for src/analysis/unmet_demand.py. One real-cache test (skips loudly if
data/cache.db isn't built) plus a synthetic-db test for the NULL-seats path.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis.unmet_demand import get_unmet_demand_decomposition
from src.cache import db

ENVELOPE_KEYS = {"result", "method", "caveats", "source", "confidence"}
FORBIDDEN_KEY_SUBSTRINGS = ("unmet_demand_count", "unserved_passengers", "unmet_demand_total")


def _walk_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk_keys(item)


def test_no_forbidden_unmet_demand_keys(real_conn):
    r = get_unmet_demand_decomposition(real_conn, "SFO")
    keys = list(_walk_keys(r))
    for forbidden in FORBIDDEN_KEY_SUBSTRINGS:
        assert not any(forbidden == k for k in keys), f"forbidden key {forbidden!r} found in result tree"


def test_sfo_real_cache(real_conn):
    r = get_unmet_demand_decomposition(real_conn, "SFO")
    assert set(r.keys()) == ENVELOPE_KEYS
    assert r["result"]["airport"] == "SFO"
    assert len(r["result"]["measured"]) == 5
    for fact in r["result"]["measured"]:
        assert {"name", "value", "unit", "as_of", "source", "peer_context"} <= set(fact.keys())
        if fact["value"] is not None:
            assert fact["as_of"] is not None
    assert len(r["result"]["unknown"]) > 0
    for item in r["result"]["unknown"]:
        assert "why_we_cannot_know" in item
        assert "data_needed" in item
    assert r["result"]["inferred"] == []


def test_null_seats_load_factor_is_null_with_reason(tmp_path):
    db_path = str(tmp_path / "null_seats.db")
    conn = db.connect(db_path)
    db.init_schema(conn)

    rows = []
    year, month = 2024, 1
    for i in range(12):
        rows.append({
            "origin_airport_code": "ZZZ", "year": year, "month": month,
            "reporting_month": f"{year:04d}-{month:02d}", "origin_airport_name": "Test Airport",
            "total_departures": 100, "total_passengers": 10000, "total_seats": None,
            "total_load_factor": None, "total_distance_flight_sm": None,
            "domestic_departures": 100, "domestic_passengers": 10000, "domestic_seats": None,
            "domestic_load_factor": None, "intl_departures": 0, "intl_passengers": 0, "intl_seats": 0,
        })
        month += 1
        if month > 12:
            month = 1
            year += 1
    db.upsert_source_a_rows(conn, rows, "test-source", "2024-01-01T00:00:00Z")

    r = get_unmet_demand_decomposition(conn, "ZZZ")
    load_factor_fact = next(f for f in r["result"]["measured"] if f["name"] == "ttm_load_factor")
    assert load_factor_fact["value"] is None
    assert "reason" in load_factor_fact
    conn.close()
