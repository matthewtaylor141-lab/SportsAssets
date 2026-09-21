"""The observation stop control, and every way it says no.

THE DEFECT THIS FILE PINS. `BETTOR_LIVE_LOOP=off` was set on
sportsassets-workers at 2026-09-21T22:38:58Z and acknowledged with
HTTP 200. Render raised no deploy for it. A restart was then requested
and DID happen -- `server_restarted` at 22:43:40.131647Z in the
service's own event log -- and at 22:47:29Z the loop was still running
the discovery path instead of reporting itself disabled. The
environment variable is not a demonstrated control on that service.

So the control moved into the database, where changing it needs no
deploy, and the tests below are mostly about the ways it REFUSES.
There is exactly one way to run and four ways to stop.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

from sportsassets import bettor_live_control as ctl


class Pool:
    """asyncpg's `fetchval`, over one value or one failure."""

    def __init__(self, value=None, *, raises=None, hang=None):
        self.value, self.raises, self.hang = value, raises, hang
        self.calls, self.sql = 0, None

    async def fetchval(self, sql, *args):
        self.calls += 1
        self.sql, self.args = sql, args
        if self.hang:
            await asyncio.sleep(self.hang)
        if self.raises is not None:
            raise self.raises
        return self.value


def read(pool):
    return asyncio.run(ctl.read_control(pool))


# ── 1. the one way to run ────────────────────────────────────────────

class TestTheOnlyWayToRun:

    @pytest.mark.parametrize("stored", ["true", True, '{"run": true}',
                                        {"run": True}])
    def test_an_explicit_true_runs(self, stored):
        s = read(Pool(stored))
        assert s["run"] is True and s["why"] == ctl.W_RUN
        assert ctl.is_closed(s) is False

    def test_the_object_form_may_carry_an_operators_note(self):
        """A note beside the switch must not change what it means."""
        s = read(Pool('{"run": true, "by": "matt", "at": "2026-09-21"}'))
        assert s["run"] is True and ctl.is_closed(s) is False

    def test_it_reads_the_namespaced_observation_key(self):
        p = Pool("true")
        read(p)
        assert p.args == (ctl.CONTROL_KEY,)
        assert ctl.CONTROL_KEY == "bettor_live_observation"
        assert "ingestion_state" in p.sql

    def test_bytes_from_the_driver_are_decoded(self):
        assert read(Pool(b"true"))["run"] is True


# ── 2. the four ways to stop ─────────────────────────────────────────

class TestEveryFailureIsClosed:

    def test_an_explicit_false_stops(self):
        s = read(Pool("false"))
        assert s["run"] is False and s["why"] == ctl.W_STOPPED
        assert ctl.is_closed(s) is True

    def test_an_absent_row_is_not_permission(self):
        """`execution_gate.read_state`'s rule, for the same reason: a
        control documented as 'off' that sits unarmed is not a control.
        It also makes activation an ordinary database write."""
        s = read(Pool(None))
        assert s["run"] is False and s["why"] == ctl.W_ABSENT
        assert s["readable"] is True, "absent is not unreadable"
        assert ctl.is_closed(s) is True

    def test_an_unreadable_control_stops(self):
        s = read(Pool(raises=ConnectionError("db down")))
        assert s["run"] is False and s["why"] == ctl.W_UNREADABLE
        assert s["readable"] is False
        assert "ConnectionError" in s["detail"], "named, not swallowed"

    @pytest.mark.parametrize("stored", [
        "yes", "off", "1", "", "null", "[]", '{"running": true}',
        '{"run": "true"}', '{"run": null}', '{"run": 1}', "not json",
    ])
    def test_anything_unparsable_stops(self, stored):
        s = read(Pool(stored))
        assert s["run"] is False and s["why"] == ctl.W_MALFORMED
        assert ctl.is_closed(s) is True

    def test_a_read_that_does_not_answer_stops_rather_than_hanging(self):
        """A control read that hangs has no answer, and from outside
        that is indistinguishable from a loop deciding to keep going."""
        s = asyncio.run(_with_timeout())
        assert s["run"] is False and s["why"] == ctl.W_UNREADABLE
        assert "did not answer" in s["detail"]

    def test_the_numbers_one_and_zero_are_not_booleans(self):
        assert read(Pool("1"))["why"] == ctl.W_MALFORMED
        assert read(Pool("0"))["why"] == ctl.W_MALFORMED


async def _with_timeout():
    orig = ctl.CONTROL_READ_TIMEOUT_S
    ctl.CONTROL_READ_TIMEOUT_S = 0.05
    try:
        return await ctl.read_control(Pool("true", hang=5.0))
    finally:
        ctl.CONTROL_READ_TIMEOUT_S = orig


# ── 3. what it is, and what it is not ────────────────────────────────

class TestItIsAnObservationControlAndNothingElse:

    def test_it_touches_no_trading_switch(self):
        """`describe()` is allowed to NAME them -- saying what this
        control does not govern is the point of that manifest. The test
        is that no code which READS OR DECIDES anything mentions one."""
        code = _code_only(ctl, without={"describe"})
        for other in ("live_trading_paused", "mirror_live",
                      "LIVE_COPY_HALT", "MAX_CONTRACTS"):
            assert other not in code, (
                "%s is named in logic, not just in the manifest" % other)

    def test_only_one_ingestion_state_key_is_ever_read(self):
        """The blunt version of the test above: whatever it says, it
        asks the database for exactly one key."""
        p = Pool("true")
        read(p)
        assert p.args == (ctl.CONTROL_KEY,)
        assert p.sql.count("$1") == 1 and "WHERE key=$1" in p.sql

    def test_it_writes_nothing(self):
        src = _code_only(ctl)
        for write in ("INSERT", "UPDATE", "DELETE", "execute("):
            assert write not in src, "a stop control is a READ"

    def test_it_imports_no_order_path(self):
        src = inspect.getsource(ctl)
        for forbidden in ("submit_fok", "close_position", "execution_gate"):
            assert forbidden not in _code_only(src)

    def test_describe_states_the_closed_set_and_the_reason(self):
        d = ctl.describe()
        assert set(d["fails_closed_on"]) == {
            ctl.W_UNREADABLE, ctl.W_ABSENT, ctl.W_MALFORMED}
        assert d["changes_without_deploy"] is True
        assert "server_restarted" in d["why_not_env"]
        assert "live_trading_paused" in d["does_not_govern"]

    def test_is_closed_is_true_for_every_non_running_why(self):
        for why in (ctl.W_STOPPED, ctl.W_ABSENT, ctl.W_UNREADABLE,
                    ctl.W_MALFORMED):
            assert ctl.is_closed({"run": True, "why": why}) is True, (
                "a stale run flag must not outvote the reason")
        assert ctl.is_closed({"run": True, "why": ctl.W_RUN}) is False
        assert ctl.is_closed({}) is True
        assert ctl.is_closed(None) is True


def _code_only(mod_or_src, *, without=()):
    """Source with docstrings and comments stripped, so the prose that
    EXPLAINS a hazard cannot fail a test that forbids DOING it.

    `without` drops named top-level functions as well -- used for the
    `describe()` manifest, whose whole job is to name the things this
    module deliberately has nothing to do with.
    """
    import ast
    src = mod_or_src if isinstance(mod_or_src, str) \
        else inspect.getsource(mod_or_src)
    tree = ast.parse(src)
    if without:
        tree.body = [n for n in tree.body
                     if getattr(n, "name", None) not in set(without)]
    for node in ast.walk(tree):
        # `body` is a list on modules/classes/functions and a single
        # expression on a lambda, so the type is checked, not assumed.
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        if not isinstance(body[0], ast.Expr):
            continue
        if isinstance(getattr(body[0], "value", None), ast.Constant) and \
                isinstance(body[0].value.value, str):
            body.pop(0)
    return ast.unparse(tree)
