"""Pinned-source failure injection for C1's revised startup and age bounds.

Freshness tests execute the source AST of xavier_measure unchanged, with
only its DB/global collaborators supplied. This is an isolated function
proof, not a deployed/full-module/Postgres acceptance test.
"""
import ast
import asyncio
import datetime as dt
from pathlib import Path
from types import SimpleNamespace

import pytest
from sportsassets import pinnapi_feed_runtime as R


class HungPool:
    def acquire(self):
        class Context:
            async def __aenter__(self):
                await asyncio.Event().wait()
            async def __aexit__(self, *_):
                return False
        return Context()


@pytest.mark.asyncio
async def test_startup_scope_read_cannot_block_the_collector_forever(monkeypatch):
    monkeypatch.setattr(R, "ROW_READ_TIMEOUT_S", .02, raising=False)
    scope = await asyncio.wait_for(R.scope(HungPool()), .2)
    assert scope == R.DEFAULT_SCOPE


def measure_function():
    source = Path(R.__file__).parent / "agents/paper_benchmark.py"
    tree = ast.parse(source.read_text())
    node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "xavier_measure")
    env = {
        "policy_for": lambda strategy: None,
        "STRICT_POLICY": {"strategy":"STRICT", "disclosure":"TEST", "kind":"STRICT"},
        "DP": SimpleNamespace(LONG="LONG", SHORT="SHORT"),
        "L": SimpleNamespace(_epoch=lambda value: value.timestamp()),
        "COMPLETED_GAME_KINDS": (),
        "MF": SimpleNamespace(LINE_FAMILIES=("spread", "total",
                                             "team_total")),
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), env)
    return env["xavier_measure"]


class Connection:
    def __init__(self, at, age):
        self.at, self.age = at, age
    async def fetchrow(self, sql, *_):
        if "FROM paper_decisions d JOIN paper_orders" in sql:
            return {"decision_id":"d1", "p_pinnacle":.5, "valuation_id":1,
                    "decided_at":dt.datetime.fromtimestamp(self.at-60,dt.timezone.utc)}
        if "SELECT payout_event" in sql:
            return {"payout_event":"HOME_WIN", "payout_is_complement":False}
        if "SELECT id, probability, observed_at" in sql:
            return {"id":2, "probability":.6,
                    "observed_at":dt.datetime.fromtimestamp(self.at-self.age,dt.timezone.utc)}
        raise AssertionError("Unexpected query")


@pytest.mark.asyncio
@pytest.mark.parametrize("age,stale", [(-.0004,True),(-.0001,True),(0,False),
                                       (.0001,False),(30,False),(30.0004,True)])
async def test_xavier_freshness_uses_unrounded_age(age, stale):
    at = 1_790_000_000.0
    out = await measure_function()(
        Connection(at,age),
        {"now":at,"config":{"entry":{"valuation_lookback_s":3600,"pinnacle_max_age_s":30}}},
        pos={"group_id":"g1","holding_side":"LONG","us_market_slug":"test"})
    assert out["stale"] is stale


@pytest.mark.parametrize("setting,expected", [(None,True),("",True),("off",False),
    ("false",False),("0",False),("no",False),("on",True)])
def test_environment_kill_switch_semantics_are_preserved(monkeypatch,setting,expected):
    monkeypatch.delenv("PINNAPI_FEED",raising=False)
    if setting is not None:
        monkeypatch.setenv("PINNAPI_FEED",setting)
    assert R.enabled() is expected


@pytest.mark.asyncio
@pytest.mark.parametrize("value,expected", [(None,False),(False,False),(True,True),
                                          ('"true"',False),('true',True)])
async def test_control_row_remains_explicit_true_only(monkeypatch,value,expected):
    async def read(*_):
        return value
    monkeypatch.setattr(R,"_read_row",read)
    assert await R.armed(None) is expected
