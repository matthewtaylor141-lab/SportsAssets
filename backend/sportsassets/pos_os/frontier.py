"""THE CAPACITY FRONTIER (RESEARCH). Pure; no I/O.

Per strategy, the depth-limited SIZE versus NET $ curve, from the capacity
model's recorded per-candidate EXPECTED_EDGE_AT_SIZE grid
(profitability/capacity.py, persisted in pos_capacity): at each USD size,
  measured        candidates whose recorded book could absorb the size
  depth_limited   candidates whose recorded book could NOT
                  (EXCEEDS_VISIBLE_DEPTH: nothing is invented past the book)
  mean net $      mean expected net profit over the measured candidates
  depth-adjusted  mean net $ x measured share: the expected net $ per
                  candidate when a size the book cannot absorb earns nothing
The frontier optimum is the size with the highest depth-adjusted net $.
EXPECTED and CONDITIONAL ON FILLING at the recorded book (the capacity
model's own label); never a realized figure.
"""
from __future__ import annotations

from . import common as C

VERSION = "POS_OS_FRONTIER_V1"
MIN_CANDIDATES = 3


def frontier(rows, *, min_n=MIN_CANDIDATES):
    by: dict = {}
    for r in rows or []:
        if r.get("status") != C.MEASURED or not isinstance(
                r.get("edge_at_size"), list):
            continue
        by.setdefault(str(r.get("strategy") or "UNKNOWN"), []).append(r)
    out = []
    for s, cands in sorted(by.items()):
        grid: dict = {}
        for c in cands:
            for g in c["edge_at_size"]:
                size = C.num((g or {}).get("size_usd"))
                if size is None:
                    continue
                e = grid.setdefault(size, {"measured": [], "limited": 0})
                v = C.num(g.get("expected_net_profit_usd"))
                if g.get("status") == C.MEASURED and v is not None:
                    e["measured"].append(v)
                else:
                    e["limited"] += 1
        curve = []
        for size in sorted(grid):
            e = grid[size]
            n = len(e["measured"]) + e["limited"]
            m = C.mean(e["measured"]) if e["measured"] else None
            sh = len(e["measured"]) / n if n else None
            curve.append({"size_usd": size, "measured": len(e["measured"]),
                          "depth_limited": e["limited"],
                          "mean_expected_net_usd": C.rnd(m),
                          "depth_adjusted_net_usd": C.rnd(
                              m * sh if m is not None else None),
                          "edge_per_dollar": C.rnd(m / size, 9)
                          if m is not None and size else None})
        best = max((c for c in curve
                    if c["depth_adjusted_net_usd"] is not None),
                   key=lambda c: c["depth_adjusted_net_usd"], default=None)
        caps = sorted(C.num(c.get("executable_capacity_usd")) for c in cands
                      if C.num(c.get("executable_capacity_usd")) is not None)
        out.append({
            "strategy": s, "candidates": len(cands), "curve": curve,
            "status": C.MEASURED if len(cands) >= min_n else C.INSUFFICIENT,
            "optimum_size_usd": best["size_usd"] if best else None,
            "optimum_depth_adjusted_net_usd":
                best["depth_adjusted_net_usd"] if best else None,
            "median_executable_capacity_usd": C.rnd(
                C.quantile(caps, 0.5)) if caps else None})
    return {"version": VERSION, "strategies": out,
            "label": "EXPECTED_CONDITIONAL_ON_FILL_AT_RECORDED_BOOK",
            "min_candidates": min_n}


def build(inputs, *, now):
    if inputs.get("capacity") is None:
        return C.unread(["capacity"], inputs, audit=C.BUILT,
                        sources=["pos_capacity_latest"])
    d = frontier(inputs["capacity"])
    return C.section(C.OK if d["strategies"] else C.EMPTY,
                     None if d["strategies"] else
                     "%s: no MEASURED capacity assessment" % C.R_NO_ROWS,
                     audit=C.BUILT, data=d, sources=["pos_capacity_latest"],
                     existing={"per_candidate_grid":
                               "sportsassets/profitability/capacity.py"})
