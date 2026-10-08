"""
Phase 1 golden tests. Run against the cache.db built by scripts/build_cache.py
(the tests build their own throwaway copy via conftest fixtures where that's
needed and otherwise read data/cache.db directly -- see each test).

Run: python -m pytest tests/ -v
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.cache import accessors, db
from src.cache.config import CACHE_DB_PATH, RAW_DIR, REFERENCE_DIR
from src.cache.t100_aggregate import aggregate_routes, long_haul_share_by_class_group

ANC_ZIP = os.path.join(RAW_DIR, "t100_segment_all_carrier_2025.zip")


@pytest.fixture(scope="module")
def conn():
    if not os.path.exists(CACHE_DB_PATH):
        pytest.skip(f"{CACHE_DB_PATH} not built -- run scripts/build_cache.py first")
    c = db.connect()
    yield c
    c.close()


@pytest.mark.skipif(not os.path.exists(ANC_ZIP), reason="2025 route-level ZIP not present in data/raw/")
def test_anc_long_haul_shares_match_raw_file(conn):
    """
    (a) ANC CY2025 long-haul shares by CLASS group at 2000/2500/3000mi, as
    returned by the cache accessor, must equal the numbers computed directly
    from the raw downloaded file by the same aggregation logic -- i.e. the
    cache build didn't silently change the answer.
    """
    raw_agg, _missing_distance = aggregate_routes(ANC_ZIP)
    thresholds = (2000, 2500, 3000)

    for class_group, classes in accessors.CLASS_GROUPS.items():
        expected = long_haul_share_by_class_group(raw_agg, "ANC", thresholds, classes)
        got = accessors.get_long_haul_share(conn, "ANC", 2025, thresholds, class_group)
        assert got["result"] is not None, f"class_group={class_group}: accessor returned no cached data"
        for thr in thresholds:
            exp_pct, exp_total = expected[thr]
            got_pct = got["result"]["shares_pct"][thr]
            if exp_pct is None:
                assert got_pct is None
            else:
                assert got_pct == pytest.approx(exp_pct, abs=0.05), (
                    f"class_group={class_group} thr={thr}: cache={got_pct} raw_file={exp_pct}"
                )
        # expected[thr][1] is the valid (distance != 0) total, same for every threshold.
        assert got["result"]["total_departures"] == pytest.approx(expected[thresholds[0]][1])


def test_anc_long_haul_shares_vs_independent_spike_measurement(conn):
    """
    (e) Independent regression check: ANC CY2025 class_group='all' values as
    measured by a SEPARATE script (scripts/scripy.py, run directly against
    the raw ZIP, not through src/cache at all) and recorded in
    docs/DECISIONS.md: total_departures=87,988, shares 47.4/43.2/31.0%.
    get_long_haul_share excludes only rows with missing DISTANCE from
    numerator and denominator; the 67 ANC->ANC same-airport 0-mile combos
    (242 departures -- real sightseeing/positioning flights, not missing
    data) are kept as short-haul. That matches this independent ground
    truth exactly.
    """
    got = accessors.get_long_haul_share(conn, "ANC", 2025, (2000, 2500, 3000), "all")
    assert got["result"]["total_departures"] == pytest.approx(87_988, abs=1)
    assert got["result"]["shares_pct"][2000] == pytest.approx(47.4, abs=0.05)
    assert got["result"]["shares_pct"][2500] == pytest.approx(43.2, abs=0.05)
    assert got["result"]["shares_pct"][3000] == pytest.approx(31.0, abs=0.05)


def test_new_england_list_matches_decisions_doc():
    """(b) New England list equals the 10 airports in docs/DECISIONS.md."""
    expected = {"BOS", "BDL", "PVD", "PWM", "BTV", "MHT", "HVN", "BGR", "ORH", "ACK"}
    path = os.path.join(REFERENCE_DIR, "regions.json")
    if not os.path.exists(path):
        pytest.skip(f"{path} not built -- run scripts/build_cache.py first")
    with open(path) as f:
        regions = json.load(f)
    got = {a["code"] for a in regions["new_england"]["result"]}
    assert got == expected


def test_hub_tiers_31_34_76():
    """(c) Hub tiers give 31/34/76."""
    path = os.path.join(REFERENCE_DIR, "hub_tiers.json")
    if not os.path.exists(path):
        pytest.skip(f"{path} not built -- run scripts/build_cache.py first")
    with open(path) as f:
        tiers = json.load(f)
    counts = tiers["result"]["counts"]
    assert counts == {"large": 31, "medium": 34, "small": 76}


def test_unknown_airport_code_raises_clear_error(conn):
    """(d) Unknown airport code returns a clear error, not a guess."""
    with pytest.raises(accessors.AirportNotFoundError, match="ZZZ"):
        accessors.validate_airport_code(conn, "ZZZ")
    with pytest.raises(accessors.AirportNotFoundError):
        accessors.get_airport_year_totals(conn, "ZZZ", 2024)
    with pytest.raises(accessors.AirportNotFoundError):
        accessors.get_long_haul_share(conn, "ZZZ", 2025)
