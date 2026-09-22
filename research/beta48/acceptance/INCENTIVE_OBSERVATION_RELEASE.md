# Incentive observation release — activation package

Built on the **verified current production SHA**, on a branch no
service tracks. Nothing has been repointed, merged, deployed or armed,
no credential has moved, and no order exists anywhere in this release.

---

## 0. Provenance

| | |
|---|---|
| **production SHA, verified** | **`ba87076`** — read from `render-ops deploys sportsassets-workers`, `live` since **2026-09-22T13:58:16.368074Z** (the deploy that began 13:57:48.958101Z). The four prior deploys are `deactivated`. |
| **base of this release** | `ba87076`, checked out directly |
| **branch** | `claude/incentive-observation-release` — **tracked by no service** (`edge-shadow`, `sportsassets-workers`, `sportsassets-api` all track `claude/session-njaewf`; `bettortoken-api` tracks `main`) |
| **candidate SHA** | pinned in `INCENTIVE_RELEASE_SHA.txt` beside this file |

---

## 1. The marginal production diff

```
 backend/sportsassets/bettor_incentive_budget.py    | 304 +++  NEW
 backend/sportsassets/bettor_incentive_feed.py      | 374 +++  NEW
 backend/sportsassets/bettor_incentive_journal.py   | 226 +++  NEW
 backend/sportsassets/bettor_incentive_manifest.py  | 473 +++  NEW
 backend/sportsassets/bettor_incentive_state.py     | 184 +++  NEW
 backend/sportsassets/workers/
     bettor_incentive_observe.py                    | 459 +++  NEW
 backend/sportsassets/bettor_market_stream.py       |  57 +++  existing
 backend/sportsassets/workers/bettor_live_loop.py   |  45 +++  existing
 ────────────────────────────────────────────────────────────
 8 files, 2122 insertions(+), 0 deletions(-)
```

**Zero lines removed. Zero lines modified.** Every one of the 2,122
lines is an addition, and only **102** of them land in files that
already existed.

### The 102 lines in pre-existing files, in full

**`workers/bettor_live_loop.py` (+45).** One constant, one predicate,
one delegation:

```python
_INCENTIVE_ENV = "BETTOR_INCENTIVE_MANIFEST"

def _incentive_mode() -> bool:
    return bool(str(os.environ.get(_INCENTIVE_ENV, "")).strip())
```

and, at the top of `main()`:

```python
    if _incentive_mode():
        from . import bettor_incentive_observe as inc_obs
        return await inc_obs.run(stream_factory=stream_factory,
                                 control_pool=control_pool,
                                 run_for_s=run_for_s, sleep=sleep)
```

The delegation is **total, not a branch inside the body**. Everything
below it — listing, BBO enrichment, the decision engine, shadow
execution, settlement polling — is code the incentive run must not
reach, and a structural delegation is what makes that so rather than
care. A single listing refresh would spend six requests against an
eight-request allowance.

**`bettor_market_stream.py` (+57).** A heartbeat listener: three
attributes, `set_heartbeat_listener()`, `_on_heartbeat()`, and a
`try`-guarded `ws.on("heartbeat", …)` beside the handlers already
registered. It changes no existing behaviour — see §4.

### What was NOT touched

`edge-engine/src/edge/venues/pmus_stream.py` (protected research path),
every trading control, every accounting record, `bettor_universe`,
`bettor_universe_probe`, `bettor_live_store`, the decision engine, the
shadow executor, and every workflow file.

---

## 2. The frozen allowlist

`bettor_incentive_manifest.py`. The universe is the **captured
programme manifest** and nothing else.

| rule | |
|---|---|
| source | a manifest file: the verbatim `/v1/incentives` response, its `manifest_id`, its `sha256` digest and the ET date it was captured for |
| gate | **≥10 distinct qualifying markets** at arm, else `INSUFFICIENT_COVERAGE` and the run does not start |
| subscription cap | **12 total** (`BETTOR_INCENTIVE_WATCH_MAX`, ceiling 20). A total, not an addition — this mode subscribes to the allowlist and to nothing else |
| truncation | by **manifest order**, which was fixed at capture before any book was seen; dropped slugs are named in `dropped_by_watch_cap` |
| qualification | daily-event period, `INSTRUMENT_STATE_OPEN`, positive pool, positive Target Size, discount factor in (0, 1]. `status: active` on the *programme* is not the *instrument* being open |
| frozen | for the whole run. No mid-run re-selection, re-ranking or top-up |
| **fallback** | **none** |

### No fallback, structurally

`test_no_module_in_this_release_can_reach_the_seeded_universe` parses
each release module, strips docstrings, and asserts that
`observation_subset`, `bettor_universe` and `_discover` appear nowhere
in the executable code. The general loop's seeded-permutation universe
is not merely unused here — it is unreachable.

### Events are reported apart from markets

Three counts, never one:

| | |
|---|---|
| `markets` | the coverage gate |
| `distinct_programs` | independent parameter draws |
| `distinct_events` | independent underlying events |

and a sentence on every verdict:

> *N markets, P programme id(s), E event start time(s). The coverage
> gate is on MARKETS. Markets sharing a programme id or an event start
> time are NOT independent statistical units, and any interval computed
> as though they were is invalid.*

This is not hypothetical: the culture block retrieved on 2026-09-22
shared one `programId` (`culture_low_20260921`) and one
`eventStartTime` across every market in it.

---

## 3. The eight-request budget

`bettor_incentive_budget.py`. **The unit is one outbound HTTP
request** — pagination pages and retries included.

| kind | cap | covers |
|---|---|---|
| `manifest` | 4 | the `/v1/incentives` discovery read **and every pagination page** |
| `recheck` | 2 | mid-run programme-drift detection |
| `retry` | 2 | transport failure anywhere |
| **total** | **8** | binds even where the sub-caps would not |

A first attempt is charged to its own kind; **a retry is charged to
`retry`**, so "how many distinct questions" and "what did failure cost"
stay separate numbers.

**Programme terms arrive inside the discovery response.**
`ListIncentivesResponse.programs[].timePeriods[]` carries `programId`,
`createdAt`, `rewardPool`, `discountFactor`, `targetSize`, `period`,
`start` and `end`. There is no per-market terms call to budget for, and
that single fact is why four requests cover discovery, terms and
pagination together. Pinned by
`test_terms_arrive_with_discovery_so_no_per_market_call_is_budgeted`,
which reads a full programme from **one** request.

### The acquisition paths are refused by name

Not "unused" — **refused at the reservation**:

```python
FORBIDDEN = {"listing": …, "distinct": …, "bbo_attempt": …,
             "settlement": …}
```

The general worker's startup round spends six listing pages and up to
1,680 BBO reads — about 1,686 requests, which is why its no-start hold
begins at five minutes. A code path that reached one of those during
this experiment fails loudly instead of quietly spending the venue's
patience. The rehearsal confirms **zero** requests of any acquisition
kind.

### The cap survives the restart the supervisor guarantees

`workers/all.py:run_forever` is a `while True`. A ledger that began at
zero each boot would grant eight requests *per restart*. So the count
lives in `ingestion_state` under `bettor_incentive_run`, keyed by **ET
date + manifest id**, and the in-process ledger is **seeded from it**
at boot and written back after every spend. An unreadable count seeds
the ledger **full**, not empty: not knowing what was spent is not
evidence that nothing was.

### Reconnects and resubscriptions are bounded separately

One reconnect resubscribes every slug, so the two counts cannot be the
same number and a single bound would be whichever it was named after.

| bound | default | env |
|---|---|---|
| reconnects | 20 | `BETTOR_INCENTIVE_MAX_RECONNECTS` |
| resubscriptions | 40 | `BETTOR_INCENTIVE_MAX_RESUBSCRIBES` |

Either one exceeded ends the run with its own reason
(`RECONNECT_BOUND_REACHED` / `RESUBSCRIBE_BOUND_REACHED`). The stream's
own 1.0 s → 30 s backoff is unchanged; these bound how long it may keep
trying.

---

## 4. Feed health is not book age

This is the correction that mattered most, and my earlier spec had it
wrong.

That spec said a scoring instant resolves only from a frame ≤5 s old.
The number came from `MAX_RECEIPT_AGE_S`, which is a **trading** gate.
Applied to research sampling it is simply wrong, because **the feed is
change-driven**: a market nobody touches for ten minutes sends nothing
for ten minutes and its book is *current* the whole time. Scoring that
as stale would discard exactly the quiet books whose qualifying uptime
the experiment exists to measure.

### Two verdicts on every sample, never merged

| | |
|---|---|
| **trading eligibility** | `MarketStream.book_at()` — **called and recorded verbatim**. Not relaxed, not widened, not re-implemented |
| **research validity** | connection continuity. **Book age never enters it** |

`test_a_quiet_book_stays_research_valid_long_past_the_trading_bound`
pins both at once: a book untouched for 600 s on a live connection
returns `research_valid=True` **and** `trading_eligible=False` with
reason `STALE_AT_RECEIPT_CLOCK`. Neither gate moved.

### Protocol semantics, as established

| | |
|---|---|
| **snapshot / update** | `BOOK_REPLACEMENT = "ASSUMED_FULL_REPLACEMENT"`. The payload carries whole `bids`/`offers` arrays and the SDK declares no delta type — **consistent with** full replacement, **not proof of** it. The status is carried into every run report so it cannot be promoted by habit. The journal classes the first frame of each epoch `INITIAL_LADDER` and the rest `UPDATE` |
| **subscription ack** | there is none. `MarketMessage` carries no "subscribed" reply, so confirmation is **the arrival of data** and refusal is an `error` naming the batch's `requestId` |
| **reconnect** | a connect increments `epoch`; **every book from an earlier epoch is ineligible whatever its age**, and every slug returns to `REQUESTED`. The missed interval cannot be replayed, so a slug is unscorable in a new epoch until a fresh full ladder arrives |
| **heartbeat** | `Heartbeat` is declared in the `MarketMessage` union and nothing listened for it. This release registers a listener, **guarded** — whether the SDK surfaces it under that name is not verified, and `heartbeat_registered` records whether the handler was accepted while `heartbeats` records whether any actually arrived. Neither is assumed |

### The honest third verdict

With no heartbeat, total connection silence is genuinely ambiguous: ten
quiet markets and a dead socket look identical from the application
side. So beyond `FEED_SILENCE_MAX_S` (60 s of silence across the whole
universe) the answer is **`GAP_LIVENESS_UNDETERMINED`** — a third
verdict, not a quiet promotion to either of the other two. An
undetermined instant is **unobserved**: never counted as a qualifying
second and never counted as a non-qualifying one. Where heartbeats do
arrive, silence is resolved and a quiet book stays valid indefinitely.

Nothing here relaxes a stale-data check. `book_at()` is untouched; a
second, differently-purposed check was added beside it and both are
recorded on every sample.

---

## 5. What is persisted

`bettor_incentive_journal.py`, append-only JSONL, fsync every 5 s.

| record | |
|---|---|
| `RUN_OPEN` | allowlist, manifest id, response digest, window, effective config, budget — and the **reconstruction rule** in words |
| `PROGRAM_VERSION` | programme terms at `ARM` and at each `RECHECK` |
| `EPOCH` | every connection epoch, opened and closed |
| `LADDER` | every frame: `INITIAL_LADDER` or `UPDATE`, `ladder_seq`, **full `bids`/`offers` arrays**, venue `transactTime` **at source precision**, venue state |
| `GAP` | every transition into and out of an unobserved interval, with its reason |
| `RUN_CLOSE` | end reason, feed report, coverage, budget, socket bounds |

**One row per book change, not one per scoring instant.** 864,000 rows
a day of mostly-unchanged books is not information; the changes are.
Instants are reconstructed offline, and the rule travels with the data:

> A scoring instant takes the last `LADDER` at or before it, **if** that
> ladder is in the same `EPOCH` and no `GAP` covers the instant. Never
> carry a ladder across an epoch boundary.

### Research coverage is not trading eligibility

Separate fields, never summed, compared or substituted. A market-date
voided for coverage **was not refused by any rule about what may be
traded** — it is a market we did not watch well enough to score. Every
coverage verdict carries `not_a_trading_verdict` saying so, and
`test_research_coverage_is_not_a_trading_verdict` asserts the word
`eligible` appears nowhere in it.

### What a gap does to a reward estimate

A gap is an **unobserved** interval. We do not know whether the side
qualified during it, so:

- the estimate covers the **observed instants only** and says so;
- it is **never divided by the observed fraction** to recover a full
  day. Scaling assumes the unobserved interval resembled the observed
  one — precisely the assumption a gap destroys, since disconnects
  cluster with venue trouble and venue trouble is when books thin out;
- below 95 % observed, the market-date is **void**, not scored down;
- a boot's fraction is **not** the date's fraction. Only the offline
  reconstruction, reading `EPOCH` and `GAP` across every boot, can say
  what fraction of the ET date was observed.

### The window

`[00:00:00.000000 ET, next 00:00:00.000000 ET)` — half-open, tz-aware
`America/New_York`.

> **A bug found by asserting rather than reading.** My first
> implementation computed the span as `(end - start).total_seconds()`.
> Python subtracts two aware datetimes **naively** when they share a
> `tzinfo` object, so both DST dates came back as exactly 86,400 — the
> one answer the function exists to avoid. Differencing the POSIX
> timestamps forces the UTC conversion. Now pinned:
> 2027-03-14 → **82,800** instants, 2026-11-01 → **90,000**,
> an ordinary date → **86,400**.

---

## 6. Required configuration

Set on **`sportsassets-workers` only**.

| variable | value | effect |
|---|---|---|
| `BETTOR_INCENTIVE_MANIFEST` | absolute path to the captured manifest | **the mode switch.** Unset or blank ⇒ mode off |
| `BETTOR_INCENTIVE_ET_DATE` | `YYYY-MM-DD` | the one ET date to collect |
| `BETTOR_INCENTIVE_WATCH_MAX` | `12` | total subscription cap (ceiling 20) |
| `BETTOR_INCENTIVE_DIR` | a **declared persistent disk** path | journal root |
| `BETTOR_INCENTIVE_DIR_DISK` | `1` | asserts that path is a real disk. Absent ⇒ the run warns, in the log and in `RUN_OPEN`, that its evidence dies at the next deploy |
| `BETTOR_INCENTIVE_MAX_RECONNECTS` | `20` | |
| `BETTOR_INCENTIVE_MAX_RESUBSCRIBES` | `40` | |
| `BETTOR_INCENTIVE_HTTP_CAP` | *(omit)* | can only **lower** 8, never raise it |
| `BETTOR_LIVE_LOOP` | must not be `off` | cheap pre-check only |

The mode switch is **a manifest path rather than a boolean** on
purpose: a mode that cannot be turned on without also saying which
markets it will watch cannot be turned on by accident.

`PMUS_KEY_ID` and `PMUS_SECRET_KEY` are **already present** on
`sportsassets-workers` (verified via `env-keys`: 36 and 88 characters,
names and lengths only). **Nothing is moved, copied, printed or
logged.**

---

## 7. Deployment effects on both services

Both `sportsassets-api` and `sportsassets-workers` track
`claude/session-njaewf` with `autoDeploy=yes`, so **merging redeploys
both**, whatever the diff touches. Render does not diff paths.

| service | effect |
|---|---|
| `sportsassets-api` | **restarts.** No route, model, schema or dependency changes. `app.py` is untouched. Behaviour after restart is identical |
| `sportsassets-workers` | **restarts.** With the mode unset, `bettor_live_loop.main()` behaves exactly as on `ba87076`. With it set, `main()` delegates and the general loop does not run |
| `edge-shadow` | also tracks the branch, so it **also restarts**. Nothing in this release touches it |
| database | **no migration, no DDL.** One new `ingestion_state` key, `bettor_incentive_run`, written only when a run starts |

A deploy is also the event that **reloads the environment** — a restart
alone does not on these services. So the configuration in §6 takes
effect at the deploy, not before.

---

## 8. The observation control stays false through deployment

Four independent facts:

1. **Nothing in the release writes it.**
   `test_nothing_in_the_release_writes_the_observation_control_true`
   parses all six modules, strips docstrings, and asserts that
   `'true'::jsonb`, `bettor_live_observation` and `disarm` appear in
   none of the executable code. The one module that writes
   `ingestion_state` at all issues exactly two statements — one
   `SELECT`, one `INSERT` — both keyed by its **own** row.
2. **Deploying does not touch it.** No migration, no DDL, no startup
   write. The row's value survives the deploy because nothing writes it.
3. **The runner reads it first and fails closed.** Before the manifest,
   before credentials, before a socket. Absent, false, malformed or
   unreadable all mean *do not observe*. Rehearsal S0 and S1 confirm a
   stopped control creates no run row and no journal directory.
4. **Arming stays a human action** through `render-ops sql obs-run`,
   which requires `confirm=DO`.

**The measured pre-deploy baseline**, read this session:

```
action=sql service=sportsassets-db arg=obs-state  at 2026-09-22T19:21:58Z
 bettor_live_observation
-------------------------
 false
```

**Re-read it after the deploy** with the same command. The value must
still be `false` at both points. If it is not, **do not proceed** —
that is a finding, not a formality.

---

## 9. Launch procedure

Steps 1–4 are preparation; the run begins only at step 8.

```
# 1  capture the manifest (out of band, <=4 public unauthenticated
#    requests, charged to the same eight-request ledger)
#      GET https://gateway.polymarket.us/v1/incentives
#          ?statuses=active&program_type=liquidityProgram
#          &instrument_states=INSTRUMENT_STATE_OPEN&page_size=100
#    -> >=10 qualifying markets, or STOP: INSUFFICIENT COVERAGE

# 2  confirm the control is OFF, and what is deployed        [read-only]
   render-ops:  action=sql      service=sportsassets-db      arg=obs-state
   render-ops:  action=deploys  service=sportsassets-workers

# 3  confirm credentials are present, names and lengths only [read-only]
   render-ops:  action=env-keys service=sportsassets-workers

# 4  OWNER DECISION: put the candidate SHA on the tracked branch.
#    Both services restart (§7). Nothing observes yet.

# 5  re-read the control AFTER the deploy                    [read-only]
   render-ops:  action=sql      service=sportsassets-db      arg=obs-state
#    expect: false

# 6  set the configuration (§6). Each env-set redeploys the service,
#    so set them together and let one deploy carry them.

# 7  arm the probe row
   render-ops:  action=sql  arg=obs-arm  confirm=DO

# 8  RUN
   render-ops:  action=sql  arg=obs-run  confirm=DO
```

### Stop

```
   render-ops:  action=sql  arg=obs-stop  confirm=DO
      -> UPDATE ingestion_state SET value='false'
         WHERE key='bettor_live_observation'
```

Effective **inside the already-running process** within
`CONTROL_EVERY_S = 30 s`. No deploy, no restart. Measured in rehearsal
S6 at **30.1 s**. `BETTOR_LIVE_LOOP=off` is a pre-check only — a
restart does not reload the environment on this service; only a deploy
does.

### The run ends on whichever comes first

`ET_DATE_ENDED` · `STOPPED_BY_CONTROL` · `SOCKET_BOUND_REACHED` ·
`UNRECOVERABLE`. Analysis runs once, offline, after the run stops.

---

## 10. Minimal forward rollback

**Forward, never backward** — the 2026-09-21 lesson that a restart does
not reload the environment applies to undoing as much as to doing.

| level | action | cost | effect |
|---|---|---|---|
| **R1 — stop** | `obs-stop` | one `UPDATE` | the run stops within 30 s. The process stays up; the general loop does not resume until R2 |
| **R2 — disable the mode** | `env-del BETTOR_INCENTIVE_MANIFEST`, `confirm=DO` | one deploy | `main()` stops delegating; the general loop returns to `ba87076` behaviour exactly |
| **R3 — remove the code** | revert the merge commit, push | one deploy of both services | back to `ba87076` |

R1 first, always: it is the only one that does not need a deploy.
R2 is sufficient in almost every case, because the release is inert
when unconfigured. R3 exists for completeness.

**There is nothing to unwind.** The run holds no order path, places no
order, writes no trading record, and touches no accounting row. Its
only durable traces are the journal files and the
`bettor_incentive_run` key.

---

## 11. The rehearsal

Ten scenarios, **all through `bettor_live_loop.main()` with no
arguments** — the exact call `workers/all.py` makes. Full output:
`incentive_rehearsal_result.json`.

| | scenario | question | result |
|---|---|---|---|
| S0 | control absent | does absence permit a run? | `CONTROL_ROW_ABSENT`; no run row, no journal directory |
| S1 | control false | does a stopped control cost anything? | `STOPPED_BY_CONTROL`; nothing created |
| S2 | no credentials | does it degrade to a public client? | `NO_CREDENTIALS`; refuses |
| S3 | manifest absent | is that an empty universe? | `MANIFEST_ABSENT`, named |
| S4 | nine markets | does it top up from the general universe? | `INSUFFICIENT_COVERAGE`; **no stream was ever constructed** |
| S5 | ET window ended | does a past date still collect? | `ET_DATE_ENDED` in <1 s; socket closed |
| S6 | collection + DB stop | does it collect, and does the row stop it? | 12 markets, **one** subscribe call of 12 slugs ≤ `SUB_BATCH`; 12/1/1 markets/programmes/events; reconnect → second epoch → 24 `INITIAL_LADDER`s; all six record kinds persisted; **0 acquisition requests**; stop landed in **30.1 s**; 0 orders |
| S7 | restart | does a restart re-grant the allowance? | same `run_id`, boots 1→2, ledger **seeded from the row**, cap binds across boots, recheck sub-cap bounds repeated restarts |
| S8 | exhausted budget | can a spent allowance be spent again? | no; listing/BBO/settlement refused **by name** |
| S9 | mode off | is the deployment inert until configured? | unset and blank both ⇒ off |

**10 of 10 passed**, plus **52 unit tests** in
`backend/tests/test_bettor_incentive_release.py`.

### The transport was simulated — said plainly

`bettor_market_stream.MarketStream` was replaced **in the rehearsal
process** by a `FakeStream`. **No socket was opened. No venue byte was
received. No credential was used** — the key id and secret were the
invented strings `REHEARSAL-KEY-ID` and `REHEARSAL-SECRET`, handed to a
class that ignores them. Every frame in the S5–S7 output was generated
by the rehearsal's own loop.

Everything else was real: the entry point, the mode switch, a real
PostgreSQL 16 `ingestion_state`, real manifest files, real journal
files with real fsync, and the real `CONTROL_EVERY_S` poll.

**What the rehearsal therefore cannot establish:** that the venue's
socket behaves as `MarketStream` assumes — that book frames are full
replacements, that heartbeats arrive under that event name, or that
subscriptions are honoured at this size. Those are venue facts and only
a live run produces them. The release is built to **record** all three
rather than assume them.

### Reused verification

`bettor_market_stream.py` and `workers/bettor_live_loop.py` are the
only pre-existing runtime files changed, and both changes are purely
additive, so their existing suites are the regression check and were
re-run rather than rewritten:

```
python -m pytest backend/tests -k "stream or live_loop or market or incentive"
737 passed, 8 skipped, 10445 deselected in 231.11s
```

**Zero regressions.** No unrelated suite was rerun.

---

## What this release does and does not claim

**Does:** collect one ET date of full ladders from a frozen,
manifest-tied allowlist of ≥10 incentive markets, inside eight public
requests and bounded reconnects, with epochs and gaps recorded, and
stop on one database row.

**Does not:** establish that the incentive programme is profitable,
that our orders would fill, or that any policy works. The reward side
is hypothetical and the cost side is simulated under an execution model
no fill of ours has ever validated. Passing data-collection acceptance
is **not** passing profitability acceptance, and the two stay separate.

**M4 and M5 remain unexecuted. Nothing has been repointed, merged,
deployed or armed. No credential has moved. No order exists in this
release.**
