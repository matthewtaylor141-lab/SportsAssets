"""EXPERIMENT-MECHANICS REPAIR: EXIT LIFECYCLE, CLOCKS, RE-ENTRY.

Owner directive 2026-09-20, three approved items in priority order:

  1  make the already-declared exit rule executable
  2  separate the clock domains
  3  correct X1C's re-entry enforcement prospectively

  §2 "If the frozen declaration is insufficient to determine WHEN TO
      EXIT, WHAT ACTION TO TAKE, WHAT PRICE TO USE, HOW MUCH QUANTITY
      TO EXIT, stop and name the missing semantics. Do not fill them
      in after seeing results."

  §11 "the rule must become actual enforcement rather than accidental
      compliance caused by the ~65s tick cadence."

THE FILE'S SPINE. The exit TRIGGER is deliberately absent, and these
tests assert its absence as a feature: two semantics the frozen rule
does not carry would each change the realised P&L of a cohort already
known to be negative, so choosing them now is the act §2 forbids. What
IS built -- the append-only lifecycle ledger, the re-entry guard, the
clock contract -- is tested here in full.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sportsassets import shadow_exit_semantics as xsem
from sportsassets import shadow_experiment_registry as reg
from sportsassets import shadow_latency_integrity as lat
from sportsassets import shadow_position_lifecycle as lc
from sportsassets import shadow_reentry_guard as rg

T0 = datetime(2026, 9, 20, 1, 56, 50, tzinfo=timezone.utc)
X1 = "X1_SHORT_HORIZON_DIRECTION"
X1C = "X1C_NULL_CONTROL"

X1_DECL = [e for e in reg.EXPERIMENTS if e["experimentId"] == X1][0]
X1C_DECL = [e for e in reg.EXPERIMENTS if e["experimentId"] == X1C][0]


def ev(kind, *, at, seq=0, qty=None, notional=None, status=None,
       book="bk1", pid="xpos_1", experiment=X1, market="m1"):
    return {"position_event_id": "xpe_%s_%d" % (kind, seq),
            "position_id": pid, "experiment_id": experiment,
            "market_id": market, "event_type": kind, "event_at": at,
            "sequence_no": seq, "qty_delta": qty,
            "notional_delta_usd": notional, "vwap": None,
            "intended_qty": None, "filled_qty": None,
            "unfilled_qty": None, "execution_status": status,
            "book_sha": book, "latency_status": None, "why": None}


# ── §2: the exit rule, read before it is implemented ─────────────────


def test_three_of_the_four_questions_are_answered_by_the_frozen_spec():
    out = xsem.exit_mechanism_status()
    assert set(out["answered"]) == {"WHEN TO EXIT", "WHAT ACTION TO TAKE",
                                    "WHAT PRICE TO USE"}


def test_the_quantity_question_is_not_answered_and_blocks_the_trigger():
    """THE CENTRAL REFUSAL. The rule says 'exit'; production books are
    thin enough that every X1 entry filled partially. What happens to a
    residual the book cannot absorb is simply not in the frozen text."""
    out = xsem.exit_mechanism_status()
    assert "HOW MUCH QUANTITY TO EXIT" in out["missing"]
    assert out["X1_EXIT_MECHANISM_STATUS"] == xsem.BLOCKED


def test_the_missing_book_tolerance_also_blocks_it():
    """'the book observed at that instant' on a ~61s grid. The markout
    rule declares a tolerance; the EXIT rule declares none, and the
    number chosen would decide the realised P&L."""
    out = xsem.exit_mechanism_status()
    assert "EXIT_BOOK_TOLERANCE" in out["missing"]


def test_the_frozen_definition_is_quoted_not_paraphrased():
    out = xsem.exit_mechanism_status()
    assert out["X1_EXIT_RULE_FROZEN_DEFINITION"] == reg.EXIT_RULE_HORIZON
    assert "no re-entry inside the horizon" in out[
        "X1_EXIT_RULE_FROZEN_DEFINITION"]


def test_the_horizon_anchor_disagreement_is_named_not_reconciled():
    """The spec anchors the horizon on ARRIVAL twice; the implemented
    markout anchors on DECISION. Named, because silently re-anchoring
    would alter evidence already recorded."""
    out = xsem.exit_mechanism_status()
    named = [i["question"] for i in out["inconsistencies"]]
    assert "WHEN TO EXIT" in named
    detail = out["inconsistencies"][0]["detail"]
    assert "decision_timestamp, not arrival" in detail


def code_only(module) -> str:
    """The module's CODE, with prose and string literals gone.

    These modules explain at length WHY they do not do a thing, so the
    word appears in the explanation. Scanning raw source would match
    the sentence promising the behaviour rather than the behaviour.
    """
    import inspect
    import io
    import tokenize
    kept = []
    for tok in tokenize.generate_tokens(
            io.StringIO(inspect.getsource(module)).readline):
        if tok.type in (tokenize.NAME, tokenize.OP, tokenize.NUMBER):
            kept.append(tok.string)
    return " ".join(kept).lower()


def test_no_exit_trigger_exists_anywhere_in_the_module():
    """§2 forbids inventing the rule. The module reads and reports; it
    does not decide an exit."""
    src = code_only(xsem)
    for forbidden in ("exit_now", "trigger", "decide_exit",
                      "marketable_fill"):
        assert forbidden not in src, forbidden


# ── §1/§3: the lifecycle, folded from append-only events ─────────────


def test_a_position_that_only_opened_reads_open():
    out = lc.fold([ev(lc.OPENED, at=T0, qty=3000.0, notional=920.0)])
    assert out["state"] == lc.OPEN
    assert out["remainingQty"] == 3000.0
    assert out["realizedShadowPnlUsd"] is None


def test_the_existing_cohort_reads_exit_mechanism_unavailable():
    """§3: 'Do not pretend they historically exited.' They opened under
    infrastructure that could not close them, and that is its own
    state -- not OPEN, and not a missed exit."""
    out = lc.fold([ev(lc.OPENED, at=T0, qty=3000.0, notional=920.0),
                   ev(lc.UNAVAILABLE, at=T0)])
    assert out["state"] == lc.EXIT_MECHANISM_UNAVAILABLE
    assert out["exitMechanismUnavailableAtEntry"] is True
    assert "could not execute the declared exit rule" in out["why"]


def test_unavailable_outranks_eligible():
    """A horizon that passed while no exit could run does not make the
    position 'owed an exit the system declined to take'."""
    out = lc.fold([ev(lc.OPENED, at=T0, qty=3000.0, notional=920.0),
                   ev(lc.UNAVAILABLE, at=T0),
                   ev(lc.ELIGIBLE, at=T0 + timedelta(seconds=60))])
    assert out["state"] == lc.EXIT_MECHANISM_UNAVAILABLE


def test_a_partial_exit_reads_partially_exited_and_releases_pro_rata():
    out = lc.fold([
        ev(lc.OPENED, at=T0, qty=3000.0, notional=920.0),
        ev(lc.EXECUTED, at=T0 + timedelta(seconds=60), qty=-1000.0,
           notional=-300.0, status="PARTIAL"),
    ])
    assert out["state"] == lc.PARTIALLY_EXITED
    assert out["remainingQty"] == 2000.0
    assert out["capitalReleasedUsd"] == pytest.approx(920.0 / 3.0, abs=1e-4)
    # §5: realized ONLY from the executed exit, against the pro-rata
    # entry it released.
    assert out["realizedShadowPnlUsd"] == pytest.approx(
        300.0 - 920.0 / 3.0, abs=1e-4)


def test_a_full_exit_reads_closed():
    out = lc.fold([
        ev(lc.OPENED, at=T0, qty=3000.0, notional=920.0),
        ev(lc.EXECUTED, at=T0 + timedelta(seconds=60), qty=-3000.0,
           notional=-900.0, status="FILLED"),
    ])
    assert out["state"] == lc.CLOSED
    assert out["remainingQty"] == 0.0
    assert out["realizedShadowPnlUsd"] == pytest.approx(-20.0)


def test_a_not_identified_exit_moves_nothing():
    """§4: 'Do not interpolate an exit. Do not mark the position closed
    merely because the horizon expired.' The attempt clears the pending
    decision and leaves the quantity exactly where it was."""
    out = lc.fold([
        ev(lc.OPENED, at=T0, qty=3000.0, notional=920.0),
        ev(lc.ELIGIBLE, at=T0 + timedelta(seconds=60)),
        ev(lc.DECIDED, at=T0 + timedelta(seconds=60)),
        ev(lc.EXECUTED, at=T0 + timedelta(seconds=61), qty=None,
           notional=None, status="NOT_IDENTIFIED", book=None),
    ])
    assert out["state"] == lc.EXIT_ELIGIBLE      # not CLOSED, not PENDING
    assert out["remainingQty"] == 3000.0
    assert out["realizedShadowPnlUsd"] is None


def test_the_state_is_never_read_from_the_stored_status_column():
    """The defect was a stored conclusion on an immutable row. The fold
    reads events only."""
    import inspect
    src = inspect.getsource(lc.fold)
    assert '"status"' not in src
    assert "bettor_experimental_positions" not in src


def test_nothing_in_the_lifecycle_module_updates_or_deletes():
    src = code_only(lc)
    for stmt in ("update", "delete", "truncate", "insert"):
        assert stmt not in src, stmt


# ── §6: capital that knows a close frees dollars ─────────────────────


def test_capital_turns_stay_one_while_nothing_closes():
    folded = {"p1": lc.fold([ev(lc.OPENED, at=T0, qty=3000.0,
                                notional=920.0, pid="p1")]),
              "p2": lc.fold([ev(lc.OPENED, at=T0 + timedelta(seconds=65),
                                qty=3000.0, notional=920.0, pid="p2")])}
    out = lc.capital_with_releases(folded, experiment=X1,
                                   now=T0 + timedelta(hours=1))
    assert out["CURRENT_CAPITAL_DEPLOYED_USD"] == 1840.0
    assert out["PEAK_CAPITAL_DEPLOYED_USD"] == 1840.0
    assert out["CAPITAL_TURNS"] == 1.0
    assert out["CAPITAL_RELEASED_USD"] == 0.0


def test_a_close_frees_capital_and_creates_a_real_turn():
    """§6: 'Capital released by a genuine close becomes available for
    subsequent shadow positions.' The same function, no edit."""
    first = lc.fold([
        ev(lc.OPENED, at=T0, qty=3000.0, notional=920.0, pid="p1"),
        ev(lc.EXECUTED, at=T0 + timedelta(seconds=60), qty=-3000.0,
           notional=-900.0, status="FILLED", pid="p1"),
    ])
    second = lc.fold([ev(lc.OPENED, at=T0 + timedelta(seconds=120),
                         qty=3000.0, notional=920.0, pid="p2")])
    out = lc.capital_with_releases({"p1": first, "p2": second},
                                   experiment=X1,
                                   now=T0 + timedelta(seconds=300))
    assert out["CURRENT_CAPITAL_DEPLOYED_USD"] == 920.0   # only p2 open
    assert out["PEAK_CAPITAL_DEPLOYED_USD"] == 920.0      # never both
    assert out["CAPITAL_TURNS"] == 2.0                    # 1840 / 920
    assert out["CAPITAL_RELEASED_USD"] == 920.0


def test_turnover_is_entry_plus_exit_not_entry_twice():
    folded = {"p1": lc.fold([
        ev(lc.OPENED, at=T0, qty=3000.0, notional=920.0, pid="p1"),
        ev(lc.EXECUTED, at=T0 + timedelta(seconds=60), qty=-3000.0,
           notional=-900.0, status="FILLED", pid="p1")])}
    out = lc.lifecycle(folded, experiment=X1)
    assert out["GROSS_TRADING_TURNOVER_USD"] == 1820.0    # 920 + 900
    assert out["REALIZED_PNL_STATUS"] == "REALIZED"


def test_realized_is_not_applicable_rather_than_zero_when_nothing_closed():
    folded = {"p1": lc.fold([ev(lc.OPENED, at=T0, qty=3000.0,
                                notional=920.0, pid="p1")])}
    out = lc.lifecycle(folded, experiment=X1)
    assert out["REALIZED_PNL_USD"] is None
    assert out["REALIZED_PNL_STATUS"] == "NOT_APPLICABLE"
    assert out["SETTLED_PNL_STATUS"] == "NOT_APPLICABLE"
    assert "not zero" in out["why"]


# ── §10/§11: re-entry, enforced rather than accidental ───────────────


def test_the_guard_reads_the_real_frozen_declaration():
    """THE BUG THIS PINS. The declaration emits camelCase; a guard
    reading snake_case finds no clause, governs nothing and refuses
    nothing -- while every hand-built-dict test still passes."""
    assert rg.governed(X1_DECL) is True
    assert rg.governed(X1C_DECL) is True
    assert rg.horizon_seconds(X1_DECL) == 60
    assert "exitRule" in X1_DECL          # not exit_rule
    assert "experimentId" in X1_DECL      # not experiment_id


@pytest.mark.parametrize("gap,expected", [
    (41.6, rg.REFUSED),      # X1C's actual historical violation
    (59.999, rg.REFUSED),
    (60.0, rg.PERMITTED),    # the boundary is NOT inside
    (60.001, rg.PERMITTED),
    (64.7, rg.PERMITTED),    # X1's actual minimum gap
])
def test_the_boundary_is_explicit(gap, expected):
    """§11's three cases, pinned so the semantics cannot drift into an
    off-by-one that silently permits or forbids a re-entry."""
    v = rg.check(declaration=X1_DECL, market_id="m1",
                 at=T0 + timedelta(seconds=gap), last_opened_at=T0)
    assert v["decision"] == expected, gap


def test_the_control_is_governed_by_the_same_literal_rule():
    """§10: 'Apply the same literal frozen rule to any lane whose
    policy declares it.' X1C would now be refused at 41.6s."""
    v = rg.check(declaration=X1C_DECL, market_id="m1",
                 at=T0 + timedelta(seconds=41.6), last_opened_at=T0)
    assert v["decision"] == rg.REFUSED
    assert v["horizonSeconds"] == 60


def test_a_first_position_on_a_market_is_always_permitted():
    v = rg.check(declaration=X1_DECL, market_id="m1", at=T0,
                 last_opened_at=None)
    assert v["decision"] == rg.PERMITTED


def test_a_lane_without_the_clause_is_untouched():
    v = rg.check(declaration={"experimentId": "OTHER", "exitRule": "hold"},
                 market_id="m1", at=T0 + timedelta(seconds=1),
                 last_opened_at=T0)
    assert v["decision"] == rg.PERMITTED
    assert v["status"] == rg.NO_RULE


def test_an_unreadable_horizon_fails_closed():
    """The clause exists and cannot be evaluated, so the entry is
    refused rather than waved through."""
    decl = dict(X1_DECL, horizon="SOMETIME")
    v = rg.check(declaration=decl, market_id="m1",
                 at=T0 + timedelta(seconds=1), last_opened_at=T0)
    assert v["decision"] == rg.REFUSED


def test_the_horizon_is_never_changed_by_this_work():
    """§10: 'Do not change the 60S horizon based on observed
    performance.'"""
    assert X1_DECL["horizon"] == "60S"
    assert rg.horizon_seconds(X1_DECL) == 60


def test_the_guard_runs_before_creation_not_after():
    import inspect
    from sportsassets import shadow_experimental_store as xstore
    src = inspect.getsource(xstore.open_position)
    assert src.index("guard.REFUSED") < src.index("INSERT INTO"), (
        "the re-entry decision must precede the position row, or the "
        "row exists and the rule is advisory")
    # AND THE VERDICT ITSELF WRITES NOTHING. It is asked before the
    # decision row too, so that a refusal and its decision cannot
    # disagree; a write in here would make the question a side effect.
    assert "INSERT" not in inspect.getsource(xstore.reentry_verdict)


def test_a_refused_entry_carries_no_position_and_no_economics():
    """Migration 086's invariant, stated in the writer as well.

    `record_decision` writes position_id straight from the
    reconstruction. If a refusal kept it, every query that counts
    `position_id IS NOT NULL` would count the refusal as a trade and
    the enforcement would ADD a position instead of preventing one.
    """
    from sportsassets import shadow_experimental_engine as eng
    from sportsassets import shadow_experimental_store as xstore
    execution = {"experimentalDecisionId": "xdec_1",
                 "positionId": "xpos_1", "executionStatus": eng.PARTIAL,
                 "executedNotionalUsd": 920.0,
                 "unfilledNotionalUsd": 80.0, "filledQty": 3000.0,
                 "vwap": 0.306667, "slippage": 0.036667,
                 "spreadCost": 0.036667, "l2BookSha": "96b9207fe6737ec6"}
    out = xstore.refused_execution(
        execution, {"decision": rg.REFUSED, "why": "inside the horizon"})
    assert out["executionStatus"] == eng.REFUSED_REENTRY
    assert out["positionId"] is None
    for key in ("executedNotionalUsd", "unfilledNotionalUsd", "filledQty",
                "vwap", "slippage", "spreadCost"):
        assert out[key] is None, key
    # THE BOOK STAYS. That book really was observed at that instant,
    # and it is the evidence of what the refusal cost.
    assert out["l2BookSha"] == "96b9207fe6737ec6"
    assert out["why"] == "inside the horizon"
    # AND THE ORIGINAL IS UNTOUCHED -- the caller's execution dict is
    # still the reconstruction it was.
    assert execution["positionId"] == "xpos_1"
    assert execution["filledQty"] == 3000.0


def test_the_refusal_status_is_one_the_database_admits():
    """A status the CHECK constraint refuses is a write that fails at
    the exact moment the rule fires."""
    import pathlib
    from sportsassets import shadow_experimental_engine as eng
    sql = pathlib.Path(__file__).resolve().parents[1].joinpath(
        "migrations", "086_reentry_refusal_status.sql").read_text()
    assert eng.REFUSED_REENTRY in sql
    assert "bettor_exp_execution_status" in sql
    # and the refusal may never carry a position
    assert "bettor_exp_refusal_has_no_position" in sql


# ── §7/§8/§9: clocks ─────────────────────────────────────────────────


def _row(**kw):
    base = {"venue_source_timestamp": "2026-09-20T01:56:21.302733768Z",
            "bettor_received_timestamp": T0 + timedelta(seconds=5.714),
            "features_sealed_timestamp": T0 + timedelta(seconds=2.692),
            "model_start_timestamp": T0 + timedelta(seconds=6.241),
            "model_end_timestamp": T0 + timedelta(seconds=6.241),
            "decision_timestamp": T0,
            "modeled_arrival_timestamp": T0 + timedelta(seconds=6.491),
            "modeled_execution_latency_ms": 6491.6}
    base.update(kw)
    return base


def test_the_modeled_component_is_labelled_an_assumption():
    """§8: 'MODELED_EXECUTION_LATENCY_ASSUMPTION = 250ms, not
    OBSERVED_EXECUTION_LATENCY. Keep those labels distinct.'"""
    out = lat.components(_row())
    assert out[lat.MODELED_ASSUMPTION_LABEL]["ms"] == 250
    assert out[lat.MODELED_ASSUMPTION_LABEL]["status"] == "CONFIGURED_CONSTANT"
    assert out[lat.OBSERVED_LABEL]["ms"] is None
    assert out[lat.OBSERVED_LABEL]["status"] == "NOT_IDENTIFIED"


def test_a_negative_component_is_withheld_not_shown_as_fast():
    out = lat.components(_row())
    feature = out["FEATURE_PROCESSING_LATENCY"]
    assert feature["ms"] is None
    assert feature["status"] == lat.CLOCK_ORDER_INVALID
    assert "do not share a clock" in feature["why"]


def test_the_venue_string_component_is_not_identified():
    out = lat.components(_row())
    assert out["SOURCE_TO_RECEIPT_LATENCY"]["status"] == \
        "NOT_IDENTIFIED_CLOCK_DOMAIN"


def test_a_sound_component_survives_a_broken_sibling():
    """Componentised means one bad pair does not silence a good one."""
    out = lat.components(_row())
    assert out["MODEL_COMPUTE_LATENCY"]["status"] == "OBSERVED"
    assert out["MODEL_COMPUTE_LATENCY"]["ms"] == 0.0


def test_existing_rows_keep_their_conflict_status():
    """§7: 'Do not rewrite them. Their status remains
    NOT_IDENTIFIED_CLOCK_DOMAIN_CONFLICT.'"""
    assert lat.integrity(_row())["LATENCY_STATUS"] == lat.CONFLICT
    assert lat.integrity(_row())["MODELED_EXECUTION_LATENCY_MS"] is None
