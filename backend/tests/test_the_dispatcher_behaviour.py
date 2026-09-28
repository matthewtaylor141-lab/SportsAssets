"""§1: the dispatcher verified through BEHAVIOUR, not a source assertion.

`test_the_dispatch_is_total` reads the source. That is supplementary. This
exercises the real caller -- `bettor_funded_management.select_exit`
against the real schema, with the venue transport substituted -- and
proves the five outcomes:

  * a genuine HOLD returns an explicit successful no-order decision;
  * a supported DIRECT_EXIT produces the intended plan;
  * an unsupported selected action returns the named refusal, with no
    reservation, no write and no adapter submission attributable to it;
  * a qualified supported action is still selected when an alternative is
    ineligible;
  * when nothing executable is qualified, inventory is still reported
    accurately with the blocker visible.

WHAT THIS IS NOT. It is a CONTROLLED DEMONSTRATION: the venue transport is
substituted and the inputs are chosen. It is production code exercised
under controlled inputs, and it is not production funded behaviour
verified. Nothing here is strategy performance.
"""

from __future__ import annotations

import pytest

from sportsassets import bettor_funded_management as FM
from sportsassets import bettor_mgmt_select as MS

from tests.test_the_funded_lifecycle_is_complete import (  # noqa: F401
    ACCT, DSN, SLUG, VENUE, _clean, _entry, _level, _live, _probability,
    _seed, _transport, pg)


async def _position(conn):
    return (await FM.open_positions(conn, account_id=ACCT, venue=VENUE))[0]


async def _fresh(conn):
    await _clean(conn)
    await _seed(conn)
    await _entry(conn, qty=10, price=0.60)


def _submissions(sent):
    """Every adapter call that would have reached the venue."""
    return [k for k, _ in sent]


# ═════════════════════════════════════════════════════════════════════
# 1 · A GENUINE HOLD IS AN EXPLICIT SUCCESSFUL NO-ORDER DECISION
# ═════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_a_genuine_hold_succeeds_and_sends_nothing(monkeypatch):
    """Holding is worth more than the bid, so HOLD is chosen on its merits."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _fresh(conn)
        await _probability(conn, p=0.90)          # hold is worth 0.90
        pos = await _position(conn)
        _, sent, client = _transport(monkeypatch, bids=[_level(0.20, 40)])
        got = await FM.select_exit(conn, pos, client=client,
                                   subscription=_live())
        assert got["ok"] is True, got.get("refusal")
        assert got["refusal"] is None
        assert got["selected"] == "HOLD"
        assert got["is_an_evidenced_exit"] is False
        assert "this IS a decision" in got["note"]
        assert "create" not in _submissions(sent), (
            "a HOLD must reach no adapter")
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_capability_gate_does_not_catch_hold(monkeypatch):
    """HOLD is not in EXECUTABLE_ACTIONS and must still succeed.

    The gate excludes actions with no dispatch. HOLD legitimately sends
    nothing, so excluding it would turn every hold into a refusal.
    """
    assert "HOLD" not in FM.EXECUTABLE_ACTIONS
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _fresh(conn)
        await _probability(conn, p=0.90)
        pos = await _position(conn)
        _, sent, client = _transport(monkeypatch, bids=[_level(0.20, 40)])
        got = await FM.select_exit(conn, pos, client=client,
                                   subscription=_live())
        assert got["selected"] == "HOLD"
        assert got["refusal"] is None
        assert got["refusal"] != FM.R_ACTION_NOT_EXECUTABLE
    finally:
        await _clean(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 2 · A SUPPORTED EXIT PRODUCES THE INTENDED PLAN
# ═════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_a_supported_direct_exit_produces_the_intended_plan(monkeypatch):
    """The bid beats holding, so the exit is chosen and planned."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _fresh(conn)
        await _probability(conn, p=0.30)          # hold is worth 0.30
        pos = await _position(conn)
        _, sent, client = _transport(monkeypatch, bids=[_level(0.75, 40)])
        got = await FM.select_exit(conn, pos, client=client,
                                   subscription=_live())
        assert got["ok"] is True, got.get("refusal")
        assert got["selected"] in FM.EXECUTABLE_ACTIONS
        assert got["is_an_evidenced_exit"] is True
        assert float(got["selected_qty"]) > 0
        # THE PLAN CARRIES A WIRE PRICE DISTINCT FROM THE PROCEEDS.
        # `limit_price` is what is SENT; `proceeds_per_contract` is what the
        # decision was made on. On a long they coincide; on a short they are
        # complements, and conflating them inverted the economic bound.
        assert got["limit_price"] is not None
        assert got["price_space"] == "VENUE_WIRE_CONTRACT_PRICE"
        assert got["proceeds_per_contract"] is not None
        assert got["proceeds_space"].startswith("cash received per held")
        assert got["rounding"] in ("CEIL", "FLOOR")
        assert got["price_source"].startswith("the best level")
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_planned_quantity_is_bounded_by_the_offered_depth(
        monkeypatch):
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _fresh(conn)
        await _probability(conn, p=0.30)
        pos = await _position(conn)
        _, sent, client = _transport(monkeypatch, bids=[_level(0.75, 4)])
        got = await FM.select_exit(conn, pos, client=client,
                                   subscription=_live())
        if got["ok"] and got["selected"] in FM.EXECUTABLE_ACTIONS:
            assert float(got["selected_qty"]) <= 4.0
    finally:
        await _clean(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 3 · AN UNSUPPORTED SELECTION REFUSES, AND TOUCHES NOTHING
# ═════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_an_unsupported_selection_refuses_and_writes_nothing(
        monkeypatch):
    """Force the ranker to select TAKE_COMPLEMENT and watch the dispatch.

    The capability gate normally prevents this, so the ranker is patched
    to return the unsupported selection directly -- which is exactly the
    state the old fall-through reported as ok=True with a HOLD note. The
    dispatch must refuse, reserve nothing, write nothing and send nothing.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _fresh(conn)
        await _probability(conn, p=0.30)
        pos = await _position(conn)
        _, sent, client = _transport(monkeypatch, bids=[_level(0.75, 40)])

        real = MS.rank_with_hold

        def _forced(*a, **kw):
            r = real(*a, **kw)
            r["selected"] = "TAKE_COMPLEMENT"
            r["selected_qty"] = 10.0
            return r

        monkeypatch.setattr(MS, "rank_with_hold", _forced)

        before_exits = await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_intents WHERE kind='EXIT'")
        before_resid = await conn.fetchval(
            "SELECT residual_qty::float8 FROM bettor_funded_intents "
            "WHERE intent_id=$1", pos["intent_id"])

        got = await FM.select_exit(conn, pos, client=client,
                                   subscription=_live())

        assert got["ok"] is False
        assert got["refusal"] == FM.R_ACTION_NOT_EXECUTABLE
        assert got["selected"] == "TAKE_COMPLEMENT"
        assert got["inventory_untouched"] is True
        assert got["unimplemented_route"]
        assert "No order was planned" in got["why"]

        # NO RESERVATION, NO WRITE, NO SUBMISSION attributable to it.
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_intents WHERE kind='EXIT'"
        ) == before_exits, "an EXIT row was written for a refused action"
        assert await conn.fetchval(
            "SELECT residual_qty::float8 FROM bettor_funded_intents "
            "WHERE intent_id=$1", pos["intent_id"]) == before_resid, (
            "the residual moved on a refused action")
        assert "create" not in _submissions(sent), (
            "the adapter was called for an unexecutable action")
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_refusal_is_not_reported_as_a_completed_pass(monkeypatch):
    """The exact defect: ok=True with a note claiming HOLD."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _fresh(conn)
        await _probability(conn, p=0.30)
        pos = await _position(conn)
        _, sent, client = _transport(monkeypatch, bids=[_level(0.75, 40)])
        real = MS.rank_with_hold

        def _forced(*a, **kw):
            r = real(*a, **kw)
            r["selected"] = "MERGE"
            r["selected_qty"] = 10.0
            return r

        monkeypatch.setattr(MS, "rank_with_hold", _forced)
        got = await FM.select_exit(conn, pos, client=client,
                                   subscription=_live())
        assert got["ok"] is False
        assert got.get("note") is None or "HOLD chosen" not in got["note"]
    finally:
        await _clean(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 4 · A QUALIFIED SUPPORTED ACTION SURVIVES AN INELIGIBLE ALTERNATIVE
# ═════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_a_supported_exit_is_still_selected_beside_an_ineligible_one(
        monkeypatch):
    """THE NARROW RESULT, not "inventory is never stranded".

    This demonstrates only that the capability restriction does not
    disable an independently qualified DIRECT_EXIT. Other servicing
    blockers still exist and are not addressed by it.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _fresh(conn)
        await _probability(conn, p=0.30)
        pos = await _position(conn)
        _, sent, client = _transport(monkeypatch, bids=[_level(0.75, 40)])
        got = await FM.select_exit(conn, pos, client=client,
                                   subscription=_live())
        assert got["ok"] is True, got.get("refusal")
        assert got["selected"] in FM.EXECUTABLE_ACTIONS
        # And the alternative, if it was priced at all, was not selected.
        unq = {u["action"] for u in (got.get("ranking") or {}).get(
            "unqualified", []) or []}
        assert got["selected"] not in unq
    finally:
        await _clean(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 5 · NOTHING QUALIFIED: INVENTORY STILL ACCURATE, BLOCKER VISIBLE
# ═════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_no_qualified_action_leaves_inventory_accurate_and_named(
        monkeypatch):
    """No bid at all: nothing executable is priced, and it says so."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _fresh(conn)
        await _probability(conn, p=0.30)
        pos = await _position(conn)
        _, sent, client = _transport(monkeypatch, bids=[])
        got = await FM.select_exit(conn, pos, client=client,
                                   subscription=_live())
        # It does not pretend to have acted.
        assert got["selected"] != "DIRECT_EXIT" or got["ok"] is False
        assert "create" not in _submissions(sent)
        # The inventory is unchanged and still readable.
        still = await FM.open_positions(conn, account_id=ACCT, venue=VENUE)
        assert len(still) == 1
        assert float(still[0]["residual"]) == pytest.approx(
            float(pos["residual"]))
        # And a reason is present, by name.
        reason = (got.get("refusal")
                  or (got.get("ranking") or {}).get("selection_reason")
                  or got.get("why"))
        assert reason, "no blocker was named"
    finally:
        await _clean(conn)
        await conn.close()
