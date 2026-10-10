"""rc6.3 capability: the research loop names WHY a review failed and WHERE a
tick's time went, and records its successes.

PRODUCTION (research-sql runs 37978689499, 38008493448, 38009039152; SELECT
only).

* Every non-genuine reply was recorded as NO_GENUINE_GROUNDED_REVIEW: 912
  directive-path refusals (REQUIRES_OPERATOR_CREDENTIAL), 55 model HTTP_400
  failures and 279 interrupted replies read the same as an answer that cited
  nothing; the cause was only in the chat transcript. And 1,138 reviews were
  recorded as a bare 'TimeoutError' -- only their 55.1-67.7 s
  claim-to-outcome times showed it was the persona call (55 s budget), not
  the evidence read (10 s).
* runtime_loop_health agents.capability_runtime read 'starts 0, successes 0,
  errors 79-81' while the loop's heartbeat said OK: run() recorded only
  failures.
* 'CLAIM: TimeoutError' / 'CONTROL: TimeoutError' / 'HEARTBEAT:
  TimeoutError' could not say whether the budget went on waiting for a pool
  connection or on the statements, nor that the API event loop was held past
  the deadline (six holds >= 2 s, max 5.2 s, in the current API process);
  the claim's control-row read has max 2,880 ms and the same read without
  FOR UPDATE max 2,972 ms -- a database-wide stall, not row-lock contention.

No database: the persona chat, the pool and loop health are stand-ins.
"""
from __future__ import annotations

import asyncio
import copy
import json
import time
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from sportsassets import loop_health as LH
from sportsassets import refusal_taxonomy as RT
from sportsassets.agents import capability_runtime as R
from sportsassets.agents import capability_work as W

NOW = 1800000000.0


@asynccontextmanager
async def _tx(**kw):
    yield


def _conn(current):
    c = AsyncMock()
    c.transaction = MagicMock(side_effect=_tx)
    c.fetchrow.return_value = current
    return c


def _task(agent="AUDREY"):
    return dict(task_id="capwork:rc63", assignee=agent,
                title="Review this recorded settlement.", status="IN_PROGRESS",
                spec=dict(account_id=W.ACCOUNT, attempts=1,
                          claim_token="owner1", lease_until=NOW + 100,
                          dependencies=[], due_at=NOW + 86400, context={}),
                outcome={})


async def _finish(reply):
    t = _task()
    c = _conn(copy.deepcopy(t))
    await W.finish(c, t, reply, NOW)
    return json.loads(c.execute.call_args_list[0].args[4])


# ── 1. a non-genuine review is recorded with its reason ───────────────

def test_a_directive_path_refusal_is_named_not_no_genuine_review():
    """The production shape: Audrey's question answered by the directive
    path with the read credential's refusal."""
    out = asyncio.run(_finish({
        "status": "REQUIRES_OPERATOR_CREDENTIAL", "message_id": "m:1",
        "answer": "Requires the operator credential",
        "provider": {"mode": "DETERMINISTIC", "failure": None},
        "facts": []}))
    assert out["error"] == \
        "REVIEW_PERSONA_REFUSED:REQUIRES_OPERATOR_CREDENTIAL"


def test_a_persona_refusal_carries_its_refusal_code():
    out = asyncio.run(_finish({
        "status": "REFUSED", "refusal": "ONLY_AUDREY_RECORDS_DIRECTIVES",
        "message_id": "m:2", "answer": "That reads as an instruction",
        "provider": {"mode": "DETERMINISTIC"}, "facts": []}))
    assert out["error"] == \
        "REVIEW_PERSONA_REFUSED:ONLY_AUDREY_RECORDS_DIRECTIVES"


def test_a_model_failure_is_named_with_the_provider_failure():
    """Production 2026-10-06 13:00Z - 10-07 01:13Z: 55 XAVIER replies fell
    back to records with the model's HTTP_400."""
    out = asyncio.run(_finish({
        "status": "ANSWERED", "message_id": "m:3",
        "answer": "Records-only answer -- the AI answer was not used",
        "provider": {"mode": "RECORDS_ONLY", "failure": "HTTP_400"},
        "facts": [{"source": "paper_orders", "record_id": "o:1"}]}))
    assert out["error"] == "REVIEW_MODEL_ANSWER_NOT_USED:HTTP_400"
    assert out["provider_failure"] == "HTTP_400"


def test_no_model_configured_and_interrupted_replies_are_named():
    a = asyncio.run(_finish({"status": "LLM_UNAVAILABLE",
                             "llm_reason": "NO_ANTHROPIC_API_KEY"}))
    assert a["error"] == "REVIEW_MODEL_UNAVAILABLE:NO_ANTHROPIC_API_KEY"
    b = asyncio.run(_finish({"status": "INTERRUPTED", "message_id": "m:4",
                             "answer": "", "provider": {
                                 "mode": "INTERRUPTED"}}))
    assert b["error"] == "REVIEW_PERSONA_INTERRUPTED"


def test_an_answer_citing_nothing_grounded_keeps_its_old_name():
    out = asyncio.run(_finish({
        "status": "ANSWERED", "message_id": "m:5", "answer": "An answer.",
        "provider": {"mode": "LLM"},
        "facts": [{"source": "agent_tasks", "field": "research_objective"}]}))
    assert out["error"] == "NO_GENUINE_GROUNDED_REVIEW"


def test_every_named_reason_is_classified_in_the_refusal_taxonomy():
    for code in (W.R_NO_GENUINE_GROUNDED_REVIEW, W.R_REVIEW_PERSONA_REFUSED,
                 W.R_REVIEW_MODEL_ANSWER_NOT_USED, W.R_REVIEW_MODEL_UNAVAILABLE,
                 W.R_REVIEW_PERSONA_INTERRUPTED, W.R_REVIEW_PERSONA_ERROR):
        assert RT.lookup(code + ":DETAIL") is not None, code


# ── 2. an execute step that raises is named ───────────────────────────

@pytest.mark.parametrize("where,expected", [
    ("evidence", "EVIDENCE:TimeoutError"),
    ("converse", "CONVERSE:TimeoutError"),
])
def test_an_execute_step_that_raises_is_recorded_with_its_step(
        monkeypatch, where, expected):
    from sportsassets.agents import persona_chat as P
    if where == "evidence":
        monkeypatch.setattr(R, "evidence",
                            AsyncMock(side_effect=TimeoutError()))
    else:
        monkeypatch.setattr(R, "evidence", AsyncMock(return_value=True))
        monkeypatch.setattr(P, "converse",
                            AsyncMock(side_effect=TimeoutError()))
    finish = AsyncMock(return_value=True)
    monkeypatch.setattr(W, "finish", finish)
    c = _conn(None)

    @asynccontextmanager
    async def acquire():
        yield c
    pool = MagicMock()
    pool.acquire = acquire
    got = asyncio.run(R.execute(pool, _task()))
    assert finish.await_args.kwargs["error"] == expected
    assert got == {"task_id": "capwork:rc63", "reviewed": False,
                   "error": expected}


# ── 3. a completed tick is a loop-health SUCCESS ──────────────────────

def _run_once(monkeypatch, state):
    recorded = []

    async def record(target, name, **kw):
        recorded.append((name, kw.get("phase"), kw.get("error"),
                         kw.get("detail")))
        return True

    async def fake_sleep(s):
        raise asyncio.CancelledError

    async def tick(pool):
        return state

    monkeypatch.setattr(R, "tick", tick)
    monkeypatch.setattr(LH, "record", record)
    monkeypatch.setattr(R.asyncio, "sleep", fake_sleep)

    async def get_pool():
        return object()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(R.run(get_pool))
    return recorded


def test_an_ok_tick_is_recorded_as_a_success_with_what_it_did(monkeypatch):
    review = {"task_id": "capwork:x", "reviewed": False,
              "error": "REVIEW_MODEL_ANSWER_NOT_USED:HTTP_400"}
    got = _run_once(monkeypatch, {"status": "OK", "task_id": "capwork:x",
                                  "review": review, "admission_error": None,
                                  "evaluation_error": None})
    assert got == [("agents.capability_runtime", LH.SUCCESS, None,
                    {"status": "OK", "task_id": "capwork:x",
                     "review": review})]


def test_a_degraded_tick_is_an_error_naming_both_steps(monkeypatch):
    got = _run_once(monkeypatch, {
        "status": "DEGRADED", "task_id": None, "review": None,
        "admission_error": "ADMIT: TimeoutError at POOL_ACQUIRE",
        "evaluation_error": None})
    assert len(got) == 1 and got[0][1] == LH.ERROR
    assert got[0][2] == ("DEGRADED: admission=ADMIT: TimeoutError at "
                         "POOL_ACQUIRE; evaluation=None")


@pytest.mark.parametrize("status", ["OFF", "SCHEMA_UNAVAILABLE"])
def test_a_tick_that_ran_nothing_records_nothing(monkeypatch, status):
    assert _run_once(monkeypatch, {"status": status}) == []


# ── 4. a tick timeout names its sub-step and lateness ─────────────────

def _pool(acquire_cm):
    pool = MagicMock()
    pool.acquire = acquire_cm
    return pool


def _shrink(monkeypatch, budget=0.05):
    real = asyncio.timeout
    monkeypatch.setattr(R.asyncio, "timeout", lambda s: real(min(s, budget)))


def _tick_fails(monkeypatch, pool):
    monkeypatch.setattr(W, "schema", AsyncMock(return_value=True))
    monkeypatch.setattr(W, "control",
                        AsyncMock(return_value={"enabled": True}))
    monkeypatch.setattr(R, "admit", AsyncMock(return_value=0))

    async def main():
        with pytest.raises(R.TickPhaseFailed) as got:
            await R.tick(pool)
        return got.value
    return asyncio.run(main())


def test_a_claim_starved_of_a_pool_slot_is_named_pool_acquire(monkeypatch):
    calls = {"n": 0}

    @asynccontextmanager
    async def ok():
        yield MagicMock()

    @asynccontextmanager
    async def starved():
        await asyncio.sleep(10)
        yield MagicMock()

    def acquire(*a, **kw):
        calls["n"] += 1
        return ok() if calls["n"] <= 2 else starved()
    _shrink(monkeypatch)
    err = _tick_fails(monkeypatch, _pool(acquire))
    assert err.phase == "CLAIM" and err.step == R.POOL_ACQUIRE
    assert err.describe().startswith(
        "CLAIM: TimeoutError at POOL_ACQUIRE after ")


def test_a_claim_whose_statements_run_out_is_named_statements(monkeypatch):
    @asynccontextmanager
    async def ok():
        yield MagicMock()

    async def slow_claim(conn, now):
        await asyncio.sleep(10)
    monkeypatch.setattr(W, "claim", slow_claim)
    _shrink(monkeypatch)
    err = _tick_fails(monkeypatch, _pool(lambda *a, **kw: ok()))
    assert err.phase == "CLAIM" and err.step == R.STATEMENTS
    assert "late" not in err.describe()


def test_a_timeout_that_fired_late_says_the_event_loop_was_held(monkeypatch):
    """A synchronous hold inside the step: the timeout cannot fire until the
    loop runs again, so it is observed well after its deadline."""
    @asynccontextmanager
    async def ok():
        yield MagicMock()

    async def held_claim(conn, now):
        time.sleep(R.LATE_S + 0.3)       # the event loop is held
        await asyncio.sleep(10)
    monkeypatch.setattr(W, "claim", held_claim)
    _shrink(monkeypatch)
    err = _tick_fails(monkeypatch, _pool(lambda *a, **kw: ok()))
    assert err.step == R.STATEMENTS
    assert "the event loop was held past the deadline" in err.describe()


def test_the_run_loop_records_the_attributed_failure(monkeypatch):
    async def failing(pool):
        raise R.TickPhaseFailed("HEARTBEAT", TimeoutError(),
                                step=R.POOL_ACQUIRE, elapsed_s=3.9,
                                budget_s=3)
    recorded = []

    async def record(target, name, **kw):
        recorded.append((kw.get("phase"), kw.get("error")))
        return True

    async def fake_sleep(s):
        raise asyncio.CancelledError
    monkeypatch.setattr(R, "tick", failing)
    monkeypatch.setattr(LH, "record", record)
    monkeypatch.setattr(R.asyncio, "sleep", fake_sleep)

    async def get_pool():
        return object()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(R.run(get_pool))
    assert recorded == [(LH.ERROR, "HEARTBEAT: TimeoutError at POOL_ACQUIRE "
                                   "after 3.90s of a 3s budget (expired "
                                   "0.90s late: the event loop was held "
                                   "past the deadline)")]


# ── 5. the heartbeat carries the claimed review's outcome by name ─────

def test_the_heartbeat_names_the_claimed_reviews_outcome(monkeypatch):
    written = []
    c = MagicMock()

    async def execute(sql, *args):
        written.append(args)
    c.execute = execute
    c.transaction = MagicMock(side_effect=_tx)

    @asynccontextmanager
    async def acquire():
        yield c
    pool = _pool(acquire)
    review = {"task_id": "capwork:rc63", "reviewed": False,
              "error": "REVIEW_PERSONA_REFUSED:REQUIRES_OPERATOR_CREDENTIAL"}
    monkeypatch.setattr(W, "schema", AsyncMock(return_value=True))
    monkeypatch.setattr(W, "control",
                        AsyncMock(return_value={"enabled": True}))
    monkeypatch.setattr(R, "admit", AsyncMock(return_value=0))
    monkeypatch.setattr(W, "claim", AsyncMock(return_value=_task()))
    monkeypatch.setattr(R, "execute", AsyncMock(return_value=review))
    from sportsassets.agents import capability_experiments as E
    monkeypatch.setattr(E, "evaluate_due", AsyncMock(return_value=None))
    state = asyncio.run(R.tick(pool))
    assert state["status"] == "OK" and state["review"] == review
    assert json.loads(written[-1][1])["review"] == review
