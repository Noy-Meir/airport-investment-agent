"""
Tests for src.agent.respond.respond() -- the single entry point used by the
UI and scripts/ask.py. Uses a fake run_turn (monkeypatched onto
src.agent.agent.run_turn, which respond() imports locally on each call) so
no real Anthropic client is ever constructed.
"""

import pytest

from src.agent.respond import Session, respond


@pytest.fixture(autouse=True)
def _no_real_dotenv(monkeypatch, tmp_path):
    """Keeps the project's real .env (with a real-looking key) out of every
    test's config resolution; each test sets exactly the env vars it needs."""
    monkeypatch.setattr("src.agent.config.DEFAULT_DOTENV_PATH", tmp_path / ".env")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("MODEL_NAME", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)


def _set_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    monkeypatch.setenv("MODEL_NAME", "claude-test-model")


def _fake_run_turn_success(monkeypatch):
    def _fake(history, message, client=None, conn=None):
        return {
            "answer": "LLM answer.",
            "history": history + [{"role": "user", "content": message}],
            "trace": [],
            "usage": {"input_tokens": 1, "output_tokens": 1, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0, "estimated_cost_usd": 0.0, "cost_note": "test"},
        }

    monkeypatch.setattr("src.agent.agent.run_turn", _fake)


def _fake_run_turn_error(monkeypatch, category):
    def _fake(history, message, client=None, conn=None):
        return {
            "answer": "fallback text (should be overridden)",
            "history": history,
            "trace": [],
            "usage": {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0, "estimated_cost_usd": 0.0, "cost_note": "test"},
            "error": {"type": category, "status_code": None, "message": "boom"},
        }

    monkeypatch.setattr("src.agent.agent.run_turn", _fake)


def test_rules_provider_never_touches_client(monkeypatch, fixture_conn):
    _set_key(monkeypatch)
    called = {"run_turn": False}

    def _fail_if_called(*args, **kwargs):
        called["run_turn"] = True
        raise AssertionError("run_turn should not be called under the rules provider")

    monkeypatch.setattr("src.agent.agent.run_turn", _fail_if_called)

    session = Session()
    out = respond(session, "Rank the large hubs for us.", conn=fixture_conn, provider_override="rules")

    assert out["mode"] == "rules"
    assert out["notice"] is None
    assert called["run_turn"] is False


def test_auto_without_key_falls_back_to_rules(fixture_conn):
    """Plain auto-mode operation with no key configured: rules runs
    silently (no notice) since this isn't a mid-turn fallback."""
    session = Session()
    out = respond(session, "Rank the large hubs for us.", conn=fixture_conn)

    assert out["mode"] == "rules"
    assert out["notice"] is None


def test_anthropic_success_path_returns_llm_mode(monkeypatch, fixture_conn):
    _set_key(monkeypatch)
    _fake_run_turn_success(monkeypatch)

    session = Session()
    out = respond(session, "How does BOS look?", conn=fixture_conn)

    assert out["mode"] == "llm"
    assert out["answer"] == "LLM answer."
    assert out["notice"] is None
    assert session.agent_history  # updated by the llm path


@pytest.mark.parametrize("category", ["auth", "bad_request", "rate_limit", "timeout", "connection", "other"])
def test_each_error_category_falls_back_to_rules(monkeypatch, fixture_conn, category):
    _set_key(monkeypatch)
    _fake_run_turn_error(monkeypatch, category)

    session = Session()
    out = respond(session, "Rank the large hubs for us.", conn=fixture_conn)

    assert out["mode"] == "rules"
    assert out["notice"] is not None
    assert "unavailable" in out["notice"]
    assert "fallback text" not in out["answer"]
    assert out["trace"]  # the rules path actually ran a tool


def test_provider_override_rules_wins_over_env(monkeypatch, fixture_conn):
    _set_key(monkeypatch)

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("run_turn should not be called when overridden to rules")

    monkeypatch.setattr("src.agent.agent.run_turn", _fail_if_called)

    session = Session()
    out = respond(session, "Rank the large hubs for us.", conn=fixture_conn, provider_override="rules")
    assert out["mode"] == "rules"


def test_provider_override_anthropic_without_key_falls_back(fixture_conn):
    session = Session()
    out = respond(session, "Rank the large hubs for us.", conn=fixture_conn, provider_override="anthropic")

    assert out["mode"] == "rules"
    assert out["notice"] is not None
    assert "API key" in out["notice"]


def test_session_state_continuity_across_fallback_turn(monkeypatch, fixture_conn):
    _set_key(monkeypatch)
    _fake_run_turn_error(monkeypatch, "timeout")

    session = Session()
    first = respond(session, "Rank the large hubs for us.", conn=fixture_conn)
    assert first["mode"] == "rules"
    assert session.rules_state.last_tool == "rank_airports"

    second = respond(session, "Tell me about the top one.", conn=fixture_conn)
    assert second["mode"] == "rules"
    assert second["trace"][0]["tool"] == "score_airport"
