"""RC6.3 allie-exposure: ALLIE'S BOOK AND FIXTURE EXPOSURE ARE THE OPEN
QUANTITY, NOT "THE GROUP HAS NO SETTLEMENT ROW".

THE DEFECT (independent reviewer of 1982534b, reproduced in production by the
SELECT-only research-sql run 38009262456): canonical_components
.allie_at_decision counted a filled ENTRY order as open exposure whenever its
group had no paper_settlements row. But bettor_paper_ledger.settle refuses
NO_OPEN_POSITION_TO_SETTLE for a position already exited to zero, so an
exited group never gets that row. Production: all 517 positions closed with 0
open, while those reads counted 322 groups worth $148,234 as open. Once Allie
is MEASURED (1982534b) her book and fixture inputs would have been wrong by
that amount.

THE FIX (open_position_canon.OPEN_EXPOSURE_*): the exposure of a position is
its canonical open quantity (bought - sold - the latest settlement's qty,
per account / group / market / side -- CANONICAL_OPEN_POSITIONS_SQL, the rule
every other reader uses) at the ledger's cost basis, per account and per
fixture.

These proofs run on a real Postgres, drive the REAL paper ledger and
simulator (submit_order, simulate_order, settle) on synthetic books in
scratch accounts, and reproduce the defect with the previous statements
(verbatim below) on the very same data.
"""
from __future__ import annotations

import pathlib
import time
import uuid

import pytest

from sportsassets import allie_capital as AC
from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_simulator as SIM
from sportsassets import canonical_components as CC
from sportsassets import decision_logic as DL
from sportsassets import open_position_canon as OPC
from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")

SRC = pathlib.Path(__file__).resolve().parents[1] / "sportsassets"

#: THE STATEMENTS THIS FIX REPLACED, verbatim from 8b4573b5
#: (canonical_components.allie_at_decision), each with only an account filter
#: added ($1 for the book, $2 for the fixture) so a scratch account is read
#: in isolation from whatever else the shared test database holds.
OLD_FIXTURE_SQL = """SELECT count(DISTINCT o.group_id) AS n,
                      coalesce(sum(o.filled_qty * o.limit_price), 0) AS usd
                 FROM paper_orders o
                WHERE o.role = 'ENTRY' AND o.fixture = $1 AND o.filled_qty > 0
                  AND NOT EXISTS (SELECT 1 FROM paper_settlements s
                                   WHERE s.group_id = o.group_id)
                  AND o.account_id = $2"""
OLD_BOOK_SQL = """SELECT coalesce(sum(o.filled_qty * o.limit_price), 0)
                 FROM paper_orders o
                WHERE o.role = 'ENTRY' AND o.filled_qty > 0
                  AND NOT EXISTS (SELECT 1 FROM paper_settlements s
                                   WHERE s.group_id = o.group_id)
                  AND o.account_id = $1"""


def _fx() -> str:
    return "fx-rc63-%s" % uuid.uuid4().hex[:10]


def SL(acct, name):
    """Market slugs are unique per test account (observed books and the
    consumed-liquidity ledger are shared by every paper order, by design)."""
    return "%s:%s" % (acct["account_id"], name)


async def _buy(conn, acct, *, key, group, slug, qty, limit, fixture, at,
               fee_fn=H.zero_fee, role="ENTRY", holding_side="LONG"):
    """A real ENTRY (or HEDGE) BUY, submitted through the ledger and filled
    by the simulator against a synthetic book that offers it all."""
    o = H.order(acct, key=key, qty=qty, limit=limit, slug=slug, at=at,
                group_id=group, fixture=fixture, role=role,
                holding_side=holding_side)
    got = await L.submit_order(conn, o, fee_fn=fee_fn, now=at)
    assert got["ok"], got
    # a two-sided book (a bid under the offer): the held position then has a
    # fresh mark, so the ledger's strict stale-management entry rail does not
    # refuse the NEXT entry of a test that holds several
    await H.observe(conn, slug, at + 3.0, offers=[(limit, qty)],
                    bids=[(round(limit - 0.05, 2), qty * 10)])
    sim = await SIM.simulate_order(conn, got["order"]["order_id"],
                                   now=at + 4.0, fee_fn=fee_fn)
    assert sim["state"] == "FILLED", sim
    return got["order"]["order_id"]


async def _sell(conn, acct, *, key, group, slug, qty, limit, fixture, at,
                fee_fn=H.zero_fee, holding_side="LONG"):
    """A real EXIT SELL of held inventory, filled against a synthetic bid."""
    o = H.order(acct, key=key, qty=qty, limit=limit, slug=slug, at=at,
                group_id=group, fixture=fixture, role="EXIT",
                direction="SELL", holding_side=holding_side)
    got = await L.submit_order(conn, o, fee_fn=fee_fn, now=at)
    assert got["ok"], got
    await H.observe(conn, slug, at + 3.0, bids=[(limit, qty)], offers=[])
    sim = await SIM.simulate_order(conn, got["order"]["order_id"],
                                   now=at + 4.0, fee_fn=fee_fn)
    assert sim["state"] == "FILLED", sim
    return got["order"]["order_id"]


async def _settle(conn, acct, *, group, slug, outcome="WON", at,
                  holding_side="LONG"):
    return await L.settle(
        conn, account_id=acct["account_id"], group_id=group, slug=slug,
        holding_side=holding_side, settlement_event_key="venue-final",
        outcome=outcome, evidence={"test": "synthetic"},
        evidence_source="TEST_FIXTURE", at=at)


async def _new(conn, tag):
    return await H.new_account(conn, tag)


async def _new_exposure(conn, acct, fixture):
    """(open groups on the fixture, their open cost, the account's book) by
    the statements the fix runs."""
    r = await conn.fetchrow(OPC.OPEN_EXPOSURE_FIXTURE_SQL,
                            acct["account_id"], fixture)
    book = await conn.fetchval(OPC.OPEN_EXPOSURE_BOOK_SQL,
                               acct["account_id"])
    return int(r["n"]), float(r["usd"]), float(book)


async def _old_exposure(conn, acct, fixture):
    r = await conn.fetchrow(OLD_FIXTURE_SQL, fixture, acct["account_id"])
    book = await conn.fetchval(OLD_BOOK_SQL, acct["account_id"])
    return int(r["n"]), float(r["usd"]), float(book)


# ───────────────────────────── the defect, reproduced ─────────────────────

@pg
async def test_an_exited_to_zero_group_is_not_open_exposure():
    """THE PRODUCTION SHAPE: a group is bought and sold back to zero. The
    ledger refuses to settle it (so it never gets a paper_settlements row),
    the previous statements count it open, and the canonical-quantity
    statements do not."""
    conn = await H.connect()
    try:
        a = await _new(conn, "exited")
        fx = _fx()
        slug = SL(a, "exited-ml")
        await _buy(conn, a, key="b", group="paper_g_exited", slug=slug,
                   qty=100, limit=0.50, fixture=fx, at=H.T0)
        # while it is held it IS open exposure: $50 on the fixture and book
        assert await _new_exposure(conn, a, fx) == (1, 50.0, 50.0)
        await _sell(conn, a, key="s", group="paper_g_exited", slug=slug,
                    qty=100, limit=0.55, fixture=fx, at=H.T0 + 100)
        # exited to zero: the ledger sees no open position ...
        assert await L.positions(conn, a["account_id"]) == []
        # ... refuses to settle it, so no settlement row will ever exist ...
        got = await _settle(conn, a, group="paper_g_exited", slug=slug,
                            at=H.T0 + 200)
        assert got == {"ok": False, "refusal": "NO_OPEN_POSITION_TO_SETTLE"}
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_settlements WHERE account_id=$1",
            a["account_id"]) == 0
        # ... THE DEFECT: the previous statements still count it open ...
        assert await _old_exposure(conn, a, fx) == (1, 50.0, 50.0)
        # ... THE FIX: nothing is open
        assert await _new_exposure(conn, a, fx) == (0, 0.0, 0.0)
    finally:
        await conn.close()


@pg
async def test_a_partially_exited_group_counts_only_the_remainder():
    conn = await H.connect()
    try:
        a = await _new(conn, "partial")
        fx = _fx()
        slug = SL(a, "partial-ml")
        await _buy(conn, a, key="b", group="paper_g_partial", slug=slug,
                   qty=100, limit=0.50, fixture=fx, at=H.T0)
        await _sell(conn, a, key="s", group="paper_g_partial", slug=slug,
                    qty=40, limit=0.55, fixture=fx, at=H.T0 + 100)
        pos = await L.positions(conn, a["account_id"])
        assert [(p["bought_qty"], p["sold_qty"], p["open_qty"])
                for p in pos] == [(100.0, 40.0, 60.0)]
        # THE DEFECT: the whole 100 x 0.50 was counted
        assert await _old_exposure(conn, a, fx) == (1, 50.0, 50.0)
        # THE FIX: 60 contracts remain, at their $0.50 cost
        assert await _new_exposure(conn, a, fx) == (1, 30.0, 30.0)
    finally:
        await conn.close()


@pg
async def test_the_remainder_is_costed_at_the_ledgers_average_including_fees():
    """The exposure is the ledger's own cost basis (avg cost per contract
    including fees x open qty -- the figure _exposure applies to
    R_PER_FIXTURE), not the limit price: 100 @ 0.50 with a $0.01 fee per
    contract costs $51.00, so 60 left is $30.60."""
    conn = await H.connect()
    try:
        a = await _new(conn, "fees")
        fx = _fx()
        slug = SL(a, "fees-ml")
        fee = H.flat_fee(0.01)
        await _buy(conn, a, key="b", group="paper_g_fees", slug=slug,
                   qty=100, limit=0.50, fixture=fx, at=H.T0, fee_fn=fee)
        await _sell(conn, a, key="s", group="paper_g_fees", slug=slug,
                    qty=40, limit=0.55, fixture=fx, at=H.T0 + 100,
                    fee_fn=fee)
        pos = (await L.positions(conn, a["account_id"]))[0]
        assert pos["cost_basis_usd"] == pytest.approx(30.6)
        n, usd, book = await _new_exposure(conn, a, fx)
        assert (n, usd, book) == (1, pytest.approx(30.6),
                                  pytest.approx(30.6))
    finally:
        await conn.close()


@pg
async def test_a_settled_group_is_not_open_exposure():
    conn = await H.connect()
    try:
        a = await _new(conn, "settled")
        fx = _fx()
        slug = SL(a, "settled-ml")
        await _buy(conn, a, key="b", group="paper_g_settled", slug=slug,
                   qty=100, limit=0.50, fixture=fx, at=H.T0)
        assert await _new_exposure(conn, a, fx) == (1, 50.0, 50.0)
        got = await _settle(conn, a, group="paper_g_settled", slug=slug,
                            at=H.T0 + 100)
        assert got["ok"], got
        assert await _new_exposure(conn, a, fx) == (0, 0.0, 0.0)
        # a corrected settlement version is still settled
        assert (await L.correct_settlement(
            conn, account_id=a["account_id"], group_id="paper_g_settled",
            slug=slug, holding_side="LONG",
            settlement_event_key="venue-final", outcome="LOST",
            evidence={"test": "synthetic"}, evidence_source="TEST_FIXTURE",
            at=H.T0 + 150))["ok"]
        assert await _new_exposure(conn, a, fx) == (0, 0.0, 0.0)
    finally:
        await conn.close()


@pg
async def test_an_open_group_counts_in_full():
    conn = await H.connect()
    try:
        a = await _new(conn, "open")
        fx = _fx()
        await _buy(conn, a, key="b1", group="paper_g_open1",
                   slug=SL(a, "open-1"), qty=100, limit=0.50, fixture=fx,
                   at=H.T0)
        await _buy(conn, a, key="b2", group="paper_g_open2",
                   slug=SL(a, "open-2"), qty=50, limit=0.40, fixture=fx,
                   at=H.T0 + 10)
        # the previous statements agree on a book with nothing exited
        assert await _old_exposure(conn, a, fx) == (2, 70.0, 70.0)
        assert await _new_exposure(conn, a, fx) == (2, 70.0, 70.0)
    finally:
        await conn.close()


@pg
async def test_fixture_exposure_is_per_fixture_and_the_book_is_the_whole_book():
    """The same book read both ways: an exited group, a partly exited one, a
    settled one and an open one on the decision's fixture, and one open group
    on ANOTHER fixture. The fixture sees only its own remainder; the book
    sees every remainder."""
    conn = await H.connect()
    try:
        a = await _new(conn, "mixed")
        fx, other = _fx(), _fx()
        s = {k: SL(a, k) for k in ("exited", "part", "settled", "open",
                                   "elsewhere")}
        for i, (k, qty, limit, f) in enumerate((
                ("exited", 100, 0.50, fx), ("part", 100, 0.50, fx),
                ("settled", 100, 0.50, fx), ("open", 40, 0.25, fx),
                ("elsewhere", 200, 0.30, other))):
            await _buy(conn, a, key="b-" + k, group="paper_g_" + k,
                       slug=s[k], qty=qty, limit=limit, fixture=f,
                       at=H.T0 + 10 * i)
        await _sell(conn, a, key="s-exited", group="paper_g_exited",
                    slug=s["exited"], qty=100, limit=0.55, fixture=fx,
                    at=H.T0 + 100)
        await _sell(conn, a, key="s-part", group="paper_g_part",
                    slug=s["part"], qty=40, limit=0.55, fixture=fx,
                    at=H.T0 + 110)
        assert (await _settle(conn, a, group="paper_g_settled",
                              slug=s["settled"], at=H.T0 + 120))["ok"]
        # the previous statements: exited + partly exited + open on the
        # fixture (100 x .50 + 100 x .50 + 40 x .25) and the other fixture
        assert await _old_exposure(conn, a, fx) == (3, 110.0, 170.0)
        # the fix: the part's 60 x 0.50 and the open group's 40 x 0.25
        assert await _new_exposure(conn, a, fx) == (2, 40.0, 100.0)
        assert await _new_exposure(conn, a, other) == (1, 60.0, 100.0)
    finally:
        await conn.close()


@pg
async def test_a_hedge_leg_still_held_keeps_its_group_open():
    """A group whose ENTRY leg is exited but whose HEDGE leg is still held is
    open exposure (its remaining inventory is capital at risk) -- which the
    "ENTRY orders with no settlement row" reads got wrong in both directions
    (they never counted the hedge, and dropped the group once ANY of its
    positions settled)."""
    conn = await H.connect()
    try:
        a = await _new(conn, "hedge")
        fx = _fx()
        main, hedge = SL(a, "hedge-main"), SL(a, "hedge-other")
        await _buy(conn, a, key="b", group="paper_g_hedge", slug=main,
                   qty=100, limit=0.50, fixture=fx, at=H.T0)
        await _buy(conn, a, key="h", group="paper_g_hedge", slug=hedge,
                   qty=100, limit=0.40, fixture=fx, at=H.T0 + 10,
                   role="HEDGE")
        assert await _new_exposure(conn, a, fx) == (1, 90.0, 90.0)
        # the ENTRY leg settles: only the hedge remains
        assert (await _settle(conn, a, group="paper_g_hedge", slug=main,
                              at=H.T0 + 100))["ok"]
        assert await _new_exposure(conn, a, fx) == (1, 40.0, 40.0)
        # the previous statements dropped the whole group at that moment
        assert await _old_exposure(conn, a, fx) == (0, 0.0, 0.0)
        assert (await _settle(conn, a, group="paper_g_hedge", slug=hedge,
                              outcome="LOST", at=H.T0 + 110))["ok"]
        assert await _new_exposure(conn, a, fx) == (0, 0.0, 0.0)
    finally:
        await conn.close()


@pg
async def test_exposure_is_per_account():
    conn = await H.connect()
    try:
        a, b = await _new(conn, "acct-a"), await _new(conn, "acct-b")
        fx = _fx()
        await _buy(conn, a, key="b", group="paper_g_acct_a",
                   slug=SL(a, "m"), qty=100, limit=0.50, fixture=fx,
                   at=H.T0)
        await _buy(conn, b, key="b", group="paper_g_acct_b",
                   slug=SL(b, "m"), qty=30, limit=0.40, fixture=fx,
                   at=H.T0)
        assert await _new_exposure(conn, a, fx) == (1, 50.0, 50.0)
        assert await _new_exposure(conn, b, fx) == (1, 12.0, 12.0)
        # no account named: every account's book on the fixture
        r = await conn.fetchrow(OPC.OPEN_EXPOSURE_FIXTURE_SQL, None, fx)
        assert (int(r["n"]), float(r["usd"])) == (2, 62.0)
        # an account with no positions has none
        c = await _new(conn, "acct-c")
        assert await _new_exposure(conn, c, fx) == (0, 0.0, 0.0)
        # exiting account A's group leaves B's untouched
        await _sell(conn, a, key="s", group="paper_g_acct_a",
                    slug=SL(a, "m"), qty=100, limit=0.55, fixture=fx,
                    at=H.T0 + 100)
        assert await _new_exposure(conn, a, fx) == (0, 0.0, 0.0)
        assert await _new_exposure(conn, b, fx) == (1, 12.0, 12.0)
    finally:
        await conn.close()


@pg
async def test_the_exposure_is_exactly_the_ledgers_open_positions_and_cost_basis():
    """Reuse, not reinvention: after a mixed lifecycle the open exposure rows
    ARE the ledger's open positions (positions(): bought - sold - settled),
    and the dollars are their cost_basis_usd."""
    conn = await H.connect()
    try:
        a = await _new(conn, "parity")
        fx = _fx()
        fee = H.flat_fee(0.02)
        # (key, bought, limit, sold): p1 untouched, p2 partly exited, p3
        # settled, p4 exited to zero
        specs = (("p1", 100, 0.50, 0), ("p2", 80, 0.35, 30),
                 ("p3", 60, 0.45, 0), ("p4", 25, 0.60, 25))
        for i, (k, qty, limit, sold) in enumerate(specs):
            await _buy(conn, a, key="b" + k, group="paper_g_" + k,
                       slug=SL(a, k), qty=qty, limit=limit, fixture=fx,
                       at=H.T0 + 10 * i, fee_fn=fee)
            if sold:
                await _sell(conn, a, key="s" + k, group="paper_g_" + k,
                            slug=SL(a, k), qty=sold, limit=limit + .05,
                            fixture=fx, at=H.T0 + 200 + 10 * i, fee_fn=fee)
        assert (await _settle(conn, a, group="paper_g_p3", slug=SL(a, "p3"),
                              at=H.T0 + 400))["ok"]
        pos = await L.positions(conn, a["account_id"])
        rows = await conn.fetch(OPC.OPEN_EXPOSURE_ROWS_SQL, a["account_id"])
        assert ({(r["group_id"], r["us_market_slug"], r["holding_side"])
                 for r in rows}
                == {(p["group_id"], p["us_market_slug"], p["holding_side"])
                    for p in pos})
        assert {p["group_id"] for p in pos} == {"paper_g_p1", "paper_g_p2"}
        want = sum(p["cost_basis_usd"] for p in pos)
        n, usd, book = await _new_exposure(conn, a, fx)
        assert n == 2 and usd == pytest.approx(want, abs=1e-5)
        assert book == pytest.approx(want, abs=1e-5)
        # and it is what the ledger's own per-fixture concentration cap sums
        assert float(await L._exposure(conn, a["account_id"],
                                       fixture=fx)) == pytest.approx(
            want, abs=1e-5)
    finally:
        await conn.close()


# ───────────────────────── through Allie, end to end ──────────────────────

def _decision(fixture):
    T = time.time()
    return {"decision_id": "paperrc63:%s" % uuid.uuid4().hex[:8],
            "decided_at": T, "strategy": "PINNACLE_ONLY_PAPER_BENCHMARK",
            "fixture": fixture, "us_market_slug": "rc63-exposure-slug",
            "holding_side": "LONG", "proposed_qty": 100,
            "limit_price": 0.52, "capital_required_usd": 52.5,
            "executable_opportunity_dollars": 5.0,
            "event_start_at": T + 3600, "per_order_cap_usd": 5000}


@pg
async def test_allie_at_the_decision_reads_the_open_quantity():
    """allie_at_decision feeds allie_capital her fixture groups, fixture
    dollars and book dollars from the account's open quantity: an exited
    group is not in them, a partly exited one is in them by its remainder,
    and her authority is unchanged (SHADOW_PENDING_OWNER_APPROVAL)."""
    conn = await H.connect()
    CC.reset_cache()
    try:
        a = await _new(conn, "allie")
        fx = _fx()
        for i, (k, qty) in enumerate((("gone", 100), ("half", 100),
                                      ("held", 40))):
            await _buy(conn, a, key="b" + k, group="paper_g_" + k,
                       slug=SL(a, k), qty=qty, limit=0.50, fixture=fx,
                       at=H.T0 + 10 * i)
        await _sell(conn, a, key="sgone", group="paper_g_gone",
                    slug=SL(a, "gone"), qty=100, limit=0.55, fixture=fx,
                    at=H.T0 + 100)
        await _sell(conn, a, key="shalf", group="paper_g_half",
                    slug=SL(a, "half"), qty=40, limit=0.55, fixture=fx,
                    at=H.T0 + 110)
        dec = _decision(fx)
        allie = await CC.allie_at_decision(
            conn, decision=dec, eddie={"status": "UNAVAILABLE"},
            now=dec["decided_at"], account_id=a["account_id"])
        cc = allie["correlation_concentration"]
        # half: 60 x 0.50 = 30; held: 40 x 0.50 = 20; gone: nothing
        assert cc["fixture_open_groups"] == 2
        assert cc["fixture_open_usd"] == pytest.approx(50.0)
        assert cc["book_open_usd"] == pytest.approx(50.0)
        assert cc["haircut"] == pytest.approx(
            min(1.0, AC.CORRELATION_HAIRCUT * 2), abs=1e-4)
        assert cc["fixture_headroom_usd"] == pytest.approx(
            AC.FIXTURE_CAP_USD - 50.0)
        assert cc["book_headroom_usd"] == pytest.approx(
            AC.BOOK_CAP_USD - 50.0)
        # the basis is recorded beside the inputs
        assert allie["open_exposure_basis"] == {
            "rule": OPC.OPEN_EXPOSURE_BASIS,
            "account_scope": a["account_id"]}
        # authority unchanged: shadow, pending the owner
        assert allie["authority"] == "SHADOW_PENDING_OWNER_APPROVAL"
        # the decision's own account is honoured when none is passed
        allie2 = await CC.allie_at_decision(
            conn, decision=dict(dec, account_id=a["account_id"]),
            eddie={"status": "UNAVAILABLE"}, now=dec["decided_at"])
        assert allie2["correlation_concentration"] == cc
        assert allie2["open_exposure_basis"]["account_scope"] == \
            a["account_id"]
        # another account's book is not hers
        b = await _new(conn, "allie-other")
        allie3 = await CC.allie_at_decision(
            conn, decision=dec, eddie={"status": "UNAVAILABLE"},
            now=dec["decided_at"], account_id=b["account_id"])
        assert allie3["correlation_concentration"]["fixture_open_groups"] == 0
        assert allie3["correlation_concentration"]["book_open_usd"] == 0.0
    finally:
        CC.reset_cache()
        await conn.close()


@pg
async def test_allie_with_every_group_exited_sees_an_empty_book():
    """THE PRODUCTION STATE: every position closed. Her inputs are 0 groups
    and $0 -- not the exited groups' notional."""
    conn = await H.connect()
    CC.reset_cache()
    try:
        a = await _new(conn, "allclosed")
        fx = _fx()
        for i in range(3):
            await _buy(conn, a, key="b%d" % i, group="paper_g_ac%d" % i,
                       slug=SL(a, "ac%d" % i), qty=100, limit=0.50,
                       fixture=fx, at=H.T0 + 10 * i)
            await _sell(conn, a, key="s%d" % i, group="paper_g_ac%d" % i,
                        slug=SL(a, "ac%d" % i), qty=100, limit=0.55,
                        fixture=fx, at=H.T0 + 100 + 10 * i)
        assert await L.positions(conn, a["account_id"]) == []
        assert await _old_exposure(conn, a, fx) == (3, 150.0, 150.0)
        dec = _decision(fx)
        allie = await CC.allie_at_decision(
            conn, decision=dec, eddie={"status": "UNAVAILABLE"},
            now=dec["decided_at"], account_id=a["account_id"])
        cc = allie["correlation_concentration"]
        assert cc["fixture_open_groups"] == 0
        assert cc["fixture_open_usd"] == 0.0
        assert cc["book_open_usd"] == 0.0
        assert cc["haircut"] == 0.0
        assert cc["fixture_headroom_usd"] == AC.FIXTURE_CAP_USD
        assert cc["book_headroom_usd"] == AC.BOOK_CAP_USD
    finally:
        CC.reset_cache()
        await conn.close()


@pg
async def test_the_correlation_graphs_allie_query_is_the_same_statement():
    from sportsassets import correlation_graph as CG
    conn = await H.connect()
    try:
        a = await _new(conn, "cgsame")
        fx = _fx()
        await _buy(conn, a, key="b", group="paper_g_cg", slug=SL(a, "cg"),
                   qty=100, limit=0.50, fixture=fx, at=H.T0)
        await _buy(conn, a, key="b2", group="paper_g_cg2",
                   slug=SL(a, "cg2"), qty=100, limit=0.50, fixture=fx,
                   at=H.T0 + 10)
        await _sell(conn, a, key="s2", group="paper_g_cg2",
                    slug=SL(a, "cg2"), qty=100, limit=0.55, fixture=fx,
                    at=H.T0 + 100)
        assert CG.ALLIE_FIXTURE_SQL is OPC.OPEN_EXPOSURE_FIXTURE_SQL
        r = await conn.fetchrow(CG.ALLIE_FIXTURE_SQL, a["account_id"], fx)
        assert (int(r["n"]), float(r["usd"])) == (1, 50.0)
    finally:
        await conn.close()


# ───────────────────────────── production shape ───────────────────────────

@pg
async def test_production_shape_exited_groups_are_zero_where_the_old_reads_counted_them():
    """The 2026-10-10 production state at scale: hundreds of positions all
    exited to zero (no settlement rows), a few settled, a few open. Written
    as the ledger's own tables hold them (orders and fills; the read side
    derives positions from fills) in one transaction that is rolled back."""
    conn = await H.connect()
    tx = conn.transaction()
    await tx.start()
    try:
        a = await _new(conn, "prodshape")
        acct, sess, fx = a["account_id"], a["session_id"], _fx()
        N_EXITED, N_SETTLED, N_OPEN = 322, 20, 12
        await conn.execute(
            """
            INSERT INTO paper_orders (order_id, idempotency_key, account_id,
                session_id, group_id, role, direction, holding_side, intent,
                us_market_slug, fixture, order_type, time_in_force,
                allow_partial, qty, limit_price, wire_price, filled_qty,
                state, decided_at, eligible_at, expires_at,
                simulator_version)
            SELECT 'paperord:ps-' || $1 || g || '-' || d.dir,
                   'ps-' || $1 || g || '-' || d.dir, $2, $3,
                   'paper_g_ps_' || $1 || g, d.role, d.dir, 'LONG', 'X',
                   'ps-' || $1 || g, $4, 'MARKETABLE', 'IOC', true, 100,
                   0.50, 0.50, 100, 'FILLED', now(), now(), now(), 'T'
              FROM generate_series(1, $5::int + $6::int + $7::int) g,
                   (VALUES ('BUY', 'ENTRY'), ('SELL', 'EXIT')) d(dir, role)
             WHERE d.dir = 'BUY' OR g <= $5::int
            """, uuid.uuid4().hex[:6], acct, sess, fx, N_EXITED, N_SETTLED,
            N_OPEN)
        await conn.execute(
            """
            INSERT INTO paper_fills (fill_id, idempotency_key, order_id,
                account_id, session_id, group_id, role, direction,
                holding_side, us_market_slug, fixture, qty, price,
                wire_price, fee_usd, gross_usd, filled_at, basis,
                simulator_version)
            SELECT 'paperfill:' || o.order_id, 'fill:' || o.order_id,
                   o.order_id, o.account_id, o.session_id, o.group_id,
                   o.role, o.direction, 'LONG', o.us_market_slug, o.fixture,
                   100, 0.50, 0.50, 0, 50, now(),
                   'DEPTH_WALK_WITHIN_LIMIT', 'T'
              FROM paper_orders o WHERE o.account_id = $1
            """, acct)
        # the settled groups: a settlement row for each (qty 100, WON)
        await conn.execute(
            """
            INSERT INTO paper_settlements (settlement_id, account_id,
                position_key, settlement_event_key, version, group_id,
                us_market_slug, holding_side, qty, outcome,
                payout_per_contract, payout_usd, evidence, evidence_source,
                settled_at)
            SELECT 'paperset:ps-' || o.order_id, o.account_id,
                   'paperpos:' || o.account_id || ':' || o.group_id || ':'
                   || o.us_market_slug || ':LONG', 'venue-final', 1,
                   o.group_id, o.us_market_slug, 'LONG', 100, 'WON', 1, 100,
                   '{}'::jsonb, 'TEST_FIXTURE', now()
              FROM paper_orders o
             WHERE o.account_id = $1 AND o.direction = 'BUY'
               AND o.group_id IN (
                   SELECT group_id FROM paper_orders WHERE account_id = $1
                   GROUP BY group_id HAVING count(*) = 1
                   ORDER BY group_id LIMIT $2::int)
            """, acct, N_SETTLED)
        # the previous statements: exited + open (every group with no row)
        old_n, old_usd, old_book = await _old_exposure(conn, a, fx)
        assert old_n == N_EXITED + N_OPEN
        assert old_usd == pytest.approx(50.0 * (N_EXITED + N_OPEN))
        # the fix: only the groups still holding inventory
        n, usd, book = await _new_exposure(conn, a, fx)
        assert n == N_OPEN
        assert usd == pytest.approx(50.0 * N_OPEN)
        assert book == pytest.approx(50.0 * N_OPEN)
        # all-closed: settle the open ones too; the book is empty
        await conn.execute(
            """
            INSERT INTO paper_settlements (settlement_id, account_id,
                position_key, settlement_event_key, version, group_id,
                us_market_slug, holding_side, qty, outcome,
                payout_per_contract, payout_usd, evidence, evidence_source,
                settled_at)
            SELECT 'paperset:ps2-' || o.order_id, o.account_id,
                   'paperpos:' || o.account_id || ':' || o.group_id || ':'
                   || o.us_market_slug || ':LONG', 'venue-final', 1,
                   o.group_id, o.us_market_slug, 'LONG', 100, 'LOST', 0, 0,
                   '{}'::jsonb, 'TEST_FIXTURE', now()
              FROM paper_orders o
             WHERE o.account_id = $1 AND o.direction = 'BUY'
               AND o.group_id IN (
                   SELECT p.group_id FROM paper_orders p
                    WHERE p.account_id = $1
                    GROUP BY p.group_id HAVING count(*) = 1
                      AND NOT EXISTS (SELECT 1 FROM paper_settlements s
                                       WHERE s.group_id = p.group_id))
            """, acct)
        assert await _new_exposure(conn, a, fx) == (0, 0.0, 0.0)
    finally:
        await tx.rollback()
        await conn.close()


# ───────────────────────────── static pins ────────────────────────────────

def test_the_exposure_statements_are_built_on_the_canonical_open_rule():
    assert OPC.CANONICAL_OPEN_POSITIONS_SQL in OPC.OPEN_EXPOSURE_ROWS_SQL
    assert OPC.OPEN_EXPOSURE_ROWS_SQL in OPC.OPEN_EXPOSURE_FIXTURE_SQL
    assert OPC.OPEN_EXPOSURE_ROWS_SQL in OPC.OPEN_EXPOSURE_BOOK_SQL
    for sql in (OPC.OPEN_EXPOSURE_FIXTURE_SQL, OPC.OPEN_EXPOSURE_BOOK_SQL):
        low = " ".join(sql.lower().split())
        # reads only; and never "no settlement row" as the test of open
        for bad in ("insert ", "update ", "delete ", "not exists",
                    "paper_orders"):
            assert bad not in low, bad
        assert "bought - f.sold - coalesce(s.qty, 0) > 1e-9" in low.replace(
            "f.bought", "bought")


def test_allie_and_the_graph_no_longer_test_open_by_a_missing_settlement_row():
    for name in ("canonical_components.py", "correlation_graph.py"):
        text = (SRC / name).read_text()
        assert "NOT EXISTS (SELECT 1 FROM paper_settlements" not in text, name
        assert "o.filled_qty * o.limit_price" not in text, name
    # the module her exposure inputs now live in is part of the pinned
    # decision logic, so a change to it restarts the forward window
    assert "open_position_canon.py" in DL.DECISION_LOGIC_FILES
    assert DL.decision_logic_hash()["missing"] == []
