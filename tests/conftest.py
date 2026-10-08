import os
import shutil
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.cache import db
from src.cache.config import CACHE_DB_PATH
from tests.fixtures.make_fixture_db import FIXTURE_DB_PATH, build as build_fixture_db


@pytest.fixture
def fixture_conn():
    """
    A connection to a fresh copy of the tiny committed synthetic fixture db
    (tests/fixtures/fixture_cache.db). Runs without data/cache.db or
    data/raw/ being present -- rebuilds the fixture from
    tests/fixtures/make_fixture_db.py if the committed copy is missing.
    """
    if not os.path.exists(FIXTURE_DB_PATH):
        build_fixture_db()
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_path = tmp.name
    shutil.copyfile(FIXTURE_DB_PATH, tmp_path)
    conn = db.connect(tmp_path)
    yield conn
    conn.close()
    os.remove(tmp_path)


@pytest.fixture(scope="module")
def real_conn():
    """A connection to the real data/cache.db. Skips LOUDLY (with a reason) if it hasn't been built."""
    if not os.path.exists(CACHE_DB_PATH):
        pytest.skip(f"REAL CACHE MISSING: {CACHE_DB_PATH} not built -- run `python scripts/build_cache.py` first")
    conn = db.connect()
    yield conn
    conn.close()
