"""
Tests for src/analysis/unmet_demand.py. One real-cache test (skips loudly if
data/cache.db isn't built) plus a synthetic-db test for the NULL-seats path.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis.unmet_demand import (
    _capacity_pressure_inference,
    _delay_linked_strain_inference,
    _high_utilization_balanced_growth_inference,
    _no_pressure_signal_inference,
    get_unmet_demand_decomposition,
)
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
    for inference in r["result"]["inferred"]:
        assert {"statement", "rule", "based_on", "strength", "alternative_explanations"} <= set(inference.keys())
        assert "proves" not in inference["statement"]
        assert "shows demand" not in inference["statement"]


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


def _fact_with_z(value, z):
    return {"value": value, "peer_context": {"z": z} if z is not None else None}


NULL_FACT = _fact_with_z(None, None)


class TestCapacityPressureInference:
    def test_fires(self):
        r = _capacity_pressure_inference(_fact_with_z(0.9, 1.2), _fact_with_z(5.0, 0.6))
        assert r is not None
        assert "is consistent with" in r["statement"]
        assert set(r["alternative_explanations"]) == {"seasonality", "airline schedule changes"}

    def test_does_not_fire_below_threshold(self):
        assert _capacity_pressure_inference(_fact_with_z(0.9, 0.5), _fact_with_z(5.0, 0.6)) is None
        assert _capacity_pressure_inference(_fact_with_z(0.9, 1.2), _fact_with_z(5.0, 0.4)) is None

    def test_null_input_never_fires(self):
        assert _capacity_pressure_inference(NULL_FACT, _fact_with_z(5.0, 0.6)) is None
        assert _capacity_pressure_inference(_fact_with_z(0.9, 1.2), NULL_FACT) is None
        assert _capacity_pressure_inference(NULL_FACT, NULL_FACT) is None

    def test_sfo_like_raw_gap_negative_does_not_fire_rule1_fires_rule4(self):
        """SFO-shaped inputs: LF z=1.01 (high rel. to peers), raw gap=-0.02 (seats outgrew
        passengers), gap z=1.60 (high rel. to peers only because peers' gaps are lower/more
        negative). Peer-relative z alone must not be enough -- raw gap must also be > 0."""
        lf_fact = _fact_with_z(0.8255, 1.01)
        gap_fact = _fact_with_z(-0.02, 1.60)
        assert _capacity_pressure_inference(lf_fact, gap_fact) is None
        r4 = _high_utilization_balanced_growth_inference(lf_fact, gap_fact)
        assert r4 is not None
        assert "NOT evidence of growing unmet demand" in r4["statement"]


class TestDelayLinkedStrainInference:
    def test_fires(self):
        r = _delay_linked_strain_inference(_fact_with_z(25.0, 1.1))
        assert r is not None
        assert "is consistent with" in r["statement"]
        assert set(r["alternative_explanations"]) == {"weather", "a single hub carrier's operations"}

    def test_does_not_fire_below_threshold(self):
        assert _delay_linked_strain_inference(_fact_with_z(25.0, 0.9)) is None

    def test_null_input_never_fires(self):
        assert _delay_linked_strain_inference(NULL_FACT) is None


class TestHighUtilizationBalancedGrowthInference:
    def test_fires(self):
        r = _high_utilization_balanced_growth_inference(_fact_with_z(0.9, 1.2), _fact_with_z(1.0, 0.1))
        assert r is not None
        assert "is consistent with" in r["statement"]
        assert "NOT evidence of growing unmet demand" in r["statement"]

    def test_does_not_fire_when_gap_also_elevated(self):
        assert _high_utilization_balanced_growth_inference(_fact_with_z(0.9, 1.2), _fact_with_z(5.0, 0.6)) is None

    def test_does_not_fire_when_load_factor_not_elevated(self):
        assert _high_utilization_balanced_growth_inference(_fact_with_z(0.9, 0.5), _fact_with_z(1.0, 0.1)) is None

    def test_null_input_never_fires(self):
        assert _high_utilization_balanced_growth_inference(NULL_FACT, _fact_with_z(1.0, 0.1)) is None
        assert _high_utilization_balanced_growth_inference(_fact_with_z(0.9, 1.2), NULL_FACT) is None


class TestNoPressureSignalInference:
    def test_fires_when_nothing_elevated(self):
        r = _no_pressure_signal_inference(
            _fact_with_z(0.8, 0.1), _fact_with_z(1.0, 0.1), _fact_with_z(10.0, 0.2)
        )
        assert r is not None
        assert "no sign of capacity pressure" in r["statement"]
        assert "proves" not in r["statement"]
        assert "unmet demand of" not in r["statement"]

    def test_fires_when_delayed_share_unmeasurable(self):
        r = _no_pressure_signal_inference(_fact_with_z(0.8, 0.1), _fact_with_z(1.0, 0.1), NULL_FACT)
        assert r is not None

    def test_does_not_fire_when_load_factor_elevated(self):
        assert _no_pressure_signal_inference(
            _fact_with_z(0.9, 1.2), _fact_with_z(1.0, 0.1), _fact_with_z(10.0, 0.2)
        ) is None

    def test_does_not_fire_when_gap_elevated(self):
        assert _no_pressure_signal_inference(
            _fact_with_z(0.8, 0.1), _fact_with_z(5.0, 0.6), _fact_with_z(10.0, 0.2)
        ) is None

    def test_does_not_fire_when_delayed_share_elevated(self):
        assert _no_pressure_signal_inference(
            _fact_with_z(0.8, 0.1), _fact_with_z(1.0, 0.1), _fact_with_z(25.0, 1.1)
        ) is None

    def test_null_input_never_fires(self):
        assert _no_pressure_signal_inference(NULL_FACT, _fact_with_z(1.0, 0.1), _fact_with_z(10.0, 0.2)) is None
        assert _no_pressure_signal_inference(_fact_with_z(0.8, 0.1), NULL_FACT, _fact_with_z(10.0, 0.2)) is None


def test_no_forbidden_wording_in_any_inference_statement():
    cases = [
        _capacity_pressure_inference(_fact_with_z(0.9, 1.2), _fact_with_z(5.0, 0.6)),
        _delay_linked_strain_inference(_fact_with_z(25.0, 1.1)),
        _high_utilization_balanced_growth_inference(_fact_with_z(0.9, 1.2), _fact_with_z(1.0, 0.1)),
        _no_pressure_signal_inference(_fact_with_z(0.8, 0.1), _fact_with_z(1.0, 0.1), _fact_with_z(10.0, 0.2)),
    ]
    for inference in cases:
        assert inference is not None
        assert "proves" not in inference["statement"]
        assert "unmet demand of" not in inference["statement"]
