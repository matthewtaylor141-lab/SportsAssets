"""The Revenue Readiness management surface (frontend/public/command/revenue.*):
reads only the authenticated same-origin readback, never substitutes demo
values, never writes, and is reachable from Command and at /revenue."""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
CMD = ROOT / "frontend" / "public" / "command"


def test_page_files_exist_and_are_wired():
    html = (CMD / "revenue.html").read_text()
    for ref in ("trader.css", "revenue.css", "brand/brand.css", "revenue.js",
                "brand/bettortoken-logo-white.png"):
        assert ref in html and (CMD / ref).exists(), ref
    # the brand stylesheet stays the LAST stylesheet in <head>
    sheets = re.findall(r'<link rel="stylesheet" href="([^"]+)"', html)
    assert sheets[-1] == "brand/brand.css"
    for sec in ("rv-plan", "rv-cash", "rv-agents", "rv-tournament", "rv-regimes", "rv-waterfall"):
        assert 'id="%s"' % sec in html


def test_reads_only_the_authenticated_readback_and_never_writes():
    js = (CMD / "revenue.js").read_text()
    assert "'/api/command/revenue-readiness'" in js
    assert "credentials: 'same-origin'" in js and "cache: 'no-store'" in js
    assert re.findall(r"fetch\(", js) == ["fetch("]
    for verb in ("POST", "PUT", "DELETE", "PATCH"):
        assert "method: '%s'" % verb not in js and 'method:"%s"' % verb not in js
    assert "localStorage" not in js


def test_failure_is_shown_as_unavailable_never_demo_values():
    js = (CMD / "revenue.js").read_text().lower()
    body = js.replace("no demo fallback", "")
    for bad in (r"\bdemo\b", r"\bsample\b", r"\bmock\b", r"fallback data", r"\bplaceholder\b"):
        assert not re.search(bad, body), bad
    assert "sign-in required" in js and "unavailable" in js and "not yet released" in js
    # every section is cleared on failure: nothing stale or invented remains
    assert "forEach(function (id) { $(id).innerHTML = ''; })" in (CMD / "revenue.js").read_text()


def test_certification_is_presented_as_never_changing_authority_and_cash_as_incumbent():
    js = (CMD / "revenue.js").read_text()
    assert "never" in js and "AUTHORITY (UNCHANGED)" in js
    assert "CASH is the incumbent" in js and "least negative" in js
    assert "Counterfactual" in js and "never shown as realized" in js


def test_reachable_from_command_centre_and_at_a_stable_url():
    center = (CMD / "center.js").read_text()
    assert '<a class="cc-tab" href="revenue.html">Revenue readiness</a>' in center
    toml = (ROOT / "netlify.toml").read_text()
    assert 'from = "https://command.bettortoken.com/revenue"' in toml
    assert 'to = "/command/revenue.html"' in toml
