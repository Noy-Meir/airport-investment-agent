import json

import pytest

from src.tools.registry import TOOLS, call_tool, get_tool_specs

SCOPE = {"region": "new_england"}


def test_get_tool_specs_shapes():
    specs = get_tool_specs()
    names = {s["name"] for s in specs}
    assert names == {
        "rank_airports", "rank_airports_by_traffic", "score_airport", "compare_airports", "sensitivity",
        "list_region_airports", "get_airport_traffic", "compare_congestion", "get_buildability",
        "get_long_haul_share", "get_unmet_demand_breakdown",
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
