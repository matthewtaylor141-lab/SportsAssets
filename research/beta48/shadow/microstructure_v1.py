"""Sections 12 and 13. MICROSTRUCTURE_V1 and the continuous relative-value target.

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.

WHY THIS DOES NOT WAIT FOR SETTLEMENTS
--------------------------------------
Every settlement-based experiment in this programme has been starved of events:
47 common events against an incremental ladder needing thousands. Short-horizon
price movement does not have that problem. A ninety-minute capture at one
observation every four seconds across six markets yields thousands of
observations of MID_MOVE_30S, and each one is a real measurement rather than
one binary outcome per fixture.

That does not make them independent -- consecutive observations on one market
overlap heavily, and the event-clustered machinery still applies -- but it does
mean the microstructure question can be answered on one capture where the
settlement question needs a season.

THE TARGETS ARE PRICE MOVES, NOT OUTCOMES
-----------------------------------------
    MID_MOVE_5S / 30S / 60S / 300S
and, where the book supports it, the move in the EXECUTABLE price, which is the
one a passive order actually earns or loses against. Mid is easier and
executable is truer; both are emitted and neither is called the other.

WHAT THIS MODULE IS NOT
-----------------------
It is not P_FILL. Nothing here identifies whether a passive order would have
been filled, and no feature here may be used as a fill proxy (section 14). A
mid that moves through your level is evidence about the market, not about your
queue position.
"""

import math
from collections import defaultdict, deque

NOT_IDENTIFIED = "NOT_IDENTIFIED"
NO_CAPTURE_YET = "NO_SUBSTANTIVE_CAPTURE_HARVESTED_YET"

# --- Section 12. Targets and features. -------------------------------------

TARGET_HORIZONS_SECONDS = (5, 30, 60, 300)

TARGETS = tuple("MID_MOVE_%dS" % s for s in TARGET_HORIZONS_SECONDS) + \
    tuple("EXECUTABLE_MOVE_%dS" % s for s in TARGET_HORIZONS_SECONDS)

CANDIDATE_FEATURES = (
    "ORDER_BOOK_IMBALANCE",
    "ORDER_FLOW_IMBALANCE",
    "MICROPRICE_MINUS_MID",
    "SPREAD",
    "TOUCH_DEPTH",
    "DEPTH_SLOPE",
    "TRADE_FLOW",
    "TRANSITION_FREQUENCY",
    "PRICE_IMPROVEMENT",
    "MOVE_THROUGH",
    "SHORT_HORIZON_VOLATILITY",
    "CROSS_MARKET_RESIDUAL",
)

EVALUATION_RULES = {
    "SPLIT": "CHRONOLOGICAL",
    "CLUSTERING": "EVENT_CLUSTERED",
    "WHY_BOTH": (
        "consecutive observations on one market overlap, so rows are not "
        "independent; and a random split would let the fit learn the session "
        "and call it skill"),
    "OVERLAPPING_TARGETS_ARE_DECLARED": (
        "MID_MOVE_300S windows overlap heavily at a 4-second sampling "
        "interval. The overlap is not removed -- it is declared, and the "
        "interval is event-clustered so it is not mistaken for independent "
        "evidence"),
}

THIS_IS_NOT_P_FILL = True
NO_FEATURE_HERE_MAY_BE_USED_AS_A_FILL_PROXY = (
    "a mid moving through a level is evidence about the market, not about "
    "whether BETTOR's passive order was ahead of it in the queue")


def _mid(bid, ask):
    if bid is None or ask is None:
        return None
    return 0.5 * (float(bid) + float(ask))


def _microprice(bid, ask, bid_size, ask_size):
    """Size-weighted touch price. Leans toward the side with less size."""
    if None in (bid, ask, bid_size, ask_size):
        return None
    b, a = float(bid_size), float(ask_size)
    if b + a <= 0:
        return None
    return (float(bid) * a + float(ask) * b) / (a + b)


def tick_features(tick, prev=None, flow=None):
    """Features from one book observation. Missing inputs give None.

    `prev` is the preceding tick for the same market; `flow` is a small window
    of recent ticks used for the rate-like features. Nothing is imputed: a
    feature whose inputs are absent is None, and a None is never a zero.
    """
    bid, ask = tick.get("BEST_BID"), tick.get("BEST_ASK")
    bs, as_ = tick.get("BID_SIZE"), tick.get("ASK_SIZE")
    mid = _mid(bid, ask)
    micro = _microprice(bid, ask, bs, as_)
    f = {k: None for k in CANDIDATE_FEATURES}
    if bs is not None and as_ is not None and (bs + as_) > 0:
        f["ORDER_BOOK_IMBALANCE"] = (float(bs) - float(as_)) / (float(bs) + float(as_))
    if micro is not None and mid is not None:
        f["MICROPRICE_MINUS_MID"] = micro - mid
    if bid is not None and ask is not None:
        f["SPREAD"] = float(ask) - float(bid)
    if bs is not None and as_ is not None:
        f["TOUCH_DEPTH"] = float(bs) + float(as_)
    levels = tick.get("LEVELS")
    if levels and len(levels) >= 2:
        try:
            d0, d1 = levels[0], levels[1]
            dp = abs(float(d1["PRICE"]) - float(d0["PRICE"]))
            if dp > 0:
                f["DEPTH_SLOPE"] = (float(d1["SIZE"]) - float(d0["SIZE"])) / dp
        except Exception:
            pass
    if prev:
        pm = _mid(prev.get("BEST_BID"), prev.get("BEST_ASK"))
        if mid is not None and pm is not None:
            f["MOVE_THROUGH"] = mid - pm
        pb, pa = prev.get("BEST_BID"), prev.get("BEST_ASK")
        if bid is not None and pb is not None and ask is not None and pa is not None:
            f["PRICE_IMPROVEMENT"] = (float(bid) - float(pb)) + (float(pa) - float(ask))
    if flow:
        mids = [_mid(t.get("BEST_BID"), t.get("BEST_ASK")) for t in flow]
        mids = [m for m in mids if m is not None]
        if len(mids) > 2:
            mu = sum(mids) / len(mids)
            f["SHORT_HORIZON_VOLATILITY"] = math.sqrt(
                sum((m - mu) ** 2 for m in mids) / (len(mids) - 1))
            changes = sum(1 for a, b in zip(mids, mids[1:]) if a != b)
            f["TRANSITION_FREQUENCY"] = changes / max(len(mids) - 1, 1)
        traded = [t.get("SHARES_TRADED") for t in flow
                  if t.get("SHARES_TRADED") is not None]
        if len(traded) > 1:
            f["TRADE_FLOW"] = float(traded[-1]) - float(traded[0])
        sizes = [(t.get("BID_SIZE"), t.get("ASK_SIZE")) for t in flow]
        deltas = [((b or 0) - (pb or 0)) - ((a or 0) - (pa or 0))
                  for (pb, pa), (b, a) in zip(sizes, sizes[1:])]
        if deltas:
            f["ORDER_FLOW_IMBALANCE"] = sum(deltas)
    f["_MID"] = mid
    f["_MICROPRICE"] = micro
    return f


def build_targets(ticks, horizons=TARGET_HORIZONS_SECONDS, time_key="REQUEST_UTC"):
    """Attach forward price moves to each tick of one market.

    A tick whose horizon extends past the end of the capture gets None for that
    horizon -- truncating the capture would make the last observations look
    calm, which is a bias toward whatever the market was doing at the close.
    """
    import datetime

    def t(x):
        s = str(x).replace("Z", "+00:00")
        try:
            d = datetime.datetime.fromisoformat(s)
        except Exception:
            return None
        return d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)

    rows = sorted([x for x in ticks if t(x.get(time_key))],
                  key=lambda x: t(x[time_key]))
    stamps = [t(r[time_key]) for r in rows]
    mids = [_mid(r.get("BEST_BID"), r.get("BEST_ASK")) for r in rows]
    out = []
    truncated = defaultdict(int)
    for i, r in enumerate(rows):
        row = dict(r)
        for h in horizons:
            row["MID_MOVE_%dS" % h] = None
            target = stamps[i] + datetime.timedelta(seconds=h)
            j = None
            for k in range(i + 1, len(rows)):
                if stamps[k] >= target:
                    j = k
                    break
            if j is None:
                truncated["MID_MOVE_%dS" % h] += 1
                continue
            if mids[i] is not None and mids[j] is not None:
                row["MID_MOVE_%dS" % h] = mids[j] - mids[i]
        out.append(row)
    return out, {"TICKS": len(out), "TRUNCATED_AT_CAPTURE_END": dict(truncated),
                 "TRUNCATION_IS_DECLARED_NOT_DROPPED": True}


# --- Section 13. The continuous relative-value target. ---------------------

RELATIVE_VALUE_RULE = (
    "fit the coherent event surface EXCLUDING the target contract, price the "
    "target from that surface, and take the residual. A surface fitted WITH "
    "the target explains the target with itself")

RELATIVE_VALUE_TARGETS = tuple("TARGET_PRICE_%dS" % s
                               for s in TARGET_HORIZONS_SECONDS)

SETTLEMENT_IS_NO_LONGER_THE_ONLY_TARGET = (
    "the proper test of whether cross-market inconsistency is monetizable is "
    "whether the residual predicts the TARGET'S OWN PRICE over the next "
    "seconds and minutes -- not whether it predicts settlement months of "
    "fixtures later")


def surface_residual(target_price, surface_price_ex_target):
    """RESIDUAL_T = P_TARGET - P_SURFACE_EX_TARGET. Sign is the direction."""
    if target_price is None or surface_price_ex_target is None:
        return None
    return float(target_price) - float(surface_price_ex_target)


def relative_value_rows(observations, horizons=TARGET_HORIZONS_SECONDS):
    """Residual at T against the target's own later price.

    Each observation carries P_TARGET, P_SURFACE_EX_TARGET and TARGET_LATER
    (seconds -> price). A negative residual means the target is cheap against
    its own event surface; if the mechanism is real, the later price rises.
    """
    out, missing = [], defaultdict(int)
    for o in observations or ():
        res = surface_residual(o.get("P_TARGET"),
                               o.get("P_SURFACE_EX_TARGET"))
        if res is None:
            missing["NO_RESIDUAL"] += 1
            continue
        row = {"EVENT_KEY": o.get("EVENT_KEY"),
               "MARKET": o.get("MARKET"), "T": o.get("T"),
               "RESIDUAL_T": res, "P_TARGET": o.get("P_TARGET")}
        any_h = False
        for h in horizons:
            p1 = (o.get("TARGET_LATER") or {}).get(h)
            if p1 is None:
                missing["NO_TARGET_AT_%dS" % h] += 1
                continue
            row["TARGET_CHANGE_%dS" % h] = float(p1) - float(o["P_TARGET"])
            any_h = True
        if any_h:
            out.append(row)
    return out, dict(missing)


def relative_value_test(rows, horizons=TARGET_HORIZONS_SECONDS):
    """Does the residual predict the target's own move? Event-clustered.

    A NEGATIVE correlation is the tradeable one: a target priced above its
    surface should fall back toward it.
    """
    import random
    if not rows:
        return {"STATUS": NO_CAPTURE_YET}
    out = {}
    for h in horizons:
        key = "TARGET_CHANGE_%dS" % h
        by = defaultdict(list)
        for r in rows:
            if r.get(key) is not None:
                by[r.get("EVENT_KEY")].append((r["RESIDUAL_T"], r[key]))
        pairs = [p for v in by.values() for p in v]
        if len(pairs) < 8 or len(by) < 3:
            out["%dS" % h] = {"STATUS": "TOO_FEW", "N": len(pairs),
                              "EVENTS": len(by)}
            continue

        def corr(ps):
            xs = [a for a, _ in ps]
            ys = [b for _, b in ps]
            n = len(xs)
            mx, my = sum(xs) / n, sum(ys) / n
            num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
            dx = math.sqrt(sum((a - mx) ** 2 for a in xs))
            dy = math.sqrt(sum((b - my) ** 2 for b in ys))
            return (num / (dx * dy)) if dx > 0 and dy > 0 else None

        evs = sorted(by)
        rnd = random.Random(20260917)
        boot = []
        for _ in range(1000):
            samp = []
            for _ in range(len(evs)):
                samp += by[evs[rnd.randrange(len(evs))]]
            c = corr(samp)
            if c is not None:
                boot.append(c)
        boot.sort()
        out["%dS" % h] = {
            "STATUS": "MEASURED",
            "N_OBSERVATIONS": len(pairs),
            "N_EVENTS": len(evs),
            "CORRELATION": corr(pairs),
            "CI95_EVENT_BOOTSTRAP": ((boot[int(0.025 * len(boot))],
                                      boot[int(0.975 * len(boot)) - 1])
                                     if boot else None),
            "NEGATIVE_MEANS_THE_RESIDUAL_REVERTS": True,
        }
    return {"STATUS": "MEASURED", "BY_HORIZON": out,
            "RULE": RELATIVE_VALUE_RULE,
            "NOTE": SETTLEMENT_IS_NO_LONGER_THE_ONLY_TARGET}


def describe():
    return {
        "TARGET_HORIZONS_SECONDS": TARGET_HORIZONS_SECONDS,
        "TARGETS": TARGETS,
        "CANDIDATE_FEATURES": CANDIDATE_FEATURES,
        "EVALUATION_RULES": dict(EVALUATION_RULES),
        "RELATIVE_VALUE_RULE": RELATIVE_VALUE_RULE,
        "RELATIVE_VALUE_TARGETS": RELATIVE_VALUE_TARGETS,
        "SETTLEMENT_IS_NO_LONGER_THE_ONLY_TARGET":
            SETTLEMENT_IS_NO_LONGER_THE_ONLY_TARGET,
        "THIS_IS_NOT_P_FILL": THIS_IS_NOT_P_FILL,
        "NO_FEATURE_HERE_MAY_BE_USED_AS_A_FILL_PROXY":
            NO_FEATURE_HERE_MAY_BE_USED_AS_A_FILL_PROXY,
        "STATUS": NO_CAPTURE_YET,
    }


# ===========================================================================
# Section 8. BASELINES. A complex model earns admission only by beating these.
#
# The failure mode this prevents is familiar: a gradient-booster on twelve
# features that looks impressive until someone checks it against "assume the
# price does not move", which on a 5-second horizon in a quiet book is very
# hard to beat. Every baseline here is one line of arithmetic, and any model
# that cannot beat all of them chronologically out of sample has not earned
# its complexity.
# ===========================================================================

BASELINES = ("B0_NO_CHANGE", "B1_CURRENT_MID", "B2_MICROPRICE",
             "B3_LAST_MOVE_DIRECTION", "B4_SIMPLE_BOOK_IMBALANCE",
             "B5_SIMPLE_ORDER_FLOW_IMBALANCE")

BASELINE_SEMANTICS = {
    "B0_NO_CHANGE": "predict zero move. The hardest one to beat at 5s",
    "B1_CURRENT_MID": "predict the mid stays where it is (equivalent to B0 for "
                      "a move target; kept separate for level targets)",
    "B2_MICROPRICE": "predict the move toward microprice minus mid",
    "B3_LAST_MOVE_DIRECTION": "momentum: predict the previous move repeats",
    "B4_SIMPLE_BOOK_IMBALANCE": "predict a move proportional to touch imbalance",
    "B5_SIMPLE_ORDER_FLOW_IMBALANCE": "predict a move proportional to order-flow "
                                      "imbalance over the recent window",
}

A_COMPLEX_MODEL_EARNS_ADMISSION_ONLY_BY_BEATING_ALL_OF_THESE = True
CHRONOLOGICALLY_OUT_OF_SAMPLE = True


def baseline_prediction(name, feats, prev_move=None, scale=1.0):
    """One baseline's predicted move. None when its inputs are absent."""
    if name in ("B0_NO_CHANGE", "B1_CURRENT_MID"):
        return 0.0
    if name == "B2_MICROPRICE":
        return feats.get("MICROPRICE_MINUS_MID")
    if name == "B3_LAST_MOVE_DIRECTION":
        return prev_move
    if name == "B4_SIMPLE_BOOK_IMBALANCE":
        v = feats.get("ORDER_BOOK_IMBALANCE")
        return None if v is None else scale * v
    if name == "B5_SIMPLE_ORDER_FLOW_IMBALANCE":
        v = feats.get("ORDER_FLOW_IMBALANCE")
        return None if v is None else scale * v
    return None


def score_baselines(rows, target, pred_key=None, baselines=BASELINES):
    """Score every baseline (and optionally a model) on one target.

    Returns absolute error and direction accuracy per predictor. A predictor
    whose inputs were missing on a row is scored on the rows it COULD price,
    and the count is reported so a thin predictor cannot look good by
    abstaining on the hard ones.
    """
    out = {}
    names = list(baselines) + ([pred_key] if pred_key else [])
    for name in names:
        errs, dirs, n = [], [], 0
        for r in rows or ():
            y = r.get(target)
            if y is None:
                continue
            p = (r.get(pred_key) if name == pred_key
                 else baseline_prediction(name, r, r.get("_PREV_MOVE")))
            if p is None:
                continue
            n += 1
            errs.append(abs(p - y))
            if y != 0:
                dirs.append(1.0 if (p > 0) == (y > 0) else 0.0)
        out[name] = {
            "N_SCORED": n,
            "MEAN_ABSOLUTE_ERROR": (sum(errs) / len(errs)) if errs else None,
            "DIRECTION_ACCURACY": (sum(dirs) / len(dirs)) if dirs else None,
            "DIRECTIONAL_ROWS": len(dirs),
        }
    return {"TARGET": target, "BY_PREDICTOR": out,
            "A_MODEL_MUST_BEAT_ALL_BASELINES":
                A_COMPLEX_MODEL_EARNS_ADMISSION_ONLY_BY_BEATING_ALL_OF_THESE,
            "CHRONOLOGICALLY_OUT_OF_SAMPLE": CHRONOLOGICALLY_OUT_OF_SAMPLE}


# ===========================================================================
# Section 9. Economically meaningful targets.
#
# A correct prediction of a 0.2-cent move is not monetizable through a 2-cent
# spread. Direction accuracy alone will happily report a triumph in exactly
# that situation, so the executable comparison is carried beside it.
# ===========================================================================

ECONOMIC_MEASURES = ("EXPECTED_PRICE_CHANGE", "SIGNED_PRICE_CHANGE",
                     "ABSOLUTE_ERROR", "DIRECTION_ACCURACY",
                     "EXPECTED_EXECUTABLE_MOVE")

A_CORRECT_TINY_PREDICTION_IS_NOT_AN_EDGE = (
    "a 0.2-cent move predicted perfectly through a 2-cent spread earns "
    "nothing. Direction accuracy must always be reported beside the predicted "
    "move relative to the spread")


def economic_row(pred_move, feats):
    """Put a predicted move next to the spread it would have to cross."""
    spread = feats.get("SPREAD")
    out = {
        "PREDICTED_MOVE": pred_move,
        "SPREAD": spread,
        "BEST_BID": feats.get("_BEST_BID"),
        "BEST_ASK": feats.get("_BEST_ASK"),
        "MOVE_AS_FRACTION_OF_SPREAD": None,
        "EXCEEDS_HALF_SPREAD": None,
    }
    if pred_move is not None and spread and spread > 0:
        out["MOVE_AS_FRACTION_OF_SPREAD"] = abs(pred_move) / spread
        out["EXCEEDS_HALF_SPREAD"] = abs(pred_move) > 0.5 * spread
    return out


def economic_summary(rows, pred_key, target):
    """How much of the predicted movement is larger than the spread?"""
    n = big = 0
    fr = []
    for r in rows or ():
        p, s = r.get(pred_key), r.get("SPREAD")
        if p is None or not s or s <= 0:
            continue
        n += 1
        f = abs(p) / s
        fr.append(f)
        if abs(p) > 0.5 * s:
            big += 1
    fr.sort()
    return {
        "TARGET": target,
        "ROWS_WITH_A_SPREAD": n,
        "SHARE_PREDICTING_MORE_THAN_HALF_THE_SPREAD":
            (big / n) if n else None,
        "MOVE_OVER_SPREAD_MEDIAN": fr[len(fr) // 2] if fr else None,
        "MOVE_OVER_SPREAD_P90": fr[int(0.9 * len(fr))] if fr else None,
        "A_CORRECT_TINY_PREDICTION_IS_NOT_AN_EDGE":
            A_CORRECT_TINY_PREDICTION_IS_NOT_AN_EDGE,
    }


# ===========================================================================
# Section 10. Raw informational edge, quantified. NOT maker profit.
# ===========================================================================

MARKOUT_HORIZONS_SECONDS = (5, 30, 60, 300)

EXECUTION_MONETIZABILITY = "NOT_IDENTIFIED"
WHY_NOT_IDENTIFIED = (
    "a markout measures what the mid did after a hypothetical fill. Whether "
    "BETTOR would have BEEN filled is P_FILL, which requires BETTOR's own "
    "passive-order sample and does not exist. Favourable predicted midpoint "
    "movement is an informational edge, not a maker profit")

DO_NOT_CALL_THIS_MAKER_PROFIT = True


def markout(quote_price, side, mid_later, mid_now=None):
    """Signed markout of a hypothetical passive fill. Sign favours the maker.

    A BUY at 0.50 with the mid at 0.52 five seconds later is +0.02 for the
    maker; a SELL at the same level is -0.02.
    """
    if quote_price is None or mid_later is None:
        return None
    if side not in ("BUY", "SELL"):
        return None
    d = float(mid_later) - float(quote_price)
    return d if side == "BUY" else -d


def markout_table(fills, horizons=MARKOUT_HORIZONS_SECONDS):
    """Expected markout by horizon, with the monetizability caveat attached."""
    out = {}
    for h in horizons:
        vals = []
        for f in fills or ():
            m = markout(f.get("QUOTE_PRICE"), f.get("SIDE"),
                        (f.get("MID_LATER") or {}).get(h))
            if m is not None:
                vals.append(m)
        out["EXPECTED_MARKOUT_%dS" % h] = {
            "N": len(vals),
            "MEAN": (sum(vals) / len(vals)) if vals else None,
        }
    out["EXECUTION_MONETIZABILITY"] = EXECUTION_MONETIZABILITY
    out["WHY_NOT_IDENTIFIED"] = WHY_NOT_IDENTIFIED
    out["DO_NOT_CALL_THIS_MAKER_PROFIT"] = DO_NOT_CALL_THIS_MAKER_PROFIT
    return out


# ===========================================================================
# Section 11. Validation protocol. Event AND chronological block.
# ===========================================================================

VALIDATION_SPLIT = "EVENT_AND_CHRONOLOGICAL_BLOCK"
NEVER_RANDOMLY_SCATTER_ADJACENT_TIMESTAMPS = (
    "two ticks four seconds apart in the same game are almost the same "
    "observation. Splitting them across train and test lets the model memorise "
    "the session and report it as skill")

REQUIRED_ALONGSIDE_EVERY_RESULT = ("ROWS", "EVENTS", "EVENT_HOURS",
                                   "OBSERVATIONS_PER_EVENT")


def split_blocks(rows, n_blocks=4, event_key="EVENT_KEY",
                 time_key="REQUEST_UTC"):
    """Contiguous chronological blocks that never split an event."""
    import datetime

    def T(x):
        s = str(x).replace("Z", "+00:00")
        try:
            d = datetime.datetime.fromisoformat(s)
        except Exception:
            return None
        return d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)

    first = {}
    for r in rows or ():
        t = T(r.get(time_key))
        e = r.get(event_key)
        if t is None or e is None:
            continue
        if e not in first or t < first[e]:
            first[e] = t
    evs = sorted(first, key=lambda e: (first[e], str(e)))
    if not evs:
        return [], {"STATUS": "NO_EVENTS"}
    size = max(1, len(evs) // n_blocks)
    blocks = [set(evs[i:i + size]) for i in range(0, len(evs), size)]
    out = [[r for r in rows if r.get(event_key) in b] for b in blocks]
    return out, {"BLOCKS": len(out), "EVENTS": len(evs),
                 "SPLIT": VALIDATION_SPLIT,
                 "NO_EVENT_SPANS_TWO_BLOCKS": True}


def result_context(rows, event_key="EVENT_KEY", time_key="REQUEST_UTC"):
    """The four numbers that must accompany every microstructure result."""
    import datetime

    def T(x):
        s = str(x).replace("Z", "+00:00")
        try:
            d = datetime.datetime.fromisoformat(s)
        except Exception:
            return None
        return d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)

    by = defaultdict(list)
    for r in rows or ():
        t = T(r.get(time_key))
        if t is not None and r.get(event_key) is not None:
            by[r[event_key]].append(t)
    hours = 0.0
    for v in by.values():
        if len(v) > 1:
            hours += (max(v) - min(v)).total_seconds() / 3600.0
    n = len(rows or ())
    return {
        "ROWS": n,
        "EVENTS": len(by),
        "EVENT_HOURS": hours,
        "OBSERVATIONS_PER_EVENT": (n / len(by)) if by else None,
        "REQUIRED_ALONGSIDE_EVERY_RESULT": REQUIRED_ALONGSIDE_EVERY_RESULT,
    }
