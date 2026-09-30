"""A MANAGEMENT DIRECTIVE BECOMES AN EVALUATED, ARTIFACT-BOUND XAVIER CANDIDATE.

The chain, through the production paths (mirroring what
test_a_directive_becomes_an_evaluated_artifact proves for Derek):

  1. RECORDED DECISIONS FROM THE REAL SCHEDULED PATH. One review pass per fixture of
     `ext_pinnacle_loop._funded_service` (manage -> the production pair-input
     builder -> `pass_once` -> `decide_and_record` -> Xavier's record) over a
     DEMONSTRATION account, with ONLY the venue transport substituted (the
     harness of test_xavier_manages_positions_through_the_scheduled_path).
     Each position: 10 Red Sox moneyline held at 0.50, HOLD valued on p=0.55
     (+0.50, worst -5.00), the Yankees +1.5 run line at 0.62 (expected value
     about $0.32 BELOW HOLD, worst about -$1.36). The approved
     EXPECTED_NET_VALUE policy HOLDs every one. Each record now persists the
     selector's frozen inputs (`reasoning.decision_inputs`).
  2. SETTLEMENT. Every held leg is settled through the production
     `reconcile_settlement` (probe substituted); the run line -- never held --
     is settled through a labelled pair observation (the production recorder
     and labeller, the settlement read substituted).
  3. AUTHENTICATED DIRECTIVE. "Cut losses on held positions without
     increasing capital limits." through Audrey's chat route with the
     operator session -> LOSS_REDUCTION, with Derek's and Xavier's tasks.
  4. XAVIER'S WORK. `improvement.run_due` has Xavier take his task up: an
     IMPROVEMENT task on XAVIER_CAPITAL_PRESERVATION_TRADEOFF with MORE-
     PROTECTION-ONLY variants (0.25, 0.50, 1.00 USD). The evaluator re-runs
     Xavier's actual decision function on every record's frozen inputs,
     selects on TRAINING only (0.25 changes no action and fails as
     IDENTICAL_SELECTION), replays the holdout ONCE and stops at
     APPROVAL_READY with its binding and RETROSPECTIVE qualification.
  5. ARTIFACT. `tools/improvement_sandbox.py --candidate-id` commits the
     one-line constant change of xavier_policy.MAX_EV_SACRIFICE_FOR_DOWNSIDE_
     USD with passing tests and evaluation.json, attached to the SAME
     candidate. A changed input record voids the approval basis; restored,
     a person approves; live promotion stays refused (economic
     qualification pending).

EVIDENCE LABELS. Every book, price, probability and settlement here is
SYNTHETIC. The settlements form a REHEARSAL scenario chosen so that a
different tradeoff genuinely settles better with no deeper drawdown: over
each 11-fixture window the held side lost 6 (priced at 45%), won by exactly
one run 2 and by two or more 3. The evaluation labels itself REHEARSAL
(demonstration books): it establishes the machinery, NOTHING about
opportunity. A mix where the held side mostly wins by two or more is shown
to FAIL in the pure tests below.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
import time

import pytest

from sportsassets import bettor_funded_decision as FD
from sportsassets.agents import improvement as IMP
from sportsassets.agents import xavier_policy as XP
from sportsassets.agents import xavier_replay as XR
from tests import _audrey_chat_fixture as F

BACKEND = pathlib.Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
TOOL = BACKEND / "tools" / "improvement_sandbox.py"
DAY = 86400.0
CLS = "XAVIER_CAPITAL_PRESERVATION_TRADEOFF"
P = "max_ev_sacrifice_for_downside_usd"
ACQ = FD.ACTION_ACQUIRE_INDIRECT_HEDGE
LONG, SHORT = XR.LONG, XR.SHORT
TEXT = "Cut losses on held positions without increasing capital limits."
PAIRING = "Improve how we pair hedges on held positions."
REHEARSAL = ("SYNTHETIC REHEARSAL -- chosen settlements, demonstration "
             "books; NOT evidence about any market")


# ════════════════════════════════════════════════════════════════════
# 0 · PURE (no database; the sandbox runs the first one on its artifact)
# ════════════════════════════════════════════════════════════════════

def test_the_versioned_default_is_a_whole_valid_policy_the_selector_reads():
    """The constant the sandbox edits IS the selector's parameter, and
    whatever value it carries the default is a whole, valid policy (the rule
    follows the sacrifice)."""
    cls = IMP.CHANGE_CLASSES[CLS]
    assert cls.code_default == ("backend/sportsassets/agents/xavier_policy.py",
                                "MAX_EV_SACRIFICE_FOR_DOWNSIDE_USD")
    s = XP.MAX_EV_SACRIFICE_FOR_DOWNSIDE_USD
    d = XP.default_params()
    assert d[P] == float(s)
    assert XP.validate(d)["ok"] is True
    assert d["selection_rule"] == (XP.SEL_CAPITAL_PRESERVATION if s > 0
                                   else XP.SEL_EXPECTED_NET_VALUE)
    assert XP.identity(XP.code_default())["params"][P] == float(s)
    assert IMP.code_default_params(cls) == {P: float(s)}
    lo, hi, integer = cls.bounds[P]
    assert (lo, hi, integer) == (0.0, 1.0, False) and lo <= s <= hi


def test_the_class_is_bounded_protected_and_stops_for_a_person():
    cls = IMP.CHANGE_CLASSES[CLS]
    assert cls.agent == IMP.XAVIER and cls.kind == IMP.K_POLICY
    assert cls.policy_key == "XAVIER_MANAGEMENT_POLICY" == XP.POLICY_KEY
    assert cls.pre_authorized is False
    assert cls.evaluator == IMP.XAVIER_EVALUATOR in IMP.REPLAY_EVALUATORS
    assert IMP.DIRECTIVE_WORK[IMP.XAVIER] == CLS
    assert IMP.describe_registry()["classes"][CLS]["unattended_release"] == \
        "NEVER: STOPS_AT_APPROVAL_READY"
    assert cls.success_metrics["min_fixtures"] == 10
    assert IMP.check_params(cls, {P: 0.5})["ok"] is True
    assert IMP.check_params(cls, {P: 1.5})["refusal"] == IMP.R_OUT_OF_BOUNDS
    assert IMP.check_params(cls, {P: -0.1})["refusal"] == IMP.R_OUT_OF_BOUNDS
    # PROTECTED KEYS ARE REFUSED BY NAME (risk and capital limits,
    # credentials, authority, submission switches)
    for k in ("max_exposure_usd", "risk_limits", "credentials",
              "account_authority", "FUNDED_SUBMISSION_ENABLED",
              "approved_by", "daily_loss_limit"):
        got = IMP.check_params(cls, {k: 1})
        assert got["refusal"] == IMP.R_PROTECTED_KEY, (k, got)
    # a limit the policy module forbids is not a parameter of the class, and
    # no policy version may carry it
    assert IMP.check_params(cls, {"max_downside_usd": 5})["refusal"] == \
        IMP.R_UNKNOWN_PARAM
    assert XP.validate({"max_downside_usd": 5})["refusal"] == \
        XP.R_FORBIDDEN_KEY
    # the sandbox refuses a diff that touches execution, limits or the
    # evaluator itself
    sys.path.insert(0, str(BACKEND / "tools"))
    try:
        import improvement_sandbox as SB
    finally:
        sys.path.pop(0)
    for path in ("backend/sportsassets/bettor_funded_execution.py",
                 "backend/sportsassets/agents/xavier_replay.py",
                 "backend/sportsassets/agents/improvement.py"):
        with pytest.raises(SB.Refused) as e:
            SB.check_diff("--- a/%s\n+++ b/%s\n@@\n-x\n+y\n" % (path, path))
        assert e.value.refusal == SB.R_PROTECTED_PATH
    with pytest.raises(SB.Refused) as e:
        SB.check_diff("--- a/x.py\n+++ b/x.py\n@@\n+max_exposure_usd = 9\n")
    assert e.value.refusal == SB.R_PROTECTED_KEY_IN_DIFF


# ── SYNTHETIC frozen inputs shaped like the scheduled path's ─────────
PAIR_CID = "asc-synthetic-neg-1pt5#" + SHORT
HOLD_K = ("HOLD", "HOLD", "")
EXIT_K = ("DIRECT_EXIT", "DIRECT_EXIT", "pd-exit")
PAIR_K = (ACQ, PAIR_CID, "pd-pair")
#: settled whole-position nets per outcome: HOLD, the +1.5 pair, the exit
NETS = {"NYY": {HOLD_K: -5.0, PAIR_K: -1.36, EXIT_K: -1.07},
        "BY1": {HOLD_K: 5.0, PAIR_K: 8.64, EXIT_K: -1.07},
        "BY2": {HOLD_K: 5.0, PAIR_K: -1.36, EXIT_K: -1.07}}
WORST = {HOLD_K: -5.0, PAIR_K: -1.36, EXIT_K: -1.07}


def _frozen():
    hr = {"version": "SYNTHETIC", "not_rankable": [], "candidates": [
        {"action": "HOLD", "candidate_id": "HOLD", "value_usd": 0.5,
         "downside_usd": -5.0, "qty": 10.0},
        {"action": "DIRECT_EXIT", "candidate_id": "DIRECT_EXIT",
         "plan_digest": "pd-exit", "value_usd": -1.07, "downside_usd": -1.07,
         "qty": 10.0, "cash_now_usd": 3.93}]}
    pair = {"action": ACQ, "candidate_id": PAIR_CID, "plan_digest": "pd-pair",
            "rankable": True, "value_usd": 0.18, "worst_case_net_usd": -1.36,
            "downside_usd": -1.36, "incremental_capital_usd": 6.36,
            "qty": 10.0}
    return dict(hold_ranking=hr, indirect_candidates=[pair], limits=None,
                capital_duration_h=None)


CV = {"ok": True, "funded_dispatch_permitted": True,
      "winner": {"fixed_action": ["HOLD", "HOLD"]}, "same_order_exits": {},
      "valued": [{"fixed_action": ["HOLD", "HOLD"], "value_at_range_low": 0.5,
                  "value_at_range_high": 0.5},
                 {"fixed_action": ["DIRECT_EXIT", "DIRECT_EXIT", 10.0],
                  "value_at_range_low": -1.07, "value_at_range_high": -1.07},
                 {"fixed_action": [ACQ, PAIR_CID], "value_at_range_low": 0.18,
                  "value_at_range_high": 0.18}]}


def _row(i, outcome, *, decided_at=1.0e9):
    fr = XP.freeze_inputs(_frozen())
    vals = {XR._skey(k): {"action": k[0], "settled_net_usd": v,
                          "ex_ante_worst_case_usd": WORST[k],
                          "capital_required_usd": 6.36 if k == PAIR_K else 0.0,
                          "capital_released_usd": 3.93 if k == EXIT_K
                          else 0.0}
            for k, v in NETS[outcome].items()}
    return {"id": "syn-%02d" % i, "fixture": "syn-fx-%02d" % i,
            "decided_at": decided_at, "outcome": vals,
            "outcome_at": decided_at + 3600, "why": None,
            "_frozen": XP.thaw_inputs(fr)["frozen"],
            "_frozen_digest": fr["digest"],
            "_policy_params": XP.default_params(), "_cv": CV,
            "_hold_key": HOLD_K}


def test_the_parameter_changes_the_real_selectors_choice():
    """SYNTHETIC. The sacrifice genuinely moves Xavier's decision function:
    0 and 0.25 hold (the pair is $0.32 below HOLD), 0.50 and 1.00 take the
    pair (worst -1.36 instead of -5.00), and a sacrifice outside the class's
    bounds would take the certain-loss exit -- the reason for the bound."""
    r = _row(0, "NYY")
    got = {s: XR.choose(r, s)["effective"] for s in (0.0, 0.25, 0.5, 1.0, 2.0)}
    assert got[0.0] == HOLD_K and got[0.25] == HOLD_K
    assert got[0.5] == PAIR_K and got[1.0] == PAIR_K
    assert got[2.0] == EXIT_K
    assert XR.choose(r, 0.5)["gate"]["permitted"] is True
    # the frozen inputs survive a jsonb round trip and a changed field is
    # caught by the digest
    fr = XP.freeze_inputs(_frozen())
    back = json.loads(json.dumps(fr))
    assert XP.thaw_inputs(back)["ok"] is True
    back["frozen"]["indirect_candidates"][0]["value_usd"] = 0.49
    assert XP.thaw_inputs(back)["refusal"] == "FROZEN_INPUTS_DIGEST_MISMATCH"
    assert XP.thaw_inputs({})["refusal"] == \
        "NO_FROZEN_DECISION_INPUTS_PERSISTED"


def _judge(rows, s, base=0.0):
    cls = IMP.CHANGE_CLASSES[CLS]
    v, b = XR.replay(rows, sacrifice=s), XR.replay(rows, sacrifice=base)
    m = dict(XR.public(v), **XR.paired(v, b), min_fixtures=10)
    return m, IMP.judge(m, success=cls.success_metrics, harm=cls.harm_metrics,
                        min_key="min_fixtures", n_key="fixtures")


#: the rehearsal's settlement pattern, in fixture order
PATTERN = ["NYY", "NYY", "BY1", "BY2", "NYY", "BY2", "NYY", "BY1", "NYY",
           "BY2", "NYY"]


def test_the_replay_judges_net_drawdown_identity_and_evidence_volume():
    """SYNTHETIC rows through the class's own rule."""
    rows = [_row(i, o) for i, o in enumerate(PATTERN)]
    m, j = _judge(rows, 0.5)
    assert j["verdict"] == IMP.V_PASS, (j, m)
    assert m["objective_outcome"] == "IMPROVED_NET_RESULT"
    assert m["actions_changed"] == 11 and m["actions"] == {ACQ: 11}
    assert m["delta_net_per_eligible_fixture"] == pytest.approx(10.04 / 11)
    assert m["delta_max_drawdown"] < 0
    assert m["delta_worst_decision_net_usd"] == pytest.approx(3.64)
    assert m["could_have_filled"] == "UNPROVEN"
    # AN IDENTICAL SELECTION GAINS NOTHING AND FAILS
    m, j = _judge(rows, 0.25)
    assert m["objective_outcome"] == "IDENTICAL_SELECTION"
    assert m["identical_selection"] is True and m["actions_changed"] == 0
    assert j["verdict"] == IMP.V_NO_GAIN
    # NO ELIGIBLE DECISION IS INSUFFICIENT, NEVER A SUCCESS
    m, j = _judge([], 0.5)
    assert m["objective_outcome"] == "NO_ELIGIBLE_DECISIONS"
    assert j["verdict"] == IMP.V_INSUFFICIENT
    m, j = _judge(rows[:9], 0.5)
    assert j["verdict"] == IMP.V_INSUFFICIENT
    # THE SAME TRADEOFF WHEN THE HELD SIDE MOSTLY WINS BY TWO OR MORE: worse
    # settled net -- rejected on its numbers, not tuned into a pass
    bad = [_row(i, o) for i, o in enumerate(
        ["BY2", "BY2", "NYY", "BY2", "BY2", "BY1", "BY2", "NYY", "BY2",
         "BY2", "NYY"])]
    m, j = _judge(bad, 0.5)
    assert m["objective_outcome"] == "WORSE_NET_RESULT"
    assert j["verdict"] in (IMP.V_NO_GAIN, IMP.V_HARM)
    # THE DRAWDOWN CRITERION BITES: a better net with a deeper drawdown fails
    deep = [_row(i, o) for i, o in enumerate(
        ["NYY", "BY1", "NYY", "BY2", "NYY", "BY1", "NYY", "BY2", "NYY",
         "BY2", "NYY"])]
    m, j = _judge(deep, 0.5)
    assert m["delta_net_per_eligible_fixture"] > 0
    assert m["delta_max_drawdown"] > 0 and j["verdict"] == IMP.V_NO_GAIN


# ════════════════════════════════════════════════════════════════════
# 1 · THE DATABASE CHAIN
# ════════════════════════════════════════════════════════════════════

pg = F.pg


def _events(holdout: bool, n: int, *, skip=()) -> list:
    """Fixture keys on the requested side of Xavier's fixture-level
    holdout, as the scheduled catalogue names them."""
    out, d = [], dt.date(2026, 10, 7)
    while len(out) < n:
        ev = "mlb-bos-nyy-%s" % d.isoformat()
        if ev not in skip and IMP.assign_holdout(
                ev, salt=XR.HOLDOUT_SALT,
                percent=XR.HOLDOUT_PERCENT) == holdout:
            out.append(ev)
        d += dt.timedelta(days=1)
    return out


def _slugs(ev):
    return "aec-" + ev, "asc-" + ev + "-neg-1pt5"


async def _catalogue(conn, H, ev):
    """The venue catalogue's rows for one fixture (the harness's own rows,
    per fixture)."""
    held, sib = _slugs(ev)
    for col, typ in (("team_abbr", "text"), ("team_name", "text"),
                     ("game_start", "timestamptz"), ("sports_type", "text"),
                     ("signed", "text"), ("intent", "text")):
        await conn.execute("ALTER TABLE us_premap ADD COLUMN IF NOT EXISTS "
                           "%s %s" % (col, typ))
    await conn.execute("DELETE FROM us_premap WHERE event_slug=$1", ev)
    for ident, slug, st, side, abbr, line, signed, intent in (
            (held + ":bos", held, "baseball_team_full_game_winner",
             "boston red sox", "bos", "00", None, LONG),
            (held + ":nyy", held, "baseball_team_full_game_winner",
             "new york yankees", "nyy", "00", None, SHORT),
            (sib + ":bos", sib, "baseball_team_full_game_spread",
             "yes", "bos", "1.5", "-1.5", LONG),
            (sib + ":nyy", sib, "baseball_team_full_game_spread",
             "no", "nyy", "1.5", "+1.5", SHORT)):
        await conn.execute(
            "INSERT INTO us_premap (identifier, event_slug, event_title,"
            " market_slug, question, kind, line, side_norm, intent, signed,"
            " team_abbr, team_name, sports_type, game_start)"
            " VALUES ($1,$2,$3,$4,$5,'side',$6,$7,$8,$9,$10,$11,$12,"
            "         now() + interval '3 hours')",
            ident, ev, "Boston Red Sox vs. New York Yankees", slug,
            "Who will win?", line, side, intent, signed, abbr, abbr, st)


async def _position(conn, H, ev, *, p=0.55):
    """The held entry through the book's own writers, and HOLD's eligible
    probability row (the harness's writers, per fixture)."""
    from sportsassets import bettor_funded_activation as FA
    from sportsassets import bettor_funded_book as FB
    from sportsassets import bettor_funded_execution as FX
    from tests import approved_conditional_model as ACM
    held, _ = _slugs(ev)
    iid = "fpi-xim-" + ev
    got = await FB.record_intent(
        conn, intent_id=iid, account_id=H.ACCT, venue=H.VENUE,
        venue_class=FA.VENUE_FUNDED, us_market_slug=held, event_key=ev,
        order_intent=FX.LONG, limit_price=0.50, quantity=10,
        collateral_usd=FX.collateral_for(0.50, 10, FX.LONG),
        effective_digest="d-xim", payout_event=H.PAYS_ON, held_is_long=True,
        portfolio_group_id=None, leg_role="PRIMARY",
        group_structure="INDIRECT_MIDDLE")
    assert got.get("ok"), got
    await FB.record_acknowledgement(conn, iid, venue_order_id="vo-" + iid,
                                    status="open")
    await FB.ingest_fills(conn, iid, [{"qty": 10.0, "price": 0.50,
                                       "venue_fill_id": "vf-" + iid}])
    when = time.time()
    await conn.execute(
        "INSERT INTO external_valuations (experiment_id, version, "
        " source_class, provider, book, devig_method, venue, "
        " contract_selection, sport_family, market, raw_odds, "
        " outcomes_priced, expected_outcomes, decision, admissible, why, "
        " refusals, us_market_slug, probability, probability_event, "
        " payout_event, payout_is_complement, buy_intent, observed_at, "
        " received_at, age_s, eligibility, decided_at, executable_price, "
        " cost_per_contract, estimated_edge_per_contract, mapped_outcome, "
        " ineligible_reason, settlement_rule, settlement_comparison) "
        "VALUES ('EXP',$1,'EXTERNAL_BOOKMAKER_VALUATION','PINNACLE',"
        " 'pinnacle','multiplicative',$2,$3,'baseball','WINNER','{}'::jsonb,"
        " 2,2,'BUY',TRUE,$4,ARRAY[]::text[],$5,$6,$7,$7,FALSE,$8,"
        " to_timestamp($9),to_timestamp($9),0.5,'ELIGIBLE',to_timestamp($9),"
        " 0.5,0.5,0.05,$3,NULL,$10::jsonb,$11::jsonb)",
        ACM.CAL_SOURCE, H.PROB_VENUE, H.PAYS_ON, H.LABEL, held, float(p),
        H.PAYS_ON, FX.LONG, float(when), json.dumps(H.SETTLED_RULE),
        json.dumps({"verdict": "COMPATIBLE",
                    "fixture_event_state": "IN_PROGRESS",
                    "fixture_read": True}))
    return iid


def _books(H, events):
    out = {}
    for ev in events:
        held, sib = _slugs(ev)
        out[held] = {"bids": [(0.40, 400)], "offers": [(0.99, 5)]}
        out[sib] = {"bids": [(0.38, 500)], "offers": [(0.90, 500)]}
    return out


#: long-side settlement prices: (held moneyline, BOS -1.5 run line)
PRICES = {"NYY": (0.0, 0.0), "BY1": (1.0, 0.0), "BY2": (1.0, 1.0)}


async def _settle(conn, H, events, outcomes, table):
    """Held legs through the production settlement close (probe
    substituted); the unheld run line through a labelled pair observation
    (production recorder and labeller, settlement read substituted). The
    probe table is extended by reference, so later passes' re-reads agree."""
    from sportsassets import bettor_funded_management as FM
    from sportsassets import bettor_pair_observations as PO
    from tests import test_the_hedge_given_primary_model_prices_the_scheduled_pair as D2
    prices = {}
    for ev, o in zip(events, outcomes):
        held, sib = _slugs(ev)
        hp, sp = PRICES[o]
        table[held] = H._settled(hp)
        table[sib] = H._settled(sp)
        got = await FM.reconcile_settlement(
            conn, intent_id="fpi-xim-" + ev, client=object(),
            probe=lambda c, s, _p=hp: H._settled(_p), now=time.time())
        assert got.get("ok") and got.get("closed"), got
        await PO.record(
            conn, fixture=ev, admitted={
                "structure": D2._middle(), "taxonomy": "MIDDLE",
                "condition_id": sib + "#" + SHORT},
            held_leg=D2._Leg(held + "#" + LONG), primary_slug=held,
            primary_side=LONG, hedge_slug=sib, hedge_side=SHORT,
            primary_cost_cents=50, hedge_cost_cents=62,
            overtime_included=True, price_basis={"synthetic": REHEARSAL},
            at=time.time())
        prices[held], prices[sib] = hp, sp
    lab = await PO.label_pending(conn, now=time.time(), limit=10_000,
                                 recheck=10_000,
                                 settlement_reader=D2._reader(prices))
    assert lab["ok"] and lab["labelled"] >= len(events), lab


async def _serve(conn, H, monkeypatch, events, table):
    """ONE scheduled servicing pass (the learning half skipped: it runs on
    its own cadence and decides nothing here)."""
    from sportsassets.workers import ext_pinnacle_loop as L
    venue = H.Venue(books=_books(H, events),
                    holdings={_slugs(ev)[0]: (10.0, 5.0) for ev in events})
    H.substitute(monkeypatch, venue, settlements=table)
    got = await L._funded_service(conn, now=time.time(), run_learning=False)
    assert got is not None and got.get("ok") is not False, got
    assert venue.creates_sent() == [], venue.sent     # every one HOLDs
    return got


async def _purge_xim(conn, events, directive_ids=()):
    from tests import test_the_pairing_model_bootstraps_from_non_funded_observations as BOOT
    await BOOT._purge(conn, "fixture = ANY($1::text[])", list(events))
    await conn.execute("DELETE FROM us_premap WHERE event_slug = "
                       " ANY($1::text[])", list(events))
    async with conn.transaction():
        await conn.execute("SET LOCAL session_replication_role = replica")
        for t in ("improvement_trials", "improvement_candidates",
                  "improvement_events"):
            if await conn.fetchval("SELECT to_regclass($1)", t):
                await conn.execute(
                    "DELETE FROM %s WHERE task_id LIKE 'imp-task-dir-%%'" % t)
        ids = list(directive_ids)
        await conn.execute(
            "DELETE FROM agent_task_events WHERE task_id IN (SELECT task_id "
            " FROM agent_tasks WHERE directive_id = ANY($1::text[])) "
            " OR task_id LIKE 'imp-task-dir-%'", ids)
        await conn.execute(
            "DELETE FROM agent_tasks WHERE directive_id = ANY($1::text[]) "
            " OR task_id LIKE 'imp-task-dir-%'", ids)
        if await conn.fetchval("SELECT to_regclass('management_directives')"):
            await conn.execute("DELETE FROM management_directive_events "
                               " WHERE directive_id = ANY($1::text[])", ids)
            await conn.execute("DELETE FROM management_directives WHERE "
                               " directive_id = ANY($1::text[])", ids)
        if await conn.fetchval("SELECT to_regclass('agent_policy_versions')"):
            await conn.execute(
                "DELETE FROM agent_policy_versions WHERE agent_id='XAVIER' "
                "  AND policy_key=$1 AND version LIKE 'imp-%'", XP.POLICY_KEY)


def _chat(client, headers, message, cookies=None):
    body = {"message": message, "request_id": F.rid("xim"),
            "conversation_id": "xim-conv"}
    client.cookies.clear()
    for k, v in (cookies or {}).items():
        client.cookies.set(k, v)
    r = client.post("/api/command/agents/audrey/chat", json=body,
                    headers=headers)
    client.cookies.clear()
    assert r.status_code == 200, r.text
    return r.json()


def _git(*a):
    return subprocess.run(["git", "-C", str(REPO), *a], check=True,
                          capture_output=True, text=True).stdout


async def _call(fn, *a, **kw):
    c = await F.connect()
    try:
        return await fn(c, *a, **kw)
    finally:
        await c.close()


@pg
def test_a_loss_directive_becomes_an_evaluated_committed_xavier_candidate(
        monkeypatch, tmp_path):
    from sportsassets.agents import audrey_chat as AC
    from sportsassets.agents import directives as D
    from tests import test_xavier_manages_positions_through_the_scheduled_path as H

    train = _events(False, 11, skip=(H.EVENT,))
    hold = _events(True, 11, skip=(H.EVENT,))
    events = train + hold
    table: dict = {}
    dids: list = []
    stamps: dict = {}

    async def _world():
        """The account (real writers), the approved model and measured void
        rate, then TWO review passes, each settled afterwards."""
        from tests import approved_conditional_model as ACM
        conn = await F.connect()
        try:
            await F.ensure_schema(conn)
            await H.clean(conn)
            await _purge_xim(conn, events)
            await H.authorize(conn)
            await ACM.approve(conn)
            # THE BOOK HOLDS ONE OPEN ENTRY POSITION AT A TIME
            # (bettor_funded_one_open_position), so every fixture is its own
            # scheduled review pass: held, reviewed, then settled.
            async def _one(ev, o):
                await _catalogue(conn, H, ev)
                await _position(conn, H, ev)
                await _serve(conn, H, monkeypatch, [ev], table)
                await _settle(conn, H, [ev], [o], table)
            # ── THE TRAINING fixtures, decided and settled first ──────
            for ev, o in zip(train, PATTERN):
                await _one(ev, o)
            await asyncio.sleep(1.1)
            stamps["boundary"] = time.time()
            await asyncio.sleep(1.1)
            # ── THE HOLDOUT fixtures, decided after the boundary ──────
            for ev, o in zip(hold, PATTERN):
                await _one(ev, o)
            recs = [dict(r) for r in await conn.fetch(
                "SELECT xavier_decision_id, chosen_action, account_id, "
                "       reasoning->'decision_inputs' AS di, "
                "       reasoning->'shadow_comparison' AS sh "
                "  FROM bettor_xavier_decisions WHERE account_id=$1",
                H.ACCT)]
            return recs
        finally:
            await conn.close()

    async def _teardown():
        conn = await F.connect()
        try:
            await H.clean(conn)
            await _purge_xim(conn, events, dids)
        finally:
            await conn.close()

    try:
        recs = F.run(_world())
        # ── 1 · THE REAL PATH RECORDED EVERY DECISION WITH ITS INPUTS ──
        decided = [r for r in recs if r["di"] is not None]
        assert len(decided) == 22, [r["chosen_action"] for r in recs]
        for r in decided:
            assert r["chosen_action"] == "HOLD"
            di = json.loads(r["di"])
            assert di["version"] == XP.DECISION_INPUTS_VERSION
            assert XP.thaw_inputs(di)["ok"] is True
            assert di["selected"][0] == "HOLD"
            assert di["held_contract"]["side"] == LONG
            assert any(c["side"] == SHORT for c in
                       di["acquisition_contracts"].values())
            # the shadow already said what CAPITAL_PRESERVATION_V1 would do
            sh = json.loads(r["sh"])
            assert sh["shadow_selected"]["selected"] == ACQ
        now_eval = stamps["boundary"] + 14 * DAY

        # ── 2 · THE AUTHENTICATED DIRECTIVE ─────────────────────────────
        F.no_network(monkeypatch)
        clock = F.Clock(stamps["boundary"] - 3600.0)
        client = F.build_client(monkeypatch, clock)
        got = _chat(client, F.desk_headers(), TEXT)
        assert got["status"] == AC.S_REQUIRES_OPERATOR   # read: refused
        got = _chat(client, {}, TEXT, cookies=F.operator_cookie())
        assert got["status"] == AC.S_DIRECTIVE, got
        d = got["directive"]
        dids.append(d["directive_id"])
        assert d["objective_kind"] == "LOSS_REDUCTION"
        assert d["requested_by_role"] == "operator"
        xtask = D.task_id_for(d["directive_id"], "XAVIER")
        assert xtask in d["task_ids"]

        # ── 3 · XAVIER TAKES THE WORK UP AND IT IS EVALUATED ────────────
        out = F.run(_call(IMP.run_due, now=now_eval))
        assert out.get("ok") is True, out
        wid = IMP.directive_work_task_id(xtask)
        taken = {t["task_id"]: t for t in out["directive_work"]["taken_up"]}
        assert xtask in taken and xtask not in out["directive_work"][
            "waiting"]
        assert taken[xtask]["change_class"] == CLS
        assert taken[xtask]["variants"] == [0.25, 0.5, 1.0]   # protect only
        w = F.run(_call(IMP.read_task, wid))
        assert w["assignee"] == "XAVIER" and w["created_by"] == "XAVIER"
        assert w["spec"]["direction"] == IMP.MORE_PROTECTION
        assert w["status"] == "APPROVAL_READY", (w, [
            a for a in out["advanced"] if a.get("task_id") == wid])
        res = [a for a in out["advanced"] if a.get("task_id") == wid][0]
        assert res["verdict"] == IMP.V_PASS, res
        assert res["evidence_scope"] == XR.SCOPE_REHEARSAL
        cid = res["candidate_id"]
        cand = F.run(_call(IMP.candidate, cid))
        assert cand["params"] == {P: 0.5}
        assert cand["state"] == "APPROVAL_READY"
        assert cand["proposed_by"] == "XAVIER"
        assert cand["evaluated_by"] == IMP.EVALUATOR_REPLAY
        assert cand["release_scope"] == IMP.SCOPE_APPROVAL
        ev = cand["evaluation"]
        assert ev["verdict"] == IMP.V_PASS and ev["segment"] == "HOLDOUT"
        hm = ev["metrics"]
        # ON THE HOLDOUT, RE-RUNNING THE REAL SELECTOR ON RECORDED INPUTS:
        # every decision changes from HOLD to the pair; a better settled net
        # per eligible fixture, a shallower drawdown and worst decision
        assert hm["fixtures"] == 11 and hm["eligible_decisions"] == 11
        assert hm["fixtures_in_both"] == 0
        assert hm["objective_outcome"] == "IMPROVED_NET_RESULT"
        assert hm["actions_changed"] == 11 and hm["actions"] == {ACQ: 11}
        assert hm["baseline"]["actions"] == {"HOLD": 11}
        assert hm["baseline"]["net_total"] == pytest.approx(-5.0)
        assert hm["delta_net_per_eligible_fixture"] > 0.5
        assert hm["delta_max_drawdown"] < 0
        assert hm["delta_worst_decision_net_usd"] > 3.0
        assert hm["delta_mean_ex_ante_worst_case_usd"] > 3.0
        assert hm["delta_capital_required"] > 0         # it buys the hedge
        assert hm["dispatch_refused_by_the_gate"] == 0
        assert hm["could_have_filled"] == "UNPROVEN"
        assert hm["evidence_class"] == "REHEARSAL_DEMONSTRATION_BOOKS"
        # TRAINING ONLY FOR SELECTION; every variant recorded
        sel = ev["selection"]
        assert sel["segment"] == "TRAINING"
        assert sel["holdout_used_for_selection"] is False
        att = {a["sacrifice"]: a for a in sel["attempted"]}
        assert sorted(att) == [0.25, 0.5, 1.0]
        assert att[0.25]["training"]["objective_outcome"] == \
            "IDENTICAL_SELECTION"
        assert att[0.25]["verdict"] == IMP.V_NO_GAIN
        assert att[0.5]["verdict"] == att[1.0]["verdict"] == IMP.V_PASS
        assert sel["selected"] == {"sacrifice": 0.5, "candidate_id": cid}
        others = {c["params"][P]: c for c in F.run(_q(
            "SELECT params, state, evaluation FROM improvement_candidates "
            " WHERE task_id=$1 AND candidate_id<>$2", wid, cid))}
        assert others[0.25]["state"] == "REJECTED"
        assert others[0.25]["evaluation"]["verdict"] == IMP.V_NO_GAIN
        assert others[1.0]["evaluation"]["verdict"] == IMP.V_NOT_SELECTED
        htr = F.run(_q("SELECT candidate_id FROM improvement_trials WHERE "
                       " task_id=$1 AND segment='HOLDOUT'", wid))
        assert [r["candidate_id"] for r in htr] == [cid]
        # THE BINDING AND WHAT THE EVIDENCE IS
        b = ev["binding"]
        assert b["evaluator_version"] == IMP.XAVIER_REPLAY_VERSION
        assert b["params"] == {P: 0.5} and b["units"] == XR.UNITS
        assert b["current"][P] == 0.0
        assert len(b["input_records"]["digest"]) == 64
        assert b["input_records"]["evidence_scope"] == XR.SCOPE_REHEARSAL
        q = ev["qualification"]
        assert q["evidence"] == IMP.QUAL_RETROSPECTIVE
        assert q["economic_qualification"] == IMP.ECON_PENDING
        assert q["rehearsal"] is True and q["rehearsal_label"]
        assert IMP.economic_promotion_gate(cand)["permitted"] is False
        # nothing activated; the directive task follows
        assert F.run(_q("SELECT 1 FROM agent_policy_versions WHERE "
                        " agent_id='XAVIER' AND state='ACTIVE'")) == []
        xt = F.run(_call(IMP.read_task, xtask))
        assert xt["status"] == "APPROVAL_READY", xt

        # ── 4 · THE ARTIFACT: EXACTLY THE EVALUATED PARAMETER ──────────
        base = _git("rev-parse", "HEAD").strip()
        branch = "improve/%s" % wid
        target = BACKEND / "sportsassets" / "agents" / "xavier_policy.py"
        before = target.read_text()
        env = {k: v for k, v in os.environ.items() if k != "RENDER"}
        try:
            p = subprocess.run(
                [sys.executable, str(TOOL), "--task-id", wid,
                 "--candidate-id", cid, "--dsn", F.DSN, "--repo", str(REPO),
                 "--base", base, "--workdir", str(tmp_path),
                 "--now", str(now_eval + 60)],
                capture_output=True, text=True, env=env, timeout=900)
            rep = json.loads(p.stdout)
            assert p.returncode == 0, rep
            sha = _git("rev-parse", branch).strip()
            assert rep["commit"] == sha and rep["base_commit"] == base
            diff = _git("diff", base, branch, "--",
                        "backend/sportsassets/agents/xavier_policy.py")
            minus = [ln for ln in diff.splitlines()
                     if ln.startswith("-") and not ln.startswith("---")]
            plus = [ln for ln in diff.splitlines()
                    if ln.startswith("+") and not ln.startswith("+++")]
            assert len(minus) == len(plus) == 1, diff      # ONE LINE
            assert minus[0].startswith(
                "-MAX_EV_SACRIFICE_FOR_DOWNSIDE_USD = 0.0  # versioned")
            assert plus[0].startswith(
                "+MAX_EV_SACRIFICE_FOR_DOWNSIDE_USD = 0.5  # versioned")
            tr = rep["test_results"]
            assert tr["passed"] is True, tr
            assert tr["counts"]["passed"] >= 3
            files = set(_git("show", "--name-only", "--format=",
                             branch).split())
            evj = [f for f in files if f.endswith("/evaluation.json")][0]
            committed = json.loads(_git("show", "%s:%s" % (branch, evj)))
            assert committed["evaluated_candidate"]["candidate_id"] == cid
            assert committed["replay"]["new"]["objective_outcome"] == \
                "IMPROVED_NET_RESULT"
            assert committed["replay"]["evidence_scope"] == \
                XR.SCOPE_REHEARSAL
            assert rep["recorded"]["ok"] is True, rep["recorded"]
            cand = F.run(_call(IMP.candidate, cid))
            assert cand["artifact_ref"] == "%s@%s" % (branch, sha)
            assert cand["test_results"]["passed"] is True
            assert cand["state"] == "APPROVAL_READY"
            assert cand["evaluation"]["verdict"] == IMP.V_PASS   # unchanged
            assert target.read_text() == before     # this checkout untouched
        finally:
            subprocess.run(["git", "-C", str(REPO), "branch", "-D", branch],
                           capture_output=True)
            subprocess.run(["git", "-C", str(REPO), "worktree", "prune"],
                           capture_output=True)

        # ── 5 · APPROVAL RE-VERIFIES THE BASIS ─────────────────────────
        for who, why in (("XAVIER", IMP.R_AGENT_APPROVER),
                         (IMP.EVALUATOR_REPLAY, IMP.R_AGENT_APPROVER)):
            got = F.run(_call(IMP.approve, cid, approver=who,
                              credential_role="admin", now=now_eval + 90))
            assert got["refusal"] == why, (who, got)
        # AN INPUT RECORD CHANGES: one holdout position's booked settlement
        # (a SIMULATED correction, written directly): the basis is void
        victim = "fpi-xim-" + hold[0]

        async def _flip(to):
            c = await F.connect()
            try:
                async with c.transaction():
                    await c.execute(
                        "SET LOCAL session_replication_role = replica")
                    await c.execute(
                        "UPDATE bettor_funded_intents SET settlement = "
                        " jsonb_set(settlement, '{payout_price}', $2::jsonb)"
                        " WHERE intent_id=$1", victim, json.dumps(to))
            finally:
                await c.close()
        orig = PRICES[PATTERN[0]][0]
        F.run(_flip(1.0 - orig))
        got = F.run(_call(IMP.approve, cid, approver="owner@desk",
                          credential_role="admin", now=now_eval + 91))
        assert got["refusal"] == IMP.R_BASIS_CHANGED, got
        assert got["changed"] == ["INPUT_RECORDS"]
        F.run(_flip(orig))
        got = F.run(_call(IMP.approve, cid, approver="owner@desk",
                          credential_role="admin",
                          statement="read the rehearsal replay",
                          now=now_eval + 92))
        assert got["ok"] is True, got
        assert got["approval_basis"]["input_digest"] == b["input_records"][
            "digest"]
        assert got["deployed"] is False
        # REVIEWED AS A CANDIDATE -- NOT QUALIFIED FOR LIVE USE
        assert got["live_promotion"]["permitted"] is False
        assert got["live_promotion"]["refusal"] == IMP.R_ECON_PENDING
        pv = F.run(_q("SELECT state, params, approved_by FROM "
                      " agent_policy_versions WHERE agent_id='XAVIER' AND "
                      " policy_key=$1", XP.POLICY_KEY))
        assert [(r["state"], r["approved_by"]) for r in pv] == [
            ("CANDIDATE", "owner@desk")]
        params = pv[0]["params"]
        # the CANDIDATE row is a WHOLE policy that validates as written
        assert XP.validate(params)["ok"] is True
        assert params[P] == 0.5 and params["selection_rule"] == \
            XP.SEL_CAPITAL_PRESERVATION
        # ...and the next scheduled review shows it as the SHADOW (displayed,
        # never dispatched); the ACTIVE policy is still the approved default
        shadow = F.run(_call(XP.load_shadow, XP.code_default()))
        assert shadow["source"] == XP.SOURCE_STORED_CANDIDATE
        assert (F.run(_call(XP.load)))["source"] == XP.SOURCE_CODE_DEFAULT
    finally:
        F.run(_teardown())


async def _q_async(sql, *args):
    c = await F.connect()
    try:
        out = []
        for r in await c.fetch(sql, *args):
            row = dict(r)
            for k in ("params", "evaluation"):
                if isinstance(row.get(k), str):
                    row[k] = json.loads(row[k])
            out.append(row)
        return out
    finally:
        await c.close()


def _q(sql, *args):
    return _q_async(sql, *args)


@pg
def test_an_objective_the_class_cannot_address_waits_and_no_eligible_window_is_insufficient(
        monkeypatch):
    """A PAIRING directive names Xavier only; his tradeoff class cannot move
    it, so the task WAITS and says why. A task over a window with no
    recorded decision is INSUFFICIENT_EVIDENCE for every variant, never a
    success. (SYNTHETIC; no decisions exist in the 2031 window.)"""
    from sportsassets.agents import audrey_chat as AC
    from sportsassets.agents import directives as D

    async def _setup():
        c = await F.connect()
        try:
            await F.ensure_schema(c)
            await F.purge(c)
            await _purge_xim(c, [])
        finally:
            await c.close()

    async def _teardown():
        c = await F.connect()
        try:
            await _purge_xim(c, [])
            await F.teardown(c)
        finally:
            await c.close()
    F.run(_setup())
    try:
        F.no_network(monkeypatch)
        client = F.build_client(monkeypatch, F.Clock(F.T0))
        got = _chat(client, {}, PAIRING, cookies=F.operator_cookie())
        assert got["status"] == AC.S_DIRECTIVE, got
        d = got["directive"]
        assert d["objective_kind"] == "PAIRING_AND_HEDGING"
        xtask = D.task_id_for(d["directive_id"], "XAVIER")
        out = F.run(_call(IMP.run_due, now=F.T0 + DAY))
        assert out.get("ok") is True, out
        assert xtask in out["directive_work"]["waiting"]
        assert out["directive_work"]["taken_up"] == []
        xt = F.run(_call(IMP.read_task, xtask))
        assert xt["status"] == "WAITING"
        evs = F.run(_q("SELECT kind, detail FROM agent_task_events WHERE "
                       " task_id=$1", xtask))
        w = [json.loads(e["detail"]) if isinstance(e["detail"], str)
             else e["detail"] for e in evs
             if e["kind"] == "WAITING_FOR_AN_EVALUATOR"]
        assert w and w[0]["why"] == IMP.R_OBJECTIVE_NOT_ADDRESSED
        assert w[0]["addresses"] == ["DRAWDOWN_REDUCTION", "LOSS_REDUCTION"]
        assert F.run(_call(IMP.read_task, IMP.directive_work_task_id(
            xtask))) is None

        # ── A WINDOW WITH NO RECORDED DECISION ─────────────────────────
        tid = "imp-task-dir-xim-noeligible"
        made = F.run(_call(
            IMP.create_task, assignee="XAVIER", created_by="XAVIER",
            kind=IMP.TASK_KIND, title="no eligible window (SYNTHETIC)",
            spec={"change_class": CLS, "evidence_scope": XR.SCOPE_AUTO,
                  "training_start": F.T0 - 40 * DAY,
                  "training_boundary": F.T0 - 10 * DAY,
                  "evaluation_boundary": F.T0},
            task_id=tid, now=F.T0))
        assert made.get("ok", True), made
        out = F.run(_call(IMP.run_due, now=F.T0 + DAY))
        res = [a for a in out["advanced"] if a.get("task_id") == tid][0]
        assert res["verdict"] == IMP.V_INSUFFICIENT, res
        assert res["baseline_training"]["no_eligible_decisions"] is True
        assert F.run(_call(IMP.read_task, tid))["status"] == "REJECTED"
        cands = F.run(_q("SELECT state, evaluation FROM "
                         " improvement_candidates WHERE task_id=$1", tid))
        assert len(cands) == 3
        for c in cands:
            assert c["state"] == "REJECTED"
            assert c["evaluation"]["verdict"] == IMP.V_INSUFFICIENT
            assert c["evaluation"]["metrics"]["objective_outcome"] == \
                "NO_ELIGIBLE_DECISIONS"
        assert F.run(_q("SELECT 1 FROM improvement_trials WHERE task_id=$1 "
                        " AND segment='HOLDOUT'", tid)) == []
    finally:
        F.run(_teardown())
