# Airport Investment Intelligence Agent

A screening aid for US airport investment research: a deterministic scoring
layer over real BTS and OurAirports data, paired with an LLM that selects
tools and narrates the results. It does not compute or assert numbers
itself, and this is not investment advice. See [DESIGN.md](DESIGN.md) for
methodology, tradeoffs, and where AI is used.

## Requirements

- Python 3.12 recommended (3.10+ required)
- An Anthropic API key, created **inside a Console workspace** — a key that
  is not scoped to a workspace returns an HTTP 400 error

## Quick start

```bash
git clone https://github.com/Noy-Meir/airport-investment-agent.git
cd airport-investment-agent
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/fetch_cache.py   # downloads the prebuilt ~58MB data snapshot
                                 # from the GitHub Release data-v1 and
                                 # verifies its SHA-256
cp .env.example .env            # fill in ANTHROPIC_API_KEY and MODEL_NAME
streamlit run app.py
```

`MODEL_NAME` is read from `.env` (the value used in development is
`claude-sonnet-5-5`). Never commit `.env`.

If Streamlit asks for an email on first run, just press Enter.

## Sample questions

The UI offers these starting points:

- "Which New England airports look like the best expansion candidates?"
- "How does congestion at LAX compare to Santa Ana (SNA)?"
- "What percent of flights out of Anchorage are long-haul?"
- "What's the unmet demand at SFO, and what's driving it?"

Good follow-ups to try in the same conversation:

- "Now re-rank that with more weight on growth and less on congestion."
- "Why did SNA score lower than LAX?"
- "Compare BOS and PVD on load factor and demand/supply gap."

## Voice

Each assistant answer has a read-aloud button, and the chat input has a mic
button for dictating a question. Both are English only (en-US) and use the
browser's Web Speech API (Chrome or Edge recommended) — recognized speech
may be sent to the browser vendor's speech service for transcription.
Nothing is auto-submitted; you can review and edit before sending.

## Data

Sourced from BTS T-100 (segment traffic), BTS On-Time Performance, and
OurAirports. The current data window ends 2026-04. Every stored row carries
a `source` and `fetched_at` stamp. To rebuild the cache from source instead
of fetching the snapshot (slower, requires network access to BTS/
OurAirports):

```bash
python scripts/build_cache.py
```

## Tests

```bash
python -m pytest -q
```

To run one agent turn against the real API from the command line (prints
the answer, the tools called, and the estimated cost):

```bash
python scripts/ask.py "How does BOS look as an investment?"
```

## Project layout

```
src/cache      pre-built local SQLite over BTS and OurAirports data, with source stamps
src/clients    HTTP clients for BTS T-100, BTS OTP, and OurAirports
src/reference  curated/derived lookups: hub tiers, regions, pax windows, buildability
src/scoring    per-airport TTM signals, peer z-scores, composite score
src/analysis   unmet-demand decomposition (measured/inferred/unknown)
src/tools      the LLM-facing tool registry wrapping the layers above
src/agent      the agent loop, config, and pricing/cost estimation
src/ui         Streamlit chat logic and voice controls
scripts        fetch/build the data cache, and a CLI for one-off questions
data           cached SQLite DB, raw downloads, and reference JSON
tests          unit tests over the deterministic layers
docs           design decisions and scoring methodology notes
```

## Limitations

- OTP congestion coverage is partial: domestic reporting carriers only, and
  only part of the TTM window is downloaded; low coverage is flagged in
  caveats.
- Buildability constraints are curated for a handful of airports only;
  everywhere else returns "unknown constraints," not "no constraints."
- `demand_supply_gap` can be positive because seats were cut rather than
  because demand grew — the two are arithmetically indistinguishable.
- Metro groupings and hub-tier thresholds are analyst conventions, not
  cross-checked against an official FAA/OMB/CBSA definition.
- Verification so far is the unit-test suite over the deterministic layer
  plus manual review of sample questions.
- Runs locally only; it is not deployed anywhere.
