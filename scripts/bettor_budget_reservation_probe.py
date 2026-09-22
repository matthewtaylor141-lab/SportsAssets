#!/usr/bin/env python3
"""DOES THE 40-DISTINCT CEILING SURVIVE A RESTART? Measured, not argued.

The Stage 2B pre-arming condition is precise: request budgets must be
RESERVED DURABLY BEFORE DISPATCH, not merely counted afterward, and
listing retries and restarts must not replenish the total allowance. It
also says, correctly, that concurrent-increment tests do not establish
this -- atomicity of a decrement says nothing about WHEN the decrement
happens relative to the request it is supposed to pay for.

So this script does not test atomicity. It counts the venue requests a
RESTARTING worker actually issues against a single armed budget, using
the real `main()`, the real `_discover`, the real probe and a real
PostgreSQL row. The only fakes are the venue client (which counts
calls) and the crash (which is raised at a real crash point).

Run:  python scripts/bettor_budget_reservation_probe.py --dsn ...

Exit 0 means the ceiling held. Exit 1 means it did not, and the output
says by how much.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "backend"))

import asyncpg                                              # noqa: E402

from sportsassets import bettor_live_control as ctl         # noqa: E402
from sportsassets import bettor_live_store as st            # noqa: E402
from sportsassets.workers import bettor_live_loop as bl     # noqa: E402

MAX_DISTINCT = 40
CANDIDATES = 400


def rule(t):
    print("\n" + "=" * 76 + "\n" + t + "\n" + "=" * 76)


def check(label, got, want):
    ok = got == want
    print("      %-52s %-10s %s" % (
        label, got, "expected %s" % want if ok else
        "expected %s   *** MISMATCH ***" % want))
    return ok


# ── the venue, which only counts ─────────────────────────────────────
class CountingMarkets:
    """Serves a listing and BBO bodies, and counts every call.

    `bbo` records the slug so DISTINCT markets requested can be counted
    apart from attempts -- the two numbers the ceiling is written in.
    """

    def __init__(self, *, fail_slugs=frozenset()):
        self.list_calls = 0
        self.bbo_calls: list[str] = []
        self.fail_slugs = set(fail_slugs)

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
            for i in range(CANDIDATES)]}

    def bbo(self, slug):
        self.bbo_calls.append(slug)
        if slug in self.fail_slugs:
            raise RuntimeError("venue read failed")
        return {"marketData": {
            "marketSlug": slug, "state": "MARKET_STATE_OPEN",
            "bids": [{"price": "0.20", "quantity": "500"}],
            "offers": [{"price": "0.25", "quantity": "500"}],
            "stats": {"sharesTraded": "5000"},
            "transactTime": "2026-09-22T01:00:00Z"}}

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


async def arm(pool):
    await pool.execute("""
        INSERT INTO ingestion_state (key, value) VALUES ($1,
            jsonb_build_object(
                'started_at', to_char(now() AT TIME ZONE 'UTC',
                    'YYYY-MM-DD"T"HH24:MI:SS+00:00'),
                'deadline_at', to_char((now() + interval '30 minutes')
                    AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS+00:00'),
                'max_distinct', $2::int, 'distinct_consumed', 0))
        ON CONFLICT (key) DO UPDATE SET value = excluded.value,
            updated_at = now()""", ctl.BUDGET_KEY, MAX_DISTINCT)
    await pool.execute("""
        INSERT INTO ingestion_state (key, value) VALUES ($1, 'true'::jsonb)
        ON CONFLICT (key) DO UPDATE SET value = 'true'::jsonb,
            updated_at = now()""", ctl.CONTROL_KEY)


async def run_one(pool, markets, tmp, *, crash_before_charge, boot):
    """One worker lifetime. `crash_before_charge` raises at a REAL crash
    point that sits after dispatch and before `consume_budget` --
    `loop.recover()`, which `main()` does not guard."""
    store = st.FileStore(tmp, durable_across_redeploy=True)

    real_build = bl.build

    def build(*a, **kw):
        loop, stream = real_build(*a, **kw)
        stream.start = lambda: None
        stream.stop = lambda: None
        if crash_before_charge:
            async def die():
                raise RuntimeError("OOM-killed between dispatch and charge")
            loop.recover = die
        else:
            async def nothing():
                return {"restored": 0}
            loop.recover = nothing
        return loop, stream

    class Cfg:
        pmus_key_id, pmus_secret_key = "k", "s"

    import sportsassets.config as cfgmod
    old_settings = cfgmod.settings
    old_build = bl.build
    cfgmod.settings = lambda: Cfg()
    bl.build = build
    try:
        return await bl.main(client=Client(markets), store=store,
                             control_pool=pool, run_for_s=0.0,
                             sleep=_nap)
    finally:
        cfgmod.settings = old_settings
        bl.build = old_build


async def _nap(_d):
    """The backoff schedule is not what is being measured here."""
    return None


async def main_async(dsn):
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
    await pool.execute("""CREATE TABLE IF NOT EXISTS ingestion_state (
        key text PRIMARY KEY, value jsonb NOT NULL,
        updated_at timestamptz NOT NULL DEFAULT now())""")

    ok = True
    tmp = tempfile.mkdtemp(prefix="budget-probe-")
    os.environ["BETTOR_PROBE_MAX_RPS"] = "100000"  # pace is not what is measured
    os.environ["BETTOR_PROBE_CONCURRENCY"] = "1"

    # ── A ────────────────────────────────────────────────────────────
    rule("A. THE HAPPY PATH -- one lifetime, nothing fails")
    await arm(pool)
    m = CountingMarkets()
    out = await run_one(pool, m, tmp, crash_before_charge=False, boot=1)
    b = await ctl.read_budget(pool)
    print("   started=%s why=%s" % (out.get("started"), out.get("why")))
    print("   venue: %d listing calls, %d BBO attempts, %d DISTINCT markets"
          % (m.list_calls, len(m.bbo_calls), m.distinct))
    print("   budget row: consumed=%s remaining=%s"
          % (b.get("consumed"), b.get("remaining")))
    ok &= check("distinct markets requested <= the ceiling",
                m.distinct <= MAX_DISTINCT, True)
    ok &= check("the row was charged for what was dispatched",
                b.get("consumed"), m.distinct)

    # ── B ────────────────────────────────────────────────────────────
    rule("B. THE RESTART -- a crash between DISPATCH and CHARGE")
    print("   `main()` dispatches every BBO read inside `_discover`, then")
    print("   charges `consume_budget` afterwards. A worker that dies in")
    print("   between has spent the venue requests and recorded nothing.")
    print("   `workers/all.py:run_forever` restarts it in 5 s.\n")
    await arm(pool)
    m = CountingMarkets()
    lifetimes, spent = 0, []
    for i in range(4):
        try:
            await run_one(pool, m, tmp, crash_before_charge=True, boot=i + 2)
        except Exception as exc:
            pass                    # the supervisor's catch
        lifetimes += 1
        b = await ctl.read_budget(pool)
        spent.append((len(m.bbo_calls), m.distinct, b.get("consumed")))
        print("   lifetime %d: %3d BBO attempts, %3d distinct so far, "
              "row consumed=%s" % (i + 1, len(m.bbo_calls), m.distinct,
                                   b.get("consumed")))

    b = await ctl.read_budget(pool)
    print("\n   after %d lifetimes against ONE armed budget:" % lifetimes)
    print("      distinct markets the venue was asked about : %d" % m.distinct)
    print("      the ceiling                                : %d" % MAX_DISTINCT)
    print("      what the durable row believes was consumed : %s"
          % b.get("consumed"))
    print("      listing calls (bounded per lifetime only)  : %d" % m.list_calls)
    ok &= check("distinct markets requested <= the ceiling",
                m.distinct <= MAX_DISTINCT, True)
    ok &= check("the budget row still says the probe may continue",
                b.get("open"), False)

    # ── C ────────────────────────────────────────────────────────────
    rule("C. FAILED READS ARE FREE -- the charge counts SUCCESSES")
    print("   `consumed = coverage['distinct_enriched']`, and")
    print("   `distinct_enriched = len(rows)` counts only the markets that")
    print("   PARSED. A market that was requested and failed cost a venue")
    print("   request and is charged nothing.\n")
    await arm(pool)
    fails = {"m%03d" % i for i in range(0, 30)}
    m = CountingMarkets(fail_slugs=fails)
    await run_one(pool, m, tmp, crash_before_charge=False, boot=99)
    b = await ctl.read_budget(pool)
    print("   %d BBO attempts over %d distinct markets, %d of them failing"
          % (len(m.bbo_calls), m.distinct,
             len(set(m.bbo_calls) & fails)))
    print("   the row was charged: %s" % b.get("consumed"))
    ok &= check("the charge equals the distinct markets requested",
                b.get("consumed"), m.distinct)

    await pool.execute("DELETE FROM ingestion_state WHERE key = ANY($1::text[])",
                       [ctl.CONTROL_KEY, ctl.BUDGET_KEY])
    await pool.close()

    rule("VERDICT")
    if ok:
        print("   The ceilings are enforced. Reservation precedes dispatch.")
        return 0
    print("   THE CEILINGS ARE NOT ENFORCED. See the mismatches above.")
    print("   Reservation does NOT precede dispatch, so a restart")
    print("   replenishes the allowance. Stop before obs-run.")
    return 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=os.environ.get("BETTOR_TEST_PG_DSN"))
    a = ap.parse_args()
    if not a.dsn:
        print("NO DSN. Pass --dsn or set BETTOR_TEST_PG_DSN. This cannot "
              "be faked.")
        raise SystemExit(2)
    raise SystemExit(asyncio.run(main_async(a.dsn)))
