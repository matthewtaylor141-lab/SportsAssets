"""THE MARKET PLANE'S MEMORY ACROSS FULL CYCLES OF ITS UNIVERSE (RC6).

A plane that holds a bounded working set has the same RSS floor at the end
of every full cycle of its universe; one that leaks has a floor that rises
cycle after cycle. Hourly means cannot tell those apart early -- the RC5
plane read 251 -> 270 -> 296 MB in its first hours (warm-up) and ~2 MB/h
after (render-ops metrics, 2026-10-08/09) -- so this module measures at the
plane's own cycle boundaries:

  populate_full      the full catalogue pass (every FULL_POPULATE_EVERY_S,
                     or on a new full receipt)
  refdata_full_pull  a full refdata pull finished
  kalshi_persist     the Kalshi catalogue walk persisted

and, for each, the FLOOR (the lowest RSS read at the start of a pass since
the previous boundary of that kind) and the RSS right after the boundary.
Growth is the least-squares slope of the floors, per cycle and per hour.

Two sources, one answer:
  * the plane's heartbeat (`memory.cycles`, workers/universal_market_plane.
    StepMemory) -- the last CYCLE_RING boundaries of each kind;
  * its log lines ("universal_market_plane RSS by step (MB): {...} rss=..
    peak=.. limit=..", every MEMORY_LOG_EVERY_S) -- a boundary is where the
    step's recorded RSS changes; the floor is the lowest `rss=` between two
    boundaries. This reads production as it runs today (RC5).

PURE: no I/O, no clock. tools/plane_memory_cycles.py is the command line.
"""
from __future__ import annotations

import ast
import math
import re
from datetime import datetime, timezone

CYCLE_STEPS = ("populate_full", "refdata_full_pull", "kalshi_persist")
LOG_MARK = "universal_market_plane RSS by step (MB):"
#: a floor slope at or under this many MB per hour is FLAT (the RC5 plane's
#: measured post-warm-up drift was ~2 MB/h; 1 MB/h is 24 MB a day)
FLAT_MB_PER_HOUR = 1.0
MIN_CYCLES = 3

FLAT = "FLAT"
GROWING = "GROWING"
INSUFFICIENT = "INSUFFICIENT_CYCLES"

_TS = re.compile(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(?:[.,](\d+))?")
_NUM = r"(-?\d+(?:\.\d+)?|None)"


def _epoch(line: str):
    m = _TS.search(line)
    if not m:
        return None
    frac = float("0." + m.group(3)) if m.group(3) else 0.0
    dt = datetime.strptime("%s %s" % (m.group(1), m.group(2)),
                           "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return dt.timestamp() + frac


def _num(s):
    return None if s in (None, "None") else float(s)


def parse_log_lines(lines) -> list:
    """[{"at", "by_step", "rss_mb", "peak_mb", "limit_mb"}] from the plane's
    RSS-by-step log lines (any other line is skipped), oldest first, one
    per instant."""
    out = {}
    for line in lines:
        if LOG_MARK not in line:
            continue
        at = _epoch(line)
        tail = line.split(LOG_MARK, 1)[1]
        try:
            brace = tail[tail.index("{"):tail.index("}") + 1]
            by_step = ast.literal_eval(brace)
        except (ValueError, SyntaxError):
            continue
        if at is None or not isinstance(by_step, dict):
            continue
        rss = re.search(r"\brss=" + _NUM, tail)
        peak = re.search(r"\bpeak=" + _NUM, tail)
        lim = re.search(r"\blimit=" + _NUM, tail)
        out[round(at, 3)] = {
            "at": at, "by_step": by_step,
            "rss_mb": _num(rss.group(1)) if rss else None,
            "peak_mb": _num(peak.group(1)) if peak else None,
            "limit_mb": _num(lim.group(1)) if lim else None}
    return [out[k] for k in sorted(out)]


def cycles_from_log(samples: list, step: str = "populate_full") -> list:
    """[{"at", "rss_mb", "floor_mb", "samples"}] -- one per boundary of
    `step` the log shows: where the step's recorded RSS changes (the first
    value seen is not a boundary: its time is unknown). The floor is the
    lowest `rss=` of the samples after the previous boundary and before
    this one (the first window starts with the log)."""
    out, prev, since = [], None, []
    for s in samples:
        v = (s.get("by_step") or {}).get(step)
        if v is not None and prev is not None and v != prev and since:
            out.append({"at": s["at"], "rss_mb": v,
                        "floor_mb": min(since), "samples": len(since)})
            since = []
        if s.get("rss_mb") is not None:
            since.append(s["rss_mb"])
        if v is not None:
            prev = v
    return out


def _fit(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return 0.0, my, 0.0
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    a = my - b * mx
    res = [y - (a + b * x) for x, y in zip(xs, ys)]
    sd = math.sqrt(sum(r * r for r in res) / max(1, n - 2)) if n > 2 else 0.0
    return b, a, sd


def growth(cycles: list, *, limit_mb=None) -> dict:
    """The floors' trend across cycles: slope per cycle and per hour,
    residual spread, first / last floor, the verdict (FLAT, GROWING or
    INSUFFICIENT_CYCLES under MIN_CYCLES) and, when growing, the days to
    `limit_mb` at that slope."""
    pts = [c for c in cycles if c.get("floor_mb") is not None
           and c.get("at") is not None]
    out = {"cycles": len(pts), "flat_mb_per_hour": FLAT_MB_PER_HOUR,
           "min_cycles": MIN_CYCLES}
    if len(pts) < MIN_CYCLES:
        return dict(out, verdict=INSUFFICIENT)
    ys = [float(c["floor_mb"]) for c in pts]
    per_cycle, _a, sd = _fit(list(range(len(pts))), ys)
    t0 = float(pts[0]["at"])
    per_s, _a2, _sd2 = _fit([float(c["at"]) - t0 for c in pts], ys)
    per_h = per_s * 3600.0
    span_h = (float(pts[-1]["at"]) - t0) / 3600.0
    verdict = GROWING if per_h > FLAT_MB_PER_HOUR else FLAT
    out.update(verdict=verdict, span_h=round(span_h, 2),
               floor_first_mb=ys[0], floor_last_mb=ys[-1],
               floor_min_mb=min(ys), floor_max_mb=max(ys),
               slope_mb_per_cycle=round(per_cycle, 3),
               slope_mb_per_hour=round(per_h, 3),
               residual_sd_mb=round(sd, 2))
    if verdict == GROWING and limit_mb:
        out["days_to_limit_at_slope"] = round(
            (float(limit_mb) - ys[-1]) / (per_h * 24.0), 1)
    return out


def report(*, samples=None, heartbeat_memory=None, limit_mb=None) -> dict:
    """{step: {"source", "boundaries": [...], "growth": {...}}} from the
    log samples (parse_log_lines) and/or the heartbeat's `memory` (its
    `cycles` ring); the heartbeat's ring wins for a step it carries."""
    out = {}
    lim = limit_mb
    if lim is None and samples:
        lim = next((s["limit_mb"] for s in reversed(samples)
                    if s.get("limit_mb")), None)
    if lim is None and heartbeat_memory:
        lim = heartbeat_memory.get("limit_mb")
    ring = (heartbeat_memory or {}).get("cycles") or {}
    for step in CYCLE_STEPS:
        if ring.get(step):
            cyc, src = list(ring[step]), "HEARTBEAT_CYCLE_RING"
        elif samples:
            cyc, src = cycles_from_log(samples, step), "LOG_RSS_BY_STEP"
        else:
            continue
        out[step] = {"source": src, "boundaries": cyc,
                     "growth": growth(cyc, limit_mb=lim)}
    return {"by_cycle": out, "limit_mb": lim,
            "basis": "RSS floor (lowest pass-start RSS) between consecutive "
                     "boundaries of each full cycle; slope by least squares"}
