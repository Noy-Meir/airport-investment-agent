# Airport Investment Intelligence Agent

Deloitte FDE take-home. Agent that answers airport-investment questions using
real BTS/FAA/OurAirports data.

Python 3.12 recommended (>=3.10 required).

## Layers

- `src/cache` — cache-through SQLite over BTS/FAA/OurAirports (`accessors.py`,
  `db.py`); every row/aggregate carries a source stamp and as-of period.
- `src/clients` — HTTP clients for BTS T-100, BTS OTP, FAA TAF, and OurAirports
  (`faa_taf.py`, `runways.py`).
- `src/reference` — curated/derived lookups: hub tiers, regions, pax
  windows, buildability.json, metro_areas.json, the `envelope()` helper.
- `src/scoring` — per-airport TTM signals (growth, load_factor,
  demand_supply_gap, congestion), peer-tier z-scores, composite score.
- `src/analysis` — unmet-demand decomposition (measured/inferred/unknown).
- `src/tools` — the LLM-facing tool registry (`registry.py`) wrapping the
  above as uniform-envelope, schema-validated tool calls.
- `src/agent` — `respond.py` is the single entry point (UI + CLI): picks
  AI model vs. built-in rules interpreter per `LLM_PROVIDER`/sidebar
  override, and falls back to rules with a notice if the AI model is
  unavailable or errors. `rules_router.py` is the deterministic,
  no-LLM path (regex/keyword `plan()` + `answer()`, zero cost).
  `narrators.py` renders tool envelopes into template text for that path.
  An API key is optional; `config.py` resolves it from `.env`/environment.

## Tools (`src/tools/registry.py`)

- `rank_airports` — leaderboard for a scope (region/tier/states).
- `rank_airports_by_traffic` — rank a scope by TTM departing-passenger volume.
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
- `get_forward_outlook` — FAA Terminal Area Forecast base-year enplanements
  (FY2024) and +5y/+10y forecasts, runway counts (open, paved, >=5,000 ft),
  and enplanements-per-runway proxy. Context only, not in composite score.
- `describe_data_sources` — lists every upstream data source (name,
  publisher, vintage, access method, caveat, source URL).

## Rules

- No imputation of missing values; NULL stays NULL, caveats say how many.
- Every tool/accessor returns the uniform envelope:
  `{result, method, caveats, source, confidence}`.
- Scoring z-scores are computed within an airport's own TTM hub-tier peer
  group, never against the full universe.
- **Raw-gap condition**: a peer-relative z-score alone is never enough for
  an inference (it can be high purely because peers are low) — the raw
  demand_supply_gap must also be positive.
- The LLM only selects tools and narrates; it never computes or asserts a
  number itself. No unsourced facts — every claim traces to a tool `source`.
  The rules interpreter path follows the same rule by construction: it only
  selects a tool and fills a template, never computes a number.
- Forward outlook (FAA TAF) is context only; never fold it into the composite
  score.
- No raw user text interpolated into queries (SQL/Socrata/etc.) — parameterize.
- No API keys in git; no hardcoded verdicts in scripts.

## Cost rules

- Never read big files (CSVs, ZIPs, large JSON) into the LLM context; long
  output goes to files, only a summary to the console/LLM.
- Ask before any download over 50MB. No auto-continue loops.

## Open items

See `docs/DECISIONS.md`: FAA cross-check of hub tiers, buildability.json and
metro_areas.json `needs_verification` entries.
