"""poll_wallet end-to-end with fakes.

Regression for 2026-08-21: the batch pre-dedupe wrote ev.dedupe_key()
— but dedupe_key is a @property, so every wallet with events raised
'str' object is not callable and Path B went fully dark. The chain
listener masked it for the sports whales; the crypto copy leg starved
(hourly-reconciler rows only, 50-70 min stale, 100% refused at the
90s freshness bar). This test drives the REAL poll_wallet against a
faked HTTP layer and pool, so any future shape break in the hot path
throws here first.
"""

from __future__ import annotations

import asyncio

import pytest

from sportsassets.ingestion import poller as poller_mod
from sportsassets.ingestion.poller import Poller


RAW_TRADE = {
    "transactionHash": "0x" + "ab" * 32,
    "asset": "123456789",
    "side": "buy",
    "size": "100.5",
    "price": "0.42",
    "timestamp": 1_760_000_000,
    "conditionId": None,
    "outcome": "Yes",
    "outcomeIndex": 0,
    "title": "Will BTC reach 130k",
    "slug": "will-btc-reach-130k",
    "eventSlug": "btc-130k",
}


class _FakeResp:
    def raise_for_status(self) -> None:
        pass

    def json(self) -> list[dict]:
        return [RAW_TRADE]


class _FakePool:
    def __init__(self) -> None:
        self.any_queries: list[list[str]] = []

    async def fetch(self, sql: str, *args):
        # The batch pre-dedupe ANY() probe: record the keys it was
        # handed and report none seen, so ingest proceeds.
        self.any_queries.append(list(args[0]) if args else [])
        return []

    async def fetchval(self, sql: str, *args):
        return None


@pytest.fixture()
def fake_env(monkeypatch):
    pool = _FakePool()

    async def fake_get_pool():
        return pool

    async def fake_polite_get(http, path, params=None):
        return _FakeResp()

    ingested: list = []

    async def fake_ingest(ev):
        ingested.append(ev)
        # (id, was_new). The id alone stopped meaning "new" when
        # ingest_trade switched to ON CONFLICT DO UPDATE.
        return 42, True

    monkeypatch.setattr(poller_mod, "get_pool", fake_get_pool)
    monkeypatch.setattr(
        "sportsassets.ratelimit.polite_get", fake_polite_get)
    monkeypatch.setattr(poller_mod, "ingest_trade_result", fake_ingest)
    return pool, ingested


def test_poll_wallet_ingests_without_raising(fake_env):
    pool, ingested = fake_env
    p = Poller.__new__(Poller)  # skip __init__ (needs settings/http)
    p._http = None
    p.last_lag_s = None
    new = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        p.poll_wallet({"id": 28, "address": "0xf705fa04520139...", "username": "0xf705fa04"})
    )
    assert new == 1
    assert len(ingested) == 1
    # The pre-dedupe probe must have been handed STRING keys (the
    # property's value), not bound methods.
    assert pool.any_queries and all(
        isinstance(k, str) and k for k in pool.any_queries[0])


def test_dedupe_key_is_a_property_not_a_method():
    ev = poller_mod.parse_data_api_trade(RAW_TRADE, 28, "0xf705fa04")
    key = ev.dedupe_key
    assert isinstance(key, str) and key
    assert not callable(key)
