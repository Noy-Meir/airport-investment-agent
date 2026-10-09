"""
Pure display/formatting logic for the Streamlit chat UI. No streamlit
import here so this module stays unit-testable without a running app.
"""

import json

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
