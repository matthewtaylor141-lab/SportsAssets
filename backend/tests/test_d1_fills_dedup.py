"""D1 (2026-09-06): his fills are counted twice across the chain and poll
paths; the mirror reads the venue-exact position.

Book 16, aec-wta-markos-linnos-2026-09-06 (his wta-kostyuk-noskova-
2026-09-06): the chain path writes ONE row per tx per token (the
wallet's net 1155 legs, the whole taker order at its average price),
the Data-API path writes ONE ROW PER MAKER MATCH, and the ingest dedupe
key (tx, asset, side, size, price, ts) never collapses them when a
taker order matched more than one maker. `mirror_shadow.his_fills` now
returns ONE reading per (tx_hash, asset, side): the net-leg row (chain /
s1) when the key holds one, else the per-match rows -- and the
collapsed reading is the exit worker's snapshot of his wallet, to the
share (55,993.4 / 29,555.0).

The collapse is SQL, so the reconciliation tests below EXECUTE it
against a scratch Postgres built here (the shape tests/
test_s1_sql_real_pg.py set: skips visibly when no local Postgres
answers -- text is not proof). The pure tests (the parse, the
plumbing, the drift arithmetic, the live worker's terminal memo) run
everywhere.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import uuid

import pytest

from sportsassets.analytics import mirror as mi
from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.api import app as api_app
from sportsassets.workers import mirror_live as ml
from sportsassets.workers import mirror_shadow as ms
from tests.test_mirror_live_worker import CID, M, N, NOW, SLUG, _armed  # noqa: F401 — the fixture
from tests.test_mirror_live_worker import _census, _pool, _tick, _Venue
from tests.test_mirror_live_worker import _Pool as _LivePool
from tests.test_mirror_shadow import HIS, _fill, _nosleep, _Pmus
from tests.test_mirror_shadow import _Pool as _ShadowPool

DSN_BASE = os.environ.get(
    "MIRROR_SQL_PIN_DSN",
    os.environ.get("S1_SQL_PIN_DSN",
                   "postgresql://sportsassets:sportsassets@localhost:5432/postgres"))

# the columns his_fills reads, as migrations/001_init.sql (+003) shape them
DDL = """
CREATE TABLE whales (id bigserial PRIMARY KEY, address text, username text,
    active boolean DEFAULT true, banned boolean DEFAULT false);
CREATE TABLE markets (condition_id text PRIMARY KEY, title text, slug text,
    event_slug text, event_title text, sport text NOT NULL DEFAULT 'unclassified');
CREATE TABLE market_tokens (token_id text PRIMARY KEY, condition_id text,
    outcome text, outcome_index integer);
CREATE TABLE trades (id bigserial PRIMARY KEY, whale_id bigint NOT NULL, tx_hash text NOT NULL,
    asset text NOT NULL, condition_id text, side text NOT NULL, outcome text, outcome_index integer,
    size numeric(24, 6) NOT NULL, price numeric(10, 6) NOT NULL, notional numeric(24, 6),
    market_title text, market_slug text, event_slug text,
    sport text NOT NULL DEFAULT 'unclassified', ts timestamptz NOT NULL, source text NOT NULL,
    detected_at timestamptz);
"""
# `detected_at` (E9, 2026-09-07): his_fills now selects the ingest's clock
# beside the fill's own stamp (the plan's his_fills_seen); the real table
# has carried it since migration 001. Nullable here: no D1 row sets it and
# the collapse rule never reads it.

D1_CID = "0xd1-wta-kostyuk-noskova-2026-09-06"
K, NS = "tok-kostyuk", "tok-noskova"          # his long (Kostyuk) and other (Noskova) tokens
T0 = 1_788_000_000                             # an epoch inside 2026-09-06


def _run(coro):
    return asyncio.run(coro)


async def _scratch():
    asyncpg = pytest.importorskip("asyncpg")
    try:
        admin = await asyncpg.connect(DSN_BASE, timeout=4)
    except Exception:  # noqa: BLE001 — no local PG: skip, never fake
        pytest.skip("no local postgres for the D1 real-SQL reconciliation")
    name = "d1_fills_" + uuid.uuid4().hex[:10]
    await admin.execute(f'CREATE DATABASE "{name}"')
    conn = await asyncpg.connect(DSN_BASE.rsplit("/", 1)[0] + "/" + name, timeout=4)
    for stmt in DDL.split(";"):
        if stmt.strip():
            await conn.execute(stmt)
    await conn.execute("INSERT INTO whales (id, address, username) VALUES (1, '0xrn1', 'RN1')")
    await conn.execute(
        "INSERT INTO markets (condition_id, title, slug, event_title, sport) VALUES ($1, $2, $3, $4, $5)",
        D1_CID, "WTA: Marta Kostyuk vs Linda Noskova", "wta-kostyuk-noskova-2026-09-06",
        "US Open 2026", "tennis")
    await conn.execute(
        "INSERT INTO market_tokens (token_id, condition_id, outcome, outcome_index) "
        "VALUES ($1, $2, 'Marta Kostyuk', 0), ($3, $2, 'Linda Noskova', 1)", K, D1_CID, NS)
    return admin, conn, name


async def _drop(admin, conn, name):
    await conn.close()
    await admin.execute(f'DROP DATABASE "{name}"')
    await admin.close()


async def _insert(conn, rows, cid=D1_CID):
    """rows: (source, tx, asset, side, size, price, ts_offset_s)."""
    for i, (src, tx, asset, side, size, price, dt) in enumerate(rows):
        await conn.execute(
            "INSERT INTO trades (whale_id, tx_hash, asset, condition_id, side, size, price, "
            "notional, ts, source) VALUES (1, $1, $2, $3, $4, $5, $6, $7, to_timestamp($8), $9)",
            tx, asset, cid, side, size, price, round(size * price, 6), T0 + dt + i * 0.001, src)


def _d1_rows():
    """EXACTLY the production shape read at 19:13Z (D1 brief): the four
    shared txs row for row, the remaining chain rows summing to the
    per-source totals, and the two poll-only txs the chain path
    missed. Totals by (source, outcome): chain/Noskova 50 rows 29,555.0;
    chain/Kostyuk 37 rows 49,483.5; poll/Noskova 2 rows 10,224.4;
    poll/Kostyuk 10 rows 42,661.7."""
    rows = [
        # tx 0x5446ded9b2e157ec Kostyuk BUY: chain 15164.0@0.563 | poll 4996@0.560, 5172@0.560, 4996@0.570
        ("chain", "0x5446ded9b2e157ec", K, "BUY", 15164.0, 0.563, 8196),
        ("poll", "0x5446ded9b2e157ec", K, "BUY", 4996.0, 0.560, 8196),
        ("poll", "0x5446ded9b2e157ec", K, "BUY", 5172.0, 0.560, 8196),
        ("poll", "0x5446ded9b2e157ec", K, "BUY", 4996.0, 0.570, 8196),
        # tx 0x9cf14ff1b7711eb6 Kostyuk BUY: chain 14777.0@0.453 | poll 4809@0.450, 5159@0.450, 4809@0.460
        ("chain", "0x9cf14ff1b7711eb6", K, "BUY", 14777.0, 0.453, 723),
        ("poll", "0x9cf14ff1b7711eb6", K, "BUY", 4809.0, 0.450, 723),
        ("poll", "0x9cf14ff1b7711eb6", K, "BUY", 5159.0, 0.450, 723),
        ("poll", "0x9cf14ff1b7711eb6", K, "BUY", 4809.0, 0.460, 723),
        # tx 0xaa52fe6c5a12901d Noskova BUY: chain 10224.4@0.835 | poll 5031.4@0.830, 5193.0@0.840
        ("chain", "0xaa52fe6c5a12901d", NS, "BUY", 10224.4, 0.835, 5341),
        ("poll", "0xaa52fe6c5a12901d", NS, "BUY", 5031.4, 0.830, 5341),
        ("poll", "0xaa52fe6c5a12901d", NS, "BUY", 5193.0, 0.840, 5341),
        # tx 0xf66028301c277143 Kostyuk BUY: chain 6210.8@0.538 | poll 1214.8@0.530, 4996@0.540
        ("chain", "0xf66028301c277143", K, "BUY", 6210.8, 0.538, 8214),
        ("poll", "0xf66028301c277143", K, "BUY", 1214.8, 0.530, 8214),
        ("poll", "0xf66028301c277143", K, "BUY", 4996.0, 0.540, 8214),
        # poll-only txs (the chain path missed them): 6,509.9 Kostyuk
        ("poll", "0xpollonly000000a1", K, "BUY", 3254.95, 0.50, 3000),
        ("poll", "0xpollonly000000a2", K, "BUY", 3254.95, 0.50, 3100),
    ]
    # the remaining chain rows: 34 Kostyuk summing 13,331.7 and 49
    # Noskova summing 19,330.6, each its own tx
    for i in range(33):
        rows.append(("chain", f"0xchainK{i:04d}", K, "BUY", 392.0, 0.50, 100 + i))
    rows.append(("chain", "0xchainK0033", K, "BUY", 395.7, 0.50, 200))
    for i in range(48):
        rows.append(("chain", f"0xchainN{i:04d}", NS, "BUY", 394.0, 0.80, 300 + i))
    rows.append(("chain", "0xchainN0048", NS, "BUY", 418.6, 0.80, 400))
    return rows


def _by(rows, src, asset):
    sel = [r for r in rows if r[0] == src and r[2] == asset]
    return len(sel), round(sum(r[4] for r in sel), 4)


def test_the_fixture_is_the_production_table_by_source_and_outcome():
    rows = _d1_rows()
    assert _by(rows, "chain", NS) == (50, 29555.0)
    assert _by(rows, "chain", K) == (37, 49483.5)
    assert _by(rows, "poll", NS) == (2, 10224.4)
    assert _by(rows, "poll", K) == (10, 42661.7)
    # the poll legs of each shared tx sum EXACTLY to its chain row
    for tx, chain_size in (("0x5446ded9b2e157ec", 15164.0), ("0x9cf14ff1b7711eb6", 14777.0),
                           ("0xaa52fe6c5a12901d", 10224.4), ("0xf66028301c277143", 6210.8)):
        assert round(sum(r[4] for r in rows if r[1] == tx and r[0] == "poll"), 4) == chain_size


# ------------------------------------------------ 1. the reconciliation

def test_the_collapsed_reading_is_the_snapshot_to_the_share():
    """55,993.4 long / 29,555.0 other (+-1 share): the exit worker's
    snapshot of his wallet at 19:10Z-19:13Z, fills_since 0. Ten poll
    rows and 46,376.2 shares collapsed; the raw table read 92,145 /
    39,779."""
    async def run():
        admin, c, name = await _scratch()
        try:
            await _insert(c, _d1_rows())
            fills = await ms.his_fills(c, "rn1", D1_CID)
            pos = mi.net_positions(fills)
            assert abs(pos[K] - 55993.4) <= 1.0 and abs(pos[NS] - 29555.0) <= 1.0, pos
            assert ms.his_fills_dedup() == {"dup_rows": 10, "dup_shares": 46376.2}
            # the four shared txs come back as their chain row alone
            for tx in ("0x5446ded9b2e157ec", "0x9cf14ff1b7711eb6", "0xaa52fe6c5a12901d",
                       "0xf66028301c277143"):
                got = [f for f in fills if f["tx_hash"] == tx]
                assert len(got) == 1 and got[0]["source"] == "chain", (tx, got)
            assert len(fills) == 37 + 50 + 2
            assert [f["ts"] for f in fills] == sorted(f["ts"] for f in fills), "ts, id order kept"
            # the mapper's context still rides on every row
            assert fills[0]["outcome"] in ("Marta Kostyuk", "Linda Noskova")
            assert fills[0]["event_title"] == "US Open 2026" and fills[0]["sport"] == "tennis"
            assert not any(k in fills[0] for k in ("dup_rows", "dup_shares", "has_net_leg", "collapsed"))
            # the raw table is what the mirror used to read
            raw = await c.fetch("SELECT asset, sum(size)::float8 AS s FROM trades GROUP BY asset")
            raw = {r["asset"]: r["s"] for r in raw}
            assert abs(raw[K] - 92145.2) < 0.01 and abs(raw[NS] - 39779.4) < 0.01
        finally:
            await _drop(admin, c, name)
    _run(run())


def test_a_poll_only_tx_is_kept_whole_and_two_poll_legs_without_a_chain_row_are_both_kept():
    async def run():
        admin, c, name = await _scratch()
        try:
            await _insert(c, [
                ("poll", "0xonlypoll", K, "BUY", 3254.95, 0.50, 10),           # one leg, no chain row
                ("poll", "0xtwolegs", K, "BUY", 1214.8, 0.53, 20),             # two legs, no chain row
                ("poll", "0xtwolegs", K, "BUY", 4996.0, 0.54, 20),
                ("backfill", "0xbackfill", NS, "BUY", 100.0, 0.80, 30),        # the backfill shape too
            ])
            fills = await ms.his_fills(c, "rn1", D1_CID)
            assert len(fills) == 4 and ms.his_fills_dedup() == {"dup_rows": 0, "dup_shares": 0.0}
            pos = mi.net_positions(fills)
            assert abs(pos[K] - (3254.95 + 1214.8 + 4996.0)) < 1e-6 and pos[NS] == 100.0
        finally:
            await _drop(admin, c, name)
    _run(run())


def test_a_chain_row_wins_even_when_the_poll_legs_do_not_sum_to_it():
    """The chain row is the wallet's net legs -- the truth; a poll leg
    the venue re-delivered at another size still collapses, and is
    counted.

    E19b (2026-09-08): BACK TO 7a4b852's PIN. Lane 8 (E19 part (b))
    re-pinned this to "a distinct fill and counts" on Martinez's rows;
    the fills-vs-venue preset's first production run (17:07Z) showed
    that key over-reading the venue on the day's live books (old closer
    22 markets, new closer 4; book 622: chain 3 rows / 15,000 = the
    venue's 15,000, plus ONE poll row of 5,000 the new key counted on
    top), so this -- D1's key -- is the reader that ships. Lane 8's
    reading is kept below on the UNWIRED his_fills_distinct."""
    async def run():
        admin, c, name = await _scratch()
        try:
            await _insert(c, [
                ("chain", "0xmismatch", K, "BUY", 15164.0, 0.563, 10),
                ("poll", "0xmismatch", K, "BUY", 4996.0, 0.560, 10),            # 4,996 != 15,164
                # the SAME tx, the other side / the other token: different keys, kept
                ("poll", "0xmismatch", K, "SELL", 50.0, 0.60, 11),
                ("poll", "0xmismatch", NS, "BUY", 70.0, 0.40, 12),
                # tx hash case never splits a key
                ("chain", "0xABCDEF", NS, "BUY", 200.0, 0.80, 20),
                ("poll", "0xabcdef", NS, "BUY", 200.0, 0.80, 20),
            ])
            fills = await ms.his_fills(c, "rn1", D1_CID)
            assert [(f["source"], f["asset"], f["side"], f["size"]) for f in fills] == [
                ("chain", K, "BUY", 15164.0), ("poll", K, "SELL", 50.0), ("poll", NS, "BUY", 70.0),
                ("chain", NS, "BUY", 200.0)]
            assert ms.his_fills_dedup() == {"dup_rows": 2, "dup_shares": 5196.0}
            pos = mi.net_positions(fills)
            assert pos[K] == 15114.0 and pos[NS] == 270.0
        finally:
            await _drop(admin, c, name)
    _run(run())


def test_e19b_reference_a_poll_leg_that_neither_sums_nor_repeats_is_distinct_on_the_unwired_key():
    """Lane 8's re-pin of the test above, kept as a pin of the WITHDRAWN
    behaviour on the unwired reference (his_fills_distinct): Martinez's
    rows (hard2/book_534_1424.log) carried s1 5,225 @0.61 beside poll
    4,283.5 @0.60 under one tx at 12:09:45Z and the venue's own
    per-market snapshot summed EVERY row (29,054.9), so under that key a
    per-match row that neither sums to the net-leg row nor repeats it is
    a distinct fill and counts; a repeat (0xABCDEF) still collapses. The
    reader's counter (his_fills_dedup) is never moved by the call."""
    async def run():
        admin, c, name = await _scratch()
        try:
            await _insert(c, [
                ("chain", "0xmismatch", K, "BUY", 15164.0, 0.563, 10),
                ("poll", "0xmismatch", K, "BUY", 4996.0, 0.560, 10),            # 4,996 != 15,164: a second fill (the reference)
                ("poll", "0xmismatch", K, "SELL", 50.0, 0.60, 11),
                ("poll", "0xmismatch", NS, "BUY", 70.0, 0.40, 12),
                ("chain", "0xABCDEF", NS, "BUY", 200.0, 0.80, 20),
                ("poll", "0xabcdef", NS, "BUY", 200.0, 0.80, 20),
            ])
            await ms.his_fills(c, "rn1", D1_CID)
            before = ms.his_fills_dedup()
            assert before == {"dup_rows": 2, "dup_shares": 5196.0}
            ref = await ms.his_fills_distinct(c, "rn1", D1_CID)
            assert [(f["source"], f["asset"], f["side"], f["size"]) for f in ref] == [
                ("chain", K, "BUY", 15164.0), ("poll", K, "BUY", 4996.0), ("poll", K, "SELL", 50.0),
                ("poll", NS, "BUY", 70.0), ("chain", NS, "BUY", 200.0)]
            assert ms.his_fills_distinct_dedup() == {"dup_rows": 1, "dup_shares": 200.0}
            pos = mi.net_positions(ref)
            assert pos[K] == 15164.0 + 4996.0 - 50.0 and pos[NS] == 270.0
            assert ms.his_fills_dedup() == before, "the reference never moves the reader's counter"
            assert not any(k in ref[0] for k in ("dup_rows", "dup_shares", "has_net_leg", "collapsed", "tx_key", "net_leg"))
        finally:
            await _drop(admin, c, name)
    _run(run())


def test_s1_rows_are_net_leg_rows_and_follow_the_chain_rule():
    """ingestion/s1_emitter.py is the SECOND chain source: its `agg`
    record is the wallet's aggregate view of the tx (the same shape as
    chain.py's _wallet_1155_legs), and the ingest probes on both paths
    (SQL_PROBE and _handle_v3's pre-probe, `source IN ('chain', 's1')`
    per (tx, whale, asset)) keep an s1 row and a chain row off the same
    fill. An s1 row CAN share a tx with poll rows -- the venue
    re-delivers the fill, and a multi-maker split lands under other
    keys exactly as it does beside a chain row -- so they follow the
    same rule: poll rows under an s1 row collapse; an s1 row alone is
    kept; were a chain row and an s1 row ever to share a key, both are
    net-leg rows and both are kept."""
    assert ms.FILLS_NET_LEG_SOURCES == ("chain", "s1")

    async def run():
        admin, c, name = await _scratch()
        try:
            await _insert(c, [
                ("s1", "0xs1split", K, "BUY", 9000.0, 0.50, 10),
                ("poll", "0xs1split", K, "BUY", 4000.0, 0.49, 10),
                ("poll", "0xs1split", K, "BUY", 5000.0, 0.51, 10),
                ("s1", "0xs1alone", NS, "BUY", 300.0, 0.80, 20),
                ("chain", "0xboth", NS, "BUY", 100.0, 0.80, 30),
                ("s1", "0xboth", NS, "BUY", 100.0, 0.80, 30),
                ("poll", "0xboth", NS, "BUY", 100.0, 0.80, 30),
            ])
            fills = await ms.his_fills(c, "rn1", D1_CID)
            assert [(f["source"], f["size"]) for f in fills] == [
                ("s1", 9000.0), ("s1", 300.0), ("chain", 100.0), ("s1", 100.0)]
            assert ms.his_fills_dedup() == {"dup_rows": 3, "dup_shares": 9100.0}
        finally:
            await _drop(admin, c, name)
    _run(run())


def test_drift_on_the_collapsed_reading_reads_zero_against_the_snapshot():
    """The Kostyuk numbers: collapsed 55,993.4 / 29,555.0 against the
    snapshot reads 0.0; the venue's integer rendering (55,993) reads
    under 2e-5, three orders under MIRROR_DRIFT_MAX; the raw reading
    (92,145 / 39,779) is what refused every increase under `drift`."""
    assert rules.drift_net_rule(55993.4, 29555.0, 55993.4, 29555.0) == 0.0
    d_int = rules.drift_net_rule(55993.4, 29555.0, 55993.0, 29555.0)
    assert d_int is not None and 0.0 <= d_int < 2e-5 < rules.MIRROR_DRIFT_MAX
    d_raw = rules.drift_net_rule(92145.2, 39779.4, 55993.0, 29555.0)
    assert d_raw is not None and d_raw > rules.MIRROR_DRIFT_MAX
    # the per-token rule on the long token alone reads the same verdicts:
    # the raw long against the snapshot is refused `drift`, the collapsed
    # long is admitted
    assert rules.drift_rule(92145.2, 55993.0, True, False).refusal == "drift"
    assert rules.drift_rule(55993.4, 55993.0, True, False).increase_ok is True


# ------------------------------------------- 2. the parse and the plumbing

def test_his_fills_sql_parses_states_the_rule_and_keeps_the_mappers_columns():
    import pglast
    src = inspect.getsource(ms.his_fills)
    sql = src.split('"""')[3]
    assert pglast.parse_sql(sql)
    flat = " ".join(sql.split())
    assert "IN ('chain', 's1')" in flat and "PARTITION BY" in flat and "WHERE NOT d.collapsed" in flat
    assert "ORDER BY d.ts, d.id" in flat
    assert "AS market_title, t.event_slug" in flat, "the fake pools key on this fragment"
    for frag in ("COALESCE(t.outcome, mt.outcome) AS outcome",
                 "COALESCE(t.outcome_index, mt.outcome_index) AS outcome_index",
                 "LEFT JOIN market_tokens mt ON mt.token_id = t.asset",
                 "LEFT JOIN markets m ON m.condition_id = t.condition_id"):
        assert frag in flat, frag
    assert "m.event_title" in src and "t.event_title" not in src
    doc = " ".join(ms.his_fills.__doc__.split())
    assert "ONE READING PER (tx_hash, asset, side)" in doc and "read-side" in doc.lower()
    assert "dedupe on (tx, asset, side) at ingest with a sum check" in doc


def test_his_fills_strips_the_totals_off_the_rows_and_reports_them_per_call():
    class _P:
        def __init__(self, rows, raise_=False):
            self.rows, self.raise_ = rows, raise_

        async def fetch(self, sql, *a):
            if self.raise_:
                raise RuntimeError("db down")
            return list(self.rows)

    rows = [dict(_fill(M, "BUY", 100.0, 0.5, 1000), dup_rows=3, dup_shares=1234.5),
            dict(_fill(M, "BUY", 50.0, 0.5, 1001), dup_rows=3, dup_shares=1234.5)]
    fills = _run(ms.his_fills(_P(rows), "rn1", CID))
    assert len(fills) == 2 and not any("dup_rows" in f or "dup_shares" in f for f in fills)
    assert ms.his_fills_dedup() == {"dup_rows": 3, "dup_shares": 1234.5}
    assert "dup_rows" in rows[0], "the caller's rows are not mutated"
    # a fake's rows carry no totals: zero; an empty result: zero
    _run(ms.his_fills(_P([_fill(M, "BUY", 1.0, 0.5, 1000)]), "rn1", CID))
    assert ms.his_fills_dedup() == {"dup_rows": 0, "dup_shares": 0.0}
    _run(ms.his_fills(_P(rows), "rn1", CID))
    _run(ms.his_fills(_P([]), "rn1", CID))
    assert ms.his_fills_dedup() == {"dup_rows": 0, "dup_shares": 0.0}
    # a raising read leaves no stale reading behind
    _run(ms.his_fills(_P(rows), "rn1", CID))
    with pytest.raises(RuntimeError):
        _run(ms.his_fills(_P(rows, raise_=True), "rn1", CID))
    assert ms.his_fills_dedup() == {"dup_rows": 0, "dup_shares": 0.0}


def test_every_reader_of_his_position_goes_through_his_fills():
    """The readers the brief names -- net_positions, _his_level,
    notional_in_window, fills_since, the report's census -- take the
    fills his_fills hands back; no other statement in the mirror reads
    his position off `trades` raw."""
    for fn in (ml._tick_candidate, ml._tick_book):
        s = inspect.getsource(fn)
        assert "await ms.his_fills(t.pool, w, cid)" in s and "_count_fills_dedup(t)" in s
        assert "FROM trades" not in s
    assert "fills = await his_fills(pool, whale, condition_id)" in inspect.getsource(ms.shadow_market)
    from sportsassets.analytics import mirror_report as mr
    s = inspect.getsource(mr.mirror_cover_report)
    assert "await ms.his_fills(pool, whale, cid)" in s and "net_positions(fills)" in s
    assert "notional_in_window(fills" in s
    # the only raw `trades` statements left in the two workers read no
    # position: the ratio's opening bursts (compute_ratio) and the
    # active-market list (active_conditions), beside his_fills itself;
    # the live worker reads `trades` nowhere. E19b (2026-09-08): a FOURTH
    # statement, the UNWIRED reference his_fills_distinct (lane 8's key,
    # withdrawn from sizing by the fills-vs-venue first run), reads a
    # position that no worker, tick or report ever calls for -- pinned
    # by name here, so the count is 4 and the callers are named
    assert "FROM trades" not in inspect.getsource(ml)
    assert inspect.getsource(ms).count("FROM trades t") == 4
    assert "FROM trades t" in inspect.getsource(ms.compute_ratio)
    assert "FROM trades t" in inspect.getsource(ms.active_conditions)
    assert "FROM trades t" in inspect.getsource(ms.his_fills)
    assert "FROM trades t" in inspect.getsource(ms.his_fills_distinct)
    assert inspect.getsource(ms).count("his_fills_distinct(") == 1, "the def alone"
    assert "his_fills_distinct" not in inspect.getsource(ml)
    assert "his_fills_distinct" not in inspect.getsource(mr)
    for fn in (ms.shadow_market, ms.tick_once):
        assert "his_fills_distinct" not in inspect.getsource(fn), fn.__name__
    # his_fills itself is 7a4b852's text byte for byte (D1's key, the
    # reader that ships) -- read off a COMMITTED COPY of the commit's own
    # slice (tests/fixtures/his_fills_7a4b852.py.txt), never a paraphrase,
    # so the pin holds on every checkout: backend-tests.yml checks out at
    # depth 1 and `git show 7a4b852:...` there is "invalid object name"
    # (exit 128), and a source export carries no history at all. Where
    # the checkout DOES carry the commit, the fixture is read back
    # against it too, so the copy can never drift from the commit.
    import pathlib
    import subprocess
    fixture = pathlib.Path(__file__).resolve().with_name("fixtures") / "his_fills_7a4b852.py.txt"
    want = fixture.read_text()
    assert want == inspect.getsource(ms.his_fills)
    head = "async def his_fills(pool, whale: str, condition_id: str) -> list[dict]:\n"
    tail = "    _FILLS_DEDUP.update(dup_rows=dup_rows, dup_shares=dup_shares)\n    return out\n"
    assert want.startswith(head) and want.endswith(tail)
    try:
        has_commit = subprocess.run(["git", "cat-file", "-e", "7a4b852^{commit}"],
                                    capture_output=True).returncode == 0
    except OSError:                      # no git on the PATH: the fixture stands alone
        has_commit = False
    if has_commit:
        old = subprocess.run(["git", "show", "7a4b852:backend/sportsassets/workers/mirror_shadow.py"],
                             capture_output=True, text=True, check=True).stdout
        i = old.index(head)
        assert old[i:old.index(tail, i) + len(tail)] == want, "the fixture is the commit's own slice"


# --------------------------------------------- 3. the two ticks' census

class _DedupShadowPool(_ShadowPool):
    """his_fills rows that carry the SQL's totals, as the real query
    hands them back on every kept row."""

    def __init__(self, dup_rows, dup_shares, **kw):
        super().__init__(**kw)
        self.dup = (dup_rows, dup_shares)

    async def fetch(self, sql, *a):
        rows = await super().fetch(sql, *a)
        if "AS market_title, t.event_slug" in " ".join(sql.split()):
            return [dict(r, dup_rows=self.dup[0], dup_shares=self.dup[1]) for r in rows]
        return rows


def test_the_shadow_tick_sums_what_it_collapsed(monkeypatch):
    _nosleep(monkeypatch)
    monkeypatch.setenv("MIRROR_WHALES", "rn1")
    ms._ratio_cache.update(at=0.0, by_whale={})
    ms._backoff_until = 0.0
    ms._unmapped_until.clear()
    p = _DedupShadowPool(4, 21374.8, fills=HIS, conds=["c0", "c1", "c2"])
    stats = _run(ms.tick_once(p, _Pmus(), now_ts=9000.0))
    assert stats["markets"] == 3 and not stats.get("abandoned")
    assert stats["fills_dedup_rows"] == 12 and stats["fills_dedup_shares"] == round(3 * 21374.8, 4)
    rows = [a for s, a in p.writes if "INSERT INTO mirror_shadow" in s]
    assert rows
    import json
    d = json.loads(rows[0][-1]) if isinstance(rows[0][-1], str) else rows[0][-1]
    assert d["fills_dedup_rows"] == 4 and d["fills_dedup_shares"] == 21374.8
    # present at zero on a tick that collapsed nothing
    p0 = _ShadowPool(fills=HIS)
    ms._backoff_until = 0.0
    st0 = _run(ms.tick_once(p0, _Pmus(), now_ts=9100.0))
    assert st0["fills_dedup_rows"] == 0 and st0["fills_dedup_shares"] == 0.0
    ms._backoff_until = 0.0


class _DedupPool(_LivePool):
    def __init__(self, dup_rows, dup_shares, **kw):
        super().__init__(**kw)
        self.dup = (dup_rows, dup_shares)

    async def fetch(self, sql, *a):
        rows = await super().fetch(sql, *a)
        if "AS market_title, t.event_slug" in " ".join(sql.split()):
            return [dict(r, dup_rows=self.dup[0], dup_shares=self.dup[1]) for r in rows]
        return rows


def _live_pool(dup_rows, dup_shares, **kw):
    from tests.test_mirror_live_worker import _his, _ratio_fills
    kw.setdefault("fills", _his())
    kw.setdefault("snap", {M: 300.0, N: 0.0})
    kw.setdefault("snap_at", NOW - 40)
    kw.setdefault("ratio_fills", _ratio_fills())
    return _DedupPool(dup_rows, dup_shares, **kw)


def test_the_live_tick_publishes_the_collapse_as_one_block_that_survives_the_sanitizer():
    """A book's read and a candidate's read both count; the block is
    appended LAST, so on a tick that also carries `venue_positions` the
    top level sits exactly at the sanitizer's cap and nothing is
    truncated -- and `integ` (39 keys) is untouched."""
    p = _live_pool(3, 15164.0)
    p.add_book(ledger=100)
    st = _tick(p, _Venue())
    assert st["fills_dedup"] == {"rows": 3, "shares": 15164.0}, st["fills_dedup"]
    assert list(st)[-1] == "fills_dedup", "appended after every other key"
    served = api_app._sanitize_detail(st)
    assert served["fills_dedup"] == {"rows": 3, "shares": 15164.0}
    assert "_truncated_keys" not in served and len(st["integ"]) < api_app._DETAIL_MAX_KEYS
    assert "fills_dedup" not in ml._new_stats() and "fills_dedup" not in st["integ"]
    # a book AND a candidate: two reads, both counted
    p2 = _live_pool(2, 1000.0, conds=[CID, "0xother"])
    p2.add_book(ledger=100)
    st2 = _tick(p2, _Venue())
    assert st2["fills_dedup"]["rows"] == 4 and st2["fills_dedup"]["shares"] == 2000.0
    # zero, not absent, on a tick that collapsed nothing
    st3 = _tick(_pool(), _Venue())
    assert st3["fills_dedup"] == {"rows": 0, "shares": 0.0}


# ------------------------------------------------ 4. the terminal memo

def test_terminal_candidates_are_memoised_so_the_open_one_gets_a_slot(monkeypatch):
    """Census 19:02Z: venue_halted on 26 of 29 reads, every one
    MARKET_STATE_EXPIRED -- his newest-touched mapped markets are
    matches he trades to settlement, and the 20 candidate slots were
    spent re-reading them every tick. 25 expired candidates ahead of 1
    open one: the first tick reads 20 expired (as today); the second
    tick skips those 20 under `cand_terminal_skipped`, reads the 5
    remaining expired and then the open one."""
    exp = [f"0xexpired{i:02d}" for i in range(25)]
    slugs = {c: f"aec-wta-done{i:02d}-2026-09-06" for i, c in enumerate(exp)}
    slugs[CID] = SLUG

    async def _map(pool, fills, pmus=None, **kw):
        return {"us_slug": slugs[kw["condition_id"]], "long_asset": M, "other_asset": N,
                "source": "premap"}

    monkeypatch.setattr(ms, "map_market", _map)
    monkeypatch.setattr(ml, "_terminal_until", {})
    # the walk's geometry this test is about: 20 slots (the default is
    # 40 since E2, 2026-09-06; the memo's rule is the same at either)
    monkeypatch.setattr(ml, "MAX_MARKETS_PER_TICK", 20)
    p = _pool(conds=exp + [CID])
    v = _Venue(states={slugs[c]: "MARKET_STATE_EXPIRED" for c in exp})
    st1 = _tick(p, v)
    reads1 = [c[1] for c in v.calls if c[0] == "bbo"]
    assert len(reads1) == 20 and SLUG not in reads1 and st1["capped_tick"] is True
    assert set(reads1) == {slugs[c] for c in exp[:20]}, "the newest 20, as today"
    assert _census(st1, "cand_terminal_skipped") == 0 and _census(st1, "venue_halted") == 20
    assert len(ml._terminal_until) == 20 and all(k[1] in exp[:20] for k in ml._terminal_until)
    assert all(v_ == NOW + ms.UNMAPPED_TTL_S for v_ in ml._terminal_until.values())
    assert not st1["abandoned"], "expired markets never count toward the miss streak"
    v.calls.clear()
    st2 = _tick(p, v, now=NOW + 30)
    reads2 = [c[1] for c in v.calls if c[0] == "bbo"]
    assert _census(st2, "cand_terminal_skipped") == 20
    assert [s for s in reads2 if s != SLUG] == [slugs[c] for c in exp[20:]]
    assert SLUG in reads2, "the open market got its slot"
    assert st2.get("capped_tick") is not True and len(ml._terminal_until) == 25
    # the memo expires with the TTL: every expired market is read again
    v.calls.clear()
    st3 = _tick(p, v, now=NOW + 30 + ms.UNMAPPED_TTL_S + 1)
    reads3 = [c[1] for c in v.calls if c[0] == "bbo"]
    assert _census(st3, "cand_terminal_skipped") == 0 and len([s for s in reads3 if s != SLUG]) == 20
    assert "cand_terminal_skipped" in ml.CENSUS_KEYS and ml.CENSUS_KEYS[-1] == "cand_terminal_skipped"


def test_the_terminal_memo_is_never_written_for_a_halt_or_from_a_books_read(monkeypatch):
    monkeypatch.setattr(ml, "_terminal_until", {})
    # HALTED / SUSPENDED / PREOPEN on a candidate: counted, never memoised
    for state in ("MARKET_STATE_HALTED", "MARKET_STATE_SUSPENDED", "MARKET_STATE_PREOPEN"):
        st = _tick(_pool(), _Venue(state=state))
        assert _census(st, "venue_halted") >= 1 and ml._terminal_until == {}, state
    # every terminal state on a candidate is
    for state in sorted(ms.STATE_TERMINAL):
        monkeypatch.setattr(ml, "_terminal_until", {})
        _tick(_pool(), _Venue(state=state))
        assert ml._terminal_until == {("rn1", CID): NOW + ms.UNMAPPED_TTL_S}, state
    # an existing book on an expired market: managed every tick, never memoised
    monkeypatch.setattr(ml, "_terminal_until", {})
    p = _pool()
    p.add_book(ledger=100)
    st = _tick(p, _Venue(state="MARKET_STATE_EXPIRED"))
    assert _census(st, "venue_halted") >= 1 and ml._terminal_until == {}
    assert "_terminal_until" not in inspect.getsource(ml._tick_book)
    # a candidate skipped by the memo spends no read and maps nothing
    monkeypatch.setattr(ml, "_terminal_until", {("rn1", CID): NOW + 100.0})
    calls = []

    async def _map(pool, fills, pmus=None, **kw):
        calls.append(kw)
        return None

    monkeypatch.setattr(ms, "map_market", _map)
    v = _Venue()
    st = _tick(_pool(), v)
    assert not calls and not [c for c in v.calls if c[0] == "bbo"]
    assert _census(st, "cand_terminal_skipped") == 1 and st["reads"] == 0
