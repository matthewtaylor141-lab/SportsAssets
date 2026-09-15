"""rebuild_positions retains O(positions), and the rollups did not move.

The per-fill lists that OOM-killed the workers at their 2 GiB limit
(2026-09-05 17:59:41Z, four minutes into the full-ledger replay) are gone;
each state now carries a few unboxed scalars per rollup window. Two things
are pinned here:

1. compute_rollups over the new states produces the rows the OLD retention
   produced. The oracle is the old logic reimplemented inline exactly as
   engine.py had it at 4ad58a3 — a Realization per realizing fill, a
   (ts, notional) tuple per BUY, resolutions realized at resolved_at, then
   compute_rollups filtering the lists by cutoff — run over the same
   synthetic ledger through the same Position math.

2. What a state holds does not depend on how many fills it has seen.
"""

import asyncio
import dataclasses
import math
import random
import sys
from array import array
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest

from sportsassets.analytics import engine
from sportsassets.analytics.engine import WINDOWS, compute_rollups
from sportsassets.analytics.positions import EPS, Fill, Position, market_result

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
CUT7 = NOW - timedelta(days=7)
CUT30 = NOW - timedelta(days=30)
SPORTS = ("NFL", "NBA", "MLB", "NHL")
SINGLE_LEG_WHALES = {1, 2, 3, 4}  # never hold both tokens of one market
HEDGING_WHALES = {5, 6}


# ───────────────────────────── the synthetic ledger ─────────────────────────────

def _tok(m: int, idx: int) -> str:
    return f"tk{m:03d}-{idx}"


def _cid(m: int) -> str:
    return f"0xc{m:03d}"


def _sport(m: int) -> str:
    return SPORTS[m % len(SPORTS)]


def build_ledger() -> tuple[list[dict], dict[str, tuple[list[float], datetime]]]:
    """A few thousand fills over 6 whales × ~80 markets × 4 sports, in
    (ts, id) order like the cursor yields them, plus the resolutions."""
    rnd = random.Random(20260905)
    rows: list[dict] = []
    resolutions: dict[str, tuple[list[float], datetime]] = {}

    def add(ts, whale, m, idx, side, size, price, *, sport=None, cid="auto",
            outcome_index="auto"):
        rows.append({
            "whale_id": whale, "asset": _tok(m, idx),
            "condition_id": _cid(m) if cid == "auto" else cid,
            "outcome": "Yes" if idx == 0 else "No",
            "outcome_index": idx if outcome_index == "auto" else outcome_index,
            "side": side, "size": float(size), "price": float(price),
            "notional": float(size) * float(price),
            "sport": sport or _sport(m), "ts": ts,
        })

    def rand_ts(days_back_max=400, days_forward=0.05):
        span = (days_back_max + days_forward) * 86400
        return NOW - timedelta(days=days_back_max) + timedelta(seconds=rnd.random() * span)

    def burst(whale, m, idx, n, *, t_lo=None, t_hi=None, buy_p=0.6):
        for _ in range(n):
            ts = rand_ts() if t_lo is None else t_lo + timedelta(
                seconds=rnd.random() * (t_hi - t_lo).total_seconds())
            side = "BUY" if rnd.random() < buy_p else "SELL"
            add(ts, whale, m, idx, side, rnd.uniform(1, 300), rnd.uniform(0.05, 0.95))

    # Bulk: 60 markets; every whale trades ~25 of them; hedgers take both legs half the time.
    for whale in sorted(SINGLE_LEG_WHALES | HEDGING_WHALES):
        for m in rnd.sample(range(60), 25):
            legs = [rnd.randrange(2)]
            if whale in HEDGING_WHALES and rnd.random() < 0.5:
                legs = [0, 1]
            for idx in legs:
                burst(whale, m, idx, rnd.randrange(5, 30))
    for m in range(60):
        if rnd.random() < 0.6:
            win = rnd.randrange(2)
            resolutions[_cid(m)] = ([1.0, 0.0] if win == 0 else [0.0, 1.0],
                                    rand_ts(days_back_max=100, days_forward=2))

    # Edge cases, one market each (60+).
    # 60: BUY exactly at the 30d cutoff, SELL exactly at the 7d cutoff; unresolved.
    add(CUT30, 1, 60, 0, "BUY", 100, 0.40)
    add(CUT7, 1, 60, 0, "SELL", 100, 0.70)
    # 61: fully sold before a recent resolution — resolving realizes nothing.
    add(NOW - timedelta(days=50), 1, 61, 1, "BUY", 80, 0.30)
    add(NOW - timedelta(days=40), 1, 61, 1, "SELL", 80, 0.55)
    resolutions[_cid(61)] = ([0.0, 1.0], NOW - timedelta(days=2))
    # 62: only BUYs, last one exactly at the 30d cutoff; never resolved.
    add(NOW - timedelta(days=90), 2, 62, 0, "BUY", 10, 0.5)
    add(CUT30, 2, 62, 0, "BUY", 20, 0.6)
    # 63: unresolved market with a sell inside 7d.
    add(NOW - timedelta(days=20), 3, 63, 0, "BUY", 200, 0.45)
    add(NOW - timedelta(days=3), 3, 63, 0, "SELL", 100, 0.65)
    # 64: first row un-enriched (no condition_id, unclassified), later rows enriched.
    add(NOW - timedelta(days=12), 4, 64, 0, "BUY", 50, 0.5, cid=None, sport="unclassified")
    add(NOW - timedelta(days=11), 4, 64, 0, "BUY", 50, 0.6, sport="NHL")
    add(NOW - timedelta(days=4), 4, 64, 0, "SELL", 60, 0.8, sport="NHL")
    resolutions[_cid(64)] = ([1.0, 0.0], NOW - timedelta(days=1))
    # 65: condition_id never known — grouped as token:<id>.
    add(NOW - timedelta(days=9), 4, 65, 1, "BUY", 30, 0.2, cid=None, sport="NBA")
    add(NOW - timedelta(days=8), 4, 65, 1, "SELL", 10, 0.3, cid=None, sport="NBA")
    # 66: hedged package resolved inside 7d.
    add(NOW - timedelta(days=10), 5, 66, 0, "BUY", 1000, 0.60)
    add(NOW - timedelta(days=10, hours=1), 5, 66, 1, "BUY", 500, 0.35)
    resolutions[_cid(66)] = ([1.0, 0.0], NOW - timedelta(days=3))
    # 67: hedged, but the resolution prices list is short — the No leg stays open.
    add(NOW - timedelta(days=6), 6, 67, 0, "BUY", 100, 0.5)
    add(NOW - timedelta(days=6), 6, 67, 1, "BUY", 100, 0.5)
    resolutions[_cid(67)] = ([1.0], NOW - timedelta(days=2))
    # 68/69: resolved exactly at the 30d / 7d cutoff.
    add(NOW - timedelta(days=35), 2, 68, 0, "BUY", 100, 0.5)
    resolutions[_cid(68)] = ([1.0, 0.0], CUT30)
    add(NOW - timedelta(days=9), 3, 69, 1, "BUY", 100, 0.5)
    resolutions[_cid(69)] = ([1.0, 0.0], CUT7)
    # 70: resolved 45d ago per the market, but a sell landed 5d ago (data quirk).
    add(NOW - timedelta(days=60), 2, 70, 0, "BUY", 200, 0.4)
    add(NOW - timedelta(days=5), 2, 70, 0, "SELL", 50, 0.9)
    resolutions[_cid(70)] = ([1.0, 0.0], NOW - timedelta(days=45))
    # 71: resolved between the 7d and 30d cutoffs.  72: resolved in the future.
    add(NOW - timedelta(days=20), 4, 71, 0, "BUY", 100, 0.5)
    resolutions[_cid(71)] = ([0.0, 1.0], NOW - timedelta(days=15))
    add(NOW - timedelta(days=1), 1, 72, 0, "BUY", 100, 0.5)
    resolutions[_cid(72)] = ([1.0, 0.0], NOW + timedelta(days=1))
    # 73: a fill stamped after `now` (clock skew).
    add(NOW + timedelta(minutes=30), 3, 73, 0, "BUY", 10, 0.5)
    # 74: sells beyond inventory (untracked), before any buy and after.
    add(NOW - timedelta(days=8), 2, 74, 1, "SELL", 50, 0.5)
    add(NOW - timedelta(days=7, hours=12), 2, 74, 1, "BUY", 40, 0.4)
    add(NOW - timedelta(days=2), 2, 74, 1, "SELL", 100, 0.7)
    # 75: outcome_index unknown on a resolved market — left open.
    add(NOW - timedelta(days=3), 6, 75, 0, "BUY", 100, 0.5, outcome_index=None)
    resolutions[_cid(75)] = ([1.0, 0.0], NOW - timedelta(days=1))
    # 76: void-style resolution at 0.5/0.5.
    add(NOW - timedelta(days=4), 5, 76, 0, "BUY", 100, 0.5)
    add(NOW - timedelta(days=4), 5, 76, 1, "BUY", 100, 0.5)
    resolutions[_cid(76)] = ([0.5, 0.5], NOW - timedelta(days=1))
    # 77: a round trip at cost — dust, no realization event; then resolved.
    add(NOW - timedelta(days=3), 1, 77, 0, "BUY", 100, 0.5)
    add(NOW - timedelta(days=2), 1, 77, 0, "SELL", 100, 0.5)
    resolutions[_cid(77)] = ([1.0, 0.0], NOW - timedelta(days=1))
    # 78: a sport that never gets classified.
    add(NOW - timedelta(days=2), 3, 78, 0, "BUY", 10, 0.5, sport="unclassified")
    add(NOW - timedelta(days=1), 3, 78, 0, "SELL", 10, 0.9, sport="unclassified")
    # 79: a perfectly hedged package resolved yesterday — +50 and -50, a scratch in 7d.
    add(NOW - timedelta(days=5), 5, 79, 0, "BUY", 100, 0.5)
    add(NOW - timedelta(days=5), 5, 79, 1, "BUY", 100, 0.5)
    resolutions[_cid(79)] = ([1.0, 0.0], NOW - timedelta(days=1))

    rows.sort(key=lambda r: r["ts"])  # stable: ties keep insertion order, like (ts, id)
    return rows, resolutions


# ───────────────────── the oracle: engine.py at 4ad58a3, inline ─────────────────────

@dataclass
class _Realization:
    ts: datetime
    amount: float


@dataclass
class _OldState:
    whale_id: int
    condition_id: str | None
    token_id: str
    outcome: str | None
    outcome_index: int | None
    sport: str
    position: Position
    realizations: list[_Realization] = field(default_factory=list)
    buys: list[tuple[datetime, float]] = field(default_factory=list)
    first_ts: datetime | None = None
    last_ts: datetime | None = None


def _old_rebuild(rows, resolutions) -> list[_OldState]:
    """rebuild_positions before 2026-09-05: per-fill lists, minus the DB."""
    states: dict[tuple[int, str], _OldState] = {}
    for t in rows:
        key = (t["whale_id"], t["asset"])
        st = states.get(key)
        if st is None:
            st = states[key] = _OldState(
                whale_id=t["whale_id"], condition_id=t["condition_id"], token_id=t["asset"],
                outcome=t["outcome"], outcome_index=t["outcome_index"], sport=t["sport"],
                position=Position())
        st.condition_id = st.condition_id or t["condition_id"]
        st.outcome = st.outcome or t["outcome"]
        st.outcome_index = st.outcome_index if st.outcome_index is not None else t["outcome_index"]
        if st.sport == "unclassified" and t["sport"] != "unclassified":
            st.sport = t["sport"]
        before = st.position.realized_pnl
        st.position.apply(Fill(side=t["side"], size=t["size"], price=t["price"]))
        delta = st.position.realized_pnl - before
        if abs(delta) > EPS:
            st.realizations.append(_Realization(ts=t["ts"], amount=delta))
        if t["side"] == "BUY":
            st.buys.append((t["ts"], t["notional"]))
        st.first_ts = st.first_ts or t["ts"]
        st.last_ts = t["ts"]
    for st in states.values():
        if st.condition_id and st.condition_id in resolutions and not st.position.resolved:
            prices, resolved_at = resolutions[st.condition_id]
            idx = st.outcome_index if st.outcome_index is not None else -1
            if 0 <= idx < len(prices):
                before = st.position.realized_pnl
                st.position.resolve(float(prices[idx]))
                delta = st.position.realized_pnl - before
                if abs(delta) > EPS:
                    st.realizations.append(_Realization(ts=resolved_at, amount=delta))
    return list(states.values())


def _old_compute_rollups(states, now) -> list[dict]:
    """compute_rollups before 2026-09-05, verbatim."""
    by_market: dict[tuple[int, str, str], list[_OldState]] = defaultdict(list)
    for st in states:
        cid = st.condition_id or f"token:{st.token_id}"
        by_market[(st.whale_id, st.sport, cid)].append(st)

    rows: dict[tuple[int, str, str], dict] = {}
    for (whale_id, sport, _cid), legs in by_market.items():
        for window, span in WINDOWS.items():
            cutoff = now - span if span else None
            realized = sum(
                r.amount for st in legs for r in st.realizations if cutoff is None or r.ts >= cutoff
            )
            events_in_window = any(
                (cutoff is None or r.ts >= cutoff) for st in legs for r in st.realizations
            )
            traded_in_window = any(
                st.last_ts and (cutoff is None or st.last_ts >= cutoff) for st in legs
            )
            if not events_in_window and not traded_in_window:
                continue
            notional = sum(
                n for st in legs for (ts, n) in st.buys if cutoff is None or ts >= cutoff
            )
            open_exposure = sum(st.position.open_exposure for st in legs)
            fully_resolved = all(st.position.resolved for st in legs)
            key = (whale_id, sport, window)
            agg = rows.setdefault(key, {
                "whale_id": whale_id, "sport": sport, "window": window,
                "markets_traded": 0, "wins": 0, "losses": 0, "scratches": 0,
                "realized_pnl": 0.0, "notional": 0.0, "open_exposure": 0.0,
            })
            agg["markets_traded"] += 1
            agg["realized_pnl"] += realized
            agg["notional"] += notional
            agg["open_exposure"] += open_exposure
            if fully_resolved and events_in_window:
                result = market_result(realized)
                agg["wins" if result == "win" else "losses" if result == "loss" else "scratches"] += 1

    out = []
    for agg in rows.values():
        settled = agg["wins"] + agg["losses"]
        agg["win_pct"] = round(agg["wins"] / settled, 4) if settled else None
        agg["roi"] = round(agg["realized_pnl"] / agg["notional"], 6) if agg["notional"] > 0 else None
        agg["avg_position"] = (
            round(agg["notional"] / agg["markets_traded"], 6) if agg["markets_traded"] else None
        )
        out.append(agg)
    return out


# ───────────────────── the new path: the REAL rebuild over a fake pool ─────────────────────

class _Cursor:
    def __init__(self, rows):
        self._it = iter(rows)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration from None


class _Ctx:
    def __init__(self, obj=None):
        self.obj = obj

    async def __aenter__(self):
        return self.obj

    async def __aexit__(self, *exc):
        return False


class _Conn:
    def __init__(self, rows):
        self.rows = rows
        self.prefetch = None

    def transaction(self):
        return _Ctx(self)

    def cursor(self, sql, *args, prefetch=None):
        assert "ORDER BY ts, id" in sql
        self.prefetch = prefetch
        return _Cursor(self.rows)


class _Pool:
    def __init__(self, rows):
        self.conn = _Conn(rows)

    def acquire(self):
        return _Ctx(self.conn)


def _rebuild_new(monkeypatch, rows, resolutions, now=NOW):
    pool = _Pool(rows)
    persisted = []

    async def _get_pool():
        return pool

    async def _load_resolutions():
        return dict(resolutions)

    async def _persist(states):
        persisted.append(len(states))

    monkeypatch.setattr(engine, "get_pool", _get_pool)
    monkeypatch.setattr(engine, "_load_resolutions", _load_resolutions)
    monkeypatch.setattr(engine, "_persist_positions", _persist)
    states = asyncio.run(engine.rebuild_positions(now=now))
    assert persisted == [len(states)] and pool.conn.prefetch == 5_000
    return states


INT_FIELDS = ("markets_traded", "wins", "losses", "scratches")
PRE_ROUNDED = ("win_pct", "roi", "avg_position")  # rounded inside compute_rollups
RAW = ("realized_pnl", "notional", "open_exposure")  # persist_rollups rounds these to 6


def _key(r):
    return (r["whale_id"], r["sport"], r["window"])


def _as_persisted(r):
    """The row as persist_rollups writes it."""
    return ({f: r[f] for f in INT_FIELDS + PRE_ROUNDED}
            | {f: round(r[f], 6) for f in RAW})


# ───────────────────────────────────── tests ─────────────────────────────────────

def test_ledger_exercises_the_edges():
    rows, resolutions = build_ledger()
    assert 2_000 <= len(rows) <= 8_000
    assert [r["ts"] for r in rows] == sorted(r["ts"] for r in rows)
    ts = {r["ts"] for r in rows}
    assert CUT7 in ts and CUT30 in ts and any(t > NOW for t in ts)
    assert {r_at for _, r_at in resolutions.values()} >= {CUT7, CUT30}
    assert any(r["condition_id"] is None for r in rows)
    assert any(r["sport"] == "unclassified" for r in rows)
    old = _old_compute_rollups(_old_rebuild(rows, resolutions), NOW)
    for window in WINDOWS:
        wr = [r for r in old if r["window"] == window]
        assert sum(r["wins"] for r in wr) and sum(r["losses"] for r in wr)
        assert any(r["scratches"] for r in wr), window
        assert any(r["open_exposure"] > 0 for r in wr)
    assert any(r["sport"] == "unclassified" for r in old)


def test_rollups_identical_to_the_per_event_oracle(monkeypatch):
    rows, resolutions = build_ledger()
    old_states = _old_rebuild(rows, resolutions)
    old = {_key(r): r for r in _old_compute_rollups(old_states, NOW)}

    new_states = _rebuild_new(monkeypatch, rows, resolutions)
    assert all(st.as_of == NOW for st in new_states)
    assert len(new_states) == len(old_states)
    assert [(s.whale_id, s.token_id, s.condition_id, s.sport, s.outcome_index)
            for s in new_states] == \
           [(s.whale_id, s.token_id, s.condition_id, s.sport, s.outcome_index)
            for s in old_states]
    assert [round(s.position.realized_pnl, 9) for s in new_states] == \
           [round(s.position.realized_pnl, 9) for s in old_states]
    new = {_key(r): r for r in compute_rollups(new_states, now=NOW)}

    assert new.keys() == old.keys()
    max_diff = 0.0
    for k in old:
        o, n = old[k], new[k]
        assert _as_persisted(n) == _as_persisted(o), k
        for f in RAW:
            assert math.isclose(o[f], n[f], rel_tol=1e-12, abs_tol=1e-9), (k, f, o[f], n[f])
            max_diff = max(max_diff, abs(o[f] - n[f]))
    # Every window has settled markets on both sides, so W-L was really compared.
    for window in WINDOWS:
        assert sum(r["wins"] + r["losses"] for k, r in new.items() if k[2] == window) > 0

    # ON THE PRODUCTION INTERPRETER (python:3.12-slim) THE SINGLE-LEG ROWS ARE
    # THE SAME DOUBLES. builtin sum() is Neumaier-compensated from 3.12 and
    # the state's accumulator mirrors it, so a one-leg market's window sum
    # is the exact double the flat per-event sum produced; the aggregation
    # over markets runs in the same order. Hedged markets (whales 5-6) sum
    # per-leg partials and may differ in the last ulp — identical once
    # rounded, as asserted above for every row of THIS ledger (a decimal
    # tie at the 6th place, e.g. avg_position = notional/markets_traded,
    # can break either way; see WindowAgg). On 3.11 (CI) sum() is naive, so
    # the old figure carried more rounding error than the new one; only the
    # rounded identity holds there.
    if sys.version_info >= (3, 12):
        for k in old:
            if k[0] in SINGLE_LEG_WHALES:
                for f in RAW + PRE_ROUNDED:
                    assert old[k][f] == new[k][f], (k, f, old[k][f], new[k][f])
    else:
        assert max_diff < 1e-9


def test_a_state_footprint_does_not_grow_with_fills(monkeypatch):
    """Replay N fills into ONE position: the bytes the state owns are the
    same for N=10 and N=20,000, and nothing in it is a per-fill container."""

    def fills(n):
        rnd = random.Random(n)
        rows = []
        for i in range(n):
            size, price = rnd.uniform(1, 100), rnd.uniform(0.1, 0.9)
            rows.append({
                "whale_id": 7, "asset": "tk-one", "condition_id": "0xone", "outcome": "Yes",
                "outcome_index": 0, "side": "BUY" if rnd.random() < 0.5 else "SELL",
                "size": size, "price": price, "notional": size * price, "sport": "NFL",
                "ts": NOW - timedelta(days=400) + timedelta(seconds=i * 17)})
        return rows

    def footprint(obj, seen):
        if id(obj) in seen:
            return 0
        seen.add(id(obj))
        size = sys.getsizeof(obj)
        if dataclasses.is_dataclass(obj):
            for f in dataclasses.fields(obj):
                size += footprint(getattr(obj, f.name), seen)
        elif isinstance(obj, dict):
            for k, v in obj.items():
                size += footprint(k, seen) + footprint(v, seen)
        elif isinstance(obj, (list, tuple, set, frozenset)):
            for v in obj:
                size += footprint(v, seen)
        return size

    def containers(obj, seen):
        if id(obj) in seen:
            return
        seen.add(id(obj))
        if isinstance(obj, (list, tuple, set, frozenset, dict, array)):
            yield obj
        if dataclasses.is_dataclass(obj):
            for f in dataclasses.fields(obj):
                yield from containers(getattr(obj, f.name), seen)
        elif isinstance(obj, dict):
            for v in obj.values():
                yield from containers(v, seen)
        elif isinstance(obj, (list, tuple, set, frozenset)):
            for v in obj:
                yield from containers(v, seen)

    sizes = {}
    for n in (10, 1_000, 20_000):
        (st,) = _rebuild_new(monkeypatch, fills(n), {})
        assert st.position.fills == n
        assert not hasattr(st, "realizations") and not hasattr(st, "buys")
        # The only thing sized by the windows is the per-window scalar
        # array (a handful of doubles per window); no container anywhere in
        # the state is sized by anything else.
        for c in containers(st, set()):
            bound = engine._SLOTS_PER_WINDOW if isinstance(c, array) else 1
            assert len(c) <= bound * len(WINDOWS), (type(c), len(c))
        sizes[n] = footprint(st, set())
    assert len(set(sizes.values())) == 1, sizes
    assert sizes[10] < 2_048, sizes  # one position is a few hundred bytes, not a ledger


def test_run_cycle_hands_one_clock_to_replay_and_rollup(monkeypatch):
    """The buckets are cut at the replay's `now`; the rollup must be handed
    the SAME instant or it refuses (test_rollups). run_cycle is the only
    production caller, so this is where the clock is read once."""
    seen = {}

    async def fake_rebuild(now=None):
        seen["rebuild"] = now
        return []

    def fake_rollups(states, now=None):
        seen["rollup"] = now
        return []

    async def _none(*a, **k):
        return []

    async def _zero(*a, **k):
        return 0

    monkeypatch.setattr(engine, "rebuild_positions", fake_rebuild)
    monkeypatch.setattr(engine, "compute_rollups", fake_rollups)
    monkeypatch.setattr(engine, "persist_rollups", _none)
    monkeypatch.setattr(engine, "validate_against_leaderboard", _none)
    monkeypatch.setattr(engine, "settle_engine_fills", _zero)
    monkeypatch.setattr(engine, "settle_ai_trades", _zero)
    monkeypatch.setattr(engine, "pool_settle_live", _zero)
    out = asyncio.run(engine.run_cycle())
    assert seen["rebuild"] is not None and seen["rebuild"] is seen["rollup"]
    assert seen["rebuild"].tzinfo is timezone.utc
    assert out["positions"] == 0 and out["rollup_rows"] == 0


def test_rollup_over_replayed_states_refuses_another_clock(monkeypatch):
    rows, resolutions = build_ledger()
    states = _rebuild_new(monkeypatch, rows, resolutions)
    with pytest.raises(ValueError, match="bucketed"):
        compute_rollups(states, now=NOW + timedelta(seconds=1))
    assert compute_rollups(states) == compute_rollups(states, now=NOW)
