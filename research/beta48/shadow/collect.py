#!/usr/bin/env python3
"""PHASE_2A collector: live public market state + decision telemetry.

THE SPLIT THAT KEEPS THE BOUNDARY HONEST. `position_state.py` contacts nothing
and is proved to import no HTTP client at all. This file is the only place a
network read happens, and it is GET-only against one host with no credential --
the same boundary the stage-2 census ran under. Everything it reads is handed to
`position_state` as an argument, so the decision engine stays structurally
incapable of touching a venue.

WHAT A PAIR IS, ON THIS VENUE -- MEASURED, AFTER A WRONG FIRST ASSUMPTION.

I built this expecting the Polymarket shape, where YES and NO are separately
quoted tokens in independent books whose asks can sum below 1.00. The captured
board refutes that on all 20,000 rows: `slugs` holds exactly one slug,
`side_identifiers` holds the SAME slug twice, and `side_descriptions` is
["Yes", "No"]. Every market is ONE binary book carrying both sides, so buying
the complement IS selling the own side, and there is no sibling contract to
pair with.

That makes the pair channel a MAKER-ONLY trade here, measured on the sealed
census over 9,143 routed two-sided books:

    cross both legs   ask + (1 - bid) = 1 + spread    0 of 9,143 below 1.00
    rest  both legs   bid + (1 - ask) = 1 - spread    9,143 of 9,143 below

So the pair channel on this venue collapses ONTO
`ACTUAL_BETTOR_FILL_PROBABILITY` rather than around it. It does not add a
blocker; it removes a hoped-for way past the one we already had.

The sibling-complement resolver is KEPT, and refuses to guess. If the venue
ever quotes a complement independently, `EVENT_KEY_VALIDATED = NO` still holds,
so a complement is admitted only at identity LEVEL_A or LEVEL_B -- a venue
event id, or a cluster proved to be one contest by one start time, one pair of
teams and one provider. Otherwise the sibling is NOT_IDENTIFIED and the row is
still written: a tick with no identifiable sibling is data, and dropping it
would bias the sample toward exactly the markets where pairing looks easy.
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from decimal import Decimal as D
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "forward"))

import eligibility as E  # noqa: E402
import position_state as P  # noqa: E402

NOT_IDENTIFIED = "NOT_IDENTIFIED"

# The capability boundary, identical to the census's and asserted structurally
# by test_collect.py rather than by this comment.
HOST = "https://api.sportstradingus.com"
BOOK_PATH = "/v1/markets/{slug}/book"
METHOD = "GET"
CREDENTIAL = None
ORDER_PATH_EXISTS = False
mirror_live = False
RATE_LIMIT_RPS = 2.0

COLLECT_VERSION = "beta48-shadow-collect/1"
SELECTION_SALT = "BETA48-SHADOW-2026-09-16"


def _jsonable(o):
    """Decimal -> its EXACT decimal string. Never a float."""
    if isinstance(o, D):
        return str(o)
    raise TypeError("not JSON serializable: %s" % type(o).__name__)


class Pacer:
    """One request per 1/rps, measured, never bursted."""

    def __init__(self, rps=RATE_LIMIT_RPS):
        self.gap = 1.0 / float(rps)
        self.last = 0.0

    def wait(self):
        now = time.monotonic()
        due = self.last + self.gap
        if now < due:
            time.sleep(due - now)
        self.last = time.monotonic()


def read_book(http, pacer, slug):
    """One paced public GET. Returns (body, receipt_iso, status, error)."""
    pacer.wait()
    url = HOST + BOOK_PATH.format(slug=slug)
    recv = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    try:
        r = http.get(url, timeout=20.0)
    except Exception as exc:                       # noqa: BLE001
        return None, recv, None, "%s: %s" % (type(exc).__name__, exc)
    if r.status_code != 200:
        return None, recv, r.status_code, "http_%d" % r.status_code
    try:
        return r.json(), recv, 200, None
    except Exception as exc:                       # noqa: BLE001
        return None, recv, 200, "decode: %s" % exc


# ---------------------------------------------------------------------------
# CANDIDATE SELECTION
# ---------------------------------------------------------------------------

def select_candidates(census_rows, limit=None, salt=SELECTION_SALT):
    """HIGH_ACTIVITY markets from the sealed census, ordered by salted hash.

    The order is a FROZEN HASH, not the census's scan order and not activity
    rank. Scan order would bias toward whatever the board listed first;
    activity rank would select the most active markets and then report an
    activity distribution measured on them, which is the same circularity the
    UFC panel already taught this programme to avoid.
    """
    out = []
    for r in census_rows:
        d = r.get("DECISION") or {}
        if not d.get("HIGH_ACTIVITY_AT_DECISION"):
            continue
        slug = r.get("slug")
        h = hashlib.sha256((salt + "|" + str(slug)).encode()).hexdigest()
        out.append((h, slug, r))
    out.sort()
    sel = [(s, r) for _, s, r in out]
    return sel[:limit] if limit else sel


def resolve_complement(slug, board_by_slug, family_index):
    """The sibling contract that completes a pair, or NOT_IDENTIFIED.

    Admitted ONLY on a LEVEL_A or LEVEL_B identity. `EVENT_KEY_VALIDATED = NO`,
    so a family key alone is a heuristic: it over-merges (394 markets, 32
    participant sets, one key) and under-merges (8 keys, one roster, one date).
    A pair basis built on a wrong complement would be a number about two
    unrelated contests.
    """
    fam = E.market_family_key(slug)
    siblings = [s for s in family_index.get(fam, []) if s != slug]
    if not siblings:
        return NOT_IDENTIFIED, NOT_IDENTIFIED, "NO_SIBLING_IN_FAMILY"
    rows = [board_by_slug[s] for s in [slug] + siblings if s in board_by_slug]
    key, level = E.underlying_event_key(rows)
    if level not in (E.IDENTITY_LEVEL_A, E.IDENTITY_LEVEL_B):
        return NOT_IDENTIFIED, NOT_IDENTIFIED, "IDENTITY_%s" % level
    if len(siblings) != 1:
        # A two-outcome contest has exactly one complement. More than one and
        # "the" complement is a choice we have no basis to make.
        return NOT_IDENTIFIED, key, "AMBIGUOUS_%d_SIBLINGS" % len(siblings)
    return siblings[0], key, "OK_%s" % level


# WHAT A PAIR COSTS ON THIS VENUE -- a correction to my own first design.
#
# I built this expecting the Polymarket shape: YES and NO as separately quoted
# tokens in independent books, whose asks can sum below 1.00. The captured
# board says otherwise, on all 20,000 rows:
#
#     slugs            exactly one
#     side_identifiers the SAME slug twice
#     side_descriptions ["Yes", "No"]
#
# Every market is ONE binary book carrying both sides. So the complement of
# YES is not a sibling contract -- it is the other side of the same ladder,
# and buying NO IS selling YES. That collapses the pair into two arithmetic
# facts, both measured on the sealed census (9,143 routed two-sided books):
#
#     AGGRESSIVE: cross both legs   ask + (1 - bid) = 1 + spread   >= 1.00
#                 P10/P50/P90 = 1.01 / 1.01 / 1.01, and 0 of 9,143 below 1.00
#     PASSIVE:    rest both legs    bid + (1 - ask) = 1 - spread    < 1.00
#                 P10/P50/P90 = 0.99 / 0.99 / 0.99, all 9,143 below 1.00
#
# The consequence is the important part. THE PAIR TRADE IS NOT DEAD HERE, but
# it is ONLY available to a maker: crossing is arithmetically certain to lose,
# and resting both sides is arithmetically certain to win IF BOTH REST. So on
# this venue the pair channel collapses exactly onto
# ACTUAL_BETTOR_FILL_PROBABILITY -- the single unknown the whole programme is
# already blocked on. It does not add a new blocker; it removes a hoped-for
# way around the existing one.

def pair_basis(own_book, comp_book=None):
    """Both bases from the arriving book(s). NEVER one number.

    Returns {"AGGRESSIVE_PAIR_BASIS", "PASSIVE_PAIR_BASIS", ...}. A single
    "basis" would have to pick an execution style, and picking the wrong one
    is the difference between a trade that cannot win and a trade that cannot
    lose. NOT_IDENTIFIED where a side has no price -- an absent quote is not a
    bad price, it is no price.
    """
    def touch(b):
        md = ((b or {}).get("marketData") or {})
        bids, offs = md.get("bids") or [], md.get("offers") or []
        return (D(str(bids[0]["px"]["value"])) if bids else None,
                D(str(offs[0]["px"]["value"])) if offs else None)

    bid, ask = touch(own_book)
    if comp_book is not None and comp_book is not own_book:
        # Two genuinely separate books (another venue, or a venue that ever
        # quotes the complement independently). Kept because the engine must
        # not silently assume the single-book shape it happens to see today.
        cbid, cask = touch(comp_book)
        agg = ask + cask if ask is not None and cask is not None \
            else NOT_IDENTIFIED
        pas = bid + cbid if bid is not None and cbid is not None \
            else NOT_IDENTIFIED
        shape = "TWO_INDEPENDENT_BOOKS"
    else:
        agg = ask + (D("1") - bid) if bid is not None and ask is not None \
            else NOT_IDENTIFIED
        pas = bid + (D("1") - ask) if bid is not None and ask is not None \
            else NOT_IDENTIFIED
        shape = "ONE_BINARY_BOOK_BOTH_SIDES"
    return {
        "AGGRESSIVE_PAIR_BASIS": agg,
        "PASSIVE_PAIR_BASIS": pas,
        "COMPLEMENT_SHAPE": shape,
        "AGGRESSIVE_PAIR_PROFITABLE_BEFORE_FEES": (
            NOT_IDENTIFIED if agg == NOT_IDENTIFIED else agg < D("1")),
        "PASSIVE_PAIR_PROFITABLE_BEFORE_FEES": (
            NOT_IDENTIFIED if pas == NOT_IDENTIFIED else pas < D("1")),
        "PASSIVE_PAIR_REQUIRES_BOTH_RESTS_TO_FILL": True,
        "ACTUAL_BETTOR_FILL_PROBABILITY": NOT_IDENTIFIED,
    }


def observation(slug, own_book, comp_book, entry, now_iso, elapsed_s,
                event_id=NOT_IDENTIFIED, sport=NOT_IDENTIFIED,
                market_type=NOT_IDENTIFIED):
    """One fully observed decision tick, in the shape position_state expects.

    EVERY PROBE IS SET EXPLICITLY, including to False. Leaving one out would
    make it NOT_IDENTIFIED and block the comparison, which is the correct
    behaviour for a probe nobody read -- but here we DID read the book, so
    "no depth to cross" is INFEASIBLE and must be recorded as such.
    """
    md = (own_book or {}).get("marketData") or {}
    bids, offers = md.get("bids") or [], md.get("offers") or []
    cmd = (comp_book or {}).get("marketData") or {}
    cbids, coffers = cmd.get("bids") or [], cmd.get("offers") or []
    have_comp = comp_book is not None

    bid = D(str(bids[0]["px"]["value"])) if bids else None
    ask = D(str(offers[0]["px"]["value"])) if offers else None
    stats = md.get("stats") or {}

    return {
        "TIMESTAMP": now_iso,
        "MARKET_ID": slug,
        "EVENT_ID": event_id,
        "SPORT": sport,
        "MARKET_TYPE": market_type,
        "ELAPSED_S": elapsed_s,
        "ENTRY_STATE": entry,
        "TIME_UNPAIRED_S": entry.get("TIME_UNPAIRED_S", NOT_IDENTIFIED),
        "CURRENT_BOOK": {"bids": bids[:5], "offers": offers[:5]},
        "COMPLEMENT_BOOK": ({"bids": cbids[:5], "offers": coffers[:5]}
                            if have_comp else NOT_IDENTIFIED),
        "CURRENT_BID": bid if bid is not None else NOT_IDENTIFIED,
        "CURRENT_ASK": ask if ask is not None else NOT_IDENTIFIED,
        "CURRENT_SPREAD": (ask - bid if bid is not None and ask is not None
                           else NOT_IDENTIFIED),
        "CURRENT_DEPTH": {
            "BEST_BID_QTY": D(str(bids[0]["qty"])) if bids else NOT_IDENTIFIED,
            "BEST_ASK_QTY": (D(str(offers[0]["qty"])) if offers
                             else NOT_IDENTIFIED)},
        "COMPLEMENT_BID": (D(str(cbids[0]["px"]["value"])) if cbids
                           else NOT_IDENTIFIED),
        "COMPLEMENT_ASK": (D(str(coffers[0]["px"]["value"])) if coffers
                           else NOT_IDENTIFIED),
        # The complement is the SAME book's other side on this venue, so a
        # pair basis exists on every two-sided book -- with or without a
        # sibling contract. Both bases are carried; neither is "the" basis.
        "PAIR_BASIS": pair_basis(own_book, comp_book if have_comp else None),
        # COUNT AND ELAPSED TOGETHER. A recency alone is not a rate.
        "TRADE_ACTIVITY": {
            "SHARES_TRADED": stats.get("sharesTraded", NOT_IDENTIFIED),
            "LAST_TRADE_SET_TIME": stats.get("lastTradeSetTime",
                                             NOT_IDENTIFIED),
            "IS_NOT_A_FILL_RATE": True},
        "QUEUE_AHEAD": (D(str(bids[0]["qty"])) if bids else NOT_IDENTIFIED),
        # Fair value is NOT populated. The venue's own mid is its opinion
        # restated, and fair_value_status() classifies it as such.
        "FAIR_VALUE": NOT_IDENTIFIED,
        "FV_BASIS": NOT_IDENTIFIED,
        "CAPITAL_OCCUPANCY": entry.get("CAPITAL_OCCUPANCY", NOT_IDENTIFIED),
        "EVENT_EXPOSURE": entry.get("EVENT_EXPOSURE", NOT_IDENTIFIED),
        "CORRELATION_EXPOSURE": entry.get("CORRELATION_EXPOSURE",
                                          NOT_IDENTIFIED),
        # --- probes, every one set explicitly from the book we just read ---
        # Buying the complement IS trading the own book's other side, so the
        # probes read the own book when no separate complement exists.
        "COMPLEMENT_EXECUTABLE_NOW": bool(coffers if have_comp else bids),
        "COMPLEMENT_PASSIVE_PLACEABLE": bool(cbids if have_comp else offers),
        "PASSIVE_EXIT_PLACEABLE": bool(bids),
        "AGGRESSIVE_EXIT_DEPTH_EXISTS": bool(bids),
        # No hedge instrument has been identified on this venue. That is a
        # measured absence, not an unread probe.
        "HEDGE_INSTRUMENT_EXECUTABLE": False,
        "SETTLED": md.get("state") not in (None, "MARKET_STATE_OPEN"),
        "PAIR_COMPLETE": entry.get("PAIR_COMPLETE", False),
    }


def telemetry_row(obs, priors, horizon_minutes=1.0, position=None):
    """The full PHASE_2A row: state, both priors, every action, both blockers.

    No action is chosen and none is expected to be: fair value is
    NOT_IDENTIFIED, so every EV that needs it is unpriced and the allocator
    declines with a stated reason. The row is the product.
    """
    row = P.decision_row(obs, priors, horizon_minutes=horizon_minutes)
    bucket = row["TIME_UNPAIRED"]["BUCKET"]
    fmap = row["ACTION_FEASIBILITY"]
    play = P.actions_in_play(fmap)

    dual = P.dual_prior_block(priors, bucket, horizon_minutes,
                              feas=fmap, in_play=play)
    fv = P.fair_value_status(obs)

    row.update({
        "COLLECT_VERSION": COLLECT_VERSION,
        "PHASE": P.PHASE_2_STAGE,
        "MARKET_ID": obs.get("MARKET_ID", NOT_IDENTIFIED),
        "EVENT_ID": obs.get("EVENT_ID", NOT_IDENTIFIED),
        "SPORT": obs.get("SPORT", NOT_IDENTIFIED),
        "MARKET_TYPE": obs.get("MARKET_TYPE", NOT_IDENTIFIED),
        "CURRENT_BID": obs.get("CURRENT_BID", NOT_IDENTIFIED),
        "CURRENT_ASK": obs.get("CURRENT_ASK", NOT_IDENTIFIED),
        "CURRENT_SPREAD": obs.get("CURRENT_SPREAD", NOT_IDENTIFIED),
        "CURRENT_DEPTH": obs.get("CURRENT_DEPTH", NOT_IDENTIFIED),
        "COMPLEMENT_BID": obs.get("COMPLEMENT_BID", NOT_IDENTIFIED),
        "COMPLEMENT_ASK": obs.get("COMPLEMENT_ASK", NOT_IDENTIFIED),
        "EVENT_EXPOSURE": obs.get("EVENT_EXPOSURE", NOT_IDENTIFIED),
        "CORRELATION_EXPOSURE": obs.get("CORRELATION_EXPOSURE",
                                        NOT_IDENTIFIED),
        "FAIR_VALUE_STATUS": fv,
        "DUAL_PRIOR": dual,
        "ENTRY_PROVENANCE": (position or {}).get("ENTRY_PROVENANCE",
                                                 NOT_IDENTIFIED),
        "ADMISSIBLE_AS_STRATEGY_EVIDENCE":
            (position or {}).get("ADMISSIBLE_AS_STRATEGY_EVIDENCE",
                                 NOT_IDENTIFIED),
        "BETTOR_EXIT_ENGINE_PNL": P.BETTOR_EXIT_ENGINE_PNL,
        "PROFITABILITY_REPORTABLE": P.PROFITABILITY_REPORTABLE,
    })
    return row


def write_row(path, row):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as fh:
        fh.write(json.dumps(row, default=_jsonable, sort_keys=True) + "\n")
    return p


def summarise(rows):
    """The PHASE_2A report fields -- and NONE of the forbidden ones.

    No profitability, no win rate, no expected monthly return. Those need a
    sample and a counterfactual execution methodology that do not exist yet,
    and reporting them early is how a plumbing test becomes a business case.
    """
    blocked_fv = blocked_fill = blocked_other = priced = 0
    disagree = agree = 0
    for r in rows:
        reason = r.get("ACTION_REASON")
        if reason == P.DOMINATES or reason == P.ROBUSTLY_DOMINATES:
            priced += 1
        elif r.get("FAIR_VALUE_STATUS") != P.FV_VALIDATED:
            blocked_fv += 1
        elif reason == P.FEASIBILITY_NOT_IDENTIFIED:
            blocked_fill += 1
        else:
            blocked_other += 1
        d = (r.get("DUAL_PRIOR") or {}).get("ACTION_DISAGREEMENT")
        if d == "YES":
            disagree += 1
        elif d == "NO":
            agree += 1
    return {
        "PHASE": P.PHASE_2_STAGE,
        "TELEMETRY_ROWS": len(rows),
        "POSITIONS_WITH_ALL_ACTIONS_PRICED": priced,
        "POSITIONS_BLOCKED_BY_FAIR_VALUE": blocked_fv,
        "POSITIONS_BLOCKED_BY_FILL_UNCERTAINTY": blocked_fill,
        "POSITIONS_BLOCKED_BY_OTHER_UNKNOWN": blocked_other,
        "PRIOR3_VS_PRIOR4_ACTION_DISAGREEMENT": disagree,
        "PRIOR3_VS_PRIOR4_ACTION_AGREEMENT": agree,
        "PROFITABILITY": "NOT_REPORTABLE_THIS_PHASE",
        "WIN_RATE": "NOT_REPORTABLE_THIS_PHASE",
        "EXPECTED_MONTHLY_RETURN": "NOT_REPORTABLE_THIS_PHASE",
        "WHY": ("PHASE_2A is market state and decision telemetry. A "
                "profitability number needs a counterfactual execution "
                "methodology and a sample, and neither exists yet."),
    }
