"""Mark every whale buy at the prices that decompose his edge.

Owner question 2026-09-01 (evening), see analytics/decompose.py. For
each rostered whale's BUY inside the window, read the token's public
CLOB price history once per token and record the price 5, 10 and 60
minutes after his fill and the last price before the game started.
Written to trade_marks; the analysis is then a query.

MEASUREMENT ONLY. Reads trades/markets, writes trade_marks and
market_starts, calls the public CLOB (no auth, a different host from the
US venue the copy path trades on). Never places, cancels or touches an
order. Paced; a run of failures backs off; never dies.

A MARK IS A READING AT t, NOT THE FIRST READING AFTER t. The series has
a fidelity (minutes between points); a mark is the first point at or
after the target inside two fidelities plus a minute, else NULL. NULL is
dropped from that leg by the analysis, never zero-filled.

A BUY IS MARKED ONLY WHEN ITS MARKS CAN EXIST (round four, 2026-09-01).
The first version took the NEWEST buys first and wrote them within a
minute of the fill: no +5m/+10m/+60m price yet, no pre-game price
because the game had not started, four NULLs, never revisited. So a buy
is picked only once it is older than the last offset plus the coarsest
tolerance, a token whose game has not started yet is deferred until it
has, and a venue miss is RETRIED later rather than written as a
permanent NULL row.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone

from ..analytics.decompose import WHALES
from ..db import get_pool

log = logging.getLogger(__name__)

EDGE_MARKS = os.environ.get("EDGE_MARKS", "on").lower() in ("1", "on", "true", "yes")
EVERY_S = float(os.environ.get("EDGE_MARKS_EVERY_S", "20"))
WINDOW_DAYS = int(os.environ.get("EDGE_MARKS_WINDOW_DAYS", "30"))
TOKENS_PER_TICK = 8
PACING_S = 0.4
BACKOFF_S = 90.0
PRE_GAME_LEAD_S = 60.0
OFFSETS = {"p_5m": 300.0, "p_10m": 600.0, "p_60m": 3600.0}
MAX_FIDELITY_M = 15
# The youngest buy whose last mark can exist: the last offset plus the
# coarsest series' tolerance.
MIN_AGE_S = max(OFFSETS.values()) + 2 * MAX_FIDELITY_M * 60.0 + 60.0
# A token the venue could not serve (no series, metadata fetch failed,
# game not started) is retried after this, not written as NULLs.
RETRY_S = 6 * 3600.0
START_RETRY_S = 3600.0
_backoff_until = 0.0
_skip_until: dict[str, float] = {}
_sleep = asyncio.sleep


def fidelity_for(span_s: float) -> int:
    """Minutes between series points for a span: fine when short."""
    if span_s <= 6 * 3600:
        return 1
    if span_s <= 3 * 86400:
        return 5
    return MAX_FIDELITY_M


def mark_at(series: list[tuple[float, float]], target_ts: float,
            fidelity_m: int) -> float | None:
    """First point at or after target inside the tolerance, else None."""
    tol = 2 * fidelity_m * 60.0 + 60.0
    for t, p in series:
        if t >= target_ts:
            return p if t - target_ts <= tol and 0.0 < p < 1.0 else None
    return None


def mark_before(series: list[tuple[float, float]], target_ts: float,
                fidelity_m: int) -> float | None:
    """Last point at or before target inside the tolerance, else None."""
    tol = 2 * fidelity_m * 60.0 + 60.0
    best = None
    for t, p in series:
        if t > target_ts:
            break
        best = (t, p)
    if best is None or target_ts - best[0] > tol or not (0.0 < best[1] < 1.0):
        return None
    return best[1]


def marks_for(trade_ts: float, series: list[tuple[float, float]],
              game_start_ts: float | None, fidelity_m: int) -> dict:
    out = {k: mark_at(series, trade_ts + off, fidelity_m)
           for k, off in OFFSETS.items()}
    pre = None
    if game_start_ts is not None and trade_ts < game_start_ts - PRE_GAME_LEAD_S:
        pre = mark_before(series, game_start_ts - PRE_GAME_LEAD_S, fidelity_m)
    out["p_pre"] = pre
    return out


# ------------------------------------------------------------ the venue

def _parse_ts(val) -> float | None:
    if not val or not isinstance(val, str):
        return None
    try:
        d = datetime.fromisoformat(val.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.timestamp()
    except ValueError:
        return None


def _interval_for(span_s: float) -> str:
    """The venue's named lookback that covers a span ending now. NOTE
    the venue's '1m' is one MONTH."""
    h = span_s / 3600.0
    if h <= 1:
        return "1h"
    if h <= 6:
        return "6h"
    if h <= 24:
        return "1d"
    if h <= 168:
        return "1w"
    return "1m"


def _points(raw: dict) -> list[tuple[float, float]]:
    pts = []
    for row in (raw or {}).get("history") or []:
        try:
            t, p = float(row["t"]), float(row["p"])
        except (KeyError, TypeError, ValueError):
            continue
        pts.append((t, p))
    pts.sort()
    return pts


def fetch_history(token_id: str, start_ts: float, end_ts: float,
                  fidelity_m: int) -> list[tuple[float, float]]:
    """Sync (run in a thread): the public CLOB series for one token
    inside [start_ts, end_ts].

    Asks with startTs/endTs first. If the venue answers with points that
    all lie OUTSIDE the window (it ignored the bounds) or with nothing,
    asks again with the named interval that covers the span and windows
    the answer client-side -- the form desk_history already uses.
    """
    import httpx

    from ..config import settings

    lo, hi = start_ts - 60.0, end_ts + 60.0
    with httpx.Client(base_url=settings().clob_api_base, timeout=10) as c:
        resp = c.get("/prices-history", params={
            "market": token_id, "startTs": int(start_ts),
            "endTs": int(end_ts), "fidelity": int(fidelity_m)})
        resp.raise_for_status()
        pts = _points(resp.json() or {})
        inside = [x for x in pts if lo <= x[0] <= hi]
        if inside:
            return inside
        resp = c.get("/prices-history", params={
            "market": token_id,
            "interval": _interval_for(time.time() - start_ts),
            "fidelity": int(fidelity_m)})
        resp.raise_for_status()
        pts = _points(resp.json() or {})
    return [x for x in pts if lo <= x[0] <= hi]


def fetch_game_start(condition_id: str) -> float | None:
    import httpx

    from ..config import settings

    with httpx.Client(base_url=settings().clob_api_base, timeout=10) as c:
        resp = c.get(f"/markets/{condition_id}")
        resp.raise_for_status()
        raw = resp.json() or {}
    return _parse_ts(raw.get("game_start_time"))


# --------------------------------------------------------------- the job

async def _pick_tokens(pool, whales: list[str], limit: int,
                       min_age_s: float) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT t.asset, t.condition_id,
               extract(epoch FROM min(t.ts))::float8 AS first_ts,
               extract(epoch FROM max(t.ts))::float8 AS last_ts,
               count(*)::int AS n
          FROM trades t
          JOIN whales w ON w.id = t.whale_id
          LEFT JOIN trade_marks tm ON tm.trade_id = t.id
         WHERE lower(w.username) = ANY($1::text[])
           AND t.side = 'BUY'
           AND t.condition_id IS NOT NULL
           AND t.ts >= now() - make_interval(days => $2)
           AND t.ts <= now() - make_interval(secs => $4)
           AND tm.trade_id IS NULL
         GROUP BY t.asset, t.condition_id
         ORDER BY max(t.ts) DESC
         LIMIT $3
        """, whales, int(WINDOW_DAYS), int(limit), float(min_age_s))
    return [dict(r) for r in rows]


async def _game_start(pool, condition_id: str,
                      now_ts: float) -> tuple[float | None, bool]:
    """(game_start_ts, known). known=False means the venue could not be
    read (the token is deferred); known=True with None means the market
    carries no game start (no pre-game leg)."""
    row = None
    try:
        row = await pool.fetchrow(
            "SELECT game_start, err, extract(epoch FROM fetched_at)::float8 "
            "AS fetched_ts FROM market_starts WHERE condition_id = $1",
            condition_id)
    except Exception:  # noqa: BLE001 — table absent until 044
        row = None
    if row is not None:
        gs = row["game_start"]
        if row["err"] is None:
            return (gs.timestamp() if gs is not None else None), True
        if now_ts - float(row["fetched_ts"] or 0.0) < START_RETRY_S:
            return None, False           # failed recently: still deferred
    err = None
    try:
        ts = await asyncio.to_thread(fetch_game_start, condition_id)
    except Exception as exc:  # noqa: BLE001
        ts, err = None, f"{type(exc).__name__}"
    try:
        await pool.execute(
            "INSERT INTO market_starts (condition_id, game_start, err) "
            "VALUES ($1, to_timestamp($2), $3) ON CONFLICT (condition_id) "
            "DO UPDATE SET game_start = EXCLUDED.game_start, err = EXCLUDED.err, "
            "fetched_at = now()", condition_id, ts, err)
    except Exception:  # noqa: BLE001
        pass
    return ts, err is None


async def mark_token(pool, tok: dict, whales: list[str],
                     now_ts: float) -> int:
    """Read one token's series and mark every unmarked buy on it.
    Returns the number written; -1 when the token is deferred."""
    asset = str(tok["asset"])
    gs, known = await _game_start(pool, str(tok["condition_id"]), now_ts)
    if not known:
        _skip_until[asset] = now_ts + START_RETRY_S
        return -1
    if gs is not None and now_ts < gs + PRE_GAME_LEAD_S + 60.0:
        # THE GAME HAS NOT STARTED: p_pre cannot exist yet. Defer the
        # whole token rather than write a row the analysis would read
        # as "no pre-game mark".
        _skip_until[asset] = gs + PRE_GAME_LEAD_S + 60.0
        return -1
    first, last = float(tok["first_ts"]), float(tok["last_ts"])
    end = min(now_ts, max(last + MIN_AGE_S, (gs or 0.0)))
    start = first - 120.0
    fid = fidelity_for(end - start)
    series = await asyncio.to_thread(fetch_history, asset, start, end, fid)
    if not series:
        _skip_until[asset] = now_ts + RETRY_S
        log.info("edge_marks: no series for %s — retry in %ss",
                 asset[:16], RETRY_S)
        return -1
    trades = await pool.fetch(
        """
        SELECT t.id, extract(epoch FROM t.ts)::float8 AS ts
          FROM trades t
          JOIN whales w ON w.id = t.whale_id
          LEFT JOIN trade_marks tm ON tm.trade_id = t.id
         WHERE t.asset = $1 AND t.side = 'BUY'
           AND lower(w.username) = ANY($2::text[])
           AND t.ts >= now() - make_interval(days => $3)
           AND t.ts <= now() - make_interval(secs => $4)
           AND tm.trade_id IS NULL
        """, asset, whales, int(WINDOW_DAYS), float(MIN_AGE_S))
    n = 0
    for tr in trades:
        m = marks_for(float(tr["ts"]), series, gs, fid)
        await pool.execute(
            "INSERT INTO trade_marks (trade_id, p_5m, p_10m, p_60m, p_pre, "
            "game_start, fidelity_m, err) VALUES ($1,$2,$3,$4,$5,"
            "to_timestamp($6),$7,$8) ON CONFLICT (trade_id) DO NOTHING",
            int(tr["id"]), m["p_5m"], m["p_10m"], m["p_60m"], m["p_pre"],
            gs, fid, None)
        n += 1
    return n


async def run_once(pool, whales: list[str] | None = None,
                   now_ts: float | None = None) -> int:
    global _backoff_until
    now_ts = time.time() if now_ts is None else now_ts
    if now_ts < _backoff_until:
        return 0
    whales = [w.lower() for w in (whales or WHALES)]
    try:
        # Deferred tokens are skipped in Python, so the pick over-fetches
        # a little to keep the tick full.
        toks = await _pick_tokens(pool, whales, TOKENS_PER_TICK * 3, MIN_AGE_S)
    except Exception as exc:  # noqa: BLE001 — table absent until 044
        log.debug("edge_marks: pick failed (%s)", type(exc).__name__)
        return 0
    toks = [t for t in toks
            if _skip_until.get(str(t["asset"]), 0.0) <= now_ts][:TOKENS_PER_TICK]
    n = 0
    for i, tok in enumerate(toks):
        if i:
            await _sleep(PACING_S)
        try:
            got = await mark_token(pool, tok, whales, now_ts)
            n += max(0, got)
        except Exception as exc:  # noqa: BLE001 — venue or db: back off
            _backoff_until = now_ts + BACKOFF_S
            log.warning("edge_marks: %s on %s — backing off %ss",
                        type(exc).__name__, str(tok.get("asset"))[:16],
                        BACKOFF_S)
            break
    if n:
        log.info("edge_marks: marked %d buys on %d tokens", n, len(toks))
    return n


async def probe(pool) -> dict:
    """Does the venue honour startTs/endTs? One read on the newest
    marked-or-markable rn1 token, reported for the diagnostic."""
    try:
        row = await pool.fetchrow(
            """
            SELECT t.asset, extract(epoch FROM t.ts)::float8 AS ts
              FROM trades t JOIN whales w ON w.id = t.whale_id
             WHERE lower(w.username) = $1 AND t.side = 'BUY'
               AND t.ts <= now() - make_interval(secs => $2)
             ORDER BY t.ts DESC LIMIT 1
            """, WHALES[0], float(MIN_AGE_S))
    except Exception as exc:  # noqa: BLE001
        return {"error": f"ledger: {type(exc).__name__}"}
    if row is None:
        return {"error": "no buy old enough to mark"}
    start, end = float(row["ts"]) - 120.0, float(row["ts"]) + MIN_AGE_S
    try:
        pts = await asyncio.to_thread(fetch_history, str(row["asset"]),
                                      start, end, 1)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"venue: {type(exc).__name__}: {str(exc)[:120]}"}
    return {"asset": str(row["asset"])[:20], "points_in_window": len(pts),
            "span_s": int(end - start),
            "honoured": bool(pts),
            "skipped_tokens": len(_skip_until), "backoff_until": _backoff_until}


async def main() -> None:
    if not EDGE_MARKS:
        log.info("edge_marks disabled (EDGE_MARKS=off)")
        return
    pool = await get_pool()
    log.info("edge_marks up: whales=%s window=%sd every=%ss min_age=%ss",
             WHALES, WINDOW_DAYS, EVERY_S, MIN_AGE_S)
    while True:
        try:
            await run_once(pool)
        except Exception:  # noqa: BLE001 — a measurement worker never dies
            log.exception("edge_marks pass failed")
        await asyncio.sleep(EVERY_S)
