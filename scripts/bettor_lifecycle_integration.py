"""THE FULL BETTOR OBSERVATION LIFECYCLE, INSIDE THE DEPLOYMENT IMAGE.

Run from WORKDIR /app inside the image built from `backend/Dockerfile`,
against a PostgreSQL database built by `sportsassets.scripts.migrate`.

WHAT IS REAL
  the image; the database; `bettor_live_loop.main()` WITH NO ARGUMENTS as
  `workers/all.py` invokes it; `settings()`; `db.get_pool()`; every
  reservation; every control read; the whole selection rule; the real
  `bettor_market_stream.MarketStream` class.

WHAT IS REPLACED -- the external venue network boundary, and only that.
  `pmus._get_client`        -> serves recorded HTTP bodies
  `MarketStream._run`       -> the socket thread, replaced by a replay

THE FOUR FIXTURES ARE KEPT APART, each with its provenance printed, because
merging them is how a constructed record gets mistaken for a recording:

  FIXTURE_BBO      RECORDED_VERBATIM     `/v1/markets/{slug}/bbo` HTTP 200
                                         marketData objects
  FIXTURE_BOOK     RECORDED_VERBATIM     `/v1/markets/{slug}/book` HTTP 200
                                         marketData objects, paired with the
                                         BBO of the same market and segment
  FIXTURE_LISTING  CONSTRUCTED           no capture of the COLLECTION endpoint
                                         exists; the envelope is built to the
                                         SDK's declared MarketDetail fields
                                         over real slugs taken from the
                                         recorded corpus
  FIXTURE_WS       ABSENT                NO RECORDED PMUS WEBSOCKET FRAME
                                         EXISTS ANYWHERE IN THE CORPUS, and
                                         none is invented here. The socket
                                         path is exercised by replaying the
                                         RECORDED /book bodies through the
                                         production `_on_market_data`, which
                                         is the same ingest the socket calls.
                                         That tests our handling. It does NOT
                                         establish the venue's frame format.

Every result this file produces is SIMULATED TRANSPORT. It is not a live
venue connection and must never be reported as one.
"""
import argparse
import asyncio
import json
import os
import sys
import time
import uuid

sys.path.insert(0, "/app")

import asyncpg                                              # noqa: E402
import sportsassets.pmus as pmus                            # noqa: E402
from sportsassets import bettor_live_control as ctl         # noqa: E402
from sportsassets import bettor_live_store as store_mod     # noqa: E402
from sportsassets import bettor_market_stream as ms         # noqa: E402
from sportsassets import bettor_universe as uni             # noqa: E402
from sportsassets.db import get_pool                        # noqa: E402
from sportsassets.workers import bettor_live_loop as bl     # noqa: E402

CORPUS = os.environ.get("BETTOR_RECORDED_CORPUS",
                        "/recorded/bbo_book_real_400.json")

FAILS = []
CHECKS = 0


def check(label, got, want):
    global CHECKS
    CHECKS += 1
    ok = got == want
    print("   [%s] %-62s got %r" % ("PASS" if ok else "FAIL", label, got))
    if not ok:
        FAILS.append((label, got, want))
    return ok


def rule(t):
    print("\n" + "=" * 78 + "\n" + t + "\n" + "=" * 78)


# ══ fixtures ════════════════════════════════════════════════════════════
class Fixtures:
    """Loads the recorded corpus and keeps the four kinds apart."""

    def __init__(self, n=40, *, spread_min=0.03):
        raw = json.load(open(CORPUS))
        self.corpus_total = raw["_corpus_totals"]["bbo_http_200"]
        self.bbo = {}          # slug -> RECORDED /bbo marketData
        self.book = {}         # slug -> RECORDED /book marketData
        self.slugs = []
        for p in raw["pairs"]:
            b, bk = p.get("bbo") or {}, p.get("book") or {}
            if not (b.get("bestBid") and b.get("bestAsk")):
                continue
            if not (bk.get("bids") and bk.get("offers")):
                continue
            try:
                lo = float(b["bestBid"]["value"])
                hi = float(b["bestAsk"]["value"])
            except (TypeError, ValueError, KeyError):
                continue
            if hi - lo < spread_min or b.get("state") != "MARKET_STATE_OPEN":
                continue
            slug = b.get("marketSlug")
            if not slug or slug in self.bbo:
                continue
            self.bbo[slug] = b
            self.book[slug] = dict(bk, marketSlug=slug)
            self.slugs.append(slug)
            if len(self.slugs) >= n:
                break

    def listing_envelope(self):
        """CONSTRUCTED. See the module docstring."""
        return [{"id": "id-%s" % s, "slug": s, "title": "Recorded %s" % s,
                 "outcome": "yes", "description": "", "active": True,
                 "closed": False, "liquidity": 1000.0, "volume": 5000.0,
                 "eventSlug": "ev-%s" % s, "team": "T"} for s in self.slugs]

    def describe(self):
        return {
            "FIXTURE_BBO": "RECORDED_VERBATIM  %d markets (of %d HTTP 200 "
                           "bodies in the corpus)" % (len(self.bbo),
                                                      self.corpus_total),
            "FIXTURE_BOOK": "RECORDED_VERBATIM  %d markets, paired by "
                            "(slug, segment)" % len(self.book),
            "FIXTURE_LISTING": "CONSTRUCTED  no capture of the collection "
                               "endpoint exists",
            "FIXTURE_WS": "ABSENT  no recorded PMUS WebSocket frame exists; "
                          "none invented",
        }


class RecordedMarkets:
    """The HTTP venue. Counts every request; never invents a body."""

    def __init__(self, fx, *, fail_bbo=False, timeout_bbo=False,
                 mangle=None, empty_listing=False, listing_fails=0):
        self.fx = fx
        self.fail_bbo = fail_bbo
        self.timeout_bbo = timeout_bbo
        self.mangle = mangle or {}      # slug -> callable(md) -> md
        self.empty_listing = empty_listing
        self.listing_fails = listing_fails
        self.list_calls = 0
        # EVERY DISPATCH, WITH ITS ARGUMENTS. `list_calls` alone cannot
        # distinguish "one page" from "six pages" and that is precisely
        # how the reservation check came to pass on a broken build; see
        # `list` below.
        self.list_dispatch: list = []
        self.bbo_calls = []
        self.book_calls = []

    def list(self, params):
        """ONE INVOCATION == ONE OUTBOUND REQUEST, and it PAGINATES.

        THE DEFECT IN THIS DOUBLE, found 2026-09-22 after a reviewer
        asked how `listing_attempts_reserved >= list_calls` could ever
        have passed on a build that issued six requests per
        reservation. It could not have -- unless `list_calls` was
        counting a different boundary, and it was:

            if ... params.get("offset", 0) > 0: return {"markets": []}
            return {"markets": self.fx.listing_envelope()}

        The envelope is 40 rows and the page size is 500, so the
        worker's pagination loop got a short page on its FIRST call and
        stopped. `list_calls` was 1, the reservation was 1, and
        `1 >= 1` passed. THE CHECK NEVER EXERCISED PAGINATION AT ALL,
        so tightening it to `==` would have changed nothing.

        This version slices the envelope by `offset`/`limit` like the
        venue does, so a small page size produces real multi-page
        traffic and the reconciliation in L3B has something to count.
        """
        self.list_calls += 1
        off = int(params.get("offset", 0) or 0)
        lim = int(params.get("limit", 500) or 500)
        self.list_dispatch.append({"n": self.list_calls, "offset": off,
                                   "limit": lim,
                                   "at": time.time()})
        if self.listing_fails > 0:
            self.listing_fails -= 1
            raise RuntimeError("listing transport failed")
        if self.empty_listing:
            return {"markets": []}
        rows = self.fx.listing_envelope()
        return {"markets": rows[off:off + lim]}

    def bbo(self, slug):
        self.bbo_calls.append(slug)
        if self.timeout_bbo:
            raise TimeoutError("venue read timed out")
        if self.fail_bbo:
            raise RuntimeError("venue read failed")
        md = self.fx.bbo.get(slug)
        if md is None:
            raise RuntimeError("no recorded body for %s" % slug)
        fn = self.mangle.get(slug)
        return {"marketData": fn(dict(md)) if fn else md}

    def book(self, slug):
        self.book_calls.append(slug)
        return {"marketData": self.fx.book[slug]}

    def settlement(self, slug):
        return {"marketData": {}}

    @property
    def distinct(self):
        return len(set(self.bbo_calls))


class Client:
    def __init__(self, markets):
        self.markets = markets


class ReplayStream(ms.MarketStream):
    """The production MarketStream with ONE method overridden: `_run`.

    `book_at`, `_on_market_data`, the freshness gates, epoch invalidation,
    `subscribe`, `prune`, `stop` and the capture path are inherited.

    evidence_class is REPLAY_DECISION. The production class states the rule
    itself: "a replaying transport reading a file produces REPLAY_DECISION".
    """

    evidence_class = "REPLAY_DECISION"
    made = []
    frames = {}
    restamp = True
    stall = False               # deliver nothing, to test NO_BOOK refusals

    def __init__(self, key_id, secret_key, on_book=None, on_trade=None,
                 autostart=False):
        super().__init__(key_id, secret_key, on_book=on_book,
                         on_trade=on_trade, autostart=False)
        ReplayStream.made.append(self)

    def _run(self):
        import datetime as _dt
        self.epoch += 1
        self.connected = True
        self.connected_since = ms._now_iso()
        self.first_connected_at = self.connected_since
        while not self._stop:
            if not ReplayStream.stall:
                with self._lock:
                    want = list(self._subs)
                for slug in want:
                    md = ReplayStream.frames.get(slug)
                    if md is None:
                        continue
                    md = dict(md)
                    if ReplayStream.restamp:
                        # ONLY transactTime moves, to the replay clock, so
                        # the frames clear MAX_SOURCE_AGE_S = 10 s. Every
                        # price, size, ladder level, state and counter below
                        # is exactly as the venue sent it.
                        md["transactTime"] = _dt.datetime.now(
                            _dt.timezone.utc).strftime(
                                "%Y-%m-%dT%H:%M:%S.%f000Z")
                    self._on_market_data({"marketData": md})
            for _ in range(20):
                if self._stop:
                    break
                time.sleep(0.1)
        self.connected = False


# ══ operator statements, verbatim from render-ops ═══════════════════════
ARM_SQL = (
    "INSERT INTO ingestion_state (key, value) VALUES ($1, "
    "jsonb_build_object('probe_id', $2::text,"
    " 'started_at', to_char(now() AT TIME ZONE 'UTC',"
    "   'YYYY-MM-DD\"T\"HH24:MI:SS+00:00'),"
    " 'deadline_at', to_char((now() + ($3::text || ' minutes')::interval)"
    "   AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS+00:00'),"
    " 'max_distinct', $4::int, 'max_bbo_attempts', $5::int,"
    " 'max_listing_attempts', $6::int, 'distinct_reserved', 0,"
    " 'bbo_attempts_reserved', 0, 'listing_attempts_reserved', 0,"
    " 'slugs', '[]'::jsonb)) "
    "ON CONFLICT (key) DO UPDATE SET value = excluded.value")


async def arm(pool, *, minutes=30, distinct=40, attempts=160, listing=18):
    pid = str(uuid.uuid4())
    await pool.execute(ARM_SQL, ctl.BUDGET_KEY, pid, str(minutes),
                       distinct, attempts, listing)
    await run_on(pool)
    return pid


async def journal_rows(pool, slugs):
    """The COMMITTED records for these markets, from the database."""
    if not slugs:
        return []
    rows = await pool.fetch(
        "SELECT record FROM bettor_live_journal WHERE market_id = ANY($1)",
        list(slugs))
    return [json.loads(r["record"]) if isinstance(r["record"], str)
            else r["record"] for r in rows]


async def run_on(pool):
    await pool.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1, 'true'::jsonb)"
        " ON CONFLICT (key) DO UPDATE SET value = 'true'::jsonb",
        ctl.CONTROL_KEY)


async def stop_now(pool):
    await pool.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1, 'false'::jsonb)"
        " ON CONFLICT (key) DO UPDATE SET value = 'false'::jsonb",
        ctl.CONTROL_KEY)


async def budget(pool):
    v = await pool.fetchval("SELECT value FROM ingestion_state WHERE key=$1",
                            ctl.BUDGET_KEY)
    return json.loads(v) if isinstance(v, str) else v


async def counters(pool):
    b = await budget(pool)
    if b is None:
        return None
    return (b["listing_attempts_reserved"], b["distinct_reserved"],
            b["bbo_attempts_reserved"])


async def journal(pool):
    return await pool.fetchval("SELECT count(*) FROM bettor_live_journal")


async def wipe_observation(pool):
    """Reset ONLY this experiment's own rows between phases."""
    await pool.execute("DELETE FROM ingestion_state WHERE key = ANY($1::text[])",
                       [ctl.BUDGET_KEY, ctl.CONTROL_KEY])
    for t in ("bettor_live_journal", "bettor_live_cursor",
              "bettor_live_ledger"):
        await pool.execute("TRUNCATE %s" % t)


def install(fx, markets):
    ReplayStream.made.clear()
    ReplayStream.frames = fx.book          # RECORDED /book bodies
    ReplayStream.stall = False
    ms.MarketStream = ReplayStream
    bl.ms.MarketStream = ReplayStream
    pmus._get_client = lambda: Client(markets)


async def supervisor_run(timeout_s):
    """`main()` exactly as workers/all.py invokes it: NO ARGUMENTS.

    Exceptions are CAUGHT AND NAMED, because that is what the supervisor
    does: `run_forever` logs the exception and restarts the loop after
    RESTART_DELAY_SECONDS. A crash is a lifecycle event here, not a
    harness failure.
    """
    task = asyncio.create_task(bl.main())
    try:
        return await asyncio.wait_for(task, timeout=timeout_s)
    except asyncio.TimeoutError:
        task.cancel()
        try:
            await task
        except BaseException:
            pass
        return {"started": None, "why": "HARNESS_TIMEOUT"}
    except Exception as exc:                            # noqa: BLE001
        return {"started": None, "why": "CRASHED",
                "exc": type(exc).__name__}


async def run_until_stopped(pool, work_s=12.0, timeout_s=120.0):
    """Let `main()` work, then obs-stop it and take its OWN return value.

    `main()` only returns a report when it stops for a reason; cancelling
    it yields nothing, which is why the earlier revision of this file read
    an empty durability block and reported it as a missing bound.
    """
    task = asyncio.create_task(bl.main())
    await asyncio.sleep(work_s)
    await stop_now(pool)
    try:
        return await asyncio.wait_for(task, timeout=timeout_s)
    except asyncio.TimeoutError:
        task.cancel()
        try:
            await task
        except BaseException:
            pass
        return {"started": None, "why": "HARNESS_TIMEOUT"}


def report_of(out):
    """main() returns {started, stopped_by, report}; durability is inside."""
    return ((out or {}).get("report") or {})


async def settle(timeout=8.0):
    """Wait for the replay threads to observe their stop."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if all(not f.connected for f in ReplayStream.made):
            return True
        await asyncio.sleep(0.1)
    return False


def fast_probe():
    # The 0.25 req/s pace is a separate, separately-asserted property.
    # Spending 160 s of it in every phase would prove nothing these
    # phases are about.
    os.environ["BETTOR_PROBE_MAX_RPS"] = "100000"
    os.environ["BETTOR_PROBE_CONCURRENCY"] = "4"
    ctl.CONTROL_EVERY_S = 1.0


# ══ phases ══════════════════════════════════════════════════════════════

async def L1_disabled_startup(pool):
    rule("L1  A DISABLED WORKER MAKES NO OBSERVATION VENUE REQUEST")
    await wipe_observation(pool)
    fx = Fixtures()
    m = RecordedMarkets(fx)
    install(fx, m)
    await arm(pool)                      # allowance OPEN and control ON,
    os.environ[bl.KILL_ENV] = "off"      # so only the kill switch refuses
    try:
        bl._reset_backoff()
        # KILL_SWITCH is a control reason, so the refusal holds IDLE_POLL_S
        # (30 s) before returning. The wait is the behaviour, not a delay.
        out = await supervisor_run(75)
    finally:
        os.environ.pop(bl.KILL_ENV, None)
    print("   result: started=%s why=%s" % (out.get("started"),
                                            out.get("why")))
    check("the kill switch refused the run", out.get("why"), "KILL_SWITCH")
    check("it is a CONTROL reason, so it polls and never escalates",
          bl.is_control_reason("KILL_SWITCH"), True)
    check("ZERO listing requests were issued", m.list_calls, 0)
    check("ZERO BBO requests were issued", len(m.bbo_calls), 0)
    check("ZERO stream connections were opened",
          sum(1 for f in ReplayStream.made if f.epoch), 0)
    check("and the OPEN allowance was not touched",
          await counters(pool), (0, 0, 0))


async def L3_reservation_precedes_dispatch(pool):
    rule("L3  EVERY LISTING AND BBO REQUEST IS PRECEDED BY A DURABLE "
         "RESERVATION")
    await wipe_observation(pool)
    fx = Fixtures()
    m = RecordedMarkets(fx)
    install(fx, m)
    pid = await arm(pool)
    bl._reset_backoff()
    await supervisor_run(45)
    await stop_now(pool)
    await settle()
    b = await budget(pool)
    print("   venue : %d listing, %d BBO over %d distinct"
          % (m.list_calls, len(m.bbo_calls), m.distinct))
    print("   row   : listing=%s distinct=%s attempts=%s"
          % (b["listing_attempts_reserved"], b["distinct_reserved"],
             b["bbo_attempts_reserved"]))
    # EQUALITY, NOT `>=`. This check was written as `>=` while
    # `_list_candidates` reserved ONE unit around a loop that issued up
    # to SIX requests -- so it passed on a build that undercounted
    # outbound listing requests by 6x, and the defect had to be found
    # by reading the source instead. One reserved unit per outbound
    # request is the repair, and equality is what states it.
    check("one listing reservation per outbound listing request",
          b["listing_attempts_reserved"], m.list_calls)
    check("listing requests were actually issued", m.list_calls >= 1, True)
    check("one distinct slot per distinct market read",
          b["distinct_reserved"], m.distinct)
    check("one attempt reservation per BBO request",
          b["bbo_attempts_reserved"], len(m.bbo_calls))
    check("every reserved slug is recorded in the row",
          set(b["slugs"]) == set(m.bbo_calls), True)
    check("the identity on the row is the armed one", b["probe_id"], pid)
    check("ceiling: distinct <= 40", b["distinct_reserved"] <= 40, True)
    check("ceiling: attempts <= 160", b["bbo_attempts_reserved"] <= 160, True)
    check("ceiling: listing <= 18",
          b["listing_attempts_reserved"] <= 18, True)


async def L3B_listing_dispatch_reconciles(pool):
    """EVERY OUTBOUND LISTING REQUEST, RECONCILED AGAINST ITS RESERVATION.

    WHY THIS PHASE EXISTS. L3 asserted
    `listing_attempts_reserved >= m.list_calls` and passed on a build
    where `_list_candidates` took ONE reservation and issued up to SIX
    `markets.list` calls. That was only possible because the check was
    counting the wrong boundary: the fixture's listing is 40 rows, the
    page size defaults to 500, so the worker's pagination loop got a
    short page on its FIRST call and stopped. `list_calls` was 1, the
    reservation was 1, and `1 >= 1` passed. Tightening `>=` to `==`
    would have changed nothing -- PAGINATION WAS NEVER EXERCISED.

    This phase forces it. The page size is set small enough that the
    same 40 markets span several pages, one attempt is made to fail so
    a RETRY is exercised too, and the reconciliation is made against
    the RECORDED DISPATCH LIST -- every invocation with its offset --
    rather than against a bare counter.
    """
    rule("L3B  LISTING PAGINATION AND RETRIES ARE RESERVED PER REQUEST")
    await wipe_observation(pool)
    fx = Fixtures(n=40)
    # 40 markets at 8 per page = 5 pages, inside the 6-page bound.
    os.environ["BETTOR_LIVE_DISCOVERY_PAGE_SIZE"] = "8"
    os.environ["BETTOR_LIVE_DISCOVERY_PAGES"] = "6"
    # The FIRST request fails, so a retry is issued and must be paid
    # for like any other request.
    m = RecordedMarkets(fx, listing_fails=1)
    install(fx, m)
    pid = await arm(pool)
    bl._reset_backoff()
    await supervisor_run(60)
    await stop_now(pool)
    await settle()
    b = await budget(pool)

    offsets = [d["offset"] for d in m.list_dispatch]
    print("   dispatched : %d requests at offsets %s"
          % (len(m.list_dispatch), offsets))
    print("   reserved   : %s listing units"
          % b["listing_attempts_reserved"])

    check("pagination was actually exercised", len(m.list_dispatch) > 1, True)
    check("a retry was actually exercised",
          offsets.count(0) >= 2, True)
    check("the pages walked distinct offsets",
          sorted(set(offsets)) == sorted({0, 8, 16, 24, 32}), True)
    # THE RECONCILIATION. One reserved unit per outbound request,
    # counted at the dispatch boundary, retries and pages included.
    check("reserved == outbound listing requests dispatched",
          b["listing_attempts_reserved"], len(m.list_dispatch))
    check("   ... and the bare counter agrees with the dispatch list",
          m.list_calls, len(m.list_dispatch))
    check("the listing ceiling still holds",
          b["listing_attempts_reserved"] <= 18, True)
    check("the identity on the row is the armed one", b["probe_id"], pid)
    os.environ.pop("BETTOR_LIVE_DISCOVERY_PAGE_SIZE", None)
    os.environ.pop("BETTOR_LIVE_DISCOVERY_PAGES", None)


async def L4_no_replenishment(pool):
    rule("L4  FAILURES, RETRIES, EMPTY SELECTION AND CRASHES CANNOT "
         "REPLENISH AN ALLOWANCE")
    await wipe_observation(pool)
    fx = Fixtures()

    # (a) every BBO read fails -> allowance still spent, nothing refunded
    m = RecordedMarkets(fx, fail_bbo=True)
    install(fx, m)
    await arm(pool)
    bl._reset_backoff()
    await supervisor_run(45)
    await stop_now(pool)
    await settle()
    a = await counters(pool)
    print("   (a) all reads fail   : venue %d BBO, row %s" % (len(m.bbo_calls), (a,)))
    check("failed reads consumed their attempts", a[2], len(m.bbo_calls))
    check("failed reads consumed their distinct slots", a[1], m.distinct)

    # (b) a second lifetime on the SAME row adds, never resets
    await run_on(pool)
    m2 = RecordedMarkets(fx, fail_bbo=True)
    install(fx, m2)
    bl._reset_backoff()
    await supervisor_run(45)
    await stop_now(pool)
    await settle()
    b2 = await counters(pool)
    print("   (b) second lifetime  : row %s (was %s)" % (b2, a))
    check("no counter moved backwards",
          all(y >= x for x, y in zip(a, b2)), True)
    check("the allowance was not reset to zero", b2 != (0, 0, 0), True)

    # (c) empty listing -> the listing attempt is still consumed
    await wipe_observation(pool)
    m3 = RecordedMarkets(fx, empty_listing=True)
    install(fx, m3)
    await arm(pool)
    bl._reset_backoff()
    await supervisor_run(30)
    await stop_now(pool)
    await settle()
    c = await counters(pool)
    print("   (c) empty universe   : venue %d listing, row %s"
          % (m3.list_calls, (c,)))
    check("an empty universe still consumed its listing attempt",
          c[0] >= 1, True)
    check("and reserved no BBO attempt it did not make",
          c[2], len(m3.bbo_calls))

    # (d) crash after dispatch -> wasted, never replenished
    await wipe_observation(pool)
    m4 = RecordedMarkets(fx)
    install(fx, m4)
    await arm(pool)
    before = None
    for i in range(3):
        bl._reset_backoff()
        real = bl.LiveLoop.recover

        async def boom(self, *a, **k):
            # `recover()` sits AFTER every reservation and after every
            # request has gone out -- the exact window a post-dispatch
            # crash falls into.
            raise RuntimeError("OOM-killed after dispatch")
        bl.LiveLoop.recover = boom
        try:
            out = await supervisor_run(30)
        finally:
            bl.LiveLoop.recover = real
        if i == 0:
            check("the crash propagated to the supervisor, as designed",
                  out.get("why"), "CRASHED")
        now = await counters(pool)
        if before is not None:
            check("crash %d: no counter moved backwards" % (i + 1),
                  all(y >= x for x, y in zip(before, now)), True)
        before = now
        await run_on(pool)
    print("   (d) after 3 crashes  : row %s" % (before,))
    check("crashes spent the allowance rather than resetting it",
          before[2] > 0, True)
    check("and never exceeded the ceiling", before[2] <= 160, True)
    await stop_now(pool)
    await settle()


async def L5_full_path(pool):
    rule("L5  LISTING -> ENRICHMENT -> SELECTION -> STREAM -> PERSISTED "
         "DECISIONS")
    await wipe_observation(pool)
    fx = Fixtures()
    m = RecordedMarkets(fx)
    install(fx, m)

    # THE SELECTION RULE IS PINNED, NOT RE-DERIVED. If a repair ever moves
    # a threshold to make a test pass, this fails first.
    check("selection rule version unchanged", uni.UNIVERSE_VERSION,
          "BETTOR_UNIVERSE_V1")
    check("MIN_SPREAD_TICKS unchanged", uni.MIN_SPREAD_TICKS, 2)
    check("MAX_PRICE unchanged", uni.MAX_PRICE, 0.50)
    check("MIN_SHARES_TRADED unchanged", uni.MIN_SHARES_TRADED, 100.0)
    check("MAX_MARKETS unchanged", uni.MAX_MARKETS, 100)

    await arm(pool)
    bl._reset_backoff()
    await supervisor_run(45)
    subs = sorted({s for f in ReplayStream.made for s in f._subs})
    updates = sum(f.updates for f in ReplayStream.made)
    await stop_now(pool)
    await settle()
    n = await journal(pool)
    rows = await pool.fetch(
        "SELECT status, record->>'evidence_class' AS ec, count(*) AS n "
        "FROM bettor_live_journal GROUP BY 1,2 ORDER BY n DESC")
    print("   venue   : %d listing, %d BBO over %d distinct"
          % (m.list_calls, len(m.bbo_calls), m.distinct))
    print("   selected: %d subscribed; %d recorded frames ingested"
          % (len(subs), updates))
    for r in rows:
        print("   journal : %-12s %-18s %d" % (r["status"], r["ec"], r["n"]))
    check("the listing produced candidates", m.list_calls >= 1, True)
    check("enrichment read every candidate once at least",
          m.distinct >= len(subs), True)
    check("the rule admitted a non-empty subset", 0 < len(subs), True)
    check("and admitted no more than it was offered",
          len(subs) <= m.distinct, True)
    check("recorded frames reached the production ingest", updates > 0, True)
    check("DECISIONS ARE PERSISTED", n > 0, True)
    check("every record is labelled REPLAY_DECISION, never "
          "PROSPECTIVE_SHADOW", {r["ec"] for r in rows}, {"REPLAY_DECISION"})
    check("no order was submitted", os.environ.get("MAX_CONTRACTS", "0"), "0")


async def L6_malformed_refusals(pool):
    rule("L6  MISSING AND MALFORMED FIELDS PRODUCE EXPLICIT NAMED REFUSALS")
    await wipe_observation(pool)
    fx = Fixtures(n=12)
    victims = fx.slugs[:6]

    def drop_bid(md):
        md.pop("bestBid", None)
        return md

    def drop_state(md):
        md.pop("state", None)
        return md

    def junk_price(md):
        md["bestAsk"] = {"value": "not-a-number", "currency": "USD"}
        return md

    def drop_volume(md):
        md.pop("sharesTraded", None)
        return md

    def tiny_volume(md):
        md["sharesTraded"] = "1.0"
        return md

    def expensive(md):
        md["bestAsk"] = {"value": "0.9900", "currency": "USD"}
        md["bestBid"] = {"value": "0.9000", "currency": "USD"}
        return md

    def closed_flag(md):
        md["state"] = "MARKET_STATE_CLOSED"
        return md

    def crossed(md):
        md["bestBid"] = {"value": "0.6000", "currency": "USD"}
        md["bestAsk"] = {"value": "0.2000", "currency": "USD"}
        return md

    mangle = dict(zip(victims, [drop_bid, closed_flag, junk_price,
                                drop_volume, tiny_volume, expensive]))
    m = RecordedMarkets(fx, mangle=mangle)
    install(fx, m)
    await arm(pool)
    bl._reset_backoff()
    await supervisor_run(40)
    subs = {s for f in ReplayStream.made for s in f._subs}
    await stop_now(pool)
    await settle()

    # THE RULE'S OWN VERDICTS, asserted as the rule actually defines them
    # -- not as I first guessed. `assess()` returns `included`, and its
    # ordering is: closed/state, then one-sided, then unparsable, then
    # spread, price, volume. Two of these are deliberately NOT what a
    # naive reading expects, and both are reported rather than "fixed".
    expected = {
        victims[0]: uni.R_ONE_SIDED,     # bestBid absent
        victims[1]: uni.R_NOT_OPEN,      # state says CLOSED
        victims[2]: uni.R_ONE_SIDED,     # junk price -> _f() None -> one-sided
        victims[3]: uni.R_NO_VOLUME,     # sharesTraded absent
        victims[4]: uni.R_VOLUME,        # below MIN_SHARES_TRADED
        victims[5]: uni.R_PRICE,         # ask above MAX_PRICE
    }
    for slug, want in expected.items():
        md = mangle[slug](dict(fx.bbo[slug]))
        row = {"slug": slug, "bestBid": md.get("bestBid"),
               "bestAsk": md.get("bestAsk"),
               "sharesTraded": md.get("sharesTraded"),
               "venue_state": md.get("state")}
        got = uni.assess(row)
        check("refusal is named %-22s" % want, got.get("reason"), want)
        check("   ... and it is NOT included", got.get("included"), False)

    # a crossed book is refused as UNPARSABLE, which is the path a junk
    # price does NOT take
    cr = uni.assess({"slug": "x", **{k: v for k, v in
                                     crossed(dict(fx.bbo[victims[0]])).items()
                                     if k in ("bestBid", "bestAsk")},
                     "sharesTraded": "500", "venue_state":
                     "MARKET_STATE_OPEN"})
    check("a crossed book is refused PRICE_UNPARSABLE", cr.get("reason"),
          uni.R_UNPARSABLE)
    check("   ... and it is NOT included", cr.get("included"), False)

    # and an EMPTY body -- no fields at all -- is refused, not admitted
    empty = uni.assess({})
    check("an empty body is refused, not admitted", empty.get("included"),
          False)
    check("   ... named PRICE_UNPARSABLE (no slug to blame the book)",
          empty.get("reason"), uni.R_UNPARSABLE)

    # ── WHAT MAY AND MAY NOT BE SUBSCRIBED ───────────────────────────
    #
    # This was ONE check -- "no mangled market reached the stream" --
    # and it now fails, because the observation subset deliberately
    # watches markets the rule REFUSED so the transport can be verified
    # when the strategy admits nothing. Keeping it as written would be
    # asserting the circular dependency the repair exists to remove.
    #
    # It is restated as TWO checks along the line the whole repair
    # rests on, and the safety half is not weakened:
    #
    #   a body we could not READ            -> never subscribed
    #   a body that reads and is merely
    #   unattractive to the strategy        -> MAY be subscribed, and
    #                                          carries its refusal on
    #                                          every record
    never = {victims[1],        # state says CLOSED -- not open
             victims[2]}        # bestAsk present and unreadable
    check("a closed or unreadable market is NEVER subscribed",
          subs & never, set())
    economic = {victims[0],     # genuinely one-sided (bestBid absent)
                victims[3],     # no volume figure
                victims[4],     # volume below the minimum
                victims[5]}     # ask above the maximum price
    watched = subs & economic
    print("   economically refused markets watched: %d of %d"
          % (len(watched), len(economic)))
    # EVERY ONE OF THEM CARRIES ITS REFUSAL, durably, on every record
    # it produced -- that is what makes watching it evidence rather
    # than a relaxation.
    rows_ = await journal_rows(pool, watched) if watched else []
    if watched:
        check("every watched refusal is journalled as OBSERVATION_ONLY",
              all(r.get("observation_basis") == "OBSERVATION_ONLY"
                  for r in rows_), True)
        check("   ... and names the rule's reason",
              all(r.get("universe_rule_reason") for r in rows_), True)
        check("   ... under the frozen rule version",
              {r.get("universe") for r in rows_}, {uni.UNIVERSE_VERSION})
    check("a malformed body never raised out of the probe",
          len(m.bbo_calls) >= len(victims), True)

    # ── TWO FINDINGS, REPORTED RATHER THAN PAPERED OVER ────────────────
    print("\n   FINDING L6-1  a BBO body with NO `state` field is NOT")
    print("     refused. `assess()` refuses only when state is present and")
    print("     not OPEN; a missing state falls through to the listing's")
    print("     `active` flag. Deliberate -- the same function assesses")
    print("     listing rows, which carry `active` and no `state` -- but it")
    print("     means a BBO that omits `state` is admitted on a flag the")
    print("     LISTING set earlier, not on anything the BBO confirmed.")
    no_state = uni.assess({"slug": "x", "bestBid": {"value": "0.20"},
                           "bestAsk": {"value": "0.30"},
                           "sharesTraded": "500"})
    print("     measured: reason=%r included=%r"
          % (no_state.get("reason"), no_state.get("included")))
    check("FINDING L6-1 is real and is recorded here",
          no_state.get("included"), True)
    print("     NOT CHANGED in this run: tightening it would alter the")
    print("     frozen selection rule. `state` is present on 30,590 of")
    print("     30,590 recorded BBO bodies, so it is unobserved in")
    print("     practice. Recommended as a separate, reviewable change.")

    print("\n   FINDING L6-2  an UNPARSABLE price is reported as")
    print("     ONE_SIDED_BOOK, because `_f()` returns None for junk and")
    print("     the one-sided test runs first: `R_ONE_SIDED if slug else")
    print("     R_UNPARSABLE`. The market IS refused -- only the reason")
    print("     code is imprecise, and reason codes are what the counters")
    print("     are grouped by. No behavioural risk; misleading telemetry.")


async def L7_db_outage_and_lost_ack(pool, dsn):
    rule("L7  A DATABASE OUTAGE AND A LOST ACKNOWLEDGEMENT FABRICATE NO "
         "WRITE AND INFLATE NO COUNT")
    await wipe_observation(pool)
    fx = Fixtures(n=8)

    # (a) A REAL DATABASE-SIDE OUTAGE, not a patched function. The
    # allowance table is renamed out from under the running worker, so
    # every reservation statement fails AT THE SERVER and the production
    # handler in `ctl.reserve` is the thing under test. Patching
    # `ctl.reserve` itself -- which an earlier revision of this file did
    # -- tests the harness's own stub and skips the code that matters.
    m = RecordedMarkets(fx)
    install(fx, m)
    await arm(pool)
    await pool.execute(
        "ALTER TABLE ingestion_state RENAME TO ingestion_state_outage")
    try:
        bl._reset_backoff()
        out = await supervisor_run(60)
    finally:
        await pool.execute(
            "ALTER TABLE ingestion_state_outage RENAME TO ingestion_state")
    await settle()
    print("   (a) reservation raises: started=%s why=%s venue %d/%d"
          % (out.get("started"), out.get("why"), m.list_calls,
             len(m.bbo_calls)))
    check("a database outage refused the run, it did not crash it",
          out.get("started"), False)
    # CONTROL_UNREADABLE, not DISCOVERY_FAILED. The outage takes out the
    # control read, which is the FIRST gate -- before the allowance, before
    # discovery, before any client is built. Refusing there is earlier and
    # safer than refusing at discovery, which is what I had expected.
    check("and it is named, not swallowed", out.get("why"),
          "CONTROL_UNREADABLE")
    check("the earliest gate is the one that caught it",
          bl.is_control_reason(out.get("why")), True)
    check("and issued ZERO listing requests", m.list_calls, 0)
    check("and issued ZERO BBO requests", len(m.bbo_calls), 0)
    check("and the counters survived the outage untouched",
          await counters(pool), (0, 0, 0))
    # the outage verdict itself, from the production handler
    await pool.execute(
        "ALTER TABLE ingestion_state RENAME TO ingestion_state_outage")
    try:
        r = await ctl.reserve(pool, ctl.R_LISTING, probe_id="whatever")
    finally:
        await pool.execute(
            "ALTER TABLE ingestion_state_outage RENAME TO ingestion_state")
    print("   (a) outage verdict    : %s / uncertain=%s"
          % (r["why"], r.get("uncertain")))
    check("a server-side failure is UNREADABLE, never granted",
          (ctl.granted(r), r["why"]), (False, ctl.V_UNREADABLE))
    check("and the unit is treated as SPENT, not refunded",
          "SPENT" in (r.get("detail") or ""), True)

    # (b) a reservation whose COMMIT is lost must not be treated as granted
    res = await ctl.reserve(pool, ctl.R_LISTING, probe_id="not-the-armed-one")
    print("   (b) wrong identity    : %s" % res["why"])
    check("a mismatched identity is refused", ctl.granted(res), False)
    check("and is named, not swallowed", res["why"], ctl.V_MISMATCH)
    check("and spent nothing", await counters(pool), (0, 0, 0))

    # (c) A FAILING FLUSH, failed AT THE DATABASE. The journal table is
    # renamed out from under a RUNNING loop, after it has booted and
    # verified its schema, so `PgStore.flush`'s own handler is what runs.
    # Patching the store method -- which an earlier revision did -- again
    # only tests the harness's stub.
    await wipe_observation(pool)
    m3 = RecordedMarkets(fx)
    install(fx, m3)
    await arm(pool)
    bl._reset_backoff()
    task = asyncio.create_task(bl.main())
    await asyncio.sleep(6)                      # boot, decide, start writing
    n_before = await journal(pool)
    await pool.execute("ALTER TABLE bettor_live_journal "
                       "RENAME TO bettor_live_journal_outage")
    try:
        await asyncio.sleep(10)                 # several flush intervals
        await stop_now(pool)
        try:
            out3 = await asyncio.wait_for(task, timeout=90)
        except asyncio.TimeoutError:
            task.cancel()
            out3 = {}
        n_during = await pool.fetchval(
            "SELECT count(*) FROM bettor_live_journal_outage")
    finally:
        await pool.execute("ALTER TABLE bettor_live_journal_outage "
                           "RENAME TO bettor_live_journal")
    await settle()
    d = report_of(out3).get("durability") or {}
    print("   (c) journal table gone mid-flight: rows before=%d after=%d, "
          "records_written=%s flush_failures=%s error=%s"
          % (n_before, n_during, d.get("records_written"),
             d.get("flush_failures"), d.get("last_flush_error")))
    check("the outage did not crash the loop", isinstance(out3, dict), True)
    check("NO row was fabricated while the table was gone",
          n_during, n_before)
    check("the failure is COUNTED, not swallowed",
          (d.get("flush_failures") or 0) > 0, True)
    check("and the error is named", d.get("last_flush_error") is not None,
          True)
    check("records_written never counted a failed batch",
          (d.get("records_written") or 0) <= n_during, True)
    check("the batch was RETAINED, not dropped",
          (d.get("uncommitted_records") or 0) > 0
          or (d.get("records_dropped") or 0) == 0, True)


async def L8_stop_and_deadline(pool):
    rule("L8  STOP AND DEADLINE EXPIRY END ACQUISITION AND CLOSE THE "
         "TRANSPORT")
    await wipe_observation(pool)
    fx = Fixtures(n=6)
    m = RecordedMarkets(fx)
    install(fx, m)
    await arm(pool)
    bl._reset_backoff()
    task = asyncio.create_task(bl.main())
    for _ in range(300):                       # wait for a live transport
        if any(f.connected for f in ReplayStream.made):
            break
        await asyncio.sleep(0.1)
    check("a transport was open before the stop",
          any(f.connected for f in ReplayStream.made), True)
    at_stop = (m.list_calls, len(m.bbo_calls))
    t0 = time.monotonic()
    await stop_now(pool)
    closed = None
    for _ in range(900):
        if all(not f.connected for f in ReplayStream.made):
            closed = time.monotonic() - t0
            break
        await asyncio.sleep(0.1)
    task.cancel()
    try:
        await task
    except BaseException:
        pass
    after = (m.list_calls, len(m.bbo_calls))
    print("   stop: transport closed in %s s; venue %s -> %s"
          % (None if closed is None else round(closed, 2), at_stop, after))
    check("the transport closed after obs-stop", closed is not None, True)
    check("within 90 s", (closed or 999) <= 90.0, True)
    check("and NO further venue request was issued", after, at_stop)

    # the deadline, on its own
    await wipe_observation(pool)
    m2 = RecordedMarkets(fx)
    install(fx, m2)
    pid = await arm(pool)
    await pool.execute(
        "UPDATE ingestion_state SET value = jsonb_set(value, "
        "'{deadline_at}', to_jsonb(to_char((now() - interval '1 second')"
        " AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS+00:00'))) "
        "WHERE key=$1", ctl.BUDGET_KEY)
    bl._reset_backoff()
    out = await supervisor_run(40)
    ctlv = await pool.fetchval(
        "SELECT value::text FROM ingestion_state WHERE key=$1",
        ctl.CONTROL_KEY)
    print("   deadline: started=%s why=%s control now %s"
          % (out.get("started"), out.get("why"), ctlv))
    check("an expired probe does not start", out.get("started"), False)
    check("it issued no request", (m2.list_calls, len(m2.bbo_calls)), (0, 0))
    check("it disarmed the control", ctlv, "false")
    await run_on(pool)      # isolate the deadline gate from the control
    res = await ctl.reserve(pool, ctl.R_LISTING, probe_id=pid)
    check("the DEADLINE gate refuses on its own, control back ON",
          (ctl.granted(res), res["why"]), (False, ctl.V_EXPIRED))
    await stop_now(pool)


async def L9_deadline_survives_disarm_failure(pool):
    rule("L9  DEADLINE ENFORCEMENT SURVIVES A FAILURE TO WRITE THE "
         "DISARMED STATE")
    await wipe_observation(pool)
    fx = Fixtures(n=6)
    m = RecordedMarkets(fx)
    install(fx, m)
    pid = await arm(pool)
    await pool.execute(
        "UPDATE ingestion_state SET value = jsonb_set(value, "
        "'{deadline_at}', to_jsonb(to_char((now() - interval '1 second')"
        " AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS+00:00'))) "
        "WHERE key=$1", ctl.BUDGET_KEY)

    # A REAL WRITE FAILURE, enforced by the database itself: a trigger that
    # raises on any attempt to set the control row to false. Not a patched
    # Python function -- the write genuinely cannot succeed.
    await pool.execute("""
        CREATE OR REPLACE FUNCTION bettor_block_disarm() RETURNS trigger AS $$
        BEGIN
          IF NEW.key = 'bettor_live_observation'
             AND NEW.value::text = 'false' THEN
            RAISE EXCEPTION 'disarm write blocked by the harness';
          END IF;
          RETURN NEW;
        END $$ LANGUAGE plpgsql;""")
    await pool.execute(
        "DROP TRIGGER IF EXISTS bettor_block_disarm_t ON ingestion_state")
    await pool.execute(
        "CREATE TRIGGER bettor_block_disarm_t BEFORE INSERT OR UPDATE ON "
        "ingestion_state FOR EACH ROW EXECUTE FUNCTION "
        "bettor_block_disarm()")
    try:
        bl._reset_backoff()
        out = await supervisor_run(40)
        await settle()
        ctlv = await pool.fetchval(
            "SELECT value::text FROM ingestion_state WHERE key=$1",
            ctl.CONTROL_KEY)
        res = await ctl.reserve(pool, ctl.R_LISTING, probe_id=pid)
        print("   disarm blocked: why=%s control still %s; reservation %s"
              % (out.get("why"), ctlv, res["why"]))
        check("local acquisition still stopped", out.get("started"), False)
        check("and named the deadline", out.get("why"), "DEADLINE_PASSED")
        check("no venue request was issued",
              (m.list_calls, len(m.bbo_calls)), (0, 0))
        check("the control was genuinely NOT written", ctlv, "true")
        check("every transport is closed", all(not f.connected
                                               for f in ReplayStream.made),
              True)
        check("and the DATABASE DEADLINE goes on refusing regardless",
              (ctl.granted(res), res["why"]), (False, ctl.V_EXPIRED))
    finally:
        await pool.execute(
            "DROP TRIGGER IF EXISTS bettor_block_disarm_t ON ingestion_state")
        await pool.execute("DROP FUNCTION IF EXISTS bettor_block_disarm()")


async def L11_bounded(pool):
    rule("L11  RESOURCE USE, QUEUES, RETRIES AND RETENTION ARE BOUNDED")
    await wipe_observation(pool)
    fx = Fixtures(n=10)
    m = RecordedMarkets(fx)
    install(fx, m)
    await arm(pool)
    bl._reset_backoff()
    out = await run_until_stopped(pool, work_s=12.0)
    await settle()
    check("main() returned its own report after the stop",
          out.get("started"), True)
    d = report_of(out).get("durability") or {}
    bounds = d.get("bounds") or {}
    print("   bounds reported: %s" % json.dumps(bounds))
    check("the outbox is capped", bounds.get("outbox_max"),
          store_mod.OUTBOX_MAX)
    check("the cursor cache is capped", bounds.get("cursor_cache_max"),
          store_mod.CURSOR_MAX_IN_MEMORY)
    check("the records ring is bounded",
          isinstance(bounds.get("records_ring"), int), True)
    check("the touches ring is bounded",
          isinstance(bounds.get("touches_ring"), int), True)
    check("nothing uncommitted was left behind after the stop",
          (d.get("uncommitted_records") or 0) == 0, True)
    check("no record was dropped at this volume",
          d.get("records_dropped"), 0)
    check("no window was marked incomplete", d.get("windows_incomplete"),
          False)
    # retries are bounded by construction, and the numbers are reported
    cfg = bl.effective_config()
    print("   retries: listing %s, probe %s; backoff ladder %s"
          % (cfg["listing_max_retries"], cfg["probe_max_retries"],
             list(bl.NOSTART_BACKOFF_S)))
    check("listing retries are bounded",
          cfg["listing_max_retries"] <= 3, True)
    check("probe retries are bounded", cfg["probe_max_retries"] <= 3, True)
    check("the control poll never escalates", bl.IDLE_POLL_S, 30.0)
    check("and the acquisition ladder is finite",
          len(bl.NOSTART_BACKOFF_S) <= 8, True)


# ── the two phases that need a REAL process boundary ────────────────────
async def L2A_arm_and_spend(pool):
    """First process: arm, spend part of the allowance, exit."""
    rule("L2/L10 (process 1)  ARM, SPEND, PERSIST, EXIT")
    await wipe_observation(pool)
    fx = Fixtures(n=8)
    m = RecordedMarkets(fx)
    install(fx, m)
    pid = await arm(pool, minutes=30)
    bl._reset_backoff()
    await supervisor_run(40)
    await stop_now(pool)
    await settle()
    b = await budget(pool)
    n = await journal(pool)
    cur = await pool.fetchval("SELECT count(*) FROM bettor_live_cursor")
    state = {"probe_id": b["probe_id"], "deadline_at": b["deadline_at"],
             "counters": [b["listing_attempts_reserved"],
                          b["distinct_reserved"],
                          b["bbo_attempts_reserved"]],
             "journal": n, "cursors": cur}
    print("   PROCESS_1_STATE=%s" % json.dumps(state))
    check("process 1 armed an identity", bool(pid), True)
    check("process 1 spent allowance", state["counters"][2] > 0, True)
    check("process 1 committed evidence", n > 0, True)
    check("process 1 recorded settlement obligations", cur > 0, True)
    return state


async def L2B_restart(pool, expect):
    """Second process: a genuinely new interpreter, same database."""
    rule("L2/L10 (process 2)  A NEW PROCESS RESUMES THE SAME PROBE")
    b = await budget(pool)
    n = await journal(pool)
    cur = await pool.fetchval("SELECT count(*) FROM bettor_live_cursor")
    print("   before any work: probe_id=%s deadline=%s counters=%s"
          % (b["probe_id"][:8], b["deadline_at"],
             [b["listing_attempts_reserved"], b["distinct_reserved"],
              b["bbo_attempts_reserved"]]))
    check("the probe IDENTITY survived the restart", b["probe_id"],
          expect["probe_id"])
    check("the ABSOLUTE DEADLINE survived unchanged", b["deadline_at"],
          expect["deadline_at"])
    check("the counters survived unchanged",
          [b["listing_attempts_reserved"], b["distinct_reserved"],
           b["bbo_attempts_reserved"]], expect["counters"])
    check("committed evidence survived the restart", n, expect["journal"])
    check("outstanding outcome obligations survived", cur, expect["cursors"])

    # and the loop RECOVERS them rather than starting blank
    fx = Fixtures(n=8)
    m = RecordedMarkets(fx)
    install(fx, m)
    await run_on(pool)
    bl._reset_backoff()
    out = await run_until_stopped(pool, work_s=12.0)
    await settle()
    d = report_of(out).get("durability") or {}
    rec = d.get("recovered_at_start") or {}
    b2 = await budget(pool)
    print("   recovered_at_start: %s" % json.dumps(rec))
    check("the loop RECOVERED the committed journal",
          rec.get("journal_rows"), expect["journal"])
    check("and RECOVERED the outstanding cursors",
          rec.get("cursors_recovered"), expect["cursors"])
    check("the deadline is STILL the original one", b2["deadline_at"],
          expect["deadline_at"])
    check("the identity is STILL the original one", b2["probe_id"],
          expect["probe_id"])
    check("and the restart did not replenish the allowance",
          all(y >= x for x, y in zip(
              expect["counters"],
              [b2["listing_attempts_reserved"], b2["distinct_reserved"],
               b2["bbo_attempts_reserved"]])), True)


# ══ L12: THE STOP RECEIPT, THROUGH THE REAL SHUTDOWN PATH ═══════════════
#
# THE GAP THIS CLOSES. Every other phase here drives `main()`, but none
# of them ever read `ingestion_state.bettor_live_stop_receipt`. The
# receipt -- and with it the active-close gate that decides whether a
# shutdown may be reported as having closed a live connection -- was
# covered by unit tests only. A unit test exercises `stop()`; it does
# not exercise `main()`'s finally block, `ctl.write_stop_receipt`, the
# jsonb round trip, or the identities the row is supposed to carry.
#
# TRANSPORT IS SIMULATED, EXPLICITLY. `ReplayStream` speaks to no
# venue. The four streams below stamp the SAME fields the real
# `_main` finally block stamps (`socket_closed_at`, `socket_close_ok`,
# `thread_exited_at`) so the SHUTDOWN BOOKKEEPING is exercised end to
# end -- the receipt, the gate and the persistence. It establishes
# nothing whatever about a real socket.

class _StampingStream(ReplayStream):
    """A replay transport that keeps the real closure bookkeeping.

    `ReplayStream._run` returns without stamping anything, which is
    honest for a replay -- there is no socket -- but it means the
    CLOSED path is never reached. These subclasses stamp exactly what
    `MarketStream._main`'s finally does, and nothing else.
    """

    close_ok = True
    close_error = None
    drop_before_stop = False     # go disconnected while still running
    hang_s = 0.0                 # ignore _stop for this long

    def _run(self):
        import datetime as _dt
        self.epoch += 1
        self.connected = True
        self.connected_since = ms._now_iso()
        self.first_connected_at = self.connected_since
        dropped_at = time.monotonic() + 1.0
        try:
            while not self._stop:
                if type(self).drop_before_stop and \
                        time.monotonic() > dropped_at:
                    # THE CONNECTION DIES WHILE THE LOOP RUNS ON. This
                    # is the case a clean close() cannot distinguish.
                    with self._lock:
                        self.connected = False
                with self._lock:
                    want = list(self._subs)
                for slug in want:
                    md = ReplayStream.frames.get(slug)
                    if md is None:
                        continue
                    md = dict(md)
                    md["transactTime"] = _dt.datetime.now(
                        _dt.timezone.utc).strftime(
                            "%Y-%m-%dT%H:%M:%S.%f000Z")
                    self._on_market_data({"marketData": md})
                for _ in range(5):
                    if self._stop:
                        break
                    time.sleep(0.1)
            if type(self).hang_s:
                # A THREAD THAT WILL NOT LEAVE. The join must time out
                # and the receipt must say so.
                time.sleep(type(self).hang_s)
        finally:
            with self._lock:
                self.connected = False
                # The same three stamps the production finally writes.
                self.socket_closed_at = time.time()
                self.socket_closed_at_iso = ms._now_iso()
                self.socket_close_ok = type(self).close_ok
                self.socket_close_error = type(self).close_error
                self.socket_closes += 1
                if self.thread_exited_at is None:
                    self.thread_exited_at = time.time()
                    self.thread_exited_at_iso = ms._now_iso()


async def receipt(pool):
    v = await pool.fetchval("SELECT value FROM ingestion_state WHERE key=$1",
                            ctl.RECEIPT_KEY)
    return json.loads(v) if isinstance(v, str) else v


async def _run_to_receipt(pool, cls, *, wait_for_connect=True,
                          settle_s=30.0):
    """Arm, run `main()` for real, stop it, return the PERSISTED row."""
    await wipe_observation(pool)
    await pool.execute("DELETE FROM ingestion_state WHERE key=$1",
                       ctl.RECEIPT_KEY)
    fx = Fixtures(n=6)
    m = RecordedMarkets(fx)
    cls.made = ReplayStream.made
    ms.MarketStream = cls
    bl.ms.MarketStream = cls
    ReplayStream.made.clear()
    ReplayStream.frames = fx.book
    pmus._get_client = lambda: Client(m)
    pid = await arm(pool)
    bl._reset_backoff()
    task = asyncio.create_task(bl.main())
    if wait_for_connect:
        for _ in range(300):
            if any(f.connected for f in ReplayStream.made):
                break
            await asyncio.sleep(0.1)
    else:
        await asyncio.sleep(3.0)
    await stop_now(pool)
    try:
        await asyncio.wait_for(task, timeout=settle_s)
    except (asyncio.TimeoutError, BaseException):
        task.cancel()
        try:
            await task
        except BaseException:
            pass
    await asyncio.sleep(0.5)
    return pid, await receipt(pool)


async def L12_stop_receipt(pool):
    rule("L12  THE STOP RECEIPT IS WRITTEN, AND ONLY A LIVE CONNECTION "
         "MAY BE CALLED AN ACTIVE CLOSE")
    print("   TRANSPORT: SIMULATED. ReplayStream speaks to no venue; it "
          "stamps\n   the same closure fields the production finally "
          "block stamps, so the\n   RECEIPT AND THE GATE are exercised "
          "-- not a real socket.\n")

    # ── 1. A LIVE CONNECTION, CLOSED CLEANLY ─────────────────────────
    class Clean(_StampingStream):
        close_ok, close_error, drop_before_stop, hang_s = True, None, False, 0.0

    pid, r = await _run_to_receipt(pool, Clean)
    check("a receipt row was persisted", bool(r), True)
    if not r:
        return
    t = r.get("transport") or {}
    check("the receipt carries the ARMED probe id", r.get("probe_id"), pid)
    check("   ... and a boot id", bool(r.get("boot_id")), True)
    check("   ... matching the durability record",
          r.get("boot_id"), (r.get("durability") or {}).get("boot_id"))
    check("   ... and this process's pid", isinstance(r.get("pid"), int),
          True)
    check("   ... and its host", bool(r.get("host")), True)
    check("connection state was captured BEFORE the stop",
          t.get("connected_at_stop"), True)
    check("   ... with the connection's own identity",
          bool(t.get("connected_since")) and t.get("epoch_at_stop", 0) >= 1,
          True)
    check("the shutdown verdict is CLOSED", t.get("shutdown"), "CLOSED")
    check("   ... and it is reported as verified",
          r.get("shutdown_verified"), True)
    check("AN ACTIVE CLOSE IS CLAIMED, because one was exercised",
          r.get("active_close_exercised"), True)
    check("   ... so no NOT-EXERCISED note is carried",
          r.get("active_close_note"), None)
    check("the close latency is a subtraction, not a guess",
          isinstance(t.get("close_latency_s"), (int, float)), True)
    check("the control detection time is recorded",
          bool(r.get("control_detected_at")), True)
    check("the last outbound request time is recorded",
          bool(r.get("last_request_started_at")), True)
    check("no order was submitted", r.get("orders_submitted"), 0)

    # ── 2. THE CONNECTION WAS ALREADY DOWN ───────────────────────────
    #
    # close() still returns cleanly -- the ws object survives a drop --
    # so the verdict is CLOSED and the ACTIVE-CLOSE CLAIM MUST NOT BE.
    class Dropped(_StampingStream):
        close_ok, close_error, drop_before_stop, hang_s = True, None, True, 0.0

    _, r2 = await _run_to_receipt(pool, Dropped)
    t2 = (r2 or {}).get("transport") or {}
    check("a dropped connection still closes cleanly",
          t2.get("shutdown"), "CLOSED")
    check("   ... but connected_at_stop is FALSE",
          t2.get("connected_at_stop"), False)
    check("   ... and NO active close may be claimed",
          (r2 or {}).get("active_close_exercised"), False)
    check("   ... the receipt says NOT EXERCISED",
          "NOT EXERCISED" in ((r2 or {}).get("active_close_note") or ""),
          True)

    # ── 3. close() RAISED ────────────────────────────────────────────
    class Raised(_StampingStream):
        close_ok, close_error = False, "ConnectionResetError"
        drop_before_stop, hang_s = False, 0.0

    _, r3 = await _run_to_receipt(pool, Raised)
    t3 = (r3 or {}).get("transport") or {}
    check("a close that raised is INCOMPLETE",
          t3.get("shutdown"), "INCOMPLETE_CLOSE_RAISED")
    check("   ... not verified", (r3 or {}).get("shutdown_verified"), False)
    check("   ... the error is named",
          t3.get("socket_close_error"), "ConnectionResetError")
    check("   ... and no active close is claimed",
          (r3 or {}).get("active_close_exercised"), False)

    # ── 4. THE JOIN TIMED OUT ────────────────────────────────────────
    class Hangs(_StampingStream):
        close_ok, close_error = True, None
        drop_before_stop = False
        hang_s = bl.STREAM_CLOSE_WAIT_S + 10.0

    _, r4 = await _run_to_receipt(
        pool, Hangs, settle_s=bl.STREAM_CLOSE_WAIT_S + 40.0)
    t4 = (r4 or {}).get("transport") or {}
    check("a join that timed out is INCOMPLETE",
          t4.get("shutdown"), "INCOMPLETE_JOIN_TIMEOUT")
    check("   ... the thread is reported still alive",
          t4.get("thread_alive"), True)
    check("   ... not verified", (r4 or {}).get("shutdown_verified"), False)
    check("   ... and no active close is claimed",
          (r4 or {}).get("active_close_exercised"), False)

    ms.MarketStream = ReplayStream
    bl.ms.MarketStream = ReplayStream
    print("\n   ESTABLISHED: the receipt is written through main()'s real "
          "shutdown\n   path, carries the armed identities, and gates the "
          "active-close claim\n   on connection state read before the stop."
          "\n   NOT ESTABLISHED: anything about a real WebSocket.")


PHASES = {
    "L1": L1_disabled_startup,
    "L3": L3_reservation_precedes_dispatch,
    "L3B": L3B_listing_dispatch_reconciles,
    "L4": L4_no_replenishment,
    "L5": L5_full_path,
    "L6": L6_malformed_refusals,
    "L8": L8_stop_and_deadline,
    "L9": L9_deadline_survives_disarm_failure,
    "L11": L11_bounded,
    "L12": L12_stop_receipt,
}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", required=True)
    ap.add_argument("--state", default=None,
                    help="JSON from process 1, for the restart phase")
    a = ap.parse_args()

    fast_probe()
    pool = await get_pool()
    dsn = os.environ.get("DATABASE_URL", "")

    print("image  : %s" % __import__("sportsassets").__file__)
    print("entry  : bettor_live_loop.main() with NO ARGUMENTS")
    print("db     : %s" % await pool.fetchval("SELECT current_database()"))
    for k, v in Fixtures(n=1).describe().items():
        print("%-16s %s" % (k, v))

    if a.phase == "L2A":
        state = await L2A_arm_and_spend(pool)
        print("STATE_JSON:" + json.dumps(state))
    elif a.phase == "L2B":
        await L2B_restart(pool, json.loads(a.state))
    elif a.phase == "L7":
        await L7_db_outage_and_lost_ack(pool, dsn)
    elif a.phase in PHASES:
        await PHASES[a.phase](pool)
    else:
        print("unknown phase %s" % a.phase)
        return 2

    rule("PHASE %s RESULT" % a.phase)
    if FAILS:
        print("FAILED %d of %d checks:" % (len(FAILS), CHECKS))
        for lbl, got, want in FAILS:
            print("  - %s: got %r want %r" % (lbl, got, want))
        return 1
    print("all %d checks passed (SIMULATED TRANSPORT -- not a live venue "
          "connection)" % CHECKS)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
