"""THE TRADING FLOOR (frontend/public/command/floor.html) DRAWS ONLY THE TRUTH.

The restored real Three.js floor -- floor.js (controller) driving
floor-scene.js (the cinematic scene), floor-model.js (its data) and
floor-screens.js (its in-world screens) -- is pinned here by reading the
served files:

  - no Math.random anywhere in the floor's controller, data, scene or screen
    paths (no random number, no synthetic price, no fake activity);
  - no recolour CAST: every desk wears its OWN model (manifest entry by its
    own slug, else its own file named in CAST), never a tint; the abstract
    HQ2 floor assets are not loaded by floor.html;
  - the seven desks and the Allie label (slug / route stay allocator);
  - the floor API states map one to one, and only WORKING / REVIEWING /
    CHALLENGING on a fresh read drive "at work" motion;
  - PAPER and SMALL LIVE (and each legacy venue) are never added together;
  - a desk whose model is not present shows PORTRAIT ARRIVING;
  - the scene keeps the contract floor.js drives (createFloor + its methods).
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
CMD = ROOT / "frontend" / "public" / "command"
SCENE, MODEL, SCREENS, CTRL, CORE = (CMD / "floor-scene.js", CMD / "floor-model.js",
                                     CMD / "floor-screens.js", CMD / "floor.js",
                                     CMD / "floor-core.js")
HTML = CMD / "floor.html"
FLOOR_FILES = (SCENE, MODEL, SCREENS, CTRL)
SLUGS = ["derek", "karen", "scout", "eddie", "allocator", "audrey", "xavier"]


def _code(text: str) -> str:
    """The source without /* */ and // comments (string contents kept)."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(?m)(^|[^:'\"\\])//.*$", r"\1", text)


def test_the_page_loads_the_real_floor_and_not_the_hq2_floor():
    html = HTML.read_text()
    assert 'src="floor-core.js"' in html and 'src="floor.js"' in html
    assert not re.search(r'hq2-floor(-scene)?\.(js|css)', html)
    ctrl = CTRL.read_text()
    assert "import('./floor-scene.js')" in ctrl
    scene = SCENE.read_text()
    assert "from './floor-screens.js'" in scene and "from './floor-model.js'" in scene
    assert "hq2-floor" not in scene


def test_no_math_random_in_any_floor_path():
    for f in FLOOR_FILES:
        assert "Math.random" not in f.read_text(), f.name


def test_every_desk_wears_its_own_model_and_nothing_is_tinted():
    scene = _code(SCENE.read_text())
    cast = dict(re.findall(r"(\w+): \{model: '(\w+)', tint: null\}", re.search(
        r"export const CAST = \{(.*?)\};", scene, flags=re.S).group(1)))
    assert cast == {"derek": "derek", "xavier": "xavier", "audrey": "audrey", "karen": "karen",
                    "allocator": "allie", "eddie": "eddie", "scout": "scout"}, cast
    assert len(set(cast.values())) == 7          # seven distinct bodies
    for f in FLOOR_FILES:
        code = _code(f.read_text())
        assert "tintMaterial" not in code and "uTint" not in code, f.name
        assert not re.search(r"tint\s*:\s*\[", code), f.name
    # each desk looks itself up by its OWN slug; the only alias is Allie's file
    assert "chars[s.slug]" in scene
    assert re.search(r"s\.slug === 'allocator' \? \(chars\.allie", scene)
    assert "e.test_asset === false" in scene


def test_portrait_arriving_until_the_agents_own_model_is_present():
    screens = SCREENS.read_text()
    assert "'PORTRAIT'" in screens and "'ARRIVING'" in screens
    scene = SCENE.read_text()
    assert "d.arr.mesh.visible = false" in scene
    assert "Capsule" not in _code(scene)


def test_the_seven_desks_and_the_allie_label():
    core = CORE.read_text()
    assert re.search(r"agent: 'CHIEF_ALLOCATOR', slug: 'allocator', name: 'Allie', short: 'Chief Allocator'", core)
    scene = _code(SCENE.read_text())
    plan = dict(re.findall(r"\b(\w+): (-?\d+)", re.search(r"const SEAT_ANGLE = \{(.*?)\};", scene).group(1)))
    assert sorted(plan) == sorted(SLUGS), plan
    model = _code(MODEL.read_text())
    assert "allocator: ['Capital allocation', 'Chief Allocator']" in model
    assert "href: '/' + s.slug" in model          # routes stay /<slug>, /allocator


def test_states_map_one_to_one_and_only_real_work_moves():
    code = _code(MODEL.read_text())
    block = re.search(r"export const STATE = \{(.*?)\};", code, flags=re.S).group(1)
    rows = dict(re.findall(r"(\w+):\s*\{label: '([^']+)'", block))
    assert rows == {"WORKING_ON": "WORKING", "REVIEWING": "REVIEWING",
                    "CHALLENGING": "CHALLENGING", "WAITING": "WAITING",
                    "BLOCKED": "BLOCKED", "IDLE": "NO TASK", "STALE": "STALE",
                    "NOT_DEPLOYED": "OFFLINE", "UNAVAILABLE": "UNAVAILABLE"}
    active = set(re.findall(r"(\w+):\s*\{[^}]*active: true", block))
    assert active == {"WORKING_ON", "REVIEWING", "CHALLENGING"}
    assert "active: !!meta.active && floorFresh()" in code
    scene = _code(SCENE.read_text())
    assert "av.posture === 'work' && d.active && !reduced" in scene


SUM = re.compile(r"(?:equity_usd|equity\.usd|cash_usd)\s*\+\s*[\w(]|"
                 r"[\w)]\s*\+\s*[\w.]*(?:equity_usd|equity\.usd|cash_usd)\b")


def test_the_sum_rule_catches_a_violation():
    assert SUM.search("var t = paper.equity_usd + small.equity.usd;")
    assert SUM.search("x = pm.equity_usd+kalshi.equity_usd")
    assert not SUM.search("'PAPER EQUITY ' + (u.usd(q.paper.equity_usd) || 'UNAVAILABLE')")


def test_paper_and_small_live_are_never_summed():
    for f in FLOOR_FILES:
        code = _code(f.read_text())
        m = SUM.search(code)
        assert not m, (f.name, m and m.group(0))
    screens = SCREENS.read_text()
    assert "SEPARATE BOOKS · NEVER SUMMED" in screens
    assert "never added to paper" in screens


def test_missing_values_say_unavailable_and_fixtures_are_labelled():
    screens, model = SCREENS.read_text(), MODEL.read_text()
    assert screens.count("UNAVAILABLE") >= 20
    assert "NO NEW MARK" in screens
    assert "B.isFixture(" in model and "FIXTURE" in screens


def test_the_floor_reads_only_command_get_endpoints():
    model = _code(MODEL.read_text())
    paths = set(re.findall(r"'(/api/command/[^'?]*)", model))
    assert paths == {"/api/command/floor/", "/api/command/equity/live",
                     "/api/command/coverage"}, paths
    for f in FLOOR_FILES:
        code = _code(f.read_text())
        assert not re.search(r"method\s*:\s*['\"](POST|PUT|PATCH|DELETE)", code, re.I), f.name


def test_the_scene_keeps_the_contract_floor_js_drives():
    scene = _code(SCENE.read_text())
    assert "export function webglAvailable()" in scene
    assert "export async function createFloor(host, opts)" in scene
    for m in ("setData(", "focus(slug)", "focusWall(id)", "resetView()", "tickClocks()",
              "wallQuads()", "anchorOf(slug)", "setInsetRight(px)", "setPaused(v)", "stats()"):
        assert m in scene, m
    for cb in ("o.onPick(", "o.onHover(", "o.onFrame(", "o.onStatus('characters-ready'"):
        assert cb in scene, cb
    for wall in ("'feed'", "'equity'", "'health'"):
        assert "mk(%s" % wall in scene, wall


def test_reduced_motion_is_respected():
    scene = _code(SCENE.read_text())
    assert "const reduced = !!o.reducedMotion;" in scene
    for guard in ("if (!reduced) {", "uFlow: {value: reduced ? 0 : 1}",
                  "camState.t = reduced ? 1 : 0"):
        assert guard in scene, guard
