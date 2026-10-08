"""
Unit tests for src/cache/otp_aggregate.py: sums/counts (not means), no
imputation of missing values, cancelled flights excluded from taxi/delay
observations, and n_carriers counted distinctly per (year, month, origin).
"""

import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.cache.otp_aggregate import OTPAggregateError, aggregate_otp

HEADER = (
    "Year,Month,Origin,Reporting_Airline,Cancelled,Diverted,TaxiOut,"
    "DepDelayMinutes,DepDel15,ArrDelayMinutes,ArrDel15\n"
)


def _make_zip(path, rows, header=HEADER, name="data.csv"):
    csv_text = header + "".join(rows)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(name, csv_text)
    return path


def test_sums_and_counts_not_means(tmp_path):
    zip_path = _make_zip(
        tmp_path / "t.zip",
        [
            "2026,7,TST,XX,0.00,0.00,20,10,0.00,8,0.00\n",
            "2026,7,TST,YY,0.00,0.00,30,20,1.00,15,0.00\n",
        ],
    )
    agg = aggregate_otp(str(zip_path))
    entry = agg[(2026, 7, "TST")]
    assert entry["n_flights"] == 2
    assert entry["n_cancelled"] == 0
    assert entry["n_taxi_out_obs"] == 2
    assert entry["sum_taxi_out"] == 50.0
    assert entry["n_dep_delay_obs"] == 2
    assert entry["sum_dep_delay_min"] == 30.0
    assert entry["n_dep_del15"] == 1
    assert entry["n_carriers"] == 2


def test_cancelled_flight_excluded_from_taxi_and_delay_obs(tmp_path):
    zip_path = _make_zip(
        tmp_path / "t.zip",
        [
            "2026,7,TST,XX,0.00,0.00,20,10,0.00,8,0.00\n",
            "2026,7,TST,XX,1.00,0.00,,,,,\n",  # cancelled flight: no taxi/delay values at all
        ],
    )
    agg = aggregate_otp(str(zip_path))
    entry = agg[(2026, 7, "TST")]
    assert entry["n_flights"] == 2
    assert entry["n_cancelled"] == 1
    assert entry["n_taxi_out_obs"] == 1
    assert entry["sum_taxi_out"] == 20.0
    assert entry["n_dep_delay_obs"] == 1


def test_missing_non_cancelled_value_excluded_from_obs_not_imputed(tmp_path):
    zip_path = _make_zip(
        tmp_path / "t.zip",
        [
            "2026,7,TST,XX,0.00,0.00,20,10,0.00,8,0.00\n",
            "2026,7,TST,XX,0.00,0.00,,,,,\n",  # not cancelled, but TaxiOut/delay fields are empty
        ],
    )
    agg = aggregate_otp(str(zip_path))
    entry = agg[(2026, 7, "TST")]
    assert entry["n_flights"] == 2
    assert entry["n_cancelled"] == 0
    # the second row's missing taxi/delay fields are excluded from the observation count, not treated as 0.
    assert entry["n_taxi_out_obs"] == 1
    assert entry["sum_taxi_out"] == 20.0
    assert entry["n_dep_delay_obs"] == 1
    assert entry["sum_dep_delay_min"] == 10.0


def test_missing_required_column_raises():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "t.zip")
        _make_zip(path, ["2026,7,TST,XX,0.00,0.00,20,10,0.00,8,0.00\n"], header="Year,Month,Origin\n")
        try:
            aggregate_otp(path)
            assert False, "expected OTPAggregateError"
        except OTPAggregateError as e:
            assert "missing required column" in str(e)
