"""COMMAND OPS RELEASE 1: THE OPERATIONS DESK (/ops) AND THE GLOBAL HEADER.

Static checks on the committed frontend (nothing here opens a socket):

  * the command host serves /ops as a REWRITE to command/ops.html, below the
    /api/command/* proxy and above the host catch-all;
  * every Command page that loads the Command Final layer also loads the
    global Command Ops layer after it (the executive header is on every page);
  * Operations is the first destination of the navigation, the palette and
    the homepage strip;
  * the desk is READ ONLY: no verb but GET, no form, the only stream is the
    committed paper ledger SSE, every read path is under /api/command/;
  * a value the API does not serve renders DATA NOT AVAILABLE, never 0;
  * the refusal table classifies every code as SOFTWARE / ECONOMIC /
    UNCLASSIFIED and an unknown code is UNCLASSIFIED, never economic;
  * the frontend reports its own build (command/build.json), LOCAL outside
    Netlify.
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
TOML = ROOT / "netlify.toml"
FRONT = ROOT / "frontend"
COMMAND = FRONT / "public" / "command"
HOST = "command.bettortoken.com"
OPS_HTML = (COMMAND / "ops.html").read_text()
OPS_JS = (COMMAND / "command-ops.js").read_text()
DESK_JS = (COMMAND / "ops-desk.js").read_text()
TAX_JS = (COMMAND / "ops-taxonomy.js").read_text()


def _redirects():
    try:
        import tomllib
    except ImportError:                                        # pragma: no cover
        pytest.skip("tomllib needs Python 3.11+")
    return tomllib.loads(TOML.read_text())["redirects"]


def _index(pred):
    for i, r in enumerate(_redirects()):
        if pred(r):
            return i
    return None


def test_ops_is_a_rewrite_below_the_api_proxy_and_above_the_catch_all():
    ops = _index(lambda r: r["from"] == "https://%s/ops" % HOST)
    api = _index(lambda r: r["from"] == "/api/command/*")
    catch = _index(lambda r: r["from"] == "https://%s/*" % HOST)
    assert None not in (ops, api, catch)
    rule = _redirects()[ops]
    assert rule["to"] == "/command/ops.html"
    assert rule["status"] == 200 and rule.get("force") is True
    assert api < ops < catch


def test_the_build_record_is_never_cached():
    try:
        import tomllib
    except ImportError:                                        # pragma: no cover
        pytest.skip("tomllib needs Python 3.11+")
    headers = tomllib.loads(TOML.read_text()).get("headers", [])
    paths = {h["for"]: h["values"] for h in headers}
    for p in ("/command/build.json", "/build.json"):
        assert paths[p]["Cache-Control"] == "no-cache"


def test_every_command_final_page_also_loads_the_ops_layer_after_it():
    pages = [p for p in COMMAND.glob("*.html") if "command-final.js" in p.read_text()]
    assert len(pages) >= 10
    for p in pages:
        html = p.read_text()
        assert 'src="command-ops.js"' in html, p.name
        assert html.index('src="command-final.js"') < html.index('src="command-ops.js"'), p.name
        assert 'href="command-ops.css"' in html, p.name


def test_the_ops_page_loads_its_layers_in_order_with_relative_references():
    refs = re.findall(r'(?:href|src)="([^"]+\.(?:css|js))"', OPS_HTML)
    # device-fit.css last: touch targets, the shell and the notch (RC6 device acceptance)
    assert refs == ['hq2-brand.css', 'hq5-workspace.css', 'hq6-complete.css', 'brand/brand.css',
                    'command-final.css', 'command-ops.css', 'ops-desk.css', 'device-fit.css',
                    'hq2-shell.js', 'ops-taxonomy.js', 'hq5-workspace.js', 'hq6-complete.js',
                    'command-final.js', 'command-ops.js', 'ops-desk.js']
    for panel in ("funnel", "coverage", "opportunities", "blotter", "agents", "refusals",
                  "pinnapi", "capital", "incidents"):
        assert 'id="%s"' % panel in OPS_HTML
    assert "<form" not in OPS_HTML and 'type="submit"' not in OPS_HTML


def test_operations_is_the_first_destination():
    shell = (COMMAND / "hq2-shell.js").read_text()
    hq6 = (COMMAND / "hq6-complete.js").read_text()
    hq5 = (COMMAND / "hq5-workspace.js").read_text()
    assert re.search(r"var links=\[\s*\['ops','/ops'", shell)
    assert "var wanted=[['/ops'," in hq6
    assert re.search(r"var ROUTES=\[\s*\{name:'Operations Desk'[^}]*href:'/ops'", hq5)
    assert "ops-home-entry" in OPS_JS and "href = '/ops'" in OPS_JS


@pytest.mark.parametrize("name", ["command-ops.js", "ops-desk.js"])
def test_the_ops_layers_are_read_only(name):
    src = (COMMAND / name).read_text()
    # no verb but GET; no write-shaped call of any kind
    assert set(re.findall(r"method\s*:\s*'([A-Z]+)'", src)) <= {"GET"}
    for bad in ("'POST'", "'PUT'", "'PATCH'", "'DELETE'", "sendBeacon", "XMLHttpRequest", "WebSocket("):
        assert bad not in src, bad
    # every API path named is a COMMAND read path
    for path in re.findall(r"'(/api/[^'?]+)", src):
        assert path.startswith("/api/command/"), path


def test_the_only_stream_is_the_committed_paper_ledger():
    assert DESK_JS.count("new EventSource(") == 1
    assert "'/api/command/paper/stream'" in DESK_JS
    assert "new EventSource(" not in OPS_JS


def test_the_broker_refuses_anything_outside_command_and_never_polls_faster():
    assert "if (String(url).indexOf('/api/command/') !== 0)" in OPS_JS
    # the coverage read keeps the existing pages' 120 s floor
    assert "'/api/command/coverage': 120000" in OPS_JS
    assert "if (document.hidden) { return; }" in OPS_JS


def test_missing_values_are_named_not_zeroed():
    assert "var DNA = 'DATA NOT AVAILABLE'" in DESK_JS
    # the four owner funnel stages R29 does not measure are DATA NOT AVAILABLE with a reason
    for stage in ("MARKETS MATCHED", "PROBABILITY SUPPORTED", "FRESH", "POSITIVE EV"):
        m = re.search(r"\{h: '%s', k: null, why: '([^']+)'\}" % re.escape(stage), DESK_JS)
        assert m and len(m.group(1)) > 20, stage
    # an unmeasured stage is never summed as zero
    assert "t[s.k] = measured ? sum : null;" in DESK_JS


def test_the_health_rule_is_deterministic_and_stated():
    for word in ("NOT MEASURED", "BROKEN", "NO_CURRENT_EVENTS", "DEGRADED", "HEALTHY"):
        assert word in DESK_JS
    assert "The provider feed existing is never enough." in DESK_JS


def _table():
    rows = re.findall(r'^\s*"([A-Z0-9_]+)":\["(SOFTWARE|ECONOMIC|UNCLASSIFIED)","([0-9A-Z_]+)"', TAX_JS, re.M)
    stages = json.loads(re.search(r"var STAGES = (\[[^\]]+\]);", TAX_JS).group(1))
    return rows, stages


def test_the_refusal_table_is_complete_and_three_valued():
    rows, stages = _table()
    assert len(rows) >= 150
    codes = [r[0] for r in rows]
    assert len(codes) == len(set(codes))
    for code, cls, stage in rows:
        assert stage in stages, (code, stage)
    by = {r[0]: r[1] for r in rows}
    # the economic refusal the incident keeps (inputs validated, threshold unchanged)
    assert by["BELOW_MIN_GROSS_EDGE"] == "ECONOMIC"
    assert by["NET_EV_NOT_POSITIVE_AFTER_FEES"] == "ECONOMIC"
    # capability gaps are software, never economic
    assert by["NO_COMPLETED_GAME_TERMS_FOR_THIS_SPORT"] == "SOFTWARE"
    assert by["MARKET_NOT_IN_SUPPORTED_SET"] == "SOFTWARE"
    assert by["NO_PINNACLE_ON_EVENT"] == "SOFTWARE"
    assert by["9_UNCLASSIFIED_REFUSAL"] == "UNCLASSIFIED"


def test_an_unknown_code_is_unclassified():
    body = TAX_JS.split("function classify(code) {")[1].split("\n  }\n")[0]
    assert "return {code: c, cls: 'UNCLASSIFIED'" in body
    assert "never assumed economic" in body
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    # frontend/package.json is "type": "module", so the browser script is
    # evaluated as a classic script in a VM context, exactly as a page runs it
    out = subprocess.run([node, "-e", (
        "const vm=require('vm'),fs=require('fs');const c={};c.globalThis=c;"
        "vm.runInNewContext(fs.readFileSync(%r,'utf8'),c);const t=c.BTOpsTaxonomy;"
        "console.log(JSON.stringify([t.classify('NEVER_SEEN_CODE').cls,t.classify('BELOW_MIN_GROSS_EDGE:detail').cls,"
        "t.classify('PINNAPI_PRIMARY_SOMETHING_NEW').cls,t.classify('').cls,t.classify('QUOTE_STALE').stage]))"
    ) % str(COMMAND / "ops-taxonomy.js")], capture_output=True, text=True, timeout=30, check=True)
    assert json.loads(out.stdout) == ["UNCLASSIFIED", "ECONOMIC", "SOFTWARE", "UNCLASSIFIED", "2_FRESHNESS"]


def test_venue_slugs_with_an_event_class_prefix_bucket_to_their_league():
    # production rows carry slugs like "aec-mlb-atl-lad-2026-10-04"; the first
    # token is the venue's event class, not the league (they all read "Other")
    assert "TOKEN_SPORT[toks[1]]" in OPS_JS
    assert "lab.competition ? O.sportOf(lab.competition" in DESK_JS
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    out = subprocess.run([node, "-e", (
        "const vm=require('vm'),fs=require('fs');const c={location:{pathname:'/ops',hostname:'localhost'},"
        "document:{addEventListener(){},querySelector(){return null},readyState:'loading'},"
        "addEventListener(){},setTimeout(){},clearTimeout(){},setInterval(){},clearInterval(){}};c.window=c;c.globalThis=c;"
        "vm.runInNewContext(fs.readFileSync(%r,'utf8'),c);const s=c.BTOps.sportOf;"
        "console.log(JSON.stringify([s('aec-mlb-atl-lad-2026-10-04'),s('aec-nfl-kc-buf-2026-10-04'),s('aec-cfb-ala-uga-2026-10-04'),"
        "s('MLB'),s('College Football'),s('americanfootball_ncaaf'),s('nba-lal-bos-2026-10-04'),s('aec-xyz-foo-bar'),"
        "s('soccer_epl'),s('aec-xyz-foo','soccer')]))"
    ) % str(COMMAND / "command-ops.js")], capture_output=True, text=True, timeout=30, check=True)
    assert json.loads(out.stdout) == ["MLB", "NFL", "NCAAF", "MLB", "NCAAF", "NCAAF", "NBA", "Other", "Soccer", "Soccer"]


def test_the_frontend_reports_its_own_build(tmp_path):
    pkg = json.loads((FRONT / "package.json").read_text())
    assert pkg["scripts"]["build"].endswith("&& node scripts/write-build-info.mjs dist")
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    script = FRONT / "scripts" / "write-build-info.mjs"
    env = {"PATH": "/usr/bin:/bin", "COMMIT_REF": "a" * 40, "BRANCH": "claude/session-njaewf", "CONTEXT": "production",
           "DEPLOY_ID": "0123456789abcdef01234567", "BUILD_ID": "fedcba9876543210fedcba98", "SITE_NAME": "polymarkettracker1"}
    subprocess.run([node, str(script), str(tmp_path / "netlify")], env=env, check=True, capture_output=True, timeout=30)
    rec = json.loads((tmp_path / "netlify" / "command" / "build.json").read_text())
    assert rec["schema"] == "bt.frontend.build.v1" and rec["sha"] == "a" * 40
    assert rec["source"] == "NETLIFY" and rec["context"] == "production"
    assert rec["deploy_id"] == "0123456789abcdef01234567" and rec["site"] == "polymarkettracker1"
    env = {"PATH": "/usr/bin:/bin", "COMMIT_REF": "not-a-sha"}
    subprocess.run([node, str(script), str(tmp_path / "bad")], env=env, check=True, capture_output=True, timeout=30)
    rec = json.loads((tmp_path / "bad" / "command" / "build.json").read_text())
    assert rec["sha"] is None and rec["sha_why"]
    env = {"PATH": "/usr/bin:/bin"}
    subprocess.run([node, str(script), str(tmp_path / "local")], env=env, check=True, capture_output=True,
                   timeout=30, cwd=str(ROOT))
    rec = json.loads((tmp_path / "local" / "command" / "build.json").read_text())
    assert rec["source"] == "LOCAL" and rec["context"] == "LOCAL" and rec["deploy_id"] is None


def test_small_live_stays_shadow_in_every_label():
    assert "SMALL LIVE stays SHADOW" in OPS_HTML
    assert "FUNDED SUBMISSION ENABLED" in OPS_JS            # shown red if it ever were
    assert "NON-FUNDED · NO ORDER CAN BE SENT" in OPS_JS


def test_the_equity_treatment_object_is_rendered_as_text():
    # production serves equity_treatment as {rule, unmarked_positions, ..., text};
    # passing it to esc() printed "[object Object]" in the Capital panel (readback
    # run 37242010767)
    assert "esc(P.equity_treatment" not in DESK_JS
    assert "treatment(P.equity_treatment)" in DESK_JS
    body = DESK_JS.split("function treatment(tr) {")[1].split("\n      }\n")[0]
    assert "tr.unmarked_positions" in body and "tr.text" in body
