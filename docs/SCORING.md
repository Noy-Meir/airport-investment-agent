# Composite investment score

Implements the scoring layer on top of `src/scoring/signals.py`'s four TTM
signals: `growth`, `load_factor`, `demand_supply_gap`, `congestion`. Per
CLAUDE.md, every number is deterministic code output -- the LLM only narrates
it, never computes or asserts it.

## Weights

Named in `src/scoring/weights.py` as `HYPOTHESES` -- an analyst hypothesis
about relative importance, **not** fit to any outcome data:

| signal | weight | why |
|---|---|---|
| growth | 0.30 | passenger growth is the clearest forward demand signal available |
| load_factor | 0.25 | high utilization of existing capacity is a direct investment case |
| demand_supply_gap | 0.25 | demand outrunning capacity growth suggests underbuilt infrastructure |
| congestion | 0.20 | lowest weight because OTP coverage is incomplete (freighters, int'l excluded, and low for ANC-like airports) |

`src/scoring/weights.py` also defines the 4 alternative sets used by
`sensitivity()`: `EQUAL` (0.25 each), `GROWTH_HEAVY` (growth 0.55),
`CONGESTION_HEAVY` (congestion 0.55), `DROP_CONGESTION` (congestion 0.0).

## Composite formula

```
composite = sum(effective_weight[s] * z[s] for s in available_signals)
effective_weight[s] = weight[s] / sum(weight[s] for s in available_signals)
```

- `z[s]` is the robust z-score (`src/scoring/normalize.py`) of signal `s`
  within the airport's peer group: `(x - median) / (1.4826 * MAD)`, clipped
  to `[-3, 3]`.
- **>= 3 of 4 signals available**: drop the missing signal(s) and
  renormalize the remaining weights so they still sum to 1. A caveat on the
  score names which signal(s) were dropped.
- **< 3 signals available**: no composite score. `status` is
  `"insufficient data"`, `composite_score` is `None`, with a caveat
  explaining how many signals were available and why (missing raw value, or
  no peer-group z-score).
- A signal is "available" for an airport only if it has both a non-`None`
  raw value *and* a computable peer-group z-score (peer group >= 5 members
  with data, non-zero MAD -- see `src/scoring/normalize.py`). Neither
  condition is ever imputed around.

## One as_of window per airport

All four signals for an airport are resolved to the **same** TTM `as_of`
window: by default, the latest month cached in source A (T-100), not each
signal's own independent default. Without this, `congestion` would silently
default to OTP's own latest cached month, which can differ from source A's
(real cache: source A ends 2026-04, OTP ends 2026-07 -- see
docs/DECISIONS.md). `src/scoring/signals.compute_signals` resolves the
window once and passes it explicitly to all four signal functions; every
signal's `result` (when not `None`) exposes it as `as_of`.

`data/reference/buildability.json` (curated SNA/DCA/LGA/JFK/EWR constraints)
is surfaced on every score result as a separate `buildability` flag
(`has_constraints`, `constraints`, `note`). It is never weighted into the
composite -- a high-demand airport that is also slot/perimeter/MAP-capped
should score well on demand and separately show the constraint, not have the
constraint silently lower its number.

## Peer groups

**An airport's peer group is always its own TTM hub tier**
(large/medium/small/micro, `src/scoring/signals.get_peer_group_ttm`),
computed once per tier and shared by every airport in it
(`src/scoring/score._tiered_group_data`).

Hub tiers are share-of-national-passengers buckets (see "Hub tiers" /
"Micro tier" below), so by construction every tier except the smallest has
>= `MIN_PEER_GROUP_SIZE` members. An airport only falls back to a peer group
of itself (and therefore `"insufficient data"`) if it's below
`VOLUME_FLOOR_PAX` entirely -- i.e. not eligible for scoring at all, not
merely small. Every volume-floor-eligible airport has a real, >= 5-member
peer group.

`score_airport`, `rank_airports`, `compare_airports` and `sensitivity` all
share this rule -- none of them ever use their own `codes`/`scope` argument
as the peer group:

- `score_airport(code)`: scores `code` against its own tier.
- `rank_airports(scope)` / `sensitivity(scope)`: `scope` only *selects which
  airports to display/rank* (`{"region": "new_england"}`,
  `{"tier": "large"|"medium"|"small"|"micro"}`, or `{"states": [...]}`).
  Each airport in `scope` is still scored against its own tier, which may
  differ airport to airport -- a New England ranking mixes a large-tier
  airport (BOS) with medium/small/micro-tier ones side by side, each scored
  against its real peers.
- `compare_airports(codes)`: same thing -- `codes` only picks which airports
  to display side by side. Comparing a large-tier and a medium-tier airport
  (e.g. SFO/LAX vs. SNA) does not shrink either one's peer group to the size
  of the comparison list; each keeps its own (>= 30-member) tier.
- An airport's z-scores and composite are therefore identical regardless of
  which of the four entry points computed them -- enforced by
  `test_z_score_identical_alone_in_compare_and_in_rank` in
  `tests/test_scoring.py`.

### Micro tier

`src/reference/hub_tiers.compute_hub_tiers_ttm` adds a 4th, TTM-only tier:
**micro** = volume-floor-eligible (TTM total_passengers >= `VOLUME_FLOOR_PAX`)
but below every large/medium/small share threshold. Without it, an airport
like ACK/BGR/ORH (real New England airports above the volume floor but too
small a national share for "small") had no tier at all, fell back to a
1-member peer group, and came back `"insufficient data"` even though the
user explicitly asked about it (e.g. a New England ranking) -- an unscored
airport inside the user's requested scope is a worse answer than a scored
one with its peer group honestly stated as "micro". The CY2024-pinned
`compute_hub_tiers` (golden-test baseline) is unchanged -- it never computes
a micro tier, so `data/reference/hub_tiers.json` and the 31/34/76 golden
test stay exactly as they were.

## Confidence rules

Base confidence from signal count: `high` with all 4 signals, `medium` with
3. That base is then lowered (never raised) if any signal actually used in
the composite has:
- short underlying data (the signal's own envelope confidence is
  `medium`/`low` -- e.g. fewer than 12 months in a TTM window), or
- a degenerate or small peer group on that signal (the z-score envelope's
  confidence is `medium`/`low` -- MAD == 0, or fewer than
  `MIN_PEER_GROUP_SIZE` members with data).

`< 3` available signals is reported as `"insufficient data"` with confidence
`low`, not scored at all.

## API (`src/scoring/score.py`)

All four functions return the uniform envelope (`src/reference/envelope.py`):
`{result, method, caveats, source, confidence}`.

- `score_airport(code, weights=None, conn=None, end_month=None)` -- one
  airport's score against its TTM hub-tier peers. `result` includes
  `composite_score`, `status`, `confidence`, `peer_group`, `signals` (per
  signal: `raw`, `z`, `weight`, `effective_weight`, `contribution`,
  `caveats`), `why` (signals ordered by `|contribution|` descending),
  `buildability`.
- `rank_airports(scope, weights=None, top_n=10, conn=None, end_month=None)`
  -- ranks every airport in `scope`, highest composite first; airports with
  insufficient data are listed last with `rank=None`.
- `compare_airports(codes, weights=None, conn=None, end_month=None)` --
  same per-airport shape as `score_airport`, side by side; `codes` only
  selects which airports to display, each is scored against its own tier
  (see "Peer groups" above).
- `sensitivity(scope, weight_sets=None, conn=None, end_month=None)` --
  re-ranks `scope` under every named weight set (default:
  `src/scoring/weights.SENSITIVITY_WEIGHT_SETS` -- `hypotheses` + the 4
  alternatives above). `result` includes `rankings` (per set), `rank_range`
  (`min_rank`/`max_rank`/`n_sets_scored` per airport across all sets), and
  `stable_top3` (airports in every set's top 3).

`conn=None` opens `data/cache.db` for the call and closes it after; pass an
existing connection to reuse one (e.g. in tests, against a fixture db).

## Limitations

`demand_supply_gap_pct` is `passenger growth % - seat growth %`; a positive
value means passengers grew faster than seats, but the arithmetic can't
distinguish *why*. It reads the same whether passengers are growing into
flat capacity, or a carrier is cutting seats while passengers merely hold
flat or decline. The latter is a capacity pullback, not demand pressure,
and scoring it identically would reward the wrong airports. `src/scoring/
signals.get_demand_supply_gap_ttm` flags this case deterministically --
`gap_pct > 0` and `passenger_growth_pct <= 0` -- with a caveat on the
signal envelope and a `gap_driven_by_seat_cuts` boolean surfaced on every
per-airport score (`score_airport`/`rank_airports`/`compare_airports`,
and through the tool layer). This is a caveat only: it does not change
`demand_supply_gap_pct`, its z-score, its weight, or the composite score --
an analyst or the LLM narrating the result is expected to discount the
signal accordingly, not the scoring code.

## Definitions guessed, not specified up front

- `scope` dict shape (`{"region": ...} / {"tier": ...} / {"states": [...]}`)
  -- the task only said "scope is states, region or tier"; a single-key dict
  was chosen to keep the three unambiguous and reuse the exact code paths
  already in `src/reference/regions.py` / `src/reference/hub_tiers.py`.
- `score_airport`'s default peer group (TTM hub tier) -- not specified;
  chosen because it's the only peer grouping already defined in the
  codebase (`get_peer_group_ttm`) and matches "peer group" language already
  used there.
- "why" list ordering by `|contribution|` descending (not signed
  contribution, not raw z) -- picked so the most *impactful* signal leads
  regardless of whether it pushed the score up or down.
- Micro tier's floor is `VOLUME_FLOOR_PAX` (the existing scoring-eligibility
  floor, 100,000 TTM passengers) -- not a new, separately-tuned threshold.
  Chosen so "has a tier" and "is eligible to be scored at all" are the same
  condition; a micro tier with its own arbitrary floor would just move the
  same gap to a different boundary.
- `MIN_SIGNALS_FOR_SCORE = 3` zero-weight edge case: if the >=3 available
  signals happen to carry zero total weight under a given weight set (e.g.
  `DROP_CONGESTION` when congestion is the only non-missing signal with
  nonzero weight -- not actually reachable with the current weight sets, but
  guarded anyway), that is also reported as `"insufficient data"` rather
  than dividing by zero.

## Unmet demand: measured / inferred / unknown (src/analysis/unmet_demand.py)

There is no public BTS/FAA/OurAirports dataset that measures "unmet demand"
(travelers priced or scheduled out, fares, diversion, physical constraints)
directly. `get_unmet_demand_decomposition` / the `get_unmet_demand_breakdown`
tool therefore never return a single unmet-demand number -- they return
three sections, and the LLM must present all three:

- **measured** -- facts computed from cached data, each its own envelope
  (`{name, value, unit, as_of, source, peer_context}`), with `value=None`
  and a `reason` when a fact can't be computed. Includes `ttm_load_factor`,
  `ttm_passenger_vs_seat_growth_gap`, peak/high-load-factor-month facts,
  `ttm_delayed_share`, and `metro_context` (TTM passenger growth of sibling
  airports in the airport's metro group, per `data/reference/
  metro_areas.json` -- an analyst convention, not an official FAA/OMB/CBSA
  definition; airports with no metro entry get `value=None`, `reason="no
  metro grouping on file"`).
- **inferred** -- deterministic rules over measured peer-context z-scores
  (thresholds in `src/analysis/unmet_demand_constants.HYPOTHESES`, analyst
  judgment, not measured values). Always worded "is consistent with", never
  "proves" or "shows demand", and always lists `alternative_explanations`.
  One rule, `_metro_diversion_inference`, only fires when a sibling airport's
  TTM passenger growth exceeds this airport's own AND this airport
  independently shows capacity pressure; it is explicitly `strength: "weak"`
  because `UNKNOWABLE.diversion_to_other_metro_airports` (below) means this
  data can never establish diversion, only suggest it as one explanation
  among several.
- **unknown** -- a fixed list (`UNKNOWABLE`) of what public data
  structurally cannot answer here (fares, priced-out travelers, slot/gate
  constraints, international O&D demand, diversion to other metro
  airports).

There is no single number because each of the three sections answers a
different question (what we measured, what a pattern in those measurements
is consistent with, what we structurally cannot know) and collapsing them
into one score would hide exactly the caveats CLAUDE.md requires.
