"""
Shared thresholds and lookups. Centralized here (not hardcoded at each call
site) so data/reference/*.json builders and src/cache accessors agree.

CLASS codes verified against 14 CFR Sec. 291.45 (BTS Schedule T-100
regulatory text), not from the ZIP's Documentation.csv -- that file lists
CLASS as "Service Class" with no legend. See docs/DECISIONS.md.
"""

import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE_DB_PATH = os.path.join(REPO_ROOT, "data", "cache.db")
RAW_DIR = os.path.join(REPO_ROOT, "data", "raw")
REFERENCE_DIR = os.path.join(REPO_ROOT, "data", "reference")

# CLASS field (Service Class), 14 CFR Sec. 291.45:
#   F = Scheduled Passenger/Cargo        G = Scheduled All-Cargo
#   L = Nonscheduled Passenger/Cargo     P = Nonscheduled All-Cargo
CLASS_MEANINGS = {
    "F": "Scheduled Passenger/Cargo",
    "G": "Scheduled All-Cargo",
    "L": "Nonscheduled Passenger/Cargo",
    "P": "Nonscheduled All-Cargo",
}
PASSENGER_CLASSES = {"F", "L"}
CARGO_CLASSES = {"G", "P"}

# Volume floor: CY2024 total (domestic+international) passengers, source A.
# See docs/DECISIONS.md "Volume floor" -- recompute on trailing 12 months
# before this is relied on for anything beyond Phase 1 region/tier lists.
VOLUME_FLOOR_YEAR = 2024
VOLUME_FLOOR_PAX = 100_000

# Hub tiers: share of national CY2024 total passengers, source A.
HUB_TIER_YEAR = 2024
HUB_TIER_THRESHOLDS = {
    "large": 0.01,
    "medium": 0.0025,
    "small": 0.0005,
}

NEW_ENGLAND_STATES = ["CT", "ME", "MA", "NH", "RI", "VT"]

LONG_HAUL_THRESHOLDS_MI = (2000, 2500, 3000)
