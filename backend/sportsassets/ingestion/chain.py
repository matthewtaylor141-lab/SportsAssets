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


# The 2026 exchange contract's fill event (Polymarket migrated exchanges;
# the old CTF contracts stopped emitting — diagnosed 2026-08-10 when the
# listener sat subscribed-but-silent all day). The topic is the OBSERVED
# constant from live receipts (tx 0x2be95df8...62e3d4), not computed from
# a signature: the contract is unverified on the explorers, so the
# signature string is unknowable — but the layout was decoded empirically
# against a known RN1 fill and every word tied out exactly
# ($7.6322 / 12.31 shares @ 0.62, fee 0.145):
#   topic1 orderHash, topic2 order owner, topic3 counterparty/exchange
#   word0 side (0 = owner bought, 1 = owner sold)
#   word1 outcome token id
#   word2 amount the owner GAVE   (USDC on buys, tokens on sells)
#   word3 amount the owner GOT    (tokens on buys, USDC on sells)
#   word4 fee (USDC, 6dp)         words 5-6 observed zero
ORDER_FILLED_V2_TOPIC = (
    "0xd543adfd945773f1a62f74f0ee55a5e3b9b1a28262980ba90b1a89f2ea84d8ee")


def decode_order_filled_v2(log_entry: dict[str, Any],
                           roster: set[str]) -> DecodedFill | None:
    """Decode one v2 fill log; each matched order emits its OWN log with
    the order owner in topic2, so only that side is matched against the
    roster. Pure function — unit-tested with the live log verbatim."""
    topics = log_entry.get("topics") or []
    if len(topics) < 4:
        return None
    owner = _topic_addr(topics[2])
    if owner not in roster:
        return None
    data = log_entry.get("data", "0x")[2:]
    if len(data) < 4 * 64:
        return None
    words = [int(data[i * 64: (i + 1) * 64], 16) for i in range(4)]
    side_flag, token, gave, got = words
    if gave == 0 or got == 0:
        return None
    if side_flag == 0:
        side, usdc_units, size_units = "BUY", gave, got
    else:
        side, size_units, usdc_units = "SELL", gave, got
    size = size_units / USDC_DECIMALS
    price = round(usdc_units / size_units, 6)
    if not (0 < price < 1):
        return None
    return DecodedFill(
        wallet=owner,
        token_id=str(token),
        side=side,
        size=round(size, 6),
        price=price,
        tx_hash=str(log_entry.get("transactionHash", "")).lower(),
        block_number=int(str(log_entry.get("blockNumber", "0x0")), 16),
    )


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
            cfg.pm_exchange_v2_address.lower(),
        ]
        # OR-list in topic position 0: legacy OrderFilled plus the v2
        # fill event — either matches.
        self._topic = order_filled_topic()
        self._topics = [[self._topic, ORDER_FILLED_V2_TOPIC]]
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
        topics = log_entry.get("topics") or []
        if topics and str(topics[0]).lower() == ORDER_FILLED_V2_TOPIC:
            fill = decode_order_filled_v2(log_entry, set(self._roster))
        else:
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

    async def _get_logs(self, start: int, end: int) -> list[dict]:
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
                        "topics": self._topics,
                    }
                ],
            },
        )
        if resp.status_code >= 400:
            # Surface the provider's own explanation — a bare "400 Bad
            # Request" burned an hour of Path A downtime on 2026-08-11
            # because the reason (range/response cap? param shape?) was
            # discarded here and only Alchemy's dashboard showed the
            # rejects.
            raise RuntimeError(
                f"eth_getLogs {start}..{end} -> HTTP {resp.status_code}: "
                f"{resp.text[:300]}")
        body = resp.json()
        if body.get("error"):
            raise RuntimeError(
                f"eth_getLogs {start}..{end} -> RPC error: "
                f"{str(body['error'])[:300]}")
        return body.get("result") or []

    async def backfill(self, from_block: int, to_block: int) -> int:
        """eth_getLogs over a gap (reconnect recovery). Returns log count processed.

        Providers cap getLogs differently (block span, log count, response
        bytes) and answer an over-cap query with 400 — so a rejected chunk
        retries at smaller spans before giving up, and the final failure
        carries the provider's error text.
        """
        count = 0
        step = 2000
        start = from_block
        while start <= to_block:
            end = min(start + step - 1, to_block)
            try:
                entries = await self._get_logs(start, end)
            except RuntimeError:
                if step > 10:
                    step = max(10, step // 10)
                    log.warning("backfill chunk %s..%s rejected — retrying "
                                "at %s-block spans", start, end, step)
                    continue
                raise
            for entry in entries:
                await self._handle_log(entry)
                count += 1
            start = end + 1
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
                # Recover any gap before subscribing — but NEVER let the
                # catch-up block the live stream. On 2026-08-11 a rejected
                # getLogs threw here, so every reconnect died before
                # eth_subscribe and Path A stayed down for an hour while
                # the subscription itself would have worked fine. The
                # poller + reconciler own gap coverage; a skipped backfill
                # costs nothing but duplicate-suppressed rows.
                cursor = await self._load_cursor()
                if cursor is not None:
                    tip = await self._current_block()
                    if tip > cursor:
                        try:
                            n = await self.backfill(cursor + 1, tip)
                            log.info("backfilled %s logs over blocks %s..%s",
                                     n, cursor + 1, tip)
                        except Exception as exc:  # noqa: BLE001
                            log.warning("backfill %s..%s failed (%s) — "
                                        "skipping catch-up, subscribing live",
                                        cursor + 1, tip, exc)
                            await heartbeat(
                                "chain_listener", "ok",
                                {"backfill_skipped": str(exc)[:300],
                                 "gap_blocks": tip - cursor})
                            # Move past the poisoned range so the next
                            # reconnect doesn't re-fight the same rejection
                            # forever; the poller covers the gap.
                            await self._save_cursor(tip)

                async with websockets.connect(self._ws_url, ping_interval=15, ping_timeout=10) as ws:
                    await ws.send(
                        json.dumps(
                            {
                                "jsonrpc": "2.0",
                                "id": 1,
                                "method": "eth_subscribe",
                                "params": [
                                    "logs",
                                    {"address": self._addresses,
                                     "topics": self._topics},
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
