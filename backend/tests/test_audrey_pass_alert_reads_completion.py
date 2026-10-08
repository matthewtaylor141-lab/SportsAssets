"""Audrey's "paper agent pass has not completed" alert reads the last
COMPLETED pass, not the last attempt.

2026-10-08 05:20Z and 06:20Z: the alert said "for an unknown time" while
passes were completing, because a timed-out attempt (no "at") overwrote the
attempt record the alert read."""
from __future__ import annotations

import asyncio

from sportsassets import slack_updates as SU


class _Conn:
    def __init__(self, health_at):
        self.health_at = health_at

    async def fetchval(self, sql, *args):
        assert "paper_session_health" in sql
        if isinstance(self.health_at, Exception):
            raise self.health_at
        return self.health_at


def _run(conn, attempt):
    return asyncio.run(SU.last_completed_pass_at(conn, attempt))


TIMED_OUT = {"ran": False, "trigger": "SERVICING_TASK",
             "refusal": "PAPER_PASS_RAISED_OR_TIMED_OUT",
             "why": "TimeoutError: ", "written_at": 1791442100.0}


def test_a_timed_out_attempt_does_not_erase_the_last_completed_pass():
    at = _run(_Conn(1791441990.0), TIMED_OUT)
    assert at == 1791441990.0
    # and the alert stays quiet when that completion is recent
    snap = {"reconcile": {"reconciled": True}, "research": [],
            "cycle_at": 1791442000.0, "pass_at": at}
    assert not [a for a in SU.alerts(snap, now=1791442100.0)
                if a[0] == "service:pass"]


def test_a_pass_that_ran_counts_from_the_attempt_record_too():
    ran = {"ran": True, "at": 1791442107.0, "trigger": "SERVICING_TASK"}
    assert _run(_Conn(1791441990.0), ran) == 1791442107.0
    assert _run(_Conn(None), ran) == 1791442107.0


def test_no_completed_pass_anywhere_is_still_unknown_and_alerts():
    assert _run(_Conn(None), TIMED_OUT) is None
    assert _run(_Conn(RuntimeError("no table")), TIMED_OUT) is None
    snap = {"reconcile": {"reconciled": True}, "research": [],
            "cycle_at": 1791442000.0, "pass_at": None}
    msgs = [a[2] for a in SU.alerts(snap, now=1791442100.0)
            if a[0] == "service:pass"]
    assert msgs and "unknown time" in msgs[0]


def test_a_stale_completion_still_alerts_with_its_age():
    at = _run(_Conn(1791442100.0 - 3600), TIMED_OUT)
    snap = {"reconcile": {"reconciled": True}, "research": [],
            "cycle_at": 1791442000.0, "pass_at": at}
    msgs = [a[2] for a in SU.alerts(snap, now=1791442100.0)
            if a[0] == "service:pass"]
    assert msgs == ["ALERT · paper agent pass has not completed for 60 min"]
