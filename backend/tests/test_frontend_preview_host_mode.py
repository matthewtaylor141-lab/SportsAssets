"""Frontend device harness: the production host, and saying which was measured.

frontend-preview run 37868158244 (2026-10-09) was reported as the device
evidence for the deployed frontend f90dafdd, but it measured a PREVIEW BUILD:
target_ref built on the runner and served by serve.js at 127.0.0.1:8787, its
API reads proxied to production, in headless Chromium with phone and tablet
profiles emulated. That is what the harness could do. It now also measures
the DEPLOYED SITE (`host: production`): https://command.bettortoken.com
itself, its build.json sha checked against target_ref and recorded, the read
credential added by the harness at the browser's request layer to GET/HEAD
/api/command/* on that origin only. Every preview.json / trader_accept.json
names its mode, engine and engine version and states that devices are
emulated; `engine: webkit` runs the same matrix in WebKit.

The same run's proxy counted 20 upstream errors and kept no record of which
reads failed. serve.js now records each failed read (time, path without its
query, status or error, latency), bounded at 300 with a total.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FP = ROOT / ".github" / "frontend-preview"
WF = ROOT / ".github" / "workflows" / "frontend-preview.yml"
SUITE = ROOT / "backend" / "tests" / "js" / "frontend-preview-host-mode.test.cjs"


def test_host_mode_node_suite_passes():
    node = shutil.which("node")
    assert node, "node is required to run the harness host-mode suite"
    got = subprocess.run([node, "--test", str(SUITE)], capture_output=True, text=True, timeout=120)
    out = got.stdout + got.stderr
    assert got.returncode == 0, out[-3000:]
    assert re.search(r"^# pass 5$", out, re.M), out[-3000:]
    assert re.search(r"^# fail 0$", out, re.M), out[-3000:]
    assert re.search(r"^# skipped 0$", out, re.M), out[-3000:]


def test_both_harness_scripts_use_the_shared_request_rule_and_label_their_records():
    for name in ("shots.js", "trader_accept.js"):
        src = (FP / name).read_text(encoding="utf-8")
        assert "require('./harness_env')" in src, name
        assert "H.continueRead(route, BASE)" in src, name
        assert "H.describe(BASE, launched)" in src, name
        assert "chromium.launch(" not in src, name
        # every non-GET is still aborted in the browser
        assert "if (m !== 'GET' && m !== 'HEAD') { aborted++; return route.abort(); }" in src, name


def test_failed_reads_are_counted_not_dropped():
    shots = (FP / "shots.js").read_text(encoding="utf-8")
    assert "page.on('requestfailed'" in shots
    serve = (FP / "serve.js").read_text(encoding="utf-8")
    assert "failures: [], failures_total: 0" in serve
    assert "if (r.statusCode >= 400) failure(req, u.pathname, t0, { status: r.statusCode });" in serve
    assert "failure(req, u.pathname, t0, { status: 'upstream-error'" in serve
    # the path is recorded without its query string
    assert "path: pathname" in serve and "u.search" not in serve.split("function failure")[1].split("}")[0]


def test_production_mode_checks_the_live_build_and_never_builds_or_serves():
    wf = WF.read_text(encoding="utf-8")
    assert "options: [preview, production]" in wf
    assert "options: [chromium, webkit]" in wf
    assert "if: inputs.host != 'production'" in wf
    prod = wf[wf.index('if [ "$HOST_MODE" = "production" ]; then'):wf.index("          else\n")]
    assert "BASE=https://command.bettortoken.com" in prod
    assert 'curl -sS --max-time 30 "$BASE/build.json" -o out/build.json' in prod
    assert "PRODUCTION_BUILD_DIFFERS_FROM_TARGET" in prod
    assert "serve.js" not in prod
    assert 'export PROD_READ_TOKEN="$ADMIN_TOKEN"' in prod and "unset PROD_READ_TOKEN" in prod
    # the artifact token scan still runs after both modes
    assert wf.index("TOKEN_TOO_SHORT_TO_SCAN") > wf.index('if [ "$HOST_MODE" = "production" ]; then')
