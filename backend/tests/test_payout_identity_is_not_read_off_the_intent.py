"""A SHORT INTENT DOES NOT INVERT THE PAYOUT EVENT.

THE DEFECT THIS EXISTS FOR, and it was mine. `resolve_venue_identity` did:

    short = intent == "ORDER_INTENT_BUY_SHORT"
    pays_on_priced_outcome = not short
    payout_event = "NOT(%s)" % priced_outcome if short else priced_outcome

reading the payout event off the INTENT. `premap.resolve` is asked for a
specific OUTCOME and returns the row matching it together with the intent
that BUYS it. A BUY_SHORT result for "Chicago Cubs" means Cubs is the
venue's SHORT side -- the contract still PAYS ON CUBS. It does not become
NOT(Cubs).

The consequence was not cosmetic. `payout_is_complement` then inverted a
probability that already described the selected exposure:

    requested Cubs, matched side Cubs, BUY_SHORT
    p(Cubs) = 0.30, acquisition cost = 0.40
    true edge before fees  = 0.30 - 0.40 = -0.10
    defect edge            = 0.70 - 0.40 = +0.30

A fabricated positive edge on a contract that is a bad buy. The previous
test in this suite asserted `payout_event == "NOT(Chicago Cubs)"`, so it
encoded the defect and was corrected rather than kept.

THE THREE RELATIONSHIPS ARE NOW SEPARATE:

  1 intent            -> which LADDER supplies acquisition cost
  2 requested outcome + matched venue side + settlement terms
                      -> which EVENT pays
  3 complement applied ONLY when the probability's source event is
    demonstrably the complement of the payout event

The inversion machinery is NOT disabled: a genuinely complementary payout
is a real case and is tested below, as is soccer's distinction between
"away win" and "home does not win".
"""
import asyncio

import pytest

from sportsassets import bettor_external_shadow as EXT
from sportsassets import bettor_pinnacle_devig as devig
from sportsassets.workers import ext_pinnacle_loop as loop

LONG = "ORDER_INTENT_BUY_LONG"
SHORT = "ORDER_INTENT_BUY_SHORT"



# THE CATALOGUE ROW THE PERIOD CHECK READS.
#
# `resolve_venue_identity` now asks `us_premap` for the venue's own kind,
# event and side, and for how many contracts it publishes for that event.
# A `_Conn` with no `fetchrow` makes that read raise, which correctly
# refuses the identity -- and would make these tests assert nothing about
# the payout-orientation rule they exist for. So the stub answers with the
# catalogue's own fields, which is what production reads.
class _CatalogueConn:
    def __init__(self, *, kind="side", event_slug=None, side_norm=None,
                 siblings=2):
        self._row = {"kind": kind, "event_slug": event_slug,
                     "side_norm": side_norm,
                     "sibling_markets": siblings}

    async def fetchrow(self, sql, *args):
        return self._row

class _Conn:
    pass


def _resolve_as(*, market_slug, intent, side_norm, matched_by="keys"):
    """The shape `premap.resolve` ACTUALLY returns.

    THIS STUB USED TO LIE, and it is why the bug it was guarding against
    had a sibling nobody saw. It returned `side_norm`, `identifier` and
    `question` -- the three keys `resolve_venue_identity` was reading --
    while the real resolver returns the side under `outcome`, the
    identifier under `market_slug` and the question under `title`. In
    production all three reads came back None; here they came back
    populated, so the assertions below passed against a shape that does
    not exist. Three separate test files stubbed it the same wrong way.

    The parameter is still called `side_norm`, because that is the venue's
    own name for the thing; only the key it is returned under is fixed.
    """
    async def _fake(conn, title, event_title, outcome, slug, **kw):
        return {"market_slug": market_slug, "intent": intent,
                "outcome": side_norm,
                "title": "Will %s win?" % side_norm,
                "matched_by": matched_by, "score": 1.0}
    return _fake


def _identity(*, intent, side_norm, requested):
    from sportsassets.workers import premap as _pm

    orig = _pm.resolve
    _pm.resolve = _resolve_as(
        market_slug="aec-mlb-chc-mia-2026-09-24-cubs",
        intent=intent, side_norm=side_norm)
    try:
        return asyncio.run(loop.resolve_venue_identity(
            _CatalogueConn(event_slug="mlb-chc-mia-2026-09-24", side_norm="cubs"),
            market_row={"slug": "mlb-chc-mia-2026-09-24",
                        "condition_id": "0xabc",
                        "title": "Will the Cubs beat the Marlins?",
                        "event_title": "Chicago Cubs vs Miami Marlins"},
            priced_outcome=requested))
    finally:
        _pm.resolve = orig


# ── THE REQUIRED REGRESSION, EXACT NUMBERS ───────────────────────────

def test_cubs_short_side_still_pays_on_cubs():
    """Requested Cubs, matched venue side Cubs, BUY_SHORT."""
    out = _identity(intent=SHORT, side_norm="cubs", requested="Chicago Cubs")
    assert out["ok"] is True, out
    assert out["intent"] == SHORT
    # THE INTENT PICKS THE LADDER, and only that
    assert out["ladder_side"] == "BID"
    # THE PAYOUT EVENT IS THE REQUESTED OUTCOME, not its negation
    assert out["payout_event"] == "Chicago Cubs"
    assert out["payout_event"] != "NOT(Chicago Cubs)", (
        "this is the defect: the payout event was read off the intent")
    assert out["probability_event"] == "Chicago Cubs"
    assert out["payout_is_complement"] is False
    # and the resolver's own evidence survived
    assert out["matched_side_norm"] == "cubs"
    assert out["resolver_asked_for"] == "Chicago Cubs"
    assert out["matched_by"] == "keys"
    assert out["matched_identifier"] == "aec-mlb-chc-mia-2026-09-24-cubs"


def test_the_edge_is_minus_ten_cents_not_plus_thirty():
    """p(Cubs)=0.30 against an acquisition cost of 0.40.

    -0.10 is a bad buy correctly priced. +0.30 is the fabricated edge the
    defect produced, and it is the number this test exists to forbid.
    """
    out = _identity(intent=SHORT, side_norm="cubs", requested="Chicago Cubs")
    now = 1_790_000_000.0
    # decimal odds giving de-vigged p(Cubs) = 0.30 exactly on a 2-way book
    quote = {"book": devig.BOOK,
             "outcomes": {"Chicago Cubs": 10.0 / 3.0,
                          "Miami Marlins": 10.0 / 7.0},
             "observed_at": now, "received_at": now,
             "event_key": "evt", "period": "FULL_GAME", "line": None,
             "settlement_rule": "NINE_INNINGS"}
    contract = {"venue": "PMUS", "condition_id": "0xabc",
                "us_market_slug": out["us_market_slug"],
                "selection": "Chicago Cubs", "sport_family": "baseball",
                "market": "h2h", "period": "FULL_GAME", "line": None,
                "settlement_rule": "NINE_INNINGS", "event_key": "evt"}
    rec = EXT.evaluate(
        contract=contract, quote=quote,
        # the ACQUISITION cost for the short leg, in cost space
        market_state={"ask": 0.40, "depth": 5000.0, "readable": True},
        execution_estimate={"p_fill": None,
                            "basis": "P_FILL_NOT_IDENTIFIED",
                            "crossing": True},
        size=1.0, risk={"permitted": True, "reason": "shadow"},
        fee_fn=lambda qty, price, maker=False: 0.0,
        now=now, outcome_books=2, armed=True,
        payout_is_complement=bool(out["payout_is_complement"]))

    assert rec["probability"] == pytest.approx(0.30, abs=1e-9)
    assert rec["payout_event"] == "Chicago Cubs"
    assert rec["estimated_edge_per_contract"] == pytest.approx(
        -0.10, abs=1e-9)
    assert rec["estimated_edge_per_contract"] < 0
    assert rec["estimated_edge_per_contract"] != pytest.approx(0.30)
    assert rec["decision"] == "NO_TRADE"


def test_a_long_side_match_is_unchanged():
    out = _identity(intent=LONG, side_norm="cubs", requested="Chicago Cubs")
    assert out["ok"] is True
    assert out["ladder_side"] == "ASK"
    assert out["payout_event"] == "Chicago Cubs"
    assert out["payout_is_complement"] is False


def test_the_wrapper_no_longer_derives_payout_from_intent():
    import inspect

    src = "\n".join(l.split("#", 1)[0] for l in
                    inspect.getsource(loop.resolve_venue_identity)
                    .splitlines())
    assert 'pays_on_priced_outcome' not in src, (
        "the discarded inference is back")
    assert '"NOT(%s)" % priced_outcome' not in src
    assert 'out["payout_is_complement"] = False' in src

    csrc = "\n".join(l.split("#", 1)[0]
                     for l in inspect.getsource(loop.cycle).splitlines())
    assert 'payout_is_complement=bool(ident["payout_is_complement"])' in csrc


# ── THE INVERSION IS NOT DISABLED ────────────────────────────────────

def test_a_genuinely_complementary_payout_still_inverts():
    """Kept deliberately. The flag exists for the real case: a payout
    event that IS the complement of the probability's source event. That
    must still invert, exactly once, inside evaluate."""
    now = 1_790_000_000.0
    quote = {"book": devig.BOOK,
             "outcomes": {"Chicago Cubs": 10.0 / 3.0,
                          "Miami Marlins": 10.0 / 7.0},
             "observed_at": now, "received_at": now,
             "event_key": "evt", "period": "FULL_GAME", "line": None,
             "settlement_rule": "NINE_INNINGS"}
    contract = {"venue": "PMUS", "condition_id": "0xabc",
                "us_market_slug": "aec-x", "selection": "Chicago Cubs",
                "sport_family": "baseball", "market": "h2h",
                "period": "FULL_GAME", "line": None,
                "settlement_rule": "NINE_INNINGS", "event_key": "evt"}
    kw = dict(contract=contract, quote=quote,
              market_state={"ask": 0.40, "depth": 10.0, "readable": True},
              execution_estimate={"p_fill": None,
                                  "basis": "P_FILL_NOT_IDENTIFIED",
                                  "crossing": True},
              size=1.0, risk={"permitted": True, "reason": "shadow"},
              fee_fn=lambda qty, price, maker=False: 0.0,
              now=now, outcome_books=2, armed=True)
    same = EXT.evaluate(**kw, payout_is_complement=False)
    comp = EXT.evaluate(**kw, payout_is_complement=True)
    assert same["probability"] == pytest.approx(0.30, abs=1e-9)
    assert comp["probability"] == pytest.approx(0.70, abs=1e-9)
    assert comp["payout_event"] == "NOT(Chicago Cubs)"
    assert comp["probability_of_selection"] == pytest.approx(0.30, abs=1e-9)
    # inverted once: the two edges differ by exactly 2p - 1
    assert (comp["estimated_edge_per_contract"]
            - same["estimated_edge_per_contract"]) == pytest.approx(
        1.0 - 2 * 0.30, abs=1e-9)


def test_soccer_home_not_win_is_not_away_win():
    """Retained. On a three-way book the complement of "home win" is
    "away win OR draw"; substituting p(away) prices a different event."""
    now = 1_790_000_000.0
    q = {"book": devig.BOOK,
         "outcomes": {"Home FC": 1.60, "Away FC": 5.00, "Draw": 4.20},
         "observed_at": now, "received_at": now, "event_key": "e",
         "period": "FULL_GAME", "line": None,
         "settlement_rule": "REGULATION_PLUS_STOPPAGE"}
    c = {"venue": "PMUS", "condition_id": "0x1", "us_market_slug": "aec-s",
         "selection": "Home FC", "sport_family": "soccer", "market": "h2h",
         "period": "FULL_GAME", "line": None,
         "settlement_rule": "REGULATION_PLUS_STOPPAGE", "event_key": "e"}
    dv = devig.valuation(contract=c, quote=q, now=now)["devigged"]
    assert len(dv) == 3
    assert sum(dv.values()) == pytest.approx(1.0, abs=1e-9)
    not_home = 1.0 - dv["Home FC"]
    assert not_home == pytest.approx(dv["Away FC"] + dv["Draw"], abs=1e-9)
    assert not_home > dv["Away FC"] + 0.10, (not_home, dv["Away FC"])


def test_the_complement_note_states_the_rule_that_was_broken():
    out = _identity(intent=SHORT, side_norm="cubs", requested="Chicago Cubs")
    note = out["complement_note"]
    assert "not that the payout inverted" in note
    assert "the SAME event" in note
    assert out["intent_selects"].startswith("the ladder")
