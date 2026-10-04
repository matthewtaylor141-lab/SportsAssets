#!/usr/bin/env python3
"""Install BETTOR HQ5 workspace polish. Idempotent, frontend-only."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
pages=[
 "frontend/public/command/index.html","frontend/public/command/floor.html","frontend/public/command/agent.html",
 "frontend/public/command/position.html","frontend/public/command/profitability.html","frontend/public/command/acceptance.html",
 "frontend/public/command/institutional.html","frontend/public/command/live.html","frontend/public/command/improvements.html",
 "frontend/public/command/company.html"
]
changed=[]
for rel in pages:
    p=ROOT/rel
    if not p.exists(): continue
    s=p.read_text()
    if 'data-hq5="css"' not in s:
        s=s.replace("</head>",'<link rel="stylesheet" href="hq5-workspace.css" data-hq5="css">\n</head>',1);changed.append(rel+" css")
    if 'data-hq5="js"' not in s:
        s=s.replace("</body>",'<script src="hq5-workspace.js" data-hq5="js"></script>\n</body>',1);changed.append(rel+" js")
    p.write_text(s)
print("HQ5 installed")
for x in changed: print(" +",x)
