"""
Thin JSON accessor over data/reference/buildability.json -- a hand-curated
file (see its own "_note"), not pulled from an API and not refreshed by
scripts/build_cache.py.
"""

import json
import os

from src.cache.config import REFERENCE_DIR
from src.reference.envelope import envelope

BUILDABILITY_PATH = os.path.join(REFERENCE_DIR, "buildability.json")


def get_buildability(code):
    """
    Curated constraints for `code`, exactly as recorded (verified_by_user /
    needs_verification shown as-is, never reinterpreted). An airport with no
    entry returns "no constraints on file" -- absence here means "not
    researched", never "verified unconstrained" (CLAUDE.md: no imputation).
    """
    method = "lookup in data/reference/buildability.json (hand-curated, not an API)"
    if not os.path.exists(BUILDABILITY_PATH):
        return envelope(None, method, ["buildability.json not found"], "no cached data", "low")

    with open(BUILDABILITY_PATH) as f:
        data = json.load(f)
    source = f"data/reference/buildability.json, as_of {data.get('_as_of')}"

    airport = data.get("airports", {}).get(code)
    if airport is None:
        return envelope(
            {"airport": code, "has_constraints": False, "constraints": [], "note": "no constraints on file"},
            method,
            ["no entry for this airport -- absence means not researched, not verified unconstrained"],
            source, "low",
        )

    constraints = airport["constraints"]
    confidence = "medium" if any(c.get("needs_verification") for c in constraints) else "high"
    return envelope(
        {"airport": code, "has_constraints": True, "constraints": constraints, "name": airport.get("name")},
        method, [], source, confidence,
    )
