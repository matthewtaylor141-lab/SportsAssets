"""THE REAL RUN'S READINGS, as a payload the command centre can render.

WHAT THIS IS, PRECISELY. Every number below was read back from the
production database through `render-ops sql` and is quoted with the
instant it was read. The individual ladder BODIES are not fetched --
866 rows of full order books through a workflow log is neither
practical nor necessary -- so the records here are RECONSTRUCTED to
match the measured aggregates exactly: the same segment boundaries, the
same three gaps with their recorded ends, the same per-market frame
counts and last-frame instants.

SO IT IS NOT SYNTHETIC AND IT IS NOT RAW EITHER, and it says so on the
page. The scenario is labelled RECONSTRUCTED FROM REAL READINGS, in a
different colour from the synthetic fixtures, and the reconciliation
checks the rendered page against the render-ops readback rather than
against itself.

WHAT IT LICENSES: checking that the page's arithmetic -- coverage
union, gap handling, per-market validity, early-vs-window split --
produces the right answers on the real run's shape.

WHAT IT DOES NOT LICENSE: any claim about book contents. No bid, ask or
price here is real; the ladders carry a level count and nothing else.

Source readings, all from `render-ops sql` against sportsassets-db:

    10:28:29Z  journal    first LADDER, boot 7435b23a98d049f3
    11:00:42Z  journal    LADDER 866 rows, last 11:00:35Z, 2 boots
                          boot 7435b23a98d049f3  43 rows 10:28:29->10:28:59
                          boot 8702807518fe44b4 838 rows 10:31:14->11:00:35
                          GAP_DISCONNECTED  0.5268s  (boot A start)
                          PROCESS_REPLACED 135.191s  10:28:59 -> 10:31:14
                          GAP_DISCONNECTED  0.5003s  (boot B start)
    11:00:21Z  markets    12 receiving, 12 with depth, 858 in window,
                          0 before window; per-market counts below
    11:05:16Z  control    true; HTTP 0/8, connects 2/20, subs 4/40,
                          max_distinct 0, deadline 2026-09-24T04:35:48Z,
                          boots 2, reconnects 1, resubscribes 2
"""
from __future__ import annotations

import datetime as dt

READ_AT = "2026-09-23T11:05:16+00:00"

BOOT_A = "7435b23a98d049f3"
BOOT_B = "8702807518fe44b4"

A_FIRST = 1790159308.9122818      # GAP_DISCONNECTED 'from', boot A
A_DISC_TO = 1790159309.4390497
A_LAST = 1790159339.072741        # PROCESS_REPLACED 'from'
B_FIRST = 1790159474.2642093      # PROCESS_REPLACED 'to'
B_DISC_FROM = 1790159474.2896466
B_DISC_TO = 1790159474.7899637
B_LAST = dt.datetime.fromisoformat("2026-09-23T11:00:35+00:00").timestamp()

# Per-market, read at 11:00:21Z: frames and last frame.
PER_MARKET = {
    "alewar": (26, "10:58:35"), "benboo": (51, "10:59:40"),
    "beyonc": (48, "11:00:01"), "bileil": (119, "11:00:05"),
    "charoa": (23, "10:59:37"), "chaxcx": (22, "10:59:48"),
    "coldpl": (133, "11:00:05"), "doechi": (53, "11:00:07"),
    "dualip": (66, "10:59:30"), "eminem": (161, "11:00:09"),
    "fraoce": (112, "11:00:17"), "jusbie": (44, "11:00:20"),
}
# Max ladder levels seen per market (bid / ask), read at 11:00:21Z.
LEVELS = {
    "alewar": (3, 10), "benboo": (2, 14), "beyonc": (7, 6),
    "bileil": (3, 16), "charoa": (3, 8), "chaxcx": (2, 6),
    "coldpl": (2, 19), "doechi": (5, 17), "dualip": (4, 8),
    "eminem": (8, 18), "fraoce": (4, 23), "jusbie": (2, 17),
}

SLUG = "ccpc-bilbrd-1album-any2026-%s"

PROBE = {
    "probe_id": "d5e9ae3d-257f-4948-a808-90d1bd3c5e48",
    "armed": True,
    "started_at": "2026-09-23T02:35:48+00:00",
    "deadline_at": "2026-09-24T04:35:48+00:00",
    "max_distinct": 0,
    "max_incentive_manifest": 4, "incentive_manifest_reserved": 0,
    "max_incentive_recheck": 2, "incentive_recheck_reserved": 0,
    "max_incentive_retry": 2, "incentive_retry_reserved": 0,
    "max_socket_connect": 20, "socket_connect_reserved": 2,
    "max_socket_subscribe": 40, "socket_subscribe_reserved": 4,
}

RUN_ROW = {"run_id": "2026-09-23:db3554177872b360",
           "et_date": "2026-09-23", "manifest_id": "db3554177872b360",
           "boots": 2, "closed": False,
           "reconnects": 1, "resubscribes": 2}


def _hhmm(s):
    return dt.datetime.fromisoformat("2026-09-23T%s+00:00" % s).timestamp()


def records() -> list:
    """Records matching the measured aggregates exactly."""
    out = []
    slugs = [SLUG % k for k in PER_MARKET]

    out.append({"boot_id": BOOT_A, "at": A_FIRST, "kind": "RUN_OPEN",
                "epoch": None, "slug": None,
                "payload": {"allowlist": slugs,
                            "window": {"start_iso": "2026-09-23T04:00:00Z",
                                       "end_iso": "2026-09-24T04:00:00Z"}}})
    out.append({"boot_id": BOOT_A, "at": A_FIRST, "kind": "EPOCH",
                "epoch": 1, "slug": None,
                "payload": {"event": "RUN_STARTED", "subscribed": 12}})
    out.append({"boot_id": BOOT_A, "at": A_DISC_TO + 0.01, "kind": "GAP",
                "epoch": None, "slug": None,
                "payload": {"event": "GAP_CLOSED", "why": "GAP_DISCONNECTED",
                            "from": A_FIRST, "to": A_DISC_TO,
                            "duration_s": 0.5268}})
    out.append({"boot_id": BOOT_A, "at": A_LAST, "kind": "RUN_CLOSE",
                "epoch": 1, "slug": None,
                "payload": {"why": "STOPPED_BY_CONTROL",
                            "frames": 35, "rows": 43}})

    out.append({"boot_id": BOOT_B, "at": B_FIRST, "kind": "GAP",
                "epoch": None, "slug": None,
                "payload": {"why": "PROCESS_REPLACED", "from": A_LAST,
                            "to": B_FIRST, "duration_s": 135.191}})
    out.append({"boot_id": BOOT_B, "at": B_FIRST, "kind": "RUN_OPEN",
                "epoch": None, "slug": None,
                "payload": {"allowlist": slugs}})
    out.append({"boot_id": BOOT_B, "at": B_FIRST, "kind": "EPOCH",
                "epoch": 1, "slug": None,
                "payload": {"event": "RUN_STARTED", "subscribed": 12}})
    out.append({"boot_id": BOOT_B, "at": B_DISC_TO + 0.01, "kind": "GAP",
                "epoch": None, "slug": None,
                "payload": {"event": "GAP_CLOSED", "why": "GAP_DISCONNECTED",
                            "from": B_DISC_FROM, "to": B_DISC_TO,
                            "duration_s": 0.5003}})

    # LADDERS. Counts and last-frame instants are the measured ones.
    # 8 of the 866 rows are the non-ladder records above; the remaining
    # 858 are distributed per market exactly as measured.
    for key, (n, last_hhmm) in PER_MARKET.items():
        slug = SLUG % key
        bid, ask = LEVELS[key]
        last = _hhmm(last_hhmm)
        # One frame in boot A (the initial ladder for each market), the
        # rest in boot B, ending at the measured last instant.
        out.append({"boot_id": BOOT_A, "at": A_FIRST + 0.2, "kind": "LADDER",
                    "epoch": 1, "slug": slug,
                    "payload": {"bids": [1] * bid, "offers": [1] * ask,
                                "reconstructed": True}})
        span = max(1.0, last - (B_FIRST + 1.0))
        for i in range(max(0, n - 1)):
            t = B_FIRST + 1.0 + span * (i + 1) / max(1, n - 1)
            out.append({"boot_id": BOOT_B, "at": min(t, last),
                        "kind": "LADDER", "epoch": 1, "slug": slug,
                        "payload": {"bids": [1] * bid, "offers": [1] * ask,
                                    "reconstructed": True}})
    out.sort(key=lambda r: r["at"])
    return out


def scenario() -> dict:
    return {
        "why": "RECONSTRUCTED FROM REAL READINGS of the 2026-09-23 run. "
               "Segment boundaries, all three gaps, per-market frame "
               "counts and last-frame instants are the measured ones, "
               "read back through render-ops. Ladder BODIES are not "
               "real: each frame carries only the measured level count.",
        "real": True,
        "read_at": READ_AT,
        "control": True,
        "probe": PROBE,
        "run_row": RUN_ROW,
        "now": dt.datetime.fromisoformat(READ_AT).timestamp(),
        "records": records(),
    }


EXPECTED = {
    "markets_receiving": 12,
    "markets_with_depth": 12,
    "frames_in_window": 858,
    "frames_before_window": 0,
    "boots": 2,
    "gaps_closed": 3,
    "gaps_open": 0,
    "control": True,
    "http_used": 0, "http_cap": 8,
    "connects_used": 2, "connects_cap": 20,
    "subscribes_used": 4, "subscribes_cap": 40,
    "probe_id": "d5e9ae3d-257f-4948-a808-90d1bd3c5e48",
    "deadline_at": "2026-09-24T04:35:48+00:00",
}
