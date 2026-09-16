"""BETA48 Engine B — M1 (favourite-longshot at the mid) and M2 (touch quality).

Runs exactly the experiment frozen in ENGINE_B_PREREGISTRATION.md.

The whole point of the reconstructed mid is stated once here because it
is what separates this from Track P, which failed:

    Track P measured SETTLE - ASK and found it negative in 9 of 10 price
    bands, and read that as "the ask is systematically expensive". It is
    -- on BOTH legs, by the round-trip spread. A deficit that appears
    symmetrically on both sides of a binary carries no direction and
    cannot be traded. Measured here: ask(leg0)+ask(leg1) has median
    1.0100 and minimum 1.0000 over 4,101 near-simultaneous pairs.

    So the only referenced price that can carry a tradable bias is the
    MID, and on a binary the complement's ask supplies it:

        bid(leg0) = 1 - ask(leg1)
        mid(leg0) = (ask(leg0) + 1 - ask(leg1)) / 2

Leakage rules enforced in code, not by convention:
  * a row is admitted only when resolved_at is STRICTLY after BOTH probes
  * RN1's price/size/side are never read into a feature
  * no post-entry price, no future probe, no settlement field reaches a
    decision
  * splits are chronological and fixed; the holdout is read once

Read-only. No orders, no capital, no production write.
"""

from __future__ import annotations

import bisect
import gzip
import json
import math
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SNAP = HERE.parent / "snapshots"

MAX_GAP_S = 5.0          # frozen: opposite-leg probes must be this close
TRAIN_END = "2026-08-21"
VALID_END = "2026-08-31"
FEE_GRID_BP = (0, 100, 200)
GATE_ROI_PP = 3.3        # derived in the preregistration, not chosen
GATE_MIN_CONDITIONS = 200


def ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def day(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")


def split_of(d: str) -> str:
    if d <= TRAIN_END:
        return "TRAIN"
    if d <= VALID_END:
        return "VALIDATION"
    return "HOLDOUT"


# ------------------------------------------------------------------ load --

def load_rows() -> list:
    settle = {}
    for line in open(SNAP / "settlement_v1.jsonl"):
        r = json.loads(line)
        if not r.get("resolved"):
            continue
        p = r.get("payouts") or []
        if len(p) != 2:
            continue
        try:
            p = [float(x) for x in p]
        except (TypeError, ValueError):
            continue
        if sorted(p) != [0.0, 1.0]:
            continue
        if not r.get("resolved_at"):
            continue
        settle[r["condition_id"]] = (p, ts(r["resolved_at"]))

    legs = defaultdict(lambda: defaultdict(list))
    idxset = defaultdict(set)
    sport = {}
    for line in gzip.open(SNAP / "u2_events_v1.jsonl.gz", "rt"):
        r = json.loads(line)
        if not r.get("book_ok"):
            continue
        ba = r.get("best_ask")
        if ba in (None, ""):
            continue
        c = r["condition_id"]
        idxset[c].add(r["outcome_index"])
        sport[c] = r.get("sport") or "unclassified"
        d = r.get("depth") or []
        try:
            top_sz = float(d[0][1]) if d else 0.0
        except (TypeError, ValueError, IndexError):
            top_sz = 0.0
        legs[c][r["outcome_index"]].append((ts(r["probe_at"]), float(ba), top_sz))

    binary = {c for c, v in idxset.items() if v == {0, 1}}
    rows = []
    for c in binary:
        s = settle.get(c)
        if s is None:
            continue
        a = sorted(legs[c][0])
        b = sorted(legs[c][1])
        bt = [x[0] for x in b]
        for (t0, ask0, sz0) in a:
            j = bisect.bisect_left(bt, t0)
            best = None
            for k in (j - 1, j):
                if 0 <= k < len(b):
                    g = abs(b[k][0] - t0)
                    if best is None or g < best[0]:
                        best = (g, b[k])
            if best is None or best[0] > MAX_GAP_S:
                continue
            gap, (t1, ask1, sz1) = best
            t_entry = max(t0, t1)
            # LEAKAGE GATE: the market must still be live at entry.
            if s[1] <= t_entry:
                continue
            mid0 = (ask0 + (1.0 - ask1)) / 2.0
            if not (0.0 < mid0 < 1.0):
                continue
            rows.append({
                "cond": c, "t": t_entry, "day": day(t_entry),
                "split": split_of(day(t_entry)),
                "ask0": ask0, "ask1": ask1,
                "sz0": sz0, "sz1": sz1,
                "mid0": mid0, "mid1": 1.0 - mid0,
                "spread": ask0 + ask1 - 1.0,
                "touch": min(sz0, sz1),
                "win0": s[0][0], "sport": sport[c],
            })
    return rows


# --------------------------------------------------------------- stats --

def cluster_ci(values_by_cond: dict) -> tuple:
    """Mean and 95% CI clustered by condition: one draw per condition."""
    per = [statistics.fmean(v) for v in values_by_cond.values() if v]
    n = len(per)
    if n < 2:
        return (statistics.fmean(per) if per else 0.0, None, None, n)
    m = statistics.fmean(per)
    sd = statistics.stdev(per)
    se = sd / math.sqrt(n)
    return (m, m - 1.96 * se, m + 1.96 * se, n)


# ------------------------------------------------------- M1 calibration --

def calibration(rows: list, label: str) -> list:
    """Q1: is the MID biased? Train-period descriptive step."""
    bands = [(0.02, 0.10), (0.10, 0.20), (0.20, 0.30), (0.30, 0.40),
             (0.40, 0.50), (0.50, 0.60), (0.60, 0.70), (0.70, 0.80),
             (0.80, 0.90), (0.90, 0.98)]
    # Each row gives TWO observations: leg0 at mid0, leg1 at mid1. Using
    # both keeps the test symmetric, which is the whole point -- a bias
    # that is really the spread shows up as equal and opposite here.
    obs = []
    for r in rows:
        obs.append((r["mid0"], r["win0"], r["cond"]))
        obs.append((r["mid1"], 1.0 - r["win0"], r["cond"]))
    out = []
    print(f"\n--- MID CALIBRATION ({label}) ---")
    print(f"{'BAND':<14}{'N':>7}{'MEAN_MID':>10}{'SETTLE':>9}"
          f"{'SETTLE-MID':>12}{'95% CI':>22}")
    for lo, hi in bands:
        sel = [(m, w, c) for (m, w, c) in obs if lo <= m < hi]
        if len(sel) < 30:
            continue
        by = defaultdict(list)
        for m, w, c in sel:
            by[c].append(w - m)
        d, lo_ci, hi_ci, nc = cluster_ci(by)
        mm = statistics.fmean([m for m, _, _ in sel])
        ss = statistics.fmean([w for _, w, _ in sel])
        ci = f"[{lo_ci:+.4f}, {hi_ci:+.4f}]" if lo_ci is not None else "n/a"
        print(f"[{lo:.2f},{hi:.2f})  {len(sel):>7,}{mm:>10.4f}{ss:>9.4f}"
              f"{d:>+12.4f}{ci:>22}")
        out.append({"band": f"[{lo:.2f},{hi:.2f})", "n": len(sel),
                    "conditions": nc, "mean_mid": mm, "settle": ss,
                    "deficit": d, "ci_lo": lo_ci, "ci_hi": hi_ci})
    return out


# ------------------------------------------------------------ the rule --

def trade(rows: list, direction: str, thr: float, fee_bp: int,
          touch_filter: str = "ALL", touch_cut: float = 0.0) -> dict:
    """Frozen execution model: BUY only, TAKER only, at the observed ask.

    `direction` = 'FAVOURITE' buys the leg whose mid is the higher one
    when mid - ask edge clears `thr`. It is the only directional rule M1
    licenses; the reverse ('LONGSHOT') is reported alongside purely so a
    reader can see both signs of the same test, never to pick the better.
    """
    fee = fee_bp / 10000.0
    by_cond = defaultdict(list)
    n = 0
    stake_total = 0.0
    cap = []
    for r in rows:
        if touch_filter == "THIN" and r["touch"] >= touch_cut:
            continue
        if touch_filter == "DEEP" and r["touch"] < touch_cut:
            continue
        # which leg is the favourite at MID
        fav = 0 if r["mid0"] >= 0.5 else 1
        leg = fav if direction == "FAVOURITE" else 1 - fav
        ask = r["ask0"] if leg == 0 else r["ask1"]
        mid = r["mid0"] if leg == 0 else r["mid1"]
        won = r["win0"] if leg == 0 else 1.0 - r["win0"]
        # Expected edge at entry, using ONLY pre-entry quantities.
        edge = mid - ask
        if edge < thr:
            continue
        if not (0.0 < ask < 1.0):
            continue
        ret = (won - ask) / ask - fee          # per dollar staked
        by_cond[r["cond"]].append(ret)
        n += 1
        stake_total += 1.0
        cap.append(min(r["sz0"], r["sz1"]) * ask)
    m, lo, hi, nc = cluster_ci(by_cond)
    return {"direction": direction, "thr": thr, "fee_bp": fee_bp,
            "touch": touch_filter, "trades": n, "conditions": nc,
            "roi": m, "ci_lo": lo, "ci_hi": hi,
            "median_capacity_usd": statistics.median(cap) if cap else 0.0,
            "total_capacity_usd": sum(cap)}


def show(res: list, title: str) -> None:
    print(f"\n--- {title} ---")
    print(f"{'DIR':<11}{'TOUCH':<7}{'THR':>7}{'FEE':>6}{'TRADES':>8}"
          f"{'CONDS':>7}{'NET_ROI':>10}{'95% CI':>24}")
    for r in res:
        ci = (f"[{r['ci_lo']:+.4f}, {r['ci_hi']:+.4f}]"
              if r["ci_lo"] is not None else "n/a")
        print(f"{r['direction']:<11}{r['touch']:<7}{r['thr']:>7.3f}"
              f"{r['fee_bp']:>6}{r['trades']:>8,}{r['conditions']:>7,}"
              f"{r['roi']:>+10.4f}{ci:>24}")
