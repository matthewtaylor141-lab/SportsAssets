"""The Venues & Claims surface (frontend/public/command/venues.*): reads only
the authenticated same-origin readback, never substitutes demo values or a
fake green, shows KALSHI_HEALTH and POLYMARKET_HEALTH apart, highlights the
BEST ALL-IN ROUTE, never writes, and is reachable at /venues."""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
CMD = ROOT / "frontend" / "public" / "command"


def test_files_exist_and_are_wired():
    html = (CMD / "venues.html").read_text()
    for ref in ("trader.css", "revenue.css", "readiness.css", "venues.css",
                "brand/brand.css", "venues.js",
                "brand/bettortoken-logo-white.png"):
        assert ref in html and (CMD / ref).exists(), ref
    sheets = re.findall(r'<link rel="stylesheet" href="([^"]+)"', html)
    assert sheets[-1] == "brand/brand.css"
    for sec in ("vn-health", "vn-coverage", "vn-claims", "vn-arb"):
        assert 'id="%s"' % sec in html


def test_reads_only_the_readback_and_never_writes_or_fakes():
    js = (CMD / "venues.js").read_text()
    assert "'/api/command/venues'" in js
    assert "credentials: 'same-origin'" in js and "cache: 'no-store'" in js
    assert re.findall(r"fetch\(", js) == ["fetch("]
    for verb in ("POST", "PUT", "DELETE", "PATCH"):
        assert "method: '%s'" % verb not in js
    body = js.lower().replace("no demo fallback", "")
    for bad in (r"\bdemo\b", r"\bsample\b", r"\bmock\b", r"\bplaceholder\b"):
        assert not re.search(bad, body), bad
    assert "UNMEASURED" in js and "SIGN-IN REQUIRED" in js
    assert "SECTIONS.forEach(function (id) { $(id).innerHTML = ''; })" in js


def test_it_shows_the_required_institutional_view():
    js = (CMD / "venues.js").read_text()
    css = (CMD / "venues.css").read_text()
    for s in ("KALSHI_HEALTH", "POLYMARKET_HEALTH", "never blended", "ASK",
              "FEES", "ALL-IN", "FRESH / ELIGIBLE", "TOPOLOGY",
              "CHOSEN CONTRACTS", "PRINCIPAL", "SLIPPAGE BUFFERS",
              "GUARANTEED PAYOUT", "GUARANTEED NET", "ROI",
              "no submit, cancel or capital authority"):
        assert s in js, s
    assert "BEST ALL-IN ROUTE" in css


def test_reachable_from_command_and_at_a_stable_url():
    assert '<a class="cc-tab" href="venues.html">Venues</a>' in (
        CMD / "center.js").read_text()
    toml = (ROOT / "netlify.toml").read_text()
    assert 'from = "https://command.bettortoken.com/venues"' in toml
    assert 'to = "/command/venues.html"' in toml
