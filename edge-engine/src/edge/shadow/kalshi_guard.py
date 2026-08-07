"""Same-event exposure guard for Kalshi order flow.

Owner directive (2026-08-04, from the live account): both sides of single
matches showed up in the portfolio at prices summing over $1 — the copy
sweeps were following different whale positions onto opposite outcomes
with no awareness of each other. A pair like that is not an arb, it is a
guaranteed loss on the overlap whenever the combined cost exceeds the $1
payout.

The rule this module enforces: holding one side of a Kalshi event, we may
buy the OTHER side only when the completed pair is guaranteed profit —
new price + opposite side's average cost + the new leg's taker fee must
clear the $1 settlement payout with margin — and only in matched-pair
size (capped to the opposite side's share count), so no directional tail
rides in wearing an "arb" label. With no opposite side held, the trade is
directional and normal rules apply untouched.
"""

from __future__ import annotations

import time


def live_blocked(ledger, scope: str = "engine") -> str | None:
    """Honor the account-level stops from ANY order path.

    The kill switch, circuit-breaker halt, and watchdog live in shared
    ledger state, but only RiskManager.approve() consulted them — the
    sweeps (copies/adds/xv) spent real money straight through a halt.
    Every side-channel spender must call this before a live order.

    scope="engine" (default): the engine strategy's breaker (halt_until).
    scope="copy": the copy sleeve's OWN breaker (copy_halt_until) — owner
    directive 2026-08-05: copies are a separate strategy class graded on
    their own P&L, and an engine-side loss day must not pause profitable
    copying. Kill switch and watchdog stay global: they mean "the account
    or the inputs are unsafe", which no sleeve trades through.
    scope="arb": dutched guaranteed-margin pairs (xv_watch/xv_crypto) —
    owner directive 2026-08-06: "do not miss a single arbitrage
    opportunity". A daily-loss breaker exists to stop a bleeding
    strategy; a completed dutch pair locks its margin at entry and
    structurally cannot bleed, so NEITHER halt applies. Kill switch and
    watchdog still do — bad inputs make "guaranteed" a lie (a mapping
    error dutches two different games), and those are exactly the
    conditions the watchdog trips on.
    scope="manual": admin-directed desk tickets (owner 2026-08-07) — a
    human's deliberate order rides no strategy or sleeve halt; only the
    global stops apply.
    """
    if ledger.get_state("kill_switch", False):
        return "kill_switch"
    if scope not in ("arb", "manual"):
        halt_key = "copy_halt_until" if scope == "copy" else "halt_until"
        halt = ledger.get_state(halt_key)
        if halt and float(halt.get("until", 0)) > time.time():
            return "copy_halted" if scope == "copy" else "halted"
    wd = ledger.get_state("watchdog_tripped")
    if wd and wd.get("tripped"):
        return "watchdog"
    return None


def event_of(ticker: str) -> str:
    """'KXWTAMATCH-26AUG04ANDPLI-AND' -> 'KXWTAMATCH-26AUG04ANDPLI'."""
    parts = (ticker or "").rsplit("-", 1)
    return parts[0] if len(parts) == 2 else (ticker or "")


def game_of(ticker: str) -> str:
    """'KXMLBGAME-26AUG04BALTEX-BAL' -> '26AUG04BALTEX': the GAME identity,
    shared across the game/spread/total series of the same matchup. The
    event-level key let one game accumulate opposing positions through
    different series and different order paths (owner report 2026-08-05)."""
    parts = (ticker or "").split("-")
    return parts[1] if len(parts) >= 3 else (ticker or "")


def open_kalshi_sides(ledger) -> dict[str, list[dict]]:
    """Open LIVE kalshi positions grouped by event ticker."""
    out: dict[str, list[dict]] = {}
    for p in ledger.open_positions(live_only=True):
        mk = p.get("market_key") or ""
        if not mk.startswith("kalshi:"):
            continue
        ticker = mk.split(":", 1)[1]
        shares = float(p.get("shares") or 0)
        if shares <= 0:
            continue
        out.setdefault(game_of(ticker), []).append(
            {"ticker": ticker, "shares": shares,
             "avg_cost": float(p.get("avg_cost") or 0)})
    return out


def cross_side_cap(sides: dict[str, list[dict]], ticker: str, price: float,
                   want: int, fee_per_contract: float = 0.0,
                   margin: float = 0.01) -> int:
    """Contracts of `ticker` at `price` allowed given existing event sides.

    Returns `want` when we hold no opposite side (plain directional
    trade). Otherwise returns a matched-pair count only when the pair is
    guaranteed profit, else 0.
    """
    gm = game_of(ticker)
    others = [s for s in (sides.get(gm) or []) if s["ticker"] != ticker]
    if not others:
        return want
    # ONE position per GAME (owner rule 2026-08-05). A second position on
    # the same game is allowed ONLY as a guaranteed pair: the opposite
    # outcome of the SAME market event, priced so the pair locks profit.
    # Positions in a DIFFERENT market of the same game (spread vs
    # moneyline vs total) can never lock $1, so they are refused outright.
    same_event = [s for s in others if event_of(s["ticker"]) == event_of(ticker)]
    if len(same_event) != len(others):
        return 0
    other_shares = sum(s["shares"] for s in same_event)
    if other_shares <= 0:
        return want
    other_cost = sum(s["shares"] * s["avg_cost"] for s in same_event) / other_shares
    if price + other_cost + fee_per_contract > 1.0 - margin:
        return 0
    return max(0, min(want, int(other_shares)))


def note_fill(sides: dict[str, list[dict]], ticker: str, price: float,
              count: int) -> None:
    """Record an in-sweep fill so later orders in the SAME sweep see it —
    the ledger snapshot was taken at sweep start."""
    if count <= 0:
        return
    sides.setdefault(game_of(ticker), []).append(
        {"ticker": ticker, "shares": float(count), "avg_cost": price})
