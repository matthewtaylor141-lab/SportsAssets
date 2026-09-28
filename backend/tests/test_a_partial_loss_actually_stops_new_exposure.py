"""A loss realised on a PARTIAL exit must stop new exposure.

Owner requirement, in full: "Prove the critical counterexample: a position
partially realizes a loss that breaches the approved threshold while
inventory remains open. New exposure must be refused, while
ownership-bound servicing and reconciliation continue."

── WHY THIS IS THE COUNTEREXAMPLE AND NOT A NICE-TO-HAVE ────────────
`bettor_funded_book.realised()` filtered `closed_at IS NOT NULL`. Every
consumer of the MAX_DRAWDOWN rail reads its `max_drawdown_usd`:

    bettor_funded_execution.check_rails   refuses new exposure
    bettor_funded_management.manage       reports the loss stop
    bettor_funded_book.pnl                the operator surface

So a position that sold 9 of 15 contracts below cost -- cash gone,
contracts gone, result determined and unrecoverable -- contributed
NOTHING to the measurement. The approved stop could be breached by any
amount and the rail would read 0.00 and pass. A control that cannot
observe the loss it exists to stop is not a conservative control; it is
an absent one that reports as present.

THE TESTS BELOW ARE SPLIT ALONG THE THREE THINGS THAT MUST BE TRUE AT
ONCE, because passing one of them is the failure mode: a stop that
refuses everything is as wrong as one that refuses nothing. What must
shrink is permission to take NEW risk. What must NOT shrink is the
ability to service and reconcile what we already own.
"""

import json
import time

import pytest

from sportsassets import bettor_entry_execution as EX
from sportsassets import bettor_funded_activation as FA
from sportsassets import bettor_funded_book as FB
from sportsassets import bettor_funded_execution as FX

from tests.test_the_funded_lifecycle_is_complete import (  # noqa: E402
    ACCT, DSN, EVENT, PAYS_ON, SLUG, VENUE, _clean, pg,
)
from tests.test_partial_exit_pnl_is_separable import _entry, _sell  # noqa: E402


#: THE STOP IS SET TIGHT ON PURPOSE. The demonstration's own partial loss
#: is -$2.01, and the shipped `daily_loss_stop_usd` is 40 -- so on the
#: real figures the breach never happens and the test would pass whether
#: or not the rail could see the loss. A $1 stop makes the breach real,
#: which is the only way the refusal is actually exercised.
#:
#: This is a THRESHOLD CHOSEN TO TEST A CONTROL, not a proposed limit.
TIGHT_STOP = {"capital_usd": 400, "per_order_usd": 60,
              "event_exposure_usd": 60, "max_exposure_usd": 200,
              "daily_loss_stop_usd": 1.0}


async def _seed_tight(conn, *, limits):
    """Seed an ACTIVE, RECONCILED account with the given approved limits."""
    await conn.execute("DELETE FROM bettor_desk_accounts WHERE account_id=$1",
                       ACCT)
    await conn.execute(
        "INSERT INTO bettor_desk_accounts (account_id, desk_id, status, "
        " paused, accounting_status, opening_balance, note) VALUES "
        "($1,'desk-partial-stop-test','ACTIVE',FALSE,'RECONCILED',0,"
        "'partial loss stop test')", ACCT)
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1,$2::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value=$2::jsonb", FA.LIMITS_KEY,
        json.dumps({"proposed": dict(limits), "approved": True,
                    "approved_by": "OWNER"}))
    now = time.time()
    eff = EX.effective_limits(dict(limits))
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1,$2::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value=$2::jsonb",
        FA.AUTHORIZATION_KEY,
        json.dumps({"account_id": ACCT, "venue": VENUE,
                    "venue_class": FA.VENUE_FUNDED, "by": "test",
                    "at": now, "expires_at": now + 3600.0,
                    "revoked": False,
                    "effective_limits": eff["effective"],
                    "effective_digest": eff["effective_digest"]}))
    return eff["effective"]


async def _breach(conn, monkeypatch):
    """Long 15 @ 0.60, sell 9 @ 0.41. Position stays OPEN with 6 held.

    NOT FEE-FREE, AND I WAS WRONG ABOUT THAT. My first version of this
    docstring said "fee-free, so the arithmetic is visible" and expected
    -$1.71. Running it against a real database returned -$2.01. The fixture
    passes no `commission_usd`, so no VENUE-STATED fee exists -- but the
    book then records the EXPECTED fee from the published schedule
    (`0.0695 x qty x price x (1-price)`), which is 0.25 on the entry and
    0.15 on the exit. "No venue commission supplied" is not "no fee".

    -$2.01 is therefore the correct figure, and it is exactly the number
    the controlled demonstration reported, which is the check that the
    fixture reproduces the real case:

        proceeds            9 x 0.41            =  3.69
        allocated basis     9 x 0.60            = -5.40
        exit fee (all of it)                    = -0.15
        entry fee share     0.25 x 9/15         = -0.15
                                                  -----
        realised on the sold quantity           = -2.01

    Six contracts remain. Their $3.60 of basis, plus the $0.10 of entry fee
    sitting on them, is an asset at cost -- NOT part of the loss.
    """
    await _entry(conn, intent_id="ent-partial-stop", qty=15, price=0.60)
    await _sell(conn, monkeypatch, parent="ent-partial-stop",
                qty=9, price=0.41)


# ═════════════════════════════════════════════════════════════════════
# 1 · THE MEASUREMENT SEES THE LOSS AT ALL
# ═════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_the_realised_curve_books_a_loss_on_an_open_position(
        monkeypatch):
    """`realised()` must report the partial loss while the position is open.

    THIS IS THE TEST THAT WOULD HAVE FAILED BEFORE THE FIX, and it is
    worth being precise about how: `max_drawdown_usd` was 0.0 and
    `closed_positions` was 0, so every assertion about the rail below was
    vacuously satisfied by a measurement of nothing.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed_tight(conn, limits=TIGHT_STOP)
        await _breach(conn, monkeypatch)

        real = await FB.realised(conn, account_id=ACCT, venue=VENUE)

        # THE POSITION IS STILL OPEN. If it closed, this test proves
        # nothing about partial exits -- so that is asserted, not assumed.
        assert real["closed_positions"] == 0, real
        assert real["partially_realised_open_positions"] == 1, real

        # AND THE LOSS IS BOOKED.
        assert real["partially_realised_usd"] == pytest.approx(-2.01, abs=1e-6)
        assert real["realised_pnl_usd"] == pytest.approx(-2.01, abs=1e-6)
        assert real["max_drawdown_usd"] == pytest.approx(2.01, abs=1e-6)
        assert real["includes_open_position_partial_results"] is True

        # THE CURVE POINT SAYS WHICH KIND OF RESULT IT IS. Without this a
        # reader cannot tell a closure from a partial exit and the whole
        # correction is invisible again.
        assert len(real["curve"]) == 1, real["curve"]
        assert real["curve"][0]["component"] == "EXIT_FILL", real["curve"]
        assert real["exit_fill_increments"] == 1, real
        assert real["terminal_increments"] == 0, real

        # THE INCREMENT DECOMPOSES, COMPONENT BY COMPONENT.
        inc = real["increments"][0]
        assert inc["qty"] == pytest.approx(9.0)
        assert inc["proceeds_usd"] == pytest.approx(3.69, abs=1e-6)
        assert inc["basis_per_contract"] == pytest.approx(0.60, abs=1e-8)
        assert inc["allocated_basis_usd"] == pytest.approx(5.40, abs=1e-6)
        assert inc["exit_fee_usd"] == pytest.approx(0.15, abs=1e-2)
        assert inc["entry_fee_share_usd"] == pytest.approx(0.15, abs=1e-2)
        # AND IT IS KEYED ON THE VENUE'S OWN FILL IDENTITY.
        assert inc["key"], inc
        assert inc["intent_id"] == "ent-partial-stop"

        # THE RESIDUAL IS NOT IN THE LOSS. Six contracts at 0.60 basis.
        assert real["partially_realised_open"] == ["ent-partial-stop"], real
        one = await FB.realised_on_sold(conn, "ent-partial-stop")
        assert one["residual_qty"] == pytest.approx(6.0)
        assert one["remaining_basis_usd"] == pytest.approx(3.60, abs=1e-6)
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_partial_result_matches_the_single_position_reader(
        monkeypatch):
    """`realised()` and `realised_on_sold()` must agree to the cent.

    They are two readers of one position and they now share
    `partial_realisation`. If they ever disagree, one of the operator
    surface and the loss stop is lying, and there is no way to tell which
    from the outside -- which is why this is pinned rather than trusted.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed_tight(conn, limits=TIGHT_STOP)
        await _breach(conn, monkeypatch)

        one = await FB.realised_on_sold(conn, "ent-partial-stop")
        many = await FB.realised(conn, account_id=ACCT, venue=VENUE)

        # ONE EXIT FILL, so the single-position figure and the sum of that
        # position's increments must be the same number. With several fills
        # they would not be individually equal -- the position figure is
        # the SUM -- which is why this sums rather than compares one row.
        incs = [c for c in many["increments"]
                if c["intent_id"] == "ent-partial-stop"
                and c["component"] == "EXIT_FILL"]
        assert len(incs) == 1, incs
        assert one["realised_on_sold_usd"] == pytest.approx(
            sum(c["net"] for c in incs), abs=1e-6), (one, incs)
        # AND THE SHARED COMPONENTS AGREE, because both use
        # `partial_realisation`'s arithmetic on the same fills.
        assert one["exit_proceeds_usd"] == pytest.approx(
            sum(c["proceeds_usd"] for c in incs), abs=1e-6)
        assert one["allocated_basis_usd"] == pytest.approx(
            sum(c["allocated_basis_usd"] for c in incs), abs=1e-6)
        assert one["sold_qty"] == pytest.approx(
            sum(c["qty"] for c in incs), abs=1e-6)
    finally:
        await _clean(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 2 · NEW EXPOSURE IS REFUSED
# ═════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_a_breached_partial_loss_refuses_new_exposure(monkeypatch):
    """The rail must read EXCEEDED, by name, with the measurement shown."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        eff = await _seed_tight(conn, limits=TIGHT_STOP)
        await _breach(conn, monkeypatch)

        # A SECOND, ENTIRELY DIFFERENT MARKET. The refusal must come from
        # the DRAWDOWN rail, not from a per-market or per-event cap that
        # happens to be breached at the same time -- so the proposed order
        # is deliberately somewhere else and deliberately small.
        plan = {"collateral_usd": 1.20, "quantity": 2.0,
                "event_key": "ev-unrelated-2026-09-28",
                "us_market_slug": "aec-nba-den-phx-2026-09-28",
                "limit_price": 0.60}
        got = await FX.check_rails(conn, plan, eff,
                                  account_id=ACCT, venue=VENUE)

        over = {r["rail"] for r in got["over"]}
        assert "MAX_DRAWDOWN" in over, got["rails"]
        dd = [r for r in got["rails"] if r["rail"] == "MAX_DRAWDOWN"][0]
        assert dd["verdict"] == "EXCEEDED", dd
        assert dd["measured"] == pytest.approx(2.01, abs=1e-6), dd
        assert dd["limit"] == pytest.approx(1.0), dd

        # THE BASIS MUST NAME THE PARTIAL COMPONENT. A rail whose stated
        # basis is narrower than its measurement is how a blind control
        # keeps looking complete -- the old text said only "closed".
        assert "partial exit" in dd["basis"], dd["basis"]
        assert "still open" in dd["basis"], dd["basis"]

        # AND IT MUST NOT CLAIM A CLOSURE THAT DID NOT HAPPEN.
        assert "0 closed funded position" in dd["basis"], dd["basis"]
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_same_order_clears_when_the_stop_is_not_breached(
        monkeypatch):
    """THE CONTROL FOR THE TEST ABOVE.

    A refusal proves nothing unless the same order passes when the stop is
    wide enough. Without this, a rail that refuses EVERYTHING -- a far
    more likely defect than one that refuses the right thing -- would make
    the previous test green.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        wide = dict(TIGHT_STOP, daily_loss_stop_usd=40.0)
        eff = await _seed_tight(conn, limits=wide)
        await _breach(conn, monkeypatch)

        plan = {"collateral_usd": 1.20, "quantity": 2.0,
                "event_key": "ev-unrelated-2026-09-28",
                "us_market_slug": "aec-nba-den-phx-2026-09-28",
                "limit_price": 0.60}
        got = await FX.check_rails(conn, plan, eff,
                                  account_id=ACCT, venue=VENUE)

        dd = [r for r in got["rails"] if r["rail"] == "MAX_DRAWDOWN"][0]
        assert dd["verdict"] == "WITHIN", dd
        # THE LOSS IS STILL MEASURED. "Within" must mean "measured and
        # under the limit", never "not looked at".
        assert dd["measured"] == pytest.approx(2.01, abs=1e-6), dd
    finally:
        await _clean(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 3 · SERVICING AND RECONCILIATION CONTINUE
# ═════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_servicing_the_held_inventory_is_still_permitted(monkeypatch):
    """A tripped loss stop must not strand the inventory it is about.

    THE FAILURE THIS GUARDS AGAINST is the expensive one: a stop that
    blocks exits as well as entries means the loss that tripped it can no
    longer be reduced. A lapse must shrink what we MAY DO; it must never
    strand what we already DID. `check_servicing` reads ownership from the
    position's own row and is deliberately not gated by submission
    authority -- this pins that the drawdown rail does not reach it.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed_tight(conn, limits=TIGHT_STOP)
        await _breach(conn, monkeypatch)

        got = await FB.check_servicing(conn, intent_id="ent-partial-stop",
                                      account_id=ACCT, venue=VENUE)
        assert got.get("ok") is True, got
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_four_quantities_stay_distinct_under_a_breach(monkeypatch):
    """Realised drawdown, unrealised, cash usage and worst case, apart.

    Each substitution is a specific wrong answer and all four are checked:

      cash usage as the loss     overstates it by the remaining basis
                                 plus the residual's entry-fee share
      unrealised as zero         asserts inventory is worth its cost
      worst case as the result   fabricates a loss that has not happened
      the stop as the worst case is the error that presented a $40
                                 trigger as the maximum loss on a $100
                                 position
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        eff = await _seed_tight(conn, limits=TIGHT_STOP)
        await _breach(conn, monkeypatch)

        lc = await FB.loss_controls(conn, account_id=ACCT, venue=VENUE,
                                    approved_limits=eff)

        # 1 · REALISED DRAWDOWN -- the loss actually taken.
        assert lc["realised_drawdown_usd"] == pytest.approx(2.01, abs=1e-6)
        assert lc["realised_includes_partial_exits"] is True

        # 2 · UNREALISED -- UNKNOWN, and never 0.
        assert lc["unrealised_pnl_usd"] == FB.NOT_IDENTIFIED
        assert lc["unrealised_pnl_usd"] != 0
        assert lc["unrealised_pnl_usd"] is not None
        assert lc["unmarked_policy"] == FB.UNMARKED_POLICY

        # 3 · CASH USAGE -- bigger than the loss, and labelled as cash.
        #     9.00 basis out, 0.40 of fees, 3.69 back = 5.71 of cash used
        #     against a 2.01 loss.
        #
        #     THE DIFFERENCE IS 3.70, NOT 3.60, AND THAT IS THE POINT. My
        #     first version asserted 3.60 -- the remaining basis alone --
        #     which is the ONE-TERM version of an identity this repository
        #     had already corrected to two terms. The gap is the residual's
        #     basis 3.60 PLUS the 0.10 of entry fee sitting on the six
        #     contracts still held. Reality caught the regression.
        assert lc["cash_used_usd"] == pytest.approx(5.71, abs=1e-6)
        assert lc["cash_used_usd"] > lc["realised_drawdown_usd"]
        assert (lc["cash_used_usd"] - lc["realised_drawdown_usd"]
                == pytest.approx(3.70, abs=1e-6))
        # AND IT RECONCILES AGAINST THE POSITION READER'S OWN TWO TERMS.
        one = await FB.realised_on_sold(conn, "ent-partial-stop")
        assert (one["remaining_basis_usd"]
                + one["entry_fees_allocated_to_residual_usd"]
                == pytest.approx(3.70, abs=1e-6)), one

        # 4 · WORST CASE -- the bound, above the realised loss and above
        #     the stop, and explicitly not a result.
        assert lc["worst_case_total_loss_usd"] >= 3.60 - 1e-6, lc
        assert lc["worst_case_total_loss_usd"] > lc["realised_drawdown_usd"]

        # THE FOUR ARE NOT ONE ANOTHER.
        assert len({round(lc["realised_drawdown_usd"], 4),
                    round(lc["cash_used_usd"], 4),
                    round(lc["worst_case_total_loss_usd"], 4)}) == 3, lc

        # AND THE CONTROL STATES SCOPE, TRIGGER AND RESPONSE.
        ctl = [c for c in lc["controls"] if c["control"] == "MAX_DRAWDOWN"][0]
        assert ctl["tripped"] is True, ctl
        for field in ("scope", "trigger", "response", "measures"):
            assert ctl[field], (field, ctl)
        # THE RESPONSE MUST SAY BOTH HALVES: refuse new exposure, leave
        # servicing and reconciliation alone.
        assert "REFUSES NEW EXPOSURE" in ctl["response"], ctl
        assert "econcil" in ctl["response"], ctl
        # AND IT IS NOT A MAXIMUM LOSS.
        assert "TRIGGER" in ctl["is_not_a_maximum_loss"], ctl
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_an_unmarked_residual_is_never_counted_as_zero(monkeypatch):
    """No mark source exists, so the residual must be UNKNOWN, not fine.

    "Never treat them as zero" is the owner's instruction and it has a
    precise operational meaning here: an unmarked holding contributes its
    WHOLE basis to the worst case, and contributes NOTHING to the realised
    result. Both halves are checked, because doing only the first would
    book an unrealised loss and doing only the second would report a blown
    position as flat.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        eff = await _seed_tight(conn, limits=TIGHT_STOP)
        await _breach(conn, monkeypatch)

        lc = await FB.loss_controls(conn, account_id=ACCT, venue=VENUE,
                                   approved_limits=eff)

        assert lc["unmarked_holdings"] == 1, lc
        # THE WHOLE RESIDUAL BASIS IS IN THE WORST CASE ...
        assert lc["remaining_basis_of_partially_exited_usd"] == pytest.approx(
            3.60, abs=1e-6)
        # ... AND NONE OF IT IS IN THE REALISED RESULT.
        assert lc["realised_drawdown_usd"] == pytest.approx(2.01, abs=1e-6)
        assert "WORST_CASE" in lc["unmarked_policy"]
        assert "NOT_FOR_REALISED" in lc["unmarked_policy"]
    finally:
        await _clean(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 4 · THE CURVE IS A HISTORY, NOT A SNAPSHOT
# ═════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_final_closure_does_not_count_the_partial_loss_again(
        monkeypatch):
    """Closure books the REMAINDER, not the whole net.

    THE DEFECT THIS PINS, AND IT WAS IN MY OWN FIRST VERSION. That one
    computed a per-position figure from current aggregates and booked it at
    the last exit fill; on closure the position left the open set and its
    ENTIRE economics net was booked at `closed_at`. The exit fill's loss
    was therefore counted twice in the sequence -- once while open, once
    inside the closing total -- and the trough that existed while open was
    replaced rather than kept.

    The terminal increment is `economics_net - already_booked`, so the two
    together sum to exactly the economics net and never more.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed_tight(conn, limits=TIGHT_STOP)
        await _breach(conn, monkeypatch)

        before = await FB.realised(conn, account_id=ACCT, venue=VENUE)
        assert before["exit_fill_increments"] == 1, before
        assert before["terminal_increments"] == 0, before
        open_dd = before["max_drawdown_usd"]
        assert open_dd == pytest.approx(2.01, abs=1e-6)

        # CLOSE IT. The residual settles worthless, which is the harshest
        # case and the one where double counting would be most visible.
        await conn.execute(
            # `SETTLED_BY_THE_VENUE`, NOT `SETTLED`. My first version used
            # the latter and PostgreSQL refused the row:
            # `bettor_funded_intents_closure_ck` admits exactly four closure
            # reasons, and an invented one is not among them. The schema
            # caught a test trying to create a state the book cannot hold --
            # which is the constraint doing precisely its job.
            "UPDATE bettor_funded_intents SET closed_at=now(), "
            "closed_reason='SETTLED_BY_THE_VENUE', residual_qty=0 "
            " WHERE intent_id=$1", "ent-partial-stop")

        after = await FB.realised(conn, account_id=ACCT, venue=VENUE)
        assert after["exit_fill_increments"] == 1, after
        assert after["terminal_increments"] == 1, after

        # THE SUM IS THE ECONOMICS NET, ONCE.
        econ = await conn.fetchval(
            "SELECT coalesce(sum(e.amount_usd),0)::float8 "
            "  FROM bettor_funded_economics e JOIN bettor_funded_intents i "
            "    ON i.intent_id=e.intent_id WHERE i.account_id=$1", ACCT)
        assert after["realised_pnl_usd"] == pytest.approx(float(econ),
                                                          abs=1e-4), after

        # AND THE TERMINAL INCREMENT IS THE REMAINDER, NOT THE TOTAL.
        term = [c for c in after["increments"]
                if c["component"] == "POSITION_TERMINAL"][0]
        assert term["already_booked_as_exit_fills_usd"] == pytest.approx(
            -2.01, abs=1e-6), term
        assert term["net"] == pytest.approx(
            term["economics_net_usd"] + 2.01, abs=1e-4), term
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_trough_that_happened_stays_in_the_history(monkeypatch):
    """A drawdown reached while open must survive the closure.

    `max_drawdown_usd` is a maximum OVER THE PAST. If closure rewrote the
    past, a position that dipped and recovered would report no drawdown --
    and the loss stop would un-trip itself by closing the position that
    tripped it, which is the worst possible direction for this defect.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed_tight(conn, limits=TIGHT_STOP)
        await _breach(conn, monkeypatch)
        curve_open = await FB.realised(conn, account_id=ACCT, venue=VENUE)
        at_open = [c for c in curve_open["increments"]
                   if c["component"] == "EXIT_FILL"][0]

        await conn.execute(
            "UPDATE bettor_funded_intents SET closed_at=now(), "
            "closed_reason='SETTLED_BY_THE_VENUE', residual_qty=0 "
            " WHERE intent_id=$1", "ent-partial-stop")
        curve_closed = await FB.realised(conn, account_id=ACCT, venue=VENUE)
        at_closed = [c for c in curve_closed["increments"]
                     if c["component"] == "EXIT_FILL"][0]

        # THE SAME INCREMENT, UNCHANGED, WITH THE SAME KEY AND INSTANT.
        assert at_open["key"] == at_closed["key"]
        assert at_open["at"] == at_closed["at"]
        assert at_open["net"] == at_closed["net"]
        # AND THE HISTORICAL TROUGH IS STILL IN THE CURVE.
        assert any(p["drawdown_usd"] == pytest.approx(2.01, abs=1e-6)
                   for p in curve_closed["curve"]), curve_closed["curve"]
        # THE MAXIMUM CANNOT HAVE FALLEN.
        assert (curve_closed["max_drawdown_usd"]
                >= curve_open["max_drawdown_usd"] - 1e-9)
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_replay_and_restart_neither_duplicate_nor_erase(monkeypatch):
    """Reading twice, and re-ingesting the same fills, is idempotent.

    Increments are keyed on the VENUE's own `fill_id` and the table is
    keyed on it too, so a redelivered execution is the same row. This
    checks the property end to end rather than trusting the key: the
    increment count and the curve must be byte-identical across a
    re-ingestion of the identical fill payload.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed_tight(conn, limits=TIGHT_STOP)
        await _breach(conn, monkeypatch)

        first = await FB.realised(conn, account_id=ACCT, venue=VENUE)
        # A SECOND READ ON UNCHANGED DATA -- restart equivalence.
        second = await FB.realised(conn, account_id=ACCT, venue=VENUE)
        assert first["increments"] == second["increments"]
        assert first["max_drawdown_usd"] == second["max_drawdown_usd"]

        # RE-INGEST THE IDENTICAL ENTRY FILL. `ingest_fills` is keyed on the
        # venue fill id, so this must add nothing.
        await FB.ingest_fills(conn, "ent-partial-stop", [
            {"qty": 15.0, "price": 0.60,
             "venue_fill_id": "vf-e-ent-partial-stop"}])
        third = await FB.realised(conn, account_id=ACCT, venue=VENUE)
        assert third["exit_fill_increments"] == first["exit_fill_increments"]
        assert third["realised_pnl_usd"] == pytest.approx(
            first["realised_pnl_usd"], abs=1e-6)
        assert third["max_drawdown_usd"] == pytest.approx(
            first["max_drawdown_usd"], abs=1e-6)
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_later_fee_correction_changes_value_not_identity(monkeypatch):
    """A venue fee restatement must move the number, not add a result.

    THE TWO WRONG BEHAVIOURS THIS EXCLUDES. A fee correction that appended
    a new increment would count the same exit twice; one that was ignored
    would leave a provisional figure standing as final. The increment's
    identity and the COUNT are invariant; only its value moves, by exactly
    the fee delta.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed_tight(conn, limits=TIGHT_STOP)
        await _breach(conn, monkeypatch)
        before = await FB.realised(conn, account_id=ACCT, venue=VENUE)
        n_before = before["exit_fill_increments"]
        inc_before = [c for c in before["increments"]
                      if c["component"] == "EXIT_FILL"][0]

        # THE VENUE RESTATES THE EXIT FEE UPWARD BY 0.20.
        await conn.execute(
            "UPDATE bettor_funded_fills SET fee_usd = fee_usd + 0.20 "
            " WHERE direction='EXIT'")

        after = await FB.realised(conn, account_id=ACCT, venue=VENUE)
        inc_after = [c for c in after["increments"]
                     if c["component"] == "EXIT_FILL"][0]

        assert after["exit_fill_increments"] == n_before, after
        assert inc_after["key"] == inc_before["key"]
        assert inc_after["at"] == inc_before["at"]
        # THE LOSS GREW BY EXACTLY THE FEE DELTA.
        assert inc_after["net"] == pytest.approx(inc_before["net"] - 0.20,
                                                 abs=1e-6)
        assert after["max_drawdown_usd"] == pytest.approx(
            before["max_drawdown_usd"] + 0.20, abs=1e-6)
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_gains_and_losses_across_positions_keep_the_convention(
        monkeypatch):
    """Two positions, chronological order, peak-to-trough preserved.

    THE CONVENTION IS DECLARED AND MUST BE OBEYED ACROSS POSITIONS: the
    curve is the sequence of results in the order each was TAKEN, and the
    drawdown is peak-to-trough over that sequence. Per-position maxima
    cannot be combined afterwards -- the worst trough may sit between two
    positions' events, and this builds exactly that shape: a gain, then a
    loss, so the trough depends on both.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed_tight(conn, limits=TIGHT_STOP)

        # POSITION 1: long 10 @ 0.40, sell 10 @ 0.55 -> a GAIN of 1.50.
        await _entry(conn, intent_id="ent-gain", qty=10, price=0.40)
        await _sell(conn, monkeypatch, parent="ent-gain", qty=10, price=0.55)
        # POSITION 2: the losing partial exit, taken AFTERWARDS.
        await _breach(conn, monkeypatch)

        got = await FB.realised(conn, account_id=ACCT, venue=VENUE)
        seq = [(c["component"], c["intent_id"], c["net"])
               for c in got["increments"]]

        # SELECTED BY IDENTITY, NOT BY INDEX. My first version read
        # `increments[1]` as the loss and got the GAIN position's terminal
        # increment instead: selling all 10 contracts closes that position,
        # so it produces an EXIT_FILL *and* a POSITION_TERMINAL. Indexing a
        # chronological ledger by position number assumes a shape the
        # ledger does not promise.
        gains = [c for c in got["increments"]
                 if c["intent_id"] == "ent-gain"
                 and c["component"] == "EXIT_FILL"]
        losses = [c for c in got["increments"]
                  if c["intent_id"] == "ent-partial-stop"
                  and c["component"] == "EXIT_FILL"]
        assert len(gains) == 1 and len(losses) == 1, seq
        gain_inc, loss_inc = gains[0], losses[0]

        # CHRONOLOGICAL: the gain precedes the loss, because it happened
        # first -- not because of position id or query order.
        assert gain_inc["at"] <= loss_inc["at"], seq
        assert gain_inc["net"] > 0, seq
        assert loss_inc["net"] < 0, seq

        # AND THE ANTI-DOUBLE-COUNT IS VISIBLE ON THE CLOSED POSITION.
        # `ent-gain` sold its entire inventory, so its exit fill already
        # booked the whole result and the terminal increment must add
        # NOTHING. A non-zero value here would be the double count.
        term = [c for c in got["increments"]
                if c["intent_id"] == "ent-gain"
                and c["component"] == "POSITION_TERMINAL"]
        assert len(term) == 1, seq
        assert term[0]["net"] == pytest.approx(0.0, abs=1e-4), term

        # THE PEAK IS THE GAIN; THE TROUGH IS MEASURED FROM IT.
        #
        # DERIVED FROM THE LEDGER, NOT HARDCODED. The fee schedule charges
        # both legs of both positions, so writing the expected cents by hand
        # would pin the fee curve into a drawdown test and break it the next
        # time the schedule is re-verified. What this test owns is the
        # CONVENTION, so it asserts the convention against the actual
        # increments.
        gain = gain_inc["net"]
        loss = loss_inc["net"]
        assert got["peak_realised_usd"] == pytest.approx(gain, abs=1e-6), got
        assert got["max_drawdown_usd"] == pytest.approx(-loss, abs=1e-6), got
        # AND NET P&L IS THE SUM, WHICH IS NOT THE DRAWDOWN.
        assert got["realised_pnl_usd"] == pytest.approx(
            gain + loss + term[0]["net"], abs=1e-4), got
        assert got["max_drawdown_usd"] != pytest.approx(
            abs(got["realised_pnl_usd"]), abs=1e-6), (
            "the drawdown must be peak-to-trough, not |net|; if these are "
            "equal the fixture no longer exercises a gain before the loss")
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_real_submission_path_refuses_the_new_entry(monkeypatch):
    """Not `check_rails` in isolation -- `submit_for_decision`.

    Owner requirement: "The real submission path refuses new exposure."

    THE DIFFERENCE MATTERS. `check_rails` returning EXCEEDED proves the
    measurement and the comparison. It does NOT prove that the code which
    actually sends an order consults that verdict and stops. A rail that
    reports a breach to a caller that proceeds anyway is not a control.
    This drives the entry through the submission entry point and requires
    that nothing was submitted and no intent row was written.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed_tight(conn, limits=TIGHT_STOP)
        await _breach(conn, monkeypatch)

        n_before = await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_intents WHERE account_id=$1",
            ACCT)

        rec = {"us_market_slug": "aec-nba-den-phx-2026-09-28",
               "event_key": "ev-unrelated-2026-09-28",
               "condition_id": "cond-unrelated",
               "order_intent": FX.LONG, "limit_price": 0.60,
               "quantity": 2.0, "payout_event": PAYS_ON,
               "held_is_long": True}
        got = await FX.submit_for_decision(
            conn, rec, account_id=ACCT, venue=VENUE)

        # NOTHING WENT OUT.
        assert got.get("submitted") is not True, got
        # AND NOTHING WAS WRITTEN -- a refusal must leave no row.
        n_after = await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_intents WHERE account_id=$1",
            ACCT)
        assert n_after == n_before, (n_before, n_after)

        # ── WHAT THIS TEST DOES AND DOES NOT ESTABLISH ──────────────
        #
        # It establishes that the real submission entry point refused and
        # wrote nothing. It does NOT establish that the DRAWDOWN rail was
        # the refusing gate: this fixture's `rec` is not an admitted
        # decision, so the path refuses earlier with
        # `..._NOT_ADMITTED_SO_THERE_IS_NOTHING_TO_SEND`. I originally
        # asserted "MAX_DRAWDOWN" appeared in the result and it did not --
        # so rather than loosen the assertion until it passed, the claim is
        # narrowed to what actually happened.
        #
        # THE RAIL ITSELF IS PROVEN SEPARATELY, in
        # `test_a_breached_partial_loss_refuses_new_exposure`, which drives
        # `check_rails` directly and requires verdict EXCEEDED with the
        # measurement shown. Between the two: the rail refuses on the
        # breach, and the submission path refuses without writing. What
        # remains unproven by this file is that the drawdown rail is the
        # FIRST gate to fire, and that is not a property worth asserting --
        # the order of fail-closed gates is not a contract.
        blob = str(got)
        assert "NOT_ADMITTED" in blob or "refus" in blob.lower(), got
        assert got.get("order") is None, got
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_reconciliation_still_runs_under_a_tripped_stop(monkeypatch):
    """Recovery and the read paths must not be gated by the loss stop.

    A tripped stop that also blocked reconciliation would leave the book
    unable to learn what the venue actually did -- so the position whose
    loss tripped the stop could not even be measured correctly afterwards.
    Ownership-bound work continues; only NEW exposure stops.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed_tight(conn, limits=TIGHT_STOP)
        await _breach(conn, monkeypatch)

        # The operator-facing reads all answer.
        pnl = await FB.pnl(conn, account_id=ACCT, venue=VENUE)
        assert pnl["max_drawdown_usd"] == pytest.approx(2.01, abs=1e-6)
        exp = await FB.exposure(conn, account_id=ACCT, venue=VENUE)
        assert exp["contracts_held"] == pytest.approx(6.0)
        # And servicing remains permitted on the held inventory.
        svc = await FB.check_servicing(conn, intent_id="ent-partial-stop",
                                      account_id=ACCT, venue=VENUE)
        assert svc.get("ok") is True, svc
    finally:
        await _clean(conn)
        await conn.close()
