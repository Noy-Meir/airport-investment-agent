"""
Pure display/formatting logic for the Streamlit chat UI. No streamlit
import here so this module stays unit-testable without a running app.
"""

import json
import re

from src.agent.pricing import COST_NOTE, estimate_cost

CAVEATS_CAP = 12
_AS_OF_PATTERN = re.compile(r"(?:ending|as_of)\s+[\w-]+", re.IGNORECASE)

SPEECH_TEXT_CAP = 1200

_TABLE_ROW_PATTERN = re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE)
_TABLE_SEPARATOR_PATTERN = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$")
_MD_LINK_PATTERN = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
_MD_HEADER_PATTERN = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)
_MD_BOLD_ITALIC_PATTERN = re.compile(r"(\*{1,3}|_{1,3})(.+?)\1")
_MD_BULLET_PATTERN = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
_MD_NUMBERED_PATTERN = re.compile(r"^\s*\d+\.\s+", re.MULTILINE)
_MD_CODE_FENCE_PATTERN = re.compile(r"```.*?```", re.DOTALL)
_MD_INLINE_CODE_PATTERN = re.compile(r"`([^`]*)`")
_SENTENCE_END_PATTERN = re.compile(r"[.!?](?:\s|$)")

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


def _collect_as_of(value, into, key=None):
    """
    Recursively walk a (possibly nested) tool result, collecting every
    value found under a key named "as_of" or ending in "_as_of" (e.g. the
    per-airport entries in compare_congestion, or current_as_of/prior_as_of
    in growth signals), plus any "ending <window>" labels embedded in
    strings. Dedupes, preserves first-seen order.
    """
    if isinstance(value, dict):
        for k, v in value.items():
            is_as_of_key = k == "as_of" or (isinstance(k, str) and k.endswith("_as_of"))
            if is_as_of_key and isinstance(v, str) and v not in into:
                into.append(v)
            _collect_as_of(v, into, key=k)
    elif isinstance(value, list):
        for item in value:
            _collect_as_of(item, into, key=key)
    elif isinstance(value, str):
        _find_as_of(value, into)


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

        _collect_as_of(result, as_of_windows)
        for c in entry_caveats:
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


def _drop_tables(text):
    lines = text.split("\n")
    kept = [
        line for line in lines
        if not (_TABLE_ROW_PATTERN.match(line) or _TABLE_SEPARATOR_PATTERN.match(line))
    ]
    return "\n".join(kept)


def _truncate_at_sentence(text, limit):
    if len(text) <= limit:
        return text
    window = text[:limit]
    last_end = None
    for match in _SENTENCE_END_PATTERN.finditer(window):
        last_end = match.end()
    if last_end:
        return window[:last_end].rstrip()
    return window.rstrip()


def speech_text(markdown):
    """
    Convert an assistant markdown answer into plain text suitable for
    speechSynthesis: markdown syntax stripped (links keep their visible
    text), tables dropped entirely (unintelligible read aloud), whitespace
    collapsed, truncated to about SPEECH_TEXT_CAP characters at a sentence
    boundary.
    """
    if not markdown:
        return ""

    text = _drop_tables(markdown)
    text = _MD_CODE_FENCE_PATTERN.sub(" ", text)
    text = _MD_INLINE_CODE_PATTERN.sub(r"\1", text)
    text = _MD_LINK_PATTERN.sub(r"\1", text)
    text = _MD_HEADER_PATTERN.sub("", text)
    text = _MD_BOLD_ITALIC_PATTERN.sub(r"\2", text)
    text = _MD_BULLET_PATTERN.sub("", text)
    text = _MD_NUMBERED_PATTERN.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()

    return _truncate_at_sentence(text, SPEECH_TEXT_CAP)


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
