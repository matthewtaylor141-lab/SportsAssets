"""CAPITAL-CRITICAL: XAVIER'S REVIEW AND ITS CANONICAL MANAGEMENT INTENT ARE ONE
WRITE (RC6.3c pass-hardening, X).

Found by an independent review of 5979416f (probe test_p3): a paper pass cut
between Xavier's protective order and his `paper_xavier_reviews` INSERT left a
`canonical_management_intents` row whose `review_id` never got a review row.
The intent was recorded EARLY in `review_group` (before the action), the
protective order and the two adapters followed, and the review row came last,
so a cut anywhere in between (the reviewer hung the adapter hook; the step
bound, or a restart, does the same) left an orphan intent -- and its adapter
executions -- that no review explains.

Now the canonical intent, what the two adapters did with it
(canonical_intent_executions, the parity ledger) and the review row are
written in ONE transaction, after the action. A cut leaves BOTH or NEITHER.
What the action did before that stays (the protective order is its own
committed write) and the next review finds it standing: the one-live-
protective-order invariant holds, so no second protective order is placed.

Proved here on a real Postgres through the real paper pass (the production
steps, the canonical hooks installed as the executing process installs them),
on the reviewer's own scaffold (one completed-game decision, filled, handed to
Xavier):

  * the pass is cut in the adapter hook (FAILS ON THE BASE: an intent whose
    review_id has no review): afterwards there is no intent, no adapter
    execution, no review of the group, exactly ONE protective order -- and
    the next pass writes the review with its intent, still ONE live protective
    order, the ledger consistent, no intent without its review;
  * the pass is cut in the record hook the same way;
  * a pass cut AFTER the review was written (the management assessment hung)
    leaves the review and its intent, paired (passes on the base too);
  * an adapter that raises -- or one whose own SQL fails -- or a record hook
    that raises or refuses never loses the review (each runs in a savepoint of
    its own, so it cannot abort the transaction the review rides on).

SYNTHETIC rows and books on a scratch test database; no venue is called and
SMALL LIVE stays SHADOW.
"""
from __future__ import annotations

import asyncio
import json
import time

import asyncpg
import pytest

from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_session as S
from sportsassets import decision_hooks as DH
from sportsassets.agents import paper_runtime as PRT
from sportsassets.agents import xavier_management as XM

from tests import paper_harness as H
from tests import paper_live_fixture as PL
from tests.test_rc63_one_paper_decision_through_every_duty import (  # noqa: F401
    LEVELS, P_PIN, _clean_allie_inputs, _nosleep, _seed_allie_inputs,
    cg_with_canonical_hooks)

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")

#: a guard so a candidate that cannot bound a pass FAILS instead of hanging
GUARD_S = 60.0


async def _pool():
    pool = await asyncpg.create_pool(H.DSN, min_size=1, max_size=3)

    async def get_pool():
        return pool
    return pool, get_pool


async def _run_once(get_pool, acct, t, now, client, steps=None):
    t.t = max(t.t, float(now))
    return await asyncio.wait_for(PRT.run_once(
        get_pool, trigger="TEST_RC63C_XAVIER", now=now,
        account_id=acct["account_id"], market_data=client,
        config=acct["config"], force=True, fee_fn=None, sleep=_nosleep,
        steps=steps), GUARD_S)


async def _prepared(conn, get_pool, tag):
    """The reviewer's scaffold: one completed-game ENTER, its entry filled and
    handed to Xavier -- WITHOUT a review of the group yet (the xavier step is
    left out of the two passes that build it)."""
    await PL.purge_everything(conn)
    await PL.purge_research_models(conn)
    await _clean_allie_inputs(conn)
    if await conn.fetchval("SELECT count(*) FROM execmirror_control") == 0:
        await conn.execute("INSERT INTO execmirror_control DEFAULT VALUES")
    now = time.time() + 5.0
    acct = await PL.new_account(conn, tag, now=now)
    t = PL.Transport(now)
    v = await PL.valuation(conn, decided_at=now - 10, p_pin=P_PIN,
                           compatibility="INCOMPATIBLE")
    slug = v["slug"]
    await _seed_allie_inputs(conn, slug=slug, now=now)
    t.set(slug, offers=LEVELS, bids=[(0.48, 2000)])
    client = PL.client(t)
    no_xav = [s for s in PRT.default_steps() if s[0] != "xavier"]
    r1 = await _run_once(get_pool, acct, t, now, client, steps=no_xav)
    assert r1["ran"] and not r1["errors"], r1["errors"]
    r2 = await _run_once(get_pool, acct, t, now + 5, client, steps=no_xav)
    assert r2["ran"] and not r2["errors"], r2["errors"]
    o = await conn.fetchrow(
        "SELECT * FROM paper_orders WHERE account_id=$1 AND role='ENTRY'",
        acct["account_id"])
    assert o is not None and o["state"] == "FILLED", o and o["state"]
    gid = o["group_id"]
    assert await conn.fetchval(
        "SELECT count(*) FROM paper_xavier_reviews WHERE group_id=$1",
        gid) == 0
    assert await conn.fetchval(
        "SELECT count(*) FROM paper_handoffs WHERE group_id=$1", gid) == 1
    assert DH.CANONICAL_MANAGEMENT_ADAPTERS is not None
    assert DH.CANONICAL_MANAGEMENT_RECORD is not None
    return acct, t, client, gid, now


async def _state(conn, gid):
    live_states = list(L.OPEN_STATES)
    return {
        "reviews": [r["review_id"] for r in await conn.fetch(
            "SELECT review_id FROM paper_xavier_reviews WHERE group_id=$1 "
            "ORDER BY reviewed_at", gid)],
        "intents": [r["review_id"] for r in await conn.fetch(
            "SELECT review_id FROM canonical_management_intents WHERE "
            "group_id=$1 ORDER BY created_at", gid)],
        "orphan_intents": await conn.fetchval(
            "SELECT count(*) FROM canonical_management_intents i WHERE "
            "i.group_id=$1 AND NOT EXISTS (SELECT 1 FROM "
            "paper_xavier_reviews r WHERE r.review_id = i.review_id)", gid),
        "executions": await conn.fetchval(
            "SELECT count(*) FROM canonical_intent_executions e JOIN "
            "canonical_management_intents i ON i.intent_id = e.intent_id "
            "WHERE e.intent_kind='MANAGEMENT' AND i.group_id=$1", gid),
        "protective_orders": await conn.fetchval(
            "SELECT count(*) FROM paper_orders WHERE group_id=$1 AND "
            "role='STANDING_PROTECTION'", gid),
        "live_protective_orders": await conn.fetchval(
            "SELECT count(*) FROM paper_orders WHERE group_id=$1 AND "
            "role='STANDING_PROTECTION' AND state = ANY($2::text[])", gid,
            live_states)}


def _restore_pass(monkeypatch):
    monkeypatch.setattr(PRT, "HARD_TIMEOUT_S", 90.0)
    monkeypatch.setattr(PRT, "PASS_RECORD_RESERVE_S", 10.0)


def _tight_pass(monkeypatch):
    """A 14 s pass with 4 s kept for the record: the xavier step is cut at
    10 s (the reviewer's probe constants). The decision steps, which could owe
    an ENTER, are not started in it (that is N2 of this lane); the position
    under review was entered and filled by the earlier passes."""
    monkeypatch.setattr(PRT, "HARD_TIMEOUT_S", 14.0)
    monkeypatch.setattr(PRT, "PASS_RECORD_RESERVE_S", 4.0)


# ═════════════════════════════════════════════════════════════════════
# A CUT BETWEEN THE PROTECTIVE ORDER AND THE REVIEW ROW
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_a_pass_cut_in_the_adapter_hook_leaves_neither_intent_nor_review_and_no_second_protective_order(
        cg_with_canonical_hooks, monkeypatch):
    """FAILS ON THE BASE: the cut leaves a canonical_management_intents row
    (and its adapter executions) with no review row."""
    conn = await H.connect()
    pool, get_pool = await _pool()
    try:
        acct, t, client, gid, now = await _prepared(conn, get_pool, "phx1")

        async def hang_adapter(*a, **k):
            await asyncio.sleep(3600)
        real_adapters = DH.CANONICAL_MANAGEMENT_ADAPTERS
        monkeypatch.setattr(DH, "CANONICAL_MANAGEMENT_ADAPTERS", hang_adapter)
        _tight_pass(monkeypatch)
        cut = await _run_once(get_pool, acct, t, now + 70, client)
        assert cut["ran"] is True, cut
        assert cut["exceeded_step"] == "xavier", cut["errors"]
        assert (await S.health(conn, acct["session_id"]))["passes"] == 3

        s = await _state(conn, gid)
        # NEITHER: no review, no intent, no adapter execution
        assert s["reviews"] == [], s
        assert s["intents"] == [], s
        assert s["orphan_intents"] == 0 and s["executions"] == 0, s
        # the protective order the action placed stands, and it is the ONE
        assert s["protective_orders"] == 1, s
        assert s["live_protective_orders"] == 1, s

        # the next pass makes the review that was never written -- both, once
        monkeypatch.setattr(DH, "CANONICAL_MANAGEMENT_ADAPTERS", real_adapters)
        _restore_pass(monkeypatch)
        nxt = await _run_once(get_pool, acct, t, now + 140, client)
        assert nxt["ran"] is True and "xavier" not in nxt["errors"], \
            nxt["errors"]
        s = await _state(conn, gid)
        assert len(s["reviews"]) >= 1, s
        assert s["intents"] == s["reviews"], "every intent has its review: " \
            + json.dumps(s)
        assert s["orphan_intents"] == 0, s
        assert s["executions"] == 2 * len(s["reviews"]), \
            "the PAPER and the SMALL_LIVE (SHADOW) adapter, per review"
        assert s["protective_orders"] == 1 and \
            s["live_protective_orders"] == 1, "never a second protective order"
        b = await L.balances(conn, acct["account_id"], now=now + 141)
        assert b["ledger_consistent"] is True
        assert len(b["open_positions"]) == 1
    finally:
        await _clean_allie_inputs(conn)
        await pool.close()
        await conn.close()


@pg
async def test_a_pass_cut_in_the_record_hook_leaves_neither_intent_nor_review(
        cg_with_canonical_hooks, monkeypatch):
    """The record hook hangs. Neither is written -- and, since the intent is
    now recorded AFTER the action, the protective order is already placed (on
    the base the hang came first and the position was left bare until a later
    review: that assertion fails there)."""
    conn = await H.connect()
    pool, get_pool = await _pool()
    try:
        acct, t, client, gid, now = await _prepared(conn, get_pool, "phx2")

        async def hang_record(*a, **k):
            await asyncio.sleep(3600)
        monkeypatch.setattr(DH, "CANONICAL_MANAGEMENT_RECORD", hang_record)
        _tight_pass(monkeypatch)
        cut = await _run_once(get_pool, acct, t, now + 70, client)
        assert cut["ran"] is True and cut["exceeded_step"] == "xavier"
        s = await _state(conn, gid)
        assert s["reviews"] == [] and s["intents"] == [], s
        assert s["orphan_intents"] == 0 and s["executions"] == 0, s
        assert s["protective_orders"] == 1 and \
            s["live_protective_orders"] == 1, s
    finally:
        await _clean_allie_inputs(conn)
        await pool.close()
        await conn.close()


@pg
async def test_a_pass_cut_after_the_review_was_written_leaves_the_review_and_its_intent_together(
        cg_with_canonical_hooks, monkeypatch):
    """Passes on the base too: BOTH is also a legal outcome of a cut."""
    conn = await H.connect()
    pool, get_pool = await _pool()
    try:
        acct, t, client, gid, now = await _prepared(conn, get_pool, "phx3")

        async def hang_assessment(*a, **k):
            await asyncio.sleep(3600)
        monkeypatch.setattr(XM, "paper_review_hook", hang_assessment)
        _tight_pass(monkeypatch)
        cut = await _run_once(get_pool, acct, t, now + 70, client)
        assert cut["ran"] is True and cut["exceeded_step"] == "xavier"
        s = await _state(conn, gid)
        assert len(s["reviews"]) == 1, s
        assert s["intents"] == s["reviews"], s
        assert s["orphan_intents"] == 0 and s["executions"] >= 2, s
        assert s["protective_orders"] == 1, s
        row = await conn.fetchrow(
            "SELECT action FROM paper_xavier_reviews WHERE group_id=$1", gid)
        act = row["action"]
        act = json.loads(act) if isinstance(act, str) else dict(act)
        assert act["canonical_intent_id"].startswith("cmi_")
        assert act["live_parity"].get("paper")       # the adapters' answer
    finally:
        await _clean_allie_inputs(conn)
        await pool.close()
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# A HOOK THAT FAILS NEVER LOSES THE REVIEW (savepoints)
# ═════════════════════════════════════════════════════════════════════

async def _one_review(conn, get_pool, acct, t, client, now):
    res = await _run_once(get_pool, acct, t, now + 70, client)
    assert res["ran"] is True and "xavier" not in res["errors"], res["errors"]


async def _review_action(conn, gid):
    row = await conn.fetchrow(
        "SELECT action FROM paper_xavier_reviews WHERE group_id=$1", gid)
    assert row is not None, "the review was written"
    act = row["action"]
    return json.loads(act) if isinstance(act, str) else dict(act)


@pg
async def test_an_adapter_that_raises_or_fails_its_own_sql_does_not_lose_the_review(
        cg_with_canonical_hooks, monkeypatch):
    conn = await H.connect()
    pool, get_pool = await _pool()
    try:
        acct, t, client, gid, now = await _prepared(conn, get_pool, "phx4")

        async def sql_error_adapter(c, intent, *, taken, open_qty):
            await c.execute("SELECT 1/0")        # aborts a bare transaction
        monkeypatch.setattr(DH, "CANONICAL_MANAGEMENT_ADAPTERS",
                            sql_error_adapter)
        await _one_review(conn, get_pool, acct, t, client, now)
        s = await _state(conn, gid)
        assert len(s["reviews"]) == 1 and s["intents"] == s["reviews"], s
        act = await _review_action(conn, gid)
        assert act["live_parity"] == {"error": "DivisionByZeroError"}
        assert act["canonical_intent_id"].startswith("cmi_")
        assert s["protective_orders"] == 1
    finally:
        await _clean_allie_inputs(conn)
        await pool.close()
        await conn.close()


@pg
async def test_a_record_hook_that_raises_or_refuses_does_not_lose_the_review(
        cg_with_canonical_hooks, monkeypatch):
    conn = await H.connect()
    pool, get_pool = await _pool()
    try:
        acct, t, client, gid, now = await _prepared(conn, get_pool, "phx5")

        async def sql_error_record(c, intent):
            await c.execute("SELECT 1/0")
        monkeypatch.setattr(DH, "CANONICAL_MANAGEMENT_RECORD",
                            sql_error_record)
        await _one_review(conn, get_pool, acct, t, client, now)
        s = await _state(conn, gid)
        assert len(s["reviews"]) == 1 and s["intents"] == [], s
        act = await _review_action(conn, gid)
        assert "canonical_intent_id" not in act and "live_parity" not in act
        # nothing was recorded for an intent that was not recorded
        assert s["executions"] == 0 and s["orphan_intents"] == 0
    finally:
        await _clean_allie_inputs(conn)
        await pool.close()
        await conn.close()


@pg
async def test_a_record_hook_that_refuses_leaves_a_review_without_an_intent(
        cg_with_canonical_hooks, monkeypatch):
    conn = await H.connect()
    pool, get_pool = await _pool()
    try:
        acct, t, client, gid, now = await _prepared(conn, get_pool, "phx6")

        async def refuses(c, intent):
            return False
        monkeypatch.setattr(DH, "CANONICAL_MANAGEMENT_RECORD", refuses)
        await _one_review(conn, get_pool, acct, t, client, now)
        s = await _state(conn, gid)
        assert len(s["reviews"]) == 1 and s["intents"] == [], s
        assert "canonical_intent_id" not in await _review_action(conn, gid)
    finally:
        await _clean_allie_inputs(conn)
        await pool.close()
        await conn.close()


@pg
async def test_the_normal_review_writes_the_intent_the_adapters_and_the_review_together(
        cg_with_canonical_hooks):
    """The unchanged happy path: one review, its intent (same review_id), the
    PAPER and SMALL_LIVE adapter executions (SHADOW) and the parity."""
    conn = await H.connect()
    pool, get_pool = await _pool()
    try:
        acct, t, client, gid, now = await _prepared(conn, get_pool, "phx7")
        await _one_review(conn, get_pool, acct, t, client, now)
        s = await _state(conn, gid)
        assert len(s["reviews"]) == 1 and s["intents"] == s["reviews"], s
        assert s["executions"] == 2 and s["orphan_intents"] == 0, s
        act = await _review_action(conn, gid)
        assert act["live_parity"]["live"] and act["live_parity"]["paper"]
        modes = {r["adapter"]: r["mode"] for r in await conn.fetch(
            "SELECT e.adapter, e.mode FROM canonical_intent_executions e "
            "JOIN canonical_management_intents i ON i.intent_id = "
            "e.intent_id WHERE i.group_id=$1", gid)}
        assert modes == {"PAPER": "SIMULATED", "SMALL_LIVE": "SHADOW"}
        assert await conn.fetchval(
            "SELECT count(*) FROM small_live_order_events") == 0
    finally:
        await _clean_allie_inputs(conn)
        await pool.close()
        await conn.close()
