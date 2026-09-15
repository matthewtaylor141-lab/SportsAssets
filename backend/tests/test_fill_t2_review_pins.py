"""Reviewer pins for T2 (FILL lane 4): the three mutants the lane's own
tests let live, and the late-committed row the hwm rule drops."""
from __future__ import annotations

from sportsassets.workers import mirror_live as ml
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails
    CID, M, N, NOW, SLUG, _Venue, _armed, _census, _fill, _mkt, _pool, _tick,
)


def test_review_a_requeued_entry_keeps_the_clock_of_the_tick_that_named_it():
    """The table absent on tick 1 (the entry named `rest_placed` at NOW),
    present on tick 2 (NOW + 60): the row written carries the NAMING
    tick's clock (`at` = NOW), never the queuing tick's."""
    p = _pool()
    b = p.add_book(ledger=0)
    v = _Venue()
    p.no_fill_answers_table = True
    _tick(p, v)
    assert [e["at"] for e in b["last_plan"]["his_fills_seen"]] == [NOW]
    p.no_fill_answers_table = False
    p.orders[b["open_order_id"]]["state"] = "cancelled"
    b["open_order_id"] = None
    b["ledger_net"] = 300
    _tick(p, v, now=NOW + 60, http=_mkt(300.0))
    row = p.fill_answers[("rn1", str(NOW - 3000))]
    assert row["name"] == "rest_placed" and row["at"] == NOW, "the clock the entry was named at, not the flush's"
    assert row["order_id"] is not None, "the order the naming tick placed, not the later tick's none"


def test_review_the_pending_memo_keeps_the_first_row_per_key_across_failed_writes():
    """The write failing on ticks 1 and 2: the same key is re-queued on
    tick 2 (the hwm stands) and the memo keeps tick 1's row; the write
    that succeeds on tick 3 carries tick 1's `tick`."""
    p = _pool()
    p.add_book(ledger=300)
    v = _Venue(held={SLUG: 300})
    p.raise_on.append(("ml-fill-answers */", RuntimeError("db")))
    _tick(p, v)
    _tick(p, v, now=NOW + 30)
    assert list(ml._fill_pending) == [("rn1", str(NOW - 3000))]
    assert ml._fill_pending[("rn1", str(NOW - 3000))]["tick"] == 1, "the first row per key"
    p.raise_on.clear()
    _tick(p, v, now=NOW + 60)
    assert p.fill_answers[("rn1", str(NOW - 3000))]["tick"] == 1


def test_review_a_prior_entry_with_no_clock_is_not_requeued_every_tick():
    """A prior list entry with neither `det` nor `ts` (a junk prior plan)
    under a standing hwm: written once when appended, never queued again
    (the conflict clause would refuse it, but the INSERT is a write per
    tick for nothing)."""
    p = _pool()
    b = p.add_book(ledger=300)
    v = _Venue(held={SLUG: 300})
    _tick(p, v)
    assert p.fill_writes == [1]
    seen = b["last_plan"]["his_fills_seen"]
    seen.append({"id": "junk-1", "ts": None, "det": None, "at": NOW, "order": None, "name": "on_target"})
    _tick(p, v, now=NOW + 30)
    assert p.fill_writes == [1] and ("rn1", "junk-1") not in p.fill_answers
    _tick(p, v, now=NOW + 60)
    assert p.fill_writes == [1]


def test_review_a_fill_the_tick_appends_is_written_even_when_its_ingest_clock_is_under_the_hwm():
    """The row the record exists for. Tick 1 holds one fill (its clock
    NOW - 3000): written, hwm NOW - 3000. A second row of his COMMITS after
    that read with an earlier detected_at (ingestion/pipeline.py stamps
    detected_at at line 128 and starts the INSERT at line 132, the column
    list naming the stamp at 136: a slow batch commits after a faster row
    with a later stamp): tick 2 APPENDS it to the list -- a fill the
    list names -- and the record must hold it too; the hwm is a guard
    against re-writing what a prior list already carried, never against
    an entry this tick is the first to name."""
    p = _pool()
    b = p.add_book(ledger=300)
    v = _Venue(held={SLUG: 300})
    _tick(p, v)
    assert p.fill_writes == [1] and ml._fill_hwm == {b["id"]: NOW - 3000}
    p.fills.append(_fill(M, "BUY", 0.0, 0.31, NOW - 3100, detected_at=NOW - 3050))   # committed late, dated early
    _tick(p, v, now=NOW + 30)
    assert str(NOW - 3100) in {e["id"] for e in b["last_plan"]["his_fills_seen"]}, "the list names it"
    assert ("rn1", str(NOW - 3100)) in p.fill_answers, "the record must name what the list names"
    assert p.fill_writes == [1, 1]
