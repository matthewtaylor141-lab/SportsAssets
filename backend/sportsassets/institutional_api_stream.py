"""THE READ-ONLY INSTITUTIONAL STREAM IN THE DECIDING PROCESS (the API).

WHY. P5_LIVE_STREAM_BOOK_V1 applies "in the deciding process at the decision
instant": a book resident in the workers proves nothing in the API. This
module gives the API process what the workers process already has, and
nothing more:

  C2  `start()` arms THE SAME stream (`institutional_stream.start_default`)
      in the API lifespan. That function is off unless
      INSTITUTIONAL_MD_STREAM=on, refuses a credential the market-data
      identity guard refuses (absent PMX_* -> CREDENTIAL_REFUSED_BY_IDENTITY_
      GUARD / MARKET_DATA_CREDENTIAL_ABSENT), and records DISABLED_BY_
      CONFIGURATION otherwise -- exactly as in the workers. Only when it
      actually started does this module run its one background task: the
      instrument REFDATA path the stream needs to price a book (each
      instrument's own scales), via the WORKERS' OWN
      `workers.institutional_md.bootstrap_instrument` (REST `instruments`
      read, paced), for the workers' focus set plus the symbols the decision
      path asked about; then `set_instrument` and `want` (subscribe). The
      refdata reads run here, never on the decision path. `stop()` ends the
      task and the stream on API shutdown.

  C1  `identity_mapper(slug, order_intent)` -- installed as
      `live_book_evidence.IDENTITY_MAPPER` -- answers {"status": "EXACT",
      "symbol": ...} ONLY when `institutional_contract_map` maps the retail
      slug to an institutional symbol exactly (same instrument, the YES /
      long side, the instrument's own scales, payout $1.00) on the refdata
      record this process holds. Anything else -- no record yet, a NO /
      short leg, an unknown intent, any refusal -- is None (fail closed).
      With the stream off there is never a record, so it is always None and
      the decision path is unchanged.

NO ORDER PATH. The stream client sends subscribe and keepalive only
(institutional_stream). The refdata read is pmx_institutional's allow-listed
`instruments` read. Nothing here can reach an order, cancel or position
endpoint; test_institutional_md_orderless pins it.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time

from . import institutional_contract_map as ICM
from . import institutional_stream as IS

log = logging.getLogger(__name__)

VERSION = "INSTITUTIONAL_API_STREAM_V1"
#: Symbols this process will bootstrap and subscribe (focus set + asked).
MAX_SYMBOLS = 32
#: The workers' cadences, reused.
LOOP_S = 2.0
FOCUS_EVERY_S = 60.0
REFDATA_REFRESH_S = 3600.0
READ_PACING_S = 0.15
BOOTSTRAP_BACKOFF_S = 30.0
#: A symbol whose refdata read failed or was unlisted is retried no sooner.
RETRY_UNLISTED_S = 300.0

LONG_INTENTS = ("ORDER_INTENT_BUY_LONG", "ORDER_INTENT_SELL_LONG", "YES",
                "LONG", "BUY_YES", "BUY_LONG")

_LOCK = threading.Lock()
#: symbol -> {"record": venue refdata record, "at": epoch s}. Filled ONLY by
#: the background task, i.e. only while the stream runs in this process.
REFDATA: dict = {}
#: symbols the decision path asked about (bounded, insertion ordered).
_REQUESTED: dict = {}
#: symbol -> epoch s of the last refdata attempt (bounds REST reads).
_ATTEMPT: dict = {}
_STATE: dict = {"task": None, "start": None, "last_error": None,
                "refdata_reads": 0, "refdata_failures": 0}


def running() -> bool:
    return _STATE.get("task") is not None and IS.BOOKS.state in IS.RUNNING


def _leg(order_intent) -> str | None:
    s = str(order_intent or "").strip().upper()
    if s in LONG_INTENTS:
        return "yes"
    return None


def request(symbol) -> None:
    """The decision path asked about this symbol. Recorded only while the
    stream runs here; never a venue call."""
    s = str(symbol or "").strip()
    if not s or not running():
        return
    with _LOCK:
        if s not in _REQUESTED and len(_REQUESTED) >= MAX_SYMBOLS:
            return
        _REQUESTED[s] = time.time()


def identity_mapper(slug, order_intent=None) -> dict | None:
    """C1: EXACT or None. Pure over the refdata this process holds."""
    s = str(slug or "").strip()
    leg = _leg(order_intent)
    if not s or leg is None:
        return None
    with _LOCK:
        rec = (REFDATA.get(s) or {}).get("record")
    if rec is None:
        request(s)
        return None
    try:
        m = ICM.map_retail_to_institutional(s, leg, rec)
    except Exception:                                         # noqa: BLE001
        return None
    if not m.get("ok") or m.get("institutional_symbol") != s:
        return None
    return {"status": "EXACT", "symbol": s, "version": m.get("version"),
            "price_scale": m.get("price_scale"),
            "qty_scale": m.get("qty_scale"),
            "price_transform": m.get("price_transform"),
            "institutional_side": m.get("institutional_side"),
            "evidence": list(m.get("basis") or [])}


def describe() -> dict:
    with _LOCK:
        held = sorted(REFDATA)
        asked = list(_REQUESTED)
    return {"version": VERSION, "running": running(),
            "start": _STATE.get("start"), "refdata_symbols": held,
            "requested": asked[:MAX_SYMBOLS],
            "refdata_reads": _STATE.get("refdata_reads"),
            "refdata_failures": _STATE.get("refdata_failures"),
            "last_error": _STATE.get("last_error")}


# ── lifecycle (API lifespan) ──────────────────────────────────────────

async def start(get_pool=None, *, env=None, client=None, focus=None,
                bootstrap=None, transport_factory=None, token_fn=None,
                available=None) -> dict:
    """Arm the stream in THIS process (institutional_stream.start_default)
    and, only when it started, the refdata/subscribe task. NEVER RAISES."""
    env = os.environ if env is None else env
    try:
        out = IS.start_default(env=env, transport_factory=transport_factory,
                               token_fn=token_fn, available=available)
    except Exception as exc:                                  # noqa: BLE001
        out = {"started": False, "state": IS.S_STOPPED,
               "why": "start raised %s" % type(exc).__name__}
    _STATE["start"] = {k: out.get(k) for k in ("started", "state", "why")}
    if not out.get("started"):
        log.info("institutional_api_stream: %s (%s)", out.get("state"),
                 out.get("why"))
        return dict(out, version=VERSION)
    if client is None and bootstrap is None:
        from . import pmx_institutional as pmx
        client = pmx.Institutional(env=env)
    _STATE["task"] = asyncio.get_running_loop().create_task(
        _run(get_pool, client=client, focus=focus, bootstrap=bootstrap),
        name="institutional-api-stream")
    log.info("institutional_api_stream: started")
    return dict(out, version=VERSION)


async def stop() -> None:
    """End the task and the stream (API shutdown). NEVER RAISES."""
    task = _STATE.get("task")
    _STATE["task"] = None
    if task is not None:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):           # noqa: BLE001
            pass
    try:
        t = IS._TRANSPORT
        if t is not None:
            t.stop()
        IS.BOOKS.set_state(IS.S_STOPPED, "API shutdown")
    except Exception:                                         # noqa: BLE001
        pass


def reset() -> None:
    """Tests only."""
    with _LOCK:
        REFDATA.clear()
        _REQUESTED.clear()
        _ATTEMPT.clear()
    _STATE.update(task=None, start=None, last_error=None, refdata_reads=0,
                  refdata_failures=0)


async def _focus_symbols(get_pool, focus) -> list:
    if focus is not None:
        got = focus() if callable(focus) else focus
        if asyncio.iscoroutine(got):
            got = await got
        return [str(s) for s in got or ()]
    if get_pool is None:
        return []
    from . import shadow_experimental_store as xstore
    from .workers import institutional_md as W
    pool = await get_pool()
    return [r["symbol"] for r in await xstore.focus_set(
        pool, size=W.MAX_INSTRUMENTS)]


def _bootstrap_one(client, symbol, bootstrap):
    """The workers' own refdata read (paced), run off the loop."""
    if bootstrap is not None:
        return bootstrap(client, symbol)
    from .venue_pace import pace
    from .workers import institutional_md as W
    pace(READ_PACING_S)
    return W.bootstrap_instrument(client, symbol)


def _due(symbol, at) -> bool:
    with _LOCK:
        have = REFDATA.get(symbol)
        tried = _ATTEMPT.get(symbol)
    if have is not None:
        return at - have["at"] >= REFDATA_REFRESH_S
    return tried is None or at - tried >= RETRY_UNLISTED_S


def pending(now=None) -> list:
    """Asked symbols whose refdata is due (drives an early pass)."""
    at = float(now if now is not None else time.time())
    with _LOCK:
        asked = list(_REQUESTED)
    return [s for s in asked if _due(s, at)]


async def refresh_once(get_pool=None, *, client=None, focus=None,
                       bootstrap=None, now=None, symbols=None) -> dict:
    """ONE pass: focus set + asked symbols -> refdata (only when due: never
    held, older than REFDATA_REFRESH_S, or a failed / unlisted symbol after
    RETRY_UNLISTED_S) -> set_instrument -> want."""
    at = float(now if now is not None else time.time())
    if symbols is None:
        try:
            symbols = await _focus_symbols(get_pool, focus)
        except Exception as exc:                              # noqa: BLE001
            symbols = []
            _STATE["last_error"] = "focus: %s" % type(exc).__name__
    with _LOCK:
        asked = list(_REQUESTED)
    wanted = []
    for s in list(symbols) + asked:
        if s and s not in wanted:
            wanted.append(s)
    wanted = wanted[:MAX_SYMBOLS]
    boot = 0
    for s in wanted:
        if not _due(s, at):
            continue
        with _LOCK:
            _ATTEMPT[s] = at
        try:
            got = await asyncio.to_thread(_bootstrap_one, client, s, bootstrap)
            _STATE["refdata_reads"] += 1
        except Exception as exc:                              # noqa: BLE001
            _STATE["refdata_failures"] += 1
            _STATE["last_error"] = "refdata: %s" % type(exc).__name__
            continue
        rec = (got or {}).get("record")
        if rec is None:
            continue
        with _LOCK:
            REFDATA[s] = {"record": rec, "at": at}
        IS.set_instrument(s, rec)
        boot += 1
    with _LOCK:
        priced = [s for s in wanted if s in REFDATA]
    IS.want(priced)
    return {"wanted": len(wanted), "bootstrapped": boot,
            "subscribed": len(priced)}


async def _run(get_pool, *, client, focus, bootstrap) -> None:
    last_focus, focus_cache = 0.0, []
    while True:
        try:
            if time.monotonic() - last_focus >= FOCUS_EVERY_S:
                last_focus = time.monotonic()
                try:
                    focus_cache = await _focus_symbols(get_pool, focus)
                except Exception as exc:                      # noqa: BLE001
                    _STATE["last_error"] = "focus: %s" % type(exc).__name__
                await refresh_once(client=client, bootstrap=bootstrap,
                                   symbols=focus_cache)
            elif pending():
                await refresh_once(client=client, bootstrap=bootstrap,
                                   symbols=focus_cache)
            await asyncio.sleep(LOOP_S)
        except asyncio.CancelledError:
            raise
        except Exception as exc:                              # noqa: BLE001
            _STATE["last_error"] = type(exc).__name__
            await asyncio.sleep(BOOTSTRAP_BACKOFF_S)
