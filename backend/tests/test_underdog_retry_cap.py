"""The sleeve must never stack tickets on one match.

Owner screenshot 2026-08-14 07:03 ET: SIX ~$2 tickets on the same tennis
match (Bozemoj), each bought and later cashed out. The chain that allowed
it: a FOK the venue reports 'expired' can still fill moments later, so
the ledger row said 'unfilled'; the held-veto ignores 'unfilled' by
design (genuine in-window retries); and the venue-holdings check reads a
positions API that lags real fills by minutes. Every sweep re-bought the
dog until the venue caught up. The fix is a hard cap: at most TWO
attempt rows (any status) per market token per day — one genuine retry,
never a stack. These tests pin the cap's shape in source.
"""

import inspect
import re

from sportsassets.workers import underdog


def _entry_src() -> str:
    return inspect.getsource(underdog)


def test_retry_cap_query_counts_every_status():
    """The cap counts attempts, not outcomes: no status filter — an
    'unfilled' that secretly filled is exactly the row it must count."""
    src = _entry_src()
    m = re.search(r"SELECT count\(\*\) FROM live_orders.*?placed_at", src,
                  re.S)
    assert m, "retry-cap count query missing"
    assert "status" not in m.group(0), \
        "cap must count ALL statuses — filtering by status re-opens the leak"


def test_retry_cap_is_two_and_refuses():
    src = _entry_src()
    assert "int(attempts) >= 2" in src
    assert "skipped_retry_cap" in src


def test_retry_cap_scoped_to_sleeve_and_day():
    """Cap must not throttle whale copies (separate cohort) and must
    reset daily so tomorrow's rematch is enterable."""
    src = _entry_src()
    block = src[src.index("SELECT count(*) FROM live_orders"):
                src.index("skipped_retry_cap")]
    assert "whale_username = 'underdog'" in block
    assert "interval '1 day'" in block


def test_retry_cap_runs_before_order_insert():
    """Refusal must happen before a row is written, or the cap counts
    its own refusals toward the limit."""
    src = _entry_src()
    cap_at = src.index("skipped_retry_cap")
    insert_at = src.index("INSERT INTO live_orders (whale_username, asset")
    assert cap_at < insert_at
