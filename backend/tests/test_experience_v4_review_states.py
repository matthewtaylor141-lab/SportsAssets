"""Experience V4 review findings (2026-10-08): no green state without data.

B1  the Trader ribbon labelled positions with an incomplete evidence packet
    MANAGE in mint green and stayed green after a readback outage;
B2  the Floor shift board read "Live floor · CURRENT" with a green dot while
    the page was signed out (it counted agent-tile text, never the read).
Plus: logo requests set the referrer policy before src; a disconnected page
marks the Broadcast DISCONNECTED; live scores survive a back/forward return.
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2] / "frontend/public/command"
JS = (ROOT / "experience-v4.js").read_text()
CSS = (ROOT / "experience-v4.css").read_text()
LGS = (ROOT / "trader-live-scores.js").read_text()


def _fn(name):
    i = JS.index("function " + name + "(")
    return JS[i:JS.index("\n", i)]


def test_the_ribbon_never_defaults_to_green_and_reads_the_evidence_pill():
    f = _fn("ribbonState")
    assert "'good'" not in f
    assert "packet-pill" in f and "contains('blocked')" in f and "disconnected" in f
    assert "TOUCH \\u2260 FILL" in f
    assert re.search(r"\.bt-v4-ribbon-state\{[^}]*color:#89d7bd", CSS) is None


def test_the_floor_shift_board_takes_its_state_from_the_floor_read():
    f = _fn("updateFloorShift")
    assert "#fl-status .fl-pill" in f and "tone==='live'" in f and "FIXTURE" in f
    # 'good' (green) only when the read itself is live and nothing needs attention
    assert "live&&!attn&&agents.length?'good':'warn'" in f
    assert "Live floor</span>" not in JS


def test_logo_requests_set_the_referrer_policy_before_the_source():
    f = _fn("img")
    assert f.index("referrerPolicy") < f.index("im.src=")
    assert 'referrerpolicy="no-referrer" src=' in JS


def test_a_disconnected_page_marks_the_broadcast():
    assert "DISCONNECTED · last received data" in JS


def test_live_scores_survive_a_back_forward_cache_return():
    assert "pagehide',e=>{if(e&&e.persisted)return;" in LGS
