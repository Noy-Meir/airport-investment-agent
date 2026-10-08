# Data-source spike: decisions and measured facts

## Source A: Socrata `r495-tyji` — airport-month T-100 summary
- Coverage: 2014-01 to 2026-04. Values returned as strings.
- Passengers are by **ORIGIN** airport (enplanements, including connections).

## Source B: route-level T-100, TranStats table FMG — GO
- The earlier spike run recorded a NO-GO; that was wrong. Scripted form POST
  (viewstate + eventvalidation + all field checkboxes + `chkDownloadZip`)
  works from the user's machine and returns a real ZIP.
- CY2025: ZIP ~19.5MB, extracted CSV ~178MB, 571,205 rows, 12 months,
  1,924 origins.
- Columns include: ORIGIN, DEST, DISTANCE, CLASS, SEATS, PASSENGERS,
  DEPARTURES_PERFORMED, UNIQUE_CARRIER, YEAR, MONTH, ORIGIN_COUNTRY,
  DEST_COUNTRY.
- Manual download remains the documented fallback if the scripted POST
  breaks (BTS form internals can change).

## CLASS field
- Values observed: F, G, L, P.
- **Verified (Phase 1):** the ZIP's `Documentation.csv` does NOT contain a
  legend for CLASS -- it only labels the column "Service Class" with no
  code mapping, so it cannot be used to verify this. The meanings below are
  sourced instead from 14 CFR Sec. 291.45 (BTS's own T-100 regulatory
  text): F = Scheduled Passenger/Cargo, G = Scheduled All-Cargo,
  L = Nonscheduled Passenger/Cargo, P = Nonscheduled All-Cargo. Encoded in
  `src/cache/config.py` (`CLASS_MEANINGS`, `PASSENGER_CLASSES`, `CARGO_CLASSES`).
- Passenger vs. freighter must be defined by **CLASS**, not by SEATS — they
  disagree on ~2,500 ANC departures.

## ANC long-haul, CY2025 (weighted by DEPARTURES_PERFORMED, origin=ANC)
Seats-based split; must be re-computed by CLASS once verified.
- All 87,988 departures: 47.4% / 43.2% / 31.0% at >=2000/2500/3000 mi. This
  is the ground truth, reproduced exactly by `get_long_haul_share` -- see
  the 0-distance rule below.
- seats>0 (40,712 deps): 19.0% / 15.1% / 5.6%.
- seats=0 (47,276 deps): 71.9% / 67.3% / 52.9%.

### 0-distance rule
T-100 route rows can have DISTANCE == 0 for two different reasons, and only
one of them is a data problem:
- **Real**: ORIGIN == DEST (same-airport sightseeing/positioning flights --
  67 ANC->ANC combos, 242 departures, CY2025). These are genuine short
  flights and belong in both the numerator and denominator as short-haul,
  not excluded.
- **Data error**: ORIGIN != DEST but DISTANCE == 0 anyway. These are
  excluded from both numerator and denominator and reported in caveats,
  same as a missing distance.
A truly missing DISTANCE (empty/NULL CSV field) is never imputed to 0 --
`t100_aggregate.aggregate_routes` keeps it as `None`, tracks the count and
departures of such rows separately, and `get_long_haul_share` excludes only
those rows from numerator and denominator.

## OTP (On-Time Performance)
Flight-level, domestic reporting carriers only — no freighters, no
international. Use **only** for congestion metrics. The spike's OTP
long-haul numbers for ANC were never reproduced and look inconsistent with
T-100 — **do not use OTP for long-haul share**.

## Volume floor
- Calendar-year total passengers >= 100,000 (CY2024) -> 222 airports.
- Growth stdev: 0.24 for the 10k-100k bucket vs. 0.08 for the 100k-1M
  bucket. Also compute IQR/MAD.
- Must be recomputed on trailing 12 months ending 2026-04 in code, not
  hardcoded from this spike.

## Hub tiers
Based on share of national total passengers, CY2024 (national total =
988,696,980):
- Large >= 1%: 31 airports.
- Medium >= 0.25%: 34 airports.
- Small >= 0.05%: 76 airports.

## New England airports above floor (10)
BOS, BDL, PVD, PWM, BTV, MHT, HVN, BGR, ORH, ACK.
Region lists must be built in code from OurAirports `iso_region`
intersected with source A airports above the floor — the
`scheduled_service` flag alone is the wrong filter.

## OurAirports
Join on `iata_code`. ~3% of US scheduled airports have no IATA code and
will not join.

## Phase 1 status (data layer)
- `src/clients/t100_routes.py`: productionized FMG form download (retry x3,
  300s timeout, validates body starts with `PK`, logs to
  `data/logs/t100_routes.log`). CLI: `python -m src.clients.t100_routes <year> [<year> ...]`.
- `scripts/build_cache.py`: builds `data/cache.db` from source A (paged,
  typed Socrata pull), route-level T-100 aggregated by
  (year, month, origin, dest, class, carrier) restricted to rows where
  origin or dest country is US, and OurAirports. Every row carries
  `source` + `fetched_at`. Also regenerates `data/reference/regions.json`
  and `hub_tiers.json` from the cache it just built.
- `src/cache/accessors.py`: cache-through reads returning the uniform
  envelope, parameterized SQL, airport-code validation against the cached
  airport universe (raises `AirportNotFoundError`, never guesses).
- `data/reference/buildability.json`: curated SNA/DCA/LGA/JFK/EWR
  constraints, each with a source URL and a `needs_verification` note for
  anything not nailed down to an exact current figure.
- Golden tests (`tests/test_golden.py`, 4/4 passing): ANC CY2025 long-haul
  shares by CLASS group match a from-scratch recomputation off the raw ZIP;
  New England list = the 10 airports above; hub tiers = 31/34/76; unknown
  airport code raises a clear error.
- Socrata quirk found in Phase 1: `year` is a text field on `r495-tyji`
  despite looking numeric -- `$where=year='2024'` (quoted) is required,
  `year=2024` (unquoted) returns an HTTP 400 type-mismatch error.

## Phase 1 fixes (source A field accuracy, no-imputation, OurAirports hygiene, TTM)
- Source A (`r495-tyji`) only has `total_*`/`domestic_*` fields -- the prior
  `outbound_international*`/`inbound_international*` FIELD_MAP entries (and
  the comment claiming Socrata renamed them) were removed. International
  figures (`intl_departures`, `intl_passengers`, `intl_seats`) are now
  computed at ingest as `total - domestic`, NULL if either side is NULL.
- No imputation: `src/cache/accessors.py` no longer does `x or 0` anywhere.
  NULL stays NULL; sums exclude NULL rows and every caveat list says how
  many. `get_long_haul_share` now excludes route-level rows with a missing
  `DISTANCE` from both the numerator and denominator, and excludes
  `DISTANCE == 0` only when origin != dest (a data error) -- see the
  0-distance rule above. Same-airport (origin == dest) `DISTANCE == 0` rows
  are kept as short-haul. `src/cache/t100_aggregate.aggregate_routes`
  matches this: an empty CSV `DISTANCE` field is kept as `None` (never
  coerced to 0.0) and tracked separately (row count + departures), reported
  by `scripts/build_cache.py`. Both `tests/test_golden.py` and
  `tests/test_t100_aggregate.py` cover this; the ANC class_group="all"
  regression test now passes exactly against the original spike numbers.
- OurAirports: rows with `type == 'closed'` are dropped before insert; if
  two or more remaining active rows still share an `iata_code`, ingest now
  fails loudly (`OurAirportsError`, naming the offending rows) instead of
  silently picking one.
- TTM: added `get_ttm_totals` plus trailing-12-month versions of the hub
  tier / New England list builders. `data/reference/*.json` stay pinned to
  CY2024 (the golden-test baseline); the TTM vs. CY2024 diff is appended
  below in the auto-generated block, regenerated by every
  `scripts/build_cache.py` run.
- Source-A default fetch range widened to 2023-2026 (data runs through
  2026-04) so a real trailing-12-month window is available.

## Open items
- Reconcile T-100 vs. OTP long-haul numbers for ANC.
- FAA cross-check of hub tiers (large/medium/small) against FAA's own list.
- `buildability.json` facts flagged `needs_verification` (exact current
  slot-cap / exemption counts) should be rechecked before being surfaced as
  a firm number rather than a general constraint description.
- Now that the volume floor/hub tiers/New England list have been computed
  on a real trailing-12-month window (see the diff below), decide whether
  to switch the golden/production definition from CY2024 to TTM, or keep
  CY2024 as the stable baseline and surface TTM only as a secondary view.

<!-- TTM_DIFF_START (auto-generated by scripts/build_cache.py -- do not hand-edit this block) -->

## TTM vs CY2024 diff (auto-generated)

Trailing 12 months ending 2026-04 (0 NULL total_passengers month-rows excluded from TTM sums).

- Volume floor (>= 100,000/yr, all US airports): CY2024 n=227, TTM n=234. Entering: ['ABI', 'CPR', 'ELM', 'HTS', 'LAL', 'SCC', 'VRB', 'XWA']. Leaving: ['PSE'].
- New England list: CY2024 n=10, TTM n=10. Entering: none. Leaving: none.
- Hub tier large: CY2024 n=31, TTM n=30. Entering: none. Leaving: ['MDW'].
- Hub tier medium: CY2024 n=34, TTM n=35. Entering: ['MDW']. Leaving: none.
- Hub tier small: CY2024 n=76, TTM n=80. Entering: ['BIL', 'CAK', 'PSC', 'PVU', 'SBN']. Leaving: ['ACY'].

<!-- TTM_DIFF_END -->
