#!/usr/bin/env python3
"""TRACK P: does BETTOR have a native predictive edge that survives out of
sample?

OFFLINE. Contacts nothing. Writes only under research/evidence/trackp/.

THE QUESTION, STATED SO IT CAN FAIL
At time T a token is buyable at ask `a`. It later settles to $1 or $0.
Gross expectancy per contract is  P(settles $1) - a.  If the market is
calibrated, that is zero everywhere and there is nothing to trade. So the
whole experiment is: IS THERE A PROSPECTIVELY IDENTIFIABLE REGION WHERE
THE SETTLEMENT RATE EXCEEDS THE ASK?

WHAT IS AND IS NOT A FEATURE
Features come only from the venue's book at probe_at: the ask, the ladder,
its depth. RN1's side, size, price, notional and reaction time are the
REASON the row exists and are never read -- asserted at load time, not
promised in a comment.

LEAKAGE
Every feature is observed at probe_at. The label is settlement. Rows whose
market had already resolved at probe_at are dropped. Nothing else about
the future is touched: no closing line, no later price, no result-derived
grouping.

SPLITS ARE CHRONOLOGICAL AND DECLARED HERE, BEFORE ANY RESULT
  TRAIN     .. 2026-08-20     fit the calibrators
  VALIDATION   08-21 .. 08-31 choose ONE model and ONE threshold
  HOLDOUT      09-01 ..       opened once, after the spec is hashed
A condition lands wholly in one split by its first probe, so the same game
never straddles a boundary.

INDEPENDENCE
RN1 probed the same token many times. The primary decision set keeps the
FIRST eligible probe per (condition, token): 112k rows collapse to far
fewer genuinely independent bets, and reporting the row count as the
sample size would overstate the evidence by an order of magnitude.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import random
from collections import Counter, defaultdict
from datetime import datetime

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SNAP = os.path.join(HERE, "snapshots")
OUT = os.path.join(HERE, "evidence", "trackp")
EVENTS = os.path.join(SNAP, "u2_events_v1.jsonl.gz")
SETTLE = os.path.join(SNAP, "settlement_v1.jsonl")

# --- declared before any result -----------------------------------------
TRAIN_END = "2026-08-20"
VALID_END = "2026-08-31"
EDGE_GRID = (0.005, 0.01, 0.02, 0.03, 0.04, 0.05, 0.075, 0.10)
PRICE_FLOOR, PRICE_CEIL = 0.02, 0.98      # a 0.00/1.00 quote is not a bet
MIN_TRAIN_BIN = 200                        # bins thinner than this do not
                                           # get their own calibration
SLIPPAGE_GRID = (0.0, 0.01, 0.02, 0.03)
BOOTSTRAP = 2000
SEED = 20260915

# PMUS taker fee, the only VERIFIED schedule we hold (effective
# 2026-07-01). The prices here come from the Polymarket CLOB, whose own
# schedule we have not verified, so this is applied as a CROSS-VENUE COST
# ASSUMPTION and labelled as one -- never as the observed venue's fee.
PMUS_THETA_TAKER = 0.06

RN1_BANNED = ("side", "size", "price", "notional", "his_price", "his_size",
              "his_notional", "reaction_s", "trade_id", "detected_at",
              "source")


def ts(x):
    if not x:
        return None
    try:
        return datetime.fromisoformat(x.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def winner_index(s):
    try:
        vals = [float(x) for x in (s.get("payouts") or [])]
    except (TypeError, ValueError):
        return None
    ones = [i for i, v in enumerate(vals) if v == 1.0]
    zeros = [i for i, v in enumerate(vals) if v == 0.0]
    if len(ones) == 1 and len(ones) + len(zeros) == len(vals):
        return ones[0]
    return None


# ============================================================ decisions ==
def build_decisions():
    """One row per (condition, token): the FIRST probe that is eligible.

    Eligibility is decided only from things knowable at probe_at plus the
    requirement that the label lies in the future.
    """
    settle = {}
    with open(SETTLE) as fh:
        for line in fh:
            if line.strip():
                r = json.loads(line)
                settle[r["condition_id"]] = r

    reject = Counter()
    best = {}
    for line in gzip.open(EVENTS, "rt"):
        r = json.loads(line)
        cid = r.get("condition_id_effective") or r.get("condition_id")
        s = settle.get(cid) if cid else None
        if s is None:
            reject["NO_SETTLEMENT_ROW"] += 1
            continue
        if not s.get("resolved"):
            reject["UNRESOLVED"] += 1
            continue
        wi = winner_index(s)
        if wi is None:
            reject["LABEL_NOT_ONE_HOT"] += 1
            continue
        p = ts(r.get("probe_at"))
        ra = ts(s.get("resolved_at"))
        if p is None or ra is None:
            reject["TIMESTAMP_MISSING"] += 1
            continue
        if ra <= p:
            reject["ALREADY_RESOLVED_AT_PROBE"] += 1
            continue
        if not r.get("book_ok"):
            reject["BOOK_NOT_OK"] += 1
            continue
        try:
            ask = float(r.get("best_ask"))
        except (TypeError, ValueError):
            reject["ASK_NOT_NUMERIC"] += 1
            continue
        if not (PRICE_FLOOR <= ask <= PRICE_CEIL):
            reject["ASK_OUTSIDE_TRADABLE_RANGE"] += 1
            continue
        oi = r.get("outcome_index")
        if oi is None:
            reject["NO_OUTCOME_INDEX"] += 1
            continue
        # top-of-book size, from the ladder, for capacity only
        depth = r.get("depth") or []
        try:
            top_sz = float(depth[0][1]) if depth else None
            top_px = float(depth[0][0]) if depth else None
        except (TypeError, ValueError, IndexError):
            top_sz = top_px = None
        if top_px is not None and abs(top_px - ask) > 1e-9:
            reject["LADDER_TOP_DISAGREES_WITH_BEST_ASK"] += 1
            continue

        key = (cid, r.get("asset"))
        prev = best.get(key)
        if prev is not None and prev["probe_at"] <= p:
            continue
        best[key] = {
            "condition_id": cid,
            "asset": r.get("asset"),
            "outcome_index": oi,
            "sport": r.get("sport") or "NOT_IDENTIFIED",
            "market_slug": r.get("market_slug"),
            "probe_at": p,
            "resolved_at": ra,
            "ask": ask,
            "top_size": top_sz,
            "ladder": [[float(a), float(b)] for a, b in depth
                       if a is not None],
            "y": 1 if oi == winner_index(s) else 0,
        }
    rows = sorted(best.values(), key=lambda d: d["probe_at"])
    return rows, reject


def split_of(rows):
    """A condition belongs wholly to the split of its FIRST probe."""
    first = {}
    for r in rows:
        c = r["condition_id"]
        if c not in first or r["probe_at"] < first[c]:
            first[c] = r["probe_at"]
    out = {}
    for c, p in first.items():
        d = p.date().isoformat()
        out[c] = ("TRAIN" if d <= TRAIN_END else
                  "VALIDATION" if d <= VALID_END else "HOLDOUT")
    return out


# ========================================================= calibrators ==
def pav(x, y, w):
    """Pool-adjacent-violators: the monotone fit, with no tuning knob to
    be tempted by."""
    xs = np.asarray(x, float)
    order = np.argsort(xs, kind="mergesort")
    yy = np.asarray(y, float)[order]
    ww = np.asarray(w, float)[order]
    xx = xs[order]
    val, wt, idx = [], [], []
    for i in range(len(yy)):
        val.append(yy[i])
        wt.append(ww[i])
        idx.append(i)
        while len(val) > 1 and val[-2] > val[-1]:
            v2, w2 = val.pop(), wt.pop()
            v1, w1 = val.pop(), wt.pop()
            val.append((v1 * w1 + v2 * w2) / (w1 + w2))
            wt.append(w1 + w2)
            idx.pop()
    fit = np.empty(len(yy))
    pos = 0
    for v, w_ in zip(val, wt):
        k = 0
        acc = 0.0
        while pos + k < len(yy) and acc < w_ - 1e-9:
            acc += ww[pos + k]
            k += 1
        fit[pos:pos + k] = v
        pos += k
    return xx, fit


class Isotonic:
    NAME = "D_ISOTONIC_ON_ASK"

    def fit(self, rows):
        a = np.array([r["ask"] for r in rows])
        y = np.array([r["y"] for r in rows], float)
        self.x, self.f = pav(a, y, np.ones_like(y))
        return self

    def __call__(self, ask):
        return float(np.interp(ask, self.x, self.f))


class Logistic:
    NAME = "C_LOGISTIC_ON_LOGIT_ASK"

    def fit(self, rows):
        from scipy.optimize import minimize
        a = np.clip(np.array([r["ask"] for r in rows]), 1e-6, 1 - 1e-6)
        z = np.log(a / (1 - a))
        y = np.array([r["y"] for r in rows], float)

        def nll(p):
            t = np.clip(p[0] + p[1] * z, -30, 30)
            q = 1.0 / (1.0 + np.exp(-t))
            q = np.clip(q, 1e-9, 1 - 1e-9)
            return -np.sum(y * np.log(q) + (1 - y) * np.log(1 - q))

        self.p = minimize(nll, np.array([0.0, 1.0]), method="Nelder-Mead",
                          options={"maxiter": 4000, "xatol": 1e-8,
                                   "fatol": 1e-8}).x
        return self

    def __call__(self, ask):
        a = min(max(ask, 1e-6), 1 - 1e-6)
        z = math.log(a / (1 - a))
        return 1.0 / (1.0 + math.exp(-max(-30, min(30, self.p[0]
                                                   + self.p[1] * z))))


class Market:
    NAME = "A_MARKET_ONLY"

    def fit(self, rows):
        return self

    def __call__(self, ask):
        return ask


class SportIsotonic:
    """Per-sport isotonic, falling back to the pooled fit wherever TRAIN is
    too thin to earn its own curve. The fallback is a rule, not a decision
    made after seeing which sports looked good."""
    NAME = "E_ISOTONIC_BY_SPORT"

    def fit(self, rows):
        self.pool = Isotonic().fit(rows)
        by = defaultdict(list)
        for r in rows:
            by[r["sport"]].append(r)
        self.per = {s: Isotonic().fit(v) for s, v in by.items()
                    if len(v) >= MIN_TRAIN_BIN}
        return self

    def __call__(self, ask, sport=None):
        m = self.per.get(sport)
        return m(ask) if m else self.pool(ask)


def predict(model, r):
    try:
        return model(r["ask"], r["sport"])
    except TypeError:
        return model(r["ask"])


# ============================================================ economics ==
def fee_per_contract(p):
    """PMUS's VERIFIED taker coefficient applied to a Polymarket price.
    Cross-venue assumption, labelled everywhere it is reported."""
    return PMUS_THETA_TAKER * p * (1.0 - p)


def simulate(rows, model, thresh, slippage=0.0, fees=True):
    """One contract per qualifying decision, held to settlement.

    No stop, no target, no cash-out, no hedge: if the ENTRY has no edge,
    an exit rule can only hide that.
    """
    trades = []
    for r in rows:
        ask = r["ask"] + slippage
        if ask >= 1.0 or ask < PRICE_FLOOR:
            continue
        fair = predict(model, r)
        edge = fair - ask
        if edge < thresh:
            continue
        cost = ask + (fee_per_contract(ask) if fees else 0.0)
        trades.append({
            "condition_id": r["condition_id"], "sport": r["sport"],
            "slug": r["market_slug"], "probe_at": r["probe_at"],
            "resolved_at": r["resolved_at"], "ask": ask, "fair": fair,
            "edge": edge, "cost": cost, "y": r["y"],
            "pnl": (1.0 if r["y"] else 0.0) - cost,
            "gross": (1.0 if r["y"] else 0.0) - ask,
            "top_size": r["top_size"],
        })
    return trades


def summarize(trades, label):
    if not trades:
        # every key the caller reads, so an empty cell prints as a blank
        # line rather than exploding -- a grid cell with no trades is a
        # RESULT (the threshold admitted nothing), not an error.
        return {"LABEL": label, "NUMBER_OF_TRADES": 0,
                "NUMBER_OF_INDEPENDENT_MARKETS": 0, "CAPITAL_DEPLOYED": 0.0,
                "GROSS_PNL": 0.0, "NET_PNL": 0.0,
                "ROI_ON_DEPLOYED_CAPITAL": None, "GROSS_ROI": None,
                "WIN_RATE": None, "AVERAGE_EXPECTED_EDGE_AT_ENTRY": None,
                "AVERAGE_REALIZED_RETURN": None, "MAX_DRAWDOWN": None,
                "WORST_DAY": None, "WORST_WEEK": None, "WORST_MONTH": None,
                "POSITIVE_MONTHS": 0, "NEGATIVE_MONTHS": 0, "MONTHS": {}}
    cap = sum(t["cost"] for t in trades)
    net = sum(t["pnl"] for t in trades)
    gross = sum(t["gross"] for t in trades)
    byres = sorted(trades, key=lambda t: t["resolved_at"])
    eq, peak, dd = 0.0, 0.0, 0.0
    for t in byres:
        eq += t["pnl"]
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    day, week, month = Counter(), Counter(), Counter()
    for t in byres:
        d = t["resolved_at"]
        day[d.date().isoformat()] += t["pnl"]
        week[d.strftime("%G-W%V")] += t["pnl"]
        month[d.strftime("%Y-%m")] += t["pnl"]
    return {
        "LABEL": label,
        "NUMBER_OF_TRADES": len(trades),
        "NUMBER_OF_INDEPENDENT_MARKETS": len({t["condition_id"]
                                              for t in trades}),
        "CAPITAL_DEPLOYED": cap,
        "GROSS_PNL": gross,
        "NET_PNL": net,
        "ROI_ON_DEPLOYED_CAPITAL": net / cap if cap else None,
        "GROSS_ROI": gross / sum(t["ask"] for t in trades),
        "WIN_RATE": sum(t["y"] for t in trades) / len(trades),
        "AVERAGE_EXPECTED_EDGE_AT_ENTRY": sum(t["edge"] for t in trades)
                                          / len(trades),
        "AVERAGE_REALIZED_RETURN": net / len(trades),
        "MAX_DRAWDOWN": dd,
        "WORST_DAY": min(day.values()) if day else None,
        "WORST_WEEK": min(week.values()) if week else None,
        "WORST_MONTH": min(month.values()) if month else None,
        "POSITIVE_MONTHS": sum(1 for v in month.values() if v > 0),
        "NEGATIVE_MONTHS": sum(1 for v in month.values() if v <= 0),
        "MONTHS": dict(month),
    }


def prob_metrics(rows, model):
    if not rows:
        return {}
    p = np.array([min(max(predict(model, r), 1e-9), 1 - 1e-9)
                  for r in rows])
    y = np.array([r["y"] for r in rows], float)
    brier = float(np.mean((p - y) ** 2))
    ll = float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))
    edges = np.linspace(0, 1, 11)
    ece, tot = 0.0, len(rows)
    for i in range(10):
        m = (p >= edges[i]) & (p < edges[i + 1] if i < 9 else p <= 1.0)
        if m.sum():
            ece += (m.sum() / tot) * abs(y[m].mean() - p[m].mean())
    return {"BRIER": brier, "LOG_LOSS": ll, "CALIBRATION_ERROR_ECE": ece}


def bootstrap_ci(trades, n=BOOTSTRAP, seed=SEED):
    """Resample by MARKET, not by trade: two tokens of one game settle
    together, so resampling rows would pretend they are independent."""
    if not trades:
        return (None, None)
    by = defaultdict(list)
    for t in trades:
        by[t["condition_id"]].append(t)
    keys = list(by)
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        pick = [by[keys[rng.randrange(len(keys))]] for _ in range(len(keys))]
        flat = [t for g in pick for t in g]
        cap = sum(t["cost"] for t in flat)
        out.append(sum(t["pnl"] for t in flat) / cap if cap else 0.0)
    out.sort()
    return (out[int(0.025 * n)], out[int(0.975 * n)])


def concentration(trades):
    if not trades:
        return {}
    net = sum(t["pnl"] for t in trades)
    s = sorted(trades, key=lambda t: -t["pnl"])

    def share(k):
        return (sum(t["pnl"] for t in s[:k]) / net) if net else None

    def group(key):
        g = Counter()
        for t in trades:
            g[t[key]] += t["pnl"]
        top, val = (g.most_common(1)[0] if g else (None, 0.0))
        return {"TOP": top, "SHARE": (val / net) if net else None}

    mon = Counter()
    for t in trades:
        mon[t["resolved_at"].strftime("%Y-%m")] += t["pnl"]
    best_month = mon.most_common(1)[0][0] if mon else None

    def rerun(excl_trades=0, excl_month=None):
        keep = s[excl_trades:] if excl_trades else list(s)
        if excl_month:
            keep = [t for t in keep
                    if t["resolved_at"].strftime("%Y-%m") != excl_month]
        cap = sum(t["cost"] for t in keep)
        return {"TRADES": len(keep), "NET_PNL": sum(t["pnl"] for t in keep),
                "ROI": (sum(t["pnl"] for t in keep) / cap) if cap else None}

    return {
        "TOP_1_TRADE_SHARE": share(1), "TOP_5_TRADE_SHARE": share(5),
        "TOP_10_TRADE_SHARE": share(10),
        "TOP_SPORT": group("sport"), "TOP_MONTH": best_month,
        "EXCLUDING_BEST_TRADE": rerun(1),
        "EXCLUDING_BEST_5_TRADES": rerun(5),
        "EXCLUDING_BEST_MONTH": rerun(0, best_month),
    }


# =============================================================== driver ==
def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    lines = []

    def say(m=""):
        lines.append(m)
        print(m)

    # the banned-column assertion is executed, not promised
    src = open(__file__).read()
    body = src.split("RN1_BANNED = ")[1].split("\n\n")[1]
    for bad in RN1_BANNED:
        assert 'r["%s"]' % bad not in body and 'r.get("%s")' % bad not in body, bad

    say("=" * 72)
    say("TRACK P -- NATIVE PREDICTIVE PROFITABILITY")
    say("=" * 72)
    say()
    rows, reject = build_decisions()
    say("--- DECISION SET (first eligible probe per condition+token) ---")
    say("  DECISIONS                  %d" % len(rows))
    say("  INDEPENDENT MARKETS        %d" % len({r["condition_id"]
                                                 for r in rows}))
    for w, c in reject.most_common():
        say("  rejected %-34s %d" % (w, c))
    say()

    sp = split_of(rows)
    parts = defaultdict(list)
    for r in rows:
        parts[sp[r["condition_id"]]].append(r)
    say("--- CHRONOLOGICAL SPLITS (declared before any result) ---")
    for k in ("TRAIN", "VALIDATION", "HOLDOUT"):
        v = parts[k]
        if not v:
            say("  %-11s EMPTY" % k)
            continue
        say("  %-11s %6d decisions  %5d markets  %s .. %s  base rate %.4f"
            % (k, len(v), len({r["condition_id"] for r in v}),
               min(r["probe_at"] for r in v).date(),
               max(r["probe_at"] for r in v).date(),
               sum(r["y"] for r in v) / len(v)))
    say()

    train, valid, hold = parts["TRAIN"], parts["VALIDATION"], parts["HOLDOUT"]

    # ---- THE FIRST QUESTION: is the ask miscalibrated on TRAIN? --------
    say("--- Q1: IS THE ASK MISCALIBRATED?  (TRAIN only) ---")
    say("  %-16s %7s %8s %9s %10s" % ("ASK BAND", "N", "MEAN_ASK",
                                      "SETTLE", "SETTLE-ASK"))
    bands = [(0.02, 0.10), (0.10, 0.20), (0.20, 0.30), (0.30, 0.40),
             (0.40, 0.50), (0.50, 0.60), (0.60, 0.70), (0.70, 0.80),
             (0.80, 0.90), (0.90, 0.98)]
    for lo, hi in bands:
        b = [r for r in train if lo <= r["ask"] < hi]
        if not b:
            continue
        ma = sum(r["ask"] for r in b) / len(b)
        sr = sum(r["y"] for r in b) / len(b)
        say("  [%.2f,%.2f)      %7d %8.4f %9.4f %+10.4f"
            % (lo, hi, len(b), ma, sr, sr - ma))
    say()

    models = [Market().fit(train), Logistic().fit(train),
              Isotonic().fit(train), SportIsotonic().fit(train)]

    # ---- VALIDATION: choose ONE model and ONE threshold ---------------
    say("--- VALIDATION GRID (model x threshold, net of the cross-venue "
        "fee assumption) ---")
    say("  %-26s %6s %7s %7s %9s %9s" % ("MODEL", "THR", "TRADES",
                                         "MKTS", "NET_PNL", "ROI"))
    grid = []
    for m in models:
        for th in EDGE_GRID:
            t = simulate(valid, m, th)
            s = summarize(t, "%s@%.3f" % (m.NAME, th))
            grid.append((m, th, s))
            say("  %-26s %6.3f %7d %7d %9.2f %9s"
                % (m.NAME, th, s["NUMBER_OF_TRADES"],
                   s["NUMBER_OF_INDEPENDENT_MARKETS"], s["NET_PNL"],
                   ("%.4f" % s["ROI_ON_DEPLOYED_CAPITAL"])
                   if s["ROI_ON_DEPLOYED_CAPITAL"] is not None else "-"))
    n_hyp = len(grid)
    say()
    say("  NUMBER_OF_HYPOTHESES_TESTED = %d "
        "(%d models x %d thresholds), all shown above"
        % (n_hyp, len(models), len(EDGE_GRID)))
    say()

    # selection rule, fixed in advance: best VALIDATION ROI among cells
    # with at least 200 independent markets, so a 3-bet fluke cannot win.
    MIN_MKTS = 200
    eligible = [g for g in grid
                if g[2]["NUMBER_OF_INDEPENDENT_MARKETS"] >= MIN_MKTS
                and g[2]["ROI_ON_DEPLOYED_CAPITAL"] is not None]
    say("  selection rule: highest VALIDATION ROI among cells with >= %d "
        "independent markets" % MIN_MKTS)
    if not eligible:
        say("  NO CELL QUALIFIES -- nothing to freeze")
        chosen = None
    else:
        chosen = max(eligible, key=lambda g: g[2]["ROI_ON_DEPLOYED_CAPITAL"])
        say("  CHOSEN: %s @ %.3f  (validation ROI %.4f on %d markets)"
            % (chosen[0].NAME, chosen[1],
               chosen[2]["ROI_ON_DEPLOYED_CAPITAL"],
               chosen[2]["NUMBER_OF_INDEPENDENT_MARKETS"]))
    say()

    if chosen is None:
        say("GATE = P-C -- NO VALIDATED PREDICTIVE EDGE "
            "(no cell survived the validation floor)")
        open(os.path.join(OUT, "trackp_report.txt"), "w").write(
            "\n".join(lines) + "\n")
        return 0

    model, thresh, vsum = chosen

    # ---- FREEZE AND HASH BEFORE THE HOLDOUT IS TOUCHED ----------------
    spec = {
        "STRATEGY": "TRACKP-NATIVE-1",
        "ELIGIBLE_SPORTS": "ALL (no sport filter)",
        "ELIGIBLE_LEAGUES": "NOT_IDENTIFIED (no league field in data)",
        "ELIGIBLE_MARKET_TYPES": "NOT_IDENTIFIED (no market-type field)",
        "PREGAME_LIVE_RULE": "NOT_IDENTIFIED (no start time, no live flag)",
        "PRICE_RANGE": [PRICE_FLOOR, PRICE_CEIL],
        "MODEL_VERSION": model.NAME,
        "FEATURE_SET": ["best_ask@probe_at", "sport"]
                        if "SPORT" in model.NAME else ["best_ask@probe_at"],
        "ENTRY_THRESHOLD": thresh,
        "ORDER_ASSUMPTION": "buy 1 contract at the observed best ask "
                            "(taker), top-of-book only",
        "FEE_MODEL": "PMUS verified taker 0.06*p*(1-p) per contract, "
                     "APPLIED CROSS-VENUE to Polymarket prices",
        "SLIPPAGE_MODEL": "0 in the primary result; +1c/+2c/+3c reported "
                          "as robustness",
        "POSITION_SIZE": "1 contract per decision (normalized fixed risk)",
        "EXIT_RULE": "HOLD TO SETTLEMENT. No stop, target, cash-out or "
                     "hedge.",
        "MAXIMUM_EXPOSURE": "not modelled; capacity reported separately",
        "EXCLUSION_RULES": ["condition already resolved at probe_at",
                            "book_ok false", "ask outside [%.2f,%.2f]"
                            % (PRICE_FLOOR, PRICE_CEIL),
                            "ladder top disagrees with best_ask",
                            "payout vector not one-hot",
                            "only the FIRST probe per condition+token"],
        "TRAIN_END": TRAIN_END, "VALID_END": VALID_END,
        "HYPOTHESES_TESTED_BEFORE_FREEZE": n_hyp,
    }
    blob = json.dumps(spec, indent=1, sort_keys=True, default=str)
    spec_hash = hashlib.sha256(blob.encode()).hexdigest()
    open(os.path.join(OUT, "trackp_frozen_spec.json"), "w").write(blob)
    say("--- FROZEN STRATEGY SPECIFICATION ---")
    for k, v in sorted(spec.items()):
        say("  %-34s %s" % (k, v))
    say("  SPEC_SHA256                        %s" % spec_hash)
    say()
    say("  >>> HOLDOUT OPENED ONCE, AFTER THIS HASH <<<")
    say()

    results = {}
    for name, part in (("TRAIN", train), ("VALIDATION", valid),
                       ("HOLDOUT", hold)):
        t = simulate(part, model, thresh)
        s = summarize(t, name)
        s.update(prob_metrics(part, model))
        lo, hi = bootstrap_ci(t)
        s["ROI_CI95"] = [lo, hi]
        s["CONCENTRATION"] = concentration(t)
        sizes = [x["top_size"] for x in t if x["top_size"]]
        s["MEDIAN_TOP_OF_BOOK_SIZE"] = (sorted(sizes)[len(sizes) // 2]
                                        if sizes else None)
        s["TOTAL_TOP_OF_BOOK_SIZE"] = sum(sizes) if sizes else None
        results[name] = (s, t)

    say("--- PRIMARY PROFITABILITY REPORT ---")
    keys = ["NUMBER_OF_TRADES", "NUMBER_OF_INDEPENDENT_MARKETS",
            "CAPITAL_DEPLOYED", "GROSS_PNL", "NET_PNL",
            "ROI_ON_DEPLOYED_CAPITAL", "GROSS_ROI", "WIN_RATE",
            "AVERAGE_EXPECTED_EDGE_AT_ENTRY", "AVERAGE_REALIZED_RETURN",
            "MAX_DRAWDOWN", "WORST_DAY", "WORST_WEEK", "WORST_MONTH",
            "POSITIVE_MONTHS", "NEGATIVE_MONTHS", "BRIER", "LOG_LOSS",
            "CALIBRATION_ERROR_ECE"]
    say("  %-34s %14s %14s %14s" % ("", "TRAIN", "VALIDATION", "HOLDOUT"))
    for k in keys:
        vals = []
        for nm in ("TRAIN", "VALIDATION", "HOLDOUT"):
            v = results[nm][0].get(k)
            vals.append("-" if v is None else
                        ("%14.4f" % v if isinstance(v, float)
                         else "%14s" % v))
        say("  %-34s %s" % (k, " ".join(
            x if x.startswith(" ") else "%14s" % x for x in vals)))
    for nm in ("TRAIN", "VALIDATION", "HOLDOUT"):
        ci = results[nm][0]["ROI_CI95"]
        say("  ROI_CI95 %-25s %s" % (nm, "[%.4f, %.4f]" % (ci[0], ci[1])
                                     if ci[0] is not None else "-"))
    say()

    hs, ht = results["HOLDOUT"]
    say("--- HOLDOUT PROFIT CONCENTRATION ---")
    c = hs["CONCENTRATION"]
    for k in ("TOP_1_TRADE_SHARE", "TOP_5_TRADE_SHARE",
              "TOP_10_TRADE_SHARE"):
        v = c.get(k)
        say("  %-28s %s" % (k, "-" if v is None else "%.4f" % v))
    say("  TOP_SPORT                    %s" % c.get("TOP_SPORT"))
    say("  TOP_MONTH                    %s" % c.get("TOP_MONTH"))
    for k in ("EXCLUDING_BEST_TRADE", "EXCLUDING_BEST_5_TRADES",
              "EXCLUDING_BEST_MONTH"):
        v = c.get(k) or {}
        say("  %-28s trades=%s net=%s roi=%s"
            % (k, v.get("TRADES"),
               "-" if v.get("NET_PNL") is None else "%.2f" % v["NET_PNL"],
               "-" if v.get("ROI") is None else "%.4f" % v["ROI"]))
    say()

    say("--- HOLDOUT ROBUSTNESS (worse execution; strategy NOT redesigned) ---")
    say("  %-22s %7s %10s %9s" % ("DEGRADATION", "TRADES", "NET_PNL", "ROI"))
    for slip in SLIPPAGE_GRID:
        t = simulate(hold, model, thresh, slippage=slip)
        s = summarize(t, "slip+%.2f" % slip)
        say("  ask +%.2f              %7d %10.2f %9s"
            % (slip, s["NUMBER_OF_TRADES"], s["NET_PNL"],
               ("%.4f" % s["ROI_ON_DEPLOYED_CAPITAL"])
               if s["ROI_ON_DEPLOYED_CAPITAL"] is not None else "-"))
    t0 = simulate(hold, model, thresh, fees=False)
    s0 = summarize(t0, "no fee")
    say("  no fee (gross only)    %7d %10.2f %9s"
        % (s0["NUMBER_OF_TRADES"], s0["NET_PNL"],
           ("%.4f" % s0["ROI_ON_DEPLOYED_CAPITAL"])
           if s0["ROI_ON_DEPLOYED_CAPITAL"] is not None else "-"))
    say()

    say("--- CAPACITY ---")
    say("  MEDIAN_TOP_OF_BOOK_SIZE (holdout trades)  %s"
        % hs["MEDIAN_TOP_OF_BOOK_SIZE"])
    say("  TOTAL_TOP_OF_BOOK_SIZE                    %s"
        % hs["TOTAL_TOP_OF_BOOK_SIZE"])
    say("  CAPACITY = PARTIAL -- top-of-book size only, one snapshot per")
    say("  market, on a population selected by RN1's flow. Not a capacity")
    say("  estimate for a native strategy on the whole board.")
    say()

    say("--- PER $ NORMALIZATION (holdout) ---")
    cap = hs["CAPITAL_DEPLOYED"]
    say("  per $1 deployed           %s"
        % ("-" if not cap else "%+.6f" % (hs["NET_PNL"] / cap)))
    say("  per $100 deployed         %s"
        % ("-" if not cap else "%+.4f" % (100.0 * hs["NET_PNL"] / cap)))
    say("  per 1,000 decisions       %s"
        % ("-" if not hs["NUMBER_OF_TRADES"] else
           "%+.4f" % (1000.0 * hs["NET_PNL"] / hs["NUMBER_OF_TRADES"])))
    say()

    json.dump({k: v[0] for k, v in results.items()},
              open(os.path.join(OUT, "trackp_results.json"), "w"),
              indent=1, default=str)
    open(os.path.join(OUT, "trackp_report.txt"), "w").write(
        "\n".join(lines) + "\n")
    say("SPEC_SHA256 %s" % spec_hash)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
