"""L2 (2026-09-07): the copy sleeve's breaker, read over the mirror's window.

Owner 18:3xZ, twenty minutes after the L1 re-arm: "Turn the trip off
(reset it)." The 18:23:39Z tick refused 14 increases under
`loss_breaker` with no 'mirror_loss_stop' key standing: that census name
is the copy sleeve's own 24 h breaker (live_executor._loss_breaker_tripped,
PMUS_LOSS_BREAKER_USD), which sums EVERY live_orders row settled in a
fixed trailing 24 h -- the mirror's standing rows settle there too -- so
the morning's -5,000 tripped it for the rest of the day whatever
'mirror_loss_rearm' said. Two rails counted the same losses over two
windows, and the wider one won.

The rule: the mirror reads the sleeve's sum from the start ITS stop
uses (_loss_window_start off the re-arm key, read once per tick and
reused by _loss_stop). The copy lane's own call, its signature and its
24 h text are untouched. Driven through tests/test_mirror_live_worker's
fakes, with the sum helper replaced by a recorder that answers by the
window it was handed.
"""

from __future__ import annotations

import inspect
import logging
import pathlib
import re
from datetime import datetime, timezone

import pytest
import yaml

from sportsassets import live_executor as le
from sportsassets.workers import mirror_live as ml
from tests.test_l1_rearm_window import H24, TRIPPED, _loss_reads, _rearm, _reduce_world
from tests.test_mirror_live_worker import M, N, NOW, SLUG, _armed  # noqa: F401 — the fixture
from tests.test_mirror_live_worker import _census, _places, _pool, _tick, _Venue

RENDER_OPS = pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows" / "render-ops.yml"
SQL_24H = ("SELECT COALESCE(sum(pnl), 0) FROM live_orders "
           "WHERE settled_at > now() - interval '24 hours' "
           "AND status IN ('settled', 'cashed_out') "
           "AND COALESCE(whale_username, '') NOT IN "
           "('manual', 'underdog')")


class _Ledger:
    """A pool whose fetchval records (sql, args) and answers a value, or raises."""

    def __init__(self, value=None, boom=False):
        self.value, self.boom, self.calls = value, boom, []

    async def fetchval(self, sql, *a):
        self.calls.append((sql, a))
        if self.boom:
            raise RuntimeError("db blip")
        return self.value


def _recorder(monkeypatch, by_window):
    """Replace le._loss_breaker_sum with one that answers by the window
    it was handed: by_window maps None / an aware datetime to a sum (a
    missing window raises, so a wrong start cannot pass as the right one)."""
    seen = []

    async def _sum(pool, since=None):
        seen.append(since)
        if since not in by_window:
            raise AssertionError("unexpected window %r" % (since,))
        return by_window[since]
    monkeypatch.setattr(le, "_loss_breaker_sum", _sum)
    return seen


REARM_TS = ml._rearm_at(_rearm(), None)
REARM_DT = ml._utc(REARM_TS)


@pytest.fixture(autouse=True)
def _threshold(monkeypatch):
    monkeypatch.setattr(le, "PMUS_LOSS_BREAKER_USD", 5000.0)


# ------------------------------------------------- 1. the sum helper's texts

def test_l2_the_sum_helper_reads_24_h_with_no_window_and_the_parameter_with_one():
    import asyncio
    p = _Ledger(-12.5)
    assert asyncio.run(le._loss_breaker_sum(p)) == -12.5
    assert p.calls == [(SQL_24H, ())]
    since = datetime(2026, 9, 7, 18, 16, 6, tzinfo=timezone.utc)
    p = _Ledger(-100.0)
    assert asyncio.run(le._loss_breaker_sum(p, since)) == -100.0
    (sql, args), = p.calls
    assert args == (since,)
    assert "settled_at > $1::timestamptz" in sql and "interval" not in sql and "now()" not in sql
    assert sql.replace("settled_at > $1::timestamptz", "settled_at > now() - interval '24 hours'") == SQL_24H
    assert asyncio.run(le._loss_breaker_sum(_Ledger(None), since)) == 0.0, "an empty ledger sums to nothing"
    assert asyncio.run(le._loss_breaker_sum(_Ledger("-6000"), since)) == -6000.0
    assert asyncio.run(le._loss_breaker_sum(_Ledger(boom=True), since)) is None
    assert asyncio.run(le._loss_breaker_sum(_Ledger(boom=True))) is None


def test_l2_the_tripped_helper_is_the_copy_lanes_and_unchanged():
    """Signature ["pool"], the 24 h text, the truth table, the breaker
    line: the copy lane's call is not this change's."""
    import asyncio
    assert list(inspect.signature(le._loss_breaker_tripped).parameters) == ["pool"]
    src = inspect.getsource(le._loss_breaker_tripped)
    assert "_loss_breaker_sum(pool)" in src and "since=" not in src and "$1" not in src
    p = _Ledger(-5000.0)
    assert asyncio.run(le._loss_breaker_tripped(p)) is True and p.calls == [(SQL_24H, ())]
    assert asyncio.run(le._loss_breaker_tripped(_Ledger(-4999.99))) is False
    assert asyncio.run(le._loss_breaker_tripped(_Ledger(boom=True))) is None
    assert "_loss_breaker_tripped(pool)" in inspect.getsource(le.maybe_execute)
    assert "_loss_breaker_sum" not in inspect.getsource(le.maybe_execute)


# -------------------------------------------- 2. the mirror reads its window

def test_l2_the_re_armed_mirror_reads_the_sleeve_since_the_re_arm_and_opens(monkeypatch):
    """The 18:23Z shape: -6,000 over the sleeve's 24 h, -100 since the
    re-arm half an hour back. The tick reads the re-arm window, carries
    no loss_breaker, opens the candidate, and publishes the reading."""
    seen = _recorder(monkeypatch, {None: -6000.0, REARM_DT: -100.0})
    p = _pool()
    p.state["mirror_loss_rearm"] = _rearm()
    v = _Venue()
    st = _tick(p, v)
    assert seen == [REARM_DT]
    assert _census(st, "loss_breaker") == 0 and _census(st, "loss_breaker_unreadable") == 0
    assert [c[1] for c in _places(v)] == [SLUG]
    assert ml._last_sleeve == {"sum": -100.0, "limit": 5000.0, "since": ml._iso(REARM_TS)}
    assert ml._last_loss is not None and ml._last_loss["since"] == ml._iso(REARM_TS)


def test_l2_with_no_re_arm_the_full_window_trips_and_is_logged(monkeypatch, caplog):
    seen = _recorder(monkeypatch, {ml._utc(NOW - H24): -6000.0})
    p = _pool()
    v = _Venue()
    with caplog.at_level(logging.WARNING, logger=ml.log.name):
        st = _tick(p, v)
    assert seen == [ml._utc(NOW - H24)]
    assert _census(st, "loss_breaker") >= 1 and not _places(v)
    assert not _loss_reads(p), "the mirror's own sum is not read under the sleeve's block"
    assert ml._last_sleeve == {"sum": -6000.0, "limit": 5000.0, "since": ml._iso(NOW - H24)}
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("LOSS BREAKER")]
    assert lines == ["LOSS BREAKER: copy sleeve realized -6000.00 since %s (threshold -5000)"
                     " -- the mirror refuses increases" % ml._iso(NOW - H24)]


def test_l2_the_threshold_is_the_sleeves_own_at_the_edge(monkeypatch):
    _recorder(monkeypatch, {REARM_DT: -5000.0})
    p = _pool()
    p.state["mirror_loss_rearm"] = _rearm()
    assert _census(_tick(p, _Venue()), "loss_breaker") >= 1
    _recorder(monkeypatch, {REARM_DT: -4999.99})
    p = _pool()
    p.state["mirror_loss_rearm"] = _rearm()
    v = _Venue()
    assert _census(_tick(p, v), "loss_breaker") == 0 and [c[1] for c in _places(v)] == [SLUG]


def test_l2_a_re_arm_older_than_24_h_is_the_full_window(monkeypatch):
    seen = _recorder(monkeypatch, {ml._utc(NOW - H24): -10.0})
    p = _pool()
    p.state["mirror_loss_rearm"] = _rearm(at=NOW - H24 - 100)
    v = _Venue()
    st = _tick(p, v)
    assert seen == [ml._utc(NOW - H24)] and _census(st, "loss_breaker") == 0
    assert [c[1] for c in _places(v)] == [SLUG]
    assert ml._last_sleeve["since"] == ml._iso(NOW - H24)


@pytest.mark.parametrize("value", [{"by": "render-ops"}, {"at": "yesterday"}, {"at": "2026-09-07T17:30:00"}, 7])
def test_l2_a_malformed_key_is_the_full_window_for_the_sleeve_too(monkeypatch, value, caplog):
    seen = _recorder(monkeypatch, {ml._utc(NOW - H24): -10.0})
    p = _pool()
    p.state["mirror_loss_rearm"] = value
    with caplog.at_level(logging.WARNING, logger=ml.log.name):
        st = _tick(p, _Venue())
    assert seen == [ml._utc(NOW - H24)] and _census(st, "loss_breaker") == 0
    assert ml._last_sleeve["since"] == ml._iso(NOW - H24)
    warned = [x for x in caplog.records if "mirror_loss_rearm malformed" in x.getMessage()]
    assert len(warned) == 1, "one warning across the two readers of the same key"


def test_l2_an_unreadable_re_arm_read_is_the_full_window_and_the_stop(monkeypatch):
    """A raised read: the sleeve is read over the wider window (never a
    shorter one), then _loss_stop stops the tick on that same read (L1),
    no mirror sum is read, and the reduce still goes out."""
    orig = ml._state

    async def _st(pool, key):
        if key == "mirror_loss_rearm":
            return None, "ConnectionDoesNotExistError"
        return await orig(pool, key)
    monkeypatch.setattr(ml, "_state", _st)
    seen = _recorder(monkeypatch, {None: -10.0})
    p, v = _reduce_world()
    st = _tick(p, v)
    assert seen == [None]
    assert _census(st, "mirror_loss_stop") >= 1 and _census(st, "loss_breaker") == 0
    assert not _loss_reads(p) and "mirror_loss_stop" not in p.state
    assert ml._last_sleeve == {"sum": -10.0, "limit": 5000.0, "since": None}
    assert ("cancel", "oid-1", SLUG) in v.calls
    pl = _places(v)
    assert len(pl) == 1 and pl[0][4] is True, pl       # the reduce, as L1 pinned it


def test_l2_an_unreadable_ledger_still_refuses_by_name(monkeypatch):
    async def _none(pool, since=None):
        return None
    monkeypatch.setattr(le, "_loss_breaker_sum", _none)
    p = _pool()
    p.state["mirror_loss_rearm"] = _rearm()
    v = _Venue()
    st = _tick(p, v)
    assert _census(st, "loss_breaker_unreadable") >= 1 and not _places(v)
    assert ml._last_sleeve is None


def test_l2_the_re_arm_key_is_read_once_per_tick(monkeypatch):
    orig = ml._state
    reads = []

    async def _st(pool, key):
        if key in ("mirror_loss_rearm", "mirror_loss_stop"):
            reads.append(key)
        return await orig(pool, key)
    monkeypatch.setattr(ml, "_state", _st)
    _recorder(monkeypatch, {REARM_DT: -100.0})
    p = _pool()
    p.state["mirror_loss_rearm"] = _rearm()
    st = _tick(p, _Venue())
    assert reads == ["mirror_loss_stop", "mirror_loss_rearm"], "each key once, the stop first"
    assert _census(st, "loss_breaker") == 0 and _census(st, "mirror_loss_stop") == 0
    assert ml._last_loss["since"] == ml._iso(REARM_TS)
    assert ml._last_loss["rearmed_at"] == ml._iso(REARM_TS)


def test_l2_behind_a_standing_stop_the_re_arm_key_is_not_read_and_the_sleeve_reads_its_full_window(monkeypatch):
    """L1's review rule stands: behind a standing (or unreadable) stop
    key nothing else is read -- the sleeve reads its FULL window without
    consulting the re-arm key, the stop key is read once, the tick is
    exits-only by the stop's name."""
    orig = ml._state
    reads = []

    async def _st(pool, key):
        reads.append(key)
        return await orig(pool, key)
    monkeypatch.setattr(ml, "_state", _st)
    seen = _recorder(monkeypatch, {None: -100.0})
    p = _pool()
    p.state["mirror_loss_rearm"] = _rearm()
    p.state["mirror_loss_stop"] = dict(TRIPPED)
    v = _Venue()
    st = _tick(p, v)
    assert seen == [None] and _census(st, "mirror_loss_stop") >= 1 and not _places(v)
    assert reads.count("mirror_loss_stop") == 1 and "mirror_loss_rearm" not in reads
    assert p.state["mirror_loss_stop"] == TRIPPED and ml._last_loss is None
    assert ml._last_sleeve == {"sum": -100.0, "limit": 5000.0, "since": None}


def test_l2_a_tick_that_is_not_on_reads_no_sleeve(monkeypatch):
    seen = _recorder(monkeypatch, {None: 0.0, ml._utc(NOW - H24): 0.0})
    monkeypatch.setenv("PMUS_MIRROR", "exits")
    p = _pool()
    _tick(p, _Venue())
    assert seen == [] and ml._last_sleeve is None


# --------------------------------------------------------- 3. the mode line

def test_l2_the_mode_line_prints_the_sleeve_beside_the_loss(caplog):
    stats = ml._new_stats()
    stats.update(mode=ml.MODE_ON, whales=["rn1"], books_live=2, orders_open=1)
    loss = {"sum": -100.0, "books": 1, "limit": 5000.0,
            "since": "2026-09-07T18:16:06Z", "rearmed_at": "2026-09-07T18:16:06Z"}
    with caplog.at_level(logging.INFO, logger=ml.log.name):
        ml._mode_line(dict(stats), ml.MODE_LINE_EVERY_TICKS, loss,
                      {"sum": -120.5, "limit": 5000.0, "since": "2026-09-07T18:16:06Z"})
    line = [r.getMessage() for r in caplog.records if r.getMessage().startswith("mirror_live mode=")][0]
    assert line.startswith("mirror_live mode=on whales=['rn1'] books=2 open=1 day=None "
                           "loss=-100.0/5000.0 since 18:16 sleeve=-120.5/5000.0 venue=None stats={")
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=ml.log.name):
        ml._mode_line(dict(stats), ml.MODE_LINE_EVERY_TICKS)
    line = [r.getMessage() for r in caplog.records if r.getMessage().startswith("mirror_live mode=")][0]
    assert "sleeve=" not in line and "loss=" not in line
    assert list(inspect.signature(ml._mode_line).parameters) == ["stats", "ticks", "loss", "sleeve"]
    src = inspect.getsource(ml.main)
    assert "_mode_line(stats, ticks, _last_loss, _last_sleeve)" in src
    src = inspect.getsource(ml.tick_once)
    assert "_last_sleeve = None" in src and "_last_sleeve = t.sleeve" in src


# ------------------------------------------------------------- 4. the preset

def _presets():
    text = RENDER_OPS.read_text()
    wf = yaml.safe_load(text)
    run = wf["jobs"]["ops"]["steps"][-1]["run"] if "run" in wf["jobs"]["ops"]["steps"][-1] else \
        next(s["run"] for s in wf["jobs"]["ops"]["steps"] if "run" in s and "loss-breaker)" in s["run"])
    return run


def test_l2_the_preset_reads_both_windows_and_sits_in_the_error_line():
    run = _presets()
    m = re.search(r'^\s+loss-breaker\) SQL="(.*?)"; TO=30000 ;;$', run, flags=re.M)
    assert m, "the loss-breaker preset"
    sql = m.group(1)
    assert "need_confirm" not in run[max(0, m.start() - 40):m.start()]
    assert "WHERE key = 'mirror_loss_rearm'" in sql and "(value->>'at')::timestamptz" in sql
    assert "GREATEST(now() - interval '24 hours', COALESCE((SELECT at FROM r), now() - interval '24 hours'))" in sql
    assert "status IN ('settled', 'cashed_out')" in sql and sql.count("live_orders") == 2
    assert "ORDER BY o.settled_at DESC LIMIT 40" in sql
    assert not re.search(r"\b(DELETE|UPDATE|INSERT|DROP|TRUNCATE)\b", sql)
    line = re.search(r'\*\) echo "sql: arg must be one of ([^ ]+) \(got', run).group(1).split("|")
    labels = re.findall(r"^\s+([a-z0-9-]+)\) (?:need_confirm; )?SQL=", run, flags=re.M)
    assert line == labels and "loss-breaker" in line and "mirror-rearm" in line
