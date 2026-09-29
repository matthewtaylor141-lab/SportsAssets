"""ONE VENUE INSTRUMENT, TWO SIDES, TWO IDENTITIES -- AND ONE NETTING QUESTION.

── WHAT THE PRODUCTION CATALOGUE ACTUALLY LOOKS LIKE ─────────────────

Run 268, over all 52,950 `us_premap` rows:

    rows per market_slug ............................. exactly 2.000
    distinct `identifier` per slug ................... 1   (identifier IS the
                                                        slug, so NOT the key)
    slugs where `side_norm` differs .................. 26,475 of 26,475
    slugs where `intent` differs ..................... 26,475 of 26,475
    slugs where `line` or `sports_type` differ ........ 0
    the whole `intent` vocabulary .................... ORDER_INTENT_BUY_LONG
                                                       ORDER_INTENT_BUY_SHORT

Run 269:

    (market_slug, intent) distinct pairs ............. 52,950 of 52,950 rows,
                                                       zero duplicates
    future slugs ..................................... 16,540
    future slugs the OLD sibling key collapsed ........ 5,930  (35.9%)

── THE TWO DEFECTS, WHICH ARE OPPOSITE ERRORS ────────────────────────

1. `ROW_SQL` was `WHERE market_slug = $1 LIMIT 1`, no ORDER BY. The held leg's
   orientation -- and so its whole payout function -- came from whichever of the
   two rows Postgres returned. On a spread the pair carries `signed` -1.5 and
   +1.5; binding to the wrong one inverts the payout with no error anywhere.

2. Candidate identity was the slug, so the two sides collapsed onto one id.
   Measured: 16,545 candidates across 1,558 future fixtures -- every future
   fixture, exactly half the set -- and two ranked rows came out sharing one
   `condition_id` with different scores.

── AND THE THING THE REPAIR MUST NOT DO ──────────────────────────────

The owner's words: "Do not pretend opposite sides are separate venue
instruments to avoid fixing the identity model", and "Direct complements on the
same netted PMUS instrument must not be presented as an independent liquidity
opportunity."

So there are TWO identities and they answer different questions. The CANDIDATE
identity is side-aware, because two sides are two contracts to quote, value and
rank. The NETTING identity is the venue slug, because two sides are one book:
buying the other side of what you hold nets the position. A single key bent to
answer both questions gets one of them wrong, and the last section of this file
is entirely about the second one.
"""

import pytest

from sportsassets import bettor_funded_execution as FX
from sportsassets import bettor_funded_hedge_supply as HS
from sportsassets import bettor_funded_pair_cycle as PC
from sportsassets import bettor_indirect_structures as IS
from sportsassets import bettor_settlement_clauses as SC

SLUG = "asc-mlb-bos-nyy-2026-10-05-neg-1pt5"
OTHER = "aec-mlb-bos-nyy-2026-10-05"
EVENT = "mlb-bos-nyy-2026-10-05"

PROSE = ("Resolves on the final score and includes any extra innings played. "
         "A tie resolves 50-50. If the game is cancelled all stakes are "
         "refunded.")


# ═════════════════════════════════════════════════════════════════════
# THE VOCABULARY IS THE CATALOGUE'S, NOT AN INVENTION
# ═════════════════════════════════════════════════════════════════════

def test_the_two_sides_are_the_catalogues_own_two_intent_values():
    assert HS.SIDES == ("ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT")
    assert len(set(HS.SIDES)) == 2
    # And they are the SAME strings the execution module already uses for an
    # order intent, which is why the join to a funded position is direct rather
    # than a mapping somebody has to maintain.
    assert FX.LONG in HS.SIDES


def test_an_identity_round_trips_and_a_bare_slug_reads_as_side_unknown():
    for side in HS.SIDES:
        ident = HS.candidate_identity(SLUG, side)
        assert ident == "%s#%s" % (SLUG, side)
        assert HS.split_identity(ident) == (SLUG, side)
        assert HS.venue_slug_of(ident) == SLUG
    # A record written before the identity model carries a bare slug. Reading it
    # back as "side unknown" is the truth about the record.
    assert HS.split_identity(SLUG) == (SLUG, None)
    assert HS.venue_slug_of(SLUG) == SLUG


def test_the_two_halves_of_an_identity_normalise_differently():
    """Slugs are lowercase and side tokens are uppercase.

    MY OWN BUG, CAUGHT BY THE INTEGRATION SUITE ON THE FIRST RUN: `_clean`
    lowercases, and I used it for the side too, so every catalogue value became
    something not in SIDES and every held leg refused.
    """
    ident = HS.candidate_identity("ASC-MLB-BOS-NYY-2026-10-05-NEG-1PT5",
                                  "order_intent_buy_long")
    assert ident == "%s#%s" % (SLUG, FX.LONG)
    assert HS.split_identity(ident) == (SLUG, FX.LONG)
    # A side that is not one of the two is not silently accepted.
    assert HS.split_identity(SLUG + "#ORDER_INTENT_SELL") == (SLUG, None)


def test_a_separator_that_cannot_occur_in_a_venue_slug():
    assert HS.IDENTITY_SEP == "#"
    for slug in (SLUG, OTHER, EVENT):
        assert HS.IDENTITY_SEP not in slug


@pytest.mark.parametrize("intent,expected", [
    ("ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_LONG"),
    ("ORDER_INTENT_BUY_SHORT", "ORDER_INTENT_BUY_SHORT"),
    ("order_intent_buy_long", "ORDER_INTENT_BUY_LONG"),
    ("  ORDER_INTENT_BUY_SHORT  ", "ORDER_INTENT_BUY_SHORT"),
    ("ORDER_INTENT_SELL_SHORT", None),
    ("side", None), ("", None), (None, None),
])
def test_the_side_is_read_from_the_rows_intent_or_refused(intent, expected):
    assert HS.side_of({"intent": intent}) == expected


# ═════════════════════════════════════════════════════════════════════
# THE HELD LEG IS BOUND TO THE SIDE ACTUALLY HELD
# ═════════════════════════════════════════════════════════════════════

def test_a_position_that_does_not_state_its_side_is_refused_not_guessed():
    got = HS.held_side_of({"us_market_slug": SLUG, "residual_qty": 10})
    assert got["side"] is None
    assert got["refusal"] == HS.R_HELD_SIDE_NOT_STATED
    assert "LIMIT 1" in got["why"], (
        "the refusal should name the thing it replaces")
    assert got["source"] == "bettor_funded_intents.order_intent"


@pytest.mark.parametrize("side", list(HS.SIDES))
def test_a_position_that_states_a_side_is_bound_to_it(side):
    got = HS.held_side_of({"us_market_slug": SLUG, "order_intent": side})
    assert got["refusal"] is None
    assert got["side"] == side


def test_a_position_stating_a_side_the_vocabulary_lacks_is_refused():
    got = HS.held_side_of({"order_intent": "ORDER_INTENT_SELL_LONG"})
    assert got["refusal"] == HS.R_HELD_SIDE_NOT_STATED
    assert got["stated"] == "ORDER_INTENT_SELL_LONG"


def test_the_row_query_requires_a_side_and_names_it_in_the_sql():
    assert "AND intent = $2" in HS.ROW_SQL
    assert HS.ROW_SQL.count("$") == 2


async def test_read_row_refuses_a_side_outside_the_vocabulary():
    """The read itself refuses rather than falling back to either row."""
    got = await HS.read_row(None, SLUG, "ORDER_INTENT_SELL")
    assert isinstance(got, dict) and got.get("error", "").startswith("SIDE_NOT")
    got = await HS.read_row(None, SLUG, None)
    assert isinstance(got, dict) and got.get("error", "").startswith("SIDE_NOT")


# ═════════════════════════════════════════════════════════════════════
# THE SIBLING READ KEEPS BOTH SIDES
# ═════════════════════════════════════════════════════════════════════

def test_the_sibling_key_is_the_intent_not_the_team_abbreviation():
    """The measured repair.

    Run 269: on 5,930 of 16,540 future slugs (35.9%) `team_abbr` is present and
    EQUAL on both rows -- e.g. `aachc-nhl-fewestpts-2027-04-10-ana`, whose yes
    row and no row are both `ana`. `DISTINCT ON (market_slug,
    coalesce(team_abbr, side_norm))` saw one value and returned ONE row, so on
    more than a third of tradable instruments the second side was never offered.
    """
    assert "DISTINCT ON (market_slug, intent)" in HS.SIBLINGS_SQL
    assert "coalesce(team_abbr" not in HS.SIBLINGS_SQL
    # And the ORDER BY has to lead with the DISTINCT ON key or Postgres refuses.
    assert "ORDER BY market_slug, intent, updated_at DESC" in HS.SIBLINGS_SQL


def test_the_netting_exclusion_is_still_on_the_slug_not_the_identity():
    """Both sides of the held instrument are excluded, by the slug.

    This is the half that must NOT become side-aware.
    """
    assert "market_slug <> $2" in HS.SIBLINGS_SQL
    assert "#" not in HS.SIBLINGS_SQL


# ═════════════════════════════════════════════════════════════════════
# THE BUILT LEG CARRIES THE IDENTITY, THE SLUG AND THE SIDE, APART
# ═════════════════════════════════════════════════════════════════════

def _row(side, **kw):
    """One row of the spread, in the shape run 270 measured for a real spread.

    `team_abbr` DIFFERS between the two sides -- measured on 3,347 of 5,974
    graded-prefix future slugs, e.g. the tennis pair
    `asc-atp-abeshe-litu-2026-09-29-gs-neg-1pt5` carrying `yes | abeshe | -1.5`
    and `no | litu | +1.5`. The catalogue has already resolved the flip into
    team_abbr and signed, so orientation reads it directly.

    MY FIRST DRAFT PUT `bos` ON BOTH ROWS and both legs came back backs='A'.
    That is the collapsing shape, and run 270 established it reaches graded
    prefixes on 468 slugs -- every one of them a TEAM TOTAL, which has no
    orientation at all. `test_a_team_total_...` below covers that case on its
    own terms, and `test_two_sides_claiming_one_orientation_are_refused` covers
    the shape the catalogue does not currently produce.
    """
    base = {"market_slug": SLUG, "event_slug": EVENT,
            "sports_type": "baseball_team_full_game_spread",
            "side_norm": "yes" if side == FX.LONG else "no",
            "team_abbr": "bos" if side == FX.LONG else "nyy",
            "team_name": "bos" if side == FX.LONG else "nyy",
            "line": "1.5", "signed": "-1.5" if side == FX.LONG else "+1.5",
            "intent": side, "kind": "side"}
    base.update(kw)
    return base


def _build(side, **kw):
    return HS.build_leg(row=_row(side, **kw), quantity=10, cost_per_unit=0.30,
                        prose=PROSE, prose_source="test", sport_family="baseball")


@pytest.mark.parametrize("side", list(HS.SIDES))
def test_a_built_leg_names_all_three_identifiers(side):
    got = _build(side)
    assert got["ok"] is True, got
    assert got["candidate_id"] == HS.candidate_identity(SLUG, side)
    assert got["venue_slug"] == SLUG
    assert got["side"] == side
    assert got["leg"].condition_id == got["candidate_id"]
    # The provenance says WHY the identity is not the slug.
    assert "netting identity" in got["built_from"]["condition_id"]


def test_the_two_sides_of_one_spread_are_two_distinct_candidates():
    a, b = _build(FX.LONG), _build(HS.SIDE_SHORT)
    assert a["ok"] and b["ok"]
    assert a["candidate_id"] != b["candidate_id"]
    # SAME instrument, SAME grading variable -- and opposite payout functions.
    assert a["venue_slug"] == b["venue_slug"]
    assert a["leg"].grading_key() == b["leg"].grading_key()
    assert a["leg"].backs != b["leg"].backs, (
        "the two rows carry signed -1.5 and +1.5: they back opposite teams")
    # And that shows up as opposite payouts in the same outcome region.
    win_a = IS.Region("margin = 5", lo=5, hi=5)
    pa = IS._leg_payout_cents(a["leg"], win_a)
    pb = IS._leg_payout_cents(b["leg"], win_a)
    assert {pa, pb} == {0, 100}, (pa, pb)


def test_a_row_stating_no_side_refuses_rather_than_building_half_a_leg():
    got = HS.build_leg(row=_row(FX.LONG, intent="side"), quantity=10,
                       cost_per_unit=0.30, prose=PROSE, sport_family="baseball")
    assert got["ok"] is False
    assert got["refusal"] == HS.R_SIDE_NOT_STATED_ON_ROW
    assert got["field"] == "condition_id"


def test_the_leg_that_backs_a_is_not_decided_by_which_row_came_back_first():
    """The defect, stated as the thing that can no longer happen.

    Two rows of ONE spread. Built from either, the leg knows which side it is,
    so `backs` is a function of the ROW rather than of Postgres' row order.
    """
    seen = {}
    for side in HS.SIDES:
        got = _build(side)
        seen[side] = (got["leg"].backs, got["leg"].line)
    assert seen[FX.LONG][0] != seen[HS.SIDE_SHORT][0]
    # `line` is always expressed against team A's margin, so BOTH legs of one
    # spread agree on it -- that is the other half of the same repair and it
    # must not be undone by the side work.
    assert seen[FX.LONG][1] == seen[HS.SIDE_SHORT][1], seen


# ═════════════════════════════════════════════════════════════════════
# THE IDENTITY SURVIVES QUOTING, VALUATION, RANKING AND DISPATCH
# ═════════════════════════════════════════════════════════════════════

def _admitted(side, taxonomy="MIDDLE", units=10):
    leg = _build(side)["leg"]
    return {"condition_id": leg.condition_id, "taxonomy": taxonomy,
            "units": units, "leg": leg,
            "structure": {"taxonomy": taxonomy, "units": units,
                          "min_payout_cents": 100, "max_payout_cents": 200,
                          "cost_cents": 85, "table": []}}


def test_two_sides_of_one_instrument_get_their_own_price_and_depth():
    """THE COLLAPSE, PINNED. Detail rows keyed by the side-aware identity.

    Keyed by slug, both sides read the same price and the same depth -- so the
    cheap side and the dear side of one book were scored on one number.
    """
    long_id = HS.candidate_identity(SLUG, FX.LONG)
    short_id = HS.candidate_identity(SLUG, HS.SIDE_SHORT)
    details = [
        {"candidate_id": long_id, "market_slug": SLUG, "side": FX.LONG,
         "price": 0.30, "depth_qty": 500},
        {"candidate_id": short_id, "market_slug": SLUG, "side": HS.SIDE_SHORT,
         "price": 0.72, "depth_qty": 4},
    ]
    got = PC.rank_admitted([_admitted(FX.LONG), _admitted(HS.SIDE_SHORT)],
                           details=details, wanted_qty=10, fee_usd=0.10,
                           fee_basis="test")
    rows = {r["condition_id"]: r for r in
            (got["ranked"] + got["not_rankable"])}
    assert set(rows) == {long_id, short_id}, rows
    assert rows[long_id]["price"] == 0.30
    assert rows[short_id]["price"] == 0.72, (
        "keyed by slug these were the same number")
    assert rows[long_id]["depth_qty"] == 500
    assert rows[short_id]["depth_qty"] == 4
    for r in rows.values():
        assert r["venue_slug"] == SLUG
        assert r["side"] in HS.SIDES
        assert r["price_is_this_sides_own"] is True


def test_a_slug_only_detail_row_is_used_and_recorded_as_shared():
    """A supplier that predates the identity model still works, and says so.

    The same treatment `rank_admitted` already gives a shared depth reading: the
    limitation bounds what the number means instead of vanishing into it.
    """
    got = PC.rank_admitted(
        [_admitted(FX.LONG)],
        details=[{"market_slug": SLUG, "price": 0.30, "depth_qty": 500}],
        wanted_qty=10, fee_usd=0.10, fee_basis="test")
    rows = got["ranked"] + got["not_rankable"]
    assert len(rows) == 1
    assert rows[0]["price"] == 0.30
    assert rows[0]["price_is_this_sides_own"] is False, (
        "a price read for the instrument cannot be this SIDE's own price")


def test_the_winner_resolves_to_the_candidate_that_actually_won():
    """`best_admitted` used to look the winner up by a shared slug.

    With two entries under one key the lookup returned whichever was last in
    the dict -- so the ACQUIRED contract could be the loser of the comparison.
    """
    long_id = HS.candidate_identity(SLUG, FX.LONG)
    short_id = HS.candidate_identity(SLUG, HS.SIDE_SHORT)
    admitted = [_admitted(FX.LONG), _admitted(HS.SIDE_SHORT, units=10)]
    got = PC.rank_admitted(
        admitted,
        details=[{"candidate_id": long_id, "price": 0.30, "depth_qty": 500},
                 {"candidate_id": short_id, "price": 0.90, "depth_qty": 500}],
        wanted_qty=10, fee_usd=0.10, fee_basis="test")
    assert got["ranked"], got
    winner = got["ranked"][0]
    best = got.get("best_admitted")
    assert best is not None
    assert best["condition_id"] == winner["condition_id"]
    # The cheap side wins on the numbers, and the object that comes back is the
    # cheap side's own -- not the other entry under the same slug.
    assert winner["condition_id"] == long_id, got["ranked"]


def test_the_order_addresses_the_venue_by_slug_never_by_the_identity():
    """A venue does not know about `#ORDER_INTENT_BUY_LONG`."""
    ident = HS.candidate_identity(SLUG, FX.LONG)
    assert HS.venue_slug_of(ident) == SLUG
    assert "#" not in HS.venue_slug_of(ident)


# ═════════════════════════════════════════════════════════════════════
# AND THE OPPOSING SIDE OF THE HELD INSTRUMENT IS STILL NETTING
# ═════════════════════════════════════════════════════════════════════
#
# This is the section that stops the identity repair from becoming a way to
# admit a netting trade. `discover` compares VENUE SLUGS, deliberately.

def _held_leg(side=FX.LONG):
    row = {"market_slug": OTHER, "event_slug": EVENT,
           "sports_type": "baseball_team_full_game_winner",
           "side_norm": "boston red sox" if side == FX.LONG else "new york",
           "team_abbr": "bos" if side == FX.LONG else "nyy",
           "team_name": "bos", "line": "00", "signed": None,
           "intent": side, "kind": "side"}
    got = HS.build_leg(row=row, quantity=10, cost_per_unit=0.55, prose=PROSE,
                       prose_source="test", sport_family="baseball")
    assert got["ok"], got
    return got["leg"]


def test_the_opposing_side_of_the_held_instrument_is_rejected_as_netting():
    held = _held_leg(FX.LONG)
    other_side = _held_leg(HS.SIDE_SHORT)
    # Two DIFFERENT identities on ONE instrument.
    assert held.condition_id != other_side.condition_id
    assert HS.venue_slug_of(held.condition_id) == \
        HS.venue_slug_of(other_side.condition_id)

    got = PC.discover(held_leg=held, candidate_legs=[other_side],
                      sport_permits_tie=True, fixture_can_void=True,
                      fixture_can_postpone=True)
    assert got["admitted"] == [], got
    assert len(got["rejected"]) == 1
    rej = got["rejected"][0]
    assert rej["refusal"] == PC.R_NOT_DISTINCT
    assert rej["is_the_same_side"] is False
    assert rej["venue_instrument"] == OTHER
    assert "nets the position" in rej["why"]
    assert "independent liquidity opportunity" in rej["why"]


def test_the_held_contract_itself_is_still_rejected_and_named_as_such():
    held = _held_leg(FX.LONG)
    got = PC.discover(held_leg=held, candidate_legs=[held],
                      sport_permits_tie=True)
    rej = got["rejected"][0]
    assert rej["refusal"] == PC.R_NOT_DISTINCT
    assert rej["is_the_same_side"] is True
    assert rej["why"] == "this is the contract already held"


def test_a_different_instrument_on_the_same_fixture_is_not_netting():
    """The control. Without it, the two tests above would pass on a function
    that rejected everything."""
    held = _held_leg(FX.LONG)
    spread = _build(FX.LONG)["leg"]
    assert HS.venue_slug_of(spread.condition_id) != OTHER
    got = PC.discover(held_leg=held, candidate_legs=[spread],
                      sport_permits_tie=True, fixture_can_void=True,
                      fixture_can_postpone=True)
    nets = [r for r in got["rejected"]
            if r["refusal"] == PC.R_NOT_DISTINCT]
    assert nets == [], nets
    assert got["examined"] == 1


# ═════════════════════════════════════════════════════════════════════
# THE SETTLEMENT READING IS PER SIDE TOO
# ═════════════════════════════════════════════════════════════════════

def test_each_side_carries_its_own_settlement_reading_and_provenance():
    for side in HS.SIDES:
        got = _build(side)
        leg = got["leg"]
        assert leg.settlement_provenance["raw_text"] == PROSE
        assert leg.rule_for(SC.CANCELLED)["established"] is True
        assert leg.rule_for(SC.TIE)["established"] is True
        assert leg.void_rule == "If the game is cancelled all stakes are refunded."


def test_a_refund_on_cancellation_pays_each_side_its_own_basis():
    """The two sides paid different prices, so a refund pays them differently.

    This is where the settlement repair and the identity repair meet: a
    per-contract constant would have been wrong for BOTH sides at once.
    """
    long_leg = HS.build_leg(row=_row(FX.LONG), quantity=10, cost_per_unit=0.30,
                            prose=PROSE, sport_family="baseball")["leg"]
    short_leg = HS.build_leg(row=_row(HS.SIDE_SHORT), quantity=10,
                             cost_per_unit=0.72, prose=PROSE,
                             sport_family="baseball")["leg"]
    void = IS.Region("cancelled", state=IS.STATE_VOID)
    assert IS._leg_payout_cents(long_leg, void) == 30
    assert IS._leg_payout_cents(short_leg, void) == 72


# ═════════════════════════════════════════════════════════════════════
# THE TWO SHAPES RUN 270 MEASURED, EACH ON ITS OWN TERMS
# ═════════════════════════════════════════════════════════════════════
#
# Run 270, over the graded prefixes (aec-/asc-/tsc-), 5,974 future slugs:
#
#     team_abbr absent on both ....  2,159  orientation refuses, correctly
#     team_abbr differs ...........  3,347  orientation established
#     team_abbr EQUAL .............    468  every one a TEAM TOTAL
#     side_norm differs ...........  5,974  all of them
#
# So the shape that would collapse orientation only occurs where orientation is
# never consulted. That is a fact about the catalogue TODAY, which is why the
# guard below exists as well as the measurement.

def _total_row(side, team="del"):
    """A TEAM TOTAL, in the exact shape run 270 printed.

        tsc-cfb-librty-del-2026-10-02-tt1h-del-10pt5
          over  | del | ORDER_INTENT_BUY_LONG  | 10.5 | football_team_first_half_total
          under | del | ORDER_INTENT_BUY_SHORT | 10.5 | football_team_first_half_total

    `team_abbr` is EQUAL on both rows because it names WHOSE total it is, not
    which side is backed. The side is over/under.
    """
    return {"market_slug": "tsc-cfb-librty-del-2026-10-02-tt1h-del-10pt5",
            "event_slug": "cfb-librty-del-2026-10-02",
            "sports_type": "football_team_first_half_total",
            "side_norm": "over" if side == FX.LONG else "under",
            "team_abbr": team, "team_name": team, "line": "10.5",
            "signed": None, "intent": side, "kind": "side"}


def test_a_team_total_is_refused_two_steps_before_orientation_is_consulted():
    """THE 468 COLLAPSING GRADED SLUGS NEVER REACH A PAYOUT FUNCTION.

    Every one of them is a TEAM total -- one team's points, not the combined
    score -- and `derive_kind` already refuses that family for a reason that is
    the same error in a different place: a team total mapped onto VAR_TOTAL
    would share a grading key with a GAME total while counting something else,
    so one random variable would appear to grade two legs that it does not.

    So the chain is: the equal-team_abbr shape occurs only on team totals; team
    totals are refused at `derive_kind`; orientation is never consulted; no
    payout function is inverted. Asserted here rather than reasoned about,
    because the reasoning is three steps long and each step is a measurement.
    """
    for side in HS.SIDES:
        got = HS.build_leg(row=_total_row(side), quantity=10,
                           cost_per_unit=0.45, prose=PROSE,
                           prose_source="test", sport_family="football")
        assert got["ok"] is False, got
        assert got["field"] == "kind"
        assert "shared grading key" in got["why"], got["why"]
        # AND THE REFUSAL COMES BEFORE ORIENTATION. If it did not, the equal
        # team code would have produced two legs backing the same participant.
        assert got["refusal"] != HS.R_ORIENTATION_NOT_ESTABLISHED


def test_a_game_total_is_built_and_its_two_sides_differ_by_over_under():
    """The shape that IS supported, so the test above is not vacuous.

    A total has no orientation at all -- `Leg` carries `over_under` and
    `build_leg` never calls `orientation_of` for KIND_TOTAL -- so an equal team
    code could not collapse anything here either.
    """
    built = {}
    for side in HS.SIDES:
        row = _total_row(side)
        # THE SUFFIX THE PARSER ACTUALLY DECLARES. `GRADED_SUFFIXES` carries
        # `_game_total_points`, not `_full_game_total` -- my first draft used
        # the latter, the build refused, and the control SKIPPED. A skipped
        # control is not a control, which is the whole point of the gate
        # tooling in `tools/gate_verdict.py`, so it is spelled correctly here
        # and asserted rather than skipped.
        # The FAMILY has to match the prose too: PROSE says "extra innings",
        # which is a declared BASEBALL overtime pattern. Asking for a football
        # reading of baseball prose refuses on overtime, correctly.
        row["sports_type"] = "baseball_game_total_points"
        row["side_norm"] = "over" if side == FX.LONG else "under"
        got = HS.build_leg(row=row, quantity=10, cost_per_unit=0.45,
                           prose=PROSE, prose_source="test",
                           sport_family="baseball")
        assert got["ok"] is True, got
        built[side] = got["leg"]
    a, b = built[FX.LONG], built[HS.SIDE_SHORT]
    assert a.condition_id != b.condition_id
    assert HS.venue_slug_of(a.condition_id) == HS.venue_slug_of(b.condition_id)
    assert a.backs is None and b.backs is None
    assert {a.over_under, b.over_under} == {"OVER", "UNDER"}
    assert a.variable == IS.VAR_TOTAL == b.variable
    high = IS.Region("total in [11, 20]", lo=11, hi=20)
    assert {IS._leg_payout_cents(a, high),
            IS._leg_payout_cents(b, high)} == {0, 100}


async def test_two_sides_claiming_one_orientation_are_refused_not_ranked():
    """THE GUARD, on the shape the catalogue does not currently produce.

    A spread whose two rows state the SAME team code. Both legs come back
    backing the same participant while holding opposite outcome tokens, so one
    payout function is inverted and the rows do not say which. `candidate_legs_for`
    is the only place both rows of one instrument are in scope, so it is the only
    place this is visible -- and it refuses BOTH rather than keeping one.
    """
    class _Conn:
        async def fetch(self, sql, *args):
            if "count(DISTINCT" in sql:
                return []
            return [_row(s, team_abbr="bos", team_name="bos")
                    for s in HS.SIDES]

        async def fetchrow(self, sql, *args):
            return {"pairs": 2, "slugs": 1}

    async def quoter(slug, side=None):
        return {"price": 0.30, "depth_qty": 500}

    async def prose(slug):
        return {"ok": True, "rules_text": PROSE, "read_at": 0.0}

    got = await HS.candidate_legs_for(
        _Conn(), held_row={"market_slug": OTHER, "event_slug": EVENT},
        quoter=quoter, prose_reader=prose, now=0.0)
    assert got["legs"] == [], got["legs"]
    assert got.get("orientation_contradictions") == [SLUG]
    assert len(got["refused"]) == 2
    for r in got["refused"]:
        assert r["refusal"] == HS.R_BOTH_SIDES_CLAIM_ONE_ORIENTATION
        assert "one of these payout functions is inverted" in r["why"]
        assert r["side"] in HS.SIDES


async def test_the_real_spread_shape_is_not_refused_by_that_guard():
    """The control. The guard must not fire on the 3,347 normal slugs."""
    class _Conn:
        async def fetch(self, sql, *args):
            return [_row(s) for s in HS.SIDES]

        async def fetchrow(self, sql, *args):
            return {"pairs": 2, "slugs": 1}

    async def quoter(slug, side=None):
        return {"price": 0.30, "depth_qty": 500}

    async def prose(slug):
        return {"ok": True, "rules_text": PROSE, "read_at": 0.0}

    got = await HS.candidate_legs_for(
        _Conn(), held_row={"market_slug": OTHER, "event_slug": EVENT},
        quoter=quoter, prose_reader=prose, now=0.0)
    assert len(got["legs"]) == 2, got["refused"]
    assert "orientation_contradictions" not in got
    assert {e["side"] for e in got["legs"]} == set(HS.SIDES)
    assert {e["leg"].backs for e in got["legs"]} == {"A", "B"}


# ═════════════════════════════════════════════════════════════════════
# QUOTING IS PER SIDE, AND A ONE-ARGUMENT QUOTER SAYS SO
# ═════════════════════════════════════════════════════════════════════

async def test_a_side_aware_quoter_is_called_with_the_side():
    seen = []

    async def quoter(slug, side):
        seen.append((slug, side))
        return {"price": 0.30 if side == FX.LONG else 0.72, "depth_qty": 500}

    got, aware = await HS._quote_side(quoter, SLUG, FX.LONG)
    assert aware is True
    assert seen == [(SLUG, FX.LONG)]
    assert got["price"] == 0.30


async def test_a_slug_only_quoter_still_answers_and_is_recorded_as_shared():
    """A price read for the instrument is not this SIDE's own price.

    The two sides of one book have separate ladders whose prices need not sum
    to a dollar -- that gap is the venue's spread. So a shared reading is used
    and REPORTED, the same treatment a shared depth reading already gets.
    """
    async def quoter(slug):
        return {"price": 0.30, "depth_qty": 500}

    got, aware = await HS._quote_side(quoter, SLUG, FX.LONG)
    assert aware is False
    assert got["price"] == 0.30


async def test_a_quoter_that_throws_is_a_named_error_not_an_exception():
    async def quoter(slug, side):
        raise RuntimeError("the venue transport exploded")

    got, aware = await HS._quote_side(quoter, SLUG, FX.LONG)
    assert got["ok"] is False
    assert got["error"] == "RuntimeError"


# ═════════════════════════════════════════════════════════════════════
# TRUNCATION IS REPORTED, BECAUSE THE LIMIT NOW COVERS HALF AS MANY
# ═════════════════════════════════════════════════════════════════════

async def test_a_truncated_discovery_says_so_rather_than_looking_complete():
    """Both sides are returned now, so the same limit covers half as many
    instruments. A prefix of the candidate set is not the candidate set, and
    nothing in it supports "no better candidate exists"."""
    class _Conn:
        async def fetch(self, sql, *args):
            return [_row(s) for s in HS.SIDES]

        async def fetchrow(self, sql, *args):
            return {"pairs": 1496, "slugs": 748}   # nfl-pit-cle, run 268

    async def quoter(slug, side=None):
        return {"price": 0.30, "depth_qty": 500}

    async def prose(slug):
        return {"ok": True, "rules_text": PROSE, "read_at": 0.0}

    got = await HS.candidate_legs_for(
        _Conn(), held_row={"market_slug": OTHER, "event_slug": EVENT},
        quoter=quoter, prose_reader=prose, limit=2, now=0.0)
    assert got["truncated_at_limit"] is True
    assert got["fixture_candidate_pairs"] == 1496
    assert got["fixture_candidate_slugs"] == 748
    assert got["limit"] == 2
    assert "PREFIX" in got["truncation_note"]
    assert "no better candidate exists" in got["truncation_note"]


async def test_an_untruncated_discovery_does_not_claim_truncation():
    class _Conn:
        async def fetch(self, sql, *args):
            return [_row(s) for s in HS.SIDES]

        async def fetchrow(self, sql, *args):
            return {"pairs": 2, "slugs": 1}

    async def quoter(slug, side=None):
        return {"price": 0.30, "depth_qty": 500}

    async def prose(slug):
        return {"ok": True, "rules_text": PROSE, "read_at": 0.0}

    got = await HS.candidate_legs_for(
        _Conn(), held_row={"market_slug": OTHER, "event_slug": EVENT},
        quoter=quoter, prose_reader=prose, limit=40, now=0.0)
    assert got["truncated_at_limit"] is False
    assert "truncation_note" not in got
