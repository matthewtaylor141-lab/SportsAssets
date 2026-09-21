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
# Bumped whenever the pacing behaviour changes, so a coverage figure
# can be attributed to the regime that produced it rather than pooled
# across regimes that behaved differently.
PACING_VERSION = "BETTOR_CAPTURE_PACING_V4_ALLOWANCE_RESTORED"
# V3 -> V4 restores the ORIGINAL REQUEST ALLOWANCE (floor 1, not 2)
# and orders the due queue ON-TIME FIRST. V4 issues FEWER requests than
# V3 at backoff, never more: at pacing >= 5.063 it sends one read per
# tick where V3 sent two. Coverage must not be pooled across V3 and V4.
#
# V2 -> V3 changed the FOLLOW-UP ALLOCATION only: the horizon order now
# rotates on service opportunities and each horizon is capped at its
# share of the tick's follow-up budget. The gateway pacing arithmetic
# -- base, max, growth, recovery and the per-tick read ceiling -- is
# byte-for-byte the same, and V3 issues no more requests per tick than
# V2 did. Coverage figures must not be pooled across the two versions
# because WHICH horizon a read went to changed, not how many were sent.
_FU_SERVICE_OPS = 0
_TICK_SEQ = 0

# Bumped when WHICH observations enter the sample changes. V4's
# scheduler decided which follow-ups to serve; this decides how much
# work is created in the first place, which is a property of the
# sampling frame and not of the scheduler.
# V2 MEASURES THE SAME THING AND ENFORCES NOTHING. V1's gate shut on
# all seven of its production ticks and wrote zero observations; its
# threshold compared a queue depth against a per-tick rate. The 27
# follow-up reads taken under V1 must stay distinguishable from
# everything after, because they were taken while intake was stopped.
ADMISSION_VERSION = "BETTOR_ADMISSION_V2_MEASURE_ONLY"
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


def _tick_id(at, tick_index) -> str:
    """Deterministic, so a retried tick cannot double-count itself."""
    import hashlib
    raw = "%s|%s|%s" % (sc.UNIVERSE_VERSION, at.isoformat(), tick_index)
    return "btk_" + hashlib.sha256(raw.encode()).hexdigest()[:40]


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
             "pacingVersion": PACING_VERSION,
             "obsSkippedBudget": 0, "obsSkippedAbandon": 0,
             "unreadableOther": 0, "fuDue": 0, "fuAttempted": 0,
             "fuSkippedBudget": 0, "fuOnTime": 0, "fuLate": 0,
             "fuSelected": 0,
             "obsSkippedAdmission": 0, "backlogTasks": 0,
             "admitCap": None, "admissionSaturated": False,
             "admissionVersion": ADMISSION_VERSION,
             # None, not 60: a tick with no follow-up budget had no
             # rotation head at all, and the two must not collapse.
             "fuRotationHead": None, "fuPerHorizonCap": None,
             "fuFailed": 0,
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
    # ── THE TICK'S READ BUDGET, SPLIT BEFORE EITHER PASS RUNS ────────
    #
    # W1 DIAGNOSIS (2026-09-20, research-sql run on 6612731). Follow-ups
    # were funded from whatever the sampling pass LEFT OVER, and the
    # sampling pass almost always exhausted it:
    #
    #   18 ticks where sampling exhausted budget -> 0.28 follow-ups/tick
    #    7 ticks with budget left over           -> 2.14 follow-ups/tick
    #
    # and all five follow-ups on starved ticks came from the rate-limit
    # floor max(1, 0//2) = 1, an accident of that branch rather than a
    # design. Zero reads were ON_TIME at any horizon.
    #
    # THE ORDERING HYPOTHESIS WAS REFUTED IN W1, CORRECTLY, AND THE
    # CONDITION IT DEPENDED ON HAS SINCE CHANGED. In W1 reads were late
    # because they waited for a rare spare-budget tick, not because the
    # queue was sorted oldest-first: reordering a queue that is never
    # serviced changes nothing. W2 funded the queue and W3 spread it
    # across horizons, so the queue IS serviced now -- and W3 measured
    # reads taken at a median 538-549s past their horizon against a
    # 600s expiry, with 1 read in 122 inside its band. Ordering binds
    # once servicing exists. It did not bind then; it does now.
    #
    # THE BUDGET FLOOR IS RESTORED TO ONE. max(2, ...) issued TWO reads
    # per tick where the original issued one, at pacing >= 5.063 --
    # a 2x increase in request rate exactly when the venue is refusing.
    # W3's pacing reached 5.695, so this was live.
    #
    # RESTORING IT IS NOT A ONE-CONSTANT CHANGE. The floor was masking
    # an over-spend: at total_budget = 1,
    #     fu_reserve = max(1, 1 // 2) = 1
    #     budget     = max(1, 1 - 1)  = 1
    # which is TWO reads against a budget of one. The single-read tick
    # must alternate instead, and the assertion below is what stops
    # this from being re-introduced quietly.
    #
    # ALTERNATION IS SAFE ONLY BECAUSE THE ROTATION ADVANCES ON SERVICE
    # OPPORTUNITIES. Gating follow-ups to alternate ticks is exactly
    # the parity that aliases against a tick-keyed rotation offset and
    # leaves two horizons permanently unreachable. _FU_SERVICE_OPS
    # advances once per serving tick, so it cannot alias.
    global _TICK_SEQ
    _TICK_SEQ += 1
    total_budget = max(1, int(MAX_READS_PER_TICK * min(
        1.0, READ_PACING_BASE_S / pacing)))
    if total_budget <= 1:
        fu_reserve = 1 if (_TICK_SEQ % 2) else 0
        budget = total_budget - fu_reserve
    else:
        fu_reserve = max(1, total_budget // 2)
        budget = max(1, total_budget - fu_reserve)
    assert fu_reserve + budget <= total_budget, (
        "the split must never exceed the tick budget", total_budget,
        fu_reserve, budget)
    stats["budget"] = budget
    stats["fuReserve"] = fu_reserve
    stats["totalBudget"] = total_budget
    # ── CAPACITY-AWARE ADMISSION ─────────────────────────────────────
    #
    # THE DEFECT EVERY WINDOW SO FAR HAS MEASURED AND NONE HAS FIXED.
    # Admitting an observation is not free: it creates one follow-up
    # task PER HORIZON, four of them, each of which must be serviced
    # inside its own recovery window or it expires unread. Measured in
    # W3 (run 180, 60-minute flows):
    #
    #     intake                         1.43 observations/min
    #     follow-up tasks made eligible  5.85 tasks/min  (intake x 4)
    #     attempts completed             2.03 tasks/min
    #     tasks expiring unread          4.02 tasks/min
    #
    # Sixty-five percent of the work this collector created for itself
    # was thrown away. Scheduling repairs cannot fix that: rotation and
    # ordering decide WHICH tasks are served, never HOW MANY. The
    # collector was admitting work at roughly three times the rate it
    # could service, and no amount of fairness between horizons makes
    # an unservable task servable.
    #
    # THE RULE. Sustainable intake is the follow-up capacity divided by
    # the number of horizons each observation obliges us to read. The
    # reserved follow-up share IS that capacity, measured in the same
    # unit, on the same tick.
    #
    # THE BACKLOG BRAKE. Capacity already committed to outstanding work
    # is not available for new work. When the queue is deeper than one
    # tick's reserve can clear, intake stops entirely for this tick and
    # the whole budget goes to draining it. Admitting into a saturated
    # queue does not collect more data; it converts reads that would
    # have completed on time into reads that expire.
    #
    # THIS IS A SAMPLING CHANGE AND IT IS VERSIONED. Which markets
    # enter the sample is what the frozen rule governs, so this carries
    # its own ADMISSION_VERSION and the rows it produces are
    # distinguishable from every row admitted before it.
    horizons_per_obs = max(1, len(sc.HORIZONS_OBSERVABLE_S))
    sustainable = fu_reserve // horizons_per_obs
    backlog = 0
    try:
        for _h in sc.HORIZONS_OBSERVABLE_S:
            backlog += await sstore.mids_outstanding(_h, pool=pool)
    except Exception:                                          # noqa: BLE001
        backlog = 0
    # MEASURED IN PRODUCTION, NOT ENFORCED. 2026-09-21 12:07:34Z-12:14Z,
    # seven ticks, and the gate closed on every one of them:
    #
    #     saturated_ticks 7/7   admit_cap 0.00   obs_written 0
    #     ON_TIME 3/27 = 11.1%  against V4's 246/796 = 30.9%
    #
    # Intake stopped completely and timing got WORSE. The replay's
    # 254/256 on time did not reproduce, and the reason is a unit error
    # in the line below as it was first written:
    #
    #     saturated = backlog > max(1, fu_reserve)
    #
    # `backlog` is a LEVEL -- tasks due right now, summed across all four
    # horizons. `fu_reserve` is a FLOW -- reads available in ONE tick,
    # which is 5. Comparing them asks "is there more than one tick's
    # work waiting", and since every admitted observation owes reads at
    # 60s, 300s, 900s and 3600s, any steady state holds more than five
    # due at once. So the gate latched shut on the first tick and had no
    # path back open: with intake at zero the only thing that could
    # drain it was the pre-existing commitment, and that takes an hour.
    #
    # This is the same level-versus-flow confusion I named in the W3
    # write-up -- summing a level across ticks double-counts the same
    # waiting task -- and then encoded into the repair for it.
    #
    # The threshold has to compare time-to-drain against the deadline,
    # not a queue depth against a per-tick rate. I am not guessing at
    # that here: the replay harness is what establishes such a number,
    # and a second unverified threshold is how this cycle repeats.
    #
    # So the measurement stays and the enforcement goes. backlog_tasks
    # and admission_saturated keep recording the oversubscription that
    # is genuinely there -- 15 to 26 tasks due against 5 reads a tick --
    # while the allowance returns to V4, which is the only configuration
    # this system has measured doing better.
    saturated = backlog > max(1, fu_reserve)
    admit_cap = budget
    stats["admissionVersion"] = ADMISSION_VERSION
    stats["backlogTasks"] = backlog
    stats["admitCap"] = admit_cap
    stats["admissionSaturated"] = saturated
    stats["admissionEnforced"] = False
    stats["admissionSustainableWas"] = sustainable
    # THE CAP IS APPLIED AS ITS OWN LIMIT, NOT BY SHRINKING `budget`.
    # Lowering budget would make the sampling loop below report every
    # declined market as `read_budget_exhausted` /
    # `obs_skipped_budget`, which is the LOSS counter. Admission is not
    # a loss -- a declined market stays in the rotation and comes round
    # again -- and conflating the two would hide the throttle inside a
    # figure I have been reporting as coverage failure since W1.
    admitted_budget = min(budget, admit_cap)

    # SCHEDULED IS THE DENOMINATOR, recorded before any read. Coverage
    # computed against what was attempted can only ever be 100%.
    #
    # ADMISSION-CAPPED WORK IS NOT A COVERAGE FAILURE. A market the
    # rotation offered and admission declined was never attempted and
    # never lost -- it stays in the rotation and comes round again. It
    # is counted apart from obs_skipped_budget, which IS a loss.
    stats["scheduled"] = len(sel["SELECTED"])
    abandoned = False
    for idx, subject in enumerate(sel["SELECTED"]):
        # ADMISSION FIRST, AND COUNTED SEPARATELY. Declined work is
        # deferred; exhausted budget is lost. Two counters, two causes.
        if admitted_budget <= 0:
            stats["status"] = ("admission_capped" if admit_cap < budget
                               else "read_budget_exhausted")
            remaining = len(sel["SELECTED"]) - idx
            if admit_cap < budget:
                stats["obsSkippedAdmission"] = remaining
            else:
                stats["obsSkippedBudget"] = remaining
            break
        admitted_budget -= 1
        if budget <= 0:
            stats["status"] = "read_budget_exhausted"
            stats["obsSkippedBudget"] = len(sel["SELECTED"]) - idx
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
            else:
                stats["unreadableOther"] += 1
        else:
            stats["readable"] = stats.get("readable", 0) + 1

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
            stats["obsSkippedAbandon"] = len(sel["SELECTED"]) - idx - 1
            abandoned = True
            break

    # ── pass 2: the declared horizons ────────────────────────────────
    #
    # FOLLOW-UPS ARE NOT OPTIONAL. Skipping them wholesale on any
    # rate-limited tick cost almost all of them: 21 mid rows against
    # 205 observations, 3.4% coverage at the 60s horizon. A T0 with no
    # follow-up is a state observation with no outcome, which is half a
    # dataset.
    #
    # So a refused tick now HALVES the follow-up budget instead of
    # zeroing it, and the outcome side keeps a floor of one read. The
    # sampling pass can be made up on the next rotation; a horizon that
    # passes unobserved cannot be.
    # The reserve, plus anything the sampling pass did not need. A
    # refused tick still halves it -- backing off from the venue is
    # preserved -- but it can no longer fall to zero because sampling
    # took everything.
    follow_budget = min(MAX_FOLLOWUP_READS,
                        fu_reserve + max(0, budget))
    if stats["rateLimited"]:
        follow_budget = max(1, follow_budget // 2)
    # ── W3: WHICH HORIZON GOES FIRST, AND HOW MUCH IT MAY TAKE ───────
    #
    # A fixed order meant the first horizon in the tuple took the whole
    # follow-up budget on every tick and the other three were starved
    # deterministically -- not by capacity. That is the W1 shape: reads
    # at 60s, zero at 300s, 900s and 3600s.
    #
    # ROTATE THE START, AND ON SERVICE OPPORTUNITIES, NOT ON THE CLOCK.
    # Keying the rotation to a tick counter aliases against any rule
    # that makes follow-ups run on only some ticks: offline, with the
    # budget at one read per tick, follow-ups run on odd ticks only, so
    # a tick-keyed offset uses only odd values and two of the four
    # horizons are NEVER at the head. A counter that advances once per
    # tick that can actually serve something cannot alias, because
    # consecutive serving ticks take consecutive offsets.
    #
    # AND TO BE EXACT ABOUT WHAT THAT DEFECT WAS: it was found in the
    # W3 CANDIDATE, offline, before deployment. It was never live. The
    # deployed budget floors total at 2 and follow_budget at 1, so
    # every production tick serves follow-ups and a tick-keyed offset
    # would not have aliased here. This is the correct-by-construction
    # choice, not the repair of an observed production fault -- the
    # observed production fault is the FIXED ORDER below it replaces,
    # which starved three horizons at every budget.
    #
    # THE CAP is what makes the rotation matter. Without it the head
    # horizon still drains the budget before the next one is reached.
    global _FU_SERVICE_OPS
    order = list(sc.HORIZONS_OBSERVABLE_S)
    if follow_budget > 0:
        k = _FU_SERVICE_OPS % len(order)
        order = order[k:] + order[:k]
        _FU_SERVICE_OPS += 1
        stats["fuRotationHead"] = order[0]
    per_horizon_cap = max(1, follow_budget // len(order))
    stats["fuPerHorizonCap"] = per_horizon_cap

    for horizon in order:
        if follow_budget <= 0:
            break
        taken_this_horizon = 0
        try:
            # DEMAND IS COUNTED BEFORE THE LIMIT. Previously fu_due was
            # len(due) AFTER mids_due applied the budget as a LIMIT, so
            # fu_due could never exceed the budget and FU_DUE ==
            # FU_ATTEMPTED was a tautology rather than a
            # reconciliation. True demand had never been recorded.
            outstanding = await sstore.mids_outstanding(horizon,
                                                        pool=pool)
            due = await sstore.mids_due(
                horizon, limit=min(follow_budget, per_horizon_cap),
                pool=pool)
        except Exception:                                      # noqa: BLE001
            break
        stats["fuDue"] += outstanding
        stats["fuSelected"] += len(due)
        for j, d in enumerate(due):
            if follow_budget <= 0 or taken_this_horizon >= per_horizon_cap:
                stats["fuSkippedBudget"] += len(due) - j
                break
            follow_budget -= 1
            taken_this_horizon += 1
            stats["reads"] += 1
            stats["fuAttempted"] += 1
            book = await asyncio.to_thread(_read_book, d["market_id"],
                                           pacing)
            read_at = datetime.now(tz=timezone.utc)
            mid = _mid_of(book)
            try:
                rec = sc.mid_observation(
                    d["observation_id"], horizon_s=horizon,
                    observed_at=d["observed_at"], read_at=read_at,
                    mid=mid)
                await sstore.record_mid(rec, pool=pool)
                stats["followups"] += 1
                # ON TIME AND LATE ARE COUNTED APART. A recovery read is
                # not a 60-second outcome, and a single "followups"
                # counter would let it read as one.
                if rec.get("TIMING_CLASS") == sc.TIMING_ON_TIME:
                    stats["fuOnTime"] += 1
                else:
                    stats["fuLate"] += 1
            except Exception:                                  # noqa: BLE001
                stats["failures"] += 1
                stats["fuFailed"] += 1

    # ── the tick's own account, written before it ends ───────────────
    #
    # SCHEDULED minus ATTEMPTED is the loss that leaves no observation
    # row. Recorded here or it is not recorded anywhere.
    sched = stats.get("scheduled", 0)
    att = stats.get("read", 0)
    await sstore.record_tick({
        "TICK_ID": _tick_id(at, tick_i),
        "TICK_AT": at,
        "UNIVERSE_VERSION": sc.UNIVERSE_VERSION,
        "RULE_SHA": sc.RULE_SHA,
        "PACING_VERSION": PACING_VERSION,
        "PACING_S": round(pacing, 3),
        "SELECTION_CYCLE": sel.get("CYCLE"),
        "TICK_INDEX": tick_i,
        "OBS_SCHEDULED": sched,
        "OBS_ATTEMPTED": att,
        "OBS_SKIPPED_BUDGET": stats["obsSkippedBudget"],
        "OBS_SKIPPED_ABANDON": stats["obsSkippedAbandon"],
        "OBS_READABLE": stats.get("readable", 0),
        "OBS_RATE_LIMITED": stats["rateLimited"],
        "OBS_UNREADABLE_OTHER": stats["unreadableOther"],
        "OBS_WRITTEN": stats["written"],
        "OBS_DUPLICATE_BUCKET": stats["duplicateBucket"],
        "FU_DUE": stats["fuDue"],
        "FU_SELECTED": stats["fuSelected"],
        # None when the tick had no follow-up budget: no horizon led.
        "OBS_SKIPPED_ADMISSION": stats.get("obsSkippedAdmission", 0),
        "BACKLOG_TASKS": stats.get("backlogTasks"),
        "ADMIT_CAP": stats.get("admitCap"),
        "ADMISSION_SATURATED": stats.get("admissionSaturated"),
        "ADMISSION_VERSION": stats.get("admissionVersion"),
        "FU_ROTATION_HEAD": stats.get("fuRotationHead"),
        "FU_PER_HORIZON_CAP": stats.get("fuPerHorizonCap"),
        "FU_ATTEMPTED": stats["fuAttempted"],
        "FU_SKIPPED_BUDGET": stats["fuSkippedBudget"],
        "FU_ON_TIME": stats["fuOnTime"],
        "FU_LATE": stats["fuLate"],
        "FU_FAILED": stats["fuFailed"],
        "STATUS": stats["status"],
        "OBS_NEVER_ATTEMPTED": max(0, sched - att),
    }, pool=pool)
    stats["neverAttempted"] = max(0, sched - att)
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
