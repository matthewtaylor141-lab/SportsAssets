"""The venue's own Time & Sales, per market, per replay interval.

WHAT THIS REPLACES. The replay derived its traded volume from the
difference in `shares_traded` between two BBO snapshots, and priced ALL
of it at `last_trade_px` -- the single most recent print. That is one
number and one price standing in for everything that happened in the
interval. Measured against the venue's published tape, the total was
right (ratio 1.00 on eight of twelve markets) and the PRICE ATTRIBUTION
was not: a single interval carries up to 120 DISTINCT prices.

    aec-nfl-chi-car-2026-09-13     max 120 distinct prices in one interval
    aec-cfb-coast-del-2026-09-19   max  62
    atc-lmx-pue-tol-2026-09-04     max  55
    aec-cfb-uwg-etnst-2026-09-19   max  43

A resting quote is filled by the prints that reach ITS price, not by an
interval's average. With one price per interval the crossing test is a
coin flip on which side of our quote `last_trade_px` happened to land.

AND THE OPPORTUNITY IS RARER THAN THE SNAPSHOT SUGGESTS. Counting
intervals that contain at least one real print:

    aec-cfb-portst-ore-2026-09-18   1426 of 4114   (34.7%)
    aec-cfb-kentst-ohiost-2026-09-19  801 of 4619   (17.3%)
    aec-cfb-coast-del-2026-09-19      552 of 4659   (11.8%)
    aec-cfb-uwg-etnst-2026-09-19      240 of 4644    (5.2%)
    aec-boxing-canalv-chrmbi          144 of 5384    (2.7%)
    atc-lmx-pue-tol-2026-09-04         20 of 1569    (1.3%)
    atc-lmx-ame-tij-2026-09-05          9 of 4919    (0.2%)

SOURCE. Eight daily files, 2026-09-13..20, streamed on a GitHub runner
and published to the orphan branch `claude/tape-data`. Retrieval was
complete: declared Content-Length equalled bytes read on all eight, no
file hit the byte cap, 21,166,873 rows parsed with 0 malformed, and
121,723 prints belong to our twelve markets.

WHAT THE TAPE DOES NOT CARRY. Four columns -- Transaction Time, Symbol,
Last Price, Last Quantity. There is NO side, NO aggressor flag and NO
buyer/seller identity. So a print establishes that a trade happened at
a price; it does NOT establish which side initiated it. That is why a
print is used here only to test whether trading REACHED our price, and
never to claim we know who lifted whom.
"""
from __future__ import annotations

import bisect
import collections
import csv
import datetime as dt
import glob
import gzip
import os

TAPE_DIRS = (
    "/tmp/claude-0/tape",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "tape"),
)


def tape_dir() -> str | None:
    for d in TAPE_DIRS:
        if os.path.isdir(d) and glob.glob(os.path.join(d, "*.filtered.csv.gz")):
            return d
    return None


def load_prints(directory: str | None = None):
    """{slug: [(epoch, price, quantity), ...]}, time-ordered.

    Returns an empty mapping when the tape is not present, so a caller
    can tell "no tape" from "no prints" rather than silently replaying
    the snapshot-derived volume and calling it the tape.
    """
    directory = directory or tape_dir()
    out: dict[str, list] = collections.defaultdict(list)
    if not directory:
        return {}
    for path in sorted(glob.glob(os.path.join(directory,
                                              "*.filtered.csv.gz"))):
        with gzip.open(path, "rt", newline="") as fh:
            for row in csv.DictReader(fh):
                t = dt.datetime.fromisoformat(row["transaction_time"])
                out[row["symbol"]].append((t.timestamp(),
                                           float(row["last_price"]),
                                           float(row["last_quantity"])))
    for s in out:
        out[s].sort()
    return dict(out)


class PrintIndex:
    """Prints for one market, sliceable by (t_prev, t_now].

    Half-open on the left so a print exactly on a snapshot boundary is
    counted once, in the interval that ENDS at it -- the interval whose
    closing book we then observe.
    """

    __slots__ = ("ts", "rows")

    def __init__(self, rows):
        self.rows = rows or []
        self.ts = [t for t, _, _ in self.rows]

    def between(self, t_prev, t_now):
        if not self.rows or t_prev is None or t_now is None:
            return ()
        lo = bisect.bisect_right(self.ts, t_prev)
        hi = bisect.bisect_right(self.ts, t_now)
        return self.rows[lo:hi] if hi > lo else ()


def index_for(prints, slug):
    return PrintIndex(prints.get(slug, []))
