"""THREE DEFECTS CODEX REPRODUCED AT b616aa2, AND THEIR EXACT COUNTEREXAMPLES.

Each was a case where something that looked like a check was not one:

  1. FIXTURE CONFIRMATION accepted two DIFFERENT fixtures. Provider Manchester
     United vs Manchester City against venue Manchester United vs Liverpool
     returned ok=True, strong_matches=1, shared=["manchester"] -- because
     `_participants` dropped "United" and "City" as club-form noise, both
     provider teams collapsed to {manchester}, and ONE venue token satisfied
     BOTH sides. A two-sided match that was one shared token counted twice.

  2. A FAILED DECISION WRITE DID NOT STOP DISPATCH. `decide_and_record` can
     return ok=False and still carry an action; `pass_once` read the action and
     dispatched without looking at `ok`. An order could reach the venue with no
     durable record of the decision authorising it.

  3. THE ORDER WAS NOT BOUND TO THE WINNING CANDIDATE. The exit branch fetched
     a deferred selection by POSITION ID and sent it. Given a ranking selecting
     REDUCE for 2 and a selection holding DIRECT_EXIT for 10, the dispatcher got
     the ten-contract exit while the pass reported REDUCE.

These tests exercise ACCEPTANCE BEHAVIOUR -- what is and is not sent -- rather
than checking explanatory strings.
"""

import pytest

from sportsassets import bettor_funded_pair_cycle as PC
from sportsassets.workers import ext_pinnacle_loop as loop


# ═════════════════════════════════════════════════════════════════════
# 1 · IDENTITY: A ONE-TO-ONE MATCH OF BOTH TEAMS
# ═════════════════════════════════════════════════════════════════════

MU_V_MC = [{"home_team": "Manchester United", "away_team": "Manchester City"}]
MU_V_LIV = ["Manchester United vs Liverpool"]


def test_codex_counterexample_a_shared_city_is_not_a_match():
    """THE EXACT REPRODUCTION. Two different fixtures sharing one city name."""
    got = loop.confirm_mapping_by_fixtures(
        provider_events=MU_V_MC, venue_event_titles=MU_V_LIV)
    assert got["ok"] is False, got
    assert got["refusal"] == loop.R_MAPPING_FIXTURES_DO_NOT_MATCH
    assert got["strong_matches"] == 0
    assert got["matched_fixtures"] == []


def test_the_discriminating_word_is_never_dropped():
    """"United" and "City" ARE the difference between two clubs in one city.
    Dropping them is what made the counterexample match."""
    mu, _ = loop._team_tokens("Manchester United")
    mc, _ = loop._team_tokens("Manchester City")
    assert "united" in mu and "city" in mc
    assert not loop._same_team(mu, mc)
    # AND THE AFFILIATION MARKERS STILL ARE dropped, with the drop reported.
    im1, dropped = loop._team_tokens("Inter Miami CF")
    im2, _ = loop._team_tokens("Inter Miami")
    assert dropped == ["cf"]
    assert loop._same_team(im1, im2)


def test_a_legitimate_fixture_confirms_and_reports_its_normalisation():
    got = loop.confirm_mapping_by_fixtures(
        provider_events=[{"home_team": "Columbus Crew",
                          "away_team": "Inter Miami CF"}],
        venue_event_titles=["Columbus Crew vs. Inter Miami"])
    assert got["ok"] is True, got
    assert got["strong_matches"] == 1
    m = got["matched_fixtures"][0]
    assert m["orientation"] == "SAME"
    # EVERY DROP IS ON THE RECORD, so a match can be checked rather than trusted.
    assert got["normalisation"], got
    assert all("affiliation" in n["why"] for n in got["normalisation"])


def test_reversed_sides_still_confirm():
    """The two sources do not agree on home/away ordering, so a swap is the
    same fixture -- but it must still be a ONE-TO-ONE assignment."""
    got = loop.confirm_mapping_by_fixtures(
        provider_events=[{"home_team": "Liverpool",
                          "away_team": "Manchester United"}],
        venue_event_titles=MU_V_LIV)
    assert got["ok"] is True, got
    assert got["matched_fixtures"][0]["orientation"] == "SIDES_SWAPPED"


def test_the_same_teams_on_a_different_day_are_a_different_meeting():
    """Two teams meet more than once a season. Names alone cannot separate a
    fixture from its return leg, so the date is compared where both sides
    supply one."""
    title = "Columbus Crew vs. Inter Miami CF"
    got = loop.confirm_mapping_by_fixtures(
        provider_events=[{"home_team": "Columbus Crew",
                          "away_team": "Inter Miami CF",
                          "commence_time": "2026-11-02T20:00:00Z"}],
        venue_event_titles=[title],
        venue_event_days={title: "2026-09-26"})
    assert got["ok"] is False, got
    assert got["refusal"] == loop.R_MAPPING_FIXTURES_DO_NOT_MATCH
    assert any("different days" in r.get("why", "")
               for r in got["rejected_fixtures"]), got["rejected_fixtures"]


def test_the_same_teams_on_the_same_day_confirm_on_the_date_too():
    title = "Columbus Crew vs. Inter Miami CF"
    got = loop.confirm_mapping_by_fixtures(
        provider_events=[{"home_team": "Columbus Crew",
                          "away_team": "Inter Miami CF",
                          "commence_time": "2026-09-26T23:30:00Z"}],
        venue_event_titles=[title],
        venue_event_days={title: "2026-09-26"})
    assert got["ok"] is True, got
    assert got["matched_fixtures"][0]["date_check"]["same"] is True
    assert got["dating_incomplete"] is False


def test_a_confirmation_without_dating_says_so_rather_than_implying_it():
    """An undated match is weaker evidence than a dated one and must not be
    reported as though it excluded a repeated meeting."""
    got = loop.confirm_mapping_by_fixtures(
        provider_events=[{"home_team": "Columbus Crew",
                          "away_team": "Inter Miami CF"}],
        venue_event_titles=["Columbus Crew vs. Inter Miami CF"])
    assert got["ok"] is True
    assert got["dating_incomplete"] is True
    assert "not excluded" in got["why"]


def test_a_title_that_does_not_name_two_distinct_sides_is_refused():
    for title in ("Some Outright Winner", "Arsenal vs Arsenal"):
        got = loop.confirm_mapping_by_fixtures(
            provider_events=MU_V_MC, venue_event_titles=[title])
        assert got["ok"] is False, (title, got)
        assert any(r["refusal"] == loop.R_SIDES_NOT_TWO
                   for r in got["rejected_fixtures"]), got


def test_confirmation_admits_no_contract_and_says_so():
    """Candidate discovery is separate from confirmed identity. This function
    answers only "are these the same competition"."""
    got = loop.confirm_mapping_by_fixtures(
        provider_events=MU_V_MC, venue_event_titles=MU_V_LIV)
    assert got["this_confirms_identity_not_a_candidate"] is True
    assert "admitted" not in got and "candidate" not in got


# ═════════════════════════════════════════════════════════════════════
# 2 · A FAILED DECISION WRITE MUST STOP DISPATCH
# ═════════════════════════════════════════════════════════════════════

class _Dispatcher:
    """Records every dispatch request. Nothing reaches a venue."""

    def __init__(self):
        self.calls: list = []

    async def __call__(self, conn, *, selection, adapter, venue, now=None):
        self.calls.append(dict(selection or {}))
        return {"ok": True, "submitted": True, "exit_intent_id": "x",
                "quantity": (selection or {}).get("selected_qty"),
                "limit_price": (selection or {}).get("limit_price")}


def _position(intent_id="fpi-1"):
    return {"intent_id": intent_id, "us_market_slug": "aec-slug",
            "portfolio_group_id": None, "residual_qty": 10.0,
            "filled_qty": 10.0, "event_key": "evt"}


def _selection(action="DIRECT_EXIT", qty=10.0, price=0.47):
    return {"intent_id": "fpi-1", "selected": action, "selected_qty": qty,
            "limit_price": price, "proceeds_per_contract": price,
            "expected_net_usd": -0.85, "inputs_expire_at": 1790000900.0}


async def _run_pass(monkeypatch, *, decision, selection, conn=None,
                    dispatcher=None):
    """Drive `pass_once` with the decision and selection under test.

    `decide_and_record` and the book read are substituted so the DISPATCH
    BOUNDARY is what is exercised -- the question is what reaches the adapter,
    not whether a database accepts a row.
    """
    dispatcher = dispatcher or _Dispatcher()

    async def _decide(conn_, **kw):
        if isinstance(decision, Exception):
            raise decision
        return dict(decision)

    async def _open(conn_, *, account_id, venue):
        return [_position()]

    async def _recover(conn_, *, account_id, venue_reader=None, now=None):
        return {"ok": True, "resubmitted_anything": False}

    from sportsassets import bettor_funded_book as FB

    monkeypatch.setattr(PC, "decide_and_record", _decide)
    monkeypatch.setattr(PC, "recover_reservations", _recover)
    monkeypatch.setattr(FB, "open_entry_positions", _open)

    async def _inputs(conn_, pos, *, at):
        return {"ok": True, "held_leg": None, "candidate_legs": [],
                "decision_id": "dec-1", "operation_id": "op-1",
                "hold_ranking": {"version": "T", "candidates": [],
                                 "not_rankable": []},
                "region_probabilities": None, "limits": None, "fee_usd": None,
                "depth": None, "incremental": None, "capital_duration_h": None,
                "hedge_us_market_slug": None, "hedge_quantity": None,
                "hedge_limit_price": None, "hedge_collateral_usd": None,
                "hedge_decision_record": None}

    got = await PC.pass_once(
        conn, account_id="acct", venue="PMUS", pair_inputs=_inputs,
        deferred_exits={"fpi-1": selection} if selection else {},
        exit_dispatcher=dispatcher, now=1790000000.0)
    return got, dispatcher


async def test_a_named_persistence_refusal_sends_nothing(monkeypatch):
    """CODEX'S REPRODUCTION: ok=False, action DIRECT_EXIT, a persistence
    refusal -- and the exit dispatcher was still called."""
    got, disp = await _run_pass(
        monkeypatch,
        decision={"ok": False, "action": "EXIT",
                  "refusal": "THE_DECISION_LEDGER_IS_NOT_IN_THIS_DATABASE",
                  "selected": {"action": "DIRECT_EXIT", "qty": 10.0}},
        selection=_selection())
    step = got["considered"][0]
    assert step["refusal"] == PC.R_DECISION_NOT_PERSISTED, step
    assert step["dispatched"] is None
    assert step["decision_refusal"] == (
        "THE_DECISION_LEDGER_IS_NOT_IN_THIS_DATABASE")
    # THE ACCEPTANCE BEHAVIOUR: nothing was sent, and nothing claims it was.
    assert disp.calls == []
    assert got.get("exits") in (None, [])
    assert got["opened_anything"] is False
    assert got["resubmitted_anything"] is False


async def test_a_decision_write_that_raises_sends_nothing(monkeypatch):
    """An exception is the same answer as a refusal: no durable record."""
    got, disp = await _run_pass(
        monkeypatch, decision=RuntimeError("write failed"),
        selection=_selection())
    assert got["ok"] is True, got
    step = got["considered"][0]
    # The pass must not raise out; the step names what happened.
    assert step.get("refusal"), step
    assert disp.calls == []
    assert got["opened_anything"] is False


async def test_a_persisted_decision_does_dispatch(monkeypatch):
    """THE POSITIVE CONTROL. Without it the two tests above would pass on a
    pass that dispatches nothing under any circumstances."""
    got, disp = await _run_pass(
        monkeypatch,
        decision={"ok": True, "action": "EXIT",
                  "selected": {"action": "DIRECT_EXIT", "qty": 10.0,
                               "limit_price": 0.47,
                               "proceeds_per_contract": 0.47,
                               "inputs_expire_at": 1790000900.0}},
        selection=_selection())
    step = got["considered"][0]
    assert step.get("refusal") is None, step
    assert step["dispatched"] == "EXIT"
    assert len(disp.calls) == 1
    assert disp.calls[0]["selected_qty"] == 10.0


# ═════════════════════════════════════════════════════════════════════
# 3 · THE ORDER MUST BE THE CANDIDATE THE RANKING SELECTED
# ═════════════════════════════════════════════════════════════════════

async def test_codex_reproduction_reduce_decided_exit_in_the_order(monkeypatch):
    """THE EXACT CASE: ranking selects REDUCE for 2, the deferred selection
    holds DIRECT_EXIT for 10. The ten-contract exit must not be sent, and the
    pass must not report a dispatch."""
    got, disp = await _run_pass(
        monkeypatch,
        decision={"ok": True, "action": "REDUCE",
                  "selected": {"action": "REDUCE", "qty": 2.0,
                               "limit_price": 0.47,
                               "proceeds_per_contract": 0.47,
                               "inputs_expire_at": 1790000900.0}},
        selection=_selection(action="DIRECT_EXIT", qty=10.0))
    step = got["considered"][0]
    assert step["refusal"] == PC.R_ORDER_DOES_NOT_MATCH_THE_DECISION, step
    assert step["dispatched"] is None
    assert set(step["order_binding"]["mismatched"]) >= {"action", "quantity"}
    # NOTHING WENT OUT, and no row claims a dispatch.
    assert disp.calls == []
    assert got.get("exits") in (None, [])


@pytest.mark.parametrize("field,candidate_patch,selection_patch", [
    ("quantity", {"qty": 4.0}, {"selected_qty": 10.0}),
    ("limit_price", {"limit_price": 0.60}, {"limit_price": 0.47}),
    ("proceeds_per_contract", {"proceeds_per_contract": 0.60},
     {"proceeds_per_contract": 0.47}),
    ("inputs_expire_at", {"inputs_expire_at": 1790000001.0},
     {"inputs_expire_at": 1790000900.0}),
])
async def test_each_bound_field_refuses_on_its_own(monkeypatch, field,
                                                   candidate_patch,
                                                   selection_patch):
    """One parametrisation per field, because a binding that only checked the
    action would have passed the quantity swap -- and a ten-contract order on a
    two-contract decision is the loss, not the label."""
    cand = {"action": "DIRECT_EXIT", "qty": 10.0, "limit_price": 0.47,
            "proceeds_per_contract": 0.47, "inputs_expire_at": 1790000900.0}
    cand.update(candidate_patch)
    sel = _selection()
    sel.update(selection_patch)
    got, disp = await _run_pass(
        monkeypatch,
        decision={"ok": True, "action": "EXIT", "selected": cand},
        selection=sel)
    step = got["considered"][0]
    assert step["refusal"] == PC.R_ORDER_DOES_NOT_MATCH_THE_DECISION, step
    assert field in step["order_binding"]["mismatched"], step["order_binding"]
    assert disp.calls == []


async def test_the_two_vocabularies_are_reconciled_not_papered_over(monkeypatch):
    """The ledger says EXIT and the selector says DIRECT_EXIT. Comparing raw
    strings would refuse every legitimate exit; ignoring the action would let
    the REDUCE/DIRECT_EXIT swap through. Both must hold."""
    assert PC.LEDGER_ACTION_FOR_SELECTION["DIRECT_EXIT"] == "EXIT"
    good = PC.bind_selection_to_candidate(
        selection=_selection(), action="EXIT",
        candidate={"action": "DIRECT_EXIT", "qty": 10.0, "limit_price": 0.47,
                   "proceeds_per_contract": 0.47,
                   "inputs_expire_at": 1790000900.0},
        position=_position())
    assert good["ok"] is True, good
    bad = PC.bind_selection_to_candidate(
        selection=_selection(), action="REDUCE",
        candidate={"action": "REDUCE", "qty": 10.0, "limit_price": 0.47,
                   "proceeds_per_contract": 0.47,
                   "inputs_expire_at": 1790000900.0},
        position=_position())
    assert bad["ok"] is False and "action" in bad["mismatched"]


def test_an_absent_candidate_binds_to_nothing_and_refuses():
    got = PC.bind_selection_to_candidate(
        selection=_selection(), candidate=None, action="EXIT",
        position=_position())
    assert got["ok"] is False
    assert "bound to nothing" in got["why"]


def test_the_binding_reports_which_fields_it_actually_covered():
    """A field the candidate does not carry is recorded as UNBOUND rather than
    silently treated as equal, so the report cannot overstate the check."""
    got = PC.bind_selection_to_candidate(
        selection=_selection(),
        candidate={"action": "DIRECT_EXIT", "qty": 10.0},
        action="EXIT", position=_position())
    assert got["ok"] is True
    assert got["compared"]["limit_price"]["bound"] is False
    assert got["compared"]["quantity"]["bound"] is True


def test_only_an_action_with_an_executable_plan_may_win_the_ranking():
    """THE SECOND HALF OF THE SAME RULE, found by the binding.

    `select_exit` values several actions; `manage` defers a priced, bounded
    order for exactly one. A REDUCE candidate with no plan used to win the
    ranking, persist as REDUCE, and then be refused at the venue boundary --
    the right refusal in the wrong place, after the decision was written.
    """
    assert loop.R_NO_EXECUTABLE_PLAN
    import asyncio

    sel = _selection(action="DIRECT_EXIT", qty=9.0)
    sel["ranking"] = {"version": "T", "candidates": [
        {"action": "HOLD", "qty": 9, "value_usd": -2.0,
         "expected_net_usd": -2.0, "downside_usd": -5.0,
         "incremental_capital_usd": 0.0, "capital_duration_h": 20.0,
         "evidence_quality": "EXTERNAL_LABELLED", "execution_secured": True},
        {"action": "REDUCE", "qty": 9, "value_usd": 5.0,
         "expected_net_usd": 5.0, "downside_usd": 5.0,
         "incremental_capital_usd": 0.0, "capital_duration_h": 0.0,
         "evidence_quality": "VENUE_IMPLIED", "execution_secured": False},
    ], "not_rankable": []}
    facts = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        loop.funded_pair_inputs(None, _position(), at=1790000000.0,
                                deferred={"fpi-1": sel}))
    actions = [c["action"] for c in facts["hold_ranking"]["candidates"]]
    blocked = {c.get("action"): c.get("blocker")
               for c in facts["hold_ranking"]["not_rankable"]}
    # REDUCE outscores everything and has NO plan, so it cannot win.
    assert "REDUCE" not in actions, actions
    assert blocked.get("REDUCE") == loop.R_NO_EXECUTABLE_PLAN, blocked
    assert "DIRECT_EXIT" in actions
