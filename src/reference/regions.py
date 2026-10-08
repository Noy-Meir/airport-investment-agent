"""
Region airport lists (currently: New England) -- moved out of
src/cache/accessors.py (Phase 2b refactor). Behavior is unchanged; built in
code from OurAirports iso_region intersected with source-A airports above
the volume floor, not from the scheduled_service flag alone -- see
docs/DECISIONS.md.
"""

from src.cache.config import NEW_ENGLAND_STATES, VOLUME_FLOOR_PAX, VOLUME_FLOOR_YEAR
from src.reference.envelope import envelope, source_stamp
from src.reference.pax import pax_by_airport_for_year, pax_by_airport_ttm


def region_list(conn, pax_by_code, floor, source_note, states=NEW_ENGLAND_STATES):
    regions = [f"US-{st}" for st in states]
    placeholders = ",".join("?" for _ in regions)
    oa_rows = conn.execute(
        f"SELECT iata_code, name, iso_region, source, fetched_at FROM ourairports WHERE iso_region IN ({placeholders})",
        regions,
    ).fetchall()

    above = []
    for r in oa_rows:
        pax = pax_by_code.get(r["iata_code"])
        if pax is not None and pax >= floor:
            above.append({"code": r["iata_code"], "name": r["name"], "state": r["iso_region"][3:], "passengers": pax})
    above.sort(key=lambda x: x["code"])

    source = "no cached data" if not oa_rows else f"{oa_rows[0]['source']}, cached {oa_rows[0]['fetched_at']} + {source_note}"
    return envelope(
        above,
        f"OurAirports iso_region in {states} intersected with {source_note} >= {floor:,}",
        [f"volume floor {floor:,} is provisional pending trailing-12-month confirmation (see docs/DECISIONS.md)"],
        source, "medium",
    )


def list_new_england_airports(conn, floor=VOLUME_FLOOR_PAX, year=VOLUME_FLOOR_YEAR):
    """
    New England = CT/ME/MA/NH/RI/VT (OurAirports iso_region), intersected
    with source-A airports whose CY `year` total_passengers >= `floor`.
    Built in code, not from the scheduled_service flag alone -- see
    docs/DECISIONS.md. Months with NULL total_passengers are excluded from
    the sum, not zeroed.
    """
    pax_by_code, excluded = pax_by_airport_for_year(conn, year)
    return region_list(conn, pax_by_code, floor, source_note=f"CY{year} total_passengers (excluded {excluded} NULL month-rows)")


def list_new_england_airports_ttm(conn, end_month=None, floor=VOLUME_FLOOR_PAX):
    """Same as list_new_england_airports, but over the trailing 12 months ending at the latest cached month (or `end_month`)."""
    pax_by_code, excluded, as_of = pax_by_airport_ttm(conn, end_month)
    if as_of is None:
        return envelope(None, "OurAirports intersected with TTM total_passengers", ["cache is empty"], "no cached data", "low")
    return region_list(conn, pax_by_code, floor, source_note=f"TTM total_passengers ending {as_of} (excluded {excluded} NULL month-rows)")
