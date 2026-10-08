"""
Client for BTS T-100 Segment Summary by Origin Airport (Socrata dataset
r495-tyji) -- airport-month aggregates. See docs/DECISIONS.md, Source A.

Paged (Socrata $limit/$offset) and typed (numeric strings cast to float/int
on the way out). No raw user text is interpolated into the SoQL query --
years are validated as ints before being embedded.
"""

import json
import logging
import socket
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE_URL = "https://data.bts.gov/resource/r495-tyji.json"
PAGE_SIZE = 1000
TIMEOUT_SECONDS = 60

SOURCE_NAME = "BTS T-100 Segment Summary by Origin Airport (r495-tyji)"

# Map of our internal column name -> Socrata field name for the fields we
# ingest from this dataset: total_* and domestic_*. International figures
# are not read from a dataset field -- they're computed at ingest as
# total - domestic (see _intl below), which is robust to exactly how the
# upstream table happens to name any international-specific columns.
FIELD_MAP = {
    "origin_airport_code": "origin_airport_code",
    "origin_airport_name": "origin_airport_name",
    "year": "year",
    "reporting_month": "reporting_month",
    "total_departures": "total_departures",
    "total_passengers": "total_passengers",
    "total_seats": "total_seats",
    "total_load_factor": "total_load_factor",
    "total_distance_flight_sm": "total_distance_flight_sm",
    "domestic_departures": "domestic_departures",
    "domestic_passengers": "domestic_passengers",
    "domestic_seats": "domestic_seats",
    "domestic_load_factor": "domestic_load_factor",
}

logger = logging.getLogger("source_a")


class SourceAError(Exception):
    pass


def _to_float(v):
    if v in (None, ""):
        return None
    return float(v)


def _intl(total, domestic):
    """total - domestic, computed at ingest. NULL (None) if either side is NULL -- no imputation."""
    if total is None or domestic is None:
        return None
    return total - domestic


def _get_page(year, offset):
    if not isinstance(year, int):
        raise SourceAError(f"year must be an int, got {year!r}")
    params = {
        "$where": f"year='{year}'",
        "$order": "origin_airport_code,reporting_month",
        "$limit": str(PAGE_SIZE),
        "$offset": str(offset),
    }
    url = BASE_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "airport-investment-agent/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, socket.timeout) as e:
        raise SourceAError(f"request failed for year={year} offset={offset}: {e}") from e


def fetch_year(year):
    """Yields typed row dicts for every airport-month in `year`, paginated."""
    offset = 0
    while True:
        page = _get_page(year, offset)
        if not page:
            break
        for raw in page:
            row = {"year": int(raw.get("year", year))}
            reporting_month = raw.get("reporting_month", "")
            row["reporting_month"] = reporting_month
            row["month"] = int(reporting_month[5:7]) if len(reporting_month) >= 7 else None
            row["origin_airport_code"] = raw.get("origin_airport_code")
            row["origin_airport_name"] = raw.get("origin_airport_name")
            for our_name, socrata_name in FIELD_MAP.items():
                if our_name in ("origin_airport_code", "origin_airport_name", "year", "reporting_month"):
                    continue
                row[our_name] = _to_float(raw.get(socrata_name))
            row["intl_departures"] = _intl(row["total_departures"], row["domestic_departures"])
            row["intl_passengers"] = _intl(row["total_passengers"], row["domestic_passengers"])
            row["intl_seats"] = _intl(row["total_seats"], row["domestic_seats"])
            yield row
        logger.info("year=%s offset=%d -> %d rows", year, offset, len(page))
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE


def fetch_years(years):
    for year in years:
        yield from fetch_year(year)


def fetched_at_stamp():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
