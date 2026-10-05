"""THE EQUITY WALL PAGE (frontend/public/command/equity-wall.{js,css}).

Static proofs over the shipped files:
  * one global (window.BTEquityWall), and the homepage hook only wraps
    BTPaper.mount -- app.js, office.js and paper.js are untouched;
  * it reads only GET /api/command/equity/live and /equity/curve,
    same-origin, and never posts;
  * nothing moves on its own: no random number, no requestAnimationFrame
    loop; the only timers are the poll, the curve refresh, the per-second
    AGE text and the flash clean-up;
  * no figure adds the paper book to the actual book, or one venue to the
    other: no key or label sums them;
  * reduced motion is respected; the homepage loads it after paper.js.
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "public" / "command"
JS = (ROOT / "equity-wall.js").read_text()
CSS = (ROOT / "equity-wall.css").read_text()
# The legacy COMMAND shell moved byte-for-byte to classic.html when Command
# Center V2 was promoted to index.html (2026-10-05).
INDEX = (ROOT / "classic.html").read_text()


def _code(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(line.split(" // ")[0] for line in src.splitlines()
                     if not line.strip().startswith("//"))


CODE = _code(JS)


def test_one_global_and_a_wrapper_only():
    assigned = set(re.findall(r"window\.([A-Za-z_$][\w$]*)\s*=(?!=)", CODE))
    assert assigned == {"BTEquityWall"}, assigned
    assert "window.BTPaper.mount = function" in CODE
    for api in ("mount:", "subscribe:", "state:", "paint:", "refresh:"):
        assert api in CODE, api


def test_it_reads_only_the_two_equity_routes_with_get():
    urls = set(re.findall(r"'(/api/[^']*)'", CODE))
    assert urls == {"/api/command/equity/live", "/api/command/equity/curve"}, urls
    assert "method:" not in CODE and "'POST'" not in CODE
    assert CODE.count("credentials: 'same-origin'") == 2
    assert "localStorage" in CODE          # the chosen window only
    assert re.search(r"localStorage\.setItem\(STORE_KEY", CODE)


def test_nothing_moves_on_its_own():
    assert "Math.random" not in CODE
    assert "requestAnimationFrame" not in CODE
    intervals = re.findall(r"setInterval\(([^,]+),", CODE)
    assert sorted(i.strip() for i in intervals) == sorted([
        "function () { if (S.subs.length && !document.hidden) { refreshCurves(false); } }",
        "ages"]), intervals
    # the odometer only rolls when the formatted value changed
    assert "if (prev === text) { return; }" in CODE
    assert "oldEq !== newEq" in CODE


def test_no_figure_combines_the_books_or_the_venues():
    # The owner's required labels "PAPER TOTAL EQUITY" and "COMBINED
    # ACCOUNTING TOTAL" (2026-10-04) total the PAPER book's own sleeves only --
    # never paper with actual money, never one venue with another -- so exactly
    # those paper-only phrases (and the sleeve card's reference back to the
    # paper total) are allowed; every other "total"/"combined" still fails.
    low = CODE.lower()
    for ok in ("paper total equity", "the paper total above",
               "combined accounting total"):
        low = low.replace(ok, "")
    for bad in ("total", "combined", "grand", "net worth", "sum("):
        assert bad not in low, bad
    assert not re.search(r"paper[^;\n]{0,40}equity_usd\s*\+", CODE)
    assert not re.search(r"polymarket_us[^;\n]{0,60}\+[^;\n]{0,60}kalshi", CODE)
    assert "never added together" in JS


def test_stale_and_unavailable_are_shown_never_zero():
    assert "FROZEN · STALE" in CODE and "ew-stale-banner" in CODE
    assert "UNAVAILABLE" in CODE
    assert "fin(n) ? USD.format(n) : '—'" in CODE


def test_reduced_motion_and_the_homepage_hook():
    assert "prefers-reduced-motion:reduce" in CSS.replace(" ", "")
    assert "prefers-reduced-motion: reduce" in JS
    i_paper = INDEX.index('<script src="paper.js"></script>')
    i_wall = INDEX.index('<script src="equity-wall.js"></script>')
    assert i_wall > i_paper
    assert '<link rel="stylesheet" href="equity-wall.css">' in INDEX
