"""CAPITAL-CRITICAL: A HELD POSITION CONSUMES A GENUINELY CURRENT PROVIDER
FRAME -- AND NOTHING ELSE.

Owner closeout (2026-10-07): "if PinnAPI is producing genuinely current
frames or authoritative provider timestamps and Xavier is failing to
consume/persist them, that is a software defect ... A new poll time is never
freshness." Production: 22 of 30 held positions refused
FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE while the feed held their prices
re-asserted by provider-stamped frames.

`FeedCache.read_held` (held reads only; `read` is unchanged):
  * a price re-carried by a PROVIDER-STAMPED authoritative list / matchup
    version / live record within the SAME 30 s limit is current, its age
    measured from that provider stamp;
  * a confirmation older than 30 s, one dated only by our receipt, and the
    subscribe snapshot (the provider's stored mirror) are NOT;
  * every other refusal (authority, epoch, closed, unknown) is unchanged."""
from __future__ import annotations

import copy

from sportsassets import bettor_market_family as MF
from sportsassets import pinnapi_feed as F

ML = {"key": "s;0;m", "type": "moneyline", "period": 0, "status": "open",
      "prices": [{"designation": "home", "price": -120},
                 {"designation": "away", "price": 105}]}


def _snap(c, ep, ts=1_000_000):
    c.apply({"type": "snapshot", "stream": "prematch", "sport_id": 5,
             "ts": ts, "events": [{"id": 9, "version": 7, "markets": [ML]}]},
            epoch=ep, received_ms=ts + 20)


def _cache():
    c = F.FeedCache()
    ep = c.new_connection([("prematch", 5)])
    _snap(c, ep)
    return c, ep


def test_a_provider_stamped_matchup_confirmation_within_30s_is_current():
    c, ep = _cache()
    c.apply({"type": "prematch_markets", "sport_id": 5, "matchup_id": 9,
             "ts": 1_005_000, "data": [dict(ML, version=7)]}, epoch=ep,
            received_ms=1_005_030)
    c.apply({"type": "prematch_matchups", "sport_id": 5, "ts": 1_010_000,
             "data": [{"id": 9, "version": 7}]}, epoch=ep,
            received_ms=1_010_010)
    # the change rule still refuses (no change observed) ...
    assert c.read(9, "s;0;m", evaluated_ms=1_020_000)["reason"] == \
        F.R_NO_CHANGE_TIME
    # ... the held read consumes the provider's own current frame
    r = c.read_held(9, "s;0;m", evaluated_ms=1_020_000, max_age_s=30.0)
    assert r["ok"] is True
    p = r["provenance"]
    assert p["freshness_basis"] == F.FRESHNESS_BASIS_CONFIRMED
    assert p["freshness_at_ms"] == 1_010_000
    assert p["quote_age_s"] == 10.0
    assert p["change_rule_refusal"] == F.R_NO_CHANGE_TIME
    assert p["confirmed_by"] == F.C_MATCHUP_VERSION
    assert p["confirmed_clock"] == F.CLOCK_PROVIDER
    assert p["change_ms"] is None, "the change clock is never invented"


def test_the_same_30s_limit_applies_to_the_confirmation():
    c, ep = _cache()
    c.apply({"type": "prematch_markets", "sport_id": 5, "matchup_id": 9,
             "ts": 1_005_000, "data": [dict(ML, version=7)]}, epoch=ep,
            received_ms=1_005_030)
    r = c.read_held(9, "s;0;m", evaluated_ms=1_035_001, max_age_s=30.0)
    assert r["ok"] is False and r["reason"] == F.R_NO_CHANGE_TIME


def test_the_subscribe_snapshot_alone_is_never_a_current_frame():
    c, ep = _cache()
    r = c.read_held(9, "s;0;m", evaluated_ms=1_000_500, max_age_s=30.0)
    assert r["ok"] is False and r["reason"] == F.R_NO_CHANGE_TIME
    # a re-subscribe snapshot re-asserting the same price is the provider's
    # stored mirror: still not admitted
    _snap(c, ep, ts=1_002_000)
    r = c.read_held(9, "s;0;m", evaluated_ms=1_002_500, max_age_s=30.0)
    assert r["ok"] is False


def test_a_confirmation_dated_only_by_our_receipt_is_never_freshness():
    c, ep = _cache()
    # an authoritative list with NO provider stamp: confirmed on OUR clock
    c.apply({"type": "prematch_markets", "sport_id": 5, "matchup_id": 9,
             "data": [dict(ML, version=7)]}, epoch=ep, received_ms=1_005_000)
    q = c.quotes[(9, "s;0;m")]
    assert q.confirmed_clock == F.CLOCK_LOCAL
    r = c.read_held(9, "s;0;m", evaluated_ms=1_006_000, max_age_s=30.0)
    assert r["ok"] is False


def test_an_old_change_reconfirmed_by_a_live_record_is_current():
    c, ep = _cache()
    moved = copy.deepcopy(ML)
    moved["prices"][0]["price"] = -130
    c.apply({"type": "prematch_markets", "sport_id": 5, "matchup_id": 9,
             "ts": 1_005_000, "data": [dict(moved, version=8)]}, epoch=ep,
            received_ms=1_005_010)
    assert c.read(9, "s;0;m", evaluated_ms=1_100_000,
                  max_age_s=30.0)["reason"] == F.R_STALE
    c.apply({"type": "live", "op": "upd", "sport_id": 5, "ts": 1_090_000,
             "rec": {"id": 9, "markets": [moved]}}, epoch=ep,
            received_ms=1_090_015)
    r = c.read_held(9, "s;0;m", evaluated_ms=1_100_000, max_age_s=30.0)
    assert r["ok"] is True
    assert r["provenance"]["freshness_at_ms"] == 1_090_000
    assert r["provenance"]["change_ms"] == 1_005_000
    assert r["provenance"]["change_rule_refusal"] == F.R_STALE


def test_other_refusals_are_unchanged():
    c, ep = _cache()
    assert c.read_held(9, "nope")["reason"] == F.R_UNKNOWN_MARKET
    c.new_connection([("prematch", 5)])          # epoch moves on
    assert c.read_held(9, "s;0;m")["ok"] is False


def test_the_entry_path_keeps_the_change_rule():
    """`read` (entries, discovery, every non-held reader) is unchanged; the
    line reader admits a confirmation only when called for a held position."""
    import inspect
    src = inspect.getsource(MF._pinnacle_pair)
    assert "read_held" in src and "if held" in src
    assert inspect.signature(MF.pinnacle_pair).parameters["held"].default \
        is False
