"""
Constants for src/analysis/unmet_demand.py.

No public BTS/FAA/OurAirports dataset measures unmet demand directly, so
this module never computes a single "unmet demand" number. UNKNOWABLE names
what the available data structurally cannot answer, each with why and what
data would be required -- these are listed, not guessed around.
"""

# Hypothesis, not a measured threshold: a TTM month's load factor at or
# above this is labelled "high" in the peak-month fact. Analyst judgment,
# not derived from any source.
LOAD_FACTOR_HIGH = 0.85

UNKNOWABLE = [
    {
        "name": "passengers_priced_or_scheduled_out",
        "why_we_cannot_know": (
            "BTS/FAA data only records flights and passengers that actually flew -- "
            "travelers who did not fly because of fare levels or schedule availability "
            "leave no record in any source this agent reads."
        ),
        "data_needed": "stated-preference survey or a booking/search-abandonment dataset covering this market",
    },
    {
        "name": "fare_or_yield_levels",
        "why_we_cannot_know": (
            "T-100/source-A reports passengers and seats, not ticket prices -- fare and "
            "yield are not present in any cached source."
        ),
        "data_needed": "DOT DB1B (O&D fare survey) or carrier yield data, neither currently cached",
    },
    {
        "name": "diversion_to_other_metro_airports",
        "why_we_cannot_know": (
            "each airport's traffic is reported independently; there is no cached "
            "origin-level data linking a traveler's airport choice to nearby alternatives, "
            "so we cannot tell whether suppressed traffic at one airport shows up at another."
        ),
        "data_needed": "passenger origin-zip or survey data covering all airports in the metro, not just this one",
    },
    {
        "name": "slot_gate_runway_or_off_airport_constraints",
        "why_we_cannot_know": (
            "operational capacity constraints (slot controls, gate counts, runway capacity, "
            "surrounding land use) are not in BTS/FAA traffic data -- the only constraint "
            "data this agent has is the curated buildability reference for a handful of "
            "named airports (data/reference/buildability.json), not a general capacity model."
        ),
        "data_needed": "FAA facility/slot records or an airport-specific capacity study",
    },
    {
        "name": "international_origin_destination_demand",
        "why_we_cannot_know": (
            "cached OTP congestion data covers domestic reporting carriers only; "
            "international O&D passenger demand is not fully captured by the sources cached here."
        ),
        "data_needed": "DOT T-100 international segment and O&D survey data, not currently cached",
    },
]
