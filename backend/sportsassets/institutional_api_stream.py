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

  H   THE ASKED-SYMBOL HAND-OFF (RC6.3c, PMX-1; the ASKED_HANDOFF_* block
      below). The symbols consumers ask about are written, bounded, to the
      plane's own key/value state so the plane's refdata priority pull takes
      them first; the records the plane persists are taken back here on the
      same beat, so identity is bootstrapped from the plane's record without
      a venue call. Rule (a) of paper_pmx_books is not relaxed: identity is
      still institutional_contract_map over the record, and a symbol with no
      plane record stays unproven (its consumer reads REST exactly as today).

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
from . import institutional_focus_universe as FU
from . import institutional_stream as IS

log = logging.getLogger(__name__)

VERSION = "INSTITUTIONAL_API_STREAM_V1"
#: Symbols this process will bootstrap and subscribe (focus universe +
#: asked); the focus universe's own bound (FU.MAX_MEMBERS) equals it.
MAX_SYMBOLS = 32
#: HELD PAPER MARKETS BEYOND THE FOCUS BOUND (P1 institutional primary). The
#: held-mark refresh names every held market (FU.held_first, due first); those
#: not already in the focus universe are bootstrapped and subscribed too, up
#: to this many, so held positions can be marked from the stream. Bounded by
#: the stream's own per-process limit: MAX_SYMBOLS + HELD_SYMBOL_BUDGET =
#: institutional_stream.MAX_SYMBOLS (200), itself far inside the venue's
#: documented 1,000 symbols per stream (/streaming-endpoints/market-data-
#: stream; institutional_stream.MAX_SYMBOLS's own note). One subscribe is ONE
#: client message carrying many symbols, so the documented 100 msg/s per firm
#: is not approached. A test pins the sum.
HELD_SYMBOL_BUDGET = 168
#: Refdata reads per pass (paced READ_PACING_S each): a large held set is
#: bootstrapped over consecutive LOOP_S passes instead of one long pass.
BOOTSTRAPS_PER_PASS = 24
SERVICE = "sportsassets-api"
#: The workers' cadences, reused.
LOOP_S = 2.0
FOCUS_EVERY_S = 60.0
REFDATA_REFRESH_S = 3600.0
READ_PACING_S = 0.15
BOOTSTRAP_BACKOFF_S = 30.0
#: A symbol whose refdata read failed or was unlisted is retried no sooner.
RETRY_UNLISTED_S = 300.0
#: One INFO line with the live stream digest this often (the stream's steady
#: state was never in the log: only the boot line, by construction IDLE).
DIGEST_LOG_EVERY_S = 60.0
#: The SUBSCRIPTION MODE of this process's stream: an explicit, bounded
#: held + priority symbol list. Subscribe-all (symbols=[]) is the dedicated
#: market plane's alone (institutional_stream.MODE_SUBSCRIBE_ALL).
SUBSCRIPTION_MODE = "EXPLICIT_SYMBOL_LIST"   # = IS.MODE_EXPLICIT (pinned)

#: ── THE DECISION PATH'S ASKED SYMBOLS (RC5, PMX consumer books) ─────────
#:
#: THE DEFECT. `request` recorded every symbol the decision path asked about
#: into a set that was NEVER pruned and refused new entries at MAX_SYMBOLS
#: (32): the first 32 distinct slugs asked after a boot held the slots for
#: the process's life, so every later candidate -- each new game -- was
#: never bootstrapped or subscribed. And the asked symbols shared only the
#: focus universe's 32 slots (production RC4: focus 30 -> 2 slots), while
#: the held budget's 168 slots stood 165 empty (held_wanted 3). So the PMX
#: books a consumer could use (paper_pmx_books) were never the books it
#: asked for.
#:
#: NOW. The asked set keeps the most recently asked MAX_REQUESTED symbols
#: (the least recently asked leaves first; one idle REQUEST_IDLE_S leaves
#: on the next pass), every consumer read refreshes its instant
#: (paper_pmx_books), and the asked symbols that do not fit the focus bound
#: fill the stream's SPARE capacity AFTER the held markets: held first,
#: always; the per-process total stays STREAM_MAX_SYMBOLS (200, far inside
#: the venue's documented 1,000 per stream). Nothing is unsubscribed
#: (OUTBOUND_COMMANDS); an entry nobody asks for ages out of the books by
#: RETAIN_IDLE_S as before.
#: = institutional_stream.MAX_SYMBOLS (a test pins it)
STREAM_MAX_SYMBOLS = 200
MAX_REQUESTED = STREAM_MAX_SYMBOLS
#: = institutional_stream.RETAIN_IDLE_S (a test pins it)
REQUEST_IDLE_S = 1800.0

#: ── REFDATA THE DEDICATED PLANE ALREADY HOLDS (RC5) ──────────────────────
#:
#: Each symbol's refdata was a separate POST /v1/refdata/instruments read
#: from this process (refdata_reads 138 since boot, production RC4
#: readback 06:39Z), on an endpoint the venue caps at 6 calls a minute
#: (market_plane.refdata_universe) and the plane already spends 5 of. The
#: plane persists the venue's own record for every registry member
#: (market_plane_registry.refdata, refdata_at; the same endpoint, the same
#: exact-symbol selection, refdata_progress.instrument_response). So:
#:   * a focus / held / first-32 asked symbol takes the plane's record only
#:     when it was persisted within REFDATA_REFRESH_S (this process's own
#:     refresh interval, never longer); otherwise its REST read happens
#:     exactly as before;
#:   * a SPARE-capacity asked symbol takes the plane's record ONLY -- one
#:     persisted within PLANE_REFDATA_MAX_AGE_S (the plane's own cache life,
#:     venue guidance: pull once, cache, follow changes incrementally) -- and
#:     is NEVER a REST read: the new capacity adds no load on the capped
#:     endpoint. A stale, dead or restarting plane simply leaves it
#:     unsubscribed (re-asked after RETRY_UNLISTED_S).
#: Identity is still proven only by institutional_contract_map on the
#: record, and the instrument's live state is the stream's own.
#: = market_plane.refdata_universe.FULL_REFRESH_S (a test pins it)
PLANE_REFDATA_MAX_AGE_S = 24 * 3600.0
PLANE_REFDATA_SQL = """
    SELECT contract_id, refdata, extract(epoch FROM refdata_at) AS at
      FROM market_plane_registry
     WHERE venue = 'POLYMARKET_US' AND contract_id = ANY($1::text[])
       AND refdata IS NOT NULL
       AND coalesce(refdata->>'unlisted', 'false') <> 'true'
       AND refdata_at > now() - make_interval(secs => $2)
"""

#: ── THE ASKED-SYMBOL HAND-OFF TO THE PLANE (RC6.3c, PMX-1) ──────────────
#:
#: PRODUCTION (RC6.3b approved-judge packet, pm-acceptance ec8b892d,
#: completion.json market_data.pmx_primary.consumer_reads, scope
#: PROCESS_SINCE_IMPORT): 14,224 consumer reads in the deciding process, 5
#: served by the PMX book (pmx_share 0.0004). The second fallback reason,
#: PMX_IDENTITY_NOT_PROVEN_EXACT 4,752, is THIS PROCESS HOLDING NO REFDATA
#: for the symbol a consumer asked about. A spare-capacity asked symbol
#: takes the plane's persisted record only (above), and the plane's
#: priority pull knew nothing of the ask: it read refdata first for the
#: registry's held / EVALUATED_CANDIDATE / imminent contracts
#: (market_plane.populate names a candidate from ext_candidate_outcomes
#: AFTER the collector's cycle, and only the money line that row records),
#: so the line contracts the collector asks `venue_quote` about, and every
#: asked symbol the registry held at venue-activity priority, waited for the
#: full universe pull -- TRUNCATED at MAX_PULL_S on every attempt in the
#: packet (refdata_pending 29,352 of 75,415 active contracts, coverage
#: 0.61). The ask never became a record, the record never became identity,
#: and the consumer read REST.
#:
#: NOW. The asked set is HANDED TO THE PLANE. `handoff_asked` (the stream
#: task's beat, at most every ASKED_HANDOFF_EVERY_S) writes every asked
#: symbol -- its ask instant, its asker, whether this process holds its
#: refdata -- as ONE bounded row, ingestion_state[ASKED_HANDOFF_KEY] (the
#: key/value table every loop's state lives in; at most MAX_REQUESTED
#: symbols, upserted, rewritten when it changes and every
#: ASKED_HANDOFF_REFRESH_S so the plane can bound its age). The plane's
#: refdata slot puts the asked symbols FIRST in its PRIORITY by-symbol read
#: (market_plane.registry.refdata_pending_split: asked, then
#: EVALUATED_CANDIDATE, then held and imminent), still under its 6 calls a
#: minute cap. On the same beat this process takes, for every asked symbol
#: it holds no refdata for, the record the plane has since persisted --
#: within REFDATA_REFRESH_S, the bound a core symbol's pass already honours,
#: so nothing a pass would refuse is taken -- and the next pass subscribes
#: it: identity is bootstrapped from the plane's record WITHOUT A VENUE CALL.
#: What is unchanged: identity is proven only by institutional_contract_map
#: on the record (paper_pmx_books rule (a)); a symbol with no plane record
#: stays unproven and its consumer reads REST exactly as before; the pass's
#: own plane read, REST bootstrap, budgets and bounds. The share of asks
#: this process could answer with refdata at ask time is published
#: (`asked_refdata_report`, carried by paper_pmx_books.telemetry into the
#: held-mark run record and completion.json).
ASKED_HANDOFF_VERSION = "PMX_ASKED_HANDOFF_V1"
#: = market_plane.registry.ASKED_HANDOFF_KEY (a test pins it)
ASKED_HANDOFF_KEY = "pmx_asked_symbols"
#: the hand-off beat: one row written (when changed), one bounded SELECT of
#: the plane's records for the asked symbols without refdata
ASKED_HANDOFF_EVERY_S = 10.0
#: an unchanged hand-off is rewritten this often (the plane bounds the age)
ASKED_HANDOFF_REFRESH_S = 60.0
ASKED_HANDOFF_SQL = """
    INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb)
    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
"""

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
#: symbol -> when a SPARE-capacity ask last found no plane record. Kept
#: apart from _ATTEMPT so a symbol that later fits the core set is never
#: held back from its REST bootstrap by a plane-only miss.
_PLANE_TRIED: dict = {}
#: symbol -> the consumer that last asked (paper_pmx_books names them)
_ASKED_BY: dict = {}
#: every identity question answered here since import: with a record held
#: at ask time, or without (asked_refdata_report)
_ASKS: dict = {"total": 0, "with_refdata": 0}
#: the hand-off beat's own record (handoff_asked)
_HANDOFF: dict = {"at": None, "written_at": None, "digest": None,
                  "symbols": 0, "without_refdata": 0, "installed": 0,
                  "writes": 0, "error": None}
_STATE: dict = {"task": None, "start": None, "last_error": None,
                "refdata_reads": 0, "refdata_failures": 0, "backlog": 0,
                "held_wanted": 0, "held_subscribed": 0,
                "held_preempted": 0, "refdata_from_plane": 0,
                "asked_spare": 0, "asked_from_plane": 0}
#: The last focus universe computed here (members without identity).
_UNIVERSE: dict = {}


def running() -> bool:
    return _STATE.get("task") is not None and IS.BOOKS.state in IS.RUNNING


def _leg(order_intent) -> str | None:
    s = str(order_intent or "").strip().upper()
    if s in LONG_INTENTS:
        return "yes"
    return None


def request(symbol, *, now=None, consumer=None) -> None:
    """The decision path asked about this symbol. Recorded only while the
    stream runs here; never a venue call. Every ask refreshes its instant;
    a full set gives up its LEAST RECENTLY asked symbol, never the new ask
    (see MAX_REQUESTED). `consumer` (paper_pmx_books names them) is kept
    beside the symbol for the hand-off and the readback."""
    s = str(symbol or "").strip()
    if not s or not running():
        return
    at = float(time.time() if now is None else now)
    with _LOCK:
        if s in _REQUESTED:
            _REQUESTED.pop(s)
        elif len(_REQUESTED) >= MAX_REQUESTED:
            oldest = min(_REQUESTED, key=_REQUESTED.get)
            _REQUESTED.pop(oldest, None)
            _ASKED_BY.pop(oldest, None)
        _REQUESTED[s] = at
        if consumer:
            _ASKED_BY[s] = str(consumer)


def asked_symbols(*, now=None) -> list:
    """The asked symbols, most recently asked first; one not asked for
    REQUEST_IDLE_S is dropped here."""
    at = float(time.time() if now is None else now)
    with _LOCK:
        for s in [s for s, t in _REQUESTED.items()
                  if at - float(t) > REQUEST_IDLE_S]:
            _REQUESTED.pop(s, None)
            _ASKED_BY.pop(s, None)
        return sorted(_REQUESTED, key=lambda s: -float(_REQUESTED[s]))


def identity_mapper(slug, order_intent=None) -> dict | None:
    """C1: EXACT or None. Pure over the refdata this process holds. Every
    answer is counted as an ask with or without a record held at that
    instant (asked_refdata_report)."""
    s = str(slug or "").strip()
    leg = _leg(order_intent)
    if not s or leg is None:
        return None
    with _LOCK:
        rec = (REFDATA.get(s) or {}).get("record")
        _ASKS["total"] += 1
        if rec is not None:
            _ASKS["with_refdata"] += 1
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


def primary_report(*, now=None) -> dict:
    """PMX gRPC PRIMARY, AS THIS PROCESS HOLDS IT (the deciding process: the
    decision path and the held-mark refresh read these books). Plain data
    for readbacks; never raises. `requested` symbols, venue `acked` on the
    current connection, RESIDENT L2 BOOKS current under the decision and the
    held-mark bounds, their ages, and the held-market coverage."""
    try:
        d = IS.BOOKS.digest(now=now)
        t = IS._TRANSPORT
    except Exception as exc:                                  # noqa: BLE001
        return {"version": VERSION, "error": type(exc).__name__}
    return {
        "version": VERSION, "process": SERVICE,
        "subscription_mode": getattr(t, "subscription_mode", None)
        or SUBSCRIPTION_MODE,
        "target": d.get("target"),
        "state": d.get("state"), "why": d.get("why"),
        "connected": d.get("connected"),
        "connection_seq": d.get("connection_seq"),
        "requested": d.get("symbols"), "acked": d.get("acked"),
        "refused_symbols": d.get("refused_symbols"),
        "current_l2_books": d.get("current_books"),
        "held_mark_current_l2_books": d.get("held_mark_current_books"),
        "book_age_s": d.get("book_age_s"),
        "venue_receipt_lag_s": d.get("venue_receipt_lag_s"),
        "dropped_at_cap": d.get("dropped_at_cap"),
        "evicted": d.get("evicted"),
        "held_preempted": _STATE.get("held_preempted"),
        "by_refusal": d.get("by_refusal"),
        "held_wanted": _STATE.get("held_wanted"),
        "held_subscribed": _STATE.get("held_subscribed"),
        "refdata_reads": _STATE.get("refdata_reads"),
        "refdata_failures": _STATE.get("refdata_failures"),
        # refdata taken from the plane's persisted record instead of a read
        "refdata_from_plane": _STATE.get("refdata_from_plane"),
        # asked symbols subscribed in the spare capacity after held markets
        "asked_spare": _STATE.get("asked_spare"),
        "bootstrap_backlog": _STATE.get("backlog"),
        "last_error": _STATE.get("last_error"),
        # (RC6.3c) the share of asks answered with refdata held, the asked
        # set's refdata coverage now, and the hand-off to the plane
        "asked_refdata": asked_refdata_report(now=now),
        "at": d.get("at")}


def log_digest() -> None:
    """One INFO line: the live stream state (never the boot record)."""
    r = primary_report()
    log.info("institutional_api_stream: state=%s connected=%s requested=%s "
             "acked=%s current_l2=%s held_mark_current_l2=%s held_wanted=%s "
             "held_subscribed=%s refdata_reads=%s refdata_failures=%s "
             "dropped_at_cap=%s last_error=%s", r.get("state"),
             r.get("connected"), r.get("requested"), r.get("acked"),
             r.get("current_l2_books"), r.get("held_mark_current_l2_books"),
             r.get("held_wanted"), r.get("held_subscribed"),
             r.get("refdata_reads"), r.get("refdata_failures"),
             r.get("dropped_at_cap"), r.get("last_error"))


def describe() -> dict:
    with _LOCK:
        held = sorted(REFDATA)
        asked = list(_REQUESTED)
    return {"version": VERSION, "running": running(),
            "stream_state": IS.BOOKS.state,
            "start": _STATE.get("start"), "refdata_symbols": held,
            "held_symbol_budget": HELD_SYMBOL_BUDGET,
            "held_wanted": _STATE.get("held_wanted"),
            "held_subscribed": _STATE.get("held_subscribed"),
            "bootstrap_backlog": _STATE.get("backlog"),
            "requested": asked[:MAX_REQUESTED],
            "refdata_reads": _STATE.get("refdata_reads"),
            "refdata_failures": _STATE.get("refdata_failures"),
            "refdata_from_plane": _STATE.get("refdata_from_plane"),
            "asked_spare": _STATE.get("asked_spare"),
            "last_error": _STATE.get("last_error"),
            "asked_refdata": asked_refdata_report(),
            "focus_universe": FU.summary(universe_snapshot())}


def asked_refdata_report(*, now=None) -> dict:
    """THE SHARE OF ASKED SYMBOLS WITH REFDATA AT ASK TIME (RC6.3c readback;
    plain data, never raises). Two measures: by ASK -- every identity
    question this process answered since import, with a record held at that
    instant or without -- and by SYMBOL -- the asked set now (inside
    REQUEST_IDLE_S), how many this process holds refdata for, how many of
    those from the plane's record, by consumer. With the hand-off's own
    record: when it was last written, what it carried, the records it
    installed, its last error."""
    at = float(time.time() if now is None else now)
    try:
        with _LOCK:
            asked = [s for s, t in _REQUESTED.items()
                     if at - float(t) <= REQUEST_IDLE_S]
            held = [s for s in asked if s in REFDATA]
            plane = [s for s in held
                     if REFDATA[s].get("source") == "MARKET_PLANE_REGISTRY"]
            total, hit = int(_ASKS["total"]), int(_ASKS["with_refdata"])
            by_consumer: dict = {}
            for s in asked:
                d = by_consumer.setdefault(_ASKED_BY.get(s) or "UNNAMED",
                                           {"asked": 0, "with_refdata": 0})
                d["asked"] += 1
                d["with_refdata"] += int(s in REFDATA)
            h = dict(_HANDOFF)
        n = len(asked)
        written = h.get("written_at")
        return {
            "version": ASKED_HANDOFF_VERSION, "scope": "PROCESS_SINCE_IMPORT",
            "asks": {"total": total, "with_refdata": hit,
                     "share": (round(hit / total, 4) if total else None)},
            "symbols": {"asked": n, "with_refdata": len(held),
                        "from_plane": len(plane),
                        "share": (round(len(held) / n, 4) if n else None),
                        "by_consumer": by_consumer},
            "handoff": {"key": ASKED_HANDOFF_KEY,
                        "every_s": ASKED_HANDOFF_EVERY_S,
                        "written_at": written,
                        "age_s": (None if written is None
                                  else round(at - float(written), 1)),
                        "writes": h.get("writes"),
                        "symbols": h.get("symbols"),
                        "without_refdata": h.get("without_refdata"),
                        "installed_last_beat": h.get("installed"),
                        "installed_total": _STATE.get("asked_from_plane"),
                        "error": h.get("error")}}
    except Exception as exc:                                  # noqa: BLE001
        return {"version": ASKED_HANDOFF_VERSION, "error": type(exc).__name__}


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
        _PLANE_TRIED.clear()
        _UNIVERSE.clear()
        _ASKED_BY.clear()
        _ASKS.update(total=0, with_refdata=0)
        _HANDOFF.update(at=None, written_at=None, digest=None, symbols=0,
                        without_refdata=0, installed=0, writes=0, error=None)
    _STATE.update(task=None, start=None, last_error=None, refdata_reads=0,
                  refdata_failures=0, backlog=0, held_wanted=0,
                  held_subscribed=0, held_preempted=0, refdata_from_plane=0,
                  asked_spare=0, asked_from_plane=0)


async def _focus_symbols(get_pool, focus) -> list:
    if focus is not None:
        got = focus() if callable(focus) else focus
        if asyncio.iscoroutine(got):
            got = await got
        return [str(s) for s in got or ()]
    if get_pool is None:
        return []
    # THE FOCUS UNIVERSE (institutional_focus_universe): actual positions,
    # live / imminent execution intents, paper investment positions, V3
    # candidates, the mapped investment universe, exploration (diagnostic
    # only), then the experimental lane's focus set -- bounded by
    # FU.MAX_MEMBERS (= MAX_SYMBOLS here). Only EXACT members are subscribed
    # (refresh_once wants only symbols whose refdata maps exactly).
    from . import shadow_experimental_store as xstore
    from .workers import institutional_md as W
    pool = await get_pool()
    try:
        discovery = [r["symbol"] for r in await xstore.focus_set(
            pool, size=W.MAX_INSTRUMENTS)]
    except Exception:                                         # noqa: BLE001
        discovery = []
    # HELD PAPER MARKETS FIRST (P0 market-data freshness): the held-mark
    # refresh in this process names every held market, due first; those keep
    # FU.HELD_PAPER_RESERVE slots after the real-money tiers, so their exact
    # symbols are subscribed and can mark held positions from the stream.
    # (FU.held_first is a plain process-local list the paper owner fills;
    # this module imports nothing from the paper path.)
    u = await FU.compute(pool, discovery=discovery, limit=MAX_SYMBOLS,
                         held_first=FU.held_first())
    with _LOCK:
        _UNIVERSE.clear()
        _UNIVERSE.update(u)
    return [m["retail_slug"] for m in u["members"]]


def _exact_here(symbol) -> bool:
    """The refdata this process holds maps the slug EXACTLY (YES / long)."""
    with _LOCK:
        rec = (REFDATA.get(symbol) or {}).get("record")
    if rec is None:
        return False
    try:
        m = ICM.map_retail_to_institutional(symbol, "yes", rec)
    except Exception:                                         # noqa: BLE001
        return False
    return bool(m.get("ok")) and m.get("institutional_symbol") == symbol


def universe_snapshot() -> dict:
    """The last focus universe with each member's identity as THIS process
    holds it (EXACT on its refdata, or UNAVAILABLE with the reason)."""
    with _LOCK:
        u = {k: (list(v) if isinstance(v, list) else v)
             for k, v in _UNIVERSE.items()}
        held = {s: (REFDATA.get(s) or {}).get("record") for s in REFDATA}
        tried = set(_ATTEMPT)
    if not u.get("members"):
        return {}
    u["members"] = [dict(m) for m in u["members"]]
    return FU.attach(u, record_for=held.get,
                     attempted=lambda s: s in tried or s in held)


async def persist_universe(get_pool, *, process_id=None) -> int:
    """This process's focus-universe snapshot -> institutional_focus_universe
    (migration 213). Evidence only; never raises."""
    if get_pool is None:
        return 0
    try:
        u = universe_snapshot()
        if not u:
            return 0
        pool = await get_pool()
        return await FU.persist(
            pool, u, process_id=process_id or "%s:%s" % (
                VERSION, os.getpid()), service=SERVICE,
            wanted=IS.BOOKS.wanted())
    except Exception:                                         # noqa: BLE001
        return 0


def _bootstrap_one(client, symbol, bootstrap):
    """The workers' own refdata read (paced), run off the loop."""
    if bootstrap is not None:
        return bootstrap(client, symbol)
    from .venue_pace import pace
    from .workers import institutional_md as W
    pace(READ_PACING_S)
    return W.bootstrap_instrument(client, symbol)


def _due(symbol, at, *, plane_only: bool = False) -> bool:
    """`plane_only`: a spare-capacity symbol, also held back for
    RETRY_UNLISTED_S after the plane had no record for it."""
    with _LOCK:
        have = REFDATA.get(symbol)
        tried = _ATTEMPT.get(symbol)
        if plane_only and _PLANE_TRIED.get(symbol) is not None:
            tried = max(float(tried or 0.0), float(_PLANE_TRIED[symbol]))
    if have is not None:
        return at - have["at"] >= REFDATA_REFRESH_S
    return tried is None or at - tried >= RETRY_UNLISTED_S


def pending(now=None) -> list:
    """Asked symbols whose refdata is due (drives an early pass)."""
    at = float(now if now is not None else time.time())
    with _LOCK:
        asked = list(_REQUESTED)
    return [s for s in asked if _due(s, at, plane_only=True)]


def _held_extra(already) -> list:
    """The held markets the refresh named (due first) that the focus / asked
    set does not already carry, bounded by HELD_SYMBOL_BUDGET."""
    seen = set(already)
    out = []
    for s in FU.held_first():
        if s and s not in seen:
            seen.add(s)
            out.append(s)
            if len(out) >= HELD_SYMBOL_BUDGET:
                break
    return out


async def plane_refdata(get_pool, symbols, *,
                        max_age_s: float = PLANE_REFDATA_MAX_AGE_S) -> dict:
    """{symbol: {"record", "at"}} for THESE symbols from the dedicated
    plane's persisted refdata (PLANE_REFDATA_SQL): only records persisted
    within `max_age_s`, listed, and naming exactly the symbol. {} on any
    failure (the REST bootstrap then reads, exactly as before). Read only;
    never raises."""
    syms = sorted({str(s) for s in symbols or () if s})
    if get_pool is None or not syms:
        return {}
    try:
        import json
        pool = await get_pool()
        async with pool.acquire() as c:
            if not await c.fetchval(
                    "SELECT to_regclass('market_plane_registry') IS NOT NULL"):
                return {}
            rows = await c.fetch(PLANE_REFDATA_SQL, syms, float(max_age_s))
        out = {}
        for r in rows:
            rec = r["refdata"]
            if isinstance(rec, str):
                rec = json.loads(rec)
            if isinstance(rec, dict) and rec.get("symbol") == \
                    r["contract_id"] and r["at"] is not None:
                out[r["contract_id"]] = {"record": rec, "at": float(r["at"])}
        return out
    except Exception:                                         # noqa: BLE001
        return {}


def _take_plane_record(symbol, got, at, *, limit, own_age: bool) -> bool:
    """Install the plane's persisted record for `symbol` -- ONLY one naming
    exactly this symbol, persisted within `limit` seconds of `at` -- and
    hand it to the stream (set_instrument). False when nothing is installed.
    `own_age`: the entry keeps the record's own persist instant (re-asked
    when THE PLANE's record turns REFDATA_REFRESH_S: a core symbol); else
    this instant (a spare symbol, re-checked against the plane every
    REFDATA_REFRESH_S). Identity is still institutional_contract_map over
    the record: nothing here proves anything."""
    g = got or {}
    rec = g.get("record")
    try:
        rec_at = float(g.get("at"))
    except (TypeError, ValueError):
        return False
    if not isinstance(rec, dict) or rec.get("symbol") != symbol or \
            not (0.0 <= at - rec_at < float(limit)):
        return False
    with _LOCK:
        REFDATA[symbol] = {"record": rec, "at": rec_at if own_age else at,
                           "source": "MARKET_PLANE_REGISTRY",
                           "plane_refdata_at": rec_at}
        _PLANE_TRIED.pop(symbol, None)
    IS.set_instrument(symbol, rec)
    _STATE["refdata_from_plane"] = int(
        _STATE.get("refdata_from_plane") or 0) + 1
    return True


def _handoff_payload(at: float) -> dict:
    """The asked set as the plane reads it (ASKED_HANDOFF_KEY): most
    recently asked first, at most MAX_REQUESTED, each with its ask instant,
    its asker and whether THIS process holds its refdata now."""
    with _LOCK:
        rows = []
        for s, t in sorted(_REQUESTED.items(), key=lambda kv: -float(kv[1])):
            if at - float(t) > REQUEST_IDLE_S:
                continue
            have = REFDATA.get(s)
            rows.append({"symbol": s, "asked_at": round(float(t), 3),
                         "consumer": _ASKED_BY.get(s),
                         "refdata": have is not None,
                         "source": ((have or {}).get("source")
                                    or ("REST_BOOTSTRAP" if have else None))})
    rows = rows[:MAX_REQUESTED]
    return {"version": ASKED_HANDOFF_VERSION,
            "process": "%s:%s" % (SERVICE, os.getpid()), "at": at,
            "idle_s": REQUEST_IDLE_S, "symbols": rows, "asked": len(rows),
            "without_refdata": sum(1 for r in rows if not r["refdata"])}


async def handoff_asked(get_pool, *, now=None, plane_records=None,
                        force: bool = False) -> dict:
    """THE HAND-OFF BEAT (see ASKED_HANDOFF_KEY). At most every
    ASKED_HANDOFF_EVERY_S while the stream runs here: (1) the asked set is
    written to ingestion_state[ASKED_HANDOFF_KEY] when it changed since the
    last write (or every ASKED_HANDOFF_REFRESH_S), so the plane's refdata
    slot puts these symbols first; (2) for every asked symbol this process
    holds NO refdata for, the plane's record persisted within
    REFDATA_REFRESH_S is taken (`plane_records`, default `plane_refdata` at
    that bound) -- never a venue call, never a record a pass would refuse.
    A record installed here is subscribed by the next pass (the caller runs
    one). With no plane record nothing changes: the symbol stays unproven,
    its consumer reads REST. NEVER RAISES; a failed write or read is named
    in the report and tried again on the next beat."""
    at = float(time.time() if now is None else now)
    if get_pool is None and plane_records is None:
        return {"skipped": "NO_POOL"}
    if not force and not running():
        return {"skipped": "NOT_RUNNING"}
    last = _HANDOFF.get("at")
    if not force and last is not None and \
            at - float(last) < ASKED_HANDOFF_EVERY_S:
        return {"skipped": "NOT_DUE"}
    _HANDOFF["at"] = at
    payload = _handoff_payload(at)
    digest = tuple((r["symbol"], r["refdata"]) for r in payload["symbols"])
    written_at = _HANDOFF.get("written_at")
    written = False
    if get_pool is not None and (
            digest != _HANDOFF.get("digest") or written_at is None
            or at - float(written_at) >= ASKED_HANDOFF_REFRESH_S):
        try:
            import json
            pool = await get_pool()
            async with pool.acquire() as c:
                if await c.fetchval(
                        "SELECT to_regclass('ingestion_state') IS NOT NULL"):
                    await c.execute(ASKED_HANDOFF_SQL, ASKED_HANDOFF_KEY,
                                    json.dumps(payload, default=str))
                    written = True
            if written:
                _HANDOFF.update(written_at=at, digest=digest, error=None,
                                writes=int(_HANDOFF.get("writes") or 0) + 1,
                                symbols=payload["asked"],
                                without_refdata=payload["without_refdata"])
            else:
                _HANDOFF["error"] = "write: INGESTION_STATE_ABSENT"
        except Exception as exc:                              # noqa: BLE001
            _HANDOFF["error"] = "write: %s" % type(exc).__name__
    # THE PLANE'S RECORDS FOR THE ASKED SYMBOLS THIS PROCESS HOLDS NONE FOR:
    # the pass's own bound for a core symbol (REFDATA_REFRESH_S), so the
    # entry keeps the record's own age and every later pass treats it
    # exactly as one the pass installed
    missing = [r["symbol"] for r in payload["symbols"] if not r["refdata"]]
    installed = 0
    if missing:
        try:
            if plane_records is not None:
                got = await plane_records(missing)
            else:
                got = await plane_refdata(get_pool, missing,
                                          max_age_s=REFDATA_REFRESH_S)
            got = got or {}
        except Exception as exc:                              # noqa: BLE001
            got = {}
            _HANDOFF["error"] = "plane: %s" % type(exc).__name__
        for s in missing:
            if _take_plane_record(s, got.get(s), at, limit=REFDATA_REFRESH_S,
                                  own_age=True):
                installed += 1
    _HANDOFF["installed"] = installed
    _STATE["asked_from_plane"] = int(
        _STATE.get("asked_from_plane") or 0) + installed
    return {"written": written, "asked": payload["asked"],
            "without_refdata": len(missing), "installed": installed,
            "error": _HANDOFF.get("error")}


async def refresh_once(get_pool=None, *, client=None, focus=None,
                       bootstrap=None, now=None, symbols=None,
                       held=None, max_bootstraps=None,
                       plane_records=None) -> dict:
    """ONE pass: focus set + asked symbols (<= MAX_SYMBOLS), then the held
    markets beyond them (<= HELD_SYMBOL_BUDGET), then the asked symbols that
    did not fit into the stream's spare capacity (<= STREAM_MAX_SYMBOLS)
    -> refdata (only when due: never held, older than REFDATA_REFRESH_S, or a
    failed / unlisted symbol after RETRY_UNLISTED_S; the plane's persisted
    record first when `plane_records` (async symbols -> {symbol: {record,
    at}}) has one inside REFDATA_REFRESH_S, else a REST read, at most
    `max_bootstraps` per pass, the rest is the backlog the next pass takes)
    -> set_instrument -> want."""
    at = float(now if now is not None else time.time())
    if symbols is None:
        try:
            symbols = await _focus_symbols(get_pool, focus)
        except Exception as exc:                              # noqa: BLE001
            symbols = []
            _STATE["last_error"] = "focus: %s" % type(exc).__name__
    asked = asked_symbols(now=at)
    wanted = []
    for s in list(symbols) + asked:
        if s and s not in wanted:
            wanted.append(s)
    wanted = wanted[:MAX_SYMBOLS]
    held_names = list(FU.held_first() if held is None else held)
    extra = (_held_extra(wanted) if held is None
             else [s for s in held_names if s and s not in wanted]
             [:HELD_SYMBOL_BUDGET])
    held_set = set(held_names)
    wanted = wanted + extra
    # THE ASKED SYMBOLS THAT DID NOT FIT, into the spare capacity AFTER the
    # held markets (see MAX_REQUESTED): most recently asked first
    room = max(0, STREAM_MAX_SYMBOLS - len(wanted))
    spare = [s for s in asked if s and s not in wanted][:room]
    spare_set = set(spare)
    wanted = wanted + spare
    # THE PLANE'S PERSISTED REFDATA FIRST (PLANE_REFDATA_SQL): a due symbol
    # it holds a record for -- within REFDATA_REFRESH_S, or for a spare
    # symbol within PLANE_REFDATA_MAX_AGE_S -- needs no REST read
    with _LOCK:
        for s in [s for s, t in _PLANE_TRIED.items()
                  if at - float(t) >= RETRY_UNLISTED_S]:
            _PLANE_TRIED.pop(s, None)        # bounded: only live retries
    due = [s for s in wanted if _due(s, at, plane_only=s in spare_set)]
    got_plane = {}
    if due and plane_records is not None:
        try:
            got_plane = await plane_records(due) or {}
        except Exception:                                     # noqa: BLE001
            got_plane = {}
    for s in due:
        spare_only = s in spare_set
        # a core / held symbol keeps the record's own age (re-asked when the
        # PLANE's record turns REFDATA_REFRESH_S); a spare symbol is
        # re-checked against the plane every REFDATA_REFRESH_S
        if _take_plane_record(
                s, got_plane.get(s), at, own_age=not spare_only,
                limit=(PLANE_REFDATA_MAX_AGE_S if spare_only
                       else REFDATA_REFRESH_S)):
            continue
        if spare_only:
            # NEVER a REST read for spare capacity: re-asked of the plane
            # after the retry interval, like an unlisted symbol (the hand-off
            # beat takes a record the plane persists meanwhile)
            with _LOCK:
                _PLANE_TRIED[s] = at
    cap = BOOTSTRAPS_PER_PASS if max_bootstraps is None else int(
        max_bootstraps)
    boot = 0
    attempted = 0
    backlog = 0
    for s in wanted:
        if s in spare_set:
            continue        # spare capacity: the plane's record only
        if not _due(s, at):
            continue
        if attempted >= cap:
            backlog += 1
            continue
        attempted += 1
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
        held = [s for s in wanted if s in REFDATA]
    # SUBSCRIBE ONLY WHAT MAPS EXACTLY: an UNAVAILABLE member (no record,
    # not listed, any mapping refusal) is never subscribed or priced.
    priced = [s for s in held if _exact_here(s)]
    # HELD MARKETS FIRST: the stream's per-process bound is spent on held
    # positions before candidates / discovery, and entries nobody has named
    # for RETAIN_IDLE_S are evicted first, so a newly held market is never
    # the one the bound refuses.
    priced = ([s for s in priced if s in held_set]
              + [s for s in priced if s not in held_set])
    IS.BOOKS.retain(wanted)
    IS.want(priced)
    # THE GUARANTEE, not a likelihood: `retain` frees only entries idle for
    # RETAIN_IDLE_S, so focus churn or the decision path's own wants inside
    # that window can still fill the bound. A held market the bound refused
    # takes the slot of an entry THIS pass does not want. `wanted` is at
    # most STREAM_MAX_SYMBOLS (MAX_SYMBOLS + HELD_SYMBOL_BUDGET, then the
    # spare capacity up to the same total), so it fits.
    have = set(IS.BOOKS.wanted())
    if any(s in held_set and s not in have for s in priced):
        _STATE["held_preempted"] = int(_STATE.get("held_preempted") or 0) \
            + len(IS.BOOKS.retain(wanted, idle_s=0.0))
        IS.want(priced)
    _STATE.update(backlog=backlog,
                  held_wanted=sum(1 for s in wanted if s in held_set),
                  held_subscribed=sum(1 for s in priced if s in held_set))
    _STATE["asked_spare"] = len(spare)
    return {"wanted": len(wanted), "bootstrapped": boot,
            "subscribed": len(priced), "held_extra": len(extra),
            "asked_spare": len(spare), "backlog": backlog}


async def _run(get_pool, *, client, focus, bootstrap) -> None:
    last_focus, focus_cache = 0.0, []
    last_log = None

    async def _plane(symbols):
        return await plane_refdata(get_pool, symbols)

    async def _plane_recent(symbols):
        # the hand-off beat's bound: a core symbol's own (REFDATA_REFRESH_S)
        return await plane_refdata(get_pool, symbols,
                                   max_age_s=REFDATA_REFRESH_S)
    while True:
        try:
            if last_log is None or \
                    time.monotonic() - last_log >= DIGEST_LOG_EVERY_S:
                last_log = time.monotonic()
                try:
                    log_digest()
                except Exception:                             # noqa: BLE001
                    pass
            if not last_focus or \
                    time.monotonic() - last_focus >= FOCUS_EVERY_S:
                last_focus = time.monotonic()
                try:
                    focus_cache = await _focus_symbols(get_pool, focus)
                except Exception as exc:                      # noqa: BLE001
                    _STATE["last_error"] = "focus: %s" % type(exc).__name__
                await refresh_once(client=client, bootstrap=bootstrap,
                                   symbols=focus_cache, plane_records=_plane)
                await persist_universe(get_pool)
            elif pending() or _STATE.get("backlog"):
                await refresh_once(client=client, bootstrap=bootstrap,
                                   symbols=focus_cache, plane_records=_plane)
            # THE ASKED-SYMBOL HAND-OFF BEAT (ASKED_HANDOFF_EVERY_S): the
            # asked set to the plane; the plane's fresh records for the asked
            # symbols this process holds none for; a record installed is
            # subscribed by a pass now, not at the next focus refresh
            beat = await handoff_asked(get_pool, plane_records=_plane_recent)
            if beat.get("installed"):
                await refresh_once(client=client, bootstrap=bootstrap,
                                   symbols=focus_cache, plane_records=_plane)
            await asyncio.sleep(LOOP_S)
        except asyncio.CancelledError:
            raise
        except Exception as exc:                              # noqa: BLE001
            _STATE["last_error"] = type(exc).__name__
            await asyncio.sleep(BOOTSTRAP_BACKOFF_S)
