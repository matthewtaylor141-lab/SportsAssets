"""KALSHI WEBSOCKET MARKET DATA -- THE PRIMARY KALSHI BOOK RUNTIME,
DEDICATED-ONLY (Kalshi rep production contract 2026-10-07).

Runs inside the dedicated market-data service (sportsassets-market-plane,
started beside the universal market plane by its own entry point), never in
the shared workers and never on sportsassets-api (workers/all.py names it in
DEDICATED_ONLY_LOOPS; it is not in LOOPS).

  credential   KALSHI_API_KEY_ID + KALSHI_PRIVATE_KEY_PEM on THIS service
               only; absent -> OWNER_ACTION_REQUIRED in the heartbeat, no
               connection attempted, no crash
  limits       GET /trade-api/v2/account/limits at start and every 30 min:
               usage tier, read / write buckets, grants, as_of, source
  books        kalshi_ws.Subscriber over the tracked game markets
               (kalshi_fixtures_current, written by the REST catalogue loop)
  persistence  kalshi_books_current, book_basis KALSHI_WS_...: a changed
               CURRENT book is written within ~2 s (the flush); every
               CURRENT book's observed_at is re-asserted every 10 s (the
               stream proves it current) while its row holds the book as it
               is now; (RC6.2) a book that leaves CURRENT (a gap, the
               venue ending the subscription, a disconnect) has its row made
               readable = false AT ONCE -- the runtime awaits the write
               (gap_sink, bounded) before it reads the next frame, so no
               reader is served a pre-gap row between the gap and the next
               flush; a market no longer tracked has its row made unreadable
               at the wanted-set refresh that drops it (UNTRACKED)
  heartbeat    service kalshi_ws_market_data, domain KALSHI_HEALTH (never
               blended with POLYMARKET_HEALTH): connected, subscribed
               markets, current / gap counts, snapshots / deltas / gaps /
               resubscribes, the oldest current-book update age, account
               limits, REST pacing derived from them; (RC6.2) why the last
               session ended (session_end: a storm bound, a gap during
               recovery, the venue ending the subscription), the commands
               sent by kind and the immediate GAP writes that failed

STRUCTURALLY READ-ONLY: imports kalshi_ws, the db helpers and nothing that
can place, cancel, fund or authorize (test_kalshi_ws_market_data checks the
import closure). That is a property of THIS CODE and its process
(market_plane_guard), never of the key: Kalshi documents no read-only API
key class, so the key is account-wide. The heartbeat says so
(`credential_scope`) beside the key's documented type (`key`: Ed25519 or
RSA, read from the parsed key; RC5 2026-10-08). A key that is present but
not a documented Kalshi key is named in the heartbeat (OWNER_ACTION_REQUIRED,
the kalshi_key refusal) instead of raising out of the runtime.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time

from .. import credential_isolation as CI
from .. import kalshi_key as KK
from .. import kalshi_market_data as KMD
from .. import kalshi_ws as KWS
from ..db import get_pool, heartbeat

log = logging.getLogger(__name__)
SERVICE = "kalshi_ws_market_data"
WANTED_EVERY_S = 60.0
FLUSH_EVERY_S = 2.0
REASSERT_EVERY_S = 10.0
LIMITS_EVERY_S = 1800.0
HEARTBEAT_EVERY_S = 15.0
LOOKAHEAD_S = 36 * 3600.0
LOOKBACK_S = 4 * 3600.0
MAX_MARKETS = 2000
#: the error a row of a market no longer tracked is retired with
UNTRACKED = "UNTRACKED:KALSHI_WS_MARKET_NOT_TRACKED"
OWNER_ACTION = (
    "provision a Kalshi API key (Ed25519 -- Kalshi's default -- or RSA) as "
    "KALSHI_API_KEY_ID + KALSHI_PRIVATE_KEY_PEM on the dedicated "
    "sportsassets-market-plane service ONLY -- never on sportsassets-api or "
    "the shared workers. The WebSocket handshake requires authentication "
    "even for public order books (docs: websockets/websocket-connection). "
    "Kalshi documents no read-only key class: the key is account-wide, and "
    "this service only reads because its own code cannot do anything else "
    "(market_plane_guard), not because the key is scoped. This code path "
    "places, cancels and funds nothing.")
#: the key's scope, stated as what it is: Kalshi has no read-only key class
CREDENTIAL_SCOPE = ("ACCOUNT_WIDE_KEY_NO_PROVIDER_READ_ONLY_CLASS; read-only "
                    "by this process's code (market_plane_guard), not by the "
                    "key")


async def wanted_tickers(conn, *, now: float) -> list:
    rows = await conn.fetch(
        "SELECT team_tickers, tie_ticker FROM kalshi_fixtures_current "
        " WHERE mapping_status = 'ESTABLISHED' AND start_at BETWEEN "
        "       to_timestamp($1) AND to_timestamp($2) ORDER BY start_at",
        now - LOOKBACK_S, now + LOOKAHEAD_S)
    out = []
    for r in rows:
        out.extend(r["team_tickers"] or [])
        if r["tie_ticker"]:
            out.append(r["tie_ticker"])
    return sorted(set(out))[:MAX_MARKETS]


#: (RC6.2) the statement that makes rows unreadable the moment their books
#: leave CURRENT (one per gap, for every market it touched)
GAP_ROWS_SQL = (
    "UPDATE kalshi_books_current SET readable = false, error = $2, "
    " updated_at = now() WHERE ticker = ANY($1::text[]) AND book_basis = $3"
    " AND readable")


async def write_book(conn, ticker: str, books: KWS.WsBooks, *,
                     now: float) -> None:
    cur = books.current(ticker)
    ev = ticker.rsplit("-", 1)[0]
    series = ev.split("-")[0]
    if cur["ok"]:
        b = KMD.book_from_orderbook(cur["book"], observed_at=now)
        await conn.execute(
            "INSERT INTO kalshi_books_current (ticker, event_ticker, "
            " series_ticker, yes_bids, no_bids, yes_asks, no_asks, "
            " book_basis, readable, error, observed_at, updated_at) VALUES "
            "($1,$2,$3,$4::jsonb,$5::jsonb,$6::jsonb,$7::jsonb,$8,true,NULL,"
            " to_timestamp($9), now()) ON CONFLICT (ticker) DO UPDATE SET "
            " yes_bids = EXCLUDED.yes_bids, no_bids = EXCLUDED.no_bids, "
            " yes_asks = EXCLUDED.yes_asks, no_asks = EXCLUDED.no_asks, "
            " book_basis = EXCLUDED.book_basis, readable = true, "
            " error = NULL, observed_at = EXCLUDED.observed_at, "
            " updated_at = now()",
            ticker, ev, series, _lv(b.get("yes_bids")),
            _lv(b.get("no_bids")), _lv(b.get("yes_asks")),
            _lv(b.get("no_asks")), KWS.BOOK_BASIS, now)
    else:
        # GAP / awaiting snapshot: never routable, at once
        await conn.execute(
            "UPDATE kalshi_books_current SET readable = false, error = $2, "
            " updated_at = now() WHERE ticker = $1 AND book_basis = $3",
            ticker, "%s:%s" % (cur["state"], cur.get("why")), KWS.BOOK_BASIS)


def flush_key(b: dict) -> tuple:
    """What the flush compares: the book's state and the stamp of its
    latest change (WsBooks bumps it on every level or state change). The
    key used to be (state, seq, updated_at): a gap, a resubscribe and a
    fresh snapshot at the same seq inside one clock tick (or on a clock
    that stepped back) left the same key, so the row kept the pre-gap
    book."""
    return (b["state"], b.get("rev"))


class _NoLock:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *a):
        return False


async def flush(conn, books: KWS.WsBooks, written: dict, *,
                now: float, lock=None) -> int:
    """Write every book whose key changed since it was last written;
    `written` then holds, per market, the key of the book its row holds.
    The key is taken in the same step as write_book reads the book (no
    suspension between them), so it names exactly what was written.
    (RC6.2) Each row is written under `lock`, the lock the immediate GAP
    write takes too, so a GAP write can never land BEFORE a CURRENT write of
    the same row that read the pre-gap book (the GAP write waits for at most
    one row)."""
    n = 0
    lock = lock or _NoLock()
    for t in list(books.books):
        async with lock:
            b = books.books.get(t)
            if b is None:
                continue
            k = flush_key(b)
            if written.get(t) != k:
                await write_book(conn, t, books, now=now)
                written[t] = k
                n += 1
    return n


async def write_gaps(conn, books: KWS.WsBooks, written: dict,
                     tickers) -> int:
    """(RC6.2) THE PRE-GAP ROW IS NEVER SERVED. Every listed market whose
    book is not CURRENT now has its row made unreadable at once, named by
    its state and reason, and `written` records the key of what the row now
    holds (the flush then has nothing to redo). The window the second review
    found -- between a gap and the next flush (up to FLUSH_EVERY_S) a reader
    took the row of the pre-gap book as a current WebSocket book -- is
    closed: the runtime awaits this before it reads the next frame. Returns
    how many markets."""
    by_err: dict = {}
    for t in sorted(set(tickers)):
        b = books.books.get(t)
        if b is None:
            continue
        cur = books.current(t)
        if cur["ok"]:
            continue
        by_err.setdefault("%s:%s" % (cur["state"], cur.get("why")),
                          []).append(t)
    n = 0
    for err, ts in sorted(by_err.items()):
        await conn.execute(GAP_ROWS_SQL, ts, err, KWS.BOOK_BASIS)
        for t in ts:
            b = books.books.get(t)
            if b is not None:
                written[t] = flush_key(b)
        n += len(ts)
    return n


def gap_sink(get_pool_fn, books: KWS.WsBooks, written: dict, lock):
    """The Subscriber's gap_sink: write_gaps on a connection of the pool,
    under the flush's lock -- taken only once the connection is held, so the
    flush (which holds a connection and waits on the lock between rows) and
    the GAP write can never wait on each other."""
    async def sink(tickers):
        pool = await get_pool_fn()
        async with pool.acquire() as conn:
            async with lock:
                await write_gaps(conn, books, written, tickers)
    return sink


async def retire_untracked(conn, want) -> None:
    """(RC6 acceptance model) No WebSocket-basis row reads readable for a
    market the runtime does not track. A dropped market's row used to stay
    readable = true at its last observed_at (re-asserted up to 10 s
    before), so for up to BOOK_SLA_S every reader -- REST ws_current (REST
    then skips it), db_freshness, the claim scan -- took a book nobody
    maintained any more as current; a row left by an earlier process (its
    market no longer wanted at restart) stayed readable for ever. One
    statement per wanted-set refresh: every readable WS row outside the
    wanted set is made unreadable, named UNTRACKED."""
    await conn.execute(
        "UPDATE kalshi_books_current SET readable = false, error = $2, "
        " updated_at = now() WHERE book_basis = $1 AND readable AND "
        " NOT (ticker = ANY($3::text[]))",
        KWS.BOOK_BASIS, UNTRACKED, sorted(set(want)))


def prune_untracked(sub, written: dict, want) -> int:
    """(RC6) The books, the written-key map and the tracked subscription of
    every market no longer in the wanted set are dropped (KWS.Subscriber.
    forget; the venue-side subscription ends with the session); returns
    how many. Without it all three grew with every market the
    runtime ever tracked (production 2026-10-09: 235 books held, 185
    wanted; research-sql rc6_api-responsive_kalshi_ws_growth.sql), and the
    heartbeat's freshness counted the 50 untracked ones. Their rows are
    made unreadable in the same pass (retire_untracked)."""
    keep = set(want)
    gone = [t for t in list(sub.books.books) if t not in keep]
    gone += [t for t in list(written) if t not in keep and t not in gone]
    if not gone:
        return 0
    n = sub.forget(gone)
    for t in gone:
        written.pop(t, None)
    return n


def _lv(levels) -> str:
    return json.dumps([[str(p), int(q)] for p, q in (levels or ())])


async def reassert(conn, books: KWS.WsBooks, *, now: float,
                   written: dict | None = None) -> int:
    """Re-stamp observed_at of the rows of CURRENT books. Given `written`
    (the flush's keys), only a row that holds the book as it is now: a
    reassert due when the flush is not used to re-stamp a row still holding
    the PRE-GAP book of a market whose fresh snapshot had arrived since the
    last flush (review of ca102147); that row now keeps its age until the
    flush rewrites it."""
    cur = [t for t, b in books.books.items() if books.current(t)["ok"]
           and (written is None or written.get(t) == flush_key(b))]
    if not cur:
        return 0
    await conn.execute(
        "UPDATE kalshi_books_current SET observed_at = to_timestamp($2) "
        " WHERE ticker = ANY($1::text[]) AND book_basis = $3 AND readable",
        cur, now, KWS.BOOK_BASIS)
    return len(cur)


async def read_limits(key_id: str, pk, *, now: float) -> dict:
    import httpx
    hdr = KWS.auth_headers(key_id, pk, "GET", KWS.LIMITS_PATH)
    async with httpx.AsyncClient(timeout=15.0) as c:
        r = await c.get(KWS.REST_BASE + KWS.LIMITS_PATH, headers=hdr)
    if r.status_code != 200:
        return {"status": "HTTP_%d" % r.status_code, "as_of": now,
                "source": "GET " + KWS.LIMITS_PATH}
    return dict(KWS.parse_limits(r.json(), as_of=now), status="OK")


async def record_limits(conn, lim: dict | None) -> None:
    """APPEND each account-limits read (migration 315)."""
    if not lim or not await conn.fetchval(
            "SELECT to_regclass('kalshi_account_limits_receipts') "
            "IS NOT NULL"):
        return
    r, w = lim.get("read") or {}, lim.get("write") or {}

    def n(v):
        try:
            return None if v is None else __import__("decimal").Decimal(
                str(v))
        except Exception:                                       # noqa: BLE001
            return None
    await conn.execute(
        "INSERT INTO kalshi_account_limits_receipts (receipt_id, as_of, "
        " status, usage_tier, read_refill_rate, read_bucket_capacity, "
        " write_refill_rate, write_bucket_capacity, grants, "
        " websocket_connection_limit, source) VALUES ($1, to_timestamp($2),"
        " $3, $4, $5, $6, $7, $8, $9::jsonb, $10, $11) ON CONFLICT "
        " (receipt_id) DO NOTHING",
        "kal-limits-%d" % int(float(lim.get("as_of") or 0) * 1000),
        float(lim.get("as_of") or 0), str(lim.get("status") or "OK"),
        lim.get("usage_tier"), n(r.get("refill_rate")),
        n(r.get("bucket_capacity")), n(w.get("refill_rate")),
        n(w.get("bucket_capacity")), json.dumps(lim.get("grants") or []),
        str(lim.get("websocket_connection_limit") or
            "NOT_RETURNED_BY_VENUE"),
        str(lim.get("source") or "GET " + KWS.LIMITS_PATH))


def health(books: KWS.WsBooks, sub, *, now: float, limits, pace,
           key: dict | None = None) -> dict:
    c = books.counts()
    ages = [now - b["updated_at"] for t, b in books.books.items()
            if books.current(t)["ok"] and b["updated_at"]]
    return {"domain": KMD.HEALTH_DOMAIN, "source": "KALSHI_WEBSOCKET",
            "version": KWS.VERSION, "ws": c,
            "connections": getattr(sub, "connections", 0),
            "resubscribes": getattr(sub, "resubscribes", 0),
            "untracked_dropped": {
                "books": books.stats.get("forgotten", 0),
                "messages_ignored": books.stats.get("ignored_not_tracked", 0),
                "still_subscribed_until_session_end": len(
                    getattr(books, "forgotten", ()) or ())},
            "last_error": getattr(sub, "last_error", None),
            # (RC6.2) why sessions ended (a storm bound, a gap during
            # recovery, the venue ending the subscription), what was sent
            "session_end": {
                "last": getattr(sub, "last_end_reason", None),
                "by_reason": dict(getattr(sub, "session_ends", {}) or {}),
                "storms": getattr(sub, "storms", 0)},
            "commands_sent": dict(getattr(sub, "commands_sent", {}) or {}),
            "gap_writes_failed": getattr(sub, "gap_sink_failures", 0),
            "subscribed_markets": len(getattr(sub, "subscribed", ()) or ()),
            "current_book_update_age_s": {
                "max": round(max(ages), 1) if ages else None,
                "min": round(min(ages), 1) if ages else None},
            "freshness": {"domain": KMD.HEALTH_DOMAIN,
                          "numerator": c["current"],
                          "denominator": c["markets"],
                          "rate": (round(c["current"] / c["markets"], 4)
                                   if c["markets"] else None),
                          "basis": "WS snapshot on the current subscription "
                                   "and an unbroken sequence"},
            "account_limits": limits, "rest_recovery_pacing": pace,
            "key": key, "credential_scope": CREDENTIAL_SCOPE,
            "authority": "MARKET_DATA_READ_ONLY_NO_ORDER_AUTHORITY"}


def key_class(env) -> dict:
    """The configured key's documented TYPE and the form it loaded in,
    never its value (kalshi_key.describe)."""
    d = KK.describe(env.get(KWS.PRIVATE_KEY_PEM_ENV))
    return {"type": d.get("type"), "form": d.get("form"),
            "refusal": d.get("refusal"),
            "documented_types": list(KK.APPROVED_KEY_TYPES)}


async def run(get_pool_fn=get_pool, *, env=None) -> None:
    env = os.environ if env is None else env
    if not KWS.credential_present(env):
        while True:
            try:
                await heartbeat(SERVICE, "blocked", {
                    "domain": KMD.HEALTH_DOMAIN, "source": "KALSHI_WEBSOCKET",
                    "state": "OWNER_ACTION_REQUIRED",
                    "why": KWS.R_NO_CREDENTIAL, "owner_action": OWNER_ACTION,
                    "rest_fallback": "kalshi_market_data (shared workers) "
                                     "remains the book source until then"})
            except Exception:                                   # noqa: BLE001
                pass
            await asyncio.sleep(300)
    key_id = str(env.get(KWS.KEY_ID_ENV)).strip()
    key = key_class(env)
    why, pk = None, None
    # ANOTHER VENUE'S KEY (RC6 red-team, credential isolation): the same
    # key pair configured in this process for PMX / PMUS never signs a
    # Kalshi handshake (names only, never a value)
    other = CI.reused_by(KWS.PRIVATE_KEY_PEM_ENV, env)
    if other:
        why = CI.R_CROSS_VENUE_KEY_REUSE
        key = dict(key, also_configured_as=other)
    else:
        try:
            pk = KWS.load_private_key(env.get(KWS.PRIVATE_KEY_PEM_ENV))
        except ValueError as exc:
            # PRESENT BUT NOT A DOCUMENTED KALSHI KEY: named, never a crash
            # loop (it used to raise out of run())
            why = getattr(exc, "code", None) or str(exc)
    if why is not None:
        # no connection is attempted
        while True:
            try:
                await heartbeat(SERVICE, "blocked", {
                    "domain": KMD.HEALTH_DOMAIN, "source": "KALSHI_WEBSOCKET",
                    "state": "OWNER_ACTION_REQUIRED", "why": why,
                    "key": key, "credential_scope": CREDENTIAL_SCOPE,
                    "owner_action": OWNER_ACTION,
                    "rest_fallback": "kalshi_market_data (shared workers) "
                                     "remains the book source until then"})
            except Exception:                                   # noqa: BLE001
                pass
            await asyncio.sleep(300)
    books = KWS.WsBooks()
    want: list = []
    written: dict = {}
    # (RC6.2) the flush and the immediate GAP write share one lock
    lock = asyncio.Lock()
    sub = KWS.Subscriber(KWS.websockets_connect(key_id, pk), books,
                         wanted=lambda: want,
                         gap_sink=gap_sink(get_pool_fn, books, written, lock))
    state = {"limits": None, "limits_at": 0.0, "dirty_at": {}}
    task = asyncio.create_task(sub.run())
    last = {"want": 0.0, "flush": 0.0, "reassert": 0.0, "beat": 0.0}
    while True:
        now = time.time()
        try:
            pool = await get_pool_fn()
            async with pool.acquire() as conn:
                if now - last["want"] >= WANTED_EVERY_S:
                    want[:] = await wanted_tickers(conn, now=now)
                    # (RC6.2) the runtime re-reads the list in full now
                    sub.wanted_changed()
                    last["want"] = now
                    prune_untracked(sub, written, want)
                    await retire_untracked(conn, want)
                if now - state["limits_at"] >= LIMITS_EVERY_S:
                    try:
                        state["limits"] = await read_limits(key_id, pk,
                                                            now=now)
                    except Exception as exc:                    # noqa: BLE001
                        state["limits"] = {"status": type(exc).__name__,
                                           "as_of": now}
                    state["limits_at"] = now
                    await record_limits(conn, state["limits"])
                if now - last["flush"] >= FLUSH_EVERY_S:
                    # (the loop variable used to be `key`, overwriting the
                    # key class the heartbeat reports: after the first flush
                    # the beat's `key` was a book's flush tuple)
                    await flush(conn, books, written, now=now, lock=lock)
                    last["flush"] = now
                if now - last["reassert"] >= REASSERT_EVERY_S:
                    await reassert(conn, books, now=now, written=written)
                    last["reassert"] = now
                if now - last["beat"] >= HEARTBEAT_EVERY_S:
                    pace = KWS.pacing(state["limits"])
                    await heartbeat(SERVICE, "ok" if books.connected
                                    else "degraded",
                                    health(books, sub, now=now,
                                           limits=state["limits"],
                                           pace=pace, key=key), con=conn)
                    last["beat"] = now
        except asyncio.CancelledError:
            task.cancel()
            raise
        except Exception as exc:                                # noqa: BLE001
            log.warning("kalshi ws: pass failed (%s)", type(exc).__name__)
        if task.done():
            task = asyncio.create_task(sub.run())
        await asyncio.sleep(1.0)
