# BETTOR closeout requirement register

Durable register required by the owner's overnight directive (section 1). Each
row links a requirement to its implementation SHA, tests, production evidence,
status and next action. Updated in place as work lands; the git history of this
file is the audit trail.

**Status levels, in order.** A row is at the highest level it has *proven*:

| Level | Meaning |
|---|---|
| `OPEN` | not implemented |
| `LOCAL` | committed in a worktree, not pushed |
| `PUBLISHED` | pushed to a remote branch |
| `TESTED` | backend-tests, capital-critical, commit-guard and engine-diagnostic green on the exact SHA |
| `DEPLOYED` | running SHA verified on the service(s) that carry it |
| `ACCEPTED` | production behaviour proven by machine evidence (readback, census, packet) |
| `EXTERNAL` | needs an owner/venue action that existing permissions cannot perform (named) |
| `FORWARD` | needs forward evidence that has not occurred yet (named) |

**Hard boundaries in force for every row:** SMALL LIVE = SHADOW; Kalshi live
money NOT ACTIVATED; Adriana SHADOW ONLY; no order/cancel/funding/capital
authority added; historical PAPER unchanged; no thresholds relaxed.

## Production baseline (2026-10-08)

| Item | Value | Evidence |
|---|---|---|
| RC4 implementation | `9b94ef5c8721699da56124694ed2a6d8a7fd226a` (claude/red-team-closeout-v1) | gates 37727994146 / 37727997121 / 37727994149 / 37727994093 all success |
| RC4 release | `7fd4574e9ac8b95c355035a5bd4a9927d01c29ea` (claude/release-api; tree = 9b94ef5c) | gates 37731116971 / 37731117029 / 37731117020 / 37731117015 all success |
| API running | 7fd4574e since 05:15:50Z | pm-acceptance 37738089957 render.json; Render events |
| Workers running | 7fd4574e since 05:16:03Z | same |
| Market plane running | 7fd4574e since 05:16:49Z, OOM-killed repeatedly (06:10, 06:12, 06:47 and ~11 more by 12:50Z) | Render events srv-db3idqvavr4c739udecg |
| Frontend (Netlify production) | `f16c5ce8a101294ce33bb356b929e5832d6d4545`, deploy 6ac6b59dea05d000071c61c2 | https://command.bettortoken.com/build.json |
| Historical PAPER | unchanged across the RC4 deploy | research/rt_historical_paper_fingerprint.sql pre/post (cutoff 2026-10-08 02:00Z) |

## Register

### A. Policy integrity and lifecycle

| ID | Requirement | Implementation | Tests | Production evidence | Status | Next action |
|---|---|---|---|---|---|---|
| A1 | V6 semantic dependency boundary closed before first freeze; history preserved; cross-interpreter reproducible; semantic mutation fails closed | RC4 9b94ef5c | test_bettor_policy_v6_integrity, test_shadow_bettor_codesha (capital-critical) | V6 VERIFIED, codeShaMatches true, code SHA 9c66940429caf9b7 on 3.12.3 and 3.13.16 | ACCEPTED | re-verify on RC5 |
| A2 | bettor_live quiescent park, bounded control polling, backoff reset, capped DB backoff, real-crash restart | RC4 9b94ef5c | test_bettor_live_delegated_lane_parks (capital-critical) | no "exited cleanly; restarting" churn in RC4 log counts | DEPLOYED | include churn count in RC5 packet window |
| A3 | Migration immutability manifest; zero changed-after-apply | RC3/RC4 | migration manifest tests | packet migrations section | ACCEPTED | re-verify RC5 forward migrations |

### B. Memory and runtime

| ID | Requirement | Implementation | Tests | Production evidence | Status | Next action |
|---|---|---|---|---|---|---|
| B1 | Workers OOM root cause (analytics settle pass read the whole archive) | RC4 9b94ef5c | test_analytics_archive_bounded | no workers OOM since 05:16:03Z (7.5+ h at 12:50Z) | DEPLOYED | 60-min window inside RC5 acceptance |
| B2 | Premap sweep holds one page | RC4 f4c758e6 | test_premap_memory_bound | same | DEPLOYED | same |
| B3 | Market plane OOM at 2 GiB: attribute and bound (no larger instance) | in progress: rc5/plane-memory (attribution harness, registry/coverage/heartbeat bounds) | full-mode boot test pending | plane 1.4-2.0 GB resident, repeated oomKilled | OPEN | finish, gate, deploy, prove OOM-free window |
| B4 | SizedBooks.unwant releases instrument records (review patch) | RC5 263a7ad7 (patch was malformed; applied by hand) | test_market_plane_unwant_releases_instruments (3, fail on base) | ~23 MB per catalogue rotation; not the main driver | LOCAL | ship in RC5 |
| B5 | Audrey "paper pass not completed" alert reads the last completed pass | RC5 eab1bdbc | test_audrey_pass_alert_reads_completion (4, fail on base) | false "unknown time" alerts 05:20Z / 06:20Z | LOCAL | ship in RC5; confirm no false alert after deploy |

### C. Acceptance harness (PM defects 2026-10-08 02:30Z)

| ID | Requirement | Implementation | Tests | Production evidence | Status | Next action |
|---|---|---|---|---|---|---|
| C1 | historical_paper_immutable bound to pre/post fingerprint receipt (fixed cutoff, watermark, deterministic order); missing/conflicting = UNPROVEN | in progress: rc5/acceptance-harness | new binder + DB tests | RC4 pre/post fingerprints identical (manual) | OPEN | finish, gate |
| C2 | no-OOM from complete Render event windows for API/workers/plane; injected OOM must turn RED | in progress: rc5/acceptance-harness | injected-failure fixture | RC4 replay: plane FAILED with 2 oomKilled | OPEN | finish, gate |
| C3 | Declared schema paths in evidence_packet; READ_UNAVAILABLE never GREEN | in progress: rc5/acceptance-harness | reordered / contradictory / partial / absent tests | | OPEN | finish, gate |
| C4 | post_receipt default OFF (zero POSTs), false receipt preserved append-only, attestation verified (not continue-on-error) | in progress: rc5/acceptance-harness | workflow + receipt tests | earlier false receipt for 08828d04 preserved | OPEN | finish, gate |
| C5 | Plane memory/events/log counts in the packet | in progress: rc5/acceptance-harness | | | OPEN | finish |

### D. Market data, Kalshi, PMUS

| ID | Requirement | Implementation | Tests | Production evidence | Status | Next action |
|---|---|---|---|---|---|---|
| D1 | PMX gRPC primary: subscribe-all semantics, acks, L2, identity, same-book arbitration, gaps, recovery, bounded storage | RC4 (accounting, plane-only harness input, majority rule) | test_p1_pmx_grpc_primary_accounting | plane snapshot stale whenever the plane is OOM-restarting | DEPLOYED (source); consumer use OPEN | rc5/pmx-consumer-books |
| D2 | Consumer use of PMX books (QUOTE_STALE / PROBABILITY_DEADLINE first losses capped by REST budget) | in progress: rc5/pmx-consumer-books | | SOFTWARE first losses 73/h after RC4 | OPEN | finish, gate, census by source |
| D3 | Dedicated plane in approved full mode, orderless guard, full-mode boot test with mocked transports | guard RC4; boot test in progress (rc5/plane-memory) | test_market_plane_orderless_guard (22) | plane provisioned 05:15Z; 6 values copied byte-exact from workers (disclosed to owner) | DEPLOYED (guard); boot test OPEN | finish |
| D4 | Kalshi key classes (RSA-PSS + Ed25519) everywhere a signer exists; PEM bytes preserved | in progress: rc5/kalshi-keyclass-pmus-signer | | plane GET account/limits 200 with Ed25519 | OPEN | finish |
| D5 | PMUS funded readers refuse the wrong key class before network; never substitute institutional positions | in progress: rc5/kalshi-keyclass-pmus-signer | test_pmus_credential_census | PMUS_KEY_ID/SECRET hold the PMX RSA client | EXTERNAL (funded retail Ed25519 key absent) + code OPEN | finish code; owner provides retail key |
| D6 | Priority freshness >= 95% on full denominator | | | 116/139 (83%) | OPEN | rc5/pmx-consumer-books census |

### E. Management, settlement, accounting

| ID | Requirement | Implementation | Tests | Production evidence | Status | Next action |
|---|---|---|---|---|---|---|
| E1 | Xavier strict no-growth, protection never lost in transition, full population | RC4 entry guard (cannot price / protect) | test_exploration_entry_xavier_manageability | complete packets 0/3 (waiting for evidence) | OPEN | rc5/xavier-no-growth audit |
| E2 | Priced settlement-difference policy; unknown payout refused | in progress: rc5/settlement-difference-epoch-b | | | OPEN | finish |
| E3 | $500,000 epoch exact reconciliation (4 carried EPOCH_OPEN_MARK_UNVERIFIED, $141.80 post-epoch cash outside) | in progress: rc5/settlement-difference-epoch-b | | equity $458,852 (-8.23%) not re-baseable | OPEN | finish |
| E4 | Fixture normalization, rotation, WNBA namespace, aliases, schema hygiene, SOFTWARE first losses | RC4 (12 leagues admitted, first-loss producers) | RC4 suites | SOFTWARE 73/h after deploy | DEPLOYED (partial) | census on RC5 |

### F. Frontend and live scores

| ID | Requirement | Implementation | Tests | Production evidence | Status | Next action |
|---|---|---|---|---|---|---|
| F1 | Experience V3 applied exactly (superseded by V4) | 18bc48ea + freeze fix e01a7d4d (claude/uiux-experience-v3) | verifier 30/30; 19 files 242 passed | CI preview 37783164445: occlusion, header clipping, touch < 44 px, phone Command blank band | PUBLISHED (not for production) | fixes carried into V4 |
| F2 | Experience V4 applied exactly + necessary fixes; device acceptance; publish | in progress: claude/experience-v4-live-game-state | verifier 33/33 required | | OPEN | finish, CI preview with production data, reviewer, publish |
| F3 | Live Game State backend (migration 316, flags OFF, real-PG tests, isolation) | in progress: claude/live-game-state-v1 | 7 real-PG tests required | | OPEN | finish, gates, census |
| F4 | Live Game State frontend (in V4 branch) | in progress | 20 Node tests | | OPEN | finish |
| F5 | Collector hosting within existing resources, minimal DB permissions | | | no dedicated service exists; a new paid service is not authorized | OPEN | propose hosting; owner decision if new spend needed |
| F6 | Read-only frontend preview harness (Netlify publishes only the production branch) | 01df6c0f.. (claude/session-njaewf, CI-only) | runs 37783159982 / 37783164445 | | PUBLISHED | reuse for V4 |

### G. Security

| ID | Requirement | Implementation | Tests | Production evidence | Status | Next action |
|---|---|---|---|---|---|---|
| G1 | ADMIN_TOKEN strength (production accepts a 2-character admin credential; it is the HMAC signing key) | throttle 7ad4b746 (RC5) | test_admin_token_guard (8) | frontend-preview 37781022031 printed token length 2; owner notified 2026-10-08 13:16Z | EXTERNAL (rotation is the owner's) + mitigation LOCAL | owner rotates ADMIN_TOKEN (Render API/workers + GitHub secret) |
| G2 | No secrets in messages, artifacts, bundles or logs | frontend-preview guard; plane.sh masking | | | ongoing | audit pass |

### H. Economics

| ID | Requirement | Implementation | Tests | Production evidence | Status | Next action |
|---|---|---|---|---|---|---|
| H1 | Economic funnel per mechanism and frozen policy, first stopping point and cause | in progress: research/economic-funnel | read-only SQL | scale observer RED, rows_recorded=0 (02:41Z) | OPEN | finish report |
| H2 | 98% confidence claim | | | realized PAPER P&L -$44,374 (11:00Z) | FORWARD / NOT ESTABLISHED | measure forward; never manufacture |

## Change log

- 2026-10-08 13:55Z: register created on the RC5 branch (claude/red-team-closeout-v1) with RC4 baseline, RC5 local commits 7ad4b746 / eab1bdbc / 263a7ad7 and the in-flight lanes.
