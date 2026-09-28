"""The action register must cover every action and agree with the gate.

Owner rule: "Every advertised action must either have a verified
executable path or be explicitly unavailable. An unavailable required
capability remains unfinished; disabling it is containment, not
completion."

An inventory that drifted from the code would be worse than none -- this
repository has shipped a stale hand-written inventory twice. So these
tests derive the advertised set from the SOURCE and from the dispatcher's
own constants, and fail if the register misses or invents anything.
"""

import re

import pytest

from sportsassets import bettor_action_inventory as AI
from sportsassets import bettor_funded_management as FM
from sportsassets import bettor_mgmt_select as MS


def _actions_in_the_ranker() -> set:
    import inspect
    return set(re.findall(r'"action": "([A-Z_]+)"', inspect.getsource(MS)))


# ═════════════════════════════════════════════════════════════════════
# 1 · COVERAGE: NOTHING ADVERTISED IS MISSING, NOTHING INVENTED
# ═════════════════════════════════════════════════════════════════════

def test_every_action_the_ranker_advertises_is_in_the_register():
    missing = _actions_in_the_ranker() - set(AI.ACTIONS)
    assert not missing, (
        "the ranker advertises %s and the register does not list them. An "
        "advertised action with no verdict is exactly what the rule "
        "forbids" % sorted(missing))


def test_every_unimplemented_route_is_in_the_register():
    missing = set(FM.UNIMPLEMENTED_ROUTES) - set(AI.ACTIONS)
    assert not missing, sorted(missing)


def test_the_register_invents_no_action():
    known = _actions_in_the_ranker() | set(FM.UNIMPLEMENTED_ROUTES)
    extra = set(AI.ACTIONS) - known
    assert not extra, (
        "%s are in the register but advertised nowhere. A register that "
        "lists actions the system does not offer overstates its surface"
        % sorted(extra))


@pytest.mark.parametrize("name", sorted(AI.ACTIONS))
def test_every_entry_has_a_known_status(name):
    assert AI.ACTIONS[name]["status"] in (
        AI.EXECUTABLE, AI.NOT_APPLICABLE, AI.REQUIRED_BUT_UNAVAILABLE,
        AI.BLOCKED_ON_EVIDENCE)


# ═════════════════════════════════════════════════════════════════════
# 2 · THE REGISTER AGREES WITH THE GATE THAT ACTUALLY RESTRICTS
# ═════════════════════════════════════════════════════════════════════

def test_the_order_sending_executables_are_exactly_the_gate():
    """The gate is EXECUTABLE_ACTIONS; HOLD sends no order and is not in it."""
    sending = tuple(sorted(
        a for a in AI.executable_actions()
        if AI.ACTIONS[a]["sends_an_order"]))
    assert sending == tuple(sorted(FM.EXECUTABLE_ACTIONS)), (
        sending, FM.EXECUTABLE_ACTIONS)


def test_hold_is_executable_but_sends_nothing():
    assert AI.ACTIONS["HOLD"]["status"] == AI.EXECUTABLE
    assert AI.ACTIONS["HOLD"]["sends_an_order"] is False
    assert "HOLD" not in FM.EXECUTABLE_ACTIONS


def test_nothing_unavailable_is_in_the_gate():
    for name, v in AI.ACTIONS.items():
        if v["status"] != AI.EXECUTABLE:
            assert name not in FM.EXECUTABLE_ACTIONS, (
                "%s is %s and would still be selectable" % (name, v["status"]))


# ═════════════════════════════════════════════════════════════════════
# 3 · EVERY EXECUTABLE ENTRY NAMES A DISPATCH AND A TEST
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("name", sorted(
    a for a, v in AI.ACTIONS.items() if v["status"] == AI.EXECUTABLE))
def test_an_executable_action_names_its_dispatch_and_its_proof(name):
    v = AI.ACTIONS[name]
    assert v.get("dispatch"), name
    assert v.get("verified_by"), (
        "%s claims EXECUTABLE with no named test. 'Verified' without a "
        "citation is an assertion" % name)


@pytest.mark.parametrize("name", ["DIRECT_EXIT", "REDUCE"])
def test_an_order_sending_action_names_the_switch_that_is_off(name):
    assert "FUNDED_EXIT_SUBMISSION_ENABLED" in AI.ACTIONS[name]["gated_by"]
    assert FM.FUNDED_EXIT_SUBMISSION_ENABLED is False


# ═════════════════════════════════════════════════════════════════════
# 4 · NOT_APPLICABLE MUST CITE THE VENUE, NOT A PREFERENCE
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("name", sorted(
    a for a, v in AI.ACTIONS.items() if v["status"] == AI.NOT_APPLICABLE))
def test_not_applicable_rests_on_a_cited_venue_source(name):
    v = AI.ACTIONS[name]
    assert v.get("rests_on"), name
    for key in v["rests_on"]:
        src = AI.VENUE_SOURCES[key]
        assert src["page"].startswith("docs.polymarket.us")
        assert len(src["sha256"]) == 64
        assert src["quote"]
    assert "completion not containment" in \
        v["this_is_completion_not_containment"].lower() or \
        "not because we" in v["this_is_completion_not_containment"]


def test_take_complement_would_be_unavailable_on_a_two_token_venue():
    """The verdict is venue-specific and says so."""
    v = AI.ACTIONS["TAKE_COMPLEMENT"]
    assert "TWO_TOKEN" in v["where_it_would_be_real"]
    assert "REQUIRED_BUT_UNAVAILABLE, not EXECUTABLE" in \
        v["where_it_would_be_real"]


# ═════════════════════════════════════════════════════════════════════
# 5 · UNFINISHED MEANS NAMED GAPS, AN OWNER AND A PATH
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("name", sorted(
    a for a, v in AI.ACTIONS.items()
    if v["status"] == AI.REQUIRED_BUT_UNAVAILABLE))
def test_an_unfinished_action_names_its_gaps_owner_and_path(name):
    v = AI.ACTIONS[name]
    assert v.get("required_by"), name
    assert v.get("missing") and len(v["missing"]) >= 1, name
    assert v.get("owner"), name
    assert v.get("completion_path"), (
        "%s is unfinished with no completion path. 'Implement the feature' "
        "is not a path" % name)


def test_the_four_pairing_capabilities_are_all_unfinished():
    """None of them is quietly counted as done."""
    unfinished = set(AI.unfinished_actions())
    for name in ("FORM_INDIRECT_HEDGE", "COMPLETE_PAIR", "MERGE",
                 "POST_COMPLEMENT"):
        assert name in unfinished, name


def test_the_indirect_hedge_names_the_half_executed_risk():
    v = AI.ACTIONS["FORM_INDIRECT_HEDGE"]
    assert any("restart recovery" in m for m in v["missing"])
    assert "NEW directional position" in v["completion_path"]
    # AND that the cross-event case gets no venue capital efficiency.
    assert "SAME event" in v["and_a_venue_dependency"]


# ═════════════════════════════════════════════════════════════════════
# 6 · THE SUMMARY DOES NOT CLAIM COMPLETION
# ═════════════════════════════════════════════════════════════════════

def test_the_summary_states_the_system_is_not_complete():
    d = AI.describe()
    assert d["unfinished_count"] >= 1
    assert "NOT complete" in d["so_the_honest_summary"]
    assert str(d["executable_count"]) in d["so_the_honest_summary"]
    assert str(d["unfinished_count"]) in d["so_the_honest_summary"]


def test_the_register_says_it_is_not_the_gate():
    d = AI.describe()
    assert "EXECUTABLE_ACTIONS is what actually" in d["the_gate_is_elsewhere"]


def test_the_rule_is_quoted_not_paraphrased_away():
    d = AI.describe()
    assert "containment, not" in d["the_rule"]


# ═════════════════════════════════════════════════════════════════════
# 6 · THE VENUE SURFACE IS ENUMERATED, NOT SAMPLED (2026-09-28)
# ═════════════════════════════════════════════════════════════════════
#
# Every "the venue does not support X" verdict in this register used to
# rest on X not being MENTIONED in whichever pages had been read. That is
# an argument from silence. Reading the API reference INDEX enumerates the
# surface, so a missing operation becomes a positive finding -- and the
# same read turned up TWO capabilities I had not known about, one of which
# contradicted a conclusion I was about to ship.


def test_the_authenticated_api_surface_is_enumerated_with_its_source():
    s = AI.VENUE_SOURCES["authenticated_api_surface_is_enumerated"]
    assert s["page"] == "docs.polymarket.us/api-reference/introduction"
    assert s["retrieved"] == "2026-09-28"
    # The three groups, in the venue's own words.
    assert set(s["groups"]) == {"Orders", "Portfolio", "Account"}
    # The order operations, in full -- so a later reader can check rather
    # than trust that no merge hides among them.
    for op in ("create-order", "close-position-order", "cancel-all-open-orders",
               "modify-multiple-orders", "preview-order"):
        assert op in s["order_operations"], op
    for absent in ("merge", "split", "netting", "redeem", "convert",
                   "combine"):
        assert absent in s["no_operation_exists_for"], absent
    # AND THE POINT: close-position is an order, not a merge.
    assert "ORDER" in s["and_what_close_position_actually_is"]
    assert "spread" in s["and_what_close_position_actually_is"]


def test_merge_and_complete_pair_now_answer_the_venue_question():
    """Both rested on 'establish whether the venue releases collateral'.

    It is established: no. And both must cite the enumerated surface, so
    the verdict is traceable to the index rather than to two sampled pages.
    """
    for name in ("MERGE", "COMPLETE_PAIR"):
        a = AI.ACTIONS[name]
        assert a["venue_question_answered"].startswith("NO")
        assert "authenticated_api_surface_is_enumerated" in a["rests_on"]


def test_merge_is_not_promoted_to_not_applicable_on_a_documentation_read():
    """Two things are unread and either could change the verdict.

    The register exists to avoid exactly this kind of early claim, so the
    status must stay REQUIRED_BUT_UNAVAILABLE and say why.
    """
    a = AI.ACTIONS["MERGE"]
    assert a["status"] == AI.REQUIRED_BUT_UNAVAILABLE
    assert "NOT_APPLICABLE" in a["why_the_status_is_unchanged"]
    deps = a["specific_remaining_dependencies"]
    assert len(deps) >= 2
    joined = " ".join(deps)
    assert "combo" in joined.lower()
    assert "Institutional" in joined


def test_form_indirect_hedge_records_the_conclusion_i_corrected():
    """I was about to ship 'needs a SECOND VENUE'. The API contradicts it.

    `POST /v1/combos` takes 2-10 legs with independent sides on different
    markets, on THIS venue. The correction has to be visible in the
    register, not silently replaced -- otherwise the next reader inherits
    my reasoning without knowing it was wrong once.
    """
    a = AI.ACTIONS["FORM_INDIRECT_HEDGE"]
    assert a["status"] == AI.REQUIRED_BUT_UNAVAILABLE
    cap = a["venue_capability_found"]
    assert cap["endpoint"] == "POST /v1/combos"
    assert "2-10" in cap["what_it_does"]
    assert "second-venue premise" in cap["why_it_matters_here"]
    # The status note must explain why a capability is not a path.
    assert "VENUE CAPABILITY, not a verified path" in a["status_note"]
    # And the open questions must be named, including the shared quota.
    deps = " ".join(a["specific_remaining_dependencies"])
    assert "invalid combination" in deps.lower()
    assert "1,000 new instruments per week" in deps
    assert "10 requests per 10 seconds" in deps


def test_post_complement_names_a_taker_route_that_avoids_p_fill():
    """P_FILL is about OUR RESTING order. A taker holds no queue position.

    The venue states combos trade directly on the Orders API and that RFQ
    use is optional, and the RFQ requester ACCEPTS a quote. Both are
    taker-side, so the unmeasurable input is not on that path -- while the
    maker path's blocker is unchanged.
    """
    a = AI.ACTIONS["POST_COMPLEMENT"]
    r = a["a_taker_route_may_avoid_p_fill_entirely"]
    assert "optional" in r["route_1"].lower()
    assert "accept" in r["route_2"].lower()
    assert "TAKER-side" in r["why_p_fill_does_not_apply"]
    assert "no queue position" in r["why_p_fill_does_not_apply"]
    # IT MUST NOT CLAIM THE ECONOMICS. A taker pays the spread.
    assert "spread" in r["and_what_is_still_needed"]
    assert "NOT been run" in r["and_what_is_still_needed"]
    # AND the submitted-is-not-filled semantic must be carried over.
    assert "do not mean the orders" in r["and_one_semantic_the_lane_must_carry"]
    # The maker-side dependency stays exactly as it was.
    assert "BETTOR_NATIVE_ADMITTED_FILLS_REQUIRED" in a[
        "specific_remaining_dependency"]
    assert "DOWNSTREAM of funded activation" in a["cannot_be_completed_before"]


def test_the_combo_freshness_blocker_is_not_forgotten():
    """A combo book needs currency evidence too, and none is available.

    The most likely way to fool oneself here is to treat a newly-found
    venue capability as a way around the blocker that stops every exit
    today. It is not: the same freshness mechanism is missing.
    """
    a = AI.ACTIONS["POST_COMPLEMENT"]
    deps = " ".join(a["specific_remaining_dependencies_for_the_taker_route"])
    assert "freshness" in deps
    assert "blocks every exit today" in deps


def test_the_count_of_unfinished_capabilities_is_still_four():
    """Nothing was promoted on a documentation read. The system is not
    complete and the headline number must not have quietly improved."""
    assert len(AI.unfinished_actions()) == 4
    assert AI.describe()["unfinished_count"] == 4
    assert "NOT complete" in AI.describe()["so_the_honest_summary"]


def test_a_combo_is_recorded_as_not_a_substitute_for_the_hedge():
    """THE SECOND CORRECTION TO THIS ENTRY, and the more serious one.

    I recorded `POST /v1/combos` as removing the second-venue premise for
    FORM_INDIRECT_HEDGE -- which it does -- and left the impression that a
    combo could therefore serve as the hedge. It cannot. The combos FAQ:

        "Every leg has to resolve the way you took it for the combo to pay."
        payout = potential x PRODUCT of every leg's value
        "[one leg against] and the combo pays $0.00. This holds however the
         other legs turn out."

    A combo is MULTIPLICATIVE; separate holdings are ADDITIVE with a floor.
    On Bears ML + Panthers +4.5 the separate pair never returns zero and the
    combo returns zero in three of four margin scenarios. Substituting it
    would INVERT the risk in a lane authorized only to reduce exposure.
    """
    a = AI.ACTIONS["FORM_INDIRECT_HEDGE"]
    note = a["it_is_not_a_substitute_for_the_hedge"]
    assert "MULTIPLICATIVE" in note
    assert "ADDITIVE" in note
    assert "inverts the risk" in note
    # The void treatment has no additive analogue and must be recorded.
    void = a["and_a_void_leg_scales_the_whole_position"]
    assert "LFMP" in void
    assert "not" in void.lower() and "removed" in void
    assert "Settlement Committee" in void
    assert a["payoff_source"].startswith("docs.polymarket.us/faqs/combos-faqs")
    # AND THE CAPABILITY STAYS OPEN. A payoff analysis is not an integration.
    assert a["status"] == AI.REQUIRED_BUT_UNAVAILABLE


def test_the_capability_discovery_did_not_promote_anything():
    """Documentation discovery is evidence, not completed integration.

    Two venue capabilities were found and one payoff was refuted. None of
    that implements anything, so the count must be unchanged at four.
    """
    assert len(AI.unfinished_actions()) == 4
    assert AI.describe()["unfinished_count"] == 4
