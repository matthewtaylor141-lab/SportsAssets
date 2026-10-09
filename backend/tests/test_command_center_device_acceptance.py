"""Command Center desktop and mobile: every page, working controls, the notch,
data to screen (scorecard 14, Command Center desktop and mobile).

The device matrix (.github/frontend-preview/shots.js) measured Command, Floor
and Trader only, never pressed a control, ran without the notch, and read
horizontal overflow against a layout viewport a phone widens to fit the page.
It now covers every page -- the agent pages (/derek /xavier /audrey /karen
/allocator /archer /scout), /ops, /positions and one position room -- on all
five device profiles, taps every declared control (CONTROLS), checks that a
tap can reach each control on screen (REACHABILITY), applies each device's
safe-area insets (SAFE AREA) and checks a background layer's own controls
against the bars above it (LAYER CHILDREN). The Trader acceptance
(.github/frontend-preview/trader_accept.js) compares every position AND
every order with the API snapshot (TRADER FIDELITY), and refuses the feed
(OUTAGE) to prove the page stops claiming anything is current.

Found and fixed on the production frontend (f90dafdd; local preview against
the RC6 API on an empty database and a synthetic trader snapshot built by the
backend's own trader_mode.build_snapshot):
  agent.html     the agent navigation (9 links) under the shell's page bar,
                 strip and rail on every device; the workspace hero 209 px
                 past an iPad-landscape screen
  position.html  the bar (5 links + Refresh) under the page bar and strip, the
                 page under the rail
  every shell    phone tab bar on the home indicator; side rail under the
  page           landscape notch and past the bottom of a landscape phone;
                 Company Pulse / its list, ops jumps, sort heads, selects,
                 agent and position controls under 44 px on touch
  trader         a RESTING order past its recorded expiry drawn as the
                 standing limit; Xavier's review time nowhere on the page; a
                 fixed "DEREK -> XAVIER" instead of the recorded agents
These tests pin the fixes and run the harness's node suites (CI has node).
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMMAND = ROOT / "frontend" / "public" / "command"
PREVIEW = ROOT / ".github" / "frontend-preview"
JS_TESTS = ROOT / "backend" / "tests" / "js"


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _node_suite(name: str, count: int) -> None:
    node = shutil.which("node")
    assert node, "node is required to run the device-acceptance suites"
    got = subprocess.run([node, "--test", str(JS_TESTS / name)], capture_output=True, text=True, timeout=180)
    out = got.stdout + got.stderr
    assert got.returncode == 0, out[-3000:]
    assert re.search(r"^# tests %d$" % count, out, re.M), out[-3000:]
    assert re.search(r"^# pass %d$" % count, out, re.M), out[-3000:]
    assert re.search(r"^# fail 0$", out, re.M), out[-3000:]
    assert re.search(r"^# skipped 0$", out, re.M), out[-3000:]


# --- the harness node suites -------------------------------------------------


def test_controls_suite_passes():
    _node_suite("frontend-preview-controls.test.cjs", 9)


def test_reachability_suite_passes():
    _node_suite("frontend-preview-reach.test.cjs", 7)


def test_safe_area_suite_passes():
    _node_suite("frontend-preview-safe-area.test.cjs", 5)


def test_layer_children_suite_passes():
    _node_suite("frontend-preview-layers.test.cjs", 3)


def test_trader_fidelity_suite_passes():
    _node_suite("trader-accept-fidelity.test.cjs", 10)


def test_trader_core_expiry_suite_passes():
    _node_suite("trader-core-expiry.test.cjs", 5)


def test_trader_game_fallback_suite_passes():
    _node_suite("trader-game-fallback.test.cjs", 3)


# --- the matrix covers every page on every device ---------------------------


def test_every_page_runs_on_all_five_devices():
    src = read(PREVIEW / "shots.js")
    for page in ("derek", "xavier", "audrey", "karen", "allocator", "archer", "scout", "ops", "positions", "room"):
        assert ("%s: '/" % page) in src, page
    assert "const DEVICE_ORDER = ['desktop', 'iphone', 'iphone_landscape', 'ipad_portrait', 'ipad_landscape'];" in src
    assert ".concat(AGENTS.concat(['ops', 'positions', 'room']).reduce((a, pg) => a.concat(DEVICE_ORDER.map(dev => [dev, pg])), []));" in src
    # Command on the fifth profile too (it had iPhone landscape for Floor and Trader only)
    assert "['iphone_landscape', 'command']," in src
    # the original 17 views keep their order (the scorecard units keep their names)
    assert src.index("['desktop', 'command'], ['desktop', 'floor'], ['desktop', 'trader'],") < src.index("['iphone_landscape', 'command'],")
    # a partial local run is named as such; a missing room is UNMEASURED, never a pass
    assert "views_planned: VIEWS.length, views_run: records.length, subset: ONLY.length ? ONLY : null," in src
    assert "records.push({ view: name, device: dev, page: pg, viewport: d.viewport, status: null, signed_in: null, unmeasured: (room && room.why) || 'NO_ROOM_KEY' });" in src
    # the room key is a production identifier: only a short hash is written out
    assert "key_sha256_12: got.key ? crypto.createHash('sha256').update(got.key).digest('hex').slice(0, 12) : null" in src
    assert "room_key_sha256_12: room.key_sha256_12" in src and "room_key: room.key" not in src


def test_measurements_made_stricter_never_looser():
    src = read(PREVIEW / "shots.js")
    # overflow against the initial containing block (a phone widens innerWidth to fit the page)
    assert "overflow_px: document.documentElement.scrollWidth - document.documentElement.clientWidth," in src
    assert "overflow_px: document.documentElement.scrollWidth - innerWidth," not in src
    ta = read(PREVIEW / "trader_accept.js")
    assert "overflow_px: document.documentElement.scrollWidth - document.documentElement.clientWidth," in ta
    # signed in only without a sign-in step on screen AND without a 401 / 403 from the API
    assert "signed_in: !document.getElementById('command-unlock') && !/\\bSIGN-IN REQUIRED\\b/.test(text)," in src
    assert "m.signed_in = m.signed_in === true && authRefusals === 0;" in src
    # touch targets include same-origin frames (the framed agent desks)
    assert "if (touch && doc) scanSmall(doc, fr.left, fr.top, 'frame');" in src
    # the background-layer rule is unchanged (pinned by the bars suite)
    assert "const BG_SHARE = 0.9;" in src


def test_the_workflow_has_time_for_the_full_matrix():
    wf = read(ROOT / ".github" / "workflows" / "frontend-preview.yml")
    m = re.search(r"timeout-minutes: (\d+)", wf)
    assert m and int(m.group(1)) >= 120


# --- the product fixes --------------------------------------------------------


def _last_sheet(page: str) -> str:
    html = read(COMMAND / page)
    head = html[:html.index("</head>")]
    sheets = re.findall(r'<link rel="stylesheet" href="([^"]+)"', head)
    return sheets[-1]


def test_device_fit_is_loaded_last_on_every_shell_page():
    for page in ("agent.html", "ops.html", "position.html", "index.html", "floor.html", "trader.html"):
        assert _last_sheet(page) == "device-fit.css", page


def test_agent_navigation_and_position_bar_clear_the_shell():
    css = read(COMMAND / "device-fit.css")
    assert 'body.bt-hq2>nav[aria-label="AI agents"]{margin:calc(64px + var(--sa-t)) var(--sa-r) 0 calc(92px + var(--sa-l));}' in css
    assert 'body.bt-hq2>nav[aria-label="AI agents"]~.hq6-agent-layout{padding-top:12px;}' in css
    assert "body.bt-hq2.pr-body.ops-hdr-on .pr-bar{top:calc(64px + var(--ops-hdr-h) + var(--sa-t));}" in css
    assert "body.bt-hq2.pr-body{padding-left:calc(92px + var(--sa-l));padding-right:var(--sa-r);padding-top:calc(64px + var(--sa-t));}" in css
    # the agent nav exists exactly where these rules point (agent.html)
    assert '<nav aria-label="AI agents">' in read(COMMAND / "agent.html")
    assert '<body class="pr-body">' in read(COMMAND / "position.html")


def test_the_shell_keeps_its_content_off_the_notch_and_the_home_indicator():
    css = read(COMMAND / "device-fit.css")
    for rule in (
        ":root{--sa-t:env(safe-area-inset-top,0px);--sa-r:env(safe-area-inset-right,0px);",
        "body.bt-hq2 .bt-hq2-pagebar{height:calc(64px + var(--sa-t))!important;padding-top:var(--sa-t);padding-right:calc(24px + var(--sa-r));}",
        "body.bt-hq2 .bt-hq2-shell{height:calc(66px + var(--sa-b));padding:7px calc(12px + var(--sa-r)) calc(7px + var(--sa-b)) calc(12px + var(--sa-l));}",
        "body.bt-hq2 .bt-hq2-shell{width:calc(92px + var(--sa-l));padding-left:calc(10px + var(--sa-l));padding-top:calc(18px + var(--sa-t));padding-bottom:calc(16px + var(--sa-b));}",
        "body.bt-hq2 .ops-hdr{left:calc(92px + var(--sa-l));right:var(--sa-r);}",
        ".ops-desk-page .od-panel{scroll-margin-top:calc(80px + var(--ops-hdr-h,46px) + var(--sa-t));}",
    ):
        assert rule in css, rule
    # a landscape phone's rail reaches all six destinations
    short = css[css.index("@media (min-width:781px) and (max-height:560px){"):]
    short = short[:short.index("\n}")]
    assert "overflow-y:auto" in short and "min-height:44px!important" in short


def test_touch_targets_on_the_shell_pages():
    css = read(COMMAND / "device-fit.css")
    i = css.index("@media (pointer:coarse){")
    depth, j = 1, i + len("@media (pointer:coarse){")
    while depth:
        depth += {"{": 1, "}": -1}.get(css[j], 0)
        j += 1
    coarse = re.sub(r"/\*.*?\*/", "", css[i:j], flags=re.S)
    for rule in (
        ".hq5-pulse-btn,.hq5-pulse-agent{min-height:44px;}",
        ".bt-hq2 .bt-hq2-nav a{min-width:44px;align-self:stretch;}",
        'body.bt-hq2>nav[aria-label="AI agents"] a{min-height:44px;}',
        ".hq6-agent-person summary,.bt-agent2-actions button,.bt-agent2-actions a,.ws-tabs button{min-height:44px;}",
        ".od-jump a{min-height:44px;display:inline-flex;align-items:center;}",
        ".od-t th[data-sort]{height:44px;}",
        ".od-ctl select{min-height:44px;}",
        ".pr-brand,.pr-nav a,#pr-refresh{min-height:44px;}",
    ):
        assert rule in coarse, rule
    # only ever raised, never shrunk or hidden (the RC6 rule for this block)
    assert "max-height" not in coarse and "max-width" not in coarse
    assert "display:none" not in coarse and "font-size" not in coarse


def test_the_agent_hero_fits_beside_the_person_column():
    css = read(COMMAND / "device-fit.css")
    assert "@media (min-width:981px) and (max-width:1360px){\n  .hq6-agent-layout .wsx-hero{grid-template-columns:1fr 1fr;}" in css
    ws = read(COMMAND / "workspace.css")
    assert ".wsx-hero{display:grid;grid-template-columns:minmax(220px,1.1fr) minmax(260px,1.6fr) minmax(200px,.9fr);" in ws
