#!/usr/bin/env python3
"""RUN 85 TRACK B-L — one frozen BLOCK. READ ONLY. NO CREDENTIAL.

Separate experiment. Never pooled with Track A. Track A is frozen, unmodified
and running; this job shares Track A's CONCURRENCY GROUP so the two can never
execute at the same time. That is the rate gate: platform-level mutual
exclusion over actual execution, not an argument about schedules.

Why mutual exclusion and not pacing. Two independently paced streams can
collide however each paces itself -- neither stream's spacing bounds the
combined minimum gap. And a pre-flight "is Track A running?" check cannot
help: GitHub creates a delayed scheduled run's record only when it starts
(Track A run #4: cron 18:00:00Z, record created 20:30:42Z), so a late run is
invisible for an unbounded period. Only the concurrency group survives that.

--------------------------------------------------------------------------
BLOCK LENGTH AND WHAT IT COSTS
--------------------------------------------------------------------------
A block is capped at 20 minutes so that a Track A segment falling due mid-block
waits minutes, not hours. Four 300 s cycles fit.

The long horizons are free because they are later cycles' t0 reads:

    5m  = cycle c+1      10m = cycle c+2      15m = cycle c+3

and therefore 30m and 60m are NOT reachable inside a 20-minute block. They are
reported NOT_OBSERVED_WITHIN_BLOCK_LENGTH rather than silently dropped. A
60-minute horizon needs a 60-minute block, which is a separate decision about
how long Track A may be made to wait.

--------------------------------------------------------------------------
THE SCHEDULE
--------------------------------------------------------------------------
Six market lanes, staggered so no two reads ever want the same instant. Burst
offsets {0, 5, 10, 30, 60} s differ by {5,10,20,25,30,50,55,60}, so a stagger
is admissible only if it avoids that set; the first six admissible staggers on
the 2.5 s grid are used. Minimum spacing inside the block is 2.5 s.

--------------------------------------------------------------------------
SELECTION -- OBSERVABLE AT SELECTION TIME ONLY
--------------------------------------------------------------------------
Candidates are scored from the DISCOVERY payload alone, which already carries
bestBidQuote/bestAskQuote per market, so scoring costs no extra requests.
Nothing about later profitability can enter, because nothing later exists yet.

Diversity is enforced, not optimised: one market per event, round robin over
sports, then over price band and spread bucket. spread/mid is RECORDED but is
never the objective -- B-L exists to learn whether tighter books trade
differently, not to assume it.

The block is frozen at selection: market list, identities, selection rule
version and timestamp are written before the first capture read.

--------------------------------------------------------------------------
ORDER MODEL
--------------------------------------------------------------------------
At each t0: LONG rests at bestBid(t0), SHORT rests at 1 - bestAsk(t0). Those
prices are frozen for the life of that hypothetical order. No repricing, no
chasing. A touch is never a fill.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import json
import platform
import sys
import time
from decimal import Decimal as D
from pathlib import Path

import httpx

_c = importlib.util.spec_from_file_location(
    "run85c", Path(__file__).with_name("run85_pmus_collector.py"))
C = importlib.util.module_from_spec(_c)
_c.loader.exec_module(C)

_b = importlib.util.spec_from_file_location(
    "run85b", Path(__file__).with_name("run85_phase2b.py"))
B = importlib.util.module_from_spec(_b)
_b.loader.exec_module(B)

PHASE = "run85/trackbl/block/1"
SELECTION_RULE_VERSION = "BL-SELECT-1"

SPACING_S = 2.5
CYCLE_S = 300.0
BURST_OFFSETS = (0.0, 5.0, 10.0, 30.0, 60.0)
LANE_STAGGERS = (0.0, 2.5, 15.0, 17.5, 80.0, 82.5)
N_LANES = len(LANE_STAGGERS)
MAX_CYCLES = 4                                   # 20 minutes
DERIVABLE_LONG_HORIZONS = {"5m": 1, "10m": 2, "15m": 3}
UNREACHABLE_HORIZONS = ("30m", "60m")

MAX_DISCOVERY_PAGES = 12
MAX_VENUE_REQUESTS = 200
EVENTS_PATH = "/v1/events"
BOOK_PATH = "/v1/markets/%s/book"

GAME_TYPES = ("SPORTS_MARKET_TYPE_MONEYLINE", "SPORTS_MARKET_TYPE_SPREAD",
              "SPORTS_MARKET_TYPE_TOTAL", "SPORTS_MARKET_TYPE_TOTALS",
              "SPORTS_MARKET_TYPE_DRAWABLE_OUTCOME")


def say(s=""):
    print(s)
    sys.stdout.flush()


class Budget:
    def __init__(self, cap):
        self.cap, self.spent = cap, 0

    def take(self, n=1):
        if self.spent + n > self.cap:
            raise RuntimeError("BOUND_REACHED spent=%d cap=%d" % (self.spent, self.cap))
        self.spent += n


def amount(v):
    if v is None:
        return None
    raw = v.get("value") if isinstance(v, dict) else v
    try:
        return D(str(raw))
    except Exception:                                      # noqa: BLE001
        return None


def price_band(mid):
    if mid is None:
        return None
    return "TAIL" if mid < D("0.10") or mid > D("0.90") else (
        "NEAR_MID" if D("0.35") <= mid <= D("0.65") else "MODERATE")


def spread_bucket(spread, tick):
    if spread is None or not tick:
        return None
    t = D(str(tick))
    if t <= 0:
        return None
    n = (spread / t).to_integral_value()
    return "S_1T" if n <= 1 else ("S_2_3T" if n <= 3 else "S_4T_PLUS")


def candidates(events, now_epoch):
    """Score from the discovery payload only. No extra requests, no hindsight."""
    out = []
    for ev in events or []:
        eid = str(ev.get("id"))
        tags = ev.get("tags") or []
        sport = (ev.get("primaryTag") or (tags[0] if tags else None))
        for m in (ev.get("markets") or []):
            if m.get("closed") or m.get("archived") or not m.get("active"):
                continue
            if m.get("sportsMarketTypeV2") not in GAME_TYPES:
                continue
            b = amount(m.get("bestBidQuote"))
            a = amount(m.get("bestAskQuote"))
            if b is None or a is None or b <= 0 or a <= 0 or a <= b:
                continue
            tick = m.get("orderPriceMinTickSize")
            mid = (a + b) / 2
            sp = a - b
            out.append({
                "native_event_id": eid, "sport": sport,
                "league": tags, "market_slug": m.get("slug"),
                "sports_market_type_v2": m.get("sportsMarketTypeV2"),
                "tick_size": str(tick) if tick is not None else None,
                "bid": str(b), "ask": str(a), "mid": str(mid), "spread": str(sp),
                "spread_over_mid": str(sp / mid) if mid else None,
                "spread_bucket": spread_bucket(sp, tick),
                "price_band": price_band(mid),
                "game_start_time": m.get("gameStartTime"),
                "state": m.get("status"),
                "selected_from": "discovery_payload",
            })
    return out


def select_block(cands, cap=N_LANES):
    """Round robin over sports, then strata. One market per event. Deterministic."""
    by_event = {}
    for c in cands:
        by_event.setdefault(c["native_event_id"], []).append(c)
    # one market per event: the tightest spread/mid available for that event,
    # chosen on selection-time evidence only
    picks = {}
    for eid, rows in by_event.items():
        rows.sort(key=lambda r: (D(r["spread_over_mid"] or "999"), r["market_slug"]))
        picks[eid] = rows[0]
    by_sport = {}
    for eid, r in picks.items():
        if not r["sport"]:
            continue
        by_sport.setdefault(r["sport"], []).append(r)
    for rows in by_sport.values():
        rows.sort(key=lambda r: (D(r["spread_over_mid"] or "999"), r["market_slug"]))
    order = sorted(by_sport, key=lambda s: (-len(by_sport[s]), s))
    out, i = [], 0
    while len(out) < cap and any(by_sport.values()):
        s = order[i % len(order)]
        if by_sport[s]:
            out.append(by_sport[s].pop(0))
        i += 1
        if i > 10000:
            break
    return out


def discover(http, pacer, budget, log, res):
    events, pages = [], 0
    offset = 0
    while pages < MAX_DISCOVERY_PAGES:
        budget.take()
        r, rows = B.get_paced(http, EVENTS_PATH, pacer, {"limit": 100, "offset": offset})
        for x in rows:
            log.append({k: v for k, v in x.items() if k != "body"})
        pages += 1
        body = r.get("body") or {}
        evs = body.get("events") if isinstance(body, dict) else None
        if not evs:
            break
        events.extend(evs)
        offset += len(evs)
        say("  page %d offset=%d events=%d cumulative=%d" % (pages, offset, len(evs), len(events)))
    res["discovery_pages"] = pages
    res["discovery_events"] = len(events)
    return events


def run_block(http, pacer, budget, block, log_fh, res):
    """Four 300 s cycles. Long horizons fall out of later cycles' t0 reads."""
    t0 = time.monotonic()
    slots = []
    for cyc in range(MAX_CYCLES):
        for lane, m in enumerate(block):
            base = cyc * CYCLE_S + LANE_STAGGERS[lane]
            for off in BURST_OFFSETS:
                slots.append((base + off, lane, m["market_slug"], cyc, off))
    slots.sort()
    gaps = [slots[i + 1][0] - slots[i][0] for i in range(len(slots) - 1)]
    res["planned_min_gap_s"] = min(gaps) if gaps else None
    res["planned_reads"] = len(slots)
    if res["planned_min_gap_s"] is not None and res["planned_min_gap_s"] < SPACING_S - 1e-9:
        raise RuntimeError("PLAN_VIOLATES_SPACING min=%.4f" % res["planned_min_gap_s"])

    last = None
    n429 = 0
    for target, lane, slug, cyc, off in slots:
        budget.take()
        when = t0 + target
        now = time.monotonic()
        floor = (last + SPACING_S) if last is not None else now
        fire = max(when, floor)
        if fire > now:
            time.sleep(fire - now)
        start = time.monotonic()
        last = start
        row = C._get(http, BOOK_PATH % slug, None)
        row["_bl"] = {"lane": lane, "cycle": cyc, "burst_offset_s": off,
                      "planned_s": target, "actual_s": start - t0,
                      "timing_error_s": (start - t0) - target}
        log_fh.write(json.dumps(row, default=str) + "\n")
        if row.get("http_status") == 429:
            n429 += 1
            pacer.on_429((row.get("response_headers") or {}).get("retry-after"))
            last = time.monotonic()
        elif row.get("http_status") == 200:
            pacer.on_success()
    res["http_429"] = n429
    res["cycles"] = MAX_CYCLES
    res["block_elapsed_s"] = time.monotonic() - t0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--block", required=True)
    a = ap.parse_args(argv)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    res, log = {}, []
    budget = Budget(MAX_VENUE_REQUESTS)
    pacer = B.AdaptivePacer(base=SPACING_S)

    meta = {
        "phase": PHASE, "block": a.block,
        "selection_rule_version": SELECTION_RULE_VERSION,
        "python": sys.version, "platform": platform.platform(),
        "httpx": httpx.__version__, "gateway": C.GATEWAY_BASE,
        "spacing_s": SPACING_S, "cycle_s": CYCLE_S,
        "burst_offsets_s": list(BURST_OFFSETS),
        "lane_staggers_s": list(LANE_STAGGERS),
        "max_cycles": MAX_CYCLES,
        "derivable_long_horizons": DERIVABLE_LONG_HORIZONS,
        "unreachable_horizons": list(UNREACHABLE_HORIZONS),
        "unreachable_reason": "NOT_OBSERVED_WITHIN_BLOCK_LENGTH",
        "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "selection_timestamp_utc": None,
        "planned_start_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    with httpx.Client(timeout=30.0) as http:
        say("=== DISCOVERY ===")
        events = discover(http, pacer, budget, log, res)
        cands = candidates(events, time.time())
        res["candidates"] = len(cands)
        say("candidates scored from discovery payload: %d" % len(cands))
        block = select_block(cands)
        meta["selection_timestamp_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        res["block_size"] = len(block)
        say()
        say("=== FROZEN BLOCK %s (%d markets) ===" % (a.block, len(block)))
        for m in block:
            say("  %-46s %-8s mid=%-8s sprd=%-8s s/m=%-7s %s"
                % (str(m["market_slug"])[:46], m["sport"], m["mid"], m["spread"],
                   (m["spread_over_mid"] or "")[:6], m["price_band"]))
        blob = json.dumps(block, indent=1, sort_keys=True).encode()
        (out / "block_cohort.json").write_bytes(blob)
        meta["block_cohort_sha256"] = hashlib.sha256(blob).hexdigest()
        say()
        if len(block) < 2:
            say("BLOCK TOO SMALL -- no capture attempted.")
            res["aborted"] = "BLOCK_TOO_SMALL"
        else:
            say("=== CAPTURE (%d cycles x %ds) ===" % (MAX_CYCLES, int(CYCLE_S)))
            meta["actual_start_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            with gzip.open(out / "block_log.jsonl.gz", "wt") as fh:
                run_block(http, pacer, budget, block, fh, res)
            meta["actual_end_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    res["venue_requests"] = budget.spent
    res["throttle_events"] = pacer.events
    (out / "runner_metadata.json").write_text(json.dumps(meta, indent=1))
    (out / "block_summary.json").write_text(json.dumps(res, indent=1, default=str))
    with gzip.open(out / "discovery_request_log.jsonl.gz", "wt") as fh:
        for r in log:
            fh.write(json.dumps(r, default=str) + "\n")

    lines = ["=== RUN 85 TRACK B-L BLOCK %s ===" % a.block,
             "SELECTION_RULE_VERSION = %s" % SELECTION_RULE_VERSION,
             "BLOCK_COHORT_SHA256 = %s" % meta.get("block_cohort_sha256"),
             "BLOCK_SIZE = %d" % res.get("block_size", 0),
             "VENUE_REQUESTS = %d" % budget.spent,
             "PLANNED_MIN_GAP_S = %s" % res.get("planned_min_gap_s"),
             "HTTP_429 = %s" % res.get("http_429"),
             "HORIZONS_DERIVABLE = %s" % json.dumps(DERIVABLE_LONG_HORIZONS),
             "HORIZONS_UNREACHABLE = %s (NOT_OBSERVED_WITHIN_BLOCK_LENGTH)"
             % list(UNREACHABLE_HORIZONS),
             "TOUCH_IS_NOT_FILL. MAKER_FILL_PROBABILITY = NOT_IDENTIFIED.",
             "PAIR_COMPLETION_PROBABILITY = NOT_IDENTIFIED.",
             "NOT POOLED WITH TRACK A. mirror_live = false."]
    for l in lines:
        say(l)
    B.seal(out, lines)
    ok, bad = B.verify(out)
    say()
    say("SEAL_VERIFIED = %s%s" % (ok, "" if ok else " BAD=%s" % bad))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
