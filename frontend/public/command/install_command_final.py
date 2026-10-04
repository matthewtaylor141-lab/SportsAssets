#!/usr/bin/env python3
"""Install BETTOR Command Final Convergence.

Base expected: the current HQ6 production frontend (4d57500 or descendant).
Idempotent. Presentation/read-only changes plus truthful work-state plumbing.
No backend financial behavior is modified.
"""
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[3]
C=ROOT/"frontend/public/command"
changed=[]

def patch(path, old, new, label, required=False):
    p=ROOT/path
    if not p.exists():
        if required: raise SystemExit(f"missing required file: {path}")
        return
    s=p.read_text()
    if new in s: return
    if old not in s:
        if required: raise SystemExit(f"required patch anchor missing: {label} in {path}")
        return
    p.write_text(s.replace(old,new,1)); changed.append(label)

# 1) Load the single final convergence layer on every Command page.
pages=[
"frontend/public/command/index.html","frontend/public/command/floor.html",
"frontend/public/command/agent.html","frontend/public/command/company.html",
"frontend/public/command/acceptance.html","frontend/public/command/improvements.html",
"frontend/public/command/institutional.html","frontend/public/command/live.html",
"frontend/public/command/position.html","frontend/public/command/profitability.html",
"frontend/public/command/center.html"]
for rel in pages:
    p=ROOT/rel
    if not p.exists(): continue
    s=p.read_text()
    if 'data-command-final="css"' not in s:
        s=s.replace("</head>",'<link rel="stylesheet" href="command-final.css" data-command-final="css">\n</head>',1);changed.append(rel+" final css")
    if 'data-command-final="js"' not in s:
        s=s.replace("</body>",'<script src="command-final.js" data-command-final="js"></script>\n</body>',1);changed.append(rel+" final js")
    p.write_text(s)

# 2) The new backend work_state is the UX source of truth when present.
p=C/"floor-core.js"; s=p.read_text()
old="""    UNKNOWN:      {label: 'Unknown',          color: '#4a5566', tone: 'off',    motion: 'still'}
  };"""
new="""    UNKNOWN:      {label: 'Unknown',          color: '#4a5566', tone: 'off',    motion: 'still'},
    WORKING:      {label: 'Working',          color: '#50d8ac', tone: 'green',  motion: 'work'},
    WAITING_FOR_FRESH_EVIDENCE:{label:'Waiting for fresh evidence',color:'#e9be74',tone:'amber',motion:'idle'},
    BLOCKED_ON_MARKET_DATA:{label:'Blocked on market data',color:'#ff9d78',tone:'red',motion:'idle'},
    HANDOFF_PENDING:{label:'Handoff pending',color:'#82b8ff',tone:'blue',motion:'review'},
    IDLE_NO_OPEN_WORK:{label:'No open work',color:'#93a3b8',tone:'slate',motion:'idle'}
  };"""
if old in s: s=s.replace(old,new,1);changed.append("floor-core work-state vocabulary")
old2="function stateOf(a) { return a && STATES[a.state] ? a.state : 'UNKNOWN'; }"
new2="function stateOf(a) { var w=a&&a.work_state; if(w&&STATES[w])return w; return a&&STATES[a.state]?a.state:'UNKNOWN'; }"
if old2 in s: s=s.replace(old2,new2,1);changed.append("floor-core work-state precedence")
p.write_text(s)

# 3) Floor boards use the operational work detail, not legacy desk detail.
patch("frontend/public/command/floor-scene.js",
      "const detail = !a ? (readMeta.why || 'No floor read yet') : (a.state_detail || '');",
      "const detail = !a ? (readMeta.why || 'No floor read yet') : (a.work_detail || a.state_detail || '');",
      "floor boards work_detail", True)

# More lifelike collaboration: while visiting a colleague, gesture/nod; during
# a walk add torso/head rhythm. This still happens ONLY on a real collaboration.
p=C/"floor-scene.js"; s=p.read_text()
old="""  function stepArms(av, dt, T) {
    const p = av.pose; const tgt = av.walk ? 0 : (p.target || 0);
    p.type = damp(p.type, tgt, 3, dt);
    if (p.type < 0.01 && !av.walk) return;
    const typing = p.type;"""
new="""  function stepArms(av, dt, T) {
    const p = av.pose; const social = av.walk && av.walk.stage === 'hold';
    if (social) {
      const beat = 0.5 + 0.5 * Math.sin(T * 2.3 + av.phase);
      av.ctl._hinge('rightUpperArm', 0.07 + 0.05 * beat);
      av.ctl._hinge('rightLowerArm', 0.42 + 0.20 * beat);
      av.ctl._hinge('leftUpperArm', 0.04);
      av.ctl._hinge('leftLowerArm', 0.28);
      av.ctl._turn('head', 0.015 * Math.sin(T * 2.0), 0.03 * Math.sin(T * 0.9), 0);
      av.ctl._turn('chest', 0, 0, 0.018 * Math.sin(T * 1.25));
      return;
    }
    const pWalk = av.walk && (av.walk.stage === 'out' || av.walk.stage === 'back');
    if (pWalk) {
      const gait = Math.sin(av.phase);
      av.ctl._turn('chest', -0.018, 0, gait * 0.022);
      av.ctl._turn('head', 0.008 * Math.cos(av.phase), 0, -gait * 0.010);
    }
    const tgt = av.walk ? 0 : (p.target || 0);
    p.type = damp(p.type, tgt, 3, dt);
    if (p.type < 0.01 && !av.walk) return;
    const typing = p.type;"""
if old in s: s=s.replace(old,new,1);changed.append("floor collaboration body language")
p.write_text(s)

# 4) Equity: current MARK OBSERVATIONS should keep the line live even if the
# price itself did not change. Still no invented value and no interpolation.
p=C/"equity-wall.js"; s=p.read_text()
if "var CURVE_REFRESH_MS = 60000;" in s:
    s=s.replace("var CURVE_REFRESH_MS = 60000;","var CURVE_REFRESH_MS = 10000;",1);changed.append("equity curve refresh 10s")
if "if (c && force && Date.now() - c.at < 15000) { return; }" in s:
    s=s.replace("if (c && force && Date.now() - c.at < 15000) { return; }","if (c && force && Date.now() - c.at < 5000) { return; }",1);changed.append("equity forced curve refresh 5s")
old="""      if (ok && !acct.no_new_mark) {
        // the live value, placed at its last genuine change, held to now
        var at = Math.max(last ? last.t : (since || now), changeAt || 0);
        if (!last || last.v !== eq) { series.push({t: Math.min(at, now), v: eq, cause: 'LIVE'}); }
        end = {t: now, v: eq, live: true};"""
new="""      var ma = acct.marks_as_of || {};
      var markFresh = fin(ma.newest_age_s) && ma.newest_age_s <= (ma.stale_mark_after_s || acct.stale_after_s || 300);
      if (ok && markFresh) {
        // A CURRENT RECORDED MARK keeps the endpoint live even when its price
        // is unchanged. The value is the server's current equity; no price is
        // interpolated or invented between observations.
        var at = Math.max(last ? last.t : (since || now), ts(ma.newest_at) || changeAt || 0);
        if (!last || last.v !== eq || at > last.t) { series.push({t: Math.min(at, now), v: eq, cause: 'LIVE'}); }
        end = {t: now, v: eq, live: true};"""
if old in s: s=s.replace(old,new,1);changed.append("equity current-mark endpoint")
p.write_text(s)

# 5) Homepage/company cards display work_state/work_detail when R30 serves it.
p=C/"meeting-release.js"; s=p.read_text()
s=s.replace("var s=(a&&a.state)||'';","var s=(a&&(a.work_state||a.state))||'';",1)
s=s.replace("String(a.state||'UNKNOWN').replace(/_/g,' ')","String(a.work_state||a.state||'UNKNOWN').replace(/_/g,' ')",1)
s=s.replace("a&&a.state_detail||'Open workspace for current evidence.'","a&&(a.work_detail||a.state_detail)||'Open workspace for current evidence.'",1)
p.write_text(s);changed.append("meeting home work_state")

p=C/"hq4-company.js"; s=p.read_text()
s=s.replace("var s=(a&&a.state)||'';","var s=(a&&(a.work_state||a.state))||'';",1)
s=s.replace("String(a.state||'UNKNOWN').replace(/_/g,' ')","String(a.work_state||a.state||'UNKNOWN').replace(/_/g,' ')",1)
s=s.replace("a&&a.state_detail||'Open '+m.name+'’s workspace for current work and evidence.'","a&&(a.work_detail||a.state_detail)||'Open '+m.name+'’s workspace for current work and evidence.'",1)
s=s.replace("/WORK|REVIEW|CHALLENG/.test(a.state||'')","/WORK|REVIEW|CHALLENG/.test(a.work_state||a.state||'')",1)
p.write_text(s);changed.append("company work_state")

print("BETTOR Command Final installed")
for x in changed: print(" +",x)
