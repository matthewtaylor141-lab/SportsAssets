"""The `closed-while-he-traded` render-ops preset (FILL lane 0b item 5,
2026-09-08): per book closed in the last 24 h by the MIRROR'S OWN close
(the plan's close reads cashed_out / cancelled, or last_reason reads
closed_cashed_out / closed_cancelled -- post_booksnew_1707 385: book 309's
last_reason reads `on target` while its plan reads close cashed_out, the
same tick's plan write having overwritten the close's reason, so both are
read; never the settle's `closed: standing row *`), his fills AFTER the
close on the condition, his P&L on them at his price, his net now, the
next book and its lag, and the reopen path from mirror_candidate_refusals
after closed_at; then the totals and the books by first refusal name. The
three sign-flip closes of 2026-09-08 (309 / 338 / 630 with 151,699.7 /
57,113.2 / 52,996.0 later shares, post_fvv_1707 332 / 338 / 340) are its
first read. The pins: three statements on one chain, read-only, LIMITed,
the population clause, the chain-first collapse of his rows, the later
fills' measures, the next book, the reopen path in the cand-refusals
idiom, the totals and the by-name block; the case label after exits-band,
the help line, NOT in the hourly; and on the scratch database a 309-shaped
close (plan close cashed_out, last_reason overwritten) beside a
settle-closed book that never enters the set.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests import test_render_ops_hourly as hourly
from tests.test_render_ops_fills_missed import World, _f

YML = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "render-ops.yml"


def _preset(text: str, name: str) -> tuple[str, int]:
    m = re.search(r'^ {16}' + re.escape(name) + r'\) SQL="(.*?)"; TO=(\d+)', text, re.M | re.S)
    assert m, name
    return m.group(1), int(m.group(2))


def _statements(sql: str) -> list[str]:
    parts = sql.split("; WITH cb AS (")
    assert len(parts) == 3, "three statements, all on the chain"
    return [parts[0] + ";", "WITH cb AS (" + parts[1] + ";", "WITH cb AS (" + parts[2]]


def test_closed_while_he_traded_is_three_read_only_statements_on_one_chain_limited():
    text = YML.read_text()
    # FILL lane 4 placed fill-answers (its own comment block) between this preset and the hourly
    block = text[text.index("closed-while-he-traded) SQL="):text.index("# THE PER-FILL RECORD'S HEALTH LINE")]
    assert "need_confirm" not in block and "$ARG" not in block and "HEAD=" not in block
    for bad in ("INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "ALTER"):
        assert bad not in block, bad
    sql, to = _preset(text, "closed-while-he-traded")
    assert to == 60000 and block.rstrip().endswith('"; TO=60000 ;;')
    rows, totals, by_name = _statements(sql)
    end = "LEFT JOIN rp ON rp.book = cb.book)"
    chains = {s[:s.index(end)] for s in (rows, totals, by_name)}
    assert len(chains) == 1, "one chain, byte for byte"
    assert rows.endswith("FROM v ORDER BY later_usd DESC NULLS LAST, book LIMIT 60;") and rows.count("LIMIT 60") == 1
    assert totals.endswith("AS sign_flips FROM v;")
    assert by_name.endswith("FROM v GROUP BY 1 ORDER BY 4 DESC NULLS LAST, 1;")


def test_closed_while_he_traded_reads_the_mirrors_own_close_by_plan_or_reason_never_the_settles():
    sql, _ = _preset(YML.read_text(), "closed-while-he-traded")
    assert ("FROM mirror_books b WHERE b.whale = 'rn1' AND b.state = 'closed' AND b.closed_at >= now() - interval"
            " '24 hours' AND (b.last_reason IN ('closed_cashed_out', 'closed_cancelled') OR b.last_plan->>'close' IN"
            " ('cashed_out', 'cancelled')))") in sql
    assert "standing row" not in sql and "venue_market_ended" not in sql
    for col in ("b.last_plan->>'close' AS close", "b.last_plan->'sign_flip' AS sign_flip", "b.last_plan->'turn' AS turn",
                "b.last_plan->'reopen_refused' AS reopen_refused", "(b.last_plan->>'net')::float8 AS his_net_at_close"):
        assert col in sql, col


def test_closed_while_he_traded_collapses_his_rows_chain_first_and_measures_the_later_fills():
    sql, _ = _preset(YML.read_text(), "closed-while-he-traded")
    # the D1 collapse: a per-match row under a net-leg key is dropped (fills-vs-venue's j / paired-day's read)
    assert ("bool_or(t.source IN ('chain', 's1')) OVER (PARTITION BY t.whale_id, COALESCE(lower(NULLIF(t.tx_hash, '')),"
            " 'row:' || t.id::text), t.asset, upper(t.side)) AS has_net FROM trades t WHERE t.whale_id IN (SELECT id FROM"
            " whales WHERE lower(username) = 'rn1') AND t.ts >= now() - interval '48 hours' AND t.condition_id IN (SELECT"
            " condition_id FROM cb)) d WHERE d.source IN ('chain', 's1') OR NOT d.has_net)") in sql
    # the later fills: after closed_at, on the condition; his P&L at his price where resolved
    assert "FROM cb JOIN r ON r.condition_id = cb.condition_id AND r.ts > cb.closed_at" in sql
    for needle in ("count(*) AS later_n", "round(sum(r.size * r.price)::numeric, 2) AS later_usd",
                   "round(sum(r.size)::numeric, 1) AS later_sh",
                   "(CASE WHEN r.side = 'BUY' THEN 1 ELSE -1 END) * r.size *"
                   " ((m.resolved_prices->>mt.outcome_index)::numeric - r.price) END)::numeric, 2) AS later_pnl",
                   "max(r.ts) AS his_last"):
        assert needle in sql, needle
    # his net now on the book's axis: long minus other, the mirror's own his_net
    assert ("round(sum((CASE WHEN r.side = 'BUY' THEN 1 ELSE -1 END) * r.size * (CASE WHEN r.asset = cb.long_asset THEN 1"
            " WHEN r.asset = cb.other_asset THEN -1 ELSE 0 END))::numeric, 1) AS his_net_now") in sql
    # the next book on the condition and the reopen path, cand-refusals' idiom
    assert ("LEFT JOIN LATERAL (SELECT b2.id, b2.opened_at, b2.state, b2.settled_pnl FROM mirror_books b2 WHERE"
            " b2.whale = 'rn1' AND b2.condition_id = cb.condition_id AND b2.opened_at > cb.closed_at ORDER BY"
            " b2.opened_at LIMIT 1) n ON true") in sql
    assert ("count(x.at) AS refusals, (array_agg(x.refusal ORDER BY x.at))[1] AS first_refusal,"
            " left(string_agg(x.refusal || '@' || to_char(x.at, 'HH24:MI'), ' ' ORDER BY x.at), 200) AS path") in sql
    assert "x.condition_id = cb.condition_id AND x.at > cb.closed_at" in sql
    rows, totals, by_name = _statements(sql)
    assert "round(extract(epoch FROM (next_opened - closed_at))::numeric, 0) AS reopen_lag_s" in rows
    for needle in ("count(*) AS books", "count(*) FILTER (WHERE later_n > 0) AS with_later_fills",
                   "round(sum(later_usd)::numeric, 2) AS later_usd", "round(sum(later_sh)::numeric, 1) AS later_sh",
                   "round(sum(later_pnl)::numeric, 2) AS later_pnl_at_his_px", "count(next_book) AS reopened_n",
                   "count(*) FILTER (WHERE next_book IS NULL AND refusals > 0) AS refused_only_n",
                   "count(*) FILTER (WHERE next_book IS NULL AND COALESCE(refusals, 0) = 0) AS no_verdict_n",
                   "count(*) FILTER (WHERE sign_flip = 'true'::jsonb) AS sign_flips"):
        assert needle in totals, needle
    assert by_name.endswith("SELECT COALESCE(first_refusal, 'none') AS first_refusal, count(*) AS books, count(*) FILTER"
                            " (WHERE later_n > 0) AS with_later_fills, round(sum(later_usd)::numeric, 2) AS later_usd,"
                            " count(next_book) AS reopened_n FROM v GROUP BY 1 ORDER BY 4 DESC NULLS LAST, 1;")


def test_closed_while_he_traded_sits_after_exits_band_before_hourly_and_stays_out_of_it():
    text = YML.read_text()
    line = next(ln for ln in text.splitlines() if ln.lstrip().startswith('*) echo "sql: arg must be one of'))
    names = line.split("one of ", 1)[1].split(" (got", 1)[0].split("|")
    labels = re.findall(r"^ {16}([a-z0-9-]+)\) ", text[text.index('case "$ARG" in'):text.index(line)], re.M)
    assert names == labels and names.index("closed-while-he-traded") == names.index("exits-band") + 1
    # FILL lane 4 (2026-09-08): fill-answers sits after this label, hourly still last (names[-2] -> names[-3])
    assert names[-1] == "hourly" and names[-2] == "fill-answers" and names[-3] == "closed-while-he-traded"
    h, _ = _preset(text, "hourly")
    assert "closed-while-he-traded" not in h and "later_pnl_at_his_px" not in h and "first_refusal" not in h
    hourly.test_the_hourly_preset_is_the_nine_presets_sql_joined_under_section_markers()
    hourly.test_the_help_line_is_the_case_labels_with_hourly_last()
    block = text[text.index("# CLOSED WHILE HE TRADED"):text.index("closed-while-he-traded) SQL=")]
    for word in ("309 / 338 / 630", "MIRROR'S OWN close", "cashed_out / cancelled", "closed_cashed_out / closed_cancelled",
                 "overwrite last_reason", "standing row", "turn / reopen_refused", "lane 5", "mirror_candidate_refusals",
                 "refused_only_n", "FIRST refusal", "Read-only, LIMITed"):
        assert word in block, word


# --------------------------------------------------------- the scratch database

# book 309's shape (post_booksnew_1707 385; verify_1750 359): a LONG book the
# flip closed -- plan close cashed_out, sign_flip true, net -16589.9 -- whose
# last_reason the same tick's plan write left at `on target`; his fills AFTER
# the close (two BUYs of the OTHER token, 3 h and 2 h ago), the candidate
# refused `drift` then `side_band` after the close, no next book. Beside it
# book 42 closed by the settle (`closed: standing row settled`, no close on
# the plan) with fills of his after it: never in the set. His net now on
# 41's axis: 100 long bought before the close, 500 other after = -400.
FIXTURE_309 = """
INSERT INTO whales (id, address, username) VALUES (99, '0xrn1-l0', 'RN1');
INSERT INTO markets (condition_id, title, slug, event_title, sport, resolved_prices, resolved) VALUES
 ('c309', 'Karkha v Leatie', 'atp-karkha-leatie-2026-09-07', 'ev', 'tennis', '["0", "1"]'::jsonb, true),
 ('c42', 'Other', 'atp-other-2026-09-07', 'ev', 'tennis', NULL, false);
INSERT INTO market_tokens (token_id, condition_id, outcome, outcome_index) VALUES
 ('L9', 'c309', 'k', 0), ('O9', 'c309', 'l', 1), ('L42', 'c42', 'a', 0), ('O42', 'c42', 'b', 1);
INSERT INTO trades (id, whale_id, tx_hash, asset, condition_id, side, size, price, notional, market_slug, sport, ts, source, detected_at, dedupe_key) VALUES
 (951, 99, '0xg1', 'L9', 'c309', 'BUY', 100, 0.40, 40, 'atp-karkha-leatie-2026-09-07', 'tennis', now() - interval '6 hours', 'chain', now() - interval '6 hours' + interval '2 seconds', 'g1'),
 (952, 99, '0xg2', 'O9', 'c309', 'BUY', 200, 0.60, 120, 'atp-karkha-leatie-2026-09-07', 'tennis', now() - interval '3 hours', 'chain', now() - interval '3 hours' + interval '2 seconds', 'g2'),
 (953, 99, '0xg3', 'O9', 'c309', 'BUY', 300, 0.70, 210, 'atp-karkha-leatie-2026-09-07', 'tennis', now() - interval '2 hours', 'chain', now() - interval '2 hours' + interval '2 seconds', 'g3'),
 (954, 99, '0xg3', 'O9', 'c309', 'BUY', 300, 0.70, 210, 'atp-karkha-leatie-2026-09-07', 'tennis', now() - interval '2 hours', 'poll', now() - interval '2 hours' + interval '9 seconds', 'g3p'),
 (955, 99, '0xg5', 'L42', 'c42', 'BUY', 100, 0.50, 50, 'atp-other-2026-09-07', 'tennis', now() - interval '1 hour', 'chain', now() - interval '1 hour' + interval '2 seconds', 'g5');
INSERT INTO mirror_books (id, whale, condition_id, us_market_slug, long_asset, other_asset, intent, ratio, state, target, target_raw, his_net, ledger_net, last_plan, peak_exposure_usd, avg_cost, settled_pnl, opened_at, closed_at, last_reason, flow_base) VALUES
 (41, 'rn1', 'c309', 'aec-atp-karkha-leatie-2026-09-07', 'L9', 'O9', 'ORDER_INTENT_BUY_LONG', 0.1, 'closed', 0, 0.0, -16589.9, 0, '{"close": "cashed_out", "sign_flip": true, "net": -16589.910328}'::jsonb, 178.0, 0.37, -12.0, now() - interval '8 hours', now() - interval '4 hours', 'on target', 0),
 (42, 'rn1', 'c42', 'aec-atp-other-2026-09-07', 'L42', 'O42', 'ORDER_INTENT_BUY_LONG', 0.1, 'closed', 10, 10.0, 100, 0, '{"kind": "no_plan", "venue_terminal": "MARKET_STATE_EXPIRED"}'::jsonb, 5.0, 0.50, 5.0, now() - interval '8 hours', now() - interval '3 hours', 'closed: standing row settled', 0);
INSERT INTO mirror_candidate_refusals (at, whale, condition_id, us_slug, refusal, his_net, ask, his_px, band) VALUES
 (now() - interval '5 hours', 'rn1', 'c309', 'aec-atp-karkha-leatie-2026-09-07', 'on_target', -100, 0.40, 0.40, 0.15),
 (now() - interval '3 hours' - interval '50 minutes', 'rn1', 'c309', 'aec-atp-karkha-leatie-2026-09-07', 'drift', -200, 0.62, 0.60, 0.15),
 (now() - interval '2 hours', 'rn1', 'c309', 'aec-atp-karkha-leatie-2026-09-07', 'side_band', -500, 0.90, 0.70, 0.15);
"""


@pytest.fixture(scope="module")
def world():
    w = World(FIXTURE_309, "closed-while-he-traded lane 0b")
    try:
        yield w
    finally:
        w.close()


def test_closed_while_he_traded_on_book_309s_shape_reads_the_later_fills_and_the_reopen_path(world):
    sql, _ = _preset(YML.read_text(), "closed-while-he-traded")
    rows, totals, by_name = _statements(sql)
    for s in (rows, totals, by_name):
        world.rows(s)
    world.run(sql)
    out = world.rows(rows)
    assert [r["book"] for r in out] == [41], "the settle's close (42) never enters the set"
    r = out[0]
    assert r["close"] == "cashed_out" and r["sign_flip"] == "true" and r["last_reason"] == "on target"
    assert _f(r["his_net_at_close"]) == -16589.9 and r["turn"] is None and r["reopen_refused"] is None
    # the later fills: 952 and 953 (954 is 953's poll echo under the chain key: collapsed)
    assert r["later_n"] == 2 and _f(r["later_usd"]) == 330.0 and _f(r["later_sh"]) == 500.0
    # his P&L on them at his price: the other token resolved 1: 200 x 0.40 + 300 x 0.30 = 170
    assert _f(r["later_pnl"]) == 170.0
    assert _f(r["his_net_now"]) == -400.0
    assert r["next_book"] is None and r["reopen_lag_s"] is None and r["next_settled"] is None
    # the reopen path after the close: drift then side_band (the on_target row before the close is not)
    assert r["refusals"] == 2 and r["first_refusal"] == "drift"
    assert r["path"].startswith("drift@") and " side_band@" in r["path"] and "on_target" not in r["path"]
    t = world.rows(totals)[0]
    assert t["books"] == 1 and t["with_later_fills"] == 1 and _f(t["later_usd"]) == 330.0
    assert _f(t["later_sh"]) == 500.0 and _f(t["later_pnl_at_his_px"]) == 170.0
    assert t["reopened_n"] == 0 and t["refused_only_n"] == 1 and t["no_verdict_n"] == 0 and t["sign_flips"] == 1
    n = world.rows(by_name)
    assert [(x["first_refusal"], x["books"], x["with_later_fills"], x["reopened_n"]) for x in n] == [("drift", 1, 1, 0)]
