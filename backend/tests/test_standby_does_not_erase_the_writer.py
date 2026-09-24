"""A PROCESS THAT WRITES NOTHING MUST NOT OVERWRITE THE ONE THAT DOES.

THE FAILURE THIS EXISTS FOR. Run 26 read production and got, from the
external loop's `last_cycle`:

    state      STANDBY_NOT_THE_WRITER
    evaluated  0
    markets    0
    refusals   {"ANOTHER_PROCESS_HOLDS_THE_WRITER_LOCK": 1}
    (no venue diagnostic this cycle)

and, from the prospective lane, `flow {}` with `accounted null`. Both read
as "the loop looked at nothing and refused nothing" -- and both were wrong.
Four loops were genuinely holding their locks (pids 3560456-9, one each,
`writer_ownership OK`) and cycling normally. The rows being read had been
written by a DIFFERENT process: the standby in the API container (pid 1),
which re-wrote the writer's own heartbeat key every IDLE_POLL_S with an
empty record.

The standby polls every 60 s. A writer cycles more slowly. So the standby
won nearly every race, and the flow accounting built to answer "reconcile
400 against 3" had been deployed for an hour while every read of it
returned `{}`.

This is the check that was missing: the standby path and the cycle path
must not address the same row.
"""
import inspect

import pytest

from sportsassets.workers import ext_pinnacle_loop as EXT
from sportsassets.workers import rn1x_model_loop as ML
from sportsassets.workers import rn1x_shadow as SH


class _Conn:
    """Records every (key, value) an INSERT ... ingestion_state writes."""

    def __init__(self):
        self.writes = []

    async def execute(self, sql, *args):
        if "ingestion_state" in sql:
            self.writes.append((args[0], args[1]))
        return "INSERT 0 1"


def test_the_two_keys_are_distinct_in_every_loop():
    assert EXT.STANDBY_KEY != EXT.HEARTBEAT_KEY
    assert ML.STANDBY_KEY != ML.HEARTBEAT_KEY
    assert SH.STANDBY_SERVICE != SH.SERVICE


def test_a_standby_write_lands_on_the_standby_key():
    import asyncio

    conn = _Conn()
    asyncio.run(EXT._heartbeat(
        conn, {"state": "STANDBY_NOT_THE_WRITER"}, key=EXT.STANDBY_KEY))
    assert len(conn.writes) == 1
    assert conn.writes[0][0] == EXT.STANDBY_KEY


def test_a_cycle_write_still_lands_on_the_writer_key():
    import asyncio

    conn = _Conn()
    asyncio.run(EXT._heartbeat(conn, {"state": "RAN", "evaluated": 3}))
    assert conn.writes[0][0] == EXT.HEARTBEAT_KEY


def test_the_model_loop_standby_write_is_separated_too():
    import asyncio

    conn = _Conn()
    asyncio.run(ML._heartbeat(
        conn, {"state": "STANDBY_NOT_THE_WRITER"}, key=ML.STANDBY_KEY))
    assert conn.writes[0][0] == ML.STANDBY_KEY


def _code_only(src: str) -> str:
    """Drop comments and docstring lines.

    The first draft of these assertions matched the COMMENTS this fix
    added, which is the same mistake as a check that cannot fail: it would
    pass on a file where the comment survived and the call did not.
    """
    out = []
    for line in src.splitlines():
        stripped = line.split("#", 1)[0]
        out.append(stripped)
    return "\n".join(out)


def test_the_standby_branch_passes_the_standby_key():
    """The defect was in the CALL, not the writer -- so read the call.

    `_heartbeat` was always capable of writing the right row; the standby
    branch simply did not say which row. Asserting on the helper alone
    would have passed throughout the failure.
    """
    src = _code_only(inspect.getsource(EXT.run))
    assert "key=STANDBY_KEY" in src, (
        "the external standby branch must name the standby key")
    src = _code_only(inspect.getsource(ML.run))
    assert "key=STANDBY_KEY" in src

    src = _code_only(inspect.getsource(SH.run))
    assert "heartbeat(STANDBY_SERVICE" in src, (
        "the shadow standby must not beat on the writer's service row")
    # The writer's own beat -- `heartbeat(SERVICE, "idle" if idle else
    # "ok", res)` -- is correct and must stay, so this pins the STANDBY
    # literal specifically rather than any beat on SERVICE.
    assert 'heartbeat(SERVICE, "idle",' not in src, (
        "the standby branch is back on the writer's row")


def test_the_prospective_lane_flow_is_not_written_by_a_standby():
    """The specific loss: `flow` lives in the shadow loop's cycle record,
    and a standby beat on the same service row replaced it wholesale."""
    src = _code_only(inspect.getsource(SH.run))
    # the standby branch carries no cycle fields at all
    i = src.find("STANDBY_NOT_THE_WRITER")
    assert i > 0
    window = src[max(0, i - 600):i + 400]
    assert "flow" not in window, (
        "a standby has no flow to report and must not write one")
    assert "STANDBY_SERVICE" in window


def test_the_reader_reports_the_standby_without_conflating_it():
    from sportsassets.api import command_rn1x as API

    src = inspect.getsource(API)
    assert "_EXT.STANDBY_KEY" in src
    assert "ML.STANDBY_KEY" in src
    assert 'out["standby"]' in src
