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

**Window 2026-08-21T14:28:09Z → 2026-09-23T13:07:08Z. 19,890 recorded
Ferrari prints, 1,006 conditions, $100,000 starting capital.**

| | |
|---|---:|
| **Realized P&L** | **−$1,898.29** |
| Fees | **+$381.75** (maker **rebate income**) |
| Residual inventory at cost | $24,530.33 (396 legs, unresolved) |
| Cash | $73,571.39 |
| **Ledger identity** | **RECONCILES, drift −0.0** |

Orders: **687 filled · 529 partial · 782 expired never filled ·
34.4% fill rate.** Consumption ledger: 14,752 prints offered 2.33M,
released 139k — the cap binds hard.

### Why it lost

| cause | USD |
|---|---:|
| Settlement (57 legs won, 65 lost) | **−$1,358.88** |
| Exit vs basis | **−$921.16** |
| Fees | **+$381.75** |
| Stranded capital | $24,530.33 in 396 unresolved legs |

**Diagnosis:** the entry band [0.40, 0.65] sits exactly where the
fair-value work measured **no** edge — that band's mean(payout − price)
interval straddles zero — while the measured edge lives in the tails
this policy does not trade. Entries with no edge, then spread paid on
the way out. Fees are **not** the cause; they are income.

**The band is not being retuned against this window.** Making the
obvious change now, on the same data that suggested it, is how a
replay becomes a curve fit.

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
