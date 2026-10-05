# BETTOR 95% readiness matrix — frozen candidate

Candidate: `claude/int-wave1` @ the commit that adds this file (parent `f0d4af0`).
Production today: API = workers = `7cb1979`; Command frontend `867e838`.
Rule: GREEN = exact SHA + named passing tests + migration applied + deployed + production readback + no safety gate weakened. Anything short of that is RED. DEFERRED_FORWARD_EVIDENCE only where the remaining proof is elapsed market time.

| # | Category | Status | Evidence on this candidate | What is missing | Acceptance test / readback |
|---|---|---|---|---|---|
| 1 | Management epoch ($500,000 @ 2026-10-05 00:00 ET) | RED (deploy pending) | release `fd2bff3`: backend-tests run 37364658747 ✓, capital-critical run 37364658685 ✓; `tests/test_paper_management_epoch.py` 16 ✓ | API+workers deploy (runs 37370800006 / 37370802493 queued: no hosted runner) and readback | `api_readback.json` → `/api/command/equity/live` `paper.management.status == OK`, `opening.identity_holds`, `ledger_reconciliation.reconciles`; Audrey `MANAGEMENT_*` checks pass |
| 2 | Mark freshness ≥95% markable, 100% classified | RED (deploy pending) | `claude/freshness` a6ac490 merged; migration 270; `test_paper_mark_freshness_classifier.py` 27 ✓, `test_paper_mark_refresh_coverage.py` 11 ✓ | deploy + steady-state readback | `paper.freshness.fresh_rate ≥ 0.95`, `open_positions == classified` |
| 3 | Xavier management packet (no stale-probability management) | RED (deploy pending) | `test_xavier_management_packet.py` 3 ✓ | deploy + a production review row with packet or `XAVIER_MANAGEMENT_PACKET_INCOMPLETE` | `/api/command/paper/freshness` rows; `paper_management_refusals` |
| 4 | Coverage first-loss census + NCAAF / basketball / Serie B / UNL | RED (deploy pending) | `claude/coverage` a73a7d1 merged; `test_coverage_first_loss_census.py` 16 ✓, `test_ncaaf_native_seed_identity.py` 4 ✓ | deploy + census readback; basketball money line remains a SOFTWARE capability gap (no winner family) | `/api/command/coverage/first-loss?hours=24` |
| 5 | Execution / capital gating (depth, settlement, EV≤0 → CASH/WAIT) | RED (deploy pending) | `claude/exec-gating` merged; `test_capital_eligibility.py` 32 ✓ | deploy + decisions carrying `CASH_WAIT_TOTAL_EXECUTABLE_EV_NOT_POSITIVE` / capital gates | `paper_decisions` refusal mix via `/api/command/paper/derek` |
| 6 | Kalshi venue-neutral routing | BLOCKED (external) | routing is Polymarket-only; `KALSHI_BOOK_READ_PATH_BLOCKED_IN_PRODUCTION` | Kalshi orderbook read path: no production caller, `KALSHI_ENV` unset, no Kalshi catalogue | owner/venue provisioning |
| 7 | PAPER turnaround lifecycle (demotion after losses) | RED (deploy pending) | migration 290; `test_strategy_lifecycle.py` 40 ✓ | deploy + first lifecycle evaluation events | `/api/command/paper/turnaround` |
| 8 | ADRIANA (8th agent, SHADOW/PAPER) | RED (deploy pending) | migration 265; `test_adriana_agent.py` 19 ✓, `test_adriana_arb_engine.py` 97 ✓ | deploy + census pass readback | `adriana_readback.json` seat deployed + census pass |
| 9 | ARCHER rename (EDDIE alias kept) | RED (deploy pending) | migrations 266, 302; 1845 ✓ / 1 pre-existing ✗ (lane run); frontend `claude/archer-ui` 4bdfcb9 558 ✓ | deploy + `/api/command/archer` readback + frontend publish | `api_readback.json` `/api/command/archer` |
| 10 | R30 tails (agents / execution / risk) | RED (deploy pending) | migrations 300, 301; receipt `docs/R30_TAILS_RECEIPT.md` (59 included / 14 reimplemented / 5 rejected) | deploy | `/api/command/execution-calibration`, scorecards |
| 11 | Profitability OS (15 components) | RED (deploy pending) | `test_pos_os_*` 34 ✓ | deploy + section readback | `/api/command/profitability/os` |
| 12 | Command frontend | GREEN (current) / RED (epoch + ARCHER view) | `867e838` live, readback run 37358399674 ✓ | publish epoch view `5511498` + ARCHER UI after backend | hqprod readback |
| 13 | Full exact-SHA CI on this candidate | RED | capital-critical dispatched on int-wave1 | hosted runners not acquiring jobs since ~20:05 UTC | backend-tests + capital-critical green on the exact SHA |
| 14 | Realized profitability / promotion evidence | DEFERRED_FORWARD_EVIDENCE | lifecycle + POS measure it | elapsed forward market time | `/api/command/paper/turnaround` forward windows |

CURRENT_RED_COUNT: 12 (all code-complete; 11 wait on the hosted-runner blocker for CI/deploy/readback, 1 on deploy of the frontend view).
External: Kalshi read path; GitHub hosted runners (owner billing/limits check).
Safety: SMALL LIVE SHADOW; no cap, scale, allowlist, EV/settlement/freshness/risk weakening; every new gate only refuses or shrinks.
