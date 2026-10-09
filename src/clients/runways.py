"""
Client for the OurAirports static runways.csv (one row per runway end pair).
See docs/DECISIONS.md, source D. ~4MB, under the 50MB ask-first threshold.

Context-only (src/cache/outlook.py) -- never imported by src/scoring or
src/analysis.
"""

import csv
import io
import socket
import urllib.error
import urllib.request

URL = "https://davidmegginson.github.io/ourairports-data/runways.csv"
TIMEOUT_SECONDS = 60
SOURCE_NAME = "OurAirports runways.csv"


class RunwaysError(Exception):
    pass


def _fetch_csv_text():
    req = urllib.request.Request(URL, headers={"User-Agent": "airport-investment-agent/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, socket.timeout) as e:
        raise RunwaysError(f"request failed: {e}") from e


def parse_runways_by_airport_ref(csv_text):
    """
    Parses runways.csv into {airport_ref: [runway_dict, ...]}, where
    airport_ref is the OurAirports internal numeric airport id (joins to
    airports.csv's `id` column, not iata_code/local_code). Each runway_dict
    keeps only the fields the qualifying-runway filter needs: closed,
    surface, length_ft (left as the raw string/None -- no imputation,
    casting happens in src.cache.outlook).
    """
    reader = csv.DictReader(io.StringIO(csv_text))
    by_ref = {}
    for r in reader:
        ref = r.get("airport_ref")
        if not ref:
            continue
        by_ref.setdefault(ref, []).append({
            "closed": r.get("closed"),
            "surface": r.get("surface"),
            "length_ft": r.get("length_ft"),
        })
    return by_ref


def fetch_runways_by_airport_ref():
    return parse_runways_by_airport_ref(_fetch_csv_text())
