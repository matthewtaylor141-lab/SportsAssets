"""A FULLY SOLD POSITION READS CLOSED, HELD 0 -- NEVER "8" -- AND THE SMALL
LIVE POSITION VIEW IS USABLE AT IPAD WIDTHS.

The protection summary carries `position_state` (order_state_truth: held <=
epsilon is CLOSED whatever position_qty = held + sold-by-filled-protection
says). The small-live payload (execmirror_view.protection_quantities) now
carries it too, and live.js renders the SERVER'S state: CLOSED shows held 0
and no position quantity. The page is driven here under node with a stub
DOM, so the rendered HTML itself is asserted, not only the source.

iPad (768-1024px): desk.css keeps every grid track minmax(0, 1fr) and stacks
a PAPER / ACTUAL column's facts one per row in that band; wide tables scroll
inside .tablewrap, never the page.
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
FRONT = ROOT / "frontend" / "public" / "command"


def test_the_small_live_payload_states_closed_for_a_fully_sold_position():
    from sportsassets import execmirror_view as V
    q = V.protection_quantities(held=0, resting=0, filled=8)
    assert q["position_qty"] == 8.0            # the historical held + sold
    assert q["position_state"] == "CLOSED" and q["held_qty_now"] == 0.0
    q = V.protection_quantities(held=3, resting=3, filled=0, pending=0)
    assert q["position_state"] == "OPEN"
    q = V.protection_quantities(held=1e-12, resting=0, filled=8)
    assert q["position_state"] == "CLOSED"


def test_both_protection_sides_carry_the_state():
    src = (ROOT / "backend" / "sportsassets" / "execmirror_view.py").read_text()
    body = src[src.index("def _protection("):src.index("def _next_review(")]
    assert body.count('"position_state": q["position_state"]') == 2
    assert body.count('"position_state": None') == 2


NODE_DRIVER = r"""
const fs = require('fs'); const vm = require('vm');
const [FILE, CASES] = process.argv.slice(-2);
const src = fs.readFileSync(FILE, "utf8");
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const window = {BTCore: {esc, endpoint: p => p}};
const document = {readyState: 'loading', addEventListener() {},
                  getElementById() { return null; }};
vm.runInNewContext(src, {window, document, setInterval() {}, console});
const col = window.BettorSmallLive.protectionCol;
const out = {};
for (const [k, side, p] of JSON.parse(CASES)) out[k] = col(side, p);
process.stdout.write(JSON.stringify(out));
"""


def _render(cases):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    r = subprocess.run([node, "-e", NODE_DRIVER, "--", str(FRONT / "live.js"),
                        json.dumps(cases)], capture_output=True, text=True,
                       timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _facts(html):
    return dict(re.findall(r'<span class="k">([^<]*)</span><span class="v">(.*?)</span></div>',
                           html))


def test_a_fully_sold_actual_position_renders_closed_held_zero_never_8():
    closed = {"held_qty": 0, "position_qty": 8, "position_state": "CLOSED",
              "standing_resting_qty": 0, "filled_protection_qty": 8,
              "unprotected_qty": 0, "pending_submission_qty": 0}
    open_ = dict(closed, held_qty=3, position_qty=11, position_state="OPEN")
    paper = {"open_qty": 0, "position_qty": 8, "position_state": "CLOSED",
             "standing_resting_qty": 0, "filled_protection_qty": 8,
             "unprotected_qty": 0}
    legacy = dict(closed)
    del legacy["position_state"]
    out = _render([["closed", "actual", closed], ["open", "actual", open_],
                   ["paper", "paper", paper], ["legacy", "actual", legacy]])
    f = _facts(out["closed"])
    assert "CLOSED" in f["Position state"]
    assert f["Held qty"] == "0"
    pq = f["Position qty (held + sold by filled protection)"]
    assert "CLOSED" in pq and "8" not in pq
    f = _facts(out["open"])
    assert "OPEN" in f["Position state"] and f["Held qty"] == "3"
    assert f["Position qty (held + sold by filled protection)"] == "11"
    f = _facts(out["paper"])
    assert f["Open qty"] == "0" and "CLOSED" in f["Position state"]
    # a payload without the field: nothing derived, quantities as sent
    f = _facts(out["legacy"])
    assert "Position state" not in f
    assert f["Position qty (held + sold by filled protection)"] == "8"


def test_the_position_view_holds_at_ipad_widths():
    css = (FRONT / "desk.css").read_text()
    band = css[css.index("@media (min-width: 760px) and (max-width: 1024px)"):]
    assert ".sl-col .facts { grid-template-columns: minmax(0, 1fr); }" in band
    assert "table.dt th { white-space: normal; }" in band
    assert ".facts.three { grid-template-columns: repeat(2, minmax(0, 1fr)); }" in band
    # every two-track grid in the small-live view is minmax(0, 1fr): a long
    # id or a nowrap pill cannot widen the page
    assert ".sl-decbranches { grid-template-columns: repeat(2, minmax(0, 1fr)); }" in css
    assert ".tablewrap { overflow-x: auto; max-width: 100%; min-width: 0; }" in css
    assert ".fact .v .pill { max-width: 100%; white-space: normal;" in css
    html = (FRONT / "live.html").read_text()
    assert 'name="viewport" content="width=device-width' in html
    # every dt table on the desk pages is inside a horizontal scroller
    for js in ("desk.js", "institutional.js"):
        t = (FRONT / js).read_text()
        assert t.count('class="dt') <= t.count("tablewrap"), js
