"""THE HEDGE SEARCH'S COMPLETENESS IS DECISION EVIDENCE, NOT PRESENTATION.

WHAT RUNS. The scheduled servicing pass (`ext_pinnacle_loop._funded_service`)
with ONLY the venue transport substituted -- the harness and scenario of
`test_xavier_search_completeness_and_policy_through_the_cycle` (8 sibling
pairs; an ACTIVE policy requiring a complete comparison). SYNTHETIC.

A. THE LADDER-RENDERING MODULE FAILS (every attribute raises). Management and
   recovery continue; the persisted decision still carries the SUPPLIER's
   actual account (`reasoning.search_account`, from `bettor_hedge_search`):
   READ_BUDGET_EXHAUSTED, 3 of 8 examined -- not UNKNOWN, not COMPLETE, and
   not blank. The pair is withheld by the policy on that evidence and the
   protective REDUCE is selected and sent. The presentation failure is named;
   the alternatives are not emptied.

B. THE SUPPLIER'S ACCOUNT ITSELF CANNOT BE COMPUTED on a search that would
   have been complete. Completeness is UNKNOWN (never COMPLETE); the policy
   requiring a complete comparison therefore withholds the pair it would
   otherwise have bought (case c of the source test), and HOLD / EXIT /
   REDUCE remain rankable. No hedge order is sent.
"""
from __future__ import annotations

import sys
import types

import pytest

from sportsassets import bettor_funded_decision as FD
from sportsassets import bettor_funded_hedge_supply as HSUP
from sportsassets import bettor_hedge_search as HS
from sportsassets.agents import audrey_audit as AA
from sportsassets.agents import xavier_policy as XP
from sportsassets.api import agents_xavier as AX
from tests import test_xavier_ladder_compares_every_spread as T
from tests import test_xavier_manages_positions_through_the_scheduled_path as H
from tests import test_xavier_search_completeness_and_policy_through_the_cycle as S

pg = H.pg
ACQ = FD.ACTION_ACQUIRE_INDIRECT_HEDGE


class _BrokenLadder(types.ModuleType):
    """The rendering module, deployed broken: every attribute raises."""

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        raise RuntimeError("ladder rendering broken (test): %s" % name)


def _break_the_ladder(monkeypatch):
    full = "sportsassets.agents.xavier_ladder"
    mod = _BrokenLadder(full)
    monkeypatch.setitem(sys.modules, full, mod)
    import sportsassets.agents as pkg
    monkeypatch.setattr(pkg, "xavier_ladder", mod, raising=False)


def _spent_budget_books():
    return S._ladder_books(held_bids=H.PROFIT_LADDER,
                           hedge_bids={1: (0.55, 500), 2: (0.45, 500),
                                       3: (0.35, 500), 4: (0.25, 500)})


@pg
@pytest.mark.asyncio
async def test_a_a_broken_ladder_keeps_the_suppliers_account_and_the_reduce(
        monkeypatch):
    conn = await H._connect()
    created = await S._policy_table(conn)
    try:
        await H.start(conn, p=0.55)
        await T.ladder_catalogue(conn)
        await S._activate(conn, "V1-complete-comparison",
                          requires_complete_comparison=True)
        monkeypatch.setattr(HSUP, "MAX_CANDIDATE_ROWS", 3)
        _break_the_ladder(monkeypatch)
        out, rec, venue = await S._serve(
            conn, monkeypatch, books=_spent_budget_books(),
            holdings={H.HELD: (10.0, 5.0)})
        # MANAGEMENT RAN AND RECORDED BEFORE DISPATCH
        assert out["funded_servicing"].get("ok") is not False
        # THE SUPPLIER'S ACTUAL ACCOUNT IS ON THE RECORD
        sa = rec["reasoning"]["search_account"]
        assert sa["complete"] is False
        assert sa["stop_reason"] == HS.STOP_BUDGET
        assert sa["discovered"] == 8 and sa["examined"] == 3
        assert sa["unexamined"] == 5
        assert sa["supplier_truncated_at_limit"] is True
        assert "BEST_AMONG_EXAMINED (3 of 8" in rec["reasoning"][
            "selection_scope"]
        # THE PRESENTATION FAILURE IS NAMED, NOT HIDDEN
        xl = rec["reasoning"]["xavier_ladder"]
        assert "search_completeness" not in xl
        blockers = [a.get("blocker") or "" for a in rec["alternatives"]
                    if a.get("action") == "XAVIER_LADDER"]
        assert any(b.startswith("XAVIER_LADDER_") for b in blockers), blockers
        # THE ALTERNATIVES WERE NOT EMPTIED; THE GATE USED THE EVIDENCE
        for act in ("HOLD", "DIRECT_EXIT", "REDUCE"):
            assert S._rankable(rec, act), act
        gate = rec["reasoning"]["search_policy_gate"]
        assert gate["applied"] is True
        assert gate["search_complete"] is False
        assert gate["search_stop_reason"] == HS.STOP_BUDGET
        assert T.nyy(1) in gate["withheld"]
        pair = H.alternative(rec, ACQ, T.nyy(1))
        assert pair["rankable"] is False
        assert pair["blocker"] == XP.R_INCOMPLETE_SEARCH
        # THE PROTECTIVE REDUCE IS SELECTED AND SENT AS PLANNED
        assert rec["chosen_action"] == "REDUCE"
        plan, c = S._assert_the_plan_is_the_order(rec, venue)
        assert c["marketSlug"] == H.HELD
        assert c["intent"] == "ORDER_INTENT_SELL_LONG"
        assert int(c["quantity"]) == 7
        # AUDREY AND THE WORKSPACE READ THE SAME EVIDENCE
        got = AA.search_completeness(rec)
        assert got["status"] == "INCOMPLETE:%s" % HS.STOP_BUDGET
        assert got["examined"] == 3 and got["left_unexamined"] == 5
        ws = await AX.workspace(conn)
        row = next(r for r in ws["sections"]["reviews"]["data"]["current"]
                   if r["xavier_decision_id"] == rec["xavier_decision_id"])
        assert row["search_completeness"]["stop_reason"] == HS.STOP_BUDGET
    finally:
        await H.clean(conn)
        await S._drop_policy(conn, created)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_b_an_uncomputable_account_is_unknown_and_buys_nothing(
        monkeypatch):
    conn = await H._connect()
    created = await S._policy_table(conn)
    try:
        await H.start(conn, p=0.55)
        await T.ladder_catalogue(conn, lines=(1,))
        await S._activate(conn, "V1-complete-comparison",
                          requires_complete_comparison=True)

        def _raises(facts):
            raise ValueError("supplier account broken (test)")
        monkeypatch.setattr(HS, "search_account", _raises)
        out, rec, venue = await S._serve(
            conn, monkeypatch,
            books=S._ladder_books(held_bids=H.PROFIT_LADDER,
                                  hedge_bids={1: (0.55, 500)}),
            holdings={H.HELD: (10.0, 5.0)})
        sa = rec["reasoning"]["search_account"]
        assert sa["complete"] is None
        assert sa["stop_reason"] == HS.STOP_UNKNOWN
        assert sa["error"] == "ValueError"
        assert "COMPLETENESS_UNKNOWN" in rec["reasoning"]["selection_scope"]
        gate = rec["reasoning"]["search_policy_gate"]
        assert gate["applied"] is True and gate["search_complete"] is None
        assert T.nyy(1) in gate["withheld"]
        # the pair case c would have bought is refused on unknown evidence
        assert gate["would_have_selected"][0] == ACQ
        assert rec["chosen_action"] != "ACQUIRE_HEDGE"
        for act in ("HOLD", "DIRECT_EXIT", "REDUCE"):
            assert S._rankable(rec, act), act
        for c in venue.creates_sent():
            assert c["marketSlug"] == H.HELD, c
            assert c["intent"] == "ORDER_INTENT_SELL_LONG", c
        got = AA.search_completeness(rec)
        assert got["status"] != "COMPLETE" and got["limited"] is True
    finally:
        await H.clean(conn)
        await S._drop_policy(conn, created)
        await conn.close()


def test_unknown_is_never_complete():
    u = HS.unknown("X")
    assert u["complete"] is None and u["stop_reason"] == HS.STOP_UNKNOWN
    got = XP.gate_incomplete_search(
        [{"action": ACQ, "rankable": True, "candidate_id": "c1",
          "value_usd": 1.0}],
        {"params": {"requires_complete_comparison": True}}, u)
    assert got["applied"] is True and got["withheld"] == ["c1"]
    assert AA.search_completeness(
        {"reasoning": {"search_account": u}})["limited"] is True
