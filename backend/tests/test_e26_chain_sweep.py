"""E26 (FILL lane 26, 2026-09-09): the chain listener sweeps the logs the
socket did not deliver.

THE ROWS (hard2/book_1177_1740.txt 104-107): at 17:04:57Z his 12,960-share
Rybakina buy settled in more than one transaction; the listener handled
one (2,880 @0.7105, source chain, seen 17:04:56) and the other two
(4,869 and 5,211 @0.70) reached the table only as poll rows at 17:08:24
-- 207 s later, the venue's publication lag -- so the book reduced 302
at 17:05:29 (order 7213) and flattened 851 + 654 at 17:08:34-17:09:49
(7226 / 7228) on ONE order of his (rows 14-19). fvv_1743.txt row 10
(book 1177): old_dropped_sh 2,880.0 -- only the 2,880 twin collapsed
under the (whale, tx, asset, side) window, so 4,869 and 5,211 sit under
tx keys holding no chain row: two transactions the listener never
handled (no chain row, no s1 row, no refusal in evidence).

THE RULE. A side task started from run() after a successful subscribe
sweeps eth_getLogs over [cursor + 1, min(tip - CHAIN_SWEEP_CONFIRM_BLOCKS,
cursor + CHAIN_SWEEP_MAX_BLOCKS)] every CHAIN_SWEEP_S with the listener's
OWN filter and hands a v3 log whose tx the socket did not handle to
`_handle_v3` DIRECTLY (never through `_handle_log`'s observe calls: the
emitter and the shadow see only what the socket delivered) and a legacy
/ v2 log to `_handle_log` as `backfill` does. Every fake below is the
suite's own (test_chain_v3.py's `_ts` / `_erc20` shapes).
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import pathlib
import re
import subprocess
import sys
import time

import pytest

from sportsassets.ingestion import chain as ch
from sportsassets.ingestion.chain import (
    ERC20_TRANSFER_TOPIC,
    FILL_V3_TOPIC,
    TRANSFER_SINGLE_TOPIC,
    BlockTimestampCache,
    ChainListener,
)

ROOT = pathlib.Path(__file__).resolve().parents[2]
BACKEND = pathlib.Path(__file__).resolve().parents[1]

WHALE = "0x2005d16a84ceefa912d4e380cd32e7ff827875ea"   # RN1 (wlog_6m_1734.txt 1036)
OTHER = "0x1ffb493b0042ded0fc2871f4a13590e59d830554"
EXCH = "0xe111180000d2663c0091e4f400237545b87b996b"
TOKEN = 4792829448                                     # the 'other' leg, book 104-107
CID = "0x5628760cc56964480883e0f1059df18b418c4de1852c90a6271db30476baf90f"
TS = 1788973497                                        # 2026-09-09 17:04:57Z
TX1, TX2, TX3, TX4 = ("0x" + "a1" * 32, "0x" + "a2" * 32, "0x" + "a3" * 32,
                      "0x" + "a4" * 32)
CURSOR, TIP = 1000, 1010                               # span [1001, 1008] at confirm 2
BLK = hex(1004)
U = 10 ** 6


def _t(addr: str) -> str:
    return "0x" + "0" * 24 + addr[2:]


def _w(v: int) -> str:
    return f"{v:064x}"


def _ts(frm, to, tid, val, tx, blk=BLK):
    return {"topics": [TRANSFER_SINGLE_TOPIC, _t(EXCH), _t(frm), _t(to)],
            "data": "0x" + _w(tid) + _w(val),
            "transactionHash": tx, "blockNumber": blk}


def _erc20(frm, to, val, tx, blk=BLK):
    return {"topics": [ERC20_TRANSFER_TOPIC, _t(frm), _t(to)],
            "data": "0x" + _w(val),
            "transactionHash": tx, "blockNumber": blk}


def _fill_log(owner, tx, blk=BLK):
    """A v3 fill log as the socket (or eth_getLogs) delivers it."""
    return {"address": EXCH,
            "topics": [FILL_V3_TOPIC, "0x" + "9" * 64, _t(owner), _t(EXCH)],
            "data": "0x" + _w(0) + _w(TOKEN) + _w(0) + _w(0) + _w(0) + _w(0),
            "transactionHash": tx, "blockNumber": blk}


def _receipt(tx, shares, usdc):
    return {"logs": [_ts(EXCH, WHALE, TOKEN, shares * U, tx),
                     _erc20(WHALE, EXCH, usdc, tx)]}


# the three fills of 17:04:57: 2,880 @0.7105 the socket handled (TX1);
# 4,869 @0.70 = 3,408.30 and 5,211 @0.70 = 3,647.70 it did not
RECEIPTS = {TX1: _receipt(TX1, 2880, 2_046_240_000),
            TX2: _receipt(TX2, 4869, 3_408_300_000),
            TX3: _receipt(TX3, 5211, 3_647_700_000)}
LOGS = [_fill_log(WHALE, TX1), _fill_log(WHALE, TX2), _fill_log(WHALE, TX3)]


class _Resp:
    def __init__(self, body, status=200):
        self._body, self.status_code = body, status
        self.text = json.dumps(body)

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _Rpc:
    """The provider: eth_blockNumber, eth_getLogs, eth_getTransactionReceipt,
    eth_getBlockByNumber. Records every call; `during_get_logs` runs inside
    the getLogs await (a socket log landing mid-sweep)."""

    def __init__(self, tip=TIP, logs=None, receipts=None, get_logs=None):
        self.tip, self.logs = tip, list(LOGS if logs is None else logs)
        self.receipts = dict(RECEIPTS if receipts is None else receipts)
        self.get_logs = get_logs          # override: callable(params) -> body / raise
        self.calls: list[dict] = []
        self.during_get_logs = None

    async def post(self, url, json=None):
        req = json or {}
        self.calls.append(req)
        m = req.get("method")
        if m == "eth_blockNumber":
            return _Resp({"result": hex(self.tip)})
        if m == "eth_getBlockByNumber":
            return _Resp({"result": {"timestamp": hex(TS)}})
        if m == "eth_getTransactionReceipt":
            return _Resp({"result": self.receipts.get(req["params"][0], {})})
        if m == "eth_getLogs":
            if self.during_get_logs is not None:
                await self.during_get_logs()
            if self.get_logs is not None:
                return self.get_logs(req["params"][0])
            return _Resp({"result": self.logs})
        raise AssertionError(m)

    def receipts_fetched(self):
        return [c["params"][0] for c in self.calls
                if c.get("method") == "eth_getTransactionReceipt"]

    def get_logs_calls(self):
        return [c["params"][0] for c in self.calls if c.get("method") == "eth_getLogs"]


class _Pool:
    """ingestion_state's cursor (GREATEST on save), the (tx, whale, asset,
    chain/s1) pre-probe over the rows this fake holds."""

    def __init__(self, cursor=CURSOR, rows=()):
        self.cursor = cursor
        self.rows = [tuple(r) for r in rows]     # (tx, whale_id, asset, source)
        self.saves: list[int] = []

    async def fetchval(self, sql, *a):
        assert "chain.last_block" in sql
        return None if self.cursor is None else json.dumps(self.cursor)

    async def execute(self, sql, *a):
        assert "GREATEST" in sql
        self.saves.append(int(a[0]))
        self.cursor = max(self.cursor or 0, int(a[0]))

    async def fetchrow(self, sql, *a):
        assert "source IN ('chain', 's1')" in sql
        tx, whale_id, asset = a
        for r in self.rows:
            if r[0] == tx and r[1] == whale_id and r[2] == asset and r[3] in ("chain", "s1"):
                return {"?column?": 1}
        return None


def _listener(rpc, pool, monkeypatch, v3_seen=None, subscribed=True, throttled=False):
    """A listener built without settings, the socket's state as given."""
    lst = ChainListener.__new__(ChainListener)
    lst._http = rpc
    lst._http_url = "http://rpc"
    lst._addresses = [EXCH]
    lst._topics = [[ch.order_filled_topic(), ch.ORDER_FILLED_V2_TOPIC, FILL_V3_TOPIC]]
    lst._blocks = BlockTimestampCache(rpc, "http://rpc")
    lst._roster = {WHALE: {"id": 1, "username": "RN1", "address": WHALE}}
    lst.last_event_at = time.time()
    lst.events_seen = lst.decoded = lst.ingested = 0
    lst.last_lag_s = None
    lst._v3_seen = dict(v3_seen or {})
    lst._subscribed = subscribed
    lst._last_throttled = throttled
    ingested: list = []

    async def fake_get_pool():
        return pool

    async def fake_ingest(ev):
        ingested.append(ev)
        pool.rows.append((ev.tx_hash, ev.whale_id, ev.asset, ev.source))
        return len(ingested), True

    observed = {"emitter": [], "shadow": []}
    monkeypatch.setattr(ch, "get_pool", fake_get_pool)
    monkeypatch.setattr(ch, "ingest_trade_result", fake_ingest)
    monkeypatch.setattr(ch, "emitter_observe", lambda l, e: observed["emitter"].append(e))
    monkeypatch.setattr(ch, "shadow_observe", lambda l, e: observed["shadow"].append(e))
    return lst, ingested, observed


def _run(coro):
    return asyncio.run(coro)


# ── 1. the 17:04:57 shape ─────────────────────────────────────────────

def test_the_two_fills_the_socket_did_not_hand_over_land_within_one_sweep(monkeypatch):
    """Three RN1 BUY fill logs of token 4792829448 in three txs at
    ts 1788973497, the first already in _v3_seen (the socket handled
    2,880 @0.7105): the sweep over [cursor + 1, tip - 2] fetches exactly
    two receipts, lands two chain rows (4,869 and 5,211 @0.70 as the
    receipts price them), counts found 2, saves the swept end as the
    cursor and leaves the seen tx untouched."""
    rpc, pool = _Rpc(), _Pool()
    seen_at = time.time() - 30
    lst, ingested, observed = _listener(rpc, pool, monkeypatch, v3_seen={TX1: seen_at})
    _run(lst._sweep_once())
    # the request is the listener's own filter over the confirmed span
    assert rpc.get_logs_calls() == [{"fromBlock": hex(CURSOR), "toBlock": hex(TIP - 2),
                                     "address": [EXCH], "topics": lst._topics}]
    assert rpc.receipts_fetched() == [TX2, TX3], "one receipt per unseen tx, none for the seen one"
    assert [(e.tx_hash, e.size, e.price, e.side, e.source, e.asset, e.ts_epoch, e.whale_id)
            for e in ingested] == [
        (TX2, 4869.0, 0.7, "BUY", "chain", str(TOKEN), TS, 1),
        (TX3, 5211.0, 0.7, "BUY", "chain", str(TOKEN), TS, 1)]
    st = lst._sweep_state()
    assert (st["runs"], st["found"], st["handled"], st["failed"], st["skipped_throttled"]) == (1, 2, 2, 0, 0)
    assert st["last_span"] == [CURSOR, TIP - 2] and st["last_at"] is not None
    assert pool.cursor == TIP - 2, "the swept end is the cursor (GREATEST)"
    assert pool.saves[-1] == TIP - 2
    assert lst._v3_seen[TX1] == seen_at and TX2 in lst._v3_seen and TX3 in lst._v3_seen
    assert lst.ingested == 2 and lst.decoded == 2
    assert observed == {"emitter": [], "shadow": []}, "a swept log is nobody else's input"
    # the beat carries the block
    assert lst._beat_detail()["sweep"] == st


def test_the_socket_delivering_the_same_tx_after_the_sweep_fetches_nothing(monkeypatch):
    """The sweep set _v3_seen for TX2 / TX3; the socket's late copy of
    the same log is dropped before any RPC -- no second receipt, no
    second row (the per-tx guard is untouched)."""
    rpc, pool = _Rpc(), _Pool()
    lst, ingested, _ = _listener(rpc, pool, monkeypatch, v3_seen={TX1: time.time()})
    _run(lst._sweep_once())
    n = len(rpc.calls)
    _run(lst._handle_log(_fill_log(WHALE, TX2)))
    assert len(rpc.calls) == n and len(ingested) == 2


def test_after_the_poll_twins_exist_two_chain_rows_still_land(monkeypatch):
    """The pre-probe reads chain / s1 rows only: the poll rows 105-107
    under TX1..TX3 do not block the chain rows; a chain row under TX1
    (the socket's) does block a second one."""
    rows = [(TX1, 1, str(TOKEN), "chain"), (TX1, 1, str(TOKEN), "poll"),
            (TX2, 1, str(TOKEN), "poll"), (TX3, 1, str(TOKEN), "poll")]
    rpc, pool = _Rpc(), _Pool(rows=rows)
    lst, ingested, _ = _listener(rpc, pool, monkeypatch)      # _v3_seen empty: a restart
    _run(lst._sweep_once())
    assert rpc.receipts_fetched() == [TX1, TX2, TX3], "after a restart the receipt is read; the pre-probe decides"
    assert [e.tx_hash for e in ingested] == [TX2, TX3], "never a second chain row for (tx, whale, asset)"
    assert lst._v3_skip_preexist == 1
    st = lst._sweep_state()
    assert (st["found"], st["handled"]) == (3, 2)


def test_on_the_scratch_database_his_fills_reads_each_fill_once(monkeypatch):
    """The D1 file's scratch database: the chain row 2,880 @0.7105 and
    the three poll twins @0.70 exist (book 104-107); the sweep lands
    4,869 and 5,211 as chain rows through the REAL pre-probe and cursor
    statements; `his_fills` then reads 2,880 + 4,869 + 5,211 ONCE each
    (the three poll rows in dup_rows), never a double count."""
    from tests.test_d1_fills_dedup import _drop, _scratch
    from sportsassets.workers import mirror_shadow as ms

    async def run():
        admin, c, name = await _scratch()
        try:
            await c.execute("CREATE TABLE ingestion_state (key text PRIMARY KEY, value jsonb)")
            await c.execute("INSERT INTO ingestion_state VALUES ('chain.last_block', to_jsonb($1::bigint))", CURSOR)
            await c.execute(
                "INSERT INTO markets (condition_id, title, slug, event_title, sport) VALUES ($1, $2, $3, $4, $5)",
                CID, "WTA: Zheng vs Rybakina", "aec-wta-qinzhe-eleryb-2026-09-08", "WTA 2026-09-08", "tennis")
            await c.execute(
                "INSERT INTO market_tokens (token_id, condition_id, outcome, outcome_index) "
                "VALUES ($1, $2, 'long', 0), ($3, $2, 'other', 1)", "4423448454", CID, str(TOKEN))
            rows = [("chain", TX1, 2880.0, 0.7105, 0.0), ("poll", TX1, 2880.0, 0.70, 207.0),
                    ("poll", TX2, 4869.0, 0.70, 207.0), ("poll", TX3, 5211.0, 0.70, 207.0)]
            for src, tx, size, px, _det in rows:
                await c.execute(
                    "INSERT INTO trades (whale_id, tx_hash, asset, condition_id, side, size, price, notional, "
                    "ts, source, detected_at) VALUES (1, $1, $2, $3, 'BUY', $4, $5, $6, to_timestamp($7), $8, "
                    "to_timestamp($9))", tx, str(TOKEN), CID, size, px, round(size * px, 6), TS, src, TS + _det)

            async def fake_get_pool():
                return c

            async def fake_ingest(ev):
                tid = await c.fetchval(
                    "INSERT INTO trades (whale_id, tx_hash, asset, condition_id, side, size, price, notional, "
                    "ts, source, detected_at) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, to_timestamp($9), $10, now()) "
                    "RETURNING id", ev.whale_id, ev.tx_hash, ev.asset, CID, ev.side, ev.size, ev.price,
                    round(ev.size * ev.price, 6), ev.ts_epoch, ev.source)
                return tid, True

            monkeypatch.setattr(ch, "get_pool", fake_get_pool)
            monkeypatch.setattr(ch, "ingest_trade_result", fake_ingest)
            rpc = _Rpc()
            lst = ChainListener.__new__(ChainListener)
            lst._http, lst._http_url, lst._addresses = rpc, "http://rpc", [EXCH]
            lst._topics = [[FILL_V3_TOPIC]]
            lst._blocks = BlockTimestampCache(rpc, "http://rpc")
            lst._roster = {WHALE: {"id": 1, "username": "RN1", "address": WHALE}}
            lst.last_event_at, lst.last_lag_s = time.time(), None
            lst.events_seen = lst.decoded = lst.ingested = 0
            lst._v3_seen, lst._subscribed, lst._last_throttled = {TX1: time.time()}, True, False
            await lst._sweep_once()
            assert rpc.receipts_fetched() == [TX2, TX3]
            assert lst._sweep_state()["found"] == 2 and lst._sweep_state()["handled"] == 2
            assert int(json.loads(await c.fetchval(
                "SELECT value FROM ingestion_state WHERE key='chain.last_block'"))) == TIP - 2
            fills = await ms.his_fills(c, "rn1", CID)
            assert [(f["tx_hash"], f["source"], f["size"], f["price"]) for f in fills] == [
                (TX1, "chain", 2880.0, 0.7105), (TX2, "chain", 4869.0, 0.7), (TX3, "chain", 5211.0, 0.7)]
            assert ms.his_fills_dedup() == {"dup_rows": 3, "dup_shares": 12960.0}
            assert await c.fetchval("SELECT count(*) FROM trades WHERE source = 'chain'") == 3
            # a second sweep of the same span, the socket's memory gone (a restart): no fourth row
            lst._v3_seen, lst._sweep_end = {}, None
            await c.execute("UPDATE ingestion_state SET value = to_jsonb($1::bigint) WHERE key='chain.last_block'", CURSOR)
            await lst._sweep_once()
            assert await c.fetchval("SELECT count(*) FROM trades WHERE source = 'chain'") == 3
            assert lst._v3_skip_preexist == 3
        finally:
            await _drop(admin, c, name)
    _run(run())


# ── 2. the observers see only the socket ──────────────────────────────

def test_a_swept_log_never_reaches_the_emitter_or_the_shadow_while_a_socket_log_does(monkeypatch):
    rpc, pool = _Rpc(), _Pool()
    lst, ingested, observed = _listener(rpc, pool, monkeypatch, v3_seen={TX1: time.time()})
    sock = _fill_log(WHALE, TX4)

    async def socket_lands():
        await lst._handle_log(sock)
    rpc.during_get_logs = socket_lands
    _run(lst._sweep_once())
    assert observed["emitter"] == [sock] and observed["shadow"] == [sock]
    assert not getattr(lst, "_shadow_replay", False), "backfill's listener-wide flag is not the sweep's"
    assert [e.tx_hash for e in ingested] == [TX2, TX3]      # TX4's receipt is {} -> refused, as today
    assert lst.v3_refused == 1


def test_the_sweep_source_hands_v3_logs_to_handle_v3_directly_and_uses_no_observer():
    src = inspect.getsource(ChainListener._sweep_once).split('"""', 2)[2]   # the body, past the docstring
    assert "await self._handle_v3(entry)" in src
    assert "await self._handle_log(entry)" in src, "legacy / v2 logs as backfill does"
    for word in ("emitter_observe", "shadow_observe", "_shadow_replay", "backfill("):
        assert word not in src, word
    assert 'if tx in (getattr(self, "_v3_seen", None) or {}):' in src
    i = src.index('_v3_seen')
    assert "await" not in src[src.index("for entry in entries:"):i], "the seen check spends no RPC"
    assert "await self._get_logs(start, end)" in src


# ── 3. fail closed by input ───────────────────────────────────────────

def test_a_get_logs_that_raises_counts_failed_and_leaves_the_cursor(monkeypatch):
    def boom(params):
        return _Resp({"error": {"code": -32000, "message": "range too wide"}})
    rpc, pool = _Rpc(get_logs=boom), _Pool()
    lst, ingested, _ = _listener(rpc, pool, monkeypatch, v3_seen={TX1: time.time()})
    _run(lst._sweep_once())
    st = lst._sweep_state()
    assert (st["runs"], st["found"], st["failed"]) == (1, 0, 1)
    assert rpc.receipts_fetched() == [] and ingested == []
    assert pool.saves == [] and pool.cursor == CURSOR
    assert st["last_span"] is None


def test_a_non_list_body_counts_failed_and_leaves_the_cursor(monkeypatch):
    for body in ({"logs": LOGS}, "junk", None, 7):
        rpc, pool = _Rpc(get_logs=lambda p, _b=body: _Resp({"result": _b})), _Pool()
        lst, ingested, _ = _listener(rpc, pool, monkeypatch, v3_seen={TX1: time.time()})
        _run(lst._sweep_once())
        st = lst._sweep_state()
        if body is None:
            # _get_logs reads `or []`: an absent result is an empty sweep, not a failure
            assert (st["failed"], st["found"]) == (0, 0) and pool.cursor == TIP - 2
            continue
        assert (st["failed"], st["found"]) == (1, 0), body
        assert rpc.receipts_fetched() == [] and ingested == [] and pool.saves == []


def test_an_unreadable_tip_or_cursor_counts_failed_and_makes_no_get_logs(monkeypatch):
    class _NoTip(_Rpc):
        async def post(self, url, json=None):
            if (json or {}).get("method") == "eth_blockNumber":
                self.calls.append(json)
                return _Resp({"error": "throttled"}, status=429)
            return await super().post(url, json=json)
    rpc, pool = _NoTip(), _Pool()
    lst, ingested, _ = _listener(rpc, pool, monkeypatch)
    _run(lst._sweep_once())
    assert lst._sweep_state()["failed"] == 1 and rpc.get_logs_calls() == [] and pool.saves == []
    rpc, pool = _Rpc(), _Pool(cursor=None)
    lst, ingested, _ = _listener(rpc, pool, monkeypatch)
    _run(lst._sweep_once())
    assert lst._sweep_state()["failed"] == 1 and rpc.get_logs_calls() == [] and ingested == []


def test_a_throttled_listener_skips_and_spends_no_call(monkeypatch):
    rpc, pool = _Rpc(), _Pool()
    lst, ingested, _ = _listener(rpc, pool, monkeypatch, throttled=True)
    _run(lst._sweep_once())
    st = lst._sweep_state()
    assert (st["runs"], st["skipped_throttled"], st["failed"]) == (0, 1, 0)
    assert rpc.calls == [] and pool.saves == [] and ingested == []


def test_an_unsubscribed_listener_sweeps_nothing(monkeypatch):
    rpc, pool = _Rpc(), _Pool()
    lst, ingested, _ = _listener(rpc, pool, monkeypatch, subscribed=False)
    _run(lst._sweep_once())
    assert rpc.calls == [] and ingested == [] and lst._sweep_state()["runs"] == 0


def test_a_receipt_that_cannot_be_fetched_or_decoded_is_todays_refusal(monkeypatch):
    """TX2's receipt fetch raises, TX3's receipt decodes to nothing: no
    row for either, the refusal counted as today, the sweep's own
    counters say found 2 / handled 0 / failed 0 (nothing raised out of
    the handler), the cursor advances (the poller carries them)."""
    class _Bad(_Rpc):
        async def post(self, url, json=None):
            if (json or {}).get("method") == "eth_getTransactionReceipt" and json["params"][0] == TX2:
                self.calls.append(json)
                raise RuntimeError("receipt fetch failed")
            return await super().post(url, json=json)
    rpc, pool = _Bad(receipts={TX3: {"logs": []}}), _Pool()
    lst, ingested, _ = _listener(rpc, pool, monkeypatch, v3_seen={TX1: time.time()})
    _run(lst._sweep_once())
    st = lst._sweep_state()
    assert (st["found"], st["handled"], st["failed"]) == (2, 0, 0)
    assert ingested == [] and lst.v3_refused == 1
    assert pool.cursor == TIP - 2


def test_a_handler_that_raises_counts_failed_and_does_not_advance_the_cursor(monkeypatch):
    rpc, pool = _Rpc(), _Pool()
    lst, ingested, _ = _listener(rpc, pool, monkeypatch, v3_seen={TX1: time.time()})

    async def dead_ingest(ev):
        raise RuntimeError("database gone")
    monkeypatch.setattr(ch, "ingest_trade_result", dead_ingest)
    _run(lst._sweep_once())
    st = lst._sweep_state()
    assert (st["found"], st["handled"], st["failed"]) == (2, 0, 2)
    # _handle_v3 saved nothing (it raised before its own save); the sweep saved nothing
    assert pool.saves == [] and pool.cursor == CURSOR


def test_a_span_with_nothing_confirmed_past_the_cursor_makes_no_get_logs(monkeypatch):
    rpc, pool = _Rpc(tip=CURSOR + 1), _Pool()          # tip - 2 < cursor: end < start
    lst, ingested, _ = _listener(rpc, pool, monkeypatch)
    _run(lst._sweep_once())
    assert rpc.get_logs_calls() == [] and pool.saves == []
    assert lst._sweep_state()["runs"] == 1 and lst._sweep_state()["failed"] == 0


def test_the_span_is_capped_at_max_blocks_and_confirmed_by_confirm_blocks(monkeypatch):
    monkeypatch.setattr(ch, "CHAIN_SWEEP_MAX_BLOCKS", 5.0)
    rpc, pool = _Rpc(tip=CURSOR + 100), _Pool()
    lst, _, _ = _listener(rpc, pool, monkeypatch)
    _run(lst._sweep_once())
    assert rpc.get_logs_calls()[0]["toBlock"] == hex(CURSOR + 4), "[cursor, cursor + MAX - 1]"
    monkeypatch.setattr(ch, "CHAIN_SWEEP_MAX_BLOCKS", 2000.0)
    monkeypatch.setattr(ch, "CHAIN_SWEEP_CONFIRM_BLOCKS", 10.0)
    rpc, pool = _Rpc(tip=CURSOR + 100), _Pool()
    lst, _, _ = _listener(rpc, pool, monkeypatch)
    _run(lst._sweep_once())
    assert rpc.get_logs_calls()[0]["toBlock"] == hex(CURSOR + 90)


def test_other_wallets_and_legacy_logs(monkeypatch):
    """A v3 log naming no roster wallet is not found and costs no RPC; a
    legacy / v2 log goes through _handle_log exactly as backfill sends
    it (it decodes to no roster fill here and lands nothing)."""
    legacy = {"topics": [ch.order_filled_topic(), "0x" + "1" * 64, _t(OTHER), _t(OTHER)],
              "data": "0x" + _w(0) * 5, "transactionHash": TX4, "blockNumber": BLK}
    rpc, pool = _Rpc(logs=[_fill_log(OTHER, TX4), legacy, _fill_log(WHALE, TX2)]), _Pool()
    lst, ingested, observed = _listener(rpc, pool, monkeypatch)
    _run(lst._sweep_once())
    assert rpc.receipts_fetched() == [TX2] and [e.tx_hash for e in ingested] == [TX2]
    assert lst._sweep_state()["found"] == 1 and lst.events_seen == 1
    assert observed == {"emitter": [], "shadow": []}


# ── 4. the task, the lock, the switch ─────────────────────────────────

def test_chain_sweep_off_starts_no_task(monkeypatch):
    monkeypatch.setattr(ch, "CHAIN_SWEEP", False)
    lst = ChainListener.__new__(ChainListener)

    async def run():
        assert lst.ensure_sweep_task() is None
        assert getattr(lst, "_sweep_task", None) is None
    _run(run())


def test_chain_sweep_on_starts_one_task_idempotently(monkeypatch):
    monkeypatch.setattr(ch, "CHAIN_SWEEP", True)
    lst = ChainListener.__new__(ChainListener)

    async def run():
        t = lst.ensure_sweep_task()
        assert t is not None and t.get_name() == "chain.sweep"
        assert lst.ensure_sweep_task() is t
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
        t2 = lst.ensure_sweep_task()
        assert t2 is not t, "a dead loop is restarted at the next subscribe"
        t2.cancel()
        try:
            await t2
        except asyncio.CancelledError:
            pass
    _run(run())


def test_the_loop_waits_the_period_and_survives_a_raising_sweep(monkeypatch):
    slept: list = []
    calls = {"n": 0}

    async def fake_sleep(s):
        slept.append(s)
        if len(slept) > 2:
            raise asyncio.CancelledError
    monkeypatch.setattr(ch.asyncio, "sleep", fake_sleep)
    lst = ChainListener.__new__(ChainListener)

    async def raising_sweep():
        calls["n"] += 1
        raise RuntimeError("boom")
    lst._sweep_once = raising_sweep

    async def run():
        with pytest.raises(asyncio.CancelledError):
            await lst._sweep_loop()
    _run(run())
    assert slept == [ch.CHAIN_SWEEP_S] * 3 and calls["n"] == 2
    assert lst._sweep_state()["failed"] == 2


def test_the_sweep_and_the_catch_up_share_one_lock(monkeypatch):
    rpc, pool = _Rpc(), _Pool()
    lst, _, _ = _listener(rpc, pool, monkeypatch, v3_seen={TX1: time.time()})
    gate = asyncio.Event()
    held: list = []

    async def hold():
        held.append(lst._sweep_lock.locked())
        await gate.wait()
    rpc.during_get_logs = hold

    async def run():
        t = asyncio.create_task(lst._sweep_once())
        await asyncio.sleep(0.01)
        assert held == [True], "the sweep holds the lock across its RPC"
        assert lst._sweep_lock.locked()
        gate.set()
        await t
        assert not lst._sweep_lock.locked()
    _run(run())
    src = inspect.getsource(ChainListener.run)
    i = src.index("async with self._sweep_lock:")
    assert "await self.backfill(cursor + 1, tip)" in src[i:i + 300], "the catch-up runs under the same lock"


def test_run_starts_the_sweep_after_the_ack_and_marks_the_socket_state():
    src = inspect.getsource(ChainListener.run)
    i = src.index("self._last_throttled = False")
    j = src.index("self._subscribed = True")
    assert i < j < src.index("roster_refreshed = time.time()")
    assert "self.ensure_sweep_task()" in src[j:j + 400]
    # every path out of the socket clears it: the loop head, the quiet timeout, the error
    assert src.count("self._subscribed = False") == 3
    assert src.index("self._subscribed = False") < src.index("await self.refresh_roster()")


# ── 5. the rails, in a fresh interpreter ──────────────────────────────

def _fresh(env: dict) -> dict:
    code = ("import json, sportsassets.ingestion.chain as c, sportsassets.ingestion.poller as p; "
            "print(json.dumps([c.CHAIN_SWEEP, c.CHAIN_SWEEP_S, c.CHAIN_SWEEP_CONFIRM_BLOCKS, "
            "c.CHAIN_SWEEP_MAX_BLOCKS, p.POLL_OVERFLOW_PAGES]))")
    e = {k: v for k, v in os.environ.items()
         if not k.startswith(("CHAIN_SWEEP", "POLL_OVERFLOW"))}
    e.update(env)
    out = subprocess.run([sys.executable, "-c", code], cwd=str(BACKEND), env=e,
                         capture_output=True, text=True, check=True).stdout
    sw, s, conf, mx, pages = json.loads(out.strip().splitlines()[-1])
    return {"switch": sw, "s": s, "confirm": conf, "max": mx, "pages": pages}


def test_the_rails_move_only_toward_today():
    assert _fresh({}) == {"switch": True, "s": 15.0, "confirm": 2.0, "max": 2000.0, "pages": 2.0}
    r = _fresh({"CHAIN_SWEEP": "off", "CHAIN_SWEEP_S": "5", "CHAIN_SWEEP_CONFIRM_BLOCKS": "1",
                "CHAIN_SWEEP_MAX_BLOCKS": "5000", "POLL_OVERFLOW_PAGES": "5"})
    assert r == {"switch": False, "s": 15.0, "confirm": 2.0, "max": 2000.0, "pages": 2.0}, \
        "shorter, shallower, wider, deeper: every one lands on the default"
    r = _fresh({"CHAIN_SWEEP": "on", "CHAIN_SWEEP_S": "60", "CHAIN_SWEEP_CONFIRM_BLOCKS": "6",
                "CHAIN_SWEEP_MAX_BLOCKS": "100", "POLL_OVERFLOW_PAGES": "0"})
    assert r == {"switch": True, "s": 60.0, "confirm": 6.0, "max": 100.0, "pages": 0.0}
    r = _fresh({"CHAIN_SWEEP": "maybe", "CHAIN_SWEEP_S": "junk", "CHAIN_SWEEP_CONFIRM_BLOCKS": "-3",
                "CHAIN_SWEEP_MAX_BLOCKS": "0", "POLL_OVERFLOW_PAGES": "-1"})
    assert r == {"switch": True, "s": 15.0, "confirm": 2.0, "max": 1.0, "pages": 0.0}, \
        "junk is the default; the span's floor is 1; the pages' floor is 0"


def test_the_rails_are_read_through_the_mirrors_three_readers():
    src = pathlib.Path(ch.__file__).read_text()
    assert 'CHAIN_SWEEP = _rules.env_switch("CHAIN_SWEEP", True)' in src
    assert 'CHAIN_SWEEP_S = _rules.min_wait_env("CHAIN_SWEEP_S", 15.0)' in src
    assert 'CHAIN_SWEEP_CONFIRM_BLOCKS = _rules.min_wait_env("CHAIN_SWEEP_CONFIRM_BLOCKS", 2.0)' in src
    assert 'CHAIN_SWEEP_MAX_BLOCKS = _rules.capped_env("CHAIN_SWEEP_MAX_BLOCKS", 2000.0, floor=1.0)' in src
    assert "from ..analytics import mirror_live_rules as _rules" in src
    from sportsassets.ingestion import poller as p
    psrc = pathlib.Path(p.__file__).read_text()
    assert 'POLL_OVERFLOW_PAGES = _rules.capped_env("POLL_OVERFLOW_PAGES", 2.0, floor=0.0)' in psrc


# ── 6. not touched ────────────────────────────────────────────────────

def _h(fn) -> str:
    return hashlib.sha256(inspect.getsource(fn).encode()).hexdigest()[:16]


def test_the_decoders_the_guard_and_the_rpc_helpers_are_09b35cds():
    """sha256[:16] of each function's source on 09b35cd: the decoders,
    the selector, the bundle decoder (measurement only), the per-tx
    guard and the no-retry rule inside _handle_v3, _handle_log's
    observe calls, the filter, backfill, the cursor, the tip."""
    assert _h(ch._decode_v3_legacy) == "0dec92b995873db4"
    assert _h(ch._decode_v3_selected) == "7e0f334ebd6353a1"
    assert _h(ch.decode_fills_v3_bundle) == "fa144f6c6f495fbe"
    assert _h(ch.decode_fill_v3_receipt_ex) == "7ecbeee20bdfe6a6"
    assert _h(ch.v3_owner_candidates) == "39e0181a06b0fad1"
    assert _h(ch.decode_order_filled_v2) == "f6008f8f0cb6c74b"
    assert _h(ch.decode_order_filled) == "aa9fbbd17d806da5"
    assert _h(ChainListener._handle_v3) == "6d914233c2672704"
    assert _h(ChainListener._handle_log) == "62df9c56014112a6"
    assert _h(ChainListener._get_logs) == "9610f8ed1e7dce3f"
    assert _h(ChainListener.backfill) == "d2926b27f8d23f86"
    assert _h(ChainListener._save_cursor) == "0ca320e7d88bbb93"
    assert _h(ChainListener._load_cursor) == "5addcd215584b0af"
    assert _h(ChainListener._current_block) == "3e8113a1df4bb859"
    assert _h(ChainListener.refresh_roster) == "1f1b2cc5ffd1af9d"
    assert ch.V3_EXCHANGES_DEFAULT == ("0xe2222d279d744050d28e00520010520000310f59,"
                                       "0xe111180000d2663c0091e4f400237545b87b996b")


def test_the_beat_block_and_the_v3_keys():
    lst = ChainListener.__new__(ChainListener)
    lst.last_event_at, lst.events_seen, lst.decoded, lst.ingested = time.time(), 0, 0, 0
    lst.last_lag_s, lst._roster = None, {}
    d = lst._beat_detail()
    assert d["sweep"] == {"runs": 0, "found": 0, "handled": 0, "failed": 0,
                          "skipped_throttled": 0, "last_span": None, "last_at": None}
    for key in ("v3_refused", "v3_no_token_match", "v3_multi_pass", "v3_px_oob", "v3_ref", "v3_selected"):
        assert key in d
    assert ch._sweep_block() is not ch._sweep_block()


def test_the_docs_name_the_rule():
    doc = (ROOT / "docs" / "mirror-coverage.md").read_text()
    assert re.search(r"^## 68\. E26 -- .* \(2026-09-09, FILL lane 26\)", doc, re.M), "the E26 section header"
    sec = doc[doc.index("## 68. E26"):]
    for k in ("CHAIN_SWEEP", "CHAIN_SWEEP_S", "CHAIN_SWEEP_CONFIRM_BLOCKS", "CHAIN_SWEEP_MAX_BLOCKS",
              "POLL_OVERFLOW_PAGES", "_sweep_once", "ensure_sweep_task", "_handle_v3", "_v3_seen",
              "skipped_throttled", "last_span", "page_overflow", "page_overflow_counts", "_PAGE_OVERFLOW",
              "test_e26_chain_sweep.py", "test_e26_poll_overflow.py", "2,880", "4,869", "5,211",
              "17:04:57", "17:08:24", "207 s", "emitter_observe", "shadow_observe", "_sweep_end"):
        assert k in sec, k
