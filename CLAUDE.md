# Airport Investment Intelligence Agent

Deloitte FDE take-home. Agent that answers airport-investment questions using
real BTS/FAA/OurAirports data.

## Architecture

- The LLM only **selects tools and narrates results**. It never computes or
  asserts a number itself.
- All numbers come from **deterministic code** (tool implementations), never
  from the LLM.
- Every tool returns a **uniform envelope**:
  `{result, method, caveats, source, confidence}`.
- Data access is **cache-through SQLite**: each cached row/aggregate carries
  a source stamp and an as-of period; cache is refreshed, not replaced blind.

## DON'Ts

- No imputation of missing values.
- No unsourced facts — every claim must trace to a tool result's `source`.
- No raw user text interpolated into queries (SQL/Socrata/etc.) — parameterize.
- No API keys in git — use `.env.example` only, never commit `.env`.
- No hardcoded verdicts in scripts (e.g. no canned "NO-GO" text) — scripts
  must print the real, current result of what they do.
- **Numeric guard**: every number appearing in an answer must be traceable to
  a tool result, not generated/estimated by the LLM.

## Cost rules

- Never read big files (CSVs, ZIPs, large JSON) into the LLM context.
- Long output goes to files; print only a summary to the console/LLM.
- Ask before any download over 50MB.
- No auto-continue loops — don't re-run expensive scripts/downloads
  automatically.

## Reference

See `docs/DECISIONS.md` for data-source spike findings (what's GO/NO-GO,
measured numbers, open items).
