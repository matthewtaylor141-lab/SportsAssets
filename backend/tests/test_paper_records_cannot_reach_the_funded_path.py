"""PAPER RECORDS CANNOT REACH THE FUNDED PATH, AND THE PAPER LEDGER WRITES NO
FUNDED TABLE.

  * the funded executor (`bettor_funded_execution.submit_for_decision`)
    refuses any paper id or paper record FIRST -- before the schema check,
    the account, the rails or any write -- with nothing written;
  * the funded submission switches stay False;
  * a full paper lifecycle (reserve, fill, sale, settlement, correction)
    changes the row count of no bettor_funded_* table;
  * no funded module imports a paper module except the executor's refusal.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from sportsassets import bettor_entry_execution as EX
from sportsassets import bettor_funded_execution as FX
from sportsassets import bettor_funded_management as FMG
from sportsassets import bettor_paper_guard as G
from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_simulator as SIM

from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
ROOT = pathlib.Path(__file__).resolve().parents[1] / "sportsassets"


def test_the_submission_switches_stay_false():
    assert FX.FUNDED_SUBMISSION_ENABLED is False
    assert EX.REAL_ORDER_SUBMISSION_ENABLED is False
    assert FMG.FUNDED_EXIT_SUBMISSION_ENABLED is False


class _Conn:
    """Any database call fails the test: the refusal comes first."""

    def __getattr__(self, name):
        raise AssertionError("the funded executor touched the database (%s) "
                             "for a paper record" % name)


class _Adapter:
    def __getattr__(self, name):
        raise AssertionError("the funded executor reached the adapter (%s)"
                             % name)


@pytest.mark.parametrize("rec,ids", [
    ({"record_purpose": "ENTRY_DECISION", "order_id": "paperord:abc"}, {}),
    ({"record_purpose": "ENTRY_DECISION", "decision_id": "paperdec:1"}, {}),
    ({"record_purpose": "ENTRY_DECISION", "fill_id": "paperfill:1"}, {}),
    ({"record_purpose": "ENTRY_DECISION",
      "data_label": "LIVE MARKET DATA / SIMULATED EXECUTION"}, {}),
    ({"record_purpose": "ENTRY_DECISION", "event_source": "SIMULATOR"}, {}),
    ({"record_purpose": "PAPER_ENTRY"}, {}),
    ({"record_purpose": "ENTRY_DECISION"}, {"account_id": "paper_acct_main"}),
    ({"record_purpose": "ENTRY_DECISION"},
     {"portfolio_group_id": "paper_group_x"}),
    ({"record_purpose": "ENTRY_DECISION"}, {"operation_id": "paperop:1"}),
])
async def test_the_funded_executor_refuses_any_paper_id_or_record(rec, ids):
    kw = {"account_id": "acct-real", "venue": "PMUS"}
    kw.update(ids)
    got = await FX.submit_for_decision(_Conn(), rec, adapter=_Adapter(),
                                       **kw)
    assert got["ok"] is False and got["submitted"] is False
    assert got["refusal"] == G.R_PAPER_TO_FUNDED
    assert got["nothing_was_written"] is True


def test_a_real_record_is_not_mistaken_for_paper():
    assert G.refuse_paper_record({"record_purpose": "ENTRY_DECISION",
                                  "order_id": "ord-123"},
                                 account_id="acct-real") is None


def test_no_funded_module_imports_the_paper_modules():
    offenders = []
    for p in ROOT.rglob("*.py"):
        name = p.name
        if name.startswith("bettor_paper") or name.startswith("paper_") \
                or p.parent.name == "api" and name == "command_paper.py":
            continue
        tree = ast.parse(p.read_text())
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.ImportFrom):
                mods = [node.module or ""] + [a.name for a in node.names]
            elif isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            if any("paper" in (m or "") for m in mods):
                offenders.append(str(p.relative_to(ROOT)))
    allowed = {"bettor_funded_execution.py",       # the refusal itself
               "api/app.py",                        # the read-only router
               "agents/runtime.py",                 # the guarded paper hook
               "workers/ext_pinnacle_loop.py",      # the scheduled seam
               # the agents' CONVERSATIONS read the paper experiment through
               # the read-only `agents/paper_brief` (balances, session,
               # today's decisions); neither is a funded module, neither
               # writes, and only the API chat routes import them
               "agents/persona_facts.py",
               "agents/audrey_chat.py",
               # the capability workbench's evidence tools read the paper
               # ledger inside a READ ONLY transaction, and its experiment
               # registration writes only paper_improvement_proposals via
               # paper_learning; neither grants activation or order authority
               "agents/capability_tools.py",
               "agents/capability_experiments.py",
               # the management Slack updates compose briefings from the
               # read-only paper operations/brief readers; the module writes
               # only its own schedule row and agent_slack_delivery, and
               # nothing in Slack can place, change or approve an order
               "slack_updates.py",
               # (310) the CAPITAL READINESS LAB's feeds and observer read the
               # paper evaluations / settlements / freshness / integrity in a
               # transaction they roll back, and append only to the four
               # migration-310 RESEARCH / SHADOW_NO_AUTHORITY tables; no
               # order, size, gate or promotion effect (its own static
               # authority audit forbids the order modules)
               "capital_readiness/feeds.py",
               "capital_readiness/observer.py",
               # REVENUE RELIABILITY V1: the readback reads the paper ledger's
               # cash_state inside a READ ONLY transaction it rolls back (the
               # route and the read); Audrey's proposal hook writes only
               # governed improvement candidates / holdouts (migration 155,
               # REQUIRES_APPROVAL). No order, size, limit or authority effect.
               "revenue_reliability/read.py",
               # RED TEAM CLOSEOUT V1: the canonical exposure lock is CALLED
               # BY the paper ledger (its ENTRY path, under the account lock)
               # and reads the ledger's open positions / reservations; the
               # readiness binding reads the paper book read-only. Neither is
               # a funded module; neither has an order or authority effect
               "redteam/exposure.py",
               "redteam/readiness.py",
               # PM EVIDENCE PACK: the forward scoreboard reconciles to the
               # paper ledger's own positions (bettor_paper_ledger.positions)
               # inside the readback's READ ONLY transaction; a read layer,
               # never a second ledger, no order or authority effect
               "pm_bind/scoreboard.py",
               # COMPLETION READINESS: the readback reads the paper ledger's
               # cash / positions and the paper freshness summary inside a
               # READ ONLY transaction (GET /api/command/completion-readiness);
               # no order, size, limit or authority effect
               "completion/read.py",
               "api/command_revenue_reliability.py",
               "agents/revenue_improvements.py",
               # (209) Audrey's coverage integrity, postmortems and the
               # improvement driver: they read the paper ledger, write their
               # own migration-209 tables, Audrey findings and the
               # collaboration loop's PAPER_ONLY stages; none is a funded
               # module and none has an order, limit or policy effect
               "agents/coverage_integrity.py",
               "agents/postmortems.py",
               "agents/improvement_driver.py",
               # (206) Xavier's management assessments: reads the paper
               # ledger/simulator's exit walk to value HOLD/EXIT/REDUCE and
               # REALLOCATE, and writes ONLY its own migration-206 tables
               # (theses, assessments, value-add). It holds no order, submit
               # or cancel call; the actual lane hands its review hook in
               # from app.py, so execmirror imports no paper module
               "agents/xavier_management.py",
               # (RC6 xavier-records) the management READBACK's record of
               # each held paper position (management_view): reads the paper
               # ledger's positions, the review rows, the orders and the
               # protection state; writes nothing, holds no order, submit or
               # cancel call, and imports no funded module
               "agents/xavier_management_record.py",
               # the held-position watch on the PinnAPI feed: on a held
               # market's price change it SCHEDULES a paper Xavier review
               # (paper_runtime.schedule_held_review); no write, no order
               "pinnapi_held.py",
               # (217) Archer's SHADOW execution estimator reads the paper
               # simulator's book ladders (levels_for) and the ledger's fee
               # schedule (_fee) as pure functions over recorded books; it
               # writes only eddie_execution_estimates / _outcomes, declared
               # as ARCHER (the database refuses him on every order table),
               # and holds no submit, cancel or reserve call
               "agents/archer.py",
               # the live equity wall (/api/command/equity/*): GET-only, reads
               # bettor_paper_ledger.balances inside a READ ONLY transaction
               # beside the actual books (never summed); it writes nothing and
               # imports no order, venue, execution or funded module
               # (tests/test_equity_wall_authority.py)
               "api/command_equity.py",
               # (closeout) the market plane's held-position denominator
               # (/api/command/market-plane): GET-only, reads
               # bettor_paper_freshness.read inside a READ ONLY transaction so
               # the started market-plane worker never imports the paper
               # ledger; writes nothing, imports no order or funded module
               "api/command_market_plane.py",
               # (closeout) the PAPER loss attribution (/api/command/paper/
               # loss-attribution): GET-only, reads paper_loss_attribution
               # (intel.attribution over the immutable paper history) inside
               # a READ ONLY transaction; writes nothing, no order authority
               "api/command_paper_loss_attribution.py",
               # (223) the paper sleeve economics (/api/command/profitability/
               # sleeves): GET-only, reads bettor_paper_sleeves.sleeve_book
               # (paper ledger + paper_sleeve_classifications) inside a READ
               # ONLY transaction; writes nothing, imports no order, venue,
               # execution or funded module (tests/test_paper_sleeves.py)
               "api/command_sleeves.py",
               # the profitability validation per sleeve (/api/command/
               # profitability/validation): GET-only, reads
               # bettor_paper_ledger.positions / balances and
               # bettor_paper_sleeves.classifications inside a READ ONLY
               # transaction; writes nothing, imports no order, venue,
               # execution or funded module
               # (tests/test_profitability_validation.py)
               "api/command_validation.py",
               # (R30A) the confidence ladder (/api/command/confidence-
               # ladder): GET-only; it imports bettor_paper_ledger (for
               # ACCOUNT_ID, and command_validation.gather's positions /
               # balances / sleeves reads), counts paper_decisions per
               # strategy, and reads the parity ledger and the SMALL LIVE
               # control, all inside one READ ONLY transaction. It writes
               # nothing. NOTE (R30A review): it also imports live_parity --
               # whose module top imports execmirror (the legacy mirror that
               # holds Venue.place) -- and calls ONLY its read functions
               # readiness_report / readiness: no venue, order, submit or
               # control call is made, and execmirror.Venue.place refuses
               # without a canonical LIVE authorization in any case. No
               # funded module is imported (tests/test_confidence_ladder.py
               # pins the route's imports and SQL)
               "api/command_confidence_ladder.py",
               # (226, owner R30) Xavier's fresh-evidence work queue: reads
               # the paper ledger's open positions (to close the requests of
               # a position that closed) and asks the paper runtime for a
               # priority book read / a held re-review; it writes ONLY
               # agent_work_* records and holds no submit, cancel or reserve
               # call (tests/test_agent_work_state_authority.py)
               "agents/work_queue.py",
               # (290) PAPER TURNAROUND: the strategy lifecycle reads the
               # paper ledger's positions and marks (through the read-only
               # stale-management seam) and writes ONLY its own append-only
               # paper_strategy_lifecycle_events; the ledger calls its entry
               # gate, which can only refuse or shrink a paper ENTRY. The
               # GET-only turnaround route reads it in a READ ONLY
               # transaction. None imports an order, venue, execution or
               # funded module (tests/test_strategy_lifecycle.py)
               "bettor_strategy_lifecycle.py",
               "bettor_stale_management.py",
               "api/command_turnaround.py",
               # (305) PAPER CAPITAL AUTHORITY: reads the paper ledger's
               # positions / balances and the simulator's pure window and
               # walk rules, writes ONLY its own append-only shadow
               # counterfactual / outcome / refusal-census rows; the ledger
               # calls its entry check, which can only refuse a paper ENTRY.
               # The GET-only acceptance route reads it in a READ ONLY
               # transaction. Neither imports an order, venue, execution or
               # funded module (tests/test_capital_authority.py)
               "bettor_capital_authority.py",
               "api/command_capital_authority.py",
               # (311) the forward profitability scoreboard: GET-only, reads
               # bettor_paper_profitability_stack.scoreboard_read inside a
               # READ ONLY transaction; writes nothing, imports no order,
               # venue, execution or funded module
               # (tests/test_profitability_stack_binding.py)
               "api/command_profitability_scoreboard.py",
               # (R30C, 300) the live execution calibration read model
               # (/api/command/execution-calibration): GET-only, reads the
               # PAPER adapter's paper orders / fills / books and walks the
               # SHADOW proposal through the observed book with the paper
               # simulator's PURE walk (levels_for / walk) inside a READ ONLY
               # transaction; writes nothing, and imports no order, venue,
               # execution or funded module -- directly or in what a request
               # loads at run time (the micro-calibration lane's class is
               # restated, never imported; tests/test_execution_calibration.py
               # checks the route's run-time import closure)
               "api/command_execution_calibration.py",
               # (R30C, 300) the Opportunity Score V1 / V2 shadow tournament
               # (/api/command/opportunity-score-tournament): GET-only, joins
               # each intent's paper order to bettor_paper_ledger.positions
               # (realized P&L) inside a READ ONLY transaction exactly as
               # api/command_validation.py does; writes nothing
               # (tests/test_opportunity_score_tournament.py)
               "api/command_opportunity_tournament.py"}
    assert set(offenders) <= allowed, sorted(set(offenders) - allowed)
    # ...and neither capability module reaches a funded or execution module,
    # directly or through the rest of the capability package
    for rel in ("agents/capability_tools.py", "agents/capability_experiments.py",
                "agents/capability_runtime.py", "agents/capability_work.py",
                "agents/capability_scorecards.py", "api/agent_capabilities.py",
                "slack_updates.py", "slack_bridge.py",
                "agents/intelligence_reports.py", "agents/cross_venue_research.py",
                # (217) Archer, Scout and their workflow: no funded module
                "agents/archer.py", "agents/archer_runner.py", "agents/scout.py",
                "agents/scout_runner.py", "agents/feature_tournament.py",
                "agents/pos_workflow.py", "agents/pos_authority.py",
                "agents/pos_evidence.py", "api/agents_pos.py",
                "api/agent_desks.py"):
        tree = ast.parse((ROOT / rel).read_text())
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.ImportFrom):
                mods = [node.module or ""] + [a.name for a in node.names]
            elif isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            for m in mods:
                assert not any(k in (m or "") for k in
                               ("funded", "entry_execution", "execution_gate", "pmus")), (rel, m)


@pg
async def test_a_whole_paper_lifecycle_writes_no_funded_table():
    conn = await H.connect()
    try:
        before = await H.funded_table_counts(conn)
        assert before, "the funded tables should exist in the test database"
        a = await H.new_account(conn, "sep")
        slug = a["account_id"] + ":m"
        o = H.order(a, key="sep-1", qty=500, limit=0.40, slug=slug, at=H.T0,
                    group_id="paper_g_sep")
        got = await L.submit_order(conn, o, fee_fn=H.flat_fee(0.01),
                                   now=H.T0)
        await H.observe(conn, slug, H.T0 + 3, offers=[(0.40, 500)],
                        bids=[(0.38, 500)])
        await SIM.simulate_order(conn, got["order"]["order_id"], now=H.T0 + 4,
                                 fee_fn=H.flat_fee(0.01))
        so = H.order(a, key="sep-2", direction="SELL", qty=200, limit=0.38,
                     slug=slug, role="REDUCE", group_id="paper_g_sep",
                     at=H.T0 + 10)
        sg = await L.submit_order(conn, so, now=H.T0 + 10)
        await H.observe(conn, slug, H.T0 + 13, bids=[(0.39, 500)])
        await SIM.simulate_order(conn, sg["order"]["order_id"],
                                 now=H.T0 + 14, fee_fn=H.flat_fee(0.01))
        await L.settle(conn, account_id=a["account_id"],
                       group_id="paper_g_sep", slug=slug,
                       holding_side="LONG", settlement_event_key="f",
                       outcome="WON", evidence={}, evidence_source="T",
                       at=H.T0 + 50)
        await L.correct_settlement(conn, account_id=a["account_id"],
                                   group_id="paper_g_sep", slug=slug,
                                   holding_side="LONG",
                                   settlement_event_key="f", outcome="LOST",
                                   evidence={}, evidence_source="T",
                                   at=H.T0 + 60)
        after = await H.funded_table_counts(conn)
        assert after == before
    finally:
        await conn.close()
