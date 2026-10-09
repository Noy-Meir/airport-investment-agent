"""
Fast regression test for scripts/run_eval.py's core evaluation function and
its number-traceability checker, so they don't silently rot.
"""

import os

import pytest

from src.cache.config import CACHE_DB_PATH


@pytest.fixture(scope="module")
def real_conn():
    if not os.path.exists(CACHE_DB_PATH):
        pytest.skip(f"REAL CACHE MISSING: {CACHE_DB_PATH} not built")
    from src.cache import db

    conn = db.connect()
    yield conn
    conn.close()


def test_evaluate_tool_selection_cases_on_three_cases(real_conn):
    from scripts.run_eval import evaluate_tool_selection_cases

    cases = [
        {
            "id": "assignment_new_england_expansion",
            "question": "Which airports in New England look like good expansion candidates?",
            "rules_expectation": "tool:rank_airports",
        },
        {
            "id": "assignment_la_vs_santa_ana_congestion",
            "question": "How does congestion at LAX compare to John Wayne (Santa Ana)?",
            "rules_expectation": "tool:compare_congestion",
        },
        {
            "id": "out_of_scope_non_us_airport",
            "question": "Is Heathrow a good investment target?",
            "rules_expectation": "refuse",
        },
    ]

    results = evaluate_tool_selection_cases(cases, real_conn)

    assert len(results) == 3
    for r in results:
        assert r["status"] == "pass", (r["id"], r.get("reason"))


# ---------------------------------------------------------------------------
# Unit tests for the two traceability-checker fixes.
# ---------------------------------------------------------------------------

def test_numeric_tokens_splits_hyphenated_ranges_instead_of_misreading_as_negative():
    from scripts.run_eval import numeric_tokens

    tokens = numeric_tokens("rank 2-4 across weight sets")
    assert "2" in tokens
    assert "4" in tokens
    assert "-4" not in tokens


def test_numbers_not_traced_ignores_display_only_position_column():
    from scripts.run_eval import numbers_not_traced

    # "Rank" column values (1, 2) are synthesized by list position, not
    # sourced from the envelope -- only the score (0.43, -0.04) is.
    narration = (
        "| Rank | Airport | Score |\n"
        "|---|---|---|\n"
        "| 1 | BOS | 0.43 |\n"
        "| 2 | PVD | -0.04 |\n"
    )
    envelope = {"result": {"compared": [
        {"airport": "BOS", "composite_score": 0.43},
        {"airport": "PVD", "composite_score": -0.04},
    ]}}
    assert numbers_not_traced(narration, envelope) == []


def test_numbers_not_traced_still_flags_a_genuinely_invented_number():
    from scripts.run_eval import numbers_not_traced

    narration = "BOS: composite investment score 0.99."
    envelope = {"result": {"composite_score": 0.43}}
    assert numbers_not_traced(narration, envelope) == ["0.99"]
