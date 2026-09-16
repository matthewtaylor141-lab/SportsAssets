"""Better-price adds (owner directive 2026-08-04): for every OPEN
Polymarket position, if Kalshi currently sells the SAME bet meaningfully
cheaper than we paid, add a $1 clip there (owner directive 2026-08-05:
all software-generated trades at $1; was $2).

This deliberately relaxes the engine's never-add rule for one narrow,
owner-ordered case: buying more of a bet we already hold at a strictly
better price than our entry. Guardrails carry the weight:

- MATCHING IS STRICT OR NOTHING. Same league (Kalshi series map), both
  team codes from the Polymarket slug must resolve against the Kalshi
  market's two outcomes, the game DATE must match inside the Kalshi
  event ticker, and the held side must match a Kalshi outcome at the
  0.95 mapper bar. Any doubt -> skip. A wrong match here buys the wrong
  game, which is worse than missing a better price.
- BETTER means fee-loaded: kalshi ask + taker fee <= our entry minus
  min_improve (2c default). Kalshi's fee is real money.
- ONCE per position, ever (ledger claim), $1 per add, and a daily
  budget for the class (EDGE_KADD_DAY_USD, default $50).
- Every add is ledger-recorded category "kalshi_add" so settlement
  grades this cohort SEPARATELY — if averaging-in doesn't pay, the
  record will say so and the feature dies on evidence.
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timezone

log = logging.getLogger(__name__)

_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
           "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def _kalshi_date_token(slug: str) -> str | None:
    m = _DATE_RE.search(slug or "")
    if not m:
        return None
    y, mo, d = m.groups()
    return f"{y[2:]}{_MONTHS[int(mo) - 1]}{d}"


def sweep(*, pmus, kalshi, ledger, live: bool,
          min_improve: float = 0.02, per_add_usd: float = 1.0,
          day_usd: float = 50.0) -> dict:
    """One pass over open PMUS positions. Returns stats for telemetry."""
    from edge.venues.mapper import team_score
    from edge.venues.pmus_slug import parse_slug

    stats = {"positions": 0, "league_listed": 0, "matched": 0,
             "better": 0, "added": 0, "spent": 0.0, "skipped_claimed": 0,
             "best_improve_c": -99.0}
    try:
        positions = pmus.open_positions_map()
    except Exception as exc:  # noqa: BLE001
        stats["error"] = f"positions:{type(exc).__name__}"
        return stats
    if not positions:
        return stats

    day = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    spend = ledger.get_state("kadd_day") or {}
    spent = float(spend.get("spent", 0.0)) if spend.get("day") == day else 0.0

    from edge.shadow.kalshi_guard import (cross_side_cap, live_blocked,
                                          note_fill, open_kalshi_sides)
    if live:
        blocked = live_blocked(ledger)
        if blocked:
            stats["blocked"] = blocked
            return stats
    sides = open_kalshi_sides(ledger)
    discovered: dict = {}   # league -> list[VenueMarket]
    for slug, pos in positions.items():
        stats["positions"] += 1
        qty = float(pos.get("qty") or 0)
        cost = float(pos.get("cost") or 0)
        outcome = (pos.get("outcome") or "").strip()
        if qty <= 0 or cost <= 0 or not outcome:
            continue
        entry = cost / qty
        parsed = parse_slug(slug)
        league = parsed.league
        from edge.venues.kalshi import _series_map
        if league not in _series_map():
            continue
        stats["league_listed"] += 1
        date_tok = _kalshi_date_token(slug)
        if date_tok is None:
            continue
        if league not in discovered:
            try:
                discovered[league] = kalshi.discover_markets({league})
            except Exception:  # noqa: BLE001
                discovered[league] = []
        target, ticker = None, None
        for vm in discovered[league]:
            if date_tok not in (vm.market_id or ""):
                continue
            names = list(vm.outcome_tokens)
            if len(names) != 2:
                continue
            # Both slug codes must resolve against this market's two
            # outcome names — the same discrimination bar the mapper uses.
            from edge.venues.pmus_slug import resolve_side
            a, b = names
            if not all(resolve_side(c, a, b) for c in parsed.codes if c):
                continue
            # The held side, at the mapper's confidence bar.
            for name in names:
                if team_score(name, outcome) >= 0.95:
                    target, ticker = vm, vm.outcome_tokens[name]
                    break
            if target:
                break
        if target is None:
            # Name the near-miss: the exact strings that failed to join,
            # so the matcher gets fixed against reality.
            ex = stats.setdefault("unmatched_examples", [])
            if len(ex) < 4:
                near = None
                for vm in discovered[league]:
                    if date_tok in (vm.market_id or ""):
                        names = list(vm.outcome_tokens)
                        scores = sorted(
                            ((round(team_score(n, outcome), 2), n[:30])
                             for n in names), reverse=True)
                        near = {"event": vm.market_id[-24:],
                                "top": scores[:2]}
                        break
                ex.append({"slug": slug[-40:], "outcome": outcome[:30],
                           "near": near})
            continue
        stats["matched"] += 1
        book = kalshi.get_book(target.market_id, ticker)
        if book is None or not book.asks or book.asks[0].size < 1:
            continue
        ask = book.asks[0].price
        eff = ask + kalshi.taker_fee(ask)
        improve = entry - eff
        stats["best_improve_c"] = max(stats["best_improve_c"],
                                      round(improve * 100, 2))
        if improve < min_improve:
            continue
        stats["better"] += 1
        claim = f"kadd:{slug}"
        if ledger.get_state(claim):
            stats["skipped_claimed"] += 1
            continue
        if spent + per_add_usd > day_usd:
            stats["skipped_day_cap"] = stats.get("skipped_day_cap", 0) + 1
            continue
        count = int(per_add_usd / ask)
        if count < 1:
            continue
        # Same-event guard: an add must never complete a losing pair.
        capped = cross_side_cap(sides, ticker, ask, count,
                                fee_per_contract=kalshi.taker_fee(ask))
        if capped < 1:
            stats["skipped_cross_side"] = stats.get("skipped_cross_side",
                                                    0) + 1
            continue
        count = capped
        if not live:
            stats["added"] += 1     # dry-run telemetry only
            continue
        import uuid
        r = kalshi.place_order(ticker, ask, count,
                               client_order_id=str(uuid.uuid4()), taker=True)
        filled = int(float(r.get("count") or 0)) if r.get("ok") else 0
        if r.get("ok") and filled > 0:
            ledger.set_state(claim, {"ts": time.time(),
                                     "status": r.get("status"),
                                     "filled": filled})
        elif r.get("ok"):
            # Accepted IOC, zero filled (book moved between look and order):
            # no claim, so the add retries against a fresh book next sweep.
            stats["ioc_zero_fill"] = stats.get("ioc_zero_fill", 0) + 1
        else:
            # NO claim on a failed order — the venue's answer is surfaced
            # and the position stays retryable (2026-08-04: a rejected
            # first attempt permanently blacklisted the one position that
            # was 5.39c better on Kalshi).
            stats["last_order_error"] = {
                "ticker": ticker,
                "status": str(r.get("status"))[:200],
                "raw": str((r.get("raw") or {}).get("error"))[:300]}
        if filled > 0:
            if r.get("order_id"):
                ledger.set_state(f"kalshi_inline:{r['order_id']}",
                                 {"ts": time.time()})
            note_fill(sides, ticker, ask, filled)
            stats["added"] += 1
            add_cost = round(filled * ask, 2)
            spent += add_cost
            stats["spent"] = round(stats["spent"] + add_cost, 2)
            ledger.set_state("kadd_day", {"day": day,
                                          "spent": round(spent, 2)})
            ledger.record_fill(
                fill_uid=f"kadd-{slug}-{int(time.time())}",
                venue="kalshi", market_key=f"kalshi:{ticker}",
                side="BUY", qty=float(filled), price=ask,
                fee=round(kalshi.taker_fee(ask) * filled, 4),
                league=league, mode="LIVE_BETA", category="kalshi_add",
                decision={"kalshi_add": True, "pmus_slug": slug,
                          "pmus_entry": round(entry, 4),
                          "kalshi_ask": ask,
                          "improve_c": round(improve * 100, 2),
                          "outcome": outcome})
            log.warning("KALSHI ADD %s: %s x%d @ %.2f (entry %.3f, "
                        "improve %.1fc)", slug, ticker, filled, ask,
                        entry, improve * 100)
    return stats
