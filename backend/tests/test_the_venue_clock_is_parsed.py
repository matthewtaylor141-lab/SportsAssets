"""The venue's book clock is READ, and an unread clock stays UNKNOWN.

THE PRODUCTION DEFECT THESE PIN. On build 51f20e7 every book read in the
external-valuation lane recorded

    venue_clock VENUE_CLOCK_UNPARSEABLE
    fresh       null

and nine positive-edge candidates were refused by the STALE_DATA gate --
not because a book was stale, but because the age was never measured. The
parse was one line:

    vt = float(venue_ts)
    vt = vt / 1000.0 if vt > 1e11 else vt      # ms or s

which is a parser for a NUMERIC epoch field. `marketData.transactTime` is
an ISO-8601 string with a bare Z and up to nine fractional digits, so
`float()` refused every real value the venue has ever sent. The ms-or-s
heuristic never ran at all.

THE REPO ALREADY HAD THE RIGHT PARSER. `bettor_market_stream._parse_ts`
rewrites the Z, truncates the fraction to six digits and refuses a naive
stamp. The lane now reuses it. These tests pin both halves: the forms that
must parse, and the ones that must stay UNKNOWN rather than be guessed.
"""

import json
import pathlib

import pytest

from sportsassets.bettor_market_stream import _parse_ts


FIXTURE = (pathlib.Path(__file__).parent / "fixtures"
           / "pmus_book_transact_time_forms.json")


@pytest.fixture(scope="module")
def captured():
    return json.loads(FIXTURE.read_text())


# ── 1 · THE FORMS THE VENUE ACTUALLY SENDS ───────────────────────────

def test_the_fixture_names_its_field_and_its_parser(captured):
    assert captured["field_path"] == "marketData.transactTime"
    assert captured["json_type"] == "string"
    assert captured["supported_parser"].endswith("_parse_ts")


def test_every_observed_form_parses_to_its_stated_epoch(captured):
    for form in captured["observed_forms"]:
        dt = _parse_ts(form["value"])
        assert dt is not None, form["value"]
        assert dt.tzinfo is not None, form["value"]
        assert dt.timestamp() == pytest.approx(form["expected_epoch_s"],
                                               abs=1e-3), form["value"]


def test_the_nanosecond_form_is_present_and_parses(captured):
    """The form that breaks BOTH float() and fromisoformat unaided."""
    nano = [f for f in captured["observed_forms"]
            if f["fractional_digits"] == 9]
    assert nano, "the nanosecond capture must stay in the fixture"
    for f in nano:
        assert _parse_ts(f["value"]) is not None


def test_float_refuses_every_observed_form(captured):
    """The old parse, shown failing on the real data rather than argued."""
    for form in captured["observed_forms"]:
        with pytest.raises(ValueError):
            float(form["value"])


def test_fromisoformat_is_not_the_reason_to_use_the_supported_parser(captured):
    """A CORRECTION TO MY OWN FIRST DRAFT of this test.

    It asserted that 3.11's `fromisoformat` rejects a bare Z and nine
    fractional digits, so the supported parser was needed for that. Run
    against this interpreter it ACCEPTS all five forms -- the rejection was
    true of <=3.10. The defect is narrower and undiminished: the field is a
    STRING and the lane called `float()` on it.

    The reason to reuse `_parse_ts` is the NAIVE case: `fromisoformat`
    returns a naive datetime for a stamp with no zone, and `.timestamp()`
    on that silently reads it in the host's local time. `_parse_ts` refuses
    it, so the verdict stays UNKNOWN instead of being shifted by an offset.
    """
    from datetime import datetime
    for form in captured["observed_forms"]:
        assert datetime.fromisoformat(form["value"]) is not None
    naive = datetime.fromisoformat("2026-09-21T18:20:25.743291447")
    assert naive.tzinfo is None, "fromisoformat hands back a naive stamp"
    assert _parse_ts("2026-09-21T18:20:25.743291447") is None, (
        "the supported parser refuses it rather than guessing a zone")


# ── 2 · WHAT MUST STAY UNKNOWN ───────────────────────────────────────

def test_the_unparseable_values_stay_unparsed(captured):
    for bad in captured["must_stay_unparsed"]:
        assert _parse_ts(bad["value"]) is None, bad


def test_a_naive_stamp_is_refused_not_assumed_to_be_utc(captured):
    naive = "2026-09-21T18:20:25.743291447"
    assert _parse_ts(naive) is None
    assert any(b["value"] == naive for b in captured["must_stay_unparsed"])


def test_none_and_the_absent_sentinel_are_not_timestamps():
    assert _parse_ts(None) is None
    assert _parse_ts("NOT_IDENTIFIED") is None


# ── 3 · THE LANE USES THAT PARSER, AND NOTHING ELSE ──────────────────

def test_the_entry_lane_imports_the_supported_parser():
    from sportsassets.workers import ext_pinnacle_loop as loop
    assert loop._stream_parse_ts is _parse_ts


def test_the_lane_no_longer_calls_float_on_the_clock():
    """A second implementation is how the first one became float()."""
    import inspect

    from sportsassets.workers import ext_pinnacle_loop as loop
    # COMMENTS STRIPPED. The first draft of this assertion failed on the
    # comment that EXPLAINS the old parse, which is the wrong thing to
    # forbid -- the record of a defect belongs next to its fix.
    src = "\n".join(l for l in inspect.getsource(loop.venue_quote).splitlines()
                    if not l.lstrip().startswith("#"))
    assert "float(venue_ts)" not in src
    assert "_stream_parse_ts(venue_ts)" in src
    # AND THE FALLBACK IS STILL NAMED, never our own read clock.
    assert "VENUE_CLOCK_UNPARSEABLE" in src
    assert "VENUE_CLOCK_NOT_PROVIDED" in src


def test_the_freshness_limits_are_unchanged_and_say_what_they_govern():
    """THE NUMBERS ARE THE SAME AND THE STAMP DETERMINATION IS WITHDRAWN.

    30 s still governs the bookmaker's own observation instant. 30 s still
    governs the venue book -- applied now to an ESTABLISHED book-state age
    rather than to a field whose meaning is unresolved. Our own processing
    delay has its own 10 s bound, and it is an ADDITIONAL requirement that on
    its own establishes nothing about the payload's age.

    WHAT THIS TEST USED TO ASSERT. That `VENUE_STAMP_SEMANTICS` is
    "LAST_BOOK_CHANGE", determined from the 2026-09-27 probe. That inference is
    invalid -- a cache replaying one representation to both reads predicts the
    identical observation, and the probe captured no headers -- so the semantics
    is UNRESOLVED and the stamp decides nothing in either direction.
    """
    from sportsassets import bettor_venue_currency as vc
    from sportsassets.workers import ext_pinnacle_loop as loop
    assert loop.PINNACLE_MAX_AGE_S == 30.0
    assert loop.MAX_VENUE_QUOTE_AGE_S == 30.0
    assert vc.MAX_BOOK_STATE_AGE_S == 30.0
    assert loop.MAX_OUR_PROCESSING_DELAY_S == 10.0
    # THE WITHDRAWAL, PINNED. If anyone reinstates the determination without
    # the evidence, this fails.
    assert loop.VENUE_STAMP_SEMANTICS == "UNRESOLVED"
    assert vc.VENUE_STAMP_SEMANTICS == "UNRESOLVED"
    # AND THE OBSERVATION IS STILL CARRIED, as an observation, with the list of
    # what it does not establish attached to it.
    d = loop.STAMP_OBSERVATION
    assert d["observed"]["contracts"] == 6
    assert d["observed"]["transact_time_identical_across_the_two_reads"] == 6
    assert d["observed"]["response_headers_captured"] == 0
    assert "conclusion" not in d
    assert any("cache" in s.lower()
               for s in d["what_this_does_NOT_establish"])
    assert "withdrawn" in d["superseded"]


def test_the_three_currency_verdicts_and_their_default():
    """NOT_ESTABLISHED is the default, and it is not the same as stale."""
    from sportsassets import bettor_venue_currency as vc
    assert vc.describe()["default"] == vc.NOT_ESTABLISHED
    assert vc.admits({"verdict": vc.ESTABLISHED}) is True
    assert vc.admits({"verdict": vc.NOT_ESTABLISHED}) is False
    assert vc.admits({"verdict": vc.CONTRADICTED}) is False
    # The three things that are never mechanisms, stated in the module so a
    # reader does not have to reconstruct the argument.
    never = " ".join(vc.describe()["never_a_mechanism"]).lower()
    assert "latency" in never and "receipt instant" in never
    assert "identical samples" in never


# ── 4 · THE AGE IS RE-AGED AT THE DECISION, NOT AT THE READ ──────────

def _subscribed(now, *, last_update_s, alive_s=1.0):
    """A live market-data subscription, the mechanism that CAN establish
    currency (M1). Supplied explicitly so a test that wants an admitted book
    has to say which contract admitted it."""
    return {"alive_at": now - alive_s, "last_update_at": now - last_update_s}


def test_the_decision_instant_governs_not_the_receipt_time():
    """`_entry_freshness` re-ages at the DECISION, and that has not changed.

    Both quantities are re-aged: the established book-state age and our own
    processing delay. Inheriting the age a book had when it was READ would
    claim a freshness the decision never had.
    """
    from sportsassets.workers import ext_pinnacle_loop as loop

    read_at = 1_000_000.0
    decided_at = read_at + 9.0          # two more network reads happened
    got = loop._entry_freshness(
        quote={"observed_at": decided_at - 3.0,
               "received_at": decided_at - 1.0},
        vq={"venue_ts": read_at - 4.0, "age_s": 4.0, "read_at": read_at,
            "age_basis": "VENUE_TRANSACT_TIME",
            # the subscription's last update was 4 s before OUR read, so at the
            # decision the established book state is 13 s old, not 4.
            "subscription": {"alive_at": decided_at - 1.0,
                             "last_update_at": read_at - 4.0}},
        now=decided_at)
    assert got["venue_currency_verdict"] == (
        "BOOK_CURRENCY_ESTABLISHED"), got["why"]
    assert got["venue_age_s"] == pytest.approx(13.0, abs=1e-3)
    assert got["venue_age_basis"] == "M1_LIVE_MARKET_DATA_SUBSCRIPTION"
    # OUR OWN DELAY IS THE OTHER NUMBER, and it is 9 s, not the 0 s it was
    # when the read was taken.
    assert got["our_processing_delay_s"] == pytest.approx(9.0, abs=1e-6)
    assert got["venue_age_at_read_s"] == 4.0
    assert got["both_reaged_at_the_decision"] is True


# ── 4a · THE DECISIVE REGRESSION ─────────────────────────────────────
#
# These two tests are the reason the receipt-only gate was withdrawn. They are
# the cases it admitted, and each of them is a book of unknown or provably old
# age being certified current.

def test_a_minutes_old_cached_snapshot_received_one_second_ago_is_not_fresh():
    """THE CASE THE RECEIPT-ONLY GATE PASSED, AND THE WHOLE REASON IT IS GONE.

    A snapshot the origin generated 300 s ago, served from a cache, returned
    fast, and received ONE SECOND before the decision. Every one of our own
    clocks looks perfect: transport latency negligible, processing delay 1 s,
    well inside 10 s. And the book is five minutes old.

    The response's own headers say so -- Date minus Age is RFC 9111 §5.1's
    generation instant for the stored representation -- so this is not merely
    unestablished, it is CONTRADICTED, which refuses on evidence.
    """
    import email.utils

    from sportsassets import bettor_venue_currency as vc
    from sportsassets.workers import ext_pinnacle_loop as loop

    now = 1_700_000_000.0
    cached = {"headers": {
        # the edge answered 2 s ago, from a representation generated 302 s ago
        "date": email.utils.formatdate(now - 2.0, usegmt=True),
        "age": "300",
        "x-cache": "HIT",
        "cache-control": "public, max-age=600"}}
    got = loop._entry_freshness(
        quote={"observed_at": now - 3.0, "received_at": now - 2.0},
        vq={"venue_ts": now - 1.0,      # a RECENT stamp: it must not rescue it
            "age_s": 1.0, "read_at": now - 1.0,
            "age_basis": "VENUE_TRANSACT_TIME",
            "http_observation": cached},
        now=now)
    assert got["fresh"] is not True, got["why"]
    assert got["venue_currency_verdict"] == vc.CONTRADICTED
    assert got["venue_age_s"] is None
    # OUR OWN NUMBERS ARE FINE, and that is precisely the point.
    assert got["our_processing_delay_s"] == pytest.approx(1.0, abs=1e-6)
    assert got["our_processing_delay_s"] < loop.MAX_OUR_PROCESSING_DELAY_S
    # AND A RECENT STAMP DOES NOT MAKE IT FRESH EITHER.
    assert got["venue_stamp_age_s"] == pytest.approx(1.0, abs=1e-6)
    assert got["venue_stamp_semantics"] == "UNRESOLVED"

    # THE SAME CASE AT THE GATE ITSELF, not only in the freshness report.
    verdict = vc.evaluate(now=now, observation=cached,
                          venue_ts=now - 1.0, our_receipt_at=now - 1.0,
                          bound_s=loop.MAX_VENUE_QUOTE_AGE_S)
    assert vc.admits(verdict) is False
    assert verdict["verdict"] == vc.CONTRADICTED
    # RENAMED with the RFC 9111 4.2.3 repair: the label named the
    # arithmetic that was removed.
    assert verdict["contradicted_by"] == "HTTP_AGE_OUTSIDE_THE_BOUND"
    # ── 301, NOT 302: THIS ASSERTION WAS STALE ───────────────────────
    #
    # It read 302.0 = the Date-based estimate (2 s) PLUS the `Age` header
    # (300 s) -- the double-count the RFC 9111 4.2.3 repair removed. Those
    # are two independent lower bounds on the SAME interval, and the
    # evaluator now takes the larger of them and never their sum. The line
    # above was updated by that repair (the label was renamed) and this
    # number was not, so the test went on pinning the arithmetic the repair
    # deleted. A test may record an open question; it must not keep a
    # corrected defect alive.
    #
    # 301 rather than 300 because the `Age` value is re-aged to `now`:
    # `Age` is the payload's age when the response LEFT THE CACHE, and it
    # kept ageing for the 1 s it sat with us. That residency is RFC 9111
    # 4.2.3's `resident_time`, it is a DIFFERENT interval from the one
    # `Age` measures, and adding it is not the double-count -- the
    # intervals are disjoint (origin -> cache-exit, then receipt -> now).
    assert verdict["origin_generation_age_s"] == pytest.approx(301.0, abs=0.1)
    assert verdict["origin_generation_age_from"] == "AGE_HEADER_LOWER_BOUND"
    assert verdict["age_header_resident_time_s"] == pytest.approx(1.0, abs=0.1)
    assert verdict["age_header_was_re_aged"] is True


def test_the_age_header_is_re_aged_to_the_decision_instant():
    """A cached book just inside the bound, plus our residency, is outside it.

    `Age: 29` against a 30 s bound looks admissible. The response left the
    cache 5 s before this decision, so the payload is 34 s old and the
    response's own headers say so.

    WHAT THIS DOES AND DOES NOT CHANGE, measured against 4ef37a7 rather
    than reasoned about. Before: 29 s, NOT_ESTABLISHED, `admits: False`.
    After: 34 s, CONTRADICTED, `admits: False`. So this is NOT an admission
    being closed -- the partial path admits nothing either way. It is the
    reported AGE becoming right and the refusal moving from "we cannot
    tell" to "the contract says it is too old", which are different facts
    about the venue and decide different investigations.
    """
    import email.utils

    from sportsassets import bettor_venue_currency as vc

    now = 1_700_000_000.0
    cached = {"headers": {
        "date": email.utils.formatdate(now - 1.0, usegmt=True),
        "age": "29",
        "x-cache": "HIT"}}
    v = vc.evaluate(now=now, observation=cached, venue_ts=now - 1.0,
                    our_receipt_at=now - 5.0, bound_s=30.0)
    assert v["origin_generation_age_s"] == pytest.approx(34.0, abs=0.1), (
        "the Age header was not re-aged by our residency")
    assert v["verdict"] == vc.CONTRADICTED
    assert vc.admits(v) is False
    assert v["age_header_resident_time_s"] == pytest.approx(5.0, abs=0.1)


def test_an_age_header_with_no_receipt_instant_says_it_understates():
    """No receipt instant means the residency is unmeasured, and it is named.

    The figure is then a lower bound known to be short by an unknown
    amount. Reporting it as though it were the age is the same class of
    error as reporting a reduced rate as a prohibition: a number that is
    not what its name says.
    """
    import email.utils

    from sportsassets import bettor_venue_currency as vc

    now = 1_700_000_000.0
    cached = {"headers": {
        "date": email.utils.formatdate(now - 1.0, usegmt=True),
        "age": "29", "x-cache": "HIT"}}
    v = vc.evaluate(now=now, observation=cached, venue_ts=now - 1.0,
                    our_receipt_at=None, bound_s=30.0)
    assert v["origin_generation_age_s"] == pytest.approx(29.0, abs=0.1)
    assert v["age_header_was_re_aged"] is False
    assert "unknown residency" in v["origin_generation_age_understates_by"]
    assert vc.admits(v) is False


def test_a_fast_read_with_no_venue_timestamp_and_no_headers_is_unestablished():
    """AN ABSENT VENUE TIMESTAMP, COVERED. And absent headers with it.

    Nothing says how old this book is. Our own processing delay is half a
    second. The verdict must be UNKNOWN -- explicitly NOT_ESTABLISHED, and
    explicitly NOT a claim that the book is stale -- and it must not be
    `fresh: true`.

    WHAT THIS TEST USED TO ASSERT. That this case is `fresh: True`, because the
    receipt instant was the whole gate and an absent stamp "costs only the
    quiet-time observation". It costs the only upstream evidence there was.
    """
    from sportsassets import bettor_venue_currency as vc
    from sportsassets.workers import ext_pinnacle_loop as loop

    now = 1_000_001.0
    got = loop._entry_freshness(
        quote={"observed_at": now - 1.0, "received_at": now - 1.0},
        vq={"venue_ts": None, "age_s": None, "read_at": now - 0.5,
            "age_basis": "VENUE_CLOCK_UNPARSEABLE",
            "http_observation": None},
        now=now)
    assert got["fresh"] is None, got["why"]
    assert got["unknown_side"] == "venue"
    assert got["venue_currency_verdict"] == vc.NOT_ESTABLISHED
    assert got["venue_age_s"] is None
    assert got["venue_stamp_age_s"] is None
    # OUR DELAY IS MEASURED AND SMALL, and it does not carry the verdict.
    assert got["our_processing_delay_s"] == pytest.approx(0.5, abs=1e-6)
    # THE REFUSAL SAYS WHAT IS MISSING, BY MECHANISM.
    named = {m["mechanism"] for m in got["mechanisms_unavailable"]}
    assert vc.M1_LIVE_SUBSCRIPTION in named
    assert vc.M2_REVALIDATION in named
    assert vc.M3_ORIGIN_GENERATION in named
    # AND IT DOES NOT CALL THE BOOK STALE.
    assert got["venue_currency"]["not_established_is_not_stale"] is True


def test_an_old_venue_timestamp_alone_does_not_prove_the_book_is_stale():
    """THE OTHER DIRECTION, AND THE FIRST GATE'S ERROR.

    A stamp two minutes old, with a live subscription establishing the book
    state at 3 s. The old stamp must not refuse: what the field denotes is
    unresolved, so it cannot be read as evidence of staleness any more than as
    evidence of currency. The established mechanism governs.
    """
    from sportsassets import bettor_venue_currency as vc
    from sportsassets.workers import ext_pinnacle_loop as loop

    now = 1_000_000.0
    got = loop._entry_freshness(
        quote={"observed_at": now - 5.0, "received_at": now - 1.0},
        vq={"venue_ts": now - 120.0, "age_s": 119.0, "read_at": now - 1.0,
            "age_basis": "VENUE_TRANSACT_TIME",
            "subscription": _subscribed(now, last_update_s=3.0)},
        now=now)
    assert got["fresh"] is True, got["why"]
    assert got["venue_currency_verdict"] == vc.ESTABLISHED
    assert got["venue_age_s"] == pytest.approx(3.0, abs=1e-3)
    assert got["venue_stamp_age_s"] == pytest.approx(120.0, abs=1e-6)
    assert "not proof the book is stale" in got["venue_stamp_decides_nothing"]
    assert got["pinnacle_age_s"] == pytest.approx(5.0)


def test_an_unmeasured_read_instant_leaves_the_verdict_unknown():
    """OURS, MISSING. An unknown delay is not a permitted one, so it blocks
    even when the book's currency IS established."""
    from sportsassets.workers import ext_pinnacle_loop as loop
    now = 1_000_001.0
    got = loop._entry_freshness(
        quote={"observed_at": now - 1.0, "received_at": now - 1.0},
        vq={"venue_ts": now - 2.0, "age_s": 1.0,
            "age_basis": "VENUE_TRANSACT_TIME",
            "subscription": _subscribed(now, last_update_s=2.0)},
        now=now)
    assert got["fresh"] is None
    assert got["unknown_side"] == "our_processing_delay"
    assert got["our_processing_delay_s"] is None
    assert got["our_processing_delay_basis"] == (
        "OUR_RECEIPT_INSTANT_NOT_RECORDED")


def test_a_read_we_sat_on_is_false_not_unknown():
    """The distinction the report has to make: false vs null. A read we held
    for four minutes, on a book whose currency WAS established, is refused on
    our own delay -- and the refusal says it is ours."""
    from sportsassets.workers import ext_pinnacle_loop as loop
    now = 1_000_000.0
    got = loop._entry_freshness(
        quote={"observed_at": now - 2.0, "received_at": now - 1.0},
        vq={"venue_ts": now - 121.0, "age_s": 1.0, "read_at": now - 240.0,
            "age_basis": "VENUE_TRANSACT_TIME",
            "subscription": _subscribed(now, last_update_s=2.0)},
        now=now)
    assert got["fresh"] is False
    assert got["our_processing_delay_s"] == pytest.approx(240.0)
    assert got["venue_age_s"] == pytest.approx(2.0, abs=1e-3)


def test_a_subscription_gone_quiet_cannot_establish_anything():
    """SILENCE ON A DEAD SOCKET LOOKS EXACTLY LIKE A QUIET MARKET.

    Which is the same confusion as the cached-bytes case, one layer up. A
    subscription that has not proven itself alive inside the liveness bound
    establishes nothing, however recent its last update claims to be.
    """
    from sportsassets import bettor_venue_currency as vc

    now = 1_000_000.0
    verdict = vc.evaluate(
        now=now,
        subscription={"alive_at": now - 90.0, "last_update_at": now - 2.0})
    assert vc.admits(verdict) is False
    assert verdict["verdict"] == vc.NOT_ESTABLISHED
    why = " ".join(m["why"] for m in verdict["mechanisms_unavailable"])
    assert "alive" in why


def test_origin_generation_alone_is_a_partial_and_never_admits():
    """M3 BOUNDS THE RESPONSE, NOT THE BOOK.

    An uncached response generated at this instant proves the HTTP response is
    new. It does not prove the matching engine's book was true then: an origin
    can build a fresh response out of an internal snapshot minutes old, and
    admitting on M3 alone certifies exactly that. It is recorded as a partial.
    """
    import email.utils

    from sportsassets import bettor_venue_currency as vc

    now = 1_700_000_000.0
    fresh_uncached = {"headers": {
        "date": email.utils.formatdate(now - 1.0, usegmt=True),
        "age": "0",
        "cache-control": "no-store",
        "etag": '"abc123"'}}
    verdict = vc.evaluate(now=now, observation=fresh_uncached,
                          our_receipt_at=now - 1.0)
    assert verdict["verdict"] == vc.NOT_ESTABLISHED
    assert vc.admits(verdict) is False
    assert verdict["partial"]["mechanism"] == vc.M3_ORIGIN_GENERATION
    assert "matching engine" in verdict["partial"]["does_not_establish"]
    assert verdict["contract"]["served_from_cache"] is False
    # AND THE VALIDATOR IT DID CARRY IS REPORTED, because that is the route to
    # M2 and therefore to an actual admission.
    assert verdict["contract"]["revalidation_possible"] is True


def test_an_in_bound_304_STILL_DOES_NOT_ESTABLISH_THE_BOOK():
    """THIS TEST USED TO ASSERT THE OPPOSITE, AND IT WAS WRONG.

    It read `test_a_304_from_the_origin_establishes_currency` and asserted
    ESTABLISHED on M2. The reasoning was that a 304 affirms what we hold. It
    affirms the REPRESENTATION, and on this venue that is demonstrably
    unconnected to the book:

        last-modified: Sun, 27 Sep 2026 16:24:27 GMT   equals `date`, exactly
        transactTime:  2026-02-20T03:07:30.947946180Z  219 days earlier
        state:         MARKET_STATE_EXPIRED, 0 bids / 0 offers

    Two independent markets, and `last-modified` held still across an 8 s gap
    while `date` advanced. So the origin really does validate a representation
    while its own market-data source is arbitrarily delayed --
    research/evidence/VENUE_BOOK_PROTOCOL_2026-09-27.md, runs 36332797806 and
    36333087522.

    The exchange still happens and is still reported. What changed is that it
    cannot admit, which is the difference between a signal and a certificate.
    """
    import email.utils

    from sportsassets import bettor_venue_currency as vc

    now = 1_700_000_000.0
    verdict = vc.evaluate(
        now=now,
        observation={"headers": {"etag": '"abc123"'}},
        revalidation={"status": 304,
                      "headers": {"date": email.utils.formatdate(
                          now - 4.0, usegmt=True)}})
    assert verdict["verdict"] == vc.NOT_ESTABLISHED
    assert vc.admits(verdict) is False
    assert verdict["mechanism"] == vc.NO_MECHANISM
    # THE EXCHANGE IS REPORTED AS HAVING SUCCEEDED, so this is not mistaken for
    # a request that failed.
    assert verdict["revalidation"]["affirmed_in_bound"] is True
    m2 = [m for m in verdict["mechanisms_unavailable"]
          if m["mechanism"] == vc.M2_REVALIDATION][0]
    assert m2["exchange_succeeded"] is True
    assert "affirms the representation rather than the book" in m2["why"]
    assert "third false certificate" in m2["why"]
    # AND M2 IS NO LONGER LISTED AS ESTABLISHING.
    assert vc.M2_REVALIDATION not in vc.ESTABLISHING_MECHANISMS
    assert vc.M2_REVALIDATION in vc.PARTIAL_MECHANISMS


def test_the_withdrawal_of_M2_carries_its_evidence():
    """A mechanism removed on evidence records the evidence, so the change is
    auditable rather than a quiet edit in the other direction."""
    from sportsassets import bettor_venue_currency as vc

    w = vc.M2_WITHDRAWN_AS_ESTABLISHING
    assert w["withdrawn_on"] == "2026-09-27"
    o = w["the_contradicting_observation"]
    # THE OBSERVATION, AS OBSERVED. My first statement of this said
    # "last-modified equals date, exactly" -- true of the ORIGIN read and false
    # of the cache HIT, where date advanced 8 s and last-modified did not.
    assert o["read_1"]["cf_cache_status"] == "EXPIRED"
    assert o["read_2"]["cf_cache_status"] == "HIT"
    assert o["read_1"]["last_modified"] == o["read_2"]["last_modified"]
    assert o["read_1"]["date"] != o["read_2"]["date"]
    assert o["market_state"] == "MARKET_STATE_EXPIRED"
    assert o["book_levels"].startswith("0 bids")
    assert "VENUE_BOOK_PROTOCOL_2026-09-27" in o["source"]
    # AND THE INFERENCE, AS A SEPARATE KEY, resting on the book being KNOWN
    # static rather than on any claim about what a timestamp may precede.
    assert "cannot have had its book change today" in w["the_inference"]
    assert "not a book clock" in w["the_inference"]
    assert "withdrawn as false" in w["what_this_inference_does_NOT_rely_on"]
    assert "not a book clock" in w["so"]
    assert "only for the origin read" in (
        w["and_last_modified_does_not_track_Date"])
    # AND WHAT WOULD BRING IT BACK IS NAMED, because "withdrawn" must not mean
    # "closed": an absent route and an unproven one are different findings.
    assert "documenting that its validator changes" in (
        w["what_would_make_it_establishing"])


def test_the_three_response_ages_are_kept_apart_from_the_book_age():
    """FOUR QUANTITIES, AND COLLAPSING ANY TWO IS HOW EACH FALSE CERTIFICATE
    GOT BUILT. Transport latency, our receipt instant and now an HTTP validator
    were each, at some point, about to stand in for the one age that matters."""
    from sportsassets import bettor_venue_currency as vc

    ages = vc.DISTINCT_AGES
    assert set(ages) == {"HTTP_CACHE_AGE", "ORIGIN_GENERATION_AGE",
                         "OUR_OBSERVATION_AGE", "UPSTREAM_MARKET_DATA_AGE"}
    for name in ("HTTP_CACHE_AGE", "ORIGIN_GENERATION_AGE",
                 "OUR_OBSERVATION_AGE"):
        assert "a market-data age" in ages[name]["is_not"], name
    up = ages["UPSTREAM_MARKET_DATA_AGE"]
    assert up["from"] == "NO HEADER STATES IT"
    assert "the only one that matters" in up["bounds"]
    assert "transactTime" in up["the_only_candidate"]
    assert "unresolved" in up["the_only_candidate"]
    # OUR OWN RECEIPT INSTANT IS FLAGGED AS THE MOST DANGEROUS, because it is
    # the one that reads as a measurement while being zero by construction.
    assert "most dangerous" in ages["OUR_OBSERVATION_AGE"]["is_not"]


def test_a_cache_hit_inside_the_bound_is_still_not_the_origin_speaking():
    """A representation generated 5 s ago and served from a cache. Inside the
    bound, and still not an establishment: the cache is not the origin, and a
    cache hit is the hypothesis the whole correction exists to keep live."""
    import email.utils

    from sportsassets import bettor_venue_currency as vc

    now = 1_700_000_000.0
    verdict = vc.evaluate(now=now, observation={"headers": {
        "date": email.utils.formatdate(now - 1.0, usegmt=True),
        "age": "4",
        "x-cache": "HIT"}})
    assert verdict["verdict"] == vc.NOT_ESTABLISHED
    assert verdict["contract"]["served_from_cache"] is True
    assert verdict["partial"] is None


# ── 5 · THE RAW VALUE TRAVELS WITH THE VERDICT ───────────────────────

def test_the_clock_record_names_the_field_the_value_and_the_parser():
    """A refusal reading only "unparseable" cannot be acted on."""
    import inspect

    from sportsassets.workers import ext_pinnacle_loop as loop
    src = inspect.getsource(loop.venue_quote)
    for key in ("field_path", "raw_type", "parser", "parser_accepts",
                "parsed_epoch_s", "age_at_read_s"):
        assert '"%s"' % key in src, key
    assert "marketData.transactTime" in src


# ── 6 · THE HEARTBEAT CARRIES THE NUMBER IT REFUSED ON ───────────────

def test_a_stale_refusal_is_only_reachable_once_the_age_is_measured():
    """Run 75's proof that the parse works, stated as a rule.

    `VENUE_QUOTE_STALE` comes from exactly one `return`, and it sits
    behind `age is not None`. When the clock does not parse, `age` is
    None and this refusal CANNOT fire -- which is why seeing it in
    production established the repair. If that guard is ever relaxed to
    fire on an unmeasured age, an UNKNOWN would start reporting itself as
    an observed stale book, and this test is what stops it.
    """
    import inspect
    import re

    from sportsassets.workers import ext_pinnacle_loop as loop
    src = inspect.getsource(loop.venue_quote)
    # THE PROPERTY, ACROSS BOTH REWRITES OF THIS GATE: an UNMEASURED age must
    # never report itself as an OBSERVED stale book. There are now two refusals
    # and the distinction between them is exactly that property.
    #
    #   R_BOOK_CURRENCY_CONTRADICTED  fires only when the response's own
    #       headers date the payload outside the bound. Evidence.
    #   R_BOOK_CURRENCY_NOT_ESTABLISHED  fires when nothing established it, and
    #       carries `unmeasured: True` plus an explicit statement that it is
    #       not an observation of staleness.
    #
    # One return each, and the contradicted branch is reachable only through a
    # verdict of CONTRADICTED -- which `bettor_venue_currency` sets only from a
    # dated contract, never from an absence.
    contra = [m for m in re.finditer(
        r'if currency\["verdict"\] == vc\.CONTRADICTED:', src)]
    assert len(contra) == 1, "the contradiction branch moved or was duplicated"
    after = src[contra[0].end():contra[0].end() + 300]
    assert "R_BOOK_CURRENCY_CONTRADICTED" in after
    assert src.count("R_BOOK_CURRENCY_CONTRADICTED") == 1
    # THE UNESTABLISHED BRANCH SAYS IT IS UNMEASURED AND SAYS IT IS NOT STALE.
    unest = src[src.index("R_BOOK_CURRENCY_NOT_ESTABLISHED"):][:900]
    assert '"unmeasured": True' in unest
    assert "this_is_not_a_stale_book" in unest
    assert "mechanisms_unavailable" in unest
    # AND OUR OWN DELAY REFUSES UNDER ITS OWN NAME, exactly once, never as a
    # statement about the book.
    delay = [m for m in re.finditer(
        r"if our_delay > MAX_OUR_PROCESSING_DELAY_S:", src)]
    assert len(delay) == 1, "the processing-delay gate moved or was duplicated"
    assert src.count("R_OUR_PROCESSING_DELAY") == 1
    # THE TWO WITHDRAWN GATES ARE GONE FROM THIS FUNCTION.
    assert "R_VENUE_QUOTE_STALE" not in src, (
        "the venue stamp's age must not refuse a candidate: what it denotes "
        "is unresolved")
    assert "R_OUR_READ_IS_STALE" not in src, (
        "our own receipt age must not be the whole gate: it admits a cached "
        "snapshot returned quickly")


def test_the_venue_error_projection_carries_the_age_limit_and_raw_value():
    """A refusal that names a number must show it.

    Run 75 printed `VENUE_QUOTE_STALE` on
    `aec-mlb-nym-wsh-2026-09-26` and could not say by how much the book
    was stale or what string it read, because the heartbeat projection
    kept only the slug and the code.
    """
    import inspect

    from sportsassets.workers import ext_pinnacle_loop as loop
    src = inspect.getsource(loop.cycle)
    for key in ("age_s", "limit_s", "age_basis", "venue_clock"):
        assert '"%s"' % key in src, key
    assert 'diag["venue_clock"]' in src
    for key in ("field_path", "raw", "raw_type", "parsed_epoch_s",
                "age_at_read_s", "basis"):
        assert '"%s"' % key in src, key


# ── 7 · THE SEMANTICS ARE NOT ESTABLISHED, AND THE CODE SAYS SO ──────

def test_the_module_declares_the_clock_semantics_unestablished():
    """The claim the gate rests on, marked as a claim.

    `decision_instant - transactTime` measures OUR staleness only if the
    venue stamps the RESPONSE. If it stamps the LAST BOOK CHANGE, an old
    value means the book has not moved -- the ordinary condition of a quiet
    pre-game money line -- and refusing on it would refuse every quiet book
    and call the result freshness.

    Two other modules here took the other side and they are the established
    execution path: `institutional_book.current()` computes
    FRESHNESS_STATUS from BETTOR_RECEIVED_TIMESTAMP and uses transactTime
    only for MARKET_DATA_LAG_MS; `obs/streamstate` forbids subtracting a
    venue timestamp from a local reading outright.
    """
    from sportsassets.workers import ext_pinnacle_loop as loop
    assert loop.VENUE_CLOCK_SEMANTICS.startswith("UNESTABLISHED")
    assert "LAST_BOOK_UPDATE" in loop.VENUE_CLOCK_SEMANTICS
    assert "RESPONSE_STAMP" in loop.VENUE_CLOCK_SEMANTICS


def test_the_freshness_limit_is_not_relaxed_while_the_basis_is_unestablished():
    """RELAXING A GATE ON AN UNESTABLISHED READING IS LOOSENING IT.

    The conservative direction is to keep refusing. This pins the two
    limits against the values the lane has always used, so a future edit
    that widens them to let candidates through has to change this test and
    say why.
    """
    from sportsassets.workers import ext_pinnacle_loop as loop
    assert loop.MAX_VENUE_QUOTE_AGE_S == 30.0
    assert loop.PINNACLE_MAX_AGE_S == 30.0


def test_a_book_refusal_owes_five_numbers_in_the_candidate_ledger():
    """Raw value, parsed stamp, decision instant, age and limit.

    "VENUE_QUOTE_STALE" with no number is not a finding anybody can act
    on, and the semantics question cannot even be asked without the raw
    string.
    """
    import inspect

    from sportsassets.workers import ext_pinnacle_loop as loop
    src = inspect.getsource(loop.cycle)
    for key in ("raw_transact_time", "parsed_epoch_s",
                "decision_instant_epoch_s", "age_s", "limit_s",
                "age_semantics"):
        assert '"%s"' % key in src, key


# ── 8 · THE CYCLE'S OWN LABEL, AND WHAT IT REFUSES TO CLAIM ──────────

def test_a_cycle_that_evaluated_nothing_is_labelled_an_input_path_block():
    """NOT "zero positive edge" -- that is a claim about the market.

    A cycle whose candidates never reached execution estimation did not
    measure the market, so it establishes nothing about available edge.
    """
    from sportsassets.workers import ext_pinnacle_loop as loop
    assert loop.ZERO_EVALUATED_INPUT_PATH_BLOCKED == \
        "ZERO_EVALUATED__INPUT_PATH_BLOCKED"
    import inspect
    src = inspect.getsource(loop.cycle)
    assert "ZERO_EVALUATED_INPUT_PATH_BLOCKED if evaluated == 0" in src
    assert '"cycle_label"' in src


def test_every_mapped_candidate_gets_a_ledger_row():
    """One row per candidate that reached the mapping, at its first refusal.

    Run 75 reported `mapped 3 -> evaluated 0` with no reconciliation,
    because the only per-candidate census lived in a route whose step had
    died. Three candidates failing one gate and one candidate failing three
    are different facts.
    """
    import inspect

    from sportsassets.workers import ext_pinnacle_loop as loop
    src = inspect.getsource(loop.cycle)
    # the identity, book, record and admitted paths all record
    assert src.count("_ledger({") >= 4, src.count("_ledger({")
    assert '"first_refusal"' in src
    assert '"mapped_candidate_ledger": ledger' in src


# ── 9 · THE PROBE OBSERVES; IT DOES NOT CLASSIFY ─────────────────────

def test_the_clock_probe_returns_no_semantics_verdict():
    """TWO SAMPLES DO NOT SETTLE A TIMESTAMP'S MEANING.

    A quiet book and a stalled feed look identical; a stamp can advance
    for reasons other than a response; and a gap of seconds on a handful
    of contracts is not a sample. A verdict field here would be the same
    mistake as the period rule's first three versions -- a rule read off a
    vocabulary nobody had established.
    """
    import inspect

    from sportsassets.api import app as A
    src = inspect.getsource(A.api_venue_clock_probe)
    assert '"classifies_semantics": False' in src
    assert '"changes_admission": False' in src
    for banned in ("LAST_UPDATE_CLOCK", "RESPONSE_CLOCK",
                   "AGE_IS_A_PATH_LAG_MEASUREMENT",
                   "AGE_IS_MARKET_QUIET"):
        assert banned not in src, banned
    # what it DOES return: raw stamps, our two local clocks, book hashes
    for key in ("raw_transact_time", "asked_at_epoch_s",
                "returned_at_epoch_s", "round_trip_s", "book_sha16",
                "transact_time_delta_s", "our_receipt_delta_s",
                "book_sha_changed", "configured_limit_s"):
        assert '"%s"' % key in src, key


def test_the_probe_records_why_the_established_contract_does_not_transfer():
    """institutional_book's guarantee is about a CACHE, and it was checked.

    FRESHNESS_LIMIT_S = 5.0 s measures BETTOR_RECEIVED_TIMESTAMP, and the
    reason that is meaningful there is `workers/institutional_md`: a
    persistent poll process refreshes an in-memory store at a tight cadence
    and its consumer reads memory rather than calling REST to decide. This
    lane calls REST AT the decision, so receipt age is the round trip by
    construction and the check cannot fail.
    """
    import inspect

    from sportsassets.api import app as A
    src = inspect.getsource(A.api_venue_clock_probe)
    assert "why_it_does_not_transfer" in src
    assert "institutional_book.FRESHNESS_LIMIT_S" in src
    assert "workers.institutional_md" in src

    from sportsassets.workers import ext_pinnacle_loop as loop
    import sportsassets.workers.ext_pinnacle_loop as mod
    modsrc = inspect.getsource(mod)
    assert "RECEIPT TIME ALONE DOES NOT PROVE THE" in modsrc
    assert "THE EXPLICIT REFUSAL IS RETAINED" in modsrc
    # and the established limit is real, so the comparison is not invented
    from sportsassets import institutional_book as ib
    assert ib.FRESHNESS_LIMIT_S == 5.0
    assert loop.MAX_VENUE_QUOTE_AGE_S == 30.0
