# edge-shadow containment record (2026-10-01)

Owner approval: suspend ONLY edge-shadow and disable its auto-deploy. No cancels,
no liquidation, no credential revocation, no account-wide settings, no other service.

## Pre-state, captured 02:15–02:16Z (render-ops on claude/session-njaewf, read-only)
Full job logs are retained in GitHub Actions under these runs:
| run | action | result |
|---|---|---|
| #1804 (36804967019) | services | `srv-d9gilegn5bic73escs5g edge-shadow background_worker suspended=not_suspended plan=starter branch=claude/session-njaewf autoDeploy=yes`. Others: sportsassets-workers srv-d9gcv6urnols73ce6erg autoDeploy=yes; sportsassets-api srv-d9gcv6urnols73ce6er0 autoDeploy=no; bettortoken-api srv-d7nnm7hkh4rs73bajdtg; sportsassets-db available 50GB |
| #1805 (36804969244) | rootdir-get | rootDir=edge-engine, dockerfilePath=./Dockerfile, context=., branch=claude/session-njaewf, AUTO_DEPLOY=yes |
| #1806 (36804971542) | env-keys | 15 keys (names + lengths only): EDGE_KCOPY(1) EDGE_KCOPY_DAY_USD(5) EDGE_KALSHI_PRIVATE_KEY_PATH(23) EDGE_LIVE_VENUES(20) EDGE_KALSHI_KEY_ID(36) EDGE_SKIP_MAPPER_GATE(1) EDGE_PMUS_SECRET_KEY(88) EDGE_PMUS_KEY_ID(36) EDGE_LEDGER_DB(34) EDGE_DATA_DIR(14) EDGE_ODDS_API_KEY(32) EDGE_INGEST_TOKEN(2) EDGE_PLATFORM_API(37) EDGE_KALSHI(1) EDGE_CYCLE_SECONDS(3) |
| #1807 (36804975627) | events | OOM kill (512Mi) roughly every 5 minutes; latest server_failed oomKilled 02:12:54Z, server_available 02:12:41Z |
| #1808 (36804978112) | deploys | live fe1678f (manual, 00:23:10Z); previous f5d1c05 service_resumed 23:01:05Z |
| #1809 (36804980237) | logs 'account link' | every boot 23:20–02:12Z: kalshi $44,340.72; polymarket-us $14,000.01 until 01:13:39Z, $13,000.01 from 01:19:07Z |
| #1810 (36804982387) | logs last 30 min | boots 02:01:53, 02:07:16, 02:12:44: "edge runner starting: mode=LIVE_BETA venues=['kalshi','polymarket-us']"; armed: xv watch (day cap $1000), xv crypto (live=False), kalshi first-set comeback sleeve, kalshi underdog leg (boot-immediate), reactor on polymarket-us. Only GET requests visible for PMUS. |

## Shutdown behaviour (code at origin/claude/session-njaewf, edge-engine/)
- Entry: `CMD ["python", "-m", "edge.shadow.runner"]` (Dockerfile). No signal handler,
  no atexit hook and no add_signal_handler anywhere in edge-engine/src.
- `main()` (runner.py L2340) wraps `_main_impl()` in `except BaseException` and then
  `_post_status("crash", ...)` → POST `{EDGE_PLATFORM_API}/api/engine/status` (our API, not a venue).
  Python's default SIGTERM action ends the process without raising an exception, so that
  handler does not run on SIGTERM; SIGKILL cannot be caught.
- Conclusion: no code path sends a venue request on shutdown. The one possible shutdown-time
  request is the crash beacon to our own API, and it only fires on an exception, not on SIGTERM.
- Not verifiable from source: third-party SDK internals (polymarket-us, websockets). With default
  SIGTERM handling, Python runs no cleanup, so no library close handshake is expected.

## Actions (owner-approved)
| time (UTC) | run | action | result |
|---|---|---|---|
| 02:17:17 | render-ops #1811 (36805109969), ref claude/session-njaewf | suspend edge-shadow, confirm=DO | `suspend -> edge-shadow (srv-d9gilegn5bic73escs5g)` HTTP 202 |
| 02:17:38 | render-ops #1812 (36805135202), ref claude/command-center (action pinned to srv-d9gilegn5bic73escs5g) | edge-autodeploy-off | before `autoDeploy=yes suspended=suspended`; PATCH HTTP 200; after `autoDeploy=no suspended=suspended` |

Nothing else was changed: no env, plan, deploy, credential or order action, and no other service touched.

## Post-checks
| check | run | evidence | result |
|---|---|---|---|
| suspended | #1813 (36805260146) 02:19:10Z | `srv-d9gilegn5bic73escs5g edge-shadow background_worker suspended=suspended plan=starter branch=claude/session-njaewf autoDeploy=no` | PASS |
| autoDeploy no | #1813, #1815 | autoDeploy=no | PASS |
| events | #1814 (36805262758) 02:19:11Z | newest first: 02:17:38.59 auto_deploy_disabled (setting_change); 02:17:17.97 server_available; 02:17:17.73 service_suspended; 02:17:17.73 suspender_added (actor User = account owner's API key); 02:12:54.89 server_failed oomKilled 512Mi | PASS: no restart/deploy after the suspend (the server_available row is part of Render's suspend transition, 0.24 s after service_suspended, and no process output follows it) |
| no deploy | #1817 (36806295996) 02:32:16Z | newest deploy is still fe1678f live 00:23:10Z (manual) | PASS |
| no process activity | #1816 (36806293572) logs 02:07:14–02:32:14Z, ~75 lines (under the 100 cap) | last line 02:12:54.212Z (`GET gateway.polymarket.us/v1/events ... 200`), then OOM kill 02:12:54.89Z; no 'edge runner starting' and no line at all after that through 02:32:14Z | PASS |
| last observed worker activity | #1816 + #1814 | last boot 02:12:44.33Z (`edge runner starting: mode=LIVE_BETA`), last log line 02:12:54.21Z (a venue GET), process OOM-killed 02:12:54.89Z, suspended 02:17:17.73Z | recorded |
| sportsassets-workers unchanged | #1813, #1818 (36806298188), #1819 (36806300552) | srv-d9gcv6urnols73ce6erg not_suspended, plan standard, autoDeploy=yes (same as pre-state); live deploy f5d1c05 since 2026-09-24T11:44:49Z; newest event 2026-09-30T23:42:01Z commit_ignored fe1678f; nothing since | PASS |

## Still unresolved
Venue orders and positions on both venues are NOT established. Suspension stopped this worker's management (including Kalshi underdog exit re-rests) as well as its new submissions. Any order it left resting, and every position, remains unverified until authoritative venue reads exist. Kalshi has no reachable read path (the only key is on the suspended worker). For Polymarket US, no venue read of open orders or short positions is available (repo secrets empty; the API normalizer drops shorts). edge-shadow must not be resumed.
