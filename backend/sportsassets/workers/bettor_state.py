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

from .. import bettor_sport_mapping as sportmap
from .. import bettor_state_capture as sc
from .. import bettor_state_store as sstore
from .. import pmus
from ..db import get_pool, heartbeat
from ..venue_pace import pace

log = logging.getLogger(__name__)

TICK_S = 60.0
BACKOFF_S = 120.0

# ── THIS LOOP YIELDS TO EVERYTHING ELSE ──────────────────────────────
#
# It is the lowest-priority consumer of the shared venue gateway: no
# latency requirement, no capital, no decision waiting on it. When the
# venue refuses reads, the right response is for THIS loop to get out
# of the way, not to keep claiming gaps the mirror needs.
#
# Measured 2026-09-20 19:37Z: 33 of 53 reads came back 429 at a 0.4s
# base pacing. The base is raised and a 429 in one tick multiplies the
# next tick's pacing and halves its budget, recovering a step at a time
# once reads succeed again. The alternative -- pushing through -- costs
# the mirror its gaps AND biases this dataset's readable subset toward
# quiet hours, which is the worse of the two failures.
READ_PACING_BASE_S = 1.0
READ_PACING_MAX_S = 8.0
BACKOFF_GROWTH = 2.0
BACKOFF_RECOVERY = 0.75
# One tick's share of the bucket, plus a little slack so a tick can
# finish its share when an earlier one fell short. NOT the bucket's
# whole slice: that was V1's burst.
MAX_READS_PER_TICK = sc.MAX_MARKETS_PER_TICK + 2
MAX_FOLLOWUP_READS = 6
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


def _read_book(slug: str, pacing: float = READ_PACING_BASE_S) -> dict:
    """One paced public book read, off the event loop. Never raises."""
    pace(pacing)
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
    """Eligible MARKETS, one entry each. NOT one per leg.

    The venue's book endpoint takes a market slug and knows nothing
    about sides, so the yes and no legs of a market would issue two
    identical requests for one payload -- double the venue load for no
    extra information, which is what V1 did. The market is sampled
    once; its row carries the leg the returned book actually belongs
    to, and the complement stays absent by name rather than being
    derived from it.

    Deduplication keeps the FIRST leg by a stable sort on the venue's
    own side string, so which leg represents a market does not depend
    on row order from the database.
    """
    rows = await pool.fetch(PREMAP_SQL, str(int(sc.PREMAP_FRESH_S)))
    by_market = {}
    for r in rows:
        ok, _why = sc.eligible(dict(r))
        if not ok:
            continue
        slug = r["market_slug"]
        # THE VENUE'S OWN SPORT AND LEAGUE, MAPPED. Every row written
        # before this carried sport = NOT_IDENTIFIED, because the
        # subject was built here without ever calling the mapper --
        # exactly the defect the sibling lane was fixed for on
        # 2026-09-20, reintroduced in a new worker. The raw strings
        # travel alongside so a row can be re-derived when the mapping
        # changes, and it will: the venue adds leagues.
        cls = sportmap.classify(sports_type=r["sports_type"],
                                team_league=r["team_league"])
        cand = {
            "identifier": slug, "symbol": slug, "marketId": slug,
            "eventId": r["event_slug"], "outcomeLeg": r["side_norm"],
            "kind": r["kind"],
            "sport": (cls["SPORT"] if cls["SPORT"] != sc.NOT_IDENTIFIED
                      else None),
            "league": (cls["LEAGUE"] if cls["LEAGUE"] != sc.NOT_IDENTIFIED
                       else None),
            "sportSourceRaw": r["sports_type"],
            "leagueSourceRaw": r["team_league"],
            "sportUnresolvedReason": cls["UNRESOLVED_MAPPING_REASON"],
            "gameStart": r["game_start"],
            "legIdentifier": r["identifier"],
        }
        prior = by_market.get(slug)
        if prior is None or str(cand["outcomeLeg"]) < str(
                prior["outcomeLeg"]):
            by_market[slug] = cand
    return list(by_market.values())


async def tick(pool, *, pacing: float = READ_PACING_BASE_S) -> dict:
    """One 60s cycle: this tick's share of the bucket's markets.

    The loop ticks every 60s and the sampling cadence is 300s, so five
    ticks fall inside one bucket and compute the same rotation cycle.
    The bucket's MEMBERSHIP is fixed by the cycle; which of its markets
    a given tick reads is that tick's share. V1 read the whole slice at
    the top of the bucket and idled for the other four ticks, putting
    40 requests through a shared gateway in ~16s -- ten of its first
    fourteen came back 429. Same markets, same bucket, a fifth of the
    instantaneous rate.

    The observation bucket is still the primary key, so a market read
    twice inside one bucket is discarded rather than duplicated.
    """
    at = datetime.now(tz=timezone.utc)
    bucket = sc.bucket_of(at)
    tick_i = sc.tick_index(at)
    stats = {"status": "ok", "cycle": sc.cycle_of(at),
             "bucket": bucket, "tick": tick_i, "sampled": True,
             "universe": sc.UNIVERSE_VERSION,
             "ruleSha": sc.RULE_SHA[:16],
             "rotationSlices": sc.ROTATION_SLICES,
             "eligible": 0, "inSlice": 0, "read": 0, "written": 0,
             "duplicateBucket": 0, "unreadable": 0, "failures": 0,
             "rateLimited": 0, "pacingS": round(pacing, 2),
             "sliceTruncated": False, "followups": 0, "reads": 0}

    try:
        cands = await _candidates(pool)
    except Exception as exc:                                   # noqa: BLE001
        stats["status"] = "premap_unreadable"
        stats["lastError"] = "%s: %s" % (type(exc).__name__, exc)
        return stats
    sel = sc.select(cands, at=at, tick=tick_i)
    stats["eligible"] = sel["CANDIDATES_ELIGIBLE"]
    stats["inSlice"] = sel["CANDIDATES_IN_SLICE"]
    stats["bucketShare"] = sel["BUCKET_SHARE"]
    stats["sliceTruncated"] = sel["SLICE_TRUNCATED"]
    stats["sliceTruncatedBy"] = sel["SLICE_TRUNCATED_BY"]
    # A CAP THAT BINDS EVERY PASS IS NOT A ROTATION. V1's did, on every
    # row it ever wrote, and the loop said nothing. This is loud.
    if sel["SLICE_TRUNCATED"]:
        stats["status"] = "slice_truncated"
        log.error("bettor_state: slice %d holds %d markets against a cap "
                  "of %d -- the cap is binding, so the rotation is "
                  "drawing a fixed panel and %d markets are never "
                  "sampled. The rule needs a version bump.",
                  sel["CYCLE"], sel["CANDIDATES_IN_SLICE"],
                  sc.MAX_MARKETS_PER_CYCLE, sel["SLICE_TRUNCATED_BY"])

    misses = 0
    budget = max(1, int(MAX_READS_PER_TICK * min(
        1.0, READ_PACING_BASE_S / pacing)))
    stats["budget"] = budget
    for subject in sel["SELECTED"]:
        if budget <= 0:
            stats["status"] = "read_budget_exhausted"
            break
        budget -= 1
        stats["reads"] += 1

        request_at = datetime.now(tz=timezone.utc)
        book = await asyncio.to_thread(_read_book, subject["symbol"],
                                       pacing)
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
            # RATE LIMITING IS ABOUT US, NOT THE MARKET. It is counted
            # apart from a venue that published no book, because the
            # two call for opposite responses: one means slow down, the
            # other is a fact about the market and means carry on.
            if "RateLimit" in str(book.get("error") or ""):
                stats["rateLimited"] += 1

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
    # A TICK THAT IS BEING RATE LIMITED DOES NOT THEN GO AND READ MORE.
    # Follow-ups are skipped entirely when the sampling pass hit the
    # venue's limit; a missing follow-up leaves a visible gap, while
    # pushing through a 429 makes the next sampling read fail too.
    follow_budget = 0 if stats["rateLimited"] else min(
        MAX_FOLLOWUP_READS, max(0, budget))
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
            book = await asyncio.to_thread(_read_book, d["market_id"],
                                           pacing)
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

    pacing = READ_PACING_BASE_S
    while True:
        started = time.monotonic()
        try:
            stats = await tick(pool, pacing=pacing)
            # ADAPTIVE, AND ASYMMETRIC ON PURPOSE. A refused read
            # multiplies the gap; a clean tick only walks it back a
            # step. Backing off fast and recovering slowly is what
            # keeps this loop out of the mirror's way, and the cost of
            # being too slow here is a smaller dataset, while the cost
            # of being too fast is the mirror's latency.
            if stats.get("rateLimited"):
                pacing = min(READ_PACING_MAX_S, pacing * BACKOFF_GROWTH)
                log.warning(
                    "bettor_state: %d of %d reads refused by the venue; "
                    "pacing now %.2fs. The readable subset of this "
                    "dataset is NOT missing-at-random while this "
                    "persists -- see READABILITY_IS_NOT_MISSING_AT_RANDOM",
                    stats["rateLimited"], stats.get("read", 0), pacing)
            elif stats.get("read"):
                pacing = max(READ_PACING_BASE_S, pacing * BACKOFF_RECOVERY)
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
