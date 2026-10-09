"""BETTOR Floor: touch targets in the states a finger opens.

The production device run measures each page as it loads; the RC6 touch fix
(device-fit.css) covers every control it found. Probing the states a touch
user opens next (synthetic reads, Chromium, iPhone 390 px and iPad 820 px)
found more under 44 px on 0a2e7822 and on the first fix:

  agent panel (a desk tapped)   close 34 x 34, mandate <summary> 41 px tall,
                                'Open ... workspace' / 'Back to the floor' 38 px
                                (experience-v4's body.fl.bt-exp-v4 .fl-btn
                                min-height:38px out-ranked the first fix)
  Company Pulse list            agent rows 38 px
  floor map (Map, or no WebGL)  desk markers 33-42 x 54 px: the map is 1000
                                SVG units wide, so the 92-unit seat circle is
                                ~34 px on a 374 px phone map

  signed out (the harness run     sign-in card password field 42 px; the
  locally, no credential)         equity wall's inline Sign in button 28-31 x
                                  12-13 px (iPad landscape Floor)

The Trader orders view, focus desk and evidence dialog, and Command's
Floor / Markets / Capital / Reports views and desk panel, had none.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMMAND = ROOT / "frontend" / "public" / "command"


def _coarse() -> str:
    sheet = (COMMAND / "device-fit.css").read_text(encoding="utf-8")
    head = "@media (pointer:coarse){"
    i = sheet.index(head) + len(head)
    depth, j = 1, i
    while depth:
        depth += {"{": 1, "}": -1}.get(sheet[j], 0)
        j += 1
    return re.sub(r"/\*.*?\*/", "", sheet[i:j - 1], flags=re.S)


def test_agent_panel_and_pulse_list_controls_are_44px():
    coarse = _coarse()
    assert "body.fl .fl-btn,body.fl.bt-exp-v4 .fl-btn,body.fl .fl-skip{min-height:44px;}" in coarse
    assert "body.fl .fl-x{min-width:44px;min-height:44px;}" in coarse
    assert "body.fl .fl-p-sec summary{min-height:44px;}" in coarse
    assert "body.fl .hq5-pulse-agent{min-height:44px;}" in coarse
    # the rule the first fix lost to (same specificity, earlier in the cascade)
    v4 = (COMMAND / "experience-v4.css").read_text(encoding="utf-8")
    assert "body.fl.bt-exp-v4 .fl-btn{min-height:38px;border-radius:9px;}" in v4
    floor = (COMMAND / "floor.css").read_text(encoding="utf-8")
    assert ".fl-x{margin-left:auto;width:34px;height:34px;" in floor


def test_floor_map_desks_carry_a_touch_sized_transparent_disc():
    js = (COMMAND / "floor.js").read_text(encoding="utf-8")
    hit = '<circle class="m-hit" r="75" fill="transparent"/></g>'
    assert hit in js
    # it closes the desk group, after the heartbeat text: the seat circle stays
    # the group's first child, which floor.css styles for focus and selection
    assert "'<text y=\"80\" text-anchor=\"middle\" class=\"m-hb\">♥ ' + esc(hb(a)) + '</text>' +\n      '" + hit in js
    css = (COMMAND / "floor.css").read_text(encoding="utf-8")
    assert ".m-desk.sel circle:first-child{stroke-width:4}" in css
    # a 150-unit disc of a 1000-unit map is >= 44 px on a 304 px (320 px phone) map
    assert 2 * 75 * 304 / 1000 >= 44
    # the seats (floor.js: th = -75 + 150 i / 6, x = 500 + sin th R, y = 140 + cos th R 0.92,
    # R = 380) are 154-164 units apart, so neighbouring discs never overlap
    import math
    pts = [(500 + math.sin(math.radians(-75 + 25 * i)) * 380, 140 + math.cos(math.radians(-75 + 25 * i)) * 380 * 0.92)
           for i in range(7)]
    assert "const th = -75 + 150 * i / (N - 1), r = th * Math.PI / 180; pos[s.agent] = {x: cx + Math.sin(r) * R, y: cy + Math.cos(r) * R * 0.92};" in js
    assert "const W = 1000, H = 560, cx = 500, cy = 140, R = 380, N = B.SEATS.length;" in js
    assert min(math.dist(pts[i], pts[i + 1]) for i in range(6)) > 2 * 75
    core = (COMMAND / "floor-core.js").read_text(encoding="utf-8")
    seats = core[core.index("var SEATS = ["):core.index("];", core.index("var SEATS = ["))]
    assert seats.count("{agent: '") == 7  # an eighth seat would bring them closer: re-check the disc
    # nothing is drawn (transparent fill, no stroke, no CSS)
    assert 'class="m-hit"' in js and "m-hit" not in css


def test_signed_out_controls_are_44px_too():
    coarse = _coarse()
    assert "#command-unlock input,#command-unlock button{min-height:44px;}" in coarse
    assert "body.fl [data-ew-signin]{min-height:44px;min-width:44px;}" in coarse
    unlock = (COMMAND / "unlock.js").read_text(encoding="utf-8")
    # the card's own inline styles set padding, never min-height, so the sheet applies
    assert "'aria-label': 'Workspace password'," in unlock and "min-height:44" not in unlock
    assert "<button type=\"button\" data-ew-signin>Sign in</button>" in (COMMAND / "equity-wall.js").read_text(encoding="utf-8")
