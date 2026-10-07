"""The Red Team & PM Acceptance surface (frontend/public/command/redteam.*):
reads only the two authenticated same-origin readbacks, never substitutes
demo values or a fake green, lists fields without machine evidence, shows
the authority lines, never writes, and is reachable at /red-team."""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
CMD = ROOT / "frontend" / "public" / "command"


def test_files_exist_and_are_wired():
    html = (CMD / "redteam.html").read_text()
    for ref in ("trader.css", "revenue.css", "readiness.css", "venues.css",
                "redteam.css", "brand/brand.css", "redteam.js",
                "brand/bettortoken-logo-white.png"):
        assert ref in html and (CMD / ref).exists(), ref
    sheets = re.findall(r'<link rel="stylesheet" href="([^"]+)"', html)
    assert sheets[-1] == "brand/brand.css"
    for sec in ("rt-verdict", "rt-gates", "rt-controls", "rt-golden",
                "rt-board", "rt-release"):
        assert 'id="%s"' % sec in html


def test_reads_only_the_readbacks_and_never_writes_or_fakes():
    js = (CMD / "redteam.js").read_text()
    assert "'/api/command/red-team'" in js
    assert "'/api/command/pm-acceptance'" in js
    assert "credentials: 'same-origin'" in js and "cache: 'no-store'" in js
    assert re.findall(r"fetch\(", js) == ["fetch("]
    for verb in ("POST", "PUT", "DELETE", "PATCH"):
        assert "method: '%s'" % verb not in js
    assert "/api/admin" not in js
    body = js.lower().replace("no demo fallback", "")
    for bad in (r"\bdemo\b", r"\bsample\b", r"\bmock\b", r"\bplaceholder\b"):
        assert not re.search(bad, body), bad
    assert "UNMEASURED" in js and "SIGN-IN REQUIRED" in js
    assert "SECTIONS.forEach(function (id) { $(id).innerHTML = ''; })" in js
    # either read failing clears the page (no half-truth)
    assert "Promise.all([getJSON(RED_URL), getJSON(PM_URL)])" in js


def test_it_shows_the_required_acceptance_view():
    js = (CMD / "redteam.js").read_text()
    for s in ("Fields without machine evidence", "SMALL LIVE = ",
              "KALSHI LIVE MONEY = ", "ADRIANA = ", "HISTORICAL PAPER = ",
              "AUTHORITY EXPANDED = ", "NO RELEASE RECEIPT FOR THE SERVING SHA",
              "FROZEN_INTEGRATION_CASE", "Reconciliation to the PAPER ledger",
              "Kalshi NO beat Kalshi YES", "never blended",
              "not realized P&amp;L"):
        assert s in js, s
    spec_gates = ("release_lineage", "exact_sha", "no_oom",
                  "shared_worker_ump_isolated", "live_authority_shadow",
                  "digital_twin_certified", "positive_forward_edge",
                  "mechanism_breakers_green")
    for g in spec_gates:
        assert "'%s'" % g in js, g


def test_reachable_from_command_and_at_a_stable_url():
    assert '<a class="cc-tab" href="redteam.html">Red team</a>' in (
        CMD / "center.js").read_text()
    toml = (ROOT / "netlify.toml").read_text()
    assert 'from = "https://command.bettortoken.com/red-team"' in toml
    assert 'to = "/command/redteam.html"' in toml
    # the pre-existing Command Final acceptance page is left untouched
    assert (CMD / "acceptance.html").exists()
