"""RUN ONE HISTORICAL R30 DECISION REPLAY AND RECORD IT (research only).

    python -m sportsassets.scripts.r30_replay --hours 24
    python -m sportsassets.scripts.r30_replay --start 2026-10-03T00:00:00Z \\
        --end 2026-10-04T00:00:00Z [--account paper_acct_main] \\
        [--max-decisions 200] [--strategy PINNACLE_COMPLETED_GAME_PAPER]
        [--enter-only] [--dry-run]

Reads DATABASE_URL. Every read goes through the replay's choke point
(sportsassets/replay/pit.py: only rows recorded by each replay clock); the
only write is the append-only run into r30_replay_* (migration 237), labelled
REPLAY_NOT_FORWARD_EVIDENCE. --dry-run prints the summary and writes
nothing. Nothing here places, sizes, cancels or manages an order, or changes
a rail, threshold, control or capital; no replay result can promote
production policy.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time


def _clock(v: str) -> float:
    try:
        return float(v)
    except ValueError:
        return dt.datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()


def code_sha() -> str:
    """The commit that runs the replay: the deploy's RENDER_GIT_COMMIT, else
    the checkout's HEAD; else UNAVAILABLE with the reason (never invented)."""
    sha = os.environ.get("RENDER_GIT_COMMIT", "")
    if re.fullmatch(r"[0-9a-f]{40}", sha or ""):
        return sha
    try:
        got = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                             text=True, timeout=5,
                             cwd=os.path.dirname(os.path.abspath(__file__)))
        sha = got.stdout.strip()
        if re.fullmatch(r"[0-9a-f]{40}", sha):
            return sha
    except (OSError, subprocess.SubprocessError):
        pass
    return "UNAVAILABLE:NO_COMMIT_IN_ENVIRONMENT_OR_CHECKOUT"


async def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--hours", type=float)
    ap.add_argument("--account")
    ap.add_argument("--max-decisions", type=int)
    ap.add_argument("--strategy", action="append", default=[])
    ap.add_argument("--enter-only", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    now = time.time()
    end = _clock(a.end) if a.end else now
    if a.start:
        start = _clock(a.start)
    elif a.hours:
        start = end - a.hours * 3600.0
    else:
        ap.error("--start or --hours is required")
    import asyncpg

    from ..replay import runner as RN
    from ..replay import store as ST
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        params = {}
        if a.max_decisions:
            params["max_decisions"] = a.max_decisions
        built = await RN.run(conn, start=start, end=end, params=params,
                             account_id=a.account, now=now,
                             strategies=a.strategy or None,
                             enter_only=a.enter_only)
        out = {"summary": built["summary"], "meta": built["meta"]}
        if not a.dry_run:
            out["recorded"] = await ST.record_run(
                conn, built, code_sha=code_sha(),
                triggered_by="scripts.r30_replay (%s)" % (
                    os.environ.get("USER") or "operator"))
        print(json.dumps(out, indent=1, default=str)[:200000])
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
