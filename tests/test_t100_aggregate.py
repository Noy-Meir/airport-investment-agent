"""
Unit tests for the distance-handling rules in src/cache/t100_aggregate.py:
  - an empty DISTANCE CSV field stays None (never coerced to 0.0) and is
    tracked separately (rows + departures), not imputed.
  - distance == 0 at origin == dest (real same-airport flights) is kept.
  - distance == 0 at origin != dest (data error) is excluded.
"""

import io
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.cache.t100_aggregate import aggregate_routes, long_haul_share_by_class_group

HEADER = (
    "YEAR,MONTH,ORIGIN,DEST,CLASS,UNIQUE_CARRIER,DISTANCE,"
    "DEPARTURES_PERFORMED,SEATS,PASSENGERS,ORIGIN_COUNTRY,DEST_COUNTRY\n"
)


def _make_zip(path, rows):
    csv_text = HEADER + "".join(rows)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("data.csv", csv_text)
    return path


def test_missing_distance_stays_none_and_is_tracked(tmp_path):
    zip_path = _make_zip(
        tmp_path / "t.zip",
        [
            "2025,1,TST,AAA,F,XX,2500,5,50,40,US,US\n",
            "2025,1,TST,CCC,F,XX,,2,20,0,US,US\n",  # empty DISTANCE
        ],
    )
    agg, missing = aggregate_routes(str(zip_path))
    assert agg[(2025, 1, "TST", "CCC", "F", "XX")]["distance"] is None
    assert agg[(2025, 1, "TST", "AAA", "F", "XX")]["distance"] == 2500.0
    assert missing == {"rows": 1, "departures_performed": 2.0}


def test_same_airport_zero_distance_kept_cross_airport_zero_excluded(tmp_path):
    zip_path = _make_zip(
        tmp_path / "t.zip",
        [
            "2025,1,TST,AAA,F,XX,2500,5,50,40,US,US\n",
            "2025,1,TST,TST,L,XX,0,3,10,0,US,US\n",   # real same-airport positioning flight
            "2025,1,TST,BBB,F,XX,0,4,10,0,US,US\n",   # data error: 0mi between different airports
        ],
    )
    agg, missing = aggregate_routes(str(zip_path))
    assert missing == {"rows": 0, "departures_performed": 0.0}

    result = long_haul_share_by_class_group(agg, "TST", (2000,), class_group={"F", "G", "L", "P"})
    pct, total = result[2000]
    # valid: AAA (5 deps, long) + TST->TST (3 deps, short) = 8; TST->BBB (4 deps) excluded as a data error.
    assert total == 8.0
    assert pct == 100.0 * 5 / 8
