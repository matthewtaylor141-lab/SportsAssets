# PinnAPI — owner update and immediate integration priority

2026-10-01: Matt reports purchasing PinnAPI and adding its key as **`pinnapi_key` (lowercase)** on multiple Render servers. Codex has not read the key, independently checked those services or tested the account. No PinnAPI integration exists in the audited `5a00cd30` code. This update supersedes the assumption that The Odds API is the only available reference source.

## Verified provider documentation

PinnAPI describes live and prematch moneyline, spread, total and team-total coverage. Its SSE is a filtered drop-alert stream, not a complete price-change feed. The raw WebSocket add-on is the relevant candidate for rebuilding full market state; a paid base subscription alone does not prove entitlement. Advertised latency is a provider measurement, not our measured source-to-decision latency.

Sources: https://pinnapi.com/docs and https://pinnapi.com/pinnacle-websocket-api . Canonical machine-readable reference: https://pinnapi.com/llms-full.txt . Check current documentation during integration.

## Claude's next actions

1. Use the owner-confirmed variable `pinnapi_key` (case-sensitive); verify its presence on the chosen ingestion service without printing its value. Adding a key alone does not connect a feed. Do not copy it into source, browser code, URLs, patches or logs.
2. Run `research/pinnapi_readiness_probe.py --sport-id <documented-id> --key-env pinnapi_key` in that environment. It uses three bounded GETs and emits counts/status only. Record actual live/prematch coverage and schema. It does not test streaming entitlement, place orders or modify the database.
3. Read account entitlements privately and report only plan/features/limits. Verify raw-feed access before selecting transport. Coordinate any streaming probe with the single production ingestion owner: an extra consumer may displace or conflict with the existing account connection. Do not purchase another subscription or silently assume the add-on is included.
4. Implement a separate provider adapter and durable input provenance. Keep event identity, phase, period, side and line exact; preserve source timestamps separately from local receipt. Respect snapshot-versus-delta semantics and market/period close signals. A stream reconnect must not leave old active prices usable.
5. Publish a shared current-state cache to Derek and Xavier. Both should react to validated changes through deterministic policy logic. Avoid independent feed connections per agent or API replica. Prove the ownership/lease behaviour across redeploys, reconnects and failures.
6. Integrate incrementally into the existing paper path, starting with established mappings, then adding each requested market family with explicit grading tests. Keep the owner-approved 0.5pp investment rule, positive after-fee EV requirement, training labels and existing budgets. A provider's odds-drop percentage is not our probability-point EV threshold.
7. Measure coverage and source/receipt/evaluation delays; record a genuine in-play decision-to-fill-to-Xavier-to-Audrey trace. Capture missed/stale evaluations too. Continue the 200–500 distinct-position/day capacity work in `codex_continuous_coverage_readiness.md`; a faster feed does not guarantee that many qualifying trades.

The office UI patch is independent and ready for normal integration/gating. Continue its release while the feed adapter is built. Report what is deployed and evidenced, not merely configured. Keep real-money execution disabled and the protected workers and edge-shadow state unchanged.

Probe status: local syntax and controlled-response checks only; no authenticated PinnAPI call has been made by Codex. Production integration remains Claude's next task.
