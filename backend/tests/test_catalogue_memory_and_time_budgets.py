"""BROAD CATALOGUE WORK IS BOUNDED BY MEMORY AND TIME, AND A BOUNDED PASS IS
TRUNCATED, NEVER COMPLETE (completion readiness, 2026-10-07).

Production: sportsassets-workers was OOM-killed at its 2 GiB limit while
broad catalogue work (premap ~2,086 events / ~64k rows / ~856 s; the
calendar lane ~927 s) ran beside the capital-critical loops.

  * every catalogue PageWalk can carry a memory guard: over budget it stops
    MEMORY_BUDGET_EXHAUSTED before the next page -- truncated, not complete
  * a partition whose root or bucket stopped on memory is unresolved and
    aborted: never PARTITIONED_TO_A_NATURAL_END, never split into more walks
  * premap's guard reads RSS against the container's own limit; an
    unreadable figure is not a breach
  * the window walk has a wall-time bound; the calendar lane keeps its own
"""
from __future__ import annotations

import ast
import inspect

from sportsassets import venue_catalogue as vc
from sportsassets.workers import premap


def _full(n, base=0):
    return [{"slug": "e%d" % (base + i), "id": base + i} for i in range(n)]


def test_a_walk_over_its_memory_budget_stops_truncated():
    over = {"v": False}
    w = vc.PageWalk(limit=5, max_requests=50, memory_guard=lambda: over["v"])
    assert w.next_offset() == 0
    w.accept(_full(5))
    over["v"] = True
    assert w.next_offset() is None
    assert w.stopped == vc.STOP_MEMORY_BUDGET
    assert w.truncated is True and w.complete is False
    r = w.receipt()
    assert r["truncated"] is True and r["complete"] is False
    assert r["natural_end"] is False
    assert vc.STOP_MEMORY_BUDGET in vc.TRUNCATING_STOPS
    assert vc.STOP_MEMORY_BUDGET not in vc.NATURAL_ENDS


def test_a_guard_that_raises_is_not_a_manufactured_stop():
    def boom():
        raise OSError("no /proc")
    w = vc.PageWalk(limit=5, max_requests=5, memory_guard=boom)
    assert w.next_offset() == 0 and w.stopped is None


def test_a_root_stopped_on_memory_is_never_partitioned_to_a_natural_end():
    w = vc.PageWalk(limit=5, max_requests=50, memory_guard=lambda: True)
    w.next_offset()
    part = vc.WindowPartition(pass_name=vc.PASS_WINDOW, budget=40,
                              bucket_max_requests=10)
    part.record(1000.0, 2000.0, 0, w.receipt(), root=True)
    assert part.complete is False and part.aborted == vc.STOP_MEMORY_BUDGET
    assert part.pending == [] or not part.pending      # never split
    out = vc.apply_partition(w.receipt(), part)
    assert out["stopped"] == vc.STOP_MEMORY_BUDGET
    assert out["truncated"] is True and out["complete"] is False
    assert out["natural_end"] is False


def test_a_bucket_stopped_on_memory_aborts_the_partition():
    root = vc.PageWalk(limit=5, max_requests=1)
    root.next_offset()
    root.accept(_full(5))
    root.next_offset()                              # REQUEST_BUDGET: truncated
    part = vc.WindowPartition(pass_name=vc.PASS_WINDOW, budget=40,
                              bucket_max_requests=10)
    part.record(0.0, 100000.0, 0, root.receipt(), root=True)
    assert part.pending                             # halves to walk
    b = vc.PageWalk(limit=5, max_requests=10, memory_guard=lambda: True)
    b.next_offset()
    a, z, d = part.pending.pop()
    part.record(a, z, d, b.receipt())
    assert part.aborted == vc.STOP_MEMORY_BUDGET
    out = vc.apply_partition(root.receipt(), part)
    assert out["truncated"] is True and out["complete"] is False


def test_premap_memory_guard_reads_rss_against_the_container_limit():
    assert premap.MEMORY_BUDGET_FRACTION == 0.80
    assert premap.memory_over_budget(rss=1700.0, limit=2048.0) is True
    assert premap.memory_over_budget(rss=1500.0, limit=2048.0) is False
    assert premap.memory_over_budget(rss=None, limit=2048.0) in (True, False)
    assert premap.memory_over_budget(rss=0.0, limit=0.0) is False


def test_every_premap_catalogue_walk_carries_the_guard_and_the_window_a_deadline():
    tree = ast.parse(inspect.getsource(premap))
    walks = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "PageWalk"]
    kws = [{k.arg for k in n.keywords} for n in walks]
    # the markets fallback walks offsets by hand (no next_offset loop); every
    # events walk -- window, partition bucket, calendar slice -- is guarded
    guarded = [k for k in kws if "memory_guard" in k]
    assert len(guarded) == 3, kws
    assert all("deadline" in k for k in guarded)
    assert premap.WINDOW_MAX_SECONDS == 900.0
    assert premap.CALENDAR_MAX_SECONDS == 900.0
