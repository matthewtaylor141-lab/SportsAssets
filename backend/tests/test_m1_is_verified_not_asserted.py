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
    # THE 'FULL BOOK' CLAIM IS PUBLISHED, AND I HAD REPORTED IT AS A DOCSTRING.
    #
    # This assertion used to require the word "docstring" here, which pinned my
    # own mistake in place. The venue's WebSocket page states it in its
    # subscription-types table -- read on the runner 2026-09-27, run
    # 36332797806, preserved in
    # research/evidence/VENUE_BOOK_PROTOCOL_2026-09-27.md. The SDK docstring
    # AGREES with the published protocol; it was never the only source.
    assert "PUBLISHED" in SC.FEED_CONTRACT["full_book_claim_rests_on"]
    assert "was wrong" in SC.FEED_CONTRACT["full_book_claim_rests_on"]
    assert "full order book" in (types_mod.MarketData.__doc__ or "").lower()
    pp = SC.PUBLISHED_PROTOCOL
    assert pp["states_full_order_book"] is True
    assert pp["subscription_types"]["SUBSCRIPTION_TYPE_MARKET_DATA"] == (
        "Full order book and market stats")
    # AND THE PAGE DOCUMENTS NO INCREMENTAL MECHANISM AT ALL, which is why a
    # delta-continuity requirement was the wrong shape.
    assert pp["sentences_on_deltas_or_increments"] == 0
    assert pp["sentences_on_sequence_or_ordering_or_gaps"] == 0
    # NOR ANY TIMING GUARANTEE, which is the gap that actually binds.
    assert pp["sentences_stating_a_latency_or_as_of_guarantee"] == 0
    # THE SDK AND THE PUBLISHED PROTOCOL AGREE, INCLUDING ON THE ABSENCES --
    # which is what makes them properties of the FEED rather than of this client.
    assert SC.FEED_CONTRACT["sdk_and_protocol_disagree_on"].startswith(
        "nothing found")
    assert "full-replacement semantics" in (
        SC.FEED_CONTRACT["sdk_and_protocol_agree_on"])


# ── §2 · THE PRECONDITIONS ──────────────────────────────────────────

def test_the_verdict_is_unchanged_but_the_MISSING_PRECONDITIONS_ARE_NOT():
    """THE CORRECTION, AND IT DOES NOT MOVE THE ANSWER.

    This test used to be `test_m1_needs_four_things_and_two_are_missing` and
    asserted the missing two were P1 (replacement authority) and P3 (gap-free
    continuity). Both were wrong, in opposite directions:

      P1 is ESTABLISHED -- the venue PUBLISHES full-book semantics.
      P3 was the WRONG REQUIREMENT -- a delta-reconstruction guarantee demanded
         of a feed that documents no deltas.

    What is actually missing is TIMING (P5) and RESYNCHRONISATION (P6). The
    verdict is still M1_NOT_AVAILABLE, so no gate loosened; what changed is that
    the refusal now points at the real gap, and the gap is a narrower claim.
    """
    st = SC.mechanism_status()
    assert st["status"] == SC.M1_NOT_AVAILABLE, "the verdict does not move"
    # AND P6 IS NOW AVAILABLE TOO, which was a second correction. The SDK does
    # not reconnect; OUR wrapper does, and on a documented full-replacement feed
    # discard-reconnect-resubscribe-await IS resynchronisation. Missing SDK
    # convenience is not an unavailable venue capability.
    assert set(st["missing"]) == {SC.P5_DOCUMENTED_TIMING}
    assert st["preconditions"][SC.P1_REPLACEMENT_AUTHORITY]["available"] is True
    assert st["preconditions"][SC.P2_LIVENESS]["available"] is True
    assert st["preconditions"][SC.P3_CONNECTION_CONTINUITY]["available"] is True
    assert st["preconditions"][SC.P4_IDENTITY]["available"] is True
    assert "the WHOLE book" in st["what_a_subscription_here_does_prove"]
    assert "none of them is a currency guarantee" in \
        st["what_a_subscription_here_does_prove"]


def test_the_two_questions_are_answered_SEPARATELY():
    """FUSING THEM IS WHAT PRODUCED A CONCLUSION WIDER THAN THE EVIDENCE.

    Q1 is about the message's AUTHORITY, Q2 about its TIMING. This feed settles
    Q1 completely and says nothing about Q2, and one verdict covering both
    reported the first as missing.
    """
    q = SC.questions()
    q1 = q[SC.Q1_REPLACEMENT_AUTHORITY]
    q2 = q[SC.Q2_SUFFICIENTLY_CURRENT]
    assert q1["answered"] is True and q1["missing"] == []
    assert q2["answered"] is False
    assert set(q2["missing"]) == {SC.P5_DOCUMENTED_TIMING}
    # AND THE REASON A MISSING SEQUENCE DOES NOT TOUCH Q1 IS STATED.
    assert "DELTA stream" in q1["and_a_missing_sequence_does_not_change_it"]
    assert "does not build on the increments before it" in (
        q1["and_a_missing_sequence_does_not_change_it"])
    assert "an unmeasured age is not an old one" == (
        q2["and_this_is_not_a_claim_of_staleness"]), (
        "NOT_ESTABLISHED is the default and is not an accusation of staleness")


def test_the_withdrawn_delta_requirement_is_recorded_as_withdrawn():
    """A requirement dropped is written down, not quietly deleted -- otherwise
    it reads as though it had never been asserted."""
    w = SC.WITHDRAWN_REQUIREMENTS[SC.P3_WITHDRAWN_REQUIREMENT]
    assert w["withdrawn_on"] == "2026-09-27"
    assert "DELTA-stream requirement" in w["why_withdrawn"]
    assert "FULL-REPLACEMENT feed" in w["why_withdrawn"]
    assert "ZERO sentences" in w["why_withdrawn"]
    assert w["what_replaced_it"] == SC.P3_CONNECTION_CONTINUITY
    assert "the verdict" in w["and_what_did_not_change"]
    assert "TIMING gap" in w["and_what_did_not_change"]


def test_the_surviving_continuity_requirement_is_about_OUR_socket():
    """P3 was replaced, not deleted. What survives is enforceable without a
    sequence number, because it is a fact about our own connection."""
    p3 = SC.PRECONDITION_STATUS[SC.P3_CONNECTION_CONTINUITY]
    assert p3["available"] is True
    assert "DISCARDS every market's state on a drop" in p3["why"]
    assert "not claim" in p3["this_is_not_the_withdrawn_requirement"]
    assert "did not hold a book across a disconnect" in (
        p3["this_is_not_the_withdrawn_requirement"])


def test_the_binding_precondition_is_TIMING_and_it_names_its_exit():
    """AND THE EXIT IT NAMES IS NO LONGER A MOVING-BOOK READ.

    This asserted `"MOVING book" in p5["what_would_establish_it"]`, pinning a
    second route I had offered: "a read of a demonstrably MOVING book that
    separates last-change from now". That route is withdrawn, so this test is
    updated to assert the withdrawal rather than deleted.

    WHY IT WAS WRONG. A delayed feed of a moving book shows exactly the same
    movement as a current one -- a sixty-second-late stream of a live market is
    indistinguishable, by movement alone, from a current one. Movement separates
    LIVE from FROZEN. It does not separate CURRENT from LATE, and P5 is about the
    second. The only thing that establishes P5 is the venue documenting the
    guarantee, which is not ours to produce.
    """
    p5 = SC.PRECONDITION_STATUS[SC.P5_DOCUMENTED_TIMING]
    assert p5["available"] is False
    assert p5["this_is_the_binding_precondition"] is True
    assert "no as-of instant" in p5["why"]
    # THE ONLY ESTABLISHING ROUTE IS THE VENUE'S OWN DOCUMENTATION.
    assert "documenting" in p5["what_would_establish_it"]
    assert "MOVING" not in p5["what_would_establish_it"]
    # AND THE WITHDRAWAL IS RECORDED, with what a movement experiment could and
    # could not distinguish, so the next reader does not re-derive the offer.
    assert "not current from late" in p5["movement_does_not_establish_it"]
    assert "unmeasured" in p5["what_a_movement_experiment_could_distinguish"]
    assert "promoting on movement" in p5["so_it_is_evidence_not_a_promotion"]


def test_the_transact_time_DISCRIMINATOR_IS_WITHDRAWN():
    """MY ARGUMENT WAS FALSE AND THIS TEST USED TO ASSERT ITS CONCLUSION.

    It read `test_transact_time_is_a_market_data_instant_and_no_more_than_that`
    and asserted the field WAS established as a market-data instant, on the
    grounds that "a response stamp cannot precede its own response".

    THE PREMISE IS FALSE, two ways, either sufficient: a timestamp can describe
    representation GENERATION, which happens before transmission; and a cached
    representation retains its original timestamp -- and these responses came
    from a CDN under max-age=30 with cache-status EXPIRED then HIT. So the
    representation-generation hypothesis was never excluded.
    """
    t = SC.TRANSACT_TIME
    assert t["denotation"] == "UNRESOLVED"
    assert t["nothing_is_gated_on_it"] is True
    assert "ESTABLISHED" not in t, (
        "the old key asserted a denotation the evidence does not support")
    w = t["withdrawn_argument"]
    assert "cannot precede its own response" in w["what_I_claimed"]
    assert "representation GENERATION" in w["why_it_is_false"]
    assert "cached representation retains its original timestamp" in (
        w["why_it_is_false"])
    assert "never excluded" in w["so"]
    assert "more rigorous-looking sleeve" in w["the_pattern"]


def test_the_observations_and_the_interpretations_are_SEPARATE_KEYS():
    """The instruction is to record observations separately from
    interpretations, because the failure mode is one being read as the other."""
    t = SC.TRANSACT_TIME
    o = t["observed"]
    # OBSERVATIONS: facts about what came back, and nothing else.
    assert o["market_state"] == "MARKET_STATE_EXPIRED"
    assert o["book_levels"].startswith("0 bids")
    assert "EXPIRED" in o["cache_status"] and "HIT" in o["cache_status"]
    assert o["date_minus_transact_time_s"] > 18_000_000
    # AND WHAT THEY SUPPORT, WHICH IS ALMOST NOTHING.
    assert "OLD IN THE RETURNED REPRESENTATION" in t["what_this_establishes"]
    assert "not about what the field denotes" in t["what_this_establishes"]


def test_all_four_hypotheses_stay_open_and_none_is_preferred():
    h = SC.TRANSACT_TIME["hypotheses"]
    assert set(h) == {"H1_LAST_BOOK_CHANGE", "H2_LAST_TRADE",
                      "H3_SETTLEMENT_OR_CLOSE",
                      "H4_REPRESENTATION_GENERATION"}
    assert "PRECEDES" in h["H4_REPRESENTATION_GENERATION"]
    assert "survives caching" in h["H4_REPRESENTATION_GENERATION"]
    assert "no discriminating power" in (
        SC.TRANSACT_TIME["why_the_observations_cannot_separate_them"])


def test_a_moving_book_experiment_is_not_offered_as_the_answer():
    """Correlation between a moving book and a moving stamp is evidence about
    one hypothesis against another. It is not a semantic guarantee and must not
    become the next asserted certificate."""
    t = SC.TRANSACT_TIME
    assert "venue's own contract" in t["what_would_resolve_it"]
    nope = t["what_would_NOT_resolve_it"]
    assert "moving-book experiment on its own" in nope
    assert "not a semantic guarantee" in nope
    assert "third asserted certificate" in nope
    # AND WHAT TURNS ON IT IS STATED WITHOUT PRESUMING THE ANSWER.
    turns = t["what_turns_on_it"]
    assert "under H1" in turns and "Under H4" in turns
    assert "NOT a loosening" in turns


def test_the_conclusion_is_narrower_than_no_engineering_route():
    """'Our predicate cannot qualify this feed' is narrower than 'the venue has
    no engineering route', and I asserted the second."""
    st = SC.mechanism_status()
    assert "CANNOT QUALIFY THIS FEED" in st["the_conclusion_is"]
    assert "no engineering route" in st["the_conclusion_is_NOT"]
    assert "I asserted it" in st["the_conclusion_is_NOT"]
    assert st["how_it_could_close"], "a gap with no named exit is a dead end"


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
    assert set(state["unmet"]) == {SC.P5_DOCUMENTED_TIMING}
    # EVERY LIVE CHECK PASSED, which is what makes the refusal informative --
    # and now includes P1, so the refusal is explicitly NOT a doubt about
    # whether the message is the whole book.
    assert state["checks"][SC.P1_REPLACEMENT_AUTHORITY]["met"] is True
    assert state["checks"][SC.P2_LIVENESS]["met"] is True
    assert state["checks"][SC.P3_CONNECTION_CONTINUITY]["met"] is True
    assert state["checks"][SC.P4_IDENTITY]["met"] is True
    assert "no amount of subscribing supplies them" in state["why"]
    assert "TIMING refusal" in state["why"]


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
        SC.P5_DOCUMENTED_TIMING}
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
    # THE TWO THAT ARE ACTUALLY MISSING NOW: timing and resynchronisation.
    # ONLY P5 IS LEFT TO FLIP, since P6 was corrected to available.
    status[SC.P5_DOCUMENTED_TIMING]["available"] = True
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
