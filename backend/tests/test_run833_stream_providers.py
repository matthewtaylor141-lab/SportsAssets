"""RUN 83.3 -- the two stream providers, against synthetic frames only.

NO CONNECTION, AND NO RECORDED CAPTURE EITHER. Run 83.2A established that this
session cannot reach any Polymarket host (403 at the proxy on every one), so
there is no passive capture to replay and none is faked. What these frames are:
SYNTHETIC PAYLOADS SHAPED FROM THE SDK'S OWN TYPE DEFINITIONS. They prove the
decoder and the state machine behave as designed; they do NOT establish venue
semantics, and no test here asserts one.

The two questions this file keeps open on purpose, because the evidence to close
them does not exist yet:

    PMUS_FULL_REPLACEMENT_UNCONFIRMED
    CLOB_PRICE_CHANGE_SEMANTICS_UNRESOLVED

Several tests below assert that the code REFUSES rather than answers. Those are
the important ones. A test suite that made the refusals go away would be
recording a decision nobody is entitled to make yet.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from sportsassets.obs import clob_stream, clock, pmus_stream, streamstate
from sportsassets.obs.streamstate import (
    DepthAuthority,
    SelectionOutcome,
    StateValidity,
    StreamChannel,
)


def _at(base: clock.Instant, offset_s: float) -> clock.Instant:
    return clock.Instant(monotonic=base.monotonic + offset_s,
                         wall=base.wall + timedelta(seconds=offset_s),
                         process_boot_id=base.process_boot_id,
                         process_identity=base.process_identity)


def _pmus_frame(slug="aec-nfl-ne-sea", bid=0.40, ask=0.42, transact="1700000000000"):
    return {"marketData": {
        "marketSlug": slug,
        "bids": [{"px": str(bid), "qty": "500"}, {"px": "0.39", "qty": "1200"}],
        "offers": [{"px": str(ask), "qty": "400"}, {"px": "0.43", "qty": "900"}],
        "state": "OPEN", "stats": {}, "transactTime": transact}}


def _clob_book(token="tok1", bid=0.40, ask=0.42, hash_="h1", ts="1700000000123"):
    return {"asset_id": token, "market": "m",
            "bids": [{"price": str(bid), "size": "500"}],
            "asks": [{"price": str(ask), "size": "400"}],
            "hash": hash_, "timestamp": ts}


# ======================================================================
# PMUS
# ======================================================================
def test_pmus_decodes_the_sdk_payload_shape():
    base = clock.now()
    st = pmus_stream.decode_market_data(_pmus_frame(), receive=base,
                                        feed_session_id="s1")
    assert st is not None
    assert st.channel == StreamChannel.PMUS_FAST_STREAM_PATH
    assert st.token_id == "aec-nfl-ne-sea"
    assert st.best_bid == 0.40 and st.best_ask == 0.42
    assert st.receive is base


def test_pmus_state_is_never_promoted_to_full_replacement_by_a_type_signature():
    """The finding that a list of levels is not evidence of a full book."""
    st = pmus_stream.decode_market_data(_pmus_frame(), receive=clock.now(),
                                        feed_session_id="s1")
    assert st.depth_authority == DepthAuthority.TOP_OF_BOOK_ONLY
    assert pmus_stream.FULL_REPLACEMENT_STATUS == "PMUS_FULL_REPLACEMENT_UNCONFIRMED"
    assert st.bids and st.asks, "levels are recorded even though depth is unconfirmed"


def test_pmus_refuses_a_depth_weighted_price():
    """Q_A is the PRIMARY size measure, so a partial-depth Q_A would be the
    worst kind of wrong: indistinguishable from a correct one in the table."""
    st = pmus_stream.decode_market_data(_pmus_frame(), receive=clock.now(),
                                        feed_session_id="s1")
    with pytest.raises(streamstate.DepthUnavailable) as exc:
        st.vwap(q=100.0)
    assert "TOP_OF_BOOK_ONLY" in str(exc.value)
    # Top of book, however, IS recordable -- the refusal is scoped to depth.
    assert st.best_ask == 0.42


def test_pmus_continuity_can_never_be_verified_from_the_message_stream():
    st = pmus_stream.decode_market_data(_pmus_frame(), receive=clock.now(),
                                        feed_session_id="s1")
    assert st.continuity == streamstate.Continuity.UNVERIFIED
    # No hash and no sequence arrive, so nothing could detect a gap.
    assert st.venue_book_hash is None and st.venue_sequence is None


def test_pmus_transact_time_is_provenance_and_stays_a_string():
    st = pmus_stream.decode_market_data(
        _pmus_frame(transact="1700000000000"), receive=clock.now(),
        feed_session_id="s1")
    assert st.venue_timestamp_raw == "1700000000000"
    assert isinstance(st.venue_timestamp_raw, str)


def test_pmus_history_keeps_every_frame_so_a_past_anchor_can_be_answered():
    """A latest-value cell would answer the wrong question under burst.

    At RN1's measured p99 of 10 fills in one second, a frame landing between
    receipt and sampling is the normal case, not a race.
    """
    base = clock.now()
    s = pmus_stream.PmusStreamState(feed_session_id="s1")
    s.on_frame(_pmus_frame(bid=0.40), receive=_at(base, 0.0))
    s.on_frame(_pmus_frame(bid=0.55), receive=_at(base, 0.200))

    sel = streamstate.select_state_at(s.history("aec-nfl-ne-sea"),
                                      _at(base, 0.100), stale_tolerance_s=5.0)
    assert sel.selected and sel.state.best_bid == 0.40
    assert sel.state_age_at_receipt_ms == pytest.approx(100.0, abs=1e-6)


def test_pmus_disconnect_invalidates_retained_state_rather_than_deleting_it():
    """CACHE_INVALID with a reason is a result; CACHE_MISS would be a lie.

    The SDK ships no reconnect logic and there is no sequence number, so a drop
    is silent and a resumed stream is indistinguishable from a gapped one.
    Carrying pre-disconnect state forward would let a sample be answered from a
    book that may have moved arbitrarily far while nobody was listening.
    """
    base = clock.now()
    s = pmus_stream.PmusStreamState(feed_session_id="s1")
    s.on_frame(_pmus_frame(), receive=_at(base, 0.0))
    assert s.on_disconnect(receive=_at(base, 0.5)) == 1

    sel = streamstate.select_state_at(s.history("aec-nfl-ne-sea"),
                                      _at(base, 0.100), stale_tolerance_s=5.0)
    assert sel.outcome == SelectionOutcome.CACHE_INVALID
    assert StateValidity.INVALIDATED_BY_DISCONNECT in sel.reason
    # The age is still reported: the slot is resolved, not abandoned.
    assert sel.state_age_at_receipt_ms == pytest.approx(100.0, abs=1e-6)


def test_pmus_malformed_levels_are_skipped_not_zero_filled():
    frame = {"marketData": {"marketSlug": "m",
                            "bids": [{"px": "0.4", "qty": "10"},
                                     {"px": None, "qty": "5"},
                                     {"px": "oops", "qty": "1"}],
                            "offers": []}}
    st = pmus_stream.decode_market_data(frame, receive=clock.now(),
                                        feed_session_id="s")
    assert st.bids == [(0.4, 10.0)]
    assert st.best_ask is None


def test_pmus_subscribes_by_slug_and_clob_by_token_and_they_are_not_the_same():
    assert pmus_stream.SUBSCRIBE_BY == "marketSlug"
    assert clob_stream.SUBSCRIBE_BY == "assets_ids"
    assert pmus_stream.SUBSCRIBE_BY != clob_stream.SUBSCRIBE_BY


# ======================================================================
# CLOB
# ======================================================================
def test_clob_full_book_carries_authoritative_depth():
    st = clob_stream.decode_book(_clob_book(), receive=clock.now(),
                                 feed_session_id="c1")
    assert st.depth_authority == DepthAuthority.FULL_REPLACEMENT_CONFIRMED
    vwap, exhausted = st.vwap(q=100.0, side="ask")
    assert vwap == pytest.approx(0.42)
    assert exhausted is False


def test_clob_depth_exhaustion_is_reported_never_extrapolated():
    st = clob_stream.decode_book(_clob_book(), receive=clock.now(),
                                 feed_session_id="c1")
    vwap, exhausted = st.vwap(q=10_000.0, side="ask")
    assert exhausted is True, "the retained ladder did not hold q"
    assert vwap == pytest.approx(0.42), "priced only what was actually there"


def test_clob_price_change_is_recorded_and_deliberately_not_applied():
    """The refusal that keeps the two readings from silently collapsing.

    Under LEVEL_DELTA, ignoring the message leaves the book stale. Under
    BEST_QUOTE_NOTIFICATION, applying it corrupts the ladder with a level that
    was never a level. Opposite failures, so there is no cautious middle guess.
    """
    base = clock.now()
    c = clob_stream.ClobStreamState(feed_session_id="c1")
    c.on_book(_clob_book(bid=0.40, ask=0.42), receive=_at(base, 0.0))
    st = c.on_price_change({"asset_id": "tok1", "price": "0.45", "size": "10"},
                           receive=_at(base, 0.050))

    assert st is not None
    assert st.best_bid == 0.40 and st.best_ask == 0.42, (
        "the price_change mutated the book; its semantics are unresolved and it "
        "must not be interpreted")
    assert st.pending_unapplied_updates == 1
    assert st.depth_authority == DepthAuthority.DEPTH_PENDING_SEMANTICS
    assert clob_stream.PRICE_CHANGE_SEMANTICS == \
        "CLOB_PRICE_CHANGE_SEMANTICS_UNRESOLVED"
    assert set(clob_stream.PRICE_CHANGE_READINGS) == \
        {"LEVEL_DELTA", "BEST_QUOTE_NOTIFICATION"}


def test_clob_a_pending_state_cannot_price_a_size():
    base = clock.now()
    c = clob_stream.ClobStreamState(feed_session_id="c1")
    c.on_book(_clob_book(), receive=_at(base, 0.0))
    st = c.on_price_change({"asset_id": "tok1"}, receive=_at(base, 0.050))
    with pytest.raises(streamstate.DepthUnavailable):
        st.vwap(q=10.0)


def test_clob_the_raw_price_change_is_retained_for_offline_resolution():
    base = clock.now()
    c = clob_stream.ClobStreamState(feed_session_id="c1")
    c.on_book(_clob_book(), receive=_at(base, 0.0))
    c.on_price_change({"asset_id": "tok1", "price": "0.45"},
                      receive=_at(base, 0.05))
    c.on_price_change({"asset_id": "tok1", "price": "0.46"},
                      receive=_at(base, 0.06))
    assert len(c.unapplied["tok1"]) == 2
    assert c.stats()["unapplied"] == 2


def test_clob_a_full_book_supersedes_everything_unapplied():
    """Whatever those messages meant, the venue has now stated its own ladder."""
    base = clock.now()
    c = clob_stream.ClobStreamState(feed_session_id="c1")
    c.on_book(_clob_book(bid=0.40), receive=_at(base, 0.0))
    c.on_price_change({"asset_id": "tok1"}, receive=_at(base, 0.05))
    st = c.on_book(_clob_book(bid=0.50, hash_="h2"), receive=_at(base, 0.10))
    assert st.pending_unapplied_updates == 0
    assert st.depth_authority == DepthAuthority.FULL_REPLACEMENT_CONFIRMED
    assert "tok1" not in c.unapplied


def test_clob_a_price_change_before_any_book_invents_no_state():
    base = clock.now()
    c = clob_stream.ClobStreamState(feed_session_id="c1")
    assert c.on_price_change({"asset_id": "tok1"}, receive=base) is None
    assert c.history("tok1") is None
    assert len(c.unapplied["tok1"]) == 1, "the frame is kept for the harness"


def test_clob_hash_is_retained_but_never_treated_as_a_sequence_number():
    st = clob_stream.decode_book(_clob_book(hash_="abc"), receive=clock.now(),
                                 feed_session_id="c1")
    assert st.venue_book_hash == "abc"
    assert st.venue_sequence is None
    assert st.continuity == streamstate.Continuity.UNVERIFIED, (
        "a hash proves this book differs from the last; it says nothing about "
        "whether a book in between was missed")


# ======================================================================
# THE TWO CHANNELS DO NOT CONTAMINATE ONE ANOTHER
# ======================================================================
def test_neither_stream_module_imports_the_other():
    import ast
    import pathlib

    obs = pathlib.Path(__file__).resolve().parents[1] / "sportsassets" / "obs"
    for name, forbidden in (("pmus_stream.py", "clob_stream"),
                            ("clob_stream.py", "pmus_stream")):
        tree = ast.parse((obs / name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert forbidden not in (node.module or ""), name
                assert forbidden not in {a.name for a in node.names}, name
            if isinstance(node, ast.Import):
                assert all(forbidden not in a.name for a in node.names), name


def test_a_state_cannot_be_appended_to_the_other_channels_history():
    base = clock.now()
    hist = streamstate.TokenStateHistory(
        channel=StreamChannel.PMUS_FAST_STREAM_PATH, token_id="m")
    alien = streamstate.StreamState(
        channel=StreamChannel.CLOB_FAST_STREAM_PATH, token_id="m",
        receive=base, feed_session_id="s")
    with pytest.raises(ValueError):
        hist.append(alien)


def test_stale_state_beyond_tolerance_is_invalid_not_selected():
    base = clock.now()
    hist = streamstate.TokenStateHistory(
        channel=StreamChannel.PMUS_FAST_STREAM_PATH, token_id="m")
    hist.append(streamstate.StreamState(
        channel=hist.channel, token_id="m", receive=base, feed_session_id="s"))
    sel = streamstate.select_state_at(hist, _at(base, 9.0),
                                      stale_tolerance_s=5.0)
    assert sel.outcome == SelectionOutcome.CACHE_INVALID
    assert "STALE_BEYOND_TOLERANCE" in sel.reason
    assert sel.state_age_at_receipt_ms == pytest.approx(9000.0, abs=1e-6)


def test_the_history_is_bounded_and_drops_the_oldest_first():
    base = clock.now()
    hist = streamstate.TokenStateHistory(
        channel=StreamChannel.PMUS_FAST_STREAM_PATH, token_id="m", retain=4)
    for i in range(10):
        hist.append(streamstate.StreamState(
            channel=hist.channel, token_id="m", receive=_at(base, i * 0.01),
            feed_session_id="s", best_bid=float(i)))
    assert len(hist) == 4
    assert hist.latest.best_bid == 9.0
    # And selection still works against what remains.
    assert streamstate.select_state_at(hist, _at(base, 0.075),
                                       stale_tolerance_s=5.0).state.best_bid == 7.0
