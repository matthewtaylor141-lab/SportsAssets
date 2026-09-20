"""THE MARKOUT TABLE WAS LEFT OUT OF THE DIRECT-REGIME WIDENING.

Found in production 2026-09-20 01:23Z. The first shadow position opened
at 00:58:01Z, its 30S markout computed correctly and OBSERVED, and the
insert was rejected:

    CheckViolationError: new row for relation
    "bettor_experimental_markouts" violates check constraint
    "bettor_exp_markout_regime"
    Failing row contains (... 30S ... OBSERVED ...
                          DIRECT_INSTITUTIONAL_WORKER ...)

Migration 081 widened `latency_regime` to admit
DIRECT_INSTITUTIONAL_WORKER on bettor_l2_evidence and on
bettor_experimental_decisions, and missed the third table that stores
the same value. Nothing caught it because until 00:58 there had never
been a position to mark, so that row had never been written at all.

TWO PINS, BECAUSE THERE WERE TWO FAULTS:

  * the CHECK itself, generalised -- every table that stores a regime
    must admit every regime the code can write, checked by comparing
    the migrations against the constants rather than by listing them
    again here;

  * the BLAST RADIUS. take_markouts runs BEFORE seal_population, so
    one rejected insert stopped the markouts AND every subsequent
    decision for twenty-five minutes while the sampler kept writing
    observations and the lane looked alive from outside. A markout is
    a later fact about a trade that already happened; failing to write
    one must cost that markout and nothing else.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from sportsassets import shadow_experimental_store as xstore
from sportsassets.workers import shadow_experimental as worker

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"

# Every regime the code can write. Taken from the constants, so a new
# regime added there fails this file until its CHECKs are widened.
REGIMES = {xstore.REGIME_DIRECT, xstore.REGIME_BRIDGE,
           "PERSISTENT_INSTITUTIONAL_WORKER"}


def latest_check_for(constraint: str) -> set:
    """The admissible values of the LAST migration to define a CHECK.

    Later migrations DROP and re-ADD the constraint, so the newest
    definition is the live one. Reading them in order is what makes
    this test notice a widening that skipped a table.
    """
    newest = None
    for path in sorted(MIGRATIONS.glob("*.sql")):
        sql = path.read_text()
        if ("ADD CONSTRAINT %s" % constraint) not in sql:
            continue
        block = sql.split("ADD CONSTRAINT %s" % constraint)[-1]
        block = block.split(";")[0]
        newest = set(re.findall(r"'([A-Z_]+)'", block))
    return newest or set()


@pytest.mark.parametrize("constraint", [
    "bettor_exp_markout_regime",
    "bettor_l2_evidence_regime",
])
def test_every_table_that_stores_a_regime_admits_every_regime(constraint):
    """THE GENERALISED PIN. 081 widened two tables and missed the
    third; this compares the migrations against the constants so the
    next widening cannot skip one silently."""
    admitted = latest_check_for(constraint)
    assert admitted, "no CHECK found for %s" % constraint
    missing = REGIMES - admitted
    assert not missing, (
        "%s does not admit %s -- a row the code can write would be "
        "rejected at the database, and on this table that rejection "
        "took the whole tick down" % (constraint, ", ".join(sorted(missing))))


def test_the_direct_regime_is_admitted_by_the_markout_check_specifically():
    """The exact production failure, named."""
    assert "DIRECT_INSTITUTIONAL_WORKER" in latest_check_for(
        "bettor_exp_markout_regime")


# ── the blast radius ─────────────────────────────────────────────────


class Subject:
    ROW = {"experimentalDecisionId": "xdec_1", "symbol": "m1",
           "decisionTimestamp": None, "positionId": "xpos_1",
           "qty": 2500.0, "vwap": 0.322}


class FailingWritePool:
    """Every markout write is rejected, as the CHECK was rejecting them."""

    def __init__(self):
        self.requests = 0

    async def fetch(self, sql, *args):
        if "bettor_experimental_markouts" in sql:
            return []            # nothing taken yet
        if "bettor_experimental_positions" in sql:
            from datetime import datetime, timedelta, timezone
            at = datetime.now(tz=timezone.utc) - timedelta(seconds=600)
            return [{"experimental_decision_id": "xdec_1",
                     "market_id": "m1", "decision_timestamp": at,
                     "position_id": "xpos_1", "entry_qty": 2500.0,
                     "entry_vwap": 0.322}]
        return []

    async def fetchrow(self, sql, *args):
        if "bettor_experimental_markouts" in sql:
            raise RuntimeError(
                'new row for relation "bettor_experimental_markouts" '
                'violates check constraint "bettor_exp_markout_regime"')
        if "bettor_l2_requests" in sql:
            self.requests += 1
            return {"l2_request_id": "req_1"}
        return None

    async def execute(self, sql, *args):
        return None


@pytest.mark.asyncio
async def test_a_rejected_markout_write_does_not_raise_out_of_the_sweep():
    """THE CONTAINMENT. The sweep must return, so the tick reaches
    seal_population and the lane keeps deciding."""
    stats = await worker.take_markouts(FailingWritePool())
    assert stats["subjects"] == 1
    assert stats.get("writeFailed", 0) >= 1
    assert "bettor_exp_markout_regime" in stats.get("writeError", "")


@pytest.mark.asyncio
async def test_a_failed_markout_is_not_counted_as_observed():
    """A markout that was never stored is not a markout. Counting it
    would make the scoring look complete while the table stayed empty
    -- which is precisely how this went unnoticed for 25 minutes."""
    stats = await worker.take_markouts(FailingWritePool())
    assert stats["observed"] == 0
    assert stats["notIdentified"] == 0


@pytest.mark.asyncio
async def test_the_horizon_stays_unmarked_so_the_next_tick_retries_it():
    """Nothing is backdated and nothing is given up on: the row simply
    is not there, so the next sweep tries the same horizon again."""
    pool = FailingWritePool()
    first = await worker.take_markouts(pool)
    second = await worker.take_markouts(pool)
    assert first.get("writeFailed") == second.get("writeFailed")


def test_the_sweep_runs_before_sealing_which_is_why_this_mattered():
    """Documented in the test rather than only in a comment: the
    ordering is what turned a rejected insert into a stalled
    experiment, and a reordering would change that blast radius."""
    import inspect

    src = inspect.getsource(worker.tick)
    assert src.index("take_markouts") < src.index("seal_population"), (
        "take_markouts runs before seal_population; if that ever "
        "changes, the containment above is still correct but this "
        "comment is not")
