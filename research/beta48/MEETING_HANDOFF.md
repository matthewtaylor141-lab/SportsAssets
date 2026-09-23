# Shadow desk — management handoff, 2026-09-23

## 1. Where to look

**`https://command.bettortoken.com`** → **Desk · REPLAY** tab.
Desk password unlock; HttpOnly session cookie scoped to `/api/command`.

## 2. Release, policy, model

| | |
|---|---|
| API release | `2da37d2` (previous live: `2d7ed3f`, 13:47:37Z) |
| Engine | `BETTOR_DESK_V1` |
| Policy | `FERRARI_INSPIRED_DEV_V1` — **development policy, not a trained strategy** |
| Fill model | `PRINT_THROUGH_WITH_QUEUE_SHARE_V1`, queue_share **0.25 (ASSUMED)** |
| Learned artifact | frozen isotonic price→settlement curve, 7,364 train rows |
| `P_FILL` | **NOT_IDENTIFIED** — preserved, not resolved |

**Policy maturity, per rule.** Calling this "the Ferrari model" would be
false:

| rule | status |
|---|---|
| entry band | **HAND_WRITTEN** (Ferrari-inspired) |
| pair completion | **HAND_WRITTEN** (pair-clears arithmetic) |
| residual exit | **LEARNED** (the frozen curve) |
| fill probability | **ASSUMED** (queue_share) |

## 3. Live-data decisions

The live engine is running and has **not stopped**: `shadow_decisions`
25,995 rows, newest **1 second old** at the audit read.

**Every decision in the last 24 hours is `NO_TRADE`** — 3,629
(`BETTOR_EV_SHADOW_V5`) + 3,001 (`RN1_SHADOW_V1`). The refusal
distribution:

| blocker | n |
|---|---:|
| `INDEPENDENT_EV_NOT_ESTABLISHED` | 3,629 |
| `P_FILL_NOT_IDENTIFIED` | 3,629 |
| `NO_FAIR_VALUE` | 3,629 |
| `SYMBOL_NOT_RESOLVED` | 2,592 |
| `MARKET_STATE_UNREADABLE` | 2,165 |
| `SPREAD_TOO_WIDE` | 592 |
| `BOOK_UNREADABLE` | 409 |

**Live shadow orders: 0. Live shadow positions: 0.** The engine is not
broken — it is refusing on production risk controls, and those controls
were not weakened to produce a busier screen.

**The binding requirement was found and fixed in the code:**
`shadow_store.record_execution()` and `record_position()` existed,
were correct, and had **zero callers in the entire tree**. `bettor_desk`
is what calls them. The live loop that drives it is **not yet
deployed** — see §7.

## 4. Reconciled shadow P&L — HISTORICAL REPLAY

### Three corrections to what I said in the first handoff

**1. The replay tape is NOT the market tape.** I called its events
"recorded prints". `trades` is keyed on `whale_id`; every row is **one
tracked account's own execution**. Ferrari filling at a price is
evidence that *Ferrari's* order filled — not that ours would have.
Labelled `SINGLE_ACCOUNT_EXECUTIONS` throughout.

**2. Wrong venue.** The fills come from the on-chain listener
(`polygon_ws_url`, `PM_EXCHANGE_V3_ADDRESSES`,
`ts_provenance = polygon_block_timestamp`) — **Polymarket global**. The
fees applied are the published **PMUS** schedule. That is a
`TRANSFERRED_SCENARIO`, not same-venue execution evidence.

**3. The learned exit had lookahead, and it is measured.** 2,404 of
7,364 training rows (**32.6%**) come from markets that traded *inside*
the replay window, and **2,402** training markets resolved *after* the
replay's first event. Verdict: **`DEVELOPMENT_EVIDENCE`**. Its own
pre-registered test was **NOT SUPPORTED** (paired log-loss difference
+0.0052, 95% CI [−0.0017, +0.0120]).

**Withdrawn:** *"the measured edge is in the tails."* Gross of costs,
measured on prices Ferrari *chose* to buy, and formed after I had
already inspected that data. **Now a research hypothesis, not a
finding.**

### The full economic position

Window 2026-08-21T14:28:09Z → 2026-09-23T13:07:08Z, 19,890 single-account
executions, 1,006 conditions, $100,000 start.

| | |
|---|---:|
| Realized P&L | −$1,898.29 |
| Cash | $73,571.39 |
| **Unvalued exposure** | **$24,530.37 across 396 legs** |
| Inventory mark | **NOT_IDENTIFIED** |
| Executable liquidation | **NOT_IDENTIFIED** |
| **Total portfolio performance** | **NOT_IDENTIFIED** |

**The realised figure is one component, not the whole.** No mark and no
depth exist for the 396 unresolved legs, so neither an unrealised
figure nor a liquidation estimate can be produced. The ledger identity
reconciles (drift −0.0) — **that proves accounting consistency only,
and validates neither the valuations nor the fill assumptions.**

Orders: 687 filled · 529 partial · **782 expired never filled** ·
34.4% fill rate. Consumption: 14,752 events offered 2.33M, released
139k.

### Why it lost

| cause | USD |
|---|---:|
| Settlement (57 won / 65 lost) | −$1,358.88 |
| Exit vs basis | −$921.16 |
| Fees | **+$381.75** (maker rebate income) |

**What is established:** the entry band [0.40, 0.65] sits where the
fair-value study found mean(payout − price) indistinguishable from
zero, so entries there carry no demonstrated edge and spread is paid on
the way out. **The band is not being retuned against this window.**

## 5. Inspectable lifecycle

Desk tab → *Inspectable lifecycles*. Sixteen conditions: the **six
worst** and six best settled by realised P&L, plus the four most
active. Range −$244.47 to +$337.25. **Losers are in by construction**
and the selection rule is printed above the table.

## 6. Demonstrated / unvalidated / next gate

**Demonstrated**
- Live market data → our decision → risk check → recorded refusal, continuously, unattended
- Order state machine: proposed / resting / partial / filled / expired, with full transition history
- Execution model that refuses a touch and requires a print, with a consumption ledger
- Per-leg inventory, cost basis, fees, realised P&L, settlement, and a ledger identity that reconciles to −0.0
- Loss attribution by cause; stranded capital shown, not dropped
- Mode separation; no combined live+replay total exists anywhere in the code

**Unvalidated**
- **Our execution.** The replay tape is Ferrari's. We were never in the queue. Every fill is an assumption under queue_share.
- **Profitability.** The only measured result is a loss.
- **Continuous learning.** One curve was fitted by a script that a human ran. No training service runs on a schedule.
- **Capacity / institutional scale.** `NOT_IDENTIFIED`, and no multiple of a small-order result is offered.

**Next capital gate:** a bounded funded pilot is the only way to
measure queue position at placement, book depth at our price, and
whether a cancellation was ours or the venue's — the three
measurements that would turn `P_FILL` from `NOT_IDENTIFIED` into a
number. **Not requested in this directive and not requested here.**

## 7. The one remaining deployment action

The **live desk loop is not deployed.** The engine, its tests and the
read surface are in the API release; the loop that steps it against
incoming data needs a process, and the only processes that exist are
`sportsassets-api` and `sportsassets-workers`. **`sportsassets-workers`
runs the observation collector**, which must not restart before its
fixed stop at 2026-09-24T04:00:00Z.

**Recommended: host the loop in `sportsassets-api` behind an env flag**
— no new service, no new cost, no worker restart, and it ships by the
same isolated `deploy-api-commit` route already used twice today. That
needs no authorization beyond what is in force.

Preserved throughout: collector untouched (worker deploy list
byte-identical across both deploys; `claude/session-njaewf` still at
`7f76fd9`; journal shows no new gap or boot), allowance 0/8 · 2/20 ·
4/40, fixed stop unmoved.
