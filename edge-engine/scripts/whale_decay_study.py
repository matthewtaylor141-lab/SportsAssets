"""How much of the reference whale's edge survives OUR copy latency?

The question that decides whether whale-copying earns a live sleeve. His
lifetime edge is measured (2.3c/fill stake-weighted, Phase 1, n=5.35M).
What that study cannot say is what remains for a FOLLOWER who fills
Δ seconds after him — if the market reprices toward fair within our
reaction time, his edge is real and ours is gone.

Method, all public data:
  1. Pull his recent BUY fills from /activity (honors start/end).
  2. For each sampled fill, pull the market's own trade tape and read the
     last traded price on the SAME outcome token at t+15s/30s/60s/300s.
  3. drift(Δ) = price(t+Δ) - his_price, in cents. The copyable edge at
     latency Δ is his entry edge minus that drift.

Honesty rules: a fill with no tape coverage at Δ is EXCLUDED and counted
(no zero-filling — an untraded market is unknown, not unmoved); every
figure carries its n; medians shown beside means because whale fills are
heavy-tailed.
"""

from __future__ import annotations

import json
import os
import random
import time
from collections import defaultdict
from datetime import datetime, timezone

import requests

WALLET = "0x204f72f35326db932158cba6adff0b9a1da95e14"  # swisstony (Phase 1)
DATA_API = "https://data-api.polymarket.com"
DELTAS = (15, 30, 60, 300)
DAYS = int(os.environ.get("STUDY_DAYS", "7"))
MAX_FILLS = int(os.environ.get("STUDY_MAX_FILLS", "400"))
MAX_MARKETS = int(os.environ.get("STUDY_MAX_MARKETS", "150"))
TAPE_PAGES = int(os.environ.get("STUDY_TAPE_PAGES", "4"))   # x500 trades
ASSUMED_EDGE_C = 2.3   # his measured lifetime entry edge, cents/fill


def _get(url: str, params: dict, retries: int = 6):
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=30)
            if r.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)
    return None


def pull_his_buys(start_ts: int, end_ts: int) -> list[dict]:
    """His TRADE activity, BUY side, newest-first, cursor on `end`."""
    out, cursor = [], end_ts
    for _ in range(200):
        rows = _get(f"{DATA_API}/activity",
                    {"user": WALLET, "type": "TRADE", "limit": 500,
                     "start": start_ts, "end": cursor}) or []
        if not rows:
            break
        for t in rows:
            if (t.get("side") or "").upper() == "BUY":
                out.append(t)
        oldest = min(int(t.get("timestamp") or cursor) for t in rows)
        if oldest <= start_ts or oldest >= cursor:
            break
        cursor = oldest
    # De-dup boundary overlaps by fill key.
    seen, uniq = set(), []
    for t in out:
        k = (t.get("transactionHash"), t.get("asset"), t.get("price"),
             t.get("size"), t.get("timestamp"))
        if k not in seen:
            seen.add(k)
            uniq.append(t)
    return uniq


def market_tape(condition_id: str) -> list[dict]:
    """The market's public trade tape, newest-first, a few pages deep."""
    tape = []
    for page in range(TAPE_PAGES):
        rows = _get(f"{DATA_API}/trades",
                    {"market": condition_id, "limit": 500,
                     "offset": page * 500}) or []
        tape.extend(rows)
        if len(rows) < 500:
            break
    return tape


def main() -> None:
    now = int(time.time())
    fills = pull_his_buys(now - DAYS * 86400, now)
    per_day = defaultdict(int)
    for t in fills:
        day = datetime.fromtimestamp(int(t["timestamp"]),
                                     tz=timezone.utc).strftime("%m-%d")
        per_day[day] += 1
    print(f"WHALE ACTIVITY ({DAYS}d): {len(fills)} BUY fills")
    for day in sorted(per_day):
        print(f"  {day}: {per_day[day]}")
    if not fills:
        print("VERDICT: the reference account has NOT traded in this window."
              " Nothing to copy; edge-carry is moot until he returns.")
        return

    random.seed(20260802)
    random.shuffle(fills)
    sample, markets = [], []
    seen_mkts: set[str] = set()
    for t in fills:
        cid = t.get("conditionId")
        if not cid:
            continue
        if cid not in seen_mkts and len(seen_mkts) >= MAX_MARKETS:
            continue
        seen_mkts.add(cid)
        sample.append(t)
        if len(sample) >= MAX_FILLS:
            break
    print(f"sample: {len(sample)} fills across {len(seen_mkts)} markets")

    tapes: dict[str, list[dict]] = {}
    for i, cid in enumerate(seen_mkts):
        tapes[cid] = market_tape(cid)
        if i % 25 == 0:
            print(f"  tape {i}/{len(seen_mkts)}...")

    drifts = {d: [] for d in DELTAS}
    no_cover = {d: 0 for d in DELTAS}
    fillable_30 = 0
    for t in sample:
        cid, asset = t["conditionId"], t.get("asset")
        ts, px = int(t["timestamp"]), float(t.get("price") or 0)
        if not (0 < px < 1):
            continue
        tape = [(int(r.get("timestamp") or 0), float(r.get("price") or 0))
                for r in tapes.get(cid, [])
                if r.get("asset") == asset and r.get("price")]
        tape.sort()
        if not tape or tape[0][0] > ts:
            for d in DELTAS:
                no_cover[d] += 1
            continue
        after = [(tt, pp) for tt, pp in tape if tt > ts]
        if any(ts < tt <= ts + 30 for tt, _ in after):
            fillable_30 += 1
        for d in DELTAS:
            window = [pp for tt, pp in after if tt <= ts + d]
            if not window:
                # Tape must COVER the horizon or the fill is unknowable:
                # if the tape's newest trade predates t+d we cannot claim
                # "no move", only "no data".
                if tape[-1][0] < ts + d:
                    no_cover[d] += 1
                    continue
                drifts[d].append(0.0)    # covered, and nothing printed: unmoved
                continue
            drifts[d].append((window[-1] - px) * 100)

    print()
    print("EDGE DECAY AFTER HIS BUYS (cents; + = market moved toward his side)")
    print(f"assumed entry edge: {ASSUMED_EDGE_C}c (Phase-1 lifetime, stake-wtd)")
    print(f"{'Δ':>6} {'n':>5} {'no_cover':>8} {'mean':>7} {'median':>7} "
          f"{'p25':>6} {'p75':>6} {'copyable(mean)':>14}")
    for d in DELTAS:
        xs = sorted(drifts[d])
        if not xs:
            print(f"{d:>5}s {0:>5} {no_cover[d]:>8}      —       —      —"
                  f"      —              —")
            continue
        n = len(xs)
        mean = sum(xs) / n
        med = xs[n // 2]
        p25, p75 = xs[n // 4], xs[(3 * n) // 4]
        print(f"{d:>5}s {n:>5} {no_cover[d]:>8} {mean:>6.2f}c {med:>6.2f}c "
              f"{p25:>5.2f}c {p75:>5.2f}c {ASSUMED_EDGE_C - mean:>13.2f}c")
    if sample:
        print(f"\nfillable within 30s (a trade printed): "
              f"{fillable_30}/{len(sample)}")
    print("\nREAD: 'copyable(mean)' is his entry edge minus the average move "
          "the market makes before a copier at that latency gets filled. "
          "Above ~1.5-2.0c at our 15-60s latency, a copy sleeve clears the "
          "same net floor the engine's own trades must clear. Near zero, "
          "his edge is real but PRICED IN before we arrive.")


if __name__ == "__main__":
    main()
