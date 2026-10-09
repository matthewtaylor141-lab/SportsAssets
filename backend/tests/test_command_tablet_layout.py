"""BETTOR Command on tablets: SMALL LIVE SHADOW on screen, the CRITICAL bar readable.

Found while measuring the RC6 touch fix (synthetic labelled reads; the same
on 0a2e7822, the production frontend): on iPad landscape (1180 px) the
status pills ran past the top bar -- PAPER at x 1177, SMALL LIVE SHADOW at
1253-1380 -- and on iPad portrait (820 px) all four started at x 830,
because the brand and the workspace bar (150 + 624 px) fill the row. On
iPad portrait the CRITICAL bar and the freshness strip sat in the 168 px
between the HUD columns: the alert title had 0 px, Briefing (92 px)
spilled 87 px over the Management Attention panel and the freshness cells
read "N f.". The harness counts PAPER / SHADOW words in the page text, so
it never saw a label pushed off screen.

device-fit.css gives the pills their own row on 761-1180 px (the HUD hangs
from --top, so it moves down with the bar) and, on 761-1000 px, spans the
CRITICAL bar and freshness strip across the Command view above the columns.
Measured after (synthetic reads, Chromium): iPad portrait pills x 250-800,
alert 18-802 with Briefing inside (697-789), freshness 18-802, columns from
y 255; iPad landscape pills x 610-1160; desktop and phones unchanged; 0
fixed-bar overlaps and 0 small targets on every view.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMMAND = ROOT / "frontend" / "public" / "command"


def _block(sheet: str, head: str) -> str:
    i = sheet.index(head) + len(head)
    depth, j = 1, i
    while depth:
        depth += {"{": 1, "}": -1}.get(sheet[j], 0)
        j += 1
    return sheet[i:j - 1]


def fit() -> str:
    return (COMMAND / "device-fit.css").read_text(encoding="utf-8")


def test_status_pills_get_their_own_row_on_tablets():
    tab = _block(fit(), "@media (min-width:761px) and (max-width:1180px){")
    # 92 px, plus the status-bar inset of an installed app (env() is 0 without one)
    assert "body.hq{--top:calc(92px + var(--sa-t));}" in tab
    assert ":root{--sa-t:env(safe-area-inset-top,0px);" in fit()
    assert "body.hq #hq-top{flex-wrap:wrap;align-content:center;row-gap:4px;}" in tab
    assert "body.hq #hq-top>#hq-nav{flex:1 1 0;min-width:0;max-width:max-content;}" in tab
    assert "body.hq #hq-status{flex:0 0 100%;justify-content:flex-end;margin-left:0;}" in tab
    # the two rows fit the taller bar: the touch workspace bar (44 + 2 x 4 + 2
    # = 54 px, experience-v4 .bt-v4-workspaces) + row gap + a 26 px pill
    hq = (COMMAND / "hq.css").read_text(encoding="utf-8")
    v4 = (COMMAND / "experience-v4.css").read_text(encoding="utf-8")
    assert ".hq-pill { display: inline-flex; align-items: center; gap: 6px; height: 26px;" in hq
    assert ".bt-v4-workspaces{display:flex;align-items:center;gap:3px;padding:4px;border:1px solid var(--v3-line);" in v4
    assert (44 + 2 * 4 + 2) + 4 + 26 <= 92
    # it is a Command-only variable: the Floor and Trader sheets never read --top
    for name in ("floor.css", "trader.css", "hq5-workspace.css", "command-final.css", "command-ops.css"):
        assert "var(--top)" not in (COMMAND / name).read_text(encoding="utf-8"), name
    # phones keep the pocket layout (hq.css: --top 54 px under 760 px)
    assert ":root { --top: 54px; }" in hq


def test_portrait_alert_and_freshness_span_the_view_above_the_columns():
    por = _block(fit(), "@media (min-width:761px) and (max-width:1000px){")
    # 18 px from each edge, plus the notch's side inset on a landscape phone (0 elsewhere)
    assert 'body.hq.has-critical:not(.desk-open)[data-view="command"] #hq-alert{left:calc(18px + var(--sa-l));right:calc(18px + var(--sa-r));}' in por
    assert 'body.hq:not(.desk-open)[data-view="command"] #hq-fresh{left:calc(18px + var(--sa-l));right:calc(18px + var(--sa-r));}' in por
    assert "--sa-r:env(safe-area-inset-right,0px);" in fit() and "--sa-l:env(safe-area-inset-left,0px);}" in fit()
    hq = (COMMAND / "hq.css").read_text(encoding="utf-8")
    # where hq.css puts them under the bar: alert +6 px; freshness +8 px, or +76 px under an alert
    assert "#hq-alert { position: fixed; z-index: 22; top: calc(var(--top) + 6px);" in hq
    assert "#hq-fresh { position: fixed; z-index: 11; top: calc(var(--top) + 8px);" in hq
    assert "body.has-critical #hq-fresh { top: calc(var(--top) + 76px); }" in hq
    fresh_h, gap = 77, 10  # measured strip height (nowrap cells: constant), gap to the columns
    plain = int(re.search(r'body\.hq\[data-view="command"\] \.hud-col\{top:calc\(var\(--top\) \+ (\d+)px\);\}', por).group(1))
    crit = int(re.search(r'body\.hq\.has-critical\[data-view="command"\] \.hud-col\{top:calc\(var\(--top\) \+ (\d+)px\);\}', por).group(1))
    assert plain == 8 + fresh_h + gap
    assert crit == 76 + fresh_h + gap
    # the alert (64 px with a 44 px Briefing on touch) ends before the freshness strip starts
    assert 6 + 64 <= 76
    # a desk open keeps hq.css's own right edge beside the desk panel
    assert "body.desk-open #hq-fresh { right: 418px; }" in hq
    assert "body.has-critical.desk-open #hq-alert { right: 418px; }" in hq
