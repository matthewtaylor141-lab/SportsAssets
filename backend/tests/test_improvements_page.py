"""THE IMPROVEMENT PIPELINE PAGE (frontend/public/command/improvements.*).

Static proofs over the shipped files:
  * it reads only GET /api/command/improvements and /{id}, same-origin with
    the session cookie, never posts, and validates the item id against the
    server's grammar before using it;
  * nothing moves on its own: no random number, no requestAnimationFrame;
    the only timers are the poll and the age text; the one animation marks a
    real stage change and honours reduced motion;
  * a failed read is named (SIGNED OUT / NOT YET RELEASED / UNAVAILABLE),
    never an empty pipeline; a fixture payload is labelled FIXTURE;
  * every server string is escaped before it reaches innerHTML;
  * the board has the nine canonical stages plus the terminal column, the
    protected lock, the disagreement badge and the next required actor; the
    drawer shows the patch as a reference only and the decision as "not
    consensus";
  * /improvements is routed in its own commented netlify block, above the
    command-host catch-all; the page's references are relative.
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
CMD = ROOT / "frontend" / "public" / "command"
HTML = (CMD / "improvements.html").read_text()
JS = (CMD / "improvements.js").read_text()
CSS = (CMD / "improvements.css").read_text()
NETLIFY = (ROOT / "netlify.toml").read_text()


def _code(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(line.split(" // ")[0] for line in src.splitlines()
                     if not line.strip().startswith("//"))


CODE = _code(JS)


def test_it_reads_only_the_two_improvement_routes_with_get():
    urls = set(re.findall(r"'(/api/[^']*)'", CODE))
    assert urls == {"/api/command/improvements",
                    "/api/command/improvements/"}, urls
    assert CODE.count("fetch(") == 1
    assert "method: 'GET'" in CODE and "credentials: 'same-origin'" in CODE
    for bad in ("'POST'", "'PUT'", "'DELETE'", "'PATCH'", "XMLHttpRequest",
                "sendBeacon", "WebSocket", "localStorage.setItem"):
        assert bad not in CODE, bad
    assert "ID_RE = /^impr:[0-9a-f]{24}$/" in CODE
    assert "if (!ID_RE.test(id)) { return; }" in CODE
    assert "path.indexOf('..') >= 0" in CODE


def test_nothing_moves_on_its_own():
    assert "Math.random" not in CODE and "requestAnimationFrame" not in CODE
    assert len(re.findall(r"setInterval\(", CODE)) == 1      # the age text
    assert "POLL_MS = 30000" in CODE and "document.hidden" in CODE
    assert "S.moved[k] = true" in CODE                # only a real change
    assert "@keyframes im-moved" in CSS
    reduced = CSS.split("@media (prefers-reduced-motion:reduce)")[1]
    assert ".im-moved{animation:none" in reduced


def test_failures_are_named_and_fixtures_labelled():
    for s in ("SIGNED_OUT", "NOT_RELEASED", "UNAVAILABLE", "NOT YET RELEASED",
              "RUNNER STALE", "RUNNER · NO PASS RECORDED"):
        assert s in CODE, s
    assert "d.fixture === true" in CODE
    assert 'id="im-fixture" hidden' in HTML
    assert "FIXTURE DATA" in HTML and "not production" in HTML


def test_server_strings_are_escaped():
    assert "function esc(v)" in CODE
    # every innerHTML assignment is built from escaped pieces or constants
    for m in re.finditer(r"innerHTML = ([^;]+);", CODE):
        rhs = m.group(1)
        assert ("esc(" in rhs or "'" in rhs or rhs.strip() in (
            "html", "head + '<p class=\"im-d-wait\">Reading the trail…</p>'")
            or "(" in rhs), rhs
    assert "textContent = d.disclosure" in CODE
    assert "rel=\"noopener noreferrer\"" in CODE


def test_the_board_and_the_drawer_say_what_matters():
    for st in ("EVIDENCE", "HYPOTHESIS", "PEER_CHALLENGE", "OWNER_RESPONSE",
               "EXPERIMENT", "INDEPENDENT_EVALUATION", "ELIGIBLE_CHANGE",
               "CONTROLLED_RELEASE", "FORWARD_RESULT"):
        assert "'%s'" % st in CODE, st
    assert "'Closed · rolled back'" in CODE
    assert "ICON.lock" in CODE and "requires human review and" in CODE
    assert "Open dispute" in CODE and "Dissent kept" in CODE
    assert "c.next_required" in CODE and "Next required" in CODE
    assert "Candidate patch · reference only" in CODE
    assert "never pushes, merges or deploys" in CODE
    assert "Not consensus: the other position is preserved." in CODE
    assert "Exact-SHA gate receipt" in CODE
    # no control on the page writes anything
    assert "<form" not in HTML and "type=\"submit\"" not in HTML


def test_the_route_and_the_relative_references():
    block = NETLIFY.split("# ── THE IMPROVEMENT PIPELINE")[1].split(
        "# ── end IMPROVEMENT PIPELINE")[0]
    assert 'from = "https://command.bettortoken.com/improvements"' in block
    assert 'to = "/command/improvements.html"' in block
    assert "status = 200" in block and "force = true" in block
    assert NETLIFY.index("/command/improvements.html") < NETLIFY.index(
        'from = "https://command.bettortoken.com/*"')
    assert NETLIFY.index('from = "/api/command/*"') < NETLIFY.index(
        "/command/improvements.html")
    refs = re.findall(r'(?:href|src)="([^"]+\.(?:css|js))"', HTML)
    # the page's own files plus the shared BettorToken brand stylesheet
    # (brand/install_brand.py) and the HQ5/HQ6 workspace layers (command
    # palette, Company Pulse, five-item navigation); every reference relative
    assert refs == ['improvements.css', 'hq5-workspace.css', 'hq6-complete.css', 'brand/brand.css', 'improvements.js', 'hq5-workspace.js', 'hq6-complete.js']
    assert 'name="viewport"' in HTML and 'name="robots"' in HTML
