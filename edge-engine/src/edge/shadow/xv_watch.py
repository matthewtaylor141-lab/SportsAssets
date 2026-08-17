"""24/7 cross-venue arbitrage watcher (owner directive 2026-08-04:
"a full time agent checking every price at Kalshi and Polymarket
simultaneously... I never want to miss guaranteed profit").

Arbitrage is model-free arithmetic, so it gets its own clock instead of
waiting for the fair-value sweep. Three design rules keep the speed from
becoming a new risk:

1. THE WATCHER NEVER MAPS. It only watches identity pairs the sweep has
   already proven (feed-team join, league-gated to no-tie two-way sports).
   Mapping mistakes are how fake arbitrage is born; the fast path reuses
   the slow path's certainty and adds none of its own.
2. NO CONTENTION WITH THE SWEEP. Kalshi books come from this thread's own
   REST session; Polymarket books are read from the push-stream cache
   only — never a competing REST call on the shared client.
3. SAME GUARANTEES AS THE SLOW PATH. Fee-loaded cost, minimum profit,
   believability cap, once-per-event ledger claim (shared with the sweep,
   so the two paths can never double-fire), atomicity-ordered execution,
   every fill ledger-recorded, and a daily spend cap for the class.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from edge.execution.arbitrage import (
    MAX_BELIEVABLE_PROFIT,
    XVLeg,
    cross_venue_cost,
    execute_cross_venue,
)

log = logging.getLogger(__name__)


class XVWatch:
    def __init__(self, *, ledger, is_live, min_profit: float = 0.02,
                 max_usd: float = 10.0, day_usd: float = 200.0,
                 poll_s: float = 3.0, scan_cap: int = 40) -> None:
        self._ledger = ledger
        self._is_live = is_live          # callable: () -> bool
        self.min_profit = float(min_profit)
        self.max_usd = float(max_usd)
        self.day_usd = float(day_usd)
        self.poll_s = float(poll_s)
        self.scan_cap = int(scan_cap)
        self._lock = threading.Lock()
        self._registry: dict = {}        # event_key -> entry
        self._rr = 0                     # round-robin cursor
        self.stats = {"scans": 0, "pairs": 0, "best_gap_c": -99.0,
                      "fired": 0, "profit": 0.0, "exposed": 0,
                      "skipped_day_cap": 0, "last_hit": None}

    # ── published by the sweep ──────────────────────────────────────────
    def publish(self, event_key: str, label: str, league: str | None,
                pool: dict, ttl_s: float = 6 * 3600.0) -> None:
        """pool: {feed_team: {venue_name: XVLeg}} — the sweep's proven join.
        Tokens are what matter; prices are re-read fresh every poll."""
        legs = {}
        for team, sides in pool.items():
            if len(sides) >= 2:
                legs[team] = dict(sides)
        if len(legs) != 2:
            return
        # OWNER ORDER 2026-08-17 night: no Kalshi tennis outside the
        # FSC sleeve — a tennis pair is never registered, so the watch
        # can never fire a tennis leg on Kalshi. (Tennis arbs are
        # therefore off; the owner can re-open them by widening this.)
        from edge.shadow.kalshi_guard import TENNIS_LEAGUES, \
            is_tennis_ticker
        if (str(league or "").lower() in TENNIS_LEAGUES
                or any(is_tennis_ticker(leg.token)
                       for sides in legs.values()
                       for leg in sides.values())):
            return
        with self._lock:
            self._registry[event_key] = {
                "label": label, "league": league, "legs": legs,
                "expires": time.time() + ttl_s}

    def registered(self) -> int:
        with self._lock:
            return len(self._registry)

    # ── the 24/7 loop ───────────────────────────────────────────────────
    def run(self) -> None:
        while True:
            try:
                self._tick()
            except Exception as exc:  # noqa: BLE001 — the watch never dies
                log.warning("xv watch tick failed: %s", exc)
            time.sleep(self.poll_s)

    def _tick(self) -> None:
        now = time.time()
        with self._lock:
            self._registry = {k: v for k, v in self._registry.items()
                              if v["expires"] > now}
            keys = sorted(self._registry)
        if not keys:
            return
        self.stats["scans"] += 1
        start, n = self._rr, len(keys)
        batch = [keys[(start + i) % n] for i in range(min(self.scan_cap, n))]
        self._rr = (start + len(batch)) % n
        for key in batch:
            with self._lock:
                entry = self._registry.get(key)
            if entry is None:
                continue
            self._check(key, entry)

    def _fresh_leg(self, leg: XVLeg):
        """Re-price one leg from the live book, venue-appropriately."""
        adapter = leg.adapter
        name = getattr(adapter, "name", "")
        if name == "kalshi":
            book = adapter.get_book(leg.token, leg.token)
        else:
            peek = getattr(adapter, "peek_book", None)
            book = peek(leg.token) if peek else None
        if book is None or not book.asks:
            return None
        ask = book.asks[0]
        if ask.size < 1:
            return None
        return XVLeg(adapter=adapter, token=leg.token, outcome=leg.outcome,
                     price=ask.price, size=float(ask.size))

    def _check(self, event_key: str, entry: dict) -> None:
        (t1, sides1), (t2, sides2) = entry["legs"].items()
        fresh1 = {v: self._fresh_leg(l) for v, l in sides1.items()}
        fresh2 = {v: self._fresh_leg(l) for v, l in sides2.items()}
        best, best_cost = None, 10.0
        for v1, l1 in fresh1.items():
            if l1 is None:
                continue
            for v2, l2 in fresh2.items():
                if l2 is None or v1 == v2:
                    continue
                cost = cross_venue_cost([l1, l2])
                if cost < best_cost:
                    best, best_cost = [l1, l2], cost
        if best is None:
            return
        # Exposure freeze (owner directive 2026-08-08): one naked hold is
        # a diagnosis event, not a cadence — no further arb fires until
        # the freeze expires or a human clears the state.
        frozen = self._ledger.get_state("xv_exposed_block") or {}
        if float(frozen.get("until", 0)) > time.time():
            self.stats["blocked_exposed"] = \
                self.stats.get("blocked_exposed", 0) + 1
            return
        self.stats["pairs"] += 1
        profit = round(1.0 - best_cost, 4)
        self.stats["best_gap_c"] = max(self.stats["best_gap_c"],
                                       round(profit * 100, 2))
        if profit < self.min_profit or profit > MAX_BELIEVABLE_PROFIT:
            return
        claim = f"xv_tried:{event_key}"        # SHARED with the sweep path
        day = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        spend = self._ledger.get_state("xv_watch_day") or {}
        spent = float(spend.get("spent", 0.0)) if spend.get("day") == day else 0.0
        if spent >= self.day_usd:
            self.stats["skipped_day_cap"] += 1
            return
        live = bool(self._is_live())
        if live:
            from edge.shadow.kalshi_guard import live_blocked

            # Arb scope (owner directive 2026-08-06): a dutched pair locks
            # its margin at entry — the daily-loss halts don't apply, only
            # the global kill switch/watchdog do.
            blocked = live_blocked(self._ledger, scope="arb")
            if blocked:
                self.stats["blocked_" + blocked] = \
                    self.stats.get("blocked_" + blocked, 0) + 1
                return
            # ATOMIC claim BEFORE execution: get-then-set left a multi-
            # second window in which this 3s watcher and the sweep both
            # passed the check and each bought the full set.
            if not self._ledger.claim_event(claim, event_key, "xv"):
                return
        # Same-venue complements: the other outcome's leg on each chosen
        # leg's OWN venue — the relock path's raw material.
        l1, l2 = best
        v1 = getattr(l1.adapter, "name", "")
        v2 = getattr(l2.adapter, "name", "")
        comps = {}
        if fresh2.get(v1) is not None:
            comps[l1.token] = fresh2[v1]
        if fresh1.get(v2) is not None:
            comps[l2.token] = fresh1[v2]
        res = execute_cross_venue(
            event=claim, legs=best,
            max_sets=max(1, int(min(self.max_usd, self.day_usd - spent)
                                / best_cost)),
            dry_run=not live, complements=comps)
        self.stats["fired"] += 1
        self.stats["last_hit"] = {"event": entry["label"],
                                  "profit_per_set": profit,
                                  "status": res.status, "sets": res.sets,
                                  "at": day}
        log.warning("XV WATCH %s: %s profit/set %.3f sets %s",
                    res.status, entry["label"], profit, res.sets)
        if not live:
            return
        if res.status in ("no_fills", "nothing_to_do", "not_cross_venue",
                          "implausible_profit"):
            # Clean miss: give the claim back so the pair stays retryable.
            self._ledger.release_event(claim)
        else:
            self._ledger.set_state(claim, {"ts": time.time(),
                                           "status": res.status,
                                           "via": "xv_watch"})
        for o in res.orders:
            if o.get("ok") and o.get("order_id") \
                    and float(o.get("count") or 0) > 0:
                self._ledger.set_state(f"kalshi_inline:{o['order_id']}",
                                       {"ts": time.time()})
        if res.status == "INCOMPLETE_EXPOSED":
            self.stats["exposed"] += 1
            # Self-freeze for 6h: an exposure means both the cross-venue
            # closer AND the same-venue relock failed — something is wrong
            # with the pair identity or the books, and firing again into
            # the same condition is how 1-9 happened.
            self._ledger.set_state("xv_exposed_block", {
                "until": time.time() + 6 * 3600,
                "event": entry["label"], "at": time.time()})
        if res.status == "relocked_same_venue":
            self.stats["relocked"] = self.stats.get("relocked", 0) + 1
        legs_by_token = {l.token: l for l in best}
        filled_cost = 0.0
        for o in res.orders:
            if not (o.get("ok") and float(o.get("count") or 0) > 0):
                continue
            leg = legs_by_token.get(o.get("token") or "")
            if leg is None:
                continue
            vname = getattr(leg.adapter, "name", "?")
            qty = float(o.get("count") or res.sets)
            px = float(o.get("price") or leg.price)
            filled_cost += qty * px
            self._ledger.record_fill(
                fill_uid=f"xvw-{claim}-{leg.token}-{int(time.time())}",
                venue=vname, market_key=f"{vname}:{leg.token}",
                side="BUY", qty=qty, price=px,
                fee=round(leg.fee() * qty, 4),
                league=entry["league"], mode="LIVE_BETA", category="arb",
                decision={"arb": True, "cross_venue": True,
                          "via": "xv_watch", "cost_per_set": best_cost,
                          "profit_per_set": profit, "status": res.status,
                          "legs": [f"{getattr(l.adapter, 'name', '?')}:"
                                   f"{l.outcome}" for l in best]})
            # Import at call time: runner imports this module at wiring.
            from edge.shadow.runner import mirror_side_channel_fill
            mirror_side_channel_fill(
                venue=vname, slug=leg.token, price=px, qty=qty,
                league=entry["league"], category="arb")
        if filled_cost:
            self._ledger.set_state("xv_watch_day",
                                   {"day": day,
                                    "spent": round(spent + filled_cost, 2)})
        if res.ok and res.complete:
            self.stats["profit"] = round(self.stats["profit"] + res.profit, 4)
