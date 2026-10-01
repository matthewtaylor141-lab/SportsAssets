# PINNACLE_ONLY_PAPER_BENCHMARK: the first collection cycles on a8bf09a (2026-10-01)

Paper only. Real-money submission DISABLED. **No orders and no fills yet**: the paper experiment still has zero simulated executions.

## Deployment and switches
- sportsassets-api `a8bf09a` live at 04:17:34Z (render-ops #1828, run 36814577797); migration 182 applied at 04:17:21Z.
- Control rows (research-sql runs 36814580007 and 36814792579):
  - `PINNACLE_ONLY_PAPER_BENCHMARK` enabled=t;
  - `PAPER_ENTRIES:DEREK_ENTRY_POLICY_V2` enabled=f (the two-model policy still records decisions; no entries);
  - env `PAPER_BENCHMARK=on`.
- GET /api/command/paper/benchmark at 04:22:50Z (ping run 36814796950) shows:
  - `enablement.enabled=true`;
  - the strategy label and disclosure;
  - `book_currency NOT_ESTABLISHED`;
  - each decision with its contract-match checks, and `internal_model.available=false`, `p_internal=null`.

## What the first cycles decided (run 36814792579, 04:22:22Z)
- 12 benchmark decisions on 7 markets, all `REFUSE` with `SETTLEMENT_NOT_SUPPORTED`.
  - 5 were decided in the cycle at the valuation instant (Pinnacle age 8.2–30.4 s); 7 by the paper-pass backstop on older valuations.
  - Every other contract-match check passed: identity, payout outcome, lane probability, venue PMUS.
- No book was read, no edge was computed and no order was placed. Under `decide_one`, the book is read only for a candidate that passes the contract match.
- Leading candidates by Pinnacle probability, decided in-cycle:

| valuation | market | side | p_pinnacle | Pinnacle age at decision | settlement check |
|---|---|---|---|---|---|
| 1901 | aec-mlb-chc-sd-2026-09-30 | SHORT | 0.9356 | 9.7 s | UNKNOWN |
| 1900 | aec-mlb-phi-atl-2026-10-01 | SHORT | 0.4934 | 8.2 s | UNKNOWN |
| 1903 | atc-unl-wal-den-2026-10-04-wal | LONG | 0.2211 | 28.3 s | UNKNOWN |
| 1902 | atc-unl-wal-nor-2026-10-01-wal | LONG | 0.1295 | 27.2 s | UNKNOWN |
| 1904 | atc-unl-gre-ger-2026-10-04-gre | LONG | 0.2786 | 30.4 s (also STALE) | UNKNOWN |

- Net economics: **none computed for any candidate**. Edge, EV after fees and depth are evaluated only after the settlement match, which none passed.
- Account: cash $500,000, reserved $0, available $500,000; 1 ledger entry (INITIAL_FUNDING); running balance agrees. Session `paper_session_20261001T014716Z`: 166 passes, 0 errors, 0 mutation attempts.
- Xavier handoffs: 0, since there were no fills. Audrey benchmark findings: 0.

## The bottleneck: settlement-terms evidence, not coverage or freshness
benchmark_settlement_bottleneck.sql (run 36814919847) and benchmark_settlement_comparison_full.sql (run 36815011230) show the following.

**S3: every valuation of the last 24 h has compatibility UNKNOWN.**

| sport | valuations | markets | compatibility |
|---|---|---|---|
| soccer | 534 | 21 | UNKNOWN |
| baseball | 174 | 5 | UNKNOWN |

So the benchmark's contract match (`compatibility == COMPATIBLE`) can never pass on the current collection, whatever the freshness, depth or edge.

**Why each family is UNKNOWN, from the stored comparison**

- **MLB, postseason.** Valuation 1901 (game_pk 849840, `game_type F`, phase `PLAYOFF_OR_PLAY_IN`):
  - fixture read and context PRE_GAME established;
  - `bettor_settlement_terms.book_terms` returns nothing outside the captured scope (CAPTURE_LIMITS: "MLB PLAYOFF AND PLAY-IN fixtures carry an explicit exception and are outside these terms");
  - so all 7 conditions are book-silent.
  - The regular season has ended, so every MLB contract now collected is postseason.
- **MLB, regular season** (settlement_replay.txt): the replay of the three captured venue rule texts through `compare_prose` gives **INCOMPATIBLE**.
  - On POSTPONED_OR_ABANDONED, Pinnacle returns the stake and the venue settles at the last fair market price.
  - The venue text is also silent on the other six conditions.
  - So capturing Pinnacle's playoff exception would not produce COMPATIBLE either.
- **Soccer.** Valuation 1904:
  - `fixture_acquisition.refusal = FIXTURE_METADATA_HAS_NO_CONDITION_KEY` (a venue-native identity has no global condition id to key the fixture row), so phase and format are not established and the book side is empty.
  - With phase and format supplied, the replay is **still UNKNOWN**: the captured Pinnacle soccer section states no payout for SUSPENDED_AND_RESUMED_WITHIN_THE_PUBLISHED_WINDOW or SUSPENDED_TO_RESUME_BEYOND_THE_PUBLISHED_WINDOW. The module leaves them out deliberately rather than inferring them.

**The engineering defect, and why fixing it alone would not help.** The missing soccer fixture key is a real collector defect. But fixing it changes no verdict: the soccer comparison stays UNKNOWN on the two suspension conditions. No engineering change inside the approved rule produces a qualifying contract in today's PMUS sports universe.

**Owner decision needed. Nothing was loosened.** Under the approved rule ("matched to the exact contract, outcome and settlement terms", implemented as condition-by-condition payout compatibility), the benchmark cannot enter on current PMUS sports contracts. The options:
1. Keep the rule. The benchmark records refusals and never enters.
2. Define "settlement terms matched" as agreement on the completion conditions, with the void and suspension states, where the two documents differ or are silent, carried as an explicit priced risk (the benchmark economics already carry `settlement_states`). That is a change to the approved strategy, so only the owner can make it.
3. Capture more Pinnacle text first: the MLB playoff exception, and any soccer suspension clause. Option 3 cannot fix MLB, which is INCOMPATIBLE on the postponed condition.

## Management visibility
- **Benchmark page** (/api/command/paper/benchmark): shows the strategy label, disclosure, per-check decision explanation, `p_internal=null`, balance and enablement. There is no handoff yet, because there are no fills.
- **Chat, defect found.** The 04:22Z demonstration (run 36814796950, demo job) was answered by the LLM for all three agents, citing decision and session IDs, with the balance reconciled. But all three attributed the 12 benchmark refusals to Derek's decisions: `paper_brief._decisions` predates migration 182's strategy column.
  - Fixed on `claude/rel-label`: decisions counted by strategy, one fact per strategy, each decision fact naming its strategy, citations pointing to /paper/benchmark, and Audrey's tool, rules and records-only text split by strategy.
  - New test: G6 in test_agent_chat_answers_paper_questions_from_the_ledger.py.
- **Voice.** ElevenLabs identifies the cause, for each of the three agents:
  - `GET /v1/voices` → 401, `provider_error_status: missing_permissions`, "The API key you used is missing the permission voices_read to execute this operation." (request IDs 124889b99cf48c48e493646a0ae13f2a, cb6a350a7b9a5ab813bbdd4e1696c823, ab6eccf90d36994b6554c09fdf8ab2fa).
  - Provisioning correction named by the provider: grant the key `voices_read`. Alternatively, configure ELEVENLABS_VOICE_ID_{DEREK,XAVIER,AUDREY} so the voice list read is not needed (env_voice_id is null today).
  - Audrey's speak now resolves `conv-…:3`; the earlier 404 is fixed.
