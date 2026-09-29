"""EVERY LEG FACT IS READ OR REFUSED -- and the counterexamples are the rows
the live catalogue actually returned.

Codex asked for `held_leg` and `candidate_legs` through production suppliers.
The suppliers' hard part is not plumbing: it is that three `us_premap` columns
each LOOK like they name the market kind and none of them does. Two successive
versions of `derive_kind` read one of them and were refuted by the live
catalogue, and a third defect -- a sign inversion on every B-side spread --
survived inspection and appeared only when the real row went through.

So the cases below are not invented shapes. Every row in this file is copied
from the output of an authorized read-only run against production, and the
run number is named on each one, because a fixture is not the venue and a
claim about what the venue carries cannot be made from a shape I made up.
"""

import pytest

from sportsassets import bettor_funded_hedge_supply as HS
from sportsassets import bettor_indirect_structures as IS

# ── the real rows, with their provenance ──────────────────────────────

#: RUN 264, statement 5. A baseball moneyline, which is the only market this
#: lane has ever held. NOTE `line` = '00': that is the GAME START MINUTE (the
#: fixture starts 9:00 AM UTC), not a handicap.
BASEBALL_ML_A = {
    "sports_type": "baseball_team_full_game_winner",
    "market_slug": "aec-npb-clm-nhf-2026-10-01",
    "event_slug": "npb-clm-nhf-2026-10-01",
    "side_norm": "chiba lotte marines", "team_abbr": "clm",
    "team_name": "chiba lotte marines", "line": "00", "signed": None,
    "intent": "ORDER_INTENT_BUY_LONG", "kind": "side"}

#: The OTHER side of the SAME market. Run 264 measured 2.00 rows per
#: market_slug on every one of the venue's seventeen slug prefixes, so this
#: shares `market_slug` with the row above -- one instrument, two sides.
BASEBALL_ML_B = dict(BASEBALL_ML_A, side_norm="nippon ham fighters",
                     team_abbr="nhf", team_name="nippon ham fighters",
                     intent="ORDER_INTENT_BUY_SHORT")

#: RUN 263, statement 3. Both rows of ONE first-half spread. They are mirror
#: images: lar states -10.5 and den states +10.5.
SPREAD_A = {
    "sports_type": "football_team_first_half_spread",
    "market_slug": "asc-nfl-lar-den-2026-09-27-1h-neg-10pt5",
    "event_slug": "nfl-lar-den-2026-09-27",
    "side_norm": "yes", "team_abbr": "lar", "line": "10.5",
    "signed": "-10.5", "intent": "ORDER_INTENT_BUY_LONG", "kind": "side"}
SPREAD_B = dict(SPREAD_A, side_norm="no", team_abbr="den", signed="+10.5",
                intent="ORDER_INTENT_BUY_SHORT")


# ═════════════════════════════════════════════════════════════════════
# 1 · THE THREE COLUMNS THAT LOOK LIKE THE KIND AND ARE NOT
# ═════════════════════════════════════════════════════════════════════

def test_kind_is_not_read_from_the_kind_column():
    """RUN 262: `kind` is the literal 'side' on all 60,540 rows.

    Both real rows carry kind='side' and are DIFFERENT market kinds. A
    supplier reading that column would have built every leg identically.
    """
    assert BASEBALL_ML_A["kind"] == SPREAD_A["kind"] == "side"
    assert HS.derive_kind(BASEBALL_ML_A)["kind"] == IS.KIND_MONEYLINE
    assert HS.derive_kind(SPREAD_A)["kind"] == IS.KIND_SPREAD


def test_a_moneylines_line_column_is_the_start_minute_and_is_not_read():
    """RUN 264: every aec- `line` is two digits in 00..59 -- 00, 30, 45, 55.

    The moneyline row carries line='00'. Read as a handicap it would make
    every moneyline a spread at line 0, where "A wins by more than 0" happens
    to coincide -- and at line '30' it would make one at line 30. So the
    column is not read on a moneyline at all, and the leg carries no line.
    """
    got = HS.derive_kind(BASEBALL_ML_A)
    assert got["kind"] == IS.KIND_MONEYLINE
    assert got["line"] is None and got["signed_line"] is None
    assert "START MINUTE" in HS.LINE_IS_NOT_A_HANDICAP_ON_A_MONEYLINE


def test_side_norm_on_a_real_moneyline_is_the_team_name_not_yes_no():
    """RUN 263: this is what refuted the second version.

    'a yes/no binary with no handicap is a moneyline' refuses every real
    moneyline, because a real moneyline's side_norm is the team's own name.
    """
    assert BASEBALL_ML_A["side_norm"] == "chiba lotte marines"
    assert HS.derive_kind(BASEBALL_ML_A)["refusal"] is None


# ═════════════════════════════════════════════════════════════════════
# 2 · THE SIGN INVERSION, WHICH IS THE EXPENSIVE ONE
# ═════════════════════════════════════════════════════════════════════

def test_both_rows_of_one_spread_yield_the_same_line_against_a():
    """THE DEFECT THIS PINS. `derive_kind` returned the row's OWN signed
    handicap as `Leg.line`, and `Leg.line` is defined against TEAM A's margin
    whichever side the leg backs -- its own comment says so: "Panthers +4.5
    backing Panthers is the SAME contract from B's side, so line=-4.5
    backs='B'".

    Left unfixed the den leg carries +10.5, and `_leg_payout_cents` grades it
    as "A wins by more than -10.5" -- so A LOSING by up to ten still pays.
    That inverts the payout function of every B-side spread, and a pair built
    from one reads as a middle that cannot lose while being a doubled position
    on one side.
    """
    a, b = HS.derive_kind(SPREAD_A), HS.derive_kind(SPREAD_B)
    oa, ob = HS.orientation_of(SPREAD_A), HS.orientation_of(SPREAD_B)
    assert (oa["backs"], ob["backs"]) == ("A", "B")
    # The rows DISAGREE, as mirror images must.
    assert a["signed_line"] == -b["signed_line"]
    la = HS.line_against_a(signed_line=a["signed_line"], backs=oa["backs"])
    lb = HS.line_against_a(signed_line=b["signed_line"], backs=ob["backs"])
    # And both resolve to A's handicap, which is the one Leg.line means.
    assert la["line"] == lb["line"]
    assert float(la["line"]) == -10.5


def test_an_unoriented_line_refuses_rather_than_picking_a_sign():
    got = HS.line_against_a(signed_line=1, backs=None)
    assert got["refusal"] == HS.R_LINE_NEEDS_ORIENTATION
    assert got["line"] is None


# ═════════════════════════════════════════════════════════════════════
# 3 · ORIENTATION IS AN EQUALITY, NOT A NAME MATCH
# ═════════════════════════════════════════════════════════════════════

def test_the_slug_supplies_the_listed_order_and_team_abbr_the_side():
    """RUN 264 read the slug against the title:
        npb-clm-nhf-2026-10-01  /  "Chiba Lotte Marines vs. Nippon Ham
        Fighters"
    so clm is first and nhf second, and each row's team_abbr names its own.
    """
    pr = HS.fixture_participants("npb-clm-nhf-2026-10-01")
    assert pr["participants"] == ("clm", "nhf")
    assert pr["league"] == "npb"
    assert HS.orientation_of(BASEBALL_ML_A)["backs"] == "A"
    assert HS.orientation_of(BASEBALL_ML_B)["backs"] == "B"


def test_the_intent_is_not_used_as_the_orientation():
    """BUY_LONG sat on the first-listed team in both fixtures run 264
    returned, and two examples are not a rule. So a row whose intent
    disagrees with its team code is oriented by the CODE, and the answer does
    not move when the intent is changed.
    """
    flipped = dict(BASEBALL_ML_A, intent="ORDER_INTENT_BUY_SHORT")
    assert HS.orientation_of(flipped)["backs"] == "A"


@pytest.mark.parametrize("slug,why", [
    ("", "no event at all"),
    ("mlb-2026-09-29", "one code after the date is dropped"),
    ("soccer-epl-a-b-c-2026-09-29", "four codes: which two are the sides?"),
    ("nfl-lar-lar-2026-09-27", "the same code twice"),
])
def test_a_slug_that_does_not_yield_two_codes_refuses(slug, why):
    """RUN 265 measured the shapes: 2,767 events yield three tokens after the
    date (league + two codes), 516 yield FOUR and 109 yield two. The 516 are
    refused rather than trimmed until two remain -- trimming would invent the
    order, and an inverted order inverts every margin.
    """
    got = HS.fixture_participants(slug)
    assert got["participants"] is None, why
    assert got["refusal"] in (HS.R_NO_EVENT, HS.R_FIXTURE_SIDES_NOT_TWO)


def test_a_team_code_matching_neither_participant_refuses():
    got = HS.orientation_of(dict(BASEBALL_ML_A, team_abbr="sea"))
    assert got["backs"] is None
    assert got["refusal"] == HS.R_ORIENTATION_NOT_ESTABLISHED


def test_no_team_code_at_all_refuses():
    got = HS.orientation_of(dict(SPREAD_A, team_abbr=None))
    assert got["refusal"] == HS.R_ORIENTATION_NOT_ESTABLISHED


# ═════════════════════════════════════════════════════════════════════
# 4 · THE NARROW ALLOWLIST, AND WHY NARROW IS CORRECT
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("sports_type,rows_in_production", [
    ("soccer_game_total_corners", 560),
    ("football_player_receiving_yards", 946),
    ("tennis_match_total_games", 1686),
    ("table_tennis_match_total_sets", 1796),
    ("football_team_full_game_total", 540),
    ("esports_map_rounds_handicap_1", None),
    ("baseball_game_extra_innings", None),
    ("futures", 6562),
])
def test_a_market_graded_on_another_variable_is_refused_by_name(
        sports_type, rows_in_production):
    """THE WORST AVAILABLE ERROR IN THIS MODULE, and it is reached by being
    helpful. A corners total and a points total are both "a total", so mapping
    the first onto VAR_TOTAL gives the pair a shared `grading_key()` --
    fixture, period, VAR_TOTAL, overtime -- while one settles on corners and
    the other on points. `discover` would classify two unrelated contracts as
    a middle and report a guaranteed minimum payout that does not exist.

    Every type here is real and run 265 counted its rows. `futures` is the
    largest excluded type at 6,562 rows and is not a fixture at all.
    """
    got = HS.derive_kind({"sports_type": sports_type, "side_norm": "over",
                          "line": "9.5"})
    assert got["kind"] is None
    assert got["refusal"] == HS.R_TYPE_NOT_A_GRADED_VARIABLE


def test_the_soccer_winner_is_admitted_because_it_was_measured_not_recalled():
    """Run 265 showed `soccer_team_full_time_winner` -- 1,338 rows over 223
    events -- excluded by the first list, for no better reason than the venue
    spelling it `full_time` where the American sports spell it `full_game`.
    Added from the measurement.
    """
    got = HS.derive_kind({"sports_type": "soccer_team_full_time_winner",
                          "side_norm": "arsenal", "team_abbr": "ars"})
    assert got["kind"] == IS.KIND_MONEYLINE
    assert got["period"] == IS.PERIOD_FULL


def test_the_simulated_twin_is_left_to_the_realism_classifier():
    """`efootball_team_full_time_winner` (2,934 rows, 489 events) matches the
    same suffix. It is refused by `bettor_venue_realism`, upstream, and that
    refusal is NOT duplicated here: two divergent lists of what is real is how
    one of them ends up missing a family.
    """
    got = HS.derive_kind({"sports_type": "efootball_team_full_time_winner",
                          "side_norm": "yes", "team_abbr": "x"})
    assert got["kind"] == IS.KIND_MONEYLINE       # the SHAPE is gradeable
    from sportsassets import bettor_venue_realism as VR
    verdict = VR.classify({"sports_type": "efootball_team_full_time_winner",
                           "market_slug": "aec-x", "event_slug": "x",
                           "event_title": "A vs B", "question": "who wins?"})
    assert verdict["verdict"] != VR.REAL           # and it is still refused


def test_a_row_with_no_sports_type_refuses_rather_than_defaulting():
    got = HS.derive_kind({"side_norm": "yes", "team_abbr": "x",
                          "event_slug": "nfl-a-b-2026-09-27"})
    assert got["refusal"] == HS.R_NO_SPORTS_TYPE


def test_the_longest_suffix_wins_so_a_half_is_never_read_as_a_full_game():
    got = HS.derive_kind({"sports_type": "football_game_first_half_total_"
                                         "points", "side_norm": "over",
                          "line": "24.5"})
    assert got["period"] == IS.PERIOD_H1
    assert got["matched_suffix"] == "_game_first_half_total_points"


def test_a_total_with_no_direction_refuses():
    got = HS.derive_kind({"sports_type": "football_game_total_points",
                          "side_norm": "yes", "line": "44.5"})
    assert got["kind"] is None
    assert got["refusal"] == HS.R_KIND_NOT_DERIVABLE


def test_a_spread_with_no_signed_handicap_refuses():
    """The unsigned `line` column carries the magnitude, and a handicap
    without its sign is the opposite bet half the time -- premap's leak hunt
    records a whale taking +3.5 matched to the venue's -3.5 side on every
    spread for exactly that reason."""
    got = HS.derive_kind(dict(SPREAD_A, signed=None))
    assert got["kind"] is None
    assert got["refusal"] == HS.R_LINE_REQUIRED
    assert SPREAD_A["line"] == "10.5"        # present, and not enough


def test_a_malformed_line_refuses_instead_of_raising_on_the_decision_path():
    got = HS.parse_line("10pt5")
    assert got["line"] is None
    assert got["refusal"] == HS.R_LINE_NOT_A_NUMBER


# ═════════════════════════════════════════════════════════════════════
# 5 · THE OVERTIME LABEL TRAP
# ═════════════════════════════════════════════════════════════════════

def test_baseball_prose_including_extra_innings_reads_as_ot_included():
    got = HS.overtime_from_venue_prose(
        sport_family="baseball",
        prose="This market includes any extra innings played.")
    assert got["overtime"] == IS.OT_INCLUDED
    assert got["established"] is True


def test_soccer_prose_saying_ninety_minutes_reads_as_ot_EXCLUDED():
    """THE TRAP, AND IT IS PINNED HERE. `OVERTIME_PROSE['soccer']['includes']`
    contains "90 minutes" and "regulation time only". Those labels mean AGREES
    WITH THE BOOK RULE, and soccer's book rule EXCLUDES extra time, so a match
    in the `includes` list means the venue excludes it.

    Read literally, every soccer leg would carry OT_INCLUDED. Two such legs
    would share a `grading_key()` they do not share, and the pair would be
    classified against a variable neither leg grades against.
    """
    got = HS.overtime_from_venue_prose(
        sport_family="soccer",
        prose="Settled on the result after 90 minutes.")
    assert got["overtime"] == IS.OT_EXCLUDED, got
    assert got["agrees_with_book_rule"] is True
    assert got["book_rule"] == "REGULATION_90_PLUS_STOPPAGE_NO_EXTRA_TIME"


def test_soccer_prose_naming_extra_time_reads_as_ot_INCLUDED():
    got = HS.overtime_from_venue_prose(
        sport_family="soccer",
        prose="The winner including any extra time and penalty shootout.")
    assert got["overtime"] == IS.OT_INCLUDED
    assert got["agrees_with_book_rule"] is False


@pytest.mark.parametrize("fam,prose,refusal", [
    ("baseball", "", HS.R_OT_NO_PROSE),
    ("baseball", "Who will win this game?", HS.R_OT_PROSE_SILENT),
    ("baseball", "Includes any extra innings. Regulation nine innings only.",
     HS.R_OT_PROSE_STATES_BOTH),
    ("tennis", "Settled after 90 minutes.", HS.R_OT_NO_PATTERNS),
])
def test_an_unread_or_contradictory_rule_is_ot_unknown(fam, prose, refusal):
    got = HS.overtime_from_venue_prose(sport_family=fam, prose=prose)
    assert got["overtime"] == IS.OT_UNKNOWN
    assert got["established"] is False
    assert got["refusal"] == refusal


def test_ot_unknown_makes_the_leg_itself_refuse():
    """The point of returning OT_UNKNOWN rather than a default: `Leg` turns it
    into a missing fact, so an unread rule cannot become a graded leg."""
    leg = IS.Leg(condition_id="aec-x", fixture_id="npb-clm-nhf-2026-10-01",
                 kind=IS.KIND_MONEYLINE, period=IS.PERIOD_FULL,
                 overtime=IS.OT_UNKNOWN, backs="A", quantity=10,
                 cost_cents_per_unit=47)
    gaps = leg.missing_facts()
    assert any("overtime" in g for g in gaps), gaps
    # AND the same leg with the rule stated is complete.
    ok = IS.Leg(condition_id="aec-x", fixture_id="npb-clm-nhf-2026-10-01",
                kind=IS.KIND_MONEYLINE, period=IS.PERIOD_FULL,
                overtime=IS.OT_INCLUDED, backs="A", quantity=10,
                cost_cents_per_unit=47)
    assert ok.missing_facts() == []


def test_a_type_that_states_its_own_overtime_rule_is_read_from_the_type():
    """`hockey_team_regulation_winner` says regulation in its own name, which
    is stronger than any prose read. It is still refused as a graded kind
    because `_team_regulation_winner` is not in GRADED_SUFFIXES -- the
    overtime fact and the kind are separate reads and neither implies the
    other."""
    got = HS.derive_kind({"sports_type": "hockey_team_regulation_winner",
                          "side_norm": "yes", "team_abbr": "x"})
    assert got["overtime_from_type"] == IS.OT_EXCLUDED
    assert got["refusal"] == HS.R_TYPE_NOT_A_GRADED_VARIABLE


# ═════════════════════════════════════════════════════════════════════
# 6 · ONE INSTRUMENT PER MARKET: THE OPPOSITE SIDE IS NETTING
# ═════════════════════════════════════════════════════════════════════

def test_both_sides_of_a_real_market_share_one_slug():
    """RUN 264 measured exactly 2.00 rows per market_slug and exactly 2
    distinct intents on EVERY ONE of the venue's seventeen slug prefixes --
    asta, tsc-, asc-, atc-, tec-, aec- and eleven more, without exception
    across 60,540 rows.

    So the opposite side of a held contract is netting on ONE instrument, not
    a second settling holding, and this is now a measurement rather than an
    assertion about how the venue probably works.
    """
    assert BASEBALL_ML_A["market_slug"] == BASEBALL_ML_B["market_slug"]
    assert SPREAD_A["market_slug"] == SPREAD_B["market_slug"]
    # Different sides, same instrument.
    assert HS.orientation_of(BASEBALL_ML_A)["backs"] != \
        HS.orientation_of(BASEBALL_ML_B)["backs"]


def test_the_provenance_table_names_a_source_or_a_refusal_for_every_field():
    """A reviewer should be able to ask where a field came from without
    reading the code, and a field with no source must be a refusal."""
    import dataclasses
    fields = {f.name for f in dataclasses.fields(IS.Leg)}
    documented = set(HS.LEG_FIELD_SOURCES)
    assert fields - documented <= {"settlement_text_captured"}, (
        fields - documented)
    for name, (source, refusal) in HS.LEG_FIELD_SOURCES.items():
        assert source and isinstance(source, str), name
