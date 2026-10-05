"""THE COMMAND FRONTEND NAMES THE EXECUTION AGENT ARCHER (migration 266).

Static and node-run proofs over frontend/public/command:
  * the seat, the floor plan, the desks, the screens and every label say
    Archer (archer / ARCHER); there is exactly one execution seat;
  * EDDIE survives only as a HISTORICAL ALIAS: an API answer that still
    names EDDIE / eddie (a serving build from before the rename, or a record
    written before it) is read as Archer's, labelled historical_alias, and
    /eddie still reaches his page;
  * Archer wears Eddie's licensed Rocketbox model and portrait under the new
    key (byte-identical files), and the old file paths still resolve;
  * every remaining "eddie" in the shipped files is an alias, a storage name
    or a historical receipt -- nothing else.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
CMD = ROOT / "frontend" / "public" / "command"
MODELS = CMD / "team-demo" / "assets" / "models"
NODE = shutil.which("node")


def _sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_one_execution_seat_named_archer():
    core = (CMD / "floor-core.js").read_text()
    assert re.search(r"agent: 'ARCHER', slug: 'archer', name: 'Archer', "
                     r"short: 'Execution'", core)
    assert "agent: 'EDDIE'" not in core and "slug: 'eddie'" not in core
    model = (CMD / "hq-model.js").read_text()
    order = re.search(r"export const ORDER = \[([^\]]+)\]", model).group(1)
    slugs = re.findall(r"'(\w+)'", order)
    assert slugs.count("archer") == 1 and "eddie" not in slugs
    assert "archer: ['Execution & microstructure', 'Head of Execution']" in \
        model
    scene = (CMD / "hq-scene.js").read_text()
    assert "archer: 54" in scene and "archer: {model: 'archer', tint: null}" \
        in scene
    screens = (CMD / "hq-screens.js").read_text()
    assert "ARCHER SHADOW DECISIONS" in screens
    assert "archer: ['marks', 'smalllive'" in screens


def test_archer_wears_eddies_licensed_model_and_the_old_paths_stay():
    man = json.loads((MODELS / "manifest.json").read_text())
    chars = man["characters"]
    assert "archer" in chars and "eddie" not in chars   # one person, not two
    e = chars["archer"]
    assert e["model"] == "archer.glb" and e["test_asset"] is False
    assert "Business_Male_04" in e["licensed_from"]
    assert man["historical_aliases"] == {"eddie": "archer"}
    assert e["historical_alias"]["model"] == "eddie.glb"
    # byte-identical: the same licensed asset under the new key, and the
    # historical files still at their old paths
    assert _sha(MODELS / "archer.glb") == _sha(MODELS / "eddie.glb")
    assert _sha(MODELS / "portraits" / "archer.jpg") == \
        _sha(MODELS / "portraits" / "eddie.jpg")
    for css in ("floor.css", "hq4-company.css", "hq5-workspace.css",
                "hq6-complete.css", "office.css"):
        src = (CMD / css).read_text()
        assert "portraits/archer.jpg" in src, css
        assert "portraits/eddie.jpg" not in src, css


def test_the_old_address_reaches_archers_page():
    shell = (CMD / "agent.html").read_text()
    assert '<a href="/archer" data-agent="archer">Archer' in shell
    assert 'var ALIASES = {eddie: "archer"};' in shell
    assert 'url = "/api/command/agents/eddie/page";' in shell
    toml = (ROOT / "netlify.toml").read_text()
    catch_all = toml.index('command.bettortoken.com/*"')
    for path in ("archer", "eddie"):
        m = re.search(r'from = "https://command\.bettortoken\.com/%s"\s+'
                      r'to = "/command/agent\.html"\s+status = 200\s+'
                      r'force = true' % path, toml)
        assert m, path
        assert toml.index('command.bettortoken.com/%s"' % path) < catch_all
    for js in ("command-final.js", "hq6-complete.js", "hq2-shell.js",
               "meeting-release.js"):
        src = (CMD / js).read_text()
        assert re.search(r"archer\|eddie", src), js


def _node(script: str) -> dict:
    if not NODE:
        pytest.skip("node is not installed")
    out = subprocess.run([NODE, "-e", script, str(CMD / "floor-core.js")],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_an_answer_naming_eddie_reads_as_archer_labelled_historical():
    got = _node(r"""
global.window = global; global.document = {hidden: false, addEventListener() {}};
require(process.argv[1]);
const B = window.BTFloor;
const d = B.dealias({
  agents: [{agent: 'EDDIE', slug: 'eddie', state: 'IDLE'},
           {agent: 'DEREK', slug: 'derek', state: 'WORKING_ON'}],
  edges: [{from: 'derek', to: 'eddie', kind: 'EXECUTION_ESTIMATE'}],
  states: {EDDIE: {state: 'IDLE'}, DEREK: {state: 'WORKING_ON'}},
  chain: {eddie_estimates: 3, last_eddie_decision_at: 9},
  evidence: [{kind: 'eddie_execution_estimates', id: 'eex:1'}]});
console.log(JSON.stringify({d, slugs: B.SEATS.map((s) => s.slug),
  canon: [B.canonicalSlug('eddie'), B.canonicalSlug('scout')]}));
""")
    d = got["d"]
    assert d["agents"][0] == {"agent": "ARCHER", "slug": "archer",
                              "state": "IDLE", "historical_alias": "EDDIE"}
    assert "historical_alias" not in d["agents"][1]
    assert d["edges"][0]["to"] == "archer"
    assert d["edges"][0]["historical_alias"] == "EDDIE"
    # a map keyed by agent never lists the seat twice
    assert sorted(d["states"]) == ["ARCHER", "DEREK"]
    assert d["chain"]["archer_estimates"] == 3
    assert d["chain"]["last_archer_decision_at"] == 9
    # storage names (an evidence kind) are values, never touched
    assert d["evidence"][0]["kind"] == "eddie_execution_estimates"
    assert got["slugs"].count("archer") == 1 and "eddie" not in got["slugs"]
    assert got["canon"] == ["archer", "scout"]


def test_every_remaining_eddie_is_an_alias_a_storage_name_or_a_receipt():
    allowed = re.compile(
        r"historical|migration 266|alias|ALIAS|eddie_execution_estimates|"
        r"217_eddie_scout_agents|EDDIE_SKIP_EXECUTION|"
        r"\(ARCHER\|EDDIE\)|archer\|eddie|r\.eddie|ex\.eddie_execution|"
        r"'/eddie'|/eddie/|\"/eddie\"|/api/command/eddie|"
        r"floorRead\('eddie'|idRead\('eddie'|m\[1\]==='eddie'|"
        r"a\.slug === 'eddie'|e\[k\] === 'eddie'|eddie\.glb|"
        r"portraits/eddie\.jpg|\"slug\": \"eddie\"|\"eddie\": \"archer\"|"
        r"\(eddie\|EDDIE\)|eddie: 'archer'|eddie: \"archer\"|"
        r"Eddie \(Archer since|named Eddie until|EDDIE / eddie|"
        r"\(eddie_estimates\)")
    bad = []
    for p in sorted(CMD.rglob("*")):
        if not p.is_file() or p.suffix not in (".js", ".html", ".css",
                                               ".json", ".py"):
            continue
        if "data/receipts" in str(p):
            continue                      # historical release receipts
        for n, line in enumerate(p.read_text(errors="ignore").splitlines(),
                                 1):
            if re.search(r"eddie", line, re.I) and not allowed.search(line):
                bad.append("%s:%d: %s" % (p.relative_to(CMD), n,
                                          line.strip()[:120]))
    assert bad == [], "\n".join(bad)
