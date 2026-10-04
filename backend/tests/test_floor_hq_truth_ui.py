"""THE TRADING FLOOR (frontend/public/command/floor.html) DRAWS ONLY THE TRUTH.

The cinematic floor (hq2-floor.js data + HUD, hq2-floor-scene.js three.js
scene, hq2-floor-screens.js in-world screens) is pinned here by reading the
served files:

  - no Math.random anywhere in the floor's data, scene or screen paths
    (no random number, no synthetic price, no fake activity);
  - no recolour CAST: every desk loads its OWN model from models/manifest.json
    by its own slug (the Chief Allocator may be listed as 'allie'), the legacy
    recolouring floor (floor.js / floor-scene.js) is not loaded;
  - the seven desks, their slugs and their workspace routes;
  - the Chief Allocator's user-facing name is Allie, slug/route allocator;
  - the floor API states map one to one, and only WORKING / REVIEWING /
    CHALLENGING may drive "at work" motion;
  - PAPER and SMALL LIVE (and each legacy venue) are never added together;
  - a desk whose model is not present shows PORTRAIT ARRIVING.
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
CMD = ROOT / "frontend" / "public" / "command"
DATA, SCENE, SCREENS = (CMD / "hq2-floor.js", CMD / "hq2-floor-scene.js",
                        CMD / "hq2-floor-screens.js")
HTML = CMD / "floor.html"
FLOOR_FILES = (DATA, SCENE, SCREENS)
SEVEN = [("derek", "Derek", "/derek"), ("karen", "Karen", "/karen"),
         ("scout", "Scout", "/scout"), ("allocator", "Allie", "/allocator"),
         ("eddie", "Eddie", "/eddie"), ("audrey", "Audrey", "/audrey"),
         ("xavier", "Xavier", "/xavier")]


def _code(text: str) -> str:
    """The source without // and /* */ comments (string contents kept)."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(?m)(^|[^:'\"\\])//.*$", r"\1", text)


def test_the_floor_files_exist_and_the_page_loads_them():
    html = HTML.read_text()
    for f in FLOOR_FILES:
        assert f.exists(), f
    assert 'src="hq2-floor.js"' in html
    assert 'src="hq2-floor-scene.js"' in html
    assert "hq2-floor-screens.js" in SCENE.read_text()
    # the owner's shell and the HQ3 live layer stay
    assert 'src="hq2-shell.js"' in html and 'href="hq2-brand.css"' in html
    assert 'src="hq3-live.js"' in html


def test_no_math_random_in_any_floor_path():
    for f in FLOOR_FILES:
        assert "Math.random" not in f.read_text(), f.name


def test_no_recolour_cast_and_no_legacy_recolouring_floor():
    html = HTML.read_text()
    assert not re.search(r'<script[^>]+src="floor(-scene)?\.js"', html)
    for f in FLOOR_FILES:
        code = _code(f.read_text())
        assert not re.search(r"\bCAST\s*=", code), f.name
        assert "tintMaterial" not in code and "uTint" not in code, f.name
        # no seat is ever pointed at another agent's model file
        assert not re.search(r"model\s*:\s*['\"](derek|xavier|audrey|karen|"
                             r"allie|eddie|scout)['\"]", code), f.name
    scene = _code(SCENE.read_text())
    # each desk looks itself up in the manifest by its own slug; the only
    # alias is the Chief Allocator's 'allie' entry
    assert "chars[s.slug]" in scene
    assert re.search(r"s\.slug === 'allocator' \? \(chars\.allie", scene)
    assert "e.test_asset === false" in scene


def test_portrait_arriving_when_a_model_is_not_present():
    assert "'PORTRAIT'" in SCREENS.read_text() and "'ARRIVING'" in SCREENS.read_text()
    scene = SCENE.read_text()
    assert "arr.mesh.visible = false" in scene     # hidden only once the own model mounted
    assert "CapsuleGeometry" not in scene and "Capsule" not in _code(scene)


def test_the_seven_desks_slugs_routes_and_the_allie_label():
    code = _code(DATA.read_text())
    seats = re.findall(r"\{agent: '([A-Z_]+)', slug: '([a-z]+)', name: '([^']+)', "
                       r"role: '([^']+)'.*?href: '([^']+)'\}", code, flags=re.S)
    got = [(s, n, h) for _a, s, n, _r, h in seats]
    assert sorted(got) == sorted(SEVEN), got
    allie = [x for x in seats if x[1] == "allocator"][0]
    assert allie[0] == "CHIEF_ALLOCATOR" and allie[2] == "Allie"
    assert allie[3] == "Chief Allocator" and allie[4] == "/allocator"
    assert "name: 'Chief Allocator'" not in code
    scene = _code(SCENE.read_text())
    for slug, _n, _h in SEVEN:
        assert re.search(r"\b%s: -?\d+" % slug, scene), slug   # a seat on the plan


def test_states_map_one_to_one_and_only_real_work_moves():
    code = _code(DATA.read_text())
    block = re.search(r"var STATE = \{(.*?)\};", code, flags=re.S).group(1)
    rows = dict(re.findall(r"(\w+):\s*\{label: '([^']+)'", block))
    assert rows == {"WORKING_ON": "WORKING", "REVIEWING": "REVIEWING",
                    "CHALLENGING": "CHALLENGING", "WAITING": "WAITING",
                    "BLOCKED": "BLOCKED", "IDLE": "NO TASK", "STALE": "STALE",
                    "NOT_DEPLOYED": "OFFLINE", "UNAVAILABLE": "UNAVAILABLE"}
    active = set(re.findall(r"(\w+):\s*\{[^}]*active: true", block))
    assert active == {"WORKING_ON", "REVIEWING", "CHALLENGING"}
    # a stale floor read never animates a desk as working
    assert "active: !!meta.active && floorFresh()" in code
    scene = _code(SCENE.read_text())
    assert "av.posture === 'work' && d.active && !reduced" in scene


SUM = re.compile(r"(?:equity_usd|equity\.usd|cash_usd)\s*\+\s*[\w(]|"
                 r"[\w)]\s*\+\s*[\w.]*(?:equity_usd|equity\.usd|cash_usd)\b")


def test_the_sum_rule_catches_a_violation():
    assert SUM.search("var t = paper.equity_usd + small.equity.usd;")
    assert SUM.search("x = pm.equity_usd+kalshi.equity_usd")
    # formatting a figure into a label is not arithmetic
    assert not SUM.search("'PAPER EQUITY ' + (u.usd(q.paper.equity_usd) || 'UNAVAILABLE')")


def test_paper_and_small_live_are_never_summed():
    pat = SUM
    for f in FLOOR_FILES:
        code = _code(f.read_text())
        assert not pat.search(code), (f.name, pat.search(code).group(0))
    screens = SCREENS.read_text()
    assert "SEPARATE BOOKS · NEVER SUMMED" in screens
    assert "never added to paper" in screens


def test_missing_values_say_unavailable_and_fixtures_are_labelled():
    data, screens = DATA.read_text(), SCREENS.read_text()
    assert screens.count("UNAVAILABLE") >= 20
    assert "NO NEW MARK" in screens and "NO NEW MARK" in data
    assert "FIXTURE PAYLOAD · NOT PRODUCTION" in HTML.read_text()
    assert "B.isFixture(" in data


def test_the_floor_reads_only_command_get_endpoints():
    data = _code(DATA.read_text())
    paths = set(re.findall(r"'(/api/command/[^'?]*)", data))
    assert paths == {"/api/command/floor", "/api/command/floor/",
                     "/api/command/equity/live", "/api/command/coverage"}, paths
    for f in FLOOR_FILES:
        code = _code(f.read_text())
        assert not re.search(r"method\s*:\s*['\"](POST|PUT|PATCH|DELETE)", code, re.I), f.name


def test_reduced_motion_is_respected():
    scene = _code(SCENE.read_text())
    assert "const reduced = HQ.reducedMotion();" in scene
    for guard in ("if (!reduced) {", "uFlow: {value: reduced ? 0 : 1}",
                  "camState.t = reduced ? 1 : 0"):
        assert guard in scene, guard
