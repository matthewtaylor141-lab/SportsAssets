#!/usr/bin/env python3
"""Install BETTOR Command Headquarters V2 presentation layer.
Idempotent. Frontend only. Does not touch API, workers, migrations, credentials,
financial controls, order paths, thresholds or production data.
Run from repository root:
    python frontend/public/command/install_hq2.py
"""
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[2]

def inject(path, marker, addition, before):
    p = ROOT / path
    s = p.read_text()
    if marker in s:
        return False
    if before not in s:
        raise SystemExit(f"{path}: insertion anchor missing: {before}")
    s = s.replace(before, addition + "\n" + before, 1)
    p.write_text(s)
    return True

common_css = '<link rel="stylesheet" href="hq2-brand.css" data-hq2="brand">'
common_js  = '<script src="hq2-shell.js" data-hq2="shell"></script>'

pages = [
    "frontend/public/command/index.html",
    "frontend/public/command/floor.html",
    "frontend/public/command/agent.html",
    "frontend/public/command/position.html",
    "frontend/public/command/profitability.html",
    "frontend/public/command/acceptance.html",
    "frontend/public/command/institutional.html",
    "frontend/public/command/live.html",
]
changed=[]
for page in pages:
    if inject(page, 'data-hq2="brand"', common_css, "</head>"): changed.append(page)
    if inject(page, 'data-hq2="shell"', common_js, "</body>"): changed.append(page)

if inject("frontend/public/command/floor.html",'data-hq2="floor-css"',
          '<link rel="stylesheet" href="hq2-floor.css" data-hq2="floor-css">',"</head>"): changed.append("floor.html css")
if inject("frontend/public/command/floor.html",'data-hq2="floor-js"',
          '<script src="hq2-floor.js" data-hq2="floor-js"></script>',"</body>"): changed.append("floor.html js")

if inject("frontend/public/command/agent.html",'data-hq2="agent-css"',
          '<link rel="stylesheet" href="hq2-agent.css" data-hq2="agent-css">',"</head>"): changed.append("agent.html css")
if inject("frontend/public/command/agent.html",'data-hq2="agent-js"',
          '<script src="hq2-agent.js" data-hq2="agent-js"></script>',"</body>"): changed.append("agent.html js")

if inject("frontend/public/command/index.html",'data-hq2="equity-css"',
          '<link rel="stylesheet" href="hq2-equity.css" data-hq2="equity-css">',"</head>"): changed.append("index.html equity css")
if inject("frontend/public/command/index.html",'data-hq2="equity-js"',
          '<script src="hq2-equity.js" data-hq2="equity-js"></script>',"</body>"): changed.append("index.html equity js")

# HQ2 owns the floor rendering. Do not load the legacy floor.js module: it
# imports the duplicated/recoloured Rocketbox avatars even when hidden.
floor_page = ROOT / "frontend/public/command/floor.html"
s = floor_page.read_text()
legacy = '<script type="module" src="floor.js"></script>'
if legacy in s and 'data-hq2="legacy-floor-disabled"' not in s:
    s = s.replace(legacy, '<!-- data-hq2="legacy-floor-disabled": legacy cloned-avatar floor removed by HQ2 -->')
    floor_page.write_text(s)
    changed.append("floor.html legacy scene disabled")
if inject("frontend/public/command/floor.html",'data-hq2="floor-scene"',
          '<script type="module" src="hq2-floor-scene.js" data-hq2="floor-scene"></script>',"</body>"):
    changed.append("floor.html HQ2 WebGL scene")

print("HQ2 installed.")
for x in changed: print(" +",x)
