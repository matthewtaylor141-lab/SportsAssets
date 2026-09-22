# Consolidated delivery — three acceptance tracks

One release, three tracks, one blocker list. Artifacts are executable;
every number below comes from a run recorded in this directory.

**Verdict in one line: the software is complete and verified, and NO
CANDIDATE QUALIFIES on economics. The single measurement that decides
whether one can is named in §C and is the one the release was built to
take.**

---

## A. The exact release and deployment action

### The release

| | |
|---|---|
| **candidate SHA** | pinned in `INCENTIVE_RELEASE_SHA.txt` |
| **base** | `ba87076` — the SHA `render-ops deploys` reports live on `sportsassets-workers` since 2026-09-22T13:58:16.368074Z |
| **branch** | `claude/incentive-observation-release` — tracked by no service |
| **marginal diff** | ~2.6k insertions, **3 deletions** (the closing brace of three dict literals in `bettor_live_control`; every existing mapping, cap and default survives, and `reserve()`, `read_control()`, `read_budget()` and `disarm()` are untouched) |

**Socket allowance — now reserved BEFORE dispatch, at the boundary.**
Counting crossed epochs afterwards did not satisfy the requirement, and
the correction was right: an attempt already made cannot be bounded by
counting it. The gate moved into `MarketStream` itself, and every
attempt goes through the **same** `bettor_live_control.reserve()` the
HTTP allowance uses — one locked row, control and deadline checked in
the same transaction, committed before the caller may act.

| event | unit | note |
|---|---|---|
| initial connection | `socket_connect` | reserved before `ws.connect()` |
| **failed** connection attempt | `socket_connect` | the venue saw it; the allowance does too |
| reconnect | `socket_connect` | same event, same unit |
| `subscribe_market_data` | `socket_subscribe` | |
| `subscribe_trades` | `socket_subscribe` | **a batch is two messages, so two units** |

Caps live in the armed row (`max_socket_connect` 20,
`max_socket_subscribe` 40) and are **separate from the eight HTTP
requests** — mixing them would let a reconnect storm eat the manifest
allowance. A refusal means the attempt is **not made**: the reader
records it and stops. An uncertain reservation answers no.

A real deadlock was found while proving this. The bridge hands the
reservation to the main loop and blocks the socket thread on it, which
is safe only while `start()` returns immediately. The rehearsal's first
fake reserved inline and hung every reservation.
`test_start_must_not_block_or_the_reservation_bridge_deadlocks` now
asserts `MarketStream.start()` spawns a thread and never joins it.

**Regression — settled by direct comparison, not by assertion.**

| tree | result |
|---|---|
| `ba87076` (untouched production) | **2 failed, 982 passed, 8 skipped** |
| candidate | **2 failed, 1046 passed, 8 skipped** |

*Identical failure set.* Both are `test_calibration_claim.py`, a file
this release does not touch: it calls
`asyncio.get_event_loop().run_until_complete()` from sync test bodies,
which needs a usable current loop, and **any** preceding async suite
leaves it closed. Reproduced on the production SHA with
`test_bettor_live_store.py` in place of ours. Pre-existing; left alone.
**Zero regressions, +64 passing tests.**

**Focused verification** (no broad suites rerun):

| | |
|---|---|
| `test_bettor_socket_allowance.py` | **14 passed** — order, refusal, failed attempts, both messages, durable ceiling, crash, overlap, stop |
| socket + release + stream + live_loop | **288 passed** |
| durability proof, real PostgreSQL | **16 / 16** |
| rehearsal through `main()`, no arguments | **11 / 11**, now driving the real reservation bridge |

### How the bounded manifest capture reaches the worker

**It rides in the deploy.** The worker reads
`BETTOR_INCENTIVE_MANIFEST` as a **path** and loads a file; it has no
capture path of its own and cannot fetch one. So the capture happens
**before** the deploy, here, and the file is **committed**:

```
python research/beta48/capture_incentive_manifest.py --et-date YYYY-MM-DD
  -> <=6 public unauthenticated requests, bounded and refused past it
  -> writes research/beta48/acceptance/incentive_manifest.json
  -> prints a FREEZE PREVIEW: markets / programmes / events, and whether
     the run would return INSUFFICIENT COVERAGE
```

Committing it makes the allowlist **reviewable before it is armed** —
the exact markets, the exact terms and the response digest are in the
diff rather than discovered at runtime.

**This costs the run's allowance nothing.** The eight-request row budget
covers the *run*; this capture happens before the run exists and
enforces its own bound in-process. The run's `manifest` sub-cap
therefore goes unused, and only the recheck and retry units remain in
play — conservative, and the manifest records it.

### What the approval covers — exactly three things

| # | approval | consequence |
|---|---|---|
| **1** | **three-service deployment** of the candidate SHA to `claude/session-njaewf` | `sportsassets-api`, `sportsassets-workers` and `edge-shadow` restart. Behaviour identical to `ba87076`: mode unconfigured, control false |
| **2** | **configuration** of five variables on `sportsassets-workers` | one further deploy of that service; the mode becomes armed-capable. Still nothing observes |
| **3** | **one observation run** — one ET date, one arm, one `obs-run` | the socket opens, the journal fills, and the run ends at the date boundary or on `obs-stop` |

**Not covered, and not requested:** any order, any change to a trading
control, any credential movement, any second run.

### The deployment action — ONE action, requiring authorization

```
# 0  READ-ONLY PREFLIGHT
   render-ops:  action=sql      service=sportsassets-db  arg=obs-state
   render-ops:  action=sql      service=sportsassets-db  arg=obs-incentive
   render-ops:  action=deploys  service=sportsassets-workers
   render-ops:  action=env-keys service=sportsassets-workers
   expect: control false; the deployed SHA; PMUS_KEY_ID + PMUS_SECRET_KEY
           present (names and lengths only, never values)

# 1  THE DEPLOYMENT ACTION  *** REQUIRES YOUR AUTHORIZATION ***
#    Put the candidate SHA on claude/session-njaewf.
#    SCOPE: this redeploys THREE services, because all three track that
#    branch with autoDeploy=yes -- sportsassets-api, sportsassets-workers
#    and edge-shadow. Render does not diff paths. No migration, no DDL at
#    deploy; the journal table is created by the first run under an
#    advisory lock.
#    CONSEQUENCE AT THIS STEP: all three restart and behave exactly as on
#    ba87076, because the mode is unconfigured and the control is false.

# 2  re-read the control AFTER the deploy                    [read-only]
   render-ops:  action=sql  service=sportsassets-db  arg=obs-state
   expect: false

# 3  CONFIGURATION -- on sportsassets-workers only, set together so ONE
#    deploy carries them (an env-set redeploys; only a deploy reloads env)
     BETTOR_INCENTIVE_MANIFEST         = research/beta48/acceptance/incentive_manifest.json
     BETTOR_INCENTIVE_ET_DATE          = YYYY-MM-DD
     BETTOR_INCENTIVE_WATCH_MAX        = 12
#    (the socket ceilings are in the ARMED ROW, not the environment --
#     max_socket_connect 20, max_socket_subscribe 40)

# 4  ARM -- 4/2/2 HTTP caps, 20/40 socket caps, 26h deadline,
#          general acquisition caps ZEROED
   render-ops:  action=sql  arg=obs-arm-incentive  confirm=DO
   render-ops:  action=sql  service=sportsassets-db  arg=obs-incentive

# 5  RUN
   render-ops:  action=sql  arg=obs-run  confirm=DO
```

**Storage.** PostgreSQL table `bettor_incentive_journal` — full ladder
depth, connection epochs, gaps with reasons, programme versions. Rows
carry `run_id` **and** `boot_id`; a resumed boot writes a `BOOT_GAP`
before any of its own records, because `MarketStream.epoch` restarts at
1 in each process and a reconstruction keyed on the bare epoch would
score an outage as a quiet book. The ephemeral file backend is removed,
not demoted.

**Rollback — stop first, always.**

| | action | needs a deploy? |
|---|---|---|
| **R0** | `obs-stop` — ends observation within 30 s **in the running process** | **no** |
| **R1** | `obs-state` / `obs-incentive` — verify by read | no |
| **R2** | `env-del BETTOR_INCENTIVE_MANIFEST` | yes |
| **R3** | revert the merge | yes |

Configuration removal is **not** a stop: it returns `main()` to the
general loop, and with the control still true that loop would begin
discovery — six listing pages and up to 1,680 BBO reads. Two defences:
the sequence above, and structurally, `obs-arm-incentive` zeroes the
general acquisition caps so a mistaken removal reads `BUDGET_EXHAUSTED`
before a client is constructed and issues nothing.

**Live acceptance is NOT claimed and is not substituted for.** No
further simulated rehearsal stands in for it. After authorization and
deployment, acceptance is: the deployed SHA confirmed by `deploys`; the
control read false then true; `obs-incentive` showing HTTP spend ≤ 8 and
`general_max_distinct` 0; and journal rows accumulating under both
record kinds with a real `boot_id`.

---

## B. The executable management demonstration

```
python research/beta48/bettor_management_demo.py
```

→ `acceptance/management_demo.json` · 420 episodes, policy C3
(inventory-aware), queue fraction 0.25, tape-backed. **`orders_sent: 0`.**

Ten decisions, each exposing inputs available at decision time,
alternatives considered, expected cash/fees/uncertainty/capital, the
selected action and reason, and the case-study mechanism with its
evidence:

| # | decision | label | observed |
|---|---|---|---|
| 1 | entry — worth quoting? | REPLAY | 420 episodes admitted; **85% never fill** |
| 2 | quote placement | REPLAY | AT_TOUCH; queue swept, not known |
| 3 | size | REPLAY | **NOT SUPPORTED** — fixed clip, no size model |
| 4 | partial fill | REPLAY | **partial is the normal case**, not the exception |
| 5 | completion / pair | REPLAY | 60 of 60 filled reached `FLAT_PAIRED` |
| 6 | netting to cash | REPLAY | **NOT SUPPORTED** — venue capability |
| 7 | exit / loss-taking | **SCENARIO** | path fires twice, both at **$0.00 realised** |
| 8 | holding | REPLAY | collateral committed whether or not a fill arrives |
| 9 | capital reuse | REPLAY | **NOT SUPPORTED** — settlement-bound |
| 10 | incentive eligibility | **SCENARIO** | eligibility created only at 400–499 competing |

**Three capabilities the engine does not have**, stated in the output
rather than implied away:

1. **Netting to cash before settlement.** A matched pair is worth
   exactly $1 — certain, but not cash. No merge call has been
   demonstrated against this venue, so capital stays committed until the
   event resolves. This is the dominant constraint on capital reuse and
   it is a venue question, not a policy choice.
2. **Adaptive size.** `size` is a constant. There is no size model.
3. **Queue position.** Not observable; swept, and every fill figure
   inherits the sweep.

**Nothing simulated is presented as executed.** Every fill carries
`REPLAY -- tape-priced, NOT an execution`. Decision 7 is labelled
`SCENARIO` precisely because the corpus fires the loss-taking path twice
and closes nothing both times — presenting either as a demonstration of
loss-taking would be showing the rule by showing no loss. Its economics
are instead computed at the verified SEP2026 taker rate: a known **$3.43**
to flatten against **$73.00** at risk if carried.

---

## C. The economic verdict

```
python research/beta48/bettor_economic_verdict.py
```
→ `acceptance/economic_verdict.json`

### Development replay — simulated execution, NOT account results

Four declared candidates, four queue fractions, tape-backed fills:

| candidate | qfrac | net $ | drawdown $ | capital-hours | $/cap-hr |
|---|---|---|---|---|---|
| C0 base | 0.00 | −35.79 | | 65,233 | −0.000549 |
| C0 base | 0.25 | −158.77 | | 82,400 | −0.001927 |
| C2 retired | any | −69.73 … −98.37 | | ~26,000 | −0.0028 … −0.0036 |
| **C3 inventory-aware** | 0.00 | **−90.22** | | 27,955 | −0.003227 |
| **C3 inventory-aware** | 0.50 | **−58.74** | | 27,957 | −0.002101 |
| **C4 incentive-aware LP** | 0.00 | **−10.23** | | 50,506 | −0.000202 |
| **C4 incentive-aware LP** | 0.25 | **−9.62** | | 51,200 | −0.000188 |

**Every candidate is negative at every queue fraction.** C4 loses the
least per capital-hour; C3 is the best of the trading-only candidates
and is still negative.

### C4 against the retrieved programme terms

Terms retrieved 2026-09-22T18:14Z, unauthenticated, from
`GET /v1/incentives`: culture daily pool **$50**, DF **0.25**, Target
Size **500**; crypto 1h **$30**/0.25/500; eFootball live
**$100**/0.50/**5000**.

**The reward is NOT COMPUTABLE from the development corpus, and that is
a finding about the corpus, not a reward of zero.** An episode's
`entry_book` is `{bid, ask, spread, spread_ticks, mid, bid_at_touch,
offer_at_touch}` — **prices only, no sizes at any level**. The reward's
denominator is the discounted size walked to Target Size; with no sizes
there is no walk, no denominator and no share.

> A first version of this module reported a gross reward of `$0.0000`
> across every programme and queue fraction. That was an artifact of a
> book parser returning `None` on all 44 episodes, and reporting it
> would have been a false negative dressed as a measurement. The module
> now checks for depth and refuses.

**What is missing, and from where.** The absence is in the **reduced
episode output** — `entry_book`, the summary the replay emits and the
only thing this calculation reads. Whether the **underlying captured
ladder corpus** carried depth is a separate question about the capture,
and this module has **not** established it either way; recovering it
would mean re-deriving episodes from the raw frames. The honest claim is
"not computable here", not "the depth was never captured".

What **is** computable inverts the formula and needs no depth:

**These are TRANSFERRED SCENARIOS, not measured qualification hurdles.**
Three separate reasons, carried in the output beside every figure:
the programme terms come from *other* markets; the loss is a *simulated
replay*; and a qualification requires rewards and trading losses
measured on the **same markets under the same policy**, which these are
not.

| programme | addressable pool $ | required share *(transferred scenario)* |
|---|---|---|
| culture ($50/day) | 244.31 | **3.94%** |
| crypto ($30/day) | 146.58 | **6.56%** |
| eFootball ($100/day) | 488.61 | **1.97%** |

**Scoring exposure is now resting quantity over time**, not wall clock.
The first version multiplied the pool by an episode's fraction of a day
as though the full clip rested on both sides throughout. It does not: a
fill removes resting size (partials are the normal case here), a
cancellation removes it entirely (C3/C4 cancel the opposite leg on the
first fill), and two sides are two exposures. Integrating size-weighted
resting time gives **4.89** full-clip two-sided days against a naive
wall-clock **10.78** — the correction more than halves the addressable
pool. Fill *timestamps* are absent from the corpus, so a reduction is
placed at the cancel instant where one exists and at the episode
midpoint otherwise; that places it approximately and does not change its
magnitude.

And a **labelled scenario** sweep over competing depth at our price,
with a 100-contract clip against Target Size 500:

| competing size | our share | reward $ | covers loss? |
|---|---|---|---|
| 0 / 100 / 250 | 0.0000 | 0.00 | no — **the side never reaches Target Size** |
| 400 | 0.2000 | 107.83 | **COVERS** |
| 500 | 0.1667 | 89.86 | **COVERS** |
| 1000 | 0.0909 | 49.01 | **COVERS** |
| 2500 | 0.0385 | 20.74 | no — diluted below the hurdle |

eFootball never qualifies at any tested depth: Target Size 5000 against
a 100-contract clip.

### The verdict

**NO CANDIDATE QUALIFIES.** Not one is credibly positive on net
economics. C0, C2 and C3 are negative on trading alone with no
offsetting mechanism. C4 is negative on trading and its offset cannot
be evaluated from the evidence in hand.

**The precise thing that prevents qualification** is not a policy and
not a cost — it is **one unmeasured input: the resting depth at the
touch in programme markets**. C4's economics invert entirely on it:

- below ~400 competing contracts the side never reaches Target Size and
  the reward is **exactly zero**;
- between ~400 and ~1000 the reward **covers the trading loss several
  times over**;
- above ~2500 dilution puts it back under water.

The corpus cannot supply that number — it records no sizes. **The
specific experiment that resolves it is the observation release in §A**:
one ET date of full ladder depth across ≥10 qualifying markets, which
journals exactly the quantity the reward denominator needs. That is not
"more research"; it is a named, bounded, already-built measurement with
a defined decision at its end.

### Separation of evidence

| kind | what it covers | status |
|---|---|---|
| **ACTUAL account** | 3,285 real executions: fee engine validated (maker −0.012495 vs published −0.0125; taker +0.059971 vs +0.06; exact to the cent on all 345 maker fills). Position state: **no filled positions reported at 2026-09-22T17:51:18Z**; ~$32.02 and two possibly-resting orders unresolved; account identity unverified. | measured |
| **DEVELOPMENT replay** | the table above. Corpus **fully consumed** — nothing here is out of sample. | simulated |
| **PROSPECTIVE** | the depth measurement. Can establish qualifying uptime, competing depth and hence the reward denominator. **Cannot** establish our fill-conditioned profitability, because it places no orders. | not yet taken |

### Metrics required by the directive

| metric | value |
|---|---|
| net P&L | negative for all four candidates at all four queue fractions |
| residual exposure | reported per candidate in `economic_verdict.json`; all paired residuals are `MATCHED_PAIR_PAYS_1` — certain value, **not cash until settlement** |
| drawdown | peak-to-trough on the episode cash series, per candidate |
| capital-hours | 27,955 (C3) … 51,242 (C4) over the corpus |
| supportable turnover | **bounded by settlement cadence, not engine speed** — with no netting call a committed dollar cannot be reused until the event resolves (demo decision 9) |

---

## D. External dependencies — smallest concrete action each

| # | dependency | smallest action | who |
|---|---|---|---|
| **D1** | **Deployment authorization.** The release cannot be verified in the actual runtime without it, and scope is three services. | Approve step 1 of §A. | you |
| **D2** | **Account identity reference.** Reconciliation is narrowed to "no filled positions at the read timestamp" because nothing trusted exists to compare the venue's identifier against. | Set `pmus_account_ref` on `sportsassets-api` from your account records, out of band. Never in chat. | you |
| **D3** | **Netting/merge capability.** Unknown whether the venue supports releasing a matched pair before settlement. This sets capital velocity and therefore every per-capital-hour figure. | One documentation read, or one authenticated read-only capability probe. | either |
| **D4** | **Payout aggregation grain.** The $1 minimum's unit is undocumented; A1/A2/A3 remain open. | One authenticated `GET /v1/incentives/earnings` **after** a period in which we earned something. Requires D1 first. | sequenced |

---

## E. Remaining schedule — software vs evidence

**Software completion: done, pending only authorization.** No further
build is required for the observation release. Tracks 2 and 3 are
delivered as executable artifacts.

| phase | elapsed | gated by |
|---|---|---|
| deploy + live acceptance | **~1 hour** | D1 |
| manifest capture + arm + one ET date | **~26 hours** | the calendar — a day is a day |
| offline scoring + verdict | **~1 hour** | nothing |
| **→ depth question answered** | **~2 days from authorization** | |

Beyond that, and it is important not to blur the two:

- **Reward *confirmation*** needs an authenticated earnings read after a
  period we actually earned in — the programme calculates within 5
  business days of period end and credits within 2 more, so **~7
  business days** after any qualifying activity. That requires resting
  real orders, which is a separate authorization.
- **Fill-conditioned profitability** cannot be established by observation
  at all. It needs our own orders in the book. No amount of watching
  substitutes for it, and I am not going to imply otherwise.

So: **~2 days** to know whether C4's reward can clear its hurdle;
**~2 weeks** from a funded-order authorization to independent economic
evidence of whether it actually does.

---

## The blocker list

Each tied to an acceptance requirement and a concrete resolution.

| # | blocker | blocks | resolution | state |
|---|---|---|---|---|
| **B1** | deployment not authorized | A — live acceptance | approve §A step 1 | **open, yours** |
| **B2** | competing depth unmeasured | C — C4 qualification | the §A observation run | **open, unblocked by B1** |
| **B3** | netting capability unknown | B(6,9), C — capital velocity | D3 | **open** |
| **B4** | payout aggregation grain | C — floor treatment | D4, after B1 | **open, sequenced** |
| **B5** | account identity unverified | actual-results attribution | D2 | **open, yours** |
| **B6** | no size model | B(3) | not on this release's path; record only | **accepted limitation** |
| **B7** | corpus consumed | C — out-of-sample claims | fresh capture; the §A run is the first | **open, unblocked by B1** |
| **B10** | rewards and losses not measured on the same markets/policy | C — any qualification | the §A run collects depth on programme markets; the replay must then be run on **those** markets | **open, unblocked by B1** |
| ~~B8~~ | ~~socket attempts not reserved before dispatch~~ | ~~A~~ | reserved at the connect/subscribe boundary through `reserve()`; 14 focused tests | **CLOSED** |
| ~~B9~~ | ~~regression unverified~~ | ~~A~~ | baseline vs candidate, identical failure set | **CLOSED** |

---

## What is not claimed

Profitability. Not for C4, not for anything. The one candidate whose
economics could plausibly turn positive does so only inside a band of a
quantity nobody has measured, and the honest statement is that the band
exists and the measurement is one authorization and two days away.

Nor is the search abandoned. C2 stays retired and is not retuned; C0 and
C3 are negative on their own terms; C4 is **undetermined**, which is a
different verdict from failed, and the experiment that determines it is
built, tested and waiting.

**No funded orders. No trading-control changes. No credential movement.
No unapproved deployment. M4 and M5 unexecuted.**
