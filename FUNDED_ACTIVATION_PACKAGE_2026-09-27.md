# FUNDED ACTIVATION PACKAGE — for review, 2026-09-27

Everything here is prepared and **nothing here is done**. Funded submission is
disabled in code; this document is what an owner needs in order to decide, plus
the exact change that follows a decision.

It deliberately does **not** ask for a credential in this conversation, and it
contains no secret.

---

## 0 · What is already finished and needs no credential

| | state |
|---|---|
| funded intent/fill/economics schema | applied in production (126–128, 03:58:00Z) |
| funded schema is an enforced condition | every submission path refuses when an object is missing |
| order terminality vs inventory closure | separate predicates, one open *position* |
| exit inventory reservation under `FOR UPDATE` | oversell becomes a discrepancy, not a clamp |
| recovery without a client order id | never adopts; UNRESOLVED keeps exposure |
| fill + economics + closure in one commit | replay-repairable, late fees reconciled idempotently |
| realised P&L and drawdown from a ledger | unrealised reported UNMEASURED, never zero |
| servicing outlives entry permission | exits gated on ownership, not on the entry grant |
| the inputs' own expiry, checked pre-send | expired reservation released, zero venue calls |
| PostgreSQL 18 migration validation | in CI, on the version production runs |

The remaining work in this section is **zero**. What follows needs a decision or
a credential.

---

## 1 · Account onboarding — the exact procedure

Each step is a read that already exists. None of them submits an order.

1. **Name the account.** `POST /api/command/bettor/control/account`
   with `{name, venue}` — records a *proposal*. A recorded proposal is intent,
   not approval, and the desk shows it as such.
2. **Provision the venue credential** as service environment variables
   `PMUS_KEY_ID` / `PMUS_SECRET_KEY` on `sportsassets-api`, through the Render
   dashboard or `render-ops action=env-set` (the workflow masks the value and
   never prints it). **Do not put a credential in a chat message or a commit.**
3. **Read-only venue reconciliation, FIRST.** Before any approval:
   `GET /api/admin/funded-activation-readiness` re-runs onboarding against the
   live account and reports four reads — balance, positions, open orders,
   executions. All four currently read UNREADABLE *because* there is no
   credential, which is the reconciliation working rather than a gap in it.
   **A discrepancy here stops the process**: it means our book and the venue's
   disagree before a single funded order exists.
4. **Approve the limit set, in dollars** (section 2):
   `POST /api/admin/funded-limits/approve`. The effective rails are
   `MIN(frozen, approved)` — approving a larger number than the frozen rail
   changes nothing.
5. **Activate**: `POST /api/command/bettor/control/activate` returns 409 with
   the unmet list until every check is met, and `AUTHORIZED_FOR_A_TEST_VENUE`
   when they are. This is *not* the same as enabling funded submission (step 6).
6. **The code change** in section 3, reviewed and merged as its own commit.

### The account that exists today

`acct_fc2d773a2afa4851` is **paused** and its accounting status is not
resolved. It stays paused: onboarding refuses an account whose accounting is
unreconciled, and that refusal is correct. Either reconcile it or name a
different account — both are owner decisions.

---

## 2 · Proposed limits, beside the frozen rails

The frozen rails are declared in code (`bettor_entry_execution.declaration()`,
`limitsSha a19eb60a…`) and derived from a $1,000 standard notional. The proposal
below is a **pilot** set: deliberately a small fraction, so the first funded
position is small enough that being wrong about anything costs little.

| rail | frozen | proposed pilot | effective = MIN |
|---|---|---|---|
| `MAX_MARKET_EXPOSURE` | $1,000 | **$25** | $25 |
| `MAX_EVENT_EXPOSURE` | $1,000 | **$25** | $25 |
| `MAX_CAPITAL_DEPLOYED` | $3,000 | **$50** | $50 |
| `MAX_CORRELATED_EXPOSURE` | $1,000 | **$25** | $25 |
| `MAX_RESIDUAL_INVENTORY` | $2,000 | **$50** | $50 |
| `MAX_DRAWDOWN` (daily loss stop) | $1,000 | **$20** | $20 |
| `MAX_CAPITAL_HOURS` | 72,000 | **1,200** | 1,200 |

Reading of the pilot set: **one position at a time, about $25 of exposure, and
the lane stops for the day after $20 of realised loss.** At the observed
per-contract prices (0.35–0.65) that is roughly 40–70 contracts.

These are numbers *I* propose. They are not approved, and nothing reads them as
approved until step 4 records an owner approval.

---

## 3 · The activation change, exactly

Three constants, three files. Each is a one-line change and each is
independently reversible:

```
backend/sportsassets/bettor_funded_execution.py
-  FUNDED_SUBMISSION_ENABLED = False
+  FUNDED_SUBMISSION_ENABLED = True          # new funded exposure

backend/sportsassets/bettor_funded_management.py
-  FUNDED_EXIT_SUBMISSION_ENABLED = False
+  FUNDED_EXIT_SUBMISSION_ENABLED = True     # exits and cancels

backend/sportsassets/bettor_entry_execution.py
-  REAL_ORDER_SUBMISSION_ENABLED = False
+  REAL_ORDER_SUBMISSION_ENABLED = True      # any real order at all
```

**Order matters, and I recommend doing this in two releases:**

1. **Servicing first.** Flip `FUNDED_EXIT_SUBMISSION_ENABLED` alone. It cannot
   open a position — it can only reduce one — so it is the half that carries no
   new risk, and it means that if the entry switch is ever flipped, the lane can
   already get out.
2. **Entry second**, after the first funded position's whole lifecycle has been
   reconciled against the venue.

Two further things stay true after all three flips, by design: the
process-bound `execution_gate` authorises every submission at the venue
boundary independently, and without `PMUS_KEY_ID` / `PMUS_SECRET_KEY` no client
can be constructed at all.

---

## 4 · What activation does **not** clear

Approving an account and a limit set does not make a quote younger, reconcile
two published payout rules, calibrate a source, or qualify a candidate. As of
today the funded lane would be authorised to trade and would still find nothing
to trade, because the shadow lane's own refusals apply to it too:

- **`VOID_ABANDONMENT_RULE_CONFLICTS_WITH_BOOK_RULE`** — on 419 valuations and
  every recent one. The venue's void/abandonment rule and the bookmaker's
  disagree, so the two contracts do not pay on the same event. This is a
  **payout conflict**, not a defect, and it must not be waived.
- **`NO_ACTION_HAS_POSITIVE_NET_EDGE`** — on 967. When a candidate *is* priced
  the edge is negative: p ≈ ask, and the 2-cent cost model closes it.
- **Source calibration is NOT established** — `external_source_calibration`
  has no passing measurement, so the MODEL_TRUST_DRIFT gate is NOT_EVALUABLE.

The honest sequence is therefore: finish onboarding and the reconciliation read,
approve nothing until that read is clean, and expect the lane to keep declining
until at least the payout-rule conflict and the edge arithmetic change.
