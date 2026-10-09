"""
Pure logic for the airport_outlook table: FAA TAF enplanement forecast +
OurAirports runway counts, joined to our airport table by FAA LID.

Context-only -- this module (and the airport_outlook table it feeds) is
never read by src/scoring, src/analysis, or any ranking/composite-score
path. No network/db I/O here (see src.clients.faa_taf, src.clients.runways,
src.clients.ourairports for fetching; scripts/build_cache.py wires them
together). No imputation: a missing component anywhere below propagates to
a None field, never a guessed number.
"""

PAVED_PREFIXES = ("ASP", "CON", "PEM", "BIT")  # asphalt, concrete, pem(bituminous), bitumen-coated
MIN_RUNWAY_LENGTH_FT = 5000


def compute_cagr(base, future, years):
    """
    Compound annual growth rate from `base` to `future` over `years` years.
    None if base or future is missing, or base <= 0 (CAGR undefined) --
    never imputed.
    """
    if base is None or future is None or base <= 0 or years <= 0:
        return None
    return round((future / base) ** (1.0 / years) - 1.0, 4)


def select_base_plus_years(rows_for_lid):
    """
    `rows_for_lid`: list of {scenario, ayear, total} for one FAA locid (see
    src.clients.faa_taf.parse_enplanements_rows). Base year = the latest
    year with scenario == 0 (the latest actual, not hardcoded). Returns a
    dict with taf_base_fy, enplanements_base, enplanements_plus5,
    enplanements_plus10, cagr_5y, cagr_10y. All None if there's no
    scenario == 0 row, or if a given target year isn't present in the data
    -- never imputed/interpolated.
    """
    by_year = {r["ayear"]: r["total"] for r in rows_for_lid}
    actual_years = [r["ayear"] for r in rows_for_lid if r["scenario"] == 0]
    if not actual_years:
        return {
            "taf_base_fy": None, "enplanements_base": None,
            "enplanements_plus5": None, "enplanements_plus10": None,
            "cagr_5y": None, "cagr_10y": None,
        }
    base_fy = max(actual_years)
    base_val = by_year.get(base_fy)
    plus5_val = by_year.get(base_fy + 5)
    plus10_val = by_year.get(base_fy + 10)
    return {
        "taf_base_fy": base_fy,
        "enplanements_base": base_val,
        "enplanements_plus5": plus5_val,
        "enplanements_plus10": plus10_val,
        "cagr_5y": compute_cagr(base_val, plus5_val, 5),
        "cagr_10y": compute_cagr(base_val, plus10_val, 10),
    }


def match_lid(iata_code, local_code, lid_set):
    """
    Maps one airport to an FAA locid. Tries local_code first (the FAA LID
    for US airports), then falls back to iata_code itself (true for most
    large hubs, e.g. ATL/ORD/LAX). No manual mapping table, no guessing --
    an unmatched airport gets (None, reason).
    """
    if local_code and local_code in lid_set:
        return local_code, None
    if iata_code and iata_code in lid_set:
        return iata_code, "matched via iata_code fallback (no local_code match)"
    if not local_code:
        return None, "no OurAirports local_code on record and iata_code not found in TAF data"
    return None, "neither local_code nor iata_code found in TAF enplanements data"


def is_paved(surface):
    """True if `surface` (OurAirports free-text surface code) looks paved, by prefix. None/"" -> False, never imputed as paved."""
    if not surface:
        return False
    return surface.strip().upper().startswith(PAVED_PREFIXES)


def _to_float(v):
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def qualifying_runway_count(runway_rows):
    """
    Counts runways in `runway_rows` (src.clients.runways row dicts for one
    airport_ref) that are not closed, have a paved surface, and
    length_ft >= MIN_RUNWAY_LENGTH_FT. A runway with a missing/unparseable
    length_ft does not qualify (not imputed as long enough).
    """
    count = 0
    for r in runway_rows or []:
        if (r.get("closed") or "").strip() == "1":
            continue
        if not is_paved(r.get("surface")):
            continue
        length = _to_float(r.get("length_ft"))
        if length is None or length < MIN_RUNWAY_LENGTH_FT:
            continue
        count += 1
    return count


def build_outlook_row(iata_code, local_code, ourairports_id, enplanements_by_lid, runways_by_ref, lid_set):
    """
    Combines matching + enplanement selection + runway counting into one
    airport_outlook row dict (without source/fetched_at, added by the
    caller). Always returns a row for `iata_code` -- unmatched airports get
    NULL TAF/enplanement fields plus match_note explaining why, per the
    no-imputation rule (never silently dropped, never guessed).
    """
    faa_lid, match_note = match_lid(iata_code, local_code, lid_set)
    if faa_lid is not None:
        taf = select_base_plus_years(enplanements_by_lid.get(faa_lid, []))
    else:
        taf = {
            "taf_base_fy": None, "enplanements_base": None,
            "enplanements_plus5": None, "enplanements_plus10": None,
            "cagr_5y": None, "cagr_10y": None,
        }

    runway_rows = runways_by_ref.get(ourairports_id, []) if ourairports_id else []
    qualifying = qualifying_runway_count(runway_rows)
    base = taf["enplanements_base"]
    enplanements_per_runway = round(base / qualifying, 1) if base is not None and qualifying > 0 else None

    return {
        "iata_code": iata_code,
        "faa_lid": faa_lid,
        "qualifying_runways": qualifying,
        "enplanements_per_runway": enplanements_per_runway,
        "match_note": match_note,
        **taf,
    }
