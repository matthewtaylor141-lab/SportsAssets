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

FILL lane 8 (2026-09-09; h2225 1212: the rest row read 278 / 111 filled /
140 replaced under ONE fill_pct): (a) a replaced rest prints under its own
word `rest_replaced`, so n / filled_n / fill_pct / roi read per side of the
split; `replaced_n` stays on the `rest` row (a window sum over the base
word and bucket: the rest row and its rest_replaced sibling both read the
bucket's replaced count, ALL the total); (b) a SECOND statement pairs every
replaced entry row of 24 h (both intents) with the next entry row of the
SAME book -- touch_moved / same_quote / qty_regrow / other / unread, with
future_clock beside -- by side and hour, ROLLUP (side, hour); (c) the word
pins: the first statement prints whatever `decision` the row carries, so
lane 10's `take_on_add` and lane 12's `rest_held` print the day they are
written. The one-statement pin is re-pinned to two; the scratch-database
pins (skipped without a local server) read 826's / 823's / 816's / 829's
shapes from h2225 and tick_2245.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests import test_render_ops_hourly as hourly
from tests.test_render_ops_fills_missed import World, _f

YML = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "render-ops.yml"
HIS_CENT = "floor(round((o.his_level * 100)::numeric, 6)) / 100"
RESOLVED = "filled > 0 AND payoff IS NOT NULL"
CAUSES = "('replace_cent', 'replace_qty', 'replace_side', 'ttl', 'replace_unread')"
# FILL lane 8: replaced_n on the rest row = the bucket's replaced count, whichever side of the split the row is
REPLACED_N = ("sum(count(*) FILTER (WHERE replaced)) OVER (PARTITION BY regexp_replace(decision, '_replaced$', ''), bucket)"
              " AS replaced_n")


def _preset(text: str, name: str) -> tuple[str, int]:
    m = re.search(r'^ {16}' + re.escape(name) + r'\) SQL="(.*?)"; TO=(\d+)', text, re.M | re.S)
    assert m, name
    return m.group(1), int(m.group(2))


def _statements(sql: str) -> list[str]:
    """FILL lane 8: two statements -- the (decision, bucket) table and the
    replaced-rows pairs table -- each on its own chain (psql runs them one
    by one)."""
    parts = sql.split("; WITH e AS (")
    assert len(parts) == 2, "two statements since FILL lane 8 (was one)"
    return [parts[0] + ";", "WITH e AS (" + parts[1]]


def test_take_band_is_two_read_only_statements_on_its_own_timeout():
    text = YML.read_text()
    block = text[text.index("take-band) SQL="):text.index("# THE EXIT BAND")]
    assert "need_confirm" not in block and "$ARG" not in block and "HEAD=" not in block
    for bad in ("INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "ALTER"):
        assert bad not in block, bad
    sql, to = _preset(text, "take-band")
    assert to == 60000 and block.rstrip().endswith('"; TO=60000 ;;')
    # FILL lane 8: was `sql.count(";") == 1 and sql.endswith("ORDER BY 1, 2;")` -- the pairs statement is the second
    assert sql.count(";") == 2
    table, pairs = _statements(sql)
    assert table.endswith("FROM x GROUP BY ROLLUP (decision, bucket) ORDER BY 1, 2;")
    assert pairs.endswith("FROM z GROUP BY ROLLUP (side, hour) ORDER BY 1, 2 DESC;")
    # the review's HIGH-1: a replace OVERWRITES the row's decision with its cause (059; E18's
    # _SQL_ORDER_DECISION), so the cause words fold back to a placement-side word -- since FILL
    # lane 8 their OWN word 'rest_replaced' (was 'rest'), and `replaced` rides beside as before
    assert sql.startswith("WITH e AS (SELECT o.id, CASE WHEN o.decision IN " + CAUSES + " THEN 'rest_replaced' ELSE"
                          " COALESCE(o.decision, 'unrecorded') END AS decision, o.decision IN " + CAUSES +
                          " AS replaced, o.filled, o.wire, o.qty,")
    assert "THEN 'rest' ELSE" not in table, "a replaced rest is no longer folded into 'rest'"


def test_take_band_reads_long_book_entries_of_24h_at_our_price_against_the_long_tokens_payoff():
    sql, _ = _preset(YML.read_text(), "take-band")
    assert ("FROM mirror_orders o JOIN mirror_books b ON b.id = o.book_id LEFT JOIN market_tokens mt ON"
            " mt.token_id = b.long_asset LEFT JOIN markets m ON m.condition_id = b.condition_id WHERE b.whale = 'rn1'"
            " AND b.intent = 'ORDER_INTENT_BUY_LONG' AND o.side = 'BUY_LONG' AND o.kind IN ('increase', 'take')"
            " AND o.placed_at >= now() - interval '24 hours')") in sql
    # FILL lane 8: the pin reads the grading TABLE (was the whole preset) -- the pairs statement reads both intents
    table, pairs = _statements(sql)
    assert "'SELL_LONG'" not in table and "ORDER_INTENT_BUY_SHORT" not in table, "a short book's add is never band-graded"
    assert "resolved_prices" not in pairs and "payoff" not in pairs, "the pairs statement grades nothing"
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
    table, _pairs = _statements(sql)
    head = table[table.index(" SELECT COALESCE(decision, 'ALL') AS decision"):]
    for needle in ("COALESCE(decision, 'ALL') AS decision, COALESCE(bucket, 'ALL') AS bucket, count(*) AS n",
                   "count(*) FILTER (WHERE filled > 0) AS filled_n",
                   REPLACED_N,      # FILL lane 8: was "count(*) FILTER (WHERE replaced) AS replaced_n"
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
    assert names[-1] == "hourly" and "nf-venue|exits-paired|take-band|exits-band|closed-while-he-traded|fill-answers|hourly (got" in line
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


# ------------------------------------------------------------ FILL lane 8 (2026-09-09)

PAIR_CLAUSE = "(CASE WHEN b.intent = 'ORDER_INTENT_BUY_SHORT' THEN o.side = 'SELL_LONG' ELSE o.side = 'BUY_LONG' END)"


def test_take_band_pairs_statement_reads_the_replaced_entry_rows_against_the_same_books_next_row():
    """The plan's `LEAD(his_level) / LEAD(wire) / LEAD(qty) / LEAD(placed_at)
    OVER (PARTITION BY book_id ORDER BY placed_at)`: the replacement is the
    NEXT entry row of the SAME book, never another book's; a NULL his_level
    or wire on either side, or no next row, reads `unread`, never a guess."""
    sql, _ = _preset(YML.read_text(), "take-band")
    _table, pairs = _statements(sql)
    # the population: every entry row of 24 h on BOTH intents (the exits-paired reducing clause's
    # mirror image: a short book's entry is its SELL_LONG), kind increase / take, so a band take that
    # replaced a rest (lane 2) is the next row too; the window over ALL of them, the count over the replaced
    assert ("FROM mirror_orders o JOIN mirror_books b ON b.id = o.book_id WHERE b.whale = 'rn1' AND o.kind IN"
            " ('increase', 'take') AND " + PAIR_CLAUSE + " AND o.placed_at >= now() - interval '24 hours')") in pairs
    assert ("CASE WHEN b.intent = 'ORDER_INTENT_BUY_SHORT' THEN 'short' ELSE 'long' END AS side, o.decision,"
            " o.decision IN " + CAUSES + " AS replaced,") in pairs
    assert ("CASE WHEN o.his_level IS NULL THEN NULL ELSE floor(round((o.his_level * 100)::numeric, 6)) END AS his_cent,"
            " round(o.wire::numeric, 4) AS wire_c") in pairs, "HIGH-3: his cent numeric-rounded; the wire compared in numeric"
    assert ("n AS (SELECT e.*, LEAD(his_cent) OVER w AS nx_cent, LEAD(wire_c) OVER w AS nx_wire, LEAD(qty) OVER w AS nx_qty,"
            " LEAD(placed_at) OVER w AS nx_placed FROM e WINDOW w AS (PARTITION BY book_id ORDER BY placed_at, id))") in pairs
    assert pairs.count("PARTITION BY book_id") == 1 and "PARTITION BY condition_id" not in pairs
    # the four words and the two guards, in order: unread first (fail closed), then the pair reading
    assert ("CASE WHEN nx_placed IS NULL OR his_cent IS NULL OR nx_cent IS NULL OR wire_c IS NULL OR nx_wire IS NULL"
            " THEN 'unread' WHEN his_cent = nx_cent AND wire_c <> nx_wire THEN 'touch_moved'"
            " WHEN his_cent = nx_cent AND wire_c = nx_wire AND nx_qty = qty THEN 'same_quote'"
            " WHEN his_cent = nx_cent AND wire_c = nx_wire AND nx_qty > qty AND (nx_qty - qty) < 0.10 * greatest(qty - filled, 0)"
            " THEN 'qty_regrow' ELSE 'other' END AS pair") in pairs
    assert ("(decision = 'replace_unread' AND done_at - placed_at < interval '45 seconds' AND filled = 0) AS future_clock") in pairs
    assert "extract(epoch FROM (done_at - placed_at)) AS done_s FROM n WHERE replaced)" in pairs
    # by side and hour, ROLLUP (side, hour): the side's ALL row is lane 12's gate (touch_moved on long books)
    tail = pairs[pairs.index(" SELECT COALESCE(side, 'ALL') AS side"):]
    for needle in ("COALESCE(side, 'ALL') AS side, COALESCE(hour::text, 'ALL') AS hour, count(*) AS replaced",
                   "count(*) FILTER (WHERE pair = 'touch_moved') AS touch_moved",
                   "count(*) FILTER (WHERE pair = 'same_quote') AS same_quote",
                   "count(*) FILTER (WHERE future_clock) AS future_clock",
                   "count(*) FILTER (WHERE pair = 'qty_regrow') AS qty_regrow",
                   "count(*) FILTER (WHERE pair = 'other') AS other",
                   "count(*) FILTER (WHERE pair = 'unread') AS unread",
                   "round(percentile_cont(0.5) WITHIN GROUP (ORDER BY done_s)::numeric, 0) AS done_med_s",
                   "FROM z GROUP BY ROLLUP (side, hour) ORDER BY 1, 2 DESC;"):
        assert needle in tail, needle
    assert "date_trunc('hour', placed_at)::time(0) AS hour" in pairs


def test_take_band_prints_whatever_decision_word_the_row_carries():
    """FILL lane 8 item 4: the (decision, bucket) table groups by the row's
    own word (`COALESCE(o.decision, 'unrecorded')`) and never filters the
    population by a word list, so `take_on_add` (lane 10) and `rest_held`
    (lane 12) print the day they are written; the only `o.decision IN` in
    the table are the two cause folds, and the pairs statement filters on
    `replaced` alone."""
    sql, _ = _preset(YML.read_text(), "take-band")
    table, pairs = _statements(sql)
    assert "COALESCE(o.decision, 'unrecorded') END AS decision" in table
    assert table.count("o.decision IN ") == 2 and table.count("o.decision IN " + CAUSES) == 2
    assert "decision IN ('rest'" not in sql and "decision = 'rest'" not in sql and "'take_in_band'" not in sql
    assert pairs.count("o.decision IN ") == 1 and "FROM n WHERE replaced)" in pairs
    assert REPLACED_N in table and "count(*) FILTER (WHERE replaced) AS replaced_n" not in table


def test_take_band_comment_names_the_split_the_pairs_and_the_gate():
    text = YML.read_text()
    block = text[text.index("# REST VS TAKE, BY DECISION AND BAND"):text.index("take-band) SQL=")]
    for word in ("FILL lane 8", "rest_replaced", "replaced_n` STAYS", "window sum", "SECOND STATEMENT", "LEAD",
                 "touch_moved", "same_quote", "qty_regrow", "other", "unread", "future_clock", "45 s", "lane 12",
                 "2 x 24 + 3", "Read-only"):
        assert word in block, word
    hourly.test_the_hourly_preset_is_the_nine_presets_sql_joined_under_section_markers()


# the scratch database: today's shapes (h2225 862-901, tick_2245 393-394), every
# row inside 24 h, both intents. LONG book 823 (atc-sud-isf-vas ... isf): 4660,
# a rest with NO his_level cancelled `replace_unread` after 100 s -> its pair is
# `unread`; 4672, a rest at 0.47 under his 0.47 cancelled `replace_cent` after
# 25 s and re-placed as 4674 at 0.46 with the SAME his_level (the wires and the
# his_level are the plan's ASSUMED shape: h2225's order table carries neither,
# and no row carries the bid at the cancel) -> touch_moved;
# then two words no lane has written yet, `rest_held` (a filled rest) and
# `take_on_add` (a filled IOC at his cent) -> their own rows. LONG book 840:
# 4800, 100 sh with 50 filled (leaves 50) cancelled `replace_qty`, re-placed as
# 4801 at 104 sh, the same cent and wire (growth 4 < 10 % of 50) -> qty_regrow.
# SHORT book 826 (atc-sud-isf-vas ... dra): 4731 6 @0.41 cancelled
# `replace_unread` after 12 s with 0 filled, 4732 6 @0.41 open 12 s later ->
# same_quote AND future_clock. SHORT book 816 (aec-wta-enakoi-nadpod): 4692
# 1,403 @0.50 with 631.85 filled cancelled `replace_qty`, re-placed as 4699 at
# 752 -> neither (other). SHORT book 829 (tsc-lib-flu-cpa ... 3pt): 4688 / 4693 /
# 4697, three rests of 92 @0.30 for his one 22:13:57 fill, cancelled after 21 /
# 139 / 87 s (`replace_unread` / `replace_unread` / `replace_cent`), 4704 the
# fourth, filled -> same_quote 3 on the short side, future_clock 1 (4688 alone
# under 45 s). Review (FILL_L8_review LOW): SHORT book 830, 4740 40 @0.35 with 4
# FILLED cancelled `replace_unread` after 30 s and re-placed as 4741 at the same
# quote -> same_quote, and NOT future_clock (filled <> 0: the clock's proxy
# reads a fill as a real replace); SHORT book 831, 4750 20 @0.52 under his 0.52
# cancelled `replace_cent` after 87 s and re-placed as 4751 at 0.55 under his
# 0.55 -> other (his cent moved: never touch_moved). Both on the short side so
# the long-book split table above is untouched; the short done median stays 87.
FIXTURE_L8 = """
INSERT INTO whales (id, address, username) VALUES (99, '0xrn1-l0', 'RN1');
INSERT INTO markets (condition_id, title, slug, event_title, sport, resolved_prices, resolved) VALUES
 ('c823', 'isf', 'atc-sud-isf-vas-2026-09-08-isf', 'ev', 'soccer', '["1", "0"]'::jsonb, true),
 ('c840', 'regrow', 'atc-regrow-2026-09-08', 'ev', 'soccer', NULL, false),
 ('c826', 'dra', 'atc-sud-isf-vas-2026-09-08-dra', 'ev', 'soccer', NULL, false),
 ('c816', 'enakoi', 'aec-wta-enakoi-nadpod-2026-09-08', 'ev', 'tennis', NULL, false),
 ('c829', '3pt', 'tsc-lib-flu-cpa-2026-09-08-3pt', 'ev', 'soccer', NULL, false),
 ('c830', 'filled-requote', 'atc-filled-requote-2026-09-08', 'ev', 'soccer', NULL, false),
 ('c831', 'cent-moved', 'atc-cent-moved-2026-09-08', 'ev', 'soccer', NULL, false);
INSERT INTO market_tokens (token_id, condition_id, outcome, outcome_index) VALUES
 ('L23', 'c823', 'a', 0), ('O23', 'c823', 'b', 1), ('L40', 'c840', 'a', 0), ('O40', 'c840', 'b', 1),
 ('L26', 'c826', 'a', 0), ('O26', 'c826', 'b', 1), ('L16', 'c816', 'a', 0), ('O16', 'c816', 'b', 1),
 ('L29', 'c829', 'a', 0), ('O29', 'c829', 'b', 1), ('L30', 'c830', 'a', 0), ('O30', 'c830', 'b', 1),
 ('L31', 'c831', 'a', 0), ('O31', 'c831', 'b', 1);
INSERT INTO mirror_books (id, whale, condition_id, us_market_slug, long_asset, other_asset, intent, ratio, state, target, target_raw, his_net, ledger_net, last_plan, peak_exposure_usd, avg_cost, settled_pnl, opened_at, closed_at, last_reason, flow_base) VALUES
 (823, 'rn1', 'c823', 'atc-sud-isf-vas-2026-09-08-isf', 'L23', 'O23', 'ORDER_INTENT_BUY_LONG', 0.1, 'live', 60, 60.0, 600, 50, '{}'::jsonb, 30.0, 0.47, NULL, now() - interval '4 hours', NULL, 'on target', 0),
 (840, 'rn1', 'c840', 'atc-regrow-2026-09-08', 'L40', 'O40', 'ORDER_INTENT_BUY_LONG', 0.1, 'live', 104, 104.0, 1040, 50, '{}'::jsonb, 15.0, 0.30, NULL, now() - interval '4 hours', NULL, 'on target', 0),
 (826, 'rn1', 'c826', 'atc-sud-isf-vas-2026-09-08-dra', 'L26', 'O26', 'ORDER_INTENT_BUY_SHORT', 0.1, 'live', -6, -6.0, -60, 0, '{}'::jsonb, 0.0, 0.41, NULL, now() - interval '4 hours', NULL, 'on target', 0),
 (816, 'rn1', 'c816', 'aec-wta-enakoi-nadpod-2026-09-08', 'L16', 'O16', 'ORDER_INTENT_BUY_SHORT', 0.1, 'live', -1746, -1746.0, -17460, -631, '{}'::jsonb, 315.9, 0.50, NULL, now() - interval '4 hours', NULL, 'on target', 0),
 (829, 'rn1', 'c829', 'tsc-lib-flu-cpa-2026-09-08-3pt', 'L29', 'O29', 'ORDER_INTENT_BUY_SHORT', 0.1, 'live', -92, -92.0, -920, -92, '{}'::jsonb, 27.6, 0.30, NULL, now() - interval '4 hours', NULL, 'on target', 0),
 (830, 'rn1', 'c830', 'atc-filled-requote-2026-09-08', 'L30', 'O30', 'ORDER_INTENT_BUY_SHORT', 0.1, 'live', -40, -40.0, -400, -4, '{}'::jsonb, 2.6, 0.35, NULL, now() - interval '4 hours', NULL, 'on target', 0),
 (831, 'rn1', 'c831', 'atc-cent-moved-2026-09-08', 'L31', 'O31', 'ORDER_INTENT_BUY_SHORT', 0.1, 'live', -20, -20.0, -200, 0, '{}'::jsonb, 0.0, 0.55, NULL, now() - interval '4 hours', NULL, 'on target', 0);
INSERT INTO mirror_orders (id, book_id, whale, us_market_slug, kind, side, tif, his_level, price, wire, qty, state, filled, avg_px, bid_at_place, ask_at_place, placed_at, done_at, reason, decision) VALUES
 (4660, 823, 'rn1', 'atc-sud-isf-vas-2026-09-08-isf', 'increase', 'BUY_LONG', 'GTC', NULL, 0.47, 0.47, 30, 'cancelled', 0, NULL, 0.47, 0.48, now() - interval '3 hours' - interval '300 seconds', now() - interval '3 hours' - interval '200 seconds', 'replace', 'replace_unread'),
 (4672, 823, 'rn1', 'atc-sud-isf-vas-2026-09-08-isf', 'increase', 'BUY_LONG', 'GTC', 0.47, 0.47, 0.47, 34, 'cancelled', 0, NULL, 0.47, 0.48, now() - interval '3 hours', now() - interval '3 hours' + interval '25 seconds', 'replace', 'replace_cent'),
 (4674, 823, 'rn1', 'atc-sud-isf-vas-2026-09-08-isf', 'increase', 'BUY_LONG', 'GTC', 0.47, 0.46, 0.46, 7, 'open', 0, NULL, 0.46, 0.48, now() - interval '3 hours' + interval '42 seconds', NULL, 'increase', 'rest'),
 (4676, 823, 'rn1', 'atc-sud-isf-vas-2026-09-08-isf', 'increase', 'BUY_LONG', 'GTC', 0.47, 0.47, 0.47, 30, 'filled', 30, 0.47, 0.47, 0.48, now() - interval '3 hours' + interval '600 seconds', now() - interval '3 hours' + interval '700 seconds', 'increase', 'rest_held'),
 (4677, 823, 'rn1', 'atc-sud-isf-vas-2026-09-08-isf', 'take', 'BUY_LONG', 'IOC', 0.47, 0.47, 0.47, 20, 'filled', 20, 0.47, 0.46, 0.47, now() - interval '3 hours' + interval '900 seconds', now() - interval '3 hours' + interval '901 seconds', 'take', 'take_on_add'),
 (4800, 840, 'rn1', 'atc-regrow-2026-09-08', 'increase', 'BUY_LONG', 'GTC', 0.30, 0.30, 0.30, 100, 'cancelled', 50, 0.30, 0.30, 0.31, now() - interval '3 hours', now() - interval '3 hours' + interval '60 seconds', 'replace', 'replace_qty'),
 (4801, 840, 'rn1', 'atc-regrow-2026-09-08', 'increase', 'BUY_LONG', 'GTC', 0.30, 0.30, 0.30, 104, 'open', 0, NULL, 0.30, 0.31, now() - interval '3 hours' + interval '61 seconds', NULL, 'increase', 'rest'),
 (4731, 826, 'rn1', 'atc-sud-isf-vas-2026-09-08-dra', 'increase', 'SELL_LONG', 'GTC', 0.41, 0.41, 0.41, 6, 'cancelled', 0, NULL, 0.40, 0.42, now() - interval '3 hours', now() - interval '3 hours' + interval '12 seconds', 'replace', 'replace_unread'),
 (4732, 826, 'rn1', 'atc-sud-isf-vas-2026-09-08-dra', 'increase', 'SELL_LONG', 'GTC', 0.41, 0.41, 0.41, 6, 'open', 0, NULL, 0.40, 0.42, now() - interval '3 hours' + interval '12 seconds', NULL, 'increase', 'rest'),
 (4692, 816, 'rn1', 'aec-wta-enakoi-nadpod-2026-09-08', 'increase', 'SELL_LONG', 'GTC', 0.50, 0.50, 0.50, 1403, 'cancelled', 631.85, 0.50, 0.49, 0.51, now() - interval '3 hours', now() - interval '3 hours' + interval '140 seconds', 'replace', 'replace_qty'),
 (4699, 816, 'rn1', 'aec-wta-enakoi-nadpod-2026-09-08', 'increase', 'SELL_LONG', 'GTC', 0.50, 0.50, 0.50, 752, 'open', 0, NULL, 0.49, 0.51, now() - interval '3 hours' + interval '174 seconds', NULL, 'increase', 'rest'),
 (4688, 829, 'rn1', 'tsc-lib-flu-cpa-2026-09-08-3pt', 'increase', 'SELL_LONG', 'GTC', 0.30, 0.30, 0.30, 92, 'cancelled', 0, NULL, 0.29, 0.31, now() - interval '3 hours', now() - interval '3 hours' + interval '21 seconds', 'replace', 'replace_unread'),
 (4693, 829, 'rn1', 'tsc-lib-flu-cpa-2026-09-08-3pt', 'increase', 'SELL_LONG', 'GTC', 0.30, 0.30, 0.30, 92, 'cancelled', 0, NULL, 0.29, 0.31, now() - interval '3 hours' + interval '21 seconds', now() - interval '3 hours' + interval '160 seconds', 'replace', 'replace_unread'),
 (4697, 829, 'rn1', 'tsc-lib-flu-cpa-2026-09-08-3pt', 'increase', 'SELL_LONG', 'GTC', 0.30, 0.30, 0.30, 92, 'cancelled', 0, NULL, 0.29, 0.31, now() - interval '3 hours' + interval '160 seconds', now() - interval '3 hours' + interval '247 seconds', 'replace', 'replace_cent'),
 (4704, 829, 'rn1', 'tsc-lib-flu-cpa-2026-09-08-3pt', 'increase', 'SELL_LONG', 'GTC', 0.30, 0.30, 0.30, 92, 'filled', 92, 0.30, 0.29, 0.31, now() - interval '3 hours' + interval '255 seconds', now() - interval '3 hours' + interval '358 seconds', 'increase', 'rest'),
 (4740, 830, 'rn1', 'atc-filled-requote-2026-09-08', 'increase', 'SELL_LONG', 'GTC', 0.35, 0.35, 0.35, 40, 'cancelled', 4, 0.35, 0.34, 0.36, now() - interval '3 hours', now() - interval '3 hours' + interval '30 seconds', 'replace', 'replace_unread'),
 (4741, 830, 'rn1', 'atc-filled-requote-2026-09-08', 'increase', 'SELL_LONG', 'GTC', 0.35, 0.35, 0.35, 40, 'open', 0, NULL, 0.34, 0.36, now() - interval '3 hours' + interval '33 seconds', NULL, 'increase', 'rest'),
 (4750, 831, 'rn1', 'atc-cent-moved-2026-09-08', 'increase', 'SELL_LONG', 'GTC', 0.52, 0.52, 0.52, 20, 'cancelled', 0, NULL, 0.51, 0.53, now() - interval '3 hours', now() - interval '3 hours' + interval '87 seconds', 'replace', 'replace_cent'),
 (4751, 831, 'rn1', 'atc-cent-moved-2026-09-08', 'increase', 'SELL_LONG', 'GTC', 0.55, 0.55, 0.55, 20, 'open', 0, NULL, 0.54, 0.56, now() - interval '3 hours' + interval '90 seconds', NULL, 'increase', 'rest');
"""


@pytest.fixture(scope="module")
def world():
    w = World(FIXTURE_L8, "take-band lane 8")
    try:
        yield w
    finally:
        w.close()


def test_take_band_on_todays_shapes_splits_the_rest_row_and_prints_the_new_words(world):
    sql, _ = _preset(YML.read_text(), "take-band")
    table, _pairs = _statements(sql)
    world.run(sql)
    by = {(r["decision"], r["bucket"]): r for r in world.rows(table)}
    # long books only: 823's five rows and 840's two; the replaced three under their own word
    assert by[("ALL", "ALL")]["n"] == 7 and _f(by[("ALL", "ALL")]["replaced_n"]) == 3.0
    r = by[("rest", "1")]
    assert r["n"] == 2 and r["filled_n"] == 0 and _f(r["fill_pct"]) == 0.0
    assert _f(r["replaced_n"]) == 2.0, "replaced_n stays on the rest row: the bucket's replaced count (4672, 4800)"
    rr = by[("rest_replaced", "1")]
    assert rr["n"] == 2 and rr["filled_n"] == 1 and _f(rr["fill_pct"]) == 50.0 and _f(rr["replaced_n"]) == 2.0
    assert by[("rest_replaced", "unread")]["n"] == 1 and _f(by[("rest_replaced", "unread")]["replaced_n"]) == 1.0
    assert by[("rest", "ALL")]["n"] == 2 and _f(by[("rest", "ALL")]["replaced_n"]) == 3.0
    assert by[("rest_replaced", "ALL")]["n"] == 3 and _f(by[("rest_replaced", "ALL")]["replaced_n"]) == 3.0
    assert by[("rest", "ALL")]["n"] + by[("rest_replaced", "ALL")]["n"] == 5, "rest + rest_replaced = the old rest row"
    # the words no lane has written yet print as their own rows the day they are written
    assert by[("rest_held", "1")]["n"] == 1 and by[("rest_held", "1")]["filled_n"] == 1
    assert by[("take_on_add", "<=0")]["n"] == 1 and by[("take_on_add", "<=0")]["filled_n"] == 1
    # graded at the wire: (1 - 0.47) / 0.47 = 1.1277 on the resolved market, 0.0c paid over his cent
    assert _f(by[("take_on_add", "<=0")]["paid_med_c"]) == 0.0 and _f(by[("take_on_add", "<=0")]["roi"]) == 1.1277
    assert not [k for k in by if k[0] in ("replace_cent", "replace_qty", "replace_unread", "rest_ttl")]
    assert not [k for k in by if k[0] in ("short", "cover")], "a short book's entry is never in the table"


def test_take_band_pairs_on_todays_shapes_reads_the_four_words_by_side(world):
    sql, _ = _preset(YML.read_text(), "take-band")
    _table, pairs = _statements(sql)
    rows = world.rows(pairs)
    by = {(r["side"], r["hour"]): r for r in rows}
    long_, short = by[("long", "ALL")], by[("short", "ALL")]
    # 823: touch_moved 1 (0.47 -> 0.46 under the same cent), unread 1 (4660, no his_level); 840: qty_regrow 1
    assert long_["replaced"] == 3 and long_["touch_moved"] == 1 and long_["qty_regrow"] == 1 and long_["unread"] == 1
    assert long_["same_quote"] == 0 and long_["future_clock"] == 0 and long_["other"] == 0
    # 826: same_quote 1 + future_clock 1; 816: other 1; 829: same_quote 3, one of them (21 s) future_clock;
    # 830: same_quote 1 and NOT future_clock (4 filled, 30 s: a fill is never the clock); 831: other 1 (his cent moved)
    assert short["replaced"] == 7 and short["same_quote"] == 5 and short["future_clock"] == 2 and short["other"] == 2
    assert short["touch_moved"] == 0 and short["qty_regrow"] == 0 and short["unread"] == 0
    for r in (long_, short):
        assert r["touch_moved"] + r["same_quote"] + r["qty_regrow"] + r["other"] + r["unread"] == r["replaced"]
    all_ = by[("ALL", "ALL")]
    assert all_["replaced"] == 10 and all_["same_quote"] == 5 and all_["touch_moved"] == 1 and all_["future_clock"] == 2
    # the replaced rows' done_at - placed_at median: long 25 / 60 / 100 -> 60; short 12 / 21 / 30 / 87 / 87 / 139 / 140 -> 87
    assert _f(long_["done_med_s"]) == 60.0 and _f(short["done_med_s"]) == 87.0
    # the hour rows sum to the side rows and carry the same words
    hours = [r for r in rows if r["hour"] != "ALL"]
    assert hours and sum(r["replaced"] for r in hours if r["side"] == "long") == 3
    assert sum(r["replaced"] for r in hours if r["side"] == "short") == 7
    assert all(re.match(r"^\d\d:00:00$", r["hour"]) for r in hours)
    assert rows[0]["side"] == "ALL" and rows[0]["hour"] == "ALL", "ORDER BY 1, 2 DESC: the totals lead"
    for side in ("long", "short"):
        first = next(r for r in rows if r["side"] == side)
        assert first["hour"] == "ALL", "each side's ALL row leads its hours (lane 12's gate reads the first long row)"


def test_fill_lane_8_docs_section_names_the_six_amendments():
    doc = (Path(__file__).resolve().parents[2] / "docs" / "mirror-coverage.md").read_text()
    m = re.search(r"^## \d+\. The reads batch C is judged by \(2026-09-09, FILL lane 8\)", doc, re.M)
    assert m, "docs section for FILL lane 8"
    sec = doc[m.start():]
    for word in ("missed_replace", "TWO places", "rest_replaced", "exit_rest_replaced", "cover_replaced", "replaced_n` STAYS",
                 "touch_moved", "same_quote", "future_clock", "qty_regrow", "unread", "PARTITION BY book_id",
                 "we_matched_his_cut", "0.05 chosen", "matched_cut_n", "15 s", "rested_15s", "$246.20",
                 "Census: none", "059's list stands", "Expected dollars: $0", "DOES NOT FIX"):
        assert word in sec, word
