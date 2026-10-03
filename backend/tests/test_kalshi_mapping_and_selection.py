"""KALSHI CONTRACT EQUIVALENCE, VENUE SELECTION AND THE PAPER/LIVE VIEWS.

  mapping      ESTABLISHED only from structured evidence on both sides;
               title-only similarity is refused and the missing elements
               are named; a short maps to the explicitly declared
               complement outcome only when its payoff vector matches
  settlement   per-state payoff vectors: overtime, draw, void, push and
               postponement windows; unequal or unknown -> NOT_ESTABLISHED
  selection    net EV after depth-walk slippage, entry fee, exit cost and
               other costs; both / one / neither venue; never by preference
  linkage      execmirror's sizing columns on a Kalshi live intent;
               Xavier's position from venue fills only; Audrey's diff
"""
from __future__ import annotations

import copy
import datetime as dt
from decimal import Decimal

from sportsassets import kalshi_linkage as KL
from sportsassets import kalshi_mapping as KM
from sportsassets import kalshi_orders as KO
from sportsassets import venue_selection as VS

from tests import kalshi_fixtures as F

START = "2026-10-03T23:05:00+00:00"
MLB_RULES = {"overtime_included": True, "draw_rule": "IMPOSSIBLE",
             "void_rule": "STAKE_BACK", "postponement_window_hours": 48,
             "postponement_payout": "STAKE_BACK"}


def _event(**k):
    e = {"league": "MLB", "home_team": "NYY", "away_team": "BOS", "start_time": START}
    e.update(k)
    return e


def bettor(**k):
    b = {"us_market_slug": "aec-mlb-bos-nyy-2026-10-03",
         "provider_event_id": "pin:1612345", "event": _event(),
         "market_type": "MONEYLINE", "period": "FULL_GAME",
         "outcome": {"team": "NYY"}, "complement_outcome": {"team": "BOS"},
         "settlement": dict(MLB_RULES), "title": "Red Sox at Yankees"}
    b.update(k)
    return b


def kalshi(**k):
    m = {"ticker": F.TICKER_NYY, "event_ticker": F.EVENT,
         "event": _event(start_time="2026-10-03T23:10:00Z"),
         "market_type": "MONEYLINE", "period": "FULL_GAME",
         "outcome": {"team": "NYY"}, "settlement": dict(MLB_RULES),
         "title": "Boston vs New York Y Winner?", "yes_sub_title": "New York Y"}
    m.update(k)
    return m


# ───────────────────────────── identity ─────────────────────────────

def test_structured_agreement_is_established_and_titles_are_ignored():
    v = KM.establish(bettor(), kalshi())
    assert v.verdict == KM.ESTABLISHED, (v.missing, v.mismatched)
    assert v.kalshi_ticker == F.TICKER_NYY
    assert any(n.startswith("TITLES_IGNORED") for n in v.notes)


def test_title_only_similarity_is_never_equivalence():
    same = "Boston Red Sox at New York Yankees"
    b = {"us_market_slug": "aec-mlb-bos-nyy-2026-10-03", "title": same,
         "outcome_title": "New York Yankees"}
    k = {"ticker": F.TICKER_NYY, "title": same, "yes_sub_title": "New York Yankees"}
    v = KM.establish(b, k)
    assert v.verdict == KM.NOT_ESTABLISHED
    for need in ("bettor.event.league", "kalshi.event.home_team",
                 "bettor.event.start_time", "kalshi.event.start_time",
                 "bettor.market_type", "kalshi.period", "bettor.outcome",
                 "kalshi.outcome", "bettor.settlement.overtime_included",
                 "kalshi.settlement.void_rule"):
        assert need in v.missing, need


def test_titles_cannot_rescue_a_structural_gap():
    k = kalshi()
    del k["event"]["start_time"]
    v = KM.establish(bettor(title=k["title"]), k)
    assert v.verdict == KM.NOT_ESTABLISHED
    assert v.missing == ["kalshi.event.start_time"]


def test_a_naive_start_time_is_not_evidence():
    v = KM.establish(bettor(), kalshi(event=_event(start_time=dt.datetime(2026, 10, 3, 23, 5))))
    assert "kalshi.event.start_time" in v.missing


def test_each_identity_mismatch_is_named():
    cases = {
        "event.home_team": kalshi(event=_event(home_team="BOS", away_team="NYY")),
        "event.league": kalshi(event=_event(league="NBA")),
        "event.start_time": kalshi(event=_event(start_time="2026-10-04T01:05:00Z")),
        "market_type": kalshi(market_type="SPREAD", outcome={"team": "NYY", "line": "-1.5"}),
        "period": kalshi(period="FIRST_5_INNINGS"),
        "outcome": kalshi(outcome={"team": "BOS"}),
    }
    for want, k in cases.items():
        v = KM.establish(bettor(), k)
        assert v.verdict == KM.NOT_ESTABLISHED and want in v.mismatched, (want, v)


def test_the_start_tolerance_is_explicit():
    k = kalshi(event=_event(start_time="2026-10-03T23:25:00Z"))       # +20 min
    assert not KM.establish(bettor(), k).established
    assert KM.establish(bettor(), k, start_tolerance_s=30 * 60).established


def test_a_spread_needs_the_same_line():
    b = bettor(market_type="SPREAD", outcome={"team": "NYY", "line": "-1.5"},
               complement_outcome={"team": "BOS", "line": "1.5"})
    ok = kalshi(market_type="SPREAD", outcome={"team": "NYY", "line": "-1.50"})
    assert KM.establish(b, ok).established
    bad = kalshi(market_type="SPREAD", outcome={"team": "NYY", "line": "-2.5"})
    assert "outcome" in KM.establish(b, bad).mismatched
    noline = kalshi(market_type="SPREAD", outcome={"team": "NYY"})
    assert "kalshi.outcome" in KM.establish(b, noline).missing


# ───────────────────────────── settlement ─────────────────────────────

def test_overtime_inclusion_mismatch_is_refused():
    s = dict(MLB_RULES, overtime_included=False)
    v = KM.establish(bettor(), kalshi(settlement=s))
    assert not v.established
    assert set(v.settlement.unequal_states) >= {KM.S_OT_WIN}
    assert any(m.startswith("settlement:") for m in v.mismatched)


def test_a_postponement_window_difference_is_refused_in_the_band():
    s = dict(MLB_RULES, postponement_window_hours=336)     # two weeks vs 48h
    v = KM.settlement_compatibility(bettor(), kalshi(settlement=s))
    assert v.verdict == KM.NOT_ESTABLISHED
    assert v.unequal_states == ["COMPLETED_AFTER_DELAY_48H_TO_336H"]
    assert v.bettor_vector["COMPLETED_AFTER_DELAY_0H_TO_48H"] == "FOLLOWS_RESULT"


def test_void_rules_must_pay_the_same():
    s = dict(MLB_RULES, void_rule="LAST_FAIR_PRICE", postponement_payout="LAST_FAIR_PRICE")
    v = KM.settlement_compatibility(bettor(), kalshi(settlement=s))
    assert KM.S_CANCELLED in v.unequal_states and not v.verdict == KM.ESTABLISHED


def test_an_unknown_rule_is_not_established():
    s = dict(MLB_RULES)
    del s["void_rule"]
    v = KM.settlement_compatibility(bettor(), kalshi(settlement=s))
    assert v.verdict == KM.NOT_ESTABLISHED
    assert "kalshi.settlement.void_rule" in v.missing_fields
    assert KM.S_CANCELLED in v.unknown_states
    s = dict(MLB_RULES, overtime_included="yes")              # not a boolean
    assert KM.settlement_compatibility(bettor(), kalshi(settlement=s)).unknown_states


def test_push_handling_on_whole_and_half_lines():
    b = bettor(market_type="TOTAL", outcome={"side": "OVER", "line": "8"},
               settlement=dict(MLB_RULES, push_rule="STAKE_BACK"))
    k = kalshi(market_type="TOTAL", outcome={"side": "OVER", "line": "8"},
               settlement=dict(MLB_RULES, push_rule="RESOLVES_NO"))
    v = KM.establish(b, k)
    assert not v.established and KM.S_PUSH in v.settlement.unequal_states
    half_b = bettor(market_type="TOTAL", outcome={"side": "OVER", "line": "8.5"})
    half_k = kalshi(market_type="TOTAL", outcome={"side": "OVER", "line": "8.5"})
    hv = KM.establish(half_b, half_k)
    assert hv.established and hv.settlement.kalshi_vector[KM.S_PUSH] == "IMPOSSIBLE"


def test_a_short_maps_to_the_declared_complement_when_payoffs_match():
    k_bos = kalshi(ticker=F.TICKER_BOS, outcome={"team": "BOS"})
    v = KM.establish(bettor(), k_bos, holding="SHORT")
    assert v.established and v.kalshi_ticker == F.TICKER_BOS
    # the long mapping to the same market is (correctly) refused
    assert "outcome" in KM.establish(bettor(), k_bos).mismatched


def test_a_short_without_a_declared_complement_is_refused():
    b = bettor()
    del b["complement_outcome"]
    v = KM.establish(b, kalshi(ticker=F.TICKER_BOS, outcome={"team": "BOS"}),
                     holding="SHORT")
    assert "bettor.complement_outcome" in v.missing


def test_a_three_way_short_is_not_the_other_side():
    soccer = {"overtime_included": False, "draw_rule": "RESOLVES_NO",
              "void_rule": "STAKE_BACK", "postponement_window_hours": 48,
              "postponement_payout": "STAKE_BACK"}
    ev = _event(league="EPL", home_team="ARS", away_team="CHE")
    b = bettor(event=ev, outcome={"team": "ARS"}, complement_outcome={"team": "CHE"},
               settlement=soccer, period="REGULATION")
    k = kalshi(event=ev, ticker="KXEPLGAME-X-CHE", outcome={"team": "CHE"},
               settlement=soccer, period="REGULATION")
    v = KM.establish(b, k, holding="SHORT")
    assert not v.established and KM.S_DRAW in v.settlement.unequal_states


def test_find_target_wants_exactly_one():
    good = kalshi()
    other = kalshi(ticker=F.TICKER_BOS, outcome={"team": "BOS"})
    assert KM.find_target(bettor(), [other, good]).kalshi_ticker == F.TICKER_NYY
    dup = copy.deepcopy(good)
    dup["ticker"] = "KXMLBGAME-DUP-NYY"
    amb = KM.find_target(bettor(), [good, dup])
    assert not amb.established and amb.mismatched[0].startswith("AMBIGUOUS")
    none = KM.find_target(bettor(), [other])
    assert not none.established and none.missing == ["no candidate established"]


def test_a_verdict_feeds_translation():
    v = KM.establish(bettor(), kalshi())
    o = {"mirror_id": "km:paperord:9", "intent": "BUY_LONG",
         "wire_price": Decimal("0.61"), "qty": Decimal("3000"),
         "time_in_force": "IOC", "order_type": "MARKETABLE"}
    assert KO.plan(o, v)["payload"]["ticker"] == F.TICKER_NYY
    bad = KM.establish(bettor(), kalshi(period="FIRST_5_INNINGS"))
    assert KO.plan(o, bad)["exclusion"] == KO.MAPPING_NOT_ESTABLISHED


# ───────────────────────────── venue selection ─────────────────────────────

OPP = {"opportunity_id": "op1", "fair_probability": Decimal("0.60"), "count": 10,
       "limit_price": Decimal("0.56")}


def _kfee(count, price):
    return KO.fee_for(count, price)


def venue(name, asks, **k):
    v = {"venue": name, "mapping_verdict": "ESTABLISHED",
         "settlement_verdict": "ESTABLISHED", "asks": asks, "fee_fn": _kfee,
         "min_count": 1, "exit_cost_per_contract": Decimal("0.005"),
         "other_costs": Decimal(0), "book_age_s": 1, "max_book_age_s": 5}
    v.update(k)
    return v


def test_the_depth_walk_and_its_slippage():
    w = VS.walk_book([(Decimal("0.52"), 4), (Decimal("0.53"), 10), (Decimal("0.60"), 50)],
                     10, Decimal("0.56"))
    assert w["filled"] == 10 and w["best"] == Decimal("0.52")
    assert w["cost"] == Decimal("5.26") and w["slippage"] == Decimal("0.06")
    capped = VS.walk_book([(Decimal("0.52"), 4), (Decimal("0.60"), 50)], 10,
                          Decimal("0.56"))
    assert capped["filled"] == 4                          # the limit stops the walk


def test_net_ev_subtracts_every_cost():
    e = VS.evaluate(OPP, venue("KALSHI", [(Decimal("0.52"), 4), (Decimal("0.53"), 10)]))
    gross = (Decimal("0.60") - Decimal("0.52")) * 10                  # 0.80
    fee = KO.fee_for(4, Decimal("0.52")) + KO.fee_for(6, Decimal("0.53"))
    assert e["gross_ev"] == gross and e["entry_fee"] == fee
    assert e["net_ev"] == gross - Decimal("0.06") - fee - Decimal("0.05")
    assert e["eligible"]


def test_both_venues_valid_the_higher_net_ev_wins():
    k = venue("KALSHI", [(Decimal("0.50"), 20)])
    p = venue("POLYMARKET_US", [(Decimal("0.53"), 20)], fee_fn=lambda c, px: Decimal(0))
    got = VS.select(OPP, [p, k])
    assert got["decision"] == VS.CHOSEN and got["venue"] == "KALSHI"
    assert got["why"] == "HIGHER_NET_EV" and got["runner_up"]["venue"] == "POLYMARKET_US"
    # make Polymarket US cheaper and the choice follows the numbers
    p2 = venue("POLYMARKET_US", [(Decimal("0.48"), 20)], fee_fn=lambda c, px: Decimal(0))
    assert VS.select(OPP, [k, p2])["venue"] == "POLYMARKET_US"


def test_an_exact_tie_is_refused_rather_than_preferred():
    a = venue("KALSHI", [(Decimal("0.50"), 20)], fee_fn=lambda c, px: Decimal(0))
    b = venue("POLYMARKET_US", [(Decimal("0.50"), 20)], fee_fn=lambda c, px: Decimal(0))
    got = VS.select(OPP, [a, b])
    assert got["decision"] == VS.REFUSED
    assert got["reasons"] == {"_": ["EQUAL_NET_EV_NO_PREFERENCE"]}


def test_one_established_venue_is_used_when_its_economics_qualify():
    k = venue("KALSHI", [(Decimal("0.50"), 20)])
    p = venue("POLYMARKET_US", [(Decimal("0.40"), 20)], mapping_verdict="NOT_ESTABLISHED")
    got = VS.select(OPP, [p, k])
    assert got["decision"] == VS.CHOSEN and got["venue"] == "KALSHI"
    assert got["why"] == "ONLY_ELIGIBLE_VENUE"


def test_one_established_venue_with_no_net_edge_is_refused():
    k = venue("KALSHI", [(Decimal("0.59"), 20)], limit_price=None)
    p = venue("POLYMARKET_US", [(Decimal("0.40"), 20)], settlement_verdict="NOT_ESTABLISHED")
    got = VS.select(dict(OPP, limit_price=Decimal("0.60")), [k, p])
    assert got["decision"] == VS.REFUSED
    assert "NET_EV_NOT_POSITIVE" in got["reasons"]["KALSHI"]
    assert got["reasons"]["POLYMARKET_US"] == ["SETTLEMENT_NOT_ESTABLISHED"]


def test_neither_venue_is_refused_with_every_reason():
    k = venue("KALSHI", [], mapping_verdict="NOT_ESTABLISHED", book_age_s=30)
    p = venue("POLYMARKET_US", [(Decimal("0.50"), 20)], fee_fn=None,
              exit_cost_per_contract=None)
    got = VS.select(OPP, [k, p])
    assert got["decision"] == VS.REFUSED and got["venue"] is None
    assert set(got["reasons"]["KALSHI"]) == {"MAPPING_NOT_ESTABLISHED", "BOOK_NOT_FRESH",
                                             "INSUFFICIENT_DEPTH_AT_LIMIT"}
    assert set(got["reasons"]["POLYMARKET_US"]) == {"FEE_UNKNOWN", "EXIT_COST_UNKNOWN"}
    assert VS.select(OPP, [])["decision"] == VS.REFUSED


def test_an_unverified_mapping_on_the_better_venue_sends_it_to_the_other():
    k = venue("KALSHI", [(Decimal("0.40"), 20)], mapping_verdict="NOT_ESTABLISHED")
    p = venue("POLYMARKET_US", [(Decimal("0.52"), 20)])
    got = VS.select(OPP, [k, p])
    assert got["venue"] == "POLYMARKET_US"
    kev = [e for e in got["evaluations"] if e["venue"] == "KALSHI"][0]
    assert kev["net_ev"] is None and kev["reasons"] == ["MAPPING_NOT_ESTABLISHED"]


# ───────────────────────────── linkage, Xavier, Audrey ─────────────────────────────

def _paper_order(**k):
    o = {"order_id": "paperord:1", "decision_id": "paperdec:1", "group_id": "paper_g1",
         "role": "ENTRY", "intent": "ORDER_INTENT_BUY_LONG", "mirror_id": "km:paperord:1",
         "wire_price": Decimal("0.45"), "qty": Decimal("3000"),
         "time_in_force": "IOC", "order_type": "MARKETABLE",
         "us_market_slug": "aec-mlb-bos-nyy-2026-10-03",
         "decided_at": dt.datetime(2026, 10, 3, 19, 6, tzinfo=dt.timezone.utc)}
    o.update(k)
    return o


def _link(**k):
    po = _paper_order(**k)
    plan = KO.plan(po, KM.establish(bettor(), kalshi()))
    return po, plan, KL.build_link(po, plan, paper_fill_ids=["paperfill:1"])


def test_the_link_record_carries_execmirror_sizing_columns():
    _, plan, link = _link()
    assert link["venue"] == "KALSHI" and link["link_id"] == "kl:paperord:1"
    assert link["paper_decision_id"] == "paperdec:1"
    assert link["paper_fill_ids"] == ["paperfill:1"]
    assert (link["paper_qty"], link["scale"], link["raw_scaled_qty"],
            link["rounded_qty"], link["rounding_delta"], link["venue_minimum"]) == (
        Decimal("3000"), Decimal("1000"), Decimal("3"), 3, Decimal("0"), 1)
    assert link["live_notional"] == Decimal("1.35") and link["exclusion"] is None
    assert link["client_order_id"] == plan["client_order_id"]
    _, _, excl = _link(qty=Decimal("400"), mirror_id="km:paperord:2", order_id="paperord:2")
    assert excl["exclusion"] == "BELOW_VENUE_MINIMUM" and excl["rounded_qty"] == 0
    assert excl["live_notional"] is None and excl["raw_scaled_qty"] == Decimal("0.4")


def test_xavier_sees_venue_fills_only():
    # an accepted order with an ack fill count is NOT a position
    ack = KO.parse_ack(F.ACK_EXECUTED, 2)
    assert ack["outcome"] == "ACCEPTED"
    assert KL.xavier_positions([]) == []
    fills = [F.fill("t1", "ord-1", count="3.00", price="0.4500", fee="0.05"),
             F.fill("t1", "ord-1", count="3.00", price="0.4500", fee="0.05"),   # dup
             F.fill("t2", "ord-1", count="2.00", price="0.4700", fee="0.03"),
             F.fill("t3", "ord-2", action="sell", count="1.00", price="0.5000",
                    fee="0.01", created="2026-10-03T20:00:00Z")]
    (pos,) = KL.xavier_positions(fills)
    assert pos == {"venue": "KALSHI", "ticker": F.TICKER_NYY, "side": "yes",
                   "count": Decimal("4.00"), "avg_price": Decimal("0.4580"),
                   "fees_usd": Decimal("0.09"), "fees_complete": True, "fills": 3,
                   "last_fill_at": "2026-10-03T20:00:00Z", "source": "VENUE_FILLS"}
    (p2,) = KL.xavier_positions([F.fill("t9", "o", fee=None)])
    assert p2["fees_complete"] is False
    flat = [F.fill("a", "o"), F.fill("b", "o", action="sell")]
    assert KL.xavier_positions(flat) == []


def test_audrey_diffs_price_quantity_fees_and_timing():
    _, _, link = _link()
    link["venue_order_id"] = "ord-1"
    paper = [{"fill_id": "paperfill:1", "order_id": "paperord:1", "qty": Decimal("3000"),
              "price": Decimal("0.45"), "fee_usd": Decimal("30"),
              "filled_at": dt.datetime(2026, 10, 3, 19, 6, 0, tzinfo=dt.timezone.utc)}]
    live = [F.fill("t1", "ord-1", count="2.00", price="0.4500", fee="0.02",
                   created="2026-10-03T19:06:02Z"),
            F.fill("t2", "ord-1", count="1.00", price="0.4600", fee="0.01",
                   created="2026-10-03T19:06:03Z")]
    d = KL.audrey_diff([link], paper, live)
    (r,) = d["rows"]
    assert r["expected_live_qty"] == 3 and r["live_qty"] == Decimal(3)
    assert r["qty_diff"] == 0 and r["paper_price"] == Decimal("0.4500")
    assert r["live_price"] == Decimal("0.4533")
    assert r["price_diff"] == Decimal("0.0033")
    assert r["paper_fee_scaled"] == Decimal("0.03") and r["live_fee"] == Decimal("0.03")
    assert r["fee_diff"] == 0 and r["timing_s"] == 2.0
    assert r["codes"] == [] and d["clean"] is True


def test_audrey_names_every_missing_link():
    _, _, linked = _link()
    linked["venue_order_id"] = "ord-1"
    _, _, unsent = _link(order_id="paperord:2", mirror_id="km:paperord:2")
    _, _, excl = _link(order_id="paperord:3", mirror_id="km:paperord:3",
                       qty=Decimal("100"))
    paper = [{"fill_id": "pf1", "order_id": "paperord:1", "qty": 3000,
              "price": "0.45", "fee_usd": "0", "filled_at": "2026-10-03T19:06:00Z"},
             {"fill_id": "pf2", "order_id": "paperord:2", "qty": 3000,
              "price": "0.45", "fee_usd": "0", "filled_at": "2026-10-03T19:06:00Z"},
             {"fill_id": "pf9", "order_id": "paperord:9", "qty": 1000,
              "price": "0.45", "fee_usd": "0", "filled_at": "2026-10-03T19:06:00Z"}]
    live = [F.fill("t1", "ord-1", count="2.00", fee=None),
            F.fill("tx", "ord-stray")]
    d = KL.audrey_diff([linked, unsent, excl], paper, live)
    codes = {r["paper_order_id"]: r["codes"] for r in d["rows"]}
    assert codes["paperord:1"] == ["LIVE_FEE_UNSTATED", "QTY_DIFF"]
    assert codes["paperord:2"] == ["NO_LIVE_ORDER"]
    assert codes["paperord:3"] == ["EXCLUDED:BELOW_VENUE_MINIMUM"]
    kinds = {(i["code"], i.get("order_id") or i.get("paper_order_id"))
             for i in d["missing_links"]}
    assert kinds == {("UNLINKED_LIVE_FILL", "ord-stray"),
                     ("PAPER_FILL_WITHOUT_LINK", "paperord:9")}
    assert d["clean"] is False


def test_a_live_fill_without_a_paper_fill_is_named():
    _, _, link = _link()
    link["venue_order_id"] = "ord-1"
    d = KL.audrey_diff([link], [], [F.fill("t1", "ord-1", count="3.00")])
    assert "LIVE_WITHOUT_PAPER_FILL" in d["rows"][0]["codes"]


def test_an_exit_is_expected_at_its_planned_live_size_not_paper_over_scale():
    po = _paper_order(order_id="paperord:5", mirror_id="km:paperord:5", role="REDUCE",
                      intent="ORDER_INTENT_SELL_LONG", qty=Decimal("1000"))
    plan = KO.plan(po, KM.establish(bettor(), kalshi()), live_held=3,
                   paper_open_qty=Decimal("1500"), opened_intent="ORDER_INTENT_BUY_LONG")
    assert plan["live_qty"] == 2                         # 2/3 of 3 held
    link = dict(KL.build_link(po, plan), venue_order_id="ord-5")
    paper = [{"fill_id": "pf5", "order_id": "paperord:5", "qty": 1000, "price": "0.50",
              "fee_usd": "0", "filled_at": "2026-10-03T20:00:00Z"}]
    live = [F.fill("t5", "ord-5", action="sell", count="2.00", price="0.5000",
                   fee="0.01", created="2026-10-03T20:00:01Z")]
    (r,) = KL.audrey_diff([link], paper, live)["rows"]
    assert r["expected_live_qty"] == 2 and r["qty_diff"] == 0
    assert "QTY_DIFF" not in r["codes"]
