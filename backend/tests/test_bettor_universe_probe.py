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
import json

from sportsassets import bettor_universe as uni
from sportsassets import bettor_universe_probe as probe

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
