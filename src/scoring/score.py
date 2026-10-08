"""
Composite airport investment score. See docs/SCORING.md for the full
methodology (weighting rationale, renormalization rule, confidence rules).

CLAUDE.md: the LLM only narrates these numbers, it never computes or asserts
one itself. Composite = weighted mean of the available per-signal robust
z-scores (src/scoring/normalize.py over src/scoring/signals.py), weights
named in src/scoring/weights.py, renormalized over whichever signals are
actually available for that airport. Buildability
(data/reference/buildability.json) is a separate, curated flag -- it is
never folded into the composite number.
"""

import json
import os

from src.cache import db
from src.cache.config import REFERENCE_DIR
from src.reference.envelope import envelope
from src.scoring.normalize import robust_z_scores
from src.scoring.signals import compute_signals, get_peer_group_ttm
from src.scoring.weights import HYPOTHESES, SENSITIVITY_WEIGHT_SETS, SIGNAL_NAMES, validate_weights

MIN_SIGNALS_FOR_SCORE = 3
BUILDABILITY_PATH = os.path.join(REFERENCE_DIR, "buildability.json")

SIGNAL_RESULT_KEYS = {
    "growth": "growth_pct",
    "load_factor": "load_factor",
    "demand_supply_gap": "demand_supply_gap_pct",
    "congestion": "congestion_pct",
}

_CONF_ORDER = {"low": 0, "medium": 1, "high": 2}


class ScoringError(Exception):
    pass


def _open_conn(conn):
    """Returns (conn, owns_it) -- opens data/cache.db if the caller didn't pass a connection."""
    if conn is not None:
        return conn, False
    return db.connect(), True


def _raw_value(signal_envelope, signal_name):
    if signal_envelope["result"] is None:
        return None
    return signal_envelope["result"][SIGNAL_RESULT_KEYS[signal_name]]


def _signals_for_group(conn, codes, end_month):
    """{code: {signal_name: envelope}} for every code -- each signal stays its own envelope, never imputed."""
    return {code: compute_signals(conn, code, end_month) for code in codes}


def _z_scores_for_group(signals_by_code):
    """{signal_name: robust_z_scores envelope}, computed once across the whole peer group."""
    z_by_signal = {}
    for name in SIGNAL_NAMES:
        values = {code: _raw_value(sigs[name], name) for code, sigs in signals_by_code.items()}
        z_by_signal[name] = robust_z_scores(values)
    return z_by_signal


def _buildability_flag(code):
    """Curated constraint flag for `code`, kept entirely separate from the score itself."""
    if not os.path.exists(BUILDABILITY_PATH):
        return {"has_constraints": None, "constraints": [], "note": "buildability.json not found"}
    with open(BUILDABILITY_PATH) as f:
        data = json.load(f)
    airport = data.get("airports", {}).get(code)
    if airport is None:
        return {"has_constraints": False, "constraints": [], "note": "no curated buildability constraints for this airport"}
    return {"has_constraints": True, "constraints": airport["constraints"], "note": airport.get("name")}


def _compose(code, signals, z_by_signal, weights):
    """
    Weighted mean of the z-scores available for `code`, renormalized over
    whichever signals actually have both a raw value and a peer-group
    z-score. Returns (per_signal, status, caveats, composite, available).
    """
    per_signal = {}
    caveats = []
    available = []
    for name in SIGNAL_NAMES:
        z_env = z_by_signal[name]
        raw = _raw_value(signals[name], name)
        z = z_env["result"].get(code) if z_env["result"] is not None else None
        per_signal[name] = {
            "raw": raw, "z": z, "weight": weights[name],
            "effective_weight": None, "contribution": None,
            "caveats": signals[name]["caveats"],
        }
        if z is not None:
            available.append(name)
        else:
            reason = "signal not computable for this airport" if raw is None else "no peer-group z-score for this signal"
            caveats.append(f"{name}: excluded from composite -- {reason}")

    if len(available) < MIN_SIGNALS_FOR_SCORE:
        caveats.append(
            f"only {len(available)}/{len(SIGNAL_NAMES)} signal(s) available "
            f"(need >= {MIN_SIGNALS_FOR_SCORE}) -- insufficient data for a composite score"
        )
        return per_signal, "insufficient data", caveats, None, []

    weight_sum = sum(weights[name] for name in available)
    if weight_sum <= 0:
        caveats.append(
            f"the {len(available)} available signal(s) {available} carry zero total weight in this weight set "
            "-- cannot renormalize, insufficient data for a composite score"
        )
        return per_signal, "insufficient data", caveats, None, []

    composite = 0.0
    for name in available:
        eff_weight = weights[name] / weight_sum
        contribution = round(eff_weight * per_signal[name]["z"], 4)
        per_signal[name]["effective_weight"] = round(eff_weight, 4)
        per_signal[name]["contribution"] = contribution
        composite += contribution
    composite = round(composite, 4)

    if len(available) < len(SIGNAL_NAMES):
        dropped = sorted(set(SIGNAL_NAMES) - set(available))
        caveats.append(
            f"renormalized weights over {len(available)}/{len(SIGNAL_NAMES)} available signals "
            f"(dropped {dropped}) -- weight/contribution above already reflect the renormalized weight"
        )

    return per_signal, "scored", caveats, composite, available


def _confidence(available, signals, z_by_signal):
    """
    Base confidence from signal count (high=4, medium=3; <3 is handled as
    "insufficient data" before this is called), then lowered if any signal
    actually used has short underlying data or sits in a small/degenerate
    peer group -- see docs/SCORING.md "Confidence rules".
    """
    base = "high" if len(available) == len(SIGNAL_NAMES) else "medium"
    worst = _CONF_ORDER[base]
    reasons = []
    for name in available:
        sig_conf = signals[name]["confidence"]
        z_conf = z_by_signal[name]["confidence"]
        for label, conf in (("signal", sig_conf), ("peer-group z-score", z_conf)):
            if _CONF_ORDER[conf] < _CONF_ORDER[base]:
                reasons.append(f"{name} {label} confidence is {conf}")
            worst = min(worst, _CONF_ORDER[conf])
    inv = {v: k for k, v in _CONF_ORDER.items()}
    return inv[worst], reasons


def _why_list(per_signal, available):
    """[{signal, raw, z, weight, contribution}], ordered by |contribution| descending."""
    entries = [
        {
            "signal": name, "raw": per_signal[name]["raw"], "z": per_signal[name]["z"],
            "weight": per_signal[name]["effective_weight"], "contribution": per_signal[name]["contribution"],
        }
        for name in available
    ]
    entries.sort(key=lambda e: -abs(e["contribution"]))
    return entries


def _tiered_group_data(conn, codes, end_month):
    """
    For every code, resolves its OWN TTM hub-tier peer group -- z-scoring
    must depend only on an airport's real statistical peers, never on which
    other airports a caller happens to be displaying together (a 3-airport
    rank_airports()/compare_airports() call must not shrink anyone's peer
    group to 3). Tier peer groups are computed once and shared by every code
    that lands in the same tier; a code with no TTM tier falls back to a
    peer group of itself only.

    Returns {code: {"signals": {...per-signal envelopes for code...},
    "z_by_signal": {...robust_z_scores envelopes over the code's own peer
    group...}, "tier": tier_name_or_None, "peer_codes": [...],
    "peer_caveats": [...]}}.
    """
    tier_cache = {}
    out = {}
    for raw_code in codes:
        code = raw_code.strip().upper()
        peer_group = get_peer_group_ttm(conn, code, end_month)
        if peer_group["result"] is not None:
            tier_name = peer_group["result"]["tier"]
            peer_codes = peer_group["result"]["peer_codes"]
            peer_caveats = list(peer_group["caveats"])
            cache_key = ("tier", tier_name)
        else:
            tier_name = None
            peer_codes = [code]
            peer_caveats = list(peer_group["caveats"]) + ["no TTM hub-tier peer group found -- scoring against itself only"]
            cache_key = ("self", code)

        if cache_key not in tier_cache:
            signals_by_code = _signals_for_group(conn, peer_codes, end_month)
            tier_cache[cache_key] = (signals_by_code, _z_scores_for_group(signals_by_code))
        signals_by_code, z_by_signal = tier_cache[cache_key]

        out[code] = {
            "signals": signals_by_code[code], "z_by_signal": z_by_signal,
            "tier": tier_name, "peer_codes": peer_codes, "peer_caveats": peer_caveats,
        }
    return out


def _score_one(code, entry, weights):
    """Composes one code's score from its pre-resolved tiered signals/z-scores (see _tiered_group_data)."""
    per_signal, status, caveats, composite, available = _compose(code, entry["signals"], entry["z_by_signal"], weights)
    confidence, downgrade_reasons = (
        _confidence(available, entry["signals"], entry["z_by_signal"]) if status == "scored" else ("low", [])
    )
    sources = sorted({entry["signals"][n]["source"] for n in SIGNAL_NAMES if entry["signals"][n]["result"] is not None})
    return {
        "airport": code,
        "tier": entry["tier"],
        "status": status,
        "composite_score": composite,
        "confidence": confidence,
        "signals": per_signal,
        "why": _why_list(per_signal, available) if status == "scored" else [],
        "buildability": _buildability_flag(code),
        "peer_group": {"basis": entry["tier"], "codes": entry["peer_codes"]},
        "caveats": caveats + downgrade_reasons + entry["peer_caveats"],
        "source": "; ".join(sources) if sources else "no cached data",
    }


def score_airport(code, weights=None, conn=None, end_month=None):
    """
    Score one airport against its TTM hub-tier peer group (large/medium/
    small/micro, see src/scoring/signals.get_peer_group_ttm). Falls back to a
    peer group of just itself (and therefore "insufficient data", peer group
    too small) if it isn't classified into any TTM hub tier.
    """
    weights = weights or HYPOTHESES
    validate_weights(weights)
    conn, owns_conn = _open_conn(conn)
    try:
        code = code.strip().upper()
        entry = _tiered_group_data(conn, [code], end_month)[code]
        r = _score_one(code, entry, weights)
        method = (
            "composite = weighted mean of available robust z-scores (src/scoring/normalize.py) "
            "over {growth, load_factor, demand_supply_gap, congestion}, weights from "
            "src/scoring/weights.py renormalized over available signals; peer group = the airport's "
            "own TTM hub tier; buildability is a separate flag, not part of the score"
        )
        result = {k: v for k, v in r.items() if k not in ("caveats", "source")}
        return envelope(result, method, r["caveats"], r["source"], r["confidence"])
    finally:
        if owns_conn:
            conn.close()


# ---------------------------------------------------------------------------
# Scope resolution (shared by rank_airports and sensitivity)
# ---------------------------------------------------------------------------

def _resolve_scope(conn, scope, end_month):
    """
    scope: {"region": "new_england"} | {"tier": "large"|"medium"|"small"|"micro"} |
    {"states": ["CT", "ME", ...]}. Returns (codes, basis_label, caveats, source).
    """
    from src.cache.config import VOLUME_FLOOR_PAX
    from src.reference.hub_tiers import compute_hub_tiers_ttm
    from src.reference.pax import pax_by_airport_ttm
    from src.reference.regions import list_new_england_airports_ttm, region_list

    if not isinstance(scope, dict) or len(scope) != 1:
        raise ScoringError(f"scope must be a single-key dict, one of 'region'/'tier'/'states', got {scope!r}")
    (kind, value), = scope.items()

    if kind == "region":
        if value != "new_england":
            raise ScoringError(f"unsupported region {value!r} -- only 'new_england' is defined")
        r = list_new_england_airports_ttm(conn, end_month)
        if r["result"] is None:
            return [], f"region:{value}", r["caveats"], r["source"]
        return [a["code"] for a in r["result"]], f"region:{value}", r["caveats"], r["source"]

    if kind == "tier":
        if value not in ("large", "medium", "small", "micro"):
            raise ScoringError(f"tier must be 'large'/'medium'/'small'/'micro', got {value!r}")
        r = compute_hub_tiers_ttm(conn, end_month)
        if r["result"] is None:
            return [], f"tier:{value}", r["caveats"], r["source"]
        codes = [m["code"] for m in r["result"]["tiers"][value]]
        return codes, f"tier:{value}", r["caveats"], r["source"]

    if kind == "states":
        pax_by_code, excluded, as_of = pax_by_airport_ttm(conn, end_month)
        if as_of is None:
            return [], f"states:{value}", ["cache is empty"], "no cached data"
        r = region_list(conn, pax_by_code, VOLUME_FLOOR_PAX, f"TTM total_passengers ending {as_of}", states=value)
        if r["result"] is None:
            return [], f"states:{value}", r["caveats"], r["source"]
        return [a["code"] for a in r["result"]], f"states:{value}", r["caveats"], r["source"]

    raise ScoringError(f"scope key must be 'region', 'tier' or 'states', got {kind!r}")


def rank_airports(scope, weights=None, top_n=10, conn=None, end_month=None):
    """
    Ranks every airport in `scope` (see _resolve_scope) by composite score,
    highest first. `scope` only selects which airports to display/rank --
    each airport's z-scores are computed against its OWN TTM hub tier, not
    against the other members of `scope` (see _tiered_group_data). Airports
    in different tiers can appear side by side in the same ranking; each was
    scored against its real peers.
    """
    weights = weights or HYPOTHESES
    validate_weights(weights)
    conn, owns_conn = _open_conn(conn)
    try:
        codes, basis, scope_caveats, scope_source = _resolve_scope(conn, scope, end_month)
        if not codes:
            return envelope(None, "rank airports in scope by composite score", scope_caveats, scope_source, "low")

        tiered = _tiered_group_data(conn, codes, end_month)
        results = {code: _score_one(code, entry, weights) for code, entry in tiered.items()}
        scored = sorted(
            (r for r in results.values() if r["status"] == "scored"),
            key=lambda r: (-r["composite_score"], r["airport"]),
        )
        unscored = sorted((r for r in results.values() if r["status"] != "scored"), key=lambda r: r["airport"])
        ranked = []
        for i, r in enumerate(scored, start=1):
            ranked.append({**r, "rank": i})
        for r in unscored:
            ranked.append({**r, "rank": None})
        ranked = ranked[:top_n]

        method = (
            f"rank {basis} airports by composite score; scope only selects/displays -- each airport's "
            "z-scores are computed against its own TTM hub tier, not against the other scope members"
        )
        caveats = list(scope_caveats)
        if unscored:
            caveats.append(f"{len(unscored)}/{len(codes)} airport(s) in scope have insufficient data for a composite score")
        return envelope(
            {"scope": basis, "codes": codes, "ranked": ranked}, method, caveats, scope_source,
            "high" if not unscored else "medium",
        )
    finally:
        if owns_conn:
            conn.close()


def compare_airports(codes, weights=None, conn=None, end_month=None):
    """
    Side-by-side composite scores for exactly the given codes. `codes` only
    selects which airports to display -- each one is scored against its OWN
    TTM hub tier (see _tiered_group_data), so e.g. comparing a large-tier and
    a medium-tier airport does NOT shrink either one's peer group down to the
    size of this list.
    """
    weights = weights or HYPOTHESES
    validate_weights(weights)
    if not codes:
        raise ScoringError("compare_airports requires at least one code")
    codes = [c.strip().upper() for c in codes]
    conn, owns_conn = _open_conn(conn)
    try:
        tiered = _tiered_group_data(conn, codes, end_month)
        ordered = [_score_one(code, tiered[code], weights) for code in codes]
        method = (
            "side-by-side composite score for the given airports; each is scored against its own "
            "TTM hub tier, not against the other airports in this list"
        )
        sources = sorted({r["source"] for r in ordered if r["source"] != "no cached data"})
        return envelope(
            {"codes": codes, "compared": ordered}, method, [],
            "; ".join(sources) if sources else "no cached data",
            "high" if all(r["status"] == "scored" for r in ordered) else "medium",
        )
    finally:
        if owns_conn:
            conn.close()


def sensitivity(scope, weight_sets=None, conn=None, end_month=None):
    """
    Re-ranks `scope` under every weight set in `weight_sets` (default:
    src/scoring/weights.SENSITIVITY_WEIGHT_SETS -- the production weights
    plus 4 alternatives: equal, growth-heavy, congestion-heavy, drop-
    congestion). Reports each airport's rank range across sets and which
    top-3 members are stable in every set. Each airport's signals/z-scores
    are computed once against its OWN TTM hub tier (see
    _tiered_group_data -- `scope` only selects/displays) and reused across
    weight sets; only the composite and ranking change per set.
    """
    weight_sets = weight_sets or SENSITIVITY_WEIGHT_SETS
    for name, w in weight_sets.items():
        validate_weights(w)
    conn, owns_conn = _open_conn(conn)
    try:
        codes, basis, scope_caveats, scope_source = _resolve_scope(conn, scope, end_month)
        if not codes:
            return envelope(None, "sensitivity of ranking to weight set", scope_caveats, scope_source, "low")

        tiered = _tiered_group_data(conn, codes, end_month)

        rankings = {}
        rank_by_code = {code: {} for code in tiered}
        for set_name, weights in weight_sets.items():
            composites = {}
            for code, entry in tiered.items():
                _, status, _, composite, _ = _compose(code, entry["signals"], entry["z_by_signal"], weights)
                if status == "scored":
                    composites[code] = composite
            ordered = sorted(composites, key=lambda c: (-composites[c], c))
            rankings[set_name] = [{"airport": c, "rank": i, "composite_score": composites[c]} for i, c in enumerate(ordered, start=1)]
            for i, c in enumerate(ordered, start=1):
                rank_by_code[c][set_name] = i

        rank_range = {}
        for code, ranks in rank_by_code.items():
            if ranks:
                rank_range[code] = {"min_rank": min(ranks.values()), "max_rank": max(ranks.values()), "n_sets_scored": len(ranks)}
            else:
                rank_range[code] = {"min_rank": None, "max_rank": None, "n_sets_scored": 0}

        top3_by_set = {name: {e["airport"] for e in entries[:3]} for name, entries in rankings.items()}
        stable_top3 = sorted(set.intersection(*top3_by_set.values())) if top3_by_set else []

        method = (
            f"re-rank {basis} airports under {len(weight_sets)} weight set(s) "
            f"({sorted(weight_sets)}); signals/z-scores shared across sets, only the composite changes"
        )
        caveats = list(scope_caveats)
        not_always_scored = [c for c, r in rank_range.items() if r["n_sets_scored"] < len(weight_sets)]
        if not_always_scored:
            caveats.append(
                f"{len(not_always_scored)} airport(s) lack a composite score under at least one weight set "
                f"(a weight set can zero out the only available signal): {sorted(not_always_scored)}"
            )
        return envelope(
            {
                "scope": basis, "weight_sets": weight_sets, "rankings": rankings,
                "rank_range": rank_range, "stable_top3": stable_top3,
            },
            method, caveats, scope_source, "medium",
        )
    finally:
        if owns_conn:
            conn.close()
