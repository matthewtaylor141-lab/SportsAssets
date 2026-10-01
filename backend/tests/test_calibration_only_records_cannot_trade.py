"""A VALUATION RECORDED FOR CALIBRATION ONLY CAN NEVER TRADE (D4, addendum A).

THE CHANGE UNDER TEST. The entry lane used to write a valuation only after an
ok venue read, and every venue read has refused VENUE_BOOK_CURRENCY_NOT_
ESTABLISHED since d66e89e -- so the odds-source calibration cohort stopped
growing. The lane now records the valuation after a currency refusal too,
through the SAME `ext.evaluate` / `ext.persist`, sealed CALIBRATION_ONLY.

WHAT THIS FILE PROVES, boundary by boundary:

  * the record is sealed: never admissible, no executable price, no size, no
    plan; the displayed venue price is kept only as evidence, flagged
    unusable for orders;
  * every consumer that could turn a valuation into inventory, an order, a
    reservation or an exposure refuses it BY NAME, even with `admissible`
    flipped to true -- payout binding, inventory planning and writing, the
    funded attempt and connector, the research waiver;
  * the database refuses a calibration-only row that could trade, refuses a
    relabel, and refuses a position opened from one (migration 144);
  * every reader of `external_valuations` in `sportsassets` is classified:
    the ones that select candidates filter the purpose, and the calibration
    measurement and outcome join deliberately do not;
  * through the SCHEDULED path (`cycle()` against substituted transport, the
    venue's book currency NOT established): the row is written with its
    purpose and evidence, no inventory / funded intent / reservation /
    exposure / order results, the event stays counted under the currency
    refusal, and the outcome join and the measurement still see the row;
  * with currency established, the entry path is what it was: the same 48
    row values the base commit wrote (pinned), plus the column-default
    purpose, an ENTRY_DECISION row with no calibration evidence, admitted and
    carried to inventory exactly as the empty-book lifecycle proof expects;
  * map4 §9 D8: `venue_quote` judges currency at (or after) its own
    post-read receipt, never at the caller's earlier instant.

SYNTHETIC EVIDENCE, LABELLED. Every probability, book and settlement here is a
test fixture. The empty-book fixture's supplied assumptions (book currency,
calibration, activation, switches) are named in `_emptybook_fixture`; the
currency-not-established test REMOVES the supplied currency and runs with the
production seam, which supplies none.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import inspect
import json
import os
import pathlib
import re
import time

import pytest

from sportsassets import bettor_entry_inventory as inv
from sportsassets import bettor_external_shadow as ext
from sportsassets import bettor_funded_execution as FX
from sportsassets import bettor_research_shadow as rsh
from sportsassets import bettor_source_calibration as CAL
from sportsassets import bettor_valuation_purpose as vp
from sportsassets import bettor_venue_currency as VC
from sportsassets.workers import ext_pinnacle_loop as loop

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs a migrated database")


# ── a record the gate WOULD admit, so the seal is what refuses it ────

def _admissible_kwargs(**over):
    """The control inputs from test_ext_pinnacle_loop: these clear the gate.
    SYNTHETIC: odds, ask and fee are fixture values."""
    kw = dict(
        contract={"venue": "V", "condition_id": "c-d4-seal", "selection": "A",
                  "us_market_slug": "aec-d4-seal",
                  "sport_family": "soccer", "market": "h2h",
                  "period": "FULL_GAME", "line": None,
                  "settlement_rule": "R", "event_key": "e-d4-seal"},
        quote={"book": "pinnacle",
               "outcomes": {"A": 1.5, "B": 3.0, "C": 4.0},
               "observed_at": 1000.0, "received_at": 1000.0,
               "event_key": "e-d4-seal",
               "period": "FULL_GAME", "line": None,
               "settlement_rule": "R"},
        market_state={"ask": 0.05, "depth": 500.0, "readable": True},
        execution_estimate={"p_fill": 0.9, "basis": "TEST", "crossing": True},
        size=1.0, risk={"permitted": True}, fee_fn=lambda qty, price: 0.0,
        now=1001.0, outcome_books=4, armed=True)
    kw.update(over)
    return kw


def _evidence():
    return {"venue_read_refusal": loop.R_BOOK_CURRENCY_NOT_ESTABLISHED,
            "displayed_quote": {"ok": True, "acquisition_price": 0.05,
                                "usable_for_orders": True},   # overridden
            "book_currency": {"verdict": VC.NOT_ESTABLISHED}}


def _sealed(**over):
    return ext.evaluate(**_admissible_kwargs(**over),
                        record_purpose=ext.PURPOSE_CALIBRATION_ONLY,
                        calibration_only_evidence=_evidence())


class _Untouchable:
    """A connection that fails the test if anything reads or writes it: the
    refusals below must happen before any database access."""

    def __getattr__(self, name):
        raise AssertionError("the database was touched (%s) for a "
                             "calibration-only record" % name)


# ═════════════════════════════════════════════════════════════════════
# 1 · THE SEAL
# ═════════════════════════════════════════════════════════════════════

def test_the_control_inputs_are_admissible_as_an_entry_decision():
    rec = ext.evaluate(**_admissible_kwargs())
    assert rec["admissible"] is True and rec["decision"] == "BUY"
    assert rec["record_purpose"] == vp.ENTRY_DECISION
    assert "calibration_only_evidence" not in rec


def test_a_calibration_only_record_is_sealed_even_where_the_gate_admits():
    rec = _sealed()
    assert rec["record_purpose"] == vp.CALIBRATION_ONLY
    assert rec["admissible"] is False and rec["decision"] == "NO_TRADE"
    for k in ("executable_price", "cost_per_contract",
              "estimated_edge_per_contract", "proposed_size",
              "executable_price_basis"):
        assert rec[k] is None, k
    assert vp.R_CALIBRATION_ONLY in rec["refusals"]
    assert "execution_plan" not in rec
    ev = rec["calibration_only_evidence"]
    # THE DISPLAYED PRICE IS EVIDENCE, NEVER AN ORDER PRICE -- even when the
    # caller claimed otherwise.
    assert ev["usable_for_orders"] is False
    assert ev["displayed_quote"]["usable_for_orders"] is False
    assert ev["compared_at_the_displayed_price"]["price"] == 0.05
    assert ev["compared_at_the_displayed_price"]["usable_for_orders"] is False
    assert ev["compared_at_the_displayed_price"]["edge_per_contract"] > 0
    # the probability -- what calibration needs -- is intact
    assert rec["probability"] is not None


def test_a_plan_handed_to_a_calibration_record_is_discarded():
    rec = _sealed(execution_plan=lambda fair_value, contract: {
        "execution_estimate": {"p_fill": 1.0}, "size": 5.0,
        "risk": {"permitted": True}, "ask": 0.05,
        "detail": {"execution": {"size": 5.0}, "risk": {"permitted": True},
                   "exposure": {}}})
    assert "execution_plan" not in rec
    assert rec["calibration_only_evidence"]["execution_plan_discarded"]
    assert rec["proposed_size"] is None and rec["admissible"] is False


def test_an_unrecognised_purpose_is_refused_not_defaulted():
    rec = ext.evaluate(**_admissible_kwargs(), record_purpose="SOMETHING")
    assert rec["admissible"] is False
    assert vp.R_UNKNOWN_PURPOSE in rec["refusals"]
    assert vp.refuse_unless_entry(rec)["refusal"] == vp.R_UNKNOWN_PURPOSE


def test_evidence_alone_makes_a_record_calibration_only():
    """Fail-closed: a record whose purpose key was lost but that carries the
    calibration evidence is calibration-only."""
    rec = _sealed()
    rec.pop("record_purpose")
    assert vp.purpose_of(rec) == vp.CALIBRATION_ONLY


# ═════════════════════════════════════════════════════════════════════
# 2 · THE ENTRY PATH IS WHAT IT WAS
# ═════════════════════════════════════════════════════════════════════

class _Recorder:
    def __init__(self):
        self.calls = []

    async def fetchval(self, sql, *args):
        self.calls.append((sql, args))
        return 1


#: THE 48 VALUES THE ENTRY PATH WROTE BEFORE THIS CHANGE, for the control
#: inputs above: produced by `ext.evaluate` + `ext.persist` at the base commit
#: 95fa2e4 (git worktree of the base, same inputs, a recording connection),
#: pasted verbatim. SYNTHETIC inputs; the point is the equality.
GOLDEN_ENTRY_VALUES_AT_BASE = json.loads(r'''["EXT_PINNACLE_DEVIG_V1_SHADOW", "PINNACLE_DEVIG_V1", "EXTERNAL_BOOKMAKER_VALUATION", "the-odds-api.com/v4", "pinnacle", "power", "V", "c-d4-seal", "aec-d4-seal", "BOTH_PRESENT_AND_INDEPENDENTLY_SOURCED", "A", "soccer", "h2h", "FULL_GAME", null, "R", "e-d4-seal", "{\"A\": 1.5, \"B\": 3.0, \"C\": 4.0}", 3, 3, 0.25, 1000.0, 1000.0, 1.0, 4, "A", "EXACT_AFTER_NORMALISATION", 0.5919477155091288, 0.05, 0.0, 0.5419477155091288, "BUY", true, [], "external probability 0.5919 on A vs acquisition 0.0500 less cost 0.0000", 1.0, "A", "RESOLVER_MATCHED_A_SIDE_FOR_THE_REQUESTED_OUTCOME", "A", false, null, null, "A", null, null, null, null, null]''')


def _same(a, b):
    if isinstance(a, float) or isinstance(b, float):
        return a == pytest.approx(b, rel=1e-12, abs=1e-15)
    return a == b


def test_an_entry_decision_writes_the_same_row_as_before():
    """BYTE FOR BYTE: the 48 values the entry path always wrote, unchanged,
    then the purpose -- 'ENTRY_DECISION' with no evidence, which is exactly
    the column default, so the row is the row it always was."""
    default = ext.evaluate(**_admissible_kwargs())
    explicit = ext.evaluate(**_admissible_kwargs(),
                            record_purpose=vp.ENTRY_DECISION)
    assert default == explicit
    r = _Recorder()
    asyncio.run(ext.persist(r, default))
    (sql, args), = r.calls
    assert sql is ext.INSERT
    assert len(args) == 50
    now = json.loads(json.dumps(list(args[:48]), default=str))
    assert len(now) == len(GOLDEN_ENTRY_VALUES_AT_BASE) == 48
    for i, (a, b) in enumerate(zip(now, GOLDEN_ENTRY_VALUES_AT_BASE)):
        assert _same(a, b), (i, a, b)
    assert args[48:] == (vp.ENTRY_DECISION, None)


def test_a_calibration_record_is_written_with_its_purpose_and_evidence():
    r = _Recorder()
    rec = _sealed()
    asyncio.run(ext.persist(r, rec))
    (sql, args), = r.calls
    assert sql is ext.INSERT
    assert len(args) == 50
    assert args[48] == vp.CALIBRATION_ONLY
    assert json.loads(args[49])["usable_for_orders"] is False
    # the executable-price and size columns go down empty
    assert args[28] is None                      # executable_price ($29)
    assert args[35] is None                      # proposed_size ($36)
    assert args[31] == "NO_TRADE"                # decision ($32)
    assert args[32] is False                     # admissible ($33)


def test_persist_refuses_a_calibration_record_that_could_trade():
    rec = dict(_sealed(), admissible=True, decision="BUY")
    with pytest.raises(ValueError, match=vp.R_CALIBRATION_ONLY):
        asyncio.run(ext.persist(_Recorder(), rec))
    bad = _sealed()
    bad["calibration_only_evidence"] = dict(
        bad["calibration_only_evidence"], usable_for_orders=True)
    with pytest.raises(ValueError):
        asyncio.run(ext.persist(_Recorder(), bad))
    with pytest.raises(ValueError, match=vp.R_UNKNOWN_PURPOSE):
        asyncio.run(ext.persist(_Recorder(),
                                dict(ext.evaluate(**_admissible_kwargs()),
                                     record_purpose="X")))


# ═════════════════════════════════════════════════════════════════════
# 3 · EVERY CONSUMER REFUSES IT BY NAME, WHATEVER `admissible` SAYS
# ═════════════════════════════════════════════════════════════════════

def _flipped():
    """The sealed record, with someone having flipped it to a BUY."""
    return dict(_sealed(), admissible=True, decision="BUY",
                proposed_size=10.0)


def test_inventory_planning_refuses_it_by_name():
    for rec in (_sealed(), _flipped()):
        plan = inv.plan_entry(rec, now=1001.0, outcome_index=0,
                              fee_fn=lambda qty, price: 0.0)
        assert plan["ok"] is False
        assert plan["refusals"] == [vp.R_CALIBRATION_ONLY]


def test_inventory_writing_refuses_a_hand_built_plan_before_any_read():
    plan = {"ok": True, "record_purpose": vp.CALIBRATION_ONLY,
            "position": {}, "order": {"order_id": "x"}, "fills": []}
    got = asyncio.run(inv.persist_entry(_Untouchable(), plan))
    assert got["written"] is False
    assert got["refusals"] == [vp.R_CALIBRATION_ONLY]


def test_payout_binding_refuses_before_reading_a_token():
    got = asyncio.run(loop.bind_payout_outcome(
        _Untouchable(), condition_id="c", payout_event="A",
        intent="ORDER_INTENT_BUY_LONG", record_purpose=vp.CALIBRATION_ONLY))
    assert got["ok"] is False and got["refusal"] == vp.R_CALIBRATION_ONLY
    got = asyncio.run(loop.bind_payout_outcome(
        _Untouchable(), condition_id="c", payout_event="A",
        intent="ORDER_INTENT_BUY_LONG", record_purpose="NOT_A_PURPOSE"))
    assert got["refusal"] == vp.R_UNKNOWN_PURPOSE


def test_the_funded_attempt_offers_it_to_nothing():
    got = asyncio.run(loop._funded_attempt(_Untouchable(), _flipped(),
                                           now=1001.0))
    assert got["ok"] is False and got["refusal"] == vp.R_CALIBRATION_ONLY


def test_the_funded_connector_refuses_it_before_the_schema_or_the_rails():
    rec = dict(_flipped(), us_market_slug="aec-d4-seal",
               event_key="e-d4-seal", order_intent="ORDER_INTENT_BUY_LONG")
    plan = FX.plan_from_decision(rec)
    assert plan["ok"] is False and plan["refusal"] == vp.R_CALIBRATION_ONLY
    got = asyncio.run(FX.submit_for_decision(
        _Untouchable(), rec, account_id="acct-d4", venue="PMUS",
        now=1001.0, size_to_approved_rails=True))
    assert got["ok"] is False and got["refusal"] == vp.R_CALIBRATION_ONLY
    assert got["submitted"] is False and got["exposure"] == "NONE"


def test_the_research_waiver_refuses_it_and_waives_nothing():
    state = {"STALE_DATA": None, "UNRESOLVED_SETTLEMENT_SEMANTICS": True,
             "OUT_OF_DISTRIBUTION": True, "MODEL_TRUST_DRIFT": None}
    got = rsh.waive(state, authorised_flag=True, calibration=None,
                    record_purpose=vp.CALIBRATION_ONLY)
    assert got["refusals"] == [vp.R_CALIBRATION_ONLY]
    assert got["waived"] == [] and got["state"] == state
    # the control: an authorised entry decision does get MODEL_TRUST_DRIFT
    ok = rsh.waive(state, authorised_flag=True, calibration=None)
    assert ok["waived"] == ["MODEL_TRUST_DRIFT"]


def test_stale_data_and_currency_are_never_waivable():
    """A book whose currency is not established leaves STALE_DATA unknown,
    and STALE_DATA is never waivable -- so the waiver cannot carry a
    currency-refused candidate anywhere, calibration-only or not."""
    from sportsassets import bettor_entry_execution as entryx

    assert "STALE_DATA" in rsh.NEVER_WAIVABLE
    assert rsh.WAIVABLE == frozenset({"MODEL_TRUST_DRIFT"})
    now = time.time()
    refused_read = {"book_currency": VC.evaluate(now=now),
                    "venue_ts": now - 2.0, "read_at": now}
    fr = loop._entry_freshness({"observed_at": now - 1.0,
                                "received_at": now - 0.5},
                               refused_read, now)
    assert fr["fresh"] is None and fr["unknown_side"] == "venue"
    gates = entryx.state_from_evidence(freshness=fr,
                                       settlement={"compatibility":
                                                   "COMPATIBLE"},
                                       probability=0.5, calibration=None)
    w = rsh.waive(gates["state"], authorised_flag=True, calibration=None)
    assert w["state"]["STALE_DATA"] is None
    assert "STALE_DATA" not in w["waived"]
    verdict = entryx.verdict("TAKE_YES", observed=None, state=w["state"])
    assert verdict.get("permitted") is not True


# ═════════════════════════════════════════════════════════════════════
# 4 · EVERY READER OF external_valuations IS ACCOUNTED FOR
# ═════════════════════════════════════════════════════════════════════

FILTERS = "record_purpose = 'ENTRY_DECISION'"


def test_every_reader_that_selects_candidates_filters_the_purpose():
    from sportsassets import bettor_entry_settlement as ES
    from sportsassets import bettor_funded_activation as FA
    from sportsassets import bettor_hold_value as HV
    from sportsassets import candidate_assessment as CA
    from sportsassets.api import command_rn1x as RN
    from sportsassets.workers import rn1x_shadow as RS

    for name, sql in (
            ("hold_value.LATEST_PROBABILITY_SQL", HV.LATEST_PROBABILITY_SQL),
            ("hold_value.LATEST_ANY_SQL", HV.LATEST_ANY_SQL),
            ("rn1x_shadow._CHALLENGER_VALUATION", RS._CHALLENGER_VALUATION),
            ("entry_settlement.CANDIDATE_IDENTITY_SQL",
             ES.CANDIDATE_IDENTITY_SQL),
            ("funded_activation.SETTLEMENT_SQL", FA.SETTLEMENT_SQL),
            ("funded_activation.SCOPE_SQL", FA.SCOPE_SQL),
            ("funded_activation.ADMITTED_SQL", FA.ADMITTED_SQL),
            ("funded_activation.ELIGIBLE_MARKET_SQL", FA.ELIGIBLE_MARKET_SQL),
            ("candidate_assessment.CENSUS_SQL", CA.CENSUS_SQL),
            ("command_rn1x.ENTRY_EVIDENCE", RN.ENTRY_EVIDENCE)):
        assert FILTERS in sql, name
    # the challenger's "held row" diagnostic filters too
    src = inspect.getsource(RS)
    held = src[src.index('"SELECT id, eligibility, ineligible_reason FROM "'):]
    assert "AND record_purpose = 'ENTRY_DECISION'" in held[:300]


def test_the_calibration_measurement_and_the_join_read_every_purpose():
    """They SHOULD see calibration-only rows: that is why the rows exist."""
    assert "record_purpose" not in CAL.ROWS_SQL
    assert "record_purpose" not in loop.UNJOINED_SQL


#: Every module under `sportsassets` that names `external_valuations`: what it
#: does with the table, and how many times it names it. A new module fails
#: this test until it is classified; a new mention in a classified module
#: changes its count and fails it too, so a reader added later has to be
#: looked at -- "grep every reader" as a standing check, not a one-off.
READERS = {
    # writes, or joins outcomes onto, the table; census reads are reporting
    "bettor_external_shadow.py": ("WRITER_AND_REPORTING_CENSUS", 9),
    "workers/ext_pinnacle_loop.py": ("WRITER_AND_OUTCOME_JOIN_READS_ALL", 8),
    # SHOULD read calibration-only rows
    "bettor_source_calibration.py": ("CALIBRATION_MEASUREMENT_READS_ALL", 3),
    # select candidates or a decision's probability -> filter the purpose
    # (asserted constant by constant above)
    "bettor_hold_value.py": ("FILTERS_ENTRY_DECISION", 4),
    "workers/rn1x_shadow.py": (
        "FILTERS_ENTRY_DECISION; COVERED_* READ ONLY EXISTENCE OF A SLUG", 7),
    "bettor_entry_settlement.py": ("FILTERS_ENTRY_DECISION", 1),
    "bettor_funded_activation.py": ("FILTERS_ENTRY_DECISION", 5),
    "candidate_assessment.py": ("FILTERS_ENTRY_DECISION", 2),
    "api/command_rn1x.py": (
        "ENTRY_EVIDENCE FILTERS; TILE, TRACE AND DIAGNOSTIC ARE REPORTING", 6),
    # by id of a row a filtered reader selected
    "bettor_funded_management.py": ("BY_ID_FROM_A_FILTERED_READER", 2),
    # Audrey: the daily audit and the improvement replay read ENTRY_DECISION
    # rows only (record_purpose filtered) to report and to evaluate candidate
    # thresholds by replay; neither selects a candidate or places anything
    "agents/audrey_audit.py": ("AUDIT_REPORTING_FILTERS_ENTRY_DECISION", 5),
    "agents/improvement.py": ("REPLAY_EVALUATION_FILTERS_ENTRY_DECISION", 5),
    # Audrey's chat reads ENTRY_DECISION rows (record_purpose filtered) to
    # answer why an entry was selected or refused; the workspace pages
    # name the table only in explanatory prose. Neither selects anything.
    "agents/audrey_chat.py": ("CHAT_READS_ENTRY_DECISION_FOR_ANSWERS", 16),
    "api/agent_pages.py": ("PROSE_ONLY", 3),
    # read-only reporting / prose / probes
    "bettor_capacity_fingerprint.py": ("COUNT_ONLY", 2),
    # counts rows by record_purpose for the management overview; selects
    # no candidate and no probability
    "api/command_overview.py": ("COUNT_BY_PURPOSE_REPORTING", 1),
    "api/app.py": ("GET_ONLY_SETTLEMENT_PROBE_PICK_AND_PROSE", 6),
    "bettor_demonstration.py": ("PROSE_ONLY", 1),
    "bettor_pilot_prerequisites.py": ("PROSE_ONLY", 1),
    "bettor_legacy_identity_repair.py": ("PROSE_ONLY_NEVER_READS", 4),
    "bettor_valuation_purpose.py": ("PROSE_ONLY", 1),
    # (1002) Derek: candidates and model labels filter ENTRY_DECISION; the
    # census counts both purposes and files CALIBRATION_ONLY as blocked by
    # name; the workspace reads rows by the id a Derek decision links to
    "agents/derek.py": ("FILTERS_ENTRY_DECISION", 3),
    "agents/derek_policy.py": ("FILTERS_ENTRY_DECISION", 5),
    "agents/coverage.py": ("REPORTING_CENSUS_CALIBRATION_ONLY_BLOCKED", 3),
    "api/agents_derek.py": ("BY_ID_FROM_A_FILTERED_READER", 4),
    # (V2) the RETROSPECTIVE replay of Derek's recorded decisions under V1
    # and V2: joins each decision's valuation by id for its outcome, with
    # record_purpose = 'ENTRY_DECISION' in the join; reports only, selects
    # no candidate and writes nothing
    "agents/derek_policy_replay.py": (
        "RETROSPECTIVE_REPLAY_BY_ID_FILTERS_ENTRY_DECISION", 1),
    # (170) Derek's NON-FUNDED RESEARCH observer and labeller: reads BOTH
    # purposes deliberately -- a calibration-only row's settlement is exactly
    # what the internal model's research needs -- and writes only
    # derek_research_observations, a table with no size, verdict or plan
    # whose price is CHECKed unusable for orders. It selects no candidate and
    # no execution module reads what it writes
    # (tests/test_derek_research_observations_break_the_deadlock.py).
    "agents/derek_research.py": (
        "RESEARCH_ONLY_READS_BOTH_PURPOSES_NEVER_SELECTS_A_CANDIDATE", 4),
    # (171/172) PAPER TRADING on the fictional $500,000 account. Derek's paper
    # decisions read BOTH purposes deliberately: in production every
    # valuation is CALIBRATION_ONLY (P5), and the owner's paper authorization
    # is to evaluate live markets anyway with that gap recorded on every
    # decision (QUOTE_TIMING_UNCERTAINTY_P5). What they select can only become
    # a SIMULATED paper order in paper_* tables: the funded executor refuses
    # any paper id or record first and no funded table is written
    # (tests/test_paper_records_cannot_reach_the_funded_path.py). The paper
    # runtime reads one row by the id the cycle just wrote; paper Xavier reads
    # the latest Pinnacle reading for a held paper contract and the venue's
    # joined settlement evidence to settle paper positions.
    "agents/paper_derek.py": (
        "PAPER_ONLY_READS_BOTH_PURPOSES_SIMULATED_ORDERS_NEVER_FUNDED", 3),
    "agents/paper_runtime.py": ("PAPER_ONLY_BY_ID_OF_THE_ROW_JUST_WRITTEN", 1),
    # (184) plus the venue's published settlement PRICE for a contract it
    # settled at a price (VENUE_PRICE_SQL): a settlement-evidence read by
    # slug for an already-held paper position, never a candidate selection.
    "agents/paper_xavier.py": (
        "PAPER_ONLY_MEASURE_AND_SETTLEMENT_EVIDENCE_READS", 8),
    # (182) THE EXPERIMENTAL PINNACLE_ONLY_PAPER_BENCHMARK: like paper Derek
    # it reads BOTH purposes deliberately (every production row is
    # CALIBRATION_ONLY under P5, disclosed as book_currency NOT_ESTABLISHED on
    # every record) and can only produce a SIMULATED paper order in paper_*
    # tables, priced on its own paper book read, never the row's displayed
    # quote; plus the Pinnacle-only measure of its held positions for Xavier.
    # The management pages' paper read model: joins a decision's own
    # valuation row for the market's readable name. Display only.
    "bettor_paper_ops.py": ("PAPER_ONLY_DISPLAY_JOIN_FOR_MARKET_NAMES", 1),
    "agents/paper_benchmark.py": (
        "PAPER_ONLY_READS_BOTH_PURPOSES_SIMULATED_ORDERS_NEVER_FUNDED", 5),
    # (185) THE PAPER LEARNING RECORD: the venue-joined OUTCOME of a paper
    # decision's contract, by slug, for the forward evaluation of a paper
    # improvement proposal (outcomes known by the evaluation instant only),
    # plus two mentions in labels (the decision-inputs SHA basis and a
    # settlement's evidence source). It selects no candidate and writes no
    # order of any kind.
    "agents/paper_learning.py": (
        "PAPER_ONLY_OUTCOME_READS_FOR_FORWARD_EVALUATION_NEVER_SELECTS", 3),
    # (189) THE BOUNDED EXPLORATION STRATEGY: its candidate is the row the
    # cycle just wrote, handed in by the benchmark hook (paper_benchmark,
    # above); its own read is the SAMPLING FRAME -- a count of distinct
    # fixtures per sport in the window, for the recorded inclusion
    # probability. It selects nothing from the table.
    "agents/paper_explore.py": (
        "PAPER_ONLY_SAMPLING_FRAME_COUNT_NEVER_SELECTS", 1),
    # (189) THE MAKER-ENTRY POLICY: its candidate also arrives through the
    # benchmark hook; its reads re-check an already RESTING paper order
    # against the latest Pinnacle reading for the same contract and payout
    # outcome (the cancellation conditions), like paper Xavier's measure.
    "agents/paper_maker.py": (
        "PAPER_ONLY_RESTING_ORDER_RECHECK_NEVER_SELECTS", 2),
    # (189) AUDREY'S OPERATIONAL AUDIT: counts valuations with no decision
    # from an enabled strategy (missing decisions). Read-only; no order.
    "agents/paper_ops_audit.py": (
        "PAPER_ONLY_AUDIT_COUNT_NEVER_SELECTS", 1),
    # (189) THE HOMEPAGE EXPERIMENT READ MODEL: counts of valuations,
    # fixtures and sports evaluated. Display only.
    "bettor_paper_experiment.py": (
        "PAPER_ONLY_DISPLAY_COUNTS_NEVER_SELECTS", 2),
}


def test_no_reader_of_the_table_is_unaccounted_for():
    root = pathlib.Path(loop.__file__).resolve().parents[1]
    found = {}
    for p in root.rglob("*.py"):
        n = p.read_text(errors="ignore").count("external_valuations")
        if n:
            found[str(p.relative_to(root))] = n
    unknown = set(found) - set(READERS)
    assert not unknown, ("unclassified reader(s) of external_valuations: "
                         "classify them in READERS and filter "
                         "record_purpose where they select candidates: %s"
                         % sorted(unknown))
    moved = {rel: (READERS[rel][1], n) for rel, n in found.items()
             if READERS[rel][1] != n}
    assert not moved, ("a classified module's mentions of external_valuations "
                       "changed (pinned, found): %s -- look at the new "
                       "reader, filter record_purpose if it selects "
                       "candidates, then re-pin" % moved)


# ═════════════════════════════════════════════════════════════════════
# 5 · THE CENSUS CLASSIFIES THE NEW CODES
# ═════════════════════════════════════════════════════════════════════

def test_a_calibration_record_is_attributed_to_the_venue_read_stage():
    refusals = [loop.R_BOOK_CURRENCY_NOT_ESTABLISHED,
                "EXECUTION_ESTIMATE_NOT_IDENTIFIED",
                "SIZING_POLICY_NOT_APPLICABLE", "RISK_GATE_BLOCKED",
                vp.R_CALIBRATION_ONLY]
    assert ext.first_stage(refusals) == "2_FRESHNESS"
    assert ext.evaluability(refusals) == ext.EXTERNAL_DEPENDENCY
    cyc = ext.cycle_evaluability([refusals])
    assert cyc["verdict"] != ext.V_CLASSIFICATION_HAS_DRIFTED, cyc
    for code in (loop.R_BOOK_CURRENCY_CONTRADICTED, loop.R_OUR_PROCESSING_DELAY,
                 vp.R_CALIBRATION_ONLY, loop.R_BOOK_CURRENCY_NOT_ESTABLISHED):
        assert code in ext.EVALUABILITY_OF, code


# ═════════════════════════════════════════════════════════════════════
# 6 · D8: THE VERDICT IS TAKEN AT OR AFTER THE READ
# ═════════════════════════════════════════════════════════════════════

def _iso(t):
    return (_dt.datetime.fromtimestamp(t, _dt.timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z")


def _book():
    """A SYNTHETIC venue book in the SDK's marketData shape."""
    lvl = lambda p, q: {"px": {"value": "%.2f" % p, "currency": "USD"},
                        "qty": str(q)}
    return {"marketData": {"offers": [lvl(0.62, 400), lvl(0.64, 300)],
                           "bids": [lvl(0.60, 400)],
                           "transactTime": _iso(time.time() - 2.0)}}


class _Conn:
    pass


def _quote(monkeypatch, *, now, subscription):
    monkeypatch.setattr(loop, "_read_book_blocking", lambda _slug: _book())
    # THE LISTING ROW'S TICK (SYNTHETIC 0.01), the other venue read an
    # orderable ladder needs: the executable grid refuses an unread tick.
    monkeypatch.setattr(loop, "_read_venue_rules_blocking",
                        lambda _slug, **_k: {
                            "tick_size": "0.01",
                            "tick_field": "orderPriceMinTickSize",
                            "read_at": time.time(), "from_cache": False})
    return asyncio.run(loop.venue_quote(
        _Conn(), us_slug="aec-mlb-d4-d8-2026-10-02",
        intent="ORDER_INTENT_BUY_LONG", now=now, subscription=subscription))


def test_currency_is_judged_after_the_read_not_at_the_callers_instant(
        monkeypatch):
    """A subscription current at the caller's pre-read instant and silent by
    the time the payload is ours: judged at the pre-read instant (the D8
    defect) it read ESTABLISHED; judged at the receipt it is not."""
    pre = time.time() - 20.0
    sub = {"alive_at": pre, "last_update_at": pre - 5.0}
    assert VC.evaluate(now=pre, subscription=sub,
                       bound_s=loop.MAX_VENUE_QUOTE_AGE_S)["verdict"] \
        == VC.ESTABLISHED, "the defect's instant would have admitted it"
    vq = _quote(monkeypatch, now=pre, subscription=sub)
    assert vq["ok"] is False
    assert vq["refusal"] == loop.R_BOOK_CURRENCY_NOT_ESTABLISHED
    clock = vq["venue_clock"]
    assert clock["verdict_instant_epoch_s"] >= \
        clock["our_response_received_at"]
    assert clock["caller_instant_epoch_s"] == pytest.approx(pre)
    assert clock["our_processing_delay_s"] >= 0.0


def test_a_later_decision_instant_can_fire_our_processing_delay(monkeypatch):
    later = time.time() + 20.0
    sub = {"alive_at": later - 1.0, "last_update_at": later - 2.0}
    vq = _quote(monkeypatch, now=later, subscription=sub)
    assert vq["ok"] is False
    assert vq["refusal"] == loop.R_OUR_PROCESSING_DELAY
    assert vq["age_s"] > loop.MAX_OUR_PROCESSING_DELAY_S


def test_with_no_caller_instant_the_verdict_is_at_the_receipt(monkeypatch):
    now = time.time()
    sub = {"alive_at": now, "last_update_at": now - 1.0}
    vq = _quote(monkeypatch, now=None, subscription=sub)
    assert vq["ok"] is True, vq.get("refusal")
    clock = vq["venue_clock"]
    assert clock["verdict_instant_epoch_s"] == \
        clock["our_response_received_at"]
    assert clock["our_processing_delay_s"] == 0.0


def test_a_currency_refusal_carries_the_displayed_book_under_no_order_key(
        monkeypatch):
    vq = _quote(monkeypatch, now=None, subscription=None)
    assert vq["refusal"] == loop.R_BOOK_CURRENCY_NOT_ESTABLISHED
    shown = vq["displayed_not_for_orders"]
    assert shown["usable_for_orders"] is False
    assert shown["acquisition_price"] == pytest.approx(0.62)
    assert shown["book_currency_verdict"] == VC.NOT_ESTABLISHED
    for order_key in ("ask", "acquisition_price", "acquisition_ladder",
                      "depth", "sized"):
        assert order_key not in vq, order_key
    basis = loop._calibration_only_basis(vq)
    assert basis["displayed"]["usable_for_orders"] is False
    ms = loop._displayed_market_state(basis)
    assert "NOT_USABLE_FOR_ORDERS" in ms["ask_basis"]
    # a read that FAILED builds nothing
    assert loop._calibration_only_basis(
        {"ok": False, "refusal": loop.R_VENUE_READ_FAILED}) is None
    assert loop._calibration_only_basis(
        {"ok": False, "refusal": loop.R_BOOK_CURRENCY_NOT_ESTABLISHED}) \
        is None, "a quote without the displayed block builds nothing"


# ═════════════════════════════════════════════════════════════════════
# 7 · THE DATABASE (migration 144)
# ═════════════════════════════════════════════════════════════════════

async def _connect():
    import asyncpg

    return await asyncpg.connect(DSN)


TEST_EXPERIMENT = "D4_TEST_CALIBRATION_ONLY_BOUNDARY"


async def _clean_db(conn):
    await conn.execute(
        "DELETE FROM rn1x_positions WHERE experiment_id=$1", TEST_EXPERIMENT)
    await conn.execute(
        "DELETE FROM rn1x_experiments WHERE experiment_id=$1",
        TEST_EXPERIMENT)
    await conn.execute(
        "DELETE FROM external_valuations WHERE condition_id LIKE 'c-d4-db-%'")


def _db_rec(tag, *, calibration_only):
    kw = _admissible_kwargs()
    kw["contract"] = dict(kw["contract"], condition_id="c-d4-db-%s" % tag,
                          us_market_slug="aec-d4-db-%s" % tag,
                          event_key="e-d4-db-%s" % tag)
    kw["quote"] = dict(kw["quote"], observed_at=time.time() - 1.0,
                       received_at=time.time() - 0.5,
                       event_key="e-d4-db-%s" % tag)
    kw["now"] = time.time()
    if calibration_only:
        return ext.evaluate(**kw, record_purpose=vp.CALIBRATION_ONLY,
                            calibration_only_evidence=_evidence())
    return ext.evaluate(**kw)


async def _raises(conn, sql, *args, match):
    import asyncpg

    with pytest.raises(asyncpg.PostgresError) as got:
        await conn.execute(sql, *args)
    assert match in str(got.value), str(got.value)


@pg
@pytest.mark.asyncio
async def test_the_database_refuses_a_calibration_row_that_could_trade():
    conn = await _connect()
    try:
        await _clean_db(conn)
        cid = await ext.persist(conn, _db_rec("cal", calibration_only=True))
        eid = await ext.persist(conn, _db_rec("ent", calibration_only=False))
        assert cid and eid
        row = await conn.fetchrow(
            "SELECT record_purpose, admissible, executable_price, "
            "proposed_size, execution_estimate, calibration_only_evidence "
            "FROM external_valuations WHERE id=$1", cid)
        assert row["record_purpose"] == vp.CALIBRATION_ONLY
        assert row["admissible"] is False
        assert row["executable_price"] is None
        assert json.loads(row["calibration_only_evidence"])[
            "usable_for_orders"] is False
        assert await conn.fetchval(
            "SELECT record_purpose FROM external_valuations WHERE id=$1",
            eid) == vp.ENTRY_DECISION

        # FORCING IT ADMISSIBLE -- with every column 103 wants, so only
        # migration 144 stands in the way -- is rejected.
        await _raises(conn,
                      "UPDATE external_valuations SET admissible=true, "
                      "decision='BUY', executable_price=0.05, "
                      "cost_per_contract=0.0, estimated_edge_per_contract=0.1 "
                      "WHERE id=$1", cid,
                      match="external_valuations_calibration_only_cannot_trade")
        await _raises(conn,
                      "UPDATE external_valuations SET proposed_size=10 "
                      "WHERE id=$1", cid,
                      match="external_valuations_calibration_only_cannot_trade")
        # RELABELLING IT, either way, is rejected.
        await _raises(conn,
                      "UPDATE external_valuations SET "
                      "record_purpose='ENTRY_DECISION', "
                      "calibration_only_evidence=NULL WHERE id=$1", cid,
                      match="fixed at insert")
        await _raises(conn,
                      "UPDATE external_valuations SET "
                      "calibration_only_evidence='{\"usable_for_orders\":"
                      "false}'::jsonb WHERE id=$1", cid,
                      match="fixed at insert")
        await _raises(conn,
                      "UPDATE external_valuations SET "
                      "record_purpose='CALIBRATION_ONLY' WHERE id=$1", eid,
                      match="fixed at insert")
        # AN UNDECLARED PURPOSE, AND EVIDENCE CLAIMING ORDER USE, ARE REJECTED
        # at insert: copy the calibration row with one field changed.
        base = ("INSERT INTO external_valuations (experiment_id, version, "
                "source_class, provider, book, devig_method, venue, "
                "condition_id, us_market_slug, contract_selection, "
                "sport_family, market, raw_odds, outcomes_priced, "
                "expected_outcomes, decision, admissible, record_purpose, "
                "calibration_only_evidence) VALUES ($1, 'PINNACLE_DEVIG_V1', "
                "'EXTERNAL_BOOKMAKER_VALUATION', 'the-odds-api', 'pinnacle', "
                "'power', 'V', 'c-d4-db-raw', 'aec-d4-db-raw', 'A', "
                "'soccer', 'h2h', '{}'::jsonb, 3, 3, $2, $3, $4, $5::jsonb)")
        # (whichever of the two checks Postgres evaluates first names it)
        await _raises(conn, base, ext.EXPERIMENT_ID, "NO_TRADE", False,
                      "SOMETHING_ELSE", None,
                      match="violates check constraint")
        assert await conn.fetchval(
            "SELECT count(*) FROM pg_constraint WHERE conname = "
            "'external_valuations_record_purpose_declared'") == 1
        await _raises(conn, base, ext.EXPERIMENT_ID, "NO_TRADE", False,
                      vp.CALIBRATION_ONLY,
                      json.dumps({"usable_for_orders": True}),
                      match="external_valuations_calibration_evidence_matches")
        await _raises(conn, base, ext.EXPERIMENT_ID, "NO_TRADE", False,
                      vp.CALIBRATION_ONLY, None,
                      match="external_valuations_calibration_evidence_matches")
        await _raises(conn, base, ext.EXPERIMENT_ID, "NO_TRADE", False,
                      vp.ENTRY_DECISION,
                      json.dumps({"usable_for_orders": False}),
                      match="external_valuations_calibration_evidence_matches")

        # NO POSITION FROM A CALIBRATION ROW; an entry row is the control.
        await inv.ensure_experiment(conn, TEST_EXPERIMENT)
        pos = ("INSERT INTO rn1x_positions (position_id, experiment_id, "
               "policy, source_account, condition_id, outcome_index, "
               "entry_kind, entry_kind_why, seed_qty, seed_price, "
               "seed_basis_usd, source_ts, detected_ts, decision_ts, "
               "source_valuation_id) VALUES ($1, $2, 'D4_TEST_POLICY', "
               "'D4_TEST', 'c-d4-db-pos', 0, 'D4_TEST', 'synthetic', 1, 0.5, "
               "0.5, now(), now(), now(), $3)")
        await _raises(conn, pos, "d4-pos-cal", TEST_EXPERIMENT, cid,
                      match=vp.R_CALIBRATION_ONLY)
        await conn.execute(pos, "d4-pos-ent", TEST_EXPERIMENT, eid)
        await _raises(conn,
                      "UPDATE rn1x_positions SET source_valuation_id=$1 "
                      "WHERE position_id='d4-pos-ent'", cid,
                      match=vp.R_CALIBRATION_ONLY)
        # THE OUTCOME JOIN MAY STILL LABEL IT: outcome columns are not the
        # purpose.
        await conn.execute(
            "UPDATE external_valuations SET decided_at = now() - "
            "interval '3 hours' WHERE id=$1", cid)
        await conn.execute(loop.JOIN_RESOLVED_SQL, cid, 1, time.time(),
                           loop.B_SETTLEMENT_PRICE, loop.SIDE_LONG, "1")
        assert await conn.fetchval(
            "SELECT outcome FROM external_valuations WHERE id=$1", cid) == 1
    finally:
        await _clean_db(conn)
        await conn.close()


@pg
def test_the_rollback_refuses_while_calibration_rows_exist():
    """144's down migration must not silently turn calibration rows into
    rows that read as entry decisions. Run inside a transaction that is
    always rolled back, so the live schema is untouched."""
    from tests.conftest import backend_path

    down = backend_path("migrations", "rollback",
                        "144_calibration_only_valuations.down.sql").read_text()

    async def go():
        conn = await _connect()
        try:
            tr = conn.transaction()
            await tr.start()
            try:
                await ext.persist(conn, _db_rec("rb", calibration_only=True))
                import asyncpg
                with pytest.raises(asyncpg.PostgresError,
                                   match="not rolled back"):
                    await conn.execute(down)
            finally:
                await tr.rollback()
            # and with none present it applies (then rolled back too)
            tr = conn.transaction()
            await tr.start()
            try:
                await conn.execute(
                    "DELETE FROM external_valuations "
                    "WHERE record_purpose <> 'ENTRY_DECISION'")
                await conn.execute(down)
                assert not await conn.fetchval(
                    "SELECT count(*) FROM information_schema.columns WHERE "
                    "table_name='external_valuations' AND "
                    "column_name='record_purpose'")
            finally:
                await tr.rollback()
            assert await conn.fetchval(
                "SELECT count(*) FROM information_schema.columns WHERE "
                "table_name='external_valuations' AND "
                "column_name='record_purpose'") == 1
        finally:
            await conn.close()

    asyncio.run(go())


# ═════════════════════════════════════════════════════════════════════
# 8 · THROUGH THE SCHEDULED ENTRY PATH
# ═════════════════════════════════════════════════════════════════════

def _capture_persist(monkeypatch):
    """Record every record the cycle persists, then persist it for real."""
    seen: list = []
    real = ext.persist

    async def recording(conn, rec):
        seen.append(rec)
        return await real(conn, rec)

    monkeypatch.setattr(loop.ext, "persist", recording)
    return seen


@pg
@pytest.mark.asyncio
async def test_the_scheduled_path_records_a_calibration_row_and_nothing_trades(
        monkeypatch):
    from sportsassets import bettor_live_read as lr

    from tests import _emptybook_fixture as F

    conn = await _connect()
    venue = F.Venue()
    try:
        await F.clean(conn)
        await F.seed(conn)
        # THE FIXTURE SUPPLIES ACTIVATION AND TURNS THE SUBMISSION SWITCHES
        # ON -- the strongest setting for this test: if anything could carry
        # a calibration-only record to a venue, it would here.
        F.substitute(monkeypatch, venue)
        # ...AND THE PRODUCTION CURRENCY SEAM IS PUT BACK, which supplies no
        # mechanism: every read refuses VENUE_BOOK_CURRENCY_NOT_ESTABLISHED.
        monkeypatch.setattr(loop, "book_currency_evidence", _SHIPPED_BCE)
        assert _SHIPPED_BCE(F.US_SLUG)["subscription"] is None
        seen = _capture_persist(monkeypatch)

        out = await loop.cycle(conn)
        assert out["ran"] is True, out.get("why")

        # ── THE EVENT STAYS COUNTED UNDER THE CURRENCY REFUSAL ────────
        assert out["refusals"].get(loop.R_BOOK_CURRENCY_NOT_ESTABLISHED) \
            == 1, out["refusals"]
        for code in ("ADMITTED", "ENTRY_INVENTORY_WRITTEN",
                     "EXPOSURE_RESERVED"):
            assert code not in out["refusals"], out["refusals"]
        assert not any(k.startswith("FUNDED:") for k in out["refusals"])
        assert out["evaluated"] == 0 and out["written"] == 0
        assert out["cycle_label"] == loop.ZERO_EVALUATED_INPUT_PATH_BLOCKED
        assert out["valuations_recorded_inadmissible_for_calibration"] == 1
        assert out["calibration_only"]["recorded"] == 1
        assert out["entries"] == []
        ev_rows = [r for r in out["event_ledger"]
                   if r["provider_event_id"] == F.ODDS_EVENT]
        assert ev_rows and ev_rows[0]["outcome"] == "REFUSED"
        assert ev_rows[0]["first_refusal"] == \
            loop.R_BOOK_CURRENCY_NOT_ESTABLISHED
        led = [e for e in out["mapped_candidate_ledger"]
               if e.get("us_market_slug") == F.US_SLUG]
        assert led[0]["first_refusal"] == loop.R_BOOK_CURRENCY_NOT_ESTABLISHED
        assert led[0]["calibration_only_record"] == "RECORDED"

        # ── THE ROW: ITS PURPOSE AND ITS EVIDENCE, EXPLICIT ──────────
        row = await conn.fetchrow(
            "SELECT id, record_purpose, admissible, decision, refusals, "
            "probability, executable_price, cost_per_contract, "
            "estimated_edge_per_contract, proposed_size, execution_estimate, "
            "risk_verdict, exposure_observed, calibration_only_evidence, "
            "us_market_slug, event_key, settlement_comparison "
            "FROM external_valuations WHERE condition_id=$1", F.CONDITION)
        assert row["record_purpose"] == vp.CALIBRATION_ONLY
        assert row["admissible"] is False and row["decision"] == "NO_TRADE"
        refusals = list(row["refusals"])
        assert refusals[0] == loop.R_BOOK_CURRENCY_NOT_ESTABLISHED
        assert vp.R_CALIBRATION_ONLY in refusals
        # every later check that could still be evaluated ran and is named
        for later in ("EXECUTION_ESTIMATE_NOT_IDENTIFIED",
                      "SIZING_POLICY_NOT_APPLICABLE", "RISK_GATE_BLOCKED"):
            assert later in refusals, refusals
        assert row["settlement_comparison"] is not None
        for empty in ("executable_price", "cost_per_contract",
                      "estimated_edge_per_contract", "proposed_size",
                      "execution_estimate", "risk_verdict",
                      "exposure_observed"):
            assert row[empty] is None, empty
        assert row["probability"] is not None
        assert row["us_market_slug"] == F.US_SLUG
        assert row["event_key"] == F.ODDS_EVENT
        ev = json.loads(row["calibration_only_evidence"])
        assert ev["usable_for_orders"] is False
        assert ev["venue_read_refusal"] == loop.R_BOOK_CURRENCY_NOT_ESTABLISHED
        assert ev["displayed_quote"]["usable_for_orders"] is False
        assert ev["displayed_quote"]["acquisition_price"] == \
            pytest.approx(F.OFFERS[0][0])
        assert ev["book_currency"]["verdict"] == VC.NOT_ESTABLISHED
        assert ev["state_gates"]["state"]["STALE_DATA"] is None
        assert "NOT_EVALUATED" in ev["rails"]

        # ── NOTHING TRADED: no inventory, intent, reservation or order ─
        assert await conn.fetchval(
            "SELECT count(*) FROM rn1x_positions WHERE condition_id=$1",
            F.CONDITION) == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_intents WHERE account_id=$1",
            F.ACCT) == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_leg_reservations WHERE "
            "group_id IN (SELECT group_id FROM bettor_funded_portfolio_groups "
            "WHERE account_id=$1)", F.ACCT) == 0
        assert venue.creates_sent() == []
        assert out["open_book_rows"] == 0

        # ── FORCING IT ADMISSIBLE IS REJECTED BY THE DATABASE ────────
        import asyncpg
        with pytest.raises(asyncpg.PostgresError):
            await conn.execute(
                "UPDATE external_valuations SET admissible=true, "
                "decision='BUY' WHERE id=$1", row["id"])

        # ── EACH CONSUMER, HANDED THE CYCLE'S OWN RECORD, REFUSES ─────
        rec, = [r for r in seen
                if (r.get("contract") or {}).get("condition_id")
                == F.CONDITION]
        assert rec["record_purpose"] == vp.CALIBRATION_ONLY
        for candidate in (rec, dict(rec, admissible=True, decision="BUY")):
            b = await loop.bind_payout_outcome(
                conn, condition_id=F.CONDITION, payout_event=F.HOME,
                intent="ORDER_INTENT_BUY_LONG",
                record_purpose=candidate.get("record_purpose"))
            assert b["refusal"] == vp.R_CALIBRATION_ONLY
            p = inv.plan_entry(candidate, now=time.time(), outcome_index=0,
                               fee_fn=lambda qty, price: 0.0)
            assert p["refusals"] == [vp.R_CALIBRATION_ONLY]
            w = await inv.persist_entry(conn, dict(
                p, ok=True, record_purpose=vp.CALIBRATION_ONLY))
            assert w["refusals"] == [vp.R_CALIBRATION_ONLY]
            f = await loop._funded_attempt(conn, dict(
                candidate, event_key=F.ODDS_EVENT,
                us_market_slug=F.US_SLUG,
                order_intent="ORDER_INTENT_BUY_LONG"), now=time.time())
            assert f["refusal"] == vp.R_CALIBRATION_ONLY
            s = await FX.submit_for_decision(
                conn, dict(candidate, us_market_slug=F.US_SLUG,
                           event_key=F.ODDS_EVENT,
                           order_intent="ORDER_INTENT_BUY_LONG"),
                account_id=F.ACCT, venue=F.VENUE, now=time.time(),
                size_to_approved_rails=True)
            assert s["refusal"] == vp.R_CALIBRATION_ONLY
            wv = rsh.waive({"MODEL_TRUST_DRIFT": None},
                           authorised_flag=True,
                           record_purpose=candidate.get("record_purpose"))
            assert wv["refusals"] == [vp.R_CALIBRATION_ONLY]
        assert venue.creates_sent() == []
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_intents WHERE account_id=$1",
            F.ACCT) == 0

        # ── THE COMMAND CENTRE DOES NOT SHOW IT AS A CANDIDATE ───────
        from sportsassets.api import command_rn1x as RN
        evd = await RN.entry_evidence(conn, hours=1, limit=200)
        assert row["id"] not in [c["id"] for c in evd["candidates"]]
        assert evd["calibration_only_valuations_in_window"] >= 1

        # ── THE HEARTBEAT CARRIES THE COUNTER AND THE MEASUREMENT ────
        hb = json.loads(await conn.fetchval(
            "SELECT value::text FROM ingestion_state WHERE key=$1",
            loop.HEARTBEAT_KEY))
        assert hb["valuations_recorded_inadmissible_for_calibration"] == 1
        assert hb["calibration_only"]["recorded"] == 1
        scm = hb["source_calibration_measurement"]
        for k in ("status", "resolved_fixtures", "scored_events",
                  "shortfall", "would_write", "written", "next_due_at",
                  "last_ran_at"):
            assert k in scm, k

        # ── THE OUTCOME JOIN AND THE MEASUREMENT STILL SEE IT ────────
        await conn.execute(
            "UPDATE external_valuations SET decided_at = now() - "
            "interval '3 hours' WHERE id=$1", row["id"])
        queued = [r["id"] for r in await conn.fetch(
            loop.UNJOINED_SQL, ext.EXPERIMENT_ID, 500)]
        assert row["id"] in queued

        def settled(slug):
            # SYNTHETIC SETTLEMENT: the fixture's contract settles YES; any
            # other row still queued in this database is left pending.
            if slug == F.US_SLUG:
                return {"status": lr.RESOLVED, "settlement_price": 1.0,
                        "settlement_price_raw": "1"}
            return {"status": lr.PENDING}

        monkeypatch.setattr(loop, "_read_resolution_blocking", settled)
        joined = await loop.join_outcomes(conn, limit=500)
        assert joined["resolved"] >= 1, joined
        got = await conn.fetchrow(
            "SELECT outcome_known, outcome, outcome_basis, record_purpose "
            "FROM external_valuations WHERE id=$1", row["id"])
        assert got["outcome_known"] is True and got["outcome"] == 1
        assert got["outcome_basis"] == loop.B_SETTLEMENT_PRICE
        assert got["record_purpose"] == vp.CALIBRATION_ONLY
        rows = [dict(r) for r in await conn.fetch(
            CAL.ROWS_SQL, ext.EXPERIMENT_ID, "90")]
        mine = [r for r in rows if r["id"] == row["id"]]
        assert mine and CAL.in_scope(mine[0])
        assert CAL.classify(mine[0]) == CAL.RESOLVED
        m = await CAL.measure(conn, experiment_id=ext.EXPERIMENT_ID,
                              days=90, now=time.time(),
                              measured_by="D4_TEST_NOT_A_MEASUREMENT",
                              write=False)
        assert m["ran"] is True and m["resolved_fixtures"] >= 1
        assert m["cohort_shortfall"]["resolved_fixtures"] == \
            m["resolved_fixtures"]
        assert m["written"] is False
    finally:
        await F.clean(conn)
        await conn.close()


#: The shipped currency seam, captured at import -- before any test's
#: monkeypatch replaces the module attribute.
_SHIPPED_BCE = loop.book_currency_evidence


@pg
@pytest.mark.asyncio
async def test_with_currency_established_the_entry_path_is_unchanged(
        monkeypatch):
    """THE CONTROL, and the lifecycle proof's own expectations: with the
    supplied currency the candidate is admitted, written as an ENTRY_DECISION
    (the column-default purpose, no evidence), carried to inventory and to the funded
    connector exactly as before -- and no calibration-only row appears."""
    from tests import _emptybook_fixture as F

    conn = await _connect()
    venue = F.Venue(split=[50, 35])
    try:
        await F.clean(conn)
        await F.seed(conn)
        F.substitute(monkeypatch, venue)
        seen = _capture_persist(monkeypatch)
        out = await loop.cycle(conn)
        assert out["evaluated"] == 1
        assert out["refusals"].get("ADMITTED") == 1, out["refusals"]
        assert out["refusals"].get("FUNDED:SUBMITTED") == 1, out["refusals"]
        assert out["valuations_recorded_inadmissible_for_calibration"] == 0
        assert out["calibration_only"]["attempted"] == 0
        rec, = [r for r in seen
                if (r.get("contract") or {}).get("condition_id")
                == F.CONDITION]
        assert vp.purpose_of(rec) == vp.ENTRY_DECISION
        assert "calibration_only_evidence" not in rec
        r = _Recorder()
        await ext.persist(r, rec)
        assert r.calls[0][0] is ext.INSERT
        assert r.calls[0][1][48:] == (vp.ENTRY_DECISION, None)
        row = await conn.fetchrow(
            "SELECT record_purpose, calibration_only_evidence, admissible, "
            "decision, executable_price, proposed_size, execution_estimate "
            "FROM external_valuations WHERE condition_id=$1", F.CONDITION)
        assert row["record_purpose"] == vp.ENTRY_DECISION
        assert row["calibration_only_evidence"] is None
        assert row["admissible"] is True and row["decision"] == "BUY"
        assert row["executable_price"] is not None
        assert row["proposed_size"] is not None
        assert row["execution_estimate"] is not None
        assert await conn.fetchval(
            "SELECT count(*) FROM rn1x_positions WHERE condition_id=$1",
            F.CONDITION) == 1
        assert len(venue.creates_sent()) == 1
    finally:
        await F.clean(conn)
        await conn.close()
