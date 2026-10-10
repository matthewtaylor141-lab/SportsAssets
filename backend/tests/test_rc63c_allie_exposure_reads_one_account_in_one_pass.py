"""RC6.3c allie-exposure scale: ALLIE'S OPEN EXPOSURE IS READ FOR ONE ACCOUNT,
IN ONE PASS, AND IS THE SAME NUMBER AS BEFORE.

THE COST (independent review of 5979416f, lifecycle-proof measurement).
canonical_components.allie_at_decision runs the open-exposure statements once
for the fixture and once for the book, uncached, on EVERY decision, under
Allie's 2.0 s component timeout. Each statement aggregated the WHOLE
paper_fills table twice (the canonical open-quantity subquery and the
average-cost subquery) and the WHOLE paper_settlements table (DISTINCT ON over
every settlement of every account), and only then filtered to the account in
the outermost WHERE: 0.45 s per call at 120,000 fills, growing with every
account's history, not the deciding account's.

THE FIX (open_position_canon): the account filter sits INSIDE the two scans
(paper_fills by account_id; paper_settlements by the account's position-key
prefix), the two aggregates over paper_fills are one, and the fixture and the
book come from ONE statement (OPEN_EXPOSURE_BOOK_AND_FIXTURE_SQL), so a
decision reads the ledger once.

WHAT THIS FILE PROVES, on a real Postgres, in a transaction it rolls back:
  1. the canonical open-position rule every other reader uses is byte for
     byte what it was (it is one template now, expanded without a scope);
  2. over a ledger seeded across accounts -- open, exited, partly exited,
     settled, re-settled (a later version), short-side, multi-fill average
     cost, SELL-only, an unrelated settlement, and two accounts whose
     position keys COLLIDE ('x' + group 'y:g' and 'x:y' + group 'g') -- the
     new rows, book and fixture figures are EQUAL, as exact numerics, to the
     previous statements (verbatim below), for every account and for none;
  3. the same over a seeded random ledger of several hundred groups;
  4. the scans are scoped (EXPLAIN ANALYZE: one scan of each table, each
     with the account in its own filter) and allie_at_decision executes ONE
     exposure statement per decision where it executed two.
"""
from __future__ import annotations

import json
import random
import time
import uuid

import pytest

from sportsassets import bettor_paper_ledger as L
from sportsassets import canonical_components as CC
from sportsassets import open_position_canon as OPC
from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")

#: THE STATEMENTS THIS CHANGE REPLACED, verbatim from f971d665
#: (open_position_canon), so the equivalence is against what ran, not against
#: a restatement of it.
LEGACY_CANONICAL_OPEN_POSITIONS_SQL = """
    SELECT f.account_id, f.group_id, f.us_market_slug, f.holding_side,
           f.bought - f.sold - coalesce(s.qty, 0) AS open_qty
      FROM (SELECT account_id, group_id, us_market_slug, holding_side,
                   coalesce(sum(qty) FILTER (WHERE direction='BUY'), 0)
                       AS bought,
                   coalesce(sum(qty) FILTER (WHERE direction='SELL'), 0)
                       AS sold
              FROM paper_fills
             GROUP BY account_id, group_id, us_market_slug, holding_side) f
      LEFT JOIN (SELECT DISTINCT ON (position_key) position_key, qty
                   FROM paper_settlements
                  ORDER BY position_key, version DESC) s
        ON s.position_key = 'paperpos:' || f.account_id || ':' || f.group_id
                            || ':' || f.us_market_slug || ':'
                            || f.holding_side
     WHERE f.bought - f.sold - coalesce(s.qty, 0) > 1e-9
"""

LEGACY_ROWS_SQL = """
    SELECT c.account_id, c.group_id, c.us_market_slug, c.holding_side,
           b.fixture,
           c.open_qty * (b.buy_cost / nullif(b.bought, 0)) AS exposure_usd
      FROM (""" + LEGACY_CANONICAL_OPEN_POSITIONS_SQL + """) c
      JOIN (SELECT account_id, group_id, us_market_slug, holding_side,
                   max(fixture) AS fixture,
                   sum(qty) FILTER (WHERE direction = 'BUY') AS bought,
                   sum(gross_usd + fee_usd) FILTER (WHERE direction = 'BUY')
                       AS buy_cost
              FROM paper_fills
             GROUP BY account_id, group_id, us_market_slug, holding_side) b
        ON b.account_id = c.account_id AND b.group_id = c.group_id
       AND b.us_market_slug = c.us_market_slug
       AND b.holding_side = c.holding_side
     WHERE ($1::text IS NULL OR c.account_id = $1::text)
"""

LEGACY_FIXTURE_SQL = (
    "SELECT count(DISTINCT e.group_id) AS n, "
    "coalesce(sum(e.exposure_usd), 0) AS usd FROM ("
    + LEGACY_ROWS_SQL + ") e WHERE e.fixture = $2")

LEGACY_BOOK_SQL = (
    "SELECT coalesce(sum(e.exposure_usd), 0) AS usd FROM ("
    + LEGACY_ROWS_SQL + ") e")

FIXTURES = ("eqfx-1", "eqfx-2", "eqfx-3")


async def _tx():
    """A connection inside a transaction this test rolls back, with the
    ledger's FK and append-only triggers out of the way for the SEEDING only
    (the statements under test read; they never write)."""
    conn = await H.connect()
    tx = conn.transaction()
    await tx.start()
    await conn.execute("SET LOCAL session_replication_role = replica")
    return conn, tx


def _key(r):
    return (r["account_id"], r["group_id"], r["us_market_slug"],
            r["holding_side"])


class Ledger:
    """Seeds paper_fills / paper_settlements rows directly."""

    FILL_SQL = (
        "INSERT INTO paper_fills (fill_id, idempotency_key, order_id, "
        " account_id, session_id, group_id, role, direction, holding_side, "
        " us_market_slug, fixture, qty, price, wire_price, fee_usd, "
        " gross_usd, filled_at, basis, event_source, simulator_version, "
        " strategy) VALUES ($1,$1,$2,$3,'eq-session',$4,$5,$6,$7,$8,$9,$10,"
        " $11,$11,$12,$13,now(),'DEPTH_WALK_WITHIN_LIMIT','SIMULATOR','TEST',"
        " 'PINNACLE_COMPLETED_GAME_PAPER')")
    SETTLE_SQL = (
        "INSERT INTO paper_settlements (settlement_id, account_id, "
        " position_key, settlement_event_key, version, group_id, "
        " us_market_slug, holding_side, qty, outcome, payout_per_contract, "
        " payout_usd, evidence, evidence_source, settled_at) VALUES "
        " ($1,$2,$3,'eq-final',$4,$5,$6,$7,$8,'WON',1,$8,'{}'::jsonb,"
        " 'TEST_FIXTURE',now())")

    def __init__(self, conn):
        self.c = conn
        self.fills = []
        self.settlements = []

    def fill(self, acct, group, slug, side, direction, qty, price, fee=0.0,
             fixture=FIXTURES[0], role=None):
        n = len(self.fills)
        self.fills.append((
            "paperfill:eq%s%d" % (uuid.uuid4().hex[:10], n),
            "paperord:eq%s" % uuid.uuid4().hex[:10], acct, group,
            role or ("ENTRY" if direction == "BUY" else "EXIT"),
            direction, side, slug, fixture, round(float(qty), 6),
            round(float(price), 6), round(float(fee), 6),
            round(float(qty) * float(price), 6)))

    def settle(self, acct, group, slug, side, version, qty, key_acct=None):
        """A settlement row for the position key of (key_acct or acct)."""
        pk = L.position_key(account_id=key_acct or acct, group_id=group,
                            slug=slug, holding_side=side)
        self.settlements.append((
            "paperset:eq%s" % uuid.uuid4().hex[:12], acct, pk, int(version),
            group, slug, side, round(float(qty), 6)))

    async def write(self):
        await self.c.executemany(self.FILL_SQL, self.fills)
        await self.c.executemany(self.SETTLE_SQL, self.settlements)


def _seed_named(led, A, B, C, X, Y):
    """The groups the owner's cases name, across accounts."""
    # A: open; exited; partly exited; settled
    led.fill(A, "g-open", "eq-m1", "LONG", "BUY", 100, 0.40, 1.00, "eqfx-1")
    led.fill(A, "g-exit", "eq-m2", "LONG", "BUY", 100, 0.50, 0.50, "eqfx-1")
    led.fill(A, "g-exit", "eq-m2", "LONG", "SELL", 100, 0.60, 0.50, "eqfx-1")
    led.fill(A, "g-half", "eq-m3", "LONG", "BUY", 100, 0.50, 0.25, "eqfx-1")
    led.fill(A, "g-half", "eq-m3", "LONG", "SELL", 30, 0.55, 0.10, "eqfx-1")
    led.fill(A, "g-done", "eq-m4", "LONG", "BUY", 60, 0.30, 0.10, "eqfx-2")
    led.settle(A, "g-done", "eq-m4", "LONG", 1, 60)
    # B: re-settled (the LATEST version's qty rules), short side, two groups
    # on one fixture
    led.fill(B, "g-re", "eq-m5", "LONG", "BUY", 80, 0.45, 0.20, "eqfx-1")
    led.settle(B, "g-re", "eq-m5", "LONG", 1, 80)
    led.settle(B, "g-re", "eq-m5", "LONG", 2, 30)       # open 50 again
    led.fill(B, "g-short", "eq-m6", "SHORT", "BUY", 40, 0.35, 0.05, "eqfx-2")
    led.fill(B, "g-two-a", "eq-m7", "LONG", "BUY", 10, 0.20, 0.0, "eqfx-3")
    led.fill(B, "g-two-b", "eq-m8", "LONG", "BUY", 10, 0.30, 0.0, "eqfx-3")
    # C: a multi-fill average cost, a SELL-only group (never open), a
    # settlement with no fills at all
    led.fill(C, "g-avg", "eq-m9", "LONG", "BUY", 10, 0.30, 0.10, "eqfx-1")
    led.fill(C, "g-avg", "eq-m9", "LONG", "BUY", 20, 0.50, 0.20, "eqfx-1")
    led.fill(C, "g-avg", "eq-m9", "LONG", "BUY", 5, 0.90, 0.0, "eqfx-1")
    led.fill(C, "g-avg", "eq-m9", "LONG", "SELL", 7, 0.60, 0.05, "eqfx-1")
    led.fill(C, "g-sellonly", "eq-m10", "LONG", "SELL", 5, 0.50, 0.0,
             "eqfx-1")
    led.settle(C, "g-orphan", "eq-m11", "LONG", 1, 9)
    # THE KEY COLLISION: account X with group "y:g1" and account "X:y" with
    # group "g1" build the SAME position key, so one settlement row closes
    # (or part-closes) both. The previous statement joined on the key string
    # alone; scoping the settlements by the ACCOUNT COLUMN would have changed
    # the answer for one of the two, scoping by the key's prefix cannot.
    led.fill(X, "y:g1", "eq-mc", "LONG", "BUY", 10, 0.40, 0.0, "eqfx-2")
    led.fill(Y, "g1", "eq-mc", "LONG", "BUY", 25, 0.40, 0.0, "eqfx-2")
    led.settle(X, "y:g1", "eq-mc", "LONG", 1, 10)       # written for X's key


def _seed_random(led, rng, accounts, n_groups):
    for g in range(n_groups):
        acct = rng.choice(accounts)
        grp = "rg-%d" % g
        slug = "eq-r%d" % rng.randrange(40)
        side = rng.choice(("LONG", "LONG", "SHORT"))
        fx = rng.choice(FIXTURES + (None,))
        bought = 0.0
        for _ in range(rng.randint(1, 4)):
            q = round(rng.uniform(0.5, 60), 6)
            bought += q
            led.fill(acct, grp, slug, side, "BUY", q,
                     round(rng.uniform(0.05, 0.95), 6),
                     round(rng.uniform(0, 1.5), 6), fx)
        sold = 0.0
        for _ in range(rng.choice((0, 0, 1, 2))):
            # a sale never exceeds what is held (the ledger refuses one)
            left = bought - sold
            if left < 0.2:
                break
            q = round(rng.uniform(0.1, left), 6)
            sold += q
            led.fill(acct, grp, slug, side, "SELL", q,
                     round(rng.uniform(0.05, 0.95), 6),
                     round(rng.uniform(0, 1.0), 6), fx)
        r = rng.random()
        if r < 0.35:                         # settled once, in full / part
            led.settle(acct, grp, slug, side, 1,
                       round(rng.choice((max(bought - sold, 0.0), bought,
                                         max(bought - sold, 0.0) / 2)), 6))
        elif r < 0.5:                        # re-settled to a later version
            led.settle(acct, grp, slug, side, 1, round(bought, 6))
            led.settle(acct, grp, slug, side, 2,
                       round(rng.uniform(0, bought), 6))


async def _ledger(seed_random=0):
    conn, tx = await _tx()
    tag = uuid.uuid4().hex[:8]
    A, B, C = ("eqx%s_%s" % (tag, s) for s in "abc")
    X, Y = "eqx%s_x" % tag, "eqx%s_x:y" % tag
    led = Ledger(conn)
    _seed_named(led, A, B, C, X, Y)
    if seed_random:
        _seed_random(led, random.Random(63), [A, B, C, X, Y], seed_random)
    await led.write()
    return conn, tx, (A, B, C, X, Y), led


async def _same_figures(conn, acct):
    """Every statement, previous and new, for one account (None: all)."""
    old = sorted(map(dict, await conn.fetch(LEGACY_ROWS_SQL, acct)), key=_key)
    new = sorted(map(dict, await conn.fetch(OPC.OPEN_EXPOSURE_ROWS_SQL, acct)),
                 key=_key)
    assert new == old, (acct, len(new), len(old))
    old_book = await conn.fetchval(LEGACY_BOOK_SQL, acct)
    assert await conn.fetchval(OPC.OPEN_EXPOSURE_BOOK_SQL, acct) == old_book
    for fx in FIXTURES + ("eqfx-none", None):
        o = await conn.fetchrow(LEGACY_FIXTURE_SQL, acct, fx)
        n = await conn.fetchrow(OPC.OPEN_EXPOSURE_FIXTURE_SQL, acct, fx)
        both = await conn.fetchrow(
            OPC.OPEN_EXPOSURE_BOOK_AND_FIXTURE_SQL, acct, fx)
        assert dict(n) == dict(o), (acct, fx)
        assert (both["book_usd"], both["fixture_n"], both["fixture_usd"]) == \
            (old_book, o["n"], o["usd"]), (acct, fx, dict(both), dict(o))
    return old, old_book


# ── 1 · the rule every other reader uses is what it was ─────────────────

def test_the_canonical_open_position_rule_is_byte_for_byte_what_it_was():
    assert OPC.CANONICAL_OPEN_POSITIONS_SQL == \
        LEGACY_CANONICAL_OPEN_POSITIONS_SQL
    # and the unparameterised template still carries the ONE epsilon
    assert "> 1e-9" in OPC.CANONICAL_OPEN_POSITIONS_SQL
    assert OPC.OPEN_QTY_EPS == 1e-9


# ── 2 · the named cases, across accounts ────────────────────────────────

@pg
async def test_the_new_statements_equal_the_previous_ones_on_the_named_cases():
    conn, tx, accts, _ = await _ledger()
    try:
        A, B, C, X, Y = accts
        rows_by_acct = {}
        for acct in accts + (None,):
            rows, book = await _same_figures(conn, acct)
            rows_by_acct[acct] = {r["group_id"]: r for r in rows}
        a, b, c = (rows_by_acct[k] for k in (A, B, C))
        # the cases mean what they say: open and partly exited are in, the
        # exited, settled and SELL-only are out, the LATEST settlement
        # version rules, the short side is its own position
        assert sorted(a) == ["g-half", "g-open"]
        assert sorted(b) == ["g-re", "g-short", "g-two-a", "g-two-b"]
        assert sorted(c) == ["g-avg"]
        assert float(a["g-half"]["exposure_usd"]) == pytest.approx(
            70 * (50.0 + 0.25) / 100.0, rel=1e-9)     # 70 left at avg cost
        assert float(b["g-re"]["exposure_usd"]) == pytest.approx(
            50 * (36.0 + 0.20) / 80.0, rel=1e-9)      # v2: 30 settled
        assert b["g-short"]["holding_side"] == "SHORT"
        assert float(c["g-avg"]["exposure_usd"]) == pytest.approx(
            28 * ((3.0 + 0.10) + (10.0 + 0.20) + (4.5 + 0.0)) / 35.0,
            rel=1e-9)
        # the colliding keys: one settlement row, two positions
        assert "y:g1" not in rows_by_acct[X]          # closed by its row
        assert "g1" in rows_by_acct[Y]                # 25 - 10 still open
        assert float(rows_by_acct[Y]["g1"]["exposure_usd"]) == \
            pytest.approx(15 * 0.40, rel=1e-9)
    finally:
        await tx.rollback()
        await conn.close()


# ── 3 · a seeded random ledger ──────────────────────────────────────────

@pg
async def test_the_new_statements_equal_the_previous_ones_on_a_random_ledger():
    conn, tx, accts, led = await _ledger(seed_random=400)
    try:
        assert len(led.fills) > 800 and len(led.settlements) > 100
        seen_open = 0
        for acct in accts + (None,):
            rows, _ = await _same_figures(conn, acct)
            seen_open += len(rows)
        assert seen_open > 150      # the comparison is not vacuously empty
    finally:
        await tx.rollback()
        await conn.close()


# ── 4 · the scans are scoped, once; one statement per decision ──────────

def _scans(plan, out=None):
    out = [] if out is None else out
    if "Relation Name" in plan:
        out.append(plan)
    for child in plan.get("Plans") or []:
        _scans(child, out)
    return out


@pg
async def test_each_table_is_scanned_once_and_the_account_is_in_the_scans_filter():
    conn, tx, (A, B, C, X, Y), _ = await _ledger(seed_random=200)
    try:
        for stmt, args in (
                (OPC.OPEN_EXPOSURE_BOOK_AND_FIXTURE_SQL, (A, "eqfx-1")),
                (OPC.OPEN_EXPOSURE_BOOK_SQL, (A,)),
                (OPC.OPEN_EXPOSURE_FIXTURE_SQL, (A, "eqfx-1"))):
            raw = await conn.fetchval(
                "EXPLAIN (ANALYZE, FORMAT JSON) " + stmt, *args)
            plan = (json.loads(raw) if isinstance(raw, str) else raw)[0][
                "Plan"]
            scans = _scans(plan)
            fills = [s for s in scans if s["Relation Name"] == "paper_fills"]
            setts = [s for s in scans
                     if s["Relation Name"] == "paper_settlements"]
            # ONE pass over each table (it was two over paper_fills)
            assert len(fills) == 1, [s["Relation Name"] for s in scans]
            assert len(setts) == 1
            # the account is in the scan's own filter, so what the
            # aggregate above it sees is this account's rows, not the table
            assert "account_id" in (fills[0].get("Filter") or ""), fills[0]
            assert "position_key" in (setts[0].get("Filter") or ""), setts[0]
            assert fills[0]["Rows Removed by Filter"] > 0
    finally:
        await tx.rollback()
        await conn.close()


class _Spy:
    """A connection that records every statement it is asked to run."""

    def __init__(self, conn):
        self._c = conn
        self.sql = []

    def __getattr__(self, name):
        attr = getattr(self._c, name)
        if name not in ("fetch", "fetchrow", "fetchval", "execute"):
            return attr

        async def run(sql, *a, **k):
            self.sql.append(sql)
            return await attr(sql, *a, **k)
        return run


@pg
async def test_allie_at_the_decision_runs_one_exposure_statement_not_two(
        monkeypatch):
    from sportsassets import allie_capital as AC
    conn, tx, (A, B, C, X, Y), _ = await _ledger()
    CC.reset_cache()
    # THE INPUTS ALLIE IS HANDED, exactly (her module rounds what it reports
    # to cents; the equivalence is of the inputs, not of her rounding)
    handed = []
    real_allocate = AC.allocate

    def allocate(**kw):
        handed.append({k: kw[k] for k in ("fixture_open_groups",
                                          "fixture_open_usd",
                                          "book_open_usd")})
        return real_allocate(**kw)
    monkeypatch.setattr(AC, "allocate", allocate)
    try:
        T = time.time()
        dec = {"decision_id": "paperrc63c:%s" % uuid.uuid4().hex[:8],
               "decided_at": T, "fixture": "eqfx-1",
               "strategy": "PINNACLE_ONLY_PAPER_BENCHMARK",
               "us_market_slug": "eq-m1", "holding_side": "LONG",
               "proposed_qty": 100, "limit_price": 0.52,
               "capital_required_usd": 52.5,
               "executable_opportunity_dollars": 5.0,
               "event_start_at": T + 3600, "per_order_cap_usd": 5000}
        spy = _Spy(conn)
        allie = await CC.allie_at_decision(
            spy, decision=dec, eddie={"status": "UNAVAILABLE"}, now=T,
            account_id=A)
        exposure = [s for s in spy.sql if "paper_fills" in s]
        assert exposure == [OPC.OPEN_EXPOSURE_BOOK_AND_FIXTURE_SQL], \
            [s[:80] for s in spy.sql]
        # the figures Allie was given ARE the previous statements' figures
        o = await conn.fetchrow(LEGACY_FIXTURE_SQL, A, "eqfx-1")
        book = await conn.fetchval(LEGACY_BOOK_SQL, A)
        assert o["n"] == 2 and float(o["usd"]) > 0
        assert handed == [{"fixture_open_groups": 2,
                           "fixture_open_usd": float(o["usd"]),
                           "book_open_usd": float(book)}]
        cc = allie["correlation_concentration"]
        assert cc["fixture_open_groups"] == 2
        assert allie["open_exposure_basis"]["account_scope"] == A
        # and on an account whose book is NOT its fixture (B holds four
        # groups on three fixtures): the two figures differ and both match
        CC.reset_cache()
        await CC.allie_at_decision(
            _Spy(conn), decision=dict(dec, fixture="eqfx-3"),
            eddie={"status": "UNAVAILABLE"}, now=T, account_id=B)
        ob = await conn.fetchrow(LEGACY_FIXTURE_SQL, B, "eqfx-3")
        book_b = await conn.fetchval(LEGACY_BOOK_SQL, B)
        assert ob["n"] == 2 and 0 < float(ob["usd"]) < float(book_b)
        assert handed[1] == {"fixture_open_groups": 2,
                             "fixture_open_usd": float(ob["usd"]),
                             "book_open_usd": float(book_b)}
        # no fixture on the decision: the book is read, the fixture is not
        spy2 = _Spy(conn)
        CC.reset_cache()
        allie2 = await CC.allie_at_decision(
            spy2, decision=dict(dec, fixture=None),
            eddie={"status": "UNAVAILABLE"}, now=T, account_id=A)
        assert [s for s in spy2.sql if "paper_fills" in s] == \
            [OPC.OPEN_EXPOSURE_BOOK_SQL]
        assert handed[2] == {"fixture_open_groups": 0,
                             "fixture_open_usd": 0,
                             "book_open_usd": float(book)}
        assert allie2["correlation_concentration"]["haircut_status"] == \
            "UNMEASURED"
    finally:
        await tx.rollback()
        await conn.close()
