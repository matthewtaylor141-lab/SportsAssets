"""Same-event guard: both sides of one Kalshi market only as a
guaranteed, pair-matched book — never by accident (live incident
2026-08-04: copies followed two whales onto opposite sides at prices
summing over $1)."""

import tempfile
import time

from edge.ledger.service import Ledger
from edge.shadow.kalshi_copies import sweep
from edge.shadow.kalshi_guard import cross_side_cap, event_of, note_fill, \
    open_kalshi_sides


def test_event_of_strips_the_outcome_segment():
    assert event_of("KXWTAMATCH-26AUG04ANDPLI-AND") == "KXWTAMATCH-26AUG04ANDPLI"
    assert event_of("KXMLBGAME-26AUG041835LAABAL-LAA") == "KXMLBGAME-26AUG041835LAABAL"


def test_no_opposite_side_means_directional_rules_untouched():
    assert cross_side_cap({}, "EVT-A", 0.60, 7) == 7


def test_losing_pair_is_refused_outright():
    sides = {"EVT": [{"ticker": "EVT-B", "shares": 8, "avg_cost": 0.38}]}
    # 0.63 + 0.38 > 0.99: the Atmane/Draper shape. Zero contracts.
    assert cross_side_cap(sides, "EVT-A", 0.63, 4, fee_per_contract=0.016) == 0


def test_guaranteed_pair_is_allowed_but_only_pair_matched():
    sides = {"EVT": [{"ticker": "EVT-B", "shares": 6, "avg_cost": 0.18}]}
    # 0.43 + 0.18 + fee well under 0.99 -> allowed, capped at 6 pairs.
    assert cross_side_cap(sides, "EVT-A", 0.43, 10,
                          fee_per_contract=0.017) == 6


def test_same_side_reentry_is_not_cross_side():
    sides = {"EVT": [{"ticker": "EVT-A", "shares": 5, "avg_cost": 0.50}]}
    assert cross_side_cap(sides, "EVT-A", 0.55, 3) == 3


def test_note_fill_makes_later_orders_in_sweep_see_the_leg():
    sides = {}
    note_fill(sides, "EVT-A", 0.60, 4)
    assert cross_side_cap(sides, "EVT-B", 0.55, 4) == 0


def test_open_kalshi_sides_reads_only_live_kalshi_positions():
    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    led.record_fill(fill_uid="k1", venue="kalshi",
                    market_key="kalshi:EVT-A", side="BUY", qty=4, price=0.6,
                    fee=0.02, league="wta", mode="LIVE_BETA",
                    category="kalshi_copy", decision={})
    led.record_fill(fill_uid="p1", venue="polymarket_us",
                    market_key="pmus:slug-x", side="BUY", qty=4, price=0.5,
                    fee=0.0, league="mlb", mode="LIVE_BETA",
                    category="engine", decision={})
    led.record_fill(fill_uid="k2", venue="kalshi",
                    market_key="kalshi:EVT2-C", side="BUY", qty=2, price=0.3,
                    fee=0.01, league="wta", mode="PAPER",
                    category="kalshi_copy", decision={})
    sides = open_kalshi_sides(led)
    assert set(sides) == {"EVT"}
    assert sides["EVT"][0]["shares"] == 4.0


class _Kalshi:
    name = "kalshi"

    def __init__(self, ask):
        self.ask = ask
        self.orders = []

    def taker_fee(self, price):
        return 0.07 * price * (1 - price)

    def discover_markets(self, league_codes):
        from edge.venues.mapper import VenueMarket
        return [VenueMarket(market_id="KXMLBGAME-26AUG04BALTEX",
                            title="Orioles at Rangers", league_code="mlb",
                            outcome_tokens={"Baltimore Orioles": "KXMLBGAME-26AUG04BALTEX-BAL",
                                            "Texas Rangers": "KXMLBGAME-26AUG04BALTEX-TEX"})]

    def get_book(self, market_id, ticker):
        from edge.venues.base import BookLevel, MarketBook
        return MarketBook(venue=self.name, market_id=market_id,
                          outcome_id=ticker, bids=[],
                          asks=[BookLevel(self.ask, 60)], ts=time.time())

    def place_order(self, ticker, price, count, **kw):
        self.orders.append((ticker, price, count))
        return {"ok": True, "count": count, "price": price,
                "status": "filled"}


def test_copy_sweep_refuses_the_second_side_of_a_losing_pair():
    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    # We already hold Texas at 55c live.
    led.record_fill(fill_uid="k1", venue="kalshi",
                    market_key="kalshi:KXMLBGAME-26AUG04BALTEX-TEX",
                    side="BUY", qty=4, price=0.55, fee=0.02, league="mlb",
                    mode="LIVE_BETA", category="kalshi_copy", decision={})
    ka = _Kalshi(0.50)   # Baltimore ask 50c: 0.50+0.55 > 1 -> refuse
    row = {"slug": "mlb-bal-tex-2026-08-04", "outcome": "Baltimore Orioles",
           "price": 0.55, "whale": "swisstony",
           "entered_ts": time.time() - 60}
    st = sweep(kalshi=ka, ledger=led, identities=[row], live=True)
    assert st.get("skipped_cross_side") == 1
    assert not ka.orders


def test_copy_sweep_completes_a_guaranteed_pair_matched():
    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    # Held: Texas 3 @ 20c. New: Baltimore at 50c -> 0.70 + fee < 0.99 ok.
    led.record_fill(fill_uid="k1", venue="kalshi",
                    market_key="kalshi:KXMLBGAME-26AUG04BALTEX-TEX",
                    side="BUY", qty=3, price=0.20, fee=0.01, league="mlb",
                    mode="LIVE_BETA", category="kalshi_copy", decision={})
    ka = _Kalshi(0.50)
    row = {"slug": "mlb-bal-tex-2026-08-04", "outcome": "Baltimore Orioles",
           "price": 0.55, "whale": "swisstony",
           "entered_ts": time.time() - 60}
    st = sweep(kalshi=ka, ledger=led, identities=[row], live=True)
    assert st["copied"] == 1
    assert ka.orders == [("KXMLBGAME-26AUG04BALTEX-BAL", 0.50, 3)], \
        "pair-matched: 3 contracts, not the $2 default of 4"


def test_reaper_releases_expired_unfilled_maker_claims():
    from edge.execution.executor import reap_kalshi_makers

    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    led.claim_event("ev-1", "kalshi:EVT-A", "kalshi")
    led.set_state("kalshi_order:o1", {"market_key": "kalshi:EVT-A",
                                      "event_key": "ev-1", "taker": False,
                                      "ts": time.time() - 2000})
    out = reap_kalshi_makers(led)
    assert out == {"checked": 1, "released": 1, "filled": 0}
    assert led.get_state("kalshi_order:o1") is None
    assert led.claim_event("ev-1", "kalshi:EVT-A", "kalshi"), \
        "the claim must be reusable after the release"


def test_reaper_keeps_the_claim_when_the_order_filled():
    from edge.execution.executor import reap_kalshi_makers

    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    led.claim_event("ev-2", "kalshi:EVT-B", "kalshi")
    led.record_fill(fill_uid="f1", venue="kalshi",
                    market_key="kalshi:EVT-B", side="BUY", qty=3, price=0.4,
                    fee=0.0, league="mlb", mode="LIVE_BETA",
                    category="engine", decision={})
    led.set_state("kalshi_order:o2", {"market_key": "kalshi:EVT-B",
                                      "event_key": "ev-2", "taker": False,
                                      "ts": time.time() - 2000})
    out = reap_kalshi_makers(led)
    assert out["filled"] == 1 and out["released"] == 0
    assert not led.claim_event("ev-2", "kalshi:EVT-B", "kalshi"), \
        "a filled market keeps its never-add claim"


def test_reaper_leaves_young_orders_alone():
    from edge.execution.executor import reap_kalshi_makers

    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    led.set_state("kalshi_order:o3", {"market_key": "kalshi:EVT-C",
                                      "event_key": "ev-3", "taker": False,
                                      "ts": time.time() - 60})
    out = reap_kalshi_makers(led)
    assert out["checked"] == 0
    assert led.get_state("kalshi_order:o3") is not None
