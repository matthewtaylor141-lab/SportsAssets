"""Same-event guard: both sides of one Kalshi market only as a
guaranteed, pair-matched book — never by accident (live incident
2026-08-04: copies followed two whales onto opposite sides at prices
summing over $1)."""

import tempfile
import time

from edge.ledger.service import Ledger
from edge.shadow.kalshi_copies import sweep
from edge.shadow.kalshi_guard import cross_side_cap, event_of, live_blocked, \
    note_fill, open_kalshi_sides


def test_event_of_strips_the_outcome_segment():
    assert event_of("KXWTAMATCH-26AUG04ANDPLI-AND") == "KXWTAMATCH-26AUG04ANDPLI"
    assert event_of("KXMLBGAME-26AUG041835LAABAL-LAA") == "KXMLBGAME-26AUG041835LAABAL"


def test_no_opposite_side_means_directional_rules_untouched():
    assert cross_side_cap({}, "KX-EVT-A", 0.60, 7) == 7


def test_losing_pair_is_refused_outright():
    sides = {"EVT": [{"ticker": "KX-EVT-B", "shares": 8, "avg_cost": 0.38}]}
    # 0.63 + 0.38 > 0.99: the Atmane/Draper shape. Zero contracts.
    assert cross_side_cap(sides, "KX-EVT-A", 0.63, 4, fee_per_contract=0.016) == 0


def test_guaranteed_pair_is_allowed_but_only_pair_matched():
    sides = {"EVT": [{"ticker": "KX-EVT-B", "shares": 6, "avg_cost": 0.18}]}
    # 0.43 + 0.18 + fee well under 0.99 -> allowed, capped at 6 pairs.
    assert cross_side_cap(sides, "KX-EVT-A", 0.43, 10,
                          fee_per_contract=0.017) == 6


def test_same_side_reentry_is_not_cross_side():
    sides = {"EVT": [{"ticker": "KX-EVT-A", "shares": 5, "avg_cost": 0.50}]}
    assert cross_side_cap(sides, "KX-EVT-A", 0.55, 3) == 3


def test_note_fill_makes_later_orders_in_sweep_see_the_leg():
    sides = {}
    note_fill(sides, "KX-EVT-A", 0.60, 4)
    assert cross_side_cap(sides, "KX-EVT-B", 0.55, 4) == 0


def test_open_kalshi_sides_reads_only_live_kalshi_positions():
    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    led.record_fill(fill_uid="k1", venue="kalshi",
                    market_key="kalshi:KX-EVT-A", side="BUY", qty=4, price=0.6,
                    fee=0.02, league="wta", mode="LIVE_BETA",
                    category="kalshi_copy", decision={})
    led.record_fill(fill_uid="p1", venue="polymarket_us",
                    market_key="pmus:slug-x", side="BUY", qty=4, price=0.5,
                    fee=0.0, league="mlb", mode="LIVE_BETA",
                    category="engine", decision={})
    led.record_fill(fill_uid="k2", venue="kalshi",
                    market_key="kalshi:KX-EVT2-C", side="BUY", qty=2, price=0.3,
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
           "price": 0.55, "whale": "HomeRunHazard",
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
           "price": 0.55, "whale": "HomeRunHazard",
           "entered_ts": time.time() - 60}
    st = sweep(kalshi=ka, ledger=led, identities=[row], live=True)
    assert st["copied"] == 1
    assert ka.orders == [("KXMLBGAME-26AUG04BALTEX-BAL", 0.50, 3)], \
        "pair-matched: 3 contracts, not the $3 default of 6"


def test_reaper_releases_expired_unfilled_maker_claims():
    from edge.execution.executor import reap_kalshi_makers

    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    led.claim_event("ev-1", "kalshi:KX-EVT-A", "kalshi")
    led.set_state("kalshi_order:o1", {"market_key": "kalshi:KX-EVT-A",
                                      "event_key": "ev-1", "taker": False,
                                      "ts": time.time() - 2000})
    out = reap_kalshi_makers(led)
    assert out == {"checked": 1, "released": 1, "filled": 0}
    assert led.get_state("kalshi_order:o1") is None
    assert led.claim_event("ev-1", "kalshi:KX-EVT-A", "kalshi"), \
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


def test_cross_market_same_game_is_refused_outright():
    """Spread/total vs moneyline of the SAME game can never lock $1 —
    refused regardless of prices (owner rule 2026-08-05)."""
    sides = {"26AUG04BALTEX": [{"ticker": "KXMLBGAME-26AUG04BALTEX-TEX",
                                "shares": 4, "avg_cost": 0.20}]}
    assert cross_side_cap(sides, "KXMLBSPREAD-26AUG04BALTEX-BAL",
                          0.30, 5) == 0


def test_engine_executor_path_is_game_guarded(tmp_path):
    from edge.execution.executor import execute, market_key

    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    led.record_fill(fill_uid="k1", venue="kalshi",
                    market_key="kalshi:KXMLBGAME-26AUG04BALTEX-TEX",
                    side="BUY", qty=4, price=0.55, fee=0.02, league="mlb",
                    mode="LIVE_BETA", category="kalshi_copy", decision={})

    class _A:
        name = "kalshi"

        def taker_fee(self, p):
            return 0.07 * p * (1 - p)

        def plan_maker_order(self, *a, **k):
            return (0.50, True)

        def place_order(self, *a, **k):
            raise AssertionError("order must be blocked before the venue")

    r = execute(adapter=_A(), ledger=led, mode="LIVE_BETA",
                mkey=market_key("kalshi", "KXMLBGAME-26AUG04BALTEX-BAL"),
                league="mlb", ask_price=0.51, ask_size=50, size_usd=2.0,
                edge=0.03, threshold=0.02, decision={}, ts=1000.0)
    assert r["status"] == "cross_game_blocked" and not r["placed"]


def test_copy_scope_rides_its_own_breaker_not_the_engines(tmp_path):
    """Owner directive 2026-08-05: an engine-side halt must not pause the
    copy sleeve; a copy-side halt must not need the engine's. Kill switch
    and watchdog stay global — no sleeve trades through those."""
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    # Engine breaker live: engine scope blocks, copy scope does not.
    led.set_state("halt_until", {"until": time.time() + 3600})
    assert live_blocked(led) == "halted"
    assert live_blocked(led, scope="copy") is None
    # Copy breaker live: copy scope blocks, engine scope unaffected.
    led.set_state("halt_until", {"until": 0})
    led.set_state("copy_halt_until", {"until": time.time() + 3600})
    assert live_blocked(led, scope="copy") == "copy_halted"
    assert live_blocked(led) is None
    # Global stops block BOTH scopes.
    led.set_state("kill_switch", True)
    assert live_blocked(led) == "kill_switch"
    assert live_blocked(led, scope="copy") == "kill_switch"
    led.set_state("kill_switch", False)
    led.set_state("copy_halt_until", {"until": 0})
    led.set_state("watchdog_tripped", {"tripped": True, "reason": "feed"})
    assert live_blocked(led, scope="copy") == "watchdog"


def test_copy_breaker_trips_on_copy_losses_only(tmp_path, monkeypatch):
    """The copy breaker reads the kalshi_copy cohort's realized 24h P&L —
    engine-category losses can never trip it, and a trip halts copies."""
    from edge.shadow.kalshi_copies import check_copy_breaker

    monkeypatch.setenv("EDGE_KCOPY_HALT_USD", "50")
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    # $80 of ENGINE losses: copy breaker must not care.
    led.record_fill(fill_uid="e1", venue="kalshi", market_key="kalshi:KXA-1-X",
                    side="BUY", qty=100.0, price=0.8, league="mlb",
                    mode="LIVE_BETA", category="edge")
    led.record_resolution("kalshi:KXA-1-X", 0.0)
    assert check_copy_breaker(led) is None
    # $60 of COPY losses inside 24h: trips, and the halt state persists.
    led.record_fill(fill_uid="c1", venue="kalshi", market_key="kalshi:KXB-1-Y",
                    side="BUY", qty=100.0, price=0.6, league="mlb",
                    mode="LIVE_BETA", category="kalshi_copy")
    led.record_resolution("kalshi:KXB-1-Y", 0.0)
    assert check_copy_breaker(led) == "copy_halted"
    assert live_blocked(led, scope="copy") == "copy_halted"
    assert live_blocked(led) is None      # engine untouched by copy halt
