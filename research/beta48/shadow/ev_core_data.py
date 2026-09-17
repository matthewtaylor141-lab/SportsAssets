#!/usr/bin/env python3
"""EV CORE -- THE DATASET. Joining priced observations to settled outcomes.

WHAT THIS BUILDS. One row per (contract, observation time): the market's own
price for that contract at a moment strictly before it settled, plus the book
state at that moment, plus the eventual binary settlement. That row is the unit
every probability model in this programme trains and is scored on.

THE AS-OF RULE, AND IT IS THE WHOLE POINT. A feature is legal only if it was
KNOWABLE AT THE OBSERVATION TIME. Not "exists in the corpus", not "is about the
past" -- knowable then. Every feature therefore carries SOURCE, AS_OF, and a
MISSINGNESS status, and the builder refuses any observation whose timestamp is
not strictly earlier than the settlement it is labelled with. The label comes
from after the event; that is what a label is. Nothing else may.

WHAT THE OBSERVATIONS ACTUALLY ARE. The retained corpus is a whale-trade probe:
each row is a large account's fill, captured with the venue's book at probe
time. So the observation TIMES are where that account chose to trade, which is
emphatically NOT a uniform sample of market time.

    OBSERVATION_SAMPLING = WHALE_TRADE_TRIGGERED
    SAMPLING_IS_NOT_UNIFORM_IN_TIME = TRUE

This biases WHEN we look, not WHAT the price was when we looked -- the price and
the settlement are both the venue's own facts. It matters for any claim about
"the market's calibration in general", and it is reported beside every such
number rather than discovered later.

THE INDEPENDENT UNIT IS THE EVENT. Sixteen totals lines on one fixture share one
outcome. Treating them as sixteen independent observations would shrink every
confidence interval by a factor of four for free. Every row therefore carries
EVENT_KEY, and everything downstream clusters on it.

This module contacts nothing and can place no order.
"""
import gzip
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone

import ev_core_outcomes as OC

NOT_IDENTIFIED = "NOT_IDENTIFIED"
NOT_AVAILABLE = "NOT_AVAILABLE"

OBSERVATION_SAMPLING = "WHALE_TRADE_TRIGGERED"
SAMPLING_IS_NOT_UNIFORM_IN_TIME = True
WHY_SAMPLING_MATTERS = (
    "observation times are where a large account chose to trade, not a uniform "
    "sample of market time; the price and settlement at those times are still "
    "the venue's own facts, but a claim about the market's calibration IN "
    "GENERAL does not follow from a claim about its calibration WHERE A WHALE "
    "TRADED")

INDEPENDENT_UNIT = "EVENT"
WHY_EVENT_IS_THE_UNIT = (
    "every contract on one fixture shares one outcome; counting them as "
    "independent observations shrinks every interval for free")

# ---------------------------------------------------------------------------
# FEATURE REGISTRY. Nothing reaches a model without an entry here.
# AVAILABLE_AT_DECISION_TIME is the field that decides legality.
# ---------------------------------------------------------------------------

FEATURES = {
    "P_VENUE_TRADE": {
        "SOURCE": "u2_events.price (the price this fill traded at)",
        "AVAILABLE_AT_DECISION_TIME": True,
        "NOTE": "a traded price is a market probability observation"},
    "BEST_ASK": {
        "SOURCE": "u2_events.best_ask (venue book at probe time)",
        "AVAILABLE_AT_DECISION_TIME": True,
        "NOTE": "probe follows the fill by a measured reaction_s"},
    "DEPTH_LEVELS": {
        "SOURCE": "u2_events.depth_levels",
        "AVAILABLE_AT_DECISION_TIME": True},
    "ASK_DEPTH_USD": {
        "SOURCE": "u2_events.best_ask_usd",
        "AVAILABLE_AT_DECISION_TIME": True},
    "WHALE_SIDE": {
        "SOURCE": "u2_events.side",
        "AVAILABLE_AT_DECISION_TIME": True,
        "NOTE": "the corpus is BUY-only; it carries no SELL contrast"},
    "WHALE_NOTIONAL": {
        "SOURCE": "u2_events.notional",
        "AVAILABLE_AT_DECISION_TIME": True},
    "SPORT": {"SOURCE": "u2_events.sport", "AVAILABLE_AT_DECISION_TIME": True},
    "MARKET_FAMILY": {
        "SOURCE": "derived from the venue slug grammar",
        "AVAILABLE_AT_DECISION_TIME": True},
    "TIME_TO_SETTLEMENT_S": {
        "SOURCE": "settlement.resolved_at - observation ts",
        "AVAILABLE_AT_DECISION_TIME": False,
        "NOTE": "USES THE FUTURE. Diagnostic stratification only -- it must "
                "never enter a model. Scheduled kickoff time would be the "
                "legal analogue and the retained corpus does not carry it."},
    "SETTLED_YES": {
        "SOURCE": "settlement.payouts",
        "AVAILABLE_AT_DECISION_TIME": False,
        "NOTE": "THE LABEL"},
}

ILLEGAL_AS_FEATURES = tuple(sorted(
    k for k, v in FEATURES.items() if not v["AVAILABLE_AT_DECISION_TIME"]))

# Market family from the settlement slug grammar, most specific first so a
# segment contract is never read as a full-game one.
FAMILY_PATTERNS = (
    ("FIRST_HALF_TOTAL", re.compile(r"-first-half-total-\d+pt5$")),
    ("HALFTIME_RESULT", re.compile(r"-halftime-result-(home|away|draw)$")),
    ("EXACT_SCORE", re.compile(r"-exact-score-\d+-\d+$")),
    ("TEAM_TOTAL", re.compile(r"-[a-z0-9]+-team-total-\d+pt5$")),
    ("TOTAL", re.compile(r"-total-\d+pt5$")),
    ("BTTS", re.compile(r"-btts$")),
    ("DRAW", re.compile(r"-draw$")),
    ("SPREAD", re.compile(r"-spread-")),
    ("HANDICAP", re.compile(r"-handicap")),
)


def market_family(slug):
    for name, pat in FAMILY_PATTERNS:
        if pat.search(slug or ""):
            return name
    return "MONEYLINE_OR_OTHER"


def _parse(ts):
    if not ts:
        return None
    s = str(ts).replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def load_settlements(path):
    """Settled contracts, keyed by slug, with the winning token recorded."""
    out = {}
    counts = Counter()
    for line in open(path):
        try:
            d = json.loads(line)
        except Exception:                                      # noqa: BLE001
            counts["UNPARSEABLE"] += 1
            continue
        counts["ROWS"] += 1
        slug = d.get("market_slug")
        if not slug:
            counts["NO_SLUG"] += 1
            continue
        if not d.get("resolved"):
            counts["UNRESOLVED"] += 1
            continue
        payouts = d.get("payouts") or []
        tokens = d.get("tokens") or []
        if len(payouts) != len(tokens) or not tokens:
            counts["MALFORMED_PAYOUT"] += 1
            continue
        win = {}
        for t, p in zip(tokens, payouts):
            try:
                win[str(t.get("outcome"))] = float(p)
            except (TypeError, ValueError):
                pass
        if not win:
            counts["MALFORMED_PAYOUT"] += 1
            continue
        out[slug] = {
            "MARKET_SLUG": slug,
            "SPORT": d.get("sport"),
            "RESOLVED_AT": d.get("resolved_at"),
            "PAYOUT_BY_OUTCOME": win,
            "EVENT_KEY": OC.event_key(slug),
            "MARKET_FAMILY": market_family(slug),
            "CONDITION_ID": d.get("condition_id"),
        }
        counts["USABLE"] += 1
    return out, dict(counts)


def build(observations_path, settlements_path, sport=None, limit=None):
    """One row per priced observation of a contract that later settled.

    Refusals are counted, never silently dropped: a join that quietly loses
    rows is how a biased sample gets built without anyone noticing.
    """
    settle, scounts = load_settlements(settlements_path)
    rows = []
    r = Counter()

    opener = gzip.open if str(observations_path).endswith(".gz") else open
    for i, line in enumerate(opener(observations_path, "rt")):
        if limit and i >= limit:
            break
        r["OBSERVATIONS_READ"] += 1
        try:
            d = json.loads(line)
        except Exception:                                      # noqa: BLE001
            r["UNPARSEABLE"] += 1
            continue
        slug = d.get("market_slug")
        s = settle.get(slug)
        if s is None:
            r["NO_SETTLEMENT_FOR_SLUG"] += 1
            continue
        if sport is not None and s["SPORT"] != sport:
            r["SPORT_FILTERED"] += 1
            continue

        outcome = d.get("outcome")
        if outcome is None or str(outcome) not in s["PAYOUT_BY_OUTCOME"]:
            r["OUTCOME_NOT_IN_SETTLEMENT"] += 1
            continue

        ts, res = _parse(d.get("ts")), _parse(s["RESOLVED_AT"])
        if ts is None or res is None:
            r["MISSING_TIMESTAMP"] += 1
            continue
        # THE AS-OF GATE. An observation at or after settlement is not a
        # forecast, it is a readback. Refuse it.
        if ts >= res:
            r["OBSERVED_AT_OR_AFTER_SETTLEMENT"] += 1
            continue

        try:
            price = float(d.get("price"))
        except (TypeError, ValueError):
            r["NO_PRICE"] += 1
            continue
        if not (0.0 < price < 1.0):
            r["PRICE_OUT_OF_RANGE"] += 1
            continue

        ev = s["EVENT_KEY"] or ("SLUG:" + slug)
        rows.append({
            "EVENT_KEY": ev,
            "MARKET_SLUG": slug,
            "MARKET_FAMILY": s["MARKET_FAMILY"],
            "SPORT": s["SPORT"],
            "OUTCOME": str(outcome),
            "AS_OF": d.get("ts"),
            "RESOLVED_AT": s["RESOLVED_AT"],
            "TIME_TO_SETTLEMENT_S": (res - ts).total_seconds(),
            "P_VENUE_TRADE": price,
            "BEST_ASK": _f(d.get("best_ask")),
            "ASK_DEPTH_USD": _f(d.get("best_ask_usd")),
            "DEPTH_LEVELS": d.get("depth_levels"),
            "WHALE_SIDE": d.get("side"),
            "WHALE_NOTIONAL": _f(d.get("notional")),
            "SETTLED_YES": 1 if s["PAYOUT_BY_OUTCOME"][str(outcome)] == 1.0
                           else 0,
        })
        r["ROWS_BUILT"] += 1

    events = {x["EVENT_KEY"] for x in rows}
    return {
        "ROWS": rows,
        "ROW_COUNT": len(rows),
        "EVENT_COUNT": len(events),
        "MARKET_COUNT": len({x["MARKET_SLUG"] for x in rows}),
        "JOIN_ACCOUNTING": dict(r),
        "SETTLEMENT_ACCOUNTING": scounts,
        "BY_FAMILY": dict(Counter(x["MARKET_FAMILY"] for x in rows)),
        "BY_SPORT": dict(Counter(x["SPORT"] for x in rows)),
        "BASE_RATE_YES": (sum(x["SETTLED_YES"] for x in rows) / float(len(rows))
                          if rows else NOT_IDENTIFIED),
        "OBSERVATION_SAMPLING": OBSERVATION_SAMPLING,
        "SAMPLING_IS_NOT_UNIFORM_IN_TIME": SAMPLING_IS_NOT_UNIFORM_IN_TIME,
        "WHY_SAMPLING_MATTERS": WHY_SAMPLING_MATTERS,
        "INDEPENDENT_UNIT": INDEPENDENT_UNIT,
        "WHY_EVENT_IS_THE_UNIT": WHY_EVENT_IS_THE_UNIT,
        "ILLEGAL_AS_FEATURES": list(ILLEGAL_AS_FEATURES),
        "FEATURES": FEATURES,
        "AS_OF_GATE": ("every row is observed strictly before its settlement; "
                       "violations are counted as "
                       "OBSERVED_AT_OR_AFTER_SETTLEMENT and discarded"),
    }


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def render(rep):
    L = []
    for k in ("ROW_COUNT", "EVENT_COUNT", "MARKET_COUNT", "BASE_RATE_YES",
              "OBSERVATION_SAMPLING", "INDEPENDENT_UNIT"):
        L.append("%-36s = %s" % (k, rep.get(k, NOT_IDENTIFIED)))
    L.append("%-36s = %s" % ("BY_FAMILY", rep.get("BY_FAMILY")))
    L.append("%-36s = %s" % ("JOIN_ACCOUNTING", rep.get("JOIN_ACCOUNTING")))
    return "\n".join(L)


def to_json(rep):
    out = dict(rep)
    out.pop("ROWS", None)
    return json.dumps(out, indent=1, sort_keys=True, default=str)
