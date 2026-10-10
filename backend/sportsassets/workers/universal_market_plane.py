"""Universal Market Plane supervisor (workers process, PARALLEL / SHADOW).

durable registry (from the complete venue catalogue) -> priority-ordered,
stable PMX shards -> canonical books -> coverage, certification, latency and
Radar, recorded append-only. No orders, no sizing, no authority.

PARALLEL FIRST (package APPLY.md): the legacy market-data lanes keep running
and keep serving every decision and held mark. This supervisor builds the
universal plane beside them and records its evidence so parity can be shown
before any reader is cut over.

INTEGRATION FIXES OVER THE DELIVERED SUPERVISOR (each pinned by a test):

  * the PMX streams are armed under the SAME conditions as the existing
    stream: INSTITUTIONAL_MD_STREAM on, the market-data identity guard passes
    (a refused credential opens no stream), the transport available, and the
    token-invalidation hook handed to every transport;
  * shard books are armed RUNNING (a fresh ResidentBooks refuses every read);
  * only contracts whose own refdata is held are subscribed (scales known),
    in priority order (open positions, candidates, core families soonest);
    an unlisted contract is recorded and re-asked after UNLISTED_RETRY_S, not
    every pass;
  * DEDICATED READ-ONLY RUNTIME ONLY (owner decision 2026-10-06; completion
    readiness 2026-10-07): this supervisor runs ONLY as its own read-only
    service, `python -m sportsassets.workers.universal_market_plane`
    (sportsassets-market-plane, ops/render_market_plane_service.yaml). The
    shared workers never start it, by source rule (workers/all.py
    DEDICATED_ONLY_LOOPS), whatever UNIVERSAL_MARKET_PLANE says there;
  * ONE SUBSCRIBE-ALL STREAM (venue guidance 2026-10-07, Polymarket
    representative): an empty symbol list subscribes one market-data stream
    to EVERY instrument and is the recommended architecture for a universe
    this size; the 20 concurrent gRPC streams are a FIRM-WIDE budget shared
    with orders, drop copy, positions, balances and RFQ. So the default is
    subscription_mode SUBSCRIBE_ALL_EMPTY_SYMBOL_LIST, exactly one market-data
    stream, sports filtered locally (the books hold the registry's
    refdata-listed contracts, bounded by UMP_SUBSCRIBE_ALL_MAX_BOOKS; every
    other instrument is counted, never held). UMP_SUBSCRIBE_ALL=off falls
    back to explicit 1,000-symbol shards -- a fallback / debug mode, never
    more than EXPLICIT_MAX_STREAMS_CEILING streams;
  * the bearer token (180 s) is refreshed in the background
    (market_plane.token_keeper) and read only on the next connect: a healthy
    stream is never cycled to re-authenticate;
  * reference data is ONE bounded, cached full ListInstruments pull plus
    batched by-symbol reads for priority contracts and new listings
    (market_plane.refdata_universe): at most 6 calls a minute, priority
    first, COMPLETE only on the venue's own end of pagination, never
    repolled inside FULL_REFRESH_S (the receipt is durable).

SETTLEMENT RULE REGISTRY (integration): the coverage pass also computes each
contract's settlement state from evidence (market_plane.settlement); the
snapshot carries the settlement counts, the bounded venue x sport x league x
family breakdown, the NOT_PROVEN -> PROVEN delta and the rules-text counts;
and a Kalshi SPORTS catalogue step (kalshi_catalogue: credential-free,
GET-only, cursor-complete, TRUNCATED by name) runs every KALSHI_EVERY_S in a
background thread, kill switch KALSHI_CATALOGUE=off. Radar still audits the
PMUS universe; Kalshi rows are counted beside it. No order module is
imported or reachable.

MEMORY (RC5, 2026-10-08). The dedicated service (Render standard, 2 GiB,
MALLOC_ARENA_MAX=2) was OOM-killed at 2 GiB from 06:10:01Z, ~14 times by
12:50Z, after RC4 deployed at 05:16:49Z: 736 -> 890 -> 1400 MB in the first
three minutes, then a 1.4-1.9 GB plateau with spikes to 1998 MB; 1613 MB
within a minute of the 06:12 restart. tools/market_plane_memory.py drives
THIS run loop at production cardinality (synthetic rows) and reproduced it:
RSS 940 MB after the boot pass, 1.68 GB after five, VmHWM 1.95 GB inside a
coverage pass. Every megabyte was a pass holding the whole universe at once
or a temporary that outlived its pass:

  coverage_pass       every row + rules evidence + a result per contract
                      (+1,081-1,531 MB peak)       -> one page at a time
  populate (full)     the catalogue + the registry sha map + every tuple
                      (+444 MB)                    -> one page at a time
  Kalshi walk/persist 75,169 whole venue objects, then every tuple and
                      rules row (+232 / +318 MB)   -> persisted fields only,
                                                      one page at a time
  assignment          32,937 full refdata records read and parsed every
                      30 s, the parsed dict alive between passes (+52 /
                      +165 MB)                     -> the four fields read,
                                                      extracted by the DB
  certify             the same records, parsed     -> the scale fields only
  heartbeat/snapshot  the shard plan's member list: 1,401,121 characters
                      per heartbeat (every 2 s), 1.4 MB per SNAPSHOT event
                                                   -> counts per shard

Nothing is evicted and nothing is subscribed less: the same contracts, books,
coverage, certification and snapshot fields (parity tests in
tests/test_market_plane_memory_bound.py). The heartbeat now carries
`memory` {rss_mb, peak_mb, limit_mb, by_step}: the RSS after each step of
the last pass and the largest rise each step has shown since boot, and a log
line repeats it every MEMORY_LOG_EVERY_S.

PRIORITY ACTIVE REFRESH (RC6). Priority freshness read 114 PMX + 1 REST of
186 (0.618) in the 2026-10-08 20:07Z snapshot: 56 of the 72 members not
current were quiet candidates the healthy subscribe-all stream had not
re-sent inside the 300 s bound (STREAM:SNAPSHOT_OLDER_THAN_THE_BOUND). Each
pass, after the refdata slot, market_plane.active_refresh re-reads such
members through the venue's allow-listed REST book (HELD first, then
CANDIDATE by event start; at most the venue's 12 GetOrderBook reads a
minute; 429 held; every outcome counted); the coverage pass counts a current
refresh as REST_RECOVERY labelled PLANE_ACTIVE_REFRESH, and the census names
each remaining miss with its refresh outcome. The bound, the stream's books
and the PMX parity books are unchanged. UMP_ACTIVE_REFRESH=off removes it.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

from .. import institutional_stream as IS
from ..db import get_pool, heartbeat
from ..market_plane import active_refresh as AR
from ..market_plane import certification as CERT
from ..market_plane import freshness as FR
from ..market_plane import freshness_window as FW
from ..market_plane import populate as POP
from ..market_plane import radar as RADAR
from ..market_plane import registry as R
from ..market_plane import refdata_progress as RP
from ..market_plane import refdata_universe as RU
from ..market_plane import snapshot_refresh as SR
from ..market_plane import rules as RULES
from ..market_plane.sharded_stream import Manager
from ..market_plane.token_keeper import TokenKeeper
from .loop_contract import LOOP_DISABLED

log = logging.getLogger(__name__)
SERVICE = "universal_market_plane"
ENV_FLAG = "UNIVERSAL_MARKET_PLANE"

INTERVAL_S = 2.0
POPULATE_EVERY_S = 10.0
FULL_POPULATE_EVERY_S = 1800.0
ASSIGN_EVERY_S = 30.0
COVERAGE_EVERY_S = 120.0
#: (RC6, lane D2) the coverage pass reads each never-valued full-game or
#: period line contract against its family's captured terms and reports the
#: target-universe waterfall (market_plane.waterfall). Evidence only;
#: neither can make a contract PRICEABLE. COVERAGE_WATERFALL=off restores
#: the RC5 pass.
COVERAGE_WATERFALL_ENV = "COVERAGE_WATERFALL"
CERTIFY_EVERY_S = 300.0
SNAPSHOT_EVERY_S = 60.0
#: a by-symbol refdata read that failed (not a proven absence) is not
#: re-asked for this long; a proven-unlisted contract after UNLISTED_RETRY_S
REFDATA_RETRY_S = 300.0
UNLISTED_RETRY_S = 6 * 3600.0
#: "imminent" for refdata priority: starting within this many seconds
IMMINENT_S = 2 * 3600.0
#: (RC6.3c PMX-1) the deciding process's asked-symbol hand-off
#: (registry.ASKED_HANDOFF_KEY) is read as asks only when written within
#: this, and a symbol only when asked within it: = institutional_api_stream
#: .REQUEST_IDLE_S, the API's own idle bound on an ask (a test pins it)
ASKED_HANDOFF_MAX_AGE_S = 1800.0
#: the held-mark SLA: a canonical book is "fresh" for coverage under the same
#: 300 s rule the held marks use (never wider)
FRESH_SLA_S = 300.0
#: (completion readiness) ONE market-data stream by default: subscribe-all.
DEFAULT_MAX_STREAMS = 1
DEFAULT_MAX_PER_STREAM = 1000
#: the venue's concurrent gRPC streams PER FIRM, pooled across orders, drop
#: copy, positions, balance ledger, RFQ and market data (rep, 2026-10-07)
FIRM_STREAM_BUDGET = 20
#: the explicit-symbol fallback never takes more than this share of it
EXPLICIT_MAX_STREAMS_CEILING = 4
SUBSCRIBE_ALL_ENV = "UMP_SUBSCRIBE_ALL"
MODE_SUBSCRIBE_ALL = "SUBSCRIBE_ALL_EMPTY_SYMBOL_LIST"
MODE_EXPLICIT = "EXPLICIT_SYMBOL_SHARDS"
#: the subscribe-all books' local bound (memory): registry contracts held;
#: every other instrument on the stream is counted, never stored
SUBSCRIBE_ALL_MAX_BOOKS_DEFAULT = 100_000
SUBSCRIBE_ALL_MAX_BOOKS_CEILING = 150_000
#: (settlement rule registry) the Kalshi SPORTS catalogue step: a
#: credential-free, GET-only, cursor-complete walk (kalshi_catalogue) every
#: KALSHI_EVERY_S, run in a background thread so the plane's pass never waits
#: on it, persisted into the registry (venue KALSHI) and market_plane_rules.
#: Kill switch KALSHI_CATALOGUE=off. No order path is imported or reachable.
KALSHI_ENV_FLAG = "KALSHI_CATALOGUE"
KALSHI_EVERY_S = 1800.0


def enabled(env=None) -> bool:
    env = os.environ if env is None else env
    return str(env.get(ENV_FLAG, "on")).strip().lower() not in (
        "off", "0", "false", "no")


def kalshi_enabled(env=None) -> bool:
    env = os.environ if env is None else env
    return str(env.get(KALSHI_ENV_FLAG, "on")).strip().lower() not in (
        "off", "0", "false", "no")


def kalshi_walk() -> dict:
    """The plane's Kalshi catalogue walk: kalshi_catalogue.walk keeping, per
    market, only the fields the registry and rules persisters read
    (populate.kalshi_slim). The whole venue objects of 75,169 markets were
    held from the first page to the persist (+232 MB, RC5 harness)."""
    from .. import kalshi_catalogue as KC
    return KC.walk(project=POP.kalshi_slim)


async def kalshi_step(pool, task, *, now: float, last: float, env=None,
                      walk=None) -> tuple:
    """ONE scheduling decision for the Kalshi catalogue: start a walk in a
    worker thread when due (and none is running), or persist a finished one.
    Returns (task, last_started, report-or-None). Never raises."""
    from .. import kalshi_catalogue as KC
    report = None
    if task is not None and task.done():
        try:
            res = task.result()
            async with pool.acquire() as c:
                persisted = await POP.populate_kalshi(c, res, now=now)
            report = dict(KC.summary(res), persisted=persisted,
                          finished_at=now)
        except Exception as exc:                                # noqa: BLE001
            report = {"error": type(exc).__name__, "finished_at": now,
                      "complete": False}
        # the finished task holds the walk's result until it is dropped
        task = res = None
    if task is None and kalshi_enabled(env) and now - last >= KALSHI_EVERY_S:
        task = asyncio.ensure_future(asyncio.to_thread(walk or kalshi_walk))
        last = now
    return task, last, report


def _env_int(env, k, d):
    try:
        v = int(str(env.get(k, d)))
        return v if v > 0 else d
    except (TypeError, ValueError):
        return d


def caps(env=None) -> tuple:
    """(streams, symbols per stream) for the EXPLICIT fallback: never above
    the documented 1,000 per stream, never more than
    EXPLICIT_MAX_STREAMS_CEILING of the firm's 20 pooled streams."""
    env = os.environ if env is None else env
    return (min(EXPLICIT_MAX_STREAMS_CEILING,
                _env_int(env, "UMP_MAX_STREAMS", DEFAULT_MAX_STREAMS)),
            min(1000, _env_int(env, "UMP_MAX_PER_STREAM",
                               DEFAULT_MAX_PER_STREAM)))


def subscribe_all_enabled(env=None) -> bool:
    env = os.environ if env is None else env
    return str(env.get(SUBSCRIBE_ALL_ENV, "on")).strip().lower() not in (
        "off", "0", "false", "no")


def subscription_plan(env=None) -> dict:
    """The market-data subscription this runtime holds: mode, the streams
    it opens, the symbols its books may hold, the firm budget beside it."""
    env = os.environ if env is None else env
    max_streams, max_per = caps(env)
    if subscribe_all_enabled(env):
        books = min(SUBSCRIBE_ALL_MAX_BOOKS_CEILING,
                    _env_int(env, "UMP_SUBSCRIBE_ALL_MAX_BOOKS",
                             SUBSCRIBE_ALL_MAX_BOOKS_DEFAULT))
        return {"mode": MODE_SUBSCRIBE_ALL, "subscribe_all": True,
                "streams": 1, "books_capacity": books,
                "assign_max_streams": 1, "assign_max_per_stream": books,
                "firm_stream_budget": FIRM_STREAM_BUDGET,
                "symbols_on_the_wire": [],
                "local_filter": "registry refdata-listed PMUS contracts"}
    return {"mode": MODE_EXPLICIT, "subscribe_all": False,
            "streams": max_streams, "books_capacity": max_streams * max_per,
            "assign_max_streams": max_streams,
            "assign_max_per_stream": max_per,
            "firm_stream_budget": FIRM_STREAM_BUDGET,
            "explicit_max_streams_ceiling": EXPLICIT_MAX_STREAMS_CEILING,
            "local_filter": None}


def runtime_resources() -> dict:
    """This process's memory against its own container limit (read, never
    assumed) -- the dedicated runtime's half of the RSS high-water proof."""
    from .. import procmem
    rss, peak, lim = procmem.rss_mb(), procmem.peak_mb(), procmem.limit_mb()
    return {"rss_mb": rss, "peak_mb": peak, "limit_mb": lim,
            "rss_fraction": (round(rss / lim, 4) if rss and lim else None),
            "highwater_fraction": (round(peak / lim, 4) if peak and lim
                                   else None),
            "pid": os.getpid()}


def stream_arming(env=None, *, available=None) -> dict:
    """The same arming rule as institutional_stream.start_default: off unless
    INSTITUTIONAL_MD_STREAM is on; refused when the identity guard refuses the
    credential; unavailable without the transport. NEVER RAISES."""
    env = os.environ if env is None else env
    try:
        from .. import institutional_stream as IS
        from .. import market_data_identity as mdi
        if not IS.enabled(env):
            return {"armed": False, "why": "%s_IS_NOT_ON" % IS.ENV_FLAG}
        refusal = mdi.guard(mdi.PMX, env=env)
        if refusal is not None:
            return {"armed": False, "why": refusal}
        ok, why = (available() if available else IS.transport_available())
        if not ok:
            return {"armed": False, "why": "TRANSPORT_UNAVAILABLE: %s" % why}
        return {"armed": True, "why": None}
    except Exception as exc:                                    # noqa: BLE001
        return {"armed": False, "why": "ARMING_RAISED:%s" % type(exc).__name__}


def fresh_symbols(mgr, *, now: float) -> set:
    if mgr is None:
        return set()
    out = set()
    for s in mgr.subscribed():
        try:
            r = mgr.current(s, now=now, max_snapshot_age_s=FRESH_SLA_S)
        except Exception:                                       # noqa: BLE001
            continue
        if (r or {}).get("ok"):
            out.add(s)
    return out


def latency_report(mgr, *, now: float) -> dict:
    """The latency decomposition over the shards' recent updates: venue age
    (the venue's own cadence) apart from transport (venue clock -> our
    receipt) and processing (receipt -> canonical book replaced). The book is
    readable in this process the instant it is replaced, so distribution
    inside the process is zero by construction; cross-process distribution is
    not measured until a reader is cut over (named)."""
    samples = []
    for vts, recv, norm in (mgr.latency_samples() if mgr is not None else ()):
        d = FR.decompose(venue_at=vts, received_at=recv, normalized_at=norm,
                         distributed_at=norm, now=now)
        samples.append(d)
    rep = FR.summarize(samples)
    ages = [s["venue_age_ms"] for s in samples if s.get("venue_age_ms")
            is not None]
    rep["venue_age_ms"] = {"p50": FR.percentile(ages, .5),
                           "p95": FR.percentile(ages, .95), "n": len(ages),
                           "note": "the venue's own publishing cadence; not "
                                   "a BETTOR latency"}
    rep["distribution_scope"] = ("IN_PROCESS (book readable on replacement); "
                                 "CROSS_PROCESS_NOT_CUT_OVER")
    return rep


def record_populate(state: dict, pop: dict, *, full: bool,
                    now: float) -> None:
    """THE POPULATE RESULT INTO THE PLANE'S STATE. `populate` is the last
    pass of either kind (an incremental pass every POPULATE_EVERY_S replaces
    it); `populate_full` is the last FULL pass's record of the markets it
    kept out of the registry by name (populate.full_pass_record), kept
    under its own key so no incremental pass overwrites it (RC6, lane D2,
    review finding 2: the non-sports markets the slug grammar retires from
    the registry stay counted, by code, with the pass time)."""
    state["populate"] = pop
    if full:
        state["populate_full"] = POP.full_pass_record(pop, at=now)


def coverage_waterfall_on() -> bool:
    """COVERAGE_WATERFALL (default on): the RC6 coverage readings."""
    return os.environ.get(COVERAGE_WATERFALL_ENV, "on").strip().lower() \
        not in ("off", "0", "false", "no")


#: the venue league code of an event slug, in SQL: populate.league_of's
#: grammar (a market-kind prefix -> the next segment, else the first)
_LEAGUE_SQL = ("(CASE WHEN lower(split_part(coalesce(event_slug,''),'-',1)) "
               "        = ANY($4::text[]) "
               "      THEN lower(split_part(coalesce(event_slug,''),'-',2)) "
               "      ELSE lower(split_part(coalesce(event_slug,''),'-',1)) "
               " END)")


async def venue_active_count(conn, *, now: float) -> int:
    """Active SPORTS contracts the venue catalogue lists (non-sports leagues
    excluded by name). (RC6) The league is read by populate.league_of's
    grammar: the venue's event slug carries its league FIRST, so the second
    segment this read before was a team code and excluded nothing.
    (RC6.2, p-coverage rework) By ontology.excluded_as_non_sports, as the
    registry: a row whose market type names a sport is counted whatever its
    code (NAMES_SPORT_SQL_REGEX is that rule's sport-head test)."""
    try:
        return int(await conn.fetchval(
            "SELECT count(DISTINCT market_slug) FROM us_premap "
            " WHERE market_slug IS NOT NULL "
            "   AND listing_state = ANY($1::text[]) "
            "   AND updated_at > to_timestamp($2) "
            "   AND NOT (" + _LEAGUE_SQL + " = ANY($3::text[]) "
            "            AND replace(lower(btrim(coalesce(sports_type, ''))),"
            "                        '-', '_') !~ $5)",
            list(POP.ACTIVE_LISTING_STATES), now - POP.ACTIVE_HORIZON_S,
            sorted(POP.O.NON_SPORTS_LEAGUES),
            sorted(POP.MARKET_KIND_PREFIXES),
            POP.O.NAMES_SPORT_SQL_REGEX) or 0)
    except Exception:                                           # noqa: BLE001
        return -1


async def certify(conn, mgr) -> dict:
    """DURABLE SAME-BOOK CERTIFICATION: the strict window evidence (30
    comparable samples at >= 95% agreement, same venue instant, exact
    identity -- unchanged) for each subscribed contract, persisted keyed by
    its identity fingerprint. A contradiction or a fall below the rule
    overwrites the certificate (not SUPPORTED); a changed fingerprint never
    reuses it."""
    from .. import institutional_same_book as SB
    out = {"evaluated": 0, "supported": 0, "accumulating": 0,
           "contradicted": 0}
    # (RC5) the scale fields only (registry.CERT_SCALE_KEYS), extracted by
    # the database: the identity reads nothing else from a record
    rows = await conn.fetch(
        "SELECT contract_id, " + R.slim_refdata_sql(R.CERT_SCALE_KEYS)
        + " AS refdata FROM market_plane_registry "
        " WHERE venue='POLYMARKET_US' AND active AND refdata IS NOT NULL "
        "   AND coalesce(refdata->>'unlisted','false') <> 'true' "
        "   AND subscription_shard IS NOT NULL")
    recs = {}
    for r in rows:
        rd = POP._jsonish(r["refdata"]) or {}
        recs[r["contract_id"]] = rd
    del rows
    syms = sorted(recs)
    for i in range(0, len(syms), 500):
        chunk = syms[i:i + 500]
        ev = await SB.same_book_by_symbol(conn, chunk)
        for s in chunk:
            e = ev.get(s) or {"status": "UNTESTED", "detail": {}}
            det = e.get("detail") or {}
            rd = recs[s]
            ident = CERT.identity_for(
                s, price_scale=rd.get("priceScale") or rd.get("price_scale"),
                qty_scale=rd.get("fractionalQtyScale") or rd.get("qty_scale"))
            comparable = int(det.get("comparable") or 0)
            agree = int(det.get("agree_top_n") or 0) + int(
                det.get("agree_touch_only") or 0)
            cert = CERT.verdict(comparable=comparable, agreeing=agree,
                                identity=ident)
            supported = e.get("status") == "SUPPORTED" and \
                cert["status"] == "SUPPORTED"
            cert["status"] = "SUPPORTED" if supported else "ACCUMULATING"
            cert["window_status"] = e.get("status")
            cert["identity"] = ident
            await R.save_certification(conn, s, cert)
            out["evaluated"] += 1
            out["supported"] += int(supported)
            out["accumulating"] += int(not supported)
            out["contradicted"] += int(e.get("status") == "CONTRADICTED")
    return out


async def sync_books(conn, mgr) -> dict:
    """THE MANAGER'S SUBSCRIPTION FROM THE REGISTRY, once per assignment
    pass: every contract holding a shard slot and the refdata fields its
    book reads (registry.assigned_instruments). Before (RC4) the run loop
    read every assigned contract's full refdata record and parsed it into a
    dict that stayed bound in the loop until the next assignment pass
    rebound it (+52 MB read, +165 MB parsed, at 32,937 contracts); here the
    temporaries end with the call."""
    rows = await R.assigned_instruments(conn)
    assignments = {r["contract_id"]: r["subscription_shard"] for r in rows}
    instruments = {r["contract_id"]: POP._jsonish(r["refdata"])
                   for r in rows}
    del rows
    return mgr.sync(assignments, instruments)


#: one INFO line with the per-step RSS at most this often
MEMORY_LOG_EVERY_S = 60.0
#: (RC6) boundaries of each full cycle the heartbeat keeps (memory.cycles)
CYCLE_RING = 24


class StepMemory:
    """THE PLANE'S RSS AFTER EACH STEP (RC5). Read from /proc after every
    step that ran in a pass (procmem.rss_mb; None where unreadable): the
    figure the last time the step ran, its rise over the step before it in
    that pass, and the largest rise the step has shown since boot (`passes`
    counts its runs) -- so the next memory question is answered by
    the plane's own heartbeat (`memory`) and log line instead of by
    correlation (analytics run_cycle's rss_mb_by_step is the precedent). A
    rise includes the stream thread's book updates landing during the step.
    VmHWM is read, never reset: `resources.peak_mb` stays the process's own
    high-water.

    (RC6) ACROSS FULL CYCLES. Each boundary of a full cycle of the plane's
    universe (market_plane.memory_cycles.CYCLE_STEPS: the full catalogue
    populate, a finished full refdata pull, a persisted Kalshi walk) is
    kept with the RSS right after it and the FLOOR since the previous one
    (the lowest RSS at the start of a pass), the last CYCLE_RING of each;
    the heartbeat carries them and their trend (`memory.cycles`,
    `memory.cycle_growth`). A bounded working set has a flat floor from
    cycle to cycle; a leak's rises (tools/plane_memory_cycles.py reads the
    same from the logs of a plane that predates this)."""

    def __init__(self, rss=None, clock=None):
        import collections

        from .. import procmem
        from ..market_plane import memory_cycles as MC
        self._rss = rss or procmem.rss_mb
        self._clock = clock or time.time
        self.by_step: dict = {}
        self._prev = None
        self.logged_at = 0.0
        self.cycles = {k: collections.deque(maxlen=CYCLE_RING)
                       for k in MC.CYCLE_STEPS}
        self._floor = {k: None for k in MC.CYCLE_STEPS}

    def begin(self) -> None:
        self._prev = self._rss()
        if self._prev is not None:
            for k, f in self._floor.items():
                if f is None or self._prev < f:
                    self._floor[k] = self._prev

    def cycle(self, kind: str, cur=None) -> None:
        """A full cycle of `kind` just closed: keep the RSS now (`cur`, when
        the caller has just read it) and the floor since the last one; the
        next floor starts here."""
        if kind not in self.cycles:
            return
        if cur is None:
            cur = self._rss()
        floor = self._floor.get(kind)
        self.cycles[kind].append({
            "at": round(self._clock(), 1), "rss_mb": cur,
            "floor_mb": floor if floor is not None else cur})
        self._floor[kind] = cur

    def mark(self, step: str) -> None:
        cur, prev = self._rss(), self._prev
        d = (round(cur - prev, 1) if cur is not None and prev is not None
             else None)
        e = self.by_step.setdefault(step, {"rss_mb": None, "delta_mb": None,
                                           "max_delta_mb": None,
                                           "passes": 0})
        e["rss_mb"], e["delta_mb"] = cur, d
        e["passes"] += 1
        if d is not None and (e["max_delta_mb"] is None
                              or d > e["max_delta_mb"]):
            e["max_delta_mb"] = d
        self._prev = cur
        if step in self.cycles:
            self.cycle(step, cur)

    def digest(self) -> dict:
        from .. import procmem
        from ..market_plane import memory_cycles as MC
        lim = procmem.limit_mb()
        cycles = {k: list(v) for k, v in self.cycles.items() if v}
        return {"rss_mb": procmem.rss_mb(), "peak_mb": procmem.peak_mb(),
                "limit_mb": lim,
                "by_step": {k: dict(v) for k, v in self.by_step.items()},
                "cycles": cycles,
                "cycle_growth": {k: MC.growth(v, limit_mb=lim)
                                 for k, v in cycles.items()},
                "basis": "RSS after each step the last time it ran (/proc);"
                         " max_delta_mb: the largest rise since boot;"
                         " cycles: the RSS after each full-cycle boundary"
                         " and the floor since the previous one"}

    def log_due(self, now: float) -> bool:
        if now - self.logged_at < MEMORY_LOG_EVERY_S:
            return False
        self.logged_at = now
        return True


#: (RC5) THE HEARTBEAT DETAIL IS BOUNDED. It is written every pass (2 s) and
#: read whole by every heartbeat reader, the shared workers' notification
#: monitor every 10 s among them; RC4's carried the shard plan's member list
#: (1,401,121 characters in production). The plan now carries counts; this
#: bound keeps any other section from growing back: a section over
#: HEARTBEAT_SECTION_MAX_CHARS is replaced by its size, by name. The fields
#: the readers read (completion.read runtime_block: runtime,
#: subscription_mode, market_data_streams, resources; status) are scalars or
#: small and are never replaced.
HEARTBEAT_SECTION_MAX_CHARS = 16_000
HEARTBEAT_READER_FIELDS = ("arming", "subscription_mode",
                           "market_data_streams", "runtime", "resources",
                           "memory", "fresh")
R_SECTION_OMITTED = "HEARTBEAT_SECTION_OVER_BOUND_OMITTED"


def heartbeat_detail(*, arming, state, plan_cfg, mgr, sync, fresh,
                     memory=None) -> dict:
    """The `universal_market_plane` heartbeat detail (one per pass)."""
    from ..db import heartbeat_json
    d = {
        "arming": arming, "plan": state.get("plan"),
        "subscription_mode": plan_cfg["mode"],
        "market_data_streams": mgr.stream_count() if mgr else 0,
        "sync": sync, "refdata": state.get("refdata"),
        "runtime": os.environ.get("UMP_RUNTIME", "STANDALONE_UNLABELLED"),
        "resources": runtime_resources(),
        "populate": {k: v for k, v in (state.get("populate") or {})
                     .items() if k not in ("excluded",
                                           "excluded_listed_active")},
        "kalshi": {k: (state.get("kalshi") or {}).get(k) for k in (
            "enabled", "complete", "stopped", "markets", "requests",
            "error")},
        # (RC6) this pass of the priority active refresh: due / read /
        # deferred and why, counts only
        "refresh": {k: v for k, v in (state.get("refresh") or {}).items()
                    if k != "reads"},
        # (RC6 D1) the freshness task beside the pass: ticks, errors; the
        # frozen-window sampler's last sample (counts, never the members)
        "freshness_task": dict(state.get("freshness_task") or {}),
        "freshness_window": dict((state.get("freshness_window") or {})
                                 .get("last") or {}),
        "fresh": len(fresh)}
    if memory is not None:
        d["memory"] = memory
    for k, v in list(d.items()):
        if k in HEARTBEAT_READER_FIELDS or not isinstance(v, (dict, list)):
            continue
        n = len(heartbeat_json(v))
        if n > HEARTBEAT_SECTION_MAX_CHARS:
            d[k] = {"omitted": R_SECTION_OMITTED, "chars": n,
                    "bound": HEARTBEAT_SECTION_MAX_CHARS}
    return d


async def refdata_step(pool, client, planner, attempted: dict, *,
                       now: float, lock=None) -> dict:
    """ONE refdata call slot (refdata_universe.Planner decides which): a
    batched by-symbol read for priority contracts, the next full-pull page,
    or a batched read for contracts added since the last complete pull.
    Persists only registry members; a COMPLETE pull proves absent exactly
    the contracts that were pending when it started and never appeared.
    Returns this slot's digest. Raises nothing it can name. `lock` (RC6 D1)
    is the plane's one PMX client lock, shared with the freshness task's
    book reads: the call is made holding it. (RC6.3c PMX-1) The deciding
    process's asked symbols (registry.asked_handoff) lead the priority
    read, then EVALUATED_CANDIDATE, then held and imminent
    (registry.PRIORITY_ORDER); the slot's digest names the hand-off."""
    out = {"action": None}
    async with pool.acquire() as c:
        hand = await R.asked_handoff(c, now=now,
                                     max_age_s=ASKED_HANDOFF_MAX_AGE_S)
        pend = await R.refdata_pending_split(
            c, now=now, unlisted_retry_s=UNLISTED_RETRY_S,
            priority_max=POP.P_CANDIDATE, imminent_s=IMMINENT_S,
            limit=RU.BATCH_MAX,
            excluded=RP.cooling_ids(attempted, now=now,
                                    retry_s=REFDATA_RETRY_S),
            asked=hand["symbols"])
        out["asked"] = {"handoff": hand["state"], "age_s": hand["age_s"],
                        "symbols": len(hand["symbols"]),
                        "without_refdata_at_api": hand["without_refdata"],
                        "pending": pend.get("asked_pending"),
                        "not_in_registry": pend.get("asked_not_in_registry")}
        action = planner.next_action(now=now,
                                     priority_pending=pend["priority"],
                                     other_pending=pend["other"])
        if action is None:
            return dict(out, pending_priority=len(pend["priority"]),
                        pending_other=len(pend["other"]))
        if action.get("start"):
            planner.pull["pending_at_start"] = await R.refdata_pending_ids(c)
    body = planner.body_for(action)
    if lock is not None:
        async with lock:
            res = await asyncio.to_thread(client.read_instruments, body)
    else:
        res = await asyncio.to_thread(client.read_instruments, body)
    done_at = time.time()
    # paced from the slot's start: call STARTS stay >= interval apart
    got = planner.record(action, res, now=now)
    parsed = got["parsed"]
    out.update(action=action["kind"], status=parsed["status"],
               records=len(parsed["records"]), ms=parsed.get("ms"),
               pending_priority=len(pend["priority"]),
               pending_other=len(pend["other"]))
    if action["kind"] == RU.A_PRIORITY:
        # the asked symbols this read answered (persisted below)
        out["asked"]["answered"] = sum(
            1 for s_ in action["symbols"][:int(pend.get("asked_pending") or 0)]
            if s_ in parsed["records"])
    async with pool.acquire() as c:
        if action["kind"] == RU.A_PAGE:
            out["stored"] = await R.save_refdata_many(
                c, parsed["records"], at=done_at)
        else:
            asked = set(action["symbols"])
            out["stored"] = await R.save_refdata_many(
                c, {k: v for k, v in parsed["records"].items() if k in asked},
                at=done_at)
            out["unlisted"] = await R.save_unlisted_many(
                c, got["unlisted"], at=done_at,
                basis="BY_SYMBOL_READ_200_OMITTED")
            if not parsed["ok"]:
                for s_ in action["symbols"]:
                    attempted[s_] = done_at
        fin = got.get("finished")
        if fin is not None:
            seen = fin.pop("_seen", set()) or set()
            pending0 = fin.pop("_pending_at_start", None)
            if fin["status"] == RU.COMPLETE and pending0:
                fin["proven_unlisted"] = await R.save_unlisted_many(
                    c, set(pending0) - seen, at=done_at,
                    basis="COMPLETE_FULL_PULL_OMITTED")
            await R.record_event(c, R.REFDATA_FULL_PULL_KIND, fin["id"], fin)
            out["finished"] = fin
    if len(attempted) > 200000:
        attempted.clear()
    return out


#: (RC6 D1) THE FRESHNESS TASK'S TICK: one refresh step a second, beside
#: the pass. The budget (12 book reads in any 60 s, >= 1 s apart) is the
#: refresher's own; this only decides how often it is asked.
FRESHNESS_TICK_S = 1.0
#: a failing freshness step is logged at most this often (counted always)
FRESHNESS_LOG_EVERY_S = 300.0


async def freshness_loop(refresher, mgr, client, *, state: dict,
                         client_lock, bound: float = FRESH_SLA_S,
                         tick_s: float | None = None,
                         clock=time.time, snapper=None,
                         token_fn=None) -> None:
    """THE PLANE'S FRESHNESS TASK (RC6 D1), beside the pass loop and never
    waiting on it.

    WHY. The pass made the refresh's reads (one per pass) and production's
    pass is minutes long: SNAPSHOT events, one per pass at most, arrived
    p50 92 s / p90 248 s / p99 739 s apart over 24 h and every one of the
    newest 40 more than 197 s apart (research-sql run 37870039455), so the
    RC6 budget of 12 book reads a minute became about 0.25. Here one refresh
    step runs every FRESHNESS_TICK_S whatever the pass is doing (coverage,
    certification and snapshot queries await the database, and this task
    runs in those awaits). The pass keeps its own step, so a stuck task
    still leaves one read a pass; both drivers share the refresher (one
    budget, one in-flight set) and the client lock (one PMX client, never
    two calls at once -- the refdata slot takes it too).

    THE FROZEN-WINDOW SAMPLE (RC6 D1, measurement): every
    freshness_window.SAMPLE_EVERY_S the same task freezes the hour's
    eligible membership (once; persisted) and samples it at one instant,
    persisted as FRESHNESS_SAMPLE -- so the measure is the whole window,
    sampled every minute whatever the pass is doing, and a minute with no
    sample is an outage the readback counts.

    THE FROZEN MEMBERS ARE SERVED FOR THE WHOLE WINDOW (RC6.2, lane
    p-freshness, independent review rev1). The refresh read only the LIVE
    registry list (priority <= P_CANDIDATE, or a working order), re-read
    every 30 s, while the window measures the membership frozen at its first
    sample for the whole hour: a member that left the live list mid-hour
    lost its refresh record and was never read again -- coded N for the
    rest of the hour with the budget idle (production, window 15:00Z on the
    RC6.1 plane, research-sql 37959672993: 8 pregame candidates now at
    priority 20, N in 8-47 of 53 samples, ~3.8 a sample). Each tick hands
    the window this task holds to the refresher (`set_frozen`); its members
    join the live list with their frozen tier. Same budget, same gap, same
    bound; nothing removed from either list. The members only the window
    holds are outside the scorecard's instant denominator and share the
    same 12 reads a minute, so the order between the two lists is the
    owner's (active_refresh.list_order; by default the live list first:
    the window's other members get every read the live list does not
    need -- independent review rev2).

    THE SNAPSHOT-ONLY gRPC REFRESH (RC6 D1, market_plane.snapshot_refresh):
    when `snapper` is given, the tick first offers it its call (at most one
    a minute, every member due within 90 s of its bound, the venue's own
    CreateMarketDataSubscription snapshot_only); the REST step then reads
    only what that did not make current.

    Never raises but CancelledError: a failed step is counted in
    state["freshness_task"] (logged at most every FRESHNESS_LOG_EVERY_S)
    and the next tick tries again."""
    tick = FRESHNESS_TICK_S if tick_s is None else float(tick_s)
    st = state.setdefault("freshness_task", {})
    st.update(started_at=clock(), ticks=0, errors=0, last_error=None,
              tick_s=tick)
    fw = state.setdefault("freshness_window", {})
    logged = 0.0
    while True:
        try:
            pool = await get_pool()
            if snapper is not None and refresher is not None and \
                    token_fn is not None:
                got_s = await snapper.step(refresher, mgr,
                                           token_fn=token_fn, bound=bound,
                                           clock=clock)
                if got_s is not None:
                    st["last_snapshot_call"] = got_s
            if refresher is not None:
                # (RC6.2) the frozen window's members are refreshed for the
                # whole window, not only while the live registry list holds
                # them (one shared refresher: the pass's step too)
                refresher.set_frozen(fw.get("window"), now=clock())
                # the task's own step digest (the heartbeat's `refresh`
                # stays the pass's step): due / read / deferred, no reads
                got = await AR.step(
                    pool, client, refresher, mgr, bound=bound, clock=clock,
                    lock=client_lock)
                st["last_refresh"] = {k: v for k, v in got.items()
                                      if k not in ("reads", "by_reason")}
                st["reads"] = int(st.get("reads") or 0) + int(
                    got.get("read") or 0)
            await FW.step(pool, mgr, refresher, fw, now=clock(),
                          sla_s=bound)
            st["ticks"] += 1
            st["last_tick_at"] = clock()
        except asyncio.CancelledError:
            raise
        except Exception as exc:                                # noqa: BLE001
            st["errors"] += 1
            st["last_error"] = type(exc).__name__
            if clock() - logged >= FRESHNESS_LOG_EVERY_S:
                logged = clock()
                log.warning("market plane freshness task step failed (%s); "
                            "%d failures so far", type(exc).__name__,
                            st["errors"])
        await asyncio.sleep(tick)


async def run() -> None:
    if not enabled():
        log.info("universal_market_plane: off by switch (%s)", ENV_FLAG)
        return LOOP_DISABLED  # off by configuration: not restarted (loop_contract)
    plan_cfg = subscription_plan()
    max_streams = plan_cfg["assign_max_streams"]
    max_per = plan_cfg["assign_max_per_stream"]
    arming = stream_arming()
    mgr, client, keeper = None, None, None
    if arming["armed"]:
        from .. import pmx_institutional as PMX
        client = PMX.Institutional(env=os.environ)
        keeper = TokenKeeper(client)
        keeper.start()
        mgr = Manager(token_fn=keeper.token, max_per_stream=max_per,
                      max_streams=max_streams,
                      subscribe_all=plan_cfg["subscribe_all"],
                      invalidate_token=keeper.invalidate)
    log.info("universal_market_plane: streams %s (%s) mode=%s streams=%d "
             "books=%d", "ARMED" if mgr else "NOT_ARMED", arming["why"],
             plan_cfg["mode"], plan_cfg["streams"], plan_cfg["books_capacity"])
    # (RC6) the priority active refresh rides the armed stream's client and
    # books; off by switch, or absent when no stream is armed
    # (RC6.2) the order between the live registry list and the members only
    # the frozen window holds is the owner's (UMP_REFRESH_LIST_ORDER;
    # LIVE_FIRST unless set): the budget is the same in every order
    refresher = (AR.ActiveRefresh(per_minute=AR.per_min(os.environ),
                                  order=AR.list_order(os.environ))
                 if mgr is not None and AR.enabled() else None)
    log.info("universal_market_plane: priority active refresh %s",
             ("ON (%d book reads/min, bound %.0f s, list order %s)"
              % (refresher.per_min, FRESH_SLA_S, refresher.order))
             if refresher else
             (AR.R_REFRESH_NO_STREAM if mgr is None else AR.R_REFRESH_OFF))
    # (RC6 D1) the snapshot-only gRPC refresh beside it: one call a minute
    snapper = (SR.SnapshotRefresh() if refresher is not None
               and SR.enabled() else None)
    log.info("universal_market_plane: snapshot-only refresh %s",
             ("ON (%s snapshot_only, <= %d symbols, every %.0f s)"
              % (SR.RPC, SR.MAX_SYMBOLS_PER_CALL, SR.CALL_EVERY_S))
             if snapper else SR.R_SNAPSHOT_OFF if refresher is not None
             else "OFF (no refresh here)")
    watermark = 0.0
    last = {"populate": 0.0, "full": 0.0, "assign": 0.0, "coverage": 0.0,
            "certify": 0.0, "snapshot": 0.0}
    seen_receipts: dict = {}
    attempted: dict = {}
    planner = None
    state: dict = {"plan": {}, "coverage": {}, "certification": {},
                   "populate": {}, "catalogue": {}, "refdata": {},
                   "kalshi": {"enabled": kalshi_enabled()},
                   "subscription_plan": plan_cfg}
    kalshi_task, kalshi_last = None, 0.0
    mem = StepMemory()
    # (RC6 D1) the plane's one PMX client is shared by the refdata slot and
    # both refresh drivers: one call at a time
    client_lock = asyncio.Lock()
    fresh_task = None
    while True:
        try:
            pool = await get_pool()
            now = time.time()
            mem.begin()
            if fresh_task is None or fresh_task.done():
                if fresh_task is not None and not fresh_task.cancelled() \
                        and fresh_task.exception() is not None:
                    state.setdefault("freshness_task", {})[
                        "restarted_after"] = type(
                            fresh_task.exception()).__name__
                fresh_task = asyncio.ensure_future(freshness_loop(
                    refresher, mgr, client, state=state,
                    client_lock=client_lock, snapper=snapper,
                    token_fn=(keeper.token if keeper is not None
                              else None)))
            kalshi_task, kalshi_last, krep = await kalshi_step(
                pool, kalshi_task, now=now, last=kalshi_last)
            if krep is not None:
                state["kalshi"] = dict(krep, enabled=kalshi_enabled())
                mem.mark("kalshi_persist")
            async with pool.acquire() as c:
                if mgr is not None and planner is None:
                    planner = RU.Planner(
                        calls_per_minute=RU.calls_per_min(os.environ),
                        last_complete_at=await R.last_complete_full_pull(c))
                cat = await POP.catalogue_completeness(c)
                state["catalogue"] = cat
                ids = {k: v.get("receipt_id")
                       for k, v in (cat.get("lanes") or {}).items()}
                new_receipt = ids != seen_receipts
                full = (now - last["full"] >= FULL_POPULATE_EVERY_S
                        or (new_receipt and ids.get("full") !=
                            seen_receipts.get("full")))
                if full or now - last["populate"] >= POPULATE_EVERY_S:
                    pop = await POP.populate(c, since=watermark, now=now,
                                             full=full, excluded_detail=full)
                    watermark = max(watermark, pop.get("watermark") or 0.0)
                    record_populate(state, pop, full=full, now=now)
                    last["populate"] = now
                    if full:
                        last["full"] = now
                        seen_receipts = ids
                    mem.mark("populate_full" if full else "populate")
                if mgr is not None and now - last["assign"] >= ASSIGN_EVERY_S:
                    state["plan"] = await R.assign_missing_shards(
                        c, max_per_stream=max_per, max_streams=max_streams)
                    state["plan"]["subscription_mode"] = plan_cfg["mode"]
                    last["assign"] = now
                    state["sync"] = await sync_books(c, mgr)
                    mem.mark("assign")
            sync = state.get("sync") or {}
            if mgr is not None:
                # ONE refdata call slot per pass at most (the planner paces
                # it to the venue's 6/min); priority contracts first
                state["refdata"] = await refdata_step(
                    pool, client, planner, attempted, now=now,
                    lock=client_lock)
                mem.mark("refdata")
                if state["refdata"].get("finished") is not None:
                    mem.cycle("refdata_full_pull")
            if refresher is not None:
                # (RC6) a fresh REST book for the priority members the
                # stream has gone quiet on, inside the book-read budget;
                # (RC6 D1) the freshness task makes the same step every
                # second beside this pass -- one budget, one client lock
                state["refresh"] = await AR.step(pool, client, refresher,
                                                 mgr, bound=FRESH_SLA_S,
                                                 lock=client_lock)
                mem.mark("refresh")
            now = time.time()  # source ages checked AFTER the catch-up work
            fresh = fresh_symbols(mgr, now=now)
            async with pool.acquire() as c:
                if now - last["coverage"] >= COVERAGE_EVERY_S:
                    rc6 = coverage_waterfall_on()
                    state["coverage"] = await POP.coverage_pass(
                        c, fresh_symbols=fresh, now=now,
                        refreshed=(refresher.current_for_coverage(
                            mgr, now=now, bound=FRESH_SLA_S)
                            if refresher is not None else None),
                        derivative_terms=rc6, waterfall=rc6,
                        outside_registry=state.get("populate_full"))
                    last["coverage"] = now
                    mem.mark("coverage")
                if mgr is not None and \
                        now - last["certify"] >= CERTIFY_EVERY_S:
                    state["certification"] = await certify(c, mgr)
                    last["certify"] = now
                    mem.mark("certify")
                if now - last["snapshot"] >= SNAPSHOT_EVERY_S:
                    # (RC6 D1) THE SNAPSHOT IS VERIFIED WHEN IT IS MADE.
                    # `now` is the pass's instant before coverage and
                    # certification -- minutes earlier in production -- and
                    # the census read the books (which kept changing) as if
                    # at that instant: a book received since counted with a
                    # negative age, a refresh received since did not count,
                    # and computed_at named an instant nothing was verified
                    # at. The stream's fresh set and the census are taken
                    # now; the coverage tiers keep their own instant
                    # (priority_universe.verified_at).
                    snap_now = time.time()
                    snap_fresh = fresh_symbols(mgr, now=snap_now)
                    state["token"] = keeper.digest() if keeper else None
                    state["refdata_universe"] = (planner.digest(snap_now)
                                                 if planner else None)
                    snap = await snapshot(c, mgr, state, now=snap_now,
                                          arming=arming, fresh=snap_fresh,
                                          caps=(max_streams, max_per),
                                          refresher=refresher,
                                          snapper=snapper)
                    # THE CONSUMER PARITY BRIDGE (SHADOW): the priority
                    # members' PMX tops, one append-only event per pass;
                    # the API compares them with the REST books the paper
                    # runtime used. Never read by a decision.
                    cen = ((snap.get("freshness") or {}).get(
                        "priority_universe") or {}).get("census") or {}
                    books = cen.pop("pmx_books", None)
                    if books:
                        await R.record_event(
                            c, PRIORITY_BOOKS_KIND,
                            "pbooks:%d" % int(snap_now // 60),
                            {"at": snap_now, "books": books,
                             "mode": "SHADOW_PARITY_NO_DECISION_EFFECT"})
                    await R.record_event(
                        c, "SNAPSHOT", "snapshot:%d" % int(snap_now // 60),
                        snap)
                    state["last_snapshot"] = snap
                    last["snapshot"] = now
                    del snap, cen, books, snap_fresh
                    mem.mark("snapshot")
            status = "ok" if (state.get("last_snapshot") or {}).get(
                "radar", {}).get("green") else "degraded"
            memory = mem.digest()
            if mem.log_due(now):
                log.info("universal_market_plane RSS by step (MB): %s "
                         "rss=%s peak=%s limit=%s",
                         {k: v["rss_mb"] for k, v in
                          memory["by_step"].items()}, memory["rss_mb"],
                         memory["peak_mb"], memory["limit_mb"])
            await heartbeat(SERVICE, status, heartbeat_detail(
                arming=arming, state=state, plan_cfg=plan_cfg, mgr=mgr,
                sync=sync, fresh=fresh, memory=memory))
            await asyncio.sleep(INTERVAL_S)
        except asyncio.CancelledError:
            if fresh_task is not None:
                fresh_task.cancel()
            if mgr is not None:
                mgr.stop()
            if keeper is not None:
                keeper.stop()
            if kalshi_task is not None:
                kalshi_task.cancel()
            raise
        except Exception as exc:                                # noqa: BLE001
            log.exception("universal market plane pass failed")
            try:
                await heartbeat(SERVICE, "error",
                                {"error": type(exc).__name__})
            except Exception:                                   # noqa: BLE001
                pass
            await asyncio.sleep(10)


PRIORITY_CENSUS_SAMPLE = 40
#: the market_plane_events kind carrying the priority members' PMX tops
PRIORITY_BOOKS_KIND = "PRIORITY_PMX_BOOKS"


#: (RC6) the stream refusal QUIET_VALID would stand in for, in the
#: counterfactual the census reports (never counted)
QUIET_COUNTERFACTUAL_REFUSAL = "STREAM:%s" % IS.R_SNAPSHOT_OLD


async def priority_census(conn, mgr, *, fresh: set, now: float,
                          refreshed: dict | None = None,
                          refresher=None) -> dict:
    """EVERY PRIORITY MEMBER THAT IS NOT CURRENT, CLASSIFIED (owner closeout
    item 3): by tier (held / candidate), refdata state, shard assignment,
    the stream's own refusal for the symbol, and the age of its latest
    REST / public book. Read only; nothing is excluded from the denominator
    here -- this names why each member is not current.

    (RC6) A member CURRENT VIA THE PLANE'S ACTIVE REFRESH (`refreshed`, the
    refresher's own `current` at this instant) is current, counted apart
    (`current_via_refresh`). Every member still not current carries its
    last refresh outcome (`refresh`; NOT_YET_READ when none was made, None
    when the refresh does not run here), counted in `by_refresh_outcome`.
    `quiet_valid_counterfactual` counts the members a QUIET_VALID rule
    (connection live, symbol acknowledged on the current connection, no gap
    since the snapshot) would make current: REPORTED FOR THE PM'S DECISION,
    NEVER COUNTED -- the numerator does not read it.

    (RC6.2) A paper REST read makes a member current here exactly as in the
    coverage pass (populate.REST_BOOK_SQL / paper_book_counts): the newest
    error-free read, inside the bound, whose own state does not say the
    market is not open (REST_MARKET_NOT_OPEN names one that does), and
    `refresh_held_market_terminal` counts the members the refresh does not
    re-read now (for an hour after the read, active_refresh.RETRY_ENDED_S)
    because the venue said the market has ended -- each still a
    member, still not current."""
    rows = await conn.fetch(
        "SELECT contract_id, priority, required_reason, event_start, "
        "       CASE WHEN refdata IS NULL THEN 'REFDATA_PENDING' "
        "            WHEN refdata->>'unlisted' = 'true' THEN 'PMX_UNLISTED' "
        "            ELSE 'PMX_LISTED' END AS refdata_state "
        "  FROM market_plane_registry WHERE active AND priority <= $1",
        POP.P_CANDIDATE)
    slugs = [r["contract_id"] for r in rows]
    # (RC6.2) the paper runtime's NEWEST ERROR-FREE book read (6 h) and the
    # state it stated: current only inside the bound and only when that
    # state does not say the market is not open -- the coverage pass's own
    # rule (populate.REST_BOOK_SQL / paper_book_counts). Before, the age of
    # ANY row (an error row, a read never made, included) made it current.
    rest, rest_open = {}, {}
    for r in (await conn.fetch(
            "SELECT DISTINCT ON (us_market_slug) us_market_slug AS slug, "
            "       extract(epoch FROM now() - observed_at) AS age, "
            "       market_state "
            "  FROM paper_book_observations WHERE us_market_slug = "
            "   ANY($1::text[]) AND observed_at > now() - interval '6 hours' "
            "   AND error IS NULL "
            " ORDER BY us_market_slug, observed_at DESC", slugs)
            if slugs else ()):
        rest[r["slug"]] = float(r["age"])
        rest_open[r["slug"]] = POP.paper_book_counts(r.get("market_state"))
    shard_of = dict(getattr(mgr, "symbol_to_shard", {}) or {}) \
        if mgr is not None else {}
    connected = {}
    if mgr is not None:
        try:
            connected = {d["shard"]: d.get("connected")
                         for d in mgr.shard_digest()}
        except Exception:                                       # noqa: BLE001
            connected = {}
    by, sample, pmx = {}, [], {}
    via_refresh, by_refresh, via_origin = 0, {}, {}
    held_terminal = 0
    quiet = {"stream_quiet_on_the_live_connection": 0,
             "of_which_symbol_acked_on_this_connection": 0,
             "snapshot_age_s": []}
    refreshed = refreshed or {}
    for r in rows:
        s = r["contract_id"]
        age = rest.get(s)
        if s in fresh and mgr is not None:
            # THE PMX BOOK OF A PRIORITY MEMBER, for the consumer parity
            # bridge (SHADOW: published, compared, never a decision input)
            try:
                cur = mgr.current(s, now=now, max_snapshot_age_s=FRESH_SLA_S)
            except Exception:                                   # noqa: BLE001
                cur = None
            if (cur or {}).get("ok"):
                bk = cur.get("book") or {}
                sn = (cur.get("evidence") or {}).get("snapshot") or {}
                pmx[s] = {"best_bid": bk.get("best_bid"),
                          "best_offer": bk.get("best_offer"),
                          "venue_ts": sn.get("venue_ts"),
                          "received_at": sn.get("received_at")}
        if s in fresh or (age is not None and age <= FRESH_SLA_S
                          and rest_open.get(s, True)):
            continue
        if s in refreshed:
            # a REST book the plane read within the bound (RC6), or its
            # snapshot-only gRPC read (RC6 D1), counted by origin
            via_refresh += 1
            o = (refresher.origin_of(s) if refresher is not None
                 and hasattr(refresher, "origin_of") else None) or "REST"
            via_origin[o] = via_origin.get(o, 0) + 1
            continue
        cur = None      # the stream's read of this member, when one is made
        tier = "HELD" if int(r["priority"]) <= POP.P_HELD else "CANDIDATE"
        if r["refdata_state"] != "PMX_LISTED":
            why = r["refdata_state"]
        elif s not in shard_of:
            why = "LISTED_NOT_ASSIGNED_TO_A_SHARD"
        elif connected.get(shard_of[s]) is False:
            why = "SHARD_NOT_CONNECTED"
        else:
            try:
                cur = mgr.current(s, now=now, max_snapshot_age_s=FRESH_SLA_S)
            except Exception as exc:                            # noqa: BLE001
                cur = {"refusal": "READ_RAISED:%s" % type(exc).__name__}
            why = "STREAM:%s" % str((cur or {}).get("refusal") or (
                cur or {}).get("reason") or (cur or {}).get(
                "FRESHNESS_STATUS") or "NOT_CURRENT")[:60]
        start = r["event_start"]
        phase = ("NO_START" if start is None else
                 "STARTED_GT_4H" if (now - start.timestamp()) > 4 * 3600 else
                 "IN_PLAY_OR_RECENT" if start.timestamp() <= now else
                 "PREGAME")
        rest_k = ("NO_REST_BOOK_6H" if age is None else
                  "REST_MARKET_NOT_OPEN" if not rest_open.get(s, True) else
                  "REST_OLDER_THAN_300S")
        k = "%s|%s|%s|%s" % (tier, why, rest_k, phase)
        by[k] = by.get(k, 0) + 1
        ro = None
        if refresher is not None:
            ro = refresher.outcome_of(s) or "NOT_YET_READ"
            by_refresh[ro] = by_refresh.get(ro, 0) + 1
            # (RC6.2) a member the refresh holds out of its reads because
            # the venue said the market has ended: still a member, still
            # not current, counted here by name of the rule
            if hasattr(refresher, "held_terminal") and \
                    refresher.held_terminal(s, now=now, stream_received_at=((
                        (cur or {}).get("evidence") or {}).get(
                            "snapshot") or {}).get("received_at")):
                held_terminal += 1
        if why == QUIET_COUNTERFACTUAL_REFUSAL and isinstance(cur, dict):
            # every check current() makes before the snapshot age passed:
            # running, scaled, connected, this connection's snapshot, no
            # gap, the venue clock, the stream alive -- only the age failed
            ev = cur.get("evidence") or {}
            quiet["stream_quiet_on_the_live_connection"] += 1
            quiet["of_which_symbol_acked_on_this_connection"] += int(bool(
                (ev.get("subscription") or {}).get(
                    "acked_on_current_connection")))
            sa = (ev.get("snapshot") or {}).get("age_s")
            if sa is not None:
                quiet["snapshot_age_s"].append(float(sa))
        if len(sample) < PRIORITY_CENSUS_SAMPLE:
            sample.append({"contract_id": s, "tier": tier, "why": why,
                           "rest_age_s": None if age is None
                           else round(age, 1), "phase": phase,
                           "refresh": ro})
    ages = sorted(quiet.pop("snapshot_age_s"))
    quiet["snapshot_age_s"] = ({"n": len(ages), "p50": round(
        ages[len(ages) // 2], 1), "max": round(ages[-1], 1)} if ages else None)
    quiet.update(counted=False, rule=(
        "QUIET_VALID (PM decision, not implemented): connection live, symbol "
        "acknowledged on the current connection, no gap since the snapshot"))
    return {"members": len(rows), "not_current": sum(by.values()),
            "current_via_refresh": via_refresh,
            "current_via_refresh_by_origin": via_origin,
            "by_tier_reason_rest_phase": dict(sorted(
                by.items(), key=lambda kv: -kv[1])),
            "by_refresh_outcome": dict(sorted(
                by_refresh.items(), key=lambda kv: -kv[1])),
            "refresh_held_market_terminal": held_terminal,
            "quiet_valid_counterfactual": quiet, "sample": sample,
            "pmx_books": pmx}


async def freshness_denominators(conn, cov: dict, reg: dict, plan: dict, *,
                                 subscribed: int, fresh: int,
                                 now: float, refresh: dict | None = None
                                 ) -> dict:
    """THE TWO DENOMINATORS, never blended (owner, 2026-10-06).

      priority_universe   open PAPER positions + evaluated candidates (the
                          capital-required markets): current via the PMX
                          stream or the REST recovery read inside the 300 s
                          SLA, over all of them (external named apart)
      held_positions      the management truth: bettor_paper_freshness over
                          the account's open positions (markable = all but
                          EXTERNAL_UNAVAILABLE with venue evidence)
      total_universe      every active sports contract: subscription
                          eligible, streamed, current via fallback, stale,
                          overflow, external -- nothing excluded to raise it

    (RC6) The priority REST fallback is labelled by origin
    (`current_rest_fallback_by_origin`: PAPER_BOOK_OBSERVATION, the paper
    runtime's reads; PLANE_ACTIVE_REFRESH, the plane's own book reads of
    members the stream went quiet on -- the coverage pass's count, None when
    that pass ran without the refresh), and `active_refresh` is the
    refresh's digest (budget, outcomes, members current through it).
    """
    tiers = (cov or {}).get("freshness_tiers") or {}
    origins = (cov or {}).get("rest_recovery_by_origin") or {}
    pr = tiers.get("PRIORITY") or {}
    al = tiers.get("ALL") or {}
    # (RC6 D1) PMX_GRPC = the stream + the snapshot-only refresh, apart
    snap = int((((cov or {}).get("pmx_grpc_by_origin") or {}).get(
        "PRIORITY") or {}).get("PLANE_SNAPSHOT_REFRESH") or 0)

    def rate(t):
        den = int(t.get("total") or 0) - int(t.get(
            "EXTERNAL_DATA_UNAVAILABLE") or 0)
        cur = int(t.get("PMX_GRPC") or 0) + int(t.get("REST_RECOVERY") or 0)
        return None if den <= 0 else round(cur / den, 4)
    # THE HELD-POSITION DENOMINATOR IS READ BY THE API, NOT HERE: this
    # worker is a started loop and must not import the paper ledger (whose
    # import graph reaches venue-write layers -- test_workers_hold_no_venue_
    # write / test_paper_records_cannot_reach_the_funded_path). GET
    # /api/command/market-plane fills it from bettor_paper_freshness.read.
    held = {"status": "READ_BY_THE_API", "rate": None,
            "source": "GET /api/command/market-plane (bettor_paper_freshness"
                      ".read: FRESH + QUIET_VALID / markable; 300 s SLA)"}
    total_active = int(reg.get("active") or 0)
    streamed_fresh = int(fresh)
    via_fallback = int(al.get("REST_RECOVERY") or 0)
    ext = int(al.get("EXTERNAL_DATA_UNAVAILABLE") or 0)
    current = int(al.get("PMX_GRPC") or 0) + via_fallback
    return {
        "priority_universe": {
            "denominator": int(pr.get("total") or 0),
            "current_pmx_stream": int(pr.get("PMX_GRPC") or 0) - snap,
            "current_pmx_snapshot_refresh": snap,
            "current_rest_fallback": int(pr.get("REST_RECOVERY") or 0),
            "current_rest_fallback_by_origin": (
                dict(origins["PRIORITY"]) if origins.get("PRIORITY")
                is not None else None),
            "not_current": int(pr.get("NONE") or 0),
            "external_unavailable": int(pr.get(
                "EXTERNAL_DATA_UNAVAILABLE") or 0),
            "rate": rate(pr), "target": 0.95,
            "members": "OPEN_PAPER_POSITION + EVALUATED_CANDIDATE (6 h)",
            # (RC6 D1) THE INSTANT THESE COUNTS WERE VERIFIED: the coverage
            # pass's own (it took the stream's fresh set then), not the
            # snapshot's -- the two are minutes apart when a pass is slow
            "verified_at": (cov or {}).get("computed_at"),
            "verified_age_s": (None if (cov or {}).get("computed_at") is None
                               else round(float(now) - float(
                                   cov["computed_at"]), 1)),
            "active_refresh": refresh},
        "held_positions": held,
        "total_universe": {
            "active_contracts": total_active,
            "subscription_eligible": int(reg.get("pmx_listed") or 0),
            "pmx_unlisted": int(reg.get("pmx_unlisted") or 0),
            "refdata_pending": int(reg.get("refdata_pending") or 0),
            "streamed": int(subscribed),
            "streamed_current": streamed_fresh,
            "current_via_fallback": via_fallback,
            "stale_or_unread": max(0, total_active - current - ext),
            "overflow": plan.get("overflow_count"),
            "external_unavailable": ext,
            "rate": (round(current / max(1, total_active - ext), 4)
                     if total_active > ext else None)}}


async def snapshot(conn, mgr, state: dict, *, now: float, arming: dict,
                   fresh: set, caps: tuple, refresher=None,
                   snapper=None) -> dict:
    """ONE append-only market-plane snapshot: universe, registry, coverage,
    subscription plan, sources, freshness, latency, certification, catalogue
    completeness and Radar. Read by GET /api/command/market-plane.
    `refresher` is the priority active refresh (RC6), or None where it does
    not run (off by switch, or no stream armed: said so in the digest)."""
    venue_active = await venue_active_count(conn, now=now)
    reg = dict(await conn.fetchrow(
        "SELECT count(*) FILTER (WHERE active) AS active, "
        "       count(*) FILTER (WHERE active AND sport IS NOT NULL) "
        "           AS active_sports, "
        "       count(*) FILTER (WHERE active AND required_reason = "
        "                        'VENUE_ACTIVE') AS venue_listed, "
        "       count(*) FILTER (WHERE active AND required_reason IN "
        "           ('OPEN_PAPER_POSITION','EVALUATED_CANDIDATE')) "
        "           AS required, "
        "       count(*) FILTER (WHERE active AND refdata IS NOT NULL AND "
        "           coalesce(refdata->>'unlisted','false') <> 'true') "
        "           AS pmx_listed, "
        "       count(*) FILTER (WHERE active AND "
        "           refdata->>'unlisted' = 'true') AS pmx_unlisted, "
        "       count(*) FILTER (WHERE active AND refdata IS NULL) "
        "           AS refdata_pending, "
        "       count(*) FILTER (WHERE active AND subscription_shard IS NOT "
        "           NULL) AS assigned, "
        "       count(*) AS total FROM market_plane_registry "
        # the PMUS universe (the streams, the catalogue and Radar are its);
        # Kalshi rows are counted on their own below
        " WHERE venue = 'POLYMARKET_US'"))
    kalshi_reg = dict(await conn.fetchrow(
        "SELECT count(*) FILTER (WHERE active) AS active, count(*) AS total "
        "  FROM market_plane_registry WHERE venue = 'KALSHI'"))
    rules = await RULES.rules_counts(conn)
    cert = dict(await conn.fetchrow(
        "SELECT count(*) FILTER (WHERE status = 'SUPPORTED') AS supported, "
        "       count(*) FILTER (WHERE status = 'ACCUMULATING') "
        "           AS accumulating, count(*) AS total "
        "  FROM market_plane_certification"))
    cov = state.get("coverage") or {}
    plan = state.get("plan") or {}
    lat = latency_report(mgr, now=now)
    subscribed = len(mgr.subscribed()) if mgr is not None else 0
    reg_active = int(reg.get("active") or 0)
    # Radar audits the PMUS universe: its coverage counts only (a Kalshi row
    # is a named ontology gap of another venue, reported beside it)
    pm_cov = ({"by_state": (cov.get("by_venue") or {})["POLYMARKET_US"]}
              if "POLYMARKET_US" in (cov.get("by_venue") or {}) else cov)
    rad = RADAR.audit(
        venue_active=max(0, venue_active), registry_active=reg_active,
        subscribed=subscribed, fresh=len(fresh), coverage=pm_cov,
        catalogue_complete=bool((state.get("catalogue") or {}).get(
            "complete")),
        shard_complete=bool(plan.get("complete", mgr is not None)),
        latency=lat if lat.get("n") else None)
    extra = []
    if mgr is None:
        extra.append("PMX_STREAMS_NOT_ARMED:%s" % arming.get("why"))
    if venue_active < 0:
        extra.append("VENUE_CATALOGUE_UNREADABLE")
    by = pm_cov.get("by_state") or {}
    sbs = ((cov.get("settlement") or {}).get("by_venue") or {}).get(
        "POLYMARKET_US") or {}
    for k in ("SETTLEMENT_RULE_EVIDENCE_CONFLICT",
              "EXTERNAL_SETTLEMENT_DATA_UNAVAILABLE"):
        if sbs.get(k):
            extra.append("%s:%d" % (k, sbs[k]))
    if by.get("MAPPED_BUT_SETTLEMENT_NOT_PROVEN"):
        extra.append("SETTLEMENT_PROOF_MISSING:%d"
                     % by["MAPPED_BUT_SETTLEMENT_NOT_PROVEN"])
    if by.get("MAPPED_BUT_NO_FAIR_VALUE_SOURCE"):
        extra.append("FAIR_VALUE_SOURCE_MISSING:%d"
                     % by["MAPPED_BUT_NO_FAIR_VALUE_SOURCE"])
    stale = max(0, subscribed - len(fresh))
    if stale:
        extra.append("SUBSCRIBED_BUT_NOT_FRESH:%d" % stale)
    if reg_active < max(0, venue_active):
        extra.append("AGENT_VISIBLE_BELOW_VENUE:%d"
                     % (max(0, venue_active) - reg_active))
    rad["extra_findings"] = extra
    max_streams, max_per = caps
    # CAPACITY, explicit (owner): streams, symbol capacity, subscribed,
    # overflow and the streams the whole subscribable universe would need
    rad["capacity"] = {
        "streams_open": len(mgr.shard_digest()) if mgr is not None else 0,
        "max_streams": max_streams, "max_per_stream": max_per,
        "symbol_capacity": max_streams * max_per,
        "subscribed": subscribed,
        "subscribable": plan.get("subscribable"),
        "overflow": plan.get("overflow_count"),
        "streams_required_for_full_coverage": plan.get(
            "shards_required_for_all"),
        "runtime": os.environ.get("UMP_RUNTIME", "STANDALONE_UNLABELLED")}
    sub_plan = state.get("subscription_plan") or subscription_plan()
    rad["capacity"].update({
        "subscription_mode": sub_plan["mode"],
        "market_data_streams": (mgr.stream_count() if mgr is not None
                                and hasattr(mgr, "stream_count") else 0),
        "market_data_streams_expected": sub_plan["streams"],
        "firm_stream_budget": FIRM_STREAM_BUDGET})
    if refresher is not None:
        refresh = refresher.digest(now=now, bound=FRESH_SLA_S, mgr=mgr)
        refreshed = refresher.current(mgr, now=now, bound=FRESH_SLA_S)
    else:
        refresh = AR.off_digest(AR.R_REFRESH_OFF if not AR.enabled()
                                else AR.R_REFRESH_NO_STREAM)
        refreshed = None
    freshness = await freshness_denominators(conn, cov, reg, plan,
                                             subscribed=subscribed,
                                             fresh=len(fresh), now=now,
                                             refresh=refresh)
    # (RC6 D1) the snapshot-only gRPC refresh's digest, or why it is off
    freshness["priority_universe"]["snapshot_refresh"] = (
        snapper.digest(now=now) if snapper is not None else
        SR.off_digest(SR.R_SNAPSHOT_OFF if refresher is not None
                      and not SR.enabled() else AR.R_REFRESH_NO_STREAM
                      if refresher is None else SR.R_SNAPSHOT_OFF))
    try:
        freshness["priority_universe"]["census"] = await priority_census(
            conn, mgr, fresh=fresh, now=now, refreshed=refreshed,
            refresher=refresher)
    except Exception as exc:                                    # noqa: BLE001
        freshness["priority_universe"]["census"] = {
            "error": type(exc).__name__}
    rad["capacity"].update(RP.capacity_view(
        active=reg_active, subscribable=int(reg.get("pmx_listed") or 0),
        pending=int(reg.get("refdata_pending") or 0),
        subscribed=subscribed, max_streams=max_streams,
        max_per_stream=max_per))
    rad["freshness"] = {k: (v.get("rate") if isinstance(v, dict) else None)
                        for k, v in freshness.items()}
    return {
        "computed_at": now, "version": "UNIVERSAL_MARKET_PLANE_SNAPSHOT_V1",
        "authority": R.AUTHORITY,
        "universe": {"venue_active_sports_contracts": venue_active,
                     "registry": reg,
                     "represented": reg_active,
                     "coverage_pct": (round(100.0 * min(reg_active,
                                                        venue_active)
                                            / venue_active, 3)
                                      if venue_active > 0 else None)},
        "coverage": {k: v for k, v in cov.items() if k not in (
            "rows", "settlement")},
        # (settlement rule registry) the settlement state of every active
        # contract, its venue x sport x league x family breakdown (bounded),
        # this pass's NOT_PROVEN -> PROVEN delta, and the rules-text counts
        "settlement": cov.get("settlement"),
        "rules": rules,
        "kalshi": dict(state.get("kalshi") or {}, registry=kalshi_reg),
        "subscription": {"armed": mgr is not None,
                         "arming_why": arming.get("why"),
                         "subscription_mode": sub_plan["mode"],
                         "market_data_streams": rad["capacity"][
                             "market_data_streams"],
                         "market_data_streams_expected": sub_plan["streams"],
                         "firm_stream_budget": FIRM_STREAM_BUDGET,
                         "books_capacity": sub_plan["books_capacity"],
                         "discovery": (mgr.discovery(limit=10)
                                       if mgr is not None and
                                       hasattr(mgr, "discovery") else None),
                         "token": state.get("token"),
                         "configured_capacity": {"max_streams": max_streams,
                                                 "max_per_stream": max_per,
                                                 "symbols": max_streams
                                                 * max_per},
                         "plan": plan,
                         "shards": mgr.shard_digest() if mgr else [],
                         "subscribed": subscribed, "fresh": len(fresh),
                         "stale_subscribed": stale},
        "sources": cov.get("source_counts"),
        "freshness": freshness,
        # (RC6 D1) the frozen-window sampler's newest sample (counts, its
        # window's membership hash), beside the two denominators -- never
        # blended into them; the whole window is completion.read's
        # market_data.freshness_window
        "freshness_window_last_sample": dict(
            (state.get("freshness_window") or {}).get("last") or {}),
        "latency": lat, "certification": dict(
            cert, last_pass=state.get("certification")),
        "catalogue": state.get("catalogue"),
        "populate": {k: v for k, v in (state.get("populate") or {}).items()},
        # (RC6) the last FULL pass's markets kept out of the registry by
        # name, by code: no incremental pass replaces it
        "populate_full": state.get("populate_full"),
        "refdata": state.get("refdata"),
        "refdata_universe": state.get("refdata_universe"),
        "runtime": {"label": os.environ.get("UMP_RUNTIME",
                                            "STANDALONE_UNLABELLED"),
                    "resources": runtime_resources()},
        "radar": rad}


async def main():
    """THE DEDICATED MARKET-DATA SERVICE'S ENTRY POINT: the universal market
    plane and, beside it, the authenticated Kalshi WebSocket book runtime
    (Kalshi rep production contract 2026-10-07: WebSocket primary, never on
    sportsassets-api; it reports OWNER_ACTION_REQUIRED and stays idle until
    its key is provisioned on this service).

    ORDERLESS BY STRUCTURE (market_plane_guard, closeout 2026-10-08): the
    guard locks this process against every venue write and blocks the
    order-client modules from import BEFORE any runtime starts; an
    order-capable credential on this service makes it refuse to run (it
    idles and says why -- never restart-churned). Its boot record
    (`market_plane` heartbeat) carries the running commit and the guard
    report, so the plane's SHA is read back like the other services'."""
    from .. import market_plane_guard as GUARD
    guard = GUARD.install()
    from . import kalshi_ws_market_data as KWSMD
    if guard["refused"]:
        await plane_beat(guard, status="blocked")
        return
    await asyncio.gather(run(), KWSMD.run(), plane_beat(guard))


PLANE_SERVICE = "market_plane"
PLANE_BEAT_EVERY_S = 60.0


async def plane_beat(guard: dict, *, status: str = "ok",
                     forever: bool = True) -> None:
    """The dedicated plane's own boot / liveness record: commit, mode,
    guard report (names only). Never raises; a refused plane keeps beating
    `blocked` so the reason is readable without a restart loop."""
    started = time.time()
    while True:
        try:
            await heartbeat(PLANE_SERVICE, status, {
                "commit": os.environ.get("RENDER_GIT_COMMIT"),
                "service": os.environ.get("RENDER_SERVICE_NAME"),
                "runtime": os.environ.get("UMP_RUNTIME",
                                          "STANDALONE_UNLABELLED"),
                "started_at": started, "guard": guard})
        except Exception:                                       # noqa: BLE001
            log.warning("market plane boot record not written",
                        exc_info=True)
        if not forever:
            return
        await asyncio.sleep(PLANE_BEAT_EVERY_S)


if __name__ == "__main__":
    asyncio.run(main())
