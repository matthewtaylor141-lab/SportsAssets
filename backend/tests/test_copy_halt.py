"""The emergency halt fails CLOSED (2026-08-25).

Fill forensics returned five rows where the venue took 1.15x-3.87x the
authorized clip, at prices near the COMPLEMENT of our limit — the
signature of landing on the opposite side. Copying is off until that
mechanism is understood.

These tests deliberately clear the suite-wide LIVE_COPY_HALT=off from
conftest, so they see the real production default.
"""

import inspect

from sportsassets import live_executor as le


def test_the_default_is_halted(monkeypatch):
    """No env var set — the halt must be ON. A missing variable can
    never resurrect live copying."""
    monkeypatch.delenv("LIVE_COPY_HALT", raising=False)
    assert le.copy_halted() is True


def test_only_an_explicit_off_lifts_it(monkeypatch):
    monkeypatch.setenv("LIVE_COPY_HALT", "off")
    assert le.copy_halted() is False
    monkeypatch.setenv("LIVE_COPY_HALT", "OFF")
    assert le.copy_halted() is False
    monkeypatch.setenv("LIVE_COPY_HALT", " off ")
    assert le.copy_halted() is False


def test_garbage_values_stay_halted(monkeypatch):
    """A typo'd or half-written override must not open the money path."""
    for v in ("", "on", "0", "false", "no", "of", "disabled", "1", "true"):
        monkeypatch.setenv("LIVE_COPY_HALT", v)
        assert le.copy_halted() is True, f"{v!r} must not lift the halt"


def test_the_halt_sits_at_the_common_gate():
    """The reclaim path calls maybe_execute directly — a halt checked
    only at the fresh-detection entry point would leak, exactly as the
    master kill switch did before 2026-08-24."""
    src = inspect.getsource(le.maybe_execute)
    assert "copy_halted()" in src


def test_the_halt_is_checked_before_any_order_is_built():
    """It must precede venue selection and sizing, not sit downstream of
    them."""
    src = inspect.getsource(le.maybe_execute)
    assert src.index("copy_halted()") < src.index("active_venue()")


def test_the_reason_is_recorded_for_a_human():
    r = le.COPY_HALT_REASON.lower()
    assert "overspend" in r and "wrong side" in r
