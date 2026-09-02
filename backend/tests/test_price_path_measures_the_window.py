"""The post-fill price curve, and the sampler that produces it.

The whale's edge minus the impact of capturing it rounds to zero. Whether
any fill rule beats that turns on one unmeasured curve: mean ask change
at t seconds after his fill. RISES -> take the ask now. REVERTS -> rest
at his price. Neither -> the order type is not the lever.

These tests pin what makes the curve trustworthy and the sampler safe:

  * t=0 is the baseline; every other offset is a difference from it
  * a missing offset is dropped from that offset, never zero-filled
  * an ask outside (0,1) is not a price and is dropped
  * the interval clusters by GAME and the verdict names the direction
  * a sample is a reading AT t: an offset not read inside its window is
    not read at all (round three: a gap collapsed six offsets into one
    instant and read as a flat curve)
  * reads are paced, capped per tick, and a run of misses backs off --
    the sampler shares the venue quota with the copy path's quote
  * the sampler reads only rostered whales' BUY attempts that carry a
    slug, never t=0 (the copy path records that), and never raises
"""
import asyncio

import pytest

from sportsassets.analytics import price_path as pp
from sportsassets.workers import price_path as w


def _s(row_id, t, ask, event_key=None):
    return {"row_id": row_id, "t_s": t, "ask": ask, "event_key": event_key}


# ------------------------------------------------------------ the curve

def test_t0_is_the_baseline_and_others_are_differences_in_cents():
    out = pp.path_curve([_s(1, 0, 0.50), _s(1, 30, 0.52), _s(1, 60, 0.49)])
    assert out["by_t"][0]["mean_cents"] == 0.0
    assert out["by_t"][30]["mean_cents"] == pytest.approx(2.0)
    assert out["by_t"][60]["mean_cents"] == pytest.approx(-1.0)


def test_a_missing_offset_is_dropped_not_zero_filled():
    """A pile of zeros is a flat curve by fiat."""
    out = pp.path_curve([_s(1, 0, 0.50), _s(1, 30, 0.53),
                         _s(2, 0, 0.40)])          # row 2 has no t=30
    assert out["by_t"][30]["n"] == 1
    assert out["by_t"][30]["mean_cents"] == pytest.approx(3.0)


def test_a_row_without_a_baseline_contributes_nothing():
    out = pp.path_curve([_s(1, 30, 0.53), _s(1, 60, 0.55)])
    assert out["by_t"][30]["n"] == 0


def test_an_ask_outside_the_unit_interval_is_not_a_price():
    out = pp.path_curve([_s(1, 0, 0.50), _s(1, 30, 1.0), _s(1, 60, 0.0),
                         _s(1, 120, None)])
    for t in (30, 60, 120):
        assert out["by_t"][t]["n"] == 0


def _curve(rows, key=None):
    return pp.path_curve([x for i, a0, a30 in rows
                          for x in (_s(i, 0, a0, key(i) if key else None),
                                    _s(i, 30, a30, key(i) if key else None))])


def test_the_interval_is_over_games_and_names_the_direction():
    rows = [(i, 0.50, 0.50 + 0.02 + 0.002 * (i % 3)) for i in range(36)]
    out = _curve(rows, key=lambda i: f"g{i}")
    b = out["by_t"][30]
    assert b["n"] == 36 and b["clusters"] == 36
    assert b["ci95_cents"][0] > 0
    assert b["verdict"].startswith("RISES")
    assert "RISES by t=30s" in out["reading"]


def test_under_thirty_games_a_direction_is_a_lean_not_a_rule():
    """First live read (2026-09-02): 19 rows on a handful of games printed
    'RISES at 95%' while every other verdict holds at 30 games."""
    rows = [(i, 0.50, 0.50 + 0.02 + 0.002 * (i % 3)) for i in range(12)]
    out = _curve(rows, key=lambda i: f"g{i}")
    b = out["by_t"][30]
    assert b["ci95_cents"][0] > 0                  # the interval does leave zero
    assert b["verdict"].startswith("PROVISIONAL (games<30) — leans RISES")
    assert out["reading"].startswith("PROVISIONAL: the curve leans RISES")
    assert "not a rule" in out["reading"]


def test_three_legs_of_one_match_are_one_game():
    """Twelve rows on one event are one result. No interval, no reading."""
    rows = [(i, 0.50, 0.52 + 0.002 * (i % 3)) for i in range(12)]
    out = _curve(rows, key=lambda i: "one-match")
    b = out["by_t"][30]
    assert b["clusters"] == 1
    assert b["ci95_cents"] is None
    assert "one game" in b["verdict"]
    assert "not the lever" in out["reading"]


def test_clustering_widens_the_interval_when_legs_move_together():
    """Same deltas, keyed four-per-game vs one-per-game: the clustered
    interval is wider (deff > 1), and reported."""
    rows = [(i, 0.50, 0.50 + (0.03 if (i // 4) % 2 else -0.01)) for i in range(16)]
    iid = _curve(rows, key=lambda i: f"g{i}")["by_t"][30]
    clu = _curve(rows, key=lambda i: f"game{i // 4}")["by_t"][30]
    assert clu["clusters"] == 4 and iid["clusters"] == 16
    assert clu["deff"] > 1.0
    assert (clu["ci95_cents"][1] - clu["ci95_cents"][0]) > \
        (iid["ci95_cents"][1] - iid["ci95_cents"][0])


def test_unkeyed_rows_are_their_own_clusters():
    rows = [(i, 0.50, 0.50 + 0.02 + 0.002 * (i % 3)) for i in range(12)]
    out = _curve(rows)
    assert out["by_t"][30]["clusters"] == 12


def test_a_reverting_curve_says_rest_at_his_price():
    rows = [(i, 0.50, 0.50 - 0.02 - 0.002 * (i % 3)) for i in range(36)]
    out = _curve(rows, key=lambda i: f"g{i}")
    assert out["by_t"][30]["verdict"].startswith("REVERTS")
    assert "resting at his price" in out["reading"]


def test_a_flat_curve_says_the_order_type_is_not_the_lever():
    rows = [(i, 0.50, 0.50 + (0.02 if i % 2 else -0.02)) for i in range(12)]
    out = _curve(rows, key=lambda i: f"g{i}")
    assert "not the lever" in out["reading"]


def test_one_row_cannot_carry_an_interval():
    out = pp.path_curve([_s(1, 0, 0.50), _s(1, 30, 0.55)])
    assert out["by_t"][30]["ci95_cents"] is None
    assert "INSUFFICIENT" in out["by_t"][30]["verdict"]


# ---------------------------------------------------------- the sampler

class _Row(dict):
    def keys(self):
        return list(super().keys())


class _Pool:
    def __init__(self, rows, insert_raises=False, fetch_raises=False):
        self.rows = rows
        self.insert_raises = insert_raises
        self.fetch_raises = fetch_raises
        self.inserted: list[tuple] = []
        self.fetch_args = None

    async def fetch(self, sql, *a):
        if self.fetch_raises:
            raise RuntimeError("relation price_path does not exist")
        self.fetch_args = (sql, a)
        return self.rows

    async def execute(self, sql, *a):
        if self.insert_raises:
            raise RuntimeError("relation price_path does not exist")
        self.inserted.append(a)


class _Pmus:
    def __init__(self, ask=0.51, raises=False):
        self.ask, self.raises = ask, raises
        self.reads: list[tuple] = []

    def side_ask(self, slug, intent):
        self.reads.append((slug, intent))
        if self.raises:
            raise RuntimeError("venue down")
        return self.ask


def _attempt(row_id=7, placed_ts=1000.0, taken=(), slug="aec-x-y-2026-09-01"):
    return _Row(row_id=row_id, slug=slug, placed_ts=placed_ts,
                intent="ORDER_INTENT_BUY_LONG", taken=list(taken))


@pytest.fixture
def sampler(monkeypatch):
    """Rostered rn1, no pacing sleep, no backoff carried between tests."""
    slept: list[float] = []

    async def _sleep(s):
        slept.append(s)

    monkeypatch.setattr(w, "exitable_whales", lambda: {"rn1"})
    monkeypatch.setattr(w, "_sleep", _sleep)
    # the process-wide measurement gate (venue_pace) never sleeps here;
    # `slept` records this worker's own pacing only
    monkeypatch.setattr(w, "pace", lambda s=w.READ_PACING_S: 0.0)
    monkeypatch.setattr(w, "_backoff_until", 0.0)
    return slept


def _run(pool, pmus, now_ts):
    return asyncio.run(w.sample_once(pool, pmus, now_ts=now_ts))


def test_it_takes_only_offsets_inside_their_window(sampler):
    pool = _Pool([_attempt(placed_ts=1000.0)])
    # at +65: 60's window [60, 70] is open; 30's [30, 40] has passed
    assert _run(pool, _Pmus(), 1000.0 + 65) == 1
    assert [t for _, t, _ in pool.inserted] == [60]


def test_a_gap_writes_nothing_rather_than_six_equal_asks(sampler):
    """THE ROUND-THREE REPRO. After a 650 s gap the first version wrote
    all six offsets in one tick with one ask: zero deltas everywhere,
    reading 'not the lever'. Now: nothing, and the row is simply not in
    the curve."""
    pool = _Pool([_attempt(placed_ts=1000.0)])
    p = _Pmus()
    assert _run(pool, p, 1000.0 + 650) == 0
    assert pool.inserted == [] and p.reads == []


def test_the_worker_never_takes_t0(sampler):
    """t=0 is the copy path's own pre-trade quote (the true baseline);
    the worker would only ever read it seconds late."""
    pool = _Pool([_attempt(placed_ts=1000.0)])
    assert _run(pool, _Pmus(), 1000.0 + 3) == 0
    assert 0 not in w.WORKER_OFFSETS and 0 in pp.OFFSETS_S


def test_the_copy_path_records_t0_from_its_own_quote():
    import inspect

    from sportsassets import live_executor as le

    src = inspect.getsource(le.maybe_execute)
    assert "INSERT INTO price_path (row_id, t_s, ask) VALUES ($1, 0, $2)" in src


def test_it_reads_the_ask_on_our_side_of_the_market(sampler):
    pool = _Pool([_attempt(placed_ts=1000.0)])
    p = _Pmus(ask=0.47)
    _run(pool, p, 1000.0 + 30)
    assert p.reads == [("aec-x-y-2026-09-01", "ORDER_INTENT_BUY_LONG")]
    assert pool.inserted == [(7, 30, 0.47)]


def test_a_dead_market_is_read_a_bounded_number_of_times(sampler):
    """One offset, a venue that never answers: at most one read per tick
    inside the window, none after it. The first version re-read every
    offset of every dead market for twelve minutes."""
    pool = _Pool([_attempt(placed_ts=1000.0)])
    p = _Pmus(raises=True)
    for tick in (1030.0, 1035.0, 1040.0, 1041.0, 1045.0, 1100.0):
        _run(pool, p, tick)
    assert len(p.reads) <= int(w.TOL_S / w.POLL_S) + 1
    assert pool.inserted == []


def test_reads_are_paced(sampler):
    pool = _Pool([_attempt(row_id=i, placed_ts=1000.0) for i in range(3)])
    _run(pool, _Pmus(), 1000.0 + 30)
    assert sampler == [w.READ_PACING_S, w.READ_PACING_S]
    assert w.READ_PACING_S >= 0.35


def test_the_per_tick_cap_binds(sampler):
    pool = _Pool([_attempt(row_id=i, placed_ts=1000.0) for i in range(25)])
    p = _Pmus()
    _run(pool, p, 1000.0 + 30)
    assert len(p.reads) == w.MAX_READS_PER_TICK


def test_a_miss_streak_abandons_the_tick_and_backs_off(sampler):
    pool = _Pool([_attempt(row_id=i, placed_ts=1000.0) for i in range(6)])
    p = _Pmus(raises=True)
    _run(pool, p, 1000.0 + 30)
    assert len(p.reads) == w.MISS_STREAK_ABANDON
    # inside the backoff: nothing is read at all
    _run(pool, p, 1000.0 + 31)
    assert len(p.reads) == w.MISS_STREAK_ABANDON
    # after it: reads resume (a later offset's window)
    _run(pool, p, 1000.0 + 31 + w.BACKOFF_S + 30)
    assert len(p.reads) > w.MISS_STREAK_ABANDON


def test_the_roster_is_bound_as_a_parameter(monkeypatch, sampler):
    monkeypatch.setattr(w, "exitable_whales", lambda: {"rn1", "homerunhazard"})
    pool = _Pool([])
    _run(pool, _Pmus(), 1000.0)
    sql, args = pool.fetch_args
    assert "ANY($2::text[])" in sql
    assert sorted(args[1]) == ["homerunhazard", "rn1"]


def test_the_due_query_threads_only_buy_attempts(sampler):
    """SELL rows (mirror_exit) and refusals without a raw carry a slug
    too; they were re-threaded every tick for twelve minutes."""
    pool = _Pool([])
    _run(pool, _Pmus(), 1000.0)
    sql, _ = pool.fetch_args
    assert "IN ('ORDER_INTENT_BUY_LONG'" in sql


def test_an_empty_roster_samples_nothing(monkeypatch, sampler):
    monkeypatch.setattr(w, "exitable_whales", lambda: set())
    pool = _Pool([_attempt()])
    assert _run(pool, _Pmus(), 1030.0) == 0
    assert pool.fetch_args is None


def test_a_venue_miss_skips_that_offset_only(sampler):
    pool = _Pool([_attempt(placed_ts=1000.0)])
    assert _run(pool, _Pmus(raises=True), 1000.0 + 35) == 0
    assert pool.inserted == []


def test_a_missing_table_never_raises(sampler):
    assert _run(_Pool([], fetch_raises=True), _Pmus(), 1030.0) == 0
    pool = _Pool([_attempt(placed_ts=1000.0)], insert_raises=True)
    assert _run(pool, _Pmus(), 1030.0) == 0


def test_the_insert_is_idempotent_by_primary_key():
    import inspect

    src = inspect.getsource(w._take)
    assert "ON CONFLICT (row_id, t_s) DO NOTHING" in src


def test_the_offsets_bracket_the_venue_publication_lag():
    assert 281 in pp.OFFSETS_S and 0 in pp.OFFSETS_S and max(pp.OFFSETS_S) >= 600


# ------------------------------------------------------------- the query

class _QPool:
    def __init__(self):
        self.sql, self.args = None, None

    async def fetch(self, sql, *a):
        self.sql, self.args = sql, a
        return []


def test_cohort_path_binds_the_whale_excludes_the_desk_and_keys_by_game():
    pool = _QPool()
    asyncio.run(pp.cohort_path(pool, "2026-09-01T00:00:00+00:00", whale="rn1"))
    assert "'manual', 'underdog'" in pool.sql
    assert "$2" in pool.sql and pool.args[1] == "rn1"
    assert "event_slug" in pool.sql and "market_tokens" in pool.sql


def test_migration_042_ships_with_the_code():
    import pathlib

    root = pathlib.Path(w.__file__).resolve().parents[2]
    body = (root / "migrations" / "042_price_path.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS price_path" in body
    assert "PRIMARY KEY (row_id, t_s)" in body
