"""CAPITAL-CRITICAL: THE R30B AGENTS STREAM'S MODULES HOLD NO AUTHORITY
(migration 301: durable work queues, scorecards, lesson usage, root-cause
clusters).

  §1 agents/agent_work.py (every agent's durable queue) writes ONLY the
     agent_work_* tables, imports no order, execution, funded, venue or paper
     module (its imports are agent_work_state and work_queue, and karen_runner
     lazily for the same-rule re-check), and calls no order path.
  §2 the runner edits are each a single queue sync AFTER the runner's own
     work: Archer's, Scout's, Karen's runner, the peer responder, the paper
     pass (a step, before the memory step which stays last).
  §3 every module this stream adds is checked by the same rules as it lands:
     the root-cause clusters and their read API, lesson usage (§4), and the
     agent scorecards and their read API (SELECT only, READ ONLY, no
     activity counted as a score); the stream's proofs are on the
     capital-critical list.
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
            ("agents/archer_runner.py", "E.record_outcomes(", "archer_runner"),
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


# ═════════════════════════════════════════════════════════════════════
# §3 THE ROOT-CAUSE CLUSTERS AND THEIR READ API
# ═════════════════════════════════════════════════════════════════════

RCC = ROOT / "agents" / "improvement_clusters.py"
RCC_API = ROOT / "api" / "command_improvement_clusters.py"


def test_the_cluster_runner_writes_only_its_own_tables():
    assert _writes(RCC) == {"improvement_clusters",
                            "improvement_cluster_events"}, _writes(RCC)
    for s in _sql(RCC):
        assert not re.search(r"\bUPDATE\s+[a-z_]+\s+SET|\bDELETE\s+FROM|"
                             r"TRUNCATE|ALTER\s|DROP\s", s, re.I), s[:120]
    # the runner's own rows are OPENED / EFFECT_MEASURED; the human steps
    # are separate functions it never calls
    src = RCC.read_text()
    runner = src[src.index("async def refresh("):
                 src.index("# THE HUMAN STEPS")]
    for human in ("link_fix(", "assign_owner(", "close(", "reopen(",
                  "'FIX_LINKED'", "'CLOSED'", "'OWNER_ASSIGNED'"):
        assert human not in runner, human


def test_the_cluster_modules_import_no_order_execution_or_paper_module():
    for path, pkg, allowed in (
            (RCC, "sportsassets.agents",
             ("sportsassets.agents.karen_runner",)),
            # (R30B review) the human steps' admin route reads the admin
            # token check from api.app, as the clear-halt route does
            (RCC_API, "sportsassets.api",
             ("sportsassets.api.agents_core", "sportsassets.api.app",
              "sportsassets.agents.improvement_clusters"))):
        for imp in _imports(path, pkg):
            if imp.startswith("sportsassets"):
                assert any(imp == a or imp.startswith(a + ".")
                           for a in allowed), (path.name, imp)
            leaf = imp.rsplit(".", 1)[-1].lower()
            assert not any(f in leaf for f in FORBIDDEN), (path.name, imp)
        for word in ORDER_CALLS:
            assert word not in path.read_text(), (path.name, word)


def test_the_cluster_read_api_writes_nothing():
    for s in _sql(RCC_API):
        assert not re.search(r"\b(INSERT\s+INTO|UPDATE\s+[a-z_]+\s+SET|"
                             r"DELETE\s+FROM)", s, re.I), s[:120]
    assert "transaction(readonly=" in RCC_API.read_text()
    # THE HUMAN STEPS (R30B review): one admin-token POST that writes only
    # through the cluster module's own human-step functions; the GET routes
    # stay read only
    src = RCC_API.read_text()
    post = src[src.index("async def apply("):]
    assert set(re.findall(r"IC\.([a-z_]+)\(", post)) == {
        "link_fix", "assign_owner", "close", "reopen"}
    assert src.count("@router.post(") == 1
    assert "dependencies=[Depends(_require_admin)]" in src


# ═════════════════════════════════════════════════════════════════════
# §4 MEMORY USEFULNESS
# ═════════════════════════════════════════════════════════════════════

LU = ROOT / "agents" / "lesson_usage.py"


def test_lesson_usage_writes_only_its_own_records():
    assert _writes(LU) == {"agent_lesson_retrievals",
                           "agent_lesson_supersessions"}, _writes(LU)
    for s in _sql(LU):
        assert not re.search(r"\bUPDATE\s+[a-z_]+\s+SET|\bDELETE\s+FROM|"
                             r"TRUNCATE|ALTER\s|DROP\s", s, re.I), s[:120]


def test_lesson_usage_imports_no_order_execution_or_paper_module():
    allowed = ("sportsassets.api.command_validation",
               "sportsassets.profitability.validation")
    for imp in _imports(LU, "sportsassets.agents"):
        if imp.startswith("sportsassets"):
            assert any(imp == a or imp.startswith(a + ".")
                       for a in allowed), imp
        leaf = imp.rsplit(".", 1)[-1].lower()
        assert not any(f in leaf for f in FORBIDDEN), imp
    for word in ORDER_CALLS:
        assert word not in LU.read_text(), word


def test_memory_never_grants_authority_and_the_scorecards_only_read():
    """(Scorecards, section 18.) The card module and its read API SELECT
    only, run in a READ ONLY transaction, import nothing outside the queue
    helpers and Karen's rule table, and count no activity as a score."""
    SC = ROOT / "agents" / "agent_scorecards.py"
    SC_API = ROOT / "api" / "command_agent_scorecards.py"
    for path in (SC, SC_API):
        assert not _writes(path), (path.name, _writes(path))
        for s in _sql(path):
            assert not re.search(r"\b(INSERT\s+INTO|UPDATE\s+[a-z_]+\s+SET|"
                                 r"DELETE\s+FROM|TRUNCATE|ALTER\s|DROP\s)",
                                 s, re.I), (path.name, s[:120])
        for word in ORDER_CALLS:
            assert word not in path.read_text(), (path.name, word)
    for path, pkg, allowed in (
            (SC, "sportsassets.agents",
             ("sportsassets.agent_work_state",
              "sportsassets.agents.karen_runner")),
            (SC_API, "sportsassets.api",
             ("sportsassets.api.agents_core",
              "sportsassets.agents.agent_scorecards"))):
        for imp in _imports(path, pkg):
            if imp.startswith("sportsassets"):
                assert any(imp == a or imp.startswith(a + ".")
                           for a in allowed), (path.name, imp)
            leaf = imp.rsplit(".", 1)[-1].lower()
            assert not any(f in leaf for f in FORBIDDEN), (path.name, imp)
    assert "transaction(readonly=" in SC_API.read_text()
    from sportsassets.agents import agent_scorecards as S
    assert S.METRICS == (
        "decision_latency", "evidence_completeness", "citation_correctness",
        "calibration", "false_approval", "false_refusal", "value_added",
        "challenge_quality", "freshness_compliance",
        "unresolved_blocker_age")
    assert not any(S.NOT_A_SCORE.search(m) for m in S.METRICS)
    test_memory_never_grants_authority()


def test_the_stream_proofs_are_capital_critical():
    listed = (ROOT.parent / "tools" / "capital_critical_tests.txt"
              ).read_text().splitlines()
    for name in ("test_agent_operations_authority.py",
                 "test_agent_work_queues.py", "test_agent_work_state.py",
                 "test_improvement_clusters.py", "test_lesson_usage.py",
                 "test_agent_scorecards.py"):
        assert "tests/%s" % name in listed, name


def test_memory_never_grants_authority():
    """Only the evaluator version decides; a weight only falls; no module
    outside lesson_usage / the scorecards / the two context readers reads a
    lesson weight -- and those readers only SELECT it.

    WHY THE READER SET WIDENED (R30B review): a supersession had changed
    nothing an agent actually reads. The conversation context
    (agents/learning_context.py) and the agent's own context bundle
    (agents/agent_memory.private_memories, used by agent_context) now read
    the weight to EXCLUDE a superseded lesson and rank a downweighted one
    lower. Neither is on an order, capital or approval path (learning
    context: OBSERVATIONS_ONLY_NO_POLICY_CHANGE; agent memory: the
    identity authority tests)."""
    src = LU.read_text()
    assert 'VERSION = "MEMORY_USEFULNESS_V1"' in src
    assert "UPWEIGHT" not in src and "PROMOTE" not in src
    readers = []
    for p in ROOT.rglob("*.py"):
        t = p.read_text()
        if "agent_lesson_supersessions" in t or "agent_lesson_retrievals" in t:
            readers.append(str(p.relative_to(ROOT)))
    context_readers = {"agents/learning_context.py",
                       "agents/agent_memory.py"}
    assert set(readers) <= {"agents/lesson_usage.py",
                            "agents/agent_scorecards.py",
                            "api/command_agent_scorecards.py"} | \
        context_readers, readers
    for rel in context_readers:
        path = ROOT / rel
        for q in _sql(path):
            if "agent_lesson_supersessions" in q:
                assert not re.search(r"\b(INSERT\s+INTO|UPDATE\s+[a-z_]+"
                                     r"\s+SET|DELETE\s+FROM)", q, re.I), rel
        assert "agent_lesson_retrievals" not in path.read_text(), rel


# ── (R30 tails integration) the current rails win over the stream's copies ──

MIGS = ROOT.parent / "migrations"


def _fn_body(path, name) -> str:
    s = path.read_text()
    i = s.index("AS $$", s.index("FUNCTION " + name))
    return re.sub(r"\s+", " ", s[i:s.index("$$;", i)])


def _registry(path) -> dict:
    s = path.read_text()
    i = s.index("FUNCTION pos_agents_authority_guarded_tables")
    return dict(re.findall(r"\('([a-z_]+)',\s+ARRAY\[([^\]]*)\]",
                           s[i:s.index("$$;", i)]))


def test_301_machine_actor_test_is_the_current_one_adriana_included():
    """R30B copied 221's machine-actor list before migration 265 added
    ADRIANA; the copy in 301 is the CURRENT definition, so ADRIANA (or
    'adriana-arb') can never stand as the named person who links a fix,
    assigns an owner or decides a supersession."""
    cur = _fn_body(MIGS / "265_adriana_arbitrage_agent.sql",
                   "improve_is_machine_actor")
    ours = _fn_body(MIGS / "301_agent_operations.sql",
                    "agent_ops_is_machine_actor")
    assert ours == cur
    assert "'ADRIANA'" in ours and "|ADRIANA)" in ours


def test_301_and_its_rollback_keep_every_registry_row_of_the_current_225():
    """R30B re-declared the authority-guard registry from 225 before R30A
    added live_parity_cutover / live_approvals; 301 and its rollback keep
    every current 225 row with the same columns (dropping either would take
    the no-authority registry off the owner's live approvals)."""
    cur = _registry(MIGS / "225_live_parity.sql")
    for f in (MIGS / "301_agent_operations.sql",
              MIGS / "rollback" / "301_agent_operations.down.sql"):
        got = _registry(f)
        assert {k: got.get(k) for k in cur} == cur, f.name
    assert {"live_parity_cutover", "live_approvals"} <= set(cur)
