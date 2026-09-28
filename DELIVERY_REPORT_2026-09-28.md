# Delivery report — bounded release, 2026-09-28

**No funded activation is authorized by this release, and none is claimed.** `FUNDED_EXIT_SUBMISSION_ENABLED` ships `False`; no order has been sent; the protected worker is untouched.

**This is not "all requirements completed."** Section 6 lists what remains, with owners and dependencies. Complement execution and indirect hedging stay **OPEN**.

---

## 1 · Release identity

| | |
|---|---|
| Branch | `claude/command-center` |
| **Final SHA** | `f9f63d8dce19f384598cd43ef62b1a74de5eaaf6` |
| Previously serving | `ad95d69` (deployed 2026-09-27T19:36:06Z, live) |
| Protected worker | `sportsassets-workers`, **pinned, not redeployed** |
| Release route | `render-ops` → `deploy-api-commit` (API-only by construction: *"deploys ONLY sportsassets-api, by commit id. Cannot reach a worker."*) |

*(Command Centre URL, serving SHA readback and scheduled-cycle identifier: §5.)*

## 2 · Matched regression on the exact final SHA

| | Baseline | Release |
|---|---|---|
| SHA | `9bc0a78` | `f9f63d8` |
| Instrument | `pytest tests -q --timeout=90 --timeout-method=signal -p no:cacheprovider`, `RN1X_TEST_DSN` **unset** | identical |
| Tests | 13,909 | *(§5)* |
| Failures | 370 | *(§5)* |
| Skipped | 423 | *(§5)* |
| Duration | 1,518.4s | *(§5)* |

**A correction I have to make about my own process here.** I started this comparison, then edited `bettor_mgmt_select.py` while it was running — which invalidates it, because the tree changed under a measurement in flight. I killed that run, discarded its output, committed the changes, and restarted on the clean final SHA. The numbers reported are from the clean run only.

The baseline ran with **no database**, which makes the comparison genuinely reproducible: no shared DB state to drift. It is a different instrument from the 152-test gate baseline and the two are not comparable.

## 3 · Controlled lifecycle trace and reconciled accounting

Full write-up: `LIFECYCLE_DEMONSTRATION_2026-09-28.md`. Traces: `research/evidence/lifecycle/CASE_A_PROFITABLE_EXIT.json`, `CASE_B_LOSS_CONTAINMENT.json`.

Driven through the authorized scheduled path — `ext_pinnacle_loop.cycle → _funded_service → manage → select_exit → submit_exit → pmus` with the transport substituted. The test decides, sizes, prices and books nothing; every assertion is a database readback.

**Case A — profitable, long 20 @ 0.55:**

| Step | Mechanic | Residual | Available |
|---|---|---:|---:|
| 1 | Partial exit: depth caps at 8 of 20; venue fills 5 | 15.0 | **12.0** |
| 2 | Duplicate delivery of the same execution | 15.0 | 12.0 |
| 3 | Restart; recovery reads 2 executions, writes 1 | 12.0 | 12.0 |
| 4 | Completing exit; `EXITED_IN_THE_MARKET` | 0.0 | 0.0 |

Basis \$11.0000 · proceeds \$14.1600 · fees \$0.6380 · **realised \$2.5220**, and `realised == proceeds − basis − fees` to 1e-6.

**Case B — loss containment, long 15 @ 0.60 into a 0.41 book:** `DIRECT_EXIT` 9 @ 0.41 with `locks_a_loss: true`, chosen because holding is worth 0.30. **6 contracts remain held**, position not closed. Basis \$9.0000 · proceeds \$3.6900 · realised **\$0.0000** (this lane realises on *closure*) · open-position net cash **−\$5.7100**. The residual surfaces as `RESIDUAL_INVENTORY_STILL_HELD`.

**Labelled and excluded at the reader**, not by a heading: the account id carries `DEMONSTRATION`, so `bettor_funded_book.classify_book` returns `counts_toward_strategy_performance: false`, and `command_center` returns `funded_books` and `demonstration_books` separately with no total spanning them. It is a convention, not a permission, and the module says so.

**"Production code exercised under controlled inputs" is not "production funded behavior verified."** The prices, depth and probability are chosen. It establishes nothing about opportunity or profitability.

## 4 · Actual candidate census

`research/census_crosstab.sql` via `research-sql.yml` against the production read replica. Full write-up: `CENSUS_CROSSTAB_2026-09-28.md`.

**1,126 evaluation rows over 62 distinct candidates** (18.16 each) on 54 events. Every count I previously gave as "candidates" was rows.

| Bucket | Rows | Distinct candidates |
|---|---:|---:|
| **1 Qualified non-positive economics** | 17 | **9** |
| 2 Economics not refused, blocked elsewhere | 42 | 22 |
| 3 Execution economics unmeasured | 668 | 54 |
| 4 Valuation evidence missing | 399 | 59 |
| 5 Settlement conflict *alone* | 0 | — |
| 6 Settlement unresolved *alone* | 0 | — |

**Measured negative economics: 9 candidates.** Not 1,073 rows, not 527. 98.4% of the "no positive edge" refusals co-occur with a missing execution estimate, so they are an artefact of a missing input, not an economic finding.

**The largest actionable gap is the execution estimate — produced on 59 of 1,126 rows (5.2%)**, not settlement. Three of my earlier claims are refuted in that document, including "42 blocked on settlement alone" (measured: `every_other_stage_ran_and_passed = 0`).

## 5 · Deploy, readback and operator-page verification

*(Filled in after the gate completes — see the live section below.)*

## 6 · Remaining management-report requirements

| # | Requirement | Status | Owner | Dependency |
|---|---|---|---|---|
| 1 | **Complement execution** (`TAKE_COMPLEMENT`) | **OPEN — and narrowed to closed** | engineering | The venue documents **one instrument per market**; buying NO at `a` *is* selling YES at `1−a`. On PMUS this is not a distinct action, so it is permanently ineligible here rather than pending implementation. It remains open only for a TWO_TOKEN venue. |
| 2 | **Indirect hedging** (`FORM_INDIRECT_HEDGE`) | **OPEN** | engineering | No destination-venue order plan, no inventory/exposure reservation, no partial-execution or two-leg reconciliation. Unavailable for execution and named as such in `UNIMPLEMENTED_ROUTES`. |
| 3 | `MERGE` / merge mechanism | **NOT_IDENTIFIED** | venue | Neither published collateral-return mechanism is "complete a binary pair to release collateral". See `VENUE_COLLATERAL_MECHANICS_2026-09-28.md`. |
| 4 | **Execution-estimate coverage** (5.2% → usable) | **OPEN, largest actionable** | engineering | None external identified. This is the single upstream gap behind 668 rows / 54 candidates. |
| 5 | `QUOTE_STALE` / `transactTime` freshness | **OPEN** | engineering | The most common *first* refusal, 399 rows. Needs the M1/M2 currency mechanism in production; `book_currency_evidence` returns no mechanism today, which is why the scheduled path refuses on book currency. |
| 6 | **Exit rejected for redeployed collateral** | **OPEN, newly identified** | engineering | The venue rejects a close when freed collateral is deployed elsewhere. `submit_exit` does not model it. Not patched: the account's collateral-return entitlement is unknown, and inventing a refusal code for an unobserved mechanism would be guessing. |
| 7 | **Collateral-return entitlement on our account** | **NOT_IDENTIFIED** | owner/venue | Both venue pages condition on *"when collateral return is enabled on your account"*. Requires an account read, which requires a credential this container does not hold. |
| 8 | **Database permission separation** | **NOT IMPLEMENTED** | engineering/owner | One `DATABASE_URL`; every process can write every `ingestion_state` key including `live_trading_paused`. Remedy specified in `READ_ONLY_CREDENTIAL_AND_PERMISSION_BOUNDARY.md` §4. |
| 9 | **Read-only venue credential capability** | **NOT_ESTABLISHED** | venue | Never assume it exists. Until a key is issued and its refusals observed, treat any present credential as able to trade. |
| 10 | **18 timing-out tests** | cause unestablished | engineering | Five files time out under the measured conditions. Not "hanging tests" — the blocking cause is not established, and I have twice mis-stated the count. |
| 11 | Defensible training data / pairing policy labels | **OPEN** | engineering | Unreconciled case-study data must not be used as verified training labels. |
| 12 | Venue translation, measured | **PARTIAL** | engineering | Institutional capability stays `NOT_IDENTIFIED` until inspected. |

## 7 · Funded-pilot authorization package — a request, not an authorization

**Nothing here activates capital.** `bettor_funded_activation` reports `authorises_capital: false`, and it refuses with `FUNDED_ACTIVATION_REQUIRES_THE_OWNERS_WRITTEN_AUTHORIZATION` absent an explicit grant. The four items below are what the code requires; I am asking for them, not assuming them.

### 7.1 Account — exactly one value needed

The **canonical `account_id` in `bettor_desk_accounts`**. The pause state and the accounting state are read from that row, not from a display name. It must satisfy:

- `status = ACTIVE` (else `ACCOUNT_IS_NOT_ACTIVE`)
- `paused = false` (else `ACCOUNT_IS_PAUSED`) — **and the existing account stays paused until reconciliation succeeds**
- `accounting_status ∈ {CLEAN, RECONCILED, VERIFIED}` (else `ACCOUNT_ACCOUNTING_IS_NOT_RESOLVED`)

A new registry row is **not** evidence: *"Inserting a registry row proves an INSERT ran."* Eligibility is earned against the venue by four reads that reconcile — `balances`, `positions`, `open_orders`, `executions` — each verdict `RECONCILED`. An account is created `PENDING_VERIFICATION / UNVERIFIED / paused=true`.

A demonstration account and a research-waived account **both fail** this gate by design (`demonstration_does_not_qualify`, `research_waived_does_not_qualify`).

### 7.2 Credential — capability unestablished, protect accordingly

`PMUS_KEY_ID` and `PMUS_SECRET_KEY`. Reads required for onboarding: `GET /v1/account/balances`, `portfolio.positions` (paged to EOF), `orders.list`, `portfolio.activities`.

**Do not assume a read-only credential exists.** Its granularity is `NOT_ESTABLISHED`; treat any key issued as able to trade until its refusals are observed. Never in chat or logs.

### 7.3 Limits — five values, all required, owner-approved

| Limit | Rail it binds |
|---|---|
| `capital_usd` | `MAX_CAPITAL_DEPLOYED` |
| `per_order_usd` | `MAX_MARKET_EXPOSURE` |
| `event_exposure_usd` | `MAX_EVENT_EXPOSURE` |
| `max_exposure_usd` | `MAX_CORRELATED_EXPOSURE` |
| `daily_loss_stop_usd` | `MAX_DRAWDOWN` |

An incomplete set refuses with `LIMIT_SET_INCOMPLETE`; an unapproved one with `LIMIT_SET_NOT_APPROVED_BY_THE_OWNER`. **Approvals can only tighten.** I am not proposing amounts: an unspecified amount is exactly what I was told not to assume.

### 7.4 Supported operating scope — what would actually be authorized

Only **`DIRECT_EXIT`** and **`REDUCE`**, on **PMUS**, servicing **existing** inventory. Everything else in the action table is selection-ineligible with a named code, and the total-dispatch guard refuses rather than silently holding. Entry remains behind its own separate switches.

### 7.5 What I am asking for

1. The canonical `account_id`, and confirmation that it may be unpaused **after** four reconciling venue reads.
2. The five limit values, as the owner-approved set.
3. Confirmation of the operating scope in 7.4 — or a narrower one.
4. Explicit written authorization. Without it the lane refuses, and that refusal is correct.

**Blocked pending the above.** No part of this release is affected by that: everything in §§1–5 is non-submitting.
