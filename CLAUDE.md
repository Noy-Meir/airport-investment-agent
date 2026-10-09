# Airport Investment Intelligence Agent

Deloitte FDE take-home. Agent that answers airport-investment questions using
real BTS/FAA/OurAirports data.

Python 3.12 recommended (>=3.10 required).

## Layers

- `src/cache` — cache-through SQLite over BTS/FAA/OurAirports (`accessors.py`,
  `db.py`); every row/aggregate carries a source stamp and as-of period.
- `src/reference` — curated/derived lookups: hub tiers, regions, pax
  windows, buildability.json, metro_areas.json, the `envelope()` helper.
- `src/scoring` — per-airport TTM signals (growth, load_factor,
  demand_supply_gap, congestion), peer-tier z-scores, composite score.
- `src/analysis` — unmet-demand decomposition (measured/inferred/unknown).
- `src/tools` — the LLM-facing tool registry (`registry.py`) wrapping the
  above as uniform-envelope, schema-validated tool calls.

## Tools (`src/tools/registry.py`)

- `rank_airports` — leaderboard for a scope (region/tier/states).
- `score_airport` — one airport's composite score + signal breakdown.
- `compare_airports` — side-by-side scores for a named list.
- `sensitivity` — re-rank a scope under alternative weight sets.
- `list_region_airports` — airports in a region/states above the volume floor.
- `get_airport_traffic` — TTM passengers/seats/load factor/growth.
- `compare_congestion` — TTM flights + % delayed, with OTP/T-100 coverage.
- `get_long_haul_share` — long-haul departure share by distance threshold.
- `get_buildability` — curated capacity/slot/perimeter constraints.
- `get_unmet_demand_breakdown` — measured/inferred/unknown decomposition;
  never a single unmet-demand number.

## Rules

- No imputation of missing values; NULL stays NULL, caveats say how many.
- Every tool/accessor returns the uniform envelope:
  `{result, method, caveats, source, confidence}`.
- Scoring z-scores are computed within an airport's own TTM hub-tier peer
  group, never against the full universe.
- **Absolute-gap guard**: a peer-relative z-score alone is never enough for
  an inference (it can be high purely because peers are low) — the raw
  demand_supply_gap must also be positive.
- The LLM only selects tools and narrates; it never computes or asserts a
  number itself. No unsourced facts — every claim traces to a tool `source`.
- No raw user text interpolated into queries (SQL/Socrata/etc.) — parameterize.
- No API keys in git; no hardcoded verdicts in scripts.

## Cost rules

- Never read big files (CSVs, ZIPs, large JSON) into the LLM context; long
  output goes to files, only a summary to the console/LLM.
- Ask before any download over 50MB. No auto-continue loops.

## Open items

See `docs/DECISIONS.md`: FAA cross-check of hub tiers, buildability.json and
metro_areas.json `needs_verification` entries, pinning Python 3.12 in CI, an
API key + spend cap for LLM calls.
