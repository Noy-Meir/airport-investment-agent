"""
One-time migration: reads data/cache.db (read-only -- never opened for
writing, never modified, never deleted) and writes a NEW file
data/cache.slim.db with t100_route_agg collapsed to the grain
src.cache.accessors.get_long_haul_share actually reads: (year, origin,
dest, class) -> departures_performed summed across months and carriers,
distance kept as the max non-null value seen (same rule as
src.cache.t100_aggregate.collapse_to_storage_grain, applied here in SQL
since the source is already in the DB, not a raw CSV), keeping only the
latest year present (older years are never reachable -- get_long_haul_share
always reads MAX(year)).

source_a_airport_month, otp_airport_month and ourairports are copied
unchanged -- every other accessor reads most/all of those tables' rows.

Downloads nothing; reads only the local data/cache.db.

After building data/cache.slim.db, verifies equivalence: for a fixed list
of airports present in the data, calls accessors.get_long_haul_share
against both the old and new DB with the default thresholds and asserts
identical results. Prints a size table (per table, old vs new) and the
verification outcome.

Usage:
    python scripts/slim_cache.py
"""

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.cache import accessors, db
from src.cache.config import CACHE_DB_PATH, LONG_HAUL_THRESHOLDS_MI

SLIM_DB_PATH = os.path.join(os.path.dirname(CACHE_DB_PATH), "cache.slim.db")

TABLES = ["t100_route_agg", "source_a_airport_month", "otp_airport_month", "ourairports"]

VERIFY_AIRPORTS = ["ANC", "SEA", "LAX", "HNL", "BOS", "JFK", "ORD", "ATL", "DFW"]


def _table_sizes(db_path):
    """{table_name: MB}, table data plus its own indexes, via the dbstat virtual table."""
    con = sqlite3.connect(db_path)
    sizes = {}
    try:
        # sqlite_master maps every index (named or sqlite_autoindex_*) to its owning table.
        owner = dict(con.execute("SELECT name, tbl_name FROM sqlite_master WHERE type IN ('table','index')").fetchall())
        totals = {}
        for name, pgsize in con.execute("SELECT name, pgsize FROM dbstat"):
            tbl = owner.get(name, name)
            totals[tbl] = totals.get(tbl, 0) + pgsize
        sizes = {t: totals.get(t, 0) / 1e6 for t in TABLES}
    finally:
        con.close()
    return sizes


def build_slim_db(src_path=CACHE_DB_PATH, dst_path=SLIM_DB_PATH):
    if os.path.exists(dst_path):
        os.remove(dst_path)

    src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    dst = sqlite3.connect(dst_path)
    db.init_schema(dst)
    # init_schema only creates tables that don't exist yet -- t100_route_agg
    # here is the new (slim) schema, defined in src/cache/db.py.

    latest_year = src.execute("SELECT MAX(year) AS y FROM t100_route_agg").fetchone()["y"]
    route_rows = src.execute(
        """
        SELECT year, origin, dest, class,
               SUM(departures_performed) AS departures_performed,
               MAX(distance) AS distance,
               MIN(source) AS source, MIN(fetched_at) AS fetched_at
        FROM t100_route_agg
        WHERE year = ?
        GROUP BY year, origin, dest, class
        """,
        (latest_year,),
    ).fetchall()
    dst.executemany(
        "INSERT INTO t100_route_agg (year, origin, dest, class, departures_performed, "
        "distance, source, fetched_at) VALUES (?,?,?,?,?,?,?,?)",
        [(r["year"], r["origin"], r["dest"], r["class"], r["departures_performed"],
          r["distance"], r["source"], r["fetched_at"]) for r in route_rows],
    )

    source_a_cols = [c[1] for c in src.execute("PRAGMA table_info(source_a_airport_month)")]
    rows = src.execute(f"SELECT {', '.join(source_a_cols)} FROM source_a_airport_month").fetchall()
    dst.executemany(
        f"INSERT INTO source_a_airport_month ({', '.join(source_a_cols)}) "
        f"VALUES ({', '.join('?' for _ in source_a_cols)})",
        [tuple(r) for r in rows],
    )

    otp_cols = [c[1] for c in src.execute("PRAGMA table_info(otp_airport_month)")]
    rows = src.execute(f"SELECT {', '.join(otp_cols)} FROM otp_airport_month").fetchall()
    dst.executemany(
        f"INSERT INTO otp_airport_month ({', '.join(otp_cols)}) "
        f"VALUES ({', '.join('?' for _ in otp_cols)})",
        [tuple(r) for r in rows],
    )

    oa_cols = [c[1] for c in src.execute("PRAGMA table_info(ourairports)")]
    rows = src.execute(f"SELECT {', '.join(oa_cols)} FROM ourairports").fetchall()
    dst.executemany(
        f"INSERT INTO ourairports ({', '.join(oa_cols)}) "
        f"VALUES ({', '.join('?' for _ in oa_cols)})",
        [tuple(r) for r in rows],
    )

    dst.commit()
    dst.execute("VACUUM")
    dst.close()
    src.close()
    return latest_year, len(route_rows)


def verify_equivalence(old_path, new_path, airports):
    """
    For each airport, calls accessors.get_long_haul_share against both DBs
    with the default thresholds/class_groups and asserts identical results.
    Returns (ok, details) -- details is a list of per-airport outcome lines.
    """
    old_conn = db.connect(old_path)
    new_conn = db.connect(new_path)
    details = []
    ok = True
    latest_year = accessors.get_latest_t100_route_year(new_conn)
    for code in airports:
        mismatch = None
        for class_group in ("passenger", "all"):
            old_env = accessors.get_long_haul_share(
                old_conn, code, latest_year, thresholds=LONG_HAUL_THRESHOLDS_MI, class_group=class_group
            )
            new_env = accessors.get_long_haul_share(
                new_conn, code, latest_year, thresholds=LONG_HAUL_THRESHOLDS_MI, class_group=class_group
            )
            if old_env["result"] != new_env["result"]:
                mismatch = (class_group, old_env["result"], new_env["result"])
                break
        if mismatch is None:
            details.append(f"  {code}: OK (passenger + all class_group, year={latest_year})")
        else:
            ok = False
            class_group, old_result, new_result = mismatch
            details.append(f"  {code}: MISMATCH on class_group={class_group}\n    old={old_result}\n    new={new_result}")
    old_conn.close()
    new_conn.close()
    return ok, details


def main():
    if not os.path.exists(CACHE_DB_PATH):
        print(f"ERROR: {CACHE_DB_PATH} not found -- nothing to slim.")
        sys.exit(1)

    old_sizes = _table_sizes(CACHE_DB_PATH)
    latest_year, n_rows = build_slim_db()
    new_sizes = _table_sizes(SLIM_DB_PATH)

    print(f"t100_route_agg collapsed to year={latest_year}: {n_rows:,} (year,origin,dest,class) rows")
    print()
    print(f"{'table':<28} {'old MB':>10} {'new MB':>10}")
    for t in TABLES:
        print(f"{t:<28} {old_sizes.get(t, 0):>10.2f} {new_sizes.get(t, 0):>10.2f}")
    old_total = os.path.getsize(CACHE_DB_PATH) / 1e6
    new_total = os.path.getsize(SLIM_DB_PATH) / 1e6
    print(f"{'TOTAL (file size)':<28} {old_total:>10.2f} {new_total:>10.2f}")
    print()

    ok, details = verify_equivalence(CACHE_DB_PATH, SLIM_DB_PATH, VERIFY_AIRPORTS)
    print(f"Verification ({len(VERIFY_AIRPORTS)} airports, get_long_haul_share, default thresholds):")
    print("\n".join(details))
    print()
    print("VERIFICATION " + ("PASSED" if ok else "FAILED"))
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
