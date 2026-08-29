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


# ── fleet round 14 (major): one hostile row costs ONE row ───────────
HOSTILE_INF = dict(RAW_TRADE, transactionHash="0x" + "cd" * 32,
                   size=float("inf"))
HOSTILE_TS = dict(RAW_TRADE, transactionHash="0x" + "ef" * 32,
                  timestamp=float("inf"))
HOSTILE_NAN = dict(RAW_TRADE, transactionHash="0x" + "aa" * 32,
                   price=float("nan"))


class _MixedResp:
    """One healthy row buried between hostile ones."""

    def raise_for_status(self) -> None:
        pass

    def json(self) -> list[dict]:
        return [HOSTILE_INF, HOSTILE_TS, RAW_TRADE, HOSTILE_NAN]


def test_one_hostile_row_never_kills_the_wallet_poll(fake_env, monkeypatch):
    """fleet r14 (major): a single Infinity row aborted the ENTIRE
    poll_wallet batch at the upfront dedupe-key build — the wallet's
    poll carrier (which every S1 abstention and the venue_seen_at
    stamp lean on) died on every cycle, below the alert threshold
    because the failure counter resets on the next wallet's success.
    Validity now refuses non-finite values BEFORE the key, the parse
    is contained per row, and the healthy fill still ingests."""
    pool, ingested = fake_env

    async def mixed_get(http, path, params=None):
        return _MixedResp()

    monkeypatch.setattr("sportsassets.ratelimit.polite_get", mixed_get)
    p = Poller.__new__(Poller)
    p._http = None
    p.last_lag_s = None
    new = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        p.poll_wallet({"id": 28, "address": "0xf705", "username": "0xf7"})
    )
    assert new == 1, "the healthy row ingests despite three hostile ones"
    assert len(ingested) == 1
    assert ingested[0].tx_hash == RAW_TRADE["transactionHash"]


# ── fleet round 15: stored-as-judged + ingest containment ───────────
def test_side_is_stored_as_the_gate_judged_it():
    """r15 (major): side='buy ' passed the gate on .upper().strip()
    but the INSERT bound the raw 'BUY ' and the side CHECK constraint
    killed the batch — parse now normalizes what it stores."""
    raw = dict(RAW_TRADE, side="buy ", transactionHash=" 0x" + "11" * 32)
    ev = poller_mod.parse_data_api_trade(raw, 28, "x")
    assert ev.side == "BUY"
    assert ev.tx_hash == "0x" + "11" * 32, "tx stored stripped too"


def test_ingest_failure_costs_one_row_not_the_batch(fake_env, monkeypatch):
    """r15 (major): a gate-passing row that fails INSIDE ingest (DB
    constraint / overflow / datetime) must not abort the wallet's
    poll batch — the round-14 promise, one call deeper."""
    pool, ingested = fake_env
    poison = dict(RAW_TRADE, transactionHash="0x" + "99" * 32)

    class _TwoRows:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> list[dict]:
            return [poison, RAW_TRADE]

    async def two_get(http, path, params=None):
        return _TwoRows()

    async def picky_ingest(ev):
        if ev.tx_hash == poison["transactionHash"]:
            raise RuntimeError("constraint violated inside ingest")
        ingested.append(ev)
        return 42, True

    monkeypatch.setattr("sportsassets.ratelimit.polite_get", two_get)
    monkeypatch.setattr(poller_mod, "ingest_trade_result", picky_ingest)
    p = Poller.__new__(Poller)
    p._http = None
    p.last_lag_s = None
    new = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        p.poll_wallet({"id": 28, "address": "0xf705", "username": "0xf7"})
    )
    assert new == 1, "the healthy row still ingests"
    assert len(ingested) == 1
    assert ingested[0].tx_hash == RAW_TRADE["transactionHash"]


def test_non_list_body_is_a_poll_failure_not_an_empty_success(
        fake_env, monkeypatch):
    """fleet r23 (major): an HTTP-200 body that is valid JSON but not
    a list (error dict, pagination envelope, bare string) iterated
    anyway — every element died in the per-row containment and the
    cycle returned 0 as a SUCCESSFUL empty poll, resetting the
    failure counter and heartbeating 'ok'. The 'Path B degraded'
    alert (and the ops monitor reading the same heartbeat row) was
    structurally unreachable while the carrier was dead. The poller
    now fails the cycle — mirroring the reconciler's round-19
    classification of the identical body — so run()'s failure
    accounting counts it and the alert can fire."""
    class _ErrResp:
        def __init__(self, body):
            self._body = body

        def raise_for_status(self):
            pass

        def json(self):
            return self._body

    for body in ({"error": "upstream", "code": 503},
                 {"data": [RAW_TRADE]},
                 "service unavailable"):
        async def fake_get(http, path, params=None, _b=body):
            return _ErrResp(_b)

        monkeypatch.setattr("sportsassets.ratelimit.polite_get", fake_get)
        p = Poller.__new__(Poller)
        p._http = None
        p.last_lag_s = None
        loop = asyncio.get_event_loop_policy().new_event_loop()
        with pytest.raises(ValueError):
            loop.run_until_complete(p.poll_wallet(
                {"id": 1, "address": "0xw", "username": "w"}))
        loop.close()


def test_all_junk_list_page_is_also_a_poll_failure(fake_env, monkeypatch):
    """The round-23 shape one level down: a LIST page whose every
    element is unusable is the same dead carrier as a non-list body —
    it must fail the cycle, while a mixed page still ingests its
    healthy rows per the round-14 one-row-costs-one-row design."""
    class _R:
        def __init__(self, body):
            self._b = body

        def raise_for_status(self):
            pass

        def json(self):
            return self._b

    async def all_junk(http, path, params=None):
        return _R([None, {"size": "inf"}, "garbage", {}])

    monkeypatch.setattr("sportsassets.ratelimit.polite_get", all_junk)
    p = Poller.__new__(Poller)
    p._http = None
    p.last_lag_s = None
    loop = asyncio.get_event_loop_policy().new_event_loop()
    with pytest.raises(ValueError):
        loop.run_until_complete(p.poll_wallet(
            {"id": 1, "address": "0xw", "username": "w"}))
    loop.close()

    async def mixed(http, path, params=None):
        return _R([None, RAW_TRADE])

    monkeypatch.setattr("sportsassets.ratelimit.polite_get", mixed)
    _, ingested = fake_env
    p2 = Poller.__new__(Poller)
    p2._http = None
    p2.last_lag_s = None
    loop = asyncio.get_event_loop_policy().new_event_loop()
    new = loop.run_until_complete(p2.poll_wallet(
        {"id": 1, "address": "0xw", "username": "w"}))
    loop.close()
    assert new == 1 and len(ingested) == 1, \
        "one junk row still costs one row, never the page"


def test_total_ingest_loss_fails_the_cycle(fake_env, monkeypatch):
    """fleet r24 (minor): the round-23 arithmetic one layer down — a
    cycle whose EVERY attempted row failed inside ingest (constraint
    drift; the 2026-08-21 side-CHECK incident is the documented
    precedent) still returned success and reset the failure counter.
    Total ingest loss now fails the cycle; a mixed outcome stays
    per-row (round 15)."""
    class _R2:
        def raise_for_status(self):
            pass

        def json(self):
            return [RAW_TRADE,
                    dict(RAW_TRADE, asset="99", transactionHash="0x" + "cc" * 32)]

    async def two_rows(http, path, params=None):
        return _R2()

    monkeypatch.setattr("sportsassets.ratelimit.polite_get", two_rows)

    async def always_boom(ev):
        raise RuntimeError("constraint drift")

    monkeypatch.setattr(poller_mod, "ingest_trade_result", always_boom)
    p = Poller.__new__(Poller)
    p._http = None
    p.last_lag_s = None
    loop = asyncio.get_event_loop_policy().new_event_loop()
    with pytest.raises(ValueError):
        loop.run_until_complete(p.poll_wallet(
            {"id": 1, "address": "0xw", "username": "w"}))
    loop.close()

    booms = {"n": 0}

    async def boom_once(ev):
        if booms["n"] == 0:
            booms["n"] += 1
            raise RuntimeError("constraint drift")
        return 7, True

    monkeypatch.setattr(poller_mod, "ingest_trade_result", boom_once)
    p2 = Poller.__new__(Poller)
    p2._http = None
    p2.last_lag_s = None
    loop = asyncio.get_event_loop_policy().new_event_loop()
    new = loop.run_until_complete(p2.poll_wallet(
        {"id": 1, "address": "0xw", "username": "w"}))
    loop.close()
    assert new == 1, "a mixed outcome stays per-row — never the cycle"


def _empty_page_pool(has_fills):
    class _P:
        def __init__(self):
            self.probes = []

        async def fetchval(self, sql, *a, timeout=None):
            self.probes.append(a)
            return 1 if has_fills else None
    return _P()


def test_empty_page_for_a_whale_with_known_fills_fails_the_cycle(monkeypatch):
    """fleet r36 (major): a 200-[] page slipped past the round-23
    guards (non-list; all-junk needs a truthy page) and returned 0 as
    a SUCCESSFUL poll — the failure counter reset, the heartbeat
    stayed 'ok', and the Path-B-degraded alert was structurally
    unreachable while the primary carrier was dead. The request asks
    for the wallet's most recent 100 trades, so a whale with ANY
    known fill can never legitimately serve [] — the round-25
    cold-index shape the backfill already refuses."""
    import sportsassets.ingestion.poller as pl

    pool = _empty_page_pool(has_fills=True)

    async def fake_get_pool():
        return pool

    monkeypatch.setattr(pl, "get_pool", fake_get_pool)
    p = pl.Poller.__new__(pl.Poller)
    p._http = None

    class _R:
        def raise_for_status(self):
            pass

        def json(self):
            return []

    async def fake_polite_get(http, path, params=None):
        return _R()

    import sportsassets.ratelimit as rl
    monkeypatch.setattr(rl, "polite_get", fake_polite_get)
    import asyncio as aio
    import pytest as pt
    with pt.raises(ValueError, match="cold venue index"):
        aio.run(p.poll_wallet({"id": 7, "address": "0xw",
                               "username": "w"}))
    assert pool.probes, "the known-fills probe ran"


def test_empty_page_for_a_fresh_whale_is_a_normal_poll(monkeypatch):
    """A whale with no fills yet (fresh cohort wallet) legitimately
    serves an empty page — that stays a successful 0, never a raise."""
    import sportsassets.ingestion.poller as pl

    pool = _empty_page_pool(has_fills=False)

    async def fake_get_pool():
        return pool

    monkeypatch.setattr(pl, "get_pool", fake_get_pool)
    p = pl.Poller.__new__(pl.Poller)
    p._http = None

    class _R:
        def raise_for_status(self):
            pass

        def json(self):
            return []

    async def fake_polite_get(http, path, params=None):
        return _R()

    import sportsassets.ratelimit as rl
    monkeypatch.setattr(rl, "polite_get", fake_polite_get)
    import asyncio as aio
    assert aio.run(p.poll_wallet({"id": 7, "address": "0xw",
                                  "username": "w"})) == 0
