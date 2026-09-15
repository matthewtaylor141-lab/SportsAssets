#!/usr/bin/env python3
"""Offline gate for the Phase X cross-venue research path. Contacts nothing.

The ten numbered tests are the owner's pre-network gate, in order. The
rest guard the mechanisms those ten depend on.

Run:  python3 -m pytest research/test_run85_phasex.py -q
      python3 research/test_run85_phasex.py
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_k = importlib.util.spec_from_file_location(
    "px_kalshi", Path(__file__).with_name("run85_phasex_kalshi.py"))
K = importlib.util.module_from_spec(_k)
_k.loader.exec_module(K)

_e = importlib.util.spec_from_file_location(
    "px_econ", Path(__file__).with_name("run85_phasex_economics.py"))
E = importlib.util.module_from_spec(_e)
_e.loader.exec_module(E)

SRC_K = Path(K.__file__).read_text()

VERIFIED, QUOTED, ABSENT = K.VERIFIED, K.QUOTED, K.ABSENT


# ------------------------------------------------------------- fixtures
#
# Payload shapes are taken from the parsers this repo already runs against
# the live venue -- edge-engine/src/edge/venues/kalshi.py (orderbook_fp
# with dollar-string prices and decimal quantities; /events with nested
# markets; yes_sub_title) and app.py's _kalshi_shape. They are NOT copied
# from Kalshi's documentation, which is unread: kalshi.com is blocked too.

KALSHI_MARKET = {
    "ticker": "KXNFLGAME-26SEP14DENKC-KC",
    "event_ticker": "KXNFLGAME-26SEP14DENKC",
    "series_ticker": "KXNFLGAME",
    "market_type": "binary",
    "title": "Will the Kansas City Chiefs beat the Denver Broncos?",
    "yes_sub_title": "Kansas City Chiefs",
    "no_sub_title": "Denver Broncos",
    "status": "active",
    "close_time": "2026-09-15T04:15:00Z",
    "expiration_time": "2026-09-15T08:00:00Z",
    "expected_expiration_time": "2026-09-15T07:30:00Z",
    "settlement_timer_seconds": 3600,
    "settlement_sources": [{"name": "NFL", "url": "https://www.nfl.com/"}],
    "rules_primary": (
        "If the Kansas City Chiefs win the game, then the market resolves "
        "to Yes. Overtime is included if played. If the game is postponed "
        "and not completed by the expiration date, the market is voided "
        "and all contracts refunded. If the game is cancelled, the market "
        "resolves to No."),
    "rules_secondary": "Outcome sourced from the NFL.",
    "can_close_early": True,
    "risk_limit_cents": 500000,
    "tick_size": 1,
}
KALSHI_EVENT = {
    "event_ticker": "KXNFLGAME-26SEP14DENKC",
    "series_ticker": "KXNFLGAME",
    "title": "Denver Broncos at Kansas City Chiefs",
    "category": "Sports",
    "mutually_exclusive": True,
}
KALSHI_ORDERBOOK = {
    "orderbook_fp": {
        "yes_dollars": [["0.6100", "1500.00"], ["0.6000", "8000.00"],
                        ["0.5900", "250.00"], ["0.5500", "40000.00"]],
        "no_dollars": [["0.3800", "900.00"], ["0.3700", "12000.00"],
                       ["0.3600", "300.00"], ["0.3000", "5000.00"],
                       ["0.2000", "99000.00"]],
    }
}
PMUS_MARKET_DATA = {
    "marketSlug": "aec-nfl-den-kc-2026-09-14-kc",
    "transactTime": "2026-09-15T03:00:00.000000000Z",
    "bids": [{"px": {"value": "0.6000", "currency": "USD"}, "qty": "700.00"},
             {"px": {"value": "0.5900", "currency": "USD"}, "qty": "2200.00"}],
    "offers": [{"px": {"value": "0.6100", "currency": "USD"}, "qty": "500.00"},
               {"px": {"value": "0.6200", "currency": "USD"}, "qty": "3000.00"},
               {"px": {"value": "0.7000", "currency": "USD"},
                "qty": "10000.00"}],
}


class FakeResponse:
    def __init__(self, status, payload=None, headers=None, text=None):
        self.status_code = status
        self.headers = headers or {}
        self._payload = payload
        self.text = text if text is not None else (
            "" if payload is None else __import__("json").dumps(payload))

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeClock:
    def __init__(self, t0=1_000_000.0):
        self.t = t0
        self.slept = []

    def monotonic(self):
        return self.t

    def time(self):
        return self.t

    def sleep(self, s):
        assert s >= 0
        self.slept.append(s)
        self.t += s


def client_for(responses, clock=None):
    clock = clock or FakeClock()
    seq = list(responses)

    def transport(method, url, params, timeout):
        assert method == "GET", method
        if not seq:
            raise AssertionError("more requests than fixtures")
        r = seq.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    return K.KalshiResearchClient(transport, pacer=K.ResearchPacer(clock=clock),
                                  clock=clock), clock


# ============================================ 1. rules survive ingestion
def test_1_kalshi_rules_fields_survive_ingestion_unchanged():
    rec = K.kalshi_contract_record(KALSHI_MARKET, KALSHI_EVENT)
    assert rec["RULES_PRIMARY"]["value"] == KALSHI_MARKET["rules_primary"]
    assert rec["RULES_SECONDARY"]["value"] == KALSHI_MARKET["rules_secondary"]
    assert rec["SETTLEMENT_SOURCES"]["value"] == \
        KALSHI_MARKET["settlement_sources"]
    assert rec["SETTLEMENT_TIMER_SECONDS"]["value"] == 3600
    assert rec["EXPIRATION_TIME"]["value"] == KALSHI_MARKET["expiration_time"]
    # raw beside normalized, byte-identical
    assert rec["RAW_MARKET"]["rules_primary"] == KALSHI_MARKET["rules_primary"]
    for k in ("rules_primary", "rules_secondary", "settlement_sources",
              "expiration_time", "expected_expiration_time",
              "settlement_timer_seconds"):
        assert k in rec["RAW_MARKET"], k


def test_1b_the_rules_the_trading_adapter_drops_are_the_ones_retained():
    """The trading client keeps ticker + yes_sub_title. Every field a
    settlement proposition needs is in the retained set here."""
    for k in ("rules_primary", "rules_secondary", "settlement_sources",
              "expiration_time", "expected_expiration_time",
              "settlement_timer_seconds", "close_time", "result"):
        assert k in K.RETAINED_MARKET_FIELDS, k


def test_1c_rule_topics_are_quoted_verbatim_never_paraphrased():
    rec = K.kalshi_contract_record(KALSHI_MARKET, KALSHI_EVENT)
    for slot in ("OVERTIME_RULES", "POSTPONEMENT_RULES", "VOID_RULES",
                 "CANCELLATION_RULES"):
        node = rec[slot]
        assert node["status"] == QUOTED, slot
        for hit in node["value"]:
            pool = {"market.rules_primary": KALSHI_MARKET["rules_primary"],
                    "market.rules_secondary":
                        KALSHI_MARKET["rules_secondary"]}[hit["source"]]
            assert hit["text"] in pool, (slot, hit["text"])


# ============================================== 2. full depth survives
def test_2_full_depth_survives_ingestion_unchanged():
    bk = K.kalshi_book_record("T", KALSHI_ORDERBOOK, 1.0, 2.0)
    assert bk["BID_LEVELS"] == 4
    assert bk["ASK_LEVELS"] == 5                    # every NO level mirrored
    assert [lv["PRICE"] for lv in bk["BIDS"]] == [0.61, 0.60, 0.59, 0.55]
    assert sum(lv["QUANTITY"] for lv in bk["BIDS"]) == 49750.0
    assert sum(lv["QUANTITY"] for lv in bk["ASKS"]) == 117200.0
    assert bk["RAW"] is KALSHI_ORDERBOOK


def test_2b_research_depth_is_not_capped_at_the_production_relays_ten():
    deep = {"orderbook_fp": {
        "yes_dollars": [[f"0.{50 - i:02d}00", "10.00"] for i in range(25)],
        "no_dollars": [[f"0.{40 - i:02d}00", "10.00"] for i in range(25)]}}
    bk = K.kalshi_book_record("T", deep, 1.0, 2.0)
    assert bk["BID_LEVELS"] == 25 and bk["ASK_LEVELS"] == 25


def test_2c_levels_carry_every_required_column_and_both_clocks():
    bk = K.kalshi_book_record("T", KALSHI_ORDERBOOK, 111.0, 222.0,
                              venue_ts="2026-09-15T03:00:00Z")
    for lv in bk["BIDS"] + bk["ASKS"]:
        for col in ("VENUE", "TICKER", "SIDE", "PRICE", "QUANTITY", "LEVEL"):
            assert col in lv, col
    assert bk["LOCAL_RECEIPT_WALL"] == 111.0
    assert bk["LOCAL_RECEIPT_MONOTONIC"] == 222.0
    assert bk["VENUE_TIMESTAMP"] == "2026-09-15T03:00:00Z"
    assert [lv["LEVEL"] for lv in bk["ASKS"]] == [1, 2, 3, 4, 5]


def test_2d_pmus_books_reach_the_same_structure():
    bk = K.pmus_book_record("slug", PMUS_MARKET_DATA, 1.0, 2.0)
    assert bk["BID_LEVELS"] == 2 and bk["ASK_LEVELS"] == 3
    assert [lv["PRICE"] for lv in bk["ASKS"]] == [0.61, 0.62, 0.70]
    assert bk["VENUE_TIMESTAMP"] == PMUS_MARKET_DATA["transactTime"]


# ================================ 3. no direction from title similarity
def test_3_pays_1_if_cannot_be_verified_from_title_similarity_alone():
    p = E.pays_1_if(venue="KALSHI", contract_id="T",
                    side_label="Kansas City Chiefs",
                    sibling_label="Denver Broncos",
                    rules_prose=[],            # titles only, no rules
                    subject="Kansas City Chiefs")
    assert p["PAYOFF_STATUS"] == E.NOT_IDENTIFIED_SETTLEMENT_CONDITION
    assert "NO_RULES_PROSE" in p["DIRECTION_BLOCKERS"]
    assert p["PAYOFF_PROPOSITION"] is None
    assert p["ELIGIBLE_FOR_AUTOMATED_EQUIVALENCE"] is False


def test_3b_matching_titles_do_not_create_direction_on_either_venue():
    for venue in ("PMUS", "KALSHI"):
        p = E.pays_1_if(venue=venue, contract_id="X",
                        side_label="Kansas City Chiefs",
                        sibling_label="Denver Broncos",
                        rules_prose=[("market.title",
                                      "Kansas City Chiefs vs Denver "
                                      "Broncos")])
        assert p["PAYOFF_STATUS"] == \
            E.NOT_IDENTIFIED_SETTLEMENT_CONDITION, venue
        assert "NO_AFFIRMATIVE_SETTLEMENT_SENTENCE" in p["DIRECTION_BLOCKERS"]


def test_3c_a_real_affirmative_sentence_does_earn_direction():
    p = E.pays_1_if(venue="KALSHI", contract_id="T",
                    side_label="Kansas City Chiefs",
                    sibling_label="Denver Broncos",
                    rules_prose=[("market.rules_primary",
                                  KALSHI_MARKET["rules_primary"])],
                    event="Denver Broncos at Kansas City Chiefs")
    assert p["PAYOFF_STATUS"] == E.PAYOFF_VERIFIED
    assert p["DIRECTION_ANCHOR"]
    assert p["PROOF_COMPONENTS_MISSING"] == []
    assert p["ELIGIBLE_FOR_AUTOMATED_EQUIVALENCE"] is True


# ============================== 4. missing settlement -> NOT_IDENTIFIED
def test_4_missing_settlement_terms_produce_not_identified():
    bare = {"ticker": "T", "title": "Some game", "yes_sub_title": "A"}
    rec = K.kalshi_contract_record(bare)
    for slot in ("RULES_PRIMARY", "RULES_SECONDARY", "SETTLEMENT_SOURCES",
                 "EXPIRATION_TIME", "SETTLEMENT_TIMER_SECONDS",
                 "CANCELLATION_RULES", "POSTPONEMENT_RULES",
                 "OVERTIME_RULES", "DRAW_RULES", "VOID_RULES"):
        assert rec[slot]["status"] == ABSENT, slot
        assert rec[slot]["value"] is None, slot


def test_4b_an_empty_venue_field_is_absent_not_an_empty_verified():
    rec = K.kalshi_contract_record({"ticker": "T", "rules_primary": "",
                                    "settlement_sources": []})
    assert rec["RULES_PRIMARY"]["status"] == ABSENT
    assert rec["SETTLEMENT_SOURCES"]["status"] == ABSENT
    assert rec["TICKER"]["status"] == VERIFIED


# ================== 5. opposite venue labels cannot reverse orientation
def test_5_opposite_labels_cannot_accidentally_reverse_orientation():
    """The real PMUS case: `long` sits on Denver while the description
    settles Yes on Kansas City. Trusting the label would hedge the wrong
    way round; the gate refuses instead."""
    prose = [("market.description",
              "This market will settle to Yes if Kansas City Chiefs "
              "outscores Denver Broncos by more than 21.5 points in the "
              "second half.")]
    denver = E.pays_1_if(venue="PMUS", contract_id="asc-nfl-den-kc",
                         side_label="Denver Broncos",
                         sibling_label="Kansas City Chiefs",
                         rules_prose=prose, event="Broncos at Chiefs")
    assert denver["PAYOFF_STATUS"] == E.NOT_IDENTIFIED_CONTRADICTION
    assert "SETTLEMENT_SUBJECT_IS_THE_SIBLING_SIDE" in \
        denver["DIRECTION_BLOCKERS"]
    assert denver["ELIGIBLE_FOR_AUTOMATED_EQUIVALENCE"] is False


def test_5b_a_contest_sentence_verifies_through_the_proof_chain():
    """'settle to the winner of A vs B' states WHAT settles, not which side
    pays. Both names sit inside the matchup, so the venue's side field
    supplies orientation and nothing contradicts it -> VERIFIED."""
    p = E.pays_1_if(venue="PMUS", contract_id="aec-boxing",
                    side_label="Canelo Alvarez",
                    sibling_label="Christian Mbilli",
                    rules_prose=[("market.description",
                                  "This market will settle to the winner "
                                  "of the Canelo Alvarez vs. Christian "
                                  "Mbilli boxing match.")],
                    event="Canelo Alvarez vs Christian Mbilli")
    assert p["PAYOFF_STATUS"] == E.PAYOFF_VERIFIED
    assert p["PAYOFF_PROPOSITION"] == "PAYS $1 IF: Canelo Alvarez"


def test_5b2_the_same_sentence_verifies_the_other_side_too():
    """Symmetry check: the contest rule must not favour whichever side we
    happened to ask about."""
    p = E.pays_1_if(venue="PMUS", contract_id="aec-boxing",
                    side_label="Christian Mbilli",
                    sibling_label="Canelo Alvarez",
                    rules_prose=[("market.description",
                                  "This market will settle to the winner "
                                  "of the Canelo Alvarez vs. Christian "
                                  "Mbilli boxing match.")],
                    event="Canelo Alvarez vs Christian Mbilli")
    assert p["PAYOFF_STATUS"] == E.PAYOFF_VERIFIED
    assert p["PAYOFF_PROPOSITION"] == "PAYS $1 IF: Christian Mbilli"


def test_5b3_disagreeing_side_binding_fields_are_a_contradiction():
    """Two authoritative side fields naming different participants: report
    the contradiction, never elect one."""
    p = E.pays_1_if(venue="PMUS", contract_id="x",
                    side_label="Denver Broncos",
                    sibling_label="Kansas City Chiefs",
                    rules_prose=[("market.description",
                                  "Settles to the winner of the Denver "
                                  "Broncos vs Kansas City Chiefs game.")],
                    event="DEN at KC",
                    side_binding_fields=[
                        ("marketSides[0].team.name", "Denver Broncos"),
                        ("outcomes[0]", "Kansas City Chiefs")])
    assert p["PAYOFF_STATUS"] == E.NOT_IDENTIFIED_CONTRADICTION
    assert any(b.startswith("SIDE_BINDING_FIELDS_DISAGREE")
               for b in p["DIRECTION_BLOCKERS"])


def test_5b4_two_subject_labels_are_ambiguous_not_verified():
    """Word order finds disagreement, never agreement: 'A outscores B' and
    'B is outscored by A' are one fact in two orders."""
    p = E.pays_1_if(venue="PMUS", contract_id="x",
                    side_label="Kansas City Chiefs",
                    sibling_label="Denver Broncos",
                    rules_prose=[("market.description",
                                  "This market will settle to Yes if "
                                  "Kansas City Chiefs outscores Denver "
                                  "Broncos by more than 21.5 points.")],
                    event="DEN at KC")
    assert p["PAYOFF_STATUS"] == E.NOT_IDENTIFIED_AMBIGUOUS_SUBJECT
    assert "BOTH_LABELS_ACT_AS_SUBJECTS" in p["DIRECTION_BLOCKERS"]


def test_5b5_a_missing_side_binding_is_named_as_such():
    p = E.pays_1_if(venue="KALSHI", contract_id="x", side_label=None,
                    sibling_label="B",
                    rules_prose=[("market.rules_primary",
                                  "The market resolves to Yes if A wins.")],
                    event="A vs B")
    assert p["PAYOFF_STATUS"] == E.NOT_IDENTIFIED_SIDE_BINDING
    assert "NO_SIDE_BINDING_FIELD" in p["DIRECTION_BLOCKERS"]


def test_5b6_a_verified_payoff_carries_all_five_proof_components():
    p = E.pays_1_if(venue="KALSHI", contract_id="T",
                    side_label="Kansas City Chiefs",
                    sibling_label="Denver Broncos",
                    rules_prose=[("market.rules_primary",
                                  KALSHI_MARKET["rules_primary"])],
                    event=KALSHI_EVENT["title"], subject="Chiefs")
    assert p["PAYOFF_STATUS"] == E.PAYOFF_VERIFIED
    got = {c["proposition_component"] for c in p["PAYOFF_PROOF"]
           if c["status"] in (VERIFIED, QUOTED)}
    for comp in E.PROOF_COMPONENTS:
        assert comp in got, comp
    for entry in p["PAYOFF_PROOF"]:
        for key in ("source_field", "raw_value", "proposition_component",
                    "status"):
            assert key in entry, key


def test_5b7_an_absent_underlying_event_blocks_verification():
    p = E.pays_1_if(venue="KALSHI", contract_id="T",
                    side_label="Kansas City Chiefs",
                    sibling_label="Denver Broncos",
                    rules_prose=[("market.rules_primary",
                                  KALSHI_MARKET["rules_primary"])],
                    event=None)
    assert p["PAYOFF_STATUS"] == E.NOT_IDENTIFIED_EVENT


def test_5c_a_label_with_no_rules_never_reaches_the_gate():
    p = E.pays_1_if(venue="KALSHI", contract_id="T", side_label="YES",
                    sibling_label="NO", rules_prose=[], event="E")
    q = E.pays_1_if(venue="PMUS", contract_id="S", side_label="LONG",
                    sibling_label="SHORT", rules_prose=[], event="E")
    gate = E.equivalence(q, p, {d: ("x", "x") for d in E.MATERIAL_DIMENSIONS})
    assert gate["EQUIVALENCE_STATUS"] == E.EQUIVALENCE_NOT_IDENTIFIED
    assert gate["ELIGIBLE_FOR_ECONOMICS"] is False


# ========================= 6. unresolved rule difference blocks VERIFIED
def _good_payoffs():
    a = E.pays_1_if(venue="PMUS", contract_id="p", side_label="Chiefs",
                    sibling_label="Broncos",
                    rules_prose=[("market.description",
                                  "The market resolves to Yes if the "
                                  "Chiefs win.")], event="Broncos at Chiefs")
    b = E.pays_1_if(venue="KALSHI", contract_id="k", side_label="Chiefs",
                    sibling_label="Broncos",
                    rules_prose=[("market.rules_primary",
                                  "If the Chiefs win, the market resolves "
                                  "to Yes.")], event="Broncos at Chiefs")
    assert a["PAYOFF_STATUS"] == E.PAYOFF_VERIFIED
    assert b["PAYOFF_STATUS"] == E.PAYOFF_VERIFIED
    return a, b


def _all_match():
    return {d: ("same", "same") for d in E.MATERIAL_DIMENSIONS}


def test_6_equivalence_cannot_be_verified_with_an_unresolved_rule():
    a, b = _good_payoffs()
    dims = _all_match()
    dims["OVERTIME"] = ("Overtime is included", None)   # venue B silent
    gate = E.equivalence(a, b, dims)
    assert gate["EQUIVALENCE_STATUS"] == E.EQUIVALENCE_NOT_IDENTIFIED
    assert "OVERTIME" in gate["UNRESOLVED_DIMENSIONS"]
    assert gate["ELIGIBLE_FOR_ECONOMICS"] is False


def test_6b_two_silences_are_not_agreement():
    a, b = _good_payoffs()
    dims = _all_match()
    dims["VOID"] = (None, None)
    gate = E.equivalence(a, b, dims)
    assert gate["DIMENSION_RESULTS"]["VOID"] == E.UNRESOLVED
    assert gate["EQUIVALENCE_STATUS"] == E.EQUIVALENCE_NOT_IDENTIFIED


def test_6c_a_material_difference_is_rejected_not_merely_unidentified():
    a, b = _good_payoffs()
    dims = _all_match()
    dims["LINE"] = (21.5, 20.5)
    gate = E.equivalence(a, b, dims)
    assert gate["EQUIVALENCE_STATUS"] == E.EQUIVALENCE_REJECTED
    assert gate["MISMATCHED_DIMENSIONS"] == ["LINE"]


def test_6d_a_fully_evidenced_pair_can_still_pass():
    """The gate must be strict, not impossible -- otherwise it proves
    nothing when it refuses."""
    a, b = _good_payoffs()
    gate = E.equivalence(a, b, _all_match())
    assert gate["EQUIVALENCE_STATUS"] == E.EQUIVALENCE_VERIFIED
    assert gate["ELIGIBLE_FOR_ECONOMICS"] is True


def test_6e_every_material_dimension_the_owner_listed_is_checked():
    for dim in ("SPORT", "LEAGUE", "UNDERLYING_EVENT", "PARTICIPANTS",
                "PROPOSITION", "LINE", "PERIOD", "START_TIME", "CLOSE_TIME",
                "SETTLEMENT_SOURCE", "SETTLEMENT_RULE", "OVERTIME",
                "EXTRA_TIME", "POSTPONEMENT", "CANCELLATION", "DRAW_TIE",
                "VOID", "OTHER_MATERIAL_CONDITIONS"):
        assert dim in E.MATERIAL_DIMENSIONS, dim


# ================================ 7. VWAP never exceeds displayed depth
def test_7_vwap_never_exceeds_displayed_depth():
    bk = K.kalshi_book_record("T", KALSHI_ORDERBOOK, 1.0, 2.0)
    displayed = sum(lv["QUANTITY"] for lv in bk["ASKS"])
    r = E.vwap(bk["ASKS"], displayed + 50_000)
    assert r["FILLED_QTY"] == displayed
    assert r["DEPTH_EXHAUSTED"] is True
    assert r["PRICES_EXTRAPOLATED"] is False
    assert sum(w["TAKEN"] for w in r["LEVELS_WALKED"]) == displayed


def test_7b_vwap_is_the_actual_walk_of_the_levels_it_took():
    levels = [{"PRICE": 0.10, "QUANTITY": 100, "LEVEL": 1},
              {"PRICE": 0.20, "QUANTITY": 100, "LEVEL": 2}]
    r = E.vwap(levels, 150)
    assert r["FILLED_QTY"] == 150
    assert abs(r["VWAP"] - ((100 * 0.10 + 50 * 0.20) / 150)) < 1e-12
    assert r["DEPTH_EXHAUSTED"] is False


def test_7c_an_empty_book_prices_nothing():
    r = E.vwap([], 100)
    assert r["FILLED_QTY"] == 0 and r["VWAP"] is None
    assert r["DEPTH_EXHAUSTED"] is True


# ============ 8. no opportunity may be generated from missing book levels
def test_8_no_positive_opportunity_from_missing_book_levels():
    """A book with one thin level must not price 10,000 contracts. The
    hedgeable size is the thinner leg's fill, and a leg that filled
    nothing produces no P&L at all."""
    thin = E.vwap([{"PRICE": 0.01, "QUANTITY": 1, "LEVEL": 1}], 10_000)
    fat = E.vwap([{"PRICE": 0.20, "QUANTITY": 10_000, "LEVEL": 1}], 10_000)
    econ = E.pair_economics(pmus_leg=fat, kalshi_leg=thin, qty=10_000)
    assert econ["MAX_HEDGEABLE_SIZE"] == 1
    assert abs(econ["TOTAL_ACQUISITION_COST_GROSS"] - 0.21) < 1e-12
    assert abs(econ["LOCKED_GROSS_PNL"] - 0.79) < 1e-12


def test_8b_a_leg_that_cannot_fill_at_all_yields_no_pnl():
    empty = E.vwap([], 100)
    fat = E.vwap([{"PRICE": 0.2, "QUANTITY": 1000, "LEVEL": 1}], 100)
    econ = E.pair_economics(pmus_leg=fat, kalshi_leg=empty, qty=100)
    assert econ["MAX_HEDGEABLE_SIZE"] == 0
    assert econ["LOCKED_GROSS_PNL"] is None
    assert econ["REASON"] == "NO_COMMON_HEDGEABLE_SIZE"


def test_8c_depth_exhaustion_is_reported_on_both_legs():
    thin = E.vwap([{"PRICE": 0.01, "QUANTITY": 1, "LEVEL": 1}], 500)
    fat = E.vwap([{"PRICE": 0.2, "QUANTITY": 10_000, "LEVEL": 1}], 500)
    econ = E.pair_economics(pmus_leg=fat, kalshi_leg=thin, qty=500)
    assert econ["KALSHI_DEPTH_EXHAUSTED"] is True
    assert econ["PMUS_DEPTH_EXHAUSTED"] is False


# ================== 9. fee-unknown state blocks LOCKED_NET_PNL entirely
def test_9_fee_unknown_prevents_locked_net_pnl():
    fat = E.vwap([{"PRICE": 0.40, "QUANTITY": 1000, "LEVEL": 1}], 100)
    other = E.vwap([{"PRICE": 0.45, "QUANTITY": 1000, "LEVEL": 1}], 100)
    econ = E.pair_economics(pmus_leg=fat, kalshi_leg=other, qty=100)
    assert econ["LOCKED_NET_PNL"] == E.NOT_IDENTIFIED_FEES
    assert econ["LOCKED_NET_BPS"] == E.NOT_IDENTIFIED_FEES
    assert econ["KALSHI_FEES"] is None
    # gross is still reported, clearly as gross
    assert econ["LOCKED_GROSS_PNL"] is not None
    assert any(b.startswith("KALSHI:") for b in econ["NET_BLOCKED_BY"])


def test_9b_the_kalshi_fee_registry_is_unverified_in_every_component():
    reg = E.FEE_REGISTRY["KALSHI"]
    for comp, node in reg.items():
        assert node["VERIFICATION_STATUS"] == ABSENT, comp
    assert "FROM_BETTOR_SOURCE_ONLY" in reg["TAKER_FEE_FORMULA"]["value"]
    assert E.fee_readiness("KALSHI")["NET_REPORTABLE"] is False


def test_9c_pmus_fees_alone_do_not_unblock_the_pair():
    assert E.fee_readiness("PMUS")["NET_REPORTABLE"] is True
    fat = E.vwap([{"PRICE": 0.4, "QUANTITY": 1000, "LEVEL": 1}], 100)
    econ = E.pair_economics(pmus_leg=fat, kalshi_leg=fat, qty=100)
    assert econ["LOCKED_NET_PNL"] == E.NOT_IDENTIFIED_FEES


def test_9d_the_kalshi_fee_function_refuses_rather_than_defaulting():
    try:
        E._kalshi_taker_fee(100, 0.5, E.FEE_REGISTRY)
    except RuntimeError as exc:
        assert "not venue-verified" in str(exc)
    else:
        raise AssertionError("an unverified fee must refuse, not default")


def test_9e_verifying_the_schedule_is_what_unblocks_net():
    """Proves the block is the VERIFICATION state and not dead code: with
    a verified registry the same pair reports a real net."""
    import copy
    reg = copy.deepcopy(E.FEE_REGISTRY)
    for comp in E.FEE_COMPONENTS_REQUIRED_FOR_NET:
        reg["KALSHI"][comp]["VERIFICATION_STATUS"] = VERIFIED
    reg["KALSHI"]["TAKER_FEE_FORMULA"]["COEFFICIENT"] = "0.07"
    reg["KALSHI"]["ROUNDING_RULE"]["value"] = "banker's rounding to $0.01"
    a = E.vwap([{"PRICE": 0.40, "QUANTITY": 1000, "LEVEL": 1}], 100)
    b = E.vwap([{"PRICE": 0.45, "QUANTITY": 1000, "LEVEL": 1}], 100)
    econ = E.pair_economics(pmus_leg=a, kalshi_leg=b, qty=100, registry=reg)
    assert isinstance(econ["LOCKED_NET_PNL"], float)
    assert econ["LOCKED_NET_PNL"] < econ["LOCKED_GROSS_PNL"]
    assert econ["PMUS_FEES"] > 0 and econ["KALSHI_FEES"] > 0


# ============ 10. no write endpoint is reachable through the research client
def test_10_no_write_endpoint_is_reachable():
    cli, _ = client_for([])
    for method in ("POST", "PUT", "PATCH", "DELETE", "HEAD", "get"):
        try:
            K.ReadOnlyGuard.check(method, "/markets")
        except K.ReadOnlyViolation:
            continue
        if method == "get":
            continue                       # case-insensitive GET is fine
        raise AssertionError("method %s was not refused" % method)
    for path in ("/portfolio/balance", "/portfolio/orders",
                 "/portfolio/positions", "/portfolio/fills",
                 "/portfolio/settlements", "/exchange/schedule",
                 "/markets/../portfolio/orders", "/markets/X/../../orders"):
        try:
            K.ReadOnlyGuard.check("GET", path)
        except K.ReadOnlyViolation:
            continue
        raise AssertionError("path %s was not refused" % path)
    assert not hasattr(cli, "place_order")
    assert not hasattr(cli, "cancel_order")
    assert not hasattr(cli, "post")


def test_10b_the_client_exposes_only_market_data_reads():
    public = {n for n in dir(K.KalshiResearchClient)
              if not n.startswith("_")}
    assert public == {"get", "events", "market", "orderbook", "pacer",
                      "receipts", "base", "transport", "clock"} - \
        {"pacer", "receipts", "base", "transport", "clock"} | \
        {"get", "events", "market", "orderbook"}


def test_10c_the_module_never_reads_a_trading_credential():
    """Not a comment scan -- the module must have no way to obtain one."""
    for bad in ("EDGE_KALSHI_KEY_ID", "EDGE_KALSHI_PRIVATE_KEY",
                "KALSHI-ACCESS-KEY", "load_pem_private_key", "os.environ",
                "getenv", "cryptography"):
        assert bad not in SRC_K, bad
    assert not any(rx.match("/portfolio/balance") for rx in K.ALLOWED_PATHS)
    assert "portfolio" in K.DENIED_SUBSTRINGS


def test_10d_the_allowlist_admits_exactly_the_reads_phase_x_needs():
    for path in ("/events", "/markets", "/markets/KXNFL-X",
                 "/markets/KXNFL-X/orderbook", "/exchange/status"):
        K.ReadOnlyGuard.check("GET", path)          # must not raise


# ------------------------------------------------- rate safety mechanics
def test_the_pacer_holds_an_explicit_minimum_interval():
    clock = FakeClock()
    cli, _ = client_for([FakeResponse(200, {"a": 1}),
                         FakeResponse(200, {"a": 2})], clock)
    cli.get("/markets")
    first = clock.t
    cli.get("/markets")
    assert clock.t - first >= K.MIN_INTERVAL_S - 1e-9


def test_a_429_honours_retry_after_exactly_then_widens_the_interval():
    clock = FakeClock()
    cli, _ = client_for([FakeResponse(429, headers={"Retry-After": "7"}),
                         FakeResponse(200, {"ok": True})], clock)
    r = cli.get("/markets")
    assert r["ok"] is True
    assert 7.0 in clock.slept
    assert cli.pacer.events[0]["event"] == "429"
    assert cli.pacer.events[0]["interval_after"] > \
        cli.pacer.events[0]["interval_before"]


def test_a_429_without_retry_after_still_backs_off():
    clock = FakeClock()
    cli, _ = client_for([FakeResponse(429), FakeResponse(200, {"ok": 1})],
                        clock)
    cli.get("/markets")
    ev = cli.pacer.events[0]
    assert ev["event"] == "429" and ev["retry_after"] is None
    assert ev["interval_after"] == ev["interval_before"] * K.BACKOFF_MULT
    # the later success decays the interval but must never snap it back
    assert cli.pacer.interval > K.MIN_INTERVAL_S


def test_a_failed_observation_is_sealed_never_silently_abandoned():
    """The trading adapter sleeps 1 s and drops the read. A dropped read
    becomes an absence, and an absence reads as 'no opportunity there'."""
    clock = FakeClock()
    cli, _ = client_for([FakeResponse(500, text="boom")] * K.MAX_ATTEMPTS,
                        clock)
    r = cli.get("/markets/T/orderbook")
    assert r["ok"] is False
    assert r["OBSERVATION_FAILED"] is True
    assert len(r["attempts"]) == K.MAX_ATTEMPTS
    assert all(a["outcome"] == "HTTP_500" for a in r["attempts"])
    assert cli.receipts[-1] is r


def test_a_transport_exception_is_a_sealed_attempt_not_a_crash():
    clock = FakeClock()
    cli, _ = client_for([OSError("reset"), FakeResponse(200, {"ok": 1})],
                        clock)
    r = cli.get("/markets")
    assert r["ok"] is True
    assert r["attempts"][0]["outcome"] == "TRANSPORT_EXCEPTION"
    assert "OSError" in r["attempts"][0]["exception"]


def test_bad_json_is_named_and_retried_not_treated_as_an_empty_book():
    clock = FakeClock()
    cli, _ = client_for([FakeResponse(200, None, text="<html>"),
                         FakeResponse(200, {"orderbook_fp": {}})], clock)
    r = cli.get("/markets/T/orderbook")
    assert r["attempts"][0]["outcome"] == "BAD_JSON"
    assert r["ok"] is True


def test_every_receipt_carries_both_clocks_and_the_response_hash():
    clock = FakeClock()
    cli, _ = client_for([FakeResponse(200, {"ok": 1})], clock)
    r = cli.get("/markets")
    assert r["local_receipt_wall"] is not None
    assert r["local_receipt_monotonic"] is not None
    assert len(r["attempts"][-1]["response_sha256"]) == 64


def test_the_venue_rate_limit_is_never_claimed_as_established():
    assert K.KALSHI_RATE_LIMIT_NOT_ESTABLISHED is True
    clock = FakeClock()
    cli, _ = client_for([FakeResponse(200, {"ok": 1})], clock)
    assert cli.get("/markets")["rate_limit_established"] is False


def test_the_research_pacer_is_separate_from_production_trading_behaviour():
    """This path must not import, subclass or reconfigure the trading
    client -- changing production rate behaviour was explicitly excluded."""
    body = SRC_K.split('"""', 2)[2]          # past the module docstring
    for bad in ("KalshiAdapter", "edge.venues", "edge-engine", "import edge",
                "VenueAdapter"):
        assert bad not in body, bad
    assert K.ResearchPacer is not None
    assert K.MIN_INTERVAL_S >= 1.0


# --------------------------------------------------- capture synchrony
def test_capture_difference_is_measured_and_never_called_simultaneous():
    pb = K.pmus_book_record("s", PMUS_MARKET_DATA, 1000.0, 1.0)
    kb = K.kalshi_book_record("T", KALSHI_ORDERBOOK, 1000.4, 2.0)
    sync = E.capture_sync(pb, kb)
    assert abs(sync["ABS_CAPTURE_DIFFERENCE_MS"] - 400.0) < 1e-6
    assert sync["SIMULTANEOUS"] is False


# ------------------------------------------------ provenance discipline
def test_every_pays_1_if_component_carries_full_provenance():
    p = E.pays_1_if(venue="KALSHI", contract_id="T",
                    side_label="Kansas City Chiefs",
                    sibling_label="Denver Broncos",
                    rules_prose=[("market.rules_primary",
                                  KALSHI_MARKET["rules_primary"])],
                    subject="Kansas City Chiefs", line=None, period="full",
                    settlement_source="NFL", event=KALSHI_EVENT["title"])
    for comp in p["PAYOFF_PROOF"]:
        for key in ("source_field", "raw_value", "proposition_component",
                    "status"):
            assert key in comp, key
        assert comp["proposition_component"] in E.PROOF_COMPONENTS, comp


def test_the_status_vocabulary_has_exactly_three_members():
    assert K.STATUSES == (VERIFIED, QUOTED, ABSENT)
    assert "INFERRED" not in SRC_K


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(vars().items())
           if n.startswith("test_") and callable(f)]
    bad = []
    for n, f in fns:
        try:
            f()
        except Exception as exc:                       # noqa: BLE001
            bad.append((n, exc))
    for n, exc in bad:
        print("FAIL %s: %r" % (n, exc))
    print("%d passed, %d failed" % (len(fns) - len(bad), len(bad)))
    sys.exit(1 if bad else 0)
