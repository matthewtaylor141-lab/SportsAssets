# CONTROLLED DEMONSTRATION — SOFTWARE PROOF, NOT PERFORMANCE

**Excluded from strategy performance.** `counts_toward_strategy_performance: false`, enforced on the book by `bettor_funded_book.classify_book` at the reader every consumer uses — not by this heading.

**Source:** `backend/tests/test_the_funded_lifecycle_demonstration.py`, 7 tests, against an isolated `lifecycle_demo` database.
**Traces:** `research/evidence/lifecycle/CASE_A_PROFITABLE_EXIT.json`, `CASE_B_LOSS_CONTAINMENT.json` — actual venue payloads and ledger readbacks per step.

---

## What is deployed code here, and what is a chosen input

Every decision, order and ledger row below is made by the functions the scheduled lane calls, entered at the top:

```
ext_pinnacle_loop.cycle
  → _funded_service
    → bettor_funded_management.manage
      → select_exit → bettor_mgmt_select.rank_with_hold
      → submit_exit → pmus.submit_fok   (transport substituted)
    → bettor_funded_book.recover / ingest_fills / pnl / exposure
```

The test decides nothing, sizes nothing, prices nothing and books nothing. It supplies **inputs** — a probability row, a bid ladder with depth, and the venue's answers — and those inputs are **chosen, not observed**. No venue and no odds provider is read.

**What it establishes:** the shipped software carries a funded position through a depth-limited partial exit, a partial fill, a redelivered execution, a restart, a late execution arriving on recovery, a completing exit, and a loss-containing exit that leaves inventory behind — and the ledger adds up at every step.

**What it does not establish:** anything about opportunity or profitability. Not that such a contract existed, not that it was priced this way, not that it would have filled.

> **"Production code exercised under controlled inputs" is not "production funded behavior verified."** No funded order has been sent. `FUNDED_EXIT_SUBMISSION_ENABLED` ships `False`; a test reloads the module to prove it, and it is monkeypatched `True` only inside these tests.

---

## Case A — the profitable path, five mechanics on one position

Long 20 @ 0.55 (basis $11.00). Holding valued at 0.50/contract. Book bids 0.72 **for 8 contracts only**.

| Step | Mechanic | Residual | Available to exit | State |
|---|---|---:|---:|---|
| 0 | Entry held | 20.0 | 20.0 | open |
| 1 | **Partial exit** — depth caps the order at 8 of 20; the venue fills **5** | 15.0 | **12.0** | open |
| 2 | **Duplicate delivery** — the same `vx-a1` re-ingested | 15.0 | 12.0 | unchanged |
| 3 | **Restart + late execution** — recovery reads 2 executions, writes 1 | 12.0 | 12.0 | open |
| 4 | Completing exit — the remaining 12 at 0.70 | 0.0 | 0.0 | `EXITED_IN_THE_MARKET` |

**Step 1 is the detail that matters.** 15 are held but only **12 are available**: the 3 unfilled contracts on the live exit order stay *reserved*. That distinction is what stops an oversell, and a lane that read "held" as "sellable" would have sold them twice.

**Step 3, verbatim from the reconciliation:** `executions_read = 2`, `fills_written = 1`. Both executions came back on the recovery read; the already-booked one deduped and only the late 3 were written. Late ingestion and idempotency in one operation.

**Step 1's actual venue payload:** `intent=ORDER_INTENT_SELL_LONG quantity=8 price=0.72`.

### Reconciled accounting, case A

| | |
|---|---:|
| Cost basis | **$11.0000** |
| Exit proceeds (5+3 @ 0.72, 12 @ 0.70) | **$14.1600** |
| Fees (venue's own commission, read on recovery) | **$0.6380** |
| **Realised** | **$2.5220** |
| Closed positions | 1 |

`realised == proceeds − basis − fees` holds to 1e-6. My first expectation here was `> 3.0`, which silently assumed fee-free arithmetic; the identity is the real check.

---

## Case B — loss containment, with inventory left behind

Long 15 @ 0.60 (basis $9.00) into a fallen book. Holding valued at 0.30. Book bids **0.41** for 9 contracts.

The exit realises **−0.19 a contract** and is chosen anyway, because holding is worth less. Depth covers 9 of 15, so **6 contracts remain held** and the position does **not** close.

| | |
|---|---:|
| Selected | `DIRECT_EXIT`, 9 contracts @ wire 0.41 |
| `locks_a_loss` | **true** |
| `depth_limited` | true |
| Residual after | **6.0**, `closed_reason: null` |
| Cost basis | $9.0000 |
| Exit proceeds | $3.6900 |
| **Realised** (`realised_pnl_usd`, books on closure) | **$0.0000** |
| Open-position net cash | **−$5.7100** |
| **Realised on the 9 already sold** | **−$2.0100** |

**Realised is 0 and that is the convention, not a bug.** `FB.realised` filters `closed_at IS NOT NULL`: a result is realised when the *position* closes, not when an exit fill lands. I expected a negative number here and was wrong about the code.

### Two things I published here wrongly, now corrected

This section previously said: *"The slice-level −$1.71 is deliberately not split out. Doing so needs a cost-attribution convention (FIFO or average) between the 9 sold and the 6 held, and this lane declares none."* **Both halves of that were wrong.**

**1 · The lane does declare a convention.** `remaining_basis` already used *average entry cost per contract* to size a void refund. I asserted none existed without checking. `realised_on_sold` now uses that same convention, because a second one on the same position would make the two disagree.

**2 · −$1.71 was the wrong number.** It is the *fee-free* arithmetic, 3.69 − 5.40. The sold quantity carries **$0.30** of fees — all $0.15 of the exit fee, plus 9/15 of the $0.25 entry fee — so the result on the 9 sold is **−$2.01**. My figure understated the loss by exactly the fees I had left out.

### The three figures, separated

| | | |
|---|---:|---|
| Exit proceeds on 9 sold | $3.6900 | what came back |
| Allocated basis (9 × $0.60) | $5.4000 | average entry cost |
| Fees on the sold quantity | $0.3000 | $0.15 exit fee + $0.15 entry-fee share (9/15 of $0.25) |
| **Realised on sold** | **−$2.0100** | proceeds − basis − fees |
| Remaining basis (6 × $0.60) | $3.6000 | still at risk |
| Entry fee on the residual | $0.1000 | paid, not yet attributed to a result |
| Unrealised on the residual | `NOT_IDENTIFIED` | no funded mark source — **not** 0 |

**Net cash is not the partial result, and the gap is arithmetic.** −$5.71 exceeds −$2.01 by **$3.70** = the $3.60 of basis still held **plus** the $0.10 of entry fee sitting on the 6 residual contracts. That is a *two*-term difference; I first wrote it as the remaining basis alone, which misses by that $0.10. `test_net_cash_differs_by_two_terms_not_one_when_fees_exist` exists so it cannot be written that way again.

**Operator view:** the book reads `book_class: CONTROLLED_DEMONSTRATION`, `counts_toward_strategy_performance: false`, and the 6 residual contracts appear as a `RESIDUAL_INVENTORY_STILL_HELD` discrepancy — something to act on, not a rounding error.

---

## Two defects the demonstration found

**1. `locks_a_loss` was never set on the actions this lane can execute.** It was set only on `TAKE_COMPLEMENT`. So `_choose` could never append its "THIS LOCKS A LOSS and is selected anyway" sentence to a `DIRECT_EXIT` or `REDUCE`: a pass could sell at 0.41 on a 0.60 basis, correctly, and nothing an operator could read said a loss was realised. Now set on both, from the **slice** value — not `value_usd`, which carries the retained inventory's unrealised value too.

**2. `manage`'s ranking projection listed the outcome and dropped the reasoning.** `candidates` and `unqualified` now travel with the decision, plus a resolved `selected_candidate`, `selected_locks_a_loss` and `selected_is_depth_limited`.

## And one correction to my own earlier note

I recorded the candidate table's units as "EV_VS_HOLD — incremental relative to HOLD". **Measured:** they are **absolute P&L against basis**, and HOLD is a row *in* the table at its own absolute value — (0.30 − 0.60) × 15 = **−4.50** against `DIRECT_EXIT`'s **−3.66**, a 0.84 margin. Not increments over HOLD.

---

## Exclusion, and where it is enforced

The funded tables have no experiment column — a funded book is keyed by `(account_id, venue)` — so the discipline `bettor_demonstration` applies to the shadow lane by experiment id is applied here on the **account id**:

- `DEMONSTRATION` in the account id ⇒ `book_class: CONTROLLED_DEMONSTRATION`, `counts_toward_strategy_performance: false`.
- A funded book reads **`None`**, not `False`. `None` means *not established here*; reporting `False` would claim a finding this reader has not made.
- `command_center` returns `funded_books` and `demonstration_books` **separately** and no total spans them.

**It is a convention, not a permission,** and the module says so: nothing stops a caller writing a demonstration row under a production account id. What it does is make an unlabelled demonstration *visible* rather than silently countable.
