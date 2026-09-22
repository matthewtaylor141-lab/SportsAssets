# Package B — operational preflight

**Research design frozen at `bd56ee2`** (pinned by `f62ef6b`). Nothing in
this document changes the design, the policy, the statistical protocol or
the decision rule. It is an operational check of whether the frozen design
can be *executed*, and it answers five questions put to it.

**Headline: it cannot be launched as specified.** Three independent
blockers are below, one of which was mis-stated in Package B §7 and is
corrected here. This is a decision for the owner, not something to work
around.

**M4 and M5 remain unexecuted. No order is placed. Nothing is deployed,
armed or merged by this document.**

---

## 1. How the runner obtains the live ladder — and it is authenticated

### The correction

Package B §7 says **"authenticated calls: zero."** That statement is true
**only of HTTP**. The ladder does not arrive over HTTP; it arrives over a
WebSocket, and **that WebSocket authenticates.**

`backend/sportsassets/bettor_market_stream.py`

| line | fact |
|---|---|
| 141 | `def __init__(self, key_id: str, secret_key: str, *, on_book=None, on_trade=None, autostart: bool = False)` |
| 706–707 | `ws = MarketsWebSocket(key_id=self._key_id, secret_key=self._secret_key)` |

`backend/sportsassets/workers/bettor_live_loop.py` lines 1910–1920 read
`pmus_key_id` / `pmus_secret_key` from settings and **refuse to start**
without them, with the comment: *"NAMED, not degraded to a public client.
A stream that cannot authenticate produces no books, and 'no books' must
never look like 'a quiet market'."*

So the corrected line for §7 is:

> **HTTP requests: ≤8, all public and unauthenticated. WebSocket session:
> authenticated with the PMUS key pair. Authenticated *HTTP* calls: zero —
> the 9-request account-read allowance is untouched.**

### The already-authorized credential arrangement

`PMUS_KEY_ID` and `PMUS_SECRET_KEY` are **already present in the
environment of `sportsassets-workers` (`srv-d9gcv6urnols73ce6erg`)**,
verified this session through render-ops `env-keys`, which returns key
names and lengths only: `PMUS_KEY_ID (36 chars)`, `PMUS_SECRET_KEY
(88 chars)`. No value was read, printed, logged or copied, and none will
be. The arrangement Package B uses is **exactly this one, unchanged**:
the process that already holds the credentials opens the socket. Nothing
is moved to a runner, a new service, or the repository.

**GitHub Actions runners do not hold these secrets.** That is deliberate
and stays that way. It is also half of blocker #1 below.

### Connection, reconnect and subscription limits

| limit | value | source |
|---|---|---|
| concurrent connections | **1** | one `MarketsWebSocket` per loop |
| slugs per subscribe request | **100** (`SUB_BATCH`) | `bettor_market_stream.py:88`, SDK-documented ceiling |
| subscription ceiling | **600** (`MAX_SUBSCRIPTIONS`) | `bettor_market_stream.py:91` |
| **worker's own watch cap** | **8** (`WATCH_MAX`) | `bettor_live_loop.py:167` — *the binding one; see blocker #2* |
| reconnect backoff | 1.0 s, doubling, **capped at 30.0 s** | `bettor_live_loop.py:701, 775–776` |
| reconnect attempts | unbounded while the control flag is true | same |
| subscriptions per reconnect | **re-subscribes the same set; creates no new subscription** | — |

---

## 2. Running from the untracked preparation branch — not supported

### Verified refs

| | |
|---|---|
| preparation branch | `claude/bettor-none-pool-fix` |
| local HEAD = `origin/` HEAD | **`f62ef6b`** (design frozen at `bd56ee2`, pinned by `f62ef6b`) |
| auto-deploy branch | `claude/session-njaewf`, tip **`ba87076`** |
| services tracking it | `sportsassets-api`, `sportsassets-workers`, `edge-shadow` — all `autoDeploy=yes` |
| services tracking the preparation branch | **none** |

The SHA actually deployed on `sportsassets-workers` has **not** been
confirmed this turn. It must be read with render-ops `deploys` before any
launch; the branch tip is not proof of what is running.

### Why the dispatch route does not carry the code

`render-ops.yml` is a `workflow_dispatch` workflow, so GitHub will happily
run **the workflow file** at `claude/bettor-none-pool-fix`. That does not
help, because **render-ops never checks out the repository.** It calls the
Render API and `psql`. Its `obs-arm` / `obs-run` / `obs-stop` actions write
rows in `ingestion_state`; they do not ship code.

The code that would then execute is **whatever is deployed on
`sportsassets-workers`** — that is, the tracked branch. Dispatching from
the preparation branch changes which YAML runs; it does not change which
Python runs.

> **There is no supported route to launch this collection from the
> untracked preparation branch.** The only two routes are (a) merge into
> `claude/session-njaewf` — which the instruction forbids doing *solely to
> launch this collection* — or (b) repoint the service's tracked branch,
> which is a production configuration change and is not authorized here.
> **This is the owner's decision.**

### The "empty backend diff" claim was wrong

Package B §7 said: *"Package B adds no production code — only
`research/beta48/` modules and this document. If Package A is not being
deployed, this merge changes no service behaviour."*

That is wrong, on two counts.

1. **Render redeploys on *any* push to the tracked branch.** It does not
   diff paths. A merge that touches only `research/beta48/` still restarts
   `sportsassets-api`, `sportsassets-workers` and `edge-shadow`. An empty
   backend diff establishes nothing about restarts.
2. **A restart is not free here.** It cycles the running observation
   process, drops the open socket, and — per the finding recorded at
   `bettor_live_loop.py:80–96` — a **deploy** is precisely the event that
   *does* reload the environment, which is the mechanism a restart lacks.
   A merge is therefore an environment-reloading event, not a no-op.

### Blocker #2 — the deployed worker cannot collect what Package B specifies

Separate from the branch question, three properties of the deployed worker
are incompatible with the frozen design. Two are real blockers; one is not.

| # | requirement | deployed code | blocker? |
|---|---|---|---|
| B1 | **≥10 distinct qualifying markets** | `WATCH_MAX = 8` is a **total** subscription cap (union of strategy-admitted and observation-only). Overridable by `BETTOR_LIVE_WATCH_MAX`, but **env-set redeploys the service**. | **yes** |
| B2 | universe = markets with an active `daily_event` liquidity programme covering the target ET date, **frozen at arm** | `observation_subset()` picks a *seeded deterministic permutation* over the enriched listing. There is **no incentive-programme filter anywhere in the worker**, and no configuration route to supply a fixed slug list. This is new code. | **yes** |
| B3 | 1 Hz incentive scoring | `bettor_incentive_score.py` exists only on the preparation branch and **nothing calls it**. | **no** — see below |

**B3 is not a blocker, and this is a genuine positive finding.**
`to_observation()` (`bettor_market_stream.py:811–864`) already persists
everything the scorer needs:

- `multi_level_depth.{bid,ask}` — the **full ladder**, one entry per level
  with explicit `level`, `price` and `qty`, best-first, `qty` carried as a
  string and parsed as float (partial-contract markets);
- `book_source_ts` — the venue's own `transactTime`, at source precision;
- `book_received_ts`, `book_age_s`, `venue_state`.

Package B §7 already requires that *"analysis runs once, after the run
stops."* So the scorer runs **offline against journalled rows**, and no
scoring code needs to be deployed at all. B1 and B2 remain.

---

## 3. The eight-request allowance, and the coverage requirement

### The allowance covers discovery, terms, pagination and retries

The reason 8 is enough is a property of the API, not an estimate:

> `GET /v1/incentives` returns, for each programme, a **`timePeriods[]`
> array already carrying `programId`, `programType`, `start`, `end`,
> `rewardPool`, `discountFactor`, `targetSize`, `period`, `createdAt` and
> `minTakerNotional`.**

**Programme terms arrive in the same response as discovery.** There is no
per-market terms call. This was confirmed against the live responses
retrieved under P3.

| phase | requests | what it buys |
|---|---|---|
| arm — universe + terms | **≤4** | `?statuses=active&program_type=liquidityProgram&instrument_states=INSTRUMENT_STATE_OPEN&page_size=100`, following `nextPageToken`. At `page_size=100` the live active-liquidity universe fitted in **one** page, so the remaining 3 absorb pagination *or* arm-phase retries. |
| mid-run parameter re-check | **≤2** | detect a `programId` / `createdAt` / pool / DF / targetSize change |
| held for retries | **2** | transport failures anywhere |
| **total** | **≤8** | hard cap; the collector refuses past it |

Every request counts against the cap — including a redirect-followed
retry, a 429 retry and each pagination page. **The budget is never
expanded.** If the arm phase cannot resolve the universe inside it, the
run does not start.

### Coverage requirement at start

> **≥10 distinct qualifying markets must resolve at arm.** Fewer →
> **`INSUFFICIENT COVERAGE`**, reported with what was found, and the run
> does not start. No budget expansion, no second attempt, no relaxation of
> the filter.

### Ten markets are not ten independent statistical units

The live active-liquidity universe retrieved under P3 included a block of
culture (Billboard) markets that **share one `programId`
(`culture_low_20260921`) and one `eventStartTime`
(`2026-12-27T04:59:00.000Z`)**. They are outcomes of **one event**, drawing
on programme terms set once.

So coverage must be reported as three separate counts, and the first is
not a substitute for the others:

| count | what it is |
|---|---|
| **distinct markets** | the coverage gate (≥10) |
| **distinct `programId`s** | how many independent parameter draws |
| **distinct `eventStartTime`s** | how many independent underlying events |

**≥10 markets satisfies the coverage gate and says nothing about
independence.** Any interval or power statement that treats market-dates
as independent units is invalid when the event count is small; the
clustered treatment in the frozen protocol (event cluster as the unit)
applies, and the report must state the realised cluster count rather than
the market count.

---

## 4. The collection window

### The window

**`[00:00:00.000000 ET, next 00:00:00.000000 ET)`** — half-open, one ET
date, tz-aware `America/New_York`.

- DST is handled by the **zone**, not by a fixed −4/−5 offset. A
  spring-forward date is **23 h**; a fall-back date is **25 h**. Neither is
  padded or truncated to 24 h, and the scoring-instant count is derived
  from the realised window, not assumed to be 86,400.
- **Subsecond timestamps are preserved at source precision.** The ordering
  key is the venue's `transactTime` (`book_source_ts`); `book_received_ts`
  is ours and is kept beside it, never substituted for it. The 1 Hz is a
  **scoring resample**, not a storage truncation — nothing is rounded to
  the second on the way to disk.

### Reconstructing 1 Hz from a change-driven feed

This has to be stated, because the feed is **not** a 1 Hz sample: the
stream emits **on change**, and the loop drains dirty slugs every
`POLL_S = 0.2 s`.

> Each 1 Hz scoring instant takes the **last frame at or before it,
> forward-filled** — valid because, on a change-driven feed *while the
> connection is up*, "no frame" means "the book did not change."

That validity has two conditions, and an instant that fails either is a
**gap, not a quiet book**:

1. the connection was up across the whole interval since that frame, and
2. the forward-fill age is **≤ F = 5.0 s** (`book_age_s` bounds it).

### Freshness, completeness, gaps

| rule | |
|---|---|
| **freshness** | an instant resolves only from a frame with age ≤ **5.0 s** |
| **gaps** | counted and reported per market-date; **never filled, never interpolated**. A disconnect is recorded with its start and end. |
| **completeness** | a market-date is **complete** iff ≥95% of the realised window's scoring instants resolve to a fresh frame **and** the market was `INSTRUMENT_STATE_OPEN` across the whole scored span |
| **>5% gap** | market-date **partial → void**. Not scored down, not pro-rated. |

### If a market closes, or programme terms change

- **Market closes mid-window** → that market-date is **void**. It is *not*
  rescaled to the open portion: the reward pool is a **period** pool, and a
  partial period's share is not the period's share. The close time and
  state transition are recorded.
- **`programId` or `createdAt` changes mid-window** → void (already in the
  frozen design).
- **`rewardPool`, `discountFactor` or `targetSize` changes without a
  `programId` change** → also **void**, with **both** parameter sets
  recorded. A silently re-priced programme is not one programme.
- **No automatic extension.** Voided units reduce coverage. If coverage
  falls below 10 the run returns **`INSUFFICIENT COVERAGE`**. It does not
  run a second date, extend past midnight, or re-arm.

---

## 5. The four-contract $0.40 assertion — withdrawn

**Withdrawn. It is not supported and is removed from the package.**

Two errors, either of which is fatal on its own:

1. **Target Size is not the reward-share denominator.** The denominator is
   the **discounted** score of every level inside the walk,
   `Σ DF^ticks × size`. At DF 0.25 that is strictly smaller than the raw
   size walked, and it depends on the *shape* of the book — how much sits
   at the touch versus one, two, three ticks back. Target Size only decides
   **where the walk stops**.
2. **A snapshot share is not a reward.** The reward is
   `pool × (Σ share over qualifying side-snapshots ÷ qualifying
   side-snapshots)`, and it is **zero in every snapshot where the side does
   not qualify**. Multiplying one snapshot's share by a pool is exactly the
   error `period_reward()` was written to prevent, and
   `test_a_single_snapshot_is_never_multiplied_by_a_pool` pins it.

**What *is* supported** — and only as book-specific arithmetic, with no
dollar figure attached. At `targetSize = 500`, four contracts added at the
best price change eligibility **only in the narrow band where the side
already holds 496–499**:

| side holds (raw, ex-us) | +4 contracts | eligibility | our snapshot share |
|---|---|---|---|
| 450 | 454 | **no** — still below 500 | 0 |
| 495 | 499 | **no** | 0 |
| **496** | 500 | **created** | 0.0080 |
| **499** | 503 | **created** | 0.0080 |
| 500 | 504 | already qualified | 0.0079 |

So the corrected claim is: *four contracts cannot rescue a side at 450
against a 500 target, but can create eligibility at 496. Its effect
depends on the observed book.* Any dollar figure requires a **named
book, a named programme, and an integration over the period** — none of
which a single snapshot supplies.

**M5 stays mechanics-only and unexecuted**, and the programme's
reward-reporting delay (calculated within 5 business days of period end,
credited within 2 more — up to ~7 business days) means a short M5 could
not observe its own reward in any case; confirmation would need a
separately-budgeted **authenticated** `/v1/incentives/earnings` read.

---

## 6. Launch, configuration, storage verification, stop

**None of this is authorized to run.** It is written out so the owner can
see exactly what a launch would consist of, *after* deciding the branch
question in §2. Steps 0a and 0b do not exist yet and are the reason the
answer to "can it launch from the preparation branch" is no.

### Prerequisites that are not satisfied

| | |
|---|---|
| **0a** | a route that runs Package B's universe selection on `sportsassets-workers` — **requires merge to `claude/session-njaewf` or a tracked-branch change; neither is authorized** |
| **0b** | `BETTOR_LIVE_WATCH_MAX ≥ 10` — **`env-set` redeploys the service** |
| 0c | confirm the deployed SHA with render-ops `deploys` (not done) |

### The sequence, once and only once 0a–0c are resolved

```
# 1 — confirm what is actually deployed (read-only)
   render-ops:  action=deploys   service=sportsassets-workers

# 2 — confirm the credentials are present, names and lengths only
   render-ops:  action=env-keys  service=sportsassets-workers
                expect: PMUS_KEY_ID (36 chars), PMUS_SECRET_KEY (88 chars)

# 3 — confirm the control flag is OFF before arming (read-only)
   render-ops:  action=sql       arg=obs-state

# 4 — arm: write the probe row
   render-ops:  action=sql       arg=obs-arm      confirm=DO

# 5 — run: set bettor_live_observation = true
   render-ops:  action=sql       arg=obs-run      confirm=DO
```

### Configuration

| setting | value |
|---|---|
| `BETTOR_LIVE_MAX_CONTRACTS` | **`0`** — no order capability |
| `BETTOR_LIVE_WATCH_MAX` | **`10`** minimum (see 0b) |
| `BETTOR_LIVE_LOOP` | must **not** be `off` — cheap pre-check only |
| subscriptions | **1** connection, ≤100 slugs per request, ≤10 slugs used |
| HTTP cap | **≤8**, public, unauthenticated, including pagination and retries |
| authenticated HTTP | **zero** — the 9-request account allowance is untouched |
| expired probe budget | **untouched** |
| hypothetical quote | 100 contracts per side at the touch, **never sent** |
| window | `[00:00:00 ET, next 00:00:00 ET)`, one date |
| scoring | offline, once, after the run stops |

### Storage verification

Before arming, **and** at the end of the window:

```
render-ops:  action=sql  arg=obs-state      # probe row, flag, counters
render-ops:  action=sql  arg=tables         # row counts
render-ops:  action=sql  arg=sizes          # disk
```

Acceptance, checked **after** the run and **before** any scoring:

1. every stored observation carries a non-null **`multi_level_depth`** with
   ≥1 bid or ask level — a top-of-book-only row cannot be walked to Target
   Size and is not scorable;
2. `book_source_ts` is present and **subsecond**, not `NO_TS` /
   `NOT_IDENTIFIED`;
3. per market-date: instants resolved, instants gapped, gap fraction,
   longest gap, disconnect count;
4. `programId` and `createdAt` unchanged across the window per market.

Any market-date failing 1, 2 or 4, or exceeding the 5% gap bound in 3, is
**void** and is named in the report with its reason.

### Stop

**Authoritative:**

```
render-ops:  action=sql  arg=obs-stop  confirm=DO
   -> UPDATE ingestion_state SET value='false'
      WHERE key='bettor_live_observation'
```

Takes effect **inside the already-running process** within
`CONTROL_EVERY_S = 30.0 s`. No deploy, no restart.

`BETTOR_LIVE_LOOP=off` is a **cheap pre-check only**: a restart does not
reload the environment on this service — only a deploy does. It is not the
kill switch.

**End conditions**, whichever comes first: the ET date ends; the stream is
unrecoverable; the HTTP cap is reached; the control flag is set false.

**Rollback:** the run is read-only and holds no order path — the worker
imports no order function and runs at `MAX_CONTRACTS=0`. There is nothing
to unwind. If 0a was resolved by a merge, rollback is revert + redeploy,
which is itself a restart of all three tracked services.

---

## What this preflight establishes

1. The ladder stream **authenticates**; "zero authenticated calls" was true
   only of HTTP, and §7 is corrected.
2. The credentials for it already exist on `sportsassets-workers` and stay
   there. Nothing is moved.
3. **There is no supported route to launch from the untracked preparation
   branch**, because render-ops ships no code and the runners hold no PMUS
   credentials. The choice between merging and repointing the tracked
   branch is the owner's.
4. An **empty backend diff does not prevent a restart** — Render redeploys
   on any push to a tracked branch, and a deploy is exactly the event that
   reloads the environment.
5. Two further blockers sit in the deployed worker: `WATCH_MAX = 8` against
   a requirement of ≥10, and no incentive-programme universe filter. The
   scorer itself is **not** a blocker — the persisted rows already carry
   full ladder depth and venue timestamps, and scoring runs offline.
6. The 8-request allowance **is** sufficient, because programme terms
   arrive in the discovery response; ≥10 distinct markets are required at
   arm, and ten markets sharing one `programId` and one `eventStartTime`
   are **not** ten independent statistical units.
7. The four-contract `$0.40` assertion is **withdrawn**.

**M4 and M5 remain unexecuted. Nothing was deployed, armed, merged or
ordered.** This is preparation for an observation-only activation
decision, not authorization to launch or trade.
