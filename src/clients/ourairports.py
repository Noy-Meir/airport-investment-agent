"""
Client for the OurAirports static reference CSV (airport code/name/region).
See docs/DECISIONS.md, source D. ~12.7MB, under the 50MB ask-first threshold.

Streams and parses without holding the whole decoded text as one giant
string longer than necessary; never printed to the console.
"""

import csv
import io
import logging
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
TIMEOUT_SECONDS = 60
SOURCE_NAME = "OurAirports airports.csv"

logger = logging.getLogger("ourairports")


class OurAirportsError(Exception):
    pass


def _fetch_csv_text():
    req = urllib.request.Request(URL, headers={"User-Agent": "airport-investment-agent/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        raise OurAirportsError(f"request failed: {e}") from e


def parse_us_airports(csv_text):
    """
    Parses the OurAirports CSV text into a list of dict rows for US airports
    (iso_country == 'US') with a non-empty iata_code, dropping type='closed'
    rows. Raises OurAirportsError (naming the offenders) if two or more
    remaining active rows still share an iata_code -- never silently picks
    one. Takes plain text (not a URL) so it's testable without network.
    """
    reader = csv.DictReader(io.StringIO(csv_text))
    rows = []
    for r in reader:
        if r.get("iso_country") != "US" or not r.get("iata_code"):
            continue
        if r.get("type") == "closed":
            continue
        rows.append({
            "iata_code": r["iata_code"],
            "icao_ident": r.get("ident"),
            "name": r.get("name"),
            "type": r.get("type"),
            "iso_country": r.get("iso_country"),
            "iso_region": r.get("iso_region"),
            "municipality": r.get("municipality"),
            "latitude_deg": float(r["latitude_deg"]) if r.get("latitude_deg") else None,
            "longitude_deg": float(r["longitude_deg"]) if r.get("longitude_deg") else None,
            "scheduled_service": r.get("scheduled_service"),
        })

    by_code = defaultdict(list)
    for r in rows:
        by_code[r["iata_code"]].append(r)
    dupes = {code: rs for code, rs in by_code.items() if len(rs) > 1}
    if dupes:
        detail = "; ".join(
            f"{code}: {[(r['name'], r['icao_ident'], r['type']) for r in rs]}"
            for code, rs in sorted(dupes.items())
        )
        raise OurAirportsError(
            f"{len(dupes)} iata_code(s) shared by multiple active (non-closed) rows -- "
            f"not picking one arbitrarily: {detail}"
        )

    logger.info("parsed %d US airports with an IATA code (closed dropped)", len(rows))
    return rows


def fetch_us_airports():
    """Fetches the live CSV and returns parsed, deduped US airport rows. See parse_us_airports."""
    return parse_us_airports(_fetch_csv_text())


def fetched_at_stamp():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
