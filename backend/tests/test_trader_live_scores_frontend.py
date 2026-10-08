"""BETTOR Live Game State V1, frontend component, inside this repository.

The package's guarded installer (applied exactly in its own commit) copies
its 20-test Node suite to backend/tests/js/trader-live-scores.test.cjs and
points it at frontend/public/command/trader-live-scores.js. In THIS
repository frontend/package.json declares "type": "module", so Node loads
that browser script as an ES module: `module` is undefined, the script takes
its browser branch and the suite dies on `document is not defined` before a
single assertion runs (observed locally: 1 file, 0 of 20 tests executed).
The package's own verifier passes only because the package directory has no
package.json.

The installed bytes stay exactly as the installer wrote them (its
verify_live_game_state.py --repo check compares them byte for byte), so this
test runs that installed suite, unmodified, against the installed script,
unmodified, in a temporary CommonJS tree with the same relative layout -- the
condition the package's own verification runs under. Node is required: a
missing interpreter fails the test rather than skipping it.

It also pins the one Experience V4 / Live Game State stylesheet conflict
found in the browser: V4's `.card-score .team-row` 4-column !important grid
overrode the provider scoreboard's 3-column grid (provider logo, name,
score), leaving the score in the third column and an empty fourth.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMMAND = ROOT / "frontend" / "public" / "command"
INSTALLED_TEST = ROOT / "backend" / "tests" / "js" / "trader-live-scores.test.cjs"
SCRIPT = COMMAND / "trader-live-scores.js"


def test_installed_node_suite_passes_in_a_commonjs_tree(tmp_path: Path):
    node = shutil.which("node")
    assert node, "node is required to run the Live Game State scoreboard suite"
    # same relative layout as the repository: backend/tests/js -> ../../../frontend/public/command
    t = tmp_path / "backend" / "tests" / "js"
    c = tmp_path / "frontend" / "public" / "command"
    t.mkdir(parents=True)
    c.mkdir(parents=True)
    shutil.copyfile(INSTALLED_TEST, t / INSTALLED_TEST.name)
    shutil.copyfile(SCRIPT, c / SCRIPT.name)
    (tmp_path / "package.json").write_text(json.dumps({"type": "commonjs", "private": True}))
    got = subprocess.run([node, "--test", str(t / INSTALLED_TEST.name)],
                         capture_output=True, text=True, timeout=120, cwd=tmp_path)
    out = got.stdout + got.stderr
    assert got.returncode == 0, out[-3000:]
    assert re.search(r"^# tests 20$", out, re.M), out[-3000:]
    assert re.search(r"^# pass 20$", out, re.M), out[-3000:]
    assert re.search(r"^# fail 0$", out, re.M), out[-3000:]
    assert re.search(r"^# skipped 0$", out, re.M), out[-3000:]


def test_installed_suite_targets_the_installed_script():
    src = INSTALLED_TEST.read_text(encoding="utf-8")
    assert "require(path.join(__dirname,'../../../frontend/public/command/trader-live-scores.js'))" in src
    assert src.count("test(") == 20


def test_v4_row_grid_does_not_override_the_provider_scoreboard():
    css = (COMMAND / "experience-v4.css").read_text(encoding="utf-8")
    assert ".card-score .team-row{grid-template-columns:auto auto minmax(0,1fr) auto!important}" not in css
    assert ".card-score .scoreboard:not(.live-scoreboard) .team-row{grid-template-columns:auto auto minmax(0,1fr) auto!important}" in css
    lgs = (COMMAND / "trader-live-scores.css").read_text(encoding="utf-8")
    assert ".live-scoreboard .team-row { display:grid; grid-template-columns:38px minmax(0,1fr) auto;" in lgs


def test_trader_page_loads_the_scoreboard_before_trader_js():
    html = (COMMAND / "trader.html").read_text(encoding="utf-8")
    assert html.index('src="trader-live-scores.js"') < html.index('src="trader.js"')
    assert 'href="trader-live-scores.css"' in html
    js = (COMMAND / "trader.js").read_text(encoding="utf-8")
    assert "const liveGame=window.TraderLiveScores && window.TraderLiveScores.render(p,screen,now);" in js


def test_scoreboard_script_has_no_network_client():
    src = SCRIPT.read_text(encoding="utf-8")
    for pat in (r"\bfetch\s*\(", r"XMLHttpRequest", r"\bWebSocket\b", r"/api/", r"sendBeacon", r"EventSource"):
        assert re.search(pat, src) is None, pat
