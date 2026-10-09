import json
import os

from src.agent.rules_router import plan, resolve_airport_codes

EVAL_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "eval", "tool_selection_cases.json")


# --- the 4 brief questions --------------------------------------------------

def test_expansion_candidates_region():
    p = plan("Which airports in New England look like good expansion candidates?")
    assert p.tool == "rank_airports"
    assert p.args["scope"] == {"region": "new_england"}


def test_congestion_comparison():
    p = plan("How does congestion at LAX compare to John Wayne (Santa Ana)?")
    assert p.tool == "compare_congestion"
    assert p.args["codes"] == ["LAX", "SNA"]


def test_long_haul_share():
    p = plan("What percent of flights out of Anchorage are long-haul?")
    assert p.tool == "get_long_haul_share"
    assert p.args["code"] == "ANC"


def test_unmet_demand():
    p = plan("Is there unmet demand at SFO, and why?")
    assert p.tool == "get_unmet_demand_breakdown"
    assert p.args["code"] == "SFO"


# --- paraphrases -------------------------------------------------------

def test_expansion_paraphrase_tier():
    p = plan("Rank the large hubs for us.")
    assert p.tool == "rank_airports"
    assert p.args["scope"] == {"tier": "large"}


def test_expansion_paraphrase_states():
    p = plan("What are the best expansion candidates in CT and ME?")
    assert p.tool == "rank_airports"
    assert p.args["scope"] == {"states": ["CT", "ME"]}


def test_expansion_paraphrase_state_names():
    p = plan("Looking for expansion candidates in Connecticut and Maine.")
    assert p.tool == "rank_airports"
    assert set(p.args["scope"]["states"]) == {"CT", "ME"}


def test_congestion_paraphrase_delays():
    p = plan("Are BDL and BTV equally delayed?")
    assert p.tool == "compare_congestion"
    assert p.args["codes"] == ["BDL", "BTV"]


def test_congestion_paraphrase_on_time():
    p = plan("What's the on-time performance of ORD vs DFW?")
    assert p.tool == "compare_congestion"
    assert p.args["codes"] == ["ORD", "DFW"]


def test_long_haul_paraphrase_threshold():
    p = plan("What's the long-haul share at MIA using a 3000-mile cutoff instead of the default?")
    assert p.tool == "get_long_haul_share"
    assert p.args["code"] == "MIA"
    assert p.args["thresholds_mi"] == [3000]


def test_long_haul_paraphrase_longhaul_nospace():
    p = plan("Longhaul share for ANC?")
    assert p.tool == "get_long_haul_share"
    assert p.args["code"] == "ANC"


def test_unmet_demand_paraphrase_constrained():
    p = plan("Is ORD constrained right now?")
    assert p.tool == "get_unmet_demand_breakdown"
    assert p.args["code"] == "ORD"


def test_unmet_demand_paraphrase_pressure():
    p = plan("What's the demand pressure situation at DEN?")
    assert p.tool == "get_unmet_demand_breakdown"
    assert p.args["code"] == "DEN"


def test_compare_airports_scores():
    p = plan("Compare SFO and SNA scores directly.")
    assert p.tool == "compare_airports"
    assert p.args["codes"] == ["SFO", "SNA"]


def test_score_airport_single():
    p = plan("What's the composite score for DEN and what drives it?")
    assert p.tool == "score_airport"
    assert p.args["code"] == "DEN"


def test_airport_traffic_single():
    p = plan("How many passengers does BOS carry?")
    assert p.tool == "get_airport_traffic"
    assert p.args["code"] == "BOS"


def test_buildability():
    p = plan("What are the buildability constraints at PVD?")
    assert p.tool == "get_buildability"
    assert p.args["code"] == "PVD"


def test_describe_data_sources():
    p = plan("What data sources do you use?")
    assert p.tool == "describe_data_sources"
    assert p.args == {}


def test_busiest_airports_default_all_scope():
    p = plan("What are the 10 busiest airports?")
    assert p.tool == "rank_airports_by_traffic"
    assert p.args["scope"] == {"all": True}
    assert p.args["top_n"] == 10


def test_sensitivity():
    p = plan("How robust is the New England ranking if we weight congestion more heavily?")
    assert p.tool == "sensitivity"
    assert p.args["scope"] == {"region": "new_england"}


def test_sensitivity_no_scope_is_unknown():
    p = plan("How robust is this ranking if we weight congestion more heavily?")
    assert p.tool is None


# --- unknown / unresolved -------------------------------------------------

def test_unknown_for_unrelated_text():
    p = plan("What's a good recipe for banana bread?")
    assert p.tool is None
    assert p.intent == "unknown"


def test_unknown_when_no_airport_resolvable():
    p = plan("What's the TTM growth rate for an airport with no recent BTS data on file?")
    assert p.tool is None


# --- airport resolution helper -----------------------------------------

def test_bare_la_resolves_to_lax():
    assert resolve_airport_codes("flights out of LA") == ["LAX"]


def test_ordinary_words_not_mistaken_for_codes_without_conn():
    assert resolve_airport_codes("Compare airports in the New York metro area.") == []


# --- eval harness: matched / unmatched / out-of-scope buckets ---------

def test_eval_cases_bucketed():
    with open(EVAL_PATH) as f:
        cases = json.load(f)

    matched, unmatched, out_of_scope = [], [], []
    for case in cases:
        if "previous_turn" in case or case["id"].startswith("followup_") or not case["expected_tools"]:
            out_of_scope.append(case["id"])
            continue
        p = plan(case["question"])
        if p.tool is not None and p.tool in case["expected_tools"]:
            matched.append(case["id"])
        else:
            unmatched.append(case["id"])

    print(f"\nmatched ({len(matched)}): {matched}")
    print(f"unmatched ({len(unmatched)}): {unmatched}")
    print(f"out_of_scope ({len(out_of_scope)}): {out_of_scope}")

    # Every case is accounted for in exactly one bucket; nothing silently dropped.
    assert len(matched) + len(unmatched) + len(out_of_scope) == len(cases)
    # This step's known-good core: the 4 brief questions must match.
    for required in (
        "assignment_new_england_expansion",
        "assignment_la_vs_santa_ana_congestion",
        "assignment_anchorage_long_haul_pct",
        "assignment_sfo_unmet_demand",
    ):
        assert required in matched
