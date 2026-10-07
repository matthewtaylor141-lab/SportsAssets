from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMMAND = ROOT / "frontend" / "public" / "command"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_phone_floor_routes_to_dedicated_3d_floor():
    src = read(COMMAND / "hq.js")
    assert "if (PHONE && view === 'floor'" in src
    assert "location.assign('/floor')" in src
    assert "if (PHONE && S.view === 'floor')" in src
    assert "location.replace('/floor')" in src


def test_dedicated_floor_remains_phone_webgl_capable():
    src = read(COMMAND / "floor.js")
    css = read(COMMAND / "floor.css")
    assert "phone: PHONE" in src
    assert "createFloor($('#fl-stage')" in src
    assert "state.view = gl ? '3d' : '2d'" in src
    assert ".fl-stage{flex:none;width:100%;height:62vh;min-height:420px}" in css


def test_mobile_trader_launcher_is_not_inserted_into_hidden_desktop_nav():
    src = read(COMMAND / "command-polish.js")
    css = read(COMMAND / "command-polish.css")
    assert "link.href='/trader'" in src
    assert "const nav=mobile?null:" in src
    assert "bt-trader-launch--mobile-docked" in src
    assert "bottom:calc(76px + env(safe-area-inset-bottom))" in css


def test_trader_has_stable_management_route():
    netlify = read(ROOT / "netlify.toml")
    assert 'from = "https://command.bettortoken.com/trader"' in netlify
    assert 'to = "/command/trader.html"' in netlify


def test_trader_mode_stays_read_only_and_live_polled():
    src = read(COMMAND / "trader.js")
    assert "fetch('/api/command/paper/trader-mode'" in src
    assert "method:'GET'" in src
    assert "setTimeout(poll,backoff)" in src
    assert "credentials:'same-origin'" in src
    assert "method:'POST'" not in src
