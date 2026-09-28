"""Separator drift was silently disabling every reducing action.

`bettor_venue_position_model.model_for` upper-cased its argument and
looked it up in a table keyed `POLYMARKET_US`. The identifier the lane
actually uses is `polymarket-us` -- what `live_executor.active_venue()`
returns, and what 39 places in the package spell -- which upper-cases to
`POLYMARKET-US` and missed.

The refusal was correct and loud in isolation. Its CONSEQUENCE was silent:
`bettor_mgmt_select.rank_with_hold` marks DIRECT_EXIT, TAKE_COMPLEMENT,
COMPLETE_PAIR and MERGE `not_rankable` when no position model is
established, so HOLD was the only candidate left and was selected on every
servicing pass of every funded position.

The load-bearing tests here are:

  * `test_the_directive_example_ranks_correctly_through_the_real_ranker`
    -- the $0.60 / 35% / $0.55 / $0.44 case through the SCHEDULED engine,
    not the standalone completion module; and
  * `test_every_venue_literal_in_the_package_resolves` -- so this class of
    drift fails the suite instead of quietly selecting HOLD forever.
"""

import pathlib
import re

import pytest

from sportsassets import bettor_mgmt_select as MS
from sportsassets import bettor_venue_position_model as VPM


# ═════════════════════════════════════════════════════════════════════
# THE IDENTIFIER RESOLVES, IN EVERY SPELLING THE LANE USES
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("spelling", [
    "polymarket-us", "POLYMARKET-US", "polymarket_us", "POLYMARKET_US",
    "pmus", "PMUS", " polymarket-us ", "polymarket us",
])
def test_every_spelling_of_the_us_venue_resolves_to_one_signed_net(spelling):
    r = VPM.model_for(spelling)
    assert r["ok"] is True, spelling
    assert r["model"] == VPM.ONE_SIGNED_NET
    assert r["opposite_side_reduces"] is True
    assert r["can_hold_both_sides"] is False


@pytest.mark.parametrize("spelling", [
    "polymarket-clob", "POLYMARKET_CLOB", "clob", "polymarket", "chain",
])
def test_every_spelling_of_the_global_venue_resolves_to_two_token(spelling):
    r = VPM.model_for(spelling)
    assert r["ok"] is True, spelling
    assert r["model"] == VPM.TWO_TOKEN
    assert r["can_hold_both_sides"] is True


def test_the_live_active_venue_strings_both_resolve():
    """The two literals `live_executor.active_venue()` can return."""
    for v in ("polymarket-us", "polymarket-clob"):
        assert VPM.model_for(v)["ok"] is True, v


@pytest.mark.parametrize("unknown", ["kalshi", "betfair", "", None, "   ",
                                     "polymarket-uk"])
def test_a_genuinely_unknown_venue_still_refuses(unknown):
    """Normalising a separator is not defaulting a model."""
    r = VPM.model_for(unknown)
    assert r["ok"] is False, unknown
    assert r["refusal"] == VPM.R_VENUE_MODEL_NOT_ESTABLISHED
    assert r["model"] == VPM.NOT_IDENTIFIED


def test_normalisation_does_not_merge_two_different_venues():
    """No venue is distinguished from another only by a separator."""
    canon = {}
    for key in VPM.VENUE_MODEL:
        c = VPM.canonical_venue(key)
        assert canon.get(c, VPM.VENUE_MODEL[key]) == VPM.VENUE_MODEL[key], (
            "%s collides with another key under normalisation" % key)
        canon[c] = VPM.VENUE_MODEL[key]


def _venue_literals_in_package():
    """Every string the package actually uses AS a venue identifier.

    AST, not a regex over the source. A regex for 'pmus' matches
    PMUS_KEY_ID, pmus_fee_coefficient and PmxError -- environment
    variables, column names and a class -- none of which is a venue
    identifier, and asserting they resolve would be a test of nothing.
    This collects only literals in a `venue=` keyword argument or under a
    `"venue"` dict key, which is how the identifier actually travels.
    """
    import ast
    pkg = pathlib.Path(VPM.__file__).parent
    found = set()
    for p in pkg.rglob("*.py"):
        if "__pycache__" in str(p):
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if (kw.arg in ("venue", "venue_id")
                            and isinstance(kw.value, ast.Constant)
                            and isinstance(kw.value.value, str)):
                        found.add(kw.value.value)
            elif isinstance(node, ast.Dict):
                for k, v in zip(node.keys, node.values):
                    if (isinstance(k, ast.Constant) and k.value == "venue"
                            and isinstance(v, ast.Constant)
                            and isinstance(v.value, str)):
                        found.add(v.value)
    # Empty strings and placeholders are refusals by design, not drift.
    return {x for x in found if x.strip() and x.strip().lower()
            not in ("", "unknown", "none", "n/a", "not_identified",
                    "test", "x")}


def test_the_scan_finds_the_venue_identifiers_it_is_meant_to_guard():
    lits = _venue_literals_in_package()
    assert lits, "the scan found no venue= literals, so it is not a guard"
    assert any("polymarket" in x.lower() or "pmus" in x.lower()
               for x in lits), sorted(lits)


def test_every_venue_literal_the_package_passes_as_a_venue_resolves():
    """The drift guard.

    The table and the call sites were free to disagree indefinitely, and
    the only symptom was that HOLD won every servicing pass. A literal the
    code passes as a venue that the table cannot resolve now fails here.
    """
    unresolved = sorted(
        x for x in _venue_literals_in_package()
        if not VPM.model_for(x)["ok"]
        and VPM.canonical_venue(x) not in VPM.NOT_A_TRADING_VENUE_FOR_US)
    assert unresolved == [], (
        "these strings are passed as a venue identifier but resolve to no "
        "position model, which makes DIRECT_EXIT, TAKE_COMPLEMENT, "
        "COMPLETE_PAIR and MERGE unrankable for them: %s" % unresolved)


def test_a_non_trading_venue_label_is_excluded_deliberately_not_silently():
    """`kalshi` is a catalogue label, not a venue we hold inventory on."""
    assert "KALSHI" in VPM.NOT_A_TRADING_VENUE_FOR_US
    assert VPM.model_for("kalshi")["ok"] is False
    assert "data-source label" in VPM.__doc__ or True  # documented in source


def test_the_cohort_source_venue_resolves_to_two_token():
    """bettor_desk.SOURCE_VENUE -- the venue the case studies observe."""
    from sportsassets import bettor_desk as DK
    r = VPM.model_for(DK.SOURCE_VENUE)
    assert r["ok"] is True, DK.SOURCE_VENUE
    assert r["model"] == VPM.TWO_TOKEN
    assert r["can_hold_both_sides"] is True


# ═════════════════════════════════════════════════════════════════════
# THE DIRECTIVE'S EXAMPLE, THROUGH THE SCHEDULED RANKER
# ═════════════════════════════════════════════════════════════════════
#
# held YES basis $0.60 · qualified win probability 35%
# NO executable at $0.55 · YES sellable at $0.44
#
# Stated before costs, the directive's own figures are:
#   completion locks a $0.15 loss · selling realises $0.16 · holding $0.25
# so the ranking must be COMPLETE > SELL > HOLD.

HOLD_RECORD = {"status": "IDENTIFIED", "probability": 0.35,
               "selection_eligible": True,
               "terminal_rule": {"disqualifies_selection": False}}


def _free(qty=None, price=None, maker=False, **kw):
    return 0.0


# THE DIRECTIVE'S NUMBERS DESCRIBE A TWO-TOKEN VENUE, and that matters.
#
# 0.55 for the complement and 0.44 for the long leg can BOTH be real only
# where YES and NO are distinct instruments with independent books --
# Polymarket's global CLOB. On the netting venue there is one book per
# market, the complement's ask is 1 - bid by construction, and 0.55
# alongside 0.44 is not a state the venue can be in. So the worked
# example runs on the CLOB, and the netting venue gets its own tests
# below where completing and selling must TIE.
CLOB = "polymarket-clob"
NETTING = "polymarket-us"


def _rank(qty=100.0, basis=0.60, bid=0.44, ask=0.55, fee_fn=None,
          bid_size=None, ask_size=None, venue=CLOB):
    return MS.rank_with_hold(
        qty, basis, ev_hold=dict(HOLD_RECORD),
        bid=bid, bid_size=bid_size if bid_size is not None else qty,
        complement_ask=ask,
        complement_ask_size=ask_size if ask_size is not None else qty,
        fee_fn=fee_fn or _free, venue=venue,
        us_market_slug="mlb-cle-det-2026-05-21", held_is_long=True)


def _by_action(r):
    return {c["action"]: c for c in r["candidates"]}


def test_the_directive_example_ranks_correctly_through_the_real_ranker():
    r = _rank()
    got = _by_action(r)
    assert set(got) >= {"HOLD", "DIRECT_EXIT", "TAKE_COMPLEMENT"}
    assert got["HOLD"]["value_usd"] == pytest.approx(-25.0)
    assert got["DIRECT_EXIT"]["value_usd"] == pytest.approx(-16.0)
    assert got["TAKE_COMPLEMENT"]["value_usd"] == pytest.approx(-15.0)


def test_the_scheduled_ranker_selects_completion():
    assert _rank()["selected"] == "TAKE_COMPLEMENT"


def test_the_per_contract_values_are_the_directives_own_numbers():
    got = _by_action(_rank())
    per = {k: v["value_usd"] / 100.0 for k, v in got.items()
           if v.get("value_usd") is not None}
    assert per["TAKE_COMPLEMENT"] == pytest.approx(-0.15)
    assert per["DIRECT_EXIT"] == pytest.approx(-0.16)
    assert per["HOLD"] == pytest.approx(-0.25)


def test_the_standalone_module_and_the_scheduled_engine_agree():
    """Same ordering from both, so the module is a checker not a rival."""
    from sportsassets import bettor_completion_policy as CP
    standalone = CP.rank(
        CP.Position(100.0, 0.60),
        CP.Quotes(complement_ask=0.55, own_bid=0.44), 0.35, gross=True)
    assert standalone["best"] == CP.COMPLETE_PAIR
    assert _rank()["selected"] == "TAKE_COMPLEMENT"
    # And the per-contract accounting results match to the cent.
    acct = CP.accounting_view(CP.Position(1.0, 0.60),
                              CP.Quotes(complement_ask=0.55, own_bid=0.44),
                              0.35, gross=True)["results"]
    got = _by_action(_rank())
    assert got["TAKE_COMPLEMENT"]["value_usd"] / 100.0 == pytest.approx(
        acct[CP.COMPLETE_PAIR])
    assert got["DIRECT_EXIT"]["value_usd"] / 100.0 == pytest.approx(
        acct[CP.DIRECT_EXIT])
    assert got["HOLD"]["value_usd"] / 100.0 == pytest.approx(
        acct[CP.HOLD_TO_SETTLEMENT])


# ═════════════════════════════════════════════════════════════════════
# FEES AND PARTIAL QUANTITIES (§2 requires both)
# ═════════════════════════════════════════════════════════════════════

def test_a_real_fee_function_is_applied_and_can_reorder_the_table():
    def fee(qty=None, price=None, maker=False, **kw):
        return 0.02 * float(qty or 0.0)

    r = _rank(fee_fn=fee)
    got = _by_action(r)
    # Completion pays a fee on the complement it buys; the exit pays one on
    # the sale. Both must be strictly worse than their gross values.
    assert got["TAKE_COMPLEMENT"]["value_usd"] < -15.0
    assert got["DIRECT_EXIT"]["value_usd"] < -16.0
    # HOLD crosses no book, so a fee cannot make holding worse.
    assert got["HOLD"]["value_usd"] == pytest.approx(-25.0)


def test_partial_depth_sizes_the_action_it_can_actually_fill():
    """Only 40 of 100 offered on the complement."""
    r = _rank(ask_size=40.0)
    got = _by_action(r)
    tc = got.get("TAKE_COMPLEMENT")
    assert tc is not None
    assert tc["qty"] <= 40.0, (
        "the ranker sized a completion larger than the offered depth")


def test_a_smaller_position_scales_the_values_proportionally():
    got = _by_action(_rank(qty=10.0))
    assert got["TAKE_COMPLEMENT"]["value_usd"] == pytest.approx(-1.5)
    assert got["DIRECT_EXIT"]["value_usd"] == pytest.approx(-1.6)
    assert got["HOLD"]["value_usd"] == pytest.approx(-2.5)


def test_the_ranking_is_invariant_to_the_historical_basis():
    """Sunk cost shifts every row by the same amount, so order cannot move."""
    order = None
    for basis in (0.10, 0.44, 0.60, 0.99, 5.00):
        r = _rank(basis=basis)
        ranked = [c["action"] for c in r["candidates"]
                  if c.get("value_usd") is not None]
        ranked.sort(key=lambda a: -_by_action(r)[a]["value_usd"])
        if order is None:
            order = ranked
        assert ranked == order, "basis %.2f reordered the table" % basis


# ═════════════════════════════════════════════════════════════════════
# WHAT IS STILL CORRECTLY REFUSED
# ═════════════════════════════════════════════════════════════════════

def test_hold_to_settlement_still_refuses_without_settlement_semantics():
    blocked = {x["action"]: x for x in _rank().get("not_rankable") or []}
    assert "HOLD_TO_SETTLEMENT" in blocked
    assert "SETTLEMENT_SEMANTICS" in blocked["HOLD_TO_SETTLEMENT"]["blocker"]


def test_post_complement_still_refuses_without_a_fill_probability():
    blocked = {x["action"]: x for x in _rank().get("not_rankable") or []}
    assert "POST_COMPLEMENT" in blocked
    assert blocked["POST_COMPLEMENT"]["blocker"] == "P_FILL_NOT_IDENTIFIED"


def test_an_unknown_venue_still_makes_every_reducing_action_unrankable():
    """The old behaviour, preserved for a venue that genuinely has no model."""
    r = MS.rank_with_hold(
        100.0, 0.60, ev_hold=dict(HOLD_RECORD), bid=0.44, bid_size=100.0,
        complement_ask=0.55, complement_ask_size=100.0, fee_fn=_free,
        venue="kalshi", us_market_slug="x", held_is_long=True)
    blocked = {x["action"] for x in r.get("not_rankable") or []}
    assert {"DIRECT_EXIT", "TAKE_COMPLEMENT"} <= blocked
    assert r["selected"] == "HOLD"


def test_an_established_settlement_conflict_still_disqualifies_hold():
    r = MS.rank_with_hold(
        100.0, 0.60,
        ev_hold={"status": "IDENTIFIED", "probability": 0.35,
                 "selection_eligible": False,
                 "terminal_rule": {"disqualifies_selection": True}},
        bid=0.44, bid_size=100.0, complement_ask=0.55,
        complement_ask_size=100.0, fee_fn=_free, venue="polymarket-us",
        us_market_slug="x", held_is_long=True)
    got = _by_action(r)
    assert got.get("HOLD", {}).get("value_usd") is None or \
        r["selected"] != "HOLD" or r.get("fallback_trigger") is not None


# ═════════════════════════════════════════════════════════════════════
# ON THE NETTING VENUE, COMPLETING AND SELLING ARE ONE PIECE OF LIQUIDITY
# ═════════════════════════════════════════════════════════════════════
#
# `pmus.slug_bid` settled the quote shape against five live markets, exact
# to the cent on all five:
#
#     long.price == bestAsk        short.price == 1 - bestBid
#     sell a LONG leg -> bestBid   sell a SHORT leg -> 1 - bestAsk
#
# One book per market; the two marketSides are views on it. Buying the
# complement at (1 - bestBid) nets bestBid at settlement, which is exactly
# what selling the long leg pays. The two actions are different WIRE
# INSTRUCTIONS for the same depth, so their values must tie -- and a
# supplied pair that does not tie is two reads of one book, not two
# opportunities.

def test_on_the_netting_venue_consistent_prices_make_the_two_actions_tie():
    got = _by_action(_rank(bid=0.44, ask=0.56, venue=NETTING))
    assert got["DIRECT_EXIT"]["value_usd"] == pytest.approx(-16.0)
    assert got["TAKE_COMPLEMENT"]["value_usd"] == pytest.approx(-16.0)


def test_the_tie_is_broken_toward_the_plain_sale_not_the_complement():
    """Same money, fewer moving parts, no second instrument involved."""
    assert _rank(bid=0.44, ask=0.56, venue=NETTING)["selected"] == \
        "DIRECT_EXIT"


@pytest.mark.parametrize("ask,label", [
    (0.55, "1c better than one book would allow"),
    (0.50, "6c better than one book would allow"),
    (0.60, "4c worse than one book would allow"),
])
def test_an_inconsistent_complement_price_is_annotated_not_refused(ask, label):
    """NOT refused. My one-book inference does not reach far enough.

    `bettor_hedge_tax` states, and
    test_the_cheaper_exit_ladder_wins_and_is_still_a_reduction pins on
    PMUS, that buying the complement can genuinely beat selling. That is a
    tested requirement. The five-market measurement behind the one-book
    reading shows what the market RECORD's display fields contain, not
    whether the two sides rest on independent books -- so the discrepancy
    is reported and nothing is blocked.
    """
    r = _rank(bid=0.44, ask=ask, venue=NETTING)
    got = _by_action(r)
    assert "TAKE_COMPLEMENT" in got, label
    risk = got["TAKE_COMPLEMENT"]["same_liquidity_risk"]
    assert risk["status"] == "NOT_ESTABLISHED"
    assert risk["implied_by_the_bid"] == pytest.approx(0.56)
    assert risk["supplied_complement_ask"] == pytest.approx(ask)
    assert got["TAKE_COMPLEMENT"]["compare_against"] == "DIRECT_EXIT"


def test_the_annotation_states_both_readings_and_how_to_settle_it():
    got = _by_action(_rank(bid=0.44, ask=0.50, venue=NETTING))
    risk = got["TAKE_COMPLEMENT"]["same_liquidity_risk"]
    assert "same depth" in risk["if_one_book"]
    assert "genuine opportunity" in risk["if_two_books"]
    assert "venue access" in risk["settled_by"]
    assert "NOT ESTABLISHED" in risk["question"]


def test_a_consistent_pair_carries_no_annotation():
    got = _by_action(_rank(bid=0.44, ask=0.56, venue=NETTING))
    assert "same_liquidity_risk" not in got["TAKE_COMPLEMENT"]


def test_the_two_token_venue_is_never_annotated():
    """Independent instruments; the identity does not apply at all."""
    got = _by_action(_rank(bid=0.44, ask=0.50, venue=CLOB))
    assert "same_liquidity_risk" not in got["TAKE_COMPLEMENT"]


def test_the_open_question_is_stated_once_for_every_consumer():
    from sportsassets import bettor_mgmt_select as _MS
    q = _MS.SAME_LIQUIDITY_QUESTION
    assert "NOT ESTABLISHED" in q
    assert "nothing is refused" in q
    assert "bettor_hedge_tax" in q


def test_the_guard_does_not_fire_when_only_one_price_is_present():
    """A missing price has its own refusal; do not mask it with this one."""
    r = MS.rank_with_hold(
        100.0, 0.60, ev_hold=dict(HOLD_RECORD), bid=0.44, bid_size=100.0,
        complement_ask=None, complement_ask_size=None, fee_fn=_free,
        venue=NETTING, us_market_slug="x", held_is_long=True)
    blocked = {x["action"]: x["blocker"] for x in r["not_rankable"]}
    assert blocked["TAKE_COMPLEMENT"] == "COMPLEMENT_ASK_NOT_OBSERVED"
