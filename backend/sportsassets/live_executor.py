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
import json
import logging
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


async def execute_copy(payload: dict) -> None:
    """Fresh-detection execution entry point, spawned by the ingestion
    pipeline ALONGSIDE (not inside) the measurement probe.

    Deliberately no 120s probe gate here: MAX_REACTION_S protects the
    probe cohort's measurement integrity, but for EXECUTION the
    pipeline's 600s fresh gate plus the FOK at his+2% already bound
    staleness — a 2-10 minute detection is a copy the sleeve previously
    forfeited to the 10-minute sweep for no risk reason."""
    try:
        # copy_probe_enabled was the fresh-copy path's de facto master
        # switch while execution lived inside the probe; it stays one
        # (same-day review finding — an operator flipping it expects
        # copies to stop, and silently un-braking a kill dial is worse
        # than the naming being imperfect).
        if not settings().copy_probe_enabled:
            return
        # HIS SELL IS A SIGNAL TOO (owner order 2026-08-25). This path
        # returned early on anything that was not a BUY, so his exits
        # were discarded at the door — we copied entries and then held
        # to resolution while he took the move. mirror_exit carries its
        # own gates; it is not a bypass of this one.
        if payload.get("side") == "SELL":
            await mirror_exit(payload)
            return
        if payload.get("side") != "BUY":
            return
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
_SIBLING_CACHE_AT = 0.0
_SIBLING_TTL_S = 300.0


async def _sibling_from_positions(pool, asset: str) -> str:
    """The complementary token id per the VENUE, or ''.

    Never a guess. An absent map, an absent key, or an unreadable row
    all return '' and the caller refuses exactly as it did before.
    """
    global _SIBLING_CACHE, _SIBLING_CACHE_AT
    import time as _t

    now = _t.monotonic()
    if now - _SIBLING_CACHE_AT > _SIBLING_TTL_S:
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


async def mirror_exit(payload: dict) -> None:
    """The whale sold. Sell our copy of the same market.

    Owner order 2026-08-25: "copy both buys and sells at a proportional
    rate", and the worked example — he buys $5,000, sells for $7,500,
    then re-enters at $60. Copying only the entry leaves us holding to
    resolution while he banks the move, which is a different and worse
    strategy than his. Mirroring both legs makes our net position on a
    market track his automatically: no house-money special case is
    needed, because after his exit our exposure is proportionally the
    same as his.

    V1 MIRRORS FULL EXITS ONLY. If he closes 95%+ of his position we
    close ours; a partial scale-out is logged and skipped. Partial
    position accounting is where errors compound — the fraction has to
    be right against a position we track across many fills — and his
    "cash out" behaviour is a full exit, which is the case that carries
    the value. The narrower version is the one I can be sure of.

    Inert until whale sells are ingested: every copied whale currently
    shows zero sells all time, so this fires zero times today. It is
    built now so that the moment the data lands the sell leg is already
    correct rather than being written in a hurry against live money.
    """
    if payload.get("side") != "SELL":
        return _exit_stop("mx_not_a_sell")
    if not settings().copy_probe_enabled or copy_halted():
        return _exit_stop("mx_halted")
    username = (payload.get("whale_username") or "").lower()
    if username not in _whale_set("LIVE_VERIFIED_WHALES"):
        return _exit_stop("mx_whale_not_verified", whale=username)
    asset = str(payload.get("asset") or "")
    if not asset:
        return _exit_stop("mx_no_asset")
    pool = await get_pool()
    if await overspend_halt(pool):
        return _exit_stop("mx_overspend_halt")
    # STAMPED AFTER THE HALT GATE, NOT BEFORE (2026-08-25, adversarial
    # review). It was counted before overspend_halt and before the
    # query, so a sleeve stopped by a tripped breaker still reported
    # every exit as "reaching the position lookup" — and the endpoint's
    # verdict reads that counter to decide whether exits are arriving
    # at all. A halted system would have looked like a working one with
    # nothing to sell.
    _exit_stop("mx_reached_position_lookup", whale=username, asset=asset)
    row = await pool.fetchrow(
        "SELECT id, us_market_slug, filled_shares::float8 AS qty, "
        "       fill_price::float8 AS entry, "
        f"      {ORDER_INTENT_SQL} AS intent "
        "FROM live_orders "
        "WHERE asset = $1 AND lower(COALESCE(whale_username,'')) = $2 "
        "  AND status = 'filled' AND us_market_slug IS NOT NULL "
        "ORDER BY placed_at DESC LIMIT 1", asset, username)
    if row is None or (row["qty"] or 0) <= 0:
        # WE NEVER COPIED HIS ENTRY. At a 0.55% fill rate this is the
        # expected majority outcome and it is NOT an exit-path defect:
        # there is nothing to sell. Counting it separately is what
        # stops "mirror_exit never fired" from being read as a broken
        # exit path when it is actually a coverage number.
        return _exit_stop("mx_no_position_of_ours", whale=username,
                          asset=asset)
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
            return _exit_stop("mx_bad_supplied_fraction", frac=supplied)
    else:
        if bought <= 0:
            return _exit_stop("mx_no_ledger_position", asset=asset)
        closed_frac = min(sold / bought, 1.0)
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
        return _exit_stop("mx_below_floor", whale=username,
                          closed_frac=round(closed_frac, 4))
    from . import pmus

    us_slug = row["us_market_slug"]
    # ATOMIC CLAIM. Five complement fills inside one second spawn five
    # execute_copy tasks, and mirror_exit runs outside _COPY_SEM — so
    # without this every one of them reads the same 'filled' row and
    # every one issues a sell. Exactly one caller gets a row back here.
    claimed = await pool.fetchval(
        "UPDATE live_orders SET status='exiting' "
        "WHERE id=$1 AND status='filled' RETURNING id", row["id"])
    if claimed is None:
        log.info("MIRROR-EXIT %s already claimed by another task", us_slug)
        return _exit_stop("mx_already_claimed", slug=us_slug)
    try:
        held, _avg = await _pm_held(us_slug)
        ours = min(int(row["qty"]), held)
        # His fraction OF OUR position — the proportional relationship.
        qty = int(ours * closed_frac)
        if closed_frac >= FULL_EXIT_FRAC:
            qty = ours          # he is out; so are we, to the share
        if qty <= 0:
            await _release_exit_claim(pool, row["id"])
            # Our ledger says filled, the VENUE says we hold nothing.
            # A disagreement between the two is its own class of
            # problem and must never be filed under "no position".
            return _exit_stop("mx_venue_holds_nothing", slug=us_slug,
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
        _full = closed_frac >= FULL_EXIT_FRAC
        limit = None
        if not _full:
            bid = await asyncio.to_thread(pmus.slug_bid, us_slug)
            if bid is None or not (0 < bid < 1):
                log.warning("MIRROR-EXIT no bid for %s — partial exit "
                            "deferred rather than sold blind", us_slug)
                await _release_exit_claim(pool, row["id"])
                return _exit_stop("mx_no_bid_for_partial", slug=us_slug)
            limit = sell_limit_price(bid)
    except Exception:
        await _release_exit_claim(pool, row["id"])
        raise
    try:
        if _full:
            result = await asyncio.to_thread(
                pmus.close_position, us_slug,
                slippage_bips=EXIT_SLIPPAGE_BIPS)
        else:
            result = await asyncio.to_thread(
                pmus.submit_fok, us_slug, limit, qty, True,
                "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL")
    except Exception:
        await _release_exit_claim(pool, row["id"])
        raise
    filled = float(result.get("filled_shares") or 0)
    px = result.get("fill_price")
    if not (result.get("ok") and filled > 0):
        log.warning("MIRROR-EXIT unfilled %s: %s", us_slug,
                    str(result.get("raw"))[:160])
        await _release_exit_claim(pool, row["id"])
        return _exit_stop("mx_venue_unfilled", slug=us_slug)
    entry = row["entry"] or 0
    # SHORT EXITS WERE SIGN-INVERTED. See realized_pnl: on a short the
    # venue's price field names the LONG leg, so the realized amount is
    # (entry - exit), not (exit - entry). Six shorts filled before the
    # branch was banned and we may still hold them.
    pnl = realized_pnl(entry, px, filled, row["intent"])
    # PARTIAL EXITS MUST NOT RETIRE THE WHOLE POSITION. Writing
    # 'cashed_out' after selling a fraction would orphan the remainder:
    # the settlement sweep targets status='filled', so the shares we
    # still hold would never be graded, and copy_sweep's blocking list
    # would stop us re-entering a market we are still in.
    remaining = max(0, int(row["qty"]) - int(filled))
    if remaining > 0 and closed_frac < FULL_EXIT_FRAC:
        await pool.execute(
            "UPDATE live_orders SET status='filled', "
            "filled_shares=$2, pnl=COALESCE(pnl,0)+$3 WHERE id=$1",
            row["id"], remaining, pnl or 0)
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
    _exit_stop("mx_SOLD", whale=username, slug=us_slug,
               shares=int(filled), pnl=pnl or 0,
               full=bool(closed_frac >= FULL_EXIT_FRAC))
    log.warning("MIRROR-EXIT %s %s: sold %d of %d @ %.3f (entry %.3f) "
                "pnl %.2f — mirroring his %.1f%% exit, %d left",
                username, us_slug, int(filled), int(row["qty"]),
                float(px or limit or 0), entry, pnl or 0,
                closed_frac * 100, remaining)


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
    "0x076daa87,rn1,ferrarichampions2026")


def _whale_set(env_name: str) -> set[str]:
    raw = os.getenv(env_name, VERIFIED_PROFITABLE_DEFAULT)
    return {w.strip() for w in raw.lower().split(",") if w.strip()}


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


OVERSPEND_TOLERANCE = 1.01  # a cent of rounding on a whole-unit fill


def is_overspend(requested_usd: float, filled_shares: float,
                 fill_price: float | None,
                 intent: str | None = None) -> bool:
    r = overspend_ratio(requested_usd, filled_shares, fill_price, intent)
    return r is not None and r > OVERSPEND_TOLERANCE


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
                   ::float8 AS day,
               COALESCE(sum(filled_usd), 0)::float8 AS total
        FROM live_orders
        WHERE COALESCE(whale_username, '') NOT IN ('manual', 'underdog')
        """
    )
    return (cfg.live_max_daily_usd - row["day"], cfg.live_max_total_usd - row["total"])


async def _is_paused(pool) -> bool:
    val = await pool.fetchval("SELECT value FROM ingestion_state WHERE key=$1", PAUSE_KEY)
    if val is None:
        return False
    parsed = json.loads(val) if isinstance(val, str) else val
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
    if all(ctx.get(k) for k in ("market_slug", "outcome")):
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
COPY_CUT_WHALES = frozenset({"swisstony", _W2C33, "homerunhazard"})
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
PER_FILL_BY_WHALE = {"rn1": 250.00, "swisstony": 0.00,
                     _W2C33: 0.00,
                     # CUT 2026-08-25: -0.14% on $27.56M merge-inclusive.
                     "homerunhazard": 0.00,
                     "kch123": 150.00,
                     "ferrarichampions2026": 250.00,
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


def copy_limit_price(whale_username: str | None, his_price: float,
                     fresh: bool = True) -> float:
    """FOK limit for a copy: his price floored to the venue tick —
    same-or-better — plus the per-whale capture tolerance (RN1 +2c).

    fresh=False (the sweep's reclaims, audit 2026-08-21): NO tolerance.
    The +2c evidence base is fresh-capture misses grading +5.3%; paying
    2c over a days-old entry price is adverse selection, not capture."""
    import math

    limit = math.floor(round(his_price * 100, 6)) / 100.0
    if fresh and (whale_username or "").lower() == "rn1" \
            and RN1_TOL_CENTS > 0:
        limit = min(0.99, round(limit + RN1_TOL_CENTS / 100.0, 2))
    return limit


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
    per = (1.0 - px) if _use_short_math(intent) else px
    return round(filled_shares * per, 2)


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
    2026-08-20). A 0.00 cell stays a block."""
    w = (whale_username or "").lower()
    base = None
    if slug:
        from .copy_sports import sport_of

        ov = PER_FILL_BY_WHALE_SPORT.get((w, sport_of(slug)))
        if ov is not None:
            base = ov
    if base is None:
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
        spent = float(await pool.fetchval(
            "SELECT COALESCE(sum(filled_usd), 0) FROM live_orders "
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


# Tennis league code translation, feed -> US venue. The US venue splits
# ITF by tour ('itfwo' women / 'itfme' men) where the feed says 'itf';
# unknown codes are enumerated (a wrong guess is a 404, never a trade).
_TENNIS_LEAGUES = {"atp", "wta", "itf", "itfwo", "itfme", "chal"}
_TENNIS_US_CODES = {"itf": ["itfwo", "itfme", "itf"],
                    "chal": ["chal", "atpchal"]}


def _abbrev_player(name: str) -> str | None:
    """US-venue tennis token: first 3 of first name + first 3 of last.
    Proven against live fills — 'Dusan Lajovic' is 'duslaj' in
    aec-atp-duslaj-benbon-2026-08-11, 'Rafael Jodar' is 'rafjod',
    'Sinja Kraus' is 'sinkra'. Unicode folds ('João' -> 'joa');
    single-token names refuse (no grammar evidence for them)."""
    import re as _re
    import unicodedata as _ud

    folded = _ud.normalize("NFKD", name or "").encode(
        "ascii", "ignore").decode().lower()
    toks = _re.findall(r"[a-z]+", folded)
    if len(toks) < 2 or len(toks[0]) < 3 or len(toks[-1]) < 3:
        return None
    return toks[0][:3] + toks[-1][:3]


def _tennis_candidates(title: str | None, global_slug: str) -> list[str]:
    """US aec- candidates for a tennis match, built from the PLAYER
    NAMES in the title — the feed's slug uses surnames while the US
    grammar abbreviates 'First Last' to 6 chars, so slug-to-slug
    translation cannot work for tennis (1,730 ITF + 1,249 ATP + 623
    WTA moneylines dead in the funnel, 2026-08-13). Both player orders
    are generated (home/away order is the venue's choice, not the
    title's) and the outcome-similarity floor downstream remains the
    side authority — a colliding abbreviation still has to present the
    right player NAME to be ordered."""
    import re as _re

    s = (global_slug or "").lower()
    m = _re.search(r"\d{4}-\d{2}-\d{2}", s)
    head = [t for t in s[:m.start()].strip("-").split("-") if t] if m \
        else []
    if not m or not head or head[0] not in _TENNIS_LEAGUES:
        return []
    date = m.group(0)
    # LAST colon: 'Tennis: ATP Cincinnati: A vs B' keeps only the
    # matchup (review 2026-08-13 — a first-colon split swallowed the
    # tournament word into the first player's token).
    body = (title or "").rsplit(":", 1)[-1]
    # Doubles refuse outright: 'A / B vs C / D' has no singles grammar,
    # and a fabricated token is a live probe into the 6-char slug space.
    if "/" in body:
        return []
    players = _re.split(r"\s+vs\.?\s+", body, flags=_re.I)
    if len(players) != 2:
        return []
    a, b = (_abbrev_player(p) for p in players)
    if not a or not b or a == b:
        return []
    codes = list(_TENNIS_US_CODES.get(head[0], [head[0]]))
    if head[0] == "itf":
        # Tour hint from the title ('ITF W15 ...' / 'Women' vs 'M25' /
        # 'Men') puts the likelier code first; both are still tried.
        tl = (title or "").lower()
        if _re.search(r"\bm\d{2}\b|\bmen\b", tl) and "women" not in tl:
            codes = ["itfme", "itfwo", "itf"]
    out: list[str] = []
    for lg in codes:
        out.append(f"aec-{lg}-{a}-{b}-{date}")
        out.append(f"aec-{lg}-{b}-{a}-{date}")
    return out


def _us_slug_candidates(global_slug: str, outcome: str) -> list[str]:
    """US-venue slug candidates for a global market, most exact first.

    The global feed's slugs are kindless and league-led
    ('atp-ruud-fonseca-2026-08-07'); the US venue keys the same game as
    'atc-<league>-<a>-<b>-<date>-<side>' (per-side team contract) and
    'aec-<league>-<a>-<b>-<date>' (the two-outcome event contract). The
    side code is chosen only when exactly ONE of the slug's two codes
    matches the outcome name — ambiguity falls through to the aec form,
    whose own outcome-similarity floor disambiguates."""
    import re as _re

    out: list[str] = []
    s = (global_slug or "").lower()
    m = _re.search(r"\d{4}-\d{2}-\d{2}", s)
    if m:
        head = [t for t in s[:m.start()].strip("-").split("-") if t]
        if len(head) == 3:
            lg, a, b = head
            date = m.group(0)
            ol = (outcome or "").lower()
            words = ol.split()

            def _hits(code: str) -> bool:
                return code in ol or any(w.startswith(code)
                                         or code.startswith(w)
                                         for w in words)

            sides = [c for c in (a, b) if _hits(c)]
            if len(sides) == 1:
                out.append(f"atc-{lg}-{a}-{b}-{date}-{sides[0]}")
            out.append(f"aec-{lg}-{a}-{b}-{date}")
    if s:
        out.append(s)
    return out


async def _reap_stale_submitting(pool) -> None:
    """Terminal-ize 'submitting' rows whose process died mid-order
    (audit 2026-08-21). A phantom submitting row is load-bearing in two
    bad ways: copy rows hold the one-fill-per-asset claim forever (the
    asset can never be copied again), and manual rows would wedge the
    migration-023 in-flight index. Ten minutes is far past any real
    submit (mapping + preview + IOC is tens of seconds); if the orphaned
    venue order did fill, the money is at the venue either way and the
    row's error text says exactly what to reconcile."""
    await pool.execute(
        "UPDATE live_orders SET status = 'error', "
        "error = 'stale submitting row reaped — process died mid-order; "
        "reconcile against the venue account' "
        "WHERE status = 'submitting' "
        "AND placed_at < now() - interval '10 minutes'")


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
    day_spent = float(await pool.fetchval(
        "SELECT COALESCE(sum(filled_usd), 0) FROM live_orders "
        "WHERE whale_username = 'manual' "
        "AND placed_at > now() - interval '24 hours'") or 0)
    if day_spent + usd > MANUAL_DAILY_USD:
        return {"ok": False,
                "error": (f"manual day budget exhausted "
                          f"(${day_spent:.2f} of ${MANUAL_DAILY_USD:.0f} "
                          "in 24h)")}
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
    bid = await asyncio.to_thread(pmus.slug_bid, us_slug)
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
        None if result["ok"] else str(result.get("raw"))[:300], pnl)
    log.info("MANUAL SELL %s: %.0f/%d @ %.2f (bid %.2f) proceeds %.2f",
             status, filled, qty, fill_price or limit, bid, proceeds)
    return {"ok": bool(result["ok"] and filled > 0), "row_id": row_id,
            "filled_shares": filled, "avg_price": fill_price,
            "proceeds_usd": proceeds, "quoted_bid": bid,
            "limit_price": limit, "pnl": pnl, "held": held,
            "detail": ("sold" if filled > 0 else
                       "no fill at the protective limit — the bid moved; "
                       "nothing was sold")}


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
    if mapping_src in ("exact", "desk_exact", "desk_exact_side",
                       "derivative_exact", "spread_exact"):
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
                            mapping_src: str | None = None) -> None:
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
    if not shadow and intent:
        try:
            net = await asyncio.to_thread(pmus_mod.position_side, us_slug)
            if net is not None and net != 0:
                want_long = intent == "ORDER_INTENT_BUY_LONG"
                got_long = net > 0
                if want_long != got_long:
                    verdict = "mismatch"
                    detail = (f"POSITION SIDE WRONG: sent {intent} but "
                              f"hold netPosition={net}")
                elif verdict == "unverified":
                    verdict, detail = "ok", f"position sign agrees ({net})"
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
    state_key = "side_echo_shadow" if shadow else "side_echo_last"
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


def _spawn_echo(pool, row_id: int, us_slug: str, outcome: str | None,
                his_title: str | None, *, shadow: bool,
                his_slug: str | None = None,
                intent: str | None = None,
                mapping_src: str | None = None) -> None:
    """Fire-and-forget echo with a strong task ref (a bare create_task
    can be garbage-collected mid-flight)."""
    t = asyncio.create_task(_side_echo_verify(
        pool, row_id, us_slug, outcome, his_title, shadow=shadow,
        his_slug=his_slug, intent=intent, mapping_src=mapping_src))
    _ECHO_TASKS.add(t)
    t.add_done_callback(_ECHO_TASKS.discard)


async def maybe_execute(payload: dict, reaction: float | None) -> None:
    """Called on every fresh detection (after the paper trade). All guards
    re-checked here; failure of any guard is a silent no-op or logged skip."""
    if COPY_MODE == "off":
        return
    cfg = settings()
    # MASTER KILL SWITCH, AT THE COMMON GATE (leak-hunt round 3,
    # 2026-08-24): copy_probe_enabled was checked only in execute_copy,
    # the FRESH-detection entry point, so copy_sweep's reclaim path
    # called maybe_execute directly and kept placing real orders after
    # an operator disabled copying. Every copy crosses this function;
    # the switch belongs here.
    if not cfg.copy_probe_enabled:
        return
    # EMERGENCY HALT rides the same common gate as the master kill, for
    # the same reason: the reclaim path reaches maybe_execute directly.
    # Fail-closed by default — see COPY_HALT_REASON.
    if copy_halted():
        log.warning("LIVE refused: %s", COPY_HALT_REASON)
        return
    venue = active_venue()
    if venue is None:
        return
    username = (payload.get("whale_username") or "").lower()
    if payload.get("side") != "BUY" or username not in cfg.source_whales():
        return
    # TRUEEDGE CUT (owner order 2026-08-24): a whale whose full detected
    # book is negative at his OWN prices is not copyable at any speed.
    # First of three independent blocks (this gate, the 0.00 clip, the
    # premap-live allowlist) — any one of them alone stops the dollars.
    if username in COPY_CUT_WHALES:
        return
    his_notional = float(payload.get("notional") or 0)
    his_price = float(payload.get("price") or 0)
    if his_notional <= 0 or not (0 < his_price < 1):
        return

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
        await mirror_exit({**payload, **_exit, "side": "SELL"})
        return
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
        return
    if await _is_paused(pool):
        return
    # Cell-level copy policy (owner directive 2026-08-06): each source
    # whale is copied ONLY in its statistically proven sport x market-type
    # x entry-band cells, derived from fill-level forensic data. Fails
    # closed on anything unrecognized. The market identity must be
    # RESOLVED before it is judged: fresh Path-A detections reach here
    # before enrichment (the payload carries no slug — it lands in the
    # trades row afterwards), and gating on the raw payload fed the
    # fail-closed parser an empty string, silently dropping every fresh
    # copy for ~3h on 2026-08-06 evening.
    from .copy_sports import copy_allowed
    ctx = await _market_context(pool, payload)
    for k, v in ctx.items():
        if v and not payload.get(k):
            payload[k] = v
    if not copy_allowed(username, payload.get("market_slug")
                        or payload.get("event_slug") or "",
                        price=payload.get("price")):
        return
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
        return
    # Capital turnover (owner, 2026-08-04): fresh detections on games more
    # than ~a day out are DEFERRED — no audit row is written, so the 6h
    # sweep re-candidates them once the game is inside the window. A small
    # bankroll compounds by settling, not by holding Thursday's ticket
    # since Monday.
    import re as _re
    from datetime import date, timedelta

    mslug = payload.get("market_slug") or payload.get("event_slug") or ""
    mdate = _re.search(r"\d{4}-\d{2}-\d{2}", mslug)
    if mdate:
        try:
            y, mo, d = map(int, mdate.group(0).split("-"))
            if date(y, mo, d) > date.today() + timedelta(days=1):
                return
        except ValueError:
            pass
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
        return
    # The rolling-loss breaker gates every copy BEFORE any row is
    # written: realized copy P&L (settled + cashed out, copies only)
    # over the last 24h at or past -$1500 pauses the sleeve. The
    # query answers from Postgres so a deploy cannot amnesia it.
    try:
        lost_24h = float(await pool.fetchval(
            "SELECT COALESCE(sum(pnl), 0) FROM live_orders "
            "WHERE settled_at > now() - interval '24 hours' "
            "AND status IN ('settled', 'cashed_out') "
            "AND COALESCE(whale_username, '') NOT IN "
            "('manual', 'underdog')") or 0)
    except Exception:  # noqa: BLE001 — an unreadable ledger must not
        lost_24h = 0.0  # be the thing that blocks copies; caps below
        # and the venue guards still bound every order.
    if lost_24h <= -PMUS_LOSS_BREAKER_USD:
        log.warning(
            "LOSS BREAKER: copy sleeve realized %.2f in 24h "
            "(threshold -%.0f) — copying paused until the window "
            "rolls off", lost_24h, PMUS_LOSS_BREAKER_USD)
        return
    day_room, total_room = await _caps_room(pool)
    if COPY_MODE == "penny_trial":
        # In penny_trial the TRIAL knobs are the authority, not the config
        # caps (owner 2026-08-05: per-trade limits only; day and lifetime
        # ceilings are env knobs, default unlimited). Deriving spend from
        # the config-cap rooms keeps _caps_room's single query.
        day_spent = cfg.live_max_daily_usd - day_room
        total_spent = cfg.live_max_total_usd - total_room
        day_room = PENNY_TRIAL_DAILY_USD - day_spent
        total_room = PENNY_TRIAL_TOTAL_USD - total_spent
        # PROBE DAY CAP (owner authorization 2026-08-24 evening): the
        # resume is a bounded proof, so the day's copy spend is bounded
        # too — an unimagined defect costs this much, not an open-ended
        # amount. Raise it deliberately once real fills verify.
        if PROBE_DAY_USD > 0:
            day_room = min(day_room, PROBE_DAY_USD - day_spent)
    if day_room <= 0 or total_room <= 1:
        log.warning("live caps exhausted (day room %.2f, total room %.2f) — skipping",
                    day_room, total_room)
        return

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
            return
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
            _arms = [per * CONVICTION_ANCHOR_FRAC * _conv, per]
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
            return
        usd = round(shares * limit, 2)
        # Dust floor: at a mirror ratio his small probes size to a few
        # dollars, where spread and fees eat the edge.
        if usd < COPY_MIN_CLIP_USD:
            return
        if usd > day_room:
            return
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
            return

    try:
        row_id = await pool.fetchval(
            """
            INSERT INTO live_orders (trade_id, whale_username, asset, condition_id, side,
                                     his_price, reaction_s, limit_price, requested_usd,
                                     requested_shares, status, venue)
            VALUES ($1,$2,$3,$4,'BUY',$5,$6,$7,$8,$9,'submitting',$10)
            ON CONFLICT (trade_id) DO UPDATE
              SET status='submitting', error=NULL
              WHERE live_orders.status IN ('rejected', 'unfilled', 'error')
            RETURNING id
            """,
            payload.get("id"), payload.get("whale_username"), str(payload["asset"]),
            payload.get("condition_id"), his_price, reaction, limit, usd, shares, venue,
        )
    except Exception as exc:  # noqa: BLE001
        # The one-fill-per-asset index catching a concurrent duplicate is
        # the guard WORKING, not an error.
        if "live_orders_one_fill_per_asset" in str(exc):
            return
        raise
    if row_id is None:
        return  # duplicate detection — never double-order one source trade

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
            if mapping is None and mtype == "moneyline":
                # Tennis first (owner order 2026-08-13): the feed's
                # surname slugs can never hit the US first3+last3
                # player grammar, so tennis candidates come from the
                # TITLE's player names. Non-tennis slugs add nothing.
                cands = (_tennis_candidates(ctx.get("market_title"),
                                            src_slug)
                         + _us_slug_candidates(src_slug,
                                               ctx.get("outcome") or ""))
                try:
                    mapping = await asyncio.wait_for(
                        asyncio.to_thread(pmus.resolve_market_exact,
                                          cands, ctx.get("outcome")),
                        timeout=_EXACT_BOX_S)
                except asyncio.TimeoutError:
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
                    mapping = None
            if mapping_src is None:
                mapping_src = "exact" if mapping is not None else None
            if mapping is None:
                mapping = await asyncio.to_thread(
                    pmus.resolve_market, ctx.get("market_slug"), ctx.get("event_slug"),
                    ctx.get("market_title"), ctx.get("event_title"), ctx.get("outcome"),
                )
                if mapping is not None:
                    mapping_src = "fuzzy"
            if mapping is None:
                diag = getattr(pmus.resolve_market, "last_diag", "") or ""
                await pool.execute(
                    "UPDATE live_orders SET status='rejected', error=$2 WHERE id=$1",
                    row_id, ("unmapped: " + diag)[:300] if diag
                    else "no verified Polymarket US market for this outcome",
                )
                log.info("LIVE (US) unmapped: %s / %s", ctx.get("market_title"),
                         ctx.get("outcome"))
                return
            await pool.execute(
                "UPDATE live_orders SET us_market_slug=$2 WHERE id=$1",
                row_id, mapping["market_slug"],
            )
            # SIDE-ECHO CIRCUIT (leak-hunt find 2026-08-24): the echo's
            # auto-requarantine writes the DB switches, but the env
            # overrides (LIVE_PREMAP=on / LIVE_MAPPING_QUARANTINE=off)
            # short-circuit BEFORE those DB reads — env-armed operation
            # would sail past a confirmed wrong-side mismatch. This
            # circuit deliberately has NO env override: tripped means
            # every copy mapping refuses until an admin explicitly
            # resets it (POST /api/admin/side-echo-reset). Unreadable
            # state fails safe.
            _q_slug0 = str(mapping.get("market_slug") or "").lower()
            try:
                _tv = await pool.fetchval(
                    "SELECT value FROM ingestion_state WHERE key=$1",
                    "side_echo_tripped")
                _tripped = (bool(json.loads(_tv) if isinstance(_tv, str)
                                 else _tv) if _tv is not None else False)
            except Exception:  # noqa: BLE001 — fail safe: refuse
                _tripped = True
            if _tripped:
                await pool.execute(
                    "UPDATE live_orders SET status='rejected', error=$2 "
                    "WHERE id=$1", row_id,
                    "side-echo tripped: confirmed wrong-side evidence — "
                    "copying halted pending admin review "
                    f"(slug={_q_slug0[:120]})")
                log.warning("LIVE (US) refused: side-echo circuit "
                            "tripped (%s)", _q_slug0)
                return
            # PROFITABILITY HOLD (leak-hunt find 2026-08-24): swisstony's
            # exclusion previously lived only in the premap-live
            # allowlist, which is consulted ONLY while the quarantine is
            # armed — lifting the quarantine (a mapping-fidelity action)
            # would silently re-arm his $300 clip with no profitability
            # decision made. The hold is its own gate, independent of
            # quarantine state: LIVE_HOLD_WHALES refuses with an audit
            # reason until a whale's paper cohort at the new sub-second
            # detection certifies positive. It defaulted to swisstony
            # until 2026-08-24 evening, when TRUEEDGE-FAST graded him
            # positive on detections inside 5s and the owner order
            # lifted it; the default is now EMPTY, and the env re-arms a
            # hold on any whale without a deploy. A held row keeps its
            # mapping — fidelity samples continue.
            # VERIFIED-ONLY, INDEPENDENT OF QUARANTINE (leak-hunt round
            # 2): the allowlist below lived INSIDE the `if _q_on` branch,
            # so lifting the quarantine — a mapping-fidelity action —
            # silently dropped the profitability allowlist with it and
            # opened the lane to every non-cut whale. Only whales the
            # TRUEEDGE table verified profitable may spend, whatever the
            # quarantine says. LIVE_VERIFIED_WHALES widens it
            # deliberately; empty disables the gate for a full resume.
            # The verified set is exactly TRUEEDGE cf_total > 0:
            # homerunhazard (+26,076), 0x076 (+6,189), swisstony
            # (+11,895). swisstony is verified but HELD below, pending
            # his paper cohort at the new sub-second detection — the two
            # gates answer different questions and must stay separate.
            _verified = _whale_set("LIVE_VERIFIED_WHALES")
            if _verified and username not in _verified:
                await pool.execute(
                    "UPDATE live_orders SET status='rejected', error=$2 "
                    "WHERE id=$1", row_id,
                    "not verified-profitable: only whales certified by "
                    "the TRUEEDGE counterfactual may spend "
                    f"(slug={_q_slug0[:100]})")
                return
            # HOLD LIFTED for swisstony (owner order 2026-08-24
            # evening: "make sure we are profitable copying SwissTony").
            # The hold's stated condition was his paper cohort grading
            # positive at the NEW detection latency. Measured, on
            # detections inside 5 seconds (TRUEEDGE-FAST):
            #   cf_total    +8,874.90   his edge on the fast book
            #   paper_actual +6,864.52  what OUR fill achieves — POSITIVE
            #   lat_cost     1,983.71   vs 15,063 blended
            # The -604.88 that held him was an artifact of averaging in
            # months of minutes-late polling; at chain speed we capture
            # 77% of his edge. He now trades under the 15s staleness cap,
            # so a signal we did NOT catch fast is still refused — the
            # condition that makes him profitable is enforced, not hoped
            # for. LIVE_HOLD_WHALES re-arms a hold without a deploy.
            _held = {w.strip() for w in
                     os.getenv("LIVE_HOLD_WHALES", "")
                     .lower().split(",") if w.strip()}
            if username in _held:
                await pool.execute(
                    "UPDATE live_orders SET status='rejected', error=$2 "
                    "WHERE id=$1", row_id,
                    "hold: pending paper certification at the new "
                    "detection latency (TRUEEDGE 2026-08-24) "
                    f"(slug={_q_slug0[:120]})")
                return
            # MAPPING QUARANTINE (owner emergency 2026-08-23): the venue
            # ledger proved a large share of two-outcome copies were held
            # on the WRONG SIDE while the old settlement sweep graded them
            # by the whale's result. Until each path is re-verified against
            # venue truth, only the deterministic US slug-grammar paths may
            # place money: fuzzy-resolved mappings and the aec- (tennis
            # side-slug) class are refused, with the mapping kept on the
            # row for the postmortem. LIVE_MAPPING_QUARANTINE=off lifts.
            _q_slug = str(mapping.get("market_slug") or "").lower()
            # Resume is a DB switch (POST /api/admin/quarantine/off) so
            # the owner's go is one admin call; the env var stays as a
            # hard override in either direction. Default is ON — an
            # unreadable state key must fail safe, not fail open.
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
            # 2026-08-24 05:00 ET: the venue-certified restatement shows
            # EVERY sleeve August-negative at our fills (RN1 -8.6k at
            # 39% wins; the restated ledger now matches the account
            # day-by-day). While the switch is ON, ALL copy mappings
            # refuse — not just the fuzzy/aec classes — pending the
            # owner's morning review. Same switch lifts it.
            # RESUME LEVER (owner order 2026-08-24): while the total
            # quarantine holds, ONLY premap-resolved mappings may trade,
            # and only once the owner flips premap_live (DB switch via
            # POST /api/admin/premap-live/on; env LIVE_PREMAP overrides
            # both ways). Everything else stays refused-but-recorded.
            # PREMAP-LIVE ALLOWLIST (owner order 2026-08-24): the resume
            # lane admits ONLY the whales the TRUEEDGE table verified
            # profitable at their own prices AND at our measured latency.
            # swisstony joins via env (no code change) the moment his
            # paper cohort at the new sub-second detection grades
            # positive; everyone else stays refused-but-recorded.
            _allowed = _whale_set("LIVE_PREMAP_WHALES")
            _premap_ok = False
            if _q_on and mapping_src == "premap" and username in _allowed:
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
                if mapping_src == "premap" and username not in _allowed:
                    _q_reason = ("premap-live: whale not in the "
                                 "verified-profitable set (TRUEEDGE "
                                 "2026-08-24) "
                                 f"(src=premap, slug={_q_slug[:120]})")
                else:
                    _q_reason = ("quarantined: mapping class unverified "
                                 "after wrong-side incident 2026-08-23 "
                                 f"(src={mapping_src}, "
                                 f"slug={_q_slug[:120]})")
                await pool.execute(
                    "UPDATE live_orders SET status='rejected', error=$2 "
                    "WHERE id=$1",
                    row_id, _q_reason)
                # SHADOW CERTIFICATION: a refused PREMAP resolution is
                # free evidence — re-derive it from live venue data and
                # record the verdict. This is the streak the resume
                # decision reads, produced at zero dollars of risk.
                if mapping_src == "premap":
                    _spawn_echo(pool, row_id, mapping["market_slug"],
                                ctx.get("outcome"),
                                ctx.get("market_title"), shadow=True,
                                his_slug=src_slug,
                                intent=mapping.get("intent"))
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
            prior = await pool.fetchval(
                "SELECT 1 FROM live_orders "
                "WHERE us_market_slug = $2 AND id <> $1 "
                "AND status IN ('filled', 'submitting') "
                "AND COALESCE(whale_username, '') <> 'underdog' "
                "AND placed_at > now() - interval '48 hours' LIMIT 1",
                row_id, mapping["market_slug"])
            if prior:
                await pool.execute(
                    "UPDATE live_orders SET status='rejected', error=$2 "
                    "WHERE id=$1", row_id,
                    "never-add: this market was already copied")
                return
            # ONE POSITION PER GAME (owner audit order 2026-08-11
            # evening, superseding the ladder-only rule from the same
            # afternoon): a game is ONE bet. Ladder rungs are the same
            # bet several times; opposite-side moneylines (the Halys+
            # Kwon guaranteed-loss shape — RN1 completes pairs, we must
            # not) are the same bet against itself. The first market a
            # whale entered on a game is copied; every later market on
            # that game — any type, any side, any whale — is refused.
            gk = _us_game_key(mapping["market_slug"])
            if gk is not None:
                # 'underdog' rows excluded: the $2 sleeve buying a
                # game's dog at T-5 must not consume that game's ONE
                # copy slot (owner 2026-08-12 independence order).
                held = await pool.fetch(
                    "SELECT us_market_slug FROM live_orders "
                    "WHERE status IN ('filled', 'submitting') "
                    "AND id <> $1 AND us_market_slug IS NOT NULL "
                    "AND COALESCE(whale_username, '') <> 'underdog' "
                    "AND placed_at > now() - interval '48 hours'",
                    row_id)
                if any(_us_game_key(r["us_market_slug"]) == gk
                       for r in held):
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
            if await asyncio.to_thread(pmus.account_holds,
                                       mapping["market_slug"]):
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
            _tol = 1.0 + float(os.getenv("LIVE_ASK_TOLERANCE_PCT", "0.08"))
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
            result = await asyncio.to_thread(
                pmus.submit_fok, mapping["market_slug"], _wire,
                int(shares), False, "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL",
                _intent)
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
                        "limit": limit, "fill_price": fill_price,
                        "why": "venue filled above our limit — copying "
                               "halted after the first occurrence"}))
                log.error("COPY SLEEVE HALTED by overspend breaker")
            except Exception:  # noqa: BLE001 — never lose the fill record
                log.exception("could not persist the overspend halt")
        await pool.execute(
            """
            UPDATE live_orders
            SET status=$2, order_id=$3, filled_shares=$4, fill_price=$5,
                filled_usd=$6, raw=$7::jsonb, error=$8
            WHERE id=$1
            """,
            row_id,
            "filled" if result["ok"] and filled > 0 else "unfilled",
            result.get("order_id"), filled, fill_price, spent,
            json.dumps(result.get("raw"), default=str),
            (f"OVERSPEND: asked ${usd:.2f}, filled ${spent:.2f}"
             if overspent else
             None if result["ok"] else str(result.get("raw"))[:300]),
        )
        log.info("LIVE order [%s] %s: %s %.2f shares @ %.3f (his %.3f)",
                 venue, "FILLED" if result["ok"] and filled > 0 else "unfilled",
                 payload.get("whale_username"), filled, fill_price or limit, his_price)
        if _echo_args and result["ok"] and filled > 0:
            _spawn_echo(pool, row_id, *_echo_args, shadow=False,
                        **_echo_kw)
    except Exception as exc:  # noqa: BLE001 — record, never crash ingestion
        log.exception("live order failed for trade %s", payload.get("id"))
        await pool.execute(
            "UPDATE live_orders SET status='error', error=$2 WHERE id=$1",
            row_id, str(exc)[:300],
        )
    finally:
        if _first_fill_gate and _FIRST_FILL_LOCK.locked():
            _FIRST_FILL_LOCK.release()
