"""THE VENUE RULES READ LEAVES THE 30 s WINDOW (RC6 lane C, software reds).

PRODUCTION (research-sql on claude/p0-closeout, 2026-10-09, read only):
  * every calibration-only valuation 2026-10-08 16:00Z .. 2026-10-09 02:24Z
    (rc6_swreds_rules_cache, run 37874370857): football decision lag (book
    read -> decision) p50 1.58 s when the venue rules text was read inside
    the window (272 rows) against 0.25 s when the hourly cache answered
    (144); NCAAF read it fresh for 194 of 224 candidates;
  * the 20:09Z cycle (rc6_swreds_candidate_timing, 37873120447): seven NCAAF
    candidates read ~2.1 s apart on quotes delivered 14.8 s old; the other
    ~44 mapped events refused QUOTE_STALE_ON_ARRIVAL -- the largest SOFTWARE
    first loss of the RC5 runtime (24-49 events an hour, PinnAPI feed up and
    down alike, the census re-run per hour on production's own inputs).
The rotation (REQUEUE_AFTER_OUR_DELAY_RULE) serves an NCAAF event about once
in six fetches, past the rules cache's hour, so the window paid for the read.

RULES_READ_AHEAD_RULE: after the cycle's time-critical work, the events still
owed a fresh window have their rules text read ahead -- same reader, gate,
pacing and hourly cache (TTL unchanged), each answer with its own read
instant; an answer valid at the next fetch is not read again; a read refused
by our gate or lost in transport is never cached. And, found on the way: a
transport failure used to be cached for the hour as if it were the venue's
answer.

No freshness limit, clock, pacing gap, gate, TTL or threshold is changed.
"""
from __future__ import annotations

import os
import time

import pytest

from sportsassets import bettor_live_read as lr
from sportsassets import pmus
from sportsassets import venue_pace
from sportsassets import venue_request_gate as grt
from sportsassets.workers import ext_pinnacle_loop as loop

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

SLUG = "aec-cfb-rc6-ahead-2026-10-10"


@pytest.fixture(autouse=True)
def _clean_cache():
    loop.rules_cache_reset()
    grt.clear_hold()
    yield
    loop.rules_cache_reset()
    grt.clear_hold()


def _reader_sequence(monkeypatch, answers):
    """read_rules_text answering `answers` in turn; the client and the pacer
    stood in (no venue)."""
    calls = []
    monkeypatch.setattr(venue_pace, "pace", lambda *a, **k: 0.0)
    monkeypatch.setattr(pmus, "_get_client", lambda: object())

    def _read(client, slug):
        calls.append(slug)
        return dict(answers[min(len(calls), len(answers)) - 1], slug=slug)
    monkeypatch.setattr(lr, "read_rules_text", _read)
    return calls


_TEXT = {"ok": True, "rules_text": "This market resolves ...",
         "rules_field": "description", "error": None}


def test_a_transport_failure_is_not_served_from_the_cache_for_an_hour(
        monkeypatch):
    """412c4962 cached EVERY answer, so a 429 / timeout / our gate's refusal
    (read_rules_text names it by its exception class) was re-served as the
    slug's rules for the next hour."""
    calls = _reader_sequence(monkeypatch, [
        {"ok": False, "rules_text": None, "error": "RateLimitError"},
        _TEXT])
    first = loop._read_venue_rules_blocking(SLUG)
    assert first["ok"] is False and first["error"] == "RateLimitError"
    second = loop._read_venue_rules_blocking(SLUG)
    assert calls == [SLUG, SLUG], "the failure was served from the cache"
    assert second["ok"] is True and second["from_cache"] is False
    third = loop._read_venue_rules_blocking(SLUG)
    assert third["from_cache"] is True and len(calls) == 2


@pytest.mark.parametrize("venue_says", [lr.R_RULES_NOT_PUBLISHED,
                                        lr.R_RULES_NOT_LISTED])
def test_the_venues_own_none_is_still_cached_for_the_hour(monkeypatch,
                                                         venue_says):
    """UNCHANGED: a contract that publishes no prose is not re-asked."""
    calls = _reader_sequence(monkeypatch, [
        {"ok": False, "rules_text": None, "error": venue_says}])
    loop._read_venue_rules_blocking(SLUG)
    again = loop._read_venue_rules_blocking(SLUG)
    assert again["from_cache"] is True and calls == [SLUG]


def test_valid_until_reads_again_what_would_expire_and_keeps_its_instant(
        monkeypatch):
    calls = _reader_sequence(monkeypatch, [_TEXT])
    t = 2_000_000_000.0
    loop._RULES_CACHE[SLUG] = dict(_TEXT, read_at=t - 3000.0)
    # inside its TTL now: served as before
    assert loop._read_venue_rules_blocking(SLUG, now=t)["from_cache"] is True
    assert calls == []
    # not inside its TTL at the next fetch: read now, stamped NOW
    got = loop._read_venue_rules_blocking(SLUG, now=t, valid_until=t + 900.0)
    assert calls == [SLUG] and got["from_cache"] is False
    assert got["read_at"] == t
    assert loop._RULES_CACHE[SLUG]["read_at"] == t
    assert loop.RULES_CACHE_TTL_S == 3600.0


def test_the_owed_slugs_are_oldest_first_interleaved_and_bounded():
    ledger = [
        {"sport_key": "americanfootball_ncaaf", "provider_event_id": "a",
         "us_market_slug": "aec-cfb-a"},
        {"sport_key": "americanfootball_ncaaf", "provider_event_id": "b",
         "us_market_slug": "aec-cfb-b"},
        {"sport_key": "americanfootball_ncaaf", "provider_event_id": "c",
         "us_market_slug": "aec-cfb-c"},
        {"sport_key": "americanfootball_nfl", "provider_event_id": "n",
         "us_market_slug": "aec-nfl-n"},
        # an owed event this cycle never mapped: nothing to read
        {"sport_key": "americanfootball_ncaaf", "provider_event_id": "u",
         "us_market_slug": None}]
    owed = {"americanfootball_ncaaf": {"a": 30.0, "b": 10.0, "c": 20.0,
                                       "u": 5.0},
            "americanfootball_nfl": {"n": 40.0}}
    assert loop.owed_rules_slugs(
        owed, ledger, sport_order=["americanfootball_nfl",
                                   "americanfootball_ncaaf"]) == \
        ["aec-nfl-n", "aec-cfb-b", "aec-cfb-c", "aec-cfb-a"]
    assert loop.owed_rules_slugs(owed, ledger, limit=2) == \
        ["aec-cfb-b", "aec-nfl-n"]
    assert loop.owed_rules_slugs({}, ledger) == []


def test_the_read_ahead_reads_only_what_the_next_fetch_would_miss():
    now = [1_000.0]
    seen = []

    def _reader(slug, valid_until=None):
        seen.append((slug, valid_until,
                     (grt.read_state(grt.current_read()) or {})
                     .get("deadline_epoch_s")))
        now[0] += 1.3
        got = dict(_TEXT, slug=slug, read_at=now[0], from_cache=False)
        loop._RULES_CACHE[slug] = dict(got)
        return got

    # valid through the next fetch (read 100 s ago): not read again
    loop._RULES_CACHE["aec-cfb-fresh"] = dict(_TEXT, read_at=900.0)
    # valid now, expired by the next fetch (read 3000 s ago): read now
    loop._RULES_CACHE["aec-cfb-expiring"] = dict(_TEXT, read_at=-2000.0)
    out = loop.rules_read_ahead_blocking(
        ["aec-cfb-fresh", "aec-cfb-expiring", "aec-cfb-absent"],
        reader=_reader, clock=lambda: now[0], budget_s=60.0,
        valid_for_s=900.0)
    assert [s for s, _, _ in seen] == ["aec-cfb-expiring", "aec-cfb-absent"]
    # the read is asked to be valid through the next fetch, and the gate is
    # given the budget's end as this read's deadline
    assert seen[0][1] == pytest.approx(1000.0 + 900.0)
    assert seen[0][2] == pytest.approx(1000.0 + 60.0)
    assert (out["asked"], out["read"], out["already_valid"],
            out["not_cached"], out["budget_spent"]) == (3, 2, 1, 0, False)
    assert grt.current_read() is None


def test_the_read_ahead_stops_at_its_budget():
    now = [0.0]

    def _slow(slug, valid_until=None):
        now[0] += 25.0
        return dict(_TEXT, slug=slug, from_cache=False)

    out = loop.rules_read_ahead_blocking(
        ["aec-1", "aec-2", "aec-3", "aec-4"], reader=_slow,
        clock=lambda: now[0], budget_s=60.0)
    assert out["read"] == 3 and out["budget_spent"] is True


def test_a_venue_hold_past_the_budget_is_refused_at_once_and_not_cached(
        monkeypatch):
    """Through the REAL reader: the transport's dispatch check (what
    venue_request_gate.PacedTransport runs) sees the read-ahead's deadline,
    so a 429 hold that outlasts the budget is refused by name instead of
    slept out (an undeadlined read waits up to 20 s), and the refusal --
    a transport outcome -- is not cached for the candidate."""
    monkeypatch.setattr(venue_pace, "pace", lambda *a, **k: 0.0)

    class _M:
        def list(self, params=None):
            grt.check_before_dispatch(read_id=grt.current_read())
            raise AssertionError("dispatched through a hold past the budget")

    class _C:
        markets = _M()
    monkeypatch.setattr(pmus, "_get_client", lambda: _C())
    grt.hold_until(until_epoch_s=time.time() + 120.0,
                   reason="VENUE_429_ON_BOOK_READ")
    t0 = time.monotonic()
    out = loop.rules_read_ahead_blocking([SLUG], budget_s=10.0)
    assert time.monotonic() - t0 < 2.0
    assert out["not_cached"] == 1 and out["read"] == 0
    assert out["errors"] == {"VenueGateRefusal": 1}
    assert SLUG not in loop._RULES_CACHE


def test_the_rule_moves_no_limit():
    assert loop.PINNACLE_MAX_AGE_S == 30.0
    assert loop.RULES_CACHE_TTL_S == 3600.0
    assert grt.MAX_UNDEADLINED_WAIT_S == 20.0
    assert venue_pace.MIN_GAP_S == 0.35
    assert "TTL unchanged" in loop.RULES_READ_AHEAD_RULE


# ── THROUGH THE REAL CYCLE (the production-shaped single-event fixture) ───

@pg
@pytest.mark.asyncio
async def test_a_candidate_our_queue_made_stale_reads_its_rules_before_its_next_window(  # noqa: E501
        monkeypatch):
    """Cycle 1: the quote was received 40 s ago with a 5 s provider lag --
    our queue made it stale: refused QUOTE_STALE_ON_ARRIVAL by name and
    requeued, exactly as before -- and after the cycle's time-critical work
    its venue rules text is read ahead (once). Cycle 2: a fresh quote; the
    candidate is judged and its settlement comparison takes the rules from
    the cache with the read-ahead's own read instant: no rules read inside
    its window. On 412c4962 cycle 1 read nothing ahead and cycle 2 read the
    rules inside the window (venue_rules_from_cache False)."""
    from tests import test_quote_stale_on_arrival_is_not_self_inflicted as Q
    monkeypatch.setitem(loop._COVERAGE, "requeued_after_our_delay", {})
    conn, F, venue, out, calls, handed = await Q._run(
        monkeypatch, [(-40.0, -45.0)])
    try:
        assert out["ran"] is True, out.get("why")
        assert out["refusals"].get(loop.R_QUOTE_STALE_ON_ARRIVAL) == 1
        assert out["latency"]["requeued_after_our_delay"] == 1
        ahead = out["rules_read_ahead"]
        assert (ahead["asked"], ahead["read"], ahead["not_cached"]) == \
            (1, 1, 0), ahead
        lists = [s for s in venue.sent if s[0] == "markets.list"
                 and F.US_SLUG in (s[1] or [])]
        assert len(lists) == 1
        read_at = loop._RULES_CACHE[F.US_SLUG]["read_at"]
        assert out["step_timing_s"]["rules_read_ahead"] >= 0.0

        # CYCLE 2 -- the same process, the same cache; a fresh quote
        await F.clean(conn)
        await F.seed(conn)
        sent_before = len(venue.sent)
        Q._provider(monkeypatch, F, [(0.0, -2.0)])
        out2 = await loop.cycle(conn)
        assert out2["ran"] is True, out2.get("why")
        assert loop.R_QUOTE_STALE_ON_ARRIVAL not in out2["refusals"]
        assert [s for s in venue.sent[sent_before:]
                if s[0] == "markets.list"
                and F.US_SLUG in (s[1] or [])] == []
        row = await conn.fetchrow(
            "SELECT settlement_comparison->>'venue_rules_from_cache' AS c, "
            "       (settlement_comparison->>'venue_rules_retrieved_at')"
            "::float8 AS at "
            "  FROM external_valuations WHERE us_market_slug = $1 "
            " ORDER BY id DESC LIMIT 1", F.US_SLUG)
        assert row is not None
        assert (row["c"], row["at"]) == ("true", pytest.approx(read_at))
    finally:
        await F.clean(conn)
        await conn.close()
