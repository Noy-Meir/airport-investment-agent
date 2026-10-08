"""
Aggregates the raw T-100 Segment (All Carriers) CSV (inside the ZIP
downloaded by src.clients.t100_routes) down to one row per
(year, month, origin, dest, class, carrier), summing DEPARTURES_PERFORMED,
SEATS, PASSENGERS and keeping DISTANCE (fixed per route).

Streams the CSV row-by-row out of the ZIP -- never holds the raw rows or
the decoded CSV text in memory, only the aggregated dict (bounded by the
number of distinct route/carrier/class/month combinations, not row count).
Never loaded into the LLM's context; callers only see the aggregate.

Only rows where ORIGIN_COUNTRY == 'US' or DEST_COUNTRY == 'US' are kept,
per the Phase 1 spec (route-level data is otherwise worldwide).
"""

import csv
import io
import zipfile


class T100AggregateError(Exception):
    pass


def _num(x):
    if x in (None, ""):
        return 0.0
    return float(x)


def aggregate_routes(zip_path):
    """
    Returns (agg, missing_distance) where agg is a dict keyed by
    (year, month, origin, dest, cls, carrier) ->
    {"departures_performed": float, "seats": float, "passengers": float, "distance": float|None}.
    `distance` is taken as the max of the non-empty values seen for that key
    (it's fixed per route; max guards against a rare bad zero row rather
    than silently averaging) and stays None if every row for that key had an
    empty DISTANCE field -- never coerced to 0.0, since 0 is a real distance
    (same-airport positioning/sightseeing flights) and None is not.
    `missing_distance` is {"rows": int, "departures_performed": float},
    tracking CSV rows with an empty DISTANCE field across the whole file.
    """
    try:
        z = zipfile.ZipFile(zip_path)
    except (FileNotFoundError, zipfile.BadZipFile) as e:
        raise T100AggregateError(f"cannot open {zip_path}: {e}") from e

    try:
        data_name = next(
            n for n in z.namelist()
            if n.lower().endswith(".csv") and "documentation" not in n.lower()
        )
    except StopIteration:
        raise T100AggregateError(f"{zip_path}: no data CSV found in ZIP") from None

    agg = {}
    missing_distance_rows = 0
    missing_distance_departures = 0.0
    with z.open(data_name) as f:
        reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
        for row in reader:
            if row.get("ORIGIN_COUNTRY") != "US" and row.get("DEST_COUNTRY") != "US":
                continue
            key = (
                int(row["YEAR"]),
                int(row["MONTH"]),
                row["ORIGIN"],
                row["DEST"],
                row["CLASS"],
                row["UNIQUE_CARRIER"],
            )
            entry = agg.setdefault(key, {"departures_performed": 0.0, "seats": 0.0, "passengers": 0.0, "distance": None})
            departures = _num(row.get("DEPARTURES_PERFORMED"))
            entry["departures_performed"] += departures
            entry["seats"] += _num(row.get("SEATS"))
            entry["passengers"] += _num(row.get("PASSENGERS"))
            dist_raw = row.get("DISTANCE")
            if dist_raw in (None, ""):
                missing_distance_rows += 1
                missing_distance_departures += departures
            else:
                dist = float(dist_raw)
                entry["distance"] = dist if entry["distance"] is None else max(entry["distance"], dist)
    return agg, {"rows": missing_distance_rows, "departures_performed": missing_distance_departures}


def long_haul_share_by_class_group(agg, origin, thresholds, class_group):
    """
    Pure function over an already-aggregated dict (as returned by the agg
    half of aggregate_routes): % of DEPARTURES_PERFORMED on routes >= each
    threshold, for rows at this origin whose CLASS is in `class_group`,
    weighted by departures. Rows with distance is None (never a recorded
    DISTANCE) are excluded from both numerator and denominator. Rows with
    distance == 0 are included when origin == dest (real same-airport
    positioning/sightseeing flights) and excluded as a data error when
    origin != dest, matching src.cache.accessors.get_long_haul_share.
    Returns {threshold: (pct_or_None, total_departures)}.
    """
    rows = [(k[3], v) for k, v in agg.items() if k[2] == origin and k[4] in class_group]
    valid_rows = [
        v for dest, v in rows
        if v["distance"] is not None and (v["distance"] != 0 or dest == origin)
    ]
    total = sum(r["departures_performed"] for r in valid_rows)
    result = {}
    for thr in thresholds:
        if total == 0:
            result[thr] = (None, 0.0)
            continue
        long_deps = sum(r["departures_performed"] for r in valid_rows if r["distance"] >= thr)
        result[thr] = (100.0 * long_deps / total, total)
    return result
