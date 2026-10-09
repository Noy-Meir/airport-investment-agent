"""
SQLite schema and low-level write helpers for data/cache.db.

Every table carries `source` (which upstream dataset) and `fetched_at` (UTC
ISO8601 timestamp of the pull) on every row, per CLAUDE.md's cache-through
rule: cache is refreshed, not replaced blind, and every number must trace to
a source. Read access goes through src/cache/accessors.py, not this module
directly, except for build_cache.py which owns writes.
"""

import sqlite3

from src.cache.config import CACHE_DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS source_a_airport_month (
    origin_airport_code TEXT NOT NULL,
    year INTEGER NOT NULL,
    month INTEGER NOT NULL,
    reporting_month TEXT,
    origin_airport_name TEXT,
    total_departures REAL,
    total_passengers REAL,
    total_seats REAL,
    total_load_factor REAL,
    total_distance_flight_sm REAL,
    domestic_departures REAL,
    domestic_passengers REAL,
    domestic_seats REAL,
    domestic_load_factor REAL,
    intl_departures REAL,
    intl_passengers REAL,
    intl_seats REAL,
    source TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (origin_airport_code, year, month)
);

-- Grain is (year, origin, dest, class): departures_performed summed across
-- months and carriers, distance kept as the max non-null value seen (fixed
-- per route). This is the minimal grain src.cache.accessors.get_long_haul_share
-- actually reads -- it filters by origin/year/class and never selects month
-- or carrier. See docs/DECISIONS.md "Phase 8: t100_route_agg slimming".
CREATE TABLE IF NOT EXISTS t100_route_agg (
    year INTEGER NOT NULL,
    origin TEXT NOT NULL,
    dest TEXT NOT NULL,
    class TEXT NOT NULL,
    departures_performed REAL,
    distance REAL,
    source TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (year, origin, dest, class)
);
CREATE INDEX IF NOT EXISTS idx_t100_route_origin ON t100_route_agg (origin, year);

CREATE TABLE IF NOT EXISTS otp_airport_month (
    year INTEGER NOT NULL,
    month INTEGER NOT NULL,
    origin TEXT NOT NULL,
    n_flights INTEGER,
    n_cancelled INTEGER,
    n_diverted INTEGER,
    n_taxi_out_obs INTEGER,
    sum_taxi_out REAL,
    n_dep_delay_obs INTEGER,
    sum_dep_delay_min REAL,
    n_dep_del15 INTEGER,
    n_arr_delay_obs INTEGER,
    sum_arr_delay_min REAL,
    n_arr_del15 INTEGER,
    n_carriers INTEGER,
    source TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (year, month, origin)
);
CREATE INDEX IF NOT EXISTS idx_otp_origin ON otp_airport_month (origin, year);

CREATE TABLE IF NOT EXISTS ourairports (
    iata_code TEXT PRIMARY KEY,
    icao_ident TEXT,
    name TEXT,
    type TEXT,
    iso_country TEXT,
    iso_region TEXT,
    municipality TEXT,
    latitude_deg REAL,
    longitude_deg REAL,
    scheduled_service TEXT,
    source TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
"""


def connect(db_path=CACHE_DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def upsert_source_a_rows(conn, rows, source, fetched_at):
    conn.executemany(
        """
        INSERT INTO source_a_airport_month (
            origin_airport_code, year, month, reporting_month, origin_airport_name,
            total_departures, total_passengers, total_seats, total_load_factor,
            total_distance_flight_sm, domestic_departures, domestic_passengers,
            domestic_seats, domestic_load_factor, intl_departures,
            intl_passengers, intl_seats,
            source, fetched_at
        ) VALUES (
            :origin_airport_code, :year, :month, :reporting_month, :origin_airport_name,
            :total_departures, :total_passengers, :total_seats, :total_load_factor,
            :total_distance_flight_sm, :domestic_departures, :domestic_passengers,
            :domestic_seats, :domestic_load_factor, :intl_departures,
            :intl_passengers, :intl_seats,
            :source, :fetched_at
        )
        ON CONFLICT(origin_airport_code, year, month) DO UPDATE SET
            reporting_month=excluded.reporting_month,
            origin_airport_name=excluded.origin_airport_name,
            total_departures=excluded.total_departures,
            total_passengers=excluded.total_passengers,
            total_seats=excluded.total_seats,
            total_load_factor=excluded.total_load_factor,
            total_distance_flight_sm=excluded.total_distance_flight_sm,
            domestic_departures=excluded.domestic_departures,
            domestic_passengers=excluded.domestic_passengers,
            domestic_seats=excluded.domestic_seats,
            domestic_load_factor=excluded.domestic_load_factor,
            intl_departures=excluded.intl_departures,
            intl_passengers=excluded.intl_passengers,
            intl_seats=excluded.intl_seats,
            source=excluded.source,
            fetched_at=excluded.fetched_at
        """,
        [dict(r, source=source, fetched_at=fetched_at) for r in rows],
    )
    conn.commit()


def upsert_route_agg(conn, agg, source, fetched_at):
    """
    `agg` is keyed by (year, origin, dest, class) -> {"departures_performed",
    "distance"}, as returned by src.cache.t100_aggregate.collapse_to_storage_grain.
    """
    def gen():
        for (year, origin, dest, cls), v in agg.items():
            yield {
                "year": year, "origin": origin, "dest": dest, "class": cls,
                "departures_performed": v["departures_performed"], "distance": v["distance"],
                "source": source, "fetched_at": fetched_at,
            }
    conn.executemany(
        """
        INSERT INTO t100_route_agg (
            year, origin, dest, class, departures_performed, distance, source, fetched_at
        ) VALUES (
            :year, :origin, :dest, :class, :departures_performed, :distance, :source, :fetched_at
        )
        ON CONFLICT(year, origin, dest, class) DO UPDATE SET
            departures_performed=excluded.departures_performed,
            distance=excluded.distance,
            source=excluded.source,
            fetched_at=excluded.fetched_at
        """,
        list(gen()),
    )
    conn.commit()


def upsert_otp_rows(conn, agg, source, fetched_at):
    """
    `agg` is a dict keyed by (year, month, origin) -> sum/count fields, as
    returned by src.cache.otp_aggregate.aggregate_otp. Upsert replaces the
    row for a given key wholesale (not additive), so re-running the same
    month's ZIP is idempotent.
    """
    def gen():
        for (year, month, origin), v in agg.items():
            yield {
                "year": year, "month": month, "origin": origin,
                "n_flights": v["n_flights"], "n_cancelled": v["n_cancelled"], "n_diverted": v["n_diverted"],
                "n_taxi_out_obs": v["n_taxi_out_obs"], "sum_taxi_out": v["sum_taxi_out"],
                "n_dep_delay_obs": v["n_dep_delay_obs"], "sum_dep_delay_min": v["sum_dep_delay_min"],
                "n_dep_del15": v["n_dep_del15"],
                "n_arr_delay_obs": v["n_arr_delay_obs"], "sum_arr_delay_min": v["sum_arr_delay_min"],
                "n_arr_del15": v["n_arr_del15"], "n_carriers": v["n_carriers"],
                "source": source, "fetched_at": fetched_at,
            }
    conn.executemany(
        """
        INSERT INTO otp_airport_month (
            year, month, origin, n_flights, n_cancelled, n_diverted,
            n_taxi_out_obs, sum_taxi_out, n_dep_delay_obs, sum_dep_delay_min, n_dep_del15,
            n_arr_delay_obs, sum_arr_delay_min, n_arr_del15, n_carriers, source, fetched_at
        ) VALUES (
            :year, :month, :origin, :n_flights, :n_cancelled, :n_diverted,
            :n_taxi_out_obs, :sum_taxi_out, :n_dep_delay_obs, :sum_dep_delay_min, :n_dep_del15,
            :n_arr_delay_obs, :sum_arr_delay_min, :n_arr_del15, :n_carriers, :source, :fetched_at
        )
        ON CONFLICT(year, month, origin) DO UPDATE SET
            n_flights=excluded.n_flights,
            n_cancelled=excluded.n_cancelled,
            n_diverted=excluded.n_diverted,
            n_taxi_out_obs=excluded.n_taxi_out_obs,
            sum_taxi_out=excluded.sum_taxi_out,
            n_dep_delay_obs=excluded.n_dep_delay_obs,
            sum_dep_delay_min=excluded.sum_dep_delay_min,
            n_dep_del15=excluded.n_dep_del15,
            n_arr_delay_obs=excluded.n_arr_delay_obs,
            sum_arr_delay_min=excluded.sum_arr_delay_min,
            n_arr_del15=excluded.n_arr_del15,
            n_carriers=excluded.n_carriers,
            source=excluded.source,
            fetched_at=excluded.fetched_at
        """,
        list(gen()),
    )
    conn.commit()


def upsert_ourairports(conn, rows, source, fetched_at):
    conn.executemany(
        """
        INSERT INTO ourairports (
            iata_code, icao_ident, name, type, iso_country, iso_region, municipality,
            latitude_deg, longitude_deg, scheduled_service, source, fetched_at
        ) VALUES (
            :iata_code, :icao_ident, :name, :type, :iso_country, :iso_region, :municipality,
            :latitude_deg, :longitude_deg, :scheduled_service, :source, :fetched_at
        )
        ON CONFLICT(iata_code) DO UPDATE SET
            icao_ident=excluded.icao_ident,
            name=excluded.name,
            type=excluded.type,
            iso_country=excluded.iso_country,
            iso_region=excluded.iso_region,
            municipality=excluded.municipality,
            latitude_deg=excluded.latitude_deg,
            longitude_deg=excluded.longitude_deg,
            scheduled_service=excluded.scheduled_service,
            source=excluded.source,
            fetched_at=excluded.fetched_at
        """,
        [dict(r, source=source, fetched_at=fetched_at) for r in rows],
    )
    conn.commit()
