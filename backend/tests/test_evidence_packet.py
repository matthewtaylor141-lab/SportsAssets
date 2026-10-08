"""The production evidence packet (backend/tools/evidence_packet.py) reports
only what a readback carries: every value names its file and its DECLARED
JSON path (never the first key of that name found anywhere), an absent value
is NOT_IN_READBACK and a failed read is READ_UNAVAILABLE with its reason
(never a default pass), memory quantiles come from Render's own samples per
instance for all three services, and the packet plus every input file is
hash-manifested so it is checkable against the bytes received.

PM review of the RC4 packet (run 37738089957): the breadth-first find()
reported policyVersion "RN1_SHADOW_V1" from shadow_health.environment (the
benchmark lane) next to the BETTOR policy component's status; the memory
and log loops never read sportsassets-market-plane."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "evidence_packet", ROOT / "tools" / "evidence_packet.py")
EP = importlib.util.module_from_spec(spec)
spec.loader.exec_module(EP)

SHA = "a" * 40
REL = "b" * 40


def _w(acc, name, obj):
    (acc / ("%s.json" % name)).write_text(json.dumps(obj))


def _acc(tmp_path):
    acc = tmp_path / "acc"
    acc.mkdir()
    _w(acc, "lineage", {"sha": REL, "release_branch": "claude/release-api",
                        "release_sha": REL, "accepted_base_sha": "c" * 40,
                        "descendant_of_base": True})
    _w(acc, "gates", {"runs": {"backend_tests": {
        "id": 1, "conclusion": "success", "head_sha": REL}}})
    _w(acc, "release", {"api": {"sha": REL}, "workers": {"sha": REL},
                        "alignment": {"verdict": "ALIGNED"}})
    # the production shape of /api/command/shadow/health (RC4 packet): the
    # environment carries the BENCHMARK lane's policyVersion first
    _w(acc, "shadow_health", {
        "environment": {"policyVersion": "RN1_SHADOW_V1",
                        "primaryLane": "BETTOR_EV_SHADOW"},
        "generatedAt": "2026-10-08T06:40:00Z",
        "components": {"BETTOR_POLICY_INTEGRITY": {
            "state": "HEALTHY", "policyIntegrityStatus": "VERIFIED",
            "codeShaMatches": True, "decisionWritingAllowed": True,
            "policyVersion": "BETTOR_EV_SHADOW_V6"}}})
    _w(acc, "pm_after", {"status": "OK", "data": {
        "pm_acceptance": {"pm_state": "RED", "evidence_input": {
            "software_red_count": 3, "held_fresh": 11, "held_required": 11}},
        "authority": {"small_live": "SHADOW"}}})
    _w(acc, "completion", {"status": "OK", "data": {
        "account_id": "paper_acct_main",
        "market_data": {"pmx_primary": {
            "source": "PMX_GRPC", "requested_symbols": 58}},
        "section_timings": {"market_data": {"ms": 66.3, "ok": True},
                            "pmx_primary": {"ms": 14.0, "ok": True}},
        "sections_unavailable": []}})
    (acc / "mem_sportsassets-workers.json").write_text(json.dumps([
        {"labels": [{"field": "instance", "value": "srv-x-abc"}],
         "values": [{"timestamp": "2026-10-08T01:%02d:00Z" % i,
                     "value": (1000 + i) * 1048576} for i in range(21)]},
        {"labels": [{"field": "instance", "value": "srv-x-old"}],
         "values": []}]))
    _w(acc, "logs_sportsassets-workers_churn",
       {"logs": [], "hasMore": False, "_filter": "exited cleanly; restarting",
        "_window": "a..b"})
    return acc


def test_values_carry_their_source_and_path(tmp_path):
    p = EP.build(_acc(tmp_path), now=1.0)
    pi = p["policy_integrity"]
    assert pi["policyIntegrityStatus"] == {
        "value": "VERIFIED", "source": "shadow_health.json",
        "path": "components.BETTOR_POLICY_INTEGRITY.policyIntegrityStatus"}
    assert pi["decisionWritingAllowed"]["value"] is True
    # the BETTOR policy's own version, never the benchmark lane's
    assert pi["policyVersion"]["value"] == "BETTOR_EV_SHADOW_V6"
    assert p["policy_identity"]["verdict"] == "BOUND"
    assert p["pmx_primary"]["path"] == "data.market_data.pmx_primary"
    ev = p["pm_evidence_input"]["software_red_count"]
    assert ev == {"value": 3, "source": "pm_after.json",
                  "path": "data.pm_acceptance.evidence_input."
                          "software_red_count"}
    assert p["identity"]["release_sha"] == REL
    assert p["identity"]["running_workers_sha"] == REL
    assert p["gates"]["backend_tests"] == {"run_id": 1,
                                           "conclusion": "success",
                                           "head_sha": REL}


def test_absent_values_are_never_a_default_pass(tmp_path):
    acc = _acc(tmp_path)
    (acc / "shadow_health.json").unlink()
    (acc / "pm_after.json").write_text("not json")
    p = EP.build(acc, now=1.0)
    # a readback that was never read is READ_UNAVAILABLE with its reason;
    # neither it nor NOT_IN_READBACK is ever a value
    for f in p["policy_integrity"].values():
        assert f["value"] == EP.UNAVAILABLE
        assert f["reason"] == "READ_UNAVAILABLE:shadow_health:FILE_ABSENT"
    assert p["pm_evidence_input"]["value"] == EP.UNAVAILABLE
    assert p["pm_evidence_input"]["reason"] == \
        "READ_UNAVAILABLE:pm_after:NOT_JSON"
    for k in ("canary", "market_plane"):
        assert p[k] == {"value": EP.UNAVAILABLE, "reason":
                        "READ_UNAVAILABLE:%s:FILE_ABSENT" % k}
    assert p["readbacks"]["shadow_health"]["present"] is False
    assert p["readbacks"]["pm_after"]["parsed"] is False
    assert p["identity"]["implementation_sha"] in (EP.MISSING,
                                                   EP.os.environ.get(
                                                       "IMPL_SHA"))


def test_memory_quantiles_are_per_instance_from_render_samples(tmp_path):
    p = EP.build(_acc(tmp_path), now=1.0)
    m = p["memory"]["sportsassets-workers"]
    assert list(m) == ["srv-x-abc"]           # an empty series is not a 0
    s = m["srv-x-abc"]
    assert s["samples"] == 21 and s["min_mb"] == 1000.0
    assert s["max_mb"] == 1020.0 and s["p50_mb"] == 1010.0
    assert s["p95_mb"] == 1019.0
    assert s["first"] == "2026-10-08T01:00:00Z"
    assert s["last"] == "2026-10-08T01:20:00Z"


def test_log_counts_name_their_filter_and_window(tmp_path):
    p = EP.build(_acc(tmp_path), now=1.0)
    assert p["log_counts"]["sportsassets-workers_churn"] == {
        "lines": 0, "has_more": False, "lower_bound": False,
        "filter": "exited cleanly; restarting", "window": "a..b",
        "wrong_resource": 0}


def test_the_packet_and_inputs_are_hash_manifested(tmp_path, monkeypatch,
                                                   capsys):
    acc = _acc(tmp_path)
    monkeypatch.setenv("IMPL_SHA", SHA)
    assert EP.main(["x", str(acc)]) == 0
    pkt = json.loads((acc / "evidence_packet.json").read_text())
    assert pkt["identity"]["implementation_sha"] == SHA
    sums = dict(reversed(ln.split("  ")) for ln in
                (acc / "SHA256SUMS").read_text().splitlines())
    assert "SHA256SUMS" not in sums
    for name, h in sums.items():
        assert hashlib.sha256((acc / name).read_bytes()).hexdigest() == h
    # the packet itself records the hashes of the inputs it was built from
    assert pkt["input_files_sha256"]["lineage.json"] == sums["lineage.json"]
    assert sums["evidence_packet.json"] in capsys.readouterr().out


def test_the_builder_is_stdlib_only_and_writes_nothing_else(tmp_path):
    src = (ROOT / "tools" / "evidence_packet.py").read_text()
    for mod in ("requests", "httpx", "psycopg", "asyncpg", "sportsassets"):
        assert "import %s" % mod not in src and "from %s" % mod not in src
    acc = _acc(tmp_path)
    before = {p.name for p in acc.iterdir()}
    EP.main(["x", str(acc)])
    assert {p.name for p in acc.iterdir()} - before == {
        "evidence_packet.json", "SHA256SUMS"}


def test_pm_acceptance_builds_and_attests_the_packet():
    wf = (ROOT.parent / ".github" / "workflows" /
          "pm-acceptance.yml").read_text()
    # the judge's builder (the workflow's own commit), never the release's
    assert "python3 -I judge/backend/tools/evidence_packet.py acc" in wf
    steps = yaml.safe_load(wf)["jobs"]["accept"]["steps"]
    att = [s for s in steps if "attest-build-provenance" in str(s.get(
        "uses"))]
    assert len(att) == 1
    assert att[0]["with"]["subject-path"].split() == [
        "acc/evidence_packet.json", "acc/SHA256SUMS"]
    assert "implementation_sha:" in wf
    for f in ("exited cleanly; restarting", "WAS ALREADY APPLIED",
              "POLICY_CODE_DRIFT", "/metrics/memory?resource="):
        assert f in wf, f


# ── declared schema paths (PM review of RC4, defect 3) ────────────────────

def _rewrite(acc, name, fn):
    d = json.loads((acc / ("%s.json" % name)).read_text())
    d = fn(d)
    (acc / ("%s.json" % name)).write_text(json.dumps(d))


def test_reordered_json_cannot_change_what_is_reported(tmp_path):
    """V1's first breadth-first match depended on key order: completion
    carries pmx_primary under data.market_data AND a timing record under
    data.section_timings, shadow_health a policyVersion under environment
    AND under the policy component."""
    acc = _acc(tmp_path)
    base = EP.build(acc, now=1.0)

    def reorder(d):
        if isinstance(d, dict):
            return {k: reorder(d[k]) for k in reversed(list(d))}
        if isinstance(d, list):
            return [reorder(x) for x in d]
        return d
    for name in ("shadow_health", "completion", "pm_after"):
        _rewrite(acc, name, reorder)
    again = EP.build(acc, now=1.0)
    for k in ("policy_integrity", "pmx_primary", "pm_evidence_input",
              "policy_identity"):
        assert again[k] == base[k], k
    assert again["pmx_primary"]["value"]["source"] == "PMX_GRPC"


def test_an_old_record_in_a_history_list_is_never_the_current_one(tmp_path):
    """An older policyIntegrityStatus nested shallower than the current
    component (a history list at the top of the body) was V1's first
    breadth-first match."""
    acc = _acc(tmp_path)

    def add_history(d):
        out = {"history": [{"policyIntegrityStatus": "DRIFT",
                            "policyVersion": "BETTOR_EV_SHADOW_V5",
                            "codeShaMatches": False,
                            "decisionWritingAllowed": False}]}
        out.update(d)
        return out
    _rewrite(acc, "shadow_health", add_history)
    pi = EP.build(acc, now=1.0)["policy_integrity"]
    assert pi["policyIntegrityStatus"]["value"] == "VERIFIED"
    assert pi["policyVersion"]["value"] == "BETTOR_EV_SHADOW_V6"
    assert pi["codeShaMatches"]["value"] is True
    assert all(v["path"].startswith("components.BETTOR_POLICY_INTEGRITY.")
               for v in pi.values())


def test_a_component_without_the_field_is_not_in_readback(tmp_path):
    acc = _acc(tmp_path)

    def drop(d):
        d["components"]["BETTOR_POLICY_INTEGRITY"].pop("codeShaMatches")
        d["environment"]["codeShaMatches"] = True       # elsewhere: ignored
        return d
    _rewrite(acc, "shadow_health", drop)
    f = EP.build(acc, now=1.0)["policy_integrity"]["codeShaMatches"]
    assert f["value"] == EP.MISSING
    assert f["reason"] == ("NOT_IN_READBACK:shadow_health:components."
                           "BETTOR_POLICY_INTEGRITY.codeShaMatches")


def test_a_policy_component_of_another_lane_is_an_identity_mismatch(
        tmp_path):
    acc = _acc(tmp_path)

    def other(d):
        d["environment"]["primaryLane"] = "RN1_SHADOW"
        return d
    _rewrite(acc, "shadow_health", other)
    assert EP.build(acc, now=1.0)["policy_identity"]["verdict"] == \
        EP.MISMATCH


def test_a_failed_section_read_is_read_unavailable_never_a_default(
        tmp_path):
    acc = _acc(tmp_path)

    def down(d):
        d["data"]["sections_unavailable"] = ["market_data"]
        d["data"]["section_timings"]["market_data"] = {
            "ms": 30000.0, "ok": False, "why": "QueryCanceledError"}
        d["data"]["management_freshness"] = {}
        d["data"]["section_timings"]["management_freshness"] = {
            "ms": 1.0, "ok": False, "why": "SKIPPED_TOTAL_BUDGET_100s"}
        return d
    _rewrite(acc, "completion", down)
    p = EP.build(acc, now=1.0)
    assert p["pmx_primary"]["value"] == EP.UNAVAILABLE
    assert p["pmx_primary"]["reason"] == (
        "READ_UNAVAILABLE:completion.management_freshness,market_data")
    assert p["management_freshness"]["value"] == EP.UNAVAILABLE
    assert p["management_freshness"]["reason"] == \
        "READ_UNAVAILABLE:completion.management_freshness"


def test_an_unavailable_envelope_keeps_every_dependent_control_unavailable(
        tmp_path):
    acc = _acc(tmp_path)
    _w(acc, "red_team", {"status": "UNAVAILABLE", "why": "TimeoutError: ",
                         "data": None})
    p = EP.build(acc, now=1.0)
    ctl = p["red_team"]["controls"]
    assert set(ctl) == set(EP.CONTROLS)                  # never an empty {}
    assert {v["value"] for v in ctl.values()} == {EP.UNAVAILABLE}
    assert {v["reason"] for v in ctl.values()} == {
        "READ_UNAVAILABLE:red_team:UNAVAILABLE"}
    assert p["red_team"]["status"]["value"] == EP.UNAVAILABLE
    assert p["venue_health"]["value"] == EP.UNAVAILABLE
    assert p["migrations"]["control"]["status"]["value"] == EP.UNAVAILABLE


def test_a_non_200_read_is_unavailable_even_with_a_json_body(tmp_path):
    acc = _acc(tmp_path)
    _w(acc, "shadow_health", {"detail": "admin token required"})
    (acc / "readback_http.tsv").write_text(
        "shadow_health\t401\t2026-10-08T06:40:00Z\n")
    p = EP.build(acc, now=1.0)
    assert p["policy_integrity"]["policyIntegrityStatus"]["reason"] == \
        "READ_UNAVAILABLE:shadow_health:HTTP_401"
    assert p["readbacks"]["shadow_health"]["http"] == "401"


def test_account_and_venue_identities_are_declared(tmp_path):
    acc = _acc(tmp_path)

    def other_acct(d):
        d["data"]["account_id"] = "paper_test_other"
        return d
    _rewrite(acc, "completion", other_acct)
    _w(acc, "venues", {"status": "OK", "data": {"health": {
        "KALSHI_HEALTH": {"domain": "POLYMARKET_HEALTH", "state": "OK"},
        "POLYMARKET_HEALTH": {"domain": "POLYMARKET_HEALTH",
                              "state": "OK"}}}})
    p = EP.build(acc, now=1.0)
    assert p["pmx_primary"]["value"] == EP.MISMATCH
    assert p["pmx_primary"]["reason"].startswith(
        "IDENTITY_MISMATCH:completion:data.account_id=paper_test_other")
    assert p["kalshi_health"]["value"] == EP.MISMATCH
    assert p["polymarket_health"]["value"]["state"] == "OK"


# ── all three services (defect 5) ─────────────────────────────────────────

def _bind_services(acc):
    for svc, sid in (("sportsassets-api", "srv-x"),
                     ("sportsassets-workers", "srv-x"),
                     ("sportsassets-market-plane", "srv-plane")):
        _w(acc, "render_lookup_%s" % svc,
           [{"service": {"id": sid if svc != "sportsassets-api" else
                         "srv-api", "name": svc, "ownerId": "tea-x"}}])


def test_the_market_plane_memory_is_reported_per_instance(tmp_path):
    acc = _acc(tmp_path)
    _bind_services(acc)
    # 2026-10-08 06:00..06:59 with 06:20..06:24 missing (an oomKilled
    # restart), rising 1 MB per minute
    vals = [{"timestamp": "2026-10-08T06:%02d:00Z" % i,
             "value": (1700 + i) * 1048576}
            for i in range(60) if not 20 <= i <= 24]
    (acc / "mem_sportsassets-market-plane.json").write_text(json.dumps([
        {"labels": [{"field": "instance", "value": "srv-plane-z2qbz"},
                    {"field": "service", "value": "srv-plane"}],
         "values": vals}]))
    p = EP.build(acc, now=1.0)
    m = p["memory"]["sportsassets-market-plane"]["srv-plane-z2qbz"]
    assert m["samples"] == 55 and m["min_mb"] == 1700.0
    # 1700..1719 and 1725..1759: the 28th of 55 samples
    assert m["max_mb"] == 1759.0 and m["p50_mb"] == 1732.0
    assert m["p95_mb"] == 1756.0
    assert m["trend_mb_per_h"] == 60.0
    assert m["gaps"]["count"] == 1 and m["gaps"]["missing_samples"] == 5
    assert m["gaps"]["longest_s"] == 360.0
    ident = p["memory_identity"]["sportsassets-market-plane"]
    assert ident == {"service_id": "srv-plane", "read": "OK",
                     "instances": ["srv-plane-z2qbz"],
                     "wrong_service_labels": [],
                     "instances_not_of_service": [], "verdict": "BOUND"}
    # the workers' instance belongs to the workers' bound id
    assert p["memory_identity"]["sportsassets-workers"]["verdict"] == "BOUND"
    assert p["memory_identity"]["sportsassets-workers"]["service_id"] == \
        "srv-x"


def test_an_unread_service_is_named_never_omitted(tmp_path):
    p = EP.build(_acc(tmp_path), now=1.0)
    assert set(p["memory"]) == set(EP.SERVICES)
    assert p["memory"]["sportsassets-market-plane"] == {
        "value": EP.UNAVAILABLE,
        "reason": "READ_UNAVAILABLE:mem_sportsassets-market-plane:"
                  "FILE_ABSENT"}
    for name, _ in EP.LOG_FILTERS:
        assert p["log_counts"]["sportsassets-market-plane_%s" % name][
            "value"] == EP.UNAVAILABLE


def test_another_services_memory_or_logs_are_a_mismatch(tmp_path):
    acc = _acc(tmp_path)
    _bind_services(acc)
    (acc / "mem_sportsassets-market-plane.json").write_text(json.dumps([
        {"labels": [{"field": "instance", "value": "srv-x-abc"},
                    {"field": "service", "value": "srv-x"}],
         "values": [{"timestamp": "2026-10-08T06:00:00Z",
                     "value": 1048576}]}]))
    _w(acc, "logs_sportsassets-market-plane_crash", {
        "logs": [{"labels": [{"name": "resource", "value": "srv-x"}],
                  "message": "crashed; restarting"}],
        "hasMore": True, "_filter": "crashed; restarting",
        "_window": "a..b", "_http": "200"})
    _w(acc, "logs_sportsassets-market-plane_churn", {
        "logs": [], "_filter": "something else", "_http": "200"})
    _w(acc, "logs_sportsassets-market-plane_memory_error", {
        "message": "unauthorized", "_filter": "MemoryError",
        "_http": "401"})
    p = EP.build(acc, now=1.0)
    ident = p["memory_identity"]["sportsassets-market-plane"]
    assert ident["verdict"] == EP.MISMATCH
    assert ident["wrong_service_labels"] == ["srv-x"]
    assert ident["instances_not_of_service"] == ["srv-x-abc"]
    lc = p["log_counts"]
    assert lc["sportsassets-market-plane_crash"]["wrong_resource"] == 1
    assert lc["sportsassets-market-plane_crash"]["lower_bound"] is True
    assert lc["sportsassets-market-plane_churn"]["value"] == EP.MISMATCH
    assert lc["sportsassets-market-plane_memory_error"]["reason"] == \
        "READ_UNAVAILABLE:logs_sportsassets-market-plane_memory_error:" \
        "HTTP_401"


def test_the_workflow_reads_memory_and_logs_for_all_three_services():
    wf = (ROOT.parent / ".github" / "workflows" /
          "pm-acceptance.yml").read_text()
    loop = ("for svc in sportsassets-api sportsassets-workers "
            "sportsassets-market-plane; do")
    steps = {s.get("name", ""): s for s in yaml.safe_load(wf)["jobs"][
        "accept"]["steps"]}
    render = [s for n, s in steps.items() if n.startswith("Render ")][0]
    packet = [s for n, s in steps.items() if n.startswith(
        "Production evidence packet")][0]
    assert loop in render["run"] and "/metrics/memory?resource=" in \
        render["run"]
    assert loop in packet["run"] and "/logs?ownerId=" in packet["run"]
    for _, text in EP.LOG_FILTERS:
        assert text in packet["run"], text


def test_the_runtime_and_paper_receipts_are_in_the_packet(tmp_path):
    acc = _acc(tmp_path)
    _w(acc, "runtime_window", {"status": "FAILED", "no_oom_minutes": 0.0})
    _w(acc, "paper_history", {"status": "UNPROVEN", "reasons": [
        "PAPER_HISTORY_BASELINE_ABSENT"]})
    _w(acc, "acceptance", {"independent_pm_state": "RED",
                           "api_pm_state": "RED"})
    p = EP.build(acc, now=1.0)
    assert p["runtime_window"]["status"] == "FAILED"
    assert p["paper_history"]["reasons"] == ["PAPER_HISTORY_BASELINE_ABSENT"]
    assert p["acceptance"]["independent_pm_state"] == {
        "value": "RED", "source": "acceptance.json",
        "path": "independent_pm_state"}
    assert p["acceptance"]["same_input_pm_state"]["value"] == EP.MISSING


def test_the_packet_names_the_judge_commit(tmp_path, monkeypatch):
    monkeypatch.setenv("JUDGE_SHA", "d" * 40)
    assert EP.build(_acc(tmp_path), now=1.0)["identity"]["judge_sha"] == \
        "d" * 40
    monkeypatch.delenv("JUDGE_SHA")
    (tmp_path / "x").mkdir()
    assert EP.build(_acc(tmp_path / "x"), now=1.0)["identity"][
        "judge_sha"] == EP.MISSING
