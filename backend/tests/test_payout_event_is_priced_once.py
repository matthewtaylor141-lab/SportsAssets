"""THE PROBABILITY AND THE COST MUST DESCRIBE THE SAME PAYOUT EVENT.

A BUY_SHORT leg pays on the COMPLEMENT of the selection the de-vig prices.
Two errors are possible and they look like opposite bugs:

  * invert NEITHER -- compare p(home) against the cost of a contract that
    pays on NOT(home). That is the sign error the old LONG-only refusal
    existed to prevent;
  * invert BOTH -- the caller flips the probability AND the price space,
    which silently restores the original outcome and reads as a working
    edge with the wrong contract underneath it.

So the inversion happens exactly ONCE, inside `evaluate`, driven by an
explicit flag. The caller states which event the contract pays on; it does
not pre-invert.

AND THE COMPLEMENT IS NOT THE OTHER TEAM. On a three-way soccer book
NOT(home win) is "away win OR draw". Because the de-vig normalises over
the COMPLETE outcome set -- it refuses a partial one -- 1 - p(home) is
exactly p(away) + p(draw). Substituting p(away) prices a different event
with the same-looking number.
"""
import pytest

from sportsassets import bettor_external_shadow as EXT
from sportsassets import bettor_pinnacle_devig as devig

LONG = "ORDER_INTENT_BUY_LONG"
SHORT = "ORDER_INTENT_BUY_SHORT"


def _fee(qty, price, maker=False):
    return 0.0


def _soccer_quote(now):
    """Three-way h2h: home / away / draw, decimal odds."""
    return {"book": devig.BOOK,
            "outcomes": {"Home FC": 2.00, "Away FC": 4.00, "Draw": 4.00},
            "observed_at": now, "received_at": now,
            "event_key": "evt-1", "period": "FULL_GAME", "line": None,
            "settlement_rule": "REGULATION_PLUS_STOPPAGE"}


def _contract(**kw):
    base = {"venue": "PMUS", "condition_id": "0xabc",
            "us_market_slug": "aec-soc-home-away-2026-09-24-homefc",
            "selection": "Home FC", "sport_family": "soccer",
            "market": "h2h", "period": "FULL_GAME", "line": None,
            "settlement_rule": "REGULATION_PLUS_STOPPAGE",
            "event_key": "evt-1"}
    base.update(kw)
    return base


def _run(*, complement, ask, now=1_790_000_000.0):
    return EXT.evaluate(
        contract=_contract(), quote=_soccer_quote(now),
        market_state={"ask": ask, "depth": 5000.0, "readable": True},
        execution_estimate={"p_fill": None,
                            "basis": "P_FILL_NOT_IDENTIFIED",
                            "crossing": True},
        size=1.0, risk={"permitted": True, "reason": "shadow"},
        fee_fn=_fee, now=now, outcome_books=3, armed=True,
        payout_is_complement=complement)


def test_the_long_leg_prices_the_selection_itself():
    rec = _run(complement=False, ask=0.50)
    assert rec["payout_is_complement"] is False
    assert rec["payout_event"] == "Home FC"
    assert rec["probability"] == pytest.approx(
        rec["probability_of_selection"])
    # de-vigged p(home) on 2.00/4.00/4.00 is 0.50
    assert rec["probability"] == pytest.approx(0.50, abs=1e-9)
    assert rec["estimated_edge_per_contract"] == pytest.approx(0.0, abs=1e-9)


def test_the_short_leg_prices_the_complement_exactly_once():
    rec = _run(complement=True, ask=0.50)
    assert rec["payout_is_complement"] is True
    assert rec["payout_event"] == "NOT(Home FC)"
    # the selection's own probability is preserved on the row
    assert rec["probability_of_selection"] == pytest.approx(0.50, abs=1e-9)
    # and the one compared against the cost is its complement
    assert rec["probability"] == pytest.approx(0.50, abs=1e-9)
    assert rec["probability"] + rec["probability_of_selection"] == \
        pytest.approx(1.0, abs=1e-9)


def test_an_asymmetric_book_shows_the_inversion_actually_happened():
    """With p(home) = 0.50 the complement is also 0.50 and a double
    inversion would be invisible. Skew the book so the two differ."""
    now = 1_790_000_000.0
    q = {"book": devig.BOOK,
         "outcomes": {"Home FC": 1.25, "Away FC": 8.00, "Draw": 8.00},
         "observed_at": now, "received_at": now, "event_key": "evt-1",
         "period": "FULL_GAME", "line": None,
         "settlement_rule": "REGULATION_PLUS_STOPPAGE"}
    kw = dict(contract=_contract(), quote=q,
              market_state={"ask": 0.10, "depth": 5000.0, "readable": True},
              execution_estimate={"p_fill": None,
                                  "basis": "P_FILL_NOT_IDENTIFIED",
                                  "crossing": True},
              size=1.0, risk={"permitted": True, "reason": "shadow"},
              fee_fn=_fee, now=now, outcome_books=3, armed=True)
    lg = EXT.evaluate(**kw, payout_is_complement=False)
    sh = EXT.evaluate(**kw, payout_is_complement=True)
    p_home = lg["probability"]
    assert p_home > 0.70, p_home
    assert sh["probability"] == pytest.approx(1.0 - p_home, abs=1e-9)
    assert sh["probability"] != pytest.approx(p_home)
    # the edges differ by exactly the inversion, nothing else
    assert (lg["estimated_edge_per_contract"]
            - sh["estimated_edge_per_contract"]) == pytest.approx(
        2 * p_home - 1.0, abs=1e-9)


def test_the_complement_is_both_other_outcomes_not_the_other_team():
    """NOT(home) on a three-way book == p(away) + p(draw)."""
    now = 1_790_000_000.0
    q = {"book": devig.BOOK,
         "outcomes": {"Home FC": 1.60, "Away FC": 5.00, "Draw": 4.20},
         "observed_at": now, "received_at": now, "event_key": "evt-1",
         "period": "FULL_GAME", "line": None,
         "settlement_rule": "REGULATION_PLUS_STOPPAGE"}
    val = devig.valuation(contract=_contract(), quote=q, now=now)
    dv = val["devigged"]
    assert len(dv) == 3, dv
    p_home, p_away, p_draw = dv["Home FC"], dv["Away FC"], dv["Draw"]
    assert sum(dv.values()) == pytest.approx(1.0, abs=1e-9)

    complement = 1.0 - p_home
    assert complement == pytest.approx(p_away + p_draw, abs=1e-9)
    # and it is materially NOT the away price alone
    assert complement > p_away + 0.10, (complement, p_away)


def test_the_row_carries_both_numbers_so_a_reader_cannot_confuse_them():
    rec = _run(complement=True, ask=0.50)
    for field in ("probability", "probability_of_selection",
                  "payout_event", "payout_is_complement",
                  "complement_note"):
        assert field in rec, field
    assert "never the opposing team alone" in rec["complement_note"]


def test_the_caller_must_not_pre_invert():
    """The loop states the event; it does not flip the probability. If it
    ever did both, the short edge would equal the long edge."""
    import inspect

    from sportsassets.workers import ext_pinnacle_loop as L

    src = "\n".join(l.split("#", 1)[0]
                    for l in inspect.getsource(L.cycle).splitlines())
    assert 'payout_is_complement=bool(ident["payout_is_complement"])' in src, (
        "the loop must pass the resolver's EXPLICIT flag, never derive it "
        "from the intent -- deriving it is what fabricated a +0.30 edge")
    assert "1.0 - " not in src and "1 - val" not in src, (
        "the loop is inverting a probability itself")
