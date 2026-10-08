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
