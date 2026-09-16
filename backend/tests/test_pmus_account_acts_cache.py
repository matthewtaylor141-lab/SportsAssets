"""The week-activities cache is bounded (2026-09-03 API restarts behind
the 502s).

A slot is up to 8,000 verbatim venue rows -- 95-170 MB at production's
10.6 KB/row -- and the old sweep ran only past eight keys, so up to
eight slots sat on the resident floor, uncounted by the memory census
(five keys per probe run, one new key per day from venue-truth's
rolling since-day). That floor is where the per-request transients
landed in a 2 GB container. Now: expired slots go on every call, and at
most three live slots are resident, the oldest evicted first. Hit
behaviour and the signature are unchanged -- test_venue_truth.py
monkeypatches _week_activities by name.
"""

from __future__ import annotations

import asyncio
import inspect
import random
import time

import pytest

from sportsassets.api import pmus_account as pa


@pytest.fixture
def crawls(monkeypatch):
    """A counting stand-in for the 80-page venue crawl."""
    monkeypatch.setattr(pa, "_acts_cache", {})
    calls: list[str] = []

    def fake_fetch(oldest_day):
        calls.append(oldest_day)
        return [{"type": "ACTIVITY_TYPE_TRADE", "since": oldest_day,
                 "crawl": len(calls)}]

    monkeypatch.setattr(pa, "_fetch_week_activities_sync", fake_fetch)
    return calls


def _get(day):
    return asyncio.run(pa._week_activities(day))


def _expire(day):
    t0, acts = pa._acts_cache[day]
    pa._acts_cache[day] = (t0 - pa._ACTS_TTL - 1, acts)


def test_a_hit_within_the_ttl_does_not_re_crawl(crawls):
    first = _get("2026-08-21")
    second = _get("2026-08-21")
    assert crawls == ["2026-08-21"]
    assert second is first, "a hit returns the cached list itself, as before"


def test_an_expired_key_is_swept_on_the_next_call_even_beside_a_live_one(crawls):
    _get("2026-08-21")
    _get("2026-08-01")
    _expire("2026-08-01")
    # A HIT on the live key sweeps the dead one: the old code only swept
    # on a miss, and only past eight keys.
    _get("2026-08-21")
    assert crawls == ["2026-08-21", "2026-08-01"]
    assert set(pa._acts_cache) == {"2026-08-21"}


def test_an_expired_key_is_re_crawled_not_served(crawls):
    _get("2026-08-21")
    _expire("2026-08-21")
    _get("2026-08-21")
    assert crawls == ["2026-08-21", "2026-08-21"]
    assert time.time() - pa._acts_cache["2026-08-21"][0] < 5


def test_a_fourth_live_key_evicts_the_oldest(crawls):
    days = ["2026-08-21", "2026-08-01", "2026-09-01", "2026-09-03"]
    for i, d in enumerate(days):
        _get(d)
        # Distinct timestamps so "oldest" is unambiguous on a fast box.
        t0, acts = pa._acts_cache[d]
        pa._acts_cache[d] = (t0 - (len(days) - i), acts)
        assert len(pa._acts_cache) <= pa._ACTS_MAX_SLOTS == 3
    assert set(pa._acts_cache) == set(days[1:])
    assert crawls == days
    # The evicted key crawls again and pushes out the next-oldest.
    _get(days[0])
    assert set(pa._acts_cache) == {days[2], days[3], days[0]}


def test_room_is_made_after_the_crawl_lands_never_before(crawls, monkeypatch):
    """The cap holds at rest; during a crawl the three live slots stay
    resident (the crawl-time peak is one slot over, by choice -- see the
    comment in _week_activities), so a crawl that fails cannot have
    cost anything."""
    seen: list[set[str]] = []

    def counting_fetch(oldest_day):
        seen.append(set(pa._acts_cache))
        return [{"since": oldest_day}]

    monkeypatch.setattr(pa, "_fetch_week_activities_sync", counting_fetch)
    days = ("2026-08-21", "2026-08-01", "2026-09-01", "2026-09-03",
            "2026-09-04")
    for d in days:
        _get(d)
    # Each crawl saw every live slot that was there before it started.
    assert seen[3] == set(days[:3]) and seen[4] == set(days[1:4])
    assert max(len(s) for s in seen) == pa._ACTS_MAX_SLOTS
    assert len(pa._acts_cache) == 3


def test_never_more_than_three_slots_over_a_random_key_sequence(crawls):
    rng = random.Random(2026_09_03)
    keys = [f"2026-08-{d:02d}" for d in range(1, 12)]
    for _ in range(120):
        day = rng.choice(keys)
        if day in pa._acts_cache and rng.random() < 0.3:
            _expire(day)
        _get(day)
        assert len(pa._acts_cache) <= 3
        assert all(time.time() - t0 < pa._ACTS_TTL
                   for t0, _ in pa._acts_cache.values())


def test_a_failed_crawl_caches_nothing(crawls, monkeypatch):
    def boom(oldest_day):
        raise RuntimeError("RateLimitError")

    monkeypatch.setattr(pa, "_fetch_week_activities_sync", boom)
    with pytest.raises(RuntimeError):
        _get("2026-08-21")
    assert pa._acts_cache == {}
    assert not pa._acts_lock.locked()


def test_a_failed_crawl_leaves_a_full_cache_exactly_as_it_was(crawls, monkeypatch):
    """Hotfix review (2026-09-03 API restarts): the first cut evicted
    the oldest slot BEFORE the crawl, so three misses inside one
    rate-limited window emptied the live slots and every call after
    re-crawled 80 pages against a venue already limiting. Now a failing
    crawl -- a full cache, a fresh key, three misses in a row -- leaves
    the slots, their lists and their timestamps untouched."""
    for d in ("2026-08-21", "2026-08-01", "2026-09-01"):
        _get(d)
    before = dict(pa._acts_cache)
    lists = {k: v[1] for k, v in before.items()}

    def limited(oldest_day):
        raise RuntimeError("RateLimitError: 429")

    monkeypatch.setattr(pa, "_fetch_week_activities_sync", limited)
    for miss in ("2026-09-03", "2026-09-04", "2026-09-05"):
        with pytest.raises(RuntimeError):
            _get(miss)
        assert pa._acts_cache == before
        assert all(pa._acts_cache[k][1] is lists[k] for k in lists)
        assert not pa._acts_lock.locked()
    # The live keys still hit, with no crawl, while the venue limits.
    for d in ("2026-08-21", "2026-08-01", "2026-09-01"):
        assert _get(d) is lists[d]
    assert crawls == ["2026-08-21", "2026-08-01", "2026-09-01"]


def test_a_timed_out_crawl_leaves_the_cache_as_it_was(crawls, monkeypatch):
    for d in ("2026-08-21", "2026-08-01", "2026-09-01"):
        _get(d)
    before = dict(pa._acts_cache)

    def slow(oldest_day):
        time.sleep(0.2)
        return []

    monkeypatch.setattr(pa, "_fetch_week_activities_sync", slow)
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(pa._week_activities("2026-09-03", timeout=0.02))
    assert pa._acts_cache == before


def test_the_signature_the_venue_truth_tests_monkeypatch_is_unchanged():
    params = inspect.signature(pa._week_activities).parameters
    assert list(params) == ["since_day", "timeout"]
    assert params["timeout"].default == 240
