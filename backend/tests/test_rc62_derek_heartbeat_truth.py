"""rc6.2 agent-truth: Derek's heartbeat says what his records say.

Production: the registry heartbeat said WAITING_FOR_EVIDENCE
'NO_DECISION_RECORDED:ZERO_...' (the FUNDED collection cycle wrote no
valuation rows) while paper_decisions held 390 of his decisions in the
last hour. The heartbeat is now derived from his recorded paper decisions,
and a funded-path 'nothing recorded' is labelled as the funded path's.
"""
import json
import time
import uuid
import pytest
from sportsassets.agents import runtime as RT, registry as R
from tests import paper_harness as H


def test_strategy_constant_is_dereks_paper_strategy():
    from sportsassets.agents import paper_derek
    assert RT.DEREK_PAPER_STRATEGY == paper_derek.STRATEGY


def test_paper_decisions_make_the_state_decision_recorded():
    st, act, w = RT.derek_end_state({"ran": True, "written": 0, "cycle_label": "ZERO_VALUATIONS"}, None)
    assert st == R.S_WAITING_FOR_EVIDENCE and act == "NO_DECISION_RECORDED:ZERO_VALUATIONS"
    paper = {"decisions_1h": 390, "latest": {"decision_id": "pd:1", "decided_at": 1.0, "verdict": "REFUSE", "refusal": "STRATEGY_QUARANTINED"}}
    st2, act2, w2 = RT.derek_truthful_state(st, act, w, paper)
    assert st2 == R.S_DECISION_RECORDED
    assert act2 == "PAPER_DECISIONS_RECORDED_1H:390:LATEST_REFUSE:STRATEGY_QUARANTINED"
    assert w2["funded_path"] == "NO_DECISION_RECORDED:ZERO_VALUATIONS"
    assert w2["paper_decisions_1h"] == 390 and w2["latest_paper_decision"]["decision_id"] == "pd:1"


def test_no_paper_decision_keeps_waiting_but_labels_the_funded_path():
    st, act, w = RT.derek_truthful_state(R.S_WAITING_FOR_EVIDENCE, "NO_DECISION_RECORDED:X", {}, {"decisions_1h": 0, "latest": None})
    assert st == R.S_WAITING_FOR_EVIDENCE and act == "FUNDED_PATH_NO_DECISION_RECORDED:X"
    assert w["paper_decisions_1h"] == 0
    st, act, w = RT.derek_truthful_state(R.S_WAITING_FOR_EVIDENCE, "NO_DECISION_RECORDED:X", None, None)
    assert st == R.S_WAITING_FOR_EVIDENCE and act == "FUNDED_PATH_NO_DECISION_RECORDED:X"
    assert w["paper_decisions_1h"] is None


@pytest.mark.parametrize("state,activity", [
    (R.S_FAILED, "HOOK_RAISED:after_cycle"),
    (R.S_BLOCKED, "ENTRY_BLOCKED:STOPPED"),
    (R.S_DECISION_RECORDED, "VALUATION_ROWS_WRITTEN:4"),
    (R.S_WAITING_FOR_EVIDENCE, "REPORTED_BY_DEREK")])
def test_other_measured_states_are_left_alone(state, activity):
    paper = {"decisions_1h": 5, "latest": {"verdict": "REFUSE"}}
    assert RT.derek_truthful_state(state, activity, {"a": 1}, paper) == (state, activity, {"a": 1})


class _Tx:
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


class _BrokenConn:
    def transaction(self): return _Tx()
    async def fetchrow(self, *a): raise RuntimeError("relation does not exist")


@pytest.mark.asyncio
async def test_unreadable_record_is_none_never_raises():
    assert await RT.derek_paper_record(_BrokenConn(), now=time.time()) is None


@pytest.mark.asyncio
@pytest.mark.skipif(not H.DSN, reason="requires RN1X_TEST_DSN")
async def test_reader_counts_dereks_recorded_decisions_in_the_hour():
    conn = await H.connect()
    try:
        acct = await H.new_account(conn, "dhb")
        # paper_decisions is append-only, so each run writes in its own
        # far-future window on a whole-day grid (BASE + k days). This
        # run's rows lie in [now - 2 h, now + 1 min]; any other run's lie
        # whole days away, so neither the hour (now - 1 h, now] nor the
        # empty probe at now + 12 h can see them. A k whose windows
        # already hold rows (an earlier run drew the same k) is skipped.
        base, day = 2524608000.0, 86400.0           # 2050-01-01T00:00Z
        for _ in range(20):
            now = base + (uuid.uuid4().int % 10**5) * day
            if (await RT.derek_paper_record(conn, now=now))["decisions_1h"] == 0 and (
                    await RT.derek_paper_record(conn, now=now + day / 2))["decisions_1h"] == 0:
                break
        else:
            pytest.fail("no unused far-future window")
        async def ins(at, verdict, refusal, strategy="DEREK_ENTRY_POLICY_V2"):
            did = "paper_dec_" + uuid.uuid4().hex
            await conn.execute(
                "INSERT INTO paper_decisions(decision_id,session_id,account_id,decided_at,verdict,refusal,internal_model,pinnacle,qualification_gaps,policy_version,simulator_version,strategy)"
                " VALUES($1,$2,$3,to_timestamp($4),$5,$6,'{}','{}','[]','P','S',$7)",
                did, acct["session_id"], acct["account_id"], at, verdict, refusal, strategy)
            return did
        await ins(now - 7200, "ENTER", None)               # outside the hour
        await ins(now - 1800, "REFUSE", "A")
        mine = await ins(now - 60, "REFUSE", "STRATEGY_QUARANTINED")
        await ins(now - 30, "ENTER", None, strategy="PINNACLE_EXPLORATION_PAPER")  # not Derek
        await ins(now + 60, "ENTER", None)                 # after `now`: not in his last hour
        got = await RT.derek_paper_record(conn, now=now)
        assert got["decisions_1h"] == 2
        assert got["latest"]["decision_id"] == mine
        assert got["latest"]["verdict"] == "REFUSE" and got["latest"]["refusal"] == "STRATEGY_QUARANTINED"
        st, act, _ = RT.derek_truthful_state(R.S_WAITING_FOR_EVIDENCE, "NO_DECISION_RECORDED:ZERO", None, got)
        assert st == R.S_DECISION_RECORDED and act == "PAPER_DECISIONS_RECORDED_1H:2:LATEST_REFUSE:STRATEGY_QUARANTINED"
        empty = await RT.derek_paper_record(conn, now=now + day / 2)
        assert empty == {"decisions_1h": 0, "latest": None}
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_cycle_finished_writes_the_truthful_heartbeat(monkeypatch):
    beats = []
    async def hb(conn, aid, **kw):
        beats.append((aid, kw)); return {"ok": True}
    async def hook(*a, **k): return {"ok": True, "installed": True, "result": None, "hook": "after_cycle"}
    async def paper(conn, now=None): return {"decisions_1h": 390, "latest": {"verdict": "REFUSE", "refusal": "Q"}}
    monkeypatch.setattr(R, "heartbeat", hb)
    monkeypatch.setattr(RT, "call_hook", hook)
    monkeypatch.setattr(RT, "derek_paper_record", paper)
    monkeypatch.setattr(RT, "paper_pass_hook", lambda **k: None)
    got = await RT.derek_cycle_finished(object(), cycle={"ran": True, "written": 0, "cycle_label": "ZERO_VALUATIONS"}, now=1000.0)
    assert got["state"] == R.S_DECISION_RECORDED
    assert beats[-1][1]["state"] == R.S_DECISION_RECORDED
    assert beats[-1][1]["activity"] == "PAPER_DECISIONS_RECORDED_1H:390:LATEST_REFUSE:Q"
    assert beats[-1][1]["waiting_on"]["funded_path"] == "NO_DECISION_RECORDED:ZERO_VALUATIONS"
