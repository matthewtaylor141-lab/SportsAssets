"""THE EVIDENCE THE GATE NEEDS, AND THE GATE ON THE SCHEDULED PATH.

Two things are proved here, and the second is the one component tests cannot
reach:

  1 · `venue_http_observer` records the response metadata the venue SDK
      discards, records only the whitelist, and never touches a request.

  2 · `venue_quote` -- the function the SCHEDULED LOOP actually calls, through
      the real `_read_book_blocking` and the real SDK client object, with only
      the transport stubbed -- refuses a cached snapshot, refuses a book with no
      contract at all, and admits only when a mechanism with a published
      contract says so.

WHY 2 IS HERE. The correction being pinned is that a receipt-age check cannot
establish upstream freshness. A unit test of `_entry_freshness` proves the
verdict function; it does not prove that the loop's own read path collects the
evidence the verdict needs. The first version of this work passed every
component test and still shipped a gate that would certify a four-minute-old
cached book, because nothing exercised the path that was supposed to notice.
"""

from __future__ import annotations

import asyncio
import email.utils
import time

import pytest

from sportsassets import bettor_venue_currency as vc
from sportsassets import venue_http_observer as vho
from sportsassets.workers import ext_pinnacle_loop as loop


# ── 1 · THE RECORDER ────────────────────────────────────────────────

class _FakeHeaders(dict):
    def get(self, name, default=None):            # case-insensitive, like httpx
        return dict.get(self, str(name).lower(), default)


class _FakeRequest:
    def __init__(self, path, method="GET"):
        self.method = method
        self.url = type("U", (), {"path": path})()


class _FakeResponse:
    def __init__(self, path, headers, status=200):
        self.request = _FakeRequest(path)
        self.headers = _FakeHeaders({k.lower(): v for k, v in headers.items()})
        self.status_code = status


class _FakeHttp:
    """Just enough httpx.Client for the hook to be installed on."""

    def __init__(self):
        self.event_hooks = {"response": []}


class _FakeSdkClient:
    def __init__(self):
        self._http = _FakeHttp()


@pytest.fixture(autouse=True)
def _clean():
    vho.reset()
    yield
    vho.reset()


def test_the_hook_records_the_whitelist_and_nothing_else():
    client = _FakeSdkClient()
    assert vho.install(client)["installed"] is True
    hook = client._http.event_hooks["response"][0]

    hook(_FakeResponse("/v1/markets/aec-x/book", {
        "Date": "Sun, 27 Sep 2026 12:00:00 GMT",
        "Age": "12",
        "Cache-Control": "public, max-age=30",
        "ETag": '"v1"',
        # THINGS THAT MUST NOT BE RECORDED.
        "Set-Cookie": "session=secret",
        "Authorization": "Bearer nope",
        "Content-Type": "application/json"}))

    row = vho.take("/v1/markets/aec-x/book")
    assert row is not None
    assert row["headers"]["date"] == "Sun, 27 Sep 2026 12:00:00 GMT"
    assert row["headers"]["age"] == "12"
    assert row["headers"]["etag"] == '"v1"'
    assert "set-cookie" not in row["headers"]
    assert "authorization" not in row["headers"]
    assert "content-type" not in row["headers"]
    assert row["status"] == 200
    # ABSENCE IS RECORDED AS ABSENCE, not omitted -- a missing header is the
    # fact a refusal has to name.
    assert "last-modified" in row["headers_absent"]


def test_installing_twice_does_not_stack_hooks():
    client = _FakeSdkClient()
    vho.install(client)
    vho.install(client)
    vho.install(client)
    assert len(client._http.event_hooks["response"]) == 1


def test_an_observation_is_taken_not_left_behind():
    """A header set silently reused on a later read is the failure this
    evidence exists to detect, so `take` removes it."""
    client = _FakeSdkClient()
    vho.install(client)
    hook = client._http.event_hooks["response"][0]
    hook(_FakeResponse("/v1/markets/a/book", {"Date": "x"}))
    assert vho.take("/v1/markets/a/book") is not None
    assert vho.take("/v1/markets/a/book") is None
    # ... but the fact that this path was EVER observed survives, because
    # "never observed" and "observed and consumed" are different diagnoses.
    assert vho.ever_seen("/v1/markets/a/book") is True


def test_a_broken_hook_body_cannot_break_a_read():
    """A bookkeeping failure must not take down a market-data read."""
    client = _FakeSdkClient()
    vho.install(client)
    hook = client._http.event_hooks["response"][0]

    class Hostile:
        request = None

        @property
        def headers(self):
            raise RuntimeError("boom")

    hook(Hostile())                                   # must not raise


def test_a_client_without_an_httpx_client_reports_it_rather_than_raising():
    got = vho.install(object())
    assert got["installed"] is False
    assert "httpx" in got["why"]


# ── 2 · THE SCHEDULED READ PATH, END TO END ─────────────────────────
#
# The real `_read_book_blocking`, the real `venue_quote`, the real currency
# evaluator. Only the SDK's transport is replaced -- and it is replaced at the
# httpx layer, so the observer hook the loop installs is the thing that
# actually records the headers.

_MD = {
    "marketSlug": "aec-test-book",
    "bids": [{"price": "0.40", "qty": "500"}],
    "offers": [{"price": "0.45", "qty": "500"}],
    "state": "OPEN",
}


def _wire(monkeypatch, headers):
    """Make `pmus.book_read` go through a client whose hook fires with
    `headers`, exactly as a real response would."""
    from sportsassets import pmus

    client = _FakeSdkClient()

    def fake_book_read(_client, slug, md=_MD):
        # The transport, standing in for httpx: run the installed hooks with a
        # response carrying `headers`, then return the parsed body -- which is
        # precisely what the SDK does, minus the headers it throws away.
        for hook in client._http.event_hooks["response"]:
            hook(_FakeResponse(vho.book_path(slug), headers))
        return {"marketData": dict(md), "feed": "book", "error": None}

    monkeypatch.setattr(pmus, "_get_client", lambda: client)
    monkeypatch.setattr(pmus, "book_read", fake_book_read)
    monkeypatch.setattr(loop, "pace_is_disabled", True, raising=False)
    from sportsassets import venue_pace
    monkeypatch.setattr(venue_pace, "pace", lambda *a, **k: None)
    return client


def _quote(**kw):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        loop.venue_quote(None, us_slug="aec-test-book", intent="LONG",
                         now=kw.pop("now"), **kw))


def test_the_scheduled_read_refuses_a_minutes_old_cached_snapshot(monkeypatch):
    """THE DECISIVE CASE, ON THE PATH THE SCHEDULER USES.

    The origin generated this representation 300 s ago and a cache served it.
    Our transport was instant and our decision follows immediately, so every
    one of our own clocks is perfect. The read must refuse, the refusal must be
    the CONTRADICTED one -- this is evidence, not an absence -- and it must not
    be reported as a processing-delay problem.
    """
    now = time.time()
    _wire(monkeypatch, {
        "Date": email.utils.formatdate(now - 2.0, usegmt=True),
        "Age": "300",
        "X-Cache": "HIT",
        "Cache-Control": "public, max-age=600"})

    got = _quote(now=now + 0.5)
    assert got["ok"] is False
    assert got["refusal"] == loop.R_BOOK_CURRENCY_CONTRADICTED
    assert got["book_currency"]["verdict"] == vc.CONTRADICTED
    assert got["book_currency"]["contradicted_by"] == (
        "HTTP_AGE_OUTSIDE_THE_BOUND")
    # ── 300.5, NOT 302.5, AND THE OLD NUMBER WAS THE DOUBLE-COUNT ────
    #
    # This fixture is `Date` = now-2, `Age: 300`, decision at now+0.5, and
    # the docstring above says the representation is 300 s old. The old
    # arithmetic reported 302.5 for it: `now+0.5 - (Date - Age)` =
    # 0.5 + 2.0 + 300, adding the 2 s of Date offset to the 300 s of Age.
    # The test's own prose and its own number disagreed.
    #
    # RFC 9111 4.2.3: apparent_age 2.0, corrected_age_value 300, their
    # MAXIMUM 300, plus 0.5 s residence = 300.5.
    #
    # THE REFUSAL IS UNCHANGED and that is the point -- 300 s is far
    # outside a 30 s bound either way. What changed is that the reported
    # age is now the true one, so a borderline case lands on the right
    # side of the line instead of being inflated over it.
    assert got["age_s"] == pytest.approx(300.5, abs=1.0)
    # AND THE HEADERS REALLY TRAVELLED THROUGH THE LOOP'S OWN READ PATH.
    contract = got["book_currency"]["contract"]
    assert contract["age_header_s"] == 300
    assert contract["served_from_cache"] is True
    assert got["venue_clock"]["http_observer"]["installed"] is True


def test_the_scheduled_read_refuses_when_no_mechanism_is_available(monkeypatch):
    """THE STATE THE LANE IS IN TODAY, ON THE SCHEDULED PATH.

    A response with no cache metadata at all, no subscription and no
    revalidation. Refused as NOT_ESTABLISHED, carrying `unmeasured: True`, the
    statement that this is not a stale book, and the list of mechanisms it
    lacked -- which is what makes the blocker actionable rather than a mystery.
    """
    now = time.time()
    _wire(monkeypatch, {"Content-Type": "application/json"})

    got = _quote(now=now)
    assert got["ok"] is False
    assert got["refusal"] == loop.R_BOOK_CURRENCY_NOT_ESTABLISHED
    assert got["unmeasured"] is True
    assert "not an observation that it is old" in got["this_is_not_a_stale_book"]
    named = {m["mechanism"] for m in got["mechanisms_unavailable"]}
    assert named == {vc.M1_LIVE_SUBSCRIPTION, vc.M2_REVALIDATION,
                     vc.M3_ORIGIN_GENERATION}
    assert got["partial"] is None
    # THE STAMP IS ABSENT FROM THIS BOOK AND THAT IS NOT WHY IT REFUSED.
    assert got["venue_clock"]["stamp_semantics"] == "UNRESOLVED"


def test_the_scheduled_read_admits_on_a_live_subscription(monkeypatch):
    """AND IT DOES ADMIT, when a published contract establishes the state.

    Same book, same transport. The difference is a market-data subscription
    proven alive whose last update for this market arrived 3 s ago -- mechanism
    M1. The full ladder comes back, so the rest of the lane can proceed.
    """
    now = time.time()
    _wire(monkeypatch, {"Content-Type": "application/json"})

    got = _quote(now=now,
                 subscription={"alive_at": now - 1.0,
                               "last_update_at": now - 3.0})
    assert got["ok"] is True, got.get("why")
    assert got["book_currency"]["verdict"] == vc.ESTABLISHED
    assert got["book_currency"]["mechanism"] == vc.M1_LIVE_SUBSCRIPTION
    assert got["book_currency"]["book_state_age_s"] == pytest.approx(3.0,
                                                                     abs=0.5)
    assert got["ask"] is not None
    assert got["depth"] is not None


def test_an_established_book_we_then_sat_on_refuses_on_our_own_delay(
        monkeypatch):
    """BOTH REQUIREMENTS, AND THE SECOND ONE STILL BITES.

    Currency established by M1, and then we hold the read for 25 s before
    deciding. Refused -- on OUR delay, under its own name, with the refusal
    saying explicitly that the currency was established and this is about us.
    """
    now = time.time()
    _wire(monkeypatch, {"Content-Type": "application/json"})

    got = _quote(now=now + 25.0,
                 subscription={"alive_at": now + 24.0,
                               "last_update_at": now + 23.0})
    assert got["ok"] is False
    assert got["refusal"] == loop.R_OUR_PROCESSING_DELAY
    assert got["age_s"] == pytest.approx(25.0, abs=1.0)
    assert got["limit_s"] == loop.MAX_OUR_PROCESSING_DELAY_S
    assert "about our own delay and nothing upstream" in got["why"]
    assert got["book_currency"]["verdict"] == vc.ESTABLISHED


def test_a_304_revalidation_DOES_NOT_admit_on_the_scheduled_path(monkeypatch):
    """M2 WAS WITHDRAWN AS AN ESTABLISHING MECHANISM, and this test asserted the
    opposite.

    A 304 affirms the REPRESENTATION. On this venue `last-modified` read TODAY on
    markets whose books cannot have moved since February -- EXPIRED, zero levels,
    a February `transactTime`. A validator that moves while the book provably does
    not is not a book clock.

    THE EXCHANGE STILL HAPPENS AND IS STILL REPORTED. What changed is that it
    cannot admit, which is the difference between a signal and a certificate --
    and having found ETag absent I was one step from treating a validator's
    PRESENCE as the qualifying condition, which would have been the third false
    certificate after transport latency and our own receipt instant.
    """
    now = time.time()
    _wire(monkeypatch, {"ETag": '"v7"'})

    got = _quote(now=now,
                 revalidation={"status": 304,
                               "headers": {"date": email.utils.formatdate(
                                   now - 2.0, usegmt=True)}})
    assert got["ok"] is False
    assert got["refusal"] == loop.R_BOOK_CURRENCY_NOT_ESTABLISHED
    # AND M2 IS RECORDED AS AN EXCHANGE THAT SUCCEEDED, so this is not mistaken
    # for a request that failed.
    m2 = [m for m in got["book_currency"]["mechanisms_unavailable"]
          if m["mechanism"] == vc.M2_REVALIDATION][0]
    assert m2["exchange_succeeded"] is True
    assert "affirms the representation rather than the book" in m2["why"]
    assert vc.M2_REVALIDATION not in vc.ESTABLISHING_MECHANISMS


def test_an_uncached_fresh_response_alone_still_refuses(monkeypatch):
    """M3 IS A PARTIAL AND THE SCHEDULED PATH TREATS IT AS ONE.

    `Age: 0`, `Cache-Control: no-store`, `Date` one second ago: the response is
    certainly newly generated at the origin. That is not the book's state, and
    admitting on it would certify an origin serving an internally stale
    snapshot. Refused -- and the partial is reported, so the evidence is not
    lost.
    """
    now = time.time()
    _wire(monkeypatch, {
        "Date": email.utils.formatdate(now - 1.0, usegmt=True),
        "Age": "0", "Cache-Control": "no-store", "ETag": '"v9"'})

    got = _quote(now=now)
    assert got["ok"] is False
    assert got["refusal"] == loop.R_BOOK_CURRENCY_NOT_ESTABLISHED
    assert got["partial"]["mechanism"] == vc.M3_ORIGIN_GENERATION
    assert "matching engine" in got["partial"]["does_not_establish"]
    # AND THE VALIDATOR IS REPORTED, because it is the route to M2.
    assert got["book_currency"]["contract"]["revalidation_possible"] is True
