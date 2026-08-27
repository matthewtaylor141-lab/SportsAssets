"""S1 — the quiesced chain emitter (flip contract, backend/docs/s1_flip_contract.md).

Promotes the shadow's proven decode (decode_shadow_views +
classify_mints + the venue-shaped per-market selection) to a LIVE
second chain emitter for exactly the fill shapes `_handle_v3`'s
receipt reconstruction cannot produce: events 2..N of a tx, bundles,
mint-matched complements — the classes that today die at the
executor's staleness cap because only the poller carries them.

This module is the ONLY place that imports both the pipeline's ingest
and the shadow's pure decode functions. shadow_v2 itself is untouched
and keeps measuring independently (contract C2): the instrument that
certifies the flip is never contaminated by the flip.

DESIGN (survived a 3-design adversarial panel; kills and grafts are
recorded in the flip contract):

- Emission timing: buffer raw logs per tx; a tx finalizes only after a
  3s debounce AND the chain head has advanced CONFIRM_DEPTH past the
  fill block (the head is self-advanced by polling when the logs-only
  WS goes quiet). Timestamps come from a STRICT resolver — never
  wall-clock, never a cached guess; the same RPC's returned blockHash
  must equal the buffered log's blockHash (reorg check for free).
- THE EMIT SET: per (tx, wallet) group after classify_mints, only
  (a) agg records and (b) exec_owner records whose asset has no agg
  view. exec_counter / exec_mint records are tie-out evidence, never
  rows: a counter-only group means the wallet's OWN event was lost,
  and emitting the raw complement leg would buy the wrong outcome
  (the panel's critical kill). One untrusted member abstains the
  whole group; the poller carries everything abstained.
- Collision protocol with _handle_v3: the receipt path claims
  (tx, wallet) first (claim_registry); the emitter proceeds only on
  outcome 'refused' or no-claim, and both paths run a trades-table
  pre-probe immediately before ingest — the DB is the cross-restart
  authority.
- Certification gate: emits only while the env flag is on, the
  persisted `armed` flag is set, AND the shadow's own state row is
  GREEN (7-day window, 24h health, matching decoder fingerprint,
  volume floor). Arming requests (S1_ARM env) are honoured only while
  GREEN; a window reset after arming auto-disarms; a key self-check
  failure or observed key divergence trips STICKY (manual re-arm
  only). Unarmed with the flag on = burn-in: the full pipeline runs
  and counts s1.would_emit, writing nothing.

Money safety: everything downstream of ingest_trade_result — dedupe,
fan-out, every executor gate — is byte-untouched (contract C1). The
executor's one-copy-per-asset guard remains the final backstop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import weakref
from collections import OrderedDict
from decimal import Decimal
from typing import Any

import httpx

from .claim_registry import claim as _claim
from .claim_registry import get as _claim_get
from .shadow_v2 import (
    DECODER_FP,
    classify_mints,
    agg_tieout,
    decode_shadow_views,
    rec_keys,
    rec_prices,
)

log = logging.getLogger(__name__)

PENDING_CAP = 4096
DEBOUNCE_S = 3.0
CONFIRM_DEPTH = 3
TS_BUDGET_S = 30.0
EMIT_MAX_AGE_S = 45.0
V3_WAIT_S = 10.0
RPC_PER_TICK = 4
RPC_PER_MIN = 30
RPC_BACKOFF_S = 120.0
TICK_S = 1.0
FLUSH_EVERY_S = 60.0
CERT_EVERY_S = 60.0
CERT_WINDOW_S = 7 * 86400.0
CERT_HEALTH_S = 86400.0
CERT_MIN_VEN = 500
CERT_MIN_AGG = 50
STATE_KEY = "s1_emitter"
SHADOW_KEY = "shadow_v2_fill"

SQL_PROBE = (
    "SELECT 1 FROM trades WHERE lower(tx_hash) = $1 AND whale_id = $2 "
    "AND source = 'chain' LIMIT 1"
)
SQL_READ = "SELECT value FROM ingestion_state WHERE key = $1"
SQL_WRITE = (
    "INSERT INTO ingestion_state (key, value) VALUES ($1, $2) "
    "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
)


class S1Emitter:
    def __init__(self) -> None:
        # the flag gates the whole task; armed gates real emission
        self.enabled = os.getenv("S1_EMITTER", "off").lower() in ("on", "1", "true")
        self.listener: Any = None                    # weakref.ref
        self.http_url = ""
        # tx -> {logs, first_seen, last_seen, replay, evicted,
        #        blocks: {blk -> blockHash}, ts: {blk -> int},
        #        ts_started, v3_wait_started}
        self.pending: OrderedDict[str, dict] = OrderedDict()
        self.deltas: dict[str, int] = {}
        self.counters: dict[str, int] = {}           # last flushed view
        self.head = 0                                 # chain-head watermark
        self.armed = False
        self.armed_at = 0.0
        self.tripped: str | None = None
        self.cert_green = False
        self.cert_reason = "never_checked"
        self.cert_checked_at = 0.0
        self.last_flush_at = 0.0
        self.last_emit_at = 0.0
        self.rpc_tokens = float(RPC_PER_MIN)
        self.rpc_token_at = time.time()
        self.rpc_backoff_until = 0.0
        self._client: httpx.AsyncClient | None = None
        self._state_loaded = False
        self._exch_cache: tuple[tuple, set[str]] | None = None

    # ── tiny helpers ────────────────────────────────────────────────
    def bump(self, key: str, n: int = 1) -> None:
        if n:
            self.deltas[key] = self.deltas.get(key, 0) + n

    def exchange_set(self, listener: Any) -> set[str]:
        addrs = getattr(listener, "_addresses", None) or []
        key = tuple(addrs)
        if self._exch_cache and self._exch_cache[0] == key:
            return self._exch_cache[1]
        s = {str(a).lower() for a in addrs}
        self._exch_cache = (key, s)
        return s

    # ── observe (sync, zero I/O, never raises) ──────────────────────
    def observe(self, listener: Any, log_entry: dict[str, Any]) -> None:
        try:
            if not self.enabled:
                return
            if bool(getattr(listener, "_shadow_replay", False)):
                # rule 7: replayed logs never emit — cheapest at the door
                self.bump("s1.abstain.replay")
                return
            if log_entry.get("removed"):
                tx0 = str(log_entry.get("transactionHash", "")).lower()
                if tx0 and self.pending.pop(tx0, None) is not None:
                    self.bump("s1.abstain.reorged")
                return
            tx = str(log_entry.get("transactionHash", "")).lower()
            if not tx:
                return
            blk = int(str(log_entry.get("blockNumber", "0x0")), 16)
            if blk > self.head:
                self.head = blk
            now = time.time()
            e = self.pending.get(tx)
            if e is None:
                e = {"logs": [], "first_seen": now, "last_seen": now,
                     "evicted": False, "blocks": {}, "ts": {},
                     "ts_started": None, "v3_wait_started": None}
                self.pending[tx] = e
                while len(self.pending) > PENDING_CAP:
                    _k, victim = self.pending.popitem(last=False)
                    victim["evicted"] = True
                    self.bump("s1.abstain.overflow")
            e["logs"].append(log_entry)
            e["last_seen"] = now
            if blk:
                e["blocks"][blk] = str(log_entry.get("blockHash", "")).lower()
        except Exception:  # noqa: BLE001 — the wall
            try:
                self.deltas["s1.errors"] = self.deltas.get("s1.errors", 0) + 1
            except Exception:  # noqa: BLE001
                pass

    # ── strict ts resolver (never wall-clock; reorg check included) ─
    async def _resolve_block(self, blk: int, want_hash: str) -> int | str | None:
        """int ts on success; 'reorged' on hash mismatch; None = retry."""
        self.bump("s1.rpc_calls")
        try:
            if self._client is None:
                self._client = httpx.AsyncClient(timeout=4)
            resp = await self._client.post(self.http_url, json={
                "jsonrpc": "2.0", "id": 1, "method": "eth_getBlockByNumber",
                "params": [hex(blk), False]})
            if resp.status_code == 429 or resp.status_code >= 500:
                self.rpc_backoff_until = time.time() + RPC_BACKOFF_S
                self.bump("s1.rpc_backoffs")
                return None
            result = (resp.json() or {}).get("result") or {}
            raw = result.get("timestamp")
            if raw is None:
                self.bump("s1.rpc_empty")
                return None
            got_hash = str(result.get("hash", "")).lower()
            if want_hash and got_hash and got_hash != want_hash:
                return "reorged"
            return int(str(raw), 16)
        except Exception:  # noqa: BLE001
            self.bump("s1.rpc_errors")
            return None

    async def _poll_head(self) -> None:
        self.bump("s1.head_polls")
        try:
            if self._client is None:
                self._client = httpx.AsyncClient(timeout=4)
            resp = await self._client.post(self.http_url, json={
                "jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber",
                "params": []})
            raw = (resp.json() or {}).get("result")
            if raw is not None:
                blk = int(str(raw), 16)
                if blk > self.head:
                    self.head = blk
        except Exception:  # noqa: BLE001
            self.bump("s1.rpc_errors")

    def _take_token(self) -> bool:
        now = time.time()
        if now < self.rpc_backoff_until:
            return False
        self.rpc_tokens = min(
            float(RPC_PER_MIN),
            self.rpc_tokens + (now - self.rpc_token_at) * RPC_PER_MIN / 60.0)
        self.rpc_token_at = now
        if self.rpc_tokens < 1:
            return False
        self.rpc_tokens -= 1
        return True

    # ── certification ───────────────────────────────────────────────
    def _judge_cert(self, doc: dict | None, now: float) -> tuple[bool, str]:
        if not isinstance(doc, dict):
            return False, "no_shadow_state"
        ws, hs = doc.get("window_start"), doc.get("health_start")
        if not isinstance(ws, (int, float)) or now - ws < CERT_WINDOW_S:
            return False, "window_young"
        if not isinstance(hs, (int, float)) or now - hs < CERT_HEALTH_S:
            return False, "health_young"
        if doc.get("decoder_fp") != DECODER_FP:
            # the emitter refuses to run a decode the window did not
            # certify — version skew in either direction is RED
            return False, "decoder_fp_mismatch"
        c = doc.get("counters") or {}
        aw = doc.get("at_window") or {}

        def _delta(key: str) -> int:
            cv, wv = c.get(key), aw.get(key)
            cv = cv if isinstance(cv, (int, float)) else 0
            wv = wv if isinstance(wv, (int, float)) else 0
            return int(cv - wv)

        if _delta("sim_ven_suppressed") < CERT_MIN_VEN:
            return False, "volume_floor_ven"
        if _delta("decoded_agg") < CERT_MIN_AGG:
            return False, "volume_floor_agg"
        return True, "green"

    async def _check_cert(self, pool: Any, now: float) -> None:
        self.cert_checked_at = now
        try:
            raw = await pool.fetchval(SQL_READ, SHADOW_KEY, timeout=6)
            doc = raw if isinstance(raw, dict) else (
                json.loads(raw) if raw else None)
        except Exception:  # noqa: BLE001
            self.bump("s1.errors")
            doc = None
        green, reason = self._judge_cert(doc, now)
        self.cert_green, self.cert_reason = green, reason
        if self.armed:
            ws = (doc or {}).get("window_start")
            if not green or (isinstance(ws, (int, float)) and ws > self.armed_at):
                # ratchet: the evidence moved out from under the arm
                self.armed = False
                self.bump("s1.trip.window_reset")
                log.warning("S1 auto-disarmed: %s", reason if not green
                            else "window_reset_after_arm")
        elif (green and self.tripped is None
              and os.getenv("S1_ARM", "").lower() in ("on", "1", "true")):
            self.armed = True
            self.armed_at = now
            log.warning("S1 ARMED at %s (cert green)", now)

    def _trip(self, reason: str) -> None:
        self.armed = False
        self.tripped = reason
        self.bump("s1.trip." + reason)
        log.error("S1 STICKY TRIP: %s — manual re-arm required", reason)

    # ── finalize one tx ─────────────────────────────────────────────
    async def _finalize_tx(self, pool: Any, tx: str, e: dict,
                           now: float) -> bool:
        """True = done with this tx (emitted/abstained); False = retry."""
        lst = self.listener() if self.listener else None
        if lst is None:
            return False
        if e["evicted"]:
            return True                       # counted at eviction time
        # ts resolution for every block the tx touched (usually one)
        if e["ts_started"] is None:
            e["ts_started"] = now
        for blk, want_hash in list(e["blocks"].items()):
            if blk in e["ts"]:
                continue
            if not self._take_token():
                break
            got = await self._resolve_block(blk, want_hash)
            if got == "reorged":
                self.bump("s1.abstain.reorged")
                return True
            if got is None:
                break
            e["ts"][blk] = got
        unresolved = [b for b in e["blocks"] if b not in e["ts"]]
        if unresolved:
            if now - e["ts_started"] > TS_BUDGET_S:
                self.bump("s1.abstain.ts_unresolved")
                return True
            return False
        # freshness on BLOCK time, never first_seen
        newest_ts = max(e["ts"].values())
        if now - newest_ts > EMIT_MAX_AGE_S:
            self.bump("s1.abstain.too_old")
            return True

        roster = getattr(lst, "_roster", {}) or {}
        exch = self.exchange_set(lst)
        recs: list[dict] = []
        for le in e["logs"]:
            got, reason, _inv = decode_shadow_views(le, roster, exch)
            if reason is not None:
                self.bump("s1.abstain.decode_refusal")
                return True                   # one bad event = whole tx
            tps = le.get("topics") or []
            if len(tps) >= 4 and str(tps[2]).lower()[-40:] == \
                    str(tps[3]).lower()[-40:]:
                self.bump("s1.abstain.self_trade")
                return True
            for r in got:
                if not r["block"]:
                    self.bump("s1.abstain.decode_refusal")
                    return True
                r["ts"] = e["ts"].get(r["block"])
                recs.append(r)
        if not recs:
            return True                       # foreign tx — nothing to do
        groups = classify_mints(recs)
        emitted_any = False
        for wallet, g in groups.items():
            done = await self._emit_group(pool, tx, wallet, g, e, now)
            if done is None:
                return False                  # v3 outcome pending — retry
            emitted_any = emitted_any or done
        return True

    async def _emit_group(self, pool: Any, tx: str, wallet: str,
                          g: dict, e: dict, now: float) -> bool | None:
        """True/False = group finished (emitted at least one / none);
        None = retry the tx later (v3 outcome pending)."""
        flags = dict(g.get("flags") or {})
        # bookkeeping flags, not verdicts: agg_missing marks the
        # counter-stays-raw arm (the counter-only rule below names that
        # abstention properly), mint_transformed marks a transform the
        # tie-out branch still has to PROVE. Everything else abstains.
        flags.pop("agg_missing", None)
        flags.pop("mint_transformed", None)
        if g.get("dropped") or flags:
            self.bump("s1.abstain.flags")
            return False
        aggs, execs = g.get("aggs") or [], g.get("execs") or []
        if any(r.get("mint_unresolved") for r in execs):
            self.bump("s1.abstain.mint_unresolved")
            return False
        owners = [r for r in execs if r["view"] == "exec_owner"]
        if not aggs and not owners:
            # counter-only: the wallet's own event was lost — emitting
            # the complement leg buys the wrong outcome (panel kill 1)
            self.bump("s1.abstain.counter_only")
            return False
        tie = agg_tieout(g)
        if aggs:
            legs = [r for r in execs
                    if r["view"] in ("exec_counter", "exec_mint")
                    and r["asset"] == aggs[0]["asset"]]
            if len(aggs) > 1:
                self.bump("s1.abstain.flags")
                return False
            if legs and tie != "ok":
                self.bump("s1.abstain.tieout_fail")
                return False
            if not legs and tie == "fail":
                self.bump("s1.abstain.tieout_fail")
                return False
        # THE EMIT SET: aggs, plus exec_owner records for markets
        # without an agg view. exec_counter/exec_mint are never rows.
        agg_assets = {a["asset"] for a in aggs}
        emit_recs = list(aggs) + [r for r in owners
                                  if r["asset"] not in agg_assets]
        if not emit_recs:
            self.bump("s1.abstain.counter_only")
            return False
        # v3 collision protocol
        cl = _claim_get(tx, wallet)
        if cl is not None and cl["owner"] == "receipt":
            if not cl["done"]:
                if e["v3_wait_started"] is None:
                    e["v3_wait_started"] = now
                if now - e["v3_wait_started"] < V3_WAIT_S:
                    return None               # wait a tick
                self.bump("s1.abstain.v3_unknown")
                return False
            if cl["outcome"] == "ingested":
                self.bump("s1.abstain.v3_ingested")
                return False
            # outcome 'refused': the class is ours — proceed
        roster = (self.listener() and self.listener()._roster) or {}
        whale = roster.get(wallet)
        if whale is None:
            self.bump("s1.abstain.decode_refusal")
            return False
        emitted = False
        for rec in emit_recs:
            keys = rec_keys(rec)
            if len(keys) > 1:
                # the venue might store the HALF_UP price; a one-key
                # emit can diverge from the poll row's key (panel kill 2)
                self.bump("s1.abstain.price_variant")
                continue
            if not self.armed or not self.cert_green or self.tripped:
                self.bump("s1.would_emit")
                emitted = True                # burn-in counts as handled
                continue
            try:
                row = await pool.fetchrow(SQL_PROBE, tx, rec["whale_id"],
                                          timeout=6)
            except Exception:  # noqa: BLE001
                self.bump("s1.errors")
                return False                  # no probe = no emit
            if row is not None:
                self.bump("s1.abstain.chain_row_preexists")
                continue
            from .pipeline import TradeEvent, ingest_trade_result
            size = float(Decimal(rec["size_units"]) / Decimal(10 ** 6))
            price = rec_prices(rec)[0]
            ev = TradeEvent(
                whale_id=rec["whale_id"],
                whale_username=rec.get("username"),
                tx_hash=tx, asset=rec["asset"], side=rec["side"],
                size=size, price=price, ts_epoch=rec["ts"],
                source="chain")
            if ev.dedupe_key != keys[0][1]:
                # the built event does not reproduce the certified key —
                # the one condition that must never be guessed around
                self.bump("s1.abstain.key_selfcheck")
                self._trip("key_selfcheck")
                return False
            _claim(tx, wallet, "emitter")
            try:
                _tid, was_new = await ingest_trade_result(ev)
            except Exception:  # noqa: BLE001
                self.bump("s1.errors")
                return False
            emitted = True
            self.last_emit_at = now
            if was_new:
                self.bump("s1.emitted")
                self.bump("s1.emitted_agg" if rec["view"] == "agg"
                          else "s1.emitted_exec_owner")
                age = now - rec["ts"]
                b = ("le5s" if age <= 5 else "le10s" if age <= 10
                     else "le15s" if age <= 15 else "gt15s")
                self.bump("s1.emit_age." + b)
                log.info("S1 fill: %s %s %s %.2f @ %.4f (+%.1fs)",
                         rec.get("username") or wallet[:10], rec["side"],
                         rec["asset"][:12], size, price, age)
            else:
                self.bump("s1.emit_dup")
        return emitted

    # ── flush (own state key; observability, not evidence) ──────────
    async def _flush(self, pool: Any, now: float) -> None:
        self.last_flush_at = now
        if not self.deltas and self._state_loaded:
            pass
        try:
            raw = await pool.fetchval(SQL_READ, STATE_KEY, timeout=6)
            doc = raw if isinstance(raw, dict) else (
                json.loads(raw) if raw else {})
            if not isinstance(doc, dict):
                doc = {}
        except Exception:  # noqa: BLE001
            self.bump("s1.flush_failures")
            return
        counters = doc.get("counters") or {}
        if not isinstance(counters, dict):
            counters = {}
        if not self._state_loaded:
            # boot: adopt persisted arm state (sticky across restarts)
            self._state_loaded = True
            if doc.get("tripped"):
                self.tripped = str(doc["tripped"])
            if doc.get("armed") and self.tripped is None:
                self.armed = True
                self.armed_at = float(doc.get("armed_at") or now)
        snap, self.deltas = self.deltas, {}
        for k, v in snap.items():
            base = counters.get(k)
            counters[k] = (base if isinstance(base, (int, float)) else 0) + v
        self.counters = counters
        payload = {"counters": counters, "armed": self.armed,
                   "armed_at": self.armed_at, "tripped": self.tripped,
                   "cert_green": self.cert_green,
                   "cert_reason": self.cert_reason,
                   "decoder_fp": DECODER_FP,
                   "updated_at_epoch": now}
        try:
            await pool.execute(SQL_WRITE, STATE_KEY, json.dumps(payload),
                               timeout=6)
        except Exception:  # noqa: BLE001
            # restore so the deltas land on the next flush
            for k, v in snap.items():
                self.deltas[k] = self.deltas.get(k, 0) + v
            self.bump("s1.flush_failures")

    # ── the task loop ───────────────────────────────────────────────
    async def run(self) -> None:
        from ..db import get_pool
        last_cert = 0.0
        while True:
            try:
                await asyncio.sleep(TICK_S)
                if not self.enabled:
                    continue
                lst = self.listener() if self.listener else None
                if lst is not None:
                    self.http_url = getattr(lst, "_http_url", "") or self.http_url
                if not self.http_url:
                    continue
                pool = await get_pool()
                now = time.time()
                if now - last_cert >= CERT_EVERY_S:
                    last_cert = now
                    await self._check_cert(pool, now)
                # head poll only when someone is waiting on confirmation
                waiting = any(
                    max(e["blocks"], default=0) + CONFIRM_DEPTH > self.head
                    for e in self.pending.values() if e["blocks"])
                if waiting and self._take_token():
                    await self._poll_head()
                now = time.time()
                for tx in list(self.pending.keys())[:64]:
                    e = self.pending.get(tx)
                    if e is None:
                        continue
                    if now - e["last_seen"] < DEBOUNCE_S:
                        continue
                    if e["blocks"] and \
                            max(e["blocks"]) + CONFIRM_DEPTH > self.head:
                        if now - e["first_seen"] > EMIT_MAX_AGE_S:
                            self.bump("s1.abstain.confirm_timeout")
                            self.pending.pop(tx, None)
                        continue
                    done = await self._finalize_tx(pool, tx, e, now)
                    if done:
                        self.pending.pop(tx, None)
                now = time.time()
                if now - self.last_flush_at >= FLUSH_EVERY_S:
                    await self._flush(pool, now)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                self.bump("s1.errors")
                await asyncio.sleep(2)


_STATE = S1Emitter()
_TASK: asyncio.Task | None = None


def emitter_observe(listener: Any, log_entry: dict[str, Any]) -> None:
    """Sync, zero-I/O, never raises. Called from chain._handle_log for
    every FILL_V3_TOPIC log, before _handle_v3 dispatch."""
    try:
        _STATE.observe(listener, log_entry)
    except Exception:  # noqa: BLE001
        try:
            _STATE.deltas["s1.errors"] = _STATE.deltas.get("s1.errors", 0) + 1
        except Exception:  # noqa: BLE001
            pass


def ensure_emitter_task(listener: Any) -> None:
    """Idempotent; one task per process. Mirror of ensure_shadow_task."""
    global _TASK
    try:
        _STATE.listener = weakref.ref(listener)
        _STATE.http_url = getattr(listener, "_http_url", "") or _STATE.http_url
        if not _STATE.enabled:
            return
        if _TASK is None or _TASK.done():
            _TASK = asyncio.get_running_loop().create_task(_STATE.run())
    except Exception:  # noqa: BLE001
        pass


def emitter_beat() -> dict:
    try:
        st = _STATE
        return {"enabled": st.enabled, "armed": st.armed,
                "cert": st.cert_reason, "tripped": st.tripped,
                "pending": len(st.pending),
                "emitted": st.counters.get("s1.emitted", 0)
                + st.deltas.get("s1.emitted", 0),
                "would": st.counters.get("s1.would_emit", 0)
                + st.deltas.get("s1.would_emit", 0),
                "last_emit_age_s": (round(time.time() - st.last_emit_at)
                                    if st.last_emit_at else None),
                "err_unflushed": st.deltas.get("s1.errors", 0)}
    except Exception:  # noqa: BLE001
        return {"enabled": False}
