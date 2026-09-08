"""The `take-band` render-ops preset (FILL lane 0b item 1, 2026-09-08):
brief question 3's read -- rest vs take per DECISION and band bucket on
every entry order row of the last 24 h on a LONG book, graded at OUR
price against the long token's resolved price. Runs today for 'rest' and
'take' before any band lands ('take_in_band' joins the rows once lane 2
writes it); the per-fill classes split by decision are fills-missed's
third statement, beside this one in the hourly. The pins: one statement,
read-only, its own timeout; the population (long books, BUY_LONG,
increase / take, 24 h); band_c on the numeric-rounded cent (HIGH-3) with
the buckets <=0 / 1 / 2 / 3+ / unread; the measures and their FILTERs
(roi and ci95 over the FILLED rows on resolved markets, paid_med_c over
the filled rows, the send-ask read over IOC rows); 'unrecorded' for a row
older than 059, the ROLLUP's ALL; the case label after exits-paired, the
help line regenerated, and the hourly carrying it after fills-missed.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests import test_render_ops_hourly as hourly

YML = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "render-ops.yml"
HIS_CENT = "floor(round((o.his_level * 100)::numeric, 6)) / 100"
RESOLVED = "filled > 0 AND payoff IS NOT NULL"
CAUSES = "('replace_cent', 'replace_qty', 'replace_side', 'ttl', 'replace_unread')"


def _preset(text: str, name: str) -> tuple[str, int]:
    m = re.search(r'^ {16}' + re.escape(name) + r'\) SQL="(.*?)"; TO=(\d+)', text, re.M | re.S)
    assert m, name
    return m.group(1), int(m.group(2))


def test_take_band_is_one_read_only_statement_on_its_own_timeout():
    text = YML.read_text()
    block = text[text.index("take-band) SQL="):text.index("# THE EXIT BAND")]
    assert "need_confirm" not in block and "$ARG" not in block and "HEAD=" not in block
    for bad in ("INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "ALTER"):
        assert bad not in block, bad
    sql, to = _preset(text, "take-band")
    assert to == 60000 and block.rstrip().endswith('"; TO=60000 ;;')
    assert sql.count(";") == 1 and sql.endswith("ORDER BY 1, 2;")
    # the review's HIGH-1: a replace OVERWRITES the row's decision with its cause (059; E18's
    # _SQL_ORDER_DECISION), so the cause words fold back to the placement word 'rest' -- the
    # only word a replace cancels on a long book's BUY_LONG row -- and `replaced` rides beside
    assert sql.startswith("WITH e AS (SELECT o.id, CASE WHEN o.decision IN " + CAUSES + " THEN 'rest' ELSE"
                          " COALESCE(o.decision, 'unrecorded') END AS decision, o.decision IN " + CAUSES +
                          " AS replaced, o.filled, o.wire, o.qty,")


def test_take_band_reads_long_book_entries_of_24h_at_our_price_against_the_long_tokens_payoff():
    sql, _ = _preset(YML.read_text(), "take-band")
    assert ("FROM mirror_orders o JOIN mirror_books b ON b.id = o.book_id LEFT JOIN market_tokens mt ON"
            " mt.token_id = b.long_asset LEFT JOIN markets m ON m.condition_id = b.condition_id WHERE b.whale = 'rn1'"
            " AND b.intent = 'ORDER_INTENT_BUY_LONG' AND o.side = 'BUY_LONG' AND o.kind IN ('increase', 'take')"
            " AND o.placed_at >= now() - interval '24 hours')") in sql
    assert "'SELL_LONG'" not in sql and "ORDER_INTENT_BUY_SHORT" not in sql, "a short book's add is never band-graded"
    # payoff = the long token's resolved price, NULL until the market resolves (no 1 - p: long books only)
    assert ("CASE WHEN m.resolved_prices IS NULL OR mt.outcome_index IS NULL THEN NULL ELSE"
            " (m.resolved_prices->>mt.outcome_index)::numeric END AS payoff") in sql
    assert "1 - (m.resolved_prices" not in sql


def test_take_band_buckets_the_ask_over_his_cent_and_the_cents_paid_on_the_numeric_rounded_cent():
    sql, _ = _preset(YML.read_text(), "take-band")
    assert ("CASE WHEN o.his_level IS NULL OR o.ask_at_place IS NULL THEN NULL ELSE"
            " round(((o.ask_at_place::numeric - " + HIS_CENT + ") * 100)::numeric, 0) END AS band_c") in sql
    assert ("CASE WHEN o.his_level IS NULL THEN NULL ELSE round(((o.wire::numeric - " + HIS_CENT + ") * 100)::numeric, 1)"
            " END AS paid_c") in sql
    assert ("CASE WHEN band_c IS NULL THEN 'unread' WHEN band_c <= 0 THEN '<=0' WHEN band_c = 1 THEN '1'"
            " WHEN band_c = 2 THEN '2' ELSE '3+' END AS bucket") in sql
    assert "floor((o.his_level * 100))" not in sql and "floor(o.his_level * 100)" not in sql, "HIGH-3: never a float8 floor"


def test_take_band_measures_fill_rate_dollars_roi_ci95_and_the_cents_paid_per_decision_and_bucket():
    sql, _ = _preset(YML.read_text(), "take-band")
    head = sql[sql.index(" SELECT COALESCE(decision, 'ALL') AS decision"):]
    for needle in ("COALESCE(decision, 'ALL') AS decision, COALESCE(bucket, 'ALL') AS bucket, count(*) AS n",
                   "count(*) FILTER (WHERE filled > 0) AS filled_n",
                   "count(*) FILTER (WHERE replaced) AS replaced_n",
                   "round(100.0 * count(*) FILTER (WHERE filled > 0) / count(*), 1) AS fill_pct",
                   "round(sum(filled * wire)::numeric, 2) AS our_usd",
                   "count(*) FILTER (WHERE " + RESOLVED + ") AS n_resolved",
                   "round((sum(filled * (payoff - wire)) FILTER (WHERE " + RESOLVED + ") / NULLIF(sum(filled * wire)"
                   " FILTER (WHERE " + RESOLVED + "), 0))::numeric, 4) AS roi",
                   "round((1.96 * stddev_samp((payoff - wire) / NULLIF(wire, 0)) FILTER (WHERE " + RESOLVED + ")"
                   " / sqrt(NULLIF(count(*) FILTER (WHERE " + RESOLVED + "), 0)))::numeric, 4) AS ci95",
                   "round(percentile_cont(0.5) WITHIN GROUP (ORDER BY paid_c) FILTER (WHERE filled > 0)::numeric, 1)"
                   " AS paid_med_c",
                   "round(percentile_cont(0.5) WITHIN GROUP (ORDER BY (ask_at_send - wire) * 100)"
                   " FILTER (WHERE tif = 'IOC' AND ask_at_send IS NOT NULL)::numeric, 1) AS send_ask_over_wire_med_c",
                   "FROM x GROUP BY ROLLUP (decision, bucket) ORDER BY 1, 2;"):
        assert needle in head, needle
    # graded at OUR price: the wire, never his level, in the roi
    assert "(payoff - his_level)" not in sql and "avg_px" not in sql


def test_take_band_sits_after_exits_paired_and_rides_the_hourly_after_fills_missed():
    text = YML.read_text()
    line = next(ln for ln in text.splitlines() if ln.lstrip().startswith('*) echo "sql: arg must be one of'))
    names = line.split("one of ", 1)[1].split(" (got", 1)[0].split("|")
    labels = re.findall(r"^ {16}([a-z0-9-]+)\) ", text[text.index('case "$ARG" in'):text.index(line)], re.M)
    assert names == labels and len(names) == len(set(names))
    assert names.index("take-band") == names.index("exits-paired") + 1
    assert names[-1] == "hourly" and "nf-venue|exits-paired|take-band|exits-band|closed-while-he-traded|hourly (got" in line
    h, _ = _preset(text, "hourly")
    sql, _ = _preset(text, "take-band")
    markers = re.findall(r"SELECT '== ([a-z-]+)' AS section;", h)
    assert markers.index("take-band") == markers.index("fills-missed") + 1
    assert "SELECT '== take-band' AS section; " + sql in h
    hourly.test_the_hourly_preset_is_the_nine_presets_sql_joined_under_section_markers()
    hourly.test_the_hourly_preset_is_read_only_with_its_own_output_cap_and_timeout()
    hourly.test_the_help_line_is_the_case_labels_with_hourly_last()
    block = text[text.index("# REST VS TAKE, BY DECISION AND BAND"):text.index("take-band) SQL=")]
    for word in ("brief", "question 3", "LONG book", "BUY_LONG", "<=0 / 1 / 2 / 3+", "'unrecorded'", "HIGH-3",
                 "paid_med_c", "send_ask_over_wire_med_c", "fills-missed's third", "Read-only"):
        assert word in block, word
