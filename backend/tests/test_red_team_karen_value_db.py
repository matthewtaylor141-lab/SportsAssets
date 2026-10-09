"""KAREN_VALUE reads what the twin writes (RC6 red-team lane).

The twin's Karen counterfactual (twin.scorecards.karen over the
KAREN_BLOCK_ACCEPTED world) is persisted in book COUNTERFACTUAL -- a metric
derived from a twin world names its basis book in the metric
(`loss_avoided_paper_basis`), never in `book`. The red-team reader asked for
book = 'PAPER', a row the twin never writes, so KAREN_VALUE read
KAREN_COUNTERFACTUALS_UNMEASURED whatever the twin had measured (production
RC5: 43 twin runs, every Karen row of these metrics COUNTERFACTUAL).

These tests write the rows WITH THE TWIN'S OWN PRODUCER AND STORE
(twin.scorecards.karen -> twin.store.save_scorecards) and read them back
through redteam.readiness.karen_counterfactuals and controls.karen:

  measured (>= the twin's minimum sample)  -> GREEN with the values
  the twin's UNAVAILABLE / INSUFFICIENT_SAMPLE -> UNKNOWN, the twin's own
                                              status and reason named;
                                              nothing concluded
  two runs                                  -> both metrics from the newest
                                              run, never mixed
  unmeasured values                         -> null, never "0"

Every write is rolled back."""
from __future__ import annotations

import asyncio
import os
import time

import pytest

from sportsassets.redteam import controls as C

DSN = os.environ.get("RN1X_TEST_DSN")
needs_db = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

KC = {"upheld": 3, "rejected": 1, "false_blocks": 0, "blocks_assessed": 0}


class _Rollback(Exception):
    pass


def _karen_rows(pairs):
    """The twin's own Karen scorecard rows for blocked positions whose
    (world pnl, recorded pnl) are `pairs` (PAPER basis)."""
    from sportsassets.twin import scorecards as TS
    traces = {("KAREN_BLOCK_ACCEPTED", "PAPER"): [
        {"world_action": "BLOCK_ACCEPTED_EXIT", "pnl_usd": a,
         "baseline_pnl_usd": b} for a, b in pairs]}
    return TS.karen(KC, {}, traces)


def _read_after_writing(runs):
    """Write each (run_id, now, rows) with twin.store, read KAREN_VALUE
    through the red-team reader, roll everything back."""
    import asyncpg
    from sportsassets.redteam import readiness as R
    from sportsassets.twin import store as TST

    async def go():
        conn = await asyncpg.connect(DSN)
        out = {}
        try:
            try:
                async with conn.transaction():
                    for run_id, now, rows in runs:
                        await TST.save_scorecards(conn, run_id=run_id,
                                                  now=now, rows=rows)
                    got = await R.karen_counterfactuals(conn)
                    out["got"] = got
                    raise _Rollback()
            except _Rollback:
                pass
        finally:
            await conn.close()
        return out["got"]
    got = asyncio.run(go())
    assert len(got) == 4, "the reader returns the twin rows beside the values"
    saved, cost, src, rows = got
    return C.karen(saved, cost, source=src, twin_rows=rows), got


def _measured_pairs(n=40):
    # half the blocks avoided a loss, half sacrificed a profit
    return [(0.0, -2.0) if i % 2 else (0.0, 1.0) for i in range(n)]


@needs_db
def test_a_measured_twin_counterfactual_is_read_and_green():
    rows = _karen_rows(_measured_pairs(40))
    by = {r["metric"]: r for r in rows}
    # the producer's own book: COUNTERFACTUAL, never PAPER
    assert by["loss_avoided_paper_basis"]["book"] == "COUNTERFACTUAL"
    assert by["loss_avoided_paper_basis"]["status"] == "MEASURED"
    c, (saved, cost, _src, _rows) = _read_after_writing(
        [("twinrun:rt-measured", time.time(), rows)])
    assert c["status"] == C.GREEN, c["blockers"]
    assert saved == pytest.approx(40.0) and cost == pytest.approx(20.0)
    ev = c["evidence"]
    assert ev["value_added_usd"] == "20.0"
    assert ev["twin"]["book"] == "COUNTERFACTUAL"
    assert ev["twin"]["rows"]["loss_avoided_paper_basis"]["run_id"] == \
        "twinrun:rt-measured"


@needs_db
def test_the_twins_unavailable_row_is_named_never_green_never_zero():
    rows = _karen_rows([])            # production: no blocked position
    c, _ = _read_after_writing([("twinrun:rt-empty", time.time(), rows)])
    assert c["status"] == C.UNKNOWN
    assert "KAREN_COUNTERFACTUALS_UNMEASURED" in c["blockers"]
    assert ("KAREN_TWIN_UNAVAILABLE:loss_avoided_paper_basis:"
            "NO_BLOCKED_POSITION_SCORED_IN_BOTH_WORLDS") in c["blockers"]
    ev = c["evidence"]
    assert ev["saved_loss_usd"] is None and ev["value_added_usd"] is None


@needs_db
def test_an_insufficient_sample_concludes_nothing():
    rows = _karen_rows(_measured_pairs(6))
    assert {r["status"] for r in rows if r["metric"]
            in C.KAREN_TWIN_METRICS} == {"INSUFFICIENT_SAMPLE"}
    c, _ = _read_after_writing([("twinrun:rt-small", time.time(), rows)])
    assert c["status"] == C.UNKNOWN
    assert "KAREN_TWIN_INSUFFICIENT_SAMPLE:loss_avoided_paper_basis:6" in \
        c["blockers"]
    assert c["evidence"]["value_added_usd"] is None


@needs_db
def test_both_metrics_come_from_the_newest_run_never_mixed():
    now = time.time()
    old = _karen_rows(_measured_pairs(40))
    new = _karen_rows([])
    c, (saved, cost, _s, rows) = _read_after_writing([
        ("twinrun:rt-old", now - 3600, old),
        ("twinrun:rt-new", now, new)])
    assert {r["run_id"] for r in rows.values()} == {"twinrun:rt-new"}
    assert saved is None and cost is None
    assert c["status"] == C.UNKNOWN


def test_the_control_names_every_twin_verdict_purely():
    ok = {m: {"status": "MEASURED", "value": 1.0, "sample_n": 40}
          for m in C.KAREN_TWIN_METRICS}
    assert C.karen(5, 2, source="t", twin_rows=ok)["status"] == C.GREEN
    assert C.karen(5, 2, source="t", twin_rows=ok)["evidence"][
        "value_added_usd"] == "3"
    small = dict(ok, profit_sacrificed_paper_basis={
        "status": "INSUFFICIENT_SAMPLE", "value": 1.0, "sample_n": 7})
    r = C.karen(5, 2, source="t", twin_rows=small)
    assert r["status"] == C.UNKNOWN and \
        "KAREN_TWIN_INSUFFICIENT_SAMPLE:profit_sacrificed_paper_basis:7" in \
        r["blockers"]
    r = C.karen(None, None, source="t", twin_rows={})
    assert r["status"] == C.UNKNOWN
    assert "KAREN_TWIN_ROW_ABSENT:loss_avoided_paper_basis" in r["blockers"]
    assert r["evidence"]["saved_loss_usd"] is None
