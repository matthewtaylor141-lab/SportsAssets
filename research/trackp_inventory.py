#!/usr/bin/env python3
"""TRACK P step 0: inventory the data we already have, and audit it for the
two things that would make any profitability claim worthless -- future
leakage and undeclared selection bias.

READ ONLY, OFFLINE. Contacts nothing, writes only under research/evidence/.

WHAT THE SNAPSHOT ACTUALLY IS
`u2_events_v1` is 214,609 (trade, probe) pairs from AUDIT_SNAPSHOT_V1: each
row is one moment at which BETTOR read a token's ASK LADDER from the venue
because RN1 had just traded that token. `settlement_v1` is the settled
payout for 17,752 conditions.

So the row carries, at one timestamp:
  - an EXECUTABLE ask ladder (price, size) -- what we could have paid
  - the token's identity and outcome index
and the condition carries which outcome paid $1.

That is a calibration dataset: a contract buyable at ask `a` either settles
to $1 or to $0, and the question is whether the settlement rate matches
`a`.

TWO THINGS THIS DATA IS NOT
1. It is NOT the tradable universe. The sampling frame is "moments RN1
   traded", so every count here is conditional on RN1's selection. That is
   a selection bias on the POPULATION, and it is reported, never removed by
   assumption.
2. RN1's own fields (his_price, his_size, side, notional, reaction_s) are
   NOT features. They are the reason the row exists, which is exactly why
   they must not also be the signal. This file lists them so the modelling
   step can refuse them by name.
"""

from __future__ import annotations

import gzip
import json
import os
from collections import Counter, defaultdict
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
SNAP = os.path.join(HERE, "snapshots")
OUT = os.path.join(HERE, "evidence", "trackp")

EVENTS = os.path.join(SNAP, "u2_events_v1.jsonl.gz")
SETTLE = os.path.join(SNAP, "settlement_v1.jsonl")

# RN1-derived columns. Named here so the model can assert it never reads
# one. The row exists BECAUSE of these; using them as the signal would be
# measuring RN1, which is the one thing Track P must not do.
RN1_FIELDS = ("side", "size", "price", "notional", "his_price", "his_size",
              "his_notional", "reaction_s", "trade_id", "ts", "detected_at",
              "source", "probe_id")

# Market observation columns, available AT probe_at.
MARKET_FIELDS = ("best_ask", "best_ask_usd", "depth", "depth_levels",
                 "book_ok", "probe_at")


def ts(x):
    if not x:
        return None
    try:
        return datetime.fromisoformat(x.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def load_settlement():
    rows = {}
    with open(SETTLE) as fh:
        for line in fh:
            if not line.strip():
                continue
            r = json.loads(line)
            rows[r["condition_id"]] = r
    return rows


def winner_index(s):
    """Which outcome index paid $1. A payout vector that is not a clean
    one-hot is NOT a label -- 50/50 voids and multi-way payouts are
    excluded by name rather than rounded into a winner."""
    pay = s.get("payouts") or []
    try:
        vals = [float(x) for x in pay]
    except (TypeError, ValueError):
        return None, "PAYOUT_NOT_NUMERIC"
    if not vals:
        return None, "PAYOUT_EMPTY"
    ones = [i for i, v in enumerate(vals) if v == 1.0]
    zeros = [i for i, v in enumerate(vals) if v == 0.0]
    if len(ones) == 1 and len(ones) + len(zeros) == len(vals):
        return ones[0], "OK"
    if all(v == 0.5 for v in vals):
        return None, "PAYOUT_SPLIT_50_50"
    return None, "PAYOUT_NOT_ONE_HOT"


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    lines = []

    def say(m=""):
        lines.append(m)
        print(m)

    settle = load_settlement()
    say("=" * 72)
    say("TRACK P -- DATA INVENTORY AND INTEGRITY AUDIT")
    say("=" * 72)
    say()

    n = 0
    probe_min = probe_max = None
    sports = Counter()
    linked = 0
    resolved = 0
    label_ok = 0
    label_reject = Counter()
    book_ok = 0
    has_depth = 0
    depth_levels = Counter()
    leak_resolved_before_probe = 0
    leak_unknown_resolution_time = 0
    missing_probe_at = 0
    ask_present = 0
    conditions = set()
    slugs = set()
    per_day = Counter()
    gamestart_present = 0
    live_flag_present = 0

    for line in gzip.open(EVENTS, "rt"):
        r = json.loads(line)
        n += 1
        p = ts(r.get("probe_at"))
        if p is None:
            missing_probe_at += 1
        else:
            probe_min = p if probe_min is None or p < probe_min else probe_min
            probe_max = p if probe_max is None or p > probe_max else probe_max
            per_day[p.date().isoformat()] += 1
        sports[r.get("sport") or "NOT_IDENTIFIED"] += 1
        if r.get("book_ok"):
            book_ok += 1
        d = r.get("depth") or []
        if d:
            has_depth += 1
            depth_levels[min(len(d), 20)] += 1
        if r.get("best_ask") not in (None, ""):
            ask_present += 1
        if r.get("market_slug"):
            slugs.add(r["market_slug"])
        # the venue never sends a game start time or a live flag on this row
        if r.get("game_start_time"):
            gamestart_present += 1
        if r.get("live") is not None:
            live_flag_present += 1

        cid = r.get("condition_id_effective") or r.get("condition_id")
        if not cid:
            continue
        conditions.add(cid)
        s = settle.get(cid)
        if s is None:
            continue
        linked += 1
        if not s.get("resolved"):
            continue
        resolved += 1
        wi, why = winner_index(s)
        if wi is None:
            label_reject[why] += 1
            continue
        label_ok += 1
        # LEAKAGE GATE: the decision instant must precede resolution.
        ra = ts(s.get("resolved_at"))
        if ra is None:
            leak_unknown_resolution_time += 1
        elif p is not None and ra <= p:
            leak_resolved_before_probe += 1

    say("--- DATASET ---")
    say("  DATASET                    AUDIT_SNAPSHOT_V1 "
        "(u2_events_v1 + settlement_v1)")
    say("  SOURCE                     research/snapshots/, drawn "
        "2026-09-12T02:53:56Z, read-only repeatable-read")
    say("  DATE RANGE (probe_at)      %s .. %s"
        % (probe_min.isoformat() if probe_min else "NOT_IDENTIFIED",
           probe_max.isoformat() if probe_max else "NOT_IDENTIFIED"))
    say("  DAYS COVERED               %d" % len(per_day))
    say("  OBSERVATIONS               %d" % n)
    say("  DISTINCT CONDITIONS        %d" % len(conditions))
    say("  DISTINCT MARKET SLUGS      %d" % len(slugs))
    say("  SETTLED MARKETS (file)     %d" % len(settle))
    say()
    say("--- SPORTS ---")
    for sp, c in sports.most_common():
        say("  %-24s %8d  (%5.1f%%)" % (sp, c, 100.0 * c / n))
    say()
    say("--- FIELD AVAILABILITY ---")
    say("  PRICE HISTORY AVAILABLE?   NO -- one observation per probe, not "
        "a time series")
    say("  EXECUTABLE PRICE AT T?     YES -- best_ask present on %d/%d "
        "(%.1f%%)" % (ask_present, n, 100.0 * ask_present / n))
    say("  BOOK DEPTH AVAILABLE?      YES -- ask ladder on %d/%d (%.1f%%), "
        "median %d levels"
        % (has_depth, n, 100.0 * has_depth / n,
           sorted(depth_levels.elements())[len(list(depth_levels.elements()))
                                           // 2] if depth_levels else 0))
    say("  BOOK_OK                    %d/%d (%.1f%%)"
        % (book_ok, n, 100.0 * book_ok / n))
    say("  GAME START TIME AVAILABLE? NO -- field absent on every row (%d)"
        % gamestart_present)
    say("  LIVE/PREGAME IDENTIFIABLE? NO -- no live flag and no start time, "
        "so time-to-event cannot be computed (%d)" % live_flag_present)
    say("  OUTCOME AVAILABLE?         YES -- via condition_id -> "
        "settlement_v1 payouts")
    say()
    say("--- LABEL LINKAGE ---")
    say("  LINKED TO A SETTLEMENT ROW %d (%.1f%%)"
        % (linked, 100.0 * linked / n))
    say("  OF THOSE, RESOLVED         %d" % resolved)
    say("  CLEAN ONE-HOT LABEL        %d" % label_ok)
    for why, c in label_reject.most_common():
        say("    rejected %-22s %d" % (why, c))
    say()
    say("--- LEAKAGE AUDIT ---")
    say("  resolved_at <= probe_at    %d  (EXCLUDED: the market was "
        "already settled when observed)" % leak_resolved_before_probe)
    say("  resolution time unknown    %d  (EXCLUDED: cannot prove the "
        "label is in the future)" % leak_unknown_resolution_time)
    say("  MODELLABLE ROWS            %d"
        % (label_ok - leak_resolved_before_probe
           - leak_unknown_resolution_time))
    say()
    say("--- RN1 CONTAMINATION CONTROL ---")
    say("  RN1-DERIVED COLUMNS (banned as features):")
    say("    %s" % ", ".join(RN1_FIELDS))
    say("  MARKET COLUMNS (allowed, all observed at probe_at):")
    say("    %s" % ", ".join(MARKET_FIELDS))
    say()
    say("--- KNOWN COVERAGE GAPS ---")
    say("  * No price time series: one ask ladder per probe, so no")
    say("    pre/post drift, no closing line, no market movement feature.")
    say("  * No game start time and no live flag, so PREGAME vs LIVE")
    say("    cannot be separated. That entire axis is NOT_IDENTIFIED.")
    say("  * Market TYPE is not a field; only a slug string exists.")
    say("  * The ladder is the ASK side only -- no bid, so no spread and")
    say("    no sell-side execution can be modelled.")
    say()
    say("--- KNOWN SELECTION BIAS (the big one) ---")
    say("  The sampling frame is MOMENTS RN1 TRADED. This is not the")
    say("  tradable universe and not a random sample of it. Every rate")
    say("  below is conditional on RN1 having just bought that token.")
    say("  A calibration result here describes the prices RN1's flow")
    say("  selects, which is a narrower and possibly very different")
    say("  population from 'all quotable sports contracts'.")
    say()
    say("  SAFE_FOR_MODELING?  YES for a CALIBRATION test on this")
    say("  population, with the bias declared. NO for any claim about the")
    say("  venue's whole board without a second, unbiased sample.")
    say()
    say("--- OBSERVATIONS PER DAY ---")
    for d, c in sorted(per_day.items()):
        say("  %s  %6d" % (d, c))

    with open(os.path.join(OUT, "trackp_inventory.txt"), "w") as fh:
        fh.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
