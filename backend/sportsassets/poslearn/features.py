"""THE OPPORTUNITY FEATURE CONTRACT: every candidate feature named in the
brief, what it is computed from, and -- per opportunity -- whether it was
available, AS OF the opportunity instant. Pure; no I/O.

NO LOOK-AHEAD, BY CONSTRUCTION. `build` receives only context the caller
filtered to `t = opportunity_at` (a book observation at or before t, quotes
strictly before t, a regime state computed at or before t, and a history
index advanced only over outcomes KNOWN at or before t). `HistoryIndex`
refuses to move backwards and never counts an outcome whose outcome_at is
after the query time. Migration 218 CHECKs features_as_of <= opportunity_at
and refuses any outcome-bearing key in the stored features.

UNAVAILABLE IS NAMED, NEVER ZERO. A feature that cannot be computed is
absent from `features` and present in `unavailable` with its reason.
"""
from __future__ import annotations

import bisect
import math

from ..intel import common as IC
from . import common as C

VERSION = "POSLEARN_FEATURES_V1"
BOOK_MAX_AGE_S = 300.0
QUOTE_WINDOW_S = 3600.0
PROB_EDGES = (0.2, 0.4, 0.6, 0.8)
PROB_LABELS = ("0.0-0.2", "0.2-0.4", "0.4-0.6", "0.6-0.8", "0.8-1.0")

#: brief feature -> (stored key(s), source, standing status). "PER_ROW"
#: means available when its source row exists for that opportunity;
#: anything else is a standing UNAVAILABLE reason for this SHA.
CATALOG = (
    ("PinnAPI probability", ("p_reference", "logit_p"),
     "the valuation row's probability", "PER_ROW"),
    ("Polymarket price", ("price",),
     "the valuation's executable price, else the CALIBRATION_ONLY "
     "displayed price (labelled DISPLAYED_NOT_EXECUTABLE)", "PER_ROW"),
    ("gross edge", ("gross_edge",), "p - price", "PER_ROW"),
    ("net edge", ("net_edge",), "p - price - fee per contract", "PER_ROW"),
    ("spread", ("spread",), "paper_book_observations as of t", "PER_ROW"),
    ("depth", ("depth_top_qty", "log_depth_top"),
     "best level on our side, paper_book_observations as of t", "PER_ROW"),
    ("liquidity", ("liquidity_usd", "log_liquidity"),
     "top-5 displayed notional both sides, as of t", "PER_ROW"),
    ("time-to-event", ("tte_h", "tte_h_log"),
     "us_premap.game_start - t", "PER_ROW"),
    ("live/pregame", ("is_live",), "t vs us_premap.game_start", "PER_ROW"),
    ("sport", ("sport",), "the valuation's sport family", "PER_ROW"),
    ("league", ("league",), "us_premap.team_league", "PER_ROW"),
    ("probability band", ("probability_band",), "band of p", "PER_ROW"),
    ("PinnAPI volatility", ("pinnapi_vol_1h",),
     "mean |dp| of this contract's quotes in the hour before t", "PER_ROW"),
    ("market-change frequency", ("change_freq_1h",),
     "count of changed quotes for this contract in the hour before t",
     "PER_ROW"),
    ("price movement", ("price_move_1h",),
     "p now - first p in the hour before t", "PER_ROW"),
    ("execution latency", ("quote_age_s", "quote_age_log"),
     "PROXY ONLY: the valuation's quote age (provider stamp -> decision); "
     "order-to-fill latency needs EDDIE", "PER_ROW"),
    ("cross-market disagreement", ("cross_market_gap",),
     "p - venue mid in our contract's space (PinnAPI vs Polymarket); no "
     "independent third market is recorded", "PER_ROW"),
    ("calibration history", ("cal_half_width", "cal_miscal"),
     "Wilson half-width and mean(p) - frequency in p's band over outcomes "
     "known by t", "PER_ROW"),
    ("regime", ("regime", "regime_reduce"),
     "latest intel_regime_states at or before t", "PER_ROW"),
    ("settlement confidence", ("settlement_family",),
     "settlement family only; a per-contract settlement confidence is not "
     "recorded at decision time",
     "UNAVAILABLE_NO_PER_CONTRACT_SETTLEMENT_CONFIDENCE_AT_DECISION_TIME"),
    ("Karen challenge state", (),
     "karen_challenges target agent records, not opportunities",
     "UNAVAILABLE_KAREN_CHALLENGES_ARE_NOT_KEYED_BY_OPPORTUNITY"),
    ("Scout feature state", (), "no Scout feature exists at this SHA",
     "UNAVAILABLE_SCOUT_FEATURES_DO_NOT_EXIST_YET"),
    ("historical edge reliability", ("hist_reliability",),
     "mean(outcome - p) for the sport over outcomes known by t", "PER_ROW"),
    ("EDDIE execution uncertainty", ("eddie_exec_uncertainty",),
     "INTERFACE ONLY: eddie_execution_estimates (claude/pos-agents) when "
     "that table exists", "PER_ROW_WHEN_EDDIE_PRESENT"),
)

#: the numeric vector the edge-confidence meta-model consumes
EC_FEATURES = ("logit_p", "gross_edge", "net_edge", "spread",
               "log_depth_top", "log_liquidity", "tte_h_log", "is_live",
               "pinnapi_vol_1h", "change_freq_1h", "price_move_1h",
               "quote_age_log", "cross_market_gap", "cal_half_width",
               "cal_miscal", "regime_reduce", "hist_reliability",
               "eddie_exec_uncertainty")

#: keys that may NEVER appear among features (post-outcome information)
FORBIDDEN_KEYS = ("outcome", "outcome_known", "outcome_at", "outcome_basis",
                  "realised_net_usd", "settlement_read", "realized_edge",
                  "settlement_comparison")


def prob_band(p):
    return C.band(p, PROB_EDGES, PROB_LABELS)


def price_of(row: dict):
    """(price, fee, basis) of the contract the valuation prices. An
    ENTRY_DECISION row carries its executable ask; a CALIBRATION_ONLY row
    carries only the DISPLAYED quote as evidence -- used here for SHADOW
    research economics only, labelled as such, never as an order price."""
    px = C.num(row.get("executable_price"))
    if px is not None and 0.0 < px < 1.0:
        return px, C.num(row.get("cost_per_contract")), "EXECUTABLE_ASK"
    ev = C.jload(row.get("calibration_only_evidence")) or {}
    cmp_ = ev.get("compared_at_the_displayed_price") if isinstance(
        ev, dict) else None
    if isinstance(cmp_, dict):
        px = C.num(cmp_.get("price"))
        if px is not None and 0.0 < px < 1.0:
            return (px, C.num(cmp_.get("cost_per_contract")),
                    "DISPLAYED_NOT_EXECUTABLE")
    return None, None, None


def side_of(row: dict) -> str:
    return "SHORT" if "SHORT" in str(row.get("buy_intent") or "").upper() \
        else "LONG"


def settlement_family(row: dict) -> str:
    rule = str(row.get("settlement_rule") or "").upper()
    if not rule:
        return "UNKNOWN"
    for tok in ("OVERTIME", "REGULATION", "FULL_GAME", "MAP", "SET",
                "INNINGS", "PERIOD"):
        if tok in rule:
            return tok
    return "OTHER"


class HistoryIndex:
    """Outcomes known by time t, queried with non-decreasing t.

    records: [{"outcome_at", "p", "o", "sport", "probability_band"}]."""

    def __init__(self, records):
        self.recs = sorted((r for r in records
                            if r.get("outcome_at") is not None
                            and r.get("p") is not None
                            and r.get("o") in (0, 1)),
                           key=lambda r: r["outcome_at"])
        self.i = 0
        self.t = float("-inf")
        self.sport = {}     # sport -> [sum(o - p), n]
        self.band = {}      # band -> [sum p, sum o, n]

    def advance(self, t):
        t = float(t)
        if t < self.t:
            raise ValueError("HistoryIndex cannot move backwards "
                             "(look-ahead guard)")
        self.t = t
        while self.i < len(self.recs) and self.recs[self.i]["outcome_at"] <= t:
            r = self.recs[self.i]
            s = self.sport.setdefault(r.get("sport") or "UNKNOWN", [0.0, 0])
            s[0] += r["o"] - r["p"]
            s[1] += 1
            b = self.band.setdefault(r.get("probability_band")
                                     or prob_band(r["p"]), [0.0, 0, 0])
            b[0] += r["p"]
            b[1] += r["o"]
            b[2] += 1
            self.i += 1
        return self

    def known(self) -> int:
        return self.i

    def reliability(self, sport, min_n=20):
        s = self.sport.get(sport or "UNKNOWN")
        if not s or s[1] < min_n:
            return None, (0 if not s else s[1])
        return s[0] / s[1], s[1]

    def calibration(self, band, min_n=20):
        from .. import bettor_source_calibration as SC
        b = self.band.get(band)
        if not b or b[2] < min_n:
            return None, None, (0 if not b else b[2])
        lo, hi = SC.wilson_interval(b[1], b[2])
        return (hi - lo) / 2.0, b[0] / b[2] - b[1] / b[2], b[2]


def quote_stats(quotes, t, p_now):
    """quotes: [(at, p)] of this contract strictly before t (any order)."""
    qs = sorted((a, p) for a, p in quotes
                if a is not None and p is not None
                and t - QUOTE_WINDOW_S <= a < t)
    if not qs:
        return None, None, None
    ps = [p for _, p in qs] + ([p_now] if p_now is not None else [])
    moves = [abs(b - a) for a, b in zip(ps, ps[1:])]
    vol = (sum(moves) / len(moves)) if moves else 0.0
    changes = sum(1 for m in moves if m > 1e-9)
    move = (p_now - qs[0][1]) if p_now is not None else None
    return vol, float(changes), move


def build(row: dict, *, t: float, book=None, premap=None, quotes=(),
          regime=None, history: HistoryIndex | None = None,
          eddie=None) -> tuple:
    """(features, unavailable, meta) for one valuation row as of t."""
    f, un = {}, {}
    premap = premap or {}
    p = C.num(row.get("probability"))
    side = side_of(row)
    price, fee, basis = price_of(row)
    meta = {"price": price, "fee": fee, "price_basis": basis, "side": side,
            "sport": str(row.get("sport_family") or premap.get(
                "sports_type") or "UNKNOWN"),
            "league": str(premap.get("team_league") or "UNKNOWN"),
            "market": str(row.get("market") or "UNKNOWN")}

    def put(k, v, why):
        if v is None:
            un[k] = why
        else:
            f[k] = v

    put("p_reference", p, "NO_PROBABILITY")
    put("logit_p", None if p is None else C.logit(p), "NO_PROBABILITY")
    put("price", price, "NO_EXECUTABLE_OR_DISPLAYED_PRICE")
    gross = (p - price) if (p is not None and price is not None) else None
    put("gross_edge", gross, "NEEDS_PROBABILITY_AND_PRICE")
    put("net_edge", None if gross is None or fee is None else gross - fee,
        "FEE_NOT_RECORDED" if gross is not None else
        "NEEDS_PROBABILITY_AND_PRICE")
    # ── the venue book as of t, in OUR contract's space
    if book is not None:
        view = IC.book_view(book.get("bids"), book.get("offers"))
        spread = view["spread"]
        mid = view["mid"]
        if side == "SHORT" and mid is not None:
            mid = 1.0 - mid
        ladder = view["offers"] if side == "LONG" else view["bids"]
        top = ladder[0][1] if ladder else None
        put("spread", spread, "BOOK_HAS_NO_TWO_SIDED_QUOTE")
        put("depth_top_qty", top, "NO_LEVEL_ON_OUR_SIDE")
        put("log_depth_top", None if top is None else math.log1p(top),
            "NO_LEVEL_ON_OUR_SIDE")
        liq = view["top5_depth_usd"]
        put("liquidity_usd", liq, "NO_BOOK")
        put("log_liquidity", math.log1p(liq), "NO_BOOK")
        put("cross_market_gap", None if (mid is None or p is None)
            else p - mid, "NO_VENUE_MID")
        meta["book_observed_at"] = book.get("observed_at")
    else:
        for k in ("spread", "depth_top_qty", "log_depth_top",
                  "liquidity_usd", "log_liquidity", "cross_market_gap"):
            un[k] = "NO_BOOK_OBSERVATION_WITHIN_%dS_BEFORE_T" % BOOK_MAX_AGE_S
    # ── time to event / live state
    gs = C.num(premap.get("game_start"))
    if gs is None:
        for k in ("tte_h", "tte_h_log", "is_live"):
            un[k] = "NO_VENUE_GAME_START"
        meta["live_state"] = "UNKNOWN"
    else:
        h = (gs - t) / 3600.0
        f["tte_h"] = h
        f["tte_h_log"] = math.log1p(max(0.0, h))
        f["is_live"] = 1.0 if h <= 0 else 0.0
        meta["live_state"] = "LIVE" if h <= 0 else "PREGAME"
    f["holding_side"] = side
    f["payout_event"] = str(row.get("payout_event") or "")
    f["sport"] = meta["sport"]
    f["league"] = meta["league"]
    f["market"] = meta["market"]
    f["probability_band"] = prob_band(p)
    f["settlement_family"] = settlement_family(row)
    # ── quotes in the hour before t
    vol, freq, move = quote_stats(quotes, t, p)
    why_q = "NO_EARLIER_QUOTE_OF_THIS_CONTRACT_IN_THE_HOUR_BEFORE_T"
    put("pinnapi_vol_1h", vol, why_q)
    put("change_freq_1h", freq, why_q)
    put("price_move_1h", move, why_q)
    age = C.num(row.get("age_s"))
    put("quote_age_s", age, "QUOTE_AGE_NOT_RECORDED")
    put("quote_age_log", None if age is None else math.log1p(max(0.0, age)),
        "QUOTE_AGE_NOT_RECORDED")
    un["execution_latency_ms"] = "UNAVAILABLE_NEEDS_EDDIE_ORDER_TO_FILL"
    # ── regime
    if regime:
        f["regime"] = regime
        f["regime_reduce"] = 0.0 if regime == "NORMAL" else 1.0
    else:
        un["regime"] = un["regime_reduce"] = "NO_REGIME_STATE_AT_OR_BEFORE_T"
    # ── history known by t
    if history is not None:
        history.advance(t)
        rel, rn = history.reliability(meta["sport"])
        put("hist_reliability", rel,
            "FEWER_THAN_20_OUTCOMES_KNOWN_FOR_SPORT_N_%d" % rn)
        hw, mis, bn = history.calibration(f["probability_band"])
        put("cal_half_width", hw,
            "FEWER_THAN_20_OUTCOMES_KNOWN_IN_BAND_N_%d" % bn)
        put("cal_miscal", mis,
            "FEWER_THAN_20_OUTCOMES_KNOWN_IN_BAND_N_%d" % bn)
    else:
        for k in ("hist_reliability", "cal_half_width", "cal_miscal"):
            un[k] = "NO_HISTORY_INDEX"
    # ── EDDIE (interface only)
    eu = C.num((eddie or {}).get("execution_uncertainty"))
    put("eddie_exec_uncertainty", eu,
        (eddie or {}).get("why") or "EDDIE_INTERFACE_ABSENT")
    un["settlement_confidence"] = CATALOG[19][3]
    un["karen_challenge_state"] = CATALOG[20][3]
    un["scout_feature_state"] = CATALOG[21][3]
    for k in FORBIDDEN_KEYS:
        assert k not in f, k
    return f, un, meta


def catalog_status(opps: list) -> list:
    """Per brief feature: share of the given opportunities that had it."""
    out = []
    for name, keys, source, standing in CATALOG:
        if not keys or standing.startswith("UNAVAILABLE"):
            out.append({"feature": name, "keys": list(keys), "source": source,
                        "status": "UNAVAILABLE", "why": standing,
                        "available_share": None})
            continue
        n = len(opps)
        have = sum(1 for o in opps if keys[0] in (o.get("features") or {}))
        out.append({"feature": name, "keys": list(keys), "source": source,
                    "status": ("AVAILABLE" if n and have == n else
                               "PARTIAL" if have else
                               "UNAVAILABLE" if n else "UNMEASURED"),
                    "why": None if have else (
                        "NO_OPPORTUNITY_CAPTURED_YET" if not n else
                        "ABSENT_ON_EVERY_CAPTURED_OPPORTUNITY"),
                    "available_share": (C.rnd(have / n, 4) if n else None)})
    return out
