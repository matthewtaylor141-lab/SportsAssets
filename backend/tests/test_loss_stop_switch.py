"""The loss stop switched off by owner order (2026-09-09 ~18:45Z).

Owner, verbatim: "Remove the stop loss for the time being so that we
are not down when it turns". The rule: rules.MIRROR_LOSS_STOP is an
env_switch, ON in code, that the environment may only turn OFF
(MIRROR_LOSS_STOP=off / 0). OFF, neither stop refuses an increase --
the mirror's own realized-loss stop (_loss_stop) never writes the stop
key and a standing stop key does not hold; the copy sleeve's breaker
read (le.PMUS_LOSS_BREAKER_USD, L2) never blocks; an unreadable sum
blocks nothing -- while the sums are still read every tick and
published with `"stop": "off"` (`loss=<sum>/off` / `sleeve=<sum>/off`
on the mode line). ON (the default, and every existing pin), nothing
changes: the L1 / L2 shapes trip exactly as they did. The limits do not
move (MIRROR_LOSS_STOP_USD stays capped_env, PMUS_LOSS_BREAKER_USD its
plain default), and the exits carve-out is untouched: the stop only
ever blocked increases.

Driven through tests/test_mirror_live_worker's fakes, on the shapes
test_l1_rearm_window and test_l2_sleeve_window pin.
"""

from __future__ import annotations

import inspect
import logging
import pathlib
import re

import pytest

from sportsassets import live_executor as le
from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.workers import mirror_live as ml
from tests.test_l1_rearm_window import TRIPPED, H24, _rearm, _reduce_world
from tests.test_l2_sleeve_window import _recorder
from tests.test_mirror_live_worker import IOC_TIF, NOW, SLUG, _armed  # noqa: F401 — the fixture
from tests.test_mirror_live_worker import _ZZ, _census, _places, _pool, _settled_book, _tick, _Venue

DOCS = pathlib.Path(__file__).resolve().parents[2] / "docs" / "mirror-coverage.md"


@pytest.fixture(autouse=True)
def _limits(monkeypatch):
    """The fixtures of the L1 / L2 files are cut at the $5,000 stops."""
    monkeypatch.setattr(rules, "MIRROR_LOSS_STOP_USD", 5000.0)
    monkeypatch.setattr(le, "PMUS_LOSS_BREAKER_USD", 5000.0)


def _off(monkeypatch):
    monkeypatch.setattr(rules, "MIRROR_LOSS_STOP", False)


def _mode_lines(caplog):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("mirror_live mode=")]


# ------------------------------------------------------------ 1. the switch

def test_the_switch_is_on_in_code_and_the_environment_may_only_turn_it_off(monkeypatch):
    src = inspect.getsource(rules)
    assert src.count('MIRROR_LOSS_STOP = env_switch("MIRROR_LOSS_STOP", True)') == 1
    assert "MIRROR_LOSS_STOP" in rules.__all__ and "MIRROR_LOSS_STOP_USD" in rules.__all__
    assert rules.MIRROR_LOSS_STOP is True
    assert rules.env_switch("MIRROR_LOSS_STOP", True) is True
    for word in ("off", "0", "false", "no"):
        monkeypatch.setenv("MIRROR_LOSS_STOP", word)
        assert rules.env_switch("MIRROR_LOSS_STOP", True) is False, word
    for word in ("on", "1", "true", "yes", "banana", ""):
        monkeypatch.setenv("MIRROR_LOSS_STOP", word)
        assert rules.env_switch("MIRROR_LOSS_STOP", True) is True, word
    monkeypatch.delenv("MIRROR_LOSS_STOP")
    assert rules.env_switch("MIRROR_LOSS_STOP", True) is True
    # the limits did not move: still downward-only, still $10,000
    assert 'MIRROR_LOSS_STOP_USD = capped_env("MIRROR_LOSS_STOP_USD", 10000.0)' in src
    assert 'os.environ.get("PMUS_LOSS_BREAKER_USD", "10000")' in inspect.getsource(le)
    # the worker reads the switch through the module at tick time, at
    # the three sites and nowhere else: the sleeve read, the stop step
    # and the stop's own read
    wsrc = inspect.getsource(ml)
    assert wsrc.count("rules.MIRROR_LOSS_STOP)") + wsrc.count("rules.MIRROR_LOSS_STOP:") == 3
    assert "armed = bool(rules.MIRROR_LOSS_STOP)" in inspect.getsource(ml._global_guards)
    assert "if not rules.MIRROR_LOSS_STOP:" in inspect.getsource(ml._global_guards)
    assert "armed = bool(rules.MIRROR_LOSS_STOP)" in inspect.getsource(ml._loss_stop)


# ---------------------------------------------- 2. the mirror's own stop, off

def test_off_a_loss_past_the_limit_does_not_trip_and_the_candidate_opens(monkeypatch):
    """test_l1_a_trip_receipt_carries_since_and_rearmed_at's shape: a
    loss of 1.1 x the stop after the re-arm. ON it trips (the L1 pin);
    OFF the tick refuses nothing, writes no stop key, opens the
    candidate, and publishes the reading with `stop: off`."""
    _off(monkeypatch)
    stop = float(rules.MIRROR_LOSS_STOP_USD)
    p = _pool()
    _settled_book(p, -0.4 * stop, -1.1 * stop, closed_ago=600, **_ZZ)
    p.state["mirror_loss_rearm"] = _rearm()
    v = _Venue()
    st = _tick(p, v)
    assert _census(st, "mirror_loss_stop") == 0 and "mirror_loss_stop" not in p.state
    assert [c[1] for c in _places(v)] == [SLUG], "off: the candidate opens past the limit"
    assert ml._last_loss == {"sum": pytest.approx(-1.1 * stop), "books": 1, "limit": stop,
                             "since": ml._iso(ml._rearm_at(_rearm(), None)),
                             "rearmed_at": ml._iso(ml._rearm_at(_rearm(), None)), "stop": "off"}
    # the same rows with the switch ON trip, as L1 pinned: the switch is
    # the only difference
    monkeypatch.setattr(rules, "MIRROR_LOSS_STOP", True)
    p2 = _pool()
    _settled_book(p2, -0.4 * stop, -1.1 * stop, closed_ago=600, **_ZZ)
    p2.state["mirror_loss_rearm"] = _rearm()
    v2 = _Venue()
    st2 = _tick(p2, v2)
    assert _census(st2, "mirror_loss_stop") >= 1 and not _places(v2)
    assert p2.state["mirror_loss_stop"]["sum"] == pytest.approx(-1.1 * stop)
    assert "stop" not in ml._last_loss


def test_off_a_standing_stop_key_does_not_hold_and_is_left_as_it_stands(monkeypatch):
    """A stop that tripped before the switch went off (the 17:07:45Z
    receipt standing): OFF, the tick opens through it and the key is
    neither deleted nor rewritten -- it holds again the tick the switch
    is back on (the re-arm stays the owner's word)."""
    _off(monkeypatch)
    p = _pool()
    p.state["mirror_loss_stop"] = dict(TRIPPED)
    v = _Venue()
    st = _tick(p, v)
    assert _census(st, "mirror_loss_stop") == 0
    assert [c[1] for c in _places(v)] == [SLUG]
    assert p.state["mirror_loss_stop"] == TRIPPED
    assert ml._last_loss == {"sum": 0.0, "books": 0, "limit": 5000.0,
                             "since": ml._iso(NOW - H24), "rearmed_at": None, "stop": "off"}
    monkeypatch.setattr(rules, "MIRROR_LOSS_STOP", True)
    p2 = _pool()
    p2.state["mirror_loss_stop"] = dict(TRIPPED)
    v2 = _Venue()
    st2 = _tick(p2, v2)
    assert _census(st2, "mirror_loss_stop") >= 1 and not _places(v2)
    assert ml._last_loss is None


def test_off_an_unreadable_sum_blocks_nothing(monkeypatch):
    """ON, each unreadable read is a stop (fail closed toward the stop).
    OFF there is no stop to fail toward: the tick opens, and the
    reading published carries `sum` None under `stop: off`."""
    _off(monkeypatch)
    # the loss sum unreadable
    p = _pool()
    p.raise_on.append(("ml-loss-sum", RuntimeError("db blip")))
    v = _Venue()
    st = _tick(p, v)
    assert _census(st, "mirror_loss_stop") == 0 and [c[1] for c in _places(v)] == [SLUG]
    assert ml._last_loss["sum"] is None and ml._last_loss["stop"] == "off"
    assert ml._last_loss["since"] == ml._iso(NOW - H24)
    # ON, the same unreadable sum is a stop, as it was
    monkeypatch.setattr(rules, "MIRROR_LOSS_STOP", True)
    p = _pool()
    p.raise_on.append(("ml-loss-sum", RuntimeError("db blip")))
    v = _Venue()
    st = _tick(p, v)
    assert _census(st, "mirror_loss_stop") >= 1 and not _places(v)


def test_off_an_unreadable_rearm_key_blocks_nothing_and_is_a_stop_on(monkeypatch):
    """The re-arm key unreadable (not malformed). ON, L1's pin: a stop by
    name. OFF: the tick opens, the reading is published with `sum` None /
    `since` None / `stop: off`, and no stop key is written (review HIGH-1:
    mutant M16, the OFF branch blocking on an unreadable re-arm key)."""
    _off(monkeypatch)
    real = ml._state

    async def _state(pool, key):
        if key == ml._STATE_LOSS_REARM:
            return None, "RuntimeError"
        return await real(pool, key)
    monkeypatch.setattr(ml, "_state", _state)
    p = _pool()
    v = _Venue()
    st = _tick(p, v)
    assert _census(st, "mirror_loss_stop") == 0
    assert [c[1] for c in _places(v)] == [SLUG]
    assert "mirror_loss_stop" not in p.state
    assert ml._last_loss == {"sum": None, "books": None, "limit": 5000.0,
                             "since": None, "rearmed_at": None, "stop": "off"}
    assert ml._last_sleeve["since"] is None and ml._last_sleeve["stop"] == "off"
    monkeypatch.setattr(rules, "MIRROR_LOSS_STOP", True)
    p2 = _pool()
    v2 = _Venue()
    st2 = _tick(p2, v2)
    assert _census(st2, "mirror_loss_stop") >= 1 and not _places(v2)


def test_off_an_unreadable_stop_key_does_not_hold_and_is_a_stop_on(monkeypatch):
    """The stop KEY itself unreadable (not the sum): ON it is a stop by
    name (L1); OFF it holds nothing, the tick opens, the mirror's own
    window is still read and published with `stop: off` (review HIGH-2:
    mutant M21, `and err is None` on the OFF branch, survived the text
    pin alone)."""
    _off(monkeypatch)
    real = ml._state

    async def _state(pool, key):
        if key == ml._STATE_LOSS_STOP:
            return None, "RuntimeError"
        return await real(pool, key)
    monkeypatch.setattr(ml, "_state", _state)
    p = _pool()
    v = _Venue()
    st = _tick(p, v)
    assert _census(st, "mirror_loss_stop") == 0
    assert [c[1] for c in _places(v)] == [SLUG]
    assert ml._last_loss == {"sum": 0.0, "books": 0, "limit": 5000.0,
                             "since": ml._iso(NOW - H24), "rearmed_at": None, "stop": "off"}
    monkeypatch.setattr(rules, "MIRROR_LOSS_STOP", True)
    p2 = _pool()
    v2 = _Venue()
    st2 = _tick(p2, v2)
    assert _census(st2, "mirror_loss_stop") >= 1 and not _places(v2)
    assert ml._last_loss is None


# ------------------------------------------------ 3. the sleeve's breaker, off

def test_off_the_sleeve_breaker_past_its_threshold_blocks_nothing_and_is_not_logged(monkeypatch, caplog):
    """test_l2_with_no_re_arm_the_full_window_trips_and_is_logged's shape
    (-6,000 over the full window against 5,000): OFF the sum is read on
    the same window, published with `stop: off`, no LOSS BREAKER line,
    no loss_breaker, the mirror's own sum still read, the candidate
    opens."""
    _off(monkeypatch)
    seen = _recorder(monkeypatch, {ml._utc(NOW - H24): -6000.0})
    p = _pool()
    v = _Venue()
    with caplog.at_level(logging.WARNING, logger=ml.log.name):
        st = _tick(p, v)
    assert seen == [ml._utc(NOW - H24)]
    assert _census(st, "loss_breaker") == 0 and _census(st, "loss_breaker_unreadable") == 0
    assert [c[1] for c in _places(v)] == [SLUG]
    assert ml._last_sleeve == {"sum": -6000.0, "limit": 5000.0, "since": ml._iso(NOW - H24), "stop": "off"}
    assert [x for x in p.sent if "ml-loss-sum" in x[1]], "the mirror's own sum is read under the off switch"
    assert not [r for r in caplog.records if r.getMessage().startswith("LOSS BREAKER")]


def test_off_an_unreadable_sleeve_blocks_nothing(monkeypatch):
    _off(monkeypatch)

    async def _none(pool, since=None):
        return None
    monkeypatch.setattr(le, "_loss_breaker_sum", _none)
    p = _pool()
    v = _Venue()
    st = _tick(p, v)
    assert _census(st, "loss_breaker_unreadable") == 0 and _census(st, "loss_breaker") == 0
    assert [c[1] for c in _places(v)] == [SLUG]
    assert ml._last_sleeve == {"sum": None, "limit": 5000.0, "since": ml._iso(NOW - H24), "stop": "off"}
    monkeypatch.setattr(rules, "MIRROR_LOSS_STOP", True)
    p2 = _pool()
    v2 = _Venue()
    st2 = _tick(p2, v2)
    assert _census(st2, "loss_breaker_unreadable") >= 1 and not _places(v2)


# ------------------------------------------------------ 4. what does not move

def test_on_the_readings_carry_no_stop_key_and_reduces_pass_either_way(monkeypatch):
    """ON (the default) the published readings are byte-identical to
    L1's / L2's (no `stop` key). And the exits carve-out never depended
    on the switch: a reduce passes under a tripped stop ON, and under
    the switch OFF."""
    seen = _recorder(monkeypatch, {ml._utc(NOW - H24): -100.0})
    p = _pool()
    _tick(p, _Venue())
    assert seen == [ml._utc(NOW - H24)]
    assert "stop" not in ml._last_loss and "stop" not in ml._last_sleeve

    async def _any(pool, since=None):
        return -100.0
    monkeypatch.setattr(le, "_loss_breaker_sum", _any)
    for off in (False, True):
        monkeypatch.setattr(rules, "MIRROR_LOSS_STOP", not off)
        p, v = _reduce_world()
        p.state["mirror_loss_stop"] = dict(TRIPPED)
        st = _tick(p, v)
        # the reduce's IOC at his cent (the L1 every-guard pin's shape), either way
        assert (200, True, IOC_TIF) in [c[3:6] for c in _places(v)], ("the reduce passes", off)
        assert (_census(st, "mirror_loss_stop") == 0) is off


def test_the_mode_line_prints_off_where_the_limit_was(caplog):
    stats = ml._new_stats()
    stats.update(mode=ml.MODE_ON, whales=["rn1"], books_live=2, orders_open=1)
    with caplog.at_level(logging.INFO, logger=ml.log.name):
        ml._mode_line(dict(stats), ml.MODE_LINE_EVERY_TICKS,
                      {"sum": -2029.5491, "books": 3, "limit": 10000.0, "since": "2026-09-09T14:07:00Z",
                       "rearmed_at": "2026-09-09T14:07:00Z", "stop": "off"},
                      {"sum": -1436.8519, "limit": 10000.0, "since": "2026-09-09T14:07:00Z", "stop": "off"})
    assert " day=None loss=-2029.5491/off since 14:07 sleeve=-1436.8519/off venue=None stats={" \
        in _mode_lines(caplog)[0]
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=ml.log.name):
        ml._mode_line(dict(stats), ml.MODE_LINE_EVERY_TICKS,
                      {"sum": -100.0, "books": 1, "limit": 5000.0, "since": "2026-09-07T17:30:00Z",
                       "rearmed_at": "2026-09-07T17:30:00Z"},
                      {"sum": -100.0, "limit": 5000.0, "since": "2026-09-07T17:30:00Z"})
    assert " loss=-100.0/5000.0 since 17:30 sleeve=-100.0/5000.0 venue=" in _mode_lines(caplog)[0]


def test_the_docs_name_the_switch():
    text = DOCS.read_text()
    assert re.search(r"^## \d+\. The loss stop switched off \(2026-09-09, owner order\)", text, re.M)
    assert "MIRROR_LOSS_STOP" in text and "Remove the stop loss for the time being" in text
