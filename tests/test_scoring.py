"""
Tests for src/scoring/normalize.py (pure, hand-computed), src/scoring/
signals.py's NULL-congestion-on-low-coverage path, and src/scoring/score.py
(synthetic multi-airport db for most tests; one real-cache golden test that
skips loudly if data/cache.db isn't built).
"""

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.cache import db
from src.scoring.normalize import MIN_PEER_GROUP_SIZE, Z_CLIP, robust_z_scores
from src.scoring.score import _resolve_scope, compare_airports, rank_airports, score_airport, sensitivity
from src.scoring.signals import compute_signals, eligible_universe_ttm, get_congestion_signal_ttm, get_peer_group_ttm
from src.scoring.weights import SENSITIVITY_WEIGHT_SETS
from src.reference.hub_tiers import compute_hub_tiers, compute_hub_tiers_ttm

ENVELOPE_KEYS = {"result", "method", "caveats", "source", "confidence"}


# --- normalize.py -----------------------------------------------------------

def test_robust_z_hand_computed():
    # values: 10, 12, 14, 16, 18 -> median=14, abs devs: 4,2,0,2,4 -> MAD=2.
    # z(A=10) = (10-14)/(1.4826*2) = -4/2.9652 = -1.34890...
    values = {"A": 10.0, "B": 12.0, "C": 14.0, "D": 16.0, "E": 18.0}
    r = robust_z_scores(values)
    assert set(r.keys()) == ENVELOPE_KEYS
    assert r["result"]["A"] == pytest.approx(-1.3489, abs=1e-3)
    assert r["result"]["C"] == pytest.approx(0.0, abs=1e-9)
    assert r["result"]["E"] == pytest.approx(1.3489, abs=1e-3)
    assert r["confidence"] == "high"


def test_robust_z_clipping():
    # one extreme outlier must clip to +3, not exceed it.
    values = {"A": 10.0, "B": 11.0, "C": 10.5, "D": 10.2, "E": 1000.0}
    r = robust_z_scores(values)
    assert r["result"]["E"] == Z_CLIP
    assert any("clipped" in c for c in r["caveats"])


def test_robust_z_mad_zero():
    # four identical values + one different -> median=5, MAD=0.
    values = {"A": 5.0, "B": 5.0, "C": 5.0, "D": 5.0, "E": 9.0}
    r = robust_z_scores(values)
    assert r["result"] == {"A": 0.0, "B": 0.0, "C": 0.0, "D": 0.0, "E": 0.0}
    assert any("MAD is 0" in c for c in r["caveats"])
    assert r["confidence"] == "medium"


def test_robust_z_small_peer_group():
    values = {"A": 1.0, "B": 2.0, "C": 3.0}
    assert len(values) < MIN_PEER_GROUP_SIZE
    r = robust_z_scores(values)
    assert r["result"] is None
    assert any("peer group too small" in c for c in r["caveats"])
    assert r["confidence"] == "low"


def test_robust_z_excludes_null_values():
    values = {"A": 10.0, "B": 12.0, "C": 14.0, "D": 16.0, "E": 18.0, "F": None}
    r = robust_z_scores(values)
    assert "F" not in r["result"]
    assert any("excluded 1 peer" in c for c in r["caveats"])


# --- signals.py: NULL congestion on low coverage ----------------------------

@pytest.fixture
def low_coverage_conn(tmp_path):
    """
    Synthetic db where OTP flight counts are tiny relative to T-100
    domestic_departures (coverage well under 50%), across a full 12-month
    TTM window so get_congestion_ttm doesn't short-circuit on "no cached
    data" first.
    """
    path = str(tmp_path / "low_coverage.db")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    db.init_schema(conn)

    source_a_rows = []
    otp_agg = {}
    months = [(2025, m) for m in range(1, 13)]
    for year, month in months:
        source_a_rows.append({
            "origin_airport_code": "LOW", "year": year, "month": month,
            "reporting_month": f"{year}-{month:02d}-01T00:00:00.000", "origin_airport_name": "Low Coverage Field",
            "total_departures": 1000.0, "total_passengers": 50000.0, "total_seats": 60000.0,
            "total_load_factor": 83.3, "total_distance_flight_sm": 900.0,
            "domestic_departures": 1000.0, "domestic_passengers": 50000.0, "domestic_seats": 60000.0,
            "domestic_load_factor": 83.3, "intl_departures": 0.0, "intl_passengers": 0.0, "intl_seats": 0.0,
        })
        otp_agg[(year, month, "LOW")] = {
            "n_flights": 100, "n_cancelled": 0, "n_diverted": 0,
            "n_taxi_out_obs": 100, "sum_taxi_out": 1500.0,
            "n_dep_delay_obs": 100, "sum_dep_delay_min": 1000.0, "n_dep_del15": 20,
            "n_arr_delay_obs": 100, "sum_arr_delay_min": 900.0, "n_arr_del15": 18,
            "n_carriers": 1,
        }
    db.upsert_source_a_rows(conn, source_a_rows, "synthetic", "2026-01-01T00:00:00Z")
    db.upsert_otp_rows(conn, otp_agg, "synthetic", "2026-01-01T00:00:00Z")
    yield conn
    conn.close()


def test_congestion_null_for_low_otp_coverage(low_coverage_conn):
    # coverage = 100*12 OTP flights / 1000*12 T-100 domestic_departures = 10% < 50% floor.
    r = get_congestion_signal_ttm(low_coverage_conn, "LOW")
    assert set(r.keys()) == ENVELOPE_KEYS
    assert r["result"] is None
    assert any("low OTP coverage" in c and "10.0%" in c for c in r["caveats"])
    assert r["confidence"] == "low"


# --- score.py: synthetic multi-airport peer group ---------------------------

# (pax_growth_pct, seat_growth_pct, load_factor, dep_delay_ge15_pct) per airport.
# Chosen to spread every signal across the 6-airport peer group (MAD != 0) and
# to give each airport a distinct, hand-traceable demand_supply_gap = pax - seat.
SCORE_FIXTURE_AIRPORTS = {
    "AAA": (20.0, 10.0, 0.85, 30.0),
    "BBB": (10.0, 10.0, 0.75, 25.0),
    "CCC": (5.0, 15.0, 0.65, 20.0),
    "DDD": (0.0, -5.0, 0.80, 22.0),
    "EEE": (-5.0, 0.0, 0.70, 28.0),
    "FFF": (15.0, 5.0, 0.90, 18.0),
}
PRIOR_ANNUAL_PAX = 1_200_000.0
MONTHLY_DOMESTIC_DEPARTURES = 500.0


@pytest.fixture
def score_fixture_conn():
    """
    Synthetic db with 6 well-formed airports (2024 + 2025, full 24 months,
    100% OTP/T-100 coverage) so growth/load_factor/demand_supply_gap/
    congestion are all computable and spread across the peer group -- plus
    DDD missing OTP (renormalize to 3 signals) and GGG missing all 2024 data
    (fewer than 3 signals, "insufficient data").
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.init_schema(conn)

    source_a_rows = []
    otp_agg = {}
    for code, (pax_growth_pct, seat_growth_pct, load_factor, delay_pct) in SCORE_FIXTURE_AIRPORTS.items():
        annual_pax_2025 = PRIOR_ANNUAL_PAX * (1 + pax_growth_pct / 100.0)
        annual_seats_2025 = annual_pax_2025 / load_factor
        annual_seats_2024 = annual_seats_2025 / (1 + seat_growth_pct / 100.0)
        monthly_pax = {2024: PRIOR_ANNUAL_PAX / 12.0, 2025: annual_pax_2025 / 12.0}
        monthly_seats = {2024: annual_seats_2024 / 12.0, 2025: annual_seats_2025 / 12.0}
        n_dep_del15 = round(delay_pct / 100.0 * MONTHLY_DOMESTIC_DEPARTURES)

        for year in (2024, 2025):
            for month in range(1, 13):
                source_a_rows.append({
                    "origin_airport_code": code, "year": year, "month": month,
                    "reporting_month": f"{year}-{month:02d}-01T00:00:00.000", "origin_airport_name": code,
                    "total_departures": MONTHLY_DOMESTIC_DEPARTURES, "total_passengers": monthly_pax[year],
                    "total_seats": monthly_seats[year], "total_load_factor": load_factor * 100,
                    "total_distance_flight_sm": 900.0,
                    "domestic_departures": MONTHLY_DOMESTIC_DEPARTURES, "domestic_passengers": monthly_pax[year],
                    "domestic_seats": monthly_seats[year], "domestic_load_factor": load_factor * 100,
                    "intl_departures": 0.0, "intl_passengers": 0.0, "intl_seats": 0.0,
                })
                if code != "DDD":
                    otp_agg[(year, month, code)] = {
                        "n_flights": MONTHLY_DOMESTIC_DEPARTURES, "n_cancelled": 0, "n_diverted": 0,
                        "n_taxi_out_obs": MONTHLY_DOMESTIC_DEPARTURES, "sum_taxi_out": MONTHLY_DOMESTIC_DEPARTURES * 15,
                        "n_dep_delay_obs": MONTHLY_DOMESTIC_DEPARTURES, "sum_dep_delay_min": MONTHLY_DOMESTIC_DEPARTURES * 10,
                        "n_dep_del15": n_dep_del15,
                        "n_arr_delay_obs": MONTHLY_DOMESTIC_DEPARTURES, "sum_arr_delay_min": MONTHLY_DOMESTIC_DEPARTURES * 9,
                        "n_arr_del15": n_dep_del15, "n_carriers": 2,
                    }

    # GGG: only 2025 data, no 2024 at all, no OTP -- growth/gap/congestion all
    # unavailable, leaving only load_factor -- fewer than 3 signals available.
    ggg_load_factor = 0.72
    annual_pax_2025 = PRIOR_ANNUAL_PAX
    annual_seats_2025 = annual_pax_2025 / ggg_load_factor
    for month in range(1, 13):
        source_a_rows.append({
            "origin_airport_code": "GGG", "year": 2025, "month": month,
            "reporting_month": f"2025-{month:02d}-01T00:00:00.000", "origin_airport_name": "GGG",
            "total_departures": MONTHLY_DOMESTIC_DEPARTURES, "total_passengers": annual_pax_2025 / 12.0,
            "total_seats": annual_seats_2025 / 12.0, "total_load_factor": ggg_load_factor * 100,
            "total_distance_flight_sm": 900.0,
            "domestic_departures": MONTHLY_DOMESTIC_DEPARTURES, "domestic_passengers": annual_pax_2025 / 12.0,
            "domestic_seats": annual_seats_2025 / 12.0, "domestic_load_factor": ggg_load_factor * 100,
            "intl_departures": 0.0, "intl_passengers": 0.0, "intl_seats": 0.0,
        })

    db.upsert_source_a_rows(conn, source_a_rows, "synthetic", "2026-01-01T00:00:00Z")
    db.upsert_otp_rows(conn, otp_agg, "synthetic", "2026-01-01T00:00:00Z")
    yield conn
    conn.close()


def test_score_airport_renormalizes_missing_signal(score_fixture_conn):
    """DDD has no OTP data at all -- congestion must be dropped and the other 3 signals renormalized."""
    r = score_airport("DDD", conn=score_fixture_conn, end_month=(2025, 12))
    assert set(r.keys()) == ENVELOPE_KEYS
    result = r["result"]
    assert result["status"] == "scored"
    assert result["composite_score"] is not None
    assert result["signals"]["congestion"]["z"] is None
    assert result["signals"]["congestion"]["contribution"] is None
    for name in ("growth", "load_factor", "demand_supply_gap"):
        assert result["signals"][name]["z"] is not None
        assert result["signals"][name]["contribution"] is not None
    # renormalized weights (of the 3 available) must sum to 1.
    available_weight = sum(result["signals"][n]["effective_weight"] for n in ("growth", "load_factor", "demand_supply_gap"))
    assert available_weight == pytest.approx(1.0, abs=1e-6)
    assert any("renormalized" in c for c in r["caveats"])
    assert any("congestion" in c and "excluded" in c for c in r["caveats"])
    assert result["confidence"] == "medium"
    assert len(result["why"]) == 3
    assert {w["signal"] for w in result["why"]} == {"growth", "load_factor", "demand_supply_gap"}
    # why is ordered by |contribution| descending.
    contributions = [abs(w["contribution"]) for w in result["why"]]
    assert contributions == sorted(contributions, reverse=True)


def test_score_airport_insufficient_data_below_three_signals(score_fixture_conn):
    """GGG has only load_factor computable (no prior-year data, no OTP) -- fewer than 3 signals, no score."""
    r = score_airport("GGG", conn=score_fixture_conn, end_month=(2025, 12))
    result = r["result"]
    assert result["status"] == "insufficient data"
    assert result["composite_score"] is None
    assert result["confidence"] == "low"
    assert result["why"] == []
    assert any("insufficient data" in c for c in r["caveats"])


def test_score_airport_all_four_signals_scored(score_fixture_conn):
    """AAA has every signal available -- full composite, high confidence, no renormalization caveat."""
    r = score_airport("AAA", conn=score_fixture_conn, end_month=(2025, 12))
    result = r["result"]
    assert result["status"] == "scored"
    assert all(result["signals"][n]["z"] is not None for n in ("growth", "load_factor", "demand_supply_gap", "congestion"))
    assert result["confidence"] == "high"
    assert not any("renormalized" in c for c in r["caveats"])
    # buildability is reported but is not folded into the composite.
    assert "buildability" in result
    assert result["buildability"]["has_constraints"] is False


def test_compute_signals_shares_one_as_of_window(score_fixture_conn):
    """
    All four signals must resolve to the same as_of window -- congestion
    used to default to OTP's own latest cached month independently of
    source-A's latest month; compute_signals must force the same window on
    all four (see src/scoring/signals.py compute_signals).
    """
    signals = compute_signals(score_fixture_conn, "AAA", end_month=(2025, 12))
    assert signals["as_of"] == "2025-12"
    for name in ("growth", "load_factor", "demand_supply_gap", "congestion"):
        assert signals[name]["result"] is not None, name
        assert signals[name]["result"]["as_of"] == "2025-12", name


def test_compute_signals_as_of_consistent_real_cache(real_conn):
    """
    (golden) Against the real cache, source-A's latest month and OTP's
    latest month differ (source-A ends 2026-04, OTP ends 2026-07 -- see
    docs/DECISIONS.md). compute_signals must still resolve every signal to
    the same as_of, overriding congestion's own OTP-latest-month default.
    """
    signals = compute_signals(real_conn, "SFO")
    as_of = signals["as_of"]
    assert signals["growth"]["result"]["as_of"] == as_of
    assert signals["load_factor"]["result"]["as_of"] == as_of
    assert signals["demand_supply_gap"]["result"]["as_of"] == as_of
    assert any(f"ending {as_of}" in c for c in signals["congestion"]["caveats"])


def test_score_airport_determinism(score_fixture_conn):
    r1 = score_airport("AAA", conn=score_fixture_conn, end_month=(2025, 12))
    r2 = score_airport("AAA", conn=score_fixture_conn, end_month=(2025, 12))
    assert r1 == r2


def test_rank_airports_shape_and_order(score_fixture_conn):
    r = rank_airports({"tier": "large"}, conn=score_fixture_conn, end_month=(2025, 12), top_n=10)
    result = r["result"]
    ranked = result["ranked"]
    scored = [a for a in ranked if a["status"] == "scored"]
    composites = [a["composite_score"] for a in scored]
    assert composites == sorted(composites, reverse=True)
    assert [a["rank"] for a in scored] == list(range(1, len(scored) + 1))
    unscored = [a for a in ranked if a["status"] != "scored"]
    assert all(a["rank"] is None for a in unscored)
    # GGG has < 3 signals so must be unscored; the other 6 synthetic airports should all score.
    assert "GGG" in {a["airport"] for a in unscored}


def test_compare_airports_peer_group_is_not_the_compared_list(score_fixture_conn):
    """
    AAA/BBB/CCC all share the same (6-member) synthetic tier -- comparing
    just these 3 must NOT shrink their peer group down to 3 and must score
    normally, because z-scoring always uses each airport's own TTM hub tier.
    """
    r = compare_airports(["AAA", "BBB", "CCC"], conn=score_fixture_conn, end_month=(2025, 12))
    assert r["result"]["codes"] == ["AAA", "BBB", "CCC"]
    compared = r["result"]["compared"]
    assert len(compared) == 3
    for a in compared:
        assert a["status"] == "scored"
        assert len(a["peer_group"]["codes"]) > 3
    assert not any("peer group has only" in c for c in r["caveats"])


def test_compare_airports_across_different_tiers_real_cache(real_conn):
    """
    (fix for the reported bug) SFO/LAX are large-tier, SNA is medium-tier.
    Comparing all three together must score every one of them against its
    OWN tier, not against this 3-airport display list.
    """
    r = compare_airports(["SFO", "LAX", "SNA"], conn=real_conn)
    compared = {a["airport"]: a for a in r["result"]["compared"]}
    assert compared["SFO"]["tier"] == "large"
    assert compared["LAX"]["tier"] == "large"
    assert compared["SNA"]["tier"] == "medium"
    for code in ("SFO", "LAX", "SNA"):
        assert compared[code]["status"] == "scored"
        assert len(compared[code]["peer_group"]["codes"]) >= MIN_PEER_GROUP_SIZE


def test_z_score_identical_alone_in_compare_and_in_rank(score_fixture_conn):
    """An airport's z-scores must not depend on which entry point computed them."""
    solo = score_airport("AAA", conn=score_fixture_conn, end_month=(2025, 12))["result"]
    compared = compare_airports(["AAA", "BBB", "CCC"], conn=score_fixture_conn, end_month=(2025, 12))["result"]
    aaa_compared = next(a for a in compared["compared"] if a["airport"] == "AAA")
    ranked = rank_airports({"tier": "large"}, conn=score_fixture_conn, end_month=(2025, 12))["result"]
    aaa_ranked = next(a for a in ranked["ranked"] if a["airport"] == "AAA")

    for name in ("growth", "load_factor", "demand_supply_gap", "congestion"):
        z_solo = solo["signals"][name]["z"]
        assert aaa_compared["signals"][name]["z"] == z_solo
        assert aaa_ranked["signals"][name]["z"] == z_solo
    assert aaa_compared["composite_score"] == solo["composite_score"]
    assert aaa_ranked["composite_score"] == solo["composite_score"]


def test_sensitivity_shape(score_fixture_conn):
    r = sensitivity({"tier": "large"}, conn=score_fixture_conn, end_month=(2025, 12))
    result = r["result"]
    assert set(result["weight_sets"].keys()) == set(SENSITIVITY_WEIGHT_SETS.keys())
    assert set(result["rankings"].keys()) == set(SENSITIVITY_WEIGHT_SETS.keys())
    for set_name, entries in result["rankings"].items():
        ranks = [e["rank"] for e in entries]
        assert ranks == list(range(1, len(entries) + 1))
    # GGG never has >= 3 signals, so it never scores under any weight set.
    assert result["rank_range"]["GGG"]["n_sets_scored"] == 0
    assert result["rank_range"]["GGG"]["min_rank"] is None
    # an airport that does score should have a rank in every weight set.
    assert result["rank_range"]["AAA"]["n_sets_scored"] == len(SENSITIVITY_WEIGHT_SETS)
    assert isinstance(result["stable_top3"], list)
    assert len(result["stable_top3"]) <= 3
    for entries in result["rankings"].values():
        top3 = {e["airport"] for e in entries[:3]}
        assert set(result["stable_top3"]) <= top3


# --- real-cache golden test (skips loudly if data/cache.db isn't built) -----

def test_new_england_scope_and_anc_congestion_real_cache(real_conn):
    """
    (golden) New England scope resolves to the same 10 airports as
    docs/DECISIONS.md / tests/test_golden.py, and ANC's congestion signal is
    NULL with the documented low-OTP-coverage reason (ANC's OTP/T-100
    coverage is ~25.6%, well under CONGESTION_MIN_COVERAGE_PCT -- see
    docs/DECISIONS.md "Congestion snapshot").
    """
    expected = {"BOS", "BDL", "PVD", "PWM", "BTV", "MHT", "HVN", "BGR", "ORH", "ACK"}
    codes, basis, caveats, source = _resolve_scope(real_conn, {"region": "new_england"}, None)
    assert set(codes) == expected

    r = score_airport("ANC", conn=real_conn)
    congestion = r["result"]["signals"]["congestion"]
    assert congestion["raw"] is None
    assert congestion["z"] is None
    assert any("low OTP coverage" in c for c in congestion["caveats"])


# --- micro tier: every volume-floor-eligible airport must get a peer group --

def test_cy2024_hub_tiers_unchanged_no_micro_key(real_conn):
    """The CY2024-pinned baseline (compute_hub_tiers) must be untouched: no 'micro' key, same 31/34/76."""
    cy = compute_hub_tiers(real_conn)
    assert "micro" not in cy["result"]["tiers"]
    assert "micro" not in cy["result"]["counts"]
    assert cy["result"]["counts"] == {"large": 31, "medium": 34, "small": 76}


def test_ttm_tier_sizes_sum_to_eligible_count_real_cache(real_conn):
    """(golden) large + medium + small + micro must equal the volume-floor-eligible TTM universe, exactly."""
    tiers = compute_hub_tiers_ttm(real_conn)
    elig = eligible_universe_ttm(real_conn)
    counts = tiers["result"]["counts"]
    assert set(counts.keys()) == {"large", "medium", "small", "micro"}
    assert sum(counts.values()) == elig["result"]["count"]


def test_every_new_england_airport_has_a_ttm_tier_real_cache(real_conn):
    """(golden) Every one of the 10 New England airports -- including the small ones below the small-tier threshold -- must land in some tier."""
    expected = {"BOS", "BDL", "PVD", "PWM", "BTV", "MHT", "HVN", "BGR", "ORH", "ACK"}
    tiers = compute_hub_tiers_ttm(real_conn)
    tier_by_code = {m["code"]: t for t, members in tiers["result"]["tiers"].items() for m in members}
    missing = expected - set(tier_by_code)
    assert not missing, f"airports with no TTM tier at all: {missing}"
    for code in expected:
        r = get_peer_group_ttm(real_conn, code)
        assert r["result"] is not None, f"{code} has no peer group"


def test_micro_tier_peer_group_at_least_five_real_cache(real_conn):
    """(golden) The micro tier itself must be a usable peer group (src/scoring/normalize.MIN_PEER_GROUP_SIZE)."""
    tiers = compute_hub_tiers_ttm(real_conn)
    assert len(tiers["result"]["tiers"]["micro"]) >= MIN_PEER_GROUP_SIZE
