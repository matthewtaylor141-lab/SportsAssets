"""PAPER TRADING READ MODELS: /api/command/paper/* (read-only, COMMAND auth).

Every figure is LIVE MARKET DATA / SIMULATED EXECUTION on the fictional
$500,000 paper account. Paper totals are NEVER mixed with funded totals: no
route here reads a funded table.

EVERY SECTION IS INDEPENDENTLY
    {"status": "OK" | "EMPTY" | "UNAVAILABLE", "why": <reason or null>,
     "data": ...}
EMPTY carries the named reason; UNAVAILABLE names the failed read. Every
response carries `labels`, `data_label` and `last_updated_at`.

ROUTES (shapes documented in docs/PAPER_TRADING_READ_MODELS.md):
  GET /api/command/paper/account     derived balances + latest N entries
  GET /api/command/paper/stream      Server-Sent Events of COMMITTED ledger
                                     entries (Last-Event-ID replay)
  GET /api/command/paper/session     id, start, frozen config, simulator
                                     version, health, heartbeats, mutation
                                     attempts
  GET /api/command/paper/derek       opportunities (decisions), paper orders,
                                     fills, handoffs
  GET /api/command/paper/xavier      positions, standing orders,
                                     recommendations
  GET /api/command/paper/audrey      daily report and audit entries
  GET /api/command/paper/benchmark   the EXPERIMENTAL PINNACLE_ONLY_PAPER_
                                     BENCHMARK (decisions with shortfalls,
                                     orders, fills, handoffs; disclosure: not
                                     evidence of qualified or proven
                                     profitability)
"""
from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from .agents_core import require_read

router = APIRouter()

STREAM_POLL_S = 1.0
STREAM_HEARTBEAT_S = 15.0
STREAM_BATCH = 200


def _labels() -> dict:
    from .. import bettor_paper_ledger as L
    return {"data_label": L.DATA_LABEL, "labels": dict(L.LABELS)}


async def _pool():
    from ..db import get_pool
    try:
        return await get_pool()
    except Exception as exc:                                    # noqa: BLE001
        raise HTTPException(status_code=503, detail={
            "reason": "NO_DATABASE_POOL", "detail": type(exc).__name__})


async def section(coro, *, empty_why: str, is_empty=None) -> dict:
    try:
        data = await coro
    except Exception as exc:                                    # noqa: BLE001
        return {"status": "UNAVAILABLE",
                "why": "%s: %s" % (type(exc).__name__, str(exc)[:160]),
                "data": None}
    empty = (is_empty(data) if is_empty is not None else not data)
    if empty:
        return {"status": "EMPTY", "why": empty_why, "data": data}
    return {"status": "OK", "why": None, "data": data}


async def _schema(conn) -> bool:
    try:
        return bool(await conn.fetchval(
            "SELECT to_regclass('paper_ledger') IS NOT NULL"))
    except Exception:                                           # noqa: BLE001
        return False


def _unavailable_schema() -> dict:
    return {"status": "UNAVAILABLE", "why": "MIGRATION_171_IS_NOT_APPLIED",
            "data": None}


# ═════════════════════════════════════════════════════════════════════
# ACCOUNT
# ═════════════════════════════════════════════════════════════════════

async def account_payload(conn, *, entries: int = 50,
                          now: float | None = None) -> dict:
    from .. import bettor_paper_ledger as L
    at = float(now if now is not None else time.time())
    out = dict(_labels(), as_of=at, account_id=L.ACCOUNT_ID)
    if not await _schema(conn):
        out.update(account=_unavailable_schema(),
                   ledger=_unavailable_schema(), last_updated_at=None)
        return out
    acct = await L.selected_account(conn)
    out['account_id'] = acct
    bal = await section(L.balances(conn, acct, now=at),
                        empty_why="THE_PAPER_ACCOUNT_DOES_NOT_EXIST",
                        is_empty=lambda d: not (d or {}).get("ok"))
    led = await section(L.latest_entries(conn, acct, limit=entries),
                        empty_why="NO_LEDGER_ENTRIES")
    out["account"] = bal
    out["ledger"] = led
    from .. import bettor_day_one as E
    if acct != L.ACCOUNT_ID:
        out['epoch'] = await E.read(conn, acct)
    out["session"] = await session_brief(conn, bal.get("data") or {})
    out["last_updated_at"] = ((bal.get("data") or {}).get("last_updated_at")
                              if bal["status"] == "OK" else None)
    # Drawdown, from the session's equity snapshots when they exist.
    try:
        from .. import bettor_paper_readmodel as RM
        out["drawdown"] = await section(
            RM.drawdown(conn, acct),
            empty_why="NO_EQUITY_SNAPSHOTS_YET_THE_SESSION_HAS_NOT_RUN",
            is_empty=lambda d: not (d or {}).get("snapshots"))
    except ImportError:
        out["drawdown"] = {"status": "UNAVAILABLE",
                           "why": "READ_MODEL_NOT_INSTALLED", "data": None}
    return out


@router.get('/api/command/paper/day-one', dependencies=[Depends(require_read)])
async def day_one_epoch():
    from .. import bettor_day_one as E
    pool = await _pool()
    async with pool.acquire() as conn:
        return await E.read(conn)


@router.get('/api/command/paper/archive/{account_id}', dependencies=[Depends(require_read)])
async def historical_account(account_id: str):
    from .. import bettor_paper_ledger as L
    if not L.is_paper_id(account_id):
        raise HTTPException(status_code=400, detail='NOT_A_PAPER_IDENTIFIER')
    pool = await _pool()
    async with pool.acquire() as conn:
        return {'label': 'HISTORICAL PAPER — LOSSES AND OBLIGATIONS PRESERVED',
                'account_id': account_id, 'balances': await L.balances(conn, account_id),
                'ledger': await L.latest_entries(conn, account_id, limit=100)}


async def session_brief(conn, bal: dict) -> dict:
    """THE BANNER'S ONE READ: {active, reason, session_id, started_at,
    starting_cash_usd, last_heartbeat_at, real_money_submission}."""
    from .. import bettor_paper_ledger as L
    from .. import bettor_paper_session as S
    out = {"active": False, "reason": None, "session_id": None,
           "started_at": None,
           "starting_cash_usd": bal.get("starting_cash_usd",
                                        float(L.STARTING_CASH_USD)),
           "last_heartbeat_at": None, "real_money_submission": "DISABLED"}
    try:
        en = await S.enablement(conn)
        sess = await S.active_session(conn, bal.get('account_id') or await L.selected_account(conn))
        if sess is not None:
            h = await S.health(conn, sess["session_id"]) or {}
            out.update(session_id=sess["session_id"],
                       started_at=sess["started_at"],
                       last_heartbeat_at=h.get("heartbeat_at"))
        out["active"] = bool(sess is not None and en.get("enabled"))
        out["reason"] = (None if out["active"] else
                         en.get("refusal") or "NO_ACTIVE_PAPER_SESSION_YET")
    except Exception as exc:                                    # noqa: BLE001
        out["reason"] = "SESSION_UNREADABLE: %s" % type(exc).__name__
    return out


@router.get("/api/command/paper/account",
            dependencies=[Depends(require_read)])
async def paper_account(entries: int = Query(50, ge=1, le=500)) -> dict:
    pool = await _pool()
    async with pool.acquire() as conn:
        return await account_payload(conn, entries=entries)


# ═════════════════════════════════════════════════════════════════════
# THE LIVE STREAM: COMMITTED LEDGER ENTRIES ONLY
# ═════════════════════════════════════════════════════════════════════

def sse(event: str, data: dict, *, event_id=None) -> str:
    head = "" if event_id is None else "id: %s\n" % event_id
    return "%sevent: %s\ndata: %s\n\n" % (head, event, json.dumps(
        data, default=str, separators=(",", ":")))


async def stream_events(acquire, *, last_event_id: int | None,
                        poll_s: float = STREAM_POLL_S,
                        heartbeat_s: float = STREAM_HEARTBEAT_S,
                        max_polls: int | None = None,
                        is_disconnected=None, sleep=None,
                        account_id: str | None = None):
    """THE STREAM, as an async generator of SSE frames.

    `acquire()` is an async context manager yielding a connection (the
    pool's `acquire`). Publishes ONLY committed ledger entries: it polls the
    ledger by sequence, and the ledger's trigger numbers each entry after the
    account row lock, so sequence order is commit order and no committed
    entry is skipped. With `last_event_id` it REPLAYS every entry after it
    (reconnect recovery); without one it opens with a full snapshot."""
    from .. import bettor_paper_ledger as L
    async with acquire() as conn:
        acct = account_id or await L.selected_account(conn)
    if account_id is None and acct != L.ACCOUNT_ID:
        # Legacy Last-Event-ID is a bare per-account sequence; it cannot be
        # carried into another account. Start with a verified full snapshot.
        last_event_id = None
    sleep = sleep or asyncio.sleep
    cursor = last_event_id
    polls = 0
    last_beat = time.monotonic()
    if cursor is None:
        async with acquire() as conn:
            if not await _schema(conn):
                yield sse("unavailable", dict(
                    _labels(), why="MIGRATION_171_IS_NOT_APPLIED"))
                return
            bal = await L.balances(conn, acct)
            latest = await L.latest_entries(conn, acct, limit=20)
            epoch = None
            if acct.startswith('paper_day_one_'):
                from .. import bettor_day_one as E
                epoch = await E.read(conn, acct, bal=bal)
        cursor = int(bal.get("last_sequence") or 0)
        snapshot = dict(_labels(), sequence=cursor, balances=bal,
                        latest_entries=latest, last_updated_at=bal.get('last_updated_at'))
        if epoch is not None:
            snapshot['epoch'] = epoch
        yield sse('snapshot', snapshot, event_id=cursor)
    while max_polls is None or polls < max_polls:
        polls += 1
        if is_disconnected is not None and await is_disconnected():
            return
        async with acquire() as conn:
            if account_id is None and await L.selected_account(conn) != acct:
                yield sse('epoch_changed', {'previous_account_id': acct, 'reset_cursor': True})
                return
            rows = await L.ledger_after(conn, acct, after_seq=int(cursor),
                                        limit=STREAM_BATCH)
            bal = await L.balances(conn, acct) if rows else None
        for i, e in enumerate(rows):
            cursor = e["sequence"]
            frame = dict(_labels(), sequence=e["sequence"], entry=e,
                         running_balances={
                             "cash_usd": e["cash_after_usd"],
                             "reserved_usd": e["reserved_after_usd"],
                             "available_usd": e["available_after_usd"]},
                         committed_at=e["committed_at"],
                         last_updated_at=e["committed_at"])
            if i == len(rows) - 1:
                frame["balances"] = bal
            yield sse("ledger", frame, event_id=e["sequence"])
        if time.monotonic() - last_beat >= heartbeat_s:
            last_beat = time.monotonic()
            yield sse("heartbeat", dict(_labels(), sequence=cursor,
                                        at=time.time()))
        if max_polls is None or polls < max_polls:
            await sleep(poll_s)


@router.get("/api/command/paper/stream",
            dependencies=[Depends(require_read)])
async def paper_stream(request: Request,
                       last_event_id: str | None = Header(
                           default=None, alias="Last-Event-ID"),
                       last: int | None = Query(None, ge=0)):
    pool = await _pool()
    cur = last
    if cur is None and last_event_id:
        try:
            cur = int(str(last_event_id).strip())
        except ValueError:
            cur = None
    gen = stream_events(pool.acquire, last_event_id=cur,
                        is_disconnected=request.is_disconnected)
    return StreamingResponse(gen, media_type="text/event-stream",
                             headers={"cache-control": "no-store",
                                      "x-accel-buffering": "no"})


# ═════════════════════════════════════════════════════════════════════
# SESSION, DEREK, XAVIER, AUDREY
# ═════════════════════════════════════════════════════════════════════

async def _readmodel(name: str, conn, **kw) -> dict:
    from .. import bettor_paper_readmodel as RM
    return await getattr(RM, name)(conn, **kw)


@router.get("/api/command/paper/freshness",
            dependencies=[Depends(require_read)])
async def paper_freshness(limit: int = Query(500, ge=0, le=5000)) -> dict:
    """EVERY OPEN PAPER POSITION, CLASSIFIED (bettor_paper_freshness): FRESH /
    QUIET_VALID / STALE / FEED_GAP / UNMARKED / EXTERNAL_UNAVAILABLE, each
    count with its rule, the fresh and stale-management rates, the latest
    held-mark refresh run, and per position the mark source / time / age,
    the valuation id, the executable bid / ask and exit depth, the residual
    qty reconciled with the ledger, the settlement fingerprint and the
    protection state. Read only (a READ ONLY transaction); a failed read is
    UNAVAILABLE, never zero."""
    from .. import bettor_paper_freshness as PMF
    from .. import bettor_paper_ledger as L
    pool = await _pool()
    async with pool.acquire() as conn:
        if not await _schema(conn):
            return dict(_labels(), freshness=_unavailable_schema())
        async with conn.transaction(readonly=True):
            await conn.execute("SET LOCAL statement_timeout = 8000")
            got = await PMF.read(conn, await L.selected_account(conn), rows_limit=limit)
        return dict(_labels(), freshness=got,
                    market_data=_market_data_telemetry(got))


def _market_data_telemetry(fresh: dict) -> dict:
    """THE SHARED PAPER MARKET-DATA PATH, AS THIS PROCESS SEES IT (P0
    market-data freshness): the one paper REST owner's requests / min, 2xx,
    429, cache hits, coalesced reads, REST dispatches and queue depth per
    lane; the retail and institutional stream update counts; the latest
    held-mark refresh run's per-source counts; and, from the freshness read,
    held marks by source and the oldest held-mark age. Never raises."""
    from .. import paper_market_data as PMD
    try:
        tel = PMD.telemetry()
    except Exception as exc:                                    # noqa: BLE001
        tel = {"unavailable": type(exc).__name__}
    try:
        from ..agents import paper_mark_refresh as PMR
        last = (PMR.status() or {}).get("last") or {}
    except Exception:                                           # noqa: BLE001
        last = {}
    feeds = (fresh or {}).get("feeds") or {}
    tot = tel.get("totals") or {}
    return {
        "version": tel.get("version"),
        "scope": "THIS_API_PROCESS (paper pass, mark refresh and the retail "
                 "subscription run here)",
        "requests_per_min": tel.get("rest_requests_per_min"),
        "responses_2xx_per_min": tel.get("responses_2xx_per_min"),
        "responses_429_per_min": tel.get("responses_429_per_min"),
        "responses_2xx": tot.get("responses_2xx"),
        "responses_429": tot.get("responses_429"),
        "rest_dispatches": tot.get("rest_dispatches"),
        "cache_hits": tot.get("cache_hits"),
        "coalesced_reads": tot.get("coalesced_reads"),
        "discovery_deferred_during_hold":
            tot.get("discovery_deferred_during_hold"),
        "queue_deadline_refusals": tot.get("queue_deadline_refusals"),
        "queue_depth": tel.get("queue_depth"),
        "venue_hold": tel.get("venue_hold"),
        "stream_updates": {
            "retail": ((tel.get("streams") or {}).get("retail") or {}).get(
                "updates"),
            "institutional": ((tel.get("streams") or {}).get(
                "institutional") or {}).get("updates")},
        "streams": tel.get("streams"),
        # PER AUTH LANE (AUTHENTICATED / PUBLIC_GATEWAY): requests / min,
        # 2xx, 429, the last Retry-After seen and the current hold -- each
        # lane's own; a 429 on one never holds the other
        "auth_lanes": tel.get("auth_lanes"),
        "rest_fallbacks": last.get("read_attempted"),
        "last_refresh_sources": last.get("sources"),
        "held_marks_by_source": feeds.get("held_marks_by_source"),
        "fresh_marks_by_source": feeds.get("fresh_marks_by_source"),
        "oldest_held_mark_age_s": feeds.get("oldest_held_mark_age_s"),
        "never_read_markable": feeds.get("never_read_markable"),
        "owner": tel}


@router.get("/api/command/paper/session",
            dependencies=[Depends(require_read)])
async def paper_session() -> dict:
    pool = await _pool()
    async with pool.acquire() as conn:
        if not await _schema(conn):
            return dict(_labels(), session=_unavailable_schema())
        return await _readmodel("session_payload", conn)


@router.get("/api/command/paper/reconciliation",
            dependencies=[Depends(require_read)])
async def paper_reconciliation() -> dict:
    """THE CANONICAL POSITION RECONCILIATION RECEIPT
    (bettor_paper_reconciliation): true opens by the one rule (bought - sold
    - latest settlement > the ledger epsilon), what the legacy readers would
    have called open that is not (phantom opens), sub-contract remainders,
    economic-duplicate fill suspects (listed, never rewritten) and any live
    protection on a closed position. Read only; a failed read is
    UNAVAILABLE, never zero."""
    from .. import bettor_paper_ledger as L
    from .. import bettor_paper_reconciliation as REC
    pool = await _pool()
    async with pool.acquire() as conn:
        if not await _schema(conn):
            return dict(_labels(), reconciliation=_unavailable_schema())
        async with conn.transaction(readonly=True):
            await conn.execute("SET LOCAL statement_timeout = 12000")
            got = await REC.receipt(conn, await L.selected_account(conn))
        return dict(_labels(), reconciliation=got)


@router.get("/api/command/paper/derek", dependencies=[Depends(require_read)])
async def paper_derek(limit: int = Query(100, ge=1, le=1000)) -> dict:
    pool = await _pool()
    async with pool.acquire() as conn:
        if not await _schema(conn):
            return dict(_labels(), derek=_unavailable_schema())
        return await _readmodel("derek_payload", conn, limit=limit)


@router.get("/api/command/paper/xavier",
            dependencies=[Depends(require_read)])
async def paper_xavier(limit: int = Query(100, ge=1, le=1000)) -> dict:
    pool = await _pool()
    async with pool.acquire() as conn:
        if not await _schema(conn):
            return dict(_labels(), xavier=_unavailable_schema())
        return await _readmodel("xavier_payload", conn, limit=limit)


@router.get("/api/command/paper/audrey",
            dependencies=[Depends(require_read)])
async def paper_audrey(limit: int = Query(50, ge=1, le=500)) -> dict:
    pool = await _pool()
    async with pool.acquire() as conn:
        if not await _schema(conn):
            return dict(_labels(), audrey=_unavailable_schema())
        return await _readmodel("audrey_payload", conn, limit=limit)


@router.get("/api/command/paper/benchmark",
            dependencies=[Depends(require_read)])
async def paper_benchmark(limit: int = Query(100, ge=1, le=1000),
                          strategy: str | None = Query(None)) -> dict:
    """THE EXPERIMENTAL PINNACLE_ONLY_PAPER_BENCHMARK (read only): its
    decisions with refusal shortfalls, orders, fills and handoffs, with the
    disclosure that it is experimental execution, not evidence of qualified
    or proven profitability."""
    pool = await _pool()
    async with pool.acquire() as conn:
        if not await _schema(conn):
            return dict(_labels(), benchmark=_unavailable_schema())
        return await _readmodel("benchmark_payload", conn, limit=limit,
                                strategy=strategy)


# ═════════════════════════════════════════════════════════════════════
# THE OPERATIONAL VIEW: THE PAPER SESSION AS THE PAGES' DEFAULT
# ═════════════════════════════════════════════════════════════════════
#
# bettor_paper_ops: Derek's strategies apart (the original two-model research
# policy, then each EXPERIMENTAL benchmark with its policy version and
# labels) with the eligibility funnel from each decision's own conditions;
# every paper position handed to Xavier with its strategy, his reviews,
# protection orders, settlements and what is pending; Audrey's account,
# P&L by strategy, audits and daily report; and THREE freshness stamps
# (server read, the paper runtime's own heartbeat, the last committed ledger
# transaction). Every section independently OK / EMPTY / UNAVAILABLE; a
# failed read is UNAVAILABLE by name, never a zero.

@router.get("/api/command/paper/operations",
            dependencies=[Depends(require_read)])
async def paper_operations(agent: str = Query(..., pattern="^(derek|xavier|audrey)$"),
                           limit: int = Query(25, ge=1, le=200)) -> dict:
    from .. import bettor_paper_ledger as L
    from .. import bettor_paper_ops as OPS
    pool = await _pool()
    async with pool.acquire() as conn:
        if not await _schema(conn):
            return dict(_labels(), agent=agent,
                        operations=_unavailable_schema())
        out = await OPS.OPERATIONS[agent](conn, account_id=await L.selected_account(conn),
                                          limit=limit)
        out["agent"] = agent
        return out


@router.get("/api/command/paper/experiment",
            dependencies=[Depends(require_read)])
async def paper_experiment() -> dict:
    """THE EXPERIMENT AS MANAGEMENT SEES IT (`bettor_paper_experiment`):
    session, freshness, opportunities, refusals, closest opportunities,
    standing orders, fills, positions, account, agents, throughput -- every
    figure a persisted record. Answering at all means CONNECTED."""
    from .. import bettor_paper_experiment as EXP
    pool = await _pool()
    async with pool.acquire() as conn:
        if not await _schema(conn):
            return dict(_labels(), experiment=_unavailable_schema())
        return await EXP.experiment(conn)


@router.get("/api/command/paper/overview",
            dependencies=[Depends(require_read)])
async def paper_overview() -> dict:
    from .. import bettor_paper_ledger as L
    from .. import bettor_paper_ops as OPS
    pool = await _pool()
    async with pool.acquire() as conn:
        if not await _schema(conn):
            return dict(_labels(), overview=_unavailable_schema())
        return await OPS.overview(conn, account_id=await L.selected_account(conn))


@router.get("/api/command/paper/trader-mode", dependencies=[Depends(require_read)])
async def paper_trader_mode() -> dict:
    """Read-only management wall; same-origin command authentication."""
    from . import trader_readmodel as TR
    from fastapi.responses import JSONResponse
    pool = await _pool()
    try:
        payload = await TR.cached(pool)
    except Exception as exc:
        raise HTTPException(status_code=503, detail={
            "reason":"TRADER_READBACK_UNAVAILABLE","detail":type(exc).__name__})
    return JSONResponse(payload,headers={"Cache-Control":"private, no-store"})
