"""The `fill-answers` render-ops preset and fills-missed's record read
(T2, FILL lane 4, 2026-09-08; migration 060, mirror_fill_answers). The
fills-missed preset at 17:37Z (hard2/hourly_1737.txt rows 1659-1660)
read 3,234 fills of his on our markets in 24 h, 1,105 / $517,203.44
`unseen`; $397,223.87 of that on live or closing books whose plan list
(20 entries, E9) had rolled the name off. Now fills-missed reads the
record FIRST through a CTE `fa` -- its order_id, then the plan's, then
the 120 s window; its name, then the plan's -- and `unseen` only when
neither the table nor the list holds the fill; the CTE reads the table
only when to_regclass finds it (xmltable over query_to_xml inside a CASE
that a plain FROM could not make conditional), so the hourly bundle --
nine presets in ONE psql -c under ON_ERROR_STOP -- survives the minutes
between a deploy and the boot that applies 060 instead of losing
take-band and on-target-why for the hour. The standalone `fill-answers`
preset (per market: fills held, written, names, the oldest written vs
the plan's oldest kept; then the totals) reads the table directly and
fails by name against a database without it: it IS the measurement.

The pins, on the exits-paired model: read-only, its own timeout, the
case label after closed-while-he-traded with hourly last, the help line
regenerated, the hourly still the nine presets (its own pins re-run);
fills-missed's fa CTE, the COALESCE order, the class words re-read on
all four chain statements; and, on the scratch database lane 0b's World
builds (skipped without one), book 285's shape (a closed book, the list
gone, the record naming order N: `filled` off N) and Oz/Denchev 544's
shape (a live book, the list rolled, the record naming
`open_order_pending`: `refused:open_order_pending` with state in_book,
never `unseen`), the fill-answers rows, then the table DROPPED:
fills-missed still runs (544's fill back to `unseen`) and fill-answers
fails by name.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests import test_render_ops_fills_missed as fm
from tests import test_render_ops_hourly as hourly
from tests.test_render_ops_fills_missed import World, _f

YML = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "render-ops.yml"
FA = ("WITH fa AS (SELECT x.fill_id, x.name, x.order_id, x.book_id FROM xmltable('/table/row' PASSING (CASE WHEN"
      " to_regclass('mirror_fill_answers') IS NULL THEN '<table/>'::xml ELSE query_to_xml('SELECT fill_id, name,"
      " order_id, book_id FROM mirror_fill_answers WHERE whale = ''rn1'' AND fill_ts >= extract(epoch FROM now() -"
      " interval ''25 hours'')', false, false, '') END) COLUMNS fill_id text PATH 'fill_id', name text PATH 'name',"
      " order_id bigint PATH 'order_id', book_id bigint PATH 'book_id') x), r AS (")
S_HEAD = ("s AS (SELECT f.*, fa.name AS fa_name, fa.order_id AS fa_order, fa.book_id AS fa_book, COALESCE(fa.order_id,"
          " CASE WHEN COALESCE(x.e->>'order', '') <> '' AND NOT ((x.e->>'order') ~ '[^0-9]') THEN (x.e->>'order')::bigint"
          " END) AS aorder, x.e, x.seen_book FROM f LEFT JOIN fa ON fa.fill_id = f.id::text LEFT JOIN LATERAL (")
O_WHERE = ("WHERE (s.aorder IS NOT NULL AND o.id = s.aorder) OR (s.aorder IS NULL AND (s.e->>'order') IS NULL"
           " AND b.condition_id = s.condition_id AND o.kind IN ('increase', 'take')"
           " AND o.placed_at >= s.ts AND o.placed_at < s.ts + interval '120 seconds')")
CLASS_TAIL = ("WHEN o.e IS NULL AND o.fa_name IS NULL THEN 'unseen' WHEN o.aorder IS NOT NULL OR (o.e->>'order') IS NOT NULL"
              " THEN 'order_row_unread' ELSE 'refused:' || COALESCE(o.fa_name, o.e->>'name', 'unnamed') END AS class")


def _preset(text: str, name: str) -> tuple[str, int]:
    m = re.search(r'^ {16}' + re.escape(name) + r'\) SQL="(.*?)"; TO=(\d+)', text, re.M | re.S)
    assert m, name
    return m.group(1), int(m.group(2))


def _stmts(sql: str) -> list[str]:
    return [s.strip() for s in sql.split("; ") if s.strip()]


# ------------------------------------------------------------------ the text

def test_fill_answers_is_two_read_only_statements_on_its_own_timeout_named_for_the_db_service():
    text = YML.read_text()
    block = text[text.index("# THE PER-FILL RECORD'S HEALTH LINE"):text.index("# THE HOURLY, IN ONE RUN")]
    assert "need_confirm" not in block and "$ARG" not in block and "HEAD=" not in block
    for bad in ("INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "ALTER"):
        assert bad not in block, bad
    assert "sportsassets-db" in block and "fails by name" in block and "060" in block
    sql, to = _preset(text, "fill-answers")
    assert to == 60000 and block.rstrip().endswith('"; TO=60000 ;;')
    st = _stmts(sql)
    assert len(st) == 2 and sql.count(";") == 2
    assert st[0].startswith("WITH w AS (SELECT condition_id, name, count(*) AS n, count(order_id) AS with_order,")
    assert st[0].endswith("ORDER BY 4 DESC, 1 DESC LIMIT 60") and st[0].count("LIMIT ") == 1 and "LIMIT" not in st[1]
    assert st[1].startswith("SELECT count(*) AS rows_24h, count(DISTINCT condition_id) AS markets, count(order_id) AS with_order,")
    assert st[1].endswith("FROM mirror_fill_answers WHERE whale = 'rn1' AND at >= extract(epoch FROM now() - interval '24 hours');")
    assert "to_regclass" not in sql, "the health line reads the table itself: absent, it fails by name"


def test_fill_answers_reads_the_table_his_collapsed_fills_and_the_plans_list():
    text = YML.read_text()
    sql, _ = _preset(text, "fill-answers")
    fm_sql, _ = _preset(text, "fills-missed")
    # the table's rows of 24 h by (market, name); the fills held read by fills-missed's OWN collapse
    assert ("FROM mirror_fill_answers WHERE whale = 'rn1' AND at >= extract(epoch FROM now() - interval '24 hours')"
            " GROUP BY 1, 2)") in sql
    collapse = ("COALESCE(t.source, '') IN ('chain', 's1') AS net_leg, bool_or(COALESCE(t.source, '') IN ('chain', 's1'))"
                " OVER (PARTITION BY t.whale_id, COALESCE(lower(NULLIF(t.tx_hash, '')), 'row:' || t.id::text), t.asset,"
                " upper(t.side)) AS has_net")
    assert collapse in sql and collapse in fm_sql
    assert "WHERE (d.net_leg OR NOT d.has_net) GROUP BY 1)" in sql
    assert "t.condition_id IN (SELECT condition_id FROM mirror_books WHERE whale = 'rn1')" in sql
    # the newest book per market: the plan's oldest kept entry and its fills_hwm, both cast only when numeric
    assert "SELECT DISTINCT ON (condition_id) condition_id, id AS book, left(us_market_slug, 36) AS slug, state," in sql
    assert ("(SELECT min((e->>'ts')::float8) FROM jsonb_array_elements(CASE WHEN jsonb_typeof(last_plan->'his_fills_seen')"
            " = 'array' THEN last_plan->'his_fills_seen' ELSE '[]'::jsonb END) e WHERE NOT ((e->>'ts') ~ '[^0-9.]'))"
            " AS plan_oldest_kept") in sql
    assert ("CASE WHEN NOT ((last_plan->>'fills_hwm') ~ '[^0-9.]') THEN (last_plan->>'fills_hwm')::float8 END AS fills_hwm"
            " FROM mirror_books WHERE whale = 'rn1' ORDER BY condition_id, id DESC)") in sql
    # FILL lane 9 (migration 061): `causes` (cause=n, left 60) beside `names`, read through the guarded CTE `c`
    assert ("SELECT b.book, b.slug, b.state, COALESCE(h.held, 0) AS held_24h, COALESCE(a.written, 0) AS written,"
            " COALESCE(a.with_order, 0) AS with_order, left(a.names, 120) AS names, left(c.causes, 60) AS causes,"
            " to_timestamp(a.oldest_written)::time(0) AS oldest_written,"
            " to_timestamp(b.plan_oldest_kept)::time(0) AS plan_oldest_kept, to_timestamp(b.fills_hwm)::time(0) AS fills_hwm") in sql
    assert " LEFT JOIN c ON c.condition_id = b.condition_id WHERE COALESCE(h.held, 0) > 0" in sql
    assert "string_agg(name || '=' || n, ' ' ORDER BY n DESC, name) AS names" in sql


def test_fill_answers_sits_after_closed_while_he_traded_before_hourly_and_stays_out_of_it():
    text = YML.read_text()
    line = next(ln for ln in text.splitlines() if ln.lstrip().startswith('*) echo "sql: arg must be one of'))
    names = line.split("one of ", 1)[1].split(" (got", 1)[0].split("|")
    labels = re.findall(r"^ {16}([a-z0-9-]+)\) ", text[text.index('case "$ARG" in'):text.index(line)], re.M)
    assert names == labels and len(names) == len(set(names))
    assert names[-1] == "hourly" and names[-2] == "fill-answers" and names[-3] == "closed-while-he-traded"
    assert "|exits-band|closed-while-he-traded|fill-answers|hourly (got" in line
    h, _ = _preset(text, "hourly")
    assert "'== fill-answers'" not in h and "rows_24h" not in h and "plan_oldest_kept" not in h
    # the hourly is the ten presets joined (nine until FILL lane 14 put tick-ring after
    # mirror-tick; fill-answers itself still rides no hourly) -- its own pins, re-run here
    assert hourly.PARTS == ("mirror-tick", "tick-ring", "mirror-pnl", "paired-day", "paired-ratio", "latency-census",
                            "fills-answered", "fills-missed", "take-band", "on-target-why")
    hourly.test_the_hourly_preset_is_the_nine_presets_sql_joined_under_section_markers()
    hourly.test_the_hourly_preset_is_read_only_with_its_own_output_cap_and_timeout()
    hourly.test_the_help_line_is_the_case_labels_with_hourly_last()
    # the record read rides the hourly's copy of fills-missed, guarded, on all four chain statements -- and
    # (FILL lane 9, 061) on the seventh, the cause block, whose own `fc` CTE and latency-census's `fr` CTE
    # (keep_to_replace by rest_id) test to_regclass too: 5 chains, 7 to_regclass reads, 5 column-existence tests
    assert h.count(FA) == 5 and h.count("to_regclass('mirror_fill_answers')") == 7
    assert h.count("information_schema.columns") == 5


def test_fills_missed_reads_the_record_first_through_a_guarded_cte_on_every_chain_statement():
    text = YML.read_text()
    sql, to = _preset(text, "fills-missed")
    assert to == 120000
    st = _stmts(sql)
    # FILL lane 9 (061): seven statements -- the seventh (the cause block) carries the chain's `fa` and its own
    # guarded `fc`; the band table and the fill rate by hour (st[4], st[5]) still name the table nowhere
    assert len(st) == 7 and all(s.startswith(FA) for s in st[:4]) and not any("mirror_fill_answers" in s for s in st[4:6])
    assert st[6].startswith(FA) and st[6].count("to_regclass('mirror_fill_answers')") == 2
    assert ("column_name = 'cause') THEN '<table/>'::xml ELSE query_to_xml('SELECT fill_id, cause, fast FROM mirror_fill_answers"
            " WHERE whale = ''rn1'' AND fill_ts >= extract(epoch FROM now() - interval ''25 hours'')', false, false, '') END)"
            " COLUMNS fill_id text PATH 'fill_id', cause text PATH 'cause', fast boolean PATH 'fast') x)") in st[6]
    chain = st[0]
    # fa joined before the his_fills_seen LATERAL; `aorder` = the record's order, else the plan's readable one
    assert S_HEAD in chain and chain.index("LEFT JOIN fa ON fa.fill_id = f.id::text") < chain.index("jsonb_array_elements(")
    # `o` takes aorder first; the 120 s window only when neither the record nor the plan names an order
    assert O_WHERE in chain and "o.id = (s.e->>'order')::bigint" not in chain
    # the class: the record's name first; `unseen` only when neither holds the fill; the book off the record too
    assert CLASS_TAIL in chain and "COALESCE(o.obook, o.fa_book, o.seen_book) AS book" in chain
    assert "'refused:' || COALESCE(o.e->>'name', 'unnamed')" not in chain and "WHEN o.e IS NULL THEN 'unseen'" not in chain
    # lane 0b's blocks untouched: the state CASE and its LATERALs, ostate, the (class, decision) statement
    assert fm.STATE in chain and fm.BW in chain and "ord.state AS ostate" in chain and chain.count("ord.state") == 1
    assert st[2].endswith(" FROM g WHERE class IN ('filled', 'partial', 'missed_expired_ioc', 'missed_replace')"
                          " GROUP BY 1, 2 ORDER BY 1, 4 DESC")      # FILL lane 8: missed_replace joins the word
    # the guard: the table is named only inside the CASE's ELSE arm, never in a bare FROM
    literal = ("query_to_xml('SELECT fill_id, name, order_id, book_id FROM mirror_fill_answers WHERE whale = ''rn1''"
               " AND fill_ts >= extract(epoch FROM now() - interval ''25 hours'')', false, false, '')")
    assert chain.count("mirror_fill_answers") == 2 and literal in chain
    bare = chain.replace(literal, "").replace("to_regclass('mirror_fill_answers')", "")
    assert "mirror_fill_answers" not in bare, "the table is never a bare FROM / JOIN: the parser must not resolve it"
    assert chain.index("CASE WHEN to_regclass('mirror_fill_answers') IS NULL THEN '<table/>'::xml ELSE " + literal + " END") > 0
    # the four chain statements carry ONE chain (lane 0b's pin) -- re-run here
    fm.test_fills_missed_has_six_statements_on_one_chain_read_only_on_its_own_timeout()
    fm.test_fills_missed_reads_the_state_off_the_book_windows_and_the_decision_off_the_answering_row()
    # the comment names the read
    block = text[text.index("# THE MIRROR'S OWN FILLED-VS-MISSED"):text.index("fills-missed) SQL=")]
    for word in ("FILL lane 4", "mirror_fill_answers", "to_regclass", "the hourly bundle survives"):
        assert word in block, word


def test_the_fa_cte_and_the_fill_answers_preset_parse_as_postgres_sql():
    pglast = pytest.importorskip("pglast")
    text = YML.read_text()
    for name in ("fills-missed", "fill-answers", "hourly"):
        sql, _ = _preset(text, name)
        pglast.parse_sql(sql)


# --------------------------------------------------------- the scratch database

# book 285's shape (post_exits_1707 row 334: our order 23 s after his fill,
# the book closed) as book 85 on c285: his BUY 9851 of L285 at -3 h inside
# the window (opened -4 h, closed -2 h 30 m), the list GONE with the close's
# plan, the record naming `rest_placed` with order 98510 (filled whole 23 s
# later). Oz/Denchev 544's shape (verify_1750 row 564; hourly_1737 row 1691:
# 8 fills `unseen`) as book 86 on c544, live, the list rolled to []: his BUY
# 9852 at -2 h named `open_order_pending` on the record with no order; his
# BUY 9853 at -90 m held by nothing -- no record, no list, no order in its
# window: `unseen`, state in_book.
FIXTURE_L4 = """
INSERT INTO whales (id, address, username) VALUES (99, '0xrn1-l4', 'RN1');
INSERT INTO markets (condition_id, title, slug, event_title, sport, resolved_prices, resolved) VALUES
 ('c285', 'Elche v Real Sociedad total', 'lal-elc-rso-2026-09-07-total-2', 'ev', 'soccer', '["1", "0"]'::jsonb, true),
 ('c544', 'Oz v Denchev', 'wta-oz-denchev-2026-09-08', 'ev', 'tennis', NULL, false);
INSERT INTO market_tokens (token_id, condition_id, outcome, outcome_index) VALUES
 ('L285', 'c285', 'over', 0), ('O285', 'c285', 'under', 1), ('L544', 'c544', 'oz', 0), ('O544', 'c544', 'denchev', 1);
INSERT INTO trades (id, whale_id, tx_hash, asset, condition_id, side, size, price, notional, market_slug, sport, ts, source, detected_at, dedupe_key) VALUES
 (9851, 99, '0xl4a1', 'L285', 'c285', 'BUY', 50, 0.84, 42, 'lal-elc-rso-2026-09-07-total-2', 'soccer', now() - interval '3 hours', 'chain', now() - interval '3 hours' + interval '2 seconds', 'l4a1'),
 (9852, 99, '0xl4a2', 'L544', 'c544', 'BUY', 2000, 0.52, 1040, 'wta-oz-denchev-2026-09-08', 'tennis', now() - interval '2 hours', 'chain', now() - interval '2 hours' + interval '2 seconds', 'l4a2'),
 (9853, 99, '0xl4a3', 'L544', 'c544', 'BUY', 700, 0.52, 364, 'wta-oz-denchev-2026-09-08', 'tennis', now() - interval '90 minutes', 'chain', now() - interval '90 minutes' + interval '2 seconds', 'l4a3');
INSERT INTO mirror_books (id, whale, condition_id, us_market_slug, long_asset, other_asset, intent, ratio, state, target, target_raw, his_net, ledger_net, last_plan, peak_exposure_usd, avg_cost, settled_pnl, opened_at, closed_at, last_reason, flow_base) VALUES
 (85, 'rn1', 'c285', 'lal-elc-rso-2026-09-07-total-2', 'L285', 'O285', 'ORDER_INTENT_BUY_LONG', 0.1, 'closed', 5, 5.0, 50, 0, '{"kind": "closed"}'::jsonb, 4.2, 0.84, -1.0, now() - interval '4 hours', now() - interval '2 hours' - interval '30 minutes', 'closed: standing row settled', 0),
 (86, 'rn1', 'c544', 'aec-wta-ipeoz-rosden-2026-09-08', 'L544', 'O544', 'ORDER_INTENT_BUY_LONG', 0.1, 'live', 270, 270.0, 2700, 200, '{"his_fills_seen": [], "fills_hwm": 1757000000.5}'::jsonb, 104.0, 0.52, NULL, now() - interval '3 hours', NULL, 'open_order_pending', 0);
INSERT INTO mirror_orders (id, book_id, whale, us_market_slug, kind, side, tif, his_level, price, wire, qty, state, filled, avg_px, bid_at_place, ask_at_place, placed_at, done_at, reason, decision) VALUES
 (98510, 85, 'rn1', 'lal-elc-rso-2026-09-07-total-2', 'increase', 'BUY_LONG', 'GTC', 0.84, 0.84, 0.84, 5, 'filled', 5, 0.84, 0.83, 0.85, now() - interval '3 hours' + interval '23 seconds', now() - interval '3 hours' + interval '60 seconds', 'increase', 'rest');
INSERT INTO mirror_fill_answers (whale, condition_id, fill_id, fill_ts, detected_at, at, book_id, order_id, name, tick) VALUES
 ('rn1', 'c285', '9851', extract(epoch FROM now() - interval '3 hours'), extract(epoch FROM now() - interval '3 hours') + 2, extract(epoch FROM now() - interval '3 hours') + 23, 85, 98510, 'rest_placed', 1201),
 ('rn1', 'c544', '9852', extract(epoch FROM now() - interval '2 hours'), extract(epoch FROM now() - interval '2 hours') + 2, extract(epoch FROM now() - interval '2 hours') + 20, 86, NULL, 'open_order_pending', 1440);
"""


@pytest.fixture(scope="module")
def world():
    w = World(FIXTURE_L4, "fill-answers lane 4")
    try:
        yield w
    finally:
        w.close()


def test_fills_missed_keys_285s_fill_off_the_records_order_and_544s_off_its_name_never_unseen(world):
    sql, _ = _preset(YML.read_text(), "fills-missed")
    st = _stmts(sql)
    for s in st:
        world.rows(s)
    world.run(sql)
    by = {r["class"]: r for r in world.rows(st[0])}
    assert by["ALL"]["n"] == 3 and by["filled"]["n"] == 1 and by["refused:open_order_pending"]["n"] == 1 and by["unseen"]["n"] == 1
    assert _f(by["filled"]["his_usd"]) == 42.0 and _f(by["refused:open_order_pending"]["his_usd"]) == 1040.0
    assert _f(by["unseen"]["his_usd"]) == 364.0
    states = {(r["class"], r["state"]): r["n"] for r in world.rows(st[1])}
    assert states == {("filled", "in_book"): 1, ("refused:open_order_pending", "in_book"): 1, ("unseen", "in_book"): 1}
    assert sum(states.values()) == by["ALL"]["n"]
    dec = {(r["class"], r["decision"]): r["n"] for r in world.rows(st[2])}
    assert dec == {("filled", "rest"): 1}
    per = {(r["book"], r["class"]): r["n"] for r in world.rows(st[3])}
    assert per == {(85, "filled"): 1, (86, "refused:open_order_pending"): 1, (None, "unseen"): 1}, \
        "the record's book keys the closed book's fill; the fill nothing holds has no book, as today"


def test_fill_answers_reads_the_two_markets_and_the_totals(world):
    sql, _ = _preset(YML.read_text(), "fill-answers")
    st = _stmts(sql)
    rows = world.rows(st[0])
    assert [(r["book"], r["state"], r["held_24h"], r["written"], r["with_order"], r["names"]) for r in rows] == [
        (86, "live", 2, 1, 0, "open_order_pending=1"), (85, "closed", 1, 1, 1, "rest_placed=1")]
    assert all(r["oldest_written"] is not None and r["plan_oldest_kept"] is None for r in rows)
    assert [r["fills_hwm"] is not None for r in rows] == [True, False]
    tot = world.rows(st[1])[0]
    assert (tot["rows_24h"], tot["markets"], tot["with_order"], tot["names"]) == (2, 2, 1, 2)
    assert tot["first_at"] is not None and tot["last_at"] is not None
    world.run(sql)


def test_with_the_table_dropped_fills_missed_still_runs_and_fill_answers_fails_by_name(world):
    """Runs last in this module: the table gone (the minutes before 060
    lands on a fresh database), fills-missed's guarded CTE reads nothing
    -- 544's fill is `unseen` again, 285's is still `filled` off the 120 s
    window -- and the hourly's copy runs; fill-answers raises the driver's
    UndefinedTableError: the measurement says so by name."""
    text = YML.read_text()
    world.run("DROP TABLE mirror_fill_answers")
    sql, _ = _preset(text, "fills-missed")
    st = _stmts(sql)
    by = {r["class"]: r for r in world.rows(st[0])}
    assert by["ALL"]["n"] == 3 and by["filled"]["n"] == 1 and by["unseen"]["n"] == 2
    assert "refused:open_order_pending" not in by
    per = {(r["book"], r["class"]): r["n"] for r in world.rows(st[3])}
    assert per == {(85, "filled"): 1, (None, "unseen"): 2}
    world.run(sql)
    world.run(_preset(text, "hourly")[0])
    fa_sql, _ = _preset(text, "fill-answers")
    with pytest.raises(Exception) as ei:
        world.rows(_stmts(fa_sql)[0])
    assert type(ei.value).__name__ == "UndefinedTableError"
