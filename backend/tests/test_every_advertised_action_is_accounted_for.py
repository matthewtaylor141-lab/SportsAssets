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
