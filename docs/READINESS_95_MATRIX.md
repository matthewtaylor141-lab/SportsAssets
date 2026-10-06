# BETTOR 95% readiness matrix — live state

As of 2026-10-05 23:47Z. Production: API = workers = `e54ee95` (ALIGNED, migrations through `302_archer_r30_tails.sql`, 213 applied); Command frontend `4bdfcb9` (Netlify deploy `6ac42b233fed21000856c8d0`).
Evidence: hqprod readback run 37389929039 (`claude/site-verify-evidence:evidence/hqprod/20261005T234733Z_run37389929039/`), research-sql runs 37384642567 / 37384839810 / 37385477712 / 37389862924.
Rule: GREEN = exact SHA + named passing tests + migration applied + deployed + production readback + no safety gate weakened. Anything short of that is RED or BLOCKED with the reason. DEFERRED_FORWARD_EVIDENCE only where the remaining proof is elapsed market time.

| # | Category | Status | Evidence | What is missing |
|---|---|---|---|---|
| 1 | Management epoch ($500,000 @ 2026-10-05 00:00 ET) | GREEN | `tests/test_paper_management_epoch.py` 16 ✓ (capital-critical); `equity/live paper.management` status OK, opening identity holds, ledger reconciles; equity $479,006.66, total P&L −$20,993.34 (realized −$11,642.28, unrealized −$9,351.06) | — |
| 2 | Mark freshness ≥95% markable, 100% classified | BLOCKED (external) | classified 208/208 ✓. Fresh rate 0.13 → **0.56** after `e54ee95` (`test_paper_mark_refresh_cooldown.py` 9 ✓, capital-critical run 37385385898 ✓): refresh runs 0–1 → 5–11 reads OK; gate refusals 1,405 → 39; 429 rate 34% → 17% | The authenticated REST book endpoint is rate-limited (Retry-After 7–10 s; 429s seen at 0.23 req/s) below what ~100 held markets need inside 300 s. The retail websocket path is wired (`HELD_MARK_STREAM`) but needs a dedicated market-data key on `sportsassets-workers` (`PMUS_MD_KEY_ID` / `PMUS_MD_SECRET_KEY`; the funded key is refused by the owner topology rule). The institutional stream was measured and rejected as a mark source: 14% of comparable samples disagree on a best price, and it covers 33 of 96 held markets |
| 3 | Xavier management packet (no stale-probability management) | GREEN | `test_xavier_management_packet.py` 3 ✓; production reviews record `WAITING_FOR_FRESH_EVIDENCE` with `ev_is_current: false`, and `XAVIER_MANAGEMENT_PACKET_INCOMPLETE` refusals are present | — |
| 4 | Coverage first-loss census | GREEN | `test_coverage_first_loss_census.py` 16 ✓, `test_ncaaf_native_seed_identity.py` 4 ✓; `/api/command/coverage/first-loss` OK: 229 provider events, 113 entered, first loss by stage, 0 UNCLASSIFIED | Basketball money line is still a software capability gap (no winner family); it is named in the census, not hidden |
| 5 | Execution / capital gating | GREEN | `test_capital_eligibility.py` 32 ✓; in production the allocation rail refuses `STRATEGY_OPEN_POSITIONS_CANNOT_BE_FRESHLY_MANAGED` / `STALE_MANAGEMENT_RATE_ABOVE_THE_DECLARED_THRESHOLD`; `/api/command/execution-calibration` OK, labelled as PAPER simulation and not proof of live execution | — |
| 6 | Kalshi venue-neutral routing | BLOCKED (external) | `KALSHI_BOOK_READ_PATH_BLOCKED_IN_PRODUCTION`; ADRIANA census reports `NO_KALSHI_BOOK_SOURCE` | Kalshi orderbook read path / provisioning |
| 7 | PAPER turnaround lifecycle | GREEN | migration 290; `test_strategy_lifecycle.py` 40 ✓; `/api/command/paper/turnaround` OK (rules sha `794c47f0…`): DEREK_ENTRY_POLICY_V2 and PINNACLE_EXPLORATION_PAPER demoted to SHADOW_ONLY by `LOSS_BUDGET_SHADOW`; caps unchanged | Promotion back needs forward evidence (row 14) |
| 8 | ADRIANA (eighth agent, SHADOW/PAPER) | GREEN | migration 265; `test_adriana_agent.py` 19 ✓, `test_adriana_arb_engine.py` 97 ✓; seat deployed, WORKING; census OK; Kalshi and Polymarket international honestly UNAVAILABLE | — |
| 9 | ARCHER rename (EDDIE alias kept) | GREEN | migrations 266, 302; `/api/command/archer` → ARCHER / HEAD_OF_EXECUTION with measured metrics; frontend `4bdfcb9` live, 9 views 0 errors; `/eddie` alias kept | — |
| 10 | R30 tails | GREEN | migrations 300, 301 applied; `docs/R30_TAILS_RECEIPT.md`; `/api/command/execution-calibration` 200 OK | — |
| 11 | Profitability OS (15 components) | GREEN | `test_pos_os_*` 34 ✓; `/api/command/profitability/os` OK, 15/15 sections OK, SHADOW_NO_AUTHORITY | — |
| 12 | Command frontend (epoch + ARCHER views) | GREEN | `5511498` → `4bdfcb9` published fast-forward; readback run 37389929039 clean (desktop 1440 and mobile 390) | — |
| 13 | Exact-SHA CI on the released candidate | GREEN | `e54ee95`: backend-tests 37385385847 ✓, capital-critical 37385385898 ✓, commit-guard ✓, engine-diagnostic ✓ | — |
| 14 | Realized profitability / promotion evidence | DEFERRED_FORWARD_EVIDENCE | lifecycle and POS measure it | elapsed forward market time |

CURRENT_RED_COUNT: 0 code-controlled RED. BLOCKED (external): 2 — mark freshness ≥95% (dedicated market-data key) and Kalshi read path. DEFERRED_FORWARD_EVIDENCE: 1.
Safety: SMALL LIVE SHADOW; PAPER only; no change to the cap, scale, allowlists or EV/settlement/freshness/risk gates; every new gate only refuses or shrinks.
