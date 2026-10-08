"""
Trailing-12-month window + passenger-sum helpers shared by hub tier and
region computations (src/reference/hub_tiers.py, src/reference/regions.py)
and by the TTM accessors in src/cache/accessors.py.

No imputation: months with NULL total_passengers are excluded from the sum,
not zeroed -- see CLAUDE.md.
"""


def get_latest_cached_month(conn):
    """Returns (year, month) of the most recent source-A row in the cache, or None if empty."""
    row = conn.execute("SELECT MAX(year * 100 + month) AS ym FROM source_a_airport_month").fetchone()
    if row is None or row["ym"] is None:
        return None
    ym = int(row["ym"])
    return ym // 100, ym % 100


def trailing_12_window(end_year, end_month):
    months = []
    y, m = end_year, end_month
    for _ in range(12):
        months.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return sorted(months)


def pax_by_airport_for_year(conn, year):
    rows = conn.execute(
        "SELECT origin_airport_code, total_passengers FROM source_a_airport_month WHERE year = ?",
        (year,),
    ).fetchall()
    by_code = {}
    excluded = 0
    for r in rows:
        if r["total_passengers"] is None:
            excluded += 1
            continue
        by_code[r["origin_airport_code"]] = by_code.get(r["origin_airport_code"], 0) + r["total_passengers"]
    return by_code, excluded


def pax_by_airport_ttm(conn, end_month=None):
    if end_month is None:
        latest = get_latest_cached_month(conn)
        if latest is None:
            return {}, 0, None
        end_year, end_m = latest
    else:
        end_year, end_m = end_month
    window = trailing_12_window(end_year, end_m)
    ym_values = [y * 100 + m for y, m in window]
    placeholders = ",".join("?" for _ in ym_values)
    rows = conn.execute(
        f"SELECT origin_airport_code, total_passengers FROM source_a_airport_month WHERE (year * 100 + month) IN ({placeholders})",
        ym_values,
    ).fetchall()
    by_code = {}
    excluded = 0
    for r in rows:
        if r["total_passengers"] is None:
            excluded += 1
            continue
        by_code[r["origin_airport_code"]] = by_code.get(r["origin_airport_code"], 0) + r["total_passengers"]
    as_of = f"{end_year:04d}-{end_m:02d}"
    return by_code, excluded, as_of
