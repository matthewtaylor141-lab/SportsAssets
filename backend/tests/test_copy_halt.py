"""The emergency halt: armed 00:50Z, lifted 01:10Z by owner decision.

Fill forensics returned five rows where the venue took 1.15x-3.87x the
authorized clip, at prices near the COMPLEMENT of our limit — the
signature of landing on the opposite side. Copying is off until that
mechanism is understood.

The owner reads those rows as round-trips on one game and asked to run
live while the reporting question is settled. The default is now LIVE;
what protects the money path is the fail-closed preview cost guard,
which refuses any order the venue prices above what we authorized —
or prices at all.

These tests deliberately override the suite-wide LIVE_COPY_HALT from
conftest, so they see the real production default.
"""

import inspect

from sportsassets import live_executor as le


def test_the_default_is_live(monkeypatch):
    """Owner decision 2026-08-25 01:10Z: run live while the reporting
    question is settled. What protects the money path now is the
    fail-closed preview cost guard, not this switch."""
    monkeypatch.delenv("LIVE_COPY_HALT", raising=False)
    assert le.copy_halted() is False


def test_any_plausible_yes_re_arms_the_halt(monkeypatch):
    """The default inverted, so a typo can no longer strand us HALTED —
    only LIVE. The forgiving direction must therefore be the one that
    STOPS trading: every reasonable spelling of "halt" typed into
    Render at 2am has to work."""
    for v in ("on", "ON", " on ", "1", "true", "TRUE", "yes",
              "halt", "halted", "stop"):
        monkeypatch.setenv("LIVE_COPY_HALT", v)
        assert le.copy_halted() is True, f"{v!r} must re-arm the halt"


def test_explicit_off_and_blank_stay_live(monkeypatch):
    for v in ("", "off", "OFF", " off ", "no", "0", "false"):
        monkeypatch.setenv("LIVE_COPY_HALT", v)
        assert le.copy_halted() is False, f"{v!r} must not halt"


def test_the_guard_is_what_protects_us_now(monkeypatch):
    """Lifting the halt is only defensible because submit_fok refuses a
    preview it cannot read. If that ever regresses, this pairing is the
    reminder that the halt is no longer covering it."""
    from sportsassets import pmus

    assert pmus._order_cost({}) is None


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
