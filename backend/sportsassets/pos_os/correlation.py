"""THE SCENARIO / CORRELATION ENGINE (RESEARCH). Pure; no I/O.

EXPOSURE CORRELATION BY EVENT, LEAGUE AND TEAM over the OPEN PAPER book.
Exposure is the open cost basis (the most a binary contract can lose). Each
open position takes its labels from its entry fill's recorded label: event
(label.event_key, else the fixture, else the market), league
(label.competition) and team (label.participant -- the side backed -- else
the event's two teams). A dimension the label does not carry is the bucket
UNKNOWN, named, never guessed.

  buckets      exposure per bucket, its share, the Herfindahl index and the
               effective number of independent bets (1 / HHI)
  scenarios    "this event / league / team goes against every position in
               it": the loss if every contract in the bucket pays 0 -- an
               UPPER BOUND, flagged when the bucket holds both sides of a
               market (they cannot all lose)
  co_exposure  team pairs held in the same event (one result moves both)
  realized     the empirical outcome correlation of closed positions: the
               phi coefficient of (net > 0) over pairs settled the same UTC
               day in the SAME league versus in DIFFERENT leagues --
               measured co-movement, INSUFFICIENT below MIN_PAIRS pairs
Nothing here caps, hedges or sizes anything.
"""
from __future__ import annotations

import datetime as _dt

from . import common as C

VERSION = "POS_OS_CORRELATION_V1"
MIN_PAIRS = 20
MAX_PAIRS = 5000
UNKNOWN = "UNKNOWN"


def labels_by_position(fills):
    out = {}
    for f in sorted(fills or [], key=lambda f: C.num(f.get("filled_at")) or 0):
        k = (f.get("group_id"), f.get("us_market_slug"))
        if k in out or str(f.get("direction") or "BUY").upper() != "BUY":
            continue
        lab = C.jload(f.get("label")) or {}
        lab = lab if isinstance(lab, dict) else {}
        teams = sorted({t for t in (lab.get("home_team"), lab.get("away_team"))
                        if t})
        out[k] = {"event": lab.get("event_key") or f.get("fixture")
                  or f.get("us_market_slug") or UNKNOWN,
                  "league": lab.get("competition") or UNKNOWN,
                  "team": lab.get("participant") or (
                      " v ".join(teams) if teams else UNKNOWN),
                  "event_teams": teams}
    return out


def _tag(p, labels):
    lab = labels.get((p.get("group_id"), p.get("us_market_slug"))) or {
        "event": p.get("us_market_slug") or UNKNOWN, "league": UNKNOWN,
        "team": UNKNOWN, "event_teams": []}
    return lab


def exposure(positions, fills):
    labels = labels_by_position(fills)
    open_ = [p for p in positions or [] if p.get("book") == "PAPER"
             and p.get("state") == "OPEN"
             and (C.num(p.get("open_cost_basis_usd")) or 0) > 0]
    total = sum(float(p["open_cost_basis_usd"]) for p in open_)
    out = {"open_positions": len(open_), "exposure_usd": C.rnd(total),
           "dimensions": {}, "scenarios": [], "co_exposure": []}
    if not open_:
        return out
    tagged = [(p, _tag(p, labels)) for p in open_]
    for dim in ("event", "league", "team"):
        b: dict = {}
        for p, lab in tagged:
            e = b.setdefault(lab[dim], {"positions": 0, "exposure": 0.0,
                                        "sides": set()})
            e["positions"] += 1
            e["exposure"] += float(p["open_cost_basis_usd"])
            e["sides"].add((p.get("us_market_slug"), p.get("holding_side")))
        rows = []
        for k, e in b.items():
            slugs = {s for s, _ in e["sides"]}
            rows.append({"bucket": k, "positions": e["positions"],
                         "exposure_usd": C.rnd(e["exposure"]),
                         "share": C.share(e["exposure"], total),
                         "both_sides_or_several_markets": len(e["sides"]) > 1
                         and (len(slugs) < len(e["sides"]) or len(slugs) > 1)})
        rows.sort(key=lambda r: -r["exposure_usd"])
        hhi = sum((r["share"] or 0) ** 2 for r in rows)
        out["dimensions"][dim] = {
            "buckets": rows[:25], "bucket_count": len(rows),
            "hhi": C.rnd(hhi), "effective_bets": C.rnd(1 / hhi)
            if hhi else None,
            "unknown_exposure_usd": C.rnd(sum(r["exposure_usd"] for r in rows
                                              if r["bucket"] == UNKNOWN))}
        for r in rows[:5]:
            out["scenarios"].append({
                "scenario": "%s %s goes against every position" % (dim,
                                                                   r["bucket"]),
                "dimension": dim, "bucket": r["bucket"],
                "loss_upper_bound_usd": r["exposure_usd"],
                "share_of_book": r["share"],
                "upper_bound_only": r["both_sides_or_several_markets"]})
    out["scenarios"].sort(key=lambda s: -s["loss_upper_bound_usd"])
    pairs: dict = {}
    for p, lab in tagged:
        ts = lab["event_teams"]
        if len(ts) == 2:
            k = tuple(ts)
            pairs[k] = pairs.get(k, 0.0) + float(p["open_cost_basis_usd"])
    out["co_exposure"] = [{"teams": list(k), "exposure_usd": C.rnd(v)}
                          for k, v in sorted(pairs.items(),
                                             key=lambda kv: -kv[1])][:20]
    return out


def _phi(pairs):
    if len(pairs) < 2:
        return None
    xs = [a for a, _ in pairs] + [b for _, b in pairs]
    ys = [b for _, b in pairs] + [a for a, _ in pairs]
    mx, my = C.mean(xs), C.mean(ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sx = sum((x - mx) ** 2 for x in xs) ** 0.5
    sy = sum((y - my) ** 2 for y in ys) ** 0.5
    return None if not sx or not sy else sxy / (sx * sy)


def realized(positions, fills):
    labels = labels_by_position(fills)
    closed = [p for p in positions or [] if p.get("book") == "PAPER"
              and p.get("state") != "OPEN"
              and C.num(p.get("net_profit_usd")) is not None
              and C.num(p.get("released_at")) is not None]
    rows = [(_dt.datetime.fromtimestamp(p["released_at"], _dt.timezone.utc)
             .date().isoformat(), _tag(p, labels)["league"],
             1.0 if p["net_profit_usd"] > 0 else 0.0) for p in closed]
    same, diff = [], []
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            if rows[i][0] != rows[j][0]:
                continue
            tgt = same if rows[i][1] == rows[j][1] != UNKNOWN else diff
            if len(tgt) < MAX_PAIRS:
                tgt.append((rows[i][2], rows[j][2]))
    out = {}
    for k, ps in (("same_league_same_day", same),
                  ("different_league_same_day", diff)):
        v = _phi(ps) if len(ps) >= MIN_PAIRS else None
        out[k] = {"pairs": len(ps), "phi": C.rnd(v),
                  "status": C.MEASURED if v is not None else C.INSUFFICIENT,
                  "why": None if v is not None else (
                      "%s: fewer than %d pairs or no variation"
                      % (C.R_BELOW_MIN_SAMPLE, MIN_PAIRS))}
    return out


def build(inputs, *, now):
    miss = C.missing(inputs, ["positions", "fills"])
    if miss:
        return C.unread(miss, inputs, audit=C.BUILT)
    ex = exposure(inputs["positions"], inputs["fills"])
    rz = realized(inputs["positions"], inputs["fills"])
    has = ex["open_positions"] or any(v["pairs"] for v in rz.values())
    return C.section(C.OK if has else C.EMPTY,
                     None if has else "%s: no open PAPER position and no "
                     "closed pair" % C.R_NO_ROWS, audit=C.BUILT,
                     data={"version": VERSION, "exposure": ex,
                           "realized_outcome_correlation": rz,
                           "summed_across_books": False,
                           "existing_cluster_view":
                           "sportsassets/intel/risk.py:clusters"},
                     sources=["pos_economics_latest", "paper_fills"])
