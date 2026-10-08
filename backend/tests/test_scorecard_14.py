"""The 14-category scorecard reads declared paths only, never averages,
never lets a large healthy member set carry failed checks, and treats an
unreadable or zero-sample unit as a failure."""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import scorecard_14 as SC  # noqa: E402


def _write(d, name, obj):
    with open(os.path.join(d, name), "w") as f:
        json.dump(obj, f)


def _cat(out, name):
    return next(c for c in out["categories"] if c["category"] == name)


def _env(data):
    return {"status": "OK", "data": data}


def test_an_empty_packet_fails_every_category_with_named_reasons(tmp_path):
    out = SC.score(str(tmp_path), release_sha="a" * 40)
    assert len(out["categories"]) == 14 and out["passing"] == 0
    assert out["all_categories_pass"] is False and out["not_averaged"] is True
    for c in out["categories"]:
        assert c["passes"] is False
        assert all(u["class"] in ("READ_UNAVAILABLE", "UNMEASURED", "FAIL")
                   for u in c["units"]), c["category"]


def test_a_large_healthy_set_cannot_carry_failed_checks(tmp_path):
    kalshi = {"health": {"KALSHI_HEALTH": {
        "state": "DEGRADED",
        "mechanism": {"mechanism": "KALSHI_WS", "why": []},
        "freshness": {"numerator": 1000, "denominator": 1000},
        "catalogue": {"complete": False, "stopped": "KALSHI_MD_RATE_LIMITED_429"}}}}
    _write(tmp_path, "venues.json", _env(kalshi))
    _write(tmp_path, "red_team.json", _env({"readiness": {"controls": {
        "CREDENTIAL_CLASSES": {"status": "RED", "blockers": ["X"]}}}}))
    k = _cat(SC.score(str(tmp_path)), "Kalshi integration")
    assert k["readiness"] == 0.25 and k["passes"] is False
    assert k["binding_component"] == "checks"


def test_zero_samples_are_unmeasured_not_one_hundred_percent(tmp_path):
    _write(tmp_path, "paper_freshness.json", {"freshness": {
        "counts": {"FRESH": {"count": 0}, "QUIET_VALID": {"count": 0}},
        "markable": 0, "sla_s": 300}})
    f = _cat(SC.score(str(tmp_path)), "Data freshness and latency")
    held = next(u for u in f["units"] if u["unit"] == "held_positions_fresh")
    assert held["class"] == "UNMEASURED" and f["passes"] is False


def test_a_non_ok_envelope_is_read_unavailable(tmp_path):
    _write(tmp_path, "completion.json", {"status": "UNAVAILABLE", "data": None, "why": "x"})
    c = _cat(SC.score(str(tmp_path)), "Core trading engine")
    assert any("ENVELOPE_UNAVAILABLE" in str(u["detail"]) for u in c["units"])


def test_forward_reds_are_annotated_but_still_failed(tmp_path):
    _write(tmp_path, "red_team.json", _env({"readiness": {"controls": {
        "CAPACITY": {"status": "RED", "blockers": ["NO_POSITIVE_CAPACITY"]},
        "RELEASE": {"status": "GREEN", "blockers": []}}}}))
    r = _cat(SC.score(str(tmp_path)), "Red-team safeguards")
    cap = next(u for u in r["units"] if u["unit"] == "CAPACITY")
    assert cap["class"] == "FORWARD" and cap["passed"] is False
    assert r["readiness"] == 0.5 and r["passes"] is False


def test_the_runtime_window_comes_from_the_harness_receipt(tmp_path):
    _write(tmp_path, "acceptance.json", {
        "runtime_window": {"status": "FAILED", "no_oom_minutes": 0.0},
        "paper_history": {"status": "UNPROVEN", "immutable": None}})
    m = _cat(SC.score(str(tmp_path)), "Market-plane stability")
    u = next(u for u in m["units"] if u["unit"] == "runtime_window_ge_60_min")
    assert u["passed"] is False and u["class"] == "FAIL"
    core = _cat(SC.score(str(tmp_path)), "Core trading engine")
    ph = next(u for u in core["units"] if u["unit"] == "historical_paper_immutable_by_receipt")
    assert ph["passed"] is False


def test_device_views_count_every_check(tmp_path):
    good = {"view": "iphone_trader", "device": "iphone", "status": 200, "signed_in": True,
            "main_thread_responsive": True, "overflow_px": 0, "fixed_overlaps": [],
            "touch_targets_under_44": 0, "paper_mentions": 2, "shadow_mentions": 1,
            "console_errors": 0, "suspect_words": []}
    bad = dict(good, view="iphone_floor", touch_targets_under_44=3, fixed_overlaps=[{"a": 1}])
    p = tmp_path / "preview.json"
    p.write_text(json.dumps({"records": [good, bad]}))
    cc = _cat(SC.score(str(tmp_path), frontend_preview=str(p)), "Command Center desktop and mobile")
    comp = cc["components"][0]
    assert (comp["numerator"], comp["denominator"]) == (16, 18)
    assert cc["passes"] is False
