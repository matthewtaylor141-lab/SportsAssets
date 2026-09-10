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

FILL lane 0b (2026-09-08; book 266: he cut 30 % and we held 1,955 sh to
-977.65, read `no_exit_by_him` because the 10 % rule never fired;
hourly_1737 1424): `trough` = his lowest running net after the peak,
`his_reduced_pct` = 1 - trough / peak, and the verdict he_reduced_we_held
(no exit by the 10 % rule, his_reduced_pct >= 0.25, under half our peak
filled after his first reducing fill) read BEFORE no_exit_by_him -- a
CASE reads in order and the arm is that verdict's own sub-case -- counted
in the stuck dollars and the totals. VERDICTS gains the word in its CASE
place; the scratch-database pin at the end reads 266's shape.

FILL lane 8 (2026-09-09; C5's read of exitspaired_2304 rows 532-575: six of
the twenty stuck he_reduced_we_held books had our filled fraction within
five points of his cut -- $1,088.27 of the $2,900.48 is the E12 pro-rata
ratchet following him): the arm `we_matched_his_cut` -- no exit by the
10 % rule, his_reduced_pct >= 0.25, |our filled fraction - his_reduced_pct|
<= 0.05 (0.05 chosen) -- BEFORE he_reduced_we_held, NOT in the stuck
dollars, counted as matched_cut_n in the totals. VERDICTS gains it in
place; the fixture gains book 269 (he cut 0.30, we sold 0.28 -> matched)
and book 270, 683's shape (he cut 0.43, we sold 0.35: eight points ->
still he_reduced_we_held); the totals re-pinned to five books.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests import test_render_ops_hourly as hourly
from tests.test_render_ops_fills_missed import World, _f

YML = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "render-ops.yml"
VERDICTS = ("we_matched_his_cut", "he_reduced_we_held", "no_exit_by_him", "exited_with_him", "partial_exit",
            "exit_placed_unfilled", "no_exit_order")
# he_reduced_we_held since FILL lane 0b, before no_exit_by_him (its sub-case); we_matched_his_cut since FILL
# lane 8, before he_reduced_we_held (the ratchet's match is not a hold)
MATCHED = ("WHEN hs.exit_ts IS NULL AND (1 - COALESCE(hs.trough, hs.peak) / hs.peak) >= 0.25"
           " AND abs(COALESCE(ox.our_exit_filled, 0) / NULLIF(op.our_peak, 0) - (1 - COALESCE(hs.trough, hs.peak) / hs.peak))"
           " <= 0.05 THEN 'we_matched_his_cut' ")
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
        assert "hn.net_after, hn.net_after <= 0.10 * pk.peak AS out, hn.leg < 0 AS reducing" in stmt
        assert "WHERE (hn.ts, hn.id) > (pk.peak_ts, pk.peak_id)" in stmt
        assert "h.side" not in stmt and "hn.side" not in stmt, "no BUY / SELL reading of his exit"
        # his exit price and dollars on the long axis; the dollars its complement on a short
        assert "min(hr.net_after) AS trough, min(hr.ts) FILTER (WHERE hr.reducing) AS exit_from" in stmt
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
    # FILL lane 0b: the reduction the 10 % rule hides, read before no_exit_by_him (0.25 / 0.5 chosen);
    # FILL lane 8: the match within five points read before it (0.05 chosen), in BOTH statements
    assert (MATCHED + "WHEN hs.exit_ts IS NULL AND (1 - COALESCE(hs.trough, hs.peak) / hs.peak) >= 0.25"
            " AND COALESCE(ox.our_exit_filled, 0) / NULLIF(op.our_peak, 0) < 0.5 THEN 'he_reduced_we_held'"
            " WHEN hs.exit_ts IS NULL THEN 'no_exit_by_him'") in case
    assert sql.count(MATCHED) == 2 and case.count("'we_matched_his_cut'") == 1
    assert "CASE WHEN hs.peak > 0 THEN round((1 - COALESCE(hs.trough, hs.peak) / hs.peak)::numeric, 2) END AS his_reduced_pct" in rows
    assert "WHEN ox.our_exit_filled / NULLIF(op.our_peak, 0) >= 0.8 THEN 'exited_with_him'" in case
    assert "WHEN ox.our_exit_filled > 0 THEN 'partial_exit'" in case
    assert "WHEN ox.our_exit_placed > 0 THEN 'exit_placed_unfilled'" in case
    assert "his_fills_unseen" not in sql, "the guard's old name: it now also covers a net never on the axis"
    # the stuck dollars: settled NEGATIVE on the three verdicts that left us in, as a positive figure
    assert ("CASE WHEN verdict IN ('partial_exit', 'exit_placed_unfilled', 'no_exit_order', 'he_reduced_we_held')"
            " AND our_settled < 0 THEN -our_settled END AS " + STUCK) in rows      # the fourth word since FILL lane 0b
    assert "we_matched_his_cut'" not in rows[rows.index("CASE WHEN verdict IN ("):], "FILL lane 8: a match is never stuck"
    assert rows.endswith("FROM v ORDER BY %s DESC NULLS LAST, book LIMIT 60;" % STUCK)
    cols = rows[rows.rindex(" SELECT ") + len(" SELECT "):rows.rindex(" FROM v ")].split(", ")
    assert cols == ["book", "market", "side", "his_peak", "his_exit_from", "his_exit_ts", "his_exit_px",
                    "his_exit_usd", "his_reduced_pct", "his_pnl", "our_peak", "our_exit_ts", "our_exit_px",
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
                   "count(*) FILTER (WHERE verdict = 'he_reduced_we_held') AS reduced_we_held",
                   "round(sum(stuck_after_his_exit_usd) FILTER (WHERE verdict = 'he_reduced_we_held')::numeric, 2)"
                   " AS reduced_we_held_stuck_usd, count(*) FILTER (WHERE verdict = 'we_matched_his_cut') AS matched_cut_n,",
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
    # FILL lane 0b's three read presets sit between exits-paired and hourly (was "nf-venue|exits-paired|hourly")
    # E31 (FILL lane 31): `maker-rests` joins them between take-band and exits-band
    assert names[-1] == "hourly" and ("nf-venue|exits-paired|take-band|maker-rests|exits-band"
                                      "|closed-while-he-traded|fill-answers|hourly (got") in line
    # the hourly line joins the five it always joined: this preset is not one of them
    hourly_sql, _ = _preset(text, "hourly")
    assert "exits-paired" not in hourly_sql and STUCK not in hourly_sql and "his_exit_from" not in hourly_sql
    # E31 (FILL lane 31, 2026-09-10): `AS leg,` no longer tells this preset apart --
    # the lane's own `maker-rests` section, which DOES ride the hourly, names a leg
    # too (add / reduce beside each rest's cent). The three fingerprints above are
    # this preset's own; here the pin is the section marker, which cannot collide
    assert "'== exits-paired'" not in hourly_sql and "'== maker-rests'" in hourly_sql
    hourly.test_the_hourly_preset_is_the_nine_presets_sql_joined_under_section_markers()
    hourly.test_the_hourly_preset_is_read_only_with_its_own_output_cap_and_timeout()
    hourly.test_the_help_line_is_the_case_labels_with_hourly_last()
    # the comment block over the preset names the rule the columns read by
    block = text[text.index("# THE EXITS, PAIRED"):text.index("exits-paired) SQL=")]
    for word in ("CLOSED in the last 24 h", "LEG ON THE BOOK'S TOKEN AXIS", "his_net, long minus other",
                 "BUYING the No", "-1 on a", "BUY_SHORT book", "10 % of the peak", "his_exit_from",
                 "a No at p is a Yes at 1 - p", "10 s of", "clock skew",
                 "book's intent and the row's plan side", "no_position", "his_net_unseen",
                 "Read-only, LIMITed", "he_reduced_we_held", "his_reduced_pct", "0.25 (chosen)", "0.5 chosen",
                 "BEFORE no_exit_by_him", "FILL lane 8", "we_matched_his_cut", "0.05 chosen", "BEFORE\n"
                 "                # he_reduced_we_held", "NOT in stuck_after_his_exit_usd", "matched_cut_n", "$1,088.27"):
        assert word in block, word


# ------------------------------------------- FILL lane 0b: the scratch database

# book 266's shape (hourly_1737 1424: wta-scott-lepchen, LONG, our 1,955 sh at
# 0.50 held to settlement, -977.65; he cut 30 % of his peak and never reached
# the 10 % rule): his BUY of the long 30,000 (the peak) then a SELL of 9,000
# (running 21,000 = 0.70 x peak: his_reduced_pct 0.30); our one entry filled
# 1,955, no reducing row of ours -> he_reduced_we_held, stuck 977.65. Beside
# it two controls on their own conditions: 267, he cut 30 % and we SOLD 60 %
# of our peak after his first reducing fill -> no_exit_by_him as before; 268,
# he cut 10 % and we held -> no_exit_by_him (under the 0.25 chosen). FILL lane
# 8: 269, he cut 30 % and we sold 28 % of our peak after his first reducing
# fill (two points: the ratchet following him) -> we_matched_his_cut, NOT
# stuck; 270, book 683's shape (exitspaired_2304 534: he cut 0.43, we sold
# 0.35 -- eight points) -> he_reduced_we_held still, stuck 331.99. Review
# (FILL_L8_review LOW): 271, he SOLD 950 of 1,000 (out by the 10 % rule: exit_ts
# set, his_reduced_pct 0.95) and we sold 92 of 100 after his first reducing fill
# (0.92, within five points of 0.95) -> exited_with_him, never we_matched_his_cut
# (the arm reads `hs.exit_ts IS NULL` first: a match is a book he still holds).
FIXTURE_266 = """
INSERT INTO whales (id, address, username) VALUES (99, '0xrn1-l0', 'RN1');
INSERT INTO markets (condition_id, title, slug, event_title, sport, resolved_prices, resolved) VALUES
 ('c266', 'Scott v Lepchenko', 'wta-scott-lepchen-2026-09-07', 'ev', 'tennis', '["0", "1"]'::jsonb, true),
 ('c267', 'Control A', 'wta-ctl-a-2026-09-07', 'ev', 'tennis', '["0", "1"]'::jsonb, true),
 ('c268', 'Control B', 'wta-ctl-b-2026-09-07', 'ev', 'tennis', '["0", "1"]'::jsonb, true),
 ('c269', 'Matched', 'wta-matched-2026-09-07', 'ev', 'tennis', '["0", "1"]'::jsonb, true),
 ('c270', 'AEK v Lin total', 'ucl-aek1-lin2-2026-09-08-total', 'ev', 'soccer', '["0", "1"]'::jsonb, true),
 ('c271', 'Exited', 'wta-exited-2026-09-07', 'ev', 'tennis', '["0", "1"]'::jsonb, true);
INSERT INTO market_tokens (token_id, condition_id, outcome, outcome_index) VALUES
 ('L6', 'c266', 's', 0), ('O6', 'c266', 'l', 1), ('L7', 'c267', 'a', 0), ('O7', 'c267', 'b', 1), ('L8', 'c268', 'c', 0), ('O8', 'c268', 'd', 1),
 ('L9', 'c269', 'e', 0), ('O9', 'c269', 'f', 1), ('L10', 'c270', 'g', 0), ('O10', 'c270', 'h', 1),
 ('L11', 'c271', 'i', 0), ('O11', 'c271', 'j', 1);
INSERT INTO trades (id, whale_id, tx_hash, asset, condition_id, side, size, price, notional, market_slug, sport, ts, source, detected_at, dedupe_key) VALUES
 (961, 99, '0xh1', 'L6', 'c266', 'BUY', 30000, 0.50, 15000, 'wta-scott-lepchen-2026-09-07', 'tennis', now() - interval '6 hours', 'chain', now() - interval '6 hours' + interval '2 seconds', 'h1'),
 (962, 99, '0xh2', 'L6', 'c266', 'SELL', 9000, 0.45, 4050, 'wta-scott-lepchen-2026-09-07', 'tennis', now() - interval '4 hours', 'chain', now() - interval '4 hours' + interval '2 seconds', 'h2'),
 (963, 99, '0xh3', 'L7', 'c267', 'BUY', 1000, 0.50, 500, 'wta-ctl-a-2026-09-07', 'tennis', now() - interval '6 hours', 'chain', now() - interval '6 hours' + interval '2 seconds', 'h3'),
 (964, 99, '0xh4', 'L7', 'c267', 'SELL', 300, 0.45, 135, 'wta-ctl-a-2026-09-07', 'tennis', now() - interval '4 hours', 'chain', now() - interval '4 hours' + interval '2 seconds', 'h4'),
 (965, 99, '0xh5', 'L8', 'c268', 'BUY', 1000, 0.50, 500, 'wta-ctl-b-2026-09-07', 'tennis', now() - interval '6 hours', 'chain', now() - interval '6 hours' + interval '2 seconds', 'h5'),
 (966, 99, '0xh6', 'L8', 'c268', 'SELL', 100, 0.45, 45, 'wta-ctl-b-2026-09-07', 'tennis', now() - interval '4 hours', 'chain', now() - interval '4 hours' + interval '2 seconds', 'h6'),
 (967, 99, '0xh7', 'L9', 'c269', 'BUY', 1000, 0.50, 500, 'wta-matched-2026-09-07', 'tennis', now() - interval '6 hours', 'chain', now() - interval '6 hours' + interval '2 seconds', 'h7'),
 (968, 99, '0xh8', 'L9', 'c269', 'SELL', 300, 0.45, 135, 'wta-matched-2026-09-07', 'tennis', now() - interval '4 hours', 'chain', now() - interval '4 hours' + interval '2 seconds', 'h8'),
 (969, 99, '0xh9', 'L10', 'c270', 'BUY', 1000, 0.50, 500, 'ucl-aek1-lin2-2026-09-08-total', 'soccer', now() - interval '6 hours', 'chain', now() - interval '6 hours' + interval '2 seconds', 'h9'),
 (970, 99, '0xh10', 'L10', 'c270', 'SELL', 430, 0.43, 184.9, 'ucl-aek1-lin2-2026-09-08-total', 'soccer', now() - interval '4 hours', 'chain', now() - interval '4 hours' + interval '2 seconds', 'h10'),
 (971, 99, '0xh11', 'L11', 'c271', 'BUY', 1000, 0.50, 500, 'wta-exited-2026-09-07', 'tennis', now() - interval '6 hours', 'chain', now() - interval '6 hours' + interval '2 seconds', 'h11'),
 (972, 99, '0xh12', 'L11', 'c271', 'SELL', 950, 0.45, 427.5, 'wta-exited-2026-09-07', 'tennis', now() - interval '4 hours', 'chain', now() - interval '4 hours' + interval '2 seconds', 'h12');
INSERT INTO mirror_books (id, whale, condition_id, us_market_slug, long_asset, other_asset, intent, ratio, state, target, target_raw, his_net, ledger_net, last_plan, peak_exposure_usd, avg_cost, settled_pnl, opened_at, closed_at, last_reason, flow_base) VALUES
 (266, 'rn1', 'c266', 'aec-wta-scott-lepchen-2026-09-07', 'L6', 'O6', 'ORDER_INTENT_BUY_LONG', 0.1, 'closed', 1955, 1955.0, 21000, 0, '{}'::jsonb, 977.65, 0.50, -977.65, now() - interval '6 hours', now() - interval '1 hour', 'closed: standing row settled', 0),
 (267, 'rn1', 'c267', 'aec-wta-ctl-a-2026-09-07', 'L7', 'O7', 'ORDER_INTENT_BUY_LONG', 0.1, 'closed', 100, 100.0, 700, 0, '{}'::jsonb, 50.0, 0.50, -20.0, now() - interval '6 hours', now() - interval '1 hour', 'closed: standing row settled', 0),
 (268, 'rn1', 'c268', 'aec-wta-ctl-b-2026-09-07', 'L8', 'O8', 'ORDER_INTENT_BUY_LONG', 0.1, 'closed', 100, 100.0, 900, 0, '{}'::jsonb, 50.0, 0.50, -50.0, now() - interval '6 hours', now() - interval '1 hour', 'closed: standing row settled', 0),
 (269, 'rn1', 'c269', 'aec-wta-matched-2026-09-07', 'L9', 'O9', 'ORDER_INTENT_BUY_LONG', 0.1, 'closed', 100, 100.0, 700, 0, '{}'::jsonb, 50.0, 0.50, -36.0, now() - interval '6 hours', now() - interval '1 hour', 'closed: standing row settled', 0),
 (270, 'rn1', 'c270', 'ucl-aek1-lin2-2026-09-08-total', 'L10', 'O10', 'ORDER_INTENT_BUY_LONG', 0.1, 'closed', 100, 100.0, 570, 0, '{}'::jsonb, 50.0, 0.50, -331.99, now() - interval '6 hours', now() - interval '1 hour', 'closed: standing row settled', 0),
 (271, 'rn1', 'c271', 'aec-wta-exited-2026-09-07', 'L11', 'O11', 'ORDER_INTENT_BUY_LONG', 0.1, 'closed', 100, 100.0, 50, 0, '{}'::jsonb, 50.0, 0.50, -8.6, now() - interval '6 hours', now() - interval '1 hour', 'closed: standing row settled', 0);
INSERT INTO mirror_orders (id, book_id, whale, us_market_slug, kind, side, tif, his_level, price, wire, qty, state, filled, avg_px, bid_at_place, ask_at_place, placed_at, done_at, reason, decision) VALUES
 (9611, 266, 'rn1', 'aec-wta-scott-lepchen-2026-09-07', 'increase', 'BUY_LONG', 'GTC', 0.50, 0.50, 0.50, 1955, 'filled', 1955, 0.50, 0.50, 0.51, now() - interval '6 hours' + interval '10 seconds', now() - interval '6 hours' + interval '100 seconds', 'increase', 'rest'),
 (9631, 267, 'rn1', 'aec-wta-ctl-a-2026-09-07', 'increase', 'BUY_LONG', 'GTC', 0.50, 0.50, 0.50, 100, 'filled', 100, 0.50, 0.50, 0.51, now() - interval '6 hours' + interval '10 seconds', now() - interval '6 hours' + interval '100 seconds', 'increase', 'rest'),
 (9632, 267, 'rn1', 'aec-wta-ctl-a-2026-09-07', 'reduce', 'SELL_LONG', 'GTC', 0.45, 0.45, 0.45, 60, 'filled', 60, 0.45, 0.45, 0.46, now() - interval '4 hours' + interval '20 seconds', now() - interval '4 hours' + interval '60 seconds', 'reduce', 'exit_rest'),
 (9651, 268, 'rn1', 'aec-wta-ctl-b-2026-09-07', 'increase', 'BUY_LONG', 'GTC', 0.50, 0.50, 0.50, 100, 'filled', 100, 0.50, 0.50, 0.51, now() - interval '6 hours' + interval '10 seconds', now() - interval '6 hours' + interval '100 seconds', 'increase', 'rest'),
 (9691, 269, 'rn1', 'aec-wta-matched-2026-09-07', 'increase', 'BUY_LONG', 'GTC', 0.50, 0.50, 0.50, 100, 'filled', 100, 0.50, 0.50, 0.51, now() - interval '6 hours' + interval '10 seconds', now() - interval '6 hours' + interval '100 seconds', 'increase', 'rest'),
 (9692, 269, 'rn1', 'aec-wta-matched-2026-09-07', 'reduce', 'SELL_LONG', 'GTC', 0.45, 0.45, 0.45, 28, 'filled', 28, 0.45, 0.45, 0.46, now() - interval '4 hours' + interval '20 seconds', now() - interval '4 hours' + interval '60 seconds', 'reduce', 'exit_rest'),
 (9701, 270, 'rn1', 'ucl-aek1-lin2-2026-09-08-total', 'increase', 'BUY_LONG', 'GTC', 0.50, 0.50, 0.50, 100, 'filled', 100, 0.50, 0.50, 0.51, now() - interval '6 hours' + interval '10 seconds', now() - interval '6 hours' + interval '100 seconds', 'increase', 'rest'),
 (9702, 270, 'rn1', 'ucl-aek1-lin2-2026-09-08-total', 'reduce', 'SELL_LONG', 'GTC', 0.43, 0.43, 0.43, 35, 'filled', 35, 0.43, 0.43, 0.44, now() - interval '4 hours' + interval '20 seconds', now() - interval '4 hours' + interval '60 seconds', 'reduce', 'exit_rest'),
 (9711, 271, 'rn1', 'aec-wta-exited-2026-09-07', 'increase', 'BUY_LONG', 'GTC', 0.50, 0.50, 0.50, 100, 'filled', 100, 0.50, 0.50, 0.51, now() - interval '6 hours' + interval '10 seconds', now() - interval '6 hours' + interval '100 seconds', 'increase', 'rest'),
 (9712, 271, 'rn1', 'aec-wta-exited-2026-09-07', 'reduce', 'SELL_LONG', 'GTC', 0.45, 0.45, 0.45, 92, 'filled', 92, 0.45, 0.45, 0.46, now() - interval '4 hours' + interval '20 seconds', now() - interval '4 hours' + interval '60 seconds', 'reduce', 'exit_rest');
"""


@pytest.fixture(scope="module")
def world():
    w = World(FIXTURE_266, "exits-paired lane 0b")
    try:
        yield w
    finally:
        w.close()


def test_exits_paired_on_book_266s_shape_reads_he_reduced_we_held_and_the_controls_stay(world):
    sql, _ = _preset(YML.read_text(), "exits-paired")
    rows, totals = _statements(sql)
    world.rows(rows)
    world.rows(totals)
    world.run(sql)
    by = {r["book"]: r for r in world.rows(rows)}
    assert set(by) == {266, 267, 268, 269, 270, 271}      # FILL lane 8: 269 / 270 join (was {266, 267, 268}); 271 the review's
    r = by[266]
    assert r["verdict"] == "he_reduced_we_held" and _f(r["his_reduced_pct"]) == 0.30
    assert _f(r["his_peak"]) == 30000.0 and r["his_exit_ts"] is None and r["his_exit_from"] is not None
    assert _f(r["our_peak"]) == 1955.0 and r["our_exit_filled_pct"] is None and r["our_exit_ts"] is None
    assert _f(r["our_settled"]) == -977.65 and _f(r["stuck_after_his_exit_usd"]) == 977.65
    assert r["held_to_settlement"] is True
    # 267: he cut 30 %, we sold 60 % of our peak after his first reducing fill: no_exit_by_him as before
    assert by[267]["verdict"] == "no_exit_by_him" and _f(by[267]["his_reduced_pct"]) == 0.30
    assert _f(by[267]["our_exit_filled_pct"]) == 0.6 and by[267]["stuck_after_his_exit_usd"] is None
    # 268: he cut 10 %, under the 0.25 chosen: no_exit_by_him
    assert by[268]["verdict"] == "no_exit_by_him" and _f(by[268]["his_reduced_pct"]) == 0.10
    assert by[268]["stuck_after_his_exit_usd"] is None
    # FILL lane 8: 269, he cut 30 % and we sold 28 % after his first reducing fill -> we_matched_his_cut, never stuck
    assert by[269]["verdict"] == "we_matched_his_cut" and _f(by[269]["his_reduced_pct"]) == 0.30
    assert _f(by[269]["our_exit_filled_pct"]) == 0.28 and by[269]["stuck_after_his_exit_usd"] is None
    assert _f(by[269]["our_settled"]) == -36.0, "settled negative, and still not stuck: he held and so did we, at the ratio"
    # 683's shape: he cut 0.43, we sold 0.35 -- eight points -> he_reduced_we_held as before, stuck 331.99
    assert by[270]["verdict"] == "he_reduced_we_held" and _f(by[270]["his_reduced_pct"]) == 0.43
    assert _f(by[270]["our_exit_filled_pct"]) == 0.35 and _f(by[270]["stuck_after_his_exit_usd"]) == 331.99
    # 271: he exited by the 10 % rule (0.95) and we sold 0.92 after his first reducing fill -- within five points,
    # and NOT we_matched_his_cut: the arm is a book he still holds (hs.exit_ts IS NULL); this is exited_with_him
    assert by[271]["verdict"] == "exited_with_him" and by[271]["his_exit_ts"] is not None
    assert _f(by[271]["his_reduced_pct"]) == 0.95 and _f(by[271]["our_exit_filled_pct"]) == 0.92
    assert by[271]["stuck_after_his_exit_usd"] is None and _f(by[271]["lag_s"]) == 20.0
    t = world.rows(totals)[0]
    # FILL lane 8: was books 3, reduced_we_held 1, stuck 977.65 / 977.65 (269 / 270 added); matched_cut_n new;
    # the review's 271 adds one his_exit and one with_him
    assert t["books"] == 6 and t["his_exits"] == 1 and t["he_held"] == 2 and t["reduced_we_held"] == 2
    assert t["matched_cut_n"] == 1 and t["with_him"] == 1 and _f(t["lag_med_s"]) == 20.0
    assert _f(t["reduced_we_held_stuck_usd"]) == 1309.64 and _f(t["stuck_usd"]) == 1309.64     # 977.65 + 331.99
    assert t["partial"] == 0 and t["unfilled"] == 0 and t["no_order"] == 0
