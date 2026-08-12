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
_COPY_SEM = asyncio.Semaphore(4)


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
        if not settings().copy_probe_enabled or payload.get("side") != "BUY":
            return
        async with _COPY_SEM:
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


def plan_order(
    his_price: float, his_notional: float, ratio: float,
    max_per_fill: float, max_slippage_cents: float,
    whole_units: bool = False,
) -> tuple[float, float, float]:
    """Pure sizing/pricing: (limit_price, requested_usd, requested_shares).

    whole_units=True (Polymarket US): whole-cent limit price and integer
    contract count, rounding down so the cost never exceeds the budget."""
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
# $1 constant (workers/underdog.py PER_FILL_USD).
PENNY_TRIAL_PER_FILL_USD = 50.00
# Per-whale override map; keys are lowercased usernames; anyone absent
# gets the default. RN1 $100 -> $150 2026-08-11 (owner approval,
# round 4): profitable every single day since Aug 3.
PER_FILL_BY_WHALE = {"rn1": 150.00, "swisstony": 200.00}


def per_fill_usd(whale_username: str | None) -> float:
    return PER_FILL_BY_WHALE.get((whale_username or "").lower(),
                                 PENNY_TRIAL_PER_FILL_USD)


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


async def execute_manual(asset: str, usd: float, note: str = "") -> dict:
    """Place an admin-directed BUY: FOK limit at the live ask +2c
    protection, whole contracts rounded down so the ticket never
    exceeds the requested budget. Returns a UI-ready result dict —
    every refusal is a named reason, never an exception (an unhandled
    500 loses its CORS headers and reads as a blank network failure on
    the desk — observed 2026-08-07)."""
    try:
        return await _execute_manual(asset, usd, note)
    except Exception as exc:  # noqa: BLE001 — the desk reports, never 500s
        log.exception("manual order failed pre-flight")
        return {"ok": False,
                "error": f"{type(exc).__name__}: {str(exc)[:160]}"}


async def _execute_manual(asset: str, usd: float, note: str = "") -> dict:
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
    # Double-click / impatient-retry guard: placement takes tens of
    # seconds (market resolution + preview + FOK), and a retried request
    # while the first is in flight would buy twice.
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

    row_id = await pool.fetchval(
        """
        INSERT INTO live_orders (trade_id, whale_username, asset,
                                 condition_id, side, his_price, limit_price,
                                 requested_usd, requested_shares, status,
                                 venue)
        VALUES (NULL, 'manual', $1, $2, 'BUY', $3, $4, $5, $6,
                'submitting', $7)
        RETURNING id
        """,
        str(asset), None, ask, limit, round(shares * limit, 2),
        float(shares), venue)
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
        result = await asyncio.to_thread(
            pmus.submit_fok, mapping["market_slug"], limit, shares)
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


async def maybe_execute(payload: dict, reaction: float | None) -> None:
    """Called on every fresh detection (after the paper trade). All guards
    re-checked here; failure of any guard is a silent no-op or logged skip."""
    if COPY_MODE == "off":
        return
    cfg = settings()
    venue = active_venue()
    if venue is None:
        return
    username = (payload.get("whale_username") or "").lower()
    if payload.get("side") != "BUY" or username not in cfg.source_whales():
        return
    his_notional = float(payload.get("notional") or 0)
    his_price = float(payload.get("price") or 0)
    if his_notional <= 0 or not (0 < his_price < 1):
        return

    pool = await get_pool()
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
    from .copy_sports import KALSHI_FIRST_SPORTS, kalshi_first, sport_of
    if (not payload.get("sweep_recovery")
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
    if day_room <= 0 or total_room <= 1:
        log.warning("live caps exhausted (day room %.2f, total room %.2f) — skipping",
                    day_room, total_room)
        return

    if COPY_MODE == "penny_trial":
        # $3 target per copy: whole contracts at his price +2% RELATIVE,
        # floored to the venue's cent tick (owner tolerance:
        # same/better/within-2%-worse). Whole-unit FOK fills all or kills.
        import math

        # His price + 2%, floored to AT LEAST one tick: cent-flooring gave
        # sub-50c copies zero tolerance, failing exactly when his edge was
        # confirming and filling when price moved against him — manufactured
        # adverse selection (audit 2026-08-04).
        limit = round(min(his_price + max(0.01, his_price * 0.02), 0.99), 2)
        if limit <= 0:
            return
        shares = float(int(per_fill_usd(payload.get("whale_username")) / limit))
        if shares < 1:
            return
        usd = round(shares * limit, 2)
        if usd > day_room:
            return
    else:
        limit, usd, shares = plan_order(
            his_price, his_notional, cfg.live_copy_ratio,
            min(cfg.live_max_per_fill_usd, day_room, total_room),
            cfg.live_max_slippage_cents,
            whole_units=(venue == "polymarket-us"),
        )
        if usd < 1 or shares <= 0:
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
            if market_type_of(src_slug) == "moneyline":
                mapping = await asyncio.to_thread(
                    pmus.resolve_market_exact,
                    _us_slug_candidates(src_slug, ctx.get("outcome") or ""),
                    ctx.get("outcome"))
            if mapping is None:
                mapping = await asyncio.to_thread(
                    pmus.resolve_market, ctx.get("market_slug"), ctx.get("event_slug"),
                    ctx.get("market_title"), ctx.get("event_title"), ctx.get("outcome"),
                )
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
            # NEVER-ADD, DB-SIDE (incident 2026-08-11 afternoon, the $318
            # Over-2.5 position): the venue-side no-stack below answers
            # from a positions snapshot that goes silently stale through
            # a venue outage — Polymarket's maintenance morning left it
            # hours old and every re-entry passed. Our OWN order ledger
            # is postgres: it survives deploys and outages, and one
            # market copied once is one row. Any filled or in-flight
            # order on this exact market refuses another, whatever
            # identity or asset id proposes it.
            prior = await pool.fetchval(
                "SELECT 1 FROM live_orders "
                "WHERE us_market_slug = $2 AND id <> $1 "
                "AND status IN ('filled', 'submitting') "
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
                held = await pool.fetch(
                    "SELECT us_market_slug FROM live_orders "
                    "WHERE status IN ('filled', 'submitting') "
                    "AND id <> $1 AND us_market_slug IS NOT NULL "
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
                await pool.execute(
                    "UPDATE live_orders SET status='rejected', error=$2 "
                    "WHERE id=$1", row_id,
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
            result = await asyncio.to_thread(
                pmus.submit_fok, mapping["market_slug"], limit, int(shares))
        else:
            result = await asyncio.to_thread(
                _submit_fok, str(payload["asset"]), limit, shares)

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
            None if result["ok"] else str(result.get("raw"))[:300],
        )
        log.info("LIVE order [%s] %s: %s %.2f shares @ %.3f (his %.3f)",
                 venue, "FILLED" if result["ok"] and filled > 0 else "unfilled",
                 payload.get("whale_username"), filled, fill_price or limit, his_price)
    except Exception as exc:  # noqa: BLE001 — record, never crash ingestion
        log.exception("live order failed for trade %s", payload.get("id"))
        await pool.execute(
            "UPDATE live_orders SET status='error', error=$2 WHERE id=$1",
            row_id, str(exc)[:300],
        )
