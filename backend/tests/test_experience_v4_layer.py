"""BETTOR Experience V4 shared layer: pinned compatibility repairs.

Experience V4 (Astra's package, applied exactly in its own commit) carried
V3's shared-layer code. Real-browser acceptance of V3 with authenticated
production data (read-only CI preview run 37783164445, Chromium) showed every
Command / Floor / Trader page committed but NEVER reached DOMContentLoaded:
the shared layer's MutationObserver watches the whole body (childList,
characterData and the data-tone attribute) and its callback rewrote the
Pulse's data-tone and text nodes on every call, so it re-entered itself as a
microtask forever. V4 carries the same code, and a second observer whose
rAF-throttled decorate() rewrote the Trader ribbon, the Floor shift board and
re-inserted failed logos on every run: a frame-rate busy loop.

Measured locally (Chromium 1194, synthetic labelled reads, 10 s idle):
  * exact V4: main thread never answered in 25 s on /floor and /trader;
    the same build with experience-v4.js stubbed reached DOMContentLoaded
    in 0.2-0.7 s;
  * Pulse repair only: decorate() ran on every painted frame on /trader
    (desktop 63 of 63 frames, iPhone 250 of 250) rebuilding the ribbon each
    time, with 437 / 654 logo requests for logos that had failed;
  * full repair: 0 ribbon rebuilds and 0 logo requests when nothing changed.

These tests pin the narrow repairs so a later edit cannot reintroduce the
self-feeding writes. The layer must stay presentation-only: no network
client, no order / cancel / funding path.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMMAND = ROOT / "frontend" / "public" / "command"
V4_JS = COMMAND / "experience-v4.js"
V4_CSS = COMMAND / "experience-v4.css"


def js() -> str:
    return V4_JS.read_text(encoding="utf-8")


def css() -> str:
    return V4_CSS.read_text(encoding="utf-8")


# --- the self-feeding Pulse observer (page frozen before first paint) ------


def test_pulse_writes_only_real_changes():
    src = js()
    # the original unconditional writes, each a mutation of the observed body
    assert "p.dataset.tone=pulseTone(all);q('b',p).textContent=v[0]" not in src
    assert "if(p.dataset.tone!==tone) p.dataset.tone=tone;" in src
    assert "if(b.textContent!==v[0]) b.textContent=v[0];" in src
    assert "if(s.textContent!==v[1]) s.textContent=v[1];" in src


def test_shared_observer_ignores_its_own_pulse_and_evidence_writes():
    src = js()
    assert "n.id==='bt-v4-pulse'||n.id==='bt-v4-evidence'" in src
    assert "if(recs.every(function(r){return own(r.target);})) return;updatePulse();" in src


# --- the decorate() observer: every step idempotent ------------------------


def test_decorate_observer_ignores_v4_owned_surfaces():
    src = js()
    assert "var OWN={'bt-v4-game-ribbon':1,'bt-v4-broadcast':1,'bt-v4-pulse':1,'bt-v4-evidence':1,'bt-v4-palette':1};" in src
    assert "if(scheduled||recs.every(function(r){return own(r.target)}))return;" in src


def test_floor_shift_board_writes_only_changes():
    src = js()
    for unconditional in (
        "q('[data-v4-active]',sh).textContent=active",
        "q('[data-v4-attn]',sh).textContent=attn",
        "sh.dataset.tone=attn?",
        "a.dataset.v4Activity='active'",
    ):
        assert unconditional not in src, unconditional
    assert "function setT(el,v){v=String(v);if(el&&el.textContent!==v)el.textContent=v}" in src
    assert "function setD(el,k,v){if(el&&el.dataset[k]!==v)el.dataset[k]=v}" in src


def test_ribbon_rebuilds_only_when_its_content_changed():
    src = js()
    assert "if(host.__v4Html!==html){host.innerHTML=html;host.__v4Html=html}" in src
    # one delegated listener, not a new listener on every button per rebuild
    assert "qa('[data-v4-ribbon]',host).forEach(function(btn,i){btn.addEventListener" not in src
    assert "host.addEventListener('click',ribbonClick)" in src


def test_failed_logo_is_never_reinserted():
    src = js()
    assert "failedLogo[src]=1;im.remove()" in src
    assert "u=logoURL(league,name);if(!u||failedLogo[u])return;" in src
    assert "if(!failedLogo[POLY_ICON])w.appendChild(img(POLY_ICON" in src


def test_live_game_state_logo_precedence_anchor_survives():
    # BETTOR_Live_Game_State_V1's installer patches exactly this guard (it
    # defers V4's inferred logos to the provider-logo scoreboard): before
    # that install the original anchor must be present once, after it the
    # installed form once -- never both, never neither.
    src = js()
    original = "if(q('.bt-v4-team-logo',row))return;"
    installed = "if(sb.classList.contains('live-scoreboard')||q('.bt-v4-team-logo',row))return;"
    assert original not in installed
    assert src.count(installed) + src.count(original) == 1


# --- invariants the package itself promises ------------------------------


def test_layer_has_no_network_client_or_authority_path():
    src = js()
    for pat in (r"\bfetch\s*\(", r"XMLHttpRequest", r"\bWebSocket\b", r"/api/", r"\.submit\s*\(",
                r"portfolio/orders", r"funding", r"sendBeacon", r"EventSource"):
        assert re.search(pat, src, re.I) is None, pat
    for word in ("cancel", "place order", "submit order"):
        assert word not in src.lower(), word


# --- the Pulse: never floating over content, never a default green --------
#
# CI preview run 37783164445 (V3 = V4's code, production data): the fixed
# bottom-centre Pulse covered the Floor dock's status lines (Derek WORKING /
# Karen REVIEWING on iPhone, Scout / Archer on desktop), the phone Command
# 'Trader Mode' launcher and the desktop Trader position wall. Locally
# (synthetic reads) it also showed a GREEN dot beside "Attention · API /
# WORKER SHA MISMATCH" and beside a FIXTURE floor read: pulseTone() returned
# 'good' for any text without one of its few bad / warn words.


def test_pulse_is_docked_in_flow_or_hidden_never_fixed_over_content():
    src = js()
    assert "var host=page==='trader'?q('.page-heading > div'):null;" in src
    assert "if(host){p.className='bt-v4-pulse-docked';host.appendChild(p);}else{p.hidden=true;body.appendChild(p);}" in src
    # the original appended a fixed pill to <body> on every page
    assert "p.innerHTML='<i></i><b></b><span></span>';body.appendChild(p);updatePulse();" not in src
    sheet = css()
    assert "#bt-v4-pulse.bt-v4-pulse-docked{position:static;" in sheet
    assert "#bt-v4-pulse[hidden]{display:none!important;}" in sheet


def test_pulse_tone_is_never_a_default_green():
    src = js()
    body = src[src.index("function pulseTone(text){"):src.index("function pulseText(){")]
    assert "return 'neutral';" in body
    assert body.count("return 'good'") == 1
    # green needs a positive word; FIXTURE / SYNTHETIC / PREVIEW reads are a warning, a mismatch is bad
    assert "FIXTURE|PREVIEW|REPLAY|SYNTHETIC" in body
    assert "MISMATCH" in body
    assert "if(/\\bLIVE\\b|\\bCURRENT\\b|\\bCONNECTED\\b|\\bFRESH\\b|\\bOK\\b/.test(text)) return 'good';" in body
    # the Command echo of #hq-alert (CRITICAL items only) is always bad
    assert "if(crit) return ['Attention',crit,'bad'];" in src
    assert "#bt-v4-pulse[data-tone=\"neutral\"] i{background:var(--v3-muted);box-shadow:none;}" in css()


# --- top bars, links and touch targets ------------------------------------
#
# Integrator findings (3) and (4), CI preview run 37783164445 (V3 = V4's
# code): on iPhone Command the Evidence / cmd-K / Focus buttons pushed the
# header so the BETTOR logo and the SMALL LIVE SHADOW badge were clipped; on
# iPhone Trader the buttons were pushed off-screen ('EVIDE...'); the added
# controls were 32-34 px tall on touch devices. Locally (synthetic reads):
# desktop 1440 px Command header overflowed by 27 px and pushed the SMALL
# LIVE SHADOW pill off-screen (it fits without V4); V4 also appended a
# second 'Trader' link beside command-polish.js's 'Trader Mode', and its
# palette linked to "/command/...", which on command.bettortoken.com (host
# rule /* -> /command/:splat) is /command/command/ -> 404.


def _command_host_resolves(path: str) -> bool:
    """Apply netlify.toml's [[redirects]] for command.bettortoken.com in order
    and report whether the result is a committed file (directories serve
    index.html). Mirrors .github/frontend-preview/serve.js."""
    import tomllib
    rules = tomllib.loads((ROOT / "netlify.toml").read_text())["redirects"]
    host = "https://command.bettortoken.com"
    p = path.split("#", 1)[0].split("?", 1)[0] or "/"
    for r in rules:
        frm, to = r["from"], r["to"]
        if frm.startswith(host):
            frm = frm[len(host):] or "/"
        elif frm.startswith("http"):
            continue
        if frm.endswith("/*"):
            base = frm[:-1]
            if p.startswith(base):
                splat = p[len(base):]
            elif p + "/" == base:
                splat = ""
            else:
                continue
        elif frm == p:
            splat = ""
        else:
            continue
        if to.startswith("http"):
            return True  # proxied (the API)
        p = to.replace(":splat", splat)
        break
    f = ROOT / "frontend" / "public" / p.lstrip("/")
    if f.is_dir():
        f = f / "index.html"
    return f.is_file()


def test_every_v4_link_resolves_on_the_command_host():
    src = js()
    hrefs = set(re.findall(r"""href=\"(/[^\"]*)\"""", src)) | set(re.findall(r"href:'(/[^']*)'", src))
    assert "/" in hrefs and "/floor" in hrefs and "/trader" in hrefs
    assert not [h for h in hrefs if h.startswith("/command/")], hrefs
    bad = [h for h in sorted(hrefs) if not _command_host_resolves(h)]
    assert not bad, bad
    # the resolver itself must reject what was broken
    assert not _command_host_resolves("/command/")


def test_no_second_trader_link_beside_command_polish():
    src = js()
    assert "if(!q('[data-v4-trader]',hq)&&!q('a.bt-trader-launch,a[href=\"/trader\"]')){" in src


def test_actions_are_not_appended_to_the_crowded_top_bars():
    src = js()
    assert "var host=page==='command'?q('#hq-hud'):(page==='floor'?q('.fl-tools'):(q('.heading-controls')||q('.top-right')));" in src
    assert "if(page==='command') box.classList.add('bt-v4-actions--flow');" in src
    # the original appended the row to #hq-top / .top-right
    assert "var host=page==='command'?q('#hq-top'):(page==='floor'?q('.fl-tools'):q('.top-right'));" not in src
    # Evidence and Focus stay reachable from the palette (Cmd/Ctrl+K) where the row is not shown
    assert "data-v4-evidence-toggle><b>Evidence</b>" in src and "data-v4-focus-toggle aria-pressed=\"false\"><b>Focus</b>" in src
    sheet = css()
    assert ".bt-v4-actions--flow{display:none;}" in sheet
    assert "body.hq .bt-v4-actions--flow{display:flex;" in sheet
    # V4's switcher restyle leaves command-polish's Trader Mode link at its own size (it widened the bar 13 px)
    assert ".bt-v4-workspaces a:not(.bt-trader-launch),.bt-v4-workspaces button{height:32px;" in sheet


def test_added_controls_are_44px_on_coarse_pointers():
    sheet = css()
    block = sheet[sheet.index("@media (pointer:coarse){"):]
    block = block[:block.index("}\n}") + 3]
    for sel in (".bt-v4-action", ".bt-v4-workspaces a", "#bt-v4-trader-rail button", ".bt-v4-camera-bar button",
                ".bt-v4-broadcast-top button", ".bt-v4-evidence-head button", ".tiny-button.bt-v4-broadcast-button",
                ".bt-v4-palette-list a", ".bt-v4-palette-list button"):
        assert sel in block, sel
    assert "min-height:44px;min-width:44px;" in block


# --- phone Command: hero, CRITICAL bar and equity ---------------------------
#
# Integrator finding (5), CI preview run 37783164445 (V3 = V4's code): with
# the layer, iPhone Command showed the restored WebGL HQ and the freshness
# strip, then a full screen of blank space before Management Equity, and the
# CRITICAL attention card production shows first was gone (only echoed in the
# Pulse). Cause: the phone hero was position:fixed below the top bar and its
# clearance was padding-top on main#hq-hud -- but #hq-alert and #hq-fresh sit
# BEFORE the HUD in flow, so they were painted under the fixed 3D stage (the
# freshness strip only showed because its backdrop-filter makes a stacking
# context) and the HUD's padding became the blank band. Locally (synthetic
# reads, 390x844): CRITICAL bar covered by the canvas, freshness 209-417 px,
# equity at 796 px, a 379 px gap; after: bar visible at 433 px, freshness at
# 578 px, equity at 798 px with a 12 px gap.


def test_phone_hero_scrolls_and_its_clearance_sits_under_the_top_bar():
    sheet = css()
    assert 'body.hq.bt-exp-v4[data-view="command"] #hq-stage{display:block!important;position:absolute;left:0;right:0;top:54px;height:48svh;' in sheet
    assert 'body.hq.bt-exp-v4[data-view="command"] #hq-top{margin-bottom:calc(48svh + 10px);}' in sheet
    assert 'body.hq.bt-exp-v4[data-view="command"] #hq-top{margin-bottom:calc(44svh + 8px);}' in sheet
    # the clearance must not come back as HUD padding (the blank band) or the hero as position:fixed
    assert 'main#hq-hud{padding-top:calc(' not in sheet
    assert '#hq-stage{display:block!important;position:fixed;' not in sheet


def test_phone_command_keeps_the_real_webgl_hq():
    # the restored phone 3D HQ stays (the installer's hq.js edits are untouched)
    hq = (COMMAND / "hq.js").read_text(encoding="utf-8")
    assert "mod.createHQ(stage, {phone: PHONE," in hq
    assert "if (PHONE || !stage) return;" not in hq
