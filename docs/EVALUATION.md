# Evaluation

## What was run, and how

`python scripts/run_eval.py` -- an offline evaluation of the deterministic
rules router (`src/agent/rules_router.py`), run against the shipped
`data/cache.db`. No Anthropic API calls (rules path only, zero cost), no
network access, no sub-agents. Last run: 2026-10-09.

Two suites, both driven by a `rules_expectation` field on each case (one of
`tool:<name>`, `refuse`, `clarify`, `restate`, `help_known_limitation`, or
`covered_by:<conv_case_id>`):

1. **Tool selection** (`data/eval/tool_selection_cases.json`, 31 cases) --
   one question per case, checked independently. Cases with a
   `previous_turn` run that turn first in a fresh conversation `State`,
   then the real question against the resulting state. The 3 bare
   `followup_*` cases that have no `previous_turn` of their own are marked
   `covered_by:<conv_case_id>` and not independently executed -- they need
   a hand-built `State`, which the matching conversation case already
   exercises end to end.
2. **Conversation** (`data/eval/conversation_cases.json`, 15 cases) -- a
   single scripted conversation run through one shared `State`, so later
   turns see the context earlier ones left behind. Covers the 4 sample
   questions, the follow-ups "re-rank with more weight on growth", "Why
   did HVN rank above BTV?", and "how confident are you", a named
   comparison (BOS vs PVD), a tier ranking, and refusals/limits (a
   guarantee request, a forecast request, a fares/ROI request, a non-US
   airport, a non-English question, and an unmatched free-form question).

For every executed case, the script checks:

- (a) the router's actual behavior -- which tool it called, or which
  no-tool path (refuse / clarify / restate / help) it took -- matches
  `rules_expectation`;
- (b) every number printed in the answer traces back to the tool envelope
  that produced it (a local checker that tokenizes hyphenated rank ranges
  like "2-4" as two separate numbers rather than misreading them as "-4",
  and ignores the display-only position column of result tables, since
  that column is a row index -- not an envelope-sourced value -- by
  design in `compare_airports`);
- (c) the answer states its data window and caveats (with narrator-specific
  exceptions: `describe_data_sources` has no window concept, and error/
  no-result answers carry neither by design);
- (d) no exception escaped.

A case whose expectation is `help_known_limitation` is its own category,
not pass/fail: it's reported as a confirmed known limitation when the
router falls to the generic help message as expected, and only turns into
a real `fail` if that behavior drifts (e.g. the router starts calling a
tool for it), which would be a correctness regression worth catching.

## Results

| Category | Pass | Fail | Known limitation | Covered | Total |
|---|---|---|---|---|---|
| Tool selection | 22 | 0 | - | - | 22 |
| Refusals / clarify | 13 | 0 | - | - | 13 |
| Follow-ups | 3 | 0 | - | 3 | 6 |
| Known limitations | - | 0 | 5 | - | 5 |
| Traceability (check (b) only, across every case with a tool call) | 24 | 0 | - | - | 24 |
| **Total** | **38** | **0** | **5** | **3** | **46** |

## Findings

**Five questions are confirmed known limitations of the rules path --
cases the AI-model path is the intended handler for:**

- `metro_grouping_comparison` ("Compare airports in the New York metro
  area.") -- the router has no keyword pattern for combining a metro-area
  lookup with a comparison in one turn; it falls to the generic help
  message instead of chaining `list_region_airports` and
  `compare_airports`.
- `sensitivity_multiple_scopes` ("If I weight demand-supply gap higher,
  does the Southeast ranking change a lot?") -- a self-contained scope
  named alongside a reweighting request doesn't match any router pattern,
  so it falls to help instead of calling `sensitivity`.
- `tool_error_null_result` ("What's the TTM growth rate for an airport
  with no recent BTS data on file?") -- names no real airport code, so the
  router can't resolve one to call `get_airport_traffic` against; it falls
  to the generic help message rather than asking which airport is meant
  (the router has no "which airport do you mean" clarification intent,
  only a ranking-scope one).
- `followup_pronoun_no_airport_named` ("And how does its congestion
  compare to its peers?" after a prior turn about MDW) -- the rules router
  has no pronoun resolution; "its" doesn't resolve to the prior airport,
  so this falls to help instead of calling `compare_congestion`.
- `conv_15_unmatched_help` ("Tell me about airport investment trends in
  general.") -- a genuinely open-ended, in-domain question with no
  specific tool cue; the rules path's only response to this shape of
  question is the fixed help message, whereas the AI-model path could
  engage with it more flexibly.

**Two number-traceability false positives were found and fixed in the
checker itself (not in the router or narrators), confirming both were
checker bugs, not product bugs:** a markdown table's display-only "Rank"
column (synthesized by list position in `compare_airports`, which has no
ranking concept of its own) was being checked as if it were sourced from
the envelope; and hyphenated rank ranges like "rank 2-4 across weight
sets" in `sensitivity`'s output were being tokenized as a single number
"-4" instead of two numbers, 2 and 4. Both are now handled correctly, and
each fix has a dedicated unit test in `tests/test_run_eval.py`.

**Already-known rules-mode limitations, confirmed by this run:**

- In rules mode, "re-rank with more weight on growth" returns the preset
  `sensitivity` table (one of its five fixed weight sets), not a custom
  reweighting -- custom weights need the AI model.
- `get_unmet_demand_breakdown` and `compare_congestion` can report
  different TTM windows for the same airport by design; each answer
  states its own window rather than assuming a shared one.
- A "why did X rank above Y" follow-up only makes full sense when a prior
  ranking in the same conversation already surfaced both X and Y; the
  router itself doesn't enforce this (it compares whatever two codes it
  finds in the message).

## What this does not cover

- **AI-model path quality is not evaluated offline.** This script only
  exercises the deterministic rules router (`LLM_PROVIDER` rules path);
  the AI-model answer quality, tool-selection accuracy, and narration are
  not covered here and would require live API calls.
- **No evaluation against external ground truth.** Checks are internal
  consistency (does the router pick the expected behavior, does every
  number trace back to the tool's own envelope, does the answer state its
  window and caveats) -- not a comparison against a verified "correct"
  answer for any given airport-investment question.
