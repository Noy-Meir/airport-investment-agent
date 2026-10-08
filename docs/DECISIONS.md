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

### Phase 2a: OTP ingest + reconciliation (report, not assertion)
`src/clients/otp.py` downloads monthly OTP ZIPs (12 months, 2025-08..2026-07,
343MB total, fetched after user confirmation per CLAUDE.md's cost rules).
`src/cache/otp_aggregate.py` aggregates per (year, month, origin) into sums
and counts (never means), excluding cancelled flights from taxi/delay
observations and never imputing a missing field. `get_congestion_ttm` /
`get_congestion_month` (`src/cache/accessors.py`) derive the rates from
those sums/counts.

`scripts/build_cache.py`'s `report_otp_reconciliation` recomputes July 2026
numbers for SFO/LAX/SNA/ANC and prints them next to the earlier spike
numbers on every build run, as a report, not a hardcoded verdict:
- **Flight counts match the spike exactly** for all four airports (SFO
  13,841; LAX 17,454; SNA 3,992; ANC 2,513).
- Mean taxi-out and mean departure delay match the spike within ~0.1 min
  for SFO/LAX/SNA.
- **%dep-delay>=15min runs consistently ~1-1.2pp higher** than the spike for
  all three (SFO 35.46 vs 34.3, LAX 25.51 vs 24.8, SNA 25.97 vs 25.0).
  Flight counts match exactly and taxi-out/mean delay agree within ~0.1 min,
  so the spike's OTP congestion numbers were real -- the gap is explained:
  the spike used a strict `> 15 min` threshold on `DepDelayMinutes`, while
  the cache here sums BTS's own `DepDel15` flag (`>= 15 min`). Not a data
  discrepancy, a threshold-convention difference.
- ANC has no spike figures beyond flight count to compare against (see
  above); OTP-derived ANC cancellation/taxi/delay numbers are new, not a
  reconciliation. The spike's ANC OTP distance shares (40.8/24.8/8.8%) were
  not reproduced here -- `otp_airport_month` stores no per-flight distance,
  only sums/counts -- but they are not shown to be *wrong*, just
  unverifiable from this cache. T-100 (source B) remains the source for
  ANC long-haul share: it covers international and freighter flights that
  OTP excludes entirely.

### Congestion snapshot (`get_congestion_ttm`, trailing 12 months ending 2026-07, not re-built)
| Airport | Flights | Cancel % | Mean taxi-out (min) | Mean dep delay (min) | % delayed >=15min | Coverage vs T-100 |
|---|---|---|---|---|---|---|
| SFO | 147,566 | 1.00 | 22.45 | 17.85 | 24.12 | 96.6% |
| LAX | 190,106 | 0.94 | 18.07 | 15.09 | 20.24 | 91.9% |
| SNA | 45,231 | 1.07 | 16.09 | 15.24 | 20.20 | 90.0% |
| ANC | 20,008 | 1.14 | 14.93 | 11.75 | 16.31 | 25.6% |
| BOS | 143,447 | 2.82 | 21.35 | 18.31 | 23.43 | 88.8% |

Coverage = OTP flights / T-100 domestic_departures over the overlapping
months. T-100 domestic_departures includes freighters and carriers that do
not report to OTP, so this ratio understates how much *passenger* traffic
OTP actually covers -- ANC's low 25.6% in particular reflects its freighter
volume, not OTP data loss.

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

### Micro tier (TTM production only, not part of the CY2024 baseline)
`compute_hub_tiers_ttm` (`src/reference/hub_tiers.py`) adds a 4th tier,
**micro**: TTM-eligible (total_passengers >= `VOLUME_FLOOR_PAX`) airports
that don't meet any large/medium/small share threshold. Added for scoring
(`src/scoring/signals.get_peer_group_ttm`): before this, a volume-floor-
eligible airport with too small a national share (e.g. ACK, BGR, ORH in New
England) had no tier at all, fell back to a 1-member peer group, and scored
`"insufficient data"` even inside a scope the user explicitly asked about
(e.g. a New England ranking) -- an unscored airport in the requested scope
is a worse answer than a scored one with its peer group honestly stated.
Real-cache snapshot (TTM ending 2026-04): large 30, medium 35, small 80,
micro 89 -- sums exactly to the 234-airport eligible universe. The
CY2024-pinned `compute_hub_tiers` (golden-test baseline,
`data/reference/hub_tiers.json`, the 31/34/76 test) is unchanged -- it never
computes a micro tier.

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

## Production definition: volume floor / hub tiers / regions
Decision: the **production** definition of the volume floor, hub tiers and
New England region list is **trailing 12 months (TTM) ending at the latest
month present in source A** (`compute_hub_tiers_ttm`,
`list_new_england_airports_ttm`, `get_ttm_totals`), not calendar-year.
`data/reference/*.json` and `tests/test_golden.py` stay pinned to CY2024 as
a fixed regression baseline -- they are not meant to track production, only
to catch unintended changes to the aggregation code itself.

One boundary effect from this choice, visible in the TTM vs. CY2024 diff
below: MDW moves from the large tier to the medium tier under TTM. Its
national passenger share sits right at the large-tier threshold, so this is
a threshold-boundary effect of the trailing window, not a data problem.

## Open items
- FAA cross-check of hub tiers (large/medium/small) against FAA's own list.
- `buildability.json` facts flagged `needs_verification` (exact current
  slot-cap / exemption counts, SNA's post-2025 MAP mitigation status) should
  be rechecked before being surfaced as a firm number rather than a general
  constraint description.

<!-- TTM_DIFF_START (auto-generated by scripts/build_cache.py -- do not hand-edit this block) -->

## TTM vs CY2024 diff (auto-generated)

Trailing 12 months ending 2026-04 (0 NULL total_passengers month-rows excluded from TTM sums).

- Volume floor (>= 100,000/yr, all US airports): CY2024 n=227, TTM n=234. Entering: ['ABI', 'CPR', 'ELM', 'HTS', 'LAL', 'SCC', 'VRB', 'XWA']. Leaving: ['PSE'].
- New England list: CY2024 n=10, TTM n=10. Entering: none. Leaving: none.
- Hub tier large: CY2024 n=31, TTM n=30. Entering: none. Leaving: ['MDW'].
- Hub tier medium: CY2024 n=34, TTM n=35. Entering: ['MDW']. Leaving: none.
- Hub tier small: CY2024 n=76, TTM n=80. Entering: ['BIL', 'CAK', 'PSC', 'PVU', 'SBN']. Leaving: ['ACY'].

<!-- TTM_DIFF_END -->
