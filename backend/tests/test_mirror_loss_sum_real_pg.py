"""The loss stop's 24 h figure EXECUTES against real Postgres (E3).

The day reconciliation of 2026-09-06 23:10Z found `_SQL_LOSS_SUM`
counting a settled book's sales twice: it summed realized_pnl over
every book updated in 24 h PLUS settled_pnl over every book
closed-settled in 24 h, but settled_pnl is the venue's WHOLE-position
figure (what _close_settled cross-checks `own = realized + shares x
(payout - avg)` against), so it already holds the realized part.
Book 16 (realized -244.75, settled -315.40), 3 (+2.48 / +156.17),
19 (-2.11 / -41.46) and 22 (+14.64 / +19.74) put the 22:22Z reading at
-2,445 where the truth was about -2,215. tests/test_mirror_live_worker
pins the statement's text and drives the stop through the fake pool;
text is not proof (tests/test_s1_sql_real_pg), so this file builds a
scratch database, inserts tonight's books beside an open one, a
cashed-out close and a settlement outside the window, and executes the
statement as the worker does -- and the render-ops `mirror-pnl`
preset's totals row, whose `day_pnl` is the same rule, straight from
the workflow file.

Skips (visibly) when no local Postgres answers, the way the S1 pin
does -- the fleet and dev boxes run one; CI without it loses this
file only.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import re
import uuid
from datetime import timedelta

import pytest

from sportsassets.workers import mirror_live as ml

DSN_BASE = os.environ.get(
    "S1_SQL_PIN_DSN",
    "postgresql://sportsassets:sportsassets@localhost:5432/postgres")
RENDER_OPS = pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows" / "render-ops.yml"

# the columns the loss sum and the preset read, typed as 047 types them
DDL = """
CREATE TABLE mirror_books (
    id bigserial PRIMARY KEY,
    us_market_slug text NOT NULL DEFAULT 'slug',
    intent text NOT NULL DEFAULT 'ORDER_INTENT_BUY_LONG',
    ratio double precision, ledger_net integer NOT NULL DEFAULT 0,
    avg_cost double precision,
    peak_exposure_usd double precision NOT NULL DEFAULT 0,
    state text NOT NULL DEFAULT 'live',
    realized_pnl double precision NOT NULL DEFAULT 0,
    settled_pnl double precision, own_book_pnl double precision,
    opened_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    closed_at timestamptz);
CREATE TABLE mirror_orders (
    id bigserial PRIMARY KEY, side text NOT NULL,
    filled double precision NOT NULL DEFAULT 0,
    cash_usd double precision NOT NULL DEFAULT 0,
    realized double precision NOT NULL DEFAULT 0,
    placed_at timestamptz NOT NULL DEFAULT now());
"""

# tonight's four settled books: (id, realized_pnl, settled_pnl)
TONIGHT = [(16, -244.75, -315.40), (3, 2.48, 156.17), (19, -2.11, -41.46), (22, 14.64, 19.74)]
SETTLED_TONIGHT = sum(s for _, _, s in TONIGHT)          # -180.95
REALIZED_TONIGHT = sum(r for _, r, _ in TONIGHT)         # -229.74, the double count's excess


async def _scratch():
    asyncpg = pytest.importorskip("asyncpg")
    try:
        admin = await asyncpg.connect(DSN_BASE, timeout=4)
    except Exception:  # noqa: BLE001 — no local PG: skip, never fake
        pytest.skip("no local postgres for the real-SQL pin")
    name = "loss_sum_pin_" + uuid.uuid4().hex[:10]
    await admin.execute(f'CREATE DATABASE "{name}"')
    conn = await asyncpg.connect(DSN_BASE.rsplit("/", 1)[0] + "/" + name, timeout=4)
    for stmt in DDL.split(";"):
        if stmt.strip():
            await conn.execute(stmt)
    return admin, conn, name


async def _drop(admin, conn, name):
    await conn.close()
    await admin.execute(f'DROP DATABASE "{name}"')
    await admin.close()


async def _book(c, bid, state, realized, settled=None, closed_h=1.0, opened_h=3.0,
                ledger_net=0, avg_cost=None):
    """One row, its clocks as hours before now(). A closed row's
    updated_at is its closed_at, as both close statements stamp them
    (ml-book-settled, ml-book-state); an open row's is ten minutes old."""
    updated_h = closed_h if state == "closed" else 1 / 6
    await c.execute(
        "INSERT INTO mirror_books (id, state, realized_pnl, settled_pnl, opened_at, updated_at, "
        "closed_at, ledger_net, avg_cost) "
        "VALUES ($1, $2, $3, $4, now() - $5::interval, now() - $6::interval, "
        "CASE WHEN $2 = 'closed' THEN now() - $7::interval END, $8, $9)",
        bid, state, realized, settled, timedelta(hours=opened_h), timedelta(hours=updated_h),
        timedelta(hours=closed_h), ledger_net, avg_cost)


async def _seed(c):
    """Tonight's four beside an open book (realized -100), a cashed-out
    close (realized -50, settled NULL) and a settlement 25 h old
    (realized -999, settled -1500: out of the window on both counts)."""
    for bid, realized, settled in TONIGHT:
        await _book(c, bid, "closed", realized, settled, closed_h=2.0)
    await _book(c, 30, "live", -100.0, ledger_net=300, avg_cost=0.31)
    await _book(c, 31, "closed", -50.0, None, closed_h=0.5)
    await _book(c, 32, "closed", -999.0, -1500.0, closed_h=25.0, opened_h=30.0)


def test_the_loss_sum_executes_and_counts_a_settled_books_dollars_once():
    async def run():
        admin, c, name = await _scratch()
        try:
            await _seed(c)
            row = await c.fetchrow(ml._SQL_LOSS_SUM)
            # settled over the four, realized over the open book and the
            # cashed-out close, nothing from the 25 h settlement
            assert row["lost"] == pytest.approx(SETTLED_TONIGHT - 100.0 - 50.0)
            assert row["lost"] == pytest.approx(-330.95)
            assert row["books"] == 6, "the count is every book updated in 24 h, as before"
            # the shape the reconciliation found would have read the four
            # books' realized part on top -- exactly the -229.74 excess
            assert row["lost"] - REALIZED_TONIGHT == pytest.approx(-101.21)
            # the settled figure alone, for a book whose close is in the
            # window, whatever its realized part: no realized leaks in
            await c.execute("UPDATE mirror_books SET realized_pnl = -1e6 WHERE id = 16")
            assert (await c.fetchval(ml._SQL_LOSS_SUM)) == pytest.approx(-330.95)
            # a closed-settled row with NO closed_at -- only a hand-edited
            # row reads so; both close statements stamp it -- is clocked
            # by updated_at and counted once, by its settled figure; it
            # vanished from both sums before (E3 review, minor 2)
            await _book(c, 33, "closed", -7.0, -20.0, closed_h=0.25)
            await c.execute("UPDATE mirror_books SET closed_at = NULL WHERE id = 33")
            row = await c.fetchrow(ml._SQL_LOSS_SUM)
            assert row["lost"] == pytest.approx(-350.95) and row["books"] == 7
            await c.execute("UPDATE mirror_books SET realized_pnl = -1e6 WHERE id = 33")
            assert (await c.fetchval(ml._SQL_LOSS_SUM)) == pytest.approx(-350.95)
            await c.execute("UPDATE mirror_books SET updated_at = now() - interval '25 hours' WHERE id = 33")
            assert (await c.fetchval(ml._SQL_LOSS_SUM)) == pytest.approx(-330.95)
            # an empty table reads 0.0 and 0, the worker's `or 0.0` path
            await c.execute("DELETE FROM mirror_books")
            empty = await c.fetchrow(ml._SQL_LOSS_SUM)
            assert (empty["lost"], empty["books"]) == (0.0, 0)
        finally:
            await _drop(admin, c, name)
    asyncio.run(run())


def _mirror_pnl_statements() -> list[str]:
    m = re.search(r'mirror-pnl\) SQL="(.*?)"; TO=', RENDER_OPS.read_text())
    assert m, "the mirror-pnl preset is not where render-ops.yml kept it"
    return [s.strip() for s in m.group(1).split(";") if s.strip()]


def test_the_mirror_pnl_presets_totals_row_executes_and_day_pnl_is_the_stops_rule():
    async def run():
        admin, c, name = await _scratch()
        try:
            await _seed(c)
            # the preset reads UTC DAY boundaries, not a trailing window:
            # a row opened three hours ago is yesterday's just after
            # midnight, so the day's rows are clocked no earlier than
            # today's midnight (the 25 h settlement stays yesterday's)
            await c.execute(
                "UPDATE mirror_books SET opened_at = greatest(opened_at, date_trunc('day', now())), "
                "closed_at = CASE WHEN closed_at IS NULL THEN NULL "
                "ELSE greatest(closed_at, date_trunc('day', now())) END WHERE id <> 32")
            await c.execute("INSERT INTO mirror_orders (side, filled, cash_usd, realized) "
                            "VALUES ('BUY_LONG', 300, 93.0, 0), ('SELL_LONG', 100, 35.0, 4.0)")
            stmts = _mirror_pnl_statements()
            assert len(stmts) == 3, stmts
            per_book, totals, fills = [await c.fetch(s) for s in stmts]
            # every statement prepares and runs; the per-book rows are
            # the day's (the 25 h settlement opened 30 h ago is not)
            assert {r["id"] for r in per_book} == {16, 3, 19, 22, 30, 31}
            t = totals[0]
            assert t["what"] == "today" and t["books"] == 6
            assert float(t["realized"]) == pytest.approx(REALIZED_TONIGHT - 150.0, abs=0.006)
            assert float(t["settled"]) == pytest.approx(SETTLED_TONIGHT, abs=0.006)
            # the one true number: settled over the closed-settled books,
            # realized over the others -- the loss sum's figure
            assert float(t["day_pnl"]) == pytest.approx(-330.95, abs=0.006)
            assert float(t["day_pnl"]) != pytest.approx(float(t["realized"]) + float(t["settled"]))
            assert fills[0]["what"] == "fills today" and fills[0]["n"] == 2
        finally:
            await _drop(admin, c, name)
    asyncio.run(run())
