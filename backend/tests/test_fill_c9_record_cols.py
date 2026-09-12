"""FILL lane 9 (2026-09-09): the record's columns -- migration 061 puts
`cause` / `rest_id` / `fast` on the fill row (mirror_fill_answers, 060)
and `fast` on the order row (mirror_orders); the presets read them.

The rows (hard2/h2225.txt, the 22:25Z hourly): fills-answered named
`open_order_pending` on 95 fills / $22,841.69 at lag median 3 s (row
938) -- the tick SAW the fill and refused it because a rest stood --
and nothing durable said WHY the rest was kept. Book 760's three adds of
2,983 sh at 19:51:22 / 19:52:55 / 19:53:58 (2,455.01 + 2,457.99 +
2,455.01 = $7,368.01) against ONE standing rest, named at lags 3 / 26 /
-37 s (rows 983-985); its fills-missed rows read missed_replace 3 /
$6,308.07 (1119) and filled 2 / $5,055.54 (1128). Book 347, the frozen
short, holds refused:open_order_pending 2 / $31,970.05 (1088) = 71.2%
of the class's $44,884.07 (1002). The latency census re-measured the
4688 / 4693 / 4697 / 4704 replace chain (rows 883 / 878 / 874 / 868:
four orders, one fill of his at 22:13:57) as four rows at 32 / 53 / 192
/ 287 s.

THE RULE. Every fill named against a rest that STOOD (`open_order_pending`,
or the two refusals that stood in for a replace, `take_capped` /
`replace_capped`) records the plan's `rest_cause` -- `frozen` on a frozen
book (E5's kept slot), `flow_grew` when the tick restored a rise to the
block, else rest_decision's clause (`same`, `min_life`, ...), or the
capped refusal's own name -- the standing rest's id (`plan.open_order`,
written on the keep branch and, since this lane, on the replace branch)
and whether a FAST tick named it (`t.fast`). Every order row records
`fast`. Both under the tick's OWN column probes (`_fill_cause_guard`,
`_fast_col_guard`): absent or failing, the 060 / 059 shapes byte for
byte, named on the heartbeat (`fill_answer_cause_absent`,
`fast_col_absent`, `fast_col_unreadable`), the tick NEVER refused.
Census: no new name. Decision words: none written. No rail, no knob. NO
order path reads any of the new columns.

Driven against the worker file's fakes (its pool answers every `LIMIT 0`
probe present by default) and lane 0b's scratch-database World (skipped
without a local server, never faked). The (class, decision) block's
`missed_replace` word is lane 8's and is NOT pinned here.
"""
from __future__ import annotations

import inspect
import json
import pathlib
import re

import pytest

from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.workers import mirror_live as ml
from tests.test_e12_flow_only import MIG_DIR
from tests.test_e9_fast_path import _fast, _skips, _walk
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails
    BUY, CID, M, N, NOW, SLUG, _UndefinedColumn, _Venue, _armed, _census, _fill, _gone, _his, _mkt, _places,
    _pool, _tick,
)
from tests.test_e5_frozen_exits import _frozen_long
from tests.test_e5_frozen_exits import _pool as _e5_pool      # the pool that answers E5's co-held read
from tests.test_render_ops_fills_missed import World, _preset, _stmts
import tests.test_render_ops_hourly as hourly

ROOT = pathlib.Path(__file__).resolve().parents[2]
YML = ROOT / ".github" / "workflows" / "render-ops.yml"
SQL_061 = MIG_DIR / "061_fill_answers_cause_orders_fast.sql"
HEARTBEAT = ("fill_answer_cause_absent", "fast_col_absent", "fast_col_unreadable")
CAUSES = ("same", "min_life", "take_capped", "replace_capped", "frozen", "flow_grew")


def _inserts(p):
    return [a for k, s, a in p.sent if "ml-order-insert" in s]


def _flat(s):
    return " ".join(str(s).split())


def _fill_stmts(p):
    """The fill INSERTs the tick sent, whitespace-collapsed the way the fake records them."""
    return [_flat(s) for k, s, a in p.sent if "ml-fill-answers */" in s]


OLD_INSERT = " ".join(ml._SQL_FILL_ANSWERS.split())
NEW_INSERT = " ".join(ml._SQL_FILL_ANSWERS_061.split())


def _row(p, ts):
    return p.fill_answers[("rn1", str(ts))]


# ------------------------------------------------------------ (1) the migration

def test_c9_061_exists_sorts_last_after_060_and_is_four_nullable_add_column_if_not_exists_with_no_default():
    assert SQL_061.exists()
    files = [x.name for x in sorted(MIG_DIR.glob("*.sql"))]
    i = files.index("060_mirror_fill_answers.sql")
    assert files[i + 1] == "061_fill_answers_cause_orders_fast.sql"
    assert files[-1] == "062_rn1_observability.sql"  # re-pinned 2026-09-12: run 83's 062 is the newest; this lane still adds none
    assert sum(f.startswith("061_") for f in files) == 1
    sql = SQL_061.read_text()
    assert sql.splitlines()[0].startswith("-- 061: THE RECORD'S COLUMNS (2026-09-09; FILL program lane 9")
    body = "\n".join(ln.split("--", 1)[0] for ln in sql.splitlines())
    stmts = [" ".join(s.split()) for s in body.split(";") if s.strip()]
    assert stmts == [
        "ALTER TABLE mirror_fill_answers ADD COLUMN IF NOT EXISTS cause TEXT NULL",
        "ALTER TABLE mirror_fill_answers ADD COLUMN IF NOT EXISTS rest_id BIGINT NULL",
        "ALTER TABLE mirror_fill_answers ADD COLUMN IF NOT EXISTS fast BOOLEAN NULL",
        "ALTER TABLE mirror_orders ADD COLUMN IF NOT EXISTS fast BOOLEAN NULL",
    ]
    up = " ".join(stmts).upper()
    assert "DEFAULT" not in up and "DROP " not in up and "CREATE " not in up and "NOT NULL" not in up
    assert "migrate" not in inspect.getsource(ml)


def test_c9_061_parses_as_four_add_column_if_not_exists_and_the_workers_new_statements_parse():
    pglast = pytest.importorskip("pglast")
    from pglast.enums import AlterTableType, ConstrType
    stmts = pglast.parse_sql(SQL_061.read_text())
    assert [type(s.stmt).__name__ for s in stmts] == ["AlterTableStmt"] * 4
    seen = []
    for s in stmts:
        st = s.stmt
        assert len(st.cmds) == 1 and st.cmds[0].subtype == AlterTableType.AT_AddColumn and st.cmds[0].missing_ok is True
        col = st.cmds[0].def_
        types = {c.contype for c in (col.constraints or [])}
        assert ConstrType.CONSTR_DEFAULT not in types and ConstrType.CONSTR_NOTNULL not in types
        seen.append((st.relation.relname, col.colname, [x.sval for x in col.typeName.names][-1]))
    assert seen == [("mirror_fill_answers", "cause", "text"), ("mirror_fill_answers", "rest_id", "int8"),
                    ("mirror_fill_answers", "fast", "bool"), ("mirror_orders", "fast", "bool")]
    for s in (ml._SQL_FILL_ANSWERS_061, ml._SQL_FILL_CAUSE_GUARD, ml._SQL_FAST_COL_GUARD, ml._SQL_ORDER_INSERT_061):
        pglast.parse_sql(s)
    assert ml._SQL_FILL_CAUSE_GUARD == "SELECT cause, rest_id, fast FROM mirror_fill_answers LIMIT 0 /* ml-fill-cause-guard */"
    assert ml._SQL_FAST_COL_GUARD == "SELECT fast FROM mirror_orders LIMIT 0 /* ml-fast-col-guard */"
    # the 061 shapes are the 060 / 059 statements plus the new columns, LAST
    assert ml._SQL_FILL_ANSWERS_061.replace(",\n        cause, rest_id, fast)", ")").replace(", r.cause, r.rest_id, r.fast", "") \
        == ml._SQL_FILL_ANSWERS
    assert ml._SQL_ORDER_INSERT_061.replace(", his_fill_id, fast)", ", his_fill_id)").replace("$22, $23)", "$22)") \
        == ml._SQL_ORDER_INSERT_059
    assert ml._SQL_ORDER_INSERT_061.count("$") == 23 and ml._SQL_ORDER_INSERT_059.count("$") == 22
    # 059's own probe and its refusal rule are NOT extended: an absent `fast` never reads as "059 columns absent"
    assert ml._SQL_ORDER_COLS_GUARD == ("SELECT ask_at_send, decision, his_fill_id FROM mirror_orders LIMIT 0 "
                                        "/* ml-order-cols-guard */")
    assert "fast" not in inspect.getsource(ml._order_cols_guard)


# ------------------------------------------------------ (2) book 760's shape

def test_c9_book_760s_shape_same_then_min_life_then_the_replace_names_the_old_rest_on_the_new_rests_row():
    """Three adds against one standing rest (h2225 983-985). The first,
    inside the 2% hysteresis (300 -> 303: the target stays 30), is
    `open_order_pending` with cause `same`; the second, past 2% on a
    rest under the 45 s floor (303 -> 400 at 30 s), `min_life`; the
    third, past the floor (400 -> 500 at 60 s), is answered by the
    replace: `rest_placed` with `order` the new rest's id and `rest` the
    OLD one's, no cause. Every row a full tick's (`fast` false)."""
    ml._fill_hwm.clear()
    p = _pool()
    b = p.add_book(ledger=0)
    v = _Venue()
    st = _tick(p, v)
    assert _census(st, "rest_placed") == 1 and b["open_order_id"]
    o1 = b["open_order_id"]
    first = _row(p, NOW - 3000)
    assert (first["name"], first["order_id"], first["cause"], first["rest_id"], first["fast"]) == ("rest_placed", o1, None, None, False)
    assert "rest_cause" not in b["last_plan"]
    # the first add, inside 2%: kept `same`
    p.fills.append(_fill(M, "BUY", 3.0, 0.31, NOW + 10, detected_at=NOW + 11))
    st2 = _tick(p, v, now=NOW + 15, http=_mkt(303.0))
    assert _census(st2, "open_order_pending") == 1 and _census(st2, "kept_min_life") == 0 and not _places(v)[1:]
    assert b["last_plan"]["rest_cause"] == "same" and b["last_plan"]["open_order"] == o1
    r2 = _row(p, NOW + 10)
    assert (r2["name"], r2["order_id"], r2["cause"], r2["rest_id"], r2["fast"]) == ("open_order_pending", None, "same", o1, False)
    e2 = next(e for e in b["last_plan"]["his_fills_seen"] if e["id"] == str(NOW + 10))
    assert (e2["cause"], e2["rest"], e2["fast"]) == ("same", o1, False), "the three ride the entry"
    # the second add, past 2% on a rest under the floor: kept `min_life`
    p.fills.append(_fill(M, "BUY", 97.0, 0.31, NOW + 25, detected_at=NOW + 26))
    st3 = _tick(p, v, now=NOW + 30, http=_mkt(400.0))
    assert _census(st3, "open_order_pending") == 1 and _census(st3, "kept_min_life") == 1 and not _places(v)[1:]
    assert b["last_plan"]["rest_cause"] == "min_life" and b["last_plan"]["decision"] == "kept_min_life"
    r3 = _row(p, NOW + 25)
    assert (r3["name"], r3["cause"], r3["rest_id"], r3["fast"]) == ("open_order_pending", "min_life", o1, False)
    # the third add, past the floor: the replace -- the new rest's row names the OLD rest
    p.fills.append(_fill(M, "BUY", 100.0, 0.31, NOW + 55, detected_at=NOW + 56))
    st4 = _tick(p, v, now=NOW + 60, http=_mkt(500.0))
    assert _census(st4, "rest_placed") == 1 and _census(st4, "open_order_pending") == 0 and st4["requotes"] == 1
    o2 = b["open_order_id"]
    assert o2 and o2 != o1 and p.orders[o1]["state"] == "cancelled" and p.orders[o1]["reason"] == "replace"
    assert b["last_plan"]["open_order"] == o1 and b["last_plan"]["decision"] == "rest"
    r4 = _row(p, NOW + 55)
    assert (r4["name"], r4["order_id"], r4["cause"], r4["rest_id"], r4["fast"]) == ("rest_placed", o2, None, o1, False)
    # never renamed, never re-caused
    assert _row(p, NOW + 10) == r2 and _row(p, NOW + 25) == r3 and p.fill_writes == [1, 1, 1, 1]
    # the 061 shape was sent every tick (the fake reads the columns present)
    assert all("cause, rest_id, fast" in s for s in _fill_stmts(p)) and len(_fill_stmts(p)) == 4
    # every order row of the book carries `fast` false: a full tick placed both rests
    ins = _inserts(p)
    assert len(ins) == 2 and all(len(a) == 23 and a[22] is False for a in ins)


# ------------------------------------------------------ (3) book 347's shape

def test_c9_book_347s_shape_a_frozen_books_kept_slot_names_the_cause_frozen():
    """The frozen book (347: h2225 1088). (a) The standing frozen reduce
    kept through the keep branch (test_e5 351's shape: E5's frozen exit
    rests 600 at his cent, the next tick keeps it): his new fill is
    `open_order_pending` with cause `frozen` -- rest_decision's own
    `same` is not the word, the freeze is -- and `rest` the frozen
    reduce's id. (b) The placement_lost row still 'placing' keeping the
    one-open index (test_e5 388's shape) plans nothing and names the
    freeze's verdict as before; the two stamps that would carry `frozen`
    on that path are pinned by source."""
    ml._fill_hwm.clear()
    p = _e5_pool(fills=_his(300, sold=300), snap=None)
    b = _frozen_long(p)
    v = _Venue(held={SLUG: 600}, bid=0.28, ask=0.32)
    st = _tick(p, v, http=_gone())
    assert _census(st, "frozen_reduce") == 1 and _census(st, "rest_placed") == 1 and "rest_cause" not in b["last_plan"]
    o = b["open_order_id"]
    assert o and _row(p, NOW - 3000)["cause"] is None and _row(p, NOW - 3000)["rest_id"] is None
    # his BUY of 10 (net +10, the reduce's target 1 against the 600 standing: inside 2%): the rest kept
    p.fills.append(_fill(M, "BUY", 10.0, 0.31, NOW + 10, detected_at=NOW + 11))
    v2 = _Venue(held={SLUG: 600}, bid=0.28, ask=0.32)
    v2.orders = v.orders
    st2 = _tick(p, v2, now=NOW + 30, http=_mkt(10.0))
    assert not _places(v2) and b["state"] == "frozen" and p.orders[o]["state"] == "open"
    # the frozen row keeps the freeze's word as its reason; the exit's verdict rides the plan (E5)
    assert _census(st2, "open_order_pending") == 1 and b["last_plan"]["frozen_exit"]["result"] == "open_order_pending"
    assert b["last_plan"]["rest_cause"] == "frozen" and b["last_plan"]["open_order"] == o
    # a frozen book names its fills under the freeze's OWN word (E5: the plan's reason is frozen_reason), so the
    # cause rides beside `placement_lost`, never `open_order_pending` -- the plan's kind admits it
    r = _row(p, NOW + 10)
    assert (r["name"], r["cause"], r["rest_id"], r["fast"]) == ("placement_lost", "frozen", o, False)
    assert b["last_plan"]["kind"] == "frozen"
    # (b) the 'placing' row keeping the slot: nothing placed, the book waits under the freeze's own verdict
    p2 = _e5_pool(fills=_his(300, sold=300), snap=None)
    b2 = _frozen_long(p2)
    o2 = p2.add_order(b2, side=BUY, wire=0.30, qty=300, order_id=None, state="placing", placed_ts=NOW - 90)
    v3 = _Venue(held={SLUG: 600}, bid=0.30, ask=0.32)
    st3 = _tick(p2, v3, http=_gone())
    assert not _places(v3) and o2["state"] == "placing" and b2["state"] == "frozen" and st3["status"] == "ok"
    # E5's kept slot through the non-terminal branch: `open_order_pending` counted, the freeze's verdict on the
    # plan, cause `frozen`; no rest of OURS stands (the lost row IS the slot), so `open_order` / rest_id are None
    assert _census(st3, "open_order_pending") == 1 and b2["last_plan"]["frozen_exit"]["result"] == "open_order_pending"
    assert b2["last_plan"]["rest_cause"] == "frozen" and b2["last_plan"].get("open_order") is None
    assert [(r["name"], r["cause"], r["rest_id"], r["fast"]) for r in p2.fill_answers.values()] \
        == [("placement_lost", "frozen", None, False)] * 2
    assert ml._rest_cause({"state": "frozen"}, {}, {"cause": "same"}) == "frozen"
    assert ml._rest_cause({"state": "live"}, {"flow_fills_grew": {"by": 10.0}}, {"cause": "same"}) == "flow_grew"


def test_c9_a_live_books_placing_row_inside_the_orphan_window_keeps_the_slot_and_names_no_cause():
    """The non-terminal branch on a LIVE book: a 'placing' row younger
    than PLACING_ORPHAN_S (60 s: the process died between create and
    the persist a moment ago) is counted `open_order_pending` by
    _reconcile_placing WITHOUT a freeze, and _act's non-terminal branch
    keeps the slot on the live book. No rest of ours stands and the
    book is not frozen, so the fill it names carries NO cause (NULL is
    the closed direction; the review's M12 mutant stamped `frozen` on
    any book here)."""
    ml._fill_hwm.clear()
    p = _pool()
    b = p.add_book(ledger=0)
    o = p.add_order(b, order_id=None, state="placing", placed_ts=NOW - 30)
    assert 0 < NOW - o["placed_ts"] < ml.PLACING_ORPHAN_S
    v = _Venue()
    st = _tick(p, v)
    assert not _places(v) and o["state"] == "placing" and b["state"] == "live" and st["status"] == "ok"
    assert _census(st, "open_order_pending") >= 1 and _census(st, "placement_lost") == 0
    assert "rest_cause" not in b["last_plan"], "a live book's kept slot is no `frozen`"
    r = _row(p, NOW - 3000)
    assert (r["name"], r["cause"], r["rest_id"], r["fast"]) == ("open_order_pending", None, None, False)
    assert all(e.get("cause") is None for e in b["last_plan"]["his_fills_seen"])
    assert ml._rest_cause({"state": "live"}, {}, {"cause": "min_life"}) == "min_life"
    assert ml._rest_cause({"state": "live"}, {}, {"cause": ""}) is None and ml._rest_cause({}, None, None) is None


# ------------------------------------------------------ (4) the capped refusal

def test_c9_a_replace_capped_tick_names_the_cause_replace_capped_and_the_standing_rest():
    ml._fill_hwm.clear()
    p = _pool()
    b = p.add_book(ledger=0)
    for _ in range(rules.MIRROR_MAX_REPLACES_PER_HOUR):
        p.add_order(b, state="cancelled", reason="replace", done_at=NOW - 100, order_id=None)
    o = p.add_order(b, wire=0.28)
    v = _Venue()
    v.rest("oid-1", price=0.28)
    st = _tick(p, v)
    assert _census(st, "replace_capped") == 1 and not _places(v) and b["last_reason"] == "replace_capped"
    assert b["last_plan"]["rest_cause"] == "replace_capped" and b["last_plan"]["open_order"] == o["id"]
    r = _row(p, NOW - 3000)
    assert (r["name"], r["cause"], r["rest_id"], r["fast"]) == ("replace_capped", "replace_capped", o["id"], False)
    src = inspect.getsource(ml._act)
    # E31: the take-arm's own capped refusal went out of _act with the six take arms, so
    # `plan["rest_cause"] = "take_capped"` 1 -> 0 (`take_capped` stays DECLARED on
    # CENSUS_KEYS and in _REST_STOOD_NAMES as the record of the rule it governed, but no
    # line of _act writes it). What stands at that site is E31's OWN capped refusal that
    # keeps a rest: `maker_no_cent`, stamped before its return exactly as take_capped was
    assert 'plan["rest_cause"] = "take_capped"' not in src and 'return "take_capped"' not in src
    assert src.count('plan["rest_cause"] = "maker_no_cent"') == 1
    assert src.index('plan["rest_cause"] = "maker_no_cent"') < src.index('return "maker_no_cent"')
    assert src.count('plan["rest_cause"] = "replace_capped"') == 1
    assert src.index('plan["rest_cause"] = "replace_capped"') < src.index('return "replace_capped"')
    assert ml._REST_STOOD_NAMES == frozenset(("open_order_pending", "take_capped", "replace_capped"))
    # a fill named under any other word carries no cause
    ml._fill_hwm.clear()
    p2 = _pool()
    p2.add_book(ledger=0)
    _tick(p2, _Venue())
    assert _row(p2, NOW - 3000)["cause"] is None


# ------------------------------------------------ (5) the columns absent / unreadable

def test_c9_the_fill_columns_absent_send_lane_4s_insert_named_and_the_hwm_advances(caplog):
    ml._fill_hwm.clear()
    ml._fill_cause_absent_logged = False
    p = _pool()
    b = p.add_book(ledger=0)
    v = _Venue()
    p.raise_on.append(("ml-fill-cause-guard", _UndefinedColumn('column "cause" does not exist')))
    with caplog.at_level("WARNING"):
        st = _tick(p, v)
    assert st["status"] == "ok" and _census(st, "rest_placed") == 1 and _places(v)
    assert st["fill_answer_cause_absent"] == "UndefinedColumnError" and _census(st, "fill_answers_absent") == 0
    assert _census(st, "fill_answer_write_failed") == 0 and "fast_col_absent" not in st
    assert _fill_stmts(p) == [OLD_INSERT] and "cause" not in _fill_stmts(p)[0]
    assert p.fill_writes == [1] and ml._fill_hwm == {b["id"]: NOW - 3000}, "the hwm advanced: the write succeeded"
    assert "fills_hwm" not in b["last_plan"], "the plan carries the hwm from the NEXT write on (lane 4's rule)"
    assert sum("migration 061 not applied" in r.getMessage() for r in caplog.records) == 1
    p.raise_on.clear()
    st2 = _tick(p, v, now=NOW + 30)
    # the probe answering again: nothing new to write this tick (no new fill), the key gone from the heartbeat
    assert "fill_answer_cause_absent" not in st2 and _fill_stmts(p) == [OLD_INSERT] and p.fill_writes == [1]
    assert b["last_plan"]["fills_hwm"] == NOW - 3000
    p.fills.append(_fill(M, "BUY", 3.0, 0.31, NOW + 40, detected_at=NOW + 41))
    _tick(p, v, now=NOW + 60, http=_mkt(303.0))
    assert _fill_stmts(p) == [OLD_INSERT, NEW_INSERT], "the 061 shape the tick the columns read present"
    # a probe blip is the same name (never a refusal), and the probe is not made when the table read absent
    p3 = _pool()
    p3.add_book(ledger=0)
    p3.raise_on.append(("ml-fill-cause-guard", RuntimeError("db blip")))
    st3 = _tick(p3, _Venue())
    assert st3["status"] == "ok" and st3["fill_answer_cause_absent"] == "RuntimeError" and _fill_stmts(p3) == [OLD_INSERT]
    p4 = _pool()
    p4.add_book(ledger=0)
    p4.no_fill_answers_table = True
    st4 = _tick(p4, _Venue())
    assert _census(st4, "fill_answers_absent") == 1 and "fill_answer_cause_absent" not in st4
    assert not any("ml-fill-cause-guard" in s for k, s, a in p4.sent)


def test_c9_the_orders_probe_raising_is_named_the_row_placed_without_fast_and_the_tick_never_refused():
    """The departure from _order_cols_guard's rule, pinned by name: 059's
    columns are the record the fills census keys on, so a tick that
    cannot say whether it can write them must not place; `fast` is a
    measurement flag whose absence loses a split and nothing else (docs
    section 49's rule for a measurement probe), so its probe never gates
    the money path."""
    ml._fast_col_absent_logged = False
    p = _pool()
    b = p.add_book(ledger=0)
    v = _Venue()
    p.raise_on.append(("ml-fast-col-guard", RuntimeError("db blip")))
    st = _tick(p, v)
    assert st["status"] == "ok" and not st["abandoned"] and _census(st, "rest_placed") == 1 and _places(v)
    assert st["fast_col_unreadable"] == "RuntimeError" and "fast_col_absent" not in st
    assert "order_cols_guard_unreadable" not in st and _census(st, "order_cols_guard_unreadable") == 0
    ins = _inserts(p)
    assert len(ins) == 1 and len(ins[0]) == 22 and ins[0][20] == "rest", "the 059 shape, no `fast`"
    assert b["open_order_id"] and _row(p, NOW - 3000)["fast"] is False, "the fill row still says which tick named it"
    # absent: named `fast_col_absent`, logged once, the 059 shape
    p2 = _pool()
    p2.add_book(ledger=0)
    p2.raise_on.append(("ml-fast-col-guard", _UndefinedColumn('column "fast" does not exist')))
    st2 = _tick(p2, _Venue())
    assert st2["status"] == "ok" and st2["fast_col_absent"] == "UndefinedColumnError" and "fast_col_unreadable" not in st2
    assert len(_inserts(p2)[0]) == 22
    # 059 absent: the fast probe is not even made (the 050 INSERT carries nothing to add to)
    p3 = _pool()
    p3.add_book(ledger=0)
    p3.raise_on.append(("ml-order-cols-guard", _UndefinedColumn('column "ask_at_send" does not exist')))
    st3 = _tick(p3, _Venue())
    assert st3["order_cols_absent"] == "UndefinedColumnError" and "fast_col_absent" not in st3
    assert not any("ml-fast-col-guard" in s for k, s, a in p3.sent) and len(_inserts(p3)[0]) == 19
    # the guards refuse nothing: no return value, no status change, on both paths in the same place
    for fn in (ml._fill_cause_guard, ml._fast_col_guard):
        src = inspect.getsource(fn)
        assert "return False" not in src and "return True" not in src and "degraded" not in src and "_mirror_stop" not in src
    for src in (inspect.getsource(ml._tick), inspect.getsource(ml._fast_tick)):
        assert (src.index("_fill_answers_guard(t, stats)") < src.index("_fill_cause_guard(t, stats)")
                < src.index("_fast_col_guard(t, stats)"))
        assert "if not await _fill_cause_guard" not in src and "if not await _fast_col_guard" not in src
    tsrc = inspect.getsource(ml._tick)
    assert tsrc.index("_fast_col_guard(t, stats)") < tsrc.index("_tick_seq += 1")
    assert inspect.getsource(ml._fast_tick).index("_fast_col_guard(t, stats)") < inspect.getsource(ml._fast_tick).index("_read_mode(t)")


# ------------------------------------------------------- (6) the fast tick

def test_c9_a_fast_named_fill_reads_fast_true_on_its_row_and_its_order_row_reads_fast_true():
    ml._fill_hwm.clear()
    p = _pool()
    b = p.add_book(ledger=0)
    _walk({M: 0.0, N: 0.0}, NOW)
    v = _Venue()
    fs = _fast(p, v)
    assert _skips(fs) == {} and _census(fs, "fast_tick_placed") == 1 and p.fill_writes == [1]
    r = _row(p, NOW - 3000)
    assert (r["name"], r["order_id"], r["fast"], r["cause"], r["rest_id"]) == ("rest_placed", b["open_order_id"], True, None, None)
    ins = _inserts(p)
    assert len(ins) == 1 and len(ins[0]) == 23 and ins[0][22] is True and ins[0][20] == "rest"
    assert "fast_col_absent" not in fs and "fast_col_unreadable" not in fs and "fill_answer_cause_absent" not in fs
    assert any("ml-fill-cause-guard" in s for k, s, a in p.sent) and any("ml-fast-col-guard" in s for k, s, a in p.sent)
    # the full tick after it, a rest standing: his next fill `open_order_pending` on a FULL tick, `fast` false
    p.fills.append(_fill(M, "BUY", 3.0, 0.31, NOW + 10, detected_at=NOW + 11))
    st = _tick(p, v, now=NOW + 30, http=_mkt(303.0))
    assert _census(st, "open_order_pending") == 1
    r2 = _row(p, NOW + 10)
    assert (r2["cause"], r2["rest_id"], r2["fast"]) == ("same", b["open_order_id"], False)
    # the fast tick's _Tick is the E9 field, never a new global
    assert "fast=True" in inspect.getsource(ml.fast_tick_once) and "bool(t.fast)" in inspect.getsource(ml._fills_seen)
    assert "bool(t.fast)" in inspect.getsource(ml._place_reserved) and "_full_tick" not in inspect.getsource(ml._fills_seen)


# ------------------------------------------- (7) names, plan, no order path reads

def test_c9_no_census_name_the_three_heartbeat_keys_the_plan_field_and_no_order_path_reads_the_columns():
    keys = ml.CENSUS_KEYS
    # 0 census names: drift_smaller_open still sits at keys[-13]; lane 5's block sits before it with
    # E22's four (lost_fill_*) and lane 11's one (cand_market_closed_db) between, both landed ahead of this lane;
    # E21 (FILL lane 10) placed its six fast_* names between lane 11's one and the key (-14 -> -20) -- FILL lane 16 (one name, turn_woke_fast) landed first, so every index here moved by one more
    assert keys[-13] == "drift_smaller_open" and keys[-12] == "registered_no_increase"
    # E23 (FILL lane 23) placed its six names between lane 11's and the key (-14 -> -20, -18:-14 -> -24:-20, -21:-18 -> -27:-24) -- FILL lane 16 (one name) and E21 (FILL lane 10, six) landed first, so every index past this lane's six moved by seven more
    # FILL lane 24 (E24, the desk's hand) placed its four names nearer the key (-27 -> -31, -31:-27 -> -35:-31, -34:-31 -> -38:-35, -26 / -25 / -20 -> -30 / -29 / -24)
    assert keys[-55] == "cand_market_closed_db"
    assert tuple(keys[-59:-55]) == ("lost_fill_adopted", "lost_fill_unread", "lost_fill_unexplained", "lost_fill_ambiguous")
    assert tuple(keys[-62:-59]) == ("he_holds", "he_holds_unread", "reopen_refused")
    assert keys[-54] == "turn_woke_fast" and keys[-53] == "fast_order_open" and keys[-48] == "fast_status_unread"
    assert not any(k in keys for k in HEARTBEAT) and not any(k in keys for k in ("rest_cause", "fill_cols", "fast_col"))
    src = inspect.getsource(ml)
    code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
    # each heartbeat key written once, by its guard alone
    assert code.count('stats["fill_answer_cause_absent"]') == 1 and 'stats["fill_answer_cause_absent"]' in inspect.getsource(ml._fill_cause_guard)
    assert code.count('stats["fast_col_absent"]') == 1 and code.count('stats["fast_col_unreadable"]') == 1
    assert 'stats["fast_col_absent"]' in inspect.getsource(ml._fast_col_guard)
    assert 'stats["fast_col_unreadable"]' in inspect.getsource(ml._fast_col_guard)
    assert code.count("fetch(_SQL_FILL_CAUSE_GUARD)") == 1 and code.count("fetch(_SQL_FAST_COL_GUARD)") == 1
    assert code.count("_fill_cause_guard(t, stats)") == 2 and code.count("_fast_col_guard(t, stats)") == 2
    # the plan field: SEVEN stamps -> SIX at E31, and every one of the seven's readers still
    # gets a cause. Two came off and one went on:
    #   -1  the keep branch's three `_rest_cause` returns became TWO. E31 merged the exit
    #       rest's branch and the cover rest's branch into ONE `priced_exit` arm
    #       (`long_exit or short_exit`), because both now hold the identical maker rest at
    #       his cent; the cover rest is stamped by the very same line
    #   -1  `plan["rest_cause"] = "take_capped"` left with the six take arms
    #   +1  `plan["rest_cause"] = "maker_no_cent"` -- the review's CRITICAL-2 hold, the one
    #       refusal this lane ADDS that keeps a standing rest instead of cancelling it
    # So: the keep branch's two `open_order_pending` returns through _rest_cause, the two
    # capped/held refusals (replace_capped, maker_no_cent), the frozen kept slot's two paths
    assert code.count('plan["rest_cause"]') == 6
    act = inspect.getsource(ml._act)
    assert act.count('plan["rest_cause"] = _rest_cause(book, plan, why)') == 2 and act.count('plan["rest_cause"] = "frozen"') == 1
    assert act.count('plan["rest_cause"] = "maker_no_cent"') == 1
    # the merged arm serves BOTH priced exits, so neither reader lost its cause
    assert "long_exit = ex is not None and not short" in act
    assert "short_exit = ex is not None and short and is_exit" in act
    assert "priced_exit = long_exit or short_exit" in act
    assert act.index("if priced_exit:") < act.index('plan["rest_cause"] = _rest_cause(book, plan, why)')
    assert inspect.getsource(ml._place_reserved).count('plan["rest_cause"] = "frozen"') == 1
    assert act.index('plan["open_order"] = o["id"]') < act.index('plan["rest_cause"] = _rest_cause(book, plan, why)')
    # E31: 2 -> 3. The keep branch and the replace branch as before, plus the `maker_no_cent`
    # hold, which names the STANDING rest it refuses to cancel so the record still says which
    # order stood (the same field, the same reader, one more site)
    assert act.count('plan["open_order"] = o["id"]') == 3, "the keep branch, the replace branch, E31's no-cent hold"
    assert act.index('plan["rest_cause"] = "maker_no_cent"') > act.index('plan["open_order"] = o["id"]')
    # the 061 INSERT sent only under BOTH probes; the 059 shape else
    assert "if t.order_cols is True and t.fast_col is True:" in inspect.getsource(ml._place_reserved)
    assert "elif t.order_cols is True:" in inspect.getsource(ml._place_reserved)
    # NO order path READS any of the new columns: the words appear on the record's side alone.
    # E31: `_entry_take`, `_exit_take` and `_flatten_send` are DELETED from the money path,
    # so the sweep names them by absence and sweeps what stands at their sites instead --
    # `_wire_for` (the one maker clamp, where both takes priced), `_place` (the fail-closed
    # IOC guard), `_rest_reread` (the touch-bound re-read at _ioc_reread's site) and
    # `_flatten_vanished` (the flatten's rest, where _flatten_send's slippage leg was)
    for gone in ("_entry_take", "_exit_take", "_flatten_send", "_ioc_reread", "_exit_band_take"):
        assert not hasattr(ml, gone), gone
    for fn in (ml._act, ml._place_reserved, ml._wire_for, ml._place, ml._rest_reread,
               ml._flatten_vanished, ml._tick_candidate, ml._fast_gate,
               ml._maybe_close_episode, ml._tick_book, ml._fast_book, ml._frozen_exit):
        s = "\n".join(ln for ln in inspect.getsource(fn).splitlines() if not ln.lstrip().startswith("#"))
        for word in ('get("rest_cause")', '["rest_cause"]:', "rest_id", 'get("cause")', '["cause"]', 'get("fast")',
                     '["fast"]', "fill_cols", "_REST_STOOD_NAMES", "fill_rows"):
            assert word not in s.replace('plan["rest_cause"] =', ""), (fn.__name__, word)
    for word in ("rest_cause", "fill_cols", "fast_col", "rest_id", "fill_answer"):
        assert word not in inspect.getsource(rules), word
    assert "MIRROR_FILL" not in src and "MIRROR_FAST_COL" not in src and "MIRROR_REST_CAUSE" not in src
    # the row: the three read off the ENTRY, never off the tick's plan; junk is NULL
    t = ml._Tick(pool=None, pmus=None, http=None, now=NOW, stats=ml._new_stats(), started=0.0)
    t.seq = 3
    book = {"id": 9, "whale": "rn1", "condition_id": CID}
    e = {"id": "1", "ts": 1.0, "det": 2.0, "at": 3.0, "order": None, "name": "open_order_pending",
         "cause": "same", "rest": 41, "fast": True}
    r = ml._fill_answer_row(t, book, e)
    assert (r["cause"], r["rest_id"], r["fast"]) == ("same", 41, True)
    r = ml._fill_answer_row(t, book, {**e, "cause": 7, "rest": "x", "fast": "yes"})
    assert (r["cause"], r["rest_id"], r["fast"]) == (None, None, None)
    r = ml._fill_answer_row(t, book, {"id": "1", "at": 3.0, "name": "on_target"})
    assert (r["cause"], r["rest_id"], r["fast"]) == (None, None, None), "a pre-061 entry writes NULL"
    # `_fills_seen` reads the plan handed in; without one, None / None / the tick's path
    b = {"id": 9, "whale": "rn1", "condition_id": CID, "last_plan": None, "_fills": [_fill(M, "BUY", 1.0, 0.3, 100.0)]}
    out = ml._fills_seen(t, b, "open_order_pending", plan={"rest_cause": "min_life", "open_order": 41})
    assert (out[0]["cause"], out[0]["rest"], out[0]["fast"]) == ("min_life", 41, False)
    out = ml._fills_seen(t, b, "rest_placed", plan={"rest_cause": "min_life", "open_order": 41})
    assert (out[0]["cause"], out[0]["rest"]) == (None, 41), "rest_placed names no cause but still the rest it replaced"
    out = ml._fills_seen(t, b, "open_order_pending", plan={"rest_cause": 5, "open_order": "junk"})
    assert (out[0]["cause"], out[0]["rest"]) == (None, None)
    assert ml._fills_seen(t, b, "open_order_pending")[0]["cause"] is None
    # a FROZEN plan names its fills under the freeze's word: the cause rides beside it; a live plan under
    # another word carries none even with a stale-looking stamp
    out = ml._fills_seen(t, b, "placement_lost", plan={"rest_cause": "frozen", "open_order": 41, "kind": "frozen"})
    assert (out[0]["cause"], out[0]["rest"]) == ("frozen", 41)
    assert ml._fills_seen(t, b, "placement_lost", plan={"rest_cause": "frozen", "open_order": 41})[0]["cause"] is None
    # the keep branch stamps at its RETURN: the stamp is the last statement before
    # `return "open_order_pending"`. E31: 3 -> 2 stamps, because the exit rest's arm and the
    # cover rest's arm merged into one `priced_exit` arm (both hold the same maker rest at
    # his cent); the entry's own stamp is the second and last
    stamps = [m.start() for m in re.finditer(re.escape('plan["rest_cause"] = _rest_cause(book, plan, why)'), act)]
    assert len(stamps) == 2
    for i in stamps:
        assert act[i:].split("\n")[1].strip() == 'return "open_order_pending"', "every stamp sits on a keep return"
    # E31: `take_at_his_level` was the entry take's own name and is retired with the arm, so
    # the ordering is pinned on what stands at that site -- `resting_above_level`, the record
    # that the market never came to his level, which under a maker is the NORMAL state of the
    # rest the entry's stamp then names
    assert '_mirror_stop("take_at_his_level", w)' not in act
    assert act.index('_mirror_stop("resting_above_level", w)') < stamps[-1], "the entry's stamp after the wait record"
    t.fast = True
    assert ml._fills_seen(t, b, "on_target")[0]["fast"] is True
    # 059's decision list stands: this lane writes no word
    assert "order_decision" not in inspect.getsource(ml._fills_seen) and "cause" not in inspect.getsource(rules.order_decision)


def test_c9_every_heartbeat_key_is_emitted_and_the_docs_name_the_rule():
    doc = (ROOT / "docs" / "mirror-coverage.md").read_text()
    m = re.search(r"^## (\d+)\. The record's columns \(2026-09-09, FILL lane 9\)", doc, re.M)
    assert m, "the section header"
    sec = doc[m.start():]
    for k in HEARTBEAT + CAUSES + ("rest_cause", "rest_id", "061", "_fill_cause_guard", "_fast_col_guard", "_order_cols_guard",
                                   "_SQL_FILL_ANSWERS_061", "_SQL_ORDER_INSERT_061", "his_to_first_order", "keep_to_replace",
                                   "fills-missed", "fill-answers", "latency-census", "test_fill_c9_record_cols.py",
                                   "$12,914.02", "$44,884.07", "760", "347", "4688", "32 s", "missed_replace",
                                   "information_schema", "unrecorded", "sportsassets-db"):
        assert k in sec, k
    assert "NO order path" in sec and "$0" in sec


# --------------------------------------------------------- (8) the presets

def test_c9_the_presets_read_the_columns_guarded_and_the_hourly_is_still_nine():
    text = YML.read_text()
    fm, _ = _preset(text, "fills-missed")
    st = _stmts(fm)
    # the chain's fa (to_regclass + its inner query), fc's own two and fc's column-existence test: five namings
    assert len(st) == 7 and st[6].count("mirror_fill_answers") == 5
    assert "COALESCE(fc.cause, 'unrecorded') AS cause, COALESCE(fc.fast::text, 'unrecorded') AS path" in st[6]
    assert "column_name = 'cause') THEN '<table/>'::xml ELSE query_to_xml('SELECT fill_id, cause, fast FROM mirror_fill_answers" in st[6]
    # the (class, decision) block's WHERE is lane 8's to widen (it landed ahead: missed_replace is its word);
    # this lane leaves the word list as it found it
    assert st[2].endswith(" FROM g WHERE class IN ('filled', 'partial', 'missed_expired_ioc', 'missed_replace') GROUP BY 1, 2 ORDER BY 1, 4 DESC")
    fa, to = _preset(text, "fill-answers")
    assert to == 60000 and len(_stmts(fa)) == 2
    assert "left(c.causes, 60) AS causes" in fa and "LEFT JOIN c ON c.condition_id = b.condition_id" in fa
    assert ("c AS (SELECT x.condition_id, string_agg(x.cause || '=' || x.n, ' ' ORDER BY x.n DESC, x.cause) AS causes FROM xmltable("
            "'/table/row' PASSING (CASE WHEN NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'mirror_fill_answers'"
            " AND column_name = 'cause') THEN '<table/>'::xml ELSE query_to_xml(") in fa
    lc, to = _preset(text, "latency-census")
    assert to == 60000
    ls = _stmts(lc)
    assert len(ls) == 6
    FP = ("WITH fp AS (SELECT x.id, x.fast FROM xmltable('/table/row' PASSING (CASE WHEN NOT EXISTS (SELECT 1 FROM"
          " information_schema.columns WHERE table_name = 'mirror_orders' AND column_name = 'fast') THEN '<table/>'::xml ELSE"
          " query_to_xml('SELECT id, fast FROM mirror_orders WHERE fast IS NOT NULL AND placed_at >= now() - interval ''6 hours''',"
          " false, false, '') END) COLUMNS id bigint PATH 'id', fast boolean PATH 'fast') x), o AS (")
    assert all(s.startswith(FP) for s in ls[:2]) and ls[3].startswith(FP)
    assert all("o.his_fill_id, COALESCE(fp.fast::text, 'unrecorded') AS path FROM mirror_orders o JOIN mirror_books b ON b.id = o.book_id"
               " LEFT JOIN fp ON fp.id = o.id" in s for s in (ls[0], ls[1], ls[3]))
    assert " SELECT date_trunc('hour', placed_at)::time(0) AS hour, kind, path, count(*) AS n, " in ls[0]
    assert ls[0].endswith(" FROM o WHERE his_ts IS NOT NULL GROUP BY 1, 2, 3 ORDER BY 1 DESC, 2, 3")
    assert "pf AS (SELECT DISTINCT ON (o.book_id, o.his_fill_id) o.book_id, o.his_fill_id, o.kind, o.path, o.placed_at AS first_order," in ls[1]
    # the trades row by its PRIMARY KEY (an Index Scan on trades_pkey), the cast guarded at run time so a stamp-keyed
    # or junk his_fill_id reads NULL and never raises -- never `t.id::text = o.his_fill_id`, a sequential scan of
    # trades per order row (~300 rows per 6 h window at 20-22Z: h2225 835-841) inside the 60 s / 120 s timeouts
    assert ("(SELECT t.ts FROM trades t WHERE t.id = CASE WHEN o.his_fill_id !~ '[^0-9]' AND length(o.his_fill_id) BETWEEN 1 AND 18"
            " THEN o.his_fill_id::bigint END) AS fill_ts FROM o WHERE o.his_fill_id IS NOT NULL") in ls[1]
    assert "t.id::text" not in ls[1]
    assert "count(DISTINCT (book_id, his_fill_id)) AS fills" in ls[1] and "AS his_to_first_order_med_s" in ls[1]
    assert "AS his_to_first_order_p90_s FROM pf WHERE fill_ts IS NOT NULL GROUP BY 1, 2, 3 ORDER BY 1 DESC, 2, 3" in ls[1]
    assert ls[2].startswith("WITH fr AS (SELECT x.fill_id, x.fill_ts, x.book_id, x.rest_id, x.cause FROM xmltable(")
    assert "column_name = 'rest_id') THEN '<table/>'::xml ELSE query_to_xml('SELECT fill_id, fill_ts, book_id, rest_id, cause FROM mirror_fill_answers" in ls[2]
    assert "LEAD(o.id) OVER (PARTITION BY o.book_id ORDER BY o.placed_at, o.id) AS next_id" in ls[2]
    assert "AS keep_to_replace_med_s" in ls[2] and "AS keep_to_replace_p90_s FROM fr LEFT JOIN nx ON nx.id = fr.rest_id AND nx.book_id = fr.book_id" in ls[2]
    # by CAUSE alone -- at most one row per cause word -- never by hour: the hourly bundle runs under the runner's
    # 1,500-line cap (HEAD=1500; h2225 at 1,270 lines before this lane) and the sections cut first are take-band
    # and on-target-why, lanes 8 and 10's judges
    assert " SELECT COALESCE(fr.cause, 'unrecorded') AS cause, count(*) AS fills_behind_rest, " in ls[2]
    assert ls[2].endswith(" FROM fr LEFT JOIN nx ON nx.id = fr.rest_id AND nx.book_id = fr.book_id GROUP BY 1 ORDER BY 1")
    assert "date_trunc" not in ls[2] and "to_timestamp(fr.fill_ts)" not in ls[2]
    assert " SELECT id, book_id AS book, slug, kind, path, side, tif, state, filled, qty, placed_at::time(0) AS placed," in ls[3]
    assert ls[4].startswith("SELECT date_trunc('hour', t.ts)::time(0) AS hour, count(*) AS his_fills,")
    assert ls[5].startswith("WITH f AS (SELECT t.condition_id, t.ts, t.detected_at FROM")
    for sql in (fm, fa, lc):
        for bad in ("INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "need_confirm", "$ARG"):
            assert bad not in sql, bad
    pglast = pytest.importorskip("pglast")
    for sql in (fm, fa, lc):
        pglast.parse_sql(sql)
    # the hourly is still the nine presets, regenerated -- its own pins, re-run here
    hourly.test_the_hourly_preset_is_the_nine_presets_sql_joined_under_section_markers()
    hourly.test_the_hourly_preset_is_read_only_with_its_own_output_cap_and_timeout()
    hourly.test_the_help_line_is_the_case_labels_with_hourly_last()
    h, _ = _preset(text, "hourly")
    # `AS path`: latency-census's o CTE in its three carriers (by row, per fill, the rows line) and fills-missed's cause block
    assert h.count("AS keep_to_replace_med_s") == 1 and h.count("AS his_to_first_order_med_s") == 1 and h.count("AS path") == 4
    # the case labels: nothing added, hourly last, the fill-answers label still before it
    line = next(ln for ln in text.splitlines() if ln.lstrip().startswith('*) echo "sql: arg must be one of'))
    names = line.split("one of ", 1)[1].split(" (got", 1)[0].split("|")
    assert names[-1] == "hourly" and names[-2] == "fill-answers" and "latency-census" in names


# ------------------------------------------------ (9) the scratch database

# Book 760's shape as book 87 on c760 (h2225 983-985): his three BUYs of
# 2,983 at -2 h, -2 h + 93 s and -2 h + 300 s; one rest 7601 (placed
# -2 h - 60 s, cancelled `replace` / replace_qty at -2 h + 305 s) and its
# replacement 7602 (placed -2 h + 305 s, open); the record: fill 1
# `open_order_pending` cause same, fill 2 `open_order_pending` cause
# min_life, fill 3 `rest_placed` order 7602 -- every row rest_id 7601,
# fast false. 7602 sits more than 120 s after fills 1 and 2, so lane 4's
# window never keys them to it. The 4688 chain as book 88 on c829 (h2225
# 883 / 878 / 874 / 868): his SELL of 920 at -1 h (trades row 4688001),
# four SELL_LONG increase rows 32 / 53 / 192 / 287 s after it, every one
# answering his_fill_id '4688001', fast false, three cancelled `replace`
# and the last filled 92.
FIXTURE_L9 = """
INSERT INTO whales (id, address, username) VALUES (99, '0xrn1-l9', 'RN1');
INSERT INTO markets (condition_id, title, slug, event_title, sport, resolved_prices, resolved) VALUES
 ('c760', 'BVB v Villarreal exact score', 'ucl-bvb-vil-2026-09-08-exact-s', 'ev', 'soccer', NULL, false),
 ('c829', 'Libertad v Fluminense 3pt', 'tsc-lib-flu-cpa-2026-09-08-3pt', 'ev', 'soccer', NULL, false);
INSERT INTO market_tokens (token_id, condition_id, outcome, outcome_index) VALUES
 ('L760', 'c760', '1-0', 0), ('O760', 'c760', 'other', 1), ('L829', 'c829', 'over', 0), ('O829', 'c829', 'under', 1);
INSERT INTO trades (id, whale_id, tx_hash, asset, condition_id, side, size, price, notional, market_slug, sport, ts, source, detected_at, dedupe_key) VALUES
 (7600001, 99, '0xl9a1', 'L760', 'c760', 'BUY', 2983, 0.823, 2455.01, 'ucl-bvb-vil-2026-09-08-exact-s', 'soccer', now() - interval '2 hours', 'chain', now() - interval '2 hours' + interval '1 second', 'l9a1'),
 (7600002, 99, '0xl9a2', 'L760', 'c760', 'BUY', 2983, 0.824, 2457.99, 'ucl-bvb-vil-2026-09-08-exact-s', 'soccer', now() - interval '2 hours' + interval '93 seconds', 'chain', now() - interval '2 hours' + interval '94 seconds', 'l9a2'),
 (7600003, 99, '0xl9a3', 'L760', 'c760', 'BUY', 2983, 0.823, 2455.01, 'ucl-bvb-vil-2026-09-08-exact-s', 'soccer', now() - interval '2 hours' + interval '300 seconds', 'chain', now() - interval '2 hours' + interval '301 seconds', 'l9a3'),
 (4688001, 99, '0xl9b1', 'L829', 'c829', 'SELL', 920, 0.44, 404.8, 'tsc-lib-flu-cpa-2026-09-08-3pt', 'soccer', now() - interval '1 hour', 'chain', now() - interval '1 hour' + interval '1 second', 'l9b1');
INSERT INTO mirror_books (id, whale, condition_id, us_market_slug, long_asset, other_asset, intent, ratio, state, target, target_raw, his_net, ledger_net, last_plan, peak_exposure_usd, avg_cost, settled_pnl, opened_at, closed_at, last_reason, flow_base) VALUES
 (87, 'rn1', 'c760', 'ucl-bvb-vil-2026-09-08-exact-s', 'L760', 'O760', 'ORDER_INTENT_BUY_LONG', 0.1, 'live', 895, 894.9, 8949, 597, '{"his_fills_seen": [], "fills_hwm": 1757000000.5}'::jsonb, 736.8, 0.823, NULL, now() - interval '3 hours', NULL, 'rest_placed', 0),
 (88, 'rn1', 'c829', 'tsc-lib-flu-cpa-2026-09-08-3pt', 'L829', 'O829', 'ORDER_INTENT_BUY_LONG', 0.1, 'live', 0, 0.0, 0, 0, '{"his_fills_seen": []}'::jsonb, 40.5, 0.44, NULL, now() - interval '3 hours', NULL, 'rest_placed', 0);
INSERT INTO mirror_orders (id, book_id, whale, us_market_slug, kind, side, tif, his_level, price, wire, qty, state, filled, avg_px, bid_at_place, ask_at_place, placed_at, done_at, reason, decision, his_fill_id, fast) VALUES
 (7601, 87, 'rn1', 'ucl-bvb-vil-2026-09-08-exact-s', 'increase', 'BUY_LONG', 'GTC', 0.823, 0.82, 0.82, 298, 'cancelled', 0, NULL, 0.82, 0.84, now() - interval '2 hours' - interval '60 seconds', now() - interval '2 hours' + interval '305 seconds', 'replace', 'replace_qty', NULL, false),
 (7602, 87, 'rn1', 'ucl-bvb-vil-2026-09-08-exact-s', 'increase', 'BUY_LONG', 'GTC', 0.823, 0.82, 0.82, 895, 'open', 0, NULL, 0.82, 0.84, now() - interval '2 hours' + interval '305 seconds', NULL, 'increase', 'rest', '7600003', false),
 (4688, 88, 'rn1', 'tsc-lib-flu-cpa-2026-09-08-3pt', 'increase', 'SELL_LONG', 'GTC', 0.44, 0.44, 0.44, 92, 'cancelled', 0, NULL, 0.43, 0.45, now() - interval '1 hour' + interval '32 seconds', now() - interval '1 hour' + interval '50 seconds', 'replace', 'replace_cent', '4688001', false),
 (4693, 88, 'rn1', 'tsc-lib-flu-cpa-2026-09-08-3pt', 'increase', 'SELL_LONG', 'GTC', 0.44, 0.45, 0.45, 92, 'cancelled', 0, NULL, 0.44, 0.46, now() - interval '1 hour' + interval '53 seconds', now() - interval '1 hour' + interval '190 seconds', 'replace', 'replace_cent', '4688001', false),
 (4697, 88, 'rn1', 'tsc-lib-flu-cpa-2026-09-08-3pt', 'increase', 'SELL_LONG', 'GTC', 0.44, 0.44, 0.44, 92, 'cancelled', 0, NULL, 0.43, 0.45, now() - interval '1 hour' + interval '192 seconds', now() - interval '1 hour' + interval '285 seconds', 'replace', 'replace_cent', '4688001', false),
 (4704, 88, 'rn1', 'tsc-lib-flu-cpa-2026-09-08-3pt', 'increase', 'SELL_LONG', 'GTC', 0.44, 0.44, 0.44, 92, 'filled', 92, 0.44, 0.43, 0.45, now() - interval '1 hour' + interval '287 seconds', now() - interval '1 hour' + interval '390 seconds', 'increase', 'rest', '4688001', false);
INSERT INTO mirror_fill_answers (whale, condition_id, fill_id, fill_ts, detected_at, at, book_id, order_id, name, tick, cause, rest_id, fast) VALUES
 ('rn1', 'c760', '7600001', extract(epoch FROM now() - interval '2 hours'), extract(epoch FROM now() - interval '2 hours') + 1, extract(epoch FROM now() - interval '2 hours') + 3, 87, NULL, 'open_order_pending', 2001, 'same', 7601, false),
 ('rn1', 'c760', '7600002', extract(epoch FROM now() - interval '2 hours') + 93, extract(epoch FROM now() - interval '2 hours') + 94, extract(epoch FROM now() - interval '2 hours') + 119, 87, NULL, 'open_order_pending', 2004, 'min_life', 7601, false),
 ('rn1', 'c760', '7600003', extract(epoch FROM now() - interval '2 hours') + 300, extract(epoch FROM now() - interval '2 hours') + 301, extract(epoch FROM now() - interval '2 hours') + 305, 87, 7602, 'rest_placed', 2010, NULL, 7601, false),
 ('rn1', 'c829', '4688001', extract(epoch FROM now() - interval '1 hour'), extract(epoch FROM now() - interval '1 hour') + 1, extract(epoch FROM now() - interval '1 hour') + 32, 88, 4688, 'rest_placed', 2200, NULL, NULL, false);
"""


@pytest.fixture(scope="module")
def world():
    w = World(FIXTURE_L9, "FILL lane 9")
    try:
        yield w
    finally:
        w.close()


def _f(v):
    return None if v is None else float(v)


def test_c9_061_applies_twice_on_a_real_postgres_and_the_four_columns_are_nullable_with_no_default(world):
    world.run(SQL_061.read_text())
    world.run(SQL_061.read_text())          # idempotent, on top of the migration glob's own application
    cols = world.rows("SELECT table_name, column_name, data_type, is_nullable, column_default FROM information_schema.columns"
                      " WHERE (table_name = 'mirror_fill_answers' AND column_name IN ('cause', 'rest_id', 'fast'))"
                      " OR (table_name = 'mirror_orders' AND column_name = 'fast') ORDER BY table_name, ordinal_position")
    assert [tuple(c.values()) for c in cols] == [
        ("mirror_fill_answers", "cause", "text", "YES", None), ("mirror_fill_answers", "rest_id", "bigint", "YES", None),
        ("mirror_fill_answers", "fast", "boolean", "YES", None), ("mirror_orders", "fast", "boolean", "YES", None)]
    assert world.rows(ml._SQL_FILL_CAUSE_GUARD) == [] and world.rows(ml._SQL_FAST_COL_GUARD) == []
    # the 061 INSERT writes the three; the 060 one on the same table leaves them NULL; the conflict clause keeps the first
    rows = [{"whale": "rn1", "condition_id": "c760", "fill_id": "9001", "fill_ts": 1757000000.0, "detected_at": None,
             "at": 1757000010.0, "book_id": 87, "order_id": None, "name": "open_order_pending", "tick": 7,
             "cause": "same", "rest_id": 7601, "fast": True}]
    assert world.loop.run_until_complete(world.conn.execute(ml._SQL_FILL_ANSWERS_061, json.dumps(rows))) == "INSERT 0 1"
    assert world.loop.run_until_complete(world.conn.execute(ml._SQL_FILL_ANSWERS, json.dumps([dict(rows[0], fill_id="9002")]))) == "INSERT 0 1"
    assert world.loop.run_until_complete(world.conn.execute(ml._SQL_FILL_ANSWERS_061, json.dumps([dict(rows[0], cause="min_life")]))) == "INSERT 0 0"
    got = world.rows("SELECT fill_id, cause, rest_id, fast FROM mirror_fill_answers WHERE fill_id IN ('9001', '9002') ORDER BY fill_id")
    assert [tuple(r.values()) for r in got] == [("9001", "same", 7601, True), ("9002", None, None, None)]
    world.run("DELETE FROM mirror_fill_answers WHERE fill_id IN ('9001', '9002')")


def test_c9_fills_missed_prints_book_760s_causes_and_fill_answers_prints_them_per_market(world):
    text = YML.read_text()
    fm, _ = _preset(text, "fills-missed")
    st = _stmts(fm)
    for s in st:
        world.rows(s)
    world.run(fm)
    by = {(r["cause"], r["path"]): r for r in world.rows(st[6])}
    assert set(by) == {("same", "false"), ("min_life", "false")}
    assert by[("same", "false")]["n"] == 1 and _f(by[("same", "false")]["his_usd"]) == pytest.approx(2455.01, abs=0.01)
    assert by[("min_life", "false")]["n"] == 1 and _f(by[("min_life", "false")]["his_usd"]) == pytest.approx(2457.99, abs=0.01)
    assert all(r["n_resolved"] == 0 and r["roi"] is None for r in by.values()), "347's shadow: unresolved, no roi"
    # the same fills in the class rollup: two refused:open_order_pending, the third off its record's order
    cls = {r["class"]: r for r in world.rows(st[0])}
    assert cls["refused:open_order_pending"]["n"] == 2 and cls["missed_open"]["n"] == 1
    # lane 8's word is NOT here: the (class, decision) block still reads lane 0b's three classes
    dec = {(r["class"], r["decision"]) for r in world.rows(st[2])}
    assert not any(c == "missed_replace" for c, _ in dec)
    fa, _ = _preset(text, "fill-answers")
    per = {r["book"]: r for r in world.rows(_stmts(fa)[0])}
    assert per[87]["causes"] == "min_life=1 same=1" and per[87]["written"] == 3 and per[87]["with_order"] == 1
    assert per[88]["causes"] is None
    world.run(fa)


def test_c9_latency_census_reads_the_4688_chain_as_one_fill_at_32_s_and_keep_to_replace_by_cause(world):
    text = YML.read_text()
    lc, _ = _preset(text, "latency-census")
    ls = _stmts(lc)
    for s in ls:
        world.rows(s)
    world.run(lc)
    # the by-row line (summed over the hour buckets: the fixture's clocks are relative to now()): four chain rows,
    # each measured against the one fill (32 / 53 / 192 / 287 s), and 7602 -- five rows, every one a full tick's
    rows = world.rows(ls[0])
    assert sum(r["n"] for r in rows if (r["kind"], r["path"]) == ("increase", "false")) == 5 and {r["path"] for r in rows} == {"false"}
    # the per-fill line: the chain is ONE fill, first answered at 32 s (c760's fill 3 through 7602 is the other)
    per = world.rows(ls[1])
    assert sum(r["fills"] for r in per if (r["kind"], r["path"]) == ("increase", "false")) == 2 and sum(r["fills"] for r in per) == 2
    chain = world.rows(ls[1].replace(" FROM pf WHERE fill_ts IS NOT NULL GROUP BY 1, 2, 3 ORDER BY 1 DESC, 2, 3",
                                     " FROM pf WHERE fill_ts IS NOT NULL AND book_id = 88 GROUP BY 1, 2, 3 ORDER BY 1 DESC, 2, 3"))
    assert len(chain) == 1 and chain[0]["fills"] == 1 and (chain[0]["kind"], chain[0]["path"]) == ("increase", "false")
    assert _f(chain[0]["his_to_first_order_med_s"]) == 32.0 and _f(chain[0]["his_to_first_order_p90_s"]) == 32.0
    # keep_to_replace per fill by rest_id: 7601's successor is 7602 at -2 h + 305 s -- 305 s after fill 1 (same),
    # 212 s after fill 2 (min_life), 5 s after fill 3 (rest_placed: no cause, 'unrecorded')
    ktr = world.rows(ls[2])
    by_cause = {}
    for r in ktr:
        by_cause.setdefault(r["cause"], []).append(r)
    assert set(by_cause) == {"same", "min_life", "unrecorded"} and all(len(v) == 1 for v in by_cause.values())
    assert (by_cause["same"][0]["fills_behind_rest"], by_cause["same"][0]["replaced"]) == (1, 1)
    assert _f(by_cause["same"][0]["keep_to_replace_med_s"]) == 305.0 and _f(by_cause["min_life"][0]["keep_to_replace_med_s"]) == 212.0
    assert _f(by_cause["unrecorded"][0]["keep_to_replace_med_s"]) == 5.0 and by_cause["unrecorded"][0]["replaced"] == 1
    # the rows line carries the path (7601, placed before any fill of his on c760, has no his_ts: out, as today)
    detail = world.rows(ls[3])
    assert all(r["path"] == "false" for r in detail) and {r["id"] for r in detail} == {7602, 4688, 4693, 4697, 4704}


def test_c9_without_061_every_preset_still_runs_and_prints_unrecorded(world):
    """The minutes between a deploy and the boot that applies 061: the
    four columns dropped -> fills-missed (and the hourly's copy),
    fill-answers and latency-census run, the cause block and the path
    read 'unrecorded', the per-fill line is unchanged (059's his_fill_id
    is not this lane's), keep_to_replace prints nothing; then 061
    applied twice puts the columns back."""
    text = YML.read_text()
    world.run("ALTER TABLE mirror_fill_answers DROP COLUMN cause, DROP COLUMN rest_id, DROP COLUMN fast")
    world.run("ALTER TABLE mirror_orders DROP COLUMN fast")
    try:
        for name in ("fills-missed", "fill-answers", "latency-census", "hourly"):
            sql, _ = _preset(text, name)
            world.run(sql)
        fm = _stmts(_preset(text, "fills-missed")[0])
        by = {(r["cause"], r["path"]): r["n"] for r in world.rows(fm[6])}
        assert by == {("unrecorded", "unrecorded"): 2}
        fa = _stmts(_preset(text, "fill-answers")[0])
        assert all(r["causes"] is None for r in world.rows(fa[0])) and world.rows(fa[1])[0]["rows_24h"] == 4
        ls = _stmts(_preset(text, "latency-census")[0])
        assert {r["path"] for r in world.rows(ls[0])} == {"unrecorded"}
        assert sum(r["fills"] for r in world.rows(ls[1])) == 2 and world.rows(ls[2]) == []
        # the worker's probes read absence the way rules.column_missing reads it
        for stmt, col in ((ml._SQL_FILL_CAUSE_GUARD, "cause"), (ml._SQL_FAST_COL_GUARD, "fast")):
            try:
                world.rows(stmt)
            except Exception as exc:  # noqa: BLE001 -- the absence, as Postgres answers it
                assert rules.column_missing(exc, col) and type(exc).__name__ == "UndefinedColumnError", exc
            else:
                raise AssertionError("the probe read a column just dropped")
        assert world.rows(ml._SQL_ORDER_COLS_GUARD) == [], "059's probe still reads present: an absent `fast` is never 059's absence"
    finally:
        world.run(SQL_061.read_text())
        world.run(SQL_061.read_text())
    assert world.rows(ml._SQL_FILL_CAUSE_GUARD) == [] and world.rows(ml._SQL_FAST_COL_GUARD) == []
