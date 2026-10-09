"""
Pure display/formatting logic for the Streamlit chat UI. No streamlit
import here so this module stays unit-testable without a running app.
"""

import re

from src.agent.pricing import COST_NOTE, estimate_cost

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
