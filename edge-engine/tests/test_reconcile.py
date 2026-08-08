"""Rebuilding what we own after the ledger is wiped.

Every per-market cap and the never-add rule are enforced against the
ENGINE's ledger. That ledger is SQLite on an ephemeral filesystem, so a
deploy destroys it — and a fresh ledger believes it owns nothing, re-grants
the full per-market room, and buys the same market again.

Observed live 2026-08-02 against a $1.50 cap:
    $5.00  astatc-mlb-sf-sd-2026-08-02-k-mickin-gte6
    $4.72  astatc-mlb-az-cle-2026-08-02-k-merkel-gte4
    $4.23  atc-mlb-min-sea-2026-08-02-f5-sea
"""

import pytest

from edge.execution.executor import market_key, reconcile_positions
from edge.execution.risk import RiskManager
from edge.ledger.service import Ledger


class FakeVenue:
    name = "polymarket-us"

    def __init__(self, trades):
        self._trades = trades

    def recent_trades(self, limit=100):
        return self._trades[:limit]


def _trade(tid, slug, qty, price, side="BUY"):
    return {"id": tid, "marketSlug": slug, "qty": qty, "side": side,
            "price": {"value": price}}


def test_a_wiped_ledger_relearns_what_we_already_own(tmp_path):
    led = Ledger(db_path=str(tmp_path / "fresh.sqlite3"))
    mkey = market_key("polymarket-us", "k-mickin-gte6")
    assert led.position(mkey) is None            # the state after a deploy

    v = FakeVenue([_trade("t1", "k-mickin-gte6", 4.0, 0.25)])
    out = reconcile_positions(v, led, mode="LIVE_BETA")

    assert out["fills_added"] == 1
    assert out["cost_restored"] == 1.0
    pos = led.position(mkey)
    assert pos and pos["shares"] == 4.0


def test_the_per_market_cap_holds_again_after_reconciliation(tmp_path):
    """This is the actual failure: without reconciliation the cap re-grants
    its full room to a market we already hold."""
    from edge.execution.engine import Policy

    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    risk = RiskManager(led, {**Policy.load().risk, "mode": "LIVE_BETA"})
    mkey = market_key("polymarket-us", "k-mickin-gte6")

    # Fresh ledger: the market looks untouched and full room is available.
    assert risk.market_open_cost(mkey) == 0.0

    # The venue says otherwise — we already hold a cap-filling position
    # (120 x 0.25 = $30, the per-market cap at the $10 ticket).
    v = FakeVenue([_trade("t1", "k-mickin-gte6", 120.0, 0.25)])
    reconcile_positions(v, led, mode="LIVE_BETA")

    assert risk.market_open_cost(mkey) == pytest.approx(30.0)
    assert risk.market_open_cost(mkey) >= risk.caps.per_market


def test_reconciliation_is_idempotent(tmp_path):
    """It runs at startup and on a timer. Double-counting our own history
    would inflate cost basis and understate every ROI that follows."""
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    v = FakeVenue([_trade("t1", "k-a", 4.0, 0.25),
                   _trade("t2", "k-a", 4.0, 0.25)])

    first = reconcile_positions(v, led, mode="LIVE_BETA")
    second = reconcile_positions(v, led, mode="LIVE_BETA")

    assert first["fills_added"] == 2
    assert second["fills_added"] == 0, "replaying the feed must add nothing"
    pos = led.position(market_key("polymarket-us", "k-a"))
    assert pos["shares"] == 8.0


def test_sells_and_junk_are_ignored(tmp_path):
    """We are buy-only; a SELL in the feed is not ours to reconstruct. And a
    trade with an impossible price must not become a position."""
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    v = FakeVenue([
        _trade("t1", "k-a", 4.0, 0.25, side="SELL"),
        _trade("t2", "k-b", 0.0, 0.25),
        _trade("t3", "k-c", 4.0, 0.0),
        _trade("t4", "k-d", 4.0, 1.0),
        {"id": "t5", "qty": 4.0, "price": {"value": 0.25}},   # no slug
    ])
    assert reconcile_positions(v, led, mode="LIVE_BETA")["fills_added"] == 0
