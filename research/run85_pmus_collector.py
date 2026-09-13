#!/usr/bin/env python3
"""RUN 85 -- PMUS public market-data collector. READ ONLY. NO CREDENTIAL.

WHY THIS EXISTS AND WHY IT HOLDS NO KEY.

Run 83.3 built a signing broker because the PMUS *websocket*
(wss://api.polymarket.us/v1/ws/markets) requires Ed25519-signed headers, and
the only key available to sign them is TRADING_CAPABLE_AT_CLIENT_LAYER. That
made every observability question wait on a credential decision.

The REST market-data surface does not have that problem. In polymarket_us
0.1.2, `Client._request` selects `gateway_base_url` whenever `authenticated`
is False (client.py:120), and every `markets.*` reader calls `get()` without
setting it (resources/markets.py:17-39, `get` defaults authenticated=False at
client.py:79-87). So the book comes from

    https://gateway.polymarket.us/v1/markets/{slug}/book

with NO X-PM-Access-Key, NO X-PM-Timestamp and NO X-PM-Signature constructed
at all. Our own production code already calls that host "the public gateway"
and recorded it answering 200 throughout the 2026-09-05 outage while
api.polymarket.us returned 503 (backend/sportsassets/pmus.py:49-52).

CAPABILITY ISOLATION IS THEREFORE PHYSICAL HERE, NOT ARCHITECTURAL. This file
imports httpx and the standard library. It does not import polymarket_us, it
constructs no signature, it reads no credential from anywhere, and it has no
code path that could produce an authenticated request even if one were handed
to it -- there is no parameter, header dict or branch by which a caller could
introduce one. test_run85_pmus_collector.py proves that by AST scan rather
than by assertion.

WHAT IT DOES NOT SOLVE. The websocket still needs auth, so sub-second
observation (the 100/250/500 ms markout offsets) is NOT reachable this way.
Polling resolution is the poll interval. That is a real limit, stated here
rather than discovered in Phase 5.

RATE. RPS_LIMIT_NOT_ESTABLISHED is locked and this file does not touch it. The
default pacing is deliberately slower than anything the repository has used
against a venue, and the collector refuses to run faster than its own ceiling.

Usage:
    python3 run85_pmus_collector.py discover  --out DIR [--limit N]
    python3 run85_pmus_collector.py sample    --out DIR --slugs FILE \
                                              --seconds N [--interval S]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

COLLECTOR_VERSION = "run85-pmus-public/1"

# The public gateway. The authenticated host is deliberately NOT a constant in
# this file: there is nothing here that could use it.
GATEWAY_BASE = "https://gateway.polymarket.us"

MARKETS_PATH = "/v1/markets"
BOOK_PATH = "/v1/markets/{slug}/book"

# Pacing. A ceiling, not a target. RPS_LIMIT_NOT_ESTABLISHED stays locked: no
# documented venue limit is reachable, so this number is OUR restraint and is
# not evidence about what the venue permits.
MAX_RPS = 2.0
MIN_SPACING_S = 1.0 / MAX_RPS

TIMEOUT_S = 10.0


def _now():
    """Wall and monotonic, read together, in ONE process.

    Both are recorded on every row. Only monotonic values are ever subtracted
    from each other, and never across processes -- the Run 83 lesson that
    CROSS_PROCESS_MONOTONIC_CLOCK_DOMAIN is UNRESOLVED applies here unchanged.
    """
    return (datetime.now(tz=timezone.utc).isoformat(),
            time.monotonic_ns())


class Pacer:
    """Spacing between venue reads. Never yields faster than MIN_SPACING_S."""

    def __init__(self, spacing_s=MIN_SPACING_S):
        self.spacing = max(float(spacing_s), MIN_SPACING_S)
        self._last = None

    def wait(self):
        if self._last is not None:
            due = self._last + self.spacing
            slack = due - time.monotonic()
            if slack > 0:
                time.sleep(slack)
        self._last = time.monotonic()


def _get(http, path, params=None):
    """One public GET. Returns a row that records a failure AS a row.

    A miss is never left absent. An absent row and a failed read look identical
    afterwards and only one of them is honest -- the rule is carried verbatim
    from the Run 83 snapshot spec.
    """
    wall, mono = _now()
    row = {"path": path, "params": params, "local_request_wall_utc": wall,
           "local_request_monotonic_ns": mono, "http_status": None,
           "error": None, "body": None}
    try:
        resp = http.get(GATEWAY_BASE + path, params=params, timeout=TIMEOUT_S)
        row["http_status"] = resp.status_code
        if resp.status_code == 200:
            try:
                row["body"] = resp.json()
            except ValueError:
                row["error"] = "NON_JSON_BODY"
        else:
            row["error"] = "http_%d" % resp.status_code
    except httpx.HTTPError as exc:
        row["error"] = type(exc).__name__
    wall2, mono2 = _now()
    row["local_response_wall_utc"] = wall2
    row["local_response_monotonic_ns"] = mono2
    return row


def discover(outdir: Path, limit: int, pacer: Pacer, http):
    """Find active markets and group them into complementary-outcome events.

    PMUS lists ONE MARKET PER OUTCOME grouped by eventSlug
    (backend/sportsassets/pmus.py:4). A pair therefore needs two slugs from one
    event, not two sides of one slug. Events that do not yield exactly two
    outcome markets are recorded and skipped rather than guessed at.
    """
    rows, markets = [], []
    cursor_params = {"active": "true", "closed": "false", "limit": 100}
    pacer.wait()
    r = _get(http, MARKETS_PATH, cursor_params)
    rows.append(r)
    body = r.get("body") or {}
    items = body.get("markets") or body.get("data") or []
    if isinstance(items, list):
        markets.extend(items)

    by_event = {}
    for m in markets:
        if not isinstance(m, dict):
            continue
        ev = m.get("eventSlug")
        slug = m.get("slug")
        if not ev or not slug:
            continue
        by_event.setdefault(ev, []).append(m)

    selected, skipped = [], []
    for ev, ms in by_event.items():
        if len(ms) == 2:
            selected.append({"eventSlug": ev,
                             "slugs": sorted(x["slug"] for x in ms),
                             "titles": [x.get("title") for x in ms],
                             "liquidity": [x.get("liquidity") for x in ms],
                             "volume": [x.get("volume") for x in ms]})
        else:
            skipped.append({"eventSlug": ev, "outcome_market_count": len(ms),
                            "reason": "NOT_A_TWO_OUTCOME_EVENT"})
    selected = selected[:limit] if limit else selected

    (outdir / "discovery_raw.jsonl").write_text(
        "".join(json.dumps(x) + "\n" for x in rows))
    (outdir / "selected_events.json").write_text(
        json.dumps({"collector_version": COLLECTOR_VERSION,
                    "selected": selected, "skipped": skipped,
                    "markets_seen": len(markets)}, indent=2))
    print("markets seen      : %d" % len(markets))
    print("two-outcome events: %d selected, %d skipped"
          % (len(selected), len(skipped)))
    return selected


def sample(outdir: Path, events, seconds: float, interval_s: float,
           pacer: Pacer, http):
    """Fixed-clock sampling. The schedule does NOT depend on whale activity.

    Both slugs of an event are read back to back so the two legs of a pair are
    as close in time as the transport allows, and the gap between them is
    recorded on every pair rather than assumed away. Run 84 established that
    the gap IS the result when it is not controlled.
    """
    out = (outdir / "book_samples.jsonl").open("w")
    t_end = time.monotonic() + seconds
    n = 0
    pairs = 0
    while time.monotonic() < t_end:
        cycle_start = time.monotonic()
        for ev in events:
            legs = []
            for slug in ev["slugs"]:
                pacer.wait()
                r = _get(http, BOOK_PATH.format(slug=slug))
                r["event_slug"] = ev["eventSlug"]
                r["market_slug"] = slug
                r["collector_version"] = COLLECTOR_VERSION
                legs.append(r)
                out.write(json.dumps(r) + "\n")
                n += 1
            if len(legs) == 2:
                gap_ns = abs(legs[1]["local_response_monotonic_ns"]
                             - legs[0]["local_response_monotonic_ns"])
                out.write(json.dumps({
                    "record": "PAIR_GAP", "event_slug": ev["eventSlug"],
                    "slugs": ev["slugs"], "leg_gap_ns": gap_ns,
                    "leg_gap_ms": gap_ns / 1e6,
                    "collector_version": COLLECTOR_VERSION}) + "\n")
                pairs += 1
            if time.monotonic() >= t_end:
                break
        slack = cycle_start + interval_s - time.monotonic()
        if slack > 0:
            time.sleep(min(slack, max(0.0, t_end - time.monotonic())))
    out.close()
    print("book reads: %d   pairs: %d" % (n, pairs))
    return n, pairs


def seal(outdir: Path):
    lines = []
    for p in sorted(outdir.iterdir()):
        if p.is_file() and p.name != "checksums.sha256":
            lines.append("%s  %s" % (
                hashlib.sha256(p.read_bytes()).hexdigest(), p.name))
    (outdir / "checksums.sha256").write_text("\n".join(lines) + "\n")
    for line in lines:
        print(line)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["discover", "sample"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=12)
    ap.add_argument("--slugs")
    ap.add_argument("--seconds", type=float, default=120.0)
    ap.add_argument("--interval", type=float, default=5.0)
    a = ap.parse_args(argv)
    outdir = Path(a.out)
    outdir.mkdir(parents=True, exist_ok=True)

    # No auth argument exists on this client, and none can be added by a
    # caller: the headers dict is built here and carries content-type only.
    with httpx.Client(headers={"Accept": "application/json"}) as http:
        pacer = Pacer()
        if a.mode == "discover":
            discover(outdir, a.limit, pacer, http)
        else:
            events = json.loads(Path(a.slugs).read_text())["selected"]
            sample(outdir, events, a.seconds, a.interval, pacer, http)
    seal(outdir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
