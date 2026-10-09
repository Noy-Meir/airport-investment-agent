# data/snapshots

Convention for a data source whose manifest `access` is `snapshot-file` or
`live+snapshot` instead of `snapshot-in-db`: a small, compact, **committed**
file here (CSV/JSON), one per source, instead of (or in addition to) rows in
`data/cache.db`.

Rules:

- One file per source, named after the source's manifest `id`
  (e.g. `data/snapshots/<source_id>.csv`).
- Keep it small — this directory is committed to git, so it's for compact
  reference/lookup data, never a dump of a large raw dataset (see
  CLAUDE.md's cost rules: no big CSVs/ZIPs into context, and nothing large
  belongs in git either).
- Loaded by a loader in `src/clients` or `src/cache` — never read ad hoc
  from scripts or tools.
- Refreshed only by a script under `scripts/`, named `refresh_<source>.py`,
  which re-fetches from the upstream URL in `data/sources_manifest.json`
  and overwrites the file plus the manifest entry's `fetched_at`/`row_count`
  (see `src/cache/manifest.update_entry`).
- Never hand-edited. If a value needs manual correction, fix it at the
  source or via the refresh script, not by editing the file directly.

This directory is currently empty: all four registered sources use
`access: "snapshot-in-db"` (they live in `data/cache.db`), not
`snapshot-file`. It exists so a future source that's cheaper to keep as a
flat file (e.g. a small curated lookup pulled from an API) has a clear,
pre-agreed home.
