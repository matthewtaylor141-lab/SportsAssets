"""Worker: the unselected prospective PMUS state capture. READ ONLY.

Owner directive 2026-09-20: "APPROVED -- START THE READ-ONLY
PROSPECTIVE PMUS UNSELECTED CAPTURE. ... No orders. No capital. No
shadow fill fabrication. No mandate activation."

There is no order path in this module's import graph. It reads the
venue's public book, writes state rows, and appends future mids and
settlements against those rows. Nothing it writes is a fill, and
`bettor_state_capture.forbidden_name` refuses to let anything derived
from it be called maker adverse selection.

THE THREE PASSES OF ONE TICK

  1. SAMPLE   -- the frozen rotation picks this cycle's slice from the
                 premap. Nothing about a book is read before the choice
                 is made, so the choice cannot depend on one.
  2. FOLLOW   -- observations whose declared horizon has just come due
                 get one re-read, stamped with the ACTUAL lag.
  3. SETTLE   -- resolved markets get their settlement appended, with
                 the semantics status carried as its own field.

READ BUDGET. This shares a gateway with the money path and the venue
429'd a board walk above ~3 req/s. The tick's reads are capped in
total, the sampling pass gets first claim on that budget, and the
follow-up pass takes what is left -- because a missing follow-up row
is a visible gap, while a missing state row is a hole in the sampling
frame that can never be filled in afterwards.

Kill: BETTOR_STATE_CAPTURE=off.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone

from .. import bettor_state_capture as sc
from .. import bettor_state_store as sstore
from .. import pmus
from ..db import get_pool, heartbeat
from ..venue_pace import pace

log = logging.getLogger(__name__)

TICK_S = 60.0
BACKOFF_S = 120.0
READ_PACING_S = 0.4
MAX_READS_PER_TICK = 24
MAX_FOLLOWUP_READS = 8
MISS_ABANDON = 6

PREMAP_SQL = """
    SELECT p.identifier, p.market_slug, p.event_slug, p.side_norm,
           p.kind, p.sports_type, p.team_league, p.game_start
      FROM us_premap p
     WHERE p.updated_at > now() - ($1 || ' seconds')::interval
"""
# NOTE THE ABSENT ORDER BY AND LIMIT. The sibling BETTOR lane orders by
# updated_at DESC and takes the top rows, which selects on recent venue
# activity. Here the WHOLE eligible set is fetched and the rotation --
# a pure function of identifier and clock -- decides. Ordering the
# query would put a selection upstream of the frozen rule.


def _off(name: str, default: str = "on") -> bool:
    return os.getenv(name, default).strip().lower() in (
        "off", "0", "false", "no")


def _read_book(slug: str) -> dict:
    """One paced public book read, off the event loop. Never raises."""
    pace(READ_PACING_S)
    try:
        client = pmus._get_client()
    except Exception as exc:                                   # noqa: BLE001
        return {"marketData": None, "feed": None,
                "error": type(exc).__name__}
    try:
        return pmus.book_read(client, slug)
    except Exception as exc:                                   # noqa: BLE001
        return {"marketData": None, "feed": None,
                "error": type(exc).__name__}


def _mid_of(book: dict):
    """Mid from a book payload, or None. Never one side doubled."""
    md = (book or {}).get("marketData")
    if not isinstance(md, dict):
        return None
    b = pmus._quote_px(md, "bestBid", "best_bid", "bid")
    a = pmus._quote_px(md, "bestAsk", "best_ask", "ask")
    if b is None or a is None:
        return None
    try:
        return (float(b) + float(a)) / 2
    except (TypeError, ValueError):
        return None


async def _candidates(pool) -> list:
    rows = await pool.fetch(PREMAP_SQL, str(int(sc.PREMAP_FRESH_S)))
    out = []
    for r in rows:
        ok, _why = sc.eligible(dict(r))
        if not ok:
            continue
        out.append({
            "identifier": r["identifier"], "symbol": r["market_slug"],
            "marketId": r["market_slug"], "eventId": r["event_slug"],
            "outcomeLeg": r["side_norm"], "kind": r["kind"],
            "sportSourceRaw": r["sports_type"],
            "leagueSourceRaw": r["team_league"],
            "gameStart": r["game_start"],
        })
    return out


async def tick(pool, *, last_bucket=None) -> dict:
    """One cycle. THE SAMPLING PASS RUNS ONCE PER CADENCE BUCKET.

    The loop ticks every 60s and the sampling cadence is 300s, so five
    consecutive ticks fall inside one bucket and compute the same
    rotation cycle. Sampling on every one of them would re-read the
    same markets five times, write four rows the primary key discards,
    and spend five times the venue budget to learn nothing. The four
    non-sampling ticks are spent on follow-up reads instead, which is
    where the short horizons actually live.
    """
    at = datetime.now(tz=timezone.utc)
    bucket = sc.bucket_of(at)
    sampling = bucket != last_bucket
    stats = {"status": "ok", "cycle": sc.cycle_of(at),
             "bucket": bucket, "sampled": sampling,
             "universe": sc.UNIVERSE_VERSION,
             "ruleSha": sc.RULE_SHA[:16],
             "rotationSlices": sc.ROTATION_SLICES,
             "eligible": 0, "inSlice": 0, "read": 0, "written": 0,
             "duplicateBucket": 0, "unreadable": 0, "failures": 0,
             "sliceTruncated": False, "followups": 0, "reads": 0}

    sel = {"SELECTED": []}
    if sampling:
        try:
            cands = await _candidates(pool)
        except Exception as exc:                               # noqa: BLE001
            stats["status"] = "premap_unreadable"
            stats["lastError"] = "%s: %s" % (type(exc).__name__, exc)
            return stats
        sel = sc.select(cands, at=at)
        stats["eligible"] = sel["CANDIDATES_ELIGIBLE"]
        stats["inSlice"] = sel["CANDIDATES_IN_SLICE"]
        stats["sliceTruncated"] = sel["SLICE_TRUNCATED"]
        stats["sliceTruncatedBy"] = sel["SLICE_TRUNCATED_BY"]

    misses = 0
    budget = MAX_READS_PER_TICK if sampling else 0
    for subject in sel["SELECTED"]:
        if budget <= 0:
            stats["status"] = "read_budget_exhausted"
            break
        budget -= 1
        stats["reads"] += 1

        request_at = datetime.now(tz=timezone.utc)
        book = await asyncio.to_thread(_read_book, subject["symbol"])
        received_at = datetime.now(tz=timezone.utc)
        stats["read"] += 1

        # THE HISTORY IS THIS DATASET'S OWN, and it carries no outcome
        # column, so a feature built from it cannot see the future.
        try:
            hist = await sstore.history(subject["marketId"],
                                        subject["outcomeLeg"], pool=pool)
        except Exception:                                      # noqa: BLE001
            hist = []

        row = sc.state_record(
            subject, observed_at=request_at,
            book=book.get("marketData"), received_at=received_at,
            request_at=request_at, feed=book.get("feed"),
            read_error=book.get("error"), history=hist, selection=sel)

        if row["BOOK_READABILITY_STATUS"] != "READABLE":
            misses += 1
            stats["unreadable"] += 1

        # A SELECTED MARKET WRITES A ROW EITHER WAY. Dropping the
        # unreadable ones would condition the frame on readability.
        try:
            _oid, first = await sstore.record_state(row, pool=pool)
        except Exception as exc:                               # noqa: BLE001
            stats["failures"] += 1
            stats["lastError"] = "%s: %s" % (type(exc).__name__, exc)
            log.warning("bettor_state: write failed for %s",
                        subject["symbol"], exc_info=True)
            continue
        if first:
            stats["written"] += 1
        else:
            stats["duplicateBucket"] += 1

        if misses >= MISS_ABANDON:
            stats["status"] = "venue_unreadable"
            break

    # ── pass 2: the declared horizons ────────────────────────────────
    #
    # The follow-up budget is independent of the sampling budget on a
    # non-sampling tick, which is the whole point: four ticks in five
    # do nothing but chase horizons that have come due.
    follow_budget = (min(MAX_FOLLOWUP_READS, max(0, budget)) if sampling
                     else MAX_FOLLOWUP_READS)
    for horizon in sc.HORIZONS_OBSERVABLE_S:
        if follow_budget <= 0:
            break
        try:
            due = await sstore.mids_due(horizon, limit=follow_budget,
                                        pool=pool)
        except Exception:                                      # noqa: BLE001
            break
        for d in due:
            if follow_budget <= 0:
                break
            follow_budget -= 1
            stats["reads"] += 1
            book = await asyncio.to_thread(_read_book, d["market_id"])
            read_at = datetime.now(tz=timezone.utc)
            mid = _mid_of(book)
            try:
                await sstore.record_mid(sc.mid_observation(
                    d["observation_id"], horizon_s=horizon,
                    observed_at=d["observed_at"], read_at=read_at,
                    mid=mid), pool=pool)
                stats["followups"] += 1
            except Exception:                                  # noqa: BLE001
                stats["failures"] += 1
    return stats


async def run() -> None:
    if _off("BETTOR_STATE_CAPTURE"):
        log.info("bettor_state: capture off by switch")
        return
    pool = await get_pool()
    ready = await sstore.store_ready(pool)

    boot = {
        "lane": "BETTOR_UNSELECTED_STATE",
        "readOnly": True,
        "orderPathExists": False,
        "capitalAtRisk": 0,
        "mirrorLive": False,
        "universe": sc.UNIVERSE_VERSION,
        "ruleSha": sc.RULE_SHA,
        "rotationSlices": sc.ROTATION_SLICES,
        "cadenceS": sc.SAMPLING_CADENCE_S,
        "fullRotationS": sc.ROTATION_SLICES * sc.SAMPLING_CADENCE_S,
        "horizonsObservableS": list(sc.HORIZONS_OBSERVABLE_S),
        "horizonsNotObservableS": list(sc.HORIZONS_NOT_OBSERVABLE_S),
        "measures": sc.OBJECT_A,
        "measuredQuantity": sc.MEASURED_QUANTITY,
        "doesNotMeasure": [sc.OBJECT_B, sc.OBJECT_C, sc.OBJECT_D],
        "storeReady": ready["storeReady"],
        "problems": ready["problems"],
    }
    log.info("bettor_state: %s", boot)

    if not ready["storeReady"]:
        while True:
            await heartbeat("bettor_state", "store_not_ready", boot)
            await asyncio.sleep(BACKOFF_S)

    last_bucket = None
    while True:
        started = time.monotonic()
        try:
            stats = await tick(pool, last_bucket=last_bucket)
            # ADVANCED ONLY ON A COMPLETED SAMPLING PASS. A tick that
            # died reading the premap has not sampled its bucket, and
            # marking it sampled would drop that bucket from the frame
            # for good.
            if stats.get("sampled") and stats["status"] != \
                    "premap_unreadable":
                last_bucket = stats.get("bucket")
        except Exception as exc:                               # noqa: BLE001
            log.warning("bettor_state: tick failed", exc_info=True)
            stats = {"status": "tick_failed",
                     "tickError": "%s: %s" % (type(exc).__name__, exc)}
        stats.update(boot)
        stats["tickS"] = round(time.monotonic() - started, 3)
        try:
            await heartbeat("bettor_state",
                            str(stats.get("status") or "ok"), stats)
        except Exception as exc:                               # noqa: BLE001
            log.error("bettor_state: heartbeat write failed: %s: %s",
                      type(exc).__name__, exc)
        await asyncio.sleep(
            BACKOFF_S if stats.get("status") == "venue_unreadable"
            else TICK_S)
