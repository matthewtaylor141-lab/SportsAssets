"""THE COMPLETED-GAME PAPER POLICY (PINNACLE_COMPLETED_GAME_PAPER_V1),
THROUGH THE REAL ORCHESTRATION.

An EXPERIMENTAL paper policy beside the strict benchmark. It enters only on
an exact match of fixture, participant, selected outcome, market, line and
the ORDINARY completed-game grading period; the venue's and the book's terms
for postponed, abandoned or suspended games are carried as disclosed research
risks, never as proven settlement compatibility. Its economics are
conditional on ordinary completion; exceptional payoffs are shown apart with
their probabilities UNMEASURED; an exceptional settlement is paid at the
venue's own published price, never an assumed refund.

SYNTHETIC: valuations come from `paper_live_fixture.valuation`, books from the
fixture transport; the venue texts are the venue's recorded wording (admin
probe, 2026-10-01 11:53:00Z). No real money, no venue order: the market-data
client counts mutation attempts and every proof asserts zero.
"""
from __future__ import annotations

import json
import time

import pytest

from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_settlement_terms as ST
from sportsassets.agents import paper_benchmark as PB
from sportsassets.agents import paper_derek as PD
from sportsassets.agents import paper_runtime as PR

from tests import paper_harness as H
from tests import paper_live_fixture as PL

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
FEE = H.flat_fee(0.01)
CG = PB.CG_STRATEGY

#: The venue's recorded Nations League text (admin probe, 11:53:00Z).
GRE_GER = (
    "This market will settle to the winner at the end of 90 minutes plus "
    "stoppage time in the Greece vs Germany UEFA Nations League match "
    "scheduled for 2026-10-04 2:45PM ET. If the match is tied following 90 "
    "minutes plus stoppage time, the market will settle to Tie. If the match "
    "is delayed, postponed, or suspended and not rescheduled to a date within "
    "two weeks of the originally scheduled date, the market will settle to "
    "the last fair market price. Outcome sourced from UEFA.")

#: A SYNTHETIC regulation-only baseball text (no venue's wording): the
#: ordinary game is graded after nine innings, so it does NOT match the
#: book's completed-game money line, which includes extra innings.
REGULATION_ONLY_BASEBALL = (
    "This market will settle to the winner of the A vs B MLB game after 9 "
    "innings. Extra innings are not included. Outcome sourced from MLB.")


async def _nosleep(_):
    return None


async def _pass(conn, acct, transport, now, **kw):
    transport.t = max(transport.t, float(now))
    return await PR.paper_pass(conn, now=now, account_id=acct["account_id"],
                               market_data=kw.pop("client", None)
                               or PL.client(transport),
                               config=acct["config"], force=True,
                               fee_fn=FEE, sleep=_nosleep, **kw)


@pytest.fixture
def both_on(monkeypatch, new_strategies_off):
    """THE PRODUCTION SELECTION since migration 184: the environment flag
    on, the completed-game policy's row ON and the strict benchmark's row
    OFF (one active entry experiment). Asserted, not assumed."""
    monkeypatch.setenv(PB.ENV_FLAG, "on")
    monkeypatch.setenv(PL.S.ENV_FLAG, "on")
    PL.set_policy_control(PB.CG_POLICY["control_key"], True)
    PL.set_policy_control(PB.CONTROL_KEY, False)
    PB._CONTEXT_CACHE.clear()
    PD._CONTEXT_CACHE.clear()
    yield
    PB._CONTEXT_CACHE.clear()


@pytest.fixture
def strict_too(both_on):
    """Both benchmark rows ON, to prove the cross-strategy guard; put back
    to the production selection afterwards."""
    PL.set_policy_control(PB.CONTROL_KEY, True)
    yield
    PL.set_policy_control(PB.CONTROL_KEY, False)


async def _decision(conn, acct, vid, strategy):
    return await conn.fetchrow(
        "SELECT * FROM paper_decisions WHERE session_id=$1 AND "
        " valuation_id=$2 AND strategy=$3", acct["session_id"], vid, strategy)


async def _counts(conn, acct, strategy):
    q = lambda t: ("SELECT count(*) FROM %s WHERE account_id=$1 AND "  # noqa
                   " strategy=$2" % t)
    out = {}
    for t in ("paper_decisions", "paper_orders", "paper_fills",
              "paper_handoffs"):
        out[t] = await conn.fetchval(q(t), acct["account_id"], strategy)
    out["entry_orders"] = await conn.fetchval(
        "SELECT count(*) FROM paper_orders WHERE account_id=$1 AND "
        " strategy=$2 AND role='ENTRY'", acct["account_id"], strategy)
    out["ledger"] = await conn.fetchval(
        "SELECT count(*) FROM paper_ledger WHERE account_id=$1",
        acct["account_id"])
    out["funding"] = await conn.fetchval(
        "SELECT count(*) FROM paper_ledger WHERE account_id=$1 AND "
        " kind='INITIAL_FUNDING'", acct["account_id"])
    return out


# ═════════════════════════════════════════════════════════════════════
# 1 · THE ORDINARY GRADING PERIOD, FROM EACH SIDE'S OWN TERMS (pure)
# ═════════════════════════════════════════════════════════════════════

def test_the_recorded_venue_texts_state_the_books_completed_game_period():
    mlb = PB.venue_grading_period("baseball", PL.RECORDED_PHI_ATL_VENUE_PROSE)
    assert mlb["refusal"] is None
    assert mlb["period"] == PB.book_grading_period("baseball")["period"]
    unl = PB.venue_grading_period("soccer", GRE_GER)
    assert unl["refusal"] is None
    assert unl["period"] == PB.GP_SOCCER_90
    assert PB.book_grading_period("soccer")["period"] == PB.GP_SOCCER_90


def test_regulation_and_extra_time_are_kept_apart():
    reg = PB.venue_grading_period("baseball", REGULATION_ONLY_BASEBALL)
    assert reg["refusal"] == PB.R_GP_MISMATCH
    et = PB.venue_grading_period(
        "soccer", GRE_GER.replace(
            "at the end of 90 minutes plus stoppage time in the",
            "at the end of 90 minutes plus stoppage time, including extra "
            "time and penalties, in the"))
    assert et["refusal"] == PB.R_GP_MISMATCH
    assert PB.venue_grading_period("baseball", "")["refusal"] == \
        PB.R_GP_TEXT_ABSENT
    vague = PB.venue_grading_period(
        "baseball", "This market resolves on the official MLB result.")
    assert vague["refusal"] == PB.R_GP_UNKNOWN
    assert PB.venue_grading_period("tennis", GRE_GER)["refusal"] == \
        PB.R_FAMILY


# ═════════════════════════════════════════════════════════════════════
# 2 · ENTRY, REFUSALS, FILL, CASH, HANDOFF -- ONE REAL PASS
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_conditional_entry_refusals_fill_and_xavier_handoff(both_on):
    """ONE PASS, FOUR VALUATIONS, FRESH BOOKS, BOTH POLICIES ON:

      playoff     the recorded Wild Card text: the strict policy REFUSES
                  (postponement payouts conflict); the completed-game policy
                  ENTERS, conditional on ordinary completion, the
                  exceptional terms disclosed and not called compatible
      regulation  a nine-inning-only text: REFUSE, grading period mismatch,
                  no book read, no order
      outcome     the payout names the other side: REFUSE, outcome mismatch
      no_text     no venue rules text on the row: REFUSE by name
    """
    conn = await H.connect()
    now = time.time() + 5.0
    try:
        await PL.purge_everything(conn)
        await PL.purge_research_models(conn)
        acct = await PL.new_account(conn, "cgpaper", now=now)
        t = PL.Transport(now)
        vals = {}
        for kind in ("playoff", "regulation", "outcome", "no_text"):
            v = await PL.valuation(
                conn, decided_at=now - 10, p_pin=0.62,
                compatibility=("NOT_COMPARED" if kind == "no_text"
                               else "INCOMPATIBLE"))
            t.set(v["slug"], offers=[(0.50, 2000)], bids=[(0.48, 2000)])
            vals[kind] = v
        await conn.execute(
            "UPDATE external_valuations SET settlement_comparison = "
            " jsonb_set(settlement_comparison, '{venue_rules_text}', "
            " to_jsonb($2::text)) WHERE id=$1",
            vals["regulation"]["valuation_id"], REGULATION_ONLY_BASEBALL)
        await conn.execute("UPDATE external_valuations SET payout_event="
                           "'AWAY' WHERE id=$1",
                           vals["outcome"]["valuation_id"])
        client = PL.client(t)
        p1 = await _pass(conn, acct, t, now, client=client)
        assert p1["ran"] and not p1["errors"], p1["errors"]
        assert client.mutation_attempts == 0
        assert p1["steps"]["benchmark_completed_game"][
            "decisions_recorded"] >= 4

        # ONE ACTIVE ENTRY POLICY: the strict benchmark's row is off, so it
        # records nothing new (its history is untouched)
        assert await _decision(conn, acct, vals["playoff"]["valuation_id"],
                               PB.STRATEGY) is None

        ok = await _decision(conn, acct, vals["playoff"]["valuation_id"], CG)
        assert ok["verdict"] == "ENTER", (ok["refusal"], ok["refusals"])
        assert ok["decision_id"].startswith("papercg:")
        assert ok["policy_version"] == PB.CG_VERSION
        m = H.j(ok["pinnacle"])["contract_match"]
        chk = {c["check"]: c for c in m["checks"]}
        for name in (PB.DP.C_IDENTITY, "payout_outcome_match",
                     "market_and_line", "grading_period_full_game",
                     "ordinary_completion_grading_period"):
            assert chk[name]["passed"] is True, chk[name]
        assert PB.DP.C_SETTLEMENT not in chk, \
            "the strict settlement check is not this policy's"
        exc = m["exceptional_terms"]
        assert exc["status"] == ("DISCLOSED_RESEARCH_RISK_NOT_SETTLEMENT_"
                                 "COMPATIBILITY")
        assert exc["compatibility_recorded"] == "INCOMPATIBLE"
        econ = H.j(ok["economics"])
        assert econ["label"] == PB.ECONOMICS_LABEL
        assert econ["conditional_on"] == "ORDINARY_COMPLETION"
        assert econ["best_level_edge_pp"] == pytest.approx(12.0)
        assert econ["acquisition"]["net_ev_positive"] is True
        assert econ["mapping_assumptions"]
        sc = econ["acquisition"]["exceptional_settlement"]
        assert sc["included_in_conditional_ev"] is False
        np_ = sc["scenarios"][ST.C_NOT_PLAYED]
        assert np_["venue_payout"] == ST.PAY_LAST_FAIR_MARKET_PRICE
        assert np_["probability"] == "UNMEASURED"
        assert np_["payoff_per_contract_range"] == [-0.5, 0.5]
        assert H.j(ok["policy_decision"])["economics_label"] == \
            PB.ECONOMICS_LABEL

        reg = await _decision(conn, acct, vals["regulation"]["valuation_id"],
                              CG)
        assert reg["verdict"] == "REFUSE"
        assert reg["refusal"] == PB.R_GP_MISMATCH
        assert reg["book_obs_id"] is None, "no book is read for a refusal"
        out = await _decision(conn, acct, vals["outcome"]["valuation_id"], CG)
        assert out["verdict"] == "REFUSE" and out["refusal"] == PB.R_OUTCOME
        nt = await _decision(conn, acct, vals["no_text"]["valuation_id"], CG)
        assert nt["verdict"] == "REFUSE"
        assert nt["refusal"] == PB.R_GP_TEXT_ABSENT

        orders = await conn.fetch(
            "SELECT * FROM paper_orders WHERE session_id=$1 AND "
            " role='ENTRY'", acct["session_id"])
        assert [(o["decision_id"], o["strategy"]) for o in orders] == \
            [(ok["decision_id"], CG)]

        # THE SIMULATED FILL AFTER THE DELAY, THE CASH, THE HANDOFF
        p2 = await _pass(conn, acct, t, now + 5, client=client)
        assert not p2["errors"], p2["errors"]
        o = await conn.fetchrow("SELECT * FROM paper_orders WHERE "
                                " decision_id=$1", ok["decision_id"])
        assert o["state"] == "FILLED" and o["strategy"] == CG
        fills = await conn.fetch("SELECT * FROM paper_fills WHERE "
                                 " order_id=$1", o["order_id"])
        assert {f["strategy"] for f in fills} == {CG}
        assert {f["event_source"] for f in fills} == {"SIMULATOR"}
        assert sum(float(f["qty"]) for f in fills) == float(o["qty"])
        b = await L.balances(conn, acct["account_id"], now=now + 6)
        assert b["ledger_consistent"] is True
        cost = sum(float(f["qty"]) * float(f["price"]) for f in fills)
        fees = sum(float(f["fee_usd"]) for f in fills)
        assert b["cash_usd"] == pytest.approx(500000.0 - cost - fees)
        debit = await conn.fetchval(
            "SELECT -sum(cash_delta_usd) FROM paper_ledger WHERE "
            " order_id=$1 AND kind='FILL'", o["order_id"])
        assert float(debit) == pytest.approx(cost + fees)
        h = await conn.fetchrow("SELECT * FROM paper_handoffs WHERE "
                                " group_id=$1", o["group_id"])
        assert h is not None and h["owner"] == "XAVIER"
        assert h["strategy"] == CG
        assert float(h["confirmed_qty"]) == float(o["qty"])
        # XAVIER MEASURES IT CONDITIONALLY, THE EXCEPTIONAL STATES APART
        pos = next(p for p in await L.positions(conn, acct["account_id"])
                   if p["group_id"] == o["group_id"])
        ctx = {"now": now + 6, "config": acct["config"],
               "account_id": acct["account_id"],
               "session_id": acct["session_id"]}
        meas = await PB.xavier_measure(conn, ctx, pos=pos, strategy=CG)
        assert meas.get("measure_is") == "CONDITIONAL_ON_ORDINARY_COMPLETION"
        assert meas.get("exceptional_states")
        said = json.dumps(meas, default=str).lower()
        assert "no hedge is described as guaranteeing profit" in said
        assert "guarantee" not in said.replace(
            "no hedge is described as guaranteeing", ""), said
        assert (await _counts(conn, acct, CG))["funding"] == 1
    finally:
        await PL.purge_everything(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 3 · AN EXCEPTIONAL SETTLEMENT: THE VENUE'S PRICE, NEVER A REFUND
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_an_exceptional_settlement_is_paid_at_the_venue_price(both_on):
    conn = await H.connect()
    now = time.time() + 5.0
    try:
        await PL.purge_everything(conn)
        await PL.purge_research_models(conn)
        acct = await PL.new_account(conn, "cgexc", now=now)
        t = PL.Transport(now)
        v = await PL.valuation(conn, decided_at=now - 10, p_pin=0.62,
                               compatibility="INCOMPATIBLE")
        t.set(v["slug"], offers=[(0.50, 2000)], bids=[(0.48, 2000)])
        client = PL.client(t)
        assert not (await _pass(conn, acct, t, now, client=client))["errors"]
        assert not (await _pass(conn, acct, t, now + 5,
                                client=client))["errors"]
        o = await conn.fetchrow(
            "SELECT * FROM paper_orders WHERE account_id=$1 AND strategy=$2 "
            "   AND role='ENTRY'", acct["account_id"], CG)
        assert o is not None and o["state"] == "FILLED"
        qty = float(o["filled_qty"])
        cash0 = (await L.balances(conn, acct["account_id"],
                                  now=now + 6))["cash_usd"]

        # THE GAME IS POSTPONED; THE VENUE HAS PUBLISHED NOTHING YET:
        # the position stays open and pending -- no refund is assumed
        p3 = await _pass(conn, acct, t, now + 10, client=client)
        assert not p3["errors"], p3["errors"]
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_settlements WHERE group_id=$1",
            o["group_id"]) == 0
        pos = next(p for p in await L.positions(conn, acct["account_id"])
                   if p["group_id"] == o["group_id"])
        assert pos["open_qty"] == pytest.approx(qty)

        # THE VENUE PUBLISHES ITS LAST-FAIR-MARKET-PRICE SETTLEMENT: 0.43
        await conn.execute(
            "UPDATE external_valuations SET settlement_read='0.43', "
            " settlement_read_at=now() WHERE id=$1", v["valuation_id"])
        p4 = await _pass(conn, acct, t, now + 15, client=client)
        assert not p4["errors"], p4["errors"]
        s = await conn.fetchrow("SELECT * FROM paper_settlements WHERE "
                                " group_id=$1", o["group_id"])
        assert s["outcome"] == "SETTLED_AT_VENUE_PRICE"
        assert float(s["payout_per_contract"]) == pytest.approx(0.43)
        assert float(s["payout_usd"]) == pytest.approx(0.43 * qty)
        assert s["evidence_source"] == "external_valuations.settlement_read"
        ev = H.j(s["evidence"])
        assert ev["policy"] == PB.CG_VERSION
        cash1 = (await L.balances(conn, acct["account_id"],
                                  now=now + 16))["cash_usd"]
        assert cash1 - cash0 == pytest.approx(0.43 * qty)
        assert cash1 - cash0 != pytest.approx(0.50 * qty), \
            "never the purchase price back"

        # AUDREY: ACTUAL SIMULATED P&L, ENTRY ASSUMPTIONS, THE EXCEPTION
        rep = await PB.report_section(
            conn, account_id=acct["account_id"],
            t0=PL.ts(now - 3600) if hasattr(PL, "ts") else
            __import__("datetime").datetime.fromtimestamp(
                now - 3600, __import__("datetime").timezone.utc),
            t1=__import__("datetime").datetime.fromtimestamp(
                now + 3600, __import__("datetime").timezone.utc),
            pol=PB.CG_POLICY)
        assert rep["strategy"] == CG
        exo = rep["exceptional_outcomes"]
        assert len(exo) == 1 and exo[0]["outcome"] == \
            "SETTLED_AT_VENUE_PRICE"
        assert exo[0]["payout_usd"] == pytest.approx(0.43 * qty)
        assert rep["exceptional_effect_usd"] < 0
        assert rep["entry_assumptions"]
        assert rep["qualification"] in ("NONE", None) or \
            "not" in str(rep["qualification"]).lower()
    finally:
        await PL.purge_everything(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 4 · RESTART AND REPETITION DUPLICATE NOTHING, PER STRATEGY
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_repeated_passes_hooks_and_restarts_duplicate_nothing(both_on):
    conn = await H.connect()
    now = time.time() + 5.0
    try:
        await PL.purge_everything(conn)
        await PL.purge_research_models(conn)
        acct = await PL.new_account(conn, "cgrst", now=now)
        t = PL.Transport(now)
        v = await PL.valuation(conn, decided_at=now - 2, p_pin=0.62,
                               compatibility="INCOMPATIBLE")
        t.set(v["slug"], offers=[(0.50, 2000)], bids=[(0.48, 2000)])

        async def hook(at):
            return await PR.decide_valuation(
                conn, valuation_id=v["valuation_id"], now=at,
                market_data=PL.client(t), account_id=acct["account_id"],
                fee_fn=FEE, schedule_fill=lambda: {"scheduled": False})
        g1 = await hook(now)
        assert "benchmark_completed_game" in g1, g1
        assert g1["benchmark_completed_game"]["verdict"] == "ENTER"
        assert g1["benchmark"]["decided"] is False
        c0 = await _counts(conn, acct, CG)
        assert c0["paper_orders"] == 1, [dict(r) for r in await conn.fetch(
            "SELECT order_id, role, decision_id, state, strategy FROM "
            " paper_orders WHERE account_id=$1", acct["account_id"])]
        assert (c0["paper_decisions"], c0["paper_orders"]) == (1, 1)
        await hook(now + 1)
        assert not (await _pass(conn, acct, t, now + 2))["errors"]
        # A RESTART: every in-process cache dropped, then the pass again
        PB._CONTEXT_CACHE.clear()
        PD._CONTEXT_CACHE.clear()
        assert not (await _pass(conn, acct, t, now + 6))["errors"]
        await hook(now + 7)
        c1 = await _counts(conn, acct, CG)
        # one decision, one entry order; Xavier's standing protection is
        # its own (non-entry) order on the same group, at most one live
        assert c1["paper_decisions"] == 1 and c1["entry_orders"] == 1
        assert c1["paper_orders"] - c1["entry_orders"] <= 1
        assert c1["paper_handoffs"] <= 1 and c1["funding"] == 1
        fills = await conn.fetch(
            "SELECT fill_id FROM paper_fills WHERE account_id=$1 AND "
            " strategy=$2", acct["account_id"], CG)
        assert len({f["fill_id"] for f in fills}) == len(fills)
        # the strict policy, switched off, recorded nothing
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_decisions WHERE account_id=$1 AND "
            " strategy=$2", acct["account_id"], PB.STRATEGY) == 0
    finally:
        await PL.purge_everything(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 4b · ACTIVE ENTRY POLICIES, READ BACK; NO DUPLICATE EXPOSURE ACROSS THEM
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_the_active_entry_policies_read_back(both_on):
    conn = await H.connect()
    try:
        rows = {r["control_key"]: r for r in await conn.fetch(
            "SELECT control_key, enabled, updated_by FROM paper_control")}
        assert rows[PB.CG_POLICY["control_key"]]["enabled"] is True
        assert rows[PB.CONTROL_KEY]["enabled"] is False
        assert rows[PB.CONTROL_KEY]["updated_by"] == "migration 184"
        # DEREK'S TWO-MODEL STRATEGY MAY ENTER ON PAPER (an intended change,
        # pinned at integration). This line pinned migration 182's state --
        # the row absent or OFF ("only the benchmark opens new entries").
        # Migration 264 is the owner's decision of 2026-10-04 (P0 incident,
        # decision 2): PAPER_ENTRIES:DEREK_ENTRY_POLICY_V2 ON, PAPER execution
        # only, every threshold / limit / live permission unchanged and SMALL
        # LIVE SHADOW (tests/test_derek_paper_entries_reenabled proves the
        # migration itself). The production selection read back here is now
        # the completed-game policy AND Derek V2 on, the strict benchmark off.
        two = rows.get("PAPER_ENTRIES:DEREK_ENTRY_POLICY_V2")
        assert two is not None and two["enabled"] is True, two
        assert two["updated_by"] == "migration 264", two
        assert (await PD.entries_switch(conn))["enabled"] is True
        assert (await PB.enablement(conn, PB.CG_POLICY))["enabled"] is True
        assert (await PB.enablement(conn))["enabled"] is False
    finally:
        await conn.close()


@pg
async def test_a_second_strategy_never_duplicates_exposure(strict_too):
    """The strict benchmark holds a game (synthetic fully compatible terms);
    a later valuation of the SAME contract carrying the recorded Wild Card
    text would pass the completed-game match and edge -- it is refused by
    name, before any book read, because another strategy holds the game."""
    conn = await H.connect()
    now = time.time() + 5.0
    try:
        await PL.purge_everything(conn)
        await PL.purge_research_models(conn)
        acct = await PL.new_account(conn, "cgdup", now=now)
        t = PL.Transport(now)
        first = await PL.valuation(conn, decided_at=now - 10, p_pin=0.62,
                                   compatibility="COMPATIBLE")
        t.set(first["slug"], offers=[(0.50, 2000)], bids=[(0.48, 2000)])
        client = PL.client(t)
        assert not (await _pass(conn, acct, t, now, client=client))["errors"]
        st = await _decision(conn, acct, first["valuation_id"], PB.STRATEGY)
        assert st["verdict"] == "ENTER", (st["refusal"], st["refusals"])
        second = await PL.valuation(conn, slug=first["slug"],
                                    decided_at=now - 2, p_pin=0.62,
                                    compatibility="INCOMPATIBLE")
        assert not (await _pass(conn, acct, t, now + 1,
                                client=client))["errors"]
        cg = await _decision(conn, acct, second["valuation_id"], CG)
        assert cg["verdict"] == "REFUSE"
        assert cg["refusal"] == PB.R_CROSS_STRATEGY
        held = H.j(cg["pinnacle"])["cross_strategy_exposure"]
        assert held["held"] is True
        assert {b["strategy"] for b in held["by"]} == {PB.STRATEGY}
        assert cg["book_obs_id"] is None
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_orders WHERE account_id=$1 AND "
            " strategy=$2", acct["account_id"], CG) == 0
    finally:
        await PL.purge_everything(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 5 · THE KILL SWITCH AND PAPER-TO-FUNDED ISOLATION
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_its_own_kill_switch_stops_it_alone(both_on):
    conn = await H.connect()
    now = time.time() + 5.0
    try:
        await PL.purge_everything(conn)
        acct = await PL.new_account(conn, "cgoff", now=now)
        t = PL.Transport(now)
        v = await PL.valuation(conn, decided_at=now - 2, p_pin=0.62,
                               compatibility="INCOMPATIBLE")
        t.set(v["slug"], offers=[(0.50, 2000)], bids=[(0.48, 2000)])
        await conn.execute("UPDATE paper_control SET enabled=FALSE WHERE "
                           " control_key=$1", PB.CG_POLICY["control_key"])
        try:
            en = await PB.enablement(conn, PB.CG_POLICY)
            assert en["enabled"] is False
            assert en["refusal"] == PB.R_CONTROL_OFF
            assert (await PB.enablement(conn))["enabled"] is False
            assert not (await _pass(conn, acct, t, now))["errors"]
            assert (await _counts(conn, acct, CG))["paper_decisions"] == 0
        finally:
            await conn.execute("UPDATE paper_control SET enabled=TRUE WHERE "
                               " control_key=$1",
                               PB.CG_POLICY["control_key"])
    finally:
        await PL.purge_everything(conn)
        await conn.close()


def test_the_policy_is_paper_only_and_unreachable_from_funded_paths():
    import ast
    import pathlib
    root = pathlib.Path(PB.__file__).resolve().parents[1]
    src = (root / "agents" / "paper_benchmark.py").read_text()
    names = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
    for bad in ("funded", "entry_execution", "submission", "order_router",
                "polymarket", "adapter", "live"):
        assert not any(bad in (n or "").lower() for n in names), bad
    for p in root.rglob("*.py"):
        if "funded" in p.name or "entry_execution" in p.name:
            assert "paper_benchmark" not in p.read_text(), p
    assert PB.CG_POLICY["id_prefix"].startswith("paper")
    assert PB.CG_STRATEGY in PB.BENCHMARK_STRATEGIES
