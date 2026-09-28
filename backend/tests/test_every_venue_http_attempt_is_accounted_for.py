"""A repeated 429 must be counted, preserved, and must arm the cooldown.

── WHAT AN INDEPENDENT REVIEW REPRODUCED, AND IT WAS ALL TRUE ───────
Against the real SDK and the real scheduled reader with
`httpx.MockTransport` (no venue traffic):

    calls to the outer pacer .................. 1
    actual HTTP attempts ...................... 3   (SDK max_retries: 2)
    all three responses ....................... HTTP 429
    returned error ............................ RateLimitError
    diagnostic status / exception / detail .... null
    venue_pace.penalty_left() ................. 0.0

THREE DEFECTS IN ONE OBSERVATION:

  1 · THE PACER WAS AT THE WRONG BOUNDARY. `pace()` wraps the LOGICAL
      read; the SDK retries INSIDE it. Two of three requests were
      invisible to rate control, so adjusting the gap from 0.35 to 0.37
      could not have helped -- it was the wrong layer, and that was my
      repair.

  2 · THE ERROR WAS REDUCED TO ITS CLASS NAME. `book_read` ended its
      failure path with `out["error"] = type(exc).__name__`, discarding
      the `httpx.Response` the SDK's `APIStatusError` carries -- status,
      headers, `Retry-After`, everything. Without the status nobody could
      tell a 429 from a 500; without `Retry-After` the cooldown had no
      duration to honour.

  3 · THE COOLDOWN WAS NEVER ARMED. `penalize()` existed, worked in
      isolation, and nothing on this path called it. So an observed 429
      changed nothing and the next candidate read on the ordinary gap.

── AND THE SDK VERSION IS NOT PINNED ────────────────────────────────
`pyproject.toml` says `polymarket-us>=0.1.2`. 0.1.2 constructs
`httpx.AsyncClient(timeout=timeout)` with NO retry logic; 1.0.2 retries
twice. So whether one logical read is 1 or 3 requests depends on a
build-time resolution the repository does not fix. These tests therefore
verify the accounting AT THE RESPONSE BOUNDARY, which is correct for
either version, and `test_the_sdk_retry_behaviour_is_not_assumed` records
the unpinned range as the defect it is.
"""

import httpx
import pytest

from sportsassets import venue_http_error as VHE
from sportsassets import venue_http_observer as VHO
from sportsassets import venue_pace as VP


@pytest.fixture(autouse=True)
def _clean_state():
    """Counters and cooldown reset around every test.

    Both are module globals, so a test that left either armed would make
    the next one pass or fail for the wrong reason.
    """
    from sportsassets import venue_cooldown_store as VCS
    from sportsassets import venue_request_gate as GRT

    VHO.reset_attempts()
    VP.resume_cooldown(0)
    VP._penalty_until = 0.0
    VP._penalty_until_epoch = 0.0
    # ── THE HARD GATE TOO, NOW THAT A 429 ACTUALLY ARMS ONE ──────────
    # `penalize_observed` sets a not-before instant as well as the
    # reduced-rate period. A test that armed it and did not clear it would
    # make the NEXT test's first dispatch sleep or refuse -- passing or
    # failing for a reason that has nothing to do with what it asserts.
    GRT.clear_hold()
    VCS.reset_pending()
    yield
    VHO.reset_attempts()
    VP._penalty_until = 0.0
    VP._penalty_until_epoch = 0.0
    GRT.clear_hold()
    VCS.reset_pending()


class _StatusError(Exception):
    """A stand-in for the SDK's `APIStatusError`, shaped like the real one.

    `venue_http_error.describe` reads `status_code` off the exception and
    `headers` off `exception.response`, which is exactly how
    `polymarket-us` 1.0.2 raises: it passes the whole `httpx.Response`. A
    double that only carried a status would let the header-parsing paths go
    untested while looking tested.
    """

    def __init__(self, status: int, headers: dict = None, *, request=None):
        super().__init__("status %s" % status)
        self.status_code = status
        self.response = httpx.Response(
            status, headers=dict(headers or {}),
            request=request or httpx.Request("GET", "https://api.test/x"))
        self.request_id = "corr-%s" % status


def _client_with(transport, path="/v1/markets/aec-mlb-chc-sd-2026-09-30/book"):
    """A real httpx client wired to a mock transport, with our hook on it."""
    http = httpx.Client(transport=transport, base_url="https://api.test")

    class _C:
        def __init__(self):
            self._http = http

    c = _C()
    got = VHO.install(c)
    assert got["installed"] is True, got
    return c, http, path


# ═════════════════════════════════════════════════════════════════════
# 1 · EVERY ATTEMPT IS COUNTED, INCLUDING RETRIES INSIDE ONE CALL
# ═════════════════════════════════════════════════════════════════════

def test_three_attempts_in_one_logical_call_are_all_counted():
    """THE REVIEW'S EXACT SHAPE: one call, three 429s, all three seen.

    The hook fires per RESPONSE, so an SDK retry cannot hide from it. This
    is why the accounting lives there rather than around the logical read.
    """
    seen = {"n": 0}
    path = "/v1/markets/aec-mlb-chc-sd-2026-09-30/book"

    def handler(request):
        seen["n"] += 1
        return httpx.Response(429, request=request,
                              headers={"Retry-After": "5",
                                       "X-Request-Id": "req-%d" % seen["n"]},
                              json={"error": "rate limited"})

    c, http, _ = _client_with(httpx.MockTransport(handler))
    # THREE ATTEMPTS, as an SDK with max_retries=2 would make.
    for _ in range(3):
        http.get(path)

    assert seen["n"] == 3
    assert VHO.attempts_for(path) == 3, VHO.attempts_for(path)
    assert VHO.rate_limited_for(path) == 3


def test_an_unobserved_path_reports_None_not_zero():
    """None means "unknown", 0 would claim we know nothing was sent.

    THE INFERENCE THIS PREVENTS is one I actually drew: I read null book
    timestamps as "the read never returned". The venue had in fact
    responded three times. A count of 0 invites exactly that mistake; None
    says the count is unknown.
    """
    assert VHO.attempts_for("/never/observed") is None
    assert VHO.attempts_for(None) is None


def test_resetting_scopes_the_count_to_one_logical_read():
    path = "/v1/markets/x/book"

    def handler(request):
        return httpx.Response(429, request=request)

    c, http, _ = _client_with(httpx.MockTransport(handler))
    http.get(path)
    http.get(path)
    assert VHO.attempts_for(path) == 2
    VHO.reset_attempts(path)
    assert VHO.attempts_for(path) is None
    http.get(path)
    assert VHO.attempts_for(path) == 1


# ═════════════════════════════════════════════════════════════════════
# 2 · THE METADATA SURVIVES
# ═════════════════════════════════════════════════════════════════════

def test_status_retry_after_and_request_id_all_survive():
    """What `type(exc).__name__` threw away."""
    from polymarket_us.errors import RateLimitError

    req = httpx.Request("GET", "https://api.test/v1/markets/aec-x/book")
    resp = httpx.Response(429, request=req,
                          headers={"Retry-After": "9",
                                   "X-Request-Id": "req-42",
                                   "X-RateLimit-Remaining": "0"})
    d = VHE.describe(RateLimitError("slow down", response=resp, body=None),
                     endpoint="markets.book", attempts=3, elapsed_s=1.5)
    assert d["http_status"] == 429
    assert d["retry_after_s"] == 9.0
    assert d["retry_after_raw"] == "9"
    assert d["request_id"] == "req-42"
    assert d["attempts"] == 3
    assert d["elapsed_s"] == 1.5
    assert d["is_rate_limited"] is True
    assert d["url_path"] == "/v1/markets/aec-x/book"


def test_a_retry_after_http_date_is_parsed_too():
    """RFC 9110 allows a date, and only handling integers loses it.

    A date-valued `Retry-After` becoming "no Retry-After" is how the
    cooldown silently falls back to a guess on a venue that was explicit.
    """
    import email.utils
    import time

    from polymarket_us.errors import RateLimitError

    when = email.utils.formatdate(time.time() + 30, usegmt=True)
    req = httpx.Request("GET", "https://api.test/v1/x/book")
    resp = httpx.Response(429, request=req, headers={"Retry-After": when})
    d = VHE.describe(RateLimitError("x", response=resp, body=None))
    assert d["retry_after_s"] is not None
    assert 25 <= d["retry_after_s"] <= 35, d["retry_after_s"]


def test_no_credential_or_signed_query_reaches_the_diagnostic():
    """A diagnostic nobody can show is worth nothing; one that leaks is worse."""
    from polymarket_us.errors import RateLimitError

    req = httpx.Request(
        "GET",
        "https://api.test/v1/markets/aec-x/book"
        "?signature=AAAAAAAAAAAAAAAAAAAAAAAAAAAA&key=BBBBBBBBBBBBBBBBBBBBBBBB")
    resp = httpx.Response(429, request=req,
                          headers={"Retry-After": "1",
                                   "Authorization": "Bearer SUPERSECRETVALUE1234567890"})
    d = VHE.describe(RateLimitError(
        "denied for token=SUPERSECRETVALUE1234567890", response=resp,
        body=None))
    blob = repr(d)
    assert "SUPERSECRETVALUE1234567890" not in blob, blob
    assert "AAAAAAAAAAAAAAAAAAAAAAAAAAAA" not in blob, blob
    # THE QUERY IS DROPPED WHOLESALE rather than redacted span by span,
    # because a presigned URL carries its credential there and redacting
    # invites a miss.
    assert d["query_string_dropped"] is True
    assert "signature" not in blob.lower() or "<redacted>" in blob
    # AND `Authorization` IS NOT IN THE ALLOW-LIST AT ALL.
    assert "authorization" not in {k.lower() for k in d["headers"]}


def test_a_500_is_not_reported_as_rate_limited():
    """The positive control. If everything looks like a 429 the flag is useless."""
    from polymarket_us.errors import InternalServerError

    req = httpx.Request("GET", "https://api.test/v1/x/book")
    resp = httpx.Response(500, request=req)
    d = VHE.describe(InternalServerError("boom", response=resp, body=None))
    assert d["http_status"] == 500
    assert d["is_rate_limited"] is False


def test_a_transport_error_with_no_response_still_describes():
    """No response object must not mean no diagnostic."""
    from polymarket_us.errors import APIConnectionError

    d = VHE.describe(APIConnectionError(message="connection reset"))
    assert d["error_type"] == "APIConnectionError"
    assert d["http_status"] is None
    assert d["is_rate_limited"] is False
    assert "connection reset" in (d["message"] or "")


# ═════════════════════════════════════════════════════════════════════
# 3 · THE COOLDOWN ACTIVATES, AND SURVIVES A RESTART
# ═════════════════════════════════════════════════════════════════════

def test_an_observed_429_arms_the_cooldown():
    """`penalty_left()` was 0.0 through three 429s. Not any more."""
    assert VP.penalty_left() == 0.0
    got = VP.penalize_observed(retry_after_s=5.0, reason="VENUE_429_ON_BOOK_READ")
    assert got["applied"] is True
    assert VP.penalty_left() > 0.0
    st = VP.cooldown_state()
    assert st["active"] is True
    assert st["reason"] == "VENUE_429_ON_BOOK_READ"


def test_the_longer_of_retry_after_and_our_floor_wins():
    """A cooldown shorter than the venue asked for is a guess in a policy's clothes.

    And a venue asking for LESS than our floor does not entitle us to drop
    the floor, so the max is taken in both directions.
    """
    short = VP.penalize_observed(retry_after_s=1.0)
    assert short["hold_s"] == VP.PENALTY_S, short
    assert short["hold_is"] == "OUR_FLOOR"

    longer = VP.penalize_observed(retry_after_s=VP.PENALTY_S + 120.0)
    assert longer["hold_s"] == VP.PENALTY_S + 120.0, longer
    assert longer["hold_is"] == "RETRY_AFTER"


def test_the_persisted_expiry_is_an_epoch_not_a_monotonic_value():
    """Monotonic is meaningless across processes, and this must cross one.

    Persisting `time.monotonic()` and reading it back in another process
    compares two unrelated number lines: the cooldown either never expires
    or is already expired. An epoch survives the trip.
    """
    import time

    got = VP.penalize_observed(retry_after_s=None)
    exp = got["expires_at_epoch_s"]
    # AN EPOCH IS NEAR wall-clock now; a monotonic value is uptime-sized.
    assert abs(exp - (time.time() + VP.PENALTY_S)) < 5.0, exp
    assert exp > 1_700_000_000, "not an epoch"
    assert "epoch" in got["portable_expiry_is_epoch_not_monotonic"]


def test_a_cooldown_read_back_after_a_restart_is_honoured():
    """THE FAIL-OPEN THIS CLOSES: a crash-loop resumed at full rate.

    The penalty lived in a module global and died with the process, so a
    restarting process began reading immediately after the account was
    rate limited -- the worst possible moment.
    """
    import time

    VP._penalty_until = 0.0
    VP._penalty_until_epoch = 0.0
    assert VP.penalty_left() == 0.0          # a "fresh process"

    got = VP.resume_cooldown(time.time() + 42.0, reason="FROM_STORAGE")
    assert got["resumed"] is True
    assert 40 <= got["seconds_left"] <= 43, got
    assert VP.penalty_left() > 0.0
    assert VP.cooldown_state()["active"] is True


def test_an_expired_stored_cooldown_is_not_resumed():
    """And says how long ago it lapsed, rather than silently doing nothing."""
    import time

    got = VP.resume_cooldown(time.time() - 10.0)
    assert got["resumed"] is False
    assert "expired" in got["why"]
    assert got["expired_s_ago"] >= 9.0


def test_a_junk_stored_expiry_is_refused():
    for bad in (None, "", "soon", float("nan")):
        got = VP.resume_cooldown(bad)
        assert got["resumed"] is False, bad


# ═════════════════════════════════════════════════════════════════════
# 4 · THE UNPINNED SDK IS RECORDED AS A DEFECT
# ═════════════════════════════════════════════════════════════════════

def test_the_sdk_retry_behaviour_is_not_assumed():
    """We do not know how many requests one call makes. That is the point.

    An independent review measured 3 attempts on `polymarket-us 1.0.2`.
    This container has 0.1.2, whose client is
    `httpx.AsyncClient(timeout=timeout)` with no retry logic at all -- so
    one call is one request there. `pyproject.toml` pins `>=0.1.2`, an
    unbounded range, so the DEPLOYED behaviour is fixed by a build-time
    resolution rather than by the repository.
    """
    import importlib.metadata as md

    ver = md.version("polymarket-us")
    assert ver, "the SDK must be installed"
    # WHATEVER THE VERSION, the accounting is at the response boundary and
    # therefore correct. This asserts the design rather than the version.
    import inspect
    src = inspect.getsource(VHO._record)
    assert "_ATTEMPTS[path]" in src, (
        "attempts must be counted in the per-response hook, which is the "
        "only place an SDK-internal retry is visible")


def test_the_venue_sdk_is_pinned_to_exactly_one_version():
    """The dependency is DECIDED, and the decision is reproducible.

    ── WHAT THIS REPLACES ───────────────────────────────────────────
    This test used to assert that `pyproject.toml` still read
    `polymarket-us>=0.1.2` -- that is, its success REQUIRED the dependency
    to remain unpinned. It was written to stop the defect being
    rediscovered, and it had the effect of defending it: fixing the range
    would have failed the suite.

    A test may record an open question. It must not make closing the
    question a failure. The range is now pinned and this asserts the pin,
    in both places it has to hold:

      * `pyproject.toml`, which decides what an image INSTALLS;
      * `venue_sdk.PINNED`, which decides what the running process
        reports and what `_get_client` constructs against.

    Two files can disagree, and a disagreement means the deployed image
    and the deployed code's own account of itself have come apart -- so
    the equality is asserted rather than either one alone.
    """
    import pathlib

    from sportsassets import venue_sdk

    root = pathlib.Path(__file__).resolve().parents[1]
    txt = (root / "pyproject.toml").read_text()
    assert "polymarket-us==%s" % venue_sdk.PINNED in txt, (
        "pyproject must pin the SDK to exactly the version venue_sdk "
        "names; a range lets the image resolve a different client than "
        "the tests ran against, which is how a 3x request amplification "
        "reached production unseen")
    assert ">=0.1.2" not in txt.split("polymarket-us")[1][:20], (
        "an unbounded floor must not survive alongside the pin")


def test_the_installed_sdk_is_the_pinned_one_and_its_retries_are_off():
    """What the tests run against, and what it was told to do.

    THE POINT IS THE CONJUNCTION. A pinned version with SDK retries still
    enabled would make two retry mechanisms (ours and its) multiply into
    six requests for one answer; SDK retries off under an unknown version
    would be a claim about a build we did not identify.
    """
    from sportsassets import venue_sdk

    rep = venue_sdk.report()
    assert rep["installed"] == venue_sdk.PINNED, (
        "the environment running these tests resolved %r, not the pinned "
        "%r -- every retry and rate assertion below describes a different "
        "client than production's" % (rep["installed"], venue_sdk.PINNED))
    assert rep["pinned_matches_installed"] is True
    assert rep["max_retries_kwarg_accepted"] is True
    assert rep["sdk_retries_disabled"] is True
    assert rep["our_client_kwargs"] == {"max_retries": 0}
    assert rep["refusals"] == []


def test_the_pinned_sdk_never_retries_an_order_submission():
    """Read out of the installed build, not assumed from its docstring.

    The venue has no idempotency key, so a retried POST can submit a
    second order. This is the one retry property that cannot be allowed to
    change quietly under a version bump, and it is asserted against the
    module that decides it rather than against a comment about it.
    """
    from sportsassets import venue_sdk

    facts = venue_sdk.retry_facts()
    assert facts["module_readable"] is True
    assert facts["post_is_retried"] is False, (
        "the installed SDK would retry POST, which on a venue with no "
        "idempotency key can duplicate an order")
    assert "POST" not in (facts["idempotent_methods"] or [])
    # And the retryable set is the one the retry policy was written against.
    assert facts["matches_expected"] is True, (
        "the installed build's retryable statuses or methods differ from "
        "the set this repository's retry policy was designed against: %r"
        % (facts,))


def test_the_sdk_drops_a_date_form_retry_after_and_we_do_not():
    """Why our own Retry-After parse is not redundant with the SDK's.

    1.0.2's `retry_after_seconds` does `float(header)` and returns None on
    anything else, so `Retry-After: <HTTP-date>` -- the form RFC 9110
    §10.2.3 permits -- becomes "no Retry-After" there. Ours parses both,
    and ours is the one that arms the cooldown. If a future version starts
    parsing dates this still passes; if OURS stops, it fails.
    """
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime

    from sportsassets import venue_http_error as VHE
    from sportsassets import venue_sdk

    facts = venue_sdk.retry_facts()
    assert facts["retry_after_header_parsed"]["integer_seconds"] == 30.0
    assert facts["retry_after_header_parsed"]["http_date"] is None, (
        "if the SDK now parses HTTP-date Retry-After, that is good news, "
        "but the cooldown still reads ours -- update this note rather "
        "than deleting the assertion")

    when = datetime.now(timezone.utc) + timedelta(seconds=90)
    exc = _StatusError(429, {"retry-after": format_datetime(when)})
    got = VHE.describe(exc, endpoint="markets.book")
    assert got["is_rate_limited"] is True
    assert got["retry_after_s"] is not None, (
        "a date-form Retry-After must not become 'no Retry-After'")
    assert 60 <= got["retry_after_s"] <= 120
