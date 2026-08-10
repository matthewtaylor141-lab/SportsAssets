"""Path A — on-chain OrderFilled detection (primary, ~1–3s).

Subscribes over Polygon WebSocket to OrderFilled logs on the CTF Exchange and
NegRisk CTF Exchange, filters for tracked wallets, and pushes provisional
trades into the shared pipeline immediately.

Decode notes (CTF Exchange semantics):
  OrderFilled(bytes32 indexed orderHash, address indexed maker,
              address indexed taker, uint256 makerAssetId, uint256 takerAssetId,
              uint256 makerAmountFilled, uint256 takerAmountFilled, uint256 fee)

Each matched order emits one event where `maker` is the order owner.
The order owner gave makerAsset and received takerAsset; asset id 0 is USDC
collateral (6 decimals), any other id is a CTF outcome token (6 decimals).
  makerAssetId == 0  → owner BOUGHT takerAssetId (paid USDC)
  takerAssetId == 0  → owner SOLD  makerAssetId (received USDC)

Fill timestamps MUST be real block timestamps: the Data API reports block
time, and the cross-path dedupe key includes the timestamp.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx
import websockets

from ..config import settings
from ..db import get_pool, heartbeat
from .pipeline import TradeEvent, ingest_trade

log = logging.getLogger(__name__)

ORDER_FILLED_SIG = "OrderFilled(bytes32,address,address,uint256,uint256,uint256,uint256,uint256)"
USDC_DECIMALS = 10**6


def order_filled_topic() -> str:
    """keccak256 of the event signature, computed at runtime (never hardcoded)."""
    from Crypto.Hash import keccak  # pycryptodome

    h = keccak.new(digest_bits=256)
    h.update(ORDER_FILLED_SIG.encode())
    return "0x" + h.hexdigest()


@dataclass
class DecodedFill:
    wallet: str  # tracked wallet (lowercase)
    token_id: str  # outcome token traded (decimal string)
    side: str  # BUY / SELL from the tracked wallet's perspective
    size: float  # shares
    price: float  # USDC per share
    tx_hash: str
    block_number: int


def _topic_addr(topic: str) -> str:
    return "0x" + topic[-40:].lower()


def decode_order_filled(log_entry: dict[str, Any], roster: set[str]) -> DecodedFill | None:
    """Decode one OrderFilled log; return a fill if a roster wallet is involved.

    Pure function — unit-testable with synthetic logs.
    """
    topics = log_entry.get("topics") or []
    if len(topics) < 4:
        return None
    maker = _topic_addr(topics[2])
    taker = _topic_addr(topics[3])

    data = log_entry.get("data", "0x")[2:]
    if len(data) < 5 * 64:
        return None
    words = [int(data[i * 64 : (i + 1) * 64], 16) for i in range(5)]
    maker_asset, taker_asset, maker_amt, taker_amt, _fee = words

    if maker_amt == 0 or taker_amt == 0:
        return None

    # Perspective of the order owner (`maker` field):
    if maker_asset == 0:
        owner_side, token, size_units, usdc_units = "BUY", taker_asset, taker_amt, maker_amt
    elif taker_asset == 0:
        owner_side, token, size_units, usdc_units = "SELL", maker_asset, maker_amt, taker_amt
    else:
        return None  # token-for-token (neg-risk conversion legs) — not a priced fill

    if maker in roster:
        wallet, side = maker, owner_side
    elif taker in roster:
        # Counterparty view: the taker took the opposite side of the same token.
        wallet, side = taker, ("SELL" if owner_side == "BUY" else "BUY")
    else:
        return None

    size = size_units / USDC_DECIMALS
    price = round(usdc_units / size_units, 6)
    return DecodedFill(
        wallet=wallet,
        token_id=str(token),
        side=side,
        size=round(size, 6),
        price=price,
        tx_hash=str(log_entry.get("transactionHash", "")).lower(),
        block_number=int(str(log_entry.get("blockNumber", "0x0")), 16),
    )


class BlockTimestampCache:
    """block number → unix timestamp via eth_getBlockByNumber, memoized."""

    def __init__(self, http: httpx.AsyncClient, rpc_url: str, max_size: int = 2048) -> None:
        self._http = http
        self._url = rpc_url
        self._cache: dict[int, int] = {}
        self._max = max_size

    async def get(self, block_number: int) -> int:
        if block_number in self._cache:
            return self._cache[block_number]
        resp = await self._http.post(
            self._url,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "eth_getBlockByNumber",
                "params": [hex(block_number), False],
            },
        )
        resp.raise_for_status()
        result = resp.json().get("result") or {}
        ts = int(str(result.get("timestamp", hex(int(time.time())))), 16)
        if len(self._cache) >= self._max:
            self._cache.pop(next(iter(self._cache)))
        self._cache[block_number] = ts
        return ts


class ChainListener:
    def __init__(self) -> None:
        cfg = settings()
        self._ws_url = cfg.polygon_ws_url
        self._http_url = cfg.polygon_http_url
        self._addresses = [
            cfg.ctf_exchange_address.lower(),
            cfg.neg_risk_ctf_exchange_address.lower(),
        ]
        self._topic = order_filled_topic()
        self._http = httpx.AsyncClient(timeout=10)
        self._blocks = BlockTimestampCache(self._http, self._http_url)
        self._roster: dict[str, dict] = {}  # address -> {id, username}
        self.last_event_at: float = time.time()
        # Diagnosis counters (2026-08-10: the socket beat 'ok' all day
        # while every fill arrived via the poller — 'subscribed' alone
        # cannot distinguish a silent provider from a decode mismatch).
        self.events_seen = 0    # raw OrderFilled logs delivered by the WS
        self.decoded = 0        # logs that decoded to a roster wallet
        self.ingested = 0       # decoded fills that won the dedupe

    def _beat_detail(self) -> dict:
        return {"subscribed": True,
                "last_event_age_s": round(time.time() - self.last_event_at),
                "events_seen": self.events_seen,
                "decoded": self.decoded,
                "ingested": self.ingested,
                "roster": len(self._roster)}

    async def refresh_roster(self) -> None:
        pool = await get_pool()
        rows = await pool.fetch(
            "SELECT id, address, username FROM whales WHERE active AND NOT banned"
        )
        self._roster = {r["address"].lower(): dict(r) for r in rows}

    async def _handle_log(self, log_entry: dict[str, Any]) -> None:
        self.events_seen += 1
        fill = decode_order_filled(log_entry, set(self._roster))
        if fill is None:
            return
        self.decoded += 1
        whale = self._roster[fill.wallet]
        ts_epoch = await self._blocks.get(fill.block_number)
        ev = TradeEvent(
            whale_id=whale["id"],
            whale_username=whale["username"],
            tx_hash=fill.tx_hash,
            asset=fill.token_id,
            side=fill.side,
            size=fill.size,
            price=fill.price,
            ts_epoch=ts_epoch,
            source="chain",
        )
        trade_id = await ingest_trade(ev)
        if trade_id:
            self.ingested += 1
            log.info(
                "chain fill: %s %s %s %.2f @ %.3f (trade %s)",
                whale["username"] or fill.wallet,
                fill.side,
                fill.token_id[:12],
                fill.size,
                fill.price,
                trade_id,
            )
        await self._save_cursor(fill.block_number)

    async def _save_cursor(self, block_number: int) -> None:
        pool = await get_pool()
        await pool.execute(
            """
            INSERT INTO ingestion_state (key, value) VALUES ('chain.last_block', to_jsonb($1::bigint))
            ON CONFLICT (key) DO UPDATE SET value = GREATEST((ingestion_state.value)::bigint,
                                                             ($1::bigint))::text::jsonb
            """,
            block_number,
        )

    async def _load_cursor(self) -> int | None:
        pool = await get_pool()
        val = await pool.fetchval("SELECT value FROM ingestion_state WHERE key='chain.last_block'")
        return int(json.loads(val)) if val is not None else None

    async def backfill(self, from_block: int, to_block: int) -> int:
        """eth_getLogs over a gap (reconnect recovery). Returns log count processed."""
        count = 0
        step = 2000
        for start in range(from_block, to_block + 1, step):
            end = min(start + step - 1, to_block)
            resp = await self._http.post(
                self._http_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "eth_getLogs",
                    "params": [
                        {
                            "fromBlock": hex(start),
                            "toBlock": hex(end),
                            "address": self._addresses,
                            "topics": [self._topic],
                        }
                    ],
                },
            )
            resp.raise_for_status()
            for entry in resp.json().get("result") or []:
                await self._handle_log(entry)
                count += 1
        return count

    async def _current_block(self) -> int:
        resp = await self._http.post(
            self._http_url,
            json={"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []},
        )
        resp.raise_for_status()
        return int(str(resp.json()["result"]), 16)

    async def run(self) -> None:
        if not self._ws_url:
            log.error("POLYGON_WS_URL not set — Path A disabled, Path B carries detection")
            await heartbeat("chain_listener", "disabled", {"reason": "no POLYGON_WS_URL"})
            return
        while True:
            try:
                await self.refresh_roster()
                # Recover any gap before subscribing.
                cursor = await self._load_cursor()
                if cursor is not None:
                    tip = await self._current_block()
                    if tip > cursor:
                        n = await self.backfill(cursor + 1, tip)
                        log.info("backfilled %s logs over blocks %s..%s", n, cursor + 1, tip)

                async with websockets.connect(self._ws_url, ping_interval=15, ping_timeout=10) as ws:
                    await ws.send(
                        json.dumps(
                            {
                                "jsonrpc": "2.0",
                                "id": 1,
                                "method": "eth_subscribe",
                                "params": [
                                    "logs",
                                    {"address": self._addresses, "topics": [self._topic]},
                                ],
                            }
                        )
                    )
                    await ws.recv()  # subscription ack
                    log.info("Path A subscribed to OrderFilled on %s", self._addresses)
                    await heartbeat("chain_listener", "ok", self._beat_detail())
                    roster_refreshed = time.time()
                    while True:
                        raw = await asyncio.wait_for(ws.recv(), timeout=60)
                        self.last_event_at = time.time()
                        msg = json.loads(raw)
                        params = msg.get("params") or {}
                        entry = params.get("result")
                        if entry:
                            await self._handle_log(entry)
                        if time.time() - roster_refreshed > 60:
                            await self.refresh_roster()
                            roster_refreshed = time.time()
                            await heartbeat("chain_listener", "ok",
                                            self._beat_detail())
            except asyncio.TimeoutError:
                # No events for 60s can be legitimate quiet time; heartbeat + resubscribe
                # to be safe (subscription may have silently died).
                log.info("no WS traffic for 60s — resubscribing")
                await heartbeat("chain_listener", "ok", {"resubscribe": "quiet"})
            except Exception as exc:  # noqa: BLE001
                log.warning("chain listener error: %s — reconnecting in 2s", exc)
                await heartbeat("chain_listener", "down", {"error": str(exc)})
                await asyncio.sleep(2)
