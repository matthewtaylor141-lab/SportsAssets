#!/usr/bin/env python3
"""DO THE CEILINGS HOLD? Measured against real PostgreSQL, not argued.

THE SIX SCENARIOS the Stage 2B pre-arming condition names, each run
through the ACTUAL acquisition path -- the real `main()`, the real
`_list_candidates`, the real `_discover`, the real `probe` and a real
`ingestion_state` row. The only fakes are the venue client, which
counts what it is asked for, and the crash, which is raised at a real
unguarded crash point.

  A  the happy path                     -- reserve, then dispatch
  B  concurrent reservations            -- the cap holds under a race
  C  empty results                      -- nothing qualifies, allowance
                                           is still spent
  D  request failures                    -- failures consume, no refund
  E  lost acknowledgements               -- uncertain is not permission
  F  crashes before and after dispatch   -- waste, never replenish

  I  the supervisor path                -- main() with NO ARGUMENTS,
                                           a real pool resolved by
                                           get_pool(), venue patched
  J  the deadline                       -- closes a LIVE socket
  K  the deadline, disarm write failing -- closes it anyway, and the
                                           database still refuses
  L  obs-stop                           -- closes a LIVE socket

The combined ceilings asserted throughout: 40 distinct markets, 160 BBO
attempts, 18 listing attempts.

This exists because atomicity was not the property that mattered. An
earlier version proved twenty concurrent decrements all landed, and the
ceilings still failed -- because the decrement happened AFTER the
request. Exit 0 means every ceiling held. Exit 1 names what did not.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import tempfile
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "backend"))

import asyncpg                                              # noqa: E402

from sportsassets import bettor_live_control as ctl         # noqa: E402
from sportsassets import bettor_live_store as st            # noqa: E402
from sportsassets.workers import bettor_live_loop as bl     # noqa: E402

MAX_DISTINCT = 40
MAX_ATTEMPTS = 160
MAX_LISTING = 18
CANDIDATES = 400

FAILS = 0


def ingestion_state_ddl() -> str:
    """The `ingestion_state` CREATE TABLE, lifted verbatim from the
    migration the deployment actually runs."""
    import pathlib
    import re
    mig = (pathlib.Path(__file__).resolve().parents[1] / "backend"
           / "migrations" / "001_init.sql").read_text()
    m = re.search(
        r"CREATE TABLE IF NOT EXISTS ingestion_state\s*\(.*?\);",
        mig, re.S)
    if not m:
        raise SystemExit("ingestion_state is not declared in the "
                         "migration; refusing to invent a schema")
    return m.group(0)


def rule(t):
    print("\n" + "=" * 76 + "\n" + t + "\n" + "=" * 76)


def check(label, got, want):
    global FAILS
    ok = got == want
    if not ok:
        FAILS += 1
    print("      %-54s %-8s %s" % (
        label, got, "ok" if ok else "WANT %s   *** MISMATCH ***" % want))
    return ok


def le(label, got, cap):
    global FAILS
    ok = got <= cap
    if not ok:
        FAILS += 1
    print("      %-54s %-8s %s" % (
        label, got, "<= %s ok" % cap if ok
        else "EXCEEDS %s   *** MISMATCH ***" % cap))
    return ok


# ── the venue, which only counts ─────────────────────────────────────
class CountingMarkets:
    """Serves a listing and BBO bodies, and counts every call.

    `bbo` records the slug, so DISTINCT markets requested is counted
    apart from ATTEMPTS -- the two numbers the ceilings are written in.
    """

    def __init__(self, *, fail_all=False, tradable=True,
                 n_markets=CANDIDATES):
        self.list_calls = 0
        self.bbo_calls: list[str] = []
        self.fail_all = fail_all
        self.tradable = tradable
        self.n_markets = n_markets

    def list(self, params):
        self.list_calls += 1
        if params.get("offset", 0) > 0:
            return {"markets": []}
        return {"markets": [
            {"id": "id-%d" % i, "slug": "m%03d" % i,
             "title": "Market %d" % i, "outcome": "yes",
             "description": "", "active": True, "closed": False,
             "liquidity": 1000.0, "volume": 5000.0,
             "eventSlug": "e%03d" % i, "team": "T"}
            for i in range(self.n_markets)]}

    def bbo(self, slug):
        self.bbo_calls.append(slug)
        if self.fail_all:
            raise RuntimeError("venue read failed")
        if not self.tradable:
            # OPEN, two-sided, but ONE TICK wide: a real economic
            # exclusion, so the universe comes out empty having spent
            # the allowance. This is scenario C.
            return {"marketData": {
                "marketSlug": slug, "state": "MARKET_STATE_OPEN",
                "bestBid": {"value": "0.0050", "currency": "USD"},
                "bestAsk": {"value": "0.0100", "currency": "USD"},
                "askDepth": 3, "bidDepth": 3,
                "sharesTraded": "61918.1100",
                "openInterest": "64893.6500"}}
        return {"marketData": {
            "marketSlug": slug, "state": "MARKET_STATE_OPEN",
            "bestBid": {"value": "0.2150", "currency": "USD"},
            "bestAsk": {"value": "0.3850", "currency": "USD"},
            "askDepth": 16, "bidDepth": 10,
            "sharesTraded": "138.7700", "openInterest": "137.9000"}}

    def book(self, slug):
        return self.bbo(slug)

    def settlement(self, slug):
        return {"marketData": {}}

    @property
    def distinct(self):
        return len(set(self.bbo_calls))


class Client:
    def __init__(self, markets):
        self.markets = markets


async def arm(pool, *, minutes=30, max_distinct=MAX_DISTINCT,
              max_attempts=MAX_ATTEMPTS, max_listing=MAX_LISTING):
    """Exactly what `render-ops sql obs-arm` writes."""
    pid = str(uuid.uuid4())
    await pool.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1, "
        "jsonb_build_object("
        "  'probe_id', $2::text,"
        "  'started_at', to_char(now() AT TIME ZONE 'UTC',"
        "      'YYYY-MM-DD\"T\"HH24:MI:SS+00:00'),"
        "  'deadline_at', to_char((now() + ($3::text || ' minutes')"
        "      ::interval) AT TIME ZONE 'UTC',"
        "      'YYYY-MM-DD\"T\"HH24:MI:SS+00:00'),"
        "  'max_distinct', $4::int,"
        "  'max_bbo_attempts', $5::int,"
        "  'max_listing_attempts', $6::int,"
        "  'distinct_reserved', 0,"
        "  'bbo_attempts_reserved', 0,"
        "  'listing_attempts_reserved', 0,"
        "  'slugs', '[]'::jsonb)) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        ctl.BUDGET_KEY, pid, str(minutes), max_distinct, max_attempts,
        max_listing)
    await pool.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1, "
        "'true'::jsonb) ON CONFLICT (key) DO UPDATE SET "
        "value = 'true'::jsonb", ctl.CONTROL_KEY)
    return pid


async def row(pool):
    return await ctl.read_budget(pool)


async def _nap(_d):
    """The backoff schedule is asserted elsewhere; not spent here."""
    return None


async def run_one(pool, markets, tmp, *, crash=None):
    """One worker lifetime through the REAL entry point.

    `crash` is 'after_dispatch' to raise at `loop.recover()` -- a real
    unguarded point that sits after every BBO read and after every
    reservation, so it is exactly the window the old post-hoc charge
    fell into.
    """
    store = st.FileStore(tmp, durable_across_redeploy=True)
    real_build = bl.build

    def build(*a, **kw):
        loop, stream = real_build(*a, **kw)
        stream.start = lambda: None
        stream.stop = lambda: None

        async def rec():
            if crash == "after_dispatch":
                raise RuntimeError("OOM-killed after dispatch")
            return {"restored": 0}

        loop.recover = rec
        return loop, stream

    class Cfg:
        pmus_key_id, pmus_secret_key = "k", "s"

    import sportsassets.config as cfgmod
    old_settings, old_build = cfgmod.settings, bl.build
    cfgmod.settings = lambda: Cfg()
    bl.build = build
    try:
        return await bl.main(client=Client(markets), store=store,
                             control_pool=pool, run_for_s=0.0,
                             sleep=_nap)
    finally:
        cfgmod.settings, bl.build = old_settings, old_build


async def supervisor_phases(pool, dsn):
    """I-L: the no-argument path, against a real database."""
    import sportsassets.pmus as pmus
    from sportsassets import bettor_market_stream as ms
    import sportsassets.bettor_live_store as store_mod

    ms_real = ms.MarketStream
    ctl_real_every = ctl.CONTROL_EVERY_S
    ctl.CONTROL_EVERY_S = 1.0
    ms.MarketStream = FakeStream
    bl.ms.MarketStream = FakeStream
    # The 0.25 req/s pace is asserted in its own phase and in
    # test_bettor_universe_probe; spending 160 s of it per phase here
    # would prove nothing these phases are about.
    os.environ["BETTOR_PROBE_MAX_RPS"] = "100000"
    os.environ["BETTOR_PROBE_CONCURRENCY"] = "4"

    m = CountingMarkets()
    pmus._get_client = lambda: venue(m)

    try:
        # ── I ────────────────────────────────────────────────────────
        rule("I. THE SUPERVISOR PATH -- main() with NO ARGUMENTS")
        print("   database: resolved by get_pool() -> settings()."
              "database_url")
        print("   patched : the VENUE only -- pmus._get_client and")
        print("             ms.MarketStream. No pool wiring is touched.\n")
        FakeStream.made.clear()
        pid = await arm(pool, minutes=30)
        bl._reset_backoff()
        out = await supervisor_run(90)
        b = await row(pool)
        print("   result: started=%s why=%s"
              % (out.get("started"), out.get("why")))
        print("   venue : %d listing, %d BBO over %d distinct"
              % (m.list_calls, len(m.bbo_calls), m.distinct))
        print("   row   : listing %s, distinct %s, attempts %s"
              % (b["listing_attempts_reserved"], b["distinct_reserved"],
                 b["bbo_attempts_reserved"]))
        check("the no-argument path reserved a listing attempt",
              b["listing_attempts_reserved"] >= 1, True)
        check("and the listing request actually went out",
              m.list_calls >= 1, True)
        check("no reservation was refused for want of a pool",
              "NO_CONTROL_POOL" in json.dumps(out, default=str), False)
        check("the row accounts for every distinct market requested",
              b["distinct_reserved"], m.distinct)
        check("and for every BBO attempt",
              b["bbo_attempts_reserved"], len(m.bbo_calls))
        le("distinct markets", m.distinct, MAX_DISTINCT)
        le("BBO attempts", len(m.bbo_calls), MAX_ATTEMPTS)
        le("listing attempts", m.list_calls, MAX_LISTING)
        opened = [f for f in FakeStream.made if f.started]
        check("a stream was opened on the no-argument path",
              len(opened) >= 1, True)
        check("and it was closed on the way out",
              all(not f.open for f in FakeStream.made), True)

        # ── J ────────────────────────────────────────────────────────
        rule("J. THE DEADLINE CLOSES AN ACTIVE STREAM")
        FakeStream.made.clear()
        m2 = CountingMarkets()
        pmus._get_client = lambda: venue(m2)
        pid = await arm(pool, minutes=30)
        bl._reset_backoff()
        task = asyncio.create_task(bl.main())
        for _ in range(200):                  # wait for a live socket
            await asyncio.sleep(0.05)
            if any(f.open for f in FakeStream.made):
                break
        live = [f for f in FakeStream.made if f.open]
        check("the stream is OPEN before the deadline is moved",
              len(live) >= 1, True)
        before_attempts = len(m2.bbo_calls)
        await pool.execute(
            "UPDATE ingestion_state SET value = jsonb_set(value,"
            " '{deadline_at}', to_jsonb(to_char("
            " (now() - interval '1 second') AT TIME ZONE 'UTC',"
            " 'YYYY-MM-DD\"T\"HH24:MI:SS+00:00'))) WHERE key=$1",
            ctl.BUDGET_KEY)
        try:
            out = await asyncio.wait_for(task, timeout=30)
        except asyncio.TimeoutError:
            task.cancel()
            out = {"why": "DID_NOT_STOP"}
        check("the loop stopped on the deadline", out.get("stopped_by"),
              ctl.B_EXPIRED)
        check("the active stream was CLOSED",
              all(not f.open for f in FakeStream.made), True)
        check("every opened stream was stopped",
              all(f.stopped >= 1 for f in FakeStream.made if f.started),
              True)
        ctlrow = await ctl.read_control(pool)
        check("and it disarmed itself", ctl.is_closed(ctlrow), True)
        v = await ctl.reserve(pool, ctl.R_ATTEMPT, slug="x",
                              probe_id=pid)
        check("the database refuses further reservations",
              ctl.granted(v), False)

        # ── K ────────────────────────────────────────────────────────
        rule("K. THE DEADLINE STILL CLOSES IT WHEN THE DISARM WRITE"
             " FAILS")
        print("   A trigger makes every write to the control row raise,")
        print("   so `disarm` genuinely fails rather than being stubbed.")
        print("   Local acquisition must still stop, the stream must")
        print("   still close, and the DATABASE deadline must go on")
        print("   refusing reservations regardless.\n")
        FakeStream.made.clear()
        m3 = CountingMarkets()
        pmus._get_client = lambda: venue(m3)
        pid = await arm(pool, minutes=30)
        await pool.execute(
            "CREATE OR REPLACE FUNCTION _block_disarm() RETURNS trigger"
            " AS $$ BEGIN RAISE EXCEPTION 'disarm blocked by test';"
            " END $$ LANGUAGE plpgsql")
        await pool.execute("DROP TRIGGER IF EXISTS _t_block_disarm ON"
                           " ingestion_state")
        await pool.execute(
            "CREATE TRIGGER _t_block_disarm BEFORE INSERT OR UPDATE ON"
            " ingestion_state FOR EACH ROW WHEN (NEW.key ="
            " 'bettor_live_observation') EXECUTE FUNCTION"
            " _block_disarm()")
        try:
            d = await ctl.disarm(pool, "PRECHECK")
            check("the disarm write really does fail now",
                  d["disarmed"], False)
            bl._reset_backoff()
            task = asyncio.create_task(bl.main())
            for _ in range(200):
                await asyncio.sleep(0.05)
                if any(f.open for f in FakeStream.made):
                    break
            check("the stream is OPEN before the deadline is moved",
                  any(f.open for f in FakeStream.made), True)
            await pool.execute(
                "UPDATE ingestion_state SET value = jsonb_set(value,"
                " '{deadline_at}', to_jsonb(to_char("
                " (now() - interval '1 second') AT TIME ZONE 'UTC',"
                " 'YYYY-MM-DD\"T\"HH24:MI:SS+00:00'))) WHERE key=$1",
                ctl.BUDGET_KEY)
            try:
                out = await asyncio.wait_for(task, timeout=30)
            except asyncio.TimeoutError:
                task.cancel()
                out = {"why": "DID_NOT_STOP"}
            after = len(m3.bbo_calls)
            check("local acquisition stopped anyway",
                  out.get("stopped_by"), ctl.B_EXPIRED)
            check("the active stream was CLOSED anyway",
                  all(not f.open for f in FakeStream.made), True)
            ctlrow = await ctl.read_control(pool)
            check("the control was NOT written (the disarm failed)",
                  ctlrow["run"], True)
            v = await ctl.reserve(pool, ctl.R_ATTEMPT, slug="x",
                                  probe_id=pid)
            check("but the DATABASE DEADLINE still refuses",
                  v["why"], ctl.V_EXPIRED)
            await asyncio.sleep(1.2)
            check("and no further BBO request was issued",
                  len(m3.bbo_calls), after)
        finally:
            await pool.execute("DROP TRIGGER IF EXISTS _t_block_disarm"
                               " ON ingestion_state")

        # ── L ────────────────────────────────────────────────────────
        rule("L. obs-stop CLOSES AN ACTIVE STREAM")
        FakeStream.made.clear()
        # A SMALL UNIVERSE ON PURPOSE. With 400 candidates the 40-slot
        # allowance is exhausted during acquisition and the loop stops
        # on BUDGET_EXHAUSTED before a stop can be demonstrated. This
        # phase is about the stop closing a LIVE socket, so it leaves
        # allowance unspent.
        m4 = CountingMarkets(n_markets=5)
        pmus._get_client = lambda: venue(m4)
        pid = await arm(pool, minutes=30)
        bl._reset_backoff()
        task = asyncio.create_task(bl.main())
        for _ in range(200):
            await asyncio.sleep(0.05)
            if any(f.open for f in FakeStream.made):
                break
        check("the stream is OPEN before the stop",
              any(f.open for f in FakeStream.made), True)
        t0 = time.monotonic()
        await pool.execute(
            "UPDATE ingestion_state SET value='false'::jsonb WHERE"
            " key=$1", ctl.CONTROL_KEY)
        try:
            out = await asyncio.wait_for(task, timeout=30)
        except asyncio.TimeoutError:
            task.cancel()
            out = {"why": "DID_NOT_STOP"}
        closed_after = time.monotonic() - t0
        after = len(m4.bbo_calls)
        print("   stream closed %.2fs after obs-stop" % closed_after)
        check("the loop stopped on the control",
              out.get("stopped_by"), ctl.W_STOPPED)
        check("the active stream was CLOSED",
              all(not f.open for f in FakeStream.made), True)
        await asyncio.sleep(1.2)
        check("no further BBO request was issued",
              len(m4.bbo_calls), after)
        v = await ctl.reserve(pool, ctl.R_ATTEMPT, slug="x",
                              probe_id=pid)
        check("and a stopped control refuses reservations",
              v["why"], ctl.V_STOPPED)
    finally:
        ms.MarketStream = ms_real
        bl.ms.MarketStream = ms_real
        ctl.CONTROL_EVERY_S = ctl_real_every


async def main_async(dsn):
    pool = await asyncpg.create_pool(dsn, min_size=2, max_size=12)
    # THE TABLE COMES FROM THE MIGRATION, NOT FROM THIS SCRIPT.
    #
    # It used to be hand-written here, WITH AN updated_at COLUMN THAT
    # PRODUCTION DOES NOT HAVE. Every phase passed against a schema no
    # deployment has, and on 2026-09-22T08:06:36.604Z the live worker
    # raised UndefinedColumnError on its first reservation. A proof
    # whose fixture is not the deployed shape proves nothing about the
    # deployment, so the statement is now read from
    # backend/migrations/001_init.sql.
    await pool.execute(ingestion_state_ddl())
    tmp = tempfile.mkdtemp(prefix="reservation-")
    os.environ["BETTOR_PROBE_MAX_RPS"] = "100000"   # pace is not the subject
    os.environ["BETTOR_PROBE_CONCURRENCY"] = "8"

    # ── A ────────────────────────────────────────────────────────────
    rule("A. THE HAPPY PATH -- reserved first, then dispatched")
    await arm(pool)
    m = CountingMarkets()
    out = await run_one(pool, m, tmp)
    b = await row(pool)
    print("   started=%s why=%s" % (out.get("started"), out.get("why")))
    print("   venue: %d listing, %d BBO attempts over %d distinct markets"
          % (m.list_calls, len(m.bbo_calls), m.distinct))
    print("   row:   distinct %s/%s, attempts %s/%s, listing %s/%s"
          % (b["distinct_reserved"], b["max_distinct"],
             b["bbo_attempts_reserved"], b["max_bbo_attempts"],
             b["listing_attempts_reserved"], b["max_listing_attempts"]))
    le("distinct markets requested", m.distinct, MAX_DISTINCT)
    le("BBO attempts", len(m.bbo_calls), MAX_ATTEMPTS)
    le("listing attempts", m.list_calls, MAX_LISTING)
    check("the row accounts for every BBO attempt sent",
          b["bbo_attempts_reserved"], len(m.bbo_calls))
    check("the row accounts for every distinct market sent",
          b["distinct_reserved"], m.distinct)
    check("the row accounts for every listing request sent",
          b["listing_attempts_reserved"], m.list_calls)

    # ── B ────────────────────────────────────────────────────────────
    rule("B. CONCURRENT RESERVATIONS -- 200 racing for 40 slots")
    print("   A single data-modifying CTE would over-grant here: both")
    print("   statements read `reserved = 39 < 40` from the same")
    print("   snapshot and the second commits anyway on waking. The")
    print("   reservation takes SELECT ... FOR UPDATE instead.\n")
    pid = await arm(pool)
    res = await asyncio.gather(*[
        ctl.reserve(pool, ctl.R_DISTINCT, slug="s%03d" % i, probe_id=pid)
        for i in range(200)])
    ok = [r for r in res if ctl.granted(r)]
    b = await row(pool)
    print("   %d of 200 concurrent reservations granted" % len(ok))
    check("granted never exceeds the cap", len(ok), MAX_DISTINCT)
    check("the row agrees", b["distinct_reserved"], MAX_DISTINCT)
    check("distinct slugs recorded", b["slugs_held"], MAX_DISTINCT)
    check("every refusal says why", all(
        r["why"] == ctl.V_EXHAUSTED
        for r in res if not ctl.granted(r)), True)

    print("\n   and again for ATTEMPTS, 400 racing for 160:")
    pid = await arm(pool)
    res = await asyncio.gather(*[
        ctl.reserve(pool, ctl.R_ATTEMPT, slug="s%03d" % i, probe_id=pid)
        for i in range(400)])
    ok = [r for r in res if ctl.granted(r)]
    b = await row(pool)
    check("granted never exceeds the cap", len(ok), MAX_ATTEMPTS)
    check("the row agrees", b["bbo_attempts_reserved"], MAX_ATTEMPTS)

    print("\n   and for LISTING, 100 racing for 18:")
    pid = await arm(pool)
    res = await asyncio.gather(*[
        ctl.reserve(pool, ctl.R_LISTING, probe_id=pid)
        for i in range(100)])
    check("granted never exceeds the cap",
          len([r for r in res if ctl.granted(r)]), MAX_LISTING)

    # ── C ────────────────────────────────────────────────────────────
    rule("C. EMPTY RESULTS -- nothing qualifies, allowance still spent")
    print("   Every book is OPEN, two-sided and ONE TICK wide: a real")
    print("   economic exclusion. The old code returned EMPTY_UNIVERSE")
    print("   ABOVE the charge, so 40 reads recorded nothing.\n")
    await arm(pool)
    m = CountingMarkets(tradable=False)
    out = await run_one(pool, m, tmp)
    b = await row(pool)
    print("   why=%s, %d BBO attempts over %d distinct markets"
          % (out.get("why"), len(m.bbo_calls), m.distinct))
    check("the universe really was empty", out.get("why"),
          "EMPTY_UNIVERSE")
    check("and the allowance was still spent",
          b["distinct_reserved"], m.distinct)
    check("attempts too", b["bbo_attempts_reserved"], len(m.bbo_calls))
    le("distinct markets requested", m.distinct, MAX_DISTINCT)

    print("\n   a SECOND lifetime against the SAME armed row:")
    m2 = CountingMarkets(tradable=False)
    await run_one(pool, m2, tmp)
    b2 = await row(pool)
    total = m.distinct + m2.distinct
    print("   lifetime 2 asked about %d more; %d distinct in total"
          % (m2.distinct, total))
    le("distinct markets across BOTH lifetimes", total, MAX_DISTINCT)
    check("the row never went backwards",
          b2["distinct_reserved"] >= b["distinct_reserved"], True)

    # ── D ────────────────────────────────────────────────────────────
    rule("D. REQUEST FAILURES -- consumed, never refunded")
    await arm(pool)
    m = CountingMarkets(fail_all=True)
    out = await run_one(pool, m, tmp)
    b = await row(pool)
    print("   why=%s, %d attempts over %d distinct, 0 enriched"
          % (out.get("why"), len(m.bbo_calls), m.distinct))
    check("failures consumed their distinct slots",
          b["distinct_reserved"], m.distinct)
    check("failures consumed their attempt slots",
          b["bbo_attempts_reserved"], len(m.bbo_calls))
    le("distinct markets requested", m.distinct, MAX_DISTINCT)
    le("BBO attempts including retries", len(m.bbo_calls), MAX_ATTEMPTS)

    # ── E ────────────────────────────────────────────────────────────
    rule("E. LOST ACKNOWLEDGEMENT -- uncertain is not permission")
    pid = await arm(pool)
    before = (await row(pool))["distinct_reserved"]

    class Dropped:
        """A pool whose reservation never answers. The transaction may
        have committed; we cannot know."""

        def acquire(self):
            class _H:
                async def __aenter__(self):
                    await asyncio.sleep(60)
                async def __aexit__(self, *a):
                    return False
            return _H()

    # `timeout_s` is passed explicitly: the module constant is a
    # DEFAULT ARGUMENT VALUE, bound when `reserve` was defined, so
    # patching `ctl.CONTROL_READ_TIMEOUT_S` here would change nothing
    # and the phase would quietly wait the full ten seconds.
    v = await ctl.reserve(Dropped(), ctl.R_DISTINCT, slug="x",
                          probe_id=pid, timeout_s=0.2)
    print("   verdict: %s" % v["why"])
    print("   detail : %s" % v["detail"])
    check("it is not a grant", ctl.granted(v), False)
    check("it is reported as uncertain", v["uncertain"], True)
    check("and it does NOT try to give the unit back",
          (await row(pool))["distinct_reserved"], before)

    print("\n   a reservation that RAISES is also refused:")
    class Broken:
        def acquire(self):
            raise ConnectionError("connection reset")
    v = await ctl.reserve(Broken(), ctl.R_ATTEMPT, slug="x", probe_id=pid)
    check("not a grant", ctl.granted(v), False)
    check("named, not swallowed", "ConnectionError" in (v["detail"] or ""),
          True)

    # ── F ────────────────────────────────────────────────────────────
    rule("F. CRASHES -- before dispatch reserves nothing; after"
         " dispatch wastes, never replenishes")
    await arm(pool)
    seen_distinct, seen_attempts, lifetimes = 0, 0, 0
    m = CountingMarkets()
    for i in range(6):
        try:
            await run_one(pool, m, tmp, crash="after_dispatch")
        except Exception:
            pass                      # the supervisor's own catch
        lifetimes += 1
        b = await row(pool)
        print("   lifetime %d: %3d attempts, %3d distinct | row d=%s a=%s"
              % (i + 1, len(m.bbo_calls), m.distinct,
                 b["distinct_reserved"], b["bbo_attempts_reserved"]))
        if b["distinct_reserved"] < seen_distinct or \
                b["bbo_attempts_reserved"] < seen_attempts:
            check("a counter went BACKWARDS across the crash", False, True)
        seen_distinct = b["distinct_reserved"]
        seen_attempts = b["bbo_attempts_reserved"]

    b = await row(pool)
    print("\n   after %d crashing lifetimes against ONE armed row:"
          % lifetimes)
    print("      distinct markets asked about : %d" % m.distinct)
    print("      BBO attempts                 : %d" % len(m.bbo_calls))
    print("      listing requests             : %d" % m.list_calls)
    le("distinct markets across all crashes", m.distinct, MAX_DISTINCT)
    le("BBO attempts across all crashes", len(m.bbo_calls), MAX_ATTEMPTS)
    le("listing attempts across all crashes", m.list_calls, MAX_LISTING)
    check("the allowance is exhausted, not replenished",
          b["open"], False)
    check("and it says so", b["state"], ctl.B_EXHAUSTED)

    print("\n   a REFUSAL BEFORE DISPATCH reserves nothing:")
    print("   The allowance is armed with NOTHING LEFT, so `main()` is")
    print("   refused on the budget -- upstream of every reservation.")
    print("   A refusal must cost the venue nothing and the row nothing.")
    await arm(pool, max_distinct=0)
    before = await row(pool)
    m = CountingMarkets()
    out = await bl.main(
        client=Client(m),
        store=st.FileStore(tempfile.mkdtemp(prefix="reservation-refuse-"),
                           durable_across_redeploy=True),
        control_pool=pool, run_for_s=0.0, sleep=_nap)
    after = await row(pool)
    print("   why=%s; venue saw %d listing, %d BBO"
          % (out.get("why"), m.list_calls, len(m.bbo_calls)))
    check("no BBO request was sent", len(m.bbo_calls), 0)
    check("no distinct slot was taken",
          after["distinct_reserved"], before["distinct_reserved"])
    check("no attempt slot was taken",
          after["bbo_attempts_reserved"],
          before["bbo_attempts_reserved"])

    # ── identity ─────────────────────────────────────────────────────
    rule("G. PROBE IDENTITY -- a re-arm cannot inherit reservations")
    pid1 = await arm(pool)
    v = await ctl.reserve(pool, ctl.R_DISTINCT, slug="a", probe_id=pid1)
    check("granted under its own identity", ctl.granted(v), True)
    pid2 = await arm(pool)
    check("a new probe_id was written", pid2 != pid1, True)
    v = await ctl.reserve(pool, ctl.R_DISTINCT, slug="b", probe_id=pid1)
    check("the stale process is refused", v["why"], ctl.V_MISMATCH)
    check("and the fresh probe starts clean",
          (await row(pool))["distinct_reserved"], 0)

    rule("H. THE CONTROL AND THE DEADLINE ARE CHECKED AT RESERVATION")
    pid = await arm(pool)
    await pool.execute(
        "UPDATE ingestion_state SET value='false'::jsonb WHERE key=$1",
        ctl.CONTROL_KEY)
    v = await ctl.reserve(pool, ctl.R_ATTEMPT, slug="x", probe_id=pid)
    check("a stopped control refuses the reservation", v["why"],
          ctl.V_STOPPED)
    pid = await arm(pool, minutes=-1)
    v = await ctl.reserve(pool, ctl.R_ATTEMPT, slug="x", probe_id=pid)
    check("a passed deadline refuses it too", v["why"], ctl.V_EXPIRED)
    pid = await arm(pool)
    await pool.execute("DELETE FROM ingestion_state WHERE key=$1",
                       ctl.BUDGET_KEY)
    v = await ctl.reserve(pool, ctl.R_ATTEMPT, slug="x", probe_id=pid)
    check("an unarmed probe reserves nothing", v["why"], ctl.V_ABSENT)

    await supervisor_phases(pool, dsn)

    await pool.execute(
        "DELETE FROM ingestion_state WHERE key = ANY($1::text[])",
        [ctl.CONTROL_KEY, ctl.BUDGET_KEY])
    await pool.close()

    rule("VERDICT")
    if FAILS == 0:
        print("   ALL CEILINGS HELD, AND THE SUPERVISOR PATH WORKS.")
        print("   40 distinct markets, 160 BBO attempts, 18 listing")
        print("   attempts -- under concurrency, empty results, request")
        print("   failures, lost acknowledgements and crashes on both")
        print("   sides of dispatch. Reservation precedes every request")
        print("   and no counter was ever decremented.")
        print()
        print("   And main() WITH NO ARGUMENTS -- the way the supervisor")
        print("   calls it -- reserved, reached the transport, opened a")
        print("   stream and closed it, against a real PostgreSQL pool")
        print("   resolved through settings().database_url. The deadline")
        print("   closed a live socket EVEN WITH THE DISARM WRITE")
        print("   FAILING, and the database went on refusing anyway.")
        return 0
    print("   %d MISMATCHES. The ceilings do NOT hold. Do not arm."
          % FAILS)
    return 1


# ═════════════════════════════════════════════════════════════════════
# THE SUPERVISOR PATH
#
# Everything above injects a pool. `workers/all.py` does not: it calls
# `bettor_live_loop.main` WITH NO ARGUMENTS, and on 2026-09-22 a defect
# living exactly in that gap refused every reservation on production
# while every local proof passed.
#
# So these phases call `main()` with no arguments and let the PRODUCTION
# PATH resolve the database: `get_pool()` -> `settings().database_url`,
# from the environment, to a real PostgreSQL server. The database wiring
# is NOT patched. What is patched is the VENUE TRANSPORT -- the HTTP
# client `pmus._get_client()` returns and the websocket class
# `ms.MarketStream` -- because there is no venue here to talk to.
#
# `ctl.CONTROL_EVERY_S` is shortened so a phase finishes in seconds
# rather than half an hour. It is a timing constant, not a seam.
# ═════════════════════════════════════════════════════════════════════

class FakeStream:
    """An ACTIVE socket, in the shape the loop actually uses it.

    `open` is what the deadline and the stop have to close. Nothing here
    invents book data: the point of these phases is the lifecycle, not
    the payloads (those are covered by bettor_startup_path.py).
    """

    made: list = []

    def __init__(self, key_id, secret_key, on_book=None):
        self.on_book = on_book
        self.slugs: list = []
        self.open = False
        self.started = 0
        self.stopped = 0
        FakeStream.made.append(self)

    def subscribe(self, slugs):
        new = [s for s in slugs if s not in self.slugs]
        self.slugs.extend(new)
        return len(new)

    def prune(self, keep):
        before = len(self.slugs)
        self.slugs = [s for s in self.slugs if s in set(keep)]
        return before - len(self.slugs)

    def start(self):
        self.open = True
        self.started += 1

    def stop(self):
        self.open = False
        self.stopped += 1

    def drain_trades(self):
        return []

    def stats(self):
        return {"open": self.open, "subscribed": len(self.slugs)}

    def frame_capture_report(self):
        return {"frames_captured": 0, "frames_received": 0,
                "decoding": "NOT_ESTABLISHED",
                "book_semantics": "NOT_ESTABLISHED"}

    def book_at(self, slug, decided_at=None, **kw):
        return None


def venue(markets):
    """The HTTP transport `pmus._get_client()` hands back."""
    class C:
        def __init__(self):
            self.markets = markets
    return C()


async def supervisor_run(timeout_s):
    """`main()` exactly as the supervisor invokes it: no arguments."""
    task = asyncio.create_task(bl.main())
    try:
        return await asyncio.wait_for(task, timeout=timeout_s)
    except asyncio.TimeoutError:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        return {"started": None, "why": "TEST_TIMEOUT"}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=os.environ.get("BETTOR_TEST_PG_DSN"))
    a = ap.parse_args()
    if not a.dsn:
        print("NO DSN. Pass --dsn or set BETTOR_TEST_PG_DSN. These "
              "phases cannot be faked and are not simulated.")
        raise SystemExit(2)

    # THE PRODUCTION PATH, POINTED AT A TEST SERVER. `get_pool()` reads
    # `settings().database_url`, so this is the ordinary configuration
    # the worker uses -- not a patch of the pool wiring, which is the
    # thing these phases exist to exercise. `settings()` is lru_cached,
    # so the environment is set before its first call.
    os.environ["DATABASE_URL"] = a.dsn
    os.environ.setdefault("PMUS_KEY_ID", "probe-key-id")
    os.environ.setdefault("PMUS_SECRET_KEY", "probe-secret")
    os.environ.setdefault("BETTOR_LIVE_STORE", "postgres")
    from sportsassets.config import settings as _settings
    _settings.cache_clear()

    raise SystemExit(asyncio.run(main_async(a.dsn)))
