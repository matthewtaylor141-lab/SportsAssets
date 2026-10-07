"""The Completion Readiness surface (frontend/public/command/readiness.*):
reads only the authenticated same-origin readback, never substitutes demo
values or a fake green (an unmeasured value says UNMEASURED), never writes,
and is reachable from Command and at /readiness."""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
CMD = ROOT / "frontend" / "public" / "command"


def test_page_files_exist_and_are_wired():
    html = (CMD / "readiness.html").read_text()
    for ref in ("trader.css", "revenue.css", "readiness.css", "brand/brand.css",
                "readiness.js", "brand/bettortoken-logo-white.png"):
        assert ref in html and (CMD / ref).exists(), ref
    sheets = re.findall(r'<link rel="stylesheet" href="([^"]+)"', html)
    assert sheets[-1] == "brand/brand.css"
    for sec in ("cr-verdict", "cr-runtime", "cr-market", "cr-prob", "cr-agents",
                "cr-gates", "cr-owner"):
        assert 'id="%s"' % sec in html


def test_reads_only_the_authenticated_readback_and_never_writes():
    js = (CMD / "readiness.js").read_text()
    assert "'/api/command/completion-readiness'" in js
    assert "credentials: 'same-origin'" in js and "cache: 'no-store'" in js
    assert re.findall(r"fetch\(", js) == ["fetch("]
    for verb in ("POST", "PUT", "DELETE", "PATCH"):
        assert "method: '%s'" % verb not in js
    assert "localStorage" not in js


def test_no_demo_values_and_no_fake_green():
    js = (CMD / "readiness.js").read_text()
    body = js.lower().replace("no demo fallback", "")
    for bad in (r"\bdemo\b", r"\bsample\b", r"\bmock\b", r"fallback data", r"\bplaceholder\b"):
        assert not re.search(bad, body), bad
    assert "UNMEASURED" in js                  # null is never shown as a value
    assert "SIGN-IN REQUIRED" in js and "NOT YET RELEASED" in js and "UNAVAILABLE" in js
    assert "SECTIONS.forEach(function (id) { $(id).innerHTML = ''; })" in js
    # the verdict chip is green only for CAPITAL_CANDIDATE, from the data
    assert "r.status === 'CAPITAL_CANDIDATE'" in js


def test_it_shows_every_required_section():
    js = (CMD / "readiness.js").read_text()
    for s in ("Runtime", "high-water / limit", "subscription mode", "market-data streams",
              "priority current / denominator", "Reference data", "Probability authority",
              "Executable EV", "Digital twin", "daily revenue readiness", "Licences never grant",
              "Owner-controlled blockers", "minutes since process start"):
        assert s in js, s


def test_reachable_from_command_centre_and_at_a_stable_url():
    center = (CMD / "center.js").read_text()
    assert '<a class="cc-tab" href="readiness.html">Readiness</a>' in center
    toml = (ROOT / "netlify.toml").read_text()
    assert 'from = "https://command.bettortoken.com/readiness"' in toml
    assert 'to = "/command/readiness.html"' in toml
