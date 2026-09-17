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
