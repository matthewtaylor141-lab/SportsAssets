"""The `fills-missed` render-ops preset after FILL lane 0b item 4
(2026-09-08): the market's STATE at each fill of his (in_book /
before_open / after_close / no_book, from the book windows) as a second
statement grouped by (class, state) beside the ALL rollup, and the
answering row's 059 `decision` with the band the ask sat at (band_c) on
the per-fill rows for a third statement over filled / partial /
missed_expired_ioc grouped by (class, decision). Six statements now: the
classes, (class, state), (class, decision), per book, the band table, the
fill rate by hour. The pins: the state CASE and its LATERAL, the columns
threaded through `o` and `g`, the two new statements' shape, the chain
shared by the first four statements byte for byte, read-only, the case
label and help line untouched; and, on the scratch database the lane M
pins build (skipped without one), book 347's shape -- three books on one
condition (book_347_1750 398: opened 00:15:33, closed 03:44:29) with his
fills before the first open, inside a window, between two books and after
the last close -- read before_open / in_book / no_book / after_close, and
a take row filled whole beside a rest row filled whole read filled / take
and filled / rest with their band.

Also the scratch-database helper the sibling lane 0b pins share
(`World`): the migrations in order on a scratch database, a fixture, the
preset's own text run statement by statement.

FILL lane 8 (2026-09-09; h2225 996: missed_replace 745 fills / $381,750.75,
the largest class, with no cause row -- the (class, decision) block read
filled / partial / missed_expired_ioc only, 1061-1080): `'missed_replace'`
joins that WHERE word in BOTH places (the standalone case and the hourly's
copy; `grep -o | wc -l` reads 2), so the class prints missed_replace x
{replace_cent, replace_qty, replace_side, ttl, replace_unread}; the word
printed is the row's own (COALESCE(decision, 'unrecorded')), never a list.
Book 816's shape on its own scratch fixture: a 1,403 rest cancelled
`replace_qty` after 11 s with 0 filled -> missed_replace | replace_qty, and
the 1,403 with 631.85 filled -> partial | replace_qty.
"""
from __future__ import annotations

import asyncio
import re
import uuid
from pathlib import Path

import pytest

from tests.test_e12_flow_only import DSN_BASE, MIG_DIR
from tests.test_pnl_m_review_pins import US_PREMAP_DDL

YML = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "render-ops.yml"


def _preset(text: str, name: str) -> tuple[str, int]:
    m = re.search(r'^ {16}' + re.escape(name) + r'\) SQL="(.*?)"; TO=(\d+)', text, re.M | re.S)
    assert m, name
    return m.group(1), int(m.group(2))


def _stmts(sql: str) -> list[str]:
    return [s.strip() for s in sql.split("; ") if s.strip()]


def _labels_and_help(text: str) -> tuple[list[str], list[str], str]:
    line = next(ln for ln in text.splitlines() if ln.lstrip().startswith('*) echo "sql: arg must be one of'))
    names = line.split("one of ", 1)[1].split(" (got", 1)[0].split("|")
    labels = re.findall(r"^ {16}([a-z0-9-]+)\) ", text[text.index('case "$ARG" in'):text.index(line)], re.M)
    return names, labels, line


class World:
    """One scratch database: the migrations in order (as migrate.py
    applies them), the fixture handed in, the connection's own loop.
    Skips, never fakes, without a local server (the 69 real-SQL pins'
    idiom)."""

    def __init__(self, fixture: str, label: str):
        asyncpg = pytest.importorskip("asyncpg")
        self.loop = asyncio.new_event_loop()
        try:
            self.admin = self.loop.run_until_complete(asyncpg.connect(DSN_BASE, timeout=4))
        except Exception:  # noqa: BLE001 -- no local PG: skip, never fake
            self.loop.close()
            pytest.skip("no local postgres for the %s pins" % label)
        self.name = "fill_l0_" + uuid.uuid4().hex[:10]
        self.loop.run_until_complete(self.admin.execute(f'CREATE DATABASE "{self.name}"'))
        self.conn = self.loop.run_until_complete(
            asyncpg.connect(DSN_BASE.rsplit("/", 1)[0] + "/" + self.name, timeout=4))
        self.loop.run_until_complete(self._up(fixture))

    async def _up(self, fixture: str):
        await self.conn.execute(US_PREMAP_DDL)
        for path in sorted(MIG_DIR.glob("*.sql")):
            async with self.conn.transaction():
                await self.conn.execute(path.read_text())
        await self.conn.execute(fixture)

    def rows(self, sql: str) -> list[dict]:
        return [dict(r) for r in self.loop.run_until_complete(self.conn.fetch(sql))]

    def run(self, sql: str) -> None:
        self.loop.run_until_complete(self.conn.execute(sql))

    def close(self):
        self.loop.run_until_complete(self.conn.close())
        self.loop.run_until_complete(self.admin.execute(f'DROP DATABASE "{self.name}"'))
        self.loop.run_until_complete(self.admin.close())
        self.loop.close()


def _f(v):
    return None if v is None else float(v)


STATE = ("CASE WHEN ax.in_window THEN 'in_book' WHEN r.ts < bw.first_open - interval '120 seconds' THEN 'before_open'"
         " WHEN NOT bw.any_live AND r.ts >= bw.last_close THEN 'after_close' ELSE 'no_book' END AS state")
IN_WINDOW = "(b.opened_at - interval '120 seconds' <= r.ts AND r.ts < COALESCE(b.closed_at, now()))"
BW = ("JOIN LATERAL (SELECT min(b.opened_at) AS first_open, max(b.closed_at) AS last_close,"
      " bool_or(b.closed_at IS NULL) AS any_live FROM mirror_books b WHERE b.whale = 'rn1' AND"
      " b.condition_id = r.condition_id) bw ON true")
BAND_C = ("CASE WHEN o.his_level IS NULL THEN NULL WHEN o.oside = 'BUY_LONG' AND o.ask_at_place IS NOT NULL THEN"
          " round(((o.ask_at_place::numeric - floor(round((o.his_level * 100)::numeric, 6)) / 100) * 100)::numeric, 1)"
          " WHEN o.oside = 'SELL_LONG' AND o.bid_at_place IS NOT NULL THEN"
          " round(((ceil(round((o.his_level * 100)::numeric, 6)) / 100 - o.bid_at_place::numeric) * 100)::numeric, 1)"
          " END AS band_c")


def test_fills_missed_has_six_statements_on_one_chain_read_only_on_its_own_timeout():
    text = YML.read_text()
    sql, to = _preset(text, "fills-missed")
    assert to == 120000
    for bad in ("INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "need_confirm", "$ARG"):
        assert bad not in sql, bad
    st = _stmts(sql)
    # FILL lane 9 (2026-09-09; migration 061): a SEVENTH statement, last -- the cause block over
    # refused:open_order_pending (COALESCE(cause, 'unrecorded') x path) on the SAME chain plus a `fc` CTE
    assert len(st) == 7
    heads = (" SELECT COALESCE(class, 'ALL') AS class, ", " SELECT class, state, count(*) AS n, ",
             " SELECT class, COALESCE(decision, 'unrecorded') AS decision, count(*) AS n, ",
             " SELECT book, his_slug, class, count(*) AS n, ")
    chains = []
    for s, head in zip(st[:4], heads):
        assert head in s, head
        chains.append(s[:s.index(head)])
    assert len(set(chains)) == 1, "the four fill statements carry ONE chain, byte for byte"
    assert st[4].startswith("SELECT COALESCE(kind, 'ALL') AS kind, count(*) AS orders, count(band_c) AS with_band")
    assert st[5].startswith("SELECT date_trunc('hour', o.placed_at)::time(0) AS hour, o.kind, count(*) AS n")
    cause_head = " SELECT COALESCE(fc.cause, 'unrecorded') AS cause, COALESCE(fc.fast::text, 'unrecorded') AS path, count(*) AS n, "
    assert cause_head in st[6] and st[6].startswith(chains[0] + ", fc AS (SELECT x.fill_id, x.cause, x.fast FROM xmltable(")
    assert st[6].rstrip(";").endswith(" FROM g LEFT JOIN fc ON fc.fill_id = g.id::text WHERE g.class = 'refused:open_order_pending'"
                                      " GROUP BY 1, 2 ORDER BY 4 DESC")
    assert st[3].endswith("GROUP BY 1, 2, 3 ORDER BY 5 DESC LIMIT 60") and st[0].endswith("GROUP BY ROLLUP (class) ORDER BY 3 DESC")


def test_fills_missed_reads_the_state_off_the_book_windows_and_the_decision_off_the_answering_row():
    text = YML.read_text()
    sql, _ = _preset(text, "fills-missed")
    chain = _stmts(sql)[0]
    # the axis LATERAL says whether ITS window holds the fill; a second LATERAL reads the condition's windows
    assert ("(b.intent = 'ORDER_INTENT_BUY_SHORT') AS short, " + IN_WINDOW + " AS in_window FROM mirror_books b"
            " WHERE b.whale = 'rn1' AND b.condition_id = r.condition_id ORDER BY " + IN_WINDOW +
            " DESC, b.id DESC LIMIT 1) ax ON true " + BW + " WHERE") in chain
    assert STATE in chain and chain.index(STATE) < chain.index("AS px FROM r JOIN LATERAL")
    # the answering row's decision, side and placement quote ride `o`; band_c is the band table's own formula
    assert ("ord.filled, ord.qty, ord.placed_at, ord.decision, ord.side AS oside, ord.his_level, ord.bid_at_place,"
            " ord.ask_at_place FROM s LEFT JOIN LATERAL") in chain
    # the review's CRITICAL-1: `f` names the book-window column `state` and `o` carries `s.*`, so the
    # order's own state rides `o` under ANOTHER name -- a bare `ord.state` beside it made every
    # `o.state` in `g` ambiguous on the real schema (the whole chain, and the hourly with it, refused)
    assert "ord.id AS oid, ord.book_id AS obook, ord.state AS ostate, ord.tif, ord.reason" in chain
    assert "o.ostate = 'cancelled' AND COALESCE(o.reason, '') LIKE '%replace%' THEN 'missed_replace'" in chain
    assert "o.state = 'cancelled'" not in chain and chain.count("ord.state") == 1
    assert "AS lag_s, " + BAND_C + ", CASE WHEN m.resolved_prices" in chain
    # the class CASE keeps its words; FILL lane 4 (2026-09-08) reads the per-fill record's name
    # first (`'refused:' || COALESCE(o.e->>'name', 'unnamed')` -> `COALESCE(o.fa_name, o.e->>'name', 'unnamed')`)
    # and `unseen` only when neither the table nor the plan's list holds the fill
    for word in ("'filled'", "'partial'", "'missed_expired_ioc'", "'missed_replace'", "'missed_open'", "'unseen'",
                 "'order_row_unread'", "'refused:' || COALESCE(o.fa_name, o.e->>'name', 'unnamed')"):
        assert word in chain, word
    assert "WHEN o.e IS NULL AND o.fa_name IS NULL THEN 'unseen'" in chain


def test_fills_missed_state_and_decision_statements_carry_the_class_rows_measures():
    text = YML.read_text()
    st = _stmts(_preset(text, "fills-missed")[0])
    roi = ("round((sum((payoff - px) * size) / NULLIF(sum(CASE WHEN payoff IS NOT NULL THEN usd END), 0))::numeric, 4)"
           " AS roi, round((1.96 * stddev_samp((payoff - px) / NULLIF(px, 0)) / sqrt(NULLIF(count(payoff), 0)))::numeric, 4)"
           " AS ci95")
    lag = "round(percentile_cont(0.5) WITHIN GROUP (ORDER BY lag_s)::numeric, 0) AS lag_med_s"
    assert st[1].endswith(" SELECT class, state, count(*) AS n, round(sum(usd)::numeric, 2) AS his_usd, count(payoff) AS"
                          " n_resolved, " + roi + ", " + lag + " FROM g GROUP BY 1, 2 ORDER BY 1, 4 DESC")
    assert st[2].endswith(" SELECT class, COALESCE(decision, 'unrecorded') AS decision, count(*) AS n,"
                          " round(sum(usd)::numeric, 2) AS his_usd, count(payoff) AS n_resolved, " + roi +
                          ", round(percentile_cont(0.5) WITHIN GROUP (ORDER BY band_c)::numeric, 1) AS band_med_c, " + lag +
                          " FROM g WHERE class IN ('filled', 'partial', 'missed_expired_ioc', 'missed_replace')"
                          " GROUP BY 1, 2 ORDER BY 1, 4 DESC")      # FILL lane 8: missed_replace joins the word
    # the first statement's ALL rollup and the per-book statement read as before
    assert st[0].endswith(lag + " FROM g GROUP BY ROLLUP (class) ORDER BY 3 DESC")
    assert "GROUP BY 1, 2, 3 ORDER BY 5 DESC LIMIT 60" in st[3]


def test_fills_missed_keeps_its_case_label_and_the_hourly_carries_the_six_statements():
    text = YML.read_text()
    names, labels, line = _labels_and_help(text)
    assert names == labels and names[-1] == "hourly"
    j = labels.index("fills-missed")
    assert labels[j - 1] == "close-rows" and labels[j + 1] == "on-target-why"
    hourly, _ = _preset(text, "hourly")
    sql, _ = _preset(text, "fills-missed")
    assert "SELECT '== fills-missed' AS section; " + sql.rstrip().rstrip(";") + ";" in hourly
    # FILL lane 9 (061): the seventh statement carries the chain once more -- five copies in the hourly
    assert hourly.count(STATE) == 5 and hourly.count("AS band_c, CASE WHEN m.resolved_prices") == 5
    # the comment block names the states and the decision read
    block = text[text.index("# THE MIRROR'S OWN FILLED-VS-MISSED"):text.index("fills-missed) SQL=")]
    for word in ("in_book", "before_open", "after_close", "no_book", "`decision`", "'unrecorded'", "$517,203.44"):
        assert word in block, word


# --------------------------------------------------------- the scratch database

# book 347's shape (book_347_1750 398: aec-wta-julpar-lucste, opened 00:15:33,
# closed 03:44:29) as THREE books on one condition, every one LONG on L3 so
# every BUY of L3 is adding on any of their axes:
#   book 31  opened -10 h, closed  -8 h
#   book 32  opened  -7 h, closed  -5 h
#   book 33  opened  -4 h, closed  -3 h
# his fills (BUY L3): 941 at -10 h 10 m (before the first window: before_open),
# 942 at -6 h (inside 32's window: in_book; a take row 941x filled whole 5 s
# later -> filled / take, band 1c), 943 at -5 h 30 m (inside 32: a rest filled
# whole -> filled / rest, band 2c), 944 at -7 h 30 m (between 31 and 32:
# no_book), 945 at -2 h (after the newest close, no live book: after_close).
# 941, 944, 945 are held by no plan and answered by no order: unseen.
FIXTURE_347 = """
INSERT INTO whales (id, address, username) VALUES (99, '0xrn1-l0', 'RN1');
INSERT INTO markets (condition_id, title, slug, event_title, sport, resolved_prices, resolved) VALUES
 ('c347', 'Pareja v Stefani', 'wta-pareja-stefani-2026-09-07', 'ev', 'tennis', '["1", "0"]'::jsonb, true);
INSERT INTO market_tokens (token_id, condition_id, outcome, outcome_index) VALUES ('L3', 'c347', 'p', 0), ('O3', 'c347', 's', 1);
INSERT INTO trades (id, whale_id, tx_hash, asset, condition_id, side, size, price, notional, market_slug, sport, ts, source, detected_at, dedupe_key) VALUES
 (941, 99, '0xf1', 'L3', 'c347', 'BUY', 100, 0.50, 50, 'wta-pareja-stefani-2026-09-07', 'tennis', now() - interval '10 hours' - interval '10 minutes', 'chain', now() - interval '10 hours' - interval '10 minutes' + interval '2 seconds', 'f1'),
 (942, 99, '0xf2', 'L3', 'c347', 'BUY', 200, 0.50, 100, 'wta-pareja-stefani-2026-09-07', 'tennis', now() - interval '6 hours', 'chain', now() - interval '6 hours' + interval '2 seconds', 'f2'),
 (943, 99, '0xf3', 'L3', 'c347', 'BUY', 300, 0.40, 120, 'wta-pareja-stefani-2026-09-07', 'tennis', now() - interval '5 hours' - interval '30 minutes', 'chain', now() - interval '5 hours' - interval '30 minutes' + interval '2 seconds', 'f3'),
 (944, 99, '0xf4', 'L3', 'c347', 'BUY', 400, 0.50, 200, 'wta-pareja-stefani-2026-09-07', 'tennis', now() - interval '7 hours' - interval '30 minutes', 'chain', now() - interval '7 hours' - interval '30 minutes' + interval '2 seconds', 'f4'),
 (945, 99, '0xf5', 'L3', 'c347', 'BUY', 500, 0.50, 250, 'wta-pareja-stefani-2026-09-07', 'tennis', now() - interval '2 hours', 'chain', now() - interval '2 hours' + interval '2 seconds', 'f5');
INSERT INTO mirror_books (id, whale, condition_id, us_market_slug, long_asset, other_asset, intent, ratio, state, target, target_raw, his_net, ledger_net, last_plan, peak_exposure_usd, avg_cost, settled_pnl, opened_at, closed_at, last_reason, flow_base) VALUES
 (31, 'rn1', 'c347', 'aec-wta-julpar-lucste-2026-09-07', 'L3', 'O3', 'ORDER_INTENT_BUY_LONG', 0.1, 'closed', 10, 10.0, 100, 0, '{}'::jsonb, 5.0, 0.50, 5.0, now() - interval '10 hours', now() - interval '8 hours', 'closed: standing row settled', 0),
 (32, 'rn1', 'c347', 'aec-wta-julpar-lucste-2026-09-07', 'L3', 'O3', 'ORDER_INTENT_BUY_LONG', 0.1, 'closed', 50, 50.0, 500, 0, '{}'::jsonb, 22.0, 0.44, 28.0, now() - interval '7 hours', now() - interval '5 hours', 'closed: standing row settled', 0),
 (33, 'rn1', 'c347', 'aec-wta-julpar-lucste-2026-09-07', 'L3', 'O3', 'ORDER_INTENT_BUY_LONG', 0.1, 'closed', 10, 10.0, 100, 0, '{}'::jsonb, 5.0, 0.50, 5.0, now() - interval '4 hours', now() - interval '3 hours', 'closed: standing row settled', 0);
INSERT INTO mirror_orders (id, book_id, whale, us_market_slug, kind, side, tif, his_level, price, wire, qty, state, filled, avg_px, bid_at_place, ask_at_place, placed_at, done_at, reason, decision) VALUES
 (9421, 32, 'rn1', 'aec-wta-julpar-lucste-2026-09-07', 'take', 'BUY_LONG', 'IOC', 0.50, 0.50, 0.50, 20, 'filled', 20, 0.50, 0.49, 0.51, now() - interval '6 hours' + interval '5 seconds', now() - interval '6 hours' + interval '6 seconds', 'take', 'take'),
 (9431, 32, 'rn1', 'aec-wta-julpar-lucste-2026-09-07', 'increase', 'BUY_LONG', 'GTC', 0.40, 0.40, 0.40, 30, 'filled', 30, 0.40, 0.40, 0.42, now() - interval '5 hours' - interval '30 minutes' + interval '5 seconds', now() - interval '5 hours' - interval '20 minutes', 'increase', 'rest');
"""


@pytest.fixture(scope="module")
def world():
    w = World(FIXTURE_347, "fills-missed lane 0b")
    try:
        yield w
    finally:
        w.close()


def test_fills_missed_on_book_347s_shape_splits_the_states_and_reads_the_decision(world):
    text = YML.read_text()
    sql, _ = _preset(text, "fills-missed")
    st = _stmts(sql)
    for s in st:
        world.rows(s)
    world.run(sql)
    by = {r["class"]: r for r in world.rows(st[0])}
    assert by["ALL"]["n"] == 5 and by["unseen"]["n"] == 3 and by["filled"]["n"] == 2
    assert _f(by["unseen"]["his_usd"]) == 500.0 and _f(by["filled"]["his_usd"]) == 220.0
    states = {(r["class"], r["state"]): r for r in world.rows(st[1])}
    assert set(states) == {("unseen", "before_open"), ("unseen", "no_book"), ("unseen", "after_close"),
                           ("filled", "in_book")}
    assert all(states[k]["n"] == 1 for k in states if k[0] == "unseen") and states[("filled", "in_book")]["n"] == 2
    assert _f(states[("unseen", "before_open")]["his_usd"]) == 50.0
    assert _f(states[("unseen", "no_book")]["his_usd"]) == 200.0
    assert _f(states[("unseen", "after_close")]["his_usd"]) == 250.0
    assert sum(r["n"] for r in states.values()) == by["ALL"]["n"], "the state rows sum to ALL"
    dec = {(r["class"], r["decision"]): r for r in world.rows(st[2])}
    assert set(dec) == {("filled", "take"), ("filled", "rest")}
    assert dec[("filled", "take")]["n"] == 1 and _f(dec[("filled", "take")]["band_med_c"]) == 1.0
    assert dec[("filled", "rest")]["n"] == 1 and _f(dec[("filled", "rest")]["band_med_c"]) == 2.0
    assert _f(dec[("filled", "take")]["roi"]) == 1.0 and dec[("filled", "take")]["n_resolved"] == 1
    # the per-book statement still keys the fill on the answering order's book; a fill no
    # plan held and no order answered has no book to key on (book NULL, as today)
    per = world.rows(st[3])
    assert {(r["book"], r["class"]): r["n"] for r in per} == {(32, "filled"): 2, (None, "unseen"): 3}


# ------------------------------------------------------------ FILL lane 8 (2026-09-09)

WHERE_WORD = "WHERE class IN ('filled', 'partial', 'missed_expired_ioc', 'missed_replace')"


def test_fills_missed_decision_block_carries_missed_replace_in_both_places_and_prints_the_rows_own_word():
    text = YML.read_text()
    st = _stmts(_preset(text, "fills-missed")[0])
    assert WHERE_WORD + " GROUP BY 1, 2 ORDER BY 1, 4 DESC" in st[2]
    assert "WHERE class IN ('filled', 'partial', 'missed_expired_ioc')" not in text, "the old three-word clause is gone everywhere"
    assert text.count(WHERE_WORD) == 2, "one word in TWO places: the standalone case and the hourly's copy"
    hourly, _ = _preset(text, "hourly")
    assert hourly.count(WHERE_WORD) == 1
    # the word printed is whatever the row carries: no list of decision words anywhere in the chain
    assert "COALESCE(decision, 'unrecorded') AS decision" in st[2] and "decision IN (" not in st[2]
    assert "'take_on_add'" not in st[2] and "'rest_held'" not in st[2]
    block = text[text.index("# THE MIRROR'S OWN FILLED-VS-MISSED"):text.index("fills-missed) SQL=")]
    for word in ("FILL lane 8", "missed_replace to that WHERE word", "hourly's copy", "745 fills / $381,750.75",
                 "replace_cent", "replace_unread", "COALESCE(decision, 'unrecorded')"):
        assert word in block, word


# book 816's shape (aec-wta-enakoi-nadpod, SHORT, target -1746; h2225 872-882): his
# adding fill on the short axis is a BUY of the OTHER token; 4689 answered the
# 22:14:12 fill 23 s later with 1,403 sh, cancelled `replace` after 11 s with 0
# filled (its cause word replace_qty) -> missed_replace | replace_qty; 4692
# answered a second fill with 1,403 sh, filled 631.85, cancelled `replace` after
# 140 s -> partial | replace_qty. The market is unresolved: no roi.
FIXTURE_816 = """
INSERT INTO whales (id, address, username) VALUES (99, '0xrn1-l0', 'RN1');
INSERT INTO markets (condition_id, title, slug, event_title, sport, resolved_prices, resolved) VALUES
 ('c816', 'Enakoi v Nadpod', 'wta-enakoi-nadpod-2026-09-08', 'ev', 'tennis', NULL, false);
INSERT INTO market_tokens (token_id, condition_id, outcome, outcome_index) VALUES ('L16', 'c816', 'e', 0), ('O16', 'c816', 'n', 1);
INSERT INTO trades (id, whale_id, tx_hash, asset, condition_id, side, size, price, notional, market_slug, sport, ts, source, detected_at, dedupe_key) VALUES
 (981, 99, '0xg1', 'O16', 'c816', 'BUY', 14030, 0.50, 7015, 'wta-enakoi-nadpod-2026-09-08', 'tennis', now() - interval '3 hours', 'chain', now() - interval '3 hours' + interval '2 seconds', 'g1'),
 (982, 99, '0xg2', 'O16', 'c816', 'BUY', 14030, 0.50, 7015, 'wta-enakoi-nadpod-2026-09-08', 'tennis', now() - interval '3 hours' + interval '600 seconds', 'chain', now() - interval '3 hours' + interval '602 seconds', 'g2');
INSERT INTO mirror_books (id, whale, condition_id, us_market_slug, long_asset, other_asset, intent, ratio, state, target, target_raw, his_net, ledger_net, last_plan, peak_exposure_usd, avg_cost, settled_pnl, opened_at, closed_at, last_reason, flow_base) VALUES
 (816, 'rn1', 'c816', 'aec-wta-enakoi-nadpod-2026-09-08', 'L16', 'O16', 'ORDER_INTENT_BUY_SHORT', 0.1, 'live', -1746, -1746.0, -17460, -631, '{}'::jsonb, 315.9, 0.50, NULL, now() - interval '4 hours', NULL, 'on target', 0);
INSERT INTO mirror_orders (id, book_id, whale, us_market_slug, kind, side, tif, his_level, price, wire, qty, state, filled, avg_px, bid_at_place, ask_at_place, placed_at, done_at, reason, decision) VALUES
 (4689, 816, 'rn1', 'aec-wta-enakoi-nadpod-2026-09-08', 'increase', 'SELL_LONG', 'GTC', 0.50, 0.50, 0.50, 1403, 'cancelled', 0, NULL, 0.49, 0.51, now() - interval '3 hours' + interval '23 seconds', now() - interval '3 hours' + interval '34 seconds', 'replace', 'replace_qty'),
 (4692, 816, 'rn1', 'aec-wta-enakoi-nadpod-2026-09-08', 'increase', 'SELL_LONG', 'GTC', 0.50, 0.50, 0.50, 1403, 'cancelled', 631.85, 0.50, 0.49, 0.51, now() - interval '3 hours' + interval '635 seconds', now() - interval '3 hours' + interval '775 seconds', 'replace', 'replace_qty');
"""


@pytest.fixture(scope="module")
def world_816():
    w = World(FIXTURE_816, "fills-missed lane 8")
    try:
        yield w
    finally:
        w.close()


def test_fills_missed_on_book_816s_shape_prints_the_missed_replace_cause_row(world_816):
    text = YML.read_text()
    sql, _ = _preset(text, "fills-missed")
    st = _stmts(sql)
    world_816.run(sql)
    by = {r["class"]: r for r in world_816.rows(st[0])}
    assert by["ALL"]["n"] == 2 and by["missed_replace"]["n"] == 1 and by["partial"]["n"] == 1
    assert _f(by["missed_replace"]["his_usd"]) == 7015.0 and by["missed_replace"]["n_resolved"] == 0
    dec = {(r["class"], r["decision"]): r for r in world_816.rows(st[2])}
    assert set(dec) == {("missed_replace", "replace_qty"), ("partial", "replace_qty")}, "the cause row prints (was absent)"
    assert dec[("missed_replace", "replace_qty")]["n"] == 1 and _f(dec[("missed_replace", "replace_qty")]["lag_med_s"]) == 23.0
    assert dec[("missed_replace", "replace_qty")]["roi"] is None, "unresolved: no roi, never a guess"
    assert _f(dec[("partial", "replace_qty")]["lag_med_s"]) == 35.0
    # the hourly's copy of the statement prints the same row (the whole hourly is not run here: take-band's
    # fill_pct divides by count(*) and a world with no long-book entry row raises division by zero -- lane 0b's
    # statement as it stands, pre-existing, not this lane's)
    hourly, _ = _preset(text, "hourly")
    copy = _stmts(hourly[hourly.index("SELECT '== fills-missed' AS section; ") + len("SELECT '== fills-missed' AS section; "):])[2]
    assert copy == st[2]
    dec2 = {(r["class"], r["decision"]): r["n"] for r in world_816.rows(copy)}
    assert dec2 == {("missed_replace", "replace_qty"): 1, ("partial", "replace_qty"): 1}
