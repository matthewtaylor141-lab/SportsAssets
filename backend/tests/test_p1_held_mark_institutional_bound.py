"""THE HELD-MARK LANE READS THE INSTITUTIONAL BOOK AT THE MARK SLA.

Production readback after release 2514236 (ping p0 run 37506529485): the PMX
stream CONNECTED (10,877 updates, 49 symbols) yet supplied 0 held marks --
29 of 49 symbols answered SNAPSHOT_OLDER_THAN_THE_BOUND, and the same-book
probe recorded 1,902 STREAM_BOOK_NOT_CURRENT in 2 h, because both the
held-mark lane and the evidence that certifies it read the stream through
`current()`, whose snapshot bound is the DECISION path's 30 s. A held mark is
judged under the 300 s mark SLA (the retail stream's held marks already use
`book_at` at the SLA bounds).

  * current() is unchanged: 30 s, bound_use DECISION;
  * current_for_held_mark(): every other check unchanged, the snapshot bound
    at HELD_MARK_MAX_SNAPSHOT_AGE_S == bettor_paper_freshness.SLA_S (300 s),
    never above it;
  * a caller asking for more than the SLA gets the SLA, asking for less than
    the decision bound gets the decision bound;
  * the held-mark source, the same-book tap and the workers' probe read with
    it; nothing on the decision path does;
  * institutional_held_book still refuses past the SLA on its own ages.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timezone

import pytest

from sportsassets import bettor_paper_freshness as PMF
from sportsassets import institutional_stream as IS
from sportsassets import paper_market_data as PMD

SYM = "aec-mlb-nyy-bos-2026-10-04"
REC = {"symbol": SYM, "priceScale": "1000", "fractionalQtyScale": "100",
       "state": "INSTRUMENT_STATE_OPEN", "productId": "p1"}
T0 = 1_800_000_000.0


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


def _books(age_s: float):
    """A resident book whose last full update is `age_s` old on a live,
    gap-free connection that heartbeats now."""
    c = Clock()
    b = IS.ResidentBooks(clock=c)
    b.set_state(IS.S_IDLE, "test")
    b.set_instrument(SYM, REC)
    b.want([SYM])
    b.on_connected("conn-A")
    b.on_update({"symbol": SYM, "bids": [(450, 1000)], "offers": [(470, 500)],
                 "state": "INSTRUMENT_STATE_OPEN",
                 "transact_time": datetime.fromtimestamp(T0, tz=timezone.utc),
                 "book_hidden": False}, received_at=T0)
    c.t = T0 + age_s
    b.on_heartbeat()
    return b, c


@pytest.fixture(autouse=True)
def _reset():
    IS.reset()
    yield
    IS.reset()


def test_the_held_mark_bound_is_the_mark_sla_exactly():
    assert IS.HELD_MARK_MAX_SNAPSHOT_AGE_S == PMF.SLA_S == 300.0
    assert IS.MAX_SNAPSHOT_AGE_S == 30.0          # the decision bound, kept


def test_the_decision_read_still_refuses_a_120s_old_snapshot():
    b, c = _books(120.0)
    r = b.current(SYM)
    assert r["ok"] is False and r["refusal"] == IS.R_SNAPSHOT_OLD
    assert r["evidence"]["bounds"]["max_snapshot_age_s"] == 30.0
    assert r["evidence"]["bounds"]["bound_use"] == "DECISION"


def test_the_held_mark_read_accepts_a_120s_old_snapshot_on_a_live_connection():
    b, c = _books(120.0)
    r = b.current(SYM, max_snapshot_age_s=IS.HELD_MARK_MAX_SNAPSHOT_AGE_S)
    assert r["ok"] is True, r
    assert r["evidence"]["bounds"] == {"max_silence_s": IS.MAX_SILENCE_S,
                                       "max_snapshot_age_s": 300.0,
                                       "bound_use": "HELD_MARK"}
    assert r["book"]["best_bid"] == 0.45


def test_the_held_mark_read_refuses_past_the_sla():
    b, c = _books(301.0)
    r = b.current(SYM, max_snapshot_age_s=IS.HELD_MARK_MAX_SNAPSHOT_AGE_S)
    assert r["ok"] is False and r["refusal"] == IS.R_SNAPSHOT_OLD
    assert "HELD_MARK" in r["why"]


def test_a_caller_cannot_widen_past_the_sla_or_narrow_below_the_decision():
    b, c = _books(400.0)
    r = b.current(SYM, max_snapshot_age_s=10_000.0)
    assert r["refusal"] == IS.R_SNAPSHOT_OLD
    assert r["evidence"]["bounds"]["max_snapshot_age_s"] == 300.0
    b, c = _books(20.0)
    r = b.current(SYM, max_snapshot_age_s=1.0)
    assert r["ok"] is True
    assert r["evidence"]["bounds"]["max_snapshot_age_s"] == 30.0


def test_every_other_currency_check_still_refuses_on_the_held_mark_read():
    # a silent stream (no heartbeat within MAX_SILENCE_S) is refused
    b, c = _books(120.0)
    c.t += IS.MAX_SILENCE_S + 1.0
    r = b.current(SYM, max_snapshot_age_s=300.0)
    assert r["refusal"] == IS.R_SILENT
    # a dropped connection discards the book, never ages it
    b, c = _books(60.0)
    b.on_disconnected("test")
    r = b.current(SYM, max_snapshot_age_s=300.0)
    assert r["ok"] is False and r["refusal"] == IS.R_GAP_CONNECTION


def test_the_module_held_mark_read_never_raises():
    r = IS.current_for_held_mark(SYM)
    assert r["ok"] is False and r["refusal"] == IS.R_NOT_RUNNING


def test_the_held_mark_source_tap_and_probe_use_the_held_mark_read():
    src = inspect.getsource(PMD.default_institutional)
    assert "current_fn=IS.current_for_held_mark" in src
    tap = inspect.getsource(PMD)
    assert "return IS.current_for_held_mark(slug, now=now)" in tap
    from sportsassets.workers import institutional_md as IMD
    assert "istream.current_for_held_mark" in inspect.getsource(
        IMD.probe_same_book)


def test_the_decision_path_never_reads_with_the_held_mark_bound():
    import pathlib
    root = pathlib.Path(IS.__file__).parent
    users = sorted(str(p.relative_to(root)) for p in root.rglob("*.py")
                   if "current_for_held_mark" in p.read_text()
                   or "HELD_MARK_MAX_SNAPSHOT_AGE_S" in p.read_text())
    assert users == ["institutional_stream.py", "paper_market_data.py",
                     "workers/institutional_md.py"], users


def _ident():
    return {"status": "EXACT", "symbol": SYM, "institutional_side": "LONG",
            "price_transform": "IDENTITY"}


def test_institutional_held_book_marks_a_quiet_book_inside_the_sla():
    b, c = _books(120.0)
    cur = b.current(SYM, max_snapshot_age_s=300.0)
    got = PMD.institutional_held_book(
        SYM, identity=_ident(), same_book={"status": "SUPPORTED",
                                           "detail": "30/30"},
        current=cur, now=c.t, sla_s=PMF.SLA_S)
    assert got["ok"] is True, got
    assert got["read"]["institutional_receipt_age_s"] == 120.0
    assert got["read"]["observed_at"] == T0      # ITS receipt, never now


def test_institutional_held_book_still_needs_same_book_support():
    b, c = _books(120.0)
    cur = b.current(SYM, max_snapshot_age_s=300.0)
    got = PMD.institutional_held_book(
        SYM, identity=_ident(), same_book={"status": "INSUFFICIENT",
                                           "detail": "7/30"},
        current=cur, now=c.t, sla_s=PMF.SLA_S)
    assert got["ok"] is False and got["refusal"] == PMD.I_SAME_BOOK
