"""The `traded-day` render-ops preset (2026-09-08; the owner's question
"How many dollars have we traded today in our mirror trades"): the
day's turnover from the mirror's own order rows -- every FILLED row of
the UTC day at the venue's returned average (the wire when none was
returned), split by the long token bought (BUY_LONG) and sold
(SELL_LONG), then by kind x side and by hour. Three read-only
statements on their own timeout; the label sits after verify-day (the
pinned help-line run `...|on-target-why|verify-day|` stands) and before
premap-rows; hourly stays last and does not carry it.
"""
from __future__ import annotations

import re
from pathlib import Path

import pglast

YML = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "render-ops.yml"
USD = "o.filled * COALESCE(o.avg_px, o.wire)"
WHERE = "o.whale = 'rn1' AND o.filled > 0 AND COALESCE(o.done_at, o.placed_at) >= date_trunc('day', now())"


def _preset(text: str, name: str) -> tuple[str, int]:
    m = re.search(r'^ {16}' + re.escape(name) + r'\) SQL="(.*?)"; TO=(\d+)', text, re.M | re.S)
    assert m, name
    return m.group(1), int(m.group(2))


def test_traded_day_is_three_read_only_statements_that_parse():
    text = YML.read_text()
    sql, to = _preset(text, "traded-day")
    assert to == 30000
    stmts = [s for s in sql.split(";") if s.strip()]
    assert len(stmts) == 3 and sql.endswith("ORDER BY 1;")
    for s in stmts:
        pglast.parse_sql(s)
        assert s.lstrip().startswith("SELECT ")
    start = text.index("traded-day) SQL=")
    block = text[start:text.index("\n", start)]    # the arm is one line, ending in its own timeout
    assert block.rstrip().endswith('"; TO=30000 ;;')
    assert "need_confirm" not in block and "$ARG" not in block and "HEAD=" not in block
    for bad in ("INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "ALTER"):
        assert bad not in block, bad


def test_traded_day_reads_the_days_filled_rows_at_the_returned_average():
    sql, _ = _preset(YML.read_text(), "traded-day")
    assert sql.count("FROM mirror_orders o WHERE " + WHERE) == 3, "every statement reads the same population"
    assert "AS traded_usd" in sql and "AS long_token_bought_usd" in sql and "AS long_token_sold_usd" in sql
    assert "CASE WHEN o.side = 'BUY_LONG' THEN " + USD + " ELSE 0 END" in sql
    assert "CASE WHEN o.side = 'SELL_LONG' THEN " + USD + " ELSE 0 END" in sql
    assert "count(DISTINCT o.book_id) AS books" in sql and "AS first_fill" in sql and "AS last_fill" in sql
    assert "SELECT o.kind, o.side, count(*) AS n" in sql and "GROUP BY 1, 2 ORDER BY 5 DESC, 1, 2" in sql
    assert "date_trunc('hour', COALESCE(o.done_at, o.placed_at))::time(0) AS hour" in sql
    # a JOIN would drop a row whose book vanished; the turnover reads the order rows alone
    assert "JOIN" not in sql


def test_traded_day_label_sits_after_verify_day_and_the_help_line_names_it():
    text = YML.read_text()
    i_v, i_t, i_p, i_h = (text.index(f"\n                {n}) SQL=") for n in ("verify-day", "traded-day", "premap-rows", "hourly"))
    assert i_v < i_t < i_p < i_h
    line = next(l for l in text.splitlines() if "sql: arg must be one of" in l)
    assert "|on-target-why|verify-day|traded-day|premap-rows|" in line
    assert line.count("traded-day") == 1
    hourly_sql, _ = _preset(text, "hourly")
    assert "traded-day" not in hourly_sql and "== traded-day" not in hourly_sql
