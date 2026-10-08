"""
Aggregates a raw BTS On-Time Performance (OTP) monthly CSV (inside the ZIP
downloaded by src.clients.otp) down to one row per (year, month, origin),
as SUMS AND COUNTS -- never means -- so means can be recomputed downstream
without re-reading the raw data and without losing observation counts.

Streams the CSV row-by-row out of the ZIP -- never extracts it, never holds
the raw rows or decoded CSV text in memory, only the aggregated dict
(bounded by the number of distinct (year, month, origin) combinations, not
row count). Never loaded into the LLM's context; callers only see the
aggregate.

No imputation: a missing/empty value for a given field is NOT counted as an
observation for that field (it is excluded from both the observation count
and the sum, not treated as 0). Cancelled flights are excluded from taxi-out
and delay observations entirely (BTS leaves those fields blank for
cancelled flights anyway, but this is enforced explicitly, not assumed).

OTP column names have varied across vintages (e.g. DepDelay vs.
DepDelayMinutes); `_resolve_columns` picks the first present candidate for
each field, case-insensitively, from the CSV's actual header.
"""

import csv
import io
import zipfile

# For each logical field, candidate CSV column names in preference order.
COLUMN_CANDIDATES = {
    "year": ["Year"],
    "month": ["Month"],
    "origin": ["Origin", "OriginAirportCode"],
    "carrier": ["Reporting_Airline", "OP_UNIQUE_CARRIER", "UniqueCarrier"],
    "cancelled": ["Cancelled"],
    "diverted": ["Diverted"],
    "taxi_out": ["TaxiOut"],
    "dep_delay": ["DepDelayMinutes", "DepDelay"],
    "dep_del15": ["DepDel15"],
    "arr_delay": ["ArrDelayMinutes", "ArrDelay"],
    "arr_del15": ["ArrDel15"],
}

SUM_COUNT_FIELDS = [
    "n_flights", "n_cancelled", "n_diverted",
    "n_taxi_out_obs", "sum_taxi_out",
    "n_dep_delay_obs", "sum_dep_delay_min", "n_dep_del15",
    "n_arr_delay_obs", "sum_arr_delay_min", "n_arr_del15",
]


class OTPAggregateError(Exception):
    pass


def _resolve_columns(fieldnames):
    by_lower = {fn.lower(): fn for fn in fieldnames}
    resolved = {}
    missing = []
    for logical, candidates in COLUMN_CANDIDATES.items():
        found = next((by_lower[c.lower()] for c in candidates if c.lower() in by_lower), None)
        if found is None:
            missing.append(logical)
        resolved[logical] = found
    if missing:
        raise OTPAggregateError(
            f"CSV header is missing required column(s) for: {missing} "
            f"(looked for {[COLUMN_CANDIDATES[m] for m in missing]}); "
            f"actual header: {fieldnames}"
        )
    return resolved


def _is_true(raw):
    """OTP boolean-ish fields (Cancelled, Diverted, DepDel15, ArrDel15) are '1.00'/'0.00' strings."""
    return raw not in (None, "") and float(raw) == 1.0


def _num_or_none(raw):
    if raw in (None, ""):
        return None
    return float(raw)


def aggregate_otp(zip_path):
    """
    Returns a dict keyed by (year, month, origin) -> {field: value, ...}
    with the SUM_COUNT_FIELDS above, plus "carriers" (a set of distinct
    reporting-carrier codes seen for that key -- converted to n_carriers by
    the caller once no more rows will be merged in, e.g. after upserting).
    """
    try:
        z = zipfile.ZipFile(zip_path)
    except (FileNotFoundError, zipfile.BadZipFile) as e:
        raise OTPAggregateError(f"cannot open {zip_path}: {e}") from e

    try:
        data_name = next(
            n for n in z.namelist()
            if n.lower().endswith(".csv") and "readme" not in n.lower()
        )
    except StopIteration:
        raise OTPAggregateError(f"{zip_path}: no data CSV found in ZIP") from None

    agg = {}
    with z.open(data_name) as f:
        reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
        cols = _resolve_columns(reader.fieldnames)
        for row in reader:
            key = (int(row[cols["year"]]), int(row[cols["month"]]), row[cols["origin"]])
            entry = agg.setdefault(key, {f: 0 for f in SUM_COUNT_FIELDS})
            entry.setdefault("carriers", set())

            carrier = row.get(cols["carrier"])
            if carrier:
                entry["carriers"].add(carrier)

            entry["n_flights"] += 1
            cancelled = _is_true(row.get(cols["cancelled"]))
            diverted = _is_true(row.get(cols["diverted"]))
            if cancelled:
                entry["n_cancelled"] += 1
            if diverted:
                entry["n_diverted"] += 1

            if cancelled:
                # cancelled flights never had a taxi-out or delay observation -- excluded entirely.
                continue

            taxi_out = _num_or_none(row.get(cols["taxi_out"]))
            if taxi_out is not None:
                entry["n_taxi_out_obs"] += 1
                entry["sum_taxi_out"] += taxi_out

            dep_delay = _num_or_none(row.get(cols["dep_delay"]))
            if dep_delay is not None:
                entry["n_dep_delay_obs"] += 1
                entry["sum_dep_delay_min"] += dep_delay
                if _is_true(row.get(cols["dep_del15"])):
                    entry["n_dep_del15"] += 1

            arr_delay = _num_or_none(row.get(cols["arr_delay"]))
            if arr_delay is not None:
                entry["n_arr_delay_obs"] += 1
                entry["sum_arr_delay_min"] += arr_delay
                if _is_true(row.get(cols["arr_del15"])):
                    entry["n_arr_del15"] += 1

    for entry in agg.values():
        entry["n_carriers"] = len(entry.pop("carriers"))

    return agg
