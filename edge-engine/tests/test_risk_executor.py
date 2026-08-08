"""Build steps 5-8 gates.

The core is the property test: thousands of randomized approve/fill
sequences, asserting NO sequence can exceed per-fill, per-market, per-day,
or per-venue caps, or place twice on one event. Plus: maker-first planning,
circuit breaker (trip + auto-resume + no-override), watchdog, kill switch,
paper executor, and nightly-report divergence alerts.
"""

import random
import time

import pytest

from edge.execution.executor import execute, market_key
from edge.execution.risk import HALT_UNTIL, KILL_SWITCH, RiskManager, caps_for_mode
from edge.ledger.service import Ledger
from edge.venues.base import BookLevel, MarketBook
from edge.venues.kalshi import KalshiAdapter

RISK_CFG = {
    "mode": "PAPER",
    "per_fill_usd_default": 5000, "per_fill_usd_max": 10000,
    "per_market_exposure_usd": 25000, "per_day_deployment_usd": 250000,
    "profiles": {"live_beta": {
        "per_fill_usd_default": 10, "per_fill_usd_max": 25,
        "per_market_exposure_usd": 50, "per_day_deployment_usd": 250,
        "one_position_per_market": True, "daily_loss_halt_usd": 100,
        "halt_hours": 72, "venue_bankroll_split": 0.5,
    }},
    "watchdog": {"max_feed_age_s": 60, "max_clock_skew_s": 5,
                 "max_venue_errors_per_cycle": 25, "min_tradeable_rate": 0.5},
}


@pytest.fixture
def rig(tmp_path):
    ledger = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    return ledger, RiskManager(ledger, RISK_CFG)


def _paper_fill(ledger, venue, mkey, usd, price=0.50, ts=None):
    ledger.record_fill(
        fill_uid=f"{mkey}-{usd}-{random.random()}", venue=venue, market_key=mkey,
        side="BUY", qty=usd / price, price=price, ts=ts or time.time(), mode="PAPER")


# ── caps: property test ────────────────────────────────────────────────

def test_property_no_sequence_exceeds_any_cap(rig):
    ledger, risk = rig
    caps = risk.caps
    rng = random.Random(42)
    venues = ["kalshi", "polymarket-us"]
    filled_by_market: dict[str, float] = {}
    filled_by_venue: dict[str, float] = {}
    events_filled: set[str] = set()
    now = time.time()

    for i in range(3000):
        venue = rng.choice(venues)
        event = f"ev{rng.randrange(200)}"
        mkey = f"{venue}:m{event}-{rng.randrange(2)}"
        requested = rng.choice([0.5, 5, 10, 25, 100, 10_000])
        approved, why = risk.approve(venue, mkey, event, requested, now=now)

        assert approved <= caps.per_fill_max + 1e-9
        assert approved <= requested + 1e-9
        if approved > 0:
            # One position per BET: a market is entered once, never added to.
            # (Sibling markets of the same game are separate bets — that is
            # the point of claiming the market rather than the event.)
            assert mkey not in events_filled, "one-per-market violated"
            events_filled.add(mkey)
            _paper_fill(ledger, venue, mkey, approved, ts=now)
            filled_by_market[mkey] = filled_by_market.get(mkey, 0) + approved
            filled_by_venue[venue] = filled_by_venue.get(venue, 0) + approved
            assert filled_by_market[mkey] <= caps.per_market + 1e-6
            assert filled_by_venue[venue] <= caps.per_day * caps.venue_bankroll_split + 1e-6
            assert sum(filled_by_venue.values()) <= caps.per_day + 1e-6

    assert sum(filled_by_venue.values()) > 0  # the test actually filled things
    # Day cap is binding: both venue halves exhausted at $125 each.
    assert sum(filled_by_venue.values()) <= caps.per_day


def test_per_market_cap_counts_open_positions(rig):
    """LIVE mode: no one-per-market claim, so the exposure cap is what stops
    a market being loaded up. (In the beta profile the claim stops it first.)"""
    ledger, risk = rig
    now = time.time()
    a1, _ = risk.approve("kalshi", "kalshi:mX", "ev-a", 25_000, now=now, mode="LIVE")
    assert a1 > 0
    _paper_fill(ledger, "kalshi", "kalshi:mX", 22_000, price=0.5, ts=now)
    a2, _ = risk.approve("kalshi", "kalshi:mX", "ev-b", 25_000, now=now, mode="LIVE")
    assert a2 == pytest.approx(3_000.0)   # clamped to remaining market room
    _paper_fill(ledger, "kalshi", "kalshi:mX", 3_000, price=0.5, ts=now)
    a3, why = risk.approve("kalshi", "kalshi:mX", "ev-c", 25_000, now=now, mode="LIVE")
    assert a3 == 0 and "caps" in why


def test_approve_claims_each_market_once_not_the_whole_game(rig):
    """The volume rule: a game lists a moneyline plus a ladder of spreads and
    totals, each independently priced. Entering one must not retire the rest
    — but re-entering the SAME bet is still forbidden."""
    _, risk = rig
    now = time.time()
    a1, _ = risk.approve("kalshi", "kalshi:ars-ml", "event-1", 10, now=now)
    # A different bet on the same game: allowed, it is a different bet.
    a2, _ = risk.approve("kalshi", "kalshi:over-2.5", "event-1", 10, now=now)
    a3, _ = risk.approve("kalshi", "kalshi:ars-1.5", "event-1", 10, now=now)
    assert a1 > 0 and a2 > 0 and a3 > 0
    # The same bet twice: never.
    a4, why = risk.approve("kalshi", "kalshi:ars-ml", "event-1", 10, now=now)
    assert a4 == 0 and "one-per-market" in why
    # Other venue samples the same market independently (PAPER experiment).
    a5, _ = risk.approve("polymarket-us", "polymarket-us:ars-ml", "event-1",
                         10, now=now)
    assert a5 > 0


# ── circuit breaker ────────────────────────────────────────────────────

def test_circuit_breaker_trips_and_auto_resumes(rig):
    ledger, risk = rig
    now = time.time()
    # Realize a -$120 day via a resolved losing LIVE position (paper losses
    # are excluded by design — see test_breaker_ignores_paper_losses).
    ledger.record_fill(fill_uid="cb1", venue="kalshi", market_key="kalshi:cb",
                       side="BUY", qty=240, price=0.50, ts=now - 100, mode="LIVE_BETA")
    ledger.record_resolution("kalshi:cb", 0.0, ts=now - 50)  # -$120 REAL
    assert risk.check_circuit_breaker(now=now) is True
    ok, why = risk.guard(now=now)
    assert not ok and "circuit_breaker" in why
    approved, _ = risk.approve("kalshi", "kalshi:m", "ev", 10, now=now)
    assert approved == 0
    # Auto-resume: after halt_hours the guard clears by time alone.
    later = now + 72 * 3600 + 1
    ok2, _ = risk.guard(now=later)
    assert ok2


def test_no_manual_override_for_circuit_breaker(rig):
    ledger, risk = rig
    assert not hasattr(risk, "clear_halt")
    assert not hasattr(risk, "resume")
    # `resume` CLI only touches the kill switch; HALT_UNTIL stays.
    ledger.set_state(HALT_UNTIL, {"until": time.time() + 1000, "reason": "x"})
    ledger.set_state(KILL_SWITCH, False)
    ok, why = risk.guard()
    assert not ok and "circuit_breaker" in why


def test_marked_losses_count_toward_breaker(rig):
    ledger, risk = rig
    now = time.time()
    assert risk.check_circuit_breaker(marked_delta_usd=-150.0, now=now) is True


# ── kill switch + watchdog ─────────────────────────────────────────────

def test_kill_switch_blocks_and_releases(rig):
    ledger, risk = rig
    ledger.set_state(KILL_SWITCH, True)
    assert risk.guard()[0] is False
    assert risk.approve("kalshi", "kalshi:m", "ev", 10)[0] == 0
    ledger.set_state(KILL_SWITCH, False)
    assert risk.guard()[0] is True


def test_watchdog_trips_on_stale_feed_and_self_clears(rig):
    _, risk = rig
    tripped, reason = risk.watchdog(feed_age_s=90, clock_skew_s=0,
                                    venue_errors=0, tradeable_rate=1.0)
    assert tripped and "stale" in reason
    assert risk.guard()[0] is False
    tripped2, _ = risk.watchdog(feed_age_s=5, clock_skew_s=0,
                                venue_errors=0, tradeable_rate=1.0)
    assert not tripped2
    assert risk.guard()[0] is True


def test_watchdog_trips_on_skew_errors_and_mapper_collapse(rig):
    _, risk = rig
    assert risk.watchdog(1, 10.0, 0, 1.0)[0]      # clock skew
    assert risk.watchdog(1, 0.0, 100, 1.0)[0]     # venue error burst
    assert risk.watchdog(1, 0.0, 0, 0.1)[0]       # mapper confidence collapse


# ── maker-first planning (kalshi) ──────────────────────────────────────

def test_maker_first_posts_below_ask():
    k = KalshiAdapter()
    plan = k.plan_maker_order(limit_price=0.47, best_ask=0.47, edge=0.03, threshold=0.02)
    assert plan == (0.46, False)  # rests one tick under, fee-free


def test_cross_only_when_net_of_fee_clears():
    k = KalshiAdapter()
    # Ask at the 1c floor: no maker room, so crossing is the only option.
    # Taker fee at 0.01 = 0.07*0.01*0.99 ≈ 0.000693.
    plan = k.plan_maker_order(limit_price=0.01, best_ask=0.01, edge=0.0207, threshold=0.02)
    assert plan is not None and plan[1] is True     # net 0.0200 clears
    plan2 = k.plan_maker_order(limit_price=0.01, best_ask=0.01, edge=0.0205, threshold=0.02)
    assert plan2 is None                            # net 0.0198 — fee eats it


# ── contract quantization: round toward the cap, never past it ─────────

def _live_kalshi(monkeypatch_place=None):
    a = KalshiAdapter()
    a.sent = {}

    def _po(ticker, price, count, client_order_id, taker):
        a.sent.update(ticker=ticker, price=price, count=count, taker=taker)
        return {"ok": True, "order_id": "o1", "status": "resting",
                "price": price, "count": 0, "taker": taker}

    a.place_order = _po
    return a


def test_quantize_rounds_up_only_within_the_cap():
    from edge.execution.executor import quantize_contracts

    assert quantize_contracts(2.0, 0.70, 3.0) == 3   # floor lost 60c: round up
    assert quantize_contracts(2.0, 0.70, 2.0) == 2   # extra would breach: floor
    assert quantize_contracts(2.0, 0.50, 3.0) == 4   # exact: no rounding
    assert quantize_contracts(2.0, 0.03, 3.0) == 67  # 66 leaves 2c behind
    for size, px, cap in ((2.0, 0.7, 3.0), (1.0, 0.99, 3.0), (3.0, 0.13, 3.0)):
        assert quantize_contracts(size, px, cap) * px <= cap + 1e-9


def test_quantize_rounds_to_nearest_never_inflating_a_tight_grant():
    """Rounding UP whenever the per-fill max allowed it turned small grants
    into bigger tickets (audit 2026-08-05): a $1 probation half-ticket at
    70c deployed $1.40 (+40%) and a $1 grant at 99c doubled to $1.98. The
    extra contract is taken only when it lands CLOSER to the approved size
    than flooring does."""
    from edge.execution.executor import quantize_contracts

    assert quantize_contracts(1.0, 0.70, 3.0) == 1   # $0.70, not $1.40
    assert quantize_contracts(1.0, 0.99, 3.0) == 1   # $0.99, not $1.98
    assert quantize_contracts(1.4, 0.60, 3.0) == 2   # $1.20, not $1.80
    # Sent notional never strays further from the grant than flooring would.
    for size in (1.0, 1.4, 2.0, 2.5, 3.0):
        for px in (0.03, 0.13, 0.31, 0.50, 0.70, 0.99):
            n = quantize_contracts(size, px, 1000.0)
            assert abs(n * px - size) <= (size - int(size / px) * px) + 1e-9


def test_kalshi_live_count_rounds_up_toward_the_per_fill_cap(tmp_path):
    """A $2 grant at a 70c maker price floored to 2 contracts — $1.40
    deployed of an approved $2. The extra contract fits under the $3
    per-fill ceiling approve() enforces, so it is taken."""
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    a = _live_kalshi()
    r = execute(adapter=a, ledger=led, mode="LIVE_BETA", mkey="kalshi:T",
                league="nba", ask_price=0.71, ask_size=100, size_usd=2.0,
                edge=0.05, threshold=0.02, decision={}, ts=1.0,
                max_fill_usd=3.0)
    assert r["placed"]
    assert (a.sent["price"], a.sent["taker"]) == (0.70, False)
    assert a.sent["count"] == 3
    assert a.sent["count"] * a.sent["price"] <= 3.0 + 1e-9


def test_without_a_cap_the_count_still_never_exceeds_the_grant(tmp_path):
    """Callers that pass no ceiling keep the old floor behaviour — rounding
    up must never be a silent cap breach."""
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    a = _live_kalshi()
    execute(adapter=a, ledger=led, mode="LIVE_BETA", mkey="kalshi:T",
            league="nba", ask_price=0.71, ask_size=100, size_usd=2.0,
            edge=0.05, threshold=0.02, decision={}, ts=1.0)
    assert a.sent["count"] == 2                      # $1.40 <= the $2 grant


def test_per_fill_cap_resolves_the_orders_mode(rig):
    _, risk = rig
    assert risk.per_fill_cap() == risk.caps.per_fill_max == 25
    assert risk.per_fill_cap("LIVE") == 10000


def _live_fill(ledger, venue, mkey, usd, price=0.50, ts=None):
    ledger.record_fill(
        fill_uid=f"{mkey}-{usd}-{random.random()}", venue=venue,
        market_key=mkey, side="BUY", qty=usd / price, price=price,
        ts=ts or time.time(), mode="LIVE_BETA")


def test_the_ceiling_is_the_binding_room_not_the_per_fill_max(rig):
    """Audit 2026-08-05: rounding toward per_fill_max let the extra contract
    breach whichever TIGHTER room actually clamped the grant. approve()
    must surface the binding ceiling for quantization to round toward."""
    ledger, risk = rig
    now = time.time()
    # Exhaust the kalshi half of the live-beta day budget to $2 of room.
    _live_fill(ledger, "kalshi", "kalshi:warm", 123.0, ts=now)
    approved, _why = risk.approve("kalshi", "kalshi:mT", "ev-T", 25,
                                  now=now, mode="LIVE_BETA")
    assert approved == pytest.approx(2.0)          # day room binds, not $25
    assert risk.last_fill_ceiling == pytest.approx(2.0)


def test_a_day_room_bound_grant_floors_instead_of_breaching(rig):
    """The audit repro: $2.00 of venue-day room, approved $2.00, price 70c.
    3 contracts = $2.10 breaches the room on fill — the executor must send
    2 ($1.40), because the ceiling it rounds toward is the binding room."""
    ledger, risk = rig
    now = time.time()
    _live_fill(ledger, "kalshi", "kalshi:warm", 123.0, ts=now)
    approved, _ = risk.approve("kalshi", "kalshi:mT", "ev-T", 25,
                               now=now, mode="LIVE_BETA")
    a = _live_kalshi()
    r = execute(adapter=a, ledger=ledger, mode="LIVE_BETA", mkey="kalshi:mT",
                league="nba", ask_price=0.71, ask_size=100,
                size_usd=approved, edge=0.05, threshold=0.02, decision={},
                ts=now, max_fill_usd=risk.last_fill_ceiling)
    assert r["placed"]
    assert (a.sent["price"], a.sent["count"]) == (0.70, 2)   # $1.40, not $2.10


def test_property_sent_notional_never_exceeds_any_room(tmp_path):
    """The old property test asserted on approve() output alone, so the
    execute()-level contract round-up escaped the very invariant it claimed
    (audit 2026-08-05). This one drives the EXECUTOR with the ceiling the
    runner passes and asserts SENT notional (count * px) against every
    room at approve time."""
    cfg = {**RISK_CFG, "profiles": {"live_beta": {
        **RISK_CFG["profiles"]["live_beta"],
        "per_event_exposure_usd": 30,
    }}}
    ledger = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    risk = RiskManager(ledger, cfg)
    caps = caps_for_mode(cfg, "LIVE_BETA", None)
    rng = random.Random(7)
    now = time.time()
    sent_by_market: dict[str, float] = {}
    sent_by_event: dict[str, float] = {}
    total = 0.0
    placed = 0
    for _ in range(1500):
        event = f"ev{rng.randrange(120)}"
        mkey = f"kalshi:m{event}-{rng.randrange(3)}"
        requested = rng.choice([1.5, 2, 5, 10, 25, 100])
        approved, _why = risk.approve("kalshi", mkey, event, requested,
                                      now=now, mode="LIVE_BETA")
        if approved <= 0:
            continue
        px = rng.choice([0.03, 0.13, 0.31, 0.50, 0.70, 0.97])
        a = _live_kalshi()
        r = execute(adapter=a, ledger=ledger, mode="LIVE_BETA", mkey=mkey,
                    league="nba", ask_price=round(px + 0.01, 2),
                    ask_size=10_000, size_usd=approved, edge=0.20,
                    threshold=0.02, decision={}, ts=now,
                    max_fill_usd=risk.last_fill_ceiling)
        if not r["placed"]:
            continue
        placed += 1
        notional = a.sent["count"] * a.sent["price"]
        assert notional <= caps.per_fill_max + 0.01
        assert notional <= risk.last_fill_ceiling + 0.01
        # The venue fills what was sent; the rooms must already have had
        # room for it (0.01 tolerance: the ceiling is rounded to cents).
        ledger.record_fill(
            fill_uid=f"{mkey}-{placed}", venue="kalshi", market_key=mkey,
            side="BUY", qty=a.sent["count"], price=a.sent["price"], ts=now,
            mode="LIVE_BETA", decision={"event_key": event})
        sent_by_market[mkey] = sent_by_market.get(mkey, 0) + notional
        sent_by_event[event] = sent_by_event.get(event, 0) + notional
        total += notional
        assert sent_by_market[mkey] <= caps.per_market + 0.01
        assert sent_by_event[event] <= caps.per_event + 0.01
        assert total <= caps.per_day * caps.venue_bankroll_split + 0.01
    assert placed >= 5                       # the test actually sent orders
    assert total > 100                       # ...and pressed on the day room


# ── kalshi force-taker wiring through the executor ─────────────────────

def test_a_forced_kalshi_market_crosses_through_the_executor(tmp_path):
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    a = _live_kalshi()
    a.mark_force_taker("T")
    r = execute(adapter=a, ledger=led, mode="LIVE_BETA", mkey="kalshi:T",
                league="nba", ask_price=0.50, ask_size=100, size_usd=2.0,
                edge=0.05, threshold=0.02, decision={}, ts=1.0, taker=True,
                max_fill_usd=3.0)
    # plan_entry crossed, so 0.05 is already net of the 0.0175 fee: cross.
    assert r["placed"]
    assert (a.sent["price"], a.sent["taker"]) == (0.50, True)


def test_a_forced_cross_is_not_charged_the_taker_fee_twice(tmp_path):
    """taker=True means strategy_filter ALREADY subtracted the taker fee
    from the edge (plan_entry returned a crossing price). Re-deducting it in
    plan_maker_order demanded threshold + 2x fee of every forced cross —
    edges the strategy priced as winners were silently dropped (audit
    2026-08-05)."""
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    a = _live_kalshi()
    a.mark_force_taker("T")
    # Fee-net edge 0.03 clears the 0.02 bar; a second 0.0175 deduction
    # would wrongly kill it.
    r = execute(adapter=a, ledger=led, mode="LIVE_BETA", mkey="kalshi:T",
                league="nba", ask_price=0.50, ask_size=100, size_usd=2.0,
                edge=0.03, threshold=0.02, decision={}, ts=1.0, taker=True,
                max_fill_usd=3.0)
    assert r["placed"]
    assert (a.sent["price"], a.sent["taker"]) == (0.50, True)


def test_a_forced_market_without_fee_room_places_nothing(tmp_path):
    """The entry was planned MAKER (gross edge), so the cross fallback must
    still deduct the taker fee before judging the bar."""
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    a = _live_kalshi()
    a.mark_force_taker("T")
    r = execute(adapter=a, ledger=led, mode="LIVE_BETA", mkey="kalshi:T",
                league="nba", ask_price=0.50, ask_size=100, size_usd=2.0,
                edge=0.03, threshold=0.02, decision={}, ts=1.0, taker=False,
                max_fill_usd=3.0)
    # fee at 0.50 = 0.0175; 0.03 - 0.0175 = 0.0125 < 0.02: no order.
    assert not r["placed"] and r["status"] == "no_maker_no_fee_room"
    assert a.sent == {}


# ── executor: paper path ───────────────────────────────────────────────

class _StubAdapter:
    name = "kalshi"

    @staticmethod
    def taker_fee(price):
        return 0.07 * price * (1 - price)

    @staticmethod
    def maker_fee(price):
        return 0.0


def test_paper_execute_records_ledger_fill_with_decision(tmp_path):
    ledger = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    book = MarketBook(venue="kalshi", market_id="e", outcome_id="t",
                      bids=[BookLevel(0.45, 100)], asks=[BookLevel(0.47, 100)], ts=1.0)
    r = execute(adapter=_StubAdapter(), ledger=ledger, mode="PAPER",
                mkey=market_key("kalshi", "t"), league="epl",
                ask_price=0.47, ask_size=100, size_usd=10.0,
                edge=0.03, threshold=0.02,
                decision={"fair_value": 0.5, "band": "0.45-0.50",
                          "book_asks": [(0.47, 100)]}, ts=1000.0)
    assert r["placed"] and r["filled_usd"] == 10.0 and r["status"] == "paper"
    pos = ledger.position("kalshi:t")
    assert pos["shares"] == pytest.approx(10.0 / 0.47, abs=0.01)  # qty rounds to 2dp
    assert pos["fees_paid"] > 0  # modeled taker fee logged
    s = ledger.summary()
    assert s["staked"] == pytest.approx(10.0, abs=0.01)


def test_paper_execute_clips_to_displayed_depth(tmp_path):
    ledger = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    r = execute(adapter=_StubAdapter(), ledger=ledger, mode="PAPER",
                mkey="kalshi:t2", league="epl", ask_price=0.50, ask_size=4,
                size_usd=10.0, edge=0.03, threshold=0.02, decision={}, ts=1.0)
    assert r["filled_usd"] == pytest.approx(2.0)  # 4 contracts * $0.50 shown


# ── nightly report: divergence alerts ──────────────────────────────────

def test_nightly_report_flags_divergence(tmp_path, monkeypatch):
    from edge.execution.engine import Policy
    from edge.shadow import report as rep

    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    (tmp_path / "calib_price.csv").write_text(
        "bin,n,stake,edge_cents\n\"[0.45, 0.5)\",100,1000,2.94\n")
    (tmp_path / "calib_league.csv").write_text(
        "prefix,n,stake,edge_cents\nepl,100,1000,1.98\n")
    ledger = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    # A big measured LOSS in a band the reference says is +2.94c.
    ledger.record_fill(fill_uid="f1", venue="kalshi", market_key="kalshi:m1",
                       side="BUY", qty=2000, price=0.47, ts=100, mode="PAPER",
                       league="epl", decision={"band": "0.45-0.50"})
    ledger.record_resolution("kalshi:m1", 0.0, ts=200)
    policy = Policy.load()
    out = rep.nightly_report(ledger, policy)
    assert any("band 0.45-0.50" in a for a in out["alerts"])
    assert any("league epl" in a for a in out["alerts"])
    assert (tmp_path / "reports").exists()


# ── mode config ────────────────────────────────────────────────────────

def test_paper_and_beta_share_the_beta_profile_live_uses_measured():
    beta = caps_for_mode(RISK_CFG, "LIVE_BETA")
    paper = caps_for_mode(RISK_CFG, "PAPER")
    live = caps_for_mode(RISK_CFG, "LIVE")
    assert beta.per_fill_max == paper.per_fill_max == 25
    assert live.per_fill_max == 10000 and live.per_market == 25000


# ── paper sampling decoupled from live dollars ─────────────────────────

def test_paper_profile_decouples_sampling_caps():
    from edge.execution.engine import Policy

    risk_cfg = Policy.load().risk
    paper = caps_for_mode(risk_cfg, "PAPER")
    beta = caps_for_mode(risk_cfg, "LIVE_BETA")
    assert paper.per_fill_max == 10
    # Live tier, $10 ticket (owner directive 2026-08-08, up from the $1
    # micro tier of 2026-08-05): every software-generated engine trade
    # is $10, so default and ceiling meet at $10 and the ladder is flat
    # until the owner re-scales it.
    assert beta.per_fill_default == 10
    assert beta.per_fill_max == 10
    assert paper.one_per_market and beta.one_per_market
    assert not paper.one_per_event and not beta.one_per_event


def test_day_budget_follows_the_account_balance():
    """A fixed daily cap is a trade-count cap in disguise: at $1 a fill, a
    $25 budget is '25 trades a day' regardless of how many edges exist."""
    from edge.execution.engine import Policy

    risk_cfg = Policy.load().risk
    unfunded = caps_for_mode(risk_cfg, "LIVE_BETA")
    funded = caps_for_mode(risk_cfg, "LIVE_BETA", bankroll=400.0)
    grown = caps_for_mode(risk_cfg, "LIVE_BETA", bankroll=5000.0)
    assert unfunded.per_day == 500                      # floor before we know
    assert funded.per_day == 500                        # floor still governs
    assert grown.per_day == 5000                        # scales with funding
    # A balance reading can never SHRINK the budget below the configured floor.
    assert caps_for_mode(risk_cfg, "LIVE_BETA", bankroll=1.0).per_day == 500


def test_the_breaker_scales_with_the_trade_count():
    """Daily P&L noise on N independent $1 bets is ~sqrt(N) dollars. A fixed
    $15 halt is meaningful at 25 trades and pure coin-flip at 1,000 — it
    would lock the engine out for 72h on an ordinary day."""
    from edge.execution.engine import Policy

    risk_cfg = Policy.load().risk
    small = caps_for_mode(risk_cfg, "LIVE_BETA", bankroll=100.0)
    big = caps_for_mode(risk_cfg, "LIVE_BETA", bankroll=4000.0)
    # Small day: the $150 floor governs (sigma term 4*sqrt(50)*10 ~ $283
    # of a $500-floor day... at bankroll $100 the day floor is $500, so
    # n=50 fills: 4*sqrt(50)*10 = 282.8 > 150 -> sigma governs).
    assert small.daily_loss_halt == pytest.approx(4 * (500 / 10) ** 0.5 * 10)
    # Big day: n = 4000/10 = 400 fills, sigma = 4*sqrt(400)*10 = $800 >
    # 15% of $4,000 = $600 -> the sigma term governs at the $10 ticket.
    assert big.daily_loss_halt == pytest.approx(800)
    # Comfortably outside 3 sigma of the day's noise in both cases.
    for caps in (small, big):
        n = caps.per_day / caps.per_fill_default
        assert caps.daily_loss_halt > 3 * (n ** 0.5) * caps.per_fill_default


def test_bankroll_updates_resize_the_caps(tmp_path):
    from edge.execution.engine import Policy

    ledger = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    risk = RiskManager(ledger, Policy.load().risk)
    risk.set_mode("LIVE_BETA")
    assert risk.caps.per_day == 500
    risk.set_bankroll(800.0)
    assert risk.caps.per_day == 800
    risk.set_bankroll(None)          # venue unreachable: keep what we knew
    assert risk.caps.per_day == 800


def test_the_day_budget_does_not_shrink_as_it_is_spent(tmp_path):
    """The denominator is start-of-day buying power, not cash remaining.

    `set_bankroll` is fed the venue's CASH, which falls a dollar for every
    dollar deployed. Sizing the day budget off it directly makes the budget
    chase its own spend: at 100% of bankroll, room runs out once spend
    reaches remaining cash — i.e. after deploying HALF the starting bankroll
    — and the engine reports "BUDGET SPENT: $390.22 of $232.78", which reads
    as a breach and is really a cap that shrank underneath a cumulative
    number. Observed live 2026-08-02; 390.22 + 232.78 = $623.00 exactly.
    """
    from edge.execution.engine import Policy

    ledger = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    risk = RiskManager(ledger, Policy.load().risk)
    risk.set_mode("LIVE_BETA")

    risk.set_bankroll(623.0)                 # start of day, nothing deployed
    assert risk.caps.per_day == pytest.approx(623.0)

    # Deploy $390.22 of it. Cash is now $232.78; the budget must not follow.
    ledger.record_fill(fill_uid="f1", venue="polymarket-us",
                       market_key="polymarket-us:m1", side="BUY",
                       qty=390.22, price=1.0, ts=time.time(), mode="LIVE_BETA")
    risk.set_bankroll(232.78)
    assert risk.caps.per_day == pytest.approx(623.0)
    assert risk.day_deployed() == pytest.approx(390.22)


def test_paper_claims_per_venue_live_claims_global(rig):
    ledger, risk = rig
    now = time.time()
    # PAPER: both venues sample the same market independently.
    a1, _ = risk.approve("kalshi", "kalshi:m1", "ev-x", 10, now=now, mode="PAPER")
    a2, _ = risk.approve("polymarket-us", "polymarket-us:m1", "ev-x", 10,
                         now=now, mode="PAPER")
    assert a1 > 0 and a2 > 0
    # LIVE: the market is claimed globally, once, across venues.
    a3, _ = risk.approve("polymarket-us", "pm:m2", "ev-x", 10,
                         now=now, mode="LIVE_BETA")
    a4, why = risk.approve("kalshi", "pm:m2", "ev-x", 10,
                           now=now, mode="LIVE_BETA")
    assert a3 > 0 and a4 == 0 and "one-per-market" in why


# ── paper numbers must never halt live trading ─────────────────────────

def test_breaker_ignores_paper_losses(rig):
    ledger, risk = rig
    now = time.time()
    # A catastrophic PAPER day: -$500 realized on paper fills.
    ledger.record_fill(fill_uid="pb", venue="kalshi", market_key="kalshi:pb",
                       side="BUY", qty=1000, price=0.50, ts=now - 100, mode="PAPER")
    ledger.record_resolution("kalshi:pb", 0.0, ts=now - 50)
    assert risk.check_circuit_breaker(now=now) is False  # live book is clean
    assert risk.guard(now=now)[0] is True


def test_breaker_still_trips_on_live_losses(rig):
    ledger, risk = rig
    now = time.time()
    ledger.record_fill(fill_uid="lb", venue="polymarket-us", market_key="polymarket-us:lb",
                       side="BUY", qty=240, price=0.50, ts=now - 100, mode="LIVE_BETA")
    ledger.record_resolution("polymarket-us:lb", 0.0, ts=now - 50)  # -$120 REAL
    assert risk.check_circuit_breaker(now=now) is True


def test_live_fill_count_distinguishes_bogus_halts(tmp_path):
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    now = time.time()
    led.record_fill(fill_uid="p1", venue="kalshi", market_key="kalshi:p1",
                    side="BUY", qty=10, price=0.5, ts=now, mode="PAPER")
    assert led.live_fill_count_since(now - 3600) == 0  # paper-only => bogus halt
    led.record_fill(fill_uid="l1", venue="polymarket-us", market_key="polymarket-us:l1",
                    side="BUY", qty=2, price=0.5, ts=now, mode="LIVE_BETA")
    assert led.live_fill_count_since(now - 3600) == 1


def test_live_staked_bounds_bogus_halt_detection(tmp_path):
    """A halt recording a loss larger than all live money ever staked is
    provably paper contamination — even when live fills exist."""
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    now = time.time()
    led.record_fill(fill_uid="live1", venue="polymarket-us",
                    market_key="polymarket-us:m", side="BUY", qty=14, price=0.07,
                    ts=now, mode="LIVE_BETA")           # ~$1 at risk
    led.record_fill(fill_uid="paper1", venue="kalshi", market_key="kalshi:p",
                    side="BUY", qty=100, price=0.50, ts=now, mode="PAPER")
    assert led.live_fill_count_since(now - 3600) == 1   # a live fill DOES exist
    assert led.live_staked_since(now - 3600) == pytest.approx(0.98, abs=0.01)
    # -$15 could not have come from $0.98 of live exposure.
    assert 15.0 > led.live_staked_since(now - 3600) + 0.01


# ── pricing-integrity quarantine (immune system) ───────────────────────

def test_systematic_divergence_quarantines_a_slice(tmp_path):
    """Whatever the cause, a slice whose fair values mostly disagree wildly
    with the venue's own mid stops trading."""
    from datetime import datetime, timezone

    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for _ in range(30):                      # our 37c vs venue 8c: 29c apart
        led.record_divergence(day, "polymarket-us", "mex", "spread", 0.29)
    for _ in range(30):                      # healthy slice: cents apart
        led.record_divergence(day, "polymarket-us", "epl", "moneyline", 0.012)

    rows = {(r["league"], r["category"]): r for r in led.divergence_report(days=2)}
    bad = rows[("mex", "spread")]
    good = rows[("epl", "moneyline")]
    assert bad["quarantined"] is True and bad["wild_share"] == 1.0
    assert good["quarantined"] is False and good["mean_abs_div"] < 0.02
    q = led.quarantined_slices(days=2)
    assert ("polymarket-us", "mex", "spread") in q
    assert ("polymarket-us", "epl", "moneyline") not in q


def test_quarantine_needs_a_real_sample(tmp_path):
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for _ in range(5):  # below QUARANTINE_MIN_N — noisy, not judged
        led.record_divergence(today, "kalshi", "nba", "total", 0.40)
    assert led.quarantined_slices(days=2) == set()


# ── per-event concentration (found live 2026-07-31) ────────────────────

def _live_rig(tmp_path):
    from edge.execution.engine import Policy

    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    return led, RiskManager(led, Policy.load().risk, bankroll=1000.0)


def _event_fill(led, event_key, market, usd, mode="LIVE_BETA"):
    led.record_fill(fill_uid=f"{market}-{random.random()}", venue="polymarket-us",
                    market_key=market, side="BUY", qty=usd / 0.5, price=0.5,
                    ts=time.time(), mode=mode,
                    decision={"event_key": event_key})


def test_one_game_cannot_absorb_a_ticket_per_listed_outcome(tmp_path):
    """Sides of one game are not independent bets — exactly one pays. The
    per-MARKET cap bounded each bet at $1 and silently permitted a ticket on
    every outcome the venue lists: three on a soccer match, twenty on a game
    carrying a full spread and totals ladder. Seen live: home, away AND draw
    bought on the same match, three times over."""
    led, risk = _live_rig(tmp_path)
    for i in range(10):
        approved, _ = risk.approve("polymarket-us", f"pm:m{i}", "game-1", 20.0,
                                   mode="LIVE_BETA")
        # $10 ticket (owner 2026-08-08): even a $20 request clamps to
        # $10, and the $100 event ceiling holds ten tickets.
        assert approved == 10.0
        _event_fill(led, "game-1", f"pm:m{i}", approved)

    blocked, why = risk.approve("polymarket-us", "pm:m11", "game-1", 20.0,
                                mode="LIVE_BETA")
    assert blocked == 0 and "caps" in why
    # ...and a DIFFERENT game is unaffected.
    assert risk.approve("polymarket-us", "pm:other", "game-2", 10.0,
                        mode="LIVE_BETA")[0] == 10.0


def test_event_exposure_counts_only_that_game_and_that_mode(tmp_path):
    led, risk = _live_rig(tmp_path)
    _event_fill(led, "game-1", "pm:a", 1.0)
    _event_fill(led, "game-1", "pm:b", 1.0)
    _event_fill(led, "game-2", "pm:c", 1.0)
    _event_fill(led, "game-1", "pm:paper", 50.0, mode="PAPER")

    live = led.event_exposure("game-1", "LIVE_BETA")
    assert live == {"cost": pytest.approx(2.0), "markets": 2}
    assert led.event_exposure("game-2", "LIVE_BETA")["cost"] == pytest.approx(1.0)
    assert led.event_exposure("nope", "LIVE_BETA") == {"cost": 0.0, "markets": 0}


def test_the_cap_is_opt_in_so_LIVE_keeps_its_measured_behaviour(tmp_path):
    from edge.execution.engine import Policy

    cfg = Policy.load().risk
    assert caps_for_mode(cfg, "LIVE_BETA").per_event == 100
    assert caps_for_mode(cfg, "LIVE").per_event == 0        # 0 = unbounded


def test_a_fill_records_the_game_it_belongs_to(tmp_path):
    """Without this the cap has nothing to count."""
    from edge.venues.base import BookLevel, MarketBook

    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    book = MarketBook(venue="kalshi", market_id="e", outcome_id="t",
                      bids=[BookLevel(0.45, 100)], asks=[BookLevel(0.47, 100)], ts=1.0)
    execute(adapter=_StubAdapter(), ledger=led, mode="PAPER",
            mkey=market_key("kalshi", "t"), league="epl", ask_price=0.47,
            ask_size=100, size_usd=10.0, edge=0.03, threshold=0.02,
            decision={}, ts=1000.0, event_key="game-9")
    assert led.event_exposure("game-9", "PAPER")["markets"] == 1


# ── the size ladder ─────────────────────────────────────────────────────

def _beta_risk():
    import tempfile

    from edge.execution.engine import Policy
    from edge.ledger.service import Ledger

    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    return RiskManager(led, {**Policy.load().risk, "mode": "LIVE_BETA"})


def test_every_engine_ticket_is_ten_dollars():
    """Owner directive 2026-08-08: ALL software-generated engine trades
    are $10 per trade (up from the $1 micro tier of 2026-08-05). The
    ladder mechanism stays (rungs still resolve by edge multiple) but
    every rung sits at the $10 ticket, so no edge — however strong —
    can stake more than $10."""
    risk = _beta_risk()
    bar = 0.02
    assert risk.size_for_edge(bar, bar) == 10.00          # exactly the bar
    assert risk.size_for_edge(bar * 1.9, bar) == 10.00    # not yet 2x
    assert risk.size_for_edge(bar * 2.0, bar) == 10.00    # same $10 ticket
    assert risk.size_for_edge(bar * 10, bar) == 10.00     # still $10, capped


def test_the_trigger_is_a_MULTIPLE_of_the_bar_not_a_cent_figure():
    """The bar already varies by band, by category and by measured drift.
    A fixed cent trigger would mean something different in every market —
    generous where the bar is low, unreachable where it is high. With the
    flat $1 ladder (owner 2026-08-05) both rungs resolve to the same
    stake; the multiple arithmetic itself must keep working for the day
    the rungs are re-scaled."""
    risk = _beta_risk()
    # Same 4c edge, two different bars: both stake the flat $10 ticket.
    assert risk.size_for_edge(0.04, 0.02) == 10.00
    assert risk.size_for_edge(0.04, 0.035) == 10.00


def test_an_unknown_bar_takes_the_STANDARD_ticket_not_the_top_rung():
    """A missing threshold must never be read as an infinite edge. Getting
    this backwards would stake the maximum on precisely the markets we
    understand least."""
    risk = _beta_risk()
    assert risk.size_for_edge(0.05, None) == 10.00
    assert risk.size_for_edge(0.05, 0.0) == 10.00
    assert risk.size_for_edge(None, 0.02) == 10.00


def test_the_ladder_can_never_exceed_the_per_fill_cap():
    risk = _beta_risk()
    for edge in (0.0, 0.01, 0.1, 1.0, 99.0):
        s = risk.size_for_edge(edge, 0.02)
        assert risk.caps.per_fill_default <= s <= risk.caps.per_fill_max


# ── league probation ───────────────────────────────────────────────────

def test_unmeasured_league_is_not_measured_and_allowlisted_is():
    from edge.execution.engine import Policy

    policy = Policy.load()
    # epl is in the measured allowlist; a made-up league is not.
    assert policy.league_measured("epl") is True
    assert policy.league_measured("xyz_reserve_league") is False
    assert policy.league_measured(None) is False


def test_league_live_record_counts_only_live_resolved(tmp_path):
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    now = time.time()
    led.record_fill(fill_uid="p1", venue="polymarket-us", market_key="pm:a",
                    side="BUY", qty=4, price=0.5, ts=now, mode="LIVE_BETA",
                    league="xyz")
    led.record_fill(fill_uid="p2", venue="polymarket-us", market_key="pm:b",
                    side="BUY", qty=4, price=0.5, ts=now, mode="PAPER",
                    league="xyz")
    led.record_resolution("pm:a", 1.0)
    led.record_resolution("pm:b", 0.0)
    rec = led.league_live_record("xyz")
    assert rec["n"] == 1          # the PAPER market does not count
    assert rec["net"] > 0


def test_the_ladder_survives_approve_end_to_end():
    """Audit 2026-08-04: approve() clamped every request to
    per_fill_default, so the top rung existed in config and could never
    fill. The ladder's output must survive the full approval path —
    which at the flat $10 ladder (owner 2026-08-08) means exactly $10.00
    comes out the other side, not $0 and not more."""
    risk = _beta_risk()
    bar = 0.02
    requested = risk.size_for_edge(bar * 2.0, bar)
    assert requested == 10.00
    approved, why = risk.approve("polymarket-us", "polymarket-us:tok-x",
                                 "ev-x", requested)
    assert why == "ok"
    assert approved == 10.00, "$10 must clear approve() intact"
