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

    Fee-free, so the arithmetic is visible: proceeds 9 x 0.41 = 3.69
    against an allocated basis of 9 x 0.60 = 5.40, a realised loss of
    -$1.71 on the sold quantity. Six contracts remain, and their $3.60 of
    basis is an asset at cost -- NOT part of the loss.
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
        assert real["partially_realised_usd"] == pytest.approx(-1.71, abs=1e-6)
        assert real["realised_pnl_usd"] == pytest.approx(-1.71, abs=1e-6)
        assert real["max_drawdown_usd"] == pytest.approx(1.71, abs=1e-6)
        assert real["includes_open_position_partial_results"] is True

        # THE CURVE POINT SAYS WHICH KIND OF RESULT IT IS. Without this a
        # reader cannot tell a closure from a partial exit and the whole
        # correction is invisible again.
        assert len(real["curve"]) == 1, real["curve"]
        assert (real["curve"][0]["component"]
                == "REALISED_ON_SOLD_WHILE_OPEN"), real["curve"]

        # THE RESIDUAL IS NOT IN THE LOSS. Six contracts at 0.60 basis.
        row = real["partially_realised_open"][0]
        assert row["residual_qty"] == pytest.approx(6.0)
        assert row["remaining_basis_usd"] == pytest.approx(3.60, abs=1e-6)
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
        row = many["partially_realised_open"][0]

        assert one["realised_on_sold_usd"] == row["net"], (one, row)
        for k in ("sold_qty", "exit_proceeds_usd", "allocated_basis_usd",
                  "fees_on_sold_usd", "residual_qty",
                  "remaining_basis_usd"):
            assert one[k] == row["components"][k], (k, one[k], row)
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
        assert dd["measured"] == pytest.approx(1.71, abs=1e-6), dd
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
        assert dd["measured"] == pytest.approx(1.71, abs=1e-6), dd
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
        assert lc["realised_drawdown_usd"] == pytest.approx(1.71, abs=1e-6)
        assert lc["realised_includes_partial_exits"] is True

        # 2 · UNREALISED -- UNKNOWN, and never 0.
        assert lc["unrealised_pnl_usd"] == FB.NOT_IDENTIFIED
        assert lc["unrealised_pnl_usd"] != 0
        assert lc["unrealised_pnl_usd"] is not None
        assert lc["unmarked_policy"] == FB.UNMARKED_POLICY

        # 3 · CASH USAGE -- bigger than the loss, and labelled as cash.
        #     9.00 out, 3.69 back, fee-free: 5.31 of cash used against a
        #     1.71 loss. The 3.60 difference is the residual's basis.
        assert lc["cash_used_usd"] == pytest.approx(5.31, abs=1e-6)
        assert lc["cash_used_usd"] > lc["realised_drawdown_usd"]
        assert (lc["cash_used_usd"] - lc["realised_drawdown_usd"]
                == pytest.approx(3.60, abs=1e-6))

        # 4 · WORST CASE -- the bound, above the realised loss and above
        #     the stop, and explicitly not a result.
        assert lc["worst_case_total_loss_usd"] >= pytest.approx(3.60,
                                                               abs=1e-6)
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
        assert lc["realised_drawdown_usd"] == pytest.approx(1.71, abs=1e-6)
        assert "WORST_CASE" in lc["unmarked_policy"]
        assert "NOT_FOR_REALISED" in lc["unmarked_policy"]
    finally:
        await _clean(conn)
        await conn.close()
