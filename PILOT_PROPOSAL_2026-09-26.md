# Funded pilot — proposal for review

**Status: nothing here is authorized, nothing is enabled, no order has been
sent.** Funded submission is off in code at four independent points, listed
below. This document exists so the account and limit decision is reviewable
with real numbers instead of being described.

---

## 1 · Eligible account choices

**There are none today, and a new registry row would not create one.**

Production's registry holds exactly one account:

```
registry accounts        1
eligible by their rows   (none)
paused on their rows     acct_fc2d773a2afa4851
holds no balances        true
paused account by id  ok false  refusal ACCOUNT_IS_PAUSED
a name with no id     ok false  refusal ACCOUNT_ID_NOT_SUPPLIED
```

I previously offered "register a new account id as ACTIVE with CLEAN
accounting" as one of two ways forward. **That was wrong** and it is now
impossible in code. A row this system inserts is a row this system wrote;
writing `accounting_status = 'CLEAN'` beside an id nobody reconciled is the
system certifying itself. `bettor_account_onboarding.register` therefore
creates an account as:

| field | value |
|---|---|
| `status` | `PENDING_VERIFICATION` |
| `accounting_status` | `UNVERIFIED` |
| `paused` | `true` |

and `UNVERIFIED` is not in `ACCOUNTING_OK` (`CLEAN`, `RECONCILED`,
`VERIFIED`), so activation refuses it on its own row.

### The precise onboarding requirement

Eligibility is earned against the **venue**, in four reconciliations. Each has
three outcomes, and *unreadable blocks exactly as hard as a discrepancy* —
"we could not look" must never render as "we looked and it was empty":

| # | check | read from | today on PMUS |
|---|---|---|---|
| 1 | **balances** | a balance call | **NOT_SUPPORTED** — `pmus` has no balance read |
| 2 | **positions** | `portfolio.positions`, paged to `eof` | available |
| 3 | **open orders** | `orders.list` | available |
| 4 | **executions** | `portfolio.activities`, over a stated 90-day window | available |

A venue position with no row in this book is reported as a `DISCREPANCY`, not
adopted — otherwise its cost basis is a guess from that moment on.

**So the blocking onboarding requirement is one piece of engineering, mine:**
a balance read on the `pmus` adapter. Until it exists, no account on the funded
venue can pass check 1, and `mark_eligible` writes nothing.

`acct_fc2d773a2afa4851` stays paused. `resolve_existing` runs the identical
four reconciliations against it and unpauses it only if they pass; there is no
argument, flag or endpoint that unpauses a row because someone decided to.

---

## 2 · Current enforced limits, in dollars

The frozen rails in force right now — digest `a19eb60a2817`, unmoved:

| rail | enforced now |
|---|---|
| `MAX_CAPITAL_DEPLOYED` | $3,000 |
| `MAX_MARKET_EXPOSURE` (per order) | $1,000 |
| `MAX_EVENT_EXPOSURE` | $1,000 |
| `MAX_CORRELATED_EXPOSURE` | $1,000 |
| `MAX_DRAWDOWN` | $1,000 |
| `MAX_RESIDUAL_INVENTORY` | 2,000 contracts |
| `MAX_CAPITAL_HOURS` | 72,000 USD·hours |

An approval may only **tighten**: `effective = MIN(frozen, approved)`,
element-wise, and a looser number is ignored and recorded as ignored.

### A hole in the last proposal, now closed

The four approved names did not reach `MAX_EVENT_EXPOSURE`. A $250 approval
produced a $25 per-order cap sitting beside a **$1,000** event cap — forty
times the whole pilot. `event_exposure_usd` now maps to that rail, and adding
the name moves **no existing digest**: the old four numbers still produce
`8e667b126912`, which is what every recorded authorization was granted against.

**And tightening it is not enforcing it.** `OPEN_BOOK_SQL` selects no event
key, so that rail cannot see other positions on the same event. The number
gets smaller; the measurement stays incomplete. The mitigation below is
structural rather than numeric.

---

## 3 · Proposed tighter caps

| approved name | proposed | rail it tightens | effective |
|---|---|---|---|
| `capital_usd` | **$150** | `MAX_CAPITAL_DEPLOYED` | $150 |
| `per_order_usd` | **$10** | `MAX_MARKET_EXPOSURE` | $10 |
| `event_exposure_usd` | **$10** | `MAX_EVENT_EXPOSURE` | $10 |
| `max_exposure_usd` | **$30** | `MAX_CORRELATED_EXPOSURE` | $30 |
| `daily_loss_stop_usd` | **$25** | `MAX_DRAWDOWN` | $25 |

**Why these numbers, and not the $250/$25/$100/$50 set from before.** The
earlier set was the shadow lane's own sizing scaled down. These are chosen
against what the pilot is for: establishing that a funded order behaves the way
the shadow lane says it does. At $10 a position, one adverse settlement costs
$10 and the daily stop is reached only after roughly two and a half total
losses in a day — which is a real stop rather than a formality, and small
enough that the *measurement* is the return on it, not the money.

**The structural mitigation for the blind event rail:** at most **one open
position at a time** for the pilot. With one position open, event exposure and
market exposure are the same quantity, so the rail that cannot see siblings has
no siblings to miss. This is a pilot rule, not a code guarantee — I am naming
it as such rather than claiming the rail is fixed.

**Loss stop:** `$25` on `MAX_DRAWDOWN`. Reaching it should end the pilot and
require a fresh authorization, not auto-resume.

---

## 4 · The remaining activation step

**Everything below the account and the limits is already built, gated and
tested. The step that remains after your decision is a code change, not a
record.**

The path, and where it stops:

```
workers/ext_pinnacle_loop            the schedule            NOT WIRED to the below
  -> bettor_external_shadow          a qualifying decision   works (0 of 66 admitted)
  -> bettor_funded_execution         plan, account, limits,  BUILT + TESTED
     authorization                                           against the real adapter
  -> FUNDED_SUBMISSION_ENABLED       False                   ← switch 1
  -> pmus.submit_fok                 the EXISTING adapter
       -> execution_gate.authorize                           ← switch 3
       -> orders.preview / create
  -> bettor_entry_execution.REAL_ORDER_SUBMISSION_ENABLED    ← switch 2 (False)
  -> PMUS_KEY_ID / PMUS_SECRET_KEY   absent                  ← switch 4
```

### What remains disabled, precisely — four things, none a substitute for another

| # | what | where | value | cleared by |
|---|---|---|---|---|
| 1 | `FUNDED_SUBMISSION_ENABLED` | `bettor_funded_execution` | `False` | a code change |
| 2 | `REAL_ORDER_SUBMISSION_ENABLED` | `bettor_entry_execution` | `False` | a code change |
| 3 | `execution_gate` | inside `pmus.submit_fok` | bound per process; denial **raises** | a bound, unpaused gate at submission time |
| 4 | `PMUS_KEY_ID` / `PMUS_SECRET_KEY` | the service environment | absent | provisioning a venue credential |

### The order of operations I propose

1. You choose the account route (below) and the five limit numbers.
2. I add the `pmus` balance read, then run the four-way reconciliation against
   the real venue and report it — with whatever it finds, including any
   position or order this book does not know about.
3. Only if that reconciliation is clean does the account become eligible.
4. I wire the scheduled lane to call the connection, still behind switch 1,
   and gate it.
5. You authorize activation explicitly, naming the account and the limit set.
6. Switches 1 and 2 flip in one reviewed commit; the credential is provisioned
   through the secret-management route; the first cycle runs with the one-position
   rule and I report the first order in full.

**Nothing in steps 1–4 sends an order.**

---

## 5 · What your decision does *not* clear

Stating this plainly because I implied otherwise last time. Of the eight
prerequisites production reports unmet, **five are not yours to decide**:

| owner | prerequisites |
|---|---|
| **Owner decision** | `account_selected_and_clean`, `limits_recorded_and_complete`, `limits_approved_by_the_owner`, `approved_limits_tighten_the_enforced_rails`, the venue credential |
| **Market / source evidence** | `venue_book_freshness_basis`, `settlement_compatibility`, `an_autonomous_entry_was_admitted_unwaived`, `an_eligible_market_with_one_coherent_chain`, source calibration |
| **Engineering (mine)** | the balance read, wiring the schedule, the event-rail blindness, two clock-sensitive test defects |

Approving limits does not make a quote fresher, does not reconcile two
bookmakers' published abandonment rules, does not calibrate the Pinnacle
de-vig against outcomes, and does not qualify a candidate. The last live cycle
refused 25 candidates on a measured stale quote and 18 on a stated payout
conflict; 0 of 66 were admitted. That is the engine working, and it is also why
a funded pilot may sit idle for days after activation.

---

## 6 · The decision I am asking for

1. **Account** — one of:
   - *(a)* name a new account id for me to onboard through the four
     reconciliations (requires the balance read first); or
   - *(b)* authorize me to run the reconciliation against
     `acct_fc2d773a2afa4851` and report what the venue says, with the
     understanding that it stays paused unless all four pass.
2. **The five limit numbers** — the proposal above, or your own.
3. **Explicit activation authorization**, naming the account and the limit
   set, once steps 2–4 of the order of operations are done and reviewable.

I am not asking for 3 now. 1 and 2 are what unblock the next piece of
engineering.
