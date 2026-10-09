import json

import pytest

from src.cache.manifest import (
    REQUIRED_FIELDS,
    get_source,
    load_manifest,
    update_entry,
    validate_manifest,
)

FIXTURE_ENTRIES = [
    {
        "id": "bts_t100_airport_month",
        "name": "BTS T-100 Segment Summary by Origin Airport",
        "publisher": "US DOT Bureau of Transportation Statistics (BTS)",
        "url": "https://data.bts.gov/resource/r495-tyji.json",
        "access": "snapshot-in-db",
        "storage": "source_a_airport_month",
        "vintage": "monthly, 2025-01..2025-02",
        "fetched_at": "2026-01-01T00:00:00Z",
        "row_count": 2,
        "notes": "fixture entry",
        "license": "public domain",
    },
    {
        "id": "bts_t100_route",
        "name": "BTS T-100 Segment (All Carriers), route level",
        "publisher": "US DOT Bureau of Transportation Statistics (BTS)",
        "url": "https://www.transtats.bts.gov/PREZIP/",
        "access": "snapshot-in-db",
        "storage": "t100_route_agg",
        "vintage": "CY2025",
        "fetched_at": "2026-01-01T00:00:00Z",
        "row_count": 4,
        "notes": "fixture entry",
        "license": "public domain",
    },
    {
        "id": "bts_otp",
        "name": "BTS On-Time Performance (Reporting Carrier)",
        "publisher": "US DOT Bureau of Transportation Statistics (BTS)",
        "url": "https://transtats.bts.gov/PREZIP/",
        "access": "snapshot-in-db",
        "storage": "otp_airport_month",
        "vintage": "monthly, 2025-01..2025-02",
        "fetched_at": "2026-01-01T00:00:00Z",
        "row_count": 2,
        "notes": "fixture entry",
        "license": "public domain",
    },
    {
        "id": "ourairports",
        "name": "OurAirports airports.csv",
        "publisher": "OurAirports (community-maintained)",
        "url": "https://davidmegginson.github.io/ourairports-data/airports.csv",
        "access": "snapshot-in-db",
        "storage": "ourairports",
        "vintage": "current snapshot",
        "fetched_at": "2026-01-01T00:00:00Z",
        "row_count": 1,
        "notes": "fixture entry",
        "license": "public domain",
    },
]


@pytest.fixture
def fixture_manifest_path(tmp_path):
    path = tmp_path / "sources_manifest.json"
    path.write_text(json.dumps(FIXTURE_ENTRIES))
    return str(path)


def test_load_manifest_loads_committed_file():
    entries = load_manifest()
    assert len(entries) == 4
    ids = {e["id"] for e in entries}
    assert ids == {"bts_t100_airport_month", "bts_t100_route", "bts_otp", "ourairports"}
    for e in entries:
        for field in REQUIRED_FIELDS:
            assert field in e and e[field] not in (None, ""), f"{e['id']} missing {field}"


def test_get_source_by_id():
    entry = get_source("bts_otp")
    assert entry["storage"] == "otp_airport_month"
    assert get_source("does_not_exist") is None


def test_validate_manifest_passes_against_matching_fixture_db(fixture_conn, fixture_manifest_path):
    errors = validate_manifest(fixture_conn, path=fixture_manifest_path)
    assert errors == []


def test_validate_manifest_catches_missing_table(fixture_conn, fixture_manifest_path, tmp_path):
    entries = json.loads(open(fixture_manifest_path).read())
    entries[0]["storage"] = "no_such_table"
    bad_path = tmp_path / "bad_manifest.json"
    bad_path.write_text(json.dumps(entries))

    errors = validate_manifest(fixture_conn, path=str(bad_path))
    assert any("no_such_table" in e and "does not exist" in e for e in errors)


def test_validate_manifest_catches_missing_field(fixture_conn, fixture_manifest_path, tmp_path):
    entries = json.loads(open(fixture_manifest_path).read())
    del entries[0]["url"]
    bad_path = tmp_path / "bad_manifest.json"
    bad_path.write_text(json.dumps(entries))

    errors = validate_manifest(fixture_conn, path=str(bad_path))
    assert any("url" in e for e in errors)


def test_update_entry_rewrites_file(fixture_manifest_path):
    updated = update_entry("bts_otp", {"row_count": 99}, path=fixture_manifest_path)
    assert updated["row_count"] == 99
    entries = load_manifest(fixture_manifest_path)
    assert get_source("bts_otp", path=fixture_manifest_path)["row_count"] == 99
