#!/usr/bin/env python3
"""THE NO-SUBMIT REHEARSAL. The whole path, run to its end, over sealed rows.

WHAT A REHEARSAL PROVES AND WHAT IT CANNOT. It proves the machinery runs: a
candidate becomes an EV receipt, an EV receipt becomes an order intent or a
refusal, an intent meets fifteen risk gates, and the run ends in an explicit
WOULD_SUBMIT or WOULD_NOT_SUBMIT with the reasons attached. It proves nothing
whatever about whether such an order would have filled or made money. The
public book shows the queue we would have joined, never our place in it.

EXPECT MOST CANDIDATES TO END IN NO_TRADE TODAY, and that is the correct
result rather than a disappointing one. BETTOR has no independent fair value
wired into this harness and no admitted-fill history to source P_FILL from, so
the EV-critical terms are genuinely NOT_IDENTIFIED. A rehearsal that produced
confident TRADE verdicts from those inputs would be reporting a number it had
invented.

THE EVIDENCE IS SEALED AND PROSPECTIVE. Rows come from a capture that was
frozen before it ran; this module re-reads them and cannot re-select.

This module contacts nothing and can place no order.
"""
import argparse
import json
from decimal import Decimal as D
from pathlib import Path

import micro_live as ML
import micro_live_ev as MEV
import micro_live_risk as MR
import micro_live_telemetry as MT

NOT_IDENTIFIED = "NOT_IDENTIFIED"

ORDER_TYPE = "MAKER_QUOTE"
SIDE = "BUY"
PASSIVE_ONLY = True
WHY_PASSIVE_ONLY = (
    "the first live test is a maker experiment; a taker cross answers a "
    "different question and pays the spread to ask it")
SIZE_NOT_AUTHORIZED = "SIZE_NOT_AUTHORIZED"

# What the rehearsal supplies, and what it refuses to supply.
FAIR_VALUE_STATUS = "NOT_IDENTIFIED_NO_INDEPENDENT_MODEL_WIRED"
WHY_NO_FAIR_VALUE = (
    "using the price of the market we are trading as evidence that the market "
    "is mispriced is circular; BETTOR's independent estimate is not wired into "
    "this harness yet, so fair value is absent rather than borrowed")
P_FILL_STATUS = "NOT_IDENTIFIED_NO_ADMITTED_BETTOR_FILLS"
WHY_NO_P_FILL = (
    "BETTOR has no admitted fills to estimate from, and the whale completion "
    "rate is forbidden as a substitute: it measures whether somebody else's "
    "counterparty turned up, on another venue, for trades they chose to open")


def _d(x):
    try:
        return D(str(x))
    except Exception:                                         # noqa: BLE001
        return None


def candidates_from_rows(rows, limit=10):
    """Candidates drawn round-robin across markets, in observation order.

    ROUND-ROBIN ACROSS MARKETS, then forward in time: market A's first
    two-sided observation, market B's first, and so on, before anyone's second.
    That spreads the rehearsal across the roster instead of spending it all on
    whichever market the file happened to list first.

    IN OBSERVATION ORDER, NEVER BY SPREAD. Ranking candidates by how attractive
    the book looked would select the moments that flatter the rehearsal and
    then report a rehearsal measured on them -- the same circularity the
    selection rules already refuse. A candidate is the Nth time we looked, not
    the Nth best thing we saw.
    """
    by_market = {}
    order = []
    for r in rows:
        if r.get("kind") != "TICK":
            continue
        bid, ask = r.get("BID"), r.get("ASK")
        if bid in (None, NOT_IDENTIFIED) or ask in (None, NOT_IDENTIFIED):
            continue
        slug = r.get("MARKET_SLUG") or r.get("slug")
        if slug not in by_market:
            by_market[slug] = []
            order.append(slug)
        by_market[slug].append(r)

    out, depth = [], 0
    while len(out) < limit:
        took = False
        for slug in order:
            if depth < len(by_market[slug]):
                out.append(by_market[slug][depth])
                took = True
                if len(out) >= limit:
                    break
        if not took:
            break
        depth += 1
    return out


def rehearse_one(row, limits=None, clock=None):
    """One candidate, the whole path, ending in an explicit verdict."""
    slug = row.get("MARKET_SLUG") or row.get("slug")
    event_id = row.get("EVENT_ID", NOT_IDENTIFIED)
    market_id = row.get("MARKET_ID", NOT_IDENTIFIED)
    book_ts = row.get("RECEIPT_UTC", NOT_IDENTIFIED)
    decided_at = clock() if clock else ML.utcnow()

    did = ML.decision_id(event_id, market_id, SIDE, decided_at)
    life = ML.Lifecycle(did, clock=(clock or ML.utcnow))

    bid, ask = _d(row.get("BID")), _d(row.get("ASK"))
    # THE PASSIVE QUOTE: join the bid, never cross it. A maker order that
    # crosses is a taker order with a maker's name on it.
    limit_price = bid

    ev = MEV.receipt(
        event_id=event_id, market_id=market_id, market_slug=slug,
        side=SIDE, order_type=ORDER_TYPE,
        limit_price=(str(limit_price) if limit_price is not None
                     else NOT_IDENTIFIED),
        quantity=SIZE_NOT_AUTHORIZED,
        book_timestamp=book_ts, decision_timestamp=decided_at,
        fair_value=NOT_IDENTIFIED, fair_value_status=FAIR_VALUE_STATUS,
        p_fill_value=NOT_IDENTIFIED, p_fill_source=NOT_IDENTIFIED,
        p_fill_status=P_FILL_STATUS,
        expected_fees=NOT_IDENTIFIED,
        no_fill_state="NO_EXPOSURE_CARRIED",
        ev_if_no_fill=NOT_IDENTIFIED)
    ev["WHY_NO_FAIR_VALUE"] = WHY_NO_FAIR_VALUE
    ev["WHY_NO_P_FILL"] = WHY_NO_P_FILL
    life.transition("EV_EVALUATED", "EV receipt built")

    if ev["DECISION"] == MEV.NO_TRADE:
        life.transition("ADMISSION_FAIL",
                        "EV-critical terms NOT_IDENTIFIED: %s"
                        % ", ".join(ev["NOT_IDENTIFIED_TERMS"]))
        risk = MR.evaluate(limits or MR.unset_limits(), {})
        return _pack(row, life, ev, risk, None, "WOULD_NOT_SUBMIT",
                     "NO_TRADE: no valid EV comparison exists")

    life.transition("ADMISSION_PASS", "EV comparison valid")
    intent_id = ML.order_intent_id(did, limit_price, SIZE_NOT_AUTHORIZED,
                                   ORDER_TYPE)
    life.transition("ORDER_PROPOSED", "passive maker quote constructed")
    risk = MR.evaluate(limits or MR.unset_limits(), {})
    if risk["RISK_VERDICT"] != "RISK_APPROVED":
        life.transition("RISK_REJECTED",
                        "limits not set: %s" % ", ".join(risk["LIMITS_NOT_SET"]
                                                         [:3]))
        return _pack(row, life, ev, risk, intent_id, "WOULD_NOT_SUBMIT",
                     "risk gates not satisfied")
    life.transition("RISK_APPROVED", "all gates pass")
    life.transition("DRY_RUN_READY", "instruction constructed")
    life.transition("WOULD_SUBMIT", "everything upstream of submission passed")
    return _pack(row, life, ev, risk, intent_id, "WOULD_SUBMIT",
                 "all gates passed; submission remains structurally disabled")


def _pack(row, life, ev, risk, intent_id, verdict, why):
    slug = row.get("MARKET_SLUG") or row.get("slug")
    return {
        "MARKET_SLUG": slug,
        "EVENT_ID": row.get("EVENT_ID", NOT_IDENTIFIED),
        "MARKET_ID": row.get("MARKET_ID", NOT_IDENTIFIED),
        "DECISION": ev["DECISION"],
        "PROPOSED_PRICE": ev["LIMIT_PRICE"],
        "PROPOSED_SIZE": SIZE_NOT_AUTHORIZED,
        "ORDER_TYPE": ORDER_TYPE,
        "PASSIVE_ONLY": PASSIVE_ONLY,
        "WHY_PASSIVE_ONLY": WHY_PASSIVE_ONLY,
        "EV_RECEIPT": ev,
        "RISK_RECEIPT": risk,
        "ORDER_INTENT_ID": intent_id or NOT_IDENTIFIED,
        "LIFECYCLE": life.audit(),
        "TELEMETRY_SLOT": MT.blank(),
        "VERDICT": verdict,
        "WHY_VERDICT": why,
        "ORDER_SENT": False,
        "MICRO_LIVE_MODE": ML.MICRO_LIVE_MODE,
        "LIVE_ORDER_SUBMISSION": ML.LIVE_ORDER_SUBMISSION,
    }


def rehearse(rows, limit=10, limits=None, clock=None):
    reg = ML.IntentRegistry()
    cands = candidates_from_rows(rows, limit)
    results = [rehearse_one(r, limits, clock) for r in cands]
    for r in results:
        if r["ORDER_INTENT_ID"] != NOT_IDENTIFIED:
            reg.register(r["ORDER_INTENT_ID"], {"SLUG": r["MARKET_SLUG"]})
    would = [r for r in results if r["VERDICT"] == "WOULD_SUBMIT"]
    ks = ML.kill_switch(conditions=None)
    return {
        "CANDIDATES_TESTED": len(results),
        "RESULTS": results,
        "WOULD_SUBMIT": len(would),
        "WOULD_NOT_SUBMIT": len(results) - len(would),
        "TRADE_DECISIONS": sum(1 for r in results
                               if r["DECISION"] == MEV.TRADE),
        "NO_TRADE_DECISIONS": sum(1 for r in results
                                  if r["DECISION"] == MEV.NO_TRADE),
        "DISTINCT_INTENTS_REGISTERED": len(reg),
        "KILL_SWITCH": ks,
        "ORDERS_SENT": 0,
        "ORDERS_SENT_IS_STRUCTURAL": ML.WHY_NO_SUBMIT_FUNCTION,
        "MICRO_LIVE_MODE": ML.MICRO_LIVE_MODE,
        "LIVE_ORDER_SUBMISSION": ML.LIVE_ORDER_SUBMISSION,
        "mirror_live": ML.MIRROR_LIVE,
        "WHAT_THIS_PROVES": (
            "the machinery runs end to end and refuses correctly"),
        "WHAT_THIS_DOES_NOT_PROVE": (
            "that any such order would fill, or make money"),
    }


def render(r):
    L = ["%-30s %-10s %-12s %-18s %s" % ("MARKET", "DECISION", "PRICE",
                                         "SIZE", "VERDICT")]
    for x in r["RESULTS"]:
        L.append("%-30s %-10s %-12s %-18s %s" % (
            str(x["MARKET_SLUG"])[:30], x["DECISION"], x["PROPOSED_PRICE"],
            x["PROPOSED_SIZE"], x["VERDICT"]))
    L += ["",
          "%-38s = %s" % ("CANDIDATES_TESTED", r["CANDIDATES_TESTED"]),
          "%-38s = %s" % ("WOULD_SUBMIT", r["WOULD_SUBMIT"]),
          "%-38s = %s" % ("WOULD_NOT_SUBMIT", r["WOULD_NOT_SUBMIT"]),
          "%-38s = %s" % ("ORDERS_SENT", r["ORDERS_SENT"]),
          "%-38s = %s" % ("MICRO_LIVE_MODE", r["MICRO_LIVE_MODE"]),
          "%-38s = %s" % ("LIVE_ORDER_SUBMISSION",
                          r["LIVE_ORDER_SUBMISSION"])]
    return "\n".join(L)


def read_rows(path):
    return [json.loads(l) for l in Path(path).read_text().splitlines()
            if l.strip()]


def _cli():                                                   # pragma: no cover
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", required=True)
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    r = rehearse(read_rows(a.rows), a.limit)
    Path(a.out).write_text(json.dumps(r, indent=1, sort_keys=True,
                                      default=str))
    print(render(r))


if __name__ == "__main__":                                    # pragma: no cover
    _cli()
