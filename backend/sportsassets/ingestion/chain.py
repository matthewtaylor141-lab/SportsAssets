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
from . import claim_registry
from .pipeline import TradeEvent, ingest_trade_result
from .s1_emitter import emitter_beat, emitter_observe, ensure_emitter_task
from .shadow_v2 import beat_summary as _shadow_beat, ensure_shadow_task, shadow_observe

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


# ── Path A2: the venue's NEW exchanges (vanity 0xe111…/0xe2222…) emit a
# proprietary fill event (topic 0xd543adfd…) our decoders never knew —
# whales whose flow moved there silently fell back to minutes-latency
# polling (RN1 median 212s, measured 2026-08-24). The proprietary DATA
# layout is unknown, but the same receipt carries the economics in
# STANDARD events: ERC-1155 TransferSingle (token id + shares) and
# ERC-20 Transfer (USDC legs). So A2 matches the roster wallet in the
# fill event's owner topics, pulls the receipt (~1 RPC), and decodes
# the standard legs — sub-second detection, no reverse-engineering.
FILL_V3_TOPIC = "0xd543adfd945773f1a62f74f0ee55a5e3b9b1a28262980ba90b1a89f2ea84d8ee"
TRANSFER_SINGLE_TOPIC = "0xc3d58168c5ae7397731d063d5bbf3d657854427343f4c083240f7aacaa2d0f62"
TRANSFER_BATCH_TOPIC = "0x4a39dc06d4c0dbc64b70af90fd698a233a518aa5d07e595d983b8c0526c8f7fb"
_V3_MAX_BATCH = 1024


class _Malformed(Exception):
    """A wallet-touching 1155 leg or wallet-topic fill event that cannot
    be fully read: the receipt is unpriceable, refuse."""

ERC20_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
V3_EXCHANGES_DEFAULT = ("0xe2222d279d744050d28e00520010520000310f59,"
                        "0xe111180000d2663c0091e4f400237545b87b996b")


def v3_owner_candidates(log_entry: dict[str, Any]) -> set[str]:
    """Addresses named in a v3 fill event's indexed topics."""
    topics = log_entry.get("topics") or []
    return {_topic_addr(t) for t in topics[1:4] if t}


def _decode_v3_legacy(logs: list[dict[str, Any]],
                      wallet: str) -> DecodedFill | None:
    """Reconstruct one wallet's fill from a receipt's STANDARD events.

    BUY: the wallet received CTF tokens (TransferSingle to=wallet) and
    paid USDC (ERC-20 Transfer from=wallet). SELL is the mirror. More
    than one distinct token id for the wallet is a bundle we don't
    price — refuse and let the poller carry it. Pure function.
    """
    wallet = wallet.lower()
    tok_in = tok_out = 0
    usdc_in = usdc_out = 0
    token_ids: set[int] = set()
    tx_hash = ""
    block_number = 0
    for lg in logs:
        tps = lg.get("topics") or []
        if not tps:
            continue
        t0 = str(tps[0]).lower()
        data = str(lg.get("data", "0x"))[2:]
        if t0 == TRANSFER_SINGLE_TOPIC and len(tps) >= 4                 and len(data) >= 2 * 64:
            frm, to = _topic_addr(tps[2]), _topic_addr(tps[3])
            tid = int(data[0:64], 16)
            val = int(data[64:128], 16)
            if to == wallet:
                tok_in += val
                token_ids.add(tid)
            elif frm == wallet:
                tok_out += val
                token_ids.add(tid)
            else:
                continue
        elif t0 == ERC20_TRANSFER_TOPIC and len(tps) >= 3                 and len(data) >= 64:
            frm, to = _topic_addr(tps[1]), _topic_addr(tps[2])
            val = int(data[0:64], 16)
            if frm == wallet:
                usdc_out += val
            elif to == wallet:
                usdc_in += val
            else:
                continue
        else:
            continue
        tx_hash = str(lg.get("transactionHash", tx_hash)).lower() or tx_hash
        try:
            block_number = int(str(lg.get("blockNumber", "0x0")), 16)                 or block_number
        except ValueError:
            pass
    if len(token_ids) != 1:
        return None
    token = token_ids.pop()
    if tok_in and usdc_out and not tok_out:
        side, size_units, usdc_units = "BUY", tok_in, usdc_out
    elif tok_out and usdc_in and not tok_in:
        side, size_units, usdc_units = "SELL", tok_out, usdc_in
    else:
        return None
    if size_units == 0:
        return None
    price = round(usdc_units / size_units, 6)
    if not (0 < price < 1):
        return None
    return DecodedFill(
        wallet=wallet,
        token_id=str(token),
        side=side,
        size=round(size_units / USDC_DECIMALS, 6),
        price=price,
        tx_hash=tx_hash,
        block_number=block_number,
    )



def _decode_batch_arrays(data: str) -> list[tuple[int, int]]:
    """ABI-decode (uint256[] ids, uint256[] values) from TransferBatch
    data, bounds-checked; raises _Malformed on any violation."""
    try:
        if len(data) < 2 * 64:
            raise _Malformed
        off_ids, off_vals = int(data[0:64], 16), int(data[64:128], 16)

        def arr(off_bytes: int) -> list[int]:
            if off_bytes <= 0 or off_bytes % 32:
                raise _Malformed
            p = off_bytes * 2
            if p + 64 > len(data):
                raise _Malformed
            n = int(data[p:p + 64], 16)
            if n > _V3_MAX_BATCH:
                raise _Malformed
            if p + 64 + n * 64 > len(data):
                raise _Malformed
            return [int(data[p + 64 + i * 64: p + 128 + i * 64], 16)
                    for i in range(n)]

        ids, vals = arr(off_ids), arr(off_vals)
        if len(ids) != len(vals):
            raise _Malformed
        return list(zip(ids, vals))
    except _Malformed:
        raise
    except Exception as exc:  # noqa: BLE001 — any parse slip is malformed
        raise _Malformed from exc


def _wallet_1155_legs(logs, wallet):
    """Wallet-scoped ERC-1155 flows: TransferSingle AND TransferBatch.
    Returns (my_ids, sh_in, sh_out, tx_hash, block_number)."""
    my_ids: set[int] = set()
    sh_in = sh_out = 0
    tx_hash = ""
    block_number = 0
    for lg in logs:
        tps = lg.get("topics") or []
        if not tps:
            continue
        t0 = str(tps[0]).lower()
        if t0 not in (TRANSFER_SINGLE_TOPIC, TRANSFER_BATCH_TOPIC):
            continue
        if len(tps) < 4:
            if any(wallet == _topic_addr(str(t)) for t in tps[1:]):
                raise _Malformed
            continue
        frm, to = _topic_addr(str(tps[2])), _topic_addr(str(tps[3]))
        if wallet not in (frm, to):
            continue
        data = str(lg.get("data", "0x"))[2:]
        if t0 == TRANSFER_SINGLE_TOPIC:
            if len(data) < 2 * 64:
                raise _Malformed
            pairs = [(int(data[0:64], 16), int(data[64:128], 16))]
        else:
            pairs = _decode_batch_arrays(data)
        for tid, val in pairs:
            my_ids.add(tid)          # ids count even at val 0 (probe parity)
            if val == 0:
                continue
            if to == wallet:
                sh_in += val
            else:
                sh_out += val
        tx_hash = str(lg.get("transactionHash", tx_hash)).lower() or tx_hash
        try:
            block_number = int(str(lg.get("blockNumber", "0x0")), 16) \
                or block_number
        except ValueError:
            pass
    return my_ids, sh_in, sh_out, tx_hash, block_number


def _decode_v3_selected(logs, wallet):
    """Production-verified single-event selector (probes 2026-08-30)."""
    try:
        my_ids, sh_in, sh_out, tx_hash, blk = _wallet_1155_legs(logs, wallet)
    except _Malformed:
        return None, "malformed_1155"
    if not my_ids or (sh_in == 0 and sh_out == 0):
        return None, "no_shares"
    if len(my_ids) > 1:
        return None, "multi_token"
    if sh_in and sh_out:
        return None, "mixed_direction"
    token = next(iter(my_ids))
    side = "BUY" if sh_in else "SELL"
    shares = sh_in or sh_out
    candidates: list[tuple[int, int, int]] = []
    for lg in logs:
        tps = lg.get("topics") or []
        if not tps or str(tps[0]).lower() != FILL_V3_TOPIC:
            continue
        if wallet not in {_topic_addr(str(t)) for t in tps[1:4] if t}:
            continue
        data = str(lg.get("data", "0x"))[2:]
        if len(data) < 4 * 64:
            return None, "malformed_fill"
        candidates.append((int(data[64:128], 16),
                           int(data[128:192], 16),
                           int(data[192:256], 16)))
        tx_hash = str(lg.get("transactionHash", tx_hash)).lower() or tx_hash
        try:
            blk = int(str(lg.get("blockNumber", "0x0")), 16) or blk
        except ValueError:
            pass
    if not candidates:
        return None, "no_fill_events"
    if not any(w1 == token for w1, _w2, _w3 in candidates):
        return None, "no_token_match"
    passes = [(w1, w2, w3) for w1, w2, w3 in candidates
              if w1 == token and w2 > 0 and w3 > 0 and w3 == shares]
    if not passes:
        return None, "no_share_match"
    if len(passes) > 1:
        return None, "multi_pass"
    _w1, w2, w3 = passes[0]
    price = round(w2 / w3, 6)
    if not (0 < price < 1):
        return None, "px_oob"
    return DecodedFill(
        wallet=wallet, token_id=str(token), side=side,
        size=round(shares / USDC_DECIMALS, 6), price=price,
        tx_hash=tx_hash, block_number=blk), "selected"


def decode_fill_v3_receipt_ex(logs, wallet):
    """(fill, reason). Legacy lane first; selector only on legacy refusal."""
    wallet = wallet.lower()
    try:
        fill = _decode_v3_legacy(logs, wallet)
        if fill is not None:
            return fill, "legacy"
        return _decode_v3_selected(logs, wallet)
    except Exception:  # noqa: BLE001 — fail closed, never crash the WS loop
        return None, "error"


def decode_fill_v3_receipt(logs: list[dict[str, Any]],
                           wallet: str) -> DecodedFill | None:
    """Two lanes: the legacy direct-USDC-leg decode, then the
    production-verified single-event selector."""
    return decode_fill_v3_receipt_ex(logs, wallet)[0]


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
            # Crypto/non-sports books fill on a second v2 instance —
            # without it the crypto copy whales decode zero (2026-08-22).
            cfg.pm_exchange_crypto_address.lower(),
        ]
        # Path A2 exchanges (env-extensible as the venue mints more)
        import os as _os
        self._addresses += [
            a.strip().lower() for a in
            _os.getenv("PM_EXCHANGE_V3_ADDRESSES",
                       V3_EXCHANGES_DEFAULT).split(",") if a.strip()]
        # OR-list in topic position 0: legacy OrderFilled plus the v2
        # fill event — either matches.
        self._topic = order_filled_topic()
        self._topics = [[self._topic, ORDER_FILLED_V2_TOPIC, FILL_V3_TOPIC]]
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
        # Venue block time -> our ingest, for the last fill this path
        # won (owner latency push 2026-08-20). NOTE this measures from
        # on-chain SETTLEMENT, which itself lags the off-chain CLOB
        # match — the poller's detect_lag_s is the comparable number.
        self.last_lag_s: float | None = None

    def _beat_detail(self) -> dict:
        return {"subscribed": True,
                "last_event_age_s": round(time.time() - self.last_event_at),
                "events_seen": self.events_seen,
                "decoded": self.decoded,
                "ingested": self.ingested,
                "v3_refused": getattr(self, "v3_refused", 0),
                "v3_no_token_match": (getattr(self, "v3_ref", None) or {}).get("no_token_match", 0),
                "v3_multi_pass": (getattr(self, "v3_ref", None) or {}).get("multi_pass", 0),
                "v3_px_oob": (getattr(self, "v3_ref", None) or {}).get("px_oob", 0),
                "v3_ref": dict(getattr(self, "v3_ref", None) or {}),
                "v3_selected": getattr(self, "v3_selected", 0),
                "detect_lag_s": self.last_lag_s,
                "roster": len(self._roster),
                "shadow": _shadow_beat(),
                "s1": emitter_beat()}

    async def refresh_roster(self) -> None:
        pool = await get_pool()
        rows = await pool.fetch(
            "SELECT id, address, username FROM whales WHERE active AND NOT banned"
        )
        self._roster = {r["address"].lower(): dict(r) for r in rows}

    async def _handle_log(self, log_entry: dict[str, Any]) -> None:
        self.events_seen += 1
        topics = log_entry.get("topics") or []
        if topics and str(topics[0]).lower() == FILL_V3_TOPIC:
            try:  # S0 shadow: observe-only, sync, no I/O (shadow_v2.py)
                shadow_observe(self, log_entry)
            except Exception:  # noqa: BLE001 — wall body must stay bare
                pass
            try:  # S1 emitter: buffers only; its own independent wall,
                # BEFORE _handle_v3 so _v3_seen cannot hide events 2..N
                emitter_observe(self, log_entry)
            except Exception:  # noqa: BLE001 — wall body must stay bare
                pass
            await self._handle_v3(log_entry)
            return
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
        # `if trade_id:` was a dedupe test in effect, and stopped being
        # one when ingest_trade switched to ON CONFLICT DO UPDATE — it
        # returns the id for duplicates too, so `ingested` counted every
        # re-seen chain fill and the heartbeat over-reported.
        trade_id, was_new = await ingest_trade_result(ev)
        if was_new:
            self.ingested += 1
            self.last_lag_s = round(time.time() - ts_epoch, 1)
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

    async def _handle_v3(self, log_entry: dict[str, Any]) -> None:
        """Path A2: roster wallet named in a new-exchange fill event →
        pull the receipt once and decode the standard transfer legs."""
        matched = v3_owner_candidates(log_entry) & set(self._roster)
        if not matched:
            return
        tx = str(log_entry.get("transactionHash", "")).lower()
        if not tx:
            return
        if not hasattr(self, "_v3_seen"):
            self._v3_seen = {}
        if tx in self._v3_seen:
            return
        self._v3_seen[tx] = time.time()
        # S1 collision protocol: the receipt path claims every matched
        # (tx, wallet) synchronously at the _v3_seen set point — before
        # any await — so the emitter's 3s debounce always finds the
        # claim. Outcomes land below; the emitter reads them.
        for wallet in matched:
            claim_registry.claim(tx, wallet, "receipt")
        if len(self._v3_seen) > 512:
            cutoff = sorted(self._v3_seen.values())[128]
            self._v3_seen = {k: v for k, v in self._v3_seen.items()
                             if v > cutoff}
        try:
            resp = await self._http.post(
                self._http_url,
                json={"jsonrpc": "2.0", "id": 1,
                      "method": "eth_getTransactionReceipt",
                      "params": [tx]})
            receipt = (resp.json() or {}).get("result") or {}
        except Exception:  # noqa: BLE001 — the poller still carries it
            log.warning("v3 receipt fetch failed for %s", tx)
            for wallet in matched:
                # _v3_seen means this path never retries: the class is
                # the emitter's (or the poller's) from here on
                claim_registry.finish(tx, wallet, "receipt", "refused")
            return
        logs = receipt.get("logs") or []
        for wallet in matched:
            fill, why = decode_fill_v3_receipt_ex(logs, wallet)
            if fill is None:
                # COUNTED (audit 2026-08-30): receipts show the venue
                # batches several fills — including several of ONE
                # wallet's — into one settlement tx, so this refusal
                # is where hrh's (and swisstony's) chain lane dies and
                # their edge decays at poll latency. The counter rides
                # the beat so the coming bundle decoder has a
                # before/after number instead of a story.
                self.v3_refused = getattr(self, "v3_refused", 0) + 1
                ref = getattr(self, "v3_ref", None)
                if ref is None:
                    ref = self.v3_ref = {}
                ref[why] = ref.get(why, 0) + 1
                log.info("v3 fill undecodable for %s in %s (%s)",
                         wallet[:10], tx[:14], why)
                claim_registry.finish(tx, wallet, "receipt", "refused")
                continue
            if why == "selected":
                self.v3_selected = getattr(self, "v3_selected", 0) + 1
            reg = claim_registry.get(tx, wallet)
            if reg is not None and reg["owner"] == "emitter":
                # ordering inversion inside one process: the emitter got
                # here first — its row stands, this path steps back
                self._v3_skip_emitter = getattr(
                    self, "_v3_skip_emitter", 0) + 1
                continue
            pool = await get_pool()
            # asset-scoped, both chain sources: a (tx, whale)-only probe
            # would block legitimate other-market rows of the same tx,
            # and an emitter row must count (fleet r1)
            pre = await pool.fetchrow(
                "SELECT 1 FROM trades WHERE lower(tx_hash) = $1 "
                "AND whale_id = $2 AND asset = $3 "
                "AND source IN ('chain', 's1') LIMIT 1",
                tx, self._roster[wallet]["id"], fill.token_id)
            if pre is not None:
                # cross-restart authority: a chain row (emitter's, or a
                # pre-crash twin) already exists — never write a second
                # view of the same fill with a possibly-divergent key
                self._v3_skip_preexist = getattr(
                    self, "_v3_skip_preexist", 0) + 1
                claim_registry.finish(tx, wallet, "receipt", "ingested")
                continue
            self.decoded += 1
            whale = self._roster[wallet]
            ts_epoch = await self._blocks.get(
                fill.block_number
                or int(str(log_entry.get("blockNumber", "0x0")), 16))
            ev = TradeEvent(
                whale_id=whale["id"],
                whale_username=whale["username"],
                tx_hash=fill.tx_hash or tx,
                asset=fill.token_id,
                side=fill.side,
                size=fill.size,
                price=fill.price,
                ts_epoch=ts_epoch,
                source="chain",
            )
            trade_id, was_new = await ingest_trade_result(ev)
            claim_registry.finish(tx, wallet, "receipt", "ingested")
            if was_new:
                self.ingested += 1
                self.last_lag_s = round(time.time() - ts_epoch, 1)
                log.info("v3 chain fill: %s %s %s %.2f @ %.3f (trade %s)",
                         whale["username"] or wallet, fill.side,
                         fill.token_id[:12], fill.size, fill.price,
                         trade_id)
        blk = int(str(log_entry.get("blockNumber", "0x0")), 16)
        if blk:
            await self._save_cursor(blk)

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
        self._shadow_replay = True
        try:
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
        finally:
            self._shadow_replay = False
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
                ensure_shadow_task(self)  # S0 shadow: idempotent, never raises
                ensure_emitter_task(self)  # S1 emitter: idempotent, flag-off default
                # Recover any gap before subscribing — but NEVER let the
                # catch-up block the live stream. On 2026-08-11 a rejected
                # getLogs threw here, so every reconnect died before
                # eth_subscribe and Path A stayed down for an hour while
                # the subscription itself would have worked fine. The
                # poller + reconciler own gap coverage; a skipped backfill
                # costs nothing but duplicate-suppressed rows.
                # The WHOLE catch-up (tip check included) is optional:
                # a throttled eth_blockNumber (429, 2026-08-11 afternoon)
                # used to throw here and kill every reconnect before the
                # subscribe, exactly like the rejected backfill before it.
                try:
                    cursor = await self._load_cursor()
                    if cursor is not None:
                        tip = await self._current_block()
                        if tip > cursor:
                            n = await self.backfill(cursor + 1, tip)
                            log.info("backfilled %s logs over blocks %s..%s",
                                     n, cursor + 1, tip)
                            await self._save_cursor(tip)
                except Exception as exc:  # noqa: BLE001
                    log.warning("catch-up failed (%s) — skipping, "
                                "subscribing live", exc)
                    await heartbeat(
                        "chain_listener", "ok",
                        {"backfill_skipped": str(exc)[:300]})
                    # Move past any poisoned range so the next reconnect
                    # doesn't re-fight the same rejection; the poller
                    # covers the gap. Best-effort — under a 429 the tip
                    # itself may be unknowable, and that's fine.
                    try:
                        await self._save_cursor(await self._current_block())
                    except Exception:  # noqa: BLE001
                        pass

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
                    self._fail_streak = 0
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
                # Exponential backoff, capped at 2 minutes. A fixed 2s
                # retry against a rate-limiting provider (429, 2026-08-11)
                # is a denial-of-service on our own quota: ~1,800 rejected
                # calls per hour that keep the throttle pinned. Reset on
                # every successful subscribe.
                self._fail_streak = getattr(self, "_fail_streak", 0) + 1
                delay = min(2 * (2 ** min(self._fail_streak - 1, 6)), 120)
                log.warning("chain listener error: %s — reconnecting in %ss",
                            exc, delay)
                await heartbeat("chain_listener", "down",
                                {"error": str(exc)[:300],
                                 "fail_streak": self._fail_streak,
                                 "retry_in_s": delay})
                await asyncio.sleep(delay)
