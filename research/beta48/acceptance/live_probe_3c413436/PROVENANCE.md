# Live probe 3c413436 — provenance of every artefact here

| | |
|---|---|
| probe_id | `3c413436-68ba-4e50-b106-5d349b372567` |
| code SHA | `e8f616a37f4c23b6a59a6f2e2ff845ce3283f9eb` |
| workers live at | 2026-09-22T11:35:55Z |
| api live at | 2026-09-22T11:36:00Z |
| armed | 2026-09-22T11:37:30Z |
| obs-run | 2026-09-22T11:37:52Z |
| deadline | 2026-09-22T12:07:30Z (1,800 s) |

Operator surface: `render-ops` workflow dispatches. The `bettor-probe.yml`
one-shot runner was **NOT used** — three of its claimed safety properties
failed verification before arming (see `../../WORKFLOW_DEFECTS.md`).

**Credentials appear nowhere in this directory.** The worker logs no
credential, and `render-ops` masks the database password and connection
string before any output is printed. Files here are worker log lines and
database rows only.

## What each file is

| file | what it is | provenance |
|---|---|---|
| `budget_*.txt` | `ingestion_state.bettor_live_probe_state` | `render-ops sql obs-budget` |
| `worker_log_*.txt` | worker stdout | `render-ops logs sportsassets-workers` |
| `rows_*.txt` | observation table counts | `render-ops sql obs-rows` |

## What is a VERBATIM body and what is not

This SHA does **not** persist raw venue bodies. It persists *decisions*
and reports *summaries*. So:

- **Listing** — evidenced by counts (`pages_read`, `rows_listed`,
  `page_size`) and by the selection rule's per-reason refusal tally,
  which reveals which fields the listing did and did not supply. The raw
  JSON body is **not** captured by this SHA.
- **WebSocket** — evidenced by `frame_capture_report()`, which reports
  unexpected keys, missing declared keys, whether an `asks` key exists,
  timestamp parsing, repeat frames per slug and depth thinning. The raw
  frames are held in memory and **not** persisted.

Both gaps are properties of the deployed code, not of this run, and are
named rather than worked around.
