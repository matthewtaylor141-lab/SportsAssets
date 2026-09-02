"""His edge, split into selection and timing -- and the marks that split it.

Owner question 2026-09-01 (evening): can the whales' selection be learned
from their history? Only if some of the edge survives with the timing
removed. These tests pin the instrument:

  * a whale whose picks are fair at the pre-game price but cheap at his
    fill reads TIMING; one whose picks still pay pre-game reads
    SELECTION SURVIVES; too few games reads NOT DEMONSTRATED
  * each leg drops rows whose mark is missing, never zero-fills
  * payout is the venue's resolution for the outcome HE bought
  * a mark is a reading AT t (inside tolerance), never the first reading
    after a gap; pre-game marks exist only for pre-game buys
  * the worker marks each buy once, paces, backs off, never raises, and
    never touches an order
"""
import asyncio

import pytest

from sportsassets.analytics import decompose as D
from sportsassets.workers import edge_marks as W


def _row(price, payout, p_pre=None, p_10m=None, size=100.0, key=None,
         p_5m=None, p_60m=None):
    return {"size": size, "price": price, "payout": payout, "p_pre": p_pre,
            "p_5m": p_5m, "p_10m": p_10m, "p_60m": p_60m, "event_key": key}


def _book(n, fill, later, pre, win_rate):
    """n buys on n games: he pays `fill`, the market is `later` 10 min on
    and `pre` before the game; he wins `win_rate` of them."""
    return [_row(fill, 1.0 if (i / n) < win_rate else 0.0, p_pre=pre,
                 p_10m=later, p_5m=later, key=f"g{i}") for i in range(n)]


# ------------------------------------------------------------ the legs

def test_at_fill_is_his_real_edge_and_a_mark_moves_only_its_own_leg():
    rows = _book(40, fill=0.50, later=0.55, pre=0.55, win_rate=0.55)
    out = D.score(rows)
    assert out["legs"]["at_fill"]["roi"] == pytest.approx(0.10, abs=1e-6)
    assert out["legs"]["at_10m"]["roi"] == pytest.approx(0.0, abs=1e-6)
    assert out["legs"]["pre_game"]["roi"] == pytest.approx(0.0, abs=1e-6)
    assert out["legs"]["at_60m"]["n"] == 0            # no p_60m marks


def test_a_missing_mark_is_dropped_from_that_leg_not_zero_filled():
    rows = _book(40, 0.50, 0.55, 0.55, 0.55)
    rows[0]["p_pre"] = None
    out = D.score(rows)
    assert out["legs"]["pre_game"]["n"] == 39
    assert out["legs"]["at_fill"]["n"] == 40


def test_three_legs_of_one_match_are_one_game():
    rows = [_row(0.5, 1.0, p_pre=0.5, key="one-match") for _ in range(40)]
    assert D.score(rows)["legs"]["at_fill"]["clusters"] == 1


# --------------------------------------------------------- the reading

def test_timing_reads_as_timing():
    """He wins 55% at a fill of 0.45; by game time the price is 0.55:
    fair. Everything he earns is WHEN."""
    rows = _book(200, fill=0.45, later=0.55, pre=0.55, win_rate=0.55)
    out = D.score(rows)
    assert out["legs"]["at_fill"]["ci95"][0] > 0
    assert out["reading"].startswith("TIMING")
    assert out["timing_share"] == pytest.approx(1.0, abs=1e-6)


def test_selection_survives_when_the_pre_game_price_still_pays():
    """He wins 70% of buys priced 0.55 at game time: the pick is right
    at a fair price."""
    rows = _book(200, fill=0.50, later=0.55, pre=0.55, win_rate=0.70)
    out = D.score(rows)
    assert out["legs"]["pre_game"]["ci95"][0] > 0
    assert out["reading"].startswith("SELECTION SURVIVES")
    assert 0.0 < out["timing_share"] < 1.0


def test_too_few_games_is_not_demonstrated_whatever_the_point_estimate():
    rows = _book(10, 0.50, 0.55, 0.55, 0.90)
    out = D.score(rows)
    assert out["reading"].startswith("NOT DEMONSTRATED")
    assert out["legs"]["pre_game"]["verdict"].startswith("PROVISIONAL")


def test_anti_selection_is_named():
    rows = _book(200, fill=0.40, later=0.60, pre=0.60, win_rate=0.50)
    out = D.score(rows)
    assert out["reading"].startswith("ANTI-SELECTION")


# ---------------------------------------------------------- the payout

def test_payout_is_the_resolution_for_the_outcome_he_bought():
    assert D.payout_of([1, 0], 0) == 1.0
    assert D.payout_of([1, 0], 1) == 0.0
    assert D.payout_of("[0.5, 0.5]", 1) == 0.5
    assert D.payout_of([1, 0], 2) is None
    assert D.payout_of(None, 0) is None
    assert D.payout_of("not json", 0) is None


# ------------------------------------------------------------ the marks

_SERIES = [(1000.0 + 60 * i, 0.50 + 0.001 * i) for i in range(120)]  # 1/min, 2h


def test_a_mark_is_the_first_point_at_or_after_the_target():
    assert W.mark_at(_SERIES, 1000.0 + 300, 1) == pytest.approx(0.505)
    assert W.mark_at(_SERIES, 1000.0 + 301, 1) == pytest.approx(0.506)


def test_a_mark_beyond_the_tolerance_is_none_not_the_next_reading():
    gappy = [(1000.0, 0.50), (1000.0 + 3600, 0.90)]
    assert W.mark_at(gappy, 1000.0 + 300, 1) is None
    assert W.mark_before(gappy, 1000.0 + 3000, 1) is None


def test_pre_game_marks_exist_only_for_pre_game_buys():
    gs = 1000.0 + 5400          # game starts 90 min in
    m = W.marks_for(1000.0 + 600, _SERIES, gs, 1)
    assert m["p_pre"] == pytest.approx(W.mark_before(_SERIES, gs - 60, 1))
    assert m["p_5m"] == pytest.approx(0.515)
    m = W.marks_for(gs + 60, _SERIES, gs, 1)          # in-game buy
    assert m["p_pre"] is None
    m = W.marks_for(1000.0 + 600, _SERIES, None, 1)   # unknown start
    assert m["p_pre"] is None


def test_fidelity_is_fine_for_short_spans():
    assert W.fidelity_for(3600) == 1
    assert W.fidelity_for(2 * 86400) == 5
    assert W.fidelity_for(10 * 86400) == 15


# ---------------------------------------------------------- the worker

class _Row(dict):
    def keys(self):
        return list(super().keys())


class _Pool:
    def __init__(self, tokens, trades, pick_raises=False):
        self.tokens, self.trades = tokens, trades
        self.pick_raises = pick_raises
        self.execs: list[tuple] = []
        self.fetches: list[tuple] = []

    async def fetch(self, sql, *a):
        self.fetches.append((sql, a))
        if "GROUP BY t.asset" in sql:
            if self.pick_raises:
                raise RuntimeError("relation trade_marks does not exist")
            return self.tokens
        return self.trades

    async def fetchrow(self, sql, *a):
        return _Row(game_start=None, err=None)     # start cached as unknown

    async def execute(self, sql, *a):
        self.execs.append((sql, a))


@pytest.fixture
def worker(monkeypatch):
    slept: list[float] = []

    async def _sleep(s):
        slept.append(s)

    monkeypatch.setattr(W, "_sleep", _sleep)
    monkeypatch.setattr(W, "_backoff_until", 0.0)
    monkeypatch.setattr(W, "fetch_history",
                        lambda tok, s, e, f: list(_SERIES))
    return slept


def _tok(asset="tok-1", first=1000.0, last=1600.0):
    return _Row(asset=asset, condition_id="c1", first_ts=first, last_ts=last, n=2)


_NOW = 1000.0 + W.MIN_AGE_S + 600.0        # the buys are old enough


def _ins(pool):
    return [q for q in pool.execs if "INSERT INTO trade_marks" in q[0]]


def test_it_marks_every_unmarked_buy_on_a_token_once(worker):
    pool = _Pool([_tok()], [_Row(id=1, ts=1000.0), _Row(id=2, ts=1600.0)])
    n = asyncio.run(W.run_once(pool, now_ts=_NOW))
    assert n == 2
    ins = _ins(pool)
    assert len(ins) == 2 and all("ON CONFLICT (trade_id) DO NOTHING" in q[0] for q in ins)
    assert ins[0][1][1] == pytest.approx(0.505)          # p_5m for the first buy
    assert ins[0][1][7] is None                          # no err on a real mark


def test_a_buy_too_young_for_its_marks_is_not_picked(worker):
    """ROUND FOUR: newest-first with no age floor stamped every fresh
    buy with four NULLs within a minute of the fill, permanently. The
    floor is the last offset plus the coarsest tolerance."""
    pool = _Pool([_tok()], [_Row(id=1, ts=1000.0)])
    asyncio.run(W.run_once(pool, now_ts=_NOW))
    assert W.MIN_AGE_S >= 3600.0 + 2 * 15 * 60
    # both queries bind the floor as $4, and it is the module constant
    pick = next(a for s, a in pool.fetches if "GROUP BY t.asset" in s)
    per_token = next(a for s, a in pool.fetches if "t.asset = $1" in s)
    assert pick[3] == W.MIN_AGE_S and per_token[3] == W.MIN_AGE_S
    import inspect
    for fn in (W._pick_tokens, W.mark_token):
        assert "t.ts <= now() - make_interval(secs => $4)" in inspect.getsource(fn)


def test_a_game_that_has_not_started_defers_the_token(worker, monkeypatch):
    """p_pre cannot exist before the game; nothing is written and the
    token is skipped until the start has passed."""
    class _P(_Pool):
        async def fetchrow(self, sql, *a):
            return _Row(game_start=__import__("datetime").datetime.fromtimestamp(
                _NOW + 7200, tz=__import__("datetime").timezone.utc), err=None,
                fetched_ts=_NOW)
    monkeypatch.setattr(W, "_skip_until", {})
    pool = _P([_tok()], [_Row(id=1, ts=1000.0)])
    assert asyncio.run(W.run_once(pool, now_ts=_NOW)) == 0
    assert _ins(pool) == []
    assert W._skip_until["tok-1"] >= _NOW + 7200


def test_no_series_is_retried_later_not_written_as_nulls(worker, monkeypatch):
    monkeypatch.setattr(W, "fetch_history", lambda tok, s, e, f: [])
    monkeypatch.setattr(W, "_skip_until", {})
    pool = _Pool([_tok()], [_Row(id=1, ts=1000.0)])
    assert asyncio.run(W.run_once(pool, now_ts=_NOW)) == 0
    assert _ins(pool) == []
    assert W._skip_until["tok-1"] == pytest.approx(_NOW + W.RETRY_S)
    # and the token is not re-read while skipped
    reads = []
    monkeypatch.setattr(W, "fetch_history", lambda tok, s, e, f: reads.append(tok) or [])
    asyncio.run(W.run_once(pool, now_ts=_NOW + 10))
    assert reads == []


def test_a_failed_start_fetch_defers_rather_than_nulling_p_pre_forever(worker, monkeypatch):
    class _P(_Pool):
        async def fetchrow(self, sql, *a):
            return None                       # nothing cached
    def _boom(cid):
        raise RuntimeError("timeout")
    monkeypatch.setattr(W, "fetch_game_start", _boom)
    monkeypatch.setattr(W, "_skip_until", {})
    pool = _P([_tok()], [_Row(id=1, ts=1000.0)])
    assert asyncio.run(W.run_once(pool, now_ts=_NOW)) == 0
    assert _ins(pool) == []
    assert W._skip_until["tok-1"] == pytest.approx(_NOW + W.START_RETRY_S)


def test_it_paces_between_tokens_and_caps_the_tick(worker, monkeypatch):
    monkeypatch.setattr(W, "_skip_until", {})
    pool = _Pool([_tok(f"tok-{i}") for i in range(3)], [_Row(id=1, ts=1000.0)])
    asyncio.run(W.run_once(pool, now_ts=_NOW))
    assert worker == [W.PACING_S, W.PACING_S]
    assert W.TOKENS_PER_TICK <= 10


def test_a_venue_failure_backs_off_instead_of_writing_or_retrying(worker, monkeypatch):
    def _boom(*a):
        raise RuntimeError("venue down")
    monkeypatch.setattr(W, "fetch_history", _boom)
    monkeypatch.setattr(W, "_skip_until", {})
    pool = _Pool([_tok()], [_Row(id=1, ts=1000.0)])
    assert asyncio.run(W.run_once(pool, now_ts=_NOW)) == 0
    assert _ins(pool) == []
    calls = []
    monkeypatch.setattr(W, "fetch_history", lambda *a: calls.append(a) or list(_SERIES))
    assert asyncio.run(W.run_once(pool, now_ts=_NOW + 1.0)) == 0   # inside the backoff
    assert calls == []
    assert asyncio.run(W.run_once(pool, now_ts=_NOW + W.BACKOFF_S + 1.0)) == 1


def test_a_missing_table_never_raises(worker):
    assert asyncio.run(W.run_once(_Pool([], [], pick_raises=True), now_ts=_NOW)) == 0


def test_the_pick_query_skips_marked_buys_and_binds_the_window():
    import inspect

    src = inspect.getsource(W._pick_tokens)
    assert "tm.trade_id IS NULL" in src and "t.side = 'BUY'" in src
    assert "make_interval(days => $2)" in src


def test_the_venue_window_fallback_filters_client_side():
    """If the venue ignores startTs/endTs the worker re-asks with the
    named interval and windows the answer itself."""
    assert W._interval_for(1800) == "1h" and W._interval_for(5 * 86400) == "1w"
    pts = W._points({"history": [{"t": 5, "p": 0.4}, {"t": "x"}, {"t": 1, "p": 0.3}]})
    assert pts == [(1.0, 0.3), (5.0, 0.4)]


def test_the_worker_never_touches_an_order():
    import inspect

    src = inspect.getsource(W).lower()
    for verb in ("submit_fok", "cancel_order", "close_position",
                 "live_orders", "orders.create"):
        assert verb not in src


def test_the_cohort_query_is_resolved_buys_keyed_by_game():
    class _Q:
        def __init__(self):
            self.sqls = []

        async def fetch(self, sql, *a):
            self.sqls.append((sql, a))
            return []

        async def fetchrow(self, sql, *a):
            self.sqls.append((sql, a))
            return None

    q = _Q()
    out = asyncio.run(D.cohort_decompose(q, "RN1", 30))
    sql = q.sqls[0][0]
    assert "t.side = 'BUY'" in sql and "COALESCE(m.resolved, false) = true" in sql
    assert "trade_marks" in sql and "event_slug" in sql
    assert q.sqls[0][1] == ("rn1", 30)
    assert out["whale"] == "rn1" and out["reading"].startswith("NOT DEMONSTRATED")


def test_migration_044_ships_with_the_code():
    import pathlib

    root = pathlib.Path(W.__file__).resolve().parents[2]
    body = (root / "migrations" / "044_trade_marks.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS trade_marks" in body
    assert "CREATE TABLE IF NOT EXISTS market_starts" in body


def test_the_worker_is_registered_and_the_endpoint_is_admin_gated():
    import ast
    import inspect
    import sys
    import types

    sys.modules.setdefault("pywebpush", types.SimpleNamespace(
        webpush=None, WebPushException=Exception))
    from sportsassets.api import app as app_mod
    from sportsassets.workers import all as all_mod

    assert "edge_marks" in [n for n, _ in all_mod.LOOPS]
    tree = ast.parse(inspect.getsource(app_mod.admin_edge_decomposition))
    node = tree.body[0]
    assert any(isinstance(d, ast.Call) and any(k.arg == "dependencies"
                                                for k in d.keywords)
               for d in node.decorator_list)
