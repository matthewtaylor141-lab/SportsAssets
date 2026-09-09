"""The `exits-band` render-ops preset (FILL lane 0b item 2, 2026-09-08):
R5's unread A8 -- was the bid within 1c or 2c of his price at the held
exit ticks? Every REDUCING order row of the last 24 h (the exits-paired
clause: BUY_LONG on a BUY_SHORT book, SELL_LONG on a long) whose 059
decision is exit_rest / take / cover (and the two words lane 3 adds),
with band_c = the cents past his price the touch sat when the row went
out, bucketed <=0 / 1 / 2 / 3-5 / >5 / unread. Two statements on one
chain: per (decision, bucket) the rows (n, filled_any, filled_whole,
shares_unfilled, usd_unfilled, done_med_s), then per (decision, bucket)
the BOOKS whose rows filled anything against those whose never did with
settled_pnl on each side. The pins: the two statements and one chain,
read-only, the reducing clause and the decision words, the two band
formulas and buckets, the measures, the ROLLUP; the case label after
take-band, the help line, and NOT in the hourly.

FILL lane 8 (2026-09-09): (a) a replaced exit rest prints under its own
word `exit_rest_replaced` (`cover_replaced` on a short book) in both
statements, `replaced_n` staying on the exit_rest / cover row through a
window sum over the base word and bucket; the held-shape scratch pin is
re-pinned to the split (exit_rest / 2 n 3 -> 1 beside exit_rest_replaced
/ 2 n 2, replaced_n 2 on both); (b) a THIRD statement, the per-IOC
same-tick rest line lane 0b promised (docs 46): every exit IOC of 24 h on
a long book by whole / partial / zero fill, and for each the exit_rest row
on the same book placed within 15 s after it -- book 334's shape (a 358
IOC filled 0.50, the 179 rest 3 s later) on its own scratch fixture.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests import test_render_ops_hourly as hourly
from tests.test_render_ops_fills_missed import World, _f

YML = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "render-ops.yml"
CAUSES = "('replace_cent', 'replace_qty', 'replace_side', 'ttl', 'replace_unread')"
# FILL lane 8: replaced_n on the exit_rest / cover row = the bucket's replaced count, on both sides of the split
REPLACED_N = ("sum(count(*) FILTER (WHERE replaced)) OVER (PARTITION BY regexp_replace(decision, '_replaced$', ''), bucket)"
              " AS replaced_n")


def _preset(text: str, name: str) -> tuple[str, int]:
    m = re.search(r'^ {16}' + re.escape(name) + r'\) SQL="(.*?)"; TO=(\d+)', text, re.M | re.S)
    assert m, name
    return m.group(1), int(m.group(2))


def _statements(sql: str) -> list[str]:
    """FILL lane 8: three statements -- the rows and the books on one chain
    (`WITH x AS`), then the per-IOC same-tick rest line on its own
    (`WITH i AS`); was two."""
    parts = sql.split("; WITH ")
    assert len(parts) == 3, "three statements since FILL lane 8 (was two)"
    assert parts[1].startswith("x AS (") and parts[2].startswith("i AS (")
    return [parts[0] + ";", "WITH " + parts[1] + ";", "WITH " + parts[2]]


def test_exits_band_is_three_read_only_statements_the_first_two_on_one_chain():
    text = YML.read_text()
    block = text[text.index("exits-band) SQL="):text.index("# CLOSED WHILE HE TRADED")]
    assert "need_confirm" not in block and "$ARG" not in block and "HEAD=" not in block
    for bad in ("INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "ALTER"):
        assert bad not in block, bad
    sql, to = _preset(text, "exits-band")
    assert to == 60000 and block.rstrip().endswith('"; TO=60000 ;;')
    rows, books, ioc = _statements(sql)
    chain_end = " END AS bucket, extract(epoch FROM (done_at - placed_at)) AS done_s FROM x)"
    assert rows[:rows.index(chain_end)] == books[:books.index(chain_end)], "one chain, byte for byte"
    assert rows.endswith("FROM y GROUP BY ROLLUP (decision, bucket) ORDER BY 1, 2;")
    assert books.endswith("FROM bb GROUP BY ROLLUP (decision, bucket) ORDER BY 1, 2;")
    assert ioc.startswith("WITH i AS (SELECT o.id, o.book_id, o.qty, o.filled, o.placed_at,") and ioc.endswith(
        "FROM r GROUP BY ROLLUP (ioc_fill) ORDER BY 1;")
    assert sql.count(";") == 3


def test_exits_band_reads_the_reducing_rows_by_the_exits_paired_clause_and_the_059_words():
    sql, _ = _preset(YML.read_text(), "exits-band")
    # the review's HIGH-1: a replace OVERWRITES the row's decision with its cause (059's comment;
    # E18's _SQL_ORDER_DECISION at the replace and the TTL cancel), so the held-then-moved exit
    # rests -- the read's subject -- carry 'replace_cent' / 'ttl', not 'exit_rest'. The population
    # admits the cause words, the word READ is the placement word (a reducing row's rest is
    # 'exit_rest' on a long book, 'cover' on a short one; rules.order_decision), `replaced` beside
    assert ("FROM mirror_orders o JOIN mirror_books b ON b.id = o.book_id WHERE b.whale = 'rn1' AND"
            " o.placed_at >= now() - interval '24 hours' AND (CASE WHEN b.intent = 'ORDER_INTENT_BUY_SHORT' THEN"
            " o.side = 'BUY_LONG' ELSE o.side = 'SELL_LONG' END) AND o.decision IN ('exit_rest', 'take', 'cover',"
            " 'exit_take_in_band', 'cover_in_band', 'replace_cent', 'replace_qty', 'replace_side', 'ttl',"
            " 'replace_unread'))") in sql
    # FILL lane 8: the fold's words are the replaced rows' OWN ('exit_rest_replaced' / 'cover_replaced'; were
    # 'exit_rest' / 'cover'), in both chain statements; replaced_n a window sum (was count(*) FILTER)
    assert sql.count("SELECT o.id, o.book_id, CASE WHEN o.decision IN " + CAUSES + " THEN CASE WHEN b.intent ="
                     " 'ORDER_INTENT_BUY_SHORT' THEN 'cover_replaced' ELSE 'exit_rest_replaced' END ELSE o.decision END"
                     " AS decision, o.decision IN " + CAUSES + " AS replaced, o.side,") == 2
    assert "THEN 'cover' ELSE 'exit_rest' END" not in sql
    assert REPLACED_N in sql and "count(*) FILTER (WHERE replaced) AS replaced_n" not in sql
    assert "o.kind" not in sql, "keyed on the book's intent and the row's plan side, never the kind"
    assert "'rest'" not in sql and "'take_in_band'" not in sql, "an entry's words never read as an exit"


# ------------------------------------------------- the review's scratch-database pin

# a long book (51) with three exit rests of ours after his level moved: 9011 rested at 0.55
# for an hour and was REPLACED (decision overwritten 'replace_cent'), 9013 at 0.54 expired
# ('ttl'), 9014 at 0.54 still open ('exit_rest'); and a short book (53) whose cover IOC filled
# ('cover'). Every one sits 2c past his price at the touch (bid 0.53 / 0.52 / 0.52 under his
# 0.55 / 0.54 / 0.54; ask 0.42 over his 0.40). Without the fold the two replaced rests --
# the held rests the read prices -- are not in the set at all (n 2, not 4).
FIXTURE_HELD = """
INSERT INTO whales (id, address, username) VALUES (99, '0xrn1-l0', 'RN1');
INSERT INTO markets (condition_id, title, slug, event_title, sport, resolved_prices, resolved) VALUES
 ('cA', 'A', 'wta-a-2026-09-07', 'ev', 'tennis', '["1", "0"]'::jsonb, true),
 ('cC', 'C', 'wta-c-2026-09-07', 'ev', 'tennis', '["0", "1"]'::jsonb, true);
INSERT INTO market_tokens (token_id, condition_id, outcome, outcome_index) VALUES
 ('LA', 'cA', 'p', 0), ('OA', 'cA', 's', 1), ('LC', 'cC', 'p', 0), ('OC', 'cC', 's', 1);
INSERT INTO mirror_books (id, whale, condition_id, us_market_slug, long_asset, other_asset, intent, ratio, state, target, target_raw, his_net, ledger_net, last_plan, peak_exposure_usd, avg_cost, settled_pnl, opened_at, closed_at, last_reason, flow_base) VALUES
 (51, 'rn1', 'cA', 'aec-wta-a-2026-09-07', 'LA', 'OA', 'ORDER_INTENT_BUY_LONG', 0.1, 'closed', 100, 100.0, 1000, 0, '{}'::jsonb, 50.0, 0.50, 50.0, now() - interval '19 hours', now() - interval '2 hours', 'closed: standing row settled', 0),
 (53, 'rn1', 'cC', 'aec-wta-c-2026-09-07', 'LC', 'OC', 'ORDER_INTENT_BUY_SHORT', 0.1, 'closed', -100, -100.0, -1000, 0, '{}'::jsonb, 40.0, 0.60, -40.0, now() - interval '3 hours', now() - interval '1 hour', 'closed: standing row settled', 0);
INSERT INTO mirror_orders (id, book_id, whale, us_market_slug, kind, side, tif, his_level, price, wire, qty, state, filled, avg_px, bid_at_place, ask_at_place, ask_at_send, placed_at, done_at, reason, decision) VALUES
 (9003, 51, 'rn1', 'aec-wta-a-2026-09-07', 'increase', 'BUY_LONG', 'GTC', 0.50, 0.50, 0.50, 30, 'filled', 30, 0.50, 0.50, 0.52, NULL, now() - interval '16 hours', now() - interval '15 hours', 'increase', 'rest'),
 (9011, 51, 'rn1', 'aec-wta-a-2026-09-07', 'reduce', 'SELL_LONG', 'GTC', 0.55, 0.55, 0.55, 100, 'cancelled', 0, NULL, 0.53, 0.56, NULL, now() - interval '3 hours', now() - interval '2 hours' - interval '59 minutes', 'replace', 'replace_cent'),
 (9013, 51, 'rn1', 'aec-wta-a-2026-09-07', 'reduce', 'SELL_LONG', 'GTC', 0.54, 0.54, 0.54, 100, 'cancelled', 0, NULL, 0.52, 0.55, NULL, now() - interval '2 hours' - interval '58 minutes', now() - interval '2 hours' - interval '30 minutes', 'ttl', 'ttl'),
 (9014, 51, 'rn1', 'aec-wta-a-2026-09-07', 'reduce', 'SELL_LONG', 'GTC', 0.54, 0.54, 0.54, 100, 'open', 0, NULL, 0.52, 0.55, NULL, now() - interval '2 hours' - interval '20 minutes', NULL, 'reduce', 'exit_rest'),
 (9012, 53, 'rn1', 'aec-wta-c-2026-09-07', 'reduce', 'BUY_LONG', 'IOC', 0.40, 0.40, 0.40, 50, 'filled', 50, 0.40, 0.39, 0.42, 0.42, now() - interval '2 hours', now() - interval '2 hours' + interval '1 second', 'cover', 'cover');
"""


@pytest.fixture(scope="module")
def world():
    w = World(FIXTURE_HELD, "exits-band lane 0b")
    try:
        yield w
    finally:
        w.close()


def test_exits_band_counts_the_replaced_exit_rests_under_their_placement_word(world):
    sql, _ = _preset(YML.read_text(), "exits-band")
    rows, books, _ioc = _statements(sql)
    world.run(sql)
    by = {(r["decision"], r["bucket"]): r for r in world.rows(rows)}
    assert by[("ALL", "ALL")]["n"] == 4 and _f(by[("ALL", "ALL")]["replaced_n"]) == 2.0, "the two replaced rests are in the set"
    # FILL lane 8: the split -- exit_rest / 2 read n 3 / replaced_n 2 / shares 300.0 / usd 163.0 (55 + 54 + 54) as one
    # row; now the un-moved 9014 alone under exit_rest (n 1, 100 sh, 54.00) beside 9011 / 9013 under
    # exit_rest_replaced (n 2, 200 sh, 109.00 = 55 + 54); replaced_n 2 STAYS on the exit_rest row (and on its sibling)
    r = by[("exit_rest", "2")]
    assert r["n"] == 1 and _f(r["replaced_n"]) == 2.0 and r["filled_any"] == 0
    assert _f(r["shares_unfilled"]) == 100.0 and _f(r["usd_unfilled"]) == 54.0
    rr = by[("exit_rest_replaced", "2")]
    assert rr["n"] == 2 and _f(rr["replaced_n"]) == 2.0 and rr["filled_any"] == 0
    assert _f(rr["shares_unfilled"]) == 200.0 and _f(rr["usd_unfilled"]) == 109.0
    assert r["n"] + rr["n"] == 3 and _f(r["shares_unfilled"]) + _f(rr["shares_unfilled"]) == 300.0, "the split sums to the old row"
    assert by[("cover", "2")]["n"] == 1 and _f(by[("cover", "2")]["replaced_n"]) == 0.0 and by[("cover", "2")]["filled_whole"] == 1
    assert ("cover_replaced", "2") not in by
    assert not [k for k in by if k[0] in ("replace_cent", "ttl", "rest", "rest_replaced")], "a cause is never a word of its own"
    bb = {(r["decision"], r["bucket"]): r for r in world.rows(books)}
    assert bb[("exit_rest", "2")]["books_unfilled"] == 1 and _f(bb[("exit_rest", "2")]["settled_when_unfilled"]) == 50.0
    assert bb[("exit_rest_replaced", "2")]["books_unfilled"] == 1 and _f(bb[("exit_rest_replaced", "2")]["settled_when_unfilled"]) == 50.0
    assert bb[("cover", "2")]["books_filled"] == 1 and _f(bb[("cover", "2")]["settled_when_filled"]) == -40.0


def test_exits_band_measures_the_cents_past_his_price_on_each_side_and_buckets_them():
    sql, _ = _preset(YML.read_text(), "exits-band")
    assert ("CASE WHEN o.his_level IS NULL THEN NULL WHEN o.side = 'SELL_LONG' AND o.bid_at_place IS NOT NULL THEN"
            " round(((o.his_level - o.bid_at_place) * 100)::numeric, 0) WHEN o.side = 'BUY_LONG' AND"
            " o.ask_at_place IS NOT NULL THEN round(((o.ask_at_place - o.his_level) * 100)::numeric, 0) END AS band_c") in sql
    assert ("CASE WHEN band_c IS NULL THEN 'unread' WHEN band_c <= 0 THEN '<=0' WHEN band_c = 1 THEN '1'"
            " WHEN band_c = 2 THEN '2' WHEN band_c <= 5 THEN '3-5' ELSE '>5' END AS bucket") in sql
    rows, books, _ioc = _statements(sql)
    for needle in ("COALESCE(decision, 'ALL') AS decision, COALESCE(bucket, 'ALL') AS bucket, count(*) AS n",
                   "count(*) FILTER (WHERE filled > 0) AS filled_any",
                   "count(*) FILTER (WHERE filled >= qty) AS filled_whole",
                   "round(sum(greatest(qty - filled, 0))::numeric, 1) AS shares_unfilled",
                   "round(sum(greatest(qty - filled, 0) * his_level)::numeric, 2) AS usd_unfilled",
                   "round(percentile_cont(0.5) WITHIN GROUP (ORDER BY done_s)::numeric, 0) AS done_med_s"):
        assert needle in rows, needle
    for needle in ("bb AS (SELECT book_id, decision, bucket, bool_or(filled > 0) AS any_filled, max(settled_pnl) AS settled"
                   " FROM y GROUP BY 1, 2, 3)",
                   "count(*) AS books, count(*) FILTER (WHERE any_filled) AS books_filled",
                   "round(sum(settled) FILTER (WHERE any_filled)::numeric, 2) AS settled_when_filled",
                   "count(*) FILTER (WHERE NOT any_filled) AS books_unfilled",
                   "round(sum(settled) FILTER (WHERE NOT any_filled)::numeric, 2) AS settled_when_unfilled"):
        assert needle in books, needle


def test_exits_band_sits_after_take_band_with_the_help_line_regenerated_and_stays_out_of_the_hourly():
    text = YML.read_text()
    line = next(ln for ln in text.splitlines() if ln.lstrip().startswith('*) echo "sql: arg must be one of'))
    names = line.split("one of ", 1)[1].split(" (got", 1)[0].split("|")
    labels = re.findall(r"^ {16}([a-z0-9-]+)\) ", text[text.index('case "$ARG" in'):text.index(line)], re.M)
    assert names == labels and names.index("exits-band") == names.index("take-band") + 1
    assert names[-1] == "hourly" and "|exits-paired|take-band|exits-band|closed-while-he-traded|fill-answers|hourly (got" in line
    h, _ = _preset(text, "hourly")
    assert "exits-band" not in h and "settled_when_unfilled" not in h and "usd_unfilled" not in h
    hourly.test_the_hourly_preset_is_the_nine_presets_sql_joined_under_section_markers()
    hourly.test_the_help_line_is_the_case_labels_with_hourly_last()
    block = text[text.index("# THE EXIT BAND"):text.index("exits-band) SQL=")]
    for word in ("A8", "REDUCING", "exit_rest / take / cover", "exit_take_in_band / cover_in_band", "<=0 / 1 / 2 / 3-5 / >5",
                 "settled_when_filled / settled_when_unfilled", "ask_at_send", "Read-only",
                 "FILL lane 8", "exit_rest_replaced", "cover_replaced", "PER-IOC SAME-TICK REST LINE", "15 s",
                 "whole / partial / zero", "rested_15s", "$246.20"):
        assert word in block, word
    assert "rest_lag_med_s" not in h and "'== exits-band'" not in h


# ------------------------------------------------- FILL lane 8: the per-IOC same-tick rest line

def test_exits_band_per_ioc_line_reads_every_long_exit_ioc_and_the_same_books_rest_within_15s():
    """Docs 46's live proof, promised by lane 0b and not in exitsband_2304:
    every exit IOC of 24 h on a LONG book (SELL_LONG, tif IOC, whatever
    word it carries -- a row with none still counts) by how it filled, and
    for each the exit_rest row on the SAME book placed within 15 s after
    it; the rest's word is 'exit_rest' or a cause word once a replace
    overwrote it (the HIGH-1 lesson); a rest with no word is not counted."""
    sql, _ = _preset(YML.read_text(), "exits-band")
    _rows, _books, ioc = _statements(sql)
    assert ("CASE WHEN o.filled >= o.qty THEN 'whole' WHEN o.filled > 0 THEN 'partial' ELSE 'zero' END AS ioc_fill"
            " FROM mirror_orders o JOIN mirror_books b ON b.id = o.book_id WHERE b.whale = 'rn1' AND b.intent ="
            " 'ORDER_INTENT_BUY_LONG' AND o.side = 'SELL_LONG' AND o.tif = 'IOC' AND o.placed_at >= now() - interval '24 hours')") in ioc
    assert ("LEFT JOIN LATERAL (SELECT o.id, o.qty, o.filled, o.placed_at FROM mirror_orders o WHERE o.book_id = i.book_id"
            " AND o.side = 'SELL_LONG' AND o.tif <> 'IOC' AND o.decision IN ('exit_rest', 'replace_cent', 'replace_qty',"
            " 'replace_side', 'ttl', 'replace_unread') AND o.placed_at > i.placed_at AND o.placed_at <= i.placed_at"
            " + interval '15 seconds' ORDER BY o.placed_at, o.id LIMIT 1) x ON true)") in ioc
    assert "extract(epoch FROM (x.placed_at - i.placed_at)) AS rest_lag_s" in ioc
    for needle in ("COALESCE(ioc_fill, 'ALL') AS ioc_fill, count(*) AS iocs", "round(sum(qty)::numeric, 1) AS ioc_shares",
                   "round(sum(filled)::numeric, 1) AS ioc_filled_sh", "count(rest_id) AS rested_15s",
                   "count(*) FILTER (WHERE rest_filled > 0) AS rest_filled_any", "round(sum(rest_qty)::numeric, 1) AS rest_shares",
                   "round(sum(rest_filled)::numeric, 1) AS rest_filled_sh",
                   "round(percentile_cont(0.5) WITHIN GROUP (ORDER BY rest_lag_s)::numeric, 1) AS rest_lag_med_s",
                   "FROM r GROUP BY ROLLUP (ioc_fill) ORDER BY 1;"):
        assert needle in ioc, needle
    assert "ORDER_INTENT_BUY_SHORT" not in ioc and "'BUY_LONG'" not in ioc, "long books only (lane 1 rests nothing on a short)"
    assert "o.decision IN ('take'" not in ioc and "= 'take'" not in ioc, "every exit IOC, whatever its word"


# book 334's shape (exitspaired_2304 539: cfb-smu-flst spread, LONG, his exit 0.549,
# our 358 IOC filled 0.50 at a lag of 279 s) with lane 1's same-tick rest: the IOC
# 9341 for 358 fills 179 at T, the 179 rest 9342 placed 3 s after it ('exit_rest',
# open). Beside it: book 335, an IOC filled whole with no rest; book 336, an IOC
# filled nothing whose rest came 20 s later (outside 15 s: NOT counted); book 338,
# a partial IOC whose 3 s rest was later REPLACED (decision overwritten
# 'replace_cent', 20 of 50 filled: counted -- the HIGH-1 lesson) and book 337, a
# SHORT whose cover IOC is not in the population. Review (FILL_L8_review LOW):
# 9352, a SELL_LONG GTC row with NO decision word 5 s after 335's whole IOC, is
# not counted as its same-tick rest (fail closed: a rest with no word is unread).
FIXTURE_334 = """
INSERT INTO whales (id, address, username) VALUES (99, '0xrn1-l0', 'RN1');
INSERT INTO markets (condition_id, title, slug, event_title, sport, resolved_prices, resolved) VALUES
 ('c334', 'smu-flst', 'cfb-smu-flst-2026-09-07-spread', 'ev', 'cfb', '["0", "1"]'::jsonb, true),
 ('c335', 'B', 'cfb-b-2026-09-07', 'ev', 'cfb', '["1", "0"]'::jsonb, true),
 ('c336', 'C', 'cfb-c-2026-09-07', 'ev', 'cfb', '["1", "0"]'::jsonb, true),
 ('c337', 'D', 'cfb-d-2026-09-07', 'ev', 'cfb', '["0", "1"]'::jsonb, true),
 ('c338', 'E', 'cfb-e-2026-09-07', 'ev', 'cfb', '["1", "0"]'::jsonb, true);
INSERT INTO market_tokens (token_id, condition_id, outcome, outcome_index) VALUES
 ('L34', 'c334', 'a', 0), ('O34', 'c334', 'b', 1), ('L35', 'c335', 'a', 0), ('O35', 'c335', 'b', 1),
 ('L36', 'c336', 'a', 0), ('O36', 'c336', 'b', 1), ('L37', 'c337', 'a', 0), ('O37', 'c337', 'b', 1),
 ('L38', 'c338', 'a', 0), ('O38', 'c338', 'b', 1);
INSERT INTO mirror_books (id, whale, condition_id, us_market_slug, long_asset, other_asset, intent, ratio, state, target, target_raw, his_net, ledger_net, last_plan, peak_exposure_usd, avg_cost, settled_pnl, opened_at, closed_at, last_reason, flow_base) VALUES
 (334, 'rn1', 'c334', 'cfb-smu-flst-2026-09-07-spread', 'L34', 'O34', 'ORDER_INTENT_BUY_LONG', 0.1, 'closed', 0, 0.0, 0, 179, '{}'::jsonb, 204.0, 0.57, -131.17, now() - interval '6 hours', now() - interval '1 hour', 'closed: standing row settled', 0),
 (335, 'rn1', 'c335', 'cfb-b-2026-09-07', 'L35', 'O35', 'ORDER_INTENT_BUY_LONG', 0.1, 'closed', 0, 0.0, 0, 0, '{}'::jsonb, 50.0, 0.50, 10.0, now() - interval '6 hours', now() - interval '1 hour', 'closed_cashed_out', 0),
 (336, 'rn1', 'c336', 'cfb-c-2026-09-07', 'L36', 'O36', 'ORDER_INTENT_BUY_LONG', 0.1, 'closed', 0, 0.0, 0, 100, '{}'::jsonb, 50.0, 0.50, 50.0, now() - interval '6 hours', now() - interval '1 hour', 'closed: standing row settled', 0),
 (337, 'rn1', 'c337', 'cfb-d-2026-09-07', 'L37', 'O37', 'ORDER_INTENT_BUY_SHORT', 0.1, 'closed', 0, 0.0, 0, 0, '{}'::jsonb, 40.0, 0.60, 5.0, now() - interval '6 hours', now() - interval '1 hour', 'closed_cashed_out', 0),
 (338, 'rn1', 'c338', 'cfb-e-2026-09-07', 'L38', 'O38', 'ORDER_INTENT_BUY_LONG', 0.1, 'closed', 0, 0.0, 0, 30, '{}'::jsonb, 50.0, 0.50, 20.0, now() - interval '6 hours', now() - interval '1 hour', 'closed: standing row settled', 0);
INSERT INTO mirror_orders (id, book_id, whale, us_market_slug, kind, side, tif, his_level, price, wire, qty, state, filled, avg_px, bid_at_place, ask_at_place, ask_at_send, placed_at, done_at, reason, decision) VALUES
 (9341, 334, 'rn1', 'cfb-smu-flst-2026-09-07-spread', 'take', 'SELL_LONG', 'IOC', 0.549, 0.54, 0.54, 358, 'expired', 179, 0.54, 0.54, 0.56, NULL, now() - interval '3 hours', now() - interval '3 hours' + interval '1 second', 'take', 'take'),
 (9342, 334, 'rn1', 'cfb-smu-flst-2026-09-07-spread', 'reduce', 'SELL_LONG', 'GTC', 0.549, 0.55, 0.55, 179, 'open', 0, NULL, 0.53, 0.56, NULL, now() - interval '3 hours' + interval '3 seconds', NULL, 'reduce', 'exit_rest'),
 (9351, 335, 'rn1', 'cfb-b-2026-09-07', 'take', 'SELL_LONG', 'IOC', 0.60, 0.59, 0.59, 100, 'filled', 100, 0.59, 0.59, 0.61, NULL, now() - interval '3 hours', now() - interval '3 hours' + interval '1 second', 'take', 'take'),
 (9352, 335, 'rn1', 'cfb-b-2026-09-07', 'reduce', 'SELL_LONG', 'GTC', 0.60, 0.60, 0.60, 100, 'open', 0, NULL, 0.59, 0.61, NULL, now() - interval '3 hours' + interval '5 seconds', NULL, 'reduce', NULL),
 (9361, 336, 'rn1', 'cfb-c-2026-09-07', 'take', 'SELL_LONG', 'IOC', 0.40, 0.39, 0.39, 100, 'expired', 0, NULL, 0.38, 0.41, NULL, now() - interval '3 hours', now() - interval '3 hours' + interval '1 second', 'take', 'take'),
 (9362, 336, 'rn1', 'cfb-c-2026-09-07', 'reduce', 'SELL_LONG', 'GTC', 0.40, 0.40, 0.40, 100, 'open', 0, NULL, 0.38, 0.41, NULL, now() - interval '3 hours' + interval '20 seconds', NULL, 'reduce', 'exit_rest'),
 (9371, 337, 'rn1', 'cfb-d-2026-09-07', 'take', 'BUY_LONG', 'IOC', 0.40, 0.40, 0.40, 50, 'filled', 50, 0.40, 0.39, 0.40, 0.40, now() - interval '3 hours', now() - interval '3 hours' + interval '1 second', 'cover', 'cover'),
 (9381, 338, 'rn1', 'cfb-e-2026-09-07', 'take', 'SELL_LONG', 'IOC', 0.50, 0.49, 0.49, 80, 'expired', 30, 0.49, 0.49, 0.51, NULL, now() - interval '3 hours', now() - interval '3 hours' + interval '1 second', 'take', 'take'),
 (9382, 338, 'rn1', 'cfb-e-2026-09-07', 'reduce', 'SELL_LONG', 'GTC', 0.50, 0.50, 0.50, 50, 'cancelled', 20, 0.50, 0.48, 0.51, NULL, now() - interval '3 hours' + interval '3 seconds', now() - interval '3 hours' + interval '90 seconds', 'replace', 'replace_cent');
"""


@pytest.fixture(scope="module")
def world_334():
    w = World(FIXTURE_334, "exits-band lane 8")
    try:
        yield w
    finally:
        w.close()


def test_exits_band_per_ioc_line_on_book_334s_shape_counts_the_same_tick_rest(world_334):
    sql, _ = _preset(YML.read_text(), "exits-band")
    _rows, _books, ioc = _statements(sql)
    world_334.run(sql)
    by = {r["ioc_fill"]: r for r in world_334.rows(ioc)}
    assert set(by) == {"ALL", "whole", "partial", "zero"}
    a = by["ALL"]
    assert a["iocs"] == 4 and _f(a["ioc_shares"]) == 638.0 and _f(a["ioc_filled_sh"]) == 309.0     # 358 + 100 + 100 + 80; 179 + 100 + 30
    assert a["rested_15s"] == 2 and a["rest_filled_any"] == 1 and _f(a["rest_shares"]) == 229.0 and _f(a["rest_filled_sh"]) == 20.0
    p = by["partial"]
    assert p["iocs"] == 2 and p["rested_15s"] == 2 and _f(p["rest_lag_med_s"]) == 3.0 and _f(p["rest_shares"]) == 229.0
    assert p["rest_filled_any"] == 1 and _f(p["rest_filled_sh"]) == 20.0, "338's replaced rest is counted, 20 filled"
    assert by["whole"]["iocs"] == 1 and by["whole"]["rested_15s"] == 0 and by["whole"]["rest_lag_med_s"] is None, \
        "9352 (no decision word, 5 s after 9351) is never a same-tick rest"
    assert by["whole"]["rest_shares"] is None
    assert by["zero"]["iocs"] == 1 and by["zero"]["rested_15s"] == 0, "336's rest came 20 s later: outside 15 s"
    assert "cover" not in by and a["iocs"] == 4, "337's cover IOC (a short) is not in the population"
