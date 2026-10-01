# 70aec64 release and production verification (2026-10-01)
- Deployed API-only: render-ops #1829 (run 36817755625), `deploy-api-commit 70aec64…`; dep-dauuhv8jo6nc73em5kbg live at 05:02:34Z (render-ops #1830).
- Workers: unchanged on f5d1c05, with before/after deploy lists in the #1829 log. No migration, no env change.
- The first demo at 05:04:53Z got HTTP 502 for about 15 s while the instance swapped (run 36818069018). Every endpoint answered 200 from 05:05:08Z, and agents report code_version …#70aec64.
- Demo re-run (run 36818210952, 05:06–05:07Z): all three agents answered in LLM mode (claude-opus-5-5, no fallback), with cited fact, decision and session IDs.
  - **Strategy attribution is now correct.** Xavier said: "Derek's two-model policy accounts for 99 … The Pinnacle-only benchmark refused 35, all because settlement is not supported … experimental and is not evidence of proven profitability."
  - **Audrey** counted "two separate strategies": DEREK_ENTRY_POLICY_V2 99 REFUSE and PINNACLE_ONLY_PAPER_BENCHMARK 35 × SETTLEMENT_NOT_SUPPORTED. Her citations point paperbench:* to /api/command/paper/benchmark and paperdec:* to /api/command/paper/derek.
  - **Balances:** cash $500,000, reserved $0, available $500,000, 1 ledger entry (INITIAL_FUNDING), all 8 reconciliation checks pass. Session paper_session_20261001T014716Z: 213 passes, 0 errors, 0 mutation attempts.
  - **Speech:** 503 VOICE_NOT_RESOLVED for all three. ElevenLabs `GET /v1/voices` 401 missing_permissions, "missing the permission voices_read" (request IDs 316d433a1f507218e7387a51f18023c4, b445c3e00fb3d979608d67e6b9dd79d6, d40cce57f619dc154cddb20d78d88414).
- **Benchmark:** 35 decisions by 05:07Z, all REFUSE SETTLEMENT_NOT_SUPPORTED (newest 1925, atc-brb-bot-vln-2026-10-03-bot, Pinnacle 0.391, FRESH 27.3 s). 0 orders, 0 fills: still zero simulated executions.
