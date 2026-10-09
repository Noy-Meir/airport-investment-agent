"""
Loader/validator for data/sources_manifest.json -- a uniform registry of
every upstream data source this project uses (one entry per source: id,
name, publisher, url, access, storage, vintage, fetched_at, row_count,
notes, license). See docs/DECISIONS.md "Adding a data source".

This module never refreshes data; it only describes what's already in
data/cache.db or data/snapshots/.
"""

import json
import os

from src.cache.config import REPO_ROOT

MANIFEST_PATH = os.path.join(REPO_ROOT, "data", "sources_manifest.json")

REQUIRED_FIELDS = [
    "id", "name", "publisher", "url", "access", "storage",
    "vintage", "fetched_at", "row_count", "notes", "license",
]
VALID_ACCESS = {"snapshot-in-db", "snapshot-file", "live+snapshot"}

# row_count is a point-in-time fetch snapshot; the live table/file grows
# (new months land) or shrinks (re-ingest with stricter filtering) between
# manifest edits, so an exact match would make the manifest high-maintenance
# busywork instead of a useful sanity check.
ROW_COUNT_TOLERANCE = 0.5


class ManifestError(Exception):
    pass


def load_manifest(path=MANIFEST_PATH):
    """Returns the list of source entries, parsed as-is (no validation)."""
    with open(path) as f:
        return json.load(f)


def get_source(source_id, path=MANIFEST_PATH):
    """Returns the single entry with id == source_id, or None if absent."""
    for entry in load_manifest(path):
        if entry.get("id") == source_id:
            return entry
    return None


def validate_manifest(conn, path=MANIFEST_PATH):
    """
    Checks every entry has all REQUIRED_FIELDS, a valid `access` value, that
    its `storage` table (snapshot-in-db) exists in `conn` or file
    (snapshot-file/live+snapshot) exists on disk, and that `row_count` is
    roughly in line with the table's current row count (within
    ROW_COUNT_TOLERANCE; tables grow/shrink between manifest refreshes, so
    this is a sanity check, not an exact match).

    Returns a list of human-readable error strings; empty list means valid.
    `conn` may be None if no entry uses access="snapshot-in-db" (otherwise
    required).
    """
    errors = []
    entries = load_manifest(path)
    for entry in entries:
        entry_id = entry.get("id", "<missing id>")
        missing = [f for f in REQUIRED_FIELDS if f not in entry or entry[f] in (None, "")]
        if missing:
            errors.append(f"{entry_id}: missing required field(s) {missing}")
            continue

        if entry["access"] not in VALID_ACCESS:
            errors.append(f"{entry_id}: invalid access {entry['access']!r}, must be one of {sorted(VALID_ACCESS)}")
            continue

        if entry["access"] == "snapshot-in-db":
            if conn is None:
                errors.append(f"{entry_id}: access is snapshot-in-db but no db connection was given to validate against")
                continue
            table = entry["storage"]
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if row is None:
                errors.append(f"{entry_id}: storage table {table!r} does not exist in the database")
                continue
            actual_count = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
            expected_count = entry["row_count"]
            lower = expected_count * (1 - ROW_COUNT_TOLERANCE)
            upper = expected_count * (1 + ROW_COUNT_TOLERANCE) + 1
            if not (lower <= actual_count <= upper):
                errors.append(
                    f"{entry_id}: manifest row_count={expected_count} is far from actual "
                    f"{table} row count={actual_count} (outside +/-{int(ROW_COUNT_TOLERANCE * 100)}% tolerance)"
                )
        else:
            storage_path = entry["storage"]
            if not os.path.isabs(storage_path):
                storage_path = os.path.join(REPO_ROOT, storage_path)
            if not os.path.exists(storage_path):
                errors.append(f"{entry_id}: storage file {entry['storage']!r} does not exist")

    return errors


def update_entry(entry_id, updates, path=MANIFEST_PATH):
    """
    Merges `updates` into the entry with id == entry_id and rewrites the
    manifest file in place. Raises ManifestError if entry_id isn't found.
    """
    entries = load_manifest(path)
    for i, entry in enumerate(entries):
        if entry.get("id") == entry_id:
            entries[i] = {**entry, **updates}
            with open(path, "w") as f:
                json.dump(entries, f, indent=2)
                f.write("\n")
            return entries[i]
    raise ManifestError(f"no manifest entry with id {entry_id!r}")
