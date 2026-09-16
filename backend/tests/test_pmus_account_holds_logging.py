"""account_holds logging during a venue outage (2026-09-05).

api.polymarket.us answered 503 on every authenticated positions call
from 17:39:48Z while the public gateway answered 200. The no-stack
guard failed CLOSED exactly as designed (2026-08-11 stacking incident)
— and its log lines were unreadable while it did: a never-filled cache
reported an epoch-sized age ("snapshot 1788637346s stale"), and every
failed read printed a full traceback, ~50 lines/s across callers.

These tests pin the readable lines and the fail-closed answer in the
same breath: every wording assertion sits next to the return value it
must not disturb.
"""

import logging
import re
import time

import httpx
import pytest

from sportsassets import pmus

LOGGER = "sportsassets.pmus"
SLUG = "tsc-mlb-nyy-bos-2026-09-05-8pt5"
# The wall clock the outage was logged under — ten digits, so a
# reverted age computation shows up as an unmistakable number.
T0 = 1_788_637_346.0


class VenueDown(Exception):
    """Stand-in for the SDK's APIStatusError: carries status_code."""

    status_code = 503


class _Portfolio:
    def __init__(self, fail=None, positions=None):
        self.fail = fail  # callable returning the exception to raise
        self.payload = positions or {}

    def positions(self, params):
        if self.fail is not None:
            raise self.fail()
        return {"positions": self.payload, "eof": True}


class _Client:
    def __init__(self, portfolio):
        self.portfolio = portfolio


@pytest.fixture
def clock(monkeypatch):
    """A controlled time.time. account_holds reads it through a local
    `import time as _t`, so the module attribute is what to patch."""
    now = [T0]
    monkeypatch.setattr(time, "time", lambda: now[0])
    return now


@pytest.fixture
def fresh_boot(monkeypatch):
    """Module state as it is at process start: no snapshot, no
    remembered failure. monkeypatch restores it, so the ladder tests
    that also poke _pos_cache see the state they expect."""
    monkeypatch.setitem(pmus._pos_cache, "ts", 0.0)
    monkeypatch.setitem(pmus._pos_cache, "slugs", frozenset())
    monkeypatch.setattr(pmus, "_pos_read_failing", False)


def _venue_503(monkeypatch, fail=None):
    fail = fail or (lambda: VenueDown("Service Unavailable"))
    monkeypatch.setattr(pmus, "_get_client",
                        lambda: _Client(_Portfolio(fail=fail)))


def _venue_ok(monkeypatch, positions=None):
    monkeypatch.setattr(pmus, "_get_client",
                        lambda: _Client(_Portfolio(positions=positions)))


def _records(caplog):
    return [r for r in caplog.records if r.name == LOGGER]


def _closed(caplog):
    return [r for r in _records(caplog) if "failing CLOSED" in r.getMessage()]


def _failed_reads(caplog):
    return [r for r in _records(caplog)
            if "positions read failed" in r.getMessage()]


def test_never_filled_since_boot_says_so(monkeypatch, caplog, clock,
                                         fresh_boot):
    _venue_503(monkeypatch)
    caplog.set_level(logging.WARNING, logger=LOGGER)
    assert pmus.account_holds(SLUG) is True, "fail-closed is unchanged"
    (rec,) = _closed(caplog)
    msg = rec.getMessage()
    # Reverting the boot-sentinel branch logs "snapshot 1788637346s
    # stale" here: no "since boot", and a ten-digit age.
    assert "since boot" in msg
    assert not re.search(r"\d{9,}", msg), msg
    assert msg == ("account_holds: no positions snapshot since boot "
                   f"(venue unreadable) — failing CLOSED for {SLUG}")
    assert rec.levelno == logging.WARNING


def test_real_snapshot_keeps_stale_wording_and_fails_closed(
        monkeypatch, caplog, clock, fresh_boot):
    _venue_503(monkeypatch)
    monkeypatch.setitem(pmus._pos_cache, "ts", T0 - 3600.0)
    caplog.set_level(logging.WARNING, logger=LOGGER)
    assert pmus.account_holds(SLUG) is True, \
        "past _POS_MAX_STALE_S the answer is HELD, exactly as before"
    assert [r.getMessage() for r in _closed(caplog)] == [
        f"account_holds: snapshot 3600s stale — failing CLOSED for {SLUG}"]
    assert _closed(caplog)[0].levelno == logging.WARNING

    # Inside the bound the stale snapshot still answers from its
    # contents, and no CLOSED line is written — unchanged behaviour.
    caplog.clear()
    monkeypatch.setitem(pmus._pos_cache, "ts", T0 - 60.0)
    assert pmus.account_holds(SLUG) is False
    monkeypatch.setitem(pmus._pos_cache, "slugs", frozenset({SLUG}))
    assert pmus.account_holds(SLUG) is True
    assert _closed(caplog) == []


def test_traceback_only_on_first_failure_and_rearmed_by_success(
        monkeypatch, caplog, clock, fresh_boot):
    _venue_503(monkeypatch)
    caplog.set_level(logging.WARNING, logger=LOGGER)

    pmus.account_holds(SLUG)
    clock[0] += pmus._POS_TTL  # past the TTL: the next call reads again
    pmus.account_holds(SLUG)
    first, second = _failed_reads(caplog)
    assert first.exc_info and first.exc_info[0] is VenueDown, \
        "the first failure since boot carries the stack"
    # Reverting to exc_info=True on every read fails here (the record
    # stores the literal False the call passed, not None).
    assert not second.exc_info, "a repeat failure is one line"
    assert caplog.text.count("Traceback (most recent call last)") == 1
    assert {first.levelno, second.levelno} == {logging.WARNING}

    # A successful read re-arms the traceback for the next outage.
    clock[0] += pmus._POS_TTL
    _venue_ok(monkeypatch, positions={SLUG: {"netPosition": "3"}})
    assert pmus.account_holds(SLUG) is True, "a live read answers"
    clock[0] += pmus._POS_TTL
    _venue_503(monkeypatch)
    pmus.account_holds(SLUG)
    third = _failed_reads(caplog)[2]
    # Reverting the reset on success leaves the flag stuck: no stack.
    assert third.exc_info and third.exc_info[0] is VenueDown, \
        "a success in between re-armed the stack"
    assert caplog.text.count("Traceback (most recent call last)") == 2


def test_failed_read_line_names_class_and_status(monkeypatch, caplog,
                                                 clock, fresh_boot):
    _venue_503(monkeypatch)
    caplog.set_level(logging.WARNING, logger=LOGGER)
    pmus.account_holds(SLUG)
    (rec,) = _failed_reads(caplog)
    msg = rec.getMessage()
    # The old line said only "positions read failed; using stale
    # snapshot" — neither the class nor the 503 the operator needed.
    assert "VenueDown" in msg
    assert "503" in msg
    assert "Service Unavailable" in msg
    assert "\n" not in msg
    # The exact shape, so a cosmetic rewrite (repr instead of str, the
    # class object instead of its name, a swapped order) is a failure
    # and not a surprise in the next outage's grep.
    assert msg == ("account_holds: positions read failed (VenueDown HTTP "
                   "503: Service Unavailable); using stale snapshot")
    assert rec.levelno == logging.WARNING


def test_failed_read_line_reads_the_sdk_status_error(monkeypatch, caplog,
                                                     clock, fresh_boot):
    """The real SDK class, built the way the client builds it, so the
    status_code attribute read is proven against the type production
    actually raises."""
    from polymarket_us.errors import InternalServerError

    def fail():
        resp = httpx.Response(503, request=httpx.Request(
            "GET", "https://api.polymarket.us/v1/portfolio/positions"))
        return InternalServerError("Service Unavailable", response=resp)

    _venue_503(monkeypatch, fail=fail)
    caplog.set_level(logging.WARNING, logger=LOGGER)
    assert pmus.account_holds(SLUG) is True
    (rec,) = _failed_reads(caplog)
    msg = rec.getMessage()
    assert "InternalServerError" in msg
    assert "503" in msg
    assert "Service Unavailable" in msg


def test_failed_read_line_stays_one_line_for_an_html_503(
        monkeypatch, caplog, clock, fresh_boot):
    """The production shape of a gateway 503: a non-JSON HTML page. The
    SDK's _handle_error_response makes that whole body the exception
    message, newlines included, so the line must flatten and bound it
    (review finding 2026-09-05: six lines per attempt on a stub of the
    real body, against the 'one line per attempt' promise)."""
    from polymarket_us import PolymarketUS

    body = ("<html>\n<head><title>503 Service Temporarily Unavailable"
            "</title></head>\n<body>\n<center><h1>503 Service Temporarily "
            "Unavailable</h1></center>\n<hr><center>nginx</center>\n"
            "</body>\n</html>\n")

    def fail():
        resp = httpx.Response(
            503, content=body.encode(),
            headers={"content-type": "text/html"},
            request=httpx.Request(
                "GET", "https://api.polymarket.us/v1/portfolio/positions"))
        try:
            PolymarketUS()._handle_error_response(resp)
        except Exception as exc:  # noqa: BLE001 — the SDK's own class
            return exc
        raise AssertionError("the SDK did not raise on a 503")

    _venue_503(monkeypatch, fail=fail)
    caplog.set_level(logging.WARNING, logger=LOGGER)
    assert pmus.account_holds(SLUG) is True
    (rec,) = _failed_reads(caplog)
    msg = rec.getMessage()
    assert "\n" not in msg
    assert msg.startswith(
        "account_holds: positions read failed (InternalServerError HTTP "
        "503: <html> <head><title>503 Service Temporarily Unavailable")
    assert msg.endswith("; using stale snapshot")
    assert len(msg) < 300


def test_failed_read_line_without_status_code(monkeypatch, caplog, clock,
                                              fresh_boot):
    """A transport error has no status_code: class and message only,
    never a fabricated HTTP field."""
    _venue_503(monkeypatch, fail=lambda: RuntimeError("down"))
    caplog.set_level(logging.WARNING, logger=LOGGER)
    pmus.account_holds(SLUG)
    (rec,) = _failed_reads(caplog)
    msg = rec.getMessage()
    assert "RuntimeError" in msg
    assert "down" in msg
    assert "HTTP" not in msg
    assert msg == ("account_holds: positions read failed (RuntimeError: "
                   "down); using stale snapshot")
