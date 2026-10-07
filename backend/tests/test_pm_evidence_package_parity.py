"""PM EVIDENCE & ACCEPTANCE PACK V1 -- THE PACKAGE IS REQUIRED CI.

sportsassets/pm_evidence carries the pack's three components byte for byte
(validator, scoreboard, harness and their frozen data: golden_cases.json,
thresholds.json, acceptance_spec.json -- the image does not ship research/),
and the pack's own 45 tests run, unchanged, against that backend copy on
every backend-tests run: golden 18, scoreboard 11, acceptance 16.
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
PKG = (ROOT / "research" / "pm_evidence_acceptance" /
       "BETTOR_PM_EVIDENCE_ACCEPTANCE_PACK_V1")
BACK = ROOT / "backend" / "sportsassets" / "pm_evidence"
PAIRS = (
    ("golden_market_validation/golden_validator/__init__.py",
     "golden_validator/__init__.py"),
    ("golden_market_validation/golden_validator/validator.py",
     "golden_validator/validator.py"),
    ("forward_profitability_scoreboard/scoreboard/__init__.py",
     "scoreboard/__init__.py"),
    ("forward_profitability_scoreboard/scoreboard/scoreboard.py",
     "scoreboard/scoreboard.py"),
    ("final_pm_acceptance_harness/pm_acceptance/__init__.py",
     "pm_acceptance/__init__.py"),
    ("final_pm_acceptance_harness/pm_acceptance/harness.py",
     "pm_acceptance/harness.py"),
    ("golden_market_validation/data/golden_cases.json",
     "data/golden_cases.json"),
    ("forward_profitability_scoreboard/thresholds.json",
     "data/thresholds.json"),
    ("final_pm_acceptance_harness/acceptance_spec.json",
     "data/acceptance_spec.json"),
)
SUITES = (
    ("golden_market_validation/tests/test_golden_cases.py", 18,
     {"golden_validator": "golden_validator",
      "golden_validator.validator": "golden_validator.validator"}),
    ("forward_profitability_scoreboard/tests/test_scoreboard.py", 11,
     {"scoreboard": "scoreboard", "scoreboard.scoreboard":
      "scoreboard.scoreboard"}),
    ("final_pm_acceptance_harness/tests/test_acceptance.py", 16,
     {"pm_acceptance": "pm_acceptance", "pm_acceptance.harness":
      "pm_acceptance.harness"}),
)


def _sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_the_backend_copy_is_the_pack_byte_for_byte():
    for src, dst in PAIRS:
        assert _sha(PKG / src) == _sha(BACK / dst), (src, dst)


def test_the_frozen_thresholds_are_version_1_and_unchanged():
    """thresholds.json is frozen before the forward cohort: a change is a
    NEW version file, never an edit of V1 (this pin fails on an edit)."""
    th = json.loads((BACK / "data" / "thresholds.json").read_text())
    assert th["version"] == "FORWARD_PROFITABILITY_SCOREBOARD_THRESHOLDS_V1"
    assert th["frozen_at"] == "2026-10-07"
    assert _sha(BACK / "data" / "thresholds.json") == (
        "%s" % _sha(PKG / "forward_profitability_scoreboard" /
                    "thresholds.json"))
    assert th["directional"]["min_independent_events"] == 100
    assert th["global"]["pnl_reconciliation_tolerance_usd"] == 0.01


def _run_suite(monkeypatch, rel, aliases):
    from sportsassets import pm_evidence as PE
    for name, sub in aliases.items():
        mod = importlib.import_module("sportsassets.pm_evidence." + sub)
        monkeypatch.setitem(sys.modules, name, mod)
    path = PKG / rel
    spec = importlib.util.spec_from_file_location(
        "_pm_pkg_" + path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ran = 0
    for k in sorted(dir(mod)):
        if k.startswith("test_") and callable(getattr(mod, k)):
            getattr(mod, k)()
            ran += 1
    assert PE is not None
    return ran



def test_the_45_pack_tests_pass_against_the_backend_copy(monkeypatch):
    total = 0
    for rel, n, aliases in SUITES:
        ran = _run_suite(monkeypatch, rel, aliases)
        assert ran == n, (rel, ran)
        total += ran
    assert total == 45


def test_the_import_receipt_records_45_of_45_and_the_zip():
    r = (ROOT / "research" / "pm_evidence_acceptance" /
         "IMPORT_RECEIPT.txt").read_text()
    assert "9b7e1dfe2856c066b5bb0b37ad5f271e613ddd2d872f1f5b218086bdc31ebf84" \
        in r
    assert "45/45" in r and "ALL 45 PACKAGE TESTS GREEN" in r


def test_the_kalshi_cases_stay_frozen_integration_cases():
    """The evidence distinction: Kalshi cases are FROZEN_INTEGRATION_CASE
    (not captured production payloads); PMUS cases cite captured fixtures
    that exist in this repository."""
    g = json.loads((BACK / "data" / "golden_cases.json").read_text())
    kinds = {s["id"]: s["kind"] for s in g["source_manifest"]}
    assert kinds["KALSHI_ADAPTER_SEMANTICS"] == "FROZEN_INTEGRATION_CASE"
    for s in g["source_manifest"]:
        if s["kind"] == "CAPTURED_VENUE_FIXTURE":
            assert (ROOT / s["repo_path"]).is_file(), s["repo_path"]
