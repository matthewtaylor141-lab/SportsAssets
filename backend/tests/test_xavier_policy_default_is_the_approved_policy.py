"""XAVIER_MANAGEMENT_POLICY_V1: THE DEFAULT IS THE APPROVED POLICY, UNCHANGED.

Pinned here:
  * PARITY. With the default parameters the policy's choice is IDENTICAL to
    `bettor_funded_decision.decide` on the same inputs -- the selected action,
    the selected candidate row and the ranked order -- over a stated grid of
    cases (ties on expected value, unknown worst cases, limit breaches, an
    unpriced HOLD, several indirect candidates). No silent policy change.
  * THE CAPITAL-PRESERVATION LEG IS DISABLED BY DEFAULT and, when set above
    zero, prefers a better known worst case only within that expected-value
    sacrifice, naming the parameter and the consequence.
  * NOT A PARAMETER: risk limits, credentials and approvals are refused by
    name; only the approved selection rule is accepted.
  * STORAGE: loaded from `agent_policy_versions` when an ACTIVE row
    validates, else the code default labelled CODE_DEFAULT (table absent,
    no row, or a row that does not validate).

Controlled inputs only; the database test creates its rows inside a
transaction it rolls back.
"""
from __future__ import annotations

import itertools
import json
import os

import pytest

from sportsassets import bettor_funded_decision as FD
from sportsassets.agents import xavier_policy as XP

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")
ACQ = FD.ACTION_ACQUIRE_INDIRECT_HEDGE


def _hold_ranking(hold, exit_, reduce_, *, hold_priced=True):
    cands = []
    if hold_priced:
        cands.append({"action": "HOLD", "qty": 10.0, "value_usd": hold,
                      "worst_case_net_usd": -5.0,
                      "value_per_contract": 0.55})
    nr = [] if hold_priced else [{"action": "HOLD", "blocker": "NOT_PRICED",
                                  "value_usd": None}]
    if exit_ is not None:
        cands.append({"action": "DIRECT_EXIT", "qty": 10.0,
                      "value_usd": exit_[0], "worst_case_net_usd": exit_[1],
                      "plan_digest": "pd-exit"})
    if reduce_ is not None:
        cands.append({"action": "REDUCE", "qty": 7.0,
                      "value_usd": reduce_[0],
                      "worst_case_net_usd": reduce_[1],
                      "plan_digest": "pd-reduce"})
    return {"version": "TEST", "candidates": cands, "not_rankable": nr}


def _acq(i, value, downside, capital):
    return {"action": ACQ, "rankable": True, "candidate_id": "cand-%d" % i,
            "plan_digest": "pd-acq-%d" % i, "value_usd": value,
            "expected_net_usd": value, "downside_usd": downside,
            "incremental_capital_usd": capital, "units": 10}


#: A STATED GRID (stable ids from the index, no randomness, no clock).
HOLDS = (0.5, 1.0)
EXITS = (None, (0.5, 0.2), (0.9, -1.0))
REDUCES = (None, (1.0, -2.0), (0.5, -0.5))
ACQS = (
    (),
    ((1.2, -0.3, 5.0),),
    ((1.0, -0.2, 5.0), (1.0, -0.2, 4.0)),        # EV tie, worst tie -> capital
    ((1.0, None, 5.0), (1.0, -3.0, 5.0)),         # unknown worst sorts last
    ((0.2, -9.0, 5.0), (3.0, -12.0, 9.0)),        # breaches the downside limit
)
LIMITS = (None, {"max_downside_usd": 10.0},
          {"max_downside_usd": 10.0, "max_incremental_capital_usd": 6.0})


def _grid():
    for n, (h, e, r, a, lim, priced) in enumerate(itertools.product(
            HOLDS, EXITS, REDUCES, ACQS, LIMITS, (True, False))):
        yield n, dict(hold_ranking=_hold_ranking(h, e, r, hold_priced=priced),
                      indirect_candidates=[_acq(i, *x)
                                           for i, x in enumerate(a)],
                      limits=lim)


def _ident(c):
    c = dict(c or {})
    return (c.get("action"), c.get("candidate_id"), c.get("plan_digest"),
            c.get("value_usd"))


def test_default_policy_selects_exactly_what_decide_selects():
    default = XP.code_default()
    assert default["params"]["selection_rule"] == XP.SEL_EXPECTED_NET_VALUE
    assert default["params"]["max_ev_sacrifice_for_downside_usd"] == 0.0
    n_cases = n_selected = 0
    for n, kw in _grid():
        base = FD.decide(**json.loads(json.dumps(kw)))
        mine = XP.decide(policy=default, **json.loads(json.dumps(kw)))
        ran = XP.run(default, **json.loads(json.dumps(kw)))
        assert ran["decision_policy"]["decision_function"] == \
            "bettor_funded_decision.decide", n
        assert ran["selected"] == base["selected"], n
        assert _ident(ran.get("selected_candidate")) == \
            _ident(base.get("selected_candidate")), n
        assert mine["selected"] == base["selected"], n
        assert _ident(mine.get("selected_candidate")) == \
            _ident(base.get("selected_candidate")), n
        assert [_ident(c) for c in mine["candidates"]] == \
            [_ident(c) for c in base["candidates"]], n
        assert mine.get("refusal") == base.get("refusal"), n
        assert mine["xavier_policy"]["identical_to_approved_ev_policy"] \
            is True, n
        rec = XP.record(base, default)
        assert rec["identical_to_approved_ev_policy"] is True
        assert rec["applied_to_dispatch"] is True
        n_cases += 1
        n_selected += base["selected"] is not None
    # the grid exercised selections and refusals both
    assert n_cases == 2 * 3 * 3 * 5 * 3 * 2
    assert 0 < n_selected < n_cases


def test_the_capital_preservation_leg_is_disabled_by_default_and_named_when_set():
    kw = dict(hold_ranking=_hold_ranking(0.5, (0.5, 0.2), None),
              indirect_candidates=[_acq(0, 2.0, -3.0, 5.0),
                                   _acq(1, 1.4, -0.5, 5.0),
                                   _acq(2, 0.9, 0.1, 5.0)])
    base = FD.decide(**kw)
    assert base["selected_candidate"]["candidate_id"] == "cand-0"
    # DEFAULT: the EV winner stands
    got = XP.apply(base, XP.code_default())
    assert got["identical_to_approved_ev_policy"] is True
    assert got["capital_preservation"]["enabled"] is False
    # SET TO 1.00: cand-1 (EV 1.4, worst -0.5) is within the sacrifice and
    # better in the worst case; cand-2 (EV 0.9) is outside it
    pol = dict(XP.code_default(), params=dict(
        XP.default_params(), selection_rule=XP.SEL_CAPITAL_PRESERVATION,
        max_ev_sacrifice_for_downside_usd=1.0))
    got = XP.apply(base, pol)
    assert got["identical_to_approved_ev_policy"] is False
    assert got["selected_candidate"]["candidate_id"] == "cand-1"
    cons = got["capital_preservation"]["consequence"]
    assert got["capital_preservation"]["parameter"] == \
        "max_ev_sacrifice_for_downside_usd"
    assert got["capital_preservation"]["value"] == 1.0
    assert cons["expected_value_given_up_usd"] == pytest.approx(0.6)
    assert cons["worst_case_gained_usd"] == pytest.approx(2.5)
    # THE REAL SELECTOR RUNS THE CANDIDATE'S DECISION FUNCTION, and its one
    # winner is what the record says proceeds to dispatch
    ran = XP.run(pol, **kw)
    assert ran["decision_policy"]["decision_function"] == \
        "agents.xavier_policy.decide"
    assert ran["selected_candidate"]["candidate_id"] == "cand-1"
    assert "CAPITAL_PRESERVATION_V1" in ran["selection_reason"]
    rec = XP.record(ran, pol)
    assert rec["applied_to_dispatch"] is True
    assert rec["identical_to_approved_ev_policy"] is False
    assert rec["policy_selected_candidate"][1] == "cand-1"
    assert rec["approved_ev_selected_candidate"][1] == "cand-0"
    # THE SHADOW: the approved policy on the same frozen inputs
    sh = XP.shadow_comparison(ran, XP.code_default(), **kw)
    assert sh["dispatched"] is False and sh["is"] == \
        "DISPLAYED_NEVER_DISPATCHED"
    assert sh["shadow_selected"]["selected_candidate"][1] == "cand-0"
    assert sh["ev_given_up_by_capital_preservation_usd"] == pytest.approx(0.6)
    assert sh["downside_improved_by_capital_preservation_usd"] == \
        pytest.approx(2.5)
    # and the other way round: the default active, the candidate shadowed
    base_ran = XP.run(XP.code_default(), **kw)
    sh2 = XP.shadow_comparison(base_ran, pol, **kw)
    assert sh2["active_selected"]["selected_candidate"][1] == "cand-0"
    assert sh2["shadow_selected"]["selected_candidate"][1] == "cand-1"
    assert sh2["ev_given_up_by_capital_preservation_usd"] == \
        pytest.approx(0.6)
    # SET, BUT NOTHING QUALIFIES within 0.10: the winner stands, and says so
    pol2 = dict(pol, params=dict(pol["params"],
                                 max_ev_sacrifice_for_downside_usd=0.1))
    assert XP.run(pol2, **kw)["selected_candidate"]["candidate_id"] == \
        "cand-0"
    got = XP.apply(base, pol2)
    assert got["identical_to_approved_ev_policy"] is True
    assert "NOTHING QUALIFIED" in got["capital_preservation"]["consequence"]


def test_risk_limits_credentials_and_approvals_are_not_policy_parameters():
    for k in ("max_downside_usd", "max_incremental_capital_usd",
              "credentials", "approved_by", "FUNDED_SUBMISSION_ENABLED",
              "daily_loss_stop_usd"):
        got = XP.validate({k: 1})
        assert got["ok"] is False and got["refusal"] == XP.R_FORBIDDEN_KEY
    assert XP.validate({"minimax": 1})["refusal"] == XP.R_UNKNOWN_KEY
    assert XP.validate({"selection_rule": "WORST_CASE"})["refusal"] == \
        XP.R_UNSUPPORTED_RULE
    assert XP.validate({"max_ev_sacrifice_for_downside_usd": -1})[
        "refusal"] == XP.R_BAD_VALUE
    # the rule and the sacrifice must agree: EV takes none, CP needs one
    assert XP.validate({"max_ev_sacrifice_for_downside_usd": 0.5})[
        "refusal"] == XP.R_RULE_NEEDS_SACRIFICE
    assert XP.validate({"selection_rule": XP.SEL_CAPITAL_PRESERVATION})[
        "refusal"] == XP.R_RULE_NEEDS_SACRIFICE
    assert XP.validate({"requires_complete_comparison": "yes"})[
        "refusal"] == XP.R_BAD_VALUE
    ok = XP.validate({})
    assert ok["ok"] is True and ok["params"] == XP.default_params()
    # the search preferences are documented as preferences, never permissions
    docs = XP.XAVIER_MANAGEMENT_POLICY_V1["parameter_docs"]
    assert "NOT A PERMISSION" in docs["preferred_max_combined_pair_cost"]
    assert "NOT A PERMISSION" in docs["example_hedge_price_preference"]
    view = XP.search_preference_view(combined_cost_per_unit=1.10,
                                     hedge_price=0.60)
    assert view["is_a_permission"] is False
    assert view["within_combined_cost_preference"] is False
    assert view["within_hedge_price_preference"] is True


class _NoTableConn:
    """A connection whose catalogue has no agent_policy_versions."""

    async def fetchval(self, sql, *a):
        assert "to_regclass" in sql
        return None


@pytest.mark.asyncio
async def test_the_code_default_is_used_and_labelled_when_the_table_is_absent():
    got = await XP.load(_NoTableConn())
    assert got["source"] == XP.SOURCE_CODE_DEFAULT
    assert got["params"] == XP.default_params()
    assert got["version"] == XP.VERSION


@pg
@pytest.mark.asyncio
async def test_an_active_stored_version_is_loaded_and_an_invalid_one_is_refused():
    import asyncpg
    conn = await asyncpg.connect(DSN)
    try:
        tr = conn.transaction()
        await tr.start()
        try:
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS agent_policy_versions ("
                " agent_id text, policy_key text, version text, params jsonb,"
                " state text, created_by text, approved_by text,"
                " approved_at timestamptz, created_at timestamptz DEFAULT "
                " now(), PRIMARY KEY (agent_id, policy_key, version))")
            await conn.execute(
                "DELETE FROM agent_policy_versions WHERE agent_id='XAVIER' "
                "  AND policy_key=$1", XP.POLICY_KEY)
            await conn.execute(
                "INSERT INTO agent_policy_versions (agent_id, policy_key, "
                " version, params, state, created_by, approved_by, "
                " approved_at) VALUES ('XAVIER',$1,'V1-test',$2::jsonb,"
                " 'ACTIVE','test','owner (SYNTHETIC)',now())",
                XP.POLICY_KEY, json.dumps(
                    {"selection_rule": XP.SEL_CAPITAL_PRESERVATION,
                     "max_ev_sacrifice_for_downside_usd": 0.25}))
            got = await XP.load(conn)
            assert got["source"] == XP.SOURCE_TABLE, got
            assert got["version"] == "V1-test"
            assert got["params"]["max_ev_sacrifice_for_downside_usd"] == 0.25
            assert got["params"]["selection_rule"] == \
                XP.SEL_CAPITAL_PRESERVATION
            # A STORED ROW CARRYING A RISK LIMIT DOES NOT VALIDATE
            await conn.execute(
                "UPDATE agent_policy_versions SET params=$2::jsonb "
                " WHERE agent_id='XAVIER' AND policy_key=$1",
                XP.POLICY_KEY, json.dumps({"max_downside_usd": 1000}))
            got = await XP.load(conn)
            assert got["source"] == XP.SOURCE_CODE_DEFAULT
            assert got["why"] == XP.R_INVALID_STORED
            assert got["rejected_because"]["refusal"] == XP.R_FORBIDDEN_KEY
            assert got["params"] == XP.default_params()
            # NO ACTIVE ROW
            await conn.execute(
                "UPDATE agent_policy_versions SET state='RETIRED' "
                " WHERE agent_id='XAVIER' AND policy_key=$1", XP.POLICY_KEY)
            got = await XP.load(conn)
            assert got["source"] == XP.SOURCE_CODE_DEFAULT
        finally:
            await tr.rollback()
    finally:
        await conn.close()
