# EXECUTABLE LAUNCH PLAN — 2026-09-27

## 0 · THE BLOCKING FACT, FIRST

**A funded launch today cannot happen on the evidence path, and the reason is not
in our code.**

I ran the most favourable observation the venue can produce: an origin read with
`Age: 0`, an in-bound `304`, a live subscription with a heartbeat one second old
and a full-book message one second old, and a venue timestamp equal to now.

```
ESTABLISHING_MECHANISMS : ('M1_LIVE_MARKET_DATA_SUBSCRIPTION',)
M1 status               : M1_NOT_AVAILABLE_ON_THIS_FEED
M1 missing              : ('P5_DOCUMENTED_TIMING',)

BEST POSSIBLE observation today -> BOOK_CURRENCY_NOT_ESTABLISHED
admits?                         -> False
mechanism                       -> NO_MECHANISM_AVAILABLE
```

> **Zero candidates can be admitted.** Not because any market is stale, and not
> because our engineering is unfinished — **because the venue publishes no
> market-data timing guarantee**, and an admitting rule may use only what the
> venue documents and what we can observe.

So with a credential, a named account and approved limits all in place, the lane
would still submit nothing. **A credential does not unblock today. Nothing I can
build unblocks today.**

There are exactly two ways past it:

| | route | available today? |
|---|---|---|
| **A** | the venue documents its market-data timing | **No.** Zero matching sentences on the published WebSocket page. Not ours to make happen |
| **B** | **you accept an explicit policy assumption in its place** | **Yes — and it is your decision, not mine.** §2 |

**I will not take route B by myself, and I have not.** It is built, it is off,
and it needs your signature. That is the whole of §2.

---

## 1 · Exact remaining capital-critical defects

Ranked by whether they can misprice a trade, exceed authority, lose an execution,
misstate money, or strand inventory.

| # | defect | can it…? | state | what closes it | estimate | external dependency |
|---|---|---|---|---|---|---|
| **C1** | no mechanism establishes book currency | **misprice a trade** | **OPEN — BLOCKING** | the venue documents timing, **or** your signed assumption (§2) | not ours to estimate | **the venue, or you** |
| **C2** | account-wide exposure is `UNREADABLE` without the venue's position read | **exceed authority** | code done, evidence missing | the read-scoped credential in the environment | **10 min after the credential lands** | **you** (§4) |
| **C3** | account `acct_fc2d773a2afa4851` is paused, accounting unresolved | **misstate money** | reads built, refusals named | `resolve_existing` with four complete venue reads | **20 min after the credential** | **you** (§4, §5) |
| **C4** | no approved typed limits exist | **exceed authority** | schema + enforcement done | your explicit acceptance of §6 | **immediate on approval** | **you** |
| **C5** | account isolation `NOT_DEMONSTRATED` | **exceed authority** | five exhibits named | three come from the credential; two are yours to state | **30 min after the credential** | **you** |
| **C6** | fee schedule not `VERIFIED_APPLIED` | **misstate money** | implemented, consumers traced, not verified | one real multi-fill order reconciled against the venue's own charge | **needs an authorized pilot** | **you** |
| **C7** | nothing in this batch is deployed | all five | **gate PASSED, deploy outstanding** | API-only deploy → serving-build readback | **30 min, no dependency** | none |
| **C8** | `bettor_fee_schedule` effective date 2026-09-17 vs published 2026-09-25; Θ resolved via `LATEST`; no per-sport Θ | **misprice a trade** | **CLOSED** | date and Θ now derived from `calibration_fees` and pinned by test; `LATEST` and the per-sport omission labelled at the constant | done | none |

**C7 and C8 were the only two I could close without you.** C8 is closed. C7's
gate has passed and the deploy is the remaining step.

### The exact-SHA gate result — paired, same instrument, both runs VALID

```
baseline d9c3c41   179 identities   13012 passed
final    6cb3609   177 identities   13169 passed
NEW                none
FIXED              two
```

**Zero new failures.** Two fixed: `test_the_scheduler_supplies_no_mechanism_today_and_says_so` and `test_two_concurrent_submissions_cannot_both_reach_the_venue`.

**The 177 are pre-existing and shared with the baseline.** They are not this
batch's, and they are not nothing — but "no new failures" is the acceptance
criterion this gate is for, and it is met. The standing 177 are platform work
recorded in §12.

**Six regressions of mine were found and fixed before this ran**, one of them a
safety defect: the `book-protocol` job made `command-verify` name the venue host
without the global concurrency group, so two runs could have read the venue at
once. Read-only bounds the consequence; it does not make it correct.

**And the gate itself had three defects, all mine, all the same shape** — an
instrument that reports a healthy environment as an invalid one, or a false
positive that trains a reader to skim:

| | defect | consequence |
|---|---|---|
| 1 | `ON_ERROR_STOP=1` against migrations that ALTER an app-created table | **would have voided every run** |
| 2 | `grep -c \|\| echo 0` producing `"0\n0"` on a healthy run | **voided a run where PostgreSQL never went down** |
| 3 | log lines counted as test identities | reported a **new identity that was a shifted log line** |

All three fixed, plus a new check: **zero health samples now voids**, because "no
DOWN samples" is vacuously true when the sampler never started — an absent
witness is not a clean one.

---

## 2 · The one decision that could admit a trade today

`backend/sportsassets/bettor_admission_policy.py` — **built, and OFF.**

### What it is not

It does **not** move `MAX_BOOK_STATE_AGE_S`, add a token to
`ESTABLISHING_MECHANISMS`, or let a fast response stand in for a market-data age.
`bettor_venue_currency` is untouched: `evaluate` still returns
`BOOK_CURRENCY_NOT_ESTABLISHED` and `admits()` still returns `False`.

### What it is

A **separate, visible, signed override** recorded alongside the verdict:

```
verdict:  BOOK_CURRENCY_NOT_ESTABLISHED                    <- unchanged, always reported
basis:    OWNER_ACCEPTED_POLICY_ASSUMPTION_NOT_A_MEASUREMENT
accepted_by / accepted_at / expires_at
```

The basis token contains the words `NOT_A_MEASUREMENT` so it cannot be misread in
a log. **Nothing in this system will ever say a book's currency was measured when
it was assumed.**

### Two things must both be true

1. a signed acceptance record exists, **and**
2. `POLICY_ADMISSION_ENABLED` is `True` — **`False` in the shipped build.**

Turning it on is a code change through the gate. **A signed record alone starts
nothing.** Same shape as `REAL_ORDER_SUBMISSION_ENABLED`, for the same reason.

### What you would be accepting — verbatim

> *I accept that the venue publishes no market-data timing guarantee, that the
> meaning of its transactTime field is unresolved, and that no mechanism in this
> system can establish how current an order book is. I accept that a
> full-replacement order-book message received on a live, non-dropped
> subscription may be treated as sufficiently current to trade on, as MY POLICY
> DECISION and not as a measurement. I accept that a stale book is
> indistinguishable from a fresh one at decision time, so the risk is detected
> only afterwards by comparing fills against expected prices.*

`accept()` **requires this text echoed verbatim.** A signature against a summary
is refused — the value of the record is that the person who signed it read what
they were accepting.

### The concrete risk

We buy at a displayed price that has already moved. Bounded per order by its size
and across the pilot by the cap. **The failure is invisible by watching** — a
stale book looks exactly like a fresh one — so it is detected only by comparing
fills against expected prices, which is why that comparison is a release
condition in §7 and not a nice-to-have.

### What the policy does **not** excuse

A missing book (P1) · the wrong instrument (P4) · a book carried across a
disconnect (P3) · an unreadable exposure total · an unreconciled account · the
code constant on real order submission. **It covers timing only.**

---

## 3 · The named account

| | |
|---|---|
| **the only account that exists** | `acct_fc2d773a2afa4851` |
| **its state** | **paused**, accounting **unresolved**. It stays paused |
| **its only route out** | `bettor_account_onboarding.resolve_existing`, which requires four complete venue reads and refuses `R_NO_EVIDENCE` / `R_EVIDENCE_STALE` / `R_EVIDENCE_OTHER_ACCOUNT` |
| **what I need from you** | **confirm this account, or name a different one.** I am not proposing it as a default and I will not bind an account you have not named |

**One property to decide deliberately**, because it selects which risk route is
available: **is this account reachable by anything else?** Another API key, a
human on the web session, the legacy copier, the manual sleeve. If **no**,
isolation can be demonstrated. If **yes**, enforcement is the only route and the
venue read is load-bearing on every order.

---

## 4 · Secure credential-provisioning steps

**No secret is requested in this conversation and none is in this document.**

1. **At the venue**, on the account in §3, create an API key with **read**
   permissions only. **No trade, no transfer, no withdrawal.**
2. **In the environment's secret store** for the backend service — not the
   repository, not a message, not a ticket — set `PMUS_KEY_ID` and
   `PMUS_SECRET_KEY`.
3. **Redeploy** so the process reads them. No code change; the client already
   refuses to build without them.
4. **Reply only:** *"the credential is in place."* **Do not paste the value.** If
   a value is ever pasted anywhere, treat it as compromised, revoke it, reissue.
5. Verification runs through the authorized API-only route and reports no key
   material.

**Read scope is sufficient for everything in §1.** A trade-scoped key would make
"no order can be sent" rest on code alone instead of on code *and* the
credential's own scope.

---

## 5 · Supported contracts — the initial release scope

Everything excluded is **server-side unreachable**, not merely undisplayed.

| | scope | enforced where |
|---|---|---|
| **venue** | exactly one: `PMUS` | `venue_class` refuses by name; the binding records it; submission compares against it |
| **account** | exactly one, bound under `ACCOUNT_KEY` | `account_selection` + reconciliation evidence. A second account needs a new binding *and* a new reconciliation |
| **contracts** | **money-line (`h2h`) only, full-game only**, on the two sports the lane maps | `premap.resolve` returns a contract only on an exact key match with date agreement; period gated by scope tokens. Anything else: `NO_VENUE_NATIVE_CONTRACT_IN_PREMAP` |
| **settlement** | only where venue and bookmaker payout rules are `COMPATIBLE` | `_settlement_compatibility` — `UNKNOWN` **and** `INCOMPLETE` both refuse. This ends 419 of 1,018 valuations and **must not be waived** |
| **direction** | opening long only; exits reduce | action mapping; exits run through `bettor_funded_management` and cannot open |
| **combos** | **excluded** | `combo_fee()` refuses `R_COMBO_CURVE_NOT_IMPLEMENTED` rather than pricing with the wrong curve |
| **Table Tennis** | **excluded** | outside the mapped sports. Its Θ becomes 0.10 at `2026-10-01T03:59Z` and `bettor_fee_schedule` has no per-sport Θ (C8) |

---

## 6 · Proposed limits — for your explicit approval

**Unapproved until you accept them.** `effective_limits` takes
`MIN(frozen, approved)`, so **approving a larger number than the frozen rail
changes nothing**.

| rail | proposed | unit / window / scope |
|---|---|---|
| `MAX_CAPITAL_DEPLOYED` | **$100** | USD · cumulative over the open book, no reset · **every open position — now ACCOUNT-WIDE** |
| `MAX_MARKET_EXPOSURE` | **$25** | USD · open book · one `condition_id` |
| `MAX_EVENT_EXPOSURE` | **$25** | USD · open book · one venue event slug |
| `MAX_DAILY_LOSS` | **$25** | USD · cumulative, **no reset** — the name says daily and the semantics do not reset |
| `MAX_POSITION_HOURS` | **$200** | USD-hours · accrued |
| single order | **$25** | policy bound (§2) |
| concurrent positions | **1** | policy bound (§2) |
| pilot total | **$100** | policy bound (§2) |
| assumption expiry | **24 h** | §2 — lapses and must be re-signed |

**The §2 policy bounds tighten the freshness window from 30 s to 5 s.** Under an
*assumption* rather than a measurement, exposure to being wrong is the whole
risk, so the window is tightened, not kept.

### Emergency controls

| control | what it does | where |
|---|---|---|
| **pause** | stops new exposure; servicing continues | desk panel; reads the account registry row |
| **halt** | stops the lane | desk panel |
| **cancel working orders** | withdraws resting orders | desk panel |
| **revoke authorization** | `revoked_at` — refuses as revoked, not as expired | `bettor_funded_activation` |
| **revoke the §2 assumption** | every subsequent order refuses `R_REVOKED` | acceptance record |
| **let the assumption lapse** | automatic after 24 h | `expires_at` |
| **`POLICY_ADMISSION_ENABLED = False`** | code constant; needs a release | `bettor_admission_policy` |
| **`REAL_ORDER_SUBMISSION_ENABLED = False`** | code constant; needs a release | `bettor_entry_execution` |

The panel **grants no authority** — every action removes it. Resuming a funded
lane is not available from the panel at all.

---

## 7 · The five pre-activation proofs — current state

| | proof | state |
|---|---|---|
| **1** | defensible market inputs, contract identity, qualified valuation | **identity ✅** — `OPEN_BOOK_SQL` joins `us_premap`, refuses `R_EVENT_IDENTITY_UNRESOLVED`. **valuation ✅** qualified — the Pinnacle de-vigged probability is labelled as an external source, never an internal model. **market currency ❌ — C1, blocking** |
| **2** | reconciled account state, account-wide risk enforcement | **enforcement ✅ code** — five refusals in the real submission path, both directions tested, two counterexamples against a migrated DB. **evidence ❌ — C2/C3, needs the credential** |
| **3** | correct fees, execution accounting, visible provisional amounts | **✅ implemented** — banker's rounding, running cumulative cap, venue-ordered sequence, late-fill placement with successor restatement, `FEE_PROVISIONAL` distinct from `FEE_RECONCILED`, observed charge authoritative. **❌ not `VERIFIED_APPLIED` — C6, needs a real fill** |
| **4** | authorized submission, continuing management, cancellation, recovery | **✅ against a substituted transport** — and that is *not* funded operation. **❌ against the venue — needs C1 + C2 + approval** |
| **5** | secure controls, exact deployed build, working Command Centre | **controls ✅** — notification capability, provider-key proxy, 37-route matrix, `/api/pmus-account` guarded. **build ❌ — C7, in progress, no dependency** |

---

## 8 · The largest genuinely complete scope available today

**Without your signature on §2:**

> A **verified, deployed, non-submitting** autonomous lane: it selects
> candidates, resolves contract identity, values them against the external
> source, sizes them against account-wide exposure, prices fees under the
> published cumulative algorithm, and **refuses every one of them by name** with
> the reason recorded. Plus the Command Centre showing that, and the approval
> package ready.

**That is not funded operation and I will not call it that.**

**With your signature on §2, plus the credential and §3/§6:** one position at a
time, ≤$25, ≤$100 total, money-line full-game only, compatible settlement only,
every decision stamped `OWNER_ACCEPTED_POLICY_ASSUMPTION_NOT_A_MEASUREMENT`,
lapsing in 24 hours.

---

## 9 · Realistic completion estimate

| | |
|---|---|
| **C7 deploy + readback** | 30 min, **no dependency**. The gate has PASSED |
| **C8 fee schedule unification** | **CLOSED** |
| **C2 + C3 + C5** | **~60 min after the credential lands.** Not before |
| **C1** | **not ours to estimate.** The venue's documentation, or your §2 signature |
| **C6** | needs an authorized pilot with a real fill |

**The honest bottom line:** everything that is ours is done or lands within two
hours. **Whether capital deploys today is a decision, not a schedule** — and the
decision is yours, on §2, §3 and §6.

---

## 10 · The four verdicts

| | |
|---|---|
| **Engineering verified** | **NO** — C1 blocks, C7 not deployed |
| **Ready for an authorized funded pilot** | **NO** — account, credential, limits and the §2 decision all outstanding |
| **Actual funded execution and reconciliation verified** | **NO** — zero verified real orders from this lane. 166,585 historical `live_orders` rows exist from the earlier live beta; they are **not** this lane's record |
| **Strategy profitability validated** | **NO** — and no deployment, passing suite or modelled profit moves it. **I am not claiming predictable revenue** |

## 11 · What I need from you, in one place

1. **§2** — sign the assumption verbatim, or decline it. **Declining means no
   capital deploys until the venue documents its timing.**
2. **§3** — confirm `acct_fc2d773a2afa4851` (after reconciliation) or name
   another, and say whether anything else can reach it.
3. **§4** — provision the read-scoped credential and reply *"the credential is in
   place."*
4. **§6** — approve, tighten, or reject the limits.

**Any one of these missing means no funded order.** I am telling you now, not at
the end.

---

## 12 · The standing 177, and what they are not

The gate's acceptance criterion is **no new failures**, and that is met: zero new,
two fixed. But 177 identities fail on both the baseline and the release, and
saying "the gate passed" without saying that would be the kind of half-statement
this document exists to avoid.

**What they are.** Long-standing platform failures shared with the baseline —
environmental (a missing `Crypto` module, event-loop binding in ad-hoc runs),
plus accumulated assertions about tab inventories, migration ordering and
ownership proofs that have drifted from the code.

**What they are not.** They are not this batch's, and none of them is in the
capital-critical path traced in §1: the entry gate, the exposure gate, the fee
arithmetic, the submission authorization, the notification capability and the
provider proxy are all covered by tests that pass, in both directions, through
the real callers.

**Why that is not a free pass.** A suite with 177 standing failures is a suite
whose signal is degraded, and a new failure can hide among them — which is
exactly why the gate compares exact identity SETS rather than counts, and why the
log-line false positive was worth fixing. **Reducing that number is real platform
work and it is not done.** It does not block the release scope in §8, and I am
not claiming it is finished.
