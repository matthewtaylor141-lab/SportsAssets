"""P5 C1 + C2 IN THE DECIDING PROCESS: THE API'S STREAM AND IDENTITY MAPPER.

  §1  C1 -- `institutional_api_stream.identity_mapper` answers EXACT only for
      an exact institutional_contract_map mapping on refdata this process
      holds; no record, a NO / short leg, an unknown intent, another symbol,
      a payout that is not $1.00 -> None (fail closed)
  §2  C2 -- start() is `institutional_stream.start_default`: flag off ->
      DISABLED_BY_CONFIGURATION, flag on without PMX_* ->
      CREDENTIAL_REFUSED_BY_IDENTITY_GUARD (MARKET_DATA_CREDENTIAL_ABSENT);
      neither starts a thread, a task or a venue call
  §3  flag on + PMX_*: against the in-process gRPC venue the API process
      bootstraps refdata with the workers' code, subscribes, holds a current
      book, maps EXACT, and the decision path observes it; a symbol the
      decision path asks about is bootstrapped; an unlisted symbol is not
      re-read inside its backoff; stop() ends the task and the stream
  §4  the API lifespan installs the mapper, starts and stops the stream
"""
from __future__ import annotations

import asyncio
import copy
import inspect
import time

import grpc
import pytest

from sportsassets import institutional_api_stream as IAS
from sportsassets import institutional_stream as IS
from sportsassets import live_book_evidence as LBE

try:
    from tests.test_institutional_contract_map import AEC, SLUG
    from tests.test_institutional_md_grpc_transport import (
        GOOD, Wait, ack, book, until, venue)  # noqa: F401  (fixture)
except ImportError:                                             # pragma: no cover
    from test_institutional_contract_map import AEC, SLUG  # type: ignore
    from test_institutional_md_grpc_transport import (  # type: ignore
        GOOD, Wait, ack, book, until, venue)  # noqa: F401

PMX_ENV = {"PMX_CLIENT_ID": "NOT-REAL-client", "PMX_PARTICIPANT_ID":
           "NOT-REAL-participant", "PMX_KEY_ID": "NOT-REAL-kid",
           "PMX_PRIVATE_KEY_B64": "NOT-REAL-key"}
ON_ENV = dict(PMX_ENV, INSTITUTIONAL_MD_STREAM="on")
LONG = "ORDER_INTENT_BUY_LONG"
SHORT = "ORDER_INTENT_BUY_SHORT"


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    IS.reset()
    IAS.reset()
    monkeypatch.setattr(LBE, "IDENTITY_MAPPER", None)
    monkeypatch.setattr(LBE, "READER", None)
    yield
    IS.reset()
    IAS.reset()


def hold(symbol=SLUG, record=AEC):
    IAS.REFDATA[symbol] = {"record": record, "at": time.time()}


# ── §1 C1 ───────────────────────────────────────────────────────────────

def test_the_mapper_is_exact_or_none():
    assert IAS.identity_mapper(SLUG, LONG) is None            # no record
    hold()
    m = IAS.identity_mapper(SLUG, LONG)
    assert m["status"] == "EXACT" and m["symbol"] == SLUG
    assert (m["price_scale"], m["qty_scale"]) == (1000, 100)
    assert m["price_transform"] == "IDENTITY" and m["evidence"]
    assert IAS.identity_mapper(SLUG, "yes")["status"] == "EXACT"
    for intent in (SHORT, "no", "", None, "ORDER_INTENT_UNKNOWN"):
        assert IAS.identity_mapper(SLUG, intent) is None, intent
    assert IAS.identity_mapper("", LONG) is None
    # a record for another instrument under this key: not one contract
    other = copy.deepcopy(AEC)
    other["symbol"] = "aec-mlb-nyy-bos-2026-10-04"
    hold(record=other)
    assert IAS.identity_mapper(SLUG, LONG) is None
    # a payout that is not priceScale price units
    bad = copy.deepcopy(AEC)
    bad["eventAttributes"]["payoutValue"] = "100"
    hold(record=bad)
    assert IAS.identity_mapper(SLUG, LONG) is None


def test_a_missing_record_is_requested_only_while_the_stream_runs():
    assert IAS.identity_mapper(SLUG, LONG) is None
    assert IAS.describe()["requested"] == []                   # not running


# ── §2 C2 off / refused ─────────────────────────────────────────────────

async def test_flag_off_is_disabled_by_configuration_and_starts_nothing():
    n = len(asyncio.all_tasks())
    out = await IAS.start(None, env=dict(PMX_ENV))
    assert out["started"] is False and out["state"] == IS.S_DISABLED
    assert IS.BOOKS.state == IS.S_DISABLED
    assert IS._TRANSPORT is None and IAS._STATE["task"] is None
    assert len(asyncio.all_tasks()) == n
    assert IAS.running() is False


async def test_flag_on_without_pmx_is_the_identity_guards_refusal():
    out = await IAS.start(None, env={"INSTITUTIONAL_MD_STREAM": "on"})
    assert out["started"] is False
    assert out["state"] == IS.S_CREDENTIAL
    assert out["why"] == "MARKET_DATA_CREDENTIAL_ABSENT"
    assert IS._TRANSPORT is None and IAS._STATE["task"] is None


# ── §3 C2 on ────────────────────────────────────────────────────────────

def grpc_factory(venue):
    def factory(books, token_fn):
        return IS.GrpcBidiTransport(
            books, token_fn, target=venue.target,
            channel_factory=lambda t: grpc.insecure_channel(t),
            sleep=lambda s: time.sleep(min(s, 0.05)))
    return factory


async def until_async(pred, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        await asyncio.sleep(0.02)
    raise AssertionError("condition not reached")


async def test_the_api_stream_bootstraps_subscribes_maps_and_stops(venue):
    gate = Wait()
    venue.scripts = [[ack((SLUG,)), lambda: book(symbol=SLUG), gate]]
    reads = []

    def bootstrap(client, symbol):                 # the refdata read
        reads.append(symbol)
        return {"record": AEC if symbol == SLUG else None}
    out = await IAS.start(None, env=ON_ENV, focus=[SLUG],
                          bootstrap=bootstrap,
                          transport_factory=grpc_factory(venue),
                          token_fn=lambda: GOOD,
                          available=lambda: (True, None))
    try:
        assert out["started"] is True
        await until_async(lambda: IS.current(SLUG)["ok"])
        assert reads == [SLUG]
        assert IS.BOOKS.wanted() == [SLUG]
        # C1 now answers EXACT, and the decision path observes the book
        assert IAS.identity_mapper(SLUG, LONG)["status"] == "EXACT"
        LBE.IDENTITY_MAPPER = IAS.identity_mapper
        so = LBE.observe({}, {"us_market_slug": SLUG, "side": LONG},
                         now=time.time())
        assert so["observation"] is not None
        assert so["observation"]["market_state"] == "INSTRUMENT_STATE_OPEN"
        assert so["observation"]["market_data"]["offers"][0]["px"][
            "value"] == "0.47"
        # a symbol the decision path asks about is bootstrapped (and an
        # unlisted one is not re-read inside its backoff)
        assert IAS.identity_mapper("aec-nfl-x-y-2026-10-04", LONG) is None
        assert IAS.describe()["requested"] == ["aec-nfl-x-y-2026-10-04"]
        await until_async(lambda: "aec-nfl-x-y-2026-10-04" in reads)
        r = await IAS.refresh_once(bootstrap=bootstrap, symbols=[SLUG])
        assert r["bootstrapped"] == 0
        assert reads.count("aec-nfl-x-y-2026-10-04") == 1
    finally:
        await IAS.stop()
        gate.set()
    assert IAS._STATE["task"] is None
    assert IS.BOOKS.state == IS.S_STOPPED
    assert IAS.running() is False


async def test_refdata_is_reread_only_when_due():
    reads = []

    def bootstrap(client, symbol):
        reads.append(symbol)
        return {"record": AEC}
    IS.BOOKS.set_state(IS.S_IDLE, "test")
    t0 = time.time()
    await IAS.refresh_once(bootstrap=bootstrap, symbols=[SLUG], now=t0)
    await IAS.refresh_once(bootstrap=bootstrap, symbols=[SLUG], now=t0 + 60)
    assert reads == [SLUG]
    await IAS.refresh_once(bootstrap=bootstrap, symbols=[SLUG],
                           now=t0 + IAS.REFDATA_REFRESH_S + 1)
    assert reads == [SLUG, SLUG]
    assert IS.BOOKS.wanted() == [SLUG]


# ── §4 the lifespan ─────────────────────────────────────────────────────

def test_the_api_lifespan_installs_the_mapper_starts_and_stops_the_stream():
    from sportsassets.api import app as app_mod
    src = inspect.getsource(app_mod.lifespan.__wrapped__) \
        if hasattr(app_mod.lifespan, "__wrapped__") else \
        inspect.getsource(app_mod.lifespan)
    assert "_LBE.IDENTITY_MAPPER = _IAS.identity_mapper" in src
    i, j = src.index("await _IAS.start("), src.index("yield")
    assert i < j
    k = src.index("finally:", j)
    assert k < src.index("await _IAS.stop()")
    # start() is the workers' own start path, nothing else
    s = inspect.getsource(IAS.start)
    assert "IS.start_default(" in s and "secure_channel" not in s
