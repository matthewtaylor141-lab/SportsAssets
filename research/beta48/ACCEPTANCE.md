# THE ACCEPTANCE CHECKLIST — one list, the whole system

Maintained in place. Every row carries the four states separately, because
they are four different claims and conflating them is how this delivery
has previously overrun its evidence:

| state | means |
|---|---|
| **IMPL** | the code exists and is unit-tested |
| **CONN** | a real production caller invokes it (not a test, not a script) |
| **DEPL** | it is running in the deployed image on the target service |
| **OBS** | its behaviour has been observed on production data, with a timestamp |

`—` means not applicable. `✗` means genuinely not done. A row is only
complete when OBS is filled, or when the row records exactly why OBS is
unreachable.

Last updated 2026-09-24T01:1xZ. Deployed API commit: see §0.

---

## §0 · Deployed identities

| what | value | how verified |
|---|---|---|
| default branch | `claude/session-njaewf` | `git remote show origin` |
| work branch | `claude/command-center` | 153 commits ahead of default, 6 behind |
| API service | `sportsassets-api` (`srv-d9gcv6urnols73ce6er0`) | render-ops deploys |
| API commit deployed | `f3ba6a0` (dispatched 01:12:01Z, HTTP 201, `dep-…`) | render-ops deploy-api-commit |
| worker service | `sportsassets-workers`, **untouched** | deploys BEFORE == AFTER on every API deploy |
| collector | untouched; no worker deploy in this session | same |
| damaged account | `acct_fc2d773a2afa4851` paused, ACCOUNTING_UNCERTAIN | not read or written by anything added here |
| odds credential | `EDGE_ODDS_API_KEY` on `sportsassets-api` | run 35941026587, PUT HTTP 200, key NAME in after-list only |

## §1 · Independent shadow entries (Pinnacle valuation)

| # | requirement | IMPL | CONN | DEPL | OBS | evidence / blocker |
|---|---|---|---|---|---|---|
| 1.1 | credential on the required service | ✓ | ✓ | ✓ | ✓ | run 35941026587; PUT 200; names only |
| 1.2 | running process can retrieve odds | ✓ | ✓ | ✓ | ✓ | probe 01:06:57Z: `retrieved true`, 20/20 EPL events with Pinnacle h2h, credits 8,071,071 |
| 1.3 | complete-outcome de-vigging | ✓ | ✓ | ✓ | ✓ | 42 outcomes priced, run 35935500538; `OUTCOME_SET_INCOMPLETE` refuses subsets |
| 1.4 | exact contract mapping | ✓ | ✓ | ✓ | ⏳ | `bettor_venue_mapping`; conservative normalisation; Manchester collision refused |
| 1.5 | executable same-venue price AND depth | ✓ | ✓ | ✓ | ⏳ | `pmus.book_read` + `bettor_book_snapshot` displayed depth |
| 1.6 | applicable fees, never defaulted | ✓ | ✓ | ✓ | ⏳ | production `bettor_fee_schedule.LATEST` |
| 1.7 | estimated edge = p − ask − fee | ✓ | ✓ | ✓ | ⏳ | asserted in test; no bid anywhere in the module |
| 1.8 | period / line / settlement agreement | ✓ | ✓ | ✓ | ✓ | refuses; venue settlement rule NOT attested → counted |
| 1.9 | overtime / draw / push / void rules | ✗ | — | — | — | **see §1a** |
| 1.10 | coverage scoped to the actual response | ✓ | ✓ | ✓ | ✓ | **corrected**: `PINNACLE_ABSENT` no longer a permanent claim |
| 1.11 | stale inputs rejected | ✓ | ✓ | ✓ | ✓ | 30 s rule; EPL measured 32.7 s → refused |
| 1.12 | labelled, not a proprietary model | ✓ | ✓ | ✓ | ✓ | tile text read off production 00:00:49Z |
| 1.13 | refusals persisted and inspectable | ✓ | ✓ | ✓ | ⏳ | scored refusals on the row; pre-scoring refusals in the cycle heartbeat |

## §2 · Position management (frozen baseline)

| # | requirement | IMPL | CONN | DEPL | OBS | evidence / blocker |
|---|---|---|---|---|---|---|
| 2.1 | pair when combined cost ≤ $0.91 | ✓ | ✓ | ✓ | ✓ | `MANAGEMENT_PAIR_091_STOP_16_V1`, 126 historical positions |
| 2.2 | 16% loss exit, second half only | ✓ | ✓ | ✓ | ✗ | no progress feed → `second_half_loss_exit` reads UNAVAILABLE |
| 2.3 | trigger reported separately from modelled loss | ✓ | ✓ | ✓ | ✓ | `realised_vs_trigger()` |
| 2.4 | observed event progress, never wall clock | ✓ | ✗ | ✓ | ✗ | `bettor_progress_feed` refuses `wall_clock` BY SOURCE NAME; **no provider access** |
| 2.5 | partial fills | ⏳ | | | | |
| 2.6 | cancellation acknowledgement | ⏳ | | | | |
| 2.7 | late fill during cancellation | ⏳ | | | | |
| 2.8 | residual inventory | ⏳ | | | | |
| 2.9 | settlement | ✓ | ✓ | ✓ | ✓ | step 8, payout read nowhere earlier |
| 2.10 | capital release only on the release mechanism | ⏳ | | | | |
| 2.11 | order-concurrency policy preserved | ⏳ | | | | |

## §3 · Timestamp integrity and accounting

| # | requirement | IMPL | CONN | DEPL | OBS | evidence / blocker |
|---|---|---|---|---|---|---|
| 3.1 | source + receipt preserved unchanged | ✓ | ✓ | ✓ | ✓ | migration 104; `detected_ts` no longer max()'d |
| 3.2 | availability stored separately | ✓ | ✓ | ✓ | ✓ | `available_at` |
| 3.3 | runtime decision time recorded | ✓ | ✓ | ✓ | ⏳ | `BASIS_RUNTIME`; 0 rows carry it yet |
| 3.4 | order-creation time recorded | ✓ | ✓ | ✓ | ⏳ | `rn1x_orders.created_at_runtime` |
| 3.5 | no fill against pre-creation prints | ✓ | ✓ | ✓ | ✓ | run-loop floor + `rn1x_fills` trigger |
| 3.6 | prospective RN1 records audited and reclassified | ✓ | ✓ | ✓ | ✓ | **131 positions: 0 prospective-capable, 130 backdated** (01:09:28Z) |
| 3.7 | atomic persistence | ✓ | ✓ | ✓ | ⏳ | one transaction in `bettor_rn1x_store` |
| 3.8 | idempotency | ✓ | ✓ | ✓ | ⏳ | migration 105 one-row-per-observation |
| 3.9 | single-writer ownership | ⏳ | | | | advisory lock per loop |
| 3.10 | controlled restart recovery | ⏳ | | | | |

## §4 · Continuous learning

| # | requirement | IMPL | CONN | DEPL | OBS | evidence / blocker |
|---|---|---|---|---|---|---|
| 4.1 | targets explicitly named | ✓ | ✓ | ✓ | ✓ | `bettor_model_inventory`: T_COMPLETE / T_CLEARS / T_SETTLEMENT |
| 4.2 | RN1-complement ≠ settlement ≠ p_fill | ✓ | ✓ | ✓ | ✓ | `ENTRY_REQUIRES = T_SETTLEMENT`; refuses on target before metrics |
| 4.3 | scheduled preparation → fit → predict → join → evaluate | ⏳ | | | | **the remaining gap** |
| 4.4 | frozen baseline, challengers separate | ✓ | ✓ | ✓ | ✓ | no promotion path in the loop |
| 4.5 | versions, sample sizes, assumptions recorded | ✓ | ✓ | ✓ | ⏳ | migration 102 ledger |
| 4.6 | comparisons are not called training | ✓ | — | — | ✓ | corrected in an earlier round |

## §5 · Command centre

| # | requirement | IMPL | CONN | DEPL | OBS | evidence / blocker |
|---|---|---|---|---|---|---|
| 5.1 | authenticated interface verified | ✓ | ✓ | ✓ | ✓ | command-verify run 18, 8 statuses |
| 5.2 | three lanes visibly separate | ✓ | ✓ | ✓ | ✓ | distinct experiment ids and tiles |
| 5.3 | odds → probability → price → cost → edge | ✓ | ✓ | ✓ | ⏳ | `external/trace/{id}` |
| 5.4 | why bought / held / paired / exited / refused | ✓ | ✓ | ✓ | ⏳ | refusal lists + `last_cycle` heartbeat |
| 5.5 | resting orders, partials, cancels, inventory | ⏳ | | | | |
| 5.6 | realised + unrealised P&L, fees, rebates | ⏳ | | | | |
| 5.7 | accounting health, decision timestamps, restart | ✓ | ✓ | ✓ | ✓ | `clock-audit` + `accounting_health` |
| 5.8 | learning activity and blockers | ✓ | ✓ | ✓ | ✓ | `learning_evaluation` tile |
| 5.9 | intermittent page failure investigated | ⏳ | | | | observed 22:34:18Z: `/app.js` + `/core.js` 503/0 bytes |

## §6 · Acceptance standards

| # | requirement | state | evidence |
|---|---|---|---|
| 6.1 | controlled: BUY | ⏳ | |
| 6.2 | controlled: pairing | ⏳ | |
| 6.3 | controlled: second-half loss exit | ⏳ | |
| 6.4 | controlled: no fill | ⏳ | |
| 6.5 | controlled: partial fill | ⏳ | |
| 6.6 | controlled: cancellation race | ⏳ | |
| 6.7 | controlled: settlement | ⏳ | |
| 6.8 | controlled: restart recovery | ⏳ | |
| 6.9 | production: fresh inputs + runtime decision + persisted + trace | ⏳ | |
| 6.10 | funded execution unavailable AT THE ADAPTER | ✓ | `tests/test_ext_shadow_cannot_fund.py`, AST-verified; gate authorizes first, denial raises |

---

## §1a · Overtime, draw, push and void — the honest position

These are four distinct settlement questions and only one of them is
currently answerable from our data:

| rule | Pinnacle side | venue side | status |
|---|---|---|---|
| **draw** | 3-way h2h names it explicitly; de-vig covers the complete set | the venue's binary "will X beat Y" resolves draw as NO | **asymmetric.** A 3-outcome de-vig gives p(home win); the venue contract pays on "home win" and a draw is a loss. These agree ONLY if the venue contract is genuinely 3-way-equivalent, which is not stated in `markets`. |
| **overtime** | soccer full-time = regulation 90 + stoppage; MLB = includes extra innings | not stated | not established |
| **push** | a tie at the line voids on spreads/totals | n/a for h2h | out of scope: only h2h is priced |
| **void** | abandoned / postponed refunds | not stated | not established |

So the only sound treatment is a refusal that names the missing input,
which is what `bettor_venue_settlement` does. Asserting agreement here
would make every downstream edge unfalsifiable, and for league fixtures it
would be right often enough never to look wrong.
