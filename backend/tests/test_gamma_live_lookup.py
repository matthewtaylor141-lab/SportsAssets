"""lookup_token_live — on-miss gamma fetch for chain-detected trades.

Regression context (2026-08-22): the metadata refresher sweeps sports
markets only, so chain-detected trades in CRYPTO markets never
enriched; with chain now winning every detection race the Data-API
duplicate that used to carry the slug is dedupe-dropped, so a new
market's slug would never arrive at all and the Kalshi crypto leg
refused every fresh candidate as 'no-asset'. lookup_token_live closes
the gap: cache/DB first, then ONE gamma fetch by clob token id,
persisted through the normal upsert; dead tokens are negative-cached.
"""

from __future__ import annotations

import asyncio

import pytest

from sportsassets import gamma


RAW_MARKET = {
    "conditionId": "0xc0ffee",
    "question": "Will Bitcoin reach $130,000 by December 31?",
    "title": "Will Bitcoin reach $130,000 by December 31?",
    "slug": "will-bitcoin-reach-130000-by-december-31",
    "clobTokenIds": '["111", "222"]',
    "outcomes": '["Yes", "No"]',
    "closed": False,
}


@pytest.fixture(autouse=True)
def clean_state():
    gamma._token_miss_cache.clear()
    saved = gamma._live_client
    gamma._live_client = None
    yield
    gamma._live_client = None
    gamma._token_miss_cache.clear()
    gamma._live_client = saved


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def fetch_markets(self, params):
        self.calls.append(params)
        return self.payload


def test_miss_fetches_persists_and_returns(monkeypatch):
    lookups = {"n": 0}
    mapping = {"token_id": "111", "slug": RAW_MARKET["slug"],
               "title": RAW_MARKET["title"], "condition_id": "0xc0ffee"}

    async def fake_lookup(tid):
        lookups["n"] += 1
        return None if lookups["n"] == 1 else mapping

    upserts = []

    async def fake_upsert(meta):
        upserts.append(meta)

    client = _FakeClient([RAW_MARKET])
    monkeypatch.setattr(gamma, "lookup_token", fake_lookup)
    monkeypatch.setattr(gamma, "upsert_market", fake_upsert)
    gamma._live_client = client

    out = _run(gamma.lookup_token_live("111"))
    assert out == mapping
    assert client.calls == [{"clob_token_ids": "111", "limit": 1}]
    assert len(upserts) == 1
    assert upserts[0]["condition_id"] == "0xc0ffee"
    assert upserts[0]["slug"] == RAW_MARKET["slug"]


def test_cache_hit_never_fetches(monkeypatch):
    mapping = {"token_id": "111", "slug": "s"}

    async def fake_lookup(tid):
        return mapping

    client = _FakeClient([RAW_MARKET])
    monkeypatch.setattr(gamma, "lookup_token", fake_lookup)
    gamma._live_client = client

    assert _run(gamma.lookup_token_live("111")) == mapping
    assert client.calls == []


def test_dead_token_negative_cached(monkeypatch):
    async def fake_lookup(tid):
        return None

    client = _FakeClient([])  # gamma knows nothing about this token
    monkeypatch.setattr(gamma, "lookup_token", fake_lookup)
    gamma._live_client = client

    assert _run(gamma.lookup_token_live("dead")) is None
    assert _run(gamma.lookup_token_live("dead")) is None
    assert len(client.calls) == 1, "second miss must hit the negative cache"


def test_fetch_error_is_swallowed(monkeypatch):
    async def fake_lookup(tid):
        return None

    class _Boom:
        async def fetch_markets(self, params):
            raise RuntimeError("gamma down")

    monkeypatch.setattr(gamma, "lookup_token", fake_lookup)
    gamma._live_client = _Boom()

    assert _run(gamma.lookup_token_live("111")) is None
