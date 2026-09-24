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

`—` means not applicable. `✗` means genuinely not done. **`✓*` in the OBS
column means CONTROLLED observation** -- the behaviour was exercised through
the deployed machinery with inputs this repository supplied. It is not
production observation and must not be read as one. A row is only complete
when OBS is filled, or when the row records exactly why OBS is
unreachable.

Last updated 2026-09-24T02:2xZ, against the run-21 production read
(02:03–02:08Z). Deployed API commit: see §0.

---

## §0 · Deployed identities

| what | value | how verified |
|---|---|---|
| default branch | `claude/session-njaewf` | `git remote show origin` |
| work branch | `claude/command-center` | 153 commits ahead of default, 6 behind |
| API service | `sportsassets-api` (`srv-d9gcv6urnols73ce6er0`) | render-ops deploys |
| API commit deployed | `098e94c` (dispatched 02:23:57Z; read back by run 22). `e0929fd` was the commit run 21 read at 02:03–02:08Z. The two commits after `098e94c` change only a docstring, a workflow comment and tests, so nothing deployable is missing from the service | render-ops deploy-api-commit |
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
| 1.4 | exact contract mapping | ✓ | ✓ | ✓ | ✓ | **run 21, 02:08:18Z**: 4,000 open venue markets considered (was 0 before the sport-label fix), 46 Pinnacle events attempted, **2 mapped exactly**, 41 `NO_VENUE_CONTRACT_FOR_EVENT`, 1 `VENUE_MAPPING_AMBIGUOUS`. The 2 mapped events are established by the refusal that follows mapping (see 1.5) |
| 1.5 | executable same-venue price AND depth | ✓ | ✓ | ✓ | ⏳ | the stage now RUNS on production data and refuses: `NO_CONTEMPORANEOUS_VENUE_QUOTE 2` at 02:08:18Z, reached only after a successful mapping. `VENUE_ASK_HAS_NO_DEPTH` and `VENUE_QUOTE_STALE` did NOT fire, so the cause is one of exactly three: no slug on the market row, the book read raising or timing out, or the venue returning an error. The loop collapsed those three into one counter; they are now three named counters (`VENUE_MARKET_ROW_HAS_NO_SLUG`, `VENUE_BOOK_READ_FAILED`, `VENUE_BOOK_READ_RETURNED_ERROR`) |
| 1.6 | applicable fees, never defaulted | ✓ | ✓ | ✓ | ⏳ | production `bettor_fee_schedule.LATEST` |
| 1.7 | estimated edge = p − ask − fee | ✓ | ✓ | ✓ | ⏳ | asserted in test; no bid anywhere in the module |
| 1.8 | period / line / settlement agreement | ✓ | ✓ | ✓ | ✓ | refuses; venue settlement rule NOT attested → counted |
| 1.9 | overtime / draw / push / void rules | ✓ | ✓ | ✓ | ✓ | four rules reported SEPARATELY; soccer leaves 3 unmet, baseball 2 (draw n/a) |
| 1.10 | coverage scoped to the actual response | ✓ | ✓ | ✓ | ✓ | **corrected**: `PINNACLE_ABSENT` no longer a permanent claim |
| 1.11 | stale inputs rejected | ✓ | ✓ | ✓ | ✓ | 30 s rule. Measured three times on the bulk endpoint: 32.7 s (01:06:57Z, refused), 36.2 s (01:32:20Z, refused), **28.8 s (02:03:15Z, n=20, inside the rule)**. The freshness rule is therefore not a permanent blocker — it straddles the limit. The limit was not moved |
| 1.12 | labelled, not a proprietary model | ✓ | ✓ | ✓ | ✓ | tile text read off production 00:00:49Z |
| 1.13 | refusals persisted and inspectable | ✓ | ✓ | ✓ | ✓ | heartbeat read at 02:08:18Z, a real cycle: `state LIVE`, `markets 4000`, `elapsed_s 3.67`, `evaluated 0`, `written 0`, and the four refusals that account for all 46 events: `NO_VENUE_CONTRACT_FOR_EVENT 41`, `NO_PINNACLE_ON_EVENT 2`, `NO_CONTEMPORANEOUS_VENUE_QUOTE 2`, `VENUE_MAPPING_AMBIGUOUS 1` |

## §2 · Position management (frozen baseline)

| # | requirement | IMPL | CONN | DEPL | OBS | evidence / blocker |
|---|---|---|---|---|---|---|
| 2.1 | pair when combined cost ≤ $0.91 | ✓ | ✓ | ✓ | ✓ | `MANAGEMENT_PAIR_091_STOP_16_V1`, 126 historical positions |
| 2.2 | 16% loss exit, second half only | ✓ | ✓ | ✓ | ✗ | no progress feed → `second_half_loss_exit` reads UNAVAILABLE |
| 2.3 | trigger reported separately from modelled loss | ✓ | ✓ | ✓ | ✓ | `realised_vs_trigger()` |
| 2.4 | observed event progress, never wall clock | ✓ | ✗ | ✓ | ✗ | `bettor_progress_feed` refuses `wall_clock` BY SOURCE NAME; **no provider access** |
| 2.5 | partial fills | ✓ | ✓ | ✓ | ✓* | controlled: remainder stays working, PARTIALLY_FILLED |
| 2.6 | cancellation acknowledgement | ✓ | ✓ | ✓ | ✓* | controlled: CANCEL_PENDING → CANCELLED |
| 2.7 | late fill during cancellation | ✓ | ✓ | ✓ | ✓* | controlled: a CANCEL_PENDING order still fills |
| 2.8 | residual inventory | ✓ | ✓ | ✓ | ✓* | `residual()`, capped at place AND fill time |
| 2.9 | settlement | ✓ | ✓ | ✓ | ✓ | step 8, payout read nowhere earlier |
| 2.10 | capital release only on the release mechanism | ✓ | ✓ | ✓ | ✓* | controlled: paired inventory is NOT cash until settle |
| 2.11 | order-concurrency policy preserved | ✓ | ✓ | ✓ | ✓* | one order; cancel then WAIT_CANCEL_ACK |

## §3 · Timestamp integrity and accounting

| # | requirement | IMPL | CONN | DEPL | OBS | evidence / blocker |
|---|---|---|---|---|---|---|
| 3.1 | source + receipt preserved unchanged | ✓ | ✓ | ✓ | ✓ | migration 104; `detected_ts` no longer max()'d |
| 3.2 | availability stored separately | ✓ | ✓ | ✓ | ✓ | `available_at` |
| 3.3 | runtime decision time recorded | ✓ | ✓ | ✓ | ⏳ | `BASIS_RUNTIME` deployed; **0 prospective rows carry it yet** (147 positions at 02:05:46Z, 0 prospective-capable). Every prospective row still in the ledger predates migration 104 |
| 3.4 | order-creation time recorded | ✓ | ✓ | ✓ | ⏳ | `rn1x_orders.created_at_runtime` |
| 3.5 | no fill against pre-creation prints | ✓ | ✓ | ✓ | ✓ | run-loop floor + `rn1x_fills` trigger |
| 3.6 | prospective RN1 records audited and reclassified | ✓ | ✓ | ✓ | ✓ | **147 positions at 02:05:46Z: 0 prospective-capable, 130 backdated** (126 historical + all 4 prospective), 17 `REPLAY_AT_AVAILABILITY`. Badge `CHECK` |
| 3.7 | atomic persistence | ✓ | ✓ | ✓ | ✓* | controlled: a failed transaction leaves no position |
| 3.8 | idempotency | ✓ | ✓ | ✓ | ✓* | migration 105; a replayed cycle duplicates nothing |
| 3.9 | single-writer ownership | ✓ | ✓ | ✓ | ⏳ | **this row was wrong and is corrected.** It claimed an advisory lock per loop; `ext_pinnacle_loop` and `rn1x_model_loop` took **no lock at all** and acquired a pooled connection per cycle, which cannot hold a session-scoped lock. Both now contend for their own key (…034, …035) on one connection held for the loop's life, re-asking every 60 s so a standby can take over. A twelfth tile, `writer_ownership`, reads `pg_locks` and says which loops hold theirs; the key reassembly was verified against a real server. `tests/test_single_writer_ownership.py` pins all four keys distinct, the one-connection discipline and the retry. OBS awaits the next authenticated read |
| 3.10 | controlled restart recovery | ✓ | ✓ | ✓ | ✓* | controlled: fresh connection, identical state, no duplication |

## §4 · Continuous learning

| # | requirement | IMPL | CONN | DEPL | OBS | evidence / blocker |
|---|---|---|---|---|---|---|
| 4.1 | targets explicitly named | ✓ | ✓ | ✓ | ✓ | `bettor_model_inventory`: T_COMPLETE / T_CLEARS / T_SETTLEMENT |
| 4.2 | RN1-complement ≠ settlement ≠ p_fill | ✓ | ✓ | ✓ | ✓ | `ENTRY_REQUIRES = T_SETTLEMENT`; refuses on target before metrics |
| 4.3 | scheduled preparation → fit → predict → join → evaluate | ✓ | ✓ | ✓ | ✓ | LIVE 02:08:18Z: 5,966 closed rows fitted, 299 open rows predicted, **157 written and 142 already present** in the same cycle, **316 predictions in the ledger**, 0 joined, and stage 5 refusing by name: `TOO_FEW_JOINED_PREDICTIONS_TO_EVALUATE` (floor 50) |
| 4.4 | frozen baseline, challengers separate | ✓ | ✓ | ✓ | ✓ | no promotion path in the loop |
| 4.5 | versions, sample sizes, assumptions recorded | ✓ | ✓ | ✓ | ✓ | `dataset_sha 68ac9f00dc68c2d2`, `model_version 2.68ac9f00`, n=5,966, base rate 0.31646, 1,888 positives, `unmatched_to_source_fill 0`. In-sample log loss 0.5670 against a 0.6242 baseline, **carried with `IS_IN_SAMPLE`: a fit diagnostic, not performance** |
| 4.6 | comparisons are not called training | ✓ | — | — | ✓ | corrected in an earlier round |

## §5 · Command centre

| # | requirement | IMPL | CONN | DEPL | OBS | evidence / blocker |
|---|---|---|---|---|---|---|
| 5.1 | authenticated interface verified | ✓ | ✓ | ✓ | ✓ | command-verify run 18, 8 statuses |
| 5.2 | three lanes visibly separate | ✓ | ✓ | ✓ | ✓ | distinct experiment ids and tiles |
| 5.3 | odds → probability → price → cost → edge | ✓ | ✓ | ✓ | ⏳ | `external/trace/{id}` answers, but **no row exists to trace**: nothing has reached scoring, so probability/price/cost/edge have never been populated in production. Read at 02:08:18Z: "no valuation rows yet — the census above says why" |
| 5.4 | why bought / held / paired / exited / refused | ✓ | ✓ | ✓ | ✓ | the 02:08:18Z heartbeat names every refusal of a real cycle with counts; the position trace names the buy and the pairing |
| 5.5 | resting orders, partials, cancels, inventory | ✓ | ✓ | ✓ | ✓ | LIVE 02:08:18Z: **386 modelled orders** across both lanes |
| 5.6 | realised + unrealised P&L, fees, rebates | ✓ | ✓ | ✓ | ✓ | LIVE 02:08:18Z: **143 settled positions**; unrealised NOT_IDENTIFIED with the reason |
| 5.7 | accounting health, decision timestamps, restart | ✓ | ✓ | ✓ | ✓ | `clock-audit` + `accounting_health` |
| 5.8 | learning activity and blockers | ✓ | ✓ | ✓ | ✓ | `learning_evaluation` tile |
| 5.9 | intermittent page failure investigated | ✓ | ✓ | ✓ | ⏳ | cause narrowed to a transient Netlify CDN fault on a forced rewrite (NOT size, NOT our origin, NOT the rule); the shell now names the failed file instead of rendering blank |

## §6 · Acceptance standards

| # | requirement | state | evidence |
|---|---|---|---|
| 6.1 | controlled: BUY | ✓ | `tests/test_controlled_acceptance.py`, 17 tests, all through the deployed machinery |
| 6.2 | controlled: pairing | ✓ | `tests/test_controlled_acceptance.py`, 17 tests, all through the deployed machinery |
| 6.3 | controlled: second-half loss exit | ✓ | `tests/test_controlled_acceptance.py`, 17 tests, all through the deployed machinery |
| 6.4 | controlled: no fill | ✓ | `tests/test_controlled_acceptance.py`, 17 tests, all through the deployed machinery |
| 6.5 | controlled: partial fill | ✓ | `tests/test_controlled_acceptance.py`, 17 tests, all through the deployed machinery |
| 6.6 | controlled: cancellation race | ✓ | `tests/test_controlled_acceptance.py`, 17 tests, all through the deployed machinery |
| 6.7 | controlled: settlement | ✓ | `tests/test_controlled_acceptance.py`, 17 tests, all through the deployed machinery |
| 6.8 | controlled: restart recovery | ✓ | `tests/test_controlled_acceptance.py`, 17 tests, all through the deployed machinery |
| 6.9 | production: fresh inputs + runtime decision + persisted + trace | **PARTIAL** | run 21, job 107462533297, 02:03–02:08Z, against API deploy `e0929fd`. **fresh inputs ✓** — 20/20 EPL events with Pinnacle h2h, quote age 28.8 s, credits 8,075,605/6,924,395, `key_value_returned false`. **actual runtime decisions ✓** — one real cycle, `elapsed_s 3.67`, 4,000 markets considered, 46 events decided, all refused by name. **persisted ✓** — 316 predictions, 386 orders, 143 settled. **subsequent processing without duplicate accounting ✓** — the same cycle wrote 157 predictions and recognised 142 as `already_present`. **authenticated trace ✓** — 11 tiles, clock audit, one position trace end to end. **Still missing: an external valuation row.** Nothing reached scoring, so probability → cost → edge → decision has never been populated in production, and no qualifying opportunity has been found *or* ruled out |
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
