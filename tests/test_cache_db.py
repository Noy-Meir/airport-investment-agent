import os
import sqlite3

from src.cache.config import CACHE_DB_PATH
from src.cache.manifest import validate_manifest

EXPECTED_TABLES = [
    "source_a_airport_month",
    "t100_route_agg",
    "otp_airport_month",
    "ourairports",
]


def test_cache_db_exists():
    assert os.path.isfile(CACHE_DB_PATH)


def test_cache_db_opens_read_only_with_expected_tables():
    uri = f"file:{CACHE_DB_PATH}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        for table in EXPECTED_TABLES:
            count = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
            assert count > 0, f"{table} has no rows"
    finally:
        conn.close()


def test_cache_db_passes_manifest_validation():
    conn = sqlite3.connect(CACHE_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        errors = validate_manifest(conn)
    finally:
        conn.close()
    assert errors == []
