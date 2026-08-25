"""FastAPI application: REST + SSE stream + push subscription + admin."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from .. import roster as roster_svc
from ..bus import CH_HEALTH, CH_TRADES_ENRICHED, CH_TRADES_NEW, get_redis
from ..config import settings
from ..db import close_pool, get_pool
from . import queries
from .grading import grade_rows

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Boot must not require a healthy database. get_pool() retries lazily
    # on first use; dying here just turns a DB hiccup into a full outage.
    try:
        await get_pool()
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).exception(
            "DB unavailable at boot — serving anyway, will retry lazily")
    # INGESTION FALLBACK (2026-08-02). The workers service died on Jul 27
    # and stayed dead for six days because nothing else could do its job;
    # whale detection is the platform's heartbeat and must not depend on a
    # single service's health. The poller runs here too. Coexistence with
    # a revived workers service is safe by construction: trades dedupe on
    # dedupe_key, the outbox on (trade_id, kind), and live copy orders on
    # trade_id — two pollers can never double-ingest or double-order.
    # API_INGESTION_FALLBACK=0 turns this off.
    import asyncio
    import os as _os

    poller_task = None
    # DEFAULT OFF (2026-08-03 00:0x). The fallback proved the pipeline
    # (poller heartbeat went fresh at 23:55Z after six dead days, live
    # detections flowed) — and then proved the instance can't carry it:
    # whale-rate ingestion plus per-detection probes/mapping OOM-flapped
    # the API on a ~4-minute cycle even with the history loop off, the
    # probe burst gated, and a delayed start. Serving the site is this
    # service's job; ingestion belongs on the workers service, which now
    # boots cleanly on the fixed schema. Set API_INGESTION_FALLBACK=1
    # only for short-lived diagnostic use.
    if _os.getenv("API_INGESTION_FALLBACK", "0") == "1":
        async def _delayed_poller():
            # Let the service finish booting and pass its health check
            # BEFORE the polling load starts — a heavy startup on a small
            # instance reads as a failed deploy and extends the outage.
            await asyncio.sleep(45)
            from ..ingestion.poller import Poller

            # history=False: live detection only. The deep-history backfill
            # pages millions of trades and OOM-cycled this service when the
            # fallback first shipped with it on.
            await Poller().run(history=False)

        try:
            poller_task = asyncio.get_running_loop().create_task(_delayed_poller())
            logging.getLogger(__name__).warning(
                "ingestion fallback: poller (live-only, delayed 45s) in API")
        except Exception:  # noqa: BLE001 — the API must serve regardless
            logging.getLogger(__name__).exception("ingestion fallback failed")
    # Warm the track-record snapshot (including the post-deploy deep
    # activity sweep) BEFORE the first visitor, so no page load ever waits
    # on the venue's 20-80 serial REST calls.
    from .track_record import warm_cache

    asyncio.get_running_loop().create_task(warm_cache())
    # Whale-identities snapshot refresher: the engine's Kalshi copy sweep
    # reads /api/whale-open-identities behind a 30s timeout; the query
    # can take longer under evening ingest load, so it must never run on
    # the request path (starved the copy leg 3x on 2026-08-05).
    asyncio.get_running_loop().create_task(refresh_whale_idents_loop())

    # ONE-SHOT RESTATEMENT (owner emergency 2026-08-23): re-score every
    # settled US-venue copy row since Aug 1 from the venue's own ledger.
    # The old settlement sweep graded rows by the whale's global token,
    # so the stored record is corrupt; this rewrites it from ground
    # truth exactly once (state-key guarded), after boot has settled.
    async def _rescore_once():
        await asyncio.sleep(75)
        try:
            pool = await get_pool()
            key = "rescore_copies_v2"
            done = await pool.fetchval(
                "SELECT value FROM ingestion_state WHERE key=$1", key)
            if done:
                return
            from ..analytics.engine import _settle_pmus_from_venue

            summary = await _settle_pmus_from_venue(
                pool, rescore_since="2026-08-01")
            summary["at"] = datetime.now(timezone.utc).isoformat()
            await pool.execute(
                "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
                "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
                key, json.dumps(summary))
            logging.getLogger(__name__).warning("rescore v1: %s", summary)
        except Exception:  # noqa: BLE001 — retried on next boot
            logging.getLogger(__name__).exception(
                "rescore v1 failed (will retry next boot)")

    asyncio.get_running_loop().create_task(_rescore_once())
    yield
    if poller_task is not None:
        poller_task.cancel()
    await close_pool()


app = FastAPI(title="SportsAssets Hub API", lifespan=lifespan)

_origins = [o.strip() for o in settings().cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins or ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
# The track-record payload alone is hundreds of KB of highly repetitive
# JSON, polled every 30s by every open tab — uncompressed it dominated
# page-load time on mobile. ~10x smaller on the wire with gzip.
app.add_middleware(GZipMiddleware, minimum_size=1024)

# Which frontends actually talk to this API? The deployed site's hostname
# is recorded nowhere (Netlify names are set in its UI), which has made
# "is the new build live?" unanswerable by probes twice now. Real browser
# traffic is ground truth: remember the distinct Origin/Referer hosts.
# Hostnames only — no paths, tokens, IPs, or user data — and the list is
# capped so it cannot grow unboundedly.
_SEEN_ORIGINS: dict[str, float] = {}


@app.middleware("http")
async def _track_origins(request, call_next):
    raw = request.headers.get("origin") or request.headers.get("referer") or ""
    # OPTIONS excluded: the diagnostic probe's CORS-preflight sweep sends
    # candidate Origins and polluted the list with its own guesses. Real
    # browsers follow every preflight with the actual GET/POST.
    if raw and request.method != "OPTIONS":
        try:
            from urllib.parse import urlsplit

            host = urlsplit(raw).netloc.lower()
            if host and (host in _SEEN_ORIGINS or len(_SEEN_ORIGINS) < 40):
                _SEEN_ORIGINS[host] = time.time()
        except Exception:
            pass
    return await call_next(request)


@app.get("/api/system/seen-origins")
async def seen_origins():
    now = time.time()
    return {"origins": [
        {"host": h, "ago_s": round(now - t, 1)}
        for h, t in sorted(_SEEN_ORIGINS.items(), key=lambda kv: -kv[1])
    ]}


def require_admin(x_admin_token: str = Header(default="")) -> None:
    import hmac

    # Whitespace-tolerant compare: mobile keyboards append spaces/newlines,
    # and env-var values sometimes carry a trailing newline.
    supplied = (x_admin_token or "").strip()
    expected = (settings().admin_token or "").strip()
    if not expected or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="admin token required")


# ── Desk auth (owner directive 2026-08-22) ──────────────────────────
# The trading desk unlocks with its own password (DESK_PASSWORD) so the
# admin token never has to live in a phone browser. A successful unlock
# mints a stateless 12h token: "<exp>.<hmac_sha256(admin_token,
# 'desk:'+exp)>" — verifiable on any instance without a session store,
# and rotated for free whenever the admin token rotates. Tokens are
# never logged.
DESK_TOKEN_TTL_S = 12 * 3600


def mint_desk_token(now: float | None = None) -> tuple[str, int]:
    import hashlib
    import hmac as _hmac
    import time as _t

    exp = int(now if now is not None else _t.time()) + DESK_TOKEN_TTL_S
    key = (settings().admin_token or "").strip().encode()
    sig = _hmac.new(key, f"desk:{exp}".encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{sig}", exp


def desk_token_ok(token: str, now: float | None = None) -> bool:
    import hashlib
    import hmac as _hmac
    import time as _t

    if not isinstance(token, str):
        return False
    tok = token.strip()
    exp_s, sep, sig = tok.partition(".")
    if not sep:
        return False
    try:
        exp = int(exp_s)
    except ValueError:
        return False
    if exp <= (now if now is not None else _t.time()):
        return False
    key = (settings().admin_token or "").strip().encode()
    if not key:
        return False
    want = _hmac.new(key, f"desk:{exp}".encode(),
                     hashlib.sha256).hexdigest()
    return _hmac.compare_digest(sig, want)


# ── Wall auth (TV wall, 2026-08-23) ─────────────────────────────────
# The office TV runs unattended for weeks, so its token lives 7 days
# and rolls itself over (see /api/wall/renew). Same stateless HMAC
# shape as desk tokens, keyed by the same admin token, with a distinct
# scope string — 'wall:' vs 'desk:' — so neither kind ever verifies as
# the other. Wall is strictly read-only. Tokens are never logged.
WALL_TOKEN_TTL_S = 7 * 24 * 3600


def mint_wall_token(now: float | None = None) -> tuple[str, int]:
    import hashlib
    import hmac as _hmac
    import time as _t

    exp = int(now if now is not None else _t.time()) + WALL_TOKEN_TTL_S
    key = (settings().admin_token or "").strip().encode()
    sig = _hmac.new(key, f"wall:{exp}".encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{sig}", exp


def wall_token_ok(token: str, now: float | None = None) -> bool:
    import hashlib
    import hmac as _hmac
    import time as _t

    if not isinstance(token, str):
        return False
    tok = token.strip()
    exp_s, sep, sig = tok.partition(".")
    if not sep:
        return False
    try:
        exp = int(exp_s)
    except ValueError:
        return False
    if exp <= (now if now is not None else _t.time()):
        return False
    key = (settings().admin_token or "").strip().encode()
    if not key:
        return False
    want = _hmac.new(key, f"wall:{exp}".encode(),
                     hashlib.sha256).hexdigest()
    return _hmac.compare_digest(sig, want)


def require_desk(x_desk_token: str = Header(default=""),
                 x_admin_token: str = Header(default="")) -> str:
    """Desk-scoped auth: a valid desk token, a valid wall token, OR the
    admin token. The admin path keeps working so existing tooling never
    breaks. Returns the caller's role ('admin'|'desk'|'wall') — endpoints
    that shape their payload per role read it via Depends; everyone else
    ignores it. 'wall' is read-only: every mutating endpoint must check
    the role and refuse it with a 403."""
    import hmac

    supplied = (x_admin_token or "").strip()
    expected = (settings().admin_token or "").strip()
    if expected and hmac.compare_digest(supplied, expected):
        return "admin"
    if x_desk_token and desk_token_ok(x_desk_token):
        return "desk"
    if x_desk_token and wall_token_ok(x_desk_token):
        return "wall"
    raise HTTPException(status_code=401, detail="desk unlock required")


def check_engine_token(supplied: str | None) -> None:
    """Engine-feed auth (audit 2026-08-21): every engine endpoint was
    comparing with != — non-constant-time, and unlike the admin path,
    unstripped (a trailing-newline env var silently 401'd the whole
    engine). Same discipline as require_admin, one place."""
    import hmac

    got = (supplied or "").strip()
    expected = (settings().engine_ingest_token or "").strip()
    if not expected or not hmac.compare_digest(got, expected):
        raise HTTPException(status_code=401, detail="engine token required")


# ── Health & config ─────────────────────────────────────────────────


@app.get("/healthz")
async def healthz() -> dict:
    import os

    # Health means "this process can serve" — the DB gets its own field
    # instead of a veto. A 502ing health check during a DB hiccup turns a
    # degraded product into a dead one (observed 2026-08-03: continuous
    # platform 502s because every boot died before serving).
    db_ok = False
    try:
        pool = await get_pool()
        await pool.fetchval("SELECT 1")
        db_ok = True
    except Exception:  # noqa: BLE001
        pass
    # Current RSS from /proc: after a night of OOM archaeology-by-email,
    # memory is a number the probes can track, not a timeline to argue.
    rss_mb = None
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    rss_mb = round(int(line.split()[1]) / 1024, 1)
                    break
    except OSError:
        pass
    # Render injects the deployed commit — lets anyone confirm which build is live.
    return {"ok": True, "db_ok": db_ok,
            "commit": (os.getenv("RENDER_GIT_COMMIT") or "")[:7],
            "rss_mb": rss_mb}


@app.get("/api/health/services")
async def health_services() -> list[dict]:
    """Sanitized service heartbeats — status and age only, plus a short
    error hint. The whale poller failed silently for six days (Jul 27 -
    Aug 2, 2026) because its error heartbeats were admin-gated and nobody
    was looking; a pipeline's liveness must be publicly checkable. No
    payloads, no tokens: service name, status, age, truncated error."""
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT service, status, beat_at, detail FROM service_heartbeats "
        "ORDER BY service")
    out = []
    for r in rows:
        detail = r["detail"]
        if isinstance(detail, str):
            try:
                detail = json.loads(detail)
            except ValueError:
                detail = {}
        err = str((detail or {}).get("error") or "")[:160]
        out.append({"service": r["service"], "status": r["status"],
                    "beat_at": r["beat_at"],
                    # Sweep counters are diagnostics, not payloads: the
                    # underdog sleeve's per-gate stats are how "running
                    # but placing nothing" gets localized in one probe
                    # (owner report 2026-08-08). Numbers and short
                    # strings only, capped.
                    "detail": {k: (v if isinstance(v, (int, float, bool))
                                   else str(v)[:80])
                               for k, v in (detail or {}).items()
                               if k != "error"} or None,
                    **({"error": err} if err else {})})
    return out


# Ping throttle (audit 2026-08-21): the unlock diagnostic is a public
# yes/no oracle for token guesses and the app has no other rate limit.
# 10 attempts/min/IP is far above any human retyping a token and far
# below a useful brute force.
_PING_HITS: dict[str, list[float]] = {}


@app.post("/api/admin/ping")
async def admin_ping(request: Request,
                     x_admin_token: str = Header(default="")) -> dict:
    """Unlock diagnostic. Reveals nothing about the token's value — only
    whether a non-default token is configured on the server and whether this
    attempt matched, so the UI can say 'wrong token' vs 'env not applied'
    instead of one ambiguous failure message."""
    import hmac
    import time as _t

    ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() \
        or (request.client.host if request.client else "?")
    now = _t.time()
    hits = [t for t in _PING_HITS.get(ip, []) if now - t < 60]
    if len(hits) >= 10:
        raise HTTPException(status_code=429, detail="slow down")
    hits.append(now)
    _PING_HITS[ip] = hits
    if len(_PING_HITS) > 1000:      # bound the map; drop stale IPs
        for k in [k for k, v in _PING_HITS.items()
                  if not v or now - v[-1] > 300][:500]:
            _PING_HITS.pop(k, None)
    supplied = (x_admin_token or "").strip()
    expected = (settings().admin_token or "").strip()
    return {
        "received_chars": len(supplied),
        "configured": bool(expected) and expected != "change-me",
        "match": bool(expected) and hmac.compare_digest(supplied, expected),
    }


# Unlock throttle: same shape and rationale as _PING_HITS — the desk
# password is short by design, so the guess oracle must be slow.
_UNLOCK_HITS: dict[str, list[float]] = {}


def _throttled(hits: dict[str, list[float]], request: Request,
               limit: int = 10, window: float = 60.0) -> bool:
    """True when this IP is over its budget (the _PING_HITS pattern,
    shared). Records the attempt when allowed; bounds the map."""
    import time as _t

    ip = (request.headers.get("x-forwarded-for") or "").split(",")[0] \
        .strip() or (request.client.host if request.client else "?")
    now = _t.time()
    recent = [t for t in hits.get(ip, []) if now - t < window]
    if len(recent) >= limit:
        hits[ip] = recent
        return True
    recent.append(now)
    hits[ip] = recent
    if len(hits) > 1000:      # bound the map; drop stale IPs
        for k in [k for k, v in hits.items()
                  if not v or now - v[-1] > 300][:500]:
            hits.pop(k, None)
    return False


class DeskUnlockBody(BaseModel):
    password: str = ""


@app.post("/api/desk/unlock")
async def desk_unlock(request: Request, body: DeskUnlockBody) -> dict:
    """Trade-desk unlock: password -> short-lived desk token. The
    password is compared constant-time; the response never carries the
    configured value, and nothing here is ever logged."""
    import hmac

    if _throttled(_UNLOCK_HITS, request):
        raise HTTPException(status_code=429, detail="slow down")
    supplied = (body.password or "").strip()
    expected = (settings().desk_password or "").strip()
    if not expected or not hmac.compare_digest(supplied, expected):
        return {"ok": False, "error": "wrong password"}
    token, exp = mint_desk_token()
    return {"ok": True, "token": token, "expires_at": exp}


# ── TV wall (2026-08-23) ────────────────────────────────────────────
# One password (the desk's), a long-lived read-only token, and a tiny
# in-memory switch the desk flips to steer what every TV shows. The
# state is per-instance and non-durable by design: a restart falls
# back to the live book, which is always safe to display.
_wall_state: dict = {"mode": "book", "from": None, "to": None,
                     "set_at": None, "headline": None, "ttl_s": None,
                     "screens": None}


@app.post("/api/wall/unlock")
async def wall_unlock(request: Request, body: DeskUnlockBody) -> dict:
    """Wall unlock: the desk password -> a 7-day read-only wall token.
    Shares the desk throttle bucket so the two endpoints are one guess
    oracle, not two. Constant-time compare; the response never carries
    the configured value, and nothing here is ever logged."""
    import hmac

    if _throttled(_UNLOCK_HITS, request):
        raise HTTPException(status_code=429, detail="slow down")
    supplied = (body.password or "").strip()
    expected = (settings().desk_password or "").strip()
    if not expected or not hmac.compare_digest(supplied, expected):
        return {"ok": False, "error": "wrong password"}
    token, exp = mint_wall_token()
    return {"ok": True, "token": token, "expires_at": exp}


@app.post("/api/wall/renew")
async def wall_renew(x_desk_token: str = Header(default="")) -> dict:
    """Rolling renewal: a still-valid WALL token buys a fresh 7-day
    one, so an always-on TV never sees the password again. Desk tokens
    and everything else get a 401 — renewal must never be a way to
    stretch a 12h desk grant into a week."""
    if not wall_token_ok(x_desk_token):
        raise HTTPException(status_code=401, detail="wall token required")
    token, exp = mint_wall_token()
    return {"ok": True, "token": token, "expires_at": exp}


@app.get("/api/wall/state", dependencies=[Depends(require_desk)])
async def wall_state() -> dict:
    """What every TV should show right now. Any role may read."""
    return dict(_wall_state)


class WallBroadcastBody(BaseModel):
    mode: str = "book"
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    # SCENE MODE (owner 2026-08-23: "Meridian should use both screens"):
    # MERIDIAN commands the walls — a headline it writes across both
    # TVs, an optional per-screen directive, and a TTL after which the
    # walls fall back to the live books on their own.
    headline: str | None = None
    ttl_s: int | None = None
    screens: dict | None = None


# The only board kinds a wall knows how to draw, and the only param keys a
# screen directive may carry — everything else is dropped, not stored.
_WALL_KINDS = ("book", "report", "chart", "whales", "headline")
_WALL_SCREEN_KEYS = ("kind", "from", "to", "text", "stat")


def _clean_wall_chart(raw: object) -> dict | None:
    """A projected chart (MERIDIAN throwing any series onto a TV) —
    hard-capped so wall state stays a small dict, never a data sink:
    <=3 series x <=160 finite numbers, short strings only."""
    if not isinstance(raw, dict):
        return None
    series_in = raw.get("series")
    if not isinstance(series_in, list):
        return None
    series = []
    for s in series_in[:3]:
        if not isinstance(s, dict):
            continue
        vals_in = s.get("values")
        if not isinstance(vals_in, list):
            continue
        vals = []
        for v in vals_in[:160]:
            try:
                f = float(v)
            except (TypeError, ValueError):
                continue
            if f == f and abs(f) < 1e12:      # finite, sane magnitude
                vals.append(round(f, 4))
        if len(vals) >= 2:
            name = s.get("name")
            series.append({
                "name": (name if isinstance(name, str) else "")[:40],
                "values": vals,
            })
    if not series:
        return None
    out: dict = {"series": series}
    title = raw.get("title")
    if isinstance(title, str) and title.strip():
        out["title"] = title[:120]
    labels_in = raw.get("labels")
    if isinstance(labels_in, list):
        labels = [str(x)[:12] for x in labels_in[:160]
                  if isinstance(x, (str, int, float))]
        if labels:
            out["labels"] = labels
    kind = raw.get("kind")
    if kind in ("line", "bar"):
        out["kind"] = kind
    return out


def _clean_wall_screens(raw: dict | None) -> dict | None:
    if not isinstance(raw, dict):
        return None
    out: dict = {}
    for venue in ("kalshi", "polymarket"):
        d = raw.get(venue)
        if not isinstance(d, dict):
            continue
        kind = d.get("kind")
        if kind not in _WALL_KINDS:
            continue
        cleaned = {}
        for k in _WALL_SCREEN_KEYS:
            v = d.get(k)
            if isinstance(v, str) and v.strip():
                cleaned[k] = v[:200]
        cleaned["kind"] = kind
        chart = _clean_wall_chart(d.get("chart"))
        if chart is not None:
            cleaned["chart"] = chart
        out[venue] = cleaned
    return out or None


@app.post("/api/wall/broadcast")
async def wall_broadcast(body: WallBroadcastBody,
                         role: str = Depends(require_desk)) -> dict:
    """Steer every TV: the live book, a date-ranged report, or a
    MERIDIAN scene (per-screen visuals + a headline + a TTL that hands
    the walls back to the books). The wall itself may not steer the
    wall — read-only means read-only."""
    if role == "wall":
        raise HTTPException(status_code=403, detail="wall is read-only")
    if body.mode not in ("book", "report", "scene"):
        raise HTTPException(status_code=400,
                            detail="mode must be 'book', 'report' or 'scene'")
    headline = (body.headline or "").strip()[:120] or None
    ttl = None
    if body.ttl_s is not None:
        ttl = max(60, min(3600, int(body.ttl_s)))
    _wall_state.update({"mode": body.mode, "from": body.from_,
                        "to": body.to, "set_at": time.time(),
                        "headline": headline, "ttl_s": ttl,
                        "screens": _clean_wall_screens(body.screens)})
    return {"ok": True, **_wall_state}


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
    limit: int = Query(50, ge=1, le=200),
    before_id: int | None = None,
    whale_id: int | None = None,
    sport: str | None = None,
    side: str | None = None,
    min_notional: float | None = None,
) -> list[dict]:
    return await queries.feed(limit, before_id, whale_id, sport, side, min_notional)


_whale_idents_cache: dict = {"ts": 0.0, "data": None}
# The identities query walks 7 days of source-whale trades (10-15k rows a
# day per whale) and goes >30s under evening ingest contention even with
# the CTE rewrite. A request-path cache cannot save the ONE consumer —
# the engine sweeps every 10 minutes, so every call was a cold miss and
# paid the full compute (still timing out 21:23Z after the 120s-TTL
# attempt). The snapshot is therefore maintained by a BACKGROUND
# refresher (armed in lifespan): the endpoint always answers instantly
# from the last computed snapshot, however the database is feeling; a
# refresh failure keeps serving the previous snapshot. The consumer's
# own freshness gate is 45 minutes, so a couple minutes of staleness is
# free.
_WHALE_IDENTS_TTL = 90.0


async def _compute_whale_idents() -> dict:
    import time as _time

    from ..config import settings as _settings
    from ..db import get_pool as _get_pool

    now = _time.time()
    pool = await _get_pool()
    # pmus_copied is computed AFTER the DISTINCT ON dedup, never inline:
    # inline, the EXISTS probe ran for every raw trade row in the 7-day
    # scan (a source whale posts ~10-15k fills/day), which blew past the
    # engine sweep's 30s read timeout and silently starved the Kalshi
    # copy leg for hours (observed 14:53Z and 16:06Z, 2026-08-05).
    rows = await pool.fetch(
        """
        WITH latest AS (
            SELECT DISTINCT ON (t.asset)
                   t.asset,
                   COALESCE(t.market_slug, t.event_slug, '') AS slug,
                   t.outcome, t.price::float8 AS price,
                   w.username AS whale,
                   extract(epoch FROM t.ts)::float8 AS entered_ts,
                   m.title AS market_title
            FROM trades t
            JOIN whales w ON w.id = t.whale_id
            LEFT JOIN markets m ON m.condition_id = t.condition_id
            WHERE t.side = 'BUY'
              AND t.ts > now() - interval '7 days'
              AND lower(w.username) = ANY($1)
              AND COALESCE(m.resolved, false) = false
            ORDER BY t.asset, t.ts DESC
        )
        SELECT l.asset, l.slug, l.outcome, l.price, l.whale, l.entered_ts,
               l.market_title,
               EXISTS (SELECT 1 FROM live_orders lo
                       WHERE lo.asset = l.asset
                         -- 'submitting' counts as copied (review
                         -- 2026-08-10): the event-woken Kalshi sweep
                         -- reacts inside the PMUS order's in-flight
                         -- window, and a not-yet-filled FOK must still
                         -- hold the position or both venues buy it. A
                         -- row that ends unfilled/rejected releases the
                         -- claim on the next refresh.
                         AND lo.status IN ('submitting', 'filled',
                                           'settled')
                         -- Manual-desk rows must not mark a whale
                         -- position as already copied (owner 2026-08-07:
                         -- the desk never impacts autonomous trading).
                         AND COALESCE(lo.whale_username, '') <> 'manual')
                   AS pmus_copied
        FROM latest l
        """,
        sorted(_settings().source_whales()),
    )
    # entered_ts travels with each identity so copy consumers can enforce
    # FRESHNESS — copying a days-old position at today's price is buying
    # fair value minus fees, and preferentially the collapsed ones
    # (audit 2026-08-04). pmus_copied marks positions whose fast PMUS
    # copy already FILLED (or settled): one copy per whale position
    # ACROSS venues (owner directive 2026-08-05), so the Kalshi sweep
    # must skip these rather than duplicate them.
    # `asset` rides along so the engine's Kalshi copies can claim the
    # position back (kalshi_claims) and so the venue split has a stable
    # id to hash on — same id the PMUS paths key on.
    out = {"identities": [{"asset": str(r["asset"]),
                           "slug": r["slug"], "outcome": r["outcome"],
                           "price": r["price"], "whale": r["whale"],
                           "entered_ts": r["entered_ts"],
                           # Full market title rides along (2026-08-17):
                           # the venue-name join can use real player
                           # names instead of slug-truncated surnames.
                           "market_title": r["market_title"],
                           "pmus_copied": bool(r["pmus_copied"])}
                          for r in rows if r["slug"] and r["outcome"]],
           "as_of": now}
    _whale_idents_cache["ts"] = now
    _whale_idents_cache["data"] = out
    return out


async def refresh_whale_idents_loop() -> None:
    """Keep the identities snapshot warm so the engine's 30s-timeout
    fetch NEVER runs the heavy query inline. Armed from lifespan."""
    import asyncio

    while True:
        try:
            await _compute_whale_idents()
        except Exception:  # noqa: BLE001 — keep serving the last snapshot
            logging.getLogger(__name__).exception(
                "whale identities refresh failed; serving last snapshot")
        await asyncio.sleep(_WHALE_IDENTS_TTL)


async def _fresh_whale_idents(fresh_s: float) -> list[dict]:
    """Identity rows for source-whale BUYs in the last fresh_s seconds —
    the same shape as the snapshot, from a cheap bounded scan (minutes,
    not the 7-day walk that forced the snapshot design)."""
    from ..config import settings as _settings
    from ..db import get_pool as _get_pool

    pool = await _get_pool()
    rows = await pool.fetch(
        """
        WITH latest AS (
            SELECT DISTINCT ON (t.asset)
                   t.asset,
                   COALESCE(t.market_slug, t.event_slug, '') AS slug,
                   t.outcome, t.price::float8 AS price,
                   w.username AS whale,
                   extract(epoch FROM t.ts)::float8 AS entered_ts,
                   m.title AS market_title
            FROM trades t
            JOIN whales w ON w.id = t.whale_id
            LEFT JOIN markets m ON m.condition_id = t.condition_id
            WHERE t.side = 'BUY'
              AND t.ts > now() - make_interval(secs => $2)
              AND lower(w.username) = ANY($1)
              AND COALESCE(m.resolved, false) = false
            ORDER BY t.asset, t.ts DESC
        )
        SELECT l.asset, l.slug, l.outcome, l.price, l.whale, l.entered_ts,
               l.market_title,
               EXISTS (SELECT 1 FROM live_orders lo
                       WHERE lo.asset = l.asset
                         -- 'submitting' holds the claim here too — this
                         -- fresh tail is exactly what the event-woken
                         -- sweep reads mid-race (review 2026-08-10).
                         AND lo.status IN ('submitting', 'filled',
                                           'settled')
                         AND COALESCE(lo.whale_username, '') <> 'manual')
                   AS pmus_copied
        FROM latest l
        """,
        sorted(_settings().source_whales()), float(fresh_s),
    )
    return [{"asset": str(r["asset"]), "slug": r["slug"],
             "outcome": r["outcome"], "price": r["price"],
             "whale": r["whale"], "entered_ts": r["entered_ts"],
             "market_title": r["market_title"],
             "pmus_copied": bool(r["pmus_copied"])}
            for r in rows if r["slug"] and r["outcome"]]


@app.get("/api/whale-open-identities")
async def api_whale_open_identities(
        fresh_s: float | None = None,
        x_engine_token: str = Header(default=""),
        authorization: str = Header(default="")) -> dict:
    """Source whales' open BUY positions as identity rows for the engine's
    whale-alignment tagging: [{slug, outcome}]. Moneyline-shaped consumers
    only — the engine joins on game key + team name at the mapper bar.
    Served from the background-refreshed snapshot; before the first
    refresh completes (cold boot) the caller gets an empty list and picks
    up the real one next sweep rather than waiting on a slow compute.

    fresh_s (2026-08-10, reaction-time work): the engine's copy sweep now
    wakes on fresh-fill events, and the fill that woke it is younger than
    this snapshot's 90s TTL. When set, a bounded fresh-tail query is
    merged over the snapshot (fresh rows win by asset) so the woken sweep
    can actually price the position that woke it. Tail failures serve the
    plain snapshot — freshness is an upgrade, never an outage.

    ENGINE-ONLY (audit 2026-08-21): this feed is, in real time, the list
    of positions the engine is about to copy — publicly it was a
    front-runner's dream. Gated on the engine token; the engine's
    whale_align client sends it as a Bearer header, so both header
    shapes are accepted."""
    bearer = authorization.removeprefix("Bearer ").strip() \
        if authorization.startswith("Bearer ") else ""
    check_engine_token(x_engine_token or bearer)
    data = _whale_idents_cache["data"]
    if data is None:
        return {"identities": [], "as_of": None, "warming": True}
    if not fresh_s or fresh_s <= 0:
        return data
    try:
        fresh_rows = await _fresh_whale_idents(min(float(fresh_s), 900.0))
    except Exception:  # noqa: BLE001 — snapshot alone is today's behavior
        logging.getLogger(__name__).exception("fresh-tail identities failed")
        return data
    if not fresh_rows:
        return data
    by_asset = {i["asset"]: i for i in data["identities"]}
    for r in fresh_rows:
        by_asset[r["asset"]] = r
    return {"identities": list(by_asset.values()), "as_of": data["as_of"],
            "fresh_merged": len(fresh_rows)}


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
    # ET day, not UTC (audit 2026-08-21): the platform's reporting day
    # is US/Eastern everywhere else (track_record.RECORD_TZ) — a 9:30pm
    # ET settlement was landing on tomorrow's drill-down.
    from .track_record import RECORD_TZ
    bets = [b for b in await settled_bets(whale_id)
            if b["settled_at"].astimezone(RECORD_TZ).date() == d]
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


# ── Engine (internal model) fills: record + read ────────────────────


class EngineFillBody(BaseModel):
    ts: float
    venue: str
    market_id: str
    outcome_id: str
    league: str | None = None
    band: str | None = None
    limit_price: float
    size_usd: float
    fair_value: float | None = None
    edge: float | None = None
    would_fill: bool = True
    whale_alignment: dict | None = None
    book_asks: list | None = None
    book_bids: list | None = None


@app.post("/api/engine/fills")
async def engine_fill_ingest(body: EngineFillBody, x_engine_token: str = Header(default="")) -> dict:
    cfg = settings()
    check_engine_token(x_engine_token)
    import hashlib

    from datetime import datetime, timezone

    dedupe = hashlib.sha256(
        f"{body.venue}|{body.outcome_id}|{int(body.ts)}|{body.limit_price}".encode()
    ).hexdigest()
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO engine_fills (ts, venue, market_id, outcome_id, league, band, limit_price,
                                  size_usd, fair_value, edge, would_fill, whale_alignment,
                                  book, dedupe_key)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb,$13::jsonb,$14)
        ON CONFLICT (dedupe_key) DO NOTHING RETURNING id
        """,
        datetime.fromtimestamp(body.ts, tz=timezone.utc), body.venue, body.market_id,
        body.outcome_id, body.league, body.band, body.limit_price, body.size_usd,
        body.fair_value, body.edge, body.would_fill,
        json.dumps(body.whale_alignment) if body.whale_alignment is not None else None,
        json.dumps({"asks": body.book_asks or [], "bids": body.book_bids or []}),
        dedupe,
    )
    return {"ok": True, "id": row["id"] if row else None, "duplicate": row is None}


class EngineStatusBody(BaseModel):
    status: str = "ok"
    detail: dict = {}


# Posts from engine processes that predate the boot stamp are DROPPED and
# counted. Discovered 2026-08-04: a stale engine instance survived deploys
# and kept overwriting the status row with old-code heartbeats, poisoning
# every remote diagnosis for hours ("Deploy live" on the new build while
# probes only ever saw the old one). Only a stamped process — the current
# build — may write telemetry; the drop counter keeps the stray VISIBLE.
_unstamped_drops = {"n": 0, "last_at": 0.0}


@app.post("/api/engine/status")
async def engine_status_ingest(
    body: EngineStatusBody, x_engine_token: str = Header(default="")
) -> dict:
    cfg = settings()
    check_engine_token(x_engine_token)
    if not (body.detail or {}).get("boot"):
        _unstamped_drops["n"] += 1
        _unstamped_drops["last_at"] = time.time()
        return {"ok": False, "ignored": "unstamped legacy process"}
    from ..db import heartbeat

    await heartbeat("edge_engine", body.status, body.detail)
    return {"ok": True}


class KalshiClaimBody(BaseModel):
    asset: str
    ticker: str = ""
    whale: str = ""


@app.post("/api/engine/kalshi-claim")
async def kalshi_claim_ingest(
    body: KalshiClaimBody, x_engine_token: str = Header(default="")
) -> dict:
    """The engine's Kalshi sleeve reports each FILLED copy here so the
    PMUS paths never buy the same whale position twice (one copy per
    position ACROSS venues — owner rule). Idempotent by asset."""
    cfg = settings()
    check_engine_token(x_engine_token)
    if not body.asset.strip():
        return {"ok": False, "ignored": "empty asset"}
    pool = await get_pool()
    await pool.execute(
        "INSERT INTO kalshi_claims (asset, whale, ticker) "
        "VALUES ($1, $2, $3) ON CONFLICT (asset) DO NOTHING",
        body.asset.strip(), body.whale[:120], body.ticker[:120])
    return {"ok": True}


# ── Manual trade desk (owner directive 2026-08-07) ───────────────────


@app.get("/api/admin/market-search", dependencies=[Depends(require_desk)])
async def api_market_search(q: str = Query(min_length=2)) -> dict:
    """Exchange-style market browser for the desk: title/slug/outcome
    substring over unresolved markets, grouped per MARKET with each
    outcome's live best ask/bid quoted from the venue book — so the
    desk shows real prices before the ticket, like any exchange UI."""
    import asyncio as _asyncio

    import httpx

    from ..team_aliases import matches as _team_match, terms_of

    pool = await get_pool()
    # Recall-first SQL (any alias of any term), precision in Python
    # (every term must match) — so 'braves ml' finds the Atlanta game
    # whichever word the venue titled it with.
    pats = sorted({a for s in terms_of(q) for a in s})[:12] \
        or [q.strip().lower()]
    conds = []
    for i in range(1, len(pats) + 1):
        conds.append(
            f"(m.title ILIKE '%' || ${i} || '%' "
            f"OR m.slug ILIKE '%' || ${i} || '%' "
            f"OR m.event_title ILIKE '%' || ${i} || '%' "
            f"OR mt.outcome ILIKE '%' || ${i} || '%')")
    rows = await pool.fetch(
        f"""
        SELECT m.slug, m.title, m.event_title, mt.outcome,
               mt.outcome_index, mt.token_id
        FROM markets m
        JOIN market_tokens mt ON mt.condition_id = m.condition_id
        WHERE COALESCE(m.resolved, false) = false
          AND ({' OR '.join(conds)})
        ORDER BY m.slug DESC, mt.outcome_index
        LIMIT 250
        """, *pats)
    markets: dict[str, dict] = {}
    for r in rows:
        m = markets.setdefault(r["slug"], {
            "slug": r["slug"], "title": r["title"],
            "event_title": r["event_title"], "outcomes": []})
        m["outcomes"].append({"outcome": r["outcome"],
                              "asset": str(r["token_id"]),
                              "ask": None, "bid": None})
    out = [m for m in markets.values()
           if _team_match(q, [m["title"], m["event_title"], m["slug"]]
                          + [o["outcome"] for o in m["outcomes"]])][:12]

    async def _quote(client: httpx.AsyncClient, o: dict) -> None:
        try:
            resp = await client.get("/book", params={"token_id": o["asset"]})
            if resp.status_code != 200:
                return
            d = resp.json()
            asks = sorted(float(x["price"]) for x in (d.get("asks") or []))
            bids = sorted((float(x["price"]) for x in (d.get("bids") or [])),
                          reverse=True)
            o["ask"] = asks[0] if asks else None
            o["bid"] = bids[0] if bids else None
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return

    cfg = settings()
    try:
        async with httpx.AsyncClient(base_url=cfg.clob_api_base,
                                     timeout=8) as client:
            await _asyncio.gather(*(_quote(client, o)
                                    for m in out for o in m["outcomes"]))
    except Exception:  # noqa: BLE001 — quotes are an upgrade, not a gate
        pass
    _mirror_dead_prop_sides(out)
    return {"markets": out}


_MIRROR_SUFFIX = re.compile(r"(.*?)\s*[—–-]\s*(yes|no)\s*$", re.IGNORECASE)


def _mirror_dead_prop_sides(mkts: list[dict]) -> None:
    """Route bookless sides through their mirrored sibling listing.

    Owner report 2026-08-21 evening: every prop's No button was dead on
    the desk. The venue lists props as mirrored pairs ('Q — Yes' and
    'Q — No') and only carries a book on the Yes token of each listing —
    the No token's book is empty, so the desk quoted None and disabled
    the side. But No on 'Q — Yes' IS Yes on 'Q — No' (and vice versa):
    identical bet, live book, proven order path. For any outcome with no
    ask whose sibling listing quotes the complementary side, substitute
    the sibling's token/quotes in place — the pick then executes on the
    token that actually trades. Priced outcomes are never touched, and
    with no live sibling the side stays honestly dead."""
    by_base: dict[str, dict[str, dict]] = {}
    for m in mkts:
        mm = _MIRROR_SUFFIX.match(m.get("title") or "")
        if mm:
            by_base.setdefault(mm.group(1).strip().lower(),
                               {})[mm.group(2).lower()] = m
    for pair in by_base.values():
        if "yes" not in pair or "no" not in pair:
            continue
        for mine, sib in ((pair["yes"], pair["no"]),
                          (pair["no"], pair["yes"])):
            for o in mine.get("outcomes") or []:
                if o.get("ask") is not None:
                    continue
                side = (o.get("outcome") or "").strip().lower()
                if side not in ("yes", "no"):
                    continue
                want = "no" if side == "yes" else "yes"
                twin = next(
                    (so for so in (sib.get("outcomes") or [])
                     if (so.get("outcome") or "").strip().lower() == want
                     and so.get("ask") is not None
                     and not so.get("via_sibling")), None)
                if twin is None:
                    continue
                o["asset"] = twin["asset"]
                o["ask"] = twin["ask"]
                o["bid"] = twin.get("bid")
                o["via_sibling"] = True


# Public Kalshi market data (no auth needed for market/book reads).
KALSHI_PUBLIC_API = os.environ.get(
    "KALSHI_PUBLIC_API", "https://api.elections.kalshi.com/trade-api/v2")
# The sports series the desk browses — same universe the engine trades.
_DESK_KALSHI_SERIES = ["KXMLBGAME", "KXWNBAGAME", "KXNBAGAME", "KXNFLGAME",
                       "KXNHLGAME", "KXATPMATCH", "KXWTAMATCH"]


def _kcents(m: dict, key: str) -> float | None:
    """Tolerant price read: the venue migrated int-cent fields to
    string-dollar '*_dollars' twins (learned the hard way in the BTC
    calibration) — accept either, return dollars 0-1."""
    v = m.get(f"{key}_dollars")
    try:
        if v is not None and str(v).strip():
            f = float(v)
            return f if 0 <= f <= 1 else None
    except (TypeError, ValueError):
        pass
    try:
        c = m.get(key)
        if c is not None:
            f = float(c) / 100.0
            return f if 0 <= f <= 1 else None
    except (TypeError, ValueError):
        pass
    return None


def _kvol(m: dict) -> float | None:
    """Kalshi traded volume in DOLLARS — the _kcents discipline without
    the 0-1 clamp: the venue's '*_dollars' string twin wins, the plain
    field is cents /100. Total volume preferred, 24h as fallback; None
    when the venue doesn't say (the feed never invents volume)."""
    for key in ("volume", "volume_24h"):
        v = m.get(f"{key}_dollars")
        try:
            if v is not None and str(v).strip():
                return float(v)
        except (TypeError, ValueError):
            pass
        try:
            c = m.get(key)
            if c is not None:
                return float(c) / 100.0
        except (TypeError, ValueError):
            pass
    return None


def _kalshi_shape(m: dict, series: str) -> dict:
    return {
        "ticker": m.get("ticker"),
        "series": series,
        "title": m.get("title") or "",
        "sub_title": m.get("yes_sub_title") or m.get("subtitle") or "",
        "yes_ask": _kcents(m, "yes_ask"),
        "yes_bid": _kcents(m, "yes_bid"),
        "no_ask": _kcents(m, "no_ask"),
        "no_bid": _kcents(m, "no_bid"),
        "close_time": m.get("close_time"),
        "volume_usd": _kvol(m),
    }



async def _kalshi_fetch_boards(series_list: list[str]) -> list[dict]:
    """Board fetch with per-sport close windows. Tennis carries the
    TOURNAMENT'S close time (KDESKG-T forensics 2026-08-22: US Open
    quali matches, played Aug 26, close Sep 6) — a game-time window
    structurally hides every tennis market, so tennis series fetch
    unwindowed while game sports keep the 7-day slate."""
    tennis = [x for x in series_list
              if x.startswith(("KXATPMATCH", "KXWTAMATCH"))]
    rest = [x for x in series_list if x not in tennis]
    out: list[dict] = []
    if rest:
        out += await _kalshi_fetch(rest, max_close_h=168, cap=None)
    if tennis:
        out += await _kalshi_fetch(tennis, max_close_h=None, cap=None)
    return out

async def _kalshi_fetch(series_list: list[str], q: str = "",
                        max_close_h: int | None = None,
                        cap: int | None = 60) -> list[dict]:
    """Kalshi's open markets for the given series, close-time sorted.

    limit=1000 (the venue max) per series: at 100 a big tournament board
    (ATP mid-major week is 400+ open markets) hid TODAY's matches behind
    far-future rounds — Sam's Tennis tab showed 'no games' during a live
    session (owner report 2026-08-07 evening). A max_close_h window
    trims browse views to the actual slate; search passes None."""
    import asyncio as _asyncio
    import time as _time

    import httpx

    from ..team_aliases import matches as _team_match

    ql = q.strip()
    out: list[dict] = []
    base_params: dict = {"status": "open", "limit": 1000,
                         "min_close_ts": int(_time.time())}
    if max_close_h is not None:
        base_params["max_close_ts"] = int(_time.time()) + max_close_h * 3600

    def _keep(m: dict, series: str) -> None:
        title = m.get("title") or ""
        sub = m.get("yes_sub_title") or m.get("subtitle") or ""
        # Alias-aware: 'braves' finds the game Kalshi titles
        # 'Atlanta at ...' (owner report 2026-08-07).
        if ql and not _team_match(ql, [title, sub, m.get("ticker")]):
            return
        out.append(_kalshi_shape(m, series))

    async def _series(client: httpx.AsyncClient, series: str) -> None:
        try:
            resp = await client.get("/markets", params={
                **base_params, "series_ticker": series})
            if resp.status_code != 200:
                return
            for m in (resp.json().get("markets") or []):
                _keep(m, series)
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return

    async def _series_events(client: httpx.AsyncClient,
                             series: str) -> None:
        # Fallback surface (owner report 2026-08-12: Kalshi mode showed
        # an empty board): /markets came back empty for EVERY series
        # while the diagnostic census — which queries /events with
        # nested markets — was listing live matches at the same moment.
        # A silent venue-side rejection of the /markets param shape
        # must degrade to the call shape proven working, not to an
        # empty desk. Window/price filters re-applied client-side.
        try:
            resp = await client.get("/events", params={
                "series_ticker": series, "status": "open",
                "with_nested_markets": "true", "limit": 200})
            if resp.status_code != 200:
                return
            hi = base_params.get("max_close_ts")
            for ev in (resp.json().get("events") or []):
                for m in (ev.get("markets") or []):
                    if m.get("status") not in (None, "open", "active"):
                        continue
                    ct = m.get("close_time") or ""
                    if hi and ct:
                        try:
                            from datetime import datetime as _dt
                            if _dt.fromisoformat(
                                    ct.replace("Z", "+00:00")
                            ).timestamp() > hi:
                                continue
                        except ValueError:
                            pass
                    _keep(m, series)
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return

    try:
        async with httpx.AsyncClient(base_url=KALSHI_PUBLIC_API,
                                     timeout=10) as client:
            await _asyncio.gather(*(_series(client, s)
                                    for s in series_list))
            if not out:
                await _asyncio.gather(*(_series_events(client, s)
                                        for s in series_list))
    except Exception:  # noqa: BLE001
        pass
    out.sort(key=lambda m: (m.get("close_time") or ""))
    return out[:cap] if cap else out


# ── Kalshi full universe (wave-2 2026-08-22: league=everything) ──────
# One paginated sweep of EVERY open event (politics, econ, weather,
# entertainment — not just the sports series), nested markets included.
# 5-min TTL, its own cache: the sweep is ~3 pages of 200 events and the
# desk's browse/search polling must not re-walk it per request. Capped
# at ~600 events — beyond that the desk is a search box, not a board.
_KALSHI_ALL_TTL_S = 300.0
_KALSHI_ALL_EVENTS_CAP = 600
_kalshi_all_cache: dict = {"ts": 0.0, "events": []}


async def _kalshi_all_open_events() -> list[dict]:
    """ALL open Kalshi events with nested markets, cached 5 minutes.
    Best-effort: a venue error serves the stale sweep (or empty) —
    the desk degrades, it never 500s."""
    import time as _time

    import httpx

    now = _time.time()
    if (now - _kalshi_all_cache["ts"] < _KALSHI_ALL_TTL_S
            and _kalshi_all_cache["events"]):
        return _kalshi_all_cache["events"]
    events: list[dict] = []
    try:
        async with httpx.AsyncClient(base_url=KALSHI_PUBLIC_API,
                                     timeout=10) as client:
            cursor = ""
            while len(events) < _KALSHI_ALL_EVENTS_CAP:
                params: dict = {"status": "open", "limit": 200,
                                "with_nested_markets": "true"}
                if cursor:
                    params["cursor"] = cursor
                resp = await client.get("/events", params=params)
                if resp.status_code != 200:
                    break
                d = resp.json() or {}
                got = d.get("events") or []
                if not got:
                    break
                events.extend(got)
                cursor = d.get("cursor") or ""
                if not cursor:
                    break
    except Exception:  # noqa: BLE001 — stale sweep beats an empty desk
        pass
    events = events[:_KALSHI_ALL_EVENTS_CAP]
    if events:
        _kalshi_all_cache.update(ts=now, events=events)
    return events or _kalshi_all_cache["events"]


def _kalshi_search_all(events: list[dict], q: str,
                       cap: int = 60) -> list[dict]:
    """Alias-aware market search over the full-events sweep — the same
    row shape _kalshi_fetch returns, close-time sorted."""
    from ..team_aliases import matches as _team_match

    out = []
    for ev in events:
        ev_title = ev.get("title") or ""
        for m in (ev.get("markets") or []):
            if m.get("status") not in (None, "open", "active"):
                continue
            title = m.get("title") or ""
            sub = m.get("yes_sub_title") or m.get("subtitle") or ""
            if not _team_match(q, [title, sub, ev_title,
                                   m.get("ticker")]):
                continue
            series = (m.get("ticker") or "").split("-", 1)[0]
            out.append(_kalshi_shape(m, series))
    out.sort(key=lambda m: (m.get("close_time") or ""))
    return out[:cap]


@app.get("/api/admin/kalshi-markets", dependencies=[Depends(require_desk)])
async def api_kalshi_markets(q: str = Query(default="")) -> dict:
    """Search Kalshi's live markets for the desk — event rows with
    Yes/No prices, the venue's own presentation shape. A query searches
    EVERYTHING open (wave-2: the sports-only restriction is gone) —
    full-universe sweep plus the live sports series, deduped; browsing
    with no query stays the sports slate."""
    ql = q.strip()
    sports = await _kalshi_fetch(_DESK_KALSHI_SERIES, q=q)
    if not ql:
        return {"markets": sports}
    everything = _kalshi_search_all(await _kalshi_all_open_events(), ql)
    seen = {m.get("ticker") for m in sports}
    merged = sports + [m for m in everything
                       if m.get("ticker") not in seen]
    merged.sort(key=lambda m: (m.get("close_time") or ""))
    return {"markets": merged[:60]}


@app.get("/api/admin/book", dependencies=[Depends(require_desk)])
async def api_admin_book(venue: str = Query(...),
                         id: str = Query(...)) -> dict:
    """Live order-book depth for the desk ticket — the venue's actual
    liquidity at each level, both sides. Polymarket: token book as-is.
    Kalshi: the public book lists resting BIDS per side; the YES asks
    are the NO bids mirrored through $1."""
    import httpx

    levels: dict = {"bids": [], "asks": []}
    try:
        if venue == "polymarket":
            cfg = settings()
            async with httpx.AsyncClient(base_url=cfg.clob_api_base,
                                         timeout=8) as client:
                resp = await client.get("/book", params={"token_id": id})
            if resp.status_code == 200:
                d = resp.json()
                levels["asks"] = sorted(
                    ([float(x["price"]), float(x["size"])]
                     for x in (d.get("asks") or [])),
                    key=lambda l: l[0])[:5]
                levels["bids"] = sorted(
                    ([float(x["price"]), float(x["size"])]
                     for x in (d.get("bids") or [])),
                    key=lambda l: -l[0])[:5]
        elif venue == "kalshi":
            async with httpx.AsyncClient(base_url=KALSHI_PUBLIC_API,
                                         timeout=8) as client:
                resp = await client.get(f"/markets/{id}/orderbook")
            if resp.status_code == 200:
                ob = (resp.json() or {}).get("orderbook") or {}

                def _lv(raw) -> list[list[float]]:
                    outl = []
                    for lv in raw or []:
                        try:
                            p, c = float(lv[0]), float(lv[1])
                            outl.append([p if p <= 1 else p / 100.0, c])
                        except (TypeError, ValueError, IndexError):
                            continue
                    return outl

                yes_bids = _lv(ob.get("yes_dollars") or ob.get("yes"))
                no_bids = _lv(ob.get("no_dollars") or ob.get("no"))
                levels["bids"] = sorted(yes_bids, key=lambda l: -l[0])[:5]
                levels["asks"] = sorted(
                    ([round(1 - p, 2), c] for p, c in no_bids),
                    key=lambda l: l[0])[:5]
    except Exception:  # noqa: BLE001 — depth is display, never a gate
        pass
    return levels


# ── Desk v3: venue-style browse + full game views (owner directive
#    2026-08-07: "feel like you are inside the venue placing an order").
#    Sport chips -> game cards -> a game view where EVERY market for the
#    game populates, grouped the way the venues group them. Data is the
#    venues' own (metadata + live books); the skin is ours. ──────────────

def _desk_league_of(prefix: str) -> str:
    """Venue event-slug prefix -> desk navigation bucket."""
    p = (prefix or "").lower()
    if p in ("mlb",): return "mlb"
    if p in ("wnba",): return "wnba"
    if p in ("nba", "cbb"): return "nba"
    if p in ("nfl", "cfb"): return "nfl"
    if p in ("nhl",): return "nhl"
    if p.startswith(("atp", "wta", "itf")): return "tennis"
    if p in ("cs2", "csgo", "dota2", "lol", "valorant", "val"):
        return "esports"
    return "soccer"


_DESK_LEAGUES = {
    "mlb": ("mlb",), "wnba": ("wnba",), "nba": ("nba",),
    "nfl": ("nfl",), "nhl": ("nhl",),
    "tennis": ("atp", "wta", "itf", "itfm", "itfw"),
}
_GAME_DATE_RE = __import__("re").compile(r"\d{4}-\d{2}-\d{2}")


def _game_base(slug: str) -> str | None:
    """'mlb-nyy-bos-2026-08-07-o8pt5' -> 'mlb-nyy-bos-2026-08-07'."""
    m = _GAME_DATE_RE.search(slug or "")
    if not m:
        return None
    return slug[: m.end()]


async def _pm_quote_many(assets: list[str]) -> dict[str, dict]:
    """Best ask/bid for many tokens, concurrently, best-effort."""
    import asyncio as _asyncio

    import httpx

    out: dict[str, dict] = {}
    cfg = settings()

    async def _one(client: httpx.AsyncClient, a: str) -> None:
        try:
            resp = await client.get("/book", params={"token_id": a})
            if resp.status_code != 200:
                return
            d = resp.json()
            asks = sorted(float(x["price"]) for x in (d.get("asks") or []))
            bids = sorted((float(x["price"]) for x in (d.get("bids") or [])),
                          reverse=True)
            out[a] = {"ask": asks[0] if asks else None,
                      "bid": bids[0] if bids else None}
        except Exception:  # noqa: BLE001
            return

    try:
        async with httpx.AsyncClient(base_url=cfg.clob_api_base,
                                     timeout=8) as client:
            await _asyncio.gather(*(_one(client, a) for a in assets[:48]))
    except Exception:  # noqa: BLE001
        pass
    return out


@app.get("/api/admin/desk-games", dependencies=[Depends(require_desk)])
async def api_desk_games(venue: str = Query("polymarket"),
                         league: str = Query("all")) -> dict:
    """Game cards for the browse view: today/tomorrow's games with live
    moneyline prices on each side — the venue home screen's shape."""
    from datetime import date as _date, timedelta as _td

    from ..copy_sports import market_type_of

    days = {(_date.today() + _td(days=i)).isoformat() for i in (-1, 0, 1)}
    if venue == "kalshi" and league == "everything":
        # FULL UNIVERSE (wave-2 2026-08-22): every open event on the
        # venue — politics, econ, weather, the lot — from the 5-min
        # cached sweep, one card per event, same card shape as the
        # sports board. The sports leagues keep their own per-series
        # path below, untouched.
        evs = await _kalshi_all_open_events()
        games_all = []
        for ev in evs:
            et = ev.get("event_ticker") or ""
            mkts = [m for m in (ev.get("markets") or [])
                    if m.get("status") in (None, "open", "active")]
            if not et or not mkts:
                continue
            series = et.split("-", 1)[0]
            games_all.append({
                "id": et, "venue": "kalshi", "league": "everything",
                "title": ((ev.get("title") or et)
                          .replace(" Winner?", "")),
                "outcomes": [
                    {"label": s["sub_title"] or s["title"],
                     "ticker": s["ticker"], "price": s["yes_ask"]}
                    for s in (_kalshi_shape(m, series)
                              for m in mkts[:3])]})
        return {"games": games_all,
                "counts": {"everything": len(games_all),
                           "all": len(games_all)}}
    if venue == "kalshi":
        # Kalshi games from the per-league series (each side its own
        # ticker); the league picks its OWN series so a busy MLB slate
        # can never crowd tennis out of a shared cap, and the 48h close
        # window keeps the board to the actual slate (live matches stay:
        # they close soonest and sort first).
        series_by_league = {
            "mlb": ["KXMLBGAME"], "wnba": ["KXWNBAGAME"],
            "nba": ["KXNBAGAME"], "nfl": ["KXNFLGAME"],
            "nhl": ["KXNHLGAME"], "tennis": ["KXATPMATCH", "KXWTAMATCH"],
        }
        series_list = (_DESK_KALSHI_SERIES if league == "all"
                       else series_by_league.get(league, []))
        mkts = await _kalshi_fetch_boards(series_list)
        games: dict[str, dict] = {}
        for m in mkts:
            t = m.get("ticker") or ""
            parts = t.split("-")
            if len(parts) < 3:
                continue
            gkey = "-".join(parts[:2])
            series = m.get("series") or ""
            lg = {"KXMLBGAME": "mlb", "KXWNBAGAME": "wnba",
                  "KXNBAGAME": "nba", "KXNFLGAME": "nfl",
                  "KXNHLGAME": "nhl"}.get(series, "tennis")
            g = games.setdefault(gkey, {
                "id": gkey, "venue": "kalshi", "league": lg,
                "title": (m.get("title") or "").replace(" Winner?", ""),
                "outcomes": []})
            g["outcomes"].append({
                "label": m.get("sub_title") or m.get("title"),
                "ticker": t, "price": m.get("yes_ask")})
        out = [g for g in games.values() if g["outcomes"]]
        counts = {}
        for g in out:
            counts[g["league"]] = counts.get(g["league"], 0) + 1
        counts["all"] = len(out)
        return {"games": out[:60], "counts": counts}

    # VENUE-NATIVE BOARD (owner order 2026-08-21: the desk must
    # navigate like the venue itself — the catalog join dropped every
    # event whose slug spelling differed, tennis worst of all). The
    # venue's own event listing is the source of truth: every event it
    # lists renders as a card, moneyline sides quoted from the listing
    # itself, and the per-league counts come back in one response so
    # the navigation reads like the venue's own category rail.
    from .. import pmus as _pmus
    try:
        events = await asyncio.to_thread(_pmus.list_desk_events)
    except Exception:  # noqa: BLE001
        events = []
    league_of_ev = _desk_league_of
    counts: dict[str, int] = {}
    cards = []
    for ev in events:
        lg = league_of_ev(ev["league"])
        counts[lg] = counts.get(lg, 0) + 1
        # 'everything' = the venue's whole open board, no league filter
        # (the venue-native listing already carries every open event).
        if league not in ("all", "everything") and lg != league:
            continue
        ml = [m for m in ev["markets"] if m["kind"] in ("aec", "atc")]
        outs = [{"label": (m["label"].split("—")[-1].strip()
                           or m["label"]),
                 "us_slug": m["us_slug"], "price": m["price"]}
                for m in ml[:3]]
        if not outs:
            m0 = ev["markets"][0]
            outs = [{"label": m0["label"], "us_slug": m0["us_slug"],
                     "price": m0["price"]}]
        cards.append({
            "id": ev["slug"], "venue": "polymarket", "league": lg,
            "title": ev["title"], "markets_n": len(ev["markets"]),
            "outcomes": outs})
    counts["all"] = len(events)
    counts["everything"] = len(events)
    cards.sort(key=lambda g: (g["id"][-10:], g["id"]))
    return {"games": cards[:400 if league == "everything" else 80],
            "counts": counts}


# ── Desk v8 (owner contract 2026-08-22): the venue-style FEED ────────
# Large market cards for the home feed — same venue listings and caches
# as desk-games (never a second venue sweep), plus the fields the card
# skin needs: volume_usd (from the venue payloads where present, null
# when absent, NEVER invented), close_time, and history_id = the first
# outcome's chartable id so a card charts without a second lookup.
# Sorted volume desc nulls-last, cap 60.

_DESK_FEED_CAP = 60


def _feed_finish(cards: list[dict], counts: dict) -> dict:
    """history_id, volume-desc-nulls-last sort, cap — every venue path
    funnels through here so the card contract has one spelling."""
    for c in cards:
        c["history_id"] = (c["outcomes"][0]["id"] if c["outcomes"]
                           else None)
    cards.sort(key=lambda c: (c.get("volume_usd") is None,
                              -(c.get("volume_usd") or 0.0),
                              c.get("close_time") or "~"))
    return {"cards": cards[:_DESK_FEED_CAP], "counts": counts}


@app.get("/api/admin/desk-feed", dependencies=[Depends(require_desk)])
async def api_desk_feed(venue: str = Query("polymarket"),
                        league: str = Query("all")) -> dict:
    """Market cards for the v8 venue-style feed. Card shape:
    {id, venue, title, league, volume_usd|null, close_time|null,
     outcomes: [{label, id, price}], history_id} — the outcome id is
    the venue-native orderable/chartable identifier (PM: the us-slug
    the whole desk orders by — the venue listing carries no CLOB
    token; Kalshi: the market ticker)."""
    if venue == "kalshi" and league == "everything":
        # Full-universe cards from the SAME 5-min cached sweep
        # desk-games uses: one card per open event, volume summed
        # over the event's open markets (null when none reported).
        evs = await _kalshi_all_open_events()
        cards = []
        for ev in evs:
            et = ev.get("event_ticker") or ""
            mkts = [m for m in (ev.get("markets") or [])
                    if m.get("status") in (None, "open", "active")]
            if not et or not mkts:
                continue
            series = et.split("-", 1)[0]
            shaped = [_kalshi_shape(m, series) for m in mkts]
            vols = [s["volume_usd"] for s in shaped
                    if s["volume_usd"] is not None]
            closes = [s["close_time"] for s in shaped
                      if s["close_time"]]
            cards.append({
                "id": et, "venue": "kalshi", "league": "everything",
                "title": ((ev.get("title") or et)
                          .replace(" Winner?", "")),
                "volume_usd": round(sum(vols), 2) if vols else None,
                "close_time": min(closes) if closes else None,
                "outcomes": [
                    {"label": s["sub_title"] or s["title"],
                     "id": s["ticker"], "price": s["yes_ask"]}
                    for s in shaped[:3]]})
        return _feed_finish(cards, {"everything": len(cards),
                                    "all": len(cards)})
    if venue == "kalshi":
        # Sports cards from the same per-league series fetch as
        # desk-games (48h window, each side its own ticker), grouped
        # to one card per game; volume summed across the game's sides.
        series_by_league = {
            "mlb": ["KXMLBGAME"], "wnba": ["KXWNBAGAME"],
            "nba": ["KXNBAGAME"], "nfl": ["KXNFLGAME"],
            "nhl": ["KXNHLGAME"], "tennis": ["KXATPMATCH", "KXWTAMATCH"],
        }
        series_list = (_DESK_KALSHI_SERIES if league == "all"
                       else series_by_league.get(league, []))
        mkts = await _kalshi_fetch_boards(series_list)
        games: dict[str, dict] = {}
        for m in mkts:
            t = m.get("ticker") or ""
            parts = t.split("-")
            if len(parts) < 3:
                continue
            gkey = "-".join(parts[:2])
            lg = {"KXMLBGAME": "mlb", "KXWNBAGAME": "wnba",
                  "KXNBAGAME": "nba", "KXNFLGAME": "nfl",
                  "KXNHLGAME": "nhl"}.get(m.get("series") or "",
                                          "tennis")
            g = games.setdefault(gkey, {
                "id": gkey, "venue": "kalshi", "league": lg,
                "title": (m.get("title") or "").replace(" Winner?", ""),
                "volume_usd": None, "close_time": None,
                "outcomes": []})
            g["outcomes"].append({
                "label": m.get("sub_title") or m.get("title"),
                "id": t, "price": m.get("yes_ask")})
            v = m.get("volume_usd")
            if v is not None:
                g["volume_usd"] = round((g["volume_usd"] or 0.0) + v, 2)
            ct = m.get("close_time")
            if ct and (g["close_time"] is None or ct < g["close_time"]):
                g["close_time"] = ct
        cards = [g for g in games.values() if g["outcomes"]]
        counts: dict[str, int] = {}
        for g in cards:
            counts[g["league"]] = counts.get(g["league"], 0) + 1
        counts["all"] = len(cards)
        return _feed_finish(cards, counts)

    # Polymarket: the venue-native event listing (30s cache in pmus),
    # one card per event, moneyline sides as the card outcomes —
    # exactly desk-games' card builder plus volume/close_time, which
    # now ride on the listing rows themselves.
    from .. import pmus as _pmus
    try:
        events = await asyncio.to_thread(_pmus.list_desk_events)
    except Exception:  # noqa: BLE001 — an empty feed, never a 500
        events = []
    counts = {}
    cards = []
    for ev in events:
        lg = _desk_league_of(ev["league"])
        counts[lg] = counts.get(lg, 0) + 1
        if league not in ("all", "everything") and lg != league:
            continue
        ml = [m for m in ev["markets"] if m["kind"] in ("aec", "atc")]
        outs = [{"label": (m["label"].split("—")[-1].strip()
                           or m["label"]),
                 "id": m["us_slug"], "price": m["price"]}
                for m in ml[:3]]
        if not outs:
            m0 = ev["markets"][0]
            outs = [{"label": m0["label"], "id": m0["us_slug"],
                     "price": m0["price"]}]
        cards.append({
            "id": ev["slug"], "venue": "polymarket", "league": lg,
            "title": ev["title"],
            "volume_usd": ev.get("volume_usd"),
            "close_time": ev.get("close_time"),
            "outcomes": outs})
    counts["all"] = len(events)
    counts["everything"] = len(events)
    return _feed_finish(cards, counts)


@app.get("/api/admin/desk-game", dependencies=[Depends(require_desk)])
async def api_desk_game(venue: str = Query(...),
                        id: str = Query(...)) -> dict:
    """The full game view: EVERY market for one game, grouped the way
    the venue groups them, quoted live, with the desk's own positions
    inline (cost / current value / to-win — no cash-out: this account
    holds to resolution by design)."""
    from ..copy_sports import market_type_of

    group_label = {"moneyline": "Moneyline", "spread": "Spreads",
                   "total": "Totals"}
    if venue == "kalshi":
        # The game id IS the venue's event_ticker (SERIES-EVENTCODE):
        # ask for the event directly — precise, and immune to any cap
        # or window on the shared browse list. The venue lists a
        # game's OTHER market families under SIBLING series (census
        # ground truth: ...GAME pairs with ...SPREAD/TOTAL/TEAMTOTAL/
        # 1HTOTAL/1HSPREAD; ...MATCH pairs with ...SETWINNER/GWINNER/
        # EXACTMATCH), each with its own event ticker sharing the
        # event code — sweep them all so the full board shows (owner
        # order 2026-08-12). Unknown siblings 404/empty harmlessly.
        import asyncio as _asyncio

        import httpx

        series0 = id.split("-", 1)[0]
        code = id.split("-", 1)[1] if "-" in id else ""
        sibs: list[str] = []
        if series0.endswith("MATCH"):
            stem = series0[: -len("MATCH")]
            sibs = [stem + s for s in ("SETWINNER", "GWINNER",
                                       "EXACTMATCH")]
        elif series0.endswith("GAME"):
            stem = series0[: -len("GAME")]
            sibs = [stem + s for s in ("SPREAD", "TOTAL", "TEAMTOTAL",
                                       "1HTOTAL", "1HSPREAD")]
        raw_markets: list[dict] = []

        async def _event_direct(client: httpx.AsyncClient) -> None:
            try:
                resp = await client.get("/markets", params={
                    "event_ticker": id, "status": "open", "limit": 200})
                if resp.status_code == 200:
                    raw_markets.extend(resp.json().get("markets") or [])
            except Exception:  # noqa: BLE001
                pass

        async def _sibling(client: httpx.AsyncClient, sib: str) -> None:
            try:
                resp = await client.get("/events", params={
                    "series_ticker": sib, "status": "open",
                    "with_nested_markets": "true", "limit": 200})
                if resp.status_code != 200:
                    return
                for ev in (resp.json().get("events") or []):
                    et = ev.get("event_ticker") or ""
                    if code and code in et:
                        raw_markets.extend(ev.get("markets") or [])
            except Exception:  # noqa: BLE001
                pass

        try:
            async with httpx.AsyncClient(base_url=KALSHI_PUBLIC_API,
                                         timeout=10) as client:
                await _asyncio.gather(_event_direct(client),
                                      *(_sibling(client, s)
                                        for s in sibs))
        except Exception:  # noqa: BLE001
            pass
        groups: dict[str, list] = {}
        title = id
        for rm in raw_markets:
            series = (rm.get("ticker") or "").split("-", 1)[0]
            m = _kalshi_shape(rm, series)
            label = ("Exact Score" if series.endswith("EXACTMATCH") else
                     "Set Winners" if series.endswith("SETWINNER") else
                     "Game Props" if series.endswith("GWINNER") else
                     "Moneyline" if series.endswith("GAME")
                     or series.endswith("MATCH") else
                     "Spreads" if "SPREAD" in series else
                     "Totals" if "TOTAL" in series else "More")
            row_label = m.get("sub_title") or m.get("title")
            if label not in ("Moneyline",):
                # Sibling markets repeat the matchup in the title —
                # keep the distinguishing part readable on one row.
                row_label = (f"{(m.get('title') or '').strip()} — "
                             f"{m.get('sub_title')}"
                             if m.get("sub_title") else row_label)
            groups.setdefault(label, []).append({
                "label": row_label,
                "ticker": m.get("ticker"), "price": m.get("yes_ask"),
                "no_price": m.get("no_ask")})
            if series == series0:
                title = (m.get("title") or title).replace(" Winner?", "")
        korder = ["Moneyline", "Spreads", "Totals", "Set Winners",
                  "Game Props", "Exact Score", "More"]
        return {"id": id, "venue": "kalshi", "title": title,
                "groups": [{"name": k, "markets": groups[k]}
                           for k in korder if k in groups]
                + [{"name": k, "markets": v} for k, v in groups.items()
                   if k not in korder],
                "positions": []}

    # VENUE-NATIVE FULL BOARD (owner order 2026-08-21): the event id
    # IS the venue's own eventSlug from the desk listing — every market
    # the venue lists for it renders, grouped by the venue slug grammar
    # with the venue's own market titles (tennis alternate totals and
    # game/set spreads included, because nothing filters them anymore).
    from .. import pmus as _pmus
    try:
        events = await asyncio.to_thread(_pmus.list_desk_events)
    except Exception:  # noqa: BLE001
        events = []
    ev = next((e for e in events if e["slug"] == id), None)
    if ev is None:
        try:
            board = await asyncio.to_thread(_pmus.event_board, id)
        except Exception:  # noqa: BLE001
            board = []
        ev = {"slug": id, "title": id,
              "markets": [{"us_slug": r["us_slug"],
                           "kind": (r["us_slug"] or "").split("-", 1)[0],
                           "label": r["label"], "price": r["price"]}
                          for r in board]}
    kind_group = {"aec": "Moneyline", "atc": "Moneyline",
                  "asc": "Spreads", "tsc": "Totals",
                  "astatc": "Props & Specials"}

    def _grp(mk: dict) -> str:
        g = kind_group.get(mk["kind"], "More Markets")
        lbl = (mk["label"] or "").lower()
        # The venue's own wording decides the tennis subgroups the
        # trader expects to see (game/set spreads, set winners).
        if "set" in lbl and g in ("Spreads", "Moneyline",
                                  "More Markets"):
            return "Set Markets"
        return g

    groups: dict[str, list] = {}
    for mk in ev["markets"]:
        groups.setdefault(_grp(mk), []).append({
            "label": mk["label"], "us_slug": mk["us_slug"],
            "price": mk["price"]})
    # The desk's own open positions on this event (manual sleeve),
    # matched by venue slug — the game key both sides share.
    pool = await get_pool()
    pos_rows = await pool.fetch(
        """
        SELECT lo.asset, lo.us_market_slug,
               lo.fill_price::float8 AS fill_price,
               lo.filled_shares::float8 AS shares,
               lo.filled_usd::float8 AS cost, lo.status,
               lo.pnl::float8 AS pnl, mt.outcome
        FROM live_orders lo
        LEFT JOIN market_tokens mt ON mt.token_id = lo.asset
        WHERE lo.whale_username = 'manual'
          AND lo.status IN ('filled', 'settled')
        ORDER BY lo.placed_at DESC LIMIT 200
        """)
    from ..live_executor import _us_game_key
    ev_key = _us_game_key(f"atc-{id}") or id
    positions = []
    for p in pos_rows:
        us = p["us_market_slug"] or ""
        if (_us_game_key(us) or "") != ev_key:
            continue
        positions.append({
            "asset": str(p["asset"] or us),
            "outcome": p["outcome"] or us,
            "cost": p["cost"], "fill_price": p["fill_price"],
            "shares": p["shares"], "status": p["status"],
            "current_value": None,
            "to_win": round(p["shares"], 2) if p["shares"] else None,
            "pnl": p["pnl"]})
    order = ["Moneyline", "Spreads", "Totals", "Set Markets",
             "Props & Specials", "More Markets"]
    # LIVE RE-QUOTE for the moneyline rows (owner report 2026-08-22:
    # Pegula 31c / Swiatek 32c — complements summing 63c). The venue's
    # LISTING carries a stale per-side price when one side's book is
    # thin; the ticket always re-quotes live so orders were never
    # mispriced, but the page must not display a stale print either.
    # Bounded: moneyline group only (<=6 slugs), live book read per
    # side, listing price replaced whenever the live read answers.
    ml = groups.get("Moneyline") or []
    if ml:
        from .. import pmus as _pm

        async def _fresh(row: dict) -> None:
            try:
                px = await asyncio.to_thread(_pm.slug_ask,
                                             row.get("us_slug") or "")
                if px is not None:
                    row["price"] = px
            except Exception:  # noqa: BLE001 — keep the listing price
                pass
        await asyncio.gather(*(_fresh(r) for r in ml[:6]))
    return {"id": id, "venue": "polymarket", "title": ev["title"],
            "groups": [{"name": k, "markets": groups[k]}
                       for k in order if k in groups]
            + [{"name": k, "markets": v} for k, v in groups.items()
               if k not in order],
            "positions": positions}


@app.get("/api/admin/fill-vs-miss", dependencies=[Depends(require_admin)])
async def api_fill_vs_miss(days: int = Query(7, ge=1, le=30)) -> dict:
    """The direct test of the copy thesis (owner 2026-08-12: 'same or
    better price -> same or better margin'): grade the FILLED cohort's
    realized ROI against the counterfactual ROI of the copies the
    price rule made us SKIP ('unfilled' FOK kills), each miss scored
    at HIS price against the market's actual resolution. If misses
    grade far above fills, same-or-better is selecting away his best
    trades (adverse selection) and the tolerance question gets decided
    on this number, not on theory."""
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT lo.whale_username AS whale, lo.status,
               lo.his_price::float8 AS his_price,
               lo.requested_usd::float8 AS req_usd,
               lo.filled_usd::float8 AS filled_usd,
               lo.pnl::float8 AS pnl,
               mt.outcome_index, m.resolved_prices
        FROM live_orders lo
        LEFT JOIN market_tokens mt ON mt.token_id = lo.asset
        LEFT JOIN markets m ON m.condition_id = mt.condition_id
        WHERE lo.placed_at > now() - make_interval(days => $1)
          AND COALESCE(lo.whale_username, '')
              NOT IN ('manual', 'underdog')
          AND lo.status IN ('filled', 'settled', 'unfilled')
        """, days)
    return {"days": days, "whales": grade_rows(rows)}


class MeridianJournalBody(BaseModel):
    entry: str
    mood: str = "steady"


@app.post("/api/admin/meridian-journal",
          dependencies=[Depends(require_admin)])
async def api_meridian_journal_post(body: MeridianJournalBody) -> dict:
    """MERIDIAN's journal intake: the co-CEO session authors entries in
    the repo; the diagnostic workflow publishes the newest one here.
    entry_hash dedupe makes republishing a no-op."""
    import hashlib

    entry = (body.entry or "").strip()
    if not entry:
        return {"ok": False, "error": "empty entry"}
    mood = body.mood if body.mood in ("steady", "focused", "alert") \
        else "steady"
    h = hashlib.sha256(entry.encode()).hexdigest()[:32]
    pool = await get_pool()
    nid = await pool.fetchval(
        """
        INSERT INTO meridian_journal (entry, mood, entry_hash)
        VALUES ($1, $2, $3)
        ON CONFLICT (entry_hash) DO NOTHING
        RETURNING id
        """, entry[:2000], mood, h)
    return {"ok": True, "id": nid, "new": nid is not None}


@app.get("/api/meridian/journal")
async def api_meridian_journal(limit: int = Query(5, ge=1, le=20)) -> dict:
    """Public: MERIDIAN's latest journal entries. The author controls
    the content (repo-reviewed before publish), so this is public-safe
    by construction — it is the voice of the page."""
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT entry, mood, created_at FROM meridian_journal "
        "ORDER BY id DESC LIMIT $1", limit)
    return {"entries": [{"entry": r["entry"], "mood": r["mood"],
                         "at": r["created_at"].isoformat()} for r in rows]}


class MeridianTurn(BaseModel):
    role: str
    text: str


class MeridianExchangeBody(BaseModel):
    turns: list[MeridianTurn]


@app.post("/api/admin/meridian-exchange",
          dependencies=[Depends(require_admin)])
async def api_meridian_exchange_post(body: MeridianExchangeBody) -> dict:
    """Mirror voice turns from the MERIDIAN page into the shared
    conversation record the engine session reads at its check-ins."""
    pool = await get_pool()
    n = 0
    for t in body.turns[:20]:
        role = t.role if t.role in ("user", "assistant") else None
        text = (t.text or "").strip()
        if not role or not text:
            continue
        await pool.execute(
            "INSERT INTO meridian_exchange (role, text) VALUES ($1, $2)",
            role, text[:4000])
        n += 1
    return {"ok": True, "stored": n}


@app.get("/api/admin/meridian-exchange",
         dependencies=[Depends(require_admin)])
async def api_meridian_exchange(unseen: int = 0, mark: int = 0,
                                limit: int = Query(40, ge=1, le=200)) -> dict:
    """The conversation record. unseen=1&mark=1 is the engine session's
    probe consumption (reads new turns, marks them delivered); the
    MERIDIAN page uses the plain newest-N form to restore its memory
    across visits."""
    pool = await get_pool()
    if unseen:
        rows = await pool.fetch(
            "SELECT id, role, text, at FROM meridian_exchange "
            "WHERE seen_at IS NULL ORDER BY id LIMIT $1", limit)
        if mark and rows:
            await pool.execute(
                "UPDATE meridian_exchange SET seen_at = now() "
                "WHERE id = ANY($1::bigint[])", [r["id"] for r in rows])
    else:
        rows = list(reversed(await pool.fetch(
            "SELECT id, role, text, at FROM meridian_exchange "
            "ORDER BY id DESC LIMIT $1", limit)))
    return {"turns": [{"id": r["id"], "role": r["role"], "text": r["text"],
                       "at": r["at"].isoformat()} for r in rows]}


class JarvisNoteBody(BaseModel):
    note: str


@app.post("/api/admin/jarvis-note", dependencies=[Depends(require_admin)])
async def api_jarvis_note(body: JarvisNoteBody) -> dict:
    """The JARVIS voice cockpit's one-way bridge to the autonomous engine
    session: notes queue here and the engine session reads them at its
    check-ins (probe prints unread + marks delivered)."""
    note = (body.note or "").strip()
    if not note:
        return {"ok": False, "error": "empty note"}
    pool = await get_pool()
    nid = await pool.fetchval(
        "INSERT INTO jarvis_notes (note) VALUES ($1) RETURNING id",
        note[:4000])
    return {"ok": True, "id": nid,
            "detail": "queued — the engine session reads notes at its "
                      "next check-in (within the hour)"}


@app.get("/api/admin/jarvis-notes", dependencies=[Depends(require_admin)])
async def api_jarvis_notes(mark: int = 0,
                           limit: int = Query(20, ge=1, le=100)) -> dict:
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT id, note, created_at FROM jarvis_notes "
        "WHERE read_at IS NULL ORDER BY id LIMIT $1", limit)
    if mark and rows:
        await pool.execute(
            "UPDATE jarvis_notes SET read_at = now() "
            "WHERE id = ANY($1::bigint[])", [r["id"] for r in rows])
    remaining = int(await pool.fetchval(
        "SELECT count(*) FROM jarvis_notes WHERE read_at IS NULL") or 0)
    return {"notes": [{"id": r["id"], "note": r["note"],
                       "created_at": r["created_at"].isoformat()}
                      for r in rows],
            "unread_remaining": remaining}


class ManualTradeBody(BaseModel):
    asset: str = ""            # Polymarket token id
    usd: float
    note: str = ""
    venue: str = "polymarket-us"
    ticker: str = ""           # Kalshi market ticker
    side: str = "yes"          # Kalshi side
    title: str = ""
    us_slug: str = ""          # PM: venue-board row, orderable by slug
    ask: float | None = None   # PM slug rows: bounded fallback quote


@app.post("/api/admin/manual-trade")
async def api_manual_trade(body: ManualTradeBody,
                           role: str = Depends(require_desk)) -> dict:
    """Place an admin-directed trade as the 'manual' sleeve. Separate
    budget, separate P&L line, zero interaction with autonomous flows.
    Polymarket executes synchronously; Kalshi queues for the engine's
    ~10s relay (only the engine holds Kalshi credentials)."""
    if role == "wall":
        raise HTTPException(status_code=403, detail="wall is read-only")
    from ..live_executor import (MANUAL_DAILY_USD, MANUAL_MAX_PER_ORDER_USD,
                                 execute_manual)

    if body.venue == "polymarket-us":
        return await execute_manual(
            body.asset, body.usd, body.note or body.title,
            us_slug=body.us_slug, ask_hint=body.ask)
    if body.venue != "kalshi":
        return {"ok": False, "error": "unknown venue"}
    if not (0 < body.usd <= MANUAL_MAX_PER_ORDER_USD):
        return {"ok": False,
                "error": f"size must be $0-{MANUAL_MAX_PER_ORDER_USD:.0f}"}
    # The venue is YES-denominated per outcome ticker. A NO buy is the
    # SAME BET as YES on the event's sibling ticker (NO Ruud 55c == YES
    # Fonseca 55c), so NO routes through the one order path the venue
    # has proven for us — no new order semantics, identical economics.
    side = body.side.lower() if body.side.lower() in ("yes", "no") else "yes"
    ticker = body.ticker.strip()
    if not ticker:
        return {"ok": False, "error": "pick a market"}
    pool = await get_pool()
    day_spent = float(await pool.fetchval(
        """
        SELECT COALESCE((SELECT sum(filled_usd) FROM live_orders
                         WHERE whale_username = 'manual'
                           AND placed_at > now() - interval '24 hours'), 0)
             + COALESCE((SELECT sum(usd) FROM manual_kalshi_queue
                         WHERE status IN ('pending', 'placed', 'filled')
                           AND created_at > now() - interval '24 hours'), 0)
        """) or 0)
    if day_spent + body.usd > MANUAL_DAILY_USD:
        return {"ok": False,
                "error": (f"manual day budget exhausted (${day_spent:.2f} "
                          f"of ${MANUAL_DAILY_USD:.0f} in 24h)")}
    # Re-quote server-side — never trust a client-supplied price.
    import httpx

    ask = None
    try:
        async with httpx.AsyncClient(base_url=KALSHI_PUBLIC_API,
                                     timeout=8) as client:
            if side == "no":
                event = ticker.rsplit("-", 1)[0]
                resp = await client.get("/markets",
                                        params={"event_ticker": event})
                ms = (resp.json().get("markets") or []) if \
                    resp.status_code == 200 else []
                sibs = [m for m in ms if m.get("ticker")
                        and m["ticker"] != ticker]
                if len(sibs) != 1:
                    return {"ok": False,
                            "error": ("no tradable NO side listed for "
                                      "this market")}
                ticker = sibs[0]["ticker"]
                ask = _kcents(sibs[0], "yes_ask")
            else:
                resp = await client.get("/markets",
                                        params={"tickers": ticker})
                ms = (resp.json().get("markets") or []) if \
                    resp.status_code == 200 else []
                if ms:
                    ask = _kcents(ms[0], "yes_ask")
    except Exception:  # noqa: BLE001
        ask = None
    if ask is None or not (0 < ask < 1):
        return {"ok": False, "error": "no live Kalshi quote for that side"}
    limit = round(min(ask + 0.02, 0.99), 2)
    count = int(body.usd / limit)
    if count < 1:
        return {"ok": False, "error": "budget buys zero whole contracts"}
    # Double-submit guard (audit 2026-08-21): this branch does seconds of
    # venue HTTP before the insert, so a double-click / client auto-retry
    # queued TWO real orders and the relay placed both. A same-ticket row
    # queued in the last 30s means the first click already went through —
    # refuse the second and point at the blotter instead of double-buying.
    dup_id = await pool.fetchval(
        """
        SELECT id FROM manual_kalshi_queue
        WHERE ticker = $1 AND side = $2
          AND status IN ('pending', 'placed')
          AND created_at > now() - interval '30 seconds'
        ORDER BY id DESC LIMIT 1
        """, ticker, side)
    if dup_id is not None:
        return {"ok": False,
                "error": (f"an identical ticket (#{dup_id}) was queued "
                          "seconds ago — check the blotter before "
                          "submitting again")}
    row_id = await pool.fetchval(
        """
        INSERT INTO manual_kalshi_queue
            (ticker, title, side, limit_price, count, usd, note)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING id
        """,
        ticker, body.title[:200] or ticker, side,
        limit, count, round(count * limit, 2), body.note[:200])
    return {"ok": True, "queued": True, "row_id": row_id,
            "quoted_ask": ask, "limit_price": limit, "count": count,
            "title": body.title or ticker,
            "outcome": side.upper(),
            "error": None,
            "detail": "queued — the engine places it within ~10 seconds"}


@app.get("/api/engine/crypto-copy-candidates")
async def api_crypto_copy_candidates(
    x_engine_token: str = Header(default="")
) -> dict:
    """Fresh BUY fills from the CRYPTO copy sources (owner order
    2026-08-21) for the engine's Kalshi crypto leg. Stateless: the
    engine dedupes by trade id in its own ledger (fill_uid), so this
    endpoint just serves the last few minutes of flow. Freshness is
    enforced HERE as well as engine-side — a stale crypto price is a
    different bet, and staleness must not depend on one process's
    clock."""
    cfg = settings()
    check_engine_token(x_engine_token)
    from .copies_record import CRYPTO_WHALES

    pool = await get_pool()
    # Chain-detected rows (the fresh ones, post 2026-08-22 exchange fix)
    # carry only the token id until async enrichment fills slug/title —
    # the leg classifies by slug and was refusing every one as
    # 'no-asset'. These systematics re-trade the same markets all day,
    # so stored metadata (market_tokens -> markets, persisted the first
    # time any trade on the market enriched) covers them: serve the
    # trade's own slug/title when present, else the metadata's.
    rows = await pool.fetch(
        """
        SELECT t.id, lower(COALESCE(w.username, '')) AS username,
               COALESCE(t.market_slug, m.slug)   AS market_slug,
               COALESCE(t.market_title, m.title) AS market_title,
               t.side,
               t.price::float8 AS price, t.notional::float8 AS notional,
               EXTRACT(EPOCH FROM t.ts)::float8 AS ts_epoch
        FROM trades t
        JOIN whales w ON w.id = t.whale_id
        LEFT JOIN market_tokens mt ON mt.token_id = t.asset
        LEFT JOIN markets m ON m.condition_id = mt.condition_id
        WHERE lower(COALESCE(w.username, '')) = ANY($1::text[])
          AND t.side = 'BUY'
          AND t.ts > now() - interval '10 minutes'
        ORDER BY t.ts DESC LIMIT 100
        """, list(CRYPTO_WHALES))
    return {"candidates": [dict(r) for r in rows]}


@app.get("/api/engine/manual-kalshi-queue")
async def api_manual_kalshi_queue(
    x_engine_token: str = Header(default="")
) -> dict:
    """Pending desk orders for the engine's Kalshi relay."""
    cfg = settings()
    check_engine_token(x_engine_token)
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT id, ticker, side, action, "
        "limit_price::float8 AS limit_price, "
        "count FROM manual_kalshi_queue WHERE status = 'pending' "
        "ORDER BY id LIMIT 20")
    return {"orders": [dict(r) for r in rows]}


class KalshiRelayResult(BaseModel):
    id: int
    status: str                # placed|filled|unfilled|error
    order_id: str | None = None
    fill_count: int | None = None
    fill_price: float | None = None
    error: str | None = None


@app.post("/api/engine/manual-kalshi-result")
async def api_manual_kalshi_result(
    body: KalshiRelayResult, x_engine_token: str = Header(default="")
) -> dict:
    cfg = settings()
    check_engine_token(x_engine_token)
    if body.status not in ("placed", "filled", "unfilled", "error"):
        raise HTTPException(status_code=400, detail="bad status")
    pool = await get_pool()
    await pool.execute(
        """
        UPDATE manual_kalshi_queue
        SET status=$2, order_id=$3, fill_count=$4, fill_price=$5,
            error=$6, updated_at=now()
        WHERE id=$1
        """,
        body.id, body.status, body.order_id, body.fill_count,
        body.fill_price, (body.error or None) and body.error[:300])
    return {"ok": True}


@app.get("/api/engine/held-assets")
async def api_held_assets(x_engine_token: str = Header(default="")) -> dict:
    """PMUS token ids the platform already holds through ANY sleeve —
    copies, the desk, the underdog test. The engine skips these outright
    (owner 2026-08-08: 'trades are higher than $10 per trade' — each
    sleeve capped its own ticket while stacking one position)."""
    cfg = settings()
    check_engine_token(x_engine_token)
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT DISTINCT asset FROM live_orders "
        "WHERE status IN ('submitting', 'filled') "
        "  AND placed_at > now() - interval '7 days'")
    return {"assets": [str(r["asset"]) for r in rows]}


@app.get("/api/engine/kud-queue")
async def api_kud_queue(x_engine_token: str = Header(default="")) -> dict:
    """Queued Kalshi-leg underdog tasks for the engine's relay. The
    worker queues EVERY catalogued game at first sight; the engine runs
    the T-minus-5 window off start_ts, resolves the Kalshi market, picks
    the dog from its own book, and rests the +20% exit. Soonest start
    first so a full day's slate can never starve an open window behind
    tonight's waiting games."""
    cfg = settings()
    check_engine_token(x_engine_token)
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT id, game_slug, league, dog_outcome, other_outcome, "
        "per_fill_usd::float8 AS per_fill_usd, "
        "take_profit::float8 AS take_profit, "
        "extract(epoch FROM start_ts)::float8 AS start_ts "
        "FROM kud_queue WHERE status = 'queued' "
        "ORDER BY start_ts ASC NULLS FIRST, id LIMIT 200")
    return {"tasks": [dict(r) for r in rows]}


class KudResult(BaseModel):
    id: int
    status: str          # filled|cashed_out|no_market|band_fail|held|unfilled|error|missed
    ticker: str | None = None
    entry_price: float | None = None
    qty: int | None = None
    exit_price: float | None = None
    pnl: float | None = None
    error: str | None = None


@app.post("/api/engine/kud-result")
async def api_kud_result(
    body: KudResult, x_engine_token: str = Header(default="")
) -> dict:
    cfg = settings()
    check_engine_token(x_engine_token)
    if body.status not in ("filled", "cashed_out", "no_market", "band_fail",
                           "held", "unfilled", "error", "missed"):
        raise HTTPException(status_code=400, detail="bad status")
    pool = await get_pool()
    await pool.execute(
        """
        UPDATE kud_queue
        SET status=$2, ticker=COALESCE($3, ticker),
            entry_price=COALESCE($4, entry_price),
            qty=COALESCE($5, qty), exit_price=COALESCE($6, exit_price),
            pnl=COALESCE($7, pnl), error=$8, updated_at=now()
        WHERE id=$1
        """,
        body.id, body.status, body.ticker, body.entry_price, body.qty,
        body.exit_price, body.pnl,
        (body.error or None) and body.error[:300])
    return {"ok": True}


@app.get("/api/admin/manual-order", dependencies=[Depends(require_desk)])
async def api_manual_order(id: int = Query(...),
                           venue: str = Query("polymarket")) -> dict:
    """Live status of ONE desk order — the ticket polls this at 1s
    until terminal so the trader watches the AI counterparty execute
    in real time (owner order 2026-08-21: confirmation must be
    instant, not a blotter refresh).

    venue=kalshi reads the relay queue row. Found 2026-08-22 at
    integration: live_orders ids and manual_kalshi_queue ids are
    independent serials, so the old single-table lookup left every
    Kalshi ticket spinning on found:false (or, worse, could collide
    with an unrelated PM order of the same id) — the venue param
    makes the lookup unambiguous."""
    pool = await get_pool()
    if venue == "kalshi":
        kr = await pool.fetchrow(
            """
            SELECT id, status, error, created_at, ticker,
                   limit_price::float8 AS limit_price,
                   fill_price::float8 AS fill_price,
                   usd::float8 AS usd, count, fill_count
            FROM manual_kalshi_queue WHERE id = $1
            """, id)
        if kr is None:
            return {"found": False}
        fill_count = float(kr["fill_count"] or 0)
        fill_price = float(kr["fill_price"] or 0)
        return {
            "found": True,
            "id": kr["id"],
            "status": kr["status"],
            "terminal": kr["status"] in ("filled", "unfilled",
                                         "error", "cancelled"),
            "error": kr["error"],
            "venue": "kalshi",
            "placed_at": kr["created_at"].isoformat()
                         if kr["created_at"] else None,
            "us_market_slug": kr["ticker"],
            "limit_price": kr["limit_price"],
            "fill_price": kr["fill_price"],
            "requested_usd": float(kr["usd"] or 0),
            "filled_usd": round(fill_count * fill_price, 2),
            "filled_shares": fill_count,
        }
    r = await pool.fetchrow(
        """
        SELECT lo.id, lo.status, lo.error, lo.venue,
               lo.placed_at, lo.us_market_slug,
               lo.limit_price::float8 AS limit_price,
               lo.fill_price::float8 AS fill_price,
               lo.requested_usd::float8 AS requested_usd,
               lo.filled_usd::float8 AS filled_usd,
               lo.filled_shares::float8 AS filled_shares
        FROM live_orders lo
        WHERE lo.id = $1 AND lo.whale_username = 'manual'
        """, id)
    if r is None:
        return {"found": False}
    d = dict(r)
    d["found"] = True
    d["terminal"] = d["status"] in ("filled", "settled", "unfilled",
                                    "rejected", "error", "cashed_out")
    d["placed_at"] = d["placed_at"].isoformat() if d["placed_at"] else None
    return d


@app.get("/api/admin/manual-trades", dependencies=[Depends(require_desk)])
async def api_manual_trades() -> dict:
    """The desk blotter: every manual ticket with status and settled P&L."""
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT lo.id, lo.placed_at, lo.asset, lo.us_market_slug,
               lo.limit_price, lo.fill_price, lo.requested_usd,
               lo.filled_usd, lo.filled_shares, lo.status, lo.pnl,
               lo.settled_at, lo.error,
               m.title AS market_title, mt.outcome
        FROM live_orders lo
        LEFT JOIN market_tokens mt ON mt.token_id = lo.asset
        LEFT JOIN markets m ON m.condition_id = mt.condition_id
        WHERE lo.whale_username = 'manual'
        ORDER BY lo.placed_at DESC
        LIMIT 200
        """)
    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "placed_at": r["placed_at"].isoformat() if r["placed_at"] else None,
            "title": r["market_title"] or r["us_market_slug"] or r["asset"],
            "outcome": r["outcome"],
            "status": r["status"],
            "limit_price": float(r["limit_price"] or 0) or None,
            "fill_price": float(r["fill_price"] or 0) or None,
            "requested_usd": float(r["requested_usd"] or 0),
            "filled_usd": float(r["filled_usd"] or 0),
            "filled_shares": float(r["filled_shares"] or 0),
            "pnl": float(r["pnl"]) if r["pnl"] is not None else None,
            "settled_at": r["settled_at"].isoformat() if r["settled_at"] else None,
            "venue": "polymarket",
            "error": r["error"],
        })
    # Kalshi leg of the desk: queued/relayed orders join the blotter.
    krows = await pool.fetch(
        """
        SELECT id, created_at, ticker, title, side, status,
               limit_price::float8 AS limit_price,
               fill_price::float8 AS fill_price,
               usd::float8 AS usd, count, fill_count, error
        FROM manual_kalshi_queue
        ORDER BY created_at DESC LIMIT 100
        """)
    for r in krows:
        out.append({
            "id": f"k{r['id']}",
            "placed_at": r["created_at"].isoformat() if r["created_at"] else None,
            "title": r["title"] or r["ticker"],
            "outcome": (r["side"] or "yes").upper(),
            "status": r["status"],
            "limit_price": r["limit_price"],
            "fill_price": r["fill_price"],
            "requested_usd": float(r["usd"] or 0),
            "filled_usd": round(float(r["fill_count"] or 0)
                                * float(r["fill_price"] or 0), 2),
            "filled_shares": float(r["fill_count"] or 0),
            "pnl": None,          # Kalshi manual P&L settles venue-side
            "settled_at": None,
            "venue": "kalshi",
            "error": r["error"],
        })
    out.sort(key=lambda t: t["placed_at"] or "", reverse=True)
    day_spent = float(await pool.fetchval(
        """
        SELECT COALESCE((SELECT sum(filled_usd) FROM live_orders
                         WHERE whale_username = 'manual'
                           AND placed_at > now() - interval '24 hours'), 0)
             + COALESCE((SELECT sum(usd) FROM manual_kalshi_queue
                         WHERE status IN ('pending', 'placed', 'filled')
                           AND created_at > now() - interval '24 hours'), 0)
        """) or 0)
    from ..live_executor import MANUAL_DAILY_USD, MANUAL_MAX_PER_ORDER_USD

    return {"trades": out, "day_spent": round(day_spent, 2),
            "day_budget": MANUAL_DAILY_USD,
            "max_per_order": MANUAL_MAX_PER_ORDER_USD}


# ── Desk accounts + cash-out (owner directive 2026-08-22) ────────────


_KALSHI_BALANCE_RE = re.compile(r"balance \$([0-9][0-9,]*(?:\.\d+)?)")


async def _engine_heartbeat_detail() -> dict:
    try:
        pool = await get_pool()
        row = await pool.fetchrow(
            "SELECT detail FROM service_heartbeats "
            "WHERE service='edge_engine'")
    except Exception:  # noqa: BLE001 — accounts degrade, never 500
        return {}
    if row is None:
        return {}
    detail = row["detail"]
    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except ValueError:
            return {}
    return detail if isinstance(detail, dict) else {}


def kalshi_accounts_view(detail: dict, now: float) -> dict:
    """Pure (unit-tested): engine heartbeat detail -> the desk's Kalshi
    account card. Primary source is the engine's kalshi_account export
    (~120s TTL); when a not-yet-upgraded engine hasn't published it,
    degrade to the account-link balance string plus the open book at
    cost — marked degraded, never guessed at marks."""
    ka = (detail or {}).get("kalshi_account")
    if isinstance(ka, dict):
        at = float(ka.get("at") or 0) or None
        positions = []
        for p in ka.get("positions") or []:
            cost, val = p.get("cost_usd"), p.get("value_usd")
            positions.append({
                "ticker": p.get("ticker"), "qty": p.get("qty"),
                "cost_usd": cost, "mark_bid": p.get("mark_bid"),
                "value_usd": val,
                "unrealized": (round(val - cost, 2)
                               if val is not None and cost is not None
                               else None)})
        return {"configured": True,
                "balance_usd": ka.get("balance_usd"),
                "at": at,
                "stale_s": round(now - at, 1) if at else None,
                "exposure_usd": ka.get("exposure_usd"),
                "resting": int(ka.get("resting") or 0),
                "positions": positions}
    link = ((detail or {}).get("account_link") or {})
    klink = link.get("kalshi")
    balance = None
    if isinstance(klink, dict):
        m = _KALSHI_BALANCE_RE.search(str(klink.get("detail") or ""))
        if m:
            balance = float(m.group(1).replace(",", ""))
    ko = (detail or {}).get("kalshi_open") or {}
    positions = [{"ticker": r.get("ticker"), "qty": r.get("qty"),
                  "cost_usd": r.get("cost"), "mark_bid": None,
                  "value_usd": None, "unrealized": None}
                 for r in (ko.get("rows") or [])]
    return {"configured": bool(klink is not None or positions),
            "degraded": True,
            "balance_usd": balance,
            "at": None, "stale_s": None,
            "exposure_usd": ko.get("cost"),
            "resting": 0,
            "positions": positions}


@app.get("/api/desk/history", dependencies=[Depends(require_desk)])
async def api_desk_history(venue: str = Query(...), id: str = Query(...),
                           hours: int = Query(24, ge=1, le=336)) -> dict:
    """Normalized price history for the desk's charts — both venues in
    one shape (thin route; the proxies and 60s cache live in
    desk_history). Venue errors return empty points with HTTP 200:
    charts degrade, desks never break."""
    from .desk_history import history

    return await history(venue, id, hours)


@app.get("/api/desk/accounts")
async def api_desk_accounts(role: str = Depends(require_desk)) -> dict:
    """Both live venue accounts on one card: PM from the venue's own
    portfolio API (30s-cached snapshot), Kalshi from the engine's
    heartbeat export (only the engine holds Kalshi credentials).

    COMMITTED CAPITAL (owner directive 2026-08-22, option 2): the PM
    block carries trading_capital = cash + committed_capital_pm_usd,
    ALWAYS labeled as a composite (committed_usd rides beside it so no
    client can render it without knowing what it is). Desk-password
    sessions see the composite ONLY — raw cash / buying_power /
    account_value are stripped for them (an owner draw is the owner's
    business); the admin token sees the full breakdown. The composite
    is never called cash anywhere, and the raw figure is never
    falsified — restricted sessions simply don't receive it."""
    from .pmus_account import account_snapshot

    now = time.time()
    snap = await account_snapshot()
    # PLATFORM-ONLY POSITIONS (owner 2026-08-22: personal trades placed
    # directly on the venue app share the account but are NOT platform
    # activity — the desk and team views show only what the copy engine
    # and the desk placed). Membership = the platform's own order
    # ledger; a venue position on a market we never ordered is
    # external. Externals are never silently dropped: the admin view
    # carries them in their own list so the owner always sees the whole
    # account somewhere.
    pool = await get_pool()
    ours = await pool.fetch(
        """
        SELECT DISTINCT lower(COALESCE(us_market_slug, '')) AS slug
        FROM live_orders
        WHERE status IN ('submitting', 'filled')
          AND us_market_slug IS NOT NULL
        """)
    our_slugs = {r["slug"] for r in ours if r["slug"]}
    pm_positions, pm_external = [], []
    for r in (snap.get("open_positions") or []):
        cost, value = r.get("cost"), r.get("value")
        row = {
            "market_slug": r.get("market_slug"), "title": r.get("title"),
            "outcome": r.get("outcome"), "qty": r.get("qty"),
            "cost": cost, "value": value,
            "unrealized": (round(value - cost, 2)
                           if value is not None and cost is not None
                           else None)}
        slug = (r.get("market_slug") or "").lower()
        (pm_positions if slug in our_slugs else pm_external).append(row)
    # Open value = the PLATFORM's open book (externals excluded), summed
    # from marked positions; the venue's account-wide assetNotional is
    # only a fallback when we hold no marks at all (it both zeroes out
    # intermittently and would count the owner's personal trades).
    marked = [p["value"] for p in pm_positions
              if p.get("value") is not None]
    open_value = (round(sum(marked), 2) if marked
                  else (snap.get("open_value") if not pm_external
                        else 0.0))
    pm = {"configured": bool(snap.get("configured")),
          "account_value": snap.get("account_value"),
          "cash": snap.get("cash"),
          "buying_power": snap.get("buying_power"),
          "open_value": open_value,
          "unsettled_funds": snap.get("unsettled_funds"),
          "realized_pnl": snap.get("realized_pnl"),
          "positions": pm_positions,
          "recent_trades": snap.get("recent_trades") or []}
    if snap.get("error"):
        pm["error"] = snap["error"]
    if role == "admin":
        # The whole account is always visible SOMEWHERE: externals
        # (owner's personal venue-app trades) ride admin-only.
        pm["external_positions"] = pm_external
        pm["external_count"] = len(pm_external)
    committed = float(settings().committed_capital_pm_usd or 0)
    if pm.get("cash") is not None:
        pm["trading_capital"] = round(pm["cash"] + committed, 2)
        pm["committed_usd"] = round(committed, 2)
    kalshi = kalshi_accounts_view(await _engine_heartbeat_detail(), now)
    k_pos_value = sum(
        (p["value_usd"] if p["value_usd"] is not None
         else (p["cost_usd"] or 0)) or 0
        for p in kalshi["positions"])
    totals = {
        "value": round((pm.get("account_value") or 0) + committed
                       + (kalshi.get("balance_usd") or 0)
                       + k_pos_value, 2),
        "trading_capital": round((pm.get("cash") or 0) + committed
                                 + (kalshi.get("balance_usd") or 0), 2),
        "committed_usd": round(committed, 2),
        "cash": round((pm.get("cash") or 0)
                      + (kalshi.get("balance_usd") or 0), 2),
        "unrealized": round(
            sum(p["unrealized"] or 0 for p in pm_positions)
            + sum(p["unrealized"] or 0 for p in kalshi["positions"]), 2),
    }
    if role != "admin":
        # Owner-draw privacy: composite only for desk sessions.
        for k in ("cash", "buying_power", "account_value"):
            pm.pop(k, None)
        totals.pop("cash", None)
        totals["value"] = round((pm.get("trading_capital") or 0)
                                + (pm.get("open_value") or 0)
                                + (kalshi.get("balance_usd") or 0)
                                + k_pos_value, 2)
    return {"as_of": now, "polymarket": pm, "kalshi": kalshi,
            "totals": totals, "role": role}


class CashOutBody(BaseModel):
    venue: str
    us_slug: str = ""            # PM: the held market's venue slug
    outcome: str = ""            # PM: display only
    ticker: str = ""             # Kalshi: the held market ticker
    qty: int | None = None       # contracts/shares; omit = all held
    min_price: float | None = None


async def _kalshi_held_qty(ticker: str) -> int | None:
    """Held contracts for one ticker, from the engine's heartbeat export
    (kalshi_account first, open-book fallback). None = unknown."""
    detail = await _engine_heartbeat_detail()
    ka = detail.get("kalshi_account")
    if isinstance(ka, dict):
        for p in ka.get("positions") or []:
            if p.get("ticker") == ticker:
                try:
                    return int(p.get("qty") or 0)
                except (TypeError, ValueError):
                    return None
    for r in ((detail.get("kalshi_open") or {}).get("rows") or []):
        if r.get("ticker") == ticker:
            try:
                return int(float(r.get("qty") or 0))
            except (TypeError, ValueError):
                return None
    return None


@app.post("/api/desk/cash-out")
async def api_desk_cash_out(body: CashOutBody,
                            role: str = Depends(require_desk)) -> dict:
    """Sell a held position from the desk. PM executes synchronously
    (platform-side IOC at a protective limit under the live bid);
    Kalshi queues a sell for the engine's relay — only the engine holds
    Kalshi credentials, and it clamps the count to what is actually
    held. Every path fails closed: no bid = refuse, more than held =
    refuse, limits floored at $0.01."""
    if role == "wall":
        raise HTTPException(status_code=403, detail="wall is read-only")
    from ..live_executor import execute_manual_sell, sell_limit_price

    if body.venue == "polymarket-us":
        if not body.us_slug.strip():
            return {"ok": False, "error": "pick a market"}
        return await execute_manual_sell(
            body.us_slug.strip(), qty=body.qty, min_price=body.min_price)
    if body.venue != "kalshi":
        return {"ok": False, "error": "unknown venue"}
    ticker = body.ticker.strip()
    if not ticker:
        return {"ok": False, "error": "pick a market"}
    qty = body.qty
    if qty is None:
        qty = await _kalshi_held_qty(ticker)
        if qty is None:
            return {"ok": False,
                    "error": "position size unknown — pass qty explicitly"}
    qty = int(qty)
    if qty < 1:
        return {"ok": False, "error": "nothing held on this market"}
    # Server-side re-quote — never trust a client-supplied price.
    import httpx

    bid = None
    try:
        async with httpx.AsyncClient(base_url=KALSHI_PUBLIC_API,
                                     timeout=8) as client:
            resp = await client.get("/markets", params={"tickers": ticker})
            ms = (resp.json().get("markets") or []) if \
                resp.status_code == 200 else []
            if ms:
                bid = _kcents(ms[0], "yes_bid")
    except Exception:  # noqa: BLE001
        bid = None
    if bid is None or not (0 < bid < 1):
        return {"ok": False, "error": "no live Kalshi bid for this market"}
    limit = sell_limit_price(bid, body.min_price)
    pool = await get_pool()
    # 30s duplicate-ticket guard, same shape as the buy path: the venue
    # HTTP above takes seconds and a double-click must not queue two
    # real sells.
    dup_id = await pool.fetchval(
        """
        SELECT id FROM manual_kalshi_queue
        WHERE ticker = $1 AND action = 'sell'
          AND status IN ('pending', 'placed')
          AND created_at > now() - interval '30 seconds'
        ORDER BY id DESC LIMIT 1
        """, ticker)
    if dup_id is not None:
        return {"ok": False,
                "error": (f"an identical sell ticket (#{dup_id}) was "
                          "queued seconds ago — check the blotter before "
                          "submitting again")}
    row_id = await pool.fetchval(
        """
        INSERT INTO manual_kalshi_queue
            (ticker, title, side, action, limit_price, count, usd, note)
        VALUES ($1, $2, 'yes', 'sell', $3, $4, $5, $6)
        RETURNING id
        """,
        ticker, ticker, limit, qty, round(qty * limit, 2),
        "desk cash-out")
    return {"ok": True, "queued": True, "row_id": row_id,
            "quoted_bid": bid, "limit_price": limit, "count": qty,
            "detail": ("queued — the engine places the sell within ~10 "
                       "seconds, clamped to the held quantity")}


@app.delete("/api/desk/manual-order/{id}")
async def api_desk_cancel_manual_order(
        id: int, role: str = Depends(require_desk)) -> dict:
    """Cancel a queued (not yet relayed) Kalshi desk order. Only a
    'pending' row can be cancelled — once the relay picked it up the
    order is at the venue and this endpoint says so."""
    if role == "wall":
        raise HTTPException(status_code=403, detail="wall is read-only")
    pool = await get_pool()
    rid = await pool.fetchval(
        "UPDATE manual_kalshi_queue SET status='cancelled', "
        "updated_at=now() WHERE id=$1 AND status='pending' RETURNING id",
        id)
    if rid is None:
        return {"ok": False, "error": "already picked up"}
    return {"ok": True, "cancelled": True}


class EngineMethodologyBody(BaseModel):
    markdown: str
    figures: dict = {}
    generated_ts: float | None = None


@app.post("/api/engine/methodology")
async def engine_methodology_ingest(
    body: EngineMethodologyBody, x_engine_token: str = Header(default="")
) -> dict:
    """The engine publishes its own methodology document.

    It is generated on the worker, because that is where the ledger lives,
    and stored here because that is the only place a human can read it. The
    document is a pure function of config plus the ledger — the numbers are
    computed at generation time, never transcribed — so what is served here
    always describes the system that produced it.
    """
    cfg = settings()
    check_engine_token(x_engine_token)
    from ..db import heartbeat

    await heartbeat("edge_methodology", "ok", {
        "markdown": body.markdown,
        "figures": body.figures,
        "generated_ts": body.generated_ts,
    })
    return {"ok": True, "bytes": len(body.markdown)}


@app.get("/api/engine/methodology")
async def engine_methodology(format: str = Query("json")) -> Any:
    """`?format=md` serves the raw document, for reading or piping."""
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT * FROM service_heartbeats WHERE service='edge_methodology'")
    if row is None:
        raise HTTPException(status_code=404,
                            detail="no methodology published yet")
    detail = row["detail"]
    if isinstance(detail, str):
        detail = json.loads(detail)
    if format == "md":
        return PlainTextResponse(detail.get("markdown", ""),
                                 media_type="text/markdown")
    # The heartbeats table timestamps with beat_at; reading the wrong key
    # here turned every publish into an HTTP 500 that read exactly like
    # "never published" — the worker had been publishing all along.
    return {"updated_at": row["beat_at"], **detail}


@app.get("/api/kalshi-open")
async def kalshi_open() -> dict:
    """The engine's open Kalshi book, slimmed for the public site.

    Published inside the engine heartbeat (detail.kalshi_open) every
    cycle; this endpoint exists so the site does not have to poll the
    full status payload for a dozen rows."""
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT beat_at, detail FROM service_heartbeats "
        "WHERE service='edge_engine'")
    empty = {"n": 0, "cost": 0.0, "rows": []}
    if row is None:
        return empty
    detail = row["detail"]
    if isinstance(detail, str):
        detail = json.loads(detail)
    ko = (detail or {}).get("kalshi_open") or empty
    # ISO 8601, not str(datetime): asyncpg's datetime stringifies with a
    # space separator, which Date.parse treats as NaN on Safari — the card's
    # "as of" age depends on this parsing everywhere.
    beat = row["beat_at"]
    updated = beat.isoformat() if hasattr(beat, "isoformat") else str(beat)
    return {"updated_at": updated, **ko}


# Heartbeat-detail keys that must never reach the public GET (audit
# 2026-08-21): the raw venue account exports let anyone reconstruct the
# entire book, and raw error strings can embed internal URLs. The System
# page reads the rest of the detail (funnel counters, budgets) — strip,
# don't gate. venue_truth reads the DB row directly and is unaffected.
_ENGINE_STATUS_PRIVATE_KEYS = ("kalshi_export_raw", "pmus_export_raw",
                               "venue_export_raw", "kalshi_export",
                               "positions_raw_sample", "fills_raw_sample")


@app.get("/api/engine/status")
async def engine_status() -> dict:
    pool = await get_pool()
    row = await pool.fetchrow("SELECT * FROM service_heartbeats WHERE service='edge_engine'")
    if row is None:
        return {"status": "never_reported",
                "unstamped_drops": dict(_unstamped_drops)}
    d = dict(row)
    if isinstance(d.get("detail"), str):
        d["detail"] = json.loads(d["detail"])
    if isinstance(d.get("detail"), dict):
        det = dict(d["detail"])
        for k in list(det):
            if k in _ENGINE_STATUS_PRIVATE_KEYS or k.endswith("_raw"):
                det.pop(k, None)
        if det.get("last_error"):
            det["last_error"] = str(det["last_error"])[:120]
        d["detail"] = det
    # How often a stale (unstamped) engine process is still posting — a
    # nonzero, growing count means a stray instance is alive somewhere.
    d["unstamped_drops"] = dict(_unstamped_drops)
    return d


@app.get("/api/engine/summary")
async def engine_summary() -> dict:
    pool = await get_pool()
    totals = await pool.fetchrow(
        """
        SELECT count(*)::int AS fills,
               count(*) FILTER (WHERE settled)::int AS settled,
               COALESCE(sum(size_usd), 0)::float8 AS staked,
               COALESCE(sum(size_usd) FILTER (WHERE settled), 0)::float8 AS settled_staked,
               COALESCE(sum(pnl) FILTER (WHERE settled), 0)::float8 AS pnl,
               min(ts) AS first_ts
        FROM engine_fills
        """
    )
    by_venue = await pool.fetch(
        """
        SELECT venue, count(*)::int AS fills,
               COALESCE(sum(size_usd) FILTER (WHERE settled), 0)::float8 AS settled_staked,
               COALESCE(sum(pnl) FILTER (WHERE settled), 0)::float8 AS pnl
        FROM engine_fills GROUP BY venue ORDER BY venue
        """
    )
    by_league = await pool.fetch(
        """
        SELECT league, count(*)::int AS fills,
               COALESCE(sum(pnl) FILTER (WHERE settled), 0)::float8 AS pnl
        FROM engine_fills GROUP BY league ORDER BY pnl DESC NULLS LAST LIMIT 20
        """
    )
    daily = await pool.fetch(
        """
        SELECT settled_at::date AS date, sum(pnl)::float8 AS pnl, count(*)::int AS settled
        FROM engine_fills WHERE settled AND settled_at IS NOT NULL
        GROUP BY 1 ORDER BY 1
        """
    )
    d = dict(totals)
    d["roi"] = d["pnl"] / d["settled_staked"] if d["settled_staked"] else None
    return {
        "totals": d,
        "by_venue": [dict(r) for r in by_venue],
        "by_league": [dict(r) for r in by_league],
        "daily": [{"date": r["date"].isoformat(), "pnl": round(r["pnl"], 2),
                   "volume": 0, "trades": r["settled"]} for r in daily],
    }


@app.get("/api/engine/fills")
async def engine_fills(limit: int = Query(100, le=500), venue: str | None = None) -> list[dict]:
    pool = await get_pool()
    args: list = []
    where = ""
    if venue:
        args.append(venue)
        where = "WHERE ef.venue = $1"
    args.append(limit)
    rows = await pool.fetch(
        f"""
        SELECT ef.id, ef.ts, ef.venue, ef.market_id, ef.outcome_id, ef.league, ef.band,
               ef.limit_price::float8 AS limit_price, ef.size_usd::float8 AS size_usd,
               ef.fair_value::float8 AS fair_value, ef.edge::float8 AS edge,
               ef.would_fill, ef.whale_alignment, ef.settled,
               ef.payout::float8 AS payout, ef.pnl::float8 AS pnl, ef.settled_at,
               COALESCE(m.event_title, m.title) AS market_title, m.sport, mt.outcome
        FROM engine_fills ef
        LEFT JOIN market_tokens mt ON mt.token_id = ef.outcome_id
        LEFT JOIN markets m ON m.condition_id = COALESCE(mt.condition_id, ef.market_id)
        {where}
        ORDER BY ef.ts DESC LIMIT ${len(args)}
        """,
        *args,
    )
    out = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get("whale_alignment"), str):
            d["whale_alignment"] = json.loads(d["whale_alignment"])
        out.append(d)
    return out


@app.post("/api/admin/sms-test", dependencies=[Depends(require_admin)])
async def admin_sms_test() -> dict:
    """Send a test text to every configured SMS recipient and report per-number
    results — the arming check for trade SMS alerts."""
    from ..notifications import sms

    if not sms.enabled():
        return {"ok": False, "configured": False,
                "error": "SMS not configured: set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, "
                         "TWILIO_FROM_NUMBER, SMS_TO_NUMBERS on both backend services"}
    results = await sms.broadcast(
        "SportsAssets: SMS alerts are live. You'll get a text within seconds of "
        "every watched trade.")
    return {"ok": all(r["ok"] for r in results), "configured": True,
            "watch_addresses": sorted(sms.watch_addresses()) or "all whales",
            "results": results}


@app.post("/api/admin/ntfy-test", dependencies=[Depends(require_admin)])
async def admin_ntfy_test() -> dict:
    """Publish a test notification to the configured ntfy topic."""
    from ..notifications import ntfy

    if not ntfy.enabled():
        return {"ok": False, "configured": False,
                "error": "ntfy not configured: set NTFY_TOPIC on both backend services"}
    result = await ntfy.publish(
        "SportsAssets alerts are live",
        "You'll get a notification within seconds of every watched trade.")
    cfg = settings()
    return {**result, "configured": True, "topic": cfg.ntfy_topic,
            "watch_addresses": sorted(ntfy.watch_addresses()) or "all whales"}


@app.post("/api/admin/live/{action}", dependencies=[Depends(require_admin)])
async def admin_live_switch(action: str) -> dict:
    """Kill switch for the LIVE beta. pause = no further orders; resume = re-arm."""
    if action not in ("pause", "resume"):
        raise HTTPException(status_code=400, detail="action must be pause|resume")
    from .live_executor_state import set_paused  # thin helper below

    await set_paused(action == "pause")
    return {"ok": True, "paused": action == "pause"}


_FVM_CACHE: dict = {"ts": 0.0, "data": None}


# 15s payload cache (audit 2026-08-21): this endpoint is polled by every
# open page and ran ~9 queries per request including lifetime full-table
# aggregates over live_orders — the exact load class that OOM-flapped
# this instance before. One viewer's compute serves everyone for 15s.
_LIVE_STATUS_CACHE: dict = {"ts": 0.0, "data": None}
_LIVE_STATUS_LOCK = asyncio.Lock()


@app.get("/api/live-status")
async def live_status() -> dict:
    """LIVE beta account state: config, kill switch, bankroll usage, orders."""
    now = time.time()
    if _LIVE_STATUS_CACHE["data"] is not None \
            and now - _LIVE_STATUS_CACHE["ts"] < 15:
        return _LIVE_STATUS_CACHE["data"]
    async with _LIVE_STATUS_LOCK:
        now = time.time()
        if _LIVE_STATUS_CACHE["data"] is not None \
                and now - _LIVE_STATUS_CACHE["ts"] < 15:
            return _LIVE_STATUS_CACHE["data"]
        data = await _live_status_uncached()
        _LIVE_STATUS_CACHE.update(ts=time.time(), data=data)
        return data


async def _live_status_uncached() -> dict:
    from ..live_executor import PAUSE_KEY, active_venue

    cfg = settings()
    pool = await get_pool()
    paused_val = await pool.fetchval("SELECT value FROM ingestion_state WHERE key=$1", PAUSE_KEY)
    paused = bool(json.loads(paused_val) if isinstance(paused_val, str) else paused_val) \
        if paused_val is not None else False
    agg = await pool.fetchrow(
        """
        SELECT count(*)::int AS orders,
               count(*) FILTER (WHERE status IN ('filled', 'settled'))::int AS fills,
               count(*) FILTER (WHERE status = 'unfilled')::int AS unfilled,
               count(*) FILTER (WHERE status = 'rejected')::int AS unmapped,
               count(*) FILTER (WHERE status = 'error')::int AS errors,
               COALESCE(sum(filled_usd), 0)::float8 AS deployed,
               COALESCE(sum(filled_usd) FILTER
                   (WHERE placed_at > now() - interval '24 hours'), 0)::float8 AS deployed_24h,
               COALESCE(sum(pnl) FILTER (WHERE status = 'settled'), 0)::float8 AS realized_pnl,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY (fill_price - his_price) * 100)
                   FILTER (WHERE fill_price IS NOT NULL) AS live_slippage_p50
        FROM live_orders
        """
    )
    recent = await pool.fetch(
        """
        SELECT lo.placed_at, lo.status, lo.whale_username AS whale,
               lo.his_price::float8 AS his_price,
               lo.limit_price::float8 AS limit_price, lo.fill_price::float8 AS fill_price,
               lo.filled_usd::float8 AS filled_usd, lo.requested_usd::float8 AS requested_usd,
               lo.reaction_s::float8 AS reaction_s, lo.pnl::float8 AS pnl, lo.error,
               lo.venue, lo.us_market_slug,
               COALESCE(m.event_title, m.title, t.market_title) AS market_title,
               COALESCE(mt.outcome, t.outcome) AS outcome
        FROM live_orders lo
        LEFT JOIN trades t ON t.id = lo.trade_id
        LEFT JOIN market_tokens mt ON mt.token_id = lo.asset
        LEFT JOIN markets m ON m.condition_id = COALESCE(mt.condition_id, lo.condition_id)
        ORDER BY lo.placed_at DESC LIMIT 25
        """
    )
    d = dict(agg)
    if d.get("live_slippage_p50") is not None:
        d["live_slippage_p50"] = round(float(d["live_slippage_p50"]), 3)
    # Per-whale grading: RN1 and swisstony must earn promotion on their OWN
    # settled records — a blended number lets one carry the other.
    by_whale = await pool.fetch(
        """
        SELECT COALESCE(whale_username, '?') AS whale,
               count(*) FILTER (WHERE status IN ('filled', 'settled'))::int AS fills,
               COALESCE(sum(filled_usd), 0)::float8 AS deployed,
               count(*) FILTER (WHERE status = 'settled')::int AS settled,
               COALESCE(sum(pnl) FILTER (WHERE status = 'settled'), 0)::float8 AS pnl
        FROM live_orders GROUP BY 1 ORDER BY deployed DESC
        """
    )
    # Sizing audit (owner question 2026-08-21: why is the average settled
    # copy ~$30 against $225 clip caps?): per-whale 24h requested-vs-
    # filled stats make the volume-normalized clip's behavior a served
    # number — n_24h against the whale's baseline explains the shrink,
    # and avg_filled == avg_requested rules out partial fills.
    sizing = await pool.fetch(
        """
        SELECT COALESCE(whale_username, '?') AS whale,
               count(*) FILTER (WHERE status IN
                   ('filled', 'settled', 'cashed_out'))::int AS n_24h,
               count(*) FILTER (WHERE status = 'unfilled')::int
                   AS unfilled_24h,
               count(*) FILTER (WHERE status = 'rejected')::int
                   AS rejected_24h,
               round(avg(requested_usd) FILTER (WHERE status IN
                   ('filled', 'settled', 'cashed_out')), 2)::float8
                   AS avg_req,
               round(avg(filled_usd) FILTER (WHERE status IN
                   ('filled', 'settled', 'cashed_out')), 2)::float8
                   AS avg_filled,
               round(max(requested_usd), 2)::float8 AS max_req,
               round(COALESCE(sum(filled_usd), 0), 2)::float8
                   AS deployed_24h
        FROM live_orders
        WHERE placed_at > now() - interval '24 hours'
          AND COALESCE(whale_username, '') NOT IN ('manual', 'underdog')
        GROUP BY 1 ORDER BY deployed_24h DESC
        """
    )
    # OVERSPEND FORENSICS (2026-08-25): the 24h aggregate showed
    # avg_filled ($363) ABOVE avg_req ($250) on a verified whale. Every
    # writer of filled_usd sets it to filled_shares * fill_price, and an
    # IOC buy cannot fill above its limit — so the aggregate and the
    # money path disagree and one of them is wrong. Serve the RAW rows
    # rather than reasoning about which: requested vs filled on both
    # legs (shares and price) names the culprit on sight. `ratio` > 1 is
    # the alarm; a per-row filled_usd above the authorized clip is a
    # real overspend and must halt sizing.
    fills = await pool.fetch(
        """
        SELECT COALESCE(whale_username, '?') AS whale, status,
               round(requested_usd, 2)::float8 AS req_usd,
               round(requested_shares, 2)::float8 AS req_sh,
               round(limit_price, 4)::float8 AS lim,
               round(filled_shares, 2)::float8 AS fill_sh,
               round(fill_price, 4)::float8 AS fill_px,
               round(filled_usd, 2)::float8 AS fill_usd,
               CASE WHEN COALESCE(requested_usd, 0) > 0
                    THEN round(filled_usd / requested_usd, 3)::float8
               END AS ratio,
               us_market_slug AS slug,
               -- ROUND-TRIP TEST (owner hypothesis 2026-08-25): "these
               -- were the same game bought and sold repeatedly, so the
               -- max stake was only ever $250". Two facts decide it:
               -- how many orders this account placed on THIS market,
               -- and what the venue's own execution list says. One
               -- market appearing once cannot have been round-tripped.
               (SELECT count(*) FROM live_orders o2
                 WHERE o2.us_market_slug = live_orders.us_market_slug
                   AND COALESCE(o2.whale_username, '')
                       NOT IN ('manual', 'underdog'))::int AS orders_on_mkt,
               -- jsonb_array_length THROWS on a JSON null, and COALESCE
               -- does not catch it: `#>` returns SQL NULL for a missing
               -- path but 'null'::jsonb for a present-and-null one. One
               -- such row would 500 this endpoint and silently delete
               -- every FILL line — the exact measurement in flight.
               CASE WHEN jsonb_typeof(raw #> '{response,executions}')
                         = 'array'
                    THEN jsonb_array_length(raw #> '{response,executions}')
                    ELSE 0 END AS n_exec,
               -- THE FALSIFIABLE VERSION (2026-08-25). Every overspent
               -- row so far is ORDER_INTENT_BUY_SHORT, which reads like
               -- "shorts pay the complement". That is only a real
               -- finding if the converse holds: CLEAN shorts would
               -- refute it, and clean longs would support it. The
               -- receipts endpoint only returns overspent rows, so it
               -- cannot see a clean short by construction. Carrying the
               -- intent on EVERY fill makes the claim testable instead
               -- of merely consistent.
               COALESCE(
                   raw #>> '{response,executions,0,order,intent}',
                   raw #>> '{preview,intent}') AS intent,
               -- OUR SIZE AS A MULTIPLE OF HIS (owner 2026-08-25:
               -- "copy both buys and sells at a proportional rate").
               --
               -- plan_order computes min(ratio * his_notional, cap).
               -- If the ratio is set high enough that the cap always
               -- binds, every copy is a flat clip and our size stops
               -- tracking his conviction entirely. Measured on the six
               -- receipts: 72x his size on a $3.46 probe, 0.1x on a
               -- $2,907 conviction trade. Nothing in the system
               -- reported that, so it ran unseen.
               (SELECT round(live_orders.requested_usd
                             / NULLIF(t.notional, 0), 2)::float8
                  FROM trades t WHERE t.id = live_orders.trade_id)
                   AS size_vs_his,
               (SELECT round(t.notional, 2)::float8 FROM trades t
                 WHERE t.id = live_orders.trade_id) AS his_notional,
               to_char(placed_at AT TIME ZONE 'America/New_York',
                       'MM-DD HH24:MI') AS at
        FROM live_orders
        WHERE placed_at > now() - interval '24 hours'
          AND status IN ('filled', 'settled', 'cashed_out')
          AND COALESCE(whale_username, '') NOT IN ('manual', 'underdog')
        ORDER BY filled_usd DESC NULLS LAST
        LIMIT 40
        """
    )
    # Manual-desk diagnostics (owner report 2026-08-21: "trades aren't
    # being processed"): the sleeve's status counts and its last rows
    # WITH their errors ride the public status so the probe reads the
    # exact failure mode instead of a lifetime zero.
    manual_desk = {
        "by_status": {r["status"]: r["n"] for r in await pool.fetch(
            "SELECT status, count(*)::int AS n FROM live_orders "
            "WHERE whale_username = 'manual' GROUP BY 1")},
        "recent": [dict(r) for r in await pool.fetch(
            "SELECT placed_at, status, requested_usd::float8 AS req, "
            "filled_usd::float8 AS filled, venue, "
            "left(COALESCE(error, ''), 200) AS error "
            "FROM live_orders WHERE whale_username = 'manual' "
            "ORDER BY placed_at DESC LIMIT 5")],
    }
    venue = active_venue()
    # Fill-vs-miss aggregate rides the public status (5-min cache) so
    # the hourly probe reads the copy thesis' direct test without an
    # admin credential (owner 2026-08-12: same-or-better must be
    # judged on the graded number, not on theory).
    import time as _time
    fvm = None
    try:
        if (_FVM_CACHE.get("data") is not None
                and _time.time() - _FVM_CACHE.get("ts", 0) < 300):
            fvm = _FVM_CACHE["data"]
        else:
            full = await api_fill_vs_miss(days=7)
            fvm = {w: {"f_n": b["filled_n"], "f_roi": b["filled_roi"],
                       "m_n": b["missed_n"], "m_roi": b["missed_roi"],
                       "m_unres": b["missed_unresolved"]}
                   for w, b in full["whales"].items()}
            _FVM_CACHE.update({"ts": _time.time(), "data": fvm})
    except Exception:  # noqa: BLE001 — status must serve regardless
        fvm = _FVM_CACHE.get("data")
    return {
        "enabled": venue is not None,
        "venue": venue,
        "paused": paused,
        "by_whale": [dict(r) for r in by_whale],
        "sizing_24h": [dict(r) for r in sizing],
        "fills_24h": [dict(r) for r in fills],
        "manual_desk": manual_desk,
        "fill_vs_miss_7d": fvm,
        "caps": {"per_fill": cfg.live_max_per_fill_usd, "daily": cfg.live_max_daily_usd,
                 "total": cfg.live_max_total_usd,
                 "max_slippage_cents": cfg.live_max_slippage_cents},
        "summary": d,
        "recent": [dict(r) for r in recent],
    }


@app.post("/api/admin/overspend-halt-clear",
          dependencies=[Depends(require_admin)])
async def api_overspend_halt_clear() -> dict:
    """Clear the post-fill overspend breaker.

    Deliberately a POST and deliberately not an env var: the breaker
    means the venue charged us more than we authorized on a real fill.
    Clearing it is a decision someone makes after reading
    /api/admin/overspend-receipts, not something a config change does
    as a side effect."""
    pool = await get_pool()
    prev = await pool.fetchval(
        "SELECT value FROM ingestion_state WHERE key=$1",
        "copy_overspend_halt")
    await pool.execute(
        "DELETE FROM ingestion_state WHERE key=$1", "copy_overspend_halt")
    return {"ok": True, "cleared": prev}


@app.get("/api/admin/overspend-halt",
         dependencies=[Depends(require_admin)])
async def api_overspend_halt() -> dict:
    pool = await get_pool()
    v = await pool.fetchval(
        "SELECT value FROM ingestion_state WHERE key=$1",
        "copy_overspend_halt")
    return {"tripped": bool(v), "record": v}


@app.get("/api/admin/true-edge-cashout",
         dependencies=[Depends(require_admin)])
async def api_true_edge_cashout(since_day: str = "2026-08-01",
                                max_reaction_s: float | None = None) -> dict:
    """TRUEEDGE re-graded at the whale's OWN EXIT, not at resolution.

    Owner, 2026-08-25: "it would also mean you understate all whales
    that genuinely sell before settlement (which I have confirmed is a
    number of our whale traders, i.e. SwissTony)."

    He is right, and this is the correction. Every whale number served
    today comes from:

        counterfactual_pnl = (payout - his_price) * (clip / his_price)

    where `payout` is the RESOLUTION price, 1 or 0. A whale who buys at
    0.22 and sells at 0.45 before the match ends made +0.23/share. If
    that outcome later resolves 0, we book -0.22/share. A profitable
    cash-out trader is recorded as a loser, systematically, and the
    faster he takes profits the worse we make him look.

    That is not a rounding issue. The TRUEEDGE cuts (rn1 -6,897,
    ferrarichampions2026 -18,248, 0x2c33 -59,667) were made on this
    number, so any whale who trades that way may have been cut on an
    artifact of our accounting.

    This grades each detected trade at his ACTUAL exit where he made
    one — the notional-weighted price of his later SELLs of that asset
    — and falls back to resolution only where he genuinely held. It
    writes nothing: the stored table stays as it is so the two bases
    can be compared rather than one quietly replacing the other.

    `delta` is the correction per whale. A large positive delta means
    we have been understating him.
    """
    from datetime import datetime as _dt

    pool = await get_pool()
    since_d = _dt.fromisoformat(since_day).date()
    rows = await pool.fetch(
        """
        WITH ex AS (
            -- his notional-weighted exit price per asset, from his own
            -- SELLs. Weighted, not last: a partial scale-out is one
            -- exit at a blended price, not several.
            SELECT w.username AS whale, tr.asset,
                   sum(tr.price * tr.size) / NULLIF(sum(tr.size), 0)
                       AS exit_px,
                   min(tr.ts) AS first_exit
            FROM trades tr JOIN whales w ON w.id = tr.whale_id
            WHERE tr.side = 'SELL'
            GROUP BY 1, 2
        )
        SELECT lower(COALESCE(a.whale_username, '?')) AS whale,
               count(*)::int AS detected,
               count(*) FILTER (WHERE ex.exit_px IS NOT NULL
                                  AND ex.first_exit > a.placed_at)::int
                   AS exited,
               COALESCE(sum(a.counterfactual_pnl), 0)::float8
                   AS cf_settlement,
               COALESCE(sum(
                   CASE WHEN ex.exit_px IS NOT NULL
                             AND ex.first_exit > a.placed_at
                             AND a.his_price > 0
                        THEN (ex.exit_px - a.his_price)
                             * (a.clip_target / a.his_price)
                        ELSE a.counterfactual_pnl END), 0)::float8
                   AS cf_cashout
        FROM ai_trades a
        LEFT JOIN ex ON ex.whale = lower(a.whale_username)
                    AND ex.asset = a.asset
        WHERE a.placed_at >= $1
          AND ($2::float8 IS NULL OR a.reaction_s <= $2::float8)
        GROUP BY 1
        ORDER BY cf_cashout DESC
        """, since_d, max_reaction_s)
    out = []
    for r in rows:
        d = dict(r)
        d["cf_settlement"] = round(d["cf_settlement"], 2)
        d["cf_cashout"] = round(d["cf_cashout"], 2)
        d["delta"] = round(d["cf_cashout"] - d["cf_settlement"], 2)
        det = d.get("detected") or 0
        d["exit_rate"] = round((d.get("exited") or 0) / det, 3) if det else None
        # The line that matters for the cut list.
        # NO EXITS MEANS NO SECOND BASIS (2026-08-25, corrected).
        #
        # First version emitted "negative on both bases" / "positive on
        # both bases" whenever the two numbers matched. They match
        # trivially when the whale has NO recorded sells: cf_cashout
        # falls back to cf_settlement row by row, so the "second basis"
        # is a verbatim copy of the first. The line then reads as two
        # independent confirmations of a cut when it is one number
        # printed twice.
        #
        # Observed: every copied whale returned delta 0.0 with exited
        # 0/N — swisstony 0 out of 142,890. An instrument that
        # manufactures corroboration out of missing data is worse than
        # one that stays silent, because the silence would have been
        # investigated.
        if not (d.get("exited") or 0):
            d["verdict"] = (
                "NO EXIT DATA — cashout basis unavailable; this is the "
                "settlement number repeated, NOT a second opinion")
        elif d["cf_settlement"] <= 0 < d["cf_cashout"]:
            d["verdict"] = ("CUT MAY BE WRONG — negative at settlement, "
                            "positive on his own exits")
        elif d["cf_cashout"] <= 0:
            d["verdict"] = "negative on both bases"
        else:
            d["verdict"] = "positive on both bases"
        out.append(d)
    return {"since": since_day, "max_reaction_s": max_reaction_s,
            "whales": out,
            "note": ("cf_settlement is what every whale number served "
                     "today used. cf_cashout grades at his own exit "
                     "where he made one. Nothing is overwritten.")}


@app.get("/api/admin/whale-side-census",
         dependencies=[Depends(require_admin)])
async def api_whale_side_census() -> dict:
    """BUY vs SELL rows per whale, ALL TIME, by ingestion source.

    The 7-day exit report returned exit_rate 0.0 for every whale we
    copy — swisstony 9,243 assets bought, zero sold — while two whales
    we do NOT copy showed round-trips at 46% and 29%. The owner has
    confirmed from outside the system that several of ours do sell
    before settlement. Our data says they never have.

    One of those is wrong, and it is ours. This narrows where:

      * sells = 0 ALL TIME (not just 7d) means we have never once
        recorded a sale from that wallet — a detection gap, not a
        quiet week.
      * the `source` split says WHICH path is blind. If chain records
        buys and sells but poll records only buys (or vice versa), the
        gap has an address.
      * a whale with sells proves the pipeline CAN store them, so any
        whale without them is missing data rather than not selling.

    No date filter anywhere: the question is existence, not recency.
    """
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT w.username AS whale, tr.source,
               count(*) FILTER (WHERE tr.side = 'BUY')::int  AS buys,
               count(*) FILTER (WHERE tr.side = 'SELL')::int AS sells,
               min(tr.ts) AS first_ts, max(tr.ts) AS last_ts
        FROM trades tr JOIN whales w ON w.id = tr.whale_id
        GROUP BY 1, 2 ORDER BY 1, 2
        """)
    by_whale: dict[str, dict] = {}
    for r in rows:
        d = by_whale.setdefault(r["whale"], {"whale": r["whale"],
                                             "by_source": {},
                                             "buys": 0, "sells": 0})
        d["by_source"][r["source"]] = {"buys": r["buys"],
                                       "sells": r["sells"]}
        d["buys"] += r["buys"]
        d["sells"] += r["sells"]
    out = []
    for d in by_whale.values():
        d["sell_share"] = (round(d["sells"] / (d["buys"] + d["sells"]), 4)
                           if (d["buys"] + d["sells"]) else None)
        d["verdict"] = ("NO SELLS EVER RECORDED — detection gap"
                        if d["sells"] == 0 and d["buys"] > 50
                        else "has sells")
        out.append(d)
    out.sort(key=lambda x: -x["buys"])
    return {"whales": out,
            "note": ("A whale WITH sells proves the pipeline can store "
                     "them; a whale with thousands of buys and zero "
                     "sells is missing data, not abstaining.")}


@app.get("/api/admin/whale-exits",
         dependencies=[Depends(require_admin)])
async def api_whale_exits(days: int = 7) -> dict:
    """Does the whale CASH OUT before settlement? (owner 2026-08-25)

    If he does, two things follow and both matter more than any bug
    found tonight:

    1. Our copy is only half his strategy. We mirror his entries and
       then hold to resolution. If his edge is partly in EXITING —
       taking a winner at 0.80 rather than riding it to 1.00 or 0.00 —
       then copying entries alone is a different, worse strategy that
       we have been grading as if it were his.

    2. Every number we have shown all day understates him. TRUEEDGE,
       fill-vs-miss and the copies audit all settle positions at
       resolution. A whale who sells early books a gain our accounting
       never sees, so he looks worse than he is — and our copies look
       like they track him when they do not.

    `round_trips` is the count of assets he both bought AND later sold.
    `exit_rate` is that as a share of the assets he bought: how much of
    his book he actively closes rather than letting resolve.
    """
    pool = await get_pool()
    days = max(1, min(int(days), 60))
    rows = await pool.fetch(
        """
        WITH t AS (
            SELECT w.username AS whale, tr.asset, tr.side,
                   tr.notional, tr.ts
            FROM trades tr JOIN whales w ON w.id = tr.whale_id
            WHERE tr.ts > now() - interval '1 day' * $1
        ),
        legs AS (
            SELECT whale, asset,
                   min(ts) FILTER (WHERE side = 'BUY')  AS first_buy,
                   max(ts) FILTER (WHERE side = 'SELL') AS last_sell,
                   sum(notional) FILTER (WHERE side = 'BUY')::float8
                       AS bought,
                   sum(notional) FILTER (WHERE side = 'SELL')::float8
                       AS sold
            FROM t GROUP BY 1, 2
        )
        SELECT whale,
               count(*) FILTER (WHERE first_buy IS NOT NULL)::int
                   AS assets_bought,
               count(*) FILTER (WHERE first_buy IS NOT NULL
                                  AND last_sell IS NOT NULL
                                  AND last_sell > first_buy)::int
                   AS round_trips,
               round(COALESCE(sum(bought), 0)::numeric, 2)::float8
                   AS bought_usd,
               round(COALESCE(sum(sold), 0)::numeric, 2)::float8
                   AS sold_usd
        FROM legs GROUP BY 1
        HAVING count(*) FILTER (WHERE first_buy IS NOT NULL) > 0
        ORDER BY round_trips DESC
        """, float(days))
    out = []
    for r in rows:
        d = dict(r)
        ab = d.get("assets_bought") or 0
        d["exit_rate"] = round((d.get("round_trips") or 0) / ab, 3) if ab else None
        out.append(d)
    return {"days": days, "whales": out,
            "note": ("round_trips = assets he bought AND later sold. A "
                     "high exit_rate means our hold-to-settlement copy "
                     "is NOT his strategy, and our settlement-based "
                     "P&L understates him.")}


@app.get("/api/admin/price-truth",
         dependencies=[Depends(require_admin)])
async def api_price_truth(price: float = 0.30, qty: int = 10,
                          family: str = "aec-") -> dict:
    """Which leg does the venue's `price` field name on a BUY_SHORT?

    Runs HERE rather than on the CI runner because the runner has no
    PMUS credentials — the workflow version printed "SKIP: no
    credentials" and measured nothing (2026-08-25 00:57Z).

    Previews the SAME market at the SAME price under both intents and
    reports the venue's own stated cost for each. A preview places no
    order. The arithmetic decides a question I will not decide by
    pattern-matching fills:

      BUY_SHORT cost ~= price*qty      -> price names the side we ask
                                          for; the overspend is
                                          something else
      BUY_SHORT cost ~= (1-price)*qty  -> price names the LONG leg, so
                                          every short copy has been
                                          paying the complement
    """
    from . import pmus_account  # noqa: F401 — ensures creds are loaded

    from .. import pmus as _pmus

    def _amt(a) -> float:
        try:
            return float((a or {}).get("value") or 0)
        except (TypeError, ValueError):
            return 0.0

    price = max(0.01, min(float(price), 0.99))
    qty = max(1, min(int(qty), 10))
    ours = round(price * qty, 4)
    comp = round((1 - price) * qty, 4)
    try:
        client = await asyncio.to_thread(_pmus._get_client)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:200]}
    # Any live two-sided market — the point is the arithmetic, not the
    # game. Reuse the premap table so we do not crawl the venue again.
    pool = await get_pool()
    # FAMILY MATTERS (2026-08-25). The first run of this picked the
    # most recently updated premap row — an `astatc` MLB prop — and
    # reported "matches OUR price" for both intents. All five overspend
    # rows are `aec-` tennis, and we already know side semantics differ
    # BY FAMILY (that was the whole shared-identifier finding). Testing
    # the wrong family and reporting it as an answer is the mistake
    # this parameter exists to prevent. Defaults to aec-.
    slug = await pool.fetchval(
        "SELECT identifier FROM us_premap "
        "WHERE intent IS NOT NULL AND identifier IS NOT NULL "
        "AND identifier LIKE $1 || '%' "
        "ORDER BY updated_at DESC LIMIT 1", family)
    if not slug:
        slug = await pool.fetchval(
            "SELECT identifier FROM us_premap "
            "WHERE intent IS NOT NULL AND identifier IS NOT NULL "
            "ORDER BY updated_at DESC LIMIT 1")
    if not slug:
        return {"ok": False, "error": "no premap row to preview against"}
    out = {"ok": True, "market": slug, "family": family,
           "price": price, "qty": qty,
           "ours": ours, "complement": comp, "legs": {}}
    for intent in ("ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT"):
        req = {"marketSlug": slug, "intent": intent,
               "type": "ORDER_TYPE_LIMIT",
               "price": {"value": str(price)}, "quantity": qty,
               "tif": "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"}
        try:
            pv = await asyncio.to_thread(
                client.orders.preview, {"request": req})
            o = (pv or {}).get("order") or {}
            cash = _amt(o.get("cashOrderQty"))
            px = _amt(o.get("price"))
            if not cash and px:
                cash = round(px * float(o.get("quantity") or qty), 4)
            verdict = "venue stated no cost"
            if cash:
                if abs(cash - ours) <= 0.02:
                    verdict = "matches OUR price — this leg is priced as asked"
                elif abs(cash - comp) <= 0.02:
                    verdict = ("COMPLEMENT — venue charges (1-price); we "
                               "have been passing the wrong leg's price")
                else:
                    verdict = f"neither ours ({ours}) nor complement ({comp})"
            out["legs"][intent[-10:]] = {
                "venue_cost": cash, "venue_price": px,
                "venue_qty": o.get("quantity"),
                "ratio_to_ours": round(cash / ours, 4) if ours else None,
                "verdict": verdict}
        except Exception as exc:  # noqa: BLE001 — report, never infer
            out["legs"][intent[-10:]] = {
                "error": f"{type(exc).__name__}: {str(exc)[:180]}"}
    return out


@app.get("/api/admin/overspend-receipts",
         dependencies=[Depends(require_admin)])
async def api_overspend_receipts(hours: int = 48) -> dict:
    """The venue's OWN execution records for every fill that cost more
    than it was authorized to (owner question 2026-08-25).

    The owner's hypothesis for the 1.15x-3.87x rows: "the order was
    bought, then sold for profit, rebought with the same 250, sold
    again" — i.e. round-trips on ONE market, so the true stake never
    exceeded the clip and filled_usd is just aggregating them.

    That is decidable from stored data, not from argument:

      * `executions` is the venue's list for THIS order. A single buy
        that walked the book shows several fills, all at or below the
        limit — an IOC buy cannot pay more. Round-trips would not
        appear here at all; they would be separate rows.
      * `orders_on_market` counts how many orders this account ever
        placed on the same slug. Round-tripping requires more than one.
        The never-add gate and the one-fill-per-asset index are both
        designed to make that impossible, so a count of 1 refutes the
        hypothesis and a count above 1 supports it.
      * `exec_max_px` vs `limit_price` is the decisive number. Every
        execution at or below the limit means we were never overcharged
        and the recorded fill_price is wrong. Any execution above it
        means the venue really did charge more than we authorized.
    """
    pool = await get_pool()
    hours = max(1, min(int(hours), 24 * 14))
    rows = await pool.fetch(
        """
        SELECT id, whale_username AS whale, us_market_slug AS slug,
               status,
               round(limit_price, 4)::float8 AS limit_price,
               round(requested_usd, 2)::float8 AS requested_usd,
               round(requested_shares, 2)::float8 AS requested_shares,
               round(filled_shares, 2)::float8 AS filled_shares,
               round(fill_price, 4)::float8 AS fill_price,
               round(filled_usd, 2)::float8 AS filled_usd,
               round(pnl, 2)::float8 AS pnl,
               to_char(placed_at AT TIME ZONE 'America/New_York',
                       'MM-DD HH24:MI:SS') AS placed_at,
               (SELECT count(*) FROM live_orders o2
                 WHERE o2.us_market_slug = live_orders.us_market_slug
                   AND COALESCE(o2.whale_username, '')
                       NOT IN ('manual', 'underdog'))::int
                   AS orders_on_market,
               CASE WHEN jsonb_typeof(raw #> '{response,executions}')
                         = 'array'
                    THEN raw #> '{response,executions}'
                    ELSE '[]'::jsonb END AS executions,
               -- THE WHALE'S OWN TRADE (owner hypothesis 2026-08-25:
               -- "he is cashing out — selling before settlement").
               --
               -- The arithmetic already says we sized from one price
               -- and bought at another: 249.92/0.22 = 1136 shares,
               -- 1136 x 0.78 = $886.08, the observed fill to the cent.
               -- So the venue charged correctly for the side we
               -- ordered; OUR price and OUR side disagreed.
               --
               -- If his source trade was a SELL, that is the whole
               -- story: his 0.22 is the price of a side he was LEAVING,
               -- and copying it as an entry buys the complement. This
               -- column is the test. maybe_execute refuses side != BUY,
               -- so a SELL here would mean the refusal is being reached
               -- with the wrong side already recorded.
               (SELECT t.side FROM trades t
                 WHERE t.id = live_orders.trade_id) AS his_side,
               (SELECT t.outcome FROM trades t
                 WHERE t.id = live_orders.trade_id) AS his_outcome,
               (SELECT round(t.price, 4)::float8 FROM trades t
                 WHERE t.id = live_orders.trade_id) AS his_trade_price,
               (SELECT round(t.size, 2)::float8 FROM trades t
                 WHERE t.id = live_orders.trade_id) AS his_size
        FROM live_orders
        WHERE placed_at > now() - interval '1 hour' * $1
          AND status IN ('filled', 'settled', 'cashed_out')
          AND COALESCE(whale_username, '') NOT IN ('manual', 'underdog')
          AND COALESCE(requested_usd, 0) > 0
          AND filled_usd > requested_usd * 1.01
        ORDER BY filled_usd / requested_usd DESC
        LIMIT 25
        """, float(hours))
    out = []
    for r in rows:
        d = dict(r)
        execs = d.pop("executions", None)
        if isinstance(execs, str):
            try:
                execs = json.loads(execs)
            except (TypeError, ValueError):
                execs = []
        execs = execs or []
        # Flatten each execution to the three numbers that matter, so
        # the answer is readable in a probe line rather than a blob.
        flat, mx = [], None
        for e in execs:
            if not isinstance(e, dict):
                continue
            px = e.get("lastPx")
            px = (px or {}).get("value") if isinstance(px, dict) else px
            try:
                px = float(px) if px is not None else None
            except (TypeError, ValueError):
                px = None
            try:
                sh = float(e.get("lastShares") or 0)
            except (TypeError, ValueError):
                sh = 0.0
            # WHICH INSTRUMENT DID WE ACTUALLY GET? (2026-08-25)
            # The complement hypothesis says the venue priced the other
            # leg. A simpler and worse possibility is that it FILLED the
            # other leg — a different instrument than the slug names.
            # Those look identical in price and are opposite in
            # position. Carry whatever identity the execution states,
            # plus the key list, because guessing which field names the
            # instrument is how the original incident happened.
            ident = {}
            for k in ("marketSlug", "instrumentId", "instrument",
                      "marketSideId", "sideId", "identifier", "intent",
                      "side", "long"):
                if e.get(k) is not None:
                    ident[k] = str(e.get(k))[:60]
            o = e.get("order")
            if isinstance(o, dict):
                for k in ("marketSlug", "intent", "instrumentId"):
                    if o.get(k) is not None:
                        ident[f"order.{k}"] = str(o.get(k))[:60]
                # The venue echoes back the price WE sent. Comparing it
                # to lastPx is the whole question: same number means it
                # honoured our limit, different means it did not.
                if isinstance(o.get("price"), dict):
                    ident["order.price"] = str(
                        o["price"].get("value"))[:20]
            # legPrices (2026-08-25): the venue states a price PER LEG
            # on every execution. Every overspent fill is BUY_SHORT and
            # filled near (1 - our price). If legPrices shows our number
            # on one leg and the fill price on the other, the venue's
            # `price` field names the LONG leg even on a short order —
            # and PRICE-TRUTH could not see it, because the PREVIEW
            # echoes price*qty naively while EXECUTION does the real
            # conversion. This field settles it.
            lp = e.get("legPrices")
            if lp is not None:
                ident["legPrices"] = str(lp)[:200]
            flat.append({"type": e.get("type"), "px": px, "shares": sh,
                         "ident": ident or None,
                         "keys": sorted(e.keys())[:14]})
            if px is not None and sh > 0:
                mx = px if mx is None else max(mx, px)
        d["n_executions"] = len(flat)
        d["executions"] = flat[:12]
        d["exec_max_px"] = mx
        lim = d.get("limit_price") or 0
        # The verdict, computed here so no reader has to eyeball it.
        if mx is None:
            d["verdict"] = "no execution prices recorded — undecidable"
        elif mx <= lim + 1e-9:
            d["verdict"] = ("executions all AT OR BELOW limit — we were "
                            "NOT overcharged; recorded fill_price is wrong")
        else:
            d["verdict"] = (f"execution at {mx} ABOVE limit {lim} — the "
                            "venue charged more than authorized")
        # WHICH SIDE DID WE ASK FOR, AND WHICH DID WE GET? (2026-08-25)
        #
        # order.price echoes back the price WE sent (0.22) while the
        # fill lands at 0.78 — exactly 1-0.22. The venue did not
        # reinterpret our number; it recorded our order at 0.22 and
        # filled us on the instrument trading at 0.78. That is the
        # OPPOSITE SIDE, not a pricing convention.
        #
        # If that is right, the bug is in the INTENT we derive, not in
        # the price we send, and it is the original wrong-side incident
        # still live. The side echo cannot see it because it re-derives
        # the intent with the SAME logic and agrees with itself.
        #
        # So ask the venue directly: which side carries the outcome we
        # copied, and what is its `long` flag? Compare that to the
        # intent we actually sent. A disagreement is the whole answer.
        try:
            from .. import pmus as _pmus

            m = await asyncio.to_thread(
                _pmus._get_client().markets.retrieve_by_slug, d["slug"])
            sides = ((m or {}).get("market") or {}).get("marketSides") or []
            d["venue_sides"] = [
                {"desc": str(sd.get("description"))[:40],
                 "long": sd.get("long"),
                 "price": sd.get("price")}
                for sd in sides if isinstance(sd, dict)][:4]
            sent = (d.get("executions") or [{}])[0].get(
                "ident", {}).get("order.intent") or ""
            want_long = sent.endswith("BUY_LONG")
            # The side whose price matches what we authorized is the
            # side we MEANT to buy.
            lim = d.get("limit_price")
            meant = None
            for sd in d["venue_sides"]:
                try:
                    if lim and abs(float(sd["price"]) - float(lim)) <= 0.06:
                        meant = sd
                except (TypeError, ValueError):
                    continue
            if meant is not None and meant.get("long") is not None:
                d["side_verdict"] = (
                    f"we authorized {lim}; the side priced near that is "
                    f"'{meant['desc']}' with long={meant['long']}; we "
                    f"sent {sent or '?'} — "
                    + ("AGREES"
                       if bool(meant["long"]) == want_long else
                       "INVERTED: we bought the opposite side"))
            else:
                d["side_verdict"] = (
                    "no venue side is priced near our limit — cannot "
                    "attribute; do not infer")
        except Exception as exc:  # noqa: BLE001 — report, never infer
            d["side_verdict"] = f"unreadable: {type(exc).__name__}"
        out.append(d)
    return {"hours": hours, "n": len(out), "rows": out}


@app.get("/api/copy-unmapped")
async def api_copy_unmapped(days: int | None = None) -> dict:
    """Breakdown of the copy sleeve's rejected rows — the number the site
    shows as 'unmapped' (2026-08-10, unmapped-funnel work). The counter
    blends three different writers (mapping failures, no-stack refusals,
    manual-desk refusals) and a league mix that is largely world-soccer
    flow the US venue simply does not list; this endpoint separates
    'mapper bug we can fix' from 'venue does not carry it' without a
    database dig. Pure DB read — never calls a venue."""
    from ..copy_sports import league_of, market_type_of

    pool = await get_pool()
    where_days = "AND lo.placed_at > now() - make_interval(days => $1)" \
        if days and days > 0 else ""
    args = [int(days)] if days and days > 0 else []
    rows = await pool.fetch(
        f"""
        SELECT lower(COALESCE(lo.whale_username, '?')) AS whale,
               COALESCE(t.market_slug, t.event_slug, '') AS slug,
               CASE WHEN lo.error LIKE 'no-stack%' THEN 'no_stack'
                    WHEN lo.error LIKE 'never-add%' THEN 'never_add'
                    WHEN lo.error LIKE 'one position per game%'
                         THEN 'one_per_game'
                    WHEN lo.error LIKE 'unmapped%' THEN 'unmapped'
                    ELSE 'no_us_market' END AS reason,
               count(*)::int AS n,
               count(*) FILTER
                   (WHERE lo.placed_at > now() - interval '7 days')::int
                   AS n_7d,
               -- WINNABLE SPLIT (owner question 2026-08-13: 'how do we
               -- fix the unmapping error'). The mapper's own diag
               -- strings already say which failure this was:
               --   'sides:[' = the venue LISTED the event and handed us
               --   its markets — every one of these is OUR bug, fixable.
               --   every search 0ev (and none positive: the !~ guard
               --   below) with no sides seen = the venue does not
               --   carry it — unwinnable by code. A diag where a LATER
               --   query found events is a mapper failure, not a venue
               --   gap, and lands in undiagnosed.
               -- Undiagnosed remainder stays its own bucket, never
               -- guessed into either.
               count(*) FILTER (WHERE lo.error LIKE '%sides:[%')::int
                   AS n_listed,
               count(*) FILTER (WHERE lo.error LIKE '%0ev%'
                   AND lo.error NOT LIKE '%sides:[%'
                   AND lo.error !~ ':[1-9][0-9]*ev')::int AS n_0ev,
               count(*) FILTER (WHERE lo.error LIKE '%sides:[%'
                   AND lo.placed_at > now() - interval '7 days')::int
                   AS n_listed_7d,
               count(*) FILTER (WHERE lo.error LIKE '%0ev%'
                   AND lo.error NOT LIKE '%sides:[%'
                   AND lo.error !~ ':[1-9][0-9]*ev'
                   AND lo.placed_at > now() - interval '7 days')::int
                   AS n_0ev_7d
        FROM live_orders lo
        LEFT JOIN trades t ON t.id = lo.trade_id
        WHERE lo.status = 'rejected' {where_days}
        GROUP BY 1, 2, 3
        """,
        *args,
    )
    # '(no slug)' enrichability: a rejected row whose trade STILL has no
    # metadata either has a token our catalog now knows (the hourly
    # sweep will map it on retry once enrichment lands) or a token
    # nothing knows (the enrichment gap itself). Separate query — tiny.
    noslug = await pool.fetchrow(
        f"""
        SELECT count(*)::int AS rows,
               count(*) FILTER (WHERE mt.token_id IS NOT NULL)::int
                   AS catalog_has_token
        FROM live_orders lo
        LEFT JOIN trades t ON t.id = lo.trade_id
        LEFT JOIN market_tokens mt ON mt.token_id = lo.asset
        WHERE lo.status = 'rejected'
          AND COALESCE(t.market_slug, t.event_slug, '') = ''
          {where_days}
        """, *args)
    by_reason: dict[str, int] = {}
    by_whale: dict[str, int] = {}
    by_league: dict[str, dict] = {}
    by_type: dict[str, int] = {}
    winnable = {"listed_mapper_fail": 0, "venue_unlisted": 0,
                "undiagnosed": 0, "listed_mapper_fail_7d": 0,
                "venue_unlisted_7d": 0}
    total = 0
    total_7d = 0
    for r in rows:
        n = r["n"]
        total += n
        total_7d += r["n_7d"]
        by_reason[r["reason"]] = by_reason.get(r["reason"], 0) + n
        if r["reason"] == "unmapped":
            winnable["listed_mapper_fail"] += r["n_listed"]
            winnable["venue_unlisted"] += r["n_0ev"]
            winnable["undiagnosed"] += n - r["n_listed"] - r["n_0ev"]
            winnable["listed_mapper_fail_7d"] += r["n_listed_7d"]
            winnable["venue_unlisted_7d"] += r["n_0ev_7d"]
        by_whale[r["whale"]] = by_whale.get(r["whale"], 0) + n
        slug = r["slug"] or ""
        league = league_of(slug) if slug else "(no slug)"
        mtype = market_type_of(slug) if slug else "unknown"
        lg = by_league.setdefault(league, {"n": 0, "n_7d": 0, "types": {}})
        lg["n"] += n
        lg["n_7d"] += r["n_7d"]
        lg["types"][mtype] = lg["types"].get(mtype, 0) + n
        by_type[mtype] = by_type.get(mtype, 0) + n
    leagues = sorted(by_league.items(), key=lambda kv: -kv[1]["n"])[:30]
    return {
        "totals": {"rows": total, "recent_7d": total_7d},
        "winnable": winnable,
        "no_slug": {"rows": noslug["rows"],
                    "catalog_has_token": noslug["catalog_has_token"],
                    "token_unknown": noslug["rows"]
                    - noslug["catalog_has_token"]},
        "by_reason": [{"reason": k, "n": v}
                      for k, v in sorted(by_reason.items(),
                                         key=lambda kv: -kv[1])],
        "by_market_type": [{"market_type": k, "n": v}
                           for k, v in sorted(by_type.items(),
                                              key=lambda kv: -kv[1])],
        "by_whale": [{"whale": k, "n": v}
                     for k, v in sorted(by_whale.items(),
                                        key=lambda kv: -kv[1])],
        "by_league": [{"league": k, "n": v["n"], "n_7d": v["n_7d"],
                       "market_types": dict(sorted(v["types"].items(),
                                                   key=lambda kv: -kv[1]))}
                      for k, v in leagues],
    }


@app.get("/api/track-record")
async def api_track_record(since: str | None = Query(None),
                           max_stake: float | None = Query(None)) -> dict:
    """The AI trader's record from the ACTUAL venue account, windowed on
    entry time (default 2026-08-01). The shadow mirror is not a record.

    `max_stake` excludes positions costing more than it — and the payload
    then carries `excluded_over_limit` (count, stake, net P&L) so any
    consumer can, and the site does, disclose what the view leaves out.
    The unfiltered record remains the default."""
    from .track_record import track_record

    return await track_record(since, max_stake=max_stake)


def _parse_day(s: str | None, default: str) -> str:
    from datetime import datetime as _dt

    try:
        return _dt.strptime((s or "").strip(), "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        return default


def _today_et() -> str:
    from datetime import datetime as _dt

    from .track_record import RECORD_TZ

    return _dt.now(RECORD_TZ).strftime("%Y-%m-%d")


# Strong refs for shielded background fetches (asyncio only weakly
# references running tasks; without this a warmup crawl could be GC'd).
_bg_tasks: set = set()


async def _category_breakdown(from_day: str, to_day: str) -> dict:
    """Per-ET-day results by category over a date range (owner reports
    2026-08-06/07): each live sleeve (RN1, swisstony, kch123,
    HomeRunHazard, manual) from the order-level audit table; software
    and arbitrage from the venue-account record split by the engine
    mirror's band tag; unattributed folded into software (owner rule).
    The site's ±$100 single-trade display cap applies throughout."""
    from datetime import datetime as _dt

    from .track_record import (AUDIT_SINCE, PNL_DISPLAY_CAP, RECORD_TZ,
                               track_record)

    pool = await get_pool()
    copies = await pool.fetch(
        """
        SELECT lower(COALESCE(whale_username, '?')) AS whale,
               to_char(settled_at AT TIME ZONE 'America/New_York',
                       'YYYY-MM-DD') AS day,
               count(*)::int AS settled,
               count(*) FILTER (WHERE pnl > 0)::int AS wins,
               count(*) FILTER (WHERE pnl < 0)::int AS losses,
               COALESCE(sum(pnl), 0)::float8 AS pnl
        FROM live_orders
        WHERE status = 'settled' AND settled_at IS NOT NULL
          -- Owner directive 2026-08-06: a single order swinging the P&L
          -- past the display cap is an anomaly, not the record.
          AND abs(COALESCE(pnl, 0)) <= $1
        GROUP BY 1, 2
        """, PNL_DISPLAY_CAP)
    arb_rows = await pool.fetch(
        "SELECT DISTINCT outcome_id FROM engine_fills "
        "WHERE band IN ('arb', 'arb_crypto')")
    arb_slugs = {r["outcome_id"] for r in arb_rows}
    # AUDIT SINCE (2026-08-25): this breakdown spans from first_day and
    # its sleeve rows come from live_orders with NO date floor, so the
    # account anchor must span the same period. track_record(None) reads
    # the DISPLAY epoch, which the 2026-08-24 re-baseline moved forward
    # — anchoring here would report every pre-epoch settled row as
    # unattributed. Display windows move; audits do not.
    from .track_record import AUDIT_SINCE as _audit_since

    rec = await track_record(_audit_since)
    first_day = "2026-08-01"

    days: dict[str, dict] = {}

    def _cat(day: str, cat: str) -> dict:
        d = days.setdefault(day, {})
        return d.setdefault(cat, {"pnl": 0.0, "settled": 0,
                                  "wins": 0, "losses": 0})

    def _in_range(day: str) -> bool:
        return from_day <= day <= to_day

    # RECONCILED BY CONSTRUCTION (owner report 2026-08-07: "the reports
    # don't line up with the daily PNLs"). The venue-account calendar is
    # the ANCHOR: each day's category rows must sum to that day's
    # calendar P&L exactly. Copies/manual come from the order-level
    # audit table (they are venue-account trades, so they subtract);
    # arb from the record's band-tagged rows; SOFTWARE IS THE DERIVED
    # REMAINDER — the same identity used in the owner's ops PDF, which
    # ties out to the account to the penny instead of drifting across
    # two accounting bases.
    acct_by_day: dict[str, float] = {}
    for d in rec.get("daily") or []:
        day = d.get("date") or ""
        if _in_range(day):
            acct_by_day[day] = float(d.get("pnl") or 0)

    for r in copies:
        # Each live sleeve is its own category: the source whales plus
        # the admin manual desk (owner 2026-08-07). A whale missing from
        # this tuple silently leaks its P&L into the derived Software
        # remainder — extend it with every promotion.
        if r["whale"] not in ("rn1", "swisstony", "kch123",
                              "homerunhazard", "manual",
                              "underdog",
                              "ferrarichampions2026", "0x076daa87",
                              "0x2c335066fe58fe9237c3d3dc7b275c2a034a0563"
                              "-1759935795465"):
            continue
        day = max(r["day"], first_day)
        if not _in_range(day):
            continue
        c = _cat(day, r["whale"])
        c["pnl"] = round(c["pnl"] + r["pnl"], 4)
        c["settled"] += r["settled"]
        c["wins"] += r["wins"]
        c["losses"] += r["losses"]

    for r in rec.get("trades") or []:
        if r.get("sleeve") == "copy" or not r.get("settled"):
            continue
        ts = r.get("settled_ts") or r.get("entry_ts")
        if not ts:
            continue
        day = max(_dt.fromtimestamp(ts, RECORD_TZ).strftime("%Y-%m-%d"),
                  first_day)
        if not _in_range(day) or r.get("market_slug") not in arb_slugs:
            continue
        pnl = float(r.get("pnl") or 0)
        c = _cat(day, "arb")
        c["pnl"] = round(c["pnl"] + pnl, 4)
        c["settled"] += 1
        c["wins"] += 1 if pnl > 0 else 0
        c["losses"] += 1 if pnl < 0 else 0

    # Software = account minus everything attributed, per day. Wins and
    # losses for the derived remainder come from the record's non-copy,
    # non-arb settled rows (counts are additive even though dollars are
    # derived).
    sw_counts: dict[str, dict] = {}
    for r in rec.get("trades") or []:
        if r.get("sleeve") == "copy" or not r.get("settled") \
                or r.get("market_slug") in arb_slugs:
            continue
        ts = r.get("settled_ts") or r.get("entry_ts")
        if not ts:
            continue
        day = max(_dt.fromtimestamp(ts, RECORD_TZ).strftime("%Y-%m-%d"),
                  first_day)
        if not _in_range(day):
            continue
        sc = sw_counts.setdefault(day, {"settled": 0, "wins": 0,
                                        "losses": 0})
        pnl = float(r.get("pnl") or 0)
        sc["settled"] += 1
        sc["wins"] += 1 if pnl > 0 else 0
        sc["losses"] += 1 if pnl < 0 else 0

    # EXTERNAL (owner) settlements — positive attribution BEFORE the
    # remainder is derived (owner report 2026-08-22: personal trades
    # placed directly on the venue app were landing in 'Software' and
    # reviving the 'software is still firing' scare). A venue
    # resolution on a market absent from EVERY platform ledger is the
    # owner's own activity; it gets its own labeled line. Fail-open:
    # if the venue export is unreachable, the remainder simply stays
    # merged as before.
    try:
        from .pmus_account import venue_export

        ours = {(r.get("market_slug") or "").lower()
                for r in (rec.get("trades") or [])}
        lo_rows = await pool.fetch(
            "SELECT DISTINCT lower(us_market_slug) AS s FROM live_orders "
            "WHERE us_market_slug IS NOT NULL")
        ours |= {r["s"] for r in lo_rows if r["s"]}
        # Bounded (2026-08-23): on a cold cache this crawl runs minutes
        # and was timing out every report endpoint that renders the
        # breakdown. Slow == unavailable here: fail open to the merged
        # remainder — but SHIELD the crawl so it finishes in the
        # background and warms the activities cache for the next render
        # (a bare wait_for cancels it, and the cache never warms).
        vtask = asyncio.ensure_future(venue_export(from_day))
        _bg_tasks.add(vtask)
        vtask.add_done_callback(_bg_tasks.discard)
        vexp = await asyncio.wait_for(asyncio.shield(vtask), timeout=25)
        for vr in (vexp.get("rows") or []):
            if vr.get("kind") != "resolution":
                continue
            slug = (vr.get("slug") or "").lower()
            if not slug or slug in ours:
                continue
            when = vr.get("time") or ""
            if not when:
                continue
            day = (_dt.fromisoformat(when.replace("Z", "+00:00"))
                   .astimezone(RECORD_TZ).strftime("%Y-%m-%d"))
            if not _in_range(day):
                continue
            pnl = float(vr.get("realized_pnl") or 0)
            c = _cat(day, "external")
            c["pnl"] = round(c["pnl"] + pnl, 2)
            c["settled"] += 1
            c["wins"] += 1 if pnl > 0 else 0
            c["losses"] += 1 if pnl < 0 else 0
    except Exception:  # noqa: BLE001 — attribution stays merged
        pass

    for day, acct_pnl in acct_by_day.items():
        attributed = sum(c["pnl"] for c in (days.get(day) or {}).values())
        c = _cat(day, "software")
        c["pnl"] = round(acct_pnl - attributed, 2)
        counts = sw_counts.get(day) or {"settled": 0, "wins": 0,
                                        "losses": 0}
        c["settled"] += counts["settled"]
        c["wins"] += counts["wins"]
        c["losses"] += counts["losses"]

    totals: dict[str, dict] = {}
    for day, d in days.items():
        for cat, c in d.items():
            t = totals.setdefault(cat, {"pnl": 0.0, "settled": 0,
                                        "wins": 0, "losses": 0})
            t["pnl"] = round(t["pnl"] + c["pnl"], 4)
            for k in ("settled", "wins", "losses"):
                t[k] += c[k]
    net = round(sum(t["pnl"] for t in totals.values()), 2)
    out_days = []
    for k in sorted(days):
        out_days.append({"date": k, "account": acct_by_day.get(k),
                         **days[k]})
    return {"from": from_day, "to": to_day,
            "days": out_days,
            "totals": totals, "net_pnl": net,
            "reconciled": True,
            "note": ("reconciled by construction: each day's categories "
                     "sum exactly to the account calendar; copies/manual "
                     "from the order-level audit table, arb from the "
                     "engine mirror's band tag, external is venue "
                     "settlements on markets no platform ledger touched "
                     "(owner activity). The 'software' key is the derived "
                     "remainder (identity method, owner ops PDF v1.1) — "
                     "since the software wind-down completed it holds "
                     "fees, open-stake mark moves, and trades past the "
                     "±$100 display cap, NOT software trading")}


@app.get("/api/admin/order-audit", dependencies=[Depends(require_admin)])
async def api_admin_order_audit(
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
) -> dict:
    """UNCAPPED order-level attribution (owner order 2026-08-17, weekly
    report R5). The public record's ±$100 single-trade display cap made
    every derived report blind to big winners AND big losers — the
    2026-08-10 week read -$4,984 capped against a materially different
    uncapped book, and sleeve attributions were biased positive because
    copy clips losing more than $100 vanished. This surface is the
    management truth: every settled live order, no exclusions, split by
    ET day x sleeve x venue. Admin-token gated — the cap stays on for
    the public site only."""
    from_day = _parse_day(from_, "2026-08-01")
    to_day = _parse_day(to, _today_et())
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT lower(COALESCE(whale_username, '?')) AS category,
               COALESCE(venue, '?') AS venue,
               to_char(settled_at AT TIME ZONE 'America/New_York',
                       'YYYY-MM-DD') AS day,
               count(*)::int AS settled,
               count(*) FILTER (WHERE pnl > 0)::int AS wins,
               count(*) FILTER (WHERE pnl < 0)::int AS losses,
               COALESCE(sum(pnl), 0)::float8 AS pnl,
               COALESCE(sum(filled_usd), 0)::float8 AS filled_usd,
               COALESCE(sum(pnl) FILTER (WHERE abs(pnl) > 100), 0)::float8
                   AS over_cap_pnl,
               count(*) FILTER (WHERE abs(COALESCE(pnl, 0)) > 100)::int
                   AS over_cap_n
        FROM live_orders
        WHERE status = 'settled' AND settled_at IS NOT NULL
        GROUP BY 1, 2, 3
        """)
    days: dict[str, list] = {}
    totals: dict[str, dict] = {}
    for r in rows:
        if not (from_day <= r["day"] <= to_day):
            continue
        d = dict(r)
        days.setdefault(r["day"], []).append(d)
        t = totals.setdefault(r["category"], {
            "pnl": 0.0, "settled": 0, "wins": 0, "losses": 0,
            "filled_usd": 0.0, "over_cap_pnl": 0.0, "over_cap_n": 0})
        t["pnl"] = round(t["pnl"] + r["pnl"], 4)
        t["filled_usd"] = round(t["filled_usd"] + r["filled_usd"], 2)
        t["over_cap_pnl"] = round(t["over_cap_pnl"] + r["over_cap_pnl"], 4)
        for k in ("settled", "wins", "losses", "over_cap_n"):
            t[k] += r[k]
    return {"from": from_day, "to": to_day, "capped": False,
            "days": [{"date": k, "rows": v} for k, v in sorted(days.items())],
            "totals": totals,
            "net_pnl": round(sum(t["pnl"] for t in totals.values()), 2)}


@app.get("/api/daily-breakdown")
async def api_daily_breakdown() -> dict:
    """Month-to-date category breakdown (kept for existing consumers)."""
    return await _category_breakdown("2026-08-01", _today_et())


@app.post("/api/admin/quarantine/{action}",
          dependencies=[Depends(require_admin)])
async def api_quarantine_set(action: str) -> dict:
    """Mapping-quarantine switch (wrong-side incident 2026-08-23).
    'off' resumes the quarantined copy classes after fidelity is
    proven; 'on' re-arms. The env var LIVE_MAPPING_QUARANTINE remains
    a hard override in either direction."""
    if action not in ("on", "off"):
        raise HTTPException(status_code=400, detail="action must be on|off")
    pool = await get_pool()
    await pool.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
        "mapping_quarantine", json.dumps(action == "on"))
    return {"ok": True, "quarantine": action == "on"}


@app.post("/api/admin/premap-live/{action}",
          dependencies=[Depends(require_admin)])
async def api_premap_live_set(action: str) -> dict:
    """The resume lever (owner order 2026-08-24): 'on' lets
    premap-resolved mappings trade while the total quarantine still
    refuses every legacy-resolved mapping. 'off' re-closes it. Flip on
    only after the premap fidelity samples certify."""
    if action not in ("on", "off"):
        raise HTTPException(status_code=400, detail="action must be on|off")
    pool = await get_pool()
    await pool.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
        "premap_live", json.dumps(action == "on"))
    return {"ok": True, "premap_live": action == "on"}


@app.post("/api/admin/side-echo-reset",
          dependencies=[Depends(require_admin)])
async def api_side_echo_reset() -> dict:
    """Clear the side-echo circuit after review. The circuit (tripped
    by a confirmed wrong-side mismatch on a filled copy) deliberately
    has NO env override — this explicit reset is the only way back to
    trading, so a mismatch always gets human eyes before resumption."""
    pool = await get_pool()
    await pool.execute(
        "INSERT INTO ingestion_state (key, value) "
        "VALUES ('side_echo_tripped', 'false'::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value='false'::jsonb")
    return {"ok": True, "side_echo_tripped": False}


@app.get("/api/admin/quarantine", dependencies=[Depends(require_admin)])
async def api_quarantine_get() -> dict:
    pool = await get_pool()
    val = await pool.fetchval(
        "SELECT value FROM ingestion_state WHERE key=$1",
        "mapping_quarantine")
    on = True
    if val is not None:
        try:
            on = bool(json.loads(val) if isinstance(val, str) else val)
        except (TypeError, ValueError):
            on = True
    env = os.getenv("LIVE_MAPPING_QUARANTINE", "")
    if env in ("on", "off"):
        on = env == "on"
    pl_val = await pool.fetchval(
        "SELECT value FROM ingestion_state WHERE key=$1", "premap_live")
    premap_live = False
    if pl_val is not None:
        try:
            premap_live = bool(json.loads(pl_val)
                               if isinstance(pl_val, str) else pl_val)
        except (TypeError, ValueError):
            premap_live = False
    pl_env = os.getenv("LIVE_PREMAP", "")
    if pl_env in ("on", "off"):
        premap_live = pl_env == "on"
    return {"quarantine": on, "env_override": env or None,
            "premap_live": premap_live,
            "premap_env_override": pl_env or None}


@app.get("/api/admin/whale-true-edge", dependencies=[Depends(require_admin)])
async def api_whale_true_edge(since_day: str = "2026-08-01",
                              max_reaction_s: float | None = None) -> dict:
    """The owner's verification (2026-08-24: 'will we profit — verified,
    not guessed'). ai_trades holds EVERY detected whale trade with two
    settled results: counterfactual_pnl at HIS price (his true edge on
    the full detected book — no fill-selection bias) and pnl from a
    depth-walked paper fill at OUR real reaction time. Per whale:
      cf_total      his edge, full book, his prices  ← the thesis test
      cf_on_filled  his edge on just the trades our paper fill caught
      paper_actual  what our reaction time actually achieves
    cf_total>0 and paper_actual<0 = pure latency problem (engineering
    fixes it). cf_total<=0 = the whale isn't copyable at ANY speed.

    max_reaction_s (owner order 2026-08-24 evening: "make sure we are
    profitable copying SwissTony") answers the question the blended
    figure cannot. paper_actual averages over MONTHS of detections,
    most of them minutes-late polling — so for a whale whose edge
    decays fast it reports the old world forever, however quick we
    became today. Restricting to trades we detected within N seconds
    measures what our CURRENT speed achieves on his flow, which is the
    only number that can justify resuming him."""
    from datetime import datetime as _dt

    pool = await get_pool()
    since_d = _dt.fromisoformat(since_day).date()
    rows = await pool.fetch(
        """
        SELECT lower(COALESCE(whale_username, '?')) AS whale,
               count(*)::int AS detected,
               count(*) FILTER (WHERE filled_notional > 0)::int AS filled,
               count(*) FILTER (WHERE status = 'missed')::int AS missed,
               COALESCE(sum(counterfactual_pnl), 0)::float8 AS cf_total,
               COALESCE(sum(counterfactual_pnl)
                        FILTER (WHERE filled_notional > 0), 0)::float8
                   AS cf_on_filled,
               COALESCE(sum(pnl) FILTER (WHERE status = 'settled'
                        AND filled_notional > 0), 0)::float8 AS paper_actual,
               COALESCE(sum(clip_target), 0)::float8 AS clip_total,
               count(*) FILTER (WHERE counterfactual_pnl > 0)::int AS cf_wins,
               count(*) FILTER (WHERE counterfactual_pnl IS NOT NULL
                        AND abs(counterfactual_pnl) >= 0.005)::int AS cf_graded
        FROM ai_trades
        WHERE placed_at >= $1
          AND ($2::float8 IS NULL OR reaction_s <= $2::float8)
        GROUP BY 1
        ORDER BY cf_total DESC
        """, since_d, max_reaction_s)
    whales = []
    for r in rows:
        d = dict(r)
        d["cf_total"] = round(d["cf_total"], 2)
        d["cf_on_filled"] = round(d["cf_on_filled"], 2)
        d["paper_actual"] = round(d["paper_actual"], 2)
        d["clip_total"] = round(d["clip_total"], 2)
        d["miss_selection"] = round(d["cf_total"] - d["cf_on_filled"], 2)
        d["latency_price_cost"] = round(d["cf_on_filled"]
                                        - d["paper_actual"], 2)
        whales.append(d)
    return {"since": since_day, "max_reaction_s": max_reaction_s,
            "whales": whales,
            "note": ("counterfactuals settle on the whale's own venue "
                     "(global), where resolution data was always "
                     "correct — this table is untouched by the "
                     "settlement incident.")}


@app.get("/api/admin/edge-decay", dependencies=[Depends(require_admin)])
async def api_edge_decay(since_day: str = "2026-08-01") -> dict:
    """The syllogism test, per whale (owner order 2026-08-24: 'if we copy
    their trades at the same or better price we must match their
    profit'). For every settled copy row we hold the whale's price
    (his_price), our fill (fill_price), our reaction time (reaction_s)
    and the venue-true result — so each whale's certified P&L decomposes
    into: his edge on OUR filled subset (P&L had we filled at his
    price), the price drag our latency cost, and fees/other. A whale
    with positive edge_at_his_price is copyable the moment our fills
    reach price parity; one negative even at his prices is either
    unlucky in our subset (selection effect) or not worth copying."""
    from datetime import datetime as _dt

    pool = await get_pool()
    since_d = _dt.fromisoformat(since_day).date()
    # v2 (2026-08-24): resolution-settled rows ONLY — a cash-out row's
    # pnl is sale-based and breaks the payout model; rows the model
    # can't reconcile against the venue-true actual are COUNTED as
    # unmodeled instead of silently blended (v1's decomposition mixed
    # both and produced visibly inconsistent columns).
    rows = await pool.fetch(
        """
        SELECT lower(COALESCE(whale_username, '?')) AS whale,
               his_price::float8 AS his, fill_price::float8 AS fp,
               filled_usd::float8 AS stake, pnl::float8 AS pnl,
               reaction_s::float8 AS rs,
               abs(COALESCE(pnl, 0)) > 100 AS over_cap
        FROM live_orders
        WHERE status = 'settled'
          AND filled_usd > 0 AND his_price > 0
          AND placed_at >= $1
        """, since_d)
    out: dict[str, dict] = {}
    for r in rows:
        w = out.setdefault(r["whale"], {
            "n": 0, "voids": 0, "wins": 0, "over_cap_n": 0,
            "unmodeled": 0, "staked": 0.0,
            "actual": 0.0, "actual_capped_out": 0.0,
            "at_his": 0.0, "at_ours_feefree": 0.0,
            "slip_sum": 0.0, "slip_n": 0, "rs": []})
        pnl = float(r["pnl"] or 0)
        stake = float(r["stake"])
        his, fp = float(r["his"]), float(r["fp"] or 0)
        w["n"] += 1
        w["staked"] = round(w["staked"] + stake, 2)
        w["actual"] = round(w["actual"] + pnl, 2)
        if r["over_cap"]:
            w["over_cap_n"] += 1
            w["actual_capped_out"] = round(w["actual_capped_out"] + pnl, 2)
        if r["rs"] is not None:
            w["rs"].append(float(r["rs"]))
        if fp and his:
            w["slip_sum"] += (fp - his)
            w["slip_n"] += 1
        if abs(pnl) < 0.005:
            w["voids"] += 1
            continue
        win = pnl > 0
        if not (0 < his < 1 and 0 < fp < 1):
            w["unmodeled"] += 1
            continue
        # the payout model must explain the venue-true actual for this
        # row; a row it can't explain is disclosed, never blended
        model_row = stake * (1 - fp) / fp if win else -stake
        if abs(model_row - pnl) > max(1.0, 0.15 * stake):
            w["unmodeled"] += 1
            continue
        if win:
            w["wins"] += 1
        w["at_his"] = round(
            w["at_his"] + (stake * (1 - his) / his if win else -stake), 2)
        w["at_ours_feefree"] = round(w["at_ours_feefree"] + model_row, 2)
    whales = []
    for w, d in sorted(out.items(), key=lambda kv: kv[1]["at_his"],
                       reverse=True):
        rs = sorted(d.pop("rs"))
        med = rs[len(rs) // 2] if rs else None
        p90 = rs[int(len(rs) * 0.9)] if rs else None
        whales.append({
            "whale": w, **d,
            "avg_slip": round(d["slip_sum"] / d["slip_n"], 4)
            if d["slip_n"] else None,
            "latency_median_s": round(med, 1) if med is not None else None,
            "latency_p90_s": round(p90, 1) if p90 is not None else None,
            "price_drag": round(d["at_ours_feefree"] - d["at_his"], 2),
        })
    for wrow in whales:
        wrow.pop("slip_sum", None)
        wrow.pop("slip_n", None)
    return {"since": since_day, "whales": whales,
            "note": ("at_his = venue-true outcomes priced at the whale's "
                     "fill (his edge on OUR subset, fee-free); "
                     "at_ours_feefree = same outcomes at our fill price; "
                     "price_drag = what latency cost; fees_other = "
                     "venue-actual minus fee-free at our price. A whale "
                     "is copyable-at-parity iff at_his > 0.")}


@app.get("/api/admin/premap-status", dependencies=[Depends(require_admin)])
async def api_premap_status() -> dict:
    """Pre-map coverage: how much of the venue universe the lookup table
    holds and how fresh it is (owner order 2026-08-24)."""
    pool = await get_pool()
    try:
        row = await pool.fetchrow(
            "SELECT count(*)::int AS rows, "
            "count(DISTINCT event_slug)::int AS events, "
            "max(updated_at) AS fresh FROM us_premap")
    except Exception:  # noqa: BLE001 — table not created yet
        return {"rows": 0, "events": 0, "fresh": None}
    # COVERAGE PROOF (2026-08-24): "9,353 rows" is only good news if the
    # rows are TODAY's markets — the venue's bare listing leads with a
    # stale 2025 catalog, so a full table can still be useless. Count
    # the rows carrying today's/tomorrow's date and sample a few.
    coverage: dict = {}
    try:
        from datetime import date as _date, timedelta as _td

        d0 = _date.today().isoformat()
        d1 = (_date.today() + _td(days=1)).isoformat()
        # asyncpg cannot infer a parameter's type inside a concatenation
        # ('%'||$1||'%' raises "could not determine data type") — the
        # explicit ::text casts are load-bearing. Read today=0 on a
        # 9k-row table on 2026-08-24: that was this, not missing data.
        coverage = dict(await pool.fetchrow(
            "SELECT count(*) FILTER (WHERE identifier LIKE '%'||$1::text||'%')"
            "::int AS today, "
            "count(*) FILTER (WHERE identifier LIKE '%'||$2::text||'%')"
            "::int AS tomorrow FROM us_premap", d0, d1))
        coverage["sample"] = [
            r["identifier"] for r in await pool.fetch(
                "SELECT identifier FROM us_premap "
                "WHERE identifier LIKE '%'||$1::text||'%' "
                "ORDER BY updated_at DESC LIMIT 3", d0)]
    except Exception as exc:  # noqa: BLE001 — never silent (2026-08-24)
        coverage = {"err": f"{type(exc).__name__}: {str(exc)[:160]}"}
    # SLEEVE STATE (owner order 2026-08-24: "only copies flow"). The
    # public heartbeats endpoint sanitizes detail away, so the sleeve's
    # own report is read here — "is it off" must be verifiable, never
    # assumed.
    sleeve: dict = {}
    try:
        hb = await pool.fetchrow(
            "SELECT status, beat_at, detail FROM service_heartbeats "
            "WHERE service='underdog'")
        if hb:
            det = hb["detail"]
            if isinstance(det, str):
                det = json.loads(det)
            sleeve = {"state": (det or {}).get("sleeve", "ON"),
                      "status": hb["status"],
                      "beat_at": hb["beat_at"].isoformat()
                      if hb["beat_at"] else None,
                      "copyexit_open": (det or {}).get("copyexit_open")}
        else:
            sleeve = {"state": "no-heartbeat"}
    except Exception as exc:  # noqa: BLE001 — never silent
        sleeve = {"state": "unreadable",
                  "err": f"{type(exc).__name__}: {str(exc)[:120]}"}
    extras: dict = {"coverage": coverage, "sleeve": sleeve}
    for key, out in (("premap_last", "last_sweep"),
                     ("workers_boot", "workers_boot"),
                     ("side_echo_last", "side_echo"),
                     ("side_echo_shadow", "side_echo_shadow"),
                     ("side_echo_tripped", "side_echo_tripped")):
        try:
            val = await pool.fetchval(
                "SELECT value FROM ingestion_state WHERE key=$1", key)
            extras[out] = json.loads(val) if isinstance(val, str) else val
        except Exception:  # noqa: BLE001
            extras[out] = None
    return {"rows": row["rows"], "events": row["events"],
            "fresh": row["fresh"].isoformat() if row["fresh"] else None,
            **extras}


@app.get("/api/admin/mapping-audit", dependencies=[Depends(require_admin)])
async def api_mapping_audit(days: int = 3, limit: int = 400) -> dict:
    """Side-fidelity evidence, row by row (owner order 2026-08-24: prove
    which mapping path put copies on the wrong side). For every recent
    copy row with a US mapping: the whale's pick (token outcome), the
    US slug we actually ordered (or would have — quarantined rejections
    carry the mapping in their error), and the venue-true P&L. The slug
    tail vs the pick's surname makes side-inversion readable at a
    glance; the postmortem groups by mapping shape."""
    pool = await get_pool()
    days = max(1, min(int(days), 14))
    limit = max(1, min(int(limit), 1000))
    rows = await pool.fetch(
        """
        SELECT lo.placed_at, lower(COALESCE(lo.whale_username,'?')) AS whale,
               COALESCE(mt.outcome, '') AS pick,
               COALESCE(m.title, '') AS gtitle,
               lower(COALESCE(lo.us_market_slug, '')) AS us_slug,
               lo.status, lo.error,
               lo.pnl::float8 AS pnl,
               lo.filled_usd::float8 AS filled_usd
        FROM live_orders lo
        LEFT JOIN market_tokens mt ON mt.token_id = lo.asset
        LEFT JOIN markets m ON m.condition_id = mt.condition_id
        WHERE lo.placed_at > now() - ($1::int * interval '1 day')
          AND (lo.us_market_slug IS NOT NULL
               OR lo.error LIKE 'quarantined%')
        ORDER BY lo.placed_at DESC
        LIMIT $2
        """, days, limit)
    out = []
    for r in rows:
        out.append({
            "at": r["placed_at"].isoformat() if r["placed_at"] else None,
            "whale": r["whale"], "pick": r["pick"][:60],
            "gtitle": r["gtitle"][:80], "us_slug": r["us_slug"][:120],
            "status": r["status"], "pnl": r["pnl"],
            "filled_usd": r["filled_usd"],
            "error": (r["error"] or "")[:160] or None})
    return {"days": days, "count": len(out), "rows": out}


@app.get("/api/admin/rescore-copies", dependencies=[Depends(require_admin)])
async def api_rescore_summary() -> dict:
    """Last venue-truth restatement summary (owner emergency 2026-08-23)."""
    pool = await get_pool()
    val = await pool.fetchval(
        "SELECT value FROM ingestion_state WHERE key=$1", "rescore_copies_v2")
    if val is None:
        return {"ran": False}
    data = json.loads(val) if isinstance(val, str) else val
    return {"ran": True, **(data or {})}


@app.post("/api/admin/rescore-copies", dependencies=[Depends(require_admin)])
async def api_rescore_run(since_day: str = "2026-08-01") -> dict:
    """Re-run the venue-truth restatement of settled copy rows. Idempotent:
    rows already matching the venue verdict are untouched."""
    from ..analytics.engine import _settle_pmus_from_venue

    pool = await get_pool()
    summary = await _settle_pmus_from_venue(pool, rescore_since=since_day)
    summary["at"] = datetime.now(timezone.utc).isoformat()
    await pool.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
        "rescore_copies_v2", json.dumps(summary))
    return summary


@app.get("/api/admin/breakdown-day-detail",
         dependencies=[Depends(require_admin)])
async def api_breakdown_day_detail(day: str) -> dict:
    """Every row behind one ET day of the category breakdown (owner order
    2026-08-23: 'pinpoint what is making up this $668 — every cent').
    The breakdown's Unattributed line is a derived residual (account
    anchor minus attributed categories); this lists BOTH sides row by
    row and diffs them per market, so the residual decomposes into
    named per-trade deltas instead of one opaque number."""
    from datetime import datetime as _dt

    from .track_record import PNL_DISPLAY_CAP, RECORD_TZ, track_record

    pool = await get_pool()
    # AUDIT SINCE, NOT DISPLAY SINCE (2026-08-25). track_record(None)
    # honours DEFAULT_SINCE, which the 2026-08-24 front-end re-baseline
    # moved to that day's epoch. This reconciliation is an ACCOUNTING
    # audit, not a display: the copies side counts every live_order that
    # SETTLED today, including positions entered days earlier, so an
    # anchor windowed to the epoch compares two different populations.
    # It read anchor=1 row against copies=471 rows and reported the 470
    # invisible rows as an $867 residual — an alarm firing on its own
    # window rather than on a real record-vs-venue divergence. The
    # display re-baseline was a front-end decision and the history was
    # kept on purpose; the audit reads all of it.
    rec = await track_record(AUDIT_SINCE)
    arb_rows = await pool.fetch(
        "SELECT DISTINCT outcome_id FROM engine_fills "
        "WHERE band IN ('arb', 'arb_crypto')")
    arb_slugs = {r["outcome_id"] for r in arb_rows}

    def _day_of(ts: float | None) -> str | None:
        if not ts:
            return None
        return max(_dt.fromtimestamp(ts, RECORD_TZ).strftime("%Y-%m-%d"),
                   "2026-08-01")

    # Side A — the ANCHOR: record rows settled this ET day (their sum IS
    # the day's 'account' figure the breakdown reconciles against).
    anchor_rows = []
    for r in rec.get("trades") or []:
        if not r.get("settled"):
            continue
        if _day_of(r.get("settled_ts") or r.get("entry_ts")) != day:
            continue
        slug = (r.get("market_slug") or "").lower()
        anchor_rows.append({
            "slug": slug, "title": r.get("title") or slug,
            "sleeve": r.get("sleeve"),
            "arb": slug in arb_slugs,
            "pnl": round(float(r.get("pnl") or 0), 4),
            "cashed_out": bool(r.get("cashed_out"))})
    anchor_pnl = round(sum(r["pnl"] for r in anchor_rows), 2)

    # Side B — attributed copies: the exact live_orders query the
    # breakdown runs (same whale tuple, same ±cap filter), per row.
    lo_rows = await pool.fetch(
        """
        SELECT lower(COALESCE(lo.whale_username, '?')) AS whale,
               lower(COALESCE(lo.us_market_slug, '')) AS slug,
               COALESCE(m.title, lo.us_market_slug, lo.asset) AS title,
               lo.pnl::float8 AS pnl,
               abs(COALESCE(lo.pnl, 0)) > $2 AS over_cap
        FROM live_orders lo
        LEFT JOIN market_tokens mt ON mt.token_id = lo.asset
        LEFT JOIN markets m ON m.condition_id = mt.condition_id
        WHERE lo.status = 'settled' AND lo.settled_at IS NOT NULL
          AND to_char(lo.settled_at AT TIME ZONE 'America/New_York',
                      'YYYY-MM-DD') = $1
        """, day, PNL_DISPLAY_CAP)
    sleeves = ("rn1", "swisstony", "kch123", "homerunhazard", "manual",
               "underdog", "ferrarichampions2026", "0x076daa87",
               "0x2c335066fe58fe9237c3d3dc7b275c2a034a0563"
               "-1759935795465")
    copies_rows, capped_rows, foreign_rows = [], [], []
    for r in lo_rows:
        row = {"whale": r["whale"], "slug": r["slug"],
               "title": r["title"], "pnl": round(r["pnl"] or 0, 4)}
        if r["whale"] not in sleeves:
            foreign_rows.append(row)
        elif r["over_cap"]:
            capped_rows.append(row)
        else:
            copies_rows.append(row)
    copies_pnl = round(sum(r["pnl"] for r in copies_rows), 2)

    # Arb + external, mirrored from the breakdown for this one day.
    arb_pnl = round(sum(r["pnl"] for r in anchor_rows
                        if r["arb"] and r["sleeve"] != "copy"), 2)
    external_rows = []
    try:
        from .pmus_account import venue_export

        ours = {(t.get("market_slug") or "").lower()
                for t in (rec.get("trades") or [])}
        lo_slugs = await pool.fetch(
            "SELECT DISTINCT lower(us_market_slug) AS s FROM live_orders "
            "WHERE us_market_slug IS NOT NULL")
        ours |= {r["s"] for r in lo_slugs if r["s"]}
        vtask = asyncio.ensure_future(venue_export(day))
        _bg_tasks.add(vtask)
        vtask.add_done_callback(_bg_tasks.discard)
        vexp = await asyncio.wait_for(asyncio.shield(vtask), timeout=25)
        for vr in (vexp.get("rows") or []):
            if vr.get("kind") != "resolution":
                continue
            slug = (vr.get("slug") or "").lower()
            when = vr.get("time") or ""
            if not slug or slug in ours or not when:
                continue
            d = (_dt.fromisoformat(when.replace("Z", "+00:00"))
                 .astimezone(RECORD_TZ).strftime("%Y-%m-%d"))
            if d != day:
                continue
            external_rows.append({
                "slug": slug, "title": vr.get("title") or slug,
                "pnl": round(float(vr.get("realized_pnl") or 0), 2)})
    except Exception:  # noqa: BLE001 — fail open, like the breakdown
        pass
    external_pnl = round(sum(r["pnl"] for r in external_rows), 2)
    residual = round(anchor_pnl - copies_pnl - arb_pnl - external_pnl, 2)

    # The decomposition: per market, what the anchor recorded vs what
    # the copies audit recorded. Deltas are the residual, named.
    by_slug: dict[str, dict] = {}
    for r in anchor_rows:
        if r["sleeve"] != "copy" or r["arb"]:
            continue
        s = by_slug.setdefault(r["slug"], {"slug": r["slug"],
                                           "title": r["title"],
                                           "anchor": 0.0, "copies": 0.0})
        s["anchor"] = round(s["anchor"] + r["pnl"], 4)
    for r in copies_rows:
        s = by_slug.setdefault(r["slug"], {"slug": r["slug"],
                                           "title": r["title"],
                                           "anchor": 0.0, "copies": 0.0})
        s["copies"] = round(s["copies"] + r["pnl"], 4)
    diffs = []
    for s in by_slug.values():
        s["delta"] = round(s["anchor"] - s["copies"], 4)
        if abs(s["delta"]) >= 0.005:
            diffs.append(s)
    diffs.sort(key=lambda s: -abs(s["delta"]))
    noncopy = [r for r in anchor_rows
               if r["sleeve"] != "copy" and not r["arb"]]
    return {
        "day": day,
        "anchor": {"pnl": anchor_pnl, "rows": len(anchor_rows)},
        "copies": {"pnl": copies_pnl, "rows": len(copies_rows)},
        "arb_pnl": arb_pnl, "external_pnl": external_pnl,
        "residual": residual,
        "residual_from_copy_deltas": round(sum(s["delta"] for s in diffs), 2),
        "residual_from_noncopy_rows": round(sum(r["pnl"] for r in noncopy), 2),
        "copy_deltas": diffs,
        "noncopy_anchor_rows": noncopy,
        "capped_copy_rows": capped_rows,
        "foreign_whale_rows": foreign_rows,
        "external_rows": external_rows,
        "anchor_rows": anchor_rows,
        "copies_rows": copies_rows,
        "note": ("residual == anchor - copies - arb - external, the same "
                 "identity the breakdown derives per day. copy_deltas are "
                 "per-market gaps between the venue-anchored record and "
                 "the copies audit table (fees, partial fills, cash-out "
                 "proceeds, settle-day boundaries); noncopy_anchor_rows "
                 "land wholly in the residual. capped_copy_rows are "
                 "excluded from BOTH sides by the ±$100 display rule and "
                 "shown here for full disclosure.")}


@app.get("/api/today-live")
async def api_today_live() -> dict:
    """Second-latency settlement feed from OUR OWN ledger (owner report
    2026-08-07: 'won 4 trades, page didn't move'). The venue-account
    snapshot lags minutes by design; the copy sleeves settle in
    live_orders the moment our resolution pipeline marks them — this
    endpoint powers the hero LIVE strip and the win toasts.

    COPY-WHALES ONLY, UNCAPPED (owner order 2026-08-22): the strip is
    the copy record's live edge — cash-outs count (they are settled by
    OUR sale), and the $100 display cap is gone here so the strip
    matches the uncapped copies record it fronts."""
    from .copies_record import COPY_WHALES

    # Mid-boot resilience: this endpoint is polled every 12s by every
    # open page — during a redeploy the pool may not be ready, and a
    # 500 here paints an error where a quiet empty strip belongs.
    try:
        pool = await get_pool()
    except Exception:  # noqa: BLE001
        return {"pnl": 0.0, "settled": 0, "wins": 0, "recent": [],
                "warming": True}
    # Sargable ET-day predicate (audit 2026-08-21): the to_char()-equals
    # form is a function of the column — unindexable, so every 12s poll
    # scanned all settled rows. A half-open range on settled_at uses the
    # migration-024 partial index; identical ET-midnight semantics.
    day = await pool.fetchrow(
        """
        SELECT COALESCE(sum(pnl), 0)::float8 AS pnl,
               count(*)::int AS settled,
               count(*) FILTER (WHERE pnl > 0)::int AS wins
        FROM live_orders
        WHERE status IN ('settled', 'cashed_out')
          AND settled_at IS NOT NULL
          AND settled_at >= date_trunc('day',
                now() AT TIME ZONE 'America/New_York')
              AT TIME ZONE 'America/New_York'
          AND lower(COALESCE(whale_username, '')) = ANY($1::text[])
        """, list(COPY_WHALES))
    recent = await pool.fetch(
        """
        SELECT lo.pnl::float8 AS pnl, lo.settled_at, lo.whale_username,
               m.title, mt.outcome
        FROM live_orders lo
        LEFT JOIN market_tokens mt ON mt.token_id = lo.asset
        LEFT JOIN markets m ON m.condition_id = mt.condition_id
        WHERE lo.status IN ('settled', 'cashed_out')
          AND lo.settled_at IS NOT NULL
          AND lower(COALESCE(lo.whale_username, '')) = ANY($1::text[])
        ORDER BY lo.settled_at DESC
        LIMIT 8
        """, list(COPY_WHALES))
    return {
        "pnl": round(float(day["pnl"]), 2),
        "settled": day["settled"],
        "wins": day["wins"],
        "recent": [{
            "title": r["title"] or "position",
            "outcome": r["outcome"],
            "whale": r["whale_username"],
            "pnl": round(float(r["pnl"] or 0), 2),
            "at": r["settled_at"].isoformat() if r["settled_at"] else None,
        } for r in recent],
        "scope": ("copy whales only, uncapped, cash-outs included "
                  "(order-level, our ledger)"),
    }


@app.get("/api/report/range")
async def api_report_range(
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
) -> dict:
    """Category P&L over any date range (owner directive 2026-08-07:
    daily / weekly / monthly / custom downloadable reports)."""
    return await _category_breakdown(
        _parse_day(from_, "2026-08-01"), _parse_day(to, _today_et()))


_WHALE_0X2C33 = "0x2c335066fe58fe9237c3d3dc7b275c2a034a0563-1759935795465"
_CAT_ORDER = ["rn1", "swisstony", "kch123", "homerunhazard",
              _WHALE_0X2C33,
              # Dossier promotions (owner order 2026-08-21). Bug fix
              # 2026-08-22: absent from this list, their settled rows
              # were built by _category_breakdown but silently DROPPED
              # from report.csv/pdf — the exports skip categories the
              # label map doesn't know.
              "ferrarichampions2026", "0x076daa87",
              "manual", "underdog", "arb", "external", "software"]
_CAT_LABEL = {"rn1": "RN1 copies", "swisstony": "SwissTony copies",
              "kch123": "kch123 copies", "homerunhazard": "HomeRunHazard copies",
              # Display label is the truncated address — the owner names
              # whales; until he does, the wallet is its own name.
              _WHALE_0X2C33: "0x2c33…0563 copies",
              "ferrarichampions2026": "ferrariChampions2026 copies",
              "0x076daa87": "0x076daa87 copies",
              "manual": "Manual desk", "underdog": "Underdog $1 test",
              "arb": "Arbitrage", "external": "External (owner)",
              # Renamed 2026-08-22 (owner scare x2): the derived remainder
              # is NOT software trading — software is OFF with a ~$5 tail.
              # It holds fees, open-stake marks, and >$100-cap trades the
              # display cap excludes from the sleeve rows.
              "software": "Unattributed (fees · marks · capped trades)"}


@app.get("/api/report.csv")
async def api_report_csv(
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
):
    import csv
    import io as _io

    data = await _category_breakdown(
        _parse_day(from_, "2026-08-01"), _parse_day(to, _today_et()))
    buf = _io.StringIO()
    w = csv.writer(buf)
    w.writerow(["date", "category", "pnl", "settled", "wins", "losses"])
    for d in data["days"]:
        for cat in _CAT_ORDER:
            c = d.get(cat)
            if not c:
                continue
            w.writerow([d["date"], _CAT_LABEL[cat], f"{c['pnl']:.2f}",
                        c["settled"], c["wins"], c["losses"]])
    for cat in _CAT_ORDER:
        t = data["totals"].get(cat)
        if not t:
            continue
        w.writerow(["TOTAL", _CAT_LABEL[cat], f"{t['pnl']:.2f}",
                    t["settled"], t["wins"], t["losses"]])
    w.writerow(["TOTAL", "ALL", f"{data['net_pnl']:.2f}", "", "", ""])
    name = f"bettoredge_pnl_{data['from']}_{data['to']}.csv"
    return PlainTextResponse(
        buf.getvalue(), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/report.pdf")
async def api_report_pdf(
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
):
    from fastapi.responses import Response

    from .reports import build_category_report

    data = await _category_breakdown(
        _parse_day(from_, "2026-08-01"), _parse_day(to, _today_et()))
    pdf, name = build_category_report(data)
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition":
                             f'attachment; filename="{name}"'})


@app.get("/api/report")
async def api_report(period: str = Query("weekly"),
                     format: str = Query("md")) -> Any:
    """Downloadable account report: daily/weekly/monthly x md/csv/json.
    Derived from the same builder the site renders — a report can never
    disagree with the page. Filters are stated in the report header."""
    from .report import build_report

    content, data = await build_report(period, format)
    if content is None:
        raise HTTPException(status_code=503,
                            detail=data.get("error") or "not configured")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if format == "csv":
        return PlainTextResponse(content, media_type="text/csv", headers={
            "Content-Disposition":
                f'attachment; filename="bettoredge-{period}-{stamp}.csv"'})
    if format == "md":
        return PlainTextResponse(content, media_type="text/markdown", headers={
            "Content-Disposition":
                f'inline; filename="bettoredge-{period}-{stamp}.md"'})
    return content


@app.get("/api/tennis-week")
async def api_tennis_week(days: str | None = Query(None)) -> dict:
    """Venue-ledger tennis P&L for the given slug-dates (default: this
    ET week, Monday onward). Owner question 2026-08-14 — every tennis
    play, manual and AI, priced by the venue's own realized figures."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from .pmus_account import tennis_week_report

    if days:
        want = [d.strip() for d in days.split(",") if d.strip()]
    else:
        today = datetime.now(tz=ZoneInfo("America/New_York")).date()
        monday = today - timedelta(days=today.weekday())
        want = [(monday + timedelta(days=i)).isoformat()
                for i in range((today - monday).days + 1)]
    return await tennis_week_report(want)


@app.get("/api/venue-export")
async def api_venue_export(since: str | None = Query(None)) -> dict:
    """RAW Polymarket activities ledger since a date (default Monday of
    the current ET week): every trade and every resolution row, all
    sports, verbatim venue fields. Owner order 2026-08-14: every trade
    on the account, perfect — so no interpretation happens here at all."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from .pmus_account import venue_export

    if not since:
        today = datetime.now(tz=ZoneInfo("America/New_York")).date()
        since = (today - timedelta(days=today.weekday())).isoformat()
    return await venue_export(since)


@app.get("/api/venue-export-raw")
async def api_venue_export_raw(since: str | None = Query(None)) -> dict:
    """Verbatim venue activities (no flattening) — the weekly report's
    cash-truth source after the 2026-08-17 reconciliation found the
    flat export drops the trade side and reduces resolution position
    objects to two numbers. Public on the same precedent as
    /api/venue-export (owner order 2026-08-14: every trade on the
    account, served plainly); carries no credentials or keys."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from .pmus_account import venue_export_raw

    if not since:
        today = datetime.now(tz=ZoneInfo("America/New_York")).date()
        since = (today - timedelta(days=today.weekday())).isoformat()
    return await venue_export_raw(since)


@app.get("/api/copies-record")
async def api_copies_record(since: str | None = Query(None)) -> dict:
    """The COPIES cohort, uncapped, from the order-level audit table —
    the record the copy-trading thesis stands on (owner order
    2026-08-20: show that the system is profitable). Public: these are
    our own settled orders, venue-backed, no credentials involved."""
    from .copies_record import build as build_copies

    return await build_copies(_parse_day(since, "2026-08-01"))


@app.get("/api/venue-truth")
async def api_venue_truth() -> dict:
    """Venue-truth P&L (task #74): the record rebuilt continuously from
    the venues' own ledgers — PM afterPosition.realized per resolution,
    Kalshi signed cash over raw fills+settlements with exact fees — so
    the site's numbers reconcile to the accounts, uncapped. Rolling
    window (Kalshi raw export carries 15 days); served stale-while-
    refreshing so the homepage never waits on the venue crawl."""
    from .venue_truth import snapshot

    return await snapshot()


@app.get("/api/pmus-account")
async def api_pmus_account() -> dict:
    """The REAL Polymarket US account, live from the venue's portfolio API:
    value, cash, open positions, realized PnL, recent trades. 30s cache."""
    from .pmus_account import account_snapshot

    return await account_snapshot()


@app.get("/api/ai-trader")
async def ai_trader_report(days: int = Query(7, le=90)) -> dict:
    """AI TRADER paper account: live P&L of copying the source whale at the
    configured ratio, filled from real residual books, settled by our own
    resolution pipeline. counterfactual = same clips at HIS prices — the
    delta is the measured profitability impact of his own market impact."""
    pool = await get_pool()
    cfg = settings()
    summary = await pool.fetchrow(
        """
        SELECT count(*)::int AS copies,
               count(*) FILTER (WHERE status = 'missed')::int AS missed,
               count(*) FILTER (WHERE status = 'open')::int AS open,
               count(*) FILTER (WHERE status = 'settled')::int AS settled,
               COALESCE(sum(filled_notional), 0)::float8 AS staked,
               COALESCE(sum(filled_notional) FILTER (WHERE status = 'open'), 0)::float8
                   AS open_exposure,
               COALESCE(sum(pnl) FILTER (WHERE status = 'settled'), 0)::float8 AS realized_pnl,
               COALESCE(sum(filled_notional) FILTER (WHERE status = 'settled'), 0)::float8
                   AS settled_staked,
               COALESCE(sum(counterfactual_pnl) FILTER (WHERE status = 'settled'), 0)::float8
                   AS counterfactual_pnl,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY reaction_s) AS reaction_p50,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY slippage_cents)
                   FILTER (WHERE fill_vwap IS NOT NULL) AS slippage_p50,
               min(placed_at) AS first_trade
        FROM ai_trades WHERE placed_at > now() - make_interval(days => $1)
        """,
        days,
    )
    daily = await pool.fetch(
        """
        SELECT settled_at::date AS date, sum(pnl)::float8 AS pnl,
               sum(counterfactual_pnl)::float8 AS counterfactual,
               count(*)::int AS trades, COALESCE(sum(filled_notional), 0)::float8 AS volume
        FROM ai_trades
        WHERE status = 'settled' AND settled_at > now() - make_interval(days => $1)
        GROUP BY 1 ORDER BY 1
        """,
        days,
    )
    recent = await pool.fetch(
        """
        SELECT a.id, a.placed_at, a.reaction_s::float8 AS reaction_s, a.status,
               a.his_price::float8 AS his_price, a.fill_vwap::float8 AS fill_vwap,
               a.slippage_cents::float8 AS slippage_cents,
               a.clip_target::float8 AS clip_target,
               a.filled_notional::float8 AS filled_notional,
               a.pnl::float8 AS pnl, a.counterfactual_pnl::float8 AS counterfactual_pnl,
               a.payout::float8 AS payout,
               COALESCE(m.event_title, m.title, t.market_title) AS market_title,
               COALESCE(mt.outcome, t.outcome) AS outcome, m.sport
        FROM ai_trades a
        LEFT JOIN trades t ON t.id = a.trade_id
        LEFT JOIN market_tokens mt ON mt.token_id = a.asset
        LEFT JOIN markets m ON m.condition_id = COALESCE(mt.condition_id, a.condition_id)
        ORDER BY a.placed_at DESC LIMIT 50
        """
    )
    d = dict(summary)
    d["roi"] = d["realized_pnl"] / d["settled_staked"] if d["settled_staked"] else None
    d["slippage_cost"] = round(d["counterfactual_pnl"] - d["realized_pnl"], 2)
    for k in ("reaction_p50", "slippage_p50"):
        if d.get(k) is not None:
            d[k] = round(float(d[k]), 3)
    return {
        "source": cfg.ai_trader_source,
        "ratio": cfg.ai_trader_ratio,
        "days": days,
        "summary": d,
        "daily": [{"date": r["date"].isoformat(), "pnl": round(r["pnl"] or 0, 2),
                   "volume": round(r["volume"] or 0, 2), "trades": r["trades"],
                   "counterfactual": round(r["counterfactual"] or 0, 2)} for r in daily],
        "recent": [dict(r) for r in recent],
    }


@app.get("/api/copy-report")
async def copy_report(whale: str | None = "swisstony", hours: int = Query(24, le=24 * 30)) -> dict:
    """Copy-trade feasibility: measured residual books at our real reaction
    time, for every fresh whale BUY. Answers: does the edge survive copying?"""
    pool = await get_pool()
    where_user = "AND lower(username) = lower($2)" if whale else ""
    args: list = [hours] + ([whale] if whale else [])
    agg = await pool.fetchrow(
        f"""
        SELECT count(*)::int AS probes,
               count(*) FILTER (WHERE book_ok)::int AS with_book,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY reaction_s) AS reaction_p50,
               percentile_cont(0.95) WITHIN GROUP (ORDER BY reaction_s) AS reaction_p95,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY slippage_cents)
                   FILTER (WHERE book_ok) AS slippage_p50,
               percentile_cont(0.9) WITHIN GROUP (ORDER BY slippage_cents)
                   FILTER (WHERE book_ok) AS slippage_p90,
               count(*) FILTER (WHERE fillable_1k)::int AS fillable_1k,
               count(*) FILTER (WHERE fillable_5k)::int AS fillable_5k,
               avg(residual_roi_1k) FILTER (WHERE fillable_1k) AS avg_roi_1k,
               avg(residual_roi_5k) FILTER (WHERE fillable_5k) AS avg_roi_5k,
               count(*) FILTER (WHERE residual_roi_1k > 0)::int AS positive_1k,
               count(*) FILTER (WHERE residual_roi_5k > 0)::int AS positive_5k
        FROM copy_probes
        WHERE probe_at > now() - make_interval(hours => $1) {where_user}
        """,
        *args,
    )
    # Per-whale vetting census (owner directive 2026-08-06): the same
    # residual-edge measurement for EVERY probed whale, so copy-source
    # candidates are graded on the identical yardstick as the incumbents.
    by_whale = await pool.fetch(
        """
        SELECT lower(COALESCE(username, '?')) AS whale,
               count(*)::int AS probes,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY reaction_s)
                   AS reaction_p50,
               avg(residual_roi_1k) FILTER (WHERE fillable_1k) AS avg_roi_1k,
               count(*) FILTER (WHERE residual_roi_1k > 0)::int AS positive_1k,
               count(*) FILTER (WHERE fillable_1k)::int AS fillable_1k,
               avg(his_notional) AS avg_his_notional
        FROM copy_probes
        WHERE probe_at > now() - make_interval(hours => $1)
        GROUP BY 1 ORDER BY probes DESC
        """,
        hours,
    )
    recent = await pool.fetch(
        f"""
        SELECT cp.probe_at, cp.reaction_s::float8 AS reaction_s,
               cp.his_price::float8 AS his_price, cp.best_ask::float8 AS best_ask,
               cp.slippage_cents::float8 AS slippage_cents,
               cp.his_notional::float8 AS his_notional,
               cp.fillable_5k, cp.residual_roi_1k::float8 AS residual_roi_1k,
               cp.residual_roi_5k::float8 AS residual_roi_5k, cp.book_ok, cp.error,
               COALESCE(m.event_title, m.title, t.market_title) AS market_title,
               COALESCE(mt.outcome, t.outcome) AS outcome
        FROM copy_probes cp
        LEFT JOIN trades t ON t.id = cp.trade_id
        LEFT JOIN market_tokens mt ON mt.token_id = cp.asset
        LEFT JOIN markets m ON m.condition_id = COALESCE(mt.condition_id, t.condition_id)
        WHERE cp.probe_at > now() - make_interval(hours => $1) {where_user}
        ORDER BY cp.probe_at DESC LIMIT 15
        """,
        *args,
    )
    d = dict(agg)
    for k in ("reaction_p50", "reaction_p95", "slippage_p50", "slippage_p90",
              "avg_roi_1k", "avg_roi_5k"):
        if d.get(k) is not None:
            d[k] = round(float(d[k]), 4)
    d["assumed_edge"] = settings().copy_probe_assumed_edge
    bw = []
    for r in by_whale:
        row = dict(r)
        for k in ("reaction_p50", "avg_roi_1k", "avg_his_notional"):
            if row.get(k) is not None:
                row[k] = round(float(row[k]), 4)
        bw.append(row)
    return {"whale": whale, "hours": hours, "summary": d,
            "by_whale": bw,
            "recent": [dict(r) for r in recent]}


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

    # Polymarket US venue (regulated exchange behind the mobile app): gateway
    # reachability, credential validity, and slug-mapping spot check against a
    # recent open market from our own metadata.
    import asyncio as _asyncio

    from .. import pmus

    try:
        out["pmus"] = await _asyncio.wait_for(_asyncio.to_thread(pmus.probe), timeout=15)
        sample = await pool.fetchrow(
            """
            SELECT m.slug, m.event_slug, m.title, m.event_title, mt.outcome
            FROM markets m JOIN market_tokens mt ON mt.condition_id = m.condition_id
            WHERE NOT m.closed AND m.slug IS NOT NULL AND m.sport <> 'unclassified'
            ORDER BY m.updated_at DESC LIMIT 1
            """
        )
        if sample and out["pmus"].get("gateway_ok"):
            mapping = await _asyncio.wait_for(
                _asyncio.to_thread(
                    pmus.resolve_market, sample["slug"], sample["event_slug"],
                    sample["title"], sample["event_title"], sample["outcome"],
                ),
                timeout=15,
            )
            out["pmus"]["mapping_check"] = {
                "global": {"slug": sample["slug"], "outcome": sample["outcome"]},
                "mapped": mapping,
            }
    except Exception as exc:  # noqa: BLE001
        out["pmus"] = {"status": "error", "body": str(exc)[:220]}
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
async def api_events(limit: int = Query(50, ge=1, le=200)) -> list[dict]:
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
    elif body.username:
        # 2026-08-24: rn1 and swisstony silently dropped off the active
        # roster (auto-deactivation) and the only lookups here were
        # id/address, neither of which the probe knows — reactivation
        # by username closes that gap.
        row = await pool.fetchrow(
            "SELECT * FROM whales WHERE lower(username)=lower($1) "
            "ORDER BY active DESC, id LIMIT 1", body.username)
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
