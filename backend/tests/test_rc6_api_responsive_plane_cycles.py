"""RC6 api-responsive: the market plane's memory, measured across FULL
cycles of its universe (catalogue populate, refdata pull, Kalshi walk).

WHY. Hourly means could not tell the RC5 plane's warm-up from a leak: 251 ->
270 -> 296 MB in its first hours, ~2 MB/h after (render-ops metrics). A
bounded working set has the same RSS floor at every full cycle; a leak's
floor rises cycle after cycle. Production readback with this method
(tools/plane_memory_cycles.py over render-ops logs 2026-10-09 00:00-03:10Z,
"RSS by step" lines): 7 full-catalogue boundaries, floor 301.2 -> 295.9 MB,
slope -1.8 MB/h, residual sd 4.4 MB -- FLAT; 6 Kalshi-walk boundaries, the
same. The plane's heartbeat now carries the boundaries itself
(`memory.cycles`, `memory.cycle_growth`).
"""
from __future__ import annotations

import json

from sportsassets.workers import universal_market_plane as W

LINE = ("2026-10-09T%s.607015655Z  2026-10-09 %s,606 INFO __main__: "
        "universal_market_plane RSS by step (MB): {'populate_full': %s, "
        "'assign': 303.5, 'kalshi_persist': %s, 'populate': 295.8} "
        "rss=%s peak=350.7 limit=4096.0")


def _line(hms, full, kalshi, rss):
    return LINE % (hms, hms, full, kalshi, rss)


def _log(floors, *, per_cycle=6, step_s=300):
    """A log with one populate_full boundary every `per_cycle` lines; the
    lowest rss of each cycle is its floor."""
    out, t = [], 0
    for c, floor in enumerate(floors):
        for i in range(per_cycle):
            hms = "%02d:%02d:%02d" % (t // 3600, (t // 60) % 60, t % 60)
            rss = floor if i == 2 else floor + 6.0 + i
            out.append(_line(hms, 300.0 + c, 310.0 + c // 2, rss))
            t += step_s
    return out


def test_the_log_lines_are_read_in_either_format():
    from sportsassets.market_plane import memory_cycles as MC
    raw = ("2026-10-09 01:02:03,456 INFO __main__: universal_market_plane "
           "RSS by step (MB): {'populate_full': 308.3, 'refdata': None} "
           "rss=301.2 peak=None limit=4096.0")
    got = MC.parse_log_lines([_line("00:00:03", 308.3, 317.8, 301.2), raw,
                              "2026-10-09T00:01:32Z INFO httpx: GET ...",
                              "garbage"])
    assert [g["rss_mb"] for g in got] == [301.2, 301.2]
    assert got[1]["by_step"] == {"populate_full": 308.3, "refdata": None}
    assert got[1]["peak_mb"] is None and got[0]["limit_mb"] == 4096.0
    assert got[0]["at"] < got[1]["at"]


def test_a_flat_floor_is_flat_and_a_rising_one_is_growing():
    from sportsassets.market_plane import memory_cycles as MC
    flat = MC.parse_log_lines(_log([300, 302, 299, 301, 300, 298]))
    cyc = MC.cycles_from_log(flat, "populate_full")
    assert len(cyc) == 5                       # the first value: no time
    # each boundary carries the floor of the cycle it closes
    assert [c["floor_mb"] for c in cyc] == [300, 302, 299, 301, 300]
    g = MC.growth(cyc, limit_mb=4096)
    assert g["verdict"] == MC.FLAT and g["cycles"] == 5
    leak = MC.parse_log_lines(_log([300 + 25 * i for i in range(6)]))
    g2 = MC.growth(MC.cycles_from_log(leak, "populate_full"),
                   limit_mb=4096)
    assert g2["verdict"] == MC.GROWING
    assert g2["slope_mb_per_cycle"] == 25.0
    assert g2["slope_mb_per_hour"] == 50.0     # 30 min cycles
    assert 0 < g2["days_to_limit_at_slope"] < 4
    assert MC.growth(cyc[:2])["verdict"] == MC.INSUFFICIENT


def test_the_heartbeat_keeps_every_full_cycle_boundary_and_its_floor():
    rss = {"v": 400.0}
    clock = {"t": 1_000.0}
    m = W.StepMemory(rss=lambda: rss["v"], clock=lambda: clock["t"])
    for c in range(W.CYCLE_RING + 6):
        for v in (410.0 + c, 395.0 + c, 420.0 + c):     # 3 passes
            rss["v"] = v
            m.begin()
            m.mark("populate")
            clock["t"] += 600
        rss["v"] = 430.0 + c
        m.mark("populate_full")
        if c % 2:
            m.cycle("refdata_full_pull")
    d = m.digest()
    full = d["cycles"]["populate_full"]
    assert len(full) == W.CYCLE_RING                     # bounded
    assert full[-1]["rss_mb"] == 430.0 + 29
    # the floor of each cycle is its lowest pass-start RSS
    assert [b["floor_mb"] for b in full][-3:] == [395.0 + c
                                                  for c in (27, 28, 29)]
    g = d["cycle_growth"]["populate_full"]
    assert g["verdict"] == "GROWING" and g["slope_mb_per_cycle"] == 1.0
    assert len(d["cycles"]["refdata_full_pull"]) == 15
    # the heartbeat's memory section stays small with every ring full
    from sportsassets.db import heartbeat_json
    assert len(heartbeat_json(d)) < W.HEARTBEAT_SECTION_MAX_CHARS


def test_the_plane_marks_a_finished_refdata_pull_as_a_cycle():
    import inspect
    src = inspect.getsource(W.run)
    a = src.index('mem.mark("refdata")')
    assert 'mem.cycle("refdata_full_pull")' in src[a:a + 300]
    assert '"finished"' in src[a:a + 300]


def test_the_readback_tool_reads_logs_and_the_heartbeat(tmp_path):
    from tools import plane_memory_cycles as T
    log = tmp_path / "plane.txt"
    log.write_text("\n".join(_log([300, 302, 299, 301, 300, 298])))
    out = tmp_path / "r.json"
    assert T.main(["--log", str(log), "--json", str(out)]) == 0
    rep = json.loads(out.read_text())
    pf = rep["by_cycle"]["populate_full"]
    assert pf["source"] == "LOG_RSS_BY_STEP"
    assert pf["growth"]["verdict"] == "FLAT"
    assert pf["boundaries"][0]["at_utc"].startswith("2026-10-09T")
    # the heartbeat's own ring wins where it has one; a growing floor exits 1
    hb = tmp_path / "hb.json"
    hb.write_text(json.dumps({"detail": {"memory": {
        "limit_mb": 4096, "cycles": {"populate_full": [
            {"at": 1000.0 + 1800 * i, "rss_mb": 500.0 + 40 * i,
             "floor_mb": 480.0 + 40 * i} for i in range(5)]}}}}))
    assert T.main(["--log", str(log), "--heartbeat", str(hb)]) == 1
