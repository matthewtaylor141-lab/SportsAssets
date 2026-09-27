# THE LAUNCH DECISION — 2026-09-27

**Built against:** `797dd06`, branch `claude/command-center`.
**Funded submission:** DISABLED in code. **No real-money order is authorized.**
**`acct_fc2d773a2afa4851`:** PAUSED, and stays paused.

---

## 0 · Four states, kept apart

The single most misleading thing I could do here is let one of these stand in for
another, so they are answered separately and never summed.

| | state | verdict |
|---|---|---|
| **1** | **software completion** — the code exists and its own tests pass | **substantially complete** for the non-submitting scope, with named gaps |
| **2** | **readiness for a funded pilot** | **NOT READY.** Three independent blockers, none of which is the timing assumption |
| **3** | **verified funded operation** — a real order placed, managed and accounted | **NOT STARTED.** Zero verified real orders from this lane, ever |
| **4** | **profitability validation** | **NOT STARTED,** and not reachable until 3 has run long enough to measure |

**An owner signature moves none of these.** It can authorize state 2 → 3 once 2
is actually ready. It cannot make 2 ready, and it cannot guarantee an order, a
profit, or predictable revenue.

---

## 1 · The verified release and operating scope

### What this release does

| capability | state |
|---|---|
| evaluate candidates on supported markets against the external valuation | **runs** — 464 candidates in the last 24-hour window, every one persisted with its refusals |
| record and display every decision, refusal, rail measurement and fee basis | **runs** |
| refuse, by name, at the first failing stage | **runs** — nine stages, each with its own named refusals |
| read account-wide exposure across five paths and refuse when it is unknown, unreadable, stale or for another account | **implemented**, untested against a real account |
| the corrected fee arithmetic at the published θ, banker's rounding and cumulative taker cap | **implemented**; two production callers traced |
| **place any order** | **DISABLED IN CODE.** `REAL_ORDER_SUBMISSION_ENABLED = False`, `POLICY_ADMISSION_ENABLED = False`. Each needs a code change through the gate |

### Enforced server-side, not by convention

* both disabling constants are module-level and read at every decision point;
* the desk panel's controls **only ever remove authority** — pause, halt, cancel,
  revoke. Resuming a funded lane is not available from the panel at all;
* `external_valuations` carries a table CHECK that `order_submitted = FALSE`, so
  the shadow lane cannot record a submission even if the code tried;
* the API-only deploy route reaches `sportsassets-api` alone and **cannot reach a
  worker**, which is how the protected worker (`f5d1c05`) stays untouched.

### The gate

| run | SHA | identities | new | fixed | valid |
|---|---|---|---|---|---|
| baseline | `6d75275` | 177 | — | — | yes |
| release | `02d728c` | 180 | 3 | 0 | yes |
| release | `92a9ead` | 155 | 3 | 25 | yes |

The 25 closed are the four `Crypto`-dependent files, fixed by installing
`pycryptodome>=3.20` — **already declared** in `pyproject.toml` and merely absent
from the build container. The 3 "new" are **order-dependent**: all three pass when
their files run alone, and the baseline SHA collects 29 fewer test files, so a
different subset of the suite's order-dependent tests surfaces. That is a real
debt and is recorded as one.

### And the 177 standing failures, classified

I claimed none of them affects the capital path, and supported it only by noting
they are also in the baseline. **That supports a different claim** — that the
release did not cause them. The structural support is in
`research/evidence/STANDING_FAILURES_CLASSIFIED_2026-09-27.md`:

* **107 of 177 sit in the first ring** — the capital entry points plus what they
  import directly. **The original claim is withdrawn**, and each of those needs
  individual reading before funded submission;
* the funded lane names exactly **two symbols** outside itself —
  `live_executor.fill_cash` and `bettor_market_stream._parse_ts` — and both are
  covered by tests that pass;
* the release caused none of them. That part stands.

---

## 2 · Eligible account: NONE

**Account selection is not account eligibility.** Naming
`acct_fc2d773a2afa4851` for assessment does not clear its paused or unreconciled
state, and confirming its id changes nothing about it.

| | |
|---|---|
| status | **PAUSED, accounting unresolved** |
| its only route out | `bettor_account_onboarding.resolve_existing`, which needs four complete venue reads — balances, positions, open orders, executions — and refuses `R_NO_EVIDENCE` / `R_EVIDENCE_STALE` / `R_EVIDENCE_OTHER_ACCOUNT` |
| balances, positions, open orders, executions | **all unread.** No venue account credential exists in this deployment, so there is no number to show. Reporting them as zero would be manufacturing evidence |
| evidence age | **no reconciliation evidence has ever been recorded** |
| discrepancies | **not computable** without the reads above |

### Every other writer that can reach the account

`capital_path.account_writers()` counts them by write statement, because the lanes
that share these tables have no import edge between them:

| table | real exposure? | writer modules |
|---|---|---|
| `live_orders` | **yes** | **6** — `analytics/engine.py`, `api/app.py`, `live_executor.py`, `workers/copy_sweep.py`, `workers/mirror_live.py`, `workers/underdog.py` |
| `bettor_funded_intents` | yes | 2 — `bettor_funded_book.py`, `bettor_funded_management.py` |
| `bettor_funded_fills` | yes | 1 |
| `rn1x_orders`, `bettor_desk_positions`, `shadow_positions` | modelled only | 6 |

> **This lane's uniqueness constraint controls none of the six `live_orders`
> writers**, and counting writers is not controlling them. Either every writer
> passes one enforcement boundary, or the account is isolated to one writer and
> that isolation is verified. Neither is done.

**And this is a lower bound.** A human at a database prompt, or an operator in the
venue's own app, is a writer that appears nowhere in this repository and can
commit the account between our read and our order.

---

## 3 · Proposed limits, and the exceptions that need approval

**Nothing below is approved by this document**, and none of it is approved by any
earlier instruction to deploy capital today.

| limit | proposed | scope |
|---|---|---|
| `MAX_CAPITAL_DEPLOYED` | $100 | cumulative, no reset, **account-wide** |
| `MAX_MARKET_EXPOSURE` | $25 | one `condition_id` |
| `MAX_EVENT_EXPOSURE` | $25 | one event slug |
| `MAX_DAILY_LOSS` | $25 | cumulative, **no reset** |
| single order | $25 | policy bound |
| concurrent positions | 1 | policy bound — **and one position in THIS lane does not isolate the account from the other five writers** |
| assumption expiry | 24 h | lapses, must be re-signed |

### The two exceptions requiring explicit approval

**E1 — the timing assumption** (`bettor_admission_policy`, DISABLED).
Bound to an authenticated principal, one account, one venue, a named instrument
set, the exact `POLICY_VERSION`, tightened bounds, an expiry and a revocation
state. It cannot waive any of ten other requirements, enforced rather than stated.
**What it asks the owner to accept is a book of UNKNOWN AGE** — the five-second
bound limits only the delay *we* add after receipt, and I previously described it
wrongly as reducing exposure to being wrong.

**E2 — enabling funded submission** (`REAL_ORDER_SUBMISSION_ENABLED`). A code
change through the gate, on a concrete account with concrete limits.

---

## 4 · Current candidate evidence, and every remaining blocker

### Would E1 unlock anything? **No.**

Read-only assessment, on the 24-hour census of 464 candidates, 0 admissible
(`candidate_assessment`, and the endpoint
`/api/command/rn1x/exception-assessment` computes it from production rows):

| refusal | candidates | would E1 waive it? |
|---|---|---|
| `VOID_ABANDONMENT_RULE_NOT_ESTABLISHED` | **464 / 464** | **NO** — settlement |
| `NO_ACTION_HAS_POSITIVE_NET_EDGE` | 445 | NO — economics |
| `EXECUTION_ESTIMATE_NOT_IDENTIFIED` | 442 | NO |
| `OVERTIME_RULE_NOT_ESTABLISHED` | 233 | NO — settlement |
| `RISK_GATE_BLOCKED` | 231 | NO |
| `SIZING_POLICY_NOT_APPLICABLE` | 209 | NO |
| `NO_OBSERVED_DEPTH_INSIDE_THE_BREAK_EVEN_LIMIT` | 138 | NO |
| `INDEPENDENT_FAIR_VALUE_NOT_ESTABLISHED` | 131 | NO |
| `NO_QUALIFIED_MODEL` | 131 | NO |
| `QUOTE_STALE` | 131 | **NO** — the odds provider's own 30 s rule, a different clock on a different source |

> **A settlement refusal E1 cannot waive is present on 464 of 464 candidates, so
> accepting E1 would produce zero admissions.** The inference needs no
> intersection of refusal sets, because one of the sets is everything.

And three requirements the shadow lane never even reaches are independently
blocking: **source calibration** (`external_source_calibration` has zero rows),
**account readiness** (§2), **submission authorization** (none recorded).

### Every requirement, separately

| requirement | state |
|---|---|
| settlement compatibility | **FAILS on 100% of candidates** |
| qualified probability | fails on 131 |
| source calibration | **NOT MEASURED — zero rows** |
| contract identity | **passes** — all 464 carried a venue slug and a condition id |
| executable net edge | fails on 445 |
| account readiness | **NOT ELIGIBLE** (§2) |
| risk rails | fail on 231 |
| market-data currency | **NOT_ESTABLISHED** — and this is the *only* one E1 speaks to |

**A policy exception cannot supply missing valuation evidence.** The timing
question is not the last step to capital; it is one of at least four, and not the
binding one.

---

## 5 · The bounded funded qualification procedure

Not a launch. A procedure with acceptance criteria, to be run **only** after the
blockers in §4 are closed and only on an approved account with approved limits.

| step | acceptance criterion |
|---|---|
| **Q1** provision a credential with the narrowest scope the venue actually offers | the `credential-capability` job's output shows what scopes exist. **Never probe order authority by sending an order** |
| **Q2** four complete venue reads, reconciled | `resolve_existing` returns eligible with evidence under its age bound, and discrepancies are zero or explained |
| **Q3** account-wide exposure becomes a number | `account_exposure` returns a measured total with a fresh `measured_at_epoch_s`, not `TOTAL_UNREADABLE` |
| **Q4** cross-writer control | either all six `live_orders` writers pass one enforcement boundary, or the account is provably reachable by one writer only |
| **Q5** a candidate clears every requirement in §4 **with the exception still disabled** | if none does, the procedure stops here, and that is the correct outcome |
| **Q6** one order at the approved size, managed and accounted end to end | fills reconcile to the venue's own report; fees match the published schedule to the cent; the position is cancellable and exitable |
| **Q7** fills compared against expected prices | this is the **only** way the unbounded-upstream-age risk is detectable at all, which is why it is a release condition and not a nice-to-have |

---

## 6 · The decision

> **GO** on the non-submitting release: gate the exact SHA, deploy
> `sportsassets-api` by commit id through `render-ops` → `deploy-api-commit`, read
> the serving build back, leave the protected worker untouched.
>
> **NO-GO** on funded submission today, and the blocking fact is not a missing
> signature:
>
> 1. **settlement scope fails on 100% of candidates**;
> 2. **source calibration has zero rows**;
> 3. **the account is paused with no reconciliation evidence and five other
>    writers can reach it**.
>
> Each is engineering or evidence work. None is unblocked by approving E1, and
> none is unblocked by approving a dollar limit.

**What I am asking for:** nothing today. The three blockers above are mine to
work, and asking for a signature before they are closed would be asking you to
authorize something that cannot happen.

**What I am not claiming:** that closing them produces a profitable lane. States
3 and 4 are separate for that reason — a verified funded operation is evidence
that the machinery works, not that the strategy earns.
