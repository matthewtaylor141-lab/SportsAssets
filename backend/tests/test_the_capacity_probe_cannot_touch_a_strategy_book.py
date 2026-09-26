"""THE CAPACITY HARNESS IS FENCED OFF FROM THE STRATEGY, AT THE WRITE.

THE DEFECT THIS PINS, AND IT WAS MINE. The probe's writer phases stamped
`bettor_external_shadow.EXPERIMENT_ID` -- the AUTONOMOUS STRATEGY's own
experiment id -- on every synthetic plan. Across the four disposable
databases it had run against that left 2,080 fabricated positions sitting
in the book that means "what the engine decided on current markets". The
enumeration, the affected ids and the correction are in
`research/evidence/CAPACITY_PROBE_AFFECTED_IDS.json`.

WHAT IS ASSERTED HERE, and why each one is a write-time fact rather than a
display rule:

  1 A STRATEGY EXPERIMENT IS REFUSED BY NAME, before any pool is opened.
  2 AN UNCONFIRMED DATABASE IS REFUSED. A capacity run writes thousands of
    synthetic positions and must never inherit its target.
  3 A DATABASE HOLDING A STRATEGY BOOK IS REFUSED, whatever its name.
  4 EACH REFUSAL WRITES NOTHING -- asserted by counting rows after it.
  5 A VALID RUN LEAVES THE CONSUMERS UNCHANGED: the entry lane's evidence
    read, its inventory, the per-lane P&L, the exposure the risk rail
    measures and the calibration sample. Checking the desk's book labels
    would prove only that a label is applied.
"""

from __future__ import annotations

import os

import pytest

from sportsassets import bettor_demonstration as DEMO
from sportsassets import bettor_external_shadow as ext
from tools import bettor_capacity_probe as P

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="RN1X_TEST_DSN is not set")


# ── pure: the refusal does not need a database ──────────────────────

def test_a_strategy_experiment_is_refused_by_name():
    for e in (ext.EXPERIMENT_ID, "EXT_PINNACLE_DEVIG_V1_SHADOW",
              "RN1X_SHADOW_CHALLENGER_HOLD_RANKED_V1",
              "SOMETHING_ELSE_ENTIRELY", ""):
        bad = P.experiment_guard(e)
        assert bad.get("refusal") == P.R_STRATEGY_EXPERIMENT, (e, bad)
    for ok in P.OWN_EXPERIMENTS:
        assert P.experiment_guard(ok) == {}, ok


def test_the_harness_books_are_not_the_strategys():
    assert ext.EXPERIMENT_ID not in P.OWN_EXPERIMENTS
    assert P.WRITER_EXPERIMENT in P.OWN_EXPERIMENTS
    assert P.LIFECYCLE_EXPERIMENT in P.OWN_EXPERIMENTS
    assert ext.EXPERIMENT_ID in P.STRATEGY_EXPERIMENTS


def test_the_record_builders_never_emit_a_strategy_experiment():
    """The default is the harness's own book even if `RUN` was never set."""
    P.RUN.update(id="CAPRUN-TEST", experiment=None)
    rec = P._record(1)
    assert rec["experiment_id"] == P.WRITER_EXPERIMENT
    assert rec["experiment_id"] != ext.EXPERIMENT_ID


def test_a_run_stamps_its_own_identifier_on_what_it_writes():
    P.RUN.update(id="CAPRUN-12345", experiment=P.WRITER_EXPERIMENT)
    assert "CAPRUN-12345" in P._record(7)["payout_event"]
    assert "CAPRUN-12345" in P._lc_identity(7)[2]
    assert P.run_id(1_790_000_000.0) == "CAPRUN-1790000000"


# ── against a real database ─────────────────────────────────────────

async def _seed_a_genuine_strategy_position(conn):
    """One REAL strategy-book position, written by the deployed writer.

    Not hand-inserted: the fixture has to be the kind of row the guard is
    protecting, so it goes through `plan_entry`/`persist_entry` under the
    strategy's own experiment id.
    """
    return await DEMO.run_entry(
        conn, experiment=ext.EXPERIMENT_ID, cid="0x" + ("ab" * 32),
        slug="aec-genuine-strategy-2026-09-26",
        pays_on="A Genuine Strategy Side")


async def _consumer_snapshot(conn):
    """WHAT THE CONSUMERS SEE. Not what the desk labels."""
    from sportsassets.api import command_rn1x as RN

    ev = await RN.entry_evidence(conn, hours=720, limit=200)
    pnl = await RN._pnl_status(conn)
    return {
        "evidence_candidates": len(ev.get("candidates") or []),
        "evidence_inventory": [r["position_id"]
                               for r in (ev.get("inventory") or [])],
        "calibration_measured": (ev.get("source_calibration") or {}).get(
            "measured"),
        "pnl_lanes": (pnl or {}).get("lanes"),
        "strategy_positions": await conn.fetchval(
            "SELECT count(*) FROM rn1x_positions WHERE experiment_id = $1",
            ext.EXPERIMENT_ID),
        "strategy_exposure_usd": await conn.fetchval(
            "SELECT coalesce(sum(p.seed_basis_usd), 0)::float8 "
            "  FROM rn1x_positions p WHERE p.experiment_id = $1",
            ext.EXPERIMENT_ID),
        "valuations": await conn.fetchval(
            "SELECT count(*) FROM external_valuations"),
        "calibration_rows": await conn.fetchval(
            "SELECT count(*) FROM external_source_calibration"),
        # EVERY POSITION THAT IS NOT THE HARNESS'S. Asserting this database
        # holds nothing else would be wrong -- other suites share it -- so
        # what is pinned is that the count does not MOVE across a run.
        "non_harness_positions": await conn.fetchval(
            "SELECT count(*) FROM rn1x_positions "
            " WHERE experiment_id <> ALL($1::text[])",
            list(P.OWN_EXPERIMENTS)),
    }


@pg
async def test_every_refusal_writes_nothing_and_a_valid_run_changes_no_consumer():
    asyncpg = pytest.importorskip("asyncpg")

    conn = await asyncpg.connect(DSN)
    pool = await asyncpg.create_pool(DSN, min_size=1, max_size=2)
    try:
        await _seed_a_genuine_strategy_position(conn)
        before = await _consumer_snapshot(conn)
        harness_before = await conn.fetchval(
            "SELECT count(*) FROM rn1x_positions WHERE experiment_id = "
            "ANY($1::text[])", list(P.OWN_EXPERIMENTS))
        assert int(before["strategy_positions"]) >= 1, (
            "the fixture must hold a genuine strategy position, or the "
            "guard below would have nothing to protect")

        # ── 1 · THE UNCONFIRMED DATABASE ────────────────────────────
        got = await P.database_guard(pool, confirmed="")
        assert got["refusal"] == P.R_NOT_CONFIRMED, got

        # ── 2 · THE DATABASE THAT HOLDS A STRATEGY BOOK ─────────────
        got = await P.database_guard(pool, confirmed="DO")
        assert got["refusal"] == P.R_HOLDS_A_STRATEGY_BOOK, got
        assert ext.EXPERIMENT_ID in got["strategy_books"], got

        # ── 3 · THE WHOLE PROBE, ASKED TO USE THE STRATEGY BOOK ─────
        out = await P.run(DSN, entries=5, burst=2, concurrency=2,
                          lifecycles=2, restart=0, confirm_disposable="DO",
                          experiment=ext.EXPERIMENT_ID, stamp=1.0)
        assert out["wrote_nothing"] is True, out
        assert out["refused"]["refusal"] == P.R_STRATEGY_EXPERIMENT
        assert out["isolation"] == "REFUSED_BEFORE_THE_FIRST_WRITE"

        # ── 4 · AND ASKED TO USE THIS DATABASE AT ALL ───────────────
        out2 = await P.run(DSN, entries=5, burst=2, concurrency=2,
                           lifecycles=2, restart=0,
                           confirm_disposable="DO", stamp=2.0)
        assert out2["wrote_nothing"] is True, out2
        assert out2["refused"]["refusal"] == P.R_HOLDS_A_STRATEGY_BOOK

        # ── 5 · NOTHING MOVED, AT THE CONSUMERS ────────────────────
        after = await _consumer_snapshot(conn)
        assert after == before, {"before": before, "after": after}
        # AND THE REFUSALS WROTE NOTHING AT ALL: the harness's own books are
        # counted too, and they did not grow. (An absolute zero would be the
        # wrong assertion -- other suites share this database -- so what is
        # pinned is that nothing was added.)
        assert await conn.fetchval(
            "SELECT count(*) FROM rn1x_positions WHERE experiment_id = "
            "ANY($1::text[])", list(P.OWN_EXPERIMENTS)) == harness_before
    finally:
        await pool.close()
        await conn.close()


@pg
async def test_a_valid_capacity_run_leaves_the_strategy_consumers_untouched():
    """THE POSITIVE CASE. On a database with no strategy book the run
    proceeds -- and every strategy consumer still reads exactly what it read
    before, because the harness's rows are in its own experiments."""
    asyncpg = pytest.importorskip("asyncpg")

    conn = await asyncpg.connect(DSN)
    try:
        # THE STRATEGY BOOK IS EMPTIED FIRST, deliberately: the guard
        # refuses a database that holds one, so a valid run can only be
        # measured where there is none. What is asserted is that the
        # consumers read the same thing after the run as before it.
        await conn.execute(
            "DELETE FROM rn1x_fills WHERE order_id IN (SELECT order_id "
            "FROM rn1x_orders o JOIN rn1x_positions p ON p.position_id = "
            "o.position_id WHERE p.experiment_id = $1)", ext.EXPERIMENT_ID)
        for t in ("rn1x_orders", "rn1x_decisions", "rn1x_outcomes"):
            await conn.execute(
                "DELETE FROM " + t + " WHERE position_id IN (SELECT "
                "position_id FROM rn1x_positions WHERE experiment_id = $1)",
                ext.EXPERIMENT_ID)
        await conn.execute(
            "DELETE FROM rn1x_positions WHERE experiment_id = $1",
            ext.EXPERIMENT_ID)
        before = await _consumer_snapshot(conn)

        out = await P.run(DSN, entries=6, burst=3, concurrency=3,
                          lifecycles=2, restart=0, confirm_disposable="DO",
                          stamp=1_790_000_000.0)
        assert out.get("refused") is None, out.get("refused")
        assert out["isolation"] == \
            "CONFIRMED_DISPOSABLE_AND_NO_STRATEGY_BOOK"
        assert out["run_id"] == "CAPRUN-1790000000"
        assert out["experiment"] == P.WRITER_EXPERIMENT
        # IT DID WRITE -- otherwise this proves nothing about a valid run.
        assert int(out["phases"]["sustained"]["counts_after"][
            "positions"]) >= 6, out["phases"]["sustained"]

        after = await _consumer_snapshot(conn)
        assert after["strategy_positions"] == before["strategy_positions"] \
            == 0
        assert after["strategy_exposure_usd"] == \
            before["strategy_exposure_usd"]
        assert after["evidence_inventory"] == before["evidence_inventory"]
        assert after["evidence_candidates"] == before["evidence_candidates"]
        assert after["valuations"] == before["valuations"]
        assert after["calibration_rows"] == before["calibration_rows"]
        assert after["pnl_lanes"] == before["pnl_lanes"]
        # AND EVERY ROW IT WROTE IS IN ITS OWN BOOKS: the number of
        # positions outside them is exactly what it was before the run.
        assert after["non_harness_positions"] == \
            before["non_harness_positions"]
    finally:
        await conn.close()
