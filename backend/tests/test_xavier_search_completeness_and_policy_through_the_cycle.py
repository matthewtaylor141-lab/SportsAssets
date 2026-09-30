"""SEARCH COMPLETENESS AND THE MANAGEMENT POLICY, THROUGH THE SCHEDULED PATH.

WHAT RUNS. `ext_pinnacle_loop._funded_service(conn, now=NOW)` -- the
scheduled servicing pass the worker runs (manage, the production pair-input
builder with the sibling search, `pass_once`, `decide_and_record`, Xavier's
record, the one-measure gate, the bound-plan claim and dispatch) -- with a
CONTROLLED clock (`now` is passed, read once per test) and ONLY the venue
transport substituted (the harness of `test_xavier_manages_positions_
through_the_scheduled_path`). Nothing is injected: candidates, rankings,
decisions and valuations are the production path's.

ITEM 1 -- SEARCH COMPLETENESS. The per-pass quote budget of the sibling
search (`bettor_funded_hedge_supply.MAX_CANDIDATE_ROWS`, its configured cap)
is set to 3 against a fixture with 8 sibling pairs, so the read budget is
spent with 5 candidates still unexamined. The limitation must be persisted on
the decision row (`reasoning.xavier_ladder.search_completeness`), returned by
the workspace endpoint, worded "best among examined (N of M; K unexamined:
reason)", and never suppress EXIT / REDUCE; a policy requiring a complete
comparison withholds the pair by name and the protective REDUCE proceeds.

ITEM 2 -- THE POLICY IN THE REAL SELECTOR. The ACTIVE `agent_policy_versions`
row (written by `xavier_policy.activate_version`, the owner's activation
writer) selects which decision function runs; the other policy is computed on
the same frozen inputs as `shadow_comparison`, displayed and never dispatched;
exactly one winner goes through binding, the plan digest, the claim and the
substituted venue -- asserted on the captured venue call.

SYNTHETIC evidence only; nothing here is evidence about any market.
"""
from __future__ import annotations

import inspect
import time

import pytest

from sportsassets import bettor_funded_decision as FD
from sportsassets import bettor_funded_hedge_supply as HSUP
from sportsassets.agents import xavier_ladder as XL
from sportsassets.agents import xavier_policy as XP
from sportsassets.api import agents_xavier as AX
from tests import test_xavier_ladder_compares_every_spread as T
from tests import test_xavier_manages_positions_through_the_scheduled_path as H

pg = H.pg
ACQ = FD.ACTION_ACQUIRE_INDIRECT_HEDGE
OWNER = "owner@test (SYNTHETIC APPROVAL)"

#: The policy table's shape as core's migration 152 declares it (the one
#: ACTIVE row per key, and an ACTIVE row names its approver). Created only
#: when this database lacks it, and dropped again after the test.
POLICY_DDL = """
CREATE TABLE agent_policy_versions (
    agent_id text NOT NULL, policy_key text NOT NULL, version text NOT NULL,
    params jsonb NOT NULL DEFAULT '{}'::jsonb,
    state text NOT NULL CHECK (state IN ('ACTIVE','CANDIDATE','REJECTED',
                                         'RETIRED')),
    created_by text NOT NULL, approved_by text, approved_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (agent_id, policy_key, version),
    CHECK (state <> 'ACTIVE' OR (approved_by IS NOT NULL
                                 AND approved_at IS NOT NULL)));
CREATE UNIQUE INDEX agent_policy_one_active_test
    ON agent_policy_versions (agent_id, policy_key) WHERE state = 'ACTIVE';
"""


async def _policy_table(conn) -> bool:
    if await conn.fetchval(
            "SELECT to_regclass('agent_policy_versions')") is not None:
        return False
    await conn.execute(POLICY_DDL)
    return True


async def _drop_policy(conn, created: bool):
    if created:
        await conn.execute("DROP TABLE IF EXISTS agent_policy_versions")
    elif await conn.fetchval(
            "SELECT to_regclass('agent_policy_versions')") is not None:
        await conn.execute(
            "DELETE FROM agent_policy_versions WHERE agent_id='XAVIER' "
            "  AND policy_key=$1", XP.POLICY_KEY)


async def _activate(conn, version, **params):
    got = await XP.activate_version(
        conn, version=version, params=params, approved_by=OWNER,
        created_by="synthetic-test", statement="SYNTHETIC TEST ACTIVATION")
    assert got["ok"] is True, got
    return got


async def _serve(conn, monkeypatch, *, books, holdings):
    """One scheduled servicing pass at a controlled `now`."""
    from sportsassets.workers import ext_pinnacle_loop as L
    venue = H.Venue(books=books, holdings=holdings)
    H.substitute(monkeypatch, venue)
    now = time.time()
    got = await L._funded_service(conn, now=now)
    assert got is not None and got.get("ok") is not False, got
    out = {"funded_servicing": got}
    recs = await H.xavier_records(conn)
    assert recs, got
    return out, recs[0], venue


def _rankable(rec, action):
    return [a for a in rec["alternatives"]
            if a.get("action") == action and a.get("rankable")]


def _only_create(venue):
    cr = venue.creates_sent()
    assert len(cr) == 1, venue.sent
    return cr[0]


def _assert_the_plan_is_the_order(rec, venue):
    """The captured venue call IS the persisted winning plan: instrument,
    side, quantity and limit price."""
    plan = rec["evidence"]["chosen_plan"]
    assert plan["digest"] == rec["chosen_plan_digest"]
    c = _only_create(venue)
    assert c["marketSlug"] == plan["us_market_slug"]
    assert c["intent"] == plan["order_intent"]
    assert int(c["quantity"]) == int(plan["quantity"])
    assert float(c["price"]["value"]) == pytest.approx(plan["limit_price"])
    return plan, c


def _ladder_books(**kw):
    return T.ladder_books(**kw)


# ════════════════════════════════════════════════════════════════════
# ITEM 1: THE READ BUDGET RUNS OUT WITH CANDIDATES UNEXAMINED
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_a_spent_read_budget_is_persisted_served_and_keeps_exit_and_reduce(
        monkeypatch):
    conn = await H._connect()
    try:
        await H.start(conn, p=0.55)
        await T.ladder_catalogue(conn)
        monkeypatch.setattr(HSUP, "MAX_CANDIDATE_ROWS", 3)
        out, rec, venue = await _serve(
            conn, monkeypatch,
            books=_ladder_books(held_bids=H.PROFIT_LADDER,
                                hedge_bids={1: (0.55, 500), 2: (0.45, 500),
                                            3: (0.35, 500), 4: (0.25, 500)}),
            holdings={H.HELD: (10.0, 5.0)})
        # ── PERSISTED ON THE DECISION ROW ────────────────────────────
        sc = rec["reasoning"]["xavier_ladder"]["search_completeness"]
        assert sc["complete"] is False
        assert sc["stop_reason"] == XL.STOP_BUDGET
        assert sc["discovered"] == 8 and sc["examined"] == 3
        assert sc["unexamined"] == 5
        assert sc["comparison_scope"] == (
            "BEST_AMONG_EXAMINED (3 of 8; 5 unexamined: "
            "READ_BUDGET_EXHAUSTED)")
        assert sc["account"]["supplier_truncated_at_limit"] is True
        assert sc["account"]["supplier_limit"] == 3
        assert sc["account"]["truncation_note"]
        assert "best available" not in rec["reasoning"]["selection_scope"]
        assert "BEST_AMONG_EXAMINED (3 of 8" in rec["reasoning"][
            "selection_scope"]
        # the unexamined +4.5 is not in the comparison; the examined are
        ids = {a.get("candidate_id") for a in rec["alternatives"]
               if a.get("action") == ACQ}
        assert T.nyy(4) not in ids and T.nyy(1) in ids
        # ── EXIT AND REDUCE ARE STILL SELECTABLE ─────────────────────
        assert _rankable(rec, "DIRECT_EXIT") and _rankable(rec, "REDUCE")
        assert _rankable(rec, "HOLD")
        # ── RETURNED BY THE WORKSPACE ENDPOINT ───────────────────────
        ws = await AX.workspace(conn)
        for name in ("reviews",):
            row = next(r for r in ws["sections"][name]["data"]["current"]
                       if r["xavier_decision_id"] == rec["xavier_decision_id"])
            assert row["search_completeness"]["stop_reason"] == XL.STOP_BUDGET
            assert row["search_completeness"]["unexamined"] == 5
        for name in ("ladder", "alternatives"):
            row = next(r for r in ws["sections"][name]["data"]
                       if r["xavier_decision_id"] == rec["xavier_decision_id"])
            assert row["search_completeness"]["comparison_scope"] == \
                sc["comparison_scope"]
        # and through the route itself
        from tests import test_xavier_workspace_reads_truthfully as W
        W._pool(monkeypatch)
        r = W._client(monkeypatch).get(W.WS, headers=W.AUTH)
        assert r.status_code == 200, r.text
        rv = [x for x in r.json()["sections"]["reviews"]["data"]["current"]
              if x["xavier_decision_id"] == rec["xavier_decision_id"]]
        assert rv[0]["search_completeness"]["complete"] is False
    finally:
        await H.clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_b_a_policy_requiring_a_complete_comparison_withholds_the_pair_not_the_reduce(
        monkeypatch):
    """The same spent budget, with an ACTIVE policy that requires a complete
    comparison: the +1.5 pair (which would win) is withheld by name BEFORE
    the choice, the record says so, and the protective REDUCE -- next best --
    is selected and dispatched exactly as its plan states."""
    conn = await H._connect()
    created = await _policy_table(conn)
    try:
        await H.start(conn, p=0.55)
        await T.ladder_catalogue(conn)
        await _activate(conn, "V1-complete-comparison",
                        requires_complete_comparison=True)
        monkeypatch.setattr(HSUP, "MAX_CANDIDATE_ROWS", 3)
        out, rec, venue = await _serve(
            conn, monkeypatch,
            books=_ladder_books(held_bids=H.PROFIT_LADDER,
                                hedge_bids={1: (0.55, 500), 2: (0.45, 500),
                                            3: (0.35, 500), 4: (0.25, 500)}),
            holdings={H.HELD: (10.0, 5.0)})
        pol = rec["reasoning"]["decision_policy"]
        assert pol["source"] == XP.SOURCE_TABLE
        assert pol["version"] == "V1-complete-comparison"
        assert pol["params"]["requires_complete_comparison"] is True
        gate = rec["reasoning"]["search_policy_gate"]
        assert gate["applied"] is True
        assert gate["search_stop_reason"] == XL.STOP_BUDGET
        assert T.nyy(1) in gate["withheld"]
        assert gate["would_have_selected"][0] == ACQ
        pair = H.alternative(rec, ACQ, T.nyy(1))
        assert pair["rankable"] is False
        assert pair["blocker"] == XP.R_INCOMPLETE_SEARCH
        # A REFUSED ACQUISITION DOES NOT ERASE THE MANAGEMENT ALTERNATIVES
        for act in ("HOLD", "DIRECT_EXIT", "REDUCE"):
            assert _rankable(rec, act), act
        assert rec["chosen_action"] == "REDUCE"
        plan, c = _assert_the_plan_is_the_order(rec, venue)
        assert c["marketSlug"] == H.HELD
        assert c["intent"] == "ORDER_INTENT_SELL_LONG"
        assert int(c["quantity"]) == 7
    finally:
        await H.clean(conn)
        await _drop_policy(conn, created)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_c_a_complete_search_lets_the_same_policy_choose_the_pair(
        monkeypatch):
    """The same policy, the full read budget: every sibling examined, the
    comparison COMPLETE, and the justified pair wins and is dispatched."""
    conn = await H._connect()
    created = await _policy_table(conn)
    try:
        await H.start(conn, p=0.55)
        await T.ladder_catalogue(conn, lines=(1,))
        await _activate(conn, "V1-complete-comparison",
                        requires_complete_comparison=True)
        out, rec, venue = await _serve(
            conn, monkeypatch,
            books=_ladder_books(held_bids=H.PROFIT_LADDER,
                                hedge_bids={1: (0.55, 500)}),
            holdings={H.HELD: (10.0, 5.0)})
        sc = rec["reasoning"]["xavier_ladder"]["search_completeness"]
        assert sc["complete"] is True and sc["stop_reason"] == \
            XL.STOP_COMPLETE
        assert sc["comparison_scope"].startswith("COMPLETE")
        assert rec["reasoning"]["search_policy_gate"]["applied"] is False
        assert rec["chosen_action"] == "ACQUIRE_HEDGE"
        plan, c = _assert_the_plan_is_the_order(rec, venue)
        assert c["marketSlug"] == T.LINES[1]
        assert c["intent"] == "ORDER_INTENT_BUY_SHORT"
        assert int(c["quantity"]) == 10
        assert float(c["price"]["value"]) == pytest.approx(0.55)
    finally:
        await H.clean(conn)
        await _drop_policy(conn, created)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# ITEM 2: THE POLICY SELECTS THE DECISION FUNCTION
# ════════════════════════════════════════════════════════════════════

#: HOLD 10 at 0.50 on p = 0.55 (HOLD +0.50, worst -5.00); no exit worth it
#: (bid 0.40); the Yankees +1.5 at 0.62 (bid 0.38): expected value BELOW
#: HOLD's by about $0.32, worst case about -$1.36. The approved policy holds;
#: CAPITAL_PRESERVATION_V1 with a $0.50 sacrifice takes the pair.
CP_BOOKS = dict(held_bids=[(0.40, 400)], hedge_bid=0.38)


@pg
@pytest.mark.asyncio
async def test_d_the_default_policy_preserves_the_existing_decision(
        monkeypatch):
    conn = await H._connect()
    try:
        await H.start(conn, p=0.55)
        out, rec, venue = await _serve(
            conn, monkeypatch, books=H.books(**CP_BOOKS),
            holdings={H.HELD: (10.0, 5.0)})
        pol = rec["reasoning"]["decision_policy"]
        assert pol["source"] == XP.SOURCE_CODE_DEFAULT
        assert pol["selection_rule"] == XP.SEL_EXPECTED_NET_VALUE
        assert pol["decision_function"] == "bettor_funded_decision.decide"
        assert pol["params"] == XP.default_params()
        # the approved policy's choice: HOLD (the highest expected value)
        assert rec["chosen_action"] == "HOLD"
        hold = H.alternative(rec, "HOLD")
        pair = H.alternative(rec, ACQ, H.HEDGE_CID)
        assert pair["expected_net_usd"] < hold["expected_net_usd"]
        assert pair["worst_case_net_usd"] > hold["worst_case_net_usd"]
        # THE SHADOW: what the candidate policy would have done -- shown,
        # never dispatched
        sh = rec["reasoning"]["shadow_comparison"]
        assert sh["is"] == "DISPLAYED_NEVER_DISPATCHED"
        assert sh["dispatched"] is False
        assert sh["policy"]["selection_rule"] == XP.SEL_CAPITAL_PRESERVATION
        assert sh["shadow_selected"]["selected"] == ACQ
        assert sh["shadow_selected"]["selected_candidate"][1] == H.HEDGE_CID
        assert 0 < sh["ev_given_up_by_capital_preservation_usd"] <= 0.5
        assert sh["downside_improved_by_capital_preservation_usd"] > 3.0
        assert venue.creates_sent() == []
    finally:
        await H.clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_e_the_candidate_policy_changes_the_decision_for_its_stated_reason(
        monkeypatch):
    conn = await H._connect()
    created = await _policy_table(conn)
    try:
        await H.start(conn, p=0.55)
        await _activate(conn, "CAPITAL_PRESERVATION_V1-a",
                        selection_rule=XP.SEL_CAPITAL_PRESERVATION,
                        max_ev_sacrifice_for_downside_usd=0.5)
        out, rec, venue = await _serve(
            conn, monkeypatch, books=H.books(**CP_BOOKS),
            holdings={H.HELD: (10.0, 5.0)})
        pol = rec["reasoning"]["decision_policy"]
        assert pol["source"] == XP.SOURCE_TABLE
        assert pol["version"] == "CAPITAL_PRESERVATION_V1-a"
        assert pol["selection_rule"] == XP.SEL_CAPITAL_PRESERVATION
        assert pol["decision_function"] == "agents.xavier_policy.decide"
        assert pol["approved_by"] == OWNER
        # THE CHANGED DECISION, AND ITS DOCUMENTED ECONOMIC REASON
        assert rec["chosen_action"] == "ACQUIRE_HEDGE"
        hold = H.alternative(rec, "HOLD")
        pair = H.alternative(rec, ACQ, H.HEDGE_CID)
        given_up = hold["expected_net_usd"] - pair["expected_net_usd"]
        assert 0 < given_up <= 0.5
        assert pair["worst_case_net_usd"] > hold["worst_case_net_usd"]
        pr = rec["reasoning"]["xavier_ladder"]["policy"]
        assert pr["identical_to_approved_ev_policy"] is False
        assert pr["applied_to_dispatch"] is True
        cons = pr["capital_preservation"]["consequence"]
        assert cons["expected_value_given_up_usd"] == pytest.approx(given_up)
        assert cons["worst_case_gained_usd"] == pytest.approx(
            pair["worst_case_net_usd"] - hold["worst_case_net_usd"])
        assert "CAPITAL_PRESERVATION_V1" in rec["reasoning"][
            "selection_reason"]
        # THE SHADOW IS THE APPROVED POLICY: it would have held
        sh = rec["reasoning"]["shadow_comparison"]
        assert sh["policy"]["selection_rule"] == XP.SEL_EXPECTED_NET_VALUE
        assert sh["shadow_selected"]["selected"] == "HOLD"
        assert sh["ev_given_up_by_capital_preservation_usd"] == \
            pytest.approx(given_up)
        # ONE WINNER, THROUGH THE ONE-MEASURE GATE, THE CLAIM AND THE VENUE
        step = H.step_of(out)
        assert step["funded_dispatch_gate"]["permitted"] is True, step[
            "funded_dispatch_gate"]
        assert step["funded_dispatch_gate"]["selection_basis"] == \
            "CAPITAL_PRESERVATION_ROBUST_ACROSS_THE_VOID_RATE_RANGE"
        plan, c = _assert_the_plan_is_the_order(rec, venue)
        assert c["marketSlug"] == H.SIB
        assert c["intent"] == "ORDER_INTENT_BUY_SHORT"
        assert int(c["quantity"]) == 10
        # the wire limit is the venue's YES-denominated price: a SHORT buy
        # at 0.38 costs 1 - 0.38 = 0.62 per contract
        assert float(c["price"]["value"]) == pytest.approx(0.38)
        ev = await H.XV.execution_events(
            conn, xavier_decision_id=rec["xavier_decision_id"])
        assert [e["event_kind"] for e in ev].count(H.XV.K_CLAIMED) == 1
    finally:
        await H.clean(conn)
        await _drop_policy(conn, created)
        await conn.close()


async def _under_candidate(conn, monkeypatch, *, p, price, books, holdings,
                           approve_model=True, lines=None):
    await H.start(conn, p=p, price=price, approve_model=approve_model)
    if lines:
        await T.ladder_catalogue(conn, lines=lines)
    await _activate(conn, "CAPITAL_PRESERVATION_V1-b",
                    selection_rule=XP.SEL_CAPITAL_PRESERVATION,
                    max_ev_sacrifice_for_downside_usd=0.5)
    return await _serve(conn, monkeypatch, books=books, holdings=holdings)


def _candidate_case(fn):
    """One case under the ACTIVE CAPITAL_PRESERVATION_V1, its own account
    start and cleanup."""
    async def _run(monkeypatch):
        conn = await H._connect()
        created = await _policy_table(conn)
        try:
            await fn(conn, monkeypatch)
        finally:
            await H.clean(conn)
            await _drop_policy(conn, created)
            await conn.close()
    _run.__name__ = fn.__name__
    _run.__doc__ = fn.__doc__
    return pg(pytest.mark.asyncio(_run))


@_candidate_case
async def test_f1_under_the_candidate_policy_hold_is_reachable(conn,
                                                               monkeypatch):
    """The pair far worse on expected value (outside the sacrifice): HOLD."""
    out, rec, venue = await _under_candidate(
        conn, monkeypatch, p=0.55, price=0.50,
        books=H.books(held_bids=[(0.52, 400)], hedge_bid=0.20),
        holdings={H.HELD: (10.0, 5.0)})
    assert rec["reasoning"]["decision_policy"]["selection_rule"] == \
        XP.SEL_CAPITAL_PRESERVATION
    assert rec["chosen_action"] == "HOLD" and venue.creates_sent() == []


@_candidate_case
async def test_f2_under_the_candidate_policy_a_full_exit_is_reachable(
        conn, monkeypatch):
    """The forward value collapsed: the full EXIT, dispatched as planned."""
    out, rec, venue = await _under_candidate(
        conn, monkeypatch, p=0.10, price=0.50,
        books=H.books(held_bids=[(0.45, 400)]),
        holdings={H.HELD: (10.0, 5.0)}, approve_model=False)
    assert rec["chosen_action"] == "EXIT", rec["chosen_action"]
    plan, c = _assert_the_plan_is_the_order(rec, venue)
    assert c["intent"] == "ORDER_INTENT_SELL_LONG"
    assert int(c["quantity"]) == 10


@_candidate_case
async def test_f3_under_the_candidate_policy_a_reduction_is_reachable(
        conn, monkeypatch):
    """Only part pays: REDUCE 7, dispatched as planned."""
    out, rec, venue = await _under_candidate(
        conn, monkeypatch, p=0.30, price=0.60,
        books=H.books(held_bids=[(0.45, 4), (0.42, 3), (0.10, 400)]),
        holdings={H.HELD: (10.0, 6.0)}, approve_model=False)
    assert rec["chosen_action"] == "REDUCE"
    plan, c = _assert_the_plan_is_the_order(rec, venue)
    assert int(c["quantity"]) == 7


@_candidate_case
async def test_f4_under_the_candidate_policy_the_justified_narrow_pair_beats_the_wide_dear_one(
        conn, monkeypatch):
    """The +1.5 at 0.45 is best on expected value AND worst case; the +4.5
    at 0.75 overlaps more and loses. The pair's plan is the venue's order."""
    out, rec, venue = await _under_candidate(
        conn, monkeypatch, p=0.55, price=0.50,
        books=T.ladder_books(held_bids=H.PROFIT_LADDER,
                             hedge_bids={1: (0.55, 500), 4: (0.25, 500)}),
        holdings={H.HELD: (10.0, 5.0)}, lines=(1, 4))
    assert rec["chosen_action"] == "ACQUIRE_HEDGE"
    narrow = H.alternative(rec, ACQ, T.nyy(1))
    wide = H.alternative(rec, ACQ, T.nyy(4))
    assert narrow["plan_digest"] == rec["chosen_plan_digest"]
    assert narrow["expected_net_usd"] > wide["expected_net_usd"]
    assert len(wide["settlement_compatibility"]["both_win_regions"]) > \
        len(narrow["settlement_compatibility"]["both_win_regions"])
    plan, c = _assert_the_plan_is_the_order(rec, venue)
    assert c["marketSlug"] == T.LINES[1]
    assert c["intent"] == "ORDER_INTENT_BUY_SHORT"
    assert int(c["quantity"]) == 10
    assert float(c["price"]["value"]) == pytest.approx(0.55)


def test_no_xavier_code_path_activates_or_approves_a_policy():
    """The activation writer is a person's; no decision or servicing module
    calls it, and an agent is refused as approver."""
    from sportsassets import bettor_funded_pair_cycle as PC
    from sportsassets import bettor_xavier as XV
    for mod in (PC, XV, XL):
        assert "activate_version" not in inspect.getsource(mod), mod
    import asyncio

    class _C:
        async def fetchval(self, *a):
            raise AssertionError("an agent approval must be refused first")
    for who in ("XAVIER", "agent:derek", ""):
        got = asyncio.new_event_loop().run_until_complete(
            XP.activate_version(_C(), version="x", params={},
                                approved_by=who, created_by="t"))
        assert got["ok"] is False
        assert got["refusal"] in (XP.R_AGENT_CANNOT_APPROVE,
                                  XP.R_APPROVER_REQUIRED)
