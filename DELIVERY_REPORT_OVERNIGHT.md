# Delivery report — 2026-09-28

**First, a limitation you asked me to disclose rather than work around.**

My execution environment **cannot remain active unattended.** This session runs turn by turn; when a turn ends, execution stops. I did not work overnight and I am not claiming to. Everything below happened inside the working session.

**What *does* run unattended is the deployed service itself** — `ext_pinnacle_loop` cycles inside the Render web service on its own cadence, writing to the database, with no dependence on my session. That is the existing authorized durable mechanism, and it is already operating. I used it rather than inventing a new one.

**No funded activation occurred and none is authorized.** `FUNDED_EXIT_SUBMISSION_ENABLED` ships `False`. No order has ever been sent. `live_trading_paused = true`.

---

## 1 · Command Centre URL and verified serving build

| | |
|---|---|
| **Command Centre** | `https://sportsassets-api.onrender.com/api/command/bettor/desk/page` |
| Short URL | `/command/desk` (307 → the path above) |
| Verified reachable | Yes — serves the sign-in form, `sha256 e4c741ef…`, read via runner |
| Access model | Desk password for **reads**; a **separate operator token** for controls |
| **Release SHA deployed** | `f9f63d8dce19f384598cd43ef62b1a74de5eaaf6` |
| Deploy id | `dep-dastvnojo6nc73de4csg`, `HTTP 201`, by commit id |
| Route | `render-ops → deploy-api-commit` — API-only by construction |
| **Protected worker** | `f5d1c05` live **before and after**, verified in the deploy log |
| **Serving SHA readback** | `commit: "f9f63d8"`, new `boot_id: 37184746`, `db_ok: true`, `uptime_s: 115.7` |
| **Completed cycle on that build** | **`2026-09-28 03:36:51.026033+00`** |

### The readback surfaced a blocker for the Command Centre itself

`/healthz` on the new build returns:

```
"auth_not_configured": ["desk_password"], "auth_all_configured": false
```

**The desk password is not configured on the service, so nobody can sign in.** The page serves, it is correctly gated, and the sign-in will reject every attempt. That makes the Command Centre URL reachable but **not yet usable**, and it is one command to fix with a value only you should choose:

```
render-ops → action: env-set → service: sportsassets-api
             arg: BETTOR_DESK_PASSWORD=<value you choose>   confirm: DO
```

*(Confirm the exact key name with `render-ops → env-keys` first — `healthz` names the setting `desk_password`, and I have not verified the environment variable's spelling.)*

I did not set it: it is a credential, and choosing one on your behalf is not a routine reversible engineering decision.

### The scheduled cycle, read back on the serving build

| | |
|---|---|
| Cycle completed at | **2026-09-28 03:36:51.026033+00** |
| Writer build | **`f9f63d8dce19f384598cd43ef62b1a74de5eaaf6`** — the exact deployed SHA |
| Loop source hash | `b74d2fb91b75` (was `1ccd667f5aac` on `ad95d69`) |
| State | `LIVE` |
| Label | `ZERO_EVALUATED__INPUT_PATH_BLOCKED` |
| `carries_a_servicing_decision` | **`t`** — the new field is present, which it was not on `ad95d69` |
| `servicing` | `null` |

**A cycle started and finished on the exact build that was gated and deployed.** The servicing digest is `null` and that is correct rather than missing: `_funded_service` returns `None` when no funded account is bound, and the digest maps `None → None` instead of inventing an empty decision set. The field's *presence* is what proves the new persistence took effect.

**I cannot verify the authenticated operator page's data.** It requires the desk token, which I do not hold and will not ask for in chat. What I verified is that the page is reachable, gated, and separates read from control credentials.

## 2 · Release gate — matched regression on the exact final SHA

Same instrument both times: `pytest tests -q --timeout=90 --timeout-method=signal -p no:cacheprovider`, `RN1X_TEST_DSN` **unset**.

| | Baseline `9bc0a78` | Release `f9f63d8` |
|---|---|---|
| Failed | 370 | **370** |
| Passed | 13,116 | **13,217** (+101) |
| Skipped | 423 | 435 |
| Duration | 1518.41s | 1519.86s |

**Counts are not enough, so I diffed the failure identities** from both JUnit XMLs: 370 in each, and the set difference is **empty in both directions** — zero new failures, zero resolved. Evidence: `research/evidence/gate/GATE_f9f63d8_2026-09-28.xml`.

**A process failure I have to report.** I started this comparison, then edited `bettor_mgmt_select.py` while it was running, which invalidated it. I killed that run, discarded its output, committed, and restarted clean. After that I used a separate worktree (`/tmp/impl-wt`) and a separate database so implementation could continue without contaminating the measurement — which is what you had instructed.

## 3 · Actual operating mode, measured in production

Read at 03:26:11Z, `psql exit=0`:

| | |
|---|---|
| Last completed cycle | **2026-09-28 03:25:16.455102+00**, state **LIVE** |
| Writer build | `ad95d69…` (pre-deploy) |
| Loop armed (`ext_pinnacle_shadow`) | **true** |
| Kill switch (`live_trading_paused`) | **true — engaged** |
| Cycle label | **`ZERO_EVALUATED__INPUT_PATH_BLOCKED`** |
| Funded book | **0 intents, 0 entries, 0 unresolved, 0 residual** |
| External valuations | 1,126 rows, 54 markets, newest 2026-09-27 19:35:48+00 |

**The loop runs continuously and admits nothing.** I am not presenting that as a completed trading system — you said plainly that a loop which cannot qualify or manage trades is not one, and these rows agree with you.

**One prediction of my own code confirmed against production:** `carries_a_servicing_decision = false`, because `ad95d69` predates the digest. `last_scheduled_decision()` is written to report *the serving build* rather than render an empty decision set, and that is exactly what it did.

## 4 · Autonomous decisions, executions and management actions that occurred

**On real money: none.** The funded book is empty; no order has been sent.

**In the controlled demonstration** (isolated database, chosen inputs, production components — `LIFECYCLE_DEMONSTRATION_2026-09-28.md`):

| Case A — profitable | Residual | Available |
|---|---:|---:|
| Partial exit, depth caps 8 of 20; venue fills 5 | 15.0 | **12.0** |
| Duplicate delivery of the same execution | 15.0 | 12.0 |
| Restart; recovery reads 2 executions, writes 1 | 12.0 | 12.0 |
| Completing exit → `EXITED_IN_THE_MARKET` | 0.0 | 0.0 |

Basis \$11.0000 · proceeds \$14.1600 · fees \$0.6380 · **realised \$2.5220**, with `realised == proceeds − basis − fees` to 1e-6.

**Case B — loss containment:** `DIRECT_EXIT` 9 @ 0.41 on a 0.60 basis, `locks_a_loss: true`, chosen because holding was worth 0.30. **6 contracts remain held.** `realised_pnl_usd` \$0.0000 (this lane books on *closure*), open-position net cash **−\$5.7100**, and the result on the 9 already sold is **−\$2.0100**.

> **Correction to this report.** I first wrote the sold slice as **−\$1.71** and said the lane "declares none" for cost attribution. Both were wrong. `remaining_basis` already declared *average entry cost per contract*; and −\$1.71 is the fee-free arithmetic — the 9 sold carry \$0.30 of fees, so the figure is **−\$2.01**. See `LIFECYCLE_DEMONSTRATION_2026-09-28.md` for the component-by-component breakdown.

## 5 · Reconciled results, with the three books kept apart

| Book | Realised | Counts as performance |
|---|---|---|
| **Funded (real money)** | **no positions, no results** | `None` — not established |
| **Controlled demonstration** | +\$2.5220 (A), −\$5.7100 open cash (B) | **`false`** |
| Shadow / research | separate lane, separate ledger (`rn1x_*`) | `false` |

Enforced at the reader, not by a label: `classify_book` returns `counts_toward_strategy_performance: false` for a demonstration account, and `command_center` returns `funded_books` and `demonstration_books` separately with **no total spanning them**. It is a convention, not a permission, and the module says so.

## 6 · Every advertised action — verified path or explicitly unavailable

`bettor_action_inventory`, cross-checked by tests against the ranker source and `EXECUTABLE_ACTIONS` so it cannot drift:

| Status | Actions | Meaning |
|---|---|---|
| **EXECUTABLE (3)** | `HOLD`, `DIRECT_EXIT`, `REDUCE` | each names its dispatch **and** the test that proves it |
| **NOT_APPLICABLE (1)** | `TAKE_COMPLEMENT` | the venue documents one instrument per market — **completion, not containment**, citing pages and hashes |
| **REQUIRED_BUT_UNAVAILABLE (4)** | `FORM_INDIRECT_HEDGE`, `COMPLETE_PAIR`, `MERGE`, `POST_COMPLEMENT` | **UNFINISHED.** Named gaps, owner, concrete path each |
| **BLOCKED_ON_EVIDENCE (1)** | `HOLD_TO_SETTLEMENT` | settlement semantics not supplied |

`describe()` states it outright: **4 unavailable required capabilities mean the system is not complete.**

### The defect this rule found

`REDUCE` was advertised, in `EXECUTABLE_ACTIONS`, and **could not fill its own chosen size.** `select_exit` bounded every exit at the ladder's *best* level; a `REDUCE` selected for 10 contracts on a multi-level vwap of 0.578 was submitted bounded at 0.62, which can fill 4. No money was at risk — a limit is never crossed downward — but it was allowed to *win the ranking* on an advantage the order could not realise. Fixed in three layers, and it now refuses rather than under-filling. `as_sale_ladder` had also dropped `api_price`, so the dispatch could not have bounded correctly even if it had tried.

## 7 · Remaining blockers, owners, earliest credible resolution

| # | Blocker | Rows | Owner | Path |
|---|---|---:|---|---|
| 1 | **No independent fair value / no qualified model** | 399 | engineering | The real actionable gap, and it is *upstream* of execution: without a fair value there is no break-even limit. The circularity guard correctly refuses a venue-derived one, so this cannot be shortcut. |
| 2 | **`QUOTE_STALE`** | 399 | engineering | `book_currency_evidence` returns no mechanism in production. **This is the most likely reason a funded exit authorized today would refuse to act.** |
| 3 | **No depth inside break-even** | 488 | **the market** | Not fixable by us. |
| 4 | Four pairing capabilities | — | engineering (+venue for MERGE) | §6 |
| 5 | Per-condition settlement grading | 527 + 564 | venue | Never the only refusal on any row |
| 6 | No read-only database role | — | engineering | Every process can write `live_trading_paused` |
| 7 | Venue rejection on redeployed collateral | — | engineering | Unmodelled; needs the account's entitlement first |
| 8 | 18 timing-out tests | — | engineering | Cause still unestablished |

**A correction to my own census priority.** I told you the execution estimate was the largest actionable cause with "no external dependency identified yet." The plan-level codes show it decomposes: **488 rows are the market offering no affordable depth** (an external dependency, and an economic finding), while **399 are the absent fair value** (the real engineering gap). Detail: `WHY_THE_INPUT_PATH_IS_BLOCKED_2026-09-28.md`.

## 8 · The consolidated decision package

`FUNDED_APPROVAL_PACKAGE.md`. **No existing funded authorization was found** — checked against the code, not assumed. Five things only you can supply:

1. The canonical `account_id` in `bettor_desk_accounts`
2. Five limit values (`capital_usd`, `per_order_usd`, `event_exposure_usd`, `max_exposure_usd`, `daily_loss_stop_usd`) — **I propose no amounts**
3. Confirmation of the scope: `HOLD`/`DIRECT_EXIT`/`REDUCE` on PMUS, servicing existing inventory, exposure-reducing only
4. The credential, provisioned via `render-ops env-set` (value masked, never in chat)
5. Explicit written authorization naming account, capital, limits and scope

Emergency control: `render-ops → sql → pause-on → confirm: DO` writes `live_trading_paused`, read at submission time *inside* the adapter, fail-closed. Cancellation stays ungated.

---

## What I will not claim

- Not that the autonomous system is complete. Four required capabilities are unfinished.
- Not that it is ready for real money tonight. Blocker 2 means it would most likely refuse to act.
- Not any profitability. 9 candidates with measured non-positive economics out of 62; no order ever placed.
- Not that I worked while you slept. I could not, and I have said so.

**What is true:** the release gate passes cleanly on the exact final SHA, the API-only deploy went out with the protected worker untouched, the servicing lifecycle reconciles under controlled inputs, the Command Centre now carries the decision beside the book, every advertised action has a verdict, and the approval package is ready for a decision rather than waiting on one.
