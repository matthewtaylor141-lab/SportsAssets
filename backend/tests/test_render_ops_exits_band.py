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
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests import test_render_ops_hourly as hourly
from tests.test_render_ops_fills_missed import World, _f

YML = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "render-ops.yml"
CAUSES = "('replace_cent', 'replace_qty', 'replace_side', 'ttl', 'replace_unread')"


def _preset(text: str, name: str) -> tuple[str, int]:
    m = re.search(r'^ {16}' + re.escape(name) + r'\) SQL="(.*?)"; TO=(\d+)', text, re.M | re.S)
    assert m, name
    return m.group(1), int(m.group(2))


def _statements(sql: str) -> list[str]:
    parts = sql.split("; WITH x AS (")
    assert len(parts) == 2, "two statements, both on the chain"
    return [parts[0] + ";", "WITH x AS (" + parts[1]]


def test_exits_band_is_two_read_only_statements_on_one_chain():
    text = YML.read_text()
    block = text[text.index("exits-band) SQL="):text.index("# CLOSED WHILE HE TRADED")]
    assert "need_confirm" not in block and "$ARG" not in block and "HEAD=" not in block
    for bad in ("INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "ALTER"):
        assert bad not in block, bad
    sql, to = _preset(text, "exits-band")
    assert to == 60000 and block.rstrip().endswith('"; TO=60000 ;;')
    rows, books = _statements(sql)
    chain_end = " END AS bucket, extract(epoch FROM (done_at - placed_at)) AS done_s FROM x)"
    assert rows[:rows.index(chain_end)] == books[:books.index(chain_end)], "one chain, byte for byte"
    assert rows.endswith("FROM y GROUP BY ROLLUP (decision, bucket) ORDER BY 1, 2;")
    assert books.endswith("FROM bb GROUP BY ROLLUP (decision, bucket) ORDER BY 1, 2;")


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
    assert ("SELECT o.id, o.book_id, CASE WHEN o.decision IN " + CAUSES + " THEN CASE WHEN b.intent ="
            " 'ORDER_INTENT_BUY_SHORT' THEN 'cover' ELSE 'exit_rest' END ELSE o.decision END AS decision,"
            " o.decision IN " + CAUSES + " AS replaced, o.side,") in sql
    assert "count(*) FILTER (WHERE replaced) AS replaced_n" in sql
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
    rows, books = _statements(sql)
    world.run(sql)
    by = {(r["decision"], r["bucket"]): r for r in world.rows(rows)}
    assert by[("ALL", "ALL")]["n"] == 4, "the two replaced rests are in the set"
    r = by[("exit_rest", "2")]
    assert r["n"] == 3 and r["replaced_n"] == 2 and r["filled_any"] == 0
    assert _f(r["shares_unfilled"]) == 300.0 and _f(r["usd_unfilled"]) == 163.0    # 55 + 54 + 54
    assert by[("cover", "2")]["n"] == 1 and by[("cover", "2")]["replaced_n"] == 0 and by[("cover", "2")]["filled_whole"] == 1
    assert not [k for k in by if k[0] in ("replace_cent", "ttl", "rest")], "a cause is never a word of its own"
    bb = {(r["decision"], r["bucket"]): r for r in world.rows(books)}
    assert bb[("exit_rest", "2")]["books_unfilled"] == 1 and _f(bb[("exit_rest", "2")]["settled_when_unfilled"]) == 50.0
    assert bb[("cover", "2")]["books_filled"] == 1 and _f(bb[("cover", "2")]["settled_when_filled"]) == -40.0


def test_exits_band_measures_the_cents_past_his_price_on_each_side_and_buckets_them():
    sql, _ = _preset(YML.read_text(), "exits-band")
    assert ("CASE WHEN o.his_level IS NULL THEN NULL WHEN o.side = 'SELL_LONG' AND o.bid_at_place IS NOT NULL THEN"
            " round(((o.his_level - o.bid_at_place) * 100)::numeric, 0) WHEN o.side = 'BUY_LONG' AND"
            " o.ask_at_place IS NOT NULL THEN round(((o.ask_at_place - o.his_level) * 100)::numeric, 0) END AS band_c") in sql
    assert ("CASE WHEN band_c IS NULL THEN 'unread' WHEN band_c <= 0 THEN '<=0' WHEN band_c = 1 THEN '1'"
            " WHEN band_c = 2 THEN '2' WHEN band_c <= 5 THEN '3-5' ELSE '>5' END AS bucket") in sql
    rows, books = _statements(sql)
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
                 "settled_when_filled / settled_when_unfilled", "ask_at_send", "Read-only"):
        assert word in block, word
