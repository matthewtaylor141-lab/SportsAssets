"""Process memory, read the one way this codebase has learned to trust.

The 2026-09-05 OOM kills (three at 2 GiB in forty minutes on the API)
were pinned by a probe polling /healthz's rss_mb every 5 s and lining
the readings up against the API's own log: 1213 MB flat, then 1364,
1432, 1684, dead — and the only heavy work in the log at those seconds
was the two venue board sweeps. The sweeps themselves said nothing;
they had no idea what they cost. This helper exists so a sweep can
read RSS before and after itself and put the number in its own log
line, instead of leaving it to be reconstructed from two sources with
different clocks.

/proc/self/status VmRSS, in MB, or None where /proc is not there (the
test runner on a laptop, a container without procfs). None is an
answer — rss_label prints it as '?' and never invents a figure.
"""

from __future__ import annotations


def rss_mb() -> float | None:
    """Resident set size in MB (one decimal), or None when unreadable."""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024, 1)
    except (OSError, ValueError, IndexError):
        return None
    return None


def rss_label() -> str:
    """rss_mb() for a log line: the figure, or '?' when there is none,
    so 'rss a->b MB' keeps one grammar wherever it is written."""
    v = rss_mb()
    return "?" if v is None else f"{v:.1f}"


def cap_malloc_arenas(limit: int = 2) -> str:
    """Cap glibc's per-thread malloc arenas; a status string, never a raise.

    WHY (2026-09-05). sportsassets-workers was OOM-killed at 2 GiB
    thirteen times between 17:59:41 and 20:21:49 -- every five to ten
    minutes until the analytics replay was bounded (d18cc72, ebc24ce,
    live 19:01-19:06), every twenty to twenty-four minutes after -- and
    the process had neither of the two guards the API earned in August.
    The API measured the ratchet (the 2026-08-25 census in api/app.py:
    three quarters of RSS in no cache, RSS 1,217.7 -> 1,808.2 MB in
    thirty seconds): transient allocation freed into glibc's per-thread
    arenas, up to 8 x ncores of them, each keeping its freed pages,
    never handed back. The workers process runs every venue call
    through asyncio.to_thread (mirror_live, mirror_shadow, premap,
    edge_marks, underdog, price_path, live_executor), so the same
    spread applies to it exactly as to the API.

    M_ARENA_MAX (-8) bounds the count. It only bounds arenas that do
    not exist yet, so the caller runs it at import, before any thread
    has malloc'd. The return is a status string in the API's shape
    ("arena_max=2 rc=1"; "unavailable: <ExcName>" off glibc) so the
    log line that carries it says whether the cap took instead of
    assuming it. This is a copy of api/app.py's _cap_malloc_arenas on
    purpose: procmem imports nothing from api/, and pointing app.py at
    this one is a later cleanup, not this change.
    """
    try:
        import ctypes

        M_ARENA_MAX = -8
        rc = ctypes.CDLL("libc.so.6").mallopt(M_ARENA_MAX, int(limit))
        return f"arena_max={limit} rc={rc}"
    except Exception as exc:  # noqa: BLE001 -- non-glibc simply skips
        return f"unavailable: {type(exc).__name__}"


def malloc_trim() -> bool:
    """glibc malloc_trim(0): True when it ran, False when it could not.

    WHY (2026-09-05). Capping the arenas stops the spread; it does not
    give back what a heavy cycle already took. malloc_trim walks every
    arena and returns free pages to the OS, and is cheap when there is
    nothing to return, so the workers' memory watch runs it on a timer
    the way the API's _trim_loop does -- in a thread, because it can
    take real time and the event loop carries eighteen loops. The bool
    is the difference between "trimmed and RSS did not move" and "never
    trimmed at all", which api/track_record.py's _malloc_trim (returns
    None, swallows) cannot put in a log line. That copy stays where it
    is; folding it into this one is a later cleanup, not this change.
    """
    try:
        import ctypes

        ctypes.CDLL("libc.so.6").malloc_trim(0)
        return True
    except Exception:  # noqa: BLE001 -- non-glibc simply skips
        return False
