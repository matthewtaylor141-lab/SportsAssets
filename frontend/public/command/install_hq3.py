#!/usr/bin/env python3
"""Install HQ3 LIVE delta on top of the already-integrated HQ2 frontend.
Frontend presentation only. Safe, idempotent, no financial mutations."""
from pathlib import Path
BASE=Path(__file__).resolve().parent
ROOT=BASE.parents[2]

def inject(path, marker, addition, before):
    p=ROOT/path
    if not p.exists(): return False
    s=p.read_text()
    if marker in s:return False
    if before not in s:raise SystemExit(f"{path}: missing anchor {before}")
    p.write_text(s.replace(before,addition+"\n"+before,1))
    return True

changed=[]
pages=[
 "frontend/public/command/index.html","frontend/public/command/floor.html",
 "frontend/public/command/agent.html","frontend/public/command/position.html",
 "frontend/public/command/institutional.html","frontend/public/command/live.html",
 "frontend/public/command/profitability.html","frontend/public/command/acceptance.html"
]
for p in pages:
    if inject(p,'data-hq3="css"','<link rel="stylesheet" href="hq3-live.css" data-hq3="css">','</head>'):changed.append(p+" css")
    if inject(p,'data-hq3="js"','<script src="hq3-live.js" data-hq3="js"></script>','</body>'):changed.append(p+" js")

# Explicit allocator -> Allie display-name patches. Internal agent id / route remain allocator.
fc=ROOT/"frontend/public/command/floor-core.js"
if fc.exists():
    s=fc.read_text()
    old="{agent: 'CHIEF_ALLOCATOR', slug: 'allocator', name: 'Chief Allocator', short: 'Allocation',\n     role: 'Capital allocation · shadow sleeve'"
    new="{agent: 'CHIEF_ALLOCATOR', slug: 'allocator', name: 'Allie', short: 'Chief Allocator',\n     role: 'Chief Allocator · capital allocation · shadow sleeve'"
    if old in s:
        fc.write_text(s.replace(old,new,1));changed.append("floor-core: Allie display identity")

ag=ROOT/"frontend/public/command/agent.html"
if ag.exists():
    s=ag.read_text()
    pairs=[
      ('>Allocator<span class="sep"> · </span><span class="role">Capital</span>',
       '>Allie<span class="sep"> · </span><span class="role">Chief Allocator</span>'),
      ('var label = name === "allocator" ? "Chief Allocator" :',
       'var label = name === "allocator" ? "Allie" :')
    ]
    for a,b in pairs:s=s.replace(a,b)
    ag.write_text(s);changed.append("agent shell: Allie")

# Mark Allie's office so the visual identity can be female/distinct.
a2=ROOT/"frontend/public/command/hq2-agent.js"
if a2.exists():
    s=a2.read_text()
    s=s.replace("hero.className='bt-agent2';hero.style.setProperty('--a',s.accent);",
                "hero.className='bt-agent2';hero.dataset.agent=slug;hero.style.setProperty('--a',s.accent);")
    s=s.replace("allocator:[\"The portfolio matters more than the trade.\"",
                "allocator:[\"The portfolio matters more than the trade.\"")
    a2.write_text(s);changed.append("hq2 agent: Allie marker")

# Update HQ2 fallback persona text if it names generic allocator.
f2=ROOT/"frontend/public/command/hq2-floor.js"
if f2.exists():
    s=f2.read_text().replace("allocator:[\"CONSERVATIVE · PORTFOLIO FIRST\"","allocator:[\"ALLIE · PORTFOLIO FIRST\"")
    f2.write_text(s);changed.append("hq2 floor: Allie persona")

print("HQ3 LIVE installed")
for x in changed:print(" +",x)
