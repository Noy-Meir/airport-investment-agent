# Design Document — Airport Investment Intelligence Agent

## 1. Goal and scope

A screening aid for US airport investment research, built on public BTS
(T-100, OTP) and OurAirports data: a deterministic scoring layer plus an
LLM that selects tools and narrates results.

It is explicitly **not** investment advice. The system prompt
(`src/agent/system_prompt.md`) has the agent decline guarantees or
legal/financial advice and present rankings as an adjustable hypothesis,
not a verdict. Scope is US airports only; non-US airports, non-aviation
questions, and forecasting are out of scope — there is no forecasting
model here.

## 2. Architecture

Three sources (BTS T-100, BTS OTP, OurAirports) feed a pre-built local
SQLite store (`src/cache`), every row stamped with `source` and
`fetched_at`/`as_of`. A reference layer (`src/reference`) derives curated
lookups — hub tiers, regions, pax windows, buildability, metro groupings.
A deterministic layer (`src/scoring`, `src/analysis`) computes TTM
signals, peer z-scores, a composite score, and the unmet-demand
decomposition, with no LLM involved. The tool registry wraps this as
schema-validated, uniform-envelope tool calls that the LLM agent calls
and narrates inside a Streamlit chat UI with read-aloud output and a mic
button.

```
BTS T-100 / BTS OTP / OurAirports -> SQLite cache (source + fetched_at)
                  -> reference layer (tiers, regions, buildability, metro)
                  -> scoring + analysis (signals -> z-scores -> composite;
                     unmet-demand measured/inferred/unknown)
                  -> tool layer ({result, method, caveats, source, confidence})
                  -> LLM agent (selects tools, narrates, never computes)
                  -> Streamlit chat (app.py) + read-aloud + mic input
```

## 3. Scoring methodology

Four TTM signals per airport: `growth` (passenger growth %), `load_factor`,
`demand_supply_gap` (passenger growth % minus seat growth %), and
`congestion` (% of departures delayed ≥15 min, from OTP).

**Peer groups.** Every airport is scored against its own TTM hub tier —
large, medium, small, or micro — never the full national universe
(`get_peer_group_ttm`). Tiers bucket by share of national passengers
(large ≥1%, medium ≥0.25%, small ≥0.05%); micro was added so low-share
but eligible airports (e.g. ACK, BGR, ORH) get a real, ≥5-member peer
group instead of "insufficient data." `scope` (region/tier/states) only
selects which airports to display — it never changes an airport's peer
group.

**Robust z-scores**: `(x - median) / (1.4826 * MAD)` within the peer
group, clipped to `[-3, 3]`.

**Weights are declared hypotheses, not fitted** (`src/scoring/weights.py`,
`HYPOTHESES`):

| signal | weight | why |
|---|---|---|
| growth | 0.30 | clearest forward demand signal |
| load_factor | 0.25 | utilization is a direct investment case |
| demand_supply_gap | 0.25 | suggests underbuilt infrastructure |
| congestion | 0.20 | lowest — OTP coverage is incomplete |

No weight set is fit to outcome data. `sensitivity()` re-ranks a scope
under four alternatives (`EQUAL`, `GROWTH_HEAVY`, `CONGESTION_HEAVY`,
`DROP_CONGESTION`), showing what's stable (`stable_top3`) vs. what moves
(`rank_range`).

**Missing signals.** With ≥3 of 4 available, the rest are dropped and
weights renormalized, with a caveat. Below 3, `status` is `"insufficient
data"`, `composite_score` is `None`.

**Buildability is a separate flag, not folded into the score** —
`get_buildability` (curated for SNA/DCA/LGA/JFK/EWR) is shown alongside
every score, not weighted into it.

**Confidence** starts from signal count (`high` at 4, `medium` at 3), then
is lowered — never raised — for thin data or a small peer group.

## 4. Unmet demand: no single number

No public BTS/OurAirports dataset directly measures unmet demand
(priced-out travelers, fares, diversion, physical constraints).
`get_unmet_demand_breakdown` always returns three sections instead:

- **measured** — facts from cached data (load factor, passenger-vs-seat
  growth gap, delayed-departure share, metro-sibling growth), each with
  `value=None` and a reason when not computable.
- **inferred** — deterministic rules over measured peer-context z-scores,
  worded "is consistent with," never "proves," each with alternatives.
- **unknown** — a fixed list of what public data structurally cannot
  answer (fares, priced-out travelers, slot/gate constraints,
  international demand, diversion to other metro airports).

**Raw-gap condition**: a peer-relative z-score alone is never sufficient
for an inference — it can be high purely because peers are low — the raw
`demand_supply_gap_pct` must also be positive. The prompt has the agent
decline a "just give me the number" framing.

## 5. Where AI is used

The LLM is optional. With an API key configured, it selects which tool(s)
to call, reads the returned envelopes, and writes the explanation; it does
not compute, estimate, recall, or guess any figure — the system prompt
requires every stated number to trace to a tool result. Without a key, or
if the model call fails, `src/agent/respond.py` falls back to a
deterministic rules interpreter (`src/agent/rules_router.py`):
regex/keyword matching selects at most one tool call, and
`src/agent/narrators.py` renders the result with fixed templates instead of
free-text generation. A sidebar selector (Auto / rules / AI model) lets the
user force either path; a fallback shows a one-line notice. In both modes,
numbers always come from the same deterministic tool registry — only tool
selection and narration differ.

The registry enforces input safety, not output content: `call_tool()`
never raises on bad input (unknown tool, invalid args, unknown airport
code), returning a typed error so a malformed call can't crash either path
or fabricate a result — there is no mechanical check on the model's final
text.

Not delegated to either path: hub-tier assignment, peer-group membership,
z-score/composite computation, unmet-demand rules, and all SQL/Socrata
queries — pure code, with no raw user text interpolated into any query.

## 6. Key tradeoffs and decisions

- **Local pre-built store vs. live API calls** — instant, deterministic
  tool calls against a stamped snapshot; cost is a rebuild step and a
  store that can go stale.
- **Hub-tier peers vs. one national ranking** — a national z-score would
  make small/micro airports look permanently weak next to large hubs;
  tiering compares like to like.
- **Hypothesis weights + sensitivity vs. fitted weights** — no labeled
  outcome data exists to fit against, so weights are a stated hypothesis
  and sensitivity is shown instead of one settled ranking.
- **No imputation** — NULL stays NULL; caveats say how many rows were
  excluded.
- **Passenger figures are departing passengers (enplanements)**, roughly
  half of arrivals+departures totals elsewhere; labeled accordingly.
  **Long-haul share** likewise has no official definition, so it's shown
  for passengers and all-flights side by side, threshold as a parameter.
- **Model chosen for cost vs. quality** — `src/agent/pricing.py` estimates
  Sonnet-class rates; `run_turn` returns an estimated cost per turn (shown
  by the CLI script, not in the UI), a few cents per question.
- **Optional AI: rules interpreter vs. LLM** — the LLM reads more naturally
  and handles phrasing nuance the rules interpreter can't; the rules path
  is free, instant, and runs with no account or network access at all. The
  rules path is intentionally narrow — a fixed set of patterns, not
  general language understanding — and says so when it can't match a
  question.

## 7. Known limitations

- **OTP congestion coverage is partial, two ways**: domestic reporting
  carriers only (no freighters/international, ~91-97% coverage for
  SFO/LAX/SNA/BOS but ~26% for freighter-heavy ANC), and only 9 of 12 TTM
  months downloaded. Both are reported in caveats;
  `compare_congestion` flags `low_coverage: true` below 50%.
- **Buildability is mostly "no entry on file," not fully researched** —
  curated only for SNA/DCA/LGA/JFK/EWR; other airports return "unknown
  constraints," not "no constraints"; some existing entries are flagged
  `needs_verification`.
- **`demand_supply_gap` can be positive because seats were cut**, not
  demand growth — arithmetically indistinguishable. `gap_driven_by_
  seat_cuts` is a caveat the prompt must call out, but the composite
  score is not adjusted for it.
- **Metro groupings are an analyst convention** (no `source_url` yet) and
  **hub-tier thresholds are a choice**, neither cross-checked against an
  official FAA/OMB/CBSA definition or FAA's own hub classification.
- **Verification so far is the unit-test suite over the deterministic
  layer plus manual review of sample questions.**
- **The rules interpreter only handles phrasing it has a pattern for** —
  free-form questions outside those patterns fall through to a help
  message rather than a best-effort guess, and it is English-only.

## 8. What I would do next

- Cross-check hub tiers against FAA's own hub classification.
- Research and fill in `buildability.json` / `metro_areas.json` entries
  flagged `needs_verification`, with real sources.
- Add forward-looking and infrastructure inputs (FAA Terminal Area
  Forecast, runway counts, slot-controlled airports) to complement the
  backward-looking BTS signals, and cross-check enplanements against FAA's
  published counts.
