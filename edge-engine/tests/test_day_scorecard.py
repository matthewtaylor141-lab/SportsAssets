"""Per-day Kalshi copy scorecard — 'how is TODAY going' as a probe read.

Owner question 2026-08-14 ("we have lost almost every single trade today
on Kalshi in tennis, what is going on?") was only answerable from the
LIFETIME category grading — the probes carried no day-scoped W/L at all.
These tests pin the ledger's rolling-window scorecard and its ride on
the kalshi_copies heartbeat.
"""

import inspect
import time

from edge.ledger.service import Ledger
from edge.shadow import kalshi_copies


def _mk(tmp_path):
    return Ledger(db_path=str(tmp_path / "ledger.sqlite3"))


def test_scorecard_counts_wins_losses_realized(tmp_path):
    lg = _mk(tmp_path)
    now = time.time()
    lg.record_fill("w-b1", "kalshi", "KXW", "BUY", 10, 0.40,
                   ts=now - 7200, mode="LIVE_BETA", category="kalshi_copy")
    lg.record_fill("l-b1", "kalshi", "KXL", "BUY", 10, 0.60,
                   ts=now - 7200, mode="LIVE_BETA", category="kalshi_copy")
    lg.record_resolution("KXW", 1.0, ts=now - 1800)   # +6.00
    lg.record_resolution("KXL", 0.0, ts=now - 1800)   # -6.00
    sc = lg.day_scorecard_for_category(now - 86_400, "kalshi_copy")
    assert sc["settled"] == 2
    assert sc["won"] == 1
    assert sc["lost"] == 1
    assert abs(sc["realized"]) < 0.01


def test_scorecard_excludes_old_and_other_categories(tmp_path):
    lg = _mk(tmp_path)
    now = time.time()
    # resolution outside the 24h window
    lg.record_fill("o-b1", "kalshi", "KXOLD", "BUY", 10, 0.5,
                   ts=now - 200_000, mode="LIVE_BETA",
                   category="kalshi_copy")
    lg.record_resolution("KXOLD", 1.0, ts=now - 172_800)
    # other category inside the window
    lg.record_fill("u-b1", "kalshi", "KXUD", "BUY", 10, 0.5,
                   ts=now - 7200, mode="LIVE_BETA",
                   category="kalshi_underdog")
    lg.record_resolution("KXUD", 1.0, ts=now - 1800)
    sc = lg.day_scorecard_for_category(now - 86_400, "kalshi_copy")
    assert sc["settled"] == 0


def test_paper_fills_never_grade_the_live_scorecard(tmp_path):
    lg = _mk(tmp_path)
    now = time.time()
    lg.record_fill("p-b1", "kalshi", "KXP", "BUY", 10, 0.5,
                   ts=now - 7200, mode="PAPER", category="kalshi_copy")
    lg.record_resolution("KXP", 1.0, ts=now - 1800)
    sc = lg.day_scorecard_for_category(now - 86_400, "kalshi_copy")
    assert sc["settled"] == 0


def test_heartbeat_carries_graded_24h():
    src = inspect.getsource(kalshi_copies)
    assert "day_scorecard_for_category" in src
    assert '_dc["graded_24h"]' in src
    # telemetry must never block trading
    block = src[src.index("day_scorecard_for_category"):]
    block = block[:block.index("_nw")]
    assert "except Exception" in block


def test_scorecard_rides_through_a_halt():
    """The first tripped breaker (2026-08-14) hid the W/L line that
    explained the halt — the blocked early-return skipped the day
    counters. The scorecard must be attached BEFORE that return."""
    src = inspect.getsource(kalshi_copies)
    halt = src[src.index('stats["blocked"] = blocked'):]
    halt = halt[:halt.index("return stats")]
    assert 'stats["graded_24h"]' in halt
