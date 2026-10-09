"""
Phase 8 step 3b-2: refusals/scope, clarification, and follow-ups on top of
the step 3b-1 single-turn router.

plan(message, state=None, conn=None) -> Plan(intent, tool, args, message)
still picks at most one tool + args using regex/keyword matching only -- no
model call, no tool execution. Tool names and arg shapes here must stay in
lock-step with src/tools/registry.py (SCOPE_SCHEMA / TRAFFIC_SCOPE_SCHEMA /
TOOLS).

New in this step:
- Fixed-message refusals for guarantees/certainty, legal/financial advice,
  fares/ROI/construction/financing, non-US airports, non-aviation questions,
  prompt-injection attempts, and non-English text (mirrors
  src/agent/system_prompt.md's refusal/scope rules).
- A clarification message when a ranking question names no resolvable scope,
  instead of guessing one.
- A help message (the 4 SAMPLE_QUESTIONS + what's out of scope) for truly
  unmatched/unknown questions.
- `State` + follow-up handling: ordinals against a prior ranking, "add X to
  that comparison", "what about X", tier-scope changes, "why did X rank
  above Y", "how confident/what are your caveats" (-> intent="restate",
  executed by the caller in a later step), and reweight requests (only the
  presets `sensitivity` actually supports -- growth/congestion -- are
  treated as supported; anything else gets a message offering `sensitivity`
  instead of a custom one-off reweighting, which the registry doesn't
  support).

Still out of scope here (see docs/DECISIONS.md): actually calling
call_tool(). That's a later step; `intent="restate"` and the follow-up
Plans above are planned but not executed by this module.
"""

import re
from dataclasses import dataclass
from typing import List, NamedTuple, Optional


class Plan(NamedTuple):
    intent: str
    tool: Optional[str]
    args: dict
    # Set only for refusal / clarification / help / restate / unsupported
    # intents (tool is None): the fixed text to show the user directly.
    message: Optional[str] = None


UNKNOWN = Plan(intent="unknown", tool=None, args={})

# --- conversation state for follow-ups --------------------------------

@dataclass
class State:
    last_tool: Optional[str] = None
    last_args: Optional[dict] = None
    last_scope: Optional[dict] = None
    last_airports: Optional[List[str]] = None
    last_ranking_order: Optional[List[str]] = None
    last_envelope: Optional[dict] = None


def next_state(state, plan_, result):
    """State to carry into the next turn after executing `plan_` and getting
    tool `result` (the uniform envelope dict, or None for a turn that called
    no tool -- a refusal, clarification, restate, or unsupported-reweight).
    Execution of `plan_` happens elsewhere; this just folds the outcome in.
    """
    if plan_.tool is None:
        # No tool ran: keep whatever context we had so a user can still say
        # "the second one" right after an aside question.
        return state if state is not None else State()

    prior_scope = state.last_scope if state is not None else None
    prior_airports = state.last_airports if state is not None else None
    prior_order = state.last_ranking_order if state is not None else None

    scope = plan_.args.get("scope", prior_scope)
    if "codes" in plan_.args:
        airports = list(plan_.args["codes"])
    elif "code" in plan_.args:
        airports = [plan_.args["code"]]
    else:
        airports = prior_airports

    ranking_order = prior_order
    if isinstance(result, dict) and isinstance(result.get("result"), list):
        rows = result["result"]
        codes_in_result = [row.get("code") for row in rows if isinstance(row, dict) and row.get("code")]
        if codes_in_result:
            ranking_order = codes_in_result

    return State(
        last_tool=plan_.tool,
        last_args=plan_.args,
        last_scope=scope,
        last_airports=airports,
        last_ranking_order=ranking_order,
        last_envelope=result if result is not None else (state.last_envelope if state is not None else None),
    )


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

# Anything in-domain for this project at all; used as a last-resort gate
# between "truly off-topic" (non_aviation refusal) and "on-topic but
# unresolved" (help message).
_DOMAIN_RE = re.compile(
    r"airport|airline|flight|passenger|traffic|congest|delay|runway|hub\b|tier|invest|score|\brank"
    r"|compare|long.?haul|unmet|demand|buildability|\bslot|perimeter|data source|capacity|load factor"
    r"|growth|enplane|metro|region|\bstate\b|expansion|candidate|otp\b",
    re.IGNORECASE,
)

# --- refusal / scope keyword tables -------------------------------------

_INJECTION_RE = re.compile(
    r"ignore (all |any )?(previous |prior |your )?instructions"
    r"|disregard (all |any )?(previous |prior |your )?instructions"
    r"|reveal (your |the )?(system )?prompt"
    r"|(what('s| is)|show me) your (system )?prompt",
    re.IGNORECASE,
)
_SINGLE_ANSWER_RE = re.compile(
    r"no caveats|without (any )?caveats|single best|just tell me the (single |one )?best|one (single )?answer",
    re.IGNORECASE,
)
_GUARANTEE_RE = re.compile(
    r"\bguarantee|\bguaranteed\b|\bcertain(ty)?\b|\bfor sure\b|\b100%\b|\bpromise\b",
    re.IGNORECASE,
)
_LEGAL_RE = re.compile(
    r"\blegal(ly)?\b|\bllc\b|\bpartnership\b|\blawyer\b|\battorney\b|\bincorporat",
    re.IGNORECASE,
)
_FARES_RE = re.compile(
    r"\bfare[s]?\b|\bprofit|\bconstruction cost|\bcost to build\b|\bfinanc(e|ing)\b|\broi\b"
    r"|\breturn on investment\b|\bcapex\b",
    re.IGNORECASE,
)
_NON_US_KEYWORDS = {
    "heathrow", "gatwick", "london", "paris", "cdg", "charles de gaulle", "frankfurt",
    "dubai", "tokyo", "narita", "haneda", "toronto", "pearson", "sydney", "beijing",
    "shanghai", "singapore", "changi", "amsterdam", "schiphol", "madrid", "rome",
    "fiumicino", "mexico city", "mumbai", "delhi", "hong kong", "seoul", "incheon",
}
_NON_US_RE = re.compile(r"\b(" + "|".join(re.escape(k) for k in _NON_US_KEYWORDS) + r")\b", re.IGNORECASE)


def _is_non_english(message):
    has_latin = bool(re.search(r"[A-Za-z]", message))
    has_nonascii = bool(re.search(r"[^\x00-\x7F]", message))
    return has_nonascii and not has_latin


# --- follow-up keyword tables --------------------------------------------

_ORDINAL_WORDS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "top": 1, "last": -1}
_ORDINAL_RE = re.compile(
    r"\bthe\s+(first|second|third|fourth|fifth|top|last)\s+(?:one|airport)?\b"
    r"|\bnumber\s+(\d+)\b"
    r"|#(\d+)\b",
    re.IGNORECASE,
)
_ADD_TO_RE = re.compile(r"\badd\b.{0,40}\bto\b.{0,20}\b(that |the )?(comparison|list)\b", re.IGNORECASE)
_WHAT_ABOUT_RE = re.compile(r"\bwhat about\b", re.IGNORECASE)
_TIER_FOLLOWUP_RE = re.compile(r"\bfor\s+(large|medium|small|micro)\s+(airports|hubs)\b", re.IGNORECASE)
_WHY_RANK_RE = re.compile(r"\bwhy\s+(did|does|is)\b.{0,40}\brank", re.IGNORECASE)
_RESTATE_RE = re.compile(
    r"how confident (are you|is (that|this))"
    r"|what('?s| is| are) (the |your )?(caveats|assumptions|confidence)"
    r"|what assumptions",
    re.IGNORECASE,
)
_REWEIGHT_RE = re.compile(
    r"matters? more in the ranking|weight.{0,40}(more|higher|heavier)|more weight on|re-?rank.{0,40}weight",
    re.IGNORECASE,
)
_SUPPORTED_PRESET_FACTOR_RE = re.compile(r"\bgrowth\b|\bcongestion\b|\bdelay", re.IGNORECASE)

_SINGLE_CODE_TOOLS = {
    "score_airport", "get_airport_traffic", "get_long_haul_share",
    "get_unmet_demand_breakdown", "get_buildability",
}
_MULTI_CODE_TOOLS = {"compare_airports", "compare_congestion"}
_SCOPE_TOOLS = {"rank_airports", "rank_airports_by_traffic", "sensitivity", "list_region_airports"}


# --- fixed messages -------------------------------------------------------

_CAPABILITIES_LINE = (
    " I can rank airports by a transparent score, compare named airports side by side, "
    "show traffic/growth/congestion data, or break down unmet demand."
)

_REFUSAL_MESSAGES = {
    "injection": "I can't ignore or reveal my instructions." + _CAPABILITIES_LINE,
    "non_english": (
        "This app only supports English right now, so I can't answer in another language."
        + _CAPABILITIES_LINE
    ),
    "guarantee": (
        "I can't guarantee outcomes or give a single certain answer -- I can rank airports by a "
        "transparent, adjustable score instead." + _CAPABILITIES_LINE
    ),
    "legal": "I can't give legal or financial/structuring advice." + _CAPABILITIES_LINE,
    "fares": (
        "Fares, ROI, profit, construction cost, and financing data aren't available in this project."
        + _CAPABILITIES_LINE
    ),
    "non_us": "This project only covers US airports." + _CAPABILITIES_LINE,
    "non_aviation": "That's outside airport-investment research, which is what I can help with." + _CAPABILITIES_LINE,
}


def _refusal(kind):
    return Plan(intent=f"refusal_{kind}", tool=None, args={}, message=_REFUSAL_MESSAGES[kind])


def _clarify_ranking():
    return Plan(
        intent="clarify_scope",
        tool=None,
        args={},
        message=(
            "Which scope should I rank -- a region, a hub tier, or a set of states? "
            'For example: "New England", "large hubs", or "CT and ME".'
        ),
    )


def _help():
    from src.ui.chat_logic import SAMPLE_QUESTIONS

    examples = "\n".join(f"- {q}" for q in SAMPLE_QUESTIONS)
    return Plan(
        intent="help",
        tool=None,
        args={},
        message=(
            "I can help with US airport-investment research questions, for example:\n"
            f"{examples}\n"
            "I can't give forecasts, guarantees, legal/financial advice, fares/ROI/construction-cost "
            "figures, or cover non-US airports."
        ),
    )


# --- follow-up planning ----------------------------------------------------

def _plan_followup(message, text_lower, state, conn):
    """Returns a Plan if `message` is follow-up-shaped, else None (meaning:
    not a follow-up, fall through to normal single-turn planning). A
    follow-up-shaped message with no usable `state` resolves to the help
    message rather than guessing."""

    m = _ORDINAL_RE.search(text_lower)
    if m:
        if state is None or not state.last_ranking_order:
            return _help()
        order = state.last_ranking_order
        if m.group(1):
            idx = _ORDINAL_WORDS[m.group(1)]
        else:
            idx = int(m.group(2) or m.group(3))
        if idx == 0 or abs(idx) > len(order):
            return _help()
        resolved = order[idx - 1] if idx > 0 else order[idx]
        return Plan(intent="score_airport", tool="score_airport", args={"code": resolved})

    if _ADD_TO_RE.search(text_lower):
        if state is None or state.last_tool != "compare_airports":
            return _help()
        new_codes = resolve_airport_codes(message, conn=conn)
        if not new_codes:
            return _help()
        combined = list(state.last_airports or [])
        for c in new_codes:
            if c not in combined:
                combined.append(c)
        return Plan(intent="compare_airports", tool="compare_airports", args={"codes": combined})

    if _TIER_FOLLOWUP_RE.search(text_lower):
        if state is None or state.last_tool not in _SCOPE_TOOLS:
            return _help()
        new_scope = _resolve_scope_states_uppercase(message, _resolve_scope(text_lower))
        if new_scope is None:
            return _help()
        args = dict(state.last_args or {})
        args["scope"] = new_scope
        return Plan(intent=state.last_tool, tool=state.last_tool, args=args)

    if _WHAT_ABOUT_RE.search(text_lower):
        if state is None or state.last_tool is None:
            return _help()
        new_codes = resolve_airport_codes(message, conn=conn)
        if not new_codes:
            return _help()
        if state.last_tool in _SINGLE_CODE_TOOLS:
            args = dict(state.last_args or {})
            args["code"] = new_codes[0]
            return Plan(intent=state.last_tool, tool=state.last_tool, args=args)
        if state.last_tool in _MULTI_CODE_TOOLS:
            combined = list(state.last_airports or [])
            for c in new_codes:
                if c not in combined:
                    combined.append(c)
            args = dict(state.last_args or {})
            args["codes"] = combined
            return Plan(intent=state.last_tool, tool=state.last_tool, args=args)
        return _help()

    if _WHY_RANK_RE.search(text_lower):
        codes = resolve_airport_codes(message, conn=conn)
        if len(codes) >= 2:
            return Plan(intent="compare_airports", tool="compare_airports", args={"codes": codes[:2]})
        return _help()

    if _RESTATE_RE.search(text_lower):
        if state is None or state.last_envelope is None:
            return _help()
        return Plan(intent="restate", tool=None, args={})

    if _REWEIGHT_RE.search(text_lower):
        explicit_scope = _resolve_scope_states_uppercase(message, _resolve_scope(text_lower))
        if explicit_scope is not None:
            # Self-contained scope named in the message itself (e.g. "How
            # robust is the New England ranking ...") -- not a follow-up,
            # let the normal single-turn sensitivity match handle it.
            return None
        if state is None or state.last_scope is None:
            return _help()
        if _SUPPORTED_PRESET_FACTOR_RE.search(text_lower):
            return Plan(intent="sensitivity", tool="sensitivity", args={"scope": state.last_scope})
        return Plan(
            intent="reweight_unsupported",
            tool=None,
            args={},
            message=(
                "The scoring weights are fixed hypotheses, not freely adjustable -- I can run a "
                "sensitivity check across alternative preset weight sets (equal, growth-heavy, "
                "congestion-heavy, drop-congestion) instead."
            ),
        )

    return None


def plan(message, state=None, conn=None):
    """Deterministic router. See module docstring for scope."""
    if not message or not isinstance(message, str):
        return UNKNOWN

    text_lower = message.lower()

    if _INJECTION_RE.search(text_lower):
        return _refusal("injection")
    if _is_non_english(message):
        return _refusal("non_english")

    followup = _plan_followup(message, text_lower, state, conn)
    if followup is not None:
        return followup

    if _SINGLE_ANSWER_RE.search(text_lower) or _GUARANTEE_RE.search(text_lower):
        return _refusal("guarantee")
    if _LEGAL_RE.search(text_lower):
        return _refusal("legal")
    if _FARES_RE.search(text_lower):
        return _refusal("fares")
    if _NON_US_RE.search(text_lower):
        return _refusal("non_us")

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
        return _clarify_ranking()

    if _LIST_REGION_RE.search(text_lower):
        if scope is not None and ("region" in scope or "states" in scope):
            region_or_states = {"region": scope["region"]} if "region" in scope else {"states": scope["states"]}
            return Plan("list_region_airports", "list_region_airports", {"region_or_states": region_or_states})
        return UNKNOWN

    if _SCORE_RE.search(text_lower) and len(codes) == 1:
        return Plan("score_airport", "score_airport", {"code": codes[0]})

    if _TRAFFIC_RE.search(text_lower) and len(codes) == 1:
        return Plan("airport_traffic", "get_airport_traffic", {"code": codes[0]})

    if not _DOMAIN_RE.search(text_lower):
        return _refusal("non_aviation")

    return _help()


# --- eval harness (run as a script: python -m src.agent.rules_router) ------

def _run_eval(path="data/eval/tool_selection_cases.json"):
    import json

    with open(path) as f:
        cases = json.load(f)

    matched, unmatched, out_of_scope = [], [], []
    for case in cases:
        if "previous_turn" in case:
            out_of_scope.append(
                (case["id"], "multi-turn case; needs State wired up by the caller, not evaluable from a bare question")
            )
            continue
        if case["id"].startswith("followup_"):
            out_of_scope.append(
                (case["id"], "follow-up case; covered directly in tests/test_rules_router.py with a prepared State")
            )
            continue

        p = plan(case["question"])
        if not case["expected_tools"]:
            # Refusal/out-of-scope case: "matched" means the router declined
            # with a fixed message and called no tool.
            if p.tool is None and p.message:
                matched.append(case["id"])
            else:
                unmatched.append((case["id"], p.tool))
            continue

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
