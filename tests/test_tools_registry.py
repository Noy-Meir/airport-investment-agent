import json

from src.tools.registry import TOOLS, call_tool, get_tool_specs

SCOPE = {"region": "new_england"}


def test_get_tool_specs_shapes():
    specs = get_tool_specs()
    names = {s["name"] for s in specs}
    assert names == {"rank_airports", "score_airport", "compare_airports", "sensitivity"}
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
