"""
Offline evaluation of the deterministic rules router (src.agent.rules_router)
against the shipped data/cache.db. No Anthropic API calls -- rules path
only, zero cost, no network access.

Runs two suites, both keyed on a `rules_expectation` field (one of
`tool:<name>`, `refuse`, `clarify`, `restate`, `help_known_limitation`, or
`covered_by:<conv_case_id>` for the three bare follow-up cases that need a
hand-built State -- see data/eval/*.json):
  1. data/eval/tool_selection_cases.json -- one question per case, checked
     independently. Cases with a `previous_turn` run that turn first in a
     fresh State.
  2. data/eval/conversation_cases.json -- a single ~15-question scripted
     conversation run through one shared rules_router.State, so later
     questions see the context earlier ones left behind.

For every executed case, checks:
  (a) the router's actual behavior (which tool it called, or which no-tool
      path -- refuse/clarify/restate/help -- it took) matches
      `rules_expectation`;
  (b) every number printed in the answer traces back to the tool envelope
      that produced it;
  (c) the answer states its data window and caveats;
  (d) no exception escaped.

A case whose expectation is `help_known_limitation` is reported in its own
category, not pass/fail: it passes verification as "a known limitation,
confirmed" when the router falls to the generic help message as expected,
and only fails if the router's behavior drifts from that (e.g. starts
calling a tool, which would be a correctness regression worth catching).

Prints a compact table to the console; writes full results (including every
answer) to a temp JSON file (path printed, not dumped to the console).

Usage:
    python scripts/run_eval.py
"""

import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.cache import db
from src.agent.rules_router import answer, plan

TOOL_CASES_PATH = Path(__file__).resolve().parents[1] / "data/eval/tool_selection_cases.json"
CONV_CASES_PATH = Path(__file__).resolve().parents[1] / "data/eval/conversation_cases.json"


# ---------------------------------------------------------------------------
# Number-traceability check (b).
#
# Three fixes on top of a naive "every number in the text must appear
# somewhere in the envelope" check:
#   (a) a hyphenated range like "rank 2-4" must tokenize as the two numbers
#       2 and 4, not as a single misread "-4";
#   (b) the display-only position column (the first "Rank"/"#" column of a
#       markdown table) is a row index, not a value sourced from the
#       envelope -- compare_airports in particular numbers rows 1, 2, ... by
#       list position, which has no envelope field behind it at all -- so it
#       is excluded from the check rather than treated as an invented
#       number;
#   (c) a percent-displayed value X% is accepted when the envelope carries
#       the equivalent ratio X/100 (e.g. get_forward_outlook's CAGR is
#       stored as a ratio like 0.0234 but narrated as "2.3%") -- same
#       ratio<->percent display convention tests/test_narrators.py already
#       allows. This does not loosen the check for any number with no
#       matching source value at all (ratio or otherwise) -- those still
#       fail.
# ---------------------------------------------------------------------------

_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}(?:-\d{2})?")
_NUM_TOKEN_RE = re.compile(r"-?\d[\d,]*\.?\d*")
_RANGE_RE = re.compile(r"(?<!\d)(\d+)-(\d+)(?!\d)")
_TABLE_ROW_RE = re.compile(r"^\s*\|")


def strip_position_column(text):
    """Blanks the first cell of every markdown table row when that cell is a
    bare integer (the "Rank"/"#" display-position column)."""
    out_lines = []
    for line in text.split("\n"):
        if _TABLE_ROW_RE.match(line):
            parts = line.split("|")
            if len(parts) > 2 and re.fullmatch(r"\d+", parts[1].strip()):
                parts[1] = " "
                line = "|".join(parts)
        out_lines.append(line)
    return "\n".join(out_lines)


def split_ranges(text):
    """Splits a hyphenated numeric range ("2-4") into two separate numbers
    ("2 4") so the tokenizer below doesn't misread it as "-4"."""
    return _RANGE_RE.sub(lambda m: f"{m.group(1)} {m.group(2)}", text)


def numeric_tokens(text):
    text = _ISO_DATE_RE.sub(" ", text)  # dates are labels, not measured numbers
    text = split_ranges(text)
    out = []
    for raw in _NUM_TOKEN_RE.findall(text):
        cleaned = raw.replace(",", "")
        if cleaned not in ("", "-", "."):
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
                for raw in _NUM_TOKEN_RE.findall(_ISO_DATE_RE.sub(" ", k)):
                    cleaned = raw.replace(",", "")
                    if cleaned not in ("", "-", "."):
                        out.append(float(cleaned))
            _collect_numeric_leaves(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect_numeric_leaves(v, out)
    elif isinstance(obj, str):
        for raw in _NUM_TOKEN_RE.findall(_ISO_DATE_RE.sub(" ", obj)):
            cleaned = raw.replace(",", "")
            if cleaned not in ("", "-", "."):
                out.append(float(cleaned))


def numbers_not_traced(narration, envelope):
    """Returns the list of numeric tokens printed in `narration` (after the
    position-column and range fixes above) that do not trace back to any
    numeric leaf in `envelope`, within display-rounding tolerance. Empty
    list means every number traces."""
    narration = strip_position_column(narration)
    leaves = []
    _collect_numeric_leaves(envelope, leaves)
    bad = []
    for token in numeric_tokens(narration):
        tval = float(token)
        decimals = len(token.split(".", 1)[1]) if "." in token else 0
        tol = (0.5 * (10 ** -decimals)) + 1e-9
        ok = any(
            abs(round(leaf, decimals) - tval) < 1e-6 or abs(leaf - tval) < tol
            or abs(round(leaf * 100, decimals) - tval) < 1e-6 or abs(leaf * 100 - tval) < tol
            for leaf in leaves
        )
        if not ok:
            bad.append(token)
    return bad


def _numbers_trace_ok(answer_text, trace):
    """(b): every number printed traces back to the one tool envelope used
    this turn (the rules router calls at most one tool per turn). No tool
    call -> trivially fine."""
    if not trace:
        return True, None
    bad = numbers_not_traced(answer_text, trace[0]["result"])
    if bad:
        return False, f"number(s) {bad[:3]!r} do not trace back to the envelope"
    return True, None


# ---------------------------------------------------------------------------
# Data-window / caveats check (c).
# ---------------------------------------------------------------------------

_NO_WINDOW_TOOLS = {"describe_data_sources"}


def _window_and_caveats_ok(answer_text, tool, tool_result):
    """(c): the answer states its data window and caveats. Three tool-level
    exceptions that are not eval failures:
      - describe_data_sources has no TTM/calendar window concept at all;
      - an error-path answer (e.g. unknown airport code) is a short message
        with neither, by narrators._narrate_error's design;
      - a no-result answer (nothing in scope) states caveats as "reasons"
        but has no window either, by narrators._no_result_message's design.
    """
    low = answer_text.lower()
    is_error = isinstance(tool_result, dict) and "error" in tool_result
    is_no_result = isinstance(tool_result, dict) and tool_result.get("result") is None
    if is_error:
        return True
    if is_no_result:
        return "caveat" in low
    if tool in _NO_WINDOW_TOOLS:
        return "caveat" in low
    return ("window" in low) and ("caveat" in low)


# ---------------------------------------------------------------------------
# Expectation parsing and actual-behavior classification (a).
# ---------------------------------------------------------------------------

def parse_expectation(expectation):
    """Returns (kind, detail). kind is one of "tool", "refuse", "clarify",
    "restate", "help", "covered_by". detail is the tool name for "tool", the
    referenced case id for "covered_by", else None."""
    if expectation.startswith("tool:"):
        return "tool", expectation.split(":", 1)[1]
    if expectation.startswith("covered_by:"):
        return "covered_by", expectation.split(":", 1)[1]
    if expectation == "help_known_limitation":
        return "help", None
    return expectation, None  # refuse / clarify / restate


def classify_actual(question, state):
    """Classifies what the rules router actually did, by introspecting the
    same Plan the router computes internally (src.agent.rules_router.plan,
    called the same way src.agent.rules_router._answer_inner calls it -- no
    conn, so this mirrors the real path exactly without re-implementing or
    altering any router logic). Returns (kind, detail) in the same shape as
    parse_expectation."""
    p = plan(question, state=state)
    if p.tool is not None:
        return "tool", p.tool
    if p.intent.startswith("refusal_"):
        return "refuse", None
    if p.intent == "clarify_scope":
        return "clarify", None
    if p.intent == "restate":
        return "restate", None
    return "help", None  # help, reweight_unsupported, or an unmatched question


def behavior_matches(expected, actual):
    exp_kind, exp_detail = expected
    act_kind, act_detail = actual
    if exp_kind != act_kind:
        return False
    if exp_kind == "tool":
        return exp_detail == act_detail
    return True


# ---------------------------------------------------------------------------
# Case categorization, for the result table.
# ---------------------------------------------------------------------------

def categorize(case_id, exp_kind):
    if exp_kind == "help":
        return "known_limitation"
    if exp_kind == "covered_by":
        return "follow_up"
    if "followup" in case_id.lower() or exp_kind == "restate":
        return "follow_up"
    if exp_kind in ("refuse", "clarify"):
        return "refusal_clarify"
    return "tool_selection"


# ---------------------------------------------------------------------------
# Execution.
# ---------------------------------------------------------------------------

def _run_one(question, state, conn):
    """Runs one turn, catching anything so a single bad case can't kill the
    whole eval run (check (d))."""
    try:
        return answer(question, state=state, conn=conn), None
    except Exception as e:  # noqa: BLE001 -- eval harness must never crash on one case
        return None, f"{type(e).__name__}: {e}"


def _tool_used(result):
    return result["trace"][0]["tool"] if result["trace"] else None


def _evaluate_case(case_id, question, expectation, state, conn):
    exp_kind, exp_detail = parse_expectation(expectation)
    category = categorize(case_id, exp_kind)

    if exp_kind == "covered_by":
        return {
            "id": case_id, "question": question, "status": "covered",
            "category": category,
            "reason": f"covered by conversation case {exp_detail!r}",
        }, state

    result, err = _run_one(question, state, conn)
    if err:
        return {
            "id": case_id, "question": question, "status": "fail", "category": category,
            "reason": f"exception: {err}",
        }, state

    new_state = result["state"]
    actual = classify_actual(question, state)
    behavior_ok = behavior_matches((exp_kind, exp_detail), actual)

    nums_ok, nums_reason = _numbers_trace_ok(result["answer"], result["trace"])
    tool_result = result["trace"][0]["result"] if result["trace"] else None
    checks_window = bool(result["trace"]) or exp_kind == "restate"
    window_ok = (
        _window_and_caveats_ok(result["answer"], actual[1], tool_result) if checks_window else True
    )

    if category == "known_limitation":
        status = "known_limitation" if behavior_ok else "fail"
    else:
        status = "pass" if (behavior_ok and nums_ok and window_ok) else "fail"

    reason = "ok" if status in ("pass", "known_limitation") else "; ".join(
        r for r in [
            None if behavior_ok else f"expected {expectation!r}, got {actual[0]}:{actual[1]}",
            None if nums_ok else f"number traceability: {nums_reason}",
            None if window_ok else "missing data window or caveats",
        ] if r
    )
    return {
        "id": case_id, "question": question, "status": status, "category": category,
        "reason": reason, "tool": actual[1] if actual[0] == "tool" else None,
        "answer": result["answer"],
    }, new_state


def evaluate_tool_selection_cases(cases, conn):
    """Runs data/eval/tool_selection_cases.json. Returns a list of per-case
    result dicts."""
    out = []
    for case in cases:
        case_id = case["id"]
        expectation = case["rules_expectation"]

        state = None
        if "previous_turn" in case:
            prev_result, err = _run_one(case["previous_turn"]["question"], None, conn)
            if err:
                out.append({
                    "id": case_id, "question": case["question"], "status": "fail",
                    "category": categorize(case_id, parse_expectation(expectation)[0]),
                    "reason": f"previous_turn raised: {err}",
                })
                continue
            state = prev_result["state"]

        case_result, _ = _evaluate_case(case_id, case["question"], expectation, state, conn)
        out.append(case_result)
    return out


def evaluate_conversation(cases, conn):
    """Runs data/eval/conversation_cases.json as one shared-State conversation."""
    out = []
    state = None
    for case in cases:
        case_result, state = _evaluate_case(
            case["id"], case["question"], case["rules_expectation"], state, conn
        )
        out.append(case_result)
    return out


# ---------------------------------------------------------------------------
# Reporting.
# ---------------------------------------------------------------------------

def _print_table(results, title):
    print(f"\n{title}")
    print("-" * len(title))
    for r in results:
        print(f"{r['id']:<45} {r['status']:<17} {r.get('reason', '')[:70]}")


def _category_counts(all_results):
    cats = {}
    for r in all_results:
        c = cats.setdefault(r["category"], {"pass": 0, "fail": 0, "known_limitation": 0, "covered": 0})
        c[r["status"]] = c.get(r["status"], 0) + 1
    return cats


def _traceability_counts(all_results, answer_lookup):
    """Aggregates check (b) alone across every case that reached a tool
    call, independent of the case's overall pass/fail status."""
    pass_n = fail_n = 0
    for r in all_results:
        if r["status"] == "covered" or r.get("tool") is None:
            continue
        reason = r.get("reason", "")
        if "number traceability" in reason:
            fail_n += 1
        else:
            pass_n += 1
    return pass_n, fail_n


def main():
    conn = db.connect()
    try:
        tool_cases = json.loads(TOOL_CASES_PATH.read_text())
        conv_cases = json.loads(CONV_CASES_PATH.read_text())

        tool_results = evaluate_tool_selection_cases(tool_cases, conn)
        conv_results = evaluate_conversation(conv_cases, conn)
    finally:
        conn.close()

    _print_table(tool_results, "Tool selection cases")
    _print_table(conv_results, "Conversation cases")

    all_results = tool_results + conv_results
    cats = _category_counts(all_results)
    trace_pass, trace_fail = _traceability_counts(all_results, None)

    print("\nBy category:")
    for cat in ("tool_selection", "refusal_clarify", "follow_up", "known_limitation"):
        c = cats.get(cat, {"pass": 0, "fail": 0, "known_limitation": 0, "covered": 0})
        print(
            f"  {cat:<18} pass={c['pass']:<3} fail={c['fail']:<3} "
            f"known_limitation={c['known_limitation']:<3} covered={c['covered']:<3}"
        )
    print(f"  {'traceability':<18} pass={trace_pass:<3} fail={trace_fail:<3}")

    total_pass = sum(c["pass"] for c in cats.values())
    total_fail = sum(c["fail"] for c in cats.values())
    total_known = sum(c["known_limitation"] for c in cats.values())
    total_covered = sum(c["covered"] for c in cats.values())
    print(
        f"\nTotals: {total_pass} pass / {total_fail} fail / {total_known} known_limitation / "
        f"{total_covered} covered (of {len(all_results)})"
    )

    out = {"tool_selection": tool_results, "conversation": conv_results}
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", prefix="run_eval_", delete=False
    ) as f:
        json.dump(out, f, indent=2)
        out_path = f.name
    print(f"\nFull results (including every answer): {out_path}")
    return tool_results, conv_results


if __name__ == "__main__":
    main()
