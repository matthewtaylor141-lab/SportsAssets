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
