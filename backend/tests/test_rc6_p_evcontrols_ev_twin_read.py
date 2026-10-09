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
                             "order": "eligible_at ascending (oldest first)"}
    assert cut["read"]["truncated"] is True
    assert cut["read"]["orders_read"] == pop - 1
    assert cut["status"] == EV.TWIN_READ_TRUNCATED
    assert cut["certified"] is False
