# Owner decision form — funded pilot (for review; nothing here is chosen or activated)

**Dated:** 2026-09-30.

**What this form does and does not do:**
- It records your decisions. It does not activate submission.
- The three submission switches stay `False` in code until a separate yes/no on a named activation build (§F).
- Nothing has been chosen on your behalf: not the account, not any amount, not the hedge policy, not the export policy.

## A · What each limit field actually enforces (from the code)

**Scope:** every limit is measured on the funded lane's open book at the moment a new order is decided. Only **entries and hedge acquisitions** are checked against them. Exits and reductions are not, because they reduce exposure.

| Field you set | Accurate name also accepted | Enforced rail | What it bounds | Cap |
|---|---|---|---|---|
| `capital_usd` | — | MAX_CAPITAL_DEPLOYED | Cost basis of every open position, the proposed one included | 3,000 |
| `per_order_usd` | `per_market_usd` | MAX_MARKET_EXPOSURE | Cost basis on **one market** (venue contract), the proposed one included. Not a per-order cap | 1,000 |
| `event_exposure_usd` | — | MAX_EVENT_EXPOSURE | Cost basis on **one event** (venue event slug). A primary and its same-game hedge count together | 1,000 |
| `max_exposure_usd` | — | MAX_CORRELATED_EXPOSURE | **The same number as `capital_usd`**: every open position is counted in full, as if perfectly correlated. The lower of the two is the binding bound | 1,000 |
| `daily_loss_stop_usd` | `cumulative_loss_stop_usd` | MAX_DRAWDOWN | **Cumulative, with no daily reset.** It adds three things: realised losses (gains do not offset them), mark-to-market losses on open positions that have a bid, and the **full cost** of open positions with no bid, the proposed one included. When reached, new entries and hedges are refused. Only an operator's new book clears realised losses | 1,000 |

Two further rails exist that you cannot tighten. Neither can bind at the proposed scale:
- **MAX_RESIDUAL_INVENTORY: 2,000 contracts.** At $250 and prices of at least $0.40, the lane holds at most about 625 contracts.
- **MAX_CAPITAL_HOURS: 72,000 USD-hours.** $250 held for 72 hours is 18,000 USD-hours.

**Code:** from this release, the two accurate names are accepted and give the same limit digest, and each field's meaning is stored with the recorded limits (commit 5f8767b).

## B · Proposed pilot limits — for your review, separately justified

**Purpose:** a pilot's only job is to demonstrate, with real money at the smallest useful size, what the software has shown only on simulated transport:
- venue acknowledgement;
- fills, partial fills included;
- Xavier's management;
- settlement;
- reconciliation.

It establishes nothing about profitability.

| Field | Proposed | Justification |
|---|---|---|
| `per_market_usd` | **25** | At prices of $0.40–$0.65, $25 buys about 38–62 contracts. That is enough to exercise partial fills, depth-limited exits and REDUCE, so fees are measured at a realistic per-contract scale, while any single market risks $25. |
| `event_exposure_usd` | **50** | This room is for a primary ($25) plus a same-game protective hedge of up to $25. If it equalled `per_market_usd`, the event rail would refuse every protective hedge. That matters under hedge policy (i). |
| `capital_usd` | **250** | Enough for 10 concurrent markets at $25, or 5 hedged pairs. Concurrency, restart recovery and group management then occur for real. |
| `max_exposure_usd` | **250** | This measures the same number as capital. A lower value would silently become the real capital cap, so it is set equal and one number governs. |
| `cumulative_loss_stop_usd` | **75** | 30% of capital. Open positions with no bid count at full cost. The rail refuses only above its value, so with $25 markets and no bids, at most three such positions can be open (25 + 25 + 25 = 75 is allowed; a fourth is refused). Realised losses never reset: after $75 of cumulative realised plus worst-case loss, the pilot stops taking new exposure until you decide to start a new book. Exits and settlement continue. |

**Your choice:** accept these five numbers, or write your own. Each must be above 0 and at or below its cap, and `per_market_usd` cannot exceed `capital_usd`.

## C · When an authorization expires while positions are held

There are two records:
- **Your owner authorization:** you sign it, with a finite lifetime that you choose in §E.
- **The system authorization:** issued from a valid owner authorization when activation is run. It expires after **24 hours** and is renewed only by running activation again while your record is still valid. Nothing renews it automatically.

**What happens when either expires, is revoked, or is invalidated** (a change of account or limits invalidates it):

| Activity | Effect |
|---|---|
| New entries | **Refused** |
| Protective hedges | **Refused** (they add exposure). Xavier's record names the authorization as the gate that stopped them |
| Exits and reductions | **Continue.** They depend only on the exit switch and servicing checks, not on the authorization. This is proven by `test_an_exit_survives_every_entry_side_lapse`, run for expired, revoked, paused-account and submission-disabled cases |
| Settlement, lost-acknowledgement recovery, reconciliation, learning, Xavier records | **Continue** |

**Net effect:** after a lapse, held positions are still managed to exit or settlement, but they cannot be newly hedged.

## D · How the actual activation build is verified

1. **One small commit.** The activation build S1 is the accepted, released build S0 plus one commit. That commit changes only the three switch constants and the tests that pin them `False`, each named in the commit. The diff S0..S1 is reviewed for exactly that and nothing else.
2. **Full gate.** The full gate runs on S1 through the production migration path, compared against S0's accepted report. It passes only with zero new failures, every critical test passing, and changed outcomes limited to the named pinned tests.
3. **Readiness.** Readiness is read at that moment and must show no unmet check: P5, calibration, a reconciled account, your owner authorization, and no open investigation.
4. **Your yes.** You answer yes or no, naming S1.
5. **Deploy S1 only.** Only S1 is deployed, through `render-ops` → `deploy-api-commit` with S1's 40-hex SHA and `confirm=DO`.
6. **Readback from the live process**, not from git:
   - `GET /api/admin/pilot-prerequisites` → `running_build.serving_commit` equals S1, and all three `running_build.switches` are `true`;
   - the heartbeat's writer build equals S1.
7. **Rollback.** Deploy S0 by the same route; the readback then shows all three switches `false`.

## E · Render configuration — read live on 2026-09-30 at 00:09Z

Read through the read-only `render-ops` actions: `api-branch-get` (run 36648916538) and `rootdir-get` (run 36648918818).
- `sportsassets-api`: branch `claude/session-njaewf`, **autoDeploy = no**, Dockerfile `./backend/Dockerfile`, build context `.`.
- `sportsassets-workers`: branch `claude/session-njaewf`, **autoDeploy = yes**. A push to that branch redeploys the workers. Nothing is pushed there without the established release route.

**What this means for provisioning:**
- A push does not redeploy the API.
- An environment save in the dashboard may still offer to redeploy it. Choose **"Save only"**.
- The restart is then done with the reviewed SHA through `deploy-api-commit`.

## F · Your decisions (mark one in each)

1. **Account:** ☐ A: the account the deployed key belongs to (send a label and a redacted key-page screenshot) ☐ B: another account (send a label; provision its key yourself)
2. **Limits:** ☐ accept §B as proposed ☐ my values: capital ___ / per-market ___ / event ___ / max-exposure ___ / cumulative loss stop ___
3. **Owner-authorization lifetime:** ☐ 7 days (proposed: shorter than the 14-day calibration freshness bound, and long enough to avoid daily re-signing) ☐ other: ___ days
4. **Hedges when a limit is reached:** ☐ (i) the limits apply to hedges (current) ☐ (ii) allow hedges that lower worst-case loss (a code change, gated separately)
5. **Public trade export:** ☐ (a) keep public ☐ (b) put behind the admin token before funding
6. **Provisioning:** ☐ `FUNDED_RESOLUTION_KEY` and `FUNDED_RESOLUTION_OPERATOR` entered with "Save only" → reply "resolution credentials in place"
7. **The P5 message:** ☐ sent to the venue (text in the owner request §5)

**Not on this form:** the final yes/no on S1. It is asked only when §D steps 1–3 hold.
