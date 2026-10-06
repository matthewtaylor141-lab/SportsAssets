"""A TRUNCATED CATALOGUE WINDOW IS RECURSIVELY PARTITIONED, NEVER ACCEPTED.

The owner (2026-10-06): "The catalogue walker must make truncation explicit
and recursively partition the enumeration space until completeness is
established or the API proves a genuine external limitation. A result marked
truncated=true may NEVER be treated as a complete venue universe. No active
market may disappear because it fell past an offset ceiling."

What truncates a premap pass: `venue_catalogue.PageWalk.next_offset` stops a
walk as REQUEST_BUDGET_EXHAUSTED once its `max_requests` (MAX_EVENT_PAGES,
FAST_MAX_PAGES, the calendar pass budget) is spent while the venue is still
serving full pages -- and, now, as OFFSET_CEILING_REACHED when the next
offset is past what the venue serves (a configured PREMAP_MAX_OFFSET, or the
venue refusing the offset). Such a time window is halved by start time
(`venue_catalogue.WindowPartition`) and every half walked through the same
PageWalk / writer / tally, recursing until each bucket ends naturally; a
bucket still truncated at the depth / minimum-window bound is named
PROVIDER_BUCKET_REMAINS_TRUNCATED and the refresh stays TRUNCATED.

The venue payloads are the production-shaped ones of
test_venue_catalogue_is_complete (`GET /v1/events` events with inline markets,
`marketSides` carrying `identifier` / `description` / `long`), served by its
FakeVenue (start-time window, then offset + limit over a stable order that is
NOT time order -- so every page mixes both halves of a window). The writer
under test is the real `workers/premap.refresh` on the real schema (migration
249), inside a transaction that is rolled back.
"""
from __future__ import annotations

import json
from datetime import timedelta

import pytest

from sportsassets import venue_catalogue as vc
from sportsassets.workers import premap
from tests.test_venue_catalogue_is_complete import (DSN, NOW, FakeVenue,
                                                    _filler, _iso, _tx, _wire)

pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")


# ── §1 the planner and the walk, pure ────────────────────────────────────

def _board(n, lo, hi, *, dense_at=None, dense_n=0):
    """`n` events spread over [lo, hi] (epoch seconds) plus `dense_n` at one
    instant, listed in an order that is NOT time order."""
    out = []
    for i in range(n):
        t = lo + (hi - lo) * (i + 0.5) / n
        out.append({"slug": "ev-%04d" % i, "t": t})
    for j in range(dense_n):
        out.append({"slug": "dense-%04d" % j, "t": float(dense_at)})
    return sorted(out, key=lambda e: e["slug"][::-1])


def _drive_partition(board, lo, hi, *, root_budget, budget, size=100,
                     max_offset=None, **kw):
    """The window walk, then its partition, the way premap.refresh drives
    them; returns (events returned once, partition, root walk)."""
    got = []

    def walk_window(a, b, walk):
        rows = [e for e in board if a <= e["t"] <= b]   # inclusive bounds
        while (off := walk.next_offset()) is not None:
            got.extend(walk.accept(rows[off:off + size]))
        return walk

    root = walk_window(lo, hi, vc.PageWalk(limit=size, max_requests=root_budget,
                                           max_offset=max_offset))
    part = vc.WindowPartition(pass_name=vc.PASS_WINDOW, budget=budget,
                              bucket_max_requests=root_budget, **kw)
    part.record(lo, hi, 0, root.receipt(), root=True)
    shared = root.seen_keys()
    while (b := part.next_bucket()) is not None:
        a, z, depth, mreq = b
        w = walk_window(a, z, vc.PageWalk(limit=size, max_requests=mreq,
                                          max_offset=max_offset,
                                          already_read=shared))
        part.record(a, z, depth, w.receipt())
    return got, part, root


LO, HI = 1_790_000_000.0, 1_790_000_000.0 + 108 * 3600


def test_a_truncated_window_splits_and_both_halves_complete_every_event_once():
    board = _board(500, LO, HI)
    got, part, root = _drive_partition(board, LO, HI, root_budget=3, budget=30)
    assert root.truncated and root.stopped == vc.STOP_BUDGET
    slugs = [e["slug"] for e in got]
    assert sorted(slugs) == sorted(e["slug"] for e in board)
    assert len(slugs) == len(set(slugs)) == 500          # every event once
    pr = part.receipt()
    assert pr["complete"] is True and pr["unresolved"] == []
    depths = sorted(b["depth"] for b in pr["buckets"])
    assert depths[0] == 0 and depths.count(1) == 2
    assert [b for b in pr["buckets"] if b["depth"] == 0][0]["truncated"]
    assert all(not b["truncated"] for b in pr["buckets"] if b["depth"] >= 1)
    rec = vc.apply_partition(root.receipt(), part)
    assert rec["stopped"] == vc.STOP_PARTITION_COMPLETE
    assert rec["truncated"] is False and rec["complete"] is True
    assert rec["stopped_before_partition"] == vc.STOP_BUDGET
    assert rec["requests"] == root.requests + pr["requests"]
    assert pr["requests"] <= 30


def test_an_event_on_the_split_instant_is_read_by_both_halves_and_kept_once():
    """The halves share their midpoint (the venue's bounds are inclusive, so
    nothing falls between two buckets): an event AT the midpoint is served to
    both, and the second read is counted, never returned again."""
    (_, mid), _ = vc.split_window(LO, HI)
    board = _board(400, LO, HI) + [{"slug": "on-the-split", "t": mid}]
    got, part, _ = _drive_partition(board, LO, HI, root_budget=2, budget=30)
    slugs = [e["slug"] for e in got]
    assert slugs.count("on-the-split") == 1
    assert len(slugs) == len(set(slugs)) == 401
    assert part.totals["already_read_elsewhere"] >= 1


def test_a_bucket_still_truncated_at_the_bound_is_named_never_accepted():
    """300 events at ONE instant behind an offset ceiling of 200: no split
    can separate them -- the venue's genuine limit, proven, and named."""
    dense_at = LO + 30 * 3600 + 17
    board = _board(200, LO, HI, dense_at=dense_at, dense_n=300)
    got, part, root = _drive_partition(board, LO, HI, root_budget=40,
                                       budget=400, max_offset=200)
    assert root.stopped == vc.STOP_OFFSET_CEILING and root.truncated
    pr = part.receipt()
    assert pr["complete"] is False
    assert [u["why"] for u in pr["unresolved"]] == [vc.P_REMAINS_TRUNCATED]
    u = pr["unresolved"][0]
    s, e = (vc._epoch(u["start"]), vc._epoch(u["end"]))
    assert s <= dense_at <= e and e - s <= vc.PARTITION_MIN_WINDOW_S
    # every event OUTSIDE the dense instant was still read, once
    slugs = {x["slug"] for x in got}
    assert {x["slug"] for x in board if x["t"] != dense_at} <= slugs
    rec = vc.apply_partition(root.receipt(), part)
    assert rec["truncated"] is True and rec["complete"] is False
    assert rec["stopped"] == vc.STOP_PARTITION_UNRESOLVED


def test_budget_exhaustion_names_every_unread_bucket():
    board = _board(900, LO, HI)
    got, part, root = _drive_partition(board, LO, HI, root_budget=2, budget=3)
    pr = part.receipt()
    assert pr["requests"] == 3 and pr["complete"] is False
    whys = {u["why"] for u in pr["unresolved"]}
    assert vc.P_BUDGET in whys
    for u in pr["unresolved"]:
        assert u["start"] and u["end"] and u["pass"] == vc.PASS_WINDOW
    rec = vc.apply_partition(root.receipt(), part)
    assert rec["truncated"] is True and rec["natural_end"] is False


def test_the_nearest_half_is_walked_first():
    part = vc.WindowPartition(pass_name=vc.PASS_STARTED_EARLIER, budget=10,
                              bucket_max_requests=5, nearest_high=True)
    part.record(LO, HI, 0, {"stopped": vc.STOP_BUDGET, "requests": 5},
                root=True)
    a, b, depth, _ = part.next_bucket()
    assert (a, b, depth) == (vc.split_window(LO, HI)[1] + (1,))
    part2 = vc.WindowPartition(pass_name=vc.PASS_AHEAD, budget=10,
                               bucket_max_requests=5)
    part2.record(LO, HI, 0, {"stopped": vc.STOP_BUDGET, "requests": 5},
                 root=True)
    assert part2.next_bucket()[:2] == vc.split_window(LO, HI)[0]


def test_a_configured_offset_ceiling_stops_the_walk_as_truncated():
    rows = [{"slug": "e%d" % i} for i in range(1000)]
    w = vc.PageWalk(limit=100, max_requests=50, max_offset=300)
    while (off := w.next_offset()) is not None:
        w.accept(rows[off:off + 100])
    assert w.stopped == vc.STOP_OFFSET_CEILING
    assert w.truncated and not w.complete
    assert w.receipt()["max_offset"] == 300


def test_the_tally_never_calls_a_truncated_or_unresolved_pass_complete():
    t = vc.CompletenessTally(lane="full")
    part = vc.WindowPartition(pass_name=vc.PASS_WINDOW, budget=0,
                              bucket_max_requests=5)
    part.record(LO, HI, 0, {"stopped": vc.STOP_BUDGET, "requests": 5},
                root=True)
    part.next_bucket()                      # budget 0: both halves named
    t.set_pass(vc.PASS_WINDOW, vc.apply_partition(
        {"stopped": vc.STOP_BUDGET, "requests": 5, "truncated": True}, part))
    r = t.receipt()
    assert r["outcome"] == "TRUNCATED" and r["complete"] is False
    assert r["catalogue_complete"] is False
    assert r["partition"]["complete"] is False
    assert len(r["partition"]["unresolved"]) == 2


# ── §2 the writer end to end on Postgres ─────────────────────────────────

def _window_board(n, *, back_h=11.5, fwd_h=95.5):
    """`n` production-shaped events (a table-tennis slate, one two-sided
    market each) spread over the full sweep's window."""
    lo, hi = NOW - timedelta(hours=back_h), NOW + timedelta(hours=fwd_h)
    span = (hi - lo).total_seconds()
    return [_filler(i, lo + timedelta(seconds=span * (i + 0.5) / n))
            for i in range(n)]


class WideningVenue(FakeVenue):
    """A venue loose on its start-time bounds: every requested window is
    served two hours wider on both sides, so two halves of a partition
    OVERLAP by four hours."""

    def list(self, q):
        from datetime import datetime, timezone

        q = dict(q)
        for k, d in (("startTimeMin", -2), ("startTimeMax", 2)):
            if q.get(k):
                t = datetime.strptime(q[k], "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=timezone.utc)
                q[k] = _iso(t + timedelta(hours=d))
        return super().list(q)


class CeilingVenue(FakeVenue):
    """A venue that refuses any offset past `max_offset`, in its own
    words, the way an offset-paged API answers (HTTP 422)."""

    def __init__(self, events, *, max_offset, **kw):
        super().__init__(events, **kw)
        self.max_offset = max_offset

    def list(self, q):
        if int(q.get("offset") or 0) > self.max_offset:
            self.calls.append(("events", dict(q)))
            raise RuntimeError("HTTP 422 for /v1/events: offset too large "
                               "(max %d)" % self.max_offset)
        return super().list(q)


async def _receipt_row(conn, lane):
    row = await conn.fetchrow(
        "SELECT outcome, truncated, receipt FROM venue_catalogue_receipts "
        "WHERE lane = $1 ORDER BY id DESC LIMIT 1", lane)
    rec = row["receipt"]
    return row, (json.loads(rec) if isinstance(rec, str) else rec)


@pg
@pytest.mark.parametrize("venue_cls", [FakeVenue, WideningVenue])
async def test_the_full_sweep_partitions_its_truncated_window_to_completion(
        monkeypatch, venue_cls):
    board = _window_board(400)
    venue = venue_cls(board)
    monkeypatch.setattr(premap, "PARTITION_MAX_PAGES", 40)
    conn, tx = await _tx()
    try:
        claims = _wire(monkeypatch, conn, venue)
        s = await premap.refresh(max_pages=3)
        rec = s["completeness"]
        wp = rec["passes"][vc.PASS_WINDOW]
        assert wp["stopped_before_partition"] == vc.STOP_BUDGET
        assert wp["stopped"] == vc.STOP_PARTITION_COMPLETE
        assert wp["truncated"] is False and wp["partition"]["complete"]
        assert rec["outcome"] == "COMPLETE" and rec["catalogue_complete"] is True
        assert s["truncated"] is False and s["catalogue_complete"] is True
        # every event written, and counted, exactly once
        assert await conn.fetchval(
            "SELECT count(DISTINCT event_slug) FROM us_premap") == 400
        assert rec["events"]["seen"] == rec["events"]["kept"] == 400
        assert not rec["events"]["dropped_by_reason"].get(
            vc.D_EVENT_ALREADY_READ)
        # the partition's own budget, explicit and honoured; every request
        # behind venue_pace and on the receipt
        assert 0 < wp["partition_requests"] <= 40
        assert len(claims) == rec["requests"]
        buckets = rec["partition"]["buckets"]
        assert buckets[0]["depth"] == 0 and buckets[0]["truncated"] is True
        assert {b["pass"] for b in buckets} == {vc.PASS_WINDOW}
        assert all(b["start"] and b["end"] for b in buckets)
        row, stored = await _receipt_row(conn, "full")
        assert row["outcome"] == "COMPLETE" and row["truncated"] is False
        assert stored["catalogue_complete"] is True
        assert stored["partition"]["complete"] is True
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_a_bucket_the_venue_will_not_page_past_stays_truncated_by_name(
        monkeypatch):
    """300 events at one instant behind the venue's offset ceiling: the
    partition cuts the window down to its minimum and the venue still will
    not serve them -- PROVIDER_BUCKET_REMAINS_TRUNCATED, outcome TRUNCATED,
    catalogue_complete false -- and every other listing is still written."""
    dense_at = NOW + timedelta(hours=30, seconds=17)
    dense = [_filler(500 + j, dense_at) for j in range(300)]
    spread = _window_board(150)
    venue = CeilingVenue(spread + dense, max_offset=200)
    monkeypatch.setattr(premap, "PARTITION_MAX_PAGES", 300)
    conn, tx = await _tx()
    try:
        claims = _wire(monkeypatch, conn, venue)
        s = await premap.refresh()
        rec = s["completeness"]
        wp = rec["passes"][vc.PASS_WINDOW]
        assert wp["stopped_before_partition"] == vc.STOP_OFFSET_CEILING
        assert wp["truncated"] is True
        unresolved = rec["partition"]["unresolved"]
        assert [u["why"] for u in unresolved] == [vc.P_REMAINS_TRUNCATED]
        u = unresolved[0]
        assert u["start"] <= _iso(dense_at) <= u["end"]
        assert rec["outcome"] == "TRUNCATED" and rec["complete"] is False
        assert rec["catalogue_complete"] is False
        assert s["truncated"] is True and s["catalogue_complete"] is False
        written = {r["event_slug"] for r in await conn.fetch(
            "SELECT DISTINCT event_slug FROM us_premap")}
        assert {e["slug"] for e in spread} <= written
        assert len(claims) == rec["requests"]
        row, stored = await _receipt_row(conn, "full")
        assert row["outcome"] == "TRUNCATED" and row["truncated"] is True
        assert stored["catalogue_complete"] is False
        assert stored["partition"]["unresolved"][0]["why"] == \
            vc.P_REMAINS_TRUNCATED
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_a_spent_partition_budget_names_its_unread_buckets(monkeypatch):
    venue = FakeVenue(_window_board(900))
    monkeypatch.setattr(premap, "PARTITION_MAX_PAGES", 3)
    conn, tx = await _tx()
    try:
        claims = _wire(monkeypatch, conn, venue)
        s = await premap.refresh(max_pages=2)
        rec = s["completeness"]
        wp = rec["passes"][vc.PASS_WINDOW]
        assert wp["partition"]["requests"] == wp["partition_requests"] == 3
        assert wp["partition"]["budget"] == 3
        assert wp["truncated"] is True and wp["stopped"] == vc.STOP_BUDGET
        un = rec["partition"]["unresolved"]
        assert un and {u["why"] for u in un} == {vc.P_BUDGET}
        assert all(u["start"] < u["end"] for u in un)
        assert rec["outcome"] == "TRUNCATED"
        assert rec["catalogue_complete"] is False
        # 2 window requests + 3 partition requests, every one paced
        assert rec["requests"] == len(claims) == 5
        row, stored = await _receipt_row(conn, "full")
        assert row["truncated"] is True
        assert stored["catalogue_complete"] is False
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_a_truncated_refresh_never_prunes_a_row_it_did_not_re_see(
        monkeypatch):
    """The prune deletes rows unseen for PRUNE_HOURS. A listing past the
    truncation is unseen because it was not READ: a truncated sweep keeps
    it. The same stale row IS pruned by a sweep that read its whole window."""
    stale_sql = (
        "INSERT INTO us_premap (identifier, side_norm, event_slug, "
        "listing_pass, updated_at) VALUES ($1, 'x', $1, 'WINDOW', "
        "now() - interval '2 days')")
    for budget, expect_pruned in ((3, False), (40, True)):
        venue = FakeVenue(_window_board(900))
        monkeypatch.setattr(premap, "PARTITION_MAX_PAGES", budget)
        conn, tx = await _tx()
        try:
            await conn.execute(stale_sql, "past-the-ceiling")
            _wire(monkeypatch, conn, venue)
            s = await premap.refresh(max_pages=2 if not expect_pruned else 8)
            still = await conn.fetchval(
                "SELECT count(*) FROM us_premap WHERE identifier = "
                "'past-the-ceiling'")
            if expect_pruned:
                assert s["catalogue_complete"] is True, s["partition_unresolved"]
                assert s["prune_skipped"] is None and still == 0
                assert s["pruned"] >= 1
            else:
                assert s["truncated"] is True
                assert s["catalogue_complete"] is False
                assert s["prune_skipped"] and still == 1
                assert s["pruned"] == 0
        finally:
            await tx.rollback()
            await conn.close()


@pg
async def test_calendar_rows_are_kept_while_the_calendar_is_not_proven_complete(
        monkeypatch):
    venue = FakeVenue(_window_board(50))
    conn, tx = await _tx()
    try:
        await conn.execute(
            "INSERT INTO us_premap (identifier, side_norm, event_slug, "
            "listing_pass, updated_at) VALUES ('nlchamp-future', 'x', "
            "'mlb-nlchamp', 'AHEAD', now() - interval '2 days'), "
            "('stale-window-row', 'x', 'old-game', 'WINDOW', "
            "now() - interval '2 days')")
        await conn.execute(
            "INSERT INTO venue_catalogue_receipts (lane, started_at, "
            "finished_at, outcome, pages_read, requests, events_seen, "
            "events_kept, events_dropped, markets_seen, markets_kept, "
            "markets_dropped, sides_written, truncated, version, receipt) "
            "VALUES ('calendar', now(), now(), 'TRUNCATED', 1, 1, 0, 0, 0, "
            "0, 0, 0, 0, true, $1, $2::jsonb)", vc.VERSION,
            json.dumps({"complete": False, "catalogue_complete": False}))
        _wire(monkeypatch, conn, venue)
        s = await premap.refresh()
        assert s["catalogue_complete"] is True and s["prune_skipped"] is None
        assert s["prune_protected"]
        left = {r["identifier"] for r in await conn.fetch(
            "SELECT identifier FROM us_premap WHERE identifier IN "
            "('nlchamp-future', 'stale-window-row')")}
        assert left == {"nlchamp-future"}
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_a_truncated_calendar_slice_is_partitioned_and_unread_slices_walked(
        monkeypatch):
    """AHEAD's first slice (+96 h .. +14 d) holds more than the pass budget
    reads: it is partitioned, and the slices the budget never reached are
    walked as buckets under the partition's own budget."""
    lo = NOW + timedelta(hours=100)
    ahead = [_filler(i, lo + timedelta(minutes=37 * i)) for i in range(300)]
    venue = FakeVenue(ahead)
    monkeypatch.setattr(premap, "AHEAD_MAX_PAGES", 2)
    monkeypatch.setattr(premap, "CALENDAR_PARTITION_PAGES", 40)
    conn, tx = await _tx()
    try:
        claims = _wire(monkeypatch, conn, venue)
        cal = await premap.calendar_refresh()
        rec = cal["completeness"]
        ap = rec["passes"][vc.PASS_AHEAD]
        assert ap["stopped_before_partition"] == vc.STOP_BUDGET
        assert ap["slices_not_read"], "the budget left slices unread"
        assert ap["stopped"] == vc.STOP_PARTITION_COMPLETE
        assert ap["truncated"] is False
        assert rec["catalogue_complete"] is True and rec["outcome"] == "COMPLETE"
        assert await conn.fetchval(
            "SELECT count(DISTINCT event_slug) FROM us_premap") == 300
        assert rec["events"]["seen"] == 300
        assert len(claims) == rec["requests"]
        roots = [b for b in ap["partition"]["buckets"] if b["depth"] == 0]
        assert len(roots) == len(vc.calendar_slices(
            96.0, premap.AHEAD_DAYS * 24.0, vc.AHEAD_SLICE_BOUNDS_H))
        row, stored = await _receipt_row(conn, "calendar")
        assert row["outcome"] == "COMPLETE"
        assert stored["catalogue_complete"] is True
    finally:
        await tx.rollback()
        await conn.close()
