"""
Builds a tiny, committed synthetic cache.db so accessor tests can run
without data/cache.db or data/raw/ being present (e.g. in CI, or before
scripts/build_cache.py has ever been run).

Deliberately includes:
  - airport TST, CY2025: month 1 complete, month 2 has NULL total_passengers
    (exercises the no-imputation / excluded-row-count path).
  - route-level rows for TST with one valid long route, one valid short
    route, one same-airport 0-distance combo (included as short-haul) and
    one NULL-distance combo (excluded) -- exercises the distance-handling
    path in get_long_haul_share.
  - OurAirports row for BOS (New England, above the floor) and for TST
    itself absent from ourairports (present only via source_a_airport_month,
    to exercise validate_airport_code checking both tables).
  - OTP rows for TST, month 1 (includes a cancelled flight, excluded from
    taxi/delay observations) and month 2 (no cancellations) -- exercises
    get_congestion_ttm / get_congestion_month and the OTP/T-100 coverage
    overlap (both months have non-NULL source_a domestic_departures, so
    both count toward coverage).
  - deliberately NO row for 'ZZZ' anywhere, for the unknown-code tests.
"""

import os
import sqlite3

from src.cache import db

FIXTURE_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixture_cache.db")

SOURCE = "fixture"
FETCHED_AT = "2026-01-01T00:00:00Z"


def build(path=FIXTURE_DB_PATH):
    if os.path.exists(path):
        os.remove(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    db.init_schema(conn)

    source_a_rows = [
        {
            "origin_airport_code": "TST", "year": 2025, "month": 1,
            "reporting_month": "2025-01-01T00:00:00.000", "origin_airport_name": "Test Airport",
            "total_departures": 10.0, "total_passengers": 100.0, "total_seats": 120.0,
            "total_load_factor": 83.3, "total_distance_flight_sm": 900.0,
            "domestic_departures": 8.0, "domestic_passengers": 60.0, "domestic_seats": 80.0,
            "domestic_load_factor": 75.0, "intl_departures": 2.0, "intl_passengers": 40.0, "intl_seats": 40.0,
        },
        {
            # month 2: total_passengers deliberately NULL -- no imputation, must be excluded from sums.
            "origin_airport_code": "TST", "year": 2025, "month": 2,
            "reporting_month": "2025-02-01T00:00:00.000", "origin_airport_name": "Test Airport",
            "total_departures": 12.0, "total_passengers": None, "total_seats": 130.0,
            "total_load_factor": None, "total_distance_flight_sm": 900.0,
            "domestic_departures": 9.0, "domestic_passengers": 70.0, "domestic_seats": 90.0,
            "domestic_load_factor": 77.8, "intl_departures": 3.0, "intl_passengers": None, "intl_seats": 40.0,
        },
    ]
    db.upsert_source_a_rows(conn, source_a_rows, SOURCE, FETCHED_AT)

    route_agg = {
        (2025, "TST", "AAA", "F"): {"departures_performed": 5.0, "distance": 2500.0},
        (2025, "TST", "BBB", "F"): {"departures_performed": 5.0, "distance": 1000.0},
        (2025, "TST", "TST", "L"): {"departures_performed": 3.0, "distance": 0.0},
        (2025, "TST", "CCC", "F"): {"departures_performed": 2.0, "distance": None},
    }
    db.upsert_route_agg(conn, route_agg, "fixture route-level", FETCHED_AT)

    otp_agg = {
        (2025, 1, "TST"): {
            "n_flights": 10, "n_cancelled": 1, "n_diverted": 0,
            "n_taxi_out_obs": 9, "sum_taxi_out": 180.0,
            "n_dep_delay_obs": 9, "sum_dep_delay_min": 90.0, "n_dep_del15": 2,
            "n_arr_delay_obs": 9, "sum_arr_delay_min": 81.0, "n_arr_del15": 1,
            "n_carriers": 2,
        },
        (2025, 2, "TST"): {
            "n_flights": 8, "n_cancelled": 0, "n_diverted": 1,
            "n_taxi_out_obs": 8, "sum_taxi_out": 160.0,
            "n_dep_delay_obs": 8, "sum_dep_delay_min": 40.0, "n_dep_del15": 0,
            "n_arr_delay_obs": 8, "sum_arr_delay_min": 32.0, "n_arr_del15": 0,
            "n_carriers": 1,
        },
    }
    db.upsert_otp_rows(conn, otp_agg, "fixture OTP", FETCHED_AT)

    ourairports_rows = [
        {
            "iata_code": "BOS", "icao_ident": "KBOS", "name": "Boston Logan Intl", "type": "large_airport",
            "iso_country": "US", "iso_region": "US-MA", "municipality": "Boston",
            "latitude_deg": 42.36, "longitude_deg": -71.01, "scheduled_service": "yes",
        },
    ]
    db.upsert_ourairports(conn, ourairports_rows, "fixture ourairports", FETCHED_AT)

    conn.close()
    return path


if __name__ == "__main__":
    print("built", build())
