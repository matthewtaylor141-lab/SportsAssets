"""The audit's HTTP-age and observer-installation counterexamples.

Independent audit, 28 September 2026. Two defects reproduced there and
repaired here, each with the audit's own example as the test.

NEITHER OF THESE MAKES A TRADE ADMISSIBLE, and the audit is explicit about
that. Correcting an age calculation does not establish that the order book
behind a fresh HTTP response is current; installing an observer does not
supply upstream currency evidence. Both fix a measurement that was WRONG,
which is a precondition for honest refusals, not a route to admission.
"""

import pytest

from sportsassets import bettor_venue_currency as VC
from sportsassets import venue_http_observer as OBS


# ═════════════════════════════════════════════════════════════════════
# 1 · HTTP AGE: TWO ESTIMATES, MAXIMUM, NOT SUM  (RFC 9111 §4.2.3)
# ═════════════════════════════════════════════════════════════════════

def _obs(*, date_ago_s, age_hdr, now, delay_s=0.0, with_request=True):
    """An observation shaped like the recorder's rows."""
    resp_at = now
    o = {"headers": {"Date": VC._fmt_http_date(date_ago_s, now)
                     if hasattr(VC, "_fmt_http_date") else None,
                     "Age": str(age_hdr)},
         "received_at": resp_at}
    if with_request:
        o["request_at"] = resp_at - delay_s
    return o


def _headers(date_epoch, age_hdr):
    import email.utils
    return {"Date": email.utils.formatdate(date_epoch, usegmt=True),
            "Age": str(age_hdr)}


def test_the_audits_exact_counterexample_reports_20_not_40():
    """`Date` 20 s ago with `Age: 20` is 20 s old, not 40.

    THE AUDIT'S NUMBER. The old code computed
    `now - (Date - Age) = (now - Date) + Age = 20 + 20 = 40`, and then
    contradicted a 30 s bound on a response that was inside it.

    RFC 9111 §4.2.3 treats the two as independent ESTIMATES of one
    quantity: `apparent_age = response_time - Date = 20`,
    `corrected_age_value = Age + response_delay = 20`, and the corrected
    initial age is their MAXIMUM, 20 -- not their sum.
    """
    now = 1_790_600_000.0
    obs = {"headers": _headers(now - 20.0, 20),
           "received_at": now, "request_at": now}
    got = VC.http_evidence(obs) if hasattr(VC, "http_evidence") else None
    if got is None:                      # find the real entry point
        import inspect
        fns = [f for n, f in vars(VC).items()
               if inspect.isfunction(f) and "headers_present" in
               (inspect.getsource(f) if f.__module__ == VC.__name__ else "")]
        assert fns, "no observation reader found"
        got = fns[0](obs)
    ha = got["http_age"]
    assert ha["apparent_age_s"] == pytest.approx(20.0, abs=1e-6), ha
    assert ha["corrected_age_value_s"] == pytest.approx(20.0, abs=1e-6), ha
    assert ha["corrected_initial_age_s"] == pytest.approx(20.0, abs=1e-6), ha
    assert ha["method"] == "RFC_9111_SECTION_4_2_3"
    # AND THE DERIVED GENERATION INSTANT RE-AGES TO 20, NOT 40.
    gen = got["origin_generated_at_epoch_s"]
    assert (now - gen) == pytest.approx(20.0, abs=1e-6), (now - gen)
    # THE OLD, WRONG ANSWER MUST NOT APPEAR.
    assert (now - gen) != pytest.approx(40.0, abs=1e-6)


def test_the_maximum_is_taken_when_the_two_estimates_disagree():
    """A skewed origin clock must not shrink the age.

    `Date` 5 s ago but `Age: 60` means one of the two clocks is wrong.
    Taking the minimum, or only `apparent_age`, would report 5 s and admit
    a minute-old representation. The max is the safe and the correct
    reading: each estimate is a lower bound on the true age.
    """
    now = 1_790_600_000.0
    obs = {"headers": _headers(now - 5.0, 60), "received_at": now,
           "request_at": now}
    import inspect
    fn = _reader()
    got = fn(obs)
    ha = got["http_age"]
    assert ha["apparent_age_s"] == pytest.approx(5.0, abs=1e-6), ha
    assert ha["corrected_age_value_s"] == pytest.approx(60.0, abs=1e-6), ha
    assert ha["corrected_initial_age_s"] == pytest.approx(60.0, abs=1e-6), ha


def test_the_response_delay_is_added_to_the_age_header():
    """`corrected_age_value = Age + response_delay` (§4.2.3).

    The Age a cache reports was computed when it SENT the response; the
    time in flight belongs to the age by the time we read it.
    """
    now = 1_790_600_000.0
    obs = {"headers": _headers(now - 1.0, 10), "received_at": now,
           "request_at": now - 4.0}          # 4 s in flight
    got = _reader()(obs)
    ha = got["http_age"]
    assert ha["response_delay_s"] == pytest.approx(4.0, abs=1e-6), ha
    assert ha["corrected_age_value_s"] == pytest.approx(14.0, abs=1e-6), ha
    assert ha["corrected_initial_age_s"] == pytest.approx(14.0, abs=1e-6), ha
    assert ha["is_a_lower_bound"] is False


def test_a_missing_request_instant_is_reported_as_a_lower_bound():
    """Without the request time the correction cannot be completed.

    The audit requires this said explicitly rather than presented as a
    complete measurement: an uncorrected `Age` can only UNDERSTATE the
    age, so the result is a lower bound and must be labelled one.
    """
    now = 1_790_600_000.0
    obs = {"headers": _headers(now - 2.0, 10), "received_at": now}
    got = _reader()(obs)
    ha = got["http_age"]
    assert ha["is_a_lower_bound"] is True, ha
    assert ha["response_delay_s"] is None, ha
    assert "lower bound" in ha["why_a_lower_bound"]
    assert "UNDERSTATE" in ha["why_a_lower_bound"]


def test_a_fresh_response_still_does_not_establish_book_currency():
    """The whole point, stated by the audit and asserted here.

    A 0-second age is a statement about the transport. If this ever starts
    implying admission, the freshness gate has been quietly relaxed.
    """
    now = 1_790_600_000.0
    obs = {"headers": _headers(now, 0), "received_at": now, "request_at": now}
    got = _reader()(obs)
    ha = got["http_age"]
    assert ha["corrected_initial_age_s"] == pytest.approx(0.0, abs=1e-6)
    assert "does not establish" in ha[
        "this_is_transport_age_not_book_currency"]


def _reader():
    """The observation reader, found by its own output shape."""
    import inspect
    for name, fn in vars(VC).items():
        if not inspect.isfunction(fn) or fn.__module__ != VC.__name__:
            continue
        try:
            src = inspect.getsource(fn)
        except OSError:
            continue
        if '"headers_present"' in src and '"age_header_s"' in src:
            return fn
    raise AssertionError("no HTTP observation reader found in %s" % VC.__name__)


# ═════════════════════════════════════════════════════════════════════
# 2 · OBSERVER INSTALLATION: ASK THE CLIENT, DO NOT REMEMBER AN id()
# ═════════════════════════════════════════════════════════════════════

class _Http:
    def __init__(self):
        self.event_hooks = {}


class _Client:
    def __init__(self, http):
        self._http = http


def test_install_is_idempotent_without_stacking_hooks():
    c = _Client(_Http())
    first = OBS.install(c)
    second = OBS.install(c)
    assert first["installed"] is True and first["already"] is False, first
    assert second["installed"] is True and second["already"] is True, second
    assert c._http.event_hooks["response"].count(OBS._record) == 1


def test_a_replacement_client_reusing_an_id_is_still_installed():
    """THE AUDIT'S DEFECT, as a deterministic counterexample.

    `install` kept a module-level `set[int]` of `id(http)` and returned
    early on a hit. CPython reuses a freed object's address, so a NEW
    client could be handed a recycled id and be told it was already
    installed -- while having no response hook at all. The observation
    channel would be silently absent while reporting that it existed.

    This forces the collision directly by seeding the id cache with the
    new object's own id, which is exactly the state reuse produces.
    """
    http = _Http()
    c = _Client(http)
    # Simulate: a PREVIOUS client occupied this address and was collected.
    OBS._INSTALLED.add(id(http))
    try:
        got = OBS.install(c)
        # THE HOOK MUST ACTUALLY BE THERE.
        assert got["installed"] is True, got
        assert OBS._record in http.event_hooks.get("response", []), got
        # AND THE COLLISION IS REPORTED rather than hidden.
        assert got["already"] is False, got
        assert got["id_was_seen_before"] is True, got
        assert got["identity_reuse_detected"] is True, got
    finally:
        OBS._INSTALLED.discard(id(http))


def test_a_client_that_drops_the_hook_is_reported_not_installed():
    """"We appended" is not "it is installed".

    A client that copies or validates its hook mapping on assignment can
    silently discard ours. Claiming installed on the strength of having
    tried is the same class of error as the id cache: a claim outrunning
    its evidence.
    """
    class _Dropping(_Http):
        @property
        def event_hooks(self):
            return dict(self._h) if hasattr(self, "_h") else {}

        @event_hooks.setter
        def event_hooks(self, v):
            self._h = {}                       # swallow it

    c = _Client(_Dropping())
    got = OBS.install(c)
    assert got["installed"] is False, got
    assert "did not retain" in got["why"], got


def test_no_underlying_client_is_a_named_refusal():
    got = OBS.install(_Client(None))
    assert got["installed"] is False
    assert "underlying httpx client" in got["why"]
