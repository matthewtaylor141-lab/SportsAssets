#!/usr/bin/env python3
"""STAGE-2 ROLLING ELIGIBILITY CENSUS + ROUTING-CENSORING AUDIT.

WHY THIS IS A SEPARATE MODULE. `fwd_collect.py breadth --book-reads N` reads
the first N markets in BOARD ORDER. Board order is a systematic ordering, so
that sample is a prefix of a prefix, and it spends reads on markets the frozen
stage-1 rule already rejected. The preregistered census routes on STAGE-1 BROAD
and interleaves an AUDIT lane of stage-1 rejects by salted hash. Neither
selection exists in `fwd_collect.py`, and rather than edit a collector whose
panel is running, this module is new and `fwd_collect.py` stays byte-identical.

CAPABILITY BOUNDARY, identical to the authorized collector and re-proved by an
AST scan before any contact:
    GET only. gateway.polymarket.us only. No credential, no secret, no wallet,
    no signer, no authentication header, no trading SDK, no order or cancel
    endpoint, no POST/PUT/PATCH/DELETE. 2.0 rps as OUR restraint, Retry-After
    honoured exactly and never shortened.

NO PROFITABILITY CLAIM. This records responses and the frozen screen's own
verdicts. It computes no expectancy, no ROI, no fill probability and no
fee-adjusted figure. TOUCH is never written into a FILL field.

WHAT IT ANSWERS. Whether the non-UFC board -- 98.1% of the stage-1 BROAD routed
universe, which no book read has ever touched -- carries better trade recency,
materially different depth, materially different queue sizes, and a meaningful
ACTIVE / HIGH_ACTIVITY population. It does NOT assume the answer in either
direction: the other 98.1% is the GENERALIZATION TARGET, not a presumed
improvement.
"""
from __future__ import annotations

import argparse
import json
import time
from decimal import Decimal
from pathlib import Path

import eligibility as E
import fwd_collect as F

CENSUS_SALT = "BETA48-STAGE2-CENSUS-2026-09-16"
CENSUS_VERSION = "beta48-stage2-census/1"

# The audit lane. Stage 1 routes on the BOARD's quote, which can be stale by
# the time a decision would be made; a market it rejected may have tightened.
# Reading ONLY the routed markets would make the routing rule unfalsifiable, so
# a fraction of stage-1 REJECTS is read too and the false-negative rate is
# measured rather than assumed to be zero.
AUDIT_FRACTION = 0.05
ROUTING_CENSORING_AUDIT = "ROUTING_CENSORING_AUDIT_V1"


def _jsonable(o):
    """Decimal -> str, EXACTLY. Never float.

    `eligibility.spread_ticks()` returns a Decimal, because a spread in ticks is
    computed from Decimal money and turning it into a float would silently
    change the value we measured. json.dumps cannot serialize Decimal, so it is
    written as its exact decimal string and a test round-trips a real stage-1
    dict to prove it.
    """
    if isinstance(o, Decimal):
        return str(o)
    raise TypeError("not JSON serializable: %s" % type(o).__name__)


def _rank(slug, tag, salt=CENSUS_SALT):
    import hashlib
    return hashlib.sha256(
        ("%s|%s|%s" % (salt, tag, slug)).encode("utf-8")).hexdigest()


def plan(board_events, audit_fraction=AUDIT_FRACTION, salt=CENSUS_SALT):
    """Route on stage 1, then interleave routed and audit lanes by salted hash.

    Returns (schedule, summary). The schedule's ORDER is fixed here, before any
    book has been read, so an audit market's position in the scan cannot depend
    on anything about the market.
    """
    routed, rejected = [], []
    for ev in board_events:
        s1 = E.stage1(ev)
        (routed if s1.get("BROAD") else rejected).append(ev)

    # The audit sample is itself a salted-hash draw, not the first k rejects.
    rejected.sort(key=lambda e: _rank(str(e.get("slug")), "audit-pick", salt))
    k = int(len(rejected) * audit_fraction)
    audit = rejected[:k]

    sched = E.audit_schedule([str(e.get("slug")) for e in routed],
                             [str(e.get("slug")) for e in audit], salt)
    by_slug = {str(e.get("slug")): e for e in routed}
    by_slug.update({str(e.get("slug")): e for e in audit})
    schedule = [(r["position"], r["lane"], by_slug[r["slug"]])
                for r in sched if r["slug"] in by_slug]
    return schedule, {
        "CENSUS_VERSION": CENSUS_VERSION,
        "CENSUS_SALT": salt,
        "SALT_FROZEN_BEFORE_OUTCOMES": "YES",
        "BOARD_MARKETS": len(board_events),
        "STAGE1_BROAD_ROUTED_MARKETS": len(routed),
        "STAGE1_REJECTED_MARKETS": len(rejected),
        "AUDIT_FRACTION": audit_fraction,
        "AUDIT_LANE_MARKETS": len(audit),
        "TOTAL_BOOK_READS_PLANNED": len(schedule),
        "ROUTING_CENSORING_AUDIT": ROUTING_CENSORING_AUDIT,
        "LANES_INTERLEAVED_BY_SALTED_HASH": "YES",
        "SCHEDULE_FAIRNESS_ASSERTED_FROM_POSITION": False,
        "MAKER_PROFITABILITY": "NOT_ESTABLISHED",
    }


def census(outdir: Path, board_events, pacer, http, limit=None,
           audit_fraction=AUDIT_FRACTION, salt=CENSUS_SALT):
    """One book read per scheduled market, with its own per-row clock."""
    schedule, summary = plan(board_events, audit_fraction, salt)
    if limit is not None:
        schedule = schedule[:int(limit)]
        summary["TOTAL_BOOK_READS_PLANNED"] = len(schedule)
        summary["SCHEDULE_TRUNCATED_TO"] = int(limit)
        summary["TRUNCATION_IS_A_PREFIX_OF_A_FAIR_ORDER"] = "YES"

    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "census_plan.json").write_text(
        json.dumps(summary, indent=1, default=_jsonable))

    path = outdir / "census.jsonl"
    scan_start = time.time()
    n = 0
    with path.open("a") as fh:
        for pos, lane, ev in schedule:
            slug = str(ev.get("slug"))
            leg = F._paced_get(http, pacer, F.BOOK_PATH.format(slug=slug))
            now_s = time.time()
            # EVERY row carries its own clock. Stage 1 observed the board at
            # T0; this row is decided at Ti, and the two are never conflated.
            row = {
                "kind": "CENSUS",
                "position": pos,
                "lane": lane,
                "slug": slug,
                "elapsed_s": now_s - scan_start,
                "sportsMarketTypeV2": ev.get("sportsMarketTypeV2"),
                "orderPriceMinTickSize": ev.get("orderPriceMinTickSize"),
                "board_bestBidQuote": ev.get("board_bestBidQuote"),
                "board_bestAskQuote": ev.get("board_bestAskQuote"),
                "STAGE1": E.stage1(ev),
                "leg": leg,
            }
            fh.write(json.dumps(row, default=_jsonable) + "\n")
            n += 1
            if n % 500 == 0:
                print("census rows %d / %d | requests %d | elapsed %.0fs"
                      % (n, len(schedule), pacer.requests, now_s - scan_start),
                      flush=True)
    summary["CENSUS_ROWS_WRITTEN"] = n
    summary["SCAN_DURATION_S"] = time.time() - scan_start
    summary["REQUESTS_USED"] = pacer.requests
    (outdir / "census_plan.json").write_text(
        json.dumps(summary, indent=1, default=_jsonable))
    print("census rows written %d | requests %d | %.0fs"
          % (n, pacer.requests, summary["SCAN_DURATION_S"]))
    return n


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--board", required=True)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--audit-fraction", type=float, default=AUDIT_FRACTION)
    a = ap.parse_args(argv)

    board = json.loads(Path(a.board).read_text())
    events = board.get("selected") or []
    out = Path(a.out)
    import httpx
    pacer = F.Pacer()
    with httpx.Client(headers={"User-Agent": CENSUS_VERSION}) as http:
        census(out, events, pacer, http, limit=a.limit,
               audit_fraction=a.audit_fraction)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
