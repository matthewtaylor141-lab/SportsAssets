"""THE BOUNDED REPRODUCER for the three (and more) mirror-lane review pins.

WHAT IT REPRODUCES. `tests/test_pnl_l34_review_pins.py` passes when it runs
alone and fails inside a long full-suite run. It is not an ordering effect
and it is not this repository's product code: it is the ELAPSED WALL-CLOCK
between the moment the mirror-lane fakes are IMPORTED -- they capture
`NOW = time.time()` at module import, i.e. at collection -- and the moment
the tests RUN. Some path under test reads the real clock instead of the
`now_ts` the tick was handed, so once that gap is long enough, fixtures
pinned at `NOW - 10` fall outside a staleness window and the pins break.

HOW TO USE IT. One file, one knob, no 536-file suite:

    cd backend
    RN1X_TEST_DSN=... DATABASE_URL=... ADMIN_TOKEN=t \\
      PNL_REPRO_DELAY_S=300 \\
      python3 -m pytest tests/test_pnl_l34_review_pins.py -q -p no:randomly \\
              -p tools.pnl_clock_gap_plugin

MEASURED (2026-09-26, one file, nothing else changed):

    delay    0 s  ->  10 passed
    delay  120 s  ->  10 passed
    delay  240 s  ->  10 passed
    delay  270 s  ->   3 failed   (h1, m1, the fast-tick drift pin)
    delay  300 s  ->   5 failed   (those, plus m2 and the per-market read)

The failing set GROWS with the gap, and the three the full-suite gate
reports are a subset of the 300 s set. In a 460 s suite this file runs about
300 s in; in a 415 s suite, sooner -- which is exactly why the three appear
in the slow gate runs and not the fast ones, on the BASELINE as well as on
any branch.

THE SECOND KNOB SAYS WHOSE DEFECT IT IS. With `PNL_REPRO_FREEZE=1` the real
clock is held at the value it had when the fakes were imported, while the
delay stays. If the same delayed run then passes, the tests are not merely
"flaky": a code path is reading the wall clock instead of the clock it was
given, and that is a clock-discipline defect in the mirror lane.

NOTHING HERE IS LOADED UNLESS ASKED FOR. It lives under `backend/tools/`,
which the image does not copy, and it only takes effect with an explicit
`-p tools.pnl_clock_gap_plugin` and a non-zero delay.
"""

from __future__ import annotations

import os
import time

_REAL_SLEEP = time.sleep

#: The real clock as it stood when this plugin was imported -- which is the
#: same moment the suite's fakes capture their own `NOW`.
_FROZEN_AT = time.time()


def pytest_collection_finish(session):
    """Sit between COLLECTION and EXECUTION, which is where the gap lives.

    Collection is where the fakes' `NOW` is captured, so sleeping here --
    and nowhere else -- reproduces the full suite's gap with one file.
    """
    delay = 0.0
    try:
        delay = float(os.environ.get("PNL_REPRO_DELAY_S") or 0)
    except ValueError:
        delay = 0.0
    if delay > 0:
        _REAL_SLEEP(delay)
    if os.environ.get("PNL_REPRO_FREEZE") == "1":
        # HOLD THE REAL CLOCK, keep the gap. A path that uses the clock it
        # was handed cannot notice; one that reads time.time() will.
        time.time = lambda: _FROZEN_AT
