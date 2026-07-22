"""FastAPI application: REST + SSE stream + push subscription + admin."""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .. import roster as roster_svc
from ..bus import CH_HEALTH, CH_TRADES_ENRICHED, CH_TRADES_NEW, get_redis
from ..config import settings
from ..db import close_pool, get_pool
from . import queries

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await get_pool()
    yield
    await close_pool()


app = FastAPI(title="SportsAssets Hub API", lifespan=lifespan)

_origins = [o.strip() for o in settings().cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins or ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def require_admin(x_admin_token: str = Header(default="")) -> None:
    if x_admin_token != settings().admin_token:
        raise HTTPException(status_code=401, detail="admin token required")


# ── Health & config ─────────────────────────────────────────────────


@app.get("/healthz")
async def healthz() -> dict:
    pool = await get_pool()
    await pool.fetchval("SELECT 1")
    return {"ok": True}


@app.get("/api/config")
async def public_config() -> dict:
    cfg = settings()
    return {
        "vapid_public_key": cfg.vapid_public_key,
        "telegram_channel_invite_url": cfg.telegram_channel_invite_url,
        "burst_collapse_threshold": cfg.burst_collapse_threshold,
        "burst_collapse_window_seconds": cfg.burst_collapse_window_seconds,
    }


# ── Live stream (SSE) ───────────────────────────────────────────────


@app.get("/stream")
async def stream(request: Request) -> StreamingResponse:
    """SSE feed: `trade` (provisional), `trade_update` (enriched), `health`."""

    async def gen():
        pubsub = get_redis().pubsub()
        await pubsub.subscribe(CH_TRADES_NEW, CH_TRADES_ENRICHED, CH_HEALTH)
        event_names = {
            CH_TRADES_NEW: "trade",
            CH_TRADES_ENRICHED: "trade_update",
            CH_HEALTH: "health",
        }
        try:
            yield "retry: 2000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=15.0)
                if msg is None:
                    yield ": keepalive\n\n"
                    continue
                name = event_names.get(msg["channel"], "message")
                yield f"event: {name}\ndata: {msg['data']}\n\n"
        finally:
            await pubsub.unsubscribe()
            await pubsub.aclose()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Feed / whales / matrix / events ─────────────────────────────────


@app.get("/api/feed")
async def api_feed(
    limit: int = Query(50, le=200),
    before_id: int | None = None,
    whale_id: int | None = None,
    sport: str | None = None,
    side: str | None = None,
    min_notional: float | None = None,
) -> list[dict]:
    return await queries.feed(limit, before_id, whale_id, sport, side, min_notional)


@app.get("/api/whales")
async def api_whales(include_inactive: bool = False) -> list[dict]:
    return await queries.whales(include_inactive)


@app.get("/api/whales/{whale_id}")
async def api_whale(whale_id: int) -> dict:
    profile = await queries.whale_profile(whale_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="unknown whale")
    return profile


@app.get("/api/whales/{whale_id}/day/{day}")
async def api_whale_day(whale_id: int, day: str) -> dict:
    """Day drill-down for the P&L calendar: every bet settled that day,
    sportsbook-labeled and grouped by sport, plus the day's activity."""
    from datetime import date as _date

    from .reports import settled_bets

    try:
        d = _date.fromisoformat(day)
    except ValueError:
        raise HTTPException(status_code=400, detail="day must be YYYY-MM-DD") from None
    bets = [b for b in await settled_bets(whale_id) if b["settled_at"].date() == d]
    pool = await get_pool()
    activity = await pool.fetchrow(
        "SELECT count(*)::int AS trades, COALESCE(sum(notional),0)::float8 AS volume "
        "FROM trades WHERE whale_id=$1 AND ts::date = $2",
        whale_id, d,
    )
    by_sport: dict[str, dict] = {}
    for b in bets:
        s = by_sport.setdefault(
            b["sport"] or "unclassified",
            {"sport": b["sport"] or "unclassified", "pnl": 0.0, "stake": 0.0,
             "wins": 0, "losses": 0, "bets": []},
        )
        s["pnl"] += b["pnl"]
        s["stake"] += b["stake"]
        s["wins"] += 1 if b["pnl"] > 0.01 else 0
        s["losses"] += 1 if b["pnl"] < -0.01 else 0
        s["bets"].append(b)
    sports = sorted(by_sport.values(), key=lambda s: s["pnl"], reverse=True)
    for s in sports:
        s["bets"].sort(key=lambda b: b["pnl"], reverse=True)
    return {
        "date": day,
        "pnl": round(sum(b["pnl"] for b in bets), 2),
        "stake": round(sum(b["stake"] for b in bets), 2),
        "wins": sum(1 for b in bets if b["pnl"] > 0.01),
        "losses": sum(1 for b in bets if b["pnl"] < -0.01),
        "settled_count": len(bets),
        "trades_placed": activity["trades"],
        "volume_placed": activity["volume"],
        "sports": sports,
    }


@app.get("/api/signal/{condition_id}")
async def api_signal(condition_id: str) -> dict:
    """Live whale positioning for one market — the edge engine's alignment
    feature: are the tracked top traders on this outcome right now?"""
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT w.username, w.id AS whale_id, ap.outcome, ap.size::float8 AS size,
               ap.avg_price::float8 AS avg_price,
               COALESCE(ap.current_value, ap.size * ap.avg_price)::float8 AS value
        FROM api_positions ap JOIN whales w ON w.id = ap.whale_id
        WHERE ap.condition_id = $1 AND ap.size > 0 AND w.active
        ORDER BY value DESC
        """,
        condition_id,
    )
    recent = await pool.fetch(
        """
        SELECT w.username, t.side, t.outcome, t.price::float8 AS price,
               t.notional::float8 AS notional, t.ts
        FROM trades t JOIN whales w ON w.id = t.whale_id
        WHERE t.condition_id = $1 AND t.ts > now() - interval '48 hours'
        ORDER BY t.ts DESC LIMIT 20
        """,
        condition_id,
    )
    return {
        "condition_id": condition_id,
        "positions": [dict(r) for r in rows],
        "recent_trades": [dict(r) for r in recent],
    }


@app.get("/api/admin/calibration", dependencies=[Depends(require_admin)])
async def admin_calibration(window_days: int = Query(90, le=730)) -> dict:
    """Rolling recalibration of the edge-engine's measured tables from the
    live whale ledger — band/league/size edges, Phase-1 methodology."""
    from ..analytics.calibration import full_report

    return await full_report(window_days)


@app.get("/api/admin/diag", dependencies=[Depends(require_admin)])
async def admin_diag() -> dict:
    """Live probes of every upstream API, with response snippets — run this
    when data looks wrong; it shows exactly what production sees."""
    import time as _time

    import httpx

    from ..gamma import _OPEN_MARKET_PARAM_VARIANTS

    cfg = settings()
    pool = await get_pool()
    out: dict = {}

    async def probe(client: httpx.AsyncClient, key: str, url: str, params: dict | None = None):
        try:
            resp = await client.get(url, params=params)
            body = resp.text[:220]
            out[key] = {"status": resp.status_code, "body": body}
        except Exception as exc:  # noqa: BLE001
            out[key] = {"status": "error", "body": str(exc)[:220]}

    sample_cid = await pool.fetchval(
        "SELECT condition_id FROM trades WHERE condition_id IS NOT NULL LIMIT 1"
    )
    sample_addr = await pool.fetchval("SELECT address FROM whales WHERE active LIMIT 1")

    async with httpx.AsyncClient(timeout=10) as http:
        for i, variant in enumerate(_OPEN_MARKET_PARAM_VARIANTS):
            await probe(http, f"gamma_open_v{i}", f"{cfg.gamma_api_base}/markets",
                        {**variant, "limit": 1, "offset": 0})
        if sample_cid:
            await probe(http, "gamma_condition_ids", f"{cfg.gamma_api_base}/markets",
                        {"condition_ids": sample_cid})
            await probe(http, "clob_market", f"{cfg.clob_api_base}/markets/{sample_cid}")
        if sample_addr:
            now = int(_time.time())
            await probe(http, "dataapi_offset_10k", f"{cfg.data_api_base}/trades",
                        {"user": sample_addr, "limit": 1, "offset": 10_000})
            for pname in ("before", "endTs", "to", "max_ts"):
                await probe(http, f"dataapi_timeparam_{pname}", f"{cfg.data_api_base}/trades",
                            {"user": sample_addr, "limit": 1, pname: now - 86400 * 30})
    return out


@app.get("/api/whales/{whale_id}/settled-report.pdf")
async def api_whale_settled_report(whale_id: int):
    from fastapi.responses import Response

    from .reports import build_settled_report

    try:
        pdf, filename = await build_settled_report(whale_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="unknown whale") from None
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/whales/{whale_id}/report.pdf")
async def api_whale_report(
    whale_id: int,
    period: str = Query("monthly", pattern="^(weekly|monthly)$"),
    end: str | None = None,
):
    from datetime import date as _date

    from .reports import build_report

    try:
        end_date = _date.fromisoformat(end) if end else None
    except ValueError:
        raise HTTPException(status_code=400, detail="end must be YYYY-MM-DD") from None
    try:
        pdf, filename = await build_report(whale_id, period, end_date)
    except LookupError:
        raise HTTPException(status_code=404, detail="unknown whale") from None
    from fastapi.responses import Response

    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/matrix")
async def api_matrix(window: str = Query("all", pattern="^(7d|30d|all)$")) -> dict:
    return await queries.matrix(window)


@app.get("/api/events")
async def api_events(limit: int = Query(50, le=200)) -> list[dict]:
    return await queries.events_view(limit)


# ── Push subscription + prefs ───────────────────────────────────────


class PushSubscribeBody(BaseModel):
    user_key: str
    endpoint: str
    p256dh: str
    auth: str


@app.post("/api/push/subscribe")
async def push_subscribe(body: PushSubscribeBody) -> dict:
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO push_subscriptions (user_key, endpoint, p256dh, auth)
        VALUES ($1,$2,$3,$4)
        ON CONFLICT (endpoint) DO UPDATE SET user_key=$1, p256dh=$3, auth=$4
        """,
        body.user_key, body.endpoint, body.p256dh, body.auth,
    )
    return {"ok": True}


class PushUnsubscribeBody(BaseModel):
    endpoint: str


@app.post("/api/push/unsubscribe")
async def push_unsubscribe(body: PushUnsubscribeBody) -> dict:
    pool = await get_pool()
    await pool.execute("DELETE FROM push_subscriptions WHERE endpoint=$1", body.endpoint)
    return {"ok": True}


class PrefsBody(BaseModel):
    min_notional: float = 0
    muted_whales: list[int] = []
    sports: list[str] = []


@app.get("/api/prefs/{user_key}")
async def get_prefs(user_key: str) -> dict:
    pool = await get_pool()
    row = await pool.fetchrow("SELECT * FROM user_prefs WHERE user_key=$1", user_key)
    if row is None:
        return {"user_key": user_key, "min_notional": 0, "muted_whales": [], "sports": []}
    d = dict(row)
    for k in ("muted_whales", "sports"):
        if isinstance(d[k], str):
            d[k] = json.loads(d[k])
    d["min_notional"] = float(d["min_notional"])
    return d


@app.put("/api/prefs/{user_key}")
async def put_prefs(user_key: str, body: PrefsBody) -> dict:
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO user_prefs (user_key, min_notional, muted_whales, sports, updated_at)
        VALUES ($1,$2,$3::jsonb,$4::jsonb,now())
        ON CONFLICT (user_key) DO UPDATE SET min_notional=$2, muted_whales=$3::jsonb,
                                             sports=$4::jsonb, updated_at=now()
        """,
        user_key, body.min_notional, json.dumps(body.muted_whales), json.dumps(body.sports),
    )
    return {"ok": True}


# ── Admin ───────────────────────────────────────────────────────────


@app.get("/api/admin/health", dependencies=[Depends(require_admin)])
async def admin_health() -> dict:
    pool = await get_pool()
    beats = await pool.fetch("SELECT * FROM service_heartbeats ORDER BY service")
    recon = await pool.fetch(
        "SELECT * FROM reconciliation_runs ORDER BY id DESC LIMIT 5"
    )
    outbox = await pool.fetchrow(
        """
        SELECT count(*) FILTER (WHERE NOT sent) AS pending,
               count(*) FILTER (WHERE sent) AS sent,
               count(*) FILTER (WHERE collapsed) AS collapsed
        FROM notification_outbox
        """
    )
    subs = await pool.fetchval("SELECT count(*) FROM push_subscriptions")
    return {
        "heartbeats": [dict(b) for b in beats],
        "reconciliation": [dict(r) for r in recon],
        "outbox": dict(outbox) if outbox else {},
        "push_subscriptions": subs,
    }


@app.get("/api/admin/latency", dependencies=[Depends(require_admin)])
async def admin_latency(hours: int = Query(24, le=24 * 30)) -> dict:
    return await queries.latency_stats(hours)


@app.get("/api/admin/roster", dependencies=[Depends(require_admin)])
async def admin_roster() -> dict:
    pool = await get_pool()
    events = await pool.fetch("SELECT * FROM roster_events ORDER BY id DESC LIMIT 20")
    return {
        "whales": await queries.whales(include_inactive=True),
        "events": [dict(e) for e in events],
    }


class RosterActionBody(BaseModel):
    whale_id: int | None = None
    address: str | None = None
    username: str | None = None


async def _whale_by_body(body: RosterActionBody) -> dict | None:
    pool = await get_pool()
    if body.whale_id is not None:
        row = await pool.fetchrow("SELECT * FROM whales WHERE id=$1", body.whale_id)
    elif body.address:
        row = await pool.fetchrow("SELECT * FROM whales WHERE address=$1", body.address.lower())
    else:
        return None
    return dict(row) if row else None


@app.post("/api/admin/roster/{action}", dependencies=[Depends(require_admin)])
async def admin_roster_action(action: str, body: RosterActionBody) -> dict:
    pool = await get_pool()
    if action == "refresh":
        return await roster_svc.refresh_roster()
    if action == "pin" and body.address and not await _whale_by_body(body):
        # Pin a wallet not yet tracked: insert it directly.
        await pool.execute(
            "INSERT INTO whales (address, username, pinned, active) VALUES ($1,$2,TRUE,TRUE) "
            "ON CONFLICT (address) DO UPDATE SET pinned=TRUE, active=TRUE, removed_at=NULL",
            body.address.lower(), body.username,
        )
        await pool.execute(
            "INSERT INTO roster_events (kind, detail) VALUES ('pinned', $1::jsonb)",
            json.dumps({"address": body.address.lower()}),
        )
        return {"ok": True}
    whale = await _whale_by_body(body)
    if whale is None:
        raise HTTPException(status_code=404, detail="unknown whale")
    updates = {
        "pin": "UPDATE whales SET pinned=TRUE, active=TRUE, removed_at=NULL WHERE id=$1",
        "unpin": "UPDATE whales SET pinned=FALSE WHERE id=$1",
        "ban": "UPDATE whales SET banned=TRUE, active=FALSE, removed_at=now() WHERE id=$1",
        "unban": "UPDATE whales SET banned=FALSE WHERE id=$1",
        "deactivate": "UPDATE whales SET active=FALSE, removed_at=now() WHERE id=$1",
        "activate": "UPDATE whales SET active=TRUE, removed_at=NULL WHERE id=$1",
    }
    sql = updates.get(action)
    if sql is None:
        raise HTTPException(status_code=400, detail=f"unknown action {action}")
    await pool.execute(sql, whale["id"])
    await pool.execute(
        "INSERT INTO roster_events (kind, whale_id) VALUES ($1, $2)", action, whale["id"]
    )
    return {"ok": True}
