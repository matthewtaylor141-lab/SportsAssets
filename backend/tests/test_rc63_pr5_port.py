"""RC6.3 PR #5 PORT -- the Day One epoch on the release tree, with migration
365 still reverted.

The release tree (34cb9544) reverted the provenance merge and its migration
365 "rather than weakening the receipt" (71cfc5e4). PR #5 was written on the
tree that still held 365, so it carried three things that do not belong on
the release tree:

- tools/upgrade_path_receipt.py pre-approved 365's six research-counter
  triggers (`_prove_research_counter_sidecar`) and skipped any trigger listed
  under `proven_metadata_only_triggers` in compatibility() -- the exact
  receipt relaxation the revert declined;
- two Day One review files imported a helper from
  tests.test_rc6_provenance_refuses_after_change, a module the revert
  deleted (ModuleNotFoundError at the port SHA);
- docs/closeout/PAPER_DAY_ONE.md described the 365 proof as shipped.

These tests pin the port: the receipt honours no 365 exemption (a trigger
the base never had stays NOT_PROVEN unless it is a proven-dormant 317 epoch
guard), no test imports the reverted module, and the doc describes the tree
it ships in. ALL DATA SYNTHETIC.
"""
from __future__ import annotations

import ast
import pathlib

from tools import upgrade_path_receipt as UP

BACKEND = pathlib.Path(__file__).resolve().parents[1]
REPO = BACKEND.parent


def _table(triggers=None, **extra):
    t = {"columns": {}, "constraints": {}, "unique_indexes": {},
         "triggers": dict(triggers or {})}
    t.update(extra)
    return t


def test_the_receipt_carries_no_migration_365_exemption():
    """A trigger added on an existing table that only a 365-style
    `proven_metadata_only_triggers` list vouches for is NOT_PROVEN: the
    release tree reverted 365 rather than relax this receipt."""
    assert not hasattr(UP, "_prove_research_counter_sidecar")
    assert not any(n.startswith("_RESEARCH_COUNTER") for n in vars(UP))
    before = {"external_valuations": _table()}
    after = {"external_valuations": _table(
        {"research_training_set_valuation_changed": "CREATE TRIGGER ..."},
        proven_metadata_only_triggers=[
            "research_training_set_valuation_changed"])}
    out = UP.compatibility(before, after)
    assert out["verdict"] == UP.NOT_PROVEN
    assert out["unproven"] == [
        "%s:external_valuations:research_training_set_valuation_changed"
        % UP.R_TRIGGER_ADDED]


def test_the_317_dormancy_proof_is_still_the_only_trigger_exemption():
    """The 317 epoch guard proven dormant by snapshot() is still skipped
    (its own reviewed proof); anything else added stays NOT_PROVEN."""
    before = {"paper_orders": _table()}
    after = {"paper_orders": _table(
        {"paper_orders_epoch_guard": "CREATE TRIGGER ...",
         "something_else": "CREATE TRIGGER ..."},
        proven_dormant_epoch_triggers=["paper_orders_epoch_guard"])}
    out = UP.compatibility(before, after)
    assert out["unproven"] == ["%s:paper_orders:something_else"
                               % UP.R_TRIGGER_ADDED]


def test_no_test_imports_the_reverted_provenance_suite():
    """The revert deleted tests/test_rc6_provenance_refuses_after_change.py;
    a test that imports it fails at collection (ModuleNotFoundError)."""
    assert not (BACKEND / "tests" /
                "test_rc6_provenance_refuses_after_change.py").exists()
    offenders = []
    for f in sorted((BACKEND / "tests").glob("test_*.py")):
        tree = ast.parse(f.read_text(), str(f))
        for node in ast.walk(tree):
            mod = None
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
            elif isinstance(node, ast.Import):
                mod = ",".join(a.name for a in node.names)
            if mod and "test_rc6_provenance_refuses_after_change" in mod:
                offenders.append(f.name)
    assert offenders == []


def test_the_day_one_doc_describes_the_tree_it_ships_in():
    doc = (REPO / "docs" / "closeout" / "PAPER_DAY_ONE.md").read_text()
    assert "Migration 365" not in doc
    assert "365" not in doc
    assert not list((BACKEND / "migrations").glob("365_*.sql"))
