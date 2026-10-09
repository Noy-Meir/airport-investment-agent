# Example Answers

These answers are real, unedited output from the built-in rules interpreter
(no AI model, no API cost) run against the shipped `data/cache.db`. The AI
model path answers the same questions using the same underlying tool
numbers, just with freer wording.

## Which New England airports look like the best expansion candidates?

```
Ranking for scope **New England region** by composite investment score -- a screening aid based on a hypothesis-driven set of scoring weights, not a verdict.

| Rank | Airport | Score | Confidence | Flag |
|---|---|---|---|---|
| 1 | HVN | 0.93 | medium | - |
| 2 | BTV | 0.81 | medium | gap driven by seat cuts |
| 3 | BDL | 0.71 | medium | gap driven by seat cuts |
| 4 | BGR | 0.63 | medium | - |
| 5 | BOS | 0.43 | medium | - |
| 6 | PWM | 0.34 | medium | - |
| 7 | ORH | 0.08 | medium | - |
| 8 | PVD | -0.04 | medium | - |
| 9 | MHT | -0.09 | medium | - |
| 10 | ACK | -1.26 | medium | - |

Every row above: buildability: no entry on file -- unknown constraints.
Peer group: each airport is scored against its own TTM hub-tier peers, not against the other airports in this result -- large tier (peer group size 30); medium tier (peer group size 35); micro tier (peer group size 89); small tier (peer group size 80). Peer comparisons are against other airports in the same TTM hub-size tier, not the full national set.
Data window: TTM (trailing twelve months), data through 2026-04.
Confidence: high.
Caveats:
- volume floor 100,000 is provisional pending trailing-12-month confirmation (see docs/DECISIONS.md)
- HVN: congestion: excluded from composite -- signal not computable for this airport
- HVN: renormalized weights over 3/4 available signals (dropped congestion) -- weight/contribution above already reflect the renormalized weight
- HVN: trailing 12 months ending 2026-04

Answered by the built-in rules interpreter (no AI model).
```

## Now re-rank that with more weight on growth and less on congestion.

```
Sensitivity of the ranking for scope **New England region** to 5 alternative weight set(s) (congestion_heavy, drop_congestion, equal, growth_heavy, hypotheses), base weighting 'hypotheses' shown below.

| Rank | Airport | Score | Confidence | Flag |
|---|---|---|---|---|
| 1 | HVN | 0.93 | rank 1-1 across weight sets | - |
| 2 | BTV | 0.81 | rank 2-4 across weight sets | - |
| 3 | BDL | 0.71 | rank 2-4 across weight sets | - |
| 4 | BGR | 0.63 | rank 2-4 across weight sets | - |
| 5 | BOS | 0.43 | rank 5-6 across weight sets | - |
| 6 | PWM | 0.34 | rank 5-8 across weight sets | - |
| 7 | ORH | 0.08 | rank 6-9 across weight sets | - |
| 8 | PVD | -0.04 | rank 7-9 across weight sets | - |
| 9 | MHT | -0.09 | rank 7-9 across weight sets | - |
| 10 | ACK | -1.26 | rank 10-10 across weight sets | - |

Every row above: buildability: no entry on file -- unknown constraints.
Stable across every weight set tried: HVN remain in the top 3 regardless of weighting.
Data window: TTM (trailing twelve months); this result does not carry an explicit as_of date.
Confidence: medium.
Caveats:
- volume floor 100,000 is provisional pending trailing-12-month confirmation (see docs/DECISIONS.md)

Answered by the built-in rules interpreter (no AI model).
```

## Why did HVN rank above BTV?

```
Side-by-side composite scores for HVN, BTV -- each airport is scored against its own TTM hub-tier peers, not against the other airports in this list.

| Rank | Airport | Score | Confidence | Flag |
|---|---|---|---|---|
| 1 | HVN | 0.93 | medium | - |
| 2 | BTV | 0.81 | medium | gap driven by seat cuts |

Every row above: buildability: no entry on file -- unknown constraints.
Peer group: each airport is scored against its own TTM hub-tier peers, not against the other airports in this result -- small tier (peer group size 80). Peer comparisons are against other airports in the same TTM hub-size tier, not the full national set.
Data window: TTM (trailing twelve months), data through 2026-04.
Confidence: high.
Caveats:
- HVN: congestion: excluded from composite -- signal not computable for this airport
- HVN: renormalized weights over 3/4 available signals (dropped congestion) -- weight/contribution above already reflect the renormalized weight
- HVN: trailing 12 months ending 2026-04
- BTV: congestion signal confidence is medium

Answered by the built-in rules interpreter (no AI model).
```

## How does congestion at LAX compare to Santa Ana (SNA)?

```
TTM flight counts and % of domestic departures delayed >=15min for LAX, SNA:

| Airport | Flights | % delayed >=15min | OTP/T-100 coverage | Coverage window | Low coverage |
|---|---|---|---|---|---|
| LAX | 190,106 | 20.2% | 91.9% | 9 of 12 months | no |
| SNA | 45,231 | 20.2% | 90.0% | 9 of 12 months | no |

Data window: TTM (trailing twelve months), data through 2026-07.
Confidence: high.
Caveats:
- congestion is domestic departures only (OTP covers domestic reporting carriers; international departures are not measured)
- LAX: OTP covers domestic reporting carriers only -- no freighters, no international flights
- LAX: coverage computed over 9 overlapping month(s) only: 2025-08, 2025-09, 2025-10, 2025-11, 2025-12, 2026-01, 2026-02, 2026-03, 2026-04, not the full 12-month window
- LAX: coverage = OTP flights / T-100 domestic_departures; T-100 domestic_departures includes freighters and carriers that do not report to OTP, so this ratio understates how much passenger traffic OTP actually covers
- SNA: OTP covers domestic reporting carriers only -- no freighters, no international flights
- SNA: coverage computed over 9 overlapping month(s) only: 2025-08, 2025-09, 2025-10, 2025-11, 2025-12, 2026-01, 2026-02, 2026-03, 2026-04, not the full 12-month window
- SNA: coverage = OTP flights / T-100 domestic_departures; T-100 domestic_departures includes freighters and carriers that do not report to OTP, so this ratio understates how much passenger traffic OTP actually covers

Answered by the built-in rules interpreter (no AI model).
```

## What percent of flights out of Anchorage are long-haul?

```
ANC long-haul departure share, year 2025 -- distance threshold(s) in miles: 2,000, 2,500, 3,000 (a caller-supplied parameter, not an official FAA/BTS definition).

- at 2,000 mi: passenger flights: 13.8%; all flights (passenger + cargo): 47.4%
- at 2,500 mi: passenger flights: 9.8%; all flights (passenger + cargo): 43.2%
- at 3,000 mi: passenger flights: 2.8%; all flights (passenger + cargo): 31.0%

Data window: calendar year 2025, BTS T-100 Segment (All Carriers), route level (FMG).
Confidence: high.
Caveats:
- "long-haul" has no official FAA/BTS distance threshold -- thresholds_mi is a caller-supplied parameter, not an established fact
- passenger: class_group=passenger (F, L), weighted by DEPARTURES_PERFORMED; included 29 departures on 0-mile same-airport (origin == dest) combos as short-haul
- all: class_group=all (F, G, L, P), weighted by DEPARTURES_PERFORMED; included 242 departures on 0-mile same-airport (origin == dest) combos as short-haul

Answered by the built-in rules interpreter (no AI model).
```

## What's the unmet demand at SFO, and what's driving it?

```
Unmet-demand breakdown for SFO -- there is no single 'unmet demand' number; this is a three-part decomposition of public-data facts.

Measured:
- ttm_load_factor: 0.826 (above its large-hub peers)
- ttm_passenger_vs_seat_growth_gap: -0.02 pp (well above its large-hub peers)
- peak_month_load_factor: 0.894
- months_load_factor_ge_threshold: 1 months
- ttm_delayed_share: 20.2% (below its large-hub peers)
- metro_context (San Francisco Bay Area): OAK growth -13.9%; SJC growth -11.3%
Peer comparisons are against other airports in the same TTM hub-size tier, not the full national set.

Inferred (hypotheses, each 'consistent with' the measured facts -- never proof):
- the airport fills its seats at a high level relative to peers (raw load factor 0.8255), but passenger growth is not outrunning seat growth in absolute terms (raw demand_supply_gap -0.02 percentage points); this is consistent with a mature, well-utilized airport and is NOT evidence of growing unmet demand
  Confidence in this inference: moderate.
  Alternative explanations: structural constraints that cap both seats and passengers; seasonality

Unknown (what this public data structurally cannot tell us here):
- passengers_priced_or_scheduled_out: BTS data only records flights and passengers that actually flew -- travelers who did not fly because of fare levels or schedule availability leave no record in any source this agent reads.
- fare_or_yield_levels: T-100/source-A reports passengers and seats, not ticket prices -- fare and yield are not present in any cached source.
- diversion_to_other_metro_airports: each airport's traffic is reported independently; there is no cached origin-level data linking a traveler's airport choice to nearby alternatives, so we cannot tell whether suppressed traffic at one airport shows up at another.
- slot_gate_runway_or_off_airport_constraints: operational capacity constraints (slot controls, gate counts, runway capacity, surrounding land use) are not in BTS traffic data -- the only constraint data this agent has is the curated buildability reference for a handful of named airports (data/reference/buildability.json), not a general capacity model.
- international_origin_destination_demand: cached OTP congestion data covers domestic reporting carriers only; international O&D passenger demand is not fully captured by the sources cached here.

Data window: TTM (trailing twelve months), data through 2026-04.
Confidence: medium.
Caveats:
- this decomposition is not a measure of unmet demand -- it is the subset of public-data facts that bear on investment demand; see 'unknown' for what it structurally cannot tell us

Answered by the built-in rules interpreter (no AI model).
```
