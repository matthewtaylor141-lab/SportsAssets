"""THE INSTITUTIONAL PAGE (frontend/public/command/institutional.html/.js):
it follows the live.html pattern (config/unlock/core/desk.css), reads every
institutional read model through BTCore.endpoint, renders a missing read as
"UNAVAILABLE — <why>" rather than zero, carries all fifteen sections
(including INSTITUTIONAL STREAM / P5) and the five questions, and is linked
from live.html. The rendering against the real route payloads is pinned in
test_institutional_page_renders_real_payloads.py."""
from __future__ import annotations

import pathlib
import re
import shutil
import subprocess

import pytest

BUNDLE = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "public" \
    / "command"
HTML = (BUNDLE / "institutional.html").read_text()
JS = (BUNDLE / "institutional.js").read_text()

SECTIONS = ("PAPER", "ACTUAL", "PORTFOLIO", "DEREK", "XAVIER", "AUDREY",
            "KAREN", "ALLOCATOR", "RISK", "CALIBRATION", "EXECUTION",
            "COVERAGE", "QUALITY", "AGENT COLLABORATION",
            "INSTITUTIONAL STREAM / P5")
READS = ("/api/command/small-live", "/api/command/agents",
         "/api/command/xavier/management", "/api/command/karen",
         "/api/command/karen/challenges", "/api/command/intel",
         "/api/command/p5/evidence",
         "/api/command/intel/allocator", "/api/command/intel/calibration",
         "/api/command/intel/attribution", "/api/command/intel/sizing",
         "/api/command/intel/risk", "/api/command/intel/regime",
         "/api/command/coverage", "/api/command/postmortems",
         "/api/command/quality", "/api/command/agents/findings")


def test_the_page_follows_the_command_page_pattern():
    assert "<title>Institutional View" in HTML
    for ref in ('src="config.js"', 'src="unlock.js"', 'src="core.js"',
                'src="institutional.js"', 'href="desk.css"'):
        assert ref in HTML, ref
    assert HTML.index('src="core.js"') < HTML.index('src="institutional.js"')
    assert 'aria-current="page">Institutional' in HTML


def test_every_section_and_every_read_is_present():
    for s in SECTIONS:
        assert "title: '%s'" % s in JS, s
    for r in READS:
        assert "path: '%s'" % r in JS, r
    for q in ("What does BETTOR own?", "Why?", "What is it worth now?",
              "What can go wrong?", "Is BETTOR adding value?"):
        assert q in JS, q


def test_reads_go_through_the_guard_and_nothing_writes():
    assert "C.endpoint(r.path)" in JS
    assert "credentials: 'same-origin'" in JS
    assert not re.search(r"method:\s*'(POST|PUT|PATCH|DELETE)'", JS)
    assert "localStorage" not in JS and "sessionStorage" not in JS
    # a missing read is "UNAVAILABLE — <why>", never zero
    assert "UNAVAILABLE — " in JS
    assert "HTTP 404" in JS


def test_live_html_links_the_page():
    assert 'href="institutional.html"' in (BUNDLE / "live.html").read_text()


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_the_script_parses():
    got = subprocess.run(["node", "--check", str(BUNDLE / "institutional.js")],
                         capture_output=True, text=True, timeout=60)
    assert got.returncode == 0, got.stderr
