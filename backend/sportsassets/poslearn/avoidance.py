"""3 · THE AVOIDANCE MODEL (SHADOW): when NOT to trade. Pure; no I/O.

LEARNED FROM WHERE PREDICTED EV HISTORICALLY FAILED. Over resolved training
opportunities the champion's approved rule WOULD have entered (gross edge >=
the approved threshold, net edge > 0), each segment of each dimension

    sport, league, market type, live/pregame, probability band,
    time-to-event, liquidity, spread, settlement family, regime,
    PinnAPI volatility, execution conditions (quote age), price movement

gets n, its mean PREDICTED net edge (p - c*) and its mean REALIZED net edge
(outcome - c*) with a standard error. A segment is

  AVOID    realized mean significantly below zero: the upper bound at the
           Bonferroni-adjusted z (over EVERY segment tested -- the number of
           hypotheses is recorded) is < 0;
  CAUTION  realized mean below zero, or realized significantly short of
           predicted (unadjusted 95%);
  OK       otherwise, with n >= MIN_SEGMENT_N;
  (too small segments are not judged).

A forward opportunity's output: AVOIDANCE_RISK = the largest, over its
judged segments, of P(segment mean realized net edge < 0) (normal
approximation), the level NORMAL / CAUTION / AVOID with NAMED reasons, or
UNMEASURED when none of its segments has enough history (absence of
evidence is not NORMAL).

SHADOW. Nothing reads the level to block anything; the forecast row CHECKs
applied = false. Forward measurement (losses avoided, good trades falsely
blocked, precision, recall, incremental economics) is computed over the
CHAMPION's forward entries, not a filtered set, and every figure is null with
a reason until it is measurable.
"""
from __future__ import annotations

import math

from . import common as C
from . import models as M

VERSION = "POSLEARN_AVOIDANCE_V1"
SUBJECT = "AVOIDANCE"
MIN_TRAIN = 100
MIN_SEGMENT_N = 30
MIN_SAMPLE = 100
ALPHA = 0.05

DIMENSIONS = ("sport", "league", "market", "live_state", "probability_band",
              "time_to_event", "liquidity", "spread", "settlement_family",
              "regime", "pinnapi_volatility", "execution_conditions",
              "price_movement")


def segment_values(opp: dict) -> dict:
    """dimension -> segment value of one opportunity (features as of t)."""
    f = opp.get("features") or {}
    tte = f.get("tte_h")
    move = f.get("price_move_1h")
    return {
        "sport": str(opp.get("sport") or "UNKNOWN"),
        "league": str(opp.get("league") or "UNKNOWN"),
        "market": str(opp.get("market") or "UNKNOWN"),
        "live_state": str(opp.get("live_state") or "UNKNOWN"),
        "probability_band": str(f.get("probability_band") or "UNKNOWN"),
        "time_to_event": ("UNKNOWN" if tte is None else "LIVE" if tte <= 0
                          else C.band(tte, (1, 6, 24),
                                      ("LT_1H", "1H_6H", "6H_24H", "GE_24H"))),
        "liquidity": C.band(f.get("liquidity_usd"), (100, 1000, 10000),
                            ("LT_100", "100_1K", "1K_10K", "GE_10K")),
        "spread": C.band(f.get("spread"), (0.0101, 0.0301, 0.0601),
                         ("LE_1C", "LE_3C", "LE_6C", "GT_6C")),
        "settlement_family": str(f.get("settlement_family") or "UNKNOWN"),
        "regime": str(f.get("regime") or "UNKNOWN"),
        "pinnapi_volatility": C.band(f.get("pinnapi_vol_1h"),
                                     (1e-9, 0.005, 0.02),
                                     ("STATIC", "LT_0.5PP", "LT_2PP",
                                      "GE_2PP")),
        "execution_conditions": C.band(f.get("quote_age_s"), (2, 10, 60),
                                       ("QUOTE_LT_2S", "QUOTE_LT_10S",
                                        "QUOTE_LT_60S", "QUOTE_GE_60S")),
        "price_movement": ("UNKNOWN" if move is None else
                           "FLAT" if abs(move) < 0.005 else
                           ("UP_" if move > 0 else "DOWN_")
                           + ("SMALL" if abs(move) < 0.02 else "LARGE")),
    }


def would_enter(r, threshold_pp) -> bool:
    action, _, _ = M.decision(C.num(r.get("p_reference")),
                              C.num(r.get("price")), C.num(r.get("fee")),
                              threshold_pp)
    return action == "ENTER"


def _cstar(r):
    return float(r["price"]) + float(r["fee"])


def train(rows, *, now, threshold_pp) -> dict:
    """rows: resolved records {p_reference, price, fee, o, features, sport,
    league, market, live_state, outcome_at}."""
    rows = [r for r in rows if r.get("outcome_at") is not None
            and r["outcome_at"] <= now and r.get("o") in (0, 1)
            and would_enter(r, threshold_pp)]
    if len(rows) < MIN_TRAIN:
        return {"trainable": False,
                "why": "INSUFFICIENT_CHAMPION_ENTRIES_N_%d_NEEDS_%d" % (
                    len(rows), MIN_TRAIN)}
    cells: dict = {}
    for r in rows:
        c = _cstar(r)
        for dim, val in segment_values(r).items():
            cells.setdefault(dim, {}).setdefault(val, []).append(
                (float(r["p_reference"]) - c, int(r["o"]) - c))
    tested = sum(1 for d in cells.values() for v in d.values()
                 if len(v) >= MIN_SEGMENT_N)
    z_adj = C.adjusted_z(ALPHA, max(1, tested), two_sided=False)
    table = {}
    for dim, vals in cells.items():
        for val, xs in vals.items():
            n = len(xs)
            pred = sum(x[0] for x in xs) / n
            real = [x[1] for x in xs]
            m = sum(real) / n
            sd = (math.sqrt(sum((x - m) ** 2 for x in real) / (n - 1))
                  if n > 1 else None)
            se = None if sd is None else sd / math.sqrt(n)
            short = [x[0] - x[1] for x in xs]
            ms = sum(short) / n
            sds = (math.sqrt(sum((x - ms) ** 2 for x in short) / (n - 1))
                   if n > 1 else None)
            ses = None if sds is None else sds / math.sqrt(n)
            if n < MIN_SEGMENT_N or se is None:
                flag = "NOT_JUDGED_TOO_SMALL"
            elif m + z_adj * se < 0:
                flag = "AVOID"
            elif m < 0 or (ses and ms - 1.959964 * ses > 0):
                flag = "CAUTION"
            else:
                flag = "OK"
            table.setdefault(dim, {})[val] = {
                "n": n, "mean_predicted_net_edge": C.rnd(pred, 8),
                "mean_realized_net_edge": C.rnd(m, 8),
                "se": C.rnd(se, 8), "flag": flag}
    return {"trainable": True, "n": len(rows),
            "params": {"segments": table, "segments_tested": tested,
                       "z_adjusted_one_sided": round(z_adj, 6),
                       "min_segment_n": MIN_SEGMENT_N,
                       "threshold_pp": threshold_pp},
            "window": {"start": min(r.get("decided_at") or r["outcome_at"]
                                    for r in rows) - 1.0,
                       "end": max(r["outcome_at"] for r in rows)}}


def document(trained, *, version) -> dict:
    return {
        "subject_id": SUBJECT, "version": version, "kind": "META_MODEL",
        "family": "META_MODELS_V1", "role": "META", "code_version": VERSION,
        "training_window": {"start": trained["window"]["start"],
                            "end": trained["window"]["end"],
                            "n": trained["n"]},
        "features": list(DIMENSIONS),
        "parameters": trained["params"],
        "validation_method": (
            "PROSPECTIVE FORWARD over the champion's forward entries: losses "
            "avoided, good trades falsely blocked, precision, recall and "
            "incremental economics of AVOID (and of AVOID+CAUTION)"),
        "minimum_sample": MIN_SAMPLE,
        "promotion_threshold": {
            "status": "NOT_PROMOTABLE_TO_A_BLOCK_FROM_THIS_LAYER",
            "why": "a block of production entries is an owner-approved "
                   "policy change; this model only reports"},
        "failure_threshold": {
            "rule": "forward incremental economics of AVOID < 0 with its "
                    "95% upper bound < 0 at minimum_sample -> reported "
                    "FAILED_FORWARD"},
        "hypothesis_family": "META_MODELS_V1", "family_size": 2,
        "multiple_testing": {"segments_tested": trained["params"][
            "segments_tested"], "adjustment": "BONFERRONI_ONE_SIDED"},
    }


def forecast(doc: dict, opp: dict) -> dict:
    table = doc["parameters"]["segments"]
    out = C.Out(action="SCORE")
    reasons, risks, judged = [], [], 0
    level = "UNMEASURED"
    rank = {"UNMEASURED": 0, "NORMAL": 1, "CAUTION": 2, "AVOID": 3}
    for dim, val in segment_values(opp).items():
        cell = (table.get(dim) or {}).get(val)
        if not cell or cell["flag"] == "NOT_JUDGED_TOO_SMALL":
            continue
        judged += 1
        m, se = cell["mean_realized_net_edge"], cell["se"]
        risks.append(C.norm_cdf(-m / se) if se else (1.0 if m < 0 else 0.0))
        mine = {"OK": "NORMAL"}.get(cell["flag"], cell["flag"])
        if rank[mine] > rank[level]:
            level = mine
        if cell["flag"] in ("AVOID", "CAUTION"):
            reasons.append(
                "%s=%s: %s -- realized %+.4f vs predicted %+.4f per "
                "contract (n=%d)" % (dim, val, cell["flag"], m,
                                     cell["mean_predicted_net_edge"],
                                     cell["n"]))
    if judged == 0:
        out.put("avoidance_risk", None,
                "NO_SEGMENT_OF_THIS_OPPORTUNITY_HAS_%d_HISTORICAL_ENTRIES"
                % MIN_SEGMENT_N)
        level = "UNMEASURED"
    else:
        out.put("avoidance_risk", C.rnd(max(risks), 8))
        if level == "UNMEASURED":
            level = "NORMAL"
    out["avoidance_level"] = level
    out["reasons"] = reasons
    out["output"] = {"segments_judged": judged}
    return out


def evaluate(rows: list, *, threshold_pp) -> dict:
    """rows: forward avoidance forecasts joined to RESOLVED outcomes and the
    opportunity's price/fee/p: {avoidance_level, p_reference, price, fee,
    o}. Measured over the CHAMPION's would-be entries only."""
    entries = [r for r in rows if would_enter(r, threshold_pp)]
    out = C.Out(champion_entries=len(entries), min_sample=MIN_SAMPLE)
    out["small_sample"] = len(entries) < MIN_SAMPLE
    for label, blocked_levels in (("AVOID", ("AVOID",)),
                                  ("AVOID_OR_CAUTION", ("AVOID", "CAUTION"))):
        blocked = [r for r in entries
                   if r.get("avoidance_level") in blocked_levels]
        losers = [r for r in entries if r["o"] == 0]
        b_los = [r for r in blocked if r["o"] == 0]
        b_win = [r for r in blocked if r["o"] == 1]
        s = C.Out(blocked=len(blocked), losing_entries=len(losers))
        none_b = "NO_ENTRY_WAS_BLOCKED"
        s.put("losses_avoided_per_contract",
              C.rnd(sum(_cstar(r) for r in b_los)) if blocked else None,
              none_b)
        s.put("good_trades_falsely_blocked", len(b_win) if blocked else None,
              none_b)
        s.put("gains_forgone_per_contract",
              C.rnd(sum(1.0 - _cstar(r) for r in b_win)) if blocked
              else None, none_b)
        s.put("precision", C.rnd(len(b_los) / len(blocked)) if blocked
              else None, none_b)
        s.put("recall", C.rnd(len(b_los) / len(losers)) if losers else None,
              "NO_LOSING_ENTRY_YET")
        bset = {id(r) for r in blocked}
        inc = [-(r["o"] - _cstar(r)) if id(r) in bset else 0.0
               for r in entries]
        m, lo, hi, n = C.mean_ci(inc, 1.959964)
        s.put("incremental_economics_per_contract_total",
              C.rnd(sum(inc)) if (entries and blocked) else None,
              none_b if entries else "NO_CHAMPION_ENTRY_RESOLVED")
        s.put("incremental_economics_per_entry_ci",
              None if (lo is None or not blocked) else [C.rnd(lo),
                                                        C.rnd(hi)],
              none_b if entries else "NO_CHAMPION_ENTRY_RESOLVED")
        out[label] = s
    return out
