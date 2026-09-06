"""D1 adversarial review: try to make the read-side collapse lose a real
fill, over-read his position, or make the terminal memo hide a market.

Real-SQL tests execute `mirror_shadow.his_fills` against a scratch
Postgres (the builder's `_scratch` helper; they skip visibly without
one). The SQL mutants are executed too: a wrapping connection rewrites
the statement the way a wrong build would, and the assertion that
catches the mutant is shown to fail on it.
"""

from __future__ import annotations

import asyncio
import inspect
import random

import pytest

from sportsassets.analytics import mirror as mi
from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.api import app as api_app
from sportsassets.workers import mirror_live as ml
from sportsassets.workers import mirror_shadow as ms
from tests.test_d1_fills_dedup import D1_CID, K, NS, _d1_rows, _drop, _insert, _scratch
from tests.test_mirror_live_worker import CID, M, N, NOW, SLUG, _armed  # noqa: F401 — the fixture
from tests.test_mirror_live_worker import _census, _places, _pool, _tick, _Venue


def _run(coro):
    return asyncio.run(coro)


class _Mut:
    """A connection that rewrites his_fills' SQL the way a wrong build
    would; every substitution must hit, or the mutant is not the one
    claimed."""

    def __init__(self, conn, subs):
        self.conn, self.subs = conn, subs

    async def fetch(self, sql, *a):
        for old, new in self.subs:
            assert old in sql, old
            sql = sql.replace(old, new)
        return await self.conn.fetch(sql, *a)


def _key_sums(rows):
    """Per (lower tx, asset, upper side): (net-leg shares, per-match shares)."""
    out: dict = {}
    for src, tx, asset, side, size, _px, _dt in rows:
        k = (tx.lower(), asset, side.upper())
        net, per = out.get(k, (0.0, 0.0))
        if src in ms.FILLS_NET_LEG_SOURCES:
            net += size
        else:
            per += size
        out[k] = (net, per)
    return out


# ------------------------------------------ 1. the collapse can lose fills?

def test_collapsed_reading_per_key_is_never_larger_than_max_of_chain_and_poll():
    """Randomised: per key the collapsed shares are EXACTLY the net-leg
    sum when the key holds one, else the per-match sum -- so never over
    max(chain, poll), and the market total is never over the raw total.
    (Excludes a chain+s1 collision on one key, pinned separately.)"""
    rng = random.Random(1106)
    rows = []
    for i in range(160):
        tx = f"0xtx{rng.randrange(60):03d}"
        asset = rng.choice([K, NS])
        side = rng.choice(["BUY", "SELL", "buy"])
        src = rng.choice(["chain", "poll", "poll", "backfill", "s1"])
        rows.append((src, tx if rng.random() < 0.8 else tx.upper(), asset, side,
                     round(rng.uniform(1, 5000), 2), round(rng.uniform(0.05, 0.95), 3), i))
    # one net-leg source per key: drop s1 rows on keys that hold a chain row
    chain_keys = {(r[1].lower(), r[2], r[3].upper()) for r in rows if r[0] == "chain"}
    rows = [r for r in rows if not (r[0] == "s1" and (r[1].lower(), r[2], r[3].upper()) in chain_keys)]

    async def run():
        admin, c, name = await _scratch()
        try:
            await _insert(c, rows)
            fills = await ms.his_fills(c, "rn1", D1_CID)
            got: dict = {}
            for f in fills:
                k = (f["tx_hash"].lower(), f["asset"], f["side"].upper())
                got[k] = got.get(k, 0.0) + f["size"]
            exp = _key_sums(rows)
            for k, (net, per) in exp.items():
                want = net if net else per
                assert abs(got.get(k, 0.0) - want) < 1e-6, (k, net, per, got.get(k))
                assert got.get(k, 0.0) <= max(net, per) + 1e-6
            assert set(got) == set(exp)
            raw = sum(r[4] for r in rows)
            assert sum(f["size"] for f in fills) <= raw + 1e-6
            d = ms.his_fills_dedup()
            assert abs(raw - sum(f["size"] for f in fills) - d["dup_shares"]) < 1e-3
            assert d["dup_rows"] == len(rows) - len(fills)
        finally:
            await _drop(admin, c, name)
    _run(run())


def test_chain_and_s1_on_one_key_are_BOTH_kept_an_over_read_the_rule_admits():
    """The one shape where the collapsed reading EXCEEDS max(chain, poll):
    a chain row and an s1 row on the same (tx, asset, side) are both
    net-leg rows and both survive (the builder's stated rule; the ingest
    probes are meant to make it impossible, and the raw reading counted
    both too). Pinned here so the residual is visible, not hidden."""
    async def run():
        admin, c, name = await _scratch()
        try:
            await _insert(c, [
                ("chain", "0xcollide", K, "BUY", 1000.0, 0.5, 1),
                ("s1", "0xcollide", K, "BUY", 1000.0, 0.5, 1),
                ("poll", "0xcollide", K, "BUY", 1000.0, 0.5, 1),
            ])
            fills = await ms.his_fills(c, "rn1", D1_CID)
            assert mi.net_positions(fills)[K] == 2000.0, "chain + s1 double-counted"
            assert ms.his_fills_dedup() == {"dup_rows": 1, "dup_shares": 1000.0}
        finally:
            await _drop(admin, c, name)
    _run(run())


def test_the_s1_same_asset_entry_deferral_under_reads_and_the_direction_depends_on_side():
    """A taker sweep fills two of his resting orders on one token in one
    tx: s1 emits leg 1 (exec_owner), defers leg 2 to the poller
    (`s1.abstain.same_asset_entry`), the poller carries both legs. The
    rule collapses BOTH poll legs under the s1 row: leg 2 is LOST.
    On a BUY that is an under-read of his position (smaller mirror
    target: conservative). On a SELL it is an under-read of his EXIT --
    his position reads LARGER than the venue's, so the mirror keeps
    holding what he sold: not conservative."""
    async def run():
        admin, c, name = await _scratch()
        try:
            await _insert(c, [
                ("chain", "0xopen", K, "BUY", 10000.0, 0.5, 1),
                # the sweep: leg 1 (s1 + poll) and leg 2 (poll only)
                ("s1", "0xsweep", K, "SELL", 5000.0, 0.6, 10),
                ("poll", "0xsweep", K, "SELL", 5000.0, 0.6, 10),
                ("poll", "0xsweep", K, "SELL", 3000.0, 0.6, 10),
            ])
            fills = await ms.his_fills(c, "rn1", D1_CID)
            pos = mi.net_positions(fills)
            assert pos[K] == 5000.0, "the venue holds 2,000: the collapsed reading is LARGER"
            raw_net = float(await c.fetchval(
                "SELECT sum(CASE WHEN side='BUY' THEN size ELSE -size END) FROM trades WHERE asset=$1", K))
            assert raw_net == 10000.0 - 13000.0, "the raw table over-counts his SELL (leg 1 twice)"
            # the same shape on a BUY: the collapsed reading is SMALLER
            await c.execute("DELETE FROM trades")
            await _insert(c, [
                ("s1", "0xsweepb", K, "BUY", 5000.0, 0.6, 10),
                ("poll", "0xsweepb", K, "BUY", 5000.0, 0.6, 10),
                ("poll", "0xsweepb", K, "BUY", 3000.0, 0.6, 10),
            ])
            fills = await ms.his_fills(c, "rn1", D1_CID)
            assert mi.net_positions(fills)[K] == 5000.0 < 8000.0
            assert ms.his_fills_dedup() == {"dup_rows": 2, "dup_shares": 8000.0}
        finally:
            await _drop(admin, c, name)
    _run(run())


def test_two_orders_in_one_tx_with_a_whole_chain_row_read_the_chain_row():
    """Two of his taker orders settled in one tx on one token: chain.py
    (_wallet_1155_legs) sums the wallet's whole 1155 flow, so the chain
    row is the whole; the poll rows are its maker matches and collapse.
    Equal to the venue, not an under-read."""
    async def run():
        admin, c, name = await _scratch()
        try:
            await _insert(c, [
                ("chain", "0xtwoorders", K, "BUY", 8000.0, 0.55, 1),
                ("poll", "0xtwoorders", K, "BUY", 3000.0, 0.54, 1),
                ("poll", "0xtwoorders", K, "BUY", 2000.0, 0.55, 1),
                ("poll", "0xtwoorders", K, "BUY", 3000.0, 0.56, 1),
            ])
            fills = await ms.his_fills(c, "rn1", D1_CID)
            assert mi.net_positions(fills)[K] == 8000.0
        finally:
            await _drop(admin, c, name)
    _run(run())


# ------------------------------------------ 2/3. tx_hash and side spelling

def test_tx_hash_case_and_the_empty_sentinel_and_side_case():
    async def run():
        admin, c, name = await _scratch()
        try:
            await _insert(c, [
                # chain lowercases, the poller only strips: one key
                ("chain", "0xabcdef0123", K, "BUY", 500.0, 0.5, 1),
                ("poll", "0xABCDEF0123", K, "BUY", 500.0, 0.5, 1),
                # side spelled 'buy' on a poll row: one key with the chain BUY
                ("chain", "0xsidecase", K, "BUY", 700.0, 0.5, 2),
                ("poll", "0xsidecase", K, "buy", 700.0, 0.5, 2),
                # the other side of the same tx is another key: kept
                ("poll", "0xsidecase", K, "SELL", 10.0, 0.5, 2),
                # THE '' SENTINEL (chain.py writes '' when no log carried a
                # hash): every '' row is its own key -- a chain '' row must
                # NOT swallow the poll '' rows of unrelated fills
                ("chain", "", K, "BUY", 100.0, 0.5, 3),
                ("poll", "", K, "BUY", 200.0, 0.5, 4),
                ("poll", "", K, "BUY", 300.0, 0.5, 5),
                ("poll", "", NS, "BUY", 400.0, 0.5, 6),
                # leading whitespace is NOT normalised (no writer produces it:
                # poller .strip()s, chain lowercases a hex string): a
                # different key, both kept -- the today's reading, no loss
                ("chain", "0xspace", NS, "BUY", 50.0, 0.5, 7),
                ("poll", " 0xspace", NS, "BUY", 50.0, 0.5, 7),
            ])
            fills = await ms.his_fills(c, "rn1", D1_CID)
            pos = mi.net_positions(fills)
            assert pos[K] == 500.0 + 700.0 - 10.0 + 100.0 + 200.0 + 300.0
            assert pos[NS] == 400.0 + 50.0 + 50.0
            assert ms.his_fills_dedup() == {"dup_rows": 2, "dup_shares": 1200.0}
            # NULL tx_hash: the schema forbids it (001_init: NOT NULL), the
            # COALESCE branch is unreachable but harmless
            with pytest.raises(Exception):
                await c.execute(
                    "INSERT INTO trades (whale_id, tx_hash, asset, condition_id, side, size, price, ts, source)"
                    " VALUES (1, NULL, $1, $2, 'BUY', 1, 0.5, now(), 'poll')", K, D1_CID)
        finally:
            await _drop(admin, c, name)
    _run(run())


# ------------------------------------------ 4/5. sizing and drift

def test_kostyuk_target_before_and_after_and_a_poll_only_market_is_unchanged():
    async def run():
        admin, c, name = await _scratch()
        try:
            await _insert(c, _d1_rows())
            fills = await ms.his_fills(c, "rn1", D1_CID)
            pos = mi.net_positions(fills)
            raw = {r["asset"]: r["s"] for r in await c.fetch(
                "SELECT asset, sum(size)::float8 AS s FROM trades GROUP BY asset")}
            net_after = mi.his_net(pos[K], pos[NS])
            net_before = mi.his_net(raw[K], raw[NS])
            assert round(net_after, 1) == 26438.4 and round(net_before, 1) == 52365.8
            snap_long, snap_other = 55993.0, 29555.0
            # at the production default MIRROR_RATIO 0.10 (the _armed fixture
            # pins the module's to 1.0, so the ratio is passed explicitly)
            # BEFORE: target 5,236 sh ($2,356), refused under `drift` (0.495 > 0.05)
            t0 = rules.mirror_target(0.10, net_before, 0.45, rules.MIRROR_CLIP_USD, cap_usd=2500.0)
            d0 = rules.drift_net_rule(raw[K], raw[NS], snap_long, snap_other)
            assert t0["target"] == 5236 and t0["capped"] is False and d0 > rules.MIRROR_DRIFT_MAX
            # AFTER: target 2,643 sh (~$1,189 at 0.45, under the $2,500 cap), drift 1.5e-5
            t1 = rules.mirror_target(0.10, net_after, 0.45, rules.MIRROR_CLIP_USD, cap_usd=2500.0)
            d1 = rules.drift_net_rule(pos[K], pos[NS], snap_long, snap_other)
            assert t1["target"] == 2643 and t1["capped"] is False and t1["refusal"] is None
            assert d1 is not None and d1 < 2e-5 < rules.MIRROR_DRIFT_MAX
            assert t1["target"] < t0["target"], "the admitted target is the SMALLER one"
            # at an exact-copy ratio (1.0) both readings cap at $2,500 = 5,555 sh:
            # the collapse changes the drift verdict there, never the size
            for net in (net_before, net_after):
                t = rules.mirror_target(1.0, net, 0.45, rules.MIRROR_CLIP_USD, cap_usd=2500.0)
                assert t["target"] == 5555 and t["capped"] is True

            # a POLL-ONLY market (the chain lane silent): every row kept,
            # the reading and its drift are byte-identical to the raw table
            await c.execute("DELETE FROM trades")
            await _insert(c, [
                ("poll", "0xp1", K, "BUY", 1000.0, 0.5, 1), ("poll", "0xp1", K, "BUY", 500.0, 0.5, 1),
                ("poll", "0xp2", K, "BUY", 700.0, 0.5, 2), ("poll", "0xp3", K, "SELL", 200.0, 0.5, 3),
                ("poll", "0xp4", NS, "BUY", 900.0, 0.5, 4), ("backfill", "0xp5", NS, "BUY", 100.0, 0.5, 5),
            ])
            fills = await ms.his_fills(c, "rn1", D1_CID)
            assert len(fills) == 6 and ms.his_fills_dedup() == {"dup_rows": 0, "dup_shares": 0.0}
            pos = mi.net_positions(fills)
            raw = {r["asset"]: r["s"] for r in await c.fetch(
                "SELECT asset, sum(CASE WHEN side='BUY' THEN size ELSE -size END)::float8 AS s "
                "FROM trades GROUP BY asset")}
            assert pos == {K: raw[K], NS: raw[NS]} == {K: 2000.0, NS: 1000.0}
            assert rules.drift_net_rule(pos[K], pos[NS], 1900.0, 1000.0) == \
                rules.drift_net_rule(raw[K], raw[NS], 1900.0, 1000.0) == 0.1
        finally:
            await _drop(admin, c, name)
    _run(run())


# ------------------------------------------ 9. the SQL mutants, executed

def test_sql_mutants_are_caught():
    async def run():
        admin, c, name = await _scratch()
        try:
            await _insert(c, _d1_rows() + [
                ("chain", "0xcase", NS, "BUY", 100.0, 0.5, 9000), ("poll", "0xCASE", NS, "BUY", 100.0, 0.5, 9000),
                ("chain", "0xside", NS, "BUY", 100.0, 0.5, 9001), ("poll", "0xside", NS, "buy", 100.0, 0.5, 9001),
                ("chain", "", NS, "BUY", 10.0, 0.5, 9002), ("poll", "", NS, "BUY", 20.0, 0.5, 9003),
            ])
            fills = await ms.his_fills(c, "rn1", D1_CID)
            good = mi.net_positions(fills)
            assert abs(good[K] - 55993.4) < 1e-6 and abs(good[NS] - 29555.0 - 100 - 100 - 30) < 1e-6
            mutants = {
                "no lower()": [("lower(NULLIF(t.tx_hash, ''))", "NULLIF(t.tx_hash, '')")],
                "no upper()": [("t.asset, upper(t.side)", "t.asset, t.side")],
                "no '' sentinel": [("COALESCE(lower(NULLIF(t.tx_hash, '')), 'row:' || t.id::text)",
                                    "lower(t.tx_hash)")],
                "chain rows collapse too": [
                    ("(COALESCE(f.source, '') NOT IN ('chain', 's1') AND f.has_net_leg) AS collapsed",
                     "f.has_net_leg AS collapsed")],
                "poll is a net-leg source": [("IN ('chain', 's1')", "IN ('chain', 's1', 'poll')")],
            }
            caught = {}
            for label, subs in mutants.items():
                m = mi.net_positions(await ms.his_fills(_Mut(c, subs), "rn1", D1_CID))
                caught[label] = (round(m.get(K, 0.0), 1), round(m.get(NS, 0.0), 1))
                assert m != good, label
            assert caught["no lower()"][1] == good[NS] + 100.0            # 0xCASE kept: +100
            assert caught["no upper()"][1] == good[NS] + 100.0            # 'buy' kept: +100
            assert caught["no '' sentinel"][1] == good[NS] - 20.0         # the '' poll row swallowed
            # every key that holds a chain row loses the chain row too: only
            # the poll-only txs (6,509.9 K) and the '' poll row (20 NS) survive
            assert caught["chain rows collapse too"] == (6509.9, 20.0)
            assert caught["poll is a net-leg source"][0] == 92145.2       # the raw reading
        finally:
            await _drop(admin, c, name)
    _run(run())


# ------------------------------------------ 6. the terminal memo

def test_open_none_and_unreadable_reads_never_memoise(monkeypatch):
    for venue in (_Venue(state="MARKET_STATE_OPEN"), _Venue(state=None), _Venue(raise_bbo=True)):
        monkeypatch.setattr(ml, "_terminal_until", {})
        _tick(_pool(), venue)
        assert ml._terminal_until == {}, venue.state


def test_a_books_expired_read_never_memoises_even_when_a_candidate_shares_its_slug(monkeypatch):
    """A book on cid CID (slug SLUG) reads EXPIRED (book=True); a
    candidate on another condition mapped to the SAME slug reads it
    too. Only the candidate's key is memoised; the book's never."""
    async def _map(pool, fills, pmus=None, **kw):
        return {"us_slug": SLUG, "long_asset": M, "other_asset": N, "source": "premap"}

    monkeypatch.setattr(ms, "map_market", _map)
    monkeypatch.setattr(ml, "_terminal_until", {})
    p = _pool(conds=[CID, "0xother"])
    p.add_book(ledger=100)
    v = _Venue(state="MARKET_STATE_EXPIRED")
    st = _tick(p, v)
    assert [c[1] for c in v.calls if c[0] == "bbo"].count(SLUG) == 2, "the book's read and the candidate's"
    assert ml._terminal_until == {("rn1", "0xother"): NOW + ms.UNMAPPED_TTL_S}
    assert ("rn1", CID) not in ml._terminal_until and _census(st, "venue_halted") == 2


def test_a_market_that_reads_expired_then_open_is_hidden_for_the_ttl_and_a_wake_does_not_clear_it(monkeypatch):
    """The exposure of a wrong TERMINAL read: the candidate is skipped
    for UNMAPPED_TTL_S (900 s) even if the venue then reads OPEN and
    even when a new fill of his wakes the condition. After the TTL it
    reads and opens a book as before."""
    monkeypatch.setattr(ml, "_terminal_until", {})
    p = _pool()
    _tick(p, _Venue(state="MARKET_STATE_EXPIRED"))
    assert ("rn1", CID) in ml._terminal_until
    ml._WOKEN.add(CID)                                   # he traded it again
    v = _Venue(bid=0.30, ask=0.32)                       # and the venue reads OPEN
    st = _tick(p, v, now=NOW + 60)
    assert st["woken"] == [CID] and _census(st, "cand_terminal_skipped") == 1
    assert not [c for c in v.calls if c[0] == "bbo"] and not p.books and not _places(v)
    st = _tick(p, v, now=NOW + ms.UNMAPPED_TTL_S + 1)
    assert _census(st, "cand_terminal_skipped") == 0 and [c for c in v.calls if c[0] == "bbo"]
    assert p.books, "read again and opened once the memo expired"


def test_the_memo_is_per_whale_and_condition_not_per_slug(monkeypatch):
    monkeypatch.setattr(ml, "_terminal_until", {("other", CID): NOW + 100.0})
    v = _Venue()
    st = _tick(_pool(), v)
    assert _census(st, "cand_terminal_skipped") == 0 and [c for c in v.calls if c[0] == "bbo"]


def test_memo_mutants_are_caught(monkeypatch):
    """HALTED memoised / TTL infinite: the builder's oracle assertions
    fail on each mutant (executed by patching the constants the code
    reads at write time). The book's-read mutant needs a source edit;
    it is run from the shell (see the review report)."""
    # mutant: HALTED is terminal (its own patch context: `monkeypatch` is
    # shared with the autouse _armed fixture, so undo() would disarm)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(ms, "STATE_TERMINAL", frozenset(ms.STATE_TERMINAL | {"MARKET_STATE_HALTED"}))
        mp.setattr(ml, "_terminal_until", {})
        _tick(_pool(), _Venue(state="MARKET_STATE_HALTED"))
        with pytest.raises(AssertionError):
            assert ml._terminal_until == {}
    # the same tick on the real constants: no memo
    monkeypatch.setattr(ml, "_terminal_until", {})
    _tick(_pool(), _Venue(state="MARKET_STATE_HALTED"))
    assert ml._terminal_until == {}
    # mutant: TTL infinite
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(ms, "UNMAPPED_TTL_S", float("inf"))
        mp.setattr(ml, "_terminal_until", {})
        p = _pool()
        _tick(p, _Venue(state="MARKET_STATE_EXPIRED"))
        assert ml._terminal_until == {("rn1", CID): float("inf")}
        st = _tick(p, _Venue(state="MARKET_STATE_EXPIRED"), now=NOW + 900.0 + 1)
        with pytest.raises(AssertionError):
            assert _census(st, "cand_terminal_skipped") == 0


# ------------------------------------------ 7. the served census

def test_census_prefix_and_integ_are_byte_identical_to_the_base_and_nothing_served_is_lost():
    import re
    import subprocess
    old = subprocess.run(["git", "show", "2dc3204:backend/sportsassets/workers/mirror_live.py"],
                         capture_output=True, text=True, check=True).stdout
    old_keys = eval("(" + re.search(r"CENSUS_KEYS: tuple\[str, \.\.\.\] = \((.*?)\n\)\n", old, re.S).group(1) + ")")
    old_integ = eval("(" + re.search(r"_INTEG_CENSUS_KEYS: tuple\[str, \.\.\.\] = \((.*?)\n\)\n", old, re.S).group(1) + ")")
    # the base's keys are a prefix; two names were appended after it
    # (the mirror cell's `soccer_floor_lifted`, then D1's `cand_terminal_skipped`, LAST)
    assert old_keys == ml.CENSUS_KEYS[:len(old_keys)] and ml.CENSUS_KEYS[-1] == "cand_terminal_skipped"
    assert old_keys[:api_app._DETAIL_MAX_KEYS] == ml.CENSUS_KEYS[:api_app._DETAIL_MAX_KEYS]
    assert old_integ == ml._INTEG_CENSUS_KEYS
    # a capped ON tick: 38 base + venue_positions + capped_tick + fills_dedup = 41.
    # The sanitizer drops exactly `fills_dedup` (last) and every key that
    # was served at 2dc3204 is still served; the marker `_truncated_keys`
    # is new on such a tick
    p = _pool(conds=[f"0xc{i:02d}" for i in range(25)])
    st = _tick(p, _Venue())
    assert st["capped_tick"] is True and "venue_positions" in st and list(st)[-1] == "fills_dedup"
    served = api_app._sanitize_detail(st)
    assert served["_truncated_keys"] == 1 and "fills_dedup" not in served
    assert set(st) - {"fills_dedup"} <= set(served)
    assert len(st) == 41 and len(ml._new_stats()) == 38


# ------------------------------------------ 8. who reads raw

def test_no_live_sizing_path_reads_trades_raw():
    src = inspect.getsource(ml)
    assert "FROM trades" not in src
    for fn in (ml._tick_book, ml._tick_candidate):
        assert "ms.his_fills(t.pool, w, cid)" in inspect.getsource(fn)
    # the position the target is sized from is net_positions(fills) of
    # the his_fills rows, inside _read_market
    s = inspect.getsource(ml._read_market)
    assert "pos = mi.net_positions(fills)" in s and "his_long = float(pos.get(la" in s
    # the shadow's two remaining raw reads derive no position
    assert inspect.getsource(ms).count("FROM trades t") == 3      # his_fills, compute_ratio, active_conditions
    assert "net_positions" not in inspect.getsource(ms.compute_ratio)
    from sportsassets.analytics import mirror_report as mr
    s = inspect.getsource(mr)
    assert s.count("FROM trades") == 1 and "condition_id IS NULL" in s, "a count of null-condition rows only"


# ------------------------------------------ 1b. the partition has no whale_id

def test_two_wallets_under_one_username_in_one_batched_tx_lose_the_second_wallets_poll_legs():
    """`whales.username` is NOT unique (001_init: only `address` is) and
    his_fills selects by lower(username). The venue batches settlements,
    so two wallets filed under one username can trade the same token
    and side in ONE tx: wallet A's chain row then swallows wallet B's
    poll legs (the partition carries no whale_id) -- a real fill lost.
    Latent unless a mirrored username has two wallet rows; the fix is
    `t.whale_id` in the PARTITION BY."""
    async def run():
        admin, c, name = await _scratch()
        try:
            await c.execute("INSERT INTO whales (id, address, username) VALUES (2, '0xrn1-second', 'rn1')")
            await c.execute(
                "INSERT INTO trades (whale_id, tx_hash, asset, condition_id, side, size, price, ts, source) "
                "VALUES (1, '0xbatched', $1, $2, 'BUY', 1000, 0.5, to_timestamp(1788000000), 'chain'), "
                "       (2, '0xbatched', $1, $2, 'BUY', 400, 0.5, to_timestamp(1788000000), 'poll')", K, D1_CID)
            fills = await ms.his_fills(c, "rn1", D1_CID)
            raw = float(await c.fetchval("SELECT sum(size) FROM trades"))
            assert raw == 1400.0
            # review minor 1, folded: the partition carries whale_id, so wallet
            # B's poll legs are not swallowed by wallet A's chain row
            assert mi.net_positions(fills)[K] == 1400.0, "wallet B's 400 collapsed under wallet A's chain row"
            assert ms.his_fills_dedup() == {"dup_rows": 0, "dup_shares": 0.0}
        finally:
            await _drop(admin, c, name)
    _run(run())
