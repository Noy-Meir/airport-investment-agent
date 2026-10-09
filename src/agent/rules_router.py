"""
Phase 8 step 3b-1: a pure, deterministic rules-based router.
plan(message) -> Plan(intent, tool, args) picks a single tool + args for one
single-turn question using regex/keyword matching only -- no model call, no
tool execution. Tool names and arg shapes here must stay in lock-step with
src/tools/registry.py (SCOPE_SCHEMA / TRAFFIC_SCOPE_SCHEMA / TOOLS).

Out of scope for this step (see docs/DECISIONS.md and the eval harness at
the bottom of this file): follow-up questions ("and what about...", pronoun
references to a prior turn), refusal-shaped questions (forecasts, legal/
financial advice, requests to fabricate numbers, out-of-US airports), and
actually calling call_tool(). Those return Plan(intent="unknown", tool=None,
args={}) here; a later step handles them.
"""

import re
from typing import NamedTuple, Optional


class Plan(NamedTuple):
    intent: str
    tool: Optional[str]
    args: dict


UNKNOWN = Plan(intent="unknown", tool=None, args={})

# --- scope resolution -------------------------------------------------

REGION_ALIASES = {
    "new england": "new_england",
    "new_england": "new_england",
}

TIER_KEYWORDS = {
    "large hubs": "large", "large hub": "large", "large airports": "large",
    "medium hubs": "medium", "medium hub": "medium", "medium airports": "medium",
    "small hubs": "small", "small hub": "small", "small airports": "small",
    "micro hubs": "micro", "micro hub": "micro", "micro airports": "micro",
}

# Full name -> USPS code, for "which airports are in Connecticut and Maine"
# style phrasing (state *codes* like "CT" are matched separately below).
STATE_NAME_TO_CODE = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA",
    "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
    "virginia": "VA", "washington": "WA", "west virginia": "WV", "wisconsin": "WI",
    "wyoming": "WY", "district of columbia": "DC",
}
STATE_CODES = set(STATE_NAME_TO_CODE.values())


def _resolve_scope(text_lower):
    """Returns a SCOPE_SCHEMA-shaped dict ({"region": ...} / {"tier": ...} /
    {"states": [...]})  or None if no scope cue is present. Region wins over
    tier wins over states, since a message naming a region rarely also means
    to restrict further by tier/state in the same breath."""
    for phrase, region in REGION_ALIASES.items():
        if phrase in text_lower:
            return {"region": region}
    for phrase, tier in TIER_KEYWORDS.items():
        if phrase in text_lower:
            return {"tier": tier}

    states = []
    for name, code in STATE_NAME_TO_CODE.items():
        if re.search(rf"\b{re.escape(name)}\b", text_lower):
            states.append(code)
    for code in STATE_CODES:
        if re.search(rf"\b{code.lower()}\b", text_lower) and code not in states:
            # 2-letter state codes are only trustworthy written upper-case
            # ("CT and ME"); matching them lower-case would catch too much
            # ordinary English ("in", "or", ...), so re-check against the
            # original-case text at the call site instead.
            pass
    if states:
        return {"states": sorted(set(states))}
    return None


def _resolve_scope_states_uppercase(message, scope):
    """Adds upper-case 2-letter state-code mentions (e.g. 'CT and ME') on
    top of whatever _resolve_scope found from full state names."""
    found = {m for m in re.findall(r"\b[A-Z]{2}\b", message) if m in STATE_CODES}
    if not found:
        return scope
    if scope is not None and "states" in scope:
        return {"states": sorted(set(scope["states"]) | found)}
    if scope is None:
        return {"states": sorted(found)}
    return scope


# --- airport resolution -------------------------------------------------

# City/metro alias -> IATA code. Hand-curated, deliberately small: only
# names that are unambiguous in everyday usage. A bare "LA" defaults to
# LAX (not Burbank/Long Beach/Ontario) -- documented here per the brief.
# Ambiguous city names (Chicago, New York, Washington...) are intentionally
# left out; they fall through to "unknown" rather than guessing wrong.
AIRPORT_ALIASES = {
    "los angeles": "LAX",
    "la": "LAX",
    "l.a.": "LAX",
    "santa ana": "SNA",
    "orange county": "SNA",
    "john wayne": "SNA",
    "anchorage": "ANC",
    "san francisco": "SFO",
    "sf": "SFO",
    "boston": "BOS",
    "logan": "BOS",
    "burlington": "BTV",
    "manchester": "MHT",
    "portland": "PWM",  # Portland, ME -- not Portland, OR (no IATA/PDX claim here)
    "providence": "PVD",
    "new haven": "HVN",
    "nantucket": "ACK",
    "bangor": "BGR",
    "worcester": "ORH",
    "hartford": "BDL",
}

# 3-letter all-caps tokens that look like IATA codes but aren't, so they
# don't get misread as airport mentions.
_CODE_STOPWORDS = {"TTM", "OTP", "FAA", "BTS", "LLC", "CEO", "USA", "LA"}


def resolve_airport_codes(message, conn=None):
    """
    Airport codes mentioned in `message`, in order of first mention,
    deduplicated. Matches (a) known city/metro aliases (case-insensitive,
    longest phrase first) and (b) bare 3-letter IATA-shaped tokens. With a
    `conn`, tokens are matched case-insensitively and validated against the
    cache (src.cache.accessors.validate_airport_code), dropping ordinary
    words ("the", "new", ...) that aren't real airports. Without a conn
    there is no way to tell a real code from an ordinary 3-letter word, so
    only exact upper-case tokens count as codes (the normal convention for
    writing IATA codes) -- including an intentionally-invalid one like
    "ZZZ", since whether it's a real airport is a tool-execution concern,
    not a planning one.
    """
    spans = []
    lower = message.lower()
    for phrase in sorted(AIRPORT_ALIASES, key=len, reverse=True):
        for m in re.finditer(rf"\b{re.escape(phrase)}\b", lower):
            spans.append((m.start(), AIRPORT_ALIASES[phrase]))

    code_pattern = r"\b[A-Za-z]{3}\b" if conn is not None else r"\b[A-Z]{3}\b"
    for m in re.finditer(code_pattern, message):
        token = m.group(0).upper()
        if token in _CODE_STOPWORDS:
            continue
        if conn is not None:
            from src.cache.accessors import AirportNotFoundError, validate_airport_code

            try:
                validate_airport_code(conn, token)
            except AirportNotFoundError:
                continue
        spans.append((m.start(), token))

    spans.sort(key=lambda s: s[0])
    codes = []
    for _, code in spans:
        if code not in codes:
            codes.append(code)
    return codes


# --- intent keyword tables -------------------------------------------------

_DATA_SOURCES_RE = re.compile(
    r"data sources?\b|how (recent|fresh)|where (do|does).*(data|numbers?) come from|what data (do you use|is used)",
    re.IGNORECASE,
)
_BUILDABILITY_RE = re.compile(r"buildability|\bconstraints?\b|\bslot[s]?\b|\bperimeter\b|\brunway\b", re.IGNORECASE)
_SENSITIVITY_RE = re.compile(r"sensitiv|\brobust|\bweight(ed|ing)?\b", re.IGNORECASE)
_LONG_HAUL_RE = re.compile(r"long[\s-]?haul", re.IGNORECASE)
_UNMET_DEMAND_RE = re.compile(r"unmet demand|demand pressure|capacity pressure|\bconstrained\b", re.IGNORECASE)
_CONGESTION_RE = re.compile(r"congest|\bdelay(ed|s)?\b|on[\s-]?time|\botp\b", re.IGNORECASE)
_COMPARE_RE = re.compile(r"\bcompare\b|\bvs\.?\b|\bversus\b", re.IGNORECASE)
_TRAFFIC_RANK_RE = re.compile(r"\bbusiest\b|\bbiggest\b|\blargest\b", re.IGNORECASE)
_SCORE_RE = re.compile(r"\bscore\b|\boutlook\b|composite score|how does .* look", re.IGNORECASE)
_TRAFFIC_RE = re.compile(r"\bpassenger(s)?\b|\btraffic\b|\bload factor\b|\bgrowth\b|\benplanements?\b", re.IGNORECASE)
_RANK_RE = re.compile(
    r"\bexpansion\b|\bcandidates?\b|\bbest\b|\brank(ing)?\b|\bleaderboard\b|\btop\s+\d+\b|\btop airports\b",
    re.IGNORECASE,
)
_LIST_REGION_RE = re.compile(r"which airports|list airports", re.IGNORECASE)

_THRESHOLD_MI_RE = re.compile(r"(\d[\d,]*)\s*-?\s*mile")
_TOP_N_RE = re.compile(r"\btop\s+(\d+)\b", re.IGNORECASE)
_N_BUSIEST_RE = re.compile(r"\b(\d+)\s+(?:busiest|biggest|largest)\b", re.IGNORECASE)


def plan(message, conn=None):
    """Deterministic single-turn tool plan. See module docstring for scope."""
    if not message or not isinstance(message, str):
        return UNKNOWN

    text_lower = message.lower()
    codes = resolve_airport_codes(message, conn=conn)
    scope = _resolve_scope_states_uppercase(message, _resolve_scope(text_lower))

    if _DATA_SOURCES_RE.search(text_lower):
        return Plan("describe_data_sources", "describe_data_sources", {})

    if _BUILDABILITY_RE.search(text_lower):
        if codes:
            return Plan("buildability", "get_buildability", {"code": codes[0]})
        return UNKNOWN

    if _SENSITIVITY_RE.search(text_lower):
        if scope is not None:
            return Plan("sensitivity", "sensitivity", {"scope": scope})
        return UNKNOWN

    if _LONG_HAUL_RE.search(text_lower):
        if codes:
            args = {"code": codes[0]}
            thresholds = sorted({int(m.replace(",", "")) for m in _THRESHOLD_MI_RE.findall(text_lower)})
            if thresholds:
                args["thresholds_mi"] = thresholds
            return Plan("long_haul_share", "get_long_haul_share", args)
        return UNKNOWN

    if _UNMET_DEMAND_RE.search(text_lower):
        if codes:
            return Plan("unmet_demand", "get_unmet_demand_breakdown", {"code": codes[0]})
        return UNKNOWN

    if _CONGESTION_RE.search(text_lower):
        if len(codes) >= 2:
            return Plan("compare_congestion", "compare_congestion", {"codes": codes})
        return UNKNOWN

    if _TRAFFIC_RANK_RE.search(text_lower):
        rank_scope = scope if scope is not None else {"all": True}
        args = {"scope": rank_scope}
        top_n_match = _TOP_N_RE.search(text_lower) or _N_BUSIEST_RE.search(text_lower)
        if top_n_match:
            args["top_n"] = int(top_n_match.group(1))
        return Plan("rank_by_traffic", "rank_airports_by_traffic", args)

    if _COMPARE_RE.search(text_lower) and len(codes) >= 2:
        return Plan("compare_airports", "compare_airports", {"codes": codes})

    if _RANK_RE.search(text_lower):
        if scope is not None:
            args = {"scope": scope}
            top_n_match = _TOP_N_RE.search(text_lower)
            if top_n_match:
                args["top_n"] = int(top_n_match.group(1))
            return Plan("rank_airports", "rank_airports", args)
        return UNKNOWN

    if _LIST_REGION_RE.search(text_lower):
        if scope is not None and ("region" in scope or "states" in scope):
            region_or_states = {"region": scope["region"]} if "region" in scope else {"states": scope["states"]}
            return Plan("list_region_airports", "list_region_airports", {"region_or_states": region_or_states})
        return UNKNOWN

    if _SCORE_RE.search(text_lower) and len(codes) == 1:
        return Plan("score_airport", "score_airport", {"code": codes[0]})

    if _TRAFFIC_RE.search(text_lower) and len(codes) == 1:
        return Plan("airport_traffic", "get_airport_traffic", {"code": codes[0]})

    return UNKNOWN


# --- eval harness (run as a script: python -m src.agent.rules_router) ------

def _run_eval(path="data/eval/tool_selection_cases.json"):
    import json

    with open(path) as f:
        cases = json.load(f)

    matched, unmatched, out_of_scope = [], [], []
    for case in cases:
        if "previous_turn" in case or case["id"].startswith("followup_") or not case["expected_tools"]:
            out_of_scope.append(case["id"])
            continue
        p = plan(case["question"])
        if p.tool is not None and p.tool in case["expected_tools"]:
            matched.append(case["id"])
        else:
            unmatched.append((case["id"], p.tool))
    return matched, unmatched, out_of_scope


if __name__ == "__main__":
    matched, unmatched, out_of_scope = _run_eval()
    print(f"matched ({len(matched)}): {matched}")
    print(f"unmatched ({len(unmatched)}): {unmatched}")
    print(f"out_of_scope ({len(out_of_scope)}): {out_of_scope}")
