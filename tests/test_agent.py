import json
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from src.agent import agent as agent_mod
from src.agent.agent import MAX_TOOL_ITERATIONS, run_turn
from src.agent.config import ConfigError, load_config


def _status_error(cls, status_code, message):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx2.Response(status_code, request=request)
    return cls(message, response=response, body=None)


def _connection_error(message="Connection error."):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.APIConnectionError(message=message, request=request)


def _usage(input_tokens=10, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0):
    return SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_input_tokens=cache_read_input_tokens,
        cache_creation_input_tokens=cache_creation_input_tokens,
    )


def _text_block(text):
    return SimpleNamespace(type="text", text=text)


def _tool_use_block(id_, name, input_):
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=input_)


def _response(stop_reason, content, usage=None):
    return SimpleNamespace(stop_reason=stop_reason, content=content, usage=usage or _usage())


class FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        resp = self._responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


class FakeClient:
    def __init__(self, responses):
        self.messages = FakeMessages(responses)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-SECRET-abc123")
    monkeypatch.setenv("MODEL_NAME", "claude-test-model")


def test_tool_call_then_final_answer(fixture_conn):
    client = FakeClient([
        _response("tool_use", [_tool_use_block("t1", "score_airport", {"code": "BOS"})]),
        _response("end_turn", [_text_block("BOS looks solid based on its composite score.")]),
    ])
    result = run_turn([], "How does BOS look?", client=client, conn=fixture_conn)

    assert result["answer"] == "BOS looks solid based on its composite score."
    assert len(result["trace"]) == 1
    assert result["trace"][0]["tool"] == "score_airport"
    assert result["trace"][0]["args"] == {"code": "BOS"}
    assert "error" not in result["trace"][0]["result"]
    assert isinstance(result["trace"][0]["ms"], float)


def test_two_sequential_tool_calls(fixture_conn):
    client = FakeClient([
        _response("tool_use", [_tool_use_block("t1", "get_airport_traffic", {"code": "BOS"})]),
        _response("tool_use", [_tool_use_block("t2", "get_airport_traffic", {"code": "TST"})]),
        _response("end_turn", [_text_block("Compared BOS and TST traffic.")]),
    ])
    result = run_turn([], "Compare BOS and TST traffic", client=client, conn=fixture_conn)

    assert result["answer"] == "Compared BOS and TST traffic."
    assert [t["tool"] for t in result["trace"]] == ["get_airport_traffic", "get_airport_traffic"]
    assert [t["args"]["code"] for t in result["trace"]] == ["BOS", "TST"]
    assert len(client.messages.calls) == 3


def test_tool_error_passed_back_with_is_error_true(fixture_conn):
    client = FakeClient([
        _response("tool_use", [_tool_use_block("t1", "score_airport", {"code": "ZZZ"})]),
        _response("end_turn", [_text_block("That airport code isn't in the data.")]),
    ])
    result = run_turn([], "How does ZZZ look?", client=client, conn=fixture_conn)

    assert "error" in result["trace"][0]["result"]

    second_call_messages = client.messages.calls[1]["messages"]
    tool_result_msg = second_call_messages[-1]
    assert tool_result_msg["role"] == "user"
    block = tool_result_msg["content"][0]
    assert block["type"] == "tool_result"
    assert block["is_error"] is True
    assert "error" in json.loads(block["content"])


def test_iteration_cap_triggers_safe_message(fixture_conn):
    responses = [
        _response("tool_use", [_tool_use_block(f"t{i}", "score_airport", {"code": "BOS"})])
        for i in range(MAX_TOOL_ITERATIONS + 1)
    ]
    client = FakeClient(responses)
    result = run_turn([], "Keep going forever", client=client, conn=fixture_conn)

    assert "too many steps" in result["answer"]
    assert len(result["trace"]) == MAX_TOOL_ITERATIONS
    # No further API call was made beyond the one that revealed the cap was hit.
    assert len(client.messages.calls) == MAX_TOOL_ITERATIONS + 1


def test_usage_and_cost_accumulate(fixture_conn):
    client = FakeClient([
        _response(
            "tool_use", [_tool_use_block("t1", "score_airport", {"code": "BOS"})],
            usage=_usage(input_tokens=100, output_tokens=20, cache_read_input_tokens=50, cache_creation_input_tokens=10),
        ),
        _response(
            "end_turn", [_text_block("Done.")],
            usage=_usage(input_tokens=200, output_tokens=30, cache_read_input_tokens=0, cache_creation_input_tokens=0),
        ),
    ])
    result = run_turn([], "How does BOS look?", client=client, conn=fixture_conn)

    usage = result["usage"]
    assert usage["input_tokens"] == 300
    assert usage["output_tokens"] == 50
    assert usage["cache_read_input_tokens"] == 50
    assert usage["cache_creation_input_tokens"] == 10
    expected_cost = (300 / 1e6 * 2.00) + (50 / 1e6 * 10.00) + (50 / 1e6 * 0.20) + (10 / 1e6 * 2.00)
    assert usage["estimated_cost_usd"] == round(expected_cost, 6)
    assert "ESTIMATE" in usage["cost_note"]


def test_old_tool_results_stubbed_on_second_turn(fixture_conn):
    client = FakeClient([
        _response("tool_use", [_tool_use_block("t1", "score_airport", {"code": "BOS"})]),
        _response("end_turn", [_text_block("First turn done.")]),
        _response("tool_use", [_tool_use_block("t2", "score_airport", {"code": "TST"})]),
        _response("end_turn", [_text_block("Second turn done.")]),
    ])
    first = run_turn([], "How does BOS look?", client=client, conn=fixture_conn)
    second = run_turn(first["history"], "How does TST look?", client=client, conn=fixture_conn)

    # Last create() call of the second turn: history should contain the
    # first turn's tool_result stubbed, and the second turn's own
    # (just-produced) tool_result intact.
    last_call_messages = client.messages.calls[-1]["messages"]
    tool_result_messages = [m for m in last_call_messages if m["role"] == "user" and isinstance(m["content"], list)]
    assert len(tool_result_messages) == 2

    first_turn_result = tool_result_messages[0]["content"][0]
    assert first_turn_result["content"] == "[earlier tool result omitted]"

    second_turn_result = tool_result_messages[1]["content"][0]
    assert second_turn_result["content"] != "[earlier tool result omitted]"
    assert "result" in json.loads(second_turn_result["content"])

    # The returned history itself keeps the un-stubbed originals.
    full_history_tool_results = [
        m for m in second["history"] if m["role"] == "user" and isinstance(m["content"], list)
    ]
    assert full_history_tool_results[0]["content"][0]["content"] != "[earlier tool result omitted]"


def test_missing_env_vars_give_clear_error_for_anthropic_provider(tmp_path):
    empty_dotenv = tmp_path / ".env"

    with pytest.raises(ConfigError) as excinfo:
        load_config(dotenv_path=empty_dotenv, env={"LLM_PROVIDER": "anthropic"})

    assert "ANTHROPIC_API_KEY" in str(excinfo.value)
    assert "MODEL_NAME" in str(excinfo.value)


def test_api_error_400_classified_as_bad_request(fixture_conn):
    client = FakeClient([_status_error(anthropic.BadRequestError, 400, "model field is required")])
    result = run_turn([], "How does BOS look?", client=client, conn=fixture_conn)

    assert "error" in result
    assert result["error"]["type"] == "bad_request"
    assert result["error"]["status_code"] == 400
    assert "model field is required" in result["error"]["message"]
    assert len(client.messages.calls) == 1  # non-transient: no retry


def test_api_error_401_classified_as_auth(fixture_conn):
    client = FakeClient([_status_error(anthropic.AuthenticationError, 401, "invalid x-api-key")])
    result = run_turn([], "How does BOS look?", client=client, conn=fixture_conn)

    assert result["error"]["type"] == "auth"
    assert result["error"]["status_code"] == 401
    assert len(client.messages.calls) == 1


def test_connection_error_classified_as_connection(fixture_conn):
    client = FakeClient([_connection_error(), _connection_error()])
    result = run_turn([], "How does BOS look?", client=client, conn=fixture_conn)

    assert result["error"]["type"] == "connection"
    assert result["error"]["status_code"] is None
    assert len(client.messages.calls) == 2  # transient: retried once


def test_api_error_message_redacts_api_key(fixture_conn):
    leaked = "sk-ant-abcDEF123_-xyzSECRET"
    client = FakeClient([_status_error(anthropic.BadRequestError, 400, f"rejected key {leaked} in header")])
    result = run_turn([], "How does BOS look?", client=client, conn=fixture_conn)

    assert leaked not in json.dumps(result)
    assert "[REDACTED]" in result["error"]["message"]


def test_api_key_never_appears_in_returned_values(fixture_conn, monkeypatch):
    secret = "sk-ant-test-SECRET-abc123"
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
    client = FakeClient([
        _response("tool_use", [_tool_use_block("t1", "score_airport", {"code": "BOS"})]),
        _response("end_turn", [_text_block("BOS looks solid.")]),
    ])
    result = run_turn([], "How does BOS look?", client=client, conn=fixture_conn)

    serialized = json.dumps(result, default=str)
    assert secret not in serialized
