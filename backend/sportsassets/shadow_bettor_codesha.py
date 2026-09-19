"""THE V2 CODE BOUNDARY: BETTOR's decision path, parsed, not its bytes.

Owner directive 2026-09-19 20:2xZ: "For V2 only, replace the overly
broad raw-byte boundary with a canonical load-bearing BETTOR
decision-code boundary."

WHY V1's BOUNDARY FAILED, precisely. V1 hashes the whole bytes of four
files. On 2026-09-19 that hash moved between the 19:33 freeze and the
19:58 build, and the diff was 16 added lines of which 15 were COMMENTS
and one was a real change. A boundary that a comment edit can break is
a boundary nobody can enforce, which is exactly why V1's code hash was
recorded rather than enforced -- and that is how a real change reached
production behind a warning nobody acted on.

Two defects, both fixed here for V2 and NEITHER applied retroactively:

  TOO COARSE IN CONTENT. Bytes include comments, docstrings, blank
      lines and formatting. This hashes the PARSED tree with docstrings
      stripped, so none of those can reach the digest. The parser never
      retains a comment at all.

  TOO COARSE IN SCOPE. Two of V1's four files, shadow.py and
      shadow_lanes.py, are SHARED WITH RN1. An RN1-only edit there
      would move BETTOR's code hash although BETTOR's decision could
      not change. This names the specific symbols BETTOR's decision
      actually depends on and ignores the rest of those files --
      including rn1_lane_decision, which is RN1's by definition.

WHAT IS IN THE BOUNDARY, and why each one. Every symbol below is
reachable from `decide()` and can change what a decision SAYS or what
it DOES. A change to any of them must move the hash; a change to
anything else in those files must not.

WHAT IS DELIBERATELY OUT. Opportunity collection (`opportunity_record`,
`record_opportunity`, `universe`) shapes evidence but decides nothing,
and it must keep running when decision writing is blocked -- see the
fail-closed rule, which stops decisions and never stops observation.
Holding it inside the decision boundary would couple the two exactly
where the directive requires them separated.

A MISSING SYMBOL IS AN ERROR, NOT A SHORTER HASH. V1 returned the word
NOT_IDENTIFIED when a file could not be read, which was safe only
because nothing enforced it. Here a digest is enforced, so a boundary
that silently lost a symbol would compare unequal for a reason nobody
could reconstruct -- or worse, equal. Missing symbols raise.
"""

from __future__ import annotations

import ast
import hashlib
import os

# ── the manifest ─────────────────────────────────────────────────────
#
# module -> the top-level names BETTOR's DECISION depends on.

DECISION_PATH = {
    # BETTOR's own decision module. The version strings are in the
    # boundary on purpose: a decision that claims a different policy
    # version is a different decision.
    "shadow_bettor.py": (
        "MODEL_VERSION", "POLICY_VERSION",
        # every blocker code, and the tuple that fixes the vocabulary
        "B_EV_NOT_ESTABLISHED", "B_NO_FAIR_VALUE", "B_NO_RELATIVE_VALUE",
        "B_LATENCY_DESTROYED_EDGE", "B_SPREAD_TOO_WIDE",
        "B_INSUFFICIENT_DEPTH", "B_TOXICITY", "B_P_FILL_NOT_IDENTIFIED",
        "B_ACTION_EV_BELOW_THRESHOLD", "B_OUT_OF_DISTRIBUTION",
        "B_RISK_GATE", "B_MARKET_STATE_UNREADABLE", "BLOCKERS",
        "NOT_IDENTIFIED",
        # the decision itself, what blocks it, and how its id is formed
        "_id", "blockers_for", "decide",
    ),
    # SHARED WITH RN1 -- so named symbols only. rn1_lane_decision and
    # every RN1 provenance constant are absent by design.
    "shadow_lanes.py": (
        "BETTOR_EV_SHADOW", "SIGNAL_BETTOR_INDEPENDENT",
        "NOT_ESTABLISHED", "NOT_IDENTIFIED", "ESTABLISHED",
        "REASON_EV_NOT_ESTABLISHED",
        # lineage is load-bearing: it decides whether the decision may
        # be produced at all, and the directive lists LINEAGE_CHANGE
        # among the mutations that must move the hash.
        "LineageViolation", "LineageAmbiguous", "LaneViolation",
        "INDEPENDENT_PROVENANCES", "DECLARED_PROVENANCES",
        "assert_lineage", "bettor_lane_decision", "not_yet_eligible",
    ),
    # SHARED WITH RN1 -- the record builder and the safety constants
    # every decision carries.
    "shadow.py": (
        "SHADOW_MODE", "REAL_ORDER_SUBMISSION_ENABLED", "CAPITAL_AT_RISK",
        "DISCLOSURE", "NO_TRADE", "HOLD", "BUY", "SELL", "ACTIONS",
        "NOT_IDENTIFIED", "REQUIRED_DECISION_FIELDS", "decision_record",
        "ShadowRefusal",
    ),
}

BOUNDARY_VERSION = "BETTOR_DECISION_PATH_V1"


class BoundaryIncomplete(Exception):
    """A named symbol was not found. Never degrade to a partial hash."""


def _strip_docstring(node):
    body = getattr(node, "body", None)
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        node.body = body[1:] or [ast.Pass()]


def _named(tree: ast.Module, wanted: tuple) -> dict:
    """The top-level definitions for `wanted`, docstrings stripped."""
    found = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            if node.name in wanted:
                found[node.name] = node
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in wanted:
                    found[target.id] = node
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) \
                    and node.target.id in wanted:
                found[node.target.id] = node
    # DOCSTRINGS ARE PROSE. They are stripped from the definitions
    # themselves AND from anything nested inside them, so rewriting an
    # explanation never moves a hash that gates decision writing.
    for node in found.values():
        for inner in ast.walk(node):
            if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.ClassDef)):
                _strip_docstring(inner)
    return found


def semantic_code_sha(source_dir: str | None = None,
                      overrides: dict | None = None) -> str:
    """The digest over BETTOR's parsed decision path.

    `overrides` replaces a module's SOURCE TEXT, which is how the
    mutation tests prove what moves the hash and what does not without
    editing files on disk.
    """
    here = source_dir or os.path.dirname(os.path.abspath(__file__))
    digest = hashlib.sha256()
    # The boundary's own name is in the digest: two different boundary
    # definitions must never produce the same number.
    digest.update(BOUNDARY_VERSION.encode())

    for module in sorted(DECISION_PATH):
        wanted = DECISION_PATH[module]
        if overrides and module in overrides:
            src = overrides[module]
        else:
            with open(os.path.join(here, module), "r") as fh:
                src = fh.read()
        found = _named(ast.parse(src), tuple(wanted))
        missing = [n for n in wanted if n not in found]
        if missing:
            raise BoundaryIncomplete(
                "refused: %s is missing %s from the BETTOR decision "
                "boundary; a partial digest would compare unequal for a "
                "reason nobody could reconstruct"
                % (module, ", ".join(sorted(missing))))
        digest.update(module.encode())
        # SORTED, so moving a definition up or down a file -- which
        # changes nothing about behaviour -- does not move the hash.
        for name in sorted(wanted):
            digest.update(name.encode())
            digest.update(ast.dump(found[name]).encode())
    return digest.hexdigest()
