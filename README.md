# Airport Investment Intelligence Agent

A screening aid for US airport investment research: a deterministic scoring
layer over real BTS and OurAirports data, paired with an optional LLM that
selects tools and narrates the results. Neither path computes or asserts
numbers itself, and this is not investment advice. See
[DESIGN.md](DESIGN.md) for methodology, tradeoffs, and where AI is used.

## Requirements

- Python 3.12 recommended (3.10+ required)
- No API key required. An Anthropic API key is optional and enables the AI
  model; without one, the app answers with a built-in rules interpreter at
  no cost. If you do add a key, create it **inside a Console workspace** —
  a key that is not scoped to a workspace returns an HTTP 400 error.

## Quick start

```bash
git clone https://github.com/Noy-Meir/airport-investment-agent.git
cd airport-investment-agent
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # optional: fill in ANTHROPIC_API_KEY and MODEL_NAME
streamlit run app.py
```

The app runs with `.env` left empty — every question is answered by the
built-in rules interpreter, no account or network call needed. Add
`ANTHROPIC_API_KEY` and `MODEL_NAME` to `.env` to enable the AI model
(the value used in development is `claude-sonnet-5-5`). Never commit `.env`.

If Streamlit asks for an email on first run, just press Enter.

## Sample questions

The UI offers these starting points:

- "Which New England airports look like the best expansion candidates?"
- "How does congestion at LAX compare to Santa Ana (SNA)?"
- "What percent of flights out of Anchorage are long-haul?"
- "What's the unmet demand at SFO, and what's driving it?"

Good follow-ups to try in the same conversation:

- "Now re-rank that with more weight on growth and less on congestion."
- "Why did HVN rank above BTV?"
- "Compare BOS and PVD on load factor and demand/supply gap."

## Answer modes

A sidebar selector offers three modes:

- **Auto** (default) — uses the AI model if an API key is configured,
  otherwise the built-in rules interpreter. If the AI model call fails for
  any reason, the app automatically falls back to the rules interpreter for
  that turn and shows a one-line notice explaining why.
- **Built-in rules (no AI)** — always answers with the deterministic rules
  interpreter; no API call is ever made.
- **AI model** — always uses the AI model (only shown when a key is
  configured); on failure it falls back to rules with the same notice as
  Auto.

In every mode, the numbers themselves come from the same deterministic
tools (`src/tools/registry.py`) — only the tool selection and narration
differ between the AI model and the rules interpreter.

The built-in rules interpreter matches a question to a pattern with
regex/keyword matching, no model call. It covers the 4 sample questions
above, ranking by region/hub tier/states, side-by-side comparisons, and
follow-ups in the same conversation such as "the second one," "why did X
rank above Y," and "how confident are you." It declines outcome guarantees,
forecasts, fares/ROI/construction-cost questions, non-US airports,
non-English text, and other free-form questions it can't map to a known
pattern — in the last case it shows the sample questions as examples of
what it can do.

## Voice

Each assistant answer has a read-aloud button, and the chat input has a mic
button for dictating a question. Both are English only (en-US) and use the
browser's Web Speech API (Chrome or Edge recommended) — recognized speech
may be sent to the browser vendor's speech service for transcription.
Nothing is auto-submitted; you can review and edit before sending.

## Data

The repo ships a prebuilt `data/cache.db` snapshot (about 15MB): BTS T-100
(airport-month and route-level traffic), BTS On-Time Performance, and
OurAirports, with T-100 data through 2026-04 and OTP through 2026-07. Every
stored row carries a `source` and `fetched_at`
stamp. To rebuild the cache from source instead (slower, requires network
access to BTS/OurAirports):

```bash
python scripts/build_cache.py
```

## Tests

```bash
python -m pytest -q
```

To run one turn from the command line (prints the answer, the tools called,
and, for the AI model, the estimated cost):

```bash
python scripts/ask.py "How does BOS look as an investment?"
python scripts/ask.py --rules "How does BOS look as an investment?"   # force rules, no API call
```

`scripts/ask_rules.py` runs one or more questions straight through the
rules interpreter only, sharing one conversation state across questions so
later arguments can be follow-ups to earlier ones:

```bash
python scripts/ask_rules.py "Which New England airports look like the best expansion candidates?" "Why did SNA score lower than LAX?"
```

## Project layout

```
src/cache      pre-built local SQLite over BTS and OurAirports data, with source stamps
src/clients    HTTP clients for BTS T-100, BTS OTP, and OurAirports
src/reference  curated/derived lookups: hub tiers, regions, pax windows, buildability
src/scoring    per-airport TTM signals, peer z-scores, composite score
src/analysis   unmet-demand decomposition (measured/inferred/unknown)
src/tools      the LLM-facing tool registry wrapping the layers above
src/agent      answer-mode selection (respond.py), AI model loop, rules
               interpreter (rules_router.py), narration, config, pricing
src/ui         Streamlit chat logic and voice controls
scripts        build the data cache, and a CLI for one-off questions
data           cached SQLite DB (shipped prebuilt), raw downloads, and reference JSON
tests          unit tests over the deterministic layers
docs           design decisions, scoring methodology notes, and example answers
```

## Limitations

- OTP congestion coverage is partial: domestic reporting carriers only, and
  OTP covers 12 months through 2026-07 while T-100 ends 2026-04, so coverage
  ratios are computed over the overlapping months only; low coverage is
  flagged in caveats.
- Buildability constraints are curated for a handful of airports only;
  everywhere else returns "unknown constraints," not "no constraints."
- `demand_supply_gap` can be positive because seats were cut rather than
  because demand grew — the two are arithmetically indistinguishable.
- Metro groupings and hub-tier thresholds are analyst conventions, not
  cross-checked against an official FAA/OMB/CBSA definition.
- Verification so far is the unit-test suite over the deterministic layer
  plus manual review of sample questions.
- Runs locally only; it is not deployed anywhere.
