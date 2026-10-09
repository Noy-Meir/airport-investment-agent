"""
Pure display/formatting logic for the Streamlit chat UI. No streamlit
import here so this module stays unit-testable without a running app.
"""

import json
import os
import re

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

SAMPLE_QUESTIONS = [
    "Which New England airports look like the best expansion candidates?",
    "How does congestion at LAX compare to Santa Ana (SNA)?",
    "What percent of flights out of Anchorage are long-haul?",
    "What's the unmet demand at SFO, and what's driving it?",
]

# Starter cards for the empty-state welcome grid. The first four mirror
# SAMPLE_QUESTIONS (the assignment's four questions); the fifth is a smaller
# bonus prompt. Kept separate from SAMPLE_QUESTIONS so existing callers/tests
# of that list are unaffected.
STARTER_CARDS = [
    {
        "icon": "\U0001F4C8",
        "title": "New England expansion",
        "description": "Which New England airports look like the best expansion candidates?",
        "question": SAMPLE_QUESTIONS[0],
    },
    {
        "icon": "✈️",
        "title": "LAX vs. Santa Ana congestion",
        "description": "How does congestion at LAX compare to Santa Ana (SNA)?",
        "question": SAMPLE_QUESTIONS[1],
    },
    {
        "icon": "\U0001F9ED",
        "title": "Anchorage long-haul share",
        "description": "What percent of flights out of Anchorage are long-haul?",
        "question": SAMPLE_QUESTIONS[2],
    },
    {
        "icon": "\U0001F50D",
        "title": "SFO unmet demand",
        "description": "What's the unmet demand at SFO, and what's driving it?",
        "question": SAMPLE_QUESTIONS[3],
    },
]

EXTRA_STARTER_QUESTION = "What does the FAA forecast for AUS?"

EXTRA_STARTER_CARD = {
    "icon": "\U0001F4CA",
    "title": "FAA forecast for AUS",
    "description": EXTRA_STARTER_QUESTION,
    "question": EXTRA_STARTER_QUESTION,
}

ALL_STARTER_QUESTIONS = SAMPLE_QUESTIONS + [EXTRA_STARTER_QUESTION]

_PILL_CONFIDENCE_CLASS = {
    "confidence: high": "pill-confidence-high",
    "confidence: medium": "pill-confidence-medium",
    "confidence: low": "pill-confidence-low",
}

_ELAPSED_SEGMENT_PATTERN = re.compile(r"^\d+(\.\d+)? s$")


def badge_line_to_pills(badge_line):
    """
    Splits a badge line (as produced by answer_metadata_badges) into
    {"text", "css_class"} segments for colored-pill rendering. Pure string
    logic -- no HTML is built here, so callers remain responsible for
    escaping before inserting into markup.
    """
    if not badge_line:
        return []

    pills = []
    for segment in badge_line.split(" · "):
        segment = segment.strip()
        if not segment:
            continue
        if segment == "rules-based":
            css_class = "pill-mode-rules"
        elif segment == "AI model":
            css_class = "pill-mode-ai"
        elif segment in _PILL_CONFIDENCE_CLASS:
            css_class = _PILL_CONFIDENCE_CLASS[segment]
        elif _ELAPSED_SEGMENT_PATTERN.match(segment):
            css_class = "pill-time"
        else:
            css_class = "pill-default"
        pills.append({"text": segment, "css_class": css_class})
    return pills


def data_source_chips():
    """
    Returns the data_sources_caption() content as a list of individual
    source strings (for rendering as separate chips), e.g.
    ["BTS T-100 (monthly, through 2026-04)", "FAA Terminal Area Forecast (actuals through 2024)", "OurAirports"].
    Returns an empty list if the manifest cannot be read.
    """
    caption = data_sources_caption()
    if not caption:
        return []
    prefix = "Data: "
    body = caption[len(prefix):] if caption.startswith(prefix) else caption
    return [part.strip() for part in body.split(" · ") if part.strip()]


def chip_css_class(chip_text):
    """
    Classifies a data_source_chips() entry into a CSS tint class by which
    publisher it names, so each source pill gets a distinct (but static,
    pre-defined) color.
    """
    if "FAA" in chip_text:
        return "chip-faa"
    if "OurAirports" in chip_text:
        return "chip-ourairports"
    if "BTS" in chip_text:
        return "chip-bts"
    return "chip-default"

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


def _format_vintage_for_caption(name, vintage, is_faa=False):
    """
    Formats a vintage string for the data sources caption.
    If vintage contains a range "A..B", renders "<name> (<text before the date>, through B)".
    If no range, renders "<name> (<vintage>)".
    E.g. "monthly, 2023-01..2026-04" -> "BTS T-100 (monthly, through 2026-04)"
    """
    if is_faa:
        return "FAA Terminal Area Forecast (actuals through 2024)"

    if not vintage:
        return name

    # Look for range pattern "A..B"
    if ".." in vintage:
        parts = vintage.split("..")
        if len(parts) == 2:
            # For "monthly, 2023-01", take everything up to the last comma
            before_range = parts[0]
            if "," in before_range:
                # Keep only the part before the last comma (e.g. "monthly" from "monthly, 2023-01")
                before = before_range.rsplit(",", 1)[0].strip()
            else:
                before = before_range.strip()
            after = parts[1].strip()
            return f"{name} ({before}, through {after})"

    return f"{name} ({vintage})"


def data_sources_caption():
    """
    Builds a caption line from data/sources_manifest.json listing data sources
    and vintages. Returns the caption string, or an empty string if the manifest
    cannot be read.
    """
    try:
        from src.cache.config import REPO_ROOT
        manifest_path = os.path.join(REPO_ROOT, "data", "sources_manifest.json")
        with open(manifest_path) as f:
            sources = json.load(f)
    except Exception:
        return ""

    # Map source IDs to their display format
    parts = []
    source_ids_order = ["bts_t100_airport_month", "bts_otp", "faa_taf_2025", "ourairports"]
    seen = set()

    for source_id in source_ids_order:
        for source in sources:
            if source.get("id") == source_id and source_id not in seen:
                seen.add(source_id)
                vintage = source.get('vintage', '')

                if source_id == "bts_t100_airport_month":
                    parts.append(_format_vintage_for_caption("BTS T-100", vintage))
                elif source_id == "bts_otp":
                    parts.append(_format_vintage_for_caption("BTS OTP", vintage))
                elif source_id == "faa_taf_2025":
                    parts.append(_format_vintage_for_caption("FAA TAF", vintage, is_faa=True))
                elif source_id == "ourairports":
                    parts.append("OurAirports")

    return "Data: " + " · ".join(parts) if parts else ""


def answer_metadata_badges(mode, elapsed_seconds, trace):
    """
    Builds a caption line with badges for answer mode, elapsed time, and
    confidence (if available in trace).

    `mode`: "rules" or "llm" (or the AI model label if from outcome["mode"])
    `elapsed_seconds`: numeric seconds elapsed for this turn
    `trace`: list of tool call results, or empty list if no tools called

    Returns the formatted badge line string (e.g. "rules-based · 2.3 s · confidence: high").
    """
    badges = []

    # Mode badge
    if mode == "rules":
        badges.append("rules-based")
    elif mode == "llm":
        badges.append("AI model")
    else:
        badges.append(mode)

    # Elapsed time badge
    badges.append(f"{elapsed_seconds:.1f} s")

    # Confidence badge (only if available from tool envelopes)
    if trace:
        confidences = []
        for call in trace:
            result = call.get("result", {})
            if isinstance(result, dict) and "confidence" in result:
                conf = result["confidence"]
                if conf:
                    confidences.append(conf)
        if confidences:
            # Return the lowest confidence
            min_conf = min(confidences, key=lambda x: {"low": 0, "medium": 1, "high": 2}.get(x, 2))
            badges.append(f"confidence: {min_conf}")

    return " · ".join(badges)


def render_assistant_message_with_badge(message_dict):
    """
    Helper to render an assistant message that may have a stored badge line.
    `message_dict` is a dict with "role", "content", and optionally "badge_line".
    Returns a dict with "content" and "badge_line" (or badge_line is None/missing).
    This is testable pure logic; app.py wraps it with st.caption/st.markdown.
    """
    return {
        "content": message_dict.get("content", ""),
        "badge_line": message_dict.get("badge_line"),
    }
