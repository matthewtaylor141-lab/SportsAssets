"""The `exits-paired` render-ops preset (E8 part 2, step 1, 2026-09-08):
on every market whose mirror book CLOSED in the last 24 h, did we leave
when he left, at his price? Owner: "we go down when he loses ... sells
(or exits) within 1c of his price". The pins: the preset is read-only,
LIMITed, on its own timeout; his rows are collapsed chain-first by the
paired-day preset's OWN text (verbatim) but his net is read ON THE
BOOK'S TOKEN AXIS -- size x (BUY +1 / SELL -1) x (long_asset +1 /
other_asset -1), the mirror's own his_net (long minus other), flipped on
a BUY_SHORT book -- never the per-condition BUY-minus-SELL that read 175
of 200 books "held to settlement" on the first live run (2026-09-09: on
this venue he leaves a Yes as often by BUYING the No); every price on the
long axis (a No at p is a Yes at 1 - p); the 24 h close window, the 10 %
exit rule, the reduce keyed on the book's intent and the row's plan side
(a short's cover is its exit), the verdicts in the brief's order behind
the two unmeasured guards, the stuck dollars worst first; two statements
each carrying the whole CTE chain (psql runs them one by one); the help
line regenerated from the case labels with `exits-paired` after
`nf-venue`, `hourly` still last and the hourly line untouched (its pins
re-run here).
"""
from __future__ import annotations

import re
from pathlib import Path

from tests import test_render_ops_hourly as hourly

YML = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "render-ops.yml"
VERDICTS = ("no_exit_by_him", "exited_with_him", "partial_exit", "exit_placed_unfilled",
            "no_exit_order")
GUARDS = ("no_position", "his_net_unseen")
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
    window and the chain-first collapse over (tx, asset, side) -- its
    inner window subquery and its WHERE, lifted from that line, so an edit
    there that this preset does not carry fails here. What is NOT carried
    is paired-day's running net between them: that one is BUY minus SELL
    over every token of the condition, and this preset reads the net on
    the book's token axis (the next test)."""
    text = YML.read_text()
    pd, _ = _preset(text, "paired-day")
    i0 = pd.index("(SELECT t.*, bool_or(t.source IN ('chain', 's1'))")
    i1 = pd.index("interval '48 hours') d", i0) + len("interval '48 hours') d")
    inner = pd[i0:i1]
    where = ("WHERE (d.source IN ('chain', 's1') OR NOT d.has_net)"
             " AND d.condition_id IN (SELECT condition_id FROM bk)")
    assert where in pd and "OVER (PARTITION BY t.whale_id, COALESCE(lower(NULLIF(t.tx_hash, ''))" in inner
    assert inner.endswith("AND t.ts >= now() - interval '48 hours') d")
    sql, _ = _preset(text, "exits-paired")
    assert sql.count(" FROM (SELECT d.* FROM " + inner + " " + where + ") t"
                     " JOIN bk ON bk.condition_id = t.condition_id"
                     " LEFT JOIN market_tokens mt ON mt.token_id = t.asset"
                     " LEFT JOIN markets m ON m.condition_id = t.condition_id)") == 2
    assert "AS net_after FROM (SELECT t.*" not in sql, "paired-day's asset-blind running net is not carried"
    assert "upper(d.side) = 'BUY' THEN 1 ELSE -1 END) * d.size" not in sql
    # his settled P&L is paired-day's formula, unresolved read NULL (no WHERE on resolved_prices)
    assert ("(CASE WHEN upper(t.side) = 'BUY' THEN 1 ELSE -1 END) * t.size"
            " * ((m.resolved_prices->>mt.outcome_index)::numeric - t.price) AS pnl") in sql
    assert "m.resolved_prices IS NOT NULL" not in sql


def test_the_exits_paired_preset_reads_his_net_on_the_books_token_axis():
    """The 2026-09-09 live run: his_exits 0, he_held 175 of 200, because his
    net was BUY minus SELL over every row of the condition and on this
    venue he leaves a Yes by BUYING the No as often as by selling the Yes
    (the mirror's own his_net reads long-asset shares minus other-asset
    shares). Each row is a signed leg on the book's axis; the peak, the
    exit, the reducing fills and every price read on it."""
    text = YML.read_text()
    sql, _ = _preset(text, "exits-paired")
    rows, totals = _statements(sql)
    for stmt in (rows, totals):
        # the book's tokens and its sign ride the condition row
        assert ("min(b.long_asset) AS long_asset, min(b.other_asset) AS other_asset,"
                " bool_and(b.intent = 'ORDER_INTENT_BUY_SHORT') AS is_short,") in stmt
        # the leg: size x (BUY +1 / SELL -1) x (long +1 / other -1; a binary condition's non-long
        # token is the other when the book carries none) x (-1 on a short book)
        assert ("(CASE WHEN upper(t.side) = 'BUY' THEN 1 ELSE -1 END)"
                " * (CASE WHEN t.asset = bk.long_asset THEN 1"
                " WHEN t.asset = bk.other_asset OR bk.other_asset IS NULL THEN -1 ELSE 0 END)"
                " * (CASE WHEN bk.is_short THEN -1 ELSE 1 END) * t.size AS leg,") in stmt
        # every price on the long axis: a No at p is a Yes at 1 - p
        assert "(CASE WHEN t.asset = bk.long_asset THEN t.price ELSE 1 - t.price END) AS eff_px," in stmt
        # the running net over the legs, the peak its max (> 0: his net went the book's way)
        assert ("hn AS (SELECT h.*, sum(h.leg) OVER (PARTITION BY h.condition_id ORDER BY h.ts, h.id)"
                " AS net_after FROM h)") in stmt
        assert ("pk AS (SELECT DISTINCT ON (condition_id) condition_id, ts AS peak_ts, id AS peak_id,"
                " net_after AS peak FROM hn WHERE net_after > 0"
                " ORDER BY condition_id, net_after DESC, ts, id)") in stmt
        # after the peak: out at or under 10 % of it; reducing = a leg against it (either token)
        assert "hn.net_after <= 0.10 * pk.peak AS out, hn.leg < 0 AS reducing" in stmt
        assert "WHERE (hn.ts, hn.id) > (pk.peak_ts, pk.peak_id)" in stmt
        assert "h.side" not in stmt and "hn.side" not in stmt, "no BUY / SELL reading of his exit"
        # his exit price and dollars on the long axis; the dollars its complement on a short
        assert "min(hr.ts) FILTER (WHERE hr.reducing) AS exit_from" in stmt
        assert stmt.count("FILTER (WHERE hr.reducing AND (hr.ts, hr.id) <= (hx.exit_ts, hx.exit_id))") == 3
        assert "sum(hr.size * hr.eff_px) FILTER" in stmt
        assert "sum(hr.size * (CASE WHEN bk.is_short THEN 1 - hr.eff_px ELSE hr.eff_px END))" in stmt
        assert "GROUP BY pk.condition_id, pk.peak, pk.peak_ts, hx.exit_ts, hx.exit_id, bk.is_short)" in stmt
        # the side is the book's; the cents compare on the same axis, reversed on a short
        assert "CASE WHEN bk.is_short THEN 'short' ELSE 'long' END AS side" in stmt
        assert ("(CASE WHEN bk.is_short THEN ox.our_exit_px - hs.exit_px"
                " ELSE hs.exit_px - ox.our_exit_px END) * 100") in stmt


def test_the_exits_paired_preset_pins_the_window_and_the_side_keyed_reduce():
    text = YML.read_text()
    sql, _ = _preset(text, "exits-paired")
    rows, totals = _statements(sql)
    for stmt in (rows, totals):
        # the books: CLOSED inside 24 h, whale rn1, one row per condition (the orders read the same)
        assert stmt.count("WHERE b.whale = 'rn1' AND b.state = 'closed'"
                          " AND b.closed_at >= now() - interval '24 hours'") == 2
        assert "FROM mirror_books b WHERE b.whale = 'rn1'" in stmt and "GROUP BY b.condition_id)" in stmt
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
        # the pair: lag from his first reducing fill, the rest as paired-day
        assert "round(extract(epoch FROM (ox.our_exit_ts - hs.exit_from))::numeric, 0) AS lag_s" in stmt
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
    assert "WHEN hs.condition_id IS NULL THEN 'his_net_unseen'" in case
    assert "WHEN hs.exit_ts IS NULL THEN 'no_exit_by_him'" in case
    assert "WHEN ox.our_exit_filled / NULLIF(op.our_peak, 0) >= 0.8 THEN 'exited_with_him'" in case
    assert "WHEN ox.our_exit_filled > 0 THEN 'partial_exit'" in case
    assert "WHEN ox.our_exit_placed > 0 THEN 'exit_placed_unfilled'" in case
    assert "his_fills_unseen" not in sql, "the guard's old name: it now also covers a net never on the axis"
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
                   "FILTER (WHERE verdict IN ('no_position', 'his_net_unseen')) AS unmeasured",
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
    assert "AS leg," not in hourly_sql
    hourly.test_the_hourly_preset_is_the_five_presets_sql_joined_under_section_markers()
    hourly.test_the_hourly_preset_is_read_only_with_its_own_output_cap_and_timeout()
    hourly.test_the_help_line_is_the_case_labels_with_hourly_last()
    # the comment block over the preset names the rule the columns read by
    block = text[text.index("# THE EXITS, PAIRED"):text.index("exits-paired) SQL=")]
    for word in ("CLOSED in the last 24 h", "LEG ON THE BOOK'S TOKEN AXIS", "his_net, long minus other",
                 "BUYING the No", "-1 on a", "BUY_SHORT book", "10 % of the peak", "his_exit_from",
                 "a No at p is a Yes at 1 - p", "10 s of", "clock skew",
                 "book's intent and the row's plan side", "no_position", "his_net_unseen",
                 "Read-only, LIMITed"):
        assert word in block, word
