import json

import pytest

from src.cache import db
from src.tools.registry import TOOLS, call_tool, get_tool_specs
from tests.fixtures.make_fixture_db import build as build_fixture_db

SCOPE = {"region": "new_england"}


def test_get_tool_specs_shapes():
    specs = get_tool_specs()
    names = {s["name"] for s in specs}
    assert names == {
        "rank_airports", "rank_airports_by_traffic", "score_airport", "compare_airports", "sensitivity",
        "list_region_airports", "get_airport_traffic", "compare_congestion", "get_buildability",
        "get_long_haul_share", "get_unmet_demand_breakdown", "get_forward_outlook", "describe_data_sources",
    }
    for s in specs:
        assert set(s) == {"name", "description", "input_schema"}
        assert isinstance(s["description"], str) and s["description"]
        assert s["input_schema"]["type"] == "object"
        assert "properties" in s["input_schema"]


def test_rank_airports_runs_and_is_json_serializable(fixture_conn):
    result = call_tool("rank_airports", {"scope": SCOPE, "top_n": 5}, conn=fixture_conn)
    assert "error" not in result
    assert set(result) == {"result", "method", "caveats", "source", "confidence"}
    json.dumps(result)


def test_score_airport_runs_and_is_json_serializable(fixture_conn):
    result = call_tool("score_airport", {"code": "BOS"}, conn=fixture_conn)
    assert "error" not in result
    assert set(result) == {"result", "method", "caveats", "source", "confidence"}
    json.dumps(result)


def test_compare_airports_runs_and_is_json_serializable(fixture_conn):
    result = call_tool("compare_airports", {"codes": ["BOS", "TST"]}, conn=fixture_conn)
    assert "error" not in result
    assert set(result) == {"result", "method", "caveats", "source", "confidence"}
    json.dumps(result)


def test_sensitivity_runs_and_is_json_serializable(fixture_conn):
    result = call_tool("sensitivity", {"scope": SCOPE}, conn=fixture_conn)
    assert "error" not in result
    assert set(result) == {"result", "method", "caveats", "source", "confidence"}
    json.dumps(result)


def test_get_unmet_demand_breakdown_runs_and_is_json_serializable(fixture_conn):
    result = call_tool("get_unmet_demand_breakdown", {"code": "TST"}, conn=fixture_conn)
    assert "error" not in result
    assert set(result) == {"result", "method", "caveats", "source", "confidence"}
    json.dumps(result)


def test_call_tool_unknown_tool_never_raises():
    result = call_tool("not_a_real_tool", {})
    assert result["error"]["type"] == "UnknownToolError"


def test_call_tool_missing_required_arg_never_raises(fixture_conn):
    result = call_tool("score_airport", {}, conn=fixture_conn)
    assert result["error"]["type"] == "SchemaError"


def test_call_tool_bad_scope_shape_never_raises(fixture_conn):
    result = call_tool("rank_airports", {"scope": {"region": 123}}, conn=fixture_conn)
    assert result["error"]["type"] == "SchemaError"


def test_call_tool_unknown_airport_never_raises(fixture_conn):
    result = call_tool("score_airport", {"code": "XYZ"}, conn=fixture_conn)
    assert result["error"]["type"] == "AirportNotFoundError"


def test_call_tool_unknown_airport_in_compare_never_raises(fixture_conn):
    result = call_tool("compare_airports", {"codes": ["BOS", "XYZ"]}, conn=fixture_conn)
    assert result["error"]["type"] == "AirportNotFoundError"


def test_score_airport_peer_group_is_trimmed_to_basis_and_size(fixture_conn):
    result = call_tool("score_airport", {"code": "BOS"}, conn=fixture_conn)
    peer_group = result["result"]["peer_group"]
    assert set(peer_group) == {"basis", "size"}
    assert isinstance(peer_group["size"], int)


def test_rank_airports_peer_group_is_trimmed_to_basis_and_size(fixture_conn):
    result = call_tool("rank_airports", {"scope": {"tier": "large"}}, conn=fixture_conn)
    assert result["result"]["ranked"]
    for entry in result["result"]["ranked"]:
        assert set(entry["peer_group"]) == {"basis", "size"}


def test_compare_airports_peer_group_is_trimmed_to_basis_and_size(fixture_conn):
    result = call_tool("compare_airports", {"codes": ["BOS", "TST"]}, conn=fixture_conn)
    for entry in result["result"]["compared"]:
        assert set(entry["peer_group"]) == {"basis", "size"}


def test_score_airport_includes_buildability_flag(fixture_conn):
    result = call_tool("score_airport", {"code": "BOS"}, conn=fixture_conn)
    assert "buildability" in result["result"]


def test_rank_airports_includes_buildability_flag(fixture_conn):
    result = call_tool("rank_airports", {"scope": {"tier": "large"}}, conn=fixture_conn)
    assert result["result"]["ranked"]
    for entry in result["result"]["ranked"]:
        assert "buildability" in entry


def test_compare_airports_includes_buildability_flag(fixture_conn):
    result = call_tool("compare_airports", {"codes": ["BOS", "TST"]}, conn=fixture_conn)
    for entry in result["result"]["compared"]:
        assert "buildability" in entry


def test_list_region_airports_runs_and_is_json_serializable(fixture_conn):
    result = call_tool("list_region_airports", {"region_or_states": {"region": "new_england"}}, conn=fixture_conn)
    assert "error" not in result
    assert set(result) == {"result", "method", "caveats", "source", "confidence"}
    json.dumps(result)


def test_list_region_airports_by_states(fixture_conn):
    result = call_tool("list_region_airports", {"region_or_states": {"states": ["MA"]}}, conn=fixture_conn)
    assert "error" not in result
    json.dumps(result)


def test_get_airport_traffic_runs_and_is_json_serializable(fixture_conn):
    result = call_tool("get_airport_traffic", {"code": "BOS"}, conn=fixture_conn)
    assert "error" not in result
    assert set(result) == {"result", "method", "caveats", "source", "confidence"}
    json.dumps(result)


def test_get_airport_traffic_unknown_airport_never_raises(fixture_conn):
    result = call_tool("get_airport_traffic", {"code": "XYZ"}, conn=fixture_conn)
    assert result["error"]["type"] == "AirportNotFoundError"


def test_compare_congestion_runs_and_is_json_serializable(fixture_conn):
    result = call_tool("compare_congestion", {"codes": ["BOS", "TST"]}, conn=fixture_conn)
    assert "error" not in result
    assert set(result) == {"result", "method", "caveats", "source", "confidence"}
    json.dumps(result)


def test_compare_congestion_reports_as_of_and_domestic_only_caveat(fixture_conn):
    result = call_tool("compare_congestion", {"codes": ["TST"]}, conn=fixture_conn)
    entry = result["result"]["compared"][0]
    assert "as_of" in entry
    assert entry["as_of"] is not None
    assert any("domestic departures only" in c for c in result["caveats"])


def test_compare_congestion_low_coverage_is_never_hidden(fixture_conn):
    result = call_tool("compare_congestion", {"codes": ["TST"]}, conn=fixture_conn)
    entry = result["result"]["compared"][0]
    assert "low_coverage" in entry
    if entry["coverage_pct"] is None or entry["coverage_pct"] < 50.0:
        assert entry["low_coverage"] is True
        assert entry["flights"] is not None or entry["delayed_share_pct"] is None


def test_compare_congestion_unknown_airport_never_raises(fixture_conn):
    result = call_tool("compare_congestion", {"codes": ["BOS", "XYZ"]}, conn=fixture_conn)
    assert result["error"]["type"] == "AirportNotFoundError"


def test_get_buildability_runs_and_is_json_serializable(fixture_conn):
    result = call_tool("get_buildability", {"code": "BOS"}, conn=fixture_conn)
    assert "error" not in result
    assert set(result) == {"result", "method", "caveats", "source", "confidence"}
    json.dumps(result)


def test_get_buildability_no_entry_says_no_constraints_on_file(fixture_conn):
    result = call_tool("get_buildability", {"code": "BOS"}, conn=fixture_conn)
    assert result["result"]["has_constraints"] is False
    assert result["result"]["note"] == "no constraints on file"


def test_get_long_haul_share_anc_matches_decisions_md(real_conn):
    result = call_tool("get_long_haul_share", {"code": "ANC"}, conn=real_conn)
    assert "error" not in result
    json.dumps(result)
    all_group = result["result"]["groups"]["all"]
    assert all_group["shares_pct"][2000] == pytest.approx(47.4, abs=0.05)
    assert all_group["shares_pct"][2500] == pytest.approx(43.2, abs=0.05)
    assert all_group["shares_pct"][3000] == pytest.approx(31.0, abs=0.05)
    assert "passenger" in result["result"]["groups"]
    assert any("no official" in c for c in result["caveats"])


def test_get_long_haul_share_class_group_changes_denominator(real_conn):
    passenger_only = call_tool("get_long_haul_share", {"code": "ANC", "class_group": "passenger"}, conn=real_conn)
    all_classes = call_tool("get_long_haul_share", {"code": "ANC", "class_group": "all"}, conn=real_conn)
    passenger_denom = passenger_only["result"]["groups"]["passenger"]["denominator_departures"]
    all_denom = all_classes["result"]["groups"]["all"]["denominator_departures"]
    assert passenger_denom != all_denom


def test_get_long_haul_share_unknown_airport_never_raises(fixture_conn):
    result = call_tool("get_long_haul_share", {"code": "ZZZ"}, conn=fixture_conn)
    assert result["error"]["type"] == "AirportNotFoundError"


def test_get_buildability_unknown_airport_never_raises(fixture_conn):
    result = call_tool("get_buildability", {"code": "XYZ"}, conn=fixture_conn)
    assert result["error"]["type"] == "AirportNotFoundError"


def test_rank_airports_by_traffic_runs_and_is_json_serializable(fixture_conn):
    result = call_tool("rank_airports_by_traffic", {"scope": {"states": ["MA"]}}, conn=fixture_conn)
    assert "error" not in result
    assert set(result) == {"result", "method", "caveats", "source", "confidence"}
    json.dumps(result)


def test_rank_airports_by_traffic_scope_variants_are_json_serializable(fixture_conn):
    for scope in ({"region": "new_england"}, {"tier": "small"}, {"states": ["MA"]}, {"all": True}):
        result = call_tool("rank_airports_by_traffic", {"scope": scope}, conn=fixture_conn)
        assert "error" not in result
        json.dumps(result)


def test_rank_airports_by_traffic_method_says_departing_passengers(fixture_conn):
    result = call_tool("rank_airports_by_traffic", {"scope": {"all": True}}, conn=fixture_conn)
    assert "departing passengers" in result["method"]
    assert "enplanements" in result["method"]


def test_rank_airports_by_traffic_bad_scope_shape_is_schema_error(fixture_conn):
    result = call_tool("rank_airports_by_traffic", {"scope": {"region": 123}}, conn=fixture_conn)
    assert result["error"]["type"] == "SchemaError"


def test_rank_airports_by_traffic_empty_scope_dict_never_raises(fixture_conn):
    result = call_tool("rank_airports_by_traffic", {"scope": {}}, conn=fixture_conn)
    assert result["error"]["type"] == "ScoringError"


def test_rank_airports_by_traffic_sorted_descending_and_top_n(real_conn):
    result = call_tool("rank_airports_by_traffic", {"scope": {"all": True}, "top_n": 5}, conn=real_conn)
    assert "error" not in result
    ranked = result["result"]["ranked"]
    assert len(ranked) == 5
    pax = [e["ttm_passengers"] for e in ranked]
    assert pax == sorted(pax, reverse=True)
    assert [e["rank"] for e in ranked] == [1, 2, 3, 4, 5]


def test_rank_airports_by_traffic_atl_is_busiest(real_conn):
    result = call_tool("rank_airports_by_traffic", {"scope": {"all": True}, "top_n": 1}, conn=real_conn)
    assert "error" not in result
    assert result["result"]["ranked"][0]["code"] == "ATL"


# --- regression: call_tool(conn=None) must open its own connection ---------
#
# Bug: tool fns that call accessors.py directly (get_long_haul_share,
# get_airport_traffic, compare_congestion, get_unmet_demand_breakdown, ...)
# never opened a connection themselves when conn is None -- only
# src/scoring/score.py's rank_airports/score_airport/compare_airports/
# sensitivity have that fallback (_open_conn). scripts/ask.py calls
# run_turn([], question) with no conn, so any real `python scripts/ask.py`
# run that reached one of the unguarded tools raised
# "'NoneType' object has no attribute 'execute'" from validate_airport_code.
# Pre-existing since each tool was added (confirmed via git history), not
# introduced by the t100_route_agg slimming. Fixed by having call_tool()
# itself open/close data/cache.db when conn is None, so every tool fn always
# receives a real connection. fixture_conn (an injected connection) can't
# catch this class of bug -- it never exercises the conn=None path -- so
# these tests build a real on-disk SQLite db (same schema/builder as the
# fixture) and point src.cache.db.connect's default at it.

@pytest.fixture
def real_disk_db(tmp_path, monkeypatch):
    """
    A real on-disk SQLite db, built with the same builder used for
    tests/fixtures/fixture_cache.db, with src.cache.db.connect's default
    db_path repointed at it -- so call_tool(..., conn=None) (no connection
    injected) opens *this* file instead of the real data/cache.db.
    """
    db_path = str(tmp_path / "regression_cache.db")
    build_fixture_db(path=db_path)
    monkeypatch.setattr(db.connect, "__defaults__", (db_path,))
    return db_path


def test_get_long_haul_share_with_no_conn_opens_real_db(real_disk_db):
    result = call_tool("get_long_haul_share", {"code": "TST"})
    assert "error" not in result
    assert set(result) == {"result", "method", "caveats", "source", "confidence"}
    json.dumps(result)


def test_get_airport_traffic_with_no_conn_opens_real_db(real_disk_db):
    result = call_tool("get_airport_traffic", {"code": "TST"})
    assert "error" not in result
    assert set(result) == {"result", "method", "caveats", "source", "confidence"}
    json.dumps(result)


def test_compare_congestion_with_no_conn_opens_real_db(real_disk_db):
    result = call_tool("compare_congestion", {"codes": ["TST"]})
    assert "error" not in result
    assert set(result) == {"result", "method", "caveats", "source", "confidence"}
    json.dumps(result)


def test_get_unmet_demand_breakdown_with_no_conn_opens_real_db(real_disk_db):
    result = call_tool("get_unmet_demand_breakdown", {"code": "TST"})
    assert "error" not in result
    assert set(result) == {"result", "method", "caveats", "source", "confidence"}
    json.dumps(result)


def test_rank_airports_with_no_conn_still_works(real_disk_db):
    """score.py's own _open_conn fallback already covered this path -- guard against a regression there too."""
    result = call_tool("rank_airports", {"scope": SCOPE, "top_n": 5})
    assert "error" not in result
    json.dumps(result)


def test_describe_data_sources_runs_and_is_json_serializable(fixture_conn):
    result = call_tool("describe_data_sources", {}, conn=fixture_conn)
    assert "error" not in result
    assert set(result) == {"result", "method", "caveats", "source", "confidence"}
    assert result["confidence"] == "high"
    assert len(result["result"]) == 6
    for entry in result["result"]:
        assert set(entry) == {"name", "publisher", "vintage", "access", "caveat", "url"}
    json.dumps(result)


def test_get_forward_outlook_rejects_empty_and_oversized_lists(fixture_conn):
    too_few = call_tool("get_forward_outlook", {"airports": []}, conn=fixture_conn)
    assert too_few["error"]["type"] == "SchemaError"
    too_many = call_tool("get_forward_outlook", {"airports": ["BOS"] * 9}, conn=fixture_conn)
    assert too_many["error"]["type"] == "SchemaError"


def test_get_forward_outlook_unknown_code_is_typed_error(fixture_conn):
    result = call_tool("get_forward_outlook", {"airports": ["ZZZ"]}, conn=fixture_conn)
    assert result["error"]["type"] == "AirportNotFoundError"


def test_get_forward_outlook_no_cached_row_returns_null_fields_and_reason(fixture_conn):
    """Fixture db has no airport_outlook rows at all -- every field must be None plus a reason, never guessed."""
    result = call_tool("get_forward_outlook", {"airports": ["BOS"]}, conn=fixture_conn)
    assert "error" not in result
    assert set(result) == {"result", "method", "caveats", "source", "confidence"}
    entry = result["result"]["airports"][0]
    assert entry["code"] == "BOS"
    assert entry["faa_lid"] is None
    assert entry["enplanements_base"] is None
    assert entry["qualifying_runways"] is None
    assert entry["match_note"]
    assert result["confidence"] == "low"
    for text in (
        "forecast published by the FAA",
        "unconstrained",
        "NOT part of the composite score",
        "FAA fiscal years run Oct-Sep",
        "enplanements_per_runway is only a rough proxy",
    ):
        assert any(text in c for c in result["caveats"]), text
    json.dumps(result)


def test_get_forward_outlook_matched_airport(fixture_conn):
    db.upsert_airport_outlook(
        fixture_conn,
        [{
            "iata_code": "BOS", "faa_lid": "BOS", "taf_base_fy": 2024,
            "enplanements_base": 1000.0, "enplanements_plus5": 1200.0, "enplanements_plus10": 1400.0,
            "cagr_5y": 0.0371, "cagr_10y": 0.0341, "qualifying_runways": 4,
            "enplanements_per_runway": 250.0, "match_note": None,
        }],
        "fixture TAF", "2026-01-01T00:00:00Z",
    )
    result = call_tool("get_forward_outlook", {"airports": ["BOS"]}, conn=fixture_conn)
    assert "error" not in result
    entry = result["result"]["airports"][0]
    assert entry["faa_lid"] == "BOS"
    assert entry["taf_base_fy"] == 2024
    assert entry["enplanements_base"] == 1000.0
    assert entry["match_note"] is None
    assert result["result"]["as_of"] == 2024
    assert result["confidence"] == "medium"
    json.dumps(result)


def test_get_forward_outlook_with_no_conn_opens_real_db(real_disk_db):
    result = call_tool("get_forward_outlook", {"airports": ["TST"]})
    assert "error" not in result
    json.dumps(result)


def test_describe_data_sources_with_no_conn_opens_real_db(real_disk_db):
    """describe_data_sources ignores conn entirely, but call_tool(conn=None) must still not crash opening one."""
    result = call_tool("describe_data_sources", {})
    assert "error" not in result
    assert set(result) == {"result", "method", "caveats", "source", "confidence"}
    json.dumps(result)
