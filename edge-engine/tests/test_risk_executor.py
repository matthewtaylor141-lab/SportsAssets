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
        "one_position_per_event": True, "daily_loss_halt_usd": 100,
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
            # PAPER claims are per (venue, event): each venue samples a game
            # once, never twice. (LIVE claims globally — separate test.)
            assert (venue, event) not in events_filled, "one-per-event violated"
            events_filled.add((venue, event))
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
    ledger, risk = rig
    now = time.time()
    a1, _ = risk.approve("kalshi", "kalshi:mX", "ev-a", 25, now=now)
    assert a1 > 0
    _paper_fill(ledger, "kalshi", "kalshi:mX", 45, price=0.5, ts=now)  # near $50 cap
    a2, _ = risk.approve("kalshi", "kalshi:mX", "ev-b", 25, now=now)
    assert a2 == pytest.approx(5.0)  # clamped to remaining market room
    _paper_fill(ledger, "kalshi", "kalshi:mX", 5, price=0.5, ts=now)   # cap reached
    a3, why = risk.approve("kalshi", "kalshi:mX", "ev-c", 25, now=now)
    assert a3 == 0 and "caps" in why


def test_approve_claims_event_exactly_once_per_venue_in_paper(rig):
    _, risk = rig
    now = time.time()
    a1, _ = risk.approve("kalshi", "kalshi:m1", "event-1", 10, now=now)
    # Same venue, same event: never twice.
    a2, why = risk.approve("kalshi", "kalshi:m1b", "event-1", 10, now=now)
    assert a1 > 0 and a2 == 0 and "one-per-event" in why
    # Other venue samples the same game independently (PAPER experiment).
    a3, _ = risk.approve("polymarket-us", "polymarket-us:m2", "event-1", 10, now=now)
    assert a3 > 0


# ── circuit breaker ────────────────────────────────────────────────────

def test_circuit_breaker_trips_and_auto_resumes(rig):
    ledger, risk = rig
    now = time.time()
    # Realize a -$120 day via a resolved losing position.
    ledger.record_fill(fill_uid="cb1", venue="kalshi", market_key="kalshi:cb",
                       side="BUY", qty=240, price=0.50, ts=now - 100, mode="PAPER")
    ledger.record_resolution("kalshi:cb", 0.0, ts=now - 50)  # -$120
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


# ── executor: paper path ───────────────────────────────────────────────

class _StubAdapter:
    name = "kalshi"

    @staticmethod
    def taker_fee(price):
        return 0.07 * price * (1 - price)


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
    assert paper.per_day == 5000 and paper.per_fill_max == 10
    assert beta.per_day == 25 and beta.per_fill_max == 1  # micro live tier
    assert paper.one_per_event and beta.one_per_event


def test_paper_claims_per_venue_live_claims_global(rig):
    ledger, risk = rig
    now = time.time()
    # PAPER: both venues sample the same event independently.
    a1, _ = risk.approve("kalshi", "kalshi:m1", "ev-x", 10, now=now, mode="PAPER")
    a2, _ = risk.approve("polymarket-us", "polymarket-us:m2", "ev-x", 10,
                         now=now, mode="PAPER")
    assert a1 > 0 and a2 > 0
    # LIVE: the event is claimed globally, once, across venues.
    a3, _ = risk.approve("polymarket-us", "polymarket-us:m2", "ev-x", 10,
                         now=now, mode="LIVE_BETA")
    a4, why = risk.approve("kalshi", "kalshi:m1", "ev-x", 10,
                           now=now, mode="LIVE_BETA")
    assert a3 > 0 and a4 == 0 and "one-per-event" in why
