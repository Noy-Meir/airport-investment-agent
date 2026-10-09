"""
Tests for src.agent.rules_router.answer() -- the executable, non-LLM path.

Exercises answer() end-to-end against the tiny committed synthetic fixture
db (tests/fixtures/fixture_cache.db, via the `fixture_conn` fixture in
tests/conftest.py), which only has BOS and TST, the same pattern
tests/test_tools_registry.py and tests/test_narrators.py use.
"""

from src.agent.narrators import CLOSING_LINE
from src.agent.rules_router import State, answer


def test_answer_ranking_calls_tool_and_narrates(fixture_conn):
    out = answer("Rank the large hubs for us.", conn=fixture_conn)
    assert out["mode"] == "rules"
    assert len(out["trace"]) == 1
    assert out["trace"][0]["tool"] == "rank_airports"
    assert "ms" in out["trace"][0]
    assert CLOSING_LINE in out["answer"]
    assert out["usage"]["estimated_cost_usd"] == 0.0
    assert out["usage"]["input_tokens"] == 0
    assert out["state"].last_tool == "rank_airports"
    assert out["state"].last_scope == {"tier": "large"}


def test_answer_long_haul_share(fixture_conn):
    out = answer("What's the long-haul share at TST?", conn=fixture_conn)
    assert out["trace"][0]["tool"] == "get_long_haul_share"
    assert "TST" in out["answer"]
    assert CLOSING_LINE in out["answer"]
    assert out["state"].last_airports == ["TST"]


def test_answer_unmet_demand_breakdown(fixture_conn):
    out = answer("Is there unmet demand at TST?", conn=fixture_conn)
    assert out["trace"][0]["tool"] == "get_unmet_demand_breakdown"
    assert "measured" in out["answer"].lower()
    assert "inferred" in out["answer"].lower()
    assert "unknown" in out["answer"].lower()


def test_answer_compare_with_add_followup(fixture_conn):
    prior = State(last_tool="compare_airports", last_args={"codes": ["BOS"]}, last_airports=["BOS"])
    out = answer("Can you add TST to that comparison?", state=prior, conn=fixture_conn)
    assert out["trace"][0]["tool"] == "compare_airports"
    assert out["trace"][0]["args"]["codes"] == ["BOS", "TST"]
    assert out["state"].last_airports == ["BOS", "TST"]


def test_answer_restate_previous_envelope(fixture_conn):
    first = answer("What's the composite score for BOS and what drives it?", conn=fixture_conn)
    second = answer("What are your caveats?", state=first["state"], conn=fixture_conn)
    assert second["trace"] == []
    assert second["answer"].startswith("Restating the assumptions")
    assert CLOSING_LINE in second["answer"]
    # the restated text is the same narration of the same envelope, not a fresh tool call
    assert second["state"].last_envelope == first["state"].last_envelope


def test_answer_refusal_calls_no_tool():
    out = answer("Guarantee that investing in BOS will pay off in five years.")
    assert out["trace"] == []
    assert "guarantee" in out["answer"].lower() or "certain" in out["answer"].lower()
    assert out["usage"]["estimated_cost_usd"] == 0.0
    assert out["state"].last_tool is None


def test_answer_unknown_airport_code_is_narrated_not_raised(fixture_conn):
    out = answer("What's the load factor at ZZZ?", conn=fixture_conn)
    assert out["trace"][0]["tool"] == "get_airport_traffic"
    assert "error" in out["trace"][0]["result"]
    assert "Could not answer this request" in out["answer"]
    assert CLOSING_LINE in out["answer"]


def test_answer_two_turn_conversation_passes_state(fixture_conn):
    first = answer("Rank the large hubs for us.", conn=fixture_conn)
    assert first["state"].last_ranking_order  # fixture scores at least BOS in the large tier
    top_code = first["state"].last_ranking_order[0]

    second = answer("Tell me about the top one.", state=first["state"], conn=fixture_conn)
    assert second["trace"][0]["tool"] == "score_airport"
    assert second["trace"][0]["args"] == {"code": top_code}
    assert top_code in second["answer"]
