# Shadow desk — management handoff, 2026-09-23

## 0. THE HEADLINE, in four lines

1. **The live shadow desk is running and writing.** The chain the
   audit found broken is closed end to end.
   **But two of my claims about it were wrong and are corrected in
   §3b–§3c: it was not restart-safe, and the fee diagnosis overreached.**
   Both are now fixed in code and the accounting gap is published as a
   bounded correction rather than a number.
2. **Nothing it has done is a result yet.** Under an hour of running,
   realized **+$11.48** (maker rebate income), nothing settled. Its
   first 37 minutes were booked with **no fee schedule at all** — a
   defect found by reading the numbers, fixed, and described in §3b,
   along with why `invariant_ok` could not detect it.
3. **The improvement loop ran two complete cycles and promoted
   nothing.** Ten variant-evaluations, zero acceptances. The active
   policy is unchanged and frozen.
4. **The measured obstacle is now uncertainty, not P&L.** Candidate
   effects ($3–$381) are smaller than the measurement uncertainty
   ($41–$8,739), and every policy's response to the unidentified fill
   assumption **flips sign between partitions — including the frozen
   baseline.**

## 0a. THE SHADOW ACCOUNT WAS RESET — authorized, 2026-09-23T16:06:51Z

**New account: `acct_fc2d773a2afa4851`** · opening balance **$100,000.00**
· zero positions · *"New shadow account following an accounting-recovery
defect."*

**This is a shadow-account reset only.** No real money, no venue order,
no observation control and no collection allowance changed.

### Why

The loop restarted repeatedly without restoring its book. Position rows
accumulated from several abandoned books while cash tracked only the
last, and when the restore was finally added it adopted all of them
against that single cash figure — **96 legs where the running book had
52, and the ledger identity off by $2,367.72.** Which leg belongs to
which book is **not in the record**, because `boot_id` was written as a
per-row timestamp, so no arithmetic recovers it.

### What was preserved, not repaired

| | (UNASSIGNED) — preserved |
|---|---:|
| decisions | 2,308 |
| orders | 183 |
| fills | 6 |
| positions | 98 legs, $6,084.00 |
| ledger snapshots | 213, `invariant_ok = f` |

Incident `DESK_ACCOUNTING_RECOVERY_2026_09_23` · **records_deleted 0** ·
**compensating_cash_posted $0.00**. Its P&L is
**`UNRELIABLE_DO_NOT_QUOTE`**. Its inventory is **UNATTRIBUTABLE** —
which is *not* closed, *not* zero and *not* valued.

### The new book, read back at 16:07:33Z

cash $99,994.92 + inventory $5.11 − realized $0.03 = **$100,000.00
exactly**, `invariant_ok = t`.

### What now enforces it

- **`account_id` (durable book) ≠ `epoch_id` (per process).** Conflating
  them is what caused the defect.
- **Created exactly once** — a partial unique index admits one ACTIVE
  account per desk, so a raced second insert fails at the database.
- **Capital cannot be replenished.** `opening_balance` is written by the
  creating INSERT alone; the resume path never touches it.
- **Unassigned rows cannot enter the new book** — excluded by
  `account_id IS NULL`, not by a filter anyone must remember.
- **Cash and cursor move in one statement** inside the persist
  transaction.
- **Consumption ledger persisted** at `(evidence_id, account_id)`, which
  is what lets resting orders be *restored* rather than expired.

### Not a FINAL evaluation set

The new period is **not** automatically one. That status requires a
frozen policy, criteria declared in advance and genuinely prospective
decisions. The account row says so explicitly.

## 0b. Published and verified at 15:30:47Z

The acceptance probe against the live host confirms both new tabs are
now in the bundle management actually loads:

```
['desk', 'Desk · REPLAY']
['learning', 'Learning loop']
```

All nine pre-existing COMMAND views still routed · no credential-shaped
string in the bundle · no order or mutation path in `shadow.js` · every
shadow route 401 unauthenticated, direct and proxied.

**The collector was not disturbed.** Publishing required a commit on
`claude/session-njaewf`, which both Render services track with
`autoDeploy=yes`. The `[skip render]` mechanism was verified from the
deploy history *before* being relied on — `7f76fd9` carries the
directive and appears only with trigger `api`, never `new_commit`,
while `d630d3d` without it deployed as `new_commit`. After the push,
the worker's deploy list is byte-identical to the baseline (newest row
still `7f76fd9 · live · api · 10:26:55Z`) and the API is still on
`503a42b`. No `new_commit` row on either service.

### One open observation, not smoothed over

`ENTER` last fired at 15:13:47 and `COMPLETE_PAIR` at 15:15:20 —
**no new entries in the last ~16 minutes** — while `HOLD` (15:30:38)
and `NO_TRADE` (15:30:14) continue. The desk is alive and deciding; it
is refusing. Whether that is risk limits binding or an absence of
qualifying opportunities is **not yet established**, and it is the
first thing to check on the next read rather than something to assume
is healthy.

## 1. Where to look

**`https://command.bettortoken.com`** →
**Desk · REPLAY** tab (replay), **Learning loop** tab (the improvement
cycles), `/api/command/desk/live` (live-lane state).
Desk password unlock; HttpOnly session cookie scoped to `/api/command`.

## 2. Release, policy, model

| | |
|---|---|
| API release | `90ceca3` (live chain today: `2d7ed3f` → `2da37d2` → `534484c` → `2fa8d06` → `90ceca3`) |
| Engine | `BETTOR_DESK_V1` |
| Policy | `FERRARI_INSPIRED_DEV_V1` — **development policy, not a trained strategy** |
| Fill model | `PRINT_THROUGH_WITH_QUEUE_SHARE_V1`, queue_share **0.25 (ASSUMED)** |
| Learned artifact | frozen isotonic price→settlement curve, 7,364 train rows |
| `P_FILL` | **NOT_IDENTIFIED** — preserved, not resolved |

**Policy maturity, per rule.** Calling this "the Ferrari model" would be
false:

| rule | status |
|---|---|
| entry band | **HAND_WRITTEN** (Ferrari-inspired) |
| pair completion | **HAND_WRITTEN** (pair-clears arithmetic) |
| residual exit | **LEARNED** (the frozen curve) |
| fill probability | **ASSUMED** (queue_share) |

## 3. Live-data decisions

The live engine is running and has **not stopped**: `shadow_decisions`
25,995 rows, newest **1 second old** at the audit read.

**Every decision in the last 24 hours is `NO_TRADE`** — 3,629
(`BETTOR_EV_SHADOW_V5`) + 3,001 (`RN1_SHADOW_V1`). The refusal
distribution:

| blocker | n |
|---|---:|
| `INDEPENDENT_EV_NOT_ESTABLISHED` | 3,629 |
| `P_FILL_NOT_IDENTIFIED` | 3,629 |
| `NO_FAIR_VALUE` | 3,629 |
| `SYMBOL_NOT_RESOLVED` | 2,592 |
| `MARKET_STATE_UNREADABLE` | 2,165 |
| `SPREAD_TOO_WIDE` | 592 |
| `BOOK_UNREADABLE` | 409 |

**The production engine's live shadow orders and positions remain 0.**
It is not broken — it is refusing on production risk controls, and
those controls were **not weakened** to produce a busier screen.
`P_FILL_NOT_IDENTIFIED` still refuses, as required.

**The binding requirement was found and fixed:**
`shadow_store.record_execution()` and `record_position()` existed, were
correct, and had **zero callers in the entire tree**. `bettor_desk` is
what calls them.

### 3a. The DESK lane is now live and writing

Read against the production database at **`2026-09-23T14:41:49Z`**, six
minutes after the loop came up:

| table | rows | newest |
|---|---:|---|
| `bettor_desk_decisions` | 375 | 9 s |
| `bettor_desk_orders` | 35 | 6 s |
| `bettor_desk_positions` | 20 | 6 s |
| `bettor_desk_ledger` | 18 | 6 s |

Decisions: `NO_TRADE` 133 · `HOLD` 121 · `FILLED` 86 · `ENTER` 24 ·
`COMPLETE_PAIR` 11.
Orders: `RESTING` 14 · `PARTIALLY_FILLED` 16 · `FILLED` 5.
Cursor `221,451,008`, advancing.

**The ledger identity holds on every snapshot** (18 of 18):
cash $98,850.22 + inventory $1,149.78 − realized $0.00 =
**$100,000.00 exactly**.

### 3b. That $0.00 was a defect, and the identity could not see it

Realized P&L reading **exactly** $0.00 after 334 fills is arithmetically
impossible under the PMUS schedule, whose maker side is a rebate. The
cause: `bettor_desk_loop.run()` took `fee_fn=None` and `Desk` turned
that into a silent zero-fee lambda. **The live lane booked every fill
free for 37 minutes.**

The worse half: **`invariant_ok` read `t` on all 109 snapshots, and it
was telling the truth.** The identity `cash + inventory_cost − realized
== starting_cash` holds equally well when no cost was ever charged. It
is a *consistency* check, not a *completeness* one, and it is
structurally incapable of noticing an accounting input that was never
supplied. `fees_usd = 0` is likewise ambiguous on its own — it is
equally what a fee-free book and a genuinely costless window look like.

**Fixed and verified in production at `15:24:19Z`:** realized P&L is
now non-zero and accruing — $10.06 → $10.18 → $10.21 → $10.48 →
**$11.48** — which is the maker rebate income that had been invisible.
Identity still exact: $98,228.80 + $1,782.68 = $100,011.48.

Three durable changes, not just the missing argument:
- the schedule is now the **default** on `run()`, not something a call
  site must remember, and it is the **same** schedule the replay books
  against so the two remain comparable on cost;
- `fee_basis` (`NO_FEE_SCHEDULE_GROSS` / `FEE_SCHEDULE_APPLIED`) travels
  on every snapshot and into every ledger row, so a gross book can
  never again render identically to a net one;
- the invariant now publishes its own `does_not_prove` list — that
  costs were charged, that the valuations are right, that the fill
  assumptions are right.

Five tests with controls, including one that runs a gross and a net
book side by side and asserts **both reconcile** — pinning the blind
spot itself rather than commenting on it.

That closes the chain end to end — live market data → independent
selection → risk check → resting shadow order → simulated execution →
inventory → reconciled ledger — persistent. **See §3c: it was NOT
restart-safe, and I reported that it was.**

**It is six minutes old, has realized $0.00 and has settled nothing.**
It is not evidence of profitability and is not offered as any. Its
value today is that the prospective decisions are being recorded
**before** their outcomes, which is what will make them usable as the
final evaluation set later (§8).

### 3c. CORRECTION — "restart-safety demonstrated" was WRONG

I reported that yesterday. It was not demonstrated, and the four things
I cited could not have demonstrated it.

**The book was never restored.** `run()` constructed a fresh `Desk` and
read back `cursor_event_id` and nothing else. Cash, legs and orders
were never recovered, so **every process start began the account again
at $100,000 with no positions**, and the first write then overwrote
`bettor_desk_state.cash_usd` with that fresh figure. An AST walk over
the module confirms it reads exactly one table.

Why the evidence I gave was empty:

| what I cited | what it actually shows |
|---|---|
| one `desk_state` row | what an UPSERT on a primary key gives for **any** number of writers |
| monotone cursor | what `max()` over a serial gives regardless |
| unchanged earliest timestamps | only that history was not re-read |
| `invariant_ok` | a desk against its **own** starting cash — an emptied book reconciles perfectly |

**The measured data already said so and I did not look:** inventory fell
from $3,652.96 to $1,782.68 across the 15:17 restart with **no `EXIT`
decision** to explain it.

**Consequence for the accounting.** Figures spanning a restart are not
running totals. And `boot_id` was written as a per-row timestamp, so
restarts are **not locatable** in the historical ledger at all — that is
reported as an incompleteness rather than guessed at.

**Fixed.** `_restore` rebuilds cash and legs before the first event is
stepped and reports whether the rebuilt book reconciles. Open orders are
closed with the reason recorded, not re-armed, because the consumption
ledger is not persisted and re-arming could fill them twice against
evidence already consumed. A real epoch id is minted once per process
and stamped on every row.

### 3d. The corrected ledger, and why it is an interval

`bettor_desk_fills` was created by migration 094 and **never written**,
so no per-fill quantity, price, role or timestamp exists for anything
the live lane has simulated. PMUS rounds **per fill**, so a fee
recomputed from an order's average price is not the sum of its fills'
fees; and `p(1-p)` is concave, so the aggregate **overstates** the
rebate's magnitude. Both push the same way.

So the correction is published as an **interval**:

| bound | meaning |
|---|---|
| **lower — $0.00** | every fill's amount rounded away. Not a formality: a fill needs ~2 contracts at p=0.50 and ~41 at p=0.01 before its rebate survives rounding |
| **upper** | the concavity bound from the order's average price |

Reporting the upper bound as *the* answer would be a number that
flatters the book — the same class of error as the entry-fee leak this
system already caught once.

**Originals are untouched.** Corrections are written beside them, with
two duplicate guards: `UNIQUE (desk_id, version)` on the run and
`correction_id = version:kind:subject` on each row. Ledger rows written
before the fee basis existed read as `FEE_BASIS_NOT_RECORDED` — they do
**not** acquire a net-of-fees label because the current process has a
schedule.

Fills are now persisted per fill, so the next correction can be exact.

**This lane is separate from the production engine above.** Different
tables, different decision stream, label `DESK_SHADOW_EXPERIMENTAL`.
The production refusals are untouched.

## 4. Reconciled shadow P&L — HISTORICAL REPLAY

### Three corrections to what I said in the first handoff

**1. The replay tape is NOT the market tape.** I called its events
"recorded prints". `trades` is keyed on `whale_id`; every row is **one
tracked account's own execution**. Ferrari filling at a price is
evidence that *Ferrari's* order filled — not that ours would have.
Labelled `SINGLE_ACCOUNT_EXECUTIONS` throughout.

**2. Wrong venue.** The fills come from the on-chain listener
(`polygon_ws_url`, `PM_EXCHANGE_V3_ADDRESSES`,
`ts_provenance = polygon_block_timestamp`) — **Polymarket global**. The
fees applied are the published **PMUS** schedule. That is a
`TRANSFERRED_SCENARIO`, not same-venue execution evidence.

**3. The learned exit had lookahead, and it is measured.** 2,404 of
7,364 training rows (**32.6%**) come from markets that traded *inside*
the replay window, and **2,402** training markets resolved *after* the
replay's first event. Verdict: **`DEVELOPMENT_EVIDENCE`**. Its own
pre-registered test was **NOT SUPPORTED** (paired log-loss difference
+0.0052, 95% CI [−0.0017, +0.0120]).

**Withdrawn:** *"the measured edge is in the tails."* Gross of costs,
measured on prices Ferrari *chose* to buy, and formed after I had
already inspected that data. **Now a research hypothesis, not a
finding.**

### The full economic position

Window 2026-08-21T14:28:09Z → 2026-09-23T13:07:08Z, 19,890 single-account
executions, 1,006 conditions, $100,000 start.

| | |
|---|---:|
| Realized P&L | −$1,898.29 |
| Cash | $73,571.39 |
| **Unvalued exposure** | **$24,530.37 across 396 legs** |
| Inventory mark | **NOT_IDENTIFIED** |
| Executable liquidation | **NOT_IDENTIFIED** |
| **Total portfolio performance** | **NOT_IDENTIFIED** |

**The realised figure is one component, not the whole.** No mark and no
depth exist for the 396 unresolved legs, so neither an unrealised
figure nor a liquidation estimate can be produced. The ledger identity
reconciles (drift −0.0) — **that proves accounting consistency only,
and validates neither the valuations nor the fill assumptions.**

Orders: 687 filled · 529 partial · **782 expired never filled** ·
34.4% fill rate. Consumption: 14,752 events offered 2.33M, released
139k.

### Why it lost

| cause | USD |
|---|---:|
| Settlement (57 won / 65 lost) | −$1,358.88 |
| Exit vs basis | −$921.16 |
| Fees | **+$381.75** (maker rebate income) |

**What is established:** the entry band [0.40, 0.65] sits where the
fair-value study found mean(payout − price) indistinguishable from
zero, so entries there carry no demonstrated edge and spread is paid on
the way out. **The band is not being retuned against this window.**

## 5. Inspectable lifecycle

Desk tab → *Inspectable lifecycles*. Sixteen conditions: the **six
worst** and six best settled by realised P&L, plus the four most
active. Range −$244.47 to +$337.25. **Losers are in by construction**
and the selection rule is printed above the table.

## 6. Demonstrated / unvalidated / next gate

**Demonstrated**
- Live market data → our decision → risk check → recorded refusal, continuously, unattended
- Order state machine: proposed / resting / partial / filled / expired, with full transition history
- Execution model that refuses a touch and requires a print, with a consumption ledger
- Per-leg inventory, cost basis, fees, realised P&L, settlement, and a ledger identity that reconciles to −0.0
- Loss attribution by cause; stranded capital shown, not dropped
- Mode separation; no combined live+replay total exists anywhere in the code

**Unvalidated**
- **Our execution.** The replay tape is Ferrari's. We were never in the queue. Every fill is an assumption under queue_share.
- **Profitability.** The only measured result is a loss.
- **Capacity / institutional scale.** `NOT_IDENTIFIED`, and no multiple of a small-order result is offered.
- **Any candidate policy.** Two cycles, ten variant-evaluations, zero acceptances (§8).

**Next capital gate:** a bounded funded pilot is the only way to
measure queue position at placement, book depth at our price, and
whether a cancellation was ours or the venue's — the three
measurements that would turn `P_FILL` from `NOT_IDENTIFIED` into a
number. **Not requested in this directive and not requested here.**

## 7. The live lane — deployed, guarded, and what it is

**`BETTOR_DESK_LOOP=1` is set on `sportsassets-api` and release
`534484c` carries the loop.** No new service, no new cost, no worker
restart, no additional venue allowance.

How it satisfies the isolation requirements:

| requirement | how |
|---|---|
| single active writer | `pg_try_advisory_lock(7723901544120031)` — the instance that wins runs; every other reports `STANDBY` and writes nothing, so the overlapping instances a deploy creates cannot run duplicate desks |
| idempotent intake | cursor is the evidence's **serial id**, not a timestamp; every insert is `ON CONFLICT DO NOTHING`/`DO UPDATE` on a natural key; the consumption ledger is keyed on the event id |
| non-blocking | awaited on the shared pool, sleeps between cycles |
| restart-safe | **CORRECTED — this row was false.** Only the CURSOR was restored; cash, legs and orders were not, so each start began the book again at $100,000. `_restore` now rebuilds cash and legs before the first event is stepped and reports whether the rebuilt book reconciles. Open orders are closed, not re-armed, because the consumption ledger is not persisted and re-arming could fill them twice. |
| a failed cycle | does **not** advance the cursor — the batch is retried and the idempotent keys absorb it |
| no funded path | **absent from the import graph**, not flagged off; a test reads the module's source and fails the build if a venue import or order-submitting symbol appears |
| no venue requests | its only input is evidence already in our database — the collector's allowance is untouched |
| first start | seeds the cursor at `max(trades.id)`, so five months of history is **not** replayed wearing the live label; a restart resumes exactly |

**It does not weaken production.** The production engine's refusals are
unchanged; this is a separate labelled lane (`DESK_SHADOW_EXPERIMENTAL`)
with its own tables, execution model and decision stream.

Collector preserved across every deploy today: worker deploy list
byte-identical each time, `claude/session-njaewf` still `7f76fd9`,
journal shows no new gap or boot, allowance 0/8 · 2/20 · 4/40, fixed
stop unmoved.

## 8. The policy-improvement loop — two cycles, nothing promoted

COMMAND → **Learning loop** tab. Full detail in
`research/beta48/learning/IMPROVEMENT_LOOP.md`.

The loop proposes bounded changes to entry, completion, waiting, sizing
and residual-inventory decisions, each tied to a **measured line of the
loss attribution**; replays baseline and candidates over the *same*
opportunity set, capital and declared execution scenarios; and promotes
only through a gate declared **before the cycle runs**.

| | cycle 1 | cycle 2 |
|---|---|---|
| gate | V1 | V2 (declared in `2e23798`, before the cycle) |
| variants tried | 5 | 5 |
| **promoted** | **none** | **none** |

### What each cycle established

**Cycle 1** rejected all five. More importantly it found that **the
gate's own selection statistic could not do its job**: V1 ranked on the
zero-marked lower terminal bound, which spans ~$34,000 on $100,000 of
cash while the figures it must separate are ±$1,000. Worse, that
statistic is a *degenerate objective* — its optimum is to hold no
inventory, i.e. trade nothing — and both of its accepts were **dose
reductions** (narrower band, smaller clip). Less of a losing strategy
loses less. That is arithmetic, not an edge.

**Cycle 2** used the repaired gate and rejected all five again, with a
sharper reason: **`NOT_RESOLVED`.** The candidates' effects ($381,
$237, $191, $3) are smaller than the change in measurement uncertainty
they cause ($6,071, $8,739, $968, $41). We cannot see effects this
size with this data.

### The finding that governs what comes next

`d(realized)/d(queue_share)` — the response to the one execution
assumption that is `NOT_IDENTIFIED` — is **negative on TRAIN and
positive on VALIDATION for every policy tested, including the frozen
active one.**

| baseline, realized P&L | qs 0.10 | qs 0.25 | qs 0.50 |
|---|---:|---:|---:|
| TRAIN | −$544 | −$969 | −$1,769 |
| VALIDATION | +$41 | +$377 | +$974 |

That instability is a property of **the corpus**, not of the
challengers, so it is not fixable by another entry/exit/sizing knob.
**The "+$974" must not be quoted as a profitable result** — it is the
positive half of a sign flip.

### Therefore the next work targets uncertainty, not P&L

1. **Resolve more inventory.** 24,542 unresolved shares carry the
   entire bound width. Extending settlement coverage narrows the
   instrument directly and adds no trading risk.
2. **Accrue the final evaluation set.** The live desk (§3a) is now
   writing prospective decisions forward of the freeze instant. That is
   the only genuinely uninspected evidence that will exist — every row
   of the replay corpus has already been inspected, so no "hold-out"
   carved from it would be one.
3. **A contemporaneous book** is what would remove the fill-assumption
   dependence. Until then no variant's result can be stable. That needs
   a funded pilot and **is not requested here.**

### Guarantees on the loop itself

- **The learner cannot raise its own risk limits.** Candidate
  parameters are checked against the policy's declared knobs; a
  mistyped knob is a hard refusal, not a silently unchanged policy
  reported as "no difference".
- **A candidate cannot win on an optimistic fill.** It must beat the
  baseline under **every** declared `queue_share` scenario.
- **Inactivity is reported, never rewarded** — an activity floor and a
  turnover floor, so "traded nothing" and "traded less" cannot pass.
- **Promotion is a reviewed commit, not an API call.** The read routes
  are GET-only and there is no path from the display to what the desk
  runs.
- **A finished cycle is never rescored** under a gate written after
  seeing its results.
- **Funded trading remains disabled** — no venue client in the import
  graph, enforced by a test.
- **The collector is untouched.**
