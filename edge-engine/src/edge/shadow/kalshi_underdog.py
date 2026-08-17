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
import os
import time
import uuid

log = logging.getLogger(__name__)

# Band widened 2026-08-09 (owner: "this should be firing every single
# mlb game and every single tennis match") — near-even games where the
# dog sits at 49-50c were the biggest silent skip. The dog is still
# strictly the CHEAPER side; 50/50 books have no dog and wait for one.
MIN_ASK = 0.03
MAX_ASK = 0.50
# T-minus-5 window, run HERE off the task's start_ts (the brain used to
# gate the queue on its own window and Polymarket's prices, so a game
# that missed either never reached Kalshi at all). Inside the window a
# miss retries every sweep at the fresh book; only when the window
# closes does a reason go terminal.
# Grace 1800 -> 3600 (owner approval 2026-08-10): 7 of the sleeve's
# first-week misses were windows that expired during engine deploys or
# late catalogue arrivals. A late entry is SAFE — the dog is picked from
# Kalshi's LIVE book at entry moment and the [0.03, 0.50] band re-checks
# there, so the only cost is less remaining time for the +20% exit. The
# window is now [start-5min, start+60min].
LEAD_S = 300
GRACE_S = float(os.environ.get("EDGE_KUD_GRACE_S", "3600"))
# Exit rests live HOURS, not the copies' 15 minutes: a +20% target does
# not go stale (it IS the strategy), and 15-minute churn left dozens of
# expired sell orders in the venue's history — which read as "a bunch
# sold" (owner 2026-08-09 morning). Long rests also keep queue priority.
REST_S = 4 * 3600


def threshold_price(entry: float, take: float = 0.20) -> float:
    """The resting sell limit IS the +20% trigger: a maker fill at or
    above it realizes the take on dollars spent. Capped at 99c, floored
    a full tick above entry — round() on a cheap dog (2c * 1.2 = 2.4c)
    otherwise parks the "profit" exit AT the entry price."""
    return round(min(max(entry * (1.0 + take), entry + 0.01), 0.99), 2)


def contracts_for(usd: float, limit: float) -> int:
    """Whole contracts such that count * limit never exceeds the dollar."""
    if limit <= 0:
        return 0
    return int(usd / limit)


def _resolve_game(discovered: list, side_a: str, side_b: str
                  ) -> tuple[str, str] | None:
    """(ticker_a, ticker_b) for the matchup, matched by BOTH outcome
    names — one at the mapper bar and the other confirming the same
    matchup (one name alone matches 'same player, different match').
    The caller picks the dog from the venue's own book; the task's two
    names are just the matchup, in no meaningful order."""
    from edge.venues.mapper import team_score

    for vm in discovered:
        names = list(vm.outcome_tokens)
        if len(names) != 2:
            continue
        for a_name, b_name in ((names[0], names[1]), (names[1], names[0])):
            if (team_score(a_name, side_a) >= 0.9
                    and team_score(b_name, side_b) >= 0.6):
                return vm.outcome_tokens[a_name], vm.outcome_tokens[b_name]
            if (team_score(b_name, side_b) >= 0.9
                    and team_score(a_name, side_a) >= 0.6):
                return vm.outcome_tokens[a_name], vm.outcome_tokens[b_name]
    return None


def window_state(start_ts: float | None, now: float,
                 lead: float = LEAD_S, grace: float = GRACE_S) -> str:
    """'enter' inside [start - lead, start + grace]; 'wait' before;
    'closed' after. A task with no start fires at first sight — the
    brain could not honor a window either, and 'every game' beats
    'every game we could time'. Pure."""
    if not start_ts:
        return "enter"
    if now < start_ts - lead:
        return "wait"
    if now > start_ts + grace:
        return "closed"
    return "enter"


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
    # ENTRIES PAUSED by default (owner directive 2026-08-11, profitability
    # review): the +20% exit sell has never once filled on this venue —
    # zero cash-outs lifetime — so every $1 dog degraded into
    # hold-to-resolution and the sleeve graded 12W/39L, -72% ROI. The
    # PMUS underdog sleeve (whose exits DO fill) is untouched, and
    # sections 1 and 3 here keep draining exit events and re-resting
    # sells for positions already held. Re-arm with EDGE_KUD_ENTRIES=1
    # only once the exit mechanics are proven.
    entries_live = os.environ.get("EDGE_KUD_ENTRIES", "0") == "1"
    discovered: dict[str, list | None] = {}
    sides = open_kalshi_sides(ledger) if tasks else {}
    for t in tasks:
        stats["tasks"] += 1
        if not entries_live:
            stats["entries_paused"] = stats.get("entries_paused", 0) + 1
            continue
        blocked = live_blocked(ledger, scope="underdog")
        if blocked:
            stats["blocked"] = blocked
            break
        if not live:
            stats["dry_run"] = stats.get("dry_run", 0) + 1
            continue
        # The window runs HERE now (owner 2026-08-09: "firing every
        # single mlb game and every single tennis match"): the queue
        # holds the whole slate from first sight, tasks wait for their
        # own T-minus-5, retry every sweep inside it, and a game whose
        # window already closed reports 'missed' instead of entering
        # half-played. A task with no start fires at first sight but
        # gets ONE shot — an unlisted matchup must not clog the queue
        # retrying forever.
        start_ts = t.get("start_ts")
        oneshot = not start_ts
        win = window_state(start_ts, time.time())
        if win == "wait":
            stats["waiting"] = stats.get("waiting", 0) + 1
            continue
        if win == "closed":
            _report(t["id"], "missed", error="window closed before entry")
            stats["missed"] = stats.get("missed", 0) + 1
            continue

        def _fail(task, reason: str, detail: str = "") -> None:
            """Terminal only when this was the task's single shot;
            otherwise stay queued and retry at the next sweep's book."""
            if oneshot:
                _report(task["id"], reason, error=detail[:200])
                stats[reason] = stats.get(reason, 0) + 1
            else:
                k = f"retry_{reason}"
                stats[k] = stats.get(k, 0) + 1

        league = str(t.get("league") or "").lower()
        # OWNER ORDER 2026-08-17 night: on Kalshi, tennis belongs to
        # the first-set-comeback sleeve ALONE. A tennis task is refused
        # terminally — even if this sleeve is later unpaused, it can
        # never place a Kalshi tennis order again.
        from edge.shadow.kalshi_guard import TENNIS_LEAGUES
        if league in TENNIS_LEAGUES:
            _report(t["id"], "tennis_blocked",
                    error="Kalshi tennis is FSC-only (owner 2026-08-17)")
            stats["tennis_blocked"] = stats.get("tennis_blocked", 0) + 1
            continue
        if league not in discovered:
            try:
                discovered[league] = kalshi.discover_markets({league})
            except Exception:  # noqa: BLE001
                # None = DISCOVERY failed (venue/network hiccup), which
                # must stay retryable even for oneshot tasks — the boot
                # sweep now runs immediately and a cold adapter must not
                # burn every no-start task's single shot as 'no_market'.
                # An EMPTY list means discovery succeeded and the game
                # genuinely is not listed; that stays terminal below.
                discovered[league] = None
        if discovered[league] is None:
            stats["retry_discovery"] = stats.get("retry_discovery", 0) + 1
            continue
        pair = _resolve_game(discovered[league], t.get("dog_outcome") or "",
                             t.get("other_outcome") or "")
        if pair is None:
            _fail(t, "no_market")
            continue
        # THE DOG IS THE VENUE'S DOG: strictly the cheaper ask on
        # Kalshi's own book, decided at entry time — never Polymarket's
        # opinion relayed through the queue. Both sides must quote (a
        # one-sided book cannot name a dog) and a 50/50 book has none.
        asks = []
        for tk in pair:
            book = kalshi.get_book(tk, tk)
            a = book.asks[0].price if book is not None and book.asks else None
            if a is not None:
                asks.append((tk, a))
        if len(asks) != 2:
            _fail(t, "band_fail", "one-sided or empty book")
            continue
        asks.sort(key=lambda p: p[1])
        (ticker, ask), (_, fav_ask) = asks
        if ask >= fav_ask:
            _fail(t, "band_fail", f"no dog: {ask:.2f}/{fav_ask:.2f}")
            continue
        if not (MIN_ASK <= ask <= MAX_ASK):
            _fail(t, "band_fail", f"ask={ask}")
            continue
        # Non-interference: any existing engine position on this GAME —
        # either team, any sleeve, any series — vetoes the $1 entry.
        if sides.get(game_of(ticker)):
            _report(t["id"], "held")
            stats["held"] = stats.get("held", 0) + 1
            continue
        limit = round(min(ask + 0.02, 0.99), 2)
        # HARD $1 CLAMP at the point of order (2026-08-10 evening): the
        # sleeve's settled cohort shows ~$11.66 average entries against
        # the owner's $1 directive — the queue's per_fill_usd is DATA,
        # not authority, and a bad row must not size a real order. The
        # ceiling is env-tunable for the day the owner raises the sleeve.
        per_usd = min(float(t.get("per_fill_usd") or 1.0),
                      float(os.environ.get("EDGE_KUD_MAX_USD", "1.0")))
        n = contracts_for(per_usd, limit)
        if n < 1:
            _fail(t, "band_fail", f"zero contracts at {limit}")
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
            _report(t["id"], "filled", ticker=ticker, entry_price=ask,
                    qty=filled)
            stats["placed"] += 1
            log.warning("KUD entry %s x%d @ %.2f (exit rests at %.2f)",
                        ticker, filled, limit, thr)
        elif pr.get("ok"):
            # A zero-fill IOC at a moved price retries at the NEXT
            # sweep's fresh book while the window is open — repricing
            # per cycle, never re-firing the same order blind.
            _fail(t, "unfilled", "IOC zero fill")
        else:
            _fail(t, "error", str(pr.get("status"))[:200])

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
