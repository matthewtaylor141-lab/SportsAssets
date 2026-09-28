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

### Proposed amounts, with the reasoning for each — 2026-09-28

You asked for **justified proposed limits**, so I am now proposing them. I previously wrote *"I am proposing no amounts"*; that was the wrong kind of caution — refusing to propose numbers left you to invent them without the measured anchors I actually hold. **You still set them, and the code tightens but never loosens.**

**The anchor.** In the observed week the account settled 20 markets for \$43,150.12 of cost — **\$2,157.51 average per market**, ranging \$1,239.99 to \$3,696.00 per market across the five days. That is the *legacy* operating scale. The measured figures are in `research/beta48/wk39_inputs.json`.

**The principle these numbers come from.** A first funded run exists to find out whether the software behaves when the money is real. So it is sized such that **losing all of it is an acceptable price for that information**, and it is *not* sized to earn anything. Two orders of magnitude below legacy scale.

| Limit | **Proposed** | Why this number |
|---|---:|---|
| `per_order_usd` | **\$25** | ≈1.2% of the measured \$2,157.51 average per-market cost. At a 0.60 price that is ~41 contracts; at 0.41, ~60. **Enough contracts to exercise the mechanics that actually broke in testing** — depth-capping, a partial fill leaving a residual, and a multi-level `REDUCE` — since the demonstration needed 15 contracts to produce a 9-sold/6-held split. Small enough that a total loss is \$25. |
| `capital_usd` | **\$100** | Four such orders. The lifecycle has to run more than once — entry, partial exit, residual, completing exit, reconciliation — and one defect consuming the whole budget would leave the pilot unable to answer its own question. |
| `event_exposure_usd` | **\$25** | One order per event. A single event's outcome cannot compound across positions, which is the failure mode correlated exposure exists to bound. |
| `max_exposure_usd` | **\$100** | Equal to `capital_usd`. Correlated exposure must not be permitted to exceed deployed capital; setting it higher would make the rail decorative. |
| `daily_loss_stop_usd` | **\$40** | 40% of capital. Above the \$25 a single fully-adverse order can lose, so it does not trip on one ordinary outcome; below \$50, so it halts **before** a second full-order loss rather than after it. |

**What these amounts cannot do, stated plainly.** \$100 cannot establish profitability, capacity, or edge, and I am not implying otherwise. **At this size the pilot tests the software, not the strategy.** The success criterion in §7 is deliberately not profit.

**If you want a different size,** the one number to move is `per_order_usd`: everything else is derived from it (capital = 4×, event = 1×, max = capital, loss stop = 1.6×). Scaling it up scales what a single undiscovered defect costs, in direct proportion.

**Account-wide exposure is a measurement, not an estimate:** gate 8 requires *a readable account-wide exposure total inside its cap*. An unreadable total blocks rather than defaults.

### A loss control that does not yet exist for this lane

`mirror_loss_stop` is the rolling realized-loss breaker for the **copy** lanes. The funded lane's `daily_loss_stop_usd` binds `MAX_DRAWDOWN` at submission time. **There is no continuous funded loss breaker that halts mid-session independently of a submission attempt.** Since this pilot only *reduces* exposure (no entry), the practical exposure is bounded by what is already held — but I am stating the gap rather than implying coverage.

---

## 5 · Credential requirements and secure provisioning

**Settings:** `PMUS_KEY_ID`, `PMUS_SECRET_KEY`. **The desk read credential is `DESK_PASSWORD` and the control credential is `OPERATOR_PASSWORD`** — spelling confirmed from `command-verify`, which writes both; my earlier note said I had not verified the environment variable name.

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

## 6b · A structural problem with the pilot as scoped — found 2026-09-28

**The pilot is "servicing existing inventory only, exposure-reducing, no entry". The funded book is empty.**

| | |
|---|---:|
| `bettor_funded_intents` rows | **0** |
| ENTRY intents | **0** |
| Residual contracts held | **0** |

Measured read-only against production: `research/wk39_coverage_and_attribution.sql` §6, run 246, `2026-09-28T12:36:09Z`.

**So a servicing-only pilot authorized today would have nothing to service.** It would start, find no open funded position, and correctly do nothing. That is not a refusal I can engineer away — it is the scope and the ledger disagreeing, and I had not noticed it while writing §7.

There are two ways to reconcile them, and **I am proposing neither, because each changes what you are approving:**

1. **Adopt existing venue positions into the funded book.** The account has traded (the legacy lanes, through 2026-09-11) and positions settled as recently as 2026-09-27, so venue positions may exist that the funded book does not know about. This keeps "no entry" intact. It requires reading `GET /v1/portfolio/get-user-positions` for the account and an adoption path that proves a position is ours before booking it — and `correlate_venue_order` is deliberately built to *refuse* to adopt on this venue, so this is real work, not configuration.
2. **Permit one bounded entry so there is something to service.** This is honest about what it is: **it is no longer exposure-reducing-only**, and it is a different authorization from the one §7 describes.

**Option 1 is the smaller change to what you approve; option 2 is the smaller change to the code.** Whichever you prefer, the current §7 scope cannot execute, and saying so is more useful than presenting a pilot that would sit idle and calling that containment.

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
| **Absent fair value — 399 of 1,126 rows.** *Corrected 2026-09-28.* | I previously listed this and `QUOTE_STALE` as **two** gaps with the same row count. The cross-tab shows they are **the same 399 rows** — one cause and its effect. And the chain fails at **exactly one link**: provider evidence, mapping, devig, calibration, persistence and consumer lookup account for **zero** rows. Detail: `WHERE_THE_FAIR_VALUE_CHAIN_BREAKS_2026-09-28.md`. |
| **Of that staleness, 74% is ours.** | Provider lag median 14.6 s against a 30 s budget; our own processing delay median 28.7 s. **296 of 399 rows we made stale; 103 arrived stale.** It is not a defect: `venue_pace` is a deliberate process-wide serial gate (one request per 0.35 s, shared with the protected collector, because the venue 429s above ~3 req/s). The only lever is events-per-odds-fetch, which costs ~18–21 provider credits per fetch. **The knob now exists and defaults to today's exact credit spend** — lowering it is a resource decision for you, and I have not spent your API budget on a prediction. |
| **Book currency: M1 is impossible on this feed, not unwired.** *Sharpened 2026-09-28.* | `book_currency_evidence` supplies no mechanism, and the reason is a property of the **feed**: the payload carries no sequence number (a dropped message is undetectable) and nothing distinguishes a snapshot from an increment. **Subscribing supplies neither.** M2 needs the book endpoint to emit a validator, which is unknown. **A funded exit refuses for this reason today**, demonstrated in `test_when_the_book_evidence_goes_away_while_we_hold.py`: the pass completes, sends nothing, leaves the inventory untouched and names the blocker. This is still the single most likely cause of a pilot that does nothing. |
| **Two blockers, in a fixed order.** | `select_exit` refuses at `NO_PROBABILITY` **before** it reaches the book-evidence gate. In the current production state there is no probability either, so a funded position reports the absent valuation first and the book evidence is the blocker behind it. **Both must clear, in that order.** |
| Per-condition settlement grading (draw, overtime, void) | Binary \$1.00/\$0.00 settlement is confirmed; per-condition grading is not. `HOLD_TO_SETTLEMENT` stays unrankable. |
| Collateral-return entitlement on the account | Unknown until the account is read. |
| **Actual credential permissions — the one thing only you can read.** | Established as **not establishable from documentation**, which is a finding rather than a hedge. Both retrieved pages return **0 sentences** on read-only scope and **0 on scopes or permissions at all**; the word *scope* does not appear. Key creation is described as *"Create an API key … You'll get a Key ID and a Secret Key"* — **one key, no scope choice described.** The three things the introduction lists (Orders: *place, modify, cancel, query*; Portfolio; Account) are **API groups, not permission scopes**. I corrected myself twice here: absence of documented scopes proves neither that a read-only key exists nor that every key carries full order authority. **"Unverified" licenses exactly one action: look at the provisioning screen.** That screen is behind your account login, so **please open the API-key creation page and tell me what choices it offers** — that single observation settles it, and nothing I can do from here does. Evidence: `research/evidence/CREDENTIAL_CAPABILITY_2026-09-27.md`. |
| **Four required capabilities — dependencies sharpened 2026-09-28.** | Still four, and nothing was promoted on a documentation read. But the venue's API index is now **enumerated rather than sampled**, so "no merge, split, netting, redeem or convert operation exists" is a positive finding. Two capabilities I did not know existed turned up: `POST /v1/combos` builds a tradable 2–10 leg instrument with independent sides (which **removes the second-venue premise** I was about to record for `FORM_INDIRECT_HEDGE`), and the RFQ API offers a **taker-side** route that would not need `P_FILL` at all. Detail in `bettor_action_inventory`. |

**The honest summary:** the servicing path is verified under controlled inputs and its accounting reconciles. It has never run on a funded order. Four required capabilities are unfinished. And the freshness mechanism most likely to gate a real exit is not yet live in production — so a funded pilot authorized today would most likely *refuse to act*, correctly, rather than trade.

That is the precise fact preventing readiness, and it is engineering work, not an approval you can grant.
