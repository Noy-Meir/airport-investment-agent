"""Shared envelope helpers (see CLAUDE.md: every tool/accessor returns {result, method, caveats, source, confidence})."""


def envelope(result, method, caveats, source, confidence):
    return {
        "result": result,
        "method": method,
        "caveats": caveats,
        "source": source,
        "confidence": confidence,
    }


def source_stamp(cursor_source, cursor_fetched_at):
    if cursor_fetched_at is None:
        return "no cached data"
    return f"{cursor_source}, cached {cursor_fetched_at}"
