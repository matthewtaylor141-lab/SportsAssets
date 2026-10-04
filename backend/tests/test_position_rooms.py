"""THE POSITION ROOM ASSEMBLY, PURE, ON FIXTURES (tests/position_room_fixtures
-- every row there is labelled FIXTURE).

The owner's example as one room: Derek bought the Yankees at 50c, Xavier rests
a Yankees sell at 70c, a Red Sox hedge bid at 35c is partially filled and a
Red Sox bid at 30c is proposed. On the venue's two-way baseball contract YES
Yankees is the SHORT and YES Red Sox the LONG of one contract; the room must
show them as ONE correlated economic position with the payout per outcome
computed from the recorded quantities and prices.
"""
from __future__ import annotations

import copy

import pytest

from sportsassets import position_rooms as P
from tests import position_room_fixtures as F

NYY, BOS = "new york yankees", "boston red sox"


def _rooms(raw):
    return {r["group_key"]: r for r in P.build_rooms(raw)}


def _evt(raw=None):
    return _rooms(raw or F.raw_paper())["PAPER:EVT:" + F.EVENT]


def _order(room, ref):
    return next(o for o in room["orders"] if o["order_ref"] == ref)


# ── grouping by established identity ─────────────────────────────────

def test_yankees_and_red_sox_are_one_correlated_room():
    r = _evt()
    assert r["grouping"] == "ESTABLISHED_EVENT_AND_SETTLEMENT_IDENTITY"
    assert r["outcomes"] == [BOS, NYY]
    legs = {lg["instrument"]["label"]: lg for lg in r["legs"]}
    assert legs["YES New York Yankees"]["pays_on"] == [NYY]
    assert legs["YES New York Yankees"]["holding_side"] == "SHORT"
    assert legs["YES Boston Red Sox"]["pays_on"] == [BOS]
    assert legs["YES Boston Red Sox"]["holding_side"] == "LONG"
    assert sorted(r["groups"]) == [F.G_BOS, F.G_NYY]
    # every leg/order carries its account/book and exact contract identity
    o = _order(r, "paper_ord_fx_prot")
    assert o["account"]["book"] == "PAPER"
    assert o["account"]["account"] == "paper_acct_main"
    assert o["contract"]["market_slug"] == F.SLUG
    assert o["contract"]["held_side"] == "SHORT"
    assert o["contract"]["pays_on"] == [NYY]
    assert o["contract"]["settlement_class"] == "WINNER_FULL_GAME"


def test_scenario_table_current_and_with_standing_filled():
    sc = _evt()["scenarios"]
    cur = {x["outcome"]: x for x in sc["current"]["rows"]}
    # Yankees 1000 @ .50 + 3.50 fees; Red Sox 150 @ .35 + 0.50 fees
    assert cur[NYY]["payout_usd"] == 1000.0
    assert cur[NYY]["pnl_total_usd"] == pytest.approx(443.5)
    assert cur[BOS]["pnl_total_usd"] == pytest.approx(-406.5)
    assert cur["VOID"]["pnl_total_usd"] == pytest.approx(-4.0)
    assert sc["current"]["capital_at_risk_usd"] == pytest.approx(406.5)
    # + the resting 70c sell and the 250 resting Red Sox remainder
    b = {x["outcome"]: x for x in sc["with_standing_filled"]["rows"]}
    assert b[NYY]["pnl_total_usd"] == pytest.approx(56.0)
    assert b[BOS]["pnl_total_usd"] == pytest.approx(456.0)
    assert sorted(sc["standing_orders_applied"]) == [
        "paper_ord_fx_hedge", "paper_ord_fx_prot"]
    # + the proposed 600 Red Sox @ 30c
    c = {x["outcome"]: x for x in sc["with_standing_and_proposed_filled"][
        "rows"]}
    assert c[BOS]["pnl_total_usd"] == pytest.approx(876.0)
    assert c[NYY]["pnl_total_usd"] == pytest.approx(-124.0)


def test_resting_sell_distance_and_if_it_fills():
    o = _order(_evt(), "paper_ord_fx_prot")
    assert o["state"] == "RESTING" and o["filled_qty"] == 0
    assert o["note"] == "RESTING - NOT PROTECTION UNTIL FILLED"
    # Yankees side: bid 1 - .36 = .64, ask 1 - .34 = .66
    assert o["current"]["bid"] == pytest.approx(0.64)
    assert o["current"]["ask"] == pytest.approx(0.66)
    assert o["distance"]["distance"] == pytest.approx(0.06)
    assert o["distance"]["basis"] == "LIMIT_MINUS_BID"
    f = o["if_it_fills"]
    assert f["available"] is True
    assert f["realized_on_fill_usd"] == pytest.approx(196.5)
    assert f["resulting_inventory"]["open_qty"] == 0.0
    assert f["worst_after"] == {"outcome": NYY, "pnl_total_usd": 143.5}
    assert f["locked_pnl_after_usd"] == pytest.approx(143.5)
    assert f["capital_at_risk_after_usd"] == pytest.approx(53.0)
    assert f["fees"].startswith("FEES_NOT_ESTIMATED")
    # expected value uses Xavier's RECORDED probability (0.63 Yankees)
    assert f["expected_pnl_after_usd"] == pytest.approx(
        0.63 * 143.5 + 0.37 * 293.5)


def test_partial_fill_is_partial_with_its_remainder():
    o = _order(_evt(), "paper_ord_fx_hedge")
    assert o["state"] == "PARTIAL" and o["raw_state"] == "PARTIALLY_FILLED"
    assert o["filled_qty"] == 150 and o["remaining_qty"] == 250
    f = o["if_it_fills"]
    assert f["realized_basis"] == "OPENING_FILL_REALISES_NOTHING"
    inv = f["resulting_inventory"]
    assert inv["open_qty"] == 400.0
    assert inv["avg_basis_per_contract"] == pytest.approx(140.5 / 400)


def test_cancelled_order_is_terminal_and_never_applied():
    r = _evt()
    o = _order(r, "paper_ord_fx_prot_old")
    assert o["state"] == "CANCELLED" and o["raw_state"] == "CANCELED"
    assert "if_it_fills" not in o and o["distance"] is None
    assert "paper_ord_fx_prot_old" in r["chain"]["terminal_unfilled"]
    assert "paper_ord_fx_prot_old" not in r["scenarios"][
        "standing_orders_applied"]


def test_proposed_is_not_an_order_at_the_venue():
    r = _evt()
    o = _order(r, "paper_dec_fx_rsox")
    assert o["state"] == "PROPOSED"
    assert "NOT yet" in o["state_meaning"]
    assert r["chain"]["proposed"] == ["paper_dec_fx_rsox"]
    assert o["if_it_fills"]["available"] is True


def test_actual_and_paper_are_separate_rooms_never_summed():
    paper = _rooms(F.raw_paper())
    actual = _rooms(F.raw_actual())
    assert "ACTUAL-POLYMARKET:EVT:" + F.EVENT in actual
    assert not any(k.startswith("ACTUAL") for k in paper)
    a = actual["ACTUAL-POLYMARKET:EVT:" + F.EVENT]
    assert a["economic"]["money_label"].startswith("REAL USD")
    assert paper["PAPER:EVT:" + F.EVENT]["economic"]["money_label"].startswith(
        "FICTIONAL")
    # one real contract held, at 1:1000; nothing of the paper book in it
    assert [lg["holding"]["open_qty"] for lg in a["legs"]] == [1.0]
    prot = _order(a, "mirror_fx_prot")
    # a requested-but-unconfirmed cancel can still fill: canonical RESTING,
    # sub-state CANCEL_PENDING (order_state_truth)
    assert prot["state"] == "RESTING"
    assert prot["sub_state"] == "CANCEL_PENDING"
    assert prot["raw_state"] == "CANCEL_REQUESTED"
    assert prot["if_it_fills"]["available"] is True     # it can still fill
    assert a["xavier"][0]["protection"]["filled_protection_qty"] == 0.0
    assert a["xavier"][0]["protection"][
        "unfilled_resting_protection_qty"] == 1.0
    assert a["audrey"]["status"] == "RECONCILED"


def test_kalshi_leg_never_borrows_the_polymarket_book():
    raw = F.raw_actual()
    raw["venue"] = "KALSHI"
    for o in raw["orders"]:
        o["venue"] = "KALSHI"
    r = _rooms(raw)["ACTUAL-KALSHI:EVT:" + F.EVENT]
    lg = r["legs"][0]
    assert lg["current"]["bid"] is None and lg["current"]["mark"] is None
    assert lg["current"]["reason"] == P.R_KALSHI_BOOK
    assert r["economic"]["unrealized_pnl_usd"] is None


# ── identity refusals: UNGROUPED with the reason ─────────────────────

def test_unmapped_market_is_ungrouped_with_its_reason():
    r = _rooms(F.raw_paper())["PAPER:MKT:" + F.UNMAPPED]
    assert r["grouping"] == "UNGROUPED"
    assert r["ungrouped_reason"] == P.R_NO_PREMAP
    # still exact on its own binary contract
    assert r["outcomes"] == ["RESOLVES_YES", "RESOLVES_NO"]
    rows = {x["outcome"]: x for x in r["scenarios"]["current"]["rows"]}
    assert rows["RESOLVES_YES"]["payout_usd"] == 200.0
    assert rows["RESOLVES_NO"]["payout_usd"] == 0


def test_a_similar_title_on_another_event_is_never_merged():
    raw = F.raw_paper()
    g2 = "aec-mlb-bos-nyy-2026-10-05"
    for row in F.premap_rows():
        raw["premap"].append(dict(row, market_slug=g2,
                                  event_slug="mlb-bos-nyy-2026-10-05",
                                  event_title=row["event_title"]))
    o = copy.deepcopy(_ord := next(x for x in raw["orders"]
                                  if x["order_ref"] == "paper_ord_fx_prot"))
    o.update(order_ref="paper_ord_game2", slug=g2, group_id="paper_g_g2")
    raw["orders"].append(o)
    rooms = _rooms(raw)
    assert "PAPER:EVT:mlb-bos-nyy-2026-10-05" in rooms
    assert "paper_ord_game2" not in [x["order_ref"] for x in rooms[
        "PAPER:EVT:" + F.EVENT]["orders"]]
    assert _ord["order_ref"] == "paper_ord_fx_prot"


def test_non_winner_settlement_variable_is_ungrouped():
    raw = F.raw_paper()
    for row in raw["premap"]:
        row["sports_type"] = "baseball_team_full_game_spread"
    r = _rooms(raw)["PAPER:MKT:" + F.SLUG]
    assert r["ungrouped_reason"] == P.R_NOT_WINNER


def test_two_way_rows_naming_one_team_twice_are_not_established():
    rows = F.premap_rows()
    rows[1]["team_name"] = rows[0]["team_name"]
    by = {F.SLUG: rows}
    got = P.resolve_identity(F.SLUG, "SHORT", by, {F.EVENT: rows})
    assert got["status"] == "UNGROUPED"
    assert got["reason"] in (P.R_PARTICIPANTS, P.R_TWO_WAY_SIDES)


def test_per_side_soccer_long_pays_team_short_pays_complement():
    ev = "unl-fra-ita-2026-10-02"
    base = {"event_slug": ev, "event_title": "France vs. Italy",
            "kind": "side", "sports_type": "soccer_team_full_time_winner",
            "game_start": F.NOW}
    rows = []
    for slug, team in (("atc-%s-fra" % ev, "france"),
                       ("atc-%s-ita" % ev, "italy"),
                       ("atc-%s-draw" % ev, None)):
        for intent, sn in (("ORDER_INTENT_BUY_LONG", "yes"),
                           ("ORDER_INTENT_BUY_SHORT", "no")):
            rows.append(dict(base, market_slug=slug, intent=intent,
                             side_norm=sn, team_name=team, team_abbr=team))
    by_slug, by_ev = {}, {ev: rows}
    for r in rows:
        by_slug.setdefault(r["market_slug"], []).append(r)
    lf = P.resolve_identity("atc-%s-fra" % ev, "LONG", by_slug, by_ev)
    sf = P.resolve_identity("atc-%s-fra" % ev, "SHORT", by_slug, by_ev)
    dl = P.resolve_identity("atc-%s-draw" % ev, "LONG", by_slug, by_ev)
    assert lf["outcomes"] == ["france", "italy", "DRAW"]
    assert lf["pays_on"] == ["france"]
    assert sf["pays_on"] == ["italy", "DRAW"]
    assert dl["pays_on"] == ["DRAW"]
    # one recorded probability cannot split three outcomes
    got = P.outcome_probabilities([], [], lf["outcomes"])
    assert got["p"] is None and got["reason"].startswith("THREE_WAY")


# ── game state: never a stale score as live ──────────────────────────

def test_stale_game_state_is_stale_never_live():
    gs = _evt()["game_state"]
    assert gs["reported_status"] == "LIVE" and gs["status"] == "STALE"
    assert gs["live"] is False and gs["age_s"] == 900.0
    assert gs["score"] is None and gs["score_status"] == "UNAVAILABLE"
    assert gs["score_reason"].startswith("NO_AUTHORITATIVE_LIVE_SCORE")
    assert gs["situation_status"] == "UNAVAILABLE"
    assert [t["initials"] for t in gs["teams"]] == ["BOS", "NYY"]


def test_fresh_in_progress_is_live_and_absent_is_unavailable():
    assert _evt(F.raw_paper(game_age_s=20.0))["game_state"]["live"] is True
    raw = F.raw_paper()
    raw["game_state"] = {}
    gs = _evt(raw)["game_state"]
    assert gs["status"] == "UNAVAILABLE" and gs["reason"] == P.R_NO_GAME_ROW
    assert gs["live"] is False


# ── missing inputs are named nulls ───────────────────────────────────

def test_missing_book_gives_named_nulls_not_zeros():
    raw = F.raw_paper()
    raw["books"] = {}
    r = _evt(raw)
    lg = r["legs"][0]
    assert lg["current"]["bid"] is None and lg["current"]["mark"] is None
    assert lg["current"]["reason"] == P.R_NO_BOOK
    assert lg["unrealized_pnl_usd"] is None
    assert r["economic"]["unrealized_pnl_usd"] is None
    assert r["economic"]["unrealized_reason"] == "A_HELD_LEG_HAS_NO_MARK"
    o = _order(r, "paper_ord_fx_prot")
    assert o["distance"]["distance"] is None
    assert o["distance"]["reason"] == P.R_NO_BOOK


def test_stale_book_is_flagged():
    r = _evt(F.raw_paper(book_age_s=1200.0))
    assert r["legs"][0]["current"]["freshness"] == "STALE"
    assert r["economic"]["stale_marks"]


def test_order_without_limit_and_room_without_xavier():
    raw = F.raw_paper()
    for o in raw["orders"]:
        if o["order_ref"] == "paper_ord_fx_prot":
            o["limit"] = None
    raw["xavier"]["assessments"] = {}
    r = _evt(raw)
    f = _order(r, "paper_ord_fx_prot")["if_it_fills"]
    assert f["available"] is False and f["reason"] == P.R_NO_LIMIT
    x = {p["group_id"]: p for p in r["xavier"]}
    assert x[F.G_NYY]["status"] == "UNAVAILABLE"
    assert x[F.G_NYY]["why"] == P.R_NO_XAVIER
    assert r["eddie"] == {"status": "UNAVAILABLE", "why": "EDDIE_NOT_DEPLOYED",
                          "source": r["eddie"]["source"]}
    assert r["scenarios"]["skipped"][0]["reason"] == P.R_NO_LIMIT
    assert r["economic"]["expected_pnl_usd"] is None


def test_eddie_rows_are_read_when_deployed():
    raw = F.raw_paper()
    raw["eddie"] = {"present": True, "rows": [{
        "estimate_id": "ee:1", "decision_id": "paper_dec_fx_entry",
        "recommendation": "REST_LIMIT", "estimated_at": F.NOW - 60}]}
    e = _evt(raw)["eddie"]
    assert e["status"] == "OK" and e["estimates"][0]["estimate_id"] == "ee:1"


# ── Xavier: read, never recomputed ───────────────────────────────────

def test_xavier_panel_reads_the_persisted_decision():
    x = next(p for p in _evt()["xavier"] if p["group_id"] == F.G_NYY)
    assert x["recommendation"] == "HOLD"
    assert x["display_recommendation"] == "PROTECT"        # KEEP_STANDING
    assert x["current_ev_usd"] == 630.0 and x["entry_ev_usd"] == 56.5
    assert x["why_leader_wins"]["source"] == "paper_xavier_reviews.selection"
    acts = [a["action"] for a in x["alternatives"]]
    assert acts[:3] == ["HOLD", "REDUCE", "EXIT"]          # ranked by value
    assert "HEDGE" in acts and "PROTECTION" in acts and "REALLOCATE" in acts
    prot = next(a for a in x["alternatives"] if a["action"] == "PROTECTION")
    assert prot["note"] == ("STANDING 1,000 - NOT PROTECTION UNTIL FILLED; "
                            "FILLED PROTECTION 0")
    assert x["protection"]["filled_protection_qty"] == 0.0
    assert x["protection"]["unfilled_resting_protection_qty"] == 1000.0
    assert x["next_review_due_at"] is not None
    assert x["position"]["legs"][0]["venue_prices"]["KALSHI"][
        "status"] == "UNAVAILABLE"
    assert any(w["what"] == "STANDING_ORDER_TO_FILL"
               for w in x["waiting_for"])


def test_stale_xavier_evidence_is_warned_and_waits():
    x = next(p for p in _evt(F.raw_paper(fresh=False))["xavier"]
             if p["group_id"] == F.G_NYY)
    assert x["evidence"]["discretionary_permitted"] is False
    assert any(w["what"] == "PROBABILITY_NOT_FRESH"
               for w in x["stale_evidence"])
    assert any(w["what"] == "A_FRESH_PROBABILITY" for w in x["waiting_for"])
    raw = F.raw_paper(fresh=False)
    raw["xavier"]["assessments"][F.G_NYY]["recommendation"] = None
    x2 = next(p for p in _evt(raw)["xavier"] if p["group_id"] == F.G_NYY)
    assert x2["display_recommendation"] == "WAITING_FOR_EVIDENCE"


def test_karen_challenge_on_the_room_review_is_shown():
    k = _evt()["karen"]
    assert k["status"] == "OPEN_CHALLENGE" and k["open"] == 1


def test_list_row_summarises_without_mixing_books():
    row = P.summarize(_evt())
    assert row["legs"] == 2 and row["orders_live"] == 3
    assert row["orders_by_state"]["RESTING"] == 1
    assert row["xavier"]["recommendation"] == "HOLD"
    assert row["game_status"] == "STALE" and row["game_live"] is False
    assert row["capital_at_risk_usd"] == pytest.approx(406.5)


def test_group_keys_round_trip_and_reject_garbage():
    k = P.group_key("ACTUAL", "KALSHI", "EVT", F.EVENT)
    assert P.parse_group_key(k) == {"book": "ACTUAL", "venue": "KALSHI",
                                    "kind": "EVT", "ident": F.EVENT}
    for bad in ("", "PAPER", "PAPER:EVT:", "X:EVT:a", "PAPER:FOO:a"):
        assert P.parse_group_key(bad) is None


# ── constants restated here are pinned to their sources ──────────────

def test_restated_constants_match_their_sources():
    from sportsassets import bettor_fixture_store as FS
    from sportsassets import bettor_paper_ledger as L
    from sportsassets import bettor_soccer_fixture as SF
    from sportsassets import execmirror as EM
    assert P.MARK_STALE_AFTER_S == L.MARK_STALE_AFTER_S
    assert P.MARK_METHOD == L.MARK_METHOD
    assert P.PAPER_ACCOUNT_ID == L.ACCOUNT_ID
    assert set(P.PAPER_OPEN_RAW) == set(L.OPEN_STATES)
    assert P.ACTUAL_MANAGEMENT_EVERY_S == EM.MANAGEMENT_EVERY_S
    assert P.GAME_STATE_MAX_AGE_S == FS.MAX_ROW_AGE_S == SF.MAX_ROW_AGE_S


def test_every_raw_state_of_both_machines_is_mapped():
    paper = ("PENDING_SIMULATION", "RESTING", "PARTIALLY_FILLED", "FILLED",
             "EXPIRED", "CANCEL_PENDING", "CANCELED", "REJECTED")
    mirror = ("PLANNED", "SUBMITTING", "UNKNOWN", "OPEN", "PARTIALLY_FILLED",
              "FILLED", "CANCEL_REQUESTED", "CANCELLED", "EXPIRED",
              "REJECTED", "EXCLUDED")
    assert set(P.PAPER_STATE_MAP) == set(paper)
    assert set(P.MIRROR_STATE_MAP) == set(mirror)
    from sportsassets import kalshi_orders as KO
    assert set(KO.TRANSITIONS) | set(KO.TERMINAL) == set(mirror)
    # an unmapped raw state is the explicit UNKNOWN, never FILLED
    assert P.canonical_state("NEW_STATE", table="paper_orders") == "UNKNOWN"
