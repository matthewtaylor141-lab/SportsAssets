"""The `exits-paired` render-ops preset (E8 part 2, step 1, 2026-09-08):
on every market whose mirror book CLOSED in the last 24 h, did we leave
when he left, at his price? Owner: "we go down when he loses ... sells
(or exits) within 1c of his price". The pins: the preset is read-only,
LIMITed, on its own timeout; his rows are collapsed chain-first by the
paired-day preset's OWN text (verbatim) with its running net; the
24 h close window, the 10 % exit rule, the reduce keyed on the book's
intent and the row's plan side (a short's cover is its exit), the
verdicts in the brief's order behind the two unmeasured guards, the
stuck dollars worst first; two statements each carrying the whole CTE
chain (psql runs them one by one); the help line regenerated from the
case labels with `exits-paired` after `nf-venue`, `hourly` still last
and the hourly line untouched (its pins re-run here).
"""
from __future__ import annotations

import re
from pathlib import Path

from tests import test_render_ops_hourly as hourly

YML = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "render-ops.yml"
VERDICTS = ("no_exit_by_him", "exited_with_him", "partial_exit", "exit_placed_unfilled",
            "no_exit_order")
GUARDS = ("no_position", "his_fills_unseen")
STUCK = "stuck_after_his_exit_usd"


def _preset(text: str, name: str) -> tuple[str, int]:
    m = re.search(r'^ {16}' + re.escape(name) + r'\) SQL="(.*?)"; TO=(\d+)', text, re.M | re.S)
    assert m, name
    return m.group(1), int(m.group(2))


def _block(text: str) -> str:
    return text[text.index("exits-paired) SQL="):text.index("# THE HOURLY, IN ONE RUN")]


def _statements(sql: str) -> list[str]:
    """The preset's two statements, whole: each carries the CTE chain and
    ends with its own semicolon (psql runs them one by one)."""
    parts = sql.split("; WITH bk AS (")
    assert len(parts) == 2, "two statements, both on the chain"
    return [parts[0] + ";", "WITH bk AS (" + parts[1]]


def test_the_exits_paired_preset_is_read_only_limited_on_its_own_timeout():
    text = YML.read_text()
    block = _block(text)
    assert "need_confirm" not in block and "$ARG" not in block
    for bad in ("INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE"):
        assert bad not in block, bad
    sql, to = _preset(text, "exits-paired")
    assert to == 60000 and block.rstrip().endswith('"; TO=60000 ;;')
    assert "HEAD=" not in block, "the runner's default cap"
    rows, totals = _statements(sql)
    assert rows.startswith("WITH bk AS (") and rows.endswith("LIMIT 60;")
    assert rows.count("LIMIT ") == 1 and "LIMIT" not in totals
    assert totals.endswith("FROM v;")


def test_the_exits_paired_preset_reads_his_rows_by_the_paired_days_own_collapse():
    """His rows are the paired-day preset's read, byte for byte: the 48 h
    window, the chain-first collapse over (tx, asset, side), the running
    net `net_after` over (condition ORDER BY ts, id) -- lifted from that
    line, so an edit there that this preset does not carry fails here."""
    text = YML.read_text()
    pd, _ = _preset(text, "paired-day")
    start = pd.index("(SELECT d.*, sum((CASE WHEN upper(d.side)")
    end = pd.index(") t JOIN whales w ON w.id = t.whale_id JOIN market_tokens", start) + len(") t")
    collapse = pd[start:end]
    assert "interval '48 hours'" in collapse and "AS net_after" in collapse
    assert "bool_or(t.source IN ('chain', 's1')) OVER (PARTITION BY t.whale_id" in collapse
    assert collapse.endswith("AND d.condition_id IN (SELECT condition_id FROM bk)) t")
    sql, _ = _preset(text, "exits-paired")
    assert sql.count(collapse) == 2, "both statements carry the paired-day read verbatim"
    assert sql.count(" FROM " + collapse + " LEFT JOIN market_tokens mt ON mt.token_id = t.asset"
                     " LEFT JOIN markets m ON m.condition_id = t.condition_id)") == 2
    # his settled P&L is paired-day's formula, unresolved read NULL (no WHERE on resolved_prices)
    assert ("(CASE WHEN upper(t.side) = 'BUY' THEN 1 ELSE -1 END) * t.size"
            " * ((m.resolved_prices->>mt.outcome_index)::numeric - t.price) AS pnl") in sql
    assert "m.resolved_prices IS NOT NULL" not in sql


def test_the_exits_paired_preset_pins_the_window_the_exit_rule_and_the_side_keyed_reduce():
    text = YML.read_text()
    sql, _ = _preset(text, "exits-paired")
    rows, totals = _statements(sql)
    for stmt in (rows, totals):
        # the books: CLOSED inside 24 h, whale rn1, one row per condition (the orders read the same)
        assert stmt.count("WHERE b.whale = 'rn1' AND b.state = 'closed'"
                          " AND b.closed_at >= now() - interval '24 hours'") == 2
        assert "FROM mirror_books b WHERE b.whale = 'rn1'" in stmt and "GROUP BY b.condition_id)" in stmt
        # his peak: the first row reaching max |net|; his exit: the first later row at or under 10 %
        assert ("pk AS (SELECT DISTINCT ON (condition_id) condition_id, ts AS peak_ts, id AS peak_id,"
                " net_after AS peak_net, abs(net_after) AS peak FROM h"
                " ORDER BY condition_id, abs(net_after) DESC, ts, id)") in stmt
        assert "abs(h.net_after) <= 0.10 * pk.peak AS out" in stmt
        assert "WHERE (h.ts, h.id) > (pk.peak_ts, pk.peak_id)" in stmt
        assert ("((pk.peak_net > 0 AND h.side = 'SELL') OR (pk.peak_net < 0 AND h.side = 'BUY'))"
                " AS reducing") in stmt
        assert "min(hr.ts) FILTER (WHERE hr.reducing) AS exit_from" in stmt
        assert stmt.count("FILTER (WHERE hr.reducing AND (hr.ts, hr.id) <= (hx.exit_ts, hx.exit_id))") == 3
        # the dollars his reducing fills returned: the price on a long, its complement on a short
        assert "sum(hr.size * (CASE WHEN pk.peak_net < 0 THEN 1 - hr.price ELSE hr.price END))" in stmt
        # our reduce: keyed on the book's intent and the row's plan side (never the kind alone)
        assert ("(CASE WHEN b.intent = 'ORDER_INTENT_BUY_SHORT' THEN o.side = 'BUY_LONG'"
                " ELSE o.side = 'SELL_LONG' END) AS reducing") in stmt
        assert "o.kind" not in stmt
        # our peak: the running filled sum in placed order, its max
        assert ("sum(CASE WHEN reducing THEN -filled ELSE filled END)"
                " OVER (PARTITION BY condition_id ORDER BY placed_at, id) AS run") in stmt
        assert "max(run) AS our_peak" in stmt
        # our exit window: the reducing rows placed from his first reducing fill on, 10 s of skew
        assert ("(oo.reducing AND oo.placed_at >= hs.exit_from - interval '10 seconds')"
                " AS after_his") in stmt
        assert "min(placed_at) FILTER (WHERE after_his) AS our_exit_ts" in stmt
        assert "count(*) FILTER (WHERE after_his) AS our_exit_placed" in stmt
        assert "sum(filled) FILTER (WHERE after_his) AS our_exit_filled" in stmt
        assert "sum(avg_px * filled) FILTER (WHERE after_his AND filled > 0 AND avg_px IS NOT NULL)" in stmt
        # the pair: lag from his first reducing fill; cents his minus ours on a long, reversed on a short
        assert "round(extract(epoch FROM (ox.our_exit_ts - hs.exit_from))::numeric, 0) AS lag_s" in stmt
        assert ("(CASE WHEN hs.peak_net < 0 THEN ox.our_exit_px - hs.exit_px"
                " ELSE hs.exit_px - ox.our_exit_px END) * 100") in stmt
        assert "round((ox.our_exit_filled / NULLIF(op.our_peak, 0))::numeric, 2) AS our_exit_filled_pct" in stmt
        assert "(COALESCE(ox.our_exit_filled, 0) = 0 AND bk.settled_ok) AS held_to_settlement" in stmt
        assert "bool_and(b.settled_pnl IS NOT NULL) AS settled_ok" in stmt
        assert "CASE WHEN bk.staked > 0 THEN round(bk.settled / bk.staked, 3) END AS our_roi" in stmt


def test_the_exits_paired_preset_names_the_verdicts_in_order_and_puts_the_stuck_dollars_first():
    text = YML.read_text()
    sql, _ = _preset(text, "exits-paired")
    rows, totals = _statements(sql)
    case = rows[rows.index("CASE WHEN COALESCE(op.our_peak, 0) <= 0 THEN 'no_position'"):
                rows.index(" END AS verdict")]
    order = [case.index("'%s'" % v) for v in GUARDS + VERDICTS]
    assert order == sorted(order), "the two unmeasured guards, then the brief's five, in order"
    assert case.count("THEN '") == len(GUARDS + VERDICTS) - 1 and case.endswith("ELSE 'no_exit_order'")
    assert "WHEN hs.condition_id IS NULL THEN 'his_fills_unseen'" in case
    assert "WHEN hs.exit_ts IS NULL THEN 'no_exit_by_him'" in case
    assert "WHEN ox.our_exit_filled / NULLIF(op.our_peak, 0) >= 0.8 THEN 'exited_with_him'" in case
    assert "WHEN ox.our_exit_filled > 0 THEN 'partial_exit'" in case
    assert "WHEN ox.our_exit_placed > 0 THEN 'exit_placed_unfilled'" in case
    # the stuck dollars: settled NEGATIVE on the three verdicts that left us in, as a positive figure
    assert ("CASE WHEN verdict IN ('partial_exit', 'exit_placed_unfilled', 'no_exit_order')"
            " AND our_settled < 0 THEN -our_settled END AS " + STUCK) in rows
    assert rows.endswith("FROM v ORDER BY %s DESC NULLS LAST, book LIMIT 60;" % STUCK)
    cols = rows[rows.rindex(" SELECT ") + len(" SELECT "):rows.rindex(" FROM v ")].split(", ")
    assert cols == ["book", "market", "side", "his_peak", "his_exit_from", "his_exit_ts", "his_exit_px",
                    "his_exit_usd", "his_pnl", "our_peak", "our_exit_ts", "our_exit_px",
                    "our_exit_filled_pct", "lag_s", "cents_vs_his", "held_to_settlement",
                    "our_settled", "our_roi", "verdict", STUCK, "last_reason"]
    # ONE totals row: books, his exits, ours with him, partial, unfilled, no order, the sum of the
    # stuck dollars, lag median / p90 over exited_with_him, the cents median
    for needle in ("count(*) AS books", "count(his_exit_ts) AS his_exits",
                   "FILTER (WHERE verdict = 'exited_with_him') AS with_him",
                   "FILTER (WHERE verdict = 'partial_exit') AS partial",
                   "FILTER (WHERE verdict = 'exit_placed_unfilled') AS unfilled",
                   "FILTER (WHERE verdict = 'no_exit_order') AS no_order",
                   "FILTER (WHERE verdict = 'no_exit_by_him') AS he_held",
                   "FILTER (WHERE verdict IN ('no_position', 'his_fills_unseen')) AS unmeasured",
                   "round(sum(%s)::numeric, 2) AS stuck_usd" % STUCK,
                   "percentile_cont(0.5) WITHIN GROUP (ORDER BY lag_s)"
                   " FILTER (WHERE verdict = 'exited_with_him')::numeric, 0) AS lag_med_s",
                   "percentile_cont(0.9) WITHIN GROUP (ORDER BY lag_s)"
                   " FILTER (WHERE verdict = 'exited_with_him')::numeric, 0) AS lag_p90_s",
                   "percentile_cont(0.5) WITHIN GROUP (ORDER BY cents_vs_his)::numeric, 1)"
                   " AS cents_vs_his_med"):
        assert needle in totals, needle


def test_the_exits_paired_help_line_is_the_case_labels_after_nf_venue_with_hourly_last_and_untouched():
    text = YML.read_text()
    line = next(ln for ln in text.splitlines() if ln.lstrip().startswith('*) echo "sql: arg must be one of'))
    names = line.split("one of ", 1)[1].split(" (got", 1)[0].split("|")
    labels = re.findall(r"^ {16}([a-z0-9-]+)\) ", text[text.index('case "$ARG" in'):text.index(line)], re.M)
    assert names == labels, "the line is the case labels, in order (regenerated)"
    assert len(names) == len(set(names))
    assert names.index("exits-paired") == names.index("nf-venue") + 1
    assert names[-1] == "hourly" and "nf-venue|exits-paired|hourly (got" in line
    # the hourly line joins the five it always joined: this preset is not one of them
    hourly_sql, _ = _preset(text, "hourly")
    assert "exits-paired" not in hourly_sql and STUCK not in hourly_sql and "his_exit_from" not in hourly_sql
    hourly.test_the_hourly_preset_is_the_five_presets_sql_joined_under_section_markers()
    hourly.test_the_hourly_preset_is_read_only_with_its_own_output_cap_and_timeout()
    hourly.test_the_help_line_is_the_case_labels_with_hourly_last()
    # the comment block over the preset names the rule the columns read by
    block = text[text.index("# THE EXITS, PAIRED"):text.index("exits-paired) SQL=")]
    for word in ("CLOSED in the last 24 h", "10 % of the peak", "his_exit_from", "10 s of clock skew",
                 "book's intent and the row's plan side", "no_position", "his_fills_unseen",
                 "Read-only, LIMITed"):
        assert word in block, word
