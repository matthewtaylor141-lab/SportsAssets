CLAUDE - APPLY BETTOR_COMPLETION_READINESS_PATCH_V1 AS A CLEAN COMPLETION/READINESS WORKSTREAM.

This patch is not a license to activate live capital. It is a completion patch
for the code-controlled blockers I audited.

Create a clean branch from the tested closeout tree, not from polluted
claude/p0-closeout:

  claude/completion-readiness-v1

## Verified blockers this patch attacks

1. Shared sportsassets-workers was OOM-killed repeatedly.
2. universal_market_plane was registered in the shared worker supervisor even
   though it is supposed to be dedicated read-only.
3. Setting UNIVERSAL_MARKET_PLANE=off merely made the loop exit and restart
   every 5 seconds. That is not acceptable.
4. Broad catalogue / market-plane work was in the wrong runtime.
5. The PMX architecture assumption has changed: Polymarket says use one
   empty-list market-data stream for the full universe, not many 1,000-symbol
   shards.
6. Profitability remains CASH unless the probability engine proves positive
   all-in lower-bound EV.

## Phase 1 - source-level runtime patch

Apply `runtime_patch/patches_shared_worker_isolation.diff`.

Required behavior:
- sportsassets-workers startable_loops() excludes universal_market_plane.
- There is a boot marker naming dedicated_only loops.
- No shared worker log line may say `starting loop: universal_market_plane`.
- The fix must not depend on UNIVERSAL_MARKET_PLANE=off.

Add/adapt `runtime_patch/tests/test_shared_worker_isolation.py`.

## Phase 2 - PMX stream architecture patch

Apply the intent of `runtime_patch/patches_universal_market_plane_subscribe_all.diff`
against the actual current market-plane / sharded-stream code.

Required behavior:
- dedicated UMP defaults to one market-data stream;
- subscription mode is `SUBSCRIBE_ALL_EMPTY_SYMBOL_LIST`;
- explicit-symbol shard mode is still available only as fallback;
- the 20 firm-wide stream budget is treated as shared across all gRPC services;
- the full instrument universe is filtered locally;
- refdata full-pull is bounded/cached and instrument state changes are used for listings.

If the current Manager class cannot yet support subscribe_all=True, implement it.
The outbound subscribe request for subscribe-all must use symbols=[] exactly as
documented by the venue.

## Phase 3 - broad catalogue isolation

Shared workers may keep imminent/held critical paths only. They must not own
broad market-plane catalogue work or any unbounded full-universe refresh.

If broad premap calendar remains in shared workers, hard-bound it:
- no unbounded in-memory accumulation;
- hard request/page/time budgets;
- explicit TRUNCATED / CASH / NO_COVERAGE_COMPLETE claims when budgets exhaust;
- never silently treat a truncated pass as complete.

Prefer moving broad universe work to the dedicated read-only market-plane runtime.

## Phase 4 - probability/readiness logic

Integrate the package's completion logic:
- `completion_logic/probability_authority.py`
- `completion_logic/ev_authority.py`
- `completion_logic/readiness_gate.py`

This logic is fail-closed:
- market prior is incumbent;
- BETTOR may deviate only with out-of-sample evidence;
- all-in executable EV lower bound must be positive;
- capital readiness requires no-OOM, freshness, zero software REDs, Xavier
  packet completeness, canary, probability edge, digital twin, settlement,
  capacity, venue-confirmed positions, SMALL LIVE=SHADOW and immutable PAPER.

## Phase 5 - vendor research packages

This ZIP includes three prior packages under `vendor/`:
- ev_probability_engine
- profitability_stack
- revenue_reliability_stack

Do not merge them into production hot paths. Wire them as evidence generators:
- probability receipts
- profitability receipts
- agent/revenue-readiness receipts

## Required proof checkpoint

Return proof only:
1. NEW implementation SHA
2. exact files changed
3. exact diff summary
4. shared-worker isolation tests/pass counts
5. subscribe-all stream tests/pass counts
6. completion logic tests/pass counts
7. package/vendor tests that still pass
8. exact-SHA backend-tests
9. capital-critical
10. commit-guard
11. engine-diagnostic
12. Render deploy receipt
13. production log proof: no shared-worker UMP starts
14. >=60-minute no-OOM RSS high-water trace
15. market-plane receipt: dedicated runtime / subscription mode / stream count
16. freshness receipt: priority and held denominators
17. profitability verdict
18. explicit:
    - SMALL LIVE=SHADOW
    - historical PAPER unchanged
    - no authority expanded

## Do not

- activate live trading
- change credentials
- raise risk limits
- reduce freshness gates
- reduce settlement gates
- rewrite historical paper
- classify software defects as external
- claim capital readiness while profitability remains CASH
