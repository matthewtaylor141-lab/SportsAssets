"""THE COLLECTION STEPS OF pm-acceptance, RUN AS WRITTEN, LOCALLY.

1. RENDER (against a local fake API).

The pm-acceptance step "Render services, deploys, instances and the event
window" is extracted from the workflow and run by bash exactly as the
runner runs it (bash --noprofile --norc -eo pipefail), with only its API
base pointed at a local HTTP server that answers in Render's shapes. RC4's
step read `/events?limit=100` once, with no window and no cursor, and
counted OOMs from that one page; this proves the repaired step

  * requests every page from live_since, each with the previous page's
    last cursor, until a short page;
  * stops at the page cap and leaves the window TRUNCATED (UNKNOWN), never
    zero failures;
  * reads the market plane like the other two, and an oomKilled event on
    any page fails the window;
  * never prints the key.

2. HISTORICAL PAPER (against the migrated scratch database, read only,
   with a fake `gh` and a fake Render connection-info). A run with no
   baseline captures one at a cutoff fixed 15 minutes behind it; a later
   run given that run's id downloads it, checks the attestation and the
   capture hash its packet recorded, and recomputes at the SAME cutoff --
   the receipts the binder then compares.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import yaml

from sportsassets.pm_bind import acceptance as PA

try:
    from tests import pm_acceptance_fixture as F
except ImportError:                                             # pragma: no cover
    import pm_acceptance_fixture as F  # type: ignore

ROOT = pathlib.Path(__file__).resolve().parents[2]
WF = ROOT / ".github" / "workflows" / "pm-acceptance.yml"
KEY = "rnd_fixture_key_must_never_print"


def _step(prefix: str) -> dict:
    st = [s for s in yaml.safe_load(WF.read_text())["jobs"]["accept"][
        "steps"] if s.get("name", "").startswith(prefix)]
    assert len(st) == 1
    return st[0]


def _step_script() -> str:
    return _step("Render services")["run"]


class FakeRender:
    """Render's /v1 shapes; per service: `extra_pages` full pages of
    in-window events before the short page, `forever` = never short."""

    def __init__(self, *, extra_pages=None, forever=(), oom_on=None):
        self.extra = extra_pages or {}
        self.forever = set(forever)
        self.oom_on = oom_on or {}
        self.requests = []
        self.raw = F.render_raw(minutes=90.0)
        self.end = None

    def svc_of(self, sid):
        return next(n for n, s in F.SIDS.items() if s == sid)

    def events(self, name, cursor):
        sid = F.SIDS[name]
        page = int(cursor.split("-p")[-1]) + 1 if cursor else 0
        if name in self.forever or page < self.extra.get(name, 0):
            items = [F.ev(sid, "server_available",
                          F.LIVE + 600 + page * 100 + i, page * 1000 + i)
                     for i in range(100)]
            for it in items:
                it["cursor"] = "%s-p%d" % (sid, page)
            if self.oom_on.get(name) == page:
                items[5] = F.oom(sid, F.LIVE + 700 + page * 100,
                                 n=page * 1000 + 5)
                items[5]["cursor"] = "%s-p%d" % (sid, page)
            return items
        return self.raw["services"][name]["event_pages"][0]

    def answer(self, path, q):
        if path == "/v1/services":
            return self.raw["services"][q["name"][0]]["lookup"]
        parts = path.split("/")
        if path.startswith("/v1/services/") and parts[-1] == "deploys":
            return self.raw["services"][self.svc_of(parts[3])]["deploys"]
        if path.startswith("/v1/services/") and parts[-1] == "events":
            assert q["limit"] == ["100"]
            self.end = q["endTime"][0]
            return self.events(self.svc_of(parts[3]),
                               q.get("cursor", [""])[0])
        if path == "/v1/metrics/memory":
            return self.raw["services"][self.svc_of(q["resource"][0])][
                "memory"]
        return None


def _run(tmp_path, fake: FakeRender):
    seen = fake.requests

    class H(BaseHTTPRequestHandler):
        def do_GET(self):                                   # noqa: N802
            u = urlparse(self.path)
            q = parse_qs(u.query)
            seen.append((u.path, q, self.headers.get("Authorization")))
            body = fake.answer(u.path, q)
            self.send_response(200 if body is not None else 404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(body if body is not None else {
                "message": "not found"}).encode())

        def log_message(self, *a):
            pass
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        script = _step_script()
        api = "API=https://api.render.com/v1"
        assert script.count(api) == 1
        script = script.replace(api, "API=http://127.0.0.1:%d/v1" %
                                srv.server_address[1])
        (tmp_path / "step.sh").write_text(script)
        (tmp_path / "backend").symlink_to(ROOT / "backend")
        env = {"PATH": os.environ["PATH"], "KEY": KEY, "SHA": F.SHA,
               "HOME": str(tmp_path)}
        out = subprocess.run(
            ["bash", "--noprofile", "--norc", "-eo", "pipefail",
             str(tmp_path / "step.sh")], cwd=tmp_path, env=env,
            capture_output=True, text=True, timeout=120)
    finally:
        srv.shutdown()
    assert out.returncode == 0, out.stderr[-2000:]
    # the key appears only in the runner's own ::add-mask:: directive
    shown = [ln for ln in (out.stdout + out.stderr).splitlines()
             if not ln.startswith("::add-mask::")]
    assert not any(KEY in ln for ln in shown)
    assert "::add-mask::%s" % KEY in out.stdout.splitlines()[0]
    assert all(a == "Bearer %s" % KEY for _, _, a in seen)
    return out, tmp_path / "acc"


def test_every_page_is_read_cursor_chained_from_live(tmp_path):
    fake = FakeRender(extra_pages={"sportsassets-workers": 2})
    out, acc = _run(tmp_path, fake)
    meta = json.loads((acc / "render_events_sportsassets-workers.json")
                      .read_text())
    assert meta["stopped"] == "SHORT_PAGE"
    sid = F.SIDS["sportsassets-workers"]
    assert [q["cursor"] for q in meta["requests"]] == [
        "", "%s-p0" % sid, "%s-p1" % sid]
    assert meta["start"] == F.iso(F.LIVE, frac=False)
    ev_q = [q for p, q, _ in fake.requests if p.endswith(
        "%s/events" % sid)]
    assert [q.get("cursor", [""])[0] for q in ev_q] == [
        "", "%s-p0" % sid, "%s-p1" % sid]
    assert all(q["startTime"] == [meta["start"]] for q in ev_q)
    summ = json.loads((acc / "render.json").read_text())
    assert {s["status"] for s in summ.values()} == {PA.CLEAN}, summ
    rw = PA.runtime_window(PA.load_runtime(acc), expected_commit=F.SHA)
    assert rw["status"] == PA.CLEAN
    assert rw["services"]["sportsassets-workers"]["pages"] == 3
    # all three services, the market plane included
    for name in F.SIDS:
        assert (acc / ("mem_%s.json" % name)).exists()
        assert (acc / ("render_lookup_%s.json" % name)).exists()
    assert "sportsassets-market-plane: CLEAN" in out.stdout


def test_an_endless_window_stops_at_the_cap_and_is_unknown(tmp_path):
    fake = FakeRender(forever={"sportsassets-api"})
    _, acc = _run(tmp_path, fake)
    meta = json.loads((acc / "render_events_sportsassets-api.json")
                      .read_text())
    assert meta["stopped"] == "PAGE_CAP" and len(meta["requests"]) == 50
    rw = PA.runtime_window(PA.load_runtime(acc), expected_commit=F.SHA)
    assert rw["status"] == PA.UNKNOWN and rw["no_oom_minutes"] is None
    assert "sportsassets-api:%s" % PA.R_RUNTIME_EVENTS_TRUNCATED in \
        rw["reasons"]


def test_an_oom_on_a_later_page_of_the_plane_fails_the_window(tmp_path):
    fake = FakeRender(extra_pages={"sportsassets-market-plane": 2},
                      oom_on={"sportsassets-market-plane": 1})
    out, acc = _run(tmp_path, fake)
    summ = json.loads((acc / "render.json").read_text())
    plane = summ["sportsassets-market-plane"]
    assert plane["status"] == PA.FAILED
    assert plane["oom_events_since_live"] == 1
    rw = PA.runtime_window(PA.load_runtime(acc), expected_commit=F.SHA)
    assert rw["status"] == PA.FAILED and rw["no_oom_minutes"] == 0.0


# ── 2. the historical PAPER step ───────────────────────────────────────────

DSN = os.environ.get("RN1X_TEST_DSN", "")
REPO = "matthewtaylor141-lab/SportsAssets"
FAKE_GH = r'''#!/usr/bin/env bash
# a fake gh: the baseline run's metadata, its artifact, its attestation
case "$1 $2" in
  "api repos/"*) cat "$FAKE_GH_DIR/run.json" ;;
  "run download")
     shift 2; d=""
     while [ $# -gt 0 ]; do [ "$1" = "--dir" ] && d="$2"; shift; done
     mkdir -p "$d/pm-acceptance-x" && cp "$FAKE_GH_DIR"/art/* "$d/pm-acceptance-x/" ;;
  "attestation verify") exit "${FAKE_GH_VERIFY_RC:-0}" ;;
  *) echo "fake gh: unexpected $*" >&2; exit 2 ;;
esac
'''


def _paper_run(tmp_path, *, baseline_run="", verify_rc="0"):
    """The step, verbatim but for the Render base, in its own work dir."""
    class H(BaseHTTPRequestHandler):
        def do_GET(self):                                   # noqa: N802
            u = urlparse(self.path)
            if u.path == "/v1/postgres":
                body = [{"postgres": {"id": "dpg-fixture",
                                      "name": "sportsassets-db"}}]
            elif u.path == "/v1/postgres/dpg-fixture/connection-info":
                body = {"externalConnectionString": DSN,
                        "password": "pw-fixture-never-printed"}
            else:
                body = {}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

        def log_message(self, *a):
            pass
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    work = tmp_path / ("run%s" % (baseline_run or "0"))
    work.mkdir()
    for d in ("backend", ".github"):
        (work / d).symlink_to(ROOT / d)
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    (bindir / "gh").write_text(FAKE_GH)
    (bindir / "gh").chmod(0o755)
    try:
        st = _step("Historical PAPER fingerprint")
        script = st["run"]
        api = "API=https://api.render.com/v1"
        assert script.count(api) == 1
        script = script.replace(api, "API=http://127.0.0.1:%d/v1" %
                                srv.server_address[1])
        env = {"PATH": "%s:%s" % (bindir, os.environ["PATH"]),
               "KEY": KEY, "REPO": REPO, "BASELINE_RUN": baseline_run,
               "PAPER_DB": st["env"]["PAPER_DB"], "GH_TOKEN": "t",
               "RUNNER_TEMP": str(tmp_path / ("rt%s" % baseline_run)),
               "FAKE_GH_DIR": str(tmp_path / "ghdir"), "HOME": str(tmp_path),
               "FAKE_GH_VERIFY_RC": verify_rc}
        pathlib.Path(env["RUNNER_TEMP"]).mkdir()
        out = subprocess.run(
            ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c",
             script], cwd=work, env=env, capture_output=True, text=True,
            timeout=300)
    finally:
        srv.shutdown()
    assert out.returncode == 0, (out.stdout[-2000:], out.stderr[-2000:])
    assert "pw-fixture-never-printed" not in out.stdout.replace(
        "::add-mask::pw-fixture-never-printed", "")
    return out, work / "acc"


def _publish_baseline(tmp_path, acc0):
    """What the baseline run's artifact holds: its capture and its packet
    (whose input manifest records the capture's hash)."""
    import hashlib
    gh = tmp_path / "ghdir"
    (gh / "art").mkdir(parents=True)
    cap = (acc0 / "paper_history_capture.json").read_bytes()
    (gh / "art" / "paper_history_capture.json").write_bytes(cap)
    (gh / "art" / "evidence_packet.json").write_text(json.dumps({
        "input_files_sha256": {"paper_history_capture.json":
                               hashlib.sha256(cap).hexdigest()}}))
    (gh / "run.json").write_text(json.dumps({
        "id": 4242, "path": ".github/workflows/pm-acceptance.yml",
        "repository": {"full_name": REPO}, "head_sha": "c" * 40,
        "event": "workflow_dispatch", "conclusion": "success",
        "created_at": "2026-10-08T04:00:00Z"}))


def test_a_baseline_run_then_a_post_run_at_its_fixed_cutoff(tmp_path):
    if not DSN:
        import pytest
        pytest.skip("needs RN1X_TEST_DSN")
    out0, acc0 = _paper_run(tmp_path)
    pre = json.loads((acc0 / "paper_history_capture.json").read_text())
    assert pre["version"] == PA.PAPER_FP_VERSION
    # the cutoff is FIXED 15 minutes behind the run, to the minute
    lag = pre["watermark"]["cutoff_lag_s"]
    assert 900.0 <= lag < 960.0 + 60.0 and pre["cutoff"].endswith(":00Z")
    assert pre["settings"]["TimeZone"] == "UTC"
    assert not (acc0 / "paper_history_pre.json").exists()
    _publish_baseline(tmp_path, acc0)
    out1, acc1 = _paper_run(tmp_path, baseline_run="4242")
    post = json.loads((acc1 / "paper_history_capture.json").read_text())
    meta = json.loads((acc1 / "paper_history_baseline.json").read_text())
    assert json.loads((acc1 / "paper_history_pre.json").read_text()) == pre
    assert post["cutoff"] == pre["cutoff"]                  # the SAME cutoff
    assert meta["attestation_verified"] is True
    assert meta["capture_sha256"] == meta["capture_sha256_in_packet"]
    assert meta["workflow_path"] == ".github/workflows/pm-acceptance.yml"
    paper = PA.load_paper(acc1)
    r = PA.paper_history(paper["pre"], paper["post"],
                         baseline=paper["baseline"],
                         deploy_started_at=PA._ts(pre["captured_at"]) + 0.5)
    assert r["status"] == PA.PROVEN, r["reasons"]


def test_an_unverified_baseline_attestation_is_recorded_and_unproven(
        tmp_path):
    if not DSN:
        import pytest
        pytest.skip("needs RN1X_TEST_DSN")
    _, acc0 = _paper_run(tmp_path)
    _publish_baseline(tmp_path, acc0)
    _, acc1 = _paper_run(tmp_path, baseline_run="4242", verify_rc="1")
    meta = json.loads((acc1 / "paper_history_baseline.json").read_text())
    assert meta["attestation_verified"] is False
    paper = PA.load_paper(acc1)
    r = PA.paper_history(paper["pre"], paper["post"],
                         baseline=paper["baseline"],
                         deploy_started_at=PA._ts(
                             paper["pre"]["captured_at"]) + 0.5)
    assert r["status"] == PA.UNPROVEN
    assert PA.R_PAPER_BASELINE_UNVERIFIED + ":ATTESTATION" in r["reasons"]
