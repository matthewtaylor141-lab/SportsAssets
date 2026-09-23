"""REAL POSTGRES, not the fake store. The audit named this gap.

Its verification section said, exactly: "DB recovery tests use the
repository's fake transaction store; they are not a real-Postgres or
production restart acceptance." This module is a real-Postgres
acceptance for the RN1X persistence path.

It SKIPS rather than fails when no database is offered, because a test
that silently passes without a database would be a check that cannot
fail -- the defect pattern this project has been caught on repeatedly.
The skip names what is missing.

Point it at a throwaway database:

    RN1X_TEST_DSN=postgresql://postgres@/rn1x?host=/tmp/pgsock \\
        python -m pytest -q tests/test_rn1x_persistence_pg.py

WHAT IT PROVES, each against rows read back out of Postgres:

  1. `store.ready()` reads the LIVE CATALOG -- it reports the tables
     missing on a database that has none, and ok on one that has them.
  2. The worker FAILS CLOSED on the control row: absent, false and
     malformed all leave the tables empty.
  3. A real seed replays end to end and PERSISTS: position, decisions,
     orders, outcome, with the three clocks ordered by the schema's own
     CHECK rather than by our promise.
  4. RE-RUNNING IS IDEMPOTENT. A second cycle over the same evidence
     does not create a second position or double the decisions -- the
     restart case.
  5. A FAILED WRITE LEAVES NOTHING HALF-DONE. An error injected inside
     the position transaction rolls the whole position back, and the
     cursor does not advance past evidence that was never stored.
  6. EVERY DECISION IS PERSISTED, not only the ones that acted.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

asyncpg = pytest.importorskip("asyncpg")

DSN = os.environ.get("RN1X_TEST_DSN")

pytestmark = pytest.mark.skipif(
    not DSN,
    reason=("RN1X_TEST_DSN is not set. This is a REAL-POSTGRES "
            "acceptance and refuses to pass without one; the fake "
            "transaction store cannot demonstrate a rollback or a "
            "schema CHECK."))

T0 = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
COND = "0xcondition_rn1x_test"
WHALE = 424242


async def _reset(conn):
    await conn.execute(
        "TRUNCATE rn1x_fills, rn1x_orders, rn1x_decisions, rn1x_outcomes, "
        "rn1x_positions, rn1x_experiments CASCADE")
    await conn.execute("DELETE FROM trades WHERE condition_id = $1", COND)
    await conn.execute("DELETE FROM markets WHERE condition_id = $1", COND)
    await conn.execute(
        "DELETE FROM ingestion_state WHERE key IN "
        "('rn1x_shadow', 'rn1x_source_cursor', 'rn1x_learn')")


async def _seed_evidence(conn):
    """One real-shaped seed: a 100-contract BUY at .60 on outcome 0, then
    a later .30 print on outcome 1 that a resting completion can consume.

    The payout resolves outcome 0 to 1.0. The pair, if completed, cost
    .90 all-in before fees -- inside management's .91 ceiling -- which is
    the case the policy exists for.
    """
    await conn.execute(
        "INSERT INTO whales (id, address, username) VALUES ($1, $2, $3) "
        "ON CONFLICT (id) DO NOTHING",
        WHALE, "0xrn1xtest", "rn1x-test")
    await conn.execute(
        "INSERT INTO markets (condition_id, title, resolved, "
        "resolved_prices, resolved_at) VALUES ($1, $2, TRUE, "
        "'[1, 0]'::jsonb, $3) ON CONFLICT (condition_id) DO UPDATE SET "
        "resolved = TRUE, resolved_prices = '[1, 0]'::jsonb, "
        "resolved_at = EXCLUDED.resolved_at",
        COND, "rn1x test market", T0 + timedelta(hours=6))
    rows = [
        # (whale, outcome, side, size, price, ts_offset_s, det_offset_s)
        (WHALE, 0, "BUY", 100.0, 0.60, 0, 5),
        (999999, 1, "BUY", 400.0, 0.30, 600, 605),
        (999999, 0, "BUY", 50.0, 0.62, 1200, 1205),
    ]
    await conn.execute(
        "INSERT INTO whales (id, address, username) VALUES (999999, "
        "'0xother', 'other') ON CONFLICT (id) DO NOTHING")
    ids = []
    for i, (w, oi, side, size, price, off, det) in enumerate(rows):
        rid = await conn.fetchval(
            "INSERT INTO trades (whale_id, tx_hash, asset, condition_id, "
            "side, outcome_index, size, price, notional, ts, source, "
            "detected_at, dedupe_key) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,"
            "$10,'chain',$11,$12) RETURNING id",
            w, "0xtx%d" % i, "asset%d" % oi, COND, side, oi, size, price,
            size * price, T0 + timedelta(seconds=off),
            T0 + timedelta(seconds=det), "rn1x-test-%d" % i)
        ids.append(int(rid))
    return ids


@pytest.fixture
async def conn():
    c = await asyncpg.connect(DSN)
    await _reset(c)
    try:
        yield c
    finally:
        await _reset(c)
        await c.close()


# ── 1. ready() reads the live catalog ───────────────────────────────
async def test_ready_reads_the_live_catalog(conn):
    from sportsassets import bettor_rn1x_store as store

    r = await store.ready(conn)
    assert r["ok"] is True, r
    assert r["missing"] == []
    assert set(r["present"]) == set(store.TABLES)


async def test_ready_names_missing_tables_rather_than_raising(conn):
    """The blocker is REPORTED, not raised once per cycle.

    Migration 100 sat committed at HEAD while production had none of its
    tables. A loop that raised UndefinedTableError every twenty seconds
    would have buried that; a named blocker surfaces it.
    """
    from sportsassets import bettor_rn1x_store as store

    await conn.execute("CREATE SCHEMA IF NOT EXISTS rn1x_probe")
    # A schema with none of the tables: search_path is not consulted --
    # ready() asks information_schema for table_schema = 'public'
    # explicitly -- so this proves the query, not the session.
    r = await store.ready(conn)
    assert r["ok"] is True
    # and now the negative: rename one away and it is reported by name
    await conn.execute("ALTER TABLE rn1x_fills SET SCHEMA rn1x_probe")
    try:
        r2 = await store.ready(conn)
        assert r2["ok"] is False
        assert r2["missing"] == ["rn1x_fills"]
        assert r2["blocker"] == store.NOT_READY
    finally:
        await conn.execute("ALTER TABLE rn1x_probe.rn1x_fills SET SCHEMA public")


# ── 2. fail-closed control ──────────────────────────────────────────
@pytest.mark.parametrize("value,expected", [
    (None, "CONTROL_ROW_ABSENT"),
    ("false", "CONTROL_ROW_NOT_TRUE"),
    ('"yes please"', "CONTROL_ROW_NOT_TRUE"),
    ("0", "CONTROL_ROW_NOT_TRUE"),
])
async def test_control_row_fails_closed(conn, value, expected):
    from sportsassets.workers import rn1x_shadow as W

    await _seed_evidence(conn)
    if value is not None:
        await conn.execute(
            "INSERT INTO ingestion_state (key, value) VALUES "
            "('rn1x_shadow', $1::jsonb)", value)
    res = await W.cycle(conn)
    assert res["ran"] is False
    assert res["state"] == "STOPPED"
    assert res["why"] == expected
    # AND NOTHING WAS WRITTEN. The state string alone is not the point.
    assert await conn.fetchval("SELECT count(*) FROM rn1x_positions") == 0
    assert await conn.fetchval("SELECT count(*) FROM rn1x_experiments") == 0


# ── 3. a real seed, persisted ───────────────────────────────────────
async def _arm(conn):
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES "
        "('rn1x_shadow', 'true'::jsonb) ON CONFLICT (key) DO UPDATE "
        "SET value = 'true'::jsonb")


async def test_seed_replays_and_persists(conn):
    from sportsassets.workers import rn1x_shadow as W

    ids = await _seed_evidence(conn)
    await _arm(conn)
    # start the cursor just below the seed so the scan finds it
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES "
        "('rn1x_source_cursor', $1::jsonb)", str(ids[0] - 1))

    res = await W.cycle(conn)
    assert res["ran"] is True, res
    assert res["state"] == "REPLAYED", res

    pos = await conn.fetch("SELECT * FROM rn1x_positions")
    assert len(pos) == 1, [dict(p) for p in pos]
    p = pos[0]
    assert int(p["source_trade_id"]) == ids[0]
    assert float(p["seed_qty"]) == 100.0
    assert float(p["seed_price"]) == 0.60
    # THE THREE CLOCKS, ordered by the schema's CHECK, not by our word.
    assert p["source_ts"] <= p["detected_ts"] <= p["decision_ts"]

    # the experiment row carries the FROZEN policy register
    exp = await conn.fetchrow("SELECT * FROM rn1x_experiments")
    assert exp["execution_basis"] == W.EXECUTION_BASIS
    assert exp["is_modelled"] is True

    dec = await conn.fetch(
        "SELECT selection_reason, selected_action, ev_at_decision_usd, "
        "ev_basis, conditional_on_our_fill FROM rn1x_decisions "
        "WHERE position_id = $1", p["position_id"])
    assert dec, "no decisions persisted"
    # EV IS NOT CLAIMED. This policy is a cost-and-threshold rule, not an
    # expected-value maximiser, and a number in that column would invent
    # an objective it does not have.
    assert all(d["ev_at_decision_usd"] is None for d in dec)
    assert all(d["ev_basis"] == "NOT_COMPUTED_POLICY_IS_NOT_EV_MAXIMISING"
               for d in dec)
    # AND NO DECISION CLAIMS ITS EXECUTION IS SECURED.
    import json as _json
    for d in dec:
        c = d["conditional_on_our_fill"]
        c = _json.loads(c) if isinstance(c, str) else c
        assert c.get("execution_secured") is False, c


async def test_every_decision_is_persisted_not_only_the_acting_ones(conn):
    """The acted-on subset is the activity-looking subset.

    For this policy the most common true state is HOLD_NO_FEASIBLE_PAIR,
    and a table built from `steps.MANAGE.events` alone would omit exactly
    those rows -- showing the experiment as busier than it is.
    """
    from sportsassets import bettor_rn1x_run as runner
    from sportsassets.workers import rn1x_shadow as W

    ids = await _seed_evidence(conn)
    await _arm(conn)
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES "
        "('rn1x_source_cursor', $1::jsonb)", str(ids[0] - 1))

    out = await W._replay_one(conn, trade_id=ids[0], whale_id=WHALE,
                              condition_id=COND)
    assert out["mode"] == runner.HISTORICAL
    assert out["prospective"] is False
    all_d = out["all_decisions"]
    acted = [d for d in all_d if d.get("acted")]
    assert len(all_d) > len(acted), (
        "this fixture must contain at least one non-acting decision, "
        "or the test cannot distinguish the two lists")

    await W.cycle(conn)
    n = await conn.fetchval("SELECT count(*) FROM rn1x_decisions")
    assert n == len(all_d), (
        "persisted %d decisions but the run made %d" % (n, len(all_d)))


# ── 4. idempotence across a restart ─────────────────────────────────
async def test_second_cycle_does_not_duplicate(conn):
    from sportsassets.workers import rn1x_shadow as W

    ids = await _seed_evidence(conn)
    await _arm(conn)
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES "
        "('rn1x_source_cursor', $1::jsonb)", str(ids[0] - 1))
    await W.cycle(conn)
    before = (
        await conn.fetchval("SELECT count(*) FROM rn1x_positions"),
        await conn.fetchval("SELECT count(*) FROM rn1x_decisions"),
        await conn.fetchval("SELECT count(*) FROM rn1x_orders"),
    )
    assert before[0] == 1

    # THE RESTART: rewind the cursor exactly as a lost cursor would, and
    # run again over the same evidence.
    await conn.execute(
        "UPDATE ingestion_state SET value = $1::jsonb "
        "WHERE key = 'rn1x_source_cursor'", str(ids[0] - 1))
    await W.cycle(conn)
    after = (
        await conn.fetchval("SELECT count(*) FROM rn1x_positions"),
        await conn.fetchval("SELECT count(*) FROM rn1x_decisions"),
        await conn.fetchval("SELECT count(*) FROM rn1x_orders"),
    )
    assert after == before, ("a replay of the same evidence changed the "
                            "record: %r -> %r" % (before, after))


# ── 5. a failed write leaves nothing half-done ──────────────────────
async def test_failed_write_rolls_the_whole_position_back(conn, monkeypatch):
    """Orders with no position is the shape that made the desk uncertain.

    The injected failure lands AFTER the position insert and DURING the
    same transaction, which is the only interesting case: a failure
    before it writes nothing anyway.
    """
    from sportsassets import bettor_rn1x_store as store
    from sportsassets.workers import rn1x_shadow as W

    ids = await _seed_evidence(conn)
    await _arm(conn)
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES "
        "('rn1x_source_cursor', $1::jsonb)", str(ids[0] - 1))

    calls = {"n": 0}

    class Failing:
        """A proxy, because asyncpg's Connection.execute is read-only.

        Everything else -- including `transaction()` -- goes straight to
        the real connection, so the rollback under test is Postgres's
        own, not a simulation of one.
        """

        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        async def execute(self, query, *args, **kw):
            if "INSERT INTO rn1x_decisions" in query:
                calls["n"] += 1
                raise RuntimeError("injected write failure")
            return await self._inner.execute(query, *args, **kw)

    res = await W.cycle(Failing(conn))

    assert calls["n"] >= 1, "the failure was never injected"
    assert await conn.fetchval("SELECT count(*) FROM rn1x_positions") == 0, (
        "a position survived a transaction that failed midway")
    assert await conn.fetchval("SELECT count(*) FROM rn1x_orders") == 0
    assert any("error" in r for r in res.get("results", [])), res

    # THE CURSOR DID NOT MOVE PAST THE ROW THAT WAS NEVER STORED. This
    # is asserted, not described: a cursor saved above a failed write is
    # how the evidence would be lost permanently, and only reading the
    # saved value back can tell the two apart.
    saved = await conn.fetchval(
        "SELECT value::text FROM ingestion_state "
        "WHERE key = 'rn1x_source_cursor'")
    assert int(saved.strip('"')) < ids[0], (
        "the cursor advanced to %s, at or past the failed seed %s: the "
        "next cycle would never see it again" % (saved, ids[0]))
    assert res["stopped_at_error"] == ids[0], res

    # AND THE NEXT CYCLE PICKS IT UP WITHOUT ANY REWIND. Nothing here
    # resets the cursor -- if the previous assertion is the only thing
    # keeping this seed reachable, this is what proves it.
    res2 = await W.cycle(conn)
    assert res2["written"] >= 1, res2
    assert await conn.fetchval("SELECT count(*) FROM rn1x_positions") == 1


# ── 6. the initial-inventory verification is computed, not asserted ──
async def test_initial_inventory_refuses_without_coverage(conn):
    """A flag is not proof, which the audit said explicitly.

    Here the account's first RECORDED fill postdates the condition's
    first recorded trade, so a position held before we were watching
    cannot be ruled out -- and the verification says so instead of
    assigning inventory.
    """
    from sportsassets.workers import rn1x_shadow as W

    ids = await _seed_evidence(conn)
    # Give the OTHER account an earlier trade on a different condition so
    # the condition's first recorded trade precedes ours.
    await conn.execute(
        "UPDATE trades SET ts = $1, detected_at = $1 WHERE id = $2",
        T0 - timedelta(hours=2), ids[1])

    inv = await W.verify_initial_inventory(
        conn, whale_id=WHALE, condition_id=COND, trade_id=ids[0])
    assert inv["verified"] is False
    assert inv["coverage_precedes_condition"] is False
    assert "before we were watching" in inv["why_not"]

    out = await W._replay_one(conn, trade_id=ids[0], whale_id=WHALE,
                              condition_id=COND)
    assert out["failed_step"] == "CLASSIFY"
    assert out["steps"]["CLASSIFY"]["ok"] is False


async def test_refusal_is_reported_not_written_as_a_position(conn):
    from sportsassets import bettor_rn1x_store as store
    from sportsassets.workers import rn1x_shadow as W

    ids = await _seed_evidence(conn)
    await conn.execute(
        "UPDATE trades SET ts = $1, detected_at = $1 WHERE id = $2",
        T0 - timedelta(hours=2), ids[1])
    await _arm(conn)
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES "
        "('rn1x_source_cursor', $1::jsonb)", str(ids[0] - 1))

    res = await W.cycle(conn)
    assert res["written"] == 0, res
    assert await conn.fetchval("SELECT count(*) FROM rn1x_positions") == 0
    got = [r for r in res["results"] if r.get("refused_at")]
    assert got and got[0]["refused_at"] == "CLASSIFY", res


# ── 7. THE LEARNING CYCLE, against real rows ────────────────────────
#
# The audit's list of missing learning connections included "evaluation
# on new data" and "promotion/rejection receipts". These assert both, and
# assert the thing that matters most about this loop: that it CANNOT
# promote anything.

_LEARN_REGISTRY = """
CREATE TABLE IF NOT EXISTS bettor_learn_model (
    id BIGSERIAL PRIMARY KEY, model_key TEXT NOT NULL,
    version INTEGER NOT NULL, kind TEXT NOT NULL, target TEXT NOT NULL,
    horizon_s DOUBLE PRECISION NOT NULL, kernel TEXT NOT NULL,
    dataset_sha TEXT NOT NULL, train_cutoff DOUBLE PRECISION NOT NULL,
    calib_cutoff DOUBLE PRECISION NOT NULL, code_sha TEXT NOT NULL,
    params JSONB NOT NULL, evaluation JSONB NOT NULL, status TEXT NOT NULL,
    trained_at DOUBLE PRECISION NOT NULL, note TEXT, superseded_by BIGINT);
CREATE UNIQUE INDEX IF NOT EXISTS bettor_learn_model_kv
    ON bettor_learn_model (model_key, version);
"""


async def _armed_with_a_settled_position(conn):
    from sportsassets.workers import rn1x_shadow as W

    ids = await _seed_evidence(conn)
    await _arm(conn)
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES "
        "('rn1x_source_cursor', $1::jsonb)", str(ids[0] - 1))
    await W.cycle(conn)
    assert await conn.fetchval("SELECT count(*) FROM rn1x_outcomes") == 1
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES "
        "('rn1x_learn', 'true'::jsonb) ON CONFLICT (key) DO UPDATE "
        "SET value = 'true'::jsonb")
    return ids


async def test_learning_is_blocked_by_name_when_the_registry_is_absent(conn):
    """It uses the EXISTING register and says so when it is not there.

    The alternative -- creating its own table on the fly -- is the
    competing research stack the brief forbids, and it would be invisible
    until someone noticed two registers.
    """
    from sportsassets.workers import rn1x_learn_loop as WL

    await _armed_with_a_settled_position(conn)
    await conn.execute("DROP TABLE IF EXISTS bettor_learn_model")
    res = await WL.cycle(conn)
    assert res["state"] == "BLOCKED"
    assert res["why"] == "LEARN_REGISTRY_ABSENT"
    assert "does not create a competing register" in res["detail"]


async def test_learning_fails_closed_on_its_own_control_row(conn):
    from sportsassets.workers import rn1x_learn_loop as WL

    await _armed_with_a_settled_position(conn)
    await conn.execute("DELETE FROM ingestion_state WHERE key = 'rn1x_learn'")
    res = await WL.cycle(conn)
    assert res == {"ran": False, "state": "STOPPED",
                   "why": "CONTROL_ROW_ABSENT"}, res


async def test_learning_writes_a_receipt_for_every_challenger(conn):
    """A REJECTION IS A RESULT and is written exactly like an acceptance.

    On one position the gate's 50-decided floor cannot be met, so the
    expected verdict is RETAIN_CHAMPION with INELIGIBLE among the
    reasons. A loop that wrote nothing until it agreed with itself would
    leave the panel blank and look like it had not run.
    """
    from sportsassets import bettor_rn1x_learn as L
    from sportsassets.workers import rn1x_learn_loop as WL

    await _armed_with_a_settled_position(conn)
    await conn.execute(_LEARN_REGISTRY)
    try:
        res = await WL.cycle(conn)
        assert res["state"] == "EVALUATED", res
        assert res["positions"] == 1
        assert res["champion_retained"] is True

        rows = await conn.fetch(
            "SELECT version, params, status, evaluation FROM "
            "bettor_learn_model WHERE model_key = $1 ORDER BY version",
            L.MODEL_KEY)
        assert len(rows) == len(L.CHALLENGERS), (
            "%d receipts for %d challengers" % (len(rows), len(L.CHALLENGERS)))
        import json as _json
        seen = set()
        for r in rows:
            params = r["params"]
            params = _json.loads(params) if isinstance(params, str) else params
            seen.add(params["challenger"])
            # THE ONLY TWO LEGAL VERDICTS. A third would mean a promotion
            # path had been added.
            assert r["status"] in (L.RETAIN, L.ELIGIBLE), r["status"]
        assert seen == set(L.challenger_names()), seen

        # the gate's own reason must be preserved verbatim, not summarised
        ev = rows[0]["evaluation"]
        ev = _json.loads(ev) if isinstance(ev, str) else ev
        assert "INELIGIBLE" in ev["recommendation"]["why"], ev[
            "recommendation"]["why"]
        assert ev["scenarios"] == [0.10, 0.25, 0.50]
    finally:
        await conn.execute("DROP TABLE IF EXISTS bettor_learn_model")


async def test_learning_has_no_promotion_path_in_its_source():
    """Asserted over the source, because this is the claim that matters.

    A loop that could swap out a MANAGEMENT-DEFINED frozen policy would
    be an agent rewriting management policy. There must be no UPDATE
    against the champion and no write to the experiment's policy.
    """
    import pathlib

    src = (pathlib.Path(__file__).resolve().parents[1] / "sportsassets"
           / "workers" / "rn1x_learn_loop.py").read_text()
    lowered = src.lower()
    for forbidden in ("update rn1x_experiments", "update bettor_learn_model",
                      "policy_register =", "pair_target_cost =",
                      "loss_trigger_fraction ="):
        assert forbidden not in lowered, forbidden
    from sportsassets import bettor_rn1x_learn as L
    # exactly two verdicts, and neither is a promotion
    assert {L.RETAIN, L.ELIGIBLE} == {"RETAIN_CHAMPION",
                                      "CHALLENGER_ELIGIBLE_PENDING_MANAGEMENT"}
