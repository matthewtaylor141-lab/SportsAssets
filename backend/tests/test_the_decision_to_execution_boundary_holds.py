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


def _substitute_xavier_persistence(monkeypatch):
    from sportsassets import bettor_xavier as XV

    async def _record(conn_, **kw):
        return {"ok": True, "refusal": None,
                "xavier_decision_id": "xav:harness"}

    async def _claim(conn_, **kw):
        return {"ok": True, "claimed": True, "refusal": None}

    async def _events(conn_, **kw):
        return {"ok": True, "written": False, "refusal": None}

    monkeypatch.setattr(XV, "record_decision", _record)
    monkeypatch.setattr(XV, "claim_dispatch", _claim)
    monkeypatch.setattr(XV, "record_dispatch", _events)

    # THE GROUP'S REVIEW LOCK AND ITS QUANTITY RE-READS are database reads,
    # substituted for the same reason: the lock is taken, and both re-reads
    # (under the lock, and immediately before the send) find the harness's
    # own position row unchanged.
    async def _lock(conn_, key):
        return {"ok": True, "key": key, "refusal": None}

    async def _quantities(conn_, *, intent_ids, group_id=None):
        pos = _position()
        return {"ok": True, "orders_in_flight": [],
                "legs": {pos["intent_id"]: {
                    "residual": float(pos["residual_qty"])}}}

    monkeypatch.setattr(XV, "try_group_lock", _lock)
    monkeypatch.setattr(XV, "group_quantities", _quantities)


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
    # XAVIER'S PRE-ACTION RECORD AND DISPATCH CLAIM are database writes too,
    # and every dispatch now requires both. They are substituted for the same
    # reason `decide_and_record` is: this harness has no database, and the
    # question is what reaches the adapter. (Their refusals stopping the send
    # are pinned in test_xavier_manages_every_position_through_the_scheduled_
    # pass.py against a real database.)
    _substitute_xavier_persistence(monkeypatch)

    async def _inputs(conn_, pos, *, at):
        # THE SUPPLIER'S PRODUCTION CONTRACT, including the executable plans.
        # An action is rankable only if a complete plan for it validated, so a
        # harness that omitted them would exercise a path production cannot
        # reach -- and the positive control below would pass on a dispatch that
        # was never bound to anything.
        plans = {}
        if selection:
            try:
                pl = PC.plan_for(action=str(selection.get("selected")),
                                 selection=selection, account_id="acct",
                                 venue="PMUS", position=pos)
                plans[pl.action] = pl
            except PC.PlanRefused:
                plans = {}
        return {"ok": True, "held_leg": None, "candidate_legs": [],
                "decision_id": "dec-1", "operation_id": "op-1",
                "executable_plans_by_action": plans,
                "executable_plans": {a: pl.as_dict()
                                     for a, pl in plans.items()},
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
    pass that dispatches nothing under any circumstances.

    The candidate carries its plan's DIGEST, exactly as the supplier builds it:
    a candidate without one is not bindable and is refused, which is asserted
    separately.
    """
    sel = _selection()
    pl = PC.plan_for(action="DIRECT_EXIT", selection=sel, account_id="acct",
                     venue="PMUS", position=_position())
    # THE ONE-MEASURE GATE `decide_and_record` NOW RETURNS (880377f), which
    # this harness substitutes -- so the substituted decision carries the
    # verdict the REAL `common_valuation_for` / `common_valuation_gate`
    # compute on stated inputs (SYNTHETIC): HOLD at p = 0.30 on a 0.60 basis,
    # the full exit's 4.70 of proceeds at 0.47, the held contract's own
    # terms (void pays 50c) and a measured void rate whose upper bound is
    # 0.11. HOLD is worth at most 0.89 x 0.30 + 0.11 x 0.50 = 0.32 a
    # contract, so the exit wins at both ends and may be dispatched.
    from tests import held_contract_terms as HCT
    hold_ranking = {"candidates": [
        {"action": "HOLD", "qty": 10.0, "value_per_contract": 0.30,
         "value_usd": -3.0, "basis_per_contract_valued": 0.60},
        {"action": "DIRECT_EXIT", "qty": 10.0, "value_usd": -1.30,
         "cash_now_usd": 4.70, "limit_price": 0.47}]}
    cv = PC.common_valuation_for(
        hold_ranking, [], held_leg=HCT.held_leg(),
        sport_permits_tie=HCT.SPORT_PERMITS_TIE,
        void_read={"ok": True, "rate": 0.03, "upper_95": 0.11})
    gate = PC.common_valuation_gate(
        {"selected": "DIRECT_EXIT",
         "selected_candidate": {"action": "DIRECT_EXIT", "qty": 10.0}}, cv)
    assert gate["permitted"] is True, (gate, cv)
    got, disp = await _run_pass(
        monkeypatch,
        decision={"ok": True, "action": "EXIT",
                  "selected": {"action": "DIRECT_EXIT",
                               "plan_digest": pl.digest},
                  "funded_dispatch_gate": gate},
        selection=sel)
    step = got["considered"][0]
    assert step.get("refusal") is None, step
    assert step["dispatched"] == "EXIT"
    assert len(disp.calls) == 1
    assert disp.calls[0]["selected_qty"] == 10.0


# ═════════════════════════════════════════════════════════════════════
# 3 · THE ORDER MUST BE THE PLAN THE RANKING WAS GIVEN
# ═════════════════════════════════════════════════════════════════════
#
# WHAT REPLACED THE FIELD-BY-FIELD BINDING, AND WHY IT HAD TO GO. Codex, on
# 05fa15f, showed `bind_selection_to_candidate` returning ok=True when the
# candidate lacked `limit_price`, `proceeds_per_contract` or `inputs_expire_at`
# -- it recorded them "unbound" and proceeded. A malformed number became None
# and compared equal to another None. The candidate's own `us_market_slug` was
# never read, so a candidate for market B passed against an order for market A.
# Position and intent identity were not checked at all.
#
# Every one of those is the same root error: comparing two partial records and
# treating an absence as agreement. A comparison cannot establish completeness.
#
# So a complete, immutable `ExecutionPlan` is built per action BEFORE ranking,
# an action with no valid plan is not rankable, the candidate carries its plan's
# digest, and binding is an identity check on the object that was ranked.

NOW = 1790000000.0


def _sel(action="DIRECT_EXIT", qty=10.0, price=0.47, **kw):
    out = {"intent_id": "fpi-1", "selected": action, "selected_qty": qty,
           "limit_price": price, "proceeds_per_contract": price - 0.005,
           "expected_net_usd": -0.85, "inputs_expire_at": NOW + 300.0}
    out.update(kw)
    return out


def _plan(action="DIRECT_EXIT", **kw):
    return PC.plan_for(action=action, selection=_sel(action=action, **kw),
                       account_id="acct", venue="PMUS", position=_position())


# ── the plan refuses at CONSTRUCTION, before a candidate can exist ────

@pytest.mark.parametrize("field,patch", [
    ("limit_price", {"limit_price": None}),
    ("proceeds_per_contract", {"proceeds_per_contract": None}),
    ("inputs_expire_at", {"inputs_expire_at": None}),
    ("quantity", {"selected_qty": None}),
])
def test_a_missing_required_field_refuses_the_plan(field, patch):
    """CODEX'S FIRST CASE. These three previously passed the binding as
    "unbound" and an order with no wire limit was dispatchable."""
    sel = _sel()
    sel.update(patch)
    with pytest.raises(PC.PlanRefused) as e:
        PC.plan_for(action="DIRECT_EXIT", selection=sel, account_id="acct",
                    venue="PMUS", position=_position())
    assert e.value.refusal == PC.R_PLAN_INCOMPLETE
    assert e.value.field == field


@pytest.mark.parametrize("patch", [
    {"limit_price": "0.4x"}, {"selected_qty": 0}, {"selected_qty": -3},
    {"limit_price": float("inf")}, {"proceeds_per_contract": "none"},
])
def test_a_malformed_or_non_positive_field_refuses_the_plan(patch):
    """`_num` turned garbage into None and None compared equal to None, so
    "0.4x" and a missing field agreed with each other."""
    sel = _sel()
    sel.update(patch)
    with pytest.raises(PC.PlanRefused) as e:
        PC.plan_for(action="DIRECT_EXIT", selection=sel, account_id="acct",
                    venue="PMUS", position=_position())
    assert e.value.refusal == PC.R_PLAN_MALFORMED


def test_a_selection_naming_another_instrument_refuses():
    """CODEX'S SECOND CASE: a candidate for market B against an order for
    market A. The position is the authority on what is held; a selection naming
    another market is a different order, not a correction."""
    with pytest.raises(PC.PlanRefused) as e:
        PC.plan_for(action="DIRECT_EXIT",
                    selection=_sel(us_market_slug="market-B"),
                    account_id="acct", venue="PMUS", position=_position())
    assert e.value.refusal == PC.R_PLAN_IDENTITY
    assert e.value.field == "us_market_slug"


def test_a_selection_naming_another_position_refuses():
    """Position/intent identity was not checked at all."""
    with pytest.raises(PC.PlanRefused) as e:
        PC.plan_for(action="DIRECT_EXIT", selection=_sel(intent_id="fpi-OTHER"),
                    account_id="acct", venue="PMUS", position=_position())
    assert e.value.refusal == PC.R_PLAN_IDENTITY
    assert e.value.field == "intent_id"


def test_the_plan_carries_the_whole_identity_and_cannot_be_edited():
    pl = _plan()
    for f in PC.PLAN_REQUIRED:
        assert getattr(pl, f) not in (None, ""), f
    assert pl.account_id == "acct" and pl.venue == "PMUS"
    assert pl.intent_id == "fpi-1" and pl.us_market_slug == "aec-slug"
    # IMMUTABLE: a plan editable between the ranking and the send is not a
    # binding, and its digest would stop describing what is about to go out.
    with pytest.raises(AttributeError):
        pl.quantity = 99


# ── and binding is an identity check, not a reconciliation ────────────

def test_a_substituted_plan_does_not_bind():
    """A DIFFERENT plan, perfectly well-formed, for the same position and
    action at a different size. Field-by-field comparison would have had to
    catch it field by field; a digest cannot be talked round."""
    ranked = _plan()
    substituted = _plan(qty=4.0)
    cand = {"action": "DIRECT_EXIT", "plan_digest": ranked.digest}
    got = PC.bind_plan_to_decision(plan=substituted, candidate=cand,
                                   action="EXIT", now=NOW)
    assert got["ok"] is False
    assert got["refusal"] == PC.R_PLAN_NOT_THE_RANKED_ONE
    assert ranked.digest != substituted.digest


def test_a_candidate_with_no_plan_digest_binds_to_nothing():
    got = PC.bind_plan_to_decision(plan=_plan(),
                                   candidate={"action": "DIRECT_EXIT"},
                                   action="EXIT", now=NOW)
    assert got["ok"] is False
    assert "carries no plan digest" in got["why"]


def test_no_plan_at_all_refuses_as_incomplete():
    got = PC.bind_plan_to_decision(plan=None, candidate={"plan_digest": "x"},
                                   action="EXIT", now=NOW)
    assert got["ok"] is False
    assert got["refusal"] == PC.R_PLAN_INCOMPLETE


def test_expired_evidence_refuses_on_the_clock_not_on_agreement():
    """Two records agreeing about an expiry instant does not establish that the
    instant has not passed. The clock is asked separately."""
    pl = _plan()
    cand = {"action": "DIRECT_EXIT", "plan_digest": pl.digest}
    ok = PC.bind_plan_to_decision(plan=pl, candidate=cand, action="EXIT",
                                  now=NOW)
    assert ok["ok"] is True, ok
    # SAME plan, SAME digest, SAME agreement -- one second past expiry.
    late = PC.bind_plan_to_decision(plan=pl, candidate=cand, action="EXIT",
                                    now=pl.inputs_expire_at + 1.0)
    assert late["ok"] is False
    assert late["refusal"] == PC.R_PLAN_EVIDENCE_EXPIRED
    assert late["expiry"]["expired"] is True


def test_the_positive_control_binds():
    pl = _plan()
    got = PC.bind_plan_to_decision(
        plan=pl, candidate={"action": "DIRECT_EXIT", "plan_digest": pl.digest},
        action="EXIT", now=NOW)
    assert got["ok"] is True and got["refusal"] is None
    assert got["expiry"]["remaining_s"] > 0


def test_reduce_and_exit_get_separate_plans_with_separate_digests():
    """Codex: keep separate executable plans for EXIT and REDUCE when both are
    candidates. Two actions, two plans, two digests -- and one cannot be bound
    to the other's candidate."""
    ex = _plan(action="DIRECT_EXIT", qty=10.0)
    rd = _plan(action="REDUCE", qty=2.0)
    assert ex.digest != rd.digest
    crossed = PC.bind_plan_to_decision(
        plan=ex, candidate={"action": "REDUCE", "plan_digest": rd.digest},
        action="REDUCE", now=NOW)
    assert crossed["ok"] is False
    assert crossed["refusal"] == PC.R_PLAN_NOT_THE_RANKED_ONE
    # EACH BINDS TO ITS OWN.
    for pl, ledger in ((ex, "EXIT"), (rd, "REDUCE")):
        own = PC.bind_plan_to_decision(
            plan=pl, candidate={"action": pl.action, "plan_digest": pl.digest},
            action=ledger, now=NOW)
        assert own["ok"] is True, (pl.action, own)


# ═════════════════════════════════════════════════════════════════════
# 4 · TEAM IDENTITY BEYOND THE MANCHESTER COUNTEREXAMPLE
# ═════════════════════════════════════════════════════════════════════
#
# Fixing the shared-city case with a one-to-one assignment left unrestricted
# token CONTAINMENT in place, and Codex showed what containment still accepted:
#
#     Arsenal vs Chelsea  ->  Arsenal Women vs Chelsea Women
#     Arsenal vs Chelsea  ->  Arsenal U21 vs Chelsea U21
#
# {arsenal} is contained in {arsenal, women}, so each side matched and the
# assignment was one-to-one: a correct assignment between the WRONG teams. A
# men's first team and a women's team are different competitions, and pricing
# one against the other is the same class of error as the eBattles simulation.

@pytest.mark.parametrize("venue_title", [
    "Arsenal Women vs Chelsea Women",
    "Arsenal Ladies vs Chelsea Ladies",
    "Arsenal U21 vs Chelsea U21",
    "Arsenal U23 vs Chelsea U23",
    "Arsenal II vs Chelsea II",
    "Arsenal Reserves vs Chelsea Reserves",
    "Arsenal Academy vs Chelsea Academy",
])
def test_a_squad_qualifier_on_one_side_only_is_a_different_team(venue_title):
    got = loop.confirm_mapping_by_fixtures(
        provider_events=[{"home_team": "Arsenal", "away_team": "Chelsea"}],
        venue_event_titles=[venue_title])
    assert got["ok"] is False, (venue_title, got)
    assert got["strong_matches"] == 0


@pytest.mark.parametrize("provider,venue_title", [
    ({"home_team": "Arsenal Women", "away_team": "Chelsea Women"},
     "Arsenal Women vs Chelsea Women"),
    ({"home_team": "Arsenal U21", "away_team": "Chelsea U21"},
     "Arsenal U21 vs Chelsea U21"),
    ({"home_team": "Arsenal FC", "away_team": "Chelsea FC"},
     "Arsenal vs Chelsea"),
])
def test_matching_qualifiers_on_both_sides_still_confirm(provider, venue_title):
    """THE POSITIVE CONTROL. The rule must not become "no women's football":
    a women's fixture against the same women's fixture is the same fixture."""
    got = loop.confirm_mapping_by_fixtures(
        provider_events=[provider], venue_event_titles=[venue_title])
    assert got["ok"] is True, (provider, venue_title, got)


def test_a_qualifier_is_not_confused_with_an_affiliation_marker():
    """`FC` is a rendering difference and is dropped; `Women` is a different
    team and is not. Both are single trailing tokens, so the distinction has to
    be in the lists rather than in the shape of the name."""
    assert "fc" in loop.AFFILIATION_MARKERS
    assert "women" in loop.SQUAD_QUALIFIERS
    assert not (loop.AFFILIATION_MARKERS & loop.SQUAD_QUALIFIERS)


def test_the_scheduled_board_carries_fixture_dates_to_the_confirmation():
    """CODEX'S SECOND IDENTITY POINT. The scheduled caller never supplied
    `venue_event_days`, so the date comparison was exercised only by tests --
    a path production did not use. The board query now reads `game_start` per
    title and the candidate carries it."""
    assert "title_days" in loop.VENUE_SOCCER_BOARD_SQL
    assert "game_start" in loop.VENUE_SOCCER_BOARD_SQL
    cands = loop.candidates_from_board(
        [("mls", 3)], {"mls": ["A vs B"]}, {"mls": {"A vs B": "2026-09-26"}})
    assert cands[0]["venue_title_days"] == {"A vs B": "2026-09-26"}
    # AND THE CYCLE PASSES THEM to the confirmation.
    import inspect
    src = inspect.getsource(loop.cycle)
    assert "venue_event_days=_cand.get(\"venue_title_days\")" in src


def test_missing_dating_cannot_establish_the_same_meeting():
    """Codex: missing dating may support tentative discovery but cannot
    establish that two records describe the same meeting. A confirmation
    without dates says so on the result."""
    got = loop.confirm_mapping_by_fixtures(
        provider_events=[{"home_team": "Columbus Crew",
                          "away_team": "Inter Miami"}],
        venue_event_titles=["Columbus Crew vs. Inter Miami"])
    assert got["ok"] is True
    assert got["dating_incomplete"] is True
    assert "not excluded" in got["why"]
