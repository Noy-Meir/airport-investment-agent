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
(large/medium/small, `src/scoring/signals.get_peer_group_ttm`), computed
once per tier and shared by every airport in it
(`src/scoring/score._tiered_group_data`). An airport that isn't classified
into any TTM tier (too small a national share even for the "small"
threshold) falls back to a peer group of itself only, which is below
`MIN_PEER_GROUP_SIZE` and therefore scores `"insufficient data"` -- this is
real, not a display artifact: ACK/BGR/ORH (New England, below the small-tier
threshold) genuinely have no statistically meaningful peer group yet.

`score_airport`, `rank_airports`, `compare_airports` and `sensitivity` all
share this rule -- none of them ever use their own `codes`/`scope` argument
as the peer group:

- `score_airport(code)`: scores `code` against its own tier.
- `rank_airports(scope)` / `sensitivity(scope)`: `scope` only *selects which
  airports to display/rank* (`{"region": "new_england"}`,
  `{"tier": "large"|"medium"|"small"}`, or `{"states": [...]}`). Each airport
  in `scope` is still scored against its own tier, which may differ airport
  to airport -- a New England ranking mixes a large-tier airport (BOS) with
  medium- and small-tier ones side by side, each scored against its real
  peers.
- `compare_airports(codes)`: same thing -- `codes` only picks which airports
  to display side by side. Comparing a large-tier and a medium-tier airport
  (e.g. SFO/LAX vs. SNA) does not shrink either one's peer group to the size
  of the comparison list; each keeps its own (>= 30-member) tier.
- An airport's z-scores and composite are therefore identical regardless of
  which of the four entry points computed them -- enforced by
  `test_z_score_identical_alone_in_compare_and_in_rank` in
  `tests/test_scoring.py`.

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
  same per-airport shape as `score_airport`, side by side, peer group =
  exactly the given codes.
- `sensitivity(scope, weight_sets=None, conn=None, end_month=None)` --
  re-ranks `scope` under every named weight set (default:
  `src/scoring/weights.SENSITIVITY_WEIGHT_SETS` -- `hypotheses` + the 4
  alternatives above). `result` includes `rankings` (per set), `rank_range`
  (`min_rank`/`max_rank`/`n_sets_scored` per airport across all sets), and
  `stable_top3` (airports in every set's top 3).

`conn=None` opens `data/cache.db` for the call and closes it after; pass an
existing connection to reuse one (e.g. in tests, against a fixture db).

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
- `MIN_SIGNALS_FOR_SCORE = 3` zero-weight edge case: if the >=3 available
  signals happen to carry zero total weight under a given weight set (e.g.
  `DROP_CONGESTION` when congestion is the only non-missing signal with
  nonzero weight -- not actually reachable with the current weight sets, but
  guarded anyway), that is also reported as `"insufficient data"` rather
  than dividing by zero.
