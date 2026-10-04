#!/usr/bin/env python3
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
changed = []
def read(p): return (ROOT/p).read_text()
def write(p,s): (ROOT/p).write_text(s)

p="frontend/public/command/index.html";s=read(p)
if 'data-hq4="css"' not in s:s=s.replace("</head>",'<link rel="stylesheet" href="hq4-company.css" data-hq4="css">\n</head>',1);changed.append("index css")
if 'data-hq4="js"' not in s:s=s.replace("</body>",'<script src="hq4-company.js" data-hq4="js"></script>\n</body>',1);changed.append("index js")
write(p,s)

p="frontend/public/command/office.js";s=read(p);a=s.find("const people = [");b=s.find("];",a)
if a>=0 and b>=0 and "id:'karen'" not in s[a:b]:
    arr="const people = [\n {id:'derek',name:'Derek',initial:'D',role:'Discovery & entry',desc:'Find the opportunity. Explain the conviction.',prompt:'Walk me through your latest entry decision.'},\n {id:'karen',name:'Karen',initial:'K',role:'Red team',desc:'Challenge the claim. Expose missing evidence.',prompt:'What are we missing right now?'},\n {id:'scout',name:'Scout',initial:'S',role:'Market intelligence',desc:'Find structure. Separate hypotheses from facts.',prompt:'What are you researching now?'},\n {id:'eddie',name:'Eddie',initial:'E',role:'Execution',desc:'Decide whether edge survives execution.',prompt:'Show the latest execution estimates.'},\n {id:'allocator',name:'Allie',initial:'A',role:'Chief Allocator',desc:'Put portfolio integrity ahead of a single trade.',prompt:'How would you allocate capital now?'},\n {id:'audrey',name:'Audrey',initial:'A',role:'Audit & intelligence',desc:'Verify the result. Improve the process.',prompt:'Give me a management briefing.'},\n {id:'xavier',name:'Xavier',initial:'X',role:'Portfolio management',desc:'Own the position. Manage the outcome.',prompt:'What positions are you managing?'}\n]"
    s=s[:a]+arr+s[b+2:];changed.append("office all 7")
s=s.replace("3 specialist agents · Shared accountability","7 digital employees · Shared accountability");write(p,s)

p="frontend/public/command/app.js";s=read(p)
old='<a class="agent-link" href="/derek"><span class="agent-dot derek" aria-hidden="true"></span><span><b>Derek</b><small>Discovery &amp; Entry</small></span></a><a class="agent-link" href="/xavier"><span class="agent-dot xavier" aria-hidden="true"></span><span><b>Xavier</b><small>Portfolio Management</small></span></a><a class="agent-link" href="/audrey"><span class="agent-dot audrey" aria-hidden="true"></span><span><b>Audrey</b><small>Audit &amp; Intelligence</small></span></a>'
new='<a class="agent-link" href="/derek"><span class="agent-dot derek"></span><span><b>Derek</b><small>Discovery &amp; Entry</small></span></a><a class="agent-link" href="/karen"><span class="agent-dot karen"></span><span><b>Karen</b><small>Red Team</small></span></a><a class="agent-link" href="/scout"><span class="agent-dot scout"></span><span><b>Scout</b><small>Market Intelligence</small></span></a><a class="agent-link" href="/eddie"><span class="agent-dot eddie"></span><span><b>Eddie</b><small>Execution</small></span></a><a class="agent-link" href="/allocator"><span class="agent-dot allocator"></span><span><b>Allie</b><small>Chief Allocator</small></span></a><a class="agent-link" href="/audrey"><span class="agent-dot audrey"></span><span><b>Audrey</b><small>Risk &amp; Audit</small></span></a><a class="agent-link" href="/xavier"><span class="agent-dot xavier"></span><span><b>Xavier</b><small>Portfolio Management</small></span></a>'
if old in s:s=s.replace(old,new,1);changed.append("sidebar all 7")
write(p,s)

p="frontend/public/command/floor-core.js";s=read(p)
old="{agent: 'CHIEF_ALLOCATOR', slug: 'allocator', name: 'Chief Allocator', short: 'Allocation',\n     role: 'Capital allocation · shadow sleeve'"
new="{agent: 'CHIEF_ALLOCATOR', slug: 'allocator', name: 'Allie', short: 'Chief Allocator',\n     role: 'Chief Allocator · capital allocation · shadow sleeve'"
if old in s:s=s.replace(old,new,1);changed.append("Allie floor identity")
write(p,s)

p="frontend/public/command/agent.html";s=read(p)
ns=s.replace('>Allocator<span class="sep"> · </span><span class="role">Capital</span>','>Allie<span class="sep"> · </span><span class="role">Chief Allocator</span>').replace('var label = name === "allocator" ? "Chief Allocator" :','var label = name === "allocator" ? "Allie" :')
if ns!=s:changed.append("Allie shell")
write(p,ns)

p="frontend/public/command/hq2-shell.js";s=read(p)
# guarded: the replacement contains its own search text, so an unguarded run
# re-inserts the /company test every time (and the meeting release already
# added it): not idempotent
ns=s if "path==='/company'?'company':" in s else s.replace("path==='/profitability'?'profitability':path==='/acceptance'?'acceptance':","path==='/profitability'?'profitability':path==='/acceptance'?'acceptance':path==='/company'?'company':")
ns=ns.replace("names={home:'Command',floor:'Trading Floor',positions:'Position Rooms',profitability:'Profitability OS',agents:'AI Team',acceptance:'Acceptance'}","names={home:'Command',floor:'Trading Floor',positions:'Position Rooms',profitability:'Profitability OS',agents:'AI Team',company:'Company',acceptance:'Acceptance'}")
ns=ns.replace("['profitability','/profitability','↗','Economics'],['agents','/derek','◇','Agents'],['acceptance','/acceptance','✓','Accept']","['profitability','/profitability','↗','Economics'],['company','/company','◇','Company'],['acceptance','/acceptance','✓','Accept']")
if ns!=s:changed.append("Company shell nav")
write(p,ns)

p="netlify.toml";s=read(p)
if 'command.bettortoken.com/company' not in s:
    block = '# ── COMPANY WORKSPACE ──\n[[redirects]]\n  from = "https://command.bettortoken.com/company"\n  to = "/command/company.html"\n  status = 200\n  force = true\n# ── end COMPANY WORKSPACE ──\n\n'
    anchor='[[redirects]]\n  from = "https://command.bettortoken.com/*"'
    if anchor not in s: raise SystemExit("command catch-all anchor missing")
    s=s.replace(anchor,block+anchor,1);changed.append("company route")
write(p,s)

print("HQ4 Company OS installed")
for x in changed: print(" +",x)
