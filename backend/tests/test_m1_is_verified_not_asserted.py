"""M1, VERIFIED AGAINST THE SHIPPED FEED RATHER THAN NAMED.

`bettor_venue_currency` lists M1 -- a live market-data subscription -- as a
mechanism that can establish a venue book's currency. This file is the check on
that claim, and the check fails: the feed cannot supply two of M1's four
preconditions. What is pinned here is therefore the REFUSAL and the machinery
that would make the mechanism safe if the feed ever changed.

The tests are in the order the argument runs:

  §1  the feed contract, read from the installed client, not from prose
  §2  the four preconditions and which two are absent
  §3  a token cannot grant what the feed does not provide
  §4  disconnect invalidation, which is correct regardless
  §5  identity compared rather than assumed
  §6  the production reader returns None for the right reason
  §7  and if the feed ever gains the missing guarantees, the mechanism works
"""

from __future__ import annotations

import pytest

from sportsassets import bettor_stream_currency as SC
from sportsassets import bettor_venue_currency as VC
from sportsassets.workers import ext_pinnacle_loop as L


@pytest.fixture(autouse=True)
def _clean():
    SC.reset()
    yield
    SC.reset()


# ── §1 · THE FEED CONTRACT, FROM THE INSTALLED CLIENT ───────────────

def test_the_contract_is_read_from_the_shipped_client_not_from_prose():
    """The determination must be checkable. Each field below is re-derived here
    from the SDK the image actually installs, so a contract change breaks this
    test rather than silently invalidating a verdict built on it."""
    types_mod = pytest.importorskip("polymarket_us.websocket.types")
    base_mod = pytest.importorskip("polymarket_us.websocket.base")
    import inspect

    payload = types_mod._MarketDataPayload.__annotations__
    assert set(SC.FEED_CONTRACT["market_data_payload_fields"]) == set(payload), (
        "the payload shape changed; re-verify M1 rather than trusting the "
        "recorded determination")
    # NO SEQUENCE, NO SNAPSHOT MARKER. Checked by name across plausible
    # spellings, because the absence is the whole finding.
    lowered = {k.lower() for k in payload}
    for probe in ("seq", "sequence", "sequencenumber", "msgid", "messageid",
                  "issnapshot", "snapshot", "action", "type", "delta",
                  "update_id", "updateid"):
        assert probe not in lowered, (
            "the payload now carries %r -- M1's continuity precondition may be "
            "satisfiable and must be re-verified" % probe)
    assert SC.FEED_CONTRACT["has_sequence_number"] is False
    assert SC.FEED_CONTRACT["has_snapshot_or_delta_marker"] is False
    # AND THE CLIENT DOES NOT RECONNECT OR RESYNCHRONISE.
    src = inspect.getsource(base_mod)
    assert "ConnectionClosed" in src
    assert "reconnect" not in src.lower(), (
        "the client reconnects now; the epoch-invalidation reasoning changes")
    assert SC.FEED_CONTRACT["client_reconnects"] is False
    assert SC.FEED_CONTRACT["client_resynchronises"] is False
    # THE 'FULL BOOK' CLAIM IS A DOCSTRING, and the record says so.
    assert "docstring" in SC.FEED_CONTRACT["full_book_claim_rests_on"]
    assert "full order book" in (types_mod.MarketData.__doc__ or "").lower()


# ── §2 · THE PRECONDITIONS ──────────────────────────────────────────

def test_m1_needs_four_things_and_two_are_missing():
    st = SC.mechanism_status()
    assert st["status"] == SC.M1_NOT_AVAILABLE
    assert set(st["missing"]) == {SC.P1_REPLACEMENT_AUTHORITY,
                                  SC.P3_CONTINUITY}
    # THE TWO THAT ARE AVAILABLE ARE NOT ENOUGH, and the record says why.
    assert st["preconditions"][SC.P2_LIVENESS]["available"] is True
    assert st["preconditions"][SC.P4_IDENTITY]["available"] is True
    assert "socket is open" in st["what_a_subscription_here_does_prove"]
    assert "neither is a currency guarantee" in \
        st["what_a_subscription_here_does_prove"]


def test_the_missing_continuity_reason_is_the_right_one():
    """A gap is invisible by construction, and `transactTime` cannot stand in."""
    p3 = SC.PRECONDITION_STATUS[SC.P3_CONTINUITY]
    assert p3["available"] is False
    assert "leaves no trace" in p3["why"]
    assert "unresolved" in p3["transact_time_cannot_substitute"]
    assert "BETWEEN two changes" in p3["transact_time_cannot_substitute"]


def test_the_bounds_are_not_widened_by_this_module():
    assert SC.MAX_SNAPSHOT_AGE_S == VC.MAX_BOOK_STATE_AGE_S == 30.0
    assert SC.mechanism_status()["bounds_unchanged"][
        "these_are_chosen_allowances"] is True


# ── §3 · A TOKEN CANNOT GRANT WHAT THE FEED LACKS ───────────────────

def test_a_live_looking_subscription_does_not_make_this_book_current():
    """THE CENTRAL REFUSAL. Heartbeat one second ago, a message for exactly this
    market two seconds ago, and the answer is still no -- because neither says
    the book we hold is whole or gap-free."""
    now = 1_700_000_000.0
    SC.connection_opened(now=now - 10.0)
    SC.heartbeat(now=now - 1.0)
    SC.message_received("aec-x", now=now - 2.0, payload_slug="aec-x")

    state = SC.subscription_state("aec-x", now=now)
    assert state["usable_as_a_currency_mechanism"] is False
    assert set(state["unmet"]) == {SC.P1_REPLACEMENT_AUTHORITY,
                                   SC.P3_CONTINUITY}
    # THE TWO LIVE CHECKS PASSED, which is what makes the refusal informative.
    assert state["checks"][SC.P2_LIVENESS]["met"] is True
    assert state["checks"][SC.P4_IDENTITY]["met"] is True
    assert "no amount of subscribing supplies them" in state["why"]


def test_the_refusal_is_not_relieved_by_passing_a_subscription_argument():
    """THE SHAPE OF THE OLD MISTAKE, CLOSED. `evaluate` will honour a
    hand-built subscription dict -- that is how a test supplies a mechanism --
    but the PRODUCTION reader refuses to build one, so no cycle can obtain M1 by
    calling a function differently."""
    assert SC.mechanism_status()["not_granted_by_a_token"]
    got = L.book_currency_evidence("aec-x")
    assert got["subscription"] is None
    assert got["m1"]["status"] == SC.M1_NOT_AVAILABLE
    assert set(got["m1"]["missing_from_the_feed"]) == {
        SC.P1_REPLACEMENT_AUTHORITY, SC.P3_CONTINUITY}
    assert "no sequence number" in got["why_none"]
    assert "properties of the FEED" in got["why_none"]


# ── §4 · DISCONNECT INVALIDATION ────────────────────────────────────

def test_a_book_is_discarded_across_a_connection_drop_not_aged():
    now = 1_700_000_000.0
    SC.connection_opened(now=now - 60.0)
    SC.message_received("aec-a", now=now - 50.0, payload_slug="aec-a")
    SC.message_received("aec-b", now=now - 49.0, payload_slug="aec-b")

    closed = SC.connection_closed(now=now - 40.0)
    assert closed["invalidated_markets"] == ["aec-a", "aec-b"]
    assert "unknown number of missed updates" in closed["why"]

    # AFTER THE DROP, NOTHING IS KNOWN ABOUT EITHER MARKET.
    for slug in ("aec-a", "aec-b"):
        st = SC.subscription_state(slug, now=now - 39.0)
        assert st["last_message_age_s"] is None
        assert st["checks"][SC.P4_IDENTITY]["met"] is False
        assert st["checks"][SC.P2_LIVENESS]["met"] is False


def test_a_message_from_a_previous_epoch_cannot_satisfy_identity():
    """The subtler version: reconnect, then ask about a market whose last
    message arrived on the OLD connection. There is a gap in front of it and no
    way to see the gap, so it does not count."""
    now = 1_700_000_000.0
    SC.connection_opened(now=now - 100.0)
    SC.message_received("aec-a", now=now - 95.0, payload_slug="aec-a")
    SC.connection_closed(now=now - 90.0)
    SC.connection_opened(now=now - 80.0)
    SC.heartbeat(now=now - 1.0)

    st = SC.subscription_state("aec-a", now=now)
    assert st["checks"][SC.P2_LIVENESS]["met"] is True, "the socket is alive"
    assert st["checks"][SC.P4_IDENTITY]["met"] is False, (
        "but no message for this market has arrived on THIS connection")
    assert st["connection"]["closes_seen"] == 1
    assert st["connection"]["epoch"] == 2


def test_silence_past_the_bound_is_not_a_quiet_market():
    now = 1_700_000_000.0
    SC.connection_opened(now=now - 100.0)
    SC.message_received("aec-a", now=now - 99.0, payload_slug="aec-a")
    st = SC.subscription_state("aec-a", now=now)
    assert st["checks"][SC.P2_LIVENESS]["met"] is False
    assert st["checks"][SC.P2_LIVENESS]["silence_s"] > SC.MAX_SILENCE_S


# ── §5 · IDENTITY IS COMPARED ───────────────────────────────────────

def test_a_payload_naming_a_different_market_fails_identity():
    """P4 is a comparison on the venue's own slug, not a name or a substring."""
    now = 1_700_000_000.0
    SC.connection_opened(now=now - 10.0)
    # A message recorded under one key whose payload names another market: the
    # kind of mix-up a substring match would not notice.
    SC.message_received("aec-nba-bos-lal", now=now - 1.0,
                        payload_slug="aec-nba-bos-lal-1h")
    st = SC.subscription_state("aec-nba-bos-lal", now=now)
    assert st["checks"][SC.P4_IDENTITY]["met"] is False
    assert "DIFFERENT market" in st["checks"][SC.P4_IDENTITY]["why"]


# ── §6 · THE PRODUCTION READER ──────────────────────────────────────

def test_the_reader_says_which_guarantee_is_missing_not_merely_none():
    """A reader that returns None tells whoever has to fix it nothing. This one
    distinguishes 'the feed cannot' from 'nothing is wired', which are different
    pieces of work."""
    got = SC.evidence_for("aec-x")
    assert got["subscription"] is None
    assert got["refusal"] == SC.M1_NOT_AVAILABLE
    assert got["missing_from_the_feed"] == list(SC.MISSING_PRECONDITIONS)
    ev = L.book_currency_evidence("aec-x")
    assert "sequence" in ev["what_would_change_it"]
    assert "ETag" in ev["what_would_change_it"]
    assert "BY MEASUREMENT rather than by decision" in ev["what_would_change_it"]


def test_the_scheduled_read_still_refuses_and_names_the_mechanism():
    """AND THE CONSEQUENCE, STATED WHERE IT LANDS. The lane refuses every
    candidate on an unestablished book currency, which is the honest state and
    not a defect in this file."""
    verdict = VC.evaluate(now=1_700_000_000.0,
                          subscription=L.book_currency_evidence(
                              "aec-x").get("subscription"))
    assert VC.admits(verdict) is False
    assert verdict["verdict"] == VC.NOT_ESTABLISHED
    named = {m["mechanism"] for m in verdict["mechanisms_unavailable"]}
    assert VC.M1_LIVE_SUBSCRIPTION in named


def test_m1_stays_in_the_establishing_tuple_on_purpose():
    """Removing it would hide the requirement rather than report the gap, and
    would make the verdict a decision instead of a measurement."""
    assert VC.M1_LIVE_SUBSCRIPTION in VC.ESTABLISHING_MECHANISMS
    doc = VC.__doc__ or ""
    assert "VERIFIED AGAINST THE SHIPPED CLIENT" in doc
    assert "no SEQUENCE NUMBER" in doc


# ── §7 · IF THE FEED EVER SUPPLIES THEM ─────────────────────────────

def test_the_mechanism_works_once_the_feed_supplies_the_two_guarantees(
        monkeypatch):
    """THE VERDICT IS COMPUTED, NOT HARD-CODED.

    This is the difference between reporting a gap and declaring a conclusion:
    flip the two feed properties and the same code admits, with a number
    stricter than anything the lane has used -- the age of a full snapshot for
    the right instrument on a connection with proven continuity.
    """
    status = {k: dict(v) for k, v in SC.PRECONDITION_STATUS.items()}
    status[SC.P1_REPLACEMENT_AUTHORITY]["available"] = True
    status[SC.P3_CONTINUITY]["available"] = True
    monkeypatch.setattr(SC, "PRECONDITION_STATUS", status)
    monkeypatch.setattr(SC, "MISSING_PRECONDITIONS", ())
    monkeypatch.setattr(SC, "M1_STATUS", SC.M1_AVAILABLE)

    now = 1_700_000_000.0
    SC.connection_opened(now=now - 30.0)
    SC.heartbeat(now=now - 1.0)
    SC.message_received("aec-x", now=now - 4.0, payload_slug="aec-x")

    got = SC.evidence_for("aec-x", now=now)
    assert got["subscription"] is not None, got
    assert got["subscription"]["last_update_at"] == pytest.approx(now - 4.0)

    verdict = VC.evaluate(now=now, subscription=got["subscription"])
    assert VC.admits(verdict) is True
    assert verdict["mechanism"] == VC.M1_LIVE_SUBSCRIPTION
    assert verdict["book_state_age_s"] == pytest.approx(4.0, abs=0.1)

    # AND THE DROP STILL INVALIDATES, even with both guarantees present.
    SC.connection_closed(now=now)
    after = SC.evidence_for("aec-x", now=now + 1.0)
    assert after["subscription"] is None
