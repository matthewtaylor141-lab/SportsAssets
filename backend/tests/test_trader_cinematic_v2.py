from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
CMD=ROOT/"frontend/public/command"

def read(p): return p.read_text(encoding="utf-8")

def test_real_bettortoken_logo_only_in_trader_header():
    h=read(CMD/"trader.html")
    assert 'src="brand/bettortoken-logo-white.png"' in h
    assert '<span class="brand-mark">B' not in h
    assert 'brand/brand.css' in h

def test_cinematic_layer_is_additive():
    h=read(CMD/"trader.html")
    assert 'trader.css' in h
    assert 'trader-cinematic.css' in h
    assert 'trader.js' in h
    assert 'trader-cinematic.js' in h

def test_cinematic_observer_never_fetches_or_mutates_orders():
    js=read(CMD/"trader-cinematic.js")
    assert "fetch(" not in js
    assert "XMLHttpRequest" not in js
    for bad in ["POST","PUT","PATCH","DELETE","submitOrder","placeOrder"]:
        assert bad not in js
    assert "TraderMode" in js and "inspect" in js

def test_quote_motion_requires_observed_quote_change():
    js=read(CMD/"trader-cinematic.js")
    assert "q.observation_id" in js
    assert "prior.key!==key" in js
    assert "q.bid>prior.bid" in js
    assert "q.bid<prior.bid" in js
    assert "Math.random" not in js

def test_position_wall_microcharts_use_real_quote_history():
    js=read(CMD/"trader-cinematic.js")
    assert "C.series(p)" in js
    assert "microPath" in js

def test_brain_rail_uses_existing_packet_quote_target_and_orders():
    js=read(CMD/"trader-cinematic.js")
    for token in ["C.packet(p,now)","C.standing(p)","C.target(p)","p.quote"]:
        assert token in js

def test_reduced_motion_supported():
    css=read(CMD/"trader-cinematic.css")
    assert "prefers-reduced-motion:reduce" in css
    assert ".reduced-motion" in css

def test_existing_trader_stays_get_only():
    js=read(CMD/"trader.js")
    assert "fetch('/api/command/paper/trader-mode'" in js
    assert "method:'GET'" in js
    assert "credentials:'same-origin'" in js
    assert "method:'POST'" not in js

def test_no_demo_fallback_added():
    h=read(CMD/"trader.html")
    js=read(CMD/"trader-cinematic.js")
    assert "There is no automatic demo fallback." in h
    assert "__TRADER_DEMO_INITIAL__" not in js

def test_cinematic_css_contains_mission_control_surfaces():
    css=read(CMD/"trader-cinematic.css")
    for token in [".brain-rail",".cinematic-tick",".micro-chart",".monitor-main",".wall-mode"]:
        assert token in css
