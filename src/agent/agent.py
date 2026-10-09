"""
The LLM agent loop: standard Anthropic tool-use loop over
src/tools/registry.py. The LLM only selects tools and narrates (CLAUDE.md);
this module never computes or asserts a number itself -- it just runs the
request/tool-execute/continue cycle and hands back whatever the model said,
plus a trace of every raw tool result for the (future) numeric guard to
verify answers against.
"""

import json
import time
from pathlib import Path

from src.agent.config import load_config
from src.agent.pricing import COST_NOTE, estimate_cost
from src.tools.registry import call_tool, get_tool_specs

MAX_TOOL_ITERATIONS = 8
RETRY_BACKOFF_SECONDS = 0.5

SYSTEM_PROMPT_PATH = Path(__file__).parent / "system_prompt.md"


def _attr(obj, name, default=None):
    """Works for pydantic SDK response objects, SimpleNamespace fakes, and plain dicts."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _system_blocks():
    text = SYSTEM_PROMPT_PATH.read_text()
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def _tools_with_cache_control():
    tools = [dict(t) for t in get_tool_specs()]
    if tools:
        tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}
    return tools


def _stub_old_tool_results(history):
    """
    Returns a copy of `history` for sending to the API, with tool_result
    bodies from turns before the most recent one replaced by a short stub.
    The most recent turn starts at the last plain-string user message
    (tool_result messages are role=user too, but their content is a list,
    never a bare string).
    """
    last_turn_start = 0
    for i, msg in enumerate(history):
        if msg["role"] == "user" and isinstance(msg["content"], str):
            last_turn_start = i

    stubbed = []
    for i, msg in enumerate(history):
        if i < last_turn_start and msg["role"] == "user" and isinstance(msg["content"], list):
            new_content = [
                {**block, "content": "[earlier tool result omitted]"}
                if isinstance(block, dict) and block.get("type") == "tool_result"
                else block
                for block in msg["content"]
            ]
            stubbed.append({**msg, "content": new_content})
        else:
            stubbed.append(msg)
    return stubbed


_TRANSIENT_ERROR_NAMES = ("RateLimitError", "APITimeoutError", "APIConnectionError", "APIStatusError")


def _is_transient(exc):
    return type(exc).__name__ in _TRANSIENT_ERROR_NAMES


def _call_with_retry(client, **kwargs):
    """One retry with backoff on rate-limit/timeout/connection errors; re-raises otherwise."""
    try:
        return client.messages.create(**kwargs)
    except Exception as e:
        if not _is_transient(e):
            raise
        time.sleep(RETRY_BACKOFF_SECONDS)
        return client.messages.create(**kwargs)


def _accumulate_usage(totals, usage):
    totals["input_tokens"] += _attr(usage, "input_tokens", 0) or 0
    totals["output_tokens"] += _attr(usage, "output_tokens", 0) or 0
    totals["cache_read_input_tokens"] += _attr(usage, "cache_read_input_tokens", 0) or 0
    totals["cache_creation_input_tokens"] += _attr(usage, "cache_creation_input_tokens", 0) or 0


def _finalize_usage(totals):
    return {
        **totals,
        "estimated_cost_usd": round(estimate_cost(totals), 6),
        "cost_note": COST_NOTE,
    }


def _extract_text(content_blocks):
    texts = [_attr(b, "text") for b in content_blocks if _attr(b, "type") == "text"]
    return "\n".join(t for t in texts if t)


def validate_answer(answer, trace):
    """
    TODO (step 4b): the numeric guard. Verify every number asserted in
    `answer` traces back to a raw value in `trace` (the tool call results
    for this turn); flag/strip unsourced figures instead of returning them.
    For now this is a no-op placeholder.
    """
    return True, []


def run_turn(history, user_message, client=None, conn=None):
    """
    Runs one user turn through the standard tool-use loop.

    `history`: prior messages in Anthropic Messages API format; not mutated,
    a new list is returned.
    `client`: an Anthropic-SDK-shaped client (`.messages.create(...)`);
    injectable so tests use a fake. Defaults to a real anthropic.Anthropic().
    `conn`: optional existing db connection passed through to call_tool.

    Returns {answer, history, trace, usage}.
    """
    config = load_config()
    if client is None:
        import anthropic

        client = anthropic.Anthropic(api_key=config["api_key"])

    history = list(history)
    history.append({"role": "user", "content": user_message})

    trace = []
    usage_totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    system = _system_blocks()
    tools = _tools_with_cache_control()

    round_count = 0
    while True:
        request_messages = _stub_old_tool_results(history)
        try:
            response = _call_with_retry(
                client, model=config["model_name"], max_tokens=4096,
                system=system, tools=tools, messages=request_messages,
            )
        except Exception as e:
            return {
                "answer": (
                    "I couldn't reach the model to answer this (a connection/rate-limit/"
                    "timeout error persisted after one retry). Please try again shortly."
                ),
                "history": history,
                "trace": trace,
                "usage": _finalize_usage(usage_totals),
                "error": {"type": type(e).__name__, "message": str(e)},
            }

        _accumulate_usage(usage_totals, _attr(response, "usage", {}))

        content_blocks = _attr(response, "content", [])
        history.append({"role": "assistant", "content": content_blocks})

        stop_reason = _attr(response, "stop_reason")
        if stop_reason != "tool_use":
            answer = _extract_text(content_blocks)
            validate_answer(answer, trace)
            return {"answer": answer, "history": history, "trace": trace, "usage": _finalize_usage(usage_totals)}

        round_count += 1
        tool_use_blocks = [b for b in content_blocks if _attr(b, "type") == "tool_use"]

        if round_count > MAX_TOOL_ITERATIONS:
            aborted_results = [
                {
                    "type": "tool_result",
                    "tool_use_id": _attr(b, "id"),
                    "content": json.dumps({"error": {"type": "IterationCapError", "message": "stopped: too many tool-call iterations"}}),
                    "is_error": True,
                }
                for b in tool_use_blocks
            ]
            history.append({"role": "user", "content": aborted_results})
            safe_answer = (
                "This question needed too many steps to answer reliably within the "
                f"iteration limit ({MAX_TOOL_ITERATIONS} tool-call rounds), so I'm stopping "
                "rather than guess. Please try breaking it into smaller questions."
            )
            history.append({"role": "assistant", "content": safe_answer})
            return {"answer": safe_answer, "history": history, "trace": trace, "usage": _finalize_usage(usage_totals)}

        tool_result_blocks = []
        for b in tool_use_blocks:
            name = _attr(b, "name")
            args = _attr(b, "input")
            t0 = time.perf_counter()
            result = call_tool(name, args, conn=conn)
            ms = (time.perf_counter() - t0) * 1000
            trace.append({"tool": name, "args": args, "result": result, "ms": ms})
            is_error = isinstance(result, dict) and "error" in result
            tool_result_blocks.append({
                "type": "tool_result",
                "tool_use_id": _attr(b, "id"),
                "content": json.dumps(result),
                "is_error": is_error,
            })
        history.append({"role": "user", "content": tool_result_blocks})
