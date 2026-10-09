"""The 14-category scorecard reads declared paths only, never averages,
never lets a large healthy member set carry failed checks, and treats an
unreadable or zero-sample unit as a failure."""
from __future__ import annotations

import json
import os
import sys

import pytest

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
    # the Trader device acceptance is not supplied here: READ_UNAVAILABLE
    assert "trader_device_acceptance" in cc["unreadable_units"]


def _trader(tmp_path, results):
    good = {"view": "desktop_trader", "device": "desktop", "status": 200, "signed_in": True,
            "main_thread_responsive": True, "overflow_px": 0, "fixed_overlaps": [],
            "touch_targets_under_44": 0, "paper_mentions": 2, "shadow_mentions": 1,
            "console_errors": 0, "suspect_words": []}
    p = tmp_path / "preview.json"
    p.write_text(json.dumps({"records": [good]}))
    if results is not None:
        (tmp_path / "trader_accept.json").write_text(json.dumps({"results": results}))
    return _cat(SC.score(str(tmp_path), frontend_preview=str(p)), "Command Center desktop and mobile")


def _tr(dev, failures=()):
    return {"device": dev, "verdict": "FAIL" if failures else "PASS", "failures": list(failures),
            "api": {"returned": 517, "total_position_count": 517}}


def test_trader_acceptance_passes_only_with_every_device_clean(tmp_path):
    cc = _trader(tmp_path, [_tr(d) for d in SC.TRADER_DEVICES])
    assert cc["passes"] is True


def test_one_trader_device_failure_fails_the_row(tmp_path):
    rs = [_tr(d) for d in SC.TRADER_DEVICES]
    rs[1] = _tr("iphone", ["ASK_NOT_SHOWN"])
    cc = _trader(tmp_path, rs)
    assert cc["passes"] is False
    assert "ASK_NOT_SHOWN" in json.dumps(cc)
    tr = next(c for c in cc["components"] if c["component"] == "trader_device_acceptance")
    assert (tr["numerator"], tr["denominator"], tr["rate"]) == (4, 5, 0.8)


def test_a_missing_trader_device_or_file_is_unavailable_not_green(tmp_path):
    cc = _trader(tmp_path, [_tr(d) for d in SC.TRADER_DEVICES if d != "ipad_landscape"])
    assert cc["passes"] is False and "READ_UNAVAILABLE:trader_accept.json:ipad_landscape" in json.dumps(cc)
    d2 = tmp_path / "absent"
    d2.mkdir()
    cc3 = _trader(d2, None)
    assert cc3["passes"] is False and "READ_UNAVAILABLE:trader_accept.json:NOT_SUPPLIED" in json.dumps(cc3)


def test_a_trader_run_without_a_snapshot_is_not_a_pass(tmp_path):
    rs = [_tr(d) for d in SC.TRADER_DEVICES]
    rs[0] = {"device": "desktop", "verdict": "NO_API_SNAPSHOT", "live_snapshots": 0}
    assert _trader(tmp_path, rs)["passes"] is False


# ── V2 (RC6 lane E): Kalshi's own credential verdict ──────────────────────

REL = "69a8a07e5335864bd3d70f7160494aed13305fcc"
OTHER = "7fd4574e9ac8b95c355035a5bd4a9927d01c29ea"


def _cred(status="RED", blockers=("CREDENTIAL_CLASS_MISMATCH:PMUS:"
                                  "POLYMARKET_EXCHANGE_RSA_M2M",),
          kalshi=("KALSHI_ED25519_API_KEY", "KALSHI_ED25519_API_KEY"),
          verdict="MATCHES", not_provisioned=()):
    """CREDENTIAL_CLASSES exactly as production read it (pm-acceptance
    37836393458): aggregate RED from PMUS alone, Kalshi MATCHES."""
    return {"control": "CREDENTIAL_CLASSES", "status": status,
            "blockers": list(blockers),
            "evidence": {
                "expected": {"PMX": "POLYMARKET_EXCHANGE_RSA_M2M",
                             "PMUS": "POLYMARKET_US_ED25519",
                             "KALSHI": "KALSHI_RSA_API_KEY"},
                "approved": {"PMX": ["POLYMARKET_EXCHANGE_RSA_M2M"],
                             "PMUS": ["POLYMARKET_US_ED25519"],
                             "KALSHI": ["KALSHI_ED25519_API_KEY",
                                        "KALSHI_RSA_API_KEY"]},
                "by_process": {
                    "api": {"PMX": "POLYMARKET_EXCHANGE_RSA_M2M",
                            "PMUS": "POLYMARKET_EXCHANGE_RSA_M2M",
                            "KALSHI": kalshi[0]},
                    "workers": {"PMX": "POLYMARKET_EXCHANGE_RSA_M2M",
                                "PMUS": "POLYMARKET_EXCHANGE_RSA_M2M",
                                "KALSHI": kalshi[1]}},
                "verdicts": {"PMX": "MATCHES",
                             "PMUS": "MISMATCH_PATH_BLOCKED",
                             "KALSHI": verdict},
                "not_provisioned": list(not_provisioned)}}


def _kalshi_acc(tmp_path, cred):
    _write(tmp_path, "red_team.json", _env({"implementation_sha": REL,
                                            "readiness": {"controls": {
                                                "CREDENTIAL_CLASSES": cred}}}))
    _write(tmp_path, "venues.json", _env({"health": {"KALSHI_HEALTH": {
        "state": "OK", "mechanism": {"mechanism": "KALSHI_WS", "why": []},
        "freshness": {"numerator": 145, "denominator": 145},
        "catalogue": {"complete": True, "stopped": None}}}}))
    return SC.score(str(tmp_path), release_sha=REL)


def _u(out, cat, unit):
    return next(u for u in _cat(out, cat)["units"] if u["unit"] == unit)


def test_kalshi_reads_its_own_verdict_not_the_pmus_red(tmp_path):
    """Production: aggregate RED only from PMUS, Kalshi MATCHES in both
    processes. The Kalshi unit passes; the Red-team aggregate still
    fails on PMUS and its blocker stays visible in the Kalshi detail."""
    out = _kalshi_acc(tmp_path, _cred())
    u = _u(out, "Kalshi integration", "credential_class_control")
    assert u["passed"] is True and u["class"] == "PASS"
    assert u["detail"]["kalshi_verdict"] == "MATCHES"
    assert u["detail"]["aggregate_status"] == "RED"
    assert u["detail"]["aggregate_blockers_outside_kalshi"] == [
        "CREDENTIAL_CLASS_MISMATCH:PMUS:POLYMARKET_EXCHANGE_RSA_M2M"]
    assert _cat(out, "Kalshi integration")["passes"] is True
    rt = _u(out, "Red-team safeguards", "CREDENTIAL_CLASSES")
    assert rt["passed"] is False and rt["detail"]["status"] == "RED"


@pytest.mark.parametrize("cred,needle", [
    (_cred(blockers=("CREDENTIAL_CLASS_MISMATCH:KALSHI:ED25519_PEM",),
           kalshi=("ED25519_PEM", "KALSHI_ED25519_API_KEY"),
           verdict="MISMATCH_PATH_BLOCKED"),
     "KALSHI_CREDENTIAL_VERDICT_NOT_MATCHES:MISMATCH_PATH_BLOCKED"),
    (_cred(kalshi=(None, "KALSHI_ED25519_API_KEY")),
     "KALSHI_CREDENTIAL_NOT_PROVISIONED:api"),
    (_cred(verdict="NOT_PROVISIONED_PATH_BLOCKED", kalshi=(None, None),
           not_provisioned=("KALSHI",)),
     "KALSHI_CREDENTIAL_NOT_PROVISIONED"),
    (_cred(kalshi=("UNRECOGNISED_SHAPE", "KALSHI_ED25519_API_KEY")),
     "KALSHI_CREDENTIAL_CLASS_NOT_APPROVED:api:UNRECOGNISED_SHAPE"),
    (_cred(blockers=("READ_UNAVAILABLE:credential_classes",),
           status="UNKNOWN"),
     "CREDENTIAL_BLOCKER_NOT_ATTRIBUTABLE_TO_A_SLOT"),
    (_cred(status="UNKNOWN", blockers=()),
     "KALSHI_CREDENTIAL_CONTROL_NOT_COMPUTED:UNKNOWN"),
    ({"status": "RED", "blockers": ["X"]},
     "KALSHI_CREDENTIAL_EVIDENCE_ABSENT"),
])
def test_a_kalshi_finding_or_gap_still_fails_the_unit(tmp_path, cred, needle):
    u = _u(_kalshi_acc(tmp_path, cred), "Kalshi integration",
           "credential_class_control")
    assert u["passed"] is False
    assert any(needle in r for r in u["detail"]["reasons"]), u["detail"]


# ── V2: the evaluator and its inputs are pinned ───────────────────────────

def _api_acc(tmp_path, *, red_team_sha=REL, release_api=REL, small_live=REL):
    _write(tmp_path, "red_team.json", _env({"readiness": {
        "implementation_sha": red_team_sha,
        "authority": {"small_live": "SHADOW",
                      "kalshi_live_money": "NOT_ACTIVATED",
                      "adriana": "SHADOW_ONLY",
                      "capital_authority_granted": False},
        "controls": {"CANONICAL_EXPOSURE": {"status": "GREEN",
                                            "blockers": []}}}}))
    if release_api is not None:
        _write(tmp_path, "release.json", {"api": {"sha": release_api},
                                          "workers": {"sha": REL}})
    _write(tmp_path, "completion.json", _env({"market_data": {
        "priority_freshness": {"numerator": 10, "denominator": 10}}}))
    _write(tmp_path, "small_live.json", {"launch": {
        "serving_build": small_live, "actual_orders_possible_now": False}})
    _write(tmp_path, "render.json", {s: {"live_commit": REL}
                                     for s in SC.SERVICES})
    return SC.score(str(tmp_path), release_sha=REL,
                    evaluator_sha="e" * 40, implementation_sha="1" * 40)


def test_the_scorecard_records_what_judged_what(tmp_path):
    out = _api_acc(tmp_path)
    pin = out["pinning"]
    assert out["version"] == "SCORECARD_14_V2"
    assert pin["evaluator"]["sha"] == "e" * 40
    assert pin["evaluator"]["version"] == SC.VERSION
    src = open(SC.__file__, "rb").read()
    import hashlib
    assert pin["evaluator"]["source_sha256"] == hashlib.sha256(src).hexdigest()
    assert pin["release_sha"] == REL and pin["implementation_sha"] == "1" * 40
    assert pin["api_serving_sha"] == REL
    # every input file it parsed, by the bytes it parsed
    for name in ("red_team.json", "release.json", "completion.json",
                 "small_live.json", "render.json"):
        raw = (tmp_path / name).read_bytes()
        assert pin["inputs_sha256"][name] == hashlib.sha256(raw).hexdigest()
    assert "gates.json" in pin["inputs_unreadable"]
    assert pin["refused_inputs"] == [] and pin["verdict"] in (
        "PINNED", "UNATTRIBUTED_INPUTS")
    assert {g["id"] for g in out["grading_changes"]} >= {
        "RC6E_KALSHI_CREDENTIAL_SCOPE", "RC6E_INPUT_PINNING"}


def test_readbacks_from_another_api_build_are_unmeasured_not_graded(tmp_path):
    """The serving API names 7fd4574e while 69a8a07e is graded: every unit
    reading an API-served input is UNMEASURED (a failure), never PASS --
    the 8/8 risk row cannot be credited to a release that did not serve
    it. The deployment identity checks still read and FAIL as such."""
    out = _api_acc(tmp_path, red_team_sha=OTHER, release_api=OTHER,
                   small_live=OTHER)
    risk = _cat(out, "Risk management and capital controls")
    assert risk["passes"] is False
    small = _u(out, "Risk management and capital controls", "small_live_shadow")
    assert small["class"] == "UNMEASURED" and small["passed"] is False
    assert "INPUT_NAMES_ANOTHER_RELEASE:red_team.json" in small["detail"]
    fr = _u(out, "Data freshness and latency", "priority_members_fresh")
    assert fr["class"] == "UNMEASURED"
    assert "red_team.json" in out["pinning"]["refused_inputs"]
    assert out["pinning"]["verdict"] == "INPUTS_REFUSED"
    dep = _u(out, "Deployment infrastructure", "sportsassets-api_on_release")
    assert dep["class"] == "PASS"            # render.json is the identity check


def test_an_api_that_changed_during_the_run_is_refused(tmp_path):
    out = _api_acc(tmp_path, red_team_sha=REL, release_api=OTHER)
    ident = out["pinning"]["identity"]
    assert ident["api"]["conflict"] is True
    assert ident["files"]["completion.json"]["verdict"] == SC.FOREIGN
    assert "API_SERVING_IDENTITY_CONFLICT" in \
        ident["files"]["completion.json"]["reason"]
    # red_team.json names its own build (the release): still graded
    assert ident["files"]["red_team.json"]["verdict"] == SC.MATCHES


def test_an_input_naming_its_own_other_build_is_refused_alone(tmp_path):
    out = _api_acc(tmp_path, small_live=OTHER)
    u = _u(out, "Risk management and capital controls",
           "actual_orders_impossible_now")
    assert u["class"] == "UNMEASURED"
    assert out["pinning"]["refused_inputs"] == ["small_live.json"]
    assert _u(out, "Risk management and capital controls",
              "small_live_shadow")["class"] == "PASS"


def test_without_a_release_sha_nothing_is_attributed_or_refused(tmp_path):
    _write(tmp_path, "red_team.json", _env({"readiness": {
        "implementation_sha": OTHER, "controls": {}}}))
    out = SC.score(str(tmp_path))
    assert out["pinning"]["verdict"] == SC.NO_REFERENCE
    assert out["pinning"]["refused_inputs"] == []


def _preview(tmp_path, *, target, production, trader_sha=REL):
    good = {"view": "desktop_trader", "device": "desktop", "status": 200,
            "signed_in": True, "main_thread_responsive": True,
            "overflow_px": 0, "fixed_overlaps": [], "touch_targets_under_44": 0,
            "paper_mentions": 2, "shadow_mentions": 1, "console_errors": 0,
            "suspect_words": []}
    (tmp_path / "frontend_preview.json").write_text(json.dumps(
        {"records": [good]}))
    (tmp_path / "trader_accept.json").write_text(json.dumps({"results": [
        dict(_tr(d), api={"returned": 3, "source_sha": trader_sha})
        for d in SC.TRADER_DEVICES]}))
    if target:
        _write(tmp_path, "frontend_preview_identity.json",
               {"run_id": "1", "target_sha": target})
    if production:
        _write(tmp_path, "frontend_build.json", {
            "schema": "bt.frontend.build.v1", "sha": production,
            "context": "production"})
    return SC.score(str(tmp_path), release_sha=REL, frontend_preview=str(
        tmp_path / "frontend_preview.json"))


FE = "f90dafddb9653e92816d631d09da456ac693d8a0"


def test_device_views_of_the_production_frontend_are_graded(tmp_path):
    out = _preview(tmp_path, target=FE, production=FE)
    cc = _cat(out, "Command Center desktop and mobile")
    assert cc["passes"] is True
    assert out["pinning"]["frontend_sha"] == FE
    assert out["pinning"]["identity"]["frontend"]["verdict"] == SC.MATCHES


def test_a_preview_of_another_frontend_is_unmeasured(tmp_path):
    out = _preview(tmp_path, target="a" * 40, production=FE)
    cc = _cat(out, "Command Center desktop and mobile")
    assert cc["passes"] is False
    dv = next(u for u in cc["units"] if u["unit"] == "device_views")
    assert dv["class"] == "UNMEASURED"
    assert "FRONTEND_PREVIEW_IS_NOT_THE_DEPLOYED_FRONTEND" in dv["detail"]
    tr = next(u for u in cc["units"] if u["unit"] == "trader_device_acceptance")
    assert tr["class"] == "UNMEASURED"
    gate = _u(out, "GitHub CI and regression tests", "frontend_device_gate")
    assert gate["class"] == "UNMEASURED"
    assert "frontend_preview" in out["pinning"]["refused_inputs"]


def test_a_trader_acceptance_of_another_api_is_unmeasured(tmp_path):
    out = _preview(tmp_path, target=FE, production=FE, trader_sha=OTHER)
    tr = _u(out, "Command Center desktop and mobile",
            "trader_device_acceptance")
    assert tr["class"] == "UNMEASURED" and tr["passed"] is False
    assert _cat(out, "Command Center desktop and mobile")["passes"] is False


def test_an_unattributed_preview_is_graded_and_listed(tmp_path):
    out = _preview(tmp_path, target=None, production=None)
    assert _cat(out, "Command Center desktop and mobile")["passes"] is True
    assert "frontend_preview" in out["pinning"]["unattributed_inputs"]
    assert out["pinning"]["verdict"] == "UNATTRIBUTED_INPUTS"


def test_the_scorecard_is_one_stdlib_file_a_later_judge_can_pin():
    import re as _re
    src = open(SC.__file__).read()
    imports = sorted(set(_re.findall(r"^(?:from|import) (\S+)", src, _re.M)))
    assert imports == ["__future__", "argparse", "hashlib", "json", "os",
                       "re", "sys"]
