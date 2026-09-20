"""THE DIRECT INSTITUTIONAL PATH, pinned.

Owner directive 2026-09-20: the production credential sits on
sportsassets-workers, the bridge stops being the primary market-data
path, and §4 says ORDER_SUBMISSION_IMPLEMENTATION = NONE "even if the
token contains write:orders".

WHAT THESE TESTS ARE FOR. Three of the properties here are the ones a
reader would otherwise have to take on trust, so each is checked
against the thing itself rather than against a fixture built to agree
with it:

  * that the lane cannot send an order -- checked against the SOURCE
    TEXT of every module in the direct path, not against a flag;
  * that a secret never leaves the process -- checked by putting a
    sentinel value in the environment and asserting it appears nowhere
    in what `presence()` returns;
  * that a stale book is not executable -- checked by letting the clock
    move rather than by setting a status field.
"""

from __future__ import annotations

import inspect
import json
from datetime import datetime, timedelta, timezone

import pytest

from sportsassets import institutional_book as ib
from sportsassets import pmx_institutional as pmx
from sportsassets import shadow_experimental_store as xstore
from sportsassets.workers import institutional_md as md
from sportsassets.workers import shadow_experimental as worker

NOW = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)

# One production-shaped book. px/qty are SCALED INTEGER STRINGS and the
# ask side is named `offers`, both as the venue actually sends them.
BOOK = {
    "symbol": "X",
    "transactTime": "2026-09-20T11:59:59.900000Z",
    "state": "OPEN",
    "bids": [{"px": "41", "qty": "300"}, {"px": "40", "qty": "500"}],
    "offers": [{"px": "42", "qty": "500"}, {"px": "43", "qty": "900"}],
}
INSTRUMENT = {"symbol": "X", "priceScale": "100", "fractionalQtyScale": "1"}


def store_with_book(*, received_at=NOW, response=None):
    s = ib.BookStore()
    ps, qs = pmx.scales_of(INSTRUMENT)
    s.put_instrument("X", INSTRUMENT, price_scale=ps, qty_scale=qs)
    s.put_book("X", response or BOOK, received_at=received_at)
    return s


# ── §4: there is no order path, and it is checked in the source ──────

DIRECT_PATH_MODULES = (pmx, ib, md)

# An ACTION and its object. The word "order" alone appears in this
# lane's prose constantly ("no order path", "order submission"), so the
# scan is for verbs that could only name a call.
ORDER_VERBS = ("place_order", "create_order", "submit_order",
               "cancel_order", "replace_order", "amend_order",
               "post_order", "send_order", "new_order", "modify_order",
               "order_preview", "preview_order", "withdraw", "deposit",
               "transfer_funds")


@pytest.mark.parametrize("module", DIRECT_PATH_MODULES,
                         ids=lambda m: m.__name__.rsplit(".", 1)[-1])
def test_no_module_in_the_direct_path_names_an_order_verb(module):
    src = inspect.getsource(module).lower()
    named = [v for v in ORDER_VERBS if v in src]
    assert not named, (
        "%s names an order verb: %s. §4: ORDER_SUBMISSION_IMPLEMENTATION "
        "= NONE, and that is a property of the source text, not of a "
        "flag somebody can flip." % (module.__name__, ", ".join(named)))


def test_the_read_allow_list_is_exactly_three_market_data_reads():
    assert set(pmx.READS) == {"instruments", "bbo", "book"}
    for name in pmx.READS:
        _method, path, _shape = pmx.READ_ONLY_PATHS[name]
        assert path.startswith(("/v1/refdata/", "/v1/orderbook/")), path


def test_a_read_that_is_not_on_the_allow_list_is_refused_by_name():
    # Refused BEFORE a socket is opened: no session is even constructed.
    for name in ("orders", "order", "cancel", "positions", "balance",
                 "withdrawals"):
        with pytest.raises(pmx.NotAReadPath):
            pmx.request_for(name, "X")


def test_a_non_production_host_is_refused():
    with pytest.raises(pmx.NotProduction):
        pmx.assert_production("https://api.preprod.polymarketexchange.com/v1")
    # And the module's own constants pass it, or nothing would work.
    assert pmx.assert_production(pmx.REST_BASE)


def test_the_two_audiences_are_different_values():
    """Production evidence 2026-09-19: the assertion's `aud` claim is
    Auth0's token endpoint; the `audience` FORM FIELD is the REST base.
    Collapsing them into one constant is the failure that cost a run."""
    assert pmx.CLIENT_ASSERTION_AUD != pmx.TOKEN_REQUEST_AUDIENCE
    assert pmx.CLIENT_ASSERTION_AUD.endswith("/oauth/token")
    assert pmx.TOKEN_REQUEST_AUDIENCE == pmx.REST_BASE


# ── §1: presence is presence. Never a length, a prefix or a hash ─────


def test_presence_reports_names_and_nothing_about_the_values():
    # A sentinel with no English substring in it, so a match
    # means the value leaked and never means a coincidence.
    secret = "zq7v9w2k4m8j5r3p"
    env = {"PMX_CLIENT_ID": secret, "PMX_PARTICIPANT_ID": secret,
           "PMX_KEY_ID": secret, "PMX_PRIVATE_KEY_B64": secret}
    seen = pmx.presence(env)

    assert seen["verdict"] is None and seen["missing"] == []
    assert all(seen[n + "_PRESENT"] is True
               for n in pmx.CREDENTIALS_EXPECTED)

    # THE WHOLE POINT: the value is nowhere in the answer, in any form.
    blob = json.dumps(seen)
    assert secret not in blob
    for i in range(4, len(secret)):          # no prefix of any length
        assert secret[:i] not in blob
    assert str(len(secret)) not in blob      # not even the length


def test_presence_names_which_credential_is_missing():
    seen = pmx.presence({"PMX_CLIENT_ID": "x", "PMX_KEY_ID": "  "})
    assert seen["verdict"] == pmx.A_SECRET_MISSING
    assert seen["missing"] == ["PMX_PARTICIPANT_ID", "PMX_KEY_ID",
                               "PMX_PRIVATE_KEY_B64"]
    assert seen["PMX_CLIENT_ID_PRESENT"] is True


# ── §6: the scales are the instrument's own, never a default ─────────


def test_scales_come_from_the_record_and_are_never_defaulted():
    assert pmx.scales_of(INSTRUMENT) == (100, 1)
    assert pmx.scales_of({"priceScale": "1000",
                          "fractionalQtyScale": "100"}) == (1000, 100)
    for bad in (None, {}, {"priceScale": "100"}, {"priceScale": "0",
                                                  "fractionalQtyScale": "1"}):
        assert pmx.scales_of(bad) == (None, None)


def test_a_book_without_known_scales_is_not_stored_at_all():
    """A raw book kept as if it were priced is how a $49 fill becomes
    $4,900 on an instrument whose scale is 1000."""
    s = ib.BookStore()
    assert s.put_book("X", BOOK, received_at=NOW) is None
    s.put_instrument("X", {"symbol": "X"}, price_scale=None, qty_scale=None)
    assert s.put_book("X", BOOK, received_at=NOW) is None
    assert s.current("X", at=NOW)["FRESHNESS_STATUS"] == ib.ABSENT


# ── §6: every book state carries the directive's own field names ─────


def test_a_stored_book_carries_every_field_section_six_names():
    row = store_with_book().current("X", at=NOW)
    for field in ("INSTRUMENT_ID", "SOURCE_TIMESTAMP",
                  "BETTOR_RECEIVED_TIMESTAMP", "BOOK_SHA", "BIDS",
                  "OFFERS", "FRESHNESS_STATUS"):
        assert field in row, field
    assert row["INSTRUMENT_ID"] == "X"
    assert row["SOURCE_TIMESTAMP"] == BOOK["transactTime"]
    assert row["BIDS"] == BOOK["bids"] and row["OFFERS"] == BOOK["offers"]
    assert row["evidenceEnvironment"] == "DIRECT_INSTITUTIONAL_WORKER"


def test_the_sha_is_over_the_venues_raw_levels():
    """So a decision's book and its evidence row join on the same digest
    rather than on two digests of two different objects."""
    row = store_with_book().current("X", at=NOW)
    assert row["BOOK_SHA"] == ib.book_sha(BOOK["bids"], BOOK["offers"])
    moved = dict(BOOK, bids=[{"px": "41", "qty": "301"}])
    assert ib.book_sha(moved["bids"], moved["offers"]) != row["BOOK_SHA"]


def test_market_data_lag_is_the_venues_instant_against_ours():
    row = store_with_book().current("X", at=NOW)
    # transactTime is 100ms before the receive instant.
    assert row["MARKET_DATA_LAG_MS"] == pytest.approx(100.0)


def test_a_book_with_no_venue_timestamp_has_no_lag_rather_than_zero():
    """NOT_IDENTIFIED, never 0.0: a zero in a lag column is a
    measurement, and no measurement was made."""
    no_ts = {k: v for k, v in BOOK.items() if k != "transactTime"}
    row = store_with_book(response=no_ts).current("X", at=NOW)
    assert row["MARKET_DATA_LAG_MS"] is None


# ── §6: "A stale book is not executable evidence" ────────────────────


def test_freshness_is_computed_from_the_clock_not_declared():
    s = store_with_book()
    inside = NOW + timedelta(seconds=ib.FRESHNESS_LIMIT_S - 0.1)
    outside = NOW + timedelta(seconds=ib.FRESHNESS_LIMIT_S + 0.1)
    assert s.current("X", at=inside)["FRESHNESS_STATUS"] == ib.CURRENT
    assert s.current("X", at=outside)["FRESHNESS_STATUS"] == ib.STALE
    assert s.current("X", at=outside)["bookAgeMs"] == pytest.approx(5100.0)


def test_only_a_current_book_is_executable():
    s = store_with_book()
    assert s.executable("X", at=NOW) is not None
    assert s.executable(
        "X", at=NOW + timedelta(seconds=ib.FRESHNESS_LIMIT_S + 0.1)) is None
    assert s.executable("NEVER-SEEN", at=NOW) is None


def test_an_absent_book_is_a_named_state_not_none():
    """So a caller that forgot to check cannot read 'no book' as
    'empty book' and walk it to a zero fill."""
    row = ib.BookStore().current("X", at=NOW)
    assert row["FRESHNESS_STATUS"] == ib.ABSENT
    assert row["book"] is None and row["why"]


def test_the_freshness_limit_is_a_frozen_literal():
    assert ib.FRESHNESS_LIMIT_S == 5.0


# ── §9: the modeled latency is modeled, and says so ──────────────────


def test_modeled_arrival_is_the_decision_instant_plus_the_frozen_wait():
    assert ib.modeled_arrival(NOW, 250) == NOW + timedelta(milliseconds=250)


def test_an_in_memory_book_carries_no_observed_arrival_latency():
    """There is no transport round trip at this instant -- the book was
    already here -- so claiming one would be inventing a measurement."""
    row = store_with_book().current("X", at=NOW)
    ev = worker.evidence_from_memory(row, "l2dw_x")
    assert "observedArrivalLatencyMs" not in ev
    assert ev["latencyRegime"] == "DIRECT_INSTITUTIONAL_WORKER"
    assert ev["bridgeLatencyMs"] is None
    assert ev["l2BookSha"] == row["BOOK_SHA"]


# ── §8: every instant, and every interval derived from two ───────────


def test_the_latency_block_keeps_nine_instants_and_six_intervals_apart():
    row = store_with_book().current("X", at=NOW)
    sealed_at = NOW + timedelta(milliseconds=40)
    model_start = NOW + timedelta(milliseconds=50)
    model_end = NOW + timedelta(milliseconds=70)
    arrival = ib.modeled_arrival(model_end, 250)

    lat = worker.latency_block(
        observation={"observedAt": NOW - timedelta(milliseconds=10)},
        sealed_at=sealed_at, model_start=model_start, model_end=model_end,
        modeled_arrival=arrival, book_row=row,
        persisted_at=NOW + timedelta(milliseconds=500))

    assert lat["venueSourceTimestamp"] == BOOK["transactTime"]
    assert lat["bettorReceivedTimestamp"] == NOW
    assert lat["marketDataLagMs"] == pytest.approx(100.0)
    assert lat["featureComputeMs"] == pytest.approx(60.0)
    assert lat["modelComputeMs"] == pytest.approx(20.0)
    assert lat["modeledExecutionLatencyMs"] == pytest.approx(280.0)
    # source->decision = venue lag + (received -> decision)
    assert lat["sourceToDecisionMs"] == pytest.approx(140.0)
    assert lat["sourceToModeledArrivalMs"] == pytest.approx(420.0)

    # NOT ONE NUMBER. Six intervals, all different, none derivable from
    # a single collapsed figure.
    intervals = {lat["marketDataLagMs"], lat["featureComputeMs"],
                 lat["modelComputeMs"], lat["sourceToDecisionMs"],
                 lat["modeledExecutionLatencyMs"],
                 lat["sourceToModeledArrivalMs"]}
    assert len(intervals) == 6


def test_the_latency_basis_says_modeled_and_never_observed():
    lat = worker.latency_block(
        observation={}, sealed_at=NOW, model_start=NOW, model_end=NOW,
        modeled_arrival=NOW, book_row=None)
    assert lat["executionLatencyBasis"] == "MODELED_EXECUTION_LATENCY"
    assert "OBSERVED" not in lat["executionLatencyBasis"]


def test_a_missing_venue_timestamp_leaves_the_source_intervals_null():
    no_ts = {k: v for k, v in BOOK.items() if k != "transactTime"}
    row = store_with_book(response=no_ts).current("X", at=NOW)
    lat = worker.latency_block(
        observation={}, sealed_at=NOW, model_start=NOW, model_end=NOW,
        modeled_arrival=NOW + timedelta(milliseconds=250), book_row=row)
    assert lat["sourceToDecisionMs"] is None
    assert lat["sourceToModeledArrivalMs"] is None
    # The intervals that DO NOT depend on the venue's clock survive.
    assert lat["modeledExecutionLatencyMs"] == pytest.approx(250.0)


# ── the §8 columns actually reach the insert ─────────────────────────


class CapturingPool:
    def __init__(self):
        self.args = None

    async def fetchrow(self, sql, *args):
        self.args = args
        return {"experimental_decision_id": args[0]}


SEAL = {
    "experimentId": "X1", "experimentVersion": "1", "experimentSha": "sha",
    "marketId": "X", "identityBindingStatus": "EXACT_ONE_TO_ONE",
    "featureSourceVersion": "v1", "featuresSha": "fsha", "action": "BUY_YES",
    "decisionTimestamp": NOW, "intendedNotionalUsd": 1000.0,
    "features": {}, "modelOutput": {}, "outcomeLeg": "yes",
}


@pytest.mark.asyncio
async def test_record_decision_writes_every_section_eight_column():
    pool = CapturingPool()
    row = store_with_book().current("X", at=NOW)
    arrival = NOW + timedelta(milliseconds=250)
    lat = worker.latency_block(
        observation={"observedAt": NOW}, sealed_at=NOW, model_start=NOW,
        model_end=NOW + timedelta(milliseconds=20), modeled_arrival=arrival,
        book_row=row)

    assert await xstore.record_decision(
        pool, SEAL, {"experimentalDecisionId": "d1",
                     "executionStatus": "EXECUTED"}, latency=lat)

    by_name = dict(zip(xstore.DECISION_COLUMNS, pool.args))
    assert by_name["venue_source_timestamp"] == BOOK["transactTime"]
    assert by_name["bettor_received_timestamp"] == NOW
    assert by_name["model_end_timestamp"] == NOW + timedelta(milliseconds=20)
    assert by_name["modeled_arrival_timestamp"] == arrival
    assert by_name["market_data_lag_ms"] == pytest.approx(100.0)
    assert by_name["modeled_execution_latency_ms"] == pytest.approx(250.0)
    assert by_name["book_freshness_status"] == ib.CURRENT
    assert by_name["execution_latency_basis"] == "MODELED_EXECUTION_LATENCY"
    assert by_name["evidence_environment"] == "DIRECT_INSTITUTIONAL_WORKER"
    # The persist instant is stamped where the persist happened.
    assert by_name["persisted_timestamp"] is not None


@pytest.mark.asyncio
async def test_the_bridge_path_writes_nulls_rather_than_zero_latencies():
    """A zero in a lag column is a measurement. The bridge has no
    in-memory book and made none."""
    pool = CapturingPool()
    assert await xstore.record_decision(
        pool, SEAL, {"experimentalDecisionId": "d2",
                     "executionStatus": "EXECUTED"})
    by_name = dict(zip(xstore.DECISION_COLUMNS, pool.args))
    for column in ("venue_source_timestamp", "bettor_received_timestamp",
                   "market_data_lag_ms", "modeled_execution_latency_ms",
                   "book_freshness_status", "execution_latency_basis"):
        assert by_name[column] is None, column


# ── the market-data sweep ────────────────────────────────────────────


class FakeClient:
    """Answers the three reads. Counts them, so pacing is observable."""

    def __init__(self, *, instruments=None, book=None, book_status=200):
        self.instruments = instruments
        self.book = book
        self.book_status = book_status
        self.calls = []

    def read(self, name, symbol=""):
        self.calls.append((name, symbol))
        if name == "instruments":
            rows = [] if self.instruments is None else [self.instruments]
            return {"read": name, "status": 200,
                    "body": {"instruments": rows}, "ms": 12.0}
        return {"read": name, "status": self.book_status,
                "body": self.book if self.book_status == 200 else None,
                "ms": 8.0, "requestId": "r1"}


def test_the_sweep_bootstraps_an_instrument_then_stores_its_book():
    client = FakeClient(instruments=INSTRUMENT, book=BOOK)
    store = ib.BookStore()
    stats = md.sweep_once(client, store, ["X"])
    assert stats["read"] == 1 and stats["stored"] == 1
    assert store.current("X")["BOOK_SHA"]
    assert client.calls == [("instruments", "X"), ("book", "X")]

    # SECOND SWEEP: refdata is not re-read. §6 says not to make a new
    # request for something already held.
    md.sweep_once(client, store, ["X"])
    assert client.calls.count(("instruments", "X")) == 1


def test_a_symbol_the_venue_does_not_list_is_counted_not_crashed():
    client = FakeClient(instruments=None, book=BOOK)
    stats = md.sweep_once(client, ib.BookStore(), ["X"])
    assert stats["notListed"] == 1 and stats["read"] == 0
    assert ("book", "X") not in client.calls


def test_a_failed_book_read_leaves_the_previous_book_alone():
    """An unanswered read is not an empty book. The old one keeps its
    own received timestamp and goes STALE on the clock, which is the
    state that stops it being walked."""
    store = ib.BookStore()
    md.sweep_once(FakeClient(instruments=INSTRUMENT, book=BOOK), store, ["X"])
    first = store.current("X")["BETTOR_RECEIVED_TIMESTAMP"]

    stats = md.sweep_once(
        FakeClient(instruments=INSTRUMENT, book_status=503), store, ["X"])
    assert stats["failed"] == 1 and stats["stored"] == 0
    assert store.current("X")["BETTOR_RECEIVED_TIMESTAMP"] == first


def test_the_sweep_is_bounded_by_the_focus_size():
    client = FakeClient(instruments=INSTRUMENT, book=BOOK)
    md.sweep_once(client, ib.BookStore(),
                  ["S%d" % i for i in range(md.MAX_INSTRUMENTS + 5)])
    assert len([c for c in client.calls if c[0] == "book"]) \
        == md.MAX_INSTRUMENTS


def test_the_mechanism_is_named_a_poll_because_that_is_what_it_is():
    """§6 says to use the venue's documented persistent mechanism WHERE
    VERIFIED. It is not verified, so this is not called a stream."""
    assert pmx.MARKET_DATA_MECHANISM == "REST_POLL_MAINTAINED_IN_MEMORY"
    assert pmx.STREAM_TARGET == "NOT_IDENTIFIED"


# ── the loop is registered, and registered before its reader ─────────


def test_the_market_data_loop_runs_before_the_lane_that_reads_it():
    """Read from the registry's SOURCE, not by importing it.

    Importing `workers.all` drags in the whole worker tree -- webpush,
    the venue SDK, the notification stack -- and a missing unrelated
    dependency would turn this registration check into a red herring
    about something else entirely.
    """
    import re
    from pathlib import Path

    src = Path(worker.__file__).with_name("all.py").read_text()
    registered = re.findall(r'\(\s*"([a-z_0-9]+)"\s*,\s*[a-z_0-9.]+\)', src)
    assert "institutional_md" in registered, registered
    assert registered.index("institutional_md") < \
        registered.index("shadow_experimental")


def test_the_two_regimes_are_different_words_and_stay_apart():
    assert xstore.REGIME_DIRECT != xstore.REGIME_BRIDGE
    assert worker.DIRECT == xstore.REGIME_DIRECT == ib.EVIDENCE_ENVIRONMENT
