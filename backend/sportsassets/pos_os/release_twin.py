"""THE RELEASE / INCIDENT DIGITAL TWIN (RESEARCH, READ ONLY). Pure; no I/O.

THE EVENTS, all recorded:
  POLICY_VERSION    the first decision of a strategy under a new
                    policy_version (the release as the decisions saw it)
  IMPROVEMENT_RELEASE  an improvement_releases row
  PARAMETER_ACTIVATE / PARAMETER_ROLLBACK  a paper policy parameter
                    activation
  INCIDENT          a CRITICAL Audrey finding on the account

THE REPLAY. For each event at T, the recorded decisions and the positions
they opened in a symmetric window [T - w, T) versus [T, T + w), w = the
shortest of WINDOW_H, the time recorded before T and the time since T:
decisions per hour, entry rate, the mean recorded EV of the entries, the
top refusal codes, positions opened, their realized net and realized profit
per capital-hour, and the after-minus-before differences. A POLICY_VERSION
event compares that strategy's decisions only; the others compare every
decision. This is a RECORDED BEFORE / AFTER comparison, labelled
OBSERVATIONAL_NOT_CAUSAL: it re-executes no policy, invents no decision and
no fill, and is never added to realized results.
"""
from __future__ import annotations

from . import common as C

VERSION = "POS_OS_RELEASE_TWIN_V1"
WINDOW_H = 72.0
MIN_WINDOW_H = 1.0
MAX_EVENTS = 20
LABEL = "OBSERVATIONAL_NOT_CAUSAL"


def events(decisions, improvements, activations, incidents):
    out = []
    by: dict = {}
    for d in sorted(decisions or [], key=lambda d: C.num(d.get("decided_at"))
                    or 0):
        s = d.get("strategy")
        v = d.get("policy_version")
        if not s or not v:
            continue
        prev = by.get(s)
        if prev is not None and prev != v:
            out.append({"kind": "POLICY_VERSION", "strategy": s,
                        "from": prev, "to": v, "at": d["decided_at"]})
        by[s] = v
    for r in improvements or []:
        if r.get("kind") == "RELEASE" and C.num(r.get("released_at")):
            out.append({"kind": "IMPROVEMENT_RELEASE", "strategy": None,
                        "policy_key": r.get("policy_key"),
                        "from": r.get("from_version"),
                        "to": r.get("to_version"), "at": r["released_at"],
                        "id": r.get("release_id")})
    for a in activations or []:
        if a.get("kind") in ("ACTIVATE", "ROLLBACK") and C.num(a.get("at")):
            out.append({"kind": "PARAMETER_" + a["kind"], "strategy": None,
                        "policy_key": a.get("policy_key"),
                        "from": a.get("previous_version_id"),
                        "to": a.get("version_id"), "at": a["at"],
                        "id": a.get("activation_id")})
    for f in incidents or []:
        if C.num(f.get("at")):
            out.append({"kind": "INCIDENT", "strategy": None,
                        "finding": f.get("kind"), "at": f["at"],
                        "id": f.get("finding_id")})
    out.sort(key=lambda e: -e["at"])
    return out[:MAX_EVENTS]


def _side(decs, pos):
    ent = [d for d in decs if d.get("verdict") == "ENTER"]
    evs = [C.num(d.get("recorded_ev_usd")) for d in ent
           if C.num(d.get("recorded_ev_usd")) is not None]
    codes: dict = {}
    for d in decs:
        if d.get("verdict") != "ENTER":
            c = d.get("refusal") or (d.get("refusals") or ["UNRECORDED"])[0]
            codes[c] = codes.get(c, 0) + 1
    closed = [p for p in pos if p.get("state") != "OPEN"
              and C.num(p.get("net_profit_usd")) is not None]
    net = sum(float(p["net_profit_usd"]) for p in closed)
    ch = sum(C.num(p.get("capital_hours")) or 0.0 for p in closed)
    return {"decisions": len(decs), "enters": len(ent),
            "enter_rate": C.share(len(ent), len(decs)),
            "mean_recorded_ev_usd": C.rnd(C.mean(evs)) if evs else None,
            "top_refusals": sorted(({"code": k, "n": v} for k, v in
                                    codes.items()), key=lambda e: -e["n"])[:5],
            "positions_opened": len(pos), "positions_closed": len(closed),
            "realized_net_usd": C.rnd(net) if closed else None,
            "realized_profit_per_capital_hour": C.rnd(net / ch, 9)
            if ch > 0 else None}


def replay(ev, decisions, positions, *, now, earliest):
    w = min(WINDOW_H * C.HOUR, now - ev["at"], ev["at"] - earliest)
    out = dict(ev, label=LABEL)
    if w < MIN_WINDOW_H * C.HOUR:
        out.update(status=C.INSUFFICIENT, window_h=C.rnd(max(w, 0) / C.HOUR),
                   why="%s: less than %g h recorded on a side"
                   % (C.R_BELOW_MIN_SAMPLE, MIN_WINDOW_H))
        return out
    s = ev.get("strategy")

    def dec(lo, hi):
        return [d for d in decisions if lo <= (C.num(d.get("decided_at"))
                                               or -1) < hi
                and (s is None or d.get("strategy") == s)]

    def pos(lo, hi):
        return [p for p in positions if p.get("book") == "PAPER"
                and lo <= (C.num(p.get("opened_at")) or -1) < hi
                and (s is None or p.get("strategy") == s)]
    t = ev["at"]
    b = _side(dec(t - w, t), pos(t - w, t))
    a = _side(dec(t, t + w), pos(t, t + w))
    h = w / C.HOUR
    b["decisions_per_hour"] = C.rnd(b["decisions"] / h)
    a["decisions_per_hour"] = C.rnd(a["decisions"] / h)
    delta = {}
    for k in ("decisions_per_hour", "enter_rate", "mean_recorded_ev_usd",
              "realized_net_usd", "realized_profit_per_capital_hour"):
        delta[k] = (C.rnd(a[k] - b[k], 9) if a[k] is not None
                    and b[k] is not None else None)
    out.update(status=C.MEASURED, window_h=C.rnd(h), before=b, after=a,
               after_minus_before=delta)
    return out


def build(inputs, *, now):
    miss = C.missing(inputs, ["decisions", "positions"])
    if miss:
        return C.unread(miss, inputs, audit=C.BUILT)
    evs = events(inputs["decisions"], inputs.get("improvements"),
                 inputs.get("activations"), inputs.get("incidents"))
    unread = C.missing(inputs, ["improvements", "activations", "incidents"])
    if not evs:
        return C.section(C.EMPTY, "%s: no release, activation or incident "
                         "recorded in the window" % C.R_NO_ROWS,
                         audit=C.BUILT, data={"unread_event_sources": unread})
    earliest = min([C.num(d.get("decided_at")) for d in inputs["decisions"]
                    if C.num(d.get("decided_at")) is not None] or [now])
    return C.section(C.OK, None, audit=C.BUILT, data={
        "version": VERSION, "label": LABEL,
        "replays": [replay(e, inputs["decisions"], inputs["positions"],
                           now=now, earliest=earliest) for e in evs],
        "unread_event_sources": unread, "re_executes_nothing": True},
        sources=["paper_decisions", "pos_economics_latest",
                 "improvement_releases", "paper_policy_parameter_activations",
                 "paper_audrey_findings"])
