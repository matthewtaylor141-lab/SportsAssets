"""BETTOR Command: the drag hint is gone when it is marked gone.

Production device run 37834226533 (frontend 0a2e7822) recorded
#hq-alert x #hq-hint.gone as a fixed-bar overlap on desktop (13,674 px2),
iPad portrait (6,216 px2) and iPad landscape (13,568 px2) Command. hq.js marks
the hint `.gone` 9 s after boot and hq-scene.js on the first drag; `.gone` is
a .6 s opacity transition. A CSS transition only advances on painted frames,
and those pages painted 0-0.1 fps (fps_4s 0 in the run): reproduced locally
with the same harness timing, the hint at measurement had class "gone",
computed opacity 1, display block and its transition at currentTime 0, so it
was painted at full opacity under the CRITICAL bar (and, on iPad portrait,
between the HUD columns) until a later frame. After the fix it is `hidden`
700 ms after `.gone` by a timer, which runs without frames: display none at
measurement on every Command view, the pair is gone, and nothing else on the
page changes.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMMAND = ROOT / "frontend" / "public" / "command"


def test_boot_timer_fades_then_removes_the_hint():
    hq = (COMMAND / "hq.js").read_text(encoding="utf-8")
    assert ("setTimeout(() => { const h = $('#hq-hint'); if (h) { h.classList.add('gone'); "
            "setTimeout(() => { h.hidden = true; }, 700); } }, 9000);") in hq
    # the original fade-only line is gone
    assert "setTimeout(() => { const h = $('#hq-hint'); if (h) h.classList.add('gone'); }, 9000);" not in hq


def test_dragging_the_room_fades_then_removes_the_hint_once():
    scene = (COMMAND / "hq-scene.js").read_text(encoding="utf-8")
    assert ("function hideHint() { const h = document.getElementById('hq-hint'); "
            "if (!h || h.classList.contains('gone')) return; h.classList.add('gone'); "
            "setTimeout(() => { h.hidden = true; }, 700); }") in scene
    assert "function hideHint() { const h = document.getElementById('hq-hint'); if (h) h.classList.add('gone'); }" not in scene


def test_hidden_hint_leaves_the_layout_and_the_fade_is_kept():
    css = (COMMAND / "hq.css").read_text(encoding="utf-8")
    assert "#hq-hint[hidden] { display: none; }" in css
    # the 700 ms timer outlasts the .6 s fade, so on a painting page the fade still shows
    assert "transition: opacity .6s; pointer-events: none; }" in css
    assert "#hq-hint.gone { opacity: 0; }" in css
    # nothing gives the hint a display that would beat [hidden]
    for name in ("hq.css", "experience-v4.css", "command-polish.css", "motion.css", "device-fit.css"):
        text = (COMMAND / name).read_text(encoding="utf-8")
        for line in text.splitlines():
            if "#hq-hint" in line and "display" in line:
                assert "display: none" in line or "display:none" in line, (name, line)
