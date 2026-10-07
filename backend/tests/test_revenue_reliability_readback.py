"""Revenue Reliability V1 binding: the read-only Command readback and Audrey's
governed proposals. Synthetic rows shaped like the production queries."""
from __future__ import annotations

import ast
import asyncio
import json
import re
from pathlib import Path

import pytest

from sportsassets.agents import improvement as IMP
from sportsassets.agents import revenue_improvements as RI
from sportsassets.revenue_reliability import core as C
from sportsassets.revenue_reliability import evidence as E
from sportsassets.revenue_reliability import read as RD

PKG = Path(__file__).resolve().parents[1] / "sportsassets"
ROOT = Path(__file__).resolve().parents[1]
NOW = 1_791_400_000.0
WRITE = re.compile(r"\b(INSERT\s+INTO|UPDATE\s+[a-z_]+\s+SET|DELETE\s+FROM|TRUNCATE|ALTER\s+TABLE|DROP\s+TABLE|"
                   r"CREATE\s+TABLE)", re.I)


def _cf(actual, hold, exit_):
    return json.dumps({"ACTUAL_XAVIER": {"pnl_usd": actual, "turnover_usd": 100.0, "available": True},
                       "HOLD_TO_SETTLEMENT": {"pnl_usd": hold, "available": True},
                       "IMMEDIATE_EXIT": {"pnl_usd": exit_, "available": True}})


def _positions(n_events=150, per_event=1, strategy="S1", actual=5.0, hold=4.0, exit_=3.0, day0=NOW - 20 * 86400):
    out = []
    for e in range(n_events):
        for k in range(per_event):
            out.append({"thesis_id": "t%s_%d_%d" % (strategy, e, k), "strategy": strategy,
                        "group_id": "g%s_%d_%d" % (strategy, e, k),
                        "us_market_slug": "aec-mlb-a%03d-b%03d-2026-10-01" % (e, e), "holding_side": "LONG",
                        "decision_id": "d%d" % e, "entered_at": day0 + (e % 15) * 86400, "entry_qty": 100,
                        "entry_cost_usd": 50.0, "entry_fees_usd": 1.0, "entry_probability": 0.55,
                        "entry_ev_usd": 2.0, "event_start_at": day0 + (e % 15) * 86400 + 3600, "status": "FINAL",
                        "outcome_basis": "SETTLED", "counterfactuals": _cf(actual, hold, exit_), "incremental": "{}",
                        "computed_at": NOW, "event_slug": None, "sports_type": "baseball_moneyline",
                        "team_league": "mlb"})
    return out


def _rows(**kw):
    life = kw.pop("lifecycle", [{"strategy": "S1", "state": "ACTIVE_CHAMPION", "from_state": None, "rule_id": None,
                                 "why": None, "evidence": json.dumps({"rolling": {"capital_hours": 1000.0}}),
                                 "recorded_at": NOW}])
    return {"lifecycle": life, "positions": kw.pop("positions", _positions()), "models": [], "segments": [],
            "karen": [{"target_agent": "XAVIER", "detector": "D", "category": "C", "production_effect": "NONE",
                       "challenges": 10, "blocked": 0, "false_blocks": 0, "false_block_assessed": 0,
                       "targets": 10, "with_impact": 0}],
            "variants": [], "allocations": [], "reconciliation": [], "improvements": [], **kw}


# ── route ──────────────────────────────────────────────────────────────────
def test_route_is_get_only_registered_authenticated_and_read_only():
    from fastapi.testclient import TestClient

    from sportsassets.api import app as APP
    from sportsassets.api import command_revenue_reliability as R
    assert R.PATH == "/api/command/revenue-readiness"
    paths, stack = {}, list(APP.app.routes)
    while stack:
        r = stack.pop()
        if hasattr(r, "original_router"):
            stack.extend(r.original_router.routes)
        elif getattr(r, "path", "") == R.PATH:
            paths[r.path] = set(getattr(r, "methods", set()) or set())
    assert paths and all(m <= {"GET", "HEAD"} for m in paths.values())
    client = TestClient(APP.app, raise_server_exceptions=False)
    assert client.get(R.PATH).status_code == 401
    for verb in (client.post, client.put, client.delete, client.patch):
        assert verb(R.PATH).status_code in (401, 405)
    for rel in ("api/command_revenue_reliability.py", "revenue_reliability/read.py",
                "revenue_reliability/evidence.py", "revenue_reliability/core.py"):
        src = (PKG / rel).read_text()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert not WRITE.search(node.value), (rel, node.value[:60])
        for bad in ("submit_order", "request_cancel", "place_order", "live_executor", "bettor_funded"):
            assert bad not in src, (rel, bad)
    assert "readonly=True" in (PKG / "revenue_reliability/read.py").read_text()
    ping = (ROOT.parent / ".github" / "workflows" / "ping.yml").read_text()
    assert "get revenue_readiness /api/command/revenue-readiness" in ping


def test_every_query_is_a_bounded_select():
    for k, q in E.QUERIES.items():
        s = q.strip()
        assert s.upper().startswith(("SELECT", "WITH")), k
        assert "LIMIT" in s.upper(), k
        assert not WRITE.search(s), k
        assert set(RD.PARAMS[k]) <= {"acct", "days"}
        assert len(RD.PARAMS[k]) == len(set(re.findall(r"\$(\d)", s))), k


# ── decisions ─────────────────────────────────────────────────────────────
def test_cash_is_the_incumbent_and_least_negative_is_never_promoted():
    rows = _rows(positions=_positions(strategy="S1", actual=-1.0, hold=-0.5, exit_=-0.4) +
                 _positions(strategy="S2", actual=-50.0, hold=-40.0, exit_=-30.0),
                 lifecycle=[{"strategy": s, "state": "ACTIVE_CHALLENGER", "evidence": "{}", "rule_id": None,
                             "from_state": None, "why": None, "recorded_at": NOW} for s in ("S1", "S2")])
    out = E.build(rows, bankroll_usd=500000.0, bankroll_basis="t", now=NOW)
    t = out["strategy_tournament"]
    assert t["selected"] == "CASH" and t["status"] == "NO_PROMOTION"
    d = out["daily_revenue_readiness"]
    assert d["planned_capital_usd"] == 0 and d["cash_usd"] == 500000.0
    assert d["what_has_earned_capital_today"] == "NOTHING -- CASH"
    assert out["authority_changed"] is False and out["small_live"] == "SHADOW"


def test_positive_proven_strategy_deploys_only_demonstrated_capacity_never_a_turnover_target():
    out = E.build(_rows(), bankroll_usd=500000.0, bankroll_basis="t", now=NOW)
    s = out["strategies"]["S1"]
    assert s["independent_events"] == 150 and s["net_per_event"]["lower"] > 0
    assert s["executable_capacity_usd"] == pytest.approx(150 * 50.0 / 15)
    plan = out["reliability_plan"]
    assert plan["turnover_target"] is None
    assert plan["allocations"]["S1"] <= s["executable_capacity_usd"] + 1e-9
    assert plan["allocations"]["CASH"] == pytest.approx(500000.0 - plan["allocations"]["S1"])
    assert out["strategy_tournament"]["selected"] == "S1"


def test_lifecycle_is_an_additional_gate_never_a_grant():
    rows = _rows(lifecycle=[{"strategy": "S1", "state": "QUARANTINED", "evidence": "{}", "rule_id": "R",
                             "from_state": None, "why": None, "recorded_at": NOW}])
    out = E.build(rows, bankroll_usd=500000.0, bankroll_basis="t", now=NOW)
    assert out["reliability_plan"]["allocations"] == {"CASH": 500000.0}
    assert out["reliability_plan"]["reasons"]["S1"] == "LIFECYCLE_QUARANTINED"


def test_xavier_must_beat_both_frozen_counterfactuals():
    out = E.build(_rows(positions=_positions(actual=5.0, hold=1.0, exit_=9.0)), bankroll_usd=1.0,
                  bankroll_basis="t", now=NOW)
    x = out["agent_scoreboard"]["XAVIER"]
    assert x["vs_hold_to_settlement_usd"] > 0 and x["vs_immediate_exit_usd"] < 0
    assert x["positive_against_both"] is False and x["license"] != "LICENSED"


def test_karen_is_not_scored_on_challenge_volume():
    out = E.build(_rows(), bankroll_usd=1.0, bankroll_basis="t", now=NOW)
    k = out["agent_scoreboard"]["KAREN"]
    assert k["independent_events"] == 0 and k["license"] == "SHADOW_ONLY"
    assert k["exact_blocker"].startswith("NO_BLOCKING_CHALLENGES")


def test_certification_never_changes_authority_for_any_agent():
    out = E.build(_rows(), bankroll_usd=1.0, bankroll_basis="t", now=NOW)
    for aid, a in out["agent_scoreboard"].items():
        assert a["current_authority"] == C.AGENTS[aid].current_authority
        assert a["authority"]["authority_changed"] is False
        assert a["authority"]["capital_authority_granted"] is False
        assert a["license"] in C.LICENSES


def test_audrey_reconciliation_residual_is_measured_exactly():
    rec = [{"group_id": "gS1_0_0", "us_market_slug": "x", "holding_side": "LONG", "net_qty": 0,
            "fill_cash": 5.0, "payout_per_contract": None, "value_add_actual_pnl": 5.0},
           {"group_id": "gS1_1_0", "us_market_slug": "y", "holding_side": "LONG", "net_qty": 10,
            "fill_cash": -6.0, "payout_per_contract": 1.0, "value_add_actual_pnl": 3.0}]
    a = E.build(_rows(reconciliation=rec), bankroll_usd=1.0, bankroll_basis="t", now=NOW)["agent_scoreboard"]["AUDREY"]
    assert a["positions_reconciled"] == 2
    assert a["unexplained_reconciliation_residual_usd"] == pytest.approx(1.0)
    assert a["reconciliation_status"] == "UNRECONCILED"


def test_regimes_abstain_without_settlement_proof_even_when_ev_is_positive():
    seg = [{"sport": "baseball", "family": "MONEYLINE", "regime": "PREGAME", "evaluations": 500,
            "fixtures": 300, "enters": 10, "mean_ev_per_contract": 0.2, "last_evaluated_at": NOW - 60,
            "in_registry": 500, "settlement_proven": 0}]
    models = [{"kind": "CALIBRATION", "payload": json.dumps({"cells": {"baseball|MONEYLINE|PREGAME": {
        "n": 500, "status": "MEASURED", "mean_p": 0.5, "observed": 0.5}}}), "version": "v", "observations": 500,
        "fitted_at": NOW},
              {"kind": "RESIDUAL", "payload": json.dumps({"cells": {"*|MONEYLINE|PREGAME": {
                  "residual_per_contract": 0.01}}}), "version": "v", "observations": 500, "fitted_at": NOW}]
    r = E.build(_rows(segments=seg, models=models), bankroll_usd=1.0, bankroll_basis="t", now=NOW)["regime_matrix"][0]
    assert r["status"] == "ABSTAIN" and r["reason"] == "SETTLEMENT_IDENTITY_NOT_PROVEN"
    seg[0]["settlement_proven"] = 500
    r = E.build(_rows(segments=seg, models=models), bankroll_usd=1.0, bankroll_basis="t", now=NOW)["regime_matrix"][0]
    assert r["status"] == "ELIGIBLE_FOR_EXISTING_GATED_PATH" and r["colour"] == "GREEN"


def test_waterfall_reconciles_and_counterfactuals_are_not_realized():
    out = E.build(_rows(), bankroll_usd=1.0, bankroll_basis="t", now=NOW)
    wf = out["contribution_waterfall"]
    assert abs(wf["identity_check_usd"]) < 1e-9
    assert out["counterfactual_is_never_realized_pnl"] is True
    assert "NOT_REALIZED" in json.dumps(out["agent_scoreboard"]["ARCHER"])


# ── the live read ─────────────────────────────────────────────────────────
class _Tr:
    def __init__(self, log):
        self.log = log

    async def start(self):
        self.log.append("BEGIN")

    async def rollback(self):
        self.log.append("ROLLBACK")

    async def __aenter__(self):
        self.log.append("BEGIN_RW")
        return self

    async def __aexit__(self, *a):
        self.log.append("COMMIT")


class _Conn:
    def __init__(self, missing=()):
        self.log, self.missing, self.sql = [], set(missing), []

    def is_in_transaction(self):
        return False

    def transaction(self, readonly=False):
        self.log.append("readonly" if readonly else "rw")
        return _Tr(self.log)

    async def execute(self, q, *a):
        self.sql.append(q)

    async def fetchval(self, q, *a):
        self.sql.append(q)
        return a[0] not in self.missing if a else None

    async def fetch(self, q, *a):
        self.sql.append(q)
        return []

    async def fetchrow(self, q, *a):
        self.sql.append(q)
        return None


def test_read_runs_in_a_rolled_back_read_only_transaction():
    c = _Conn()
    got = asyncio.run(RD.read(c, account_id="paper_acct_main", now=NOW))
    assert got["status"] == "OK" and c.log[:2] == ["readonly", "BEGIN"] and c.log[-1] == "ROLLBACK"
    assert not any(WRITE.search(q) for q in c.sql)
    assert got["data"]["daily_revenue_readiness"]["overall_status"] == "CASH"
    assert got["data"]["daily_revenue_readiness"]["management_bankroll_usd"] is None


def test_missing_tables_are_unavailable_never_zero():
    got = asyncio.run(RD.read(_Conn(missing={"xavier_value_add"}), account_id="a", now=NOW))
    assert got["status"] == "UNAVAILABLE" and got["missing"] == ["xavier_value_add"]


# ── Audrey's governed proposals ──────────────────────────────────────────
def test_revenue_classes_are_never_pre_authorized_and_fix_their_criteria():
    for cls, _ in RI.CLASSES.values():
        c = IMP.CHANGE_CLASSES[cls]
        assert c.pre_authorized is False and c.evaluator is None
        assert c.success_metrics and c.harm_metrics and c.rollback
        assert not IMP.protected_touched([c.policy_key] if c.policy_key else [])
        assert cls.upper() not in IMP.PROTECTED_CLASSES


def test_propose_due_records_once_per_class_through_the_framework(monkeypatch):
    data = E.build(_rows(positions=_positions(strategy="DEREK_ENTRY_POLICY_V2", actual=-5.0, hold=-6.0,
                                              exit_=-1.0)), bankroll_usd=1.0,
                   bankroll_basis="t", now=NOW)
    assert {w["agent"] for w in data["verified_weaknesses"]} >= {"XAVIER", "DEREK", "KAREN"}

    async def fake_read(conn, **kw):
        return {"status": "OK", "data": data}

    calls, holdouts, existing = [], [], set()

    async def fake_propose(conn, **kw):
        calls.append(kw)
        cid = IMP.candidate_id_for(kw["task_id"], kw["change_class"], kw["variant"])
        existing.add(cid)
        return {"ok": True, "candidate_id": cid}

    async def fake_holdout(conn, **kw):
        holdouts.append(kw)
        return {"exists": True}

    async def fake_candidate(conn, cid):
        return {"candidate_id": cid} if cid in existing else None

    async def yes(conn):
        return True

    monkeypatch.setattr(RD, "read", fake_read)
    monkeypatch.setattr(IMP, "propose", fake_propose)
    monkeypatch.setattr(IMP, "ensure_holdout", fake_holdout)
    monkeypatch.setattr(IMP, "candidate", fake_candidate)
    monkeypatch.setattr(IMP, "has_schema", yes)
    c = _Conn()
    first = asyncio.run(RI.propose_due(c, now=NOW, force=True))
    assert len(first["proposed"]) == 3 and not first["refused"]
    assert all(k["proposed_by"] == "AUDREY" for k in calls)
    assert {k["change_class"] for k in calls} == {"REVENUE_DEREK_SEGMENT_CALIBRATION",
                                                   "REVENUE_XAVIER_MANAGEMENT_POLICY",
                                                   "REVENUE_KAREN_CHALLENGE_DETECTOR"}
    assert all(h["budget"] == RI.HOLDOUT_BUDGET for h in holdouts)
    second = asyncio.run(RI.propose_due(c, now=NOW + 1, force=True))
    assert not second["proposed"] and len(second["existing"]) == 3
    throttled = asyncio.run(RI.propose_due(c, now=NOW + 2))
    assert throttled["skipped"] == "THROTTLED"


def test_the_slow_half_calls_the_proposal_hook():
    src = (PKG / "agents" / "runtime.py").read_text()
    assert '"revenue_improvements",' in src and '"propose_due"' in src
