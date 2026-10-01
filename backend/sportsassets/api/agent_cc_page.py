"""THE COMMAND CENTRE SHELL FOR DEREK, XAVIER AND AUDREY.

Each of the three agent pages is one document with TWO INDEPENDENT HALVES:

  THE DATA MODULE (classic inline script: the workspace renderers in
  `agent_pages` plus `CC_CORE_JS` / `CC_BOOT_JS` here). It reads the agent
  JSON same-origin and draws every panel. It never waits for, imports or
  depends on the character.

  THE CHARACTER MODULE (`/api/command/agents/static/cc_characters.js`, an ES
  module over a vendored, pinned three.js build). A tiny inline module
  loader decides whether to load it at all: reduced motion gets one static
  frame and no loop; no WebGL, save-data, two cores or less, or two GB of
  memory or less keep the 2D portrait card and never load it. A failure in
  it leaves the data exactly as it was.

The character's pose is driven by the page only through a `cc:mode` event
whose value comes from the agent's persisted status record (agent_status:
state, activity, last heartbeat). A stale heartbeat or a failed read is the
UNAVAILABLE pose plus a banner; the character is never animated as though an
offline agent were working.

Everything shown is read from records. A field a record does not carry says
"not recorded" with the reason. Nothing here sends an order or holds a
credential; the only writes are the existing Audrey chat and directive POSTs.
"""
from __future__ import annotations

import html as _html

CC_PAGES = ("derek", "xavier", "audrey")

CC_META = {
    "derek": {"name": "Derek", "role": "Discovery & Entry",
              "blurb": ("Scans the Polymarket US catalogue, prices each "
                        "candidate against Pinnacle and the internal model, "
                        "and records every entry decision and order."),
              "persona": ("confident, energetic, opportunity-hunting; a "
                          "sharp open-collar finance look")},
    "xavier": {"name": "Xavier", "role": "Portfolio Management",
               "blurb": ("Owns every position from Derek's first fill: "
                         "protection, the spread ladder, whole-position "
                         "payoffs and the next review."),
               "persona": ("composed, analytical, authoritative; a tailored "
                           "three-piece suit and glasses")},
    "audrey": {"name": "Audrey", "role": "Audit & Intelligence",
               "blurb": ("Audits both agents daily, reports the one "
                         "authoritative company P&L, answers management and "
                         "tracks every directive to its evaluation."),
               "persona": ("glamorous and confident; fashion-forward evening "
                           "styling, a fitted satin wrap dress with a V neckline "
                           "and an asymmetric sash, statement drop earrings and "
                           "polished waves")},
}

NAV_ORDER = ("derek", "xavier", "audrey")

#: The character module and its three.js build are served by
#: `agent_pages.agents_static`; the files live in sportsassets/assets/agents.
STATIC_BASE = "/api/command/agents/static/"
CHARACTERS_JS = STATIC_BASE + "cc_characters.js"

#: The procedural characters are placeholders until licensed models are
#: loaded through the asset pipeline (cc_characters.js, CHARACTER_ASSETS).
PLACEHOLDER_TAG = "PLACEHOLDER CHARACTER — final model pending"

UNAVAILABLE_BANNER = ("UNAVAILABLE: the agent's status could not be read or "
                      "its heartbeat is stale. The character is shown in its "
                      "offline pose; nothing here is animated as if the agent "
                      "were running.")

# ════════════════════════════════════════════════════════════════════
# STYLE (dark trading-room theme, forced; readable contrast)
# ════════════════════════════════════════════════════════════════════
CC_CSS = r"""
body.cc{color-scheme:dark;--bg:#06080c;--bg-2:#0a0d13;--panel:#0e131a;--panel-2:#131a23;--ink:#eef1f6;--ink-2:#bcc4d0;--ink-3:#94a0b2;
--line:#1f2833;--line-2:#2d3947;--ok:#4fd197;--warn:#f0b54f;--bad:#ff7d73;--info:#8aa8ff;--neutral:#9aa3b1;
--d-cfg:#8f9cff;--d-eng:#bda2ff;--d-evi:#4ad8ca;--d-time:#ecc15a;--d-own:#ff9563;
--shadow:0 1px 0 rgba(255,255,255,.04) inset,0 18px 40px -22px rgba(0,0,0,.9);
--serif:"Iowan Old Style","Palatino Linotype",Palatino,"Book Antiqua",Georgia,serif}
body.cc.ag-derek{--acc:#f2a541;--acc-2:#ffcf8a}body.cc.ag-xavier{--acc:#41d3e2;--acc-2:#9beef6}body.cc.ag-audrey{--acc:#b39bff;--acc-2:#dccfff}
body.cc{background:radial-gradient(1200px 600px at 12% -10%,color-mix(in oklab,var(--acc) 13%,transparent),transparent 60%),radial-gradient(900px 500px at 100% 10%,rgba(40,70,120,.18),transparent 60%),var(--bg)}
.cc :focus-visible{outline:2px solid var(--acc-2);outline-offset:2px;border-radius:6px}
.sr-only{position:absolute!important;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap;border:0}
.skip{position:absolute;left:12px;top:-60px;z-index:50;background:var(--acc);color:#000;padding:8px 12px;border-radius:8px;font-weight:700}
.skip:focus{top:10px}
/* persistent agent navigation */
.cc-top{gap:14px}
.cc-nav{display:flex;gap:6px;flex:1;min-width:0;overflow-x:auto;scrollbar-width:none}
.cc-nav::-webkit-scrollbar{display:none}
.cc-nav a{display:flex;align-items:center;gap:10px;padding:7px 12px;border-radius:10px;border:1px solid transparent;color:var(--ink-2);white-space:nowrap;text-decoration:none}
.cc-nav a:hover{background:var(--panel-2);text-decoration:none}
.cc-nav a[aria-current=page]{background:var(--panel);border-color:var(--line-2);box-shadow:inset 0 -2px 0 var(--acc);color:var(--ink)}
.cc-nav .nm{font:650 13.5px/1.1 var(--display)}.cc-nav .rl{display:block;font:500 11px/1.2 var(--mono);letter-spacing:.06em;color:var(--ink-3)}
.cc-nav .sd{width:9px;height:9px;border-radius:50%;background:var(--ink-3);box-shadow:0 0 0 3px rgba(255,255,255,.04)}
.cc-nav .sd[data-mode=monitoring]{background:var(--ok)}.cc-nav .sd[data-mode=reviewing]{background:var(--info)}.cc-nav .sd[data-mode=waiting]{background:var(--warn)}.cc-nav .sd[data-mode=speaking]{background:var(--acc)}.cc-nav .sd[data-mode=unavailable]{background:transparent;box-shadow:inset 0 0 0 2px var(--bad)}
.cc-sub{display:flex;gap:4px}.cc-sub a{font:12px/1 var(--mono);color:var(--ink-3);padding:6px 8px;border-radius:7px}.cc-sub a:hover{color:var(--ink);background:var(--panel-2);text-decoration:none}
.cc-top>nav.cc-nav{flex:0 1 auto;min-width:0}.cc-top>nav.cc-sub{flex:0 0 auto}.cc-top .meta{margin-left:auto}
@media (max-width:1320px){.cc-sub{display:none}}
/* framed by the management shell (/derek, /xavier, /audrey): the shell draws the navigation */
.cc-framed .cc-top .brand,.cc-framed .cc-nav,.cc-framed .cc-sub{display:none}
.cc-framed .cc-top{position:static;padding:6px 24px}
/* banner */
.cc-banner{display:none;margin:0 0 14px;padding:12px 16px;border-radius:12px;border:1px solid var(--bad);background:color-mix(in oklab,var(--bad) 12%,var(--panel));color:var(--ink);font-weight:600}
body[data-cc-mode=unavailable] .cc-banner{display:block}
.cc-rehearsal{margin:0 0 14px;padding:10px 14px;border-radius:10px;border:1px dashed var(--warn);color:var(--warn);font:650 12px/1.4 var(--mono);letter-spacing:.06em}
/* hero: stage + brief */
.cc-hero{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(0,1fr);gap:18px;margin-bottom:18px}
.cc-stage{position:relative;min-height:440px;border-radius:18px;overflow:hidden;border:1px solid var(--line-2);background:radial-gradient(120% 90% at 50% 0%,#18212e 0%,#0b0f15 55%,#06080c 100%);box-shadow:var(--shadow)}
.cc-stage canvas{position:absolute;inset:0;width:100%;height:100%;display:block;opacity:0;transition:opacity .6s ease}
.cc-stage.cc-3d-on canvas{opacity:1}
.cc-fallback{position:absolute;inset:0;display:flex;align-items:flex-end;justify-content:center}
.cc-stage.cc-3d-on .cc-fallback{display:none}
.cc-fallback svg{height:92%;width:auto;max-width:100%}
.cc-fallback .fbnote{position:absolute;right:14px;bottom:12px;font:600 10.5px/1.3 var(--mono);color:var(--ink-3);letter-spacing:.06em;text-align:right;max-width:46%}
.cc-ov{position:absolute;left:0;right:0;top:0;padding:18px 20px;display:flex;flex-direction:column;gap:6px;background:linear-gradient(180deg,rgba(4,6,9,.82),rgba(4,6,9,0));pointer-events:none}
.cc-ai{align-self:flex-start;display:inline-flex;gap:6px;align-items:center;font:700 10.5px/1 var(--mono);letter-spacing:.16em;padding:5px 8px;border-radius:6px;border:1px solid var(--acc);color:var(--acc-2);background:rgba(0,0,0,.35)}
.cc-ov h1{margin:2px 0 0;font:600 40px/1 var(--serif);letter-spacing:-.01em;color:#fff;text-shadow:0 2px 18px rgba(0,0,0,.7)}
.cc-ov .cc-role{font:650 12px/1 var(--mono);letter-spacing:.2em;text-transform:uppercase;color:var(--acc-2)}
.cc-st{position:absolute;left:0;right:0;bottom:0;padding:14px 18px;display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center;background:linear-gradient(0deg,rgba(4,6,9,.9),rgba(4,6,9,0));font:12px/1.35 var(--mono);color:var(--ink-2)}
.cc-st .anim{color:var(--ink)}.cc-st .anim b{color:var(--acc-2)}
.cc-ctl{position:absolute;right:14px;top:14px;display:flex;gap:6px}
.cc-real-model .cc-placeholder{display:none}.cc-real-model.cc-candidate .cc-placeholder{display:block}
.cc-placeholder{position:absolute;right:14px;top:52px;z-index:2;font:700 10.5px/1.3 var(--mono);letter-spacing:.08em;color:#1a1204;background:var(--warn);padding:5px 8px;border-radius:6px;pointer-events:none}
.cc-ctl button{background:rgba(6,8,12,.7);border-color:var(--line-2);font-size:11.5px;padding:6px 9px}
.cc-brief{display:flex;flex-direction:column;gap:12px}
.cc-brief .blurb{color:var(--ink-2);margin:0}
.cc-kpis{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:1px;background:var(--line);border:1px solid var(--line);border-radius:12px;overflow:hidden}
.cc-kpi{background:var(--panel);padding:11px 13px;min-width:0}
.cc-kpi .v{font:600 20px/1.2 var(--display);margin-top:4px;overflow-wrap:anywhere}
.cc-kpi .s{font-size:11.5px;color:var(--ink-3);margin-top:2px}
.cc-kpi a.v{display:block;color:var(--ink)}.cc-kpi a.v:hover{color:var(--acc-2)}
/* panels */
.cc-h2{font:600 22px/1.2 var(--serif);margin:26px 0 12px;letter-spacing:-.005em}
.cc-panels{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:16px}
.cc-p{grid-column:span 12;background:var(--panel);border:1px solid var(--line);border-radius:16px;box-shadow:var(--shadow);padding:16px 18px 14px;min-width:0;scroll-margin-top:80px;position:relative}
.cc-p.half{grid-column:span 6}
.cc-p>header{display:flex;align-items:flex-start;gap:10px;margin-bottom:10px;flex-wrap:wrap}
.cc-p>header h2{margin:0;font:650 16.5px/1.3 var(--display);flex:1;min-width:200px}
.cc-p>header .src{font:11px/1.3 var(--mono);color:var(--ink-3);flex-basis:100%}
.cc-p[data-status=EMPTY]{border-style:dashed}
.cc-p[data-status=UNAVAILABLE]{border-color:color-mix(in oklab,var(--bad) 55%,var(--line))}
.cc-p .boot{padding:12px 0}
.nr{font-style:normal;color:var(--warn);font:600 11px/1.3 var(--mono);border-bottom:1px dotted currentColor;cursor:help}
.nrw{display:block;font:11.5px/1.35 var(--sans);color:var(--ink-3);margin-top:2px}
.cc-demo{border:2px dashed var(--warn);background:repeating-linear-gradient(135deg,rgba(240,181,79,.05) 0 10px,transparent 10px 20px),var(--panel)}
.cc-demo .demolab{display:inline-block;font:700 11px/1.3 var(--mono);letter-spacing:.1em;color:#1a1204;background:var(--warn);padding:5px 8px;border-radius:6px;margin-bottom:10px}
/* derek rows */
.cc-tools{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:0 0 12px}
.cc-tools input[type=search]{flex:1;min-width:220px;font:14px var(--sans);color:var(--ink);background:var(--panel-2);border:1px solid var(--line-2);border-radius:10px;padding:9px 11px}
.cc-tools select{font:13px var(--mono);color:var(--ink);background:var(--panel-2);border:1px solid var(--line-2);border-radius:9px;padding:8px}
.cc-chips{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 10px}
.cc-chips button{font:600 12px/1 var(--mono);padding:7px 10px;border-radius:999px}
.cc-chips button[aria-pressed=true]{background:var(--acc);border-color:var(--acc);color:#081018}
.cc-pager{display:flex;gap:8px;align-items:center;justify-content:flex-end;margin-top:10px;font:12px var(--mono);color:var(--ink-3)}
.drow{border:1px solid var(--line);border-radius:12px;background:var(--panel-2);margin-bottom:10px}
.drow>summary{list-style:none;cursor:pointer;display:grid;grid-template-columns:minmax(0,2.2fr) repeat(5,minmax(0,1fr));gap:10px;align-items:center;padding:11px 13px}
.drow>summary::-webkit-details-marker{display:none}
.drow>summary .mk{font:600 13px/1.3 var(--display);overflow-wrap:anywhere}.drow>summary .sub{font:11px/1.3 var(--mono);color:var(--ink-3)}
.drow>summary .q{font:600 13px/1.2 var(--mono)}.drow>summary .q small{display:block;font:500 10px/1.2 var(--mono);color:var(--ink-3);letter-spacing:.06em;text-transform:uppercase}
.drow[open]>summary{border-bottom:1px solid var(--line)}
.fg{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;padding:12px 13px}
.fg>div{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:9px 11px;min-width:0}
.fg h4{margin:0 0 6px;font:650 10.5px/1.2 var(--mono);letter-spacing:.1em;text-transform:uppercase;color:var(--acc-2)}
.fg dl{margin:0;display:grid;grid-template-columns:auto 1fr;gap:3px 10px;font-size:12.5px}
.fg dt{color:var(--ink-3);font:11.5px/1.4 var(--mono)}.fg dd{margin:0;overflow-wrap:anywhere}
.fg .wide{grid-column:span 4}
.qty-prob{color:var(--info)}.qty-pp{color:var(--acc-2)}.qty-ret{color:var(--ok)}
.vb{font:650 10.5px/1 var(--mono);letter-spacing:.08em;padding:4px 6px;border-radius:5px;border:1px solid currentColor}
.vb-ENTER{color:var(--ok)}.vb-REFUSE{color:var(--neutral)}.vb-ORDER{color:var(--info)}
.os{font:650 10.5px/1 var(--mono);letter-spacing:.06em;padding:4px 6px;border-radius:5px;border:1px solid currentColor}
.os-FILLED{color:var(--ok)}.os-PARTIALLY_FILLED{color:var(--acc)}.os-CANCELLED,.os-ABANDONED{color:var(--neutral)}.os-REJECTED{color:var(--bad)}.os-UNRESOLVED{color:var(--bad);border-style:dashed}.os-INTENT_RECORDED,.os-SEND_ATTEMPTED,.os-ACKNOWLEDGED{color:var(--info)}
.chk{list-style:none;margin:0;padding:0;font-size:12px}.chk li{display:grid;grid-template-columns:58px 1fr;gap:8px;padding:4px 0;border-top:1px dashed var(--line)}.chk li:first-child{border-top:0}
.chk .PASS{color:var(--ok)}.chk .FAIL{color:var(--bad)}.chk .UNKNOWN{color:var(--warn)}
/* xavier */
.rec{display:grid;grid-template-columns:minmax(0,1.1fr) minmax(0,1fr);gap:14px}
.rec .act{font:700 34px/1.05 var(--display);color:var(--acc-2);letter-spacing:-.01em}
.rec .act small{display:block;font:600 12px/1.3 var(--mono);color:var(--ink-2);letter-spacing:.04em;margin-top:6px}
.rec .perm{margin-top:10px;padding:8px 10px;border-radius:9px;border:1px solid var(--bad);color:var(--ink);font-size:12.5px}
.rec .perm b{color:var(--bad)}
.tick{display:inline-block;font:600 11px/1 var(--mono);padding:4px 7px;border-radius:6px;border:1px solid var(--line-2);color:var(--ink-3)}
.tick.changed{color:#081018;background:var(--acc);border-color:var(--acc);animation:tickin .9s ease-out 1}
@keyframes tickin{0%{transform:translateY(8px);opacity:0}100%{transform:none;opacity:1}}
.poscard{border:1px solid var(--line);border-radius:14px;background:var(--panel-2);padding:14px;margin-bottom:12px}
.poscard h3{margin:0 0 4px;font:650 15px/1.3 var(--display)}
.pgrid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:1px;background:var(--line);border:1px solid var(--line);border-radius:10px;overflow:hidden;margin:10px 0}
.pgrid>div{background:var(--panel);padding:8px 10px;min-width:0}.pgrid .v{font:600 14px/1.3 var(--display);margin-top:3px;overflow-wrap:anywhere}
.mon{font:650 10px/1 var(--mono);letter-spacing:.08em;padding:4px 6px;border-radius:5px;border:1px dashed var(--ink-3);color:var(--ink-2)}
.sto{font:650 10px/1 var(--mono);letter-spacing:.08em;padding:4px 6px;border-radius:5px;border:1px solid var(--acc);color:var(--acc-2)}
.payoff td.num{font-variant-numeric:tabular-nums}
.segbtn{display:inline-flex;border:1px solid var(--line-2);border-radius:10px;overflow:hidden}
.segbtn button{border:0;border-radius:0;border-right:1px solid var(--line-2)}.segbtn button:last-child{border-right:0}
.segbtn button[aria-pressed=true]{background:var(--warn);color:#1a1204}
/* audrey */
.pl{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}
.pl>div{border:1px solid var(--line);border-radius:12px;padding:10px 12px;background:var(--panel-2)}
.pl .v{font:600 20px/1.2 var(--display);margin-top:4px}
.prov-banner{padding:12px 14px;border-radius:12px;margin:0 0 12px;border:1px solid var(--warn);background:color-mix(in oklab,var(--warn) 10%,var(--panel));font-size:13.5px}
.prov-banner b{font:700 12px/1.2 var(--mono);letter-spacing:.1em;color:var(--warn);display:block;margin-bottom:4px}
.prov-banner.live{border-color:var(--ok);background:color-mix(in oklab,var(--ok) 8%,var(--panel))}.prov-banner.live b{color:var(--ok)}
.sugg{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 10px}.sugg button{font:500 12px/1.2 var(--sans);padding:7px 10px;border-radius:999px}
.dtree{list-style:none;margin:0;padding:0}.dtree>li{border:1px solid var(--line);border-radius:12px;padding:10px 12px;margin-bottom:10px;background:var(--panel-2)}
.dtree ol{list-style:none;margin:6px 0 0;padding:0 0 0 16px;border-left:1px solid var(--line-2)}.dtree ol li{padding:4px 0 4px 10px;font-size:12.5px}
.prov-tag{font:650 10px/1 var(--mono);letter-spacing:.08em;color:var(--warn);border:1px dashed var(--warn);border-radius:5px;padding:3px 5px}
.plabel{display:flex;gap:10px;align-items:flex-start;min-width:0}
.tbadge{flex:none;width:34px;height:34px;border-radius:50%;display:grid;place-items:center;font:700 11px/1 var(--mono);color:#fff;background:hsl(var(--h,220) 38% 30%);box-shadow:inset 0 0 0 2px hsl(var(--h,220) 45% 55% / .7)}
.tbadge.none{background:var(--panel-2);color:var(--ink-3);box-shadow:inset 0 0 0 1px var(--line-2)}
.plabel .p1{font:650 14.5px/1.25 var(--display);color:var(--ink);overflow-wrap:anywhere}.plabel .p2{font:12px/1.35 var(--sans);color:var(--ink-2)}.plabel .p3{font:12px/1.35 var(--mono);color:var(--ink-3)}
.mdw{display:inline-block;margin:3px 4px 0 0;font:650 10px/1.3 var(--mono);color:var(--warn);border:1px dashed var(--warn);border-radius:5px;padding:2px 5px}
.sideno{font:700 10px/1 var(--mono);color:#1a0c0c;background:var(--bad);border-radius:4px;padding:3px 5px;margin-left:6px}
details.tech{margin-top:6px}details.tech summary{cursor:pointer;font:11px/1.2 var(--mono);color:var(--ink-3)}
details.tech dl{display:grid;grid-template-columns:auto 1fr auto;gap:3px 8px;font:11.5px/1.4 var(--mono);margin:6px 0 0}
details.tech dd{margin:0;overflow-wrap:anywhere;color:var(--ink-2)}details.tech button{font-size:10.5px;padding:3px 6px}
abbr.mlabel{text-decoration:none;border-bottom:1px dotted var(--acc);cursor:help}
@media (max-width:1000px){.cc-hero{grid-template-columns:1fr}.cc-p.half{grid-column:span 12}.fg{grid-template-columns:repeat(2,minmax(0,1fr))}.fg .wide{grid-column:span 2}.rec{grid-template-columns:1fr}.pgrid{grid-template-columns:repeat(2,minmax(0,1fr))}.pl{grid-template-columns:repeat(2,minmax(0,1fr))}.drow>summary{grid-template-columns:minmax(0,1fr) repeat(2,minmax(0,1fr))}}
@media (max-width:760px){.cc-stage{min-height:360px}.cc-ov h1{font-size:32px}.cc-sub{display:none}.fg{grid-template-columns:1fr}.fg .wide{grid-column:span 1}}
@media (prefers-reduced-motion:reduce){.cc-stage canvas{transition:none}.tick.changed{animation:none}}
"""

# ════════════════════════════════════════════════════════════════════
# 2D FALLBACK PORTRAITS (original flat vector art, no external asset)
# ════════════════════════════════════════════════════════════════════
_PORTRAITS = {
    "derek": """<svg viewBox="0 0 300 360" role="img" aria-label="Illustrated portrait card of Derek, an AI agent: short dark textured hair, navy blazer over an open-collar white shirt">
<defs><linearGradient id="fgd" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#f2a541" stop-opacity=".35"/><stop offset="1" stop-color="#f2a541" stop-opacity="0"/></linearGradient></defs>
<circle cx="150" cy="150" r="120" fill="url(#fgd)"/>
<path d="M40 360 C50 270 95 240 150 236 C205 240 250 270 260 360 Z" fill="#1c2a3f"/>
<path d="M118 244 L150 312 L182 244 Z" fill="#f2f0ea"/><path d="M118 244 L150 300 L136 250 Z M182 244 L150 300 L164 250 Z" fill="#d9d5cc"/>
<path d="M104 250 L150 330 L128 246 Z M196 250 L150 330 L172 246 Z" fill="#15202f"/>
<rect x="134" y="196" width="32" height="46" rx="12" fill="#a97b58"/>
<ellipse cx="150" cy="150" rx="50" ry="60" fill="#b98a67"/>
<path d="M100 138 C98 96 122 80 152 80 C184 80 204 98 200 138 C196 118 186 108 170 104 C150 112 126 110 110 118 C104 124 102 130 100 138 Z" fill="#1d1511"/>
<path d="M112 196 C126 214 174 214 188 196 C184 206 170 214 150 214 C130 214 116 206 112 196 Z" fill="#6e4f3b" opacity=".45"/>
<ellipse cx="131" cy="150" rx="6" ry="4.5" fill="#fff"/><ellipse cx="169" cy="150" rx="6" ry="4.5" fill="#fff"/>
<circle cx="132" cy="150" r="3" fill="#3a2618"/><circle cx="170" cy="150" r="3" fill="#3a2618"/>
<path d="M120 138 Q131 132 142 137 M158 137 Q169 132 180 138" stroke="#1d1511" stroke-width="4" fill="none" stroke-linecap="round"/>
<path d="M136 184 Q150 194 164 184" stroke="#7a3f33" stroke-width="3.5" fill="none" stroke-linecap="round"/>
<circle cx="205" cy="276" r="9" fill="#f2a541"/><text x="205" y="280" font-size="9" font-family="monospace" font-weight="700" text-anchor="middle" fill="#1a1204">AI</text></svg>""",
    "xavier": """<svg viewBox="0 0 300 360" role="img" aria-label="Illustrated portrait card of Xavier, an AI agent: swept-back silver hair, trimmed beard, glasses, charcoal three-piece suit and tie">
<defs><linearGradient id="fgx" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#41d3e2" stop-opacity=".3"/><stop offset="1" stop-color="#41d3e2" stop-opacity="0"/></linearGradient></defs>
<circle cx="150" cy="150" r="120" fill="url(#fgx)"/>
<path d="M36 360 C46 268 94 238 150 234 C206 238 254 268 264 360 Z" fill="#262d3b"/>
<path d="M120 242 L150 318 L180 242 Z" fill="#e7edf5"/><path d="M143 250 L157 250 L160 330 L150 344 L140 330 Z" fill="#1f6f78"/>
<path d="M126 262 L150 340 L174 262 L168 300 L150 348 L132 300 Z" fill="#1d2330"/>
<path d="M100 250 L150 340 L124 244 Z M200 250 L150 340 L176 244 Z" fill="#1b212c"/>
<path d="M196 276 l14 -4 l2 8 l-14 3 Z" fill="#41d3e2"/>
<rect x="134" y="194" width="32" height="46" rx="12" fill="#c99d7e"/>
<ellipse cx="150" cy="148" rx="50" ry="61" fill="#d8ae8e"/>
<path d="M100 170 C104 206 126 222 150 222 C174 222 196 206 200 170 C194 194 178 204 150 204 C122 204 106 194 100 170 Z" fill="#a1a1a6"/>
<path d="M98 136 C94 92 120 76 152 76 C186 76 206 94 202 136 C198 112 186 100 150 98 C118 100 104 112 98 136 Z" fill="#b9b9be"/>
<circle cx="131" cy="148" r="12" fill="none" stroke="#3c4452" stroke-width="3"/><circle cx="169" cy="148" r="12" fill="none" stroke="#3c4452" stroke-width="3"/><path d="M143 147 L157 147" stroke="#3c4452" stroke-width="3"/>
<circle cx="131" cy="149" r="3" fill="#2d3a44"/><circle cx="169" cy="149" r="3" fill="#2d3a44"/>
<path d="M119 131 Q131 127 143 131 M157 131 Q169 127 181 131" stroke="#8d8d93" stroke-width="3.5" fill="none" stroke-linecap="round"/>
<path d="M138 186 Q150 190 162 186" stroke="#7a4a3e" stroke-width="3" fill="none" stroke-linecap="round"/>
<circle cx="88" cy="276" r="9" fill="#41d3e2"/><text x="88" y="280" font-size="9" font-family="monospace" font-weight="700" text-anchor="middle" fill="#04161a">AI</text></svg>""",
    "audrey": """<svg viewBox="0 0 300 360" role="img" aria-label="Illustrated portrait card of Audrey, an AI agent: long polished dark waves, drop earrings, a fitted emerald satin evening dress with a V neckline and an asymmetric sash">
<defs><linearGradient id="fga" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#b39bff" stop-opacity=".34"/><stop offset="1" stop-color="#b39bff" stop-opacity="0"/></linearGradient>
<linearGradient id="fgd2" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#14605a"/><stop offset="1" stop-color="#0b3a37"/></linearGradient></defs>
<circle cx="150" cy="150" r="120" fill="url(#fga)"/>
<path d="M90 118 C76 182 80 256 98 306 L202 306 C220 256 224 182 210 118 Z" fill="#24150f"/>
<path d="M48 360 C58 276 100 246 150 242 C200 246 242 276 252 360 Z" fill="url(#fgd2)"/>
<path d="M120 246 L150 300 L180 246 C170 244 160 243 150 243 C140 243 130 244 120 246 Z" fill="#c98d6a"/>
<path d="M112 250 L150 304 L188 250" stroke="#14605a" stroke-width="5" fill="none"/>
<path d="M96 262 C130 290 176 318 214 352" stroke="#1c7a72" stroke-width="12" fill="none" stroke-linecap="round"/>
<path d="M132 250 Q150 276 168 250" stroke="#d4a64a" stroke-width="1.6" fill="none"/><circle cx="150" cy="272" r="3.2" fill="#b39bff"/>
<rect x="137" y="198" width="26" height="46" rx="11" fill="#b77d5b"/>
<ellipse cx="150" cy="150" rx="46" ry="58" fill="#c98d6a"/>
<ellipse cx="124" cy="170" rx="11" ry="7" fill="#d4776f" opacity=".28"/><ellipse cx="176" cy="170" rx="11" ry="7" fill="#d4776f" opacity=".28"/>
<path d="M100 152 C94 94 122 76 156 78 C190 80 208 104 202 152 C198 118 182 98 146 102 C126 108 110 124 100 152 Z" fill="#24150f"/>
<path d="M148 88 C174 94 194 114 200 146 C194 124 176 108 152 104 Z" fill="#6a3d27" opacity=".85"/>
<circle cx="103" cy="178" r="3.4" fill="#d4a64a"/><circle cx="197" cy="178" r="3.4" fill="#d4a64a"/>
<path d="M103 181 L103 194 M197 181 L197 194" stroke="#d4a64a" stroke-width="1.2"/><path d="M103 194 l-4 7 l4 7 l4 -7 Z M197 194 l-4 7 l4 7 l4 -7 Z" fill="#b39bff"/>
<ellipse cx="132" cy="150" rx="7" ry="4.6" fill="#fff"/><ellipse cx="168" cy="150" rx="7" ry="4.6" fill="#fff"/>
<circle cx="133" cy="150" r="3.4" fill="#5a3417"/><circle cx="167" cy="150" r="3.4" fill="#5a3417"/>
<path d="M123 146 Q132 141 141 145 L145 142 M159 145 Q168 141 177 146 L181 142" stroke="#120c0a" stroke-width="2" fill="none" stroke-linecap="round"/>
<path d="M121 135 Q132 128 143 133 M157 133 Q168 128 179 135" stroke="#24150f" stroke-width="3" fill="none" stroke-linecap="round"/>
<path d="M138 184 Q150 192 162 184 Q150 188 138 184 Z" fill="#9c2c44" stroke="#9c2c44" stroke-width="3" stroke-linejoin="round"/>
<circle cx="135" cy="176" r="1.3" fill="#3a2016"/>
<circle cx="212" cy="286" r="9" fill="#b39bff"/><text x="212" y="290" font-size="9" font-family="monospace" font-weight="700" text-anchor="middle" fill="#120a24">AI</text></svg>""",
}

# ════════════════════════════════════════════════════════════════════
# PAGE-SPECIFIC PANEL CONTAINERS (data renders into these)
# ════════════════════════════════════════════════════════════════════


def _panel(pid, title, src, cls=""):
    return ('<section class="cc-p %s" id="%s" data-status="READING" '
            'aria-labelledby="%s-h"><header><h2 id="%s-h">%s</h2>'
            '<span class="pill st-MISSING" data-pill>READING</span>'
            '<span class="src">%s</span></header><div data-body>'
            '<p class="boot">Reading&#8230;</p></div></section>'
            % (cls, pid, pid, pid, title, src))


_PANELS = {
    "derek": [
        ("p-coverage", "Markets under evaluation &amp; coverage gaps",
         "derek_coverage_census &#183; GET /api/command/agents/derek", "half"),
        ("p-policy", "Entry policy &amp; the worked example",
         "decision record policy_version &#183; derek_entry_decisions", "half"),
        ("p-opps", "Current qualifying opportunities",
         "ENTER decisions in the qualifying window &#183; GET "
         "/api/command/agents/derek/orders?view=opportunities", ""),
        ("p-orders", "Entry orders: pending, standing, partial, filled, "
         "cancelled, rejected, unresolved",
         "bettor_funded_intents (kind ENTRY) + bettor_funded_fills &#183; "
         "GET /api/command/agents/derek/orders?view=orders", ""),
        ("p-history", "Order &amp; decision history (server-side search)",
         "GET /api/command/agents/derek/orders?view=decisions|orders&amp;q=",
         ""),
        ("p-handoffs", "Confirmed inventory handed to Xavier",
         "agent_position_handoffs (from the first confirmed fill)", ""),
    ],
    "xavier": [
        ("p-rec", "Current preferred action",
         "bettor_xavier_decisions (latest review per position) &#183; GET "
         "/api/command/agents/xavier", ""),
        ("p-positions", "Managed positions",
         "the funded book + agent_position_handoffs + Xavier's reviews", ""),
        ("p-standing", "Protective orders",
         "standing protective order records &#183; GET "
         "/api/command/agents/xavier/standing-orders", "half"),
        ("p-ladder", "Hedge ladder &#183; monitored alternatives",
         "xavier_ladder on the latest reviews", "half"),
        ("p-payoff", "Whole-position payoff",
         "xavier_ladder.group_table &#183; GET "
         "/api/command/agents/xavier/payoff-demonstration", ""),
        ("p-reviews", "Review schedule, execution &amp; recovery",
         "reviews, execution, recovery and servicing_cadence sections", ""),
    ],
    "audrey": [
        ("p-pnl", "Company P&amp;L (one authoritative book)",
         "bettor_funded_book.command_center &#183; GET "
         "/api/command/agents/audrey/performance", ""),
        ("p-audit", "Audit results: Derek and Xavier",
         "audrey_audit_reports (latest daily report)", ""),
        ("p-directives", "Directive tracking",
         "management_directives &#8594; agent_tasks &#8594; improvement "
         "candidates &#8594; trials &#8594; releases", ""),
    ],
}


def _provider_banner() -> str:
    """The chat provider's state, read from the service at request time
    (presence of the key only, never its value)."""
    try:
        from ..agents import audrey_chat as AC
        cfg = AC.provider_config()
        st = AC.provider_status()
    except Exception as exc:                                    # noqa: BLE001
        return ('<div class="prov-banner" data-provider="UNREADABLE"><b>'
                'PROVIDER STATUS UNAVAILABLE</b>The chat service\'s provider '
                'status could not be read here (%s). Answers will state how '
                'they were produced.</div>'
                % _html.escape(type(exc).__name__))
    if not cfg.get("configured"):
        why = {"NO_ANTHROPIC_API_KEY": "the provider key is not configured "
               "on this service",
               "DISABLED_BY_AUDREY_PROVIDER": "the provider is disabled by "
               "configuration",
               "ANTHROPIC_SDK_NOT_INSTALLED": "the provider SDK is not "
               "installed"}.get(cfg.get("reason"), "the provider is not "
                                "configured")
        return ('<div class="prov-banner" data-provider="PENDING" '
                'id="prov-banner"><b>FALLBACK MODE &#183; CONVERSATIONAL MODE '
                'PENDING</b>%s (%s). Audrey answers deterministically from '
                'the records and cites them; this is not the conversational '
                'service and nothing here simulates it. Routine questions '
                'never change policy.</div>'
                % (_html.escape(why), _html.escape(str(cfg.get("reason")))))
    if st.get("first_success_at"):
        return ('<div class="prov-banner live" data-provider="LIVE" '
                'id="prov-banner"><b>CONVERSATIONAL MODE LIVE</b>The provider '
                'is configured and a live call succeeded (first at %s). Every '
                'answer still states how it was produced.</div>'
                % _html.escape(str(st.get("first_success_at"))))
    return ('<div class="prov-banner" data-provider="CONFIGURED_UNVERIFIED" '
            'id="prov-banner"><b>PROVIDER CONFIGURED &#183; LIVE VERIFICATION '
            'PENDING</b>No live provider call has succeeded on this process '
            'yet; an answer that falls back to the records says so.</div>')


SUGGESTED_QUESTIONS = (
    "Why did Derek buy this?",
    "What is Xavier considering now?",
    "How much money have we actually made?",
    "Which decisions could have been better?",
    "What changes are being tested?",
    "Prioritize reducing unpaired exposure.",
)


def chat_panel_html(base_chat_panel: str) -> str:
    sugg = ('<div class="sugg" role="group" aria-label="Suggested questions">'
            + "".join('<button type="button" data-sugg="%s">%s</button>'
                      % (_html.escape(q, quote=True), _html.escape(q))
                      for q in SUGGESTED_QUESTIONS)
            + '</div><p class="note">A suggestion fills the box; nothing is '
            'sent until you press Send. A directive ("Prioritize&#8230;") '
            'becomes a persistent task only when confirmed with the OPERATOR '
            'credential.</p>')
    return base_chat_panel.replace(
        '<div class="chatlog" id="chat-log"',
        _provider_banner() + sugg + '<div class="chatlog" id="chat-log"', 1)


def credit_html(asset: dict) -> str:
    """The model's attribution in the page footer (licensed models only)."""
    if not asset.get("model") or not asset.get("credit"):
        return ""
    lic = asset.get("license") or {}
    # the footer's general sentence describes the procedural stand-in; a
    # licensed model says plainly that it is one
    return (' <span class="cc-credit" data-credit>The 3D character on this page is '
            'a licensed model, not an original illustration. %s. Licence: %s, shipped with the '
            'model as %s.</span>' % (_html.escape(asset["credit"]),
                                    _html.escape(str(lic.get("spdx"))),
                                    _html.escape(str(lic.get("file", "")).rsplit("/", 1)[-1])))


def nav_html(current: str, page_paths: dict) -> str:
    out = []
    for k in NAV_ORDER:
        m = CC_META[k]
        out.append('<a href="%s"%s data-nav="%s" data-framed-href="/%s"><span class="sd" '
                   'data-sd="%s" title="state not read yet"></span><span>'
                   '<span class="nm">%s</span><span class="rl">%s</span>'
                   '</span></a>'
                   % (page_paths[k], ' aria-current="page"' if k == current
                      else "", k, k, k, m["name"], _html.escape(m["role"])))
    return "".join(out)


def stage_html(kind: str, endpoint: str, asset: dict | None = None) -> str:
    import json as _json
    m = CC_META[kind]
    asset = asset or {"model": None, "why": "no character config"}
    # THE FIGURE AREA HOLDS ONLY THE FIGURE. The name and role sit above it,
    # the status line, the placeholder caption, the 2D note and the pause
    # control below it, so no label or operational text is ever drawn over
    # the character (a phone at 390 px wide used to show the status line and
    # the placeholder tag across the figure). The placeholder caption keeps
    # its id and data-placeholder; the CSS hides it for a licensed model via
    # :has() on the wrapper.
    return (
        '<div class="cc-stagewrap" data-agent="%(k)s">'
        '<div class="cc-ov"><span class="cc-ai">&#9679; AI AGENT</span>'
        '<h1 id="cc-name">%(n)s</h1><span class="cc-role">%(r)s</span></div>'
        '<section class="cc-stage" id="cc-stage" data-cc-stage '
        'data-agent="%(k)s" data-cc-asset="%(asset)s" aria-labelledby="cc-name" '
        'aria-describedby="cc-alt">'
        '<canvas id="cc-canvas" role="img" aria-label="Placeholder character, '
        'final model pending: animated 3D stand-in for %(n)s, an AI agent. Pose '
        'reflects the recorded status."></canvas>'
        '<div class="cc-fallback" id="cc-fallback">%(svg)s</div>'
        '<p class="sr-only" id="cc-alt">%(ph)s. %(n)s is an AI agent (%(r)s), '
        'shown by an original procedural stand-in (%(p)s) until the licensed '
        'model is installed. The pose follows the paper runtime\'s own '
        'heartbeat: monitoring, reviewing, waiting, speaking or '
        'unavailable.</p>'
        '</section>'
        '<div class="cc-cap"><span class="cc-placeholder" id="cc-placeholder" '
        'data-placeholder>%(ph)s</span>'
        '<span class="fbnote" id="cc-fbnote">2D PORTRAIT &#183; 3D character '
        'not loaded</span>'
        '<div class="cc-ctl"><button type="button" id="cc-pause" '
        'aria-pressed="false">Pause animation</button></div></div>'
        '<div class="cc-st" aria-live="polite"><span id="cc-state">'
        '<span class="sb sb-NONE"><i></i>READING STATUS</span></span>'
        '<span class="anim" id="cc-anim">Animation: <b>waiting for the '
        'status read</b></span><span id="cc-hb"></span></div>'
        '<noscript><p class="note">Scripts are disabled, so neither the live '
        'records nor the character can be drawn here. The records are '
        'available as JSON at <a href="%(ep)s">%(ep)s</a>.</p></noscript>'
        '</div>' % {"k": kind, "n": m["name"], "r": _html.escape(m["role"]),
                    "p": _html.escape(m["persona"]), "ep": endpoint,
                    "svg": _PORTRAITS[kind], "ph": PLACEHOLDER_TAG,
                    "asset": _html.escape(_json.dumps(asset), quote=True)})


def brief_html(kind: str) -> str:
    m = CC_META[kind]
    return ('<section class="panel cc-brief" id="cc-brief" aria-label="Key '
            'figures"><p class="lbl">Mandate</p><p class="blurb">%s</p>'
            '<p class="lbl" style="margin:0">Paper session &#183; key figures '
            '<span class="sim">SIMULATED</span></p>'
            '<div class="cc-kpis" id="cc-kpis"><div class="cc-kpi"><div '
            'class="lbl">Reading</div><div class="v">&#8230;</div></div></div>'
            '<p class="note" style="margin:0">Every figure links to the record '
            'or JSON route it was read from. UNKNOWN is not zero; EMPTY is not '
            'success.</p></section>' % _html.escape(m["blurb"]))


def panels_html(kind: str, chat_html: str = "") -> str:
    body = "".join(_panel(pid, t, src, cls)
                   for pid, t, src, cls in _PANELS[kind])
    if kind == "audrey":
        body = body.replace(
            '<section class="cc-p " id="p-directives"',
            '<div style="grid-column:span 12">%s</div><section class="cc-p " '
            'id="p-directives"' % chat_html, 1)
    return '<div class="cc-panels" id="cc-panels" data-cc-data>%s</div>' % body


#: The inline MODULE loader: capability checks first; the character module is
#: imported only when the device can draw it. Separate from the data script.
LOADER_JS = r"""
(function () {
  var stage = document.getElementById('cc-stage');
  if (!stage) return;
  var note = document.getElementById('cc-fbnote');
  function keep2d(why) { stage.setAttribute('data-cc-3d', 'off'); stage.setAttribute('data-cc-why', why); if (note) note.textContent = '2D PORTRAIT · ' + why; }
  var nav = window.navigator || {};
  var conn = nav.connection || {};
  var reduced = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  if (conn.saveData) return keep2d('save-data is on');
  if (typeof nav.hardwareConcurrency === 'number' && nav.hardwareConcurrency <= 2) return keep2d('low-power device (' + nav.hardwareConcurrency + ' cores)');
  if (typeof nav.deviceMemory === 'number' && nav.deviceMemory <= 2) return keep2d('low-memory device');
  try {
    var c = document.createElement('canvas');
    var gl = c.getContext('webgl2') || c.getContext('webgl');
    if (!gl) return keep2d('WebGL unavailable');
  } catch (e) { return keep2d('WebGL unavailable'); }
  stage.setAttribute('data-cc-3d', 'loading');
  var asset = {}; try { asset = JSON.parse(stage.getAttribute('data-cc-asset') || '{}'); } catch (e) { asset = {}; }
  function placeholder() {
    return import('%%CHARACTERS%%').then(function (m) {
      return m.mount(stage, {agent: stage.getAttribute('data-agent'), reducedMotion: reduced});
    });
  }
  // a licensed model when the manifest provides one; the tagged placeholder otherwise
  (asset.model ? import('%%AVATAR%%').then(function (m) { return m.mountAvatar(stage, asset, {reducedMotion: reduced}); })
      .catch(function (e) { stage.setAttribute('data-cc-model-error', (e && e.message) || 'Error'); return placeholder(); })
    : placeholder()
  ).catch(function (e) { keep2d('3D character failed to load (' + ((e && e.name) || 'Error') + ')'); });
})();
"""

# ════════════════════════════════════════════════════════════════════
# DATA MODULE: PURE CORE (no DOM at definition time; node runs it)
# ════════════════════════════════════════════════════════════════════
CC_CORE_JS = r"""
var CC = (function (AG) {
  'use strict';
  var esc = AG.esc, isObj = AG.isObj, pick = AG.pick;
  var STALE_MIN_S = 900;
  var MODES = ['monitoring', 'reviewing', 'waiting', 'speaking', 'unavailable'];

  // ── number formats: probability, pp, return and dollars stay distinct ──
  function nr(why) { return '<em class="nr" title="' + esc(why || 'not recorded') + '">not recorded</em><span class="nrw">' + esc(why || '') + '</span>'; }
  function isNum(v) { return typeof v === 'number' && isFinite(v); }
  function prob(v) { return isNum(v) ? '<span class="qty-prob">' + v.toFixed(3) + '</span>' : null; }
  function pp(v) { return isNum(v) ? '<span class="qty-pp">' + (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v).toFixed(2) + ' pp</span>' : null; }
  function pct(v) { return isNum(v) ? '<span class="qty-ret">' + (v * 100).toFixed(1) + '%</span>' : null; }
  function usd(v) { if (!isNum(v)) return null; var s = Math.abs(v).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2}); return (v < 0 ? '−$' : '$') + s; }
  function usdS(v) { if (!isNum(v)) return null; return (v > 0 ? '+' : '') + usd(v); }
  function qty(v) { return isNum(v) ? (Number.isInteger(v) ? v.toLocaleString('en-US') : String(+v.toFixed(4))) : null; }
  function price(v) { return isNum(v) ? '$' + v.toFixed(v < 1 ? 3 : 2).replace(/0$/, '') : null; }
  function when(v, rd) { return AG.toEpoch(v) === null ? null : AG.ts(v, rd); }
  function show(val, why) { return val === null || val === undefined ? nr(why) : val; }
  function field(group, reasons, key, fmtFn, rd) {
    var v = isObj(group) ? group[key] : undefined;
    var why = isObj(reasons) ? reasons[key] : undefined;
    if (v === null || v === undefined) return nr(why || 'not in this record');
    var out = fmtFn ? fmtFn(v, rd) : (typeof v === 'object' ? '<code>' + esc(JSON.stringify(v)) + '</code>' : esc(v));
    return out === null || out === undefined ? esc(v) : out;
  }

  // ── character mode from the persisted status record ──────────────
  function staleAfter(a) {
    var c = isObj(a) && isObj(a.cadence) ? a.cadence : {};
    var iv = pick(c, ['review_interval_s', 'measured_interval_s', 'interval_s']);
    return Math.max(STALE_MIN_S, isNum(iv) ? 3 * iv : 0);
  }
  function modeOf(a, readAt) {
    if (!isObj(a)) return {mode: 'unavailable', recorded: null, why: 'no status row for this agent was returned'};
    var st = a.state === undefined ? null : a.state;
    if (st === null) return {mode: 'unavailable', recorded: null, why: a.why || 'agent_status holds no state for this agent'};
    var hb = AG.toEpoch(a.last_heartbeat_at);
    if (hb === null) return {mode: 'unavailable', recorded: st, why: 'no heartbeat is recorded'};
    var lim = staleAfter(a), age = isNum(readAt) ? readAt - hb : null;
    if (age !== null && age > lim) return {mode: 'unavailable', recorded: st, age: age, why: 'heartbeat stale: last ' + AG.dur(age) + ' before the read (limit ' + AG.dur(lim) + ')'};
    var m = {IDLE: 'monitoring', DECISION_RECORDED: 'monitoring', EVALUATING: 'reviewing', RECOVERING: 'reviewing',
             WAITING_FOR_EVIDENCE: 'waiting', WAITING_FOR_PROVIDER: 'waiting', BLOCKED: 'waiting'}[st];
    if (st === 'FAILED') return {mode: 'unavailable', recorded: st, age: age, why: 'the agent recorded FAILED' + (a.last_error ? ': ' + a.last_error : '')};
    if (!m) return {mode: 'unavailable', recorded: st, age: age, why: 'unrecognised state ' + st};
    return {mode: m, recorded: st, age: age, activity: a.activity || null, why: null};
  }

  // ── panel status ──────────────────────────────────────────────────
  function pill(status) {
    var cls = {OK: 'st-OK', EMPTY: 'st-EMPTY', UNAVAILABLE: 'st-UNAVAILABLE'}[status] || 'st-MISSING';
    return '<span class="pill ' + cls + '" data-pill>' + esc(status) + '</span>';
  }
  function emptyLine(why) { return '<div class="plain"><p class="big">Nothing recorded</p><p>' + esc(why || 'the record named no reason') + '</p><p class="mute">This is not a result.</p></div>'; }
  function unavLine(why) { return '<p class="unav">UNAVAILABLE: ' + esc(why || 'the read failed') + '</p>'; }
  function gateLine(o, url) { return AG.gate(o, url); }

  // ── management-friendly labels (from the shared server resolver) ──
  function techDetails(pairs) {
    var ps = pairs.filter(function (p) { return p[1] !== null && p[1] !== undefined && p[1] !== ''; });
    if (!ps.length) return '';
    return '<details class="tech"><summary>Technical details</summary><dl>' + ps.map(function (p) {
      return '<dt>' + esc(p[0]) + '</dt><dd>' + esc(p[1]) + '</dd><dd><button type="button" data-copy="' + esc(p[1]) + '" aria-label="Copy ' + esc(p[0]) + '">Copy</button></dd>';
    }).join('') + '</dl></details>';
  }
  function labelBlock(lbl, tech) {
    var ok = isObj(lbl) && !lbl.pending && lbl.primary;
    var b = ok && isObj(lbl.badge) ? '<span class="tbadge" style="--h:' + (+lbl.badge.hue || 0) + '" title="' + esc(lbl.badge.note || '') + '" aria-hidden="true">' + esc(lbl.badge.initials) + '</span>' : '<span class="tbadge none" aria-hidden="true">?</span>';
    var no = ok && lbl.side && lbl.side.is_no ? '<span class="sideno" title="the position holds the NO side">NO SIDE</span>' : '';
    var warn = ok ? (lbl.warnings || []).map(function (w) { return '<span class="mdw">' + esc(w) + '</span>'; }).join('') : (isObj(lbl) && lbl.pending === 'slot' ? '' : '<span class="mdw">LABEL_NOT_RESOLVED: the label service answered nothing for this record</span>');
    return '<div class="plabel">' + b + '<div style="min-width:0"><div class="p1" data-primary-label>' + (ok ? esc(lbl.primary) : (isObj(lbl) && lbl.pending === 'slot' ? 'Resolving label…' : 'Label not resolved')) + no + '</div>'
      + (ok && lbl.secondary ? '<div class="p2">' + esc(lbl.secondary) + '</div>' : '')
      + (ok && lbl.size ? '<div class="p3">' + esc(lbl.size) + '</div>' : '') + warn + techDetails(tech || []) + '</div></div>';
  }
  function labelSlot(item, tech) {
    return '<div class="plabel-slot" data-label-item="' + esc(item) + '" data-tech="' + esc(JSON.stringify(tech || [])) + '">' + labelBlock({pending: 'slot'}, tech) + '</div>';
  }

  // ── DEREK: one row, eight groups ─────────────────────────────────
  function dl(pairs) { return '<dl>' + pairs.map(function (p) { return '<dt>' + esc(p[0]) + '</dt><dd>' + p[1] + '</dd>'; }).join('') + '</dl>'; }
  function derekRow(r, rd) {
    r = isObj(r) ? r : {};
    var n = isObj(r.not_recorded) ? r.not_recorded : {};
    var I = r.instrument || {}, P = r.purchase || {}, M = r.internal || {}, K = r.pinnacle || {}, C = r.combined || {}, E = r.economics || {}, X = r.execution || {}, W = r.explanation || {};
    var rec = r.record || {};
    var sp = I.settlement_period;
    var spTxt = isObj(sp) ? esc(sp.period) + (sp.basis ? ' <span class="mute">(' + esc(sp.basis) + ')</span>' : '') + (sp.payout_event ? '<br><span class="mute">pays on ' + esc(sp.payout_event) + '</span>' : '') : null;
    var verdict = W.verdict ? '<span class="vb vb-' + esc(W.verdict) + '">' + esc(W.verdict) + '</span>' : '<span class="vb vb-ORDER">ORDER ONLY</span>';
    var ost = X.order_state ? '<span class="os os-' + esc(X.order_state) + '">' + esc(X.order_state.replace(/_/g, ' ')) + '</span>' : '<span class="mute">no order</span>';
    var t = rec.decided_at || rec.created_at;
    var tech = [['market slug', I.market], ['event key', I.event], ['decision id', rec.decision_id], ['order (intent) id', rec.intent_id], ['venue order id', X.venue_order_id], ['order intent', I.side]];
    var lb = r.label;
    var lbOk = isObj(lb) && !lb.pending && lb.primary;
    var sum = '<summary><div><div class="mk" data-primary-label>' + (lbOk ? esc(lb.primary) + (lb.side && lb.side.is_no ? '<span class="sideno">NO SIDE</span>' : '') : 'Label not resolved') + '</div><div class="sub">' + (lbOk && lb.secondary ? esc(lb.secondary) + ' · ' : '') + (t ? AG.ts(t, rd) : 'time not recorded') + '</div></div>'
      + '<div class="q"><small>verdict</small>' + verdict + '</div><div class="q"><small>order</small>' + ost + '</div>'
      + '<div class="q"><small>price × qty</small>' + (price(P.price) || '—') + ' × ' + (qty(P.quantity) || '—') + '</div>'
      + '<div class="q"><small>gross edge</small>' + (pp(E.gross_edge_pp) || '—') + '</div>'
      + '<div class="q"><small>net EV</small>' + (usdS(E.expected_net_profit_usd) || '—') + '</div></summary>';
    var alts = W.alternatives_considered;
    var altHtml = Array.isArray(alts) ? (alts.length ? '<ul style="margin:0;padding-left:16px">' + alts.map(function (a) {
      var h = AG.safeHref(a.href);
      return '<li>' + (h ? '<a href="' + esc(h) + '">' + esc(a.decision_id) + '</a>' : esc(a.decision_id)) + ' · ' + esc(a.market || '') + ' ' + esc(a.side || '') + ' · ' + esc(a.verdict || '') + (a.gross_edge_pp !== null && a.gross_edge_pp !== undefined ? ' · ' + pp(a.gross_edge_pp) : '') + (a.refusal ? ' · <span class="mute">' + esc(a.refusal) + '</span>' : '') + '</li>';
    }).join('') + '</ul><span class="nrw">' + esc(W.alternatives_basis || '') + '</span>' : '<span class="mute">none recorded on this fixture within the window</span>') : nr((n.explanation || {}).alternatives_considered);
    var blk = W.blocking_condition;
    var blkHtml = Array.isArray(blk) ? (blk.length ? blk.map(function (b) { return '<div>' + esc(b.blocks) + ': <b>' + esc(b.refusal || b.state || b.check || '') + '</b>' + (b.detail ? ' — ' + esc(b.detail) : '') + (b.dependency ? ' ' + AG.dep(b.dependency) : '') + '</div>'; }).join('') : '<span class="mute">' + esc(W.blocking_note || 'none recorded') + '</span>') : nr((n.explanation || {}).blocking_condition);
    var checks = Array.isArray(W.checks) && W.checks.length ? '<ul class="chk">' + W.checks.map(function (c) { return '<li><span class="' + esc(c.status) + '">' + esc(c.status) + '</span><span><span class="mono">' + esc(c.check) + '</span>' + (c.detail ? ' — ' + esc(c.detail) : '') + '</span></li>'; }).join('') + '</ul>' : '<span class="mute">no checks on this record</span>';
    var settle = E.settlement_states ? '<div class="wide"><h4>Settlement-state valuation (voids, refunds)</h4>' + esc(E.settlement_states.status) + ' — ' + esc(E.settlement_states.detail || '') + (E.settlement_states.evidence ? '<details class="raw"><summary>rules as recorded</summary><pre>' + esc(JSON.stringify(E.settlement_states.evidence, null, 2)) + '</pre></details>' : '') + '</div>' : '';
    var body = '<div class="fg"><div class="wide">' + labelBlock(lb, tech) + '</div>'
      + '<div><h4>Instrument</h4>' + dl([['event', field(I, n.instrument, 'event')], ['market', field(I, n.instrument, 'market')], ['side', field(I, n.instrument, 'side')], ['settlement period', spTxt || nr((n.instrument || {}).settlement_period)]]) + '</div>'
      + '<div><h4>Purchase</h4>' + dl([['price', field(P, n.purchase, 'price', price)], ['quantity', field(P, n.purchase, 'quantity', function (v) { return qty(v) + ' contracts'; })], ['dollars committed', P.dollars_committed === null && isNum(P.cost_at_decision_usd) ? nr((n.purchase || {}).dollars_committed) + '<span class="nrw">cost at decision ' + usd(P.cost_at_decision_usd) + '</span>' : field(P, n.purchase, 'dollars_committed', usd)], ['dollars filled', field(P, n.purchase, 'dollars_filled', usd) + (P.dollars_filled_basis ? '<span class="nrw">' + esc(P.dollars_filled_basis) + '</span>' : '')]]) + '</div>'
      + '<div><h4>Internal estimate</h4>' + dl([['probability', field(M, n.internal, 'probability', prob)], ['model', field(M, n.internal, 'model_version')], ['at', field(M, n.internal, 'at', when, rd)]]) + '</div>'
      + '<div><h4>Pinnacle</h4>' + dl([['fair probability', field(K, n.pinnacle, 'fair_probability', prob)], ['at', field(K, n.pinnacle, 'at', when, rd)]]) + '</div>'
      + '<div><h4>Combined estimate</h4>' + dl([['blended', field(C, n.combined, 'value', prob) + (C.source ? '<span class="nrw">from ' + esc(C.source) + '</span>' : '')], ['policy', field(C, n.combined, 'policy_version')], ['combination', field(C, n.combined, 'combination_rule')], ['gross edge vs price', field(C, n.combined, 'gross_edge_vs_price_pp', pp)]]) + '</div>'
      + '<div><h4>Economics</h4>' + dl([['gross edge', field(E, n.economics, 'gross_edge_pp', pp)], ['fees', field(E, n.economics, 'fees_usd', usd)], ['exp. profit before fees', field(E, n.economics, 'expected_gross_profit_usd', usd)], ['exp. net profit', field(E, n.economics, 'expected_net_profit_usd', usdS)], ['exp. return before fees', field(E, n.economics, 'expected_return_before_fees', pct)], ['exp. return after fees', field(E, n.economics, 'expected_return_after_fees', pct)]]) + '</div>'
      + '<div><h4>Execution</h4>' + dl([['order state', X.order_state ? ost : nr((n.execution || {}).order_state)], ['filled', field(X, n.execution, 'filled_qty', qty)], ['average price', field(X, n.execution, 'average_price', price)], ['remaining', field(X, n.execution, 'remaining_qty', qty) + (X.remaining_is_live === false && isNum(X.remaining_qty) && X.remaining_qty > 0 ? '<span class="nrw">not live: the order is ' + esc(X.order_state) + '</span>' : '')]]) + '</div>'
      + '<div><h4>Explanation</h4>' + dl([['verdict', W.verdict ? verdict : nr((n.explanation || {}).verdict)], ['why selected', field(W, n.explanation, 'why_selected')]]) + '</div>'
      + '<div class="wide"><h4>Alternatives considered</h4>' + altHtml + '</div>'
      + '<div class="wide"><h4>Blocking condition</h4>' + blkHtml + '</div>'
      + settle
      + '<div class="wide"><h4>Recorded decision trace (the checks, in order)</h4>' + checks + '</div>'
      + '<div class="wide"><h4>Evidence</h4>' + AG.evidence(r.evidence) + (C.rule ? '<p class="note" style="margin:6px 0 0">Rule on this record: ' + esc(C.rule) + '</p>' : '') + '</div>'
      + '</div>';
    return '<details class="drow" data-decision="' + esc(rec.decision_id || '') + '" data-intent="' + esc(rec.intent_id || '') + '">' + sum + body + '</details>';
  }
  function derekList(j, rd) {
    if (!isObj(j)) return unavLine('no response');
    if (j.status === 'UNAVAILABLE') return unavLine(j.why);
    if (!Array.isArray(j.rows) || !j.rows.length) return emptyLine(j.why);
    return j.rows.map(function (r) { return derekRow(r, rd); }).join('');
  }

  // ── XAVIER ────────────────────────────────────────────────────────
  function secData(ws, key) { var s = isObj(ws) && isObj(ws.sections) ? ws.sections[key] : null; return isObj(s) ? s : {status: 'MISSING', why: 'the endpoint returned no ' + key + ' section', data: null}; }
  function altsFor(ws, did) {
    var s = secData(ws, 'alternatives');
    var rows = Array.isArray(s.data) ? s.data : [];
    for (var i = 0; i < rows.length; i++) if (rows[i].xavier_decision_id === did) return rows[i].alternatives || [];
    return [];
  }
  function recommendation(ws, prev, rd) {
    var rv = secData(ws, 'reviews');
    if (rv.status !== 'OK') return {status: rv.status, html: rv.status === 'UNAVAILABLE' ? unavLine(rv.why) : emptyLine(rv.why), key: null};
    var cur = (rv.data && rv.data.current) || [];
    if (!cur.length) return {status: 'EMPTY', html: emptyLine('no current review'), key: null};
    var sw = (secData(ws, 'execution').data || {}).submission_switches || {};
    var permitted = sw.FUNDED_SUBMISSION_ENABLED === true;
    var html = '', keys = [];
    cur.forEach(function (d) {
      var alts = altsFor(ws, d.xavier_decision_id);
      var chosen = null, hold = null;
      alts.forEach(function (a) { if (a.action === 'HOLD') hold = a; if (a.action === d.chosen_action && !chosen) chosen = a; });
      var key = d.xavier_decision_id + '@' + d.decided_at;
      keys.push(key);
      var changed = prev && prev.indexOf(key) < 0;
      var age = isNum(rd) && isNum(d.decided_at) ? rd - d.decided_at : null;
      var tick = changed ? '<span class="tick changed" data-tick="CHANGED">CHANGED · new decision ' + esc(d.xavier_decision_id) + '</span>'
                         : '<span class="tick" data-tick="UNCHANGED">unchanged for ' + (age === null ? '?' : AG.dur(age)) + ' (since decision ' + esc(d.xavier_decision_id) + ')</span>';
      var wcH = hold && isNum(hold.worst_case_established_usd) ? hold.worst_case_established_usd : null;
      var wcC = chosen && isNum(chosen.worst_case_established_usd) ? chosen.worst_case_established_usd : null;
      var rejected = alts.filter(function (a) { return a !== chosen; }).map(function (a) {
        return '<li><b>' + esc(a.action) + '</b>' + (a.candidate_id ? ' <span class="mono">' + esc(a.candidate_id) + '</span>' : '') + ' — ' + esc(a.blocker || (a.rankable === false ? 'not rankable' : 'ranked lower')) + (isNum(a.increment_vs_hold_usd) ? ' · vs HOLD ' + usdS(a.increment_vs_hold_usd) : '') + '</li>';
      });
      html += '<div class="rec" data-rec="' + esc(d.xavier_decision_id) + '"><div>' + labelSlot('intent:' + (d.intent_id || ''), [['group id', d.portfolio_group_id], ['intent id', d.intent_id], ['decision id', d.xavier_decision_id]])
        + '<div class="act">' + esc(d.chosen_action || 'NO ACTION RECORDED') + '<small>' + (chosen && isNum(chosen.qty_executable_at_limit) ? qty(chosen.qty_executable_at_limit) + ' executable at limit · ' : '') + (chosen && chosen.candidate_id ? esc(chosen.candidate_id) : 'quantity and price as recorded on the decision') + '</small></div>'
        + '<div style="margin-top:8px">' + tick + '</div>'
        + '<div class="perm">' + (permitted ? '<b>EXECUTION PERMITTED</b> by the submission switch as the serving code has it.' : '<b>EXECUTION NOT PERMITTED</b>: funded submission is disabled (' + esc(JSON.stringify(sw)) + '). This is a recommendation on record, not an order.') + '</div></div>'
        + '<div>' + dl([['EV vs HOLD', chosen ? show(usdS(chosen.increment_vs_hold_usd), 'not recorded on the chosen alternative') : nr('the chosen alternative is not on the record')],
                          ['max loss (HOLD → chosen)', (wcH === null ? nr('HOLD worst case not established') : usd(wcH)) + ' → ' + (wcC === null ? nr('chosen worst case not established') : usd(wcC))],
                          ['execution eligibility', esc(typeof d.execution_eligibility === 'object' ? JSON.stringify(d.execution_eligibility) : (d.execution_eligibility || 'not recorded'))],
                          ['decided', show(when(d.decided_at, rd), 'no decision time')], ['next review', show(when(d.next_review_at, rd), 'no next review recorded')],
                          ['search', AG.R && AG.R.searchChip ? AG.R.searchChip(d) : ''], ['live game', '<span class="mute">no live game feed: no identified, timestamped game-state source is connected in this build</span>']])
        + '<p class="lbl" style="margin:10px 0 4px">Rejected alternatives</p>' + (rejected.length ? '<ul style="margin:0;padding-left:16px;font-size:12.5px">' + rejected.join('') + '</ul>' : '<span class="mute">none recorded</span>')
        + '<div style="margin-top:8px">' + AG.evidence(d.evidence) + '</div></div></div>';
    });
    return {status: 'OK', html: html, key: keys};
  }
  function positionCards(ws, rd) {
    var ps = secData(ws, 'positions');
    if (ps.status !== 'OK') return {status: ps.status, html: ps.status === 'UNAVAILABLE' ? unavLine(ps.why) : emptyLine(ps.why)};
    var d = ps.data || {}, groups = d.groups || [], hand = Array.isArray(d.handoffs) ? d.handoffs : [];
    var cur = (secData(ws, 'reviews').data || {}).current || [];
    var exp = d.current_exposure || {};
    var html = groups.map(function (g) {
      var legs = g.legs || [], b = g.book || {};
      var prim = legs.filter(function (l) { return (l.leg_role || 'PRIMARY') !== 'HEDGE'; })[0] || {};
      var h = hand.filter(function (x) { return legs.some(function (l) { return l.intent_id === x.entry_intent_id; }); })[0];
      var rv = cur.filter(function (x) { return x.portfolio_group_id === g.group || legs.some(function (l) { return l.intent_id === x.intent_id; }); })[0];
      var hold = rv ? altsFor(ws, rv.xavier_decision_id).filter(function (a) { return a.action === 'HOLD'; })[0] : null;
      return '<div class="poscard" data-group="' + esc(g.group) + '"><h3 class="sr-only">Position group</h3>' + labelSlot('intent:' + (prim.intent_id || ''), [['group id', g.group], ['market slug', prim.us_market_slug], ['primary intent id', prim.intent_id]])
        + '<div class="pgrid">'
        + '<div><div class="lbl">Derek’s entry</div><div class="v">' + (h ? qty(h.confirmed_qty) + ' of ' + qty(h.ordered_qty) + ' confirmed' : nr('no handoff row for this group')) + '</div>' + (h ? '<span class="nrw">first fill ' + (when(h.first_fill_at, rd) || '?') + '</span>' : '') + '</div>'
        + '<div><div class="lbl">Primary / hedge</div><div class="v">' + qty(g.primary_qty) + ' / ' + qty(g.hedge_qty) + '</div></div>'
        + '<div><div class="lbl">Matched / unpaired</div><div class="v">' + qty(g.matched_qty) + ' / ' + qty(g.residual_unpaired_qty) + '</div></div>'
        + '<div><div class="lbl">Outstanding orders · reserved</div><div class="v">' + show(qty(exp.outstanding_orders), 'not in the exposure read') + ' · ' + show(usd(pick(exp, ['reserved_usd', 'reserved_collateral_usd'])), 'reserved capital not in the exposure read') + '</div></div>'
        + '<div><div class="lbl">Realised P&amp;L</div><div class="v">' + show(usdS(b.realised_usd), 'no realised figure on the book') + '</div></div>'
        + '<div><div class="lbl">Liquidation value now</div><div class="v">' + (isNum(b.unrealised_usd) ? usdS(b.unrealised_usd) : nr(b.unrealised_status || 'no funded mark source')) + '</div></div>'
        + '<div><div class="lbl">Settlement P&amp;L (HOLD)</div><div class="v">' + (hold ? show(usdS(hold.expected_net_usd), 'not recorded') + '<span class="nrw">floor ' + show(usdS(hold.worst_case_established_usd), 'not established') + '</span>' : nr('no HOLD valuation on the latest review')) + '</div></div>'
        + '<div><div class="lbl">Preferred action</div><div class="v">' + (rv ? esc(rv.chosen_action) : nr('no review recorded')) + '</div></div>'
        + '<div><div class="lbl">Next review</div><div class="v">' + (rv ? show(when(rv.next_review_at, rd), 'not recorded') : nr('no review recorded')) + '</div></div>'
        + '<div><div class="lbl">Remaining basis</div><div class="v">' + show(usd(b.remaining_basis_usd), 'not on the book') + '</div></div>'
        + '</div>' + AG.table(legs, [{label: 'leg', keys: ['leg_role']}, {label: 'intent', keys: ['intent_id']}, {label: 'market', keys: ['us_market_slug']}, {label: 'state', keys: ['state']}, {label: 'filled', keys: ['filled_qty']}, {label: 'held', keys: ['residual_qty']}], {rd: rd}) + '</div>';
    }).join('');
    return {status: groups.length ? 'OK' : 'EMPTY', html: html || emptyLine('no group returned')};
  }
  function ladder(ws, rd) {
    var s = secData(ws, 'ladder');
    if (s.status !== 'OK') return {status: s.status, html: s.status === 'UNAVAILABLE' ? unavLine(s.why) : emptyLine(s.why)};
    var out = (Array.isArray(s.data) ? s.data : []).map(function (d) {
      var rows = Array.isArray(d.ladder) ? d.ladder : [];
      return '<p class="lbl" style="margin:8px 0 6px">Review ' + esc(d.xavier_decision_id) + '</p><div class="tbl"><table><thead><tr><th>rung</th><th>status</th><th class="num">target price</th><th class="num">executable price</th><th class="num">combined cost</th><th class="num">depth</th><th class="num">coverage</th><th class="num">EV</th><th class="num">settlement floor</th><th class="num">overlap p</th></tr></thead><tbody>'
        + rows.map(function (r) {
          var ea = r.exposure_after || {};
          return '<tr><td>' + labelSlot('slug:' + (r.candidate_id || ''), [['candidate id', r.candidate_id], ['line', r.line], ['backs', r.backs]]) + '</td><td><span class="mon">MONITORED ALTERNATIVE</span></td>'
            + '<td class="num">' + show(price(pick(r, ['target_price', 'limit_price'])), 'not recorded on this rung') + '</td>'
            + '<td class="num">' + show(price(pick(r, ['executable_price', 'price', 'ask'])), 'not recorded on this rung') + '</td>'
            + '<td class="num">' + show(usd(r.capital_required_usd), 'not recorded') + '</td>'
            + '<td class="num">' + show(qty(r.qty_executable_at_limit), 'not recorded') + '</td>'
            + '<td class="num">' + show(qty(pick(ea, ['matched', 'matched_qty'])), isNum(r.unpaired_residual_qty) ? 'unpaired after: ' + r.unpaired_residual_qty : 'not recorded') + '</td>'
            + '<td class="num">' + show(usdS(r.expected_net_usd), 'not recorded') + '</td>'
            + '<td class="num">' + show(usdS(r.worst_case_established_usd), 'not established') + '</td>'
            + '<td class="num">' + (isNum(r.p_both_legs_win) ? r.p_both_legs_win.toFixed(3) : '<span class="mute">not established</span>') + '</td></tr>';
        }).join('') + '</tbody></table></div>';
    }).join('');
    return {status: 'OK', html: out || emptyLine('no ladder rows')};
  }
  function standing(j, rd) {
    if (!isObj(j)) return {status: 'UNAVAILABLE', html: unavLine('no response')};
    var mon = Array.isArray(j.monitored_alternatives) ? j.monitored_alternatives : [];
    var monHtml = '<p class="lbl" style="margin:12px 0 6px">Monitored alternatives (never venue orders)</p>' + (mon.length ? mon.map(function (m) { return '<div style="margin-bottom:8px"><span class="mon">' + esc(m.label) + '</span>' + labelSlot('slug:' + (m.candidate_id || ''), [['candidate id', m.candidate_id], ['decision id', m.xavier_decision_id], ['group id', m.portfolio_group_id]]) + (isNum(m.expected_net_usd) ? ' EV ' + usdS(m.expected_net_usd) : '') + (m.href ? ' <a class="ev" href="' + esc(AG.safeHref(m.href) || '') + '">decision</a>' : '') + '</div>'; }).join('') : '<span class="mute">none on the latest reviews</span>');
    var rule = '<p class="note">' + esc(j.rule || '') + '</p>';
    if (j.status === 'UNAVAILABLE') return {status: 'UNAVAILABLE', html: '<div class="plain" data-standing="UNAVAILABLE"><p class="big">UNAVAILABLE · ' + esc(j.why) + '</p><p>' + esc(j.detail || '') + '</p><p class="mute">No protective order is shown because none can be read here; this is not "no orders".</p></div>' + rule + monHtml};
    if (j.status === 'EMPTY') return {status: 'EMPTY', html: emptyLine(j.why) + rule + monHtml};
    var html = (j.groups || []).map(function (g) {
      var cap = g.capacity_consumed || {};
      return '<div class="poscard"><h3>Group ' + esc(g.group_id) + (g.invariant_holds ? '' : ' <span class="warnchip">MORE THAN ONE LIVE ORDER</span>') + '</h3>'
        + (g.pinned ? '<p class="note">Instrument pinned by the first hedge fill: <span class="mono">' + esc(g.pinned.candidate_id) + '</span> (' + esc(g.pinned.first_fill_id || '') + ')</p>' : '<p class="note">No hedge fill has pinned an instrument yet.</p>')
        + (g.plans || []).map(function (p) { return '<div style="margin-bottom:8px"><span class="sto">' + esc(p.label) + '</span>' + labelBlock(p.label_resolved, [['market slug', p.venue_slug], ['plan id', p.plan_id], ['hedge intent id', p.hedge_intent_id], ['venue order id', p.venue_order_id], ['candidate id', p.candidate_id]]) + ' ' + qty(p.quantity) + ' @ ' + (price(p.wire_limit_price) || '?') + ' · order ' + esc(p.order_state || 'not sent') + ' · lifecycle ' + esc(p.lifecycle_state || 'not recorded') + ' <a class="ev" href="' + esc(AG.safeHref(p.href) || '') + '">record</a></div>'; }).join('')
        + '<p class="note">Capacity consumed until reconciled: ' + qty(cap.reserved_qty) + ' contracts · ' + (usd(cap.reserved_collateral_usd) || '?') + '. ' + esc(cap.why || '') + '</p></div>';
    }).join('');
    return {status: 'OK', html: html + rule + monHtml};
  }
  function payoff(j) {
    if (!isObj(j)) return {status: 'UNAVAILABLE', html: unavLine('no response')};
    var rows = (j.outcomes || []).map(function (o) { return '<tr><td>' + esc(o.outcome) + '</td><td class="num">' + usd(o.payout_usd) + '</td><td class="num">' + usdS(o.profit_before_fees_usd) + '</td></tr>'; }).join('');
    var ex = (j.exceptional_settlement || []).map(function (e) { return '<li>' + esc(e.region) + ': ' + (Array.isArray(e.net_pnl_range_usd) ? usdS(e.net_pnl_range_usd[0]) + ' to ' + usdS(e.net_pnl_range_usd[1]) : 'not established') + ' — ' + esc(e.why || '') + '</li>'; }).join('');
    var c = j.cost || {}, f = j.confirmed_fills || {};
    return {status: 'OK', html: '<div class="cc-demo" style="padding:14px;border-radius:14px"><span class="demolab" data-demo="1">' + esc(j.label) + '</span>'
      + '<p style="margin:0 0 8px">' + esc(j.position.primary.what) + ' ' + qty(f.primary_qty) + ' @ ' + price(j.position.primary.price) + ' = ' + usd(c.primary_usd) + ' · ' + esc(j.position.hedge.what) + ' ' + qty(f.hedge_qty) + ' confirmed @ ' + price(j.position.hedge.price) + ' = ' + usd(c.hedge_usd) + ' · total ' + usd(c.total_before_fees_usd) + ' before fees</p>'
      + '<div class="tbl"><table class="payoff"><thead><tr><th>Outcome</th><th class="num">Payout</th><th class="num">Profit before fees</th></tr></thead><tbody>' + rows + '</tbody></table></div>'
      + '<div class="pl" style="margin-top:10px"><div><div class="lbl">Minimum ordinary-settlement profit</div><div class="v">' + usdS(j.minimum_ordinary_settlement_profit_before_fees_usd) + '</div><span class="nrw">before fees (' + esc(j.fees) + ')</span></div>'
      + '<div><div class="lbl">Both win</div><div class="v">' + usdS(j.both_win_profit_before_fees_usd) + '</div><span class="nrw">before fees</span></div>'
      + '<div><div class="lbl">Unpaired inventory</div><div class="v">' + qty(j.unpaired_qty) + '</div><span class="nrw">matched ' + qty(j.matched_qty) + '</span></div>'
      + '<div><div class="lbl">Protection</div><div class="v">' + (j.fully_protected ? 'FULL (confirmed fills)' : 'PARTIAL') + '</div><span class="nrw">' + esc(j.protection_note) + '</span></div></div>'
      + '<p class="note" style="margin-top:10px">A settlement payoff floor, not realised cash. Computed by <span class="mono">' + esc(j.computed_by) + '</span> on the server.</p>'
      + '<p class="lbl" style="margin:8px 0 4px">Exceptional settlement exposure (separate)</p><ul style="margin:0;padding-left:16px;font-size:12.5px">' + ex + '</ul><p class="note">' + esc(j.exceptional_note) + '</p></div>'};
  }

  // ── AUDREY ────────────────────────────────────────────────────────
  function pnlPanel(j, rd) {
    if (!isObj(j)) return {status: 'UNAVAILABLE', html: unavLine('no response')};
    if (j.status === 'UNAVAILABLE') return {status: 'UNAVAILABLE', html: unavLine(j.why)};
    var auth = '<p class="note">' + esc(j.authority || '') + '</p>';
    function book(b, demo) {
      var per = b.periods || {}, e = b.exposure || {};
      var un = isNum(b.unrealised_pnl_usd) ? usdS(b.unrealised_pnl_usd) : nr((typeof b.unrealised_pnl_usd === 'string' ? b.unrealised_pnl_usd + ': ' : '') + (typeof b.unrealised_basis === 'string' ? b.unrealised_basis : 'no funded mark source'));
      return '<div class="' + (demo ? 'cc-demo' : '') + '" style="padding:' + (demo ? '12px' : '0') + ';border-radius:12px;margin-bottom:12px">' + (demo ? '<span class="demolab">DEMONSTRATION BOOK · NEVER ADDED TO THE COMPANY P&amp;L</span>' : '')
        + '<p class="lbl" style="margin:0 0 6px">' + esc(b.account_id) + ' · ' + esc(b.venue) + ' · ' + esc(b.book_class) + '</p>'
        + '<div class="pl"><div><div class="lbl">Realised P&amp;L</div><div class="v">' + show(usdS(b.realised_pnl_usd), 'not reported') + '</div>' + (b.realised_is_provisional ? '<span class="prov-tag">PROVISIONAL</span>' : '') + '</div>'
        + '<div><div class="lbl">Unrealised P&amp;L</div><div class="v">' + un + '</div></div>'
        + '<div><div class="lbl">Fees</div><div class="v">' + show(usd(b.fees_usd), 'not reported') + '</div>' + (b.fills_with_provisional_fees ? '<span class="nrw">' + b.fills_with_provisional_fees + ' fill(s) with provisional fees</span>' : '') + '</div>'
        + '<div><div class="lbl">Max drawdown</div><div class="v">' + show(usd(b.max_drawdown_usd), 'not reported') + '</div></div>'
        + '<div><div class="lbl">Exposure</div><div class="v">' + show(usd(e.total_exposure_usd), 'not in the exposure read') + '</div></div>'
        + '<div><div class="lbl">Reserved capital</div><div class="v">' + show(usd(pick(e, ['reserved_usd', 'reserved_collateral_usd'])), 'not in the exposure read') + '</div></div>'
        + '<div><div class="lbl">Outstanding orders</div><div class="v">' + show(qty(e.outstanding_orders), 'not reported') + '</div></div>'
        + '<div><div class="lbl">Closed positions</div><div class="v">' + show(qty(b.closed_positions), 'not reported') + '</div></div></div>'
        + '<div class="tbl" style="margin-top:8px"><table><thead><tr><th>period</th><th class="num">realised</th><th class="num">booked results</th></tr></thead><tbody>'
        + ['daily', 'weekly', 'monthly', 'cumulative'].map(function (k) { var p = per[k] || {}; return '<tr><td>' + k + '</td><td class="num">' + show(usdS(p.realised_usd), 'not reported') + '</td><td class="num">' + show(qty(p.booked_results), '') + '</td></tr>'; }).join('')
        + '</tbody></table></div></div>';
    }
    var funded = (j.funded_books || []).map(function (b) { return book(b, false); }).join('');
    var demo = (j.demonstration_books || []).map(function (b) { return book(b, true); }).join('');
    var recon = Array.isArray(j.reconciliation) ? (j.reconciliation.length ? AG.table(j.reconciliation, [{label: 'account', keys: ['account_id']}, {label: 'book', keys: ['book_class']}, {label: 'recorded', keys: ['recorded_at']}, {label: 'authoritative', keys: ['authoritative']}, {label: 'eligible', keys: ['would_be_eligible']}, {label: 'blocking', keys: ['blocking_count']}, {label: 'report', keys: ['report_id']}], {rd: rd}) : '<p class="mute">' + esc(j.reconciliation_why) + '</p>') : '<p class="unav">' + esc(j.reconciliation_why || 'reconciliation reports unreadable') + '</p>';
    var disc = isNum(j.unresolved_discrepancy_count) ? '<p class="note">Unresolved discrepancies in the funded book: <b>' + j.unresolved_discrepancy_count + '</b>' + (j.unresolved_discrepancy_count ? ' (each is something an operator must act on)' : '') + '</p>' : '';
    var body = auth + (funded || emptyLine(j.why)) + disc + '<p class="lbl" style="margin:12px 0 6px">Reconciliation status</p>' + recon + (demo ? '<p class="lbl" style="margin:14px 0 6px">Demonstration books (separate)</p>' + demo : '');
    return {status: j.status === 'OK' ? 'OK' : 'EMPTY', html: body};
  }
  function auditPanel(ws, rd) {
    var f = secData(ws, 'findings'), o = secData(ws, 'outcomes'), c = secData(ws, 'cohort_quality');
    if (f.status !== 'OK') return {status: f.status, html: f.status === 'UNAVAILABLE' ? unavLine(f.why) : emptyLine(f.why)};
    var d = f.data || {}, od = o.data || {}, cd = c.data || {};
    var cats = od.evidence_categories || {};
    var names = {ACTUAL: 'ACTUAL EXECUTION', OBSERVED: 'SUPPORTED HYPOTHETICAL EXECUTION', OBSERVED_ALTERNATIVE: 'SUPPORTED HYPOTHETICAL EXECUTION', MODELLED: 'MODELLED ESTIMATE', MODELLED_ESTIMATE: 'MODELLED ESTIMATE', UNRESOLVED: 'UNKNOWN EXECUTION'};
    var catHtml = Object.keys(cats).map(function (k) { var v = cats[k] || {}; var cls = /ACTUAL/.test(k) ? 'ACTUAL' : /OBSERVED/.test(k) ? 'OBSERVED' : /MODELLED/.test(k) ? 'MODELLED' : /UNRESOLVED|UNKNOWN/.test(k) ? 'UNRESOLVED' : 'OTHER'; return '<div class="cat cat-' + cls + '"><h5>' + esc(names[k] || k) + '</h5><div class="n">' + (isNum(v.count) ? v.count : AG.unk()) + '</div><p>' + (isNum(v.usd) ? usdS(v.usd) : 'no dollar figure') + (v.what ? ' — ' + esc(v.what) : '') + '</p></div>'; }).join('');
    return {status: 'OK', html: '<p class="note"><span class="prov-tag">PROVISIONAL UNTIL SETTLEMENT</span> Live audits are provisional until the positions they cover settle. Report <span class="mono">' + esc(d.report_id) + '</span> (' + esc(d.version || '') + ').</p>'
      + '<div class="split"><div><h4><span class="agent-dot a-DEREK"></span>DEREK · entry quality, calibration, execution, missed opportunities, policy/model</h4>' + AG.fmt('derek_quality', d.derek_quality, rd) + (cd.derek_calibration !== undefined ? '<p class="lbl" style="margin:10px 0 4px">Calibration</p>' + AG.fmt('derek_calibration', cd.derek_calibration, rd) : '') + '</div>'
      + '<div><h4><span class="agent-dot a-XAVIER"></span>XAVIER · protection timing, coverage, exposure reduction, exits, residual risk, incidents</h4>' + AG.fmt('xavier_quality', d.xavier_quality, rd) + (cd.xavier_benchmarks !== undefined ? '<p class="lbl" style="margin:10px 0 4px">Benchmarks</p>' + AG.fmt('xavier_benchmarks', cd.xavier_benchmarks, rd) : '') + '</div></div>'
      + (catHtml ? '<p class="lbl" style="margin:14px 0 6px">Execution evidence, kept apart</p><div class="cats">' + catHtml + '</div>' : '')
      + '<div style="margin-top:8px">' + AG.evidence(f.evidence) + '</div>'};
  }
  function directiveTree(ws, rd) {
    var ds = secData(ws, 'directives'), ts = secData(ws, 'tasks'), cs = secData(ws, 'candidates'), es = secData(ws, 'evaluations'), rs = secData(ws, 'releases');
    if (ds.status === 'UNAVAILABLE') return {status: 'UNAVAILABLE', html: unavLine(ds.why)};
    var dirs = Array.isArray(ds.data) ? ds.data : [];
    var tasks = Array.isArray(ts.data) ? ts.data : [], cands = Array.isArray(cs.data) ? cs.data : [];
    var trials = isObj(es.data) && Array.isArray(es.data.trials) ? es.data.trials : [];
    var rels = isObj(rs.data) && Array.isArray(rs.data.releases) ? rs.data.releases : [];
    var open = tasks.filter(function (t) { return ['OPEN', 'IN_PROGRESS', 'WAITING', 'CANDIDATE_READY', 'EVALUATING', 'APPROVAL_READY'].indexOf(t.status) >= 0; });
    var html = '<p class="note">Open improvement tasks: <b>' + open.length + '</b> of ' + tasks.length + (ts.status !== 'OK' ? ' (' + esc(ts.why || ts.status) + ')' : '') + '.</p>';
    if (!dirs.length) return {status: 'EMPTY', html: html + emptyLine(ds.why || 'no management directive recorded')};
    html += '<ul class="dtree">' + dirs.map(function (d) {
      var did = d.directive_id;
      var tk = tasks.filter(function (t) { return t.directive_id === did; });
      return '<li data-directive="' + esc(did) + '"><b>' + esc(did) + '</b> · ' + esc(d.state || d.status || '') + ' — ' + esc(d.objective || d.text || '') + ' <span class="mute">' + (when(d.created_at, rd) || '') + '</span>'
        + (tk.length ? '<ol>' + tk.map(function (t) {
          var cc = cands.filter(function (c) { return c.task_id === t.task_id; });
          return '<li>task <span class="mono">' + esc(t.task_id) + '</span> ' + AG.evidence(t.evidence) + ' · ' + esc(t.assignee || '') + ' · <b>' + esc(t.status) + '</b> — ' + esc(t.title || '')
            + (cc.length ? '<ol>' + cc.map(function (c) {
              var tr = trials.filter(function (x) { return x.candidate_id === c.candidate_id; });
              var rl = rels.filter(function (x) { return x.candidate_id === c.candidate_id; });
              return '<li>candidate ' + esc(c.candidate_id) + ' · <b>' + esc(c.state || '') + '</b>' + (c.approved_by ? ' · approved by ' + esc(c.approved_by) : ' · not approved') + ' · evaluation: ' + (tr.length ? tr.map(function (x) { return esc(x.outcome || x.state || x.trial_id); }).join(', ') : 'no trial yet') + ' · release: ' + (rl.length ? rl.map(function (x) { return esc(x.state); }).join(', ') : 'none') + '</li>';
            }).join('') + '</ol>' : '<ol><li class="mute">no candidate yet</li></ol>') + '</li>';
        }).join('') + '</ol>' : '<ol><li class="mute">no task created from this directive yet</li></ol>') + '</li>';
    }).join('') + '</ul>';
    return {status: 'OK', html: html};
  }

  return {MODES: MODES, STALE_MIN_S: STALE_MIN_S, nr: nr, prob: prob, pp: pp, pct: pct, usd: usd, usdS: usdS, qty: qty, price: price,
    labelBlock: labelBlock, labelSlot: labelSlot, techDetails: techDetails,
    modeOf: modeOf, staleAfter: staleAfter, pill: pill, emptyLine: emptyLine, unavLine: unavLine, gateLine: gateLine,
    derekRow: derekRow, derekList: derekList, secData: secData, recommendation: recommendation, positionCards: positionCards,
    ladder: ladder, standing: standing, payoff: payoff, pnlPanel: pnlPanel, auditPanel: auditPanel, directiveTree: directiveTree};
})(AG);
"""

# ════════════════════════════════════════════════════════════════════
# DATA MODULE: DOM BOOT (reads, polling, panels, the character's mode)
# ════════════════════════════════════════════════════════════════════
CC_BOOT_JS = r"""
(function (AG, CC) {
  'use strict';
  var kind = document.body.getAttribute('data-kind');
  var E = AG.ENDPOINTS, F = fetch.bind(window), esc = AG.esc;
  var st = {ws: null, index: null, base: null, chat: null, chatTimer: null, recKeys: null, hist: {view: 'decisions', q: '', page: 1}, orders: {state: 'all', page: 1}, hedge: 2000};
  function $(id) { return document.getElementById(id); }
  function setPanel(id, status, html) {
    var p = $(id); if (!p) return;
    p.setAttribute('data-status', status);
    var pl = p.querySelector('[data-pill]'); if (pl) pl.outerHTML = CC.pill(status);
    var b = p.querySelector('[data-body]'); if (b) b.innerHTML = html;
    if (b && CC.labelize) CC.labelize(b).then(function () { if ((id === 'p-audit' || id === 'p-history') && CC.relabelText) CC.relabelText(b); });
  }
  function failPanel(id, o, url) { setPanel(id, o.kind === 'LOCKED' ? 'LOCKED' : o.kind === 'NOT_DEPLOYED' ? 'NOT DEPLOYED' : 'UNAVAILABLE', AG.gate(o, url)); }
  // THE FUNDED SYSTEM'S FIGURES go to its own collapsed section
  // (#cc-funded-kpis); the brief's key figures (#cc-kpis) are the paper
  // session's, written by the paper boot from the operations read.
  function kpis(list) {
    var el = $('cc-funded-kpis'); if (!el) return;
    el.innerHTML = list.map(function (k) {
      var h = AG.safeHref(k.href);
      return '<div class="cc-kpi"><div class="lbl">' + esc(k.label) + '</div>' + (h ? '<a class="v" href="' + esc(h) + '">' + k.value + '</a>' : '<div class="v">' + k.value + '</div>') + (k.sub ? '<div class="s">' + k.sub + '</div>' : '') + '</div>';
    }).join('');
  }
  // ── the character's mode, from agent_status ─────────────────────
  function applyMode(m) {
    document.body.setAttribute('data-cc-mode', m.mode);
    var a = $('cc-anim'); if (a) a.innerHTML = 'Animation: <b>' + esc(m.mode) + '</b>' + (m.why ? ' — ' + esc(m.why) : m.activity ? ' — ' + esc(m.activity) : '');
    var real = $('cc-stage') && $('cc-stage').classList.contains('cc-real-model');
    var c = $('cc-canvas'); if (c) c.setAttribute('aria-label', (real ? 'Animated 3D character of ' : 'Placeholder character, final model pending: animated 3D stand-in for ') + document.getElementById('cc-name').textContent + ', an AI agent, shown ' + m.mode + (m.why ? ' (' + m.why + ')' : '') + '.');
    try { window.dispatchEvent(new CustomEvent('cc:mode', {detail: {mode: m.mode, recorded: m.recorded, why: m.why}})); } catch (_) {}
  }
  // THE POSE FOLLOWS THE PAPER RUNTIME (CC.setPaperMode, from the paper
  // operations read's own heartbeat stamp). The funded agent_status record is
  // reported in the funded section only; it never drives the character.
  function effective() {
    var m = st.paper || {mode: 'unavailable', why: 'the paper runtime status has not been read yet'};
    if (st.chat && m.mode !== 'unavailable') return {mode: st.chat, recorded: m.recorded, why: null, activity: st.chat === 'speaking' ? 'answering in the management chat' : 'reading the records for a chat answer'};
    return m;
  }
  function fundedState(html) { var el = $('cc-funded-state'); if (el) el.innerHTML = 'Funded agent status (agent_status, the funded lane\'s own record; it does not drive the character): ' + html; }
  CC.setPaperMode = function (m) {
    st.paper = m;
    ['derek', 'xavier', 'audrey'].forEach(function (k) { var d = document.querySelector('[data-sd="' + k + '"]'); if (d) { d.setAttribute('data-mode', m.mode); d.title = 'paper runtime · ' + m.mode + (m.why ? ' · ' + m.why : ''); } });
    applyMode(effective());
  };
  async function readIndex() {
    var o = await AG.load(E.index, F);
    if (o.kind !== 'OK') {
      st.base = {mode: 'unavailable', recorded: null, why: 'status read failed: ' + (o.why || o.kind)};
      fundedState('<span class="sb sb-UNRECOGNISED"><i></i>' + (o.kind === 'LOCKED' ? 'SIGN-IN REQUIRED' : 'UNAVAILABLE') + '</span> ' + esc(o.why || o.kind));
      return;
    }
    st.index = o.json;
    var rd = AG.toEpoch(o.json.read_at);
    (o.json.agents || []).forEach(function (a) {
      var k = String(a.agent_id || '').toLowerCase();
      if (k === kind) {
        var m = CC.modeOf(a, rd);
        st.base = m;
        var hb = AG.toEpoch(a.last_heartbeat_at);
        fundedState(AG.stateBadge(a.state) + ' · ' + esc(m.mode) + (m.why ? ' — ' + esc(m.why) : '') + ' · ' + (hb === null ? 'no heartbeat recorded' : 'heartbeat ' + AG.ts(hb, rd)) + ' · <a href="' + E.index + '">agent_status</a>');
      }
    });
    if (!st.base) { st.base = {mode: 'unavailable', why: 'the index returned no row for this agent'}; fundedState(esc(st.base.why)); }
  }
  // ── DEREK ─────────────────────────────────────────────────────────
  function qs(o) { return Object.keys(o).map(function (k) { return encodeURIComponent(k) + '=' + encodeURIComponent(o[k]); }).join('&'); }
  async function derekOrders(params) { return AG.load(E.derek_orders + '?' + qs(params), F); }
  function pager(j, which) {
    if (!j || !j.pages || j.pages < 2) return '<div class="cc-pager">' + (j && j.total ? j.total + ' record(s)' : '') + '</div>';
    return '<div class="cc-pager">page ' + j.page + ' of ' + j.pages + ' · ' + j.total + ' record(s) <button type="button" data-page="' + which + '" data-to="' + (j.page - 1) + '"' + (j.page <= 1 ? ' disabled' : '') + '>Previous</button><button type="button" data-page="' + which + '" data-to="' + (j.page + 1) + '"' + (j.page >= j.pages ? ' disabled' : '') + '>Next</button></div>';
  }
  async function derekPanels(ws) {
    var rd = AG.toEpoch(ws.read_at);
    var cov = CC.secData(ws, 'coverage');
    var covSpec = (AG.SPECS.derek.sections || []).filter(function (x) { return x.key === 'coverage'; })[0] || {};
    var covN = AG.normSection(cov, 'coverage', E.derek), covCtx = {rd: rd, json: ws, sections: ws.sections, url: E.derek, kind: 'derek'};
    var covHtml; try { covHtml = covSpec.render ? covSpec.render(covN, covCtx) : AG.genericBody(covN, rd); } catch (e) { covHtml = AG.genericBody(covN, rd); }
    setPanel('p-coverage', cov.status, (cov.status === 'OK' ? covHtml : cov.status === 'UNAVAILABLE' ? CC.unavLine(cov.why) : CC.emptyLine(cov.why)) + '<div style="margin-top:8px">' + AG.evidence(cov.evidence) + '</div><p class="note">Unsupported categories are coverage gaps, kept visible by reason.</p>');
    var ver = (CC.secData(ws, 'versions').data || {}).policy || {};
    setPanel('p-policy', 'OK', '<p>Active policy on this service: <span class="mono">' + esc(ver.key || '?') + ' @ ' + esc(ver.version || 'version not reported') + '</span> · combination <span class="mono">' + esc(ver.combination_policy || 'not reported') + '</span>. Each decision row shows the policy and combined estimate <b>its own record</b> carries; the page never recomputes them.</p>'
      + '<p class="note">V2 rule, where a record carries it: blended = (internal + Pinnacle) / 2; gross edge = blended − executable purchase price; enter at ≥ 5 pp and positive net EV after costs, subject to approved sizing, capital and model qualification.</p>'
      + '<div class="cc-demo" style="padding:12px;border-radius:12px"><span class="demolab">WORKED EXAMPLE · ILLUSTRATION OF THE ARITHMETIC · NOT A RECORD</span>'
      + '<p style="margin:0">$1,000 on Yankees at $0.50 = <b>2,000 contracts</b>. Internal <span class="qty-prob">0.600</span>, Pinnacle <span class="qty-prob">0.580</span>, blended <span class="qty-prob">0.590</span> (probabilities); gross edge <span class="qty-pp">+9.00 pp</span> (probability points); expected profit before fees <b>$180.00</b>; expected return <span class="qty-ret">18.0%</span> (of the $1,000 cost).</p></div>');
    var hs = CC.secData(ws, 'handoffs');
    setPanel('p-handoffs', hs.status, hs.status === 'OK' ? AG.table(hs.data, [{label: 'entry intent', keys: ['entry_intent_id']}, {label: 'Derek decision', keys: ['derek_decision_id']}, {label: 'group', keys: ['portfolio_group_id']}, {label: 'owner', keys: ['owner_agent']}, {label: 'ordered', keys: ['ordered_qty']}, {label: 'confirmed', keys: ['confirmed_qty']}, {label: 'outstanding', keys: ['outstanding_qty']}, {label: 'first fill', keys: ['first_fill_at']}, {label: 'handed over', keys: ['handoff_at']}], {rd: rd}) + '<div style="margin-top:8px">' + AG.evidence(hs.evidence) + '</div><p class="note">Ownership passes to Xavier from the FIRST confirmed entry fill.</p>' : hs.status === 'UNAVAILABLE' ? CC.unavLine(hs.why) : CC.emptyLine(hs.why));
  }
  async function derekLive() {
    var op = await derekOrders({view: 'opportunities', page_size: 25});
    if (op.kind !== 'OK') failPanel('p-opps', op, E.derek_orders); else setPanel('p-opps', op.json.status, CC.derekList(op.json, op.json.read_at) + pager(op.json, 'opps'));
    await derekOrdersPanel(); await derekHistory();
    var dcount = op.kind === 'OK' ? op.json.counts || {} : {};
    var ob = dcount.orders_by_state || {}, dv = dcount.decisions_by_verdict || {};
    var sumv = function (o) { return Object.keys(o).reduce(function (a, k) { return a + o[k]; }, 0); };
    kpis([
      {label: 'Qualifying now', value: op.kind === 'OK' ? String(op.json.total) : 'UNAVAILABLE', sub: 'ENTER decisions, last 30 min', href: E.derek_orders + '?view=opportunities'},
      {label: 'Decisions recorded', value: op.kind === 'OK' ? String(sumv(dv)) : 'UNAVAILABLE', sub: (dv.ENTER || 0) + ' ENTER · ' + (dv.REFUSE || 0) + ' REFUSE', href: E.derek_orders + '?view=decisions'},
      {label: 'Entry orders', value: op.kind === 'OK' ? String(sumv(ob)) : 'UNAVAILABLE', sub: Object.keys(ob).map(function (k) { return ob[k] + ' ' + k.toLowerCase(); }).join(' · ') || 'none recorded', href: E.derek_orders + '?view=orders'},
      {label: 'Workspace', value: st.ws ? 'read' : 'not read', sub: st.ws ? AG.readLine(st.ws) : '', href: E.derek}]);
  }
  async function derekOrdersPanel() {
    var o = await derekOrders({view: 'orders', state: st.orders.state, page: st.orders.page, page_size: 20});
    var chips = '<div class="cc-chips" role="group" aria-label="Filter orders by state">' + ['all', 'pending', 'partial', 'filled', 'cancelled', 'rejected', 'unresolved'].map(function (s) {
      var n = o.kind === 'OK' && o.json.counts && o.json.counts.orders_by_state ? (s === 'all' ? Object.keys(o.json.counts.orders_by_state).reduce(function (a, k) { return a + o.json.counts.orders_by_state[k]; }, 0) : (o.json.state_groups[s] || []).reduce(function (a, k) { return a + (o.json.counts.orders_by_state[k] || 0); }, 0)) : '?';
      return '<button type="button" data-ostate="' + s + '" aria-pressed="' + (st.orders.state === s) + '">' + s + ' · ' + n + '</button>';
    }).join('') + '</div>';
    if (o.kind !== 'OK') return failPanel('p-orders', o, E.derek_orders);
    setPanel('p-orders', o.json.status, chips + CC.derekList(o.json, o.json.read_at) + pager(o.json, 'orders'));
  }
  async function derekHistory() {
    var h = st.hist;
    var o = await derekOrders({view: h.view, q: h.q, page: h.page, page_size: 20});
    var tools = '<form class="cc-tools" id="hist-form" role="search"><label class="sr-only" for="hist-q">Search decisions and orders</label><input type="search" id="hist-q" placeholder="Search id, market, fixture, side, verdict, refusal, state…" value="' + esc(h.q) + '"><label class="sr-only" for="hist-view">View</label><select id="hist-view"><option value="decisions"' + (h.view === 'decisions' ? ' selected' : '') + '>decisions</option><option value="orders"' + (h.view === 'orders' ? ' selected' : '') + '>orders</option></select><button type="submit" class="primary">Search</button></form>';
    if (o.kind !== 'OK') { setPanel('p-history', 'UNAVAILABLE', tools + AG.gate(o, E.derek_orders)); return; }
    setPanel('p-history', o.json.status, tools + CC.derekList(o.json, o.json.read_at) + pager(o.json, 'hist'));
  }
  // ── XAVIER ────────────────────────────────────────────────────────
  async function xavierPanels(ws) {
    var rd = AG.toEpoch(ws.read_at);
    var r = CC.recommendation(ws, st.recKeys, rd); st.recKeys = r.key || st.recKeys;
    setPanel('p-rec', r.status, r.html);
    var p = CC.positionCards(ws, rd); setPanel('p-positions', p.status, p.html);
    var l = CC.ladder(ws, rd); setPanel('p-ladder', l.status, l.html);
    var rv = CC.secData(ws, 'reviews'), sc = CC.secData(ws, 'servicing_cadence'), ex = CC.secData(ws, 'execution'), rc = CC.secData(ws, 'recovery');
    setPanel('p-reviews', 'OK', '<div class="split"><div><p class="lbl">Current and next review</p>' + (rv.status === 'OK' ? '<p>Next review: ' + (AG.toEpoch((rv.data || {}).next_review_at) !== null ? AG.ts(rv.data.next_review_at, rd) : CC.nr('no next review recorded')) + '</p>' : CC.emptyLine(rv.why)) + '<p class="lbl">Servicing cadence</p>' + (sc.status === 'OK' ? AG.kv(sc.data, rd) : CC.emptyLine(sc.why)) + '</div>'
      + '<div><p class="lbl">Execution</p>' + (ex.status === 'OK' ? '<p>' + ((ex.data || {}).decisions || []).length + ' decision(s) with execution state · <a href="#s-execution">full record</a></p>' : CC.emptyLine(ex.why)) + '<p class="lbl">Recovery</p>' + (rc.status === 'OK' ? '<p>' + (((rc.data || {}).unresolved_dispatch_claims) || []).length + ' unanswered dispatch claim(s) · <a href="#s-recovery">full record</a></p>' : rc.status === 'UNAVAILABLE' ? CC.unavLine(rc.why) : CC.emptyLine(rc.why)) + '</div></div>');
    var ps = CC.secData(ws, 'positions');
    var groups = ps.status === 'OK' ? ((ps.data || {}).groups || []).length : 0;
    var cur = rv.status === 'OK' ? (rv.data.current || []) : [];
    kpis([
      {label: 'Position groups owned', value: ps.status === 'OK' ? String(groups) : ps.status, sub: ps.status === 'OK' ? '' : esc(ps.why || ''), href: E.xavier},
      {label: 'Preferred action', value: cur.length ? esc(cur[0].chosen_action || '?') : (rv.status === 'OK' ? 'none' : rv.status), sub: cur.length ? 'decision ' + esc(cur[0].xavier_decision_id) : esc(rv.why || ''), href: cur.length ? E.xavier + '/decisions/' + encodeURIComponent(cur[0].xavier_decision_id) : E.xavier},
      {label: 'Submission', value: ((ex.data || {}).submission_switches || {}).FUNDED_SUBMISSION_ENABLED === true ? 'ENABLED' : 'DISABLED', sub: 'as the serving code has it', href: E.xavier},
      {label: 'Workspace', value: 'read', sub: AG.readLine(ws), href: E.xavier}]);
  }
  async function xavierLive() {
    var s = await AG.load(E.xavier_standing, F);
    if (s.kind !== 'OK') failPanel('p-standing', s, E.xavier_standing); else { var r = CC.standing(s.json, s.json.read_at); setPanel('p-standing', r.status, r.html); }
    await xavierPayoff();
  }
  async function xavierPayoff() {
    var j = await AG.load(E.xavier_payoff + '?hedge_filled=' + st.hedge, F);
    var seg = '<div class="cc-tools"><span class="lbl">Confirmed hedge fills</span><div class="segbtn" role="group" aria-label="Confirmed hedge fills">' + [2000, 1000, 0].map(function (n) { return '<button type="button" data-hedge="' + n + '" aria-pressed="' + (st.hedge === n) + '">' + n.toLocaleString('en-US') + '</button>'; }).join('') + '</div><span class="note" style="margin:0">recomputed on the server for the whole position</span></div>';
    var prod = '<p class="note">Production positions: see Managed positions above. The table below is a DEMONSTRATION and is never mixed with them.</p>';
    if (j.kind !== 'OK') return failPanel('p-payoff', j, E.xavier_payoff);
    var r = CC.payoff(j.json); setPanel('p-payoff', 'OK', prod + seg + r.html);
  }
  // ── AUDREY ────────────────────────────────────────────────────────
  async function audreyPanels(ws) {
    var rd = AG.toEpoch(ws.read_at);
    var a = CC.auditPanel(ws, rd); setPanel('p-audit', a.status, a.html);
    var d = CC.directiveTree(ws, rd); setPanel('p-directives', d.status, d.html);
  }
  async function audreyLive() {
    var j = await AG.load(E.audrey_performance, F);
    if (j.kind !== 'OK') { failPanel('p-pnl', j, E.audrey_performance); return; }
    var r = CC.pnlPanel(j.json, j.json.read_at); setPanel('p-pnl', r.status, r.html);
    var fb = (j.json.funded_books || []);
    var real = fb.reduce(function (s, b) { return typeof b.realised_pnl_usd === 'number' ? s + b.realised_pnl_usd : s; }, 0);
    var ws = st.ws || {}, ts = CC.secData(ws, 'tasks'), rp = CC.secData(ws, 'daily_reports');
    kpis([
      {label: 'Company realised P&L', value: fb.length ? CC.usdS(real) : 'no funded book', sub: fb.length ? fb.length + ' funded book(s); demonstration books excluded' : esc(j.json.why || ''), href: E.audrey_performance},
      {label: 'Unresolved discrepancies', value: String(j.json.unresolved_discrepancy_count === undefined ? '?' : j.json.unresolved_discrepancy_count), sub: 'in the funded book', href: E.audrey_performance},
      {label: 'Latest audit report', value: rp.status === 'OK' && rp.data && rp.data[0] ? esc(rp.data[0].report_id || rp.data[0].report_date || 'report') : rp.status, sub: rp.status === 'OK' ? '' : esc(rp.why || ''), href: E.audrey},
      {label: 'Improvement tasks', value: ts.status === 'OK' ? String(ts.data.length) : ts.status, sub: ts.status === 'OK' ? 'filed by Audrey' : esc(ts.why || ''), href: E.audrey}]);
  }
  // ── labels: one shared server resolver for every page ────────────
  var labelCache = {};
  async function labelize(root) {
    root = root || document;
    var slots = [].slice.call(root.querySelectorAll('[data-label-item]:not([data-label-done])'));
    var want = slots.map(function (e) { return e.getAttribute('data-label-item'); }).filter(function (i, k, a) { return i && !/:$/.test(i) && a.indexOf(i) === k && !(i in labelCache); });
    for (var i = 0; i < want.length; i += 50) {
      var o = await AG.load(E.labels + '?' + want.slice(i, i + 50).map(function (x) { return 'item=' + encodeURIComponent(x); }).join('&'), F);
      if (o.kind === 'OK') Object.keys(o.json.labels || {}).forEach(function (k) { labelCache[k] = o.json.labels[k]; });
      want.slice(i, i + 50).forEach(function (k) { if (!(k in labelCache)) labelCache[k] = null; });
    }
    slots.forEach(function (e) {
      var tech = []; try { tech = JSON.parse(e.getAttribute('data-tech') || '[]'); } catch (_) {}
      e.innerHTML = CC.labelBlock(labelCache[e.getAttribute('data-label-item')], tech);
      e.setAttribute('data-label-done', '1');
    });
  }
  var SLUG_RX = /\b[a-z]{2,8}-[a-z0-9]+(?:-[a-z0-9]+)*-\d{4}-\d{2}-\d{2}(?:-[a-z0-9]+)*\b/g;
  async function relabelText(root) {
    if (!root) return;
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {acceptNode: function (n) {
      var p = n.parentNode; if (!p || p.closest('details.tech, details.raw, code, pre, abbr.mlabel, textarea, input')) return NodeFilter.FILTER_REJECT;
      SLUG_RX.lastIndex = 0; return SLUG_RX.test(n.nodeValue) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT; }});
    var nodes = [], n; while ((n = walker.nextNode())) nodes.push(n);
    if (!nodes.length) return;
    var slugs = {}; nodes.forEach(function (t) { (t.nodeValue.match(SLUG_RX) || []).forEach(function (x) { slugs['slug:' + x + '|'] = 1; }); });
    var want = Object.keys(slugs).filter(function (k) { return !(k in labelCache); });
    if (want.length) {
      var o = await AG.load(E.labels + '?' + want.slice(0, 60).map(function (x) { return 'item=' + encodeURIComponent(x); }).join('&'), F);
      if (o.kind === 'OK') Object.keys(o.json.labels || {}).forEach(function (k) { labelCache[k] = o.json.labels[k]; });
    }
    nodes.forEach(function (t) {
      var html = esc(t.nodeValue).replace(SLUG_RX, function (x) {
        var l = labelCache['slug:' + x + '|'];
        return l && l.primary ? '<abbr class="mlabel" title="' + esc('market slug: ' + x + (l.warnings && l.warnings.length ? ' · ' + l.warnings[0] : '')) + '" data-slug="' + esc(x) + '">' + esc(l.primary) + '</abbr>' : x;
      });
      var span = document.createElement('span'); span.innerHTML = html; t.parentNode.replaceChild(span, t);
    });
  }
  CC.labelize = labelize; CC.relabelText = relabelText;
  document.addEventListener('click', function (ev) {
    var b = ev.target.closest ? ev.target.closest('button[data-copy]') : null; if (!b) return;
    var v = b.getAttribute('data-copy');
    try { navigator.clipboard.writeText(v).then(function () { b.textContent = 'Copied'; setTimeout(function () { b.textContent = 'Copy'; }, 1200); }); } catch (_) {}
  });
  // ── hooks called by the workspace boot ───────────────────────────
  CC.onWorkspace = function (k, json) {
    st.ws = json;
    setTimeout(function () { if (CC.relabelText) CC.relabelText(document.getElementById('app')); }, 0);
    try {
      if (kind === 'derek') derekPanels(json);
      else if (kind === 'xavier') xavierPanels(json);
      else if (kind === 'audrey') audreyPanels(json);
    } catch (e) { console.error(e); }
  };
  CC.onWorkspaceFail = function (o) {
    var ids = {derek: ['p-coverage', 'p-handoffs'], xavier: ['p-rec', 'p-positions', 'p-ladder', 'p-reviews'], audrey: ['p-audit', 'p-directives']}[kind] || [];
    ids.forEach(function (id) { failPanel(id, o, E[kind]); });
  };
  CC.chatPending = function () { st.chat = 'reviewing'; clearTimeout(st.chatTimer); applyMode(effective()); };
  CC.chatReply = function (textLen) {
    if (CC.relabelText) CC.relabelText(document.getElementById('chat-log'));
    st.chat = 'speaking'; applyMode(effective());
    clearTimeout(st.chatTimer);
    st.chatTimer = setTimeout(function () { st.chat = null; applyMode(effective()); }, Math.min(12000, 1500 + 35 * (textLen || 0)));
  };
  CC.chatDone = function () { st.chat = null; clearTimeout(st.chatTimer); applyMode(effective()); };
  // ── interactions ──────────────────────────────────────────────────
  document.addEventListener('click', function (ev) {
    var t = ev.target.closest ? ev.target.closest('button') : null; if (!t) return;
    if (t.hasAttribute('data-ostate')) { st.orders = {state: t.getAttribute('data-ostate'), page: 1}; derekOrdersPanel(); }
    else if (t.hasAttribute('data-page')) { var w = t.getAttribute('data-page'), to = +t.getAttribute('data-to'); if (w === 'orders') { st.orders.page = to; derekOrdersPanel(); } else if (w === 'hist') { st.hist.page = to; derekHistory(); } }
    else if (t.hasAttribute('data-hedge')) { st.hedge = +t.getAttribute('data-hedge'); xavierPayoff(); }
    else if (t.hasAttribute('data-sugg')) { var i = $('chat-in'); if (i) { i.value = t.getAttribute('data-sugg'); i.focus(); } }
  });
  document.addEventListener('submit', function (ev) {
    if (ev.target && ev.target.id === 'hist-form') { ev.preventDefault(); st.hist = {view: $('hist-view').value, q: $('hist-q').value.trim(), page: 1}; derekHistory(); }
  });
  var pause = $('cc-pause');
  if (pause) pause.addEventListener('click', function () {
    var on = pause.getAttribute('aria-pressed') !== 'true';
    pause.setAttribute('aria-pressed', String(on)); pause.textContent = on ? 'Resume animation' : 'Pause animation';
    try { window.dispatchEvent(new CustomEvent('cc:pause', {detail: {paused: on}})); } catch (_) {}
  });
  function live() { if (kind === 'derek') derekLive(); else if (kind === 'xavier') xavierLive(); else if (kind === 'audrey') audreyLive(); }
  readIndex(); live();
  setInterval(function () { if (!document.hidden) readIndex(); }, 20000);
  setInterval(function () { if (!document.hidden) live(); }, 45000);
  window.addEventListener('cc:ready', function () { applyMode(effective()); });
})(AG, CC);
"""


#: FRAMED MODE. The management shell at /derek, /xavier and /audrey frames
#: these pages from the same origin and draws the top navigation itself, so a
#: framed page hides its own and its agent links leave the frame (`_top`) for
#: the shell's paths. Unframed, the links stay on the API page paths. Runs
#: before anything else and needs neither the data nor the character module.
FRAMED_JS = r"""
(function () {
  var framed = true;
  try { framed = window.top !== window.self; } catch (e) { framed = true; }
  if (!framed) return;
  document.documentElement.classList.add('cc-framed');
  function retarget() {
    [].forEach.call(document.querySelectorAll('a[data-framed-href]'), function (a) {
      a.setAttribute('href', a.getAttribute('data-framed-href'));
      a.setAttribute('target', '_top');
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', retarget); else retarget();
})();
"""

# ════════════════════════════════════════════════════════════════════
# THE PAPER SESSION (live market data, SIMULATED execution)
#
# The routes are served by the paper-session change (api/command_paper.py,
# bettor_paper_ledger.py, bettor_paper_readmodel.py). THE SERVER IS
# AUTHORITATIVE: this client reads its shapes exactly as they are written
# there and changes nothing about them. Until a route exists in a build it
# answers 404 and the section says UNAVAILABLE by name. No figure is ever
# drawn that the server did not send: not the starting bankroll, not a
# balance, not an intermediate value between two committed entries; a null
# figure (a missing mark) stays NOT STATED with the server's own basis.
# ════════════════════════════════════════════════════════════════════
PAPER_BASE = "/api/command/paper"
#: bettor_paper_ledger.balances(): the seven headline figures, by its keys.
PAPER_ACCOUNT_FIELDS = (
    ("cash_usd", "Cash"), ("reserved_usd", "Reserved cash"),
    ("available_usd", "Available cash"),
    ("open_position_value_usd", "Open-position value"),
    ("total_equity_usd", "Total equity"),
    ("realized_pnl_usd", "Realized P&L"), ("unrealized_pnl_usd", "Unrealized P&L"))
#: Every other balances() key the page reads. The server leaves the three
#: marked figures null while any open position has no mark, and sends the
#: marked-only figures beside them.
PAPER_BALANCE_KEYS = (
    "ok", "refusal", "last_sequence", "last_updated_at", "starting_cash_usd",
    "open_position_value_marked_only_usd", "equity_excluding_unmarked_usd",
    "unrealized_pnl_marked_only_usd", "marks_complete", "unmarked_positions",
    "stale_marks", "equity_basis", "mark_method", "mark_stale_after_s",
    "ledger_entries", "ledger_consistent", "reserved_is", "fees_paid_usd",
    "real_money_submission", "open_positions")
#: bettor_paper_ledger.entry_view(): what one ledger row reads.
PAPER_ENTRY_KEYS = (
    "sequence", "kind", "idempotency_key", "cash_delta_usd",
    "reserved_delta_usd", "cash_after_usd", "reserved_after_usd",
    "available_after_usd", "order_id", "fill_id", "group_id", "position_key",
    "settlement_key", "corrects_seq", "event_source", "simulator_version",
    "detail", "committed_at")
#: bettor_paper_ledger.KINDS
LEDGER_KINDS = ("INITIAL_FUNDING", "ORDER_SUBMITTED", "FILL",
                "RESERVATION_RELEASED", "SALE", "SETTLEMENT", "CORRECTION")
#: api.command_paper.session_brief(): the banner's one read.
PAPER_SESSION_BRIEF_KEYS = ("active", "reason", "session_id", "started_at",
                            "starting_cash_usd", "last_heartbeat_at",
                            "real_money_submission")
#: api.command_paper.stream_events(): the named events and what each carries.
PAPER_STREAM_EVENTS = {
    "snapshot": ("sequence", "balances", "latest_entries", "last_updated_at"),
    "ledger": ("sequence", "entry", "running_balances", "committed_at",
               "last_updated_at"),       # + "balances" on a batch's last entry
    "heartbeat": ("sequence", "at"),
    "unavailable": ("why",),
}
#: The per-agent routes: one GET each, every section {status, why, data}.
PAPER_SECTIONS = {
    "derek": (("opportunities", "Paper opportunities (Derek's decisions)"),
              ("refusal_summary_24h", "Decisions by reason, last 24 h"),
              ("orders", "Paper entry orders"),
              ("fills", "Simulated entry fills"),
              ("handoffs", "Paper handoffs to Xavier")),
    "xavier": (("positions", "Open paper positions"),
               ("standing_orders", "Standing and management paper orders"),
               ("recommendations", "Xavier's latest paper review per group")),
    "audrey": (("daily_reports", "Paper daily reports"),
               ("audit_entries", "Paper audit findings")),
}
PAPER_SESSION_SECTIONS = ("session", "health", "enablement")
PAPER_CONTRACT = {
    "source": ("api/command_paper.py; bettor_paper_ledger.py (balances, "
               "entry_view, order_view); bettor_paper_readmodel.py. The server "
               "is authoritative; this page only reads."),
    "section": {"status": "OK|EMPTY|UNAVAILABLE", "why": "the named reason, "
                "or null when OK", "data": "the section's records"},
    "GET /api/command/paper/account?entries=": {
        "top": ("data_label", "labels", "as_of", "account_id", "account",
                "ledger", "session", "last_updated_at", "drawdown"),
        "account": "section; data = balances(): " + ", ".join(
            [k for k, _ in PAPER_ACCOUNT_FIELDS] + list(PAPER_BALANCE_KEYS)),
        "ledger": "section; data = [entry_view], newest first",
        "session": "session_brief: " + ", ".join(PAPER_SESSION_BRIEF_KEYS),
        "drawdown": ("section; data = snapshots, skipped_incomplete_marks, "
                     "peak_equity_usd, current_equity_usd, "
                     "current_drawdown_usd, max_drawdown_usd, "
                     "max_drawdown_pct, basis"),
        "schema absent": ("account and ledger UNAVAILABLE "
                          "MIGRATION_171_IS_NOT_APPLIED; no session and no "
                          "drawdown")},
    "GET /api/command/paper/stream": {
        "events": PAPER_STREAM_EVENTS,
        "id": ("the ledger sequence on snapshot and ledger frames; none on "
               "heartbeat or unavailable"),
        "resume": ("the browser's Last-Event-ID header, or ?last=<sequence> "
                   "on a manual reconnect (the query wins over the header, "
                   "so the page drops replayed sequences it already holds)")},
    "GET /api/command/paper/session": PAPER_SESSION_SECTIONS,
    "GET /api/command/paper/derek": tuple(k for k, _ in PAPER_SECTIONS["derek"]),
    "GET /api/command/paper/xavier": tuple(k for k, _ in PAPER_SECTIONS["xavier"]),
    "GET /api/command/paper/audrey": tuple(k for k, _ in PAPER_SECTIONS["audrey"]),
    "schema absent (per-agent and session routes)": (
        "{data_label, labels, <derek|xavier|audrey|session>: UNAVAILABLE "
        "MIGRATION_171_IS_NOT_APPLIED}"),
}
PAPER_UNAVAILABLE = "paper account routes not in this build"
PAPER_SECTION_UNAVAILABLE = "paper session routes not in this build"

#: VOICE (design only, not built in this release): each agent's future voice
#: is a profile handed to a TTS or recorded-audio pipeline, which drives the
#: mouth through the existing 'cc:speech' hook (amplitude or visemes). Voices
#: must be original or properly licensed adult voices, non-explicit.
VOICE_PROFILES = {
    "derek": {"character": "bright, analytical, a little nerdy", "pace": "quick"},
    "xavier": {"character": "deep and composed", "pace": "measured"},
    "audrey": {"character": "warm and glamorous", "pace": "unhurried"},
}

PAPER_CSS = r"""
.cc-paper-banner{margin:0 0 14px;padding:10px 14px;border-radius:12px;font:700 12.5px/1.4 var(--mono);letter-spacing:.08em;border:1px dashed var(--line-2);color:var(--ink-2);background:var(--panel)}
.cc-paper-banner[data-state=ACTIVE]{border:1px solid #58a6ff;color:#cfe3ff;background:linear-gradient(90deg,rgba(88,166,255,.16),rgba(88,166,255,.04))}
.cc-paper-banner[data-state=OFF]{border-color:var(--warn);color:var(--warn)}
.cc-paper-banner[data-state=UNAVAILABLE]{border-color:var(--bad);color:var(--bad)}
.cc-raw{margin-top:16px}.cc-raw>summary{cursor:pointer;font:600 12.5px/1.4 var(--mono);color:var(--ink-2);padding:10px 2px;overflow-wrap:anywhere}.cc-raw[open]>summary{margin-bottom:10px}
.cc-p.paper{border-style:dashed;border-color:#3a5a86;background:repeating-linear-gradient(135deg,rgba(88,166,255,.035) 0 12px,transparent 12px 24px),var(--panel)}
.sim{display:inline-block;font:700 9.5px/1 var(--mono);letter-spacing:.1em;color:#9fc6ff;border:1px solid #3a6aa8;border-radius:4px;padding:3px 5px;margin-left:6px;vertical-align:middle}
.pfig{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:1px;background:var(--line);border:1px solid var(--line);border-radius:12px;overflow:hidden}
.pfig>div{background:var(--panel);padding:10px 12px;min-width:0}.pfig .v{font:600 18px/1.25 var(--display);margin-top:4px;overflow-wrap:anywhere}
.pfig .v .ns{color:var(--warn);font:700 13px/1.3 var(--mono);letter-spacing:.06em}
.pfig .v .sub{display:block;font:11px/1.35 var(--mono);color:var(--ink-2);margin-top:4px}
.pfig .v.flash{animation:pflash 1.2s ease-out 1}
@keyframes pflash{0%{background:rgba(88,166,255,.35)}100%{background:transparent}}
.pconn{font:650 11px/1 var(--mono);letter-spacing:.08em;padding:4px 7px;border-radius:6px;border:1px solid currentColor}
.pconn[data-conn=LIVE]{color:var(--ok)}.pconn[data-conn=RECONNECTING]{color:var(--warn)}.pconn[data-conn=DISCONNECTED],.pconn[data-conn=UNAVAILABLE]{color:var(--bad)}
.pmarks{font:12px/1.4 var(--mono);color:var(--ink-2);margin:8px 0}.pmarks b.OK{color:var(--ok)}.pmarks b.STALE,.pmarks b.UNAVAILABLE{color:var(--warn)}
td .ns{color:var(--warn);font:700 11px/1.3 var(--mono)}
.pdl{margin:0 0 8px;display:grid;grid-template-columns:auto 1fr;gap:3px 12px;font:12.5px/1.4 var(--mono)}.pdl dt{color:var(--ink-2)}
tr.pnew td{animation:pflash 1.2s ease-out 1}
@media (max-width:1000px){.pfig{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (prefers-reduced-motion:reduce){.pfig .v.flash,tr.pnew td{animation:none}}
"""


def _paper_panel(pid: str, route: str, agent: str, key: str, title: str) -> str:
    return ('<section class="cc-p paper half" id="p-paper-%s" data-status="READING" '
            'data-paper-route="%s" data-paper-agent="%s" data-paper-key="%s" '
            'aria-labelledby="p-paper-%s-h"><header><h2 id="p-paper-%s-h">%s '
            '<span class="sim">SIMULATED</span></h2><span class="pill st-MISSING" '
            'data-pill>READING</span><span class="src">GET %s &#183; %s</span></header>'
            '<div data-body><p class="boot">Reading&#8230;</p></div></section>'
            % (pid, route, agent, key, pid, pid, _html.escape(title), route, key))


def paper_html(kind: str) -> str:
    """The shared paper account and session panels, and this agent's
    sections of its one paper route."""
    acct = ('<section class="cc-p paper" id="p-paper-account" data-status="READING" '
            'aria-labelledby="p-paper-account-h"><header><h2 id="p-paper-account-h">'
            'Paper account <span class="sim">LIVE MARKET DATA · SIMULATED EXECUTION'
            '</span></h2><span class="pill st-MISSING" data-pill>READING</span>'
            '<span class="pconn" id="paper-conn" data-conn="UNAVAILABLE">NOT CONNECTED'
            '</span><span class="src">GET %s/account &#183; stream %s/stream &#183; '
            'simulated figures, never summed with the funded book</span></header>'
            '<div data-body><p class="boot">Reading&#8230;</p></div></section>'
            % (PAPER_BASE, PAPER_BASE))
    sess = _paper_panel("session", PAPER_BASE + "/session", "session", "session",
                        "Paper session and health")
    route = "%s/%s" % (PAPER_BASE, kind)
    secs = "".join(_paper_panel(key, route, kind, key, t)
                   for key, t in PAPER_SECTIONS[kind])
    return ('<h2 class="cc-h2" id="paper">Paper account, ledger and session '
            'records <span class="sim">'
            'SIMULATED</span></h2><div class="cc-panels" id="cc-paper" '
            'data-cc-paper>%s%s</div>'
            # THE RAW PER-AGENT SECTIONS (up to 100 rows each) sit under one
            # expandable element: the operational panels above already lead
            # with them, and on a phone they would otherwise run for metres.
            '<details class="cc-raw" id="paper-raw"><summary>Raw paper records '
            '&#183; GET %s (every section as the route returns it)</summary>'
            '<div class="cc-panels">%s</div></details>' % (acct, sess, route, secs))


PAPER_CORE_JS = r"""
(function (AG, CC) {
  'use strict';
  var esc = AG.esc, isObj = AG.isObj;
  // bettor_paper_ledger.balances(): the seven figures, by the server's own keys
  var FIELDS = [['cash_usd', 'Cash'], ['reserved_usd', 'Reserved cash'], ['available_usd', 'Available cash'],
    ['open_position_value_usd', 'Open-position value'], ['total_equity_usd', 'Total equity'],
    ['realized_pnl_usd', 'Realized P&L'], ['unrealized_pnl_usd', 'Unrealized P&L']];
  // what a stream 'ledger' frame's running_balances carries (from the entry's own after-balances)
  var RUNNING = ['cash_usd', 'reserved_usd', 'available_usd'];
  // null while any open position has no mark; the server sends the marked-only figure beside it
  var MARKED_ONLY = {open_position_value_usd: 'open_position_value_marked_only_usd', total_equity_usd: 'equity_excluding_unmarked_usd', unrealized_pnl_usd: 'unrealized_pnl_marked_only_usd'};
  var INTENT = {LONG: 'ORDER_INTENT_BUY_LONG', SHORT: 'ORDER_INTENT_BUY_SHORT'};
  var HINT = {INITIAL_FUNDING: 'funding', ORDER_SUBMITTED: 'reserve', FILL: 'purchase', RESERVATION_RELEASED: 'release',
    SALE: 'sale proceeds', SETTLEMENT: 'payout', CORRECTION: 'settlement correction'};
  var KEEP = 50;
  var UNAV = 'paper account routes not in this build', SECT_UNAV = 'paper session routes not in this build';
  var SIM = '<span class="sim">SIMULATED</span>';
  var FLOORS = '<p class="note"><b>Payoff floors are outcome-dependent, never realized P&amp;L.</b> Realized P&amp;L is only in the paper account.</p>';
  function num(v) { return typeof v === 'number' && isFinite(v); }
  function sec(s) { return isObj(s) && (s.status === 'OK' || s.status === 'EMPTY' || s.status === 'UNAVAILABLE') ? s : null; }
  function money(k, v) { return /pnl/.test(k) ? CC.usdS(v) : CC.usd(v); }
  function iso(v) { var e = AG.toEpoch(v); return e === null ? null : new Date(e * 1000).toISOString().slice(11, 19) + 'Z'; }
  function nyTime(v) {
    var e = AG.toEpoch(v); if (e === null) return null;
    try { return new Intl.DateTimeFormat('en-US', {timeZone: 'America/New_York', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false}).format(new Date(e * 1000)) + ' ET'; }
    catch (_) { return new Date(e * 1000).toISOString(); }
  }
  function sectUnav() { return '<div class="plain"><p class="big">UNAVAILABLE · ' + SECT_UNAV + '</p><p class="mute">No simulated figure is shown because none can be read here.</p></div>'; }

  // THE BANNER: session_brief from GET /account. The bankroll only as sent, and
  // only while the session is active; the session route's reason is the fallback.
  function fallback(so) {
    if (!so || so.kind !== 'OK' || !isObj(so.json)) return null;
    var s = sec(so.json.session); return s && s.status !== 'OK' ? s.why : null;
  }
  function banner(o, so) {
    if (!o || o.kind === 'NOT_DEPLOYED') return {state: 'UNAVAILABLE', text: 'PAPER SESSION NOT RUNNING — ' + UNAV};
    if (o.kind === 'LOCKED') return {state: 'UNAVAILABLE', text: 'SIGN-IN REQUIRED — the paper session cannot be read without a COMMAND session'};
    if (o.kind !== 'OK') return {state: 'UNAVAILABLE', text: 'PAPER SESSION NOT RUNNING — the paper account read failed (' + (o.why || o.kind) + ')'};
    var j = isObj(o.json) ? o.json : {}, s = isObj(j.session) ? j.session : null, a = sec(j.account);
    if (!s) return {state: a && a.status === 'UNAVAILABLE' ? 'UNAVAILABLE' : 'OFF', text: 'PAPER SESSION NOT RUNNING — ' + ((a && a.why) || fallback(so) || 'the server sent no session')};
    if (s.active !== true) return {state: 'OFF', text: 'PAPER SESSION NOT RUNNING — ' + (s.reason || fallback(so) || 'the server reports no active session and gave no reason')};
    var bank = num(s.starting_cash_usd) ? ' · ' + CC.usd(s.starting_cash_usd).replace(/\.00$/, '') + ' STARTING BANKROLL' : ' · STARTING BANKROLL NOT SENT';
    return {state: 'ACTIVE', text: 'LIVE MARKET DATA · SIMULATED EXECUTION' + bank};
  }

  // THE ACCOUNT: balances() exactly as sent. `j` is GET /account's payload, or
  // view(state) while streaming; `j.running` is a stream frame's after-entry
  // cash figures, used only when newer than the full recomputation.
  function account(j, prev) {
    if (!isObj(j)) return {status: 'UNAVAILABLE', html: CC.unavLine('no response'), changed: []};
    var s = sec(j.account);
    if (!s) return {status: 'UNAVAILABLE', html: CC.unavLine("the response carried no 'account' section"), changed: []};
    var a = s.data;
    if (s.status !== 'OK' || !isObj(a) || a.ok !== true) {
      var st = s.status === 'OK' ? 'UNAVAILABLE' : s.status;
      var why = s.why || (isObj(a) && a.refusal) || 'the server named no reason';
      return {status: st, html: '<div class="plain"><p class="big">' + esc(st) + ' · ' + esc(why) + '</p><p class="mute">No paper figure is shown: none was sent. This is not a zero balance.</p></div>', changed: []};
    }
    var run = isObj(j.running) && num(j.running.sequence) && !(num(a.last_sequence) && j.running.sequence <= a.last_sequence) ? j.running : null;
    var p = isObj(prev) ? prev : {}, vals = {}, changed = [];
    var figs = FIELDS.map(function (f) {
      var k = f[0], v = run && RUNNING.indexOf(k) >= 0 ? run[k] : a[k], was = p[k];
      vals[k] = v;
      var ch = num(v) && num(was) && v !== was; if (ch) changed.push(k);
      var shown;
      if (num(v)) shown = money(k, v);
      else if (v === null && MARKED_ONLY[k]) shown = '<span class="ns">NOT STATED</span><span class="sub">' + esc(a.equity_basis || 'the server stated no basis') + '</span>'
        + (num(a[MARKED_ONLY[k]]) ? '<span class="sub">marked-only ' + money(k, a[MARKED_ONLY[k]]) + ', excluding unmarked positions</span>' : '');
      else shown = CC.nr('not sent by the server');
      return '<div data-fig="' + k + '"><div class="lbl">' + esc(f[1]) + SIM + '</div><div class="v' + (ch ? ' flash' : '') + '">' + shown + '</div></div>';
    }).join('');
    var um = Array.isArray(a.unmarked_positions) ? a.unmarked_positions : [], sm = Array.isArray(a.stale_marks) ? a.stale_marks : [];
    var mm = String(a.mark_method || '').split(':')[0], marks;
    if (a.marks_complete === true) marks = sm.length ? '<b class="STALE">STALE</b> — ' + sm.length + ' position mark(s) older than ' + (num(a.mark_stale_after_s) ? a.mark_stale_after_s + ' s' : 'the stale limit') + ', shown and flagged' : '<b class="OK">COMPLETE</b>';
    else if (a.marks_complete === false) marks = '<b class="UNAVAILABLE">INCOMPLETE</b> — ' + um.length + ' open position(s) with no available mark: total equity, open-position value and unrealized P&amp;L are not stated' + (sm.length ? ' · ' + sm.length + ' stale mark(s)' : '');
    else marks = '<b class="UNAVAILABLE">MARK STATUS NOT REPORTED</b> — open-position value cannot be read as current';
    marks = '<p class="pmarks">Marks: ' + marks + (mm ? ' · <span title="' + esc(a.mark_method) + '">' + esc(mm) + '</span>' : '') + '</p>';
    var lc = a.ledger_consistent === false ? '<p class="note"><b class="neg">LEDGER INCONSISTENT</b>: the server reports that the last running balance disagrees with the sum of the ledger entries.</p>' : '';
    var rec = '';
    if (!run && num(a.total_equity_usd) && num(a.cash_usd) && num(a.open_position_value_usd)) {
      var ok = Math.abs(a.cash_usd + a.open_position_value_usd - a.total_equity_usd) <= 0.01;
      rec = '<p class="note">' + (ok ? 'Reconciles: cash + open-position value = total equity (reserved cash is part of cash).' : '<b class="neg">DOES NOT RECONCILE</b>: cash + open-position value ≠ total equity as sent. Shown as sent; nothing recomputed.') + '</p>';
    }
    var part = run ? '<p class="note">Cash, reserved and available are current to ledger entry #' + esc(run.sequence) + '; open-position value, equity and P&amp;L are from the server\'s recomputation at entry #' + esc(a.last_sequence) + '.</p>' : '';
    var t = j.last_updated_at !== undefined && j.last_updated_at !== null ? j.last_updated_at : a.last_updated_at;
    var ss = isObj(j.session) ? j.session : null;
    var upd = '<p class="note">Last committed change ' + (AG.toEpoch(t) !== null ? AG.ts(t) + ' (' + esc(nyTime(t)) + ')' : CC.nr('the server sent no update time'))
      + (num(a.last_sequence) ? ' · ledger entry #' + a.last_sequence : '')
      + (num(a.ledger_entries) ? ' · ' + a.ledger_entries + ' entries' + (a.ledger_consistent === true ? ', running balance agrees with the ledger sum' : '') : '')
      + (num(a.fees_paid_usd) ? ' · fees paid ' + CC.usd(a.fees_paid_usd) : '')
      + (ss && ss.last_heartbeat_at ? ' · session heartbeat ' + AG.ts(ss.last_heartbeat_at) : '')
      + '. ' + (a.reserved_is ? 'Reserved is ' + esc(a.reserved_is) + '. ' : '') + 'LIVE MARKET DATA · SIMULATED EXECUTION · fictional USD, never summed with the funded book'
      + (a.real_money_submission ? ' · real-money submission ' + esc(a.real_money_submission) : '') + '.</p>';
    return {status: 'OK', html: '<div class="pfig">' + figs + '</div>' + marks + lc + rec + part + upd, changed: changed, values: vals};
  }
  function drawdown(s) {
    if (s === null || s === undefined) return '<p class="pmarks">Drawdown ' + SIM + ': not sent by the server.</p>';
    s = sec(s); if (!s) return '<p class="pmarks">Drawdown: the section is not recognised.</p>';
    if (s.status !== 'OK' || !isObj(s.data)) return '<p class="pmarks">Drawdown ' + SIM + ': <b class="' + (s.status === 'EMPTY' ? 'STALE' : 'UNAVAILABLE') + '">' + esc(s.status) + '</b> — ' + esc(s.why || 'no reason given') + '</p>';
    var d = s.data;
    function u(v) { return num(v) ? CC.usd(v) : 'not stated'; }
    return '<p class="pmarks" title="' + esc(d.basis || '') + '">Drawdown ' + SIM + ': current ' + u(d.current_drawdown_usd) + ' · max ' + u(d.max_drawdown_usd) + (num(d.max_drawdown_pct) ? ' (' + d.max_drawdown_pct.toFixed(2) + '%)' : '')
      + ' · peak equity ' + u(d.peak_equity_usd) + ' · ' + (num(d.snapshots) ? d.snapshots : '?') + ' equity snapshot(s)' + (num(d.skipped_incomplete_marks) && d.skipped_incomplete_marks ? ', ' + d.skipped_incomplete_marks + ' skipped for incomplete marks' : '') + '</p>';
  }

  // THE LEDGER: entry_view rows, newest first, in New York time
  function posParts(pk) {                       // paperpos:<account>:<group>:<slug>:<side>
    if (typeof pk !== 'string' || pk.indexOf('paperpos:') !== 0) return null;
    var p = pk.split(':'); if (p.length < 5) return null;
    var side = p[p.length - 1];
    return INTENT[side] ? {slug: p[p.length - 2], side: side} : null;
  }
  function ledgerRow(e, fresh) {
    e = isObj(e) ? e : {};
    var pp = posParts(e.position_key), d = isObj(e.detail) ? e.detail : {};
    var tech = [['ledger sequence', e.sequence], ['order id', e.order_id], ['fill id', e.fill_id], ['group id', e.group_id], ['position key', e.position_key],
      ['settlement key', e.settlement_key], ['corrects entry', e.corrects_seq], ['event source', e.event_source], ['simulator version', e.simulator_version], ['idempotency key', e.idempotency_key]];
    var ins = pp ? CC.labelSlot('slug:' + pp.slug + '|' + INTENT[pp.side], [['market slug', pp.slug], ['holding side', pp.side]].concat(tech))
                 : '<span class="mute">' + (e.group_id ? 'group ' + esc(e.group_id) : '—') + '</span>' + CC.techDetails(tech);
    var why = d.role || d.reason || d.outcome || '';
    function m(v, signed) { return num(v) ? (signed ? CC.usdS(v) : CC.usd(v)) : CC.nr('not sent'); }
    return '<tr' + (fresh ? ' class="pnew"' : '') + ' data-seq="' + esc(e.sequence) + '"><td>' + (nyTime(e.committed_at) ? esc(nyTime(e.committed_at)) : CC.nr('no commit time')) + '</td><td class="num mono">#' + esc(e.sequence) + '</td>'
      + '<td class="mono">' + esc(e.kind || '?') + (HINT[e.kind] ? ' <span class="mute">' + HINT[e.kind] + '</span>' : '') + (why ? '<br><span class="mute">' + esc(why) + '</span>' : '') + '</td><td>' + ins + '</td>'
      + '<td class="num">' + m(e.cash_delta_usd, true) + '</td><td class="num">' + m(e.reserved_delta_usd, true) + '</td><td class="num">' + m(e.cash_after_usd) + SIM + '</td><td class="num">' + m(e.available_after_usd) + '</td></tr>';
  }
  function ledger(list, fresh, why) {
    var rows = (Array.isArray(list) ? list : []).filter(function (e) { return isObj(e) && num(e.sequence); })
      .sort(function (x, y) { return y.sequence - x.sequence; }).slice(0, KEEP);
    fresh = isObj(fresh) ? fresh : {};
    return '<p class="lbl" style="margin:12px 0 6px">Ledger, newest first (America/New_York) ' + SIM + '</p><div class="tbl"><table><thead><tr><th>committed</th><th class="num">#</th><th>kind</th><th>instrument</th><th class="num">cash Δ</th><th class="num">reserved Δ</th><th class="num">cash after</th><th class="num">available after</th></tr></thead><tbody id="paper-ledger">'
      + (rows.length ? rows.map(function (e) { return ledgerRow(e, !!fresh[e.sequence]); }).join('') : '<tr><td colspan="8" class="mute">' + esc(why || 'No ledger entry has been sent.') + '</td></tr>') + '</tbody></table></div>';
  }

  // THE CLIENT STATE: GET /account plus the stream's frames. Figures only move
  // forward in ledger sequence; a replayed sequence is merged, never re-applied.
  function newState() { return {bal: null, acctSec: null, run: null, seq: null, entries: {}, fresh: {}, ledgerSec: null, last: null, session: null, drawdown: null, beat: null, streamWhy: null}; }
  function merge(st, list, fresh) {
    (Array.isArray(list) ? list : []).forEach(function (e) {
      if (!isObj(e) || !num(e.sequence)) return;
      if (fresh && !(e.sequence in st.entries)) st.fresh[e.sequence] = 1;
      st.entries[e.sequence] = e;
    });
    Object.keys(st.entries).map(Number).sort(function (x, y) { return y - x; }).slice(KEEP).forEach(function (k) { delete st.entries[k]; delete st.fresh[k]; });
  }
  function bump(st, s) { if (num(s)) st.seq = st.seq === null ? s : Math.max(st.seq, s); }
  function takeBal(st, b) {
    if (!isObj(b) || b.ok !== true) return false;
    if (st.bal && num(st.bal.last_sequence) && num(b.last_sequence) && b.last_sequence < st.bal.last_sequence) return false;
    st.bal = b; bump(st, b.last_sequence); return true;
  }
  function fromAccount(st, j) {
    j = isObj(j) ? j : {};
    st.fresh = {};
    st.session = isObj(j.session) ? j.session : null;
    st.drawdown = j.drawdown === undefined ? null : j.drawdown;
    var s = sec(j.account); st.acctSec = s;
    if (s && s.status === 'OK' && isObj(s.data) && s.data.ok === true) takeBal(st, s.data); else { st.bal = null; st.run = null; }
    var l = sec(j.ledger); st.ledgerSec = l;
    if (l && l.status === 'OK') merge(st, l.data, false); else st.entries = {};
    if (j.last_updated_at !== undefined && j.last_updated_at !== null && (st.last === null || AG.toEpoch(j.last_updated_at) >= AG.toEpoch(st.last))) st.last = j.last_updated_at;
  }
  function onEvent(st, name, d) {
    d = isObj(d) ? d : {};
    st.fresh = {};
    if (name === 'snapshot') {
      if (isObj(d.balances) && d.balances.ok !== true) { st.bal = null; st.run = null; st.acctSec = {status: 'EMPTY', why: d.balances.refusal || 'the stream snapshot carried no balances', data: d.balances}; }
      else if (takeBal(st, d.balances)) st.acctSec = {status: 'OK', why: null, data: st.bal};
      merge(st, d.latest_entries, false); bump(st, d.sequence);
      if (d.last_updated_at !== undefined && d.last_updated_at !== null) st.last = d.last_updated_at;
      return 'figures';
    }
    if (name === 'ledger') {
      merge(st, [d.entry], true);
      var newer = num(d.sequence) && (st.seq === null || d.sequence > st.seq), rb = isObj(d.running_balances) ? d.running_balances : {};
      if (newer) { st.run = {sequence: d.sequence, cash_usd: rb.cash_usd, reserved_usd: rb.reserved_usd, available_usd: rb.available_usd}; bump(st, d.sequence); st.last = d.last_updated_at || d.committed_at || st.last; }
      if (isObj(d.balances) && takeBal(st, d.balances)) st.acctSec = {status: 'OK', why: null, data: st.bal};
      return newer ? 'figures' : 'replay';
    }
    if (name === 'heartbeat') { st.beat = num(d.at) ? d.at : null; return 'heartbeat'; }
    if (name === 'unavailable') { st.streamWhy = d.why || 'the stream named no reason'; return 'unavailable'; }
    return 'ignored';
  }
  function view(st) {
    return {account: st.bal ? {status: 'OK', why: null, data: st.bal} : (st.acctSec || {status: 'UNAVAILABLE', why: 'no account figures have been read yet', data: null}),
            running: st.run, last_updated_at: st.last, session: st.session, drawdown: st.drawdown};
  }
  function entries(st) { return Object.keys(st.entries).map(function (k) { return st.entries[k]; }); }

  // THE PER-AGENT ROUTES: one GET each, every section {status, why, data}
  function c(label, keys, extra) { var o = {label: label, keys: keys}; if (extra) Object.keys(extra).forEach(function (k) { o[k] = extra[k]; }); return o; }
  function inst(r) {
    var it = r.intent || INTENT[r.holding_side];
    var tech = [['market slug', r.us_market_slug], ['order intent', it], ['fixture', r.fixture], ['group id', r.group_id], ['order id', r.order_id], ['fill id', r.fill_id], ['decision id', r.decision_id], ['position key', r.position_key]];
    return r.us_market_slug ? CC.labelSlot('slug:' + r.us_market_slug + '|' + (it || ''), tech) : '<span class="mute">no market slug in this record</span>' + CC.techDetails(tech);
  }
  function markCell(r) {
    var m = isObj(r.mark) ? r.mark : null; if (!m) return CC.nr('no mark sent');
    return (num(m.price) ? CC.price(m.price) : '<span class="ns">NO MARK</span>') + ' <span class="mute">' + esc(m.status || '') + (m.stale ? ' · STALE' : '') + (num(m.age_s) ? ' · ' + Math.round(m.age_s) + ' s old' : '') + (m.why ? ' · ' + esc(m.why) : '') + '</span>';
  }
  function orNot(k, what) { return function (r) { return num(r[k]) ? money(k, r[k]) : r[k] === null ? '<span class="ns">' + what + '</span>' : CC.nr('not in this record'); }; }
  var I = {label: 'instrument', render: inst};
  var ORDERS = [c('created', ['created_at']), I, c('role', ['role']), c('direction', ['direction']), c('state', ['state']), c('qty', ['qty'], {num: true}), c('filled', ['filled_qty'], {num: true}),
    c('remaining', ['remaining_qty'], {num: true}), c('limit', ['limit_price'], {num: true}), c('reserved left', ['reserved_remaining_usd'], {num: true, u: 'usd0'}), c('expires', ['expires_at'])];
  var COLS = {
    opportunities: [c('decided', ['decided_at']), I, c('verdict', ['verdict']), c('refusal', ['refusal']), c('p internal', ['p_internal'], {num: true}), c('p Pinnacle', ['p_pinnacle'], {num: true}),
      c('p blended', ['p_blended'], {num: true}), c('proposed qty', ['proposed_qty'], {num: true}), c('limit', ['limit_price'], {num: true}), c('policy', ['policy_version'])],
    refusal_summary_24h: [c('verdict', ['verdict']), c('reason', ['reason']), c('decisions', ['n'], {num: true})],
    orders: ORDERS,
    fills: [c('filled', ['filled_at']), I, c('direction', ['direction']), c('qty', ['qty'], {num: true}), c('price', ['price'], {num: true}), c('gross', ['gross_usd'], {num: true, u: 'usd0'}),
      c('fee', ['fee_usd'], {num: true, u: 'usd0'}), c('basis', ['basis'])],
    handoffs: [c('handed off', ['created_at']), c('group', ['group_id']), c('owner', ['owner']), c('confirmed qty', ['confirmed_qty'], {num: true}), c('outstanding qty', ['outstanding_qty'], {num: true}),
      c('first fill', ['first_fill_at']), c('decision', ['decision_id'])],
    positions: [I, c('side', ['holding_side']), c('open qty', ['open_qty'], {num: true}), c('avg cost incl. fees', ['avg_cost_per_contract_incl_fees'], {num: true, u: 'price'}),
      c('cost basis', ['cost_basis_usd'], {num: true, u: 'usd0'}), {label: 'mark', render: markCell}, {label: 'marked value', num: true, render: orNot('marked_value_usd', 'NO MARK')},
      {label: 'unrealized', num: true, render: orNot('unrealized_pnl_usd', 'NOT STATED')}, c('realized', ['realized_pnl_usd'], {num: true, u: 'usd'}), c('first fill', ['first_fill_at'])],
    standing_orders: ORDERS,
    recommendations: [c('reviewed', ['reviewed_at']), c('group', ['group_id']), c('trigger', ['trigger']), c('recommendation', ['recommendation']), c('refusal', ['refusal']), c('action', ['action'])],
    daily_reports: [c('day', ['report_day']), c('version', ['version'], {num: true}), c('final', ['final']), c('reconciles', ['reconciles']), c('generated', ['generated_at']), c('time zone', ['reporting_tz']), c('digest', ['digest'])],
    audit_entries: [c('found', ['found_at']), c('severity', ['severity']), c('kind', ['kind']), c('subject', ['subject']), c('detail', ['detail']), c('improvement task', ['improvement_task_id'])]
  };
  var NOTES = {positions: FLOORS, recommendations: FLOORS,
    opportunities: '<p class="note">One decision per evaluated market once the session runs: ENTER, or a named refusal.</p>'};
  function section(agent, key, o, url) {
    if (!o || o.kind === 'NOT_DEPLOYED') return {status: 'UNAVAILABLE', html: sectUnav()};
    if (o.kind !== 'OK') return {status: 'UNAVAILABLE', html: AG.gate(o, url || '')};
    var j = isObj(o.json) ? o.json : {};
    var s = sec(j[key]) || sec(j[agent]);          // the schema-absent shape names one section for the whole route
    if (!s) return {status: 'UNAVAILABLE', html: CC.unavLine("the route returned no '" + key + "' section")};
    if (s.status === 'UNAVAILABLE') return {status: 'UNAVAILABLE', html: CC.unavLine(s.why)};
    if (s.status === 'EMPTY') return {status: 'EMPTY', html: CC.emptyLine(s.why)};
    var rows = Array.isArray(s.data) ? s.data : (isObj(s.data) ? [s.data] : []);
    if (!rows.length) return {status: 'EMPTY', html: CC.emptyLine(s.why || 'the section is OK but carried no records')};
    return {status: 'OK', html: (NOTES[key] || '') + '<p class="note">' + SIM + ' records from the paper session' + (AG.toEpoch(j.last_updated_at) !== null ? ' · last ledger change ' + AG.ts(j.last_updated_at) : '') + '.</p>'
      + AG.table(rows, COLS[key] || null, {rd: AG.toEpoch(j.as_of)})};
  }
  function sessionPanel(o, url) {
    if (!o || o.kind === 'NOT_DEPLOYED') return {status: 'UNAVAILABLE', html: sectUnav()};
    if (o.kind !== 'OK') return {status: 'UNAVAILABLE', html: AG.gate(o, url || '')};
    var j = isObj(o.json) ? o.json : {}, s = sec(j.session), h = sec(j.health), en = sec(j.enablement), out = '';
    if (!s) return {status: 'UNAVAILABLE', html: CC.unavLine("the route returned no 'session' section")};
    if (s.status === 'OK' && isObj(s.data)) {
      var d = s.data;
      out += '<dl class="pdl">' + [['session', esc(d.session_id || '?')], ['started', d.started_at ? AG.ts(d.started_at) + ' (' + esc(nyTime(d.started_at)) + ')' : CC.nr('no start time')],
        ['status', esc(d.status || '?')], ['simulator', esc(d.simulator_version || '?')], ['frozen config', esc(String(d.config_sha || '?').slice(0, 12))],
        ['reporting time zone', esc(d.reporting_tz || '?')]].map(function (p) { return '<dt>' + p[0] + '</dt><dd>' + p[1] + '</dd>'; }).join('') + '</dl>'
        + (d.frozen ? '<p class="note">' + esc(d.frozen) + '</p>' : '');
    } else out += s.status === 'EMPTY' ? CC.emptyLine(s.why) : CC.unavLine(s.why);
    if (h) {
      if (h.status === 'OK' && isObj(h.data)) {
        var hd = h.data, exp = num(hd.mutation_attempts_expected) ? hd.mutation_attempts_expected : 0, n = hd.mutation_attempts;
        var mut = num(n) ? (n > exp ? '<b class="neg">' + n + ' (expected ' + exp + ')</b>' : n + ' (expected ' + exp + ')') : 'not reported';
        out += '<p class="note">Health: last heartbeat ' + (hd.heartbeat_at ? AG.ts(hd.heartbeat_at) : 'none yet') + ' · ' + (num(hd.passes) ? hd.passes : '?') + ' pass(es) · ' + (num(hd.errors) ? hd.errors : '?') + ' error(s) · venue mutation attempts from the paper path: ' + mut + (hd.last_error ? ' · last error: ' + esc(hd.last_error) : '') + '</p>';
      } else out += '<p class="note">Health: ' + esc(h.status) + ' — ' + esc(h.why || 'no reason given') + '</p>';
    }
    if (en && isObj(en.data)) {
      var e = en.data;
      out += '<p class="note">Enablement: ' + (e.enabled === true ? '<b>ENABLED</b>' : '<b class="neg">NOT ENABLED</b>' + (e.refusal ? ' — ' + esc(e.refusal) : ''))
        + ' · environment flag ' + esc(e.env_flag || '?') + ' ' + (e.env_on === true ? 'on' : e.env_on === false ? 'off' : 'not reported') + ' · control row ' + (e.control_on === true ? 'on' : e.control_on === false ? 'off' : 'absent') + '</p>';
    }
    return {status: s.status, html: out};
  }
  CC.paper = {FIELDS: FIELDS, RUNNING: RUNNING, UNAV: UNAV, SECT_UNAV: SECT_UNAV, COLS: COLS, KEEP: KEEP, banner: banner, account: account, drawdown: drawdown,
    ledger: ledger, ledgerRow: ledgerRow, posParts: posParts, section: section, sessionPanel: sessionPanel, nyTime: nyTime, iso: iso,
    newState: newState, fromAccount: fromAccount, onEvent: onEvent, view: view, entries: entries};
})(AG, CC);
"""

#: THE PAPER BOOT: the account (GET + the ledger stream), the session and
#: per-agent sections, and the OPERATIONAL read that leads the page
#: (agent_cc_ops). LIVE ON A PHONE: same-origin credentials on every read; the
#: stream reconnects with exponential backoff; while it is not open every read
#: is repeated every 15 s (POLL_MS); a page brought back from the background
#: (visibilitychange) or from the back-forward cache (pageshow, persisted)
#: re-reads at once and reopens the stream -- mobile Safari freezes timers and
#: drops EventSource connections in both cases.
PAPER_BOOT_JS = r"""
(function (AG, CC) {
  'use strict';
  var E = Object.assign({}, AG.ENDPOINTS, %%PAPER_EP%%), F = fetch.bind(window);
  var kind = document.body.getAttribute('data-kind');
  var $ = function (id) { return document.getElementById(id); };
  var P = CC.paper, O = CC.ops, S = P.newState(), prev = null, es = null, opened = false, retry = 0, timer = null, acctO = null, sessO = null;
  var POLL_MS = 15000, LIVE_MS = 30000, pollT = null, opsT = null, busy = false;
  var OPS = {json: null, okAt: null, tryAt: null, fail: null, stream: 'CONNECTING', streamNote: ''};
  function nowS() { return Date.now() / 1000; }
  function setPanel(id, status, html) {
    var p = $(id); if (!p) return;
    p.setAttribute('data-status', status);
    var pl = p.querySelector('[data-pill]'); if (pl) pl.outerHTML = CC.pill(status);
    var b = p.querySelector('[data-body]'); if (b) { b.innerHTML = html; if (CC.labelize) CC.labelize(b); }
  }
  function setBanner() { var b = P.banner(acctO, sessO), el = $('paper-banner'); if (el) { el.setAttribute('data-state', b.state); el.textContent = b.text; } }
  function streamOpen() { return !!(es && es.readyState === 1); }
  function paintFresh() {
    var el = $('paper-fresh'); if (el && O) el.innerHTML = O.freshHtml(O.fresh(OPS, nowS()));
  }
  function conn(state, note) {
    var el = $('paper-conn');
    if (el) { el.setAttribute('data-conn', state); el.textContent = state + ' · ' + (note || new Date().toISOString().slice(11, 19) + 'Z'); }
    OPS.stream = state === 'LIVE' ? 'LIVE' : state === 'SIGN-IN REQUIRED' ? 'SIGN-IN' : state === 'RECONNECTING' ? 'RECONNECTING' : state === 'UNAVAILABLE' ? 'UNAVAILABLE' : 'POLLING';
    OPS.streamNote = note || '';
    paintFresh();
  }
  function paint() {
    var r = P.account(P.view(S), prev); if (r.values) prev = r.values;
    var l = S.ledgerSec;
    setPanel('p-paper-account', r.status, r.html + P.drawdown(S.drawdown) + P.ledger(P.entries(S), S.fresh, l && l.status !== 'OK' ? l.status + ' · ' + (l.why || 'no reason given') : null));
  }
  // SIGNED OUT (401/403 from any read, or the stream failing while a probe
  // read answers 401/403): ONE prompt with the way to sign in -- never an
  // unexplained feed failure. Polling continues, so the page recovers on its
  // own once the session is back.
  var auth = {lost: false, why: null};
  function signin(lost, why) {
    auth.lost = !!lost; auth.why = why || null;
    var el = $('cc-signin'); if (!el || !O) return;
    if (lost) { el.innerHTML = O.signinHtml(document.documentElement.classList.contains('cc-framed'), why); el.hidden = false; }
    else { el.hidden = true; el.innerHTML = ''; }
  }
  function locked(o) { return !!o && o.kind === 'LOCKED'; }
  async function probeAuth() {
    var o = await AG.load(E.paper_account + '?entries=1', F);
    if (locked(o)) {
      if (es) { es.close(); es = null; }
      clearTimeout(timer); timer = null;
      signin(true, 'the live stream needs a COMMAND session (the API answered ' + (o.status || 401) + ')');
      conn('SIGN-IN REQUIRED', 'sign in to resume live updates; polling every 15 s meanwhile');
      return true;
    }
    return false;
  }
  // THE OPERATIONAL READ: one GET for every panel this page leads with
  function paintOps() {
    if (!O) return;
    var ac = O.accountStrip(OPS), ae = $('paper-acct');
    if (ae) { ae.setAttribute('data-status', ac.status); ae.innerHTML = ac.html; }
    var ps = O.panels(kind, OPS);
    Object.keys(ps).forEach(function (id) { setPanel(id, ps[id].status, ps[id].html); });
    var k = $('cc-kpis');
    var kl = O.kpis(kind, OPS);   // one figure (a failed read) spans the row: the state is never broken mid-word
    if (k) k.innerHTML = kl.map(function (x) { return '<div class="cc-kpi"' + (kl.length === 1 ? ' style="grid-column:1/-1"' : '') + '><div class="lbl">' + AG.esc(x.label) + '</div><div class="v">' + x.value + '</div>' + (x.sub ? '<div class="s">' + x.sub + '</div>' : '') + '</div>'; }).join('');
    var sl = O.stageLine(OPS, nowS());
    if ($('cc-state')) $('cc-state').innerHTML = sl.state;
    if ($('cc-hb')) $('cc-hb').innerHTML = sl.hb;
    if (CC.setPaperMode) CC.setPaperMode(O.mode(OPS, nowS()));
    paintFresh();
  }
  async function ops() {
    var url = E.paper_operations + '?agent=' + encodeURIComponent(kind) + (kind === 'derek' ? '&limit=10' : '');
    OPS.tryAt = nowS();
    var o = await AG.load(url, F), f = O ? O.failure(o, url) : {state: 'UNAVAILABLE', why: 'the operations renderer is not loaded'};
    if (f) OPS.fail = f; else { OPS.fail = null; OPS.json = o.json; OPS.okAt = nowS(); }
    if (locked(o)) signin(true, 'the paper read answered ' + (o.status || 401));
    else if (o && o.kind === 'OK' && auth.lost) signin(false);
    paintOps();
  }
  function opsSoon() { clearTimeout(opsT); opsT = setTimeout(ops, 1500); }
  function frame(src, name) {
    return function (ev) {
      if (src !== es) return;
      var d; try { d = JSON.parse(ev.data); } catch (_) { return; }
      var what = P.onEvent(S, name, d);
      if (what === 'heartbeat') conn('LIVE', 'server heartbeat ' + (P.iso(S.beat) || 'without a time'));
      else if (what === 'unavailable') { src.close(); es = null; conn('UNAVAILABLE', S.streamWhy); schedule(); }
      else if (what === 'figures' || what === 'replay') { paint(); if (what === 'figures') opsSoon(); }
    };
  }
  // The first connection takes the server's snapshot and lets the browser resume
  // by Last-Event-ID; after the browser gives up, a manual reconnect resumes
  // from the last sequence this page holds with ?last=<sequence>.
  function stream() {
    if (!window.EventSource) { conn('DISCONNECTED', 'this browser has no EventSource; polling every 15 s'); return; }
    if (es || timer) return;
    var url = E.paper_stream + (opened && S.seq !== null ? '?last=' + encodeURIComponent(S.seq) : '');
    var src = es = new EventSource(url); opened = true;
    src.onopen = function () { if (src === es) { retry = 0; conn('LIVE'); schedule(); } };
    ['snapshot', 'ledger', 'heartbeat', 'unavailable'].forEach(function (n) { src.addEventListener(n, frame(src, n)); });
    src.onerror = function () {
      if (src !== es) return;
      // AN EventSource CANNOT SEE A 401: ask a plain read whether the session
      // is the cause before saying RECONNECTING
      probeAuth().then(function (gone) { if (gone) schedule(); });
      if (src.readyState === 2) {
        conn('DISCONNECTED', 'polling every 15 s until the stream reopens'); src.close(); es = null;
        clearTimeout(timer); timer = setTimeout(function () { timer = null; conn('RECONNECTING'); stream(); }, Math.min(30000, 1000 * Math.pow(2, retry++)));
      } else conn('RECONNECTING', 'polling every 15 s until the stream reopens');
      schedule();
    };
  }
  async function sections() {
    var routes = {};
    [].forEach.call(document.querySelectorAll('[data-paper-route]'), function (p) { var u = p.getAttribute('data-paper-route'); (routes[u] = routes[u] || []).push(p); });
    await Promise.all(Object.keys(routes).map(async function (u) {
      var r = await AG.load(u, F);
      if (u === E.paper_session) { sessO = r; setBanner(); }
      routes[u].forEach(function (p) {
        var k = p.getAttribute('data-paper-key');
        var s = k === 'session' ? P.sessionPanel(r, u) : P.section(p.getAttribute('data-paper-agent'), k, r, u);
        setPanel(p.id, s.status, s.html);
      });
    }));
  }
  async function account() {
    acctO = await AG.load(E.paper_account + '?entries=' + P.KEEP, F);
    setBanner();
    if (acctO.kind !== 'OK') {
      if (locked(acctO)) { signin(true, 'the paper account read answered ' + (acctO.status || 401)); if (es) { es.close(); es = null; } conn('SIGN-IN REQUIRED', 'sign in to resume live updates'); }
      else if (!es) conn(acctO.kind === 'NOT_DEPLOYED' ? 'UNAVAILABLE' : 'DISCONNECTED', acctO.kind === 'NOT_DEPLOYED' ? P.UNAV : (acctO.why || acctO.kind));
      setPanel('p-paper-account', 'UNAVAILABLE', acctO.kind === 'NOT_DEPLOYED' ? '<div class="plain"><p class="big">UNAVAILABLE · ' + P.UNAV + '</p><p class="mute">No paper figure is shown: none was sent. This is not a zero balance.</p></div>' : AG.gate(acctO, E.paper_account));
    } else {
      P.fromAccount(S, acctO.json); paint();
      var a = acctO.json.account;
      if (!es) { if (a && a.status !== 'UNAVAILABLE') stream(); else conn('UNAVAILABLE', (a && a.why) || 'the account read named no reason'); }
    }
  }
  async function load() {
    if (busy) return; busy = true;
    try { await Promise.all([account(), ops(), sections()]); } finally { busy = false; }
  }
  // THE POLLING FALLBACK: every 15 s while the stream is not open, 30 s while
  // it is (the operational records are not on the ledger stream).
  function schedule() {
    clearTimeout(pollT);
    pollT = setTimeout(function () { if (document.hidden) { schedule(); return; } load().then(schedule, schedule); }, streamOpen() ? LIVE_MS : POLL_MS);
  }
  function wake() {
    if (document.hidden) return;
    if (!es || es.readyState === 2) { if (es) { es.close(); es = null; } clearTimeout(timer); timer = null; retry = 0; }
    load().then(schedule, schedule);
  }
  document.addEventListener('visibilitychange', function () { if (!document.hidden) wake(); });
  window.addEventListener('pageshow', function (e) { if (e && e.persisted) { if (es) { es.close(); es = null; } clearTimeout(timer); timer = null; retry = 0; wake(); } });
  window.addEventListener('online', wake);
  setInterval(function () { if (!document.hidden) { paintFresh(); if (OPS.json && CC.setPaperMode) CC.setPaperMode(O.mode(OPS, nowS())); } }, 5000);
  load().then(schedule, schedule);
})(AG, CC);
"""
