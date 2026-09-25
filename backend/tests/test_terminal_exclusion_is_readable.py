"""TERMINAL EXCLUSION MUST BE READABLE, OR IT IS NOT VERIFIABLE.

WHAT FAILED, AND WHY IT WOULD HAVE FAILED FOREVER. The successive-cycles
check ("RN1X — the SAME position across successive cycles", step 19 in the
job log) requires TWO complete priced HOLD decisions on the SAME position
at two distinct instants. The acceptance position's fixture is over: the
odds provider stops carrying a finished game, the input chain records
`first_failing_link 3_PROVIDER_FIXTURE`, and no EV_HOLD can ever be
identified for it again. So the check's demand is unsatisfiable for that
position -- not because management is broken, but because management is
the wrong terminal path for a position whose fixture has finished. Its
terminal path is SETTLEMENT.

A check cannot be repaired into asking the right question until the right
question has an answer somewhere. "It has left recurring management" was
not readable through any route, so these tests hold the reader to the one
property that makes the repaired check meaningful: `open_to_management`
must agree with the manager's OWN open-position query, position for
position, in every state -- unreleased, part-released, fully released,
scored-but-not-terminal, and terminally settled.

IT AGREES BECAUSE IT IS THE SAME PREDICATE. `command_rn1x.POSITIONS_SQL`
interpolates `bettor_rn1x_store.NOT_TERMINALLY_SETTLED` rather than
restating it. A test that only checked the happy case would pass just as
well against two predicates that happen to agree today, so the
disagreeing states are the ones enumerated below.
"""

from __future__ import annotations

import os

import pytest

from sportsassets import bettor_rn1x_store as store
from sportsassets.api import command_rn1x as CR

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="RN1X_TEST_DSN is not set")

EXPERIMENT = "TERMINAL_EXCLUSION_TEST_EXPERIMENT"
POLICY = "TERMINAL_EXCLUSION_TEST_POLICY"
CONDITION = "c-terminal-exclusion"

POS_SQL = """
    INSERT INTO rn1x_positions (position_id, experiment_id, policy,
        source_trade_id, source_account, condition_id, outcome_index,
        entry_kind, entry_kind_why, seed_qty, seed_price, seed_basis_usd,
        source_ts, detected_ts, decision_ts, decision_basis, provenance)
    VALUES ($1,$2,$3,NULL,'t',$4,0,'NEW','t',100.0,0.60,60.0,
            now(),now(),now(),'RUNTIME_WALL_CLOCK',
            'ACCEPTANCE_SYNTHETIC_MODELLED_ENTRY')
    ON CONFLICT (position_id) DO NOTHING
"""

ORD_SQL = """
    INSERT INTO rn1x_orders (order_id, position_id, decision_id,
        condition_id, outcome_index, side, intent, liquidity,
        limit_price, qty, placed_at, state, fill_basis)
    VALUES ($1,$2,NULL,$3,0,'SELL','EXIT','TAKER',0.80,$4,now(),
            'FILLED','MODELLED')
    ON CONFLICT (order_id) DO NOTHING
"""

FILL_SQL = """
    INSERT INTO rn1x_fills (fill_id, order_id, at, qty, price, fee_usd,
        evidence_id, queue_share, fill_basis)
    VALUES ($1,$2,now(),$3,0.80,0.0,'terminal-exclusion-evidence',
            1.0,'MODELLED')
    ON CONFLICT (fill_id) DO NOTHING
"""

OUT_SQL = """
    INSERT INTO rn1x_outcomes (position_id, outcome_basis,
        residual_qty, net_usd, fees_usd, settled_at)
    VALUES ($1,$2,0.0,10.0,0.5,now())
    ON CONFLICT (position_id) DO UPDATE
       SET outcome_basis = EXCLUDED.outcome_basis
"""


async def _experiment(conn):
    await conn.execute(
        "INSERT INTO rn1x_experiments (experiment_id, code_version, "
        "seed_rule, policy_register, execution_basis, notes) "
        "VALUES ($1,'V','{}'::jsonb,'{}'::jsonb,'MODELLED','t') "
        "ON CONFLICT (experiment_id) DO NOTHING", EXPERIMENT)
    await conn.execute(
        "INSERT INTO markets (condition_id, title, event_title, slug, "
        "sport, closed, resolved) VALUES ($1,'t','t',$2,'MLB',false,false) "
        "ON CONFLICT (condition_id) DO NOTHING", CONDITION,
        "terminal-exclusion-fixture")


async def _position(conn, suffix, *, released=0.0, basis=None):
    pid = "%s:POS:%s" % (EXPERIMENT, suffix)
    await conn.execute(POS_SQL, pid, EXPERIMENT, POLICY, CONDITION)
    if released:
        oid = "%s:O" % pid
        await conn.execute(ORD_SQL, oid, pid, CONDITION, released)
        await conn.execute(FILL_SQL, "%s:F" % oid, oid, released)
    if basis is not None:
        await conn.execute(OUT_SQL, pid, basis)
    return pid


async def _cleanup(conn):
    for sql in (
            "DELETE FROM rn1x_outcomes WHERE position_id LIKE $1",
            "DELETE FROM rn1x_fills WHERE order_id IN (SELECT order_id "
            "FROM rn1x_orders WHERE position_id LIKE $1)",
            "DELETE FROM rn1x_orders WHERE position_id LIKE $1",
            "DELETE FROM rn1x_decisions WHERE position_id LIKE $1",
            "DELETE FROM rn1x_positions WHERE position_id LIKE $1"):
        try:
            await conn.execute(sql, "%s:POS:%%" % EXPERIMENT)
        except Exception:                                      # noqa: BLE001
            pass
    for sql, a in (("DELETE FROM markets WHERE condition_id = $1",
                    CONDITION),
                   ("DELETE FROM rn1x_experiments WHERE experiment_id = $1",
                    EXPERIMENT)):
        try:
            await conn.execute(sql, a)
        except Exception:                                      # noqa: BLE001
            pass


def test_the_reader_borrows_the_managers_predicate_rather_than_restating_it():
    """A second copy of the predicate is a second thing to get wrong, and
    it would make the agreement below a coincidence instead of a fact."""
    assert store.NOT_TERMINALLY_SETTLED in CR.POSITIONS_SQL
    assert CR.TERMINAL_OUTCOME_BASES == store.TERMINAL_OUTCOME_BASES
    # AND THE BASIS THAT IS DELIBERATELY NOT TERMINAL stays out of it.
    assert "OBSERVED_PAYOUT_SCORING_ONLY" not in CR.POSITIONS_SQL


@pg
@pytest.mark.asyncio
async def test_open_to_management_agrees_with_the_manager_in_every_state():
    """FIVE POSITIONS, FIVE STATES, ONE ANSWER EACH -- and the answer is
    compared against `store.open_positions`, which is the query the
    scheduled manager actually runs."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _cleanup(conn)
        await _experiment(conn)
        held = await _position(conn, "held")
        part = await _position(conn, "part", released=40.0)
        gone = await _position(conn, "gone", released=100.0)
        # SCORED BUT NOT TERMINAL. An observation recorded for measurement
        # must NOT stop management: excluding every outcome row was the
        # wrong fix in the other direction.
        scored = await _position(conn, "scored",
                                 basis="OBSERVED_PAYOUT_SCORING_ONLY")
        settled = await _position(conn, "settled",
                                  basis=store.BASIS_VENUE_SETTLED)
        voided = await _position(conn, "void",
                                 basis=store.BASIS_VENUE_VOID)

        managed = {r["position_id"] for r in await store.open_positions(
            conn, experiment_id=EXPERIMENT, limit=100)}
        rows = {r["position_id"]: r for r in await conn.fetch(
            CR.POSITIONS_SQL, 100, POLICY)}
        assert set(rows) >= {held, part, gone, scored, settled, voided}

        # THE MANAGER'S ANSWER, STATED.
        assert held in managed and part in managed and scored in managed
        assert gone not in managed
        assert settled not in managed and voided not in managed

        # AND THE READER'S, POSITION FOR POSITION.
        for pid in rows:
            assert rows[pid]["open_to_management"] is (pid in managed), (
                pid, dict(rows[pid]))

        # THE TWO COMPONENTS ARE REPORTED APART, so a reader can say WHY a
        # position left management rather than only that it did.
        assert rows[settled]["terminally_settled"] is True
        assert rows[voided]["terminally_settled"] is True
        assert rows[scored]["terminally_settled"] is False
        assert rows[gone]["terminally_settled"] is False
        assert rows[gone]["released_qty"] == pytest.approx(100.0)
        assert rows[part]["released_qty"] == pytest.approx(40.0)
        assert rows[held]["released_qty"] == pytest.approx(0.0)
        # AND THE BASIS TRAVELS, so "excluded" can be attributed to the
        # venue's own settlement rather than to an unnamed condition.
        assert rows[settled]["outcome_basis"] == store.BASIS_VENUE_SETTLED
        assert rows[voided]["outcome_basis"] == store.BASIS_VENUE_VOID
    finally:
        await _cleanup(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_settled_position_leaves_management_between_two_reads():
    """THE TRANSITION ITSELF, which is what the repaired check validates:
    the same position is open, then a terminal settlement is recorded, then
    it is excluded -- by the manager and by the reader together."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _cleanup(conn)
        await _experiment(conn)
        pid = await _position(conn, "transition")

        async def read():
            row = await conn.fetchrow(
                "SELECT * FROM (%s) q WHERE position_id = $3"
                % CR.POSITIONS_SQL.replace("LIMIT $1", "LIMIT $1"),
                100, POLICY, pid)
            managed = {r["position_id"] for r in await store.open_positions(
                conn, experiment_id=EXPERIMENT, limit=100)}
            return row, pid in managed

        row, managed = await read()
        assert managed is True and row["open_to_management"] is True

        await conn.execute(OUT_SQL, pid, store.BASIS_VENUE_SETTLED)

        row, managed = await read()
        assert managed is False, "the manager must stop selecting it"
        assert row["open_to_management"] is False
        assert row["terminally_settled"] is True
        assert row["residual_qty"] == pytest.approx(0.0)
    finally:
        await _cleanup(conn)
        await conn.close()
