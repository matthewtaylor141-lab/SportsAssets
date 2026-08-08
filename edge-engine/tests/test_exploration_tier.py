"""Exploration tier: trade small edges at scale, on a leash.

Small-edge-at-scale is the reference account's whole model, but it only
works when the edge is REAL. Our fair value is a de-vigged median of a
handful of books, so a fraction of a cent is mostly estimation noise — and
noise has zero mean before adverse selection, negative after. These tests
pin the three things that make trading it survivable: a floor below which we
never go, a consensus requirement that scales with how small the edge is,
and a hard cap on how much of the day's money can ride on the experiment.
"""

import time

import pytest

from edge.execution.engine import Policy, strategy_filter
from edge.execution.risk import RiskManager
from edge.ledger.service import Ledger

# Most tests here exercise loop and pricing MECHANICS, not trading policy.
# `blocked_categories` globally quarantines moneyline (measured -2.34c drift,
# retention 0.239 on our own fills), which would otherwise make every
# moneyline fixture untradeable and turn these into vacuous passes. The
# quarantine itself is pinned by its own tests in test_loop_health.py.
POLICY = Policy.load()
POLICY.leagues = {**POLICY.leagues, "blocked_categories": []}
DEEP = 6      # a well-covered event
THIN = 1      # one book


# The leash tests pin mode explicitly rather than inheriting `config/risk.yaml`.
# Exploration spend is accounted per mode (a PAPER manager cannot see LIVE_BETA
# fills), so a config-level halt would silently zero the spend these tests are
# measuring and turn every assertion into a vacuous pass.
def _rig(tmp_path, mode="LIVE_BETA", bankroll=1000.0):
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    return led, RiskManager(led, {**POLICY.risk, "mode": mode},
                            bankroll=bankroll)


def _fill(led, usd, tier, ts=None, mode="LIVE_BETA"):
    led.record_fill(fill_uid=f"{tier}-{usd}-{time.time()}-{id(usd)}",
                    venue="polymarket-us", market_key=f"m{time.time()}",
                    side="BUY", qty=usd / 0.5, price=0.5,
                    ts=ts or time.time(), mode=mode, decision={"tier": tier})


# ── selection ───────────────────────────────────────────────────────────

def test_a_sub_threshold_edge_now_trades_as_exploration():
    """1.5c against a 2.0c bar used to be nothing. It is now a measured bet."""
    v = strategy_filter(POLICY, "epl", 0.47, 0.485, consensus_books=DEEP)
    assert v.ok and v.tier == "exploration"
    core = strategy_filter(POLICY, "epl", 0.47, 0.50, consensus_books=DEEP)
    assert core.ok and core.tier == "core"


def test_the_noise_floor_still_refuses_fractions_of_a_cent():
    """Below the floor the 'edge' is de-vig error, at any consensus depth."""
    v = strategy_filter(POLICY, "epl", 0.47, 0.474, consensus_books=99)
    assert not v.ok and v.tier == "core"


def test_a_small_edge_needs_agreement_not_a_lone_quote():
    v = strategy_filter(POLICY, "epl", 0.47, 0.485, consensus_books=THIN)
    assert not v.ok and "thin_consensus" in v.reason
    assert strategy_filter(POLICY, "epl", 0.47, 0.485, consensus_books=DEEP).ok


def test_unknown_consensus_depth_is_not_permission():
    """Fail closed: if we cannot say how many books back this number, we are
    not trading a fraction of a cent on it."""
    v = strategy_filter(POLICY, "epl", 0.47, 0.485, consensus_books=None)
    assert not v.ok


def test_exploration_never_overrides_the_measured_blocks():
    """A cheaper bar is not a licence. Dead zones, blocked leagues, blocked
    categories and the implausibility guard all still bind."""
    assert not strategy_filter(POLICY, "epl", 0.43, 0.445,
                               consensus_books=DEEP).ok          # dead zone
    assert not strategy_filter(POLICY, "ucl", 0.50, 0.515,
                               consensus_books=DEEP).ok          # blocked league
    assert not strategy_filter(POLICY, "mlb", 0.47, 0.485, category="moneyline",
                               consensus_books=DEEP).ok          # blocked category
    assert not strategy_filter(POLICY, "epl", 0.47, 0.57,
                               consensus_books=DEEP).ok          # implausible


# ── the leash ───────────────────────────────────────────────────────────

def test_exploration_is_capped_at_a_share_of_the_day(tmp_path):
    led, risk = _rig(tmp_path)
    usd_left, fills_left = risk.exploration_room()
    assert usd_left == pytest.approx(risk.caps.per_day * 0.25)
    assert fills_left == 250

    # Spend the exploration budget; core budget is untouched by it.
    for _ in range(60):
        _fill(led, 5.0, "exploration")
    usd_left_after, _ = risk.exploration_room()
    assert usd_left_after < usd_left
    assert risk.approve("polymarket-us", "m-new", "ev", 1.0,
                        tier="core")[0] > 0


def test_a_spent_exploration_budget_stops_exploring_not_trading(tmp_path):
    # Day floor is 500 at the $10 ticket (2026-08-08), so the 25%
    # exploration budget is $125 — spend past it.
    led, risk = _rig(tmp_path, bankroll=100.0)
    for _ in range(130):
        _fill(led, 1.0, "exploration")
    approved, why = risk.approve("polymarket-us", "m1", "ev1", 1.0,
                                 tier="exploration")
    assert approved == 0 and "exploration_budget" in why
    # ...while a real, above-threshold edge is still funded.
    assert risk.approve("polymarket-us", "m2", "ev2", 1.0, tier="core")[0] > 0


def test_core_fills_do_not_consume_the_exploration_budget(tmp_path):
    led, risk = _rig(tmp_path)
    for _ in range(50):
        _fill(led, 4.0, "core")
    usd_left, fills_left = risk.exploration_room()
    assert usd_left == pytest.approx(risk.caps.per_day * 0.25)
    assert fills_left == 250


def test_exploration_spend_is_tracked_from_the_ledger_not_memory(tmp_path):
    """A restart must not reset the day's learning budget."""
    path = str(tmp_path / "l.sqlite3")
    led = Ledger(db_path=path)
    for _ in range(5):
        _fill(led, 2.0, "exploration")
    reopened = Ledger(db_path=path)
    risk = RiskManager(reopened, {**POLICY.risk, "mode": "LIVE_BETA"},
                       bankroll=1000.0)
    assert risk.explored_today() == {"usd": pytest.approx(10.0), "fills": 5}


def test_yesterdays_exploration_does_not_count_against_today(tmp_path):
    led, risk = _rig(tmp_path)
    for _ in range(20):
        _fill(led, 5.0, "exploration", ts=time.time() - 90_000)
    assert risk.explored_today()["fills"] == 0


# ── the tier reaches the ledger, so the grader can judge it ─────────────

def test_every_fill_records_which_tier_bought_it(tmp_path, monkeypatch):
    from edge.shadow.runner import run_cycle
    from edge.venues.base import BookLevel, MarketBook
    from tests.test_run_cycle_e2e import StubFeed, StubVenue, _event

    # A 1c-wide book: the exploration tier pays the SAME per-fill
    # net-margin floor as core — the study budget measures small edges, it
    # does not fund entries that are negative at the moment of purchase.
    # 1.5c edge minus 0.5c over mid, over 48.5c, is 2.06%: just inside.
    class TightVenue(StubVenue):
        def get_book(self, market_id, token):
            return MarketBook(venue=self.name, market_id=market_id,
                              outcome_id=token,
                              bids=[BookLevel(self._ask.price - 0.01, 500)],
                              asks=[self._ask], ts=time.time())

    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    risk = RiskManager(led, {**POLICY.risk, "mode": "PAPER"})
    ev = _event()
    ev.books = DEEP
    # fair 0.50 vs ask 0.485 -> 1.5c: exploration, not core.
    funnel = run_cycle([TightVenue(ask_price=0.485)], StubFeed([ev]),
                       POLICY, risk, led, ["soccer_epl"])
    assert funnel["by_tier"]["exploration"] >= 1
    assert "core" not in funnel.get("by_tier", {})
    rec = led.decision_record(
        [r["fill_uid"] for r in _all_fills(led)][0])["decision"]
    assert rec["tier"] == "exploration" and rec["consensus_books"] == DEEP


def _all_fills(led):
    with led._conn() as conn:
        return [dict(r) for r in conn.execute("SELECT fill_uid FROM fills")]
