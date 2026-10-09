"""A WORKER STOPPED BETWEEN AN OPPORTUNITY AND ITS DECISION LEAVES NO ORPHAN.

THE RESIDUAL RISK (named by the rc6/pipeline-reds lane, dbae320d): the
BETTOR shadow worker wrote the opportunity and then the decision as two
autocommitted statements, and the opportunity's id is its 300 s cadence
bucket. A deploy's SIGTERM cancels the tick wherever it is; cancelled between
the two writes, the opportunity stayed with no decision, and every later tick
in the SAME bucket was told `was_new False` by the opportunity insert's
ON CONFLICT DO NOTHING and skipped the decision. 180 s later the migration 073
view counts an orphan, and BETTOR_DECISION_PIPELINE reads DEGRADED for 24 h --
made by a restart, not by the policy.

These tests drive the REAL `workers.shadow_bettor.tick` with the real writers
(`record_opportunity`, `write_decision`, `ops.record_failure`) on a real
Postgres, inside one transaction that is rolled back at the end (the tick's
own transaction is then a savepoint, the same all-or-nothing). Only the venue
read, the universe and the clock are stood in: the clock is fixed inside one
cadence bucket so "the same bucket" is a fact of the test, not of when it ran.

  * stopped (cancelled) while the decision is being written, then the next
    tick in the same bucket: exactly one opportunity, with its decision
    (on 412c4962: the opportunity with NO decision -- the orphan);
  * a decision that FAILS to write still leaves the observation, with the
    failure named, exactly as before;
  * a decision WITHHELD by the integrity gate still leaves the observation
    written and undecided, exactly as before (owner directive 2026-09-19);
  * the V6 code boundary does not move (POLICY_CODE_SHA 9c66940429caf9b7).
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone

import pytest

from sportsassets import shadow_bettor as bettor
from sportsassets import shadow_bettor_policy as bpol
from sportsassets import shadow_store as store
from sportsassets.workers import shadow_bettor as W

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

_TABLES = ("bettor_opportunity_annotations, bettor_decision_failures, "
           "shadow_decisions, bettor_opportunities")


def _fixed_clock(monkeypatch) -> datetime:
    """The worker's clock, fixed in the middle of the current cadence
    bucket, so both ticks of a test observe in ONE bucket."""
    now = datetime.now(tz=timezone.utc).timestamp()
    mid = (int(now) // W.CADENCE_S) * W.CADENCE_S + W.CADENCE_S // 2
    at = datetime.fromtimestamp(mid, tz=timezone.utc)

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return at if tz is not None else at.replace(tzinfo=None)

    monkeypatch.setattr(W, "datetime", _Clock)
    return at


def _one_subject(monkeypatch) -> str:
    symbol = "aec-rc6-atomic-%s" % uuid.uuid4().hex[:10]

    async def _universe(_pool, **_kw):
        return [{"symbol": symbol, "outcomeLeg": "yes",
                 "eventId": "rc6-atomic-event", "marketId": None,
                 "sport": "NFL", "league": "NFL"}]

    monkeypatch.setattr(bettor, "universe", _universe)
    # no venue is called: an unreadable book is a named market state
    monkeypatch.setattr(W, "_read_quote",
                        lambda _s: {"error": "RC6_TEST_NO_VENUE_READ"})
    return symbol


async def _conn():
    import asyncpg
    c = await asyncpg.connect(DSN)
    tr = c.transaction()
    await tr.start()
    # an empty ledger for this test only; rolled back at the end, so no
    # append-only row is ever deleted
    await c.execute("TRUNCATE %s CASCADE" % _TABLES)
    got = await store.freeze_policy(c, policy=bpol.frozen_policy())
    assert got["status"] in ("FROZEN", "ALREADY_FROZEN"), got
    return c, tr


async def _close(c, tr):
    try:
        await tr.rollback()
    finally:
        await c.close()


async def _ledger(c, symbol) -> list:
    return [dict(r) for r in await c.fetch(
        "SELECT o.bettor_opportunity_id AS id, o.observed_at,"
        "       (SELECT count(*) FROM shadow_decisions d"
        "         WHERE d.bettor_opportunity_id = o.bettor_opportunity_id)"
        "           AS decisions,"
        "       (SELECT count(*) FROM bettor_decision_failures f"
        "         WHERE f.bettor_opportunity_id = o.bettor_opportunity_id)"
        "           AS failures"
        "  FROM bettor_opportunities o WHERE o.symbol = $1", symbol)]


@pg
async def test_a_tick_stopped_mid_decision_leaves_no_orphan_in_its_bucket(
        monkeypatch):
    at = _fixed_clock(monkeypatch)
    symbol = _one_subject(monkeypatch)
    c, tr = await _conn()
    try:
        entered, never = asyncio.Event(), asyncio.Event()
        real = bettor.write_decision

        async def _stopped_here(*_a, **_k):
            entered.set()
            await never.wait()          # the deploy's SIGTERM lands here

        monkeypatch.setattr(bettor, "write_decision", _stopped_here)
        task = asyncio.create_task(W.tick(c, decision_writing_allowed=True))
        await asyncio.wait_for(entered.wait(), 10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # THE RESTARTED WORKER, SAME BUCKET
        monkeypatch.setattr(bettor, "write_decision", real)
        stats = await W.tick(c, decision_writing_allowed=True)

        rows = await _ledger(c, symbol)
        assert len(rows) == 1, rows
        # on 412c4962: decisions == 0 -- the opportunity of the stopped tick
        # stayed, and this tick was told was_new False and skipped it
        assert rows[0]["decisions"] == 1, (rows, stats)
        assert rows[0]["failures"] == 0
        assert rows[0]["observed_at"] == at
        assert (stats["opportunities"], stats["decisions"],
                stats["failures"]) == (1, 1, 0), stats
    finally:
        await _close(c, tr)


@pg
async def test_a_completed_tick_then_a_second_tick_in_the_bucket_decides_once(
        monkeypatch):
    """The bucket's idempotency is unchanged: one opportunity, one
    decision, however many ticks look at the market in the bucket."""
    _fixed_clock(monkeypatch)
    symbol = _one_subject(monkeypatch)
    c, tr = await _conn()
    try:
        first = await W.tick(c, decision_writing_allowed=True)
        second = await W.tick(c, decision_writing_allowed=True)
        rows = await _ledger(c, symbol)
        assert [(r["decisions"], r["failures"]) for r in rows] == [(1, 0)]
        assert (first["opportunities"], first["decisions"]) == (1, 1)
        assert (second["opportunities"], second["decisions"]) == (0, 0)
    finally:
        await _close(c, tr)


@pg
async def test_a_decision_that_fails_to_write_keeps_the_observation_named(
        monkeypatch):
    """UNCHANGED: the observation commits, the failure is recorded in the
    database's own words, no decision is invented."""
    _fixed_clock(monkeypatch)
    symbol = _one_subject(monkeypatch)
    c, tr = await _conn()
    try:
        async def _fails(*_a, **_k):
            raise RuntimeError("rc6 test: the decision insert refused")

        monkeypatch.setattr(bettor, "write_decision", _fails)
        stats = await W.tick(c, decision_writing_allowed=True)
        rows = await _ledger(c, symbol)
        assert [(r["decisions"], r["failures"]) for r in rows] == [(0, 1)]
        assert (stats["opportunities"], stats["decisions"],
                stats["failures"]) == (1, 0, 1), stats
        stage = await c.fetchval(
            "SELECT stage FROM bettor_decision_failures "
            " WHERE bettor_opportunity_id = $1", rows[0]["id"])
        assert stage == "DECISION_WRITE"
    finally:
        await _close(c, tr)


@pg
async def test_a_withheld_decision_still_writes_the_observation(monkeypatch):
    """UNCHANGED (owner directive 2026-09-19 20:2xZ): OBSERVE = YES, WRITE
    OPPORTUNITY = YES, WRITE DECISION = NO while integrity fails."""
    _fixed_clock(monkeypatch)
    symbol = _one_subject(monkeypatch)
    c, tr = await _conn()
    try:
        stats = await W.tick(c, decision_writing_allowed=False)
        rows = await _ledger(c, symbol)
        assert [(r["decisions"], r["failures"]) for r in rows] == [(0, 0)]
        assert (stats["opportunities"], stats["decisionsWithheld"]) == (1, 1)
    finally:
        await _close(c, tr)


def test_the_v6_code_boundary_did_not_move():
    """The worker is outside BETTOR's decision boundary: the running code
    sha is still the one V6 froze."""
    assert bpol.POLICY_CODE_SHA.startswith("9c66940429caf9b7"), \
        bpol.POLICY_CODE_SHA


def test_the_rule_is_named_on_the_worker():
    assert "neither" in W.OPPORTUNITY_AND_DECISION_COMMIT_TOGETHER_RULE
