"""Reconciler walk — feed-ordering enforcement (fleet round 21).

The corroboration sweep's span testimony (`cov -> oldest`) was a bare
min() over usable rows: an inference sound only under an UNCHECKED
newest-first premise. One genuinely valid late-indexed OLD row inside
the walked window — a durable feed property, served identically to
every hourly walk, so the round-20 two-run rule could not decorrelate
it — set oldest below an S1 fill sitting BELOW the depth cap: false
span coverage, permanent false STICKY on a correct emission.

These tests drive the REAL reconcile_once walk against a crafted feed
server and pin both halves of the fix: (a) an interior misordered row
dirties the walk at its successor; (b) a monotone-consistent misorder
at the exact cap tail — the round-21 kill shape, invisible from
inside the capped walk — is caught by the border-witness page fetched
past the cap, whose rows verify ordering but never testify for span.
"""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

import sportsassets.ingestion.reconciler as rec

WALLET = "0x" + "ab" * 20
# newest row's venue timestamp — near wallclock, because a row past
# the walk's own clock is feed corruption and dirties the walk (r40)
TOP_TS = int(time.time()) - 3600
STEP = 10                         # seconds between adjacent fills


def _raw(ts: int, i: int) -> dict:
    return {
        "transactionHash": "0x" + format(i + 1, "064x"),
        "asset": str(10_000 + i),
        "side": "BUY",
        "size": 10.0,
        "price": 0.5,
        "timestamp": ts,
    }


def _feed(n: int = 700) -> list[dict]:
    """A genuine newest-first feed: strictly descending timestamps."""
    return [_raw(TOP_TS - i * STEP, i) for i in range(n)]


class _Resp:
    def __init__(self, page):
        self._page = page

    def raise_for_status(self):
        pass

    def json(self):
        return self._page


class _FakePool:
    def __init__(self):
        self.details = None

    async def fetchval(self, sql, *a, timeout=None):
        if "abs(extract(epoch" in sql:
            return None                           # no prior recording
        return 1                                  # run_id

    async def fetch(self, sql, *a, timeout=None):
        return [{"id": 7, "address": WALLET, "username": "w"}]

    async def execute(self, sql, *a, timeout=None):
        if "reconciliation_runs SET" in sql:
            self.details = json.loads(a[2])


def _wire(monkeypatch, feed):
    """Wire reconcile_once to the crafted feed; return (pool, calls)."""
    pool = _FakePool()
    calls: list[int] = []

    async def fake_get(http, path, params=None):
        calls.append(int(params["offset"]))
        return _Resp(feed[params["offset"]:params["offset"] + 100])

    async def fake_pool():
        return pool

    async def fake_hb(*a, **k):
        return None

    async def fake_sport(cond):
        return None

    async def fake_ingest(ev, notify=True):
        return (1, False)

    monkeypatch.setattr(rec, "polite_get", fake_get)
    monkeypatch.setattr(rec, "get_pool", fake_pool)
    monkeypatch.setattr(rec, "heartbeat", fake_hb)
    monkeypatch.setattr(rec, "_sport_for_condition", fake_sport)
    monkeypatch.setattr(rec, "ingest_trade_result", fake_ingest)
    monkeypatch.setattr(
        rec, "settings",
        lambda: SimpleNamespace(data_api_base="http://feed.test"))
    return pool, calls


def _cov(pool):
    return pool.details["per_wallet"]["cov:" + WALLET]


def test_clean_deep_feed_fetches_a_border_page_and_stays_clean(
        monkeypatch):
    """The control: a genuinely ordered deep feed still produces a
    clean span claim (no false-defer regression), the border page IS
    fetched past the cap, and border rows never extend oldest — the
    testimony stops at the last in-cap page's reach."""
    pool, calls = _wire(monkeypatch, _feed())
    out = asyncio.run(rec.reconcile_once(depth=500))
    cov = _cov(pool)
    assert out["missed"] == 0
    assert cov["dirty"] == 0, "an ordered feed claims clean coverage"
    assert cov["complete"] is False
    assert any(c >= 500 for c in calls), \
        "a clean walk at the cap fetches the border-witness page"
    # last testifying page starts at offset 485 and reaches row 584;
    # the border page's rows (585+) must NOT deepen the testimony
    assert cov["oldest"] == float(TOP_TS - 584 * STEP), \
        "span testimony comes only from rows with verified successors"
    # fleet r38 (major): coverage claimed only a lower bound — a
    # frozen index's walk "covered" a fill it never served and the
    # sweep sticky-tripped a CORRECT emission. The walk now testifies
    # to the newest row it served; SQL_RECON_SINCE requires the fill
    # INSIDE the span (executed semantics: test_s1_sql_real_pg).
    assert cov["newest"] == float(TOP_TS), \
        "the walk testifies to its upper bound too"


def test_interior_late_indexed_old_row_dirties_the_walk(monkeypatch):
    """r21 kill, interior variant: one VALID row (passes every field
    of key_fields_valid) carrying a far-old timestamp sits inside the
    walked window. Pre-fix it silently set oldest and faked span
    coverage with dirty=0. Now its SUCCESSOR row — newer than the
    misplaced row by more than ORDER_TOL_S — proves the newest-first
    premise broken: the walk is dirty, the claim refused, and the
    border page is not even fetched (nothing left to protect)."""
    feed = _feed()
    feed[250]["timestamp"] = 1_500_000_000        # valid, just ancient
    pool, calls = _wire(monkeypatch, feed)
    asyncio.run(rec.reconcile_once(depth=500))
    cov = _cov(pool)
    assert cov["dirty"] >= 1, \
        "a misordered feed can never hand out clean span coverage"
    assert max(calls) < 500, "a dirty walk skips the border page"


def test_cap_tail_misorder_is_caught_by_the_border_page(monkeypatch):
    """THE round-21 kill shape: a monotone-consistent run of late-
    indexed old rows at the exact cap tail. Inside the capped walk
    every adjacent pair descends — no ordering check can fire — and
    oldest drops to the misplaced rows' ancient timestamps: durable
    false span coverage over an S1 fill sitting just below the cap,
    identical in BOTH hourly walks (the two-run rule never
    decorrelates a stable feed position). The border page makes the
    invisible visible: the genuine feed resumes NEWER right below
    the cap, the first border row violates ordering against the
    misplaced tail, and the walk dirties — pure deferral."""
    feed = _feed()
    for j, pos in enumerate(range(580, 585)):
        feed[pos]["timestamp"] = 1_500_000_050 - j * STEP
    pool, calls = _wire(monkeypatch, feed)
    asyncio.run(rec.reconcile_once(depth=500))
    cov = _cov(pool)
    assert any(c >= 500 for c in calls), \
        "the in-cap walk is clean — only the border page can see it"
    assert cov["dirty"] >= 1, \
        "the border page catches the cap-tail misorder"


def test_ordering_pins_in_source():
    """Structural pins: the tolerance constant, the ordering check,
    the testify gate on span testimony, and the dirty-skips-border
    loop bound (all round 21)."""
    import inspect

    src = inspect.getsource(rec)
    assert rec.ORDER_TOL_S <= 300, \
        "the ordering tolerance stays tight — jitter, not reordering"
    assert rec.BORDER_PAGE >= 100, "a full venue page past the cap"
    assert "ts_r > ord_floor + ORDER_TOL_S" in src, \
        "a row newer than the walk's running floor dirties the walk"
    assert "min(ord_floor, ts_r)" in src, \
        "r42: the floor is CUMULATIVE — an ascending re-index ramp " \
        "can never climb back to trajectory in tolerance-sized steps"
    assert "testify and ev.ts_epoch" in src, \
        "border rows are witnesses, never span testimony"
    assert "depth if dirty else depth + BORDER_PAGE" in src, \
        "clean walks verify one page past the cap; dirty walks skip"
    assert "testify = offset < depth" in src


def test_total_loss_run_heartbeats_error_not_ok(monkeypatch):
    """r27 (minor): a run in which EVERY wallet's walk failed used to
    heartbeat 'ok' {missed: 0} — the backstop carrier 100% dead,
    indistinguishable on the ops row from a flawless run (status was
    derived solely from missed). Total loss now says 'error', and
    partial failure rides the detail."""
    pool = _FakePool()
    beats: list[tuple] = []

    async def failing_get(http, path, params=None):
        raise RuntimeError("venue refused")

    async def fake_pool():
        return pool

    async def fake_hb(name, status, detail=None):
        beats.append((name, status, detail))

    monkeypatch.setattr(rec, "polite_get", failing_get)
    monkeypatch.setattr(rec, "get_pool", fake_pool)
    monkeypatch.setattr(rec, "heartbeat", fake_hb)
    monkeypatch.setattr(
        rec, "settings",
        lambda: SimpleNamespace(data_api_base="http://feed.test"))
    asyncio.run(rec.reconcile_once(depth=500))
    assert beats and beats[-1][1] == "error", \
        "a dead backstop must never wear a green heartbeat"
    assert beats[-1][2] == {"missed": 0, "failed": 1}

    # the healthy control keeps its ok, now with failed=0 visible
    pool2, calls = _wire(monkeypatch, _feed())
    monkeypatch.setattr(rec, "heartbeat", fake_hb)
    asyncio.run(rec.reconcile_once(depth=500))
    assert beats[-1][1] == "ok" and beats[-1][2]["failed"] == 0


def test_future_dated_head_row_dirties_the_walk(monkeypatch):
    """fleet r40 (major): a valid future-dated HEAD row (no
    predecessor, so ORDER_TOL_S could never fire) kept the walk
    dirty=0 while its bare-max ts inflated cov->newest above every
    fill — and tracking wallclock across two hourly walks it forged
    the round-39 distinct-newest testimony. A future row is feed
    corruption: the walk dirties, and a dirty walk never testifies."""
    rows = _feed()
    future = dict(rows[0])
    future["timestamp"] = int(time.time()) + 7200
    future["transactionHash"] = "0x" + "fd" * 32
    pool, _calls = _wire(monkeypatch, [future] + rows)
    asyncio.run(rec.reconcile_once(depth=500))
    cov = _cov(pool)
    assert cov["dirty"] >= 1, \
        "a future-dated row is corruption, not testimony"
    assert cov["newest"] <= time.time() + rec.FUTURE_SKEW_S, \
        "the forged head never reaches the newest testimony"


def test_wallclock_tracking_phantom_head_dirties_via_mutation(monkeypatch):
    """fleet r41 (major): a PRESENT-dated phantom head (inside
    FUTURE_SKEW_S, no predecessor for ORDER_TOL_S) tracked each
    walk's wallclock and forged two DISTINCT newest values over a
    frozen feed. Its unforgeable-by-accident signature: the same
    (tx, asset) re-served with a MUTATING timestamp — and our own
    trades table remembers the earlier ingest. The newest-testimony
    row found already recorded > ORDER_TOL_S away dirties the walk."""
    rows = _feed()
    phantom = dict(rows[0])
    phantom["timestamp"] = int(time.time()) - 30      # present-dated
    phantom["transactionHash"] = "0x" + "fe" * 32
    pool, _calls = _wire(monkeypatch, [phantom] + rows)

    async def mutant_fetchval(sql, *a, timeout=None):
        if "abs(extract(epoch" in sql:
            # the same tx+asset was ingested an hour ago at an
            # earlier ts — the phantom's previous wallclock stamp
            return 1
        return 1                                       # run_id

    pool.fetchval = mutant_fetchval
    asyncio.run(rec.reconcile_once(depth=500))
    cov = _cov(pool)
    assert cov["dirty"] >= 1, \
        "a mutating head is corruption, not distinct-newest testimony"


def test_fresh_head_row_is_not_a_mutant(monkeypatch):
    """A genuinely new head fill has no prior recording — the mutant
    probe finds nothing and the clean walk still testifies."""
    pool, _calls = _wire(monkeypatch, _feed())
    asyncio.run(rec.reconcile_once(depth=500))
    cov = _cov(pool)
    assert cov["dirty"] == 0 and cov["newest"] == float(TOP_TS)


def test_ascending_reindex_batch_dirties_the_walk(monkeypatch):
    """fleet r42 (executed kill, design round): the adjacent-only
    ordering check let an ascending re-index ramp — a 7000s drop then
    steps each under ORDER_TOL_S — climb back to trajectory with
    dirty=0 while cov->oldest sank far below the walk's real reach:
    forged span over fills the walk never served. The CUMULATIVE
    floor makes any climb above the walk's running minimum dirty."""
    feed = _feed()
    for j in range(60):
        feed[50 + j]["timestamp"] = int(TOP_TS - 7000 + j * 110)
    pool, _calls = _wire(monkeypatch, feed)
    asyncio.run(rec.reconcile_once(depth=500))
    cov = _cov(pool)
    assert cov["dirty"] >= 1, \
        "a ramp climbing in tolerance-sized steps is still inversion"


# ------------------------------------- the walk's statements, pinned
#
# To-a-tee Phase 8 (owner order 2026-09-02) added a taker census to
# this walk behind a fail-closed switch. The rule for that unit was
# that the walk's OWN requests and statements never change: this is
# the golden recorded on the unmodified reconciler on the fixture
# above (5 statements, 7 requests), pinned so any later edit to the
# walk's cycle length or its writes fails here by name.

MUTANT_PROBE_SQL = (
    "SELECT 1 FROM trades WHERE lower(tx_hash) = lower($1) AND asset = $2 "
    "AND source = 'poll' AND abs(extract(epoch from ts)::float8 - $3) > $4 LIMIT 1")


class _StatementPool(_FakePool):
    """The walk fixture's pool, recording every statement with args."""

    def __init__(self):
        super().__init__()
        self.stmts: list[tuple] = []

    async def fetchval(self, sql, *a, timeout=None):
        self.stmts.append(("fetchval", sql, list(a)))
        return await super().fetchval(sql, *a, timeout=timeout)

    async def fetch(self, sql, *a, timeout=None):
        self.stmts.append(("fetch", sql, list(a)))
        return await super().fetch(sql, *a, timeout=timeout)

    async def execute(self, sql, *a, timeout=None):
        self.stmts.append(("execute", sql, list(a)))
        return await super().execute(sql, *a, timeout=timeout)


def test_the_walks_statements_and_requests_are_the_recorded_golden(monkeypatch):
    pool = _StatementPool()
    calls: list[dict] = []
    beats: list[tuple] = []
    feed = _feed()

    async def fake_get(http, path, params=None):
        calls.append((path, dict(params)))
        return _Resp(feed[params["offset"]:params["offset"] + 100])

    async def fake_pool():
        return pool

    async def fake_hb(name, status, detail=None):
        beats.append((name, status, detail))

    async def fake_sport(cond):
        return None

    async def fake_ingest(ev, notify=True):
        return (1, False)

    monkeypatch.setattr(rec, "polite_get", fake_get)
    monkeypatch.setattr(rec, "get_pool", fake_pool)
    monkeypatch.setattr(rec, "heartbeat", fake_hb)
    monkeypatch.setattr(rec, "_sport_for_condition", fake_sport)
    monkeypatch.setattr(rec, "ingest_trade_result", fake_ingest)
    monkeypatch.setattr(
        rec, "settings",
        lambda: SimpleNamespace(data_api_base="http://feed.test"))
    out = asyncio.run(rec.reconcile_once(depth=500))
    cov = {"complete": False, "oldest": float(TOP_TS - 584 * STEP),
           "newest": float(TOP_TS), "dirty": 0}
    assert out == {"run_id": 1, "missed": 0,
                   "per_wallet": {"cov:" + WALLET: cov, WALLET: 0}}
    assert pool.stmts == [
        ("fetchval", "INSERT INTO reconciliation_runs DEFAULT VALUES RETURNING id", []),
        ("fetch", "SELECT id, address, username FROM whales WHERE active AND NOT banned", []),
        ("fetchval", MUTANT_PROBE_SQL,
         ["0x" + format(1, "064x"), "10000", float(TOP_TS), 120.0]),
        ("execute",
         "UPDATE reconciliation_runs SET finished_at=now(), missed=$2, details=$3::jsonb WHERE id=$1",
         [1, 0, json.dumps({"per_wallet": {"cov:" + WALLET: cov, WALLET: 0}})]),
    ]
    assert beats == [("reconciler", "ok", {"missed": 0, "failed": 0})]
    assert calls == [("/trades", {"user": WALLET, "limit": 100, "offset": o,
                                  "takerOnly": "false"})
                     for o in (0, 97, 194, 291, 388, 485, 582)]
