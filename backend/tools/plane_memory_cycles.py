"""THE MARKET PLANE'S MEMORY ACROSS FULL CYCLES -- the readback (RC6).

Reads the plane's own records and answers: is the RSS floor flat from one
full cycle of its universe to the next (a bounded working set), or rising
(a leak)? See sportsassets/market_plane/memory_cycles.py for the method.

Sources (either or both):
  --log FILE ...   render-ops `action=logs service=sportsassets-market-plane`
                   output (or raw service logs) holding the plane's
                   "universal_market_plane RSS by step (MB): ..." lines,
                   e.g. arg="00:00:00/03:10:00 RSS by step" -- works on a
                   plane that predates the RC6 heartbeat ring
  --heartbeat FILE a JSON file: the plane's heartbeat detail, its `memory`
                   section, or a research-sql row with a `cycles` column

USAGE (from backend/):
    python -m tools.plane_memory_cycles --log logs1.txt logs2.txt
        [--heartbeat hb.json] [--limit-mb 4096] [--json out.json]

Prints a JSON report: per cycle kind, the boundaries (time, RSS after it,
floor since the previous one) and the floor's trend -- slope per cycle and
per hour, FLAT / GROWING / INSUFFICIENT_CYCLES, days to the limit when
growing. Exit 0 unless a kind is GROWING (1). Reads files only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone


def _load_heartbeat(path: str) -> dict:
    with open(path) as f:
        d = json.load(f)
    if isinstance(d, dict) and "memory" in d:
        d = d["memory"]
    if isinstance(d, dict) and "detail" in d:
        d = (d.get("detail") or {}).get("memory") or {}
    if isinstance(d, dict) and isinstance(d.get("cycles"), str):
        d = dict(d, cycles=json.loads(d["cycles"]))
    return d if isinstance(d, dict) else {}


def main(argv=None) -> int:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, here)
    from sportsassets.market_plane import memory_cycles as MC

    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--log", nargs="*", default=[])
    p.add_argument("--heartbeat", default=None)
    p.add_argument("--limit-mb", type=float, default=None)
    p.add_argument("--json", default=None)
    args = p.parse_args(argv)
    lines = []
    for path in args.log:
        with open(path, errors="replace") as f:
            lines.extend(f.read().splitlines())
    samples = MC.parse_log_lines(lines)
    hb = _load_heartbeat(args.heartbeat) if args.heartbeat else None
    rep = MC.report(samples=samples, heartbeat_memory=hb,
                    limit_mb=args.limit_mb)

    def iso(t):
        return datetime.fromtimestamp(float(t), timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
    for v in rep["by_cycle"].values():
        for b in v["boundaries"]:
            b["at_utc"] = iso(b["at"])
    rep["log_samples"] = len(samples)
    if samples:
        rep["log_window"] = [iso(samples[0]["at"]), iso(samples[-1]["at"])]
        rss = [s["rss_mb"] for s in samples if s.get("rss_mb") is not None]
        rep["log_rss_mb"] = {"min": min(rss), "max": max(rss),
                             "first": rss[0], "last": rss[-1]} if rss else None
    text = json.dumps(rep, indent=1)
    if args.json:
        with open(args.json, "w") as f:
            f.write(text)
    print(text)
    growing = [k for k, v in rep["by_cycle"].items()
               if v["growth"].get("verdict") == MC.GROWING]
    return 1 if growing else 0


if __name__ == "__main__":
    sys.exit(main())
