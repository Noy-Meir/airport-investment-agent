"""
Named weight sets for the composite investment score (see docs/SCORING.md).

HYPOTHESES is the production default: an analyst hypothesis about relative
importance, not fit to any outcome data (CLAUDE.md: no unsourced facts --
these are declared assumptions, not measurements). Alternative sets below are
used by src/scoring/score.py's sensitivity() to show how much the ranking
depends on this specific hypothesis.
"""

HYPOTHESES = {
    "growth": 0.30,
    "load_factor": 0.25,
    "demand_supply_gap": 0.25,
    "congestion": 0.20,
}

EQUAL = {
    "growth": 0.25,
    "load_factor": 0.25,
    "demand_supply_gap": 0.25,
    "congestion": 0.25,
}

GROWTH_HEAVY = {
    "growth": 0.55,
    "load_factor": 0.15,
    "demand_supply_gap": 0.20,
    "congestion": 0.10,
}

CONGESTION_HEAVY = {
    "growth": 0.15,
    "load_factor": 0.15,
    "demand_supply_gap": 0.15,
    "congestion": 0.55,
}

DROP_CONGESTION = {
    "growth": 0.40,
    "load_factor": 0.30,
    "demand_supply_gap": 0.30,
    "congestion": 0.0,
}

SENSITIVITY_WEIGHT_SETS = {
    "hypotheses": HYPOTHESES,
    "equal": EQUAL,
    "growth_heavy": GROWTH_HEAVY,
    "congestion_heavy": CONGESTION_HEAVY,
    "drop_congestion": DROP_CONGESTION,
}

SIGNAL_NAMES = tuple(HYPOTHESES.keys())


def validate_weights(weights):
    """
    Raises ValueError if `weights` doesn't have exactly SIGNAL_NAMES keys with
    non-negative values summing to > 0 (a weight set that is all zero can't be
    renormalized). Does NOT require weights to sum to 1 -- score_airport
    renormalizes over whichever signals are actually available.
    """
    if set(weights.keys()) != set(SIGNAL_NAMES):
        raise ValueError(f"weights must have exactly keys {sorted(SIGNAL_NAMES)}, got {sorted(weights.keys())}")
    if any(w < 0 for w in weights.values()):
        raise ValueError(f"weights must be non-negative, got {weights}")
    if sum(weights.values()) <= 0:
        raise ValueError("weights must sum to > 0")
