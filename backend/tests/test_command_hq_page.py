"""The 3D headquarters (frontend/public/command/hq*.js): the Command entry.

It is a presentation layer over the existing read path, so these checks pin
what it must never do:

  - send anything but a GET, or read anything outside /api/command/ (every
    read goes through BTFloor.read / BTFloor.poller, which refuse other paths);
  - invent a number or an activity: no Math.random anywhere in the scene,
    its screens, its model or its controller;
  - give Adriana's Arbitrage Desk any state the floor API did not serve:
    until an ADRIANA seat is served it is NOT DEPLOYED · UNVERIFIED, never
    active; once served it is her desk, with the floor API's state;
  - add PAPER and SMALL LIVE together, or carry an order / cancel / limit
    control.

The model is executed in node against a fake BTFloor so the Adriana rule and
the work-state translation are tested as behaviour, not text.
"""
import json
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
CMD = ROOT / "frontend" / "public" / "command"
FILES = ("hq.js", "hq-model.js", "hq-scene.js", "hq-screens.js")
SRC = {name: (CMD / name).read_text() for name in FILES}
INDEX = (CMD / "index.html").read_text()


def _code(src: str) -> str:
    """The source without its comments (block and line)."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", src)


@pytest.mark.parametrize("name", FILES)
def test_no_random_number_anywhere_in_the_headquarters(name):
    assert "Math.random" not in _code(SRC[name]), name


@pytest.mark.parametrize("name", FILES)
def test_the_headquarters_only_ever_reads(name):
    code = _code(SRC[name])
    assert not re.search(r"method\s*:\s*['\"](POST|PUT|PATCH|DELETE)", code, re.I), name
    assert "XMLHttpRequest" not in code and "sendBeacon" not in code, name
    # the scene, screens and controller never fetch the API themselves: every
    # API read goes through the model (BTFloor.read / BTFloor.poller)
    if name != "hq-model.js":
        assert not re.search(r"fetch\(\s*['\"]/api/", code), name


def test_every_model_read_is_a_command_read():
    code = _code(SRC["hq-model.js"])
    paths = re.findall(r"B\.(?:poller|read)\(\s*'([^']+)'", code)
    assert paths, "the model reads nothing -- the regex is wrong"
    for p in paths:
        assert p.startswith("/api/command/"), p
    assert "fetch(" not in code


def test_no_order_cancel_limit_or_capital_control():
    for name in FILES:
        code = _code(SRC[name]).lower()
        for word in ("/orders/submit", "/cancel", "submit_order", "cancel_order", "set_limit", "allocate("):
            assert word not in code, (name, word)
    assert "no order, cancel, limit or capital control" in INDEX


def test_paper_and_small_live_are_never_summed():
    for name in FILES:
        code = _code(SRC[name])
        assert not re.search(r"paper[^;\n]{0,80}\+[^;\n]{0,40}small[^;\n]{0,40}equity", code, re.I), name


def test_the_entry_loads_the_shared_read_layer_and_the_sign_in():
    for need in ('src="unlock.js"', 'src="floor-core.js"', 'src="mobile-command.js"', 'src="hq.js"'):
        assert need in INDEX, need
    # the floor read layer and the view models load before the module
    assert INDEX.index('floor-core.js') < INDEX.index('hq.js')
    assert INDEX.index('mobile-command.js') < INDEX.index('hq.js')


def test_the_five_places_and_nothing_else():
    nav = re.search(r'<nav id="hq-nav".*?</nav>', INDEX, re.S).group(0)
    assert re.findall(r'data-go="([a-z]+)"', nav) == ["command", "floor", "markets", "capital", "reports"]


NODE = shutil.which("node")
HARNESS = r"""
const path = process.argv[1];
globalThis.window = globalThis;
globalThis.document = {hidden: false, addEventListener() {}};
const reads = [];
const SEATS = [
  {agent: 'DEREK', slug: 'derek', name: 'Derek', short: 'CIO', role: 'CIO', accent: '#9fe3bf'},
  {agent: 'KAREN', slug: 'karen', name: 'Karen', short: 'Red team', role: 'Red team', accent: '#ff9a8f'},
  {agent: 'SCOUT', slug: 'scout', name: 'Scout', short: 'Intel', role: 'Intel', accent: '#f5b072'},
  {agent: 'EDDIE', slug: 'eddie', name: 'Eddie', short: 'Exec', role: 'Exec', accent: '#6fe0d2'},
  {agent: 'CHIEF_ALLOCATOR', slug: 'allocator', name: 'Allie', short: 'Alloc', role: 'Alloc', accent: '#ecc66d'},
  {agent: 'AUDREY', slug: 'audrey', name: 'Audrey', short: 'Audit', role: 'Audit', accent: '#cdb6f6'},
  {agent: 'XAVIER', slug: 'xavier', name: 'Xavier', short: 'PM', role: 'PM', accent: '#9fd2f2'}];
const floor = JSON.parse(process.argv[2]);
const B = {SEATS, esc: (s) => String(s), age: (s) => s + 's', ago: () => 'ago', edgeLabel: (k) => k, isFixture: () => false,
  read: (p) => { reads.push(p); return Promise.resolve({status: 'UNAVAILABLE', why: 'test', at: 0}); },
  poller: (p, every, cb) => { reads.push(p); if (p === '/api/command/floor') cb({current: {status: 'OK', data: floor, at: 1}}); return {refresh() {}}; }};
globalThis.setTimeout = (f) => f(); globalThis.setInterval = () => 0;
import(path).then((m) => {
  const hq = m.createModel(B); hq.start();
  const d = (s) => { const x = hq.desk(s); return {planned: x.planned, label: x.label, active: x.active, code: x.code}; };
  console.log(JSON.stringify({slugs: hq.SEATS.map((s) => s.slug), adriana: d('adriana'), derek: d('derek'), xavier: d('xavier'),
    adrianaPlanned: hq.BY_SLUG.adriana.planned, reads}));
});
"""


def _run(floor):
    if not NODE:
        pytest.skip("node is not installed")
    out = subprocess.run([NODE, "--input-type=module", "-e", HARNESS, str(CMD / "hq-model.js"), json.dumps(floor)],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


FLOOR = {"agents": [
    {"agent": "DEREK", "slug": "derek", "state": "WORKING_ON", "work_state": "WORKING", "deployed": True},
    {"agent": "XAVIER", "slug": "xavier", "state": "REVIEWING", "work_state": "BLOCKED_ON_MARKET_DATA", "deployed": True}],
    "edges": [], "feed": []}


def test_adriana_is_a_desk_but_not_deployed_until_the_floor_serves_her():
    r = _run(FLOOR)
    assert r["slugs"] == ["derek", "karen", "scout", "allocator", "adriana", "eddie", "audrey", "xavier"]
    assert r["adriana"] == {"planned": True, "label": "NOT DEPLOYED · UNVERIFIED", "active": False, "code": "NOT_DEPLOYED"}
    assert r["adrianaPlanned"] is True


def test_a_served_adriana_seat_replaces_the_placeholder():
    floor = json.loads(json.dumps(FLOOR))
    floor["agents"].append({"agent": "ADRIANA", "slug": "adriana", "state": "WORKING_ON", "work_state": "WORKING", "deployed": True})
    r = _run(floor)
    assert r["adriana"]["planned"] is False and r["adriana"]["code"] == "WORKING" and r["adriana"]["active"] is True
    # the seat itself is no longer planned: the scene seats her model
    assert r["adrianaPlanned"] is False
    # an UNDEPLOYED served seat (the migration not applied) is not active
    floor["agents"][-1].update(deployed=False, work_state=None, state="NOT_DEPLOYED")
    r = _run(floor)
    assert r["adriana"]["code"] == "NOT_DEPLOYED" and r["adriana"]["active"] is False


def test_adriana_wears_her_own_licensed_model_and_portrait():
    m = json.loads((CMD / "team-demo" / "assets" / "models" / "manifest.json").read_text())["characters"]["adriana"]
    assert m["model"] == "adriana.glb" and m["test_asset"] is False and m["license_spdx"] == "MIT"
    assert "Business_Female_01" in m["licensed_from"]
    others = json.loads((CMD / "team-demo" / "assets" / "models" / "manifest.json").read_text())["characters"]
    assert all(v["model"] != "adriana.glb" and "Business_Female_01" not in v["licensed_from"]
               for k, v in others.items() if k != "adriana")
    assert (CMD / "team-demo" / "assets" / "models" / "portraits" / "adriana.jpg").is_file()
    scene = SRC["hq-scene.js"]
    assert "adriana: {model: 'adriana', tint: null}" in scene and "adriana: 18" in scene


def test_critical_attention_and_freshness_dominate_every_view():
    code = _code(SRC["hq.js"])
    assert "function alertHTML()" in code and "has-critical" in code
    assert "function freshness()" in code and 'data-render="fresh"' in INDEX
    assert 'id="hq-alert"' in INDEX
    # freshness cells are recorded counts or N/A with the reason -- never a default
    assert "Management" in code and "WAITING_FOR_FRESH_EVIDENCE" in code


def test_the_opportunity_book_spells_out_each_contract_and_groups_duplicates():
    code = _code(SRC["hq.js"])
    for col in ("Contract · family · line · period", "Freshness", "Settlement", "Exec. depth"):
        assert col in code, col
    assert "function groupMkt(" in code and "×N" in SRC["hq.js"]


def test_the_report_selector_and_branded_pdf():
    code = _code(SRC["hq.js"])
    for rid in ("daily", "capital", "markets", "team"):
        assert "id: '%s'" % rid in code, rid
    assert "print-brand" in code and "brand/bettortoken-logo.png" in code and "window.print()" in code


def test_the_work_state_is_translated_one_to_one_and_only_work_is_active():
    r = _run(FLOOR)
    assert r["derek"]["code"] == "WORKING" and r["derek"]["active"] is True
    assert r["xavier"]["code"] == "BLOCKED_ON_MARKET_DATA" and r["xavier"]["active"] is False


def test_the_model_reads_only_command_routes():
    r = _run(FLOOR)
    assert r["reads"], "nothing was read"
    assert all(p.startswith("/api/command/") for p in r["reads"]), r["reads"]


def test_command_defaults_to_the_management_epoch():
    """The primary money figures are the management epoch (equity/live
    paper.management: $500,000 at 2026-10-05 00:00 ET); the ledger since
    funding is shown only as PRE-MANAGEMENT HISTORY, never mixed in."""
    code = _code(SRC["hq.js"])
    assert "function mgmt()" in code and "p && p.management" in code
    # every primary money surface asks for the management book first
    for fn in ("function capitalHTML(", "function capCurveHTML(",
               "function capBookHTML(", "function dailyReport(",
               "function capitalReport("):
        body = code.split(fn, 1)[1].split("\nfunction ", 1)[0]
        assert "mgmt()" in body, fn
    assert "MANAGEMENT START: OCT 5, 2026 · OPENING EQUITY $500,000" in SRC["hq.js"]
    assert "PRE-MANAGEMENT HISTORY" in SRC["hq.js"]
    # the KPIs management asked for, all from the management section
    kp = code.split("function mgKpis(", 1)[1].split("\nfunction ", 1)[0]
    for k in ("Opening equity", "Current equity", "Realized P&L",
              "Unrealized P&L", "Total P&L", "Return", "Drawdown", "Cash",
              "Reserved", "Marked value", "Exposure"):
        assert "'%s'" % k in kp, k
    assert "m.equity_usd" in kp and "p.equity_usd" not in kp
    # an unverified carried position is shown, never hidden
    assert "EPOCH_OPEN_MARK_UNVERIFIED" in SRC["hq.js"]
    # the 3D capital wall and ticker follow the same section
    scr = SRC["hq-screens.js"] if "hq-screens.js" in SRC else (CMD / "hq-screens.js").read_text()
    assert "function mgmtOf(p)" in scr and "MANAGEMENT EQUITY" in scr
    assert "PRE-MANAGEMENT HISTORY" in scr
