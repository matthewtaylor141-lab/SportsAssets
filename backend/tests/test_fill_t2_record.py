"""T2 (2026-09-08; FILL program lane 4): the per-fill record -- one
durable row per fill of his the mirror held (mirror_fill_answers,
migration 060), written once with the name the tick gave it and the
order it placed, never lost to the plan list's 20-entry bound or the
book's close; the fills-missed census keys on it first.

The rows (hard2/hourly_1737.txt, the fills-missed preset at 17:37Z):
3,234 fills of his on our markets in 24 h, 1,105 / $517,203.44 `unseen`
(rows 1659-1660); book 611 cs2-g2-ast10, 118 `unseen` fills / $30,288.20
on a LIVE book of 301 chain rows (row 1687; hard2/post_fvv_1707.txt row
334); Oz/Denchev 544, 8 `unseen` fills / $16,110.20 (row 1691) beside our
2,709 @0.520 bought 13:35-13:40 (hard2/verify_1750.txt row 564); book 285
lal-elc-rso total, our exit placed 23 s after his fill, the book closed
(hard2/post_exits_1707.txt row 334); book 661's replace-cancelled cover
rest 609 @0.620 filled 89.24 (hard2/closerows_1750.txt row 529).

THE RULE. _fills_seen still builds the plan's list exactly as E9 did
(bounded at HIS_FILLS_SEEN_MAX = 20, the newest kept) and, when the 060
table read present this tick, ALSO queues one row (t.fill_rows) for
every entry whose ingest clock (det, else ts) is past the book's
high-water mark `fills_hwm` -- the prior plan's or the flush memo's,
whichever is later; absent, every entry the list holds, once -- BEFORE
the bound drops the oldest. _flush_fill_answers writes the tick's rows
in ONE INSERT ... ON CONFLICT (whale, fill_id) DO NOTHING at the tail
of both tick paths under CAND_REFUSAL_WRITE_TIMEOUT_S (5.0 s); the hwm
advances ONLY on a successful flush, the plan carries it beside
his_fills_seen (the quiet skip too), a failed or timed-out write keeps
its rows for the next tick. Census `fill_answer_write_failed` and
`fill_answers_absent`; plan `fills_hwm`; no decision word written; no
rail, no knob; NO order path reads the table.

Driven against the worker file's fakes (its autouse rails are imported;
its pool answers the 060 probe present by default, `no_fill_answers_table`
makes it the database before 060) and E9's fast-tick helpers. The
scratch-database pin (the E12 idiom, DSN_BASE / MIG_DIR) skips without a
local server, never fakes.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import pathlib
import re

import pytest

from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.scripts import migrate
from sportsassets.workers import mirror_live as ml
from tests.test_e12_flow_only import DSN_BASE, MIG_DIR  # noqa: F401 -- the scratch-database idiom
from tests.test_e9_fast_path import _fast, _skips, _walk
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails
    CID, M, N, NOW, SLUG, _Venue, _armed, _census, _fill, _mkt, _places, _pool, _run, _tick,
)

ROOT = pathlib.Path(__file__).resolve().parents[2]
SQL_060 = MIG_DIR / "060_mirror_fill_answers.sql"
NEW_NAMES = ("fill_answer_write_failed", "fill_answers_absent")
COLUMNS = ("id", "whale", "condition_id", "fill_id", "fill_ts", "detected_at", "at", "book_id", "order_id",
           "name", "tick")


def _rows(p):
    """The table as written, in fill_id order."""
    return sorted(p.fill_answers.values(), key=lambda r: (float(r["fill_ts"] or 0.0), r["fill_id"]))


def _forty(n=40, start=None):
    """Book 611's shape: n fills of 10 shares, a second apart, each with
    the ingest's clock a second after its stamp."""
    s = NOW - 3000 if start is None else start
    return [_fill(M, "BUY", 10, 0.31, s + i, detected_at=s + i + 1) for i in range(n)]


# ------------------------------------------------------------ (1) the migration

def test_t2_060_exists_sorts_last_and_is_one_create_table_if_not_exists_with_the_unique_and_one_index():
    assert SQL_060.exists()
    files = [x.name for x in sorted(MIG_DIR.glob("*.sql"))]
    i = files.index("059_mirror_orders_send_record.sql")
    # FILL lane 9 (2026-09-09) added 061 after this one: 060 sorts after 059, 061 last
    assert files[i + 1] == "060_mirror_fill_answers.sql" and sum(f.startswith("060_") for f in files) == 1
    assert files[i + 2] == "061_fill_answers_cause_orders_fast.sql" == files[-1]
    sql = SQL_060.read_text()
    assert sql.splitlines()[0].startswith("-- 060: MIRROR FILL ANSWERS (T2, 2026-09-08; FILL program lane 4")
    body = "\n".join(ln.split("--", 1)[0] for ln in sql.splitlines())
    stmts = [" ".join(s.split()) for s in body.split(";") if s.strip()]
    assert len(stmts) == 2
    assert stmts[0].startswith("CREATE TABLE IF NOT EXISTS mirror_fill_answers ( id BIGSERIAL PRIMARY KEY, whale TEXT NOT NULL,")
    assert "UNIQUE (whale, fill_id)" in stmts[0] and "at DOUBLE PRECISION NOT NULL" in stmts[0]
    assert stmts[1] == ("CREATE INDEX IF NOT EXISTS mirror_fill_answers_condition_ts ON mirror_fill_answers"
                        " (condition_id, fill_ts)")
    up = " ".join(stmts).upper()
    assert "DEFAULT" not in up and "DROP " not in up and "ALTER " not in up, "additive; no DEFAULT on any column"
    assert "mirror_orders" not in " ".join(stmts) and "mirror_books" not in " ".join(stmts)
    # applied on boot: the API's start.sh runs the sorted glob before serving; the workers never do
    start = (ROOT / "backend" / "start.sh").read_text()
    assert "python -m sportsassets.scripts.migrate" in start and start.index("scripts.migrate") < start.index("exec uvicorn")
    assert 'sorted(MIGRATIONS_DIR.glob("*.sql"))' in pathlib.Path(migrate.__file__).read_text()
    assert "migrate" not in inspect.getsource(ml)


def test_t2_060_parses_as_postgres_sql_with_the_columns_the_plan_lists():
    pglast = pytest.importorskip("pglast")
    from pglast.enums import ConstrType
    stmts = pglast.parse_sql(SQL_060.read_text())
    assert [type(s.stmt).__name__ for s in stmts] == ["CreateStmt", "IndexStmt"]
    ct = stmts[0].stmt
    assert ct.relation.relname == "mirror_fill_answers" and ct.if_not_exists is True
    cols, table_constraints = [], []
    for el in ct.tableElts:
        if type(el).__name__ == "ColumnDef":
            types = {c.contype for c in (el.constraints or [])}
            assert ConstrType.CONSTR_DEFAULT not in types, el.colname
            cols.append((el.colname, [x.sval for x in el.typeName.names][-1],
                         ConstrType.CONSTR_NOTNULL in types or ConstrType.CONSTR_PRIMARY in types))
        else:
            table_constraints.append(el)
    assert cols == [("id", "bigserial", True), ("whale", "text", True), ("condition_id", "text", True),
                    ("fill_id", "text", True), ("fill_ts", "float8", False), ("detected_at", "float8", False),
                    ("at", "float8", True), ("book_id", "int8", False), ("order_id", "int8", False),
                    ("name", "text", True), ("tick", "int8", False)]
    assert [c[0] for c in cols] == list(COLUMNS)
    assert len(table_constraints) == 1 and table_constraints[0].contype == ConstrType.CONSTR_UNIQUE
    assert [k.sval for k in table_constraints[0].keys] == ["whale", "fill_id"]
    ix = stmts[1].stmt
    assert ix.if_not_exists is True and ix.relation.relname == "mirror_fill_answers" and ix.unique is not True
    assert [p.name for p in ix.indexParams] == ["condition_id", "fill_ts"]
    # the worker's two statements name the table; json_populate_recordset reads the table's own row type
    for s in (ml._SQL_FILL_ANSWERS, ml._SQL_FILL_ANSWERS_GUARD):
        pglast.parse_sql(s)
    assert "json_populate_recordset(NULL::mirror_fill_answers, $1::json)" in ml._SQL_FILL_ANSWERS
    assert "ON CONFLICT (whale, fill_id) DO NOTHING" in ml._SQL_FILL_ANSWERS and "$2" not in ml._SQL_FILL_ANSWERS
    assert ml._SQL_FILL_ANSWERS_GUARD == "SELECT fill_id FROM mirror_fill_answers LIMIT 0 /* ml-fill-answers-guard */"


def test_t2_060_applies_on_a_real_postgres_twice_the_unique_holds_and_the_first_name_stays():
    """The migration twice on a scratch database (idempotent); the guard's
    error before it is the driver's UndefinedTableError; after it the
    worker's INSERT runs and a second row for the same (whale, fill_id)
    with another name leaves the first (the conflict clause)."""
    from tests.test_e12_flow_only import _drop, _scratch

    async def _go():
        admin, conn, name = await _scratch()
        try:
            try:
                await conn.fetch(ml._SQL_FILL_ANSWERS_GUARD)
            except Exception as exc:  # noqa: BLE001 -- the absence, as Postgres answers it
                assert type(exc).__name__ == "UndefinedTableError", exc
            else:
                raise AssertionError("the guard read a table 060 has not created")
            await conn.execute(SQL_060.read_text())
            await conn.execute(SQL_060.read_text())          # idempotent
            cols = await conn.fetch(
                "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                "WHERE table_name = 'mirror_fill_answers' ORDER BY ordinal_position")
            assert [tuple(c) for c in cols] == [
                ("id", "bigint", "NO"), ("whale", "text", "NO"), ("condition_id", "text", "NO"),
                ("fill_id", "text", "NO"), ("fill_ts", "double precision", "YES"),
                ("detected_at", "double precision", "YES"), ("at", "double precision", "NO"),
                ("book_id", "bigint", "YES"), ("order_id", "bigint", "YES"), ("name", "text", "NO"),
                ("tick", "bigint", "YES")]
            assert await conn.fetch(ml._SQL_FILL_ANSWERS_GUARD) == []
            import json
            rows = [{"whale": "rn1", "condition_id": CID, "fill_id": "905", "fill_ts": 1757000000.0,
                     "detected_at": 1757000001.0, "at": 1757000010.0, "book_id": 31, "order_id": None,
                     "name": "on_target", "tick": 7},
                    {"whale": "rn1", "condition_id": CID, "fill_id": "906", "fill_ts": 1757000002.0,
                     "detected_at": None, "at": 1757000010.0, "book_id": 31, "order_id": 4421,
                     "name": "rest_placed", "tick": 7}]
            assert await conn.execute(ml._SQL_FILL_ANSWERS, json.dumps(rows)) == "INSERT 0 2"
            again = [dict(rows[0], name="renamed", order_id=9, tick=8), dict(rows[1], name="renamed")]
            assert await conn.execute(ml._SQL_FILL_ANSWERS, json.dumps(again)) == "INSERT 0 0"
            got = await conn.fetch("SELECT fill_id, name, order_id, tick FROM mirror_fill_answers ORDER BY fill_id")
            assert [tuple(r) for r in got] == [("905", "on_target", None, 7), ("906", "rest_placed", 4421, 7)]
            # another whale's same fill id is another row
            assert await conn.execute(ml._SQL_FILL_ANSWERS, json.dumps([dict(rows[0], whale="x")])) == "INSERT 0 1"
        finally:
            await _drop(admin, conn, name)
    _run(_go())


# -------------------------------------------------------- (2) book 611's shape

def test_t2_book_611s_shape_forty_fills_on_one_tick_write_forty_rows_the_list_keeps_twenty_and_the_hwm_advances():
    """40 fills held on one tick with the list at 20 -> 40 rows in ONE
    INSERT, the plan's list the newest 20, the hwm the newest ingest
    clock (advanced by the flush, so the FIRST plan does not carry it and
    the next does); the next tick's 3 new fills queue 3, not 43; a tick
    with nothing new writes nothing; a restart (the memos dropped, the
    plan read back) queues nothing already written."""
    p = _pool(fills=_forty(), snap={M: 400.0, N: 0.0})
    b = p.add_book(ledger=400)
    v = _Venue(held={SLUG: 400})
    st = _tick(p, v, http=_mkt(400.0))
    assert _census(st, "on_target") == 1 and _census(st, "fill_answers_absent") == 0
    assert _census(st, "fill_answer_write_failed") == 0
    assert p.fill_writes == [40] and len(p.fill_answers) == 40
    seen = b["last_plan"]["his_fills_seen"]
    assert len(seen) == ml.HIS_FILLS_SEEN_MAX == 20 and [e["ts"] for e in seen] == [NOW - 3000 + i for i in range(20, 40)]
    rows = _rows(p)
    assert [r["fill_id"] for r in rows] == [str(NOW - 3000 + i) for i in range(40)]
    assert all(r["whale"] == "rn1" and r["condition_id"] == CID and r["book_id"] == b["id"] for r in rows)
    assert all(r["detected_at"] == r["fill_ts"] + 1 and r["at"] == NOW and r["tick"] == 1 for r in rows)
    assert all(r["order_id"] is None and r["name"] == "on_target" for r in rows)
    # FILL lane 9 (migration 061): the row also carries cause / rest_id / fast (NULL / NULL / false here:
    # `on_target` names no rest, a full tick)
    assert set(rows[0]) == (set(COLUMNS) - {"id"}) | {"cause", "rest_id", "fast"}
    assert all(r["cause"] is None and r["rest_id"] is None and r["fast"] is False for r in rows)
    assert ml._fill_hwm == {b["id"]: NOW - 3000 + 39 + 1} and ml._fill_pending == {}
    assert "fills_hwm" not in b["last_plan"], "the first plan is written before its own flush"
    # three new fills: three rows, the plan carries the hwm the last flush left
    p.fills.extend(_forty(3, start=NOW + 10))
    p.snap = {M: 430.0, N: 0.0}
    st2 = _tick(p, v, now=NOW + 30, http=_mkt(430.0))
    assert _census(st2, "rest_placed") == 1 and _places(v)
    assert p.fill_writes == [40, 3] and len(p.fill_answers) == 43
    new = [r for r in _rows(p) if float(r["fill_ts"]) >= NOW + 10]
    assert [r["name"] for r in new] == ["rest_placed"] * 3 and {r["order_id"] for r in new} == {b["open_order_id"]}
    assert all(r["at"] == NOW + 30 and r["tick"] == 2 for r in new)
    assert b["last_plan"]["fills_hwm"] == NOW - 3000 + 40 and ml._fill_hwm == {b["id"]: NOW + 13}
    assert len(b["last_plan"]["his_fills_seen"]) == 20
    # nothing new: nothing written, the plan's hwm is the flush's
    st3 = _tick(p, v, now=NOW + 60, http=_mkt(430.0))
    assert p.fill_writes == [40, 3] and b["last_plan"]["fills_hwm"] == NOW + 13
    assert _census(st3, "fill_answer_write_failed") == 0
    # a restart: the memos are gone, the plan's hwm stands, nothing already written is queued again
    ml._fill_hwm.clear()
    ml._fill_pending.clear()
    _tick(p, v, now=NOW + 90, http=_mkt(430.0))
    assert p.fill_writes == [40, 3] and len(p.fill_answers) == 43
    assert ml._fill_hwm == {} and b["last_plan"]["fills_hwm"] == NOW + 13


def test_t2_a_restart_between_a_flush_and_the_next_plan_re_writes_at_most_one_ticks_fills_idempotently():
    """The plan carries the hwm as the last flush left it, so a restart
    after tick 2's flush but before tick 3's plan re-queues tick 2's fill
    (its clock is past the plan's hwm) -- one row, refused by the
    conflict clause, the first name kept."""
    p = _pool()
    b = p.add_book(ledger=300)
    v = _Venue(held={SLUG: 300})
    _tick(p, v)
    p.fills.append(_fill(M, "BUY", 50, 0.31, NOW + 10, detected_at=NOW + 11))
    p.snap = {M: 350.0, N: 0.0}
    _tick(p, v, now=NOW + 30, http=_mkt(350.0))
    assert p.fill_writes == [1, 1] and b["last_plan"]["fills_hwm"] == NOW - 3000 and ml._fill_hwm == {b["id"]: NOW + 11}
    first = dict(p.fill_answers[("rn1", str(NOW + 10))])
    ml._fill_hwm.clear()
    ml._fill_pending.clear()
    _tick(p, v, now=NOW + 60, http=_mkt(350.0))
    assert p.fill_writes == [1, 1, 1], "tick 2's fill written again, once"
    assert p.fill_answers[("rn1", str(NOW + 10))] == first and len(p.fill_answers) == 2
    assert b["last_plan"]["fills_hwm"] == NOW - 3000, "tick 3's plan carries the hwm the plan read back (its own flush comes after)"
    assert ml._fill_hwm == {b["id"]: NOW + 11}
    _tick(p, v, now=NOW + 90, http=_mkt(350.0))
    assert p.fill_writes == [1, 1, 1] and b["last_plan"]["fills_hwm"] == NOW + 11


# ------------------------------------------------ (3) the close, (4) 544's shape

def test_t2_book_285s_shape_the_row_survives_the_close_with_its_order_and_the_closed_book_writes_nothing():
    """A fill named `rest_placed` with order N on a live book; the book
    then closes (the standing row settled): the row stands with order N
    -- the plan's list is gone with the close's plan, the table is not --
    and the closed book queues nothing more."""
    p = _pool(fills=[_fill(M, "BUY", 300.0, 0.31, NOW - 3000)])
    b = p.add_book(ledger=0)
    v = _Venue()
    st = _tick(p, v)
    assert _census(st, "rest_placed") == 1 and b["open_order_id"]
    row = p.fill_answers[("rn1", str(NOW - 3000))]
    assert row["name"] == "rest_placed" and row["order_id"] == b["open_order_id"] and row["book_id"] == b["id"]
    n = row["order_id"]
    b.update(state="closed", last_reason="closed: standing row settled", last_plan={"kind": "closed"}, closed_at=NOW + 5)
    p.orders[n]["state"] = "cancelled"
    b["open_order_id"] = None
    _tick(p, v, now=NOW + 60)
    assert p.fill_writes == [1] and p.fill_answers[("rn1", str(NOW - 3000))]["order_id"] == n
    assert ml._fill_pending == {}


def test_t2_oz_denchev_544s_shape_his_fills_are_written_with_the_name_the_tick_gave_them():
    """Book 544 (verify_1750 row 564: our 2,709 @0.520 bought 13:35-13:40;
    hourly_1737 row 1691: 8 of his fills `unseen`). His fills held while
    an order of ours stands are named `open_order_pending` and written
    so; the ones the first tick answered on target keep `on_target`;
    neither is renamed by a later tick."""
    p = _pool(fills=[_fill(M, "BUY", 300.0, 0.31, NOW - 3000, detected_at=NOW - 2999)])
    b = p.add_book(ledger=0)
    v = _Venue()
    st = _tick(p, v)
    assert _census(st, "rest_placed") == 1 and b["open_order_id"]
    first = p.fill_answers[("rn1", str(NOW - 3000))]
    assert first["name"] == "rest_placed" and first["order_id"] == b["open_order_id"]
    p.fills.append(_fill(M, "BUY", 100.0, 0.31, NOW + 15, detected_at=NOW + 16))
    st2 = _tick(p, v, now=NOW + 30, http=_mkt(400.0))
    assert _census(st2, "open_order_pending") == 1 and b["last_reason"] == "open_order_pending"
    row = p.fill_answers[("rn1", str(NOW + 15))]
    assert row["name"] == "open_order_pending" and row["order_id"] is None and row["at"] == NOW + 30
    assert p.fill_answers[("rn1", str(NOW - 3000))] == first, "never renamed"
    assert [e["name"] for e in b["last_plan"]["his_fills_seen"]] == ["rest_placed", "open_order_pending"]
    assert p.fill_writes == [1, 1]


# ------------------------------------------------------- (6) the write failing

def test_t2_the_write_failing_is_counted_logged_once_keeps_the_rows_and_the_hwm_does_not_advance(caplog):
    """The INSERT raising (060 not applied, or a blip): counted
    `fill_answer_write_failed` every tick, logged ONCE per process, the
    plan's list unchanged, no hwm on the plan, the rows kept in the memo
    and written by the first tick that can -- once."""
    p = _pool()
    b = p.add_book(ledger=300)
    v = _Venue(held={SLUG: 300})
    p.raise_on.append(("ml-fill-answers */", RuntimeError('relation "mirror_fill_answers" does not exist')))
    with caplog.at_level(logging.WARNING):
        st = _tick(p, v)
        st2 = _tick(p, v, now=NOW + 30)
    for s in (st, st2):
        assert s["status"] == "ok" and not s["abandoned"]
        assert _census(s, "fill_answer_write_failed") == 1 and _census(s, "fill_answers_absent") == 0
    assert _census(st, "on_target") == 1 and _census(st2, "book_quiet_skipped") == 1
    assert p.fill_answers == {} and p.fill_writes == []
    assert [e["name"] for e in b["last_plan"]["his_fills_seen"]] == ["on_target"]
    assert "fills_hwm" not in b["last_plan"] and ml._fill_hwm == {}
    assert list(ml._fill_pending) == [("rn1", str(NOW - 3000))]
    warns = [x for x in caplog.records if "mirror_fill_answers write failed" in x.getMessage()]
    assert len(warns) == 1 and warns[0].levelno == logging.WARNING, "logged once per process"
    assert ml._fill_write_logged is True
    p.raise_on.clear()
    st3 = _tick(p, v, now=NOW + 60)
    assert _census(st3, "fill_answer_write_failed") == 0 and p.fill_writes == [1]
    assert p.fill_answers[("rn1", str(NOW - 3000))]["name"] == "on_target" and ml._fill_pending == {}
    assert ml._fill_hwm == {b["id"]: NOW - 3000}
    _tick(p, v, now=NOW + 90)
    assert b["last_plan"]["fills_hwm"] == NOW - 3000 and p.fill_writes == [1]


def test_t2_a_hung_write_is_bounded_by_the_candidate_flushs_timeout_and_counted_as_a_failed_write(monkeypatch):
    p = _pool()
    v = _Venue(held={SLUG: 300})
    p.add_book(ledger=300)
    orig = p.execute

    async def hung(sql, *a):
        if "ml-fill-answers */" in sql:
            await asyncio.sleep(3600)
        return await orig(sql, *a)

    p.execute = hung
    assert ml.CAND_REFUSAL_WRITE_TIMEOUT_S == 5.0
    monkeypatch.setattr(ml, "CAND_REFUSAL_WRITE_TIMEOUT_S", 0.05)
    st = _tick(p, v)
    assert st["status"] == "ok" and not st["abandoned"] and _census(st, "fill_answer_write_failed") == 1
    assert p.fill_answers == {} and list(ml._fill_pending) == [("rn1", str(NOW - 3000))] and ml._fill_hwm == {}
    src = inspect.getsource(ml._flush_fill_answers)
    # FILL lane 9 (061): the statement is chosen by this tick's column probe (the 061 shape only when
    # t.fill_cols is True, lane 4's else), still under the one bounded wait_for
    assert "stmt = _SQL_FILL_ANSWERS_061 if t.fill_cols is True else _SQL_FILL_ANSWERS" in src
    assert "asyncio.wait_for(t.pool.execute(stmt" in src and "CAND_REFUSAL_WRITE_TIMEOUT_S)" in src


def test_t2_the_batch_and_the_pending_memo_are_bounded(monkeypatch):
    """One INSERT of at most FILL_ANSWERS_FLUSH_MAX rows a tick, the rest
    waiting in the memo; the memo at most _FILL_PENDING_MAX rows, the
    OLDEST dropped past it (the plan's list still names them)."""
    assert ml.FILL_ANSWERS_FLUSH_MAX == 5000 and ml._FILL_PENDING_MAX == 20000 and ml._FILL_HWM_MEMO_MAX == 4000
    monkeypatch.setattr(ml, "FILL_ANSWERS_FLUSH_MAX", 3)
    p = _pool(fills=_forty(5), snap={M: 50.0, N: 0.0})
    b = p.add_book(ledger=50)
    v = _Venue(held={SLUG: 50})
    _tick(p, v, http=_mkt(50.0))
    assert p.fill_writes == [3] and len(ml._fill_pending) == 2 and ml._fill_hwm == {b["id"]: NOW - 3000 + 3}
    _tick(p, v, now=NOW + 30, http=_mkt(50.0))
    assert p.fill_writes == [3, 2] and ml._fill_pending == {} and ml._fill_hwm == {b["id"]: NOW - 3000 + 5}
    assert len(p.fill_answers) == 5
    # the memo's bound under a failing write: the oldest two of five dropped
    monkeypatch.setattr(ml, "_FILL_PENDING_MAX", 3)
    ml._fill_hwm.clear()            # another world (book ids repeat across pools): a fresh process
    p2 = _pool(fills=_forty(5), snap={M: 50.0, N: 0.0})
    p2.add_book(ledger=50)
    p2.raise_on.append(("ml-fill-answers */", RuntimeError("db")))
    st = _tick(p2, _Venue(held={SLUG: 50}), http=_mkt(50.0))
    assert _census(st, "fill_answer_write_failed") == 1
    assert [k[1] for k in ml._fill_pending] == [str(NOW - 3000 + i) for i in (2, 3, 4)]


# ------------------------------------------------ (7) book 661's shape, the fast tick

def test_t2_book_661s_shape_a_fast_tick_on_a_book_with_an_order_open_writes_nothing_twice_and_a_bare_one_writes_its_own():
    p = _pool()
    b = p.add_book(ledger=0)
    v = _Venue()
    st0 = _tick(p, v)
    assert _census(st0, "rest_placed") == 1 and b["open_order_id"] and p.fill_writes == [1]
    # the rest standing (book 661's shape): a woken fill finds the order open, the fast tick leaves the
    # book to the full tick -- no plan, nothing queued, nothing written. Since E21 (FILL lane 10) that
    # is the reading under MIRROR_FAST_ADD_REPLAN OFF only (pinned first, byte for byte); ON, the
    # ADDING wake is admitted and the fast tick names the fill itself (below)
    p.fills.append(_fill(M, "BUY", 100.0, 0.31, NOW + 10, detected_at=NOW + 11))
    _walk({M: 0.0, N: 0.0}, NOW + 12)
    rules.MIRROR_FAST_ADD_REPLAN = False
    try:
        fs = _fast(p, v, now=NOW + 13, http=_mkt(400.0))
    finally:
        rules.MIRROR_FAST_ADD_REPLAN = True
    assert _skips(fs) == {CID: "order_open"} and p.fill_writes == [1] and ml._fill_pending == {}
    assert _census(fs, "fill_answers_absent") == 0 and _census(fs, "fill_answer_write_failed") == 0
    # E21 ON: the fast tick plans the book (the rest 13 s old: kept under the floor, his add carried),
    # names the fill open_order_pending itself and its own flush writes the row once, `fast` true
    _walk({M: 0.0, N: 0.0}, NOW + 12)
    fs = _fast(p, v, now=NOW + 13, http=_mkt(400.0))
    assert _skips(fs) == {} and _census(fs, "fast_his_add") == 1 and _census(fs, "open_order_pending") == 1
    assert p.fill_writes == [1, 1] and ml._fill_pending == {}
    fast_row = p.fill_answers[("rn1", str(NOW + 10))]
    assert fast_row["name"] == "open_order_pending" and fast_row["fast"] is True and fast_row["cause"] == "min_life"
    # the full tick after it answers the fill by the same name and writes nothing more (named once);
    # its census reads 2: its own count plus the fast tick's, folded in (E9's fold)
    st = _tick(p, v, now=NOW + 30, http=_mkt(400.0))
    assert _census(st, "open_order_pending") == 2 and _census(st, "fast_his_add") == 1 and p.fill_writes == [1, 1]
    assert p.fill_answers[("rn1", str(NOW + 10))]["name"] == "open_order_pending"
    _tick(p, v, now=NOW + 60, http=_mkt(400.0))
    assert p.fill_writes == [1, 1]
    # a woken BARE book (no order) plans on the fast tick and the fast tick's own flush writes its fill
    ml._fill_hwm.clear()            # another world (book ids repeat across pools): a fresh process
    p2 = _pool()
    b2 = p2.add_book(ledger=0)
    _walk({M: 0.0, N: 0.0}, NOW)
    fs2 = _fast(p2, _Venue())
    assert _skips(fs2) == {} and _census(fs2, "fast_tick_placed") == 1 and p2.fill_writes == [1]
    row = p2.fill_answers[("rn1", str(NOW - 3000))]
    assert row["name"] == "rest_placed" and row["order_id"] == b2["open_order_id"] and row["book_id"] == b2["id"]
    assert ml._fill_hwm == {b2["id"]: NOW - 3000} and ml._fill_pending == {}
    fsrc = inspect.getsource(ml._fast_tick)
    assert fsrc.rstrip().endswith("await _flush_candidate_refusals(t)\n"
                                  "    await _flush_fill_answers(t)                   # T2: the fast tick's own rows, one write")
    assert fsrc.index("_order_cols_guard(t, stats)") < fsrc.index("_fill_answers_guard(t, stats)") < fsrc.index("_read_mode(t)")


# ----------------------------------------------------- (8) the table absent

def test_t2_the_table_absent_is_named_queues_nothing_and_the_tick_goes_on_on_both_paths(caplog):
    """The database before 060: the probe's UndefinedTableError is named
    `fill_answers_absent` (its type on the heartbeat), logged once per
    process; nothing is queued, no INSERT goes out, the plan's list
    carries the census as before and no hwm is written; the tick is NOT
    refused -- it places as it would. The table appearing: the next tick
    queues every entry the list holds, once."""
    p = _pool()
    b = p.add_book(ledger=0)
    v = _Venue()
    p.no_fill_answers_table = True
    with caplog.at_level(logging.WARNING):
        st = _tick(p, v)
    assert st["status"] == "ok" and not st["abandoned"] and _census(st, "rest_placed") == 1 and _places(v)
    assert _census(st, "fill_answers_absent") == 1 and st["fill_answers_absent"] == "UndefinedTableError"
    assert _census(st, "fill_answer_write_failed") == 0
    assert p.fill_writes == [] and p.fill_answers == {} and ml._fill_pending == {} and ml._fill_hwm == {}
    assert [e["name"] for e in b["last_plan"]["his_fills_seen"]] == ["rest_placed"] and "fills_hwm" not in b["last_plan"]
    warns = [x for x in caplog.records if "mirror_fill_answers is absent or unreadable" in x.getMessage()]
    assert len(warns) == 1 and ml._fill_answers_absent_logged is True
    # the table appears: the first tick that reads it present writes the list's entry, once
    p.no_fill_answers_table = False
    p.orders[b["open_order_id"]]["state"] = "cancelled"
    b["open_order_id"] = None
    b["ledger_net"] = 300
    st2 = _tick(p, v, now=NOW + 60, http=_mkt(300.0))
    assert _census(st2, "fill_answers_absent") == 0 and p.fill_writes == [1]
    assert p.fill_answers[("rn1", str(NOW - 3000))]["name"] == "rest_placed", "the FIRST name, from the list"
    assert ml._fill_hwm == {b["id"]: NOW - 3000}
    # the fast tick reads the same (its census folds into the next full tick's)
    ml._fill_hwm.clear()            # another world (book ids repeat across pools): a fresh process
    p2 = _pool()
    p2.add_book(ledger=0)
    p2.no_fill_answers_table = True
    _walk({M: 0.0, N: 0.0}, NOW)
    fs = _fast(p2, _Venue())
    assert _census(fs, "fill_answers_absent") == 1 and _census(fs, "fast_tick_placed") == 1 and p2.fill_writes == []
    assert fs["fill_answers_absent"] == "UndefinedTableError" and ml._fill_pending == {} and ml._fill_hwm == {}


def test_t2_a_probe_failing_for_any_other_reason_is_named_the_same_and_never_refuses_the_tick():
    """The 059 probe refuses the tick on a non-absence error because its
    column decides which INSERT sends an order; this table is on no
    order path, so a blip is named (`fill_answers_absent`, the error's
    name on the heartbeat), nothing is queued this tick and the tick goes
    on. The hwm stands, so the next tick that reads the table present
    queues what this one held."""
    p = _pool()
    b = p.add_book(ledger=0)
    v = _Venue()
    p.raise_on.append(("ml-fill-answers-guard", RuntimeError("db blip")))
    st = _tick(p, v)
    assert st["status"] == "ok" and _census(st, "rest_placed") == 1 and _places(v)
    assert _census(st, "fill_answers_absent") == 1 and st["fill_answers_absent"] == "RuntimeError"
    assert "order_cols_guard_unreadable" not in st and p.fill_writes == [] and ml._fill_pending == {}
    assert "fills_hwm" not in b["last_plan"]
    p.raise_on.clear()
    st2 = _tick(p, v, now=NOW + 30)
    assert _census(st2, "fill_answers_absent") == 0 and p.fill_writes == [1]
    src = inspect.getsource(ml._fill_answers_guard)
    assert "return" not in src.replace('"""', "").split("global")[1], "no refusal: the guard returns nothing"
    tsrc = inspect.getsource(ml._tick)
    assert tsrc.index("_order_cols_guard(t, stats)") < tsrc.index("await _fill_answers_guard(t, stats)") < tsrc.index("_tick_seq += 1")
    assert "if not await _fill_answers_guard" not in tsrc and "if not await _fill_answers_guard" not in inspect.getsource(ml._fast_tick)


# ------------------------------------------------------- (9) the quiet skip

def test_t2_the_quiet_skip_carries_the_hwm_and_writes_a_late_fill_by_its_ingest_clock():
    """E9's skip shape: the quiet skip names a fill it is the first to
    hold. With an ingest clock (detected_at) past the hwm the row is
    queued and written by the skip tick; an old fill that lands late with
    NO ingest clock (det None, an old stamp under the hwm) that the list
    KEEPS is written too -- the record names what the list names (the
    reviewer's re-pin: the hwm guards prior entries, never the ones this
    tick appends and keeps)."""
    p = _pool()
    b = p.add_book(ledger=300)
    v = _Venue(held={SLUG: 300})
    _tick(p, v)
    assert p.fill_writes == [1]
    p.fills.append(_fill(M, "BUY", 0.0, 0.31, NOW - 4000, detected_at=NOW + 25))    # an old, sizeless row lands late
    st = _tick(p, v, now=NOW + 30)
    assert _census(st, "book_quiet_skipped") == 1 and b["last_reason"] == "book_quiet_skipped"
    assert sorted(e["name"] for e in b["last_plan"]["his_fills_seen"]) == ["book_quiet_skipped", "on_target"]
    assert b["last_plan"]["fills_hwm"] == NOW - 3000, "the skip's plan carries the hwm the flush left"
    assert p.fill_writes == [1, 1] and p.fill_answers[("rn1", str(NOW - 4000))]["name"] == "book_quiet_skipped"
    assert p.fill_answers[("rn1", str(NOW - 4000))]["detected_at"] == NOW + 25 and ml._fill_hwm == {b["id"]: NOW + 25}
    assert "fills_hwm" not in ml._SKIP_CARRIED and "his_fills_seen" not in ml._SKIP_CARRIED
    # the same late row with no ingest clock: named on the list (the bound keeps it), so written too
    ml._fill_hwm.clear()            # another world (book ids repeat across pools): a fresh process
    p2 = _pool()
    b2 = p2.add_book(ledger=300)
    _tick(p2, v)
    p2.fills.append(_fill(M, "BUY", 0.0, 0.31, NOW - 4000))
    _tick(p2, v, now=NOW + 30)
    assert sorted(e["name"] for e in b2["last_plan"]["his_fills_seen"]) == ["book_quiet_skipped", "on_target"]
    assert p2.fill_writes == [1, 1] and p2.fill_answers[("rn1", str(NOW - 4000))]["name"] == "book_quiet_skipped"
    assert ml._fill_hwm == {b2["id"]: NOW - 3000}, "a clockless row moves no hwm"
    _tick(p2, v, now=NOW + 60)
    assert p2.fill_writes == [1, 1], "on the list already: not appended, not queued again"


# ------------------------------------------------ (10) the names, the bounds

def test_t2_the_census_names_sit_before_drift_smaller_open_the_emit_sites_and_no_order_path_reads_the_table():
    keys = ml.CENSUS_KEYS
    for k in NEW_NAMES:
        assert keys.count(k) == 1 and ml._new_stats()["census"][k] == 0, k
    # FILL lane 5 (three names), E22 (FILL lane 22, four) and FILL lane 11 (one) landed after this lane and placed theirs nearer the key (-15/-14 -> -23/-22) -- FILL lane 16 (one name) and E21 (FILL lane 10, six) landed first, so every index past this lane's six moved by seven more
    # E23 (FILL lane 23) placed its six names nearer the key (-23 / -22 -> -29 / -28, -27 / -26 / -24 -> -33 / -32 / -30)
    # FILL lane 24 (E24, the desk's hand) placed its four names nearer the key (-36 / -35 -> -40 / -39, -40 / -39 / -37 -> -44 / -43 / -41)
    assert keys[-54] == "fill_answer_write_failed" and keys[-53] == "fill_answers_absent"
    # FILL lane 3 landed first and sits between E14's name and these two (take_in_band -16 -> -27)
    assert keys[-58] == "take_in_band" and keys[-57] == "exit_take_in_band" and keys[-55] == "order_open_his_exit"
    assert keys[-13] == "drift_smaller_open" and keys[-12] == "registered_no_increase"
    assert keys[-1] == "cand_terminal_skipped" and len(set(keys)) == len(keys)
    assert keys[-8:-4] == ("fast_tick", "fast_tick_placed", "fast_tick_skipped", "fast_tick_failed")
    # the emit sites: one each
    src = inspect.getsource(ml)
    assert src.count('_mirror_stop("fill_answer_write_failed")') == 1
    assert '_mirror_stop("fill_answer_write_failed")' in inspect.getsource(ml._flush_fill_answers)
    assert src.count('_mirror_stop("fill_answers_absent")') == 1
    assert '_mirror_stop("fill_answers_absent")' in inspect.getsource(ml._fill_answers_guard)
    # the list's bound and the flush's bounds
    assert ml.HIS_FILLS_SEEN_MAX == 20 and ml.FILL_ANSWERS_FLUSH_MAX == 5000 and ml._FILL_PENDING_MAX == 20000
    # no knob, no rail: the worker and the rules read no environment for the record
    assert "MIRROR_FILL" not in src and "fill_answer" not in inspect.getsource(rules) and "fills_hwm" not in inspect.getsource(rules)
    # the table is named by the two statements alone; the INSERT is sent by the flush alone
    code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
    # FILL lane 9 (061): the second read is the lane's own column probe (_SQL_FILL_CAUSE_GUARD), the second
    # INSERT its three-column shape (_SQL_FILL_ANSWERS_061); both sent by the guard / the flush alone
    assert code.count("FROM mirror_fill_answers") == 2, "the two probes are the only reads of the table"
    assert code.count("INSERT INTO mirror_fill_answers") == 2 and code.count("NULL::mirror_fill_answers") == 2
    assert code.count("else _SQL_FILL_ANSWERS\n") == 1 and code.count("_SQL_FILL_ANSWERS,") == 0
    assert code.count("_SQL_FILL_ANSWERS)") == 0 and code.count("t.pool.execute(stmt,") == 1
    assert code.count("_SQL_FILL_ANSWERS_GUARD)") == 1
    assert code.count("_flush_fill_answers(t)") == 2, "the full tick's tail and the fast tick's"
    assert code.count("_fill_answers_guard(t, stats)") == 2
    assert code.count("_carry_fills_hwm(book, plan)") == 2, "_write_plan and the quiet skip"
    for fn in (ml._act, ml._place_reserved, ml._entry_take, ml._exit_take, ml._tick_candidate, ml._fast_gate,
               ml._maybe_close_episode, ml._flatten_send):
        s = inspect.getsource(fn)
        assert "fill_answers" not in s and "fill_rows" not in s and "fills_hwm" not in s and "_fill_hwm" not in s, fn.__name__
    # the queue reads the UNBOUNDED list (`for e in out`) inside _fills_seen, only on the bool True; the
    # bound is taken beside it (`kept`) so an appended entry the bound keeps is queued whatever its clock
    fsrc = inspect.getsource(ml._fills_seen)
    assert fsrc.index("if t.fill_answers is True:") < fsrc.index("for e in out:") < fsrc.index("return kept")
    assert 'or (e["id"] in appended and e["id"] in keep_ids)' in fsrc
    assert "t.fill_rows.append(_fill_answer_row(t, book, e))" in fsrc
    # the row's shape and the plan's key
    assert re.search(r"^## \d+\. T2, the per-fill record \(2026-09-08, FILL lane 4\)", (ROOT / "docs" / "mirror-coverage.md").read_text(), re.M)


def test_t2_the_decision_words_are_untouched_and_059s_list_stands():
    """T2 writes no decision word: rules.order_decision is E14's, 059's
    comment unchanged; the preset READS the words into its census."""
    assert rules.order_decision("add", True, False) == "take" and rules.order_decision("add", False, False) == "rest"
    assert rules.order_decision("add", True, False, True) == "take_in_band"
    sql = (ROOT / "backend" / "migrations" / "059_mirror_orders_send_record.sql").read_text()
    assert "('rest', 'take', 'cover', 'exit_rest'; 'take_in_band' is" in sql
    assert "fill_answer" not in sql and "decision" not in SQL_060.read_text()


def test_t2_every_name_is_emitted_here(caplog):
    test_t2_the_write_failing_is_counted_logged_once_keeps_the_rows_and_the_hwm_does_not_advance(caplog)
    ml._fill_hwm.clear()            # another world (book ids repeat across pools): a fresh process
    ml._fill_answers_absent_logged = False
    test_t2_the_table_absent_is_named_queues_nothing_and_the_tick_goes_on_on_both_paths(caplog)


def test_t2_the_docs_name_the_rule():
    doc = (ROOT / "docs" / "mirror-coverage.md").read_text()
    m = re.search(r"^## (\d+)\. T2, the per-fill record \(2026-09-08, FILL lane 4\)", doc, re.M)
    assert m, "the section header"
    sec = doc[m.start():]
    for k in NEW_NAMES + ("fills_hwm", "mirror_fill_answers", "060", "HIS_FILLS_SEEN_MAX", "json_populate_recordset",
                          "ON CONFLICT", "CAND_REFUSAL_WRITE_TIMEOUT_S", "FILL_ANSWERS_FLUSH_MAX", "_FILL_PENDING_MAX",
                          "to_regclass", "fill-answers", "fills-missed", "test_fill_t2_record.py",
                          "test_render_ops_fill_answers.py", "$517,203.44", "$397,223.87", "3,234", "1,105", "611",
                          "544", "285", "661", "sportsassets-db"):
        assert k in sec, k
