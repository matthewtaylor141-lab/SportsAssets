# Live probe 3c413436 — measured timeline

All times UTC, from `render-ops` job logs and database reads.

| time | event | source |
|---|---|---|
| 11:35:03.681 | workers build starts, `e8f616a`, `new_commit` | deploys |
| 11:35:55.165 | **workers LIVE on `e8f616a`** (`fd39a0a` deactivated) | deploys |
| 11:36:00.529 | **api LIVE on `e8f616a`** | deploys |
| 11:36:20.294 | worker: `not observing (STOPPED_BY_CONTROL: bare boolean false)` | logs |
| 11:36:55.300 | same, with effective config `max_rps 0.25, concurrency 1, frame_capture_n 25, max_contracts "0"` | logs |
| 11:37:30 | **obs-arm** — `3c413436-68ba-4e50-b106-5d349b372567`, deadline 12:07:30 | sql |
| 11:37:30 | readback: counters 0/0/0, caps 40/160/18, slugs `[]` | sql |
| 11:37:52.708 | **obs-run** — control `true` | sql |
| 11:37:58.026 | supervisor: `starting loop: bettor_live` | logs |
| 11:37:59.528 | `listing hit the 6-page bound; the candidate set is a PREFIX of the venue` | logs |
| 11:38:19 | counters 1 / 7 / 7; slugs include `aachc-mlb-2026-09-27-wr-ath/col/laa` | sql |
| 11:39:33.680 | **HTTP 429** on `...bavg-2026-09-29-leader-fertat`; holding the whole round **10s** (attempt 1 of 3) | logs |
| 11:39:43.692 | HTTP 429 same slug; holding **0s** (attempt 2 of 3) | logs |
| 11:39:58 | counters 1 / 22 / 27 — 5 retries already charged | sql |
| 11:39:59.696 | HTTP 429 `...-jacwil`; holding **7s** | logs |
| 11:40:30.719 | HTTP 429 `...-luiarr`; holding **9s** | logs |
| 11:40:43.733 | HTTP 429 `...-michar`; holding **10s** | logs |
| 11:41:05.780 | HTTP 429 `...-ozzalb`; holding **10s** | logs |
| 11:41:47.813 | HTTP 429 `...-yandia`; holding **9s** | logs |
| 11:41:57.157 | `not starting (EMPTY_UNIVERSE); holding 300s (acquisition backoff)` | logs |
| 11:42:58 | counters **1 / 40 / 50** — distinct ceiling reached exactly | sql |
| 11:43:34.238 | **obs-stop** — control `false` | sql |
| 11:44:05 | counters still **1 / 40 / 50** (+31 s, frozen) | sql |

## Request accounting, reconciled

```
listing attempts reserved     1   of 18    1 listing request issued
distinct markets reserved    40   of 40    CEILING REACHED, never exceeded
BBO attempts reserved        50   of 160   40 distinct + 10 retries
orders                        0            MAX_CONTRACTS=0
journal rows written          0            no market passed the rule
```

Observed pace over the first 119 s: 27 requests = **0.227 req/s**, under
the 0.25 ceiling.

## Shutdown, verified

| time | event | source |
|---|---|---|
| 11:41:57.157 | acquisition already ENDED (`EMPTY_UNIVERSE`), 97 s **before** the stop | logs |
| 11:43:34.238 | **obs-stop** — control `false` | sql |
| 11:44:05 | counters 1/40/50 (+31 s) | sql |
| 11:47:00 | counters 1/40/50 (+206 s) | sql |
| 11:47:02.164 | worker wakes from the 300 s backoff, re-reads the control, `not observing (STOPPED_BY_CONTROL: bare boolean false)` | logs |
| 11:47:02.164 | `not starting (STOPPED_BY_CONTROL); holding 30s (control poll)` | logs |
| 11:47:37.389 | same again — back on the fixed 30 s control poll, no venue requests | logs |

### The two stop criteria, reported precisely

**"Acquisition stops within 58 seconds."** Satisfied, but **not tested by
this run.** Acquisition had already ended at 11:41:57 when the universe
came back empty — 97 seconds *before* `obs-stop` was issued. The
counters were frozen before the stop existed. What this run *does*
verify is the stronger operational property: when the worker next woke
from its 300-second acquisition backoff it re-read the control, found
`false`, and refused — 11:47:02.164, no venue request, no reservation.

**"The stream closes within 90 seconds."** **Vacuous in this run — there
was no stream.** The universe was empty, so no market was ever selected,
no subscription was ever made, and no WebSocket was ever opened. Nothing
was closed because nothing was open. This criterion is **not** reported
as verified.
