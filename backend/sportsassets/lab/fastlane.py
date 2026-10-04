"""FAST-LANE SHADOW DESIGN (LAB-A): which deterministic pre-trade analyses
can evaluate the SAME immutable candidate snapshot concurrently, with the
canonical decision still WAITING FOR EVERY REQUIRED OUTPUT. A design and a
tournament specification only -- nothing here is on, or imported by, a
decision path; nothing here changes a gate, a threshold or an evidence
requirement ("Do not respond by weakening evidence requirements").

THE DEPENDENCY GRAPH is DERIVED, not asserted: `derive_graph` parses the
source text of `canonical_components.at_decision` (the components every
canonical decision intent carries) and `live_parity.canonical_decision` (the
hook that builds the intent) with `ast` -- without importing either (the
latter is an execution module) -- and records, for every awaited component
call, which earlier component outputs reach its arguments. The `decision`
dict is tracked KEY BY KEY: the event-start step writes only the keys it
names in `dict(decision, k=...)`, so a component that reads other keys with
`decision.get("k")` does not depend on it, while a component handed the
whole dict does (conservatively).

THE CONCURRENCY ESTIMATE: sequential latency = the sum of the component
latencies (today's order); concurrent latency = the longest weighted path
through the derived DAG. Two real constraints are stated, not assumed away:
(1) one asyncpg connection runs one query at a time, so concurrent
components each need their own pooled connection; (2) "the same immutable
snapshot" then needs ONE exported database snapshot (pg_export_snapshot /
SET TRANSACTION SNAPSHOT under REPEATABLE READ) shared by those
connections, or the components could read different database states.

THE URGENCY CLASSES are proposed from MEASURED edge persistence and
evidence life (sportsassets.lab.edge_decay.summarize) for a SHADOW
tournament; they choose WHEN components run, never WHETHER a gate applies.
"""
from __future__ import annotations

import ast
import pathlib

from . import stats as ST

VERSION = "LAB_FASTLANE_SHADOW_DESIGN_V1"
AUTHORITY = "SHADOW_RESEARCH_ONLY"
ROOT = pathlib.Path(__file__).resolve().parents[1]
#: the deterministic analyses the owner names (Karen, Allie, Eddie) and the
#: other component the intent carries, by their at_decision call
COMPONENT_OF = {"eddie_at_decision": "eddie",
                "opportunity_at_decision": "opportunity_score",
                "karen_at_decision": "karen",
                "allie_at_decision": "allie",
                "_bounded": "event_start"}


def _names(node) -> set:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _key_reads(node, var: str) -> tuple:
    """(keys read from `var` with .get("k") / ["k"], whether `var` is also
    used as a whole object)."""
    keys, whole = set(), False
    parents = {}
    for p in ast.walk(node):
        for c in ast.iter_child_nodes(p):
            parents[c] = p
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and n.id == var:
            p = parents.get(n)
            if isinstance(p, ast.Attribute) and p.attr == "get":
                call = parents.get(p)
                if isinstance(call, ast.Call) and call.args and isinstance(
                        call.args[0], ast.Constant):
                    keys.add(str(call.args[0].value))
                    continue
            if isinstance(p, ast.Subscript) and isinstance(
                    getattr(p, "slice", None), ast.Constant):
                keys.add(str(p.slice.value))
                continue
            whole = True
    return keys, whole


def _call_name(call) -> str | None:
    f = call.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def _function(tree, name: str):
    for n in ast.walk(tree):
        if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and \
                n.name == name:
            return n
    return None


def derive_graph(source: str | None = None) -> dict:
    """THE COMPONENT DEPENDENCY GRAPH of canonical_components.at_decision,
    derived from its source text."""
    src = source if source is not None else (
        ROOT / "canonical_components.py").read_text()
    fn = _function(ast.parse(src), "at_decision")
    if fn is None:
        return {"status": "UNAVAILABLE", "why": "AT_DECISION_NOT_FOUND"}
    producers: dict = {}            # name -> set(component nodes)
    key_writers: dict = {}          # (var, key) -> set(component nodes)
    nodes, edges, order = {}, {}, []

    def deps_of(expr) -> set:
        out = set()
        for nm in _names(expr):
            if nm in producers:
                keys, whole = _key_reads(expr, nm)
                kw = {k: v for (var, k), v in key_writers.items()
                      if var == nm}
                if whole or not kw:
                    out |= producers[nm]
                else:
                    for k in keys:
                        out |= kw.get(k, set())
        return out

    def visit(stmts):
        for st in stmts:
            if isinstance(st, ast.Assign) and len(st.targets) == 1 and \
                    isinstance(st.targets[0], ast.Name):
                tgt = st.targets[0].id
                aw = next((n for n in ast.walk(st.value)
                           if isinstance(n, ast.Await)), None)
                if aw is not None and isinstance(aw.value, ast.Call):
                    cname = _call_name(aw.value)
                    comp = COMPONENT_OF.get(cname, cname)
                    d = deps_of(aw.value)
                    nodes[tgt] = {"call": cname, "component": comp,
                                  "line": st.lineno}
                    edges[tgt] = sorted(d)
                    order.append(tgt)
                    producers[tgt] = {tgt}
                else:
                    d = deps_of(st.value)
                    # dict(var, k=...) rewrites var: its new keys' writers
                    v = st.value
                    if isinstance(v, ast.Call) and _call_name(v) == "dict" \
                            and v.args and isinstance(v.args[0], ast.Name) \
                            and v.args[0].id == tgt:
                        for kw in v.keywords:
                            if kw.arg:
                                key_writers[(tgt, kw.arg)] = deps_of(kw.value)
                        producers[tgt] = producers.get(tgt, set()) | d
                    else:
                        producers[tgt] = d
            elif isinstance(st, ast.If):
                visit(st.body)
                visit(st.orelse)
            elif isinstance(st, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
    visit(fn.body)
    comp_edges = {nodes[t]["component"]: sorted(nodes[s]["component"]
                                                for s in edges[t])
                  for t in order}
    return {"status": "DERIVED", "source": "canonical_components.at_decision",
            "nodes": {nodes[t]["component"]: dict(nodes[t], variable=t)
                      for t in order},
            "depends_on": comp_edges, "sequential_order": [
                nodes[t]["component"] for t in order],
            "layers": layers(comp_edges),
            "rule": ("an edge means a component's call arguments carry an "
                     "earlier component's output (the decision dict tracked "
                     "key by key)")}


def layers(dep: dict) -> list:
    """Topological layers: every component of a layer depends only on
    earlier layers, so a layer's members can run concurrently."""
    left = {k: set(v) for k, v in dep.items()}
    done, out = set(), []
    while left:
        ready = sorted(k for k, v in left.items() if v <= done)
        if not ready:
            return out + [{"cycle": sorted(left)}]
        out.append(ready)
        done |= set(ready)
        for k in ready:
            left.pop(k)
    return out


def intent_assembly(source: str | None = None) -> dict:
    """WHAT THE CANONICAL INTENT WAITS FOR: the components
    live_parity.canonical_decision passes to build_decision_intent (parsed,
    not imported)."""
    src = source if source is not None else (
        ROOT / "live_parity.py").read_text()
    fn = _function(ast.parse(src), "canonical_decision")
    if fn is None:
        return {"status": "UNAVAILABLE", "why": "CANONICAL_DECISION_NOT_FOUND"}
    need = []
    for n in ast.walk(fn):
        if isinstance(n, ast.Call) and _call_name(n) == "build_decision_intent":
            for kw in n.keywords:
                if kw.arg in ("opportunity_score", "derek", "karen", "allie",
                              "eddie"):
                    need.append(kw.arg)
    awaited = sorted({_call_name(n.value) for n in ast.walk(fn)
                      if isinstance(n, ast.Await) and isinstance(
                          n.value, ast.Call)})
    return {"status": "DERIVED", "source": "live_parity.canonical_decision",
            "intent_waits_for": sorted(need), "awaited_calls": awaited,
            "rule": ("the intent is built only after every component it "
                     "carries has returned (MEASURED or with its UNAVAILABLE "
                     "reason); a fast lane may only change WHEN they run")}


def critical_path(dep: dict, latency: dict) -> dict:
    """Longest weighted path (seconds) through the DAG; components without a
    measured latency make the estimate UNAVAILABLE."""
    miss = sorted(k for k in dep if latency.get(k) is None)
    if miss:
        return {"status": "UNAVAILABLE", "why": "LATENCY_NOT_MEASURED:%s"
                % ",".join(miss)}
    finish: dict = {}
    for layer in layers(dep):
        if isinstance(layer, dict):
            return {"status": "UNAVAILABLE", "why": "DEPENDENCY_CYCLE"}
        for k in layer:
            finish[k] = float(latency[k]) + max(
                [finish[d] for d in dep[k]] or [0.0])
    seq = sum(float(latency[k]) for k in dep)
    conc = max(finish.values()) if finish else 0.0
    return {"status": "ESTIMATED", "sequential_s": round(seq, 6),
            "concurrent_s": round(conc, 6),
            "saving_s": round(seq - conc, 6),
            "finish_s": {k: round(v, 6) for k, v in finish.items()},
            "constraints": [
                "each concurrent component needs its own pooled connection "
                "(one asyncpg connection runs one query at a time)",
                "the connections must share ONE exported snapshot "
                "(REPEATABLE READ + SET TRANSACTION SNAPSHOT) so every "
                "component reads the same immutable database state",
                "the canonical intent is still built only after ALL "
                "components return; a component that times out carries its "
                "UNAVAILABLE reason exactly as today"]}


def component_latency_summary(samples: dict) -> dict:
    """{component: [seconds...]} -> per component n / p50 / p90 / max."""
    out = {}
    for k, xs in samples.items():
        v = [float(x) for x in xs if x is not None]
        out[k] = {"n": len(v), "p50_s": ST.quantile(v, 0.5),
                  "p90_s": ST.quantile(v, 0.9),
                  "max_s": None if not v else round(max(v), 6)}
    return out


def urgency_classes(summary: dict, *, sequential_s: float | None,
                    concurrent_s: float | None) -> dict:
    """CANDIDATE FAST-LANE URGENCY CLASSES for a SHADOW tournament, from the
    measured evidence life and edge persistence (edge_decay.summarize). A
    class chooses WHEN the deterministic components run (concurrently on one
    exported snapshot vs in today's order); it never removes a component,
    a gate or an evidence requirement."""
    rf = (summary.get("remaining_freshness_at_detection_s") or {})
    hl = ((summary.get("decay") or {}).get("TOP_NET_EDGE") or {}).get(
        "half_life") or {}
    vhl = ((summary.get("decay") or {}).get("VENUE_BOOK_AT_DETECTION_P")
           or {}).get("half_life") or {}
    budget = None if sequential_s is None else float(sequential_s)
    rules = [
        {"class": "U1_EVIDENCE_BOUND",
         "rule": ("remaining probability freshness at detection < "
                  "max(4 x sequential component latency, 5 s): the 30 s rule "
                  "will expire the candidate before a slow pipeline "
                  "finishes, whatever the book does"),
         "lane": "CONCURRENT_COMPONENTS_ON_ONE_SNAPSHOT",
         "evidence": {"remaining_freshness_p25_s": rf.get("p25"),
                      "remaining_freshness_p50_s": rf.get("p50"),
                      "sequential_component_s": budget}},
        {"class": "U2_FAST_DECAY",
         "rule": ("the candidate's segment has a measured executable "
                  "half-life (KM p25) below 30 s, or its venue-book "
                  "half-life p25 below 60 s"),
         "lane": "CONCURRENT_COMPONENTS_ON_ONE_SNAPSHOT",
         "evidence": {"half_life_km_p25_s": hl.get("p25"),
                      "half_life_km_p50_s": hl.get("p50"),
                      "half_life_n": hl.get("n"),
                      "venue_half_life_km_p25_s": vhl.get("p25"),
                      "venue_half_life_n": vhl.get("n")}},
        {"class": "U3_NORMAL", "rule": "everything else",
         "lane": "SEQUENTIAL_AS_TODAY"}]
    return {"version": VERSION, "authority": AUTHORITY,
            "classes": rules,
            "estimated_component_latency": {"sequential_s": sequential_s,
                                            "concurrent_s": concurrent_s},
            "invariants": [
                "every arm builds the intent only after ALL required "
                "components return (MEASURED or UNAVAILABLE with a reason)",
                "every arm applies every gate, threshold, freshness rule "
                "and evidence requirement unchanged",
                "for the same snapshot the concurrent arm's component "
                "outputs must EQUAL the sequential arm's (content sha "
                "excluding stage stamps); any inequality fails the arm"]}


def tournament_spec() -> dict:
    """THE SHADOW TOURNAMENT the classes enter (no arm can act)."""
    return {"version": VERSION, "authority": AUTHORITY, "mode": "SHADOW",
            "arms": [
                {"arm": "A0_SEQUENTIAL_CONTROL",
                 "what": "today's canonical_components order"},
                {"arm": "A1_CONCURRENT_ALL",
                 "what": ("every qualified candidate: event start, then "
                          "{eddie, karen} concurrently, then {opportunity, "
                          "allie} concurrently, on one exported snapshot")},
                {"arm": "A2_CONCURRENT_URGENT_ONLY",
                 "what": "A1 for U1/U2 candidates, A0 otherwise"}],
            "unit": "one qualified candidate evaluated by all arms on the "
                    "SAME immutable snapshot (paired design)",
            "metrics": [
                "component wall latency and decision -> intent latency",
                "remaining probability freshness at intent completion",
                "decided-order EV at the first recorded book after intent "
                "completion (lab edge-decay measure), per arm",
                "output equality across arms (must be 100%)"],
            "promotion": ("none automatic: a SHADOW result is evidence for a "
                          "separate owner decision"),
            "stopping": ("paired sign test on latency per candidate, locked "
                         "checkpoints at 50 / 100 / 200 paired candidates, "
                         "alpha spent 0.01 / 0.015 / 0.025 (sequential-valid)")}
