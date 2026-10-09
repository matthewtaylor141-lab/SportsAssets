"""BETTOR Command / Floor / Trader on phones and tablets: device fit (RC6).

The production device run (frontend-preview 37834226533, frontend 0a2e7822;
pm-acceptance 37836393458 frontend_preview.json) failed touch_targets_ge_44
on 13 of the 14 touch views and no_fixed_bar_overlap on iPad-portrait and
iPhone-landscape Floor (scorecard 14, Command Center desktop and mobile,
135 / 153 checks, GitHub CI frontend_device_gate 3 / 17 views):

  Floor   view bar buttons 27-28 px tall, Company Live pulse 27-37 px,
          INCIDENTS / DESK header links 24 px; #hq5-floorbar ran under
          #hq5-floor-summary at 820 px (5,927 px2) and 844 px (5,243 px2)
  Trader  'Back to BETTOR Command' 20-27 px, full screen / sound 37-38 px,
          Follow Xavier / Reduce motion / Wall mode 37-38 px, Position wall /
          Standing orders 43 px, filters 31 px, search field 17 px tall
          (21 px wide at 820 px), card evidence button 27 px
  Command 'BETTOR Command home' 18-24 px, Briefing 30 px, Watch BETTOR work
          24 px

frontend/public/command/device-fit.css is loaded last on the three pages and
raises those controls to 44 x 44 px where the pointer is coarse (the
harness's touch devices report coarse). Measured locally with the harness's
own measure() and synthetic labelled reads (every production small control
and both overlaps reproduced on the base build first): 0 small targets and 0
overlaps on all 17 views after, Trader device acceptance 5 / 5 before and
after. These tests pin the rules so a later layer cannot drop them.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMMAND = ROOT / "frontend" / "public" / "command"
FIT = COMMAND / "device-fit.css"


def css() -> str:
    return FIT.read_text(encoding="utf-8")


def _block(sheet: str, head: str) -> str:
    """The body of the first `head{ ... }` block (balanced braces)."""
    i = sheet.index(head) + len(head)
    depth, j = 1, i
    while depth:
        depth += {"{": 1, "}": -1}.get(sheet[j], 0)
        j += 1
    return sheet[i:j - 1]


def _strip_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


# --- the layer is loaded, and loaded last ----------------------------------


def test_device_fit_is_the_last_stylesheet_on_command_floor_and_trader():
    for page in ("index.html", "floor.html", "trader.html"):
        html = (COMMAND / page).read_text(encoding="utf-8")
        head = html[:html.index("</head>")]
        sheets = re.findall(r'<link rel="stylesheet" href="([^"]+)"', head)
        assert sheets and sheets[-1] == "device-fit.css", (page, sheets)
        assert sheets.count("device-fit.css") == 1, page
    assert FIT.is_file()


# --- every control production measured under 44 px --------------------------


def test_coarse_pointer_block_raises_every_measured_control_to_44px():
    coarse = _strip_comments(_block(css(), "@media (pointer:coarse){"))
    for rule in (
        # Command
        "body.hq .hq-brand{min-height:44px;}",
        "body.hq #hq-nav button{min-height:44px;}",
        "body.hq .btn,body.hq [data-watch]{min-height:44px;}",
        "body.hq .desk-x{min-width:44px;min-height:44px;}",
        "body.hq .rep-pick [role=tab]{min-height:44px;}",
        # Floor
        "body.fl .hq5-pulse-btn{min-height:44px;}",
        "body.fl .hq5-view{min-height:44px;min-width:44px;flex:none;}",
        "body.fl .fl-btn,body.fl.bt-exp-v4 .fl-btn,body.fl .fl-skip{min-height:44px;}",
        ".ops-hdr a.ops-h-cell{min-height:44px;}",
        # Trader
        ".topbar .wordmark{min-height:44px;}",
        ".icon-button{min-width:44px;min-height:44px;}",
        ".heading-controls .button,.workspace-toolbar>.button{min-height:44px;}",
        ".view-tabs button{min-height:44px;}",
        ".filters button{min-height:44px;min-width:44px;flex-shrink:0;}",
        ".workspace-toolbar .search{height:auto;min-height:44px;flex:1 1 220px;}",
        ".workspace-toolbar .search input{min-height:44px;min-width:44px;width:100%;}",
        ".card-actions .tiny-button{min-width:44px;min-height:44px;}",
        ".orders-view .table-focus{min-height:44px;}",
    ):
        assert rule in coarse, rule
    # only ever raised: nothing in the touch block shrinks a size or hides a control
    assert "max-height" not in coarse and "max-width" not in coarse
    assert "display:none" not in coarse and "font-size" not in coarse


def test_the_sizes_it_overrides_are_the_ones_production_measured():
    """The declared heights the min-heights beat (so a later edit that makes
    them taller or removes them is noticed here, not on a phone)."""
    trader = (COMMAND / "trader.css").read_text(encoding="utf-8")
    hq = (COMMAND / "hq.css").read_text(encoding="utf-8")
    hq5 = (COMMAND / "hq5-workspace.css").read_text(encoding="utf-8")
    assert ".icon-button{width:38px;height:38px;" in trader
    assert ".button{background:white;border:1px solid #d3e0d9;border-radius:9px;height:38px;" in trader
    assert ".tiny-button{border:0;background:none;border-radius:4px;font-size:13px;color:#819c8b;width:27px;height:27px}" in trader
    assert ".search{display:flex;align-items:center;gap:7px;border:1px solid #d3e0d6;border-radius:8px;background:#ffffff7a;padding:0 10px;max-width:245px;height:34px}" in trader
    assert ".alert-bar .btn { border-color: rgba(255,200,205,.6); background: rgba(255,255,255,.08); height: 30px; }" in hq
    assert ".lbl [data-watch] { white-space: nowrap; flex: none; height: 24px;" in hq
    assert ".hq5-view{\n  appearance:none;border:0;border-radius:10px;padding:8px 10px;" in hq5


def test_a_scrolling_view_bar_never_squeezes_its_buttons():
    """An explicit min-width replaces a flex item's automatic minimum (its
    label): on the iPhone the scrolling view bar squeezed the buttons to
    44 px and the labels ran together until the buttons were flex:none."""
    coarse = _strip_comments(_block(css(), "@media (pointer:coarse){"))
    for sel in ("body.fl .hq5-view{", ".filters button{"):
        rule = coarse[coarse.index(sel):]
        rule = rule[:rule.index("}")]
        assert "min-width:44px" in rule
        assert "flex:none" in rule or "flex-shrink:0" in rule, sel


# --- the floor HUD follows the taller view bar ------------------------------


def _px(text: str, pattern: str) -> int:
    m = re.search(pattern, text)
    assert m, pattern
    return int(m.group(1))


def test_floor_hud_moves_down_exactly_as_much_as_the_bar_grew():
    sheet = css()
    final = (COMMAND / "command-final.css").read_text(encoding="utf-8")
    hq5 = (COMMAND / "hq5-workspace.css").read_text(encoding="utf-8")
    # the bar: a 27 px button (production measurement) + 2 x 6 px padding +
    # 2 x 1 px border = 41 px; on touch 44 + 2 x 4 + 2 = 54 px
    assert "padding:6px;border:1px solid rgba(151,180,214,.15);border-radius:15px;" in hq5
    assert "body.fl .hq5-floorbar{padding:4px;}" in sheet
    grew = (44 + 2 * 4 + 2) - (27 + 2 * 6 + 2)
    assert grew == 13
    base_top = _px(final, r"\.cf-floor-hud\{\n position:absolute;left:50%;top:(\d+)px")
    base_phone = _px(final, r"\.cf-floor-hud\{top:(\d+)px\}")
    assert (base_top, base_phone) == (61, 55)
    assert "body.fl .cf-floor-hud{top:%dpx;}" % (base_top + grew) in _block(sheet, "@media (pointer:coarse){")
    assert "body.fl .cf-floor-hud{top:%dpx;}" % (base_phone + grew) in _block(
        sheet, "@media (pointer:coarse) and (max-width:780px){")


# --- Floor: the view bar and the summary cannot overlap ----------------------


def test_view_bar_stops_short_of_the_bounded_summary_up_to_1180px():
    sheet = css()
    hq5 = (COMMAND / "hq5-workspace.css").read_text(encoding="utf-8")
    summary_max = _px(sheet, r"\.hq5-floor-summary\{max-width:(\d+)px;\}")
    summary_right = _px(hq5, r"\.hq5-floor-summary\{\n  position:fixed;z-index:9190;right:(\d+)px;")
    bar_left_wide = _px(hq5, r"\.hq5-floorbar\{\n  position:fixed;z-index:9200;left:(\d+)px;")
    bar_left_narrow = _px(hq5, r"@media\(max-width:900px\)\{\n  \.hq5-floorbar\{left:(\d+)px\}")
    mid = _block(sheet, "@media (min-width:781px) and (max-width:1180px){")
    reserve = _px(mid, r"\.hq5-floorbar\{max-width:calc\(100% - (\d+)px\);overflow-x:auto;")
    # the bar's right edge (left + max-width) and the summary's left edge
    # (viewport - right - max-width) keep at least 8 px apart at any width
    for left in (bar_left_wide, bar_left_narrow):
        gap = reserve - left - summary_right - summary_max
        assert gap >= 8, (left, gap)
    # a bar that is bounded must scroll (and never squeeze) its buttons
    assert ".hq5-floorbar>*{flex:none;}" in mid
    # the production summary text ("2 active . 13 collaborations", 212 px) fits
    assert summary_max >= 212
    assert ".hq5-floor-summary span{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}" in sheet
    # above 1180 px the 751 px bar (left 112) ends before the bounded summary
    assert bar_left_wide + 751 < 1181 - summary_right - summary_max
    # the phone layout keeps its own rule: the summary is hidden, the bar scrolls
    assert ".hq5-floor-summary{display:none}" in hq5


def test_the_layer_stays_presentation_only():
    sheet = css()
    for banned in ("url(", "@import", "expression(", "behavior:"):
        assert banned not in sheet
    # never hides a PAPER / SHADOW badge or the pulse
    for keep in (".safe-badge", "#bt-v4-pulse", ".hq-pill"):
        assert keep not in _strip_comments(sheet)
