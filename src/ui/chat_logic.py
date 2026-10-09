"""
Pure display/formatting logic for the Streamlit chat UI. No streamlit
import here so this module stays unit-testable without a running app.
"""

import json
import re

from src.agent.pricing import COST_NOTE, estimate_cost

CAVEATS_CAP = 12
_AS_OF_PATTERN = re.compile(r"(?:ending|as_of)\s+[\w-]+", re.IGNORECASE)

_USAGE_KEYS = (
    "input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens",
)

SAMPLE_QUESTIONS = [
    "Which New England airports look like the best expansion candidates?",
    "How does congestion at LAX compare to Santa Ana (SNA)?",
    "What percent of flights out of Anchorage are long-haul?",
    "What's the unmet demand at SFO, and what's driving it?",
]

_ERROR_MESSAGES = {
    "auth": "The API key was rejected. Check ANTHROPIC_API_KEY and try again.",
    "bad_request": "The model rejected this request as malformed. Try rephrasing your question.",
    "rate_limit": "Rate limit hit. Wait a moment and try again.",
    "timeout": "The request to the model timed out. Try again shortly.",
    "connection": "Couldn't connect to the model API. Check your network and try again.",
    "other": "Something went wrong talking to the model. Try again shortly.",
}


def format_trace(trace):
    """
    `trace`: list of {tool, args, result, ms} from run_turn.
    Returns [{tool, args_text, ms, result}] for display -- args_text is a
    compact JSON string, ms is rounded for readability.
    """
    formatted = []
    for entry in trace:
        formatted.append({
            "tool": entry["tool"],
            "args_text": json.dumps(entry["args"], sort_keys=True),
            "ms": round(entry["ms"], 1),
            "result": entry["result"],
        })
    return formatted


def _find_as_of(text, into):
    if not text:
        return
    for match in _AS_OF_PATTERN.findall(text):
        if match not in into:
            into.append(match)


def summarize_envelopes(trace):
    """
    Deterministic "assumptions and data" summary for one turn, built only
    from the uniform envelope fields (method, caveats, source, confidence)
    of each tool result in `trace` -- nothing here is written or inferred
    by the model. Tool results with an "error" key are skipped from the
    envelope summary and recorded in `failed_tools` instead.

    Returns {as_of_windows, sources, confidence_by_tool, methods_by_tool,
    caveats, caveats_truncated, failed_tools}. `as_of_windows`, `sources`,
    and `caveats` are deduped and order-preserving; `caveats` is capped to
    the `CAVEATS_CAP` most relevant (the first encountered, in trace order),
    with `caveats_truncated` set when more were dropped.
    """
    as_of_windows = []
    sources = []
    caveats = []
    confidence_by_tool = {}
    methods_by_tool = {}
    failed_tools = []

    for entry in trace:
        tool = entry.get("tool")
        result = entry.get("result")
        if not isinstance(result, dict) or "error" in result:
            failed_tools.append(tool)
            continue

        method = result.get("method")
        source = result.get("source")
        confidence = result.get("confidence")
        entry_caveats = result.get("caveats") or []

        if method:
            methods_by_tool[tool] = method
        if confidence:
            confidence_by_tool[tool] = confidence
        if source and source not in sources:
            sources.append(source)

        _find_as_of(method, as_of_windows)
        _find_as_of(source, as_of_windows)
        for c in entry_caveats:
            _find_as_of(c, as_of_windows)
            if c not in caveats:
                caveats.append(c)

    return {
        "as_of_windows": as_of_windows,
        "sources": sources,
        "confidence_by_tool": confidence_by_tool,
        "methods_by_tool": methods_by_tool,
        "caveats": caveats[:CAVEATS_CAP],
        "caveats_truncated": len(caveats) > CAVEATS_CAP,
        "failed_tools": failed_tools,
    }


def session_usage(turns):
    """
    Cumulative token usage and estimated cost across a session.

    `turns`: list of per-turn `usage` dicts as returned by
    `run_turn` (src/agent/agent.py) -- i.e. outcome["usage"] from each
    turn. Uses the same `estimate_cost` the agent uses, so the sidebar
    total and the per-turn figures are computed the same way.
    """
    totals = {key: 0 for key in _USAGE_KEYS}
    for usage in turns:
        if not usage:
            continue
        for key in _USAGE_KEYS:
            totals[key] += usage.get(key, 0) or 0

    return {
        **totals,
        "estimated_cost_usd": round(estimate_cost(totals), 6),
        "cost_note": COST_NOTE,
    }


def error_to_message(error):
    """
    `error`: the sanitized error dict from run_turn's top-level "error" key
    ({"type", "status_code", "message"}), or None.
    Returns a short, user-readable message -- never the raw message/traceback.
    """
    if not error:
        return _ERROR_MESSAGES["other"]
    category = error.get("type", "other")
    return _ERROR_MESSAGES.get(category, _ERROR_MESSAGES["other"])
