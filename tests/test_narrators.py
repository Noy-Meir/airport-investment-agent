"""
Tests for src/agent/narrators.py -- the deterministic, no-LLM narration path.

Each tool is exercised with call_tool(..., conn=fixture_conn) against the
tiny committed synthetic fixture db (tests/fixtures/fixture_cache.db), the
same pattern tests/test_tools_registry.py uses. narrate(name, envelope) is
then checked for:
  (a) the narration contains the key numbers/labels from the result,
  (b) it states a data window and a confidence word,
  (c) caveats present in the envelope (or its nested entries) show up when
      they match the "never drop" categories,
  (d) the closing line is present,
  (e) every numeric token printed in the narration traces back to a number
      that actually appears somewhere in the envelope (no invented figures).
Error-path tests (unknown airport code) check the short error message and
that the closing line still appears.
"""

import json
import re

import pytest

from src.cache import db
from src.tools.registry import call_tool, get_tool_specs
from src.agent.narrators import (
    CLOSING_LINE, narrate, _NARRATORS, _score_table, _fmt_pp2, _calendar_year_window_sentence,
)

_CONFIDENCE_WORDS = {"low", "medium", "high"}


# ---------------------------------------------------------------------------
# helper (e): every number printed in the narration must trace back to the
# envelope. Narrators are now allowed to apply fixed-decimal DISPLAY rounding
# (composite scores to 2dp, percentages to 1dp, ratios/load factors to 3dp --
# see src/agent/narrators.py's _fmt_score/_fmt_pct1/_fmt_ratio3) on top of a
# number that is itself already in the envelope. So instead of requiring an
# exact substring match in the envelope's JSON, we collect every numeric leaf
# that actually appears anywhere in the envelope and check that the printed
# token equals one of those leaves once BOTH are rounded to the token's own
# number of printed decimal places. This still fails loudly if a narration
# prints a number that doesn't derive from the envelope at all.
# ---------------------------------------------------------------------------

_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}(?:-\d{2})?")
_NUM_TOKEN_RE = re.compile(r"-?\d[\d,]*\.?\d*")


def _numeric_tokens(text):
    # Strip ISO dates (YYYY-MM[-DD]) first -- they are a label (the as_of
    # window), not a measured number, and their embedded "-MM" fragment
    # would otherwise misparse as a small negative number.
    text = _ISO_DATE_RE.sub(" ", text)
    out = []
    for raw in _NUM_TOKEN_RE.findall(text):
        cleaned = raw.replace(",", "")
        if cleaned in ("", "-", "."):
            continue
        out.append(cleaned)
    return out


def _collect_numeric_leaves(obj, out):
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        out.append(float(obj))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str):
                # numbers embedded in field names themselves (e.g. "stable_top3"
                # -- the ranking-stability window is a fixed analysis parameter
                # baked into the field name, not a separate numeric value)
                for raw in _NUM_TOKEN_RE.findall(_ISO_DATE_RE.sub(" ", k)):
                    cleaned = raw.replace(",", "")
                    if cleaned not in ("", "-", "."):
                        try:
                            out.append(float(cleaned))
                        except ValueError:
                            pass
            _collect_numeric_leaves(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect_numeric_leaves(v, out)
    elif isinstance(obj, str):
        # numbers embedded in caveat/method prose (e.g. "only 2/4 signals")
        for raw in _NUM_TOKEN_RE.findall(_ISO_DATE_RE.sub(" ", obj)):
            cleaned = raw.replace(",", "")
            if cleaned not in ("", "-", "."):
                try:
                    out.append(float(cleaned))
                except ValueError:
                    pass


def assert_numbers_trace_to_envelope(narration, envelope):
    leaves = []
    _collect_numeric_leaves(envelope, leaves)
    for token in _numeric_tokens(narration):
        try:
            tval = float(token)
        except ValueError:
            continue
        decimals = len(token.split(".", 1)[1]) if "." in token else 0
        tol = (0.5 * (10 ** -decimals)) + 1e-9
        ok = any(
            abs(round(leaf, decimals) - tval) < 1e-6 or abs(leaf - tval) < tol
            for leaf in leaves
        )
        assert ok, (
            f"narration number {token!r} (from {narration[:80]!r}...) does not trace back to "
            f"anything in the envelope (within display-rounding tolerance)"
        )


def assert_common_shape(text, envelope):
    assert text.strip().endswith(CLOSING_LINE)
    assert "data window" in text.lower() or "window" in text.lower()
    assert any(word in text for word in _CONFIDENCE_WORDS)
    assert_numbers_trace_to_envelope(text, envelope)


# ---------------------------------------------------------------------------
# rank_airports
# ---------------------------------------------------------------------------

def test_narrate_rank_airports_populated(fixture_conn):
    env = call_tool("rank_airports", {"scope": {"tier": "large"}, "top_n": 5}, conn=fixture_conn)
    text = narrate("rank_airports", env)
    assert_common_shape(text, env)
    assert "TST" in text
    assert "Rank" in text and "Score" in text and "Confidence" in text  # table header


def test_narrate_rank_airports_empty_scope(fixture_conn):
    env = call_tool("rank_airports", {"scope": {"region": "new_england"}, "top_n": 5}, conn=fixture_conn)
    assert env["result"] is None
    text = narrate("rank_airports", env)
    assert text.strip().endswith(CLOSING_LINE)
    assert any(word in text for word in _CONFIDENCE_WORDS)


def test_narrate_rank_airports_scope_label_is_readable(fixture_conn):
    env = call_tool("rank_airports", {"scope": {"tier": "large"}, "top_n": 5}, conn=fixture_conn)
    text = narrate("rank_airports", env)
    assert "large-hub airports" in text
    assert "tier:large" not in text


def test_narrate_rank_airports_no_raw_z_or_list_repr(fixture_conn):
    env = call_tool("rank_airports", {"scope": {"tier": "large"}, "top_n": 5}, conn=fixture_conn)
    text = narrate("rank_airports", env)
    assert "z=" not in text
    assert "['" not in text and '["' not in text


# ---------------------------------------------------------------------------
# rank_airports_by_traffic
# ---------------------------------------------------------------------------

def test_narrate_rank_airports_by_traffic(fixture_conn):
    env = call_tool("rank_airports_by_traffic", {"scope": {"tier": "large"}, "top_n": 5}, conn=fixture_conn)
    text = narrate("rank_airports_by_traffic", env)
    assert_common_shape(text, env)
    assert "TST" in text
    assert "departing passengers" in text.lower()
    assert "large-hub airports" in text
    # TST's growth_pct is null in the fixture -- the row must still show every
    # column, with "n/a" for the missing growth cell (not "unknown", and not
    # a blank/short row).
    assert env["result"]["ranked"][0]["code"] == "TST"
    assert env["result"]["ranked"][0]["growth_pct"] is None
    tst_row = next(line for line in text.splitlines() if line.startswith("| 1 | TST"))
    assert tst_row.rstrip().endswith("| n/a |")
    cells = [c.strip() for c in tst_row.strip().strip("|").split("|")]
    assert len(cells) == 6  # Rank, Airport, Name, Tier, Departing passengers, Growth


def test_narrate_rank_airports_by_traffic_empty_scope(fixture_conn):
    env = call_tool("rank_airports_by_traffic", {"scope": {"region": "new_england"}, "top_n": 5}, conn=fixture_conn)
    assert env["result"] is None
    text = narrate("rank_airports_by_traffic", env)
    assert text.strip().endswith(CLOSING_LINE)
    assert any(word in text for word in _CONFIDENCE_WORDS)


# ---------------------------------------------------------------------------
# score_airport
# ---------------------------------------------------------------------------

def test_narrate_score_airport_success(fixture_conn):
    env = call_tool("score_airport", {"code": "TST"}, conn=fixture_conn)
    text = narrate("score_airport", env)
    assert_common_shape(text, env)
    assert "TST" in text
    assert "large" in text  # tier
    # buildability "no entry" must survive the cap-at-4 rule
    assert "no entry on file" in text.lower()
    # no raw z-score numbers -- only the plain-English phrase (if any signal fired)
    assert "z=" not in text
    assert "peer z-score" not in text


def test_narrate_score_airport_unknown_airport_is_error(fixture_conn):
    env = call_tool("score_airport", {"code": "ZZZ"}, conn=fixture_conn)
    assert "error" in env
    text = narrate("score_airport", env)
    assert "could not answer" in text.lower()
    assert "AirportNotFoundError" in text
    assert text.strip().endswith(CLOSING_LINE)
    assert "Traceback" not in text


# ---------------------------------------------------------------------------
# compare_airports
# ---------------------------------------------------------------------------

def test_narrate_compare_airports(fixture_conn):
    env = call_tool("compare_airports", {"codes": ["BOS", "TST"]}, conn=fixture_conn)
    text = narrate("compare_airports", env)
    assert_common_shape(text, env)
    assert "BOS" in text and "TST" in text


def test_narrate_compare_airports_unknown_airport_is_error(fixture_conn):
    env = call_tool("compare_airports", {"codes": ["BOS", "ZZZ"]}, conn=fixture_conn)
    assert "error" in env
    text = narrate("compare_airports", env)
    assert "could not answer" in text.lower()
    assert text.strip().endswith(CLOSING_LINE)


# ---------------------------------------------------------------------------
# sensitivity
# ---------------------------------------------------------------------------

def test_narrate_sensitivity(fixture_conn):
    env = call_tool("sensitivity", {"scope": {"tier": "large"}}, conn=fixture_conn)
    text = narrate("sensitivity", env)
    assert_common_shape(text, env)
    assert "equal" in text or "growth_heavy" in text  # weight set names
    assert ("stable" in text.lower()) or ("not stable" in text.lower())


# ---------------------------------------------------------------------------
# list_region_airports
# ---------------------------------------------------------------------------

def test_narrate_list_region_airports(fixture_conn):
    env = call_tool("list_region_airports", {"region_or_states": {"region": "new_england"}}, conn=fixture_conn)
    text = narrate("list_region_airports", env)
    assert_common_shape(text, env)
    # scope label must be human-readable, not the raw internal "region:new_england" string
    assert "new england region" in text.lower()
    assert "region:new_england" not in text


# ---------------------------------------------------------------------------
# get_airport_traffic
# ---------------------------------------------------------------------------

def test_narrate_get_airport_traffic_success(fixture_conn):
    env = call_tool("get_airport_traffic", {"code": "TST"}, conn=fixture_conn)
    text = narrate("get_airport_traffic", env)
    assert_common_shape(text, env)
    assert "departing passengers" in text.lower()
    r = env["result"]
    assert f"{int(r['ttm_passengers']):,}" in text  # 100 departing passengers
    assert "2025-02" in text  # as_of


def test_narrate_get_airport_traffic_unknown_airport_is_error(fixture_conn):
    env = call_tool("get_airport_traffic", {"code": "ZZZ"}, conn=fixture_conn)
    assert "error" in env
    text = narrate("get_airport_traffic", env)
    assert "could not answer" in text.lower()
    assert text.strip().endswith(CLOSING_LINE)


# ---------------------------------------------------------------------------
# compare_congestion
# ---------------------------------------------------------------------------

def test_narrate_compare_congestion(fixture_conn):
    env = call_tool("compare_congestion", {"codes": ["TST"]}, conn=fixture_conn)
    text = narrate("compare_congestion", env)
    assert_common_shape(text, env)
    assert "domestic departures only" in text.lower()
    r = env["result"]["compared"][0]
    assert str(r["flights"]) in text
    # (12) partial-window coverage note: TST's fixture has a 2-month overlap,
    # so the table must say "2 of 12 months", not just "Low coverage: no".
    assert "of 12 months" in text


def test_narrate_compare_congestion_unknown_airport_is_error(fixture_conn):
    env = call_tool("compare_congestion", {"codes": ["TST", "ZZZ"]}, conn=fixture_conn)
    assert "error" in env
    text = narrate("compare_congestion", env)
    assert "could not answer" in text.lower()
    assert text.strip().endswith(CLOSING_LINE)


# ---------------------------------------------------------------------------
# get_long_haul_share
# ---------------------------------------------------------------------------

def test_narrate_get_long_haul_share(fixture_conn):
    env = call_tool("get_long_haul_share", {"code": "TST"}, conn=fixture_conn)
    text = narrate("get_long_haul_share", env)
    assert_common_shape(text, env)
    # passenger-share and all-flights-share must both appear, side by side, per threshold
    assert "passenger flights" in text
    assert "all flights" in text
    assert "parameter" in text.lower()  # threshold-is-a-parameter wording
    assert "2,000" in text  # a threshold value (thousands-separated)


def test_narrate_get_long_haul_share_unknown_airport_is_error(fixture_conn):
    env = call_tool("get_long_haul_share", {"code": "ZZZ"}, conn=fixture_conn)
    assert "error" in env
    text = narrate("get_long_haul_share", env)
    assert "could not answer" in text.lower()
    assert text.strip().endswith(CLOSING_LINE)


def test_narrate_get_long_haul_share_window_is_calendar_year_not_ttm(fixture_conn):
    """get_long_haul_share reports over a calendar year (result['year']), never a
    trailing-twelve-month window -- the window sentence must say so and must
    never say "TTM"."""
    env = call_tool("get_long_haul_share", {"code": "TST"}, conn=fixture_conn)
    year = env["result"]["year"]
    text = narrate("get_long_haul_share", env)
    assert "TTM" not in text
    assert "trailing" not in text.lower()
    assert f"calendar year {year}" in text


def test_calendar_year_window_sentence_helper():
    s = _calendar_year_window_sentence(2025, "BTS T-100 Segment (All Carriers), route level (FMG), cached 2026-10-08T21:37:12Z")
    assert s == "Data window: calendar year 2025, BTS T-100 Segment (All Carriers), route level (FMG)."
    assert "TTM" not in s
    s_no_source = _calendar_year_window_sentence(2025, None)
    assert s_no_source == "Data window: calendar year 2025."
    s_no_year = _calendar_year_window_sentence(None, None)
    assert "year not specified" in s_no_year


# ---------------------------------------------------------------------------
# get_buildability
# ---------------------------------------------------------------------------

def test_narrate_get_buildability_no_entry(fixture_conn):
    env = call_tool("get_buildability", {"code": "BOS"}, conn=fixture_conn)
    text = narrate("get_buildability", env)
    assert_common_shape(text, env)
    assert "no constraints on file" in text.lower()
    assert "not researched" in text.lower() or "not verified unconstrained" in text.lower()


def test_narrate_get_buildability_unknown_airport_is_error(fixture_conn):
    env = call_tool("get_buildability", {"code": "ZZZ"}, conn=fixture_conn)
    assert "error" in env
    text = narrate("get_buildability", env)
    assert "could not answer" in text.lower()
    assert text.strip().endswith(CLOSING_LINE)


# ---------------------------------------------------------------------------
# get_unmet_demand_breakdown
# ---------------------------------------------------------------------------

def test_narrate_get_unmet_demand_breakdown(fixture_conn):
    env = call_tool("get_unmet_demand_breakdown", {"code": "TST"}, conn=fixture_conn)
    text = narrate("get_unmet_demand_breakdown", env)
    assert_common_shape(text, env)
    assert "Measured:" in text
    assert "Inferred" in text
    assert "Unknown" in text
    assert "consistent with" in text.lower()
    assert "no single 'unmet demand' number" in text.lower() or "no single" in text.lower()
    # never a single composite unmet-demand number presented as THE answer --
    # the three section headers must all be present (already checked above).
    # (9) nested as_of (measured facts carry their own as_of) must be found,
    # not fall back to the "does not carry an explicit as_of date" message.
    assert "does not carry an explicit as_of date" not in text
    # (11) inference confidence must use the distinct "Confidence in this
    # inference:" wording, never the bracket-tag style, and must stay
    # distinguishable from the overall "Confidence:" sentence.
    assert "[weak]" not in text and "[moderate]" not in text and "[strong]" not in text
    if env["result"]["inferred"]:
        assert "Confidence in this inference:" in text


def test_fmt_pp2_never_rounds_small_gap_to_zero():
    """Percentage-point gaps must show 2 decimals, e.g. '-0.02 pp' -- 1 decimal
    would round a small but real gap down to a misleading '-0.0 pp'."""
    assert _fmt_pp2(-0.02) == "-0.02 pp"
    assert _fmt_pp2(-0.004) == "-0.00 pp"  # still non-misleading: shows as near-zero, not negative-looking "-0.0"
    assert _fmt_pp2(None) == "unknown"
    assert "-0.0 pp" not in _fmt_pp2(-0.02)


def test_narrate_get_unmet_demand_breakdown_unknown_airport_is_error(fixture_conn):
    env = call_tool("get_unmet_demand_breakdown", {"code": "ZZZ"}, conn=fixture_conn)
    assert "error" in env
    text = narrate("get_unmet_demand_breakdown", env)
    assert "could not answer" in text.lower()
    assert text.strip().endswith(CLOSING_LINE)


# ---------------------------------------------------------------------------
# get_forward_outlook
# ---------------------------------------------------------------------------

_OUTLOOK_FIXED_CAVEAT_SUBSTRINGS = (
    "forecast published by the FAA",
    "unconstrained",
    "NOT part of the composite score",
    "FAA fiscal years run Oct-Sep",
    "enplanements_per_runway is only a rough proxy",
)


def test_narrate_get_forward_outlook_single_airport(fixture_conn):
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
    env = call_tool("get_forward_outlook", {"airports": ["BOS"]}, conn=fixture_conn)
    text = narrate("get_forward_outlook", env)
    # CAGR is displayed as percent (3.7%, 3.4%) even though it's stored as a ratio (0.0371, 0.0341).
    # We check the display format but skip the general number-traceability test.
    assert text.strip().endswith("Answered by the built-in rules interpreter (no AI model).")
    assert "data window" in text.lower() or "window" in text.lower()
    assert any(word in text for word in {"low", "medium", "high"})
    assert "BOS" in text
    assert "FY2024" in text
    assert "3.7%" in text and "3.4%" in text  # CAGR shown as percent with 1 decimal
    assert "250" in text  # enplanements_per_runway as whole number
    assert "not a forecast produced by this agent" in text.lower() or "not a forecast this agent produces" in text.lower() or "FAA's own forecast" in text
    for substr in _OUTLOOK_FIXED_CAVEAT_SUBSTRINGS:
        assert substr in text, substr


def test_narrate_get_forward_outlook_multi_airport_table(fixture_conn):
    db.upsert_airport_outlook(
        fixture_conn,
        [
            {
                "iata_code": "BOS", "faa_lid": "BOS", "taf_base_fy": 2024,
                "enplanements_base": 1000.0, "enplanements_plus5": 1200.0, "enplanements_plus10": 1400.0,
                "cagr_5y": 0.0371, "cagr_10y": 0.0341, "qualifying_runways": 4,
                "enplanements_per_runway": 250.0, "match_note": None,
            },
        ],
        "fixture TAF", "2026-01-01T00:00:00Z",
    )
    env = call_tool("get_forward_outlook", {"airports": ["BOS", "TST"]}, conn=fixture_conn)
    text = narrate("get_forward_outlook", env)
    # CAGR is displayed as percent (3.7%, 3.4%) even though it's stored as a ratio (0.0371, 0.0341).
    # We check the display format but skip the general number-traceability test.
    assert text.strip().endswith("Answered by the built-in rules interpreter (no AI model).")
    assert "data window" in text.lower() or "window" in text.lower()
    assert any(word in text for word in {"low", "medium", "high"})
    assert "| Airport |" in text
    assert "BOS" in text and "TST" in text
    assert "3.7%" in text and "3.4%" in text  # CAGR shown as percent with 1 decimal
    assert "250" in text  # enplanements_per_runway as whole number
    for substr in _OUTLOOK_FIXED_CAVEAT_SUBSTRINGS:
        assert substr in text, substr


def test_narrate_get_forward_outlook_unmatched_airport_null_fields(fixture_conn):
    env = call_tool("get_forward_outlook", {"airports": ["TST"]}, conn=fixture_conn)
    text = narrate("get_forward_outlook", env)
    assert_common_shape(text, env)
    assert "unmatched" in text.lower() or "no airport_outlook row cached" in text.lower()
    for substr in _OUTLOOK_FIXED_CAVEAT_SUBSTRINGS:
        assert substr in text, substr


def test_narrate_get_forward_outlook_unknown_airport_is_error(fixture_conn):
    env = call_tool("get_forward_outlook", {"airports": ["ZZZ"]}, conn=fixture_conn)
    assert "error" in env
    text = narrate("get_forward_outlook", env)
    assert "could not answer" in text.lower()
    assert text.strip().endswith(CLOSING_LINE)


# ---------------------------------------------------------------------------
# describe_data_sources
# ---------------------------------------------------------------------------

def test_narrate_describe_data_sources(fixture_conn):
    env = call_tool("describe_data_sources", {}, conn=fixture_conn)
    text = narrate("describe_data_sources", env)
    assert text.strip().endswith(CLOSING_LINE)
    assert any(word in text for word in _CONFIDENCE_WORDS)
    assert_numbers_trace_to_envelope(text, env)
    for entry in env["result"]:
        assert entry["name"] in text


# ---------------------------------------------------------------------------
# dispatcher
# ---------------------------------------------------------------------------

def test_narrate_dispatch_unknown_tool_name():
    text = narrate("not_a_real_tool", {"result": None, "method": "", "caveats": [], "source": "", "confidence": "low"})
    assert "could not answer" in text.lower()
    assert text.strip().endswith(CLOSING_LINE)


def test_every_registry_tool_has_a_narrator():
    """
    Introspects the dispatcher's own tool-name set (_NARRATORS) against the
    live registry (get_tool_specs()) -- must fail if a new tool is ever added
    to src/tools/registry.py without a matching narrate_<tool> entry.
    """
    registry_tool_names = {spec["name"] for spec in get_tool_specs()}
    narrator_tool_names = set(_NARRATORS)
    missing = registry_tool_names - narrator_tool_names
    assert not missing, f"registry tool(s) with no narrator: {sorted(missing)}"


@pytest.mark.parametrize(
    "tool_name,args",
    [
        ("rank_airports", {"scope": {"tier": "large"}}),
        ("rank_airports_by_traffic", {"scope": {"tier": "large"}}),
        ("score_airport", {"code": "TST"}),
        ("compare_airports", {"codes": ["BOS", "TST"]}),
        ("sensitivity", {"scope": {"tier": "large"}}),
        ("list_region_airports", {"region_or_states": {"region": "new_england"}}),
        ("get_airport_traffic", {"code": "TST"}),
        ("compare_congestion", {"codes": ["TST"]}),
        ("get_long_haul_share", {"code": "TST"}),
        ("get_buildability", {"code": "BOS"}),
        ("get_unmet_demand_breakdown", {"code": "TST"}),
        ("get_forward_outlook", {"airports": ["TST"]}),
        ("describe_data_sources", {}),
    ],
)
def test_all_tools_produce_closing_line(fixture_conn, tool_name, args):
    env = call_tool(tool_name, args, conn=fixture_conn)
    text = narrate(tool_name, env)
    assert text.strip().endswith(CLOSING_LINE)
    assert "English" not in text  # sanity: no stray debug text


def test_score_table_dedups_flag_identical_on_every_row():
    """
    3 rows: all three carry the same 'buildability: no entry on file' flag,
    and one row additionally carries 'gap driven by seat cuts'. The identical
    flag must be pulled out once (common_flag_note); each row's Flag cell
    must keep only what differs from the rest.
    """
    common_buildability = {"has_constraints": None}
    entries = [
        {
            "rank": 1, "airport": "AAA", "composite_score": 0.5, "confidence": "medium",
            "status": "scored", "gap_driven_by_seat_cuts": True, "buildability": common_buildability,
        },
        {
            "rank": 2, "airport": "BBB", "composite_score": 0.3, "confidence": "medium",
            "status": "scored", "gap_driven_by_seat_cuts": False, "buildability": common_buildability,
        },
        {
            "rank": 3, "airport": "CCC", "composite_score": 0.1, "confidence": "medium",
            "status": "scored", "gap_driven_by_seat_cuts": False, "buildability": common_buildability,
        },
    ]
    table, common_flag_note = _score_table(entries)

    assert common_flag_note == "buildability: no entry on file -- unknown constraints"

    lines = table.splitlines()
    row_aaa = next(line for line in lines if "AAA" in line)
    row_bbb = next(line for line in lines if "BBB" in line)
    row_ccc = next(line for line in lines if "CCC" in line)

    # the common flag must not be repeated in any row's Flag cell
    assert "buildability" not in row_aaa
    assert "buildability" not in row_bbb
    assert "buildability" not in row_ccc

    # the differing flag stays on the one row that has it, and only that row
    assert "gap driven by seat cuts" in row_aaa
    assert "gap driven by seat cuts" not in row_bbb
    assert "gap driven by seat cuts" not in row_ccc

    # rows with no remaining flag component show "-"
    assert row_bbb.rstrip().endswith("| - |")
    assert row_ccc.rstrip().endswith("| - |")


def test_score_table_no_dedup_when_flags_differ_across_all_rows():
    """If no flag component is shared by every row, nothing is pulled out and
    every row keeps its own (possibly empty) flag cell unchanged."""
    entries = [
        {
            "rank": 1, "airport": "AAA", "composite_score": 0.5, "confidence": "medium",
            "status": "scored", "gap_driven_by_seat_cuts": True, "buildability": {"has_constraints": None},
        },
        {
            "rank": 2, "airport": "BBB", "composite_score": 0.3, "confidence": "medium",
            "status": "scored", "gap_driven_by_seat_cuts": False, "buildability": {"has_constraints": True, "note": "slot controlled"},
        },
    ]
    table, common_flag_note = _score_table(entries)
    assert common_flag_note is None
    lines = table.splitlines()
    row_aaa = next(line for line in lines if "AAA" in line)
    row_bbb = next(line for line in lines if "BBB" in line)
    assert "gap driven by seat cuts" in row_aaa
    assert "buildability constraints on file" in row_bbb
