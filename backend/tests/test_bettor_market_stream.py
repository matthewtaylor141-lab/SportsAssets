"""The corrected stream transport, and the loop it drives.

Each test names the mismatch it pins. The four the directive listed,
the fifth I found while reading, and the payload contract verified
against polymarket_us 0.1.2 rather than assumed.
"""
from __future__ import annotations

import ast
import inspect
import time
from datetime import datetime, timedelta, timezone

import pytest

from sportsassets import bettor_market_stream as ms
from sportsassets import bettor_observation_adapter as oa
from sportsassets import bettor_universe as uni


def _iso(delta_s=0.0):
    return (datetime.now(timezone.utc)
            + timedelta(seconds=delta_s)).isoformat()


def md(slug="s1", *, source_ts=None, state="MARKET_STATE_OPEN",
       bids=(("0.4500", "40"), ("0.4400", "900")),
       offers=(("0.4700", "12"), ("0.4800", "3000"))):
    return {"marketData": {
        "marketSlug": slug,
        "transactTime": source_ts if source_ts is not None else _iso(),
        "state": state,
        "bids": [{"px": {"value": p, "currency": "USD"}, "qty": q}
                 for p, q in bids],
        "offers": [{"px": {"value": p, "currency": "USD"}, "qty": q}
                   for p, q in offers],
        "stats": {"sharesTraded": "612.5"},
    }}


def stream(**kw):
    return ms.MarketStream("k", "s", **kw)


def feed(st, message):
    st._on_market_data(message)


def connect(st):
    """What a successful connect does to the cache's validity."""
    st.epoch += 1
    st.connected = True


# ── 1. the subscription ceiling ──────────────────────────────────────

class TestTheDocumentedSubscriptionCeiling:

    def test_the_batch_is_one_hundred_not_two_hundred(self):
        """Documented: "You can subscribe to a maximum of 100 markets
        per subscription." subscribe_market_data sends the whole list
        in one marketSlugs array, so the batch size IS the subscription
        size and 200 was double the ceiling on every request."""
        assert ms.SUB_BATCH == 100

    def test_the_limit_is_recorded_as_documented_not_as_a_guess(self):
        d = ms.describe()
        assert d["documented_limits"]["max_markets_per_subscription"] == 100
        assert any("docs.polymarket.us" in v
                   for v in d["verified_against"])


# ── 2. the clocks and the state ──────────────────────────────────────

class TestBothClocksAndTheMarketState:

    def test_the_venue_source_timestamp_is_kept(self):
        """The old cache stored time.time() and nothing else, so an
        engine with one clock could not tell a slow venue from a slow
        consumer."""
        st = stream()
        connect(st)
        feed(st, md(source_ts="2026-09-21T18:20:25.743291447Z"))
        at = st.book_at("s1", max_source_age_s=1e9, max_receipt_age_s=1e9)
        assert at["source_ts"] == "2026-09-21T18:20:25.743291447Z"
        assert at["received_at"] is not None
        assert at["source_ts"] != at["received_at"]

    def test_the_market_state_is_kept_and_gates_eligibility(self):
        """A HALTED market looked exactly like a quiet one. On
        2026-09-05 the venue was halted venue-wide for five hours."""
        st = stream()
        connect(st)
        feed(st, md(state="MARKET_STATE_HALTED"))
        at = st.book_at("s1", max_source_age_s=1e9, max_receipt_age_s=1e9)
        assert at["venue_state"] == "MARKET_STATE_HALTED"
        assert at["eligible"] is False
        assert at["reason"] == ms.NOT_OPEN

    def test_offers_not_asks(self):
        """The payload has `offers`. The old cache also asked for
        `asks`, a key that does not exist -- the guess is in the code."""
        fields = ms.describe()["payload_fields"]
        assert "offers" in fields and "asks" not in fields
        st = stream()
        connect(st)
        feed(st, md())
        at = st.book_at("s1", max_source_age_s=1e9, max_receipt_age_s=1e9)
        assert at["book"]["offers"][0]["qty"] == "12"

    def test_a_book_with_no_source_timestamp_is_not_eligible(self):
        st = stream()
        connect(st)
        m = md()
        del m["marketData"]["transactTime"]
        feed(st, m)
        at = st.book_at("s1", max_source_age_s=1e9, max_receipt_age_s=1e9)
        assert at["eligible"] is False
        assert at["reason"] == ms.NO_SOURCE_TS


# ── 3. no default age ────────────────────────────────────────────────

class TestNoDefaultAge:

    def test_both_bounds_are_required_arguments(self):
        """The one number deciding whether a book may be traded on is
        the number least safe to inherit by accident. The old reader
        defaulted it to 90 seconds."""
        sig = inspect.signature(ms.MarketStream.book_at)
        for name in ("max_source_age_s", "max_receipt_age_s"):
            assert sig.parameters[name].default is inspect.Parameter.empty
        st = stream()
        with pytest.raises(TypeError):
            st.book_at("s1")

    def test_a_ninety_second_book_is_stale_at_the_ten_second_bound(self):
        st = stream()
        connect(st)
        feed(st, md(source_ts=_iso(-90)))
        at = st.book_at("s1", max_source_age_s=10.0, max_receipt_age_s=1e9)
        assert at["eligible"] is False
        assert at["reason"] == ms.STALE_SOURCE
        assert at["source_age_s"] > 89

    def test_the_receipt_clock_has_its_own_bound_and_its_own_reason(self):
        st = stream()
        connect(st)
        feed(st, md(source_ts=_iso()))
        st._books["s1"]["received_at"] = time.time() - 30
        at = st.book_at("s1", max_source_age_s=1e9, max_receipt_age_s=5.0)
        assert at["reason"] == ms.STALE_RECEIPT


# ── 4. a disconnect invalidates ──────────────────────────────────────

class TestADisconnectInvalidatesEveryBook:

    def test_a_book_from_a_previous_epoch_is_ineligible_at_any_age(self):
        """The old cache kept serving across a drop, so a reconnect
        taking 40 s served 40-second-old books as live for the rest of
        the 90-second window."""
        st = stream()
        connect(st)
        feed(st, md(source_ts=_iso()))
        assert st.book_at("s1", max_source_age_s=10.0,
                          max_receipt_age_s=10.0)["eligible"] is True

        st.connected = False          # the drop
        at = st.book_at("s1", max_source_age_s=10.0, max_receipt_age_s=10.0)
        assert at["eligible"] is False
        assert at["reason"] == ms.DISCONNECTED

        connect(st)                   # reconnected, but no fresh book yet
        at = st.book_at("s1", max_source_age_s=10.0, max_receipt_age_s=10.0)
        assert at["eligible"] is False, "a stale epoch survived a reconnect"
        assert at["reason"] == ms.DISCONNECTED

        feed(st, md(source_ts=_iso()))   # the market speaks again
        assert st.book_at("s1", max_source_age_s=10.0,
                          max_receipt_age_s=10.0)["eligible"] is True

    def test_the_book_is_still_returned_so_the_refusal_can_be_audited(self):
        """Ineligible is not invisible. A caller must be able to see
        WHAT it refused, or the refusal cannot be checked."""
        st = stream()
        connect(st)
        feed(st, md())
        st.connected = False
        at = st.book_at("s1", max_source_age_s=10.0, max_receipt_age_s=10.0)
        assert at["book"] is not None and at["eligible"] is False


# ── 5. subscription was fire-and-forget ──────────────────────────────

class TestSubscribedWasAClaimNotAFact:

    def test_a_queued_slug_is_requested_not_subscribed(self):
        st = stream()
        st.subscribe(["a", "b"])
        r = st.subscription_report()
        assert r["requested"] == 2
        assert r["confirmed"] == 0
        assert r["by_state"][ms.REQUESTED] == 2

    def test_data_is_the_only_confirmation_this_protocol_offers(self):
        """MarketMessage is MarketData | MarketDataLite | Trade |
        Heartbeat | WebSocketErrorMessage. There is no 'subscribed'
        reply, so confirmation is the arrival of data."""
        st = stream()
        connect(st)
        st.subscribe(["s1", "s2"])
        feed(st, md("s1"))
        r = st.subscription_report()
        assert r["confirmed"] == 1
        assert st._subs["s1"]["state"] == ms.CONFIRMED
        assert st._subs["s2"]["state"] == ms.REQUESTED

    def test_an_error_naming_a_request_id_fails_that_batch(self):
        """The old reader logged errors at debug, so a subscription the
        venue REFUSED was indistinguishable from one with no trades."""
        st = stream()
        st.subscribe(["s1", "s2"])
        st._req_slugs["bk-1"] = ["s1", "s2"]

        class Err(Exception):
            request_id = "bk-1"

        st._on_error(Err("subscription rejected"))
        assert st.subscription_report()["failed"] == 2
        assert st._subs["s1"]["state"] == ms.FAILED

    def test_a_confirmed_slug_is_not_downgraded_by_a_later_error(self):
        st = stream()
        connect(st)
        st.subscribe(["s1"])
        feed(st, md("s1"))
        st._req_slugs["bk-1"] = ["s1"]

        class Err(Exception):
            request_id = "bk-1"

        st._on_error(Err("late error"))
        assert st._subs["s1"]["state"] == ms.CONFIRMED

    def test_the_cap_reports_what_it_dropped(self):
        st = stream()
        r = st.subscribe(["m%d" % i for i in range(ms.MAX_SUBSCRIPTIONS + 5)])
        assert r["queued"] == ms.MAX_SUBSCRIPTIONS
        assert r["dropped_over_cap"] == 5


# ── the handler/callback collision ───────────────────────────────────

class TestTheCallbacksDoNotShadowTheHandlers:

    def test_the_trade_handler_survives_a_user_callback(self):
        """`self._on_trade = on_trade` in __init__ shadowed the method
        of the same name, so ws.on("trade", self._on_trade) would have
        bound the user's callback in place of the handler."""
        seen = []
        st = stream(on_trade=seen.append)
        assert st._on_trade.__self__ is st
        st._on_trade({"trade": {"marketSlug": "s1",
                                "price": {"value": "0.45"},
                                "quantity": {"value": "10"},
                                "tradeTime": "2026-09-21T19:00:00Z",
                                "maker": {"side": "ORDER_SIDE_BUY"},
                                "taker": {"side": "ORDER_SIDE_SELL"}}})
        assert len(seen) == 1 and seen[0]["slug"] == "s1"
        assert seen[0]["maker_side"] == "ORDER_SIDE_BUY"

    def test_trades_drain_once(self):
        st = stream()
        st._on_trade({"trade": {"marketSlug": "s1",
                                "price": {"value": "0.45"},
                                "quantity": {"value": "10"},
                                "tradeTime": "t", "maker": {}, "taker": {}}})
        assert len(st.drain_trades()) == 1
        assert st.drain_trades() == []


# ── one normalizer for every source ──────────────────────────────────

class TestTheStreamFeedsTheSameAdapter:

    def test_a_stream_book_normalizes_and_sizes_from_the_touch(self):
        st = stream()
        connect(st)
        feed(st, md())
        at = st.book_at("s1", max_source_age_s=10.0, max_receipt_age_s=10.0)
        rec = oa.normalize(ms.to_observation("s1", at, outcome_leg="yes"))
        assert rec.status == oa.ACCEPTED, rec.reasons
        assert rec.bid == 0.45 and rec.ask == 0.47
        # The touch, not the five-level sum.
        assert rec.ask_size == 12.0
        assert rec.cumulative_ask_size == 3012.0

    def test_level_order_is_documented_not_assumed(self):
        """"Order book levels are sorted best-to-worst (highest bid
        first, lowest ask first)", so the array index IS the level."""
        st = stream()
        connect(st)
        feed(st, md())
        at = st.book_at("s1", max_source_age_s=10.0, max_receipt_age_s=10.0)
        row = ms.to_observation("s1", at, outcome_leg="yes")
        assert [x["level"] for x in row["multi_level_depth"]["ask"]] == [0, 1]
        assert row["multi_level_depth"]["ask"][0]["price"] == "0.4700"

    def test_the_complement_is_never_derived_from_the_stream(self):
        st = stream()
        connect(st)
        feed(st, md())
        at = st.book_at("s1", max_source_age_s=10.0, max_receipt_age_s=10.0)
        row = ms.to_observation("s1", at, outcome_leg="yes")
        assert row["no_bid"] == "NOT_IDENTIFIED"
        assert row["no_ask"] == "NOT_IDENTIFIED"

    def test_decimal_quantities_survive(self):
        """"qty may contain decimals for partial-contract markets"."""
        st = stream()
        connect(st)
        feed(st, md(offers=(("0.4700", "241.9300"),)))
        at = st.book_at("s1", max_source_age_s=10.0, max_receipt_age_s=10.0)
        rec = oa.normalize(ms.to_observation("s1", at, outcome_leg="yes"))
        assert rec.ask_size == 241.93


# ── no order path ────────────────────────────────────────────────────

class TestNoOrderPath:

    def test_no_order_call_is_reachable(self):
        tree = ast.parse(inspect.getsource(ms))
        called = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                f = n.func
                if isinstance(f, ast.Name):
                    called.add(f.id)
                elif isinstance(f, ast.Attribute):
                    called.add(f.attr)
        for forbidden in ("submit_fok", "close_position", "submit",
                          "place_order", "cancel_order"):
            assert forbidden not in called
        # It DOES subscribe, and those are the only venue calls.
        assert {"subscribe_market_data", "subscribe_trades"} <= called

    def test_it_declares_itself(self):
        d = ms.describe()
        assert d["submits_orders"] is False
        assert "pmus_stream" in d["separate_from"]
        assert d["book_replacement"] == "ASSUMED_FULL_REPLACEMENT"


# ── universe selection ───────────────────────────────────────────────

class TestUniverseSelection:

    def row(self, **kw):
        d = dict(slug="m1", bestBid="0.45", bestAsk="0.47",
                 sharesTraded="500", state="MARKET_STATE_OPEN")
        d.update(kw)
        return d

    def test_a_liquid_two_sided_open_market_is_included(self):
        a = uni.assess(self.row())
        assert a["included"] is True and a["spread_ticks"] == 2.0

    def test_a_one_tick_book_has_nothing_to_pay_adverse_selection_with(self):
        a = uni.assess(self.row(bestAsk="0.46"))
        assert a["included"] is False and a["reason"] == uni.R_SPREAD

    def test_an_illiquid_market_is_excluded_by_activity(self):
        """The test the old capacity analysis never applied: a market
        that does not trade cannot fill our quote however wide it is."""
        a = uni.assess(self.row(sharesTraded="3"))
        assert a["reason"] == uni.R_VOLUME

    def test_no_volume_figure_is_a_different_exclusion_from_zero_volume(self):
        a = uni.assess(self.row(sharesTraded=None))
        assert a["reason"] == uni.R_NO_VOLUME
        assert uni.assess(self.row(sharesTraded="0"))["reason"] == uni.R_VOLUME

    def test_a_closed_market_is_excluded(self):
        assert uni.assess(self.row(closed=True))["reason"] == uni.R_NOT_OPEN

    def test_price_above_the_cap_is_excluded(self):
        a = uni.assess(self.row(bestBid="0.60", bestAsk="0.63"))
        assert a["reason"] == uni.R_PRICE

    def test_selection_ranks_by_volume_not_by_spread(self):
        """Ranking by spread selects the markets where a resting order
        sits longest and is picked off hardest."""
        rows = [self.row(slug="wide", bestBid="0.10", bestAsk="0.40",
                         sharesTraded="200"),
                self.row(slug="busy", bestBid="0.45", bestAsk="0.47",
                         sharesTraded="9000")]
        out = uni.select(rows)
        assert out["slugs"][0] == "busy"

    def test_the_universe_is_stable_across_runs(self):
        """A universe that reshuffles makes two runs incomparable."""
        rows = [self.row(slug="a", sharesTraded="500"),
                self.row(slug="b", sharesTraded="500")]
        assert uni.select(rows)["slugs"] == uni.select(rows[::-1])["slugs"]

    def test_every_exclusion_is_counted_by_reason(self):
        rows = [self.row(slug="a", closed=True),
                self.row(slug="b", bestAsk="0.46"),
                self.row(slug="c", sharesTraded="1")]
        out = uni.select(rows)
        assert out["excluded_by_reason"] == {
            uni.R_NOT_OPEN: 1, uni.R_SPREAD: 1, uni.R_VOLUME: 1}
        assert out["selected"] == 0

    def test_the_cap_is_one_subscription_by_the_documented_limit(self):
        assert uni.MAX_MARKETS <= ms.SUB_BATCH
