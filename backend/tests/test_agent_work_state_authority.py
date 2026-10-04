"""CAPITAL-CRITICAL: THE WORK STATES READ, THE WORK QUEUE WRITES ONLY ITS
OWN RECORDS (owner R30, migration 226).

  §1 sportsassets/agent_work_state.py (read by the floor and the identity
     API) imports only the standard library and xavier_freshness, and its
     SQL is SELECT only -- the same rules the floor itself is held to.
  §2 sportsassets/agents/work_queue.py writes only agent_work_requests,
     agent_work_request_events and agent_work_open; it imports no order,
     execution, funded, venue-mutation or submit module (its lazy imports
     are the paper ledger's position read, the fixture store's read, the
     reactive scheduler's request and the paper runtime's book read / held
     review), and nothing in it sizes, places or cancels an order.
  §3 the paper_xavier edit is the single after_review call, after the
     review is written.
"""
from __future__ import annotations

import ast
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1] / "sportsassets"
AWS = ROOT / "agent_work_state.py"
WQ = ROOT / "agents" / "work_queue.py"
STDLIB = {"__future__", "json", "time", "datetime", "typing", "hashlib",
          "logging"}
WRITE = re.compile(r"\b(INSERT\s+INTO|UPDATE\s+[a-z_]+\s+SET|DELETE\s+FROM|"
                   r"TRUNCATE|ALTER\s+|DROP\s+|CREATE\s+|COPY\s+|GRANT\s+|"
                   r"SET\s+ROLE|SET\s+SESSION)", re.I)
FORBIDDEN = ("execmirror", "kalshi", "pmus", "clob", "executor", "execution",
             "bettor_funded", "funded", "smalllive", "submit", "live_",
             "actual_admission", "slack_bridge", "xavier_policy",
             "derek_policy", "order")


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


def test_the_work_state_module_imports_nothing_with_authority():
    for imp in _imports(AWS, "sportsassets"):
        top = imp.split(".")[0]
        if top == "sportsassets":
            assert imp.startswith("sportsassets.xavier_freshness"), imp
        else:
            assert top in STDLIB, imp


def test_the_work_state_sql_is_select_only():
    sqls = _sql(AWS)
    assert len(sqls) >= 10
    for s in sqls:
        assert not WRITE.search(s), s[:120]


def test_the_work_queue_writes_only_its_own_tables():
    text = WQ.read_text()
    targets = set(re.findall(r"INSERT\s+INTO\s+([a-z_]+)", text, re.I))
    assert targets == {"agent_work_requests", "agent_work_request_events",
                       "agent_work_open"}, targets
    for s in _sql(WQ):
        assert not re.search(r"\bUPDATE\s+[a-z_]+\s+SET|\bDELETE\s+FROM|"
                             r"TRUNCATE|ALTER\s|DROP\s", s, re.I), s[:120]


def test_the_work_queue_imports_no_order_or_execution_module():
    imps = _imports(WQ, "sportsassets.agents")
    for imp in imps:
        assert not any(f in imp.lower() for f in FORBIDDEN), imp
    allowed = ("sportsassets.xavier_freshness",
               "sportsassets.bettor_paper_ledger",
               "sportsassets.bettor_fixture_store",
               "sportsassets.pinnapi_reactive",
               "sportsassets.agents.paper_runtime")
    for imp in imps:
        if imp.startswith("sportsassets"):
            assert any(imp == a or imp.startswith(a + ".")
                       for a in allowed), imp
    # nothing in it calls an order path
    for word in ("submit_order", "request_cancel", "place_order",
                 "release_remainder", "L.settle"):
        assert word not in WQ.read_text(), word


def test_paper_xavier_calls_the_queue_once_after_the_review_insert():
    src = (ROOT / "agents" / "paper_xavier.py").read_text()
    assert src.count("WQ.after_review(") == 1
    assert src.index("INSERT INTO paper_xavier_reviews") < \
        src.index("WQ.after_review(")
