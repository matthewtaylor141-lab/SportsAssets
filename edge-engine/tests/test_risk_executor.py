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
    # Micro live tier. The ceiling is 2, not 1, so edge-proportional sizing
    # has somewhere to ramp — the DEFAULT stake is still $1 and only a bet
    # clearing 3x its bar reaches the top.
    assert beta.per_fill_default == 1
    assert beta.per_fill_max == 2
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
    assert unfunded.per_day == 25                       # floor before we know
    assert funded.per_day == 400                        # 400 tickets a day
    assert grown.per_day == 5000                        # scales with funding
    # A balance reading can never SHRINK the budget below the configured floor.
    assert caps_for_mode(risk_cfg, "LIVE_BETA", bankroll=1.0).per_day == 25


def test_the_breaker_scales_with_the_trade_count():
    """Daily P&L noise on N independent $1 bets is ~sqrt(N) dollars. A fixed
    $15 halt is meaningful at 25 trades and pure coin-flip at 1,000 — it
    would lock the engine out for 72h on an ordinary day."""
    from edge.execution.engine import Policy

    risk_cfg = Policy.load().risk
    small = caps_for_mode(risk_cfg, "LIVE_BETA", bankroll=100.0)
    big = caps_for_mode(risk_cfg, "LIVE_BETA", bankroll=4000.0)
    assert small.daily_loss_halt == 40                   # 4 sigma of 100 bets
    assert big.daily_loss_halt == 600                    # 15% of $4,000
    # Comfortably outside 3 sigma of the day's noise in both cases.
    for caps in (small, big):
        n = caps.per_day / caps.per_fill_default
        assert caps.daily_loss_halt > 3 * (n ** 0.5) * caps.per_fill_default


def test_bankroll_updates_resize_the_caps(tmp_path):
    from edge.execution.engine import Policy

    ledger = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    risk = RiskManager(ledger, Policy.load().risk)
    risk.set_mode("LIVE_BETA")
    assert risk.caps.per_day == 25
    risk.set_bankroll(800.0)
    assert risk.caps.per_day == 800
    risk.set_bankroll(None)          # venue unreachable: keep what we knew
    assert risk.caps.per_day == 800


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
    for i in range(5):
        approved, _ = risk.approve("polymarket-us", f"pm:m{i}", "game-1", 1.0,
                                   mode="LIVE_BETA")
        assert approved == 1.0
        _event_fill(led, "game-1", f"pm:m{i}", approved)

    blocked, why = risk.approve("polymarket-us", "pm:m6", "game-1", 1.0,
                                mode="LIVE_BETA")
    assert blocked == 0 and "caps" in why
    # ...and a DIFFERENT game is unaffected.
    assert risk.approve("polymarket-us", "pm:other", "game-2", 1.0,
                        mode="LIVE_BETA")[0] == 1.0


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
    assert caps_for_mode(cfg, "LIVE_BETA").per_event == 5
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


# ── stake in proportion to how far the edge clears its bar ──────────────

def test_size_scales_with_edge_over_threshold():
    """A flat ticket stakes the same on a 2c edge as a 6c one — it leaves
    money on the better bet and overpays for the marginal one."""
    from edge.execution.engine import Policy
    from edge.ledger.service import Ledger
    import tempfile

    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    risk = RiskManager(led, {**Policy.load().risk, "mode": "LIVE_BETA"})

    at_bar = risk.size_for_edge(0.02, 0.02)          # exactly the bar
    double = risk.size_for_edge(0.04, 0.02)          # 2x
    triple = risk.size_for_edge(0.06, 0.02)          # 3x
    absurd = risk.size_for_edge(0.50, 0.02)          # far beyond

    assert at_bar == 1.0
    assert 1.0 < double < triple
    assert triple == 2.0
    assert absurd == 2.0, "the ramp is capped, not unbounded"


def test_sizing_never_exceeds_the_cap_however_big_the_claim():
    """Kelly sizes on the edge you BELIEVE you have, so it amplifies
    estimation error as readily as edge. Ours is the thing under suspicion,
    so the ramp is linear and hard-capped."""
    from edge.execution.engine import Policy
    from edge.ledger.service import Ledger
    import tempfile

    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    risk = RiskManager(led, {**Policy.load().risk, "mode": "LIVE_BETA"})
    caps = risk.caps
    for edge in (0.0, 0.01, 0.1, 1.0, 99.0):
        assert caps.per_fill_default <= risk.size_for_edge(edge, 0.02) <= caps.per_fill_max


def test_a_missing_threshold_falls_back_to_the_default_stake():
    from edge.execution.engine import Policy
    from edge.ledger.service import Ledger
    import tempfile

    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    risk = RiskManager(led, {**Policy.load().risk, "mode": "LIVE_BETA"})
    assert risk.size_for_edge(0.05, None) == risk.caps.per_fill_default
    assert risk.size_for_edge(0.05, 0.0) == risk.caps.per_fill_default
