"""LIVE trading beta — real-money orders (REAL MONEY).

Two venues, picked by which credentials are configured:
  * Polymarket US (PMUS_KEY_ID/PMUS_SECRET_KEY) — the regulated exchange
    behind the US mobile app. Separate order books from the global CLOB the
    source whale trades on, so each copy is mapped to a verified US market
    first (see pmus.resolve_market); unmappable trades are recorded, not
    guessed. This is the supported path for US accounts.
  * Global CLOB (PM_PRIVATE_KEY) — non-US accounts only.

Trigger: identical to the paper AI TRADER (fresh source-whale BUY), so live
fills and paper fills are directly comparable per trade.

Safety model (every layer must pass, in order):
  1. LIVE_TRADING_ENABLED + credentials present    (off by default)
  2. Kill switch not engaged (admin pause)
  3. Buy-only, source-whale-only, fresh detections only
  4. Price protection: FOK LIMIT at his_price + max_slippage — fills at our
     price or not at all; no market orders, no resting orders, no chasing
     (on the US venue the order is additionally preview-verified first)
  5. Triple caps: per-fill / daily / total bankroll (SQL-enforced)
Every order and its raw API response is stored in live_orders (audit trail).
Settlement runs through the platform's resolution pipeline.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
import time

import asyncpg
from datetime import datetime, timezone
from typing import Any

from .config import settings
from .db import get_pool

log = logging.getLogger(__name__)

_client = None
_client_lock = asyncio.Lock()
PAUSE_KEY = "live_trading_paused"

# Executor-side concurrency cap (owner approval 2026-08-10, reaction-time
# work): copy execution used to run INSIDE copy_probe's 4-slot probe
# semaphore, so a whale burst serialized later fills' probes — and their
# reaction stamps — behind earlier fills' full map+order cycles.
# Execution is now its own task per fresh detection (see execute_copy);
# this semaphore is what still keeps a burst from firing unbounded
# concurrent mapping/preview/create calls at the venue, which was
# Cloudflare-throttled once already (polymarket_us.py page pacing note).
# COPY CONCURRENCY. Four slots, each held through mapping plus three
# sequential venue round trips, was a throughput cap masquerading as a
# safety control — it is neither a per-order cap, a daily cap nor a dup
# guard, and the only thing it bounded was how fast we could react.
#
# It is env-tunable so the ceiling can be raised against MEASURED queue
# wait (see _QUEUE_STATS / the QUEUE probe line) rather than a guess.
# The default is deliberately unchanged until that measurement exists:
# raising it means more orders can clear the day-room check before any
# of them lands, and that check reads a running total. Widening
# throughput and widening a money gate must not happen in one step.
_COPY_CONCURRENCY = max(1, int(os.environ.get("LIVE_COPY_CONCURRENCY", "4")))
_COPY_SEM = asyncio.Semaphore(_COPY_CONCURRENCY)
# Rolling queue-wait, surfaced on the heartbeat. n/total/max, never
# reset, so a burst hours ago still shows in the max.
_QUEUE_STATS: dict = {"n": 0, "total_s": 0.0, "max_s": 0.0}
# Held while the FIRST real fill is unverified — see the first-fill gate
# in maybe_execute.
_FIRST_FILL_LOCK = asyncio.Lock()

# SHORT PROBATION (owner approval 2026-08-26).
#
# The BUY_SHORT branch is banned by default and LIVE_ALLOW_SHORT=on
# reopens it. That flag was all-or-nothing: flipping it lets four short
# orders reach the venue before the first verdict returns, on the one
# class whose end-to-end behaviour has never been proved.
#
# This does not change what the flag means or lower any gate. It makes
# the flag SAFE TO USE: while shorts are unproven they go one at a time,
# each settled by the venue's own netPosition sign, and a single
# confirmed mismatch shuts the branch again with nobody watching.
#
# Why a separate tally from side_echo_last: that key mixes every intent,
# so one verified LONG would open the short gate on evidence about a
# different question — the recurring defect in this codebase.
_SHORT_LOCK = asyncio.Lock()
SHORT_PROOF_KEY = "short_side_proof"
SHORT_PROBATION_N = int(os.environ.get("LIVE_SHORT_PROBATION_N", "3"))


async def execute_copy(payload: dict) -> str | None:
    """Fresh-detection execution entry point, spawned by the ingestion
    pipeline ALONGSIDE (not inside) the measurement probe.

    Deliberately no 120s probe gate here: MAX_REACTION_S protects the
    probe cohort's measurement integrity, but for EXECUTION the
    pipeline's 600s fresh gate plus the FOK at his+2% already bound
    staleness — a 2-10 minute detection is a copy the sleeve previously
    forfeited to the 10-minute sweep for no risk reason.

    Returns the exit-path reason for a SELL (see EXIT_PENDING_REASONS),
    None otherwise. The BUY path's return is unchanged and unread; every
    existing caller ignores the value.
    """
    try:
        # HIS SELL IS A SIGNAL TOO (owner order 2026-08-25). This path
        # returned early on anything that was not a BUY, so his exits
        # were discarded at the door — we copied entries and then held
        # to resolution while he took the move. mirror_exit carries its
        # own gates; it is not a bypass of this one.
        #
        # DISPATCHED ABOVE THE copy_probe_enabled RETURN, which used to
        # sit first. That is not a loosened gate: mirror_exit's own
        # first check is `not copy_probe_enabled or copy_halted()`, a
        # strict superset, and it refuses before touching an order.
        # What changes is that the refusal now has a NAME the caller can
        # read ("mx_halted") instead of a bare return, so the exits
        # refused while the sleeve is off are held rather than erased.
        if payload.get("side") == "SELL":
            return await mirror_exit(payload)
        # copy_probe_enabled was the fresh-copy path's de facto master
        # switch while execution lived inside the probe; it stays one
        # (same-day review finding — an operator flipping it expects
        # copies to stop, and silently un-braking a kill dial is worse
        # than the naming being imperfect).
        if not settings().copy_probe_enabled:
            return None
        if payload.get("side") != "BUY":
            return None
        # QUEUE WAIT IS OUR OWN LATENCY, AND IT WAS INVISIBLE.
        #
        # reaction is stamped INSIDE the semaphore, which is right — the
        # staleness gate must judge the moment the order fires. But it
        # means a copy rejected as "stale-signal" may have been fresh on
        # arrival and aged entirely in OUR queue: four slots, each held
        # through mapping plus three sequential venue round trips
        # (side_ask, preview, create). Under a burst from the highest-
        # flow whale on the roster, rows age past a 15s cap waiting for
        # a slot we never widened.
        #
        # Nothing distinguished "he was slow to reach us" from "we sat
        # on it". You cannot cut what you cannot see, so the wait is
        # measured before anything else changes.
        _queued_at = time.monotonic()
        async with _COPY_SEM:
            queue_wait = round(time.monotonic() - _queued_at, 3)
            if queue_wait > float(
                    os.environ.get("COPY_QUEUE_WARN_S", "1.0")):
                log.warning("COPY-QUEUE waited %.2fs for a slot (%d in "
                            "flight) — this counts against the staleness "
                            "cap and it is our delay, not his",
                            queue_wait, _COPY_CONCURRENCY)
            _QUEUE_STATS["n"] += 1
            _QUEUE_STATS["total_s"] += queue_wait
            if queue_wait > _QUEUE_STATS["max_s"]:
                _QUEUE_STATS["max_s"] = queue_wait
            # Reaction is stamped and the ceiling judged INSIDE the
            # semaphore (review 2026-08-10): a burst can queue a task
            # here for minutes, and both the recorded latency and the
            # staleness gate must describe the moment the order actually
            # fires, not the moment the task was spawned.
            reaction = None
            ts = payload.get("ts")
            if ts:
                try:
                    fill_dt = datetime.fromisoformat(
                        str(ts).replace("Z", "+00:00"))
                    reaction = round(
                        (datetime.now(tz=timezone.utc) - fill_dt)
                        .total_seconds(), 3)
                except ValueError:
                    pass
            # Staleness ceiling: the probe's 120s gate is gone by design
            # (2-10 min detections are copies we forfeited for no risk
            # reason), but nothing may fire tens of minutes after his
            # fill — the sweep is the venue for anything older.
            max_age = float(os.environ.get("COPY_EXEC_MAX_AGE_S", "900"))
            if reaction is not None and reaction > max_age:
                return
            await maybe_execute(payload, reaction)
    except Exception:  # noqa: BLE001 — execution must never disturb ingestion
        log.exception("copy execution failed for trade %s", payload.get("id"))
        # AN EXCEPTION ON THE SELL PATH IS PENDING, NOT SILENCE
        # (2026-08-26, adversarial workflow). This returned None for
        # every side, and whale_exits reads a non-pending return as
        # "handled" and advances its snapshot -- so a transient DB blip
        # or the sweep's 60s wait_for cancellation didn't delay an exit,
        # it ERASED it: next cycle's baseline already agreed the whale
        # was out, and we held against him to resolution.
        #
        # The old comment here refused a retry because "an order may be
        # in flight and selling twice is worse than losing one exit".
        # That was true when written and is answered by machinery that
        # exists now: mirror_exit's atomic claim leaves a retried row in
        # 'exiting' (mx_already_claimed, itself a pending reason), a
        # cancellation mid-venue-call marks the row for reconciliation
        # and does NOT release it, and _reap_stale_exiting resolves the
        # stranded ones against the venue's own position. A retry
        # cannot double-sell; it can only re-enter a state machine that
        # already refuses duplicates.
        #
        # BUY-path exceptions keep returning None -- no caller reads it.
        if (payload.get("side") or "").upper() == "SELL":
            return _exit_done("mx_exception_pending",
                              whale=payload.get("whale_username"),
                              asset=str(payload.get("asset") or "")[:20])
    return None


# EMERGENCY HALT — CONFIRMED OVERSPEND ON PROBABLE WRONG SIDE
# (2026-08-25 00:50Z). The fill forensics deployed minutes earlier
# returned five rows where the venue took 1.15x-3.87x the authorized
# clip. The share count matched our request EXACTLY every time; the
# FILL PRICE did not:
#
#   req 1086sh @0.23 -> filled 1086sh @0.89   $249.78 -> $966.54
#   req  781sh @0.32 -> filled  781sh @0.6853 $249.92 -> $535.22
#   req  675sh @0.37 -> filled  675sh @0.65   $249.75 -> $438.75
#   req  555sh @0.45 -> filled  555sh @0.56   $249.75 -> $310.80
#   req  520sh @0.48 -> filled  520sh @0.55   $249.60 -> $286.00
#
# An IOC buy cannot fill above its limit. Each fill price sits near the
# COMPLEMENT of ours (1-0.32=0.68 vs 0.6853; 1-0.45=0.55 vs 0.56) —
# the signature of landing on the opposite side of the market. That is
# the 2026-08-23 wrong-side incident, still live, on a whale the side
# echo had reported ok.
#
# Two controls should have stopped this and did not:
#   - the per-fill clip bounds requested_usd, never what the venue takes
#   - submit_fok's preview cost guard compares against _order_cost(...,
#     default=expected_cost), so an unreadable preview compares equal
#     and passes. A money guard that fails OPEN.
#
# HALT LIFTED (owner decision 2026-08-25 01:10Z: "I want the system
# live while you fix the reporting"). The owner reads these rows as
# round-trips on ONE game — bought at 250, sold for profit, rebought
# with the same 250 — in which case the true stake never exceeded the
# clip and filled_usd is only a reporting artifact. That is his call to
# make, it is not yet disproven, and the receipts endpoint decides it.
#
# What makes live SAFE rather than a coin flip, on EITHER hypothesis:
# the preview cost guard now FAILS CLOSED (pmus._order_cost, same day).
# Before every buy the venue states its own cost, and submit_fok
# REFUSES when that exceeds ours beyond tolerance — or when the venue
# states no cost at all. So:
#   - owner right (reporting artifact): nothing changes, guard silent
#   - me right (venue charges the complement): the order is REFUSED
#     pre-trade instead of filling at 3.87x
# The GUARD, not the halt, is the real protection. That is what makes
# lifting this defensible while the cause is still open.
#
# LIVE_COPY_HALT=on re-arms the halt from Render with no deploy.
COPY_HALT_REASON = (
    "halted: overspend to 3.87x the authorized clip at complement "
    "prices (probable wrong side) — set LIVE_COPY_HALT=on to re-arm")


_HALT_ON_VALUES = {"on", "1", "true", "yes", "halt", "halted", "stop"}


# When the pre-trade ask guard went live. Breaches recorded BEFORE this
# were caught by the post-fill breaker because nothing stood in front of
# them; that hole is now closed, so they no longer justify keeping the
# sleeve down. Breaches AFTER it mean the guard itself failed, and those
# halt hard and stay halted.
ASK_GUARD_SINCE = "2026-08-25T02:20:00"


# A scale-out smaller than this is not worth crossing a spread for —
# the fee and the half-spread eat a 2% trim. Above it we follow him.
MIN_EXIT_FRAC = float(os.environ.get("LIVE_MIN_EXIT_FRAC", "0.10"))
# One venue call, on the copy hot path, to repair a token neither of our
# own caches knows. Short on purpose: a slow answer is worth less than
# the staleness it costs, and the pre-existing fallback is right there.
TOKEN_LOOKUP_TIMEOUT_S = float(
    os.environ.get("LIVE_TOKEN_LOOKUP_TIMEOUT_S", "2.0"))
# At or above this he is treated as fully out, and so are we. Below it
# the position stays open with the remainder still tracked.
FULL_EXIT_FRAC = float(os.environ.get("LIVE_FULL_EXIT_FRAC", "0.95"))
# Slippage bound for the venue's close-position call, which carries no
# limit price. 300 bips = 3%: wide enough that a real exit is not
# refused into a moving book, tight enough that an unbounded market
# order is never what we send.
EXIT_SLIPPAGE_BIPS = int(os.environ.get("LIVE_EXIT_SLIPPAGE_BIPS", "300"))


async def _release_exit_claim(pool, row_id: int) -> None:
    """Hand a claimed row back when the exit did not happen.

    Every refusal after the atomic claim must come through here. A row
    left in 'exiting' is invisible to the settlement sweep (which
    targets 'filled') and blocked from re-entry — a position stranded
    by a guard doing its job is still a stranded position.
    """
    try:
        await pool.execute(
            "UPDATE live_orders SET status='filled' "
            "WHERE id=$1 AND status='exiting'", row_id)
    except Exception:  # noqa: BLE001 — never mask the original refusal
        log.exception("could not release exit claim on row %s", row_id)


# ────────────────────────────────────────────────────────────────────
# EXIT CENSUS. Why the exit path did or did not act, attributed.
#
# classify_exit and mirror_exit refuse in TWENTY distinct ways and
# nineteen of them were silent. In production that makes "the whale
# never exited", "we hold nothing", "the venue says we hold nothing",
# "another task claimed it" and "the market type has no sibling token"
# a single indistinguishable event: nothing in the log.
#
# That is why "mirror_exit has never placed an order" has stood
# unexplained. It is not evidence the path is broken and not evidence
# it is fine — the question has been unanswerable, which is the same
# position the mapper was in before resolve_explain, and the same
# failure mode as a probe reading a column production does not write.
#
# Deliberately a COUNTER AND A RING, not a branch. Nothing here reads
# back into a decision: every helper returns None (or its argument) and
# is called on the way out of a path that had already decided. A
# diagnostic on the money path must be provably incapable of changing
# an order, and a reader must see that at a glance.
_EXIT_CENSUS: dict[str, int] = {}
_EXIT_RING: list[dict] = []
_EXIT_RING_MAX = 60


def _exit_stop(reason: str, **ctx) -> None:
    """Record why the exit path stopped, and return None."""
    _EXIT_CENSUS[reason] = _EXIT_CENSUS.get(reason, 0) + 1
    if ctx:
        import datetime as _d

        _EXIT_RING.append({
            "reason": reason,
            "at": _d.datetime.now(_d.timezone.utc).isoformat(
                timespec="seconds"),
            **{k: (v if isinstance(v, (int, float, bool))
                   else str(v)[:64]) for k, v in ctx.items()}})
        del _EXIT_RING[:-_EXIT_RING_MAX]
    return None


# Why the 95% gate refused, counted per reason. Same stance as the
# exit census: a diagnostic that cannot alter an order, so a reader can
# see at a glance that counting is all it does. Every refusal lands in
# exactly one bucket, so "why is nothing trading" is answerable from a
# probe line instead of a database session.
_GATE_CENSUS: dict[str, int] = {}


def _gate_census(reason: str) -> None:
    """Record why the edge gate refused a whale."""
    key = reason.split(":", 1)[0]
    _GATE_CENSUS[key] = _GATE_CENSUS.get(key, 0) + 1


# WHERE THE COPIES ACTUALLY GO (2026-08-31).
#
# Measured this session: rn1 puts up a median 1,061 playable new
# positions a day and we place 54. The other ~1,007 are invisible,
# because every funnel counter in this system reads live_orders — and
# maybe_execute has TWENTY-TWO returns BEFORE the first
# `INSERT INTO live_orders`. Exactly one of them (the 95% edge gate)
# recorded anything, into _GATE_CENSUS, which nothing ever read.
#
# So "why are we only copying 5% of him" has never had an answerable
# form. A rejected row can be counted; a copy refused before the row
# exists leaves nothing behind at all, and the pre-mapping stock
# counters people reach for instead (8,865 / 8,048 / 23,005) are
# live_orders rows too — the same blind spot wearing a different name.
#
# Same stance as _exit_census and _gate_census: in-memory, per reason
# and per whale, incapable of altering an order. A reader can see at a
# glance that counting is all it does.
_COPY_CENSUS: dict[str, int] = {}
_COPY_CENSUS_MAX = 400


def _copy_stop(reason: str, whale: str | None = None) -> None:
    """Count a copy refused BEFORE any live_orders row exists.

    Returns None so a bare `return` becomes `return _copy_stop(...)`
    without changing control flow. The bound is there because the key
    space includes the whale: an unbounded dict keyed on external data
    is a slow leak, and this process runs for days.
    """
    w = (whale or "?").lower()[:40]
    key = f"{reason}|{w}"
    if key not in _COPY_CENSUS and len(_COPY_CENSUS) >= _COPY_CENSUS_MAX:
        key = f"{reason}|(overflow)"
    _COPY_CENSUS[key] = _COPY_CENSUS.get(key, 0) + 1
    return None


def copy_census_snapshot() -> dict:
    """The census as a plain dict, newest counts, for the heartbeat."""
    return dict(sorted(_COPY_CENSUS.items(), key=lambda kv: -kv[1]))


def _exit_done(reason: str, **ctx) -> str:
    """Record why the exit path stopped, and RETURN THE REASON.

    Deliberately separate from _exit_stop even though the recording is
    identical. _exit_stop must keep returning None because classify_exit
    refuses with `return _exit_stop(...)` and its caller reads that
    return as "not an exit" — handing it a truthy string would turn
    every refusal into a classified exit on the money path.

    So the reason travels out of mirror_exit only, through its own
    helper, and nothing INSIDE this module branches on it. The one
    reader is the whale_exits worker, deciding whether to keep the
    position in its snapshot.
    """
    _exit_stop(reason, **ctx)
    return reason


# WHICH REFUSALS MEAN "STILL PENDING" RATHER THAN "SETTLED".
#
# The whale_exits worker advances its snapshot the moment it hands an
# exit to execute_copy, so a refused exit is erased and never seen
# again. Whether that is right depends entirely on WHY it was refused:
#
#   * a halted sleeve, an unreadable ledger, a venue that did not fill
#     -- he is out and we are still in. The exit is real and pending;
#     erasing it means holding to resolution against him, which is the
#     divergence this worker exists to close.
#
#   * we hold nothing, he was never copied, the position is below the
#     dust floor, it already sold -- there is nothing left to do. These
#     must NOT be retried: the worker would re-pin the asset and rediff
#     it every cycle forever.
#
# An ALLOWLIST, not a denylist, and the unknown case (a reason not
# listed, or None from the exception path) advances the snapshot --
# exactly today's behaviour. A reason nobody classified cannot create a
# retry loop, and an exception may have left an order in flight, where
# re-pinning would risk selling twice.
EXIT_PENDING_REASONS: frozenset[str] = frozenset({
    # A rest bid can fill inside its own window while the whale is
    # already exiting the same market; the row is still 'submitting'
    # when mirror_exit looks, so "no position" was being recorded and
    # the exit dropped forever (re-review 2026-09-01). Pending, so the
    # position lane pins the asset and re-offers it.
    "mx_entry_in_flight",
    # The in-flight lookup itself failed (round four): an entry may be
    # in flight and we cannot tell. Pending, never "no position".
    "mx_inflight_unreadable",
    # Kill switch or breaker. Clears by operator action.
    "mx_halted",
    "mx_paused",
    "mx_overspend_halt",
    # Infrastructure said "I don't know", which is not "nothing to do".
    "mx_exit_ledger_unreadable",
    # Another lane holds the claim on this slug right now.
    "mx_already_claimed",
    # Liquidity, not position. Both come back.
    "mx_no_bid_for_partial",
    "mx_venue_unfilled",
    # A fraction too small to buy a whole share. The position is real
    # and the exit is real; only the arithmetic came up short, and the
    # next observation measures a bigger cumulative fraction.
    "mx_exit_rounds_to_zero",
    # The dispatcher itself raised. Transient by presumption; a retry
    # re-enters mirror_exit's state machine, which refuses duplicates
    # (atomic claim, reconciliation marker, stale-exiting reaper), so
    # pinning is safe and silence was the only thing this could lose.
    "mx_exception_pending",
    # THE SUB-FLOOR TRIM, WHICH IS WHY DRIFT NEVER ACCUMULATED
    # (2026-08-26).
    #
    # closed_frac is a FLOW: one observation's delta over the position
    # as it stood immediately before that observation. The position
    # lane then advances its snapshot every 120s. So a whale trimming
    # 7% repeatedly has EVERY observation correctly refused as
    # sub-floor while the deficit is discarded each time -- he reaches
    # 23% and we still hold 100%, without one observation ever crossing
    # the floor. Simulated against the production constants:
    #
    #   7%/cycle x20  ->  whale 23.4%, we 100.0%, 20 refusals
    #   4%/cycle x20  ->  whale 44.2%, we 100.0%, 0 observations even
    #                     reported (MIN_SHRINK=0.05 drops them silently)
    #
    # mx_below_floor: 293 counts refusals, not the un-exited drift
    # behind them, and it cannot see the sub-5% band at all.
    #
    # Marking it PENDING makes whale_exits PIN the asset at its
    # pre-trim size instead of advancing past it, so the next cycle
    # measures pre-trim against a further-shrunken book and the
    # fraction GROWS until it genuinely crosses the floor. The floor
    # itself is unchanged -- this does not admit a smaller trim, it
    # stops the baseline running away from one. Same ratchet:
    #
    #   7%/cycle x20  ->  whale 23.4%, we 23.4%, 10 exits fired
    #   4%/cycle x20  ->  whale 44.2%, we 47.9%,  6 exits fired
    #
    # The starvation cost of pinning is already handled: the retry
    # cursor rotates, so a pinned asset cannot crowd out one that has
    # never had a turn.
    "mx_below_floor",
    # The whale is out and we are only partly out. The row still holds
    # shares and stays 'filled', so this is a real position to finish
    # exiting, not a settled one.
    "mx_partial_full_exit",
})


def exit_census() -> dict:
    """The census as a plain dict, newest refusals last."""
    return {"counts": dict(_EXIT_CENSUS), "recent": list(_EXIT_RING)}


def exit_census_lines(limit: int = 12) -> list[str]:
    """The recent ring as flat strings.

    NOT a convenience. The census has to travel from the worker process
    to a reader, and the only channel is the heartbeat, which
    /api/health/services sanitizes to a bounded depth of 3. A list of
    dicts inside detail.exit_census sits at exactly depth 3 and comes
    back as "<dict depth>" — the sanitizer refusing a payload, working
    as designed.
    #
    The fix is to flatten here rather than to raise the bound. That
    guard is on a PUBLIC endpoint and exists to stop a payload or a
    token reaching it; loosening a money-adjacent safety bound so a
    diagnostic can be prettier is the trade that produces the next
    incident. A string is a string at any depth.
    """
    out = []
    for e in _EXIT_RING[-limit:]:
        bits = " ".join(f"{k}={v}" for k, v in e.items()
                        if k not in ("reason", "at"))
        out.append(f"{e.get('at', '')} {e.get('reason', '?')} {bits}"[:80])
    return out


# The venue's sibling map, published by the whale_exits worker out of
# the positions call it already makes. Cached because classify_exit is
# on the copy hot path and this is a fallback, not a lookup we want to
# pay for on every miss.
_SIBLING_CACHE: dict[str, str] = {}
# None means NEVER LOADED, and that has to be its own state.
#
# This was 0.0, and the refresh test was `now - _SIBLING_CACHE_AT >
# _SIBLING_TTL_S`. time.monotonic() is time since boot, so on any
# process whose host has been up less than 300 seconds the very first
# call computed 263.9 - 0.0 = 263.9, decided the cache was FRESH, and
# answered every lookup from an empty dict. _sibling_from_positions
# returns '' for an absent map and every caller refuses on '', so for
# the first five minutes of such a process every sibling-dependent exit
# silently declined -- a guard blocking everything while reporting
# itself as safety, keyed off host uptime, which this code does not
# control and cannot see.
#
# It also made the tests flaky rather than red: whether they passed
# depended on how long the container had been up when they ran.
_SIBLING_CACHE_AT: float | None = None
_SIBLING_TTL_S = 300.0


async def _sibling_from_positions(pool, asset: str) -> str:
    """The complementary token id per the VENUE, or ''.

    Never a guess. An absent map, an absent key, or an unreadable row
    all return '' and the caller refuses exactly as it did before.
    """
    global _SIBLING_CACHE, _SIBLING_CACHE_AT
    import time as _t

    now = _t.monotonic()
    if _SIBLING_CACHE_AT is None or now - _SIBLING_CACHE_AT > _SIBLING_TTL_S:
        try:
            raw = await pool.fetchval(
                "SELECT value FROM ingestion_state WHERE key=$1",
                "token_siblings")
            d = raw if isinstance(raw, dict) else (
                json.loads(raw) if raw else {})
            _SIBLING_CACHE = {str(k): str(v)
                              for k, v in (d or {}).items() if k and v}
        except Exception:  # noqa: BLE001 — no map is not a wrong map
            _SIBLING_CACHE = {}
        _SIBLING_CACHE_AT = now
    return _SIBLING_CACHE.get(str(asset), "")


async def whale_still_holds(pool, asset: str, whale: str) -> bool | None:
    """Does he STILL hold this leg? True/False, or None if unknowable.

    THE SWEEP PATH HAS NO STALENESS GATE. execute_copy caps age at
    COPY_EXEC_MAX_AGE_S and maybe_execute at _stale_cap_for(whale), but
    BOTH are guarded on `reaction is not None`, and copy_sweep calls
    maybe_execute(payload, None) over a candidate window of
    `t.ts > now() - interval '7 days'`. So a signal a week old reaches
    the order path with no age check at all.

    That is defensible on price — the FOK limit is his price or better,
    so a market that ran away from him simply does not fill. It is NOT
    defensible on POSITION: a week-old buy is a buy he may since have
    exited, and entering a position the whale has already left is the
    precise divergence the exit work exists to close, arriving through
    the other door.

    Same net-of-merges arithmetic classify_exit uses, because on this
    venue a holding is retired by BUYING the complement and the leg's
    own sum never decrements:

        net(leg) = gross(leg) - gross(sibling)

    None when we cannot tell — no sibling, an unenriched token, a
    failed lookup. The caller treats None as "proceed", because
    refusing everything we cannot measure would close the sweep lane
    entirely, and the lane is not the problem.
    """
    if not asset or not whale:
        return None
    try:
        sibs = await pool.fetch(
            "SELECT s.token_id FROM market_tokens mt "
            "JOIN market_tokens s USING (condition_id) "
            "WHERE mt.token_id = $1 AND s.token_id <> $1", asset)
    except Exception:  # noqa: BLE001
        return None
    if not sibs or len(sibs) != 1:
        return None
    try:
        row = await pool.fetchrow(
            "SELECT COALESCE(sum(CASE WHEN t.asset = $1 THEN "
            "         (CASE WHEN t.side='BUY' THEN t.size "
            "               ELSE -t.size END) END), 0)::float8 AS mine, "
            "       COALESCE(sum(CASE WHEN t.asset = $2 THEN "
            "         (CASE WHEN t.side='BUY' THEN t.size "
            "               ELSE -t.size END) END), 0)::float8 AS sib "
            "FROM trades t JOIN whales w ON w.id = t.whale_id "
            "WHERE t.asset IN ($1, $2) AND lower(w.username) = $3",
            asset, str(sibs[0]["token_id"]), whale)
    except Exception:  # noqa: BLE001
        return None
    if row is None:
        return None
    return (float(row["mine"] or 0) - float(row["sib"] or 0)) > 0


async def classify_exit(pool, asset: str, whale: str,
                        size: float,
                        trade_id: int | None = None) -> dict | None:
    """Is this "buy" actually the whale CLOSING a position?

    Owner, 2026-08-25, after reading Polymarket's global microdata:
    "shorts, in the context of this data, means selling."

    The venue holds ONE SIGNED net position per market. There is no way
    to be long and short the same market at once — so buying the
    complementary leg of something you already hold does not open a
    second bet, it retires the first, share for share. That is why
    these whales show 860,669 buys and zero sells: the exits were never
    missing from the feed, they were sitting in it wearing a BUY label.

    Until now this path did worse than miss them. execute_copy tested
    `side == "BUY"` and handed a complement buy to maybe_execute, which
    sized a fresh clip and bought the leg he was ABANDONING. Our
    exposure to the original outcome did not fall to zero, it went to
    2x — his leg we never close, plus the opposite leg at a price that
    necessarily sums to about 1.00 with his entry. Every exit he made
    cost us twice.

    Three refusals, all fail-closed, because misreading an entry as an
    exit SELLS a position we meant to hold:

      * exactly ONE sibling token, or refuse. Multi-outcome markets and
        negative-risk baskets have no single complement, and an
        unenriched token has none at all. Guessing is not available.
      * he must actually HOLD the sibling. No holding means this is a
        genuine new bet on the other side, not a close.
      * the fraction is capped at 1.0. A buy larger than his holding is
        an exit of everything plus a new entry; we mirror the exit part
        and let the entry go.

    The fraction needs no price conversion: a complement share retires a
    held share one for one, so his closed fraction is a pure ratio of
    share counts. That is what makes it safe to compute on the fast
    path — one indexed local join, no venue call, sub-millisecond.
    """
    if not asset or not whale:
        return _exit_stop("cls_no_asset_or_whale")
    try:
        sibs = await pool.fetch(
            "SELECT s.token_id FROM market_tokens mt "
            "JOIN market_tokens s USING (condition_id) "
            "WHERE mt.token_id = $1 AND s.token_id <> $1", asset)
    except Exception:  # noqa: BLE001 — a lookup failure is not an exit
        return _exit_stop("cls_sibling_lookup_failed", asset=asset)
    sibling = ""
    if sibs and len(sibs) == 1:
        sibling = str(sibs[0]["token_id"])
    elif not sibs:
        # THE VENUE'S OWN SIBLING MAP, WHEN IT HAS ONE.
        #
        # market_tokens does not have this token, so the enrichment
        # lane has not reached it. That was 56 buys in one census
        # window, and the default when we cannot ask is to treat the
        # buy as an ENTRY — while 79 of the 122 buys we CAN classify
        # (65%) turn out to be exits. Guessing "entry" on an unknown
        # token is therefore wrong most of the time here, and being
        # wrong does not miss a copy, it DOUBLES one.
        #
        # whale_exits already fetches each whale's positions every 120
        # seconds and the venue's rows carry the complementary token.
        # We were discarding it. This reads the map that worker
        # publishes — no extra venue call, no hot-path latency beyond
        # one indexed key lookup, and it is CACHED in-process.
        #
        # If the venue does not actually send that field the map is
        # empty, this changes nothing, and the heartbeat's sib_rows
        # says so. The fallback is never a guess: no entry, no
        # classification.
        #
        # ASK GAMMA FIRST. IT HAS ALWAYS BEEN ABLE TO ANSWER
        # (2026-08-26). cls_token_unenriched reached 1,337 -- as many
        # exit signals discarded as we successfully classified -- and
        # both sources consulted above are OUR OWN caches.
        # gamma.lookup_token_live already existed, is production-tested
        # and runs on the ingestion path: one
        # GET /markets?clob_token_ids=<tid>&limit=1 returns
        # clobTokenIds (BOTH legs) plus conditionId, and upsert_market
        # writes every token into market_tokens -- the exact table the
        # join above reads. One call repairs this token PERMANENTLY,
        # for both legs, and the next exit on either side resolves
        # locally with no venue call at all.
        #
        # Why it was missing rather than merely late: a trade arriving
        # with its own condition_id is stamped enriched_at at INSERT
        # and never calls Gamma, so its market never enters
        # market_tokens -- and the trade-driven repair lane selected
        # WHERE enriched_at IS NULL, permanently excluding exactly
        # those rows. That selector is re-keyed in this same commit so
        # the population also heals in the background.
        #
        # BOUNDED, because this is the copy hot path: one attempt, a
        # short timeout, and any failure falls through to the venue map
        # exactly as before. lookup_token_live caches its own misses,
        # so a genuinely unknown token is not re-fetched.
        try:
            from . import gamma as _gamma

            await asyncio.wait_for(_gamma.lookup_token_live(asset),
                                   timeout=TOKEN_LOOKUP_TIMEOUT_S)
            _re = await pool.fetch(
                "SELECT s.token_id FROM market_tokens mt "
                "JOIN market_tokens s USING (condition_id) "
                "WHERE mt.token_id = $1 AND s.token_id <> $1", asset)
        except Exception:  # noqa: BLE001 — enrichment is best-effort
            _re = []
        if _re and len(_re) == 1:
            sibling = str(_re[0]["token_id"])
            _exit_stop("cls_sibling_from_gamma", asset=asset)
        else:
            sibling = await _sibling_from_positions(pool, asset)
            if not sibling:
                return _exit_stop("cls_token_unenriched", asset=asset,
                                  whale=whale)
            _exit_stop("cls_sibling_from_venue_map", asset=asset)
    if not sibling:
        # Multi-outcome markets and negative-risk baskets have no
        # single complement. Refusing is correct; counting it tells us
        # how much of the roster's flow is structurally unclassifiable.
        return _exit_stop("cls_not_binary", asset=asset,
                          siblings=len(sibs))
    # HIS HOLDING OF THE SIBLING, NET OF THE MERGES (2026-08-25,
    # adversarial review).
    #
    # This was buys - sells on the sibling alone. On THIS venue nobody
    # sells: a holding is retired by BUYING the complement, which lands
    # as a BUY on the OTHER asset and never decrements the sibling's
    # sum. So `open_sh` was lifetime GROSS BUYS of that leg and could
    # only ever go up.
    #
    # After a single completed round trip that is not merely imprecise,
    # it INVERTS THE ANSWER. He buys A, exits by buying B — both legs
    # are now zero on the venue, but our sum still reads 100 of B. His
    # next fresh entry on A therefore classifies as an EXIT, and we
    # SELL a position we were meant to hold. That is the one direction
    # this classifier must never fail in.
    #
    # The venue nets the pair, so the real holding does too:
    #
    #     net(sibling) = gross(sibling) - gross(this leg)
    #
    # where gross is buys - sells per leg. Both legs in one indexed
    # query over (asset, whale_id), so it is no more work than before.
    #
    # THE CURRENT FILL IS EXCLUDED BY ID. It is already in `trades` by
    # the time we are called, and it is a buy of THIS leg — leaving it
    # in would subtract his own exit from the position it is closing
    # and understate what he still held when he made it.
    try:
        his = await pool.fetchrow(
            "SELECT COALESCE(sum(CASE WHEN t.asset = $1 THEN "
            "         (CASE WHEN t.side='BUY' THEN t.size "
            "               ELSE -t.size END) END), 0)::float8 AS sib, "
            "       COALESCE(sum(CASE WHEN t.asset = $2 THEN "
            "         (CASE WHEN t.side='BUY' THEN t.size "
            "               ELSE -t.size END) END), 0)::float8 AS mine "
            "FROM trades t JOIN whales w ON w.id = t.whale_id "
            "WHERE t.asset IN ($1, $2) AND lower(w.username) = $3 "
            "  AND t.id <> COALESCE($4::bigint, -1)",
            sibling, asset, whale, trade_id)
    except Exception:  # noqa: BLE001
        return _exit_stop("cls_holding_lookup_failed", asset=sibling)
    open_sh = max(0.0, float((his["sib"] if his else 0) or 0.0)
                  - float((his["mine"] if his else 0) or 0.0))
    if open_sh <= 0:
        # He does not hold the other leg, so this really is a fresh bet
        # on this side. THE EXPECTED OUTCOME on a genuine entry — this
        # counter should dominate, and if it does not, something is
        # wrong upstream rather than here.
        return _exit_stop("cls_no_sibling_holding")
    try:
        qty = float(size or 0)
    except (TypeError, ValueError):
        return _exit_stop("cls_size_unparseable", size=size)
    if qty <= 0:
        return _exit_stop("cls_size_not_positive", size=size)
    _exit_stop("cls_classified_as_exit", asset=sibling, whale=whale,
               closed_frac=round(min(1.0, qty / open_sh), 4))
    return {"asset": sibling, "exit_via_asset": asset,
            "closed_frac": min(1.0, qty / open_sh),
            "his_open_shares": open_sh, "his_exit_shares": qty}


async def mirror_exit(payload: dict) -> str:
    """The whale sold. Sell our copy of the same market.

    Owner order 2026-08-25: "copy both buys and sells at a proportional
    rate", and the worked example — he buys $5,000, sells for $7,500,
    then re-enters at $60. Copying only the entry leaves us holding to
    resolution while he banks the move, which is a different and worse
    strategy than his. Mirroring both legs makes our net position on a
    market track his automatically: no house-money special case is
    needed, because after his exit our exposure is proportionally the
    same as his.

    PARTIALS ARE MIRRORED PROPORTIONALLY (owner order 2026-08-25: "copy
    buys and 'sells' in the correct proportional relationship"). We sell
    his closed fraction OF OUR holding and keep the remainder alive;
    only at FULL_EXIT_FRAC do we go flat. v1 mirrored 95%+ exits and
    skipped the rest, on the reasoning that partial accounting is where
    errors compound. That risk is real, but skipping leaves us holding a
    position he has already reduced, which is the exact divergence this
    path exists to close.

    NOT inert, and the docstring said it was for a day after it stopped
    being true. The copied whales still show zero SELLs across 860k
    trades — they close by MERGING back to USDC, which no trade feed
    shows — so this fires through two detectors that do not need a sell:
    classify_exit (he BOUGHT the complementary leg) and the whale_exits
    positions-snapshot diff, which supplies closed_frac directly.
    """
    if payload.get("side") != "SELL":
        return _exit_done("mx_not_a_sell")
    if not settings().copy_probe_enabled or copy_halted():
        return _exit_done("mx_halted")
    username = (payload.get("whale_username") or "").lower()
    # CUTTING A WHALE MUST NOT STRAND HIS POSITIONS (2026-08-26).
    #
    # This was the verified-whale set -- an ENTRY gate -- applied to the
    # EXIT path. The moment a whale is cut he leaves LIVE_VERIFIED_WHALES,
    # and every position we already copied from him becomes permanently
    # un-exitable: we hold it to resolution against a whale who has
    # already left it. That is the exact divergence the exit leg exists
    # to close, and it fires on every roster change rather than as a
    # one-off.
    #
    # It is live right now for homerunhazard, swisstony, 0x2c33 and
    # kch123 -- four cut whales whose open copies cannot be sold.
    #
    # Selling cannot create exposure. mirror_exit only ever reduces a
    # position, and the row query below scopes to a live_orders row that
    # is already 'filled' for this exact whale and asset -- so there is
    # nothing here to sell unless we genuinely bought it from him.
    #
    # Kept to whales we have ACTUALLY ROSTERED rather than opened to any
    # string: verified, cut, or carrying a clip entry. A username that
    # was never on the roster still refuses.
    # roster override refresh BEFORE the roster checks (owner order
    # 2026-08-29): the DB-stored set must be what judges this exit.
    # No pool is a failed read (2026-09-05): CLOSED, not the fall-through.
    try:
        _pool_for_roster = await get_pool()
    except Exception as exc:  # noqa: BLE001
        close_overrides(f"pool: {type(exc).__name__}: {str(exc)[:120]}")
    else:
        try:
            await refresh_whale_overrides(_pool_for_roster)
        except Exception:  # noqa: BLE001 — the refresh never raises; belt
            pass
    if username not in exitable_whales():
        return _exit_done("mx_whale_not_verified", whale=username)
    if username not in _whale_set("LIVE_VERIFIED_WHALES"):
        # Visible, because a standing count means we are still unwinding
        # a cut whale's book and that should not be silent.
        _exit_stop("mx_exit_of_cut_whale", whale=username)
    asset = str(payload.get("asset") or "")
    if not asset:
        return _exit_done("mx_no_asset")
    pool = await get_pool()
    # THE ADMIN KILL SWITCH DID NOT REACH THIS PATH (2026-08-26,
    # adversarial round 4).
    #
    # The module docstring lists the safety model as the layers EVERY
    # order passes, with the admin pause second. _is_paused was read in
    # exactly two places — the manual desk and maybe_execute — and
    # mirror_exit is neither. So POST /api/admin/live/pause, documented
    # as "no further orders", stopped entries while the whale_exits
    # worker went on sending order after order at 120s cadence, and the
    # full-exit branch sends close_position: no limit price, bounded
    # only by EXIT_SLIPPAGE_BIPS. The operator believes the account is
    # stopped; the account is still transacting, unpriced, into
    # whatever book condition prompted the pause.
    #
    # It is the newest order-placing path and it grew outside the gate
    # rather than through it — the same shape as a new lane missing a
    # cap. Pending, not settled: a pause is cleared by the operator and
    # the exit is still real when it clears.
    if await _is_paused(pool):
        return _exit_done("mx_paused", whale=username, asset=asset)
    if await overspend_halt(pool):
        return _exit_done("mx_overspend_halt")
    # STAMPED AFTER THE HALT GATE, NOT BEFORE (2026-08-25, adversarial
    # review). It was counted before overspend_halt and before the
    # query, so a sleeve stopped by a tripped breaker still reported
    # every exit as "reaching the position lookup" — and the endpoint's
    # verdict reads that counter to decide whether exits are arriving
    # at all. A halted system would have looked like a working one with
    # nothing to sell.
    _exit_stop("mx_reached_position_lookup", whale=username, asset=asset)
    # ONE WHALE EXIT, MIRRORED ONCE (2026-08-26, adversarial round 2).
    #
    # mirror_exit runs BEFORE maybe_execute's live_orders INSERT and
    # never writes a row keyed to the EXIT trade, so BOTH of
    # copy_sweep's idempotency guards miss it: the asset guard tests
    # lo.asset = t.asset and the exit trade's asset is the COMPLEMENT
    # leg, and the trade guard tests lo2.trade_id = t.id and no such
    # row exists.
    #
    # A FULL exit is saved by accident — the position row becomes
    # 'cashed_out' and the next sweep finds nothing to sell. A PARTIAL
    # one is not: the remainder branch writes the row back to
    # status='filled', so ~120s later the same complement buy is
    # re-detected, re-classified and mirrored again against what is
    # LEFT. A single 50% trim becomes 50%, then 50% of the rest — we
    # exit a position he only trimmed, in a staircase, paying the
    # spread at every step.
    #
    # CHECKED HERE, RECORDED ONLY AFTER A SALE. My first version wrote
    # the ledger row up front, which burns the exit on any legitimate
    # refusal — and the most important refusal is mx_no_position_of_ours,
    # which happens whenever his exit is detected BEFORE our entry
    # fills. Claiming there would mean the exit is never mirrored once
    # the entry lands, and we would hold a position he has left: the
    # exact divergence this path exists to close.
    #
    # Concurrency is already handled by the atomic status='exiting'
    # claim on the position row. This ledger is about REPLAY ACROSS
    # TIME, which is a different problem and needs a different record.
    #
    # Trade-driven path only: the position-diff detector carries no
    # trade id and is already bounded by its snapshot advancing.
    _xtid = payload.get("id")
    if _xtid is not None:
        try:
            _seen = await pool.fetchval(
                "SELECT trade_id FROM copy_exit_applied WHERE trade_id=$1",
                int(_xtid))
        except Exception:  # noqa: BLE001 — an unreadable ledger is not
            # permission to replay a sale. Refuse and retry next cycle.
            return _exit_done("mx_exit_ledger_unreadable", trade=_xtid)
        if _seen is not None:
            return _exit_done("mx_exit_already_mirrored", trade=_xtid)
    # WHAT WE ORIGINALLY BOUGHT, not what is left of it. filled_shares is
    # rewritten to the remainder by every partial exit below, so it cannot
    # be the base of a cumulative target (see migration 040). COALESCE so
    # a row never yet exited reads exactly as before.
    #
    # THE COLUMN MAY NOT EXIST YET, AND EXITS MUST NOT WAIT FOR IT.
    # Migrations run at API boot only, best-effort ("serving anyway"), and
    # the WORKER that runs this function never runs them at all -- its
    # entrypoint is `python -m sportsassets.workers.all`. So new code can
    # reach production ahead of its own column, and a bare reference would
    # raise on EVERY exit until a healthy API boot happened to land. That
    # is a worse failure than the compounding it fixes. Fall back to the
    # legacy projection instead, which is exactly today's behaviour, and
    # say so loudly once per call rather than failing the sale.
    #
    # A MIRROR BOOK IS NEVER SOLD BY THIS PATH (mirror P1, step 7; owner
    # order 2026-09-02, "go for it, let's get this working"). A book's
    # standing row is 'filled' for its whale on its asset from the day it
    # opens, so without this clause a classified exit -- or the position
    # lane's snapshot diff -- would claim it 'exiting', sell it whole and
    # close_position the market the mirror is still reconciling toward
    # his net. The panel review's predicate audit (spec 1d) makes the
    # exclusion structural: it holds whatever any switch says, because
    # the book unwinds itself from the same raw snapshot. COALESCE, so a
    # row placed before migration 041 (lane NULL: every row that exists
    # today) keeps today's path byte for byte.
    _sel_tail = (
        "       fill_price::float8 AS entry, "
        f"      {ORDER_INTENT_SQL} AS intent "
        "FROM live_orders "
        "WHERE asset = $1 AND lower(COALESCE(whale_username,'')) = $2 "
        "  AND status = 'filled' AND us_market_slug IS NOT NULL "
        "  AND COALESCE(lane,'') <> 'mirror' "
        "ORDER BY placed_at DESC LIMIT 1")
    row = await _position_row(pool, asset, username, _sel_tail)
    if row is None:
        # AN ENTRY STILL IN FLIGHT IS NOT "NO POSITION". The rest lane
        # holds a row at 'submitting' for up to REST_BID_TTL_S plus a
        # few reads, and a rest_unknown row stays 'submitting' until the
        # reaper reconciles it minutes later. A whale who exits inside
        # either window would have had his exit filed as
        # mx_no_position_of_ours -- which is NOT a pending reason -- and
        # the position ridden to resolution against him. A young row is
        # given its window to settle and looked up again; an old one, or
        # one that never becomes a fill, is answered "in flight" THROUGH
        # _exit_done so the reason reaches the position lane and the
        # asset is pinned for the next cycle. (Round three: the first
        # version returned it through _exit_stop, whose contract is to
        # return None, so nothing was ever pinned and the wait bought
        # nothing. The behavioural test in
        # test_in_flight_entry_is_pending.py drives this path.)
        _inflight, _age, _readable = await _entry_in_flight(pool, asset, username)
        if not _readable:
            # Its own reason (the census wants every path named
            # distinctly): pending, like in-flight, because an entry MAY
            # be in flight and the lookup could not say.
            return _exit_done("mx_inflight_unreadable", asset=asset,
                              whale=username)
        if _inflight:
            _window = REST_BID_TTL_S + _INFLIGHT_GRACE_S
            if _age is not None and _age <= _window:
                _deadline = time.monotonic() + _window
                while True:
                    await asyncio.sleep(_INFLIGHT_POLL_S)
                    try:
                        _st = await pool.fetchval(
                            "SELECT status FROM live_orders WHERE id = $1",
                            _inflight)
                    except Exception:  # noqa: BLE001 — keep waiting
                        _st = "submitting"
                    if _st != "submitting" or time.monotonic() >= _deadline:
                        break
                try:
                    row = await _position_row(pool, asset, username, _sel_tail)
                except Exception:  # noqa: BLE001 — a DB error is not "no position"
                    row = None
            if row is None:
                return _exit_done("mx_entry_in_flight", asset=asset,
                                  whale=username)
    if row is None or (row["qty"] or 0) <= 0:
        # WE NEVER COPIED HIS ENTRY. At a 0.55% fill rate this is the
        # expected majority outcome and it is NOT an exit-path defect:
        # there is nothing to sell. Counting it separately is what
        # stops "mirror_exit never fired" from being read as a broken
        # exit path when it is actually a coverage number.
        #
        # THE BOOK'S TOKEN IS THE MIRROR'S TO UNWIND (mirror P1, step 7,
        # spec 3.2(b); owner order 2026-09-02). The lane guard above
        # keeps a book's standing row out of this lookup, so his vanish
        # on a booked market lands here with no row. Its own name keeps
        # the census's coverage line (mx_no_position_of_ours: we never
        # copied his entry) clean of markets we DO hold, through the
        # book. Both are settled reasons, neither sells anything: the
        # exit worker counts exits_no_action and advances its snapshot,
        # which is right, because the mirror reads that same snapshot
        # and his vanish is ITS flatten trigger. An unreadable
        # mirror_books answers False and falls to today's reason.
        if await _mirror_owns_asset(pool, asset):
            return _exit_done("mx_mirror_owns_market", whale=username,
                              asset=asset)
        return _exit_done("mx_no_position_of_ours", whale=username,
                          asset=asset)
    # THE OTHER LANE MAY ALREADY HAVE MIRRORED THIS EXIT.
    # See EXIT_DEDUP_MINUTES. Only for the position-diff lane: a caller
    # WITH a trade id was already deduped by the ledger check above, and
    # applying this to it would refuse his genuine second trade on a
    # market inside the window.
    if _xtid is None:
        try:
            _recent = await pool.fetchval(
                "SELECT applied_at FROM copy_exit_applied "
                "WHERE row_id = $1 "
                "  AND applied_at > now() - make_interval(mins => $2) "
                "ORDER BY applied_at DESC LIMIT 1",
                int(row["id"]), EXIT_DEDUP_MINUTES)
        except Exception:  # noqa: BLE001 — an unreadable ledger is not
            # permission to sell again. Refuse; it retries next cycle.
            return _exit_done("mx_exit_dedup_unreadable", asset=asset)
        if _recent is not None:
            return _exit_done("mx_exit_recently_applied", whale=username,
                              asset=asset, row=int(row["id"]))
    # HIS fraction: how much of his own position did this sale close?
    # Sum his own ledger for this asset rather than trusting one row.
    pos = await pool.fetchrow(
        """
        SELECT COALESCE(sum(t.size) FILTER (WHERE t.side='BUY'), 0)::float8
                   AS bought,
               COALESCE(sum(t.size) FILTER (WHERE t.side='SELL'), 0)::float8
                   AS sold
        FROM trades t JOIN whales w ON w.id = t.whale_id
        WHERE t.asset = $1 AND lower(w.username) = $2
        """, asset, username)
    bought = (pos["bought"] if pos else 0) or 0.0
    sold = (pos["sold"] if pos else 0) or 0.0
    # POSITION-DERIVED FRACTION (2026-08-25). The ledger computation
    # below can never fire for these whales: they close by MERGING, not
    # selling, so `sold` is 0 across 860k trades and closed_frac would
    # always be 0. Measured: swisstony holds less than he bought on
    # 62 of 75 positions, ferrari on 18 of 23 — exits no trade feed
    # shows. The position poller therefore supplies the fraction it
    # measured directly, and the ledger stays as the path for a whale
    # who genuinely sells.
    supplied = payload.get("closed_frac")
    if supplied is not None:
        try:
            closed_frac = max(0.0, min(float(supplied), 1.0))
        except (TypeError, ValueError):
            return _exit_done("mx_bad_supplied_fraction", frac=supplied)
    else:
        if bought <= 0:
            return _exit_done("mx_no_ledger_position", asset=asset)
        closed_frac = min(sold / bought, 1.0)
    # A STOCK, NOT A FLOW — ON THE LANE THAT CARRIES THE VOLUME.
    #
    # classify_exit hands over `qty / open_sh`: this single complement
    # buy over what he still held AT THAT MOMENT. The denominator
    # shrinks in lockstep with the whale, so a man who trims 5% of
    # what is left, forty-five times, reads exactly 0.05 on every
    # observation and is refused by MIN_EXIT_FRAC forty-five times
    # while walking out of the entire position. The fraction never
    # ratchets because his remaining shrinks with his trims. Only a
    # constant-ABSOLUTE-size trimmer ever crosses the floor.
    #
    # Measured 2026-08-30: mx_below_floor was 94 of the 191 exits that
    # found a filled position of ours — 49%, the largest single
    # refusal — while exits_sold stayed 0. This is the same flow-vs-
    # stock defect already fixed for the position lane (lines 426-450),
    # left unfixed on the trade lane, which carries 98% of the volume
    # (mx_reached_position_lookup 1572 vs the position lane's 23).
    #
    # `bought` above is his GROSS entry on the leg he held ($1 is the
    # sibling by this point), and classify_exit supplies what he held
    # before this trade and what this trade retired. So the honest
    # question — how far out of this position is he IN TOTAL — is:
    #
    #     cum = 1 - (his_open_shares - his_exit_shares) / bought
    #
    # The position lane keeps its own supplied flow: it is pre-filtered
    # at MIN_SHRINK and pinned by EXIT_PENDING_REASONS, so it already
    # ratchets. Only the trade lane is rewritten, and only when
    # classify_exit actually supplied both terms.
    _flow_frac = closed_frac
    _hos = payload.get("his_open_shares")
    _hes = payload.get("his_exit_shares")
    if _hos is not None and _hes is not None and bought > 0:
        try:
            _remaining = max(0.0, float(_hos) - float(_hes))
            closed_frac = max(0.0, min(1.0 - _remaining / bought, 1.0))
        except (TypeError, ValueError, ZeroDivisionError):
            closed_frac = _flow_frac   # fail back to the old reading
    # PROPORTIONAL, BOTH LEGS (owner order 2026-08-25: "copy buys and
    # 'sells' in the correct proportional relationship").
    #
    # v1 mirrored only 95%+ exits and skipped the rest, because partial
    # accounting is where errors compound. The compounding risk is real
    # but the fix is not to discard the information: a partial that is
    # skipped leaves us holding a position he has already reduced,
    # which is the exact divergence this whole path exists to close.
    #
    # So we sell his fraction of OUR holding, and the row bookkeeping
    # below keeps the remainder alive rather than retiring the whole
    # position on a partial sale.
    if closed_frac < MIN_EXIT_FRAC:
        log.info("MIRROR-EXIT below floor: %s closed %.1f%% of %s "
                 "(floor %.0f%%) — not worth a spread crossing",
                 username, closed_frac * 100, asset, MIN_EXIT_FRAC * 100)
        return _exit_done("mx_below_floor", whale=username,
                          closed_frac=round(closed_frac, 4))
    from . import pmus

    us_slug = row["us_market_slug"]
    # ATOMIC CLAIM. Five complement fills inside one second spawn five
    # execute_copy tasks, and the position lane reaches mirror_exit
    # outside _COPY_SEM (execute_copy returns before the semaphore on a
    # SELL) — so without this every one of them reads the same 'filled'
    # row and every one issues a sell. Exactly one caller gets a row
    # back here. The TRADE lane is different: classify_exit calls this
    # from inside maybe_execute, under a semaphore slot, so its in-flight
    # wait above parks that slot for up to REST_BID_TTL_S plus grace.
    # Bounded, cannot deadlock (the entry it waits on holds a different
    # slot), and visible in the QUEUE probe line's wait statistics.
    # THE CLAIM IS THE AUTHORITATIVE READ (adds review round two): the
    # row was read several round trips ago, and an add leg can merge
    # onto it in between (the merge requires 'filled', which this claim
    # ends). Sizing, the base and the terminal-branch decision all use
    # the figures the claim itself returns, never the stale read.
    claimed = await pool.fetchval(
        "UPDATE live_orders SET status='exiting' "
        "WHERE id=$1 AND status='filled' RETURNING id", row["id"])
    if claimed is None:
        log.info("MIRROR-EXIT %s already claimed by another task", us_slug)
        return _exit_done("mx_already_claimed", slug=us_slug)
    try:
        # the row is 'exiting' now and a merge needs 'filled', so this
        # read cannot be overtaken; the figures it returns are final
        _post = await pool.fetchrow(
            "SELECT filled_shares::float8 AS qty, "
            "       COALESCE(orig_shares, filled_shares)::float8 AS orig_qty "
            "FROM live_orders WHERE id = $1 /* post-claim */", row["id"])
        _q, _o = _row_get(_post, "qty"), _row_get(_post, "orig_qty")
        if isinstance(_q, (int, float)) and float(_q) > 0:
            _fresh = dict(row)
            _fresh["qty"] = float(_q)
            _fresh["orig_qty"] = (float(_o) if isinstance(_o, (int, float))
                                  and float(_o) > 0 else float(_q))
            row = _fresh
    except Exception:  # noqa: BLE001 — an unreadable refresh keeps the read
        pass
    try:
        held, _avg = await _pm_held(us_slug)
        ours = min(int(row["qty"]), held)
        # His fraction OF OUR position — the proportional relationship.
        #
        # HALF-UP, NOT TRUNCATION (2026-08-26). int() floors, so a
        # 4-share remainder against a 20% trim computed int(0.8) = 0 and
        # the exit was dropped. Shares are the smallest unit the venue
        # sells, so 0.8 of one has to resolve to 1 or to 0 — and 1 is
        # the answer that moves us toward the whale. The overshoot is
        # bounded above by a single share.
        #
        # int(x + 0.5), not round(x): round() is banker's, so round(10.5)
        # is 10 and round(11.5) is 12. A sizing rule that depends on the
        # parity of the share count is not a rule.
        # SIZED TO A TARGET, NOT TO A SLICE.
        #
        # `ours * closed_frac` is right for a per-cycle FLOW and wrong
        # for the cumulative fraction computed above: `ours` shrinks
        # every time we sell, so applying a growing cumulative fraction
        # to a shrinking base compounds and walks us out faster than
        # the whale. A whale 20% then 40% out would take 20% of 100,
        # then 40% of 80 — leaving 48 where he holds 60.
        #
        # So state the target instead: he is `closed_frac` out of what
        # he bought, therefore we should still hold that same fraction
        # of what WE bought, and today's sale is the difference. This
        # is self-correcting — an observation we missed, refused or
        # under-filled is made up by the next one rather than lost, and
        # once we are at or below the target the delta goes to zero and
        # the rounds-to-zero branch below pins it instead of selling.
        # THE BASE IS WHAT WE BOUGHT, NOT WHAT IS LEFT (2026-09-01).
        #
        # This read `row["qty"]`, which is filled_shares -- the column
        # the partial-exit UPDATE below rewrites to the remainder. So
        # the second trim measured the cumulative fraction against an
        # already-shrunken base and compounded, which is the precise
        # defect the target form above was written to prevent. Driven
        # end to end, a whale going 20% then 40% out of a 1,000-share
        # book left us holding 96 of our 200 where 120 is correct.
        #
        # test_exit_fraction_is_a_stock passed throughout, because it
        # handed the second trim a FRESH Row(qty=200) instead of the 160
        # mirror_exit had just written. A test that rebuilds the state
        # under test cannot see a bug in how that state is kept.
        _orig = int(_row_get(row, "orig_qty") or row["qty"] or 0)
        _target_hold = _orig * (1.0 - closed_frac)
        qty = int(ours - _target_hold + 0.5)
        if qty > ours:
            qty = ours          # never sell more than the venue holds
        if closed_frac >= FULL_EXIT_FRAC:
            qty = ours          # he is out; so are we, to the share
        if qty <= 0:
            await _release_exit_claim(pool, row["id"])
            if ours > 1:
                # ROUNDING, NOT A POSITION FACT. Filing this under
                # "venue holds nothing" was false — the venue holds
                # `ours` — and the false name is what hid it: the census
                # showed a ledger/venue disagreement where the real
                # event was a fraction too small to buy a whole share.
                #
                # PENDING, so whale_exits pins the asset at its pre-trim
                # size instead of advancing past it. The next cycle then
                # measures a cumulative fraction against the same
                # position and crosses 1 share within a few observations
                # — the same ratchet that fixed the sub-floor trims.
                #
                # ours == 1 is excluded deliberately: no partial can
                # ever round a single share up (it would have to reach
                # 50%, and a 1-share position that big a fraction is
                # what FULL_EXIT_FRAC flattens anyway), so pinning it
                # would hold a retry slot that can never resolve.
                # A whale who trims and then stops does keep a 2-4 share
                # residual pinned until his full exit. That is a known,
                # bounded cost and it is the honest one: the alternative
                # is discarding an exit we detected.
                return _exit_done("mx_exit_rounds_to_zero", slug=us_slug,
                                  ours=ours, held=held,
                                  closed_frac=round(closed_frac, 4))
            # Our ledger says filled, the VENUE says we hold nothing.
            # A disagreement between the two is its own class of
            # problem and must never be filed under "no position".
            return _exit_done("mx_venue_holds_nothing", slug=us_slug,
                              our_qty=int(row["qty"] or 0), held=held)
        # FULL EXIT: use the venue's own flatten, which needs no price.
        #
        # Pricing the sell off slug_bid meant every unreadable bid was
        # an exit we DETECTED and then declined to take, leaving us
        # holding a position the whale had already left. close-position
        # carries no limit, so a missing bid cannot block it, and it
        # works on either sign — which matters because a short reads
        # negative and three separate guards treated that as "nothing
        # held".
        #
        # A partial still needs a quantity, so it keeps the limit IOC.
        #
        # ONLY WHEN OUR COPY IS THE WHOLE VENUE POSITION (2026-08-26,
        # adversarial round 4). close_position takes ONLY a market slug
        # — no side, no quantity, no limit — and flattens whatever the
        # ACCOUNT holds there. `held` comes from _pm_held, which reads
        # the account-wide netPosition, so on a slug another sleeve also
        # holds it sells their shares too.
        #
        # Co-holding is designed in, not accidental: the no-stack
        # carve-out lets a copy proceed when the other holder is the
        # underdog sleeve, and the manual desk is never blocked from a
        # slug the copy sleeve holds. So a whale exit would flatten the
        # underdog sleeve's position, leave its row reading 'filled'
        # with nothing behind it, and book the P&L of shares this row
        # never owned onto this row.
        #
        # When we ARE the whole position the flatten is exactly right
        # and keeps its no-price property. When we are not, selling a
        # QUANTITY is the only correct action, so it takes the limit IOC
        # path — and if the bid is unreadable it refuses, because
        # "cannot price our own shares" must not escalate to
        # "flatten somebody else's".
        _full = closed_frac >= FULL_EXIT_FRAC
        _sole = ours >= held
        _flatten = _full and _sole
        if _full and not _sole:
            log.warning("MIRROR-EXIT %s: venue holds %d on this slug and "
                        "only %d is ours — selling our quantity instead "
                        "of flattening the market", us_slug, held, ours)
        limit = None
        if not _flatten:
            # WHICH LEG WE HOLD, from the intent recorded at entry.
            # On the tennis family both sides share one identifier, so
            # the slug cannot select a leg and slug_bid refuses without
            # this — which is the whole of `no_bid` in the exit census.
            _long_leg = None
            _oi = row["intent"]
            if _oi == "ORDER_INTENT_BUY_LONG":
                _long_leg = True
            elif _oi == "ORDER_INTENT_BUY_SHORT":
                _long_leg = False
            else:
                # No recorded intent (an older row, or a raw payload
                # that never carried one). The ACCOUNT still knows
                # which side it holds, and the sign of netPosition is
                # the same fact — _exit_intent already falls back this
                # way. Without this the fix would silently reproduce
                # `no_bid` on exactly the rows whose history is
                # thinnest.
                _long_leg = await _pm_long_leg(us_slug)
            bid = await asyncio.to_thread(pmus.slug_bid, us_slug,
                                          _long_leg)
            if bid is None or not (0 < bid < 1):
                log.warning("MIRROR-EXIT no bid for %s leg=%s — "
                            "exit deferred rather than sold blind",
                            us_slug, _long_leg)
                await _release_exit_claim(pool, row["id"])
                return _exit_done("mx_no_bid_for_partial", slug=us_slug,
                                  full=bool(_full))
            limit = sell_limit_price(bid)
    except BaseException:
        # BaseException, NOT Exception (2026-08-26, adversarial round 4).
        # asyncio.CancelledError derives from BaseException, so a
        # cancellation between the claim above and the terminal UPDATE
        # sailed straight past this handler and left the row in
        # 'exiting' with the claim never released. A canceller is live
        # on this path: copy_sweep wraps maybe_execute in
        # asyncio.wait_for(ROW_TIMEOUT_S=60) and reaches mirror_exit
        # through classify_exit; Render's redeploy SIGTERM is a second.
        #
        # Nothing has been sent to the venue yet at this point, so
        # releasing back to 'filled' is unambiguously right. shield()
        # because awaiting inside a cancelled task is itself cancelled.
        with contextlib.suppress(BaseException):
            await asyncio.shield(_release_exit_claim(pool, row["id"]))
        # COUNT IT ON THE WAY OUT (2026-08-26). Every other way out of
        # this function records a reason; the three re-raise paths did
        # not, so an exit that died on a cancellation or a venue error
        # left NO trace in the census — and a census that silently omits
        # a class is worse than no census, because the reader believes
        # the totals.
        #
        # _exit_stop, not _exit_done: it returns None and nothing
        # branches on it, so this cannot alter the re-raise below.
        #
        # locals().get, because `qty` is assigned INSIDE the try: an
        # abort in _pm_held or the sizing arithmetic reached this line
        # with qty unbound, and the UnboundLocalError raised here
        # replaced the CancelledError being re-raised (found by the
        # in-flight behavioural test, 2026-09-01).
        _exit_stop("mx_aborted_before_venue", slug=us_slug,
                   qty=locals().get("qty"))
        raise
    try:
        if _flatten:
            result = await asyncio.to_thread(
                pmus.close_position, us_slug,
                slippage_bips=EXIT_SLIPPAGE_BIPS)
        else:
            # ONE FACT, ONE SOURCE (2026-08-31). The limit above is
            # priced for a specific leg. submit_fok derives the SELL
            # side itself via _exit_intent, and with intent=None that
            # falls back to the venue's position sign — a second,
            # independent derivation of the same fact. Two derivations
            # can disagree, and disagreeing here means pricing one leg
            # and selling the other. Passing the recorded BUY intent
            # makes both read the same thing; when it is absent both
            # fall back to the position sign, which is still one shared
            # source.
            result = await asyncio.to_thread(
                pmus.submit_fok, us_slug, limit, qty, True,
                "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL",
                _oi if _oi in ("ORDER_INTENT_BUY_LONG",
                               "ORDER_INTENT_BUY_SHORT") else None)
    except asyncio.CancelledError:
        # THE SALE MAY ALREADY HAVE HAPPENED. asyncio.to_thread cannot
        # be cancelled — the thread runs to completion — so a
        # cancellation here can land AFTER the venue filled the order.
        # Releasing to 'filled' would then claim we still hold shares
        # that are gone, and the next cycle would sell them again.
        # Leaving it 'exiting' silently is the other bad option: the
        # settlement sweep targets 'filled', mirror_exit's row query
        # requires 'filled', and copy_sweep blocks the asset while it
        # reads 'exiting' — the row becomes invisible and unsellable.
        #
        # So it is marked for RECONCILIATION and _reap_stale_exiting
        # resolves it against the venue's own position, which is the
        # only thing that knows the answer.
        with contextlib.suppress(BaseException):
            await asyncio.shield(pool.execute(
                "UPDATE live_orders SET error=$2 WHERE id=$1",
                row["id"],
                "exit cancelled mid-venue-call — reconcile: the sale "
                "may have executed"))
        _exit_stop("mx_cancelled_mid_venue_call", slug=us_slug, qty=qty)
        raise
    except Exception as _exc:
        await _release_exit_claim(pool, row["id"])
        _exit_stop("mx_venue_error", slug=us_slug, qty=qty,
                   err=str(_exc)[:64])
        raise
    filled = float(result.get("filled_shares") or 0)
    px = result.get("fill_price")
    if not (result.get("ok") and filled > 0):
        log.warning("MIRROR-EXIT unfilled %s: %s", us_slug,
                    str(result.get("raw"))[:160])
        await _release_exit_claim(pool, row["id"])
        return _exit_done("mx_venue_unfilled", slug=us_slug)
    entry = row["entry"] or 0
    # BOOK ONLY WHAT WAS OURS (2026-08-26, adversarial round 4).
    #
    # `filled` is the quantity the VENUE closed. On the flatten path
    # that is the account's whole position on the slug, so on a
    # co-held market it included another sleeve's shares — and feeding
    # it to realized_pnl priced those shares at OUR entry and added the
    # result to this row's pnl. A dollar counted on a position the row
    # never owned, in the row and in every aggregate that sums it.
    #
    # The flatten is now reserved for the case where our copy IS the
    # whole position, so the two agree. This clamp is the belt: it can
    # only ever reduce what is booked, and it holds even if some future
    # path reintroduces the difference.
    booked = min(float(filled), float(ours))
    # SHORT EXITS WERE SIGN-INVERTED. See realized_pnl: on a short the
    # venue's price field names the LONG leg, so the realized amount is
    # (entry - exit), not (exit - entry). Six shorts filled before the
    # branch was banned and we may still hold them.
    pnl = realized_pnl(entry, px, booked, row["intent"])
    # A POSITION IS NEVER RETIRED WHILE SHARES REMAIN.
    #
    # Writing 'cashed_out' after selling only part of the row orphans
    # the rest: the settlement sweep targets status='filled', so shares
    # we still hold would never be graded; mirror_exit's own row query
    # requires status='filled', so they could never be sold again; and
    # copy_sweep's blocking list contains 'cashed_out', so we would
    # never re-enter either. The shares sit at the venue, invisible
    # everywhere.
    #
    # The old condition was `remaining > 0 AND closed_frac <
    # FULL_EXIT_FRAC` — it gated on the WHALE'S FRACTION rather than on
    # whether shares actually remained. That covered one of the two ways
    # a remainder arises and not the other: a FULL exit that only
    # partially fills. Partial fills are a shape this venue produces and
    # the parser explicitly sums (see pmus EXECUTION_TYPE_PARTIAL_FILL),
    # and the full branch runs an unlimited close whose slippage bound
    # can exhaust a thin book long before the position is gone.
    #
    # So the test is now only about shares. Whatever the whale did, if
    # we still hold something the row stays live and carries it.
    remaining = max(0, int(row["qty"]) - int(booked))
    if remaining > 0:
        # CAPTURE THE ORIGINAL ON THE WAY PAST. This UPDATE is the only
        # thing that destroys it, and right here row["qty"] still holds
        # the pre-sale count -- so the base for every later trim is
        # written once, from the last moment it is true, and COALESCE
        # keeps it fixed on every subsequent partial. Doing it here
        # rather than on the INSERT paths means a new insert site cannot
        # forget it and silently reintroduce the compounding.
        # RELATIVE, NOT ABSOLUTE (adds review, 2026-09-02): the remainder
        # is the row's CURRENT shares less what this sale booked. An add
        # leg merged between the read above and this write grew the row;
        # writing the stale read's remainder would erase those shares
        # from the ledger while the venue still held them. orig_shares
        # is captured from the current column for the same reason.
        await pool.execute(
            "UPDATE live_orders SET status='filled', "
            "orig_shares=COALESCE(orig_shares, filled_shares::float8), "
            "filled_shares=GREATEST(filled_shares - $2::float8, 0), "
            "pnl=COALESCE(pnl,0)+$3 WHERE id=$1",
            row["id"], float(booked), pnl or 0)
    else:
        # ACCUMULATE, DO NOT ASSIGN (2026-08-25, adversarial review).
        #
        # The partial branch above adds with COALESCE(pnl,0)+$3, and
        # this one assigned pnl=$2 four lines later — so the moment a
        # whale who had already trimmed finally closed out, every
        # dollar realised by his earlier partial exits was erased from
        # the row. The position's own P&L, silently restated downward
        # (or upward) by its last leg.
        #
        # It is reachable on exactly the flow this desk exists to copy:
        # a whale who scales out and then closes. Both branches use the
        # same accumulate now, so a row's pnl is the sum of what its
        # legs actually realised however many there were.
        await pool.execute(
            "UPDATE live_orders SET status='cashed_out', "
            "pnl=COALESCE(pnl,0)+$2, settled_at=now() WHERE id=$1",
            row["id"], pnl or 0)
    # AN INCOMPLETE EXIT IS NOT AN APPLIED EXIT (2026-08-26).
    #
    # The partial-full-exit branch below returns mx_partial_full_exit, a
    # PENDING reason, precisely so the position lane brings the residual
    # round again. Recording the ledger first made that retry impossible:
    # the next attempt hits mx_exit_recently_applied (position lane,
    # inside EXIT_DEDUP_MINUTES) or mx_exit_already_mirrored (trade
    # lane) and is refused -- and mx_exit_recently_applied is a SETTLED
    # reason, so the snapshot advances and the residual is lost for good.
    #
    # I introduced both halves of this within an hour of each other and
    # the retry has never been reachable. The row keeps its shares, so
    # nothing is orphaned, but it is never sold either.
    #
    # So the ledger is written only when the exit actually COMPLETED.
    # An unrecorded incomplete exit is exactly what should be re-offered,
    # and the row now carries `remaining` as its quantity, so the retry
    # sizes against what is genuinely left rather than re-selling.
    if _full and remaining > 0:
        log.warning("MIRROR-EXIT %s: full exit filled %d of %d — %d "
                    "share(s) still held, NOT recorded as applied so "
                    "the residual can be retried",
                    us_slug, int(booked), int(row["qty"]), remaining)
        return _exit_done("mx_partial_full_exit", whale=username,
                          slug=us_slug, sold=int(booked),
                          remaining=remaining, pnl=pnl or 0)
    # RECORD IT, now that a sale actually happened.
    if _xtid is None:
        # The position lane has no trade id, so it records against a
        # NEGATIVE synthetic key derived from the row. trade_id is the
        # primary key and real trade ids are positive, so this cannot
        # collide with the trade-driven lane's records; what it does is
        # give the row_id window above something to find.
        try:
            await pool.execute(
                "INSERT INTO copy_exit_applied (trade_id, row_id, "
                "closed_frac) VALUES ($1, $2, $3) "
                "ON CONFLICT (trade_id) DO UPDATE SET "
                "applied_at = now(), closed_frac = EXCLUDED.closed_frac",
                -int(row["id"]), int(row["id"]), float(closed_frac))
        except Exception:  # noqa: BLE001 — the sale already happened;
            # failing to record it must not undo it.
            log.warning("MIRROR-EXIT: sold on the position lane but "
                        "could not record it for row %s — a duplicate "
                        "mirror is possible next cycle", row["id"])
    if _xtid is not None:
        try:
            await pool.execute(
                "INSERT INTO copy_exit_applied (trade_id, row_id, "
                "closed_frac) VALUES ($1, $2, $3) "
                "ON CONFLICT (trade_id) DO NOTHING",
                int(_xtid), int(row["id"]), float(closed_frac))
        except Exception:  # noqa: BLE001 — the sale already happened;
            # failing to record it must not undo it. Next cycle may
            # re-mirror, which is the pre-existing behaviour, and the
            # log says so.
            log.warning("MIRROR-EXIT: sold but could not record exit "
                        "trade %s — a replay is possible next cycle",
                        _xtid)
    _exit_done("mx_SOLD", whale=username, slug=us_slug,
               shares=int(booked), pnl=pnl or 0,
               full=bool(closed_frac >= FULL_EXIT_FRAC))
    log.warning("MIRROR-EXIT %s %s: sold %d of %d @ %.3f (entry %.3f) "
                "pnl %.2f — mirroring his %.1f%% exit, %d left",
                username, us_slug, int(booked), int(row["qty"]),
                float(px or limit or 0), entry, pnl or 0,
                closed_frac * 100, remaining)
    return "mx_SOLD"


async def overspend_halt(pool) -> Any:
    """The tripped-breaker record, or None.

    Read through a named helper rather than inline so the test suite can
    neutralize it in one place — the stub pools answer every fetchval
    with a truthy value, which would otherwise make all 42 downstream
    gate tests pass while testing nothing.

    An UNREADABLE breaker counts as TRIPPED. This guards real money, and
    "the database did not answer" is not evidence that nothing is
    wrong."""
    try:
        rec = await pool.fetchval(
            "SELECT value FROM ingestion_state WHERE key=$1",
            "copy_overspend_halt")
    except Exception:  # noqa: BLE001
        log.exception("overspend breaker unreadable — treating as tripped")
        return {"why": "breaker unreadable — refusing until it can be read"}
    if not rec:
        return None
    # STALE BREACH, NOW GUARDED PRE-TRADE (owner order 2026-08-25:
    # "I want trading live but I want this limit issue resolved").
    #
    # The breaker exists because nothing stood between us and a venue
    # filling above our limit — the only move available was to notice
    # afterwards and stop. The pre-trade ask guard now refuses those
    # orders BEFORE they are sent, so a breach recorded before the guard
    # existed is evidence about a hole that is closed. Keeping the
    # sleeve down for it protects nothing.
    #
    # A breach recorded AFTER the guard is the opposite: it means the
    # guard did not hold, and that must stop everything and stay
    # stopped. So this clears BACKWARDS only, never forwards.
    try:
        _rec = rec if isinstance(rec, dict) else json.loads(rec)
        _at = str(_rec.get("at") or "")
        if _at and _at < ASK_GUARD_SINCE:
            await pool.execute(
                "DELETE FROM ingestion_state WHERE key=$1",
                "copy_overspend_halt")
            log.warning(
                "overspend breaker CLEARED: breach at %s predates the "
                "pre-trade ask guard (%s), which now refuses that "
                "failure mode before the order is sent",
                _at, ASK_GUARD_SINCE)
            return None
        # A RECORD THAT DISPROVES ITSELF (2026-08-26).
        #
        # The breaker records the ratio it fired on. When that ratio is
        # at or below the halting boundary, the record is evidence that
        # the breaker was wrong to write it, not evidence about the
        # venue — and the sleeve is being held down by its own
        # arithmetic. This is not a judgement call and it reads no
        # outside state: the number in the record is compared with the
        # threshold in the code.
        #
        # It is why this clause exists at all. The sleeve sat halted
        # from 2026-08-25 19:20:51Z on a record reading ratio 0.957 —
        # an UNDERSPEND, asked $67.68 and filled $64.80 — written by a
        # breaker that was comparing a short's cost against a long's
        # denomination. That bug is fixed; the record it left could
        # only be cleared by hand.
        #
        # Deliberately NOT time-bounded like the clause above. A false
        # positive is false whenever it was written.
        _ratio = _rec.get("ratio")
        if isinstance(_ratio, (int, float)) \
                and float(_ratio) <= OVERSPEND_HALT_RATIO:
            await pool.execute(
                "DELETE FROM ingestion_state WHERE key=$1",
                "copy_overspend_halt")
            log.warning(
                "overspend breaker CLEARED: it recorded ratio %.3f, "
                "which is at or below the halting boundary %.2f — the "
                "record is evidence the breaker misfired, not evidence "
                "about the venue",
                float(_ratio), OVERSPEND_HALT_RATIO)
            return None
    except (TypeError, ValueError, AttributeError):
        pass  # an unparseable record is not a cleared one
    return rec


def copy_halted() -> bool:
    """True when the owner has re-armed the halt.

    The default is now LIVE (owner decision above), which inverts the
    risk of a typo: it can no longer strand us halted, so it must not
    be able to strand us LIVE when the owner meant to stop. Any
    plausible spelling of "yes, halt" halts — the forgiving direction
    is the one that stops trading."""
    return (os.getenv("LIVE_COPY_HALT", "").strip().lower()
            in _HALT_ON_VALUES)


# THE VERIFIED-PROFITABLE SET — ONE DEFINITION (2026-08-25).
#
# Two gates ask this same question: the verified-only gate and the
# premap-live allowlist. They carried SEPARATE hard-coded defaults, and
# on 2026-08-24 they drifted: swisstony was certified on TRUEEDGE-FAST,
# his hold was lifted and he was added to the verified set — but the
# premap allowlist still named only the original two whales. The result
# was a whale reported as "resumed" who could not place an order,
# refused by an allowlist nobody had updated. A certification decision
# must land in ONE place.
#
# Both gates still exist and still run independently; they just cannot
# disagree about who has been certified. Each stays overridable by its
# own env var for a deliberate, asymmetric change.
# Follows the roster reset above (see PER_FILL_BY_WHALE for the numbers
# and the reasoning). This set gates BOTH the premap-live lane and
# mirror_exit, so a whale missing from it can neither enter nor be
# followed out — which is why it has to move in lockstep with the clip
# map. Divergence between these two is the shape of the 2026-08-24 bug
# where SwissTony was "resumed" everywhere except the one list that
# mattered and placed 2,897 rejections and zero orders.
# HomeRunHazard CUT 2026-08-25 (owner order) on the merge-inclusive
# re-grade. Not a judgement call and not a drawdown reaction — a
# measurement over his whole book:
#
#     HomeRunHazard  -$39,738 realised on $27,560,002 of entries
#                    = -0.14% on dollar deployed, 46,905 closed lots
#
# against the three that stay: 0x076daa87 +2.05%, ferrari +1.66%,
# rn1 +0.94%. He is not a marginal member of that set, he is on the
# other side of zero, and he was our SECOND LARGEST allocation at
# $5,514 deployed in 24 hours.
#
# The old case for him was a settlement-basis read (baseball totals
# +1.89%, WNBA +6.50%) taken before merges were counted as the exits
# they are. Once his exits are priced, the book is negative.
VERIFIED_PROFITABLE_DEFAULT = (
    "0x076daa87,rn1,ferrarichampions2026,swisstony,homerunhazard")
# swisstony + homerunhazard REINSTATED 2026-08-27 (owner order: "Yes!
# Reinstate", on the venue-ledger basis below at COPY_CUT_WHALES).


# ── DB-backed roster override (owner order 2026-08-29: "update the
# variables") ────────────────────────────────────────────────────────
# The stale Render env LIVE_VERIFIED_WHALES silently overrode the
# owner's 2026-08-27 reinstate order for two days: homerunhazard
# (+2.57% at 95%) took 724 straight rejections with $0 deployed while
# the code default said he may spend. Roster decisions are the OWNER's
# and must be updatable without a deploy or console access, so the
# ingestion_state key 'live_verified_whales' now beats the env when
# set (admin endpoint POST /api/admin/verified-whales). Refreshed with
# a short TTL at the top of both money paths.
#
# A READ THAT FAILS IS NOT A ROSTER (disk-full incident 2026-09-04
# 23:15Z to 2026-09-05 ~14:30Z). sportsassets-db filled its disk, Render
# degraded it and its hostname stopped resolving; every roster read
# raised. The stored roster was intact and UNREADABLE, and this reader
# fell through to the five-whale code default -- /api/admin/gates said
# source=default and printed the hardcoded clips for three whales the
# owner had cut to $0. Nothing traded only because the workers were
# down too. "A DB blip is not a roster decision" was the reasoning for
# keeping the last adopted value; it was wrong at boot (nothing adopted
# yet, so 'keep last' fell to the default) and it was wrong for a long
# outage (the last adopted roster may be hours stale, and the owner's
# cuts since then live in the row we cannot read). So the three states
# are now DISTINCT:
#   (1) a row is stored           -> use it
#   (2) no row is stored (None)   -> env, then the code default
#   (3) the read FAILED           -> UNREADABLE: no whale verified, every
#                                    clip 0.0, until a later read SUCCEEDS
# State (3) is a value the readers below adopt (the UNREADABLE sentinel),
# never a fall-through: it is cached like any other adopted value so the
# hot path is not hammering a dead database, and the cached value is
# CLOSED -- not the last good roster, not the default. Reads are retried
# every _CLOSED_RETRY_S while closed, and the transitions (open->closed,
# closed->open) are logged once each, not once per call.
_ROSTER_DB_KEY = "live_verified_whales"
_ROSTER_TTL_S = 30.0


class _Unreadable:
    """The adopted value of the overrides when a read FAILED.

    A distinct object rather than None or {} so state (3) can never be
    mistaken for (2) by an `is None` or an `or {}`: it is truthy and
    not iterable, so any reader that reaches it without going through
    the accessors below fails loudly instead of trading on it."""
    __slots__ = ()

    def __repr__(self) -> str:
        return "UNREADABLE"

    def __iter__(self):
        raise TypeError("UNREADABLE is not a roster; ask "
                        "overrides_unreadable() first")


UNREADABLE = _Unreadable()
# ONE closed state for BOTH reads, held here. The roster and the clips
# are one owner decision (a whale is live only if he is verified AND his
# clip is above zero), read by one call on one TTL; if EITHER read fails
# the pair is unreadable and this holds UNREADABLE, whichever failed.
# The clip map keeps its last value underneath, unread until a later
# refresh reads both and puts a roster back here.
_roster_override: set[str] | _Unreadable | None = None  # None = no row
_roster_read_at = 0.0
# THE RULES' CLIPS (owner order 2026-09-01: roster decisions are made by
# the data). workers/roster_auto.py writes {whale: usd} here every pass;
# per_fill_usd reads it ahead of the hardcoded PER_FILL_BY_WHALE, so a
# measuring whale trades at the measurement clip, a promoted one at the
# full clip, and a demoted one at 0. Refreshed with the roster, on the
# same TTL, by the same call at the top of both money paths.
_CLIPS_DB_KEY = "live_clip_overrides"
_clip_override: dict[str, float] | None = None   # None = no stored clips
# The closed cache: while UNREADABLE the hot path retries no more often
# than this. Failure does not start the 30 s TTL (fleet round 49: the
# next event must retry soon), and this clock is what keeps 'soon' from
# meaning 'every event' against a dead database.
_CLOSED_RETRY_S = 5.0
_closed_read_at = 0.0
# When the gate closed and what the read said, for the gates payload.
_closed_since = 0.0
_closed_error: str | None = None
# One bounded query per read: a primary-key lookup, LIMIT 1 and a
# timeout, so a degraded database costs the money path seconds, not a
# hang (the 2026-09-04 outage was a connect failure; a stalled
# connection would have been worse).
_OVERRIDE_READ_TIMEOUT_S = 5.0
_OVERRIDE_READ_SQL = "SELECT value FROM ingestion_state WHERE key=$1 LIMIT 1"


def overrides_unreadable() -> bool:
    """State (3): the last refresh could not read the roster or the
    clips, and no refresh has succeeded since. While true no whale is
    verified and every effective clip is 0.0."""
    return _roster_override is UNREADABLE


def closed_state() -> dict:
    """What the money paths will DO, for /api/admin/gates: the probe
    must print the executor's decision, not a guess at it."""
    closed = overrides_unreadable()
    return {
        "closed": closed,
        "verified_effective": [] if closed else None,
        "clips_effective": "all 0.0" if closed else None,
        "since": (datetime.fromtimestamp(_closed_since, tz=timezone.utc)
                  .isoformat() if closed and _closed_since else None),
        "last_error": _closed_error if closed else None,
        "retry_s": _CLOSED_RETRY_S,
    }


def _adopt_closed(err: str) -> None:
    """ADOPT the closed value, synchronously. The open->closed edge is
    logged once, decided from module state at the moment of adoption
    rather than from a value captured before an await: several
    refreshes can be in flight at the edge (four copy slots, the exit
    mirror and mirror_live share one loop) and each would otherwise
    log it and restamp `since` (containment review 2026-09-05 counted
    four). `last_error` is the LATEST failure, so an outage whose
    shape changes -- connect refused, then timeouts once the host
    resolves -- is visible in the gates payload while it is on."""
    global _roster_override, _closed_since, _closed_error
    first = _closed_since == 0.0
    # "boot": nothing was ever adopted -- decided BEFORE the overwrite
    # (a forced re-read resets the TTL clock but not the adopted value)
    boot = _roster_override is None and _roster_read_at == 0.0
    _roster_override = UNREADABLE
    _closed_error = err
    if first:
        _closed_since = time.time()
        log.error("roster/clip overrides %s — money paths CLOSED (no "
                  "whale verified, every clip 0.0) until a read "
                  "succeeds: %s",
                  "UNREAD at boot" if boot else "UNREADABLE", err)


def _adopt_open(roster, clips, now: float) -> None:
    """ADOPT the pair, synchronously, after BOTH reads returned. The
    assignments here have no await between them, so no concurrent
    task can see a roster without the clips that go with it."""
    global _roster_override, _clip_override, _roster_read_at
    global _closed_since, _closed_error
    reopened = _closed_since != 0.0
    if roster is not _KEEP:
        _roster_override = roster
    if clips is not _KEEP:
        _clip_override = clips
    _roster_read_at = now
    if reopened:
        _closed_since = 0.0
        _closed_error = None
        log.warning("roster/clip overrides readable again — money paths "
                    "REOPENED on the stored roster and clips")


def close_overrides(err: str) -> None:
    """A refusal UPSTREAM of the read is a failed read. The two money
    paths wrapped `refresh_whale_overrides(await get_pool())` in one
    try, so when get_pool() itself raised (a worker booted while the
    host did not resolve) the refresh never ran and the pre-incident
    fall-through -- code default, hardcoded clips -- stood with no
    closed state at all (containment review 2026-09-05)."""
    global _closed_read_at
    _closed_read_at = time.time()
    _adopt_closed(err)


async def _read_override(pool, key: str):
    return await asyncio.wait_for(
        pool.fetchval(_OVERRIDE_READ_SQL, key),
        timeout=_OVERRIDE_READ_TIMEOUT_S)


# "A row that reads but is not the expected shape": the reader answers
# this and the adopter keeps the last adopted value (while OPEN; while
# CLOSED the readers turn it into an error, because closed reopens only
# onto a value we could read).
_KEEP = object()


async def refresh_whale_overrides(pool) -> None:
    """Adopt the stored roster and the stored clips, on one TTL.

    While CLOSED the cadence is _CLOSED_RETRY_S from the last failed
    attempt, so a dead database is asked again soon but not on every
    event. Both reads must succeed for the pair to reopen; the clips
    are not asked for when the roster already failed (half the traffic
    against a database that is down).

    THE PAIR IS ADOPTED ATOMICALLY (money-safety and containment
    reviews, 2026-09-05, each reproduced on the real functions). The
    first build published the roster -- and started the open TTL --
    inside the roster read, before the clip read was attempted. Against
    a half-alive database (roster row returns, clip read hangs, the
    15:27 "refusing connections again" shape) that left the gate OPEN
    for up to two read timeouts on a roster the database had just
    served and whatever clip map sat underneath: None at boot, i.e. the
    hardcoded $250/$100/$250 the incident printed. A concurrent event
    in this process skipped its own refresh (the TTL was fresh),
    admitted swisstony and sized him at $250; then the clip read timed
    out and the pair snapped closed, after the order left. Now the two
    reads return VALUES and write nothing; nothing is adopted until
    both are in hand, in one synchronous step (_adopt_open /
    _adopt_closed)."""
    global _closed_read_at
    now = time.time()
    if overrides_unreadable():
        if now - _closed_read_at < _CLOSED_RETRY_S:
            return
        _closed_read_at = now
    elif now - _roster_read_at < _ROSTER_TTL_S:
        return
    err, roster = await _refresh_roster(pool)
    clips = _KEEP
    if err is None:
        err, clips = await _refresh_clips(pool)
    if err is None and roster is _KEEP and overrides_unreadable():
        err = "roster: nothing to reopen on"    # belt: closed reopens onto a roster only
    # ---- ADOPT: no await from here to the return ----
    if err is not None:
        _closed_read_at = now
        _adopt_closed(err)
    else:
        _adopt_open(roster, clips, now)


async def _refresh_clips(pool) -> tuple[str | None, object]:
    """READ {whale: usd} from ingestion_state: (None, value) or
    (error, None). The value is None (no row), a map, or _KEEP (a row
    that reads but is not a map: keep the last adopted map while OPEN;
    while CLOSED that is an error, because closed reopens only onto a
    map we could read -- otherwise a boot-closed process reopened onto
    None, i.e. the hardcoded clips). Writes nothing: the caller adopts
    the pair atomically.

    A stored cell that cannot be read as a number is dropped from the
    map for this pass, not zeroed and not guessed: the whale then falls
    to the hardcoded clip, which is what he traded at before the rules
    existed. A read that FAILS closes the pair -- every clip is 0.0
    until a later read succeeds (2026-09-05: the old 'keep the last
    adopted map' printed the hardcoded fallbacks for three whales the
    owner had cut to $0 while the database was down)."""
    err: str | None = None
    for attempt in (1, 2):
        try:
            raw = await _read_override(pool, _CLIPS_DB_KEY)
            if raw is None:
                return None, None
            val = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(val, dict):
                out: dict[str, float] = {}
                for w, v in val.items():
                    try:
                        out[str(w).strip().lower()] = max(0.0, float(v))
                    except (TypeError, ValueError):
                        continue
                return None, out
            if overrides_unreadable():
                return "clips: stored value is not a map", None
            # any other shape while OPEN: keep last known — never guess a clip
            return None, _KEEP
        except Exception as exc:  # noqa: BLE001 — retry once, then CLOSE
            err = f"clips: {type(exc).__name__}: {str(exc)[:120]}"
    return err, None


async def _refresh_roster(pool) -> tuple[str | None, object]:
    """READ the stored roster: (None, value) or (error, None). The
    value is None (no row), a set, or _KEEP (a row that reads but is
    not a roster: keep the last adopted set while OPEN; while CLOSED
    that is an error). Writes nothing: the caller adopts the pair
    atomically, and the TTL clock is started only by a full success."""
    # Two attempts per refresh (fleet round 49: a fresh worker whose
    # FIRST read hit a dead pooled connection fell to the env/default
    # roster in total silence — 'keep last adopted' is vacuous before
    # anything was adopted, and one retry usually lands on a healthy
    # connection). A read that still fails CLOSES the gate.
    err: str | None = None
    for attempt in (1, 2):
        try:
            raw = await _read_override(pool, _ROSTER_DB_KEY)
            if raw is None:
                return None, None
            val = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(val, str):
                val = [w for w in val.split(",")]
            if isinstance(val, list):
                return None, {str(w).strip().lower()
                              for w in val if str(w).strip()}
            if overrides_unreadable():
                # a row that reads but is not a roster is not a roster:
                # stay closed rather than guess (2026-09-05)
                return "roster: stored value is not a list", None
            # any other shape while OPEN: keep last known — never guess a roster
            return None, _KEEP
        except Exception as exc:  # noqa: BLE001 — retry once, then CLOSE
            err = f"roster: {type(exc).__name__}: {str(exc)[:120]}"
    return err, None


def _whale_set(env_name: str) -> set[str]:
    if env_name == "LIVE_VERIFIED_WHALES":
        if overrides_unreadable():
            # state (3): nobody. The entry gate does not read this as
            # 'empty disables the gate' -- see _mapping_admitted, which
            # asks overrides_unreadable() first.
            return set()
        if _roster_override is not None:
            return set(_roster_override)
    raw = os.getenv(env_name, VERIFIED_PROFITABLE_DEFAULT)
    return {w.strip() for w in raw.lower().split(",") if w.strip()}


def _row_get(row, key, default=None):
    """Read a column that may be absent from this row's projection.

    Production's mirror_exit SELECT always projects orig_qty, on both the
    migrated and the fallback branch. This exists so a row reaching the
    sizing arithmetic from anywhere else degrades to the legacy base
    instead of raising: an exception there does not mis-size a sale, it
    CANCELS one, and a cancelled exit is the failure this whole change is
    meant to prevent. asyncpg Records and dicts both index by key but
    neither is guaranteed to carry a column nobody selected.
    """
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


# The in-flight wait in mirror_exit: how often the row is re-read while
# an entry settles, and how much longer than the rest window to allow
# for the cancel and the terminal reads. Module constants so a test can
# drive the wait in milliseconds instead of reading the source.
_INFLIGHT_POLL_S = 1.0
_INFLIGHT_GRACE_S = 5.0
# How far back a 'submitting' row can be and still be an entry in
# flight rather than an abandoned row. A rest_unknown row stays
# 'submitting' until the ledger reaper reconciles it at ten minutes, so
# a row inside the horizon may yet become a fill; its whale's exit is
# pinned in that window, not dropped (round three: a 60-second horizon
# made a ten-minute rest_unknown row invisible after its first minute;
# round six: the reaper runs once per sweep pass, which can take ten
# minutes itself, so fifteen was not past its worst case). A row the
# reaper marked "venue holds a POSITION ... no ledger row explains"
# counts as in flight too: the account holds shares the ledger cannot
# yet name, and the exit must wait for the reconcile, not be dropped.
_INFLIGHT_HORIZON = "30 minutes"
# How long a row NAMED "venue holds a POSITION ..." stays in flight for
# the exit path and is revisited by the ledger reaper. Long, because it
# describes shares the account holds that the ledger cannot yet name:
# dropping the exit on those would be the round-six defect with a delay.
_NAMED_HORIZON = "48 hours"


async def _position_row(pool, asset: str, username: str, sel_tail: str):
    """Our newest 'filled' row on this asset for this whale, projecting
    orig_qty with the migration-040 fallback: the workers never run
    migrations, so this code can reach production ahead of its own
    column, and a bare reference would raise on EVERY exit until a
    healthy API boot happened to land. One helper, both lookups."""
    try:
        return await pool.fetchrow(
            "SELECT id, us_market_slug, filled_shares::float8 AS qty, "
            "       COALESCE(orig_shares, filled_shares)::float8 AS orig_qty, "
            + sel_tail, asset, username)
    except Exception as exc:  # noqa: BLE001 — missing column only
        if "orig_shares" not in str(exc):
            raise
        log.warning("MIRROR-EXIT: orig_shares column absent (migration "
                    "040 not applied yet) — sizing off filled_shares, "
                    "which is today's behaviour; repeated trims may "
                    "over-exit until the migration lands")
        return await pool.fetchrow(
            "SELECT id, us_market_slug, filled_shares::float8 AS qty, "
            "       filled_shares::float8 AS orig_qty, "
            + sel_tail, asset, username)


async def _entry_in_flight(pool, asset: str, username: str):
    """(id, age_s, readable) of a same-asset row still 'submitting'
    inside the horizon. readable=False means the lookup itself failed:
    the caller then PENDS the exit (an entry may be in flight and we
    cannot tell) rather than filing it as "no position" -- round four:
    a DB blip here used to drop the exit."""
    try:
        r = await pool.fetchrow(
            "SELECT id, EXTRACT(EPOCH FROM (now() - placed_at))::float8 "
            "AS age_s FROM live_orders WHERE asset = $1 AND "
            "lower(COALESCE(whale_username,'')) = $2 AND us_market_slug IS "
            "NOT NULL AND ((status = 'submitting' AND "
            f"placed_at > now() - interval '{_INFLIGHT_HORIZON}') "
            "OR (status = 'error' AND (error LIKE 'venue holds a POSITION%' "
            "OR error LIKE 'venue has no record of order%') "
            f"AND placed_at > now() - interval '{_NAMED_HORIZON}')) "
            "ORDER BY placed_at DESC LIMIT 1", asset, username)
    except Exception:  # noqa: BLE001
        return None, None, False
    if not r:
        return None, None, True
    try:
        return r["id"], float(_row_get(r, "age_s") or 0.0), True
    except (TypeError, ValueError):
        return r["id"], None, True


def overspend_ratio(requested_usd: float, filled_shares: float,
                    fill_price: float | None,
                    intent: str | None = None) -> float | None:
    """How much of the AUTHORIZED clip a fill actually consumed.

    The per-fill ceiling is enforced before submit, on
    `requested_usd = shares * limit`. That bounds what we ASK to spend.
    It bounds what the venue TAKES only while an IOC buy cannot fill
    above its own limit — an assumption, not a control. This turns the
    assumption into a number: > 1.0 means the venue took more than we
    authorized, and no clip cap in the sizing map can see it.

    THE FIFTH PLACE THE SHORT DENOMINATION LIVED (2026-08-25, found by
    an adversarial review of my own diff). `spent = filled * fill_price`
    was converted in fill_cash, realized_pnl, wire_limit and the price
    fidelity scorer. This function computed it a fifth time, inline,
    long-only — and it is the one wired to a BREAKER.

    On a short, cost is (1 - price) x qty. Take the venue's own receipt:
    1,136 shares authorized at $249.92, filled at 0.78. The real cost is
    1136 x 0.22 = $249.92, EXACTLY the authorization, zero overage. The
    long formula reads 1136 x 0.78 / 249.92 = 3.545 and trips at 1.01.

    So the first correctly-priced short fill would halt the entire copy
    sleeve — every whale, both legs, via overspend_halt at the top of
    maybe_execute and mirror_exit — and it cannot self-clear, because
    the clear only removes records stamped before ASK_GUARD_SINCE. A
    manual admin clear, triggered by a trade that did nothing wrong.

    Worse, the halt RECORD already wrote the corrected ratio
    (spent / usd = 1.0) beside a predicate that fired on 3.545: the
    same block would have reported two different numbers for one fill,
    one of them exonerating.

    It takes the intent and defers to fill_cash rather than restating
    the rule a sixth time. Default None preserves the long formula
    exactly, so every existing caller and every long fill is unchanged.

    None when there is nothing to judge (no fill, or no request)."""
    if not requested_usd or requested_usd <= 0:
        return None
    if not filled_shares or filled_shares <= 0 or not fill_price:
        return None
    return round(fill_cash(filled_shares, fill_price, intent)
                 / requested_usd, 4)


# ONE DECISION — HOW MUCH OVERSPEND IS ACCEPTABLE — WAS ENCODED IN TWO
# LITERALS THAT DISAGREED (2026-08-26, adversarial round 4).
#
# The pre-trade ask guard ADMITS a fill whose ask is up to 1.08x our
# limit, and says so: "worst case this now permits is an 8% overspend on
# a clip — about $20 — against losing every copy". It was widened to
# exactly that after `ask > limit` made the sleeve sterile within the
# hour.
#
# The post-fill breaker then halted the ENTIRE sleeve — every whale,
# every entry, every exit — above 1.01x, and its halt record self-clears
# BACKWARDS ONLY, so the halt is permanent until a human edits the
# database.
#
# The two populations this was calibrated against are recorded in
# tests/test_ask_guard.py. Every honest refusal the 1.08 threshold was
# raised to admit trips the 1.01 breaker:
#
#   limit 0.57, ask 0.59  ->  ratio 1.035   admitted, then HALTS
#   limit 0.88, ask 0.89  ->  ratio 1.011   admitted, then HALTS
#   limit 0.84, ask 0.85  ->  ratio 1.012   admitted, then HALTS
#
# The whole premise of the ask guard is that our limit is not enforced
# at execution — "the order behaves like a market IOC and takes the
# book". So a fill AT the admitted ask is the expected outcome, and the
# expected outcome bricked the sleeve. One ordinary two-cent copy stops
# everything until someone opens the database, which is the same shape
# as the short-fill halt found in the previous pass: a guard that blocks
# everything, reporting itself as safety.
#
# The fix is not a looser breaker. It is to stop conflating two
# questions that have different answers:
#
#   RECORDING — "did the venue charge more than we asked?" Anything at
#   all above rounding is worth stamping on the row and counting.
#   Unchanged at 1.01. Nothing becomes invisible.
#
#   HALTING — "is this evidence of the wrong-side defect, which must
#   stop the whole sleeve?" That question was already answered on real
#   data when the ask guard was calibrated: worst honest 1.035, cheapest
#   real overspend 1.146. The wrong-side population runs 1.15x-3.87x. A
#   halt anywhere in that gap catches 100% of the incident it was built
#   for; at 1.01 it also catches every honest copy.
#
# So the halt boundary IS the ask tolerance, derived rather than
# restated. A fill at or under what the pre-trade guard AUTHORIZED
# cannot be evidence that something went wrong — it is the thing we
# asked for. max() rather than a plain env read so the halt can never
# sit below what we authorize: to tighten the halt, tighten
# LIVE_ASK_TOLERANCE_PCT and the halt follows it down.
# MAPPING CLASSES THE RESUME LANE ADMITS WHILE THE QUARANTINE HOLDS.
#
# Owner decision 2026-08-26, taken on the probe that showed the sleeve
# refusing every copy it successfully mapped:
#
#   SHADOW-CERT ok=691  MISMATCH=0  unverified=0
#   QUARANTINE true  premap_live=true
#   MAPA-Q rn1 pick=Washington Mystics quarantined (src=exact)
#
# The quarantine's own release condition -- an independent resolver
# re-deriving each refused mapping and voting -- has been satisfied 691
# times with no dissent, and the vector the quarantine was raised for is
# separately closed: every one of the six wrong-side receipts was an
# ORDER_INTENT_BUY_SHORT, and that branch is refused outright further
# down this function.
#
# The owner chose the NARROW widening: admit `exact`, keep `fuzzy`
# quarantined. That is a real distinction rather than a convenient one.
# `exact` is grammar-to-grammar resolution through resolve_market_exact
# / resolve_derivative_exact, and the mapping code says of it:
# "anything short of full corroboration falls through to the fuzzy
# pipeline exactly as before". `fuzzy` is the guessing class, and it is
# the class the 2026-08-23 incident came through.
#
# `exact` rides the SAME resume lane as `premap` -- same whale
# allowlist, same premap_live operator switch -- so this widens exactly
# one thing and inherits every other gate unchanged.
QUARANTINE_RESUME_SRC = frozenset({"premap", "exact"})
# Where the fuzzy class's certification streak accumulates. Its own key
# on purpose: see the state_key note in _side_echo_verify.
FUZZY_CERT_KEY = "side_echo_fuzzy"
# The per-team Yes/No lane certifies under ITS OWN counter, for the
# same reason fuzzy does: a new class must never dilute the streak
# that justified an old resume (2026-08-26 rule).
YESNO_CERT_KEY = "side_echo_yesno"
# TWO DETECTORS, ONE ECONOMIC EXIT (2026-08-26, adversarial round 4).
#
# classify_exit sees the whale's exit as a complement BUY in the trade
# feed, seconds after it happens. whale_exits sees the SAME exit as a
# drop in his /positions snapshot, up to 120s later. Both call
# mirror_exit and nothing correlates the two observations.
#
# copy_exit_applied dedupes the trade-driven lane by trade_id, but the
# position lane carries no trade id, so that guard cannot see it. The
# atomic status='exiting' claim serializes the two callers and does not
# deduplicate them: the first sale writes the row back to 'filled' with
# the remainder, and the second claims that row and sells a fraction of
# what is LEFT.
#
# He trims 30% of a position we hold 200 of. The trade lane sells 60 and
# leaves 140. Two minutes later the position lane reads the same 30%
# drop, takes 30% of 140, and sells 42 more. We are 51% out of a
# position he is 30% out of, and we paid two spread crossings to get
# there.
#
# Keyed on the ROW rather than the trade, because the row is the only
# identifier both lanes share. A window rather than a permanent record
# because he does genuinely trim twice; ten minutes is five whale_exits
# cycles, comfortably longer than the lag between his fill reaching the
# trade feed and reaching his positions payload.
#
# The cost of the window is a second genuine trim inside it being
# missed. That is the right side to err on: under-selling leaves us in a
# position he is partly out of, over-selling exits a position he is
# still in and pays a spread to do it.
EXIT_DEDUP_MINUTES = int(os.environ.get("LIVE_EXIT_DEDUP_MIN", "10"))


def exitable_whales() -> set[str]:
    """Whales whose open positions we are allowed to SELL.

    A named function rather than an expression inlined in mirror_exit,
    so a test can assert against the thing production actually uses. The
    first version of this fix was pinned by tests that rebuilt the set
    themselves, and restoring the bug did not fail one of them -- an
    instrument reading something other than its subject, which is the
    recurring defect in this codebase.

    Deliberately WIDER than the verified set. The verified set is an
    ENTRY gate; applying it to exits means cutting a whale strands every
    position we already copied from him, held to resolution against a
    whale who has left it. Selling cannot create exposure, and
    mirror_exit's row query already scopes to a 'filled' row for that
    exact whale and asset.

    Still bounded to whales we have ACTUALLY ROSTERED -- verified, cut,
    or carrying a clip entry, hardcoded or stored by the rules -- so a
    username that was never on the roster is refused. The stored map
    matters for a DEMOTED whale: the rules take him off the entry roster
    and write his clip as 0, and that 0 is what keeps his exits
    mirroring here after he has left every other set.

    UNREADABLE clips (2026-09-05) contribute nobody: the stored map is
    the only thing that names a rules-only whale, and a map we cannot
    read names no one. The hardcoded sets still stand, so a hardcoded
    whale's exits keep mirroring through the outage.
    """
    stored = set() if overrides_unreadable() else set(_clip_override or {})
    return (_whale_set("LIVE_VERIFIED_WHALES") | set(COPY_CUT_WHALES)
            | set(PER_FILL_BY_WHALE) | stored)
ASK_TOLERANCE = 1.0 + float(os.getenv("LIVE_ASK_TOLERANCE_PCT", "0.08"))
OVERSPEND_TOLERANCE = 1.01  # a cent of rounding on a whole-unit fill
OVERSPEND_HALT_RATIO = max(
    ASK_TOLERANCE,
    float(os.environ.get("LIVE_OVERSPEND_HALT_RATIO", "1.08")))


def is_overspend(requested_usd: float, filled_shares: float,
                 fill_price: float | None,
                 intent: str | None = None) -> bool:
    """Did the venue charge more than we asked? RECORDING, not halting."""
    r = overspend_ratio(requested_usd, filled_shares, fill_price, intent)
    return r is not None and r > OVERSPEND_TOLERANCE


def is_halting_overspend(requested_usd: float, filled_shares: float,
                         fill_price: float | None,
                         intent: str | None = None) -> bool:
    """Is this the wrong-side defect, which must stop the whole sleeve?

    Strictly implies is_overspend — OVERSPEND_HALT_RATIO cannot be below
    OVERSPEND_TOLERANCE while ASK_TOLERANCE is above it, and a test pins
    that ordering.
    """
    r = overspend_ratio(requested_usd, filled_shares, fill_price, intent)
    return r is not None and r > OVERSPEND_HALT_RATIO


# MIRROR HIS SIZE, NEVER EXCEED IT (owner order 2026-08-25).
#
# The effective production ratio was above 70 — a $3.46 whale probe
# became a $249.92 position of ours, while his $2,907 conviction trade
# got 0.1x. Our sizing was inverted against his own risk allocation on
# every copy we have ever placed.
#
# 1.0 means "take the size he takes". At the measured 0.92% fill rate
# that lands on the owner's halved turnover target (ratio needed 1.004).
# It is a CLAMP rather than a default because the env var overrides the
# config and I cannot read Render from here; a clamp can only shrink a
# copy, so it is safe without knowing what it is shrinking from.
COPY_RATIO_MAX = float(os.environ.get("LIVE_COPY_RATIO_MAX", "1.0"))

# Dust floor. At ratio 1.0 his smallest probes become $3-5 orders where
# spread and fees dominate the edge. Skipping them concentrates us on
# the trades he actually commits to — and it is a tightening, so it
# needs no further authority.
COPY_MIN_CLIP_USD = float(os.environ.get("LIVE_MIN_CLIP_USD", "10"))
# ADDS (owner order 2026-09-02, from the gate-edge read: the trades
# never-add refused returned +9.6% [-3.8%, +23.1%] at his price on 818
# games -- his adds are at least as good as his firsts). A fresh BUY of
# HIS on a market where a 'filled' row of his already stands is copied
# as an ADD: sized by the same rules, placed the same way, and on a fill
# MERGED into the standing row (shares, cost, average price), because
# the ledger holds one live row per asset and every exit, settlement and
# record reads that one row. The add's own row records the leg as
# 'add-merged'. Fresh detections only (a sweep reclaim is hours old);
# at most LIVE_MAX_ADDS legs on one row; never across whales.
ADDS_ENABLED = os.environ.get("LIVE_ADDS", "on").lower() in ("1", "on", "true", "yes")
MAX_ADDS_PER_MARKET = int(os.environ.get("LIVE_MAX_ADDS", "3"))


def plan_order(
    his_price: float, his_notional: float, ratio: float,
    max_per_fill: float, max_slippage_cents: float,
    whole_units: bool = False,
) -> tuple[float, float, float]:
    """Pure sizing/pricing: (limit_price, requested_usd, requested_shares).

    whole_units=True (Polymarket US): whole-cent limit price and integer
    contract count, rounding down so the cost never exceeds the budget.

    PROPORTIONAL, AND CLAMPED (owner order 2026-08-25: "make the switch
    to ensure proportional trades", turnover target halved to 0.5x
    daily).

    Measured: at a 0.92% fill rate, 0.5x daily turnover works out to a
    ratio of 1.004 — mirror his size. So the policy is simply "we take
    the size he takes", bounded by the per-clip cap.

    The clamp exists because the ratio is an ENV value and the env was
    lying. The config default is 0.001, yet production placed $249.92
    against a $3.46 trade — 72x his size — which requires an effective
    ratio above 70. Whatever LIVE_COPY_RATIO is set to out there, it is
    not what anyone intended, and I cannot read or change Render from
    here. So the code refuses to exceed COPY_RATIO_MAX regardless of
    what the env says. It can only ever make a copy SMALLER, which is
    why it is safe to apply without knowing the current value."""
    ratio = min(float(ratio), COPY_RATIO_MAX)
    if whole_units:
        limit = round(min(his_price + max_slippage_cents / 100.0, 0.99), 2)
        usd_budget = min(ratio * his_notional, max_per_fill)
        shares = float(int(usd_budget / limit)) if limit > 0 else 0.0
        usd = round(shares * limit, 2)
        return limit, usd, shares
    limit = round(min(his_price + max_slippage_cents / 100.0, 0.99), 3)
    usd = round(min(ratio * his_notional, max_per_fill), 2)
    shares = round(usd / limit, 2) if limit > 0 else 0.0
    return limit, usd, shares


async def _caps_room(pool) -> tuple[float, float]:
    """Remaining (daily, total) bankroll room from actual filled orders.

    Manual-desk rows are excluded: the admin's directed trades ride
    their OWN budget (execute_manual) and must never consume the copy
    sleeve's room — nor the reverse."""
    cfg = settings()
    row = await pool.fetchrow(
        """
        SELECT COALESCE(sum(filled_usd) FILTER (WHERE placed_at > now() - interval '24 hours'), 0)
                   ::float8
               -- ADD LEGS SPEND ON THEIR OWN CLOCK (adds review,
               -- 2026-09-02): a leg's dollars are booked onto its
               -- standing row, whose placed_at may be older than the day
               -- window; the legs merged inside the window onto rows
               -- outside it are added from the rows' own leg list, so
               -- the daily cap sees every dollar spent today.
               + COALESCE((SELECT sum((a->>'usd')::float8)
                             FROM live_orders lo2,
                                  jsonb_array_elements(COALESCE(lo2.raw->'adds', '[]'::jsonb)) a
                            WHERE COALESCE(lo2.whale_username, '') NOT IN ('manual', 'underdog')
                              AND lo2.placed_at <= now() - interval '24 hours'
                              AND (a->>'ts')::float8 > extract(epoch FROM now() - interval '24 hours')),
                          0)::float8 AS day,
               COALESCE(sum(filled_usd), 0)::float8 AS total
        FROM live_orders
        WHERE COALESCE(whale_username, '') NOT IN ('manual', 'underdog')
        """
    )
    return (cfg.live_max_daily_usd - row["day"], cfg.live_max_total_usd - row["total"])


async def _is_paused(pool) -> bool:
    """The admin kill switch. AN UNREADABLE SWITCH COUNTS AS ENGAGED.

    This guards real money and "the database did not answer" is not
    evidence that trading is safe — the same stance overspend_halt
    already takes, and it was missing here. The read used to propagate,
    which on the exit path meant an exception, which meant an outcome
    the caller could not classify and therefore treated as settled: a
    DB blip would have discarded the exit rather than holding it.

    A malformed value is engaged too. Somebody wrote something we do
    not understand into the kill switch row; refusing is the only
    reading of that which cannot lose money.
    """
    try:
        val = await pool.fetchval(
            "SELECT value FROM ingestion_state WHERE key=$1", PAUSE_KEY)
    except Exception:  # noqa: BLE001
        log.exception("kill switch unreadable — treating as PAUSED")
        return True
    if val is None:
        return False
    try:
        parsed = json.loads(val) if isinstance(val, str) else val
    except (TypeError, ValueError):
        log.warning("kill switch value is not JSON — treating as PAUSED")
        return True
    return bool(parsed)


def _get_client():
    """Lazy sync CLOB client (py-clob-client) — built once per process."""
    global _client
    if _client is not None:
        return _client
    from py_clob_client.client import ClobClient

    cfg = settings()
    kwargs: dict[str, Any] = {"key": cfg.pm_private_key, "chain_id": 137}
    if cfg.pm_signature_type in (1, 2) and cfg.pm_funder:
        kwargs["signature_type"] = cfg.pm_signature_type
        kwargs["funder"] = cfg.pm_funder
    client = ClobClient(cfg.clob_api_base, **kwargs)
    client.set_api_creds(client.create_or_derive_api_creds())
    _client = client
    return _client


def _submit_fok(token_id: str, price: float, shares: float,
                sell: bool = False) -> dict:
    """Sync order submission; returns a normalized result dict.

    sell=True places the SELL side (underdog cash-out sleeve, owner
    directive 2026-08-08) — same FOK contract, opposite side."""
    from py_clob_client.clob_types import OrderArgs, OrderType
    from py_clob_client.order_builder.constants import BUY, SELL

    client = _get_client()
    order = client.create_order(
        OrderArgs(token_id=token_id, price=price, size=shares,
                  side=SELL if sell else BUY)
    )
    resp = client.post_order(order, OrderType.FOK)
    ok = bool(resp.get("success")) if isinstance(resp, dict) else False
    order_id = resp.get("orderID") if isinstance(resp, dict) else None
    status = (resp.get("status") or "").lower() if isinstance(resp, dict) else ""
    fill_price = price
    if ok and order_id:
        # Try to recover the actual average fill price from the order record.
        try:
            rec = client.get_order(order_id)
            maker_amt = float(rec.get("size_matched") or shares)
            px = rec.get("price")
            if px:
                fill_price = float(px)
            shares = maker_amt or shares
        except Exception:  # noqa: BLE001 — audit still records the limit price
            pass
    return {"ok": ok, "order_id": order_id, "status": status,
            "fill_price": fill_price, "filled_shares": shares if ok else 0.0,
            "raw": resp if isinstance(resp, dict) else {"raw": str(resp)}}


def active_venue() -> str | None:
    """Which live venue is armed: 'polymarket-us' wins when its keys are set."""
    cfg = settings()
    if not cfg.live_trading_enabled:
        return None
    if cfg.pmus_key_id and cfg.pmus_secret_key:
        return "polymarket-us"
    if cfg.pm_private_key:
        return "polymarket-clob"
    return None


async def _market_context(pool, payload: dict) -> dict:
    """Slug/title/outcome for US mapping — from the payload when enriched,
    otherwise from the metadata tables."""
    keys = ("market_slug", "event_slug", "market_title", "event_title", "outcome")
    ctx = {k: payload.get(k) for k in keys}
    # THE SHORT-CIRCUIT WAS DROPPING A KEY LANE (2026-08-25).
    #
    # premap.resolve builds its key set from THREE sources — market
    # title, event title, and the global slug. Returning as soon as
    # slug+outcome are present meant a payload carrying those two but
    # no titles never reached the lookup below, and resolve then built
    # its keys from one source instead of three. On the live lane
    # event_title is set only inside _enrich, which is spawned one line
    # AFTER execute_copy, so the copy path wins that race and reads
    # nothing.
    #
    # Requiring a title before short-circuiting costs one indexed
    # lookup on the rows that were previously guaranteed to map badly.
    if (all(ctx.get(k) for k in ("market_slug", "outcome"))
            and (ctx.get("event_title") or ctx.get("market_title"))):
        return ctx
    row = await pool.fetchrow(
        """
        SELECT m.slug AS market_slug, m.event_slug, m.title AS market_title,
               m.event_title, mt.outcome
        FROM market_tokens mt JOIN markets m ON m.condition_id = mt.condition_id
        WHERE mt.token_id = $1
        """,
        str(payload.get("asset")),
    )
    if row:
        for k in keys:
            ctx[k] = ctx.get(k) or row[k]
    return ctx


# COPY MODE (2026-08-02, owner-directed). History, because it earns the
# design: on 2026-08-02 this executor was first suspected of being the
# day's "unauthorized trader" and hard-killed; the data exonerated it
# (dormant — zero probes in 30d) and convicted the edge engine's arb
# path instead. The same day, a public-tape decay study measured the
# source whale's edge surviving a copier's 15-60s latency at 1.3-1.5c
# of his 2.3c — positive enough that the owner directed a live trial at
# the venue MINIMUM: ONE CONTRACT per fresh source-whale BUY. Penny
# scale, real money, hard daily ceiling — the adverse-selection haircut
# the tape cannot measure (we fill preferentially on his mediocre
# entries) gets measured by settled dollars instead.
#   "off"         — places nothing, regardless of env or config
#   "penny_trial" — 1 contract per copy, PENNY_TRIAL_DAILY_USD ceiling
#   "full"        — ratio sizing per config; requires the trial cohort
#                   to have cleared the promotion gate first
COPY_MODE = "penny_trial"
# Owner directive 2026-08-04 (evening): "lift the maximum on copy
# trades." At $2-3 clips the sleeve's natural spend is bounded by whale
# activity itself (~$300-600/day), so $1000 is effectively unbinding —
# it survives only as a runaway-bug breaker, which no live spend path
# should ever be without. History: $50 -> $100 -> $200 -> $400 -> $1000.
# Owner directive 2026-08-04: NO total-volume cap on copies — the per-fill
# clip and the once-per-whale-position rule are the limits; total volume
# is naturally bounded by what the source whales actually trade. The env
# knob remains for the day the owner wants a ceiling back.
PENNY_TRIAL_DAILY_USD = float(os.environ.get("COPY_DAILY_USD", "inf"))
# Owner directive 2026-08-05: no LIFETIME volume cap either. The config's
# live_max_total_usd ($500) predates that directive and silently bound
# for the first time at 21:24Z 2026-08-05 — lifetime filled crossed
# $500.65 and every copy from every path no-opped for hours while all
# heartbeats read healthy. In penny_trial the trial knobs are the
# authority; the config caps govern only the future "full" mode.
PENNY_TRIAL_TOTAL_USD = float(os.environ.get("COPY_TOTAL_USD", "inf"))
# Owner directive 2026-08-08: RN1 and SwissTony to $10 per trade — the
# $5->$10 promotion gated on their own settled cohorts (RN1 +$198.66
# over 304 settled, SwissTony +$139.61 at 64% over 176 — a week of
# consistently green days each). Everyone else STAYS at $5: HRH's
# widened cells have 2 settled, kch123's sports are out of season.
# As many whole contracts as the clip buys at the limit, rounded DOWN
# so a copy never exceeds the budget. Price tolerance unchanged: his
# price +2% relative, floored to the venue's cent tick; FOK gives
# same-or-better for free. Grading stays per-whale/per-band. (History:
# $2 default 2026-08-04; RN1 $3 same day; $3 uniform 2026-08-05; $5
# uniform 2026-08-07; RN1/SwissTony $10 2026-08-08.)
# Owner directive 2026-08-10 evening: the Polymarket deposit landed —
# PMUS clips scale to parity with the Kalshi leg: RN1 $100, everyone
# else $50. SwissTony raised to $200 in the 2026-08-11 profitability
# review — strongest measured earner (+$760 on 345 settled at $100).
# The underdog cash-out sleeve is NOT this map — it stays at its own
# $2 constant (workers/underdog.py PER_FILL_USD, v2 2026-08-12).
# 2026-08-17 evening (owner order): "increase all copy trades by 50%" —
# default and every cell below x1.5; blocked 0.00 cells stay blocked.
PENNY_TRIAL_PER_FILL_USD = 75.00
# Per-whale override map; keys are lowercased usernames; anyone absent
# gets the default. RN1 $100 -> $150 2026-08-11 (owner approval,
# round 4): profitable every single day since Aug 3.
# swisstony (owner orders 2026-08-12, the soccer resume): $200 clip
# everywhere EXCEPT soccer, which limits at $100 per event — the
# per-(whale, sport) override map carries the exception.
# 2026-08-17 pm (owner order: maximize copy capture + margin per dollar):
# clips follow the whales' measured per-sport edge from the tracker week —
# see kalshi_copies.py for the numbers. A 0.00 cell is a BLOCK (skip).
_W2C33 = "0x2c335066fe58fe9237c3d3dc7b275c2a034a0563-1759935795465"
# kch123 $150 (owner go 2026-08-20 morning, allocation re-cut): the
# highest-ROI whale on the roster (+7.21% lifetime, +24.55% on spreads
# at $28.1M donor scale) — already active+pinned since migration 012,
# idle only because his cells (NBA/NFL spreads+totals, NHL moneyline)
# are out of season; sized now so the first ball of his season copies
# at the studied clip, not the $75 default.
# 0x2c33 225 -> 300 and HRH 75 -> 112.50 (owner mandate 2026-08-20
# midday, maximum-profitability pass): 0x2c33 carries the best residual
# ROI at our real latency on the whole board (+0.27%/$1k over 4,620
# 30-day probes; vetting graded +0.76%/$1k over 1,712) and his settled
# copies are positive; HRH's cell-gated book is 12W-8L with the 50-95c
# band guard doing its job. Both raises are bounded (+33%/+50%), not
# leaps — the settled samples are still small.
# TRUEEDGE CUTS + UPSIZE (owner order 2026-08-24, from the verified
# counterfactual table on the FULL detected book, settled on each
# whale's own venue — no fill-selection bias, untouched by the
# settlement incident):
#   rn1 cf_total −4,468, ferrari −10,513, 0x2c33 −29,638 → their books
#   are negative AT THEIR OWN PRICES. Not copyable at any speed; their
#   clips are 0.00 (a 0 cell is a BLOCK) and COPY_CUT_WHALES refuses
#   them at entry. Detection continues — data is free, dollars are not.
#   homerunhazard cf_total +26,076 (paper at our real latency +15,051)
#   and 0x076 +6,189 (paper +5,772) → upsized to the $300 clip the
#   formerly-largest sleeves carried ("match what our sizing was for
#   profitable whales"). swisstony (+11,895 at his prices, latency-cost
#   −14,835 at the OLD ~74s median) keeps his $300 clip but stays out
#   of LIVE_PREMAP_WHALES until his paper re-run at the new sub-second
#   detection grades positive.
# RESET 2026-08-25 with the clip map and the verified set — see
# PER_FILL_BY_WHALE for the re-graded numbers.
#
# This is the THIRD gate on the same decision, and the test suite is
# the only reason it did not get missed: the clip map and the verified
# set were both updated first, and this literal still refused rn1 and
# ferrari at entry. That is the identical shape of the 2026-08-24 bug,
# where SwissTony was resumed in two places and refused by a third,
# reported as live, and placed 2,897 rejections with $0 deployed.
#
# Three literals for one decision is a standing hazard. They are pinned
# against each other in test_verified_set and test_roster_reset so a
# future roster move cannot land in two of the three again.
# REINSTATEMENT 2026-08-27 (owner order). The merge-inclusive re-grade
# that cut swisstony (-$187,613) and homerunhazard (-$35,363) has since
# been proven BLIND: it counts only MERGE events as realized and never
# sees REDEEM, which is how these accounts realize nearly everything
# (rn1: $7.7M of redeems against $7.5M of buys in one window). The
# venue's own per-wallet P&L — pulled from the Polymarket ledger and
# user-pnl APIs, stored via /api/admin/venue-pnl — reads:
#   swisstony      +$23.6M lifetime   +$1.36M last 30d
#   homerunhazard   +$2.32M lifetime  +$869k  last 30d
#   0x2c33...       +$7.1M lifetime   -$90k   last 30d  STAYS CUT
# The old figures above remain on the record because the instrument
# that produced them measured exit mechanism, not profitability — the
# lesson is priced into the estimator work, not erased. 0x2c33 stays
# cut: negative 30d on the venue's own books AND -$1,910,412 on the
# merge-graded window that CAN see his exits (he does merge).
COPY_CUT_WHALES = frozenset({_W2C33})
# PROBE CLIPS (owner authorization 2026-08-24 evening: "$100 per clip
# on the actually verified profitable whales"). The resume is a bounded
# proof, not a return to size: the venue's side model is verified by
# 429 independent checks but has never been confirmed by a REAL FILL,
# and the position-sign check can only settle that with live orders.
# Scale follows evidence, not the other way round.
# 100 -> 250 (owner order 2026-08-24 evening, same session): the owner
# upsized the two verified whales before the first real fill returned.
# That is his risk call; what it changes is the cost of the ONE
# assumption still unproven (that BUY_SHORT buys the short side), so
# the first-fill gate below narrows the unverified window to a single
# order instead of the semaphore's four.
# ROSTER RESET 2026-08-25, owner-granted, on the first whale P&L this
# desk has ever produced that can SEE how these accounts take profit.
#
# Every prior roster decision graded at RESOLUTION. That basis cannot
# see a merge, and these whales close by merging — so the numbers the
# cuts were made on were blind to the exits that decide their P&L.
# Re-graded over full ledgers (no truncation), merges counted:
#
#   rn1                   +$222,038 on $23.9M entries  (+0.93%)  WAS CUT
#   ferrarichampions2026  +$217,159 on $12.8M entries  (+1.69%)  WAS CUT
#   0x076daa87             +$43,897 on $2.9M entries   (+1.53%)  kept
#   homerunhazard          -$35,363 on $27.4M entries  (-0.13%)  kept
#   swisstony             -$187,613 on $23.0M entries  (-0.82%)  NOW CUT
#   0x2c33...           -$1,910,412 on $47.4M entries  (-4.03%)  stays cut
#
# We had cut the two best books and were copying the second-worst. The
# settlement basis said the opposite of every one of those lines.
#
# swisstony to 0.00 is the least disruptive of these changes in
# practice: he has placed ZERO orders for days (his book resolves
# src=fuzzy and the quarantine admits only src=premap), so this makes
# an existing outage into a deliberate decision rather than stopping
# live flow.
#
# homerunhazard STAYS at -0.13%. He is roughly break-even on a huge
# book, not a leak, and cutting three of five whales at once would
# leave one live source and no way to attribute what changed.
#
# 0x2c33 stays blocked: -4.03% on $47M is the clearest loser here.
#
# WHAT THIS IS NOT: proof. It is realised P&L with large open positions
# still outstanding (ferrari $7.5M, rn1 $12.3M), and ROI on entries
# flatters high-turnover books. If the next re-grade moves these lines,
# this map moves with it.
PER_FILL_BY_WHALE = {"rn1": 250.00,
                     # REINSTATED 2026-08-27 (owner order) at the
                     # standard $250 clip on the venue-ledger basis
                     # (see COPY_CUT_WHALES): the 2026-08-25 cuts were
                     # made on the merge-only instrument since proven
                     # blind to REDEEM exits.
                     "swisstony": 250.00,
                     "homerunhazard": 250.00,
                     _W2C33: 0.00,
                     "kch123": 150.00,
                     # PROBE-SIZED (owner order 2026-08-30, on the
                     # statistical audit): ferrari's copy capture is
                     # -2.14x his edge at ZERO measured delay — price
                     # displacement, not latency (TRUEEDGE paper_actual
                     # -$21,758 vs cf_total +$10,150; his fast-detected
                     # subset's edge at our executable prices is
                     # negative outright). $100 keeps the cohort data
                     # flowing at ~40% of the bleed while resting-order
                     # mode is built; re-decide on its data. His
                     # detection, S1 certification, and exit mirroring
                     # are untouched.
                     "ferrarichampions2026": 100.00,
                     "0x076daa87": 250.00}
# HARD CEILING on the resolved clip, applied AFTER every override and
# multiplier. The owner authorized $100 per clip; a spread's x1.5 would
# otherwise place $150 and quietly exceed the authorization. A cap that
# sits below the maps cannot be defeated by a cell edit.
LIVE_MAX_CLIP_USD = float(os.environ.get("LIVE_MAX_CLIP_USD", "250"))
# DAY CAP REMOVED (owner order 2026-08-24 evening). This ceiling was
# mine, added with the probe; the owner's standing directive since
# 2026-08-05 is no day cap on copies, so removing it restores his
# policy rather than weakening a gate he set. 0 disables; the env knob
# stays so a ceiling can be re-armed in one change.
#
# What still bounds the day, none of it touched:
#   - $250 per order (LIVE_MAX_CLIP_USD, applied after every multiplier)
#   - the first-fill gate: ONE copy in flight until a real fill is
#     side-verified, so the unproven assumption cannot compound
#   - the side-echo circuit: any confirmed wrong side halts ALL copying
#   - the 24h realized-loss breaker (PMUS_LOSS_BREAKER_USD)
#   - the per-market and per-game single-bet rules, 90s staleness gate
PROBE_DAY_USD = float(os.environ.get("LIVE_PROBE_DAY_USD", "0"))

# PER-MARKET-TYPE MULTIPLIERS (owner go 2026-08-20 morning, from the
# five-whale lifetime type calibration, 2026-08-18): spreads beat every
# single whale's own blended average — the one invariant that held
# across all $2.3B of donor volume — and RN1's BTTS is his best type
# (+7.20% on $7.6M). Applied ON TOP of the whale/sport clip the maps
# above resolve, specific (whale, type) cell first, then the "*" type
# row. A 0.00 sport-cell block stays a block (0 x anything = 0).
# swisstony exact-score/BTTS down-weights from the same study need no
# row here: the copy_sports cell table already copies neither.
# kch123 spread 2.0: $150 base x 2.0 = the studied $300 spread clip.
TYPE_MULT: dict[tuple[str, str], float] = {
    ("*", "spread"): 1.5,
    ("kch123", "spread"): 2.0,
    ("rn1", "btts"): 1.5,
}
# swisstony soccer 150 -> 225 (owner go 2026-08-21, audit lever 2):
# his filled copies grade +25.2% ROI (30d fill-vs-miss) while his
# missed cohort grades -26.8% — the selectivity is doing the work, so
# the fills that DO pass the gates earn a bigger clip. Same +50% step
# discipline as every prior raise.
# The (whale, sport) cell WINS over the whale clip, so a cut whale must
# carry no live cells here — rn1's tennis/baseball/soccer rows are gone
# (2026-08-24 TRUEEDGE cut), not merely shadowed by his 0.00 base.
# homerunhazard's cells scale with his base (112.50→300, x2.667:
# baseball 225→600, football 37.50→100) so the measured per-sport
# judgment survives the upsize.
# The (whale, sport) cell WINS over the whale clip, so during the probe
# no cell may exceed the authorized $100 — HRH's measured baseball and
# football cells are retired until the probe promotes back to size.
PER_FILL_BY_WHALE_SPORT = {(_W2C33, "tennis"): 0.00}
# 24H ROLLING-LOSS BREAKER (owner 2026-08-12, threshold his call:
# "$1500"): when the copy sleeve's realized losses over any rolling
# 24 hours reach this, copying pauses by itself until the window
# rolls off — a bad day self-limits instead of compounding. Manual
# desk and the independent $2 underdog sleeve are outside it.
# 1500 -> 2250 (2026-08-17 evening, alongside the owner's "increase all
# copy trades by 50%"): the same clip-sensitivity rule the Kalshi-leg
# breaker has followed at every promotion — an unchanged floor at x1.5
# clips would trip on the ordinary variance that rode yesterday. The
# env var still overrides for an owner-set absolute.
# 2250 -> 3500 (2026-08-20 midday, alongside the envelope raise +
# spread multipliers): the same clip-sensitivity rule as every prior
# promotion — deployed copy dollars roughly +55% today, and an
# unchanged floor would trip on ordinary variance and halt the
# profitable sleeve mid-weekend. The env var still overrides.
# 3500 -> 5000 (2026-08-21, dossier promotions): two new $100-clip
# whales add up to $12k/day of probation envelope on top of ~$10k —
# same rule, same env override for an owner-set absolute.
PMUS_LOSS_BREAKER_USD = float(
    os.environ.get("PMUS_LOSS_BREAKER_USD", "5000"))


# RN1 CAPTURE TOLERANCE (owner mandate 2026-08-20 midday: "make any and
# all changes... most likely to make us the most money"): RN1 only, his
# price + 2c. The 7-day fill-vs-miss grading is the evidence — 2,102
# missed RN1 copies graded +5.3% while the missed cohorts on every
# other whale graded NEGATIVE (swisstony -16.8%, 0x2c33 -24.6%), so the
# strict same-or-better rule stays exactly where it is saving money and
# loosens only where it is provably leaving profit. The FOK still fills
# at the best price at/below the limit, so the extra cents are paid
# only when the book actually moved. The tolerance cohort is measurable
# in live_orders as limit_price > his_price; if its settled record
# grades negative the knob goes back to 0 (env override, no deploy).
# 2 -> 3 (owner capitalize order 2026-08-22): the 7d fill-vs-miss
# grade showed RN1's MISSED copies running +10.1% ROI — trades we
# refused over pennies were still profitable at worse entries, so one
# more cent of fresh-reaction capture is the highest-confidence dollar
# available. Graded as its own cohort from this deploy's timestamp;
# revert to 2 (or env-override) if the +3c entries grade below the
# +2c cohort. Fresh reactions ONLY — reclaims still pay no tolerance.
RN1_TOL_CENTS = float(os.environ.get("PMUS_RN1_TOL_CENTS", "3"))

# OPTION A (owner order 2026-08-26): a capture tolerance for EVERY
# whale, not just RN1.
#
# WHAT SAME-OR-BETTER ACTUALLY BOUGHT US. The limit was his price
# floored to the tick, and the code called the consequence a feature:
# "the order fills only at his price or cheaper, never worse. A book
# that ran past him is a skipped copy." Follow that through. The book
# only comes back to his price when the market moved AGAINST him, so
# FILLING WAS CONDITIONED ON THE WHALE BEING WRONG. We were not
# sampling his trades; we were sampling his losers.
#
# It is measured, not theorised. at_his -- his OWN P&L on the subset we
# actually filled, at HIS prices -- is negative on all six whales,
# summing -$30,248, while price_drag is POSITIVE on all six (+$13,051):
# we filled CHEAPER than he did and still lost. Under that rule no
# amount of further data reaches a positive verdict, because more data
# only tightens the negative interval. The rule had to change before
# the question could be asked.
#
# THIS IS A CEILING, NOT A PAYMENT. The FOK fills at the BOOK, so a
# +Nc limit costs whatever the ask is, up to N over. The fills it BUYS,
# though, are exactly the ones whose ask sat above him -- so the
# MARGINAL trade's cost is conditioned on being above him and is not
# the average over all fills. That distinction is why this ships with
# tolerance_cohort() and the copy-tolerance endpoint rather than alone:
# a change that cannot be graded is how the previous rule survived.
#
# THE ARITHMETIC THAT SETS THE DEFAULT. On a 50c contract one cent is
# 2% of stake, and rn1's whole measured edge is +1.50% ([-0.45%,
# +3.46%] over 103,065 lots). A wide tolerance can therefore cost more
# than the edge it captures. Default is ONE cent -- the smallest step
# that breaks the conditioning at all, since the venue ticks in cents.
# Raise it per whale, on graded evidence, never on a hunch.
COPY_TOL_CENTS = float(os.environ.get("PMUS_COPY_TOL_CENTS", "1"))

# Hard ceiling on anything the environment can ask for. A fat-fingered
# env var is the only way this becomes a 10-cent overpay, and 10 cents
# on a 50c contract is 20% of stake against a 1.5% edge. Tightening
# only -- it can never raise a tolerance, only refuse one.
COPY_TOL_MAX_CENTS = float(os.environ.get("PMUS_COPY_TOL_MAX_CENTS", "3"))

# ── THE REST LANE (owner order 2026-09-01: "full throttle, all approved")
#
# Every copy today is an IOC at his price plus a small tolerance. When
# the ask sits above that at the instant we arrive -- which is most of
# the time, because his own buy is what moved it -- the copy is lost
# outright. This lane acts ONLY on those lost copies: after the IOC
# comes back empty, rest a bid at his EXACT price (no tolerance) for a
# few seconds, then cancel. A fill here is a copy we would otherwise not
# have, at a price that pays zero over what he paid.
#
# Whether those fills are GOOD is precisely the open question -- the
# ask coming back to his price is also what "the market moved against
# him" looks like -- so they are stamped lane='rest' and graded as their
# own cohort with their own interval. Nothing about the IOC path
# changes: this runs only after it has already failed, so no fill we
# get today can be delayed, repriced or lost by it.
#
# BUDGET IS A HARD CAP ON REALISED FILLS, not attempts. Once the lane
# has filled its authorisation it disables itself and says so in the
# census; an owner decision re-opens it, never a retry.
# OFF BY THE CURVE (2026-09-02, 11:26Z probe, past the thirty-game floor):
# the ask RISES after his fill, +1.6c at 30s [+0.08c, +3.2c] and +1.7c at
# 60s [+0.27c, +3.1c], flat again by two minutes. A bid resting at his
# price inside that window fills when the market turns against him --
# adverse selection at 95%. The lane stays built and measured; it
# spends only when the owner sets PMUS_REST_BID=on, and the curve is
# re-read on every probe (PATHCURVE/PATHREAD) so a reversal is seen.
REST_BID_ENABLED = os.environ.get("PMUS_REST_BID", "off").lower() in (
    "1", "on", "true", "yes")
REST_BID_TTL_S = max(0.5, min(15.0, float(
    os.environ.get("PMUS_REST_BID_TTL_S", "5"))))
REST_BID_BUDGET_USD = float(os.environ.get("PMUS_REST_BID_BUDGET_USD", "2500"))


def _tol_overrides() -> dict[str, float]:
    """Per-whale tolerance from PMUS_COPY_TOL_BY_WHALE ("rn1:3,x:0").

    A whale named with 0 is an explicit REFUSAL to pay tolerance on him,
    which must survive a non-zero global default -- so this returns the
    parsed mapping and callers test membership, never truthiness.
    """
    out: dict[str, float] = {}
    for part in (os.environ.get("PMUS_COPY_TOL_BY_WHALE", "") or "").split(","):
        name, _, cents = part.partition(":")
        name = name.strip().lower()
        if not name:
            continue
        try:
            out[name] = float(cents)
        except ValueError:
            continue
    return out


# CODE-DEFAULT TOLERANCE REFUSALS (revenue audit 2026-08-29): whales
# whose TOLA marginal cohort — the fills that exist ONLY because of the
# tolerance — has graded materially negative. 0x076daa87: n=15 settled
# 13, staked $904.01, roi -0.7498, pnl -$653.17 (probe 19:44Z), twice
# corroborated (FVM filled -0.3462 vs missed -0.0551 — we fill his
# WORST trades; EDGE at_his negative at his own prices). A code default
# rather than an env note so it deploys with the decision attached; an
# EXPLICIT env override for the same whale still wins — re-enabling is
# an operator decision the code must not overrule.
_TOL_ZERO_DEFAULT = frozenset({"0x076daa87"})


def tol_cents_for(whale_username: str | None) -> float:
    """Capture tolerance in cents for this whale, clamped to the ceiling.

    Precedence: explicit per-whale override, then the code-default
    refusal set (audit-graded negative marginal cohorts), then RN1's own
    long-standing env var (kept so an operator's existing
    PMUS_RN1_TOL_CENTS keeps meaning what it meant), then the global
    default.
    """
    name = (whale_username or "").lower()
    over = _tol_overrides()
    if name in over:
        cents = over[name]
    elif name in _TOL_ZERO_DEFAULT:
        cents = 0.0
    elif name == "rn1":
        cents = RN1_TOL_CENTS
    else:
        cents = COPY_TOL_CENTS
    return max(0.0, min(cents, COPY_TOL_MAX_CENTS))


def copy_limit_price(whale_username: str | None, his_price: float,
                     fresh: bool = True) -> float:
    """FOK limit for a copy: his price floored to the venue tick, plus
    the per-whale capture tolerance (see tol_cents_for).

    fresh=False (the sweep's reclaims, audit 2026-08-21): NO tolerance,
    and that stays true under Option A. The tolerance exists to capture
    a signal the market is moving with; paying over a days-old entry
    price is adverse selection wearing the same clothes.

    RELATIVE CAP (revenue audit 2026-08-29, tightening only): absolute
    cents are a huge fraction of a longshot's price — +3c on a 10c book
    is +30% of stake, the exact drag shape PROOF2WORST keeps printing
    (rn1 his=0.10 ours=0.13). Whatever cents the maps grant is bounded
    at 15% of his price, floored at one venue tick so a granted
    tolerance is never rounded to nothing. No change above ~20c;
    strictly tighter below.
    """
    import math

    limit = math.floor(round(his_price * 100, 6)) / 100.0
    cents = tol_cents_for(whale_username) if fresh else 0.0
    if cents > 0:
        rel_cap = max(0.01, math.floor(15.0 * his_price) / 100.0)
        extra = min(cents / 100.0, rel_cap)
        limit = min(0.99, round(limit + extra, 2))
    return limit


def tolerance_cohort(his_price: float | None,
                     fill_price: float | None,
                     intent: str | None = None) -> str:
    """Which cohort a FILL belongs to, for grading Option A.

    'marginal' -- we paid ABOVE his price, so this fill exists ONLY
    because of the tolerance. These are the trades Option A bought, and
    they are the ONLY ones whose P&L answers whether it was worth it.
    'parity'   -- at or below his price; same-or-better would have
    filled it too, so it says nothing about the change.

    Averaging the two together is how a change like this gets graded as
    harmless: the parity fills dominate the count and drown the signal.

    ONE DENOMINATION, VIA cost_per_share (adversarial review
    2026-08-26, hours after this shipped). The first version compared
    raw fill_price to his_price. fill_price on a SHORT names the LONG
    leg while his_price is what the whale paid on HIS side, so every
    short fill was cohorted by comparing two different legs -- the
    exact defect that inverted realized_pnl and fill_cash before both
    took an intent, and that price_fidelity records as ~66c of phantom
    slippage on six real fills. cost_per_share is the single
    definition; this reads it like everything else does.
    """
    if his_price is None or fill_price is None:
        return "unknown"
    try:
        ours = cost_per_share(float(fill_price), intent)
        # Strict 'above', with a hair of tolerance for float
        # round-trips through numeric columns: a fill AT his price is
        # parity by definition -- same-or-better would have taken it.
        return "marginal" if ours > float(his_price) + 1e-9 else "parity"
    except (TypeError, ValueError):
        return "unknown"


def is_short_intent(intent: str | None) -> bool:
    """True for the two SHORT intents this venue accepts."""
    return "SHORT" in (intent or "").upper()


# THE SHORT COST MODEL IS NOT CONFIRMED, SO IT DOES NOT ARM ITSELF.
#
# I argued to the owner that the SDK settled this: CreateOrderParams
# carries no token id, therefore one market, one ladder, therefore short
# is a book-level SELL costing (1 - price) x qty. An adversarial read of
# the SDK took that apart, and I checked it myself:
#
#   * polymarket_us/types/markets.py MarketDetail has ZERO occurrences
#     of marketSides — and this repo reads marketSides SEVENTEEN times
#     in pmus.py alone, against a venue that demonstrably returns it.
#     The stubs are provably incomplete, so a field's absence from them
#     is not evidence the venue lacks it.
#   * pmus.event_board (pmus.py:291-296) already treats each side's
#     `identifier` as its own orderable slug WITH ITS OWN `price`, and
#     side_ask reads that side's own bestAsk. A per-side price plainly
#     exists here. "There is no second ladder" is contradicted by code
#     in this repository.
#
# The arithmetic still fits — six of six at or under authorisation, one
# to the cent — but a fit is not a mechanism, and the same six rows fit
# "we were filled on the opposite leg at its own ask" too, because that
# ask is approximately the complement.
#
# So the model is written, tested, and DISARMED. It applies only when
# LIVE_SHORT_COST_MODEL=confirmed, which is set after the venue's own
# order.side on those rows says ORDER_SIDE_SELL. Until then a short
# costs what a long costs, which is the arithmetic that has always
# governed the rows we actually book.
#
# This matters because flipping LIVE_ALLOW_SHORT would otherwise arm an
# unproven cost model at the same moment it re-opens the trade class —
# two unverified changes riding one switch.
def short_model_confirmed() -> bool:
    """CONFIRMED 2026-08-25 14:38Z BY THE VENUE'S OWN RECEIPTS.

    Not by my SDK argument, which was unsound and is retracted above.
    By /api/admin/short-truth reading the create-order responses we
    have been storing on every row since the beginning:

        SHORTTRUTH n=6 with_venue_side=6 sides={"ORDER_SIDE_SELL":6}
                   within_auth short_model=6/6 long_model=0/6

    Six of six booked by the venue as ORDER_SIDE_SELL. `side` is not a
    field we send — the venue derives it — so this is the venue stating
    that our BUY_SHORT was a sell. Per row:

        danalt-fracom lim0.22 qty1136 @0.78  long $886.08  short $249.92
        domsal-akaurh lim0.37 qty 675 @0.65  long $438.75  short $236.25
        colwon-elmmoe lim0.32 qty 781 @0.6853 long $535.22 short $245.78
        harwen-stetra lim0.23 qty1086 @0.89  long $966.54  short $119.46
        jancho-meerot lim0.45 qty 555 @0.56  long $310.80  short $244.20
        ekaovc-kaique lim0.48 qty 520 @0.55  long $286.00  short $234.00

    So: there was never an overspend. The breaker fired six times on
    correct trades, I took a whole class of copy off the board for a
    day, and filled_usd / pnl / deployed / the volume governor's
    throttle input have been wrong by (1-p)/p on every short row.

    The default flips to armed because the evidence is now the venue's
    rather than mine. The env var stays as an override so it can be
    disarmed in one move if a later reading disagrees.
    """
    v = os.environ.get("LIVE_SHORT_COST_MODEL", "").strip().lower()
    if v in ("off", "0", "no", "disarm", "disarmed"):
        return False
    return True


def _use_short_math(intent: str | None) -> bool:
    return is_short_intent(intent) and short_model_confirmed()


# THE INTENT, READ OFF THE ROW WE ALREADY STORE.
#
# live_orders has no `intent` column — asking for one is what made
# /api/admin/short-truth return 500s that surfaced only as "SHORTTRUTH
# unavailable". The intent has always been in `raw`, in the venue's own
# execution record, and the short-truth endpoint already reads it from
# exactly these two paths. Same expression, one definition.
ORDER_INTENT_SQL = (
    "COALESCE(raw #>> '{response,executions,0,order,intent}', "
    "         raw #>> '{preview,intent}')")


def realized_pnl(entry: float | None, exit_px: float | None,
                 shares: float, intent: str | None) -> float | None:
    """Realized P&L on closing a position — CORRECT ON BOTH SIGNS.

    `(exit - entry) * shares` is the LONG formula and it was applied to
    every exit regardless of intent. That is the same mistake, in the
    same shape, as `spent = filled * fill_price` — the line that
    produced "BUY_SHORT n=6 over=6 clean=0" and cost a whole class of
    copy before fill_cash was written. One decision was encoded in
    several places and only some of them were converted.

    On a short it does not merely misreport the magnitude, IT INVERTS
    THE SIGN. Six shorts filled on 2026-08-24 before the branch was
    banned, so this is reachable on rows we hold right now, and it
    becomes reachable on every new short the moment LIVE_ALLOW_SHORT is
    turned back on.

    The arithmetic, using what the venue's six receipts established —
    that on a short the `price` field names the LONG leg:

        short entry at price e   cost per share      = 1 - e
        closed  at    price x    short leg worth     = 1 - x
        realized per share       = (1 - x) - (1 - e) = e - x

    So a short is the long formula with the sign flipped, and nothing
    else changes. A short that WON (the long leg fell) reports a gain,
    where before it reported a loss of equal size.

    Returns None when any input is missing, exactly as the call sites
    already expected — an unknown P&L must stay unknown rather than
    become zero.
    """
    if entry is None or exit_px is None:
        return None
    try:
        e, x, n = float(entry), float(exit_px), float(shares)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    per = (e - x) if _use_short_math(intent) else (x - e)
    return round(per * n, 4)


def wire_limit(limit: float, intent: str | None) -> float:
    """The price to put ON THE WIRE for `limit` of our own money.

    THE SHORT MIS-DENOMINATION (2026-08-25, owner-confirmed against
    Polymarket's global microdata).

    polymarket_us/types/orders.py settles the venue's shape:
    CreateOrderParams takes marketSlug and intent and carries NO token
    id — there is ONE market and ONE price ladder — and Order.side is
    RETURNED, never sent, so the venue DERIVES buy/sell from the intent.
    That is a futures contract: going short is SELLING it, `price`
    denominates the contract, and a short ties up (1 - price) x qty.

    We were sending the whale's own outcome price straight to the wire
    for both intents. For a long that is right by construction: his
    outcome IS the contract. For a short it is a different number in a
    different space, and the error is not symmetric — it is LOOSE.

    Sending 0.22 as a sell limit authorises selling at anything >= 0.22,
    i.e. paying up to 0.78 a contract, when we meant to pay 0.22. That
    is a 3.5x wider authorisation than intended on the worst row, and
    the six fills came in under it by luck, not by control.

    The correct wire price for "pay at most `limit` per contract" is a
    sell limit of 1 - limit: sell at >= 0.78 means pay <= 0.22.

    Rounded so the rounding can never work against us — down for a buy,
    up for a sell, both meaning "pay no more than intended".

    This is a TIGHTENING. On the six 2026-08-24 rows it replaces wire
    limits of 0.22-0.48 with 0.78-0.52, and the money each authorises
    falls in every case.
    """
    import math

    if not _use_short_math(intent):
        return limit
    return math.ceil(round((1.0 - limit) * 100, 6)) / 100.0


def rest_tick(wire: float, intent: str | None) -> float:
    """The price the adapter will actually SEND for a rest bid, on the
    venue's cent tick, rounded so it can never pay above him.

    Round five (2026-09-01): the adapter formats every price to two
    decimals (pmus._amount), so the venue lists 0.47 for a wire of
    0.474 -- and the lost-bid fingerprint compared against 0.474, which
    matched nothing for any long-leg whale price off an exact cent
    (most of them: ingestion rounds to six). One number, used for the
    placement AND the fingerprint. Floored for a long (the contract is
    his outcome; 'his price or better' means at or below him); for a
    short wire_limit has already ceiled the sell limit, which is the
    same direction -- pay no more than intended.
    """
    import math

    if _use_short_math(intent):
        return round(float(wire), 2)
    return math.floor(round(float(wire) * 100.0, 6)) / 100.0


def fill_cash(filled_shares: float, fill_price: float | None,
              intent: str | None) -> float:
    """What a fill actually COST us, in dollars.

    `spent = filled * fill_price` is the LONG formula, and it was
    applied to every row regardless of intent. On a short, fill_price is
    the CONTRACT price and the cash is (1 - fill_price) x qty.

    That single line is what produced "BUY_SHORT n=6 over=6 clean=0".
    Re-run the six against both formulas:

        req $249.92 qty 1136 @0.78   long $886.08  short $249.92
        req $249.92 qty  781 @0.6853 long $535.22  short $245.78
        req $249.75 qty  675 @0.65   long $438.75  short $236.25
        req $249.75 qty  555 @0.56   long $310.80  short $244.20
        req $249.60 qty  520 @0.55   long $286.00  short $234.00
        req $249.78 qty 1086 @0.89   long $966.54  short $119.46

    Every one at or under what we authorised, one exact to the cent,
    worst overage across all six $0.00. There was no overspend. There
    was a units bug in our own bookkeeping, and it took a correct class
    of trade off the board for a day.

    Everything downstream inherits this number: filled_usd, pnl, the
    24h deployed totals, and the volume governor's throttle input.
    """
    px = float(fill_price or 0)
    if px <= 0 or filled_shares <= 0:
        return 0.0
    return round(filled_shares * cost_per_share(px, intent), 2)


def cost_per_share(fill_price: float, intent: str | None = None) -> float:
    """Our cost for ONE share, UNROUNDED.

    Split out of fill_cash (2026-08-26, adversarial round 4) because
    fill_cash rounds to the CENT -- correct for a dollar total, and
    silently destructive when the caller wants a rate.

    price_fidelity.fill_edge asked for our per-share cost by calling
    fill_cash(1.0, price, intent), so `round(1.0 * per, 2)` quantized
    the rate to a whole cent before subtracting the whale's price. Every
    number that instrument produces -- at_or_better, the median and
    worst edge in cents, dollar_edge_vs_his_price, edge_per_100_deployed
    and the owner-facing verdict string -- was built on that.

    Production fill prices are not cent-aligned: submit_fok returns
    round(notional / filled, 4), a VWAP across executions, and the
    venue's own receipts in this repo include 0.6853. So the rounding
    was live on ordinary fills, not an edge case:

        his 0.68, our 0.6849  ->  reported +0.00c, true -0.49c
        his 0.68, our 0.6853  ->  reported -1.00c, true -0.53c

    It erases real slippage AND fabricates slippage that never
    happened. Seventy fills of 365 shares at that first pair is $125 of
    edge given away on $17,374 -- 72bp, against a whale edge measured at
    94bp -- reported as "$0.00, 100% of fills at his price or better".
    SAME_PRICE_EPS is 0.001, a tenth of a cent, so the tolerance was ten
    times finer than the resolution of the number it filtered and could
    never fire.

    ONE definition, used by both. fill_cash keeps its cent rounding,
    which is right for money; callers wanting a rate take it from here.
    """
    px = float(fill_price or 0)
    return (1.0 - px) if _use_short_math(intent) else px


# Per-whale staleness ceilings, in seconds. Absent whales take the
# default; env overrides the default only, so a measured cap cannot be
# loosened by a stray environment variable.
STALE_CAP_BY_WHALE = {"swisstony": 15.0}


def _stale_cap_for(whale_username: str | None) -> float:
    """The oldest a signal may be for THIS whale and still be worth
    copying — derived from what latency measurably costs his edge."""
    default = float(os.getenv("LIVE_MAX_REACTION_S", "90"))
    return STALE_CAP_BY_WHALE.get((whale_username or "").lower(), default)


def per_fill_usd(whale_username: str | None,
                 slug: str | None = None) -> float:
    """Clip for this whale on this market: the (whale, sport) override
    wins, then the whale clip, then the default — scaled by the
    market-type multiplier (spreads x1.5 everywhere, owner go
    2026-08-20). A 0.00 cell stays a block.

    UNREADABLE stored clips are 0.0 for EVERY whale (disk-full incident
    2026-09-05): while the rules' map cannot be read, the hardcoded
    PER_FILL_BY_WHALE is not a fallback -- it named $250 and $100 for
    whales the owner had cut to $0 in the row we could not read."""
    if overrides_unreadable():
        return 0.0
    w = (whale_username or "").lower()
    sport_cell = None
    if slug:
        from .copy_sports import sport_of

        sport_cell = PER_FILL_BY_WHALE_SPORT.get((w, sport_of(slug)))
    stored = (_clip_override or {}).get(w)
    if stored is not None:
        # THE RULES' CLIP (roster_auto -> ingestion_state) replaces the
        # hardcoded whale clip and the default. A hand-measured (whale,
        # sport) cell can only TIGHTEN it: a 0.00 cell stays a block,
        # and a demoted whale's 0 stays 0 whatever the cell says.
        base = stored if sport_cell is None else min(stored, sport_cell)
    elif sport_cell is not None:
        base = sport_cell
    else:
        base = PER_FILL_BY_WHALE.get(w, PENNY_TRIAL_PER_FILL_USD)
    if slug and base > 0:
        from .copy_sports import market_type_of

        mt = market_type_of(slug)
        mult = TYPE_MULT.get((w, mt), TYPE_MULT.get(("*", mt)))
        if mult is not None:
            base = round(base * mult, 2)
    # the authorization is a CEILING, not a suggestion
    if LIVE_MAX_CLIP_USD > 0:
        base = min(base, LIVE_MAX_CLIP_USD)
    return base


# INVERSE VOLUME<->SIZE SCALING (owner order 2026-08-12, alongside the
# mapping recovery: "if we increase total trades by 10x sizing of each
# trade decreases by 10x"). Each whale has a day-dollar envelope of
# base_clip x baseline fills; while the day's fill count sits at or
# under baseline the clip is the base clip, and past baseline the clip
# shrinks proportionally so 10x the fills spends the same dollars at
# 1/10 the size. Never scales UP, floors at $5 so a copy stays a whole
# contract, and an unreadable count degrades to the base clip (sizing
# must not depend on a flaky read).
# rn1 40 -> 110 (owner go 2026-08-20 morning): he averages ~109 copied
# fills/day and the 40-fill baseline was shrinking his clip to ~$80 by
# midday — throttling exactly the whale whose filled copies grade
# +24.6% ROI (7d fill-vs-miss). The envelope rule is unchanged; only
# his baseline is recalibrated to his real volume (day envelope
# ~$9k -> ~$25k at the $225 clip).
# rn1 110 -> 150 (owner go 2026-08-21, audit lever 1): the 30-day
# fill-vs-miss grades his MISSED copies +10.1% ROI on 1,086 resolved —
# the misses themselves are profitable trades, so the volume governor
# was throttling provable edge. 150 keeps the envelope rule while
# letting his real flow through (~$34k day envelope at the $225 clip).
BASELINE_FILLS_PER_DAY = {"rn1": 150.0, "swisstony": 30.0,
                          # Probation envelopes for the 2026-08-21
                          # promotions, from the 30-day flow study:
                          # mapped-cell entries collapse ~2.2x by the
                          # one-per-market rule, then ~25-35% capture →
                          # expected ~40-60 and ~60-90 fills/day. The
                          # baseline x $100 clip bounds each at
                          # $4.5k/$7.5k a day while probation grades.
                          "ferrarichampions2026": 45.0,
                          "0x076daa87": 75.0}
BASELINE_FILLS_DEFAULT = 20.0
MIN_CLIP_USD = 5.0


# CONVICTION SIZING (owner order 2026-08-25).
#
# "If the average trade price for all our whales is $2k, and there is an
#  order for $5k, I want to make sure our proportional trades reflect
#  that. There is a reason that whale is putting more on that. In the
#  counter side of the same logic, if the average position is $2k and
#  the whale cashes out $1k in profit from a position, and then reenters
#  at $500, I want our behavior to match the logic there."
#
# What we had destroyed exactly that signal. The clip is capped at $250,
# so his $5,000 high-conviction bet and his $250 routine one both came
# out as our $250 — every trade he made looked identical to us, and the
# single most informative thing about a whale's book (how much HE varies
# his own size) was thrown away at the last step.
#
# Conviction is measured against HIS OWN baseline, not against ours and
# not against the roster's. A whale who habitually stakes $2k and puts
# $5k on one game is telling us something; a whale whose average is $50
# putting on $200 is telling us the same thing at a different scale, and
# both should reach us as 2.5x.
#
# The multiple is BOUNDED both ways. A single outlier in his ledger
# would otherwise let one trade claim many times the clip, and a whale
# with a thin or new history has no meaningful average at all — that
# case takes the neutral multiple rather than a guess.
CONVICTION_MIN = float(os.environ.get("LIVE_CONVICTION_MIN", "0.25"))
# Harmless above the anchor now: the governed clip binds the result, so
# a large ceiling here only means his very biggest trades reach the cap
# rather than stopping short of it.
CONVICTION_MAX = float(os.environ.get("LIVE_CONVICTION_MAX", "10.0"))
# Where a NEUTRAL trade sits as a fraction of the authorized clip. 0.40
# of $250 = a $100 anchor, leaving 2.5x of headroom for his
# high-conviction trades INSIDE the existing cap. Raising this toward
# 1.0 flattens conviction back out; it can never raise the ceiling.
CONVICTION_ANCHOR_FRAC = float(
    os.environ.get("LIVE_CONVICTION_ANCHOR_FRAC", "0.40"))
# Below this many priced entries his "average" is one or two trades.
CONVICTION_MIN_SAMPLE = int(
    os.environ.get("LIVE_CONVICTION_MIN_SAMPLE", "20"))
_CONVICTION_CACHE: dict = {}
_CONVICTION_TTL_S = float(os.environ.get("LIVE_CONVICTION_TTL_S", "900"))


def conviction_multiple(his_notional: float, his_average: float) -> float:
    """How far above or below his own habit this trade sits, bounded.

    Pure, so the arithmetic is testable without a venue or a database:
        his avg $2,000, this trade $5,000 -> 2.5x
        his avg $2,000, this trade   $500 -> 0.25x
    """
    try:
        n = float(his_notional or 0)
        a = float(his_average or 0)
    except (TypeError, ValueError):
        return 1.0
    if n <= 0 or a <= 0:
        return 1.0          # no signal — neutral, never a guess
    return max(CONVICTION_MIN, min(CONVICTION_MAX, n / a))


async def whale_average_notional(pool, whale_username: str | None) -> float:
    """His own typical ENTRY size, in dollars. 0.0 when unknowable.

    Deliberately excludes his exits. These whales close by buying the
    complementary leg, so a merge is a BUY row like any other — folding
    those into the average would mix "what he risks" with "what it cost
    him to stop risking it" and drag the baseline toward whatever his
    exit prices happen to be.

    MEDIAN, not mean. One $2M block in a book of $200 trades moves a
    mean enough to make every ordinary trade look like low conviction;
    the median is what "his usual size" actually means.

    Cached per whale: this is a habit measured over 30 days, it does not
    move between fills, and the copy path must not pay for a percentile
    scan on every trade.
    """
    w = (whale_username or "").lower()
    if not w:
        return 0.0
    hit = _CONVICTION_CACHE.get(w)
    if hit and (time.time() - hit[0]) < _CONVICTION_TTL_S:
        return hit[1]
    try:
        row = await pool.fetchrow(
            """
            SELECT count(*)::int AS n,
                   COALESCE(percentile_cont(0.5) WITHIN GROUP (
                       ORDER BY t.notional), 0)::float8 AS med
              FROM trades t JOIN whales wh ON wh.id = t.whale_id
             WHERE lower(wh.username) = $1
               AND t.side = 'BUY'
               AND t.notional > 0
               AND t.ts > now() - interval '30 days'
            """, w)
        # Reading the row belongs INSIDE the guard. The first version
        # left it outside and a row without the expected keys raised
        # KeyError straight up the copy path — 35 tests, and in
        # production it would have been an exception on the money path
        # from a function whose entire contract is "degrade to neutral".
        n = int((row["n"] if row else 0) or 0)
        med = float((row["med"] if row else 0) or 0.0)
    except Exception:  # noqa: BLE001 — degrade to neutral, never block
        return 0.0
    avg = med if n >= CONVICTION_MIN_SAMPLE else 0.0
    _CONVICTION_CACHE[w] = (time.time(), avg)
    return avg


async def volume_normalized_clip(pool, whale_username: str | None,
                                 slug: str | None = None) -> float:
    base = per_fill_usd(whale_username, slug)
    if base <= 0:      # blocked (whale, sport) cell — caller must skip
        return 0.0
    w = (whale_username or "").lower()
    baseline = BASELINE_FILLS_PER_DAY.get(w, BASELINE_FILLS_DEFAULT)
    try:
        # DOLLARS, not row count (audit 2026-08-21): since the IOC
        # partial-take change a $9 partial fill burned the same
        # baseline slot as a full $225 fill, systematically shrinking
        # clips on exactly the thin-book days partial-take exists for.
        # The governor now scales by dollars actually deployed against
        # the whale's dollar envelope (baseline fills x this clip).
        # Statuses (adversarial review 2026-08-12): 'settled' and
        # 'cashed_out' STAY in the sum — money deployed today is still
        # deployed after it resolves; 'submitting' is excluded so a
        # stranded in-flight row can never permanently shrink the clip.
        # ADD LEGS ON THEIR OWN CLOCK (adds review round two): a leg's
        # dollars sit on its standing row; legs merged inside the window
        # onto rows placed before it are added from the rows' leg list,
        # as _caps_room does.
        spent = float(await pool.fetchval(
            "SELECT COALESCE(sum(filled_usd), 0)::float8 "
            "+ COALESCE((SELECT sum((a->>'usd')::float8) FROM live_orders lo2, "
            "            jsonb_array_elements(COALESCE(lo2.raw->'adds', '[]'::jsonb)) a "
            "            WHERE lower(COALESCE(lo2.whale_username, '')) = $1 "
            "              AND lo2.status IN ('filled', 'settled', 'cashed_out') "
            "              AND lo2.placed_at <= now() - interval '24 hours' "
            "              AND (a->>'ts')::float8 > extract(epoch FROM now() - interval '24 hours')), "
            "          0)::float8 "
            "FROM live_orders "
            "WHERE lower(COALESCE(whale_username, '')) = $1 "
            "AND status IN ('filled', 'settled', 'cashed_out') "
            "AND placed_at > now() - interval '24 hours'", w) or 0)
    except Exception:  # noqa: BLE001 — degrade to base, never block
        spent = 0.0
    envelope = baseline * base
    if envelope <= 0 or spent <= envelope:
        return base
    return max(MIN_CLIP_USD, round(base * envelope / spent, 2))


def _ladder_kind(us_slug: str) -> str | None:
    """Venue-grammar kind prefix when the market belongs to a LADDERED
    family (many nested lines per game): 'tsc' totals, 'asc' spreads.
    Moneylines/events (atc/aec) have one market per game — no ladder."""
    kind = (us_slug or "").lower().split("-", 1)[0]
    return kind if kind in ("tsc", "asc") else None


def _us_game_key(us_slug: str) -> str | None:
    """Game identity of a venue-grammar slug: league + teams + date,
    kind prefix and line suffix stripped — 'tsc-epl-ars-che-2026-08-15-
    o2pt5' and 'tsc-epl-ars-che-2026-08-15-o3pt5' are the SAME game.
    None when the slug has no recognizable date (fail open: an
    unparseable slug must not block a legitimate copy)."""
    parts = [p for p in (us_slug or "").lower().split("-") if p]
    if len(parts) < 5:
        return None
    parts = parts[1:]                     # drop the kind prefix
    for i in range(len(parts) - 2):
        if (len(parts[i]) == 4 and parts[i].isdigit()
                and parts[i + 1].isdigit() and parts[i + 2].isdigit()):
            return "-".join(parts[:i + 3])
    return None


# ── Manual trade desk (owner directive 2026-08-07) ───────────────────
# An admin directs trades ("$50 on Yankees ML") executed by the live
# account as the 'manual' sleeve: its own budget, its own P&L line,
# invisible to every autonomous rule in both directions. Global stops
# still bind — the kill switch means "the ACCOUNT is unsafe", and no
# sleeve trades through that.
MANUAL_WHALE = "manual"
MANUAL_MAX_PER_ORDER_USD = float(os.environ.get("MANUAL_MAX_PER_ORDER_USD",
                                                "250"))
MANUAL_DAILY_USD = float(os.environ.get("MANUAL_DAILY_USD", "1000"))


async def _clob_best_ask(cfg, asset: str) -> float | None:
    """Best global-CLOB ask for a token — the reference price the desk
    quotes and protects the limit against. None = no live book."""
    import httpx

    try:
        async with httpx.AsyncClient(base_url=cfg.clob_api_base,
                                     timeout=8) as http:
            resp = await http.get("/book", params={"token_id": str(asset)})
        if resp.status_code != 200:
            return None
        asks = sorted(float(x["price"]) for x in
                      (resp.json().get("asks") or []))
        return asks[0] if asks else None
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return None


# The tennis / US-slug candidate grammar (_abbrev_player, _tennis_candidates,
# _us_slug_candidates and the tennis league tables) lives in copy_sports
# since 2026-09-03 (to-a-tee Phase 2, owner order 2026-09-02): one pure
# grammar for the copy lane, the underdog sleeve, the mirror and the
# runner. Re-exported so every existing import keeps working.
from .copy_sports import (_TENNIS_LEAGUES, _TENNIS_US_CODES,  # noqa: E402,F401
                          _abbrev_player, _tennis_candidates,
                          _us_slug_candidates)


async def _reap_stale_submitting(pool) -> None:
    """Terminal-ize 'submitting' rows whose process died mid-order.

    (audit 2026-08-21) A phantom submitting row is load-bearing in two
    bad ways: copy rows hold the one-fill-per-asset claim forever (the
    asset can never be copied again), and manual rows would wedge the
    migration-023 in-flight index. Ten minutes is far past any real
    submit (mapping + preview + IOC is tens of seconds).

    RECONCILE, DO NOT BLIND-MARK (2026-09-01, rest lane review). A row
    that carries an order_id names a venue order that may be RESTING --
    the rest lane persists its GTC id the instant the venue returns it
    for exactly this moment. Marking such a row 'error' would let the
    sweep re-qualify the trade and stack a second bid on a market where
    the first is still live, and would leave any fill in the window in
    no ledger row at all. So: cancel, read the terminal state, and write
    what actually happened. The blind 'error' path survives only for
    rows with NO order_id, where there is nothing to ask the venue.
    """
    # NEVER RAISES. This is pre-flight housekeeping on the manual desk
    # and a tick in the sweep; a reaper that throws takes a real order
    # down with it (the desk tests caught exactly that when this grew
    # a fetch). A pass that cannot run is skipped and logged; the rows
    # keep their claim and the next pass tries again.
    try:
        from . import pmus

        rows = await pool.fetch(
            "SELECT id, order_id, us_market_slug, requested_shares, status, "
            "       error, his_price::float8 AS his_price, "
            "       limit_price::float8 AS limit_price, "
            "       COALESCE(whale_username, '') AS whale_username, "
            "       (raw->'pre_ids')::text AS pre_ids, "
            "       (raw->>'add_of')::text AS add_of, "
            "       EXTRACT(EPOCH FROM (now() - placed_at))::float8 AS age_s, "
            f"       {ORDER_INTENT_SQL} AS intent "
            "FROM live_orders WHERE (status = 'submitting' "
            "AND placed_at < now() - interval '10 minutes') "
            # NAMED ROWS ARE REVISITED (round seven): a row named "venue
            # holds a POSITION" is re-asked every pass -- a fill of ours
            # may appear in the trade log, or the position may clear --
            # until the naming horizon, and is never left to evaporate.
            "OR (status = 'error' AND (error LIKE 'venue holds a POSITION%' "
            "OR error LIKE 'ORPHAN FILL RECORDED%' "
            "OR error LIKE 'venue has no record of order%') "
            f"AND placed_at > now() - interval '{_NAMED_HORIZON}')")
        for r in rows:
            # ONE ROW'S FAILURE IS ONE ROW'S (round eight): a write that
            # raises -- the one-fill-per-asset index on a 'filled'
            # UPDATE -- used to abandon every later row in the pass.
            try:
                await _reap_one_submitting_row(pool, pmus, r)
            except Exception as exc:  # noqa: BLE001
                if (_row_get(r, "add_of")
                        and "live_orders_one_fill_per_asset" in str(exc)):
                    # AN ADD LEG THAT DIED MID-FLIGHT (adds, 2026-09-02):
                    # the venue filled it, and it cannot be booked
                    # 'filled' beside the standing row it was meant to
                    # merge into. It is NAMED -- the same named state an
                    # orphan fill gets -- so it leaves the in-flight
                    # claim, the ledger says what the venue holds, the
                    # sweep and never-add refuse to re-buy while it
                    # stands, and the reaper revisits it: once the
                    # standing row settles or exits, the claim frees and
                    # the leg is promoted to a position of its own.
                    await pool.execute(
                        "UPDATE live_orders SET status='error', error=$2 "
                        "WHERE id=$1 AND status='submitting'", _row_get(r, "id"),
                        f"ORPHAN FILL RECORDED on a row that cannot re-enter "
                        f"'filled' (add leg of row #{_row_get(r, 'add_of')} "
                        f"died mid-flight; the standing row holds this "
                        f"asset's claim) — reconcile against the venue "
                        f"account")
                    log.error("reaper: add leg row %s of row %s filled but "
                              "could not merge — NAMED as an orphan fill",
                              _row_get(r, "id"), _row_get(r, "add_of"))
                    continue
                log.warning("reaper: row %s skipped this pass (%s: %s)",
                            _row_get(r, "id"), type(exc).__name__,
                            str(exc)[:120])
    except Exception as exc:  # noqa: BLE001 — housekeeping only
        log.warning("stale-submitting reaper skipped this pass (%s: %s)",
                    type(exc).__name__, str(exc)[:120])


async def _reap_one_submitting_row(pool, pmus, r) -> None:
    oid = r["order_id"]
    slug = r["us_market_slug"]
    age = float(_row_get(r, "age_s") or 0.0)
    if str(_row_get(r, "whale_username") or "") in _DESK_SLEEVES:
        # THE DESK'S TICKET IS THE OWNER'S (round nine, reproduced): a
        # manual limit row killed between the venue accepting its GTC
        # and the 'open' write was fingerprint-matched to the owner's
        # own resting ticket, adopted, and CANCELLED by the copy lane's
        # cancel-and-read. A desk row is handed to the desk: the id it
        # carries -- or the one its own fingerprint finds on the book --
        # is written with status 'open', which sync_open_manual_orders
        # reconciles at the owner's pace. Nothing here cancels it. A
        # desk row with no id and no bid on its book is blind-marked as
        # before this branch, never named and never reconciled from the
        # trade log, so a desk row can never carry a copy-lane fill.
        if _row_get(r, "status") != "submitting":
            return
        if not oid and slug:
            try:
                his = float(_row_get(r, "his_price") or 0.0)
                shares = float(_row_get(r, "requested_shares") or 0.0)
            except (TypeError, ValueError):
                his, shares = 0.0, 0.0
            if 0.0 < his < 1.0 and shares >= 1:
                placed_ts = time.time() - age
                # never a mirror order (P1 step 8, final review): the
                # desk's fingerprint search excludes the protected set
                # like the other two callers; an unreadable set leaves
                # the row exactly as an unreadable book does
                protected = await _protected_order_ids(pool)
                if protected is None:
                    return
                found, readable = await _find_lost_rest_bid(
                    pmus, slug, shares,
                    _fingerprint_cents(his, _row_get(r, "intent")),
                    window=(placed_ts - _ORPHAN_SKEW_S,
                            placed_ts + _ORPHAN_MATCH_S),
                    exclude=protected)
                if not readable:
                    return
                if found is not None:
                    oid = str(found.get("order_id"))
        if oid:
            await pool.execute(
                "UPDATE live_orders SET status='open', order_id=$2, "
                "error=NULL WHERE id=$1 AND status='submitting'",
                r["id"], str(oid))
            log.warning("reaper: desk row %s handed to the desk as 'open' "
                        "with order %s (never cancelled here)", r["id"], oid)
            return
        await pool.execute(
            "UPDATE live_orders SET status = 'error', "
            "error = 'stale submitting row reaped — process died "
            "mid-order; venue holds no matching bid; reconcile "
            "against the venue account' "
            "WHERE id = $1 AND status = 'submitting'", r["id"])
        return
    if _row_get(r, "status") == "error":
        err = str(_row_get(r, "error") or "")
        if err.startswith("ORPHAN FILL RECORDED"):
            # A NAMED ADD LEG MERGES FIRST (adds review, 2026-09-02): a
            # leg that could not merge when it filled -- its standing
            # row was mid-exit, or the process died -- is booked onto
            # that row now that it is 'filled' again, by the same one
            # statement the executor uses. Only when the standing row is
            # gone for good does the leg fall through to promotion.
            if _row_get(r, "add_of"):
                try:
                    # True: merged. None: standing row live but not
                    # 'filled' -- wait, never promote beside it. False:
                    # standing row gone -- promote below.
                    if await _merge_named_add_leg(pool, r) is not False:
                        return
                except Exception as exc:  # noqa: BLE001 — try again next pass
                    log.warning("reaper: named add leg %s not merged this "
                                "pass (%s: %s)", r["id"], type(exc).__name__,
                                str(exc)[:120])
                    return
            # AN ORPHAN FILL WHOSE CLAIM MAY HAVE FREED: promote it to
            # 'filled' so it is graded and exited like any copy. Refused
            # by the index while another row still holds the asset.
            try:
                await pool.execute(
                    "UPDATE live_orders SET status='filled', error=$2 "
                    "WHERE id=$1 AND status='error' "
                    "AND error LIKE 'ORPHAN FILL RECORDED%'", r["id"],
                    "reconciled by reaper: orphan fill promoted once the "
                    "asset claim freed")
                log.warning("reaper: promoted ORPHAN FILL on row %s to "
                            "'filled'", r["id"])
            except Exception:  # noqa: BLE001 — claim still held elsewhere
                pass
            return
        if err.startswith("venue has no record of order") and oid and slug:
            # A NAMED NO-RECORD ROW IS RE-ASKED (round eleven): the order
            # endpoint may answer now; failing that, the venue's own
            # trade log is read for fills of exactly that order. Until
            # one of them speaks the row stays named and un-buyable.
            res = await _reconcile_row_by_id(pool, pmus, r["id"], oid, slug,
                                             r["intent"], age,
                                             add_of=_row_get(r, "add_of"))
            if res in ("no_record", "no_record_yet"):
                await _book_from_log_by_oid(pool, pmus, r, oid, age)
            return
        if slug:
            await _adopt_lost_bid(pool, pmus, r, age)
        return
    if not oid and slug:
        # NO ID, BUT A MARKET (round five). A row killed -- or whose
        # placement response was lost -- between the venue accepting
        # the GTC and the persist has exactly this shape, and the bid
        # may be live or FILLED. Ask the venue for our fingerprint
        # before giving up on it: found -> adopt and reconcile like any
        # other id; unreadable -> leave for the next pass; nothing there
        # -> the blind mark, which is now a statement about the book and
        # the trade log, not a guess.
        oid = await _adopt_lost_bid(pool, pmus, r, age)
        if oid in ("unreadable", "booked", "position"):
            return
    if not oid or not slug:
        await pool.execute(
            "UPDATE live_orders SET status = 'error', "
            "error = 'stale submitting row reaped — process died "
            "mid-order; venue holds no matching bid; reconcile "
            "against the venue account' "
            "WHERE id = $1 AND status = 'submitting'", r["id"])
        return
    res = await _reconcile_row_by_id(pool, pmus, r["id"], oid, slug,
                                     r["intent"], age,
                                     add_of=_row_get(r, "add_of"))
    if res == "no_record":
        # The venue handed out this id and now has no record of it: its
        # trade log is the other record (round eleven).
        await _book_from_log_by_oid(pool, pmus, r, oid, age)


async def _adopt_lost_bid(pool, pmus, r, age_s: float) -> str | None:
    """For a 'submitting' row with no order id: find our bid on the
    book by fingerprint and write its id onto the row; failing that,
    ask the ACCOUNT. Returns the id, None when the venue holds nothing
    (blind-mark), "booked" / "position" when the account answered and
    the row was written here, or "unreadable" (leave it)."""
    slug = r["us_market_slug"]
    try:
        his = float(_row_get(r, "his_price") or 0.0)
        shares = float(_row_get(r, "requested_shares") or 0.0)
    except (TypeError, ValueError):
        return None
    if not (0.0 < his < 1.0) or shares < 1:
        return None
    intent = _row_get(r, "intent")
    placed_ts = time.time() - age_s
    cents = _fingerprint_cents(his, intent)
    revisit = _row_get(r, "status") == "error"
    # ORDERS THAT PREDATE OURS (round ten): the rest lane's snapshot of
    # the book before it placed, persisted on the row; none of them can
    # be our order, however well one matches.
    pre_ids = _pre_ids_of(_row_get(r, "pre_ids"))
    # ORDERS THAT ARE SOMEBODY ELSE'S BY NAME (P1 step 8, owner order
    # 2026-09-02 "go for it, let's get this working"; the panel review's
    # reaper-isolation ruling): the desk's ids and every mirror order's
    # (mirror_orders, migration 047). A mirror rest carries his cent and
    # the book's whole quantity on a market a stranded copy row may
    # share, so neither the open-book fingerprint nor the trade log's
    # order size can tell it from our orphan; the id can, and it is
    # excluded from both searches below. Unreadable is unreadable: a
    # row we cannot attribute is left for the next pass, never adopted
    # or booked on a guess -- the same outcome as an unreadable venue.
    protected = await _protected_order_ids(pool)
    if protected is None:
        log.warning("reaper: protected order ids unreadable while "
                    "reconciling row %s — leaving it for the next pass",
                    r["id"])
        return "unreadable"
    if not revisit:
        # THE OPEN BOOK, ONLY FOR A FRESH ROW AND ONLY INSIDE THE ROW'S
        # OWN WINDOW (round eight): a named row revisited 40 hours on
        # adopted -- and then cancelled and booked -- the owner's fresh
        # app bid at the whale's cent. A rest bid of ours cannot still
        # be resting hours later (the venue reaper cancels at TTL+60 s),
        # so the revisit asks only the trade log and the account.
        found, readable = await _find_lost_rest_bid(
            pmus, slug, shares, cents,
            window=(placed_ts - _ORPHAN_SKEW_S, placed_ts + _ORPHAN_MATCH_S),
            exclude=pre_ids | protected)
        if not readable:
            log.warning("reaper: venue unreadable while looking for row %s's "
                        "bid — leaving it for the next pass", r["id"])
            return "unreadable"
        if found is not None:
            oid = str(found.get("order_id"))
            await pool.execute(
                "UPDATE live_orders SET order_id=$2, status='submitting', "
                "error=NULL WHERE id=$1 AND order_id IS NULL", r["id"], oid)
            log.warning("reaper: ADOPTED lost bid %s onto row %s (%s)", oid,
                        r["id"], slug)
            return oid
    # NOTHING OPEN. A lost GTC that FILLED is not on the open book
    # (round six): the open-orders listing is exactly that, and a bid
    # resting at his price is the one most likely to fill. Ask the
    # venue's own TRADE LOG for our fingerprint -- BUY fills at our cent
    # on this market inside the row's window -- and book exactly those.
    # NEVER the account position (round seven): the account is shared
    # with the owner, and a position of "our size" was his in every
    # reproduction that mattered; booking it would have sold his shares
    # on the whale's exit. A position the trade log cannot attribute to
    # us is NAMED in a text the sweep cannot reclaim and the exit path
    # treats as in flight, and this row is revisited every pass.
    try:
        fills = await asyncio.to_thread(pmus.recent_trades, slug,
                                        placed_ts - _ORPHAN_SKEW_S)
    except Exception as exc:  # noqa: BLE001 — unreadable: leave the row
        log.error("reaper: trade log unreadable while reconciling row %s "
                  "(%s: %s) — leaving it for the next pass", r["id"],
                  type(exc).__name__, str(exc)[:120])
        return "unreadable"
    # OURS BY ORDER, NOT BY CENT (round nine, the blocker). Price, side
    # and time alone booked the OWNER's buy at the whale's cent inside
    # our window as our copy, and mirror_exit then sold his shares. The
    # venue's execution record names the ORDER each fill belongs to;
    # a fill is ours only when that order is exactly our size, on our
    # leg's side, at a cent this row authorised (the rest cent or the
    # IOC cent), inside the row's window, and unknown to every other
    # ledger row. A fill whose order the venue did not name is UNKNOWN,
    # never ours. The row is then written from the venue's ORDER
    # record by id -- the definitive quantity and average price, which
    # also books an IOC that filled below its limit. The window is
    # bounded on BOTH ends: a lost order not on the open book was
    # filled or cancelled within minutes of placement, so a fill hours
    # later is the owner's (round eight, reproduced).
    lo_ts = placed_ts - _ORPHAN_SKEW_S
    hi_ts = placed_ts + _LOST_FILL_WINDOW_S
    limit_px = _row_get(r, "limit_price")
    try:
        known = {str(x["order_id"]) for x in await pool.fetch(
            "SELECT order_id FROM live_orders WHERE order_id IS NOT NULL "
            "AND us_market_slug = $1 AND id <> $2", slug, r["id"])}
    except Exception:  # noqa: BLE001 — cannot exclude: cannot attribute
        return "unreadable"
    # ...and the snapshot's ids and the protected set (above): a fill the
    # venue names for a mirror order is the mirror's, whatever its size.
    known |= pre_ids | protected
    in_window = [f for f in fills or []
                 if lo_ts <= float(f.get("ts") or 0.0) <= hi_ts]
    ours = [f for f in in_window
            if _lost_fill_is_ours(f, his, intent, shares, lo_ts, hi_ts,
                                  limit_px)
            and str(f.get("order_id") or "") not in known]
    if in_window and any(f.get("order_qty") is None for f in in_window):
        log.error("reaper: %d fill(s) on %s inside row %s's window carry "
                  "no execution order in the venue feed — a fill with no "
                  "order is UNKNOWN and is never booked as ours",
                  sum(1 for f in in_window if f.get("order_qty") is None),
                  slug, r["id"])
    ids = {str(f["order_id"]) for f in ours if f.get("order_id")}
    if len(ids) > 1:
        log.error("reaper: %d distinct orders of row %s's exact size at "
                  "its cent on %s inside its window — cannot attribute; "
                  "naming the row", len(ids), r["id"], slug)
        ours = []
    if len(ids) == 1:
        oid = next(iter(ids))
        keep = False
        try:
            await pool.execute(
                "UPDATE live_orders SET order_id=$2, status='submitting', "
                "error=NULL WHERE id=$1 AND order_id IS NULL", r["id"], oid)
        except Exception as exc:  # noqa: BLE001
            if "unique" not in type(exc).__name__.lower() and \
                    "unique" not in str(exc).lower():
                return "unreadable"
            keep = True          # another row holds the asset claim
        res = await _reconcile_row_by_id(pool, pmus, r["id"], oid, slug,
                                         intent, age_s, keep_status=keep,
                                         add_of=_row_get(r, "add_of"))
        if res in ("filled", "unfilled"):
            await _stamp_lost_fill_lane(pool, r["id"], ours, his, intent)
            log.warning("reaper: row %s reconciled from the venue ORDER "
                        "record %s named by its own trade log (%s)",
                        r["id"], oid, res)
            return "booked"
        if res == "left":
            # STILL LIVE IS STILL LIVE (round ten): the venue says the
            # order named by its own log is not terminal yet. The row
            # now carries the id, so the next pass reconciles it by id
            # and books whatever it ends as; booking today's partial sum
            # here would terminalise the row under a live order.
            return "unreadable"
        # 'no_record': the venue showed the fill in its log but would
        # not name the order it belongs to. Book what the log itself
        # shows, below.
    qty = sum(float(f.get("qty") or 0.0) for f in ours)
    if 0.0 < qty <= float(int(shares)) + 1e-6:
        px = float(ours[0]["price"])
        fill_intent = intent or "ORDER_INTENT_BUY_LONG"
        spent = fill_cash(qty, px, fill_intent)
        _text = (f"reconciled from the venue trade log: lost order of our exact "
                 f"size filled while unattended ({len(ours)} fill(s) inside the "
                 f"row's window)")
        # AN ADD LEG IS MERGED, NEVER BOOKED BESIDE ITS ROW (adds review
        # round two): the third reaper booking site. Same rule as
        # _book_from_log_by_oid -- merge onto the standing row, or record
        # the figures under the orphan name; never a bare 'filled'.
        _booked = None
        if _row_get(r, "add_of"):
            # the log named no order id here (that branch returned above)
            _booked = await _book_add_leg_if_any(
                pool, r["id"], qty, px, spent, None, _text)
        if _booked is None:
            await pool.execute(
                "UPDATE live_orders SET status='filled', filled_shares=$2, "
                "fill_price=$3, filled_usd=$4, error=$5 "
                "WHERE id=$1 AND status IN ('submitting', 'error')",
                r["id"], qty, px, spent, _text)
        await _stamp_lost_fill_lane(pool, r["id"], ours, his, intent)
        log.warning("reaper: BOOKED lost fill of %s @ %s on row %s from the "
                    "venue trade log", qty, px, r["id"])
        return "booked"
    if str(_row_get(r, "whale_username") or "") in _DESK_SLEEVES:
        return None          # the desk's position is the owner's, by design
    # No fill of ours in the log. Is there a position the ledger cannot
    # explain at all? Only then is the row named; a clean account is a
    # clean blind mark.
    try:
        held, _avg = await _pm_held(slug)
    except Exception as exc:  # noqa: BLE001
        log.warning("reaper: account unreadable while reconciling row %s "
                    "(%s) — leaving it for the next pass", r["id"],
                    type(exc).__name__)
        return "unreadable"
    if not held or held <= 0:
        if _row_get(r, "status") == "error":
            # A named row whose position has cleared without a fill of
            # ours: resolved, and the exit path stops waiting on it.
            await pool.execute(
                "UPDATE live_orders SET status='unfilled', error=$2 "
                "WHERE id=$1 AND status='error'", r["id"],
                "reconciled by reaper: the position this row was named for "
                "has cleared; no fill of ours in the venue trade log")
            return "booked"
        return None
    try:
        explained = float(await pool.fetchval(
            "SELECT COALESCE(SUM(filled_shares), 0) FROM live_orders "
            "WHERE us_market_slug = $1 AND id <> $2 "
            "AND status IN ('filled', 'exiting')", slug, r["id"]) or 0.0)
    except Exception:  # noqa: BLE001
        return "unreadable"
    if float(held) - explained <= 1e-6:
        return None          # every share on the account is somebody's row
    await pool.execute(
        "UPDATE live_orders SET status='error', error=$2 "
        "WHERE id=$1 AND status IN ('submitting', 'error')", r["id"],
        f"venue holds a POSITION on this market that no ledger row explains "
        f"(held {held}, explained {int(explained)}, ours would be "
        f"{int(shares)}; no fill at our cent in the trade log) — reconcile "
        f"against the venue account")
    if _row_get(r, "status") != "error":
        log.error("reaper: venue holds a POSITION of %s on %s that no ledger "
                  "row explains (row %s would be %s) — named; the sweep "
                  "cannot reclaim it and the exit path treats it as in "
                  "flight", held, slug, r["id"], int(shares))
    return "position"


async def _reconcile_row_by_id(pool, pmus, row_id, oid: str, slug: str,
                               intent: str | None, age_s: float,
                               keep_status: bool = False,
                               add_of=None) -> str:
    """cancel -> read until terminal -> write what happened.

    Shared by the ledger reaper (a 'submitting' row with an id) and the
    venue reaper (an orphan it just matched to a row). Returns 'filled',
    'unfilled', 'left' or 'no_record'. With keep_status the fill fields
    are written but the row's status is left as it is (the row cannot
    re-enter 'filled' because another row holds the asset claim); the
    error text says so, so the fill is recorded and never silent."""
    try:
        await asyncio.to_thread(pmus.cancel_order, oid, slug)
    except Exception:  # noqa: BLE001 — read anyway
        pass
    st = None
    for _i in range(3):
        try:
            st = await asyncio.to_thread(pmus.order_status, oid)
        except Exception:  # noqa: BLE001
            st = None
        if _rest_terminal(st):
            break
        await asyncio.sleep(0.3)
    if not _rest_terminal(st):
        if st is None and age_s >= _NO_RECORD_GIVE_UP_S:
            # THE VENUE HAS NAMED NO SUCH ORDER FOR AN HOUR. Every read
            # came back empty (pmus.order_status's "no record" shape) or
            # raised. An order the venue cannot name is not resting on
            # its book; what it may have DONE is unknowable from here,
            # so the row is marked for reconciliation against the
            # account -- loudly, and only after a bound, so a row cannot
            # hold its market's one-per-asset claim forever on a venue
            # that will never answer. Inside the bound it is left alone.
            # The text is a NAMED state (round eleven): the sweep, the
            # reclaim and never-add refuse to re-buy while it stands, the
            # exit path treats it as in flight, and the reaper revisits
            # it -- the venue's trade log may still name the fill.
            await pool.execute(
                "UPDATE live_orders SET status='error', error=$2 "
                "WHERE id=$1 AND status IN ('submitting', 'error')", row_id,
                f"venue has no record of order {oid} after "
                f"{int(age_s)}s — reconcile against the venue account")
            log.error("reaper: venue has NO RECORD of order %s on row %s "
                      "after %ss — named; reconcile the account",
                      oid, row_id, int(age_s))
            return "no_record"
        if st is None:
            # NO RECORD IS NOT "STILL LIVE" (round eleven, reproduced):
            # every read came back empty or raised, so nothing says the
            # order is resting. Reported apart from 'left' so a caller
            # holding the venue's own trade log can book from it instead
            # of waiting an hour for a record that never comes.
            log.warning("reaper: venue has no record of order %s on row %s "
                        "yet (%ss); leaving it for the next pass",
                        oid, row_id, int(age_s))
            return "no_record_yet"
        # Still not terminal after a cancel and three reads: leave it
        # for the next pass rather than guess. The row keeps its claim
        # and never-add keeps refusing to stack on it.
        log.warning("reaper: order %s on row %s still non-terminal; "
                    "leaving 'submitting' for the next pass", oid, row_id)
        return "left"
    filled = float(st.get("filled_shares") or 0)
    if filled > 0:
        px = st.get("avg_px") or st.get("price")
        # THE SIDE. The row's intent comes from its raw, which the rest
        # cycle persists with the id; if that raw is absent (killed
        # between create and persist), the venue's own order record
        # names it. Never sideless.
        _int = intent or st.get("intent")
        spent = fill_cash(filled, float(px) if px else None, _int)
        if keep_status:
            await pool.execute(
                "UPDATE live_orders SET order_id=$6, filled_shares=$2, "
                "fill_price=$3, filled_usd=$4, error=$5 WHERE id=$1",
                row_id, filled, float(px) if px else None, spent,
                "ORPHAN FILL RECORDED on a row that cannot re-enter 'filled' "
                "(another row holds this asset's claim) — reconcile against "
                "the venue account", oid)
        else:
            # AN ADD LEG IS MERGED, NEVER BOOKED BESIDE ITS ROW (adds
            # review, 2026-09-02): a leg reconciled here died before its
            # merge. Its fill goes onto the standing row by the same one
            # statement the executor uses; if that row is not 'filled'
            # right now, the figures are recorded on the leg under the
            # orphan name and the next pass merges or promotes it.
            _booked = None
            if add_of:
                _booked = await _book_add_leg_if_any(
                    pool, row_id, filled, float(px) if px else None, spent, oid,
                    "reconciled by reaper: add leg filled while unattended")
            if _booked is None:
                await pool.execute(
                    "UPDATE live_orders SET status='filled', "
                    "filled_shares=$2, fill_price=$3, filled_usd=$4, "
                    "error=$5 WHERE id=$1 AND status IN ('submitting', 'error')",
                    row_id, filled, float(px) if px else None, spent,
                    "reconciled by reaper: resting order filled while unattended")
        try:
            # THE VENUE NAMES THE LANE (round ten): an IOC/FOK record is
            # an IOC fill whatever the row's lane says; a resting order
            # is a rest fill only where no lane was stamped.
            tif = str(st.get("tif") or "").upper()
            lane = "ioc" if tif in _IOC_TIFS else None
            await pool.execute(
                "UPDATE live_orders SET lane=COALESCE($2, lane, 'rest') "
                "WHERE id=$1", row_id, lane)
        except Exception:  # noqa: BLE001 — stamp is best-effort
            pass
        log.warning("reaper: reconciled UNATTENDED FILL %s shares on "
                    "row %s (order %s)%s", filled, row_id, oid,
                    " — status kept, asset claim held elsewhere"
                    if keep_status else "")
        return "filled"
    if keep_status:
        await pool.execute(
            "UPDATE live_orders SET order_id=$2, error='reconciled by "
            "reaper: orphan cancelled unfilled (asset claim held by another "
            "row)' WHERE id=$1", row_id, oid)
        return "unfilled"
    await pool.execute(
        "UPDATE live_orders SET status='unfilled', "
        "error='reconciled by reaper: resting order cancelled "
        "unfilled' WHERE id=$1 AND status IN ('submitting', 'error')", row_id)
    return "unfilled"


async def _book_from_log_by_oid(pool, pmus, r, oid: str, age_s: float) -> bool:
    """The venue's ORDER endpoint has no record of an id the venue itself
    handed out (or its own trade log named); the TRADE LOG is its other
    record. Book the BUY fills of exactly that order inside the row's
    window -- attribution by order id, never by price -- and nothing
    else. Returns True when a fill was written. (Round eleven,
    reproduced: a log-named fill whose order the endpoint could not
    name ended in an unprotected 'error' row, unbooked and re-buyable.)
    """
    slug = r["us_market_slug"]
    placed_ts = time.time() - age_s
    lo_ts, hi_ts = placed_ts - _ORPHAN_SKEW_S, placed_ts + _LOST_FILL_WINDOW_S
    try:
        fills = await asyncio.to_thread(pmus.recent_trades, slug, lo_ts)
    except Exception as exc:  # noqa: BLE001 — unreadable is not "no fill"
        log.warning("reaper: trade log unreadable for row %s (%s)", r["id"],
                    type(exc).__name__)
        return False
    ours = [f for f in fills or []
            if str(f.get("order_id") or "") == str(oid)
            and "BUY" in str(f.get("side") or "").upper()
            and lo_ts <= float(f.get("ts") or 0.0) <= hi_ts]
    qty = sum(float(f.get("qty") or 0.0) for f in ours)
    try:
        shares = float(_row_get(r, "requested_shares") or 0.0)
    except (TypeError, ValueError):
        shares = 0.0
    if not (0.0 < qty <= float(int(shares)) + 1e-6):
        return False
    px = float(ours[0]["price"])
    intent = _row_get(r, "intent")
    spent = fill_cash(qty, px, intent or "ORDER_INTENT_BUY_LONG")
    text = (f"reconciled from the venue trade log: order {oid} has no record "
            f"at the order endpoint but {len(ours)} fill(s) in the log")
    # AN ADD LEG IS MERGED, NEVER BOOKED BESIDE ITS ROW (adds review,
    # 2026-09-02); see _book_add_leg_if_any.
    if _row_get(r, "add_of"):
        _booked = await _book_add_leg_if_any(pool, r["id"], qty, px, spent,
                                             oid, text)
        if _booked is not None:
            await _stamp_lost_fill_lane(pool, r["id"], ours,
                                        float(_row_get(r, "his_price") or 0.0),
                                        intent)
            return True
    try:
        await pool.execute(
            "UPDATE live_orders SET status='filled', filled_shares=$2, "
            "fill_price=$3, filled_usd=$4, error=$5 "
            "WHERE id=$1 AND status IN ('submitting', 'error')",
            r["id"], qty, px, spent, text)
    except Exception as exc:  # noqa: BLE001
        if "unique" not in type(exc).__name__.lower() and \
                "unique" not in str(exc).lower():
            return False
        # another row holds the asset claim: record the fill, keep status
        await pool.execute(
            "UPDATE live_orders SET filled_shares=$2, fill_price=$3, "
            "filled_usd=$4, error=$5 WHERE id=$1", r["id"], qty, px, spent,
            "ORPHAN FILL RECORDED on a row that cannot re-enter 'filled' "
            "(another row holds this asset's claim) — " + text)
    try:
        his = float(_row_get(r, "his_price") or 0.0)
    except (TypeError, ValueError):
        his = 0.0
    await _stamp_lost_fill_lane(pool, r["id"], ours, his, intent)
    log.warning("reaper: BOOKED %s @ %s on row %s from the venue trade log "
                "for order %s (no record at the order endpoint)",
                qty, px, r["id"], oid)
    return True


async def _reap_stale_resting_bids(pool=None) -> int:
    """Cancel any resting BUY on the venue older than the rest window.

    THE NET UNDER THE NET. The ledger reaper above finds a bid through
    the row that names it. This one asks the VENUE, so a bid the ledger
    never saw -- placed in the millisecond before the process was
    killed, before the id could be persisted -- is still found and
    cancelled. Only BUY-side, only older than REST_BID_TTL_S plus a
    minute of margin: the manual desk's resting orders are the owner's
    and are not touched, which is what the whale_username filter on
    sync_open_manual_orders already protects on the ledger side.
    Returns the number cancelled; never raises.
    """
    from . import pmus

    try:
        orders = await asyncio.to_thread(pmus.open_orders)
    except Exception as exc:  # noqa: BLE001 — a blind reaper is a no-op
        log.warning("resting-bid reaper: open_orders unreadable (%s)",
                    type(exc).__name__)
        return 0
    # THE DESK'S RESTING ORDERS ARE THE OWNER'S, AND THE MIRROR'S ARE
    # THE MIRROR'S. Collect both id sets from the ledger so a manual GTC
    # is never swept, whatever its age -- and neither is a mirror rest
    # (P1 step 8, owner order 2026-09-02 "go for it, let's get this
    # working"; the panel review's reaper-isolation ruling). A mirror
    # order lives in mirror_orders, a table the scope query below never
    # reads, and it rests at HIS cent for the book's whole quantity on a
    # market a stranded copy row may share: exactly the fingerprint this
    # pass matches on. The id set is the belt for the one reaper that
    # asks the VENUE instead of the ledger.
    protected_ids: set[str] = set()
    if pool is not None:
        protected = await _protected_order_ids(pool)
        if protected is None:
            # Without the exclusion list we cannot tell a desk order or
            # a mirror rest from an orphaned bid, so we cancel neither:
            # the stance this pass has always taken. The log text is
            # kept verbatim (it names the read that used to be the only
            # one); a mirror_orders table that migration 047 has not yet
            # created reads the same way, and the pass sweeps nothing
            # until it has.
            log.warning("resting-bid reaper: manual ids unreadable — "
                        "skipping this pass")
            return 0
        protected_ids = protected
    # ONLY ROWS THAT CAN OWN AN ORDER THE LEDGER NEVER SAW. The owner
    # rests bids directly in the venue app on this shared account, and
    # those have no ledger row at all -- an account-wide sweep cancelled
    # them (re-review 2026-09-01, reproduced). Round two narrowed the
    # sweep to markets with ANY copy row in the last hour; round three
    # reproduced the remaining hole: a 59-minute-old FILLED copy row on
    # S put the owner's two-minute-old app bid on S in scope, and the
    # sweep cancelled it. A row that reached a terminal status has no
    # unpersisted order behind it -- an IOC never rests, and a rest
    # outcome was cancelled or filled and written -- so scope is exactly
    # the orphan's signature: a 'submitting' row with NO order_id (killed
    # between orders.create and the persist), or the ledger reaper's own
    # blind mark of that same row ten minutes on. And the bid is matched
    # to its row IN TIME below, not by market alone: it was created
    # within seconds of the row, never before it.
    try:
        scope: dict[str, list[dict]] = {}
        if pool is not None:
            for r in await pool.fetch(
                    "SELECT id, us_market_slug, "
                    "       EXTRACT(EPOCH FROM placed_at)::float8 AS placed_ts, "
                    "       his_price::float8 AS his_price, "
                    "       requested_shares::float8 AS requested_shares, "
                    "       (raw->'pre_ids')::text AS pre_ids, "
                    f"       {ORDER_INTENT_SQL} AS intent "
                    "FROM live_orders WHERE us_market_slug IS NOT NULL "
                    "AND COALESCE(whale_username,'') NOT IN ('manual','underdog') "
                    "AND order_id IS NULL "
                    "AND (status = 'submitting' OR (status = 'error' AND "
                    "     error LIKE 'stale submitting row reaped%')) "
                    "AND placed_at > now() - interval '60 minutes'"):
                scope.setdefault(str(r["us_market_slug"]).lower(), []).append(
                    {"id": _row_get(r, "id"), "placed_ts": float(r["placed_ts"]),
                     "intent": _row_get(r, "intent"),
                     "his_price": _row_get(r, "his_price"),
                     "shares": _row_get(r, "requested_shares"),
                     "pre_ids": _pre_ids_of(_row_get(r, "pre_ids"))})
    except Exception:  # noqa: BLE001 — unreadable: sweep nothing
        log.warning("resting-bid reaper: copy rows unreadable — "
                    "skipping this pass")
        return 0
    if not scope:
        return 0
    cutoff_s = REST_BID_TTL_S + 60.0
    n = 0
    now = time.time()
    for o in orders:
        if o.get("side") != "BUY" or not o.get("order_id"):
            continue
        if str(o.get("order_id")) in protected_ids:
            continue          # the desk's, or the mirror's: not ours to touch
        slug = str(o.get("us_market_slug") or "").lower()
        if slug not in scope:
            continue
        created_ts = _order_created_ts(o.get("created_at"))
        if created_ts is None:
            # AN UNKNOWN AGE IS NOT AN OLD ONE. Treating it as old put
            # the fail-open default on the cancel side: a shape surprise
            # in createTime would have cancelled the lane's own bids two
            # seconds into their window. Skip and say so.
            log.warning("resting-bid reaper: order %s has no parseable "
                        "age — leaving it", o.get("order_id"))
            continue
        age = now - created_ts
        if age < cutoff_s:
            continue
        # THE BID MUST BELONG TO THE ROW IN TIME. The row is INSERTed
        # and slug-stamped seconds before placement (mapping, preview,
        # IOC, then the GTC), so an orphan's createTime falls inside a
        # short window AFTER its row's placed_at. A bid created before
        # the row, or long after it, is somebody else's -- the owner's.
        owner_row = next(
            (row for row in scope[slug]
             if row["placed_ts"] - _ORPHAN_SKEW_S <= created_ts
             <= row["placed_ts"] + _ORPHAN_MATCH_S
             # an order the row saw on the book BEFORE it placed is not
             # the row's (round ten: the lane's snapshot, persisted)
             and str(o.get("order_id")) not in row.get("pre_ids", ())), None)
        if owner_row is None:
            continue
        # TIME IS NOT ENOUGH; THE BID MUST CARRY OUR FINGERPRINT (round
        # six, reproduced: the owner's 500 @0.61 rested 20 s before a
        # stranded row on the same market was cancelled AND booked as
        # our 100 @0.474 rest fill -- and mirror_exit would then have
        # sold his position out from under him). Our own orphan always
        # carries the cent the adapter sent for the row's whale price
        # and the row's whole share count; anything else is not ours,
        # and is neither cancelled nor adopted.
        try:
            his = float(owner_row.get("his_price") or 0.0)
            want = float(owner_row.get("shares") or 0.0)
        except (TypeError, ValueError):
            his, want = 0.0, 0.0
        if not (0.0 < his < 1.0) or want < 1:
            continue
        cents = _fingerprint_cents(his, owner_row.get("intent") or o.get("intent"))
        if not _bid_matches(o, cents, want):
            log.warning("resting-bid reaper: BUY %s on %s is inside row %s's "
                        "time window but is not our bid (price %s qty %s vs "
                        "ours %s x %s) — leaving it", o.get("order_id"), slug,
                        owner_row.get("id"), o.get("price"), o.get("quantity"),
                        sorted(cents), int(want))
            continue
        try:
            c = await asyncio.to_thread(pmus.cancel_order, o["order_id"],
                                        o.get("us_market_slug") or "")
            if c.get("ok"):
                n += 1
                log.warning("resting-bid reaper: cancelled unattended BUY "
                            "%s on %s (age %ss)", o["order_id"],
                            o.get("us_market_slug"),
                            round(age, 1) if age is not None else "?")
        except Exception:  # noqa: BLE001
            pass
        # ADOPT, THEN RECONCILE (round five). Cancelling and walking
        # away wrote nothing: a bid that had already filled 40 of 100
        # shares left a position on the account with no ledger row --
        # never graded, never exited, never charged to the budget. The
        # matched row takes the order id and is reconciled the same way
        # a persisted id is: cancel, read the terminal state, write
        # filled or unfilled. Best-effort; the cancel above stands.
        if owner_row.get("id") is None or pool is None:
            continue
        keep_status = False
        try:
            await pool.execute(
                "UPDATE live_orders SET order_id=$2, status='submitting', "
                "error=NULL WHERE id=$1 AND order_id IS NULL",
                owner_row["id"], str(o["order_id"]))
        except Exception as exc:  # noqa: BLE001
            # THE ASSET CLAIM IS HELD BY ANOTHER ROW (round six): the
            # blind mark released it and a later copy took it, so this
            # row cannot re-enter 'submitting' (migration 011's
            # one-fill-per-asset index). The fill is still recorded on
            # the row as it stands -- never silent -- with the status
            # left alone.
            if "unique" not in type(exc).__name__.lower() and \
                    "unique" not in str(exc).lower():
                # ANY failure to adopt still records the fill (round
                # nine): the bid above is already cancelled, so walking
                # away here left a partial fill on it in no row at all.
                log.warning("resting-bid reaper: adopt of %s onto row %s "
                            "failed (%s) — recording the outcome with the "
                            "row's status kept", o["order_id"],
                            owner_row["id"], type(exc).__name__)
            keep_status = True
        try:
            await _reconcile_row_by_id(
                pool, pmus, owner_row["id"], str(o["order_id"]),
                o.get("us_market_slug") or "",
                owner_row.get("intent") or o.get("intent"),
                now - owner_row["placed_ts"], keep_status=keep_status)
        except Exception as exc:  # noqa: BLE001 — the cancel stands
            log.warning("resting-bid reaper: reconcile of %s onto row "
                        "%s failed (%s)", o["order_id"], owner_row["id"],
                        type(exc).__name__)
    return n


async def _reap_stale_exiting(pool) -> int:
    """Resolve rows stranded in 'exiting' AGAINST THE VENUE.

    Nothing reaped this status. _reap_stale_submitting covers
    'submitting' only, and a row can reach 'exiting' and stay there:
    mirror_exit claims it with an atomic UPDATE and every path back out
    was an `except Exception`, which asyncio.CancelledError bypasses.
    copy_sweep wraps maybe_execute in a 60s wait_for and reaches
    mirror_exit through classify_exit, and Render's redeploy SIGTERM
    cancels every in-flight exit at once.

    A stranded row is invisible three ways over: the settlement sweep
    selects status='filled' so it is never graded, mirror_exit's own row
    query requires 'filled' so it can never be sold, and copy_sweep's
    blocking list contains 'exiting' so the market can never be
    re-entered. The shares sit at the venue and no number in the system
    moves.

    A timestamp cannot answer this and neither can we: the question is
    whether the sale executed, and only the venue knows. So each row is
    reconciled against the account's live position.

      venue holds nothing  -> the sale went through. Mark it cashed_out
                              with settled_at, and say in the error text
                              that the fill price was never captured, so
                              the P&L on this row is not trustworthy.
      venue still holds    -> the order never landed. Release to
                              'filled' and let the normal path retry.
      venue unreadable     -> leave it alone. An outage must not be able
                              to decide either way.

    Ten minutes, matching the submitting reaper: an exit is a claim, a
    held lookup, and one IOC — tens of seconds at worst.
    """
    try:
        rows = await pool.fetch(
            "SELECT id, us_market_slug FROM live_orders "
            "WHERE status = 'exiting' "
            "  AND placed_at < now() - interval '10 minutes' "
            "LIMIT 50")
    except Exception:  # noqa: BLE001 — a reaper never breaks its caller
        log.exception("stale-exiting reap could not read")
        return 0
    fixed = 0
    for r in rows or []:
        slug = r["us_market_slug"]
        if not slug:
            continue
        try:
            held, _avg = await _pm_held(slug)
        except Exception:  # noqa: BLE001 — unreadable venue decides nothing
            log.warning("stale-exiting %s: venue unreadable, left alone",
                        slug)
            continue
        # SHARES OTHER ROWS EXPLAIN ARE NOT THIS ROW'S (adds review round
        # two): a named add leg on the same market holds venue shares of
        # its own, so an account still holding after this row's sale is
        # not proof the sale never landed. Only the unexplained remainder
        # decides. Unreadable -> treat as nothing explained (as before).
        try:
            _explained = float(await pool.fetchval(
                "SELECT COALESCE(sum(filled_shares), 0)::float8 FROM live_orders "
                "WHERE us_market_slug = $1 AND id <> $2 "
                "AND (status = 'filled' OR (status = 'error' "
                "     AND error LIKE 'ORPHAN FILL RECORDED%'))", slug, r["id"]) or 0.0)
        except Exception:  # noqa: BLE001
            _explained = 0.0
        try:
            if held - _explained < 1:
                await pool.execute(
                    "UPDATE live_orders SET status='cashed_out', "
                    "settled_at=now(), error=$2 WHERE id=$1 "
                    "AND status='exiting'",
                    r["id"],
                    "exit completed but the process died before "
                    "recording it — venue holds nothing; fill price and "
                    "pnl on this row were never captured")
                log.warning("stale-exiting %s: venue holds nothing — the "
                            "sale executed; row retired", slug)
            else:
                await _release_exit_claim(pool, r["id"])
                log.warning("stale-exiting %s: venue still holds %d — "
                            "the order never landed; released to filled",
                            slug, held)
            fixed += 1
        except Exception:  # noqa: BLE001 — one row, not the sweep
            log.exception("stale-exiting %s: could not resolve", slug)
    return fixed


async def execute_manual(asset: str, usd: float, note: str = "",
                         us_slug: str = "",
                         ask_hint: float | None = None) -> dict:
    """Place an admin-directed BUY: FOK limit at the live ask +2c
    protection, whole contracts rounded down so the ticket never
    exceeds the requested budget. Returns a UI-ready result dict —
    every refusal is a named reason, never an exception (an unhandled
    500 loses its CORS headers and reads as a blank network failure on
    the desk — observed 2026-08-07).

    us_slug (owner order 2026-08-12, the full game board): a desk row
    sourced from the venue's own event listing executes DIRECTLY by
    its orderable slug — no catalog asset required."""
    try:
        return await _execute_manual(asset, usd, note, us_slug=us_slug,
                                     ask_hint=ask_hint)
    except Exception as exc:  # noqa: BLE001 — the desk reports, never 500s
        log.exception("manual order failed pre-flight")
        return {"ok": False,
                "error": f"{type(exc).__name__}: {str(exc)[:160]}"}


async def _execute_manual(asset: str, usd: float, note: str = "",
                          us_slug: str = "",
                          ask_hint: float | None = None) -> dict:
    cfg = settings()
    venue = active_venue()
    if venue != "polymarket-us":
        return {"ok": False, "error": "live venue not armed"}
    if not (0 < usd <= MANUAL_MAX_PER_ORDER_USD):
        return {"ok": False,
                "error": f"size must be $0-{MANUAL_MAX_PER_ORDER_USD:.0f}"}
    pool = await get_pool()
    if await _is_paused(pool):
        return {"ok": False, "error": "live trading paused (kill switch)"}
    # open resting commitments count (owner order 2026-08-28) — the
    # budget is what the day PROMISED, not only what already filled
    day_spent = await _manual_day_spent(pool)
    if day_spent + usd > MANUAL_DAILY_USD:
        return {"ok": False,
                "error": (f"manual day budget exhausted "
                          f"(${day_spent:.2f} of ${MANUAL_DAILY_USD:.0f} "
                          "in 24h, open orders included)")}
    if us_slug and not asset:
        return await _execute_manual_slug(pool, us_slug, usd, note,
                                          ask_hint, venue)
    # Double-click / impatient-retry guard: placement takes tens of
    # seconds (market resolution + preview + FOK), and a retried request
    # while the first is in flight would buy twice. This SELECT is the
    # friendly fast path; the race-proof backstop is the partial unique
    # index live_orders_manual_one_inflight (migration 023) enforced at
    # the INSERT below — two concurrent submits cannot both pass a
    # check-then-act SELECT, but they cannot both win the index.
    await _reap_stale_submitting(pool)
    inflight = await pool.fetchval(
        "SELECT 1 FROM live_orders WHERE whale_username = 'manual' "
        "AND asset = $1 AND status = 'submitting' "
        "AND placed_at > now() - interval '3 minutes' LIMIT 1",
        str(asset))
    if inflight:
        return {"ok": False,
                "error": "an order for this market is already in flight "
                         "— check the blotter in a few seconds"}
    ctx = await _market_context(pool, {"asset": str(asset)})
    if not ctx.get("outcome"):
        return {"ok": False, "error": "unknown asset — pick from search"}
    ask = await _clob_best_ask(cfg, str(asset))
    if ask is None or not (0 < ask < 1):
        return {"ok": False, "error": "no live order book for this outcome"}
    limit = round(min(ask + 0.02, 0.99), 2)
    shares = int(usd / limit)
    if shares < 1:
        return {"ok": False, "error": "budget buys zero whole contracts"}
    from . import pmus

    try:
        row_id = await pool.fetchval(
            """
            INSERT INTO live_orders (trade_id, whale_username, asset,
                                     condition_id, side, his_price,
                                     limit_price, requested_usd,
                                     requested_shares, status, venue)
            VALUES (NULL, 'manual', $1, $2, 'BUY', $3, $4, $5, $6,
                    'submitting', $7)
            RETURNING id
            """,
            str(asset), None, ask, limit, round(shares * limit, 2),
            float(shares), venue)
    except asyncpg.UniqueViolationError:
        return {"ok": False,
                "error": "an order for this market is already in flight "
                         "— check the blotter in a few seconds"}
    try:
        # Deterministic mapping ONLY (no fuzzy fallback): the desk's
        # first ticket mapped onto a player prop via the full-text
        # search. Exact slug-grammar candidates or a clean refusal.
        mapping = await asyncio.to_thread(
            pmus.resolve_market_exact,
            _us_slug_candidates(ctx.get("market_slug")
                                or ctx.get("event_slug") or "",
                                ctx.get("outcome") or ""),
            ctx.get("outcome"))
        if mapping is None:
            await pool.execute(
                "UPDATE live_orders SET status='rejected', error=$2 "
                "WHERE id=$1",
                row_id, "no verified Polymarket US market for this outcome")
            return {"ok": False, "row_id": row_id,
                    "error": "no US market maps exactly to this outcome"}
        await pool.execute(
            "UPDATE live_orders SET us_market_slug=$2 WHERE id=$1",
            row_id, mapping["market_slug"])
        # Desk orders take the book's available size at the protected
        # limit and cancel the rest (owner order 2026-08-21) — same
        # partial-take contract as the copy path.
        # The desk's resolver names the side (2026-08-24): without this
        # the last-gate backstop refuses every two-sided market — which
        # on this venue is every sports market — and the desk cannot
        # trade at all. Fail-closed was correct; staying broken is not.
        result = await asyncio.to_thread(
            pmus.submit_fok, mapping["market_slug"], limit, shares,
            False, "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL",
            mapping.get("intent"))
        filled = float(result["filled_shares"]) if result["ok"] else 0.0
        fill_price = float(result["fill_price"]) if result["ok"] else None
        await pool.execute(
            """
            UPDATE live_orders
            SET status=$2, order_id=$3, filled_shares=$4, fill_price=$5,
                filled_usd=$6, raw=$7::jsonb, error=$8
            WHERE id=$1
            """,
            row_id,
            "filled" if result["ok"] and filled > 0 else "unfilled",
            result.get("order_id"), filled, fill_price,
            round(filled * (fill_price or 0), 2),
            json.dumps(result.get("raw"), default=str),
            None if result["ok"] else str(result.get("raw"))[:300])
        log.info("MANUAL order %s: %.0f shares @ %.2f (%s)",
                 "FILLED" if filled > 0 else "unfilled", filled,
                 fill_price or limit, note[:80] or "no note")
        return {"ok": bool(result["ok"] and filled > 0), "row_id": row_id,
                "filled_shares": filled, "fill_price": fill_price,
                "limit_price": limit, "quoted_ask": ask,
                "us_market_slug": mapping["market_slug"],
                "title": ctx.get("market_title"),
                "outcome": ctx.get("outcome"),
                "error": (None if result["ok"] and filled > 0 else
                          "order did not fill at the protected limit")}
    except Exception as exc:  # noqa: BLE001 — the desk reports, never crashes
        log.exception("manual order failed (row %s)", row_id)
        await pool.execute(
            "UPDATE live_orders SET status='error', error=$2 WHERE id=$1",
            row_id, str(exc)[:300])
        return {"ok": False, "row_id": row_id,
                "error": f"{type(exc).__name__}: {str(exc)[:160]}"}


async def _execute_manual_slug(pool, us_slug: str, usd: float, note: str,
                               ask_hint: float | None, venue: str) -> dict:
    """Slug-direct manual BUY (owner order 2026-08-12: every venue
    market on a game's board must be executable). The row came from
    the venue's OWN event listing, so there is no catalog asset —
    the quote is re-read server-side from the slug; the client's
    price is accepted only as a bounded fallback, and either way the
    FOK limit caps what can actually be paid."""
    from . import pmus

    surrogate = f"slug:{us_slug}"[:120]
    await _reap_stale_submitting(pool)
    inflight = await pool.fetchval(
        "SELECT 1 FROM live_orders WHERE whale_username = 'manual' "
        "AND asset = $1 AND status = 'submitting' "
        "AND placed_at > now() - interval '3 minutes' LIMIT 1", surrogate)
    if inflight:
        return {"ok": False,
                "error": "an order for this market is already in flight "
                         "— check the blotter in a few seconds"}
    ask = await asyncio.to_thread(pmus.slug_ask, us_slug)
    if ask is None and ask_hint and 0 < float(ask_hint) < 1:
        ask = float(ask_hint)
    if ask is None or not (0 < ask < 1):
        return {"ok": False, "error": "no live quote for this market"}
    limit = round(min(ask + 0.02, 0.99), 2)
    shares = int(usd / limit)
    if shares < 1:
        return {"ok": False, "error": "budget buys zero whole contracts"}
    try:
        row_id = await pool.fetchval(
            """
            INSERT INTO live_orders (trade_id, whale_username, asset,
                                     condition_id, side, his_price,
                                     limit_price, requested_usd,
                                     requested_shares, status,
                                     venue, us_market_slug)
            VALUES (NULL, 'manual', $1, $2, 'BUY', $3, $4, $5, $6,
                    'submitting', $7, $8)
            RETURNING id
            """,
            surrogate, None, ask, limit, round(shares * limit, 2),
            float(shares), venue, us_slug)
    except asyncpg.UniqueViolationError:
        return {"ok": False,
                "error": "an order for this market is already in flight "
                         "— check the blotter in a few seconds"}
    try:
        result = await asyncio.to_thread(
            pmus.submit_fok, us_slug, limit, shares,
            False, "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL")
        filled = float(result["filled_shares"]) if result["ok"] else 0.0
        fill_price = float(result["fill_price"]) if result["ok"] else None
        await pool.execute(
            """
            UPDATE live_orders
            SET status=$2, order_id=$3, filled_shares=$4, fill_price=$5,
                filled_usd=$6, raw=$7::jsonb, error=$8
            WHERE id=$1
            """,
            row_id,
            "filled" if result["ok"] and filled > 0 else "unfilled",
            result.get("order_id"), filled, fill_price,
            round(filled * (fill_price or 0), 2),
            json.dumps(result.get("raw"), default=str),
            None if result["ok"] else str(result.get("raw"))[:300])
        log.info("MANUAL slug order %s: %.0f shares @ %.2f (%s)",
                 "FILLED" if filled > 0 else "unfilled", filled,
                 fill_price or limit, us_slug)
        return {"ok": bool(result["ok"] and filled > 0), "row_id": row_id,
                "filled_shares": filled, "fill_price": fill_price,
                "limit_price": limit, "quoted_ask": ask,
                "us_market_slug": us_slug,
                "title": note[:120] or us_slug, "outcome": None,
                "error": (None if result["ok"] and filled > 0 else
                          "order did not fill at the protected limit")}
    except Exception as exc:  # noqa: BLE001 — the desk reports, never crashes
        log.exception("manual slug order failed (row %s)", row_id)
        await pool.execute(
            "UPDATE live_orders SET status='error', error=$2 WHERE id=$1",
            row_id, str(exc)[:300])
        return {"ok": False, "row_id": row_id,
                "error": f"{type(exc).__name__}: {str(exc)[:160]}"}


# Venue states that mean the manual GTC row is still working the book.
_MANUAL_OPEN_STATES = frozenset({"new", "pending_new", "partially_filled",
                                 "pending_replace", "pending_risk", "open"})


async def _manual_day_spent(pool) -> float:
    """The 24h manual spend INCLUDING open commitments (owner order
    2026-08-28): a resting GTC is money promised to the book — counting
    only fills would let $1000 of resting orders ride beside a full
    day's fills. Strictly tighter than the old fills-only sum."""
    return float(await pool.fetchval(
        """
        SELECT COALESCE(sum(CASE WHEN status IN ('submitting', 'open')
                                 THEN GREATEST(COALESCE(filled_usd, 0),
                                               COALESCE(requested_usd, 0))
                                 ELSE COALESCE(filled_usd, 0) END), 0)
        FROM live_orders
        WHERE whale_username = 'manual'
          AND placed_at > now() - interval '24 hours'
        """) or 0)


async def execute_manual_limit(usd: float, limit_price: float,
                               asset: str = "", us_slug: str = "",
                               note: str = "") -> dict:
    """Place an admin-directed RESTING BUY: a GTC limit at the desk
    user's own price (owner order 2026-08-28, venue parity — the desk
    ticket works like the venue's own limit ticket). Same gates as
    every manual order: per-order cap, kill switch, 24h budget (which
    counts this order's commitment the moment it rests), venue-named
    side or refusal. Returns a UI-ready dict, never raises."""
    try:
        return await _execute_manual_limit(usd, limit_price, asset,
                                           us_slug, note)
    except Exception as exc:  # noqa: BLE001 — the desk reports, never 500s
        log.exception("manual limit order failed pre-flight")
        return {"ok": False,
                "error": f"{type(exc).__name__}: {str(exc)[:160]}"}


async def _execute_manual_limit(usd: float, limit_price: float,
                                asset: str, us_slug: str,
                                note: str) -> dict:
    from . import pmus

    venue = active_venue()
    if venue != "polymarket-us":
        return {"ok": False, "error": "live venue not armed"}
    if not (0 < usd <= MANUAL_MAX_PER_ORDER_USD):
        return {"ok": False,
                "error": f"size must be $0-{MANUAL_MAX_PER_ORDER_USD:.0f}"}
    try:
        limit = round(float(limit_price), 2)
    except (TypeError, ValueError):
        return {"ok": False, "error": "limit price must be a number"}
    if not (0.01 <= limit <= 0.99):
        return {"ok": False, "error": "limit must be $0.01-$0.99"}
    pool = await get_pool()
    if await _is_paused(pool):
        return {"ok": False, "error": "live trading paused (kill switch)"}
    day_spent = await _manual_day_spent(pool)
    if day_spent + usd > MANUAL_DAILY_USD:
        return {"ok": False,
                "error": (f"manual day budget exhausted "
                          f"(${day_spent:.2f} of ${MANUAL_DAILY_USD:.0f} "
                          "in 24h, open orders included)")}
    shares = int(usd / limit)
    if shares < 1:
        return {"ok": False, "error": "budget buys zero whole contracts"}
    intent = None
    title = note[:120] or us_slug
    if us_slug and not asset:
        row_asset = f"slug:{us_slug}"[:120]
        slug = us_slug
    else:
        ctx = await _market_context(pool, {"asset": str(asset)})
        if not ctx.get("outcome"):
            return {"ok": False, "error": "unknown asset — pick from search"}
        mapping = await asyncio.to_thread(
            pmus.resolve_market_exact,
            _us_slug_candidates(ctx.get("market_slug")
                                or ctx.get("event_slug") or "",
                                ctx.get("outcome") or ""),
            ctx.get("outcome"))
        if mapping is None:
            return {"ok": False,
                    "error": "no US market maps exactly to this outcome"}
        row_asset, slug = str(asset), mapping["market_slug"]
        intent = mapping.get("intent")
        title = ctx.get("market_title") or title
    await _reap_stale_submitting(pool)
    row_id = await pool.fetchval(
        """
        INSERT INTO live_orders (trade_id, whale_username, asset,
                                 condition_id, side, his_price,
                                 limit_price, requested_usd,
                                 requested_shares, status,
                                 venue, us_market_slug)
        VALUES (NULL, 'manual', $1, NULL, 'BUY', $2, $2, $3, $4,
                'submitting', $5, $6)
        RETURNING id
        """,
        row_asset, limit, round(shares * limit, 2), float(shares),
        venue, slug)
    try:
        # submit_fok's whole safety stack rides along: the venue-named
        # side (or ambiguous_side refusal), the preview cost agreement,
        # and execution accounting — only the time-in-force differs.
        result = await asyncio.to_thread(
            pmus.submit_fok, slug, limit, shares, False,
            "TIME_IN_FORCE_GOOD_TILL_CANCEL", intent)
        filled = float(result.get("filled_shares") or 0)
        fill_price = (float(result["fill_price"])
                      if result.get("fill_price") else None)
        state = str(result.get("status") or "")
        order_id = result.get("order_id")
        if filled >= shares:
            status = "filled"
        elif order_id and state in _MANUAL_OPEN_STATES:
            status = "open"          # resting on the venue's book
        elif filled > 0:
            status = "filled"        # partial then terminal
        else:
            status = "rejected"
        await pool.execute(
            """
            UPDATE live_orders
            SET status=$2, order_id=$3, filled_shares=$4, fill_price=$5,
                filled_usd=$6, raw=$7::jsonb, error=$8
            WHERE id=$1
            """,
            row_id, status, order_id, filled, fill_price,
            round(filled * (fill_price or 0), 2),
            json.dumps(result.get("raw"), default=str),
            None if status != "rejected" else
            f"venue refused: {state or 'unknown'}")
        log.info("MANUAL LIMIT %s: %s %.0f @ %.2f (filled %.0f) %s",
                 status.upper(), slug, float(shares), limit, filled,
                 note[:60] or "")
        return {"ok": status in ("open", "filled"), "row_id": row_id,
                "status": status, "order_id": order_id,
                "filled_shares": filled, "fill_price": fill_price,
                "limit_price": limit, "us_market_slug": slug,
                "title": title,
                "error": (None if status in ("open", "filled") else
                          f"venue refused the order ({state or 'unknown'})")}
    except Exception as exc:  # noqa: BLE001 — the desk reports, never crashes
        log.exception("manual limit order failed (row %s)", row_id)
        await pool.execute(
            "UPDATE live_orders SET status='error', error=$2 WHERE id=$1",
            row_id, str(exc)[:300])
        return {"ok": False, "row_id": row_id,
                "error": f"{type(exc).__name__}: {str(exc)[:160]}"}


async def sync_open_manual_orders(pool=None) -> list[dict]:
    """Reconcile every 'open' manual row against the venue's own order
    record — called lazily when the desk reads its blotter or open-
    orders list, so a resting order that filled while nobody watched
    becomes a 'filled' audit row the next time anyone looks."""
    from . import pmus

    if pool is None:
        pool = await get_pool()
    rows = await pool.fetch(
        "SELECT id, order_id, us_market_slug, requested_shares "
        "FROM live_orders WHERE whale_username = 'manual' "
        "AND status = 'open' AND venue = 'polymarket-us' "
        "ORDER BY placed_at LIMIT 20")
    out = []
    for r in rows:
        if not r["order_id"]:
            continue
        try:
            st = await asyncio.to_thread(pmus.order_status, r["order_id"])
        except Exception:  # noqa: BLE001 — sync is best-effort, retry next read
            continue
        if st is None:
            continue
        filled = float(st.get("filled_shares") or 0)
        avg = st.get("avg_px")
        state = st.get("state")
        if state in _MANUAL_OPEN_STATES:
            new_status = "open"
        elif filled > 0:
            new_status = "filled"
        else:
            new_status = "cancelled"
        await pool.execute(
            """
            UPDATE live_orders
            SET status=$2, filled_shares=$3, fill_price=$4, filled_usd=$5
            WHERE id=$1 AND status = 'open'
            """,
            r["id"], new_status, filled, avg,
            round(filled * (avg or 0), 2))
        out.append({"row_id": r["id"], "status": new_status,
                    "filled_shares": filled})
    return out


async def cancel_manual_open(row_id: int) -> dict:
    """Cancel one resting manual order at the venue, then record what
    the venue says actually happened — a cancel that raced a fill
    records the fill, never erases it."""
    from . import pmus

    pool = await get_pool()
    r = await pool.fetchrow(
        "SELECT id, order_id, us_market_slug FROM live_orders "
        "WHERE id = $1 AND whale_username = 'manual' AND status = 'open'",
        row_id)
    if r is None:
        return {"ok": False, "error": "no open resting order with that id"}
    if not r["order_id"]:
        await pool.execute(
            "UPDATE live_orders SET status='cancelled' WHERE id=$1", row_id)
        return {"ok": True, "cancelled": True}
    res = await asyncio.to_thread(pmus.cancel_order, r["order_id"],
                                  r["us_market_slug"] or "")
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error") or "cancel refused"}
    # venue truth after the cancel: a raced fill stays a fill
    try:
        st = await asyncio.to_thread(pmus.order_status, r["order_id"])
    except Exception:  # noqa: BLE001
        st = None
    filled = float((st or {}).get("filled_shares") or 0)
    avg = (st or {}).get("avg_px")
    await pool.execute(
        """
        UPDATE live_orders
        SET status=$2, filled_shares=$3, fill_price=$4, filled_usd=$5
        WHERE id=$1
        """,
        row_id, "filled" if filled > 0 else "cancelled", filled, avg,
        round(filled * (avg or 0), 2))
    return {"ok": True, "cancelled": True, "filled_shares": filled}


def sell_limit_price(bid: float, min_price: float | None = None) -> float:
    """Protective cash-out limit (owner directive 2026-08-22): the live
    best bid minus 2c of book-motion protection, floored at the venue's
    $0.01 tick — and never below the caller's own min_price. Pure;
    tested. The IOC can only fill AT OR ABOVE this limit, so the floor
    is the worst realizable price, not a hope."""
    limit = max(0.01, round(bid - 0.02, 2))
    if min_price is not None and min_price > 0:
        limit = max(limit, round(float(min_price), 2))
    return round(min(limit, 0.99), 2)


# PAIR COMPLETION (owner order 2026-08-26: "I need you to touch it to
# get the system functioning correctly").
#
# WHAT THE GUARD GOT WRONG. one-position-per-game refused every later
# market on a game, with the comment "Kwon guaranteed-loss shape -- RN1
# completes pairs, we must not". That is backwards about RN1. Completing
# a pair is a guaranteed LOSS or a guaranteed PROFIT, and which one
# depends entirely on whether the legs sum above or below a dollar:
#
#   YES + NO redeems for exactly $1 whatever the outcome. A pair bought
#   for p + q is therefore worth $1 at resolution. Profit = 1 - p - q,
#   with no directional exposure left at all.
#
# Measured on the roster's own books (merge_pnl realised per merged
# share, production probe 2026-08-26):
#
#   0x076daa87  pairs sum to  92.3c   +7.73c locked per pair
#   ferrari     pairs sum to  98.2c   +1.81c
#   rn1         pairs sum to  99.0c   +1.02c   x 95,474 completions
#   homerun     pairs sum to 100.4c   -0.37c
#   swisstony   pairs sum to 101.7c   -1.71c
#
# The three whose pairs sum BELOW a dollar are exactly the three with
# positive merge-graded ROI. The blanket refusal treated all five alike,
# so we declined a risk-free spread because it wore the same shape as a
# risk-free loss.
#
# THIS IS NOT A LOOSENING. Every clause below is a NEW test that must
# PASS before a refusal is lifted, and any one failing refuses exactly
# as before:
#   1. the market must be the VENUE-CONFIRMED complement of a leg we
#      currently hold -- never a fuzzy or inferred sibling;
#   2. our own cost plus the complement's live ask must clear a dollar
#      by PAIR_MIN_EDGE_CENTS, so the profit is proved BEFORE the order
#      exists rather than hoped for after;
#   3. the size is capped at the shares we already hold, so it can only
#      ever complete a pair and never open net exposure.
# Per-order caps, daily caps, no-stack, the kill switch and the
# overspend breaker all still apply on top. This decides one refusal,
# not the sizing and not the money gates.
PAIR_MIN_EDGE_CENTS = float(os.environ.get("PMUS_PAIR_MIN_EDGE_CENTS", "1"))


def pair_completion_edge(our_cost: float | None,
                         complement_ask: float | None) -> float | None:
    """Cents locked per pair by buying the complement, or None.

    Returns the EDGE rather than a boolean deliberately: a caller that
    only sees yes/no cannot log what it declined, and the near-misses
    are the only way to learn whether the threshold is set right.
    """
    try:
        p, q = float(our_cost), float(complement_ask)
    except (TypeError, ValueError):
        return None
    if not (0 < p < 1) or not (0 < q < 1):
        return None
    return round((1.0 - p - q) * 100.0, 3)


def pair_completion_allowed(our_cost: float | None,
                            complement_ask: float | None) -> bool:
    """True only when the completion is a PROVEN profit.

    Fails closed on an unreadable price: not knowing what the complement
    costs is not evidence that the pair is cheap.
    """
    edge = pair_completion_edge(our_cost, complement_ask)
    return edge is not None and edge >= PAIR_MIN_EDGE_CENTS


async def _pair_completion_context(pool, us_slug: str,
                                   held_slugs: list[str],
                                   order_limit: float | None = None,
                                   ) -> dict | None:
    """Is `us_slug` the complement of something we hold, and at what edge?

    None means "refuse, exactly as before", and it is returned whenever
    anything at all is unknown. The venue's own positions payload is the
    referee for both what we hold and what it cost, the same referee
    no-stack already uses on the buy side.

    TWO DEFECTS FIXED HOURS AFTER V1 SHIPPED (adversarial review
    2026-08-26), both of which made the first version wrong in the same
    direction -- it looked like a working gate and was not:

      * WRONG KEY SPACE. v1 asked _sibling_from_positions, whose map
        (token_siblings) is keyed CTF-token-id -> CTF-token-id from the
        whale's GLOBAL positions. A PMUS market slug is never a key
        there, so the lookup returned '' on every call and the
        carve-out never fired once -- inert while its tests passed.
        The complement now comes from pmus.slug_complement, the venue's
        own marketSides array, in the same key space as held_slugs.
      * PROVED AT THE WRONG PRICE. v1 proved the edge at the live ask,
        but the order transacts at up to `limit` -- the whale-derived
        price plus the Option A tolerance. A FOK can fill anywhere up
        to its limit, so a pair proved at ask can cost more than a
        dollar by fill time. The edge is now proved at the WORST price
        the order can pay: max(ask, order_limit). A tightening -- it
        can only refuse pairs v1 would have admitted.
    """
    from . import pmus

    try:
        sib = await asyncio.to_thread(pmus.slug_complement, us_slug)
    except Exception:  # noqa: BLE001 -- an unreadable venue is not a sibling
        return None
    if not sib or sib not in set(held_slugs):
        return None
    try:
        held, avg = await _pm_held(sib)
    except Exception:  # noqa: BLE001
        return None
    if held < 1 or avg is None:
        return None
    try:
        ask = await asyncio.to_thread(pmus.slug_ask, us_slug)
    except Exception:  # noqa: BLE001
        return None
    if ask is None:
        return None
    worst = max(float(ask), float(order_limit)) \
        if order_limit is not None else float(ask)
    edge = pair_completion_edge(avg, worst)
    if edge is None:
        return None
    return {"sibling": sib, "held": int(held), "our_cost": float(avg),
            "ask": float(ask), "worst_price": worst, "edge_cents": edge,
            "allowed": edge >= PAIR_MIN_EDGE_CENTS}


async def _pm_held(us_slug: str) -> tuple[int, float | None]:
    """(held whole contracts, avg cost) for one US market, from the
    venue's OWN positions payload — the account is the referee for what
    can be sold, exactly as it is for no-stack on the buy side.

    SIGNED, AND THE SIGN IS THE POSITION (2026-08-25). netPosition is
    signed on this venue: a short is NEGATIVE. `qty <= 0` therefore read
    every short we hold as "nothing here", which made a short position
    unsellable by mirror_exit and invisible to the no-stack referee —
    a pricing bug converted into permanently stranded inventory.

    The MAGNITUDE is what can be closed, in either direction. `expired`
    still returns nothing, because an expired market cannot be traded
    whatever the sign says.
    """
    from .api.pmus_account import _amt, _fetch_all_positions_sync

    positions = await asyncio.wait_for(
        asyncio.to_thread(_fetch_all_positions_sync), timeout=30)
    p = (positions or {}).get(us_slug) or {}
    qty = abs(_amt(p.get("netPosition")))
    if qty <= 0 or p.get("expired"):
        return 0, None
    cost = abs(_amt(p.get("cost")))
    return int(qty), (round(cost / qty, 4) if cost > 0 else None)


async def _pm_long_leg(us_slug: str) -> bool | None:
    """True if we are LONG this slug, False if SHORT, None unreadable.

    WHY THIS EXISTS SEPARATELY FROM _pm_held (2026-08-31). _pm_held
    takes abs(netPosition) — deliberately, because the MAGNITUDE is
    what can be closed in either direction. That makes `held >= 1`
    say nothing whatsoever about the side, and slug_bid now needs the
    side: on this venue's two-sided markets both legs share ONE
    identifier, so nothing else can select one.

    I shipped `slug_bid(us_slug, True)` on the desk path reasoning
    that a short reads negative so a positive held must be long. It
    does not — the abs() above erases exactly that. Pricing a SHORT
    off the long bid is the giveaway direction, not the safe one: with
    the long book at 0.05/0.06 the short leg is worth 0.94, and a
    bestBid floor would sell it for 0.05.

    None on anything unreadable, and the caller refuses rather than
    assuming a side."""
    from .api.pmus_account import _amt, _fetch_all_positions_sync

    try:
        positions = await asyncio.wait_for(
            asyncio.to_thread(_fetch_all_positions_sync), timeout=30)
    except Exception:  # noqa: BLE001 — unreadable is not "long"
        return None
    p = (positions or {}).get(us_slug) or {}
    net = _amt(p.get("netPosition"))
    if not net:
        return None
    return net > 0


# WHAT A DESK CASH-OUT ROW SAYS WHEN ITS REALIZED VALUE IS UNKNOWN.
#
# Written into the row's own `error` column — the same column, and the
# same purpose, as `_reap_stale_exiting`'s "fill price and pnl on this
# row were never captured". It states what the row SUBTRACTED, which is
# a fact ($0.00 — a NULL is skipped by `sum()`), and never what the
# sale was worth, which nothing in this process knows.
#
# THE FIRST SENTENCE CARRIES THE MEANING ON PURPOSE, because every
# reader of this column truncates it: the desk blotter renders
# `t.error.slice(0, 80)` and the status cell `slice(0, 40)`
# (TradeDesk.tsx:2205, :2237), the day-detail admin read takes
# `left(COALESCE(error,''), 200)` (app.py:4045), and migration 037's
# NOTIFY trigger publishes `left(NEW.error, 120)`
# (037_live_orders_notify.sql:24). At 120 characters the text still
# carries "REALIZED NOT CAPTURED" and "$0.00"; at 80 it still carries
# "this row subtr[acts]". That is the pin below.
#
# WHO ACTUALLY READS IT TODAY — checked at the consumers, not assumed
# from the payload (adversarial review, 2026-09-04, corrected on a
# second pass that found a surface the first one missed). TWO surfaces:
#
#   the desk BLOTTER   `t.error.slice(0, 80)` and the status cell's
#                      `slice(0, 40)` (TradeDesk.tsx:2205, :2237).
#   the AI TRADER page `/api/live-status`'s `recent` selects `lo.error`
#                      with NO whale filter (app.py:3892-3905, `WHERE
#                      lo.placed_at >= $1` only), so a `manual` desk row
#                      is in that list, and AITrader.tsx:315-319 renders
#                      a truthy `o.error` as a chip in `var(--critical)`.
#
# That second one is a NEW STATE on that page: a row whose status reads
# `cashed_out` — a sale that SUCCEEDED — now carries a red chip. It is
# not a mislabel. At 40 characters the chip reads "REALIZED NOT CAPTURED
# — this sale's P&L" and the hazard is real, so red is the right colour
# and surfacing it there is the point. It is recorded here because the
# earlier version of this comment claimed the blotter was "the whole of
# the disclosure's reach", and that was false.
#
# The order stream does NOT read it, even though the NOTIFY
# payload carries the text: DeskOrderStream.tsx's `cashed_out` branch
# builds its body from `e.pnl` alone and never touches `e.error`, so a
# NULL pnl prints "Position closed" with no figure and no hazard. Wiring
# that branch (and the desk's run panel, which reads neither `detail`
# nor `pnl_unmeasured` on a successful sale) is U6's work in frontend
# files no unit in this run owns — do not claim the stream.
#
# A third reader is Jarvis: jarvis/tools.ts:921 forwards the blotter's
# `t.error` into the assistant's view of `/api/admin/manual-trades`, so
# the sentence has to read as prose to a model as well as to a person.
#
# NOTHING BUCKETS OR RETRIES ON IT. The one query that pattern-matches
# `lo.error` into reason classes — the unmapped-flow census, app.py
# :7573-7605, `LIKE 'no-stack%'`, `'%0ev%'`, `'%sides:[%'` — is scoped
# `WHERE lo.status = 'rejected'` (app.py:7658), which a `cashed_out` row
# never satisfies, and the text matches none of those patterns anyway.
# No query filters on `error IS NOT NULL`; no worker retries or alerts
# on it. Every consumer found RENDERS it.
#
# WHY THE SECOND SENTENCE HEDGES THE COST. `_pm_held` returns no
# average for two different reasons — the payload carried no cost, and
# the cost was not positive (`:5050-5051`, `cost > 0`) — and a
# genuine zero-cost position is the second, not the first. The row may
# not claim the venue was silent when it was merely non-positive. What
# is true under both is that there was no usable entry price, and that
# what this row subtracted is $0.00.
CASHOUT_PNL_UNMEASURED_TEXT = (
    "REALIZED NOT CAPTURED — this sale's P&L is UNKNOWN, not zero, and "
    "this row subtracts $0.00 from its market's settlement target. "
    "The venue's positions payload gave no usable average cost for this "
    "market at sale time (none carried, or not positive), so there was "
    "no entry price to realize against and pnl was written NULL rather "
    "than invented. The per-slug sum that nets cash-out dollars out of "
    "the venue's cumulative realized skips NULLs, so this sale's "
    "proceeds remain inside the figure the entry rows are graded "
    "against. Attribute this row nothing: what it subtracted is $0.00."
)


async def execute_manual_sell(us_slug: str, qty: int | None = None,
                              min_price: float | None = None) -> dict:
    """Platform-side cash-out of a held Polymarket US position (owner
    directive 2026-08-22): sell up to the HELD quantity at a protective
    limit under the live bid, IOC — takes what rests, cancels the rest.
    Every refusal is a named reason, never an exception (same desk
    contract as execute_manual). Fails closed: refuses more than held,
    refuses with no live bid, limit floored at $0.01."""
    try:
        return await _execute_manual_sell(us_slug, qty, min_price)
    except Exception as exc:  # noqa: BLE001 — the desk reports, never 500s
        log.exception("manual sell failed pre-flight")
        return {"ok": False,
                "error": f"{type(exc).__name__}: {str(exc)[:160]}"}


async def _execute_manual_sell(us_slug: str, qty: int | None,
                               min_price: float | None) -> dict:
    from . import pmus

    venue = active_venue()
    if venue != "polymarket-us":
        return {"ok": False, "error": "live venue not armed"}
    us_slug = (us_slug or "").strip()
    if not us_slug:
        return {"ok": False, "error": "pick a market"}
    held, avg_cost = await _pm_held(us_slug)
    if held < 1:
        return {"ok": False,
                "error": "nothing held on this market — nothing to sell"}
    if qty is None:
        qty = held
    qty = int(qty)
    if qty < 1:
        return {"ok": False, "error": "qty must be a positive contract count"}
    if qty > held:
        return {"ok": False,
                "error": f"qty {qty} exceeds held {held} — selling more "
                         "than the position is refused"}
    # READ the leg; do not infer it from `held`. _pm_held returns the
    # MAGNITUDE, so `held >= 1` is true of a short too.
    _leg = await _pm_long_leg(us_slug)
    if _leg is None:
        return {"ok": False,
                "error": "cannot read which side of this market is held "
                         "— refusing rather than pricing the wrong leg"}
    bid = await asyncio.to_thread(pmus.slug_bid, us_slug, _leg)
    if bid is None or not (0 < bid < 1):
        return {"ok": False, "error": "no live bid for this market"}
    limit = sell_limit_price(bid, min_price)
    pool = await get_pool()
    result = await asyncio.to_thread(
        pmus.submit_fok, us_slug, limit, qty, True,
        "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL")
    filled = float(result["filled_shares"]) if result["ok"] else 0.0
    fill_price = float(result["fill_price"]) if result["ok"] else None
    proceeds = round(filled * (fill_price or 0), 2)
    # Same helper as every other exit. The desk's positions are long
    # today — it has no short entry path — so `intent=None` reproduces
    # today's arithmetic exactly. It routes through realized_pnl anyway
    # so that ONE function owns this decision: the way this bug reached
    # production was four places encoding it and one of them converted.
    pnl = realized_pnl(avg_cost, fill_price, filled, None)
    status = "cashed_out" if result["ok"] and filled > 0 else "unfilled"
    # A NULL REALIZED VALUE IS A HAZARD, AND THE ROW NOW SAYS SO
    # (2026-09-04).
    #
    # `_pm_held` returns `(qty, None)` whenever the venue's positions
    # payload gives no usable average cost — none carried, or not
    # positive (its `cost > 0` guard covers both) — and `realized_pnl`
    # correctly refuses to turn an unknown entry price into a zero, so
    # this INSERT can write a `cashed_out` row with `pnl NULL`.
    #
    # That NULL is not inert. The settlement sweep nets cash-out
    # dollars out of the venue's cumulative realized with
    # `COALESCE(sum(pnl), 0)` per slug before allocating what is left
    # to the entry rows (`analytics/engine.py`, `sold_by`), and SQL
    # `sum()` SKIPS NULLs. So this row subtracts $0.00 while the sale's
    # proceeds stay inside the figure the entry rows are graded
    # against: the money is already booked once, on someone else's row.
    #
    # We cannot recover the number here — the venue told us no cost,
    # and inventing one (a zero, the limit, the bid) would be a
    # fabricated measurement on the money path. What we CAN do is stop
    # the row from being silent about it: the disclosure below is
    # written into the row's own `error` column, exactly as
    # `_reap_stale_exiting` names its own uncaptured price, so that the
    # census, any future attribution, and the desk blotter all read the
    # same sentence off the same row. It states what this row
    # SUBTRACTED ($0.00) — never what the sale was worth, which nobody
    # here knows.
    #
    # This changes no stored figure and no served number: `pnl` is NULL
    # before and after, `sold_by` subtracts 0.00 before and after. It
    # is a guard on future writes only. Rows already written carry a
    # NULL `error` and are U0's census population, not this unit's.
    pnl_unmeasured = bool(status == "cashed_out" and pnl is None)
    if result["ok"]:
        err_text = CASHOUT_PNL_UNMEASURED_TEXT if pnl_unmeasured else None
    else:
        err_text = str(result.get("raw"))[:300]
    # The sale is recorded on the manual sleeve as its own terminal row
    # (the underdog sweep's 'cashed_out' contract): filled_usd carries
    # the PROCEEDS, pnl the realized gain vs the venue's own avg cost
    # where known. Terminal on insert — the in-flight index and the
    # settlement sweep both ignore it by construction.
    row_id = await pool.fetchval(
        """
        INSERT INTO live_orders (trade_id, whale_username, asset,
                                 condition_id, side, his_price,
                                 limit_price, requested_usd,
                                 requested_shares, status, venue,
                                 us_market_slug, order_id, filled_shares,
                                 fill_price, filled_usd, raw, error, pnl,
                                 settled_at)
        VALUES (NULL, 'manual', $1, NULL, 'SELL', $2, $3, $4, $5, $6,
                $7, $8, $9, $10, $11, $12, $13::jsonb, $14, $15,
                CASE WHEN $6 = 'cashed_out' THEN now() END)
        RETURNING id
        """,
        f"sell:{us_slug}"[:120], bid, limit, round(qty * limit, 2),
        float(qty), status, venue, us_slug, result.get("order_id"),
        filled, fill_price, proceeds,
        json.dumps(result.get("raw"), default=str),
        err_text, pnl)
    log.info("MANUAL SELL %s: %.0f/%d @ %.2f (bid %.2f) proceeds %.2f%s",
             status, filled, qty, fill_price or limit, bid, proceeds,
             " — REALIZED NOT CAPTURED (no venue avg cost); this row "
             "subtracts $0.00 from the slug's settlement target"
             if pnl_unmeasured else "")
    if filled <= 0:
        detail = ("no fill at the protective limit — the bid moved; "
                  "nothing was sold")
    elif pnl_unmeasured:
        detail = ("sold — but the venue gave no average cost for this "
                  "position, so this sale's realized P&L was never "
                  "captured and is recorded as unknown, not zero")
    else:
        detail = "sold"
    return {"ok": bool(result["ok"] and filled > 0), "row_id": row_id,
            "filled_shares": filled, "avg_price": fill_price,
            "proceeds_usd": proceeds, "quoted_bid": bid,
            "limit_price": limit, "pnl": pnl, "held": held,
            # THE RESPONSE CARRIES IT AT THE MOMENT OF THE SALE — but
            # nothing on the desk reads it yet, and this comment used
            # to say the operator was told, which was false. Checked:
            # TradeDesk.tsx:845 takes the SUCCESS branch on a filled
            # sale and reads neither `detail` nor `pnl_unmeasured`;
            # `detail` is read only in the failure branch at :855. The
            # operator learns of the hazard on the next loadBlotter(),
            # off the row's `error`. This flag exists so the run panel
            # can say it at the moment of the sale when U6 wires it —
            # `pnl: None` alone reads as "not computed yet", and this
            # says it will never be computed.
            "pnl_unmeasured": pnl_unmeasured,
            "detail": detail}


# Strong refs for fire-and-forget echo tasks (a bare create_task can be
# garbage-collected mid-flight).
_ECHO_TASKS: set = set()


async def _independent_check(us_slug: str, outcome: str | None,
                             his_title: str | None,
                             his_slug: str | None,
                             intent: str | None,
                             mapping_src: str | None = None) -> tuple[str, str]:
    """Re-derive the mapping from the WHALE'S OWN SIGNAL through a
    DIFFERENT resolver, and require it to agree with what we bought.

    (Leak-hunt round 2: the echo re-derived inside the market the
    suspect premap row itself named, so an internally consistent but
    wrong row self-certified. This path never reads us_premap — it
    starts from the whale's title, builds candidate slugs the way the
    deterministic desk resolver does, and asks the venue. Two
    independent resolvers agreeing is evidence; one resolver agreeing
    with itself is not.)"""
    from . import pmus

    # NOT INDEPENDENT IF IT IS THE SAME RESOLVER (certification audit
    # 2026-08-24): this cross-check calls pmus.resolve_market_exact. For
    # a mapping the EXACT resolver produced, that is a bit-for-bit
    # replay — it agrees with itself by construction and certifies
    # nothing. Only a mapping from a DIFFERENT resolver can be checked
    # this way; anything else is honestly unverified.
    # "yesno_exact" (2026-08-30): the independent matcher maps picks
    # onto NAMED-side instruments and, by the pinned 2026-08-24
    # literal rule, structurally refuses team->Yes; on a league that
    # ALSO lists an aec- sibling it would return a different-FAMILY
    # identifier for the SAME proposition, and the identifier-equality
    # compare would report a false mismatch — which, live, re-arms the
    # total quarantine off a correct order. The class keeps three real
    # verifiers: the position-sign check on every real fill, the
    # premap-refetch echo (yes/no markets yield 2 rows, so
    # _side_echo_verify's len==1 subject branch cannot false-trip;
    # match_side abstains as 'no unique live match', never mismatch),
    # and its own shadow counter.
    if mapping_src in ("exact", "desk_exact", "desk_exact_side",
                       "derivative_exact", "spread_exact",
                       "yesno_exact"):
        return ("unverified",
                "cross-check would replay the resolver that produced "
                f"this mapping (src={mapping_src})")

    cands = (_tennis_candidates(his_title, his_slug or "")
             + _us_slug_candidates(his_slug or "", outcome or ""))
    cands = [c for c in cands if c]
    if not cands:
        return "unverified", "no independent candidates"
    try:
        alt = await asyncio.to_thread(
            pmus.resolve_market_exact, cands, outcome)
    except Exception as exc:  # noqa: BLE001
        return "unverified", f"independent resolver error: {exc}"[:160]
    if alt is None:
        return "unverified", "independent resolver found nothing"
    if str(alt.get("market_slug", "")).lower() != us_slug.lower():
        return "mismatch", (f"independent resolver chose "
                            f"{alt.get('market_slug')}")
    if intent and alt.get("intent") and alt["intent"] != intent:
        return "mismatch", (f"independent resolver would order "
                            f"{alt['intent']}, we sent {intent}")
    return "ok", "independent resolver agrees"


async def _side_echo_verify(pool, row_id: int, us_slug: str,
                            outcome: str | None, his_title: str | None,
                            attempts: int = 3, shadow: bool = False,
                            his_slug: str | None = None,
                            intent: str | None = None,
                            mapping_src: str | None = None,
                            state_key: str | None = None) -> str:
    """POST-FILL SIDE ECHO (owner order 2026-08-24: "verify that we
    never ever take the wrong position ever again"): seconds after a
    copy fills, re-derive the mapping from the venue's LIVE event board
    through the same precision matcher (premap.match_side) and require
    it to land on the exact identifier we bought. A CONFIRMED
    divergence — the matcher choosing a DIFFERENT identifier on live
    venue data — re-arms the total quarantine and drops premap-live by
    itself, so a corrupt or stale premap row gets ONE order of
    exposure, never a day. Zero latency on the order path (runs after
    the fill). Fetch failures and no-unique-match are counted and
    surfaced (side_echo_last state key), never treated as divergence —
    a venue outage must not be able to trip the breaker on its own."""
    from . import pmus as pmus_mod
    from .workers import premap as _premap

    verdict, detail = "unverified", ""
    try:
        prow = await pool.fetchrow(
            "SELECT event_slug, market_slug FROM us_premap "
            "WHERE identifier=$1", us_slug.lower())
        if not prow:
            detail = "identifier not in us_premap"
        else:
            # RAW venue rows via DIRECT market lookup (leak-hunt find
            # 2026-08-24: desk-shaped event_board rows made the
            # tripwire inert; PREMAP-GT proved list-based refetch
            # compares against a GENERIC page). live_rows_for_market
            # raises on failure, so the retries here are real.
            parent = prow["market_slug"] or us_slug.lower()
            rows = None
            for _ in range(attempts):
                try:
                    rows = await asyncio.to_thread(
                        _premap.live_rows_for_market, parent)
                    break
                except Exception:  # noqa: BLE001 — retry, then count
                    await asyncio.sleep(2)
            if rows is None:
                detail = "venue unreachable"
            elif not rows:
                detail = "event has no live rows"
            elif (len(rows) == 1
                  and str(rows[0].get("identifier", "")).lower()
                  == us_slug.lower()):
                # SINGLE-SIDE CONTRACT (leak-hunt round 2, 2026-08-24):
                # for a per-side contract row the parent IS the side, so
                # the refetch returns exactly one row — itself — and
                # match_side could only ever say ok or no-unique-match:
                # 'mismatch' was structurally unreachable and the
                # tripwire was inert for the whole row class. The real
                # question here is not WHICH of several sides, it is
                # whether this contract's own subject still IS the
                # whale's pick.
                subject = _premap._norm(rows[0].get("side_norm"))
                want = _premap._norm(outcome)
                if not subject or not want:
                    detail = "contract subject unreadable"
                elif subject == want:
                    verdict = "ok"
                elif want in subject or subject in want:
                    # CONTAINMENT IS NOT IDENTITY (certification audit
                    # 2026-08-24): 'Ito' is contained in 'Mai Ito' and
                    # in 'Aoi Ito', so containment certified a WRONG
                    # PLAYER as ok. A partial match is not evidence —
                    # it is the absence of evidence, and must read as
                    # unverified rather than as confirmation.
                    detail = (f"contract subject {subject!r} only "
                              f"partially matches {want!r} — not proof")
                else:
                    verdict = "mismatch"
                    detail = (f"contract subject {subject!r} is not the "
                              f"whale's pick {want!r}")
            else:
                # THE SAME INPUTS PRODUCTION USED. The echo exists to
                # re-derive the side independently; if it feeds the
                # matcher a different argument list than resolve did,
                # it is not verifying production, it is verifying
                # something else — the failure mode that has cost the
                # most here.
                hit = _premap.match_side(rows, outcome, his_title,
                                         his_slug)
                if hit is None:
                    detail = "no unique live match"
                elif str(hit["identifier"]).lower() != us_slug.lower():
                    verdict = "mismatch"
                    detail = f"live matcher chose {hit['identifier']}"
                elif intent and hit.get("intent") \
                        and hit["intent"] != intent:
                    # IDENTIFIER EQUALITY IS NOT SIDE EQUALITY: every
                    # two-sided market here shares one identifier
                    # between its sides, so comparing identifiers alone
                    # let a wrong-side order certify itself. The INTENT
                    # is the side.
                    verdict = "mismatch"
                    detail = (f"live matcher would order "
                              f"{hit['intent']}, we sent {intent}")
                else:
                    verdict = "ok"
    except Exception as exc:  # noqa: BLE001 — the echo never raises
        detail = f"echo error: {exc}"[:200]

    # THIRD CHECK — WHAT WE ACTUALLY HOLD. The venue selects a side by
    # intent, so the only end-to-end proof that BUY_SHORT bought the
    # short side is the resulting position's sign. Runs on real fills
    # only (a shadow row holds nothing).
    #
    # ATTRIBUTION GATE (incident 2026-08-28 11:08Z): position_side
    # returns the MARKET's net, and net sign is this order's side only
    # when this order is the market's only leg. On
    # aec-itfwo-joeste-amamon-2026-08-28 the account had sold 2,901
    # shares nine minutes earlier (whale-exit cash-out) and carried a
    # separate 1,586-share short leg, so a correct BUY_LONG fill read
    # net=-2869, tripped the circuit as "POSITION SIDE WRONG", and
    # halted all copying for a day on evidence that was the aftermath
    # of our own exit — the market settled flat. The sole mismatch in
    # 451 verdicts, and it was a confound. Sign evidence now requires:
    # (a) no OTHER filled live_orders leg on this market — exits,
    # shorts, manual desk sells all write rows — and (b) |net| no
    # larger than this order's own filled shares (a net the venue
    # attributes mostly to shares we did not just buy proves other
    # legs, including venue-app trades our DB never saw). When
    # attribution fails the check abstains: no verdict either way, no
    # short proof recorded — absence of valid evidence is not
    # evidence. A genuine wrong-side fill on a clean market still
    # trips exactly as before.
    if not shadow and intent:
        try:
            net = await asyncio.to_thread(pmus_mod.position_side, us_slug)
            if net is not None and net != 0:
                sole_leg = False
                my_sh = 0.0
                try:
                    # CURRENT EXPOSURE ONLY (fleet round 49, HIGH): the
                    # first cut matched any ever-filled row, so one
                    # terminal cashed_out/settled row disarmed this
                    # tripwire on that market for the life of the
                    # ledger — every re-entered market ran uncovered.
                    # A leg confounds the net only while it still HOLDS
                    # something; terminal rows hold nothing.
                    other = await pool.fetchval(
                        "SELECT 1 FROM live_orders "
                        "WHERE lower(us_market_slug) = lower($1) "
                        "AND id <> $2 "
                        "AND status IN ('submitting', 'open', 'filled') "
                        "AND (coalesce(filled_shares, 0) > 0 "
                        "     OR coalesce(filled_usd, 0) > 0 "
                        "     OR status = 'submitting') "
                        "LIMIT 1", us_slug, row_id)
                    mine = await pool.fetchval(
                        "SELECT filled_shares::float8 FROM live_orders "
                        "WHERE id = $1", row_id)
                    my_sh = float(mine or 0)
                    sole_leg = other is None and my_sh > 0
                except Exception:  # noqa: BLE001 — unknown attribution
                    sole_leg = False        # is not attribution
                # TWO-SIDED BOUND (fleet round 49): |net| must MATCH our
                # fill, not merely not exceed it. The one-sided form let
                # any off-book leg smaller than ~2x our fill flip or
                # shrink the net inside the bound — a hidden 150-sh
                # short against our 100-sh long read net=-50 as
                # "POSITION SIDE WRONG" and re-armed the total
                # quarantine falsely. A net that deviates from our own
                # fill in EITHER direction is evidence of legs we
                # cannot see, and abstention is the only honest read.
                if sole_leg and abs(abs(net) - my_sh) <= my_sh * 0.05 + 1:
                    want_long = intent == "ORDER_INTENT_BUY_LONG"
                    got_long = net > 0
                    if want_long != got_long:
                        verdict = "mismatch"
                        detail = (f"POSITION SIDE WRONG: sent {intent} "
                                  f"but hold netPosition={net}")
                    elif verdict == "unverified":
                        verdict, detail = "ok", f"position sign agrees ({net})"
                    # THE SHORT CLASS EARNS ITS OWN PROOF HERE. This is
                    # the only place the venue's position sign is read
                    # against the intent we sent, so it is the only
                    # place a short can be certified. Recorded on real
                    # fills only — a shadow row holds nothing to check.
                    if is_short_intent(intent):
                        await _record_short_proof(
                            pool, ok=(verdict != "mismatch"), net=net,
                            slug=us_slug)
                else:
                    detail = (f"{detail} | position-sign check "
                              f"abstained: net={net} not attributable "
                              f"to this order alone (other legs on "
                              f"market, or |net| > our "
                              f"{my_sh:g} filled shares)")[:300]
        except Exception as exc:  # noqa: BLE001 — never raises
            detail = f"{detail} | position check failed: {exc}"[:200]

    # SECOND, INDEPENDENT OPINION: a wrong row that is internally
    # consistent passes the check above, so the whale's own signal is
    # re-resolved through a different resolver. Disagreement is a
    # mismatch even when the first check said ok; agreement upgrades an
    # 'unverified' to verified.
    try:
        iv, idetail = await _independent_check(
            us_slug, outcome, his_title, his_slug, intent, mapping_src)
        if iv == "mismatch":
            verdict, detail = "mismatch", f"{detail} | {idetail}".strip(" |")
        elif iv == "ok" and verdict == "unverified":
            verdict, detail = "ok", idetail
        elif iv == "ok" and verdict == "ok":
            detail = f"{detail} + {idetail}".strip(" +")
    except Exception as exc:  # noqa: BLE001 — never raises
        detail = f"{detail} | independent check failed: {exc}"[:200]

    # SHADOW MODE (owner order 2026-08-24: prove it before dollars): the
    # same verification runs on QUARANTINED premap resolutions, where no
    # money rode. A shadow mismatch never trips the circuit — there is
    # nothing to halt — but it is counted separately and printed loudly,
    # and the resume lever must never be flipped while shadow_mismatch
    # is non-zero. This is what turns the refusal stream into an
    # independent certification instrument: the mapper's choice is
    # re-derived from LIVE venue data by the same precision matcher,
    # so the evidence is not the mapper grading its own homework.
    # A SEPARATE COUNTER FOR A SEPARATE POPULATION (2026-08-26).
    #
    # The owner reads SHADOW-CERT to decide whether a mapping class may
    # trade, and it decided tonight's `exact` resume at 691 ok / 0
    # mismatch. Certifying a NEW class into the same counter would
    # dilute the streak that justified the last decision with evidence
    # about a different question, and nobody reading the number
    # afterwards could separate them. Callers certifying a new class
    # pass their own key.
    state_key = state_key or ("side_echo_shadow" if shadow
                              else "side_echo_last")
    try:
        if verdict == "mismatch" and not shadow:
            # The un-overridable circuit FIRST (leak-hunt 2026-08-24):
            # the two switches below can both be env-shadowed; this one
            # cannot.
            await pool.execute(
                "INSERT INTO ingestion_state (key, value) "
                "VALUES ('side_echo_tripped', 'true'::jsonb) "
                "ON CONFLICT (key) DO UPDATE SET value='true'::jsonb")
            await pool.execute(
                "INSERT INTO ingestion_state (key, value) "
                "VALUES ('mapping_quarantine', 'true'::jsonb) "
                "ON CONFLICT (key) DO UPDATE SET value='true'::jsonb")
            await pool.execute(
                "INSERT INTO ingestion_state (key, value) "
                "VALUES ('premap_live', 'false'::jsonb) "
                "ON CONFLICT (key) DO UPDATE SET value='false'::jsonb")
            await pool.execute(
                "UPDATE live_orders SET error=$2 WHERE id=$1", row_id,
                ("SIDE-ECHO MISMATCH — auto-requarantined "
                 f"({detail})")[:300])
            log.critical(
                "SIDE-ECHO MISMATCH row %s slug %s: %s — total "
                "quarantine re-armed, premap-live dropped",
                row_id, us_slug, detail)
        elif verdict == "mismatch":
            log.critical(
                "SHADOW SIDE-ECHO MISMATCH row %s slug %s: %s — the "
                "premap lane is NOT certifiable; do not flip the lever",
                row_id, us_slug, detail)
        prev = {}
        try:
            raw = await pool.fetchval(
                "SELECT value FROM ingestion_state WHERE key=$1",
                state_key)
            if raw:
                prev = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:  # noqa: BLE001
            prev = {}
        counts = {k: int(prev.get(k, 0)) for k in
                  ("ok", "mismatch", "unverified")}
        counts[verdict] += 1
        await pool.execute(
            "INSERT INTO ingestion_state (key, value) "
            "VALUES ($2, $1::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value=$1::jsonb",
            json.dumps({**counts, "last": verdict,
                        "last_slug": us_slug, "last_detail": detail,
                        "last_at": datetime.now(tz=timezone.utc)
                        .isoformat(timespec="seconds")}), state_key)
    except Exception:  # noqa: BLE001 — bookkeeping must not raise either
        log.exception("side-echo bookkeeping failed for row %s", row_id)
    # RETURNED so a caller can gate on it. Nothing reads this today —
    # every call site is fire-and-forget through _spawn_echo — but the
    # verdict is the whole product of this function and having it
    # reachable is what lets the fuzzy class be certified before it is
    # ever trusted, rather than trusted and then measured.
    return verdict


async def _record_short_proof(pool, *, ok: bool, net, slug: str) -> None:
    """Tally the position-sign verdict for SHORT fills only.

    netPosition's SIGN is the only end-to-end proof that BUY_SHORT buys
    the short side, and it exists only on a real fill — which is why the
    probation has to place one to earn the next.

    Its own key, because side_echo_last mixes intents: a verified LONG
    says nothing about this question.
    """
    try:
        raw = await pool.fetchval(
            "SELECT value FROM ingestion_state WHERE key=$1",
            SHORT_PROOF_KEY)
        prev = (json.loads(raw) if isinstance(raw, str) else raw) or {}
    except Exception:  # noqa: BLE001 — an unreadable tally is not a verdict
        prev = {}
    cur = {"ok": int(prev.get("ok", 0)) + (1 if ok else 0),
           "mismatch": int(prev.get("mismatch", 0)) + (0 if ok else 1),
           "last_net": net, "last_slug": slug,
           "last_at": datetime.now(tz=timezone.utc).isoformat(
               timespec="seconds")}
    try:
        await pool.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
            SHORT_PROOF_KEY, json.dumps(cur))
    except Exception:  # noqa: BLE001 — bookkeeping never breaks a fill
        log.warning("short-proof write failed for %s", slug)


async def _short_gate(pool) -> tuple[bool, str, bool]:
    """(allowed, reason, still_on_probation) for placing a SHORT.

    The third value matters: once the class is proved the caller must
    NOT take the serialising lock, or every short thereafter would queue
    behind the one before it forever — a probation that never ends is
    just a slower ban.

    Fails CLOSED on an unreadable tally: not knowing whether shorts are
    proved is not permission to place four of them at once.
    """
    try:
        raw = await pool.fetchval(
            "SELECT value FROM ingestion_state WHERE key=$1",
            SHORT_PROOF_KEY)
        st = (json.loads(raw) if isinstance(raw, str) else raw) or {}
    except Exception:  # noqa: BLE001
        return False, "short-probation: proof tally unreadable", True
    if int(st.get("mismatch", 0)) > 0:
        return False, ("short-probation: a SHORT fill held the WRONG SIDE "
                       "— the branch re-armed itself and needs a human"), True
    done = int(st.get("ok", 0))
    if done >= SHORT_PROBATION_N:
        return True, "", False
    if _SHORT_LOCK.locked():
        return False, ("short-probation: one short at a time until "
                       f"{SHORT_PROBATION_N} are side-verified "
                       f"({done} so far)"), True
    return True, "", True


async def _echo_then_release_short(coro):
    """Run the echo, then drop the short-probation lock.

    THE HANDOFF IS THE POINT. The placement path returns as soon as the
    order is acknowledged, but the proof this probation waits for —
    netPosition's sign — is only read inside the echo, seconds later.
    Releasing in the placer's finally would let SHORT_PROBATION_N orders
    go out before the first verdict exists, which is exactly the race
    the probation was built to close. So ownership moves to the echo and
    is dropped in a finally: cancellation still runs it, and a process
    that dies takes the in-memory lock with it.
    """
    try:
        return await coro
    finally:
        if _SHORT_LOCK.locked():
            _SHORT_LOCK.release()


def _spawn_echo(pool, row_id: int, us_slug: str, outcome: str | None,
                his_title: str | None, *, shadow: bool,
                his_slug: str | None = None,
                intent: str | None = None,
                mapping_src: str | None = None,
                state_key: str | None = None,
                owns_short_lock: bool = False) -> None:
    """Fire-and-forget echo with a strong task ref (a bare create_task
    can be garbage-collected mid-flight)."""
    coro = _side_echo_verify(
        pool, row_id, us_slug, outcome, his_title, shadow=shadow,
        his_slug=his_slug, intent=intent, mapping_src=mapping_src,
        state_key=state_key)
    if owns_short_lock:
        coro = _echo_then_release_short(coro)
    t = asyncio.create_task(coro)
    _ECHO_TASKS.add(t)
    t.add_done_callback(_ECHO_TASKS.discard)


async def _dh_sibling_guard(pool, src_slug: str) -> bool:
    """True = the derivative-exact mapping for this feed slug must be
    DISCARDED (the row falls through exactly as an unresolved one).

    DOUBLEHEADER GUARD (mapper-fail diagnosis 2026-08-30): on a
    doubleheader day the venue keys its markets with a dh suffix per
    game, and an exact candidate built WITHOUT one is game-agnostic —
    it can land on the wrong game of the pair. If the premap table
    knows ANY dh-keyed sibling for this matchup (either team order,
    either derivative family), the exact lane refuses. An unreadable
    table also refuses: this guard only ever REMOVES a mapping, so
    failing closed costs a copy, never buys a wrong one."""
    from . import pmus

    try:
        fd = pmus._feed_derivative(src_slug)
        pats: list[str] = []
        if fd:
            bt = fd["base"].split("-")
            if len(bt) >= 4:
                dt = "-".join(bt[3:])
                for fam in ("tsc", "asc"):
                    for x, y in ((bt[1], bt[2]), (bt[2], bt[1])):
                        pats.append(f"{fam}-{bt[0]}-{x}-{y}-{dt}-dh%")
        if not pats:
            return False
        return bool(await pool.fetchval(
            "SELECT 1 FROM us_premap WHERE "
            "identifier LIKE ANY($1::text[]) OR "
            "market_slug LIKE ANY($1::text[]) LIMIT 1", pats))
    except Exception:  # noqa: BLE001 — unverifiable: fail closed
        return True


async def _dh_sibling_guard_ml(pool, src_slug: str) -> bool:
    """Doubleheader guard for MONEYLINE-typed slugs (the yes/no
    lane): _feed_derivative returns None for them (suffixless and
    team-code suffixes both), so _dh_sibling_guard is structurally
    inert here. Parses date + 3-token head DIRECTLY from the slug.
    True = discard the mapping; an unparseable slug or unreadable
    table is also True — this guard only ever REMOVES a mapping, so
    failing closed costs a copy, never buys a wrong one."""
    import re as _re

    try:
        s = (src_slug or "").lower()
        m = _re.search(r"\d{4}-\d{2}-\d{2}", s)
        if not m:
            return True
        head = [t for t in s[:m.start()].strip("-").split("-") if t]
        if len(head) != 3:
            return True
        lg, x1, x2 = head
        dt = m.group(0)
        pats = [f"{fam}-{lg}-{x}-{y}-{dt}-dh%"
                for fam in ("atc", "aec", "asc", "tsc")
                for x, y in ((x1, x2), (x2, x1))]
        return bool(await pool.fetchval(
            "SELECT 1 FROM us_premap WHERE "
            "identifier LIKE ANY($1::text[]) OR "
            "market_slug LIKE ANY($1::text[]) LIMIT 1", pats))
    except Exception:  # noqa: BLE001 — unverifiable: fail closed
        return True


async def _rest_lane_spent(pool) -> float:
    """Realised dollars the rest lane has filled so far, from the ledger.

    Counts FILLS, never attempts: a resting bid that cancelled unfilled
    cost nothing and must not consume the authorisation. Unreadable
    means the cap is treated as reached -- a lane that cannot verify its
    own budget does not get to spend.
    """
    try:
        # IOC ADD LEGS MERGED ONTO A REST-FILLED ROW ARE NOT REST MONEY
        # (adds review, 2026-09-02): their dollars are subtracted from
        # the row's total by the row's own leg list.
        v = await pool.fetchval(
            "SELECT COALESCE(SUM(filled_usd), 0)::float8 "
            "- COALESCE((SELECT SUM((a->>'usd')::float8) FROM live_orders lo2, "
            "            jsonb_array_elements(COALESCE(lo2.raw->'adds', '[]'::jsonb)) a "
            "            WHERE lo2.lane = 'rest' AND lo2.filled_usd > 0), 0)::float8 "
            "FROM live_orders WHERE lane = 'rest' AND filled_usd > 0")
        return max(0.0, float(v or 0.0))
    except Exception as exc:  # noqa: BLE001 — a missing column is one
        # of the expected shapes here (migration 041 not yet applied):
        # fail CLOSED, do not rest.
        log.warning("rest lane: budget unreadable (%s) — treating as "
                    "exhausted", type(exc).__name__)
        return float("inf")


# THE BUDGET RACE, CLOSED. The cap is read from the ledger and the fill
# is stamped ~6s later; with four semaphore slots plus the sweep calling
# maybe_execute outside the semaphore, five attempts could pass the
# check together and land five clips over. The lock serialises
# check->reserve, and the reservation is held until the attempt's
# outcome is known, so the cap can be exceeded by at most one clip --
# the one that was already in flight when the last dollar was read.
_REST_LOCK = asyncio.Lock()
_REST_RESERVED_USD = 0.0
# Terminal means the venue will not change the order again. Anything
# in the open set, plus the transient it passes through on the way
# out, is still "unknown" and must not be recorded as a result.
_REST_NONTERMINAL = frozenset(_MANUAL_OPEN_STATES | {"pending_cancel"})


def _rest_terminal(st: dict | None) -> bool:
    return bool(st) and (str(st.get("state") or "").lower()
                         not in _REST_NONTERMINAL)


# The ledger reaper's bound on a 'submitting' row whose order the venue
# has no record of: left alone inside it, marked for reconciliation at
# it. An hour is far past any venue-side consistency lag and far short
# of "forever", which is what an unbounded claim on a market amounts to.
_NO_RECORD_GIVE_UP_S = 3600.0
# The venue reaper's time match: an orphaned GTC is created within this
# many seconds AFTER its row's placed_at (mapping + preview + IOC + GTC,
# "tens of seconds" per the ledger reaper's own bound). A resting BUY on
# a scoped market outside this window is not ours.
_ORPHAN_MATCH_S = 120.0


def _order_created_ts(created) -> float | None:
    """Epoch seconds of a venue order's createTime, or None when the
    shape is not one we can read. Never guesses: an unreadable age is
    skipped by the caller, not treated as old."""
    try:
        if isinstance(created, bool):
            return None
        if isinstance(created, (int, float)):
            return created / 1000.0 if created > 1e11 else float(created)
        if isinstance(created, str) and created:
            from datetime import datetime as _dt
            return _dt.fromisoformat(
                created.replace("Z", "+00:00")).timestamp()
    except Exception:  # noqa: BLE001
        return None
    return None


# A lost placement response: how recently the bid must have been created
# to be ours, and how far the venue's clock may lag the database's for
# the venue reaper's time match (the SIGKILL orphan is placed only ~3-10 s
# after its row's placed_at, so a clock a few seconds behind used to
# fail the match).
_LOST_BID_AGE_S = 90.0
_ORPHAN_SKEW_S = 30.0
# How long after placement a fill at our cent can still be OURS when the
# placement response was lost: the bid rests until the venue reaper's
# cutoff (TTL + 60 s) on the next sweep pass, which is minutes, never
# hours. Anything later at our cent on this shared account is the
# owner's (round eight).
_LOST_FILL_WINDOW_S = 20 * 60.0


def _fingerprint_cents(his_price: float, intent: str | None) -> set[float]:
    """The cent(s) our rest bid would carry for this whale price. With
    no recorded intent (a row killed before its raw was written) both
    legs' cents are acceptable; the side is then taken from the venue's
    own order record."""
    if intent in ("ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT"):
        legs = (intent,)
    else:
        legs = ("ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT")
    return {float(f"{rest_tick(wire_limit(his_price, i), i):.2f}") for i in legs}


def _bid_matches(o: dict, cents: set[float], shares: float) -> bool:
    """OUR fingerprint on a venue order: BUY, one of our cents, our
    whole quantity. The owner's own bid matching all three on the same
    market inside the time window is not a realistic collision."""
    if o.get("side") != "BUY" or not o.get("order_id"):
        return False
    try:
        px = float(o.get("price") or 0.0)
        qty = float(o.get("quantity") or 0.0)
    except (TypeError, ValueError):
        return False
    if abs(qty - float(int(shares))) > 1e-6:
        return False
    return any(abs(px - c) <= 1e-6 for c in cents)


# THE DESK, AND ONLY THE DESK (round ten): the manual sleeve is the one
# that rests GTC tickets and the one sync_open_manual_orders owns from
# 'open'. The underdog sleeve places FOKs like a copy and is reconciled
# like one -- routing it here blind-marked its lost fills.
_DESK_SLEEVES = frozenset({MANUAL_WHALE})
_LEG_LONG, _LEG_SHORT = "ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT"
_CENT_TOL = 0.0051
_IOC_TIFS = frozenset({"IMMEDIATE_OR_CANCEL", "FILL_OR_KILL"})


def _adds_of(v) -> list[dict]:
    """raw->'adds' as the executor reads it: JSON text from asyncpg, a
    list when decoded, None when the row has none."""
    if v is None:
        return []
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return []
    return [a for a in v if isinstance(a, dict)] if isinstance(v, list) else []


async def _merge_add_leg(pool, leg_id: int, standing_id: int, shares: float,
                         price: float | None, usd: float, requested_usd: float,
                         order_id: str | None, trade_id: str, receipt: dict,
                         leg_no: int, tx_hash: str | None = None,
                         his_price: float | None = None) -> bool:
    """Book an add leg's fill ONTO its standing row and retire the leg
    row as 'merged' -- one statement, so both rows change or neither.

    The standing row's fill price becomes the share-weighted average of
    what it held and what the leg bought (weighted by SHARES, never by
    cash: on a short the cash is 1-price per share, and a cost-space
    average would invert every consumer that reads fill_price as the
    contract price); its shares, cost and requested cost add; its
    orig_shares grows by the leg so a later partial exit's fraction is
    of everything ever bought. Every figure is computed IN SQL against
    the row's current columns -- a partial exit that landed between the
    executor's read and this write is not clobbered.

    Returns False, having changed nothing, when the standing row is no
    longer 'filled': the caller then records the leg as its own fill.
    """
    px = float(price) if price is not None else None
    leg = {"trade_id": trade_id, "row_id": leg_id, "shares": float(shares),
           "price": px, "usd": float(usd), "order_id": order_id,
           "ts": time.time(),
           # the fill's identity (twin refusal) and HIS price on this
           # leg, so fidelity can score the leg against its own trade
           "tx_hash": (str(tx_hash).lower() if tx_hash else None),
           "his_price": (float(his_price) if his_price is not None else None)}
    row = await pool.fetchrow(
        """
        WITH leg AS (
            -- IDEMPOTENT ON THE LEG (review round two): a merge whose
            -- reply was lost, or two reaper passes on one named leg,
            -- must not book it twice. The leg is locked and must still
            -- be unmerged, and the standing row must not already list
            -- it; otherwise both halves are no-ops and no row returns.
            SELECT id FROM live_orders
             WHERE id = $1 AND status IN ('submitting', 'error')
             FOR UPDATE
        ), s AS (
            UPDATE live_orders
               SET fill_price = CASE
                       WHEN filled_shares + $3::float8 > 0 AND $4::float8 IS NOT NULL
                       THEN (COALESCE(fill_price, 0)::float8 * filled_shares::float8
                             + $4::float8 * $3::float8)
                            / (filled_shares::float8 + $3::float8)
                       ELSE fill_price END,
                   filled_shares = filled_shares + $3::float8,
                   filled_usd = COALESCE(filled_usd, 0) + $5::float8,
                   requested_usd = COALESCE(requested_usd, 0) + $6::float8,
                   orig_shares = COALESCE(orig_shares, filled_shares::float8) + $3::float8,
                   raw = COALESCE(raw, '{}'::jsonb) || jsonb_build_object(
                         'adds', COALESCE(raw->'adds', '[]'::jsonb) || $7::jsonb)
             WHERE id = $2 AND status = 'filled'
               -- A MERGE NEVER TARGETS A MIRROR BOOK (mirror P1, step 7;
               -- owner order 2026-09-02): a book's standing row is
               -- 'filled' for its whole life and is fed only by the
               -- mirror's own booking. Folding a per-fill leg into it
               -- would hand the book shares the reconciler never placed.
               -- The row reads as gone and the caller keeps the leg as
               -- its own fill, exactly as when the row is gone for good.
               AND COALESCE(lane,'') <> 'mirror'
               AND EXISTS (SELECT 1 FROM leg)
               AND NOT (COALESCE(raw->'adds', '[]'::jsonb)
                        @> jsonb_build_array(jsonb_build_object('row_id', $1::bigint)))
             RETURNING id, filled_shares, fill_price, filled_usd
        )
        UPDATE live_orders l
           SET status = 'merged', order_id = $8,
               filled_shares = 0, fill_price = NULL, filled_usd = 0,
               raw = $9::jsonb, error = $10
          FROM s
         WHERE l.id = $1 AND l.status IN ('submitting', 'error')
         RETURNING s.filled_shares::float8 AS shares, s.fill_price::float8 AS px,
                   s.filled_usd::float8 AS usd
        """,
        int(leg_id), int(standing_id), float(shares), px, float(usd),
        float(requested_usd), json.dumps([leg]), order_id,
        json.dumps({**(receipt or {}), "add_leg": leg,
                    "add_of": int(standing_id)}, default=str),
        f"add-merged: +{float(shares):g} @ {px} (${float(usd):.2f}) into "
        f"row #{standing_id} — leg {leg_no} of {MAX_ADDS_PER_MARKET}")
    if row is not None:
        log.info("add leg %s merged into row %s: now %s shares @ %s ($%.2f)",
                 leg_id, standing_id, _row_get(row, "shares"),
                 _row_get(row, "px"), float(_row_get(row, "usd") or 0))
    return row is not None


# ---------------------------------------------------------------------------
# MIRROR LEDGER PRIMITIVES (P1 step 5; owner order 2026-09-02, "go for it,
# let's get this working"). A mirror BOOK is one standing live_orders row,
# lane='mirror', 'filled' from the moment it opens (migration 047's
# header says why: settlement, the record, the caps, the referees and
# 045's asset claim all read it as the copy position it is, with no new
# status and no index change). These four functions are the ONLY writers
# of that row. Each is guarded on status='filled' AND lane='mirror', so
# no switch, no reaper and no exit path can be talked into moving a
# per-fill row through them, and none of them can write 'exiting',
# 'merged' or 'submitting' -- the three states that hand a row to
# mirror_exit, the add-leg merge and the reapers. The BUY is the
# standing-half arithmetic of _merge_add_leg above as a standalone
# statement, idempotent on (order_id, seq) the way the merge is
# idempotent on the leg; the SELL is mirror_exit's partial statement
# with the lane guard, used for EVERY sale including the one that takes
# the row to zero -- a flat book on a live market stays 'filled' at 0
# shares (the claim holds; his re-lean re-buys onto the same row).
# Nothing here reads a switch: the reconciler decides, the ledger books.

MIRROR_LANE = "mirror"
MIRROR_VENUE = "polymarket-us"
# P1 is long-only (the P1 panel synthesis, section 1). realized_pnl and
# fill_cash both branch on this constant, so a book of any other intent
# would be mis-booked on every sale; the open refuses it by name.
MIRROR_INTENT = "ORDER_INTENT_BUY_LONG"
# The one error text a mirror row ever carries, written on the terminal
# 'cancelled' row of a never-filled book. It must never match a named-row
# LIKE pattern (test_mirror_live_ledger pins it against every one).
MIRROR_NO_FILL_CLOSE_TEXT = "mirror book: closed with no fill"


def _names_constraint(exc: BaseException, constraint: str) -> bool:
    """True when `exc` is the database refusing on the named unique
    index. asyncpg carries the name on constraint_name and in the
    message; maybe_execute has always read the message (the 045 catch),
    and the test fakes carry only the message, so both are read."""
    if str(getattr(exc, "constraint_name", "") or "") == constraint:
        return True
    return constraint in str(exc)


_MIRROR_BOOK_INSERT_SQL = """
INSERT INTO mirror_books (whale, condition_id, us_market_slug, game_key,
                          long_asset, other_asset, intent, map_source,
                          ratio, anchor_usd, his_level, target, episode,
                          state)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
        -- the episode is counted from this market's earlier books, in
        -- the same statement, so a flat-and-reopen numbers itself
        COALESCE((SELECT max(b.episode) FROM mirror_books b
                   WHERE b.whale = $1 AND b.us_market_slug = $3), 0) + 1,
        'live')
RETURNING id, episode
"""

# Every 007 NOT NULL is named (asset, side, his_price, limit_price,
# requested_usd, requested_shares, status); venue (008) and lane (041)
# are set explicitly; trade_id is NULL by construction (joins on trades
# drop the row); fill_price/pnl/error are NULL. raw.preview.intent is
# what ORDER_INTENT_SQL reads, so every side-aware consumer names the
# row BUY_LONG. status 'filled' at INSERT is what makes 045's
# one_fill_per_asset index the claim.
_MIRROR_ROW_INSERT_SQL = """
INSERT INTO live_orders (trade_id, whale_username, asset, condition_id,
                         us_market_slug, venue, lane, side, his_price,
                         limit_price, requested_usd, requested_shares,
                         status, filled_shares, filled_usd, orig_shares,
                         fill_price, pnl, error, raw)
VALUES (NULL, $1, $2, $3, $4, 'polymarket-us', 'mirror', 'BUY', $5, $5,
        0, $6, 'filled', 0, 0, 0, NULL, NULL, NULL, $7::jsonb)
RETURNING id
"""

_MIRROR_BOOK_BACKFILL_SQL = (
    "UPDATE mirror_books SET standing_row_id = $2, updated_at = now() "
    "WHERE id = $1 AND standing_row_id IS NULL")


async def _open_mirror_book(pool, whale: str, cid: str, slug: str,
                            long_asset: str, other_asset: str | None,
                            ratio: float | None, anchor_usd: float | None,
                            his_level: float, target: int,
                            map_source: str | None, game_key: str | None,
                            intent: str = MIRROR_INTENT) -> dict:
    """Open a book: ONE transaction that inserts the mirror_books row,
    then the standing live_orders row (P1 spec section 1b), then
    back-fills standing_row_id. Returns {ok, book_id, standing_row_id,
    refusal}; on any refusal nothing is written.

    THE INSERT IS THE CLAIM. 045's live_orders_one_fill_per_asset refuses
    the standing row while any copy row holds the asset -> 'asset_claimed'
    and the book row is rolled back with it; 047's
    mirror_books_one_open_per_market refuses a second open book on the
    market -> 'book_exists'. Anything else that raises -- an unreadable
    pool, a NOT NULL, a crash between the two INSERTs -- is
    'open_failed:<ExcName>' with the transaction rolled back: a half
    book (a mirror_books row with no standing row, or the reverse) is
    exactly the shape the reconciler cannot reason about, so it must not
    exist. Unusable inputs are refused before the pool is touched.
    """
    out = {"ok": False, "book_id": None, "standing_row_id": None,
           "refusal": None}
    if intent != MIRROR_INTENT:
        # P2's door (the _short_gate) stays shut in the ledger too
        out["refusal"] = "short_side_refused"
        return out
    try:
        w = str(whale or "").strip().lower()
        c, s, la = str(cid or "").strip(), str(slug or "").strip(), str(long_asset or "").strip()
        if not (w and c and s and la):
            raise ValueError("a book needs a whale, a condition, a slug and a long asset")
        lvl = float(his_level)
        if not (0.0 < lvl < 1.0):
            raise ValueError("his level must be a price inside (0, 1)")
        tgt = int(target)
        if tgt < 0:
            raise ValueError("target shares cannot be negative")
        r = float(ratio) if ratio is not None else None
        anchor = float(anchor_usd) if anchor_usd is not None else None
        # a NaN ratio would reach json.dumps as the bare token NaN and be
        # refused by the jsonb cast (step-5 review): refused here, by name
        for name, v in (("ratio", r), ("anchor", anchor)):
            if v is not None and not math.isfinite(v):
                raise ValueError(f"{name} is not a number")
        oa = str(other_asset).strip() if other_asset else None
        src = str(map_source) if map_source else None
        gk = str(game_key) if game_key else None
    except (TypeError, ValueError) as exc:
        out["refusal"] = f"open_failed:{type(exc).__name__}"
        log.warning("mirror book refused before open (%s %s): %s", whale, slug, exc)
        return out
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                book = await conn.fetchrow(
                    _MIRROR_BOOK_INSERT_SQL, w, c, s, gk, la, oa, intent, src,
                    r, anchor, lvl, tgt)
                book_id, episode = int(book["id"]), int(book["episode"])
                raw = {"lane": MIRROR_LANE,
                       "preview": {"intent": intent},
                       "mirror": {"book_id": book_id, "episode": episode,
                                  "ratio": r, "opened_at": time.time()},
                       "adds": []}
                row_id = int(await conn.fetchval(
                    _MIRROR_ROW_INSERT_SQL, w, la, c, s, lvl, tgt,
                    json.dumps(raw)))
                await conn.execute(_MIRROR_BOOK_BACKFILL_SQL, book_id, row_id)
    except Exception as exc:  # noqa: BLE001 — every failure is named; the
        # transaction context has already rolled the writes back
        if _names_constraint(exc, "live_orders_one_fill_per_asset"):
            out["refusal"] = "asset_claimed"
        elif _names_constraint(exc, "mirror_books_one_open_per_market"):
            out["refusal"] = "book_exists"
        else:
            out["refusal"] = f"open_failed:{type(exc).__name__}"
        log.warning("mirror book not opened (%s %s): %s — %s", w, s,
                    out["refusal"], exc)
        return out
    out.update(ok=True, book_id=book_id, standing_row_id=row_id)
    log.info("mirror book %s opened for %s on %s: standing row %s "
             "(episode %s, target %s @ %s, ratio %s)",
             book_id, w, s, row_id, episode, tgt, lvl, r)
    return out


# The standing-half arithmetic of _merge_add_leg, verbatim (parameters
# renumbered): the fill price becomes the SHARE-weighted average of what
# the row held and what this fill bought -- a flat row (0 shares, whatever
# fill_price it last carried) restarts at the fill's price, so a
# flat-then-rebuy episode never inherits the old cost -- shares, cost and
# requested cost add, orig_shares grows so a later partial sale's
# fraction is of everything ever bought. Everything is computed IN SQL
# against the row's current columns: a sale that landed between the
# reconciler's read and this write is not clobbered.
#
# IDEMPOTENT ON (order_id, seq): raw.adds lists every fill booked, and a
# fill already listed is a no-op returning no row -- a reply lost between
# the venue read and the write is re-booked exactly once. The `usd` and
# `ts` keys are what _caps_room's leg clause sums on rows older than 24 h,
# so the daily cap sees every mirror dollar on its own clock.
_MIRROR_BUY_SQL = """
UPDATE live_orders
   SET fill_price = CASE
           WHEN filled_shares + $2::float8 > 0 AND $3::float8 IS NOT NULL
           THEN (COALESCE(fill_price, 0)::float8 * filled_shares::float8
                 + $3::float8 * $2::float8)
                / (filled_shares::float8 + $2::float8)
           ELSE fill_price END,
       filled_shares = filled_shares + $2::float8,
       filled_usd = COALESCE(filled_usd, 0) + $4::float8,
       requested_usd = COALESCE(requested_usd, 0) + $5::float8,
       orig_shares = COALESCE(orig_shares, filled_shares::float8) + $2::float8,
       raw = COALESCE(raw, '{}'::jsonb) || jsonb_build_object(
             'adds', COALESCE(raw->'adds', '[]'::jsonb) || $6::jsonb)
 WHERE id = $1 AND status = 'filled' AND lane = 'mirror'
   AND NOT (COALESCE(raw->'adds', '[]'::jsonb)
            @> jsonb_build_array(jsonb_build_object('order_id', $7::text,
                                                    'seq', $8::int)))
 RETURNING filled_shares::float8 AS filled_shares,
           fill_price::float8 AS fill_price, filled_usd::float8 AS filled_usd
"""


def _mirror_qty_px(shares, price) -> tuple[float, float] | None:
    """(shares, price) as floats when they describe a fill that can be
    booked -- a positive finite quantity at a price inside (0, 1) -- else
    None. A fill with no price or no quantity is not booked: the ledger
    then disagrees with the venue and the reconciler freezes the book
    by name (venue_ledger_disagree), which is the fail-closed outcome;
    a guessed price would be a wrong average on every later sale."""

    try:
        q, px = float(shares), float(price)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(q) or not (q > 0.0) or not (0.0 < px < 1.0):
        return None
    return q, px


async def _book_mirror_buy(pool, standing_row_id: int, order_id: str, seq: int,
                           shares: float, price: float | None, usd: float,
                           wire_usd: float, his_price: float | None,
                           maker: bool):
    """Book a BUY fill of `shares` @ `price` onto the standing row: the
    RETURNING row (filled_shares, fill_price, filled_usd, all float8) or
    None when nothing was written -- the (order_id, seq) is already on
    raw.adds, the row is not a live mirror row, or the inputs describe no
    bookable fill. `usd` is fill_cash(shares, price, BUY_LONG) -- the
    cash the fill cost; `wire_usd` is shares x the wire price, added to
    requested_usd so the live-status ratio reads ~1; `maker` records
    whether the fill came off a rest (the P2 gate's maker_share).
    """
    qp = _mirror_qty_px(shares, price)
    if qp is None or not order_id:
        return None
    q, px = qp
    try:
        sid, sq = int(standing_row_id), int(seq)
        cash, wire_cash = float(usd), float(wire_usd)
        his = float(his_price) if his_price is not None else None
    except (TypeError, ValueError):
        return None
    if sq < 0 or not (cash >= 0.0) or not (wire_cash >= 0.0):
        return None
    # a NaN his level would reach json.dumps as the bare token NaN and be
    # refused by the jsonb cast on every tick (step-5 review): an
    # unreadable level is recorded as none, never as a cast error
    if his is not None and not math.isfinite(his):
        his = None
    if not (math.isfinite(cash) and math.isfinite(wire_cash)):
        return None
    add = {"order_id": str(order_id), "seq": sq, "ts": time.time(),
           "shares": q, "price": px, "usd": cash, "his_price": his,
           "lane": MIRROR_LANE, "maker": bool(maker)}
    row = await pool.fetchrow(_MIRROR_BUY_SQL, sid, q, px, cash, wire_cash,
                              json.dumps([add]), str(order_id), sq)
    if row is not None:
        log.info("mirror row %s booked BUY %s @ %s ($%.2f, order %s seq %s): "
                 "now %s shares @ %s", sid, q, px, cash, order_id, sq,
                 _row_get(row, "filled_shares"), _row_get(row, "fill_price"))
    return row


# mirror_exit's partial statement (its fragments are pinned against
# mirror_exit's source) with the lane guard and a RETURNING so the caller
# knows whether the row moved. GREATEST(.., 0) is the belt against a
# negative ledger; orig_shares is captured from the current column on the
# way past, exactly as the exit does, so a later fraction is of
# everything ever bought.
_MIRROR_SELL_SQL = (
    "UPDATE live_orders SET status='filled', "
    "orig_shares=COALESCE(orig_shares, filled_shares::float8), "
    "filled_shares=GREATEST(filled_shares - $2::float8, 0), "
    "pnl=COALESCE(pnl,0)+$3 WHERE id=$1 AND status='filled' AND lane='mirror' "
    "RETURNING filled_shares::float8 AS filled_shares, pnl::float8 AS pnl")

_MIRROR_SELL_READ_SQL = (
    "SELECT fill_price::float8 AS fill_price, filled_shares::float8 AS filled_shares "
    "FROM live_orders WHERE id = $1 AND status = 'filled' AND lane = 'mirror' "
    "/* mirror-sell */")


async def _book_mirror_sell(pool, standing_row_id: int, shares: float,
                            price: float | None, ledger_net: float) -> dict:
    """Book a SELL fill of `shares` @ `price` against the standing row.
    Returns {booked, pnl, overfill, written, refusal}.

    booked = min(shares, ledger_net, the row's own shares): the venue
    can report a sale past what the ledger held (on a signed-net venue
    that is a SHORT we never meant to hold), and the ledger books only
    what it had and flags `overfill` -- the reconciler freezes the book
    and trips mirror_live off with the receipt; the primitive never
    writes a negative ledger. pnl is realized_pnl(row.fill_price, price,
    booked, BUY_LONG), accumulated onto the row like every exit leg.

    NEVER 'cashed_out' HERE: a book sold to zero on a live market stays
    'filled' at 0 shares, holding the asset claim, so his re-lean re-buys
    onto the same row and settlement never grades a position we still
    trade (mirror_exit's "a position is never retired while shares
    remain", widened to "never retired while the market is live"; the
    episode close below is the only path off 'filled').

    A row with shares but no fill_price is a ledger that cannot price
    its own entry: refused 'no_entry_price', nothing written, rather
    than booking a $0 pnl the record would then carry as truth.
    """
    out = {"booked": 0.0, "pnl": None, "overfill": False, "written": False,
           "refusal": None}
    qp = _mirror_qty_px(shares, price)
    if qp is None:
        out["refusal"] = "bad_fill"
        return out
    q, px = qp
    try:
        sid = int(standing_row_id)
        net = float(ledger_net)
    except (TypeError, ValueError):
        out["refusal"] = "bad_fill"
        return out
    net = net if net > 0.0 else 0.0
    row = await pool.fetchrow(_MIRROR_SELL_READ_SQL, sid)
    if row is None:
        out["refusal"] = "row_not_live"
        return out
    held = float(_row_get(row, "filled_shares") or 0.0)
    ceiling = min(net, held if held > 0.0 else 0.0)
    out["overfill"] = q > ceiling + 1e-9
    booked = min(q, ceiling)
    if booked <= 0.0:
        out["refusal"] = "nothing_to_book"
        return out
    entry = _row_get(row, "fill_price")
    pnl = realized_pnl(entry, px, booked, MIRROR_INTENT)
    if pnl is None:
        out["refusal"] = "no_entry_price"
        log.error("mirror row %s: SELL %s @ %s cannot be priced (fill_price %r); "
                  "nothing booked", sid, q, px, entry)
        return out
    r = await pool.fetchrow(_MIRROR_SELL_SQL, sid, float(booked), pnl)
    if r is None:
        out["refusal"] = "row_not_live"
        return out
    out.update(booked=float(booked), pnl=pnl, written=True)
    log.info("mirror row %s booked SELL %s @ %s (entry %s, pnl %+.4f%s): now %s shares",
             sid, booked, px, entry, pnl,
             ", OVERFILL: venue sold %s" % q if out["overfill"] else "",
             _row_get(r, "filled_shares"))
    return out


# The episode close, the ONLY path off 'filled' for a mirror row: the
# le mirror_exit 'cashed_out' shape with $2 = 0 (every sale's pnl is
# already accumulated on the row), or 'cancelled' with the one mirror
# error text when the book never bought -- the asset claim is freed and a
# never-filled book never waits on a venue verdict that will not come.
# Both are guarded on the row being flat: a row that still holds shares
# is settled by _settle_pmus_from_venue when its market resolves, never
# closed by us (a position is never retired while shares remain).
_MIRROR_CLOSE_CASHED_OUT_SQL = (
    "UPDATE live_orders SET status='cashed_out', "
    "pnl=COALESCE(pnl,0)+$2, settled_at=now() "
    "WHERE id=$1 AND lane='mirror' AND status='filled' AND filled_shares = 0 "
    "RETURNING id")
_MIRROR_CLOSE_CANCELLED_SQL = (
    "UPDATE live_orders SET status='cancelled', error=$2 "
    "WHERE id=$1 AND lane='mirror' AND status='filled' AND filled_shares = 0 "
    "RETURNING id")


async def _close_mirror_episode(pool, standing_row_id: int,
                                gross_buy_usd: float) -> str | None:
    """Close the episode's standing row: 'cashed_out' when the book ever
    bought (gross_buy_usd > 0), 'cancelled' when it never did (== 0),
    None when nothing was written -- the row is not a live, FLAT mirror
    row, or gross_buy_usd is unreadable or negative (a figure the book
    cannot vouch for closes nothing)."""
    try:
        sid, gross = int(standing_row_id), float(gross_buy_usd)
    except (TypeError, ValueError):
        return None
    if not (gross >= 0.0):
        return None
    if gross > 0.0:
        r = await pool.fetchrow(_MIRROR_CLOSE_CASHED_OUT_SQL, sid, 0.0)
        verdict = "cashed_out"
    else:
        r = await pool.fetchrow(_MIRROR_CLOSE_CANCELLED_SQL, sid,
                                MIRROR_NO_FILL_CLOSE_TEXT)
        verdict = "cancelled"
    if r is None:
        return None
    log.info("mirror row %s closed %s (gross buys $%.2f)", sid, verdict, gross)
    return verdict


async def _tx_hash_of(pool, trade_id) -> str:
    """The transaction hash of one trades row, lower-cased; '' when the
    row or the hash cannot be read (an add candidate with no readable
    identity is refused, fail closed)."""
    try:
        v = await pool.fetchval(
            "SELECT tx_hash FROM trades WHERE id = $1 /* add-tx */",
            int(trade_id))
    except (TypeError, ValueError):
        return ""
    except Exception:  # noqa: BLE001 — unreadable identity is no identity
        return ""
    return str(v or "").lower()


def _json_obj(v) -> dict:
    """A jsonb column as asyncpg hands it back: text, a dict, or None."""
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        try:
            d = json.loads(v)
            return d if isinstance(d, dict) else {}
        except ValueError:
            return {}
    return {}


# The standing-row states under which an add leg must NOT be booked as a
# position of its own: 'filled'/'settled' hold the one-fill-per-asset
# claim; 'exiting' is mirror_exit's claim, which writes the row back to
# 'filled' after the sale -- a second 'filled' row on the asset would
# refuse that write and wedge the exit for good; 'submitting' is a row in
# flight that may still fill.
_ADD_STANDING_LIVE = frozenset({"filled", "settled", "exiting", "submitting"})


async def _record_add_leg_orphan(pool, row_id: int, standing_id, filled: float,
                                 px: float | None, spent: float,
                                 order_id: str | None, text: str) -> None:
    """Record an add leg's fill ON THE LEG under the orphan name: the
    figures, the order, status 'error' (so it leaves the in-flight claim
    and the sweep, the reclaim and never-add refuse to re-buy while it
    stands). The reaper revisits named rows: it merges the leg once the
    standing row is 'filled' again, or promotes it once that row is gone."""
    await pool.execute(
        "UPDATE live_orders SET status='error', order_id=COALESCE($6, order_id), "
        "filled_shares=$2, fill_price=$3, filled_usd=$4, error=$5 "
        "WHERE id=$1 AND status IN ('submitting', 'error')",
        row_id, float(filled), px, float(spent),
        f"ORPHAN FILL RECORDED on a row that cannot re-enter 'filled' "
        f"(add leg of row #{standing_id}; the standing row holds this "
        f"asset's claim) — {text}", order_id)
    log.error("ADD LEG NAMED: row %s filled %s @ %s ($%.2f) but could not "
              "merge into row %s — recorded under the orphan name for the "
              "reaper", row_id, filled, px, float(spent), standing_id)


async def _book_add_leg_if_any(pool, row_id: int, filled: float,
                               px: float | None, spent: float,
                               order_id: str | None, text: str) -> bool | None:
    """The reaper's booking for a row that names a standing row on its
    raw ('add_of'): an add leg that filled and died before its merge.

    Returns None when the row is not an add leg (the caller books it as
    it always did); True when the fill was merged onto the standing row
    by the executor's own one statement; False when the standing row is
    not 'filled' right now and the figures were recorded on the leg under
    the orphan name for a later pass. Never books a second position on
    the asset beside its standing row.
    """
    leg = await pool.fetchrow(
        "SELECT (raw->>'add_of')::text AS add_of, trade_id, "
        "       requested_usd::float8 AS requested_usd, "
        "       his_price::float8 AS his_price, raw::text AS raw "
        "FROM live_orders WHERE id = $1 /* add-leg */", row_id)
    add_of = _row_get(leg, "add_of") if leg is not None else None
    if not add_of:
        return None
    try:
        standing_id = int(add_of)
    except (TypeError, ValueError):
        return None
    standing = await pool.fetchrow(
        "SELECT status, (raw->'adds')::text AS adds FROM live_orders "
        "WHERE id = $1 /* add-standing */", standing_id)
    if standing is not None and _row_get(standing, "status") == "filled":
        tx = await _tx_hash_of(pool, _row_get(leg, "trade_id")) or None
        merged = await _merge_add_leg(
            pool, row_id, standing_id, float(filled), px, float(spent),
            float(_row_get(leg, "requested_usd") or 0.0), order_id,
            str(_row_get(leg, "trade_id") or ""), _json_obj(_row_get(leg, "raw")),
            len(_adds_of(_row_get(standing, "adds"))) + 1, tx_hash=tx,
            his_price=_row_get(leg, "his_price"))
        if merged:
            log.warning("reaper: add leg %s merged into row %s (%s)",
                        row_id, standing_id, text)
            return True
    await _record_add_leg_orphan(pool, row_id, standing_id, filled, px, spent,
                                 order_id, text)
    return False


async def _merge_named_add_leg(pool, r) -> bool | None:
    """A named add leg on the reaper's revisit. True: merged onto its
    standing row (now 'filled' again). None: the standing row is still
    live but not 'filled' (mid-exit, in flight, settled) -- wait, do not
    promote. False: the standing row is gone, or the leg carries no
    figures -- the caller's promotion path applies."""
    leg = await pool.fetchrow(
        "SELECT id, order_id, (raw->>'add_of')::text AS add_of, "
        "       filled_shares::float8 AS filled_shares, "
        "       fill_price::float8 AS fill_price, filled_usd::float8 AS filled_usd "
        "FROM live_orders WHERE id = $1 AND status = 'error' "
        "AND error LIKE 'ORPHAN FILL RECORDED%' /* named-leg */", r["id"])
    if leg is None:
        return False
    try:
        standing_id = int(_row_get(leg, "add_of"))
    except (TypeError, ValueError):
        return False
    # THE STANDING ROW'S STATE DECIDES FIRST (review round two): a leg
    # is never promoted beside a live standing row, whether or not it
    # carries figures -- a figure-less promotion beside an 'exiting' row
    # would refuse the exit's write-back just the same.
    # A MIRROR BOOK READS AS GONE (mirror P1, step 7; owner order
    # 2026-09-02): a merge can never target a book's standing row, so a
    # named leg that names one takes the promotion path below, which
    # the one-fill-per-asset index refuses for as long as the book's
    # claim stands and admits once the book has closed -- the shares
    # are real and then become the copy position they are.
    standing = await pool.fetchrow(
        "SELECT status, settled_at FROM live_orders WHERE id = $1 "
        "AND COALESCE(lane,'') <> 'mirror' /* named-standing */", standing_id)
    st = _row_get(standing, "status") if standing is not None else None
    has_figures = float(_row_get(leg, "filled_shares") or 0) > 0
    if st == "filled":
        if not has_figures:
            return None          # nothing to merge; wait for the figures
        booked = await _book_add_leg_if_any(
            pool, r["id"], float(_row_get(leg, "filled_shares") or 0),
            _row_get(leg, "fill_price"), float(_row_get(leg, "filled_usd") or 0),
            _row_get(leg, "order_id"),
            "merged by reaper: named add leg booked onto its standing row")
        return True if booked else None
    if st == "settled":
        # THE MARKET RESOLVED BEFORE THE MERGE (review round two): the
        # standing row never returns to 'filled', and the venue's
        # realized P&L for the slug -- which already nets this leg's
        # cost -- is allocated to the standing row. The leg is retired
        # settled at zero so it neither waits forever nor counts twice.
        await pool.execute(
            "UPDATE live_orders SET status='settled', pnl=0, "
            "settled_at=COALESCE($2, now()), error=$3 "
            "WHERE id=$1 AND status='error' "
            "AND error LIKE 'ORPHAN FILL RECORDED%'",
            r["id"], _row_get(standing, "settled_at"),
            f"add leg retired: standing row #{standing_id} settled before the "
            f"merge; the slug's realized P&L is on that row")
        log.warning("reaper: named add leg %s retired settled beside row %s",
                    r["id"], standing_id)
        return True
    if st in _ADD_STANDING_LIVE:
        return None
    return False


def _pre_ids_of(v) -> set[str]:
    """raw->'pre_ids' as the reapers read it: a JSON text from asyncpg,
    a list when a codec decoded it, None when the row has none."""
    if v is None:
        return set()
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return set()
    if isinstance(v, (list, tuple, set)):
        return {str(x) for x in v if x}
    return set()


def _leg_cents(his_price: float, limit_price: float | None,
               leg: str) -> tuple[float, float]:
    """The (lo, hi) cent band a row's OWN orders could carry on one leg:
    the rest cent (his price on the tick) and the IOC cent (his price
    plus the authorised slippage, the row's limit_price). One cent when
    the row carries no limit."""
    a = float(f"{rest_tick(wire_limit(his_price, leg), leg):.2f}")
    try:
        lp = float(limit_price) if limit_price is not None else 0.0
    except (TypeError, ValueError):
        lp = 0.0
    b = float(f"{wire_limit(lp, leg):.2f}") if 0.0 < lp < 1.0 else a
    return (min(a, b), max(a, b))


def _lost_fill_is_ours(f: dict, his_price: float, intent: str | None,
                       shares: float, lo_ts: float, hi_ts: float,
                       limit_price: float | None = None) -> bool:
    """One venue trade row is OURS only when the venue's own execution
    record names an order of exactly our size, on our leg's side (the
    venue books a short as a SELL), whose price sits in a cent band this
    row authorised, with no realized P&L (an opening fill), inside the
    row's window. A fill whose order the venue did not name is unknown,
    never ours: the account is shared with the owner. A row with no
    recorded leg is read as the long leg only."""
    try:
        ts = float(f.get("ts") or 0.0)
        if not (lo_ts <= ts <= hi_ts):
            return False
        if abs(float(f.get("realized_pnl") or 0.0)) > 1e-9:
            return False
        oq = f.get("order_qty")
        if oq is None or abs(float(oq) - float(int(shares))) > 1e-6:
            return False
        px = f.get("order_price")
        px = float(px) if px is not None else float(f.get("price") or 0.0)
    except (TypeError, ValueError):
        return False
    side = str(f.get("side") or "").upper()
    legs = (intent,) if intent in (_LEG_LONG, _LEG_SHORT) else (_LEG_LONG,)
    for leg in legs:
        lo, hi = _leg_cents(his_price, limit_price, leg)
        if not (lo - _CENT_TOL <= px <= hi + _CENT_TOL):
            continue
        if ("BUY" if leg == _LEG_LONG else "SELL") in side:
            return True
    return False


async def _stamp_lost_fill_lane(pool, row_id, ours: list[dict],
                                his_price: float, intent: str | None) -> None:
    """A reconciled lost fill is a REST fill when its order sat on the
    rest cent and an IOC fill otherwise, so the impact buckets read it
    where it belongs. Best-effort."""
    try:
        f = ours[0] if ours else {}
        px = f.get("order_price")
        px = float(px) if px is not None else float(f.get("price") or 0.0)
        lane = ("rest" if any(abs(px - c) <= 1e-6
                              for c in _fingerprint_cents(his_price, intent))
                else "ioc")
        await pool.execute("UPDATE live_orders SET lane=$2 WHERE id=$1",
                           row_id, lane)
    except Exception:  # noqa: BLE001 — the stamp is best-effort
        pass


async def _ioc_guarded(pool, row_id, fn, *args):
    """Run the IOC so that OUR OWN cancellation cannot lose its result.
    The venue call runs in a shielded task; if this coroutine is
    cancelled while it is in flight (the sweep's per-row timeout, a
    shutdown), the task is left to finish and its order id is written
    onto the row for the ledger reaper to reconcile by id."""
    fut = asyncio.ensure_future(asyncio.to_thread(fn, *args))
    try:
        return await asyncio.shield(fut)
    except asyncio.CancelledError:
        asyncio.ensure_future(_record_orphan_ioc(pool, row_id, fut))
        raise


async def _record_orphan_ioc(pool, row_id, fut) -> None:
    try:
        result = await asyncio.wait_for(fut, timeout=120.0)
    except BaseException as exc:  # noqa: BLE001 — includes cancellation
        log.error("IOC on row %s was cancelled mid-call and its result "
                  "could not be read (%s) — the row stays 'submitting' "
                  "with no id for the reapers", row_id, type(exc).__name__)
        return
    oid = result.get("order_id") if isinstance(result, dict) else None
    if not oid:
        return          # refused or unanswered: the blind mark is right
    try:
        await pool.execute(
            "UPDATE live_orders SET order_id=$2 WHERE id=$1 "
            "AND status='submitting' AND order_id IS NULL", row_id, str(oid))
        log.error("IOC on row %s was cancelled mid-call; its order %s "
                  "(filled %s) is persisted for the reaper to reconcile",
                  row_id, oid, result.get("filled_shares"))
    except Exception as exc:  # noqa: BLE001
        log.error("IOC on row %s was cancelled mid-call and its order %s "
                  "could not be persisted (%s)", row_id, oid,
                  type(exc).__name__)
        return
    # AN IOC IS AN IOC (round ten): the reaper's by-id reconcile stamps a
    # lane only where none is set, and 'rest' would charge this fill to
    # the rest-lane budget. Best-effort; the column is migration 041's.
    try:
        await pool.execute("UPDATE live_orders SET lane='ioc' WHERE id=$1",
                           row_id)
    except Exception:  # noqa: BLE001
        pass


async def _find_lost_rest_bid(pmus, us_slug: str, shares: float,
                              cents: set[float],
                              window: tuple[float, float] | None = None,
                              exclude: set[str] | None = None,
                              ) -> tuple[dict | None, bool]:
    """After a placement whose response was lost: the venue's open orders
    on this market, looking for OUR fingerprint (see _bid_matches)
    created inside `window` (epoch seconds; default: the last
    _LOST_BID_AGE_S). Returns (order, readable): readable=False means the
    venue could not be listed, which is not "nothing there". Anything
    else on the book is not ours. The window has BOTH bounds (round
    eight): a bid created before the row, or hours after it, is the
    owner's however well it matches."""
    try:
        orders = await asyncio.to_thread(pmus.open_orders, [us_slug])
    except Exception:  # noqa: BLE001 — unreadable is not "not found"
        return None, False
    now = time.time()
    lo, hi = window if window else (now - _LOST_BID_AGE_S, now + 5.0)
    for o in orders or []:
        if str(o.get("us_market_slug") or "").lower() != us_slug.lower():
            continue
        if exclude and str(o.get("order_id")) in exclude:
            continue          # it was on the book before we placed
        if not _bid_matches(o, cents, shares):
            continue
        ts = _order_created_ts(o.get("created_at"))
        if ts is None or not (lo <= ts <= hi):
            continue
        return o, True
    return None, True


async def _rest_cycle(pool, pmus, row_id: int, us_slug: str, wire: float,
                      shares: float, intent: str | None, whale: str,
                      his_price: float) -> dict | None:
    """place -> persist id -> wait -> cancel -> read until terminal.

    Runs under asyncio.shield from the caller, so a wait_for timeout or
    task cancellation in the copy path cannot interrupt it between
    placement and cancel. It still cannot survive process death, which
    is why the order id is persisted on the row the instant the venue
    returns it: a row left 'submitting' with an order_id is reconciled
    by _reap_stale_submitting, and a bid the ledger never saw at all is
    swept by the venue-side reaper. Belt, braces, and a third thing.
    """
    # WHAT WAS ON THE BOOK BEFORE WE PLACED (round nine): an order id
    # that existed before our GTC went out can never be our GTC, however
    # well it matches our fingerprint -- the owner resting exactly our
    # size at exactly our cent is the one collision the fingerprint
    # cannot see. UNREADABLE MEANS NO REST (round eleven, reproduced): a
    # book we could not read plus a placement whose response is lost --
    # one venue fault -- adopted, cancelled and BOOKED the owner's own
    # bid, because the only exclusion we had was the one we could not
    # take. A lane that cannot see the book does not place on it.
    pre_ids: set[str] = set()
    snapshot_ok = False
    for _attempt in (1, 2):
        try:
            pre_ids = {str(o.get("order_id"))
                       for o in (await asyncio.to_thread(pmus.open_orders, [us_slug])) or []
                       if o.get("order_id")}
            snapshot_ok = True
            break
        except Exception:  # noqa: BLE001
            pre_ids = set()
    if not snapshot_ok:
        _copy_stop("rest_book_unreadable", whale)
        log.warning("rest lane: open book unreadable before placing on %s "
                    "— not resting (row %s)", us_slug, row_id)
        return None
    # THE SNAPSHOT TRAVELS WITH THE ROW BEFORE THE ORDER GOES OUT: a
    # process killed inside the placement hang is exactly the case that
    # loses the response, and the reapers must still know what was on
    # the book before ours. Best-effort; raw is jsonb (migration 007).
    try:
        await pool.execute(
            "UPDATE live_orders SET raw = COALESCE(raw, '{}'::jsonb) || "
            "$2::jsonb WHERE id=$1", row_id,
            json.dumps({"pre_ids": sorted(pre_ids)}))
    except Exception as exc:  # noqa: BLE001
        log.warning("rest lane: could not persist the book snapshot on row "
                    "%s (%s)", row_id, type(exc).__name__)
    try:
        placed = await asyncio.to_thread(
            pmus.submit_fok, us_slug, wire, int(shares), False,
            "TIME_IN_FORCE_GOOD_TILL_CANCEL", intent)
    except Exception as exc:  # noqa: BLE001 — the RESPONSE is lost, not the order
        # THE RESPONSE WAS LOST, NOT NECESSARILY THE ORDER (round four,
        # reproduced through the real adapter: orders.create accepts
        # the GTC, then the HTTP read times out). The first version
        # returned None here -- "nothing happened" -- and the row was
        # written 'unfilled' under the IOC's id, so a clip-sized GTC
        # rested on the live book with no ledger row naming it and
        # outside BOTH reapers' scope. Two nets now: look for our own
        # fingerprint on the book and adopt it; failing that, hold the
        # row 'submitting' with NO id, which is exactly the signature
        # the venue reaper time-matches and the ledger reaper blind-
        # marks -- never 'unfilled'.
        # AND NEVER A PROTECTED ID (P1 step 8, owner order 2026-09-02 "go
        # for it, let's get this working"; the panel review's reaper-
        # isolation ruling): the desk's tickets and the mirror's rests
        # are excluded by name beside the pre-placement snapshot. A
        # mirror rest at his cent for the book's whole quantity is the
        # one collision the fingerprint cannot see, and the snapshot
        # only catches it when it predates ours. An unreadable set is
        # "not found": the row is held 'submitting' with no id for the
        # reapers -- the path below, unchanged.
        protected = await _protected_order_ids(pool)
        if protected is None:
            log.warning("rest lane: protected order ids unreadable after "
                        "a lost placement response on %s — not searching "
                        "the book (row %s)", us_slug, row_id)
            found = None
        else:
            found, _ = await _find_lost_rest_bid(
                pmus, us_slug, shares, {float(f"{float(wire):.2f}")},
                window=(time.time() - _LOST_BID_AGE_S, time.time() + 5.0),
                exclude=pre_ids | protected)
        if found is None:
            _copy_stop("rest_place_error", whale)
            log.warning("rest lane: place raised %s for row %s and no "
                        "matching bid is on the book — holding the row "
                        "'submitting' with no id for the reapers",
                        type(exc).__name__, row_id)
            return {"ok": False, "order_id": None, "status": "rest_unknown",
                    "fill_price": None, "filled_shares": 0.0,
                    # THE SNAPSHOT TRAVELS WITH THE ROW (round ten): the
                    # reapers read raw->'pre_ids' so an order that was on
                    # the book before ours is never adopted by them either.
                    "raw": {"lane": "rest", "wire": wire,
                            "ttl_s": REST_BID_TTL_S, "final": None,
                            "pre_ids": sorted(pre_ids),
                            "cancel_ok": False, "preview": {"intent": intent},
                            "why": (f"rest lane: placement raised "
                                    f"{type(exc).__name__} on {us_slug}; no "
                                    f"matching bid found on the book — row "
                                    f"held 'submitting' with no id so the "
                                    f"reapers can reconcile whatever the "
                                    f"venue holds")}}
        log.warning("rest lane: place raised %s for row %s but the venue "
                    "holds our bid %s — adopting it", type(exc).__name__,
                    row_id, found.get("order_id"))
        placed = {"ok": False, "order_id": found.get("order_id"),
                  "status": "new",
                  "filled_shares": float(found.get("filled_shares") or 0.0),
                  "fill_price": found.get("avg_px"),
                  "raw": {"recovered_after": type(exc).__name__,
                          "preview": {"intent": intent}, "response": {}}}
    # GATE ON THE VENUE'S ORDER ID, NOT ON submit_fok's `ok`. That
    # adapter defines ok as "filled > 0" (pmus.py, pinned by
    # test_submit_fok_killed_not_ok). A GTC the venue ACCEPTS AND RESTS
    # -- the normal outcome of every rest attempt, since the IOC just
    # missed -- comes back ok=False with a real id. The first version
    # tested ok and returned here, BEFORE the cancel: every successful
    # placement read as a refusal and left a clip-sized bid on the book
    # with no owner. Found by adversarial review, reproduced through
    # the real adapter, never reached production.
    oid = placed.get("order_id")
    if not oid:
        _copy_stop("rest_place_refused", whale)
        return None
    # PERSIST THE ID BEFORE ANYTHING CAN GO WRONG. This is the line
    # that makes the bid un-orphanable: whatever happens next -- a
    # SIGKILL mid-sleep, a cancel that fails twice -- the row now names
    # the order, never-add sees a 'submitting' row and refuses to stack
    # a second bid on the market, and the reaper can reconcile it
    # against the venue instead of blind-marking it 'error'.
    try:
        # THE RECEIPT TRAVELS WITH THE ID. A row reconciled by the reaper
        # after process death has only what was persisted here; without
        # the placement's raw, ORDER_INTENT_SQL cannot name its side and
        # a reconciled short becomes sideless. Same statement, no extra
        # round trip.
        await pool.execute(
            "UPDATE live_orders SET order_id=$2, "
            "raw=COALESCE(raw, '{}'::jsonb) || $3::jsonb WHERE id=$1",
            row_id, oid,
            json.dumps({**(placed.get("raw") or {}), "lane": "rest"},
                       default=str))
    except Exception as exc:  # noqa: BLE001 — never skip the cancel
        log.error("REST LANE: could not persist order %s on row %s (%s)"
                  " — the venue-side reaper is the only net now",
                  oid, row_id, type(exc).__name__)
    filled = float(placed.get("filled_shares") or 0)
    avg = placed.get("fill_price")
    cancel_ok = False
    # A FULL FILL AT PLACEMENT NEEDS NO WINDOW AND NO CANCEL. Cancelling
    # a done order is a venue error at best; at worst the refusal was
    # read as "unknown" and the lane's target outcome was recorded as
    # nothing (re-review 2026-09-01). Skip straight to the read.
    _done_at_placement = filled >= shares
    try:
        if not _done_at_placement:
            await asyncio.sleep(REST_BID_TTL_S)
    finally:
        # CANCEL FIRST, THEN READ. Reading first opens a window where a
        # fill lands between the read and the cancel and is never
        # recorded. Cancelling first means the final read sees the
        # terminal state. Two attempts; a failure after both is not
        # swallowed -- it turns the outcome UNKNOWN below.
        for _attempt in ((1, 2) if not _done_at_placement else ()):
            try:
                c = await asyncio.to_thread(pmus.cancel_order, oid, us_slug)
                cancel_ok = bool(c.get("ok"))
            except Exception:  # noqa: BLE001
                cancel_ok = False
            if cancel_ok:
                break
        if _done_at_placement:
            cancel_ok = True          # nothing to cancel; not a failure
        if not cancel_ok:
            log.error("REST LANE: cancel FAILED twice for order %s on %s "
                      "— leaving the row 'submitting' for the reaper",
                      oid, us_slug)
    # READ UNTIL TERMINAL, BOUNDED. A cancel is acknowledged before it
    # is applied; the first read after it can still show the order
    # open. Three reads over ~1s is enough for a cancel to land; if it
    # has not, the outcome is unknown and is recorded as such.
    st = None
    for _i in range(3):
        try:
            st = await asyncio.to_thread(pmus.order_status, oid)
        except Exception:  # noqa: BLE001
            st = None
        if _rest_terminal(st):
            break
        await asyncio.sleep(0.3)
    if st:
        filled = max(filled, float(st.get("filled_shares") or 0))
        avg = st.get("avg_px") or avg
    # THE RECEIPT STAYS AT THE TOP LEVEL. ORDER_INTENT_SQL reads the
    # row's side from raw -> response.executions[0].order.intent (or
    # preview.intent); nesting the placement under a key would make a
    # rest fill sideless to mirror_exit, the short restate, the grader
    # and price_fidelity. Lane metadata is added beside it, not around.
    raw = {**(placed.get("raw") or {}), "lane": "rest",
           "ttl_s": REST_BID_TTL_S, "wire": wire, "final": st,
           "cancel_ok": cancel_ok}
    # THE TERMINAL READ IS AUTHORITATIVE. It runs AFTER both cancel
    # attempts, so a terminal state is the venue's last word whatever
    # the cancel said: a bid that filled in full gets its cancel refused
    # (the order is done) and the first version turned that refusal
    # into "unknown" with filled_shares 0 -- on the lane's target
    # outcome. Cancel failure matters only while the read is still
    # open; cancel_ok stays in raw for the audit trail.
    if not _rest_terminal(st):
        # UNKNOWN IS A STATE, NOT A NONE. Returning None here would
        # record the row 'unfilled' with the IOC's id and let the sweep
        # re-attempt it inside two minutes -- a second bid stacked on an
        # order that may still be live. The caller keeps the row
        # 'submitting' with THIS order id instead, which is exactly the
        # shape the reaper reconciles and never-add refuses to stack on.
        _copy_stop("rest_unknown", whale)
        raw["why"] = (f"rest lane: fill unknown for order {oid} on "
                      f"{us_slug} — reconcile against the venue")
        return {"ok": False, "order_id": oid, "status": "rest_unknown",
                "fill_price": None, "filled_shares": 0.0, "raw": raw}
    if filled <= 0:
        _copy_stop("rest_unfilled", whale)
        return None
    _copy_stop("rest_filled", whale)
    # CONTRACT PRICE ON THE FALLBACK, NOT THE OUTCOME PRICE. fill_cash
    # and cost_per_share expect what went on the wire; his_price is in
    # outcome space, and on a BUY_SHORT that mis-denomination is the
    # 3.5x phantom overspend that halts the whole sleeve.
    return {"ok": True, "order_id": oid, "status": "rest_filled",
            "fill_price": float(avg) if avg else float(wire),
            "filled_shares": filled, "raw": raw}


async def _rest_after_ioc(pool, row_id: int, us_slug: str,
                          his_price: float, shares: float,
                          intent: str | None, whale: str,
                          reaction: float | None) -> dict | None:
    """Rest a bid at HIS price for REST_BID_TTL_S after an IOC missed.

    Returns a submit_fok-shaped result on a fill, a "rest_unknown"
    result when the venue's final state could not be established (the
    caller keeps the row 'submitting' for the reaper), or None when
    nothing happened and the IOC outcome should be recorded as before.
    """
    from . import pmus

    global _REST_RESERVED_USD
    if not REST_BID_ENABLED:
        return None
    # FRESH DETECTIONS ONLY. The sweep/reclaim path calls maybe_execute
    # with reaction=None and re-offers every 'unfilled' row every two
    # minutes at a price that is hours old -- resting there would fill
    # the cohort with stale-price fills the experiment cannot separate
    # from its target population. That path is also the one wrapped in
    # a 60s wait_for, i.e. the live canceller. Both problems, one gate.
    if reaction is None:
        _copy_stop("rest_skipped_reclaim", whale)
        return None
    if shares < 1 or not (0 < his_price < 1):
        return None
    # THE PRICE ON THE WIRE, ON THE TICK, NEVER ABOVE HIM (see rest_tick).
    wire = rest_tick(wire_limit(his_price, intent), intent)
    est = float(shares) * float(wire)
    async with _REST_LOCK:
        spent = await _rest_lane_spent(pool)
        if spent + _REST_RESERVED_USD + est > REST_BID_BUDGET_USD:
            _copy_stop("rest_budget_exhausted", whale)
            return None
        _REST_RESERVED_USD += est
    try:
        # SHIELDED. A wait_for timeout or task cancellation in the copy
        # path raises in THIS frame but the cycle runs to completion --
        # placement, cancel and terminal read all happen. If we were
        # cancelled the caller's fill UPDATE will not run; the row is
        # left 'submitting' with its order_id persisted, which is the
        # reaper's job to finish.
        return await asyncio.shield(_rest_cycle(
            pool, pmus, row_id, us_slug, wire, shares, intent, whale,
            his_price))
    finally:
        async with _REST_LOCK:
            _REST_RESERVED_USD = max(0.0, _REST_RESERVED_USD - est)


# ── MIRROR P1, STEP 1: THE GATES THIS LANE SHARES WITH THE MIRROR BOOK ──
#
# Position mirroring (owner order 2026-09-02, "maximum effort and
# certainty") holds ONE standing book per market instead of one row per
# fill, and its admission must read the same levers this lane reads: the
# rolling-loss breaker, the sleeve's day/total room, the four mapping
# gates, the game-date rule. Copying those clauses into a worker would
# hand the mirror a second, drifting copy of each money rule -- the exact
# failure the verified-set unification (test_verified_set: two lists, one
# decision, 2,897 rejections) was built against. So the clauses are
# factored out of maybe_execute HERE and maybe_execute is reduced to
# call-site substitutions; the mirror reads the same functions.
#
# This lane's behaviour is unchanged to the byte: same queries, same
# refusal texts, same log lines. Where this lane's stance on an
# unreadable read differs from the mirror's (the loss breaker fails open
# here and closed there), the helper returns the READING and the caller
# decides -- a helper never picks a stance for a lane it does not know.


async def _loss_breaker_tripped(pool) -> bool | None:
    """The 24h rolling-loss breaker: True when realized copy P&L
    (settled + cashed out, copies only) over the last 24 hours is at or
    past -PMUS_LOSS_BREAKER_USD, False when it is not, None when the
    ledger could not be read.

    None is deliberately not a verdict. maybe_execute keeps the stance
    it has carried since 2026-08-12 -- an unreadable ledger must not be
    the thing that blocks copies, because caps and the venue guards
    still bound every order -- while the mirror refuses increases on
    None: a book that cannot see its losses does not grow. The query
    answers from Postgres so a deploy cannot amnesia it. Logs the
    breaker line when tripped, exactly as the inline check did.
    """
    try:
        lost_24h = float(await pool.fetchval(
            "SELECT COALESCE(sum(pnl), 0) FROM live_orders "
            "WHERE settled_at > now() - interval '24 hours' "
            "AND status IN ('settled', 'cashed_out') "
            "AND COALESCE(whale_username, '') NOT IN "
            "('manual', 'underdog')") or 0)
    except Exception:  # noqa: BLE001 — unreadable is a reading, not a
        return None    # verdict; each lane applies its own stance
    if lost_24h <= -PMUS_LOSS_BREAKER_USD:
        log.warning(
            "LOSS BREAKER: copy sleeve realized %.2f in 24h "
            "(threshold -%.0f) — copying paused until the window "
            "rolls off", lost_24h, PMUS_LOSS_BREAKER_USD)
        return True
    return False


async def _copy_day_room(pool, cfg) -> tuple[float, float]:
    """(day_room, total_room) for the copy sleeve, as maybe_execute has
    sized against it: _caps_room's config-cap rooms, then in penny_trial
    the TRIAL knobs as the authority (owner 2026-08-05: per-trade limits
    only; day and lifetime ceilings are env knobs, default unlimited),
    then the PROBE DAY CAP on top (owner authorization 2026-08-24
    evening: the resume is a bounded proof, so the day's copy spend is
    bounded too -- an unimagined defect costs this much, not an
    open-ended amount; raise it deliberately once real fills verify).

    Deriving spend from the config-cap rooms keeps _caps_room's single
    query. An unreadable ledger RAISES, as _caps_room does: this lane has
    always let that propagate rather than size against a guess, and the
    mirror treats a raise here as no room (no_budget_room).
    """
    day_room, total_room = await _caps_room(pool)
    if COPY_MODE == "penny_trial":
        day_spent = cfg.live_max_daily_usd - day_room
        total_spent = cfg.live_max_total_usd - total_room
        day_room = PENNY_TRIAL_DAILY_USD - day_spent
        total_room = PENNY_TRIAL_TOTAL_USD - total_spent
        if PROBE_DAY_USD > 0:
            day_room = min(day_room, PROBE_DAY_USD - day_spent)
    return day_room, total_room


def _game_too_far_out(slug: str | None) -> bool:
    """Capital turnover (owner, 2026-08-04): a game more than ~a day out
    is deferred -- a small bankroll compounds by settling, not by holding
    Thursday's ticket since Monday. True when the slug carries a
    YYYY-MM-DD date later than tomorrow. An undated or unparseable slug
    is NOT far out (the other gates judge it), exactly as the inline
    rule read it since 2026-08-04.
    """
    import re as _re
    from datetime import date, timedelta

    mdate = _re.search(r"\d{4}-\d{2}-\d{2}", slug or "")
    if mdate:
        try:
            y, mo, d = map(int, mdate.group(0).split("-"))
            if date(y, mo, d) > date.today() + timedelta(days=1):
                return True
        except ValueError:
            pass
    return False


# The refusal texts _mapping_admitted can return start with exactly one
# of these. maybe_execute keys its follow-up on them -- the side-echo
# warning on the first, the shadow-certification echo on the second --
# and gate_edge parses the same texts off the row, so they are part of
# the contract, not decoration.
_MAPPING_REFUSAL_SIDE_ECHO = "side-echo tripped:"
_MAPPING_REFUSAL_QUARANTINE = ("premap-live:", "quarantined:")


async def _mapping_admitted(pool, username: str | None,
                            mapping_src: str | None,
                            q_slug: str | None) -> tuple[bool, str | None]:
    """The four admission gates a resolved mapping must clear before a
    dollar rides it, in the order maybe_execute has run them since the
    2026-08-23/24 leak hunts: the side-echo circuit, the verified set,
    the profitability hold, the mapping quarantine with its premap-live
    resume lane. Returns (True, None) or (False, refusal text). The text
    is what maybe_execute writes onto the row and what gate_edge parses,
    so every one is byte-identical to the inline version it replaces and
    starts with one of the prefixes declared above.

    Every read fails safe: an unreadable side-echo state is tripped, an
    unreadable quarantine switch stays on, an unreadable premap-live
    switch refuses. Nothing here writes; the caller records the refusal.
    """
    username = (username or "").lower()
    q_slug = str(q_slug or "").lower()
    # SIDE-ECHO CIRCUIT (leak-hunt find 2026-08-24): the echo's
    # auto-requarantine writes the DB switches, but the env overrides
    # (LIVE_PREMAP=on / LIVE_MAPPING_QUARANTINE=off) short-circuit
    # BEFORE those DB reads — env-armed operation would sail past a
    # confirmed wrong-side mismatch. This circuit deliberately has NO
    # env override: tripped means every copy mapping refuses until an
    # admin explicitly resets it (POST /api/admin/side-echo-reset).
    # Unreadable state fails safe.
    try:
        _tv = await pool.fetchval(
            "SELECT value FROM ingestion_state WHERE key=$1",
            "side_echo_tripped")
        _tripped = (bool(json.loads(_tv) if isinstance(_tv, str)
                         else _tv) if _tv is not None else False)
    except Exception:  # noqa: BLE001 — fail safe: refuse
        _tripped = True
    if _tripped:
        return False, ("side-echo tripped: confirmed wrong-side evidence — "
                       "copying halted pending admin review "
                       f"(slug={q_slug[:120]})")
    # PROFITABILITY HOLD (leak-hunt find 2026-08-24): swisstony's
    # exclusion previously lived only in the premap-live allowlist,
    # which is consulted ONLY while the quarantine is armed — lifting
    # the quarantine (a mapping-fidelity action) would silently re-arm
    # his $300 clip with no profitability decision made. The hold is
    # its own gate, independent of quarantine state: LIVE_HOLD_WHALES
    # refuses with an audit reason until a whale's paper cohort at the
    # new sub-second detection certifies positive. It defaulted to
    # swisstony until 2026-08-24 evening, when TRUEEDGE-FAST graded him
    # positive on detections inside 5s and the owner order lifted it;
    # the default is now EMPTY, and the env re-arms a hold on any whale
    # without a deploy. A held row keeps its mapping — fidelity samples
    # continue.
    # VERIFIED-ONLY, INDEPENDENT OF QUARANTINE (leak-hunt round 2): the
    # allowlist below lived INSIDE the `if _q_on` branch, so lifting
    # the quarantine — a mapping-fidelity action — silently dropped the
    # profitability allowlist with it and opened the lane to every
    # non-cut whale. Only whales the TRUEEDGE table verified profitable
    # may spend, whatever the quarantine says. LIVE_VERIFIED_WHALES
    # widens it deliberately; empty disables the gate for a full
    # resume. The verified set is exactly TRUEEDGE cf_total > 0:
    # homerunhazard (+26,076), 0x076 (+6,189), swisstony (+11,895).
    # swisstony is verified but HELD below, pending his paper cohort at
    # the new sub-second detection — the two gates answer different
    # questions and must stay separate.
    # UNREADABLE ROSTER IS CLOSED, NOT OPEN (disk-full incident
    # 2026-09-05). The empty-set-disables-the-gate rule below is for a
    # DELIBERATE empty LIVE_VERIFIED_WHALES (a full resume); a roster we
    # could not read is not that, and _whale_set answers it with the
    # same empty set. Asked here first, with the same refusal prefix,
    # so gate_edge files it under the verified gate.
    if overrides_unreadable():
        return False, ("not verified-profitable: the stored roster is "
                       "UNREADABLE and the gate is CLOSED until a read "
                       f"succeeds (slug={q_slug[:100]})")
    _verified = _whale_set("LIVE_VERIFIED_WHALES")
    if _verified and username not in _verified:
        return False, ("not verified-profitable: only whales certified by "
                       "the TRUEEDGE counterfactual may spend "
                       f"(slug={q_slug[:100]})")
    # HOLD LIFTED for swisstony (owner order 2026-08-24 evening: "make
    # sure we are profitable copying SwissTony"). The hold's stated
    # condition was his paper cohort grading positive at the NEW
    # detection latency. Measured, on detections inside 5 seconds
    # (TRUEEDGE-FAST):
    #   cf_total    +8,874.90   his edge on the fast book
    #   paper_actual +6,864.52  what OUR fill achieves — POSITIVE
    #   lat_cost     1,983.71   vs 15,063 blended
    # The -604.88 that held him was an artifact of averaging in months
    # of minutes-late polling; at chain speed we capture 77% of his
    # edge. He now trades under the 15s staleness cap, so a signal we
    # did NOT catch fast is still refused — the condition that makes
    # him profitable is enforced, not hoped for. LIVE_HOLD_WHALES
    # re-arms a hold without a deploy.
    _held = {w.strip() for w in
             os.getenv("LIVE_HOLD_WHALES", "")
             .lower().split(",") if w.strip()}
    if username in _held:
        return False, ("hold: pending paper certification at the new "
                       "detection latency (TRUEEDGE 2026-08-24) "
                       f"(slug={q_slug[:120]})")
    # MAPPING QUARANTINE (owner emergency 2026-08-23): the venue ledger
    # proved a large share of two-outcome copies were held on the WRONG
    # SIDE while the old settlement sweep graded them by the whale's
    # result. Until each path is re-verified against venue truth, only
    # the deterministic US slug-grammar paths may place money: fuzzy-
    # resolved mappings and the aec- (tennis side-slug) class are
    # refused, with the mapping kept on the row for the postmortem.
    # LIVE_MAPPING_QUARANTINE=off lifts.
    # Resume is a DB switch (POST /api/admin/quarantine/off) so the
    # owner's go is one admin call; the env var stays as a hard
    # override in either direction. Default is ON — an unreadable state
    # key must fail safe, not fail open.
    _q_env = os.getenv("LIVE_MAPPING_QUARANTINE", "")
    if _q_env == "off":
        _q_on = False
    elif _q_env == "on":
        _q_on = True
    else:
        _q_on = True
        try:
            _q_val = await pool.fetchval(
                "SELECT value FROM ingestion_state WHERE key=$1",
                "mapping_quarantine")
            if _q_val is not None:
                _q_on = bool(json.loads(_q_val)
                             if isinstance(_q_val, str) else _q_val)
        except Exception:  # noqa: BLE001 — fail safe: stay on
            _q_on = True
    # 2026-08-24 05:00 ET: the venue-certified restatement shows EVERY
    # sleeve August-negative at our fills (RN1 -8.6k at 39% wins; the
    # restated ledger now matches the account day-by-day). While the
    # switch is ON, ALL copy mappings refuse — not just the fuzzy/aec
    # classes — pending the owner's morning review. Same switch lifts
    # it.
    # RESUME LEVER (owner order 2026-08-24): while the total quarantine
    # holds, ONLY premap-resolved mappings may trade, and only once the
    # owner flips premap_live (DB switch via POST /api/admin/premap-
    # live/on; env LIVE_PREMAP overrides both ways). Everything else
    # stays refused-but-recorded.
    # PREMAP-LIVE ALLOWLIST (owner order 2026-08-24): the resume lane
    # admits ONLY the whales the TRUEEDGE table verified profitable at
    # their own prices AND at our measured latency. swisstony joins via
    # env (no code change) the moment his paper cohort at the new
    # sub-second detection grades positive; everyone else stays
    # refused-but-recorded.
    _allowed = _whale_set("LIVE_PREMAP_WHALES")
    _premap_ok = False
    if _q_on and mapping_src in QUARANTINE_RESUME_SRC \
            and username in _allowed:
        _pl_env = os.getenv("LIVE_PREMAP", "")
        if _pl_env == "on":
            _premap_ok = True
        elif _pl_env != "off":
            try:
                _pl = await pool.fetchval(
                    "SELECT value FROM ingestion_state WHERE key=$1",
                    "premap_live")
                _premap_ok = (bool(json.loads(_pl)
                              if isinstance(_pl, str) else _pl)
                              if _pl is not None else False)
            except Exception:  # noqa: BLE001 — fail safe: refuse
                _premap_ok = False
    if _q_on and not _premap_ok:
        if mapping_src in QUARANTINE_RESUME_SRC \
                and username not in _allowed:
            return False, ("premap-live: whale not in the "
                           "verified-profitable set (TRUEEDGE "
                           "2026-08-24) "
                           f"(src={mapping_src}, "
                           f"slug={q_slug[:120]})")
        return False, ("quarantined: mapping class unverified "
                       "after wrong-side incident 2026-08-23 "
                       f"(src={mapping_src}, "
                       f"slug={q_slug[:120]})")
    return True, None


async def _protected_order_ids(pool) -> set[str] | None:
    """Order ids no reaper may cancel or adopt: the desk's (order_id of
    manual rows -- the exclusion _reap_stale_resting_bids has carried
    since re-review 2026-09-01, when an account-wide sweep cancelled the
    owner's app bids) plus every mirror order's (mirror_orders, migration
    047: a table the reapers never read is a table they cannot cancel or
    adopt from, and this set is the belt for the one pass that asks the
    VENUE instead of the ledger).

    None when a read FAILS. Without the exclusion list a sweep cannot
    tell a desk order or a mirror rest from an orphaned bid, so it must
    cancel neither -- the reaper's existing stance, kept. A mirror_orders
    table that does not EXIST (047 not yet applied; start.sh serves on
    even when migrate fails) is not a failed read: a table that does not
    exist holds no order, so the mirror set is empty and every reaper
    runs exactly today's pass (final review of the chain, 2026-09-02:
    reading absence as None had turned off all three nets -- the venue
    reaper, the lost-bid adopter and the rest lane's own lost-response
    search -- on a production state the migration runner permits).
    Wired in P1 step 8; the reaper's former inline query is byte-identical
    to the first read here.
    """
    try:
        manual = {str(r["order_id"]) for r in await pool.fetch(
            "SELECT order_id FROM live_orders WHERE order_id IS NOT "
            "NULL AND COALESCE(whale_username,'') = 'manual'")}
    except Exception:  # noqa: BLE001 — unreadable: protect nothing by
        # cancelling nothing (the callers skip their pass)
        return None
    try:
        mirror = {str(r["order_id"]) for r in await pool.fetch(
            "SELECT order_id FROM mirror_orders WHERE order_id IS NOT NULL")}
    except Exception as exc:  # noqa: BLE001
        if _relation_missing(exc, "mirror_orders"):
            return manual
        return None
    return manual | mirror


def _relation_missing(exc: BaseException, relation: str) -> bool:
    """asyncpg's UndefinedTableError, or any driver's text for it."""
    if type(exc).__name__ == "UndefinedTableError":
        return True
    msg = str(exc)
    return relation in msg and "does not exist" in msg


def mirror_allowlist() -> set[str]:
    """The whales the mirror may hold: env PMUS_MIRROR_WHALES intersected
    with the shadow's MIRROR_WHALES (workers.mirror_shadow.mirror_whales,
    default rn1). The env default is EMPTY -- nobody -- which is why
    this does not go through _whale_set: that helper's unset default is
    the verified roster, and a deploy that sets PMUS_MIRROR=on without
    naming a whale must change nothing for the per-fill lane.
    Environment only; the DB narrows the mirror's INCREASES inside the
    reconciler, never who is handed off.
    """
    raw = os.environ.get("PMUS_MIRROR_WHALES", "")
    ours = {w.strip().lower() for w in raw.split(",") if w.strip()}
    if not ours:
        return set()
    # imported here, not at module top: the shadow worker imports this
    # module's constants inside its own functions for the same reason
    from .workers import mirror_shadow as _ms
    return ours & set(_ms.mirror_whales())


def mirror_mode(username: str | None) -> bool:
    """WHOLE-WHALE hand-off (P1 §3): True when this whale is mirrored,
    so the per-fill lane must not copy his entries on ANY market -- a
    per-market rule would race on the market the mirror is about to
    open and leave two regimes exiting one whale. Reads the ENVIRONMENT
    ONLY: PMUS_MIRROR in ('on', 'exits') and the whale in
    mirror_allowlist(). Nothing here can be unreadable, so nothing here
    can fail open. Whether the mirror INCREASES is the reconciler's
    question (its DB switch); whether the per-fill lane stands aside is
    answered from the deploy, here. Consulted by maybe_execute exactly
    once, directly below the exit dispatch (P1 step 7).
    """
    mode = os.environ.get("PMUS_MIRROR", "off").strip().lower()
    if mode not in ("on", "exits"):
        return False
    w = (username or "").strip().lower()
    return bool(w) and w in mirror_allowlist()


def _mirror_notify(condition_id: str | None) -> None:
    """Wake the mirror reconciler for this market, when the worker is
    there to wake. maybe_execute's hand-off gate calls this for a
    mirrored whale's fill (P1 step 7, spec 3.1) because his fill is the
    reconciler's TRIGGER and his PRICE (addendum 1: never its position),
    so the worker should read the market now rather than on its next
    poll. A courtesy, never a condition. The import is lazy because
    workers.mirror_live is step 9's module and this gate ships first;
    and nothing here may raise, because the refusal that follows this
    call is the money decision -- a wake that cannot be delivered must
    not become an exception that execute_copy logs and the sweep
    retries every pass. Missing module: the reconciler polls. A module
    that will not import, or a wake that raises: named in the log, and
    the reconciler polls.
    """
    try:
        # by FULL NAME (final review of the chain, 2026-09-02): the
        # from-import form raised a plain ImportError for a genuinely
        # absent submodule, so "step 9 has not landed" logged a traceback
        # on every fill of a mirrored whale; import_module raises
        # ModuleNotFoundError carrying the worker's own name for that case
        import importlib
        _ml = importlib.import_module("sportsassets.workers.mirror_live")
    except ModuleNotFoundError as exc:
        # ABSENCE IS NOT BREAKAGE: only the worker module itself being
        # missing is the quiet case; a dependency it cannot import raises
        # the same class with another name and is a bad deploy, named
        # below like any other import failure.
        if exc.name == "sportsassets.workers.mirror_live":
            return None
        log.warning("mirror worker would not import; wake for %s dropped",
                    condition_id, exc_info=True)
        return None
    except Exception:  # noqa: BLE001 — a broken worker module is its own defect, never a copy's
        log.warning("mirror worker would not import; wake for %s dropped",
                    condition_id, exc_info=True)
        return None
    try:
        _ml.notify(condition_id)
    except Exception:  # noqa: BLE001 — a lost wake is a late tick, never a copy
        log.warning("mirror wake failed for %s", condition_id, exc_info=True)
    return None


async def _mirror_owns_asset(pool, asset: str | None) -> bool:
    """True when an OPEN mirror book (mirror_books.state <> 'closed')
    holds this token on either side -- the long token it buys or the
    other outcome. mirror_exit's position lane asks this before it
    declares mx_no_position_of_ours (P1 step 7), so a book is never sold
    by the per-fill exit path whatever any switch says. Unreadable is
    False by design: the caller then falls to mx_no_position_of_ours,
    a settled reason that sells nothing either -- both answers are "not
    yours to sell" -- and the mirror confirms its own vanishes from the
    same raw snapshot. Consulted by mirror_exit just before
    mx_no_position_of_ours (P1 step 7).
    """
    try:
        return bool(await pool.fetchval(
            "SELECT EXISTS (SELECT 1 FROM mirror_books "
            "WHERE state <> 'closed' "
            "AND (long_asset = $1 OR other_asset = $1))",
            str(asset or "")))
    except Exception:  # noqa: BLE001 — unreadable: not ours to sell
        return False


async def maybe_execute(payload: dict, reaction: float | None) -> None:
    """Called on every fresh detection (after the paper trade). All guards
    re-checked here; failure of any guard is a silent no-op or logged skip."""
    if COPY_MODE == "off":
        return _copy_stop("mode_off")
    cfg = settings()
    # MASTER KILL SWITCH, AT THE COMMON GATE (leak-hunt round 3,
    # 2026-08-24): copy_probe_enabled was checked only in execute_copy,
    # the FRESH-detection entry point, so copy_sweep's reclaim path
    # called maybe_execute directly and kept placing real orders after
    # an operator disabled copying. Every copy crosses this function;
    # the switch belongs here.
    if not cfg.copy_probe_enabled:
        return _copy_stop("probe_disabled")
    # EMERGENCY HALT rides the same common gate as the master kill, for
    # the same reason: the reclaim path reaches maybe_execute directly.
    # Fail-closed by default — see COPY_HALT_REASON.
    if copy_halted():
        log.warning("LIVE refused: %s", COPY_HALT_REASON)
        return _copy_stop("halted")
    venue = active_venue()
    if venue is None:
        return _copy_stop("no_venue")
    # roster override refresh (owner order 2026-08-29): the DB-stored
    # verified set must beat a stale env before ANY entry gate below.
    # No pool is a failed read (2026-09-05): CLOSED, not the fall-through.
    try:
        _pool_for_roster = await get_pool()
    except Exception as exc:  # noqa: BLE001
        close_overrides(f"pool: {type(exc).__name__}: {str(exc)[:120]}")
    else:
        try:
            await refresh_whale_overrides(_pool_for_roster)
        except Exception:  # noqa: BLE001 — the refresh never raises; belt
            pass
    username = (payload.get("whale_username") or "").lower()
    his_notional = float(payload.get("notional") or 0)
    his_price = float(payload.get("price") or 0)
    if his_notional <= 0 or not (0 < his_price < 1):
        return _copy_stop("bad_price_or_notional", username)

    pool = await get_pool()
    # THE COMPLEMENT-BUY EXIT (2026-08-25, owner-confirmed against
    # Polymarket's global microdata).
    #
    # On a signed-net venue, buying the other leg of a market you
    # already hold retires the holding share for share. These whales
    # exit that way exclusively — 860,669 buys, zero sells — so every
    # exit arrives labelled BUY. Before this, the label was taken at
    # face value and the exit was copied as a fresh ENTRY on the side he
    # was LEAVING. That is not a missed copy, it is a doubled one: his
    # leg we never close, plus the opposite leg at a price summing to
    # ~1.00 with his entry. Every exit he made cost us twice.
    #
    # It sits HERE, not in execute_copy, for two reasons that are not
    # style. First, `pool` already exists at this line, so classifying
    # adds no connection acquisition to the copy path — the first
    # version called get_pool() in execute_copy and added 105 SECONDS of
    # connect-retry to a path that had never needed a pool, which the
    # reaction-stamp test caught by reading 135s where it expected 30s.
    # Second, this is after the staleness gate and inside the
    # semaphore, so a classified exit is measured with the same latency
    # accounting as an entry instead of quietly preceding it.
    #
    # classify_exit refuses unless the market has exactly ONE sibling
    # token AND he demonstrably holds it. Everything else falls through
    # to the entry path untouched.
    #
    # SCOPED TO THE UNION OF BOTH ROSTERS, AND THE UNION IS LOAD-BEARING.
    #
    # This first shipped scoped to exitable_whales() alone, which made
    # the new code classify LESS than the old code in one case, and
    # adversarial review caught it before it did damage. Before the
    # reorder, classify_exit ran for every whale that passed the entry
    # roster; a whale in source_whales() but NOT exitable — which is
    # what an AI_TRADER_SOURCE env addition on Render produces, the
    # documented no-deploy way to arm a whale — would have had his
    # complement buy skipped entirely and copied as a fresh ENTRY on the
    # leg he was LEAVING. That is the doubling classify_exit exists to
    # stop: "Every exit he made cost us twice." Silent, and multiplied
    # by the copy_sweep reclaim lane's seven-day replay.
    #
    # The union is a strict superset of BOTH the old scope and the
    # intended new one, so no whale can lose classification he had. It
    # costs nobody a query he was not already paying for: source whales
    # were classified before, and exitable-but-unrostered whales are the
    # whole point of the reorder. A source-only whale routed into
    # mirror_exit is still refused there by mx_whale_not_verified
    # (:887-888) and still returns was_an_exit_pending, which suppresses
    # the wrong-leg entry exactly as it does today.
    #
    # An exit always arrives labelled BUY, so a non-BUY payload can
    # never be one and is not worth a lookup either.
    _exit = None
    if payload.get("side") == "BUY" and username in (
            exitable_whales() | cfg.source_whales()):
        _exit = await classify_exit(
            pool, str(payload.get("asset") or ""),
            (payload.get("whale_username") or "").lower(),
            payload.get("size") or 0,
            payload.get("id"))
    if _exit:
        log.warning("EXIT-CLASSIFIED %s bought %s to close %.1f%% of %s "
                    "— mirroring the exit, not the entry",
                    payload.get("whale_username"),
                    _exit["exit_via_asset"],
                    _exit["closed_frac"] * 100, _exit["asset"])
        # THE REASON WAS BEING THROWN AWAY ON THE LANE THAT CARRIES 98%
        # OF THE EXITS. mirror_exit's whole reason-code contract exists
        # so a refusal can be re-offered (execute_copy:106), and
        # whale_exits:814 is its only reader — but that is the POSITION
        # lane, which ran 23 attempts against this lane's 1,572.
        #
        # Counted, not retried, and the distinction is deliberate. This
        # lane is TRADE-driven: the next trim from the same whale calls
        # it again, and since the fraction above is now cumulative that
        # retry arrives with a LARGER number rather than the same one.
        # The ratchet the position lane gets from pinning, this lane
        # gets from the whale's own next trade.
        #
        # What that does NOT cover is a refusal on liquidity —
        # mx_no_bid_for_partial, mx_venue_unfilled — where the whale may
        # never trade this market again. Those need a real retry record.
        # Counting them here is what makes that gap measurable instead
        # of invisible, which is how it survived until now.
        _mx = await mirror_exit({**payload, **_exit, "side": "SELL"})
        if _mx and _mx in EXIT_PENDING_REASONS and _mx != "mx_SOLD":
            _exit_stop("tradelane_dropped_" + str(_mx),
                       whale=payload.get("whale_username"),
                       asset=_exit.get("asset"))
        return _copy_stop("was_an_exit_pending", username)
    # ── THE MIRROR HAND-OFF (mirror P1 step 7, spec 3.1) ─────────────
    #
    # Owner order 2026-09-02, "go for it, let's get this working": a
    # whale in mirror mode is held as ONE standing book per market by
    # the reconciler (workers.mirror_live), so this lane must not copy
    # his entries on ANY market. Whole-whale, not per-market: the panel
    # review weighed the per-market rule and refused it, because it
    # races on the market the mirror is about to open and leaves two
    # regimes exiting one whale. The predicate reads the deploy only
    # (mirror_mode: PMUS_MIRROR and the allowlist), so nothing here can
    # fail open on a read.
    #
    # BELOW the exit dispatch: his pre-mirror per-fill rows (lane NULL,
    # 'ioc', 'rest') are still classified and sold by classify_exit ->
    # mirror_exit exactly as before, and a book's own token is answered
    # there by name (mx_mirror_owns_market). ABOVE every entry gate, the
    # sizing, the INSERT and both submit sites, so no per-fill dollar
    # leaves for a mirrored whale from either caller -- the fresh path
    # (execute_copy) or copy_sweep's reclaim call with reaction None.
    # The wake is a courtesy to the reconciler, never a condition of
    # the refusal: with the worker absent it is a no-op.
    if mirror_mode(username):
        _mirror_notify(payload.get("condition_id"))
        return _copy_stop("mirror_mode", username)
    # ── THE ENTRY ROSTER, BELOW THE EXIT (2026-09-01) ───────────────
    #
    # These two gates used to sit ~100 lines ABOVE classify_exit, which
    # broke the invariant the 95% gate below states in words:
    #
    #   "a guard placed any higher would silently kill exit detection
    #    for every whale it refuses"
    #
    # At this venue an exit arrives labelled BUY, so a whale refused by
    # an ENTRY roster was never even classified — and every position we
    # already held from him was stranded to resolution. exitable_whales()
    # is deliberately WIDER than the verified set for exactly that
    # reason (see its docstring, and test_cut_whale_exit), and
    # mirror_exit honours the wide set — but the detector that feeds it,
    # the lane carrying 1,572 attempts against the position lane's 23,
    # never reached it. 0x2c33 is in that state today by construction:
    # asserted exitable, with his exit detection dead.
    #
    # It is not only the cut list. cfg.source_whales() reads
    # AI_TRADER_SOURCE, which has NO DB override — POST
    # /api/admin/verified-whales writes live_verified_whales and cannot
    # touch it — so a stale Render env would kill a whale's exit
    # detection with no admin lever, which is the same failure mode that
    # cost homerunhazard 724 rejections at $0 deployed in August.
    #
    # Moving them below costs nothing: classification above is scoped to
    # exitable_whales(), the exact set mirror_exit would act on, so no
    # whale we would never sell for does any extra database work.
    if payload.get("side") != "BUY" or username not in cfg.source_whales():
        return _copy_stop("not_buy_or_off_roster", username)
    # TRUEEDGE CUT (owner order 2026-08-24): a whale whose full detected
    # book is negative at his OWN prices is not copyable at any speed.
    # First of three independent blocks (this gate, the 0.00 clip, the
    # premap-live allowlist) — any one of them alone stops the dollars.
    if username in COPY_CUT_WHALES:
        return _copy_stop("whale_cut", username)
    # OVERSPEND BREAKER (2026-08-25). Tripped by the post-fill detector
    # the first time the venue charges more than we authorized. Placed
    # at the first point `pool` exists, still ahead of sizing, pricing
    # and the live_orders INSERT — nothing about the order has been
    # decided when it refuses.
    #
    # COPY SLEEVE ONLY, deliberately. It is NOT wired into the manual
    # desk: the desk rides its own budget and is the owner's directed
    # trading, and I already broke it once today with a fail-closed
    # check that was right in principle and in the wrong function.
    #
    # NOT env-clearable either: LIVE_COPY_HALT is the owner's lever,
    # this one is evidence of a live money defect and takes a
    # deliberate admin clear after someone has read the receipts.
    _osh = await overspend_halt(pool)
    if _osh:
        log.warning("LIVE refused: overspend breaker tripped (%s)",
                    str(_osh)[:200])
        return _copy_stop("exit_handled", username)
    if await _is_paused(pool):
        return _copy_stop("sleeve_paused", username)
    # ── THE 95% GATE (owner requirement 2026-08-30) ─────────────────
    #
    # "at least 95% statistically proven profitable... always ensure
    # mathematical and statistical profit." Until this line existed the
    # bar was a report: on 2026-08-30, five of the six graded books
    # were funded with real money while their intervals contained zero.
    #
    # PLACEMENT IS LOAD-BEARING. classify_exit (:4468) and mirror_exit
    # (:4479) are ABOVE this line, so a refused whale can still be
    # SOLD — at this venue an exit arrives labelled BUY, and a guard
    # placed any higher would silently kill exit detection for every
    # whale it refuses. Sizing (:4642), the live_orders INSERT (:4758)
    # and both submit sites are BELOW, so this dominates every dollar.
    #
    # Fails closed on every path, including a database it cannot read.
    # That means a dead analytics worker stops copy buying entirely —
    # which is the correct direction, and is visible on the probe's
    # EDGEGATE line rather than silent.
    from . import edge_gate
    try:
        await edge_gate.refresh(pool)
    except Exception:  # noqa: BLE001 — verdict() refuses on a stale read
        pass
    _eg_ok, _eg_why = edge_gate.verdict(username)
    if not _eg_ok:
        _gate_census(_eg_why)
        log.info("LIVE refused: %s not funded (%s)", username, _eg_why)
        return _copy_stop("edge_gate", username)
    # Cell-level copy policy (owner directive 2026-08-06): each source
    # whale is copied ONLY in its statistically proven sport x market-type
    # x entry-band cells, derived from fill-level forensic data. Fails
    # closed on anything unrecognized. The market identity must be
    # RESOLVED before it is judged: fresh Path-A detections reach here
    # before enrichment (the payload carries no slug — it lands in the
    # trades row afterwards), and gating on the raw payload fed the
    # fail-closed parser an empty string, silently dropping every fresh
    # copy for ~3h on 2026-08-06 evening.
    from .copy_sports import copy_allowed, copy_verdict
    ctx = await _market_context(pool, payload)
    for k, v in ctx.items():
        if v and not payload.get(k):
            payload[k] = v
    # NAME THE CLAUSE, NOT THE FUNCTION (2026-08-31). The first
    # pre-INSERT census reported `cell_gate|rn1: 198` — the largest
    # genuine block on the roster's only whale, since his other 530
    # refusals are exits being correctly classified as exits. And it
    # was not actionable: this gate has six independent ways to say no
    # and rn1 is in UNRESTRICTED, which rules four of them out.
    # Reading the constants suggested props; suggested is not measured.
    #
    # copy_allowed REMAINS THE DECISION. The first version of this
    # asked copy_verdict instead, which moved the decision onto a new
    # seam and broke eleven test stubs across three files that patch
    # copy_allowed to bypass this gate. That blast radius was the
    # signal: only the REASON was needed, not a new decider. So the
    # verdict is consulted ONLY on the refusal path, where it cannot
    # change an outcome — the property worth having anyway.
    _slug = payload.get("market_slug") or payload.get("event_slug") or ""
    if not copy_allowed(username, _slug, price=payload.get("price")):
        # `or "unnamed"`: a stub can force the boolean False while the
        # verdict still says allowed. Recording "cell_gate_None" would
        # be a bucket named after a bug in the diagnostic.
        _cv = copy_verdict(username, _slug, payload.get("price")) \
            or "unnamed"
        return _copy_stop("cell_gate_" + _cv, username)
    # Venue split (owner directive 2026-08-07: both venues firing near
    # evenly, when pricing makes sense): Kalshi holds FIRST CLAIM on a
    # deterministic half of fresh flow in the sports it lists. The
    # engine's sweep prices those within ~2 minutes under its own gates
    # (his+2% fee-loaded, fee floors, collapse guard); whatever Kalshi
    # cannot price is reclaimed by the hourly sweep here — which is why
    # sweep-recovery rows never defer: they ARE the reclaim.
    # OWNER 2026-08-17 late night, firm rule: Kalshi trades only when
    # its price PROVABLY beats Polymarket's. Price can't be judged
    # here (only the engine reads Kalshi books), so PMUS executes
    # EVERY copy immediately and the engine's sweep takes a position
    # only when its fee-loaded price strictly beats the visible PMUS
    # ask — the claims system arbitrates so exactly one venue fills.
    # The old deterministic hash split is off by default;
    # PMUS_ALL_COPIES=0 restores it (tennis stays out of the set
    # regardless — never traded on Kalshi).
    from .copy_sports import KALSHI_FIRST_SPORTS, kalshi_first, sport_of
    if (os.environ.get("PMUS_ALL_COPIES", "1") == "0"
            and not payload.get("sweep_recovery")
            and kalshi_first(str(payload.get("asset") or ""))
            and sport_of(payload.get("market_slug")
                         or payload.get("event_slug") or "")
            in KALSHI_FIRST_SPORTS):
        return _copy_stop("deferred_to_kalshi", username)
    # Capital turnover (owner, 2026-08-04): fresh detections on games more
    # than ~a day out are DEFERRED — no audit row is written, so the 6h
    # sweep re-candidates them once the game is inside the window. A small
    # bankroll compounds by settling, not by holding Thursday's ticket
    # since Monday. The calendar rule lives in _game_too_far_out (mirror
    # P1, step 1) so a mirror book's admission reads the same window.
    mslug = payload.get("market_slug") or payload.get("event_slug") or ""
    if _game_too_far_out(mslug):
        return _copy_stop("game_too_far_out", username)
    # ONE copy per proposition, no matter how many times the source adds
    # (owner: "cardinals moneyline 10x -> copied once"). In-flight and
    # filled rows retire the asset; rejected/unfilled ones stay retryable
    # because they spent nothing. A partial unique index (migration 011)
    # enforces this at the database, so the check here is just the cheap
    # fast path.
    taken = await pool.fetchval(
        "SELECT 1 FROM live_orders WHERE asset = $1 "
        "AND status IN ('submitting','filled','settled') "
        "AND COALESCE(whale_username, '') NOT IN ('manual','underdog') "
        "LIMIT 1",
        str(payload["asset"]))
    if taken and ADDS_ENABLED and reaction is not None:
        # AN ADD CANDIDATE (owner order 2026-09-02): his fresh buy on an
        # outcome HIS filled row already holds goes on to the never-add
        # referee, which decides with the whole row (legs taken, trade
        # already merged, same asset) and merges the fill into that
        # row. Every other holder of the asset -- another whale's row,
        # a row in flight (the ORDER BY puts it first, so a leg in
        # flight refuses a second one here), a settled one -- retires
        # the asset as before; so does a sweep re-offer (reaction None).
        # ONE CLOCK WITH THE NEVER-ADD REFEREE (review round two): that
        # referee judges rows placed within 48 hours, so a holder older
        # than that must retire the asset here exactly as before -- else
        # the candidate would run as a fresh copy with no add declared.
        # A MIRROR BOOK IS NEVER HIS ADD HOLDER (mirror P1, step 7; owner
        # order 2026-09-02): the book's standing row is 'filled' for its
        # whale on the asset, and after a switch-off his fresh BUY would
        # read as an add to it and be merged into the book. The asset
        # stays taken here (the first belt; the never-add referee below
        # is the second).
        holder = await pool.fetchrow(
            "SELECT status, lower(COALESCE(whale_username, '')) AS whale, "
            "       (placed_at > now() - interval '48 hours') AS recent "
            "FROM live_orders WHERE asset = $1 "
            "AND status IN ('submitting','filled','settled') "
            "AND COALESCE(whale_username, '') NOT IN ('manual','underdog') "
            "AND COALESCE(lane,'') <> 'mirror' "
            "ORDER BY (status = 'submitting') DESC LIMIT 1 /* add-holder */",
            str(payload["asset"]))
        if (holder is not None and _row_get(holder, "status") == "filled"
                and _row_get(holder, "whale") == username
                and bool(_row_get(holder, "recent"))):
            taken = False
    if not taken:
        # Cross-venue: a position the engine already copied on Kalshi is
        # just as taken (one copy per position ACROSS venues). Guarded so
        # a not-yet-migrated table degrades to the live_orders check
        # alone — today's protection — instead of failing the copy.
        try:
            taken = await pool.fetchval(
                "SELECT 1 FROM kalshi_claims WHERE asset = $1 LIMIT 1",
                str(payload["asset"]))
        except Exception:  # noqa: BLE001
            taken = None
    if taken:
        return _copy_stop("already_taken", username)
    # The rolling-loss breaker gates every copy BEFORE any row is
    # written: realized copy P&L (settled + cashed out, copies only)
    # over the last 24h at or past -$1500 pauses the sleeve. The
    # query answers from Postgres so a deploy cannot amnesia it.
    # The reading is _loss_breaker_tripped (mirror P1, step 1), which
    # answers None for an unreadable ledger. THIS lane keeps its stance:
    # an unreadable ledger must not be the thing that blocks copies --
    # it reads as 0.0 realized, judged against the same threshold; caps
    # below and the venue guards still bound every order. The mirror
    # refuses on None instead; the helper decides for neither.
    _lb = await _loss_breaker_tripped(pool)
    if _lb is None:
        _lb = 0.0 <= -PMUS_LOSS_BREAKER_USD
    if _lb:
        return _copy_stop("loss_breaker", username)
    # Day and total room with the penny_trial knobs and the probe day
    # cap applied, from _copy_day_room (mirror P1, step 1): a mirror
    # increase consumes the same sleeve room this lane does.
    day_room, total_room = await _copy_day_room(pool, cfg)
    if day_room <= 0 or total_room <= 1:
        log.warning("live caps exhausted (day room %.2f, total room %.2f) — skipping",
                    day_room, total_room)
        return _copy_stop("no_budget_room", username)

    if COPY_MODE == "penny_trial":
        import math

        # SAME-OR-BETTER (owner order 2026-08-12, superseding the
        # 2026-08-04 his+2% tolerance): "every trade... copied as long
        # as the price available at the time of execution is the same
        # or better." The FOK limit is HIS price floored to the venue
        # tick — the order fills only at his price or cheaper, never
        # worse. A book that ran past him is a skipped copy, not a
        # chased one. RN1 carries a bounded +2c capture tolerance
        # (owner mandate 2026-08-20 — see copy_limit_price).
        limit = copy_limit_price(payload.get("whale_username"), his_price,
                                 fresh=reaction is not None)
        if limit <= 0:
            return _copy_stop("no_limit_price", username)
        per = await volume_normalized_clip(
            pool, payload.get("whale_username"),
            payload.get("market_slug") or payload.get("event_slug") or "")
        # PROPORTIONAL, IN THE PATH THAT ACTUALLY RUNS (2026-08-25).
        #
        # I put the mirror clamp in plan_order and called the switch
        # done. COPY_MODE is hardcoded "penny_trial", so plan_order is
        # NEVER REACHED in production — this branch is the live one, and
        # volume_normalized_clip scales the flat clip by our own recent
        # ACTIVITY without ever looking at the whale's size. That is why
        # every fill came back at ~$250 regardless of whether he staked
        # $3.46 or $2,907, and my "proportional switch" would have
        # changed nothing at all.
        #
        # Bounding by his notional here is what makes the copy a mirror.
        # min() can only ever shrink the clip, so it inherits the
        # existing cap, the volume governor and the cell blocks intact.
        # THE GOVERNED CLIP, BEFORE THE MIRROR CLAMP.
        #
        # The conviction anchor below multiplies whatever `per` is at
        # that point, and after this line `per` is no longer the
        # governed clip — it is the MIRRORED one. Anchoring on it
        # applies two shrinks for one decision:
        #
        #   his $100, his median $2,000 -> $10.00, design says $25.00
        #   his  $50, his median $2,000 -> $ 5.00 -> DUST-DROPPED
        #   his $100, his median   $100 -> $40.00, design says $100.00
        #   his  $25, his median   $100 -> $ 2.50 -> DUST-DROPPED
        #
        # The bottom two are not merely small, they fall under
        # COPY_MIN_CLIP_USD and the copy is DISCARDED — a whale trade we
        # mapped, priced and then threw away on an arithmetic slip.
        #
        # The mirror clamp stays, as a CEILING in the same min(). Every
        # bound is still there and the result is still <= gov <= $250
        # and <= his own stake; only the anchor's base changes.
        _gov = per
        if his_notional > 0:
            per = min(per, COPY_RATIO_MAX * his_notional)
        # CONVICTION (owner order 2026-08-25). Scale by how far this
        # trade sits above or below HIS OWN habit, then re-apply the
        # mirror clamp so a high-conviction copy can still never exceed
        # what he himself staked.
        #
        # The order matters. Multiplying first and clamping second means
        # conviction can only ever move us WITHIN the envelope his own
        # size defines — it cannot be used to out-bet him, which is the
        # thing he told us to stop doing this morning. The $250 per-fill
        # cap and the daily cap both sit outside this and are untouched.
        # ANCHOR BELOW THE CAP, THEN SCALE UP TO IT. Never multiply the
        # governed clip.
        #
        # The first version of this did `per = per * conv` and then
        # re-clamped against HIS notional. That bound us to his size —
        # which is what I checked — while silently breaching OURS: the
        # governed clip is already the $250 authorization, so a 3x
        # conviction multiple produced $750 and the re-clamp against a
        # $2,000 whale trade let all of it through. A $500 breach of the
        # owner's per-order cap, shipped, on the money path.
        #
        # The arithmetic makes the mistake unavoidable rather than
        # unlucky: inside a $250 cap an UPWARD multiple has nowhere to
        # go. Conviction can only be expressed by anchoring BELOW the
        # cap and letting high-conviction trades climb toward it.
        # ANCHOR_FRAC 0.40 puts the neutral clip at $100 of a $250
        # ceiling, so a routine trade sizes at $100 and his top-decile
        # trades reach the cap — which reallocates our dollars toward
        # the trades he backs hardest without ever adding a dollar of
        # authorization.
        #
        # ONE min() with every ceiling in it. Not a sequence of clamps:
        # a sequence is what let the breach through, because each step
        # only knew about one bound. No reachable value of the multiple
        # can exceed `gov` here, so the cap holds structurally rather
        # than by trusting CONVICTION_MAX to stay small.
        _avg = await whale_average_notional(
            pool, payload.get("whale_username"))
        _conv = conviction_multiple(his_notional, _avg)
        if _avg > 0:
            _arms = [_gov * CONVICTION_ANCHOR_FRAC * _conv, per]
            if his_notional > 0:
                _arms.append(COPY_RATIO_MAX * his_notional)
            _sized = round(min(_arms), 2)
            log.info("CONVICTION %s: his $%.2f vs his median $%.2f = "
                     "%.2fx | anchor $%.2f -> clip $%.2f (cap $%.2f)",
                     payload.get("whale_username"), his_notional, _avg,
                     _conv, per * CONVICTION_ANCHOR_FRAC, _sized, per)
            per = _sized
        shares = float(int(per / limit))
        if shares < 1:
            return _copy_stop("under_one_share", username)
        usd = round(shares * limit, 2)
        # Dust floor: at a mirror ratio his small probes size to a few
        # dollars, where spread and fees eat the edge.
        if usd < COPY_MIN_CLIP_USD:
            return _copy_stop("below_min_clip", username)
        if usd > day_room:
            return _copy_stop("over_day_room", username)
    else:
        limit, usd, shares = plan_order(
            his_price, his_notional, cfg.live_copy_ratio,
            min(cfg.live_max_per_fill_usd, day_room, total_room),
            cfg.live_max_slippage_cents,
            whole_units=(venue == "polymarket-us"),
        )
        # DUST FLOOR (2026-08-25). At ratio 1.0 his smallest probes size
        # to $3-5, where spread and fees eat the edge before the market
        # moves. Skipping them concentrates the book on trades he
        # actually commits to. Tightening only — it can never enlarge
        # an order.
        if usd < COPY_MIN_CLIP_USD or shares <= 0:
            return _copy_stop("below_min_clip_final", username)

    try:
        row_id = await pool.fetchval(
            """
            INSERT INTO live_orders (trade_id, whale_username, asset, condition_id, side,
                                     his_price, reaction_s, limit_price, requested_usd,
                                     requested_shares, status, venue)
            VALUES ($1,$2,$3,$4,'BUY',$5,$6,$7,$8,$9,'submitting',$10)
            ON CONFLICT (trade_id) DO UPDATE
              -- placed_at=now(): THE CLOCK RESTARTS WITH THE ATTEMPT (round
              -- nine) -- every window keyed off placed_at read a reclaimed
              -- row as hours old and a real fill on the re-attempt fell
              -- outside all of them.
              SET status='submitting', error=NULL, order_id=NULL,
                  placed_at=now()
              WHERE live_orders.status IN ('rejected', 'unfilled', 'error')
                -- A BLIND-MARKED ORPHAN ROW STAYS IN THE VENUE REAPER'S
                -- SCOPE (round five): reclaiming it inside the reaper's
                -- 60-minute window would let the IOC's outcome rewrite it
                -- 'unfilled' under the IOC's id, and a still-live GTC on
                -- that market would leave both reapers' scope for good.
                AND NOT (COALESCE(live_orders.error, '') LIKE 'stale submitting row reaped%'
                         AND live_orders.placed_at > now() - interval '60 minutes')
                -- A NAMED ROW IS NEVER RECLAIMED (round seven): it stands for
                -- shares the account holds that the ledger cannot name, or an
                -- orphan fill recorded on a row another row's claim kept out
                -- of 'filled'. Reclaiming it erased the record and the exit.
                AND NOT (COALESCE(live_orders.error, '') LIKE 'venue holds a POSITION%')
                AND NOT (COALESCE(live_orders.error, '') LIKE 'ORPHAN FILL RECORDED%')
                AND NOT (COALESCE(live_orders.error, '') LIKE 'venue has no record of order%')
            RETURNING id
            """,
            payload.get("id"), payload.get("whale_username"), str(payload["asset"]),
            payload.get("condition_id"), his_price, reaction, limit, usd, shares, venue,
        )
    except Exception as exc:  # noqa: BLE001
        # The one-fill-per-asset / one-in-flight-per-asset indexes
        # (migrations 011, 045) catching a concurrent duplicate is the
        # guard WORKING, not an error.
        if ("live_orders_one_fill_per_asset" in str(exc)
                or "live_orders_one_inflight_per_asset" in str(exc)):
            return _copy_stop("already_taken", username)
        raise
    if row_id is None:
        return  # duplicate detection — never double-order one source trade
    # THE CURVE BELONGS TO HIS FILL (rounds ten and eleven): a row
    # re-detected fresh (reaction known) starts a new curve, so the
    # first attempt's samples are cleared. A SWEEP reclaim (reaction
    # None, every two minutes on a missed IOC) is not a new fill of his
    # and must not wipe the only curve anchored to it -- the price-path
    # experiment is about the minutes after HIS trade, not after ours.
    # Best-effort; the table is migration 042's and may be absent.
    if reaction is not None:
        try:
            await pool.execute("DELETE FROM price_path WHERE row_id = $1",
                               row_id)
        except Exception:  # noqa: BLE001
            pass

    # STALENESS GATE (owner order 2026-08-24): a late signal is a decayed
    # edge — the copier refuses rather than chases.
    #
    # PER-WHALE CAPS (the swisstony work, same evening): one global cap
    # is wrong because edges decay at wildly different rates. Measured
    # latency cost against each whale's own edge, from TRUEEDGE:
    #   0x076        lat_cost  -59 on +15,576 — decay is ~free, 90s fine
    #   homerunhazard lat_cost 10,612 on +24,657 — survives, 90s fine
    #   swisstony    lat_cost 15,063 on +14,805 — latency eats ALL of it
    # A whale whose entire edge is consumed by delay must not be copied
    # on a delayed signal at all: at 90s his expected value is negative
    # by his own numbers. 15s reflects what the chain path now delivers
    # (measured -1.1s) while refusing anything that fell back to polling.
    # THE SWEEP PATH: NO AGE GATE, SO CHECK THE POSITION INSTEAD.
    #
    # copy_sweep calls maybe_execute(payload, None) over a seven-day
    # candidate window, and every age ceiling here and in execute_copy
    # is guarded on `reaction is not None` — so a week-old signal
    # reaches this line unchecked. Price is already protected (the FOK
    # limit is his price or better, so a market that ran away does not
    # fill), but POSITION is not: a week-old buy is one he may since
    # have exited, and entering a position the whale has already left
    # is the divergence the exit work exists to close, arriving through
    # the other door.
    #
    # Only on the stale lane. Fresh copies are seconds old and asking
    # would add a query to the hot path to answer a question that
    # cannot yet have changed. Unknowable reads as PROCEED — refusing
    # everything we cannot measure would close the sweep lane, and the
    # lane is not the problem.
    if reaction is None:
        _held = await whale_still_holds(
            pool, str(payload.get("asset") or ""), username)
        if _held is False:
            await pool.execute(
                "UPDATE live_orders SET status='rejected', error=$2 "
                "WHERE id=$1", row_id,
                "stale-signal: he no longer holds this leg — the sweep "
                "found a buy he has since exited, and copying it would "
                "open a position he is out of")
            return
    _stale_cap = _stale_cap_for(username)
    if reaction is not None and reaction > _stale_cap:
        await pool.execute(
            "UPDATE live_orders SET status='rejected', error=$2 WHERE id=$1",
            row_id,
            f"stale-signal: reaction {reaction}s > {_stale_cap:g}s cap")
        return

    # FIRST-FILL GATE (owner upsized to $250 before any real fill,
    # 2026-08-24 evening): until ONE real fill has been verified 'ok' by
    # the side echo, only a single copy may be in flight. The echo runs
    # after a fill, so without this the 4-slot semaphore could put four
    # orders on the venue before the first verdict returns — and the one
    # assumption still unproven is which side an intent actually buys.
    # After the first verified fill the gate opens permanently.
    _first_fill_gate = False
    try:
        _se = await pool.fetchval(
            "SELECT value FROM ingestion_state WHERE key=$1",
            "side_echo_last")
        _se = json.loads(_se) if isinstance(_se, str) else (_se or {})
        _first_fill_gate = int((_se or {}).get("ok", 0)) < 1
    except Exception:  # noqa: BLE001 — unverifiable: assume unproven
        _first_fill_gate = True
    if _first_fill_gate:
        if _FIRST_FILL_LOCK.locked():
            await pool.execute(
                "UPDATE live_orders SET status='rejected', error=$2 "
                "WHERE id=$1", row_id,
                "first-fill gate: one copy at a time until a real fill "
                "is side-verified")
            return
        await _FIRST_FILL_LOCK.acquire()

    _echo_args: tuple | None = None
    # Bound BEFORE the try: the finally reads it, so an exception raised
    # anywhere above the probation branch would turn a real error into a
    # NameError from the cleanup path and lose the original traceback.
    _short_probation_held = False
    try:
        if venue == "polymarket-us":
            from . import pmus

            ctx = await _market_context(pool, payload)
            # Deterministic grammar FIRST (2026-08-10, unmapped-funnel
            # work): the manual desk and underdog sleeve already map via
            # the US slug grammar (atc-/aec- candidates -> exact lookup),
            # but the auto copy path went straight to the fuzzy search
            # pipeline, whose slug-parity step can almost never hit —
            # whale-feed slugs use a different grammar than the US venue.
            # MONEYLINE ONLY (same-day review finding): the candidate
            # grammar drops the post-date line suffix, so a spread/total
            # slug would resolve to the game's MONEYLINE market — and a
            # spread outcome is a team name, which sails through the
            # outcome floor. Derivative types keep the fuzzy pipeline,
            # whose line-consistency guard is the defense that matters.
            from .copy_sports import market_type_of
            src_slug = ctx.get("market_slug") or ctx.get("event_slug") or ""
            mapping = None
            mtype = market_type_of(src_slug)
            # PREMAP FIRST (owner order 2026-08-24): the pre-built venue
            # universe answers from Postgres — exact keys, unique side or
            # nothing, zero network, and the side identifier comes from
            # the venue's own side expansion. Misses fall through to the
            # legacy resolvers unchanged.
            try:
                from .workers import premap as _premap

                mapping = await _premap.resolve(
                    pool, ctx.get("market_title"), ctx.get("event_title"),
                    ctx.get("outcome"), ctx.get("market_slug"))
            except Exception:  # noqa: BLE001 — premap never blocks a copy
                log.exception("premap resolve failed; falling through")
                mapping = None
            mapping_src = "premap" if mapping is not None else None
            # The exact phase is TIME-BOXED (review 2026-08-13): the
            # tennis candidates triple its worst-case serial lookups,
            # and copy_sweep cancels the whole row at 60s — which
            # strands the audit row in 'submitting' and permanently
            # burns that asset's copy. 20s here leaves the fuzzy
            # pipeline its full budget; a timeout falls through, it
            # never rejects.
            _EXACT_BOX_S = 20.0
            # Why the EXACT path missed — see resolve_market_exact.
            # Collected per row rather than on a function attribute:
            # four of these run concurrently under to_thread, and a
            # shared attribute hands one row's reason to another.
            _ex_diag: list[str] = []
            _ex_cands: list[str] = []
            if mapping is None and mtype == "moneyline":
                # Tennis first (owner order 2026-08-13): the feed's
                # surname slugs can never hit the US first3+last3
                # player grammar, so tennis candidates come from the
                # TITLE's player names. Non-tennis slugs add nothing.
                cands = (_tennis_candidates(ctx.get("market_title"),
                                            src_slug)
                         + _us_slug_candidates(src_slug,
                                               ctx.get("outcome") or ""))
                _ex_cands = list(cands)
                try:
                    mapping = await asyncio.wait_for(
                        asyncio.to_thread(pmus.resolve_market_exact,
                                          cands, ctx.get("outcome"),
                                          _ex_diag),
                        timeout=_EXACT_BOX_S)
                except asyncio.TimeoutError:
                    _ex_diag.append("timeout")
                    mapping = None
            elif mapping is None and mtype in ("spread", "total"):
                # MAPPING RECOVERY (owner order 2026-08-12: spreads +
                # moneylines were 94.5% of the 34k-row unmapped
                # funnel): grammar-to-grammar exact resolution with
                # the line preserved IN the candidate slug — the
                # failure mode that once mapped a spread onto its
                # moneyline is designed out, and anything short of
                # full corroboration falls through to the fuzzy
                # pipeline exactly as before.
                try:
                    mapping = await asyncio.wait_for(
                        asyncio.to_thread(pmus.resolve_derivative_exact,
                                          src_slug, ctx.get("outcome"),
                                          ctx.get("market_title")),
                        timeout=_EXACT_BOX_S)
                except asyncio.TimeoutError:
                    _ex_diag.append("timeout")
                    mapping = None
                if mapping is not None and await _dh_sibling_guard(
                        pool, src_slug):
                    _ex_diag.append("dh-siblings")
                    mapping = None
            # HIS OWN SLUG IS THE STRONGEST CANDIDATE THERE IS, AND
            # ONLY THE FUZZY RESOLVER EVER TRIED IT (2026-08-26).
            #
            # resolve_market's FIRST attempt is direct slug parity --
            # his market slug looked up verbatim on the US venue --
            # returning matched_by="slug". But everything that function
            # returns is labelled `fuzzy` at the call site below, so an
            # exact identity match was recorded as a guess, and `fuzzy`
            # is precisely what the quarantine refuses.
            #
            # Live, from the refusal stream:
            #   MAPA-Q rn1 pick=Boris Butulija quarantined
            #     (src=fuzzy, slug=aec-itfme-marifer-borbut-2026-08-26)
            # borbut is Boris Butulija under the venue's own grammar,
            # right league, right date. Nothing about that is fuzzy.
            #
            # Routed through resolve_market_exact rather than by
            # promoting matched_by=="slug", because parity inside
            # resolve_market carries NO _has_sides guard: it can return
            # a two-sided PARENT slug, and ordering a parent hands side
            # selection to the venue -- the 2026-08-23 wrong-side
            # incident exactly. The exact resolver refuses a parent,
            # applies the outcome floor, demands exactly one side above
            # it, and carries the intent. Strictly safer than the path
            # already accepting these.
            #
            # MONEYLINE ONLY, deliberately. A prior review closed the
            # derivative case: the candidate grammar drops the line
            # suffix, so a spread resolved exactly lands on the game's
            # MONEYLINE, and a spread outcome is a team name that passes
            # the outcome floor. That hazard is about GENERATED
            # candidates and this passes his slug verbatim -- but the
            # two venues' derivative grammars are not something this
            # code can verify, and buying a moneyline instead of a
            # spread is a real wrong-market loss. Taking the win where
            # it is provably safe; that guard stays as the review left
            # it, and its test still passes untouched.
            if mapping is None and src_slug and mtype == "moneyline":
                _ex_cands = _ex_cands + [src_slug]
                try:
                    mapping = await asyncio.wait_for(
                        asyncio.to_thread(pmus.resolve_market_exact,
                                          [src_slug], ctx.get("outcome"),
                                          _ex_diag),
                        timeout=_EXACT_BOX_S)
                except asyncio.TimeoutError:
                    _ex_diag.append("timeout")
                    mapping = None
            # PER-TEAM YES/NO EXACT LANE (round-4 final, 2026-08-30):
            # runs only when every prior exact attempt refused;
            # moneyline-typed slugs only; wholly corroborated or
            # nothing. A timeout falls through to fuzzy, never rejects.
            if mapping is None and mtype == "moneyline":
                try:
                    mapping = await asyncio.wait_for(
                        asyncio.to_thread(
                            pmus.resolve_team_yesno_exact, src_slug,
                            ctx.get("outcome"),
                            ctx.get("market_title"),
                            ctx.get("event_title"), _ex_diag),
                        timeout=_EXACT_BOX_S)
                except asyncio.TimeoutError:
                    _ex_diag.append("yn:timeout")
                    mapping = None
                if mapping is not None and await _dh_sibling_guard_ml(
                        pool, src_slug):
                    _ex_diag.append("yn:dh-siblings")
                    mapping = None
                if mapping is not None:
                    mapping_src = "yesno_exact"
            if mapping_src is None:
                mapping_src = "exact" if mapping is not None else None
            # OUR OWN LIST, NOT THE SHARED ATTRIBUTE (2026-08-31).
            # last_diag is read below, AFTER this await — a sibling
            # copy finishing in that window would hand this row its
            # reason, and every unmapped bucket is attributed from it.
            _fz_diag: list[str] = []
            if mapping is None:
                mapping = await asyncio.to_thread(
                    pmus.resolve_market, ctx.get("market_slug"), ctx.get("event_slug"),
                    ctx.get("market_title"), ctx.get("event_title"), ctx.get("outcome"),
                    _fz_diag,
                )
                if mapping is not None:
                    mapping_src = "fuzzy"
            if mapping is None:
                diag = ("; ".join(_fz_diag)[:280] if _fz_diag
                        else getattr(pmus.resolve_market, "last_diag", "") or "")
                # THE EXACT ATTEMPT, FIRST AND BUDGETED. Every unmapped
                # diagnostic anyone has read describes the FUZZY
                # attempt, because that is the only one that recorded
                # anything — so the class that dies one step earlier
                # has been invisible. Tennis is 48% of the recent
                # funnel and dies exactly there.
                #
                # Counted rather than listed: 4,919 rows a week is a
                # distribution question, and "6x404" aggregates where
                # six separate lines do not. The first candidate is
                # kept verbatim so the generated grammar can be read
                # back at a glance.
                _ex = ""
                if _ex_diag:
                    _c: dict[str, int] = {}
                    for _d in _ex_diag:
                        _k = _d.split(":")[0]
                        _c[_k] = _c.get(_k, 0) + 1
                    _ex = ("exact[" + (_ex_cands[0] if _ex_cands else "-")
                           + " " + ",".join(f"{v}x{k}" for k, v
                                            in sorted(_c.items()))
                           + "] ")
                elif _ex_cands:
                    _ex = f"exact[{_ex_cands[0]} not-run] "
                await pool.execute(
                    "UPDATE live_orders SET status='rejected', error=$2 WHERE id=$1",
                    row_id, ("unmapped: " + _ex + diag)[:300]
                    if (diag or _ex)
                    else "no verified Polymarket US market for this outcome",
                )
                log.info("LIVE (US) unmapped: %s / %s", ctx.get("market_title"),
                         ctx.get("outcome"))
                return
            await pool.execute(
                "UPDATE live_orders SET us_market_slug=$2 WHERE id=$1",
                row_id, mapping["market_slug"],
            )
            # THE MAPPING GATE, FACTORED (mirror P1 step 1, owner order
            # 2026-09-02): the four admission gates that stood inline here
            # since the 2026-08-23/24 leak hunts live in _mapping_admitted
            # now, in their original order and with their refusal texts
            # byte-identical, so a mirror book's admission reads the same
            # gates this lane does. What it decides, in order:
            #   1. the side-echo circuit (ingestion_state side_echo_tripped;
            #      unreadable is tripped; NO env override);
            #   2. _whale_set("LIVE_VERIFIED_WHALES") -- only whales the
            #      TRUEEDGE counterfactual certified may spend, whatever the
            #      quarantine says; an empty set disables the gate;
            #   3. LIVE_HOLD_WHALES -- the profitability hold, independent
            #      of quarantine state;
            #   4. the mapping quarantine (env LIVE_MAPPING_QUARANTINE, else
            #      ingestion_state mapping_quarantine; unreadable stays ON):
            #      while it holds, only a mapping_src in QUARANTINE_RESUME_SRC
            #      for a username in _allowed -- the resume lane's
            #      _whale_set("LIVE_PREMAP_WHALES") -- with premap_live on
            #      (env LIVE_PREMAP overrides both ways; an unreadable
            #      switch is _premap_ok = False, fail safe: refuse) may
            #      trade. `_q_on and not _premap_ok` refuses with the text
            #      that names the real class, f"(src={mapping_src}, slug=...)".
            # A refused row keeps its mapping (fidelity samples continue)
            # and the refusal text is written here, on the row, as before.
            # Which gate refused decides the follow-up, by the text's
            # declared prefix: the circuit logs; the quarantine spawns the
            # shadow echo; the verified and hold gates record and stop.
            _q_slug0 = str(mapping.get("market_slug") or "").lower()
            _q_slug = _q_slug0
            _adm_ok, _q_reason = await _mapping_admitted(
                pool, username, mapping_src, _q_slug0)
            if not _adm_ok:
                await pool.execute(
                    "UPDATE live_orders SET status='rejected', error=$2 "
                    "WHERE id=$1", row_id, _q_reason)
                if _q_reason.startswith(_MAPPING_REFUSAL_SIDE_ECHO):
                    log.warning("LIVE (US) refused: side-echo circuit "
                                "tripped (%s)", _q_slug0)
                    return
                if not _q_reason.startswith(_MAPPING_REFUSAL_QUARANTINE):
                    return
                # SHADOW CERTIFICATION: a refused PREMAP resolution is
                # free evidence — re-derive it from live venue data and
                # record the verdict. This is the streak the resume
                # decision reads, produced at zero dollars of risk.
                if mapping_src in QUARANTINE_RESUME_SRC:
                    _spawn_echo(pool, row_id, mapping["market_slug"],
                                ctx.get("outcome"),
                                ctx.get("market_title"), shadow=True,
                                his_slug=src_slug,
                                intent=mapping.get("intent"),
                                mapping_src=mapping_src)
                elif mapping_src == "yesno_exact":
                    # Refused-but-recorded and shadow-certified under
                    # its OWN counter — never the streak that
                    # justified the premap/exact resume. Going live is
                    # a later one-token reviewed change (adding
                    # "yesno_exact" to QUARANTINE_RESUME_SRC) made on
                    # that counter plus the audit rows.
                    _spawn_echo(pool, row_id, mapping["market_slug"],
                                ctx.get("outcome"),
                                ctx.get("market_title"), shadow=True,
                                his_slug=src_slug,
                                intent=mapping.get("intent"),
                                mapping_src=mapping_src,
                                state_key=YESNO_CERT_KEY)
                elif mapping_src == "fuzzy":
                    # CERTIFY THE CLASS THE OWNER ACTUALLY WANTS BACK
                    # (2026-08-26).
                    #
                    # The owner's goal is that no trade is missed for
                    # being "fuzzy", because every trade is verified.
                    # The instrument that can answer that already
                    # exists and has never been pointed at this class:
                    # the side echo re-derives a mapping from LIVE
                    # venue rows through an independent matcher, and
                    # produced 691 ok / 0 mismatch for premap, which is
                    # what justified tonight's `exact` resume.
                    #
                    # `fuzzy` was never certified, so there is NO
                    # evidence either way about the class that is 20 of
                    # every 21 refusals. Not "it is dangerous" — there
                    # is no measurement at all, and the 2026-08-23
                    # incident is one data point about six orders.
                    #
                    # This spends no money and changes no gate. A fuzzy
                    # mapping is still refused; it is refused and then
                    # MEASURED, under its own counter so the premap
                    # streak stays a clean answer to its own question.
                    # In a few hours it turns "we do not trust fuzzy"
                    # into a number, which is the only honest basis for
                    # widening the lane or leaving it shut.
                    _spawn_echo(pool, row_id, mapping["market_slug"],
                                ctx.get("outcome"),
                                ctx.get("market_title"), shadow=True,
                                his_slug=src_slug,
                                intent=mapping.get("intent"),
                                mapping_src=mapping_src,
                                state_key=FUZZY_CERT_KEY)
                log.warning("LIVE (US) quarantined %s mapping: %s / %s",
                            mapping_src, ctx.get("market_title"),
                            _q_slug)
                return
            # NEVER-ADD, DB-SIDE (incident 2026-08-11 afternoon, the $318
            # Over-2.5 position): the venue-side no-stack below answers
            # from a positions snapshot that goes silently stale through
            # a venue outage — Polymarket's maintenance morning left it
            # hours old and every re-entry passed. Our OWN order ledger
            # is postgres: it survives deploys and outages, and one
            # market copied once is one row. Any filled or in-flight
            # order on this exact market refuses another, whatever
            # identity or asset id proposes it.
            # The $2 underdog sleeve is scoped OUT (owner 2026-08-12:
            # the restarted sleeve is "completely independent" — its
            # rows must not veto copies; copy-vs-copy never-add is
            # unchanged).
            # A NAMED row (round eight) -- shares the account holds that
            # no ledger row explains, or an orphan fill recorded on a
            # row another claim kept out of 'filled' -- vetoes like a
            # fill, so the fast lane does not depend on the positions
            # snapshot to see them.
            # THE ROW ON HIS ASSET COMES FIRST (adds, 2026-09-02): the
            # market may hold a row on the other outcome or an older
            # leg's retired row; an add is judged against the row that
            # holds the SAME outcome token, and a row in flight on it
            # (a leg not yet merged) outranks the standing one so a
            # second leg waits its turn.
            # A MIRROR BOOK OUTLIVES THE WINDOW (mirror P1, step 7; owner
            # order 2026-09-02): the book's standing row is placed once,
            # at open, and holds the market for days. Judged on the 48 h
            # clock alone a three-day-old book would let the other
            # outcome through and, with the lane read below, a leg onto
            # the book itself. The panel review's predicate audit (spec
            # 1d) widens the window for that lane only; every other row
            # keeps the clock it has.
            prior = await pool.fetchrow(
                "SELECT id, status, lower(COALESCE(whale_username, '')) AS whale, "
                "       asset, filled_shares::float8 AS filled_shares, "
                "       fill_price::float8 AS fill_price, "
                "       filled_usd::float8 AS filled_usd, "
                "       (raw->'adds')::text AS adds, "
                "       lane, "
                "       (SELECT t.tx_hash FROM trades t WHERE t.id = live_orders.trade_id) "
                "           AS tx_hash /* prior-copy */ "
                "FROM live_orders "
                "WHERE us_market_slug = $2 AND id <> $1 "
                "AND (status IN ('filled', 'submitting') "
                "     OR (status = 'error' AND "
                "         (error LIKE 'venue holds a POSITION%' "
                "          OR error LIKE 'ORPHAN FILL RECORDED%' "
                "          OR error LIKE 'venue has no record of order%'))) "
                "AND COALESCE(whale_username, '') <> 'underdog' "
                "AND (placed_at > now() - interval '48 hours' OR lane = 'mirror') "
                "ORDER BY (asset = $3) DESC, (status = 'submitting') DESC, "
                "         placed_at DESC LIMIT 1",
                row_id, mapping["market_slug"], str(payload["asset"]))
            add_of = None
            if prior is not None:
                # AN ADD, NOT A DUPLICATE (owner order 2026-09-02). His
                # fresh BUY on the SAME OUTCOME TOKEN where HIS filled
                # row stands is copied as an add and merged into that
                # row on fill. Everything else that used to be never-add
                # still is: another whale's row, the other outcome of
                # the market (his hedge or reversal is not an add to
                # OUR side), a row still in flight or named, a sweep
                # reclaim (reaction None: hours old), a trade already
                # merged, or a row that has taken its legs.
                _adds = _adds_of(_row_get(prior, "adds"))
                _tid = str(payload.get("id") or "")
                _same_leg = (str(_row_get(prior, "asset") or "")
                             == str(payload["asset"]))
                # THE BOOK TAKES NO LEGS (mirror P1, step 7; owner order
                # 2026-09-02). A mirror book's standing row is HIS
                # 'filled' row on the asset, so once the whale leaves
                # the mirror allowlist his fresh BUY there would qualify
                # as an add and be merged into the book -- shares the
                # reconciler never placed, on a row it alone keeps. The
                # lane is read with the row (fail closed: a prior row on
                # that lane is never an add) and the refusal names the
                # book, so the census tells a held market from a
                # duplicate.
                _mirror_prior = (_row_get(prior, "lane") or "") == "mirror"
                _his_row = (ADDS_ENABLED and reaction is not None and _same_leg
                            and _row_get(prior, "status") == "filled"
                            and _row_get(prior, "whale") == username
                            and not _mirror_prior)
                # ONE WHALE FILL IS ONE LEG, WHATEVER ITS TRADE ID
                # (review round one): ingestion documents key-divergent
                # twins of one fill -- a reorg-shifted timestamp, a
                # rounding variant of the price -- that land as a second
                # trades row. Before adds the asset claim made a twin
                # harmless; now it would be a second buy of one fill.
                # The transaction hash is the fill's identity: a
                # candidate sharing it with the standing row's trade or
                # any merged leg is the same fill and is refused; so is
                # one whose hash cannot be read (fail closed).
                _twin, _cand_tx = False, str(payload.get("tx_hash") or "").lower()
                if _his_row:
                    if not _cand_tx and _tid:
                        _cand_tx = await _tx_hash_of(pool, _tid)
                    _seen_tx = {str(_row_get(prior, "tx_hash") or "").lower()}
                    _seen_tx |= {str(a.get("tx_hash") or "").lower() for a in _adds}
                    _seen_tx.discard("")
                    _twin = (not _cand_tx) or (_cand_tx in _seen_tx)
                if (_his_row and not _twin
                        and float(_row_get(prior, "filled_shares") or 0) > 0
                        and len(_adds) < MAX_ADDS_PER_MARKET
                        and _tid not in {str(a.get("trade_id")) for a in _adds}):
                    add_of = {"id": _row_get(prior, "id"),
                              "filled_shares": float(_row_get(prior, "filled_shares") or 0),
                              "fill_price": float(_row_get(prior, "fill_price") or 0),
                              "filled_usd": float(_row_get(prior, "filled_usd") or 0),
                              "adds": _adds, "trade_id": _tid,
                              "tx_hash": _cand_tx, "his_price": his_price}
                    _copy_stop("add_leg", username)
                    # THE LEG NAMES ITS STANDING ROW ON ITS OWN RAW, before
                    # any order: a leg killed mid-flight is recognisable to
                    # the reaper as an add that could not merge.
                    await pool.execute(
                        "UPDATE live_orders SET raw = COALESCE(raw, '{}'::jsonb) "
                        "|| $2::jsonb WHERE id=$1",
                        row_id, json.dumps({"add_of": add_of["id"]}))
                    log.info("ADD %s: fresh buy on %s where row %s stands "
                             "(%s legs so far) — copying as an add",
                             username, mapping["market_slug"], add_of["id"],
                             len(_adds))
                else:
                    await pool.execute(
                        "UPDATE live_orders SET status='rejected', error=$2 "
                        "WHERE id=$1", row_id,
                        "never-add: this market is held by the mirror book"
                        if _mirror_prior else
                        "never-add: this market was already copied"
                        + (" (adds off)" if not ADDS_ENABLED else
                           " (add refused: not a fresh detection)"
                           if reaction is None else
                           " (add refused: his buy is the other outcome of "
                           "a market we hold)" if not _same_leg else
                           " (add refused: row not filled or another whale's)"
                           if _row_get(prior, "status") != "filled"
                           or _row_get(prior, "whale") != username else
                           " (add refused: same transaction as a leg we hold "
                           "— a twin detection of one fill)" if _twin else
                           f" (add refused: {len(_adds)} legs already)"
                           if len(_adds) >= MAX_ADDS_PER_MARKET else
                           " (add refused: this trade already merged)"
                           if _tid in {str(a.get("trade_id")) for a in _adds}
                           else " (add refused: standing row holds no shares)"))
                    return
            # ONE POSITION PER GAME (owner audit order 2026-08-11
            # evening, superseding the ladder-only rule from the same
            # afternoon): a game is ONE bet. Ladder rungs are the same
            # bet several times; opposite-side moneylines (the Halys+
            # Kwon guaranteed-loss shape — RN1 completes pairs, we must
            # not) are the same bet against itself. The first market a
            # whale entered on a game is copied; every later market on
            # that game — any type, any side, any whale — is refused.
            # A MIRROR BOOK HOLDS ITS GAME FOR ITS WHOLE LIFE (mirror P1,
            # step 7; owner order 2026-09-02): the standing row is placed
            # once, at open, so on the 48 h clock alone a three-day-old
            # book would free the game for a second side. The read below
            # widens the window for that lane only (spec 1d); every other
            # row keeps the clock it has.
            gk = _us_game_key(mapping["market_slug"])
            # an ADD holds this game by definition: the gate refuses a
            # second SIDE of one game, not a second leg of one side
            if gk is not None and add_of is None:
                # 'underdog' rows excluded: the $2 sleeve buying a
                # game's dog at T-5 must not consume that game's ONE
                # copy slot (owner 2026-08-12 independence order).
                held = await pool.fetch(
                    "SELECT us_market_slug FROM live_orders "
                    "WHERE status IN ('filled', 'submitting') "
                    "AND id <> $1 AND us_market_slug IS NOT NULL "
                    "AND COALESCE(whale_username, '') <> 'underdog' "
                    "AND (placed_at > now() - interval '48 hours' OR lane = 'mirror')",
                    row_id)
                if any(_us_game_key(r["us_market_slug"]) == gk
                       for r in held):
                    # PAIR-COMPLETION CARVE-OUT (owner order 2026-08-26).
                    #
                    # The refusal above this line said "RN1 completes
                    # pairs, we must not", and it is backwards about
                    # RN1: his completions average +1.02c and he has
                    # made 95,474 of them. A YES+NO pair redeems for
                    # exactly $1 whatever the outcome, so completing at
                    # p + q < $1 is a locked profit with no directional
                    # exposure left, and completing at p + q > $1 is a
                    # locked loss. The old refusal could not tell them
                    # apart and declined both.
                    #
                    # The refusal is lifted ONLY when the profit is
                    # already proved: venue-confirmed complement of a
                    # leg we hold, our cost plus its live ask clearing a
                    # dollar by PAIR_MIN_EDGE_CENTS.
                    # _pair_completion_context returns None on anything
                    # unknown, so an unreadable price refuses exactly as
                    # this branch always has.
                    _pair = await _pair_completion_context(
                        pool, mapping["market_slug"],
                        [r["us_market_slug"] for r in held],
                        order_limit=limit)
                    if _pair and _pair["allowed"]:
                        # Cap at the shares already held: buying more
                        # than the other leg would leave net directional
                        # exposure, which is a different trade wearing
                        # this one's justification. usd is recomputed
                        # from the capped share count so downstream
                        # spend accounting sees the real order.
                        shares = min(shares, _pair["held"])
                        usd = round(shares * limit, 2)
                        log.warning(
                            "PAIR-COMPLETE %s against %s: our %.4f + "
                            "ask %.4f locks %.2fc x %d shares",
                            mapping["market_slug"], _pair["sibling"],
                            _pair["our_cost"], _pair["ask"],
                            _pair["edge_cents"], shares)
                    else:
                        # The near-miss is logged WITH its edge: a
                        # refusal that records what it declined is how
                        # PAIR_MIN_EDGE_CENTS gets set from evidence
                        # rather than taste.
                        if _pair:
                            log.info(
                                "PAIR-DECLINED %s: edge %.2fc below "
                                "%.2fc floor", mapping["market_slug"],
                                _pair["edge_cents"],
                                PAIR_MIN_EDGE_CENTS)
                        await pool.execute(
                            "UPDATE live_orders SET status='rejected', "
                            "error=$2 WHERE id=$1", row_id,
                            "one position per game")
                        return
            # NO-STACK (owner 2026-08-08: "trades are higher than $10 per
            # trade"): the engine, the desk, and this copy path each cap
            # their own tickets but shared no ledger, so two sleeves could
            # build one $20 position. The venue account is the referee —
            # an outcome the account already holds is never added to.
            # an ADD's premise is that the account holds this market;
            # no-stack guards a FRESH copy landing on an existing hold
            if add_of is None and await asyncio.to_thread(
                    pmus.account_holds, mapping["market_slug"]):
                # SLEEVE CARVE-OUT (owner 2026-08-12: the $2 underdog
                # sleeve is "completely independent"): it buys EVERY
                # MLB/tennis dog at T-5, so post-start the account
                # "holds" nearly every game market. A venue holding
                # whose ONLY Postgres explanation is the sleeve is the
                # sleeve's $2, not a copy stack — the copy proceeds
                # (its own never-add above already vetoed real copy
                # duplicates). Any other explanation — a copy row, or
                # NO row at all (unexplained venue state) — still
                # refuses, exactly as the leak fix demands.
                dog_owned = await pool.fetchval(
                    "SELECT bool_or(whale_username = 'underdog') "
                    "AND NOT bool_or(COALESCE(whale_username, '') "
                    "                <> 'underdog') "
                    "FROM live_orders WHERE us_market_slug = $2 "
                    "AND id <> $1 "
                    "AND status IN ('filled', 'submitting') "
                    "AND placed_at > now() - interval '48 hours'",
                    row_id, mapping["market_slug"])
                if not dog_owned:
                    await pool.execute(
                        "UPDATE live_orders SET status='rejected', "
                        "error=$2 WHERE id=$1", row_id,
                        "no-stack: account already holds this market")
                    return
            # Cross-venue claim RE-CHECK at the last instant (review
            # 2026-08-10): the event-woken Kalshi leg can fire and claim
            # this asset during the seconds this mapping/no-stack cycle
            # just spent — the entry check is stale by now. Guarded like
            # the entry check: a missing table degrades to no re-check.
            try:
                if await pool.fetchval(
                        "SELECT 1 FROM kalshi_claims WHERE asset = $1 "
                        "LIMIT 1", str(payload["asset"])):
                    await pool.execute(
                        "UPDATE live_orders SET status='rejected', "
                        "error=$2 WHERE id=$1", row_id,
                        "kalshi copied this position mid-flight")
                    return
            except Exception:  # noqa: BLE001
                pass
            # PARTIAL-TAKE COPIES (owner order 2026-08-21: "if there was
            # only $100 of liquidity at the price that fit within our
            # rules, fill the $100; if there was more, fill up to the
            # clip"): IOC takes what rests at his price or better and
            # cancels the rest. The same-or-better limit is unchanged —
            # only the all-or-nothing constraint is dropped, so a thin
            # book yields a smaller position instead of a killed order.
            _echo_args = (mapping["market_slug"], ctx.get("outcome"),
                          ctx.get("market_title"))
            _echo_kw = {"his_slug": src_slug,
                        "intent": mapping.get("intent"),
                        "mapping_src": mapping_src}
            # SIDE INTENT OR REFUSE (venue ground truth 2026-08-24):
            # on families whose sides share an identifier, `intent` is
            # the only field that names the side. A mapping that cannot
            # state its intent is UNORDERABLE — ordering it would hand
            # side selection to the venue, which is the incident.
            _intent = mapping.get("intent")
            # BUY_SHORT IS REFUSED (2026-08-25). Not a heuristic — a
            # controlled comparison, 31 fills in 24h:
            #
            #   BYINTENT BUY_LONG   n=25  over=0  clean=25
            #   BYINTENT BUY_SHORT  n=6   over=6  clean=0
            #
            # Perfect separation, and the whale confound is ruled out
            # WITHIN one book: 0x076daa87 filled six shorts (ratios
            # 3.87, 3.545, 2.142, 1.757, 1.244, 1.146) and several
            # longs (6250sh @0.04 = $250.00, 4166sh @0.06 = $249.96,
            # 423sh @0.59) on the same day, same venue, same aec-
            # family. Same whale, same everything — the only variable
            # that separates broken from clean is the intent.
            #
            # Every short we have ever filled has been wrong. Refusing
            # the branch costs us a class of copy we have never once
            # executed correctly and keeps the 25 that work.
            #
            # This does NOT diagnose WHY. The intent may be inverted in
            # side_intent, or BUY_SHORT may not mean what we assume at
            # this venue. The SIDE verdict is still pending and I am
            # not shipping an inversion on a theory — but I do not need
            # the cause to stop doing the thing that is 6-for-6 wrong.
            #
            # LIVE_ALLOW_SHORT=on re-opens it once the cause is known.
            #
            # THE CAUSE IS NOW KNOWN, AND THE OVERSPEND HALF OF THE
            # EVIDENCE ABOVE IS RETRACTED IN THIS FILE (2026-08-26).
            # fill_cash's docstring re-costs all six of those receipts
            # under the short denomination (1-p)*qty: "Every one at or
            # under what we authorised, one exact to the cent, worst
            # overage across all six $0.00. There was no overspend.
            # There was a units bug in our own bookkeeping." At scale
            # the endpoint agrees: short_model 50/50 within
            # authorization against long_model 23/50.
            #
            # What is still NOT proved is the side itself. The venue's
            # netPosition sign is the only end-to-end answer and it has
            # never run on a short, because we have not placed one since
            # the model was corrected. So the flag stays OFF by default
            # and this is unchanged for anyone who does not set it.
            #
            # What changes is that setting it is now bounded rather than
            # all-or-nothing: see _short_gate. Owner-approved 2026-08-26.
            if (_intent == "ORDER_INTENT_BUY_SHORT"
                    and os.getenv("LIVE_ALLOW_SHORT", "").strip().lower()
                    == "on"):
                _ok, _why, _probation = await _short_gate(pool)
                if not _ok:
                    await pool.execute(
                        "UPDATE live_orders SET status='rejected', "
                        "error=$2 WHERE id=$1", row_id, _why)
                    log.warning("LIVE (US) held %s: %s",
                                mapping["market_slug"], _why)
                    return
                if _probation:
                    # Serialise ONLY while unproven. Taking this lock
                    # after the class is proved would queue every short
                    # behind the one before it forever.
                    await _SHORT_LOCK.acquire()
                    _short_probation_held = True
            if (_intent == "ORDER_INTENT_BUY_SHORT"
                    and os.getenv("LIVE_ALLOW_SHORT", "").strip().lower()
                    != "on"):
                # SHADOW THE REFUSAL, DO NOT JUST DROP IT.
                #
                # Five hours after the ban shipped the sleeve had taken
                # ONE fill, because 0x076daa87's live flow is almost
                # entirely shorts (rejections at 04:58, 05:03, 05:04,
                # every one of them stopped here). A ban on the class
                # that carries most of the flow is an outage wearing a
                # guard's uniform, and I have made that exact mistake
                # twice tonight already.
                #
                # But the ban is not lifted on that argument alone.
                # Reopening a money gate while the owner is asleep, on
                # a class that is 6-for-6 wrong, is not a call I get to
                # make on inference. So instead: read the intent-aware
                # ask for the leg we WOULD have bought and record it
                # beside the whale's price on the rejection row.
                #
                # That converts every refusal into evidence. By morning
                # the question "would the ask guard alone have caught
                # these?" is answered by counting rows, not arguing —
                # if the shadow asks sit near his price, the ask guard
                # (1.08) and the side band (0.15) are the real fix and
                # the ban is redundant; if they sit at the complement,
                # the ban stays and we know why.
                _shadow = ""
                try:
                    _sa = await asyncio.to_thread(
                        pmus.side_ask, mapping["market_slug"], _intent)
                    if _sa is not None and his_price:
                        _r = (_sa / his_price) if his_price else 0.0
                        _shadow = (f" | SHADOW ask={_sa} his={his_price} "
                                   f"ratio={_r:.3f} gap="
                                   f"{abs(_sa - his_price):.3f}")
                    elif _sa is not None:
                        _shadow = f" | SHADOW ask={_sa} his=?"
                    else:
                        _shadow = " | SHADOW ask=unreadable"
                except Exception as _exc:  # noqa: BLE001 — evidence only
                    _shadow = f" | SHADOW err={type(_exc).__name__}"
                await pool.execute(
                    "UPDATE live_orders SET status='rejected', error=$2 "
                    "WHERE id=$1", row_id,
                    "short-branch-refused: every BUY_SHORT fill on "
                    "2026-08-24 landed on the opposite side at the "
                    "complement price (6/6, against 25/25 clean longs) "
                    "— the short branch is off until the cause is known"
                    + _shadow)
                log.warning("LIVE (US) refused %s: BUY_SHORT branch is "
                            "off (6/6 wrong on 2026-08-24)%s",
                            mapping["market_slug"], _shadow)
                return
            if not _intent:
                await pool.execute(
                    "UPDATE live_orders SET status='rejected', error=$2 "
                    "WHERE id=$1", row_id,
                    "no side intent: this market's sides share an "
                    "identifier and the resolver could not name the "
                    "side — refusing rather than letting the venue pick")
                log.warning("LIVE (US) refused %s: unorderable side",
                            mapping["market_slug"])
                return
            # PRE-TRADE ASK CHECK (2026-08-25) — the guard the evidence
            # actually supports.
            #
            # Measured, not assumed: PRICE-TRUTH previewed BOTH intents
            # on the aec- family (the family that overspent) and the
            # venue quoted OUR price both times, ratio 1.000. So the
            # venue is not charging the complement. Yet five real fills
            # came back far ABOVE our limit — 1086sh asked at 0.23,
            # filled at 0.89 — with the exact quantity requested. The
            # limit we send is evidently not enforced at execution; the
            # order behaves like a market IOC and takes the book.
            #
            # If the venue will not hold our limit, we have to check the
            # book ourselves BEFORE sending. side_ask reads the ask for
            # the leg our INTENT names (slug_ask cannot: both sides
            # share the identifier on this family). Ask above our limit
            # means the fill would cost more than authorized — refuse.
            #
            # Fail-closed on an unreadable ask: a price we cannot see is
            # not a price we can bound. All five overspent rows would
            # have been refused here.
            #
            # COST: one extra venue read per order, which is latency on
            # the copy path. That is a real tradeoff against SwissTony's
            # speed and it is the right side of it — a fast wrong-priced
            # fill is worth less than a slow refusal.
            # BOTH DIRECTIONS (owner order 2026-08-25: "resolve the
            # limit issue"). `ask > limit` alone stops the OVERSPEND but
            # not the WRONG SIDE. If our intent names the opposite leg
            # and that leg happens to be CHEAPER than our limit, the
            # order sails through looking like a bargain and we hold the
            # wrong bet at a good price.
            #
            # The whale's own price is the reference: we are copying the
            # same outcome, so the side we buy should be quoted near
            # what he paid. Far off in EITHER direction means the leg we
            # named is probably not his outcome. On the 0.22 -> 0.78
            # rows the gap was 0.56, which this refuses outright.
            #
            # Wide enough not to fight normal movement between his fill
            # and ours; far tighter than a complement flip, which is
            # always >= |1 - 2p| away.
            _band = float(os.getenv("LIVE_SIDE_PRICE_BAND", "0.15"))
            _ask = await asyncio.to_thread(
                pmus.side_ask, mapping["market_slug"], _intent)
            if (_ask is not None and his_price
                    and abs(_ask - his_price) > _band):
                await pool.execute(
                    "UPDATE live_orders SET status='rejected', error=$2 "
                    "WHERE id=$1", row_id,
                    f"side-price-mismatch: the side our intent names is "
                    f"quoted {_ask} but the whale paid {his_price} "
                    f"(gap {abs(_ask - his_price):.2f} > {_band}) — this "
                    f"is probably not his outcome, refusing")
                log.warning("LIVE (US) refused %s: side priced %s vs his "
                            "%s", mapping["market_slug"], _ask, his_price)
                return
            # PROPORTIONAL, NOT ABSOLUTE (2026-08-25, second pass).
            #
            # Shipped as `ask > limit` and it made the sleeve STERILE
            # within the hour. Observed refusals: asks of 0.59 / 0.89 /
            # 0.85 against limits of 0.57 / 0.88 / 0.84 — one and two
            # cents. Our limit is his price plus a deliberately tight
            # slippage cap, so an absolute test refuses nearly every
            # honest copy. A guard that blocks everything is not a
            # guard, it is an outage that reports itself as safety.
            #
            # Calibrated against BOTH populations rather than guessed:
            #   worst honest refusal   ratio 1.035
            #   cheapest real overspend ratio 1.146
            # A threshold of 1.08 sits in that gap with margin either
            # side. Proportional matters: +0.03 absolute would be 13%
            # at a 0.22 limit and 75% at 0.04.
            #
            # Worst case this now permits is an 8% overspend on a clip
            # — about $20 — against losing every copy. The complement
            # case starts at 1.146 and is still refused.
            # THE SHARED CONSTANT, not a fourth reading of the env.
            # This threshold and the halt boundary are one decision;
            # restating it here is how they came to disagree.
            _tol = ASK_TOLERANCE
            if _ask is None or _ask > limit * _tol:
                await pool.execute(
                    "UPDATE live_orders SET status='rejected', error=$2 "
                    "WHERE id=$1", row_id,
                    (f"ask-above-limit: venue asks {_ask} for this side, "
                     f"we authorized {limit} (ratio "
                     f"{(_ask / limit if limit else 0):.3f} > {_tol:.2f}) "
                     f"— refusing")
                    if _ask is not None else
                    ("no readable ask for this side — refusing rather "
                     "than sending an unbounded order"))
                log.warning("LIVE (US) refused %s: ask %s > limit %s",
                            mapping["market_slug"], _ask, limit)
                return
            # The wire price is denominated in the CONTRACT, not in the
            # whale's outcome. For a long they are the same number; for
            # a short they are complements, and sending the raw one
            # authorised up to 3.5x the money we meant to commit.
            _wire = wire_limit(limit, _intent)
            # THE IOC'S RESULT SURVIVES OUR OWN CANCELLATION (round
            # nine): the sweep's per-row timeout cancels this coroutine
            # inside the venue call, the thread completes the fill, and
            # nothing wrote its id -- the row sat 'submitting' with no
            # id and the fill was attributed by cent, or not at all.
            # TIME TO VENUE, MEASURED (2026-09-02). placed_at is stamped
            # at the INSERT, before mapping and three venue round trips;
            # reaction_s at the semaphore. Neither says when the order
            # actually left. These two stamps ride the row's raw so the
            # lane census can put the seconds where they went.
            _timing = {"t_send": time.time(),
                       "t_detect": payload.get("detected_at")}
            result = await _ioc_guarded(
                pool, row_id, pmus.submit_fok,
                mapping["market_slug"], _wire, int(shares), False,
                "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL", _intent)
            _timing["t_reply"] = time.time()
            _lane = "ioc"
            # ── REST LANE, ONLY AFTER THE IOC MISSED ─────────────────
            # A fill above means the existing path worked and this does
            # nothing. An empty IOC means the ask was above his price
            # plus tolerance when we arrived; rest at his exact price
            # for a few seconds and see whether it comes back. See
            # REST_BID_ENABLED for why this cannot touch any fill the
            # IOC would have produced.
            # AN ADD LEG NEVER RESTS: a leg left 'submitting' under a
            # GTC beside its standing row would be booked 'filled' by
            # the reaper into the one-fill-per-asset index. An add is
            # the IOC or nothing.
            if locals().get("add_of") is None and not (
                    result.get("ok") and
                    float(result.get("filled_shares") or 0) > 0):
                _r = await _rest_after_ioc(
                    pool, row_id, mapping["market_slug"], his_price,
                    shares, _intent, username, reaction)
                if _r is not None:
                    result = _r
                    _lane = "rest"
        else:
            # GLOBAL-CLOB LEG FAIL-CLOSED (leak-hunt find 2026-08-24):
            # active_venue() silently falls back to the global CLOB when
            # the PMUS keys go missing but PM_PRIVATE_KEY remains — and
            # this branch carried NONE of the freeze controls (total
            # quarantine, allowlist, hold, never-add, one-per-game,
            # no-stack, side echo). A config fault must fail CLOSED,
            # never into an ungated venue. LIVE_CLOB_COPIES=on re-opens
            # the leg deliberately; until then every attempt records an
            # audit-visible refusal.
            if os.environ.get("LIVE_CLOB_COPIES", "") != "on":
                await pool.execute(
                    "UPDATE live_orders SET status='rejected', error=$2 "
                    "WHERE id=$1", row_id,
                    "clob-leg-closed: global-CLOB fallback is "
                    "fail-closed (freeze controls are PMUS-only)")
                log.warning("LIVE refused: global-CLOB fallback while "
                            "fail-closed (venue=%s)", venue)
                return
            result = await asyncio.to_thread(
                _submit_fok, str(payload["asset"]), limit, shares)

        filled = float(result["filled_shares"]) if result["ok"] else 0.0
        fill_price = float(result["fill_price"]) if result["ok"] else None
        _fill_intent = locals().get("_intent") or (
            (locals().get("mapping") or {}).get("intent"))
        spent = fill_cash(filled, fill_price, _fill_intent)
        # POST-FILL OVERSPEND DETECTOR (2026-08-25). The per-fill clip is
        # enforced BEFORE submit, on `usd = shares * limit`. That bounds
        # what we ASK to spend, not what the venue takes: it holds only
        # while an IOC buy cannot fill above its own limit. The 24h
        # aggregate showed filled averaging 1.45x requested on a live
        # whale, so that assumption is not something to keep assuming.
        # Record the breach on the row itself — a number nobody can read
        # is not a control — and shout it into the log.
        # THE SAME INTENT `spent` WAS COMPUTED WITH, one line above.
        # Passing the price and letting the breaker re-derive the cost
        # is what made the predicate and the halt record disagree.
        overspent = is_overspend(usd, filled, fill_price, _fill_intent)
        # A SEPARATE, HIGHER BAR FOR STOPPING EVERYTHING. See
        # OVERSPEND_HALT_RATIO: the row below is still stamped OVERSPEND
        # at 1.01 and the log below still fires, so an overspend in the
        # authorized band stays fully visible — it just does not brick
        # the sleeve the pre-trade guard admitted it into.
        halting = is_halting_overspend(usd, filled, fill_price,
                                       _fill_intent)
        if overspent:
            # `mapping` is bound only on the PMUS branch; the CLOB leg
            # names its market by asset. Never let the alarm itself
            # raise — an overspend that crashes into the generic
            # handler is recorded as a mystery 'error' row instead.
            log.error("LIVE OVERSPEND %s %s: asked $%.2f (%.0f @ %.4f) "
                      "but filled $%.2f (%.0f @ %.4f) — ratio %.3f",
                      payload.get("whale_username"),
                      (locals().get("mapping") or {}).get("market_slug")
                      or payload.get("asset") or "?",
                      usd, shares, limit, spent, filled, fill_price or 0,
                      spent / usd)
            # AUTO-HALT ON THE FIRST OVERSPEND (2026-08-25).
            #
            # I told the owner the preview cost guard made running live
            # safe. That was WRONG and this is the correction. PRICE-
            # TRUTH showed the venue's preview simply echoes our own
            # price * quantity ($3.00 asked, $3.00 previewed, ratio
            # 1.000) — so prev_cost always equals expected_cost and the
            # guard can NEVER see an overcharge. The overcharge happens
            # at EXECUTION, which no pre-trade check observes.
            #
            # What is actually observable is the fill itself. So the
            # circuit is post-fill: the first overspend costs one clip
            # and stops the sleeve, instead of repeating across every
            # copy for the rest of the night. Bounded damage is the
            # honest protection here; pre-trade prevention is not
            # available until the mechanism is understood.
            #
            # Persisted, so it survives a worker restart and holds until
            # a human clears it — a breaker that forgets is not one.
            if not halting:
                _exit_stop("overspend_within_authorized_band",
                           ratio=round(spent / usd, 3),
                           halt_at=OVERSPEND_HALT_RATIO)
                log.warning(
                    "LIVE OVERSPEND ratio %.3f is inside the band the "
                    "pre-trade ask guard authorizes (<= %.2f) — recorded "
                    "on the row, NOT halting the sleeve",
                    spent / usd, OVERSPEND_HALT_RATIO)
            else:
                try:
                    await pool.execute(
                        "INSERT INTO ingestion_state (key, value) "
                        "VALUES ('copy_overspend_halt', $1::jsonb) "
                        "ON CONFLICT (key) DO UPDATE SET value = $1::jsonb",
                        json.dumps({
                            "at": datetime.now(timezone.utc).isoformat(),
                            "whale": payload.get("whale_username"),
                            "slug": (locals().get("mapping") or {}).get(
                                "market_slug") or payload.get("asset"),
                            "asked": usd, "spent": spent,
                            "ratio": round(spent / usd, 3),
                            "halt_at": OVERSPEND_HALT_RATIO,
                            "limit": limit, "fill_price": fill_price,
                            "why": "venue filled above our limit — copying "
                                   "halted after the first occurrence"}))
                    log.error("COPY SLEEVE HALTED by overspend breaker")
                except Exception:  # noqa: BLE001 — never lose the fill
                    log.exception("could not persist the overspend halt")
        _receipt = json.dumps({**(result.get("raw") or {}),
                               **(locals().get("_timing") or {})}, default=str)
        # THE ADD IS MERGED INTO THE STANDING ROW (owner order
        # 2026-09-02), in ONE statement with the leg's own retirement:
        # shares and cost add, the fill price becomes the share-weighted
        # average, orig_shares grows so a later partial exit's fraction
        # is of everything ever bought, and the leg is listed on the
        # standing row's raw so the ledger can show it and a replayed
        # trade is refused. The leg's row becomes 'merged' -- the venue
        # receipt stays on it, its money columns are zeroed so no cap,
        # budget or ledger counts the leg twice. A standing row that
        # left 'filled' while the leg was in flight (he exited; it
        # settled) merges nothing: the leg is then recorded as a fill
        # of its own below and stands as its own position.
        _add = locals().get("add_of")
        _merged = False
        _named = False
        if _add and filled > 0 and result.get("ok"):
            # TWO TRIES, THEN THE ORPHAN NAME, NEVER A SECOND POSITION
            # (adds review, 2026-09-02). A merge that misses because the
            # standing row is mid-exit ('exiting'), in flight, or settled
            # -- or that raised on a transient fault while the row is
            # still 'filled' -- must not fall through to a 'filled' row
            # of the leg's own: the one-fill index would then refuse the
            # exit's write-back and wedge the standing row for good. The
            # leg is recorded under the orphan name instead, with its
            # figures and receipt, and the reaper merges it once the
            # standing row is 'filled' again (or promotes it once that
            # row is gone). Only a standing row already out of every
            # live state lets the leg stand alone.
            for _try in (1, 2):
                try:
                    _merged = await _merge_add_leg(
                        pool, row_id, _add["id"], filled, fill_price, spent,
                        usd, result.get("order_id"), _add["trade_id"],
                        json.loads(_receipt), len(_add["adds"]) + 1,
                        tx_hash=_add.get("tx_hash"),
                        his_price=_add.get("his_price"))
                except Exception:  # noqa: BLE001 — the fill is paid for: say so loudly
                    log.exception("ADD MERGE FAILED (try %s) for row %s into %s",
                                  _try, row_id, _add["id"])
                if _merged:
                    break
                try:
                    _st = await pool.fetchval(
                        "SELECT status FROM live_orders WHERE id = $1 "
                        "/* add-standing */", _add["id"])
                except Exception:  # noqa: BLE001 — unreadable: treat as live
                    _st = "filled"
                if _st != "filled":
                    break
                await asyncio.sleep(0.2)
            if _merged:
                log.warning("ADD MERGED %s: +%s @ %s ($%.2f) into row %s "
                            "(leg %s of %s)", username, filled, fill_price,
                            spent, _add["id"], len(_add["adds"]) + 1,
                            MAX_ADDS_PER_MARKET)
            elif _st in _ADD_STANDING_LIVE:
                try:
                    await pool.execute(
                        "UPDATE live_orders SET raw = $2::jsonb WHERE id=$1",
                        row_id, json.dumps({**json.loads(_receipt),
                                            "add_of": _add["id"]}, default=str))
                    await _record_add_leg_orphan(
                        pool, row_id, _add["id"], filled, fill_price, spent,
                        result.get("order_id"),
                        f"standing row was '{_st}' when the leg filled")
                    _named = True
                except Exception:  # noqa: BLE001 — fall back to the fill record
                    log.exception("ADD LEG could not be named on row %s",
                                  row_id)
            else:
                log.error("ADD LEG STANDS ALONE: row %s could not merge "
                          "into %s (standing row is '%s'); recorded as its "
                          "own fill", row_id, _add["id"], _st)
        if not _merged and not _named:
            await pool.execute(
                """
                UPDATE live_orders
                SET status=$2, order_id=$3, filled_shares=$4, fill_price=$5,
                    filled_usd=$6, raw=$7::jsonb, error=$8
                WHERE id=$1
                """,
                row_id,
                # UNKNOWN STAYS 'submitting'. A rest attempt whose venue
                # state could not be established keeps the row open under
                # ITS order id: never-add refuses to stack a second bid on
                # the market, the sweep cannot re-qualify the trade, and
                # _reap_stale_submitting reconciles it against the venue.
                # Writing 'unfilled' here would erase all three protections.
                ("submitting" if result.get("status") == "rest_unknown"
                 else "filled" if result["ok"] and filled > 0
                 else "unfilled"),
                result.get("order_id"), filled, fill_price, spent,
                # the send/reply stamps ride beside the venue receipt (see
                # the IOC call site); absent on any path that never sent
                _receipt,
                (f"OVERSPEND: asked ${usd:.2f}, filled ${spent:.2f}"
                 if overspent else
                 (result.get("raw") or {}).get("why")
                 if result.get("status") == "rest_unknown" else
                 f"add-unmerged: standing row #{_add['id']} left 'filled' "
                 f"while this leg was in flight; the leg stands alone"
                 if _add and result["ok"] and filled > 0 else
                 None if result["ok"] else str(result.get("raw"))[:300]),
            )
        log.info("LIVE order [%s] %s: %s %.2f shares @ %.3f (his %.3f)",
                 venue, "FILLED" if result["ok"] and filled > 0 else "unfilled",
                 payload.get("whale_username"), filled, fill_price or limit, his_price)
        # LANE STAMP, SEPARATE AND BEST-EFFORT. Deliberately not in the
        # UPDATE above: that statement is the fill record, and a
        # database that has not yet applied migration 041 would fail
        # it on the missing column -- turning a real, paid-for fill into
        # an 'error' row. The stamp can be lost; the fill cannot.
        try:
            await pool.execute(
                "UPDATE live_orders SET lane=$2 WHERE id=$1",
                row_id, locals().get("_lane", "ioc"))
        except Exception as exc:  # noqa: BLE001
            log.warning("lane stamp skipped for row %s (%s) — migration "
                        "041 not applied yet?", row_id, type(exc).__name__)
        # t=0 OF THE PRICE PATH. The pre-trade quote this path already
        # read is the true post-impact baseline the sampler's curve
        # differences against; the sampler itself can only see the row
        # seconds later, and a late baseline biases every delta toward
        # flat. Measurement only, after the money is done, best-effort.
        try:
            if locals().get("_ask") is not None:
                await pool.execute(
                    "INSERT INTO price_path (row_id, t_s, ask) VALUES ($1, 0, $2) "
                    "ON CONFLICT (row_id, t_s) DO NOTHING",
                    row_id, float(_ask))
        except Exception:  # noqa: BLE001 — table absent until 042
            pass
        if _echo_args and result["ok"] and filled > 0:
            # Hand the probation lock to the echo (see
            # _echo_then_release_short) and stop owning it here, so the
            # finally below does not release it out from under the
            # verdict we are waiting on.
            _spawn_echo(pool, row_id, *_echo_args, shadow=False,
                        owns_short_lock=_short_probation_held,
                        **_echo_kw)
            _short_probation_held = False
    except Exception as exc:  # noqa: BLE001 — record, never crash ingestion
        log.exception("live order failed for trade %s", payload.get("id"))
        await pool.execute(
            "UPDATE live_orders SET status='error', error=$2 WHERE id=$1",
            row_id, str(exc)[:300],
        )
    finally:
        if _first_fill_gate and _FIRST_FILL_LOCK.locked():
            _FIRST_FILL_LOCK.release()
        # Released on EVERY path out, including the refusals and the
        # exception ones. A probation lock that leaks stops shorts
        # permanently and would look exactly like the ban it replaced.
        if _short_probation_held and _SHORT_LOCK.locked():
            _SHORT_LOCK.release()
