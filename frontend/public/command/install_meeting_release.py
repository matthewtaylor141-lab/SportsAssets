#!/usr/bin/env python3
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
changed=[]
def read(p): return (ROOT/p).read_text()
def write(p,s): (ROOT/p).write_text(s)

# Global homepage assets
p="frontend/public/command/index.html";s=read(p)
if 'data-meeting="css"' not in s:s=s.replace("</head>",'<link rel="stylesheet" href="meeting-release.css" data-meeting="css">\n</head>',1);changed.append("index css")
if 'data-meeting="js"' not in s:s=s.replace("</body>",'<script src="meeting-release.js" data-meeting="js"></script>\n</body>',1);changed.append("index js")
write(p,s)

# RESTORE THE REAL THREE.JS AVATAR FLOOR. Remove HQ2 abstract floor assets.
p="frontend/public/command/floor.html";s=read(p)
for tag in [
 '<link rel="stylesheet" href="hq2-floor.css" data-hq2="floor-css">\n',
 '<script src="hq2-floor.js" data-hq2="floor-js"></script>\n',
 '<script type="module" src="hq2-floor-scene.js" data-hq2="floor-scene"></script>\n']:
    s=s.replace(tag,'')
s=s.replace('<!-- data-hq2="legacy-floor-disabled": legacy cloned-avatar floor removed by HQ2 -->','<script type="module" src="floor.js"></script>')
if '<script type="module" src="floor.js"></script>' not in s:s=s.replace("</body>",'<script type="module" src="floor.js"></script>\n</body>',1)
if 'data-meeting="css"' not in s:s=s.replace("</head>",'<link rel="stylesheet" href="meeting-release.css" data-meeting="css">\n</head>',1)
if 'data-meeting="js"' not in s:s=s.replace("</body>",'<script src="meeting-release.js" data-meeting="js"></script>\n</body>',1)
write(p,s);changed.append("restored real 3D floor")

# Agent shell: avatar/desk first where a classic agent page exists.
p="frontend/public/command/agent.html";s=read(p)
s=s.replace('>Allocator<span class="sep"> · </span><span class="role">Capital</span>','>Allie<span class="sep"> · </span><span class="role">Chief Allocator</span>')
s=s.replace('var label = name === "allocator" ? "Chief Allocator" :','var label = name === "allocator" ? "Allie" :')
s=s.replace('var want = "ws";','var want = CLASSIC[name] ? "desk" : "ws";')
if 'data-meeting="css"' not in s:s=s.replace("</head>",'<link rel="stylesheet" href="meeting-release.css" data-meeting="css">\n</head>',1)
if 'data-meeting="js"' not in s:s=s.replace("</body>",'<script src="meeting-release.js" data-meeting="js"></script>\n</body>',1)
write(p,s);changed.append("agent pages avatar-first + Allie")

# Allie shared identity
p="frontend/public/command/floor-core.js";s=read(p)
s=s.replace("{agent: 'CHIEF_ALLOCATOR', slug: 'allocator', name: 'Chief Allocator', short: 'Allocation',\n     role: 'Capital allocation · shadow sleeve'",
            "{agent: 'CHIEF_ALLOCATOR', slug: 'allocator', name: 'Allie', short: 'Chief Allocator',\n     role: 'Chief Allocator · capital allocation · shadow sleeve'")
write(p,s);changed.append("Allie floor identity")

# All 7 in management office
p="frontend/public/command/office.js";s=read(p);a=s.find("const people = [");b=s.find("];",a)
if a>=0 and b>=0 and "id:'karen'" not in s[a:b]:
    arr="""const people = [
 {id:'derek',name:'Derek',initial:'D',role:'Discovery & entry',desc:'Find the opportunity. Explain the conviction.',prompt:'Walk me through your latest entry decision.'},
 {id:'karen',name:'Karen',initial:'K',role:'Red team',desc:'Challenge the claim. Expose missing evidence.',prompt:'What are we missing right now?'},
 {id:'scout',name:'Scout',initial:'S',role:'Market intelligence',desc:'Find structure. Separate hypotheses from facts.',prompt:'What are you researching now?'},
 {id:'archer',name:'Archer',initial:'A',role:'Execution',desc:'Decide whether edge survives execution.',prompt:'Show the latest execution estimates.'},
 {id:'allocator',name:'Allie',initial:'A',role:'Chief Allocator',desc:'Put portfolio integrity ahead of a single trade.',prompt:'How would you allocate capital now?'},
 {id:'audrey',name:'Audrey',initial:'A',role:'Audit & intelligence',desc:'Verify the result. Improve the process.',prompt:'Give me a management briefing.'},
 {id:'xavier',name:'Xavier',initial:'X',role:'Portfolio management',desc:'Own the position. Manage the outcome.',prompt:'What positions are you managing?'}
]"""
    s=s[:a]+arr+s[b+2:]
s=s.replace("3 specialist agents · Shared accountability","7 digital employees · Shared accountability")
write(p,s);changed.append("management office all 7")

# All 7 in legacy sidebar
p="frontend/public/command/app.js";s=read(p)
old='<a class="agent-link" href="/derek"><span class="agent-dot derek" aria-hidden="true"></span><span><b>Derek</b><small>Discovery &amp; Entry</small></span></a><a class="agent-link" href="/xavier"><span class="agent-dot xavier" aria-hidden="true"></span><span><b>Xavier</b><small>Portfolio Management</small></span></a><a class="agent-link" href="/audrey"><span class="agent-dot audrey" aria-hidden="true"></span><span><b>Audrey</b><small>Audit &amp; Intelligence</small></span></a>'
new='<a class="agent-link" href="/derek"><span class="agent-dot derek"></span><span><b>Derek</b><small>Discovery &amp; Entry</small></span></a><a class="agent-link" href="/karen"><span class="agent-dot karen"></span><span><b>Karen</b><small>Red Team</small></span></a><a class="agent-link" href="/scout"><span class="agent-dot scout"></span><span><b>Scout</b><small>Market Intelligence</small></span></a><a class="agent-link" href="/archer"><span class="agent-dot archer"></span><span><b>Archer</b><small>Execution</small></span></a><a class="agent-link" href="/allocator"><span class="agent-dot allocator"></span><span><b>Allie</b><small>Chief Allocator</small></span></a><a class="agent-link" href="/audrey"><span class="agent-dot audrey"></span><span><b>Audrey</b><small>Risk &amp; Audit</small></span></a><a class="agent-link" href="/xavier"><span class="agent-dot xavier"></span><span><b>Xavier</b><small>Portfolio Management</small></span></a>'
s=s.replace(old,new)
write(p,s);changed.append("sidebar all 7")

# Company nav in HQ shell
p="frontend/public/command/hq2-shell.js";s=read(p)
# guarded: the replacement still contains its own search text, so an
# unguarded second run appended the /company test again (not idempotent)
if "path==='/company'?'company':" not in s:
    s=s.replace("path==='/profitability'?'profitability':path==='/acceptance'?'acceptance':","path==='/profitability'?'profitability':path==='/acceptance'?'acceptance':path==='/company'?'company':")
s=s.replace("names={home:'Command',floor:'Trading Floor',positions:'Position Rooms',profitability:'Profitability OS',agents:'AI Team',acceptance:'Acceptance'}","names={home:'Command',floor:'Trading Floor',positions:'Position Rooms',profitability:'Profitability OS',agents:'AI Team',company:'Company',acceptance:'Acceptance'}")
s=s.replace("['profitability','/profitability','↗','Economics'],['agents','/derek','◇','Agents'],['acceptance','/acceptance','✓','Accept']","['profitability','/profitability','↗','Economics'],['company','/company','◇','Company'],['acceptance','/acceptance','✓','Accept']")
s=s.replace("replace('CHIEF_ALLOCATOR','Allocator')","replace('CHIEF_ALLOCATOR','Allie')")
write(p,s);changed.append("Company nav + Allie tape")

# Company route
p="netlify.toml";s=read(p)
if 'command.bettortoken.com/company' not in s:
    block='# ── COMPANY WORKSPACE ──\n[[redirects]]\n  from = "https://command.bettortoken.com/company"\n  to = "/command/company.html"\n  status = 200\n  force = true\n# ── end COMPANY WORKSPACE ──\n\n'
    anchor='[[redirects]]\n  from = "https://command.bettortoken.com/*"'
    if anchor not in s:raise SystemExit("command catch-all anchor missing")
    s=s.replace(anchor,block+anchor,1)
write(p,s);changed.append("company route")

print("BETTOR meeting release installed")
for x in changed: print(" +",x)
