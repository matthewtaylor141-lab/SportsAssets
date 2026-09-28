"""THE FUNDED LANE'S OWN RESULTS, AND THE ONE THING THEY CAN FALSIFY.

WHY THIS FILE EXISTS. The RN1X lane records a prospective valuation and joins the
venue's settlement to it, so its record is scorable. The funded lane had neither
half: it decided, acted, and the decision was gone, while the realised cash sat in
`bettor_funded_economics` with nothing to compare it against.

THE BOUNDARY THESE TESTS ENFORCE, which matters more than any number produced:

  * THE ALTERNATIVES ARE NOT SCORABLE. If HOLD was chosen, what EXIT would have
    returned is never observed -- not at the quoted price, because a sale moves
    against itself through depth, and not at that moment, because it passed. A
    "which action was right" statistic would score a counterfactual as a result
    and would flatter whichever action the system already prefers. Asserted as
    ABSENT from the output, by name.

  * THE WORST CASE IS FALSIFIABLE. It is a claimed LOWER BOUND from the venue's
    settlement terms, so a realised net below it means the model of the world was
    wrong -- the outcome space, the fee, the described structure, or an unhedged
    remainder. The violation path is driven with real ledger rows, not asserted
    from the docstring.

  * A DECISION IS NOT REWRITTEN AFTER THE FACT. The whole value of the record is
    that the first half was written BEFORE the outcome. A decision edited
    afterwards is a rationalisation whose bound can no longer be falsified.
"""

from __future__ import annotations

import os
import time

import pytest

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

from sportsassets import bettor_funded_learning as FL          # noqa: E402

ACCT = "acct-131l"
FIXTURE = "mlb-nyy-bos-2026-08-14"
PFX = "t131l-"
SLUG_A = "aec-nyy-ml"
SLUG_B = "aec-bos-ml"


async def _conn():
    import asyncpg
    return await asyncpg.connect(DSN)


async def _has(c, table) -> bool:
    return bool(await c.fetchval(
        "SELECT count(*) FROM information_schema.tables "
        " WHERE table_schema='public' AND table_name=$1", table))


async def _clean(c):
    if await _has(c, "bettor_funded_decisions"):
        await c.execute("DELETE FROM bettor_funded_decisions "
                        "WHERE decision_id LIKE $1", PFX + "%")
    if await _has(c, "bettor_funded_leg_reservations"):
        await c.execute("DELETE FROM bettor_funded_leg_reservations "
                        "WHERE group_id LIKE $1", PFX + "%")
    await c.execute("DELETE FROM bettor_funded_economics "
                    "WHERE intent_id LIKE $1", PFX + "%")
    await c.execute("DELETE FROM bettor_funded_fills WHERE intent_id LIKE $1",
                    PFX + "%")
    await c.execute("DELETE FROM bettor_funded_intents WHERE intent_id LIKE $1",
                    PFX + "%")
    if await _has(c, "bettor_funded_portfolio_groups"):
        await c.execute("DELETE FROM bettor_funded_portfolio_groups "
                        "WHERE group_id LIKE $1", PFX + "%")


async def _ready(c):
    if not await _has(c, "bettor_funded_decisions"):
        pytest.skip("migration 132 not applied to this database")
    if not await _has(c, "bettor_funded_portfolio_groups"):
        pytest.skip("migration 131 not applied to this database")
    await _clean(c)


async def _group(c, gid):
    await c.execute(
        "INSERT INTO bettor_funded_portfolio_groups "
        "(group_id, account_id, venue, event_key, structure) "
        "VALUES ($1,$2,'PMUS',$3,'INDIRECT_MIDDLE')", gid, ACCT, FIXTURE)


async def _leg(c, iid, gid, role, slug, *, qty=10, closed_reason=None,
               parent=None, kind="ENTRY"):
    await c.execute(
        "INSERT INTO bettor_funded_intents (intent_id, account_id, venue,"
        " venue_class, us_market_slug, event_key, order_intent, limit_price,"
        " quantity, collateral_usd, effective_digest, state, kind,"
        " residual_qty, portfolio_group_id, leg_role, parent_intent_id) VALUES "
        "($1,$2,'PMUS','US',$3,$4,'ORDER_INTENT_BUY_LONG',0.5,$5,$6,$7,"
        " 'FILLED',$8,0,$9,$10,$11)",
        iid, ACCT, slug, FIXTURE, int(qty), float(qty) * 0.5, iid + "-d", kind,
        gid, role, parent)
    if closed_reason:
        await c.execute(
            "UPDATE bettor_funded_intents SET closed_at=now(), "
            "  closed_reason=$2 WHERE intent_id=$1", iid, closed_reason)


async def _econ(c, iid, kind, amount, *, eid=None, provisional=False):
    await c.execute(
        "INSERT INTO bettor_funded_economics "
        "(event_id, intent_id, at, kind, amount_usd, basis, provisional) "
        "VALUES ($1,$2,now(),$3,$4,'TEST',$5)",
        eid or ("%s:%s:%s" % (iid, kind, amount)), iid, kind, float(amount),
        provisional)


# ═════════════════════════════════════════════════════════════════════
# 0 · THE BOUNDARY, DECLARED AND PURE
# ═════════════════════════════════════════════════════════════════════

def test_the_module_refuses_to_report_which_action_was_right():
    """THE STATISTIC THAT CANNOT MEAN WHAT IT LOOKS LIKE. Its absence is a
    design decision and is stated, so a reader does not assume it is merely
    unimplemented."""
    d = FL.describe()
    assert "win_rate" in d["does_not_report"]
    assert "expected_value" in d["does_not_report"]
    assert "projected_return" in d["does_not_report"]
    assert "never observed" in d["not_scorable"]


def test_a_withheld_bound_is_not_a_bound_of_zero():
    """THE VALUATION WITHHOLDS ITS VERDICT when fees are not priced. Scoring that
    as a claimed bound of zero would report a violation for every position that
    lost a cent, and would report a held bound for every one that made one --
    both meaningless."""
    got = FL.check_the_worst_case({"worst_case_usd": None,
                                   "realised_net_usd": -3.0})
    assert got["worst_case_check"] == "NO_BOUND_WAS_CLAIMED"
    assert "NOT a bound of zero" in got["why"]


def test_a_realised_net_above_the_bound_holds_it():
    got = FL.check_the_worst_case({"worst_case_usd": -4.90,
                                   "realised_net_usd": 0.10})
    assert got["worst_case_check"] == "HELD"
    assert got["shortfall_usd"] == 0.0


def test_a_realised_net_below_the_bound_is_a_violation_with_causes():
    """THE FALSIFICATION. A lower bound from the venue's own settlement terms
    cannot be beaten downward by luck; something in the model was wrong."""
    got = FL.check_the_worst_case({"worst_case_usd": 0.10,
                                   "realised_net_usd": -9.90})
    assert got["worst_case_check"] == "VIOLATED"
    assert got["shortfall_usd"] == pytest.approx(10.0)
    assert got["the_model_of_the_world_was_wrong"] is True
    assert "THE_OUTCOME_SPACE_WAS_WRONG" in got["possible_causes"]
    assert "THE_HEDGE_ONLY_PARTLY_FILLED" in got["possible_causes"]


def test_the_tolerance_is_a_cent_and_not_a_margin():
    """THE TOLERANCE EXISTS FOR ROUNDING, NOT FOR SLACK. This structure trades on
    tens of cents, so a generous tolerance would absorb the very error the check
    is looking for."""
    assert FL.check_the_worst_case({"worst_case_usd": 0.0,
                                    "realised_net_usd": -0.005}
                                   )["worst_case_check"] == "HELD"
    assert FL.check_the_worst_case({"worst_case_usd": 0.0,
                                    "realised_net_usd": -0.30}
                                   )["worst_case_check"] == "VIOLATED"


def test_the_module_cannot_reach_a_venue():
    import pathlib
    src = pathlib.Path(FL.__file__).read_text()
    for forbidden in ("import httpx", "from . import pmus", "submit_fok",
                      "close_position", "polymarket"):
        assert forbidden not in src, (
            "%r appears in the learning module, which reads the book and "
            "sends nothing" % forbidden)


# ═════════════════════════════════════════════════════════════════════
# 1 · THE RECORD IS WRITTEN BEFORE THE OUTCOME
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_a_decision_is_recorded_before_any_outcome_exists():
    c = await _conn()
    try:
        await _ready(c)
        await _group(c, PFX + "g1")
        got = await FL.record_decision(
            c, decision_id=PFX + "d1", account_id=ACCT, venue="PMUS",
            fixture=FIXTURE, group_id=PFX + "g1", action="ACQUIRE_HEDGE",
            worst_case_usd=0.10, decided_at=time.time(),
            ranking={"ranked": [{"action": "ACQUIRE_HEDGE",
                                 "worst_case_usd": 0.10},
                                {"action": "HOLD", "worst_case_usd": -4.90}],
                     "unrankable": [{"action": "EXIT",
                                     "refusal": "PRICE_NOT_ESTABLISHED"}]},
            inputs_present=["depth", "fee"], inputs_missing=["exit_price"])
        assert got["ok"] is True and got["written"] is True, got
        assert got["recorded_before_the_outcome"] is True
        d = got["decision"]
        assert d["realised_known"] is False
        assert d["realised_net_usd"] is None
        assert d["worst_case_usd"] == pytest.approx(0.10)
        assert d["inputs_missing"] == ["exit_price"]
        # THE ALTERNATIVES ARE STORED, for the record and for diagnosis.
        assert len(d["ranked"]) == 2
        assert d["unrankable"][0]["refusal"] == "PRICE_NOT_ESTABLISHED"
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_replaying_the_same_decision_does_not_record_it_twice():
    """A CYCLE THAT RE-EVALUATES THE SAME GROUP must reach the same row. The
    RN1X ledger had to learn this distinction when a cycle reported 293 writes
    against 159 rows because it counted validated attempts."""
    c = await _conn()
    try:
        await _ready(c)
        await _group(c, PFX + "g2")
        kw = dict(account_id=ACCT, venue="PMUS", fixture=FIXTURE,
                  group_id=PFX + "g2", action="HOLD", worst_case_usd=-4.90,
                  decided_at=time.time())
        first = await FL.record_decision(c, decision_id=PFX + "d2", **kw)
        again = await FL.record_decision(c, decision_id=PFX + "d2", **kw)
        assert first["written"] is True
        assert again["written"] is False and again["already"] is True, again
        assert await c.fetchval(
            "SELECT count(*) FROM bettor_funded_decisions "
            " WHERE decision_id=$1", PFX + "d2") == 1
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_a_recorded_decision_cannot_be_rewritten_afterwards():
    """THE DATABASE REFUSES THE RATIONALISATION. If the action or the claimed
    bound could be edited after the result, the bound would no longer be
    falsifiable -- which is the only thing this ledger can actually test."""
    c = await _conn()
    try:
        await _ready(c)
        await _group(c, PFX + "g3")
        await FL.record_decision(
            c, decision_id=PFX + "d3", account_id=ACCT, venue="PMUS",
            fixture=FIXTURE, group_id=PFX + "g3", action="HOLD",
            worst_case_usd=-4.90, decided_at=time.time())
        # A `datetime`, NOT AN ISO STRING. asyncpg refuses a string for
        # `timestamptz` with a DataError, and a test that accepted ANY exception
        # would have passed on that refusal without ever reaching the trigger --
        # proving nothing about whether a decision can be back-dated. Asserting
        # the trigger's own message is what caught it.
        import datetime as _dt
        for col, val in (("action", "EXIT"), ("worst_case_usd", 99),
                         ("fixture", "some-other-fixture"),
                         ("decided_at", _dt.datetime(2020, 1, 1,
                                                     tzinfo=_dt.timezone.utc))):
            with pytest.raises(Exception) as caught:
                await c.execute(
                    "UPDATE bettor_funded_decisions SET %s=$2 "
                    " WHERE decision_id=$1" % col, PFX + "d3", val)
            assert "is not rewritten" in str(caught.value), (col, caught.value)
        row = await c.fetchrow(
            "SELECT action, worst_case_usd::float8 w FROM "
            " bettor_funded_decisions WHERE decision_id=$1", PFX + "d3")
        assert row["action"] == "HOLD"
        assert row["w"] == pytest.approx(-4.90)
    finally:
        await _clean(c)
        await c.close()


# ═════════════════════════════════════════════════════════════════════
# 2 · THE REALISED NET COMES FROM THE BOOK AND NOWHERE ELSE
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_the_realised_net_sums_the_signed_ledger_including_exit_children():
    """AN EXIT IS ITS OWN INTENT AND CARRIES NO GROUP. Summing only the group's
    own legs would omit every sale's proceeds and every sale's fee -- which is
    most of the result -- and the omission would show up as a worst-case
    violation that is really a missing join."""
    c = await _conn()
    try:
        await _ready(c)
        await _group(c, PFX + "g4")
        await _leg(c, PFX + "p4", PFX + "g4", "PRIMARY", SLUG_A,
                   closed_reason="EXITED_IN_THE_MARKET")
        await _leg(c, PFX + "x4", None, None, SLUG_A, kind="EXIT",
                   parent=PFX + "p4")
        await _econ(c, PFX + "p4", "ENTRY_COST", -4.80)
        await _econ(c, PFX + "p4", "FEE", -0.10)
        # THE EXIT CHILD'S ROWS, which the group does not own.
        await _econ(c, PFX + "x4", "EXIT_PROCEEDS", 6.00)
        await _econ(c, PFX + "x4", "FEE", -0.12)

        got = await FL.realised_from_the_book(c, group_id=PFX + "g4")
        assert got["ok"] is True, got
        assert got["realised_net_usd"] == pytest.approx(0.98)
        assert got["by_kind"]["EXIT_PROCEEDS"]["usd"] == pytest.approx(6.00)
        assert got["by_kind"]["FEE"]["usd"] == pytest.approx(-0.22)
        assert got["by_kind"]["FEE"]["events"] == 2
        assert got["basis"] == FL.BASIS_FUNDED_ECONOMICS
        assert got["some_of_this_is_not_final"] is False
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_it_refuses_to_read_a_result_while_the_position_is_still_open():
    """A PARTIAL RESULT IS NOT A RESULT. Compared against a worst case that
    describes the whole structure it would report a violation that is only
    incompleteness -- which would then be investigated as a modelling error."""
    c = await _conn()
    try:
        await _ready(c)
        await _group(c, PFX + "g5")
        await _leg(c, PFX + "p5", PFX + "g5", "PRIMARY", SLUG_A)
        await c.execute(
            "UPDATE bettor_funded_intents SET residual_qty=10 "
            " WHERE intent_id=$1", PFX + "p5")
        await _econ(c, PFX + "p5", "ENTRY_COST", -4.80)
        got = await FL.realised_from_the_book(c, group_id=PFX + "g5")
        assert got["ok"] is False
        assert got["refusal"] == FL.R_POSITION_IS_STILL_OPEN, got
        assert got["open_legs"] == 1
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_a_provisional_fee_is_counted_and_flagged_as_not_final():
    """A VIOLATION CAUSED BY A PROVISIONAL FEE IS A DIFFERENT FINDING from one
    caused by the outcome space, so the count of non-final events is reported
    alongside the net rather than folded into it."""
    c = await _conn()
    try:
        await _ready(c)
        await _group(c, PFX + "g6")
        await _leg(c, PFX + "p6", PFX + "g6", "PRIMARY", SLUG_A,
                   closed_reason="SETTLED_BY_THE_VENUE")
        await _econ(c, PFX + "p6", "ENTRY_COST", -4.80)
        await _econ(c, PFX + "p6", "FEE", -0.10, provisional=True)
        await _econ(c, PFX + "p6", "SETTLEMENT", 10.00)
        got = await FL.realised_from_the_book(c, group_id=PFX + "g6")
        assert got["ok"] is True
        assert got["realised_net_usd"] == pytest.approx(5.10)
        assert got["provisional_events"] == 1
        assert got["some_of_this_is_not_final"] is True
    finally:
        await _clean(c)
        await c.close()


# ═════════════════════════════════════════════════════════════════════
# 3 · THE JOIN, AND THE VIOLATION DRIVEN END TO END
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_a_held_bound_is_joined_and_reported_as_held():
    c = await _conn()
    try:
        await _ready(c)
        await _group(c, PFX + "g7")
        await _leg(c, PFX + "p7", PFX + "g7", "PRIMARY", SLUG_A,
                   closed_reason="SETTLED_BY_THE_VENUE")
        await _leg(c, PFX + "h7", PFX + "g7", "HEDGE", SLUG_B,
                   closed_reason="SETTLED_BY_THE_VENUE")
        await _econ(c, PFX + "p7", "ENTRY_COST", -4.80)
        await _econ(c, PFX + "h7", "ENTRY_COST", -4.90)
        await _econ(c, PFX + "p7", "FEE", -0.20)
        await _econ(c, PFX + "p7", "SETTLEMENT", 10.00)
        await FL.record_decision(
            c, decision_id=PFX + "d7", account_id=ACCT, venue="PMUS",
            fixture=FIXTURE, group_id=PFX + "g7", action="ACQUIRE_HEDGE",
            worst_case_usd=0.10, decided_at=time.time())
        got = await FL.join_realised(c, decision_id=PFX + "d7")
        assert got["ok"] is True, got
        assert got["decision"]["realised_known"] is True
        assert got["decision"]["realised_net_usd"] == pytest.approx(0.10)
        assert got["decision"]["realised_basis"] == FL.BASIS_FUNDED_ECONOMICS
        assert got["worst_case_check"] == "HELD", got
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_the_draw_that_nobody_covered_shows_up_as_a_worst_case_violation():
    """THE END-TO-END FALSIFICATION, and it is the same defect the valuation
    module refuses to make.

    The decision claimed a worst case of +$0.10 -- the arithmetic of a
    two-outcome fixture. The fixture was drawn, neither moneyline paid, and the
    book's own ledger records minus the whole cost. The bound is violated by the
    full amount, and that is the signal: not bad luck, but an outcome space that
    was wrong.
    """
    c = await _conn()
    try:
        await _ready(c)
        await _group(c, PFX + "g8")
        await _leg(c, PFX + "p8", PFX + "g8", "PRIMARY", SLUG_A,
                   closed_reason="SETTLED_BY_THE_VENUE")
        await _leg(c, PFX + "h8", PFX + "g8", "HEDGE", SLUG_B,
                   closed_reason="SETTLED_BY_THE_VENUE")
        await _econ(c, PFX + "p8", "ENTRY_COST", -4.80)
        await _econ(c, PFX + "h8", "ENTRY_COST", -4.90)
        await _econ(c, PFX + "p8", "FEE", -0.20)
        # NEITHER LEG PAID. A settlement of zero is still a settlement event.
        await _econ(c, PFX + "p8", "SETTLEMENT", 0.00)
        await _econ(c, PFX + "h8", "SETTLEMENT", 0.00)
        await FL.record_decision(
            c, decision_id=PFX + "d8", account_id=ACCT, venue="PMUS",
            fixture=FIXTURE, group_id=PFX + "g8", action="ACQUIRE_HEDGE",
            worst_case_usd=0.10, decided_at=time.time())
        got = await FL.join_realised(c, decision_id=PFX + "d8")
        assert got["ok"] is True, got
        assert got["decision"]["realised_net_usd"] == pytest.approx(-9.90)
        assert got["worst_case_check"] == "VIOLATED", got
        assert got["shortfall_usd"] == pytest.approx(10.00)
        assert "THE_OUTCOME_SPACE_WAS_WRONG" in got["possible_causes"]
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_a_realised_outcome_is_not_restated():
    """ONCE, AND ONLY ONCE. A restated result makes the bound unfalsifiable after
    the fact, so both the module and the database refuse it."""
    c = await _conn()
    try:
        await _ready(c)
        await _group(c, PFX + "g9")
        await _leg(c, PFX + "p9", PFX + "g9", "PRIMARY", SLUG_A,
                   closed_reason="EXITED_IN_THE_MARKET")
        await _econ(c, PFX + "p9", "ENTRY_COST", -4.80)
        await _econ(c, PFX + "p9", "EXIT_PROCEEDS", 4.70)
        await FL.record_decision(
            c, decision_id=PFX + "d9", account_id=ACCT, venue="PMUS",
            fixture=FIXTURE, group_id=PFX + "g9", action="EXIT",
            worst_case_usd=-0.20, decided_at=time.time())
        first = await FL.join_realised(c, decision_id=PFX + "d9")
        assert first["ok"] is True and first["already"] is False
        again = await FL.join_realised(c, decision_id=PFX + "d9")
        assert again["ok"] is True and again["already"] is True, again
        with pytest.raises(Exception) as caught:
            await c.execute(
                "UPDATE bettor_funded_decisions SET realised_net_usd=99 "
                " WHERE decision_id=$1", PFX + "d9")
        assert "not restated" in str(caught.value), caught.value
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_unjoined_is_what_a_restart_picks_up():
    c = await _conn()
    try:
        await _ready(c)
        await _group(c, PFX + "ga")
        await FL.record_decision(
            c, decision_id=PFX + "da", account_id=ACCT, venue="PMUS",
            fixture=FIXTURE, group_id=PFX + "ga", action="HOLD",
            worst_case_usd=-4.90, decided_at=time.time())
        pending = [d["decision_id"] for d in await FL.unjoined(c)]
        assert PFX + "da" in pending
    finally:
        await _clean(c)
        await c.close()


# ═════════════════════════════════════════════════════════════════════
# 4 · THE SCORE NAMES ITS OWN LIMITS
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_the_score_reports_violations_and_refuses_a_rate():
    """WHAT THE RECORD SUPPORTS. Counts, a realised total, and every violation.
    No win rate, no expected value, no projected return -- and the omissions are
    named in the output so a reader does not read them as merely uncomputed."""
    c = await _conn()
    try:
        await _ready(c)
        # ONE HELD BOUND ...
        await _group(c, PFX + "gb")
        await _leg(c, PFX + "pb", PFX + "gb", "PRIMARY", SLUG_A,
                   closed_reason="SETTLED_BY_THE_VENUE")
        await _econ(c, PFX + "pb", "ENTRY_COST", -4.80)
        await _econ(c, PFX + "pb", "SETTLEMENT", 10.00)
        await FL.record_decision(
            c, decision_id=PFX + "db", account_id=ACCT, venue="PMUS",
            fixture=FIXTURE, group_id=PFX + "gb", action="HOLD",
            worst_case_usd=-4.80, decided_at=time.time())
        await FL.join_realised(c, decision_id=PFX + "db")

        got = await FL.score(c, account_id=ACCT)
        assert got["ok"] is True, got
        assert got["decisions_recorded"] >= 1
        assert got["outcomes_known"] >= 1
        assert got["bounds_held"] >= 1
        assert got["bounds_violated"] == 0
        assert got["decisions_by_action"]["HOLD"] >= 1
        assert got["realised_net_total_usd"] == pytest.approx(5.20)
        # THE OMISSIONS ARE NAMED.
        assert "win_rate" in got["not_reported"]
        assert "which_action_was_right" in got["not_reported"]
        assert "never realised" in got["why_which_action_was_right_is_absent"]
        assert "not a rate" in got["why_the_total_is_not_a_return"]
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_the_score_counts_a_withheld_bound_separately_from_a_held_one():
    """A DECISION THAT CLAIMED NOTHING IS NOT EVIDENCE THAT NOTHING WENT WRONG.
    Folding withheld bounds into `bounds_held` would make a lane that never
    prices its fees look perfectly calibrated."""
    c = await _conn()
    try:
        await _ready(c)
        await _group(c, PFX + "gc")
        await _leg(c, PFX + "pc", PFX + "gc", "PRIMARY", SLUG_A,
                   closed_reason="EXITED_IN_THE_MARKET")
        await _econ(c, PFX + "pc", "ENTRY_COST", -4.80)
        await _econ(c, PFX + "pc", "EXIT_PROCEEDS", 4.00)
        await FL.record_decision(
            c, decision_id=PFX + "dc", account_id=ACCT, venue="PMUS",
            fixture=FIXTURE, group_id=PFX + "gc", action="EXIT",
            worst_case_usd=None, decided_at=time.time())
        await FL.join_realised(c, decision_id=PFX + "dc")
        got = await FL.score(c, account_id=ACCT)
        assert got["no_bound_was_claimed"] >= 1
        assert got["bounds_claimed"] == got["bounds_held"] + \
            got["bounds_violated"]
        # THE LOSS IS IN THE TOTAL, but it is not a violated bound, because no
        # bound was claimed.
        assert got["realised_net_total_usd"] == pytest.approx(-0.80)
        assert got["bounds_violated"] == 0
    finally:
        await _clean(c)
        await c.close()
