"""RED TEAM CLOSEOUT V1 -- THE PACKAGE IS REQUIRED CI.

sportsassets/red_team is the imported package byte for byte, and the
package's own 51 tests run, unchanged, against that backend copy on every
backend-tests run. A drifted copy or a failing package test fails CI.
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
PKG = ROOT / "research" / "red_team_closeout" / "BETTOR_RED_TEAM_CLOSEOUT_V1"
BACK = ROOT / "backend" / "sportsassets" / "red_team"
SUBS = ("models", "claim_exposure", "two_leg_sentinel", "truth_quorum",
        "profit_breaker", "freshness_guard", "release_guard", "fee_guard",
        "settlement_guard", "digital_twin_gate", "sample_integrity",
        "capacity_guard", "ui_truth", "karen_value", "attribution",
        "credential_guard", "stream_guard", "migration_guard", "readiness")


def test_the_backend_package_is_the_imported_package_byte_for_byte():
    src = PKG / "bettor_red_team"
    names = sorted(p.name for p in src.glob("*.py"))
    assert names == sorted(p.name for p in BACK.glob("*.py"))
    assert len(names) == 20
    for n in names:
        assert hashlib.sha256((src / n).read_bytes()).digest() == \
            hashlib.sha256((BACK / n).read_bytes()).digest(), n


def test_every_package_module_is_aliased():
    assert sorted(SUBS + ("__init__",)) == sorted(
        p.stem for p in BACK.glob("*.py"))


def test_the_51_package_tests_pass_against_the_backend_copy(monkeypatch):
    from sportsassets import red_team as RT
    monkeypatch.setitem(sys.modules, "bettor_red_team", RT)
    for sub in SUBS:
        monkeypatch.setitem(sys.modules, "bettor_red_team." + sub,
                            importlib.import_module(
                                "sportsassets.red_team." + sub))
    spec = importlib.util.spec_from_file_location(
        "_pkg_red_team_tests", PKG / "tests" / "test_red_team_closeout.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ran = 0
    for k in sorted(dir(mod)):
        if k.startswith("test_") and callable(getattr(mod, k)):
            getattr(mod, k)()
            ran += 1
    assert ran == 51


def test_the_import_receipt_records_51_of_51_and_the_zip():
    r = (ROOT / "research" / "red_team_closeout" /
         "IMPORT_RECEIPT.txt").read_text()
    assert "3bc54e3abb2cb9c2a9725a1ff98b8544327cbc6a9d03666222c469419d805b54" \
        in r
    assert "51 passed" in r and "(expected 51)" in r
