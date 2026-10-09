"""Frontend preview harness: full-viewport background layers are not bars.

.github/frontend-preview/shots.js pairs every visible fixed / sticky box with
every other and reports an overlap when two intersect (scorecard 14 unit
no_fixed_bar_overlap; GitHub CI frontend_device_gate). Production device run
37834226533 failed it on desktop, iPad portrait and iPad landscape Command,
and every pair but one named a box that is the scene behind the HUD:

  #hq-stage            the WebGL headquarters canvas: viewport_share 1.0,
                       z-index 0 (the lowest on the page), drag-to-look
  #hq-tags.compact-all its desk-label overlay: 100 % of the viewport,
                       z-index 8, pointer-events none

against #hq-top (z 20), #hq-alert (22), #hq-fresh (11), the HUD columns (10)
and #hq-hint (9), all painted above them. Such a layer cannot cover a bar, so
the harness now lists a box that shows on >= 90 % of the viewport and paints
BENEATH the other box as `background_layers` (with every box it sits
beneath) instead of an overlap. The remaining pair, #hq-alert x
#hq-hint.gone, is a product defect and is fixed in hq.js, not here.

Still flagged (pinned in backend/tests/js/frontend-preview-bars.test.cjs and
checked in Chromium on fixture pages): two bars under 90 %, a full-viewport
box painted ABOVE a bar, a 89 % layer, a "background" whose stacking context
puts it above the bar. Measured locally (harness measure(), synthetic reads):
desktop / iPad Command 12 overlaps -> 0, background_layers #hq-stage (beneath
7 boxes) and #hq-tags (beneath 6); the Floor, Trader and phone views have no
such layer and report exactly what they did.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHOTS = ROOT / ".github" / "frontend-preview" / "shots.js"
SUITE = ROOT / "backend" / "tests" / "js" / "frontend-preview-bars.test.cjs"


def shots() -> str:
    return SHOTS.read_text(encoding="utf-8")


def test_pairing_node_suite_passes():
    node = shutil.which("node")
    assert node, "node is required to run the harness pairing suite"
    got = subprocess.run([node, "--test", str(SUITE)], capture_output=True, text=True, timeout=120)
    out = got.stdout + got.stderr
    assert got.returncode == 0, out[-3000:]
    assert re.search(r"^# tests 9$", out, re.M), out[-3000:]
    assert re.search(r"^# pass 9$", out, re.M), out[-3000:]
    assert re.search(r"^# fail 0$", out, re.M), out[-3000:]
    assert re.search(r"^# skipped 0$", out, re.M), out[-3000:]


def test_threshold_and_direction_are_the_documented_ones():
    src = shots()
    block = src[src.index("// >>> BAR PAIRING"):src.index("// <<< BAR PAIRING")]
    assert "const BG_SHARE = 0.9;" in block
    # the layer is the box BENEATH; only then is the pair excluded
    assert "const lo = beneath(i, j) ? i : j, hi = lo === i ? j : i;" in block
    assert "if (bars[lo].share >= BG_SHARE) {" in block
    # unchanged overlap geometry: nested pairs skipped, > 2 px both ways
    assert "if (nested(i, j)) continue;" in block
    assert "if (!(w > 2 && h > 2)) continue;" in block
    assert "overlaps.push({ a: bars[i].id, b: bars[j].id, area_px: Math.round(w * h) });" in block


def test_excluded_layers_stay_visible_in_the_record():
    src = shots()
    assert "fixed_overlaps: overlaps.slice(0, 12), background_layers: paired.background_layers," in src
    assert "id: bars[lo].id, viewport_share: bars[lo].share, z_index: bars[lo].z, pointer_events: bars[lo].pe, beneath: []" in src
    assert "L.beneath.push(bars[hi].id);" in src
    # the run log names them too
    assert "bg=${(r.background_layers || []).map(l => l.id + ':' + l.beneath.length).join(',') || 0}" in src


def test_the_viewport_share_is_measured_like_the_canvas_share():
    src = shots()
    canvas = "const shown = Math.max(0, Math.min(r.right, vw) - Math.max(r.left, 0)) * Math.max(0, Math.min(r.bottom, vh) - Math.max(r.top, 0));"
    assert src.count(canvas) == 2  # canvases and fixed boxes, the same visible-area rule
    assert "share: +(shown / (vw * vh)).toFixed(3)" in src
