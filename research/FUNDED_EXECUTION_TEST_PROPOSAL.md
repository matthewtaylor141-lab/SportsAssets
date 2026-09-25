# One capped funded execution test — proposal for approval

**Status: NOTHING IS SUBMITTED. This document asks for approval of exact
terms. No order has been placed and none will be until you approve the
terms in §3 verbatim.**

Measured 2026-09-25. Live API SHA `6be6f0c`, confirmed by readback
(`render-ops deploys`, run 36141558952), not by elapsed time.

Standing constraints held throughout: funded submission is disabled; the
accounting-uncertain account stays paused; no pilot account is named here
because naming one is yours to do; the protected auto-deploy branch
`claude/session-njaewf` is untouched.

---

## 1 · What this test is, and what it is not

This is an **execution-mechanics** test under the existing
`MICRO_EXECUTION_CALIBRATION` experiment. Its question is: *does one
approved, human-signed ticket travel the whole lifecycle — submit, fill or
not, exit, reconcile — with every control behaving as built and every
number reconciling?*

**It is not a strategy test.** It does not consult the autonomous entry
lane, does not use a model's edge estimate, and its market is chosen for
mechanical suitability, not expected value. Strategy qualification is
separate and is **not met**: in run 56's 6-hour window the entry lane
evaluated 79 candidates and admitted **0**, refusing every one at
`1_PROBABILITY` (44) or `4_SETTLEMENT_SCOPE` (35), with 0 reaching an
execution estimate. Nothing in this proposal changes that, and a green
result here would not be evidence of edge.

The expected economic outcome of this test is **a small loss** — fees plus
spread on a round trip. That is the price of the evidence, not a defect.

---

## 2 · Readiness: what is verified, and what is missing

### Verified working (offline, against the shipped modules)

| Control | Evidence |
|---|---|
| Per-lifecycle cap | `MAX_ALL_IN_COST_PER_TRADE_LIFECYCLE = 5.00`; `R_PER_TRADE` fires above it |
| Session cap | `MAX_SESSION_CUMULATIVE_SPEND = 100.00`; spend only rises, proceeds never replenish |
| Concurrency | `MAX_CONCURRENT_ORDER_POSITION_LIFECYCLES = 1`; a second ticket refuses `CONCURRENT_LIFECYCLE_LIMIT` (run directly) |
| Kill switch | `live_trading_paused` read fresh at every submission, never cached; absence = paused; malformed = paused |
| Venue minimum | `R_MIN_QUANTITY`; an **unknown** minimum is a refusal, not "no minimum" |
| Tick / price range | `R_TICK`, `R_PRICE_RANGE`; off-tick `0.105` refuses (run directly) |
| Short / leverage / re-entry | `R_SHORT` on `side=SELL` (run directly), `R_LEVERAGE`, `R_AUTO_REENTRY` |
| Funding | `R_CASH_UNKNOWN` on unreadable cash, `R_UNFUNDED` below all-in (both run directly) |
| Freshness | `R_STALE_STATE` unless the ticket carries a fresh account/order read |
| Duplicate prevention | DB partial unique index = one open lifecycle; one ticket per `client_order_id`; `any_unresolved_attempt` blocks **every** send in every session until resolved |
| Ambiguity | a lost response is `AMBIGUOUS`, never a refusal; no blind retry; a cancel ack is not terminal |
| Write-before-send | record, then pre-image, then mark sent, then one send |
| Destination binding | `_destination_mismatch` asks the adapter its own venue/environment/account and refuses any mismatch, missing field, or adapter with no `identity()` |
| Operator stop | `POST /api/calibration/stop` is **not** behind the writes gate, so it works while everything else is off |
| Cancellation | deliberately ungated — a paused system can still pull resting orders |

### Verified production switch state (read today)

```
live_trading_paused   = true          ← the kill switch IS engaged
LIVE_TRADING_ENABLED  = '1'           ← the venue side is enabled
LIVE_COPY_HALT        = '0'
CALIBRATION_WRITES_ENABLED = False    ← code constant; approve/release/resume → 503
```

Read this carefully: **the only thing standing between the gate and a
submission is the kill-switch row.** `LIVE_TRADING_ENABLED=1` already
satisfies the gate's `active_venue` check. "Funded trading is disabled" is
true, but it is disabled by one DB row and one code constant, not by
several independent layers.

### Blockers to this test — code and configuration

1. **There is no submit route.** `guarded_submit` has no caller anywhere
   outside tests. The API ships `state`, `preflight`, `approve` (reserves
   and explicitly sends nothing), `release`, `stop`, `resume`. The code
   says so itself: *"no venue submission path exists."* **This is required
   work before the test can run, and it is the only remaining code gap in
   the lifecycle.**
2. **`CALIBRATION_WRITES_ENABLED = False`** gates approve/release/resume.
   Its own text requires "an authorised commit" to flip.
3. **`live_trading_paused = true`** must be released for the submission
   window and re-engaged after. This is the intended mechanism, listed here
   so the sequence is explicit rather than discovered mid-test.
4. **No pilot account is authorized.** Account identity is
   `pmus:<first 8 of PMUS_KEY_ID>` — bound to the configured credential.
   You must name the account. I have not read which key the API currently
   holds and will not switch accounts silently.
5. **Account identity cannot be confirmed venue-side.**
   `reconcile_read.account_identity` returns `no_identity` when the
   balances payload carries no identifying field, and that is a standing
   blocker rather than a shrug. So the binding is *credential-based*: we
   know which key we signed with, not which account the venue thinks that
   is. A test that moves real money on an unconfirmable account identity is
   a reconciliation risk you should weigh explicitly.

### Blocker repaired today

6. **The evidence gather could not prove it owned the venue read slot.**
   `venue_domain.audit()` returned FAIL on `incentive-manifest` (no
   concurrency block, and it really does read the gateway) and `tape-probe`
   (a false positive via a test module's unreachability assertion). Both now
   join the global domain; `SLOT_OWNERSHIP_PROVEN` is true. Committed
   `2750f18`. **The submit path never consulted this** — it gates the
   evidence gather only — so this was not a submission blocker, and I am not
   claiming it was.

   Still open and recorded, not waived: the classifier walks `research/**`
   only, so `backend/sportsassets/` is invisible to it. Its PASS is narrower
   than "every venue-touching workflow is domained."

### Correction to my previous report

I told you 8 tests fail in the funded path and named a missing
`.github/workflows/calibration-evidence.yml`. **The file is not missing.**
Six of those eight read repository paths relative to the working directory
and I ran pytest from `backend/`. From the repository root the real count
was 2, both the one finding above. I also told you `VENUE_BOOK_UNREADABLE`
on every current MLB candidate; run 56 shows `aec-mlb-cle-kc-2026-09-25`
**open and quoting**, with `bestBidQuote` and `bestAskQuote` present.

---

## 3 · The terms, for your approval

Approve or amend each line. Nothing is submitted until you approve these
exact terms.

### Account
**To be named by you.** Not the accounting-uncertain account, which stays
paused. The ticket's `account` field must equal the adapter's own
`identity()["account"]`, or `_destination_mismatch` refuses before any
pre-image is written.

### Environment
`PRODUCTION`. One of the two values `calibration.ENVIRONMENTS` permits.

### Market
A **binary MLB moneyline** market on polymarket.us, selected at approval
time against these mechanical criteria, none of them about edge:

- `status == MARKET_STATUS_OPEN`, `closed == false`, `tradable == true`
  on both `marketSides`
- `bestBidQuote` **and** `bestAskQuote` both present and readable
- `minimumTradeQty` and `orderPriceMinTickSize` both present
- `feeCoefficient` present
- game start at least 30 minutes away, so no in-play halt lands mid-test
- resolution inside 24 hours, so reconciliation completes promptly

`aec-mlb-cle-kc-2026-09-25` met every one of these today
(`minimumTradeQty 0.01`, `orderPriceMinTickSize 0.01`,
`feeCoefficient 0.0695`, quotes `0.5600` / `0.445`). Whether it still
qualifies at approval time is re-checked then, not assumed now.

### Side
**LONG only** (`side: BUY`). A short refuses `R_SHORT`. No leverage, no
borrowing.

### Size — the smallest the venue and our own rules jointly permit
**1 contract.**

Not 0.01. The venue's `minimumTradeQty` is 0.01, but
`calibration.refusals` requires a positive **whole number** quantity, so
0.01 refuses `QUANTITY_NOT_A_POSITIVE_WHOLE_NUMBER`. One contract is the
true floor, and the stricter of the two rules governs.

### Price
On the venue tick `0.01`, inside `(0, 1)` exclusive. A limit order, placed
**at or better than** the touch — never a market order, never crossing more
than one tick beyond the prevailing quote. The concrete limit is fixed at
approval time from the live book.

### Maximum total loss, including fees

Computed by the shipped modules, not by hand. `all_in_cost` = purchase +
**both** fee reserves, rounded up to the cent. Entry reserved at the
**taker** rate even for a post-only order, because this venue has already
filled a post-only rest as maker=false. Exit reserved at the worst price
factor 0.25, because an exit that has not happened cannot be previewed.

| Limit price | Entry reserve | Exit reserve | **Max total loss** |
|---|---|---|---|
| 0.05 | $0.01 | $0.02 | **$0.08** |
| 0.10 | $0.01 | $0.02 | **$0.13** |
| 0.25 | $0.02 | $0.02 | **$0.29** |
| 0.50 | $0.02 | $0.02 | **$0.54** |
| 0.75 | $0.02 | $0.02 | **$0.79** |
| 0.95 | $0.01 | $0.02 | **$0.98** |

For a fully funded long in a `[0,1]` contract the purchase **is** the
maximum loss — the contract cannot settle below zero and we never owe more
than we paid — so `worst_case_exposure == all_in_cost` at every row. That
is a property of "fully funded, long only"; the moment a short or any
borrowing enters, it stops being the bound and the ticket is refused by
name.

**Proposed ceiling: $1.00 all-in, whatever the chosen price.** That is
20% of the $5.00 per-lifecycle cap and 1% of the $100.00 session cap. Both
existing caps remain in force above it; this is a tighter ceiling for this
one test, not a replacement.

### Permitted actions — exhaustive

1. Read the live book and account state.
2. Build **one** ticket and run `preflight`. Read-only.
3. **You** approve that exact ticket, confirming by its exact
   `clientOrderId`. This takes the reserve and sends nothing.
4. Submit **once**. One claim, one send, no retry of any kind.
5. Read status and reconcile fills.
6. Exit: **at most one** exit order, or hold to settlement. Partial fills
   permitted; the remainder is **not** re-offered
   (`automaticReplacementOrders: false`).
7. Cancel a resting order at any time — ungated by design.
8. Release the reserve once the venue state is terminal and fills
   reconcile.

Anything not on this list is not authorized. In particular: no second
ticket, no size increase, no re-entry, no averaging down, no switching
market or side.

### Abort conditions — any one stops the test immediately

- The kill switch reads anything other than an explicit `false`, including
  unreadable or absent.
- The claim store is unavailable. `StoreUnavailable` is never an empty
  budget.
- Any unresolved prior attempt exists (`CLAIMED`, `PRE_IMAGE_RECORDED`,
  `SENT_OUTCOME_UNKNOWN`).
- The pre-image of open order ids is unreadable.
- The adapter's `identity()` disagrees with the approved ticket on venue,
  environment or account — or cannot answer.
- Available cash is unknown or below the all-in cost.
- The book becomes unreadable, or the market leaves `MARKET_STATUS_OPEN`,
  before submission.
- The submission response is lost (`AMBIGUOUS`). **Stop and reconcile; do
  not resend.**
- Realized all-in cost would exceed $1.00.
- More than one order exists at the venue for this market under our
  account.
- Any refusal name fires that is not on the expected list.

### Recovery procedure

**If the response is lost (`AMBIGUOUS`)** — the designed-for case:
1. Do not resend. The attempt stays `SENT_OUTCOME_UNKNOWN`, which blocks
   every further send in every session and environment.
2. Read open order ids for the market and diff against the persisted
   pre-image. A new id that was not in the pre-image is our order.
3. Read positions and fills for the market.
4. If the order exists: cancel it (ungated), then reconcile and release.
5. If it does not exist and no fill is recorded: resolve the attempt as
   not-sent, with the evidence recorded.
6. Only a **human** resolves the attempt. Nothing auto-resolves an
   ambiguity.

**If a fill happened and we hold inventory:**
1. `POST /api/calibration/stop` to refuse further admissions. Reserves
   already held stay held — a stop does not make a resting order
   impossible to fill.
2. Exit with the one permitted exit order, or hold to settlement. With the
   settlement reader repaired (`6be6f0c`), terminal settlement now reads
   `REPORTED_SETTLEMENT` from the venue's own `settlement` field with
   `marketSides` corroborating orientation — confirmed in production today
   on `aec-mlb-az-col-2026-09-24` (`settlement: 1`, `reader RESOLVED`,
   Arizona `long: true` at price `1`).
3. Reconcile: entry fills, exit fills or settlement payout, fees actually
   collected, and the position closed.
4. Release the reserve.

**Unconditionally, at the end of the test window:**
Re-engage `live_trading_paused = true` and confirm by reading it back.

---

## 4 · Sequence, once you approve

Each step gates the next. I stop and report at any refusal.

1. You name the account and approve these terms.
2. I build the submit route, wired to `guarded_submit` with the
   `calibration` lane, behind admin auth and the writes gate. Tests first.
3. Regression, image gate on the exact SHA, API-only deploy, readback of
   the live SHA.
4. Select the market against §3's mechanical criteria and report it to you
   with the live book.
5. `preflight` the ticket. I show you every refusal name — expecting an
   empty list — and the computed all-in cost.
6. **You approve that exact ticket** by its `clientOrderId`.
7. Release the kill switch for the window.
8. Submit once.
9. Reconcile through exit or settlement.
10. Re-engage the kill switch, release the reserve, report the full
    lifecycle with actual fees collected.

---

## 5 · What I will not do without a further explicit instruction

- Name or switch the pilot account.
- Unpause the accounting-uncertain account.
- Flip `CALIBRATION_WRITES_ENABLED` or release the kill switch before
  you approve these terms.
- Use the autonomous entry lane's output to pick this market or size.
- Push `claude/session-njaewf`.
- Submit anything.
