"""THE IMPROVEMENT LOOP's guarantees, each pinned by a test that can fail.

Every test here has a CONTROL: a case constructed so the check it is
exercising is violated, proving the check is load-bearing rather than
vacuously true. A gate that has never rejected anything has not been
shown to be a gate.
"""
from __future__ import annotations

import ast
import copy
import json
import os
import sys

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "tools"))

import desk_experiments as X                                   # noqa: E402
from sportsassets.api import command_learning as CL            # noqa: E402


def _row(**kw):
    """A scenario result with benign defaults; override what matters."""
    base = dict(realized_pnl_usd=0.0, fees_usd=0.0,
                terminal_lower_usd=100000.0, terminal_upper_usd=100000.0,
                terminal_lower_vs_start=0.0, unresolved_legs=0,
                unresolved_cost_usd=1000.0, max_drawdown_usd=100.0,
                peak_committed_usd=1000.0, mean_committed_usd=500.0,
                turnover_usd=10000.0, turnover_x_capital=0.1,
                orders=200, decided_orders=200, fills=200,
                decisions={}, invariant_ok=True)
    base.update(kw)
    return base


def _trio(**kw):
    return [_row(**kw) for _ in X.SCENARIOS]


# ── the gate actually gates ───────────────────────────────────────────

def test_a_clear_winner_passes_so_the_gate_is_not_merely_a_rejector():
    """THE CONTROL FOR EVERY TEST BELOW.

    If nothing can ever pass, "no candidate qualified" carries no
    information. This constructs a candidate that beats the baseline on
    every declared axis and asserts it IS accepted.
    """
    base = _trio(realized_pnl_usd=-500.0)
    cand = _trio(realized_pnl_usd=+500.0, unresolved_cost_usd=900.0)
    v = X.gate_v2(base, cand, 200)
    assert v["accepted"], v["reasons"]


def test_a_dose_reduction_cannot_win_by_shrinking():
    """G1. C1 and C4 both 'won' under V1 purely by trading less."""
    base = _trio(realized_pnl_usd=-500.0, turnover_usd=10000.0)
    cand = _trio(realized_pnl_usd=+500.0, unresolved_cost_usd=900.0,
                 turnover_usd=4000.0)          # 40% < the 50% floor
    v = X.gate_v2(base, cand, 200)
    assert not v["accepted"]
    assert any("DOSE REDUCTION" in r for r in v["reasons"])


def test_winning_on_one_fill_scenario_is_refused():
    """A candidate that needs queue_share 0.50 has found liquidity."""
    base = _trio(realized_pnl_usd=-500.0)
    cand = [_row(realized_pnl_usd=-600.0, unresolved_cost_usd=900.0),
            _row(realized_pnl_usd=-600.0, unresolved_cost_usd=900.0),
            _row(realized_pnl_usd=+900.0, unresolved_cost_usd=900.0)]
    v = X.gate_v2(base, cand, 200)
    assert not v["accepted"]
    assert any("NOT ROBUST" in r for r in v["reasons"])


def test_profit_from_parking_capital_is_refused():
    """G3. Cost-marked P&L is flattered by holding rather than closing."""
    base = _trio(realized_pnl_usd=-500.0, unresolved_cost_usd=1000.0)
    cand = _trio(realized_pnl_usd=+500.0, unresolved_cost_usd=5000.0)
    v = X.gate_v2(base, cand, 200)
    assert not v["accepted"]
    assert any("CAPITAL PARKED" in r for r in v["reasons"])


def test_a_margin_smaller_than_the_bound_width_is_not_resolved():
    """G4. This is the defect cycle 1 found, encoded as a refusal.

    An improvement the instrument cannot see is not an improvement.
    """
    base = _trio(realized_pnl_usd=-500.0,
                 terminal_lower_usd=90000.0, terminal_upper_usd=110000.0)
    cand = _trio(realized_pnl_usd=-490.0, unresolved_cost_usd=900.0,
                 terminal_lower_usd=80000.0, terminal_upper_usd=120000.0)
    v = X.gate_v2(base, cand, 200)
    assert not v["accepted"]
    assert any("NOT_RESOLVED" in r for r in v["reasons"])


def test_inactivity_is_not_performance():
    base = _trio(realized_pnl_usd=-500.0)
    cand = _trio(realized_pnl_usd=+500.0, unresolved_cost_usd=900.0,
                 decided_orders=3)
    v = X.gate_v2(base, cand, 3)
    assert not v["accepted"]
    assert any("INELIGIBLE" in r for r in v["reasons"])


def test_a_broken_ledger_refuses_regardless_of_profit():
    base = _trio(realized_pnl_usd=-500.0)
    cand = _trio(realized_pnl_usd=+5000.0, unresolved_cost_usd=900.0,
                 invariant_ok=False)
    v = X.gate_v2(base, cand, 200)
    assert not v["accepted"]
    assert any("LEDGER DOES NOT RECONCILE" in r for r in v["reasons"])


# ── G7, the criterion cycle 1 discovered ─────────────────────────────

def test_sign_stability_detects_the_flip_and_passes_agreement():
    falling = [_row(realized_pnl_usd=v) for v in (-500.0, -900.0, -1700.0)]
    rising = [_row(realized_pnl_usd=v) for v in (40.0, 380.0, 970.0)]
    assert not X.sign_stability(falling, rising)["stable"]
    # CONTROL: the same helper must report STABLE when both agree.
    assert X.sign_stability(rising, rising)["stable"]
    assert X.sign_stability(falling, falling)["stable"]


# ── a mistyped knob is a refusal, not a silent no-op ─────────────────

def test_an_unknown_policy_knob_refuses_rather_than_running_unchanged():
    """A dead attribute would leave the policy UNCHANGED and report
    'no difference from baseline' -- a null result made by a typo."""
    with pytest.raises(SystemExit) as e:
        X.run_one([{"kind": "PRINT", "at": 1.0, "condition_id": "c",
                    "outcome_index": 0, "price": 0.5, "size": 10.0,
                    "evidence_id": "e1"}],
                  {}, {"entry_looo": 0.3}, 0.25, None, "x", 1000.0)
    assert "CANDIDATE_PARAM_NOT_A_POLICY_KNOB" in str(e.value)


# ── the partition rule handles overlapping outcomes ──────────────────

def test_a_condition_never_straddles_two_partitions():
    tape = [{"at": float(i), "condition_id": "c%d" % (i % 25),
             "outcome_index": 0, "price": 0.5, "size": 1.0,
             "kind": "PRINT", "evidence_id": str(i)} for i in range(500)]
    parts, counts = X.split(tape)
    where = {}
    for name, rows in parts.items():
        for e in rows:
            where.setdefault(e["condition_id"], set()).add(name)
    straddlers = {c: p for c, p in where.items() if len(p) > 1}
    assert not straddlers, straddlers
    assert sum(counts.values()) == 25


def test_the_held_back_partition_is_populated_and_unused():
    """It must exist, and nothing may score against it."""
    tape = [{"at": float(i), "condition_id": "c%d" % (i % 40),
             "outcome_index": 0, "price": 0.5, "size": 1.0,
             "kind": "PRINT", "evidence_id": str(i)} for i in range(800)]
    parts, counts = X.split(tape)
    assert counts["held_back_conditions"] > 0
    src = open(os.path.join(_ROOT, "tools",
                            "desk_experiments.py")).read()
    tree = ast.parse(src)
    scored = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.For)
                and isinstance(node.iter, ast.Tuple)):
            for el in node.iter.elts:
                if isinstance(el, ast.Constant):
                    scored.add(el.value)
    assert "TRAIN" in scored and "VALIDATION" in scored
    assert "HELD_BACK_NOT_USED" not in scored


# ── the loop cannot reach money, and cannot promote ──────────────────

def test_no_venue_client_or_write_in_the_import_graph():
    for path in (os.path.join(_ROOT, "tools", "desk_experiments.py"),
                 os.path.join(_ROOT, "sportsassets", "api",
                              "command_learning.py")):
        src = open(path).read().upper()
        for forbidden in ("INSERT INTO", "UPDATE ", "CLOB_CLIENT",
                          "PLACE_ORDER", "POST_ORDER"):
            assert forbidden not in src, (path, forbidden)


def test_the_learning_read_surface_has_no_promotion_route():
    app_src = open(os.path.join(_ROOT, "sportsassets", "api",
                                "app.py")).read()
    tree = ast.parse(app_src)
    verbs = set()
    for node in ast.walk(tree):
        # BOTH kinds. The routes are `async def`, and a walk that looked
        # only for FunctionDef found zero of them -- which is why the
        # `assert verbs` below exists: a test that searches for nothing
        # and finds nothing otherwise passes.
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            f = dec.func if isinstance(dec, ast.Call) else dec
            if not (isinstance(f, ast.Attribute)
                    and isinstance(f.value, ast.Name)
                    and f.value.id == "app"):
                continue
            args = dec.args if isinstance(dec, ast.Call) else []
            if args and isinstance(args[0], ast.Constant) and \
                    "/api/command/learning" in str(args[0].value):
                verbs.add(f.attr)
    assert verbs, "the learning routes were not found at all"
    assert verbs == {"get"}, verbs


# ── the read surface distinguishes unavailable from empty ────────────

def test_a_missing_artifact_raises_rather_than_returning_zeros(tmp_path,
                                                               monkeypatch):
    monkeypatch.setattr(CL, "_DIR", str(tmp_path))
    CL._cache.clear()
    with pytest.raises(CL.LearningUnavailable) as e:
        CL.overview()
    assert "NO_EXPERIMENT_REPORT" in str(e.value)


def test_an_unreadable_artifact_raises_rather_than_returning_zeros(
        tmp_path, monkeypatch):
    d = tmp_path / "experiments_x"
    d.mkdir()
    (d / "report.json").write_text("{ this is not json")
    monkeypatch.setattr(CL, "_DIR", str(tmp_path))
    CL._cache.clear()
    with pytest.raises(CL.LearningUnavailable) as e:
        CL.overview()
    assert "UNREADABLE" in str(e.value)


def _fake_report(**over):
    rep = {
        "cycle": 1, "variants_tried": 1, "code_version": "abc",
        "desk_version": "BETTOR_DESK_V1",
        "scenarios_queue_share": [0.25],
        "partitions": {"train_conditions": 1, "validation_conditions": 1,
                       "held_back_conditions": 1},
        "gate": {"id": "V2", "criteria": ["c"]},
        "candidates": [{"id": "BASELINE", "hypothesis": "h", "targets": "-",
                        "params": {}},
                       {"id": "C1", "hypothesis": "h", "targets": "T",
                        "params": {"entry_lo": 0.4}}],
        "results": {"TRAIN": {"BASELINE": [_row()], "C1": [_row()]},
                    "VALIDATION": {"BASELINE": [_row()], "C1": [_row()]}},
        "verdicts": {"TRAIN": {"C1": {"accepted": False,
                                      "reasons": ["BECAUSE"]}},
                     "VALIDATION": {"C1": {"accepted": False,
                                           "reasons": ["BECAUSE"]}}},
        "accepted": [], "outcome": "NO CANDIDATE QUALIFIED",
    }
    rep.update(over)
    return rep


def _write(tmp_path, rep, name="experiments_20260101T0000Z"):
    d = tmp_path / name
    d.mkdir(exist_ok=True)
    (d / "report.json").write_text(json.dumps(rep))


def test_an_empty_accept_list_still_renders_the_reasons(tmp_path,
                                                        monkeypatch):
    """"No candidate qualified" must be a FULL page, not a blank one.

    A panel that only had winners to show would render nothing and read
    as "the loop did not run".
    """
    _write(tmp_path, _fake_report())
    monkeypatch.setattr(CL, "_DIR", str(tmp_path))
    CL._cache.clear()
    o = CL.overview()
    assert o["accepted"] == [] and o["promotion_occurred"] is False
    assert o["candidates"] and o["candidates"][0]["train_reasons"] == \
        ["BECAUSE"]
    assert o["active_policy"]["status"] == "FROZEN"
    assert o["active_policy"]["changed_by_this_loop"] is False


def test_the_three_marks_are_always_returned_together(tmp_path,
                                                      monkeypatch):
    """Quoting the lower bound alone turns open inventory into a loss."""
    rep = _fake_report()
    rep["results"]["TRAIN"]["BASELINE"] = [
        _row(realized_pnl_usd=-969.0, terminal_lower_vs_start=-16558.0,
             terminal_lower_usd=83442.0, terminal_upper_usd=117976.0)]
    _write(tmp_path, rep)
    monkeypatch.setattr(CL, "_DIR", str(tmp_path))
    CL._cache.clear()
    row = [r for r in CL.results()["scenarios"]["TRAIN"]
           if r["policy"] == "BASELINE"][0]
    assert row["mark_cost_usd"] == -969.0
    assert row["mark_zero_usd"] == -16558.0
    assert row["mark_one_usd"] == pytest.approx(17976.0)
    assert row["mark_zero_usd"] < row["mark_cost_usd"] < row["mark_one_usd"]


def test_the_newest_cycle_is_the_one_shown(tmp_path, monkeypatch):
    _write(tmp_path, _fake_report(cycle=1, gate={"id": "V1",
                                                 "criteria": []}),
           "experiments_20260101T0000Z")
    _write(tmp_path, _fake_report(cycle=2, gate={"id": "V2",
                                                 "criteria": []}),
           "experiments_20260102T0000Z")
    monkeypatch.setattr(CL, "_DIR", str(tmp_path))
    CL._cache.clear()
    assert CL.overview()["cycle"] == 2
    assert CL.overview()["gate"]["id"] == "V2"
    c = CL.cycles()
    assert c["n"] == 2 and [x["gate"] for x in c["cycles"]] == ["V1", "V2"]


def test_a_promotion_would_be_reported_as_one(tmp_path, monkeypatch):
    """THE CONTROL for the two tests above.

    If `promotion_occurred` were hard-coded False the panel could never
    report a real acceptance, and the FROZEN badge would be decoration.
    """
    rep = _fake_report(accepted=["C1"], outcome="ACCEPTED: C1")
    rep["verdicts"]["TRAIN"]["C1"]["accepted"] = True
    rep["verdicts"]["VALIDATION"]["C1"]["accepted"] = True
    _write(tmp_path, rep)
    monkeypatch.setattr(CL, "_DIR", str(tmp_path))
    CL._cache.clear()
    o = CL.overview()
    assert o["promotion_occurred"] is True and o["accepted"] == ["C1"]
    assert o["candidates"][0]["promoted"] is True


# ── the committed cycle artifacts say what this session reported ─────

def test_the_committed_cycles_promoted_nothing():
    """If a later edit promotes something, this must be revisited by a
    human rather than passing silently."""
    root = os.path.join(_ROOT, "..", "research", "beta48", "learning")
    found = sorted(d for d in os.listdir(root)
                   if d.startswith("experiments_"))
    assert len(found) >= 2, found
    for d in found:
        rep = json.load(open(os.path.join(root, d, "report.json")))
        assert rep["accepted"] == [], (d, rep["accepted"])
        assert rep["variants_tried"] == 5, d
