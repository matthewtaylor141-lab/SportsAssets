"""The `flow-books` render-ops preset (E12, 2026-09-08): the books opened
in the last 6 h with the block (flow_base), the fills' net the block was
read at (flow_last_net), the open's catch-up verdict and the ratchet's
reading verdict off the plan row, then a census by verdict with the
NULL-block (old-rule) books counted apart; since the PNL program's lane M
(2026-09-08) `catchup_side` = better | worse | unread (the open's mark
against his vwap on the book's axis, a short mirrored) on every row and
a third statement with the block's dollars per side, so lane 1's
at-or-better share is read before it lands. The pins: the three
statements and their columns, read-only, its place after `books-new` in
the case block and the help line (the E8 / hourly order pins untouched).
"""
from __future__ import annotations

import re
from pathlib import Path

YML = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "render-ops.yml"


def _preset(text: str, name: str) -> tuple[str, int]:
    m = re.search(r'^ {16}' + re.escape(name) + r'\) SQL="(.*?)"; TO=(\d+)', text, re.M | re.S)
    assert m, name
    return m.group(1), int(m.group(2))


def test_flow_books_reads_the_block_the_reference_and_both_verdicts_off_the_row():
    sql, to = _preset(YML.read_text(), "flow-books")
    stmts = [s.strip() for s in sql.split(";") if s.strip()]
    assert len(stmts) == 3 and to == 30000
    rows, census, sides = stmts
    for col in ("b.flow_base", "b.flow_last_net", "b.his_net", "b.ledger_net AS held", "b.avg_cost",
                "b.last_plan->'catchup'", "b.last_plan->'flow_reading'", "b.last_plan->>'flow_wait'",
                "b.last_plan->>'flow_net'", "b.opened_at::time(0) AS opened", "b.last_reason"):
        assert col in rows, col
    assert "WHERE b.opened_at > now() - interval '6 hours'" in rows and "ORDER BY b.opened_at DESC LIMIT 60" in rows
    assert census.startswith("SELECT (b.flow_base IS NOT NULL) AS has_block, COALESCE(b.last_plan->'catchup'->>'why', 'none') AS why, count(*) AS n")
    assert "sum(b.flow_base)" in census and "sum(b.his_net)" in census and "sum(b.ledger_net)" in census
    assert "WHERE b.opened_at > now() - interval '6 hours' GROUP BY 1, 2 ORDER BY 1, 2" in census
    # lane M: the side is read off the plan's own catchup {mark, vwap}; either missing is 'unread', never a guess
    side = ("CASE WHEN (b.last_plan->'catchup'->>'mark') IS NULL OR (b.last_plan->'catchup'->>'vwap') IS NULL THEN 'unread' "
            "WHEN (b.intent = 'ORDER_INTENT_BUY_SHORT' AND (b.last_plan->'catchup'->>'mark')::float8 >= (b.last_plan->'catchup'->>'vwap')::float8) "
            "OR (b.intent <> 'ORDER_INTENT_BUY_SHORT' AND (b.last_plan->'catchup'->>'mark')::float8 <= (b.last_plan->'catchup'->>'vwap')::float8) "
            "THEN 'better' ELSE 'worse' END AS catchup_side")
    assert side in rows and rows.index(side) > rows.index("AS catchup, ")
    assert sides.startswith("SELECT " + side + ", COALESCE(b.last_plan->'catchup'->>'why', 'none') AS why, count(*) AS n")
    for col in ("AS block_shares", "AS block_usd_at_his_vwap", "AS our_share_usd_uncapped", "AS mark_minus_vwap_avg_c"):
        assert col in sides, col
    assert "AND b.flow_base IS NOT NULL AND b.flow_base <> 0 GROUP BY 1, 2 ORDER BY 1, 2" in sides


def test_flow_books_is_read_only_and_sits_after_books_new_with_the_order_pins_untouched():
    text = YML.read_text()
    sql, _ = _preset(text, "flow-books")
    for bad in ("INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "need_confirm", "$ARG"):
        assert bad not in sql, bad
    line = next(ln for ln in text.splitlines() if ln.lstrip().startswith('*) echo "sql: arg must be one of'))
    names = line.split("one of ", 1)[1].split(" (got", 1)[0].split("|")
    labels = re.findall(r"^ {16}([a-z0-9-]+)\) ", text[text.index('case "$ARG" in'):text.index(line)], re.M)
    assert names == labels
    assert names.index("flow-books") == names.index("books-new") + 1
    assert names.index("exits-paired") == names.index("nf-venue") + 1 and names[-1] == "hourly"
    assert "books-new|flow-books|nf-his|" in line
    assert "nf-venue|exits-paired|take-band|exits-band|closed-while-he-traded|fill-answers|hourly (got" in line   # FILL lane 0b's three
    hourly, _ = _preset(text, "hourly")
    assert "catchup_side" not in hourly and "flow_last_net" not in hourly, "the hourly line is its presets' SQL and no more"
