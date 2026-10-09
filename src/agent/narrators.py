"""
Deterministic, no-LLM narration of tool-registry envelopes (src/tools/
registry.py) into plain markdown text.

CLAUDE.md / system_prompt.md: the LLM only narrates tool results, it never
computes or asserts a number itself. This module is the non-LLM path that
does the same job with pure string formatting -- every number and label in
the output text comes from the envelope's own result/method/caveats/source
fields. Nothing here calls an LLM, computes a new statistic, rounds
differently than the tool already rounded, or imputes a missing value.

One `narrate_<tool_name>(envelope)` function per tool in
src/tools/registry.py's TOOLS dict, plus a dispatcher `narrate(name, envelope)`.
`envelope` is whatever src.tools.registry.call_tool() returned: either the
uniform `{result, method, caveats, source, confidence}` envelope, or a
`{"error": {"type", "message"}}` dict (call_tool never raises).

Every narration -- success or error -- ends with exactly:
    "Answered by the built-in rules interpreter (no AI model)."
"""

import json
import re

CLOSING_LINE = "Answered by the built-in rules interpreter (no AI model)."

# Caveat substrings that must never be dropped by the cap-at-4 rule (task
# spec): gap_driven_by_seat_cuts, low OTP coverage, partial congestion
# window, unknown buildability, needs_verification. Matched case-insensitively
# against the caveat text itself.
_PRIORITY_CAVEAT_PATTERNS = (
    "seat_cut", "seat cuts", "driven by seat",
    "low_coverage", "low otp coverage", "coverage",
    "only ", "/12 month", "not a full calendar year",
    "no entry", "not researched", "unknown constraint", "buildability.json not found",
    "needs_verification", "needs verification",
)

_DATE_RE = re.compile(r"\d{4}-\d{2}(?:-\d{2})?")

# Sentence used anywhere a peer-relative z-score is shown, so a reader never
# mistakes an in-result ranking table for a comparison against the full
# national set of airports.
PEER_TIER_SENTENCE = (
    "Peer comparisons are against other airports in the same TTM hub-size "
    "tier, not the full national set."
)

# A Python list repr (e.g. "['congestion']" or "['MA', 'CT']") sometimes
# leaks into a caveat/scope string built upstream with an f-string over a
# list. This is a display-only fix: convert it to prose ("congestion" /
# "MA, CT") without touching the underlying caveat/scope data.
_LIST_REPR_RE = re.compile(r"\[(?:'[^']*'|\"[^\"]*\")(?:,\s*(?:'[^']*'|\"[^\"]*\"))*\]")


def _delistify(text):
    """Replace any Python-list-repr-looking substring in `text` with a comma-joined, unquoted phrase."""
    if not text:
        return text

    def _repl(m):
        items = re.findall(r"'([^']*)'|\"([^\"]*)\"", m.group(0))
        vals = [a or b for a, b in items]
        return ", ".join(vals)

    return _LIST_REPR_RE.sub(_repl, text)


# ---------------------------------------------------------------------------
# Scope-label helper -- maps internal scope strings (e.g. "region:new_england",
# "tier:large", "states:['MA', 'CT']") to a human-readable phrase. Pure string
# mapping of the scope value already in the envelope; invents no new scopes.
# ---------------------------------------------------------------------------

_TIER_LABELS = {
    "large": "large-hub airports",
    "medium": "medium-hub airports",
    "small": "small-hub airports",
    "micro": "micro-hub airports",
}


def _scope_label(scope):
    if scope is None:
        return "an unspecified scope"
    s = str(scope)
    if s == "all":
        return "all airports nationwide above the volume floor"
    if s.startswith("region:"):
        region = s[len("region:"):]
        return f"{region.replace('_', ' ').title()} region"
    if s.startswith("tier:"):
        tier = s[len("tier:"):]
        return _TIER_LABELS.get(tier, f"{tier}-hub airports")
    if s.startswith("states:"):
        rest = s[len("states:"):]
        quoted = re.findall(r"'([^']*)'|\"([^\"]*)\"", rest)
        if quoted:
            codes = [a or b for a, b in quoted]
        else:
            codes = [c.strip() for c in rest.strip("[]").split(",") if c.strip()]
        return "states: " + ", ".join(codes)
    return s


# ---------------------------------------------------------------------------
# z-score -> plain-English phrase. Display transform only -- the z-score
# itself is computed in src/scoring/normalize.py and never recomputed here.
# Cut points (documented so they're auditable):
#   z >=  1.5            -> "well above"
#   z >=  0.5            -> "above"
#   z  > -0.5            -> "near"
#   z  > -1.5            -> "below"
#   otherwise (z <= -1.5) -> "well below"
# ---------------------------------------------------------------------------

def _z_phrase(z, tier=None):
    if z is None:
        return None
    if z >= 1.5:
        word = "well above"
    elif z >= 0.5:
        word = "above"
    elif z > -0.5:
        word = "near"
    elif z > -1.5:
        word = "below"
    else:
        word = "well below"
    tier_s = tier or "peer"
    return f"{word} its {tier_s}-hub peers"


# ---------------------------------------------------------------------------
# Fixed-decimal display formatters -- display rounding only, on top of the
# tool's own already-computed value. Never changes which value is used, only
# how many decimals are shown.
# ---------------------------------------------------------------------------

def _fmt_fixed(x, decimals):
    if x is None:
        return "unknown"
    if isinstance(x, bool):
        return str(x)
    try:
        v = round(float(x), decimals)
    except (TypeError, ValueError):
        return str(x)
    return f"{v:,.{decimals}f}"


def _fmt_score(x):
    """Composite scores: 2 decimals."""
    return _fmt_fixed(x, 2)


def _fmt_pct1(x):
    """Percentages: 1 decimal, with trailing '%'."""
    if x is None:
        return "unknown"
    return f"{_fmt_fixed(x, 1)}%"


def _fmt_pp2(x):
    """Percentage-point gaps (e.g. demand_supply_gap): 2 decimals, e.g. '-0.02 pp' -- 1 decimal
    would round small but real gaps (like -0.02) down to a misleading '-0.0'."""
    if x is None:
        return "unknown"
    return f"{_fmt_fixed(x, 2)} pp"


def _fmt_ratio3(x):
    """Load factors (and other plain ratios): 3 decimals, shown as a ratio (not a percent)."""
    return _fmt_fixed(x, 3)


# ---------------------------------------------------------------------------
# Generic formatting helpers -- format only, never compute a new value.
# ---------------------------------------------------------------------------

def _fmt_num(x):
    """Thousands-separated string for a number, preserving the tool's own decimal digits exactly (no rounding)."""
    if x is None:
        return "unknown"
    if isinstance(x, bool):
        return str(x)
    if isinstance(x, int):
        return f"{x:,}"
    if isinstance(x, float):
        s = repr(x)
        sign = ""
        if s.startswith("-"):
            sign, s = "-", s[1:]
        if "." in s:
            int_part, dec_part = s.split(".", 1)
            if dec_part == "0":
                return f"{sign}{int(int_part):,}"
            return f"{sign}{int(int_part):,}.{dec_part}"
        return f"{sign}{int(s):,}"
    return str(x)


def _fmt_pct(x):
    """x is already a percentage value computed by the tool -- appending '%' is formatting, not arithmetic."""
    if x is None:
        return "unknown"
    return f"{_fmt_num(x)}%"


def _fmt_bool(x):
    if x is None:
        return "unknown"
    return "yes" if x else "no"


def _confidence_sentence(confidence):
    return f"Confidence: {confidence}."


def _select_caveats(caveats, limit=4):
    """Priority caveats (never dropped) first, then fill remaining slots up to `limit` with the rest."""
    if not caveats:
        return []
    seen = set()
    priority, rest = [], []
    for c in caveats:
        if not c or c in seen:
            continue
        seen.add(c)
        low = c.lower()
        if any(p in low for p in _PRIORITY_CAVEAT_PATTERNS):
            priority.append(c)
        else:
            rest.append(c)
    out = list(priority)
    for c in rest:
        if len(out) >= limit:
            break
        out.append(c)
    return out


def _caveats_block(caveats, extra=None):
    all_caveats = list(caveats or [])
    if extra:
        all_caveats = all_caveats + list(extra)
    selected = _select_caveats(all_caveats)
    if not selected:
        return "Caveats: none recorded in this result."
    return "Caveats:\n" + "\n".join(f"- {_delistify(c)}" for c in selected)


def _collect_as_of_dates(obj, priority, all_dates):
    """
    Recursively walks dicts/lists collecting YYYY-MM(-DD) date-like values:
    anything under an "as_of" key goes in `priority`; any date-shaped
    substring found anywhere (including inside method/caveat prose) goes in
    `all_dates`. Pure traversal/regex -- invents nothing, computes nothing.
    """
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "as_of" and isinstance(v, str):
                m = _DATE_RE.search(v)
                if m:
                    priority.append(m.group(0))
            _collect_as_of_dates(v, priority, all_dates)
    elif isinstance(obj, list):
        for item in obj:
            _collect_as_of_dates(item, priority, all_dates)
    elif isinstance(obj, str):
        all_dates.extend(_DATE_RE.findall(obj))


def _find_as_of(result, method, caveats):
    """
    Best-effort data window, found by walking the ENTIRE result structure
    (not just a top-level `result["as_of"]`) plus method/caveats text, so
    dates nested inside list items (e.g. result["measured"][i]["as_of"],
    result["signals"]["growth"]["caveats"]) are not missed. Reports the most
    common date found (an "as_of" key takes priority over a bare text match);
    ties break on the latest date.
    """
    priority, all_dates = [], []
    _collect_as_of_dates(result, priority, all_dates)
    _collect_as_of_dates(method or "", priority, all_dates)
    _collect_as_of_dates(list(caveats or []), priority, all_dates)
    pool = priority if priority else all_dates
    if not pool:
        return None
    counts = {}
    for d in pool:
        counts[d] = counts.get(d, 0) + 1
    max_count = max(counts.values())
    candidates = [d for d, c in counts.items() if c == max_count]
    return max(candidates)


def _window_sentence(result, method, caveats):
    as_of = _find_as_of(result, method, caveats)
    if as_of:
        return f"Data window: TTM (trailing twelve months), data through {as_of}."
    return "Data window: TTM (trailing twelve months); this result does not carry an explicit as_of date."


def _calendar_year_window_sentence(year, source):
    """
    Some tools (get_long_haul_share) report over a calendar year, not a
    trailing-twelve-month window -- never label those "TTM". `source` is the
    envelope's own source string (e.g. "BTS T-100 Segment (All Carriers),
    route level (FMG), cached 2026-...Z"); strip the cache timestamp so the
    sentence names the real source without inventing new wording.
    """
    source_desc = re.sub(r",?\s*cached\s+\S+\s*$", "", source).strip() if source else None
    if year is None:
        if source_desc:
            return f"Data window: calendar year not specified in this result, {source_desc}."
        return "Data window: calendar year not specified in this result."
    if source_desc:
        return f"Data window: calendar year {year}, {source_desc}."
    return f"Data window: calendar year {year}."


# ---------------------------------------------------------------------------
# Error path
# ---------------------------------------------------------------------------

def _is_error(envelope):
    return isinstance(envelope, dict) and "error" in envelope


def _narrate_error(envelope):
    err = envelope.get("error", {})
    err_type = err.get("type", "Error")
    message = err.get("message", "no further detail available")
    return (
        f"Could not answer this request: {err_type} -- {message}\n\n"
        f"{CLOSING_LINE}"
    )


def _no_result_message(label, envelope):
    """
    result is None: nothing in scope met the requirements. State that plainly
    and quote the envelope's own caveats as the reasons -- never the raw
    `method` string, which describes how the tool computes an answer, not why
    this particular call came back empty.
    """
    reasons = _select_caveats(envelope.get("caveats"))
    if reasons:
        reason_block = "Reasons (from this result's caveats):\n" + "\n".join(f"- {_delistify(c)}" for c in reasons)
    else:
        reason_block = "No caveats were recorded explaining why -- re-check the request (scope, codes, filters)."
    return (
        f"{label}: nothing in scope met the requirements to produce a result.\n\n"
        f"{reason_block}\n\n"
        f"{_confidence_sentence(envelope.get('confidence'))}\n\n"
        f"{CLOSING_LINE}"
    )


# ---------------------------------------------------------------------------
# Ranking table helper (rank_airports, compare_airports, sensitivity)
# ---------------------------------------------------------------------------

def _score_entry_flags(entry):
    """Returns the row's flags as a list of independent component strings (not joined), so
    _score_table can dedupe individual components that are identical across every row."""
    flags = []
    if entry.get("status") and entry["status"] != "scored":
        flags.append(entry["status"])
    if entry.get("gap_driven_by_seat_cuts") is True:
        flags.append("gap driven by seat cuts")
    buildability = entry.get("buildability") or {}
    if buildability.get("has_constraints") is None:
        flags.append("buildability: no entry on file -- unknown constraints")
    elif buildability.get("has_constraints") is True:
        flags.append("buildability constraints on file")
    return flags


def _score_table(entries, limit=10):
    """
    Returns (table_markdown, common_flag_note). Any flag component whose exact text appears
    on every row (2+ rows) is pulled out of the table and shown once as `common_flag_note`
    instead of being repeated on every row; each row keeps only the flag components that
    differ from the rest.
    """
    rows = entries[:limit]
    flag_lists = [_score_entry_flags(e) for e in rows]
    common = []
    if len(rows) > 1 and flag_lists and flag_lists[0]:
        for f in flag_lists[0]:
            if f not in common and all(f in fl for fl in flag_lists):
                common.append(f)
    display_lists = [[f for f in fl if f not in common] for fl in flag_lists]
    common_note = "; ".join(common) if common else None

    header = "| Rank | Airport | Score | Confidence | Flag |\n|---|---|---|---|---|"
    lines = [header]
    for e, fl in zip(rows, display_lists):
        rank = e.get("rank")
        rank_s = _fmt_num(rank) if rank is not None else "-"
        score_s = _fmt_score(e.get("composite_score")) if e.get("composite_score") is not None else "n/a"
        conf_s = e.get("confidence") or "n/a"
        flag_s = "; ".join(fl) if fl else "-"
        lines.append(f"| {rank_s} | {e.get('airport', '?')} | {score_s} | {conf_s} | {flag_s} |")
    return "\n".join(lines), common_note


def _peer_group_sentence(entries):
    bases = sorted({e["peer_group"]["basis"] for e in entries if e.get("peer_group") and e["peer_group"].get("basis")})
    if not bases:
        base_sentence = (
            "Peer group: each airport is scored against its own TTM hub-tier peers (peer group details "
            "not available for every entry)."
        )
    else:
        sizes = {e["peer_group"]["basis"]: e["peer_group"]["size"] for e in entries if e.get("peer_group")}
        parts = [f"{b} tier (peer group size {sizes.get(b)})" for b in bases]
        base_sentence = (
            "Peer group: each airport is scored against its own TTM hub-tier peers, not against the other "
            f"airports in this result -- " + "; ".join(parts) + "."
        )
    return base_sentence + " " + PEER_TIER_SENTENCE


def _top_seat_cut_note(entries):
    if not entries:
        return None
    top = entries[0]
    if top.get("gap_driven_by_seat_cuts") is True:
        return f"Note: the top-ranked airport ({top.get('airport')})'s demand-supply gap is driven by seat cuts, not demand growth."
    return None


# ---------------------------------------------------------------------------
# rank_airports
# ---------------------------------------------------------------------------

def narrate_rank_airports(envelope):
    if _is_error(envelope):
        return _narrate_error(envelope)
    result = envelope["result"]
    if result is None:
        return _no_result_message("rank_airports", envelope)

    scope = result.get("scope")
    ranked = result.get("ranked", [])
    table, common_flag_note = _score_table(ranked)
    lines = [
        f"Ranking for scope **{_scope_label(scope)}** by composite investment score -- a screening aid "
        "based on a hypothesis-driven set of scoring weights, not a verdict.",
        "",
        table,
        "",
    ]
    if common_flag_note:
        lines.append(f"Every row above: {common_flag_note}.")
    lines.append(_peer_group_sentence(ranked))
    note = _top_seat_cut_note(ranked)
    if note:
        lines.append(note)
    entry_caveats = [f"{e.get('airport')}: {c}" for e in ranked[:10] for c in (e.get("caveats") or [])]
    lines.append(_window_sentence(result, envelope["method"], envelope["caveats"]))
    lines.append(_confidence_sentence(envelope["confidence"]))
    lines.append(_caveats_block(envelope["caveats"], extra=entry_caveats))
    lines.append("")
    lines.append(CLOSING_LINE)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# score_airport
# ---------------------------------------------------------------------------

def narrate_score_airport(envelope):
    if _is_error(envelope):
        return _narrate_error(envelope)
    result = envelope["result"]
    if result is None:
        return _no_result_message("score_airport", envelope)

    airport = result.get("airport")
    status = result.get("status")
    score = result.get("composite_score")
    tier = result.get("tier")
    peer_group = result.get("peer_group") or {}
    buildability = result.get("buildability") or {}
    signals = result.get("signals") or {}

    tier_s = tier if tier is not None else "none (not in any TTM hub tier)"
    if status == "scored":
        headline = f"{airport}: composite investment score {_fmt_score(score)} (TTM hub-tier peer group: {tier_s}, peer group size {peer_group.get('size')})."
    else:
        headline = (
            f"{airport}: insufficient data for a composite score (status: {status}; "
            f"TTM hub-tier peer group: {tier_s}, peer group size {peer_group.get('size')})."
        )
    lines = [headline, ""]

    # raw-value formatting per signal: load_factor is a ratio (3 decimals);
    # demand_supply_gap is a percentage-point gap (2 decimals -- 1 decimal
    # would round small but real gaps down to a misleading "-0.0"); the rest
    # are percentages (1 decimal).
    _SIGNAL_FMT = {
        "growth": _fmt_pct1, "load_factor": _fmt_ratio3,
        "demand_supply_gap": _fmt_pp2, "congestion": _fmt_pct1,
    }
    signal_lines = []
    for name in ("growth", "load_factor", "demand_supply_gap", "congestion"):
        sig = signals.get(name)
        if not sig:
            continue
        raw = sig.get("raw")
        z = sig.get("z")
        raw_s = _SIGNAL_FMT[name](raw) if raw is not None else "unavailable"
        z_phrase = _z_phrase(z, tier)
        z_s = z_phrase if z_phrase is not None else "n/a"
        signal_lines.append(f"- {name}: raw {raw_s}, {z_s}")
    if signal_lines:
        lines.append("Signal breakdown (within own TTM hub-tier peer group):")
        lines.extend(signal_lines)
        lines.append(PEER_TIER_SENTENCE)
        lines.append("")

    if result.get("gap_driven_by_seat_cuts") is True:
        lines.append(f"Note: {airport}'s demand-supply gap is driven by seat cuts, not demand growth.")
    elif result.get("gap_driven_by_seat_cuts") is False:
        pass

    if buildability.get("has_constraints") is None:
        lines.append(f"Buildability: no entry on file for {airport} -- unknown constraints, not 'unconstrained'.")
    elif buildability.get("has_constraints") is True:
        lines.append(f"Buildability: constraints on file for {airport} ({buildability.get('note')}).")

    lines.append(_window_sentence(result, envelope["method"], envelope["caveats"]))
    lines.append(_confidence_sentence(envelope["confidence"]))
    lines.append(_caveats_block(envelope["caveats"]))
    lines.append("")
    lines.append(CLOSING_LINE)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# compare_airports
# ---------------------------------------------------------------------------

def narrate_compare_airports(envelope):
    if _is_error(envelope):
        return _narrate_error(envelope)
    result = envelope["result"]
    if result is None:
        return _no_result_message("compare_airports", envelope)

    compared = result.get("compared", [])
    # compare_airports preserves input order and has no "rank" field; number
    # the rows by position for the table without asserting a ranking.
    display = [{**e, "rank": i} for i, e in enumerate(compared, start=1)]
    entry_caveats = [f"{e.get('airport')}: {c}" for e in compared for c in (e.get("caveats") or [])]
    table, common_flag_note = _score_table(display)
    lines = [
        f"Side-by-side composite scores for {', '.join(result.get('codes', []))} -- each airport is scored "
        "against its own TTM hub-tier peers, not against the other airports in this list.",
        "",
        table,
        "",
    ]
    if common_flag_note:
        lines.append(f"Every row above: {common_flag_note}.")
    lines.extend([
        _peer_group_sentence(compared),
        _window_sentence(result, envelope["method"], envelope["caveats"]),
        _confidence_sentence(envelope["confidence"]),
        _caveats_block(envelope["caveats"], extra=entry_caveats),
        "",
        CLOSING_LINE,
    ])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# sensitivity
# ---------------------------------------------------------------------------

def narrate_sensitivity(envelope):
    if _is_error(envelope):
        return _narrate_error(envelope)
    result = envelope["result"]
    if result is None:
        return _no_result_message("sensitivity", envelope)

    scope = result.get("scope")
    rankings = result.get("rankings", {})
    rank_range = result.get("rank_range", {})
    stable_top3 = result.get("stable_top3", [])
    weight_sets = result.get("weight_sets", {})

    base_name = "hypotheses" if "hypotheses" in rankings else (sorted(rankings)[0] if rankings else None)
    base_ranking = rankings.get(base_name, []) if base_name else []

    rows = []
    for entry in base_ranking:
        code = entry["airport"]
        rr = rank_range.get(code, {})
        rows.append({
            "rank": entry["rank"],
            "airport": code,
            "composite_score": entry["composite_score"],
            "confidence": f"rank {rr.get('min_rank')}-{rr.get('max_rank')} across weight sets",
            "status": "scored",
            "gap_driven_by_seat_cuts": None,
            "buildability": {},
        })

    table, common_flag_note = _score_table(rows)
    lines = [
        f"Sensitivity of the ranking for scope **{_scope_label(scope)}** to {len(weight_sets)} alternative "
        f"weight set(s) ({', '.join(sorted(weight_sets))}), base weighting '{base_name}' shown below.",
        "",
        table,
        "",
    ]
    if common_flag_note:
        lines.append(f"Every row above: {common_flag_note}.")
    if stable_top3:
        lines.append(
            f"Stable across every weight set tried: {', '.join(stable_top3)} remain in the top 3 regardless of weighting."
        )
    else:
        lines.append("Not stable: no airport stays in the top 3 across every weight set tried -- the ranking moves with the weighting hypothesis.")
    lines.append(_window_sentence(result, envelope["method"], envelope["caveats"]))
    lines.append(_confidence_sentence(envelope["confidence"]))
    lines.append(_caveats_block(envelope["caveats"]))
    lines.append("")
    lines.append(CLOSING_LINE)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# list_region_airports
# ---------------------------------------------------------------------------

def narrate_list_region_airports(envelope):
    if _is_error(envelope):
        return _narrate_error(envelope)
    result = envelope["result"]
    if result is None:
        return _no_result_message("list_region_airports", envelope)

    scope = result.get("scope")
    airports = result.get("airports", [])
    rows = airports[:10]
    lines = [
        f"Airports in scope **{_scope_label(scope)}** above the TTM volume floor ({len(airports)} total; not scored or ranked):",
        "",
        "| Airport | Name | State | TTM passengers |",
        "|---|---|---|---|",
    ]
    for a in rows:
        lines.append(f"| {a.get('code')} | {a.get('name')} | {a.get('state')} | {_fmt_num(a.get('passengers'))} |")
    lines.append("")
    lines.append(_window_sentence(result, envelope["method"], envelope["caveats"]))
    lines.append(_confidence_sentence(envelope["confidence"]))
    lines.append(_caveats_block(envelope["caveats"]))
    lines.append("")
    lines.append(CLOSING_LINE)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# get_airport_traffic
# ---------------------------------------------------------------------------

def narrate_get_airport_traffic(envelope):
    if _is_error(envelope):
        return _narrate_error(envelope)
    result = envelope["result"]
    if result is None:
        return _no_result_message("get_airport_traffic", envelope)

    airport = result.get("airport")
    pax = result.get("ttm_passengers")
    seats = result.get("ttm_seats")
    load_factor = result.get("load_factor")
    growth = result.get("growth_pct")

    pax_s = _fmt_num(pax) if pax is not None else "unknown"
    lines = [
        f"{airport}: {pax_s} TTM departing passengers (enplanements, by origin airport, connections included).",
    ]
    if seats is not None:
        lines.append(f"TTM seats: {_fmt_num(seats)}.")
    if load_factor is not None:
        lines.append(f"TTM load factor (passengers/seats ratio): {_fmt_ratio3(load_factor)}.")
    else:
        lines.append("TTM load factor: unknown (not computable from cached data).")
    if growth is not None:
        lines.append(f"TTM passenger growth vs the prior TTM window: {_fmt_pct1(growth)}.")
    else:
        lines.append("TTM passenger growth: unknown (not computable from cached data).")

    lines.append(_window_sentence(result, envelope["method"], envelope["caveats"]))
    lines.append(_confidence_sentence(envelope["confidence"]))
    lines.append(_caveats_block(envelope["caveats"]))
    lines.append("")
    lines.append(CLOSING_LINE)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# compare_congestion
# ---------------------------------------------------------------------------

def narrate_compare_congestion(envelope):
    if _is_error(envelope):
        return _narrate_error(envelope)
    result = envelope["result"]
    if result is None:
        return _no_result_message("compare_congestion", envelope)

    compared = result.get("compared", [])
    lines = [
        f"TTM flight counts and % of domestic departures delayed >=15min for {', '.join(result.get('codes', []))}:",
        "",
        "| Airport | Flights | % delayed >=15min | OTP/T-100 coverage | Coverage window | Low coverage |",
        "|---|---|---|---|---|---|",
    ]
    _OVERLAP_RE = re.compile(r"coverage computed over (\d+) overlapping month")
    per_airport_caveats = []
    for e in compared:
        flights_s = _fmt_num(e.get("flights")) if e.get("flights") is not None else "unknown"
        delayed_s = _fmt_pct1(e.get("delayed_share_pct")) if e.get("delayed_share_pct") is not None else "unknown"
        cov_s = _fmt_pct1(e.get("coverage_pct")) if e.get("coverage_pct") is not None else "unknown"
        n_overlap = None
        for c in e.get("caveats") or []:
            m = _OVERLAP_RE.search(c)
            if m:
                n_overlap = int(m.group(1))
                break
        window_s = f"{n_overlap} of 12 months" if n_overlap is not None else "unknown"
        lines.append(
            f"| {e.get('airport')} | {flights_s} | {delayed_s} | {cov_s} | {window_s} | {_fmt_bool(e.get('low_coverage'))} |"
        )
        if e.get("low_coverage"):
            per_airport_caveats.append(f"{e.get('airport')}: low OTP coverage")
        per_airport_caveats.extend(f"{e.get('airport')}: {c}" for c in (e.get("caveats") or []))

    lines.append("")
    lines.append(_window_sentence(result, envelope["method"], envelope["caveats"]))
    lines.append(_confidence_sentence(envelope["confidence"]))
    lines.append(_caveats_block(envelope["caveats"], extra=per_airport_caveats))
    lines.append("")
    lines.append(CLOSING_LINE)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# get_long_haul_share
# ---------------------------------------------------------------------------

def narrate_get_long_haul_share(envelope):
    if _is_error(envelope):
        return _narrate_error(envelope)
    result = envelope["result"]
    if result is None:
        return _no_result_message("get_long_haul_share", envelope)

    airport = result.get("airport")
    year = result.get("year")
    thresholds = result.get("thresholds_mi", [])
    groups = result.get("groups", {})

    lines = [
        f"{airport} long-haul departure share, year {year} -- distance threshold(s) in miles: "
        f"{', '.join(_fmt_num(t) for t in thresholds)} (a caller-supplied parameter, not an official FAA/BTS definition).",
        "",
    ]
    for thr in thresholds:
        parts = []
        for group_name in ("passenger", "all"):
            g = groups.get(group_name)
            if not g:
                continue
            share = g.get("shares_pct", {}).get(thr) if g.get("shares_pct") else None
            share = share if share is not None else g.get("shares_pct", {}).get(str(thr))
            label = "passenger flights" if group_name == "passenger" else "all flights (passenger + cargo)"
            share_s = _fmt_pct1(share) if share is not None else "unknown"
            parts.append(f"{label}: {share_s}")
        if parts:
            lines.append(f"- at {_fmt_num(thr)} mi: " + "; ".join(parts))
    lines.append("")
    lines.append(_calendar_year_window_sentence(year, envelope.get("source")))
    lines.append(_confidence_sentence(envelope["confidence"]))
    lines.append(_caveats_block(envelope["caveats"]))
    lines.append("")
    lines.append(CLOSING_LINE)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# get_buildability
# ---------------------------------------------------------------------------

def narrate_get_buildability(envelope):
    if _is_error(envelope):
        return _narrate_error(envelope)
    result = envelope["result"]
    if result is None:
        return _no_result_message("get_buildability", envelope)

    airport = result.get("airport")
    has_constraints = result.get("has_constraints")
    lines = []
    extra_caveats = []
    if has_constraints is False:
        lines.append(f"{airport}: {result.get('note', 'no constraints on file')} -- this means not researched, not verified unconstrained.")
    elif has_constraints is True:
        lines.append(f"{airport}: curated capacity/slot/perimeter constraints on file ({result.get('name')}):")
        for c in result.get("constraints", []):
            value_s = _fmt_num(c.get("value")) if c.get("value") is not None else "unknown"
            unit = c.get("unit", "")
            until = c.get("effective_until")
            verified = c.get("verified_by_user")
            needs_verif = c.get("needs_verification")
            line = f"- {c.get('type')}: {value_s} {unit}".rstrip()
            if until:
                line += f", effective until {until}"
            line += f" (verified_by_user={_fmt_bool(verified)}"
            if needs_verif:
                line += f", needs_verification: {needs_verif}"
                extra_caveats.append(f"{c.get('type')} constraint needs_verification: {needs_verif}")
            line += ")"
            lines.append(line)
    else:
        lines.append(f"{airport}: buildability unknown -- {result.get('note', 'no data')}.")

    lines.append("")
    lines.append(_window_sentence(result, envelope["method"], envelope["caveats"]))
    lines.append(_confidence_sentence(envelope["confidence"]))
    lines.append(_caveats_block(envelope["caveats"], extra=extra_caveats))
    lines.append("")
    lines.append(CLOSING_LINE)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# get_unmet_demand_breakdown
# ---------------------------------------------------------------------------

def _fact_line(fact):
    name = fact.get("name")
    value = fact.get("value")
    unit = fact.get("unit")
    if value is None:
        reason = fact.get("reason", "not computable")
        return f"- {name}: unknown ({reason})"
    if isinstance(value, dict):
        # metro_context -- a sibling_growth_list dict, not a bare number.
        siblings = value.get("siblings", [])
        sib_s = "; ".join(
            f"{s.get('airport')} growth {_fmt_pct1(s.get('growth_pct')) if s.get('growth_pct') is not None else 'unknown'}"
            for s in siblings
        )
        return f"- {name} ({value.get('metro_name')}): {sib_s if sib_s else 'no sibling data'}"
    if unit == "percentage_points":
        value_s = _fmt_pp2(value)
    elif unit == "percent":
        value_s = _fmt_pct1(value)
    elif unit == "ratio":
        value_s = _fmt_ratio3(value)
    elif unit == "months":
        value_s = f"{_fmt_num(value)} months"
    else:
        value_s = f"{_fmt_num(value)}{f' {unit}' if unit else ''}"
    peer = fact.get("peer_context")
    peer_s = ""
    if peer:
        z_phrase = _z_phrase(peer.get("z"), peer.get("tier"))
        peer_s = f" ({z_phrase})" if z_phrase else " (peer comparison not available)"
    return f"- {name}: {value_s}{peer_s}"


def narrate_get_unmet_demand_breakdown(envelope):
    if _is_error(envelope):
        return _narrate_error(envelope)
    result = envelope["result"]
    if result is None:
        return _no_result_message("get_unmet_demand_breakdown", envelope)

    airport = result.get("airport")
    measured = result.get("measured", [])
    inferred = result.get("inferred", [])
    unknown = result.get("unknown", [])

    lines = [
        f"Unmet-demand breakdown for {airport} -- there is no single 'unmet demand' number; this is a "
        "three-part decomposition of public-data facts.",
        "",
        "Measured:",
    ]
    lines.extend(_fact_line(f) for f in measured)
    lines.append(PEER_TIER_SENTENCE)
    lines.append("")
    lines.append("Inferred (hypotheses, each 'consistent with' the measured facts -- never proof):")
    if inferred:
        for inf in inferred:
            lines.append(f"- {inf.get('statement')}")
            lines.append(f"  Confidence in this inference: {inf.get('strength')}.")
            alts = inf.get("alternative_explanations") or []
            if alts:
                lines.append(f"  Alternative explanations: {'; '.join(alts)}")
    else:
        lines.append("- none of the deterministic inference rules fired for this airport's measured facts")
    lines.append("")
    lines.append("Unknown (what this public data structurally cannot tell us here):")
    for u in unknown:
        lines.append(f"- {u.get('name')}: {u.get('why_we_cannot_know')}")

    lines.append("")
    lines.append(_window_sentence(result, envelope["method"], envelope["caveats"]))
    lines.append(_confidence_sentence(envelope["confidence"]))
    lines.append(_caveats_block(envelope["caveats"]))
    lines.append("")
    lines.append(CLOSING_LINE)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# get_forward_outlook
# ---------------------------------------------------------------------------

def _outlook_window_sentence(as_of):
    if as_of is None:
        return (
            "Data window: FAA TAF base year not available for any requested airport. FAA fiscal years "
            "run Oct-Sep; the TAF base year is FY2024 and is not aligned to this project's TTM window."
        )
    return (
        f"Data window: FAA TAF base year FY{as_of} (FAA fiscal year, Oct-Sep) -- not aligned to this "
        "project's TTM (trailing-twelve-month) window."
    )


def _fmt_cagr(x):
    """cagr_5y/cagr_10y are stored as a ratio (e.g. 0.0234 for 2.34% CAGR) -- display as a percent with 1 decimal."""
    if x is None:
        return "unknown"
    return _fmt_pct1(x * 100)


def narrate_get_forward_outlook(envelope):
    if _is_error(envelope):
        return _narrate_error(envelope)
    result = envelope["result"]
    if result is None:
        return _no_result_message("get_forward_outlook", envelope)

    airports = result.get("airports", [])
    as_of = result.get("as_of")

    lines = [
        "FAA Terminal Area Forecast (TAF) outlook and runway counts -- context only, shown exactly as "
        "the FAA published them. This is the FAA's own forecast, not a forecast produced by this agent, "
        "it is unconstrained (assumes capacity is provided), and it is NOT part of the composite "
        "investment score.",
        "",
    ]

    if len(airports) > 1:
        lines.extend([
            "| Airport | FAA LID | TAF base FY | Base enplanements | +5y | +10y | CAGR 5y | CAGR 10y | "
            "Qualifying runways | Enplanements/runway | Note |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ])
        for a in airports:
            lines.append(
                f"| {a.get('code')} | {a.get('faa_lid') or 'unmatched'} | "
                f"{a.get('taf_base_fy') if a.get('taf_base_fy') is not None else 'unknown'} | "
                f"{_fmt_num(a.get('enplanements_base'))} | {_fmt_num(a.get('enplanements_plus5'))} | "
                f"{_fmt_num(a.get('enplanements_plus10'))} | "
                f"{_fmt_cagr(a.get('cagr_5y'))} | {_fmt_cagr(a.get('cagr_10y'))} | "
                f"{_fmt_num(a.get('qualifying_runways'))} | {_fmt_num(a.get('enplanements_per_runway'))} | "
                f"{a.get('match_note') or '-'} |"
            )
    else:
        a = airports[0] if airports else {}
        code = a.get("code", "?")
        if a.get("faa_lid") is None:
            lines.append(
                f"{code}: unmatched to the FAA TAF -- {a.get('match_note') or 'no reason recorded'}. "
                f"Qualifying runways on file: {_fmt_num(a.get('qualifying_runways'))}."
            )
        else:
            cagr5_s = _fmt_cagr(a.get("cagr_5y"))
            cagr10_s = _fmt_cagr(a.get("cagr_10y"))
            epr = a.get("enplanements_per_runway")
            epr_s = _fmt_num(int(round(epr))) if epr is not None else "unknown"
            lines.append(
                f"{code}: FAA TAF base-year (FY{a.get('taf_base_fy')}) enplanements "
                f"{_fmt_num(a.get('enplanements_base'))}, projected to {_fmt_num(a.get('enplanements_plus5'))} "
                f"at +5y (CAGR {cagr5_s}) and {_fmt_num(a.get('enplanements_plus10'))} at +10y (CAGR {cagr10_s}). "
                f"{_fmt_num(a.get('qualifying_runways'))} qualifying runway(s) on file, "
                f"{epr_s} enplanements/runway (a rough proxy, not capacity)."
            )

    lines.append("")
    lines.append(_outlook_window_sentence(as_of))
    lines.append(_confidence_sentence(envelope["confidence"]))
    # Fixed caveat set (always exactly these 5, never truncated by
    # _caveats_block's cap-at-4 rule -- every caveat here is load-bearing).
    lines.append("Caveats:\n" + "\n".join(f"- {c}" for c in envelope["caveats"]))
    lines.append("")
    lines.append(CLOSING_LINE)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# describe_data_sources
# ---------------------------------------------------------------------------

def narrate_describe_data_sources(envelope):
    if _is_error(envelope):
        return _narrate_error(envelope)
    result = envelope["result"]
    if result is None:
        return _no_result_message("describe_data_sources", envelope)

    lines = [
        "Upstream data sources used by this project:",
        "",
        "| Source | Publisher | Vintage | Access | Caveat |",
        "|---|---|---|---|---|",
    ]
    for e in result:
        lines.append(f"| {e.get('name')} | {e.get('publisher')} | {e.get('vintage')} | {e.get('access')} | {e.get('caveat')} |")
    lines.append("")
    lines.append(_confidence_sentence(envelope["confidence"]))
    lines.append(_caveats_block(envelope["caveats"]))
    lines.append("")
    lines.append(CLOSING_LINE)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# rank_airports_by_traffic
# ---------------------------------------------------------------------------

def narrate_rank_airports_by_traffic(envelope):
    if _is_error(envelope):
        return _narrate_error(envelope)
    result = envelope["result"]
    if result is None:
        return _no_result_message("rank_airports_by_traffic", envelope)

    scope = result.get("scope")
    ranked = result.get("ranked", [])
    lines = [
        f"Busiest airports in scope **{_scope_label(scope)}** by TTM departing passengers (enplanements, by "
        "origin airport, connections included) -- not an investment ranking.",
        "",
        "| Rank | Airport | Name | Tier | Departing passengers | Growth vs prior TTM |",
        "|---|---|---|---|---|---|",
    ]
    for e in ranked:
        rank_s = _fmt_num(e.get("rank")) if e.get("rank") is not None else "-"
        pax_s = _fmt_num(e.get("ttm_passengers")) if e.get("ttm_passengers") is not None else "unknown"
        growth_s = _fmt_pct1(e.get("growth_pct")) if e.get("growth_pct") is not None else "n/a"
        tier_s = e.get("tier") or "none (not in any TTM hub tier)"
        lines.append(f"| {rank_s} | {e.get('code', '?')} | {e.get('name') or 'unknown'} | {tier_s} | {pax_s} | {growth_s} |")
    lines.append("")
    lines.append(_window_sentence(result, envelope["method"], envelope["caveats"]))
    lines.append(_confidence_sentence(envelope["confidence"]))
    lines.append(_caveats_block(envelope["caveats"]))
    lines.append("")
    lines.append(CLOSING_LINE)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_NARRATORS = {
    "rank_airports": narrate_rank_airports,
    "rank_airports_by_traffic": narrate_rank_airports_by_traffic,
    "score_airport": narrate_score_airport,
    "compare_airports": narrate_compare_airports,
    "sensitivity": narrate_sensitivity,
    "list_region_airports": narrate_list_region_airports,
    "get_airport_traffic": narrate_get_airport_traffic,
    "compare_congestion": narrate_compare_congestion,
    "get_long_haul_share": narrate_get_long_haul_share,
    "get_buildability": narrate_get_buildability,
    "get_unmet_demand_breakdown": narrate_get_unmet_demand_breakdown,
    "get_forward_outlook": narrate_get_forward_outlook,
    "describe_data_sources": narrate_describe_data_sources,
}


def narrate(tool_name, envelope):
    """Dispatches to narrate_<tool_name>. Unknown tool name -> short error message, same closing line."""
    fn = _NARRATORS.get(tool_name)
    if fn is None:
        return (
            f"Could not answer this request: UnknownToolError -- no narrator for tool {tool_name!r}.\n\n"
            f"{CLOSING_LINE}"
        )
    return fn(envelope)
