"""THE ECONOMIC DIGITAL TWIN: COUNTERFACTUAL, VERSIONED, REPRODUCIBLE, AND
FREE FROM FUTURE INFORMATION.

  §1 REPRODUCIBLE. The same recorded inputs give the same outputs (the same
     output sha) for every frozen scenario, whatever order the rows arrive
     in; different inputs give a different input sha. Through the database,
     a second cycle over unchanged inputs inserts nothing new and the stored
     output sha is unchanged.
  §2 NO FUTURE INFORMATION. A world decision at t reads only records with
     time <= t: every trace's max_input_at <= decision_at; the time view
     refuses to serve a later record; records injected AFTER each decision
     instant change no world's result (while one injected BEFORE does -- the
     control); the database refuses a trace that read the future.
  §3 THE WORLDS. Derek threshold, sizing, Xavier always-HOLD and immediate
     EXIT, allocator, Karen block accepted/ignored, Archer and Scout
     (UNAVAILABLE without their interface, computed with it) -- each checked
     against hand-computed P&L on the synthetic stream.
  §4 VERSIONED AND FROZEN. A scenario's id is its spec's sha; the database
     refuses any change to a stored scenario, and a changed spec under an
     existing version is refused by the store.
  §5 LABELS. The baseline carries its book (PAPER/ACTUAL), the world is
     COUNTERFACTUAL, the comparison is a paired difference, never a sum.

ALL DATA IS SYNTHETIC (tests/twin_fixture.py).
"""
from __future__ import annotations

import copy
import json
import random
import time

import asyncpg
import pytest

from sportsassets.twin import engine as E
from sportsassets.twin import runner as RUN
from sportsassets.twin import scenarios as SC
from sportsassets.twin import store as ST

try:
    from tests import intel_fixture as F
    from tests import paper_harness as H
    from tests import twin_fixture as TF
except ImportError:                                             # pragma: no cover
    import intel_fixture as F
    import paper_harness as H
    import twin_fixture as TF

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
CAT = {s["scenario_key"]: s for s in SC.catalog()}


def run(key, st=None):
    return E.run_scenario(st or TF.stream(), CAT[key])


# ── §1 reproducible ──────────────────────────────────────────────────

def test_same_inputs_give_the_same_outputs_for_every_scenario():
    a = TF.stream()
    raw = TF.scenario_raw()
    rng = random.Random(7)
    for k in ("decisions", "fills", "settlements"):
        rng.shuffle(raw[k])
    books = TF.scenario_books()
    rng.shuffle(books)
    b = TF.stream(raw=raw, books=books)
    assert a.input_sha() == b.input_sha()
    for key, sc in CAT.items():
        ra, rb = E.run_scenario(a, sc), E.run_scenario(b, sc)
        assert ra["body"]["output_sha256"] == rb["body"]["output_sha256"], key
        assert ra["body"] == rb["body"], key
        # and again on the same objects
        assert E.run_scenario(a, sc)["body"] == ra["body"], key


def test_different_inputs_give_a_different_input_sha():
    raw = TF.scenario_raw()
    raw["fills"][0]["price"] = 0.54
    assert TF.stream(raw=raw).input_sha() != TF.stream().input_sha()


# ── §2 no future information ─────────────────────────────────────────

def test_every_world_decision_read_only_records_at_or_before_its_instant():
    st = TF.stream(karen=[{"challenge_id": "k1", "target_id": "gC",
                           "evidence_refs": [], "at": TF.T0 + 60}],
                   allocations=[{"run_id": "r", "decision_id": "A",
                                 "shadow_usd": 100.0, "at": TF.T0 - 5}],
                   regimes=[{"run_id": "r", "recommendation": "NORMAL",
                             "at": TF.T0 - 5}])
    n = 0
    for key, sc in CAT.items():
        for t in E.run_scenario(st, sc)["rows"]:
            n += 1
            assert t["max_input_at"] is None or \
                t["max_input_at"] <= t["decision_at"] + 1e-9, (key, t)
            assert t["inputs_read"] >= 1, (key, t)
    assert n > 30


def test_the_time_view_never_serves_a_later_record():
    st = TF.stream(books=TF.scenario_books() + [
        TF.book("syn-a", TF.T0 + 5, bids=((0.99, 999),))])
    v = st.view(TF.T0 + 2)
    b = v.latest_book("syn-a", 120.0)
    assert b["at"] == TF.T0 + 1 and b["best_bid"] == 0.54
    assert v.max_input_at == TF.T0 + 1
    with pytest.raises(E.FutureRead):
        v._seen(TF.T0 + 3)
    # an outcome is never part of what a decision may read
    dec = st.view(TF.T0).decision(st.opps[0])
    assert set(dec) == set(E.DECISION_KEYS)
    assert not {"payoff", "realized_pnl_usd", "recorded_entered"} & set(dec)


def _worlds(st):
    return {k: {x: E.run_scenario(st, sc)["body"][x]
                for x in ("world", "baseline", "comparison", "status")}
            for k, sc in CAT.items()}


def test_records_injected_after_each_decision_change_no_world():
    base = _worlds(TF.stream())
    t = TF.T0
    future_books = TF.scenario_books() + [
        # one second after each entry / exit instant: absurd prices
        TF.book("syn-a", t + 3, bids=((0.99, 9999),), offers=((0.995, 9),)),
        TF.book("syn-c", t + 23, bids=((0.99, 9999),), offers=((0.995, 9),)),
        TF.book("syn-d", t + 33, bids=((0.01, 9999),), offers=((0.02, 9),))]
    later = TF.stream(
        books=future_books,
        # an allocation recorded one second AFTER each decision
        allocations=[{"run_id": "late", "decision_id": d,
                      "shadow_usd": 900.0, "at": t + off + 1}
                     for d, off in (("A", 0), ("C", 20), ("D", 30))],
        # a Karen block recorded after every position closed / at the end
        karen=[{"challenge_id": "late", "target_id": "gA",
                "evidence_refs": [], "at": t + 99999}],
        regimes=[{"run_id": "late", "recommendation": "NO_TRADE",
                  "at": t + 99999}])
    got = _worlds(later)
    for key in CAT:
        if key == "KAREN_BLOCK_ACCEPTED":
            # the late block lands after A closed: still no effect
            pass
        assert got[key] == base[key], key


def test_the_control_a_record_before_the_exit_instant_does_change_it():
    t = TF.T0
    st = TF.stream(books=[TF.book("syn-a", t + 1.5, bids=((0.70, 500),))]
                   + TF.scenario_books())
    a = TF.by_subject(run("XAVIER_IMMEDIATE_EXIT", st))["A"]
    b = TF.by_subject(run("XAVIER_IMMEDIATE_EXIT"))["A"]
    assert a["pnl_usd"] != b["pnl_usd"]
    assert a["pnl_usd"] == pytest.approx(100 * 0.70 - 1.0 - 54.0)


def test_stored_traces_are_bounded_and_keep_the_latest_decisions():
    raw = {"decisions": [TF.decision("Z%03d" % i, TF.T0 + i, verdict="REFUSE")
                         for i in range(E.MAX_TRACES + 50)],
           "groups": {}, "fills": [], "settlements": []}
    r = run("RECORDED", TF.stream(raw=raw, books=[]))
    assert len(r["rows"]) == E.MAX_TRACES + 50
    assert len(r["traces"]) == E.MAX_TRACES
    assert r["traces"][-1]["subject_id"] == "Z%03d" % (E.MAX_TRACES + 49)
    c = r["body"]["counts"]
    assert c["traces_truncated"] == 50
    assert c["decisions_checked_no_future_read"] == E.MAX_TRACES + 50


# ── §3 the worlds ────────────────────────────────────────────────────

def test_the_recorded_world_reproduces_the_attribution_pnl():
    r = run("RECORDED")
    tr = TF.by_subject(r)
    assert tr["A"]["pnl_usd"] == pytest.approx(46.0)
    assert tr["C"]["pnl_usd"] == pytest.approx(8.0)
    assert tr["B"]["pnl_usd"] == 0.0 and tr["B"]["pnl_basis"] == "RECORDED"
    assert tr["D"]["pnl_usd"] is None
    assert tr["D"]["unmeasured"]["pnl_usd"] == "POSITION_NOT_SETTLED"
    c = r["body"]["comparison"]
    assert c["total_diff_usd"] == 0.0 and c["paired_n"] == 3


def test_a_lower_derek_threshold_takes_the_refused_decision():
    tr = TF.by_subject(run("DEREK_THRESHOLD_2PC"))
    # B: net edge .55 - .52 - .01 = .02 >= .02 -> world-only entry, its
    # contract paid 1: 100 x (1 - .52 - .01) = 47
    assert tr["B"]["world_action"] == "ENTER"
    assert tr["B"]["pnl_usd"] == pytest.approx(47.0)
    assert tr["B"]["pnl_basis"] == (
        "ASSUMED_FILL_AT_DECISION_PLANNED_PRICE_HELD_TO_SETTLEMENT")
    assert tr["A"]["pnl_usd"] == pytest.approx(46.0)
    tr5 = TF.by_subject(run("DEREK_THRESHOLD_5PC"))
    assert tr5["B"]["world_action"] == "REFUSE" and tr5["B"]["pnl_usd"] == 0
    # D: .58 - .55 - .01 = .02 < .05 -> not entered in the 5% world
    assert tr5["D"]["world_action"] == "REFUSE"
    assert tr5["D"]["pnl_basis"] == "NOT_ENTERED_IN_WORLD"
    assert tr5["A"]["world_action"] == "ENTER"


def test_sizing_scales_and_respects_recorded_depth():
    tr = TF.by_subject(run("SIZING_DOUBLE"))
    # C's depth within limit is 150: 2x 100 capped to 150 -> 1.5x
    assert tr["C"]["pnl_usd"] == pytest.approx(8.0 * 1.5)
    assert "DEPTH_CAPPED" in tr["C"]["world_action"]
    assert tr["A"]["pnl_usd"] == pytest.approx(92.0)
    half = TF.by_subject(run("SIZING_HALF"))
    assert half["A"]["pnl_usd"] == pytest.approx(23.0)


def test_xavier_always_hold_and_immediate_exit():
    hold = TF.by_subject(run("XAVIER_ALWAYS_HOLD"))
    # C held to its LOST settlement: 100 x (0 - .50) - 1 = -51
    assert hold["C"]["pnl_usd"] == pytest.approx(-51.0)
    assert hold["C"]["recorded_action"] == "MANAGED"
    assert hold["A"]["pnl_usd"] == pytest.approx(46.0)
    ex = TF.by_subject(run("XAVIER_IMMEDIATE_EXIT"))
    # C sold at entry into the book seen 1 s earlier: 100 x .48 - 1 (entry
    # fee rate) - 51 (cost) = -4
    assert ex["C"]["pnl_usd"] == pytest.approx(-4.0)
    assert ex["C"]["pnl_basis"] == "BOOK_WALK_AT_EXIT_INSTANT_ENTRY_FEE_RATE"
    # A: 100 x .54 - 1 - 54 = -1
    assert ex["A"]["pnl_usd"] == pytest.approx(-1.0)


def test_immediate_exit_prefers_xaviers_frozen_thesis_plan():
    plan = {"IMMEDIATE_EXIT": {"available": True, "exit_proceeds_usd": 55.0,
                               "exit_fees_usd": 0.5, "unsold_qty": 0.0}}
    st = TF.stream(theses=[{"position_kind": "PAPER", "group_id": "gA",
                            "at": TF.T0 + 2.5, "entry_qty": 100.0,
                            "counterfactuals": plan}])
    a = TF.by_subject(run("XAVIER_IMMEDIATE_EXIT", st))["A"]
    assert a["pnl_basis"] == ("XAVIER_THESIS_IMMEDIATE_EXIT_PLAN_FROZEN_AT_"
                              "ENTRY")
    assert a["pnl_usd"] == pytest.approx(55.0 - 0.5 - 54.0)
    assert a["decision_at"] == TF.T0 + 2.5 >= a["max_input_at"]


def test_allocator_worlds():
    eq = TF.by_subject(run("ALLOCATOR_EQUAL_WEIGHT"))
    # $100 slots: A's return 46/54 per dollar
    assert eq["A"]["pnl_usd"] == pytest.approx(100.0 * 46.0 / 54.0, rel=1e-5)
    ind = TF.by_subject(run("ALLOCATOR_INDEPENDENT_SIZING"))
    assert ind["C"]["pnl_usd"] == pytest.approx(50.0 * 8.0 / 51.0, rel=1e-5)
    # no shadow allocation recorded before the decision -> unscored
    sh = TF.by_subject(run("ALLOCATOR_INTEL_SHADOW"))
    assert sh["A"]["pnl_usd"] is None
    assert sh["A"]["unmeasured"]["pnl_usd"] == (
        "NO_SHADOW_ALLOCATION_RECORDED_AT_OR_BEFORE_DECISION")
    st = TF.stream(allocations=[{"run_id": "r", "decision_id": "A",
                                 "shadow_usd": 200.0, "at": TF.T0 - 1}])
    sh2 = TF.by_subject(run("ALLOCATOR_INTEL_SHADOW", st))
    assert sh2["A"]["pnl_usd"] == pytest.approx(200.0 * 46.0 / 54.0,
                                                rel=1e-5)


def test_equal_weight_respects_the_free_sleeve():
    raw = TF.scenario_raw()
    t = TF.T0
    for i in range(12):
        did, g, s = "E%d" % i, "gE%d" % i, "syn-e%d" % i
        raw["decisions"].append(TF.decision(did, t + 100 + i, slug=s))
        raw["groups"][did] = g
        raw["fills"].append(TF.fill(g, t + 101 + i, qty=10, price=0.5,
                                    slug=s))
        raw["settlements"].append(TF.settlement(g, slug=s, qty=10,
                                                payout=1.0, at=t + 9000))
    tr = TF.by_subject(run("ALLOCATOR_EQUAL_WEIGHT", TF.stream(raw=raw)))
    # A, C, D hold three $100 slots (still open at t+100); 7 slots remain
    got = [tr["E%d" % i]["world_action"] for i in range(12)]
    assert got[:7] == ["EQUAL_WEIGHT:100.00"] * 7
    assert got[7:] == ["EQUAL_WEIGHT:0.00"] * 5
    assert tr["E9"]["pnl_basis"] == "NO_FREE_SLEEVE_NOT_ALLOCATED"


def test_karen_block_accepted_versus_ignored():
    t = TF.T0
    karen = [{"challenge_id": "k-pre", "target_id": "A", "evidence_refs": [],
              "at": t - 1},
             {"challenge_id": "k-mid", "target_id": "gC",
              "evidence_refs": [], "at": t + 60}]
    books = TF.scenario_books() + [TF.book("syn-c", t + 59,
                                           bids=((0.55, 500),))]
    st = TF.stream(karen=karen, books=books)
    acc = TF.by_subject(run("KAREN_BLOCK_ACCEPTED", st))
    assert acc["A"]["world_action"] == "BLOCK_ACCEPTED_NOT_ENTERED"
    assert acc["A"]["pnl_usd"] == 0.0
    # C blocked at t+60 while open: sold into the t+59 book: 55 - 1 - 51
    assert acc["C"]["world_action"] == "BLOCK_ACCEPTED_EXIT"
    assert acc["C"]["pnl_usd"] == pytest.approx(3.0)
    assert acc["C"]["decision_at"] == t + 60
    assert acc["D"]["world_action"] == "NO_BLOCK"
    ign = TF.by_subject(run("KAREN_BLOCK_IGNORED", st))
    assert ign["A"]["pnl_usd"] == pytest.approx(46.0)
    assert ign["C"]["pnl_usd"] == pytest.approx(8.0)
    assert ign["C"]["world_action"] == "BLOCK_IGNORED"


def test_archer_and_scout_are_unavailable_without_their_interface():
    for key in ("EDDIE_ALTERNATE_EXECUTION", "SCOUT_FEATURE_INCLUDED",
                "SCOUT_FEATURE_EXCLUDED"):
        r = run(key)
        assert r["body"]["status"] == "UNAVAILABLE", key
        assert r["body"]["unavailable_reason"].startswith(
            "INTERFACE_ABSENT:"), key
        assert r["body"]["world"] is None and r["traces"] == []


def test_archer_and_scout_worlds_when_the_interface_exists():
    t = TF.T0
    st = TF.stream(
        archer=[{"decision_id": "A", "at": t - 1, "qty": 100,
                "baseline_vwap": 0.53, "eddie_vwap": 0.525,
                "baseline_fee_usd": 1.0, "eddie_fee_usd": 0.5}],
        scout=[{"feature_id": "f1", "decision_id": "B", "at": t + 9,
                "p_with": 0.60, "p_without": 0.50, "status": "VALIDATED"}])
    ed = TF.by_subject(run("EDDIE_ALTERNATE_EXECUTION", st))
    assert ed["A"]["pnl_usd"] == pytest.approx(46.0 + 0.5 + 0.5)
    assert ed["C"]["world_action"] == "NO_ARCHER_PLAN_AT_DECISION"
    inc = TF.by_subject(run("SCOUT_FEATURE_INCLUDED", st))
    exc = TF.by_subject(run("SCOUT_FEATURE_EXCLUDED", st))
    assert inc["B"]["world_action"] == "ENTER"
    assert exc["B"]["world_action"] == "REFUSE"


# ── §5 labels ────────────────────────────────────────────────────────

def test_labels_and_never_summed():
    for key, sc in CAT.items():
        body = E.run_scenario(TF.stream(), sc)["body"]
        if body["status"] != "OK":
            continue
        assert body["world"]["label"] == "COUNTERFACTUAL", key
        assert body["baseline"]["label"] == "PAPER", key
        assert body["comparison"]["is_a_sum"] is False
        assert body["comparison"]["summed_across_books"] is False
        assert body["comparison"]["label"].endswith("_PAIRED_DIFFERENCE")
    act = TF.stream(basis="ACTUAL")
    assert E.run_scenario(act, CAT["RECORDED"])["body"]["baseline"][
        "label"] == "ACTUAL"


def test_unmeasured_is_null_with_a_reason_never_zero():
    body = run("XAVIER_ALWAYS_HOLD")["body"]
    d = TF.by_subject(run("XAVIER_ALWAYS_HOLD"))["D"]
    assert d["pnl_usd"] is None and d["unmeasured"]["pnl_usd"]
    assert body["world"]["unscored_reasons"] == {"POSITION_NOT_SETTLED": 1}
    empty = E.Stream(basis="PAPER", opps=[], positions=[], window=(0, 1))
    w = E.run_scenario(empty, CAT["XAVIER_ALWAYS_HOLD"])["body"]["world"]
    assert w["total_pnl_usd"] is None
    assert w["unmeasured"]["total_pnl_usd"] == "NO_SCORED_SUBJECT"


# ── §4 frozen, through the database ──────────────────────────────────

def test_a_scenario_id_is_its_spec_hash():
    for s in SC.catalog():
        assert s["scenario_id"] == "twinscn:" + s["spec_sha256"][:24]
    keys = [(s["scenario_key"], s["version"]) for s in SC.catalog()]
    assert len(set(keys)) == len(keys)
    assert len({s["spec_sha256"] for s in SC.catalog()}) == len(keys)


@pg
async def test_scenarios_are_frozen_in_the_database():
    conn = await asyncpg.connect(H.DSN)
    tr = conn.transaction()
    await tr.start()
    try:
        cat = SC.catalog()
        await ST.register_scenarios(conn, cat, now=TF.T0,
                                    engine_version=E.VERSION)
        await ST.register_scenarios(conn, cat, now=TF.T0 + 5,
                                    engine_version=E.VERSION)
        sp = conn.transaction()
        await sp.start()
        with pytest.raises(asyncpg.IntegrityConstraintViolationError):
            await conn.execute(
                "UPDATE twin_scenarios SET spec = '{}' WHERE scenario_id=$1",
                cat[0]["scenario_id"])
        await sp.rollback()
        sp = conn.transaction()
        await sp.start()
        with pytest.raises(asyncpg.IntegrityConstraintViolationError):
            await conn.execute("DELETE FROM twin_scenarios")
        await sp.rollback()
        changed = copy.deepcopy(SC.CATALOG[1])
        changed["params"] = {"min_net_edge_pc": 0.01}      # same version
        sp = conn.transaction()
        await sp.start()
        with pytest.raises(ST.FrozenSpecChanged):
            await ST.register_scenarios(conn, [SC.frozen(changed)],
                                        now=TF.T0, engine_version=E.VERSION)
        await sp.rollback()
        sp = conn.transaction()
        await sp.start()
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO twin_scenarios (scenario_id, scenario_key, "
                " version, world, spec, spec_sha256, engine_version, "
                " frozen_at) VALUES ('twinscn:bogus', 'X', 1, 'RECORDED', "
                " '{}', repeat('a', 64), 'v', now())")
        await sp.rollback()
    finally:
        await tr.rollback()
        await conn.close()


@pg
async def test_the_database_refuses_a_trace_that_read_the_future():
    conn = await asyncpg.connect(H.DSN)
    tr = conn.transaction()
    await tr.start()
    try:
        cat = SC.catalog()
        await ST.register_scenarios(conn, cat, now=TF.T0,
                                    engine_version=E.VERSION)
        r = E.run_scenario(TF.stream(), CAT["RECORDED"])
        rid = await ST.save_result(conn, run_id="twinrun:t", now=TF.T0,
                                   body=r["body"], traces=r["traces"])
        assert rid
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO twin_decision_traces (result_id, seq, "
                " subject_id, decision_kind, decision_at, max_input_at, "
                " inputs_read, world_action, pnl_usd) VALUES ($1, 9999, 'x',"
                " 'ENTRY', to_timestamp($2), to_timestamp($2 + 1), 1, "
                " 'ENTER', 1.0)", rid, TF.T0)
    finally:
        await tr.rollback()
        await conn.close()


async def _seed(conn, now):
    acct = await H.new_account(conn, "twinrun", now=now - 86400)
    exp = F.uid("TWIN_EXP_")
    vid = await F.valuation(conn, experiment_id=exp, at=now - 4000, p=0.62,
                            outcome=1)
    d = await F.decision(conn, acct, at=now - 600, p=0.62, vwap=0.52,
                         fees_usd=1.0, qty=100, depth=500.0, valuation_id=vid)
    g = await F.position(conn, acct, slug=d["slug"], qty=100, price=0.53,
                         fee=1.0, at=now - 590, decision_id=d["decision_id"],
                         event_key=d["event_key"])
    await F.book(conn, d["slug"], now - 595, bids=((0.55, 100),),
                 offers=((0.57, 100),))
    return acct, d, g


@pg
async def test_a_cycle_is_reproducible_and_does_not_duplicate():
    conn = await asyncpg.connect(H.DSN)
    tr = conn.transaction()
    await tr.start()
    try:
        now = time.time()
        acct, d, g = await _seed(conn, now)
        a = await RUN.run_cycle(conn, now=now, account_id=acct["account_id"],
                                include_actual=False)
        assert a["components"]["TWIN"] == "OK", a
        first = {r["scenario_id"]: r["output_sha256"] for r in
                 await conn.fetch(
                     "SELECT scenario_id, output_sha256 FROM "
                     " twin_scenario_results WHERE run_id = $1", a["run_id"])}
        assert len(first) == len(SC.CATALOG)
        b = await RUN.run_cycle(conn, now=now, account_id=acct["account_id"],
                                include_actual=False)
        assert b["components"]["TWIN"] == "OK", b
        assert await conn.fetchval(
            "SELECT count(*) FROM twin_scenario_results WHERE run_id=$1",
            b["run_id"]) == 0                # unchanged inputs: deduplicated
        summ = await conn.fetchval(
            "SELECT summary FROM twin_runs WHERE run_id=$1 AND "
            " component='TWIN'", b["run_id"])
        assert '"inserted": 0' in summ
        # a new recorded fact (the settlement) is a new input set
        await F.settle(conn, acct, group_id=g, slug=d["slug"], qty=100,
                       outcome="WON", payout_per_contract=1.0, at=now - 10)
        c = await RUN.run_cycle(conn, now=now, account_id=acct["account_id"],
                                include_actual=False)
        rows = {r["scenario_id"]: dict(r) for r in await conn.fetch(
            "SELECT scenario_id, output_sha256, world FROM "
            " twin_scenario_results WHERE run_id = $1", c["run_id"])}
        rec = CAT["RECORDED"]["scenario_id"]
        assert rows[rec]["output_sha256"] != first[rec]
        trace = await conn.fetchrow(
            "SELECT t.pnl_usd, t.decision_at, t.max_input_at FROM "
            " twin_decision_traces t JOIN twin_scenario_results r USING "
            " (result_id) WHERE r.run_id=$1 AND r.scenario_id=$2",
            c["run_id"], rec)
        assert trace["pnl_usd"] == pytest.approx(46.0)
        assert trace["max_input_at"] <= trace["decision_at"]
    finally:
        await tr.rollback()
        await conn.close()


@pg
async def test_the_actual_book_is_replayed_separately_and_never_summed():
    conn = await asyncpg.connect(H.DSN)
    tr = conn.transaction()
    await tr.start()
    try:
        now = time.time()
        acct = await H.new_account(conn, "twinact", now=now - 86400)
        d = await F.decision(conn, acct, at=now - 5000, p=0.62, side="SHORT",
                             vwap=0.40, qty=1000)
        g = "paper_group_" + F.uid()
        mid, iid = F.uid("exm-"), F.uid("int-")
        await conn.execute(
            "INSERT INTO execution_intents (intent_id, decision_id, strategy,"
            " us_market_slug, order_intent, group_id, order_type, "
            " time_in_force, paper_target_qty, limit_price, wire_price, "
            " decided_at, live_eligible, actual_state, actual_refusal, "
            " actual_mirror_id, holding_side) VALUES ($1,$2,$3,$4,"
            " 'ORDER_INTENT_BUY_SHORT',$5,'MARKETABLE','IOC',1000,0.40,0.60,"
            " to_timestamp($6),false,'PAPER_ONLY','TEST',$7,'SHORT')",
            iid, d["decision_id"], F.STRATEGY, d["slug"], g, now - 5000, mid)
        await conn.execute(
            "INSERT INTO execmirror_orders (mirror_id, group_id, role, "
            " us_market_slug, intent, order_type, tif, live_qty, state, "
            " cum_qty) VALUES ($1,$2,'ENTRY',$3,'ORDER_INTENT_BUY_SHORT',"
            " 'LIMIT','IOC',1,'FILLED',1)", mid, g, d["slug"])
        # the venue's LONG-side wire price 0.58 is a SHORT cost of 0.42
        await conn.execute(
            "INSERT INTO execmirror_fills (fill_key, mirror_id, "
            " venue_order_id, group_id, us_market_slug, intent, qty, price, "
            " fee_usd, observed_at) VALUES ($1,$2,'v1',$3,$4,"
            " 'ORDER_INTENT_BUY_SHORT',1,0.58,0.01,to_timestamp($5))",
            F.uid("fk-"), mid, g, d["slug"], now - 4990)
        await F.settle(conn, acct, group_id=g, slug=d["slug"], side="SHORT",
                       qty=1, outcome="WON", payout_per_contract=1.0,
                       at=now - 100)
        got = await RUN.run_cycle(conn, now=now,
                                  account_id=acct["account_id"],
                                  include_actual=True)
        assert got["components"]["TWIN"] == "OK", got
        rec = CAT["RECORDED"]["scenario_id"]
        rows = {r["basis_book"]: dict(r) for r in await conn.fetch(
            "SELECT basis_book, baseline, world, summed_across_books FROM "
            " twin_scenario_results WHERE run_id=$1 AND scenario_id=$2",
            got["run_id"], rec)}
        assert set(rows) == {"PAPER", "ACTUAL"}
        act = json.loads(rows["ACTUAL"]["baseline"])
        assert act["label"] == "ACTUAL"
        assert act["total_pnl_usd"] == pytest.approx(1 - 0.42 - 0.01)
        assert json.loads(rows["ACTUAL"]["world"])["label"] == (
            "COUNTERFACTUAL")
        assert all(r["summed_across_books"] is False for r in rows.values())
    finally:
        await tr.rollback()
        await conn.close()
