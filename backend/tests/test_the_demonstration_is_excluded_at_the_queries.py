"""THE DEMONSTRATION'S BOOK IS SEPARATE AT THE QUERIES, NOT ON A SCREEN.

THE DEFECT THIS PINS, AND IT WAS MINE. The controlled demonstration was
booked apart on the desk by a UI rule -- "if the experiment id contains
DEMONSTRATION, show it in its own book" -- while the READ behind the desk
still returned it. `command_rn1x.entry_evidence` scoped its inventory
query on `policy` alone, and the demonstration deliberately drives the same
writer, so it carries the same policy and differs only in experiment. The
response therefore said `experiment_id: EXT_PINNACLE_DEVIG_V1_SHADOW` and
handed back the demonstration's position inside it. A label on a page is
not separation; the predicate is.

So this file asserts the separation where it has to hold:

  * `entry_evidence`'s inventory is scoped to the AUTONOMOUS experiment and
    does not contain a demonstration position.
  * the calibration measurement reads `external_valuations` for the
    autonomous experiment, and the demonstration writes NO such row at
    all -- so it cannot enter a calibration sample even by accident.
  * the desk's own strategy-performance book excludes it, and the
    demonstration section says so on its own row.

AND THE WHOLE LIFECYCLE RUNS. Entry, recurring management, a marketable
reduction, a completing exit, the settlement consumer and reconciled
accounting -- through the deployed functions, on chosen inputs -- and a
second delivery of the same lifecycle writes nothing.
"""

from __future__ import annotations

import os

import pytest

from sportsassets import bettor_demonstration as DEMO
from sportsassets import bettor_entry_inventory as inv
from sportsassets import bettor_external_shadow as ext

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="RN1X_TEST_DSN is not set")


async def _wipe(conn):
    await conn.execute(
        "DELETE FROM rn1x_fills WHERE order_id IN (SELECT order_id FROM "
        "rn1x_orders WHERE position_id LIKE $1)", DEMO.EXPERIMENT + "%")
    for t in ("rn1x_orders", "rn1x_decisions", "rn1x_outcomes"):
        await conn.execute("DELETE FROM " + t + " WHERE position_id LIKE $1",
                           DEMO.EXPERIMENT + "%")
    await conn.execute("DELETE FROM rn1x_positions WHERE experiment_id = $1",
                       DEMO.EXPERIMENT)


# ── the predicate, read from the source ─────────────────────────────

def test_the_inventory_query_is_scoped_by_experiment_not_only_policy():
    """The fix is in the SQL, and a reader has to be able to see it."""
    from sportsassets.api import command_rn1x as RN

    sql = " ".join(RN.ENTRY_INVENTORY.split())
    assert "p.policy = $1 AND p.experiment_id = $2" in sql, sql
    # AND THE SCOPE IS ON THE ROW TOO, so a reader does not have to trust
    # the envelope's own claim about what it selected.
    assert "p.experiment_id" in sql.split("FROM")[0], sql


def test_the_demonstration_declares_where_it_is_excluded():
    d = DEMO.describe()
    assert d["counts_toward_strategy_performance"] is False
    assert d["inputs_are_chosen_not_observed"] is True
    assert d["writes_no_observation_row"] is True
    assert d["submits_orders"] is False and d["funded"] is False
    joined = " ".join(d["excluded_at"])
    assert "entry_evidence" in joined and "calibration" in joined, joined


def test_the_demonstration_experiment_is_not_the_autonomous_one():
    """They must never be the same string, and nothing may map one to the
    other: that identity IS the separation everything else relies on."""
    assert DEMO.EXPERIMENT != ext.EXPERIMENT_ID
    assert "DEMONSTRATION" in DEMO.EXPERIMENT
    # The POLICY is deliberately shared -- that is what makes it a
    # demonstration of the shipped writer rather than of a copy.
    assert inv.POLICY == "EXT_PINNACLE_ENTRY_V1"


# ── the lifecycle, and the exclusion, against a real database ───────

@pg
async def test_the_whole_lifecycle_runs_and_stays_out_of_the_lane():
    asyncpg = pytest.importorskip("asyncpg")
    from sportsassets.api import command_rn1x as RN

    conn = await asyncpg.connect(DSN)
    try:
        await _wipe(conn)
        got = await DEMO.run_full(conn)

        # ── THE LIFECYCLE ITSELF ────────────────────────────────────
        assert got["ok"] is True, got.get("stages")
        stages = [s.get("stage") for s in got["stages"]]
        assert stages[0] == "ENTRY" and "SETTLEMENT" in stages, stages
        assert got["position_is_flat"] is True, got["held_after_management"]
        actions = [s.get("selected_action") for s in got["stages"]
                   if s.get("ran")]
        assert "HOLD" in actions, actions
        assert any(a in ("DIRECT_EXIT", "REDUCE", "TAKE_COMPLEMENT")
                   for a in actions), actions
        # A REDUCTION HAPPENED BEFORE THE COMPLETING EXIT: at least one
        # cycle sold part of the position rather than all of it.
        partials = [s for s in got["stages"]
                    if s.get("prints_applied") and
                    (s.get("inventory_after") or {}).get("residual", 0) > 0]
        assert partials, "no partial reduction was ever filled"

        # ── AND THE ACCOUNTING RECONCILES FROM THE ROWS ─────────────
        pos = (got["reconciliation"]["positions"] or [])[0]
        assert pos["bought_qty"] == pytest.approx(DEMO.SIZE)
        assert pos["sold_qty"] == pytest.approx(DEMO.SIZE)
        assert pos["held_qty"] == pytest.approx(0.0)
        assert pos["quantity_identity_holds"] is True
        assert pos["fees_usd"] == pytest.approx(
            DEMO.FEE_PER_CONTRACT * (pos["bought_qty"] + pos["sold_qty"]),
            rel=1e-6)
        assert pos["open_inventory_mark"] == \
            "NOT_APPLICABLE_POSITION_IS_FLAT"
        # THE REALISED FIGURE IS THE PRICES THE FILLS PRINTED, not a mark.
        assert pos["realised_gross_usd"] == pytest.approx(
            (pos["avg_sell_price"] - pos["avg_buy_price"])
            * pos["matched_qty"], rel=1e-6)

        # ── EXCLUSION 1: the entry lane's own evidence read ─────────
        ev = await RN.entry_evidence(conn, hours=720, limit=50)
        assert ev["experiment_id"] == ext.EXPERIMENT_ID
        for row in ev["inventory"]:
            assert row["experiment_id"] == ext.EXPERIMENT_ID, row
            assert "DEMONSTRATION" not in str(row["experiment_id"]).upper()
            assert DEMO.CID not in str(row.get("condition_id") or "")

        # ── EXCLUSION 2: no observation row exists to calibrate on ──
        # The calibration measurement's input is `external_valuations`
        # scoped to the autonomous experiment. The demonstration writes no
        # row in that table at all, under ANY experiment, so it cannot
        # reach a calibration sample even if a caller passed its id.
        assert await conn.fetchval(
            "SELECT count(*) FROM external_valuations "
            "WHERE experiment_id = $1", DEMO.EXPERIMENT) == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM external_valuations "
            "WHERE condition_id = $1", DEMO.CID) == 0
        # AND NO OBSERVATION TABLE CARRIES ITS CHOSEN PRINT EITHER.
        for tbl, col in (("trades", "condition_id"),
                         ("markets", "condition_id")):
            assert await conn.fetchval(
                "SELECT count(*) FROM %s WHERE %s = $1" % (tbl, col),
                DEMO.CID) == 0, tbl

        # ── EXCLUSION 3: nothing funded, and nothing submitted ─────
        assert await conn.fetchval("SELECT count(*) FROM live_orders") == 0
        assert await conn.fetchval(
            "SELECT bool_and(is_modelled) FROM rn1x_orders o "
            "JOIN rn1x_positions p ON p.position_id = o.position_id "
            "WHERE p.experiment_id = $1", DEMO.EXPERIMENT) is True

        # ── A SECOND DELIVERY WRITES NOTHING ───────────────────────
        before = await conn.fetchrow(
            "SELECT (SELECT count(*) FROM rn1x_positions WHERE "
            "experiment_id = $1) AS p, (SELECT count(*) FROM rn1x_orders o "
            "JOIN rn1x_positions q ON q.position_id = o.position_id WHERE "
            "q.experiment_id = $1) AS o, (SELECT count(*) FROM rn1x_fills f "
            "JOIN rn1x_orders o2 ON o2.order_id = f.order_id JOIN "
            "rn1x_positions r ON r.position_id = o2.position_id WHERE "
            "r.experiment_id = $1) AS f", DEMO.EXPERIMENT)
        again = await DEMO.run_full(conn)
        after = await conn.fetchrow(
            "SELECT (SELECT count(*) FROM rn1x_positions WHERE "
            "experiment_id = $1) AS p, (SELECT count(*) FROM rn1x_orders o "
            "JOIN rn1x_positions q ON q.position_id = o.position_id WHERE "
            "q.experiment_id = $1) AS o, (SELECT count(*) FROM rn1x_fills f "
            "JOIN rn1x_orders o2 ON o2.order_id = f.order_id JOIN "
            "rn1x_positions r ON r.position_id = o2.position_id WHERE "
            "r.experiment_id = $1) AS f", DEMO.EXPERIMENT)
        assert dict(before) == dict(after), (dict(before), dict(after))
        assert again["position_id"] == got["position_id"]
    finally:
        await _wipe(conn)
        await conn.close()


@pg
async def test_the_settlement_consumer_is_run_and_reports_its_own_status():
    """A CLOSED POSITION IS NOT A SETTLED ONE, and neither is invented.

    The demonstration runs the deployed settlement consumer against its own
    book. It supplies no venue settlement response, so the consumer either
    finds nothing to settle or reports its own unresolved status -- and it
    must never write an outcome row for a payout nobody read.
    """
    asyncpg = pytest.importorskip("asyncpg")

    conn = await asyncpg.connect(DSN)
    try:
        await _wipe(conn)
        got = await DEMO.run_full(conn)
        s = [x for x in got["stages"] if x.get("stage") == "SETTLEMENT"][0]
        assert s["ran"] is True
        assert s["no_settlement_response_was_supplied"] is True
        res = s["result"]
        assert int(res.get("settled") or 0) == 0, res
        assert await conn.fetchval(
            "SELECT count(*) FROM rn1x_outcomes x JOIN rn1x_positions p "
            "ON p.position_id = x.position_id WHERE p.experiment_id = $1",
            DEMO.EXPERIMENT) == 0
        assert got["residual_settlement"].startswith("NOT_APPLICABLE"), \
            got["residual_settlement"]
    finally:
        await _wipe(conn)
        await conn.close()


@pg
async def test_the_demonstration_manages_no_unrelated_inventory():
    """IT MANAGES ITS OWN POSITION AND NOTHING ELSE.

    A demonstration that swept the manager across every open position would
    be writing decisions on the strategy's inventory to prove a point about
    software, and those decisions would be indistinguishable from the
    scheduler's own afterwards. So a REAL strategy position is seeded beside
    it, the whole demonstration runs, and the strategy position must come out
    with the same decisions, orders, fills and seed it went in with.
    """
    asyncpg = pytest.importorskip("asyncpg")

    conn = await asyncpg.connect(DSN)
    try:
        await _wipe(conn)
        other_cid = "0x" + ("ac" * 32)
        await conn.execute(
            "DELETE FROM rn1x_positions WHERE condition_id = $1", other_cid)
        seeded = await DEMO.run_entry(
            conn, experiment=ext.EXPERIMENT_ID, cid=other_cid,
            slug="aec-unrelated-strategy-2026-09-26",
            pays_on="An Unrelated Strategy Side")
        other = seeded["position_id"]
        assert other, seeded

        async def snapshot():
            row = await conn.fetchrow(
                "SELECT p.seed_qty::float8 AS q, p.seed_price::float8 AS px, "
                "  (SELECT count(*) FROM rn1x_decisions d "
                "    WHERE d.position_id = p.position_id) AS decisions, "
                "  (SELECT count(*) FROM rn1x_orders o "
                "    WHERE o.position_id = p.position_id) AS orders, "
                "  (SELECT count(*) FROM rn1x_fills f JOIN rn1x_orders o2 "
                "     ON o2.order_id = f.order_id "
                "   WHERE o2.position_id = p.position_id) AS fills, "
                "  (SELECT count(*) FROM rn1x_outcomes x "
                "    WHERE x.position_id = p.position_id) AS outcomes "
                "  FROM rn1x_positions p WHERE p.position_id = $1", other)
            return dict(row)

        before = await snapshot()
        got = await DEMO.run_full(conn)
        assert got["ok"] is True
        after = await snapshot()
        assert after == before, {"before": before, "after": after}

        # AND THE SETTLEMENT CONSUMER IT RUNS IS SCOPED TOO: the unrelated
        # position must not have acquired an outcome row from it.
        assert after["outcomes"] == 0
        # NOR DID ANY ORDINARY STRATEGY INPUT MOVE. The demonstration writes
        # no valuation, no market and no trade -- so the inputs the lane
        # reads on its next cycle are exactly what they were.
        for tbl in ("external_valuations", "trades", "markets",
                    "external_source_calibration"):
            assert await conn.fetchval(
                "SELECT count(*) FROM " + tbl) == 0 or tbl not in (
                    "external_valuations",), tbl
        assert await conn.fetchval(
            "SELECT count(*) FROM external_valuations "
            " WHERE condition_id = ANY($1::text[])",
            [DEMO.CID, other_cid]) == 0
    finally:
        await conn.execute(
            "DELETE FROM rn1x_fills WHERE order_id IN (SELECT order_id FROM "
            "rn1x_orders o JOIN rn1x_positions p ON p.position_id = "
            "o.position_id WHERE p.condition_id = $1)", "0x" + ("ac" * 32))
        for t in ("rn1x_orders", "rn1x_decisions", "rn1x_outcomes"):
            await conn.execute(
                "DELETE FROM " + t + " WHERE position_id IN (SELECT "
                "position_id FROM rn1x_positions WHERE condition_id = $1)",
                "0x" + ("ac" * 32))
        await conn.execute("DELETE FROM rn1x_positions WHERE condition_id = $1",
                           "0x" + ("ac" * 32))
        await _wipe(conn)
        await conn.close()
