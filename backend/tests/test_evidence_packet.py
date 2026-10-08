"""The production evidence packet (backend/tools/evidence_packet.py) reports
only what a readback carries: every value names its file and JSON path, an
absent value is NOT_IN_READBACK (never a default pass), memory quantiles
come from Render's own samples per instance, and the packet plus every input
file is hash-manifested so it is checkable against the bytes received."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import pathlib

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
    _w(acc, "shadow_health", {"bettor": {"integrity": {
        "policyIntegrityStatus": "VERIFIED", "codeShaMatches": True,
        "decisionWritingAllowed": True, "policyVersion": "V6"}}})
    _w(acc, "pm_after", {"status": "OK", "data": {
        "pm_acceptance": {"pm_state": "RED", "evidence_input": {
            "software_red_count": 3, "held_fresh": 11, "held_required": 11}},
        "authority": {"small_live": "SHADOW"}}})
    _w(acc, "completion", {"data": {"market_data": {"pmx_primary": {
        "source": "PMX_GRPC", "requested_symbols": 58}}}})
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
        "path": "bettor.integrity.policyIntegrityStatus"}
    assert pi["decisionWritingAllowed"]["value"] is True
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
    for f in p["policy_integrity"].values():
        assert f["value"] == EP.MISSING
    assert p["pm_evidence_input"]["value"] == EP.MISSING
    assert p["canary"] == EP.MISSING and p["market_plane"] == EP.MISSING
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
        "lines": 0, "has_more": False,
        "filter": "exited cleanly; restarting", "window": "a..b"}


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
    assert "python3 -I backend/tools/evidence_packet.py acc" in wf
    assert "subject-path: acc/evidence_packet.json" in wf
    assert "implementation_sha:" in wf
    for f in ("exited cleanly; restarting", "WAS ALREADY APPLIED",
              "POLICY_CODE_DRIFT", "/metrics/memory?resource="):
        assert f in wf, f
