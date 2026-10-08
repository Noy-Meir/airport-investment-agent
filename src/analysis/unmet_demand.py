"""
Unmet-demand decomposition for one airport.

CLAUDE.md: no imputation, no unsourced facts. There is no public BTS/FAA/
OurAirports dataset that measures unmet demand (travelers who didn't fly,
fares, diversion, physical constraints) -- so this module never outputs a
single "unmet demand" number or a count of unserved people. Instead it
returns three buckets:
  - measured: facts computed from cached data, each its own small envelope
    ({name, value, unit, as_of, source, peer_context}), with value=None and
    a reason when a fact can't be computed (never imputed).
  - unknown: a fixed list (src/analysis/unmet_demand_constants.UNKNOWABLE) of
    what public data structurally cannot tell us here.
  - inferred: empty for now -- filled in a later step.
"""

from src.analysis.unmet_demand_constants import HYPOTHESES, LOAD_FACTOR_HIGH, UNKNOWABLE
from src.cache.accessors import validate_airport_code
from src.reference.envelope import envelope
from src.reference.pax import get_latest_cached_month, trailing_12_window
from src.scoring.normalize import robust_z_scores
from src.scoring.signals import (
    _resolve_end_month,
    get_congestion_signal_ttm,
    get_demand_supply_gap_ttm,
    get_load_factor_ttm,
    get_peer_group_ttm,
)


def _fact(name, value, unit, as_of, source, peer_context=None, reason=None, caveats=None):
    f = {"name": name, "value": value, "unit": unit, "as_of": as_of, "source": source, "peer_context": peer_context}
    if reason is not None:
        f["reason"] = reason
    if caveats:
        f["caveats"] = caveats
    return f


def _peer_context_for_metric(conn, code, end_month, raw_value, get_metric, extract_value):
    """
    z-score and percentile of `code`'s TTM metric within its own TTM hub
    tier. `get_metric(conn, peer_code, end_month)` is one of the TTM signal
    getters in src/scoring/signals.py; `extract_value(result)` pulls the
    scalar out of that signal's result dict.
    """
    peer_group = get_peer_group_ttm(conn, code, end_month)
    if peer_group["result"] is None:
        return None, peer_group["caveats"]

    tier = peer_group["result"]["tier"]
    peer_codes = peer_group["result"]["peer_codes"]
    values = {}
    for peer_code in peer_codes:
        metric = get_metric(conn, peer_code, end_month)
        values[peer_code] = extract_value(metric["result"]) if metric["result"] is not None else None

    z_env = robust_z_scores(values)
    z = z_env["result"].get(code) if z_env["result"] is not None else None

    present = sorted(v for v in values.values() if v is not None)
    percentile = None
    if raw_value is not None and present:
        n_le = sum(1 for v in present if v <= raw_value)
        percentile = round(100.0 * n_le / len(present), 1)

    peer_context = {"tier": tier, "z": z, "percentile": percentile, "peer_group_size": len(present)}
    return peer_context, peer_group["caveats"] + z_env["caveats"]


def _monthly_load_factors_ttm(conn, code, end_year, end_m):
    """[(as_of_str, load_factor), ...] for TTM months with non-NULL, non-zero total_seats."""
    window = trailing_12_window(end_year, end_m)
    ym_values = [y * 100 + m for y, m in window]
    placeholders = ",".join("?" for _ in ym_values)
    rows = conn.execute(
        f"""
        SELECT year, month, total_passengers, total_seats
        FROM source_a_airport_month
        WHERE origin_airport_code = ? AND (year * 100 + month) IN ({placeholders})
        """,
        (code, *ym_values),
    ).fetchall()
    out = []
    for r in rows:
        if r["total_passengers"] is None or r["total_seats"] in (None, 0):
            continue
        out.append((f"{r['year']:04d}-{r['month']:02d}", round(r["total_passengers"] / r["total_seats"], 4)))
    return sorted(out)


def _load_factor_facts(conn, code, end_month):
    lf = get_load_factor_ttm(conn, code, end_month)
    as_of = lf["result"]["as_of"] if lf["result"] is not None else None
    raw_value = lf["result"]["load_factor"] if lf["result"] is not None else None

    if lf["result"] is None:
        ttm_fact = _fact(
            "ttm_load_factor", None, "ratio", as_of, lf["source"],
            reason="; ".join(lf["caveats"]) or "load_factor not computable",
        )
    else:
        peer_context, peer_caveats = _peer_context_for_metric(
            conn, code, end_month, raw_value, get_load_factor_ttm, lambda r: r["load_factor"]
        )
        ttm_fact = _fact(
            "ttm_load_factor", raw_value, "ratio", as_of, lf["source"],
            peer_context=peer_context, caveats=lf["caveats"] + peer_caveats,
        )

    end = _resolve_end_month(conn, end_month)
    if end is None:
        no_data_reason = "cache is empty"
        peak_fact = _fact("peak_month_load_factor", None, "ratio", None, lf["source"], reason=no_data_reason)
        high_months_fact = _fact("months_load_factor_ge_threshold", None, "months", None, lf["source"], reason=no_data_reason)
        return ttm_fact, peak_fact, high_months_fact

    end_year, end_m = end
    monthly = _monthly_load_factors_ttm(conn, code, end_year, end_m)
    window_as_of = f"{end_year:04d}-{end_m:02d}"
    if not monthly:
        reason = "no month in the TTM window has both non-NULL total_passengers and non-NULL/non-zero total_seats"
        peak_fact = _fact("peak_month_load_factor", None, "ratio", window_as_of, lf["source"], reason=reason)
        high_months_fact = _fact("months_load_factor_ge_threshold", None, "months", window_as_of, lf["source"], reason=reason)
        return ttm_fact, peak_fact, high_months_fact

    peak_month_as_of, peak_value = max(monthly, key=lambda t: t[1])
    peak_fact = _fact(
        "peak_month_load_factor", peak_value, "ratio", peak_month_as_of, lf["source"],
        caveats=[f"peak of {len(monthly)}/12 TTM month(s) with a computable monthly load factor"],
    )

    n_high = sum(1 for _, v in monthly if v >= LOAD_FACTOR_HIGH)
    high_months_fact = _fact(
        "months_load_factor_ge_threshold", n_high, "months", window_as_of, lf["source"],
        caveats=[
            f"threshold LOAD_FACTOR_HIGH={LOAD_FACTOR_HIGH} is an analyst hypothesis, not a measured/derived value",
            f"out of {len(monthly)}/12 TTM month(s) with a computable monthly load factor",
        ],
    )
    return ttm_fact, peak_fact, high_months_fact


def _growth_gap_fact(conn, code, end_month):
    gap = get_demand_supply_gap_ttm(conn, code, end_month)
    if gap["result"] is None:
        return _fact(
            "ttm_passenger_vs_seat_growth_gap", None, "percentage_points", None, gap["source"],
            reason="; ".join(gap["caveats"]) or "demand_supply_gap not computable",
        )
    r = gap["result"]
    peer_context, peer_caveats = _peer_context_for_metric(
        conn, code, end_month, r["demand_supply_gap_pct"], get_demand_supply_gap_ttm,
        lambda res: res["demand_supply_gap_pct"],
    )
    return _fact(
        "ttm_passenger_vs_seat_growth_gap", r["demand_supply_gap_pct"], "percentage_points", r["as_of"],
        gap["source"], peer_context=peer_context,
        caveats=gap["caveats"] + peer_caveats + [
            f"passenger_growth_pct={r['passenger_growth_pct']}, seat_growth_pct={r['seat_growth_pct']}",
        ],
    )


def _delayed_share_fact(conn, code, end_month):
    congestion = get_congestion_signal_ttm(conn, code, end_month)
    if congestion["result"] is None:
        return _fact(
            "ttm_delayed_share", None, "percent", end_month and f"{end_month[0]:04d}-{end_month[1]:02d}",
            congestion["source"], reason="; ".join(congestion["caveats"]) or "congestion signal not computable",
        )
    r = congestion["result"]
    peer_context, peer_caveats = _peer_context_for_metric(
        conn, code, end_month, r["congestion_pct"], get_congestion_signal_ttm, lambda res: res["congestion_pct"]
    )
    return _fact(
        "ttm_delayed_share", r["congestion_pct"], "percent", r["as_of"], congestion["source"],
        peer_context=peer_context,
        caveats=congestion["caveats"] + peer_caveats + [f"OTP/T-100 coverage over this window: {r['coverage_pct']}%"],
    )


def _z(fact):
    """peer_context z-score for a measured fact, or None if unmeasurable/no peer group."""
    peer_context = fact.get("peer_context")
    return peer_context.get("z") if peer_context else None


def _capacity_pressure_inference(lf_fact, gap_fact):
    """
    Peer-relative z's are not enough on their own: a gap z can be high purely
    because peers' gaps are very low/negative, even while this airport's own
    raw demand_supply_gap is <= 0 (seat growth keeping pace with or
    outrunning passenger growth). Demand only "outpaces capacity" in
    absolute terms when the raw gap is positive, so that is required too.
    """
    lf_z, gap_z = _z(lf_fact), _z(gap_fact)
    raw_gap = gap_fact["value"]
    if lf_z is None or gap_z is None or raw_gap is None:
        return None
    if (
        lf_z >= HYPOTHESES["capacity_pressure_load_factor_z"]
        and gap_z >= HYPOTHESES["capacity_pressure_gap_z"]
        and raw_gap > 0
    ):
        return {
            "statement": (
                f"a load factor high relative to hub-tier peers (raw value {lf_fact['value']}), together with a "
                f"passenger-vs-seat growth gap that is both high relative to peers and positive in absolute "
                f"terms (raw value {raw_gap} percentage points, i.e. passenger growth actually exceeding seat "
                "growth), is consistent with capacity pressure at this airport"
            ),
            "rule": (
                f"load_factor z >= {HYPOTHESES['capacity_pressure_load_factor_z']} AND "
                f"demand_supply_gap z >= {HYPOTHESES['capacity_pressure_gap_z']} AND "
                "raw demand_supply_gap_pct > 0 (within hub tier)"
            ),
            "based_on": ["ttm_load_factor", "ttm_passenger_vs_seat_growth_gap"],
            "strength": "moderate",
            "alternative_explanations": ["seasonality", "airline schedule changes"],
        }
    return None


def _delay_linked_strain_inference(delayed_fact):
    delayed_z = _z(delayed_fact)
    if delayed_fact["value"] is None or delayed_z is None:
        return None
    if delayed_z >= HYPOTHESES["delay_linked_strain_delayed_share_z"]:
        return {
            "statement": (
                "a delayed-departure share elevated relative to hub-tier peers, measured with adequate "
                "OTP/T-100 coverage, is consistent with delay-linked operational strain"
            ),
            "rule": (
                f"congestion coverage >= 50% (required for ttm_delayed_share to have a value) AND "
                f"delayed_share z >= {HYPOTHESES['delay_linked_strain_delayed_share_z']} (within hub tier)"
            ),
            "based_on": ["ttm_delayed_share"],
            "strength": "moderate",
            "alternative_explanations": ["weather", "a single hub carrier's operations"],
        }
    return None


def _high_utilization_balanced_growth_inference(lf_fact, gap_fact):
    """
    Fires whenever capacity-pressure's growth-outpacing-capacity condition
    does not hold even though the airport is highly utilized: either the
    gap is not high relative to peers, or (regardless of the peer-relative
    z) the raw gap is <= 0, meaning passenger growth is not actually
    outrunning seat growth in absolute terms.
    """
    lf_z, gap_z = _z(lf_fact), _z(gap_fact)
    raw_gap = gap_fact["value"]
    if lf_z is None or gap_z is None or raw_gap is None:
        return None
    if lf_z >= HYPOTHESES["capacity_pressure_load_factor_z"] and (
        gap_z < HYPOTHESES["high_utilization_gap_z"] or raw_gap <= 0
    ):
        return {
            "statement": (
                f"the airport fills its seats at a high level relative to peers (raw load factor "
                f"{lf_fact['value']}), but passenger growth is not outrunning seat growth in absolute terms "
                f"(raw demand_supply_gap {raw_gap} percentage points); this is consistent with a mature, "
                "well-utilized airport and is NOT evidence of growing unmet demand"
            ),
            "rule": (
                f"load_factor z >= {HYPOTHESES['capacity_pressure_load_factor_z']} AND "
                f"(demand_supply_gap z < {HYPOTHESES['high_utilization_gap_z']} OR raw demand_supply_gap_pct <= 0) "
                "(within hub tier)"
            ),
            "based_on": ["ttm_load_factor", "ttm_passenger_vs_seat_growth_gap"],
            "strength": "moderate",
            "alternative_explanations": ["structural constraints that cap both seats and passengers", "seasonality"],
        }
    return None


def _no_pressure_signal_inference(lf_fact, gap_fact, delayed_fact):
    lf_z, gap_z = _z(lf_fact), _z(gap_fact)
    if lf_z is None or gap_z is None:
        return None
    delayed_z = _z(delayed_fact)
    load_factor_high = lf_z >= HYPOTHESES["capacity_pressure_load_factor_z"]
    gap_elevated = gap_z >= HYPOTHESES["capacity_pressure_gap_z"]
    delayed_high = delayed_fact["value"] is not None and delayed_z is not None and (
        delayed_z >= HYPOTHESES["delay_linked_strain_delayed_share_z"]
    )
    if not load_factor_high and not gap_elevated and not delayed_high:
        return {
            "statement": (
                "the data shows no sign of capacity pressure: load factor and the passenger-vs-seat growth gap "
                "are not elevated relative to hub-tier peers, and the delayed-departure share (where measurable) "
                "is not elevated either"
            ),
            "rule": (
                f"NOT(load_factor z >= {HYPOTHESES['capacity_pressure_load_factor_z']}) AND "
                f"NOT(demand_supply_gap z >= {HYPOTHESES['capacity_pressure_gap_z']}) AND "
                f"NOT(delayed_share z >= {HYPOTHESES['delay_linked_strain_delayed_share_z']}) (within hub tier)"
            ),
            "based_on": ["ttm_load_factor", "ttm_passenger_vs_seat_growth_gap", "ttm_delayed_share"],
            "strength": "weak",
            "alternative_explanations": ["demand pressure not yet reflected in a full TTM window"],
        }
    return None


def _inferred_rules(lf_fact, gap_fact, delayed_fact):
    """
    Deterministic inference rules over measured peer-context z-scores -- no
    LLM, no imputation (a rule that needs a null fact simply does not fire).
    Thresholds are HYPOTHESES (src/analysis/unmet_demand_constants.py):
    analyst judgment, not measured values. Wording always says "is
    consistent with", never "proves" or "shows demand".
    """
    inferences = [
        _capacity_pressure_inference(lf_fact, gap_fact),
        _delay_linked_strain_inference(delayed_fact),
        _high_utilization_balanced_growth_inference(lf_fact, gap_fact),
        _no_pressure_signal_inference(lf_fact, gap_fact, delayed_fact),
    ]
    return [i for i in inferences if i is not None]


def get_unmet_demand_decomposition(conn, code, end_month=None):
    """
    One airport's unmet-demand decomposition (see module docstring).
    `end_month`, if given, is an (year, month) tuple; otherwise resolved to
    the latest month cached in source A, same as src/scoring/signals.py.
    That resolved month is reused for every fact below (including
    congestion) so all facts share one as_of window instead of congestion
    defaulting to OTP's own latest cached month.
    """
    airport_code = validate_airport_code(conn, code)
    method = (
        "decomposition of public-data facts relevant to unmet demand for one airport -- "
        "never a single unmet-demand number; see src/analysis/unmet_demand_constants.UNKNOWABLE "
        "for what this data cannot answer"
    )

    resolved_end = _resolve_end_month(conn, end_month)

    ttm_load_factor_fact, peak_fact, high_months_fact = _load_factor_facts(conn, airport_code, resolved_end)
    growth_gap_fact = _growth_gap_fact(conn, airport_code, resolved_end)
    delayed_share_fact = _delayed_share_fact(conn, airport_code, resolved_end)

    measured = [ttm_load_factor_fact, growth_gap_fact, peak_fact, high_months_fact, delayed_share_fact]
    inferred = _inferred_rules(ttm_load_factor_fact, growth_gap_fact, delayed_share_fact)

    sources = sorted({f["source"] for f in measured if f["source"] != "no cached data"})
    any_measured = any(f["value"] is not None for f in measured)
    confidence = "medium" if any_measured else "low"

    result = {
        "airport": airport_code,
        "measured": measured,
        "unknown": UNKNOWABLE,
        "inferred": inferred,
    }
    caveats = [
        "this decomposition is not a measure of unmet demand -- it is the subset of public-data facts "
        "that bear on investment demand; see 'unknown' for what it structurally cannot tell us",
    ]
    return envelope(result, method, caveats, "; ".join(sources) if sources else "no cached data", confidence)
