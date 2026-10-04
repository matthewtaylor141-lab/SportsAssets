"""THE RED-TEAM KIT'S REFERENCE MODELS, VENDORED UNCHANGED, WITHOUT AUTHORITY.

  §1 THE KIT'S OWN TESTS (tests/test_reference_models.py of the owner's
     BETTOR_R30_INDEPENDENT_ACCEPTANCE kit), imports adapted ONLY in their
     module path: reference.X -> sportsassets.research_ref.X. Bodies
     unchanged.
  §2 UNCHANGED: each vendored file's sha256 equals the kit file's sha256
     (computed from the kit as delivered on 2026-10-04). Editing a vendored
     file fails here; a new kit release is a new pin with its reason.
  §3 NO AUTHORITY: the modules import only the standard library, and no
     module on the decision / sizing / order / management / capital path
     imports the package -- only the research replay (sportsassets/replay)
     and its read-only endpoint may.
"""
from __future__ import annotations

import ast
import hashlib
import pathlib

from sportsassets.research_ref.marginal_capital_value import Tranche, allocate
from sportsassets.research_ref.system_truth import HARD_GATES, evaluate

ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "sportsassets"
REF = PKG / "research_ref"

#: sha256 of the kit's reference/*.py as delivered (BETTOR_R30_INDEPENDENT_
#: ACCEPTANCE, reference/marginal_capital_value.py and reference/
#: system_truth.py). The vendored copies must stay byte-identical.
KIT_SHA256 = {
    "marginal_capital_value.py":
        "d90ee11f68d590428868035314148fa8c61a68f7a0eb6ad6bf8802564177df3e",
    "system_truth.py":
        "163f0b32a3d2aa4e13c22db7e5428e4e15960f5327906c69ffe61f8d00745fd5",
}


# ── §1 the kit's tests (bodies unchanged) ────────────────────────────

def test_fast_redeployment_can_beat_higher_roi():
    a = Tranche("A","1",10000,400,1)
    b = Tranche("B","1",10000,900,30)
    assert a.raw_ppch > b.raw_ppch
    out = allocate([a,b], 10000)
    assert [x.opportunity_id for x in out["chosen"]] == ["A"]

def test_marginal_allocator_respects_reserve():
    ts = [Tranche("A","1",5000,250,2), Tranche("B","1",5000,200,2)]
    out = allocate(ts, 10000, reserve_usd=5000)
    assert out["allocated_capital_usd"] == 5000
    assert out["remaining_capital_usd"] == 0

def test_hard_truth_gate_cannot_be_averaged_away():
    hard = {k: True for k in HARD_GATES}
    hard["market_data_freshness"] = False
    out = evaluate(hard, {"profitability_evidence": 1.0, "agent_quality": 1.0})
    assert out["status"] == "NOT_READY"
    assert "market_data_freshness" in out["failed_hard_gates"]

def test_unavailable_hard_gate_is_not_ready():
    hard = {k: True for k in HARD_GATES}
    hard["settlement_compatibility"] = None
    assert evaluate(hard)["status"] == "NOT_READY"


# ── §2 unchanged ─────────────────────────────────────────────────────

def test_the_vendored_files_are_byte_identical_to_the_kit():
    for name, want in KIT_SHA256.items():
        got = hashlib.sha256((REF / name).read_bytes()).hexdigest()
        assert got == want, (name, got)
    assert sorted(p.name for p in REF.glob("*.py")) == sorted(
        list(KIT_SHA256) + ["__init__.py"])


def test_the_allocator_and_the_gate_report_no_authority():
    out = allocate([Tranche("A", "1", 100.0, 5.0, 2.0)], 1000.0)
    assert out["authority"] == "RESEARCH_SHADOW_ONLY"
    assert evaluate({k: True for k in HARD_GATES})["authority"] == \
        "READ_ONLY_REFERENCE"


# ── §3 no authority ──────────────────────────────────────────────────

STDLIB = {"__future__", "dataclasses", "typing"}


def _imports(path: pathlib.Path) -> set:
    out = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                out.add("." * node.level + (node.module or ""))
            else:
                out.add(node.module or "")
    return out


def test_the_reference_modules_import_only_the_standard_library():
    for name in KIT_SHA256:
        imps = {i.split(".")[0] for i in _imports(REF / name)}
        assert imps <= STDLIB, (name, imps)


#: the ONLY modules that may import the research reference package
ALLOWED_IMPORTERS = ("sportsassets/replay/", "sportsassets/research_ref/",
                     "sportsassets/api/command_r30_replay.py")


def test_no_decision_sizing_order_or_capital_module_imports_it():
    found = []
    for path in PKG.rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        src = path.read_text(errors="replace")
        if "research_ref" not in src:
            continue
        for imp in _imports(path):
            if "research_ref" in imp:
                found.append(rel)
                assert rel.startswith(ALLOWED_IMPORTERS), (rel, imp)
    # the replay really uses it (the capital-hour ranking counterfactual)
    assert any(f.startswith("sportsassets/replay/") for f in found), found
