"""Enrichment, against the venue's REAL BBO payloads.

THE DEFECT THIS FILE PINS. On 2026-09-21 the observation worker went
live and excluded all 3,000 listed markets as ONE_SIDED_BOOK, every
five seconds, because `_discover` fed the venue's LISTING straight into
the selection rule. The listing carries no quote, no state and no
traded-share counter, so "the rule found nothing" was really "nobody
had read a book yet".

The previous harness missed it by feeding the rule 250 CAPTURED
OBSERVATION ROWS -- our own schema, our own spellings -- which the rule
also accepts. It proved the rule against a shape production never
delivers.

So every payload here is a VERBATIM HTTP 200 body from
`GET /v1/markets/{slug}/bbo`, copied out of
`research/evidence/capture/run85_phase2_segment_*/request_log.jsonl.gz`
(the same corpus `test_pmus_bbo_read.py` pins its own payloads from).
Nothing is hand-authored, nothing is rounded, and the `marketData`
wrapper is kept because the real responses have it and the SDK's
`MarketBBO` TypedDict -- which declares the fields FLAT and omits
`state` -- does not describe what the venue actually sends.

Corpus facts these tests rely on, measured over the whole archive:
30,590 BBO bodies returned HTTP 200; `sharesTraded` was present and
non-empty in every one of them; `bestBid`/`bestAsk` were present as
keys in every one and non-null in 23,914; and 6.35% of those bodies
pass the frozen selection rule unchanged.
"""
from __future__ import annotations

import asyncio
import time
import json

import pytest

from sportsassets import bettor_universe as uni
from sportsassets import bettor_universe_probe as probe


@pytest.fixture(autouse=True)
def unpaced(monkeypatch):
    """Most tests are about WHAT the probe reads, not how slowly.

    The default pace is 0.25 req/s, so a 6-market round is 24 seconds
    of real sleeping. Every test here runs unpaced EXCEPT the ones in
    TestTheRateLimitIsEnforced, which clear this and assert the real
    numbers.
    """
    monkeypatch.setenv(probe.RPS_ENV, "100000")
    monkeypatch.setenv(probe.CONC_ENV, "8")

# ── verbatim bodies ──────────────────────────────────────────────────
# Each is the `marketData` object of a real HTTP 200 /bbo response.

# A genuinely tradable book: OPEN, two-sided, 17 ticks wide, ask under
# the price cap, volume over the floor.
OPEN_WIDE = json.loads(
    '{"marketSlug":"aec-cfb-uwg-etnst-2026-09-19","currentPx":{"value":"0.3000","currency":"USD"},"lastTradePx":{"value":"0.2200","currency":"USD"},"settlementPx":{"value":"0.1200","currency":"USD"},"sharesTraded":"138.7700","openInterest":"137.9000","bestAsk":{"value":"0.3850","currency":"USD"},"bestBid":{"value":"0.2150","currency":"USD"},"askDepth":16,"bidDepth":10,"lastPriceSample":{"longPx":{"value":"0.2200","currency":"USD"},"shortPx":{"value":"0.78","currency":"USD"},"ts":"2026-09-13T16:55:29.569423158Z"},"longQuote":{"value":"0.3850","currency":"USD"},"shortQuote":{"value":"0.785","currency":"USD"},"state":"MARKET_STATE_OPEN"}'  # noqa: E501
)
# OPEN and two-sided, but one tick wide: a real ECONOMIC exclusion.
OPEN_TIGHT = json.loads(
    '{"marketSlug":"aec-cfb-portst-ore-2026-09-18","currentPx":{"value":"0.0070","currency":"USD"},"lastTradePx":{"value":"0.0050","currency":"USD"},"settlementPx":{"value":"0.0150","currency":"USD"},"sharesTraded":"61918.1100","openInterest":"64893.6500","bestAsk":{"value":"0.0100","currency":"USD"},"bestBid":{"value":"0.0050","currency":"USD"},"askDepth":33,"bidDepth":1,"lastPriceSample":{"longPx":{"value":"0.0050","currency":"USD"},"shortPx":{"value":"0.995","currency":"USD"},"ts":"2026-09-13T16:53:17.393434022Z"},"longQuote":{"value":"0.0100","currency":"USD"},"shortQuote":{"value":"0.995","currency":"USD"},"state":"MARKET_STATE_OPEN"}'  # noqa: E501
)
# OPEN with an ask and NO BID. The only payload here that deserves the
# word ONE_SIDED_BOOK, and the venue said so itself.
OPEN_NO_BID = json.loads(
    '{"marketSlug":"atc-lmx-ame-tij-2026-09-05-tij","currentPx":{"value":"0.0300","currency":"USD"},"lastTradePx":{"value":"0.0300","currency":"USD"},"settlementPx":{"value":"0.9000","currency":"USD"},"sharesTraded":"103.4200","openInterest":"1663.1400","bestAsk":{"value":"0.0200","currency":"USD"},"bestBid":null,"askDepth":1,"bidDepth":0,"lastPriceSample":{"longPx":{"value":"0.0300","currency":"USD"},"shortPx":{"value":"0.97","currency":"USD"},"ts":"2026-09-14T12:03:17.675919322Z"},"state":"MARKET_STATE_OPEN"}'  # noqa: E501
)
# EXPIRED: both quotes null, and `openInterest` an EMPTY STRING -- the
# venue really does send "" where a decimal would go.
EXPIRED = json.loads(
    '{"marketSlug":"asc-epl-mnu-mnc-2026-09-13-fh-neg-1pt5","currentPx":{"value":"0.0100","currency":"USD"},"lastTradePx":{"value":"0.0100","currency":"USD"},"settlementPx":{"value":"0.0000","currency":"USD"},"sharesTraded":"285.1800","openInterest":"","bestAsk":null,"bestBid":null,"askDepth":0,"bidDepth":0,"lastPriceSample":null,"state":"MARKET_STATE_EXPIRED"}'  # noqa: E501
)
# The 2026-09-05 venue-wide halt, from test_pmus_bbo_read's own pin:
# HTTP 200, both quotes null, state HALTED.
HALTED = json.loads(
    '{"marketSlug":"tsc-cfb-okst-tulsa-2026-09-05-total-55pt5","currentPx":{"value":"0.5700","currency":"USD"},"lastTradePx":{"value":"0.5700","currency":"USD"},"settlementPx":{"value":"0.5800","currency":"USD"},"sharesTraded":"399.1000","openInterest":"876.1900","bestAsk":null,"bestBid":null,"askDepth":0,"bidDepth":0,"state":"MARKET_STATE_HALTED"}'  # noqa: E501
)
# A CLOSED market whose `sharesTraded` is an EMPTY STRING. Same probe,
# run 2. This is why "BBO supplies traded shares" is checked and not
# assumed.
CLOSED_NO_SHARES = json.loads(
    '{"marketSlug":"astatc-cfb-cita-charlt-2026-09-05-dstd-3pt5","currentPx":{"value":"0.1000","currency":"USD"},"lastTradePx":null,"settlementPx":{"value":"0.0000","currency":"USD"},"sharesTraded":"","openInterest":"","bestAsk":{"value":"0.2000","currency":"USD"},"bestBid":{"value":"0.0100","currency":"USD"},"askDepth":7,"bidDepth":1,"state":"MARKET_STATE_CLOSED"}'  # noqa: E501
)

ALL = [OPEN_WIDE, OPEN_TIGHT, OPEN_NO_BID, EXPIRED, HALTED,
       CLOSED_NO_SHARES]


def wrapped(md):
    """What the venue really returns: the object inside `marketData`."""
    return {"marketData": md}


class FakeMarkets:
    def __init__(self, by_slug, *, raises=None, unwrapped=False):
        self.by_slug, self.raises, self.unwrapped = by_slug, raises, unwrapped
        self.calls = []

    def bbo(self, slug):
        self.calls.append(slug)
        if self.raises is not None:
            raise self.raises
        md = self.by_slug.get(slug)
        if md is None:
            return None
        return md if self.unwrapped else wrapped(md)


class FakeClient:
    def __init__(self, **kw):
        self.markets = FakeMarkets(**kw)


def client_for(payloads, **kw):
    return FakeClient(by_slug={p["marketSlug"]: p for p in payloads}, **kw)


def _spy(sink):
    """Records every sleep the round takes -- the 429 holds AND the
    pacer's own spacing, which is why `_holds` filters."""
    async def _sleep(seconds):
        sink.append(seconds)
    return _sleep


def _holds(slept):
    """The 429 holds, as the PACER served them. The hold is imposed by
    `Pacer.penalise` and paid at the next `acquire`, so it arrives as a
    pacer wait; ordinary spacing at the test's max_rps is
    sub-millisecond, so anything of a second or more is a penalty.
    Rounded because the wait is measured against a real clock."""
    return [round(s) for s in slept if s >= 1.0]


def _many(n):
    """n distinct markets, every body still verbatim."""
    out = []
    for i in range(n):
        md = dict(ALL[i % len(ALL)])
        md["marketSlug"] = "many-%03d" % i
        out.append(md)
    return out


def client_for_many(payloads):
    return client_for(payloads)


def cands(payloads):
    return probe.candidates_from_listing(
        [{"slug": p["marketSlug"], "outcome": "over", "active": True,
          "closed": False} for p in payloads])


# ── 1. the listing is identity, never judgement ──────────────────────

class TestTheListingIsIdentityOnly:
    """A missing field in a listing is not a one-sided market."""

    def test_a_listing_row_carries_no_quote_no_state_no_volume(self):
        """`MarketDetail`, exactly as the SDK declares it. This is the
        row that used to reach `assess` and be called ONE_SIDED_BOOK."""
        listing = {"id": 1, "slug": "nfl-kc-buf-2026-09-21",
                   "title": "KC vs BUF", "outcome": "KC", "active": True,
                   "closed": False, "liquidity": 48213.0,
                   "volume": 91240.0, "eventSlug": "nfl-kc-buf"}
        for absent in ("bestBid", "bestAsk", "sharesTraded", "state"):
            assert absent not in listing
        # the old path, reproduced: the rule blames the market
        assert uni.assess(listing)["reason"] == uni.R_ONE_SIDED

    def test_candidates_keep_identity_and_judge_nothing(self):
        out = probe.candidates_from_listing([
            {"slug": "b", "outcome": "no", "active": True},
            {"slug": "a", "outcome": "over", "active": True},
        ])
        assert [c["slug"] for c in out] == ["a", "b"], "sorted for rotation"
        assert out[0]["outcome_leg"] == "over", "outcome carried as the leg"
        # nothing economic was decided here
        assert all("bestBid" not in c and "included" not in c for c in out)

    def test_only_the_venues_own_closed_flags_drop_a_candidate(self):
        out = probe.candidates_from_listing([
            {"slug": "open", "active": True},
            {"slug": "shut", "closed": True},
            {"slug": "gone", "archived": True},
            {"slug": "inactive", "active": False},
            {"slug": None},
            {"slug": "unknown"},          # no `active` key at all
        ])
        slugs = [c["slug"] for c in out]
        assert slugs == ["open", "unknown"], (
            "absent `active` is unknown, and unknown is not a reason to "
            "drop a market before anyone has read its book")

    def test_duplicate_slugs_collapse(self):
        out = probe.candidates_from_listing(
            [{"slug": "x", "active": True}, {"slug": "x", "active": True}])
        assert len(out) == 1


# ── 2. the real payload, through the unchanged rule ──────────────────

class TestTheRealPayloadReachesTheRule:

    def test_the_wrapper_is_unwrapped(self):
        """Real bodies are `{"marketData": {...}}`. The SDK's MarketBBO
        declares the fields flat, so reading the declared shape would
        find nothing at all."""
        assert probe._market_data(wrapped(OPEN_WIDE)) is OPEN_WIDE
        assert probe._market_data(OPEN_WIDE) is None, "flat is not an answer"
        assert probe._market_data({"marketData": "nope"}) is None
        assert probe._market_data(None) is None

    def test_a_real_open_book_is_included(self):
        a = uni.assess(probe.row_from_bbo(OPEN_WIDE["marketSlug"],
                                          OPEN_WIDE, outcome_leg="over"))
        assert a["included"] is True and a["reason"] is None
        assert a["bid"] == 0.215 and a["ask"] == 0.385
        assert a["shares_traded"] == 138.77
        assert a["state"] == "MARKET_STATE_OPEN"
        assert a["outcome_leg"] == "over", "carried, not filtered on"

    def test_each_real_payload_gets_the_reason_it_deserves(self):
        """The point is not that things are excluded -- it is that the
        REASON is now about the market rather than about our wiring."""
        got = {}
        for md in ALL:
            row = probe.row_from_bbo(md["marketSlug"], md, outcome_leg="x")
            a = uni.assess(row)
            got[md["marketSlug"]] = ("INCLUDED" if a["included"]
                                     else a["reason"])
        assert got[OPEN_WIDE["marketSlug"]] == "INCLUDED"
        assert got[OPEN_TIGHT["marketSlug"]] == uni.R_SPREAD
        assert got[OPEN_NO_BID["marketSlug"]] == uni.R_ONE_SIDED
        assert got[EXPIRED["marketSlug"]] == uni.R_NOT_OPEN
        assert got[HALTED["marketSlug"]] == uni.R_NOT_OPEN
        # empty sharesTraded is MISSING, and missing is not zero
        assert got[CLOSED_NO_SHARES["marketSlug"]] == uni.R_NOT_OPEN

    def test_one_sided_now_means_the_venue_sent_no_bid(self):
        assert OPEN_NO_BID["bestBid"] is None
        assert OPEN_NO_BID["bestAsk"] is not None
        a = uni.assess(probe.row_from_bbo("s", OPEN_NO_BID))
        assert a["reason"] == uni.R_ONE_SIDED
        assert a["ask"] == 0.02 and a["bid"] is None

    def test_an_empty_shares_string_is_missing_not_zero(self):
        """The venue really sends `""`. `NO_TRADED_VOLUME_FIGURE` and
        `TRADED_VOLUME_BELOW_MIN` are counted apart, and an unreadable
        counter must not be reported as a market that never traded."""
        assert CLOSED_NO_SHARES["sharesTraded"] == ""
        row = probe.row_from_bbo("s", CLOSED_NO_SHARES)
        # take the state out of the way so the volume branch is reached
        row["venue_state"] = "MARKET_STATE_OPEN"
        row["bestBid"] = {"value": "0.2000"}
        row["bestAsk"] = {"value": "0.4000"}
        a = uni.assess(row)
        assert a["shares_traded"] is None
        assert a["reason"] == uni.R_NO_VOLUME
        assert a["reason"] != uni.R_VOLUME

    def test_prices_stay_as_the_venues_amount_objects(self):
        """`assess` unwraps `value` itself; re-formatting here would be
        a second place for a rounding decision to live."""
        row = probe.row_from_bbo("s", OPEN_WIDE)
        assert row["bestBid"] == {"value": "0.2150", "currency": "USD"}
        assert row["sharesTraded"] == "138.7700", "verbatim"

    def test_depth_is_carried_as_the_level_count_it_is(self):
        row = probe.row_from_bbo("s", OPEN_WIDE)
        assert row["bidDepth"] == 10 and row["askDepth"] == 16
        assert uni.assess(row)["top_of_book_qty"] == 10.0


# ── 3. bounded, deterministic acquisition ────────────────────────────

class TestAcquisitionIsBoundedAndDeterministic:

    def test_a_round_reads_only_its_batch(self):
        c = client_for(ALL)
        out = asyncio.run(probe.probe(c, cands(ALL), offset=0, batch=2))
        assert out["probed"] == 2
        assert len(c.markets.calls) == 2, "one BBO call per market, no more"
        assert out["coverage"]["candidates"] == 6

    def test_rotation_is_deterministic_and_covers_everything(self):
        candidates = cands(ALL)
        seen, off = [], 0
        for _ in range(3):
            c = client_for(ALL)
            out = asyncio.run(probe.probe(c, candidates, offset=off, batch=2))
            seen.extend(out["slugs_probed"])
            off = out["next_offset"]
        assert sorted(seen) == sorted(x["marketSlug"] for x in ALL)
        assert len(seen) == len(set(seen)), "no market read twice in a sweep"
        assert off == 0, "the cursor wraps"

    def test_the_same_input_always_probes_in_the_same_order(self):
        candidates = cands(ALL)
        runs = []
        for _ in range(2):
            c = client_for(ALL)
            runs.append(asyncio.run(
                probe.probe(c, candidates, offset=3, batch=4))["slugs_probed"])
        assert runs[0] == runs[1]

    def test_a_batch_larger_than_the_candidates_reads_each_once(self):
        c = client_for(ALL)
        out = asyncio.run(probe.probe(c, cands(ALL), offset=0, batch=999))
        assert out["probed"] == 6 and len(set(c.markets.calls)) == 6

    def test_no_candidates_is_not_an_error(self):
        out = asyncio.run(probe.probe(client_for([]), []))
        assert out["rows"] == [] and out["coverage"]["candidates"] == 0


# ── 4. coverage is not eligibility ───────────────────────────────────

class TestCoverageIsReportedApartFromEligibility:
    """'We have not looked yet' and 'we looked and it did not qualify'
    are different facts, and only the second one is about the market."""

    def test_a_read_failure_is_coverage_never_a_rule_reason(self):
        c = client_for(ALL, raises=TimeoutError("venue"))
        out = asyncio.run(probe.probe(c, cands(ALL), batch=3))
        assert out["rows"] == []
        assert out["coverage"]["by_status"][probe.P_READ_FAILED] == 3
        assert out["coverage"]["enriched"] == 0
        # nothing resembling a selection reason appears in coverage
        assert uni.R_ONE_SIDED not in out["coverage"]["by_status"]

    def test_a_response_without_market_data_is_named_as_such(self):
        c = client_for(ALL, unwrapped=True)
        out = asyncio.run(probe.probe(c, cands(ALL), batch=2))
        assert out["coverage"]["by_status"][probe.P_NO_PAYLOAD] == 2
        assert out["rows"] == []

    def test_an_absent_bbo_method_fails_the_read_not_the_market(self):
        class NoBBO:
            markets = object()
        out = asyncio.run(probe.probe(NoBBO(), cands(ALL), batch=1))
        assert out["coverage"]["by_status"][probe.P_READ_FAILED] == 1

    def test_a_slow_read_times_out_without_holding_the_round(self):
        import time as _t

        class Slow(FakeMarkets):
            def bbo(self, slug):
                _t.sleep(0.4)
                return wrapped(OPEN_WIDE)

        c = FakeClient(by_slug={})
        c.markets = Slow(by_slug={})
        out = asyncio.run(probe.probe(c, cands(ALL), batch=2,
                                      timeout_s=0.05))
        assert out["coverage"]["by_status"][probe.P_TIMEOUT] == 2

    def test_coverage_merges_across_rounds(self):
        a = {"candidates": 6, "probed": 2, "enriched": 1,
             "by_status": {probe.P_OK: 1, probe.P_READ_FAILED: 1}}
        b = {"candidates": 6, "probed": 2, "enriched": 2,
             "by_status": {probe.P_OK: 2}}
        m = probe.merge_coverage(a, b)
        assert m["probed"] == 4 and m["enriched"] == 3
        assert m["by_status"] == {probe.P_OK: 3, probe.P_READ_FAILED: 1}
        assert m["candidates"] == 6, "not summed"

    def test_partial_failure_still_enriches_what_answered(self):
        class Flaky(FakeMarkets):
            def bbo(self, slug):
                self.calls.append(slug)
                if slug == OPEN_WIDE["marketSlug"]:
                    return wrapped(OPEN_WIDE)
                raise ConnectionError("nope")

        c = FakeClient(by_slug={})
        c.markets = Flaky(by_slug={})
        out = asyncio.run(probe.probe(c, cands(ALL), batch=6))
        assert out["coverage"]["enriched"] == 1
        assert out["coverage"]["by_status"][probe.P_READ_FAILED] == 5
        assert uni.select(out["rows"])["selected"] == 1


# ── 5. the selection rule itself is untouched ────────────────────────

class TestTheFrozenRuleIsNotChanged:

    def test_the_probe_never_supplies_the_listings_volume(self):
        """`MarketDetail.volume` is not `sharesTraded`. Substituting it
        would silently change a frozen rule, so it is not carried into
        an enriched row at all."""
        import inspect
        src = inspect.getsource(probe)
        assert '"volume"' not in src and "'volume'" not in src

    def test_the_rule_parameters_are_the_published_ones(self):
        assert uni.MIN_SPREAD_TICKS == 2
        assert uni.MAX_PRICE == 0.50
        assert uni.MIN_SHARES_TRADED == 100.0
        assert uni.MAX_MARKETS == 100
        assert uni.UNIVERSE_VERSION == "BETTOR_UNIVERSE_V1"

    def test_selection_ranks_by_volume_over_real_rows(self):
        rows = [probe.row_from_bbo(m["marketSlug"], m, outcome_leg="x")
                for m in ALL]
        sel = uni.select(rows)
        assert sel["selected"] == 1
        assert sel["slugs"] == [OPEN_WIDE["marketSlug"]]
        assert sel["excluded_by_reason"][uni.R_ONE_SIDED] == 1, (
            "exactly the one payload whose bestBid the venue sent null")

    def test_the_probe_imports_no_order_path(self):
        import inspect
        src = inspect.getsource(probe)
        for forbidden in ("submit_fok", "close_position", "place_order",
                          "cancel_order"):
            assert forbidden not in src


# ── 6. the rate limit ────────────────────────────────────────────────

class TestTheRateLimitIsEnforced:
    """THE POINT: eight concurrent requests is not a rate limit. It is
    a concurrency ceiling with no bound on the rate behind it.

    What the venue publishes is NOTHING -- no RateLimit or Retry-After
    header in any of 65,980 captured responses, and no HTTP 429. What
    is DEMONSTRATED is that all 61,178 successful reads were taken
    strictly serially: the densest one-second window in the whole
    archive holds exactly one request, at 0.030-0.285 req/s sustained.
    That envelope is the default."""

    @pytest.fixture(autouse=True)
    def repaced(self, monkeypatch):
        monkeypatch.delenv(probe.RPS_ENV, raising=False)
        monkeypatch.delenv(probe.CONC_ENV, raising=False)

    def test_the_default_is_the_demonstrated_envelope(self):
        p = probe.configured_pace()
        assert p["max_rps"] <= 0.285, "above anything ever observed"
        assert p["concurrency"] == 1, "the capture never had two in flight"
        assert p["above_demonstrated_envelope"] is False

    def test_an_override_above_the_envelope_is_flagged(self, monkeypatch):
        monkeypatch.setenv(probe.RPS_ENV, "5")
        assert probe.configured_pace()["above_demonstrated_envelope"] is True
        monkeypatch.setenv(probe.RPS_ENV, "0.2")
        monkeypatch.setenv(probe.CONC_ENV, "8")
        assert probe.configured_pace()["above_demonstrated_envelope"] is True

    def test_a_nonsense_override_falls_back_to_the_default(self,
                                                           monkeypatch):
        monkeypatch.setenv(probe.RPS_ENV, "fast please")
        assert probe.configured_pace()["max_rps"] == probe.PROBE_MAX_RPS

    def test_the_pacer_spaces_request_starts(self):
        async def go():
            pacer = probe.Pacer(20.0)          # 50 ms apart
            t0 = time.monotonic()
            for _ in range(5):
                await pacer.acquire()
            return time.monotonic() - t0, pacer.grants

        elapsed, grants = asyncio.run(go())
        assert grants == 5
        # four intervals of 50 ms, allowing scheduler slack
        assert elapsed >= 0.18, "%.3fs is faster than the ceiling" % elapsed

    def test_the_rate_is_of_the_round_not_of_each_task(self):
        """A per-task limiter multiplies by concurrency and is not a
        limit at all."""
        async def go():
            pacer = probe.Pacer(20.0)
            t0 = time.monotonic()
            await asyncio.gather(*[pacer.acquire() for _ in range(5)])
            return time.monotonic() - t0

        assert asyncio.run(go()) >= 0.18

    def test_a_real_round_obeys_the_ceiling(self):
        c = client_for(ALL)
        t0 = time.monotonic()
        out = asyncio.run(probe.probe(c, cands(ALL), batch=4, max_rps=25.0))
        elapsed = time.monotonic() - t0
        assert out["accounting"]["attempts"] == 4
        assert elapsed >= 0.12, "4 reads at 25 req/s cannot take %.3fs" % elapsed

    def test_the_pace_is_reported_with_the_result(self):
        out = asyncio.run(probe.probe(client_for(ALL), cands(ALL), batch=1,
                                      max_rps=1000.0))
        assert out["pace"]["demonstrated_concurrency"] == 1
        assert "61,178" in out["pace"]["envelope_evidence"]

    def test_describe_states_that_the_limit_is_unknown(self):
        rl = probe.describe()["rate_limit"]
        assert rl["applicable_limit"] == "UNKNOWN"
        assert "NONE" in rl["published_by_venue"]
        assert "UNVERIFIED" in rl["credentials_caveat"]


# ── 7. HTTP 429 ──────────────────────────────────────────────────────

class Rate429(Exception):
    """An SDK RateLimitError, as it really arrives: an exception
    carrying `status_code` and the response it came from."""

    def __init__(self, retry_after=None):
        super().__init__("429")
        self.status_code = 429
        self.response = type("R", (), {
            "status_code": 429,
            "headers": {} if retry_after is None
            else {"retry-after": str(retry_after)}})()


class Limited(FakeMarkets):
    def __init__(self, fail_times, retry_after=None, **kw):
        super().__init__(**kw)
        self.fail_times, self.retry_after = fail_times, retry_after
        self.seen = {}

    def bbo(self, slug):
        self.calls.append(slug)
        self.seen[slug] = self.seen.get(slug, 0) + 1
        if self.seen[slug] <= self.fail_times:
            raise Rate429(self.retry_after)
        return wrapped(OPEN_WIDE)


class TestRateLimitResponses:
    """Never observed in 65,980 responses, and handled anyway: a limit
    nobody has hit is still a limit."""

    def _client(self, **kw):
        c = FakeClient(by_slug={})
        c.markets = Limited(by_slug={}, **kw)
        return c

    def test_a_429_is_retried_and_then_succeeds(self):
        c = self._client(fail_times=1)
        slept = []
        out = asyncio.run(probe.probe(
            c, cands([OPEN_WIDE]), batch=1, max_rps=1000.0,
            sleep=_spy(slept)))
        a = out["accounting"]
        assert a["attempts"] == 2 and a["retries"] == 1
        assert a["rate_limited"] == 1 and a["enriched"] == 1
        assert _holds(slept) == [round(probe.PROBE_RETRY_BACKOFF_S[0])]

    def test_retry_after_is_honoured_over_our_schedule(self):
        c = self._client(fail_times=1, retry_after=7)
        slept = []
        asyncio.run(probe.probe(c, cands([OPEN_WIDE]), batch=1,
                                max_rps=1000.0, sleep=_spy(slept)))
        assert _holds(slept) == [7], "the server's number beats ours"

    def test_retry_after_is_capped(self):
        c = self._client(fail_times=1, retry_after=99999)
        slept = []
        asyncio.run(probe.probe(c, cands([OPEN_WIDE]), batch=1,
                                max_rps=1000.0, sleep=_spy(slept)))
        assert _holds(slept) == [round(probe.PROBE_RETRY_AFTER_CAP_S)], (
            "a server-supplied delay is better than ours right up until "
            "it is an hour")

    def test_retries_are_bounded_and_the_market_is_named_not_blamed(self):
        c = self._client(fail_times=99)

        out = asyncio.run(probe.probe(c, cands([OPEN_WIDE]), batch=1,
                                      max_rps=1000.0, sleep=_spy([])))
        a = out["accounting"]
        assert a["attempts"] == probe.PROBE_MAX_RETRIES + 1
        assert a["retries"] == probe.PROBE_MAX_RETRIES
        assert a["enriched"] == 0
        assert a["by_status"][probe.P_RATE_LIMITED] == 1
        # a rate limit is a COVERAGE fact, never a verdict on the book
        assert uni.R_ONE_SIDED not in a["by_status"]

    def test_a_429_slows_the_whole_round_not_one_task(self):
        pacer = probe.Pacer(1000.0)
        before = pacer._next_at
        pacer.penalise(30.0)
        assert pacer._next_at > before + 25

    def test_a_non_429_status_is_not_treated_as_a_rate_limit(self):
        class Boom(Exception):
            status_code = 500

        c = FakeClient(by_slug={})
        c.markets = FakeMarkets(by_slug={}, raises=Boom())
        out = asyncio.run(probe.probe(c, cands([OPEN_WIDE]), batch=1,
                                      max_rps=1000.0))
        a = out["accounting"]
        assert a["by_status"][probe.P_READ_FAILED] == 1
        assert a["retries"] == 0, "only a 429 is retried"


# ── 8. stopping during acquisition ───────────────────────────────────

class TestStoppingDuringAcquisition:
    """A stop that only lands between rounds waits out every
    outstanding read. At the demonstrated pace that is minutes."""

    def test_a_stop_abandons_the_rest_of_the_batch(self):
        """60 markets, stopped at the first check. Without this the
        round runs to the end of its batch -- minutes, at the
        demonstrated pace."""
        many = _many(60)
        c = client_for_many(many)
        calls = {"n": 0}

        async def should_stop():
            calls["n"] += 1
            return True                      # stop at the first check

        out = asyncio.run(probe.probe(c, cands(many), batch=60,
                                      max_rps=1000.0, concurrency=1,
                                      should_stop=should_stop))
        assert out["stopped"] is True
        a = out["accounting"]
        assert a["attempts"] <= probe.PROBE_STOP_CHECK_EVERY + 1, (
            "read %d of 60 after the stop" % a["attempts"])
        assert a["by_status"].get(probe.P_STOPPED, 0) >= 50
        assert a["enriched"] == a["by_status"].get(probe.P_OK, 0), (
            "what was read before the stop is still kept")

    def test_the_check_is_throttled_so_it_is_not_a_query_per_read(self):
        """PROBE_STOP_CHECK_EVERY reads per call. The caller's callback
        also caches, so the database sees far fewer than this."""
        many = _many(20)
        c = client_for_many(many)
        checks = {"n": 0}

        async def should_stop():
            checks["n"] += 1
            return False

        asyncio.run(probe.probe(c, cands(many), batch=20, max_rps=1000.0,
                                should_stop=should_stop, concurrency=1))
        assert checks["n"] == 20 // probe.PROBE_STOP_CHECK_EVERY == 4

    def test_an_unanswerable_stop_check_stops(self):
        """Same rule as the control itself: we cannot show we are still
        permitted, so we are not."""
        c = client_for(ALL)

        async def should_stop():
            raise ConnectionError("db went away")

        many = _many(20)
        out = asyncio.run(probe.probe(client_for_many(many), cands(many),
                                      batch=20, max_rps=1000.0,
                                      should_stop=should_stop,
                                      concurrency=1))
        assert out["stopped"] is True

    def test_no_callback_means_no_checks_and_no_crash(self):
        out = asyncio.run(probe.probe(client_for(ALL), cands(ALL),
                                      batch=2, max_rps=1000.0))
        assert out["stopped"] is False


# ── 9. the counts reconcile ──────────────────────────────────────────

class TestTheAcquisitionCountsReconcile:
    """THE DEFECT: the report said 400 candidates enriched -- and 480
    reads. De-duplicating by slug fixed the COUNTS and left the WASTE:
    two rounds of 240 over 400 candidates had the second round cross
    the end of the list and re-read the first 80 markets. The venue
    still received 480 requests. Those 80 were the overlap."""

    def test_the_old_overlap_is_reproduced_without_the_clamp(self):
        many = _many(400)
        c = client_for_many(many)
        cs = cands(many)
        swept, off = 0, 0
        for _ in range(2):
            out = asyncio.run(probe.probe(c, cs, offset=off, batch=240,
                                          max_rps=1000.0))
            off = out["next_offset"]
            swept += out["probed"]
        assert swept == 480, "the shape of the old report"
        assert len(c.markets.calls) == 480
        assert len(set(c.markets.calls)) == 400, (
            "400 distinct markets, 480 requests: the extra 80 are the "
            "second round wrapping past the end")

    def test_the_clamp_removes_exactly_those_eighty(self):
        many = _many(400)
        c = client_for_many(many)
        cs = cands(many)
        swept, off = 0, 0
        for _ in range(2):
            out = asyncio.run(probe.probe(c, cs, offset=off, batch=240,
                                          max_rps=1000.0,
                                          remaining=400 - swept))
            off = out["next_offset"]
            swept += out["probed"]
        assert swept == 400
        assert len(c.markets.calls) == 400, "no market is read twice"
        assert len(set(c.markets.calls)) == 400

    def test_the_four_numbers_are_reported_separately(self):
        """ATTEMPTS, RETRIES, DISTINCT MARKETS and ENRICHED are four
        different things and an earlier version had one number."""
        c = FakeClient(by_slug={})
        c.markets = Limited(by_slug={}, fail_times=1)
        out = asyncio.run(probe.probe(c, cands(_many(3)), batch=3,
                                      max_rps=1000.0, sleep=_spy([])))
        a = out["accounting"]
        assert a["distinct_markets"] == 3, "markets asked about"
        assert a["attempts"] == 6, "HTTP requests issued, retries included"
        assert a["retries"] == 3, "of those, retries"
        assert a["rate_limited"] == 3, "of those, refused with 429"
        assert a["enriched"] == 3, "rows the rule can judge"
        # attempts = distinct + retries, always
        assert a["attempts"] == a["distinct_markets"] + a["retries"]

    def test_merge_sums_counts_and_carries_the_set_size(self):
        many = _many(10)
        c = client_for_many(many)
        cs = cands(many)
        cov = {}
        for off in (0, 5):
            out = asyncio.run(probe.probe(c, cs, offset=off, batch=5,
                                          max_rps=1000.0, remaining=5))
            cov = probe.merge_coverage(cov, out["coverage"])
        assert cov["candidates"] == 10, "the set size, not a running total"
        assert cov["probed"] == 10 and cov["distinct_markets"] == 10
        assert cov["attempts"] == 10 and cov["retries"] == 0
        assert cov["enriched"] == 10

    def test_a_batch_wider_than_the_remainder_is_clamped(self):
        many = _many(10)
        c = client_for_many(many)
        out = asyncio.run(probe.probe(c, cands(many), offset=7, batch=99,
                                      max_rps=1000.0, remaining=3))
        assert out["probed"] == 3
        assert len(c.markets.calls) == 3
