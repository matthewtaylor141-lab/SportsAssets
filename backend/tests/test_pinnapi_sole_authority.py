"""PINNAPI IS THE SOLE PROBABILITY AUTHORITY OF THE INVESTMENT POLICY (V3).

Owner decision 2026-10-03. Pure tests on the completed-game policy's match:

  * a PinnAPI valuation whose own outcome count is 1 qualifies its
    probability under V3 -- the multi-book floor's refusal is recorded as NOT
    APPLIED BY POLICY (never dropped, never satisfied), the count stays 1, and
    the evidence is SINGLE_SOURCE_PINNAPI, never "corroborated";
  * a valuation from any other provider keeps the floor;
  * every other probability-stage refusal (de-vig, mapping, freshness, a
    missing count) still refuses under V3;
  * identity and payout-outcome checks are untouched.
"""
from __future__ import annotations

from sportsassets import bettor_external_shadow as EXT
from sportsassets import pinnapi_primary as P
from sportsassets.agents import derek_policy as DP
from sportsassets.agents import paper_benchmark as PB


def _row(**over):
    row = {"id": 1, "provider": P.PROVIDER, "probability": 0.58,
           "outcome_books": 1, "refusals": [EXT.R_THIN_OUTCOME],
           "us_market_slug": "mlb-test-slug", "payout_event": "Home",
           "contract_selection": "Home", "buy_intent": DP.LONG,
           "period": "FULL_GAME", "market": "h2h", "venue": "PMUS",
           "event_key": "ev-1", "condition_id": "c-1",
           "observed_at": None, "received_at": None}
    row.update(over)
    return row


def _probability_check(match):
    return next(c for c in match["checks"]
                if c["check"] == "probability_qualified_by_the_lane")


def test_the_pinned_names_agree_with_their_owners():
    assert PB.PINNAPI_PROVIDER == P.PROVIDER
    assert PB.R_THIN_OUTCOME == EXT.R_THIN_OUTCOME
    assert PB.CG_VERSION == "PINNACLE_COMPLETED_GAME_PAPER_V3"
    # the general floor is NOT lowered: other providers still need two books
    assert EXT.MIN_OUTCOME_BOOKS == 2


def test_a_single_source_pinnapi_valuation_qualifies_its_probability():
    row = _row()
    cand = DP.candidate_from_row(row)
    m = PB.completed_game_match(cand, row)
    chk = _probability_check(m)
    assert chk["passed"] is True, chk
    assert chk["not_applied_by_policy"] == [EXT.R_THIN_OUTCOME]
    assert PB.R_PROBABILITY_UNQUALIFIED not in m["refusals"]
    auth = m["probability_authority"]
    assert auth["applies"] is True and auth["basis"] == PB.PINNAPI_SOLE
    assert auth["evidence"] == "SINGLE_SOURCE_PINNAPI"
    assert auth["outcome_books"] == 1                 # never raised to 2
    assert auth["provider"] == P.PROVIDER
    assert "corroborat" not in repr(auth).lower()


def test_another_provider_keeps_the_multi_book_floor():
    row = _row(provider="the-odds-api.com/v4")
    m = PB.completed_game_match(DP.candidate_from_row(row), row)
    chk = _probability_check(m)
    assert chk["passed"] is False
    assert chk["lane_refusals"] == [EXT.R_THIN_OUTCOME]
    assert PB.R_PROBABILITY_UNQUALIFIED in m["refusals"]
    assert m["probability_authority"]["applies"] is False
    assert m["probability_authority"]["multi_book_floor"] == "APPLIES"


def test_a_pinnapi_valuation_without_a_recorded_count_still_refuses():
    for books in (None, 0, "x"):
        row = _row(outcome_books=books)
        m = PB.completed_game_match(DP.candidate_from_row(row), row)
        assert _probability_check(m)["passed"] is False, books
        assert m["probability_authority"]["applies"] is False


def test_other_probability_stage_refusals_still_refuse_a_pinnapi_valuation():
    probability_codes = [c for c, stage in EXT.STAGE_OF.items()
                         if stage == "1_PROBABILITY" and c != EXT.R_THIN_OUTCOME]
    assert probability_codes, "the probability stage names no other refusal"
    for code in probability_codes:
        row = _row(refusals=[EXT.R_THIN_OUTCOME, code])
        m = PB.completed_game_match(DP.candidate_from_row(row), row)
        chk = _probability_check(m)
        assert chk["passed"] is False, code
        assert chk["lane_refusals"] == [code], chk
        assert chk["not_applied_by_policy"] == [EXT.R_THIN_OUTCOME]


def test_a_mis_mapped_pinnapi_valuation_still_fails_identity():
    row = _row(us_market_slug=None, payout_event=None)
    m = PB.completed_game_match(DP.candidate_from_row(row), row)
    assert m["established"] is False
    ident = next(c for c in m["checks"] if c["check"] == DP.C_IDENTITY)
    assert ident["passed"] is False
    # the probability itself qualified on PinnAPI alone; identity refused
    assert _probability_check(m)["passed"] is True


def test_the_strict_benchmark_does_not_inherit_the_v3_rule():
    row = _row()
    m = PB.contract_match(DP.candidate_from_row(row), row)
    assert _probability_check(m)["passed"] is False
    assert PB.R_PROBABILITY_UNQUALIFIED in m["refusals"]
