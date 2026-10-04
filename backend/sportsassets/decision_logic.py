"""THE DECISION-LOGIC IDENTITY OF A BUILD (R30A section 31). Pure: imports
only the standard library, so the execution side (live_parity: the cutover,
the readiness gate) and the read-only profitability validation endpoint
(which imports no execution module) compute it the same way.

  DECISION_LOGIC_FILES     the pinned decision-path source files
  DECISION_LOGIC_ROOTS /   the roots the list is derived from, and the
  NOT_DECISION_LOGIC       imports of theirs that are excused, with reasons
  decision_logic_hash      sha256 over the pinned files of THIS build
  build_logic_check        the serving build's hash against the effective
                           cutover's (CURRENT_BUILD_LOGIC_HAS_NO_CUTOVER)
  serving_build_identity   {decision_logic_hash, release_sha} of this
                           process, stamped on every canonical decision intent
"""
from __future__ import annotations

import hashlib
import os
import pathlib


#: THE DECISION-PATH SOURCE FILES (paths relative to the sportsassets
#: package) whose content IS the decision logic: what decides ENTER and
#: sizes it, what carries the 30 s probability rule and qualifies the
#: probability, what values and manages a position, what decides settlement
#: compatibility, what builds both canonical intents and both adapters'
#: orders, and the live gates. Their combined sha256 (`decision_logic_hash`),
#: computed from the RUNNING build's files, is recorded with every cutover:
#: a release that changes none of them keeps the forward sample; one that
#: changes any restarts it.
#:
#: DERIVED, NOT ONLY HAND-PINNED (R30A review). The first list left out
#: modules the decision reads: bettor_paper_ledger (the ENTER economics'
#: fees, the same-contract refusal), bettor_settlement_terms (the
#: settlement-compatibility refusal), bettor_paper_session (risk caps,
#: target order size, min net EV, the Pinnacle age limit),
#: workers/ext_pinnacle_loop (PINNACLE_MAX_AGE_S = 30.0, the 30 s rule
#: itself), pinnapi_primary (the qualification check) and
#: bettor_xavier_standing_orders. Now every package module a DECISION-PATH
#: ROOT imports (DECISION_LOGIC_ROOTS, read from their source by
#: `decision_logic_imports`) must be either pinned here or named in
#: NOT_DECISION_LOGIC with the reason; tests/test_live_parity.py fails on a
#: new import that is neither.
DECISION_LOGIC_FILES = (
    # this identity itself, the canonical intents, both adapters, parity
    # and the live gates
    "decision_logic.py", "canonical_intent.py", "canonical_components.py", "allie_capital.py",
    "live_parity.py", "live_approvals.py", "live_rule_artifacts.py",
    "decision_hooks.py", "execmirror.py", "execution_intent.py",
    "actual_admission.py", "live_book_currency.py",
    # ENTER, sizing, the paper account's rails and the simulator
    "agents/paper_benchmark.py", "agents/paper_derek.py",
    "agents/derek_policy.py", "bettor_paper_ledger.py",
    "bettor_paper_limits.py", "bettor_paper_session.py",
    "bettor_paper_simulator.py", "bettor_book_snapshot.py",
    "bettor_settlement_terms.py", "bettor_funded_model.py",
    # the probability: its 30 s rule, qualification, de-vig and feed reads
    "workers/ext_pinnacle_loop.py", "pinnapi_primary.py",
    "pinnapi_feed_runtime.py", "pinnapi_held.py", "bettor_pinnacle_devig.py",
    # management
    "agents/paper_xavier.py", "agents/xavier_policy.py",
    "agents/xavier_management.py", "agents/xavier_small_live_policy.py",
    "bettor_xavier_standing_orders.py", "xavier_freshness.py",
    "bettor_funded_decision.py",
    # the agent components inside the intent
    "agents/eddie.py", "lost_opportunity/score.py",
    "lost_opportunity/reads.py", "profitability/economics.py")

#: THE ROOTS whose package imports the test derives the list from: the
#: modules that make the ENTER decision, size it, review a position, and
#: build the intents / adapters' orders.
DECISION_LOGIC_ROOTS = (
    "agents/paper_benchmark.py", "agents/paper_derek.py",
    "agents/paper_xavier.py", "agents/xavier_management.py",
    "canonical_components.py", "live_parity.py")

#: Package modules a root imports that are NOT decision logic, each with the
#: reason (a decision on the record: a new import is neither pinned nor
#: excused until someone says which it is).
NOT_DECISION_LOGIC = {
    "agents/work_queue.py": ("schedules Xavier's fresh-evidence follow-ups "
                             "(WHEN a review runs); the review's action is "
                             "decided in paper_xavier / canonical_intent"),
    "agents/derek_research.py": ("fits research models offline; a model "
                                 "reaches a decision only as a registered "
                                 "row bettor_funded_model (pinned) verifies "
                                 "and scores"),
    "agents/paper_learning.py": ("builds the learning / provenance record of "
                                 "a decision after it is made; decides "
                                 "nothing"),
    "agents/paper_runtime.py": ("orchestrates the passes and reads books for "
                                "the simulator's delayed fill; decides "
                                "nothing"),
    "bettor_external_shadow.py": ("paper_derek reads only its EXPERIMENT_ID "
                                  "constant, a row label"),
    "bettor_paper_guard.py": ("the import guard keeping paper modules away "
                              "from execution; decides nothing"),
}


def decision_logic_imports(root=None) -> dict:
    """{root: sorted package modules it imports} for DECISION_LOGIC_ROOTS,
    read from source (AST; lazy imports inside functions included)."""
    import ast
    base = pathlib.Path(root) if root is not None else \
        pathlib.Path(__file__).resolve().parent
    out = {}
    for rel in DECISION_LOGIC_ROOTS:
        tree = ast.parse((base / rel).read_text())
        pkg = list(pathlib.PurePosixPath(rel).parent.parts)
        found = set()
        for node in ast.walk(tree):
            cands = []
            if isinstance(node, ast.ImportFrom) and node.level:
                up = pkg[:len(pkg) - (node.level - 1)] if node.level > 1 \
                    else pkg
                mod = node.module.split(".") if node.module else []
                if mod:
                    cands.append(up + mod)
                cands += [up + mod + [a.name] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and (
                    node.module or "").startswith("sportsassets."):
                mod = node.module.split(".")[1:]
                cands = [mod] + [mod + [a.name] for a in node.names]
            elif isinstance(node, ast.Import):
                cands = [a.name.split(".")[1:] for a in node.names
                         if a.name.startswith("sportsassets.")]
            for c in cands:
                path = "/".join(c) + ".py"
                if c and (base / path).is_file() and path != rel:
                    found.add(path)
        out[rel] = sorted(found)
    return out


def decision_logic_hash(root=None) -> dict:
    """sha256 over the pinned decision-path files of THIS build (read from
    disk beside this module, i.e. the code the serving process runs).
    {hash, files: {path: sha256}, missing}. A missing file makes the hash
    None: a cutover never records a logic identity it could not compute."""
    base = pathlib.Path(root) if root is not None else \
        pathlib.Path(__file__).resolve().parent
    files, missing = {}, []
    for rel in DECISION_LOGIC_FILES:
        f = base / rel
        try:
            files[rel] = hashlib.sha256(f.read_bytes()).hexdigest()
        except OSError:
            missing.append(rel)
    if missing:
        return {"hash": None, "files": files, "missing": missing}
    h = hashlib.sha256("".join("%s:%s\n" % (k, files[k])
                               for k in sorted(files)).encode()).hexdigest()
    return {"hash": h, "files": files, "missing": []}


_SERVING_LOGIC: dict = {}


def serving_build_identity() -> dict:
    """{decision_logic_hash, release_sha} of THIS process's build, computed
    once per process (the code a process runs is the code it imported) and
    stamped on every canonical decision intent. Never raises."""
    if "v" not in _SERVING_LOGIC:
        try:
            h = decision_logic_hash().get("hash")
        except Exception:                                     # noqa: BLE001
            h = None
        _SERVING_LOGIC["v"] = h
    return {"decision_logic_hash": _SERVING_LOGIC["v"],
            "release_sha": os.environ.get("RENDER_GIT_COMMIT")}


R_LOGIC_HAS_NO_CUTOVER = "CURRENT_BUILD_LOGIC_HAS_NO_CUTOVER"
R_LOGIC_HASH_UNAVAILABLE = "CURRENT_BUILD_LOGIC_HASH_UNAVAILABLE"


def build_logic_check(cutover_hash, *, logic: dict | None = None) -> dict:
    """THE SERVING BUILD'S DECISION LOGIC AGAINST THE EFFECTIVE CUTOVER'S
    (pure but for reading this build's pinned files). R30A review:
    decision_logic_hash() ran only inside the cutover checks, so a release
    that changed decision logic but recorded no cutover went unnoticed and
    the forward window silently spanned two logics. {serving_hash,
    cutover_hash, matches, refusal}: refusal is R_LOGIC_HAS_NO_CUTOVER when
    they differ (or no cutover exists to name it), R_LOGIC_HASH_UNAVAILABLE
    when this build's hash cannot be computed."""
    lg = logic if logic is not None else decision_logic_hash()
    sh = lg.get("hash")
    refusal = (R_LOGIC_HASH_UNAVAILABLE if sh is None
               else None if sh == cutover_hash else R_LOGIC_HAS_NO_CUTOVER)
    return {"serving_hash": sh, "cutover_hash": cutover_hash,
            "missing_files": lg.get("missing") or [],
            "matches": refusal is None, "refusal": refusal,
            "rule": ("the serving build's decision_logic_hash must equal the "
                     "effective cutover's: otherwise the build decides with "
                     "logic no recorded cutover names")}
