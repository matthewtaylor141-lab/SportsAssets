"""CAPITAL-CRITICAL: THE R30B AGENTS STREAM'S MODULES HOLD NO AUTHORITY
(migration 234: durable work queues, scorecards, lesson usage, root-cause
clusters).

  §1 agents/agent_work.py (every agent's durable queue) writes ONLY the
     agent_work_* tables, imports no order, execution, funded, venue or paper
     module (its imports are agent_work_state and work_queue, and karen_runner
     lazily for the same-rule re-check), and calls no order path.
  §2 the runner edits are each a single queue sync AFTER the runner's own
     work: Eddie's, Scout's, Karen's runner, the peer responder, the paper
     pass (a step, before the memory step which stays last).
  §3 every module this stream adds is checked by the same rules as it lands
     (the list below grows with the stream).
"""
from __future__ import annotations

import ast
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1] / "sportsassets"
FORBIDDEN = ("execmirror", "kalshi", "pmus", "clob", "executor", "execution",
             "bettor_funded", "funded", "smalllive", "submit", "live_",
             "actual_admission", "slack_bridge", "xavier_policy",
             "derek_policy", "order", "paper", "pinnapi", "venue")
ORDER_CALLS = ("submit_order", "request_cancel", "place_order",
               "release_remainder", "L.settle", "act_as(")


def _imports(path: pathlib.Path, pkg: str) -> set:
    out = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = pkg.split(".")
                base = base[:len(base) - (node.level - 1)]
                mod = ".".join(base + ([node.module] if node.module else []))
            else:
                mod = node.module
            out.update("%s.%s" % (mod, a.name) for a in node.names)
    return out


def _sql(path: pathlib.Path) -> list:
    return [n.value for n in ast.walk(ast.parse(path.read_text()))
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and re.search(r"\b(SELECT|INSERT|UPDATE|DELETE)\b", n.value)]


def _writes(path: pathlib.Path) -> set:
    return set(re.findall(r"INSERT\s+INTO\s+([a-z_]+)", path.read_text(),
                          re.I))


# ═════════════════════════════════════════════════════════════════════
# §1 THE DURABLE QUEUE
# ═════════════════════════════════════════════════════════════════════

AW = ROOT / "agents" / "agent_work.py"


def test_the_queue_writes_only_the_queue_tables():
    assert _writes(AW) == {"agent_work_requests", "agent_work_open",
                           "agent_work_request_events"}, _writes(AW)
    for s in _sql(AW):
        assert not re.search(r"\bUPDATE\s+[a-z_]+\s+SET|\bDELETE\s+FROM|"
                             r"TRUNCATE|ALTER\s|DROP\s", s, re.I), s[:120]


def test_the_queue_imports_no_order_execution_or_paper_module():
    imps = _imports(AW, "sportsassets.agents")
    allowed = ("sportsassets.agent_work_state",
               "sportsassets.agents.work_queue",
               "sportsassets.agents.karen_runner")
    for imp in imps:
        if imp.startswith("sportsassets"):
            assert any(imp == a or imp.startswith(a + ".") for a in allowed), \
                imp
        leaf = imp.rsplit(".", 1)[-1].lower()
        assert not any(f in leaf for f in FORBIDDEN), imp
    text = AW.read_text()
    for word in ORDER_CALLS:
        assert word not in text, word


# ═════════════════════════════════════════════════════════════════════
# §2 THE RUNNER EDITS
# ═════════════════════════════════════════════════════════════════════

def test_each_runner_syncs_its_queue_once_after_its_own_work():
    for rel, after, runner in (
            ("agents/eddie_runner.py", "E.record_outcomes(", "eddie_runner"),
            ("agents/scout_runner.py", "open_feature_findings(conn",
             "scout_runner"),
            ("agents/karen_runner.py", "_open_candidate(conn, c, at)",
             "karen_runner"),
            ("agents/peer_responder.py", "evaluate_as(conn, ev",
             "peer_responder")):
        src = (ROOT / rel).read_text()
        call = 'AW.sync_for(\n' if 'AW.sync_for(\n' in src else "AW.sync_for("
        assert src.count("AW.sync_for(") == 1, rel
        assert src.index(after) < src.index("AW.sync_for("), rel
        assert '"%s"' % runner in src[src.index("AW.sync_for("):][:200], rel
        assert call


def test_the_paper_pass_step_runs_before_the_memory_step():
    from sportsassets.agents import paper_runtime as PR
    names = [n for n, _ in PR.default_steps()]
    assert "agent_work_queues" in names
    assert names[-1] == "agent_memory"
    assert names.index("agent_work_queues") < names.index("agent_memory")
