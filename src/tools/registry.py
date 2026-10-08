"""
LLM-facing tool registry over src/scoring/score.py.

CLAUDE.md: the LLM only selects tools and narrates results -- it never
computes or asserts a number itself. These are thin wrappers (no new
scoring logic) that expose score_airport/rank_airports/compare_airports/
sensitivity as Anthropic-format tools, each returning the uniform envelope
({result, method, caveats, source, confidence}) as JSON-serializable data.

call_tool() never raises: unknown tool name, schema-invalid args, an
unknown airport code (AirportNotFoundError), or any other error are all
turned into {"error": {"type", "message"}} so a bad LLM tool call can't
crash the agent loop.
"""

import json

from src.cache.accessors import AirportNotFoundError, get_congestion_ttm, get_ttm_totals, validate_airport_code
from src.cache.config import VOLUME_FLOOR_PAX
from src.reference.buildability import get_buildability as _buildability_lookup
from src.reference.envelope import envelope
from src.reference.pax import pax_by_airport_ttm
from src.reference.regions import list_new_england_airports_ttm, region_list
from src.scoring.score import ScoringError, compare_airports, rank_airports, score_airport, sensitivity
from src.scoring.signals import get_growth_ttm, get_load_factor_ttm

_CONF_ORDER = {"low": 0, "medium": 1, "high": 2}

SCOPE_SCHEMA = {
    "type": "object",
    "description": (
        "Which airports to select -- exactly one of region/tier/states. "
        "Does not change any airport's peer group (always its own TTM hub tier)."
    ),
    "properties": {
        "region": {"type": "string", "enum": ["new_england"], "description": "Named region. Only 'new_england' is defined."},
        "tier": {"type": "string", "enum": ["large", "medium", "small", "micro"], "description": "TTM hub tier."},
        "states": {"type": "array", "items": {"type": "string"}, "description": "US state codes, e.g. ['CT', 'ME']."},
    },
    "additionalProperties": False,
}


def _trim_airport_entry(entry):
    """
    Tool-facing copy of one scored-airport dict: peer_group.codes (the full
    peer list, kept internally in src/scoring/score.py for tests) becomes
    {basis, size} -- an LLM only needs the peer-group size, not every code.
    """
    if not isinstance(entry, dict) or "peer_group" not in entry:
        return entry
    peer_group = entry["peer_group"]
    return {
        **entry,
        "peer_group": {"basis": peer_group["basis"], "size": len(peer_group["codes"])},
    }


def _rank_airports(args, conn):
    envelope = rank_airports(args["scope"], top_n=args.get("top_n", 10), conn=conn)
    if envelope["result"] is not None:
        ranked = [_trim_airport_entry(e) for e in envelope["result"]["ranked"]]
        envelope = {**envelope, "result": {**envelope["result"], "ranked": ranked}}
    return envelope


def _score_airport(args, conn):
    envelope = score_airport(args["code"], conn=conn)
    if envelope["result"] is not None:
        envelope = {**envelope, "result": _trim_airport_entry(envelope["result"])}
    return envelope


def _compare_airports(args, conn):
    envelope = compare_airports(args["codes"], conn=conn)
    if envelope["result"] is not None:
        compared = [_trim_airport_entry(e) for e in envelope["result"]["compared"]]
        envelope = {**envelope, "result": {**envelope["result"], "compared": compared}}
    return envelope


def _sensitivity(args, conn):
    return sensitivity(args["scope"], conn=conn)


def _list_region_airports(args, conn):
    scope = args["region_or_states"]
    if "region" in scope:
        r = list_new_england_airports_ttm(conn)
        basis = f"region:{scope['region']}"
    else:
        states = scope["states"]
        pax_by_code, excluded, as_of = pax_by_airport_ttm(conn)
        if as_of is None:
            return envelope(None, "OurAirports intersected with TTM total_passengers", ["cache is empty"], "no cached data", "low")
        r = region_list(
            conn, pax_by_code, VOLUME_FLOOR_PAX,
            f"TTM total_passengers ending {as_of} (excluded {excluded} NULL month-rows)", states=states,
        )
        basis = f"states:{states}"
    if r["result"] is None:
        return r
    return envelope({"scope": basis, "airports": r["result"]}, r["method"], r["caveats"], r["source"], r["confidence"])


def _get_airport_traffic(args, conn):
    code = validate_airport_code(conn, args["code"])
    ttm = get_ttm_totals(conn, code)
    growth = get_growth_ttm(conn, code)
    load_factor = get_load_factor_ttm(conn, code)

    caveats = (
        [f"ttm_totals: {c}" for c in ttm["caveats"]]
        + [f"growth: {c}" for c in growth["caveats"]]
        + [f"load_factor: {c}" for c in load_factor["caveats"]]
    )
    result = {
        "airport": code,
        "as_of": ttm["result"]["as_of"] if ttm["result"] else None,
        "ttm_passengers": ttm["result"]["total_passengers"] if ttm["result"] else None,
        "ttm_seats": ttm["result"]["total_seats"] if ttm["result"] else None,
        "load_factor": load_factor["result"]["load_factor"] if load_factor["result"] else None,
        "growth_pct": growth["result"]["growth_pct"] if growth["result"] else None,
    }
    sub_envelopes = [ttm, growth, load_factor]
    sources = sorted({e["source"] for e in sub_envelopes if e["result"] is not None})
    confidences = [e["confidence"] for e in sub_envelopes if e["result"] is not None]
    confidence = min(confidences, key=lambda c: _CONF_ORDER[c]) if confidences else "low"
    method = (
        "TTM totals (src/cache/accessors.get_ttm_totals) + growth and load_factor "
        "(src/scoring/signals.get_growth_ttm / get_load_factor_ttm) for one airport; thin wrapper, no new calculation"
    )
    return envelope(result, method, caveats, "; ".join(sources) if sources else "no cached data", confidence)


def _compare_congestion(args, conn):
    codes = [validate_airport_code(conn, c) for c in args["codes"]]
    entries = []
    sources = []
    for code in codes:
        cong = get_congestion_ttm(conn, code)
        if cong["result"] is None:
            entries.append({
                "airport": code, "flights": None, "delayed_share_pct": None,
                "coverage_pct": None, "low_coverage": True, "caveats": cong["caveats"],
            })
            continue
        coverage = cong["result"]["coverage"]
        coverage_pct = coverage["coverage_pct"] if coverage else None
        entries.append({
            "airport": code,
            "flights": cong["result"]["flights"],
            "delayed_share_pct": cong["result"]["pct_dep_delay_ge15min"],
            "coverage_pct": coverage_pct,
            "low_coverage": coverage_pct is None or coverage_pct < 50.0,
            "caveats": cong["caveats"],
        })
        sources.append(cong["source"])
    method = (
        "TTM flights and %dep-delay>=15min per airport (src/cache/accessors.get_congestion_ttm), plus "
        "OTP/T-100 coverage; coverage < 50% is returned with low_coverage=true, never hidden"
    )
    confidence = "medium" if any(e["low_coverage"] for e in entries) else "high"
    return envelope(
        {"codes": codes, "compared": entries}, method, [],
        "; ".join(sorted(set(sources))) if sources else "no cached data", confidence,
    )


def _get_buildability(args, conn):
    code = validate_airport_code(conn, args["code"])
    return _buildability_lookup(code)


TOOLS = {
    "rank_airports": {
        "description": (
            "Rank airports in a scope (region/tier/states) by composite investment score, highest "
            "first. Use when the user wants a leaderboard/top-N answer, e.g. 'top airports in New "
            "England' or 'best small-hub airports'. Do NOT use for a single airport's own score "
            "(use score_airport) or for a fixed, named list of airports to put side by side (use "
            "compare_airports)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "scope": SCOPE_SCHEMA,
                "top_n": {"type": "integer", "description": "Max airports to return (default 10)."},
            },
            "required": ["scope"],
            "additionalProperties": False,
        },
        "fn": _rank_airports,
    },
    "score_airport": {
        "description": (
            "Get one airport's composite investment score, signal breakdown, and buildability "
            "constraints, scored against its own TTM hub-tier peers. Use when the user asks about a "
            "single named airport, e.g. 'how does BOS look?'. Do NOT use for comparing several named "
            "airports side by side (use compare_airports) or for a ranked list (use rank_airports)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "IATA airport code, e.g. 'BOS'."},
            },
            "required": ["code"],
            "additionalProperties": False,
        },
        "fn": _score_airport,
    },
    "compare_airports": {
        "description": (
            "Side-by-side composite scores for a fixed, named list of airports, e.g. 'compare BOS, "
            "PWM and MHT'. Each airport is still scored against its own TTM hub tier, not against the "
            "others in the list. Do NOT use for an open-ended 'top N in scope X' question (use "
            "rank_airports)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "codes": {"type": "array", "items": {"type": "string"}, "description": "IATA airport codes to compare."},
            },
            "required": ["codes"],
            "additionalProperties": False,
        },
        "fn": _compare_airports,
    },
    "sensitivity": {
        "description": (
            "Re-rank a scope under several alternative weight sets (equal, growth-heavy, "
            "congestion-heavy, drop-congestion) to show how much a ranking depends on the analyst's "
            "weighting hypothesis. Use when the user asks 'how robust is this ranking' or 'does the "
            "answer change if we weight X differently'. Do NOT use for a plain single-weighting "
            "ranking (use rank_airports)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "scope": SCOPE_SCHEMA,
            },
            "required": ["scope"],
            "additionalProperties": False,
        },
        "fn": _sensitivity,
    },
    "list_region_airports": {
        "description": (
            "List airports (code, name, state, TTM passengers) in a named region or a set of US states, "
            "above the volume floor. Use to resolve 'which airports are in New England' or 'which airports "
            "are in CT and ME' before asking about them individually. Does not score or rank them."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "region_or_states": {
                    "type": "object",
                    "description": "Exactly one of region/states.",
                    "properties": {
                        "region": {"type": "string", "enum": ["new_england"]},
                        "states": {"type": "array", "items": {"type": "string"}, "description": "US state codes, e.g. ['CT', 'ME']."},
                    },
                    "additionalProperties": False,
                },
            },
            "required": ["region_or_states"],
            "additionalProperties": False,
        },
        "fn": _list_region_airports,
    },
    "get_airport_traffic": {
        "description": (
            "TTM passengers, seats, load factor, and passenger growth % for one airport, with its as_of "
            "window. Use for raw traffic-volume questions, e.g. 'how many passengers does BOS carry?'. Do "
            "NOT use for an investment score (use score_airport) or for congestion/delays (use compare_congestion)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "IATA airport code, e.g. 'BOS'."},
            },
            "required": ["code"],
            "additionalProperties": False,
        },
        "fn": _get_airport_traffic,
    },
    "compare_congestion": {
        "description": (
            "TTM flight counts and % of departures delayed >=15min for a list of named airports, plus the "
            "OTP/T-100 coverage behind that delay share. If coverage is below 50% the number is still "
            "returned, flagged with low_coverage=true -- never hidden. Use for 'is SFO more congested than "
            "LAX?' style questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "codes": {"type": "array", "items": {"type": "string"}, "description": "IATA airport codes to compare."},
            },
            "required": ["codes"],
            "additionalProperties": False,
        },
        "fn": _compare_congestion,
    },
    "get_buildability": {
        "description": (
            "Curated capacity/slot/perimeter constraints on file for one airport (data/reference/"
            "buildability.json), with verified_by_user / needs_verification shown exactly as recorded. An "
            "airport with no entry returns 'no constraints on file' -- that means not researched, NOT that "
            "the airport is unconstrained."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "IATA airport code, e.g. 'SNA'."},
            },
            "required": ["code"],
            "additionalProperties": False,
        },
        "fn": _get_buildability,
    },
}


def get_tool_specs():
    """Anthropic tool-use format: [{name, description, input_schema}, ...]."""
    return [
        {"name": name, "description": spec["description"], "input_schema": spec["input_schema"]}
        for name, spec in TOOLS.items()
    ]


class SchemaError(Exception):
    pass


def _validate_type(value, expected_type, path):
    pytype = {"object": dict, "array": list, "string": str, "integer": int, "number": (int, float), "boolean": bool}[expected_type]
    if expected_type == "integer" and isinstance(value, bool):
        raise SchemaError(f"{path}: expected integer, got boolean")
    if not isinstance(value, pytype):
        raise SchemaError(f"{path}: expected {expected_type}, got {type(value).__name__}")


def _validate(value, schema, path="args"):
    _validate_type(value, schema["type"], path)
    if "enum" in schema and value not in schema["enum"]:
        raise SchemaError(f"{path}: must be one of {schema['enum']}, got {value!r}")
    if schema["type"] == "object":
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                raise SchemaError(f"{path}: missing required field {key!r}")
        if schema.get("additionalProperties") is False:
            unknown = set(value) - set(properties)
            if unknown:
                raise SchemaError(f"{path}: unknown field(s) {sorted(unknown)}")
        for key, subschema in properties.items():
            if key in value:
                _validate(value[key], subschema, f"{path}.{key}")
    elif schema["type"] == "array":
        item_schema = schema.get("items")
        if item_schema is not None:
            for i, item in enumerate(value):
                _validate(item, item_schema, f"{path}[{i}]")


def validate_args(name, args):
    """Raises SchemaError if `args` doesn't conform to tool `name`'s input_schema."""
    spec = TOOLS[name]
    _validate(args if args is not None else {}, spec["input_schema"])


def call_tool(name, args, conn=None):
    """
    Runs tool `name` with `args` (a dict), returning the uniform envelope as
    JSON-serializable data. Never raises: unknown tool, schema-invalid args,
    unknown airport code, or any other error all come back as
    {"error": {"type", "message"}} instead.

    `conn`: optional existing db connection (e.g. a test fixture db). If
    omitted, the underlying scoring call opens/closes data/cache.db itself.
    """
    try:
        if name not in TOOLS:
            return {"error": {"type": "UnknownToolError", "message": f"no such tool {name!r}; known tools: {sorted(TOOLS)}"}}
        args = args or {}
        try:
            validate_args(name, args)
        except SchemaError as e:
            return {"error": {"type": "SchemaError", "message": str(e)}}

        result = TOOLS[name]["fn"](args, conn)
        json.dumps(result)  # fail loudly here (caught below) rather than return something non-serializable
        return result
    except AirportNotFoundError as e:
        return {"error": {"type": "AirportNotFoundError", "message": str(e)}}
    except ScoringError as e:
        return {"error": {"type": "ScoringError", "message": str(e)}}
    except Exception as e:
        return {"error": {"type": type(e).__name__, "message": str(e)}}
