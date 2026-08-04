"""Whale copies executed ON KALSHI (owner directive 2026-08-04: "we need
to ensure trades start being copied to Kalshi").

The Polymarket copy pipeline stays where it is; this is a SECOND copy leg
that expresses the same whale positions on Kalshi wherever Kalshi lists
the same proposition. Same trading rules as the Polymarket sleeve:

- his price + 2% relative tolerance, floored to the cent — we only take
  a Kalshi book whose ask is at/inside that limit;
- $2 per copy ($3 for RN1, same per-whale map spirit), whole contracts,
  rounded down;
- one copy per whale position EVER (ledger claim), day budget for the
  class (EDGE_KCOPY_DAY_USD, default $200), kill switch EDGE_KCOPY=0;
- category "kalshi_copy" so settlement grades this venue's copy edge
  SEPARATELY from Polymarket's — the venues' fill mechanics differ and
  their measured ROI may too.

Matching reuses the strict join built for the adds sweep: game key
(sorted tokens + date) plus the mapper's 0.95 name bar. Tennis works the
moment the tennis series tickers are in the Kalshi series map — the
census step in the diagnostic workflow names them from Kalshi's own
series list, so coverage is a config line, not a guess.
"""

from __future__ import annotations

import logging
import math
import time
import uuid
from datetime import datetime, timezone

log = logging.getLogger(__name__)

PER_COPY_USD = {"rn1": 3.00}
PER_COPY_DEFAULT = 2.00


def _limit_for(his_price: float) -> float:
    return round(min(math.floor(his_price * 1.02 * 100) / 100.0, 0.99), 2)


def sweep(*, kalshi, ledger, identities: list[dict], live: bool,
          day_usd: float = 200.0) -> dict:
    """One pass: whale open positions -> Kalshi orders where listed."""
    from edge.shadow.kalshi_guard import (cross_side_cap, note_fill,
                                          open_kalshi_sides)
    from edge.shadow.whale_align import game_key
    from edge.venues.kalshi import _series_map
    from edge.venues.mapper import team_score

    stats = {"whale_positions": 0, "league_listed": 0, "matched": 0,
             "priced_in_tolerance": 0, "copied": 0, "spent": 0.0,
             "skipped_claimed": 0, "best_ask_gap_c": -99.0}
    if not identities:
        return stats
    sides = open_kalshi_sides(ledger)
    day = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    spend = ledger.get_state("kcopy_day") or {}
    spent = float(spend.get("spent", 0.0)) if spend.get("day") == day else 0.0

    series = _series_map()
    discovered: dict = {}      # league -> {game_key: VenueMarket}
    for row in identities:
        stats["whale_positions"] += 1
        slug = row.get("slug") or ""
        outcome = (row.get("outcome") or "").strip()
        his_price = float(row.get("price") or 0)
        if not slug or not outcome or not (0 < his_price < 1):
            continue
        league = slug.split("-", 1)[0].lower()
        if league not in series:
            continue
        stats["league_listed"] += 1
        gkey = game_key(slug)
        if gkey is None:
            continue
        if league not in discovered:
            try:
                discovered[league] = {}
                for vm in kalshi.discover_markets({league}):
                    # Kalshi event tickers carry no slug-style date; key by
                    # the market's own outcome names + the event date token
                    # is already enforced in the adds sweep. Here the join
                    # is name-first: index every market by its outcome set.
                    discovered[league][vm.market_id] = vm
            except Exception:  # noqa: BLE001
                discovered[league] = {}
        target_ticker = None
        for vm in discovered[league].values():
            names = list(vm.outcome_tokens)
            if len(names) != 2:
                continue
            # His side must hit one outcome at the mapper bar AND the
            # OTHER outcome must belong to the same matchup we hold —
            # both names scoring against the whale slug's game tokens
            # keeps "same player, different match" out.
            hit = None
            for name in names:
                if team_score(name, outcome) >= 0.95:
                    hit = name
                    break
            if hit is None:
                continue
            other = [n for n in names if n != hit][0]
            toks = [t for t in gkey.split("|")[0].split("-") if t]
            other_ok = any(team_score(other, t) >= 0.6 for t in toks) \
                or any(t.lower() in other.lower() for t in toks)
            if not other_ok:
                continue
            target_ticker = vm.outcome_tokens[hit]
            break
        if target_ticker is None:
            continue
        stats["matched"] += 1
        claim = f"kcopy:{slug}:{outcome[:24]}"
        if ledger.get_state(claim):
            stats["skipped_claimed"] += 1
            continue
        book = kalshi.get_book(target_ticker, target_ticker)
        if book is None or not book.asks or book.asks[0].size < 1:
            continue
        ask = book.asks[0].price
        limit = _limit_for(his_price)
        stats["best_ask_gap_c"] = max(stats["best_ask_gap_c"],
                                      round((limit - ask) * 100, 2))
        if ask > limit:
            continue                # outside his price +2%: not a copy
        stats["priced_in_tolerance"] += 1
        per = PER_COPY_USD.get((row.get("whale") or "").lower(),
                               PER_COPY_DEFAULT)
        if spent + per > day_usd:
            stats["skipped_day_cap"] = stats.get("skipped_day_cap", 0) + 1
            continue
        count = int(per / ask)
        if count < 1:
            continue
        # Same-event guard: never build both sides of one market unless
        # the completed pair locks profit, and then only pair-matched.
        capped = cross_side_cap(sides, target_ticker, ask, count,
                                fee_per_contract=kalshi.taker_fee(ask))
        if capped < 1:
            stats["skipped_cross_side"] = stats.get("skipped_cross_side",
                                                    0) + 1
            continue
        count = capped
        if not live:
            stats["copied"] += 1     # dry-run telemetry
            continue
        r = kalshi.place_order(target_ticker, ask, count,
                               client_order_id=str(uuid.uuid4()), taker=True)
        filled = int(float(r.get("count") or 0)) if r.get("ok") else 0
        if r.get("ok") and filled > 0:
            ledger.set_state(claim, {"ts": time.time(),
                                     "status": r.get("status"),
                                     "filled": filled})
        elif r.get("ok"):
            # Accepted IOC, zero filled: the copy's once-ever claim must
            # not be burned on a fill that never happened — retry next
            # sweep at whatever the book says then.
            stats["ioc_zero_fill"] = stats.get("ioc_zero_fill", 0) + 1
        else:
            stats["last_order_error"] = {
                "ticker": target_ticker,
                "status": str(r.get("status"))[:200],
                "raw": str((r.get("raw") or {}).get("error"))[:300]}
        if filled > 0:
            note_fill(sides, target_ticker, ask, filled)
            stats["copied"] += 1
            cost = round(filled * ask, 2)
            spent += cost
            stats["spent"] = round(stats["spent"] + cost, 2)
            ledger.set_state("kcopy_day", {"day": day,
                                           "spent": round(spent, 2)})
            ledger.record_fill(
                fill_uid=f"kcopy-{claim}-{int(time.time())}",
                venue="kalshi", market_key=f"kalshi:{target_ticker}",
                side="BUY", qty=float(filled), price=ask,
                fee=round(kalshi.taker_fee(ask) * filled, 4),
                league=league, mode="LIVE_BETA", category="kalshi_copy",
                decision={"kalshi_copy": True, "whale": row.get("whale"),
                          "his_price": his_price, "limit": limit,
                          "ask": ask, "pm_slug": slug,
                          "outcome": outcome})
            log.warning("KALSHI COPY %s (%s): %s x%d @ %.2f (his %.3f)",
                        outcome, row.get("whale"), target_ticker, filled,
                        ask, his_price)
    return stats
