"""TRADER MODE IS THE REAL PAGE, WIRED ADDITIVELY (developer pass).

trader.html reads only the authenticated GET /api/command/paper/trader-mode,
never a sample feed; the illustrative preview is not shipped; Command and the
Floor gain a launcher without losing anything; the desk finish replaces only
the desk-top material and both scenes dispose its texture."""
from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
CMD = ROOT / "frontend" / "public" / "command"


def test_the_page_reads_only_the_authenticated_trader_endpoint():
    js = (CMD / "trader.js").read_text()
    assert "fetch('/api/command/paper/trader-mode'" in js
    assert "credentials:'same-origin'" in js and "method:'GET'" in js
    assert js.count("fetch(") == 1
    html = (CMD / "trader.html").read_text()
    assert 'href="index.html"' in html and 'href="index.html#floor"' in html
    assert "__TRADER_PREVIEW__" not in html


def test_the_illustrative_preview_is_not_shipped():
    assert not list(ROOT.joinpath("frontend").rglob("Trader_Mode_Preview*"))


def test_command_and_floor_carry_the_launcher_without_losing_anything():
    for page in ("index.html", "floor.html"):
        s = (CMD / page).read_text()
        assert 'href="command-polish.css"' in s
        assert 'src="command-polish.js"' in s
    js = (CMD / "command-polish.js").read_text()
    # the launcher targets the /trader route, which Netlify serves from trader.html
    assert "href='/trader'" in js and "fetch(" not in js
    toml = (ROOT / "netlify.toml").read_text()
    assert 'from = "https://command.bettortoken.com/trader"' in toml
    assert 'to = "/command/trader.html"' in toml


def test_only_the_desk_top_material_changes_and_it_is_disposed():
    hq = (CMD / "hq-scene.js").read_text()
    fl = (CMD / "floor-scene.js").read_text()
    assert "deskTop: makeDeskFinish(THREE, {phone})" in hq
    assert "const deskTop = makeDeskFinish(THREE, {phone});" in fl
    assert "MAT.deskTop.map.dispose(); MAT.deskTop.dispose();" in hq
    assert "deskTop.map.dispose(); deskTop.dispose();" in fl
    dm = (CMD / "desk-materials.js").read_text()
    assert "fetch(" not in dm and "http" not in dm


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_the_display_mechanics_node_suite_passes():
    got = subprocess.run(
        ["node", "--test", str(pathlib.Path(__file__).parent / "js" /
                               "trader-core.test.cjs")],
        capture_output=True, text=True, timeout=120)
    assert got.returncode == 0, got.stdout[-2000:] + got.stderr[-2000:]
