"""THE HISTORICAL R30 DECISION REPLAY (specs/HISTORICAL_REPLAY.md): only the
evidence recorded by each replay clock; REPLAY_NOT_FORWARD_EVIDENCE; no
authority.

  §1 THE CHOKE POINT (pit.Reader). A deliberately FUTURE-DATED row (recorded
     after the clock, claiming an earlier observation) is invisible at the
     clock and visible after it; a row forced past the filter raises
     HindsightViolation; unregistered tables, hidden columns (the order
     queue the simulator rewrites in place, Karen's caller-stamped columns,
     the valuation outcome), filters or orders on not-yet-revealed columns,
     TABLE / VALUES / EXISTS subqueries, literals, foreign functions,
     positional ORDER BY and clocks past the horizon are refused; an order's
     terminal state is revealed only once its terminal_at AND its durable
     last write are <= the clock; Karen's state comes from her append-only
     events; a rewritten pre-map row is invisible; a paged snapshot answers
     exactly what a direct read answers, or says where it stops.
  §2 ONE CHOKE POINT, NO AUTHORITY (static + runtime). No replay module but
     pit.py and store.py holds a query or touches the connection; store.py
     writes r30_replay_* only; the endpoint module holds no SQL; the replay
     imports no order / venue / execution / funded module, statically or at
     runtime.
  §3 THE END-TO-END REPLAY on a synthetic book (plus: missing economics,
     a truncated tape, an unseen order form and a sold-out unsettled
     position each read UNAVAILABLE or their measured basis, never a zero): every component at the
     decision clock (provider observation, receipt, mapping, valuation,
     venue book, Derek, Karen, Eddie, Allie, opportunity score, canonical
     intent and its parity with the intent recorded at the time), the PAPER
     execution, Xavier's management actual vs canonical, the settlement /
     release timeline, every counterfactual, per INDEPENDENT event the
     decision / sizing / execution / management deltas, capital-hours,
     marked drawdown, opportunity cost, lost-opportunity classes and the
     Alpha Attribution V2 identity; UNAVAILABLE with reasons where an input
     was never recorded; the anti-hindsight audit finds no violation.
  §4 PERSISTENCE (migration 237): one transaction, append-only, every
     anti-hindsight / label / authority CHECK enforced by the database; the
     rollback applies and refuses while a run exists.
  §5 THE ENDPOINT: GET only, command auth, READ ONLY transaction, bounded,
     labelled; EMPTY with a reason when no run exists.
"""
from __future__ import annotations

import ast
import json
import pathlib
import subprocess
import sys
from contextlib import asynccontextmanager

import asyncpg
import pytest

from sportsassets.replay import LABEL
from sportsassets.replay import pit as P
from sportsassets.replay import runner as RN
from sportsassets.replay import store as ST

try:
    from tests import paper_harness as H
    from tests import replay_fixture as F
except ImportError:                                             # pragma: no cover
    import paper_harness as H
    import replay_fixture as F

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "sportsassets"
REPLAY = sorted((PKG / "replay").glob("*.py"))
MIG = ROOT / "migrations"
UP = (MIG / "237_r30_replay.sql").read_text()
DOWN = (MIG / "rollback" / "237_r30_replay.down.sql").read_text()


async def _tx():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    return conn, tx


async def _expect(conn, exc, sql, *args):
    sp = conn.transaction()
    await sp.start()
    try:
        with pytest.raises(exc):
            await conn.execute(sql, *args)
    finally:
        await sp.rollback()


# ═════════════════════════════════════════════════════════════════════
# §1 THE CHOKE POINT
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_a_future_dated_row_is_invisible_at_the_clock():
    conn, tx = await _tx()
    try:
        acct, B = await F.account(conn)
        slug = F.uid("pit-")
        early = await F.book(conn, slug, observed_at=B - 5, recorded_at=B - 5,
                             bids=((0.5, 10),), offers=((0.52, 10),))
        # THE DELIBERATELY FUTURE-DATED ROW: observed "before" the clock but
        # recorded after it
        late = await F.book(conn, slug, observed_at=B - 1, recorded_at=B + 1,
                            bids=((0.1, 1),), offers=((0.2, 1),))
        R = P.Reader(conn, horizon=B + 10)
        cols = ("obs_id", "observed_at")
        where = "us_market_slug = $1 AND observed_at <= to_timestamp($2)"
        at = await R.rows("paper_book_observations", clock=B, cols=cols,
                          where=where, args=(slug, B),
                          order="observed_at DESC", limit=5, scope="s")
        assert [r["obs_id"] for r in at] == [early]
        assert R.scope("s")["max_recorded_at"] <= B
        after = await R.rows("paper_book_observations", clock=B + 2,
                             cols=cols, where=where, args=(slug, B),
                             order="observed_at DESC", limit=5)
        assert [r["obs_id"] for r in after] == [late, early]
        # a snapshot read once answers each clock exactly as a direct read
        snap = await R.snapshot("paper_book_observations", until=B + 10,
                                cols=cols, where="us_market_slug = $1",
                                args=(slug,), id_col="obs_id")
        assert [r["obs_id"] for r in snap.at(B)] == [early]
        assert sorted(r["obs_id"] for r in snap.at(B + 2)) == sorted(
            [early, late])
        with pytest.raises(P.HindsightViolation):
            snap.at(B + 11)                       # past what it read
        with pytest.raises(P.HindsightViolation):
            await R.rows("paper_book_observations", clock=B + 11, cols=cols,
                         limit=1)                 # past the horizon
    finally:
        await tx.rollback()
        await conn.close()


class _FakeConn:
    """Returns a row recorded after the clock regardless of the filter: the
    Python post-check must stop it."""

    def __init__(self, rec):
        self.rec = rec

    async def fetch(self, sql, *args):
        assert "<= to_timestamp(" in sql and " JOIN " not in sql.upper()
        return [{"obs_id": 1, "__recorded": self.rec}]


async def test_a_row_forced_past_the_filter_raises_hindsight_violation():
    R = P.Reader(_FakeConn(1000.5), horizon=2000)
    with pytest.raises(P.HindsightViolation):
        await R.rows("paper_book_observations", clock=1000.0,
                     cols=("obs_id",), limit=1)
    ok = await P.Reader(_FakeConn(999.0), horizon=2000).rows(
        "paper_book_observations", clock=1000.0, cols=("obs_id",), limit=1)
    assert ok[0]["__recorded_at"] == 999.0


async def test_the_reader_refuses_every_way_around_the_clock():
    R = P.Reader(_FakeConn(1.0), horizon=2000)
    bad = [
        dict(table="paper_ledger_shadow", cols=("x",)),          # unregistered
        dict(table="paper_orders", cols=("updated_at",)),        # hidden
        # rewritten in place by the simulator with no stamp
        dict(table="paper_orders", cols=("queue_ahead_qty",)),
        dict(table="paper_orders", cols=("queue_basis",)),
        dict(table="karen_challenges", cols=("state",)),         # hidden
        # caller-supplied stamps: hidden, the state comes from the events
        dict(table="karen_challenges", cols=("responded_at",)),
        dict(table="karen_challenges", cols=("resolution_evidence_refs",)),
        # the venue settlement time, not the write: hidden
        dict(table="external_valuations", cols=("outcome_at",)),
        dict(table="external_valuations", cols=("settlement_read",)),
        dict(table="paper_orders", cols=("order_id",),
             where="state = $1"),                                # not revealed
        dict(table="external_valuations", cols=("id",),
             where="outcome_known"),                             # outcome
        dict(table="paper_fills", cols=("fill_id",),
             where="order_id IN (SELECT order_id FROM paper_orders)"),
        # red-team probe: a TABLE subquery has no SELECT / FROM keyword
        dict(table="paper_decisions", cols=("decision_id",),
             where="decision_id = $1 AND EXISTS (TABLE paper_fills)"),
        dict(table="paper_decisions", cols=("decision_id",),
             where="decision_id = ANY(VALUES ($1))"),
        dict(table="paper_decisions", cols=("decision_id",),
             where="decision_id = ANY(TABLE paper_fills)"),
        dict(table="paper_fills", cols=("fill_id",),
             where="TRUE; DELETE FROM paper_fills"),
        dict(table="paper_fills", cols=("fill_id",),
             where="role = 'ENTRY'"),                            # a literal
        dict(table="paper_fills", cols=("fill_id",),
             where="query_to_xml($1, true, true, $2) IS NOT NULL"),
        dict(table="paper_fills", cols=("fill_id",),
             where="fill_id = $1 -- comment"),
        dict(table="paper_fills", cols=("fill_id, 1",)),         # expression
        dict(table="paper_fills", cols=("fill_id",), limit=P.MAX_LIMIT + 1),
        # red-team probe: a positional ORDER BY orders on a masked column
        dict(table="paper_orders", cols=("order_id", "state"),
             order="2 ASC"),
        dict(table="paper_orders", cols=("order_id",), order="state DESC"),
        dict(table="paper_orders", cols=("order_id",),
             order="created_at DESC, updated_at"),
        dict(table="paper_fills", cols=("fill_id",),
             order="(SELECT 1)"),
    ]
    for b in bad:
        with pytest.raises(P.HindsightViolation):
            await R.rows(b["table"], clock=1000.0, cols=b["cols"],
                         where=b.get("where", ""), order=b.get("order", ""),
                         limit=b.get("limit", 10))
    # what the replay itself writes is accepted
    ok = [("paper_fills", "account_id = ANY($1::text[]) AND filled_at >= "
           "to_timestamp($2)", "filled_at DESC"),
          ("paper_ledger", "account_id = $1 AND (group_id IS NULL OR NOT "
           "(group_id = ANY($2::text[])))", "committed_at DESC, seq DESC"),
          ("us_premap", "market_slug = ANY($1::text[]) AND game_start IS NOT "
           "NULL", "updated_at")]
    for table, where, order in ok:
        await R.rows(table, clock=1000.0, cols=("account_id",)
                     if table != "us_premap" else ("market_slug",),
                     where=where, order=order, limit=1)


@pg
async def test_late_written_columns_are_revealed_only_at_their_own_stamps():
    from sportsassets.replay import reconstruct as RC
    conn, tx = await _tx()
    try:
        acct, B = await F.account(conn)
        slug = F.uid("pit-o-")
        d = await F.decision(conn, acct, slug=slug, at=B, recorded_at=B)
        await F.order(conn, acct, decision_id=d, group_id=F.uid("g-"),
                      slug=slug, qty=10.0, limit=0.5, at=B,
                      terminal_at=B + 50)
        R = P.Reader(conn, horizon=B + 1000)
        cols = ("order_id", "state", "filled_qty", "terminal_at")
        o1 = (await R.rows("paper_orders", clock=B + 10, cols=cols,
                           where="decision_id = $1", args=(d,), limit=1))[0]
        assert o1["state"] is None and o1["filled_qty"] is None
        assert o1["state_at_clock"] == "OPEN_AT_THE_CLOCK"
        assert set(o1["__masked"]) >= {"state", "filled_qty", "terminal_at"}
        o2 = (await R.rows("paper_orders", clock=B + 60, cols=cols,
                           where="decision_id = $1", args=(d,), limit=1))[0]
        assert o2["state_at_clock"] == "FILLED" and o2["filled_qty"] == 10.0
        # the internal confirm column never leaks to the caller
        assert not any(k.startswith("__confirm") for k in o2)
        # THE LOGICAL TIME IS NOT THE COMMIT (red-team finding): terminal_at
        # is the simulator's `now`; the terminal UPDATE's durable stamp is
        # updated_at. Between the two the order is still OPEN at the clock
        d2 = await F.decision(conn, acct, slug=slug, at=B, recorded_at=B)
        await F.order(conn, acct, decision_id=d2, group_id=F.uid("g-"),
                      slug=slug, qty=10.0, limit=0.5, at=B,
                      terminal_at=B + 50, updated_at=B + 80)

        async def ostate(c):
            got = await R.rows("paper_orders", clock=c, cols=cols,
                               where="decision_id = $1", args=(d2,), limit=1)
            return got[0]["state_at_clock"]
        assert [await ostate(c) for c in (B + 40, B + 60, B + 90)] == [
            "OPEN_AT_THE_CLOCK", "OPEN_AT_THE_CLOCK", "FILLED"]
        # Karen: the challenge's response / resolution columns carry
        # caller-supplied stamps (hidden); her state AT THE CLOCK is rebuilt
        # from karen_challenge_events, whose recorded_at is the database's
        cid = await F.karen(conn, target_id=d, challenged_at=B + 1,
                            created_at=B + 1)
        await conn.execute(
            "UPDATE karen_challenges SET state='RESPONDED', "
            " responded_by='DEREK', responded_at=$2, "
            " response_stance='CONCEDE', response='conceded' "
            " WHERE challenge_id=$1", cid, F.ts(B + 100))
        await conn.execute(
            "UPDATE karen_challenges SET state='UPHELD', outcome='UPHELD', "
            " resolved_by='owner-test', resolved_at=$2, "
            " outcome_reason='synthetic' WHERE challenge_id=$1", cid,
            F.ts(B + 200))
        # the RESPONDED event became durable at B+120 although its `at` is
        # B+100; the resolution at B+210
        await F.karen_event(conn, challenge_id=cid, kind="RESPONDED",
                            at=B + 100, recorded_at=B + 120)
        await F.karen_event(conn, challenge_id=cid, kind="UPHELD",
                            at=B + 200, recorded_at=B + 210)
        with pytest.raises(P.HindsightViolation):
            await R.rows("karen_challenges", clock=B + 300,
                         cols=("challenge_id", "responded_at"), limit=1)
        ctx = RC.RunContext(R, start=B - 10, end=B + 1000)
        await ctx.prepare([{"decided_at": B, "account_id":
                            acct["account_id"], "us_market_slug": slug}])
        dec = {"decision_id": "x", "us_market_slug": slug,
               "strategy": F.INV}

        async def kstate(c):
            k = await RC.karen_at(ctx, dec, c, "s")
            return k["state"], k.get("open_on_market")
        assert (await kstate(B + 110))[1] == 1     # the event not yet durable
        assert (await kstate(B + 150))[1] == 0     # responded: not open
        assert (await kstate(B + 250))[1] == 0
        assert (await kstate(B + 0.5))[0] == "UNAVAILABLE" or \
            ctx.karen_first_at <= B + 0.5          # before her first record
        # a pre-map row rewritten after the clock is invisible at it
        await F.premap(conn, slug, game_start=B + 3600, updated_at=B + 30)
        pm = ("market_slug", "game_start")
        assert await R.rows("us_premap", clock=B + 10, cols=pm,
                            where="market_slug = $1", args=(slug,),
                            limit=1) == []
        assert len(await R.rows("us_premap", clock=B + 40, cols=pm,
                                where="market_slug = $1", args=(slug,),
                                limit=1)) == 1
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_a_queue_rewritten_after_the_clock_is_never_read():
    """Red-team probe (leak_probe.py): paper_orders.queue_ahead_qty is
    rewritten in place by the simulator. It is hidden; Eddie's point-in-time
    history takes the queue at submission from the ACKNOWLEDGED event."""
    from sportsassets.replay import reconstruct as RC
    conn, tx = await _tx()
    try:
        acct, B = await F.account(conn)
        slug = F.uid("pit-q-")
        oid = await F.order(conn, acct, decision_id=None, group_id=F.uid("g-"),
                            slug=slug, qty=10.0, limit=0.5, at=B - 100,
                            state="RESTING", filled_qty=0.0,
                            queue_ahead_qty=100.0)
        await F.order_event(conn, order_id=oid, kind="ACKNOWLEDGED",
                            at=B - 100, recorded_at=B - 100,
                            detail={"resting": True, "queue_ahead_qty": 100})
        # the in-place rewrite stamped after the clock
        await conn.execute(
            "UPDATE paper_orders SET queue_ahead_qty=3, "
            " queue_basis='{\"later\": true}'::jsonb, updated_at=$2 "
            " WHERE order_id=$1", oid, F.ts(B + 500))
        R = P.Reader(conn, horizon=B + 1000)
        with pytest.raises(P.HindsightViolation):
            await R.rows("paper_orders", clock=B - 50,
                         cols=("order_id", "queue_ahead_qty"),
                         where="order_id = $1", args=(oid,), limit=1)
        ctx = RC.RunContext(R, start=B - 3600, end=B + 1000,
                            params={"eddie_history_bucket_s": 1.0})
        h = await ctx.eddie_history(B - 50, "s")
        assert h["queue"]["n"] >= 1
        assert "ACKNOWLEDGED" in h["basis"]
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_a_paged_snapshot_is_complete_or_says_where_it_stops(
        monkeypatch):
    conn, tx = await _tx()
    try:
        acct, B = await F.account(conn)
        slug = F.uid("pit-pg-")
        ids = []
        # ten rows, three sharing one recorded stamp (a page boundary tie)
        for rec in [1, 2, 3, 4, 4, 4, 5, 6, 7, 8]:
            ids.append(await F.book(conn, slug, observed_at=B + rec,
                                    recorded_at=B + rec,
                                    bids=((0.5, 1),), offers=((0.6, 1),)))
        monkeypatch.setattr(P, "MAX_LIMIT", 4)     # force several pages
        R = P.Reader(conn, horizon=B + 100)
        snap = await R.snapshot("paper_book_observations", until=B + 100,
                                cols=("obs_id",), where="us_market_slug = $1",
                                args=(slug,), id_col="obs_id")
        assert not snap.truncated and snap.covers(B + 100)
        assert sorted(r["obs_id"] for r in snap.at(B + 100)) == sorted(ids)
        assert len(snap.at(B + 4)) == 6            # the tie read once each
        # bounded: complete strictly before the last stamp read
        cut = await R.snapshot("paper_book_observations", until=B + 100,
                               cols=("obs_id",), where="us_market_slug = $1",
                               args=(slug,), id_col="obs_id", max_rows=5)
        assert cut.truncated and cut.covers(B + 3.5)
        assert not cut.covers(B + 4)
        assert len(cut.at(B + 3)) == 3
        with pytest.raises(P.SnapshotIncomplete):
            cut.at(B + 50)                         # never a short answer
    finally:
        await tx.rollback()
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# §2 ONE CHOKE POINT, NO AUTHORITY
# ═════════════════════════════════════════════════════════════════════

import re as _re

#: a query, not prose: SELECT ... FROM, INSERT INTO, UPDATE x SET, DELETE
#: FROM, JOIN x ON, TRUNCATE
SQL = _re.compile(r"(?is)\bSELECT\b.*\bFROM\b|\bINSERT\s+INTO\b|"
                  r"\bUPDATE\s+\w+\s+SET\b|\bDELETE\s+FROM\b|"
                  r"\bJOIN\s+\w+\b.*\bON\b|\bTRUNCATE\b")
CONN_CALLS = {"fetch", "fetchrow", "fetchval", "execute", "executemany",
              "copy_records_to_table", "cursor"}


def _strings(path):
    """Every string constant except docstrings (prose)."""
    tree = ast.parse(path.read_text())
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef,
                             ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(
                    getattr(body[0], "value", None), ast.Constant):
                docs.add(id(body[0].value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docs]


def _calls(path):
    return {n.func.attr for n in ast.walk(ast.parse(path.read_text()))
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}


def test_only_the_choke_point_and_the_store_touch_the_database():
    for path in REPLAY:
        if path.name in ("pit.py", "store.py"):
            continue
        for s in _strings(path):
            assert not SQL.search(s), (path.name, s[:80])
        assert not (_calls(path) & CONN_CALLS), (path.name,
                                                 _calls(path) & CONN_CALLS)
    # the choke point issues SELECTs only
    write = _re.compile(r"(?is)\bINSERT\s+INTO\b|\bUPDATE\s+\w+\s+SET\b|"
                        r"\bDELETE\s+FROM\b|\bTRUNCATE\b")
    for s in _strings(PKG / "replay" / "pit.py"):
        assert not write.search(s), s[:80]


def test_the_store_writes_only_the_replay_tables_and_the_route_holds_no_sql():
    import re
    writes = []
    for s in _strings(PKG / "replay" / "store.py"):
        for _kw, table in re.findall(r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM)"
                                    r"\s+([a-z0-9_]+)", s):
            writes.append(table)
        for table in re.findall(r"\bFROM\s+([a-z0-9_]+)", s):
            assert table.startswith("r30_replay_"), (table, s[:80])
    assert writes and all(t.startswith("r30_replay_") for t in writes)
    for s in _strings(PKG / "api" / "command_r30_replay.py"):
        assert not SQL.search(s), s[:80]


FORBIDDEN = ("execmirror", "kalshi", "pmus", "clob", "executor", "execution",
             "funded", "venue", "live_", "submit", "order_state", "smalllive",
             "paper_benchmark", "paper_xavier", "paper_derek",
             "bettor_paper_ledger", "bettor_paper_simulator")


def test_the_replay_imports_no_order_venue_execution_or_funded_module():
    # standard library + the driver (bisect: the paged snapshot's in-memory
    # clock filter)
    allowed_top = {"__future__", "datetime", "re", "dataclasses", "decimal",
                   "hashlib", "json", "math", "time", "argparse", "asyncio",
                   "os", "subprocess", "sys", "asyncpg", "fastapi", "bisect"}
    files = REPLAY + [PKG / "api" / "command_r30_replay.py",
                      PKG / "scripts" / "r30_replay.py"]
    for path in files:
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [("." * node.level) + (node.module or "")] + [
                    a.name for a in node.names]
            for n in names:
                leaf = n.rsplit(".", 1)[-1]
                assert not any(f in leaf for f in FORBIDDEN), (path.name, n)
                if not n.startswith(".") and "." not in n and \
                        n not in allowed_top and node.__class__ is ast.Import:
                    raise AssertionError((path.name, n))


@pg
def test_a_real_replay_loads_no_execution_venue_or_submission_module():
    """Red-team finding: the runtime check only IMPORTED modules. This runs a
    REAL replay of the synthetic scenario in a fresh interpreter (the
    scenario's account is funded by the ledger's initializer and its session
    row written directly, so no session / default-config import graph is
    loaded by the fixture) and lists sys.modules afterwards. Honest
    footprint: Eddie's pure estimator
    (agents/eddie, imported lazily by the replay) imports bettor_paper_ledger
    and bettor_paper_simulator, and its default fee function imports
    bettor_funded_book for the pure fee schedule (fee_for reads and writes no
    table). No execution / venue / live / submission module is loaded, and
    the replay itself imports none of those three (static test above)."""
    code = (
        "import asyncio, json, os, sys\n"
        "import asyncpg\n"
        "from tests import replay_fixture as F\n"
        "from sportsassets.replay import runner as RN\n"
        "async def main():\n"
        "    conn = await asyncpg.connect(os.environ['RN1X_TEST_DSN'])\n"
        "    tx = conn.transaction()\n"
        "    await tx.start()\n"
        "    try:\n"
        "        sc = await F.scenario(conn, raw=True)\n"
        "        b = await RN.run(conn, start=sc['B'] - 2000, end=sc['end'],\n"
        "                         account_id=sc['acct']['account_id'],\n"
        "                         now=sc['now'])\n"
        "        assert b['summary']['decisions'] == 5\n"
        "        assert b['summary']['anti_hindsight']['violations'] == 0\n"
        "    finally:\n"
        "        await tx.rollback()\n"
        "        await conn.close()\n"
        "asyncio.run(main())\n"
        "print(json.dumps(sorted(m for m in sys.modules\n"
        "                        if m.startswith('sportsassets'))))\n")
    got = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT),
                         capture_output=True, text=True, timeout=300)
    assert got.returncode == 0, got.stderr[-3000:]
    mods = json.loads(got.stdout.strip().splitlines()[-1])
    leaf = [m.rsplit(".", 1)[-1] for m in mods]
    forbidden = ("execmirror", "execution", "live_", "venue", "kalshi",
                 "pmus", "clob", "executor", "submit", "smalllive",
                 "paper_benchmark", "paper_xavier", "paper_derek")
    assert not [m for m in leaf if any(f in m for f in forbidden)], mods
    paper_or_funded = sorted(m for m in leaf if "funded" in m
                             or m.startswith("bettor_paper_ledger")
                             or m.startswith("bettor_paper_simulator"))
    assert paper_or_funded == ["bettor_funded_book", "bettor_paper_ledger",
                               "bettor_paper_simulator"], paper_or_funded


def test_importing_the_replay_loads_no_execution_module_at_runtime():
    code = (
        "import sys\n"
        "import sportsassets.replay.runner, sportsassets.replay.store\n"
        "import sportsassets.intel.attribution_v2\n"
        "import sportsassets.research_ref.marginal_capital_value\n"
        # what a run imports lazily (Eddie's pure estimator)
        "import sportsassets.agents.eddie\n"
        "bad = [m for m in sys.modules if m.startswith('sportsassets') and "
        "any(f in m.rsplit('.', 1)[-1] for f in %r)]\n"
        "print(bad)\n" % (("execmirror", "execution", "live_", "funded",
                           "venue", "kalshi", "pmus", "clob", "executor",
                           "submit", "live_parity"),))
    got = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT),
                         capture_output=True, text=True, timeout=120)
    assert got.returncode == 0, got.stderr[-2000:]
    assert got.stdout.strip() == "[]", got.stdout


# ═════════════════════════════════════════════════════════════════════
# §3 THE END-TO-END REPLAY
# ═════════════════════════════════════════════════════════════════════

def _item(built, did):
    return next(it for it in built["items"]
                if it["rec"]["decision"]["decision_id"] == did)


@pg
async def test_the_end_to_end_replay_uses_only_what_each_clock_knew():
    conn, tx = await _tx()
    try:
        sc = await F.scenario(conn)
        B = sc["B"]
        built = await RN.run(conn, start=B - 2000, end=sc["end"],
                             account_id=sc["acct"]["account_id"],
                             now=sc["now"])
        s = built["summary"]
        assert s["label"] == LABEL and s["decisions"] == 5
        assert s["promotion"].startswith("NONE")
        ah = s["anti_hindsight"]
        assert ah["violations"] == 0 and ah["decisions_checked"] == 5
        for it in built["items"]:
            ev = it["rec"]["evidence"]
            assert ev["decision"]["max_recorded_at"] is None or \
                ev["decision"]["max_recorded_at"] <= it["rec"]["clock"]
            assert ev["outcome"]["max_recorded_at"] <= sc["end"]

        # ── d1: the INVESTMENT position, component by component ──────
        d1 = _item(built, sc["d1"])
        c = d1["rec"]["components"]
        assert d1["rec"]["clock"] == pytest.approx(B + 0.2, abs=1e-3)
        po = c["provider_observation"]
        assert po["status"] == "MEASURED" and po["valuation_id"] == sc["v1"]
        assert c["bettor_receipt"]["receipt_latency_s"] == pytest.approx(3.0)
        assert c["mapping"]["event_start_at"] == pytest.approx(B + 3600)
        assert c["valuation"]["probability"] == pytest.approx(0.62)
        # the outcome columns' stamps are the venue settlement time, not the
        # write: hidden outright (red-team: never revealed at an event time)
        assert c["valuation"]["outcome_columns"].startswith("hidden")
        # the FUTURE-DATED book (recorded after the clock) is not used
        assert c["venue_book"]["obs_id"] == sc["b1"] != sc["b_future"]
        assert c["derek"]["verdict"] == "ENTER"
        # Karen: the challenge recorded before the clock is OPEN; the one
        # recorded after it does not exist yet
        assert c["karen"]["open_on_market"] == 1
        assert c["karen"]["open_total"] == 1
        assert c["eddie"]["status"] == "MEASURED"
        assert c["eddie"]["replay_basis"].startswith("RECOMPUTED")
        a = c["allie"]
        assert a["status"] == "MEASURED"
        assert a["authority"] == "SHADOW_PENDING_OWNER_APPROVAL"
        # the six lag samples of the scenario (the shared test database may
        # hold other suites' settled markets too: settlements are market
        # facts, not scoped to the account)
        assert a["evidence"]["settlement_lag_samples"] >= 6
        assert a["final_allocatable_usd"] == pytest.approx(56.1)
        assert "POSITION_TRUTH" in a["replay_inputs"]["exposure_basis"] or \
            "PAPER_FILLS" in a["replay_inputs"]["exposure_basis"]
        assert c["canonical_intent"]["status"] == "BUILT"
        assert c["canonical_intent"]["verifies"] is True
        assert d1["rec"]["intent_parity"]["state"] == "REPRODUCED"
        # the alternatives: the refusal on m2 shortly before is NOT one --
        # it was refused under a hard rule (PROBABILITY_EVIDENCE_STALE, the
        # 30 s freshness rule): no allocator could legally take it
        # (red-team finding: hard-rule refusals were counted as qualified)
        tp = d1["rec"]["tape"]
        assert tp["status"] == "MEASURED" and tp["alternatives"] == []
        assert tp["window_counts"]["HARD_RULE_REFUSAL"] == 1
        assert d1["rec"]["alternatives_input"]["status"] == "MEASURED"
        assert d1["rec"]["alternatives_input"]["returns"] == []
        # counterfactuals (V1 realized: 100 bought at 0.554 + 0.70 fees, 40
        # sold at 0.70 - 0.20, 60 paid 1.00)
        cf = d1["eval"]["counterfactuals"]
        assert cf["CURRENT_ACTION"]["pnl_usd"] == pytest.approx(31.7)
        assert cf["HOLD_TO_SETTLEMENT"]["pnl_usd"] == pytest.approx(43.9)
        assert cf["NO_TRADE"]["pnl_usd"] == 0.0
        assert cf["R30_CANONICAL_ACTION"]["pnl_usd"] == pytest.approx(31.7)
        assert cf["ACTUAL_XAVIER_MANAGEMENT"]["pnl_usd"] == pytest.approx(31.7)
        assert cf["CANONICAL_XAVIER_MANAGEMENT"]["pnl_usd"] == \
            pytest.approx(31.7)
        assert cf["CANONICAL_XAVIER_MANAGEMENT"]["basis"].startswith(
            "CANONICAL_MATCHED_EVERY_REVIEW")
        mg = d1["eval"]["management"]
        assert mg["reviews"] == 2 and mg["divergent"] == 0
        assert [r["canonical_action"] for r in mg["per_review"]] == [
            "SELL_REDUCE", "MAINTAIN_STANDING_PROTECTION"]
        for k in ("ROI_ONLY_ALLOCATION", "ALLIE_ALLOCATION_SHADOW"):
            assert cf[k]["pnl_usd"] is not None, (k, cf[k])
        # the capital-hour allocator needs the tape hurdle; the scenario's
        # tape holds fewer than min_hurdle_sample qualified opportunities
        assert cf["CAPITAL_HOUR_ALLOCATION"]["pnl_usd"] is None
        assert cf["CAPITAL_HOUR_ALLOCATION"]["why"].startswith(
            "HURDLE_UNAVAILABLE")
        # every benchmark under the SAME hard rails (the session's caps:
        # per-order, per-market, per-fixture, hedge reserve, groups)
        rl = c["rails"]
        assert rl["status"] == "MEASURED"
        assert rl["caps_source"] == \
            "SESSION_CONFIGURATION_EFFECTIVE_AT_THE_CLOCK"
        assert rl["hedge_reserve_keep_usd"] > 0
        assert rl["usable_idle_usd"] == pytest.approx(
            rl["available_usd"] - rl["hedge_reserve_keep_usd"])
        assert set(d1["rec"]["rails"]) >= {
            "hard_rail_usd", "idle_capital_usd", "market_headroom_usd",
            "fixture_headroom_usd"}
        assert cf["CURRENT_ACTION"]["capital_hours"] > 0
        assert d1["eval"]["lost_opportunity"]["classification"] == "TAKEN"
        # Alpha Attribution V2: the full identity, exact to float rounding
        v2 = d1["v2"]
        assert v2["identity"]["claimed"] and v2["identity"]["level"] == "FULL"
        assert abs(v2["identity"]["check"]["residual_usd"]) <= 1e-9
        assert v2["realized_pnl_usd"] == pytest.approx(31.7)
        # ONE UNIT: EQUAL allocates the decision's whole capital required
        # ($56.10 = 100 contracts incl. fees) and legacy ordered the same 100
        # contracts: allocation alpha is exactly zero (it was -fees x r_c)
        bk = v2["benchmark_allocations"]
        assert bk["EQUAL_ALLOCATION"]["contracts"] == pytest.approx(100.0)
        assert bk["EQUAL_ALLOCATION"]["usd"] == pytest.approx(55.4)
        assert bk["LEGACY_SIZING"]["usd"] == pytest.approx(55.4)
        assert abs(v2["allocation_usd"]) <= 1e-9
        assert abs(v2["allocation_alpha"]["matrix"]["LEGACY_SIZING"][
            "EQUAL_ALLOCATION"]["usd"]) <= 1e-9
        ce = v2["capital_efficiency"]
        assert ce["cash_unavailable_by_prior_allocations"]["n"] == 1
        assert ce["missed_executable_ev_from_occupied_capital"][
            "total_usd"] == pytest.approx(1.5)
        # d4 (an INVESTMENT ENTER on another market) was a qualified
        # opportunity while d1's capital was held
        assert ce["marginal_opportunities_available"]["n"] == 1

        # ── d2: a qualified refusal ────────────────────────────────────
        d2 = _item(built, sc["d2"])
        lo = d2["eval"]["lost_opportunity"]
        assert lo["classification"] == "GOOD_REFUSAL"
        assert lo["hypothetical_pnl"]["label"] == "HYPOTHETICAL"
        assert lo["hypothetical_pnl"]["value"] == pytest.approx(-25.4)
        assert d2["v2"] is None
        # no allocator could legally take it: its allocation counterfactuals
        # are a MEASURED zero naming the hard rule, never a hypothetical
        assert d2["rec"]["qualification"]["state"] == "NOT_QUALIFIED"
        cf2 = d2["eval"]["counterfactuals"]
        for k in ("ROI_ONLY_ALLOCATION", "CAPITAL_HOUR_ALLOCATION"):
            assert cf2[k]["pnl_usd"] == 0.0
            assert "PROBABILITY_EVIDENCE_STALE" in cf2[k]["basis"]
        # its pre-map row was rewritten after the clock: UNAVAILABLE, named
        assert d2["rec"]["event_start_basis"].startswith(
            "NO_EVENT_START_RECORDED_AT_OR_BEFORE_THE_CLOCK")
        assert d2["rec"]["components"]["allie"]["status"] == "UNAVAILABLE"
        assert "expected_hours_to_capital_release" in \
            d2["rec"]["components"]["allie"]["why"]
        assert d2["rec"]["components"]["provider_observation"][
            "why"] == "DECISION_RECORDS_NO_VALUATION_ID"

        # ── d0: before the ledger existed -> capital UNAVAILABLE ──────
        d0 = _item(built, sc["d0"])
        assert d0["rec"]["components"]["karen"]["state"] == "UNAVAILABLE"
        assert d0["rec"]["components"]["karen"]["why"].startswith(
            "NO_KAREN_RECORD_AT_OR_BEFORE_THE_CLOCK")
        # the rails need the ledger, which had no row yet: UNAVAILABLE
        assert d0["rec"]["components"]["rails"]["status"] == "UNAVAILABLE"
        assert d0["rec"]["components"]["rails"]["why"].startswith(
            "IDLE_CAPITAL_UNMEASURED_AT_THE_CLOCK")
        # ... but d0 was refused under a hard rule (BELOW_MIN_GROSS_EDGE): no
        # allocator could take it whatever the cash, a MEASURED zero
        assert d0["rec"]["allocations"]["EQUAL_ALLOCATION"]["usd"] == 0.0
        assert "BELOW_MIN_GROSS_EDGE" in d0["rec"]["allocations"][
            "EQUAL_ALLOCATION"]["basis"]

        # ── d3: refused by the ledger for cash ─────────────────────────
        d3 = _item(built, sc["d3"])
        assert d3["eval"]["lost_opportunity"]["classification"] == \
            "CASH_UNAVAILABLE_BY_PRIOR_ALLOCATION"
        assert d3["rec"]["sleeve"] == "TRAINING"
        # an admissible opportunity that held no position: HYPOTHETICAL
        assert d3["eval"]["counterfactuals"]["ROI_ONLY_ALLOCATION"][
            "label"] == "HYPOTHETICAL"

        # ── d4: no canonical intent -> no new INVESTMENT exposure ──────
        d4 = _item(built, sc["d4"])
        assert d4["rec"]["components"]["canonical_intent"]["status"] == \
            "UNAVAILABLE"
        assert d4["rec"]["components"]["canonical_intent"]["cause"] == \
            "DECISION_CONTENT"
        cf4 = d4["eval"]["counterfactuals"]
        assert cf4["CURRENT_ACTION"]["pnl_usd"] == pytest.approx(-4.55)
        assert cf4["R30_CANONICAL_ACTION"]["pnl_usd"] == 0.0
        assert "NO_NEW_INVESTMENT_EXPOSURE" in \
            cf4["R30_CANONICAL_ACTION"]["basis"]

        # ── per INDEPENDENT event ──────────────────────────────────────
        ev = {e["event_key"]: e for e in built["events"]}
        assert len(ev) == 3                       # fx-m1, fx-m2, fx-m4
        e1 = ev["fx-" + sc["m1"]]
        assert e1["decisions"] == 3 and e1["label"] == LABEL
        assert e1["realized_pnl_usd"] == pytest.approx(31.7)
        assert e1["pnl"]["HOLD_TO_SETTLEMENT"]["pnl_usd"] == \
            pytest.approx(43.9)
        assert e1["marked_drawdown_usd"] > 0
        assert e1["deltas"]["management"]["divergent"] == 0
        assert e1["deltas"]["execution"]["planned_qty"] == pytest.approx(100)
        assert e1["deltas"]["sizing"]["legacy_usd"] == pytest.approx(55.4)
        assert e1["lost_opportunity"] == {
            "CASH_UNAVAILABLE_BY_PRIOR_ALLOCATION": 1, "TAKEN": 1,
            "UNKNOWABLE": 1}
        assert e1["attribution"]["INVESTMENT"]["identity_full"]["holds"]
        assert e1["evidence"]["every_decision_input_recorded_by_its_clock"]
        e4 = ev["fx-" + sc["m4"]]
        assert e4["deltas"]["decision"]["differ_n"] == 1
        assert e4["pnl"]["R30_CANONICAL_ACTION"]["pnl_usd"] == 0.0
        # the run: INVESTMENT apart from the research block
        inv = s["investment"]["counterfactuals"]
        assert inv["CURRENT_ACTION"]["pnl_usd"] == pytest.approx(31.7 - 4.55)
        assert inv["R30_CANONICAL_ACTION"]["pnl_usd"] == pytest.approx(31.7)
        # no hypothetical is summed into realized P&L: the INVESTMENT block
        # has none; the research block keeps d3's apart
        assert inv["ROI_ONLY_ALLOCATION"]["hypothetical"] is None
        assert inv["ROI_ONLY_ALLOCATION"]["pnl_usd"] is not None
        res = s["research_all_sleeves"]["counterfactuals"]
        assert res["ROI_ONLY_ALLOCATION"]["hypothetical"]["n"] == 1
        assert res["ROI_ONLY_ALLOCATION"]["n"] == 4
        assert s["research_all_sleeves"]["decisions"] == 5
        assert s["attribution"]["INVESTMENT"]["identity_full"]["holds"]
        assert s["unavailable_census"]["karen"][
            "NO_KAREN_RECORD_AT_OR_BEFORE_THE_CLOCK"] >= 1

        # ── a bounded review read is never silent ──────────────────────
        cut = await RN.run(conn, start=B - 2000, end=sc["end"],
                           account_id=sc["acct"]["account_id"],
                           now=sc["now"],
                           params={"max_reviews_per_position": 1})
        mg1 = _item(cut, sc["d1"])["eval"]
        assert mg1["management"]["reviews_truncated"] is True
        cxm = mg1["counterfactuals"]["CANONICAL_XAVIER_MANAGEMENT"]
        assert cxm["pnl_usd"] is None
        assert cxm["why"].startswith("REVIEWS_TRUNCATED")

        # ── a divergent review, end to end (measured valuation, Karen,
        # Allie above): fresh evidence selects REDUCE 20 at a 0.80 walk, but
        # the standing protection was kept instead. The canonical path is the
        # hold result plus each canonical sale at its review's walk price
        await F.review(
            conn, sc["acct"], group_id=sc["g1"], at=B + 1500.0,
            recorded_at=B + 1500.1,
            selection={"selected": "REDUCE", "mechanical_selection": "REDUCE"},
            measure={"evidence_state": "FRESH_CURRENT_PROBABILITY",
                     "p": 0.70, "stale": False},
            alternatives={"candidates": [
                {"action": "REDUCE", "qty": 20.0, "fee_usd": 0.1,
                 "walk": {"worst_price": 0.80, "worst_wire": 0.80}}],
                "not_rankable": []},
            standing={"live_orders": [], "protective_price": {
                "ok": True, "price": 0.57}},
            exposure={"open_qty": 60.0}, action={"taken": "KEEP_STANDING"})
        div = await RN.run(conn, start=B - 2000, end=sc["end"],
                           account_id=sc["acct"]["account_id"],
                           now=sc["now"])
        dv = _item(div, sc["d1"])["eval"]
        assert dv["management"]["reviews"] == 3
        assert dv["management"]["divergent"] == 1
        cx = dv["counterfactuals"]["CANONICAL_XAVIER_MANAGEMENT"]
        assert cx["pnl_usd"] == pytest.approx(
            43.9 + 40 * (0.70 - 1.0) - 0.2 + 20 * (0.80 - 1.0) - 0.1)
        assert cx["basis"].startswith("HOLD_TO_SETTLEMENT")
        assert dv["counterfactuals"]["ACTUAL_XAVIER_MANAGEMENT"][
            "pnl_usd"] == pytest.approx(31.7)

        # ── §4 persisted, append-only, CHECKed ─────────────────────────
        got = await ST.record_run(conn, built, code_sha="a" * 40,
                                  triggered_by="test")
        assert got["recorded"], got
        rid = got["run_id"]
        n = await conn.fetchrow(
            "SELECT (SELECT count(*) FROM r30_replay_decisions WHERE run_id=$1)"
            " AS d, (SELECT count(*) FROM r30_replay_events WHERE run_id=$1)"
            " AS e", rid)
        assert (n["d"], n["e"]) == (5, 3)
        X = asyncpg.IntegrityConstraintViolationError
        for t in ("r30_replay_runs", "r30_replay_decisions",
                  "r30_replay_events"):
            await _expect(conn, X, "UPDATE %s SET label = label "
                          "WHERE run_id = $1" % t, rid)
            await _expect(conn, X, "DELETE FROM %s WHERE run_id = $1" % t, rid)
        await _expect(conn, X, "TRUNCATE r30_replay_events")
        C = asyncpg.CheckViolationError
        ins = ("INSERT INTO r30_replay_decisions (run_id, decision_id, "
               " event_key, sleeve, verdict, decision_clock, clock_end, "
               " decision_evidence_max_recorded_at, payload, label) VALUES "
               " ($1,$2,'e','INVESTMENT','ENTER',to_timestamp($3),"
               " to_timestamp($4),to_timestamp($5),$6::jsonb,$7)")
        ok = json.dumps({"label": LABEL})
        # THE DATABASE REFUSES HINDSIGHT: an input recorded after the clock
        await _expect(conn, C, ins, rid, "x1", B, B + 10, B + 1, ok, LABEL)
        await _expect(conn, C, ins, rid, "x2", B + 20, B + 10, B, ok, LABEL)
        await _expect(conn, C, ins, rid, "x3", B, B + 10, B,
                      json.dumps({"label": "FORWARD"}), LABEL)
        await _expect(conn, C, ins, rid, "x4", B, B + 10, B, ok,
                      "FORWARD_EVIDENCE")
        await conn.execute(ins, rid, "x5", B, B + 10, B, ok, LABEL)
        run = await conn.fetchrow("SELECT * FROM r30_replay_runs WHERE "
                                  "run_id = $1", rid)
        assert run["code_sha"] == "a" * 40 and run["label"] == LABEL
        assert run["production_effect"] == "NONE"
        assert json.loads(run["parameters"])["hurdle_quantile"] == 0.75
        assert run["clock_end"].timestamp() == pytest.approx(sc["end"])
        await _expect(conn, C, "INSERT INTO r30_replay_runs (run_id, "
                      " run_version, code_sha, parameters, params_sha, "
                      " clock_start, clock_end, started_at, finished_at, "
                      " decisions_n, events_n, summary, triggered_by) VALUES "
                      " ('r30rp_000000000000000000000000','v','a','{}',$1,"
                      " now(),now(),now(),now(),0,0,'{\"label\":"
                      "\"REPLAY_NOT_FORWARD_EVIDENCE\"}','t')", "b" * 64)
        await _expect(conn, C, "INSERT INTO r30_replay_runs (run_id, "
                      " run_version, code_sha, parameters, params_sha, "
                      " clock_start, clock_end, started_at, finished_at, "
                      " decisions_n, events_n, summary, triggered_by) VALUES "
                      " ('r30rp_000000000000000000000001','v',$2,'{}',$1,"
                      " now(),now() + interval '1 hour',now(),now(),0,0,"
                      " '{\"label\":\"REPLAY_NOT_FORWARD_EVIDENCE\"}','t')",
                      "b" * 64, "c" * 40)           # horizon in the future
        # ── §5 the endpoint reads it (READ ONLY, bounded, labelled) ───
        from sportsassets.api import command_r30_replay as API

        class _Pool:
            @asynccontextmanager
            async def acquire(self):
                yield conn

        async def pool():
            return _Pool()
        orig = API._pool
        API._pool = pool
        try:
            out = await API.r30_replay(run_id=None, limit=2,
                                       investment_only=False,
                                       decision_id=sc["d1"])
            assert out["label"] == LABEL and out["status"] == "OK"
            assert out["run"]["run_id"] == rid
            assert len(out["events"]) == 2                     # bounded
            assert out["decision"]["payload"]["label"] == LABEL
            assert out["decision"]["payload"]["counterfactuals"][
                "CURRENT_ACTION"]["pnl_usd"] == pytest.approx(31.7)
            inv_only = await API.r30_replay(run_id=rid, limit=100,
                                            investment_only=True,
                                            decision_id=None)
            assert all(e["investment_only"] for e in inv_only["events"])
            missing = await API.r30_replay(run_id="r30rp_" + "f" * 24,
                                           limit=5, investment_only=False,
                                           decision_id=None)
            assert missing["status"] == "NOT_FOUND"
        finally:
            API._pool = orig
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_missing_economics_are_unavailable_never_a_zero():
    """Red-team BLOCKER: a decision whose executable economics were never
    recorded got measured 0.0 benchmarks, which put the whole ex-ante edge
    into allocation. Now: UNAVAILABLE with the reason, and the V2 identity
    drops to COMBINED_WHAT_AND_HOW_MUCH. An admissible alternative with
    unrecorded economics makes the alternative set UNAVAILABLE (not 'held
    none')."""
    conn, tx = await _tx()
    try:
        acct, B = await F.account(conn)
        ma, mb = F.uid("rpl-ma-"), F.uid("rpl-mb-")
        da = await F.decision(conn, acct, slug=ma, at=B, recorded_at=B + 0.1,
                              econ={}, qty=10.0, limit=0.5)
        ga = "paper_group_" + F.uid()
        oa = await F.order(conn, acct, decision_id=da, group_id=ga, slug=ma,
                           qty=10.0, limit=0.5, at=B + 0.1, terminal_at=B + 2)
        await F.fill(conn, acct, order_id=oa, group_id=ga, slug=ma, qty=10.0,
                     price=0.5, fee=0.05, at=B + 1, recorded_at=B + 1.1)
        await F.settle(conn, acct, group_id=ga, slug=ma, qty=10.0,
                       outcome="WON", payout=1.0, at=B + 5000,
                       recorded_at=B + 5001)
        db_ = await F.decision(conn, acct, slug=mb, at=B + 60,
                               recorded_at=B + 60.1,
                               econ=F.economics(qty=20, vwap=0.4, fees=0.1,
                                                net=3.9))
        built = await RN.run(conn, start=B - 10, end=B + 6000,
                             account_id=acct["account_id"], now=B + 6001)
        a, b = _item(built, da), _item(built, db_)
        assert a["rec"]["qualification"]["state"] == "ECONOMICS_UNRECORDED"
        for k in ("EQUAL_ALLOCATION", "ROI_ONLY_RANKING",
                  "CAPITAL_HOUR_RANKING"):
            got = a["rec"]["allocations"][k]
            assert got["usd"] is None, (k, got)
            assert got["why"].startswith(
                "DECISION_TIME_EXECUTABLE_NET_NOT_RECORDED")
        cfa = a["eval"]["counterfactuals"]
        assert cfa["ROI_ONLY_ALLOCATION"]["pnl_usd"] is None
        assert cfa["CAPITAL_HOUR_ALLOCATION"]["pnl_usd"] is None
        v2 = a["v2"]
        assert v2["selection_usd"] is None and v2["allocation_usd"] is None
        assert v2["opportunity_set_usd"] is None
        assert "DECISION_TIME_EXECUTABLE_NET_NOT_RECORDED" in \
            v2["unmeasured"]["allocation_usd"]
        assert v2["identity"]["level"] == "COMBINED_WHAT_AND_HOW_MUCH"
        assert v2["identity"]["claimed"]
        # B's contemporaneous alternative A was admissible (ENTER) but its
        # economics were never recorded
        ai = b["rec"]["alternatives_input"]
        assert ai["status"] == "UNAVAILABLE"
        assert ai["why"].startswith("ALTERNATIVES_WITH_UNRECORDED_ECONOMICS_1")
        assert b["rec"]["allocations"]["EQUAL_ALLOCATION"]["usd"] is None
        assert b["rec"]["allocations"]["EQUAL_ALLOCATION"]["why"].startswith(
            "POOL_INCOMPLETE")
        assert b["v2"]["selection_usd"] is None
        assert b["v2"]["alternatives"]["basis"] is None
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_a_truncated_tape_is_unavailable_never_an_empty_set():
    """Red-team finding: the tape snapshot kept the NEWEST rows while the
    replay walked the OLDEST decisions, so they read a measured 'no
    alternative'. Now the tape is read oldest first in pages and a clock it
    does not cover is TAPE_TRUNCATED."""
    conn, tx = await _tx()
    try:
        acct, B = await F.account(conn)
        for k in range(3):
            await F.decision(conn, acct, slug=F.uid("rpl-f-"),
                             at=B - 300 + 50 * k,
                             recorded_at=B - 300 + 50 * k + 0.1,
                             verdict="REFUSE", refusal="BELOW_MIN_GROSS_EDGE")
        ma, mt = F.uid("rpl-alt-"), F.uid("rpl-t-")
        await F.decision(conn, acct, slug=ma, at=B - 100, recorded_at=B - 99.9,
                         econ=F.economics(qty=50, vwap=0.5, fees=0.4,
                                          net=3.6))
        dt = await F.decision(conn, acct, slug=mt, at=B, recorded_at=B + 0.2,
                              econ=F.economics(qty=100, vwap=0.554, fees=0.7,
                                               net=6.0))
        kw = dict(start=B - 400, end=B + 1000, account_id=acct["account_id"],
                  now=B + 1001)
        full = await RN.run(conn, **kw)
        t = _item(full, dt)
        assert t["rec"]["tape"]["status"] == "MEASURED"
        assert len(t["rec"]["tape"]["alternatives"]) == 1
        cut = await RN.run(conn, params={"snapshot_max_rows": 4}, **kw)
        t = _item(cut, dt)
        assert t["rec"]["tape"]["status"] == "UNAVAILABLE"
        assert t["rec"]["tape"]["why"].startswith("TAPE_TRUNCATED")
        assert t["rec"]["alternatives_input"]["status"] == "UNAVAILABLE"
        assert t["rec"]["hurdle"]["why"].startswith("TAPE_TRUNCATED")
        assert t["v2"]["selection_usd"] is None
        assert t["v2"]["alternatives"]["why"].startswith("TAPE_TRUNCATED")
        assert "SNAPSHOT_TRUNCATED_TAPE" in cut["summary"]["notes"]
        # the oldest replayed decision is covered by the bounded tape
        first = min(cut["items"], key=lambda it: it["rec"]["clock"])
        assert first["rec"]["tape"]["status"] == "MEASURED"
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_the_canonical_action_is_unavailable_when_the_replay_cannot_see_the_order_form():
    """Red-team finding: a gap in the REPLAY's evidence (no order form, the
    session started after the clock) was priced as the R30 fail-closed zero.
    Only a decision whose own record cannot carry an intent gets the zero;
    this one is UNAVAILABLE. An ENTER with neither an order nor a refusal has
    its own V2 fill state."""
    conn, tx = await _tx()
    try:
        _, B = await F.account(conn)
        late = await H.new_account(conn, "rpl-late", now=B + 1000)
        m = F.uid("rpl-nf-")
        d = await F.decision(conn, late, slug=m, at=B, recorded_at=B + 0.1,
                             econ=F.economics(qty=100, vwap=0.554, fees=0.7,
                                              net=6.0))
        built = await RN.run(conn, start=B - 10, end=B + 2000,
                             account_id=late["account_id"], now=B + 2001)
        it = _item(built, d)
        ci = it["rec"]["components"]["canonical_intent"]
        assert ci["status"] == "UNAVAILABLE"
        assert ci["cause"] == "REPLAY_EVIDENCE_MISSING"
        cf = it["eval"]["counterfactuals"]
        assert cf["R30_CANONICAL_ACTION"]["pnl_usd"] is None
        assert cf["R30_CANONICAL_ACTION"]["why"].startswith(
            "R30_CANONICAL_ACTION_UNREPLAYABLE")
        assert cf["CURRENT_ACTION"]["pnl_usd"] is None
        assert it["v2"]["fill_state"] == "NO_ORDER_RECORDED"
        assert it["v2"]["unmeasured"]["execution_usd"] == \
            "ENTER_WITHOUT_A_RECORDED_ORDER_OR_REFUSAL"
        inv = built["summary"]["investment"]["counterfactuals"]
        assert inv["R30_CANONICAL_ACTION"]["pnl_usd"] is None
        assert "R30_CANONICAL_ACTION_UNREPLAYABLE" in \
            inv["R30_CANONICAL_ACTION"]["unavailable"]
        # the session (and with it the caps) is not visible: rails too
        assert it["rec"]["components"]["rails"]["status"] == "UNAVAILABLE"
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_a_position_sold_out_before_settlement_reports_its_cash_pnl():
    """Red-team finding: a fully exited, unsettled position read realized
    UNAVAILABLE. Its cash P&L is known (CLOSED_BY_SALE_CASH_PNL); only the
    hold counterfactual waits for the settlement."""
    conn, tx = await _tx()
    try:
        acct, B = await F.account(conn)
        m = F.uid("rpl-cs-")
        d = await F.decision(conn, acct, slug=m, at=B, recorded_at=B + 0.1,
                             econ=F.economics(qty=100, vwap=0.5, fees=0.5,
                                              net=11.5))
        g = "paper_group_" + F.uid()
        o = await F.order(conn, acct, decision_id=d, group_id=g, slug=m,
                          qty=100.0, limit=0.5, at=B + 0.1, terminal_at=B + 2)
        await F.fill(conn, acct, order_id=o, group_id=g, slug=m, qty=100.0,
                     price=0.5, fee=0.5, at=B + 1, recorded_at=B + 1.1)
        o2 = await F.order(conn, acct, decision_id=None, group_id=g, slug=m,
                           qty=100.0, limit=0.7, at=B + 600, role="EXIT",
                           direction="SELL", terminal_at=B + 602)
        await F.fill(conn, acct, order_id=o2, group_id=g, slug=m, qty=100.0,
                     price=0.7, fee=0.3, at=B + 601, recorded_at=B + 601.1,
                     role="EXIT", direction="SELL")
        built = await RN.run(conn, start=B - 10, end=B + 5000,
                             account_id=acct["account_id"], now=B + 5001)
        cf = _item(built, d)["eval"]["counterfactuals"]
        assert cf["CURRENT_ACTION"]["pnl_usd"] == pytest.approx(
            100 * 0.7 - 0.3 - (100 * 0.5 + 0.5))
        assert cf["CURRENT_ACTION"]["basis"].startswith(
            "CLOSED_BY_SALE_CASH_PNL")
        assert cf["CURRENT_ACTION"]["capital_hours"] > 0
        assert cf["HOLD_TO_SETTLEMENT"]["pnl_usd"] is None
        assert cf["HOLD_TO_SETTLEMENT"]["why"].startswith(
            "CONTRACT_NOT_SETTLED")
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_a_failing_decision_is_named_and_a_hindsight_violation_stops_the_run(
        monkeypatch):
    from sportsassets.replay import reconstruct as RC
    conn, tx = await _tx()
    try:
        sc = await F.scenario(conn)
        real = RC.karen_at

        async def boom(ctx, dec, clock, scope):
            if dec["decision_id"] == sc["d2"]:
                raise ValueError("synthetic unreadable record")
            return await real(ctx, dec, clock, scope)
        monkeypatch.setattr(RC, "karen_at", boom)
        built = await RN.run(conn, start=sc["B"] - 2000, end=sc["end"],
                             account_id=sc["acct"]["account_id"],
                             now=sc["now"])
        assert built["summary"]["decisions"] == 4
        assert built["summary"]["decisions_not_replayed"] == 1
        bad = built["summary"]["notes"]["DECISIONS_NOT_REPLAYED"]
        assert bad == [{"decision_id": sc["d2"], "error":
                        "ValueError: synthetic unreadable record"}]

        async def leak(ctx, dec, clock, scope):
            raise P.HindsightViolation("forced")
        monkeypatch.setattr(RC, "karen_at", leak)
        with pytest.raises(P.HindsightViolation):
            await RN.run(conn, start=sc["B"] - 2000, end=sc["end"],
                         account_id=sc["acct"]["account_id"], now=sc["now"])
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_a_run_refuses_a_future_horizon_and_an_unbounded_window():
    conn = await asyncpg.connect(H.DSN)
    try:
        with pytest.raises(RN.ReplayRefused):
            await RN.run(conn, start=0, end=2e9, now=1.9e9)
        with pytest.raises(RN.ReplayRefused):
            await RN.run(conn, start=0, end=1.9e9, now=1.9e9)
        with pytest.raises(RN.ReplayRefused):
            await RN.run(conn, start=10, end=5, now=1.9e9)
    finally:
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# §4 THE MIGRATION
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_237_is_idempotent_and_its_rollback_applies_and_guards_runs():
    conn, tx = await _tx()
    try:
        await conn.execute(UP)
        await conn.execute(UP)                          # idempotent
        await conn.execute(DOWN)                        # empty: applies
        assert not await conn.fetchval(
            "SELECT to_regclass('r30_replay_runs') IS NOT NULL")
        await conn.execute(UP)
        await conn.execute(
            "INSERT INTO r30_replay_runs (run_id, run_version, code_sha, "
            " parameters, params_sha, clock_start, clock_end, started_at, "
            " finished_at, decisions_n, events_n, summary, triggered_by) "
            "VALUES ('r30rp_000000000000000000000002','v',$1,'{}',$2,"
            " now() - interval '1 day',now() - interval '1 hour',now(),now(),"
            " 0,0,'{\"label\":\"REPLAY_NOT_FORWARD_EVIDENCE\"}','t')",
            "UNAVAILABLE:TEST", "d" * 64)
        await _expect(conn, asyncpg.RaiseError, DOWN)    # refuses
    finally:
        await tx.rollback()
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# §5 THE ENDPOINT
# ═════════════════════════════════════════════════════════════════════

def test_the_route_is_get_only_and_requires_a_command_session():
    from fastapi.testclient import TestClient

    from sportsassets.api import app as APP
    from sportsassets.api import command_r30_replay as API
    paths, stack = {}, list(APP.app.routes)
    while stack:
        r = stack.pop()
        if hasattr(r, "original_router"):
            stack.extend(r.original_router.routes)
        elif getattr(r, "path", "") == API.PATH:
            paths[r.path] = set(getattr(r, "methods", set()) or set())
    assert paths and all(m <= {"GET", "HEAD"} for m in paths.values()), paths
    client = TestClient(APP.app, raise_server_exceptions=False)
    assert client.get(API.PATH).status_code == 401
    assert client.get(API.PATH + "?limit=5").status_code == 401
    assert client.post(API.PATH).status_code in (401, 405)


@pg
async def test_the_route_answers_empty_with_a_reason_and_reads_read_only():
    from sportsassets.api import command_r30_replay as API
    conn, tx = await _tx()
    try:
        has_runs = await conn.fetchval("SELECT count(*) FROM r30_replay_runs")

        class _Pool:
            @asynccontextmanager
            async def acquire(self):
                yield conn

        async def pool():
            return _Pool()
        orig = API._pool
        API._pool = pool
        try:
            out = await API.r30_replay(run_id=None, limit=5,
                                       investment_only=False,
                                       decision_id=None)
        finally:
            API._pool = orig
        assert out["label"] == LABEL
        if not has_runs:
            assert out["status"] == "EMPTY"
            assert out["why"] == "NO_REPLAY_RUN_RECORDED"
            assert out["events"] == [] and out["run"] is None
    finally:
        await tx.rollback()
        await conn.close()


async def test_the_route_reads_in_a_read_only_transaction():
    from sportsassets.api import command_r30_replay as API
    seen = {}

    class _Tx:
        def __init__(self, ro):
            seen["readonly"] = ro

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Conn:
        def transaction(self, readonly=False):
            return _Tx(readonly)

        async def execute(self, sql):
            seen["sql"] = sql

        async def fetchval(self, sql, *a):
            return False                           # migration absent

    class _Pool:
        @asynccontextmanager
        async def acquire(self):
            yield _Conn()

    async def pool():
        return _Pool()
    orig = API._pool
    API._pool = pool
    try:
        out = await API.r30_replay(run_id=None, limit=5,
                                   investment_only=False, decision_id=None)
    finally:
        API._pool = orig
    assert seen["readonly"] is True
    assert "statement_timeout" in seen["sql"]
    assert out["status"] == "UNAVAILABLE"
    assert out["why"] == "MIGRATION_237_NOT_APPLIED"
