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
| one shared freshness rule across entry, activation and exits | `bettor_venue_currency`, three verdicts, three mechanisms |
| the shadow lane's event-exposure identity | `OPEN_BOOK_SQL` joins `us_premap.event_slug` (A9) |
| one typed limit schema with units, windows and scopes | `EX.RAIL_TYPES` + `EX.typed_limits()` (A4) |
| a real, persisted account reconciliation with an age | `POST /api/admin/funded-account-reconcile` (A3) |

### AND A SENTENCE THIS DOCUMENT USED TO GET WRONG

An earlier version said "the remaining work in this section is **zero**". That was
not true when it was written and it is not true now. The 2026-09-27 management
audit named engineering that is mine and unfinished, and the honest list is kept
in `COMPLETION_REGISTER_2026-09-27.md`. Open at the time of writing: contract
identity element-by-element (A1), the published fee schedule and a defensible
unrealised mark (A2), cross-lane rail aggregation (A4), the MERIDIAN browser
credential review (A6), triage of the standing baseline failures (A7), and
end-to-end capacity measurement (A8).

**So funded readiness is NOT waiting only on a decision or a credential.** Those
are necessary and they are not sufficient.

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
| `MAX_RESIDUAL_INVENTORY` ⚠︎ | 2,000 contracts | — | 2,000 contracts |
| `MAX_DRAWDOWN` (cumulative loss ceiling, **not** daily) | $1,000 | **$20** | $20 |
| `MAX_CAPITAL_HOURS` ⚠︎ | 72,000 $·h | — | 72,000 $·h |

### Two corrections to this table (audit finding A4)

**⚠︎ marks a rail no owner field reaches.** `MAX_RESIDUAL_INVENTORY` and
`MAX_CAPITAL_HOURS` have no name in `APPROVED_LIMIT_TO_RAIL`, so an approval
cannot tighten them and an earlier version of this table was wrong to show a
proposed value for them. They stay at their frozen amounts.

**And `MAX_RESIDUAL_INVENTORY` is CONTRACTS, not dollars.** The rail is declared
as `MAX_RESIDUAL_INVENTORY_CONTRACTS` and `exposure_from_rows` sums `qty`. An
earlier version of this table showed "$2,000 / $50", so an owner reading it would
have believed they were approving fifty dollars of residual inventory when the
number the code compares is a contract count — at the observed 0.35–0.65 prices,
roughly $18–$33. `MAX_CAPITAL_HOURS` is dollar-hours, not hours.

**`MAX_DRAWDOWN` has no daily window.** The owner field is called
`daily_loss_stop_usd` and the rail it maps to sums realised losses plus the entire
cost basis of every unmarked open position, across the whole open book, with no
date filter and **no reset**. A $20 approval means: refuse the next entry once
worst-case exposed loss reaches $20, and keep refusing until positions settle or
mark better. It does **not** lift tomorrow. `cumulative_loss_stop_usd` is accepted
as the accurate synonym; the old key is kept because recorded approvals use it.

Every unit, window and aggregation scope above is now declared once, in
`bettor_entry_execution.RAIL_TYPES`, and `EX.typed_limits()` returns this table
with the frozen, proposed and effective values side by side in their own units.

Reading of the pilot set: **one position at a time, about $25 of exposure, and
the lane refuses further entry once worst-case exposed loss reaches $20 — which
does not reset overnight.** At the observed per-contract prices (0.35–0.65) that
is roughly 40–70 contracts.

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
