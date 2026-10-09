"""CAPITAL-CRITICAL (EVIDENCE): THE EXECUTABLE-EV VERDICT PRICES ITS WHOLE
DECLARED WINDOW, AND THE DIGITAL TWIN NEVER CERTIFIES ON A CUT POPULATION
(RC6.2 lane p-evcontrols).

THE DEFECTS.
  executable EV  completion.evidence.read_ev reports `window_days: 7` but
                 read the newest 2,000 ENTER decisions. Production
                 completion.json (pm-acceptance 37888018192): 1,992 priced +
                 8 dropped = 2,000 exactly, against about 4,300 ENTER
                 decisions in the window (research-sql 37943749921 §6) -- the
                 verdict's event-clustered lower bound covered the newest
                 ~2.4 days, one strategy, and nothing said so.
  digital twin   read_twin replayed the OLDEST 800 fresh orders (eligible_at
                 ascending). Past 800, every newer order -- the ones that say
                 whether the twin still agrees -- would leave the
                 certification's population unnamed (latent: 7 orders today).

THE FIX. Each read takes one row past its bound, so a cut is known, not
guessed. The EV read prices the whole window (a 20,000 safety stop; the
pricing runs on the API's CPU lane) and reports what it covers; a cut read
is CASH by name, its subset verdict kept as evidence only. A cut twin read
certifies nothing (READ_TRUNCATED_NEWER_ORDERS_NOT_REPLAYED) and the
DIGITAL_TWIN control names TWIN_READ_TRUNCATED. No threshold, fee, edge or
acceptance rule changes; a verdict can only stay or tighten.

THE REWORK (independent review). Failing closed at 800 made the cut
permanent: the twin's population (every fresh order since a fixed instant)
only grows, so from the 801st fresh order on DIGITAL_TWIN would be RED for
good -- a software RED the scorecard labels forward evidence. The twin read
now pages the WHOLE population through a server-side cursor
(TWIN_PAGE_ORDERS at a time, each page decoded and replayed on the CPU lane
into one streaming agreement), with a 20,000-order safety stop: 25 times
production's whole history (research-sql 37970656946: 794 orders; the
server costs 0.21-0.50 ms per order). A read the stop cuts still certifies
nothing.

ALL ROWS ARE SYNTHETIC TEST DATA written inside a transaction each test rolls
back.
"""
from __future__ import annotations

import asyncio
import os
import time

import pytest

from sportsassets.completion import evidence as EV
from sportsassets.redteam import controls as C

try:
    from tests import intel_fixture as F
    from tests import paper_harness as H
except ImportError:                                             # pragma: no cover
    import intel_fixture as F
    import paper_harness as H

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

EV_POPULATION_SQL = """
SELECT count(*) FROM paper_decisions d
 WHERE d.verdict = 'ENTER' AND d.book_obs_id IS NOT NULL
   AND d.decided_at > now() - make_interval(days => 7)"""

ENTER_WITH_BOOK_SQL = """
INSERT INTO paper_decisions (decision_id, session_id, account_id,
  decided_at, us_market_slug, holding_side, fixture, label, verdict,
  refusal, p_pinnacle, internal_model, pinnacle, limit_price, proposed_qty,
  economics, qualification_gaps, policy_version, simulator_version, strategy,
  book_obs_id)
SELECT 'paperdec:evw-' || $4 || '-' || g, $1, $2, to_timestamp($3 - g * 60),
       $5, 'LONG', 'fx-evw-' || $4 || '-' || (g % 97), '{}'::jsonb, 'ENTER',
       NULL, 0.5, '{}'::jsonb, '{}'::jsonb, 0.5, 10, '{}'::jsonb,
       '[]'::jsonb, 'TEST', $6, $7, $8
  FROM generate_series(1, $9::int) g
"""


async def enter_with_book(conn, acct, *, newest_at, n, slug, obs_id):
    """`n` ENTER decisions in the last 7 days, each priced off one real
    two-sided book observation, a minute apart."""
    await conn.execute(ENTER_WITH_BOOK_SQL, acct["session_id"],
                       acct["account_id"], float(newest_at), F.uid(""), slug,
                       F.SIM_VERSION, F.STRATEGY, int(obs_id), int(n))


def _run(fn):
    import asyncpg

    async def go():
        conn = await asyncpg.connect(DSN)
        tr = conn.transaction()
        await tr.start()
        try:
            return await fn(conn)
        finally:
            await tr.rollback()
            await conn.close()
    return asyncio.run(go())


# ── §1 the executable-EV read prices the whole declared window ───────

@pg
def test_more_than_2000_decisions_in_the_window_are_all_priced():
    """b3f1b0cd: 2,000 priced + dropped, whatever the window holds."""
    async def fn(conn):
        now = time.time()
        acct = await H.new_account(conn, "evwin", now=now - 9 * 86400)
        slug = F.uid("evw-mkt-")
        obs = await H.observe(conn, slug, now - 3 * 86400,
                              bids=((0.48, 500),), offers=((0.52, 500),))
        await enter_with_book(conn, acct, newest_at=now - 120, n=2100,
                              slug=slug, obs_id=obs)
        pop = await conn.fetchval(EV_POPULATION_SQL)
        out = await EV.read_ev(conn, authority="MARKET_PRIOR_ONLY")
        return int(pop), out
    pop, out = _run(fn)
    assert pop > 2000
    got = out["decisions_priced"] + sum(out["dropped"].values())
    assert got == pop, (got, pop)
    assert out["population"]["population_in_window"] == pop
    assert out["population"]["truncated"] is False
    assert out["population"]["covers_the_declared_window"] is True
    assert out["window_days"] == 7
    assert out["verdict"] == "CASH"            # market prior: never positive


@pg
def test_a_window_the_bound_cuts_is_cash_by_name():
    async def fn(conn):
        now = time.time()
        acct = await H.new_account(conn, "evcut", now=now - 9 * 86400)
        slug = F.uid("evc-mkt-")
        obs = await H.observe(conn, slug, now - 3 * 86400,
                              bids=((0.48, 500),), offers=((0.52, 500),))
        await enter_with_book(conn, acct, newest_at=now - 120, n=12,
                              slug=slug, obs_id=obs)
        return await EV.read_ev(conn, authority="MARKET_PRIOR_ONLY",
                                limit=10)
    out = _run(fn)
    assert out["population"]["truncated"] is True
    assert out["population"]["decisions_read"] == 10
    assert out["population"]["population_in_window"] is None
    assert out["population"]["population_at_least"] == 11
    assert out["verdict"] == "CASH" and out["reason"] == EV.EV_READ_TRUNCATED
    assert out["subset_verdict"] == "CASH"


def test_a_cut_read_never_passes_even_when_its_subset_would():
    sub = {"verdict": "ELIGIBLE_FOR_EXISTING_GATED_PATH",
           "reason": "POSITIVE_ALL_IN_EXECUTABLE_EV_LOWER_BOUND",
           "decisions_priced": 5, "dropped": {}}
    cut = EV.ev_population(sub, read=5, limit=5, truncated=True)
    assert cut["verdict"] == "CASH" and cut["reason"] == EV.EV_READ_TRUNCATED
    assert cut["subset_verdict"] == "ELIGIBLE_FOR_EXISTING_GATED_PATH"
    whole = EV.ev_population(sub, read=5, limit=5, truncated=False)
    assert whole["verdict"] == sub["verdict"]           # nothing relaxed,
    assert whole["reason"] == sub["reason"]             # nothing invented
    assert whole["population"]["population_in_window"] == 5


def test_the_ev_pricing_runs_off_the_event_loop():
    import inspect
    src = inspect.getsource(EV.read_ev)
    assert "_cpu.run(ev_block," in src
    assert EV.EV_MAX_DECISIONS >= 10000


# ── §2 the twin never certifies on a cut population ──────────────────

def _agreeing(n):
    t = EV.TWIN_DIAGNOSIS_WINDOW_END + 10
    return [{"order_id": "o%d" % i, "tif": "IOC", "direction": "BUY",
             "side": "LONG", "slug": "m%d" % i, "qty": 10.0, "limit": 0.50,
             "decided": t - 2, "eligible": t, "expires": t + 90,
             "fills": [{"qty": 10.0}],
             "books": [{"at": t + 1, "bids": [{"px": 0.45, "qty": 100}],
                        "asks": [{"px": 0.50, "qty": 100}]}]}
            for i in range(n)]


def test_a_cut_twin_population_certifies_nothing():
    orders = _agreeing(EV.TWIN_MIN_FRESH_ORDERS + 5)
    whole = EV.twin_block(orders, truncated=False, limit=800)
    assert whole["certified"] is True and whole["status"] == "CERTIFIED"
    cut = EV.twin_block(orders, truncated=True, limit=len(orders))
    assert cut["certified"] is False
    assert cut["status"] == EV.TWIN_READ_TRUNCATED
    assert cut["read"]["truncated"] is True
    # the control names it beside every other blocker
    c = C.twin(dict(cut, compared=cut["replayed"], agree=cut["replayed"],
                    optimistic_false_fills=0, lookahead_violations=0))
    assert c["status"] == C.RED and "TWIN_READ_TRUNCATED" in c["blockers"]
    ok = C.twin(dict(whole, compared=whole["replayed"],
                     agree=whole["replayed"], optimistic_false_fills=0,
                     lookahead_violations=0))
    assert "TWIN_READ_TRUNCATED" not in ok["blockers"]


@pg
def test_the_twin_read_says_when_its_bound_left_newer_orders_out():
    async def fn(conn):
        now = time.time()
        acct = await H.new_account(conn, "twincut", now=now - 86400)
        for k in range(3):
            slug = F.uid("tw-mkt-")
            await F.order(conn, acct, group_id="paper_group_" + F.uid(),
                          slug=slug, qty=10, price=0.5, at=now - 3600 + k)
        pop = await conn.fetchval(
            "SELECT count(*) FROM paper_orders o WHERE o.time_in_force IN "
            "('IOC', 'FOK') AND o.terminal_at IS NOT NULL AND o.role IN "
            "('ENTRY', 'EXIT', 'REDUCE') AND o.eligible_at > "
            "to_timestamp($1)", float(EV.TWIN_DIAGNOSIS_WINDOW_END))
        whole = await EV.read_twin(conn, limit=int(pop))
        cut = await EV.read_twin(conn, limit=int(pop) - 1)
        return int(pop), whole, cut
    pop, whole, cut = _run(fn)
    assert pop >= 3
    assert whole["read"] == {"orders_read": pop, "limit": pop,
                             "truncated": False,
                             "order": EV.TWIN_READ_ORDER,
                             "population": pop, "population_at_least": pop,
                             "covers_the_population": True,
                             "pages": 1, "page_orders": EV.TWIN_PAGE_ORDERS}
    assert cut["read"]["truncated"] is True
    assert cut["read"]["orders_read"] == pop - 1
    assert cut["read"]["population"] is None
    assert cut["read"]["population_at_least"] == pop
    assert cut["read"]["covers_the_population"] is False
    assert cut["status"] == EV.TWIN_READ_TRUNCATED
    assert cut["certified"] is False


# ── §3 the twin reads its WHOLE population: no cliff at 800 (rework) ──

FRESH_TWIN_POPULATION_SQL = """
SELECT count(*) FROM paper_orders o
 WHERE o.time_in_force IN ('IOC', 'FOK') AND o.terminal_at IS NOT NULL
   AND o.role IN ('ENTRY', 'EXIT', 'REDUCE')
   AND o.eligible_at > to_timestamp($1)"""

#: `n` terminal IOC ENTRY orders, one a second, on `slugs` markets: PAPER
#: EXPIRED them (no fill); a BUY LONG at 0.50 against offers at 0.60 never
#: crosses, so the repaired twin agrees on every one
EXPIRED_IOC_SQL = """
INSERT INTO paper_orders (order_id, idempotency_key, account_id, session_id,
  group_id, role, direction, holding_side, intent, us_market_slug, fixture,
  label, order_type, time_in_force, allow_partial, qty, limit_price,
  wire_price, filled_qty, state, decision_id, decided_at, eligible_at,
  expires_at, simulator_version, strategy, terminal_at, terminal_reason)
SELECT 'paperord:' || $1 || '-' || g, 'paperord:' || $1 || '-' || g, $2, $3,
       'paper_group_' || $1 || '-' || g, 'ENTRY', 'BUY', 'LONG',
       'ORDER_INTENT_BUY_LONG', $4 || (g % $5::int), 'fx-' || $4 || (g % $5::int),
       '{}'::jsonb, 'MARKETABLE', 'IOC', true, 10, 0.50, 0.50, 0, 'EXPIRED',
       NULL, to_timestamp($6 + g - 2), to_timestamp($6 + g),
       to_timestamp($6 + g + 90), $7, $8, to_timestamp($6 + g + 90),
       'TEST_IOC_NOT_MARKETABLE'
  FROM generate_series(1, $9::int) g
"""


#: other tests' committed fresh orders (if any) leave the twin's population
#: inside this test's rolled-back transaction only, so the test owns it
HIDE_FOREIGN_FRESH_SQL = """
UPDATE paper_orders o SET terminal_at = NULL
 WHERE o.time_in_force IN ('IOC', 'FOK') AND o.terminal_at IS NOT NULL
   AND o.role IN ('ENTRY', 'EXIT', 'REDUCE')
   AND o.eligible_at > to_timestamp($1)"""


async def own_twin_universe(conn):
    await conn.execute(HIDE_FOREIGN_FRESH_SQL,
                       float(EV.TWIN_DIAGNOSIS_WINDOW_END))


async def expired_iocs(conn, acct, *, n, slugs, start):
    """`n` fresh orders the twin agrees with, over `slugs` markets whose
    recorded books (every 30 s, offers above the limit) fall inside every
    order's window; returns the market prefix."""
    tag = F.uid("")
    prefix = "twp-" + tag + "-"
    await conn.execute(EXPIRED_IOC_SQL, tag, acct["account_id"],
                       acct["session_id"], prefix, int(slugs), float(start),
                       F.SIM_VERSION, F.STRATEGY, int(n))
    for m in range(int(slugs)):
        for k in range(int(n) // 30 + 4):
            await H.observe(conn, prefix + str(m), float(start) + 30 * k,
                            bids=((0.45, 100),), offers=((0.60, 100),))
    return prefix


@pg
def test_more_than_800_fresh_orders_are_all_replayed_and_certify():
    """b3f1b0cd replayed the oldest 800 (and 178e7735 then failed closed for
    good): every fresh order past the old bound is now replayed."""
    async def fn(conn):
        now = time.time()
        acct = await H.new_account(conn, "twinall", now=now - 86400)
        await own_twin_universe(conn)
        await expired_iocs(conn, acct, n=850, slugs=5, start=now - 7200)
        pop = await conn.fetchval(FRESH_TWIN_POPULATION_SQL,
                                  float(EV.TWIN_DIAGNOSIS_WINDOW_END))
        return int(pop), await EV.read_twin(conn)
    pop, out = _run(fn)
    assert pop == 850 > 800
    assert out["replayed"] == pop, (out["replayed"], pop)
    assert out["read"]["orders_read"] == pop
    assert out["read"]["truncated"] is False
    assert out["read"]["population"] == pop
    assert out["read"]["pages"] == -(-pop // EV.TWIN_PAGE_ORDERS)
    assert out["status"] == "CERTIFIED" and out["certified"] is True
    c = C.twin(out)
    assert "TWIN_READ_TRUNCATED" not in c["blockers"]
    assert not any(b.startswith("FEWER_THAN_") for b in c["blockers"])
    assert c["status"] == C.GREEN, c["blockers"]


@pg
def test_a_read_the_safety_stop_cuts_still_certifies_nothing():
    async def fn(conn):
        now = time.time()
        acct = await H.new_account(conn, "twinstop", now=now - 86400)
        await own_twin_universe(conn)
        await expired_iocs(conn, acct, n=850, slugs=5, start=now - 7200)
        pop = await conn.fetchval(FRESH_TWIN_POPULATION_SQL,
                                  float(EV.TWIN_DIAGNOSIS_WINDOW_END))
        return int(pop), await EV.read_twin(conn, limit=int(pop) - 1)
    pop, out = _run(fn)
    assert out["read"]["truncated"] is True
    assert out["read"]["orders_read"] == pop - 1
    assert out["replayed"] == pop - 1 >= EV.TWIN_MIN_FRESH_ORDERS
    assert out["status"] == EV.TWIN_READ_TRUNCATED
    assert out["certified"] is False
    c = C.twin(out)
    assert c["status"] == C.RED and "TWIN_READ_TRUNCATED" in c["blockers"]


@pg
def test_the_paged_read_equals_the_whole_list_replayed_at_once():
    """Pages of 7 through the cursor give exactly the report one list of the
    same rows gives; the shared consumption ledger crosses page edges."""
    import json

    async def fn(conn):
        now = time.time()
        acct = await H.new_account(conn, "twinpage", now=now - 86400)
        slug = F.uid("twc-mkt-")
        # one book every 30 s that DOES cross: orders sharing a book
        # observation consume its 25 contracts in eligibility order, so
        # the ledger decides who fills
        for k in range(8):
            await H.observe(conn, slug, now - 7200 + 30 * k,
                            bids=((0.45, 25),), offers=((0.50, 25),))
        for g in range(40):
            await F.order(conn, acct, group_id="paper_group_" + F.uid(),
                          slug=slug, qty=10, price=0.5,
                          at=now - 7200 + 5 * g + 1)
        rows = await conn.fetch(EV.TWIN_SQL,
                                float(EV.TWIN_DIAGNOSIS_WINDOW_END), 100000)
        whole = EV.twin_block([json.loads(r["j"]) for r in rows],
                              limit=100000)
        paged = await EV.read_twin(conn, page=7)
        return len(rows), whole, paged
    n, whole, paged = _run(fn)
    assert n >= 40
    assert paged["read"]["pages"] == -(-n // 7)
    assert paged["read"]["orders_read"] == n
    keys = ("marketable_orders", "replayed", "agree", "fill_agreement_rate",
            "optimistic_false_fills", "lookahead_violations",
            "residual_mismatch_taxonomy",
            "optimistic_twin_mismatch_taxonomy", "markets", "status",
            "certified")
    assert {k: paged[k] for k in keys} == {k: whole[k] for k in keys}
    # the ledger mattered: some orders found the shared book consumed
    assert whole["residual_mismatch_taxonomy"] or whole["agree"] < n


def test_streaming_agreement_equals_the_whole_list_for_every_split():
    """fill_replay.Agreement over in-order batches == agreement(list)."""
    import random
    from sportsassets.completion import fill_replay as TW
    rnd = random.Random(20261009)
    t = EV.TWIN_DIAGNOSIS_WINDOW_END + 10
    books = {s: [{"at": t + 30 * k,
                  "bids": [{"px": 0.40 + 0.01 * rnd.randint(0, 5),
                            "qty": rnd.randint(5, 40)}],
                  "asks": [{"px": 0.48 + 0.01 * rnd.randint(0, 5),
                            "qty": rnd.randint(5, 40)},
                           {"px": 0.535, "qty": 50}]}
                 for k in range(40)] for s in ("a", "b", "c")}
    orders = []
    for i in range(300):
        s = rnd.choice("abc")
        el = t + 3 * i + rnd.random()
        tif = rnd.choice(("IOC", "IOC", "FOK", "GTD"))
        orders.append({"order_id": "o%d" % i, "tif": tif, "slug": s,
                       "direction": "BUY", "side": rnd.choice(("LONG",
                                                               "SHORT")),
                       "qty": float(rnd.randint(1, 30)),
                       "limit": 0.45 + 0.01 * rnd.randint(0, 10),
                       "decided": el - 2, "eligible": el,
                       "expires": el + 90,
                       "fills": ([{"qty": float(rnd.randint(1, 30))}]
                                 if rnd.random() < 0.5 else []),
                       "books": books[s]})
    whole = TW.agreement(orders)
    assert whole["replayed"] > 100 and whole["agree"] < whole["replayed"]
    for size in (1, 7, 64, 299, 300, 1000):
        acc = TW.Agreement()
        for i in range(0, len(orders), size):
            acc.add(orders[i:i + size])
        assert acc.report() == whole, size


def test_a_batch_out_of_eligibility_order_is_refused():
    from sportsassets.completion import fill_replay as TW
    early, late = _agreeing(2)
    late = dict(late, eligible=early["eligible"] + 5)
    acc = TW.Agreement().add([late])
    with pytest.raises(TW.OutOfEligibilityOrder):
        acc.add([early])


def test_the_twin_read_pages_on_the_cpu_lane_with_a_high_safety_stop():
    import inspect
    src = inspect.getsource(EV.read_twin)
    assert "_cpu.run(twin_page," in src
    assert "conn.cursor(TWIN_SQL" in src
    assert "ORDER BY o.eligible_at, o.order_id" in EV.TWIN_SQL
    # 25 x production's whole history (794, research-sql 37970656946);
    # one page bounds the memory a read holds
    assert EV.TWIN_MAX_ORDERS >= 20000
    assert EV.TWIN_PAGE_ORDERS <= 1000
