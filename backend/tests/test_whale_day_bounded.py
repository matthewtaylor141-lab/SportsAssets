"""The whale-day drill-down is bounded (2026-09-03 API restarts behind
the 502s).

reports.settled_bets fetched a whale's ENTIRE trades-join-markets ledger
per /api/whales/{id}/day/{d} call -- swisstony is 889,154 fills, +0.7 to
+1.1 GB per call -- and the diagnostic probe fires that route 56 times
at --max-time 20 plus a retry lane. curl abandons, the handler does
not, so the fetches stacked on a 1.2-1.7 GB floor in a 2 GB container
and Render replaced the process in 5 of 5 probes.

The fix streams the ledger through a server-side cursor in 5,000-row
chunks, folds per asset as rows arrive keeping ten scalars per asset
instead of the Record, and memoizes the finished list per whale (TTL
600 s, single-flight, at most three whales). These tests pin:

  * equivalence -- the streamed output equals, dict for dict and in
    order, the OLD body run over the same randomized ledger (the old
    body is kept here verbatim as the oracle);
  * the cursor-open counts behind the cache: one read per whale within
    the TTL, one read for concurrent callers, three whales resident;
  * no fetch(n) above the chunk, and the kill-switch's batch path
    giving the same answer;
  * a 200,000-row ledger peaking under 60 MB under tracemalloc.
"""

from __future__ import annotations

import asyncio
import gc
import inspect
import json
import random
import time
import tracemalloc
from datetime import datetime, timedelta, timezone

import pytest

from sportsassets.analytics.positions import EPS, Fill, Position
from sportsassets.api import reports
from sportsassets.betting import american_odds, bet_label, bet_type, result_word

# ── the pre-hotfix body, verbatim from the `by_asset` fold onward ──────


def settled_bets_batch(trades) -> list[dict]:
    """reports.settled_bets as it stood before the stream: the whole
    ledger in memory, replayed in one pass. This is the oracle."""

    class Agg:
        def __init__(self) -> None:
            self.pos = Position()
            self.bought_shares = 0.0
            self.first = None
            self.last = None
            self.meta = None

    by_asset: dict[str, Agg] = {}
    for t in trades:
        a = by_asset.setdefault(t["asset"], Agg())
        a.pos.apply(Fill(side=t["side"], size=t["size"], price=t["price"]))
        if t["side"] == "BUY":
            a.bought_shares += t["size"]
        a.first = a.first or t["ts"]
        a.last = t["ts"]
        if a.meta is None or t["m_title"]:
            a.meta = t

    bets: list[dict] = []
    for asset, a in by_asset.items():
        t = a.meta
        resolved = bool(t["resolved"])
        if resolved and not a.pos.resolved:
            prices = t["resolved_prices"]
            if isinstance(prices, str):
                prices = json.loads(prices)
            idx = t["outcome_index"]
            if prices and idx is not None and 0 <= idx < len(prices):
                a.pos.resolve(float(prices[idx]))
        fully_cashed = a.pos.shares <= EPS and a.pos.fills > 0
        if not (a.pos.resolved or (fully_cashed and not resolved)):
            continue  # still open — not a settled bet
        stake = a.pos.notional_in
        avg_price = stake / a.bought_shares if a.bought_shares > EPS else None
        settled_at = (t["resolved_at"] if a.pos.resolved and t["resolved_at"] else a.last)
        bets.append({
            "settled_at": settled_at,
            "sport": (t["m_sport"] if t["m_sport"] and t["m_sport"] != "unclassified"
                      else t["t_sport"]),
            "label": bet_label(t["outcome"], t["m_title"] or t["t_title"], t["event_title"]),
            "bet_type": bet_type(t["outcome"], t["m_title"] or t["t_title"], t["event_title"]),
            "odds": american_odds(avg_price),
            "stake": round(stake, 2),
            "result": result_word(a.pos.realized_pnl, a.pos.resolved),
            "pnl": round(a.pos.realized_pnl, 2),
        })
    bets.sort(key=lambda b: b["settled_at"])
    return bets


# ── a randomized ledger, generated row by row so the cursor can make
#    FRESH row objects per chunk (what asyncpg does) and tracemalloc sees
#    them ────────────────────────────────────────────────────────────────

_T0 = datetime(2026, 8, 1, tzinfo=timezone.utc)
_OUTCOMES = ("Yes", "No", "Over", "Under", "Player {a}")
_SPORTS = ("tennis", "mlb", "nba", "unclassified", None)
_PRICE_FORMS = ("[1, 0]", "[0, 1]", [1.0, 0.0], [0.0, 1.0], None, "[]", "[1]")


def make_specs(n_rows: int, seed: int, n_assets: int | None = None) -> list[tuple]:
    """Compact per-row parameters (small ints only). Timestamps are
    non-decreasing in row order, as ORDER BY t.ts, t.id guarantees,
    with ties so the id tiebreak is exercised."""
    rng = random.Random(seed)
    n_assets = n_assets or max(1, n_rows // 7)
    specs = []
    ts = 0
    for _ in range(n_rows):
        ts += rng.choice((0, 0, 1, 7, 61))
        specs.append((
            rng.randrange(n_assets),           # asset
            rng.random() < 0.7,                # BUY else SELL
            rng.randrange(1, 40000),           # size (x0.01)
            rng.randrange(1, 100),             # price (x0.01)
            ts,                                # seconds after _T0
            rng.random() < 0.85,               # markets row joined
            rng.random() < 0.75,               # resolved
            rng.randrange(len(_PRICE_FORMS)),  # resolved_prices form
            rng.randrange(len(_SPORTS)),       # m_sport
            rng.randrange(len(_SPORTS)),       # t_sport
            rng.randrange(len(_OUTCOMES)),     # outcome
            rng.choice((-1, 0, 0, 0, 1, 1, 1, 2)),  # outcome_index (-1 -> None)
            rng.random() < 0.8,                # resolved_at present
        ))
    return specs


def expand(spec: tuple, i: int) -> dict:
    """One row as the query returns it, every string a new object."""
    (a, buy, size, price, ts, joined, resolved, pf, ms, tsp, oc, oi,
     has_rat) = spec
    outcome = _OUTCOMES[oc].format(a=a)
    t_title = f"Player {a} vs. Player {a + 1} O/U 22.5" if oc in (2, 3) \
        else f"Will Player {a} beat Player {a + 1}?"
    return {
        "asset": f"0x{a:064x}",
        "condition_id": f"0x{a * 7:064x}",
        "outcome": outcome,
        "outcome_index": None if oi < 0 else oi,
        "side": "BUY" if buy else "SELL",
        "size": size / 100.0,
        "price": price / 100.0,
        "notional": size * price / 10000.0,
        "t_sport": _SPORTS[tsp],
        "ts": _T0 + timedelta(seconds=ts),
        "t_title": t_title,
        "m_title": (t_title.replace("Player", "P") if joined else None),
        "event_title": f"Player {a} vs. Player {a + 1}" if joined else None,
        "m_sport": _SPORTS[ms] if joined else None,
        "resolved": resolved if joined else None,
        "resolved_prices": _PRICE_FORMS[pf] if joined else None,
        "resolved_at": (_T0 + timedelta(seconds=ts + 3600)
                        if joined and has_rat else None),
    }


def materialize(specs: list[tuple]) -> list[dict]:
    return [expand(s, i) for i, s in enumerate(specs)]


# ── a pool whose cursor hands out the ledger in chunks and counts ──────


class _Cursor:
    def __init__(self, pool, specs, delay):
        self.pool, self.specs, self.delay, self.i = pool, specs, delay, 0

    async def fetch(self, n):
        self.pool.fetch_sizes.append(n)
        if self.delay:
            await asyncio.sleep(self.delay)
        stop = min(self.i + n, len(self.specs))
        rows = [expand(self.specs[j], j) for j in range(self.i, stop)]
        self.i = stop
        return rows


class _Txn:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Con:
    def __init__(self, pool):
        self.pool = pool

    def transaction(self):
        return _Txn()

    async def cursor(self, sql, *args):
        self.pool.cursor_opens += 1
        self.pool.cursor_calls.append((sql, args))
        return _Cursor(self.pool, self.pool.ledgers[args[0]], self.pool.delay)


class _Acquire:
    def __init__(self, pool):
        self.pool = pool

    async def __aenter__(self):
        self.pool.acquired += 1
        return _Con(self.pool)

    async def __aexit__(self, *exc):
        self.pool.released += 1
        return False


class FakePool:
    def __init__(self, ledgers: dict[int, list[tuple]], delay: float = 0.0):
        self.ledgers = ledgers
        self.delay = delay
        self.cursor_opens = 0
        self.cursor_calls: list = []
        self.fetch_sizes: list[int] = []
        self.batch_fetches = 0
        self.acquired = self.released = 0

    def acquire(self):
        return _Acquire(self)

    async def fetch(self, sql, *args):
        self.batch_fetches += 1
        return materialize(self.ledgers[args[0]])


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(reports, "_bets_cache", {})
    monkeypatch.setattr(reports, "_bets_locks", {})
    monkeypatch.delenv("WHALE_DAY_STREAM", raising=False)


def _use(monkeypatch, pool):
    async def _gp():
        return pool
    monkeypatch.setattr(reports, "get_pool", _gp)
    return pool


# ── equivalence ────────────────────────────────────────────────────────


class TestTheStreamMatchesTheOldBody:
    @pytest.mark.parametrize("seed", [1, 2, 3, 4])
    def test_dict_for_dict_over_a_randomized_ledger(self, monkeypatch, seed):
        # 23,456 rows: four full chunks and a partial fifth.
        specs = make_specs(23_456, seed)
        pool = _use(monkeypatch, FakePool({1: specs}))
        out = asyncio.run(reports.settled_bets(1))
        oracle = settled_bets_batch(materialize(specs))
        assert out == oracle
        assert len(out) > 100, "the fixture must settle something"
        assert pool.cursor_opens == 1 and pool.batch_fetches == 0
        assert pool.acquired == pool.released == 1

    def test_every_settlement_branch_is_exercised_by_the_fixture(self):
        bets = settled_bets_batch(materialize(make_specs(23_456, 1)))
        results = {b["result"] for b in bets}
        assert {"Win", "Loss", "Cash-out (profit)", "Cash-out (loss)"} <= results
        assert {b["bet_type"] for b in bets} >= {"Total", "Moneyline"}
        assert any(b["sport"] is None for b in bets)
        assert any(b["odds"] == "—" for b in bets)

    def test_the_same_sql_and_argument_reach_the_cursor(self, monkeypatch):
        pool = _use(monkeypatch, FakePool({7: make_specs(500, 9)}))
        asyncio.run(reports.settled_bets(7))
        (sql, args), = pool.cursor_calls
        assert args == (7,)
        assert "FROM trades t LEFT JOIN markets m USING (condition_id)" in sql
        assert "WHERE t.whale_id = $1" in sql
        assert "ORDER BY t.ts, t.id" in sql

    def test_an_empty_ledger_is_an_empty_list(self, monkeypatch):
        pool = _use(monkeypatch, FakePool({3: []}))
        assert asyncio.run(reports.settled_bets(3)) == []
        assert pool.cursor_opens == 1

    def test_the_kill_switch_keeps_the_batch_fetch_and_the_answer(
            self, monkeypatch):
        specs = make_specs(12_000, 5)
        pool = _use(monkeypatch, FakePool({1: specs}))
        monkeypatch.setenv("WHALE_DAY_STREAM", "0")
        out = asyncio.run(reports.settled_bets(1))
        assert out == settled_bets_batch(materialize(specs))
        assert pool.batch_fetches == 1 and pool.cursor_opens == 0


# ── the cache, by cursor-open counts ───────────────────────────────────


class TestOneLedgerReadPerWhale:
    def test_a_second_call_within_the_ttl_opens_no_cursor(self, monkeypatch):
        pool = _use(monkeypatch, FakePool({1: make_specs(6_000, 11)}))
        first = asyncio.run(reports.settled_bets(1))
        second = asyncio.run(reports.settled_bets(1))
        assert pool.cursor_opens == 1
        assert first == second
        # Callers get their own list: a caller sorting or trimming it
        # cannot rewrite what the next caller reads.
        assert first is not second
        first.clear()
        assert asyncio.run(reports.settled_bets(1)) == second

    def test_an_expired_entry_is_rebuilt(self, monkeypatch):
        pool = _use(monkeypatch, FakePool({1: make_specs(6_000, 12)}))
        asyncio.run(reports.settled_bets(1))
        stamp, bets = reports._bets_cache[1]
        reports._bets_cache[1] = (stamp - reports._BETS_TTL - 1, bets)
        asyncio.run(reports.settled_bets(1))
        assert pool.cursor_opens == 2

    def test_concurrent_callers_share_one_flight(self, monkeypatch):
        # The probe's shape: seven day calls for one whale in flight at
        # once. The cursor yields between chunks so the calls overlap.
        specs = make_specs(15_000, 13)
        pool = _use(monkeypatch, FakePool({1: specs}, delay=0.001))

        async def burst():
            return await asyncio.gather(*(reports.settled_bets(1)
                                          for _ in range(7)))

        outs = asyncio.run(burst())
        assert pool.cursor_opens == 1
        oracle = settled_bets_batch(materialize(specs))
        assert all(o == oracle for o in outs)

    def test_at_most_three_whales_resident_the_oldest_evicted(
            self, monkeypatch):
        pool = _use(monkeypatch, FakePool({w: make_specs(2_000, 20 + w)
                                           for w in (1, 2, 3, 4)}))
        for w in (1, 2, 3, 4):
            asyncio.run(reports.settled_bets(w))
            assert len(reports._bets_cache) <= reports._BETS_MAX_WHALES == 3
        assert set(reports._bets_cache) == {2, 3, 4}
        assert pool.cursor_opens == 4
        # The evicted whale reads its ledger again; the next-oldest goes.
        asyncio.run(reports.settled_bets(1))
        assert pool.cursor_opens == 5
        assert set(reports._bets_cache) == {3, 4, 1}
        # Locks do not outlive their whale's cache entry.
        assert set(reports._bets_locks) <= set(reports._bets_cache)

    def test_a_failed_read_caches_nothing_and_releases_the_lock(
            self, monkeypatch):
        class _Boom(FakePool):
            async def fetch(self, sql, *args):
                raise RuntimeError("no")

        pool = _use(monkeypatch, _Boom({1: make_specs(100, 1)}))

        class _BadCursor(_Cursor):
            async def fetch(self, n):
                raise RuntimeError("connection reset")

        monkeypatch.setattr(
            _Con, "cursor",
            lambda self, sql, *a: _bad(self, sql, *a))

        async def _bad(con, sql, *a):
            con.pool.cursor_opens += 1
            return _BadCursor(con.pool, [], 0)

        with pytest.raises(RuntimeError):
            asyncio.run(reports.settled_bets(1))
        assert 1 not in reports._bets_cache
        assert pool.released == pool.acquired == 1
        assert not any(lk.locked() for lk in reports._bets_locks.values())
        # The lock goes with the failure (hotfix review): the prune in
        # _remember never ran, and the ids come off the URL.
        assert 1 not in reports._bets_locks

    def test_a_db_outage_under_an_id_scan_leaves_no_locks_behind(
            self, monkeypatch):
        """100 distinct whale ids, every read failing, as a DB outage
        under the probe's whale loop looks: the lock table must end
        empty, not at 100."""
        class _Down:
            def acquire(self):
                raise ConnectionError("pool down")

            async def fetch(self, sql, *args):
                raise ConnectionError("pool down")

        _use(monkeypatch, _Down())
        for w in range(1, 101):
            with pytest.raises(ConnectionError):
                asyncio.run(reports.settled_bets(w))
        assert reports._bets_locks == {}
        assert reports._bets_cache == {}

    def test_a_waiter_behind_a_failed_leader_keeps_its_lock_object(
            self, monkeypatch):
        """The leader's failure drops the table entry; a waiter already
        queued on that Lock finishes its own flight on the object it
        holds, and its success re-enters the cache."""
        specs = make_specs(300, 2)
        pool = _use(monkeypatch, FakePool({1: specs}, delay=0.001))
        fails = [True]

        async def cursor(con, sql, *a):
            con.pool.cursor_opens += 1
            if fails.pop(0) if fails else False:
                raise RuntimeError("connection reset")
            return _Cursor(con.pool, con.pool.ledgers[a[0]], con.pool.delay)

        monkeypatch.setattr(_Con, "cursor", cursor)

        async def scenario():
            return await asyncio.gather(reports.settled_bets(1),
                                        reports.settled_bets(1),
                                        return_exceptions=True)

        a, b = asyncio.run(scenario())
        assert isinstance(a, RuntimeError)
        assert b == settled_bets_batch(materialize(specs))
        assert pool.cursor_opens == 2
        assert 1 in reports._bets_cache
        assert not any(lk.locked() for lk in reports._bets_locks.values())


# ── the bound ──────────────────────────────────────────────────────────


class TestTheBound:
    def test_no_fetch_above_the_chunk(self, monkeypatch):
        pool = _use(monkeypatch, FakePool({1: make_specs(27_000, 31)}))
        asyncio.run(reports.settled_bets(1))
        assert pool.fetch_sizes and max(pool.fetch_sizes) <= 5000
        assert reports._BETS_CHUNK == 5000
        src = inspect.getsource(reports._replay_ledger)
        assert "asyncio.wait_for(cur.fetch(_BETS_CHUNK)" in src
        assert src.count("await pool.fetch(") == 1, \
            "the whole-ledger fetch exists only behind the kill-switch"
        assert src.index("if not _stream_enabled()") < src.index("await pool.fetch(")

    def test_meta_is_ten_scalars_not_the_record(self, monkeypatch):
        specs = make_specs(300, 32)
        by_asset = asyncio.run(reports._replay_ledger(FakePool({1: specs}), 1))
        for agg in by_asset.values():
            assert isinstance(agg.meta, tuple) and len(agg.meta) == 10
        assert reports._Agg.__slots__

    def test_200k_rows_peak_under_60_mb(self, monkeypatch):
        specs = make_specs(200_000, 40)
        pool = _use(monkeypatch, FakePool({1: specs}))
        gc.collect()
        tracemalloc.start()
        try:
            bets = asyncio.run(reports.settled_bets(1))
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert len(bets) > 5_000, "the fixture must settle a real share"
        assert peak < 60 * 2**20, f"peak {peak / 2**20:.1f} MB"
        assert pool.cursor_opens == 1 and len(pool.fetch_sizes) == 41

    def test_the_loop_yields_between_chunks(self, monkeypatch):
        """A heartbeat task must get scheduled during the read: the old
        body held the loop for one synchronous replay (4.8 s at 889k
        rows), which is what turned a memory burst into unanswered
        health checks."""
        pool = _use(monkeypatch, FakePool({1: make_specs(60_000, 41)}))
        beats = 0

        async def heartbeat():
            nonlocal beats
            while True:
                await asyncio.sleep(0)
                beats += 1

        async def scenario():
            hb = asyncio.create_task(heartbeat())
            try:
                await reports.settled_bets(1)
            finally:
                hb.cancel()

        asyncio.run(scenario())
        assert beats >= len(pool.fetch_sizes)
