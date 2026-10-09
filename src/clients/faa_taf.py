"""
Client for the FAA Terminal Area Forecast (TAF) bulk download -- a ZIP of
XLSX files (Airports, AirportsOperations, BasedAircraft, Enplanements,
Tracon), of which only Enplanements.xlsx is used here. See docs/DECISIONS.md,
source: FAA TAF.

Context-only (src/cache/outlook.py), never imported by src/scoring or
src/analysis.

Downloads to a caller-supplied temp directory (not data/raw/, not
committed) and is cleaned up by the caller. No third-party xlsx library is
added as a dependency -- an .xlsx is a ZIP of XML, so this reads the one
worksheet directly with xml.etree.ElementTree.iterparse, clearing each row
element after use so the ~84MB decompressed sheet XML never sits fully
parsed in memory.
"""

import logging
import socket
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from io import BytesIO

TAF_ZIP_URL = "https://taf.faa.gov/Downloads/APO100_TAF_Final_2025.zip"
ENPLANEMENTS_MEMBER = "Enplanements.xlsx"
TIMEOUT_SECONDS = 120
SOURCE_NAME = "FAA Terminal Area Forecast (TAF), 2025 edition, Enplanements"

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
# Column order in Enplanements.xlsx: locid, scenario, ayear, aac, aat, commuter, us_flag, frgn_flag.
ENPLANEMENT_VALUE_COLS = ("aac", "aat", "commuter", "us_flag", "frgn_flag")
COLUMN_ORDER = ("locid", "scenario", "ayear") + ENPLANEMENT_VALUE_COLS


class FaaTafError(Exception):
    pass


def _fetch_zip_bytes():
    req = urllib.request.Request(TAF_ZIP_URL, headers={"User-Agent": "airport-investment-agent/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            return resp.read()
    except (urllib.error.URLError, TimeoutError, socket.timeout) as e:
        raise FaaTafError(f"TAF zip download failed: {e}") from e


def fetch_enplanements_xlsx_bytes():
    """Downloads the TAF zip (in memory) and returns the raw bytes of Enplanements.xlsx."""
    zip_bytes = _fetch_zip_bytes()
    try:
        with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
            return zf.read(ENPLANEMENTS_MEMBER)
    except (zipfile.BadZipFile, KeyError) as e:
        raise FaaTafError(f"TAF zip did not contain a readable {ENPLANEMENTS_MEMBER}: {e}") from e


def _cell_value(cell):
    t = cell.get("t")
    is_el = cell.find(f"{NS}is")
    if is_el is not None:
        t_el = is_el.find(f"{NS}t")
        return t_el.text if t_el is not None and t_el.text is not None else ""
    v_el = cell.find(f"{NS}v")
    if v_el is None or v_el.text is None:
        return None
    if t == "inlineStr":
        return v_el.text
    try:
        return float(v_el.text)
    except ValueError:
        return v_el.text


def parse_enplanements_rows(xlsx_bytes):
    """
    Parses Enplanements.xlsx bytes into a list of dicts, one per (locid,
    scenario, ayear) row: {locid, scenario, ayear, total}, where total =
    sum(aac, aat, commuter, us_flag, frgn_flag) -- None (not 0) if any of
    the five is missing, per CLAUDE.md's no-imputation rule. locid is
    whitespace-stripped (the source file space-pads it to a fixed width).
    """
    rows = []
    header = None
    with zipfile.ZipFile(BytesIO(xlsx_bytes)) as zf:
        with zf.open("xl/worksheets/sheet1.xml") as f:
            for _, elem in ET.iterparse(f, events=("end",)):
                if elem.tag != f"{NS}row":
                    continue
                cells = [_cell_value(c) for c in elem]
                if header is None:
                    header = cells
                    elem.clear()
                    continue
                values = dict(zip(COLUMN_ORDER, cells))
                comps = [values.get(c) for c in ENPLANEMENT_VALUE_COLS]
                total = None if any(v is None for v in comps) else sum(comps)
                locid = (values.get("locid") or "").strip()
                scenario = values.get("scenario")
                ayear = values.get("ayear")
                if locid and scenario is not None and ayear is not None:
                    rows.append({
                        "locid": locid,
                        "scenario": int(scenario),
                        "ayear": int(ayear),
                        "total": total,
                    })
                elem.clear()
    return rows


def rows_by_locid(rows):
    """Groups parse_enplanements_rows() output into {locid: [row, ...]}."""
    by_lid = {}
    for r in rows:
        by_lid.setdefault(r["locid"], []).append(r)
    return by_lid


def fetched_at_stamp():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
