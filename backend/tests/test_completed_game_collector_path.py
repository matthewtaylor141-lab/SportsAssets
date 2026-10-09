"""THE VENUE'S WORDS, FROM ITS OWN LISTING RESPONSE TO THE COMPLETED-GAME
DECISION, THROUGH THE SCHEDULED COLLECTION CYCLE.

The completed-game paper policy reads the venue's ordinary-game grading from
`settlement_comparison.venue_rules_text` on the valuation row. That field is
written by the collector, from the text the collector itself fetched. This
proof injects NEITHER the text into the row NOR a candidate into the policy:

  * the venue boundary is the client's `markets.list` call. A fake client
    returns the listing payload a real contract carries (the recorded
    Phillies v Braves wording, with this fixture's teams), and the REAL
    reader (`bettor_live_read.read_rules_text`) and the REAL hourly-cached
    wrapper (`ext_pinnacle_loop._read_venue_rules_blocking`) run unchanged;
  * the REAL cycle (`ext_pinnacle_loop.cycle`, the Derek harness of
    test_derek_enters_on_conservative_agreement: provider, resolver and venue
    quote substituted at their boundaries, SYNTHETIC) persists the valuation
    with the text, its source field, reader, retrieval instant and sha256;
  * the in-cycle paper hook decides the completed-game policy on that row at
    the valuation instant, and the decision's grading check cites the venue
    text's hash; the paper book read prices the entry.

SYNTHETIC teams and books. No real venue call, no real order.
"""
from __future__ import annotations

import hashlib
import time

import pytest

from sportsassets import bettor_live_read as LR
from sportsassets import bettor_paper_session as S
from sportsassets import pmus
from sportsassets import venue_pace
from sportsassets.agents import paper_benchmark as PB
from sportsassets.agents import paper_derek as PD
from sportsassets.agents import paper_runtime as PR
from sportsassets.agents import runtime as RT
from sportsassets.workers import ext_pinnacle_loop as loop

from tests import paper_harness as H
from tests import paper_live_fixture as PL
from tests import test_derek_enters_on_conservative_agreement as DT

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")

#: The venue's recorded MLB wording (admin probe 2026-10-01 11:53:00Z,
#: Phillies v Braves), with this SYNTHETIC fixture's teams substituted.
LISTED_PROSE = (
    "This market will settle to the winner of the Boston Red Sox vs New York "
    "Yankees MLB game scheduled for 2026-10-01 at 7:05PM ET. Extra innings "
    "are included if played. If the game is delayed, postponed, or suspended "
    "and not rescheduled to a date within two weeks of the originally "
    "scheduled date, the market will settle to the last fair market price. "
    "Outcome sourced from MLB.")


class _Markets:
    def __init__(self):
        self.calls = []

    def list(self, params):
        self.calls.append(params)
        slug = (params.get("slug") or [None])[0]
        return {"markets": [{
            "slug": slug, "description": LISTED_PROSE,
            "marketType": "MARKET_TYPE_SPORTS",
            "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_MONEYLINE",
            "orderPriceMinTickSize": "0.01"}]}


class _Client:
    """The venue client's listing surface ONLY. Anything else is an error:
    the collector's rules read must not reach any other venue call."""

    def __init__(self):
        self.markets = _Markets()


def _wire(monkeypatch, acct, transport):
    from unittest.mock import AsyncMock
    monkeypatch.setattr(PR.L, "selected_account", AsyncMock(return_value=acct["account_id"]))
    monkeypatch.setitem(PR._CLIENT, "client", PL.client(transport))
    monkeypatch.setattr(RT, "paper_pass_hook",
                        lambda **kw: {"scheduled": False})
    # (integration) NO BACKGROUND ENTRY-FILL READ FROM THIS PROOF. The cycle's
    # paper hook calls decide_valuation with no injected schedule_fill, which
    # in production starts `schedule_entry_fill` -- a background task on the
    # process-wide db pool. On the release candidate this cycle reaches an
    # ENTER with an order (one entry-fill read is scheduled; measured), so
    # the task opened the global pool on
    # this test's event loop and left it behind; the next test to call
    # db.close_pool (test_archer_scout_api_pages_slack) then failed with
    # "Event loop is closed" (CI runs 37262090186 / 37262958942, reproduced
    # locally by running the suite prefix). The fill read is not what this
    # proof is about; it is stubbed exactly like the paper-pass hook above.
    monkeypatch.setattr(PR, "schedule_entry_fill",
                        lambda ids, **kw: {"scheduled": False})
    PD._CONTEXT_CACHE.clear()
    PB._CONTEXT_CACHE.clear()


@pg
async def test_the_cycle_fetches_persists_and_decides_on_the_venue_text(
        monkeypatch, new_strategies_off):
    conn = await H.connect()
    t_start = time.time()
    real_rules = loop._read_venue_rules_blocking
    try:
        await DT._seed(conn)
        acct = await PL.new_account(conn, "cgcollect", now=t_start)
        t = PL.Transport(t_start)
        t.set(DT.US_SLUG, offers=[(0.50, 5000)], bids=[(0.48, 5000)])
        _wire(monkeypatch, acct, t)
        monkeypatch.setenv(S.ENV_FLAG, "on")
        monkeypatch.setenv(PB.ENV_FLAG, "on")
        # THE PRODUCTION SELECTION (migration 184): completed-game on,
        # strict off
        for key, on in ((PB.CG_POLICY["control_key"], True),
                        (PB.CONTROL_KEY, False)):
            await conn.execute("UPDATE paper_control SET enabled=$2 WHERE "
                               " control_key=$1", key, on)
        DT._stub(monkeypatch, p_home=0.60, stamp_age_s=2.0)
        # UNDO the harness's rules stub: the REAL reader runs, against the
        # venue client's listing response
        monkeypatch.setattr(loop, "_read_venue_rules_blocking", real_rules)
        client = _Client()
        monkeypatch.setattr(pmus, "_get_client", lambda: client)
        monkeypatch.setattr(venue_pace, "pace", lambda *a, **k: 0.0)
        loop.rules_cache_reset()

        out = await loop.cycle(conn)
        assert out.get("written", 0) >= 1, out.get("refusals")
        assert client.markets.calls, "the venue listing was never read"
        assert all(c.get("slug") == [DT.US_SLUG]
                   for c in client.markets.calls)

        # ── PERSISTED: the venue's words, where and when they were read ──
        v = await conn.fetchrow(
            "SELECT id, settlement_comparison, decided_at FROM "
            " external_valuations WHERE us_market_slug=$1 "
            " ORDER BY id DESC LIMIT 1", DT.US_SLUG)
        sc = H.j(v["settlement_comparison"])
        assert sc["venue_rules_text"] == LISTED_PROSE
        assert sc["venue_rules_read"] is True
        assert sc["venue_rules_field"] == "description"
        assert sc["venue_rules_reader"] == LR.READER_VERSION
        assert sc["venue_rules_source"] == \
            "pmus:/markets?slug=<slug>:rules_text"
        assert sc["venue_rules_from_cache"] is False
        assert sc["venue_rules_error"] is None
        assert abs(float(sc["venue_rules_retrieved_at"]) - t_start) < 120
        sha = hashlib.sha256(LISTED_PROSE.encode("utf-8")).hexdigest()
        assert sc["venue_rules_sha256"] == sha

        # ── DECIDED IN THE CYCLE, on that row, by the completed-game policy
        d = await conn.fetchrow(
            "SELECT * FROM paper_decisions WHERE session_id=$1 AND "
            " valuation_id=$2 AND strategy=$3", acct["session_id"], v["id"],
            PB.CG_STRATEGY)
        assert d is not None, "no completed-game decision on the new row"
        pin = H.j(d["pinnacle"])
        assert pin["decided_via"] == PD.DECIDED_VIA_CYCLE
        chk = {c["check"]: c for c in pin["contract_match"]["checks"]}
        gp = chk["ordinary_completion_grading_period"]
        assert gp["passed"] is True, gp
        assert gp["venue"]["period"] == PB.GP_BASEBALL
        assert gp["venue"]["rules_sha256"] == sha
        assert d["verdict"] == "ENTER", (d["refusal"], d["refusals"])
        assert d["book_obs_id"] is not None
        econ = H.j(d["economics"])
        assert econ["label"] == PB.ECONOMICS_LABEL
        assert econ["best_level_edge_pp"] == pytest.approx(10.0)
        o = await conn.fetchrow("SELECT * FROM paper_orders WHERE "
                                " decision_id=$1 AND role='ENTRY'",
                                d["decision_id"])
        assert o is not None and o["strategy"] == PB.CG_STRATEGY
        # the strict benchmark is switched off: nothing new from it
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_decisions WHERE session_id=$1 AND "
            " strategy=$2", acct["session_id"], PB.STRATEGY) == 0
    finally:
        loop.rules_cache_reset()
        await PL.drop_today_run(conn, t_start)
        await DT._cleanup(conn)
        await PL.purge_everything(conn)
        await conn.close()
