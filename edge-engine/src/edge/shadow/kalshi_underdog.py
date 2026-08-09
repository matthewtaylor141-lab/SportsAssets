"""Kalshi leg of the $1 underdog cash-out sleeve (owner 2026-08-08:
"Can you get the $1 underdog sleeve also placing at Kalshi").

The platform worker is the sleeve's BRAIN — it owns the game catalog,
the venue start times, the T-minus-5 windows, and the dog pick — and
queues exactly one task per game (UNIQUE(game_slug) upstream). This
module is the ARM: only the engine process holds Kalshi credentials, so
it polls the queue, resolves the game on Kalshi by outcome names, buys
$1 of the dog as a taker, and then keeps a MAKER sell resting at the
+20% threshold until it fills (the cash-out), the game settles, or the
market closes. Sells are sized to held contracts only — never a short.

Non-interference: the engine's own Kalshi sides ledger vetoes any game
where another sleeve (copies, arb, manual) already holds a position, in
either direction — the $1 test must never turn an existing position
into an accidental pair or add to one.

Exit fills land ASYNCHRONOUSLY via sync_kalshi_fills (the resting sell
is parked as kalshi_order:{id} context with category
"kalshi_underdog_exit"); it enqueues kud_exit_events, which this sweep
drains to report cash-outs back to the platform.
"""

from __future__ import annotations

import logging
import time
import uuid

log = logging.getLogger(__name__)

MIN_ASK = 0.05     # sub-5c is lottery junk, same band as the PMUS leg
MAX_ASK = 0.48     # 50c+ is no dog
# Exit rests live HOURS, not the copies' 15 minutes: a +20% target does
# not go stale (it IS the strategy), and 15-minute churn left dozens of
# expired sell orders in the venue's history — which read as "a bunch
# sold" (owner 2026-08-09 morning). Long rests also keep queue priority.
REST_S = 4 * 3600


def threshold_price(entry: float, take: float = 0.20) -> float:
    """The resting sell limit IS the +20% trigger: a maker fill at or
    above it realizes the take on dollars spent. Capped at 99c."""
    return round(min(entry * (1.0 + take), 0.99), 2)


def contracts_for(usd: float, limit: float) -> int:
    """Whole contracts such that count * limit never exceeds the dollar."""
    if limit <= 0:
        return 0
    return int(usd / limit)


def _resolve(discovered: list, dog: str, other: str) -> str | None:
    """Kalshi ticker for the dog, matched by BOTH outcome names — the
    dog at the mapper bar and the opponent confirming the same matchup
    (one name alone matches 'same player, different match')."""
    from edge.venues.mapper import team_score

    for vm in discovered:
        names = list(vm.outcome_tokens)
        if len(names) != 2:
            continue
        hit = None
        for name in names:
            if team_score(name, dog) >= 0.9:
                hit = name
                break
        if hit is None:
            continue
        rest = [n for n in names if n != hit][0]
        if team_score(rest, other) >= 0.6:
            return vm.outcome_tokens[hit]
    return None


def sweep(*, kalshi, ledger, base: str, token: str, live: bool) -> dict:
    """One pass: report cash-outs, place queued entries, re-rest exits."""
    import requests

    from edge.shadow.kalshi_guard import (game_of, live_blocked, note_fill,
                                          open_kalshi_sides)

    stats: dict = {"tasks": 0, "placed": 0, "resting": 0, "cashed_out": 0}
    hdrs = {"X-Engine-Token": token}
    sess = requests.Session()

    # 1) Drain exit events (async maker-sell fills recorded by
    # sync_kalshi_fills). A failed report stays queued for the next
    # sweep — reporting must be at-least-once, the fill dedupe upstream
    # makes the platform update idempotent.
    q = ledger.get_state("kud_exit_events") or {}
    kept = []
    for ev in (q.get("events") or []):
        pnl = round((float(ev["price"]) - float(ev["entry"]))
                    * float(ev["qty"]), 2)
        try:
            r = sess.post(f"{base}/api/engine/kud-result",
                          json={"id": int(ev["task_id"]),
                                "status": "cashed_out",
                                "exit_price": float(ev["price"]),
                                "pnl": pnl},
                          headers=hdrs, timeout=10)
            if r.status_code != 200:
                raise RuntimeError(f"http_{r.status_code}")
            stats["cashed_out"] += 1
        except Exception:  # noqa: BLE001
            kept.append(ev)
            stats["report_fail"] = stats.get("report_fail", 0) + 1
    if q.get("events") is not None and kept != (q.get("events") or []):
        ledger.set_state("kud_exit_events", {"events": kept})

    def _report(task_id: int, status: str, **kw) -> None:
        try:
            sess.post(f"{base}/api/engine/kud-result",
                      json={"id": int(task_id), "status": status, **kw},
                      headers=hdrs, timeout=10)
        except Exception:  # noqa: BLE001
            stats["report_fail"] = stats.get("report_fail", 0) + 1

    # 2) Entries.
    try:
        r = sess.get(f"{base}/api/engine/kud-queue", headers=hdrs, timeout=10)
        tasks = (r.json().get("tasks") or []) if r.status_code == 200 else []
    except Exception as exc:  # noqa: BLE001
        stats["queue_error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
        tasks = []
    discovered: dict[str, list] = {}
    sides = open_kalshi_sides(ledger) if tasks else {}
    for t in tasks:
        stats["tasks"] += 1
        blocked = live_blocked(ledger, scope="underdog")
        if blocked:
            stats["blocked"] = blocked
            break
        if not live:
            stats["dry_run"] = stats.get("dry_run", 0) + 1
            continue
        league = str(t.get("league") or "").lower()
        if league not in discovered:
            try:
                discovered[league] = kalshi.discover_markets({league})
            except Exception:  # noqa: BLE001
                discovered[league] = []
        ticker = _resolve(discovered[league], t.get("dog_outcome") or "",
                          t.get("other_outcome") or "")
        if ticker is None:
            _report(t["id"], "no_market")
            stats["no_market"] = stats.get("no_market", 0) + 1
            continue
        # Non-interference: any existing engine position on this GAME —
        # either team, any sleeve, any series — vetoes the $1 entry.
        if sides.get(game_of(ticker)):
            _report(t["id"], "held")
            stats["held"] = stats.get("held", 0) + 1
            continue
        book = kalshi.get_book(ticker, ticker)
        ask = book.asks[0].price if book is not None and book.asks else None
        if ask is None or not (MIN_ASK <= ask <= MAX_ASK):
            _report(t["id"], "band_fail",
                    error=f"ask={ask}" if ask is not None else "no quote")
            stats["band_fail"] = stats.get("band_fail", 0) + 1
            continue
        limit = round(min(ask + 0.02, 0.99), 2)
        n = contracts_for(float(t.get("per_fill_usd") or 1.0), limit)
        if n < 1:
            _report(t["id"], "band_fail", error=f"zero contracts at {limit}")
            stats["band_fail"] = stats.get("band_fail", 0) + 1
            continue
        pr = kalshi.place_order(ticker, limit, n,
                                client_order_id=f"kud-{t['id']}-{uuid.uuid4().hex[:8]}",
                                taker=True)
        filled = int(float(pr.get("count") or 0)) if pr.get("ok") else 0
        if pr.get("ok") and filled > 0:
            # ENTRY BASIS = the ask, not the limit. An IOC fills at the
            # resting book price; the +2c limit is crash-protection we
            # almost never pay. Basing the +20% target on the limit
            # parked every exit ~7% too high (a 30c dog was offered at
            # 38c, +27%) — the bug behind exits never triggering at the
            # designed rate (owner report 2026-08-09 morning).
            if pr.get("order_id"):
                ledger.set_state(f"kalshi_inline:{pr['order_id']}",
                                 {"ts": time.time()})
            ledger.record_fill(
                fill_uid=f"kud-{t['id']}",
                venue="kalshi", market_key=f"kalshi:{ticker}",
                side="BUY", qty=float(filled), price=ask,
                fee=round(kalshi.taker_fee(ask) * filled, 4),
                league=league, mode="LIVE_BETA",
                category="kalshi_underdog",
                decision={"underdog": True, "task_id": t["id"],
                          "game_slug": t.get("game_slug"),
                          "dog": t.get("dog_outcome"), "limit": limit})
            note_fill(sides, ticker, ask, filled)
            thr = threshold_price(ask, float(t.get("take_profit") or 0.20))
            ledger.set_state(f"kud:{ticker}",
                             {"task_id": t["id"], "qty": filled,
                              "entry": ask, "thr": thr, "status": "open",
                              "ts": time.time()})
            idx = ledger.get_state("kud_index") or {}
            tickers = list(dict.fromkeys((idx.get("tickers") or []) + [ticker]))
            ledger.set_state("kud_index", {"tickers": tickers})
            _report(t["id"], "filled", ticker=ticker, entry_price=limit,
                    qty=filled)
            stats["placed"] += 1
            log.warning("KUD entry %s x%d @ %.2f (exit rests at %.2f)",
                        ticker, filled, limit, thr)
        elif pr.get("ok"):
            _report(t["id"], "unfilled", error="IOC zero fill")
            stats["unfilled"] = stats.get("unfilled", 0) + 1
        else:
            _report(t["id"], "error", error=str(pr.get("status"))[:200])
            stats["order_error"] = stats.get("order_error", 0) + 1

    # 3) Exits: keep one maker sell resting at the threshold for every
    # open $1 position. The venue expires rests at 15 minutes; re-rest
    # on expiry until the sell fills or the market closes/settles.
    idx = ledger.get_state("kud_index") or {}
    keep: list[str] = []
    now = time.time()
    open_keys = {(p.get("market_key") or "")
                 for p in ledger.open_positions(live_only=True)}
    for ticker in (idx.get("tickers") or []):
        st = ledger.get_state(f"kud:{ticker}") or {}
        if st.get("status") != "open":
            continue                      # cashed_out — drop from index
        if f"kalshi:{ticker}" not in open_keys:
            # Position left the book without our sell (settled/resolved).
            ledger.set_state(f"kud:{ticker}", {**st, "status": "settled"})
            continue
        keep.append(ticker)
        rest = ledger.get_state(f"kud_rest:{ticker}") or {}
        if float(rest.get("until", 0)) >= now:
            stats["resting"] += 1
            continue
        rr = kalshi.place_order(ticker, float(st["thr"]), int(st["qty"]),
                                client_order_id=f"kudx-{uuid.uuid4().hex[:12]}",
                                taker=False, sell=True, rest_s=REST_S)
        if rr.get("ok") and rr.get("order_id"):
            ledger.set_state(
                f"kalshi_order:{rr['order_id']}",
                {"market_key": f"kalshi:{ticker}",
                 "category": "kalshi_underdog_exit",
                 "task_id": st.get("task_id"), "entry": st.get("entry"),
                 "qty": st.get("qty"), "thr": st.get("thr")})
            ledger.set_state(f"kud_rest:{ticker}",
                             {"until": now + REST_S,
                              "order_id": rr["order_id"]})
            stats["resting"] += 1
        else:
            stats["exit_rest_fail"] = stats.get("exit_rest_fail", 0) + 1
    if keep != (idx.get("tickers") or []):
        ledger.set_state("kud_index", {"tickers": keep})
    return stats
