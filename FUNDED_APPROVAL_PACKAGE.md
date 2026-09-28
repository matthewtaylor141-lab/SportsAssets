# Funded pilot — one consolidated approval package

**This is a request for authorization. It authorizes nothing.** `bettor_funded_activation` reports `authorises_capital: false` and refuses with `FUNDED_ACTIVATION_REQUIRES_THE_OWNERS_WRITTEN_AUTHORIZATION` absent an explicit grant naming the account, capital, limits and scope.

**No existing funded authorization was found.** I checked the code's requirements rather than assuming: there is no valid, unrevoked authorization row covering the verified path. Urgency is not approval, and I have not inferred one.

Everything in this document is a dependency on you. It is disclosed now, not at the end of engineering.

---

## 1 · What you must provide — the five items

| # | Item | Why it cannot be inferred |
|---|---|---|
| 1 | **The canonical `account_id`** in `bettor_desk_accounts` | The pause state and accounting state are read off that row, not from a display name. I must not invent an identifier. |
| 2 | **Five limit values** (§4) | An incomplete set refuses with `LIMIT_SET_INCOMPLETE`; an unapproved one with `LIMIT_SET_NOT_APPROVED_BY_THE_OWNER`. I am proposing no amounts — an unspecified amount is the thing I was told not to assume. |
| 3 | **Confirmation of the operating scope** in §3, or a narrower one | Only you can say the supported scope is the scope you want funded. |
| 4 | **The venue credential**, provisioned per §5 | It must never pass through chat or a log. |
| 5 | **Explicit written authorization** naming account, capital, limits and scope | Without it the lane refuses, and that refusal is correct. |

---

## 2 · Account and reconciliation

The account must satisfy three row conditions, each with its own refusal:

| Condition | Refusal if unmet |
|---|---|
| `status = ACTIVE` | `ACCOUNT_IS_NOT_ACTIVE` |
| `paused = false` | `ACCOUNT_IS_PAUSED` |
| `accounting_status ∈ {CLEAN, RECONCILED, VERIFIED}` | `ACCOUNT_ACCOUNTING_IS_NOT_RESOLVED` |

**A registry row is not evidence.** The module's own words: *"Inserting a registry row proves an INSERT ran."* Eligibility is earned against the venue by **four reads that must all return `RECONCILED`**:

| Check | Venue read |
|---|---|
| `balances` | `GET /v1/account/balances` |
| `positions` | `client.portfolio.positions`, paged to EOF |
| `open_orders` | `orders.list` |
| `executions` | `portfolio.activities` — raises on an unreadable or truncated page set |

Verdicts are `RECONCILED` / `DISCREPANCY` / `UNREADABLE` / `NOT_SUPPORTED_BY_THE_ADAPTER`. **`UNREADABLE` blocks**, deliberately: *"'we could not look' must never render the same as 'we looked and it was empty'."*

An account is created `PENDING_VERIFICATION` / `UNVERIFIED` / `paused = true`. **The existing account stays paused until reconciliation succeeds** — that standing instruction is unchanged by this package.

A demonstration account and a research-waived account both fail this gate by design (`demonstration_does_not_qualify`, `research_waived_does_not_qualify`).

**Sequence:** provision credential → run the four reads → all four `RECONCILED` → unpause → then and only then authorize.

---

## 3 · Supported instruments and operating scope

**Venue:** Polymarket US (`PMUS`, venue class `FUNDED`).

**Instruments:** binary event contracts, one instrument per market (the YES side). The venue's own mechanics: *"There's only one instrument per market — the YES side."* One central limit order book, bids and asks.

**Actions that would be authorized — three, and only three:**

| Action | Sends an order | Quantity rule |
|---|---|---|
| `HOLD` | No | — |
| `DIRECT_EXIT` | Yes | `min(residual, size_at_best)` — depth-capped |
| `REDUCE` | Yes | every ladder level whose post-fee proceeds beat holding, bounded at the **marginal** level |

**Scope is servicing existing inventory.** Entry sits behind its own separate switches and is not part of this request.

**Four required capabilities are unavailable, so this pilot does not include them:** `FORM_INDIRECT_HEDGE`, `COMPLETE_PAIR`, `MERGE`, `POST_COMPLEMENT`. Each is registered in `bettor_action_inventory` with its missing pieces, owner and completion path. **Disabling them is containment, not completion — the system is not finished.**

`TAKE_COMPLEMENT` is **not applicable** on this venue: buying NO at `a` *is* selling YES at `1−a`, the same order on the same book. That one is complete rather than contained, on the venue's published mechanics.

---

## 4 · Capital, exposure and loss controls

Five values, all required, and **approvals can only tighten**:

| Limit | Rail it binds | Enforced at |
|---|---|---|
| `capital_usd` | `MAX_CAPITAL_DEPLOYED` | `bettor_entry_execution.authorize_submission` |
| `per_order_usd` | `MAX_MARKET_EXPOSURE` | same |
| `event_exposure_usd` | `MAX_EVENT_EXPOSURE` | same |
| `max_exposure_usd` | `MAX_CORRELATED_EXPOSURE` | same |
| `daily_loss_stop_usd` | `MAX_DRAWDOWN` | same |

**I am proposing no amounts.** You set them; the code tightens but never loosens.

**Account-wide exposure is a measurement, not an estimate:** gate 8 requires *a readable account-wide exposure total inside its cap*. An unreadable total blocks rather than defaults.

### A loss control that does not yet exist for this lane

`mirror_loss_stop` is the rolling realized-loss breaker for the **copy** lanes. The funded lane's `daily_loss_stop_usd` binds `MAX_DRAWDOWN` at submission time. **There is no continuous funded loss breaker that halts mid-session independently of a submission attempt.** Since this pilot only *reduces* exposure (no entry), the practical exposure is bounded by what is already held — but I am stating the gap rather than implying coverage.

---

## 5 · Credential requirements and secure provisioning

**Settings:** `PMUS_KEY_ID`, `PMUS_SECRET_KEY`.

**Provisioning route — the only one that keeps the value out of a log:**

```
render-ops → action: env-set → service: sportsassets-api
             arg: PMUS_KEY_ID=<value>     confirm: DO
render-ops → action: env-set → service: sportsassets-api
             arg: PMUS_SECRET_KEY=<value> confirm: DO
```

The workflow masks the value in its log and Render redeploys the service. Verify with `env-keys`, which returns **key names only, never values**.

**Never** paste a credential into this conversation, a commit, an issue or a PR.

**Capability is NOT_ESTABLISHED.** No read-only venue credential has been inspected. Whether the venue issues a key that reads quotes and holdings but cannot submit is unknown. **Until a key is issued and its refusals observed, assume any key provisioned can trade** — and protect it accordingly.

---

## 6 · Emergency procedures

### Stop everything — the admin kill switch

```
render-ops → action: sql → arg: pause-on → confirm: DO
```

Writes `live_trading_paused = true` in `ingestion_state`. `execution_gate` reads it **at submission time, inside `pmus.submit_fok` and `pmus.close_position`** — not at the call sites — so it covers every caller, including the three that pass the function as a callable to `asyncio.to_thread`. **Denial raises rather than returns**, and an unreadable row **fails closed**.

Read state: `sql → pause-state`. Release: `sql → pause-off → confirm: DO`.

**Cancellation is deliberately not gated** — cancels reduce exposure and stay available while paused.

### Stop new code reaching the service

`api-branch-get` reads the API's branch and autoDeploy. The release route is `deploy-api-commit`, which takes a commit id and **cannot reach a worker**.

### Roll back

`deploys` lists recent deploys with status and commit; `deploy-api-commit` with an earlier SHA restores it. Previously serving: `ad95d69`.

### Two honest gaps in the emergency story

1. **No read-only database role.** One `DATABASE_URL`. Every process that reaches the database can write every `ingestion_state` key — **including `live_trading_paused` itself**. The kill switch is a row that any process could clear. The remedy (a second role with control keys withheld, via a second DSN) is **NOT IMPLEMENTED**. Detail: `READ_ONLY_CREDENTIAL_AND_PERMISSION_BOUNDARY.md` §4.
2. **A venue-side rejection we do not model.** The venue rejects a close when freed collateral has been redeployed: *"If that buying power is already deployed elsewhere, the order will be rejected."* `submit_exit` has no code for it, so it would surface as a generic venue error. Unpatched deliberately — the account's collateral-return entitlement is unknown, and inventing a refusal for an unobserved mechanism would be guessing.

---

## 7 · Bounded pilot — what I would ask to run

| | |
|---|---|
| Scope | Servicing existing inventory only: `HOLD`, `DIRECT_EXIT`, `REDUCE` |
| Direction | **Exposure-reducing only.** No entry. |
| Venue | PMUS |
| Duration | One trading day, then stop and reconcile before any extension |
| Success criterion | **Not profit.** That the lifecycle executes, the ledger reconciles, and the accounting identity `realised == proceeds − basis − fees` holds on real fills. |
| Abort | Any `DISCREPANCY`, any `UNRESOLVED` intent, any fee the venue charges that the schedule did not predict → `pause-on` and reconcile by hand. |

**I am not claiming this will be profitable.** The only measured economics are 9 candidates with non-positive economics out of 62 distinct candidates observed, and no order has ever been placed.

---

## 8 · Remaining evidence gaps that bear on the decision

| Gap | Effect on the pilot |
|---|---|
| Execution estimate produced on **5.2%** of evaluations | The lane can service inventory but rarely *qualifies* new opportunity. This is the largest actionable engineering gap. |
| `QUOTE_STALE` — most common first refusal (399 rows) | `book_currency_evidence` returns no mechanism in production, so the scheduled path refuses on book currency. **A funded exit will refuse for this reason until the M1/M2 currency mechanism is live.** This is the single most likely cause of a pilot that does nothing. |
| Per-condition settlement grading (draw, overtime, void) | Binary \$1.00/\$0.00 settlement is confirmed; per-condition grading is not. `HOLD_TO_SETTLEMENT` stays unrankable. |
| Collateral-return entitlement on the account | Unknown until the account is read. |
| Read-only credential capability | Never assume it exists. |

**The honest summary:** the servicing path is verified under controlled inputs and its accounting reconciles. It has never run on a funded order. Four required capabilities are unfinished. And the freshness mechanism most likely to gate a real exit is not yet live in production — so a funded pilot authorized today would most likely *refuse to act*, correctly, rather than trade.

That is the precise fact preventing readiness, and it is engineering work, not an approval you can grant.
