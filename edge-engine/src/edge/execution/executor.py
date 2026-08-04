"""Order execution (build steps 5-6): the ONLY module that turns an approved
intent into a ledger fill — paper-logged or venue-placed by mode.

PAPER      : fill recorded in the ledger at the book's ask (plus the venue's
             modeled taker fee) — never touches a venue order endpoint.
LIVE_BETA/ : Kalshi -> maker-first (post below the ask, fee-free; cross only
LIVE         when net-of-fee edge still clears the threshold).
             Polymarket US -> preview-verified FOK limit.
Every fill carries its full decision record into the ledger.
"""

from __future__ import annotations

import logging
import uuid

from edge.ledger.service import Ledger
from edge.venues.base import MarketBook

log = logging.getLogger(__name__)

PMUS_ORDER_PREFIX = "pmus_order:"   # state key: resting maker order context


def market_key(venue: str, outcome_id: str) -> str:
    return f"{venue}:{outcome_id}"


def build_decision_record(*, fair: float, edge: float, threshold: float,
                          band: str, book: MarketBook, feed_snapshot: dict,
                          approved_usd: float, guard_reason: str) -> dict:
    return {
        "fair_value": round(fair, 4), "edge": round(edge, 4),
        "threshold": threshold, "band": band,
        "book_asks": [(lv.price, lv.size) for lv in book.asks[:5]],
        "book_bids": [(lv.price, lv.size) for lv in book.bids[:5]],
        "feed": feed_snapshot, "approved_usd": approved_usd,
        "caps_state": guard_reason,
    }


def execute(*, adapter, ledger: Ledger, mode: str, mkey: str, league: str,
            ask_price: float, ask_size: float, size_usd: float,
            edge: float, threshold: float, decision: dict,
            ts: float, entry_price: float | None = None, taker: bool = True,
            event_key: str | None = None, category: str | None = None) -> dict:
    """Returns {placed, filled_usd, status}. Paper fills always 'fill' up to
    displayed depth (conservative: no fill beyond what the book showed).

    entry_price/taker come from the adapter's plan_entry(): when the venue
    can rest an order, entry_price is inside the spread and taker is False."""
    entry_price = ask_price if entry_price is None else entry_price
    displayed_usd = ask_price * ask_size
    if mode == "PAPER":
        usd = min(size_usd, displayed_usd)
        if usd < 1.0:
            return {"placed": False, "filled_usd": 0.0, "status": "paper_no_depth"}
        qty = round(usd / entry_price, 2)
        fee = (adapter.taker_fee if taker else adapter.maker_fee)(entry_price) * qty
        ledger.record_fill(
            fill_uid=f"paper-{mkey}-{int(ts)}", venue=adapter.name,
            market_key=mkey, side="BUY", qty=qty, price=entry_price, ts=ts,
            fee=round(fee, 4), league=league, mode="PAPER", category=category,
            decision={**decision, "entry_taker": taker, "ask_price": ask_price,
                      "event_key": event_key},
        )
        return {"placed": True, "filled_usd": round(usd, 2), "status": "paper"}

    # ── live paths ──────────────────────────────────────────────────────
    outcome_id = mkey.split(":", 1)[1]
    if adapter.name == "kalshi":
        plan = adapter.plan_maker_order(ask_price, ask_price, edge, threshold)
        if plan is None:
            return {"placed": False, "filled_usd": 0.0, "status": "no_maker_no_fee_room"}
        px, taker = plan
        count = int(size_usd / px)
        if count < 1:
            return {"placed": False, "filled_usd": 0.0, "status": "sub_contract"}
        result = adapter.place_order(outcome_id, px, count,
                                     client_order_id=str(uuid.uuid4()), taker=taker)
        # ALL Kalshi fills (immediate taker and resting maker alike) are
        # recorded by sync_kalshi_fills() from the portfolio fills API — one
        # source of truth, so a taker execution is never double-counted.
        if result["ok"]:
            ledger.set_state(f"kalshi_order:{result.get('order_id')}",
                             {**decision, "px": px, "count": count, "taker": taker,
                              "market_key": mkey, "league": league, "ts": ts})
            return {"placed": True, "filled_usd": 0.0,
                    "status": f"{result.get('status')}:{result.get('order_id')}"}
        return {"placed": False, "filled_usd": 0.0,
                "status": f"rejected:{result.get('status')}"}

    if adapter.name == "polymarket-us":
        qty = int(size_usd / entry_price)
        if qty < 1:
            return {"placed": False, "filled_usd": 0.0, "status": "sub_contract"}
        if not taker:
            # Maker path: rest inside the spread, post-only. The fill (if it
            # comes) arrives later via sync_pmus_fills, so nothing is written
            # to the ledger here — only the context the reconciler and the
            # reaper need. Keyed by market: one resting order per market at a
            # time, which the per-market cap already guarantees.
            result = adapter.place_order(
                outcome_id, round(entry_price, 2), qty, preview=False,
                tif="TIME_IN_FORCE_GOOD_TILL_CANCEL", post_only=True)
            if not result["ok"]:
                # Price improvement is an optimisation, never a precondition.
                # If the venue won't rest the order, cross — provided the
                # edge still clears at the ask, which is the bar we would
                # have used had we never tried to do better.
                ask_edge = edge - (ask_price - entry_price)
                if ask_edge < threshold:
                    return {"placed": False, "filled_usd": 0.0,
                            "status": f"maker_rejected:{result.get('status')}"}
                log.info("maker order refused (%s) — crossing at %.2f",
                         result.get("status"), ask_price)
                adapter.mark_force_taker(outcome_id)
                entry_price, taker, qty = ask_price, True, int(size_usd / ask_price)
                if qty < 1:
                    return {"placed": False, "filled_usd": 0.0,
                            "status": "sub_contract"}
            else:
                ledger.set_state(f"{PMUS_ORDER_PREFIX}{outcome_id}", {
                    **decision, "order_id": result.get("order_id"),
                    "px": round(entry_price, 2), "count": qty, "taker": False,
                    "market_key": mkey, "league": league, "event_key": event_key,
                    "mode": mode, "ts": ts, "category": category})
                return {"placed": True, "filled_usd": 0.0, "status": "resting_maker"}

        # Taker path. Clear any stale maker context for this market first, or
        # the reconciler would record this fill a second time from the
        # activity feed.
        ledger.clear_state(f"{PMUS_ORDER_PREFIX}{outcome_id}")
        # The FOK limit is NOT the observed ask — it is the worst price
        # that still clears the bar (audit 2026-08-04). A limit pinned to
        # the ask dies on a one-tick uptick between book read and send,
        # which converts edges into `unfilled:` misses at exactly the
        # moments prices are moving toward fair. edge and threshold were
        # judged at entry_price, so entry + (edge - threshold) is by
        # construction the highest price at which this trade still clears;
        # the venue fills at the real ask whenever it is at or under that.
        # Never pays more than the strategy permits, never worse than the
        # old behaviour when the book is still.
        slack = max(0.0, float(edge) - float(threshold))
        limit_px = min(round(entry_price + slack, 2), 0.99)
        qty = int(size_usd / limit_px) or qty
        # Micro orders skip the preview round-trip (FOK limit already bounds
        # cost); larger sizes keep the venue cost pre-check.
        result = adapter.place_order(outcome_id, limit_px, qty,
                                     preview=size_usd > 25)
        if result["ok"]:
            filled = float(result["count"])
            px = float(result["price"])
            ledger.record_fill(
                fill_uid=f"pmus-{result.get('order_id')}", venue=adapter.name,
                market_key=mkey, side="BUY", qty=filled, price=px, ts=ts,
                fee=adapter.taker_fee(px) * filled, league=league, mode=mode,
                category=category,
                decision={**decision, "event_key": event_key,
                          "order": {k: v for k, v in result.items()
                                    if k != "raw"},
                          "commissions_raw": (result.get("raw") or {}).get("response", {})},
            )
            return {"placed": True, "filled_usd": round(filled * px, 2),
                    "status": "filled_fok"}
        return {"placed": False, "filled_usd": 0.0,
                "status": f"unfilled:{result.get('status')}"}

    return {"placed": False, "filled_usd": 0.0, "status": f"unknown_venue_{adapter.name}"}


def sync_pmus_fills(adapter, ledger: Ledger, mode: str) -> int:
    """Record fills of RESTING Polymarket US orders from the activity feed.

    Only markets carrying a parked maker context are considered: a taker FOK
    fill is written by execute() the moment it happens and clears its
    context, so no trade can be counted twice. fill_uid dedupe makes repeated
    syncs idempotent regardless."""
    contexts = ledger.list_state(PMUS_ORDER_PREFIX)
    if not contexts:
        return 0
    by_slug = {k[len(PMUS_ORDER_PREFIX):]: v for k, v in contexts.items()}
    n = 0
    for trade in adapter.recent_trades(limit=100):
        slug = trade.get("marketSlug")
        ctx = by_slug.get(slug)
        if not ctx:
            continue
        try:
            qty = float(trade.get("qty") or 0)
            price = float((trade.get("price") or {}).get("value") or 0)
        except (TypeError, ValueError):
            continue
        if qty <= 0 or not (0 < price < 1):
            continue
        fee = (adapter.taker_fee(price) if trade.get("isAggressor")
               else adapter.maker_fee(price)) * qty
        r = ledger.record_fill(
            fill_uid=f"pmus-trade-{trade.get('id')}", venue=adapter.name,
            market_key=ctx.get("market_key") or market_key(adapter.name, slug),
            side="BUY", qty=qty, price=price, ts=None, fee=round(fee, 4),
            league=ctx.get("league"), mode=ctx.get("mode") or mode,
            # Carried from the parked context: a maker fill with no category
            # lands in the drift report's "?" bucket, which is why that
            # bucket exists at n=40 with its own distinct drift.
            category=ctx.get("category"),
            decision={**ctx, "source": "maker_fill_sync", "raw": trade},
        )
        n += int(r["applied"])
    return n


def reconcile_positions(adapter, ledger: Ledger, mode: str,
                        limit: int = 300) -> dict:
    """Rebuild what we already own from the venue's own trade history.

    THE BUG THIS FIXES. Every per-market cap and the never-add rule are
    enforced against the ENGINE's ledger — `market_open_cost()` reads
    ledger.position(). The ledger is SQLite at EDGE_LEDGER_DB, which on an
    ephemeral filesystem is destroyed on every deploy. A fresh ledger
    therefore believes it owns nothing, re-grants the full $1.50 of
    per-market room, and buys the same market again. Six deploys in a day
    turns a $1.50 cap into a $5.00 position.

    Observed 2026-08-02, all of them prop or segment markets added that day:
        $5.00  astatc-mlb-sf-sd-2026-08-02-k-mickin-gte6
        $4.72  astatc-mlb-az-cle-2026-08-02-k-merkel-gte4
        $4.23  atc-mlb-min-sea-2026-08-02-f5-sea

    The same wipe is why the drift surcharge and the reversion estimate read
    empty after a deploy: they are derived from the same store.

    Reconciliation is idempotent by fill_uid and only ever ADDS what the
    ledger is missing, so running it repeatedly is safe. It writes a fill
    per venue trade rather than a synthetic aggregate, which keeps the
    audited average-cost pipeline as the single implementation of the maths.

    The durable fix is a persistent disk for EDGE_LEDGER_DB. This makes a
    fresh ledger safe in the meantime, which matters because "the caps are
    only correct if the disk survived" is not a risk control.
    """
    seen, added, cost = 0, 0, 0.0
    for trade in adapter.recent_trades(limit=limit):
        slug = trade.get("marketSlug")
        if not slug:
            continue
        seen += 1
        try:
            qty = float(trade.get("qty") or 0)
            price = float((trade.get("price") or {}).get("value") or 0)
        except (TypeError, ValueError):
            continue
        if qty <= 0 or not (0 < price < 1):
            continue
        if (trade.get("side") or "BUY").upper() != "BUY":
            continue
        # SAME fill_uid as sync_pmus_fills. These two paths see the same
        # venue trades, and giving them different keys meant one trade could
        # be recorded twice — once per path — inflating a position and the
        # per-market exposure computed from it. Sharing the key makes
        # whichever path arrives first the writer and the other a no-op; the
        # ledger's own uid dedupe does the rest.
        r = ledger.record_fill(
            fill_uid=f"pmus-trade-{trade.get('id')}", venue=adapter.name,
            market_key=market_key(adapter.name, slug), side="BUY",
            qty=qty, price=price, ts=None, fee=0.0, mode=mode,
            category="reconciled",
            decision={"source": "position_reconciliation", "raw": trade})
        if r["applied"]:
            added += 1
            cost += qty * price
    return {"trades_seen": seen, "fills_added": added,
            "cost_restored": round(cost, 2)}


# How long a reaped context is kept around after its order leaves the book.
# The venue's activity feed lags its own matching engine, so a trade can be
# real for seconds before recent_trades() will admit it. Dropping the context
# at TTL is what let a filled order look unfilled — see below.
REAP_GRACE_S = 300.0


def cancel_orphan_orders(adapter, ledger: Ledger) -> dict:
    """Cancel every venue open order that no ledger context claims.

    THE BUG THIS FIXES (observed live 2026-08-02 18:36Z). The reaper can
    only cancel orders it has contexts for, and the contexts live in the
    ledger's state table — which was wiped by every deploy. Each deploy
    therefore ORPHANED every resting GTC order: still live at the venue,
    invisible to the engine, filling on their own as game prices crossed
    them. Positions grew $271 -> $316 in an hour that included a PAPER
    window in which the engine placed nothing at all, and sub-$1 partial
    fills (3-15c) appeared that approve()'s $1 floor makes impossible as
    taker orders. An order nobody is tracking is not a position waiting to
    happen — it is a standing instruction to buy at a stale price forever.

    So: anything open at the venue without a matching context is cancelled,
    every cycle, in EVERY mode — PAPER especially, because a halt that
    leaves resting orders live is not a halt. Orders the engine is
    actively tracking are spared. With a freshly wiped ledger that means
    cancel-all, which is exactly right: cancel-on-restart is standard
    exchange hygiene we should have had from the first maker order.

    SECOND ACT (observed live 2026-08-02 20:06Z). The list-and-cancel
    version above was not enough: with the sweep running every cycle, the
    over-cap cohort still grew $320.22 -> $324.92 in 16 minutes — both
    growths on one in-play tennis match. Cancel-by-id can only kill what
    /orders/open returns, and something (in-play markets, pagination, an
    erroring listing that reads as an empty book) was keeping those
    orders out of the list. So while the engine tracks NO resting orders
    of its own — maker is hard off, FOK taker orders never rest — the
    sweep now leads with the venue's CANCEL-ALL endpoint, which needs no
    listing to be complete. The selective path survives only as the
    fallback for the day maker pricing returns and contexts exist again.

    Telemetry is honest now, too: a failed per-id cancel counts as
    cancel_failed (it used to count as cancelled), and the post-sweep
    listing size is reported so a listing that hides orders at least
    shows up as a contradiction against the account's position growth.
    """
    contexts = ledger.list_state(PMUS_ORDER_PREFIX)
    tracked = {(c or {}).get("order_id") for c in contexts.values()}
    out = {"open": 0, "orphans_cancelled": 0, "cancel_failed": 0,
           "cancel_all": None}
    if not contexts and hasattr(adapter, "cancel_all_orders"):
        # Nothing tracked -> every open order is an orphan by definition,
        # and the blanket cancel reaches orders the listing hides.
        out["cancel_all"] = bool(adapter.cancel_all_orders())
    try:
        open_orders = adapter.open_orders()
    except Exception as exc:  # noqa: BLE001 — sweep must never kill the loop
        log.warning("orphan sweep could not list orders: %s", exc)
        return out
    for o in open_orders:
        oid = o.get("id")
        if not oid:
            continue
        out["open"] += 1
        if oid in tracked:
            continue
        slug = (o.get("marketSlug")
                or (o.get("market") or {}).get("slug") or "")
        if adapter.cancel_order(oid, slug):
            out["orphans_cancelled"] += 1
        else:
            out["cancel_failed"] += 1
    if out["orphans_cancelled"] or out["cancel_failed"]:
        log.error("ORPHAN SWEEP: cancelled %d untracked resting orders, "
                  "%d cancels FAILED (%d open total)",
                  out["orphans_cancelled"], out["cancel_failed"], out["open"])
    return out


def reap_pmus_makers(adapter, ledger: Ledger, ttl_s: float,
                     now: float | None = None) -> dict:
    """Close out resting orders: cancel anything past its TTL, and drop the
    context of anything the venue no longer lists as open.

    A resting order that never filled must give back its one-per-market claim
    — otherwise a single unfilled quote retires that market for good, which
    costs far more volume than maker pricing wins.

    THE BUG THIS FIXES (observed live 2026-08-02). The old version did, in
    order: clear the context, look up the position, and release the claim if
    the position was empty. Every step of that is wrong when a resting order
    fills near its TTL:

      1. clearing the context first means sync_pmus_fills — which only
         considers markets carrying a parked context — can NEVER attribute
         that trade. The fill is real at the venue and invisible to us,
         permanently;
      2. so the position lookup finds nothing, because the fill it is looking
         for is the one step 1 just made unreachable;
      3. so the claim goes back, and the next cycle buys the same market
         again — against a per-market cap computed from a ledger that does
         not know we already own it.

    Nothing in that loop is self-limiting. It ran ~25-28 times each into four
    MLB markets, turning a $1.50 per-market cap into positions of $25-$28.

    Three changes close it:

      * the context survives until REAP_GRACE_S past the order leaving the
        book, so a late trade can still be attributed to it;
      * the claim is released only when the venue CONFIRMS the cancel — a
        cancel that fails because the order already filled is evidence we
        hold something, not evidence we don't;
      * the position check runs against a ledger that reconcile_positions()
        has already refreshed this cycle (see runner.py), so "no position"
        means the venue agrees, not merely that we have not looked.

    Fail-closed throughout: when we cannot prove the order died unfilled, we
    keep the claim. The cost of being wrong that way is one missed bet. The
    cost of being wrong the other way is the paragraph above.
    """
    import time as _time

    now = now or _time.time()
    contexts = ledger.list_state(PMUS_ORDER_PREFIX)
    if not contexts:
        return {"cancelled": 0, "closed": 0, "released": 0, "held": 0}
    open_ids = {o.get("id") for o in adapter.open_orders() if o.get("id")}
    out = {"cancelled": 0, "closed": 0, "released": 0, "held": 0}
    for key, ctx in contexts.items():
        slug = key[len(PMUS_ORDER_PREFIX):]
        still_open = ctx.get("order_id") in open_ids
        if still_open and now - float(ctx.get("ts") or 0) <= ttl_s:
            continue                       # young and working: leave it be

        cancel_confirmed = False
        if still_open:
            # True = the venue pulled a live order, so it had not filled.
            # False = it was already gone, which usually means it traded.
            cancel_confirmed = bool(adapter.cancel_order(ctx["order_id"], slug))
            out["cancelled"] += 1
            ctx = {**ctx, "reaped_ts": now, "cancel_confirmed": cancel_confirmed}
            ledger.set_state(key, ctx)
        else:
            out["closed"] += 1
            if "reaped_ts" not in ctx:
                # First time we have seen it gone. Start the grace clock and
                # come back next cycle — the fill may still be in flight.
                ledger.set_state(key, {**ctx, "reaped_ts": now})
                out["held"] += 1
                continue

        pos = ledger.position(ctx.get("market_key") or "")
        held = pos and float(pos.get("shares") or 0) > 0
        aged_out = now - float(ctx.get("reaped_ts") or now) >= REAP_GRACE_S

        if held:
            # It filled. Keep the claim (we own this market) and drop the
            # context — the position is now the record.
            ledger.clear_state(key)
            continue
        if not cancel_confirmed and not aged_out:
            # Unproven: the order is gone, we hold nothing yet, and the venue
            # did not confirm a cancel. That is exactly the fill-in-flight
            # case. Hold everything and re-check next cycle.
            out["held"] += 1
            continue

        # Proven unfilled: cancel confirmed, or the grace window elapsed with
        # nothing ever arriving. Safe to release.
        ledger.clear_state(key)
        if hasattr(adapter, "mark_force_taker"):
            # Stop resting on this market for a cool-off, so the next look
            # crosses instead of quoting into a queue that has already proven
            # it won't reach us.
            adapter.mark_force_taker(slug)
        if ctx.get("event_key") and ledger.release_event(ctx["event_key"]):
            out["released"] += 1
    return out


def sync_kalshi_fills(adapter, ledger: Ledger, mode: str) -> int:
    """Reconcile resting maker orders: pull recent fills from the portfolio
    API and record them (ledger fill_uid dedupe makes this idempotent)."""
    import requests

    try:
        path = "/trade-api/v2/portfolio/fills"
        from edge.venues.kalshi import BASE

        resp = adapter._sess.get(  # noqa: SLF001 — same package
            f"{BASE}/portfolio/fills", params={"limit": 100},
            headers=adapter._auth_headers("GET", path), timeout=10)
        if resp.status_code != 200:
            return 0
        n = 0
        for f in (resp.json() or {}).get("fills") or []:
            if f.get("action") != "buy" or f.get("side") != "yes":
                continue
            qty = float(f.get("count") or 0)
            price = float(f.get("yes_price") or 0) / 100.0
            if qty <= 0 or not (0 < price < 1):
                continue
            # Sweeps/smoke/xv record their fills inline at placement and
            # mark the order id — recording those here again doubled every
            # position (2x caps, 2x P&L; audit 2026-08-04).
            if ledger.get_state(f"kalshi_inline:{f.get('order_id')}"):
                continue
            # Order context (decision record) was parked at placement time.
            ctx = ledger.get_state(f"kalshi_order:{f.get('order_id')}") or {}
            fee = adapter.taker_fee(price) * qty if f.get("is_taker") else 0.0
            r = ledger.record_fill(
                fill_uid=f"kalshi-fill-{f.get('trade_id') or f.get('fill_id')}",
                venue="kalshi",
                market_key=ctx.get("market_key")
                    or market_key("kalshi", f.get("ticker", "")),
                side="BUY", qty=qty, price=price,
                ts=float(f.get("created_time_ts") or 0) or None,
                fee=round(fee, 4), league=ctx.get("league"),
                mode=mode, decision={**ctx, "source": "fill_sync", "raw": f},
            )
            n += int(r["applied"])
        return n
    except (requests.RequestException, ValueError, RuntimeError) as exc:
        log.warning("kalshi fill sync failed: %s", exc)
        return 0
