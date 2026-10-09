"""
The single entry point for the UI and the CLI: respond(session, message) ->
{answer, trace, usage, mode, notice}.

A `session` holds two independent pieces of state that only their own path
ever updates:
- `agent_history`: the Anthropic Messages API history, consumed/produced by
  src.agent.agent.run_turn.
- `rules_state`: the src.agent.rules_router.State, consumed/produced by
  src.agent.rules_router.answer.

Provider resolution (LLM_PROVIDER, or `provider_override` for this call
only) decides which path runs:
- "rules": always src.agent.rules_router.answer -- no model call, ever.
- "anthropic", or "auto" with a usable key+model: src.agent.agent.run_turn.
  If that call comes back with an error (any category), this module
  transparently re-answers the same message with the rules interpreter and
  prepends a plain-language notice explaining the fallback.
- "anthropic" without a key (whether from the environment or an explicit
  override): answers with rules and a notice that no API key is configured,
  without ever constructing a client.

Never raises, never leaks raw error text -- only the sanitized category
produced by src.agent.agent._classify_error.
"""

from src.agent.config import load_config
from src.agent.rules_router import State

_ERROR_CATEGORY_LABELS = {
    "auth": "an authentication error",
    "bad_request": "a bad request error",
    "rate_limit": "rate limiting",
    "timeout": "a timeout",
    "connection": "a connection error",
    "other": "an unexpected error",
}

_ZERO_USAGE = {
    "input_tokens": 0,
    "output_tokens": 0,
    "cache_read_input_tokens": 0,
    "cache_creation_input_tokens": 0,
    "estimated_cost_usd": 0.0,
    "cost_note": "Rules router: no model call, no cost.",
}


class Session:
    """Holds the two independent conversation states respond() reads/writes."""

    def __init__(self):
        self.agent_history = []
        self.rules_state = State()


def _rules_answer(message, session, conn, notice):
    from src.agent.rules_router import answer as rules_answer

    out = rules_answer(message, state=session.rules_state, conn=conn)
    session.rules_state = out["state"]
    text = out["answer"] if not notice else f"{notice}\n\n{out['answer']}"
    return {
        "answer": text,
        "trace": out["trace"],
        "usage": out["usage"],
        "mode": "rules",
        "notice": notice,
    }


def _no_api_key_notice():
    return "The AI model isn't available (no API key is configured), so this answer comes from the built-in rules interpreter."


def _fallback_notice(error):
    category = error.get("type", "other")
    label = _ERROR_CATEGORY_LABELS.get(category, _ERROR_CATEGORY_LABELS["other"])
    return f"The AI model was unavailable ({label}), so this answer comes from the built-in rules interpreter."


def respond(session, message, conn=None, provider_override=None):
    """
    Runs one turn through whichever provider is in effect for this call and
    returns {answer, trace, usage, mode, notice}. `mode` is "llm" or
    "rules"; `notice` is a plain-language fallback explanation, or None.
    """
    from src.agent.config import VALID_PROVIDERS

    config = load_config()
    provider = config["llm_provider"]
    has_api_key = config["has_api_key"]

    if provider_override is not None:
        if provider_override not in VALID_PROVIDERS:
            raise ValueError(f"Invalid provider_override '{provider_override}'. Must be one of: {VALID_PROVIDERS}.")
        if provider_override == "auto":
            provider = "anthropic" if (has_api_key and config["model_name"]) else "rules"
        else:
            provider = provider_override

    if provider == "rules":
        return _rules_answer(message, session, conn, notice=None)

    # provider == "anthropic" from here on.
    if not has_api_key:
        return _rules_answer(message, session, conn, notice=_no_api_key_notice())

    from src.agent.agent import run_turn

    outcome = run_turn(session.agent_history, message, conn=conn)
    if outcome.get("error"):
        return _rules_answer(message, session, conn, notice=_fallback_notice(outcome["error"]))

    session.agent_history = outcome["history"]
    return {
        "answer": outcome["answer"],
        "trace": outcome["trace"],
        "usage": outcome["usage"],
        "mode": "llm",
        "notice": None,
    }
