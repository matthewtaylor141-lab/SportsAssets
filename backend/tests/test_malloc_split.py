"""Four memory changes, three of which moved nothing. Stop proposing.

    type filter          317,681 -> 300,182 rows      (5.5%)
    streaming packer     RSS still climbed across a grind
    arena cap            mallopt rc=1, and RSS was 1,536.5 MB at 9
                         minutes uptime against 595 MB at 11 minutes
                         on the build before it

Each was a real inefficiency. None was the cause. The census says
1,184 MB of a 1,537 MB process is in none of the caches, which rules
things out but does not name anything.

mallinfo2 splits it into two mutually exclusive answers:

    uordblks   in USE by the program. If this tracks RSS, something
               genuinely holds the memory and the census is blind to
               it — keep hunting for a holder.
    fordblks   FREE but retained by the allocator. If this is the gap,
               nothing holds it, no cache fix will help, and trim
               cannot return it because the free blocks are interleaved
               with live ones. That is fragmentation, and the answer is
               a different allocator or a smaller resident set, not
               another cache change.

One number, two hypotheses, no interpretation. That is the point.
"""

import asyncio
import inspect

from sportsassets.api import app as app_mod


class TestTheSplitIsReported:
    def test_the_census_returns_malloc_info(self):
        out = asyncio.run(app_mod.api_memory_census())
        assert "malloc_info" in out

    def test_both_sides_of_the_split_are_present(self):
        mi = asyncio.run(app_mod.api_memory_census())["malloc_info"]
        if "unavailable" in mi:
            return
        assert "in_use_mb" in mi
        assert "free_retained_mb" in mi

    def test_the_numbers_are_plausible_for_this_process(self):
        mi = asyncio.run(app_mod.api_memory_census())["malloc_info"]
        if "unavailable" in mi:
            return
        assert mi["in_use_mb"] > 0
        assert mi["free_retained_mb"] >= 0
        assert mi["arena_mb"] >= mi["in_use_mb"] * 0.5

    def test_an_old_glibc_says_unavailable_rather_than_zero(self):
        """mallinfo2 is glibc 2.33+. On anything older the struct call
        raises, and reporting zeros would read as 'nothing in use' —
        the exact false-negative shape that has cost hours tonight."""
        src = inspect.getsource(app_mod.api_memory_census)
        assert '"unavailable": type(exc).__name__' in src


class TestItUsesMallinfo2NotMallinfo:
    def test_the_struct_is_size_t_not_int(self):
        """The legacy mallinfo() returns ints and silently wraps past
        2 GB — on a process being investigated for exceeding 2 GB,
        that is the one failure mode that would invent an answer."""
        src = inspect.getsource(app_mod.api_memory_census)
        assert "mallinfo2" in src
        assert "ctypes.c_size_t" in src
        assert "ctypes.c_int" not in src

    def test_the_restype_is_set(self):
        """Without restype the return is truncated to int and every
        field is garbage — it would still print numbers."""
        src = inspect.getsource(app_mod.api_memory_census)
        assert "libc.mallinfo2.restype" in src


class TestItStaysDiagnostic:
    def test_it_allocates_nothing_large(self):
        src = inspect.getsource(app_mod.api_memory_census)
        blk = src[src.index("malloc_info: dict = {}"):
                  src.index("cache = _measure")]
        for forbidden in ("get_objects", "tracemalloc", "for row", "[:]"):
            assert forbidden not in blk

    def test_it_does_not_mutate_the_allocator(self):
        """A census that trims or caps while measuring would change the
        thing it is measuring."""
        src = inspect.getsource(app_mod.api_memory_census)
        blk = src[src.index("malloc_info: dict = {}"):
                  src.index("cache = _measure")]
        assert "malloc_trim" not in blk
        assert "mallopt" not in blk
