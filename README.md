# SportsAssets Hub — Sports Whale Tracker

Real-time platform that tracks the **top 5 most profitable sports traders on
Polymarket**, mirrors every trade they place with the lowest achievable latency,
pushes notifications to subscribed users, and maintains per-sport performance
analytics (W-L, realized P&L, ROI) for each tracked whale.

**Latency is the core product constraint.** Target: **≤3s** from on-chain fill to
user notification via the on-chain path; hard ceiling **≤10s** via the polling
fallback.

## Architecture

Five services in one monorepo (`docker compose up` for dev):

| Service | Path | Job |
|---|---|---|
| **Roster** | `backend/sportsassets/roster.py` + `workers/roster.py` | Selects/refreshes the tracked whale list from the live sports leaderboard; pin/ban overrides; weekly cron; history always retained |
| **Ingestion** | `backend/sportsassets/ingestion/` | **Path A** (primary, ~1–3s): Polygon WebSocket `OrderFilled` listener on the CTF/NegRisk exchanges with block-range backfill. **Path B** (fallback + reconciliation, 5–10s): Data-API polling per wallet on a 5s stagger. Both feed one idempotent pipeline with a shared dedupe key |
| **Analytics** | `backend/sportsassets/analytics/` | Average-cost position lifecycle, Gamma resolution tracking, per-market W/L (per-market, never per-leg), whale × sport × window rollups, leaderboard drift validation (±10%) |
| **API + Fan-out** | `backend/sportsassets/api/` + `notifications/` | FastAPI REST + SSE `/stream`; notification outbox → web push (VAPID) + Telegram channel with per-user prefs and burst collapsing |
| **Web Frontend** | `frontend/` | React (Vite): Live Feed, Whale Profiles, Sport Matrix, Markets, Alerts, Admin — dark trading-desk aesthetic, no fabricated data, honest empty states |

Infra: **PostgreSQL** (system of record), **Redis** (pub/sub + hot metadata
cache). The ingestion loop must run on an always-on host (Render/Fly/Railway
paid instance — never a sleeping free tier).

## Quick start (dev)

```bash
cp .env.example .env          # fill in RPC + keys (see below)
docker compose up --build     # postgres, redis, migrations, all services, frontend
make seed-roster              # resolve top-5 sports wallets from the LIVE leaderboard
```

Frontend: http://localhost:5173 · API: http://localhost:8000 · Admin page uses `ADMIN_TOKEN`.

Minimum config to go live:

- `POLYGON_WS_URL` / `POLYGON_HTTP_URL` — Alchemy or QuickNode Polygon endpoints (Path A).
  Without them the platform still works end-to-end on Path B at 5–10s latency.
- `VAPID_PRIVATE_KEY` / `VAPID_PUBLIC_KEY` — `python -m sportsassets.scripts.gen_vapid`.
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHANNEL_ID`, `TELEGRAM_ADMIN_CHAT_ID` — optional but
  recommended (user broadcast channel + ops alerts).
- Exchange contract addresses ship as defaults in `.env.example` — **verify against
  Polymarket's current docs before deploy**; they are config, not code.

### Roster seeding (important)

Wallet addresses are **always resolved from the live leaderboard API at
seed/refresh time — never hardcoded**. Mid-2026 reporting names wallets like
`beachboy4` and `1j59y6nk` as top sports traders; treat those as *candidates to
verify against the live board*, which `make seed-roster` does. (This build
environment had no network access to Polymarket, so no addresses were baked in
anywhere — run the seed at deploy time.)

## Key design decisions

- **Provisional-first fan-out.** A detected fill is inserted, published to
  `trades.new`, and queued for notification *before* enrichment. Market
  title/outcome/sport arrive ~a second later via the hot token→market cache
  (Redis, maintained by the metadata worker) and are pushed as `trade_update`.
- **One dedupe gate.** Both paths derive an identical SHA-256 key from
  `(tx_hash, asset, side, size, price, timestamp)`; the DB unique constraint is
  the only arbiter. Path A therefore uses real block timestamps (cached
  `eth_getBlockByNumber`) so keys match Path B's API timestamps.
- **At-most-once notifications.** Outbox rows are claimed (`sent=true …
  RETURNING`) before dispatch — replays and restarts can never double-push.
- **Average-cost accounting** (documented choice, vs FIFO): buys re-average,
  sells realize `(price − avg_cost)`, resolution realizes remainder at $1/$0.
  A market is a **Win iff its total realized P&L > 0** — never per-leg, so
  hedged packages score once.
- **Burst collapsing.** >5 pushes from one whale in 60s → one summary push
  ("beachboy4 placed 12 trades on X (net +$310K on Y)"), tracked across
  dispatch ticks; the feed itself is never collapsed.
- **Everything is a rebuild.** Positions and rollups are deterministic replays
  of (trades × resolutions); re-running any component is always safe.

## Tests

```bash
cd backend && python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest
```

40 tests cover: cross-path dedupe keys, position lifecycle math (incl. hedged
multi-leg and oversell clamping), sport classification, per-market W/L rules,
windowed rollups, burst collapsing + preference filtering, latency percentiles,
and `OrderFilled` decoding (maker/taker perspectives).

## Acceptance criteria → where satisfied

1. ≤3s Path A / ≤10s Path B — `ingestion/chain.py` (WS → provisional publish), `ingestion/poller.py` (5s stagger); per-trade latency badge in the feed.
2. WS outage → zero missed, zero dupes — cursor + `eth_getLogs` backfill (`chain.py`), hourly reconciler, DB dedupe gate, claimed outbox.
3. Matrix reconciles with leaderboard ±10% — `analytics/engine.py::validate_against_leaderboard`, Telegram drift alert.
4. Clean roster swap — `roster.py::apply_roster` (history kept, inactive mark, admin notify).
5. 100 SSE clients + 50-trade burst — Redis pub/sub fan-out; collapse policy in `notifications/collapse.py` (unit-tested).
6. Real p50/p95 latency dashboard — `/api/admin/latency` from `detected_at − ts`, rendered on the Admin page.

## Edge engine (sibling project)

`edge-engine/` is the shadow-mode trading engine built to its own spec
(`edge-engine/CLAUDE.md`) and calibration data. It shares this repo but runs
independently: `docker compose --profile edge up edge-shadow` (requires
`EDGE_ODDS_API_KEY`). It logs would-be fills only — the capital gate lives in
`edge-engine/config/risk.yaml` and is judged solely by
`python -m edge.shadow.grader`. The whale platform remains the live
cross-account validation layer for the engine's assumptions.

## Legal / ToS hygiene

The platform displays public on-chain data and public-API data with
attribution; it is informational only — **not betting or investment advice**.
No Polymarket trademarks are used in branding or UI.
