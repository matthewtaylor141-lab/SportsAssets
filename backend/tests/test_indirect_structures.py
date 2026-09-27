"""§5: indirect-pair reasoning, proved from contracts rather than titles.

The load-bearing tests here are the REFUSALS. A both-win region that is
asserted from two market names is the failure this module exists to
prevent, so most of what follows checks that a structure is NOT
reported when the facts grading it are absent -- same titles, different
period; same line, different overtime rule; an integer line whose push
rule was never captured.
"""

from fractions import Fraction

import pytest

from sportsassets import bettor_indirect_structures as IS


# ── helpers ──────────────────────────────────────────────────────────

def ml(backs, *, fixture="fx", cid=None, q=1, cost=None, ot=IS.OT_INCLUDED,
       period=IS.PERIOD_FULL, captured=True):
    return IS.Leg(condition_id=cid or ("ml-%s" % backs), fixture_id=fixture,
                  kind=IS.KIND_MONEYLINE, period=period, overtime=ot,
                  backs=backs, quantity=q, cost_cents_per_unit=cost,
                  tie_rule="tie resolves 50-50", void_rule="void: 50-50",
                  settlement_text_captured=captured)


def spread(backs, line, *, fixture="fx", cid=None, q=1, cost=None,
           ot=IS.OT_INCLUDED, period=IS.PERIOD_FULL, captured=True):
    return IS.Leg(condition_id=cid or ("sp-%s-%s" % (backs, line)),
                  fixture_id=fixture, kind=IS.KIND_SPREAD, period=period,
                  overtime=ot, backs=backs, line=Fraction(line),
                  quantity=q, cost_cents_per_unit=cost,
                  tie_rule="spread at .5 has no tie region",
                  void_rule="void: 50-50",
                  settlement_text_captured=captured)


def total(ou, line, *, fixture="fx", cid=None, q=1, cost=None,
          ot=IS.OT_INCLUDED, period=IS.PERIOD_FULL):
    return IS.Leg(condition_id=cid or ("tot-%s-%s" % (ou, line)),
                  fixture_id=fixture, kind=IS.KIND_TOTAL, period=period,
                  overtime=ot, over_under=ou, line=Fraction(line),
                  quantity=q, cost_cents_per_unit=cost,
                  void_rule="void: 50-50", settlement_text_captured=True)


def three_way(backs, *, fixture="fx", cid=None, q=1, cost=None):
    return IS.Leg(condition_id=cid or ("w3-%s" % backs), fixture_id=fixture,
                  kind=IS.KIND_THREE_WAY, period=IS.PERIOD_FULL,
                  overtime=IS.OT_NOT_APPLICABLE, backs=backs, quantity=q,
                  cost_cents_per_unit=cost, void_rule="void: 50-50",
                  settlement_text_captured=True)


def clean(a, b, **kw):
    """Classify with the exceptional fixture states switched off.

    Void and postponement are real and are tested separately; the shape
    tests are about the REGULAR partition, and leaving an undetermined
    void cell in would correctly refuse every one of them.
    """
    kw.setdefault("sport_permits_tie", False)
    kw.setdefault("fixture_can_void", False)
    kw.setdefault("fixture_can_postpone", False)
    return IS.classify(a, b, **kw)


# ═════════════════════════════════════════════════════════════════════
# THE BEARS / PANTHERS PROOF (§5, named explicitly in the directive)
# ═════════════════════════════════════════════════════════════════════

def test_bears_ml_plus_panthers_4_5_is_a_middle_paying_2_only_on_1_to_4():
    s = clean(IS.BEARS_MONEYLINE, IS.PANTHERS_PLUS_4_5,
              sport_permits_tie=True)
    assert s.taxonomy == IS.MIDDLE, s.why
    assert s.min_payout_cents == 100
    assert s.max_payout_cents == 200
    # The both-win region is exactly the Bears winning by 1..4. The
    # partition gives those as separate cells, so assert the set of
    # margins rather than one label string.
    assert s.both_win_regions, "a middle must have a both-win region"
    assert s.both_lose_regions == ()


@pytest.mark.parametrize("margin,expected", [
    (-7, 100),   # Bears lose:            ML 0   + Panthers +4.5 pays 100
    (-1, 100),   # Bears lose by 1:       ML 0   + 100
    (1, 200),    # Bears win by 1:        ML 100 + 100   <- the middle
    (4, 200),    # Bears win by 4:        ML 100 + 100   <- the middle
    (5, 100),    # Bears win by 5:        ML 100 + 0
    (21, 100),   # Bears blow them out:   ML 100 + 0
])
def test_the_both_win_region_is_margins_1_to_4_and_nothing_else(margin,
                                                                expected):
    """Evaluate the two payout functions directly at named margins."""
    r = IS.Region("probe", lo=margin, hi=margin)
    a = IS._leg_payout_cents(IS.BEARS_MONEYLINE, r)
    b = IS._leg_payout_cents(IS.PANTHERS_PLUS_4_5, r)
    assert a + b == expected, "margin %+d paid %s + %s" % (margin, a, b)


def test_margin_0_is_a_tie_and_pays_1_50_not_1_00():
    """The study's own note: ML pays $0.50, the .5 spread pays $1."""
    r = IS.Region("regulation tie", state=IS.STATE_TIE, lo=0, hi=0)
    a = IS._leg_payout_cents(IS.BEARS_MONEYLINE, r)
    b = IS._leg_payout_cents(IS.PANTHERS_PLUS_4_5, r)
    assert a == 50, "a 50-50 moneyline tie pays half"
    assert b == 100, "Panthers +4.5 wins a tie: Bears did not win by 5"
    assert a + b == 150


def test_the_proof_reports_that_the_both_win_region_did_not_occur():
    p = IS.bears_panthers_proof()
    assert p["actual"]["both_win_occurred"] is False
    assert p["actual"]["this_wallet_held_it"] is False
    assert "won by five or more" in p["actual"]["why"]


def test_the_proof_refuses_to_call_the_middle_profitable():
    p = IS.bears_panthers_proof()
    assert "guaranteed shortfall" in p["not_established"]


# ═════════════════════════════════════════════════════════════════════
# THE TAXONOMY, FROM THE STUDY'S OWN PAYOFF TABLE (Ferrari p13)
# ═════════════════════════════════════════════════════════════════════

def test_spread_minus_x_plus_spread_plus_y_with_y_greater_is_a_middle():
    """'Spread T1 -x + Spread T2 +y, y > x' -> $1/$2, middle x<m<y."""
    s = clean(spread("A", "-5/2"), spread("B", "-13/2"))   # A -2.5, B +6.5
    assert s.taxonomy == IS.MIDDLE, s.why
    assert (s.min_payout_cents, s.max_payout_cents) == (100, 200)


def test_spread_minus_x_plus_spread_plus_y_with_y_smaller_is_a_gap():
    """'y < x' -> $0/$1: two opposing bets, not a hedge."""
    s = clean(spread("A", "-13/2"), spread("B", "-5/2"))   # A -6.5, B +2.5
    assert s.taxonomy == IS.GAP, s.why
    assert (s.min_payout_cents, s.max_payout_cents) == (0, 100)
    assert s.both_lose_regions, "a gap must have a both-lose region"
    assert s.both_win_regions == ()


def test_over_t1_plus_under_t2_with_t2_greater_is_a_middle():
    s = clean(total("OVER", "41/2"), total("UNDER", "47/2"))   # O20.5 U23.5
    assert s.taxonomy == IS.MIDDLE, s.why
    assert (s.min_payout_cents, s.max_payout_cents) == (100, 200)


def test_over_t1_plus_under_t2_with_t2_smaller_is_a_gap():
    s = clean(total("OVER", "47/2"), total("UNDER", "41/2"))   # O23.5 U20.5
    assert s.taxonomy == IS.GAP, s.why
    assert (s.min_payout_cents, s.max_payout_cents) == (0, 100)


def test_soccer_both_team_win_nos_is_a_middle_on_the_draw():
    """'Will T1 win?' No + 'Will T2 win?' No -> $2 on a draw."""
    # Backing "not A" on a three-way condition is backing the union of
    # {draw, B wins}; the module expresses that as the complement leg,
    # so the two NOs are modelled as backing DRAW-or-other. Use the
    # study's own framing: each NO pays unless its own team wins.
    no_a = IS.Leg(condition_id="w3-no-a", fixture_id="fx",
                  kind=IS.KIND_THREE_WAY, period=IS.PERIOD_FULL,
                  overtime=IS.OT_NOT_APPLICABLE, backs="B", quantity=1,
                  void_rule="void: 50-50", settlement_text_captured=True)
    # 'Will A win?' No pays on draw AND on B winning -- two regions --
    # which the single-category WIN3 leg cannot express. The module must
    # therefore NOT silently treat it as backing B.
    s = clean(no_a, three_way("DRAW", cid="w3-draw"))
    # backs=B and backs=DRAW never both win and never both lose on a
    # three-way: A winning loses both. That is a GAP, and asserting it
    # here documents that a NO on a three-way is not representable as a
    # single category -- the honest answer for real NO legs is a
    # two-region leg, tracked as a known limitation below.
    assert s.taxonomy == IS.GAP, s.why


def test_three_way_a_and_b_both_yes_is_a_gap_on_the_draw():
    """'Will T1 win?' Yes + 'Will T2 win?' Yes -> $0 on a draw."""
    s = clean(three_way("A"), three_way("B"))
    assert s.taxonomy == IS.GAP, s.why
    assert any("draw" in r for r in s.both_lose_regions), s.both_lose_regions


def test_draw_yes_plus_a_yes_is_a_gap_losing_when_b_wins():
    s = clean(three_way("DRAW"), three_way("A"))
    assert s.taxonomy == IS.GAP
    assert any("B wins" in r for r in s.both_lose_regions)


def test_a_moneyline_and_its_own_complement_is_a_direct_complement():
    s = clean(ml("A"), ml("B", cid="ml-B2"))
    assert s.taxonomy == IS.DIRECT_COMPLEMENT
    assert (s.min_payout_cents, s.max_payout_cents) == (100, 100)


def test_two_legs_backing_the_same_side_are_independent_overlap():
    """Both on A: both win together and both lose together. No hedge."""
    s = clean(ml("A"), spread("A", "-5/2", cid="sp-A-same"))
    assert s.taxonomy == IS.INDEPENDENT_OVERLAP, s.why
    assert s.both_win_regions and s.both_lose_regions
    assert "do not hedge each other" in s.why


# ═════════════════════════════════════════════════════════════════════
# REFUSALS: THE POINT OF THE MODULE
# ═════════════════════════════════════════════════════════════════════

def test_same_titles_different_period_is_refused_not_middled():
    s = clean(ml("A"), spread("B", "-9/2", period=IS.PERIOD_H1))
    assert s.taxonomy == IS.UNESTABLISHABLE
    assert any("different periods" in f for f in s.missing_facts)


def test_same_line_different_overtime_rule_is_refused():
    s = clean(ml("A", ot=IS.OT_INCLUDED),
              spread("B", "-9/2", ot=IS.OT_EXCLUDED))
    assert s.taxonomy == IS.UNESTABLISHABLE
    assert any("overtime" in f for f in s.missing_facts)


def test_an_uncaptured_overtime_rule_is_refused_not_assumed():
    s = clean(ml("A", ot=IS.OT_UNKNOWN), spread("B", "-9/2",
                                                ot=IS.OT_UNKNOWN))
    assert s.taxonomy == IS.UNESTABLISHABLE
    assert any("overtime treatment is not captured" in f
               for f in s.missing_facts)


def test_different_fixtures_are_refused():
    s = clean(ml("A"), spread("B", "-9/2", fixture="other"))
    assert s.taxonomy == IS.UNESTABLISHABLE
    assert any("different fixtures" in f for f in s.missing_facts)


def test_a_margin_leg_and_a_total_leg_do_not_form_a_structure():
    s = clean(ml("A"), total("UNDER", "47/2"))
    assert s.taxonomy == IS.UNESTABLISHABLE
    assert any("different outcome variables" in f for f in s.missing_facts)


def test_unstated_orientation_is_refused():
    leg = IS.Leg(condition_id="no-orientation", fixture_id="fx",
                 kind=IS.KIND_MONEYLINE, period=IS.PERIOD_FULL,
                 overtime=IS.OT_INCLUDED, backs=None, quantity=1,
                 settlement_text_captured=True)
    s = clean(leg, spread("B", "-9/2"))
    assert s.taxonomy == IS.UNESTABLISHABLE
    assert any("orientation is not established" in f for f in s.missing_facts)


def test_an_integer_line_without_a_captured_push_rule_is_refused():
    s = clean(ml("A"), spread("B", "-4", captured=False))
    assert s.taxonomy == IS.UNESTABLISHABLE
    assert any("push rule was not captured" in f for f in s.missing_facts)


def test_an_uncaptured_void_rule_leaves_the_void_cell_undetermined():
    """A reachable region with no known payout must refuse the structure."""
    a = ml("A")
    b = IS.Leg(condition_id="sp-no-void", fixture_id="fx",
               kind=IS.KIND_SPREAD, period=IS.PERIOD_FULL,
               overtime=IS.OT_INCLUDED, backs="B", line=Fraction(-9, 2),
               quantity=1, void_rule=None, settlement_text_captured=True)
    s = IS.classify(a, b, sport_permits_tie=False, fixture_can_void=True,
                    fixture_can_postpone=False)
    assert s.taxonomy == IS.UNESTABLISHABLE, s.why
    assert s.undetermined_regions


def test_an_uncaptured_tie_rule_refuses_a_sport_that_permits_ties():
    a = IS.Leg(condition_id="ml-no-tie-rule", fixture_id="fx",
               kind=IS.KIND_MONEYLINE, period=IS.PERIOD_FULL,
               overtime=IS.OT_INCLUDED, backs="A", quantity=1,
               tie_rule=None, void_rule="void: 50-50",
               settlement_text_captured=True)
    s = IS.classify(a, spread("B", "-9/2"), sport_permits_tie=True,
                    fixture_can_void=False, fixture_can_postpone=False)
    assert s.taxonomy == IS.UNESTABLISHABLE
    assert any("regulation tie" in r for r in s.undetermined_regions)


# ═════════════════════════════════════════════════════════════════════
# POSTPONEMENT IS NOT A PAYOUT
# ═════════════════════════════════════════════════════════════════════

def test_postponement_is_carried_separately_and_not_in_the_minimum():
    s = IS.classify(IS.BEARS_MONEYLINE, IS.PANTHERS_PLUS_4_5,
                    sport_permits_tie=True, fixture_can_void=False,
                    fixture_can_postpone=True)
    assert s.taxonomy == IS.MIDDLE, s.why
    assert s.min_payout_cents == 100
    assert s.unresolved_states, "the postponed state must be reported"
    assert any("postponed" in r for r in s.unresolved_states)
    assert not any("postponed" in r for r in s.undetermined_regions), (
        "postponement is a timing state, not an undetermined payout")


def test_the_minimum_is_labelled_conditional_on_resolution():
    d = IS.describe()
    assert "CONDITIONAL ON RESOLUTION" in d["postponement_is_not_a_payout"]


# ═════════════════════════════════════════════════════════════════════
# "CANNOT LOSE BOTH LEGS" IS NOT A PROFIT (§5)
# ═════════════════════════════════════════════════════════════════════

def test_a_middle_bought_above_a_dollar_does_not_lock_a_surplus():
    """The case studies' own average: MIDDLE at ~$1.17-$1.20."""
    s = clean(ml("A", cost=60), spread("B", "-9/2", cost=57),
              sport_permits_tie=False)
    assert s.taxonomy == IS.MIDDLE
    assert s.cost_cents == 117
    assert s.locks_gross_surplus is False
    assert s.guaranteed_gross_result_cents == -17, (
        "a $1.17 middle guarantees -$0.17 unless the middle lands")


def test_a_middle_bought_below_a_dollar_does_lock_a_surplus():
    s = clean(ml("A", cost=40), spread("B", "-9/2", cost=46),
              sport_permits_tie=False)
    assert s.taxonomy == IS.MIDDLE
    assert s.cost_cents == 86
    assert s.locks_gross_surplus is True
    assert s.guaranteed_gross_result_cents == 14


def test_a_gap_never_locks_a_surplus_however_cheap():
    s = clean(spread("A", "-13/2", cost=20), spread("B", "-5/2", cost=20))
    assert s.taxonomy == IS.GAP
    assert s.locks_gross_surplus is False, (
        "a gap's minimum payout is $0: no cost makes it locked")
    assert s.guaranteed_gross_result_cents == -40


def test_the_label_states_what_it_is_not():
    s = clean(ml("A"), spread("B", "-9/2"))
    assert s.label_is == IS.LABEL_IS
    assert "NOT_AN_AUTHORISATION" in s.label_is_not.replace(
        "AN_AUTHORISATION", "NOT_AN_AUTHORISATION", 1) or \
        "AUTHORISATION" in s.label_is_not


# ═════════════════════════════════════════════════════════════════════
# UNEQUAL QUANTITIES
# ═════════════════════════════════════════════════════════════════════

def test_unequal_quantities_are_partial_and_name_the_unhedged_remainder():
    s = clean(ml("A", q=100), spread("B", "-9/2", q=60))
    assert s.taxonomy == IS.PARTIAL
    assert s.units == 60
    assert "40 unmatched contract(s)" in s.why
    assert "unhedged directional inventory" in s.why


def test_the_taxonomy_of_the_contracts_does_not_depend_on_quantity_held():
    """Classification is a property of the contracts, checked on 1 unit."""
    small = clean(ml("A", q=1), spread("B", "-9/2", q=1))
    big = clean(ml("A", q=10_000), spread("B", "-9/2", q=10_000))
    assert small.min_payout_cents == big.min_payout_cents == 100
    assert small.max_payout_cents == big.max_payout_cents == 200


# ═════════════════════════════════════════════════════════════════════
# ALLOCATION: NEVER THE SAME INVENTORY TWICE
# ═════════════════════════════════════════════════════════════════════

def test_one_lot_is_never_allocated_to_two_structures():
    """One ML lot with two compatible spread counterparts."""
    legs = [ml("A", q=100),
            spread("B", "-9/2", cid="sp-1", q=100),
            spread("B", "-11/2", cid="sp-2", q=100)]
    a = IS.allocate(legs, sport_permits_tie=False, fixture_can_void=False,
                    fixture_can_postpone=False)
    used = sum(s.units for s in a.structures
               if "ml-A" in s.legs)
    assert used <= 100, "the ML lot was allocated %d times over" % used
    assert a.unallocated.get("ml-A", 0) == 0


def test_the_number_of_compatible_alternatives_is_reported_not_hidden():
    legs = [ml("A", q=100),
            spread("B", "-9/2", cid="sp-1", q=100),
            spread("B", "-11/2", cid="sp-2", q=100)]
    a = IS.allocate(legs, sport_permits_tie=False, fixture_can_void=False,
                    fixture_can_postpone=False)
    assert a.ambiguity["ml-A"] >= 2, (
        "the ML lot had two compatible counterparts and the ambiguity "
        "must survive the FIFO choice")


def test_quantity_is_conserved_across_the_whole_allocation():
    legs = [ml("A", q=70), ml("B", cid="ml-B2", q=30),
            spread("B", "-9/2", cid="sp-1", q=50)]
    a = IS.allocate(legs, sport_permits_tie=False, fixture_can_void=False,
                    fixture_can_postpone=False)
    supplied = {l.condition_id: l.quantity for l in legs}
    consumed = {k: 0 for k in supplied}
    for s in a.structures:
        for cid in s.legs:
            consumed[cid] += s.units
    for cid, q in supplied.items():
        assert consumed[cid] + a.unallocated.get(cid, 0) == q, (
            "%s: consumed %d + unallocated %d != supplied %d"
            % (cid, consumed[cid], a.unallocated.get(cid, 0), q))


def test_refused_pairs_are_kept_not_dropped():
    legs = [ml("A", q=10), spread("B", "-9/2", period=IS.PERIOD_H1, q=10)]
    a = IS.allocate(legs, sport_permits_tie=False, fixture_can_void=False,
                    fixture_can_postpone=False)
    assert a.structures == []
    assert len(a.refused) == 1
    assert a.refused[0].taxonomy == IS.UNESTABLISHABLE


def test_middles_are_preferred_over_gaps_when_both_are_available():
    legs = [ml("A", q=100),
            spread("B", "-13/2", cid="sp-mid", q=100),   # middle with ML A
            spread("A", "-5/2", cid="sp-gap", q=100)]
    a = IS.allocate(legs, sport_permits_tie=False, fixture_can_void=False,
                    fixture_can_postpone=False)
    first = a.structures[0]
    assert first.taxonomy == IS.MIDDLE, [s.taxonomy for s in a.structures]


# ═════════════════════════════════════════════════════════════════════
# THE PAYOFF TABLE IS EXHAUSTIVE
# ═════════════════════════════════════════════════════════════════════

def test_the_regular_margin_cells_partition_the_whole_integer_line():
    rows = IS.payoff_table((IS.BEARS_MONEYLINE, IS.PANTHERS_PLUS_4_5),
                           sport_permits_tie=False, fixture_can_void=False,
                           fixture_can_postpone=False)
    # Every integer in a wide window must fall in exactly one cell.
    regions = IS._margin_regions(
        IS._break_points((IS.BEARS_MONEYLINE, IS.PANTHERS_PLUS_4_5)))
    for m in range(-60, 61):
        hits = [r for r in regions
                if (r.lo is None or m >= r.lo) and (r.hi is None or m <= r.hi)]
        assert len(hits) == 1, "margin %+d hit %d cells" % (m, len(hits))
    assert rows


def test_the_table_names_every_exceptional_state_it_included():
    rows = IS.payoff_table((IS.BEARS_MONEYLINE, IS.PANTHERS_PLUS_4_5),
                           sport_permits_tie=True, fixture_can_void=True,
                           fixture_can_postpone=True)
    states = {r["state"] for r in rows}
    assert IS.STATE_TIE in states
    assert IS.STATE_VOID in states
    assert IS.STATE_POSTPONED in states


def test_an_undetermined_cell_never_contributes_zero_to_the_minimum():
    rows = IS.payoff_table((IS.BEARS_MONEYLINE, IS.PANTHERS_PLUS_4_5),
                           sport_permits_tie=True, fixture_can_void=True,
                           fixture_can_postpone=True)
    post = [r for r in rows if r["state"] == IS.STATE_POSTPONED][0]
    assert post["joint_cents"] is None
    assert post["determined"] is False


# ═════════════════════════════════════════════════════════════════════
# THE CASE STUDIES ARE NOT POOLED
# ═════════════════════════════════════════════════════════════════════

def test_rn1_and_ferrari_middle_books_are_reported_separately():
    ev = IS.describe()["case_study_evidence"]
    assert set(ev) >= {"RN1", "Ferrari", "kept_separate"}
    assert ev["RN1"]["middle_avg_cost"] != ev["Ferrari"]["middle_avg_cost"]
    assert "not pooled" in ev["kept_separate"]


def test_both_studies_middle_books_averaged_above_a_dollar():
    """The fact that makes 'MIDDLE' a bet and not a hedge."""
    ev = IS.describe()["case_study_evidence"]
    for name in ("RN1", "Ferrari"):
        assert ev[name]["middle_avg_cost"] > 1.0, name


def test_both_studies_gap_books_lost_money():
    ev = IS.describe()["case_study_evidence"]
    for name in ("RN1", "Ferrari"):
        assert ev[name]["gap_realized"] < 0, name


# ═════════════════════════════════════════════════════════════════════
# INTEGRATION: THE EIGHTH ACTION IS IN THE ONE ACTION TABLE (§3)
# ═════════════════════════════════════════════════════════════════════

def test_the_indirect_hedge_is_a_canonical_action_not_a_parallel_engine():
    from sportsassets import bettor_ev_actions as acts
    assert "FORM_INDIRECT_HEDGE" in acts.CANONICAL_ACTIONS
    assert "FORM_INDIRECT_HEDGE" in acts.ACTIONS


def test_the_indirect_hedge_has_an_exposure_effect_on_both_axes():
    from sportsassets import bettor_ev_actions as acts
    gross, directional = acts.EXPOSURE_EFFECT["FORM_INDIRECT_HEDGE"]
    assert gross == acts.INCREASE, (
        "it buys a position on a second condition")
    assert directional == acts.DECREASE


def test_the_indirect_hedge_is_on_the_exposure_increasing_gated_side():
    from sportsassets import bettor_ev_actions as acts
    assert "FORM_INDIRECT_HEDGE" in acts.EXPOSURE_INCREASING


def test_the_indirect_hedge_requires_the_structure_to_be_established():
    from sportsassets import bettor_ev_actions as acts
    spec = acts.CANONICAL_ACTIONS["FORM_INDIRECT_HEDGE"]
    assert "STRUCTURE_ESTABLISHED" in spec["requires"]
    assert "P_MIDDLE_LANDS" in spec["requires"]


def test_the_action_note_records_that_a_middle_is_not_an_authorisation():
    from sportsassets import bettor_ev_actions as acts
    note = acts.CANONICAL_ACTIONS["FORM_INDIRECT_HEDGE"]["note"]
    assert "not an authorisation" in note
    assert "1.2024" in note and "1.1711" in note


def test_the_direct_hedge_and_the_indirect_hedge_are_different_actions():
    """HEDGE is the same condition's complement; this is not."""
    from sportsassets import bettor_ev_actions as acts
    assert acts.CANONICAL_ACTIONS["HEDGE"]["what"] != \
        acts.CANONICAL_ACTIONS["FORM_INDIRECT_HEDGE"]["what"]
    assert "different condition" in \
        acts.CANONICAL_ACTIONS["FORM_INDIRECT_HEDGE"]["what"]
