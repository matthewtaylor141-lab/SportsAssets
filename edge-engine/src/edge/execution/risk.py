"""Risk layer (build step 6): modes, caps, circuit breaker, watchdog, kill
switch. Every order — paper or live — passes through RiskManager.approve();
there is no other path to an order.

Modes (config/risk.yaml `mode`, strictly ordered):
  PAPER      full decision loop, orders logged to the ledger, never sent.
  LIVE_BETA  real orders under the beta profile; requires a human editing
             risk.yaml AND a clean `edge check-live` at startup.
  LIVE       full measured caps; additionally gated by the shadow grader
             (>=60d, >=5,000 graded fills/venue, >=1.5% net ROI). The gate is
             evaluated from grader output — there is no code path around it.

Circuit breaker: daily realized+marked <= -halt threshold => halt ALL trading
for halt_hours, then auto-resume at default sizes. Deliberately, there is no
manual override or early-resume function in this codebase.

Kill switch: `edge kill` (state flag). Watchdog: trips on stale feed, mapper
confidence collapse, venue error bursts, or clock skew; clears itself only
when the inputs are healthy again.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from edge.ledger.service import Ledger

log = logging.getLogger(__name__)

MODES = ("PAPER", "LIVE_BETA", "LIVE")

# State keys (ledger state hub).
KILL_SWITCH = "kill_switch"
HALT_UNTIL = "halt_until"          # circuit breaker
WATCHDOG = "watchdog_tripped"


@dataclass
class Caps:
    per_fill_default: float
    per_fill_max: float
    per_market: float
    per_day: float
    one_per_event: bool
    daily_loss_halt: float          # positive number; halt at -this
    halt_hours: float
    venue_bankroll_split: float     # fraction of per_day each venue may use


def caps_for_mode(risk_cfg: dict, mode: str) -> Caps:
    """LIVE uses the measured top-level caps; LIVE_BETA the beta profile;
    PAPER the sampling profile (falls back to beta) — paper dollars are
    free, so evidence velocity is capped only by opportunity supply."""
    profiles = risk_cfg.get("profiles") or {}
    if mode == "LIVE":
        src = risk_cfg
    elif mode == "PAPER":
        src = {**risk_cfg, **(profiles.get("paper") or profiles.get("live_beta") or {})}
    else:
        src = {**risk_cfg, **(profiles.get("live_beta") or {})}
    return Caps(
        per_fill_default=float(src.get("per_fill_usd_default", 10)),
        per_fill_max=float(src.get("per_fill_usd_max", 25)),
        per_market=float(src.get("per_market_exposure_usd", 50)),
        per_day=float(src.get("per_day_deployment_usd", 250)),
        one_per_event=bool(src.get("one_position_per_event", mode != "LIVE")),
        daily_loss_halt=float(src.get("daily_loss_halt_usd", 100)),
        halt_hours=float(src.get("halt_hours", 72)),
        venue_bankroll_split=float(src.get("venue_bankroll_split", 0.5)),
    )


class RiskManager:
    def __init__(self, ledger: Ledger, risk_cfg: dict) -> None:
        self.ledger = ledger
        mode = str(risk_cfg.get("mode", "PAPER")).upper()
        if mode not in MODES:
            log.warning("unknown mode %r — forcing PAPER", mode)
            mode = "PAPER"
        self.mode = mode
        self.caps = caps_for_mode(risk_cfg, mode)
        self.risk_cfg = risk_cfg

    @property
    def is_live(self) -> bool:
        return self.mode in ("LIVE_BETA", "LIVE")

    def force_paper(self) -> None:
        """Demote to PAPER (e.g. failed go-live checklist)."""
        self.set_mode("PAPER")

    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode!r}")
        self.mode = mode
        self.caps = caps_for_mode(self.risk_cfg, mode)

    # ── guards (checked before every order) ─────────────────────────────

    def guard(self, now: float | None = None) -> tuple[bool, str]:
        """Global no-trade conditions. Live modes stop; PAPER keeps logging
        (the paper record shows what live WOULD have been blocked from)."""
        now = now or time.time()
        if self.ledger.get_state(KILL_SWITCH, False):
            return False, "kill_switch"
        halt = self.ledger.get_state(HALT_UNTIL)
        if halt and now < float(halt.get("until", 0)):
            return False, f"circuit_breaker_halt until {halt.get('until')}"
        wd = self.ledger.get_state(WATCHDOG)
        if wd and wd.get("tripped"):
            return False, f"watchdog: {wd.get('reason')}"
        return True, "ok"

    # ── circuit breaker ─────────────────────────────────────────────────

    def check_circuit_breaker(self, marked_delta_usd: float = 0.0,
                              now: float | None = None) -> bool:
        """Evaluate daily realized+marked; trip the 72h halt if breached.
        Returns True if halted. Auto-resume happens by time passing — there
        is intentionally no function that clears HALT_UNTIL early."""
        now = now or time.time()
        halt = self.ledger.get_state(HALT_UNTIL)
        if halt and now < float(halt.get("until", 0)):
            return True
        # LIVE-money numbers only: paper marks/realizations must never halt
        # real trading (bug class: junk paper fills tripping the breaker).
        day_pnl = self.ledger.realized_pnl_since(now - 86_400, live_only=True) \
            + marked_delta_usd
        if day_pnl <= -self.caps.daily_loss_halt:
            until = now + self.caps.halt_hours * 3600
            self.ledger.set_state(HALT_UNTIL, {
                "until": until, "reason": "daily_loss_circuit_breaker",
                "day_pnl": round(day_pnl, 2), "tripped_at": now,
            })
            log.error("CIRCUIT BREAKER: day PnL %.2f <= -%.2f — halting %sh",
                      day_pnl, self.caps.daily_loss_halt, self.caps.halt_hours)
            return True
        return False

    # ── watchdog ────────────────────────────────────────────────────────

    def watchdog(self, feed_age_s: float, clock_skew_s: float | None,
                 venue_errors: int, tradeable_rate: float | None,
                 now: float | None = None) -> tuple[bool, str]:
        """Trip on unhealthy inputs; clear only when healthy again."""
        cfg = self.risk_cfg.get("watchdog") or {}
        reason = ""
        if feed_age_s > float(cfg.get("max_feed_age_s", 60)):
            reason = f"feed stale {feed_age_s:.0f}s"
        elif clock_skew_s is not None and abs(clock_skew_s) > float(cfg.get("max_clock_skew_s", 5)):
            reason = f"clock skew {clock_skew_s:.1f}s"
        elif venue_errors > int(cfg.get("max_venue_errors_per_cycle", 25)):
            reason = f"venue errors {venue_errors}/cycle"
        elif tradeable_rate is not None and tradeable_rate < float(cfg.get("min_tradeable_rate", 0.5)):
            reason = f"mapper confidence collapsed ({tradeable_rate:.0%} tradeable)"
        if reason:
            self.ledger.set_state(WATCHDOG, {"tripped": True, "reason": reason,
                                             "ts": now or time.time()})
            log.error("WATCHDOG tripped: %s", reason)
            return True, reason
        if self.ledger.get_state(WATCHDOG, {}).get("tripped"):
            self.ledger.set_state(WATCHDOG, {"tripped": False, "reason": "",
                                             "ts": now or time.time()})
            log.info("watchdog cleared — inputs healthy")
        return False, ""

    # ── exposure accounting (from the ledger, not in-memory) ────────────

    def day_deployed(self, venue: str | None = None, now: float | None = None,
                     mode: str | None = None) -> float:
        now = now or time.time()
        with self.ledger._conn() as conn:  # noqa: SLF001 — same package
            q = ("SELECT COALESCE(sum(qty * price), 0) FROM fills "
                 "WHERE side='BUY' AND ts >= ? AND mode = ?")
            args: list = [now - 86_400, mode or self.mode]
            if venue:
                q += " AND venue = ?"
                args.append(venue)
            row = conn.execute(q, args).fetchone()
        return float(row[0])

    def market_open_cost(self, market_key: str) -> float:
        pos = self.ledger.position(market_key)
        if not pos or pos["resolved"]:
            return 0.0
        return float(pos["shares"]) * float(pos["avg_cost"])

    # ── the single order gate ───────────────────────────────────────────

    def approve(self, venue: str, market_key: str, event_key: str,
                requested_usd: float, now: float | None = None,
                mode: str | None = None) -> tuple[float, str]:
        """Returns (approved_usd, reason). approved_usd == 0 means no order.
        Every cap is applied here; property tests assert no sequence of calls
        can exceed any cap. NOTE: approval CLAIMS the event — call only when
        an order will actually be logged/placed on approval.

        mode: effective mode for THIS order (a live engine still paper-logs
        venues outside EDGE_LIVE_VENUES; their caps and day-budget are the
        PAPER profile's, accounted separately by fill mode)."""
        now = now or time.time()
        mode = mode or self.mode
        caps = self.caps if mode == self.mode else caps_for_mode(self.risk_cfg, mode)
        ok, why = self.guard(now)
        if not ok:
            return 0.0, why

        size = min(requested_usd, caps.per_fill_default, caps.per_fill_max)
        market_room = caps.per_market - self.market_open_cost(market_key)
        venue_day_cap = caps.per_day * caps.venue_bankroll_split
        day_room = min(caps.per_day - self.day_deployed(now=now, mode=mode),
                       venue_day_cap - self.day_deployed(venue=venue, now=now, mode=mode))
        size = round(min(size, market_room, day_room), 2)
        if size < 1.0:
            return 0.0, "caps: no room (per-market/day/venue)"

        if caps.one_per_event:
            # PAPER claims per (venue, event): both venues grade the same
            # game independently (the spec's venue-choice experiment).
            # LIVE claims the event globally: one real position, ever.
            key = event_key if mode != "PAPER" else f"paper:{venue}:{event_key}"
            if not self.ledger.claim_event(key, market_key, venue, ts=now):
                return 0.0, "one-per-event: already positioned"
        return size, "ok"
