"""TIMESTAMP INTEGRITY. The clocks must be observations, not repairs.

Four separate claims are tested here, because they failed separately:

  1. `detected_ts` is the OBSERVED receipt instant, preserved verbatim
     even when it precedes the fill's own stamp. The old code wrote
     max(source_ts, detected_at) into it, so the column did not hold
     what it was named after and the real value was never persisted.
  2. `decision_ts` on a prospective run comes from the RUNTIME clock. The
     old code set it equal to the (derived) detected_ts to satisfy a
     CHECK, which backdated every prospective decision to the instant its
     evidence arrived.
  3. A prospective run whose evidence is stale is REFUSED, not recorded.
  4. An order cannot consume a print that executed before the order came
     into existence -- enforced in the run loop AND, independently, by a
     trigger in the schema.
"""

from __future__ import annotations

import os

import pytest

from sportsassets import bettor_rn1x_run as R


# A condition with two participants. The seed fill carries the chain
# lane's real shape: `detected_at` BEFORE `ts`, which is the ordinary case
# (RN1's chain median is -0.6 s over 77,712 fills) and which the old
# CHECK refused outright.
SEED_TS = 1_700_000_000.0
SEED_DET = SEED_TS - 0.6            # received BEFORE its own stamp


def _rows(*extra):
    seed = {"id": 1, "whale_id": 7, "outcome_index": 0, "side": "BUY",
            "size": 100.0, "price": 0.40, "ts": SEED_TS,
            "detected_at": SEED_DET}
    return [seed, *extra]


def _run(**kw):
    kw.setdefault("rows", _rows())
    kw.setdefault("source_whale_id", 7)
    kw.setdefault("condition_id", "c-clock")
    kw.setdefault("initial_inventory_verified", True)
    return R.run(**kw)


# ── 1 · the observed clocks survive ─────────────────────────────────

def test_detected_ts_is_preserved_even_when_it_precedes_the_source():
    out = _run()
    src = out["steps"]["SOURCE"]
    assert src["ok"] is True
    # THE POINT: the raw receipt instant is what is reported, not max().
    assert src["detected_ts"] == SEED_DET, (
        "detected_ts must be the observed receipt instant, not "
        "max(source_ts, detected_at)")
    assert src["source_ts"] == SEED_TS
    # The conservative value still exists -- separately.
    assert src["available_at"] == SEED_TS
    assert src["available_at"] == max(SEED_TS, SEED_DET)
    assert src["clock_skew_s"] == pytest.approx(SEED_TS - SEED_DET)


def test_availability_can_only_delay_never_advance():
    out = _run()
    src = out["steps"]["SOURCE"]
    assert src["available_at"] >= src["source_ts"]
    assert src["available_at"] >= src["detected_ts"]


# ── 2 · the decision instant is observed, not assigned ──────────────

def test_a_replay_is_labelled_a_replay():
    out = _run()
    assert out["decision_basis"] == R.BASIS_REPLAY
    assert out["steps"]["SEED"]["decision_ts"] == out["steps"]["SOURCE"][
        "available_at"], "a replay decides at availability, by definition"
    assert out["decision_lag_s"] == 0.0


def test_a_prospective_run_uses_the_runtime_clock():
    now = SEED_TS + 12.0
    out = _run(now=now, decision_basis=R.BASIS_RUNTIME)
    assert out["decision_basis"] == R.BASIS_RUNTIME
    seed = out["steps"]["SEED"]
    assert seed["ok"] is True
    # NOT equal to any detection stamp. That equality was the defect.
    assert seed["decision_ts"] == now
    assert seed["decision_ts"] != out["steps"]["SOURCE"]["detected_ts"]
    assert out["decision_lag_s"] == pytest.approx(12.0)


def test_a_runtime_basis_without_a_clock_refuses_rather_than_falls_back():
    """The silent fallback is the whole bug. Asking for the runtime basis
    and getting the availability instant instead is what produced a row
    that looked prospective and was not."""
    out = _run(decision_basis=R.BASIS_RUNTIME)      # no `now`
    assert out["failed_step"] == "SEED"
    assert out["steps"]["SEED"]["refusal"] == R.R_RUNTIME_CLOCK_NOT_SUPPLIED


def test_a_clock_behind_the_feed_is_refused():
    out = _run(now=SEED_TS - 60.0, decision_basis=R.BASIS_RUNTIME)
    assert out["failed_step"] == "SEED"
    assert out["steps"]["SEED"]["refusal"] == \
        R.R_DECISION_PRECEDES_AVAILABILITY


# ── 3 · stale evidence is refused, not recorded ─────────────────────

def test_a_delayed_prospective_cycle_is_refused():
    """The traced position was written by a cycle that ran about an hour
    after the fill it was seeded from. That is not a prospective decision
    and it must not be recorded as one."""
    late = SEED_TS + R.MAX_PROSPECTIVE_DECISION_LAG_S + 1.0
    out = _run(now=late, decision_basis=R.BASIS_RUNTIME)
    assert out["failed_step"] == "SEED"
    s = out["steps"]["SEED"]
    assert s["refusal"] == R.R_PROSPECTIVE_LAG_EXCEEDED
    assert s["lag_s"] > R.MAX_PROSPECTIVE_DECISION_LAG_S
    # And it says why in terms of what the order could have consumed.
    assert "did not exist" in s["why"]


def test_the_limit_is_not_applied_to_a_replay():
    """A replay of a market from March is not 'stale evidence' -- it is a
    counterfactual, and refusing it would break the historical lane."""
    out = _run()
    assert out["steps"]["SEED"]["ok"] is True


# ── 4 · a fill cannot precede its order ─────────────────────────────

def test_prints_executed_before_the_order_existed_are_skipped():
    """Received after we decided, but EXECUTED before our order existed.
    The old loop tested only the receipt clock, so on a delayed cycle it
    admitted exactly these."""
    now = SEED_TS + 30.0
    # A print that we RECEIVE after the decision (so the old filter let it
    # through) but whose own execution instant is before the order.
    stale_print = {"id": 2, "whale_id": 9, "outcome_index": 0,
                   "side": "BUY", "size": 10.0, "price": 0.41,
                   "ts": now - 5.0,            # executed BEFORE the order
                   "detected_at": now + 1.0}   # received AFTER the decision
    fresh_print = {"id": 3, "whale_id": 9, "outcome_index": 0,
                   "side": "BUY", "size": 10.0, "price": 0.42,
                   "ts": now + 2.0,            # executed after
                   "detected_at": now + 3.0}
    out = _run(rows=_rows(stale_print, fresh_print),
               now=now, decision_basis=R.BASIS_RUNTIME)
    man = out["steps"]["MANAGE"]
    assert man["order_created_ts"] == now
    assert man["prints_skipped_before_order_creation"] == 1, (
        "the print that executed before the order existed must be skipped")
    # The fresh one was NOT skipped -- otherwise this test would pass by
    # skipping everything.
    ids = [e["evidence_id"] for e in man["events"]]
    assert "trade:2" not in ids
    assert man["prints_skipped_before_order_creation"] == 1


# ── against the real schema ─────────────────────────────────────────

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="RN1X_TEST_DSN is not set")

_POS = ("INSERT INTO rn1x_positions (position_id, experiment_id, policy, "
        "source_trade_id, source_account, condition_id, outcome_index, "
        "entry_kind, entry_kind_why, seed_qty, seed_price, seed_basis_usd, "
        "source_ts, detected_ts, decision_ts, available_at, decision_basis) "
        "VALUES ($1,$2,'P',$3,'A','c',0,'NEW','why',1,0.4,0.4,"
        "to_timestamp($4),to_timestamp($5),to_timestamp($6),"
        "to_timestamp($7),$8)")


async def _wipe(conn, eid):
    """Children first. `rn1x_orders` references the position, so deleting
    positions alone raises a foreign-key violation on a re-run."""
    await conn.execute(
        "DELETE FROM rn1x_fills WHERE order_id IN (SELECT order_id FROM "
        "rn1x_orders WHERE position_id IN (SELECT position_id FROM "
        "rn1x_positions WHERE experiment_id = $1))", eid)
    await conn.execute(
        "DELETE FROM rn1x_orders WHERE position_id IN (SELECT position_id "
        "FROM rn1x_positions WHERE experiment_id = $1)", eid)
    await conn.execute(
        "DELETE FROM rn1x_positions WHERE experiment_id = $1", eid)


async def _experiment(conn, eid):
    await conn.execute(
        "INSERT INTO rn1x_experiments (experiment_id, code_version, "
        "seed_rule, policy_register, execution_basis, notes) "
        "VALUES ($1,'V','{}'::jsonb,'{}'::jsonb,'MODELLED','clock test') "
        "ON CONFLICT (experiment_id) DO NOTHING", eid)


@pg
@pytest.mark.asyncio
async def test_the_schema_accepts_the_chain_lanes_real_clock_order():
    """The row shape that killed the prospective lane. `detected_ts`
    before `source_ts` is ordinary, and the schema must stop calling it a
    violation -- that constraint is what forced the backdating."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _experiment(conn, "E_CLOCK")
        await _wipe(conn, "E_CLOCK")
        await conn.execute(
            _POS, "p-skew", "E_CLOCK", 1,
            SEED_TS, SEED_DET, SEED_TS + 5.0, SEED_TS,
            "RUNTIME_WALL_CLOCK")
        got = await conn.fetchrow(
            "SELECT extract(epoch FROM detected_ts)::float8 d, "
            "extract(epoch FROM available_at)::float8 a "
            "FROM rn1x_positions WHERE position_id = 'p-skew'")
        assert got["d"] == pytest.approx(SEED_DET), \
            "the receipt instant must be stored as observed"
        assert got["a"] == pytest.approx(SEED_TS)
    finally:
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_schema_still_refuses_the_things_that_matter():
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _experiment(conn, "E_CLOCK")
        # availability that is EARLIER than an observed clock: the derived
        # value would be claiming information arrived before it did.
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(_POS, "p-bad-avail", "E_CLOCK", 2,
                               SEED_TS, SEED_DET, SEED_TS + 5.0,
                               SEED_TS - 10.0, "RUNTIME_WALL_CLOCK")
        # deciding before the evidence was available
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(_POS, "p-early", "E_CLOCK", 3,
                               SEED_TS, SEED_DET, SEED_TS - 1.0,
                               SEED_TS, "RUNTIME_WALL_CLOCK")
        # an undeclared basis
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(_POS, "p-basis", "E_CLOCK", 4,
                               SEED_TS, SEED_DET, SEED_TS + 5.0,
                               SEED_TS, "PROSPECTIVE_HONEST")
    finally:
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_trigger_refuses_a_fill_that_precedes_its_order():
    """Independent of the run loop. Even a writer that got the filter
    wrong cannot store a fill against a print that predates the order."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _experiment(conn, "E_CLOCK")
        await _wipe(conn, "E_CLOCK")
        await conn.execute(_POS, "p-fill", "E_CLOCK", 5,
                           SEED_TS, SEED_DET, SEED_TS + 30.0, SEED_TS,
                           "RUNTIME_WALL_CLOCK")
        await conn.execute(
            "INSERT INTO rn1x_orders (order_id, position_id, condition_id, "
            "outcome_index, side, intent, liquidity, limit_price, qty, "
            "state, placed_at, fill_basis, created_at_runtime, "
            "created_at_basis) VALUES ('o1','p-fill','c',0,'BUY','ENTER',"
            "'TAKER',0.4,10,'OPEN',to_timestamp($1),'B',to_timestamp($1),"
            "'RUNTIME_WALL_CLOCK')", SEED_TS + 30.0)

        fill = ("INSERT INTO rn1x_fills (fill_id, order_id, at, qty, price, "
                "fee_usd, evidence_id, queue_share, fill_basis) "
                "VALUES ($1,'o1',to_timestamp($2),1,0.4,0.01,'e',0.25,'B')")

        # BEFORE the order actually existed -> refused by the trigger.
        with pytest.raises(asyncpg.exceptions.RaiseError):
            await conn.execute(fill, "f-early", SEED_TS + 29.0)

        # At or after -> stored. Without this half the test would pass
        # against a trigger that refused everything.
        await conn.execute(fill, "f-ok", SEED_TS + 31.0)
        assert await conn.fetchval(
            "SELECT count(*) FROM rn1x_fills WHERE order_id = 'o1'") == 1
    finally:
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_pre_existing_rows_are_labelled_not_silently_migrated():
    """Migration 104 must LABEL the rows it cannot repair. The receipt
    instant on those rows was overwritten and is unrecoverable, so the
    only honest thing is to say so on the row."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _experiment(conn, "E_OLD")
        await _wipe(conn, "E_OLD")
        # A row in the OLD shape: decision_ts == detected_ts == max(...),
        # and no basis declared, exactly as the previous code wrote it.
        await conn.execute(
            "INSERT INTO rn1x_positions (position_id, experiment_id, policy,"
            " source_trade_id, source_account, condition_id, outcome_index,"
            " entry_kind, entry_kind_why, seed_qty, seed_price, "
            "seed_basis_usd, source_ts, detected_ts, decision_ts) "
            "VALUES ('p-old','E_OLD','P',9,'A','c',0,'NEW','w',1,0.4,0.4,"
            "to_timestamp($1),to_timestamp($1),to_timestamp($1))", SEED_TS)
        # Re-run the migration's labelling pass, which is idempotent.
        await conn.execute(
            open("migrations/104_rn1x_clock_integrity.sql").read())
        row = await conn.fetchrow(
            "SELECT decision_basis, clock_integrity FROM rn1x_positions "
            "WHERE position_id = 'p-old'")
        assert row["decision_basis"] == "BACKDATED_TO_AVAILABILITY_UNAUDITED"
        assert "does NOT establish a prospective decision" in \
            row["clock_integrity"]
    finally:
        await conn.close()
