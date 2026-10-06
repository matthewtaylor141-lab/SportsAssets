"""PAPER TURNAROUND: THE STRATEGY LIFECYCLE (migration 290).

  §1 THE DECLARATION (pure): the states, the predeclared constants, their
     hash, the known strategies; no rule can raise a size or a cap.
  §2 THE RULES (pure): every stopping rule fires at its declared threshold
     with its evidence; automatic moves only tighten; RETIRED never moves;
     upward moves need a named person, the declared one step and FORWARD
     evidence (never a short-term profit alone).
  §3 THE METRICS (pure): losses are never excluded; an empty window is
     UNAVAILABLE (None), never zero; drawdown, $/capital-hour, execution cost.
  §4 REALLOCATION (pure): only toward forward evidence; else CASH; no cap.
  §5 THE DATABASE: the event table is append-only with PAPER /
     PAPER_ONLY_NO_CAPITAL_AUTHORITY; a QUARANTINED / SHADOW_ONLY / RETIRED
     strategy's ENTER is refused at the ledger; REDUCED_SIZE caps the order;
     the automatic evaluator demotes on the loss budget and records the
     evidence; the stale-management rate refuses new entries; the decision
     gate halves a REDUCED_SIZE decision.
  §6 THE AUTHORITY: GET /api/command/paper/turnaround is GET only, 401
     without a command session, holds no SQL write, imports no order / venue
     / funded module; an unreadable answer is UNAVAILABLE, never zeros.
ALL DATA HERE IS SYNTHETIC TEST DATA.
"""
from __future__ import annotations

import ast
import itertools
import json
import pathlib
import re

import pytest

from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_strategy_lifecycle as LC

try:
    from tests import paper_harness as H
except ImportError:                                             # pragma: no cover
    import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "sportsassets"
NOW = 1_791_500_000.0
DAY = 86400.0


def pos(pnl, *, at, cost=500.0, hours=10.0, strategy="S", fees=0.0,
        open_qty=0.0):
    return {"strategy": strategy, "realized_pnl_usd": pnl,
            "acquisition_cost_usd": cost, "buy_fees_usd": fees,
            "sale_fees_usd": 0.0, "open_qty": open_qty,
            "cost_basis_usd": cost if open_qty else 0.0,
            "first_fill_at": at - hours * 3600.0, "last_fill_at": at - 1,
            "settlement": None if open_qty else {"settled_at": at}}


# ── §1 the declaration ───────────────────────────────────────────────

def test_the_states_and_their_order():
    assert LC.STATES == ("ACTIVE_CHAMPION", "ACTIVE_CHALLENGER",
                         "REDUCED_SIZE", "SHADOW_ONLY", "QUARANTINED",
                         "RETIRED")
    assert LC.INITIAL_STATE == LC.ACTIVE_CHALLENGER
    assert set(LC.NO_ENTRY_STATES) == {"SHADOW_ONLY", "QUARANTINED",
                                       "RETIRED"}


def test_the_rules_are_predeclared_versioned_and_hashed():
    import hashlib
    assert LC.RULES_VERSION == "PAPER_TURNAROUND_RULES_V1"
    assert LC.RULES_SHA == hashlib.sha256(json.dumps(
        LC.RULES, sort_keys=True).encode()).hexdigest()
    for k, v in LC.RULES.items():
        if k != "version":
            assert isinstance(v, (int, float)), k
    assert 0 < LC.REDUCED_SIZE_FACTOR <= 1
    assert LC.LOSS_BUDGET_REDUCE_USD < LC.LOSS_BUDGET_SHADOW_USD \
        < LC.LOSS_BUDGET_QUARANTINE_USD


def test_the_known_strategies_are_the_paper_strategies():
    from sportsassets import bettor_paper_ops as OPS
    assert set(LC.KNOWN_STRATEGIES) == set(OPS.STRATEGY_ORDER)


@pytest.mark.parametrize("state", LC.STATES)
def test_no_state_can_raise_a_size(state):
    assert LC.decision_size_factor(state) <= 1.0
    for qty, limit, fee in itertools.product(
            (0, 1, 3, 999, 2000, 10**6), (0.01, 0.4, 0.99), (0.0, 0.02)):
        assert LC.ledger_qty_cap(state, qty=qty, limit=limit,
                                 max_fee_per_contract=fee) <= qty


def test_reduced_size_halves_and_caps_per_the_declared_rule():
    assert LC.decision_size_factor(LC.REDUCED_SIZE) == 0.5
    assert LC.decision_size_factor(LC.ACTIVE_CHALLENGER) == 1.0
    assert LC.decision_size_factor(LC.QUARANTINED) == 0.0
    assert LC.ledger_qty_cap(LC.REDUCED_SIZE, qty=2000, limit=0.40) == 1250
    assert LC.ledger_qty_cap(LC.REDUCED_SIZE, qty=100, limit=0.40) == 100
    assert LC.ledger_qty_cap(LC.ACTIVE_CHAMPION, qty=2000, limit=0.4) == 2000


def test_the_lifecycle_reads_and_writes_no_cap_and_no_live_path():
    forbidden = ("venue", "kalshi", "execmirror", "live_executor", "funded",
                 "smalllive", "live_parity", "pmus", "execution")
    for rel in ("bettor_strategy_lifecycle.py", "bettor_stale_management.py",
                "api/command_turnaround.py"):
        src = (PKG / rel).read_text()
        for imp in _imports(PKG / rel):
            leaf = imp.rsplit(".", 1)[-1].lower()
            assert not any(f in leaf for f in forbidden), (rel, imp)
        for cap in ("per_order_cap_usd", "per_market_cap_usd",
                    "per_fixture_cap_usd", "SMALLLIVE", "smalllive_cap",
                    "PAPER_ENTRIES:", "STRATEGY_SLEEVE"):
            assert cap not in src, (rel, cap)
    # the only table the lifecycle module writes is its own event log
    src = (PKG / "bettor_strategy_lifecycle.py").read_text()
    assert set(re.findall(r"INSERT INTO\s+([a-z_]+)", src)) == {
        "paper_strategy_lifecycle_events"}
    assert not re.search(r"\bUPDATE\s+[a-z_]+\s+SET|DELETE\s+FROM", src)


# ── §2 the rules ─────────────────────────────────────────────────────

def _m(pnls, *, n_open=0):
    return LC.metrics([pos(x, at=NOW - 3600 * (i + 1))
                       for i, x in enumerate(pnls)], now=NOW)


@pytest.mark.parametrize("pnls,state,rule", [
    ([-2500.0], LC.REDUCED_SIZE, LC.RULE_LOSS_REDUCE),
    ([-5000.0], LC.SHADOW_ONLY, LC.RULE_LOSS_SHADOW),
    ([-10000.0], LC.QUARANTINED, LC.RULE_LOSS_QUARANTINE),
    ([5000.0, -12500.0, 8000.0], LC.QUARANTINED, LC.RULE_DRAWDOWN_QUARANTINE),
    ([-2499.0], None, None),
])
def test_each_loss_rule_fires_at_its_declared_threshold(pnls, state, rule):
    m = LC.metrics([pos(x, at=NOW - 3600 * (len(pnls) - i))
                    for i, x in enumerate(pnls)], now=NOW)
    t = LC.automatic_transition(LC.ACTIVE_CHALLENGER, m)
    if state is None:
        assert t is None
        return
    assert t["to_state"] == state and t["rule_id"] == rule
    assert t["actor"] == LC.AUTOMATIC_ACTOR
    assert t["rules_firing"][0]["evidence"]


def test_negative_edge_and_drawdown_rate_need_the_declared_sample():
    few = _m([-10.0] * (LC.MIN_CLOSED_FOR_RATE_RULES - 1))
    assert LC.automatic_transition(LC.ACTIVE_CHALLENGER, few) is None
    many = _m([-60.0] * (LC.MIN_CLOSED_FOR_RATE_RULES + 5))
    assert many["realized_pnl_usd"] > -LC.LOSS_BUDGET_REDUCE_USD
    t = LC.automatic_transition(LC.ACTIVE_CHALLENGER, many)
    assert t["to_state"] == LC.REDUCED_SIZE
    assert {r["rule_id"] for r in t["rules_firing"]} >= {
        LC.RULE_NEGATIVE_EDGE, LC.RULE_DRAWDOWN_RATE}


def test_automatic_moves_only_ever_tighten_and_retired_never_moves():
    cases = [[-2500.0], [-5000.0], [-10000.0], [100.0] * 40,
             [-10.0, -12.0] * 30, []]
    for cur in LC.STATES:
        for pnls in cases:
            t = LC.automatic_transition(cur, _m(pnls))
            if t is not None:
                assert LC.RANK[t["to_state"]] > LC.RANK[cur], (cur, t)
                assert cur != LC.RETIRED
    # a strategy already stricter than the rule requires is left there
    assert LC.automatic_transition(LC.QUARANTINED, _m([-2500.0])) is None


def test_a_champion_whose_forward_evidence_lapses_becomes_a_challenger():
    fwd = _m([1.0] * 5)
    t = LC.automatic_transition(LC.ACTIVE_CHAMPION, _m([1.0]), forward=fwd)
    assert t["to_state"] == LC.ACTIVE_CHALLENGER
    assert t["rule_id"] == LC.RULE_CHAMPION_LAPSED


def test_upward_moves_need_a_person_the_one_step_and_forward_evidence():
    flat, good = _m([]), _m([20.0, 25.0, 30.0] * 12)
    P = "person:owner"
    ck = LC.check_manual
    assert ck(LC.QUARANTINED, LC.SHADOW_ONLY, actor=LC.AUTOMATIC_ACTOR,
              m=flat, forward=flat) == LC.R_TRANSITION_NEEDS_PERSON
    assert ck(LC.QUARANTINED, LC.SHADOW_ONLY, actor="person:", m=flat,
              forward=flat) == LC.R_TRANSITION_NEEDS_PERSON
    assert ck(LC.QUARANTINED, LC.SHADOW_ONLY, actor=P, m=flat,
              forward=flat) is None
    assert ck(LC.QUARANTINED, LC.ACTIVE_CHALLENGER, actor=P, m=flat,
              forward=good) == LC.R_TRANSITION_NOT_ONE_STEP
    assert ck(LC.RETIRED, LC.SHADOW_ONLY, actor=P, m=flat,
              forward=good) == LC.R_TRANSITION_RETIRED_TERMINAL
    assert ck(LC.REDUCED_SIZE, LC.REDUCED_SIZE, actor=P, m=flat,
              forward=good) == LC.R_TRANSITION_NO_CHANGE
    # short-term profit alone does not restore or promote
    short = _m([500.0, 400.0])
    assert ck(LC.REDUCED_SIZE, LC.ACTIVE_CHALLENGER, actor=P, m=short,
              forward=short) == LC.R_TRANSITION_NO_FORWARD_EVIDENCE
    assert ck(LC.ACTIVE_CHALLENGER, LC.ACTIVE_CHAMPION, actor=P, m=short,
              forward=short) == LC.R_TRANSITION_NO_FORWARD_EVIDENCE
    assert ck(LC.ACTIVE_CHALLENGER, LC.ACTIVE_CHAMPION, actor=P, m=good,
              forward=good) is None
    # a rule still firing blocks a restoration
    losing = _m([-3000.0])
    assert ck(LC.REDUCED_SIZE, LC.ACTIVE_CHALLENGER, actor=P, m=losing,
              forward=good) == LC.R_TRANSITION_RULE_FIRING
    # tightening: any named person, any time
    assert ck(LC.ACTIVE_CHAMPION, LC.RETIRED, actor=P, m=good,
              forward=good) is None


# ── §3 the metrics ───────────────────────────────────────────────────

def test_losses_are_counted_and_an_empty_window_is_unavailable():
    m = LC.metrics([pos(-100.0, at=NOW - 3600, fees=2.0),
                    pos(50.0, at=NOW - 1800, fees=1.0),
                    pos(-999.0, at=NOW - 30 * DAY),          # outside window
                    pos(0.0, at=NOW, open_qty=5.0)], now=NOW)
    assert m["closed_positions"] == 2 and m["losses"] == 1
    assert m["realized_pnl_usd"] == -50.0
    assert m["losses_excluded"] == 0
    assert m["max_drawdown_usd"] == 100.0
    assert m["capital_hours"] == pytest.approx(2 * 500.0 * 10.0, rel=1e-3)
    assert m["dollars_per_capital_hour"] == pytest.approx(
        -50.0 / m["capital_hours"])
    assert m["execution_cost_usd"] == 3.0
    assert m["execution_cost_rate"] == pytest.approx(3.0 / 1000.0)
    assert m["drawdown_rate"] == pytest.approx(0.1)
    assert m["open_positions"] == 1
    empty = LC.metrics([], now=NOW)
    for k in ("realized_pnl_usd", "dollars_per_capital_hour",
              "max_drawdown_usd", "drawdown_rate", "execution_cost_usd"):
        assert empty[k] is None, k


def test_forward_evidence_starts_after_the_declaration():
    old = pos(1000.0, at=LC.RULES_DECLARED_AT - DAY, hours=1)
    new = pos(-5.0, at=LC.RULES_DECLARED_AT + DAY, hours=1)
    f = LC.metrics([old, new], now=LC.RULES_DECLARED_AT + 2 * DAY,
                   window_days=3650,
                   since=LC.forward_since(None))
    assert f["closed_positions"] == 1 and f["realized_pnl_usd"] == -5.0


# ── §4 reallocation ──────────────────────────────────────────────────

def test_capital_hours_go_only_toward_better_forward_evidence():
    good = _m([20.0, 25.0, 30.0] * 12)
    short = _m([900.0, 800.0])
    rows = {
        "LOSER": {"state": LC.QUARANTINED, "forward": _m([-50.0] * 3),
                  "capital_shifted_after_demotion": {
                      "capital_hours_per_day_before": 1000.0}},
        "PROVEN": {"state": LC.ACTIVE_CHALLENGER, "forward": good},
        "LUCKY": {"state": LC.ACTIVE_CHALLENGER, "forward": short},
        "SHADOW": {"state": LC.SHADOW_ONLY, "forward": good}}
    plan = LC.reallocation_plan(rows)
    assert plan["caps_changed"] is False and plan["paper_only"] is True
    assert [r["strategy"] for r in plan["eligible_recipients"]] == ["PROVEN"]
    p = {x["donor"]: x for x in plan["plan"]}
    assert p["LOSER"]["to"] == [{"strategy": "PROVEN", "share": 1.0}]
    only_lucky = LC.reallocation_plan({k: rows[k] for k in ("LOSER",
                                                            "LUCKY")})
    assert only_lucky["plan"][0]["to"] == []
    assert only_lucky["plan"][0]["otherwise"] == "CASH"


def test_capital_shifted_after_demotion_is_measured_or_says_why():
    d = NOW - 10 * DAY
    ps = [dict(pos(0.0, at=d - 2 * DAY), first_fill_at=d - 3 * DAY),
          dict(pos(0.0, at=d + 2 * DAY, cost=100.0),
               first_fill_at=d + DAY)]
    got = LC.capital_shifted(ps, demoted_at=d, now=NOW)
    assert got["status"] == "MEASURED"
    assert got["before_usd_per_day"] == pytest.approx(500.0 / 7)
    assert got["after_usd_per_day"] == pytest.approx(100.0 / 7)
    assert LC.capital_shifted(ps, demoted_at=None, now=NOW)[
        "shifted_usd_per_day"] is None


# ── §5 the database ──────────────────────────────────────────────────

async def _tx():
    conn = await H.connect()
    tr = conn.transaction()
    await tr.start()
    return conn, tr


async def _done(conn, tr):
    await tr.rollback()
    await conn.close()


async def _state(conn, acct, strategy, to, *, at=NOW, frm=None):
    return await LC.record(conn, account_id=acct, strategy=strategy,
                           from_state=frm, to_state=to, rule_id="TEST",
                           actor="person:test", evidence={"t": 1},
                           why="test", at=at)


def _order(a, *, key, qty=100, limit=0.40, strategy="PINNACLE_COMPLETED_GAME_PAPER", at=NOW,
           slug=None):
    o = H.order(a, key=key, qty=qty, limit=limit, at=at,
                slug=slug or "%s:%s" % (a["account_id"], key),
                group_id="paper_g_%s_%s" % (a["account_id"][-6:], key),
                fixture="fx-%s" % key)
    o["strategy"] = strategy
    return o


async def _filled(conn, a, *, key, qty, limit, at, strategy, outcome=None):
    from sportsassets import bettor_paper_simulator as SIM
    o = _order(a, key=key, qty=qty, limit=limit, at=at, strategy=strategy)
    got = await L.submit_order(conn, o, fee_fn=H.zero_fee, now=at)
    assert got["ok"], got
    await H.observe(conn, o["us_market_slug"], at + 3,
                    offers=[(limit, qty)], bids=[(limit - 0.02, qty)])
    await SIM.simulate_order(conn, got["order"]["order_id"], now=at + 4,
                             fee_fn=H.zero_fee)
    if outcome:
        s = await L.settle(conn, account_id=a["account_id"],
                           group_id=o["group_id"], slug=o["us_market_slug"],
                           holding_side="LONG",
                           settlement_event_key="ev-%s" % key,
                           outcome=outcome, evidence={"t": 1},
                           evidence_source="TEST", at=at + 60)
        assert s["ok"], s
    return o


@pg
async def test_the_event_table_is_append_only_and_paper_only():
    import asyncpg
    conn, tr = await _tx()
    try:
        eid = await _state(conn, "paper_t_lc", "S", LC.QUARANTINED)
        for sql in ("UPDATE paper_strategy_lifecycle_events SET to_state = "
                    "'ACTIVE_CHAMPION' WHERE event_id = %d" % eid,
                    "DELETE FROM paper_strategy_lifecycle_events WHERE "
                    "event_id = %d" % eid,
                    "TRUNCATE paper_strategy_lifecycle_events"):
            with pytest.raises(asyncpg.PostgresError):
                async with conn.transaction():
                    await conn.execute(sql)
        base = ("INSERT INTO paper_strategy_lifecycle_events (account_id, "
                " strategy, from_state, to_state, rule_id, rules_version, "
                " rules_sha, actor, evidence, why%s) VALUES ('a','s',%s,"
                " %s,'r','v','h',%s,'{}'::jsonb,'w'%s)")
        for extra_col, frm, to, actor, extra_val in (
                ("", "NULL", "'LIVE'", "'person:x'", ""),
                ("", "'REDUCED_SIZE'", "'REDUCED_SIZE'", "'person:x'", ""),
                ("", "NULL", "'QUARANTINED'", "'someone'", ""),
                (", label", "NULL", "'QUARANTINED'", "'person:x'", ",'LIVE'"),
                (", authority", "NULL", "'QUARANTINED'", "'person:x'",
                 ",'CAPITAL'")):
            with pytest.raises(asyncpg.CheckViolationError):
                async with conn.transaction():
                    await conn.execute(base % (extra_col, frm, to, actor,
                                               extra_val))
        # the evaluator can only tighten, enforced at the write too
        with pytest.raises(ValueError):
            await LC.record(conn, account_id="a", strategy="s",
                            from_state=LC.QUARANTINED,
                            to_state=LC.ACTIVE_CHALLENGER, rule_id="r",
                            actor=LC.AUTOMATIC_ACTOR, evidence={}, why="w",
                            at=NOW)
    finally:
        await _done(conn, tr)


@pg
@pytest.mark.parametrize("state,refusal", [
    (LC.QUARANTINED, LC.R_LIFECYCLE_QUARANTINED),
    (LC.SHADOW_ONLY, LC.R_LIFECYCLE_SHADOW_ONLY),
    (LC.RETIRED, LC.R_LIFECYCLE_RETIRED)])
async def test_a_no_entry_state_refuses_the_entry_at_the_ledger(state,
                                                               refusal):
    conn, tr = await _tx()
    try:
        a = await H.new_account(conn, "lcq", now=NOW - 60)
        await _state(conn, a["account_id"], "PINNACLE_COMPLETED_GAME_PAPER", state)
        got = await L.submit_order(conn, _order(a, key="q1"),
                                   fee_fn=H.zero_fee, now=NOW)
        assert got["ok"] is False and got["refusal"] == refusal
        assert got["under_lock"] is True
        n = await conn.fetchval("SELECT count(*) FROM paper_orders WHERE "
                                "account_id = $1", a["account_id"])
        assert n == 0
        cs = await L.cash_state(conn, a["account_id"])
        assert float(cs["reserved"]) == 0.0
        # another strategy on the same account is unaffected
        ok = await L.submit_order(conn, _order(a, key="q2",
                                               strategy="PINNACLE_EXPLORATION_PAPER"),
                                  fee_fn=H.zero_fee, now=NOW)
        assert ok["ok"], ok
        # a SELL (management of a held position) is never blocked here
        assert LC.ENTRY_STATE_REFUSAL[state] == refusal
    finally:
        await _done(conn, tr)


@pg
async def test_reduced_size_caps_the_order_and_active_does_not():
    conn, tr = await _tx()
    try:
        a = await H.new_account(conn, "lcr", now=NOW - 60)
        full = await L.submit_order(conn, _order(a, key="r0", qty=2000),
                                    fee_fn=H.zero_fee, now=NOW)
        assert full["ok"] and full["order"]["qty"] == 2000
        await _state(conn, a["account_id"], "PINNACLE_COMPLETED_GAME_PAPER", LC.REDUCED_SIZE)
        got = await L.submit_order(conn, _order(a, key="r1", qty=2000),
                                   fee_fn=H.zero_fee, now=NOW)
        assert got["ok"], got
        assert got["order"]["qty"] == 1250                 # $500 / 0.40
        assert float(got["order"]["reserved_usd"]) <= \
            LC.REDUCED_SIZE_MAX_ORDER_USD
        ev = await conn.fetchval(
            "SELECT detail FROM paper_order_events WHERE order_id = $1 "
            "   AND kind = 'SUBMITTED'", got["order"]["order_id"])
        ev = json.loads(ev) if isinstance(ev, str) else ev
        assert ev["lifecycle"]["state"] == LC.REDUCED_SIZE
        assert ev["lifecycle"]["capped"] is True
        small = await L.submit_order(conn, _order(a, key="r2", qty=10),
                                     fee_fn=H.zero_fee, now=NOW)
        assert small["ok"] and small["order"]["qty"] == 10
    finally:
        await _done(conn, tr)


@pg
async def test_an_unreadable_lifecycle_refuses_the_entry(monkeypatch):
    conn, tr = await _tx()
    try:
        a = await H.new_account(conn, "lcu", now=NOW - 60)

        async def no_schema(conn):
            return False
        monkeypatch.setattr(LC, "schema", no_schema)
        got = await L.submit_order(conn, _order(a, key="u1"),
                                   fee_fn=H.zero_fee, now=NOW)
        assert got["refusal"] == LC.R_LIFECYCLE_UNREADABLE
    finally:
        await _done(conn, tr)


@pg
async def test_the_evaluator_demotes_on_the_loss_budget_with_its_evidence():
    conn, tr = await _tx()
    try:
        a = await H.new_account(conn, "lce", now=NOW - 7200)
        acct = a["account_id"]
        S = "PINNACLE_ONLY_PAPER_BENCHMARK"
        for i in range(4):                     # 4 x $2,500 lost
            await _filled(conn, a, key="l%d" % i, qty=5000, limit=0.50,
                          at=NOW - 3600 + i * 10, strategy=S,
                          outcome="LOST")
        await _filled(conn, a, key="w0", qty=100, limit=0.50,
                      at=NOW - 3000, strategy="PINNACLE_COMPLETED_GAME_MAKER_PAPER", outcome="WON")
        got = await LC.evaluate_and_record(conn, account_id=acct, now=NOW,
                                           strategies=[S, "PINNACLE_COMPLETED_GAME_MAKER_PAPER"])
        assert [(t["strategy"], t["to_state"], t["rule_id"])
                for t in got["transitions"]] == [
            (S, LC.QUARANTINED, LC.RULE_LOSS_QUARANTINE)]
        row = await conn.fetchrow(
            "SELECT * FROM paper_strategy_lifecycle_events WHERE "
            " account_id = $1", acct)
        ev = json.loads(row["evidence"]) if isinstance(
            row["evidence"], str) else row["evidence"]
        assert row["actor"] == LC.AUTOMATIC_ACTOR
        assert row["from_state"] == LC.ACTIVE_CHALLENGER
        assert row["rules_sha"] == LC.RULES_SHA
        assert ev["rolling"]["realized_pnl_usd"] == pytest.approx(-10000.0)
        assert ev["rolling"]["closed_positions"] == 4
        assert ev["rules"] == LC.RULES
        # idempotent: nothing new while nothing changed
        again = await LC.evaluate_and_record(conn, account_id=acct, now=NOW,
                                             strategies=[S])
        assert again["transitions"] == []
        # and its next ENTER is refused
        r = await L.submit_order(conn, _order(a, key="after", strategy=S),
                                 fee_fn=H.zero_fee, now=NOW + 1)
        assert r["refusal"] == LC.R_LIFECYCLE_QUARANTINED
        # leaving needs a named person, one step at a time
        bad = await LC.transition(conn, account_id=acct, strategy=S,
                                  to_state=LC.ACTIVE_CHALLENGER,
                                  actor="person:owner", why="t", now=NOW + 2)
        assert bad["refusal"] == LC.R_TRANSITION_NOT_ONE_STEP
        ok = await LC.transition(conn, account_id=acct, strategy=S,
                                 to_state=LC.SHADOW_ONLY,
                                 actor="person:owner", why="t", now=NOW + 3)
        assert ok["ok"], ok
        r = await L.submit_order(conn, _order(a, key="after2", strategy=S),
                                 fee_fn=H.zero_fee, now=NOW + 4)
        assert r["refusal"] == LC.R_LIFECYCLE_SHADOW_ONLY
        # the turnaround view shows it, the loss included, nothing zeroed
        view = await LC.turnaround_view(conn, account_id=acct, now=NOW + 5)
        row = view["strategies"][S]
        assert row["state"] == LC.SHADOW_ONLY
        assert row["entry_allowed"] is False
        assert row["rolling"]["realized_pnl_usd"] == pytest.approx(-10000.0)
        for k in ("dollars_per_capital_hour", "max_drawdown_usd",
                  "drawdown_rate", "execution_cost_usd"):
            assert k in row["rolling"]
        assert row["stale_management"]["ok"] is True
        assert row["capital_shifted_after_demotion"]["demoted_at"] == \
            pytest.approx(NOW)
        assert view["losses_hidden_or_reclassified"] == 0
        assert [e["to_state"] for e in view["events"]] == [
            LC.SHADOW_ONLY, LC.QUARANTINED]
        assert view["reallocation"]["caps_changed"] is False
    finally:
        await _done(conn, tr)


@pg
async def test_a_smaller_loss_reduces_size_and_the_runtime_step_is_main_only():
    conn, tr = await _tx()
    try:
        a = await H.new_account(conn, "lcs", now=NOW - 7200)
        await _filled(conn, a, key="l0", qty=5000, limit=0.50,
                      at=NOW - 3600, strategy="DEREK_ENTRY_POLICY_V2", outcome="LOST")
        got = await LC.evaluate_and_record(conn, account_id=a["account_id"],
                                           now=NOW, strategies=["DEREK_ENTRY_POLICY_V2"])
        assert got["transitions"][0]["to_state"] == LC.REDUCED_SIZE
        assert got["transitions"][0]["rule_id"] == LC.RULE_LOSS_REDUCE
        skip = await LC.step(conn, {"account_id": a["account_id"],
                                    "now": NOW})
        assert skip == {"ran": False, "why": "MAIN_PAPER_ACCOUNT_ONLY"}
        from sportsassets.agents import paper_runtime as PR
        names = [n for n, _ in PR.default_steps()]
        assert names.index("turnaround") < names.index("derek")
        assert names[-1] == "agent_memory"
    finally:
        await _done(conn, tr)


@pg
async def test_stale_management_refuses_new_entries_until_marks_are_fresh(
        monkeypatch):
    # the lifecycle's stale-mark rule alone is under test here; the paper
    # freshness management-integrity rail (P0 closeout) has its own tests
    from sportsassets import bettor_paper_freshness as _PMF

    async def _ok(conn, account_id, strategy, *, now):
        return {"refusal": None, "strategy": strategy}
    monkeypatch.setattr(_PMF, "strategy_management_integrity", _ok)
    conn, tr = await _tx()
    try:
        a = await H.new_account(conn, "lcm", now=NOW - 7200)
        S = "DEREK_ENTRY_POLICY_V2"
        keys = ["o%d" % i for i in range(LC.STALE_MANAGEMENT_MIN_OPEN)]
        for i, k in enumerate(keys):
            await _filled(conn, a, key=k, qty=10, limit=0.40,
                          at=NOW - 3000 + i, strategy=S)
        late = NOW
        got = await L.submit_order(conn, _order(a, key="new", strategy=S),
                                   fee_fn=H.zero_fee, now=late)
        assert got["refusal"] == LC.R_STALE_MANAGEMENT_EXCEEDED, got
        assert got["lifecycle"]["stale_management"]["rate"] == 1.0
        # other strategies are not penalised for this one's stale exposure
        other = await L.submit_order(conn, _order(a, key="oth",
                                                  strategy="PINNACLE_EXPLORATION_PAPER"),
                                     fee_fn=H.zero_fee, now=late)
        assert other["ok"], other
        # fresh marks (the freshness lane's job) lift it
        for k in keys:
            await H.observe(conn, "%s:%s" % (a["account_id"], k), late - 5,
                            offers=[(0.41, 10)], bids=[(0.39, 10)])
        ok = await L.submit_order(conn, _order(a, key="new2", strategy=S),
                                  fee_fn=H.zero_fee, now=late)
        assert ok["ok"], ok
    finally:
        await _done(conn, tr)


@pg
async def test_the_decision_gate_refuses_quarantined_and_halves_reduced():
    from sportsassets.agents import paper_derek as PD
    conn, tr = await _tx()
    try:
        a = await H.new_account(conn, "lcd", now=NOW - 60)
        ctx = {"account_id": a["account_id"]}
        cand = {"us_market_slug": "m", "payout_event": "HOME",
                "fixture": "fx", "settlement": {"compatibility":
                                                "COMPATIBLE"}}
        kw = dict(strategy="PINNACLE_ONLY_PAPER_BENCHMARK", p=0.60,
                  levels=[{"price": 0.40, "qty": 1000}],
                  sized={"qty": 300, "limit": 0.40}, cand=cand, side="LONG",
                  at=NOW, fee_fn=H.zero_fee)
        ce = await PD.capital_gate(conn, ctx, **kw)
        assert ce["capital_eligible"] and ce["qty"] == 300
        await _state(conn, a["account_id"], "PINNACLE_ONLY_PAPER_BENCHMARK", LC.REDUCED_SIZE)
        ce = await PD.capital_gate(conn, ctx, **kw)
        assert ce["capital_eligible"] and ce["qty"] == 150
        await _state(conn, a["account_id"], "PINNACLE_ONLY_PAPER_BENCHMARK", LC.QUARANTINED,
                     frm=LC.REDUCED_SIZE)
        ce = await PD.capital_gate(conn, ctx, **kw)
        assert ce["capital_eligible"] is False
        assert ce["refusals"] == [LC.R_LIFECYCLE_QUARANTINED]
        assert ce["decision"] == "CASH_WAIT" and ce["allocation_usd"] == 0.0
    finally:
        await _done(conn, tr)


# ── §6 the authority ─────────────────────────────────────────────────

def _imports(path):
    out = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom):
            out.add(node.module or "")
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
    return out


def test_the_turnaround_route_is_get_only_and_needs_a_command_session():
    from fastapi.testclient import TestClient

    from sportsassets.api import app as APP
    from sportsassets.api import command_turnaround as CT
    assert CT.PATH == "/api/command/paper/turnaround"
    paths, stack = {}, list(APP.app.routes)
    while stack:
        r = stack.pop()
        if hasattr(r, "original_router"):
            stack.extend(r.original_router.routes)
        elif getattr(r, "path", "") == CT.PATH:
            paths[r.path] = set(getattr(r, "methods", set()) or set())
    assert paths and all(m <= {"GET", "HEAD"} for m in paths.values()), paths
    client = TestClient(APP.app, raise_server_exceptions=False)
    assert client.get(CT.PATH).status_code == 401
    for verb in (client.post, client.put, client.delete, client.patch):
        assert verb(CT.PATH).status_code in (401, 405)


def test_the_turnaround_route_holds_no_write_and_no_order_path():
    write = re.compile(r"\b(INSERT\s+INTO|UPDATE\s+[a-z_]+\s+SET|DELETE\s+"
                       r"FROM|TRUNCATE|ALTER\s+TABLE|DROP\s+TABLE)", re.I)
    src = (PKG / "api/command_turnaround.py").read_text()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert not write.search(node.value), node.value[:60]
    for word in ("submit_order", "place", "cancel", "KalshiClient",
                 "record(", "transition("):
        assert word not in src, word
    assert "readonly=True" in src


async def test_an_unreadable_turnaround_is_unavailable_never_zero(
        monkeypatch):
    from sportsassets.api import command_turnaround as CT

    class Boom:
        async def acquire(self):
            raise RuntimeError("db down")

    async def pool():
        raise RuntimeError("db down")
    monkeypatch.setattr(CT, "_pool", pool)
    CT._CACHE.clear()
    got = await CT.paper_turnaround()
    assert got["status"] == "UNAVAILABLE" and got["data"] is None
    assert got["authority"] == "PAPER_ONLY_NO_CAPITAL_AUTHORITY"


@pg
async def test_the_turnaround_read_is_unavailable_without_the_migration(
        monkeypatch):
    from sportsassets.api import command_turnaround as CT
    conn = await H.connect()
    try:
        async def no_schema(conn):
            return False
        monkeypatch.setattr(LC, "schema", no_schema)
        got = await CT.read(conn, account_id="paper_t_none", now=NOW)
        assert got["status"] == "UNAVAILABLE" and got["data"] is None
        assert got["why"] == "MIGRATION_290_NOT_APPLIED"
    finally:
        await conn.close()


@pg
async def test_the_turnaround_read_runs_read_only_and_names_kalshi():
    from sportsassets.api import command_turnaround as CT
    conn = await H.connect()
    try:
        got = await CT.read(conn, account_id="paper_t_none_%d" % NOW,
                            now=NOW)
        assert got["status"] == "OK"
        d = got["data"]
        assert set(d["strategies"]) >= set(LC.KNOWN_STRATEGIES)
        for row in d["strategies"].values():
            assert row["state"] == LC.ACTIVE_CHALLENGER
            assert row["rolling"]["realized_pnl_usd"] is None   # not zero
        assert d["kalshi_read_path"]["status"] == "BLOCKED"
        assert d["rules_sha"] == LC.RULES_SHA
    finally:
        await conn.close()


def test_this_proof_is_registered_capital_critical():
    listed = (ROOT / "tools" / "capital_critical_tests.txt").read_text()
    assert "tests/test_strategy_lifecycle.py" in listed.splitlines()
