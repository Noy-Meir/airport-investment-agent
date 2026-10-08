"""
Hub tier classification (large/medium/small, + TTM-only "micro") by share of
national source-A total_passengers -- moved out of src/cache/accessors.py
(Phase 2b refactor). See docs/DECISIONS.md "Hub tiers" and "Production
definition" for the CY2024-pinned vs. TTM-production distinction, and "Micro
tier" for why a 4th tier exists.
"""

from src.cache.config import HUB_TIER_THRESHOLDS, HUB_TIER_YEAR, VOLUME_FLOOR_PAX
from src.reference.envelope import envelope, source_stamp
from src.reference.pax import pax_by_airport_for_year, pax_by_airport_ttm


def hub_tier_result(pax_by_code, thresholds, label, micro_floor=None):
    """
    `micro_floor`: if given, every airport with `pax >= micro_floor` that
    doesn't meet any share threshold gets a 4th "micro" tier instead of no
    tier at all -- so every volume-floor-eligible airport has a peer group
    (see docs/DECISIONS.md "Micro tier"). None (the CY2024-pinned caller)
    preserves the original large/medium/small-only, no-micro-key behavior
    exactly -- the committed CY2024 baselines and golden tests are unchanged.
    """
    national = sum(pax_by_code.values())
    tiers = {"large": [], "medium": [], "small": []}
    if micro_floor is not None:
        tiers["micro"] = []
    ordered = sorted(thresholds.items(), key=lambda kv: -kv[1])
    for code, pax in pax_by_code.items():
        share = pax / national if national else 0
        tier_name = next((t for t, thresh in ordered if share >= thresh), None)
        if tier_name is None and micro_floor is not None and pax >= micro_floor:
            tier_name = "micro"
        if tier_name is not None:
            tiers[tier_name].append({"code": code, "passengers": pax, "share_pct": round(100 * share, 4)})
    for t in tiers:
        tiers[t].sort(key=lambda x: -x["passengers"])
    result = {
        "national_total_passengers": national, "tiers": tiers,
        "counts": {k: len(v) for k, v in tiers.items()},
    }
    method = f"share of national {label}, thresholds {thresholds}"
    if micro_floor is not None:
        method += f", micro = eligible (total_passengers >= {micro_floor:,}) but below every share threshold"
    return result, method


def compute_hub_tiers(conn, year=HUB_TIER_YEAR, thresholds=HUB_TIER_THRESHOLDS):
    """
    Hub tier by share of national CY `year` total_passengers (source A),
    thresholds as a fraction of the national total. Large/medium/small are
    mutually exclusive, in descending threshold order. Months with NULL
    total_passengers are excluded from the sum, not zeroed. No micro tier --
    this is the CY2024-pinned baseline used by the golden tests, unchanged.
    """
    pax_by_code, excluded = pax_by_airport_for_year(conn, year)
    row = conn.execute(
        "SELECT source, fetched_at FROM source_a_airport_month WHERE year = ? LIMIT 1", (year,)
    ).fetchone()
    if row is None:
        return envelope(None, "share of national CY total_passengers", [f"no source-A rows cached for {year}"], "no cached data", "low")
    caveats = [f"excluded {excluded} month-row(s) with NULL total_passengers from the sums"] if excluded else []
    result, method = hub_tier_result(pax_by_code, thresholds, f"CY{year} total_passengers")
    return envelope(result, method, caveats, source_stamp(row["source"], row["fetched_at"]), "high")


def compute_hub_tiers_ttm(conn, end_month=None, thresholds=HUB_TIER_THRESHOLDS, volume_floor=VOLUME_FLOOR_PAX):
    """
    Same as compute_hub_tiers, but over the trailing 12 months ending at the
    latest cached month (or `end_month`), and WITH a 4th "micro" tier: every
    airport with TTM total_passengers >= `volume_floor` that doesn't meet any
    large/medium/small share threshold. This is the production definition
    used by scoring (src/scoring/signals.get_peer_group_ttm) so every
    volume-floor-eligible airport has a peer group -- see docs/DECISIONS.md
    "Micro tier".
    """
    pax_by_code, excluded, as_of = pax_by_airport_ttm(conn, end_month)
    if as_of is None:
        return envelope(None, "share of national TTM total_passengers", ["cache is empty"], "no cached data", "low")
    caveats = [f"trailing 12 months ending {as_of}"]
    if excluded:
        caveats.append(f"excluded {excluded} month-row(s) with NULL total_passengers from the sums")
    result, method = hub_tier_result(pax_by_code, thresholds, f"TTM total_passengers ending {as_of}", micro_floor=volume_floor)
    row = conn.execute("SELECT source, fetched_at FROM source_a_airport_month ORDER BY fetched_at DESC LIMIT 1").fetchone()
    return envelope(result, method, caveats, source_stamp(row["source"], row["fetched_at"]) if row else "no cached data", "high")
