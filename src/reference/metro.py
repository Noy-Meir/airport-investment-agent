"""
Thin JSON accessor over data/reference/metro_areas.json -- a hand-curated,
analyst-convention grouping of airports into metro areas (see its own
"_note"). NOT an official FAA/OMB/CBSA definition, not refreshed by
scripts/build_cache.py.
"""

import json
import os

from src.cache.config import REFERENCE_DIR
from src.reference.envelope import envelope

METRO_AREAS_PATH = os.path.join(REFERENCE_DIR, "metro_areas.json")


def get_metro_siblings(code):
    """
    `code`'s metro group (if any) and its sibling airport codes (metro
    membership minus `code` itself). An airport with no entry returns
    result=None -- absence means "no metro grouping on file", not "this
    airport has no nearby airports".
    """
    method = "lookup in data/reference/metro_areas.json (hand-curated analyst convention, not an official definition)"
    if not os.path.exists(METRO_AREAS_PATH):
        return envelope(None, method, ["metro_areas.json not found"], "no cached data", "low")

    with open(METRO_AREAS_PATH) as f:
        data = json.load(f)
    source = f"data/reference/metro_areas.json, as_of {data.get('_as_of')}"

    for metro_id, metro in data.get("metros", {}).items():
        airports = metro.get("airports", [])
        if code in airports:
            siblings = sorted(a for a in airports if a != code)
            caveats = ["metro grouping is an analyst convention, not an official FAA/OMB/CBSA definition"]
            if metro.get("needs_verification"):
                caveats.append("this metro grouping needs_verification")
            return envelope(
                {
                    "metro_id": metro_id,
                    "metro_name": metro.get("name"),
                    "siblings": siblings,
                    "source_url": metro.get("source_url") or None,
                },
                method, caveats, source, "low" if metro.get("needs_verification") else "medium",
            )

    return envelope(
        None, method, ["no entry for this airport -- absence means no metro grouping on file"], source, "low",
    )
