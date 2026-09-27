# CREDENTIAL PROVISIONING REQUEST — and exactly what it unlocks

**This document asks for no secret in this conversation and contains none.** It
names *where* a credential goes, *who* puts it there, and *which specific checks*
it turns from unreadable into measured.

**It also states plainly what it does NOT unlock.** Supplying this credential
clears no market-data evidence gap and no engineering failure. Two of the four
verdicts stay **NO** regardless.

---

## 0 · The request, in one table

| | |
|---|---|
| **what** | a read-scoped API credential for one named Polymarket US account |
| **scope needed** | **read only** — positions, open orders, order history, balances. **No trade, no transfer, no withdrawal permission.** |
| **who provisions it** | the account owner, in the venue's own API-key interface |
| **where it goes** | the environment's secret store for the backend service — never a file in this repository, never a chat message, never a commit |
| **variable names the code reads** | `PMUS_KEY_ID`, `PMUS_SECRET_KEY` |
| **who can read it afterwards** | the backend process only. It is not exposed by any API route, is not sent to the browser, and `test_no_credential_reaches_the_browser` asserts that |
| **what it must NOT be** | a key with trade permission. Read scope is sufficient for everything below, and a trade-scoped key would make the "no order can be sent" claim rest on code alone rather than on code *and* the credential's own scope |

---

## 1 · The exact provisioning steps

1. **In the venue's interface**, on the account named in §2, create an API key
   with **read** permissions only. Do not enable trading, transfers or
   withdrawals.
2. **In the environment's secret store** for the backend service (not in the
   repository, not in a message, not in a ticket), set:
   - `PMUS_KEY_ID`
   - `PMUS_SECRET_KEY`
3. **Restart or redeploy** the backend so the process reads them. No code change
   is required — the client construction already reads these names and refuses to
   build a client without them.
4. **Confirm here that step 2 is done.** Say only *"the credential is in place"*.
   **Do not paste the value.** If a value is ever pasted into a conversation,
   treat it as compromised, revoke it at the venue and issue a new one.
5. **Verification runs through the authorized API-only route.** The readback
   reports which checks became measurable; it reports no key material.

**Nothing in these steps enables a trade.** Submission stays disabled in code by
three independent constants, and the execution gate remains a separate boundary.

---

## 2 · The account selection request

This is a decision only the owner can make, and it is **not** proposed here as a
default.

| | |
|---|---|
| **the account that exists today** | `acct_fc2d773a2afa4851` — **paused**, accounting **unresolved**. It stays paused. Its only route out is `bettor_account_onboarding.resolve_existing`, which requires four complete venue reads |
| **what is needed** | **one named account**, confirmed by the owner, for the bounded pilot |
| **what the code does with the name** | binds it under `ACCOUNT_KEY`. A second account is not reachable without a new binding *and* a new reconciliation |
| **the question to answer** | *which* account, and is it the paused one (after reconciliation) or a different one? |

**And one property the owner should decide deliberately**, because it changes
which of the two risk routes is available:

> **Is this account reachable by anything else?** Another API key, a human on the
> web session, the legacy copier, the manual sleeve. If **no**, isolation can be
> demonstrated and the account-wide exposure total is a formality. If **yes**,
> enforcement is the only route and the venue read becomes load-bearing on every
> order. `bettor_account_exposure.isolation_evidence()` lists the five exhibits
> that would demonstrate isolation; it currently returns `NOT_DEMONSTRATED`.

---

## 3 · What the credential unlocks — specifically

Each row is a check that is **refusing today for want of this read**, with the
refusal it currently returns.

| check | today's refusal | what the credential changes |
|---|---|---|
| **Account-wide exposure** — `bettor_account_exposure.account_exposure` | `VENUE_HELD_POSITIONS` reads `READ_FAILED`, the total is `UNREADABLE` | the venue path becomes readable, so the total becomes a number and the submission gate can evaluate it |
| **Submission authorization** — `bettor_entry_execution.authorize_submission` | `ACCOUNT_WIDE_EXPOSURE_COULD_NOT_BE_MEASURED` | the exposure precondition can pass. **The code constant then refuses instead** — see §4 |
| **Account reconciliation** — `bettor_account_onboarding.record_reconciliation` | `R_NO_EVIDENCE`; four reads unanswered | positions, orders, activities and balances become readable, so reconciliation evidence can exist and be dated |
| **Readiness: `account_selected_and_clean`** | fails — registry clean *and* in-bound reconciliation both required | can pass once reconciliation evidence exists and is fresh |
| **Isolation exhibits** — `NO_PRE_EXISTING_HOLDINGS`, `NO_OPEN_ORDERS`, `NO_UNRESOLVED_SUBMISSIONS` | `NOT_DEMONSTRATED` | three of the five become answerable from the venue's own reads |
| **Fee schedule `VERIFIED_APPLIED`** | nothing has been compared against a real observed charge | *partially.* A read shows historical charges on **legacy** rows. Verifying **this lane's** cumulative-cap arithmetic needs a real multi-fill order from this lane, which needs an authorized pilot, not just a read |

---

## 4 · What it does NOT unlock — and this is the important half

**It does not enable a trade.** Submission is off in three code constants, the
process-bound execution gate authorises every submission at the venue boundary,
and a read-scoped key cannot place an order even if every gate opened.

**It does not clear the market-data evidence gap.** The binding item is that the
venue **publishes no timing guarantee** for market data —
`market_data_design.NO_PUBLISHED_TIMING_GUARANTEE`. A credential gives us the
account's state; it does not make the venue document how current a book message
is. `transactTime`'s denotation stays `UNRESOLVED` with four open hypotheses.
**Every candidate market still refuses on `BOOK_CURRENCY_NOT_ESTABLISHED` after
this credential is in place.**

**It does not clear any engineering failure.** The suite result, the exact-SHA
gate and the deployment readback are unaffected by it.

**It does not verify deployed behaviour.** Everything built in this batch —
account-wide exposure in the submission path, the venue-ordered cumulative fee,
the notification capability — is implemented and tested and **not deployed**.
Deployment and readback are separate steps with their own gate.

---

## 5 · The four verdicts, before and after

| | before the credential | after the credential |
|---|---|---|
| **Engineering verified** | **NO** | **NO** — unchanged. The binding item is the venue's undocumented market-data timing, which no credential supplies |
| **Ready for an authorized funded pilot** | **NO** | **still NO, and closer.** Reconciliation and account-wide exposure become measurable; approved limits, the pilot procedure and the owner's authorization remain outstanding |
| **Actual funded execution and reconciliation verified** | **NO** | **NO** — this needs a real order from this lane, which needs an approved pilot, not a read-scoped key |
| **Strategy profitability validated** | **NO** | **NO** — unaffected. No deployment, passing suite or modelled profit moves this |

**Two of the four cannot move at all on a credential.** Anyone reading this
document should not expect otherwise, and I should not have implied otherwise.

---

## 6 · Work that is NOT waiting on this

Named explicitly, because placing independently actionable work behind a
credential is a way of not doing it.

| item | state |
|---|---|
| provider-key proxy | **next**, and does not need this credential |
| permission matrix over all routes | **done** for the 37-route residue; the three unauthenticated writes are now capability-guarded |
| notification authorization | **done** — server-issued capability, header-only, hashed at rest |
| account-wide exposure in the submission path | **done** — and it refuses without the venue read, which is why the read matters |
| venue-ordered cumulative fees | **done** — sequence, execution instant, per-fill basis, late-arrival placement, successor restatement |
| gate bootstrap so an invalid environment cannot yield a number | **done** — 0 migration failures, 114 tables |
| contract identity element-by-element | **open**, not credential-dependent |
| learning / evaluation loop | **open**, not credential-dependent |
| capacity measurement | **open**, not credential-dependent |
| Command Centre completion | **open**, not credential-dependent |
| exact-SHA gate, API-only deploy, serving-build readback | **open**, not credential-dependent |
