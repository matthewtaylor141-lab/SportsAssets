# MANAGEMENT REPORT — 2026-09-27

Four questions, answered in the order that matters: what is running, what it
has actually done, what is in the way, and which claims are and are not
supported by evidence.

Nothing in this document reports a trade. **Zero autonomous orders have been
placed.** The measured cause is below, and it is not substituted by the
demonstration.

---

## 0 · Release and evidence locations

| | |
|---|---|
| release commit | `d66e89e` (branch `claude/command-center`) |
| gate baseline | `6d75275`, recorded failure identities in `research/evidence/gate/FAILURES_BASELINE_6d75275_2026-09-27_confirming.txt` |
| gate identity record | `research/evidence/gate/GATE_IDENTITY_RECORD.json` |
| operator desk | `https://sportsassets-api.onrender.com/api/command/bettor/desk/page` |
| serving build readback | `command-verify.yml` job `funded-readback` (S1–S6) |
| PostgreSQL 18 migration validation | `command-verify.yml` job `funded-pg18` |
| venue clock / contract probe | `command-verify.yml` job `venue-clock`; route `/api/admin/venue-clock-probe` (V3) |
| freshness observation record | `research/evidence/VENUE_STAMP_SEMANTICS_2026-09-27.json` |
| funded activation package | `FUNDED_ACTIVATION_PACKAGE_2026-09-27.md` |
| blocker census SQL | `research/ev_lane_blocker_census.sql` |

Funded submission switches: `FUNDED_SUBMISSION_ENABLED`,
`FUNDED_EXIT_SUBMISSION_ENABLED`, `REAL_ORDER_SUBMISSION_ENABLED` — **all
False.** The protected worker stays pinned at `f5d1c05`.

---

## 1 · Which models are automatically fitted, what they predict, and
##     whether they can change trading policy

This section exists because three different claims have been conflated.
They are separated here and each is answered on its own.

### 1.1 What is automatically fitted

`workers/rn1x_model_loop` fits, on a schedule, from the point-in-time cohort
dataset. Estimators available: **Ridge, Isotonic, Stumps, Hazard, BaseRate.**

Its fitted targets are exactly two:

- `COHORT_COMPLEMENTARY_FILL_WITHIN_H`
- `COHORT_COMPLEMENTARY_FILL_BELOW_PARITY_WITHIN_H`

Both are **execution questions**: will a complementary fill occur within a
horizon, and will it occur below parity. Predictions are written to a ledger
(`RN1X_MODEL_PROSPECTIVE_V1`) **before** the outcome is known, which is what
makes them checkable rather than retrospective.

### 1.2 What the entry decision actually requires

`EVENT_SETTLEMENT_PROBABILITY` — the probability the event settles our way.

**Neither fitted target is that quantity.** `bettor_model_inventory` reports it
plainly: `scorer_exists_for_entry: false`, and the qualification question is
"unanswerable while the target is wrong; the gate refuses on TARGET first."

### 1.3 Can a fitted model change trading policy?

**No.** `feeds_the_entry_gate: false`. There is no path by which a fit result
alters an admission rule, a limit, a size or a refusal. The entry probability
comes from an **external** source — Pinnacle prices, de-vigged
(`PINNACLE_DEVIG_V1`).

**That source is not an internally trained model and must never be described as
one.** It is a published bookmaker's price with the overround removed
arithmetically. `external_source_calibration` has **no passing measurement**, so
the MODEL_TRUST_DRIFT gate reads NOT_EVALUABLE.

### 1.4 The three claims, kept apart

| claim | status |
|---|---|
| **Whale-behaviour prediction** | Two execution targets are fitted and logged prospectively. Nothing predicts whale *intent*; `whale_roster` records who is studied and which economics each was observed to run, and it "opens nothing and decides nothing." |
| **Rule-based adaptation** | Does not exist in the trading path. Every admission rule, bound and limit is a declared constant; no fit result reaches one. |
| **Profitable autonomous trading** | **Not demonstrated.** Zero autonomous positions. No profitability claim is supported by anything in this repository, and the historical case studies are not evidence for it. |

---

## 2 · What the autonomous lane has done, measured

The lane is LIVE and running ~556 markets a cycle. It has written **1,018
valuations** and opened **zero positions**.

A valuation row is **not** an order. The command centre now prints the order and
fill counts from the order tables beside the valuation count for exactly this
reason.

### The binding blockers, by kind and owner

**(a) `VENUE_BOOK_CURRENCY_NOT_ESTABLISHED` — engineering, unfinished.**

No mechanism with a published contract establishes that a venue book we read is
current within 30 s.

- What `marketData.transactTime` denotes is **UNRESOLVED**.
- Our own receipt instant cannot establish it: a snapshot an intermediary cached
  four minutes ago and returned in 20 ms passes any receipt-age check, and the
  faster the answer the stronger the false certificate.
- **A conclusion I withdrew today.** I had recorded the stamp as a
  LAST_BOOK_CHANGE stamp from a probe where six contracts returned identical
  stamps and identical books across two reads 20 s apart, and moved admission
  onto our receipt instant. A cache replaying one representation to both reads
  predicts that observation exactly, and the probe captured no response header,
  so caching was neither shown nor excluded. The observation is kept; the
  conclusion is withdrawn; the gate it produced is reverted.

*What clears it:* **M1** — subscribe the lane's mapped candidates to the venue's
market-data socket and return per-slug liveness and last-update instants from
`book_currency_evidence`. The repository already speaks the protocol
(`bettor_market_stream`, `obs/streamstate`). Or **M2** — if the book endpoint
emits `ETag`/`Last-Modified`, a conditional re-request answered 304.

*Evidence required:* one run of the V3 clock probe reporting, per read, whether a
cache is in the path and whether a validator is emitted.

**(b) `VOID_ABANDONMENT_RULE_CONFLICTS_WITH_BOOK_RULE` — established payout
conflict, 419 valuations.** The venue's void/abandonment rule and the
bookmaker's disagree, so the two contracts do not pay on the same event. **Not
clearable by engineering and must not be waived.**

**(c) `NO_ACTION_HAS_POSITIVE_NET_EDGE` (967) and
`NO_OBSERVED_DEPTH_INSIDE_THE_BREAK_EVEN_LIMIT` (438) — established
economics.** Where a candidate is priced, p ≈ ask and the 2-cent cost model
closes the gap. A NO_TRADE here is a correct decision, not a defect.

Note the arithmetic: (b) and (c) alone end every recent candidate. **Clearing (a)
will not by itself produce a trade.** It makes the lane evaluable, which is a
prerequisite to learning anything, and nothing more is claimed for it.

---

## 3 · Activation gap list — three separate columns

The remainder is **not** only account approval. It is split below, and the
engineering column is not empty.

### 3.1 Engineering — ours, no credential or decision needed

| item | state |
|---|---|
| M1 market-data subscription for the EV lane's candidates | **NOT DONE.** The binding blocker. Protocol exists; the lane does not subscribe. |
| M2 conditional revalidation on the book path | **NOT DONE.** Blocked on reading one V3 probe result to learn whether a validator is emitted. |
| Event-key selection in `OPEN_BOOK_SQL` | **NOT DONE.** `MAX_EVENT_EXPOSURE` can now be *tightened* by an approval but cannot *see* other positions on the same event. A pilot must hold one position at a time until this is closed. |
| Cumulative taker-fill fee adjustment | **NOT IMPLEMENTED.** Multi-fill expectations are marked PROVISIONAL. |
| Funded schema as an enforced release condition | DONE — every submission path refuses when an object is missing. |
| Inputs' own expiry, checked after any lock, released without a venue call | DONE — proven with a real `FOR UPDATE` held on a second connection; zero adapter calls. |
| Order terminality vs inventory closure; oversell as a discrepancy | DONE. |
| Recovery without a client order id | DONE — never adopts; UNRESOLVED keeps exposure. |
| PostgreSQL 18 migration validation in CI | DONE. |
| One shared freshness rule across entry, activation and exits | DONE (this release). |

### 3.2 Market evidence — not clearable by engineering or by approval

| item | state |
|---|---|
| Settlement-rule compatibility | **CONFLICTING** on every recent candidate. Must not be waived. |
| Positive net edge at observed depth | **NOT PRESENT.** p ≈ ask; cost closes it. |
| External source calibration | **NOT ESTABLISHED.** No passing measurement; MODEL_TRUST_DRIFT is NOT_EVALUABLE. |
| Published fee schedule: rounding mode | **UNRECONCILED.** Audit says half-even; we implement half-up; the page cannot be read from this environment (egress denied). Six exact-tie counterexamples are pinned; one collected fee closes it. |

### 3.3 Owner decisions — yours

| item | state |
|---|---|
| Which funded account, with accounting reconciled | `acct_fc2d773a2afa4851` is **paused** and its accounting is unresolved. It stays paused. |
| The venue credential (`PMUS_KEY_ID` / `PMUS_SECRET_KEY`) as service env vars | Not provisioned. Never to be pasted into a chat message or a commit. |
| The approved limit set, in dollars | Proposed pilot in the activation package; **not approved**. Now five names, matching enforcement. |
| Activation itself | A reviewed code change, three constants, recommended in two releases: servicing first, entry second. |

**Funded execution and servicing are not complete and verified.** The servicing
path is proven end to end through the scheduler against a stubbed transport, and
it has never run against a live funded account, because there is no credential
and because 3.1's first row means the selector refuses on every real read today.
So the remainder must not be described as account approval alone.

---

## 4 · Accounting completeness

Three books, reported side by side and **never summed**:

- **Strategy** (`AUTONOMOUS_ENTRY*`) — zero positions, zero orders, zero fills.
- **Demonstration** — a chosen-input scenario. Does **not** count as strategy
  performance.
- **Funded** — capability reported from the schema catalogue; submission
  disabled.

Unrealised marks are reported **UNMEASURED** where no mark exists, never zero.
Provisional fees are labelled provisional. `FEE_ARITHMETIC_IS_EXACT` is **False**
while the rounding mode and the cumulative adjustment are unreconciled, and no
report may call the fee arithmetic exact while that holds.

`daily_loss_stop_usd` is **not daily.** It maps to `MAX_DRAWDOWN`, which sums
realised losses plus the entire cost basis of every unmarked position across the
whole open book, with no date filter and no reset. It is a **cumulative
worst-case loss ceiling**. The key is unchanged because recorded approvals use
it; `cumulative_loss_stop_usd` is accepted as the accurate synonym.

---

## 5 · The one-sentence version

The infrastructure is real and the refusals are honest; the autonomous lane has
traded nothing, the binding blocker on making it *evaluable* is a piece of
engineering that is mine and is not finished, and the blockers on making it
*profitable* are a payout conflict and an edge that is not there — neither of
which more engineering can fix.
