"""THE THREE AGENT WORKSPACES AND THE PRODUCT DEMO, SERVED FROM THIS ORIGIN.

Derek (discovery and entry), Xavier (position management and exits) and
Audrey (audit, management chat, improvement) each have a Command Centre page
here, plus an index and a cinematic walkthrough of the architecture.

WHAT THESE PAGES ARE. Presentation only. Each page makes same-origin reads of
the JSON the agent endpoints return (`GET /api/command/agents`, `/derek`,
`/xavier`, `/audrey`) with `credentials: 'same-origin'` and draws every
section from the shared workspace contract:

    {"agent": {...}, "read_at": epoch, "read_only": true,
     "sections": {"<name>": {"status": "OK"|"EMPTY"|"UNAVAILABLE",
                             "why": str|null, "data": ..., "evidence": [...]}}}

Every required section name has a specialised renderer; any other section is
drawn generically. A section the endpoint did not return is drawn as MISSING,
EMPTY carries its reason and is visually neutral (never green), UNAVAILABLE
carries the failed read's reason, and a value the record does not carry is
UNKNOWN, never zero. A 401 from a read shows the locked state with a link to
the desk; a 404 says the endpoint is not deployed in this build.

WHAT THEY ARE NOT. No page sends an order, holds a credential, or sets an
auth header. The only writes are the Audrey chat question and the directive
confirm/cancel and candidate approve buttons, which POST to the service and
say plainly that they need the OPERATOR credential; the service decides.

AUTH ON THE HTML. Same as the management overview page: the HTML is served
only to a COMMAND read credential. An unauthenticated browser gets a small
locked page (401, like the desk's sign-in) that links to the desk and
carries no data. The JSON is always behind `require_command` in the agent
routers themselves.

The standalone rehearsal copy of the demo, `research/demo/three_agents_demo.html`,
is generated from `demo_html(standalone=True)`; a test compares the two.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

router = APIRouter()

DESK_PAGE = "/api/command/bettor/desk/page"
LOCKED_TEXT = "Locked: open the desk and unlock COMMAND"
REHEARSAL_LABEL = "REHEARSAL · ILLUSTRATIVE PRICES AND FILLS · NO LIVE ORDERS"

AGENT_STATES = ("IDLE", "EVALUATING", "WAITING_FOR_EVIDENCE",
                "WAITING_FOR_PROVIDER", "BLOCKED", "DECISION_RECORDED",
                "RECOVERING", "FAILED")

DEPENDENCY_CLASSES = ("ENGINEERING_CONFIGURATION", "EVIDENCE", "ENGINEERING",
                      "ELAPSED_TIME", "OWNER_DECISION")

ENDPOINTS = {
    "index": "/api/command/agents",
    "derek": "/api/command/agents/derek",
    "xavier": "/api/command/agents/xavier",
    "audrey": "/api/command/agents/audrey",
    # Karen (red team / challenge, migration 207): api/agents_karen.py
    "karen": "/api/command/karen",
    # Archer and Scout (migration 217): api/agents_pos.py
    "archer": "/api/command/archer",
    "scout": "/api/command/scout",
    "chat": "/api/command/agents/audrey/chat",
    "directives": "/api/command/agents/audrey/directives",
    # the Command Centre pages' extra read-only routes (agents_cc_reads) and
    # the character module (served by `agents_static` below)
    "derek_orders": "/api/command/agents/derek/orders",
    "xavier_standing": "/api/command/agents/xavier/standing-orders",
    "xavier_payoff": "/api/command/agents/xavier/payoff-demonstration",
    "audrey_performance": "/api/command/agents/audrey/performance",
    "characters": "/api/command/agents/static/cc_characters.js",
    "avatar": "/api/command/agents/static/cc_avatar.js",
    "labels": "/api/command/agents/labels",
    # the capability workbench (api/agent_capabilities.py): reads with COMMAND
    # auth; its goal/control/experiment writes need CONTROL auth server-side
    # and touch only agent_tasks and the paper-learning proposals
    "capabilities": "/api/command/agents/capabilities",
    # the paper session, as api/command_paper.py serves it (404 in a build
    # without it, and the pages say so): see agent_cc_page.PAPER_CONTRACT
    "paper_account": "/api/command/paper/account",
    "paper_stream": "/api/command/paper/stream",
    "paper_session": "/api/command/paper/session",
    "paper_derek": "/api/command/paper/derek",
    "paper_xavier": "/api/command/paper/xavier",
    "paper_audrey": "/api/command/paper/audrey",
    # the operational view the pages lead with (bettor_paper_ops)
    "paper_operations": "/api/command/paper/operations",
    "paper_overview": "/api/command/paper/overview",
}
PAPER_EP_KEYS = ("paper_account", "paper_stream", "paper_session",
                 "paper_derek", "paper_xavier", "paper_audrey",
                 "paper_operations")

REQUIRED_SECTIONS = {
    "derek": ("status", "versions", "coverage", "subscription",
              "opportunity_queue", "decisions", "plans_fills", "handoffs",
              "latency", "performance", "collection"),
    "xavier": ("status", "versions", "positions", "reviews", "ladder",
               "alternatives", "payout_tables", "execution", "recovery",
               "performance", "servicing_cadence"),
    "audrey": ("status", "versions", "daily_reports", "findings", "outcomes",
               "cohort_quality", "tasks", "candidates", "evaluations",
               "releases", "directives", "conversations", "provider"),
    "karen": ("status", "authority", "current_challenges",
              "recent_challenges", "metrics", "detectors"),
    "archer": ("status", "authority", "desk", "execution_funnel",
              "current_estimates",
              "predicted_vs_realized", "scorecard", "candidate_reviews",
              "runner"),
    "scout": ("status", "authority", "desk", "sources", "features",
              "tournaments", "observations", "scorecard", "runner"),
}

PAGE_PATHS = {
    "index": "/api/command/agents/page",
    "derek": "/api/command/agents/derek/page",
    "xavier": "/api/command/agents/xavier/page",
    "audrey": "/api/command/agents/audrey/page",
    "karen": "/api/command/agents/karen/page",
    "archer": "/api/command/agents/archer/page",
    "scout": "/api/command/agents/scout/page",
    "demo": "/api/command/agents/demo/page",
}


# ════════════════════════════════════════════════════════════════════
# SHARED STYLE
# ════════════════════════════════════════════════════════════════════
BASE_CSS = r"""
:root{
--sans:ui-sans-serif,system-ui,-apple-system,"SF Pro Text","Segoe UI Variable Text","Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
--display:ui-sans-serif,system-ui,-apple-system,"SF Pro Display","Segoe UI Variable Display","Segoe UI",Roboto,sans-serif;
--mono:ui-monospace,"SF Mono",SFMono-Regular,"JetBrains Mono","Cascadia Mono",Menlo,Consolas,monospace;
--bg:#f1efe9;--bg-2:#e8e5dd;--panel:#fbfaf7;--panel-2:#f4f2ec;--ink:#14161b;--ink-2:#454a55;--ink-3:#767c88;
--line:#dcd8ce;--line-2:#c9c4b8;--ok:#1b7550;--warn:#9a6200;--bad:#b0312a;--info:#2d56c6;--neutral:#6b7280;
--d-cfg:#4557d6;--d-eng:#7c4ddb;--d-evi:#0c8279;--d-time:#9a6c00;--d-own:#bf3f0c;
--acc:#2f55d4;--r:14px;
--shadow:0 1px 0 rgba(255,255,255,.75) inset,0 1px 2px rgba(20,20,30,.05),0 10px 28px -16px rgba(20,20,30,.22);
color-scheme:light dark}
.ag-derek{--acc:#b1600a}.ag-xavier{--acc:#0a7a8b}.ag-audrey{--acc:#6a45c8}.ag-index{--acc:#2f55d4}
@media (prefers-color-scheme:dark){
:root{--bg:#08090d;--bg-2:#0c0f14;--panel:#10141a;--panel-2:#151a22;--ink:#e9ecf1;--ink-2:#a8b0bd;--ink-3:#6d7686;
--line:#1e252f;--line-2:#2a3340;--ok:#45c089;--warn:#e6ab43;--bad:#ff766b;--info:#7d9dff;--neutral:#8b93a1;
--d-cfg:#8594ff;--d-eng:#b394ff;--d-evi:#3fd2c4;--d-time:#e4b646;--d-own:#ff8a55;
--shadow:0 1px 0 rgba(255,255,255,.035) inset,0 14px 36px -18px rgba(0,0,0,.85)}
.ag-derek{--acc:#f2a541}.ag-xavier{--acc:#3fd0e0}.ag-audrey{--acc:#a98cff}.ag-index{--acc:#86a2ff}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;min-height:100vh;color:var(--ink);font:14.5px/1.5 var(--sans);font-variant-numeric:tabular-nums;
background:radial-gradient(1100px 520px at 8% -12%,color-mix(in oklab,var(--acc) 16%,transparent),transparent 62%),
radial-gradient(900px 420px at 105% 0%,color-mix(in oklab,var(--acc) 7%,transparent),transparent 60%),var(--bg)}
body::before{content:"";position:fixed;inset:0;pointer-events:none;z-index:0;opacity:.55;
background-image:linear-gradient(to right,color-mix(in oklab,var(--ink) 5%,transparent) 1px,transparent 1px),
linear-gradient(to bottom,color-mix(in oklab,var(--ink) 5%,transparent) 1px,transparent 1px);
background-size:44px 44px;-webkit-mask-image:linear-gradient(to bottom,#000,transparent 420px);mask-image:linear-gradient(to bottom,#000,transparent 420px)}
a{color:var(--acc);text-decoration:none}a:hover{text-decoration:underline}
code,.mono{font-family:var(--mono);font-size:.86em}
.top{position:sticky;top:0;z-index:20;display:flex;align-items:center;gap:18px;padding:10px 24px;
background:color-mix(in oklab,var(--bg) 78%,transparent);-webkit-backdrop-filter:blur(14px) saturate(1.2);backdrop-filter:blur(14px) saturate(1.2);
border-bottom:1px solid var(--line)}
.brand{display:flex;align-items:center;gap:9px;color:var(--ink);font:600 13px/1 var(--mono);letter-spacing:.14em;white-space:nowrap}
.brand:hover{text-decoration:none}.brand b{color:var(--ink-3);font-weight:500}
.mark{width:18px;height:18px;border-radius:5px;background:conic-gradient(from 210deg,var(--acc),color-mix(in oklab,var(--acc) 30%,var(--ink)),var(--acc));box-shadow:0 0 0 1px color-mix(in oklab,var(--acc) 40%,transparent),0 0 18px color-mix(in oklab,var(--acc) 45%,transparent)}
.top nav{display:flex;gap:2px;overflow-x:auto;scrollbar-width:none;flex:1;min-width:0}
.top nav::-webkit-scrollbar{display:none}
.top nav a{color:var(--ink-2);padding:6px 11px;border-radius:8px;font-size:13px;white-space:nowrap}
.top nav a:hover{background:var(--panel-2);text-decoration:none}
.top nav a[aria-current=page]{color:var(--ink);background:var(--panel);box-shadow:inset 0 -2px 0 var(--acc),0 0 0 1px var(--line)}
.meta{display:flex;align-items:center;gap:10px;font:11.5px/1 var(--mono);color:var(--ink-3);white-space:nowrap}
.ro{padding:4px 7px;border:1px solid var(--line-2);border-radius:6px;letter-spacing:.1em}
button,.btn{font:600 12.5px/1 var(--sans);color:var(--ink);background:var(--panel);border:1px solid var(--line-2);border-radius:9px;padding:8px 12px;cursor:pointer}
button:hover,.btn:hover{border-color:var(--acc);text-decoration:none}
button:disabled{opacity:.55;cursor:not-allowed}
.btn.primary,button.primary{background:var(--acc);border-color:var(--acc);color:#fff}
main{position:relative;z-index:1;max-width:1280px;margin:0 auto;padding:26px 24px 64px}
.boot{padding:60px 0;color:var(--ink-3);font-family:var(--mono)}
.lbl{font:600 10.5px/1.2 var(--mono);letter-spacing:.12em;text-transform:uppercase;color:var(--ink-3)}
/* hero */
.hero{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(0,1fr);gap:18px;margin-bottom:18px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:var(--r);box-shadow:var(--shadow);padding:20px 22px;min-width:0}
.ident{display:flex;gap:18px;align-items:flex-start}
.sigil{flex:none;position:relative;width:64px;height:64px;border-radius:18px;display:grid;place-items:center;
font:700 26px/1 var(--mono);color:#fff;background:linear-gradient(145deg,var(--acc),color-mix(in oklab,var(--acc) 45%,#000));
box-shadow:0 0 0 1px color-mix(in oklab,var(--acc) 50%,transparent),0 16px 34px -14px color-mix(in oklab,var(--acc) 80%,transparent)}
.sigil::after{content:"";position:absolute;inset:-6px;border-radius:22px;border:1px dashed color-mix(in oklab,var(--acc) 45%,transparent)}
.ident h1{font:680 30px/1.08 var(--display);letter-spacing:-.02em;margin:2px 0 6px}
.ident .role{font:600 11px/1 var(--mono);letter-spacing:.14em;color:var(--acc);text-transform:uppercase}
.mandate{color:var(--ink-2);margin:8px 0 0;max-width:62ch}
.contemplating{margin-top:16px;padding:12px 14px;border-radius:10px;background:var(--panel-2);border:1px solid var(--line)}
.contemplating p{margin:4px 0 0}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.chip{display:inline-flex;align-items:center;gap:6px;font:12px/1 var(--mono);padding:5px 8px;border-radius:7px;background:var(--panel-2);border:1px solid var(--line);color:var(--ink-2);white-space:nowrap}
.chip b{color:var(--ink);font-weight:600}
.statehead{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap}
.metrics{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:1px;background:var(--line);border:1px solid var(--line);border-radius:11px;overflow:hidden;margin-top:14px}
.metric{background:var(--panel);padding:10px 12px;min-width:0}
.metric .v{font:600 17px/1.25 var(--display);margin-top:4px;overflow-wrap:anywhere}
.metric .s{color:var(--ink-3);font-size:12px}
/* state badges -- only the eight truthful states */
.sb{display:inline-flex;align-items:center;gap:8px;font:650 11.5px/1 var(--mono);letter-spacing:.09em;padding:7px 11px;border-radius:999px;border:1px solid currentColor;white-space:nowrap}
.sb i{width:7px;height:7px;border-radius:50%;background:currentColor;flex:none}
.sb.big{font-size:13px;padding:9px 14px}
.sb-IDLE{color:var(--neutral)}.sb-EVALUATING{color:var(--acc)}.sb-WAITING_FOR_EVIDENCE,.sb-WAITING_FOR_PROVIDER{color:var(--warn)}
.sb-BLOCKED{color:var(--d-own)}.sb-DECISION_RECORDED{color:var(--info)}.sb-RECOVERING{color:var(--warn)}.sb-FAILED{color:var(--bad)}
.sb-UNRECOGNISED,.sb-NONE{color:var(--ink-3);border-style:dashed}
.sb-EVALUATING i,.sb-RECOVERING i{animation:pulse 1.6s ease-in-out infinite}
@keyframes pulse{0%,100%{box-shadow:0 0 0 0 currentColor;opacity:1}50%{box-shadow:0 0 0 5px transparent;opacity:.55}}
/* section nav */
.secnav{display:flex;gap:6px;overflow-x:auto;padding:2px 0 14px;margin-bottom:6px;scrollbar-width:thin}
.secnav a{display:inline-flex;align-items:center;gap:7px;font:12px/1 var(--mono);padding:7px 10px;border-radius:8px;border:1px solid var(--line);background:var(--panel);color:var(--ink-2);white-space:nowrap}
.secnav a:hover{text-decoration:none;border-color:var(--acc)}
.dot{width:7px;height:7px;border-radius:50%;flex:none;background:var(--ink-3)}
.dot-OK{background:var(--ok)}.dot-EMPTY{background:transparent;box-shadow:inset 0 0 0 1.5px var(--neutral)}.dot-UNAVAILABLE{background:var(--bad)}.dot-MISSING{background:transparent;box-shadow:inset 0 0 0 1.5px var(--warn)}
/* cards */
.grid{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:16px}
.card{grid-column:span 6;background:var(--panel);border:1px solid var(--line);border-radius:var(--r);box-shadow:var(--shadow);padding:16px 18px 12px;min-width:0;scroll-margin-top:72px;position:relative}
.card.wide{grid-column:span 12}
.card::before{content:"";position:absolute;left:-1px;top:14px;bottom:14px;width:3px;border-radius:3px;background:var(--line-2)}
.card.st-card-OK::before{background:var(--acc)}
.card.st-card-EMPTY::before{background:repeating-linear-gradient(to bottom,var(--neutral) 0 4px,transparent 4px 8px)}
.card.st-card-UNAVAILABLE::before{background:var(--bad)}
.card.st-card-MISSING::before{background:repeating-linear-gradient(to bottom,var(--warn) 0 4px,transparent 4px 8px)}
.card>header{display:flex;align-items:flex-start;gap:12px;margin-bottom:10px}
.idx{font:600 11px/1 var(--mono);color:var(--ink-3);padding-top:5px}
.ttl{flex:1;min-width:0}.ttl h3{margin:0;font:650 16px/1.3 var(--display);letter-spacing:-.005em}
.ttl .key{color:var(--ink-3);font-size:11px}
.pill{font:650 10.5px/1 var(--mono);letter-spacing:.1em;padding:5px 8px;border-radius:6px;border:1px solid currentColor;white-space:nowrap}
.st-OK{color:var(--ok);background:color-mix(in oklab,var(--ok) 9%,transparent)}
.st-EMPTY{color:var(--neutral);border-style:dashed}
.st-UNAVAILABLE{color:var(--bad);background:color-mix(in oklab,var(--bad) 8%,transparent)}
.st-MISSING,.st-UNRECOGNISED{color:var(--warn);border-style:dashed}
.why{display:flex;gap:10px;align-items:baseline;margin:0 0 10px;color:var(--ink-2);font-size:13.5px}
.why>span{font:600 10px/1 var(--mono);letter-spacing:.12em;text-transform:uppercase;color:var(--ink-3);flex:none}
.note{margin:0 0 10px;color:var(--ink-3);font-size:12.5px}
.emptyline{color:var(--ink-3);margin:6px 0}
.unav{color:var(--bad);margin:6px 0}
.card>footer{display:flex;flex-wrap:wrap;gap:8px;align-items:center;justify-content:space-between;border-top:1px dashed var(--line);margin-top:12px;padding-top:9px}
.evs{display:flex;flex-wrap:wrap;gap:5px;align-items:center}
.ev{font:11px/1 var(--mono);padding:4px 7px;border-radius:6px;border:1px solid var(--line-2);color:var(--acc);white-space:nowrap}
.ev::after{content:" \2197"}
.ev-nolink{color:var(--ink-3);border-style:dashed}.ev-nolink::after{content:""}
.none{color:var(--ink-3);font-size:12px}
details.raw summary{cursor:pointer;font:11px/1 var(--mono);color:var(--ink-3);list-style:none}
details.raw summary::-webkit-details-marker{display:none}
details.raw summary::before{content:"{ } "}
details.raw[open]{flex-basis:100%}
details.raw pre{max-height:320px;overflow:auto;background:var(--panel-2);border:1px solid var(--line);border-radius:8px;padding:10px;font:11.5px/1.45 var(--mono);white-space:pre-wrap;word-break:break-word}
/* values */
.unk{font:650 10.5px/1 var(--mono);letter-spacing:.08em;color:var(--warn);font-style:normal;border-bottom:1px dotted currentColor;cursor:help}
.neg{color:var(--bad)}.pos{color:var(--ok)}
.bool{font:600 11px/1 var(--mono)}
.age{color:var(--ink-3);font-size:.9em}
time{white-space:nowrap}
/* tables */
.tbl{overflow-x:auto;-webkit-overflow-scrolling:touch;border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%;font-size:12.5px}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:0}
thead th{font:600 10px/1.25 var(--mono);letter-spacing:.09em;text-transform:uppercase;color:var(--ink-3);background:var(--panel-2);white-space:nowrap}
thead tr.grp th{text-align:center;color:var(--ink-2);border-bottom:1px solid var(--line-2)}
thead tr.grp th.g{box-shadow:inset 1px 0 0 var(--line-2),inset -1px 0 0 var(--line-2)}
td.num,th.num{text-align:right;white-space:nowrap}
tr.sel td{background:color-mix(in oklab,var(--acc) 10%,transparent)}
tr.sel td:first-child{box-shadow:inset 3px 0 0 var(--acc)}
table.kv td:first-child{color:var(--ink-3);width:38%;font:12px/1.4 var(--mono);overflow-wrap:anywhere}
table.kv td{overflow-wrap:anywhere}
.nest table.kv{font-size:12px}.nest table.kv td{padding:3px 6px}
.selbadge{font:650 10px/1 var(--mono);letter-spacing:.08em;color:var(--acc);border:1px solid var(--acc);border-radius:5px;padding:3px 5px;margin-left:6px}
/* dependency classes */
.dep{display:inline-flex;align-items:center;gap:5px;font:650 10px/1 var(--mono);letter-spacing:.06em;padding:4px 6px;border-radius:5px;border:1px solid currentColor;white-space:nowrap}
.dep-ENGINEERING_CONFIGURATION{color:var(--d-cfg)}.dep-ENGINEERING{color:var(--d-eng)}.dep-EVIDENCE{color:var(--d-evi)}.dep-ELAPSED_TIME{color:var(--d-time)}.dep-OWNER_DECISION{color:var(--d-own)}.dep-OTHER{color:var(--ink-3);border-style:dashed}
.blk{display:flex;flex-direction:column;gap:4px}.blk>div{display:flex;gap:6px;align-items:baseline;flex-wrap:wrap}
.deplegend{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px;margin:0 0 12px}
.deplegend>div{border:1px solid var(--line);border-radius:10px;padding:9px 10px;background:var(--panel-2)}
.deplegend .n{font:650 20px/1.2 var(--display);margin-top:6px}
/* bars */
.bars{display:grid;grid-template-columns:minmax(120px,max-content) 1fr auto;gap:6px 12px;align-items:center;font-size:12.5px}
.bars .nm{font-family:var(--mono);font-size:11.5px;color:var(--ink-2);overflow-wrap:anywhere}
.track{height:10px;border-radius:5px;background:var(--panel-2);border:1px solid var(--line);overflow:hidden}
.fillb{height:100%;background:linear-gradient(90deg,color-mix(in oklab,var(--acc) 70%,transparent),var(--acc))}
.fillb.x{background:repeating-linear-gradient(135deg,var(--ink-3) 0 3px,transparent 3px 6px)}
.tag{font:600 9.5px/1 var(--mono);letter-spacing:.08em;padding:3px 5px;border-radius:4px;border:1px dashed var(--ink-3);color:var(--ink-3);margin-left:6px}
.fbar{display:flex;height:10px;min-width:120px;border-radius:5px;overflow:hidden;border:1px solid var(--line)}
.fbar .f{background:var(--acc)}.fbar .o{background:repeating-linear-gradient(135deg,color-mix(in oklab,var(--ink-3) 70%,transparent) 0 3px,transparent 3px 6px)}
.legend{display:flex;flex-wrap:wrap;gap:14px;font-size:12px;color:var(--ink-2);margin:0 0 10px}
.legend span{display:inline-flex;gap:6px;align-items:center}
.sw{width:14px;height:8px;border-radius:2px;display:inline-block}
/* economics separation strip */
.econ{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin:0 0 12px}
.econ>div{padding:9px 11px;border-radius:10px;border:1px solid var(--line);background:var(--panel-2);font-size:12px;color:var(--ink-2)}
.econ b{display:block;font:650 11px/1.2 var(--mono);letter-spacing:.06em;color:var(--ink);margin-bottom:3px;text-transform:uppercase}
/* positions */
.pos-card{border:1px solid var(--line);border-radius:12px;padding:14px;margin-bottom:12px;background:var(--panel-2)}
.pos-card h4{margin:0;font:650 15px/1.3 var(--display)}
.tiles{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:1px;background:var(--line);border:1px solid var(--line);border-radius:10px;overflow:hidden;margin:10px 0}
.tiles>div{background:var(--panel);padding:8px 10px;min-width:0}
.tiles .v{font:600 15px/1.3 var(--display);margin-top:3px;overflow-wrap:anywhere}
.action{display:flex;gap:12px;align-items:flex-start;padding:10px 12px;border-radius:10px;border:1px solid color-mix(in oklab,var(--acc) 40%,var(--line));background:color-mix(in oklab,var(--acc) 7%,var(--panel))}
.action .a{font:700 17px/1.2 var(--display);color:var(--acc);white-space:nowrap}
.plain{padding:18px;border-radius:12px;border:1px dashed var(--line-2);background:var(--panel-2)}
.plain .big{font:650 18px/1.3 var(--display);margin:0 0 6px}
.plain p{margin:4px 0}
.mute{color:var(--ink-3)}
/* timeline */
.tl{list-style:none;margin:0;padding:0 0 0 14px;border-left:1px solid var(--line-2)}
.tl li{position:relative;padding:6px 0 10px 12px}
.tl li::before{content:"";position:absolute;left:-19px;top:11px;width:9px;height:9px;border-radius:50%;background:var(--panel);border:2px solid var(--acc)}
.tl .h{display:flex;gap:10px;flex-wrap:wrap;align-items:baseline}
.tl .k{font:650 12px/1.2 var(--mono)}
/* four evidence categories -- distinct by pattern, not only colour */
.cats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}
.cat{border-radius:12px;padding:12px 13px;min-width:0}
.cat h5{margin:0 0 4px;font:650 11px/1.25 var(--mono);letter-spacing:.08em;text-transform:uppercase}
.cat .n{font:680 24px/1.15 var(--display);margin:6px 0 2px}
.cat p{margin:4px 0 0;font-size:12px;color:var(--ink-2)}
.cat-ACTUAL{border:2px solid var(--ok);background:color-mix(in oklab,var(--ok) 8%,var(--panel))}
.cat-ACTUAL h5{color:var(--ok)}
.cat-OBSERVED{border:2px solid var(--info);background:radial-gradient(color-mix(in oklab,var(--info) 18%,transparent) 1px,transparent 1.5px) 0 0/9px 9px,var(--panel)}
.cat-OBSERVED h5{color:var(--info)}
.cat-MODELLED{border:2px dashed var(--warn);background:repeating-linear-gradient(135deg,color-mix(in oklab,var(--warn) 9%,transparent) 0 6px,transparent 6px 12px),var(--panel)}
.cat-MODELLED h5{color:var(--warn)}
.cat-UNRESOLVED{border:2px dotted var(--neutral);background:var(--panel)}
.cat-UNRESOLVED h5{color:var(--neutral)}
.cat-OTHER{border:1px dashed var(--line-2);background:var(--panel)}
/* findings split */
.split{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}
.split h4{margin:0 0 8px;display:flex;align-items:center;gap:8px;font:650 13px/1 var(--mono);letter-spacing:.08em}
.agent-dot{width:9px;height:9px;border-radius:3px}
.a-DEREK{background:#d98a1f}.a-XAVIER{background:#1ea6b8}.a-AUDREY{background:#8a6ae6}.a-OTHER{background:var(--ink-3)}
.find{border:1px solid var(--line);border-radius:10px;padding:9px 11px;margin-bottom:8px;background:var(--panel-2)}
.find .h{display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap}
.sev{font:650 10px/1 var(--mono);letter-spacing:.08em;padding:3px 5px;border-radius:4px;border:1px solid currentColor}
.sev-HIGH,.sev-CRITICAL{color:var(--bad)}.sev-MEDIUM,.sev-WARN,.sev-WARNING{color:var(--warn)}.sev-LOW,.sev-INFO{color:var(--neutral)}
/* task board */
.board{display:flex;gap:10px;overflow-x:auto;padding-bottom:6px}
.col{flex:0 0 250px;background:var(--panel-2);border:1px solid var(--line);border-radius:12px;padding:10px}
.col>h5{margin:0 0 8px;display:flex;justify-content:space-between;font:650 10.5px/1 var(--mono);letter-spacing:.09em;color:var(--ink-2)}
.tcard{background:var(--panel);border:1px solid var(--line);border-radius:9px;padding:9px 10px;margin-bottom:8px;font-size:12.5px}
.tcard b{display:block;font-size:13px;margin-bottom:4px}
/* pipeline */
.rel{border:1px solid var(--line);border-radius:12px;padding:12px;margin-bottom:10px;background:var(--panel-2)}
.pipe{display:flex;align-items:stretch;gap:0;overflow-x:auto;margin:10px 0}
.pipe>div{flex:1 0 120px;padding:8px 10px;border:1px solid var(--line);background:var(--panel);font-size:12px}
.pipe>div:first-child{border-radius:9px 0 0 9px}.pipe>div:last-child{border-radius:0 9px 9px 0}
.pipe>div+div{border-left:0}
.pipe .on{box-shadow:inset 0 -3px 0 var(--acc)}
.roles{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}
.roles>div{border:1px solid var(--line);border-radius:9px;padding:8px 10px;background:var(--panel)}
.warnchip{font:650 10.5px/1 var(--mono);color:var(--bad);border:1px solid var(--bad);border-radius:5px;padding:4px 6px}
.wr{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:8px}
.wmsg{font-size:12px;color:var(--ink-2)}
.needs{font:600 10.5px/1.2 var(--mono);color:var(--ink-3)}
/* search completeness, policy vs shadow, later-discovered */
.sc{display:inline-block;font:650 10.5px/1.25 var(--mono);letter-spacing:.04em;padding:4px 7px;border-radius:6px;border:1px solid currentColor;white-space:nowrap}
.sc-full{color:var(--ok)}.sc-part{color:var(--warn)}.sc-none{color:var(--ink-3);border-style:dashed}
.scp>.sc{align-self:flex-start}.scp{display:flex;flex-direction:column;gap:8px;padding:10px 12px;border:1px solid var(--line);border-radius:10px;background:var(--panel-2);margin:0 0 10px}
.scgrid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;font-size:12.5px}.scgrid .v{font:650 16px/1.2 var(--display)}
.polcmp{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin:0 0 12px}
.pol{border:1px solid var(--line);border-radius:10px;padding:10px 12px;background:var(--panel)}
.pol.shadow{border-style:dashed;background:repeating-linear-gradient(135deg,color-mix(in oklab,var(--ink-3) 6%,transparent) 0 6px,transparent 6px 12px),var(--panel)}
.later{border:1px dashed var(--warn);border-radius:9px;padding:8px 10px;margin:8px 0 0}
.later .lbl{color:var(--warn)}
/* trace */
.trace-stages{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:8px}
.tstage{border:1px solid var(--line);border-radius:10px;padding:9px 10px;background:var(--panel-2);min-width:0;position:relative}
.tstage.none{border-style:dashed;background:var(--panel)}
.tstage h5{margin:0 0 6px;font:650 10px/1.25 var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--ink-3)}
.tstage .rec{border-top:1px dashed var(--line);padding-top:6px;margin-top:6px;font-size:12px;overflow-wrap:anywhere}
.tstage .rec:first-of-type{border-top:0;padding-top:0;margin-top:0}
a.trl{border-bottom:1px dotted var(--acc)}
.tracebar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:0 0 12px}
.tracebar select{font:13px var(--mono);padding:7px 9px;border-radius:8px;border:1px solid var(--line-2);background:var(--panel);color:var(--ink);max-width:100%}
/* chat */
.chatlog{display:flex;flex-direction:column;gap:10px;max-height:520px;overflow:auto;padding:4px 2px;margin-bottom:10px}
.msg{max-width:88%;padding:10px 12px;border-radius:12px;border:1px solid var(--line);background:var(--panel-2)}
.msg.q{align-self:flex-end;background:color-mix(in oklab,var(--acc) 12%,var(--panel));border-color:color-mix(in oklab,var(--acc) 35%,var(--line))}
.msg .who{font:650 10px/1 var(--mono);letter-spacing:.1em;color:var(--ink-3);margin-bottom:6px}
.msg p{margin:0 0 6px;white-space:pre-wrap}
.prov{font:600 11px/1.35 var(--mono);color:var(--ink-2);padding:6px 8px;border-radius:7px;border:1px dashed var(--line-2);margin-top:6px}
.cites{display:flex;flex-wrap:wrap;gap:5px;margin-top:6px}
.chatform{display:flex;gap:8px;align-items:flex-end}
.chatform textarea{flex:1;min-height:64px;resize:vertical;font:14px/1.45 var(--sans);color:var(--ink);background:var(--panel-2);border:1px solid var(--line-2);border-radius:10px;padding:10px}
/* gate states */
.gate{max-width:680px;margin:48px auto;padding:28px;border-radius:16px;border:1px solid var(--line);background:var(--panel);box-shadow:var(--shadow)}
.gate h2{margin:0 0 8px;font:680 22px/1.25 var(--display)}
.gate.locked h2::before{content:"\25cf  ";color:var(--warn)}
.gate.nd{border-style:dashed}
.gate.unav h2{color:var(--bad)}
/* index */
.agents{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;margin-bottom:18px}
.acard{display:flex;flex-direction:column;gap:12px}
.acard.ag-derek,.acard.ag-xavier,.acard.ag-audrey{border-top:3px solid var(--acc)}
.acard .ident h1{font-size:23px}
.acard .sigil{width:48px;height:48px;font-size:20px;border-radius:14px}
.acard .sigil::after{border-radius:18px;inset:-5px}
.flow{display:grid;grid-template-columns:1fr auto 1fr auto 1fr;gap:10px;align-items:center;margin-bottom:18px}
.flow .n{padding:12px 14px;border-radius:12px;border:1px solid var(--line);background:var(--panel);box-shadow:var(--shadow)}
.flow .n b{display:block;font:650 14px/1.2 var(--display)}
.flow .arr{font:600 11px/1.2 var(--mono);color:var(--ink-3);text-align:center}
.foot{position:relative;z-index:1;max-width:1280px;margin:0 auto;padding:0 24px 40px;color:var(--ink-3);font-size:12px}
@media (max-width:1000px){.trace-stages{grid-template-columns:repeat(2,minmax(0,1fr))}.scgrid{grid-template-columns:repeat(2,minmax(0,1fr))}.agents{grid-template-columns:1fr}.hero{grid-template-columns:1fr}.cats{grid-template-columns:repeat(2,minmax(0,1fr))}.deplegend{grid-template-columns:repeat(3,minmax(0,1fr))}.econ{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (max-width:760px){
.top{padding:9px 16px;gap:10px;flex-wrap:wrap}.top nav{order:3;flex-basis:100%}.meta .ro{display:none}
main{padding:18px 16px 48px}.foot{padding:0 16px 32px}
.card,.card.wide{grid-column:span 12;padding:14px 14px 10px}
.metrics{grid-template-columns:repeat(2,minmax(0,1fr))}.tiles{grid-template-columns:repeat(2,minmax(0,1fr))}
.split,.roles,.polcmp,.trace-stages{grid-template-columns:1fr}.cats{grid-template-columns:1fr}.deplegend{grid-template-columns:repeat(2,minmax(0,1fr))}
.ident h1{font-size:25px}.flow{grid-template-columns:1fr}.flow .arr{transform:none}
.bars{grid-template-columns:minmax(90px,40%) 1fr auto}.msg{max-width:100%}}
@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation:none!important;transition:none!important;scroll-behavior:auto!important}}
"""


# ════════════════════════════════════════════════════════════════════
# SHARED RENDER CORE (pure: no DOM access at definition time, so the
# tests execute it under node against contract fixtures)
# ════════════════════════════════════════════════════════════════════
CORE_JS = r"""
var AG = (function () {
  'use strict';
  var STATES = ['IDLE','EVALUATING','WAITING_FOR_EVIDENCE','WAITING_FOR_PROVIDER','BLOCKED','DECISION_RECORDED','RECOVERING','FAILED'];
  var DEP_CLASSES = ['ENGINEERING_CONFIGURATION','EVIDENCE','ENGINEERING','ELAPSED_TIME','OWNER_DECISION'];
  var DEP_WHAT = {
    ENGINEERING_CONFIGURATION: 'configuration an engineer can set',
    EVIDENCE: 'waits for evidence to accumulate',
    ENGINEERING: 'code that must be built',
    ELAPSED_TIME: 'clears only as time passes',
    OWNER_DECISION: 'needs the owner to decide'
  };
  var DESK = '/api/command/bettor/desk/page';
  var LOCKED = 'Locked: open the desk and unlock COMMAND';
  var ENDPOINTS = {
    index: '/api/command/agents', derek: '/api/command/agents/derek',
    xavier: '/api/command/agents/xavier', audrey: '/api/command/agents/audrey',
    karen: '/api/command/karen',
    archer: '/api/command/archer', scout: '/api/command/scout',
    chat: '/api/command/agents/audrey/chat', directives: '/api/command/agents/audrey/directives'
  };
  var FETCH_OPTS = {credentials: 'same-origin', cache: 'no-store', headers: {'Accept': 'application/json'}};
  var LINK_KEYS = ['entry_intent_id', 'portfolio_group_id', 'xavier_decision_id', 'task_id', 'candidate_id', 'directive_id', 'decision_ref'];

  function esc(v) {
    return String(v === null || v === undefined ? '' : v).replace(/[&<>"']/g, function (c) {
      return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
    });
  }
  function isObj(v) { return v !== null && typeof v === 'object' && !Array.isArray(v); }
  function isScalar(v) { return v === null || (typeof v !== 'object'); }
  function has(o, k) { return isObj(o) && Object.prototype.hasOwnProperty.call(o, k) && o[k] !== undefined; }
  function pickKey(o, keys) { if (!isObj(o)) return null; for (var i = 0; i < keys.length; i++) if (has(o, keys[i])) return keys[i]; return null; }
  function pick(o, keys) { var k = pickKey(o, keys); return k === null ? undefined : o[k]; }
  function pad2(n) { return (n < 10 ? '0' : '') + n; }
  function unk(why) { return '<em class="unk" title="' + esc(why || 'the record does not carry this value; unknown is not zero') + '">UNKNOWN</em>'; }

  function toEpoch(v) {
    if (typeof v === 'number' && isFinite(v)) return v > 1e12 ? v / 1000 : v;
    if (typeof v === 'string' && /^\d{4}-\d{2}-\d{2}/.test(v)) { var t = Date.parse(v); return isNaN(t) ? null : t / 1000; }
    return null;
  }
  function dur(s) {
    if (s === null || s === undefined || !isFinite(s)) return null;
    var a = Math.abs(s);
    if (a < 1) return (Math.round(a * 1000)) + ' ms';
    if (a < 90) return (a < 10 ? a.toFixed(1) : Math.round(a)) + ' s';
    if (a < 5400) return Math.round(a / 60) + ' min';
    if (a < 172800) { var h = Math.floor(a / 3600), m = Math.round((a - h * 3600) / 60); return h + ' h' + (m ? ' ' + m + ' min' : ''); }
    return (a / 86400).toFixed(1) + ' d';
  }
  function ts(v, rd) {
    var e = toEpoch(v);
    if (e === null) return esc(v);
    var iso = new Date(e * 1000).toISOString();
    var out = '<time datetime="' + esc(iso) + '" title="' + esc(iso) + '">' + iso.slice(0, 10) + ' ' + iso.slice(11, 19) + 'Z</time>';
    if (typeof rd === 'number') {
      var d = rd - e;
      out += ' <span class="age">(' + (d >= 0 ? dur(d) + ' before read' : 'in ' + dur(-d)) + ')</span>';
    }
    return out;
  }
  function money(v, signed) {
    var a = Math.abs(v), s = a.toFixed(2);
    if (a !== 0 && a < 1 && Math.abs(a - +s) > 1e-9) s = a.toFixed(4).replace(/0+$/, '');
    if (v < 0) return '<span class="neg">−$' + s + '</span>';
    if (signed && v > 0) return '<span class="pos">+$' + s + '</span>';
    return '$' + s;
  }
  var SIGNED_RX = /(pnl|net|(^|_)ev($|_)|increment|contribution|reali[sz]ed|unreali[sz]ed|delta|change|diff|worst|best|profit|loss)/;
  function numFmt(k, v, unit) {
    if (unit === 'pp' || /(^|_)pp$|edge_pp/.test(k)) return (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v).toFixed(2) + ' pp';
    if (unit === 'usd' || (!unit && /(_usd$|^usd$|_usd_|_dollars$)/.test(k))) return money(v, unit === 'usd' || SIGNED_RX.test(k));
    if (unit === 'usd0') return money(v, false);
    if (unit === 'price' || (!unit && /(^|_)(price|ask|bid)$/.test(k))) return '$' + v.toFixed(v < 1 ? 3 : 2).replace(/0$/, '');
    if (unit === 'pct' || (!unit && /(_pct$|percent)/.test(k))) return v.toFixed(1) + '%';
    if (unit === 'roi') return (/_pct$/.test(k) ? v : v * 100).toFixed(1) + '%';
    if (!unit && /(^|_)(roi|return|hit_rate|rate)$/.test(k)) return (v * 100).toFixed(1) + '%';
    if (unit === 'prob' || (!unit && /(prob|(^|_)p_|^p$|likelihood)/.test(k))) return v.toFixed(3);
    if (unit === 's' || (!unit && /(_s|_seconds|_secs|elapsed|_age)$/.test(k))) return dur(v) || esc(v);
    if (!unit && /_ms$/.test(k)) return Math.round(v) + ' ms';
    if (!unit && /_h$|_hours$/.test(k)) return v.toFixed(1) + ' h';
    if (unit === 'qty' || /(qty|contracts|count|runs|errors|^n$|_n$|total|passes|size)/.test(k)) return Number.isInteger(v) ? v.toLocaleString('en-US') : String(+v.toFixed(4));
    return Number.isInteger(v) ? v.toLocaleString('en-US') : String(+v.toFixed(4));
  }
  function isTimeKey(k) { return /(_at|_ts|_time|timestamp|_until|_since|^at|^ts|^time)$/.test(k); }
  function safeHref(h) {
    return (typeof h === 'string' && /^\/(?!\/)/.test(h) && !/[\s"'<>\\]/.test(h)) ? h : null;
  }
  function stateBadge(s, big) {
    if (s === null || s === undefined || s === '') return '<span class="sb sb-NONE' + (big ? ' big' : '') + '"><i></i>NO STATUS RECORDED</span>';
    if (STATES.indexOf(s) < 0) return '<span class="sb sb-UNRECOGNISED' + (big ? ' big' : '') + '" title="not one of the eight recorded states"><i></i>UNRECOGNISED: ' + esc(s) + '</span>';
    return '<span class="sb sb-' + s + (big ? ' big' : '') + '" data-state="' + s + '"><i></i>' + s.replace(/_/g, ' ') + '</span>';
  }
  function dep(c, reason) {
    var cls = DEP_CLASSES.indexOf(c) >= 0 ? c : 'OTHER';
    return '<span class="dep dep-' + cls + '" title="' + esc(DEP_WHAT[c] || 'dependency class not one of the five') + '">' + esc(c || 'UNCLASSIFIED') + '</span>' + (reason ? ' <span>' + esc(reason) + '</span>' : '');
  }
  function fmt(key, v, rd, unit) {
    var k = String(key || '').toLowerCase();
    if (v === null || v === undefined) return unk();
    if (typeof v === 'boolean') return '<span class="bool">' + (v ? 'YES' : 'NO') + '</span>';
    if (k === 'href' || /_href$/.test(k)) { var h = safeHref(v); return h ? '<a href="' + esc(h) + '">' + esc(v) + '</a>' : esc(v); }
    if (k === 'evidence' && Array.isArray(v)) return evidence(v);
    if (typeof v === 'string' && STATES.indexOf(v) >= 0 && /state/.test(k)) return stateBadge(v);
    if (typeof v === 'string' && DEP_CLASSES.indexOf(v) >= 0) return dep(v);
    if (LINK_KEYS.indexOf(k) >= 0 && (typeof v === 'string' || typeof v === 'number')) return '<a class="trl" href="#trace=' + k + ':' + encodeURIComponent(String(v)) + '" title="trace the records linked to this id">' + esc(v) + '</a>';
    if (unit === 'ts' || isTimeKey(k)) { if (toEpoch(v) !== null) return ts(v, rd); }
    if (typeof v === 'number') return numFmt(k, v, unit);
    if (typeof v === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(v)) return ts(v, rd);
    if (Array.isArray(v)) {
      if (!v.length) return '<span class="none">empty list</span>';
      if (v.every(isScalar)) return v.map(function (x) { return fmt(key, x, rd, unit); }).join(', ');
      if (v.every(isObj) && v.length <= 6) return table(v, null, {rd: rd, compact: true});
      return '<code>' + esc(JSON.stringify(v).slice(0, 400)) + '</code>';
    }
    if (isObj(v)) {
      var ks = Object.keys(v);
      if (!ks.length) return '<span class="none">empty record</span>';
      if (ks.length <= 10) return '<div class="nest">' + kv(v, rd) + '</div>';
      return '<code>' + esc(JSON.stringify(v).slice(0, 400)) + '</code>';
    }
    return esc(v);
  }
  function evidence(list) {
    if (!Array.isArray(list) || !list.length) return '<span class="none">no evidence link recorded</span>';
    return '<span class="evs">' + list.map(function (e) {
      if (!isObj(e)) return '<span class="ev ev-nolink">' + esc(e) + '</span>';
      var label = (e.kind || 'record') + (e.id !== undefined && e.id !== null ? ' · ' + e.id : '');
      var h = safeHref(e.href);
      return h ? '<a class="ev" href="' + esc(h) + '">' + esc(label) + '</a>'
               : '<span class="ev ev-nolink" title="no same-origin reader link on this record">' + esc(label) + '</span>';
    }).join('') + '</span>';
  }
  function kv(o, rd, skip) {
    if (!isObj(o)) return fmt('', o, rd);
    var h = '<table class="kv"><tbody>';
    Object.keys(o).forEach(function (k) {
      if (skip && skip.indexOf(k) >= 0) return;
      h += '<tr><td>' + esc(k) + '</td><td>' + fmt(k, o[k], rd) + '</td></tr>';
    });
    return h + '</tbody></table>';
  }
  function rowsOf(d, keys) {
    if (Array.isArray(d)) return d;
    if (isObj(d)) { var v = pick(d, keys || ['rows', 'items']); if (Array.isArray(v)) return v; }
    return [];
  }
  function cell(row, c, rd) {
    if (c.render) return c.render(row, rd);
    var k = pickKey(row, c.keys);
    if (k === null) return unk('not in this record');
    return fmt(k, row[k], rd, c.u);
  }
  function table(rows, cols, opts) {
    opts = opts || {};
    rows = (rows || []).map(function (r) { return isObj(r) ? r : {value: r}; });
    if (!rows.length) return '<p class="emptyline">No rows.</p>';
    if (!cols) {
      var seen = [];
      rows.forEach(function (r) { Object.keys(r).forEach(function (k) { if (seen.indexOf(k) < 0 && k !== 'evidence') seen.push(k); }); });
      cols = seen.slice(0, 16).map(function (k) { return {label: k.replace(/_/g, ' '), keys: [k]}; });
      if (rows.some(function (r) { return has(r, 'evidence'); })) cols.push({label: 'evidence', keys: ['evidence']});
    }
    var h = '<div class="tbl"><table><thead>';
    if (cols.some(function (c) { return c.group; })) {
      h += '<tr class="grp">'; var i = 0;
      while (i < cols.length) {
        var g = cols[i].group || '', n = 1;
        while (i + n < cols.length && (cols[i + n].group || '') === g) n++;
        h += '<th colspan="' + n + '"' + (g ? ' class="g"' : '') + '>' + esc(g) + '</th>'; i += n;
      }
      h += '</tr>';
    }
    h += '<tr>' + cols.map(function (c) { return '<th' + (c.num ? ' class="num"' : '') + (c.title ? ' title="' + esc(c.title) + '"' : '') + '>' + esc(c.label) + '</th>'; }).join('') + '</tr></thead><tbody>';
    rows.forEach(function (r) {
      var sel = opts.selected && opts.selected(r);
      h += '<tr' + (sel ? ' class="sel"' : '') + '>' + cols.map(function (c, j) {
        return '<td' + (c.num ? ' class="num"' : '') + '>' + cell(r, c, opts.rd) + (sel && j === 0 ? '<span class="selbadge">SELECTED</span>' : '') + '</td>';
      }).join('') + '</tr>';
    });
    return h + '</tbody></table></div>';
  }
  function statusPill(st) { return '<span class="pill st-' + esc(st) + '">' + esc(st) + '</span>'; }
  var SECTION_STATUSES = ['OK', 'EMPTY', 'UNAVAILABLE'];
  function normSection(s, key, url) {
    if (s === undefined) return {status: 'MISSING', why: 'GET ' + url + " returned no '" + key + "' section.", data: null, evidence: [], missing: true};
    if (!isObj(s)) return {status: 'UNRECOGNISED', why: 'section is not an object', data: s, evidence: []};
    var st = SECTION_STATUSES.indexOf(s.status) >= 0 ? s.status : 'UNRECOGNISED';
    return {status: st, why: s.why === undefined ? null : s.why, data: s.data, evidence: Array.isArray(s.evidence) ? s.evidence : [], raw: s, rawStatus: s.status};
  }
  function normList(x, label) {
    if (x === undefined) return {status: 'MISSING', why: 'the index did not return ' + label, data: null, evidence: []};
    if (isObj(x) && SECTION_STATUSES.indexOf(x.status) >= 0) return normSection(x, label, ENDPOINTS.index);
    if (Array.isArray(x)) return {status: x.length ? 'OK' : 'EMPTY', why: x.length ? null : 'the index returned an empty list of ' + label, data: x, evidence: []};
    if (isObj(x)) return {status: 'OK', why: null, data: x, evidence: []};
    return {status: 'UNRECOGNISED', why: 'unexpected shape', data: x, evidence: []};
  }
  function genericBody(sec, rd) {
    var d = sec.data;
    if (d === null || d === undefined) return '<p class="emptyline">No data carried.</p>';
    if (Array.isArray(d)) return d.length ? table(d, null, {rd: rd}) : '<p class="emptyline">Empty list.</p>';
    if (isObj(d)) {
      var scal = {}, h = '', anyScal = false;
      Object.keys(d).forEach(function (k) {
        var v = d[k];
        if (Array.isArray(v) && v.length && v.every(isObj)) h += '<p class="lbl" style="margin:12px 0 6px">' + esc(k.replace(/_/g, ' ')) + '</p>' + table(v, null, {rd: rd});
        else { scal[k] = v; anyScal = true; }
      });
      return (anyScal ? kv(scal, rd) : '') + h;
    }
    return '<p>' + fmt('', d, rd) + '</p>';
  }
  function card(n, spec, sec, body, rd) {
    var st = sec.status;
    var why = sec.why ? '<p class="why"><span>' + (st === 'UNAVAILABLE' ? 'Reason' : 'Why') + '</span>' + esc(sec.why) + '</p>'
      : (st !== 'OK' ? '<p class="why"><span>Why</span>' + unk('the record named no reason') + '</p>' : '');
    var raw = sec.missing ? '' : '<details class="raw"><summary>record</summary><pre>' + esc(JSON.stringify(sec.raw === undefined ? sec : sec.raw, null, 2)) + '</pre></details>';
    return '<section class="card st-card-' + st + (spec.wide ? ' wide' : '') + '" id="s-' + esc(spec.key) + '" data-section="' + esc(spec.key) + '" data-status="' + esc(st) + '">'
      + '<header><span class="idx">' + pad2(n) + '</span><div class="ttl"><h3>' + esc(spec.title) + '</h3><code class="key">' + esc(spec.key) + '</code></div>' + statusPill(st === 'UNRECOGNISED' ? 'UNRECOGNISED' : st) + '</header>'
      + why + (spec.note ? '<p class="note">' + spec.note + '</p>' : '')
      + '<div class="body">' + body + '</div>'
      + '<footer>' + evidence(sec.evidence) + raw + '</footer></section>';
  }
  function sectionBody(spec, sec, ctx) {
    try {
      if (sec.status === 'OK') return spec.render ? spec.render(sec, ctx) : genericBody(sec, ctx.rd);
      if (sec.status === 'EMPTY') return spec.empty ? spec.empty(sec, ctx) : '<p class="emptyline">Nothing is recorded here yet. This is not a result.</p>';
      if (sec.status === 'UNAVAILABLE') return '<p class="unav">The read failed; nothing is shown in its place.</p>';
      if (sec.status === 'MISSING') return '<p class="emptyline">This build of the endpoint does not return the section. Nothing is shown in its place.</p>';
      return genericBody(sec, ctx.rd);
    } catch (e) {
      return '<p class="note">This record has a shape the specialised view could not read (' + esc(e && e.name) + '); it is shown as recorded.</p>' + genericBody(sec, ctx.rd);
    }
  }

  // ── agent identity + status hero ─────────────────────────────────
  var AGENT_META = {
    DEREK: {letter: 'D', role: 'Discovery · entry', kind: 'derek'},
    XAVIER: {letter: 'X', role: 'Position management · exits', kind: 'xavier'},
    AUDREY: {letter: 'A', role: 'Audit · management chat · improvement', kind: 'audrey'},
    KAREN: {letter: 'K', role: 'Red team · challenge · no authority', kind: 'karen'},
    ARCHER: {letter: 'R', role: 'Head of execution · shadow only', kind: 'archer'},
    SCOUT: {letter: 'S', role: 'Market intelligence · research only', kind: 'scout'}
  };
  function depList(v) {
    if (v === null || v === undefined) return '';
    var list = Array.isArray(v) ? v : (isObj(v) ? Object.keys(v).map(function (k) { var x = v[k]; return isObj(x) ? Object.assign({name: k}, x) : {name: k, detail: x}; }) : [v]);
    if (!list.length) return '<span class="none">none recorded</span>';
    return '<div class="blk">' + list.map(function (x) {
      if (!isObj(x)) return '<div>' + esc(x) + '</div>';
      var c = pick(x, ['dependency_class', 'class', 'dependency', 'kind']);
      var r = pick(x, ['reason', 'why', 'name', 'detail', 'code', 'blocker', 'waiting_on']);
      return '<div>' + (c ? dep(c) : '') + ' <span>' + esc(typeof r === 'object' ? JSON.stringify(r) : r) + '</span></div>';
    }).join('') + '</div>';
  }
  function cadenceText(c, rd) {
    if (c === null || c === undefined) return unk('no cadence recorded');
    if (!isObj(c)) return fmt('cadence', c, rd);
    var m = pick(c, ['measured_interval_s', 'measured_s', 'median_interval_s', 'interval_s', 'p50_interval_s']);
    var t = pick(c, ['target_interval_s', 'expected_interval_s', 'target_s']);
    if (typeof m === 'number') return 'every ' + dur(m) + (typeof t === 'number' ? ' <span class="s">(target ' + dur(t) + ')</span>' : '') + ' <span class="s">measured</span>';
    return '<div class="nest">' + kv(c, rd) + '</div>';
  }
  function versionChips(o) {
    if (!isObj(o)) return '';
    return '<div class="chips">' + [['policy', 'policy_version'], ['model', 'model_version'], ['code', 'code_version']].map(function (p) {
      return '<span class="chip">' + p[0] + ' <b>' + (has(o, p[1]) && o[p[1]] !== null ? esc(o[p[1]]) : 'UNKNOWN') + '</b>' + (p[1] === 'policy_version' && o[p[1]] === 'CODE_DEFAULT' ? ' <span class="s">code fallback, not approved</span>' : '') + '</span>';
    }).join('') + (isObj(o.management_policy) ? '<span class="chip">management policy <b>' + esc(o.management_policy.policy_id) + ' v' + esc(o.management_policy.version) + '</b> <span class="s">' + esc(o.management_policy.status) + (o.management_policy.activated ? '' : ' · not activated') + ' · sha256 ' + esc(String(o.management_policy.sha256 || '').slice(0, 12)) + '</span></span>' : '') + '</div>';
  }
  function hero(a, rd, id) {
    a = isObj(a) ? a : {};
    var meta = AGENT_META[id] || {letter: '?', role: ''};
    var hbAt = toEpoch(a.last_heartbeat_at);
    var hb = hbAt === null ? unk('no heartbeat recorded') : dur(Math.max(0, (rd || hbAt) - hbAt)) + ' <span class="s">ago</span>';
    var lastRun = a.last_run_finished_at !== undefined && a.last_run_finished_at !== null ? ts(a.last_run_finished_at, rd) : unk('no finished run recorded');
    var activity = a.activity;
    return '<div class="hero">'
      + '<div class="panel"><div class="ident"><div class="sigil" aria-hidden="true">' + meta.letter + '</div><div>'
      + '<div class="role">' + esc(meta.role) + '</div><h1>' + esc(a.display_name || (id.charAt(0) + id.slice(1).toLowerCase())) + '</h1>'
      + versionChips(a) + '<p class="mandate">' + (a.mandate ? esc(a.mandate) : unk('no identity row: mandate not recorded')) + '</p></div></div>'
      + '<div class="contemplating"><span class="lbl">Currently contemplating · persisted evaluation state</span>'
      + '<p>' + (activity ? esc(activity) : '<span class="mute">No activity is recorded on agent_status. Nothing is inferred in its place.</span>') + '</p>'
      + (a.waiting_on ? '<p class="lbl" style="margin-top:10px">Waiting on</p>' + depList(a.waiting_on) : '')
      + (a.dependencies ? '<p class="lbl" style="margin-top:10px">Dependencies</p>' + depList(a.dependencies) : '')
      + '</div></div>'
      + '<div class="panel"><div class="statehead"><span class="lbl">Recorded state</span>' + stateBadge(a.state, true) + '</div>'
      + '<div class="metrics">'
      + '<div class="metric"><div class="lbl">Last heartbeat</div><div class="v">' + hb + '</div></div>'
      + '<div class="metric"><div class="lbl">Measured cadence</div><div class="v">' + cadenceText(a.cadence, rd) + '</div></div>'
      + '<div class="metric"><div class="lbl">Last run finished</div><div class="v" style="font-size:13px">' + lastRun + '</div></div>'
      + '<div class="metric"><div class="lbl">Runs</div><div class="v">' + (typeof a.runs === 'number' ? a.runs.toLocaleString('en-US') : unk()) + '</div></div>'
      + '<div class="metric"><div class="lbl">Errors</div><div class="v">' + (typeof a.errors === 'number' ? a.errors.toLocaleString('en-US') : unk()) + '</div></div>'
      + '<div class="metric"><div class="lbl">Last run elapsed</div><div class="v">' + (typeof a.last_run_elapsed_s === 'number' ? dur(a.last_run_elapsed_s) : unk()) + '</div></div>'
      + '</div>'
      + (a.last_error ? '<p class="why" style="margin-top:12px"><span>Last error</span>' + esc(a.last_error) + '</p>' : '')
      + (a.tool_permissions ? '<p class="lbl" style="margin:12px 0 6px">Tool permissions</p>' + fmt('tool_permissions', a.tool_permissions, rd) : '')
      + '</div></div>';
  }

  // ── fetch outcomes ───────────────────────────────────────────────
  async function load(url, f) {
    var r;
    try { r = await f(url, FETCH_OPTS); }
    catch (e) { return {kind: 'UNAVAILABLE', why: 'network error: ' + ((e && e.name) || 'Error')}; }
    // 401 (no session) and 403 (expired or wrong scope) both mean: sign in
    if (r.status === 401 || r.status === 403) return {kind: 'LOCKED', status: r.status};
    if (r.status === 404) return {kind: 'NOT_DEPLOYED', status: 404};
    if (!r.ok) {
      var why = 'HTTP ' + r.status;
      try { var j = await r.json(); if (j && j.detail) why += ' · ' + (typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)); } catch (_) {}
      return {kind: 'UNAVAILABLE', status: r.status, why: why};
    }
    try { return {kind: 'OK', status: r.status, json: await r.json()}; }
    catch (e) { return {kind: 'UNAVAILABLE', status: r.status, why: 'the response was not JSON'}; }
  }
  function gate(o, url) {
    if (o.kind === 'LOCKED') return '<div class="gate locked" data-gate="LOCKED"><h2>' + LOCKED + '</h2><p>The COMMAND read credential is missing or expired, so GET <code>' + esc(url) + '</code> answered 401. Nothing is shown in place of the records.</p><p><a class="btn primary" href="' + DESK + '">Open the desk</a></p></div>';
    if (o.kind === 'NOT_DEPLOYED') return '<div class="gate nd" data-gate="NOT_DEPLOYED"><h2>Not deployed in this build</h2><p>GET <code>' + esc(url) + '</code> returned 404: this service does not serve these records yet. Nothing is shown in their place.</p></div>';
    return '<div class="gate unav" data-gate="UNAVAILABLE"><h2>UNAVAILABLE</h2><p>GET <code>' + esc(url) + '</code> failed: ' + esc(o.why || 'unknown error') + '. Nothing is shown in place of the records.</p></div>';
  }

  // ── writes: the service decides; the page only says what is needed ─
  async function write(url, f, body) {
    var r;
    try { r = await f(url, {method: 'POST', credentials: 'same-origin', cache: 'no-store', headers: {'Content-Type': 'application/json', 'Accept': 'application/json'}, body: JSON.stringify(body || {})}); }
    catch (e) { return {kind: 'UNAVAILABLE', text: 'Not sent: network error (' + ((e && e.name) || 'Error') + ').'}; }
    var j = null; try { j = await r.json(); } catch (_) {}
    if (r.status === 401 || r.status === 403) return {kind: 'NEEDS_OPERATOR', status: r.status, json: j, text: 'Refused (' + r.status + '): this write needs the OPERATOR credential. Sign in on the desk with the operator password; the read credential cannot confirm, cancel or approve.'};
    if (r.status === 404 || r.status === 405) return {kind: 'NOT_DEPLOYED', status: r.status, json: j, text: 'This write route is not deployed in this build (' + r.status + ').'};
    if (!r.ok) return {kind: 'REFUSED', status: r.status, json: j, text: 'Refused by the service: HTTP ' + r.status + (j && j.detail ? ' · ' + (typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)) : '') + '.'};
    return {kind: 'OK', status: r.status, json: j, text: 'Recorded by the service' + (j && (j.state || j.status) ? ': ' + (j.state || j.status) : '') + '.'};
  }
  function writeButton(label, href, extra) {
    var h = safeHref(href);
    if (!h) return '<button type="button" disabled title="this record advertises no write route">' + esc(label) + '</button>';
    return '<button type="button" data-write="' + esc(h) + '"' + (extra ? ' data-body="' + esc(JSON.stringify(extra)) + '"' : '') + '>' + esc(label) + '</button>';
  }
  function writeRow(buttons) {
    return '<div class="wr">' + buttons.join('') + '<span class="needs">requires the OPERATOR credential</span><span class="wmsg" data-wmsg></span></div>';
  }

  var SPECS = {};
  function workspace(kind, json) {
    var spec = SPECS[kind], url = ENDPOINTS[kind];
    json = isObj(json) ? json : {};
    var rd = toEpoch(json.read_at);
    var sections = isObj(json.sections) ? json.sections : {};
    var ctx = {rd: rd, json: json, sections: sections, url: url, kind: kind};
    var h = hero(json.agent, rd, kind.toUpperCase());
    if (json.read_only !== true) h += '<p class="why"><span>Note</span>The endpoint did not declare read_only: true.</p>';
    var known = spec.sections.map(function (s) { return s.key; });
    var extra = Object.keys(sections).filter(function (k) { return known.indexOf(k) < 0; });
    var all = spec.sections.concat(extra.map(function (k) { return {key: k, title: k.replace(/_/g, ' '), wide: true}; }));
    var norm = all.map(function (s) { return normSection(sections[s.key], s.key, url); });
    h += '<nav class="secnav" aria-label="sections">' + all.map(function (s, i) {
      return '<a href="#s-' + esc(s.key) + '"><span class="dot dot-' + norm[i].status + '"></span>' + esc(s.key) + '</a>';
    }).join('') + '</nav><div class="grid">';
    all.forEach(function (s, i) { h += card(i + 1, s, norm[i], sectionBody(s, norm[i], ctx), rd); });
    return h + '</div>';
  }
  function readLine(json) {
    var e = isObj(json) ? toEpoch(json.read_at) : null;
    return e === null ? 'read time not reported' : 'read ' + new Date(e * 1000).toISOString().replace('T', ' ').slice(0, 19) + 'Z';
  }

  return {LINK_KEYS: LINK_KEYS, STATES: STATES, DEP_CLASSES: DEP_CLASSES, DEP_WHAT: DEP_WHAT, DESK: DESK, LOCKED: LOCKED, ENDPOINTS: ENDPOINTS, FETCH_OPTS: FETCH_OPTS,
    esc: esc, isObj: isObj, has: has, pick: pick, pickKey: pickKey, unk: unk, toEpoch: toEpoch, dur: dur, ts: ts, money: money, fmt: fmt,
    safeHref: safeHref, stateBadge: stateBadge, dep: dep, depList: depList, evidence: evidence, kv: kv, rowsOf: rowsOf, table: table,
    statusPill: statusPill, normSection: normSection, normList: normList, genericBody: genericBody, card: card, hero: hero,
    versionChips: versionChips, cadenceText: cadenceText, load: load, gate: gate, write: write, writeButton: writeButton, writeRow: writeRow,
    SPECS: SPECS, workspace: workspace, readLine: readLine, AGENT_META: AGENT_META, pad2: pad2};
})();
"""

# Renderers shared by all three agents: status and versions.
COMMON_JS = r"""
(function (AG) {
  'use strict';
  var esc = AG.esc, pick = AG.pick, isObj = AG.isObj;
  AG.R = AG.R || {};
  AG.R.status = function (sec, ctx) {
    var d = sec.data;
    if (!isObj(d)) return AG.genericBody(sec, ctx.rd);
    var h = '<div class="statehead" style="margin-bottom:10px"><span class="lbl">State</span>' + AG.stateBadge(d.state) + '</div>';
    var rest = {};
    Object.keys(d).forEach(function (k) { if (['state', 'waiting_on', 'dependencies'].indexOf(k) < 0) rest[k] = d[k]; });
    h += AG.kv(rest, ctx.rd);
    if (d.waiting_on !== undefined) h += '<p class="lbl" style="margin:12px 0 6px">Waiting on</p>' + AG.depList(d.waiting_on);
    if (d.dependencies !== undefined) h += '<p class="lbl" style="margin:12px 0 6px">Dependencies</p>' + AG.depList(d.dependencies);
    return h;
  };
  AG.R.versions = function (sec, ctx) {
    var d = sec.data, h = '';
    if (isObj(d)) {
      h += AG.versionChips(isObj(d.identity) ? d.identity : d);
      var list = pick(d, ['policies', 'policy_versions', 'rows', 'history']);
      var rest = {};
      Object.keys(d).forEach(function (k) { if (['policy_version', 'model_version', 'code_version', 'identity', 'policies', 'policy_versions', 'rows', 'history'].indexOf(k) < 0) rest[k] = d[k]; });
      if (Object.keys(rest).length) h += '<div style="margin-top:10px">' + AG.kv(rest, ctx.rd) + '</div>';
      if (Array.isArray(list)) h += '<p class="lbl" style="margin:12px 0 6px">Policy versions</p>' + AG.table(list, [
        {label: 'policy', keys: ['policy_key', 'key', 'name']}, {label: 'version', keys: ['version']},
        {label: 'state', keys: ['state']}, {label: 'created by', keys: ['created_by']},
        {label: 'approved by', keys: ['approved_by']}, {label: 'approved', keys: ['approved_at']}], {rd: ctx.rd});
      return h;
    }
    return AG.genericBody(sec, ctx.rd);
  };
  AG.R.timeline = function (sec, ctx) {
    var rows = AG.rowsOf(sec.data, ['events', 'rows', 'items', 'history']);
    if (!rows.length) return AG.genericBody(sec, ctx.rd);
    var tk = ['at', 'occurred_at', 'recorded_at', 'ts', 'created_at', 'decided_at', 'filled_at'];
    rows = rows.slice().sort(function (a, b) { return (AG.toEpoch(pick(b, tk)) || 0) - (AG.toEpoch(pick(a, tk)) || 0); });
    return '<ol class="tl">' + rows.slice(0, 60).map(function (r) {
      if (!isObj(r)) return '<li>' + esc(r) + '</li>';
      var t = pick(r, tk), k = pick(r, ['kind', 'event', 'type', 'action', 'state']);
      var rest = {};
      Object.keys(r).forEach(function (x) { if (tk.indexOf(x) < 0 && ['kind', 'event', 'type', 'evidence'].indexOf(x) < 0) rest[x] = r[x]; });
      return '<li><div class="h"><span class="k">' + esc(k === undefined ? 'event' : k) + '</span>' + (t !== undefined ? AG.ts(t, ctx.rd) : AG.unk('no time on this event')) + '</div>'
        + (Object.keys(rest).length ? '<div class="nest">' + AG.kv(rest, ctx.rd) + '</div>' : '') + (r.evidence ? AG.evidence(r.evidence) : '') + '</li>';
    }).join('') + '</ol>' + (rows.length > 60 ? '<p class="note">' + (rows.length - 60) + ' earlier events are in the record.</p>' : '');
  };
  AG.R.blockers = function (row) {
    var list = pick(row, ['blockers', 'blocked_by']);
    var one = pick(row, ['blocker', 'refusal', 'blocking_reason']);
    if (!Array.isArray(list)) list = one !== undefined && one !== null ? [one] : [];
    if (!list.length) return '<span class="none">none recorded</span>';
    return '<div class="blk">' + list.map(function (b) {
      if (!isObj(b)) return '<div>' + esc(b) + '</div>';
      return '<div>' + AG.dep(pick(b, ['dependency_class', 'class', 'dependency'])) + ' <span>' + esc(pick(b, ['reason', 'code', 'blocker', 'why', 'name'])) + '</span></div>';
    }).join('') + '</div>';
  };
  // ── decision limitations: search completeness, policy, shadow ─────
  function reasoningOf(o) { return isObj(o) && isObj(o.reasoning) ? o.reasoning : null; }
  function ladderOf(o) { var r = reasoningOf(o); return r && isObj(r.xavier_ladder) ? r.xavier_ladder : (isObj(o) && isObj(o.xavier_ladder) ? o.xavier_ladder : null); }
  function firstDef(list) { for (var i = 0; i < list.length; i++) if (list[i] !== undefined && list[i] !== null) return list[i]; return undefined; }
  function inner(o) { return isObj(o) ? firstDef([o.decision, o.audited_decision, o.xavier_decision]) : undefined; }
  AG.R.searchOf = function (o) {
    if (!isObj(o)) return undefined;
    var l = ladderOf(o);
    var v = firstDef([l && l.search_completeness, o.search_completeness]);
    if (v === undefined && isObj(inner(o))) return AG.R.searchOf(inner(o));
    return v;
  };
  function count(x) { if (Array.isArray(x)) return x.length; if (typeof x === 'number') return x; if (isObj(x)) return typeof x.count === 'number' ? x.count : Object.keys(x).length; return null; }
  AG.R.searchInfo = function (s) {
    if (!isObj(s)) return {recorded: false, label: 'search completeness not recorded'};
    var disc = count(pick(s, ['discovered', 'discovered_count', 'contracts_discovered'])), exam = count(pick(s, ['examined', 'examined_count', 'contracts_examined']));
    var excl = pick(s, ['excluded', 'excluded_with_reasons', 'exclusions']), unex = pick(s, ['unexamined', 'unexamined_count', 'not_examined']);
    var ue = count(unex), complete = null;
    var flag = pick(s, ['complete', 'is_complete']);
    if (typeof flag === 'boolean') complete = flag;
    else if (ue === 0) complete = true; else if (typeof ue === 'number' && ue > 0) complete = false;
    else if (typeof disc === 'number' && typeof exam === 'number') complete = exam >= disc;
    var n = typeof exam === 'number' ? exam : '?', m = typeof disc === 'number' ? disc : '?';
    var label = complete === true ? 'search complete: all ' + m + ' discovered contracts examined' : 'best among examined (' + n + ' of ' + m + ')';
    return {recorded: true, complete: complete, discovered: disc, examined: exam, excluded: excl, unexamined: unex, unexaminedCount: ue,
      ended: pick(s, ['why_ended', 'ended_because', 'end_reason', 'stop_reason', 'search_ended', 'ended']), label: label};
  };
  function reasonList(x, rd) {
    if (x === undefined || x === null) return AG.unk('not recorded');
    if (typeof x === 'number') return x.toLocaleString('en-US');
    if (Array.isArray(x)) {
      if (!x.length) return '<span class="none">none</span>';
      return '<ul style="margin:4px 0 0;padding-left:16px">' + x.slice(0, 12).map(function (e) {
        if (!isObj(e)) return '<li>' + esc(e) + '</li>';
        return '<li><span class="mono">' + esc(pick(e, ['contract', 'market', 'line', 'slug', 'name', 'id'])) + '</span>' + (pick(e, ['reason', 'why']) !== undefined ? ' — ' + esc(pick(e, ['reason', 'why'])) : '') + '</li>';
      }).join('') + (x.length > 12 ? '<li class="mute">' + (x.length - 12) + ' more in the record</li>' : '') + '</ul>';
    }
    if (isObj(x)) return '<ul style="margin:4px 0 0;padding-left:16px">' + Object.keys(x).slice(0, 12).map(function (k) { return '<li><span class="mono">' + esc(k) + '</span> — ' + esc(typeof x[k] === 'object' ? JSON.stringify(x[k]) : x[k]) + '</li>'; }).join('') + '</ul>';
    return esc(x);
  }
  AG.R.searchChip = function (o) {
    var i = AG.R.searchInfo(AG.R.searchOf(o));
    return '<span class="sc sc-' + (!i.recorded ? 'none' : i.complete === true ? 'full' : 'part') + '" data-search="' + (!i.recorded ? 'NOT_RECORDED' : i.complete === true ? 'COMPLETE' : 'INCOMPLETE') + '">' + esc(i.label) + '</span>';
  };
  AG.R.searchPanel = function (o, rd) {
    var i = AG.R.searchInfo(AG.R.searchOf(o));
    if (!i.recorded) return '<div class="scp">' + AG.R.searchChip(o) + '<span class="note" style="margin:0">The record does not say how many contracts were discovered or examined, so the selection is only known to be the best among the contracts it examined.</span></div>';
    return '<div class="scp">' + AG.R.searchChip(o) + '<div class="scgrid">'
      + '<div><div class="lbl">Discovered</div><div class="v">' + (i.discovered === null ? AG.unk() : i.discovered) + '</div></div>'
      + '<div><div class="lbl">Examined</div><div class="v">' + (i.examined === null ? AG.unk() : i.examined) + '</div></div>'
      + '<div><div class="lbl">Excluded, with reasons</div><div>' + reasonList(i.excluded, rd) + '</div></div>'
      + '<div><div class="lbl">Unexamined</div><div>' + reasonList(i.unexamined, rd) + '</div></div>'
      + '<div><div class="lbl">Why the search ended</div><div>' + (i.ended !== undefined ? esc(typeof i.ended === 'object' ? JSON.stringify(i.ended) : i.ended) : AG.unk('not recorded')) + '</div></div></div></div>';
  };
  AG.R.policyOf = function (o) {
    if (!isObj(o)) return undefined;
    var r = reasoningOf(o), l = ladderOf(o);
    var v = firstDef([isObj(o.selected_policy) ? o.selected_policy : undefined, isObj(o.policy) ? o.policy : undefined, r && r.selected_policy, r && r.policy, l && l.selected_policy, l && l.policy]);
    if (v === undefined && isObj(inner(o))) return AG.R.policyOf(inner(o));
    return v;
  };
  AG.R.policyText = function (p) {
    if (p === undefined || p === null) return AG.unk('selected policy not recorded');
    if (!isObj(p)) return esc(p);
    return '<span class="mono">' + esc(pick(p, ['policy_key', 'key', 'name']) || '?') + ' @ ' + esc(pick(p, ['version', 'policy_version']) || '?') + '</span> <span class="chip">' + esc(pick(p, ['source', 'state']) || 'source not recorded') + '</span>';
  };
  AG.R.shadowOf = function (o) {
    if (!isObj(o)) return undefined;
    var r = reasoningOf(o), l = ladderOf(o);
    var v = firstDef([o.shadow_comparison, r && r.shadow_comparison, l && l.shadow_comparison]);
    if (v === undefined && isObj(inner(o))) return AG.R.shadowOf(inner(o));
    return v;
  };
  AG.R.policyShadow = function (o, chosen, rd) {
    var p = AG.R.policyOf(o), sh = AG.R.shadowOf(o);
    var left = '<div class="pol"><div class="lbl">Selected policy · dispatched decision</div><div style="margin:6px 0">' + AG.R.policyText(p) + '</div><div>Chose <b>' + (chosen !== undefined && chosen !== null ? esc(chosen) : AG.unk('no action recorded')) + '</b></div></div>';
    var right;
    if (!isObj(sh)) right = '<div class="pol shadow"><div class="lbl">Shadow policy · not dispatched</div><p class="note" style="margin:6px 0 0">No shadow comparison is recorded on this decision.</p></div>';
    else {
      var sp = pick(sh, ['policy', 'shadow_policy', 'other_policy']);
      var would = pick(sh, ['would_choose', 'action', 'chosen_action', 'selected', 'would_select']);
      var ev = pick(sh, ['ev_given_up_usd', 'expected_value_given_up_usd', 'ev_delta_usd', 'ev_given_up']);
      var dn = pick(sh, ['downside_improved_usd', 'worst_case_improvement_usd', 'downside_delta_usd', 'downside_improved']);
      right = '<div class="pol shadow" data-shadow="NOT_DISPATCHED"><div class="lbl">Shadow policy · <b>NOT DISPATCHED</b></div><div style="margin:6px 0">' + AG.R.policyText(sp) + '</div>'
        + '<div>Would choose <b>' + (would !== undefined ? esc(typeof would === 'object' ? JSON.stringify(would) : would) : AG.unk()) + '</b> on the same frozen inputs</div>'
        + '<div class="tiles" style="grid-template-columns:repeat(2,minmax(0,1fr));margin:8px 0 0"><div><div class="lbl">EV given up</div><div class="v">' + (typeof ev === 'number' ? AG.money(ev, false) : AG.fmt('ev_given_up_usd', ev, rd)) + '</div></div><div><div class="lbl">Downside improved</div><div class="v">' + (typeof dn === 'number' ? AG.money(dn, false) : AG.fmt('downside_improved_usd', dn, rd)) + '</div></div></div></div>';
    }
    return '<div class="polcmp">' + left + right + '</div>';
  };
  AG.R.laterDiscovered = function (o) {
    if (!isObj(o)) return '';
    var v = pick(o, ['later_discovered', 'discovered_after_decision', 'later_discovered_contracts', 'contracts_discovered_later']);
    if (v === undefined && isObj(inner(o))) v = pick(inner(o), ['later_discovered', 'discovered_after_decision', 'later_discovered_contracts', 'contracts_discovered_later']);
    if (v === undefined || v === null || (Array.isArray(v) && !v.length)) return '';
    return '<div class="later" data-later="1"><span class="lbl">Not considered at decision time</span><div class="note" style="margin:2px 0 0">Contracts discovered after the decision; the decision could not have compared them.</div>' + reasonList(v) + '</div>';
  };
  AG.R.fillBar = function (ordered, filled) {
    if (typeof ordered !== 'number' || typeof filled !== 'number' || ordered <= 0) return AG.unk('ordered or filled quantity not in this record');
    var f = Math.max(0, Math.min(100, filled / ordered * 100));
    return '<div class="fbar" title="' + filled + ' filled of ' + ordered + ' ordered"><div class="f" style="width:' + f.toFixed(1) + '%"></div><div class="o" style="width:' + (100 - f).toFixed(1) + '%"></div></div>';
  };
})(AG);
"""

DEREK_JS = r"""
(function (AG) {
  'use strict';
  var esc = AG.esc, pick = AG.pick, isObj = AG.isObj, R = AG.R;
  function normCats(c) {
    var out = [];
    if (Array.isArray(c)) c.forEach(function (x) {
      if (!isObj(x)) return;
      out.push({name: pick(x, ['category', 'name', 'key', 'market_type', 'kind', 'label']), count: pick(x, ['count', 'n', 'markets', 'total', 'events', 'fixtures']),
        supported: pick(x, ['supported', 'is_supported']), reason: pick(x, ['reason', 'why', 'exclusion_reason'])});
    });
    else if (isObj(c)) Object.keys(c).forEach(function (k) {
      var v = c[k];
      if (typeof v === 'number' || v === null) out.push({name: k, count: v});
      else if (isObj(v)) out.push({name: k, count: pick(v, ['count', 'n', 'markets', 'total', 'events', 'fixtures']), supported: pick(v, ['supported', 'is_supported']), reason: pick(v, ['reason', 'why'])});
    });
    return out;
  }
  function coverage(sec, ctx) {
    var d = sec.data, h = '';
    if (!isObj(d)) return AG.genericBody(sec, ctx.rd);
    var ck = AG.pickKey(d, ['categories', 'census', 'by_category', 'market_types']);
    var cats = normCats(ck ? d[ck] : null);
    if (cats.length) {
      var max = Math.max.apply(null, cats.map(function (c) { return typeof c.count === 'number' ? c.count : 0; }).concat([1]));
      h += '<p class="lbl" style="margin:0 0 8px">Census by category</p><div class="bars">';
      cats.forEach(function (c) {
        var unsup = c.supported === false || /UNSUPPORTED|EXCLUDED|NOT_SUPPORTED|OUT_OF_SCOPE/i.test(String(c.name));
        h += '<div class="nm">' + esc(c.name) + (unsup ? '<span class="tag">UNSUPPORTED</span>' : '') + '</div>'
          + (typeof c.count === 'number' ? '<div class="track"><div class="fillb' + (unsup ? ' x' : '') + '" style="width:' + (c.count / max * 100).toFixed(1) + '%"></div></div><div class="num">' + c.count.toLocaleString('en-US') + '</div>'
            : '<div class="track"></div><div>' + AG.unk('count not recorded') + '</div>');
        if (c.reason) h += '<div></div><div class="note" style="margin:0;grid-column:span 2">' + esc(c.reason) + '</div>';
      });
      h += '</div>';
    }
    var ek = AG.pickKey(d, ['exclusions', 'excluded', 'unsupported_markets', 'unsupported']);
    if (ek) {
      var ex = d[ek];
      h += '<p class="lbl" style="margin:14px 0 6px">' + esc(ek.replace(/_/g, ' ')) + '</p>' + (Array.isArray(ex) && ex.length && ex.every(isObj) ? AG.table(ex, null, {rd: ctx.rd}) : AG.fmt(ek, ex, ctx.rd));
    }
    var rest = {};
    Object.keys(d).forEach(function (k) { if (k !== ck && k !== ek) rest[k] = d[k]; });
    if (Object.keys(rest).length) h += '<div style="margin-top:12px">' + AG.kv(rest, ctx.rd) + '</div>';
    return h;
  }
  function subscription(sec, ctx) {
    var d = sec.data;
    if (!isObj(d)) return AG.genericBody(sec, ctx.rd);
    var s = pick(d, ['health', 'state', 'status']);
    return (s !== undefined ? '<div class="statehead" style="margin-bottom:10px"><span class="lbl">Health</span><span class="chip"><b>' + esc(s) + '</b></span></div>' : '') + AG.kv(d, ctx.rd);
  }
  var OQ = [
    {label: 'Subject', keys: ['subject', 'market', 'fixture', 'market_slug', 'slug', 'event', 'title']},
    {label: 'Side', keys: ['side', 'outcome', 'selection']},
    {label: 'State', keys: ['state', 'evaluation_state', 'status']},
    {group: 'Pinnacle (reference)', label: 'Probability', keys: ['pinnacle_probability', 'pinnacle_prob', 'p_pinnacle', 'pinnacle_p', 'reference_probability'], u: 'prob', num: 1},
    {group: 'Pinnacle (reference)', label: 'Observed', keys: ['pinnacle_at', 'pinnacle_observed_at', 'reference_at', 'pinnacle_ts'], u: 'ts'},
    {group: 'Internal (model)', label: 'Probability', keys: ['internal_probability', 'internal_prob', 'p_internal', 'model_probability', 'model_prob'], u: 'prob', num: 1},
    {group: 'Internal (model)', label: 'Computed', keys: ['internal_at', 'model_at', 'internal_ts', 'internal_computed_at'], u: 'ts'},
    {group: 'Internal (model)', label: 'Qualification', keys: ['qualification', 'internal_qualification', 'probability_qualification', 'qualified']},
    {group: 'Policy (stored record)', label: 'Blended', keys: ['p_blended'], u: 'prob', num: 1, title: 'V2: (internal + Pinnacle) / 2, read from the stored decision; an average of two market-derived estimates, not independent confirmation'},
    {group: 'Policy (stored record)', label: 'Policy', keys: ['policy_name']},
    {group: 'Policy (stored record)', label: 'Rationale', keys: ['rationale']},
    {group: 'Economics · four separate quantities', label: 'Price', keys: ['price', 'ask', 'entry_price', 'price_usd'], u: 'price', num: 1},
    {group: 'Economics · four separate quantities', label: 'Gross edge', keys: ['gross_edge_pp', 'edge_pp', 'gross_edge'], u: 'pp', num: 1, title: 'the policy probability (V2: the blended average) minus price, in percentage points'},
    {group: 'Economics · four separate quantities', label: 'Net EV / contract', keys: ['net_ev_usd', 'net_ev_per_contract_usd', 'net_ev', 'ev_net_usd'], u: 'usd', num: 1, title: 'expected value per contract after fees'},
    {group: 'Economics · four separate quantities', label: 'ROI', keys: ['roi', 'net_roi', 'roi_pct', 'net_roi_pct'], u: 'roi', num: 1, title: 'net EV divided by capital at risk'},
    {group: 'Sizing', label: 'Contracts', keys: ['size_qty', 'sizing_qty', 'sized_qty', 'contracts', 'qty', 'sizing'], u: 'qty', num: 1, title: 'size permitted by the risk policy; not a function of edge alone'},
    {label: 'Blockers', render: R.blockers},
    {label: 'Evidence', keys: ['evidence']}
  ];
  var ECON = '<div class="econ"><div><b>Gross edge · pp</b>blended probability − price (V2)</div><div><b>Net EV · $/contract</b>after fees, per contract</div><div><b>ROI · %</b>net EV ÷ capital at risk</div><div><b>Sizing · contracts</b>set by the risk policy</div></div>';
  function blockerSummary(rows, d) {
    var all = [];
    rows.forEach(function (r) { var b = isObj(r) ? pick(r, ['blockers', 'blocked_by']) : null; if (Array.isArray(b)) all = all.concat(b); else if (isObj(r) && isObj(r.blocker)) all.push(r.blocker); });
    var top = isObj(d) ? pick(d, ['blockers']) : null;
    if (Array.isArray(top)) all = all.concat(top);
    if (!all.length) return '<p class="note">No blocker records in this section.</p>';
    var n = {}; AG.DEP_CLASSES.forEach(function (c) { n[c] = 0; }); var other = 0;
    all.forEach(function (b) { var c = isObj(b) ? pick(b, ['dependency_class', 'class', 'dependency']) : null; if (n[c] !== undefined) n[c]++; else other++; });
    return '<div class="deplegend">' + AG.DEP_CLASSES.map(function (c) {
      return '<div>' + AG.dep(c) + '<div class="n">' + n[c] + '</div><div class="note" style="margin:0">' + esc(AG.DEP_WHAT[c]) + '</div></div>';
    }).join('') + '</div>' + (other ? '<p class="note">' + other + ' blocker(s) carry no recognised dependency class.</p>' : '');
  }
  function queue(sec, ctx) {
    var d = sec.data, rows = AG.rowsOf(d, ['queue', 'rows', 'candidates', 'evaluations', 'opportunities']);
    var h = ECON + blockerSummary(rows, d);
    if (isObj(d)) {
      var ev = pick(d, ['active_evaluations', 'evaluating']);
      if (ev !== undefined) h += '<p class="lbl" style="margin:0 0 6px">Active evaluations</p>' + (Array.isArray(ev) ? AG.table(ev, OQ, {rd: ctx.rd}) : AG.fmt('active_evaluations', ev, ctx.rd)) + '<p class="lbl" style="margin:14px 0 6px">Queue</p>';
    }
    h += rows.length ? AG.table(rows, OQ, {rd: ctx.rd}) : '<p class="emptyline">The queue holds no rows.</p>';
    if (isObj(d) && Array.isArray(d.blockers) && d.blockers.length) h += '<p class="lbl" style="margin:14px 0 6px">Blockers</p>' + AG.table(d.blockers, [
      {label: 'Dependency class', keys: ['dependency_class', 'class', 'dependency']}, {label: 'Blocker', keys: ['reason', 'code', 'blocker', 'name']},
      {label: 'Subject', keys: ['subject', 'market', 'fixture']}, {label: 'Since', keys: ['since', 'first_seen_at', 'at'], u: 'ts'}, {label: 'Evidence', keys: ['evidence']}], {rd: ctx.rd});
    return h;
  }
  function summaryCell(r, rd) {
    var s = pick(r, ['summary', 'reason', 'why', 'detail']);
    if (s === undefined) return AG.unk('no summary on this decision');
    if (isObj(s)) { var o = {}; Object.keys(s).slice(0, 6).forEach(function (k) { o[k] = s[k]; }); return '<div class="nest">' + AG.kv(o, rd) + '</div>'; }
    return esc(s);
  }
  function decisions(sec, ctx) {
    var rows = AG.rowsOf(sec.data, ['decisions', 'rows', 'items']);
    if (!rows.length) return AG.genericBody(sec, ctx.rd);
    return AG.table(rows, [
      {label: 'Decided', keys: ['decided_at', 'at'], u: 'ts'}, {label: 'Kind', keys: ['kind']},
      {label: 'Subject', keys: ['subject', 'market', 'fixture']}, {label: 'Verdict', keys: ['verdict', 'decision', 'action']},
      {label: 'Summary', render: summaryCell}, {label: 'Ref', keys: ['decision_ref', 'id']}, {label: 'Evidence', keys: ['evidence', 'evidence_refs']}], {rd: ctx.rd});
  }
  var ORD = ['ordered_qty', 'qty_ordered', 'order_qty'], FIL = ['filled_qty', 'confirmed_qty', 'fill_qty', 'qty_filled'], OUT = ['outstanding_qty', 'remaining_qty', 'unfilled_qty'];
  function plans(sec, ctx) {
    var rows = AG.rowsOf(sec.data, ['plans', 'rows', 'intents', 'items']);
    var h = '<div class="legend"><span><i class="sw" style="background:var(--acc)"></i>filled · transferred to Xavier</span><span><i class="sw" style="background:repeating-linear-gradient(135deg,var(--ink-3) 0 2px,transparent 2px 4px)"></i>outstanding · not a position</span><span>An acknowledgement means the venue accepted the order; it is not a fill.</span></div>';
    if (!rows.length) return h + AG.genericBody(sec, ctx.rd);
    return h + AG.table(rows, [
      {label: 'Intent', keys: ['intent_id', 'entry_intent_id', 'plan_id', 'id']}, {label: 'Subject', keys: ['subject', 'market', 'market_slug']},
      {label: 'Ordered', keys: ORD, u: 'qty', num: 1}, {label: 'Acknowledged', keys: ['acknowledged_at', 'ack_at', 'acknowledged', 'acknowledgement'], u: 'ts'},
      {label: 'Filled', keys: FIL, u: 'qty', num: 1}, {label: 'Outstanding', keys: OUT, u: 'qty', num: 1},
      {label: 'Fill', render: function (r) { return R.fillBar(pick(r, ORD), pick(r, FIL)); }},
      {label: 'State', keys: ['state', 'status']}, {label: 'Evidence', keys: ['evidence']}], {rd: ctx.rd});
  }
  function handoffs(sec, ctx) {
    var rows = AG.rowsOf(sec.data, ['handoffs', 'rows', 'items']);
    if (!rows.length) return AG.genericBody(sec, ctx.rd);
    return '<p class="note">Only confirmed fills transfer ownership. Outstanding quantity stays with Derek’s entry plan until it fills or ends.</p>' + AG.table(rows, [
      {label: 'Entry intent', keys: ['entry_intent_id', 'intent_id']}, {label: 'Group', keys: ['portfolio_group_id']}, {label: 'Owner', keys: ['owner_agent']},
      {label: 'Ordered', keys: ORD, u: 'qty', num: 1}, {label: 'Confirmed', keys: ['confirmed_qty', 'filled_qty'], u: 'qty', num: 1}, {label: 'Outstanding', keys: OUT, u: 'qty', num: 1},
      {label: 'Fill', render: function (r) { return R.fillBar(pick(r, ORD), pick(r, ['confirmed_qty', 'filled_qty'])); }},
      {label: 'Last fill', keys: ['last_fill_at'], u: 'ts'}, {label: 'Handed off', keys: ['handoff_at'], u: 'ts'}, {label: 'Evidence', keys: ['evidence']}], {rd: ctx.rd});
  }
  function performance(sec, ctx) {
    return '<p class="note">Realised figures are read from the one authoritative book (bettor_funded_economics). UNKNOWN is not zero; repeated decisions on one fixture are not independent examples.</p>' + AG.genericBody(sec, ctx.rd);
  }
  AG.SPECS.derek = {sections: [
    {key: 'status', title: 'Status & dependencies', render: R.status},
    {key: 'versions', title: 'Policy, model & code versions', render: R.versions},
    {key: 'coverage', title: 'Catalogue coverage & exclusions', render: coverage, wide: true, note: 'Every census category, including markets Derek does not support.'},
    {key: 'subscription', title: 'Data & subscription health', render: subscription},
    {key: 'collection', title: 'Collection'},
    {key: 'opportunity_queue', title: 'Active evaluations & opportunity queue', render: queue, wide: true, note: 'Pinnacle and internal probabilities are shown separately with their own timestamps and qualification, beside the blended average the active policy (V2) enters on. The internal model is trained on market prices, so the average is not independent confirmation. Every figure is read from the stored decision record. Gross edge, net EV, ROI and sizing are separate quantities.'},
    {key: 'decisions', title: 'Entry decisions', render: decisions, wide: true},
    {key: 'plans_fills', title: 'Plans, acknowledgements & fills', render: plans, wide: true},
    {key: 'handoffs', title: 'Handoffs to Xavier', render: handoffs, wide: true},
    {key: 'latency', title: 'Latency'},
    {key: 'performance', title: 'Historical entry performance', render: performance}
  ]};
})(AG);
"""

XAVIER_JS = r"""
(function (AG) {
  'use strict';
  var esc = AG.esc, pick = AG.pick, isObj = AG.isObj, R = AG.R;
  var ACT = ['chosen_action', 'selected_action', 'action', 'decision'];
  var WHY = ['explanation', 'reasoning', 'why', 'rationale'];
  function tile(label, v) { return '<div><div class="lbl">' + esc(label) + '</div><div class="v">' + v + '</div></div>'; }
  function f(r, keys, u, rd) { var k = AG.pickKey(r, keys); return k === null ? AG.unk('not in this record') : AG.fmt(k, r[k], rd, u); }
  function standing(sp, rd) {
    // STANDING PROTECTION: filled protection and resting orders are shown
    // APART -- a resting order is an obligation that may still fill, not
    // protection. The venue capability flag and the strict fallback's
    // latency / queue trade-off are stated, not implied.
    if (!isObj(sp)) return '';
    if (sp.available === false) return '<div class="note">Standing protection: ' + esc(sp.why || 'unavailable') + '</div>';
    var fp = isObj(sp.filled_protection) ? sp.filled_protection : {};
    var sel = isObj(sp.selected_instrument) ? sp.selected_instrument : null;
    var inv = isObj(sp.invariant) ? sp.invariant : {};
    var h = '<div class="standing" data-standing="1"><div class="lbl">Standing protection &middot; mode <b>' + esc(sp.mode) + '</b> &middot; exchange-linked exclusivity <b>' + esc(sp.exchange_linked_exclusivity) + '</b></div>'
      + '<div class="tiles">' + tile('Selected instrument', sel ? esc(sel.candidate_id) + ' <span class="note">(' + esc(sel.selected_by) + ')</span>' : AG.unk('none selected: no hedge fill yet'))
      + tile('Confirmed primary', AG.fmt('confirmed_primary_qty', sp.confirmed_primary_qty, rd, 'qty'))
      + tile('Covered (filled protection)', AG.fmt('covered_qty', fp.covered_qty, rd, 'qty'))
      + tile('Uncovered', AG.fmt('uncovered_qty', fp.uncovered_qty, rd, 'qty'))
      + tile('Fill-capable (resting / potentially live)', AG.fmt('fill_capable_qty', sp.fill_capable_qty, rd, 'qty'))
      + tile('Lifecycle state', esc(sp.lifecycle_state))
      + tile('Floor class', sp.floor_class ? esc(sp.floor_class) : AG.unk('no plan'))
      + tile('Invariant', inv.holds === true ? 'holds: ' + esc(inv.hedge_held_plus_fill_capable) + ' &le; ' + esc(inv.confirmed_primary) : '<b>BREACHED</b> ' + esc(JSON.stringify(inv))) + '</div>';
    var rest = Array.isArray(sp.resting_orders) ? sp.resting_orders : [];
    h += '<div class="lbl">Resting orders &middot; obligations, not protection</div>' + (rest.length ? AG.table(rest, [
      {label: 'Instrument', keys: ['candidate_id']}, {label: 'Limit', keys: ['limit_price'], u: 'price', num: 1},
      {label: 'Quantity', keys: ['quantity'], u: 'qty', num: 1}, {label: 'Filled', keys: ['filled_qty'], u: 'qty', num: 1},
      {label: 'Fill-capable', keys: ['fill_capable_qty'], u: 'qty', num: 1}, {label: 'State', keys: ['lifecycle_state']},
      {label: 'Venue order', keys: ['venue_order_id']}, {label: 'Good till', keys: ['good_till']}], {rd: rd}) : '<p class="emptyline">No resting or potentially-live hedge order.</p>');
    h += '<p class="note">' + esc(sp.latency_disclosure || '') + '</p></div>';
    return h;
  }
  function positions(sec, ctx) {
    var rows = AG.rowsOf(sec.data, ['positions', 'groups', 'rows']);
    if (!rows.length) return AG.genericBody(sec, ctx.rd);
    return rows.map(function (p) {
      if (!isObj(p)) return '<p>' + esc(p) + '</p>';
      var rd = ctx.rd, act = pick(p, ACT), why = pick(p, WHY);
      return '<div class="pos-card"><div class="statehead"><div><h4>' + esc(pick(p, ['portfolio_group_id', 'group_id', 'entry_intent_id', 'id']) || 'position group') + '</h4>'
        + '<span class="note">' + esc(pick(p, ['market', 'us_market_slug', 'subject', 'market_slug']) || '') + '</span></div>'
        + (pick(p, ['responsibility_state', 'state']) !== undefined ? '<span class="chip"><b>' + esc(pick(p, ['responsibility_state', 'state'])) + '</b></span>' : '') + '</div>'
        + '<div class="tiles">' + tile('Basis', f(p, ['basis_usd', 'remaining_basis_usd', 'basis'], 'usd0', rd)) + tile('Current exposure', f(p, ['current_exposure_usd', 'exposure_usd', 'exposure'], 'usd0', rd))
        + tile('Primary qty', f(p, ['primary_qty'], 'qty', rd)) + tile('Hedge qty', f(p, ['hedge_qty'], 'qty', rd))
        + tile('Residual (unpaired) qty', f(p, ['residual_qty', 'unpaired_residual_qty', 'unpaired_qty'], 'qty', rd)) + tile('Owner', f(p, ['owner_agent', 'owner'], null, rd))
        + tile('Current review', f(p, ['current_review_at', 'last_review_at', 'reviewed_at'], 'ts', rd)) + tile('Next review', f(p, ['next_review_at'], 'ts', rd)) + '</div>'
        + '<div class="action"><div><div class="lbl">Selected action</div><div class="a">' + (act !== undefined ? esc(act) : AG.unk('no action recorded')) + '</div></div>'
        + '<div><div class="lbl">Explanation</div><div>' + (why !== undefined ? (isObj(why) ? AG.kv(why, rd) : esc(why)) : AG.unk('no explanation recorded')) + '</div>'
        + '<div class="wr">' + R.searchChip(p) + ' <span class="note" style="margin:0">policy ' + R.policyText(R.policyOf(p)) + '</span></div></div></div>'
        + standing(p.standing_protection, rd)
        + '<details class="raw" style="margin-top:8px"><summary>search completeness, policy and shadow comparison</summary>' + R.searchPanel(p, rd) + R.policyShadow(p, act, rd) + '</details>'
        + '<div style="margin-top:8px">' + AG.evidence(p.evidence) + '</div></div>';
    }).join('');
  }
  function servicingRuns(ctx) {
    var sc = AG.normSection(ctx.sections.servicing_cadence, 'servicing_cadence', ctx.url);
    var d = isObj(sc.data) ? sc.data : {};
    return {runs: pick(d, ['runs', 'passes', 'servicing_runs', 'pass_count', 'runs_total']),
            nothing: pick(d, ['passes_with_nothing', 'empty_passes', 'nothing_to_service', 'idle_passes', 'runs_with_nothing_to_service'])};
  }
  function positionsEmpty(sec, ctx) {
    var s = servicingRuns(ctx), line;
    if (typeof s.nothing === 'number') line = 'The servicing task ran ' + s.nothing.toLocaleString('en-US') + ' times with nothing to service' + (typeof s.runs === 'number' ? ' (of ' + s.runs.toLocaleString('en-US') + ' recorded passes)' : '') + '.';
    else if (typeof s.runs === 'number') line = 'The servicing task has ' + s.runs.toLocaleString('en-US') + ' recorded passes; this record does not say how many had nothing to service.';
    else line = 'The servicing cadence record does not report a pass count.';
    return '<div class="plain" data-empty="positions"><p class="big">No position is owned by Xavier.</p>'
      + '<p>' + esc(line) + '</p>'
      + '<p class="mute">An empty pass is not management: nothing was reviewed, hedged, reduced or exited.</p></div>';
  }
  function reviews(sec, ctx) {
    var rows = AG.rowsOf(sec.data, ['reviews', 'rows', 'decisions']);
    if (!rows.length) return AG.genericBody(sec, ctx.rd);
    return AG.table(rows, [
      {label: 'Reviewed', keys: ['reviewed_at', 'decided_at', 'at'], u: 'ts'}, {label: 'Group', keys: ['portfolio_group_id', 'intent_id', 'entry_intent_id']},
      {label: 'Action', keys: ACT}, {label: 'Search completeness', render: function (r) { return R.searchChip(r); }},
      {label: 'Selected policy', render: function (r) { return R.policyText(R.policyOf(r)); }},
      {label: 'Shadow policy (not dispatched)', render: function (r) { var sh = R.shadowOf(r); if (!isObj(sh)) return '<span class="none">not recorded</span>'; var w = pick(sh, ['would_choose', 'action', 'chosen_action', 'selected', 'would_select']); return 'would choose <b>' + esc(typeof w === 'object' ? JSON.stringify(w) : w) + '</b> <span class="sc sc-none">NOT DISPATCHED</span>'; }},
      {label: 'Why', keys: WHY}, {label: 'Next review', keys: ['next_review_at'], u: 'ts'},
      {label: 'Decision', keys: ['xavier_decision_id', 'decision_id', 'review_id', 'decision_ref']}, {label: 'Evidence', keys: ['evidence']}], {rd: ctx.rd});
  }
  function ladder(sec, ctx) {
    var rows = AG.rowsOf(sec.data, ['ladder', 'rungs', 'rows', 'lines']);
    var h = '<p class="note">Each rung pairs the held side with the opposing spread. At a pair cost above $1.00 a single winner loses the excess; both win only inside the overlap. A wider line overlaps more margins but costs more: it is not automatically better.</p>';
    h += R.searchPanel(isObj(sec.data) && !Array.isArray(sec.data) ? sec.data : (rows[0] || {}), ctx.rd);
    if (!rows.length) return h + AG.genericBody(sec, ctx.rd);
    return h + AG.table(rows, [
      {label: 'Line', keys: ['line', 'spread', 'handicap', 'label', 'rung']}, {label: 'Market', keys: ['market', 'market_slug', 'slug']},
      {label: 'Price', keys: ['price', 'ask', 'price_usd'], u: 'price', num: 1}, {label: 'P(cover)', keys: ['p_cover', 'probability', 'fair_probability', 'p'], u: 'prob', num: 1},
      {label: 'Pair cost', keys: ['pair_cost_usd', 'pair_cost'], u: 'usd0', num: 1}, {label: 'Single winner / pair', keys: ['single_winner_net_usd', 'one_wins_net_usd'], u: 'usd', num: 1},
      {label: 'Both win / pair', keys: ['double_win_net_usd', 'both_win_net_usd'], u: 'usd', num: 1}, {label: 'Both-win margins', keys: ['overlap', 'both_win_margins', 'overlap_margins']},
      {label: 'P(both win)', keys: ['p_both_win', 'p_both'], u: 'prob', num: 1}, {label: 'Executable qty', keys: ['executable_qty', 'exec_qty', 'depth_qty'], u: 'qty', num: 1},
      {label: 'Evidence age', keys: ['evidence_age_s', 'quote_age_s'], u: 's', num: 1}, {label: 'Evidence', keys: ['evidence']}], {rd: ctx.rd});
  }
  var ALT_COLS = [
    {label: 'Alternative', keys: ['action', 'alternative', 'name', 'label', 'kind']},
    {group: 'Economics', label: 'Expected net P&L', keys: ['expected_net_pnl_usd', 'expected_net_usd', 'expected_pnl_usd', 'value_usd', 'expected_value_usd', 'ev_usd'], u: 'usd', num: 1},
    {group: 'Economics', label: 'Increment vs HOLD', keys: ['increment_vs_hold_usd', 'vs_hold_usd', 'delta_vs_hold_usd'], u: 'usd', num: 1},
    {group: 'Economics', label: 'Worst case', keys: ['worst_case_usd', 'worst_usd', 'min_pnl_usd'], u: 'usd', num: 1},
    {group: 'Economics', label: 'P(net profit)', keys: ['p_net_profit', 'p_profit', 'prob_net_profit', 'probability_net_profit'], u: 'prob', num: 1},
    {group: 'Economics', label: 'P(both win)', keys: ['p_both_win', 'p_both', 'prob_both_win'], u: 'prob', num: 1},
    {group: 'Capital', label: 'Capital required', keys: ['capital_required_usd', 'capital_usd', 'capital'], u: 'usd0', num: 1},
    {group: 'Capital', label: 'Capital duration', keys: ['capital_duration_s', 'duration_s', 'capital_duration'], u: 's', num: 1},
    {group: 'Execution', label: 'Executable qty', keys: ['executable_qty', 'exec_qty', 'fillable_qty'], u: 'qty', num: 1},
    {group: 'Execution', label: 'Unpaired qty', keys: ['unpaired_qty', 'unpaired_residual_qty', 'residual_qty'], u: 'qty', num: 1},
    {group: 'Execution', label: 'Fees', keys: ['fees_usd', 'fee_usd', 'fees'], u: 'usd0', num: 1},
    {group: 'Execution', label: 'Compatibility', keys: ['compatibility', 'compatible', 'hedge_compatibility']},
    {group: 'Execution', label: 'Evidence age', keys: ['evidence_age_s', 'quote_age_s', 'evidence_age'], u: 's', num: 1},
    {label: 'Eligibility / refusal', render: function (r) {
      var e = pick(r, ['eligible', 'execution_eligible', 'execution_eligibility', 'eligibility']);
      var why = pick(r, ['refusal', 'refusal_reason', 'blocker', 'why_ineligible', 'ineligible_reason']);
      var h = e === undefined ? AG.unk('eligibility not recorded') : (e === true ? '<span class="bool">ELIGIBLE</span>' : e === false ? '<span class="bool neg">REFUSED</span>' : AG.fmt('eligibility', e));
      return h + (why !== undefined && why !== null ? '<div class="note" style="margin:3px 0 0">' + (isObj(why) ? AG.dep(pick(why, ['dependency_class', 'class'])) + ' ' + esc(pick(why, ['reason', 'code', 'why'])) : esc(why)) + '</div>' : '');
    }},
    {label: 'Evidence', keys: ['evidence']}
  ];
  function isSelected(chosen) {
    return function (r) {
      if (r.selected === true || r.chosen === true || r.is_selected === true) return true;
      var a = pick(r, ['action', 'alternative', 'name', 'label']);
      return chosen !== undefined && chosen !== null && a !== undefined && String(a) === String(chosen);
    };
  }
  function altGroups(d) {
    if (Array.isArray(d) && d.length && isObj(d[0]) && Array.isArray(d[0].alternatives)) return d;
    if (isObj(d) && Array.isArray(d.groups)) return d.groups;
    var rows = AG.rowsOf(d, ['alternatives', 'rows', 'items']);
    var by = {}, order = [];
    rows.forEach(function (r) { var g = isObj(r) ? (pick(r, ['portfolio_group_id', 'group_id', 'intent_id']) || '') : ''; if (!by[g]) { by[g] = []; order.push(g); } by[g].push(r); });
    return order.map(function (g) { return {portfolio_group_id: g || null, alternatives: by[g], chosen_action: isObj(d) ? pick(d, ACT) : undefined, explanation: isObj(d) ? pick(d, WHY) : undefined,
      reasoning: isObj(d) ? d.reasoning : undefined, search_completeness: isObj(d) ? d.search_completeness : undefined, selected_policy: isObj(d) ? d.selected_policy : undefined, shadow_comparison: isObj(d) ? d.shadow_comparison : undefined}; });
  }
  function alternatives(sec, ctx) {
    var groups = altGroups(sec.data);
    if (!groups.length || !groups.some(function (g) { return (g.alternatives || []).length; })) return AG.genericBody(sec, ctx.rd);
    return '<p class="note">Every examined alternative, HOLD included, with each quantity in its own column. Increment vs HOLD is the change in expected net P&L from choosing it instead of holding. When the search was incomplete the selection is labelled as the best among those examined, with how many of how many.</p>' + groups.map(function (g) {
      var alts = g.alternatives || [];
      var chosen = pick(g, ACT);
      if (chosen === undefined) { var s = alts.filter(function (r) { return isObj(r) && (r.selected === true || r.chosen === true); })[0]; if (s) chosen = pick(s, ['action', 'alternative', 'name', 'label']); }
      var why = pick(g, WHY);
      if (why === undefined) { var s2 = alts.filter(isSelected(chosen))[0]; if (s2) why = pick(s2, WHY); }
      var holder = g;
      if (R.searchOf(g) === undefined && R.policyOf(g) === undefined && R.shadowOf(g) === undefined) { var s3 = alts.filter(isSelected(chosen))[0]; if (s3) holder = s3; }
      var info = R.searchInfo(R.searchOf(holder));
      return (g.portfolio_group_id ? '<p class="lbl" style="margin:10px 0 6px">Group ' + AG.fmt('portfolio_group_id', g.portfolio_group_id) + '</p>' : '')
        + R.searchPanel(holder, ctx.rd)
        + AG.table(alts, ALT_COLS, {rd: ctx.rd, selected: isSelected(chosen)})
        + '<div class="action" style="margin:10px 0 10px"><div><div class="lbl">Selected · ' + esc(info.label) + '</div><div class="a">' + (chosen !== undefined ? esc(chosen) : AG.unk('no selection recorded')) + '</div></div>'
        + '<div><div class="lbl">Why this one</div><div>' + (why !== undefined ? (isObj(why) ? AG.kv(why, ctx.rd) : esc(why)) : AG.unk('no explanation recorded')) + '</div></div></div>'
        + R.policyShadow(holder, chosen, ctx.rd);
    }).join('');
  }
  function payoutSets(d) {
    var sets = [];
    function statesOf(x) {
      if (Array.isArray(x)) return x.filter(isObj).map(function (s) { return {state: pick(s, ['state', 'outcome', 'scenario']), p: pick(s, ['probability', 'p', 'prob']), gross: pick(s, ['payout_usd', 'gross_usd', 'payout']), net: pick(s, ['net_usd', 'net_pnl_usd', 'pnl_usd', 'net']), raw: s}; });
      if (isObj(x)) return Object.keys(x).map(function (k) { var v = x[k]; return isObj(v) ? {state: k, p: pick(v, ['probability', 'p']), gross: pick(v, ['payout_usd', 'gross_usd']), net: pick(v, ['net_usd', 'net_pnl_usd', 'pnl_usd', 'net']), raw: v} : {state: k, net: v}; });
      return [];
    }
    var list = Array.isArray(d) ? d : (isObj(d) ? (Array.isArray(d.tables) ? d.tables : null) : null);
    if (list) list.forEach(function (t) { if (isObj(t)) sets.push({name: pick(t, ['alternative', 'action', 'name', 'label']), group: pick(t, ['portfolio_group_id']), states: statesOf(pick(t, ['states', 'rows', 'payouts']))}); });
    else if (isObj(d)) Object.keys(d).forEach(function (k) { sets.push({name: k, states: statesOf(d[k])}); });
    return sets.filter(function (s) { return s.states.length; });
  }
  function payouts(sec, ctx) {
    var sets = payoutSets(sec.data);
    if (!sets.length) return AG.genericBody(sec, ctx.rd);
    var names = [], states = [];
    sets.forEach(function (s) { s.states.forEach(function (x) { if (states.indexOf(String(x.state)) < 0) states.push(String(x.state)); }); });
    var h = '<p class="note">Net P&L in each settlement state, per alternative. Probabilities are the qualified estimates recorded with the decision.</p><div class="tbl"><table><thead><tr><th>State</th><th class="num">P(state)</th>'
      + sets.map(function (s) { return '<th class="num">' + esc(s.name) + (s.group ? ' <span class="mute">' + esc(s.group) + '</span>' : '') + '</th>'; }).join('') + '</tr></thead><tbody>';
    states.forEach(function (st) {
      var p;
      sets.forEach(function (s) { s.states.forEach(function (x) { if (String(x.state) === st && typeof x.p === 'number') p = x.p; }); });
      h += '<tr><td>' + esc(st) + '</td><td class="num">' + (typeof p === 'number' ? p.toFixed(3) : AG.unk()) + '</td>' + sets.map(function (s) {
        var x = s.states.filter(function (y) { return String(y.state) === st; })[0];
        if (!x) return '<td class="num">' + AG.unk('state not in this table') + '</td>';
        var v = x.net !== undefined ? x.net : x.gross;
        return '<td class="num">' + (typeof v === 'number' ? AG.money(v, true) : AG.fmt('net', v, ctx.rd)) + '</td>';
      }).join('') + '</tr>';
    });
    return h + '</tbody></table></div>';
  }
  function performance(sec, ctx) {
    var d = sec.data, h = '';
    if (isObj(d)) {
      var mc = pick(d, ['management_contribution_usd', 'contribution_usd', 'management_contribution']);
      if (mc !== undefined) h += '<div class="action" style="margin-bottom:10px"><div><div class="lbl">Management contribution</div><div class="a">' + (typeof mc === 'number' ? AG.money(mc, true) : AG.fmt('management_contribution_usd', mc, ctx.rd)) + '</div></div><div class="note" style="margin:0">Realised P&L of the managed book minus what HOLD would have realised, from bettor_funded_economics. Hypothetical alternatives are not realised P&L.</div></div>';
    }
    return h + AG.genericBody(sec, ctx.rd);
  }
  function cadence(sec, ctx) {
    var d = sec.data;
    if (!isObj(d)) return AG.genericBody(sec, ctx.rd);
    return '<p class="note">How often the servicing task actually ran, measured from its recorded passes.</p>' + AG.genericBody(sec, ctx.rd);
  }
  AG.R.positionsEmpty = positionsEmpty;
  AG.SPECS.xavier = {sections: [
    {key: 'status', title: 'Status & dependencies', render: R.status},
    {key: 'versions', title: 'Policy, model & code versions', render: R.versions},
    {key: 'positions', title: 'Owned position groups', render: positions, empty: positionsEmpty, wide: true, note: 'Basis, exposure, primary / hedge / residual quantities, the current and next review, and the selected action with its explanation. <a href="/positions" target="_top">Open every position as one correlated room &#8594;</a>'},
    {key: 'servicing_cadence', title: 'Servicing cadence', render: cadence},
    {key: 'reviews', title: 'Reviews', render: reviews},
    {key: 'ladder', title: 'Spread ladder', render: ladder, wide: true},
    {key: 'alternatives', title: 'Alternatives considered', render: alternatives, wide: true},
    {key: 'payout_tables', title: 'Per-state payout tables', render: payouts, wide: true},
    {key: 'execution', title: 'Execution history', render: R.timeline},
    {key: 'recovery', title: 'Recovery & reconciliation', render: R.timeline},
    {key: 'performance', title: 'Management contribution & performance', render: performance, wide: true}
  ]};
})(AG);
"""

AUDREY_JS = r"""
(function (AG) {
  'use strict';
  var esc = AG.esc, pick = AG.pick, isObj = AG.isObj, R = AG.R;
  var BASE = '/api/command/agents/audrey';
  var CATS = [
    {id: 'ACTUAL', label: 'Actual · executed & booked', rx: /ACTUAL|EXECUTED|BOOKED|REALI[SZ]ED/i, desc: 'Fills and settlements in the authoritative book (bettor_funded_economics).'},
    {id: 'OBSERVED', label: 'Observed alternative', rx: /OBSERV|COUNTERFACTUAL|EXECUTABLE|COULD/i, desc: 'Priced from quotes captured at decision time. Never placed: its outcome is hypothetical.'},
    {id: 'MODELLED', label: 'Modelled estimate', rx: /HYPOTH|MODEL|ESTIMAT|SIMULAT/i, desc: 'Fill or price modelled, not captured. Weakest evidence.'},
    {id: 'UNRESOLVED', label: 'Unresolved', rx: /UNRESOLV|PENDING|UNKNOWN|UNSETTLED|INSUFFICIENT|OPEN/i, desc: 'Outcome or evidence not yet established; excluded from performance.'}
  ];
  function catOf(name) { for (var i = 0; i < CATS.length; i++) if (CATS[i].rx.test(String(name))) return CATS[i].id; return null; }
  function outcomes(sec, ctx) {
    var d = sec.data, found = {}, other = [];
    function add(name, v) {
      var id = catOf(name);
      var rec = isObj(v) ? v : {count: v};
      if (id && !found[id]) found[id] = {name: name, rec: rec}; else other.push({name: name, rec: rec});
    }
    var ck = isObj(d) ? AG.pickKey(d, ['categories', 'by_category', 'evidence_categories']) : null;
    var c = ck ? d[ck] : null;
    if (Array.isArray(c)) c.forEach(function (x) { if (isObj(x)) add(pick(x, ['category', 'name', 'evidence_category', 'kind']), x); });
    else if (isObj(c)) Object.keys(c).forEach(function (k) { add(k, c[k]); });
    else {
      var rows = AG.rowsOf(d, ['rows', 'outcomes', 'items']), agg = {};
      rows.forEach(function (r) { if (!isObj(r)) return; var n = pick(r, ['category', 'evidence_category', 'kind']); if (n === undefined) return; agg[n] = agg[n] || {count: 0, rows: []}; agg[n].count++; agg[n].rows.push(r); });
      Object.keys(agg).forEach(function (k) { add(k, agg[k]); });
    }
    var h = '<div class="cats">' + CATS.map(function (C) {
      var f = found[C.id];
      if (!f) return '<div class="cat cat-' + C.id + '" data-cat="' + C.id + '"><h5>' + esc(C.label) + '</h5><div class="n">' + AG.unk('not reported by this record') + '</div><p>' + esc(C.desc) + '</p></div>';
      var r = f.rec, n = pick(r, ['count', 'n', 'decisions', 'rows_total']), net = pick(r, ['net_usd', 'total_usd', 'pnl_usd', 'net_pnl_usd', 'value_usd']);
      return '<div class="cat cat-' + C.id + '" data-cat="' + C.id + '"><h5>' + esc(C.label) + '</h5><div class="n">' + (typeof n === 'number' ? n.toLocaleString('en-US') : AG.unk('count not recorded')) + '</div>'
        + (net !== undefined ? '<div>' + (typeof net === 'number' ? AG.money(net, true) : AG.fmt('net_usd', net)) + (C.id === 'ACTUAL' ? '' : ' <span class="mute">hypothetical</span>') + '</div>' : '')
        + '<p>' + esc(pick(r, ['description', 'meaning']) || C.desc) + '</p><p class="mute mono">' + esc(f.name) + '</p>' + (r.evidence ? AG.evidence(r.evidence) : '') + '</div>';
    }).join('') + other.map(function (o) { return '<div class="cat cat-OTHER"><h5>Other category: ' + esc(o.name) + '</h5><div>' + AG.fmt('', o.rec, ctx.rd) + '</div></div>'; }).join('') + '</div>';
    var dk = isObj(d) ? AG.pickKey(d, ['audited_decisions', 'decisions']) : null;
    if (dk && Array.isArray(d[dk]) && d[dk].length) h += audited(d[dk], ctx.rd);
    else { var dr = AG.rowsOf(d, ['rows', 'outcomes', 'items']).filter(isDecisionAudit); if (dr.length) h += audited(dr, ctx.rd); }
    if (isObj(d)) { var rest = {}; Object.keys(d).forEach(function (k) { if (k !== ck && k !== dk && k !== 'rows' && k !== 'outcomes') rest[k] = d[k]; }); if (Object.keys(rest).length) h += '<div style="margin-top:12px">' + AG.kv(rest, ctx.rd) + '</div>'; }
    return h;
  }
  function cohort(sec, ctx) {
    return '<p class="note">Repeated observations or decisions on one fixture are not independent examples; the independent count is the number of distinct fixtures or cohorts.</p>' + AG.genericBody(sec, ctx.rd);
  }
  function reports(sec, ctx) {
    var rows = AG.rowsOf(sec.data, ['reports', 'rows', 'daily_reports']);
    if (!rows.length) return AG.genericBody(sec, ctx.rd);
    return rows.slice(0, 14).map(function (r) {
      if (!isObj(r)) return '<p>' + esc(r) + '</p>';
      var rest = {}; Object.keys(r).forEach(function (k) { if (['report_date', 'day', 'date', 'headline', 'summary', 'evidence', 'report_id'].indexOf(k) < 0) rest[k] = r[k]; });
      var s = pick(r, ['headline', 'summary']);
      return '<div class="find"><div class="h"><b>' + esc(pick(r, ['report_date', 'day', 'date']) || 'report') + '</b><span class="mono mute">' + esc(pick(r, ['report_id', 'id']) || '') + '</span></div>'
        + (s !== undefined ? '<div>' + (isObj(s) ? AG.kv(s, ctx.rd) : esc(s)) + '</div>' : '') + (Object.keys(rest).length ? '<div class="nest">' + AG.kv(rest, ctx.rd) + '</div>' : '') + AG.evidence(r.evidence) + '</div>';
    }).join('');
  }
  function finding(r, rd) {
    if (!isObj(r)) return '<div class="find">' + esc(r) + '</div>';
    var sev = String(pick(r, ['severity', 'level']) || '');
    var rest = {}; Object.keys(r).forEach(function (k) { if (['severity', 'level', 'title', 'finding', 'summary', 'agent', 'agent_id', 'subject_agent', 'evidence'].indexOf(k) < 0) rest[k] = r[k]; });
    return '<div class="find"><div class="h"><b>' + esc(pick(r, ['title', 'finding', 'summary']) || 'finding') + '</b>' + (sev ? '<span class="sev sev-' + esc(sev.toUpperCase()) + '">' + esc(sev) + '</span>' : '') + '</div>'
      + (isDecisionAudit(r) ? '<div class="wr" style="margin:4px 0 6px">' + R.searchChip(r) + '</div>' : '')
      + (Object.keys(rest).length ? '<div class="nest">' + AG.kv(rest, rd) + '</div>' : '') + R.laterDiscovered(r) + AG.evidence(r.evidence) + '</div>';
  }
  var DECISION_KEYS = ['xavier_decision_id', 'decision_id', 'decision_ref', 'decision', 'audited_decision', 'reasoning', 'search_completeness'];
  function isDecisionAudit(r) { return isObj(r) && (R.searchOf(r) !== undefined || DECISION_KEYS.some(function (k) { return AG.has(r, k); })); }
  function audited(list, rd) {
    return '<p class="lbl" style="margin:14px 0 6px">Audited decisions</p>' + list.slice(0, 20).map(function (r) {
      if (!isObj(r)) return '<div class="find">' + esc(r) + '</div>';
      var rest = {}; Object.keys(r).forEach(function (k) { if (['reasoning', 'evidence', 'later_discovered', 'discovered_after_decision', 'later_discovered_contracts', 'contracts_discovered_later', 'search_completeness', 'shadow_comparison', 'selected_policy'].indexOf(k) < 0) rest[k] = r[k]; });
      return '<div class="find"><div class="wr" style="margin:0 0 6px">' + R.searchChip(r) + ' <span class="note" style="margin:0">policy ' + R.policyText(R.policyOf(r)) + '</span></div>'
        + '<div class="nest">' + AG.kv(rest, rd) + '</div>' + R.laterDiscovered(r) + AG.evidence(r.evidence) + '</div>';
    }).join('');
  }
  function findings(sec, ctx) {
    var d = sec.data, groups = {DEREK: [], XAVIER: []}, other = [];
    if (isObj(d) && (Array.isArray(d.DEREK) || Array.isArray(d.XAVIER) || Array.isArray(d.derek) || Array.isArray(d.xavier))) {
      groups.DEREK = d.DEREK || d.derek || []; groups.XAVIER = d.XAVIER || d.xavier || [];
    } else AG.rowsOf(d, ['findings', 'rows', 'items']).forEach(function (r) {
      var a = String(isObj(r) ? (pick(r, ['agent', 'agent_id', 'subject_agent']) || '') : '').toUpperCase();
      if (groups[a]) groups[a].push(r); else other.push(r);
    });
    if (!groups.DEREK.length && !groups.XAVIER.length && !other.length) return AG.genericBody(sec, ctx.rd);
    function col(name, list) { return '<div><h4><span class="agent-dot a-' + name + '"></span>' + name + ' · ' + list.length + '</h4>' + (list.length ? list.map(function (r) { return finding(r, ctx.rd); }).join('') : '<p class="emptyline">No findings recorded for ' + name + '.</p>') + '</div>'; }
    return '<div class="split">' + col('DEREK', groups.DEREK) + col('XAVIER', groups.XAVIER) + '</div>' + (other.length ? '<h4 class="lbl" style="margin-top:12px">Other</h4>' + other.map(function (r) { return finding(r, ctx.rd); }).join('') : '');
  }
  var TASK_ORDER = ['OPEN', 'IN_PROGRESS', 'WAITING', 'CANDIDATE_READY', 'EVALUATING', 'APPROVAL_READY', 'APPROVED', 'RELEASED', 'REJECTED', 'ROLLED_BACK', 'CLOSED_NO_CHANGE', 'CANCELLED'];
  function tasks(sec, ctx) {
    var rows = AG.rowsOf(sec.data, ['tasks', 'rows', 'items']);
    if (!rows.length) return AG.genericBody(sec, ctx.rd);
    var by = {};
    rows.forEach(function (t) { var s = isObj(t) ? String(t.status || 'UNRECORDED') : 'UNRECORDED'; (by[s] = by[s] || []).push(t); });
    var cols = TASK_ORDER.filter(function (s) { return by[s]; }).concat(Object.keys(by).filter(function (s) { return TASK_ORDER.indexOf(s) < 0; }));
    return '<div class="board">' + cols.map(function (s) {
      return '<div class="col"><h5><span>' + esc(s.replace(/_/g, ' ')) + '</span><span>' + by[s].length + '</span></h5>' + by[s].map(function (t) {
        if (!isObj(t)) return '<div class="tcard">' + esc(t) + '</div>';
        var a = String(t.assignee || '').toUpperCase();
        return '<div class="tcard"><b>' + esc(t.title || t.task_id || 'task') + '</b><div class="chips" style="margin-bottom:6px"><span class="chip"><i class="agent-dot a-' + (['DEREK', 'XAVIER', 'AUDREY'].indexOf(a) >= 0 ? a : 'OTHER') + '"></i>' + esc(t.assignee || 'unassigned') + '</span>'
          + (t.kind ? '<span class="chip">' + esc(t.kind) + '</span>' : '') + '</div>'
          + '<div class="mono mute" style="font-size:11px">' + esc(t.task_id || '') + (t.created_by ? ' · by ' + esc(t.created_by) : '') + (t.directive_id ? ' · directive ' + esc(t.directive_id) : '') + '</div>'
          + (t.updated_at !== undefined ? '<div class="note" style="margin:4px 0">updated ' + AG.ts(t.updated_at, ctx.rd) + '</div>' : '') + AG.evidence(t.evidence) + '</div>';
      }).join('') + '</div>';
    }).join('') + '</div>';
  }
  function roleOf(r, keys) { var v = pick(r, keys); return v === undefined || v === null ? null : String(v); }
  function separation(r) {
    var p = roleOf(r, ['proposer', 'proposed_by', 'created_by']), e = roleOf(r, ['evaluator', 'evaluated_by']), a = roleOf(r, ['approver', 'approved_by']);
    var warn = [];
    if (p && e && p === e) warn.push('proposer = evaluator');
    if (a && (a === p || a === e)) warn.push('approver is not separate');
    return {p: p, e: e, a: a, warn: warn};
  }
  function rolesHtml(s) {
    function one(l, v) { return '<div><div class="lbl">' + l + '</div><div>' + (v ? esc(v) : AG.unk('not recorded')) + '</div></div>'; }
    return '<div class="roles">' + one('Proposer', s.p) + one('Evaluator', s.e) + one('Approver', s.a) + '</div>' + (s.warn.length ? '<div class="wr">' + s.warn.map(function (w) { return '<span class="warnchip">SEPARATION: ' + esc(w) + '</span>'; }).join('') + '</div>' : '');
  }
  function candidates(sec, ctx) {
    var rows = AG.rowsOf(sec.data, ['candidates', 'rows', 'items']);
    if (!rows.length) return AG.genericBody(sec, ctx.rd);
    return rows.map(function (c) {
      if (!isObj(c)) return '<p>' + esc(c) + '</p>';
      var id = pick(c, ['candidate_id', 'id', 'version', 'task_id']);
      var href = pick(c, ['approve_href']) || (id !== undefined ? BASE + '/candidates/' + encodeURIComponent(id) + '/approve' : null);
      var rest = {}; Object.keys(c).forEach(function (k) { if (['evidence', 'approve_href', 'proposer', 'proposed_by', 'created_by', 'evaluator', 'evaluated_by', 'approver', 'approved_by'].indexOf(k) < 0) rest[k] = c[k]; });
      var s = String(pick(c, ['state', 'status']) || '');
      var canApprove = /APPROVAL_READY|READY_FOR_APPROVAL/.test(s);
      return '<div class="rel"><div class="statehead"><b class="mono">' + esc(id || 'candidate') + '</b><span class="chip"><b>' + esc(s || 'state not recorded') + '</b></span></div>'
        + '<div class="nest" style="margin:8px 0">' + AG.kv(rest, ctx.rd) + '</div>' + rolesHtml(separation(c))
        + writeRowFor([AG.writeButton(canApprove ? 'Approve' : 'Approve (not approval-ready)', canApprove ? href : null, {candidate_id: id})])
        + '<div style="margin-top:8px">' + AG.evidence(c.evidence) + '</div></div>';
    }).join('');
  }
  function writeRowFor(b) { return AG.writeRow(b); }
  function evaluations(sec, ctx) {
    var rows = AG.rowsOf(sec.data, ['evaluations', 'rows', 'items']);
    if (!rows.length) return AG.genericBody(sec, ctx.rd);
    return '<p class="note">Replay and test results on recorded evidence. A replay is not a live order and a passing evaluation is not proven profitability.</p>' + AG.table(rows, [
      {label: 'Evaluated', keys: ['evaluated_at', 'at', 'finished_at'], u: 'ts'}, {label: 'Candidate', keys: ['candidate_id', 'version', 'task_id']},
      {label: 'Evaluator', keys: ['evaluator', 'evaluated_by']}, {label: 'Verdict', keys: ['verdict', 'result', 'state']},
      {label: 'Tests', keys: ['tests', 'test_summary', 'tests_passed']}, {label: 'Replay / metrics', keys: ['metrics', 'replay', 'replay_report', 'summary']},
      {label: 'Independent cohorts', keys: ['independent_cohorts', 'fixtures', 'cohorts'], u: 'qty', num: 1}, {label: 'Evidence', keys: ['evidence']}], {rd: ctx.rd});
  }
  var STAGES = [['Approval', ['approval_state', 'approval', 'approved_at']], ['Deployment', ['deployment_state', 'deployment', 'deployed_at']], ['Canary', ['canary_state', 'canary']], ['Rollback', ['rollback_state', 'rollback', 'rolled_back_at']]];
  function releases(sec, ctx) {
    var rows = AG.rowsOf(sec.data, ['releases', 'rows', 'items']);
    if (!rows.length) return AG.genericBody(sec, ctx.rd);
    return rows.map(function (r) {
      if (!isObj(r)) return '<p>' + esc(r) + '</p>';
      return '<div class="rel"><div class="statehead"><b class="mono">' + esc(pick(r, ['release_id', 'candidate_id', 'version', 'id']) || 'release') + '</b><span class="chip"><b>' + esc(pick(r, ['state', 'status']) || 'state not recorded') + '</b></span></div>'
        + '<div class="pipe">' + STAGES.map(function (s) { var k = AG.pickKey(r, s[1]); return '<div class="' + (k !== null && r[k] !== null && r[k] !== false ? 'on' : '') + '"><div class="lbl">' + s[0] + '</div><div>' + (k === null ? AG.unk('not recorded') : AG.fmt(k, r[k], ctx.rd)) + '</div></div>'; }).join('') + '</div>'
        + rolesHtml(separation(r)) + '<div style="margin-top:8px">' + AG.evidence(r.evidence) + '</div></div>';
    }).join('');
  }
  var OPEN_DIRECTIVE = /PROPOSED|PENDING|DRAFT|AWAITING|UNCONFIRMED/;
  function directives(sec, ctx) {
    var rows = AG.rowsOf(sec.data, ['directives', 'rows', 'items']);
    if (!rows.length) return AG.genericBody(sec, ctx.rd);
    return rows.map(function (r) {
      if (!isObj(r)) return '<p>' + esc(r) + '</p>';
      var id = pick(r, ['directive_id', 'id']), st = String(pick(r, ['state', 'status']) || '');
      var rest = {}; Object.keys(r).forEach(function (k) { if (['directive_id', 'id', 'text', 'directive', 'instruction', 'state', 'status', 'evidence', 'confirm_href', 'cancel_href'].indexOf(k) < 0) rest[k] = r[k]; });
      var base = id !== undefined ? ENDPOINT_DIRECTIVES + '/' + encodeURIComponent(id) : null;
      var open = OPEN_DIRECTIVE.test(st);
      return '<div class="rel"><div class="statehead"><b class="mono">' + esc(id || 'directive') + '</b><span class="chip"><b>' + esc(st || 'state not recorded') + '</b></span></div>'
        + '<p style="font:600 15px/1.4 var(--display);margin:8px 0">“' + esc(pick(r, ['text', 'directive', 'instruction']) || '') + '”</p>'
        + (Object.keys(rest).length ? '<div class="nest">' + AG.kv(rest, ctx.rd) + '</div>' : '')
        + (open ? AG.writeRow([AG.writeButton('Confirm', r.confirm_href || (base && base + '/confirm')), AG.writeButton('Cancel', r.cancel_href || (base && base + '/cancel'))]) : '')
        + '<div style="margin-top:8px">' + AG.evidence(r.evidence) + '</div></div>';
    }).join('');
  }
  var ENDPOINT_DIRECTIVES = AG.ENDPOINTS.directives;
  function provider(sec, ctx) {
    var d = sec.data;
    if (!isObj(d)) return AG.genericBody(sec, ctx.rd);
    var m = pick(d, ['mode', 'provider_mode', 'state']), disc = pick(d, ['disclosure', 'note']);
    return '<div class="statehead" style="margin-bottom:8px"><span class="lbl">Provider mode</span><span class="chip"><b>' + (m !== undefined ? esc(m) : 'NOT REPORTED') + '</b></span></div>'
      + (disc ? '<p class="prov">' + esc(disc) + '</p>' : '') + AG.kv(d, ctx.rd);
  }
  // ── chat (the real service; nothing scripted here) ────────────────
  function chatAnswer(j) {
    j = isObj(j) ? j : {};
    var text = pick(j, ['answer', 'reply', 'text', 'content', 'message']);
    var p = j.provider, mode = pick(j, ['provider_mode', 'mode']), disc = j.disclosure;
    if (isObj(p)) { if (mode === undefined) mode = pick(p, ['mode', 'provider_mode', 'state', 'name']); if (disc === undefined) disc = pick(p, ['disclosure', 'note']); }
    else if (typeof p === 'string' && mode === undefined) mode = p;
    var cites = pick(j, ['citations', 'sources', 'evidence']);
    var dir = pick(j, ['proposed_directive', 'directive']);
    var h = '<div class="msg a" data-role="answer"><div class="who">AUDREY · CHAT SERVICE</div><p>' + (text !== undefined ? esc(isObj(text) ? JSON.stringify(text) : text) : '<em class="unk">the service returned no answer text</em>') + '</p>';
    h += '<div class="cites">' + (Array.isArray(cites) && cites.length ? AG.evidence(cites) : '<span class="none">no citations returned</span>') + '</div>';
    h += '<div class="prov" data-provider>' + (mode !== undefined && mode !== null ? 'Provider mode: ' + esc(mode) : 'Provider mode not reported by the service') + (disc ? ' · ' + esc(disc) : '') + '</div>';
    if (isObj(dir)) {
      var id = pick(dir, ['directive_id', 'id']);
      var base = id !== undefined ? ENDPOINT_DIRECTIVES + '/' + encodeURIComponent(id) : null;
      h += '<div class="rel" style="margin-top:8px"><div class="lbl">Proposed directive · ' + esc(dir.state || dir.status || 'state not recorded') + '</div><p>“' + esc(pick(dir, ['text', 'directive', 'instruction']) || '') + '”</p>'
        + AG.writeRow([AG.writeButton('Confirm', dir.confirm_href || (base && base + '/confirm')), AG.writeButton('Cancel', dir.cancel_href || (base && base + '/cancel'))]) + '</div>';
    }
    return h + '</div>';
  }
  async function ask(f, message, conversationId) {
    var r;
    try { r = await f(AG.ENDPOINTS.chat, {method: 'POST', credentials: 'same-origin', cache: 'no-store', headers: {'Content-Type': 'application/json', 'Accept': 'application/json'}, body: JSON.stringify({message: message, question: message, conversation_id: conversationId || null})}); }
    catch (e) { return {kind: 'UNAVAILABLE', html: '<div class="msg a"><div class="who">AUDREY</div><p class="unav">Not sent: network error.</p></div>'}; }
    if (r.status === 401) return {kind: 'LOCKED', html: '<div class="msg a"><p>' + AG.LOCKED + '. <a href="' + AG.DESK + '">Open the desk</a></p></div>'};
    if (r.status === 403) return {kind: 'NEEDS_OPERATOR', html: '<div class="msg a"><p>Refused (403): this credential may not use the management chat.</p></div>'};
    if (r.status === 404 || r.status === 405) return {kind: 'NOT_DEPLOYED', html: '<div class="msg a"><div class="who">AUDREY</div><p>The Audrey chat service is not deployed in this build (POST ' + esc(AG.ENDPOINTS.chat) + ' returned ' + r.status + '). No answer is shown in its place.</p></div>'};
    var j = null; try { j = await r.json(); } catch (_) {}
    if (!r.ok) return {kind: 'REFUSED', html: '<div class="msg a"><div class="who">AUDREY</div><p class="unav">The service refused the question: HTTP ' + r.status + (j && j.detail ? ' · ' + esc(typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)) : '') + '</p></div>'};
    return {kind: 'OK', json: j, conversationId: j && j.conversation_id, html: chatAnswer(j)};
  }
  AG.chat = {answer: chatAnswer, ask: ask};
  AG.SPECS.audrey = {sections: [
    {key: 'status', title: 'Status & dependencies', render: R.status},
    {key: 'versions', title: 'Policy, model & code versions', render: R.versions},
    {key: 'provider', title: 'AI provider & disclosure', render: provider},
    {key: 'daily_reports', title: 'Daily reports', render: reports},
    {key: 'findings', title: 'Findings · Derek and Xavier', render: findings, wide: true},
    {key: 'outcomes', title: 'Actual vs hypothetical outcomes', render: outcomes, wide: true, note: 'Four evidence categories, kept visibly distinct. Only the first is realised P&L.'},
    {key: 'cohort_quality', title: 'Evidence & cohort quality', render: cohort},
    {key: 'conversations', title: 'Conversations'},
    {key: 'tasks', title: 'Improvement tasks', render: tasks, wide: true},
    {key: 'candidates', title: 'Proposed changes', render: candidates, wide: true},
    {key: 'evaluations', title: 'Tests & evaluation results', render: evaluations, wide: true},
    {key: 'releases', title: 'Approval, deployment, canary & rollback', render: releases, wide: true},
    {key: 'directives', title: 'Directives', render: directives, wide: true}
  ]};
})(AG);
"""

# Karen (red team, migration 207): her challenges and the six metrics, each
# with its numerator and denominator. Read only; no write control here.
KAREN_JS = r"""
(function (AG) {
  'use strict';
  var esc = AG.esc, pick = AG.pick, isObj = AG.isObj, R = AG.R;
  function refs(list) { return Array.isArray(list) && list.length ? list.map(function (e) { return '<span class="mono">' + esc(e.kind) + ' · ' + esc(e.id) + '</span>'; }).join('<br>') : '<span class="mute">none cited</span>'; }
  function peer(r) {
    var p = r.peer_response;
    if (!isObj(p)) return '<span class="mute">awaiting ' + esc(r.target_agent) + '</span>';
    return '<b>' + esc(p.stance) + '</b> by ' + esc(p.by) + ' · ' + esc(p.response) + (p.evidence_refs && p.evidence_refs.length ? '<br>' + refs(p.evidence_refs) : '');
  }
  function evaluation(r) {
    var e = r.independent_evaluation;
    if (!isObj(e)) return AG.unk('no evaluation field in this record');
    if (e.status !== 'RECORDED') return '<span class="mute">' + esc(String(e.status || '').replace(/_/g, ' ').toLowerCase()) + (e.evaluator ? ' · evaluator ' + esc(e.evaluator) : '') + '</span>';
    return '<b>' + esc(e.outcome) + '</b> by ' + esc(e.by) + (e.independent ? '' : ' <span class="unk">NOT INDEPENDENT</span>') + ' · ' + esc(e.reason || '') + (e.evidence_refs && e.evidence_refs.length ? '<br>' + refs(e.evidence_refs) : '');
  }
  function falseBlock(r) {
    var o = r.false_block_outcome;
    return o ? esc(String(o).replace(/_/g, ' ').toLowerCase()) : AG.unk('not in this record');
  }
  function downstream(r) {
    var d = r.downstream;
    if (!isObj(d)) return AG.unk('not in this record');
    if (d.status !== 'IMPROVEMENT_LINKED') return '<span class="mute">' + esc(String(d.status || '').replace(/_/g, ' ').toLowerCase()) + '</span>';
    return 'improvement ' + esc(d.finding_id || d.proposal_id) + ' · linked by ' + esc(d.linked_by) + (isObj(d.impact) ? '<br>' + AG.kv(d.impact) : '');
  }
  function challengeTable(sec, ctx) {
    var rows = AG.rowsOf(sec.data, ['challenges', 'rows', 'items']);
    if (!rows.length) return AG.genericBody(sec, ctx.rd);
    return AG.table(rows, [
      {label: 'Challenge', keys: ['challenge_id']},
      {label: 'Target agent', keys: ['target_agent']},
      {label: 'Target decision', render: function (r) { return '<span class="mono">' + esc(r.target_kind) + ' · ' + esc(r.target_id) + '</span>'; }},
      {label: 'Category', keys: ['category']},
      {label: 'Severity', keys: ['severity']},
      {label: 'State', render: function (r) { return AG.statusPill(r.state); }},
      {label: 'Claim', keys: ['claim']},
      {label: 'Evidence', render: function (r) { return refs(r.evidence_refs); }},
      {label: 'Peer response', render: peer},
      {label: 'Independent evaluation', render: evaluation},
      {label: 'False-block outcome', render: falseBlock},
      {label: 'Downstream impact', render: downstream},
      {label: 'Time to challenge (s)', keys: ['time_to_challenge_s'], num: 1},
      {label: 'Challenged', keys: ['challenged_at'], u: 'ts'}], {rd: ctx.rd});
  }
  function metricsTable(sec, ctx) {
    var d = isObj(sec.data) ? sec.data : {}, m = isObj(d.metrics) ? d.metrics : {};
    var rows = Object.keys(m).map(function (k) { return m[k]; });
    if (!rows.length) return AG.genericBody(sec, ctx.rd);
    return '<p class="note">' + esc(d.rule || '') + '</p>' + AG.table(rows, [
      {label: 'Metric', keys: ['name']},
      {label: 'Value', render: function (r) { return r.value === null || r.value === undefined ? AG.unk(r.why || 'unmeasurable') : esc(r.value); }},
      {label: 'Numerator', render: function (r) { return r.numerator === null || r.numerator === undefined ? AG.unk() : esc(r.numerator); }},
      {label: 'Denominator', keys: ['denominator'], num: 1},
      {label: 'Why null', render: function (r) { return r.why ? esc(r.why) : ''; }},
      {label: 'Definition', keys: ['definition']}], {rd: ctx.rd});
  }
  AG.SPECS.karen = {sections: [
    {key: 'status', title: 'Status & heartbeat', render: R.status},
    {key: 'authority', title: 'Authority · none', note: 'Karen holds no order, approval, activation, limit or promotion authority. Enforced in code and in the database.'},
    {key: 'current_challenges', title: 'Current challenges', render: challengeTable, wide: true},
    {key: 'metrics', title: 'Metrics · numerator / denominator', render: metricsTable, wide: true, note: 'Null means unmeasurable, never zero.'},
    {key: 'recent_challenges', title: 'All recent challenges', render: challengeTable, wide: true},
    {key: 'detectors', title: 'Scheduled detectors'}
  ]};
})(AG);
"""

# Archer and Scout (migration 217): their records, read only. Null is
# UNKNOWN / NOT MEASURED with the reason, never zero; no write control.
POS_JS = r"""
(function (AG) {
  'use strict';
  var esc = AG.esc, isObj = AG.isObj, R = AG.R;
  function refs(list) { return Array.isArray(list) && list.length ? list.map(function (e) { return '<span class="mono">' + esc(e.kind) + ' · ' + esc(e.id) + '</span>'; }).join('<br>') : '<span class="mute">none cited</span>'; }
  function unm(o) { return isObj(o) && Object.keys(o).length ? Object.keys(o).map(function (k) { return '<span class="mono">' + esc(k) + '</span>: ' + esc(o[k]); }).join('<br>') : '<span class="mute">none</span>'; }
  function metricsTable(sec, ctx) {
    var d = isObj(sec.data) ? sec.data : {}, m = isObj(d.metrics) ? d.metrics : {};
    var rows = Object.keys(m).map(function (k) { return m[k]; });
    if (!rows.length) return AG.genericBody(sec, ctx.rd);
    return '<p class="note">' + esc(d.rule || '') + ' · authority ' + esc(d.authority || '') + '</p>' + AG.table(rows, [
      {label: 'Metric', keys: ['name']},
      {label: 'Status', render: function (r) { return AG.statusPill(r.status || (r.value === null ? 'UNAVAILABLE' : 'MEASURED')); }},
      {label: 'Value', render: function (r) { return r.value === null || r.value === undefined ? AG.unk(r.why || 'unmeasured') : esc(r.value); }},
      {label: 'Numerator', render: function (r) { return r.numerator === null || r.numerator === undefined ? AG.unk() : esc(r.numerator); }},
      {label: 'Denominator', keys: ['denominator'], num: 1},
      {label: 'Why null', render: function (r) { return r.why ? esc(r.why) : ''; }},
      {label: 'Definition', keys: ['definition']}], {rd: ctx.rd});
  }
  function deskNote(sec) { var d = isObj(sec.data) ? sec.data : {}; return '<p class="note">The desk above draws these values: ' + esc(d.name) + ', ' + esc(d.role) + ', authority ' + esc(d.authority) + '; affordances ' + esc(d.affordances) + '.</p>'; }
  /* RECOMMENDATIONS ARE NOT ORDERS: Archer's shadow EXECUTE_NOW /
     SKIP_EXECUTION counts, then what was actually submitted, venue-
     acknowledged and filled by a BETTOR-originated order, each in its own
     cell; the legacy mirror's orders are named apart and are not Archer's. */
  function funnel(sec) {
    var d = isObj(sec.data) ? sec.data : null;
    if (!d) return AG.genericBody(sec);
    function cell(lbl, v, sub) {
      return '<div class="metric"><div class="lbl">' + esc(lbl) + '</div><div class="v">' + (typeof v === 'number' ? esc(v.toLocaleString('en-US')) : AG.unk(d.why || 'unmeasured')) + '</div>' + (sub ? '<div class="mute" style="font-size:12px">' + esc(sub) + '</div>' : '') + '</div>';
    }
    var sl = isObj(d.small_live) ? d.small_live : {};
    var ven = isObj(sl.venues) ? sl.venues : {};
    var lm = isObj(d.legacy_mirror_not_archer) ? d.legacy_mirror_not_archer : null;
    return '<p class="note"><b>Recommendations are not orders.</b> Archer is SHADOW ONLY: an EXECUTE_NOW recommendation sends nothing.</p>'
      + '<div class="metrics">' + cell('EXECUTE_NOW recommendations', d.execute_now_recommendations, 'shadow · not orders')
      + cell('SKIP_EXECUTION recommendations', d.skip_execution_recommendations, 'shadow')
      + cell('Orders actually submitted', d.bettor_orders_submitted, 'BETTOR-originated only')
      + cell('Venue-acknowledged orders', d.bettor_orders_venue_acknowledged, 'venue ack recorded')
      + cell('Fills', d.bettor_fills, 'venue fills') + '</div>'
      + '<p class="note">' + esc(d.orders_basis || '') + '</p>'
      + '<p><b>' + esc(sl.title || 'SMALL LIVE — BETTOR ORIGINATED') + ':</b> ' + AG.statusPill(sl.status || 'UNAVAILABLE') + ' <span class="mute">' + esc(sl.why || '') + '</span></p>'
      + '<p class="mute">Polymarket US: ' + esc((ven.polymarket_us || {}).state || '—') + ' · Kalshi: ' + esc((ven.kalshi || {}).state || '—') + '</p>'
      + (lm ? '<p class="mute">' + esc(lm.label || 'LEGACY MIRROR VALIDATION') + ' (not Archer\'s, not Small Live): ' + esc(lm.orders_sent_or_planned) + ' sent or planned · ' + esc(lm.venue_acknowledged) + ' venue-acknowledged · ' + esc(lm.fills) + ' fills</p>' : '');
  }
  function estimates(sec, ctx) {
    var rows = AG.rowsOf(sec.data, ['estimates', 'rows']);
    if (!rows.length) return AG.genericBody(sec, ctx.rd);
    return AG.table(rows, [
      {label: 'Estimate', keys: ['estimate_id']}, {label: 'Derek candidate', keys: ['decision_id']},
      {label: 'Recommendation (shadow)', render: function (r) { return AG.statusPill(r.recommendation); }},
      {label: 'Style', keys: ['execution_style']},
      {label: 'Theoretical edge', keys: ['theoretical_edge_pp'], num: 1}, {label: 'Spread', keys: ['spread_cost_pp'], num: 1},
      {label: 'Slippage', keys: ['expected_slippage_pp'], num: 1}, {label: 'Fees', keys: ['expected_fees_pp'], num: 1},
      {label: 'Adverse selection', keys: ['expected_adverse_selection_pp'], num: 1},
      {label: 'Execution loss', keys: ['expected_execution_loss_pp'], num: 1},
      {label: 'Net executable edge', keys: ['expected_net_executable_edge_pp'], num: 1},
      {label: 'Fill probability', keys: ['expected_fill_probability'], num: 1},
      {label: 'Time to fill (s)', keys: ['expected_time_to_fill_s'], num: 1},
      {label: 'Capital-hours', keys: ['expected_capital_hours'], num: 1},
      {label: 'Max size', keys: ['max_executable_qty'], num: 1}, {label: 'EV (USD)', keys: ['expected_executable_ev_usd'], num: 1},
      {label: 'Unmeasured', render: function (r) { return unm(r.unmeasured); }},
      {label: 'Reason', keys: ['recommendation_reason']}, {label: 'Estimated', keys: ['estimated_at'], u: 'ts'}], {rd: ctx.rd});
  }
  function outcomes(sec, ctx) {
    var rows = AG.rowsOf(sec.data, ['outcomes', 'rows']);
    if (!rows.length) return AG.genericBody(sec, ctx.rd);
    return AG.table(rows, [
      {label: 'Outcome', keys: ['outcome_id']}, {label: 'Derek candidate', keys: ['decision_id']},
      {label: 'Archer said', keys: ['recommendation']}, {label: 'Source', keys: ['source']},
      {label: 'Filled qty', keys: ['filled_qty'], num: 1}, {label: 'VWAP', keys: ['fill_vwap'], num: 1},
      {label: 'Predicted loss', keys: ['predicted_execution_loss_pp'], num: 1},
      {label: 'Realized loss', keys: ['realized_execution_loss_pp'], num: 1},
      {label: 'Naive loss', keys: ['naive_execution_loss_pp'], num: 1},
      {label: 'Decision→submit (s)', keys: ['decision_to_submit_s'], num: 1},
      {label: 'Decision→fill (s)', keys: ['decision_to_fill_s'], num: 1},
      {label: 'Capital-hours', keys: ['capital_hours'], num: 1},
      {label: 'Unmeasured', render: function (r) { return unm(r.unmeasured); }}], {rd: ctx.rd});
  }
  function reviews(sec, ctx) {
    var rows = AG.rowsOf(sec.data, ['reviews', 'rows']);
    if (!rows.length) return AG.genericBody(sec, ctx.rd);
    return rows.slice(0, 5).map(function (rv) {
      return '<h4 class="mono">' + esc(rv.review_id) + ' · Derek candidate ' + esc(rv.decision_id) + ' · ' + esc(rv.steps_recorded) + '/7 steps</h4>' + AG.table(rv.steps || [], [
        {label: '#', keys: ['seq']}, {label: 'Step', keys: ['step']}, {label: 'Agent', keys: ['agent']},
        {label: 'Status', render: function (r) { return AG.statusPill(r.status); }},
        {label: 'Question', keys: ['question']}, {label: 'Response', keys: ['response']},
        {label: 'Evidence', render: function (r) { return refs(r.evidence_refs); }},
        {label: 'Disagreement', render: function (r) { return isObj(r.disagreement) ? AG.kv(r.disagreement) : '<span class="mute">none</span>'; }},
        {label: 'Resolution', render: function (r) { return r.resolution ? esc(r.resolution) : '<span class="mute">none</span>'; }},
        {label: 'Experiment', render: function (r) { return isObj(r.experiment_ref) ? AG.kv(r.experiment_ref) : '<span class="mute">none</span>'; }},
        {label: 'Result', render: function (r) { return isObj(r.result) ? AG.kv(r.result) : '<span class="mute">pending</span>'; }}], {rd: ctx.rd});
    }).join('');
  }
  function sources(sec, ctx) {
    var rows = AG.rowsOf(sec.data, ['sources', 'rows']);
    if (!rows.length) return AG.genericBody(sec, ctx.rd);
    return AG.table(rows, [
      {label: 'Source', keys: ['source_id']}, {label: 'Name', keys: ['name']}, {label: 'Licensing', keys: ['licensing_class']},
      {label: 'Compliance', render: function (r) { return AG.statusPill(r.compliance_passed ? 'PASSED' : 'REFUSED'); }},
      {label: 'Failed checks', render: function (r) { var c = isObj(r.compliance) ? r.compliance : {}; var f = Object.keys(c).filter(function (k) { return c[k] !== true; }); return f.length ? esc(f.join(', ')) : '<span class="mute">none</span>'; }},
      {label: 'Access', keys: ['access_method']}, {label: 'Usage terms', keys: ['usage_terms']}], {rd: ctx.rd});
  }
  function features(sec, ctx) {
    var rows = AG.rowsOf(sec.data, ['features', 'rows']);
    if (!rows.length) return AG.genericBody(sec, ctx.rd);
    return AG.table(rows, [
      {label: 'Feature', keys: ['feature']}, {label: 'State', render: function (r) { return AG.statusPill(r.state); }},
      {label: 'Source', keys: ['source_id']}, {label: 'Licensing', keys: ['licensing_class']},
      {label: 'Expected mechanism', keys: ['expected_mechanism']}, {label: 'Predeclared hypothesis', keys: ['predeclared_hypothesis']},
      {label: 'Forward test', keys: ['tournament_id']}, {label: 'Observations', keys: ['observations'], num: 1},
      {label: 'Settled / minimum', render: function (r) { return esc(r.samples_settled) + ' / ' + esc(r.min_sample); }},
      {label: 'Incremental value', render: function (r) { return isObj(r.incremental_value) ? AG.kv(r.incremental_value) : AG.unk('not evaluated'); }},
      {label: 'Set by', keys: ['state_set_by']}], {rd: ctx.rd});
  }
  function tournaments(sec, ctx) {
    var rows = AG.rowsOf(sec.data, ['tournaments', 'rows']);
    if (!rows.length) return AG.genericBody(sec, ctx.rd);
    return AG.table(rows, [
      {label: 'Tournament', keys: ['tournament_id']}, {label: 'Baseline', keys: ['baseline']}, {label: 'Challenger', keys: ['challenger']},
      {label: 'Metric', keys: ['metric']}, {label: 'Min sample', keys: ['min_sample'], num: 1}, {label: 'Min improvement', keys: ['min_improvement'], num: 1},
      {label: 'Frozen', keys: ['frozen_at'], u: 'ts'}, {label: 'Frozen samples', keys: ['samples_frozen'], num: 1},
      {label: 'Settled', keys: ['samples_settled'], num: 1},
      {label: 'Verdict', render: function (r) { return r.verdict ? AG.statusPill(r.verdict) : '<span class="mute">not evaluated</span>'; }},
      {label: 'Improvement', keys: ['improvement'], num: 1}, {label: 'Evaluated by', keys: ['evaluated_by']}], {rd: ctx.rd});
  }
  function observations(sec, ctx) {
    var rows = AG.rowsOf(sec.data, ['observations', 'rows']);
    if (!rows.length) return AG.genericBody(sec, ctx.rd);
    return AG.table(rows, [
      {label: 'Observation', keys: ['observation_id']}, {label: 'Feature', keys: ['feature']}, {label: 'Event', keys: ['event_key']},
      {label: 'Value', keys: ['value'], num: 1}, {label: 'Label', keys: ['value_label']}, {label: 'Confidence', keys: ['confidence'], num: 1},
      {label: 'Freshness (s)', keys: ['freshness_s'], num: 1}, {label: 'Source time', keys: ['source_timestamp'], u: 'ts'},
      {label: 'Observed', keys: ['observed_timestamp'], u: 'ts'}, {label: 'Licensing', keys: ['licensing_class']},
      {label: 'Provenance', render: function (r) { return refs(r.provenance); }}], {rd: ctx.rd});
  }
  AG.SPECS.archer = {sections: [
    {key: 'status', title: 'Status & heartbeat', render: R.status},
    {key: 'authority', title: 'Authority · SHADOW ONLY', note: 'Archer holds no venue submission, order, cancel, capital, approval or promotion authority. Enforced in code and in the database (migration 217).'},
    {key: 'desk', title: 'Desk record', render: deskNote},
    {key: 'execution_funnel', title: 'Recommendations vs orders · SMALL LIVE — BETTOR ORIGINATED', render: funnel, wide: true, note: 'EXECUTE_NOW and SKIP_EXECUTION are shadow recommendations, never orders. Orders, venue acknowledgements and fills count only BETTOR-originated venue orders; legacy mirror orders never count.'},
    {key: 'current_estimates', title: 'Execution estimates (shadow)', render: estimates, wide: true, note: 'Never EXECUTE_NOW / REST_LIMIT / SPLIT when the expected executable EV is not positive. Null is unmeasured, never zero.'},
    {key: 'predicted_vs_realized', title: 'Predicted vs realized execution loss', render: outcomes, wide: true},
    {key: 'scorecard', title: 'Scorecard · numerator / denominator', render: metricsTable, wide: true, note: 'UNAVAILABLE until measured.'},
    {key: 'candidate_reviews', title: 'Candidate reviews · Derek → Karen → Scout → Archer → Allocator → Audrey → Xavier', render: reviews, wide: true},
    {key: 'runner', title: 'Runner'}
  ]};
  AG.SPECS.scout = {sections: [
    {key: 'status', title: 'Status & heartbeat', render: R.status},
    {key: 'authority', title: 'Authority · RESEARCH SHADOW ONLY', note: 'Scout holds no trade, portfolio, policy-approval or feature-promotion authority and cannot validate his own feature. Enforced in code and in the database (migration 217).'},
    {key: 'desk', title: 'Desk record', render: deskNote},
    {key: 'sources', title: 'Sources and the declared compliance check', render: sources, wide: true},
    {key: 'features', title: 'Feature registry', render: features, wide: true},
    {key: 'tournaments', title: 'Feature tournaments · PinnAPI vs PinnAPI + feature (frozen)', render: tournaments, wide: true},
    {key: 'observations', title: 'Observations', render: observations, wide: true},
    {key: 'scorecard', title: 'Scorecard · numerator / denominator', render: metricsTable, wide: true, note: 'UNAVAILABLE until measured.'},
    {key: 'runner', title: 'Runner'}
  ]};
})(AG);
"""

INDEX_JS = r"""
(function (AG) {
  'use strict';
  var esc = AG.esc, pick = AG.pick, isObj = AG.isObj;
  var IDS = ['DEREK', 'XAVIER', 'AUDREY', 'KAREN', 'ARCHER', 'SCOUT'];
  var TERMINAL = ['RELEASED', 'REJECTED', 'CLOSED_NO_CHANGE', 'CANCELLED', 'ROLLED_BACK'];
  var MANDATE = {DEREK: 'Finds and enters opportunities', XAVIER: 'Manages owned positions and exits', AUDREY: 'Audits both, talks with management, runs improvement', KAREN: 'Challenges all three with evidence; holds no authority', ARCHER: 'Estimates execution of Derek\'s candidates in shadow; no order or capital authority', SCOUT: 'Tests compliant external features prospectively; no trade or promotion authority'};
  function agentCard(a, id, tasks, rd) {
    var meta = AG.AGENT_META[id];
    if (!isObj(a)) return '<div class="panel acard ag-' + meta.kind + '" data-agent="' + id + '"><div class="ident"><div class="sigil">' + meta.letter + '</div><div><div class="role">' + esc(meta.role) + '</div><h1>' + id.charAt(0) + id.slice(1).toLowerCase() + '</h1></div></div>'
      + '<p class="why"><span>Why</span>The index returned no identity or status row for ' + id + '.</p>' + AG.stateBadge(null) + '<p><a href="/api/command/agents/' + meta.kind + '/page">Open workspace →</a></p></div>';
    var hbAt = AG.toEpoch(a.last_heartbeat_at);
    var mine = tasks.filter(function (t) { return isObj(t) && String(t.assignee || '').toUpperCase() === id && TERMINAL.indexOf(t.status) < 0; });
    var ct = pick(a, ['current_tasks']);
    if (Array.isArray(ct)) mine = ct;
    return '<div class="panel acard ag-' + meta.kind + '" data-agent="' + id + '">'
      + '<div class="ident"><div class="sigil" aria-hidden="true">' + meta.letter + '</div><div><div class="role">' + esc(meta.role) + '</div><h1>' + esc(a.display_name || id) + '</h1></div></div>'
      + '<p class="mandate" style="margin:0">' + (a.mandate ? esc(a.mandate) : AG.unk('mandate not recorded')) + '</p>'
      + AG.versionChips(a)
      + '<div class="statehead">' + AG.stateBadge(a.state) + '<span class="meta">heartbeat ' + (hbAt === null ? 'never recorded' : AG.dur(Math.max(0, (rd || hbAt) - hbAt)) + ' ago') + '</span></div>'
      + '<div class="contemplating" style="margin:0"><span class="lbl">Activity · persisted</span><p>' + (a.activity ? esc(a.activity) : '<span class="mute">none recorded</span>') + '</p></div>'
      + '<div class="metrics" style="margin:0"><div class="metric"><div class="lbl">Cadence</div><div class="v" style="font-size:14px">' + AG.cadenceText(a.cadence, rd) + '</div></div>'
      + '<div class="metric"><div class="lbl">Runs</div><div class="v">' + (typeof a.runs === 'number' ? a.runs.toLocaleString('en-US') : AG.unk()) + '</div></div>'
      + '<div class="metric"><div class="lbl">Errors</div><div class="v">' + (typeof a.errors === 'number' ? a.errors.toLocaleString('en-US') : AG.unk()) + '</div></div></div>'
      + '<div><span class="lbl">Current tasks · ' + mine.length + '</span>' + (mine.length ? '<ul style="margin:6px 0 0;padding-left:18px">' + mine.slice(0, 5).map(function (t) { return '<li>' + esc(isObj(t) ? (t.title || t.task_id) : t) + (isObj(t) && t.status ? ' <span class="mono mute">' + esc(t.status) + '</span>' : '') + '</li>'; }).join('') + '</ul>' : '<p class="none" style="margin:4px 0">no open task assigned</p>') + '</div>'
      + '<p style="margin:0"><a class="btn" href="/api/command/agents/' + meta.kind + '/page">Open ' + esc(a.display_name || id) + ' workspace →</a></p></div>';
  }
  function index(json) {
    json = isObj(json) ? json : {};
    var rd = AG.toEpoch(json.read_at);
    var agents = Array.isArray(json.agents) ? json.agents : [];
    var tasksSec = AG.normList(json.tasks, 'tasks');
    var tasks = AG.rowsOf(tasksSec.data, ['tasks', 'rows', 'items']);
    var h = '<div class="flow" aria-label="operating model">'
      + '<div class="n ag-derek"><span class="lbl" style="color:var(--acc)">Derek</span><b>Discovery · entry</b><span class="note">evaluates, decides, plans; orders only via the one execution path</span></div>'
      + '<div class="arr">confirmed fills only →<br>handoff</div>'
      + '<div class="n ag-xavier"><span class="lbl" style="color:var(--acc)">Xavier</span><b>Positions · exits</b><span class="note">compares HOLD, EXIT, REDUCE and every hedge; services on its own cadence</span></div>'
      + '<div class="arr">decisions + captured alternatives →<br>audit</div>'
      + '<div class="n ag-audrey"><span class="lbl" style="color:var(--acc)">Audrey</span><b>Audit · chat · improvement</b><span class="note">reports, directives, tasks; proposer ≠ evaluator ≠ approver</span></div></div>';
    h += '<div class="agents">' + IDS.map(function (id) {
      var a = agents.filter(function (x) { return isObj(x) && String(x.agent_id || '').toUpperCase() === id; })[0];
      return agentCard(a, id, tasks, rd);
    }).join('') + '</div>';
    var ho = AG.normList(json.handoffs, 'handoffs');
    var hoRows = AG.rowsOf(ho.data, ['handoffs', 'rows', 'items']);
    var hoBody = ho.status === 'OK' ? (hoRows.length ? '<p class="note">Derek → Xavier. Only confirmed fills transfer; outstanding quantity is not a position.</p>' + AG.table(hoRows, [
      {label: 'Entry intent', keys: ['entry_intent_id', 'intent_id']}, {label: 'Group', keys: ['portfolio_group_id']}, {label: 'Owner', keys: ['owner_agent']},
      {label: 'Ordered', keys: ['ordered_qty'], u: 'qty', num: 1}, {label: 'Confirmed', keys: ['confirmed_qty'], u: 'qty', num: 1}, {label: 'Outstanding', keys: ['outstanding_qty'], u: 'qty', num: 1},
      {label: 'Fill', render: function (r) { return AG.R.fillBar(pick(r, ['ordered_qty']), pick(r, ['confirmed_qty'])); }},
      {label: 'Last fill', keys: ['last_fill_at'], u: 'ts'}, {label: 'Evidence', keys: ['evidence']}], {rd: rd}) : AG.genericBody(ho, rd))
      : ho.status === 'EMPTY' ? '<p class="emptyline">No handoff has happened. Nothing has been transferred to Xavier.</p>' : '';
    var taskBody = tasksSec.status === 'OK' ? (tasks.length ? AG.table(tasks, [
      {label: 'Task', keys: ['task_id']}, {label: 'Title', keys: ['title']}, {label: 'Assignee', keys: ['assignee']}, {label: 'Created by', keys: ['created_by']},
      {label: 'Kind', keys: ['kind']}, {label: 'Status', keys: ['status']}, {label: 'Updated', keys: ['updated_at'], u: 'ts'}, {label: 'Evidence', keys: ['evidence']}], {rd: rd}) : AG.genericBody(tasksSec, rd)) : '';
    h += '<div class="grid">' + AG.card(1, {key: 'handoffs', title: 'Handoff summary', wide: true}, ho, hoBody, rd)
      + AG.card(2, {key: 'tasks', title: 'Agent tasks', wide: true}, tasksSec, taskBody, rd) + '</div>';
    return h;
  }
  AG.index = index;
})(AG);
"""

# THE TRACE: follows ids across the four reads -- Derek entry decision ->
# handoff -> Xavier decisions for the group -> Audrey audit items -> directive
# -> task -> improvement candidate. Pure; the DOM half is in BOOT_JS.
TRACE_JS = r"""
(function (AG) {
  'use strict';
  var esc = AG.esc, isObj = AG.isObj, pick = AG.pick, KEYS = AG.LINK_KEYS;
  var EV_KIND = [[/handoff|funded_intent|entry_intent/i, 'entry_intent_id'], [/xavier_decision/i, 'xavier_decision_id'], [/agent_task|(^|_)task/i, 'task_id'],
    [/directive/i, 'directive_id'], [/candidate|policy_version/i, 'candidate_id'], [/agent_decision/i, 'decision_ref'], [/portfolio_group/i, 'portfolio_group_id']];
  var STAGES = [['DEREK_DECISION', 'Derek entry decision', 'derek'], ['HANDOFF', 'Handoff \u00b7 agent_position_handoffs', 'derek'], ['XAVIER_DECISION', 'Xavier decisions for the group', 'xavier'],
    ['AUDIT', 'Audrey audit items', 'audrey'], ['DIRECTIVE', 'Directive', 'audrey'], ['TASK', 'Task', 'audrey'], ['CANDIDATE', 'Improvement candidate', 'audrey']];
  var SRC_STAGE = {derek: {decisions: 'DEREK_DECISION', handoffs: 'HANDOFF'}, index: {handoffs: 'HANDOFF', tasks: 'TASK'},
    xavier: {reviews: 'XAVIER_DECISION', positions: 'XAVIER_DECISION', alternatives: 'XAVIER_DECISION'},
    audrey: {findings: 'AUDIT', outcomes: 'AUDIT', daily_reports: 'AUDIT', directives: 'DIRECTIVE', tasks: 'TASK', candidates: 'CANDIDATE', evaluations: 'CANDIDATE', releases: 'CANDIDATE'}};
  function linksOf(rec, src) {
    var out = {};
    function add(k, v) { v = String(v); (out[k] = out[k] || []); if (out[k].indexOf(v) < 0) out[k].push(v); }
    KEYS.forEach(function (k) { var v = rec[k]; if (typeof v === 'string' || typeof v === 'number') add(k, v); });
    if (src === 'xavier' && (typeof rec.decision_id === 'string' || typeof rec.decision_id === 'number')) add('xavier_decision_id', rec.decision_id);
    var ev = [].concat(Array.isArray(rec.evidence) ? rec.evidence : [], Array.isArray(rec.evidence_refs) ? rec.evidence_refs : []);
    ev.forEach(function (e) {
      if (!isObj(e) || e.id === undefined || e.id === null) return;
      for (var i = 0; i < EV_KIND.length; i++) if (EV_KIND[i][0].test(String(e.kind || ''))) { add(EV_KIND[i][1], e.id); break; }
    });
    return out;
  }
  function collect(outs) {
    var recs = [];
    function walk(v, src, sec, depth) {
      if (depth > 6 || v === null || typeof v !== 'object') return;
      if (Array.isArray(v)) { v.forEach(function (x) { walk(x, src, sec, depth + 1); }); return; }
      var l = linksOf(v, src);
      if (Object.keys(l).length) { recs.push({src: src, sec: sec, stage: (SRC_STAGE[src] || {})[sec] || null, rec: v, links: l}); return; }
      Object.keys(v).forEach(function (k) { if (k !== 'evidence' && k !== 'evidence_refs') walk(v[k], src, sec, depth + 1); });
    }
    ['index', 'derek', 'xavier', 'audrey'].forEach(function (src) {
      var o = outs && outs[src];
      if (!o || o.kind !== 'OK' || !isObj(o.json)) return;
      if (src === 'index') { ['handoffs', 'tasks'].forEach(function (k) { var x = o.json[k]; walk(isObj(x) && x.data !== undefined ? x.data : x, 'index', k, 0); }); return; }
      var ss = isObj(o.json.sections) ? o.json.sections : {};
      Object.keys(ss).forEach(function (k) { if (isObj(ss[k])) walk(ss[k].data, src, k, 0); });
    });
    return recs;
  }
  function chain(recs, key, value) {
    var ids = {}; KEYS.forEach(function (k) { ids[k] = {}; });
    if (!ids[key]) return [];
    ids[key][String(value)] = true;
    var picked = [], taken = [];
    for (var round = 0; round < 8; round++) {
      var grew = false;
      recs.forEach(function (r, i) {
        if (taken[i]) return;
        var via = null;
        Object.keys(r.links).some(function (k) { return r.links[k].some(function (v) { if (ids[k] && ids[k][v]) { via = k + ' = ' + v; return true; } return false; }); });
        if (!via) return;
        taken[i] = true; grew = true;
        picked.push({src: r.src, sec: r.sec, stage: r.stage, rec: r.rec, links: r.links, via: via});
        Object.keys(r.links).forEach(function (k) { r.links[k].forEach(function (v) { if (ids[k]) ids[k][v] = true; }); });
      });
      if (!grew) break;
    }
    return picked;
  }
  function seeds(recs) {
    var out = [], seen = {};
    function add(k, v) { var s = k + ':' + v; if (!seen[s]) { seen[s] = 1; out.push({key: k, value: v}); } }
    recs.forEach(function (r) { if (r.stage === 'DEREK_DECISION' || r.stage === 'HANDOFF') (r.links.entry_intent_id || []).forEach(function (v) { add('entry_intent_id', v); }); });
    recs.forEach(function (r) { if (r.stage === 'XAVIER_DECISION' || r.stage === 'HANDOFF') (r.links.portfolio_group_id || []).forEach(function (v) { add('portfolio_group_id', v); }); });
    recs.forEach(function (r) { if (r.stage === 'DIRECTIVE') (r.links.directive_id || []).forEach(function (v) { add('directive_id', v); }); });
    return out;
  }
  function parseSeed(str) {
    if (typeof str !== 'string') return null;
    var i = str.indexOf(':'); if (i < 1) return null;
    var k = str.slice(0, i), v = decodeURIComponent(str.slice(i + 1));
    return KEYS.indexOf(k) >= 0 && v ? {key: k, value: v} : null;
  }
  function summary(r, rd) {
    var o = r.rec, ids = KEYS.filter(function (k) { return r.links[k]; }).map(function (k) { return '<span class="mono">' + esc(k) + '</span> ' + AG.fmt(k, r.links[k][0]) + (r.links[k].length > 1 ? ' +' + (r.links[k].length - 1) : ''); }).join('<br>');
    var what = pick(o, ['verdict', 'chosen_action', 'action', 'title', 'text', 'finding', 'state', 'status', 'kind']);
    var t = pick(o, ['decided_at', 'reviewed_at', 'handoff_at', 'at', 'created_at', 'updated_at', 'evaluated_at']);
    return '<div class="rec">' + (what !== undefined ? '<b>' + esc(typeof what === 'object' ? JSON.stringify(what) : what) + '</b><br>' : '') + ids
      + (t !== undefined ? '<br>' + AG.ts(t, rd) : '') + '<div class="note" style="margin:3px 0">linked by ' + esc(r.via || '') + ' \u00b7 ' + esc(r.src) + '.' + esc(r.sec) + '</div>' + (o.evidence ? AG.evidence(o.evidence) : '') + '</div>';
  }
  function render(outs, seedStr) {
    var recs = collect(outs), ss = seeds(recs);
    var status = ['index', 'derek', 'xavier', 'audrey'].map(function (k) {
      var o = outs && outs[k];
      return '<span class="chip">' + k + ' <b>' + (o ? (o.kind === 'OK' ? AG.readLine(o.json) : o.kind.replace('_', ' ')) : 'not read') + '</b></span>';
    }).join('');
    var h = '<div class="tracebar"><span class="lbl">Reads</span>' + status + '</div>';
    var seed = parseSeed(seedStr) || ss[0] || null;
    if (!seed) return h + '<div class="plain"><p class="big">Nothing to trace yet.</p><p>No record in these reads carries an entry intent, position group or directive id, so there is no chain to follow. This is not a result.</p></div>';
    var cur = seed.key + ':' + seed.value;
    h += '<div class="tracebar"><label class="lbl" for="trace-seed">Follow</label><select id="trace-seed">' + (ss.some(function (x) { return x.key + ':' + x.value === cur; }) ? '' : '<option value="' + esc(cur) + '">' + esc(cur) + '</option>')
      + ss.map(function (x) { var v = x.key + ':' + x.value; return '<option value="' + esc(v) + '"' + (v === cur ? ' selected' : '') + '>' + esc(v) + '</option>'; }).join('') + '</select></div>';
    var got = chain(recs, seed.key, seed.value);
    var rd = null;
    h += '<div class="trace-stages" data-seed="' + esc(cur) + '">' + STAGES.map(function (st, i) {
      var list = got.filter(function (r) { return r.stage === st[0]; });
      var o = outs && outs[st[2]];
      var why = !o || o.kind !== 'OK' ? (st[2].charAt(0).toUpperCase() + st[2].slice(1)) + ' endpoint ' + (o ? (o.kind === 'NOT_DEPLOYED' ? 'is not deployed in this build' : o.kind === 'LOCKED' ? 'is locked' : 'is unavailable') : 'was not read') + ': this link cannot be followed.'
        : 'No record in these reads links to this chain. A missing link is not evidence that nothing happened.';
      return '<div class="tstage' + (list.length ? '' : ' none') + '" data-stage="' + st[0] + '" data-count="' + list.length + '"><h5>' + (i + 1) + ' \u00b7 ' + esc(st[1]) + '</h5>'
        + (list.length ? list.slice(0, 8).map(function (r) { return summary(r, rd); }).join('') + (list.length > 8 ? '<p class="note">' + (list.length - 8) + ' more linked</p>' : '') : '<p class="note" style="margin:0">' + esc(why) + '</p>') + '</div>';
    }).join('') + '</div>';
    return h;
  }
  AG.trace = {collect: collect, chain: chain, seeds: seeds, render: render, parseSeed: parseSeed, STAGES: STAGES};
})(AG);
"""

# The DOM half: fetch, render, refresh, write buttons and the chat form.
BOOT_JS = r"""
(function (AG) {
  'use strict';
  var kind = document.body.getAttribute('data-kind');
  var url = AG.ENDPOINTS[kind];
  var app = document.getElementById('app'), readat = document.getElementById('readat');
  var busy = false;
  async function refresh() {
    if (busy) return; busy = true;
    var open = [].map.call(document.querySelectorAll('details[open]'), function (d) { var c = d.closest('[id]'); return c && c.id; });
    var o = await AG.load(url, fetch.bind(window));
    if (o.kind !== 'OK') { app.innerHTML = AG.gate(o, url); readat.textContent = o.kind === 'LOCKED' ? 'locked' : o.kind.toLowerCase().replace('_', ' '); busy = false; if (typeof CC !== 'undefined' && CC.onWorkspaceFail) CC.onWorkspaceFail(o); return; }
    app.innerHTML = kind === 'index' ? AG.index(o.json) : AG.workspace(kind, o.json);
    if (typeof CC !== 'undefined' && CC.onWorkspace) CC.onWorkspace(kind, o.json);
    open.forEach(function (id) { var el = id && document.getElementById(id); if (el) { var d = el.querySelector('details'); if (d) d.open = true; } });
    readat.textContent = AG.readLine(o.json);
    busy = false;
  }
  document.getElementById('refresh').addEventListener('click', refresh);
  document.addEventListener('click', async function (ev) {
    var b = ev.target.closest && ev.target.closest('button[data-write]');
    if (!b) return;
    if (!window.confirm(b.textContent + ' — this is a write and needs the OPERATOR credential. Send it?')) return;
    var row = b.closest('.wr'), msg = row && row.querySelector('[data-wmsg]');
    b.disabled = true;
    var body = {}; try { body = JSON.parse(b.getAttribute('data-body') || '{}'); } catch (_) {}
    var r = await AG.write(b.getAttribute('data-write'), fetch.bind(window), body);
    if (msg) msg.textContent = r.text;
    b.disabled = false;
    if (r.kind === 'OK') refresh();
  });
  var form = document.getElementById('chat-form');
  if (form && AG.chat) {
    var log = document.getElementById('chat-log'), input = document.getElementById('chat-in'), cid = null;
    form.addEventListener('submit', async function (ev) {
      ev.preventDefault();
      var q = input.value.trim(); if (!q) return;
      log.insertAdjacentHTML('beforeend', '<div class="msg q"><div class="who">MANAGEMENT</div><p>' + AG.esc(q) + '</p></div>');
      input.value = ''; form.querySelector('button[type=submit]').disabled = true;
      if (typeof CC !== 'undefined' && CC.chatPending) CC.chatPending();
      var r = await AG.chat.ask(fetch.bind(window), q, cid);
      if (r.conversationId) cid = r.conversationId;
      log.insertAdjacentHTML('beforeend', r.html); log.scrollTop = log.scrollHeight;
      if (typeof CC !== 'undefined') { if (r.kind === 'OK' && CC.chatReply) CC.chatReply(String(r.html || '').replace(/<[^>]*>/g, '').length); else if (CC.chatDone) CC.chatDone(); }
      form.querySelector('button[type=submit]').disabled = false;
    });
  }
  // ── trace view ──────────────────────────────────────────────────
  var tpanel = document.getElementById('trace'), tbody = document.getElementById('trace-body'), tcache = null, tat = 0;
  async function traceLoad() {
    if (tcache && Date.now() - tat < 30000) return tcache;
    var keys = ['index', 'derek', 'xavier', 'audrey'];
    var res = await Promise.all(keys.map(function (k) { return AG.load(AG.ENDPOINTS[k], fetch.bind(window)); }));
    tcache = {}; keys.forEach(function (k, i) { tcache[k] = res[i]; }); tat = Date.now();
    return tcache;
  }
  async function openTrace(seed) {
    tpanel.hidden = false; tbody.innerHTML = '<p class="boot">Reading the four agent endpoints&#8230;</p>';
    tpanel.scrollIntoView({block: 'start'});
    var outs = await traceLoad();
    tbody.innerHTML = AG.trace.render(outs, seed);
    var sel = document.getElementById('trace-seed');
    if (sel) sel.addEventListener('change', function () { location.hash = 'trace=' + sel.value; });
  }
  function fromHash() {
    var h = decodeURIComponent(location.hash || '');
    if (h.indexOf('#trace') === 0) openTrace(h.indexOf('=') > 0 ? h.slice(h.indexOf('=') + 1) : null);
  }
  window.addEventListener('hashchange', fromHash);
  document.getElementById('trace-btn').addEventListener('click', function () { if (location.hash.indexOf('#trace') === 0) fromHash(); else location.hash = 'trace'; });
  document.getElementById('trace-close').addEventListener('click', function () { tpanel.hidden = true; history.replaceState(null, '', location.pathname); });
  refresh();
  fromHash();
  setInterval(function () { if (!document.hidden) refresh(); }, 45000);
})(AG);
"""

CHAT_PANEL_HTML = r"""
<section class="card wide" id="chat" style="margin-top:16px" aria-label="Management chat with Audrey">
<header><span class="idx">&gt;_</span><div class="ttl"><h3>Management chat with Audrey</h3><code class="key">POST /api/command/agents/audrey/chat</code></div></header>
<p class="note">Answers come from the Audrey chat service, not from this page. Every answer states how it was produced (provider mode and disclosure) and cites the records it used; citations open the record. A directive proposed here takes effect only when confirmed with the OPERATOR credential.</p>
<div class="chatlog" id="chat-log" aria-live="polite"></div>
<form class="chatform" id="chat-form"><textarea id="chat-in" maxlength="4000" placeholder="Ask about Derek&#8217;s or Xavier&#8217;s records, or give Audrey a directive&#8230;" aria-label="Message to Audrey"></textarea><button class="primary" type="submit">Send</button></form>
</section>
"""

# ═════════════════════════════════════════════════════════════════════
# TALK TO DEREK / XAVIER / AUDREY -- the management conversation on every
# agent page (persona chat + speech + the microphone), always visible above
# the records, never inside the collapsed funded section.
# ═════════════════════════════════════════════════════════════════════

TALK_NAMES = {"derek": "Derek", "xavier": "Xavier", "audrey": "Audrey",
              "karen": "Karen", "archer": "Archer", "scout": "Scout"}

TALK_PANEL_HTML = r"""
<section class="card wide talk" id="talk" data-agent="%%AGENT%%" aria-label="Talk to %%NAME%%">
<header><span class="idx">&#9673;</span><div class="ttl"><h3>Talk to %%NAME%%</h3><code class="key">%%NAME%% answers from the paper records, the active policy and stored lessons &#183; paper only</code></div>
<div class="talk-ctl"><button type="button" id="talk-mute" aria-pressed="false" title="Speak replies aloud">&#128266; Voice on</button><button type="button" id="talk-stop" disabled>Stop</button><button type="button" id="talk-new">New conversation</button></div></header>
<p class="talk-note">A conversation never changes a policy or places an order. %%NAME%% can explain and propose; a policy changes only by an owner decision or an evaluated proposal a named human activates.</p>
<div class="talk-log" id="talk-log" aria-live="polite"><p class="talk-empty" id="talk-empty">Ask %%NAME%% anything about %%SCOPE%%. Try: <button type="button" class="talk-sugg">%%SUGG1%%</button> <button type="button" class="talk-sugg">%%SUGG2%%</button></p></div>
<div class="talk-state" id="talk-state" role="status" hidden></div>
<form class="talk-form" id="talk-form"><textarea id="talk-in" maxlength="4000" rows="2" placeholder="Type a question for %%NAME%%&#8230;" aria-label="Message to %%NAME%%"></textarea>
<div class="talk-btns"><button type="button" id="talk-mic" class="talk-mic" aria-pressed="false" title="Record a spoken question">&#127908; Speak</button><button class="primary" type="submit" id="talk-send">Send</button></div></form>
</section>
"""

TALK_SCOPE = {
    "derek": ("entries, markets, prices, edges, fees and why a candidate "
              "was refused",
              "What is the active entry rule?",
              "Why did nothing qualify today?"),
    "xavier": ("owned positions, protection, exits and exposure",
               "What are you managing right now?",
               "What would you do with a new position?"),
    "audrey": ("audits, reconciliation, agent performance and proposals",
               "Does the ledger reconcile?",
               "What have you learned so far?"),
    "karen": ("her challenges, the evidence they cite, the peer responses "
              "and the independent evaluations",
              "What are you challenging right now?",
              "Which of your challenges were rejected, and why?"),
    "archer": ("his shadow execution estimates, the costs between decision "
              "and fill, and predicted against realized execution loss",
              "What are you estimating right now?",
              "How much edge did execution cost on the last fill?"),
    "scout": ("his sources and their compliance, the features under test "
              "and their forward tests",
              "Which features are under test?",
              "Why was the weather source refused?"),
}

TALK_CSS = r"""
.talk{margin:16px 0;border:1px solid #2e6fd8;background:linear-gradient(180deg,#0e1726,#0b111c)}
.talk header{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.talk .talk-ctl{margin-left:auto;display:flex;gap:6px;flex-wrap:wrap}
.talk .talk-ctl button,.talk .talk-btns button,.talk-sugg,.talk-replay{font:inherit;font-size:12.5px;padding:6px 10px;border-radius:8px;border:1px solid #33465f;background:#132034;color:#dbe7f7;cursor:pointer}
.talk .talk-ctl button[aria-pressed=true]{background:#2a1c10;border-color:#8a6d2a}
.talk button:disabled{opacity:.45;cursor:default}
.talk-note{font-size:12px;color:#9fb2c8;margin:6px 0 8px}
.talk-log{max-height:420px;overflow-y:auto;display:flex;flex-direction:column;gap:8px;padding:4px 2px}
.talk-empty{color:#9fb2c8;font-size:13px;line-height:1.8}
.talk-msg{max-width:92%;padding:9px 11px;border-radius:10px;font-size:14px;line-height:1.5;overflow-wrap:anywhere}
.talk-msg.q{align-self:flex-end;background:#1d3557;color:#eef4ff}
.talk-msg.a{align-self:flex-start;background:#121c2b;border:1px solid #26354a;color:#e3ebf5}
.talk-msg .who{font-size:10.5px;letter-spacing:.08em;color:#8fb6e8;margin-bottom:3px;text-transform:uppercase}
.talk-msg .meta{font-size:11.5px;color:#93a6bd;margin-top:6px}
.talk-msg .meta details{margin-top:4px}.talk-msg .meta li{margin:2px 0}
.talk-msg.err{border-color:#7a2f2f;background:#251314}
.talk-state{font-size:12.5px;padding:7px 10px;border-radius:8px;background:#221b0e;color:#f3e2b5;border:1px solid #8a6d2a;margin:6px 0}
.talk-form{display:flex;gap:8px;align-items:flex-end;margin-top:8px}
.talk-form textarea{flex:1;min-width:0;font:inherit;font-size:16px;padding:9px;border-radius:9px;border:1px solid #33465f;background:#0b1320;color:#eef4ff;resize:vertical}
.talk-btns{display:flex;gap:6px;flex-shrink:0}
.talk-mic[aria-pressed=true]{background:#5a1717;border-color:#c24b4b;color:#fff}
@media(max-width:640px){.talk-form{flex-direction:column;align-items:stretch}.talk-btns{justify-content:flex-end}.talk-msg{max-width:100%}}
"""

TALK_JS = r"""
(function () {
  var root = document.getElementById('talk'); if (!root) return;
  var agent = root.getAttribute('data-agent'), NAME = agent.charAt(0).toUpperCase() + agent.slice(1);
  var BASE = '/api/command/agents/' + agent;
  var $ = function (id) { return document.getElementById(id); };
  var log = $('talk-log'), input = $('talk-in'), form = $('talk-form'), stateEl = $('talk-state');
  var send = $('talk-send'), mic = $('talk-mic'), muteB = $('talk-mute'), stopB = $('talk-stop');
  var cid = null, muted = false, audio = null, rec = null, chunks = [], busy = false, speechAbort = null, speechEpoch = 0, waitTimer = null, activeRequest = null, chatEpoch = 0, readySpeech = null;
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]; }); }
  function setState(t) { if (!t) { stateEl.hidden = true; stateEl.textContent = ''; return; } stateEl.hidden = false; stateEl.textContent = t; }
  function paintMute() { muteB.setAttribute('aria-pressed', muted ? 'true' : 'false'); muteB.innerHTML = muted ? '&#128263; Voice off' : '&#128266; Voice on'; }
  paintMute();
  function avatar(fn, a) { try { if (typeof CC !== 'undefined' && CC[fn]) CC[fn](a); } catch (_) {} }
  function clearEmpty() { var e = $('talk-empty'); if (e) e.remove(); }
  function metaHtml(m) {
    var out = [], p = m.provider || {}, cs = m.context_supplied || null;
    if (p.disclosure) out.push(esc(p.disclosure));
    if (cs) {
      var parts = [];
      parts.push(cs.model_composed ? (cs.prior_turns + ' earlier turn' + (cs.prior_turns === 1 ? '' : 's') + ' of this conversation sent to the model') : 'records-only answer (no model)');
      parts.push(cs.facts + ' record facts');
      if (cs.policy) parts.push('active policy ' + esc(cs.policy.version_id) + ' (' + cs.policy.threshold_pp + ' pp)');
      parts.push((cs.lessons || []).length + ' stored lesson' + ((cs.lessons || []).length === 1 ? '' : 's'));
      out.push('Memory supplied: ' + parts.join(' · '));
      if((cs.lessons||[]).length)out.push('<details><summary>Lessons available to this answer</summary><ul>'+(cs.lessons||[]).map(function(l){return '<li>'+esc(l.kind)+' · '+esc(l.lesson_id)+(l.evidence_category?' · '+esc(l.evidence_category):'')+(l.historical?' · historical; needs revalidation':'')+'</li>';}).join('')+'</ul></details>');
    }
    var facts = m.facts || m.citations || [];
    var li = (m.facts || []).slice(0, 12).map(function (f) { return '<li><b>' + esc(f.fact_id) + '</b> ' + esc(f.source) + ' · ' + esc(f.record_id) + ': ' + esc(f.text || f.field) + '</li>'; }).join('');
    var html = out.map(function (x) { return '<div>' + x + '</div>'; }).join('');
    if (li) html += '<details><summary>Records cited (' + (m.facts || []).length + ')</summary><ul>' + li + '</ul></details>';
    if ((m.missing_evidence || []).length) html += '<details><summary>Not in the records</summary><ul>' + m.missing_evidence.slice(0, 8).map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('') + '</ul></details>';
    return html;
  }
  function addQ(text) { clearEmpty(); log.insertAdjacentHTML('beforeend', '<div class="talk-msg q"><div class="who">You</div>' + esc(text) + '</div>'); log.scrollTop = log.scrollHeight; }
  function addA(m) {
    clearEmpty();
    var id = m.message_id || '';
    var div = document.createElement('div'); div.className = 'talk-msg a'; div.setAttribute('data-mid', id);
    div.innerHTML = '<div class="who">' + esc(NAME) + '</div><div class="body">' + esc(m.answer || m.body || '').replace(/\n/g, '<br>') + '</div>' +
      '<div class="meta"><details><summary>Memory &amp; evidence</summary>' + metaHtml(m) + '</details>' + (id ? ' <button type="button" class="talk-replay" data-mid="' + esc(id) + '">&#9654; Play</button>' : '') + '</div>';
    log.appendChild(div); log.scrollTop = log.scrollHeight; return div;
  }
  function addErr(t) { clearEmpty(); log.insertAdjacentHTML('beforeend', '<div class="talk-msg a err"><div class="who">' + esc(NAME) + ' · not answered</div>' + esc(t) + '</div>'); log.scrollTop = log.scrollHeight; }
  function why(status, body) {
    if (status === 401 || status === 403) return 'SIGN-IN REQUIRED: your COMMAND session is missing or has expired. Sign in on the homepage, then try again.';
    var d = (body && (body.detail || body)) || {};
    var r = d.reason || d.status || d.error || ('HTTP ' + status);
    var pd = d.provider_diagnostic || null;
    var extra = pd ? (' — provider HTTP ' + (pd.http_status || '?') + (pd.provider_error_status ? ' ' + pd.provider_error_status : '') + (pd.required_permission ? ' (needs ' + pd.required_permission + ')' : '')) : '';
    return String(r) + extra + (d.display ? ' — ' + d.display : '');
  }
  async function readJson(r) { try { return await r.json(); } catch (_) { return null; } }
  async function resume() {
    var initialEpoch=chatEpoch;
    try {
      // the conversation is kept by the server, per agent and signed-in
      // role -- nothing is stored in this browser
      var r = await fetch(BASE + '/persona/latest-conversation', {credentials: 'same-origin', cache: 'no-store'});
      if (r.status === 404) { if(initialEpoch===chatEpoch)cid = null; return; }
      if (!r.ok) { setState('Earlier conversation not loaded: ' + why(r.status, await readJson(r))); return; }
      var t = await r.json();
      if(initialEpoch!==chatEpoch || busy)return;
      cid = (t.conversation || {}).conversation_id || null;
      (t.messages || []).forEach(function (m) {
        if (m.role === 'USER') addQ(m.body); else if (m.role === 'ASSISTANT') addA(m);
      });
      if ((t.messages || []).length) setState('Resumed your conversation with ' + NAME + ' (' + t.messages.length + ' messages).');
    } catch (e) { setState('Earlier conversation not loaded: ' + (e && e.message || e)); }
  }
  function stopAudio() { readySpeech=null; speechEpoch++; if(speechAbort){speechAbort.abort();speechAbort=null;} if (audio) { try { audio.pause(); } catch (_) {} audio = null; } stopB.disabled = true; avatar('chatDone'); }
  async function speak(mid, manual) {
    if (!mid || (muted && !manual)) return;
    if(manual&&readySpeech&&readySpeech.mid===mid){try{await readySpeech.play();setState(NAME+' is speaking.');}catch(e){setState('Playback unavailable in this browser. Your complete text is available.');}return;}
    stopAudio(); var generation=speechEpoch; speechAbort=new AbortController(); var signal=speechAbort.signal;
    stopB.disabled=false;setState('Connecting '+NAME+"'s voice…");
    var voiceTimeout=setTimeout(function(){if(generation===speechEpoch){speechAbort.abort();stopB.disabled=true;setState('Voice request timed out. Your text reply remains available; press Play to retry.');}},45000);
    try {
      var r = await fetch(BASE + '/speech', {method: 'POST', credentials: 'same-origin', signal:signal, headers: {'Content-Type': 'application/json'}, body: JSON.stringify({message_id: mid})});
      if(generation!==speechEpoch)return;
      if (!r.ok) { var errorBody=await readJson(r);if(generation!==speechEpoch)return;setState('Voice unavailable: ' + why(r.status, errorBody) + '. The text reply above is complete.');stopB.disabled=true;return; }
      audio=new Audio();var speaking=audio;
      speaking.onended=function(){if(generation===speechEpoch){stopAudio();setState(null);}};
      // The decoder owns errors while it attempts native playback recovery.
      import('/api/command/agents/static/cc_avatar.js').then(function(m){if(generation===speechEpoch)m.attachAudio(speaking);}).catch(function(){});
      var playback=await window.CCStreamSpeech(r,speaking,signal,function(progressive){if(generation===speechEpoch)setState(NAME+' is speaking · '+(progressive?'streaming audio':'buffered playback on this browser')+'. You can interrupt or send your next question.');});
      if(generation!==speechEpoch)return;
      if(playback&&playback.blocked){readySpeech={mid:mid,play:playback.play};setState('Voice is ready. Press Play on this reply to allow audio in your browser.');}
      speaking.onerror=function(){if(generation===speechEpoch){stopAudio();setState('Audio playback unavailable. Your complete text remains available.');}};
    } catch (e) { if(generation!==speechEpoch||e.name==='AbortError')return;stopAudio();setState('Voice could not play: '+(e.name==='NotAllowedError'?'press Play on the reply to allow audio':e.message)); } finally {clearTimeout(voiceTimeout);}
  }
  function cancelWait() {
    if (!activeRequest) return;
    activeRequest.cancelled = true; activeRequest.controller.abort();
  }
  async function ask(text, retry) {
    if (busy || !text) return;
    var style=document.getElementById('office-answer-style');
    var outgoing=style&&style.value==='brief'?'Briefly, answer conversationally in up to three sentences; offer a detailed follow-up if needed. '+text:style&&style.value==='full'?'Full analysis -- walk me through the records, figures and reasoning: '+text:text;
    var body=retry || {message:outgoing,request_id:'pg-'+agent+'-'+Date.now().toString(36)+Math.random().toString(36).slice(2,8)};
    if(!retry&&root.dataset.capabilityTask){body.context={capability_task_id:root.dataset.capabilityTask};delete root.dataset.capabilityTask;}
    if (!retry && cid) body.conversation_id=cid;
    var epoch=++chatEpoch, controller=new AbortController(), request={controller:controller,cancelled:false};
    activeRequest=request; busy=true; send.disabled=true; stopAudio();
    if(!retry)addQ(text);
    var pending=document.createElement('div');pending.className='talk-msg a talk-pending';pending.setAttribute('role','status');
    pending.innerHTML='<div class="who">'+esc(NAME)+'</div><div class="body">Waiting for a verified answer…</div><button type="button" class="talk-cancel">Stop waiting</button>';
    pending.querySelector('button').onclick=cancelWait;log.append(pending);log.scrollTop=log.scrollHeight;
    var began=Date.now();root.setAttribute('aria-busy','true');avatar('chatPending');
    function tick(){var seconds=Math.floor((Date.now()-began)/1000);setState('Request sent · '+seconds+'s'+(seconds>=12?' · taking longer than usual':'')+'. Waiting for '+NAME+"'s response.");}
    tick();waitTimer=setInterval(tick,1000);
    var timeout=setTimeout(function(){request.timedOut=true;controller.abort();},45000);
    function failed(message,pendingServer){
      pending.classList.remove('talk-pending');pending.classList.add('err');pending.setAttribute('role','status');
      pending.innerHTML='<div class="who">'+esc(NAME)+(pendingServer?' · response pending':' · not answered')+'</div><div class="body">'+esc(message)+'</div><div class="talk-recovery"><button type="button" class="talk-retry">Check / retry this request</button><button type="button" class="talk-restore">Edit question</button></div><small>Request '+esc(body.request_id)+'</small>';
      pending.querySelector('.talk-retry').onclick=function(){if(!busy){pending.remove();ask(text,body);}};
      pending.querySelector('.talk-restore').onclick=function(){input.value=text;input.focus();};
      setState('This request has no new answer. Earlier replies are conversation history.');avatar('chatDone');
    }
    try {
      var r=await fetch(BASE+'/persona/chat',{method:'POST',credentials:'same-origin',signal:controller.signal,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      var j=await readJson(r);if(epoch!==chatEpoch)return;
      clearInterval(waitTimer);
      if(r.status===202){failed('The server is still processing this request. Check it again using the same request ID.',true);return;}
      if(!r.ok){failed(why(r.status,j),false);return;}
      if(!j||!j.message_id||typeof j.answer!=='string'||!j.answer.trim()){failed('The server returned no completed answer. Check this request again; no answer is inferred.',true);return;}
      if(j.conversation_id)cid=j.conversation_id;
      pending.remove();var answer=addA(j);answer.dataset.requestId=body.request_id;
      answer.querySelector('.who').textContent=NAME+' · '+((Date.now()-began)/1000).toFixed(1)+'s';setState(null);
      speak(j.message_id,false);
    } catch(e){if(epoch===chatEpoch)failed(request.timedOut?'No response arrived within 45 seconds. The server may still finish; check this same request before sending it again.':request.cancelled?'Stopped waiting in this browser. Server processing may still finish; use Check / retry to retrieve the same request.':'Connection interrupted. The server may have received your question; check this same request.',true);}
    finally{clearInterval(waitTimer);clearTimeout(timeout);if(epoch===chatEpoch){activeRequest=null;busy=false;send.disabled=false;root.setAttribute('aria-busy','false');}}
  }
  form.addEventListener('submit', function (ev) { ev.preventDefault(); if(busy)return;var q = input.value.trim(); input.value = ''; ask(q); });
  input.addEventListener('keydown', function (ev) { if (ev.key === 'Enter' && !ev.shiftKey) { ev.preventDefault(); form.requestSubmit ? form.requestSubmit() : form.dispatchEvent(new Event('submit')); } });
  log.addEventListener('click', function (ev) { var b = ev.target.closest('.talk-replay'); if (b) speak(b.getAttribute('data-mid'), true); });
  root.addEventListener('click', function (ev) { var s = ev.target.closest('.talk-sugg'); if (s) { input.value = s.textContent; input.focus(); } });
  muteB.addEventListener('click', function () { muted = !muted; paintMute(); if (muted) stopAudio(); });
  stopB.addEventListener('click', function () { stopAudio(); setState(null); });
  $('talk-new').addEventListener('click', function () { if(busy){setState('Wait for the current reply before starting a new conversation.');return;}stopAudio(); chatEpoch++; cid = null; log.innerHTML = ''; setState('New conversation started with your next message. Earlier conversations stay in the record.'); });
  // ── the microphone: explicit start / stop, server transcription, text fallback
  function micSupported() { return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder); }
  if (!micSupported()) { mic.disabled = true; mic.title = 'This browser cannot record audio here; type your question instead.'; }
  function pickType() { var c = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg']; for (var i = 0; i < c.length; i++) { if (MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(c[i])) return c[i]; } return ''; }
  async function startRec() {
    if(busy){setState('Wait for the current reply, or type your next question below.');return;}stopAudio();
    var stream;
    try { stream = await navigator.mediaDevices.getUserMedia({audio: true}); }
    catch (e) {
      var n = e && e.name;
      setState(n === 'NotAllowedError' || n === 'SecurityError' ? 'Microphone permission denied. Allow the microphone for this site to speak, or type your question below.' : n === 'NotFoundError' ? 'No microphone was found. Type your question below.' : 'The microphone could not start (' + (n || e) + '). Type your question below.');
      input.focus(); return;
    }
    var type = pickType(); chunks = [];
    try { rec = new MediaRecorder(stream, type ? {mimeType: type} : undefined); } catch (e) { stream.getTracks().forEach(function (t) { t.stop(); }); setState('Recording is not supported here (' + (e && e.message || e) + '). Type your question below.'); return; }
    rec.ondataavailable = function (ev) { if (ev.data && ev.data.size) chunks.push(ev.data); };
    rec.onstop = async function () {
      stream.getTracks().forEach(function (t) { t.stop(); });
      mic.setAttribute('aria-pressed', 'false'); mic.innerHTML = '&#127908; Speak';
      var blob = new Blob(chunks, {type: (rec.mimeType || type || 'audio/webm').split(';')[0]});
      if (!blob.size) { setState('Nothing was recorded. Try again or type your question.'); return; }
      setState('Transcribing your question…');
      try {
        var r = await fetch(BASE + '/transcribe', {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': blob.type || 'audio/webm'}, body: blob});
        var j = await readJson(r);
        if (!r.ok) { setState('Transcription unavailable: ' + why(r.status, j) + '. Type your question below.'); input.focus(); return; }
        input.value = j.text || ''; setState('Heard: “' + (j.text || '') + '” — sending.');
        var q = input.value.trim(); input.value = ''; await ask(q);
      } catch (e) { setState('Transcription failed: ' + (e && e.message || e) + '. Type your question below.'); }
    };
    rec.start(); mic.setAttribute('aria-pressed', 'true'); mic.innerHTML = '&#9632; Stop recording';
    setState('Recording… press “Stop recording” when you have finished your question.');
  }
  mic.addEventListener('click', function () { if (rec && rec.state === 'recording') rec.stop(); else startRec(); });
  window.CCTalk = {ask: ask, speak: speak, state: function () { return {cid: cid, muted: muted, busy: busy}; }};
  resume();
})();
"""


def talk_panel_html(kind: str) -> str:
    scope, s1, s2 = TALK_SCOPE[kind]
    return (TALK_PANEL_HTML.replace("%%AGENT%%", kind)
            .replace("%%NAME%%", TALK_NAMES[kind])
            .replace("%%SCOPE%%", scope)
            .replace("%%SUGG1%%", s1).replace("%%SUGG2%%", s2))


# ═════════════════════════════════════════════════════════════════════
# KAREN'S HERO: an original flat-vector portrait card (no external or
# licensed asset), her persona line and the talk panel. The satirical
# activist look is a costume; her analysis is records only.
# ═════════════════════════════════════════════════════════════════════

KAREN_PORTRAIT_SVG = """<svg viewBox="0 0 300 360" role="img" aria-label="Illustrated portrait card of Karen, an AI agent: asymmetric teal-and-magenta undercut, oversized round glasses, a nose ring, a denim jacket covered in slogan pins, holding a hand-lettered protest placard that reads PROVE IT">
<defs><linearGradient id="fgk" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ff6b8b" stop-opacity=".34"/><stop offset="1" stop-color="#ff6b8b" stop-opacity="0"/></linearGradient>
<linearGradient id="fgkh" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#20c4b4"/><stop offset="1" stop-color="#d63a9a"/></linearGradient></defs>
<circle cx="150" cy="150" r="120" fill="url(#fgk)"/>
<path d="M38 360 C48 270 96 240 150 236 C204 240 252 270 262 360 Z" fill="#3c5a86"/>
<path d="M120 244 L150 300 L180 244 Z" fill="#f4e9d8"/>
<path d="M100 252 L150 330 L126 246 Z M200 252 L150 330 L174 246 Z" fill="#2f4a70"/>
<circle cx="112" cy="290" r="9" fill="#ffd23f"/><text x="112" y="293" font-size="9" font-family="monospace" font-weight="700" text-anchor="middle" fill="#1a1204">?</text>
<circle cx="190" cy="282" r="10" fill="#ff6b8b"/><text x="190" y="285" font-size="6" font-family="monospace" font-weight="700" text-anchor="middle" fill="#fff">CITE</text>
<rect x="96" y="306" width="34" height="11" rx="3" fill="#20c4b4"/><text x="113" y="314" font-size="6.5" font-family="monospace" font-weight="700" text-anchor="middle" fill="#03201d">SOURCES?</text>
<rect x="134" y="196" width="32" height="46" rx="12" fill="#e2b08f"/>
<ellipse cx="150" cy="150" rx="49" ry="59" fill="#efc2a0"/>
<path d="M100 150 C96 104 118 80 150 80 C176 80 196 92 202 112 L202 128 C188 112 166 104 150 104 C132 104 116 112 108 126 C104 134 101 142 100 150 Z" fill="url(#fgkh)"/>
<path d="M196 104 C206 112 208 126 204 138 L198 136 Z" fill="#8a8f98"/>
<circle cx="130" cy="150" r="15" fill="none" stroke="#7a3b1e" stroke-width="4"/><circle cx="170" cy="150" r="15" fill="none" stroke="#7a3b1e" stroke-width="4"/><path d="M145 149 L155 149" stroke="#7a3b1e" stroke-width="4"/>
<circle cx="131" cy="151" r="3.2" fill="#2b2b2b"/><circle cx="171" cy="151" r="3.2" fill="#2b2b2b"/>
<path d="M114 128 Q126 120 140 128 M160 126 Q174 118 186 124" stroke="#5b2a4a" stroke-width="4" fill="none" stroke-linecap="round"/>
<circle cx="157" cy="171" r="3" fill="none" stroke="#c9ccd2" stroke-width="1.8"/>
<path d="M136 188 Q150 184 166 190" stroke="#b0414f" stroke-width="3.5" fill="none" stroke-linecap="round"/>
<rect x="206" y="150" width="7" height="170" rx="3" fill="#a8743f"/>
<rect x="172" y="96" width="112" height="64" rx="6" fill="#fdf6e3" stroke="#1d1d1d" stroke-width="3" transform="rotate(-6 228 128)"/>
<text x="228" y="126" font-size="20" font-family="Impact, 'Arial Black', sans-serif" font-weight="900" text-anchor="middle" fill="#d6304f" transform="rotate(-6 228 128)">PROVE IT.</text>
<text x="228" y="146" font-size="8.5" font-family="monospace" font-weight="700" text-anchor="middle" fill="#1d1d1d" transform="rotate(-6 228 128)">WHAT ARE WE MISSING?</text>
<circle cx="68" cy="276" r="9" fill="#ff6b8b"/><text x="68" y="280" font-size="9" font-family="monospace" font-weight="700" text-anchor="middle" fill="#2a0712">AI</text></svg>"""

KAREN_CSS = r"""
.k-hero{display:grid;grid-template-columns:minmax(0,300px) minmax(0,1fr);gap:18px;margin:0 0 16px;align-items:stretch}
.k-card{position:relative;border-radius:16px;border:1px solid #5a2a3a;background:radial-gradient(120% 90% at 50% 0%,#2a1520 0%,#120a10 60%,#08060a 100%);min-height:320px;display:flex;align-items:flex-end;justify-content:center;overflow:hidden}
.k-card svg{width:100%;height:auto;max-height:360px}
.k-card .k-ai{position:absolute;left:12px;top:12px;font:700 10.5px/1 ui-monospace,monospace;letter-spacing:.16em;padding:5px 8px;border-radius:6px;border:1px solid #ff6b8b;color:#ffc2cf;background:rgba(0,0,0,.4)}
.k-brief{display:flex;flex-direction:column;gap:10px;padding:16px 18px;border-radius:16px;border:1px solid #3a2430;background:#120d12}
.k-brief h1{margin:0;font:600 34px/1 Georgia,serif;color:#fff}
.k-brief .k-role{font:650 12px/1 ui-monospace,monospace;letter-spacing:.2em;text-transform:uppercase;color:#ff9db2}
.k-brief .k-lines{font:700 18px/1.3 Georgia,serif;color:#ffd23f;margin:4px 0}
.k-brief p{margin:0;color:#d9cbd2}
.k-brief .k-rules{font-size:12.5px;color:#b9a8b1}
@media(max-width:760px){.k-hero{grid-template-columns:1fr}.k-card{min-height:260px}}
"""

KAREN_HERO_HTML = """<section class="k-hero" id="karen-hero" aria-label="Karen, the red-team agent">
<div class="k-card" data-agent="karen"><span class="k-ai">&#9679; AI AGENT</span>%%PORTRAIT%%</div>
<div class="k-brief"><span class="k-role">Red team &#183; challenge &#183; no authority</span><h1>Karen</h1>
<div class="k-lines">&#8220;What are we missing?&#8221; &#8220;Prove it.&#8221;</div>
<p>Aggressive, skeptical, contrarian and funny &#8212; and obsessed with evidence. Karen challenges Derek, Xavier, Audrey and the Chief Allocator only with records that exist; the challenged agent answers, and an independent evaluator (Audrey; Xavier for Audrey) decides. She never resolves her own challenge.</p>
<p class="k-rules">The placard and pins are a comedic costume: political ideology has zero influence on her analysis. She attacks assumptions and methodology, never people. She holds no order, approval, activation, limit or promotion authority, in code and in the database. Karen is an AI agent; this portrait is original vector art.</p></div>
</section>"""


def karen_hero_html() -> str:
    return KAREN_HERO_HTML.replace("%%PORTRAIT%%", KAREN_PORTRAIT_SVG)


_PAGE_TITLES = {"index": "Agents", "derek": "Derek", "xavier": "Xavier",
                "audrey": "Audrey", "karen": "Karen", "archer": "Archer",
                "scout": "Scout"}


def _nav(current: str) -> str:
    items = [("index", "Agents"), ("derek", "Derek"), ("xavier", "Xavier"),
             ("audrey", "Audrey"), ("karen", "Karen"), ("archer", "Archer"),
             ("scout", "Scout"), ("demo", "Product demo")]
    return "".join(
        '<a href="%s"%s>%s</a>' % (PAGE_PATHS[k],
                                  ' aria-current="page"' if k == current else "", t)
        for k, t in items)


_SHELL = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="robots" content="noindex,nofollow">
<meta name="description" content="%%DESC%%">
<title>%%TITLE%% | BETTOR Command</title>
<style>%%CSS%%</style></head>
<body class="ag ag-%%KIND%%" data-kind="%%KIND%%">
<header class="top"><a class="brand" href="/api/command/agents/page"><span class="mark"></span>BETTOR <b>COMMAND</b></a>
<nav aria-label="agents">%%NAV%%</nav>
<div class="meta"><span class="ro">READ-ONLY</span><span id="readat">reading&#8230;</span><button id="trace-btn" type="button">Trace</button><button id="refresh" type="button">Refresh</button></div></header>
<main><section class="card wide" id="trace" hidden aria-label="Trace linked records"><header><span class="idx">&#8594;</span><div class="ttl"><h3>Trace linked records</h3><code class="key">entry decision &#8594; handoff &#8594; Xavier decisions &#8594; audit &#8594; directive &#8594; task &#8594; candidate</code></div><button id="trace-close" type="button">Close</button></header>
<p class="note">Follows the ids the records carry (entry_intent_id, portfolio_group_id, xavier_decision_id, directive_id, task_id, candidate_id, and evidence references) across the four same-origin reads. Every step says what linked it.</p><div id="trace-body"></div></section>
<div id="app"><p class="boot">Reading %%ENDPOINT%% &#8230;</p></div>%%EXTRA%%</main>
<p class="foot">Presentation of recorded state. This page sends no order and holds no credential; it reads %%ENDPOINT%% with the COMMAND session. UNKNOWN is not zero; EMPTY is not success.</p>
<script>%%JS%%</script></body></html>"""

_DESCS = {
    "index": "The three agents: identity, mandate, versions, recorded state, heartbeat, cadence and current tasks.",
    "derek": "Derek: catalogue coverage, opportunity queue, entry decisions, fills and handoffs. Read-only.",
    "xavier": "Xavier: owned positions, the spread ladder, every alternative, payouts and servicing. Read-only.",
    "audrey": "Audrey: reports, findings, outcomes, improvement tasks, releases, directives and management chat.",
    "karen": "Karen: red-team challenges to Derek, Xavier and Audrey, their peer responses, outcomes and metrics. No authority. Read-only.",
    "archer": "Archer: head of execution. Shadow execution estimates of Derek's candidates, predicted vs realized execution loss and his scorecard. No order or capital authority. Read-only.",
    "scout": "Scout: market intelligence. Compliant sources, the feature registry, frozen forward tests against PinnAPI and his scorecard. No trade or promotion authority. Read-only.",
}


_CC_SHELL = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="robots" content="noindex,nofollow">
<meta name="color-scheme" content="dark">
<meta name="description" content="%%DESC%%">
<title>%%TITLE%% · %%ROLE%% | BETTOR Command</title>
<style>%%CSS%%</style></head>
<body class="ag cc ag-%%KIND%%" data-kind="%%KIND%%" data-cc-mode="unavailable">
<script>%%FRAMED%%</script>
<a class="skip" href="#cc-main">Skip to the records</a>
<header class="top cc-top"><a class="brand" href="/api/command/agents/page"><span class="mark"></span>BETTOR <b>COMMAND</b></a>
<nav class="cc-nav" aria-label="Agents">%%NAV%%</nav>
<nav class="cc-sub" aria-label="More"><a href="/api/command/agents/page">All agents</a><a href="/api/command/agents/karen/page">Karen · red team</a><a href="/api/command/agents/archer/page">Archer · execution</a><a href="/api/command/agents/scout/page">Scout · intelligence</a><a href="/api/command/agents/demo/page">Product demo</a></nav>
<div class="meta"><span class="ro">READ-ONLY</span><span id="readat">reading&#8230;</span><button id="trace-btn" type="button">Trace</button><button id="refresh" type="button">Refresh</button></div></header>
<main id="cc-main" tabindex="-1">
<div class="cc-paper-banner" id="paper-banner" data-state="READING" role="status" aria-live="polite">Reading the paper session&#8230;</div>
%%FRESH%%
<div class="cc-banner" role="alert" id="cc-banner">%%BANNER%%</div>
<div class="cc-hero">%%STAGE%%%%BRIEF%%</div>
%%TALK%%
%%OPS%%
%%PAPER%%
%%FUNDED_OPEN%%
%%PANELS%%
<section class="card wide" id="trace" hidden aria-label="Trace linked records" style="margin-top:16px"><header><span class="idx">&#8594;</span><div class="ttl"><h3>Trace linked records</h3><code class="key">entry decision &#8594; handoff &#8594; Xavier decisions &#8594; audit &#8594; directive &#8594; task &#8594; candidate</code></div><button id="trace-close" type="button">Close</button></header>
<p class="note">Follows the ids the records carry (entry_intent_id, portfolio_group_id, xavier_decision_id, directive_id, task_id, candidate_id, and evidence references) across the four same-origin reads. Every step says what linked it.</p><div id="trace-body"></div></section>
<h2 class="cc-h2" id="full-record">Full workspace record (funded)</h2>
<div id="app"><p class="boot">Reading %%ENDPOINT%% &#8230;</p></div>%%FUNDED_CLOSE%%</main>
<p class="foot">Presentation of recorded state. This page sends no order and holds no credential; it reads %%ENDPOINT%% with the COMMAND session. The 3D characters are licensed Microsoft Rocketbox models (MIT licence, credited below; shipped with their licence file); a device that cannot draw them shows a 2D portrait card. The pose follows the paper runtime's own heartbeat (paper_session_health) and never stands in for data. UNKNOWN is not zero; EMPTY is not success.%%CREDIT%%</p>
<script>%%JS%%</script>
<script type="module">%%LOADER%%</script></body></html>"""


def _cc_page_html(kind: str) -> str:
    from . import agent_cc_ops as OPS
    from . import agent_cc_page as CCP
    from . import agent_office as OFFICE
    from . import agent_office_experience as DESK
    from . import agent_office_finish as FINISH
    from . import agent_capability_panel as CAPABILITIES
    from . import agent_office_management as MANAGEMENT
    js = (CORE_JS + COMMON_JS + TRACE_JS
          + {"derek": DEREK_JS, "xavier": XAVIER_JS, "audrey": AUDREY_JS}[kind]
          + CCP.CC_CORE_JS + CCP.CC_BOOT_JS.replace(
              "var E = AG.ENDPOINTS,",
              "var E = Object.assign({}, AG.ENDPOINTS, %s)," % _cc_endpoints_js(),
              1)
          + CCP.PAPER_CORE_JS + OPS.OPS_CORE_JS + CCP.PAPER_BOOT_JS.replace(
              "%%PAPER_EP%%", _json_ep(PAPER_EP_KEYS))
          + BOOT_JS + OFFICE.OFFICE_JS + OFFICE.SPEECH_JS + TALK_JS + DESK.JS + FINISH.JS + CAPABILITIES.JS + MANAGEMENT.JS)
    chat = CCP.chat_panel_html(CHAT_PANEL_HTML) if kind == "audrey" else ""
    return (_CC_SHELL.replace("%%CSS%%", BASE_CSS + CCP.CC_CSS + CCP.PAPER_CSS
                              + OPS.OPS_CSS + TALK_CSS + OFFICE.OFFICE_CSS + DESK.CSS + FINISH.CSS + CAPABILITIES.CSS + MANAGEMENT.CSS)
            .replace("%%TALK%%", talk_panel_html(kind))
            .replace("%%FRESH%%", OPS.fresh_html())
            .replace("%%OPS%%", OPS.ops_html(kind))
            .replace("%%FUNDED_OPEN%%", OPS.funded_open())
            .replace("%%FUNDED_CLOSE%%", OPS.FUNDED_CLOSE)
            .replace("%%PAPER%%", CCP.paper_html(kind))
            .replace("%%JS%%", js)
            .replace("%%FRAMED%%", CCP.FRAMED_JS)
            .replace("%%LOADER%%", CCP.LOADER_JS.replace(
                "%%CHARACTERS%%", ENDPOINTS["characters"]).replace(
                "%%AVATAR%%", ENDPOINTS["avatar"]))
            .replace("%%NAV%%", CCP.nav_html(kind, PAGE_PATHS))
            .replace("%%BANNER%%", CCP.UNAVAILABLE_BANNER)
            .replace("%%STAGE%%", CCP.stage_html(kind, ENDPOINTS[kind], character_asset(kind)))
            .replace("%%BRIEF%%", CCP.brief_html(kind))
            .replace("%%PANELS%%", CCP.panels_html(kind, chat))
            .replace("%%CREDIT%%", CCP.credit_html(character_asset(kind)))
            .replace("%%ENDPOINT%%", ENDPOINTS[kind])
            .replace("%%TITLE%%", _PAGE_TITLES[kind])
            .replace("%%ROLE%%", CCP.CC_META[kind]["role"].replace("&", "&amp;"))
            .replace("%%DESC%%", _DESCS[kind])
            .replace("%%KIND%%", kind))


def _json_ep(keys) -> str:
    import json as _json
    return _json.dumps({k: ENDPOINTS[k] for k in keys}).replace('"', "'")


def _cc_endpoints_js() -> str:
    import json as _json
    return _json.dumps({k: ENDPOINTS[k] for k in (
        "derek_orders", "xavier_standing", "xavier_payoff",
        "audrey_performance", "labels")}).replace('"', "'")


def page_html(kind: str) -> str:
    """One workspace page. Derek, Xavier and Audrey are the Command Centre
    pages (data module inline, character module loaded separately); the
    index keeps the plain workspace shell."""
    if kind not in _PAGE_TITLES:
        raise KeyError(kind)
    if kind in ("derek", "xavier", "audrey"):
        return _cc_page_html(kind)
    js = CORE_JS + COMMON_JS + TRACE_JS
    js += {"index": INDEX_JS, "derek": DEREK_JS, "xavier": XAVIER_JS,
           "audrey": AUDREY_JS, "karen": KAREN_JS, "archer": POS_JS,
           "scout": POS_JS}[kind]
    if kind in ("archer", "scout"):
        return _pos_page_html(kind, js)
    js += BOOT_JS
    shell = _SHELL
    if kind == "karen":
        # her portrait, persona and the persona chat above the records
        talk = talk_panel_html("karen").replace(
            "Karen answers from the paper records, the active policy and "
            "stored lessons &#183; paper only",
            "Karen answers only from her challenge records and the evidence "
            "they cite").replace("Karen can explain and propose;",
                                  "Karen can question and challenge;")
        shell = shell.replace('<div id="app">', karen_hero_html() + talk
                              + '<div id="app">', 1)
        js += TALK_JS
    return (shell.replace("%%CSS%%", BASE_CSS + (
                TALK_CSS + KAREN_CSS if kind == "karen" else ""))
            .replace("%%JS%%", js)
            .replace("%%EXTRA%%", CHAT_PANEL_HTML if kind == "audrey" else "")
            .replace("%%NAV%%", _nav(kind))
            .replace("%%ENDPOINT%%", ENDPOINTS[kind])
            .replace("%%TITLE%%", _PAGE_TITLES[kind])
            .replace("%%DESC%%", _DESCS[kind])
            .replace("%%KIND%%", kind))


def _pos_page_html(kind: str, js: str) -> str:
    """Archer's / Scout's page: the desk (live 3D character at an original
    desk, every value from the endpoint), the persona chat, then every
    record section. Read only: no write control of any kind."""
    from . import agent_desks as DK
    name = TALK_NAMES[kind]
    talk = talk_panel_html(kind).replace(
        "%s answers from the paper records, the active policy and stored "
        "lessons &#183; paper only" % name,
        "%s answers only from his own records" % name).replace(
        "%s can explain and propose;" % name,
        "%s can explain, never act;" % name)
    shell = _SHELL.replace('<div id="app">', DK.desk_html(kind) + talk
                           + '<div id="app">', 1).replace(
        "</script></body></html>",
        "</script>\n<script type=\"module\">%s</script></body></html>"
        % DK.DESK_LOADER_JS.replace("%%CHARACTERS%%",
                                    ENDPOINTS["characters"]), 1)
    return (shell.replace("%%CSS%%", BASE_CSS + TALK_CSS + DK.DESK_CSS)
            .replace("%%JS%%", js + DK.DESK_JS + BOOT_JS + TALK_JS.replace(
                "BASE + '/persona/latest-conversation'",
                "BASE + '/persona/latest-conversation?absent=empty'", 1))
            .replace("%%EXTRA%%", "")
            .replace("%%NAV%%", _nav(kind))
            .replace("%%ENDPOINT%%", ENDPOINTS[kind])
            .replace("%%TITLE%%", _PAGE_TITLES[kind])
            .replace("%%DESC%%", _DESCS[kind])
            .replace("%%KIND%%", kind))


def render_js(kind: str) -> str:
    """The pure render code a page carries, without its DOM boot (for tests)."""
    from . import agent_cc_ops as OPS
    from . import agent_cc_page as CCP
    return CORE_JS + COMMON_JS + TRACE_JS + {"index": INDEX_JS, "derek": DEREK_JS,
                                  "xavier": XAVIER_JS, "audrey": AUDREY_JS,
                                  "karen": KAREN_JS, "archer": POS_JS,
                                  "scout": POS_JS}[kind] + (
        CCP.CC_CORE_JS + CCP.PAPER_CORE_JS + OPS.OPS_CORE_JS
        if kind in ("derek", "xavier", "audrey") else "")


LOCKED_PAGE_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>Locked | BETTOR Command</title>
<style>%%CSS%%</style></head><body class="ag ag-index">
<main><div class="gate locked" data-gate="LOCKED"><h2>%%LOCKED%%</h2>
<p>The agent workspaces are read with the COMMAND session. Unlock it on the desk, then come back to this page.</p>
<p><a class="btn primary" href="%%DESK%%">Open the desk</a></p></div></main></body></html>"""


def locked_html() -> str:
    return (LOCKED_PAGE_HTML.replace("%%CSS%%", BASE_CSS)
            .replace("%%LOCKED%%", LOCKED_TEXT).replace("%%DESK%%", DESK_PAGE))


# ════════════════════════════════════════════════════════════════════
# ROUTES
# ════════════════════════════════════════════════════════════════════
def _headers() -> dict:
    from . import app as A
    return A._desk_page_headers()


def _authorised(request: Request) -> bool:
    from . import app as A
    try:
        A.require_command(bt_command=request.cookies.get("bt_command", ""),
                          x_desk_token=request.headers.get("x-desk-token", ""),
                          x_admin_token=request.headers.get("x-admin-token", ""))
    except HTTPException:
        return False
    return True


#: The Command Centre pages load ONE same-origin module (the character, over
#: the vendored three.js build) -- so their script-src adds 'self'. Nothing
#: else changes: no external origin, connect-src 'self', no form posts.
CC_PAGE_CSP = ("default-src 'none'; style-src 'unsafe-inline'; "
               # 'wasm-unsafe-eval' lets the vendored Meshopt decoder compile
               # its WebAssembly; it does NOT allow eval() of JavaScript
               "script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval'; "
               # blob: is the glTF loader decoding a model's EMBEDDED
               # textures (object URLs of this document); nothing remote
               "connect-src 'self' blob:; "
               "img-src 'self' data: blob:; "
               # the agent's speech audio (the same-origin /speech route, or a
               # blob: of it) so the character's mouth can follow real audio
               "media-src 'self' blob:; "
               "base-uri 'none'; form-action 'none'; "
               # the management shell at /derek, /xavier and /audrey frames
               # these pages from the same origin; nothing else may frame them
               "frame-ancestors 'self'")


def _cc_headers() -> dict:
    h = dict(_headers())
    h["Content-Security-Policy"] = CC_PAGE_CSP
    return h


def _serve(request: Request, html_fn, headers_fn=None) -> HTMLResponse:
    if not _authorised(request):
        return HTMLResponse(content=locked_html(), status_code=401,
                            headers=_headers())
    return HTMLResponse(content=html_fn(), status_code=200,
                        headers=(headers_fn or _headers)())


# ── THE CHARACTER MODULE AND ITS VENDORED three.js, READ-ONLY ───────
#
# An allowlist, not a directory listing: only these files are served, from
# sportsassets/assets/agents, with their exact content type. three.js
# (r185, MIT, the minified ES module build and the core it imports) is pinned
# by file and cached long; the character module is ours and cached briefly so
# a release reaches browsers within minutes. None of these files carries data
# or a credential; they are still served only to the COMMAND session (a
# same-origin module import sends the session cookie, which is scoped to
# /api/command), so nothing under /api/command answers an anonymous caller,
# and they are cached privately.
STATIC_DIR = __import__("pathlib").Path(__file__).resolve().parents[1] \
    / "assets" / "agents"
STATIC_FILES = {
    "three.module.min.js": ("text/javascript; charset=utf-8",
                            "private, max-age=604800, immutable"),
    "three.core.min.js": ("text/javascript; charset=utf-8",
                          "private, max-age=604800, immutable"),
    "cc_characters.js": ("text/javascript; charset=utf-8",
                         "private, max-age=300"),
    "THREE_LICENSE.txt": ("text/plain; charset=utf-8",
                          "private, max-age=86400"),
    # the licensed-character pipeline: glTF loader (three.js addons, MIT),
    # the Meshopt decoder (MIT) and the controller
    "cc_avatar.js": ("text/javascript; charset=utf-8", "private, max-age=300"),
    "GLTFLoader.js": ("text/javascript; charset=utf-8",
                      "private, max-age=604800, immutable"),
    "BufferGeometryUtils.js": ("text/javascript; charset=utf-8",
                               "private, max-age=604800, immutable"),
    "SkeletonUtils.js": ("text/javascript; charset=utf-8",
                         "private, max-age=604800, immutable"),
    "meshopt_decoder.module.js": ("text/javascript; charset=utf-8",
                                  "private, max-age=604800, immutable"),
}

# Identifying MLB artwork, from the official MLB CDN; see source manifest.
STATIC_FILES.update({"mlb-logo-%s.svg" % team_id: ("image/svg+xml", "private, max-age=86400")
                     for team_id in (133, 134, 135, 136, 137, 138, 139, 140, 141, 142, 143, 144, 145, 146, 147, 158, 108, 109, 110, 111, 112, 113, 114, 115, 116, 117, 118, 119, 120, 121)})
STATIC_FILES["mlb-logo-sources.json"] = ("application/json", "private, max-age=86400")

# Team logos of every league in the account's markets: exactly the files in
# the verified team-logo manifest (team_logos.static_files), each keyed by the
# venue team id it belongs to; a file whose sha256 no longer matches is not
# served. Published by basename, read from team-logos/.
from .. import team_logos as _TEAM_LOGOS  # noqa: E402
TEAM_LOGO_FILES = {name: (rel, (ctype, "private, max-age=86400"))
                   for name, (rel, ctype) in _TEAM_LOGOS.static_files().items()
                   if rel.startswith("team-logos/")}

# ── LICENSED CHARACTER MODELS ───────────────────────────────────────
MODELS_DIR = STATIC_DIR / "models"
MODELS_URL = "/api/command/agents/static/models/"


def _manifest() -> dict:
    import json as _json
    try:
        return _json.loads((MODELS_DIR / "manifest.json").read_text())
    except (OSError, ValueError):
        return {"characters": {}}


def _safe_file(name) -> bool:
    import re as _re
    return isinstance(name, str) and bool(_re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,120}", name))


def character_asset(kind: str) -> dict:
    """The character config the page hands its loader: a licensed model when
    the manifest entry is complete and both files exist, else model None with
    the reason (and the procedural placeholder is shown, tagged)."""
    e = dict((_manifest().get("characters") or {}).get(kind) or {})
    base = {k: e.get(k) for k in ("framing", "lighting", "yaw", "scale",
                                  "bones", "blendshapes", "candidate_label",
                                  "credit", "hide_materials", "arms_down")
            if e.get(k) is not None}
    why = None
    if not e.get("model"):
        why = e.get("why_absent") or "no model in the manifest"
    elif e.get("test_asset") is not False:
        why = "refused: a test asset (or an entry not marked test_asset=false) is never a character"
    elif not (_safe_file(e.get("model")) and e["model"].endswith(".glb")):
        why = "refused: model must be a .glb file name in the models directory"
    elif not (MODELS_DIR / e["model"]).is_file():
        why = "refused: the model file is not in this build"
    elif not (_safe_file(e.get("license_file")) and (MODELS_DIR / e["license_file"]).is_file()
              and (MODELS_DIR / e["license_file"]).stat().st_size > 0):
        why = "refused: the licence file shipped with the model is missing"
    elif not (e.get("license_spdx") and e.get("licensed_from")):
        why = "refused: license_spdx and licensed_from are required"
    if why:
        return dict(base, model=None, why=why)
    return dict(base, model=MODELS_URL + e["model"],
                license={"spdx": e["license_spdx"], "from": e["licensed_from"],
                         "file": MODELS_URL + e["license_file"]})
THREE_VERSION = "0.185.1"


async def require_command(request: Request) -> str:
    """The COMMAND read check (api.app.require_command), as a dependency."""
    from . import app as A
    return A.require_command(bt_command=request.cookies.get("bt_command", ""),
                             x_desk_token=request.headers.get("x-desk-token", ""),
                             x_admin_token=request.headers.get("x-admin-token", ""))


@router.get("/api/command/agents/static/models/{name}", include_in_schema=False,
            dependencies=[Depends(require_command)])
async def agents_model(name: str):
    """Only the model and licence files of a COMPLETE manifest entry."""
    from fastapi.responses import Response as _R
    allowed = {}
    for kind in (_manifest().get("characters") or {}):
        a = character_asset(kind)
        if a.get("model"):
            allowed[a["model"].rsplit("/", 1)[1]] = "model/gltf-binary"
            allowed[a["license"]["file"].rsplit("/", 1)[1]] = "text/plain; charset=utf-8"
    if name not in allowed:
        raise HTTPException(status_code=404, detail="not a licensed model file in this build")
    return _R(content=(MODELS_DIR / name).read_bytes(), media_type=allowed[name].split(";")[0],
              headers={"Content-Type": allowed[name], "Cache-Control": "private, max-age=3600",
                       "X-Content-Type-Options": "nosniff"})


@router.get("/api/command/agents/static/{name}", include_in_schema=False,
            dependencies=[Depends(require_command)])
async def agents_static(name: str):
    from fastapi.responses import Response as _R
    rel = name
    got = STATIC_FILES.get(name)
    if got is None and name in TEAM_LOGO_FILES:
        rel, got = TEAM_LOGO_FILES[name]
    if got is None:
        raise HTTPException(status_code=404, detail="not a served file")
    try:
        body = (STATIC_DIR / rel).read_bytes()
    except OSError:
        raise HTTPException(status_code=404, detail="file absent in this build")
    return _R(content=body, media_type=got[0].split(";")[0],
              headers={"Content-Type": got[0], "Cache-Control": got[1],
                       "X-Content-Type-Options": "nosniff",
                       "Cross-Origin-Resource-Policy": "same-origin"})


@router.get("/api/command/agents/page", include_in_schema=False)
async def agents_index_page(request: Request):
    return _serve(request, lambda: page_html("index"))


@router.get("/api/command/agents/derek/page", include_in_schema=False)
async def agents_derek_page(request: Request):
    return _serve(request, lambda: page_html("derek"), _cc_headers)


@router.get("/api/command/agents/xavier/page", include_in_schema=False)
async def agents_xavier_page(request: Request):
    return _serve(request, lambda: page_html("xavier"), _cc_headers)


@router.get("/api/command/agents/audrey/page", include_in_schema=False)
async def agents_audrey_page(request: Request):
    return _serve(request, lambda: page_html("audrey"), _cc_headers)


@router.get("/api/command/agents/karen/page", include_in_schema=False)
async def agents_karen_page(request: Request):
    # the Command Centre CSP: media-src for her spoken answers, framed by
    # the /karen management shell (frame-ancestors 'self')
    return _serve(request, lambda: page_html("karen"), _cc_headers)


@router.get("/api/command/agents/archer/page", include_in_schema=False)
async def agents_archer_page(request: Request):
    # the Command Centre CSP (the 3D desk module and its spoken answers),
    # framed by the /archer management shell
    return _serve(request, lambda: page_html("archer"), _cc_headers)


@router.get("/api/command/agents/eddie/page", include_in_schema=False)
async def agents_eddie_page_alias(request: Request):
    # (266) the historical alias: the old address still draws Archer's page
    # (the agent it now names); /api/command/eddie answers alias_of: archer
    return _serve(request, lambda: page_html("archer"), _cc_headers)


@router.get("/api/command/agents/scout/page", include_in_schema=False)
async def agents_scout_page(request: Request):
    return _serve(request, lambda: page_html("scout"), _cc_headers)


@router.get("/api/command/agents/demo/page", include_in_schema=False)
async def agents_demo_page(request: Request):
    return _serve(request, lambda: demo_html(standalone=False))



# ════════════════════════════════════════════════════════════════════
# THE PRODUCT DEMO
#
# A cinematic walkthrough of the exact three-agent architecture in 14
# chapters. REHEARSAL mode draws scripted, illustrative prices and fills and
# says so on every frame; the management chat in chapter 11 is labelled as a
# scripted exchange and is never presented as the Audrey service. PRODUCTION
# mode (served page only) reads the four same-origin agent endpoints and
# shows what the records hold now, empty states included. The soundtrack is
# generated live with the Web Audio API (off by default); the depth field is
# a 2D canvas with a perspective projection; the camera is CSS 3D.
# ════════════════════════════════════════════════════════════════════
DEMO_CSS = r"""
:root{--sans:ui-sans-serif,system-ui,-apple-system,"SF Pro Text","Segoe UI Variable Text","Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
--display:ui-sans-serif,system-ui,-apple-system,"SF Pro Display","Segoe UI Variable Display","Segoe UI",Roboto,sans-serif;
--mono:ui-monospace,"SF Mono",SFMono-Regular,"JetBrains Mono","Cascadia Mono",Menlo,Consolas,monospace;
--bg:#eeece6;--bg2:#e4e1d9;--glass:rgba(255,255,255,.72);--glass2:rgba(255,255,255,.5);--ink:#12141a;--ink2:#444a56;--ink3:#737a87;--line:rgba(20,24,32,.12);--line2:rgba(20,24,32,.2);
--ok:#1b7550;--warn:#9a6200;--bad:#b0312a;--info:#2d56c6;--neutral:#6b7280;
--derek:#b1600a;--xavier:#0a7a8b;--audrey:#6a45c8;--mgmt:#2f55d4;--acc:var(--derek);--shadow:0 30px 80px -40px rgba(20,24,40,.45),0 1px 0 rgba(255,255,255,.8) inset;color-scheme:light dark}
@media (prefers-color-scheme:dark){:root{--bg:#05070b;--bg2:#0a0d13;--glass:rgba(17,21,29,.74);--glass2:rgba(24,29,39,.6);--ink:#edf0f5;--ink2:#aab2bf;--ink3:#6f7888;--line:rgba(255,255,255,.08);--line2:rgba(255,255,255,.15);
--ok:#45c089;--warn:#e6ab43;--bad:#ff766b;--info:#7d9dff;--neutral:#8b93a1;--derek:#f2a541;--xavier:#3fd0e0;--audrey:#a98cff;--mgmt:#86a2ff;--shadow:0 40px 90px -40px rgba(0,0,0,.9),0 1px 0 rgba(255,255,255,.04) inset}}
*{box-sizing:border-box}
html,body{margin:0;height:100%;overflow:hidden}
body{background:radial-gradient(1200px 700px at 50% -10%,color-mix(in oklab,var(--acc) 14%,transparent),transparent 70%),var(--bg);color:var(--ink);font:14px/1.45 var(--sans);font-variant-numeric:tabular-nums;transition:background 1.2s}
body.a-DEREK{--acc:var(--derek)}body.a-XAVIER{--acc:var(--xavier)}body.a-AUDREY{--acc:var(--audrey)}body.a-MANAGEMENT{--acc:var(--mgmt)}
#stage{position:fixed;inset:0;display:flex;flex-direction:column}
#bg{position:absolute;inset:0;width:100%;height:100%;z-index:0}
.banner{position:relative;z-index:5;display:flex;justify-content:center;align-items:center;gap:10px;padding:6px 12px;font:700 11px/1.2 var(--mono);letter-spacing:.14em;text-align:center;
color:#1a1200;background:repeating-linear-gradient(135deg,#f2c14e 0 14px,#e8b43a 14px 28px)}
.banner.prod{color:#fff;background:linear-gradient(90deg,#0c5f46,#12795a)}
.hud{position:relative;z-index:5;display:flex;align-items:center;gap:14px;padding:10px 20px}
.brand{font:650 12px/1 var(--mono);letter-spacing:.16em;color:var(--ink);text-decoration:none;display:flex;align-items:center;gap:8px;white-space:nowrap}
.brand i{width:16px;height:16px;border-radius:5px;background:conic-gradient(from 200deg,var(--derek),var(--xavier),var(--audrey),var(--derek));box-shadow:0 0 16px color-mix(in oklab,var(--acc) 60%,transparent)}
.ctitle{flex:1;min-width:0;display:flex;align-items:baseline;gap:12px}
.ctitle .n{font:650 12px/1 var(--mono);color:var(--acc);letter-spacing:.1em;white-space:nowrap}
.ctitle h1{margin:0;font:680 clamp(15px,2.1vw,22px)/1.2 var(--display);letter-spacing:-.015em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.hbtn{font:650 11.5px/1 var(--mono);letter-spacing:.06em;color:var(--ink);background:var(--glass);border:1px solid var(--line2);border-radius:9px;padding:8px 11px;cursor:pointer;white-space:nowrap;-webkit-backdrop-filter:blur(10px);backdrop-filter:blur(10px)}
.hbtn:hover{border-color:var(--acc)}.hbtn[aria-pressed=true]{background:var(--acc);border-color:var(--acc);color:#fff}
.seg{display:inline-flex;border:1px solid var(--line2);border-radius:9px;overflow:hidden;background:var(--glass)}
.seg button{font:650 11px/1 var(--mono);letter-spacing:.06em;padding:8px 10px;border:0;background:transparent;color:var(--ink2);cursor:pointer}
.seg button[aria-pressed=true]{background:var(--ink);color:var(--bg)}
#world{position:relative;z-index:2;flex:1;min-height:0;perspective:1600px;perspective-origin:50% 30%;display:flex;flex-direction:column;padding:0 16px}
#cam,#par{position:relative;flex:1;min-height:0;display:flex;flex-direction:column;transform-style:preserve-3d}
#cam{transition:transform 1.9s cubic-bezier(.2,.75,.12,1)}
#par{transition:transform .5s ease-out}
#triad{position:relative;display:flex;justify-content:center;align-items:center;gap:clamp(18px,7vw,110px);padding:22px 0 14px;transform:translateZ(36px);transform-style:preserve-3d}
#beams{position:absolute;inset:0;width:100%;height:100%;overflow:visible;pointer-events:none}
#beams path{fill:none;stroke:var(--line2);stroke-width:1.4}
#beams path.live{stroke:var(--acc);stroke-width:2;stroke-dasharray:6 8;animation:flow 0.9s linear infinite;filter:drop-shadow(0 0 6px var(--acc))}
@keyframes flow{to{stroke-dashoffset:-28}}
.node{position:relative;display:flex;flex-direction:column;align-items:center;gap:6px;min-width:84px;transition:transform .9s cubic-bezier(.2,.75,.12,1),opacity .6s}
.node .core{position:relative;width:54px;height:54px;border-radius:50%;display:grid;place-items:center;font:750 19px/1 var(--mono);color:#fff;background:radial-gradient(circle at 35% 30%,color-mix(in oklab,var(--c) 60%,#fff),var(--c) 55%,color-mix(in oklab,var(--c) 50%,#000));box-shadow:0 0 0 1px color-mix(in oklab,var(--c) 50%,transparent),0 14px 30px -12px var(--c)}
.node .core::before,.node .core::after{content:"";position:absolute;inset:-8px;border-radius:50%;border:1px solid color-mix(in oklab,var(--c) 45%,transparent);border-top-color:transparent;animation:spin 7s linear infinite}
.node .core::after{inset:-15px;border-style:dashed;animation-duration:13s;animation-direction:reverse;opacity:.6}
@keyframes spin{to{transform:rotate(360deg)}}
.node[data-a=DEREK]{--c:var(--derek)}.node[data-a=XAVIER]{--c:var(--xavier)}.node[data-a=AUDREY]{--c:var(--audrey)}.node[data-a=MANAGEMENT]{--c:var(--mgmt)}
.node[data-a=MANAGEMENT] .core{width:40px;height:40px;font-size:14px}
.node .nm{font:650 12px/1 var(--mono);letter-spacing:.12em}
.node .st{font:650 9.5px/1 var(--mono);letter-spacing:.08em;padding:4px 7px;border-radius:99px;border:1px solid currentColor;white-space:nowrap;color:var(--neutral);background:var(--glass)}
.node.active{transform:translateZ(40px) scale(1.12)}
.node.active .core{box-shadow:0 0 0 2px var(--c),0 0 40px 4px color-mix(in oklab,var(--c) 55%,transparent)}
.node.dim{opacity:.55}
.st.s-EVALUATING{color:var(--acc)}.st.s-WAITING_FOR_EVIDENCE,.st.s-WAITING_FOR_PROVIDER,.st.s-RECOVERING{color:var(--warn)}.st.s-BLOCKED{color:#d9621c}.st.s-DECISION_RECORDED{color:var(--info)}.st.s-FAILED{color:var(--bad)}
.st.s-EVALUATING::before,.st.s-RECOVERING::before{content:"\25cf ";animation:blink 1.2s infinite}
@keyframes blink{50%{opacity:.25}}
#token{position:absolute;left:0;top:0;z-index:4;pointer-events:none;font:700 11px/1 var(--mono);letter-spacing:.04em;padding:7px 10px;border-radius:99px;color:#fff;background:var(--acc);box-shadow:0 0 0 4px color-mix(in oklab,var(--acc) 25%,transparent),0 10px 30px -6px var(--acc);opacity:0;transform:translate(-50%,-50%);white-space:nowrap}
#panelwrap{flex:1;min-height:0;display:flex;justify-content:center;transform-style:preserve-3d;padding-bottom:10px}
#panel{position:relative;width:min(1120px,100%);height:100%;overflow:auto;background:var(--glass);border:1px solid var(--line2);border-radius:20px;box-shadow:var(--shadow);
-webkit-backdrop-filter:blur(18px) saturate(1.3);backdrop-filter:blur(18px) saturate(1.3);padding:22px 26px 0;transform:rotateX(2deg);transform-origin:50% 0;transition:transform .55s cubic-bezier(.2,.75,.12,1),opacity .35s;scrollbar-width:thin}
#panel.swap{opacity:0;transform:rotateY(-10deg) rotateX(6deg) translateZ(-120px) translateX(40px)}
#panel::before{content:"";position:absolute;inset:0 0 auto;height:2px;border-radius:20px 20px 0 0;background:linear-gradient(90deg,transparent,var(--acc),transparent)}
.kicker{font:650 11px/1.2 var(--mono);letter-spacing:.14em;color:var(--acc);text-transform:uppercase;margin:0 0 6px}
.lead{font:600 clamp(17px,2.2vw,24px)/1.3 var(--display);letter-spacing:-.015em;margin:0 0 18px;max-width:62ch}
.lbl{font:650 10px/1.2 var(--mono);letter-spacing:.12em;text-transform:uppercase;color:var(--ink3)}
.g2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}
.g3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}
.g4{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}
.card{background:var(--glass2);border:1px solid var(--line);border-radius:14px;padding:13px 15px;min-width:0}
.card h4{margin:0 0 8px;font:650 13px/1.3 var(--display)}
.big{font:700 clamp(20px,2.6vw,30px)/1.1 var(--display);letter-spacing:-.02em;margin:6px 0 2px}
.sub{color:var(--ink3);font-size:12px}
.mono{font-family:var(--mono);font-size:12px}
.pos{color:var(--ok)}.neg{color:var(--bad)}
.rv{opacity:0;transform:translateY(10px);transition:opacity .5s,transform .6s cubic-bezier(.2,.75,.12,1)}
.rv.on{opacity:1;transform:none}
.rv.hl.on{animation:hl 1.6s ease-out 1}
@keyframes hl{0%{box-shadow:0 0 0 0 var(--acc)}40%{box-shadow:0 0 0 6px color-mix(in oklab,var(--acc) 30%,transparent)}100%{box-shadow:0 0 0 0 transparent}}
.bars{display:grid;grid-template-columns:minmax(110px,max-content) 1fr 56px;gap:7px 12px;align-items:center;font-size:12.5px}
.track{height:9px;border-radius:5px;background:var(--line);overflow:hidden}
.gw{display:block;height:100%;width:0;background:linear-gradient(90deg,color-mix(in oklab,var(--acc) 50%,transparent),var(--acc));border-radius:5px}
.gw.x{background:repeating-linear-gradient(135deg,var(--ink3) 0 3px,transparent 3px 6px)}
.tag{font:650 9px/1 var(--mono);letter-spacing:.08em;padding:3px 5px;border-radius:4px;border:1px dashed var(--ink3);color:var(--ink3);margin-left:6px;white-space:nowrap}
.q{display:flex;justify-content:space-between;gap:10px;padding:8px 10px;border-radius:9px;border:1px solid var(--line);margin-bottom:6px;font-size:12.5px;background:var(--glass2)}
.q.hl{border-color:var(--acc);background:color-mix(in oklab,var(--acc) 12%,var(--glass2))}
.chain{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:10px;margin:14px 0}
.chain .card{position:relative}.chain .big{font-size:clamp(17px,1.9vw,24px);white-space:nowrap}
.chain .card+.card::before{content:"\2192";position:absolute;left:-10px;top:50%;transform:translate(-50%,-50%);color:var(--ink3)}
.scale{position:relative;height:56px;margin:8px 4px 4px}
.scale .ax{position:absolute;left:0;right:0;top:26px;height:2px;background:var(--line2)}
.scale .edge{position:absolute;top:18px;height:18px;background:repeating-linear-gradient(135deg,color-mix(in oklab,var(--acc) 45%,transparent) 0 4px,transparent 4px 8px);border:1px solid var(--acc);border-radius:4px}
.scale .mk{position:absolute;top:0;transform:translateX(-50%);font:650 10.5px/1.2 var(--mono);text-align:center;white-space:nowrap}
.scale .mk::after{content:"";display:block;width:2px;height:22px;margin:3px auto 0;background:currentColor}
.chk{list-style:none;margin:0;padding:0}
.chk li{display:flex;gap:8px;align-items:flex-start;padding:5px 0;font-size:12.5px;border-bottom:1px dashed var(--line)}
.chk li::before{content:"\2713";flex:none;width:17px;height:17px;border-radius:50%;display:grid;place-items:center;font-size:10px;color:#fff;background:var(--ok);transform:scale(0);transition:transform .35s cubic-bezier(.3,1.6,.5,1)}
.chk li.on::before{transform:scale(1)}
.rec{font:12px/1.6 var(--mono);background:var(--glass2);border:1px solid var(--line);border-radius:12px;padding:12px 14px;white-space:pre-wrap;overflow-wrap:anywhere;margin:0}
.rec b{color:var(--acc);font-weight:650}
table{border-collapse:collapse;width:100%;font-size:12px}
th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{font:650 9.5px/1.25 var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--ink3);white-space:nowrap}
td.n,th.n{text-align:right;white-space:nowrap}
tr.sel td{background:color-mix(in oklab,var(--acc) 14%,transparent)}
tr.sel td:first-child{box-shadow:inset 3px 0 0 var(--acc)}
tr.ref td{color:var(--ink3)}
.tw{overflow-x:auto;border:1px solid var(--line);border-radius:12px;background:var(--glass2)}
.pill{display:inline-block;font:650 9.5px/1 var(--mono);letter-spacing:.08em;padding:4px 6px;border-radius:5px;border:1px solid currentColor;white-space:nowrap}
.p-ok{color:var(--ok)}.p-bad{color:var(--bad)}.p-warn{color:var(--warn)}.p-info{color:var(--info)}.p-n{color:var(--neutral);border-style:dashed}
.fb{display:flex;height:22px;border-radius:7px;overflow:hidden;border:1px solid var(--line2);margin:10px 0}
.fb .f{background:var(--acc);width:0;display:flex;align-items:center;justify-content:center;color:#fff;font:700 11px/1 var(--mono)}
.fb .o{flex:1;background:repeating-linear-gradient(135deg,color-mix(in oklab,var(--ink3) 50%,transparent) 0 4px,transparent 4px 8px);display:flex;align-items:center;justify-content:center;font:700 11px/1 var(--mono);color:var(--ink2)}
.tl{list-style:none;margin:0;padding:0 0 0 16px;border-left:2px solid var(--line2)}
.tl li{position:relative;padding:4px 0 10px 10px;font-size:12.5px}
.tl li::before{content:"";position:absolute;left:-23px;top:8px;width:10px;height:10px;border-radius:50%;background:var(--bg);border:2px solid var(--acc)}
.margin{display:grid;grid-template-columns:40fr 18fr 12fr 9fr 6.6fr 14.4fr;gap:2px;margin:8px 0 4px;font:650 10px/1.2 var(--mono)}
.margin>div{padding:6px 4px;text-align:center;background:var(--line);border-radius:4px;color:var(--ink2);overflow:hidden;white-space:nowrap}
.mrow{display:grid;grid-template-columns:52px 1fr;gap:8px;align-items:center;margin:3px 0}
.mrow .cells{display:grid;grid-template-columns:40fr 18fr 12fr 9fr 6.6fr 14.4fr;gap:2px}
.mrow .cells i{height:12px;border-radius:3px;background:var(--line);transform-origin:left;transform:scaleX(0);transition:transform .6s cubic-bezier(.2,.75,.12,1)}
.mrow.on .cells i{transform:scaleX(1)}
.mrow .cells i.h{background:color-mix(in oklab,var(--acc) 55%,transparent)}
.mrow .cells i.b{background:var(--ok)}
.callout{border-left:3px solid var(--acc);padding:9px 12px;border-radius:0 10px 10px 0;background:color-mix(in oklab,var(--acc) 9%,var(--glass2));font-size:12.5px}
.score{display:flex;align-items:center;justify-content:center;gap:28px;padding:14px;border-radius:14px;background:var(--ink);color:var(--bg);font:750 clamp(26px,4vw,44px)/1 var(--display);letter-spacing:-.02em}
.score small{display:block;font:650 10px/1 var(--mono);letter-spacing:.14em;opacity:.7;margin-bottom:6px}
.cat{border-radius:12px;padding:12px;min-width:0;background:var(--glass2)}
.cat h5{margin:0 0 4px;font:650 10.5px/1.25 var(--mono);letter-spacing:.08em;text-transform:uppercase}
.cat p{margin:4px 0 0;font-size:12px;color:var(--ink2)}
.c-ACTUAL{border:2px solid var(--ok)}.c-ACTUAL h5{color:var(--ok)}
.c-OBSERVED{border:2px solid var(--info);background:radial-gradient(color-mix(in oklab,var(--info) 22%,transparent) 1px,transparent 1.5px) 0 0/9px 9px,var(--glass2)}.c-OBSERVED h5{color:var(--info)}
.c-MODELLED{border:2px dashed var(--warn);background:repeating-linear-gradient(135deg,color-mix(in oklab,var(--warn) 10%,transparent) 0 6px,transparent 6px 12px),var(--glass2)}.c-MODELLED h5{color:var(--warn)}
.c-UNRESOLVED{border:2px dotted var(--neutral)}.c-UNRESOLVED h5{color:var(--neutral)}
.chat{display:flex;flex-direction:column;gap:9px}
.bubble{max-width:82%;padding:10px 12px;border-radius:13px;border:1px solid var(--line);background:var(--glass2);font-size:13px}
.bubble.me{align-self:flex-end;background:color-mix(in oklab,var(--mgmt) 16%,var(--glass2))}
.bubble .who{font:650 9.5px/1 var(--mono);letter-spacing:.1em;color:var(--ink3);margin-bottom:5px}
.scripted{font:700 10px/1.2 var(--mono);letter-spacing:.1em;color:var(--warn);border:1px dashed var(--warn);border-radius:6px;padding:5px 8px;display:inline-block;margin-bottom:10px}
.diff{font:12px/1.55 var(--mono);border-radius:10px;border:1px solid var(--line);overflow:hidden}
.diff div{padding:1px 10px;white-space:pre}
.diff .m{background:color-mix(in oklab,var(--bad) 14%,transparent)}.diff .p{background:color-mix(in oklab,var(--ok) 14%,transparent)}.diff .c{color:var(--ink3)}
.pipe{display:flex;gap:0;overflow-x:auto;margin:10px 0}
.pipe>div{flex:1 0 108px;padding:9px 10px;border:1px solid var(--line);background:var(--glass2);font-size:11.5px;position:relative}
.pipe>div:first-child{border-radius:10px 0 0 10px}.pipe>div:last-child{border-radius:0 10px 10px 0}.pipe>div+div{border-left:0}
.pipe>div.on{box-shadow:inset 0 -3px 0 var(--acc);background:color-mix(in oklab,var(--acc) 10%,var(--glass2))}
.pipe b{display:block;font:650 10px/1.2 var(--mono);letter-spacing:.06em}
.roles{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
.roles .card{text-align:center}.roles .ne{font:700 18px/1 var(--mono);color:var(--acc)}
.framelab{position:sticky;bottom:0;margin:18px -26px 0;padding:8px 26px;display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;font:700 10px/1.3 var(--mono);letter-spacing:.1em;color:var(--warn);
background:color-mix(in oklab,var(--bg) 94%,transparent);-webkit-backdrop-filter:blur(8px);backdrop-filter:blur(8px);border-top:1px dashed color-mix(in oklab,var(--warn) 50%,transparent)}
.framelab.prod{color:var(--ok);border-top-color:color-mix(in oklab,var(--ok) 50%,transparent)}
.transport{position:relative;z-index:5;display:flex;align-items:center;gap:10px;padding:10px 20px 14px}
.tbtn{width:38px;height:38px;border-radius:50%;border:1px solid var(--line2);background:var(--glass);color:var(--ink);cursor:pointer;display:grid;place-items:center;font:700 14px/1 var(--mono);flex:none}
.tbtn.play{width:46px;height:46px;background:var(--acc);border-color:var(--acc);color:#fff;font-size:16px;box-shadow:0 10px 30px -10px var(--acc)}
.scrub{position:relative;flex:1;height:38px;min-width:0}
.scrub .segs{position:absolute;left:0;right:0;top:15px;height:8px;display:flex;gap:3px}
.scrub .segs button{flex:1;min-width:0;height:8px;padding:0;border:0;border-radius:3px;background:var(--line2);cursor:pointer;position:relative;overflow:hidden}
.scrub .segs button i{position:absolute;inset:0;width:0;background:var(--acc)}
.scrub .segs button.done i{width:100%}
.scrub input{position:absolute;inset:0;width:100%;opacity:0;cursor:pointer;margin:0}
.time{font:600 11px/1 var(--mono);color:var(--ink3);white-space:nowrap;min-width:92px;text-align:right}
.drawer{position:fixed;top:0;bottom:0;right:0;z-index:30;width:min(440px,100%);background:var(--glass);border-left:1px solid var(--line2);-webkit-backdrop-filter:blur(22px);backdrop-filter:blur(22px);
transform:translateX(102%);transition:transform .45s cubic-bezier(.2,.75,.12,1);padding:20px 22px 30px;overflow:auto;box-shadow:var(--shadow)}
.drawer.left{right:auto;left:0;border-left:0;border-right:1px solid var(--line2);transform:translateX(-102%)}
.drawer.open{transform:none}
.drawer h2{margin:0 0 4px;font:680 20px/1.2 var(--display)}
.drawer h3{margin:18px 0 6px;font:650 11px/1.2 var(--mono);letter-spacing:.12em;text-transform:uppercase;color:var(--acc)}
.drawer p,.drawer li{font-size:13px;color:var(--ink2)}
.drawer code{font:12px var(--mono)}
.drawer .x{position:absolute;right:14px;top:14px}
.chlist{list-style:none;margin:12px 0 0;padding:0}
.chlist button{display:flex;gap:10px;width:100%;text-align:left;padding:9px 10px;border-radius:9px;border:1px solid transparent;background:transparent;color:var(--ink);cursor:pointer;font:13px/1.3 var(--sans)}
.chlist button:hover{border-color:var(--line2)}.chlist button.cur{border-color:var(--acc);background:color-mix(in oklab,var(--acc) 10%,transparent)}
.chlist .n{font:650 11px/1.3 var(--mono);color:var(--ink3);width:22px;flex:none}
#intro{position:fixed;inset:0;z-index:40;display:grid;place-items:center;background:radial-gradient(900px 600px at 50% 40%,color-mix(in oklab,var(--acc) 18%,transparent),transparent 70%),color-mix(in oklab,var(--bg) 82%,transparent);-webkit-backdrop-filter:blur(8px);backdrop-filter:blur(8px);transition:opacity .8s}
#intro.gone{opacity:0;pointer-events:none}
#intro .box{text-align:center;padding:24px;max-width:760px}
#intro .trio{display:flex;justify-content:center;gap:14px;margin-bottom:22px}
#intro .trio span{width:14px;height:14px;border-radius:50%}
#intro h2{font:750 clamp(30px,6vw,64px)/1.02 var(--display);letter-spacing:-.035em;margin:0 0 14px}
#intro h2 em{font-style:normal;background:linear-gradient(90deg,var(--derek),var(--xavier),var(--audrey));-webkit-background-clip:text;background-clip:text;color:transparent}
#intro p{color:var(--ink2);font-size:15px;margin:0 auto 22px;max-width:58ch}
#intro .go{font:700 14px/1 var(--sans);padding:14px 22px;border-radius:12px;border:0;background:var(--ink);color:var(--bg);cursor:pointer;box-shadow:0 20px 40px -20px var(--ink)}
#intro .hint{margin-top:14px;font:600 11px/1.5 var(--mono);color:var(--ink3);letter-spacing:.04em}
.kbd{font:600 10.5px/1 var(--mono);padding:2px 5px;border:1px solid var(--line2);border-radius:4px}
.prodcard{border:1px solid var(--line);border-radius:14px;padding:12px 14px;margin-bottom:12px;background:var(--glass2)}
.prodcard .h{display:flex;justify-content:space-between;gap:10px;align-items:baseline;flex-wrap:wrap;margin-bottom:6px}
.prodcard table.kv td:first-child{color:var(--ink3);width:36%;font:11.5px var(--mono)}
.prodcard .pill.st-OK{color:var(--ok)}.prodcard .pill.st-EMPTY{color:var(--neutral);border-style:dashed}.prodcard .pill.st-UNAVAILABLE{color:var(--bad)}.prodcard .pill.st-MISSING{color:var(--warn);border-style:dashed}
.prodcard .ev{font:11px var(--mono);padding:3px 6px;border:1px solid var(--line2);border-radius:5px;margin-right:4px;color:var(--acc);text-decoration:none}
.prodcard .unk{font:650 10px var(--mono);color:var(--warn)}
.prodcard .why{color:var(--ink2);font-size:12.5px;margin:2px 0 8px}
.prodcard .plain{padding:12px;border:1px dashed var(--line2);border-radius:10px}
.prodcard .plain .big{font-size:17px}
.gatebox{padding:16px;border-radius:12px;border:1px dashed var(--line2);background:var(--glass2)}
@media (max-width:900px){.g4,.chain{grid-template-columns:repeat(2,minmax(0,1fr))}.chain .card+.card::before{display:none}.g3{grid-template-columns:1fr}}
@media (max-width:640px){.hud{padding:8px 12px;gap:8px;flex-wrap:wrap}.ctitle{order:3;flex-basis:100%}.ctitle h1{white-space:normal}.hbtn{padding:7px 8px}
#world{padding:0 10px}#panel{padding:16px 16px 0;border-radius:16px}.framelab{margin:14px -16px 0;padding:8px 16px}
.g2,.g4,.roles{grid-template-columns:1fr}#triad{gap:12px;padding-bottom:8px}.node{min-width:0}.node .core{width:40px;height:40px;font-size:15px}.node .nm{font-size:10px}.node .st{font-size:8px;padding:3px 5px}
.transport{padding:8px 12px 12px;gap:7px}.time{display:none}.bars{grid-template-columns:minmax(80px,40%) 1fr 44px}.banner{font-size:9.5px;letter-spacing:.08em}}
@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation:none!important;transition-duration:.01s!important}#cam,#par{transform:none!important}}
"""

DEMO_BODY = r"""
<div id="stage">
<canvas id="bg" aria-hidden="true"></canvas>
<div class="banner" id="banner" role="status">%%LABEL%%</div>
<header class="hud">
<a class="brand" href="%%HOME%%"><i></i>BETTOR</a>
<div class="ctitle"><span class="n" id="cnum">01 / 14</span><h1 id="ctitle">Derek finds the Yankees opportunity</h1></div>
%%MODE_SWITCH%%
<button class="hbtn" id="chapters" type="button" aria-controls="chdrawer">Chapters</button>
<button class="hbtn" id="inspectBtn" type="button" aria-controls="inspect" aria-pressed="false">Inspect <span class="kbd">I</span></button>
<button class="hbtn" id="soundBtn" type="button" aria-pressed="false" title="Original score generated live with the Web Audio API">&#9834; Sound off</button>
</header>
<div id="world"><div id="cam"><div id="par">
<div id="triad">
<svg id="beams" aria-hidden="true"></svg>
<div class="node" data-a="DEREK"><div class="core">D</div><div class="nm">DEREK</div><div class="st">IDLE</div></div>
<div class="node" data-a="XAVIER"><div class="core">X</div><div class="nm">XAVIER</div><div class="st">IDLE</div></div>
<div class="node" data-a="AUDREY"><div class="core">A</div><div class="nm">AUDREY</div><div class="st">IDLE</div></div>
<div class="node" data-a="MANAGEMENT"><div class="core">M</div><div class="nm">MANAGEMENT</div><div class="st">HUMAN</div></div>
<div id="token"></div>
</div>
<div id="panelwrap"><article id="panel" aria-live="polite"></article></div>
</div></div></div>
<footer class="transport">
<button class="tbtn" id="prev" type="button" aria-label="Previous chapter">&#9664;&#9664;</button>
<button class="tbtn play" id="play" type="button" aria-label="Play">&#9654;</button>
<button class="tbtn" id="next" type="button" aria-label="Next chapter">&#9654;&#9654;</button>
<div class="scrub"><div class="segs" id="segs"></div><input type="range" id="scrub" min="0" max="1000" value="0" aria-label="Scrub the walkthrough"></div>
<span class="time" id="time">0:00 / 0:00</span>
</footer>
</div>
<aside class="drawer" id="inspect" aria-label="Inspect this step"><button class="hbtn x" type="button" data-close="inspect">Close</button><div id="inspectBody"></div></aside>
<aside class="drawer left" id="chdrawer" aria-label="Chapters"><button class="hbtn x" type="button" data-close="chdrawer">Close</button><h2>Chapters</h2><p class="sub">Keys: <span class="kbd">Space</span> play/pause &middot; <span class="kbd">&larr;</span><span class="kbd">&rarr;</span> chapter &middot; <span class="kbd">,</span><span class="kbd">.</span> seek &middot; <span class="kbd">1</span>&ndash;<span class="kbd">0</span> jump &middot; <span class="kbd">I</span> inspect &middot; <span class="kbd">M</span> sound%%MODE_KEY%%</p><ul class="chlist" id="chlist"></ul></aside>
<div id="intro"><div class="box">
<div class="trio"><span style="background:var(--derek)"></span><span style="background:var(--xavier)"></span><span style="background:var(--audrey)"></span></div>
<h2>Three agents.<br><em>One execution authority.</em></h2>
<p>Derek finds and enters. Xavier manages what was actually filled. Audrey audits both, takes direction from management and turns it into tested, separately approved change. Fourteen chapters, one position, every number explained.</p>
<button class="go" id="begin" type="button">Play the walkthrough</button>
<div class="hint">%%INTRO_HINT%%<br>Sound is off. Press <span class="kbd">M</span> for the original score.</div>
</div></div>
"""

DEMO_JS = r"""
(function () {
  'use strict';
  var SERVED = %%SERVED%%;
  var LABEL = '%%LABEL%%';
  var mq = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : {matches: false};
  var REDUCE = mq.matches;
  function $(id) { return document.getElementById(id); }
  function esc(v) { return String(v === null || v === undefined ? '' : v).replace(/[&<>"']/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]; }); }
  function clamp(x, a, b) { return Math.max(a, Math.min(b, x)); }
  function ease(x) { return 1 - Math.pow(1 - x, 3); }

  // ── the illustrative economics, computed, never typed ──────────────
  // Published Polymarket US taker schedule: fee = 0.0695 x contracts x p x (1-p),
  // rounded up to the cent per order (illustrative rounding).
  var THETA = 0.0695;
  function fee(q, p) { return Math.ceil(THETA * q * p * (1 - p) * 100 - 1e-9) / 100; }
  function m(v, signed) {
    var a = Math.abs(v), s = a.toFixed(2);
    if (a !== 0 && a < 1 && Math.abs(a - +s) > 1e-9) s = a.toFixed(4).replace(/0+$/, '');
    if (v < 0) return '<span class="neg">−$' + s + '</span>';
    return (signed && v > 0 ? '<span class="pos">+$' : '$') + s + (signed && v > 0 ? '</span>' : '');
  }
  var ENTRY = {price: 0.50, p: 0.60, ordered: 100, filled: 30};
  ENTRY.edgePP = (ENTRY.p - ENTRY.price) * 100;
  ENTRY.gross = ENTRY.p - ENTRY.price;
  ENTRY.grossRet = ENTRY.gross / ENTRY.price;
  ENTRY.feePer = THETA * ENTRY.price * (1 - ENTRY.price);
  ENTRY.netEV = ENTRY.gross - ENTRY.feePer;
  ENTRY.netROI = ENTRY.netEV / (ENTRY.price + ENTRY.feePer);
  ENTRY.planFee = fee(ENTRY.ordered, ENTRY.price);
  ENTRY.fillFee = fee(ENTRY.filled, ENTRY.price);
  var Q = ENTRY.filled, BASIS = +(Q * ENTRY.price + ENTRY.fillFee).toFixed(2);
  var STATES = ['RS', 'Y1', 'Y2', 'Y3', 'Y4', 'Y5'];
  var SLAB = {RS: 'Red Sox win', Y1: 'NYY by 1', Y2: 'NYY by 2', Y3: 'NYY by 3', Y4: 'NYY by 4', Y5: 'NYY by 5+'};
  var PS = {RS: 0.40, Y1: 0.18, Y2: 0.12, Y3: 0.09, Y4: 0.066, Y5: 0.144};
  var MARG = {RS: 0, Y1: 1, Y2: 2, Y3: 3, Y4: 4, Y5: 5};
  var BID = 0.55, FLOOR = -6.00, CAND_FLOOR = -4.50;
  function alt(name, kind, cap, fees, pay, extra) {
    var net = {}; STATES.forEach(function (s) { net[s] = +pay(s).toFixed(2); });
    var E = STATES.reduce(function (a, s) { return a + PS[s] * net[s]; }, 0);
    var worst = Math.min.apply(null, STATES.map(function (s) { return net[s]; }));
    var pp = STATES.filter(function (s) { return net[s] > 0; }).reduce(function (a, s) { return a + PS[s]; }, 0);
    return Object.assign({name: name, kind: kind, E: +E.toFixed(2), worst: worst, pp: pp, cap: cap, fees: fees, net: net, pboth: null, q: null, unp: Q, dur: '2 h 40 min'}, extra || {});
  }
  var ALTS = [];
  ALTS.push(alt('HOLD', 'HOLD', 0, 0, function (s) { return (s === 'RS' ? 0 : Q) - BASIS; }));
  var exitFee = fee(Q, BID);
  ALTS.push(alt('EXIT 30 @ $0.55', 'EXIT', 0, exitFee, function () { return Q * BID - exitFee - BASIS; }, {unp: 0, q: 30, dur: 'released now'}));
  var redFee = fee(15, BID);
  ALTS.push(alt('REDUCE 15 @ $0.55', 'REDUCE', 0, redFee, function (s) { return 15 * BID - redFee + (s === 'RS' ? 0 : 15) - BASIS; }, {unp: 15, q: 15}));
  var RUNGS = [{k: 1.5, price: 0.60, depth: 25}, {k: 2.5, price: 0.72, depth: 20}, {k: 3.5, price: 0.82, depth: 30}, {k: 4.5, price: 0.89, depth: 30}];
  RUNGS.forEach(function (r) {
    var q = Math.min(r.depth, Q), f = fee(q, r.price), c = +(q * r.price + f).toFixed(2);
    var cov = function (s) { return s === 'RS' || MARG[s] <= r.k; };
    r.pcover = STATES.filter(cov).reduce(function (a, s) { return a + PS[s]; }, 0);
    r.pair = ENTRY.price + r.price; r.single = 1 - r.pair; r.dbl = 2 - r.pair;
    r.pboth = STATES.filter(function (s) { return s !== 'RS' && cov(s); }).reduce(function (a, s) { return a + PS[s]; }, 0);
    r.alt = alt('Red Sox +' + r.k + ' × ' + q, 'HEDGE', c, f, function (s) { return (s === 'RS' ? 0 : Q) + (cov(s) ? q : 0) - BASIS - c; }, {q: q, unp: Q - q, pboth: r.pboth, cov: cov, rung: r});
    ALTS.push(r.alt);
  });
  var HOLD = ALTS[0];
  ALTS.forEach(function (a) { a.inc = +(a.E - HOLD.E).toFixed(2); a.eligible = a.kind === 'HOLD' ? a.worst >= FLOOR : a.worst >= FLOOR; a.candEligible = a.worst >= CAND_FLOOR; });
  var CHOSEN = ALTS.filter(function (a) { return a.eligible; }).sort(function (a, b) { return b.E - a.E; })[0];
  var CAND_CHOSEN = ALTS.filter(function (a) { return a.candEligible; }).sort(function (a, b) { return b.E - a.E; })[0];
  var FINAL = 'Y4';

  // ── reveal helpers (deterministic in local progress, so scrubbing works) ─
  function R(t, inner, cls, tag) { tag = tag || 'div'; return '<' + tag + ' class="rv ' + (cls || '') + '" data-t="' + t + '">' + inner + '</' + tag + '>'; }
  function C(to, dec, pre, suf, t0, t1) { return '<span class="cu" data-to="' + to + '" data-dec="' + dec + '" data-pre="' + esc(pre || '') + '" data-suf="' + esc(suf || '') + '" data-t0="' + t0 + '" data-t1="' + t1 + '">' + esc(pre || '') + (0).toFixed(dec) + esc(suf || '') + '</span>'; }
  function W(pct, t0, t1, cls) { return '<i class="gw ' + (cls || '') + '" data-w="' + pct + '" data-t0="' + t0 + '" data-t1="' + t1 + '"></i>'; }
  function pill(t, c) { return '<span class="pill ' + c + '">' + esc(t) + '</span>'; }
  function lead(k, l) { return '<p class="kicker">' + k + '</p><p class="lead">' + l + '</p>'; }

  function altTable(opts) {
    opts = opts || {};
    var h = '<div class="tw"><table><thead><tr><th>Alternative</th><th class="n">Expected net P&amp;L</th><th class="n">vs HOLD</th><th class="n">Worst case</th><th class="n">P(net profit)</th><th class="n">P(both win)</th><th class="n">Capital required</th><th>Capital duration</th><th class="n">Executable</th><th class="n">Unpaired</th><th class="n">Fees</th><th>Eligibility</th></tr></thead><tbody>';
    ALTS.forEach(function (a, i) {
      var sel = opts.select && a === CHOSEN;
      h += '<tr class="rv' + (sel ? ' sel' : '') + '" data-t="' + (opts.t0 + i * 0.035).toFixed(3) + '"><td>' + esc(a.name) + (sel ? ' ' + pill('SELECTED', 'p-info') : '') + '</td><td class="n">' + m(a.E, true) + '</td><td class="n">' + (a.kind === 'HOLD' ? '—' : m(a.inc, true)) + '</td><td class="n">' + m(a.worst, true) + '</td><td class="n">' + a.pp.toFixed(3) + '</td><td class="n">' + (a.pboth === null ? '<span class="sub">n/a</span>' : a.pboth.toFixed(3)) + '</td><td class="n">' + m(a.cap) + '</td><td>' + a.dur + '</td><td class="n">' + (a.q === null ? '—' : a.q) + '</td><td class="n">' + a.unp + '</td><td class="n">' + m(a.fees) + '</td><td>' + (a.eligible ? pill('ELIGIBLE', 'p-ok') : pill('REFUSED · worst < −$6.00', 'p-bad')) + '</td></tr>';
    });
    return h + '</tbody></table></div>';
  }

  // ── the fourteen chapters ──────────────────────────────────────────
  var CH = [
  {a: 'DEREK', t: 'Derek finds the Yankees opportunity', dur: 10, music: 0, cam: [-7, 3, 30],
   st: {DEREK: 'EVALUATING', XAVIER: 'IDLE', AUDREY: 'IDLE'},
   scene: function () {
     var cats = [['MLB moneyline', 214, 0], ['MLB run line (hedge side)', 188, 0], ['NFL spread', 96, 0], ['Player props', 1204, 1], ['Futures', 77, 1], ['In-play', 0, 2]];
     var h = lead('01 · Discovery', 'Derek walks the whole catalogue, counts what it cannot trade as carefully as what it can, and queues what it can evaluate.');
     h += '<div class="g2"><div class="card"><div class="lbl">Catalogue census · every category</div><div class="bars" style="margin-top:10px">';
     cats.forEach(function (c, i) {
       var t = 0.06 + i * 0.05;
       h += R(t, esc(c[0]) + (c[2] === 1 ? '<span class="tag">UNSUPPORTED</span>' : c[2] === 2 ? '<span class="tag">EXCLUDED · LATENCY</span>' : ''), '', 'div') + '<div class="track">' + W(c[1] / 1204 * 100, t, t + 0.25, c[2] ? 'x' : '') + '</div><div class="mono" style="text-align:right">' + C(c[1], 0, '', '', t, t + 0.25) + '</div>';
     });
     h += '</div><p class="sub" style="margin-top:10px">Unsupported markets are counted and named, not silently dropped.</p></div>';
     h += '<div class="card"><div class="lbl">Opportunity queue</div><div style="margin-top:10px">';
     [['LAD @ SD · Dodgers to win', 'no qualified internal probability', 0.3], ['HOU @ SEA · Astros to win', 'Pinnacle quote 94 s old', 0.36], ['NYY @ BOS · Yankees to win · YES ask $0.50', 'EVALUATING', 0.5], ['ATL @ NYM · Braves to win', 'edge below threshold', 0.42]].forEach(function (r) {
       var hl = r[1] === 'EVALUATING';
       h += R(r[2], '<span>' + esc(r[0]) + '</span><span class="mono" style="color:' + (hl ? 'var(--acc)' : 'var(--ink3)') + '">' + esc(r[1]) + '</span>', 'q' + (hl ? ' hl' : ''));
     });
     return h + R(0.62, '<div class="callout" style="margin-top:10px"><b>Currently contemplating</b> is the persisted evaluation state on <span class="mono">agent_status</span>: “evaluating NYY @ BOS moneyline”. It is a record, not an invented thought.</div>') + '</div></div></div>';
   },
   inspect: '<h3>What the real system does</h3><p>Each collection cycle Derek reads the venue catalogue and writes a census by category, including unsupported and excluded markets, so coverage gaps are visible rather than inferred. Candidates enter the opportunity queue with their current blocker and its dependency class (ENGINEERING_CONFIGURATION, EVIDENCE, ENGINEERING, ELAPSED_TIME, OWNER_DECISION).</p><h3>Where it is recorded</h3><p><code>agent_status</code> (state, activity), Derek’s coverage census, <code>external_valuations</code> for each evaluation.</p><h3>What this does not mean</h3><p>Being in the queue is not a decision, and no order exists at this point.</p>'},

  {a: 'DEREK', t: 'Probability edge and net economics', dur: 12, music: 1, cam: [-6, 2, 30],
   st: {DEREK: 'EVALUATING', XAVIER: 'IDLE', AUDREY: 'IDLE'},
   scene: function () {
     var h = lead('02 · Economics', 'Two qualified probabilities, one price. Gross edge, gross return, net EV and sizing are four different numbers.');
     h += '<div class="g2">' + R(0.04, '<div class="lbl">Pinnacle · reference</div><div class="big">' + C(0.6, 3, '', '', 0.06, 0.2) + '</div><div class="sub">de-vigged · observed 23:02:11Z · age 4 s · <b>QUALIFIED</b></div>', 'card')
       + R(0.1, '<div class="lbl">Internal · model</div><div class="big">' + C(0.6, 3, '', '', 0.12, 0.26) + '</div><div class="sub">calibrated cohort · computed 23:02:12Z · <b>QUALIFIED</b></div>', 'card') + '</div>';
     h += R(0.22, '<div class="scale"><div class="ax"></div><div class="edge" style="left:50%;width:10%"></div><div class="mk" style="left:50%;color:var(--ink2)">price $0.50</div><div class="mk" style="left:60%;color:var(--acc)">p 0.600</div><div class="mk" style="left:0;color:var(--ink3)">0</div><div class="mk" style="left:100%;color:var(--ink3)">1</div></div>');
     h += '<div class="chain">'
       + R(0.3, '<div class="lbl">Gross edge</div><div class="big">' + C(ENTRY.edgePP, 2, '+', ' pp', 0.3, 0.4) + '</div><div class="sub">0.600 − 0.500</div>', 'card')
       + R(0.38, '<div class="lbl">Gross / contract</div><div class="big">' + C(ENTRY.gross, 2, '$', '', 0.38, 0.46) + '</div><div class="sub">pays $1.00 w.p. 0.60</div>', 'card')
       + R(0.46, '<div class="lbl">Gross return</div><div class="big">' + C(ENTRY.grossRet * 100, 1, '', '%', 0.46, 0.54) + '</div><div class="sub">$0.10 on $0.50</div>', 'card')
       + R(0.54, '<div class="lbl">Taker fee</div><div class="big">' + C(ENTRY.feePer, 4, '$', '', 0.54, 0.62) + '</div><div class="sub">0.0695 · p · (1−p)</div>', 'card')
       + R(0.62, '<div class="lbl">Net EV / contract</div><div class="big">' + C(ENTRY.netEV, 4, '$', '', 0.62, 0.7) + '</div><div class="sub">after fees</div>', 'card hl')
       + R(0.7, '<div class="lbl">Net ROI</div><div class="big">' + C(ENTRY.netROI * 100, 1, '', '%', 0.7, 0.78) + '</div><div class="sub">on $' + (ENTRY.price + ENTRY.feePer).toFixed(4) + ' deployed</div>', 'card') + '</div>';
     return h + R(0.8, '<div class="callout"><b>Sizing is separate:</b> ' + ENTRY.ordered + ' contracts, $' + (ENTRY.ordered * ENTRY.price).toFixed(2) + ' plus an estimated ' + m(ENTRY.planFee) + ' fee, set by the risk policy’s per-fixture limit. A bigger edge does not by itself buy a bigger size, and no agent can edit that limit.</div>');
   },
   inspect: '<h3>The arithmetic</h3><p>Blended probability = (internal 0.60 + Pinnacle 0.60) / 2 = 0.60. Gross edge = blended probability − price = 0.60 − 0.50 = <b>10 pp</b>. Gross value per contract = $0.10, a <b>20%</b> gross return on the $0.50 price. The published taker fee is 0.0695 × contracts × p × (1−p) = $0.0174 per contract here, so net EV = <b>$0.0826</b> per contract, <b>16.0%</b> on the $0.5174 deployed. Orders round fees up to the cent: 100 contracts = $1.74.</p><h3>Why two probabilities</h3><p>Pinnacle (reference) and the internal model are shown separately with their own timestamps and qualification. The active policy (DEREK_ENTRY_POLICY_V2) enters on their average, (internal + Pinnacle) / 2, and records all three. Either one missing or failing qualification blocks the entry by name; nothing is averaged with a missing value. The internal model is trained on market prices, so the average combines two market-derived estimates and is not independent confirmation.</p><h3>Illustrative</h3><p>Every price and probability in rehearsal is illustrative.</p>'},

  {a: 'DEREK', t: 'Identity, evidence, risk and execution checks', dur: 10, music: 1, cam: [-5, 2, 30],
   st: {DEREK: 'EVALUATING', XAVIER: 'IDLE', AUDREY: 'IDLE'},
   scene: function () {
     var cols = [['Identity', ['Venue market mapped to Pinnacle event and side (YES = Yankees win)', 'Fixture start 23:05 ET; not in play', 'Market rules: regulation plus extras, no tie outcome']],
       ['Evidence', ['Pinnacle quote age 4 s ≤ 30 s limit', 'Internal probability QUALIFIED (calibrated cohort)', 'Book snapshot 2 s old; ask $0.50 visible']],
       ['Risk', ['Fixture exposure $0.00 + $51.74 ≤ per-fixture limit', 'Daily loss budget intact', 'Limits read-only to every agent']],
       ['Execution', ['Venue open; account reconciled', 'One execution path: bettor_funded_execution', 'No automatic retry of an ambiguous submission']]];
     var h = lead('03 · Checks', 'Nothing is entered on edge alone. Four families of checks must all pass, and each failure would carry its own name.');
     h += '<div class="g4">';
     cols.forEach(function (c, i) {
       h += R(0.05 + i * 0.05, '<h4>' + c[0] + '</h4><ul class="chk">' + c[1].map(function (x, j) { return '<li class="rv" data-t="' + (0.18 + i * 0.12 + j * 0.035).toFixed(3) + '">' + esc(x) + '</li>'; }).join('') + '</ul>', 'card');
     });
     return h + '</div>' + R(0.78, '<div class="callout" style="margin-top:14px"><b>All four families pass.</b> Had one failed, the candidate would stay in the queue with that blocker and its dependency class; the production build today holds funded submission switched off, which is itself an OWNER_DECISION blocker.</div>');
   },
   inspect: '<h3>What the real system does</h3><p>Checks run in the deterministic entry lane, not in a model. Identity binds venue market, event and side; evidence checks quote ages and qualification; risk reads the limits (which no agent may change); execution confirms venue and account state and that the one execution authority is available.</p><h3>Production today</h3><p>FUNDED_SUBMISSION_ENABLED, REAL_ORDER_SUBMISSION_ENABLED and FUNDED_EXIT_SUBMISSION_ENABLED are False in this build. The rehearsal shows what happens after the owner enables them; it does not enable anything.</p>'},

  {a: 'DEREK', t: 'Decision and plan persisted', dur: 9, music: 2, cam: [-5, 3, 30],
   st: {DEREK: 'DECISION_RECORDED', XAVIER: 'IDLE', AUDREY: 'IDLE'},
   scene: function () {
     var h = lead('04 · Decision', 'The decision and its plan are written before anything is sent, with links to the evidence they rest on.');
     h += '<div class="g2">' + R(0.08, '<pre class="rec"><b>agent_decisions</b>\ndecision_ref   DRK-1002-0412\nagent_id       DEREK\nkind           ENTRY\nsubject        NYY @ BOS · Yankees to win · YES\nverdict        <b>ENTER</b>\nsummary        edge +10.00 pp · net EV $0.0826/ct · size 100\nevidence_refs  external_valuations#88412\n               bettor_funded_intents#FI-7Q2\ndecided_at     23:02:13.904Z</pre>')
       + R(0.24, '<pre class="rec"><b>bettor_funded_intents</b> (the plan)\nintent_id      FI-7Q2\nside           BUY YES\nqty            100\nlimit          $0.50\nexpiry         60 s\nplan_digest    9f3c…51a0\nstate          PLANNED\n\n<b>execution authority</b>\nbettor_funded_execution (deterministic)\nno model in the order path</pre>') + '</div>';
     return h + R(0.6, '<div class="callout" style="margin-top:14px">The decision index links to the authoritative records; it never copies the economics. Derek’s recorded state is now <b>DECISION_RECORDED</b>.</div>');
   },
   inspect: '<h3>Where it is recorded</h3><p><code>agent_decisions</code> is an index: its <code>evidence_refs</code> point at <code>external_valuations</code> (the evaluation) and <code>bettor_funded_intents</code> (the plan). The one authoritative accounting system is <code>bettor_funded_intents / fills / economics</code>; agents read it and link to it.</p><h3>Why before sending</h3><p>A plan persisted before submission makes every later state (acknowledged, partially filled, expired) attributable to one intent.</p>'},

  {a: 'DEREK', t: 'Acknowledgement, partial fill and full fill are different', dur: 11, music: 2, cam: [-4, 3, 20],
   st: {DEREK: 'WAITING_FOR_EVIDENCE', XAVIER: 'IDLE', AUDREY: 'IDLE'},
   scene: function () {
     var h = lead('05 · Fills', 'An acknowledgement is not a fill, and a partial fill is not the plan. Only what actually filled becomes a position.');
     h += '<div class="g2"><div class="card"><div class="lbl">Order lifecycle · FI-7Q2</div><ul class="tl" style="margin-top:10px">'
       + R(0.08, '<b>23:02:14.210</b> submitted · BUY YES 100 @ ≤ $0.50', '', 'li')
       + R(0.2, '<b>23:02:14.388</b> ' + pill('ACKNOWLEDGED', 'p-info') + ' venue accepted the order · <b>0 filled</b>', '', 'li')
       + R(0.34, '<b>23:02:14.402</b> ' + pill('PARTIAL FILL', 'p-warn') + ' 30 @ $0.50 · fee ' + m(ENTRY.fillFee), '', 'li')
       + R(0.5, '<b>23:03:14.210</b> ' + pill('EXPIRED', 'p-n') + ' remaining 70 unfilled; nothing retried', '', 'li')
       + R(0.6, '<span class="sub">A full fill would read 100 / 100. It did not happen, and the page never implies it.</span>', '', 'li') + '</ul></div>';
     h += '<div class="card"><div class="lbl">Quantities</div><div class="g3" style="margin-top:8px"><div><div class="sub">Ordered</div><div class="big">' + C(100, 0, '', '', 0.06, 0.16) + '</div></div><div><div class="sub">Filled</div><div class="big" style="color:var(--acc)">' + C(30, 0, '', '', 0.34, 0.44) + '</div></div><div><div class="sub">Outstanding</div><div class="big">' + C(70, 0, '', '', 0.34, 0.44) + '</div></div></div>'
       + '<div class="fb"><div class="f cuw" data-w="30" data-t0="0.34" data-t1="0.46">30 FILLED</div><div class="o">70 OUTSTANDING</div></div>'
       + R(0.66, '<div class="callout"><b>30 transferred to Xavier.</b> 70 outstanding stays with Derek’s plan until it fills or ends, and is never managed as a position.</div>') + '</div></div>';
     return h;
   },
   inspect: '<h3>The three states</h3><p><b>Acknowledged</b>: the venue accepted the order; quantity filled may be zero. <b>Partial</b>: some quantity filled (30 of 100). <b>Full</b>: the ordered quantity filled. Fills are recorded one by one in <code>bettor_funded_fills</code>.</p><h3>No retry</h3><p>An ambiguous submission is never retried automatically; it goes to recovery and reconciliation.</p><h3>Handoff</h3><p><code>agent_position_handoffs</code> records ordered, confirmed and outstanding quantity; <code>agent_handoff_fills</code> is keyed by fill id so a replayed fill cannot be counted twice.</p>'},

  {a: 'XAVIER', t: 'Xavier receives the confirmed position', dur: 9, music: 2, cam: [0, 3, 0], flow: ['DEREK', 'XAVIER', '30 filled · basis $' + BASIS.toFixed(2)],
   st: {DEREK: 'IDLE', XAVIER: 'EVALUATING', AUDREY: 'IDLE'},
   scene: function () {
     var h = lead('06 · Handoff', 'Ownership moves with confirmed fills, not with intentions. Xavier now owns 30 contracts and nothing else.');
     h += '<div class="g2">' + R(0.1, '<pre class="rec"><b>agent_position_handoffs</b>\nentry_intent_id    FI-7Q2\nportfolio_group_id PG-7Q2\nfrom_agent         DEREK\nowner_agent        <b>XAVIER</b>\nordered_qty        100\nconfirmed_qty      <b>30</b>\noutstanding_qty    70 → 0 (expired)\nfirst_fill_id      F-3318 · 23:02:14.402Z\nhandoff_at         23:02:14.9Z</pre>')
       + R(0.3, '<div class="card"><div class="lbl">Position group PG-7Q2</div><div class="g2" style="margin-top:8px"><div><div class="sub">Primary</div><div class="big">30</div><div class="sub">Yankees YES</div></div><div><div class="sub">Basis</div><div class="big">' + m(BASIS) + '</div><div class="sub">30 × $0.50 + ' + m(ENTRY.fillFee) + ' fee</div></div><div><div class="sub">Hedge</div><div class="big">0</div></div><div><div class="sub">Next review</div><div class="big">now</div><div class="sub">servicing cadence</div></div></div></div>') + '</div>';
     return h + R(0.6, '<div class="callout" style="margin-top:14px">Xavier’s servicing runs on its own cadence, independent of Derek’s collection cycle. With nothing owned, a servicing pass records that it had nothing to service; it is never shown as management.</div>');
   },
   inspect: '<h3>What the real system does</h3><p>A post-ingest hook on the book records each confirmed fill against the entry intent. The first fill creates the handoff; later fills add to <code>confirmed_qty</code>. Xavier’s servicing task picks the group up on its next pass.</p><h3>What this does not mean</h3><p>Receiving a position does not mean it is profitable or that any management action will be taken.</p>'},

  {a: 'XAVIER', t: 'Xavier compares the ladder with HOLD, EXIT and REDUCE', dur: 16, music: 3, cam: [2, 4, 0],
   st: {DEREK: 'IDLE', XAVIER: 'EVALUATING', AUDREY: 'IDLE'},
   scene: function () {
     var h = lead('07 · Alternatives', 'Every alternative is priced on the same states and fees. Wider is not automatically better.');
     h += '<div class="g2"><div class="card"><div class="lbl">Where each Red Sox line also pays · final margin</div><div class="margin">' + STATES.map(function (s) { return '<div title="' + (PS[s] * 100).toFixed(1) + '%">' + SLAB[s].replace('NYY ', '') + '</div>'; }).join('') + '</div>';
     RUNGS.forEach(function (r, i) {
       h += '<div class="mrow rv" data-t="' + (0.08 + i * 0.06).toFixed(2) + '"><span class="mono">+' + r.k + '</span><div class="cells">' + STATES.map(function (s) { var c = r.alt.cov(s); return '<i class="' + (c ? (s === 'RS' ? 'h' : 'b') : '') + '"></i>'; }).join('') + '</div></div>';
     });
     h += '<div class="sub" style="margin-top:6px"><span style="color:var(--ok)">■</span> both legs win &nbsp; <span style="color:var(--acc)">■</span> hedge alone wins &nbsp; □ Yankees alone win</div></div>';
     var r1 = RUNGS[0], r4 = RUNGS[3];
     h += '<div>' + R(0.32, '<div class="callout"><b>The $1.10 case.</b> Yankees $0.50 + Red Sox +1.5 $0.60 = <b>$1.10</b> per pair. One winner pays $1.00: ' + m(r1.single, true) + ' per pair. Both win (Yankees by exactly 1) pays $2.00: ' + m(r1.dbl, true) + ' per pair.</div>')
       + R(0.4, '<div class="callout" style="margin-top:8px"><b>+4.5 overlaps Yankees wins by 1–4 runs</b> (P(both) ' + r4.pboth.toFixed(3) + ') but costs $' + r4.price.toFixed(2) + ': a single winner loses ' + m(-r4.single) + ' per pair, and its worst case on 30 is ' + m(r4.alt.worst, true) + '. More overlap, not a better choice.</div>')
       + R(0.48, '<div class="callout" style="margin-top:8px"><b>Depth is real.</b> +1.5 has 25 executable at $0.60 and +2.5 has 20: the rest would stay unpaired.</div>') + '</div></div>';
     return h + '<div style="margin-top:14px">' + altTable({t0: 0.55, select: false}) + '</div>';
   },
   inspect: '<h3>How every row is computed</h3><p>Six settlement states with qualified probabilities (Red Sox win 0.40; Yankees by 1: 0.18, 2: 0.12, 3: 0.09, 4: 0.066, 5+: 0.144). For each alternative the net P&L in every state includes the entry basis ($15.53), the hedge or exit price and the fee rounded up per order. Expected net = Σ P(state) × net; worst case = the minimum; P(net profit) = total probability of states with net > 0.</p><h3>Policy</h3><p>Rehearsal policy: maximise expected net P&L subject to worst case ≥ −$6.00 on the group. HOLD (−$15.53), REDUCE (−$7.54) and +2.5 to +4.5 are refused by name.</p><h3>Real records</h3><p><code>bettor_xavier_decisions</code> keeps one pre-action record per review with every alternative, its eligibility and the reason for any refusal.</p>'},

  {a: 'XAVIER', t: 'The chosen action and its residual exposure', dur: 13, music: 3, cam: [2, 3, 0],
   st: {DEREK: 'IDLE', XAVIER: 'DECISION_RECORDED', AUDREY: 'IDLE'},
   scene: function () {
     var c = CHOSEN, states = [['RS', 'Red Sox win'], ['Y1', 'NYY by 1'], ['Y2', 'NYY by 2+']];
     var ex = ALTS[1];
     var h = lead('08 · Selection', 'Xavier selects ' + esc(c.name) + ': the best expected value that keeps the worst case inside the bound, with 5 contracts left unpaired and said so.');
     h += '<div class="g2">' + R(0.06, '<div class="lbl">Selected</div><div class="big" style="color:var(--acc)">' + esc(c.name) + '</div><div class="g3" style="margin-top:8px"><div><div class="sub">Expected net</div><b>' + m(c.E, true) + '</b></div><div><div class="sub">vs HOLD</div><b>' + m(c.inc, true) + '</b></div><div><div class="sub">Worst case</div><b>' + m(c.worst, true) + '</b></div><div><div class="sub">Capital added</div><b>' + m(c.cap) + '</b></div><div><div class="sub">P(net profit)</div><b>' + c.pp.toFixed(3) + '</b></div><div><div class="sub">Fees</div><b>' + m(c.fees) + '</b></div></div>', 'card')
       + R(0.16, '<div class="lbl">Why this one</div><p style="margin:6px 0">Worst case must be ≥ −$6.00. Eligible: EXIT (' + m(ex.E, true) + ') and ' + esc(c.name) + ' (' + m(c.E, true) + '). The hedge keeps ' + m(c.E - ex.E, true) + ' more expected value; it gives up ' + m(-c.inc) + ' against HOLD to cut the worst case from ' + m(HOLD.worst, true) + ' to ' + m(c.worst, true) + '.</p>', 'card') + '</div>';
     h += '<div class="g2" style="margin-top:14px"><div class="tw">' + R(0.3, '<table><thead><tr><th>State</th><th class="n">P</th><th class="n">HOLD</th><th class="n">' + esc(c.name) + '</th><th class="n">EXIT</th></tr></thead><tbody>' + states.map(function (s) {
       return '<tr><td>' + s[1] + '</td><td class="n">' + (s[0] === 'Y2' ? (1 - PS.RS - PS.Y1) : PS[s[0]]).toFixed(3) + '</td><td class="n">' + m(HOLD.net[s[0]], true) + '</td><td class="n">' + m(c.net[s[0]], true) + '</td><td class="n">' + m(ex.net[s[0]], true) + '</td></tr>';
     }).join('') + '</tbody></table>') + '</div>';
     h += R(0.46, '<div class="lbl">Residual exposure</div><p style="margin:6px 0">30 Yankees held, 25 paired with Red Sox +1.5. <b>5 unpaired</b>: if the Red Sox win those 5 lose their $2.50 of stake with nothing against them. That is inside the −$5.95 worst case, recorded on the group as <span class="mono">unpaired_residual_qty = 5</span>, and reviewed again on the next pass.</p>', 'card') + '</div>';
     return h + R(0.7, '<div class="callout" style="margin-top:14px">The hedge order goes through the same single execution path. In rehearsal the 25 fill at $0.60; the fill, not the decision, updates the book.</div>');
   },
   inspect: '<h3>The per-state table</h3><p>Hedge cost = 25 × $0.60 + fee $0.42 = $15.42. Red Sox win: 25 − 15.53 − 15.42 = −$5.95. Yankees by 1 (both win): 55 − 30.95 = +$24.05. Yankees by 2+: 30 − 30.95 = −$0.95.</p><h3>Unpaired remainder</h3><p>Executable quantity at the selected price was 25 of 30. The remainder is kept visible as residual exposure; the decision never pretends the pair is complete.</p><h3>What this does not mean</h3><p>A selected action is a recorded decision. It is not a promise of profit.</p>'},

  {a: 'XAVIER', t: 'Settlement updates the actual accounting', dur: 10, music: 3, cam: [3, 2, -10],
   st: {DEREK: 'IDLE', XAVIER: 'WAITING_FOR_EVIDENCE', AUDREY: 'IDLE'},
   scene: function () {
     var c = CHOSEN;
     var h = lead('09 · Settlement', 'The game settles. The book records what was actually paid, and only that.');
     h += R(0.06, '<div class="score"><div><small>NYY</small>' + C(6, 0, '', '', 0.08, 0.22) + '</div><div style="opacity:.5">–</div><div><small>BOS</small>' + C(2, 0, '', '', 0.08, 0.22) + '</div><div style="font-size:14px;letter-spacing:.14em">FINAL</div></div>');
     h += '<div class="g2" style="margin-top:14px">' + R(0.28, '<pre class="rec"><b>bettor_funded_economics</b> · PG-7Q2\nYankees YES × 30 settles $1.00   +$30.00\nRed Sox +1.5 × 25 settles $0.00   +$0.00\nentry cost + fee                  −$15.53\nhedge cost + fee                  −$15.42\n<b>realised net                       −$0.95</b></pre>')
       + R(0.46, '<div class="lbl">Not realised</div><p style="margin:6px 0">HOLD would have paid ' + m(HOLD.net[FINAL], true) + ' on this outcome. That figure is hypothetical: no such order history exists, and it is never added to the book.</p><div class="callout">Yankees won by 4, outside the +1.5 overlap. The hedge cost money on this game, exactly as its per-state table said it could.</div>', 'card') + '</div>';
     return h;
   },
   inspect: '<h3>One book</h3><p>Settlement is ingested into <code>bettor_funded_economics</code>, the one authoritative accounting system. Xavier and Audrey read it; neither keeps a second ledger.</p><h3>Hypothetical is not realised</h3><p>What an alternative would have paid is computed for audit, labelled hypothetical, and kept out of realised P&L.</p>'},

  {a: 'AUDREY', t: 'Audrey compares the decision with its captured alternatives', dur: 13, music: 3, cam: [7, 3, -30], flow: ['XAVIER', 'AUDREY', 'decision + 7 alternatives'],
   st: {DEREK: 'IDLE', XAVIER: 'IDLE', AUDREY: 'EVALUATING'},
   scene: function () {
     var h = lead('10 · Audit', 'Audrey separates what happened from what might have. Four kinds of evidence, never blended.');
     var obs = ALTS.filter(function (a) { return a.kind === 'HOLD' || a.kind === 'EXIT' || a.kind === 'REDUCE'; });
     h += '<div class="g4">'
       + R(0.1, '<h5>Actual · executed & booked</h5><div class="big">' + m(-0.95, true) + '</div><p>' + esc(CHOSEN.name) + ' on 30 Yankees, from the book.</p>', 'cat c-ACTUAL')
       + R(0.22, '<h5>Observed alternative</h5><p>' + obs.map(function (a) { return esc(a.name) + ': ' + m(a.net[FINAL], true); }).join('<br>') + '</p><p>Quotes captured at decision time. Never placed: outcomes hypothetical.</p>', 'cat c-OBSERVED')
       + R(0.34, '<h5>Modelled estimate</h5><p>' + RUNGS.slice(1).map(function (r) { return '+' + r.k + ': ' + m(r.alt.net[FINAL], true); }).join('<br>') + '</p><p>Depth beyond the top level not captured; fills modelled.</p>', 'cat c-MODELLED')
       + R(0.46, '<h5>Unresolved</h5><p>70 unfilled entry contracts: no fill, no outcome attributed.</p><p>One fixture: not a sample of the policy.</p>', 'cat c-UNRESOLVED') + '</div>';
     return h + R(0.62, '<div class="g2" style="margin-top:14px"><pre class="rec"><b>finding</b> · XAVIER · LOW\nThe selection followed the active policy.\nExpected cost of the worst-case bound: ' + CHOSEN.inc.toFixed(2) + ' vs HOLD.\n+4.5 would have paid ' + RUNGS[3].alt.net[FINAL].toFixed(2) + ' here (modelled);\nit was refused on worst case, correctly.\nHindsight on one game is not evidence.</pre><pre class="rec"><b>finding</b> · DEREK · INFO\nEntry filled 30 of 100 at the limit.\nFill rate belongs in sizing evidence;\nrepeated decisions on one fixture\nare not independent examples.</pre></div>');
   },
   inspect: '<h3>The four categories</h3><p><b>Actual</b>: realised in the book. <b>Observed alternative</b>: priced from captured executable quotes at decision time, never placed. <b>Modelled estimate</b>: fill or price modelled rather than captured. <b>Unresolved</b>: outcome or evidence not established. Only the first is P&L.</p><h3>Independence</h3><p>Audrey counts cohorts by fixture: several reviews of one position are one example, not several.</p><h3>What this does not mean</h3><p>Auditing a decision does not prove the policy profitable or unprofitable.</p>'},

  {a: 'AUDREY', t: 'Management gives Audrey a directive', dur: 11, music: 4, cam: [9, 3, -40], flow: ['MANAGEMENT', 'AUDREY', 'directive'],
   st: {DEREK: 'IDLE', XAVIER: 'IDLE', AUDREY: 'DECISION_RECORDED'},
   scene: function () {
     var h = lead('11 · Direction', 'Management states an objective in plain words. Audrey turns it into a bounded, confirmable directive.');
     h += '<div class="g2"><div><span class="scripted">SCRIPTED EXCHANGE · NOT THE AUDREY SERVICE</span><div class="chat">'
       + R(0.08, '<div class="who">MANAGEMENT</div>Prioritize reducing drawdown without increasing capital limits.', 'bubble me')
       + R(0.24, '<div class="who">AUDREY · SCRIPTED</div>Understood. I’ll record this as a directive: reduce Xavier’s drawdown; capital and risk limits unchanged (no agent can edit them). It becomes active when you confirm it with the operator credential.', 'bubble')
       + R(0.4, '<div class="who">MANAGEMENT</div>Confirmed.', 'bubble me') + '</div></div>';
     h += R(0.5, '<pre class="rec"><b>directive</b> D-0417\ntext        Prioritize reducing drawdown without\n            increasing capital limits.\nconstraints capital limits: UNCHANGED\n            risk limits: not agent-editable\nstate       PROPOSED → <b>CONFIRMED</b>\nconfirmed   operator credential · 23:41:07Z</pre>') + '</div>';
     return h + R(0.7, '<div class="callout" style="margin-top:14px">The live chat is on Audrey’s workspace. Every real answer states its provider mode and disclosure (for example “answered from records; AI provider not configured”) and cites the records it used.</div>');
   },
   inspect: '<h3>Scripted here, real elsewhere</h3><p>This exchange is scripted for the rehearsal. The functioning service is <code>POST /api/command/agents/audrey/chat</code>, on the Audrey workspace; this page never calls it in rehearsal.</p><h3>Directives</h3><p>A directive is proposed, then confirmed or cancelled with the OPERATOR credential. Directives cannot change risk limits, credentials, account authority or approval controls.</p>'},

  {a: 'AUDREY', t: 'Audrey creates a development task', dur: 10, music: 4, cam: [4, 3, -10], flow: ['AUDREY', 'XAVIER', 'task T-0418'],
   st: {DEREK: 'IDLE', XAVIER: 'EVALUATING', AUDREY: 'DECISION_RECORDED'},
   scene: function () {
     var h = lead('12 · Task', 'The directive becomes a task with a testable objective, assigned to the agent whose policy it concerns.');
     h += '<div class="g2">' + R(0.08, '<pre class="rec"><b>agent_tasks</b>\ntask_id      T-0418\nassignee     <b>XAVIER</b>\ncreated_by   AUDREY\ndirective_id D-0417\nkind         POLICY_CANDIDATE\ntitle        Reduce management drawdown\nspec         objective: max drawdown ↓\n             hold: capital limits unchanged\n             evaluate: replay on recorded\n             reviews, grouped by fixture\nstatus       OPEN → IN_PROGRESS</pre>')
       + '<div class="card"><div class="lbl">agent_task_events · append-only</div><ul class="tl" style="margin-top:10px">'
       + R(0.24, '<b>CREATED</b> by AUDREY from D-0417', '', 'li') + R(0.36, '<b>ASSIGNED</b> to XAVIER', '', 'li') + R(0.48, '<b>STARTED</b> · status IN_PROGRESS', '', 'li')
       + R(0.6, '<span class="sub">Events cannot be updated or deleted; the trigger refuses it.</span>', '', 'li') + '</ul></div></div>';
     return h;
   },
   inspect: '<h3>What the real system does</h3><p><code>agent_tasks</code> holds the task and its status; <code>agent_task_events</code> is append-only (a trigger refuses UPDATE and DELETE), so the history of every change is permanent.</p>'},

  {a: 'XAVIER', t: 'A candidate change: branch, tests and replay', dur: 12, music: 4, cam: [1, 4, 0], flow: ['XAVIER', 'AUDREY', 'candidate + replay'],
   st: {DEREK: 'IDLE', XAVIER: 'EVALUATING', AUDREY: 'IDLE'},
   scene: function () {
     var h = lead('13 · Candidate', 'Xavier proposes one parameter change on a branch, with tests and a replay on recorded reviews. Nothing live changes.');
     h += '<div class="g2"><div>' + R(0.06, '<div class="lbl" style="margin-bottom:6px">branch candidate/xavier-drawdown-guard</div><div class="diff"><div class="c">@@ xavier_policy · management @@</div><div class="m">-  worst_case_floor_usd: -6.00</div><div class="p">+  worst_case_floor_usd: -4.50</div><div class="c">   risk_limits: (untouched)</div><div class="c">   capital_limits: (untouched)</div></div>')
       + R(0.24, '<div class="card" style="margin-top:12px"><div class="lbl">Tests</div><div class="big pos">214 passed · 0 failed</div><div class="sub">including: risk limits are not agent-editable</div></div>') + '</div>';
     h += R(0.36, '<div class="tw"><table><thead><tr><th>Replay · 412 reviews · 57 fixtures</th><th class="n">Active</th><th class="n">Candidate</th></tr></thead><tbody>'
       + '<tr><td>Max drawdown</td><td class="n">' + m(-182.4, true) + '</td><td class="n">' + m(-131.1, true) + '</td></tr>'
       + '<tr><td>Expected net P&amp;L</td><td class="n">' + m(114.2, true) + '</td><td class="n">' + m(103.0, true) + '</td></tr>'
       + '<tr><td>P(net profit) per group</td><td class="n">0.41</td><td class="n">0.47</td></tr>'
       + '<tr><td>Capital limit</td><td class="n">unchanged</td><td class="n">unchanged</td></tr>'
       + '<tr><td>This position would select</td><td class="n">' + esc(CHOSEN.name) + '</td><td class="n">' + esc(CAND_CHOSEN.name) + '</td></tr>'
       + '<tr class="ref"><td>Independent examples</td><td class="n" colspan="2">57 fixtures (not 412)</td></tr></tbody></table></div>') + '</div>';
     return h + R(0.66, '<div class="callout" style="margin-top:14px">A replay re-decides recorded reviews under the candidate policy. It sends no order, and a better replay is not proven profitability.</div>');
   },
   inspect: '<h3>What the real system does</h3><p>Improvement tasks that can run in-process (policy and model replay) are advanced by <code>improvement.run_due</code>. A candidate policy version is written as CANDIDATE in <code>agent_policy_versions</code>; it cannot become ACTIVE without evaluation and a separate approval.</p><h3>Replay figures</h3><p>Rehearsal replay figures are illustrative. Real ones come from recorded reviews and are grouped by fixture.</p>'},

  {a: 'AUDREY', t: 'Evaluation, authorised release, canary and rollback', dur: 15, music: 5, cam: [6, 3, -20], flow: ['AUDREY', 'MANAGEMENT', 'approval request'],
   st: {DEREK: 'IDLE', XAVIER: 'IDLE', AUDREY: 'DECISION_RECORDED'},
   scene: function () {
     var gates = [['Max drawdown reduced ≥ 15%', '−28.1%', true], ['Expected net loss ≤ 12%', '−9.8%', true], ['Capital and risk limits unchanged', 'diff touches neither', true], ['Independent fixtures ≥ 50', '57', true], ['Tests pass', '214 / 214', true]];
     var h = lead('14 · Release', 'The proposer cannot evaluate its own change, and neither agent can approve it. A person releases it to a canary with rollback armed.');
     h += '<div class="roles">' + R(0.04, '<div class="lbl">Proposer</div><div class="big">XAVIER</div>', 'card') + R(0.1, '<div class="lbl">Evaluator</div><div class="big">AUDREY</div><div class="sub">evaluation harness</div>', 'card') + R(0.16, '<div class="lbl">Approver</div><div class="big">OPERATOR</div><div class="sub">a person, operator credential</div>', 'card') + '</div>';
     h += '<div class="g2" style="margin-top:14px"><div class="tw"><table><thead><tr><th>Gate</th><th class="n">Result</th><th></th></tr></thead><tbody>' + gates.map(function (g, i) { return '<tr class="rv" data-t="' + (0.22 + i * 0.05).toFixed(2) + '"><td>' + g[0] + '</td><td class="n mono">' + g[1] + '</td><td>' + pill('PASS', 'p-ok') + '</td></tr>'; }).join('') + '</tbody></table></div>';
     h += R(0.5, '<div class="lbl">If any gate had failed</div><p style="margin:6px 0">The task ends <b>REJECTED</b> with the failing gate named, the candidate stays CANDIDATE → REJECTED, and the active policy is untouched.</p>', 'card') + '</div>';
     var stages = [['CANDIDATE_READY', 0.56], ['EVALUATING', 0.6], ['APPROVAL_READY', 0.64], ['APPROVED', 0.7], ['CANARY 10% · 14 d', 0.76], ['ROLLBACK ARMED', 0.82]];
     h += '<div class="pipe">' + stages.map(function (s) { return '<div class="rv" data-t="' + s[1] + '" data-on="1"><b>' + s[0] + '</b></div>'; }).join('') + '</div>';
     return h + R(0.88, '<div class="callout">Canary: the candidate manages 10% of new position groups for 14 days; rollback is automatic if realised drawdown exceeds the replay’s p90 or any reconciliation breaks. None of this sends an order by itself, and none of it proves profitability.</div>');
   },
   inspect: '<h3>Separation</h3><p>Proposer ≠ evaluator ≠ approver. The evaluator runs the gates on replay and tests; approval needs the OPERATOR credential, which no agent holds. Risk limits, credentials, account authority and approval controls are not editable by any agent.</p><h3>Canary and rollback</h3><p>Release to a canary slice with a named rollback trigger; <code>agent_tasks</code> moves APPROVED → RELEASED or ROLLED_BACK, each with an event.</p><h3>What this does not mean</h3><p>An approved candidate is a policy change, not evidence of profit. Profit is only ever the book’s realised P&L.</p>'}
  ];

  var starts = [], total = 0;
  CH.forEach(function (c) { starts.push(total); total += c.dur; });

  // ── state ─────────────────────────────────────────────────────────
  var S = {t: 0, playing: false, idx: -1, mode: 'rehearsal', sound: false, prod: null, prodLoading: false};
  var AGENTS = ['DEREK', 'XAVIER', 'AUDREY', 'MANAGEMENT'];
  var nodes = {};
  [].forEach.call(document.querySelectorAll('.node'), function (n) { nodes[n.getAttribute('data-a')] = n; });
  var panel = $('panel'), token = $('token'), triad = $('triad');

  function fmtTime(s) { s = Math.max(0, Math.round(s)); return Math.floor(s / 60) + ':' + ('0' + s % 60).slice(-2); }
  function locate(t) { for (var i = 0; i < CH.length; i++) if (t < starts[i] + CH[i].dur) return {i: i, p: (t - starts[i]) / CH[i].dur}; return {i: CH.length - 1, p: 1}; }

  function frameLabel(i) {
    if (S.mode === 'production') {
      var rd = S.prod && S.prod.readLine ? S.prod.readLine : 'records not yet read';
      return '<div class="framelab prod"><span>PRODUCTION · RECORDED STATE · READ-ONLY · ' + esc(rd) + '</span><span>frame ' + (i + 1) + ' / 14</span></div>';
    }
    return '<div class="framelab"><span>' + LABEL + '</span><span>frame ' + (i + 1) + ' / 14</span></div>';
  }
  function sceneHtml(i) {
    if (S.mode === 'production' && window.DemoProd) return window.DemoProd.frame(i, S.prod, CH[i]) + frameLabel(i);
    return CH[i].scene() + frameLabel(i);
  }
  var swapTimer = null;
  function enter(i) {
    var c = CH[i], prev = S.idx; S.idx = i;
    document.body.className = 'a-' + c.a + (S.mode === 'production' ? ' m-prod' : '');
    $('cnum').textContent = (i < 9 ? '0' : '') + (i + 1) + ' / 14';
    $('ctitle').textContent = c.t;
    var st = S.mode === 'production' && window.DemoProd ? window.DemoProd.states(S.prod) : c.st;
    AGENTS.forEach(function (a) {
      var n = nodes[a]; if (!n) return;
      var active = a === c.a || (c.flow && (c.flow[0] === a || c.flow[1] === a));
      n.classList.toggle('active', a === c.a); n.classList.toggle('dim', !active);
      if (a !== 'MANAGEMENT') { var s = st[a] || 'NO STATUS RECORDED'; var el = n.querySelector('.st'); el.textContent = s.replace(/_/g, ' '); el.className = 'st s-' + s; }
    });
    if (!REDUCE) $('cam').style.transform = 'rotateY(' + c.cam[0] + 'deg) rotateX(' + c.cam[1] + 'deg) translate3d(' + (-c.cam[2]) + 'px,0,0)';
    drawBeams();
    var html = sceneHtml(i);
    if (swapTimer) clearTimeout(swapTimer);
    if (prev === -1 || REDUCE) { panel.innerHTML = html; panel.scrollTop = 0; update(locate(S.t).p); }
    else {
      panel.classList.add('swap');
      swapTimer = setTimeout(function () { panel.innerHTML = html; panel.scrollTop = 0; update(locate(S.t).p); panel.classList.remove('swap'); }, 260);
    }
    $('inspectBody').innerHTML = '<p class="kicker" style="color:var(--acc)">Inspect · ' + (i + 1) + ' / 14</p><h2>' + esc(c.t) + '</h2>' + c.inspect
      + '<h3>Mode</h3><p>' + (S.mode === 'production' ? 'PRODUCTION: this frame shows the records the service holds now, read from same-origin endpoints. Empty records are shown as empty.' : esc(LABEL) + '. Every price, fill and timestamp in this frame is illustrative.') + '</p>';
    [].forEach.call(document.querySelectorAll('#chlist button'), function (b, j) { b.classList.toggle('cur', j === i); });
    BG.setScene(c.a, c.cam[0], c.music);
    Music.setLevel(c.music);
  }
  function update(p) {
    [].forEach.call(panel.querySelectorAll('[data-t]'), function (el) { el.classList.toggle('on', p >= +el.getAttribute('data-t')); });
    [].forEach.call(panel.querySelectorAll('.cu'), function (el) {
      var k = ease(clamp((p - +el.dataset.t0) / Math.max(0.001, +el.dataset.t1 - +el.dataset.t0), 0, 1));
      el.textContent = el.dataset.pre + (+el.dataset.to * k).toFixed(+el.dataset.dec) + el.dataset.suf;
    });
    [].forEach.call(panel.querySelectorAll('.gw,.cuw'), function (el) {
      var k = ease(clamp((p - +el.dataset.t0) / Math.max(0.001, +el.dataset.t1 - +el.dataset.t0), 0, 1));
      el.style.width = (+el.dataset.w * k).toFixed(2) + '%';
    });
    if (S.playing) follow();
    var c = CH[S.idx];
    if (c && c.flow && S.mode !== 'production') {
      var a = center(c.flow[0]), b = center(c.flow[1]), k = clamp((p - 0.06) / 0.34, 0, 1), e = ease(k);
      token.textContent = c.flow[2];
      token.style.left = (a.x + (b.x - a.x) * e) + 'px';
      token.style.top = (a.y + (b.y - a.y) * e - Math.sin(e * Math.PI) * 26) + 'px';
      token.style.opacity = (k > 0 && p < 0.55) ? 1 : 0;
    } else token.style.opacity = 0;
  }
  function follow() {
    var on = panel.querySelectorAll('.rv.on'); if (!on.length) return;
    var el = on[on.length - 1], pr = panel.getBoundingClientRect(), er = el.getBoundingClientRect();
    var over = er.bottom - (pr.bottom - 60);
    if (over > 4) panel.scrollTop += Math.min(over, 14);
  }
  function center(a) {
    var n = nodes[a].querySelector('.core'), r = n.getBoundingClientRect(), t = triad.getBoundingClientRect();
    return {x: r.left - t.left + r.width / 2, y: r.top - t.top + r.height / 2};
  }
  function drawBeams() {
    var c = CH[S.idx] || {}, pairs = [['DEREK', 'XAVIER'], ['XAVIER', 'AUDREY'], ['AUDREY', 'MANAGEMENT'], ['DEREK', 'AUDREY']];
    var live = c.flow ? [c.flow[0], c.flow[1]].sort().join() : '';
    var h = '';
    pairs.forEach(function (pp) {
      var a = center(pp[0]), b = center(pp[1]);
      var arc = pp[0] === 'DEREK' && pp[1] === 'AUDREY';
      var d = arc ? 'M' + a.x + ' ' + (a.y - 30) + ' Q' + ((a.x + b.x) / 2) + ' ' + (a.y - 52) + ' ' + b.x + ' ' + (b.y - 30) : 'M' + (a.x + 36) + ' ' + a.y + ' L' + (b.x - 36) + ' ' + b.y;
      h += '<path d="' + d + '"' + (pp.slice().sort().join() === live ? ' class="live"' : '') + '/>';
    });
    $('beams').innerHTML = h;
  }

  // ── transport ─────────────────────────────────────────────────────
  var last = null;
  function tick(now) {
    if (last === null) last = now;
    var dt = Math.min(0.1, (now - last) / 1000); last = now;
    if (S.playing) { S.t += dt; if (S.t >= total) { S.t = total - 0.001; setPlaying(false); } }
    apply();
    requestAnimationFrame(tick);
  }
  function apply() {
    var L = locate(S.t);
    if (L.i !== S.idx) enter(L.i);
    if (!panel.classList.contains('swap')) update(L.p);
    $('scrub').value = Math.round(S.t / total * 1000);
    $('time').textContent = fmtTime(S.t) + ' / ' + fmtTime(total);
    [].forEach.call(document.querySelectorAll('#segs button'), function (b, j) {
      b.classList.toggle('done', j < L.i);
      b.querySelector('i').style.width = j === L.i ? (L.p * 100).toFixed(1) + '%' : '';
    });
  }
  function setPlaying(v) {
    S.playing = v;
    $('play').innerHTML = v ? '&#10074;&#10074;' : '&#9654;';
    $('play').setAttribute('aria-label', v ? 'Pause' : 'Play');
    if (S.sound) { if (v) Music.resume(); else Music.pause(); }
  }
  function seek(t) { S.t = clamp(t, 0, total - 0.001); apply(); }
  function go(i) { seek(starts[clamp(i, 0, CH.length - 1)] + 0.001); }
  $('play').addEventListener('click', function () { if (S.t >= total - 0.01) seek(0); setPlaying(!S.playing); });
  $('prev').addEventListener('click', function () { var L = locate(S.t); go(L.p > 0.15 ? L.i : L.i - 1); });
  $('next').addEventListener('click', function () { go(locate(S.t).i + 1); });
  var wasPlaying = false;
  $('scrub').addEventListener('pointerdown', function () { wasPlaying = S.playing; setPlaying(false); });
  $('scrub').addEventListener('input', function (e) { seek(+e.target.value / 1000 * total); });
  $('scrub').addEventListener('change', function () { if (wasPlaying) setPlaying(true); });
  $('segs').innerHTML = CH.map(function (c, i) { return '<button type="button" title="' + (i + 1) + '. ' + esc(c.t) + '" data-go="' + i + '"><i></i></button>'; }).join('');
  $('chlist').innerHTML = CH.map(function (c, i) { return '<li><button type="button" data-go="' + i + '"><span class="n">' + (i + 1) + '</span><span>' + esc(c.t) + '</span></button></li>'; }).join('');
  document.addEventListener('click', function (e) {
    var g = e.target.closest && e.target.closest('[data-go]');
    if (g) { go(+g.getAttribute('data-go')); if (g.closest('#chdrawer')) toggleDrawer('chdrawer', false); }
    var cl = e.target.closest && e.target.closest('[data-close]');
    if (cl) toggleDrawer(cl.getAttribute('data-close'), false);
  });
  function toggleDrawer(id, v) {
    var d = $(id); var open = v === undefined ? !d.classList.contains('open') : v;
    d.classList.toggle('open', open);
    if (id === 'inspect') $('inspectBtn').setAttribute('aria-pressed', open ? 'true' : 'false');
  }
  $('inspectBtn').addEventListener('click', function () { toggleDrawer('inspect'); });
  $('chapters').addEventListener('click', function () { toggleDrawer('chdrawer'); });
  function setSound(v) {
    S.sound = v;
    $('soundBtn').setAttribute('aria-pressed', v ? 'true' : 'false');
    $('soundBtn').innerHTML = v ? '&#9834; Sound on' : '&#9834; Sound off';
    if (v) { if (!Music.enable()) { S.sound = false; $('soundBtn').textContent = 'Sound unavailable'; return; } Music.setLevel(CH[Math.max(0, S.idx)].music); if (!S.playing) Music.pause(); }
    else Music.disable();
  }
  $('soundBtn').addEventListener('click', function () { setSound(!S.sound); });
  $('begin').addEventListener('click', function () { $('intro').classList.add('gone'); seek(0); setPlaying(true); });
  document.addEventListener('keydown', function (e) {
    if (e.target && /INPUT|TEXTAREA|SELECT/.test(e.target.tagName) && e.target.id !== 'scrub') return;
    var k = e.key;
    if (k === ' ' || k === 'k' || k === 'K') { e.preventDefault(); $('intro').classList.add('gone'); if (S.t >= total - 0.01) seek(0); setPlaying(!S.playing); }
    else if (k === 'ArrowRight') { e.preventDefault(); go(locate(S.t).i + 1); }
    else if (k === 'ArrowLeft') { e.preventDefault(); var L = locate(S.t); go(L.p > 0.15 ? L.i : L.i - 1); }
    else if (k === '.') seek(S.t + 2); else if (k === ',') seek(S.t - 2);
    else if (k === 'Home') go(0); else if (k === 'End') go(CH.length - 1);
    else if (k === 'i' || k === 'I') toggleDrawer('inspect');
    else if (k === 'm' || k === 'M') setSound(!S.sound);
    else if (k === 'c' || k === 'C') toggleDrawer('chdrawer');
    else if ((k === 'p' || k === 'P') && SERVED && window.DemoProd) setMode(S.mode === 'production' ? 'rehearsal' : 'production');
    else if (k === 'Escape') { toggleDrawer('inspect', false); toggleDrawer('chdrawer', false); }
    else if (/^[0-9]$/.test(k)) go(k === '0' ? 9 : +k - 1);
  });
  window.addEventListener('pointermove', function (e) {
    if (REDUCE) return;
    var px = e.clientX / window.innerWidth - 0.5, py = e.clientY / window.innerHeight - 0.5;
    $('par').style.transform = 'rotateY(' + (px * 4).toFixed(2) + 'deg) rotateX(' + (-py * 3).toFixed(2) + 'deg)';
    BG.setPointer(px, py);
  });
  window.addEventListener('resize', function () { BG.resize(); drawBeams(); });

  // ── modes ─────────────────────────────────────────────────────────
  function setMode(mode) {
    if (!(SERVED && window.DemoProd)) mode = 'rehearsal';
    S.mode = mode;
    var b = $('banner');
    b.className = 'banner' + (mode === 'production' ? ' prod' : '');
    b.textContent = mode === 'production' ? 'PRODUCTION · RECORDED STATE FROM THIS SERVICE · READ-ONLY · THIS PAGE SENDS NO ORDER' : LABEL;
    [].forEach.call(document.querySelectorAll('[data-mode]'), function (x) { x.setAttribute('aria-pressed', x.getAttribute('data-mode') === mode ? 'true' : 'false'); });
    if (mode === 'production' && !S.prod && !S.prodLoading) {
      S.prodLoading = true;
      window.DemoProd.load().then(function (d) { S.prod = d; S.prodLoading = false; var i = S.idx; S.idx = -1; if (i >= 0) { S.idx = -1; enter(i); } });
    }
    var i = S.idx; if (i >= 0) { S.idx = -1; enter(i); }
  }
  [].forEach.call(document.querySelectorAll('[data-mode]'), function (x) { x.addEventListener('click', function () { setMode(x.getAttribute('data-mode')); }); });

  // ── depth field: 2D canvas, perspective projection ────────────────
  var BG = (function () {
    var cv = $('bg'), g = cv.getContext('2d'), W = 0, H = 0, parts = [], off = 0;
    var COL = {DEREK: [242, 165, 65], XAVIER: [63, 208, 224], AUDREY: [169, 140, 255], MANAGEMENT: [134, 162, 255]};
    var col = COL.DEREK.slice(), tcol = col.slice(), camX = 0, tcamX = 0, energy = 0.2, tenergy = 0.2, px = 0, py = 0, flash = 0, lastT = null;
    var dark = window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches;
    function resize() {
      var dpr = Math.min(2, window.devicePixelRatio || 1);
      W = cv.clientWidth; H = cv.clientHeight; cv.width = W * dpr; cv.height = H * dpr; g.setTransform(dpr, 0, 0, dpr, 0, 0);
      var n = W < 700 ? 90 : 190; parts = [];
      for (var i = 0; i < n; i++) parts.push({x: Math.random() * 3.2 - 1.6, y: Math.random() * 2 - 1, z: Math.random() * 0.95 + 0.05, s: Math.random() * 1.1 + 0.5});
      if (REDUCE) draw(0);
    }
    function rgba(a) { return 'rgba(' + col[0] + ',' + col[1] + ',' + col[2] + ',' + a.toFixed(3) + ')'; }
    function draw(dt) {
      for (var j = 0; j < 3; j++) col[j] += (tcol[j] - col[j]) * Math.min(1, dt * 1.6);
      camX += (tcamX - camX) * Math.min(1, dt * 1.2); energy += (tenergy - energy) * Math.min(1, dt); flash *= Math.pow(0.2, dt);
      g.clearRect(0, 0, W, H);
      var vx = W / 2 - camX * W * 0.012 + px * 24, y0 = H * 0.6 + py * 10;
      var glow = g.createRadialGradient(vx, H * 0.34, 0, vx, H * 0.34, Math.max(W, H) * 0.6);
      glow.addColorStop(0, rgba((dark ? 0.16 : 0.12) + flash * 0.22)); glow.addColorStop(1, rgba(0));
      g.fillStyle = glow; g.fillRect(0, 0, W, H);
      g.lineWidth = 1;
      for (var k = -16; k <= 16; k++) { g.strokeStyle = rgba(dark ? 0.07 : 0.1); g.beginPath(); g.moveTo(vx + k * 8, y0); g.lineTo(vx + k * W / 8, H); g.stroke(); }
      off = (off + dt * (0.12 + energy * 0.5)) % 1;
      for (var r = 0; r < 20; r++) {
        var z = (r + off) / 20, y = y0 + (H - y0) * Math.pow(z, 2.3);
        g.strokeStyle = rgba((dark ? 0.1 : 0.12) * z); g.beginPath(); g.moveTo(0, y); g.lineTo(W, y); g.stroke();
      }
      var sp = dt * 0.06 * (0.3 + energy * 1.4);
      for (var i = 0; i < parts.length; i++) {
        var p = parts[i];
        p.z -= sp; if (p.z < 0.04) { p.z = 1; p.x = Math.random() * 3.2 - 1.6; p.y = Math.random() * 2 - 1; }
        var f = 1 / (p.z * 1.25 + 0.14);
        var sx = W / 2 + (p.x - camX * 0.02) * W * 0.24 * f + px * 40 * (1 - p.z), sy = H * 0.42 + p.y * H * 0.24 * f + py * 26 * (1 - p.z);
        if (sx < -10 || sx > W + 10 || sy < -10 || sy > H + 10) continue;
        g.fillStyle = rgba((1 - p.z) * (dark ? 0.75 : 0.55));
        g.beginPath(); g.arc(sx, sy, Math.max(0.4, p.s * f * 0.45), 0, 6.283); g.fill();
      }
      // streaks along the floor: data moving toward the viewer
      for (var s = 0; s < 7; s++) {
        var ph = ((performance.now() / 1000) * (0.18 + energy * 0.25) + s * 0.37) % 1, kk = (s * 5 % 13) - 6;
        var ax = vx + kk * 8, bx = vx + kk * W / 8, t1 = Math.pow(ph, 2), t2 = Math.pow(Math.min(1, ph + 0.08), 2);
        g.strokeStyle = rgba(0.45 * (1 - ph)); g.lineWidth = 2; g.beginPath();
        g.moveTo(ax + (bx - ax) * t1, y0 + (H - y0) * t1); g.lineTo(ax + (bx - ax) * t2, y0 + (H - y0) * t2); g.stroke(); g.lineWidth = 1;
      }
    }
    function loop(now) { if (lastT === null) lastT = now; var dt = Math.min(0.1, (now - lastT) / 1000); lastT = now; draw(dt); requestAnimationFrame(loop); }
    return {
      start: function () { resize(); if (!REDUCE) requestAnimationFrame(loop); },
      resize: resize,
      setScene: function (a, cx, lvl) { tcol = (COL[a] || COL.DEREK).slice(); tcamX = cx; tenergy = 0.15 + lvl * 0.17; flash = 1; if (REDUCE) { col = tcol.slice(); camX = cx; draw(0); } },
      setPointer: function (x, y) { px = x; py = y; }
    };
  })();

  // ── the score: original, generated live with the Web Audio API ─────
  // D major, 96 BPM. Chapter levels: 0 sparse pad, 1 pad + bell, 2 pulse +
  // sub, 3 bass + half-time kick + hats, 4 four-on-the-floor + arp + clap,
  // 5 riser into a resolving Dmaj9 and a long tail.
  var Music = (function () {
    var ctx = null, master, verbIn, delay, bus = {}, level = 0, timer = null, next = 0, step = 0, resolveBar = -1, resolved = false, noise = null, pauseTimer = null;
    var BPM = 96, SIX = 60 / BPM / 4;
    var PROG = [[50, 57, 61, 64, 66], [47, 54, 57, 61, 62], [43, 50, 54, 57, 59], [45, 52, 57, 62, 64]];
    var MIX = {0: {pad: .2}, 1: {pad: .22, bell: .1}, 2: {pad: .2, bell: .09, pulse: .11, sub: .2},
      3: {pad: .19, bell: .08, pulse: .12, sub: .18, bass: .15, kick: .34, hat: .05},
      4: {pad: .19, bell: .09, pulse: .11, sub: .18, bass: .17, kick: .4, hat: .06, clap: .09, arp: .06},
      5: {pad: .26, bell: .14, sub: .2, kick: .3}};
    function hz(n) { return 440 * Math.pow(2, (n - 69) / 12); }
    function init() {
      var AC = window.AudioContext || window.webkitAudioContext; if (!AC) return false;
      ctx = new AC();
      master = ctx.createGain(); master.gain.value = 0;
      var comp = ctx.createDynamicsCompressor(); comp.threshold.value = -18; comp.ratio.value = 3; comp.attack.value = 0.01; comp.release.value = 0.25;
      master.connect(comp); comp.connect(ctx.destination);
      var verb = ctx.createConvolver(), len = Math.floor(ctx.sampleRate * 3.2), ir = ctx.createBuffer(2, len, ctx.sampleRate);
      for (var c = 0; c < 2; c++) { var d = ir.getChannelData(c); for (var i = 0; i < len; i++) d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / len, 2.7); }
      verb.buffer = ir; var vg = ctx.createGain(); vg.gain.value = 0.5; verb.connect(vg); vg.connect(master);
      verbIn = ctx.createGain(); verbIn.connect(verb);
      delay = ctx.createDelay(1.5); delay.delayTime.value = SIX * 3;
      var fb = ctx.createGain(); fb.gain.value = 0.32; var dl = ctx.createBiquadFilter(); dl.type = 'lowpass'; dl.frequency.value = 2400;
      delay.connect(dl); dl.connect(fb); fb.connect(delay); dl.connect(master); dl.connect(verbIn);
      ['pad', 'bell', 'pulse', 'sub', 'bass', 'kick', 'hat', 'clap', 'arp'].forEach(function (k) { var gn = ctx.createGain(); gn.gain.value = 0; gn.connect(master); bus[k] = gn; });
      bus.pad.connect(verbIn); bus.bell.connect(verbIn); bus.bell.connect(delay); bus.pulse.connect(delay); bus.arp.connect(delay); bus.arp.connect(verbIn); bus.clap.connect(verbIn);
      noise = ctx.createBuffer(1, ctx.sampleRate, ctx.sampleRate); var nd = noise.getChannelData(0); for (var j = 0; j < nd.length; j++) nd[j] = Math.random() * 2 - 1;
      return true;
    }
    function mix(l, when) { var mm = MIX[l]; Object.keys(bus).forEach(function (k) { bus[k].gain.setTargetAtTime(mm[k] || 0, when || ctx.currentTime, 0.8); }); }
    function env(gn, t, a, peak, hold, rel) { gn.gain.setValueAtTime(0.0001, t); gn.gain.exponentialRampToValueAtTime(peak, t + a); gn.gain.setValueAtTime(peak, t + a + hold); gn.gain.exponentialRampToValueAtTime(0.0001, t + a + hold + rel); }
    function osc(type, f, t, stop, dest, det) { var o = ctx.createOscillator(); o.type = type; o.frequency.value = f; if (det) o.detune.value = det; o.connect(dest); o.start(t); o.stop(stop); return o; }
    function pad(notes, t, len, bright) {
      var f = ctx.createBiquadFilter(); f.type = 'lowpass'; f.Q.value = 0.6; f.frequency.setValueAtTime(bright * 0.55, t); f.frequency.linearRampToValueAtTime(bright, t + len * 0.6);
      var gn = ctx.createGain(); f.connect(gn); gn.connect(bus.pad); env(gn, t, 1.2, 0.16, Math.max(0.1, len - 1.2), 2.2);
      notes.forEach(function (n) { osc('sawtooth', hz(n), t, t + len + 2.6, f, -7); osc('sawtooth', hz(n), t, t + len + 2.6, f, 7); });
    }
    function pluck(n, t, v, b, type) {
      var f = ctx.createBiquadFilter(); f.type = 'lowpass'; f.frequency.setValueAtTime(4200, t); f.frequency.exponentialRampToValueAtTime(650, t + 0.25);
      var gn = ctx.createGain(); f.connect(gn); gn.connect(bus[b]); env(gn, t, 0.004, v, 0.02, 0.36); osc(type || 'triangle', hz(n), t, t + 0.5, f);
    }
    function bell(n, t, v) { var gn = ctx.createGain(); gn.connect(bus.bell); env(gn, t, 0.005, v, 0.05, 2.6); osc('sine', hz(n), t, t + 3, gn); var g2 = ctx.createGain(); g2.gain.value = 0.25; g2.connect(gn); osc('sine', hz(n) * 2.76, t, t + 1.2, g2); }
    function sub(n, t, len) { var gn = ctx.createGain(); gn.connect(bus.sub); env(gn, t, 0.06, 0.5, Math.max(0.05, len - 0.4), 0.4); osc('sine', hz(n - 12), t, t + len + 0.6, gn); }
    function bass(n, t, v) { var f = ctx.createBiquadFilter(); f.type = 'lowpass'; f.frequency.value = 420; var gn = ctx.createGain(); f.connect(gn); gn.connect(bus.bass); env(gn, t, 0.005, v, 0.08, 0.2); osc('sawtooth', hz(n - 12), t, t + 0.4, f); }
    function kick(t, v) { var o = ctx.createOscillator(), gn = ctx.createGain(); o.frequency.setValueAtTime(140, t); o.frequency.exponentialRampToValueAtTime(42, t + 0.28); o.connect(gn); gn.connect(bus.kick); env(gn, t, 0.002, v, 0.02, 0.32); o.start(t); o.stop(t + 0.4); }
    function nz(t, v, b, type, fq, dur) { var s = ctx.createBufferSource(); s.buffer = noise; var f = ctx.createBiquadFilter(); f.type = type; f.frequency.value = fq; var gn = ctx.createGain(); s.connect(f); f.connect(gn); gn.connect(bus[b]); env(gn, t, 0.001, v, 0.005, dur); s.start(t); s.stop(t + dur + 0.05); }
    function riser(t, len) { var s = ctx.createBufferSource(); s.buffer = noise; s.loop = true; var f = ctx.createBiquadFilter(); f.type = 'bandpass'; f.Q.value = 2; f.frequency.setValueAtTime(300, t); f.frequency.exponentialRampToValueAtTime(6000, t + len); var gn = ctx.createGain(); gn.gain.setValueAtTime(0.0001, t); gn.gain.exponentialRampToValueAtTime(0.12, t + len); gn.gain.exponentialRampToValueAtTime(0.0001, t + len + 0.08); s.connect(f); f.connect(gn); gn.connect(verbIn); gn.connect(master); s.start(t); s.stop(t + len + 0.1); }
    function resolve(t) {
      mix(5, t);
      pad(PROG[0].concat([74]), t, 7, 2800); kick(t, 1); sub(PROG[0][0], t, 4);
      [78, 76, 74, 69].forEach(function (n, i) { bell(n, t + i * SIX * 4, 0.5); });
      resolved = true;
    }
    function play(st, t) {
      var bar = Math.floor(st / 16), s = st % 16, ch = PROG[bar % 4];
      if (resolved) { if (s === 0 && bar > resolveBar && bar <= resolveBar + 6 && (bar - resolveBar) % 2 === 0) pad(PROG[0], t, SIX * 32, 900); return; }
      if (level === 5 && bar === resolveBar && s === 0) { resolve(t); return; }
      if (level === 5 && bar === resolveBar - 1 && s === 0) riser(t, SIX * 16);
      var L = level === 5 ? 4 : level;
      if (s === 0) { if (L > 0 || bar % 2 === 0) pad(ch, t, SIX * 16 * (L === 0 ? 2 : 1) + 0.2, 700 + L * 450); if (L >= 1) bell(ch[ch.length - 1] + 12, t, 0.45); if (L >= 2) sub(ch[0], t, SIX * 16); }
      if (L >= 3 && s === 8) bell(ch[2] + 12, t, 0.3);
      if (L >= 2 && s % 2 === 0) pluck(ch[(s / 2) % ch.length] + 12, t, s % 8 === 0 ? 0.5 : 0.3, 'pulse');
      if (L >= 3 && (s === 0 || s === 6 || s === 12 || (L >= 4 && s === 10))) bass(s === 10 ? ch[0] + 7 : ch[0], t, 0.6);
      if ((L === 3 && (s === 0 || s === 8)) || (L >= 4 && s % 4 === 0)) kick(t, 0.9);
      if (L >= 3 && s % 4 === 2) nz(t, 0.5, 'hat', 'highpass', 7200, 0.05);
      if (L >= 4 && s % 2 === 1) nz(t, 0.16, 'hat', 'highpass', 8000, 0.03);
      if (L >= 4 && (s === 4 || s === 12)) { nz(t, 0.6, 'clap', 'bandpass', 1500, 0.12); nz(t + 0.012, 0.4, 'clap', 'bandpass', 1500, 0.16); }
      if (L >= 4) { var arp = ch.concat(ch.map(function (x) { return x + 12; })); pluck(arp[s % arp.length], t, 0.22, 'arp', 'square'); }
    }
    function schedule() { while (next < ctx.currentTime + 0.14) { play(step, next); next += SIX; step++; } }
    return {
      enable: function () {
        if (!ctx && !init()) return false;
        if (pauseTimer) { clearTimeout(pauseTimer); pauseTimer = null; }
        ctx.resume();
        master.gain.cancelScheduledValues(ctx.currentTime); master.gain.setTargetAtTime(0.8, ctx.currentTime, 0.4);
        if (!timer) { next = ctx.currentTime + 0.1; timer = setInterval(schedule, 25); }
        mix(level === 5 && !resolved ? 4 : level);
        return true;
      },
      disable: function () {
        if (!ctx) return;
        master.gain.setTargetAtTime(0, ctx.currentTime, 0.15);
        if (pauseTimer) clearTimeout(pauseTimer);
        pauseTimer = setTimeout(function () { if (timer) { clearInterval(timer); timer = null; } ctx.suspend(); pauseTimer = null; }, 700);
      },
      pause: function () { this.disable(); },
      resume: function () { this.enable(); },
      setLevel: function (l) {
        if (l === level && !(l === 5 && resolveBar < 0)) return;
        if (l < 5) { resolved = false; resolveBar = -1; }
        level = l;
        if (!ctx) return;
        if (l === 5) { var bar = Math.floor(step / 16); resolveBar = bar + ((step % 16) > 10 ? 2 : 1); resolved = false; mix(4); }
        else mix(l);
      }
    };
  })();

  // ── boot ──────────────────────────────────────────────────────────
  BG.start();
  var qmode = /[?&]mode=production\b/.test(location.search) ? 'production' : 'rehearsal';
  setMode(qmode);
  enter(0);
  requestAnimationFrame(tick);
  if (mq.addEventListener) mq.addEventListener('change', function (e) { REDUCE = e.matches; });
})();
"""

# PRODUCTION mode for the served demo: same-origin reads of the four agent
# endpoints, drawn with the workspace render core. Never included in the
# standalone file.
DEMO_PROD_JS = r"""
(function (AG) {
  'use strict';
  var URLS = {index: AG.ENDPOINTS.index, derek: AG.ENDPOINTS.derek, xavier: AG.ENDPOINTS.xavier, audrey: AG.ENDPOINTS.audrey};
  var SOURCES = [
    [['derek', 'coverage'], ['derek', 'opportunity_queue']],
    [['derek', 'opportunity_queue']],
    [['derek', 'decisions'], ['derek', 'subscription']],
    [['derek', 'decisions'], ['derek', 'plans_fills']],
    [['derek', 'plans_fills']],
    [['derek', 'handoffs'], ['index', 'handoffs']],
    [['xavier', 'positions'], ['xavier', 'ladder'], ['xavier', 'alternatives']],
    [['xavier', 'positions'], ['xavier', 'payout_tables']],
    [['xavier', 'execution'], ['xavier', 'performance']],
    [['audrey', 'outcomes'], ['audrey', 'findings']],
    [['audrey', 'directives'], ['audrey', 'provider'], ['audrey', 'conversations']],
    [['audrey', 'tasks']],
    [['audrey', 'candidates']],
    [['audrey', 'evaluations'], ['audrey', 'releases']]
  ];
  async function load() {
    var keys = Object.keys(URLS), out = {};
    var res = await Promise.all(keys.map(function (k) { return AG.load(URLS[k], window.fetch.bind(window)); }));
    keys.forEach(function (k, i) { out[k] = res[i]; });
    var ix = out.index.kind === 'OK' ? out.index.json : null;
    out.readLine = ix ? AG.readLine(ix) : 'index ' + out.index.kind.toLowerCase().replace('_', ' ');
    return out;
  }
  function states(d) {
    var st = {DEREK: null, XAVIER: null, AUDREY: null};
    var ag = d && d.index && d.index.kind === 'OK' && Array.isArray(d.index.json.agents) ? d.index.json.agents : [];
    ag.forEach(function (a) { var id = String(a && a.agent_id || '').toUpperCase(); if (st.hasOwnProperty(id)) st[id] = AG.STATES.indexOf(a.state) >= 0 ? a.state : null; });
    return st;
  }
  function compact(sec, rd) {
    var d = sec.data;
    var rows = AG.rowsOf(d, ['rows', 'items', 'queue', 'decisions', 'plans', 'handoffs', 'positions', 'alternatives', 'tasks', 'candidates', 'evaluations', 'releases', 'directives', 'findings', 'reports']);
    if (rows.length) {
      return rows.slice(0, 3).map(function (r) {
        if (!AG.isObj(r)) return '<p>' + AG.esc(r) + '</p>';
        var o = {}; Object.keys(r).filter(function (k) { return k !== 'evidence'; }).slice(0, 8).forEach(function (k) { o[k] = r[k]; });
        return AG.kv(o, rd) + (r.evidence ? '<p>' + AG.evidence(r.evidence) + '</p>' : '');
      }).join('<hr style="border:0;border-top:1px dashed var(--line)">') + (rows.length > 3 ? '<p class="sub">' + (rows.length - 3) + ' more in the workspace.</p>' : '');
    }
    if (AG.isObj(d)) { var o2 = {}; Object.keys(d).slice(0, 10).forEach(function (k) { o2[k] = d[k]; }); return AG.kv(o2, rd); }
    return '<p>' + AG.fmt('', d, rd) + '</p>';
  }
  function card(d, src, key) {
    var o = d && d[src], url = URLS[src];
    var head = '<div class="h"><span class="mono">GET ' + AG.esc(url) + ' · ' + AG.esc(key) + '</span>';
    if (!o) return '<div class="prodcard">' + head + '</div><p class="sub">Reading…</p></div>';
    if (o.kind === 'LOCKED') return '<div class="prodcard">' + head + '<span class="pill st-MISSING">LOCKED</span></div><p class="why">' + AG.LOCKED + '. <a href="' + AG.DESK + '">Open the desk</a></p></div>';
    if (o.kind === 'NOT_DEPLOYED') return '<div class="prodcard">' + head + '<span class="pill st-MISSING">NOT DEPLOYED</span></div><p class="why">Not deployed in this build: the endpoint returned 404. Nothing is shown in its place.</p></div>';
    if (o.kind !== 'OK') return '<div class="prodcard">' + head + '<span class="pill st-UNAVAILABLE">UNAVAILABLE</span></div><p class="why">' + AG.esc(o.why || 'read failed') + '</p></div>';
    var j = o.json || {}, rd = AG.toEpoch(j.read_at);
    var sec = src === 'index' ? AG.normList(j[key], key) : AG.normSection((j.sections || {})[key], key, url);
    var body = '';
    if (src === 'xavier' && key === 'positions' && sec.status === 'EMPTY' && AG.R && AG.R.positionsEmpty) body = AG.R.positionsEmpty(sec, {sections: j.sections || {}, url: url});
    else if (sec.status === 'OK') body = compact(sec, rd);
    else if (sec.status === 'EMPTY') body = '<p class="sub">Nothing is recorded here yet. This is not a result.</p>';
    else if (sec.status === 'UNAVAILABLE') body = '<p class="sub">The read failed; nothing is shown in its place.</p>';
    else body = '<p class="sub">The endpoint does not return this section in this build.</p>';
    return '<div class="prodcard">' + head + '<span class="pill st-' + AG.esc(sec.status) + '">' + AG.esc(sec.status) + '</span></div>'
      + (sec.why ? '<p class="why">' + AG.esc(sec.why) + '</p>' : '') + body
      + (sec.evidence && sec.evidence.length ? '<p>' + AG.evidence(sec.evidence) + '</p>' : '')
      + '<p class="sub">read ' + (rd === null ? 'time not reported' : AG.ts(j.read_at)) + '</p></div>';
  }
  function frame(i, d, ch) {
    var h = '<p class="kicker">' + (i < 9 ? '0' : '') + (i + 1) + ' · Production</p><p class="lead">' + AG.esc(ch.t) + ': what the records hold now.</p>';
    if (!d) return h + '<p class="sub">Reading the agent endpoints…</p>';
    h += SOURCES[i].map(function (s) { return card(d, s[0], s[1]); }).join('');
    return h + '<p class="sub" style="margin-top:10px">Reading a record is not sending an order. An observed, audited or proposed decision is not proven profitability.</p>';
  }
  window.DemoProd = {load: load, states: states, frame: frame};
})(AG);
"""

_DEMO_SHELL = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="robots" content="noindex,nofollow">
<meta name="description" content="The three-agent operating system in fourteen chapters: Derek, Xavier, Audrey. %%DESC_MODE%%">
<title>Three Agents | BETTOR</title>
<style>%%CSS%%</style></head>
<body class="a-DEREK">
%%BODY%%
<script>%%JS%%</script></body></html>"""


def demo_html(*, standalone: bool) -> str:
    """The product demo.

    standalone=True is the rehearsal-only file for `research/demo/`: no
    production code, no endpoint path and no network call. standalone=False is
    the served page, with a PRODUCTION mode that reads the four same-origin
    agent endpoints through the workspace render core.
    """
    if standalone:
        mode_switch, mode_key = "", ""
        hint = "Rehearsal-only copy: scripted, illustrative data; no network access."
        home = "#"
        js = DEMO_JS.replace("%%SERVED%%", "false")
        desc = "Rehearsal-only copy."
    else:
        mode_switch = ('<div class="seg" role="group" aria-label="Mode">'
                       '<button type="button" data-mode="rehearsal" aria-pressed="true">REHEARSAL</button>'
                       '<button type="button" data-mode="production" aria-pressed="false">PRODUCTION</button></div>')
        mode_key = ' &middot; <span class="kbd">P</span> mode'
        hint = ("REHEARSAL shows scripted, illustrative data. PRODUCTION reads this "
                "service's agent records, read-only.")
        home = PAGE_PATHS["index"]
        js = (CORE_JS + COMMON_JS + XAVIER_JS + DEMO_PROD_JS
              + DEMO_JS.replace("%%SERVED%%", "true"))
        desc = "Rehearsal and production modes."
    body = (DEMO_BODY.replace("%%MODE_SWITCH%%", mode_switch)
            .replace("%%MODE_KEY%%", mode_key)
            .replace("%%INTRO_HINT%%", hint)
            .replace("%%HOME%%", home))
    return (_DEMO_SHELL.replace("%%CSS%%", DEMO_CSS)
            .replace("%%BODY%%", body)
            .replace("%%JS%%", js)
            .replace("%%DESC_MODE%%", desc)
            .replace("%%LABEL%%", REHEARSAL_LABEL))
