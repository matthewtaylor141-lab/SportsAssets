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
COPY_CUT_WHALES = frozenset({"rn1", "ferrarichampions2026", _W2C33})
PER_FILL_BY_WHALE = {"rn1": 0.00, "swisstony": 300.00,
                     _W2C33: 0.00, "homerunhazard": 300.00,
                     "kch123": 150.00,
                     "ferrarichampions2026": 0.00,
                     "0x076daa87": 300.00}

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
PER_FILL_BY_WHALE_SPORT = {("swisstony", "soccer"): 225.00,
                           (_W2C33, "tennis"): 0.00,
                           ("homerunhazard", "baseball"): 600.00,
                           ("homerunhazard", "football"): 100.00}
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
            return round(base * mult, 2)
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
        result = await asyncio.to_thread(
            pmus.submit_fok, mapping["market_slug"], limit, shares,
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
    can be sold, exactly as it is for no-stack on the buy side."""
    from .api.pmus_account import _amt, _fetch_all_positions_sync

    positions = await asyncio.wait_for(
        asyncio.to_thread(_fetch_all_positions_sync), timeout=30)
    p = (positions or {}).get(us_slug) or {}
    qty = _amt(p.get("netPosition"))
    if qty <= 0 or p.get("expired"):
        return 0, None
    cost = _amt(p.get("cost"))
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
    pnl = (round((fill_price - avg_cost) * filled, 4)
           if filled > 0 and fill_price is not None
           and avg_cost is not None else None)
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


async def _side_echo_verify(pool, row_id: int, us_slug: str,
                            outcome: str | None, his_title: str | None,
                            attempts: int = 3, shadow: bool = False) -> None:
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
                elif subject == want or want in subject or subject in want:
                    verdict = "ok"
                else:
                    verdict = "mismatch"
                    detail = (f"contract subject {subject!r} is not the "
                              f"whale's pick {want!r}")
            else:
                hit = _premap.match_side(rows, outcome, his_title)
                if hit is None:
                    detail = "no unique live match"
                elif str(hit["identifier"]).lower() == us_slug.lower():
                    verdict = "ok"
                else:
                    verdict = "mismatch"
                    detail = f"live matcher chose {hit['identifier']}"
    except Exception as exc:  # noqa: BLE001 — the echo never raises
        detail = f"echo error: {exc}"[:200]

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
                his_title: str | None, *, shadow: bool) -> None:
    """Fire-and-forget echo with a strong task ref (a bare create_task
    can be garbage-collected mid-flight)."""
    t = asyncio.create_task(_side_echo_verify(
        pool, row_id, us_slug, outcome, his_title, shadow=shadow))
    _ECHO_TASKS.add(t)
    t.add_done_callback(_ECHO_TASKS.discard)


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
        shares = float(int(per / limit))
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

    # STALENESS GATE (owner order 2026-08-24): a late signal is a decayed
    # edge — the copier refuses rather than chases. The cap is deliberate
    # slack for now; the edge-decay study tightens it per-whale later.
    _stale_cap = float(os.getenv("LIVE_MAX_REACTION_S", "90"))
    if reaction is not None and reaction > _stale_cap:
        await pool.execute(
            "UPDATE live_orders SET status='rejected', error=$2 WHERE id=$1",
            row_id,
            f"stale-signal: reaction {reaction}s > {_stale_cap:g}s cap")
        return

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
            # quarantine state: LIVE_HOLD_WHALES (default swisstony)
            # refuses with an audit reason until his paper cohort at the
            # new sub-second detection certifies positive and the env
            # deliberately changes. The row keeps its mapping — his
            # fidelity samples continue.
            _held = {w.strip() for w in
                     os.getenv("LIVE_HOLD_WHALES", "swisstony")
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
            _allowed = {w.strip() for w in
                        os.getenv("LIVE_PREMAP_WHALES",
                                  "homerunhazard,0x076daa87")
                        .lower().split(",") if w.strip()}
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
                                ctx.get("market_title"), shadow=True)
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
            result = await asyncio.to_thread(
                pmus.submit_fok, mapping["market_slug"], limit,
                int(shares), False, "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL")
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
        if _echo_args and result["ok"] and filled > 0:
            _spawn_echo(pool, row_id, *_echo_args, shadow=False)
    except Exception as exc:  # noqa: BLE001 — record, never crash ingestion
        log.exception("live order failed for trade %s", payload.get("id"))
        await pool.execute(
            "UPDATE live_orders SET status='error', error=$2 WHERE id=$1",
            row_id, str(exc)[:300],
        )
