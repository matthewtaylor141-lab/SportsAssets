#!/usr/bin/env python3
"""Install BETTOR HQ6 Complete Command on top of c5c61fa/HQ5. Idempotent."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
changed=[]

pages=[
 "frontend/public/command/index.html","frontend/public/command/floor.html","frontend/public/command/agent.html",
 "frontend/public/command/position.html","frontend/public/command/profitability.html","frontend/public/command/acceptance.html",
 "frontend/public/command/institutional.html","frontend/public/command/live.html","frontend/public/command/improvements.html",
 "frontend/public/command/company.html"
]
for rel in pages:
    p=ROOT/rel
    if not p.exists(): continue
    s=p.read_text()
    if 'data-hq6="css"' not in s:
        s=s.replace("</head>",'<link rel="stylesheet" href="hq6-complete.css" data-hq6="css">\n</head>',1);changed.append(rel+" css")
    if 'data-hq6="js"' not in s:
        s=s.replace("</body>",'<script src="hq6-complete.js" data-hq6="js"></script>\n</body>',1);changed.append(rel+" js")
    p.write_text(s)

# Five primary destinations. Acceptance/Improvements/agents remain reachable
# through Company and the existing Cmd/Ctrl+K palette.
p=ROOT/"frontend/public/command/hq2-shell.js"
s=p.read_text()
old="""var links=[
    ['home','/','⌂','HQ'],['floor','/floor','◈','Floor'],['positions','/positions','◎','Positions'],
    ['profitability','/profitability','↗','Economics'],['company','/company','◇','Company'],['acceptance','/acceptance','✓','Accept']
  ];"""
new="""var links=[
    ['home','/','⌂','HQ'],['floor','/floor','◈','Floor'],['positions','/positions','◎','Positions'],
    ['profitability','/profitability','↗','Economics'],['company','/company','◇','Company']
  ];"""
if old in s:
    s=s.replace(old,new,1);changed.append("primary nav five destinations")
p.write_text(s)

# Floor overview is closer/lower: people are recognizable while all desks stay in frame.
p=ROOT/"frontend/public/command/floor-scene.js"
s=p.read_text()
old="const view = phone ? {target: new THREE.Vector3(0, 0.6, -1.0), az: 0, pol: 0.72, r: 15.5} : {target: new THREE.Vector3(0, 2.1, -1.2), az: 0, pol: 1.2, r: 13.8};"
new="const view = phone ? {target: new THREE.Vector3(0, 0.6, -1.0), az: 0, pol: 0.72, r: 15.5} : {target: new THREE.Vector3(0, 1.55, -0.35), az: 0, pol: 1.06, r: 11.8};"
if old in s:
    s=s.replace(old,new,1);changed.append("closer lower floor overview camera")
if "renderer.toneMappingExposure = 1.05;" in s:
    s=s.replace("renderer.toneMappingExposure = 1.05;","renderer.toneMappingExposure = 1.10;",1);changed.append("floor exposure")
if "scene.add(new THREE.HemisphereLight('#bcd6ff', '#0b1210', phone ? 1.1 : 0.7));" in s:
    s=s.replace("scene.add(new THREE.HemisphereLight('#bcd6ff', '#0b1210', phone ? 1.1 : 0.7));",
                "scene.add(new THREE.HemisphereLight('#bcd6ff', '#0b1210', phone ? 1.1 : 0.92));",1);changed.append("floor face ambient")
needle="scene.add(key);"
if needle in s and "hq6 face fill" not in s:
    add="""scene.add(key);
  // hq6 face fill: the room stays dark while faces/hands remain legible.
  const faceFill = new THREE.DirectionalLight('#c7ddff', phone ? 0.32 : 0.58); // hq6 face fill
  faceFill.position.set(0, 7, 14); scene.add(faceFill);
  const sideFill = new THREE.DirectionalLight('#86c8ff', phone ? 0.14 : 0.26);
  sideFill.position.set(9, 4, 7); scene.add(sideFill);"""
    s=s.replace(needle,add,1);changed.append("floor face fill lights")
p.write_text(s)

# Productive default on agent pages: the live 3D person stays at left, so
# open Work rather than forcing the classic full-page desk first.
p=ROOT/"frontend/public/command/agent.html"
s=p.read_text()
if 'var want = CLASSIC[name] ? "desk" : "ws";' in s:
    s=s.replace('var want = CLASSIC[name] ? "desk" : "ws";','var want = "ws";',1);changed.append("agent productive default")
p.write_text(s)

print("HQ6 Complete Command installed")
for x in changed: print(" +",x)
