"""pm-acceptance CANNOT POST BY DEFAULT AND CANNOT CALL ITSELF SIGNED
(PM review of RC4, 2026-10-08).

  * post_receipt defaulted to "on": a dispatch that never asked appended a
    release receipt to the serving API (the receipt for 08828d04 reads
    green=false). The default is now "off", the POST step runs only on
    exactly "on", and .github/pm-acceptance/post_receipt.py -- the only
    sender -- sends nothing unless its mode is exactly "on" (GitHub's
    `==` is case-insensitive). Proven statically over the workflow and with
    a fake transport over the posting code.
  * A new receipt supersedes earlier ones BY REFERENCE (supersedes), never
    by deleting: the sender has no other method than one POST, and the
    API's receipt tables keep their append-only triggers (migration 315).
  * The packet was attested with continue-on-error and never verified. The
    attestation is now VERIFIED (gh attestation verify --signer-workflow)
    in a step that is not continue-on-error, and signed_acceptance.json is
    True only for a verified signature AND matching SHA256SUMS AND a GREEN
    independent state: a green collection run or a signature is not proof
    the contents pass.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import pathlib
import re

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
WF = ROOT / ".github" / "workflows" / "pm-acceptance.yml"
PMA = ROOT / ".github" / "pm-acceptance"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, PMA / ("%s.py" %
                                                               name))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


PR = _mod("post_receipt")
SA = _mod("signed_acceptance")


def _doc():
    return yaml.safe_load(WF.read_text())


def _steps():
    return _doc()["jobs"]["accept"]["steps"]


def _step(prefix):
    hits = [s for s in _steps() if s.get("name", "").startswith(prefix)]
    assert len(hits) == 1, prefix
    return hits[0]


# ── zero POSTs unless exactly "on" ─────────────────────────────────────────

def test_post_receipt_defaults_off_and_is_a_two_value_choice():
    # PyYAML reads the bare `on:` key as True
    inputs = _doc()[True]["workflow_dispatch"]["inputs"]
    pr = inputs["post_receipt"]
    assert pr["default"] == "off"
    assert pr["type"] == "choice" and pr["options"] == ["off", "on"]
    assert pr.get("required") is False


def test_the_post_step_runs_only_on_on_and_only_through_the_sender():
    st = _step("POST the release receipt")
    assert st["if"] == "${{ inputs.post_receipt == 'on' }}"
    assert st["env"]["POST_RECEIPT"] == "${{ inputs.post_receipt }}"
    assert "python3 -I judge/.github/pm-acceptance/post_receipt.py acc" in \
        st["run"]
    assert "continue-on-error" not in st


def test_no_step_sends_anything_to_the_api_but_the_sender():
    """Every other step only GETs: no curl method, body or upload flag, and
    no gh api call with a method or a field (which would make it a POST)."""
    curl_send = re.compile(r"(\s-X\s|--request|--data|\s-d\s|--form|"
                           r"\s-F\s|--upload-file|\s-T\s|--json)")
    gh_send = re.compile(r"(\s-X\s|--method|\s-f\s|\s-F\s|--field|"
                         r"--raw-field|--input)")
    seen = {"curl": 0, "gh api": 0}
    for s in _steps():
        for ln in (s.get("run") or "").splitlines():
            if "curl " in ln:
                seen["curl"] += 1
                assert not curl_send.search(ln), (s.get("name"), ln)
            if "gh api " in ln:
                seen["gh api"] += 1
                assert not gh_send.search(ln), (s.get("name"), ln)
    assert seen["curl"] >= 5 and seen["gh api"] >= 3      # the scan bites
    src = (PMA / "post_receipt.py").read_text()
    assert src.count('"POST"') == 1
    for verb in ('"PUT"', '"PATCH"', '"DELETE"'):
        assert verb not in src


@pytest.mark.parametrize("mode", [None, "", "off", "OFF", "ON", "On",
                                  " on", "on ", "yes", "true", "1"])
def test_zero_posts_unless_exactly_on(tmp_path, mode):
    calls = []

    def transport(*a):
        calls.append(a)
        return 200, "{}"
    (tmp_path / "receipt_body.json").write_text(json.dumps({"x": 1}))
    env = {"ADMIN_TOKEN": "tok-secret", "BASE": "https://api.example"}
    if mode is not None:
        env["POST_RECEIPT"] = mode
    assert PR.main(["x", str(tmp_path)], env=env, transport=transport) == 0
    assert calls == []
    out = json.loads((tmp_path / "receipt_post.json").read_text())
    assert out["posted"] is False and out["why"] == PR.R_NOT_REQUESTED
    assert PR.post({"x": 1}, mode=mode, base="b", token="t",
                   transport=transport)["posted"] is False
    assert calls == []


def test_on_posts_exactly_once_to_the_receipt_endpoint(tmp_path):
    calls = []

    def transport(method, url, headers, data):
        calls.append((method, url, headers, json.loads(data)))
        return 200, '{"receipt_id": "rel:aaaaaaaaaaaa:2"}'
    (tmp_path / "receipt_body.json").write_text(json.dumps(
        {"tested_sha": "a" * 40}))
    (tmp_path / "pm_before.json").write_text(json.dumps({
        "status": "OK", "data": {"release_receipt": {
            "receipt_id": "rel:aaaaaaaaaaaa:1"}}}))
    env = {"POST_RECEIPT": "on", "ADMIN_TOKEN": "tok-secret",
           "BASE": "https://api.example/",
           "SUPERSEDES_RECEIPT_ID": "rel:08828d047660:1791400000"}
    assert PR.main(["x", str(tmp_path)], env=env, transport=transport) == 0
    assert len(calls) == 1
    method, url, headers, body = calls[0]
    assert method == "POST"
    assert url == "https://api.example/api/admin/red-team/release-receipt"
    assert headers["X-Admin-Token"] == "tok-secret"
    # supersedes BY REFERENCE: the receipt the API reads now, and the
    # operator's named earlier receipt (e.g. 08828d04's green=false one)
    assert body["supersedes"] == ["rel:aaaaaaaaaaaa:1",
                                  "rel:08828d047660:1791400000"]
    rec = (tmp_path / "receipt_post.json").read_text()
    assert "tok-secret" not in rec
    assert json.loads(rec)["accepted"] is True


@pytest.mark.parametrize("named", ["08828d04", "rel:08828D047660:1",
                                   "rel:08828d04:1", "DROP TABLE x"])
def test_a_malformed_supersedes_id_posts_nothing_and_fails(tmp_path, named):
    calls = []
    (tmp_path / "receipt_body.json").write_text(json.dumps({"x": 1}))
    env = {"POST_RECEIPT": "on", "ADMIN_TOKEN": "t", "BASE": "b",
           "SUPERSEDES_RECEIPT_ID": named}
    assert PR.main(["x", str(tmp_path)], env=env,
                   transport=lambda *a: calls.append(a)) == 1
    assert calls == []


def test_the_workflow_validates_the_optional_ids_before_anything_runs():
    run = _step("Validate the SHA")["run"]
    assert "'^rel:[0-9a-f]{12}:[0-9]+$'" in run
    assert "'^[0-9]{1,20}$'" in run
    assert _steps()[0]["name"].startswith("Validate the SHA")


def test_release_receipts_stay_append_only_in_the_database():
    mig = (ROOT / "backend" / "migrations" /
           "315_red_team_closeout.sql").read_text()
    assert re.search(r"CREATE TRIGGER red_team_release_append_only\s+"
                     r"BEFORE UPDATE OR DELETE ON red_team_release_receipts",
                     mig)
    assert re.search(r"CREATE TRIGGER red_team_control_append_only\s+"
                     r"BEFORE UPDATE OR DELETE ON red_team_control_receipts",
                     mig)


# ── attestation VERIFIED; signed acceptance is not "the job was green" ────

def test_the_attestation_is_verified_in_a_step_that_can_fail_the_job():
    steps = _steps()
    names = [s.get("name", "") for s in steps]
    att = next(i for i, s in enumerate(steps)
               if "attest-build-provenance" in str(s.get("uses")))
    ver = names.index("Verify the attestation and SHA256SUMS "
                      "(signed acceptance)")
    assert att < ver
    for i in (att, ver):
        assert "continue-on-error" not in steps[i], names[i]
        assert steps[i]["if"] == "always()"
    run = steps[ver]["run"]
    for subj in ("acc/evidence_packet.json", "acc/SHA256SUMS"):
        assert ('gh attestation verify %s --repo "$REPO" --signer-workflow '
                '"$REPO/$WF"' % subj) in run
    assert "WF=.github/workflows/pm-acceptance.yml" in run
    assert "python3 -I judge/.github/pm-acceptance/signed_acceptance.py " \
        "acc" in run
    assert run.strip().endswith("exit $rc")
    assert steps[ver]["env"]["GH_TOKEN"] == "${{ github.token }}"
    perms = _doc()["permissions"]
    assert perms["id-token"] == "write" and perms["attestations"] == "write"


def test_only_the_viewport_capture_may_continue_on_error():
    soft = [s.get("name") for s in _steps() if s.get("continue-on-error")]
    assert soft == ["Frontend / iPad / mobile viewports (read only)"]


def _acc(tmp_path, state="GREEN", files=("acceptance.json",)):
    acc = tmp_path / "acc"
    acc.mkdir(parents=True)
    (acc / "acceptance.json").write_text(json.dumps(
        {"independent_pm_state": state}))
    (acc / "evidence_packet.json").write_text(json.dumps({"acceptance": {
        "independent_pm_state": {"value": state,
                                 "source": "acceptance.json",
                                 "path": "independent_pm_state"}}}))
    names = ("evidence_packet.json",) + tuple(files)
    (acc / "SHA256SUMS").write_text("".join(
        "%s  %s\n" % (hashlib.sha256((acc / n).read_bytes()).hexdigest(), n)
        for n in sorted(names)))
    return acc


def test_verified_matching_and_green_is_the_only_signed_acceptance(tmp_path):
    rec = SA.build(_acc(tmp_path), verify_packet_rc=0, verify_sums_rc="0")
    assert rec["signed_acceptance"] is True and rec["reasons"] == []


def test_a_signature_is_not_proof_the_contents_pass(tmp_path):
    acc = _acc(tmp_path, state="RED")
    rec = SA.build(acc, verify_packet_rc=0, verify_sums_rc=0)
    assert rec["signature_verified"] is True and rec["sha256sums_match"]
    assert rec["signed_acceptance"] is False
    assert rec["reasons"] == ["CONTENTS_NOT_GREEN:RED"]
    # the truthful RED verdict does not fail the job
    assert SA.main(["x", str(acc)], env={"VERIFY_PACKET_RC": "0",
                                         "VERIFY_SUMS_RC": "0"}) == 0


@pytest.mark.parametrize("p_rc,s_rc", [(1, 0), (0, 1), ("unset", "unset")])
def test_an_unverified_attestation_is_never_signed_and_fails_the_job(
        tmp_path, p_rc, s_rc):
    acc = _acc(tmp_path)
    rec = SA.build(acc, verify_packet_rc=p_rc, verify_sums_rc=s_rc)
    assert rec["signed_acceptance"] is False
    assert rec["signature_verified"] is False
    assert any(r.startswith(SA.R_SIGNATURE_NOT_VERIFIED)
               for r in rec["reasons"])
    env = {} if p_rc == "unset" else {"VERIFY_PACKET_RC": str(p_rc),
                                      "VERIFY_SUMS_RC": str(s_rc)}
    assert SA.main(["x", str(acc)], env=env) == 1


def test_a_tampered_or_incomplete_manifest_is_never_signed(tmp_path):
    acc = _acc(tmp_path)
    (acc / "acceptance.json").write_text(json.dumps(
        {"independent_pm_state": "GREEN", "edited": True}))
    rec = SA.build(acc, verify_packet_rc=0, verify_sums_rc=0)
    assert rec["signed_acceptance"] is False
    assert "SHA256SUMS_MISMATCH:acceptance.json" in rec["reasons"]
    assert SA.main(["x", str(acc)], env={"VERIFY_PACKET_RC": "0",
                                         "VERIFY_SUMS_RC": "0"}) == 1
    acc2 = _acc(tmp_path / "b", files=())
    rec = SA.build(acc2, verify_packet_rc=0, verify_sums_rc=0)
    assert "SHA256SUMS_DOES_NOT_COVER:acceptance.json" in rec["reasons"]
    (acc2 / "SHA256SUMS").unlink()
    assert SA.build(acc2, verify_packet_rc=0, verify_sums_rc=0)[
        "reasons"][0] == SA.R_SUMS_ABSENT


# ── the independent re-run and the PAPER step ─────────────────────────────

def test_the_independent_rerun_rebinds_no_oom_and_paper_from_the_job():
    run = _step("Re-read PM acceptance and re-run the package harness")["run"]
    assert "PA.independent(ev, runtime=PA.load_runtime(acc), " \
           "paper=PA.load_paper(acc)" in run
    assert "expected_commit=sha" in run
    assert "mine = H.evaluate(ev2, spec)" in run
    assert "same = H.evaluate(ev, spec)" in run
    assert 'same["pm_state"] != api["pm_state"]' in run
    assert '"independent_pm_state": mine["pm_state"]' in run


def test_the_paper_fingerprint_is_read_only_and_bound_to_its_baseline():
    st = _step("Historical PAPER fingerprint")
    run = st["run"]
    assert "default_transaction_read_only=on" in run
    assert "-c TimeZone=UTC" in run
    assert '-v cutoff="$CUTOFF" -f "$SQL"' in run
    assert "SQL=judge/.github/pm-acceptance/paper_history_fingerprint.sql" \
        in run
    assert 'gh run download "$BASELINE_RUN"' in run
    assert 'gh attestation verify "$pkt"' in run
    assert "capture_sha256_in_packet" in run
    assert "date -u -d '-15 minutes'" in run
    assert st["env"]["BASELINE_RUN"] == \
        "${{ inputs.paper_baseline_run_id }}"
    # the step order: the receipts exist before the harness re-run
    names = [s.get("name", "") for s in _steps()]
    assert names.index(st["name"]) < names.index(
        "Re-read PM acceptance and re-run the package harness here")


def test_the_fingerprint_sql_passes_the_read_only_guard():
    """The research-sql guard, as the step applies it to the file."""
    sql = (PMA / "paper_history_fingerprint.sql").read_text()
    kw = re.compile(r"\b(insert|update|delete|drop|alter|truncate|grant|"
                    r"revoke|create|copy|vacuum|reindex|refresh|call|do|"
                    r"merge|lock|set\s+role)\b", re.I)
    body = "\n".join(re.sub(r"--.*$", "", ln) for ln in sql.splitlines())
    assert not kw.search(body)
    assert not re.search(r"^\s*\\", sql, re.M)
    assert sql.count(":'cutoff'") >= 10


def test_the_render_step_pages_the_whole_event_window():
    run = _step("Render services, deploys, instances")["run"]
    assert "for svc in sportsassets-api sportsassets-workers " \
           "sportsassets-market-plane; do" in run
    assert "startTime=$S&endTime=$END${cursor:+&cursor=$cursor}" in run
    assert "stop=PAGE_CAP" in run and "stop=SHORT_PAGE" in run
    assert "render_events_$svc.json" in run
    assert "PA.render_summary(PA.load_runtime" in run
    # the old unpaginated read (?limit=100, no window, no cursor) is gone
    assert '/events?limit=100"' not in WF.read_text()


# ── the judge is the workflow's own commit, never the release ─────────────

def test_the_judging_code_runs_from_the_workflows_own_commit():
    """Every script that judges the release (binder, harness spec, packet
    builder, receipt sender, signed record, fingerprint SQL, viewports)
    runs from judge/, the workflow's own commit: a release cannot weaken
    the code that certifies it, and a baseline can be captured on a
    release that predates that code (RC4 has none of it)."""
    steps = _steps()
    co = [s for s in steps if str(s.get("uses", "")).startswith(
        "actions/checkout")]
    assert [c["with"]["ref"] for c in co] == ["${{ inputs.sha }}",
                                             "${{ github.sha }}"]
    assert co[1]["with"]["path"] == "judge"
    assert co[1]["with"]["persist-credentials"] is False
    runs = "\n".join(s.get("run") or "" for s in steps)
    # every interpreter call names a judge/ path (or reads stdin)
    for m in re.finditer(r"(python3 -I|node)\s+(\S+)", runs):
        assert m.group(2) == "-" or m.group(2).startswith("judge/"), \
            m.group(0)
    for m in re.finditer(r"sys\.path\.insert\(0, \"([^\"]+)\"\)", runs):
        assert m.group(1) == "judge/backend", m.group(0)
    assert 'open("judge/backend/sportsassets/pm_evidence/data/' \
        'acceptance_spec.json")' in runs
    assert "PYTHONPATH=backend" not in runs
    assert _step("Production evidence packet")["env"]["JUDGE_SHA"] == \
        "${{ github.sha }}"
