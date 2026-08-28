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

# Scoped per FILL (asset included): a (tx, whale)-only probe finds the
# row the emitter itself just wrote for the FIRST market of a bundle
# and silently forfeits every later market — the exact class S1 exists
# for (fleet round 1, confirmed major). Both chain sources count: a
# receipt row or an earlier emitter row equally forbids a second view.
SQL_PROBE = (
    "SELECT source, dedupe_key FROM trades "
    "WHERE lower(tx_hash) = $1 AND whale_id = $2 "
    "AND asset = $3 AND source IN ('chain', 's1')"
)
# The corroboration sweep: judgment anchors to the durable rows, not
# process memory (fleet round 3) — restart-proof, exactly-once via the
# s1_checked_at stamp, and the consequence of a venue-refuted emission
# lands on the responsible component: this emitter trips STICKY. A row
# whose whale left the roster is unjudgeable (no poll carrier remains)
# — counted, never alarmed (round 3: stamp starvation via roster ban).
# 45min -> 75min (fleet round 4): the sole backstop is the HOURLY
# reconciler, and the codebase's own sizing rule for exactly this
# window is ORPHAN_FINAL_S=4200 ("one deferral window covering the
# hourly reconciler"). 2700s sat INSIDE the backstop's worst case and
# falsely tripped correct emissions the /trades window slid past.
CORROBORATE_S = 75 * 60
SQL_SWEEP = """
SELECT t.id, t.dedupe_key, t.detected_at,
       (t.venue_seen_at IS NOT NULL) AS ok,
       (w.active AND NOT w.banned)   AS pollable
FROM trades t JOIN whales w ON w.id = t.whale_id
WHERE t.source = 's1' AND t.s1_checked_at IS NULL
  AND t.detected_at < now() - make_interval(secs => $1)
ORDER BY t.detected_at
LIMIT 50
"""
SQL_MARK = "UPDATE trades SET s1_checked_at = now() WHERE id = ANY($1::bigint[])"
# The verdict is conditional on the backstop having actually had its
# chance: an uncorroborated judgment requires a COMPLETED reconciler
# run that STARTED after the row was detected (its 500-deep re-fetch
# then provably re-delivered or refused the fill). No completed run =
# defer, unstamped — a Path-B outage defers judgment, never mass-trips
# (fleet round 4).
SQL_RECON_SINCE = """
SELECT 1 FROM reconciliation_runs
WHERE started_at > $1 AND finished_at IS NOT NULL
LIMIT 1
"""
SQL_READ = "SELECT value FROM ingestion_state WHERE key = $1"
# Compare-and-swap on the tripped field: a concurrent process's sticky
# trip must never be erased by our read-modify-write (fleet round 2,
# CRITICAL). The write lands only if the stored trip still equals the
# trip we read; a lost swap is re-read and merged next flush.
SQL_WRITE = """
INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb)
ON CONFLICT (key) DO UPDATE SET value = $2::jsonb
WHERE COALESCE(ingestion_state.value->>'tripped', '') = $3
RETURNING key
"""


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
        self.tripped_at = 0.0
        self.unjudged_backlog = 0
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
        self._pending_arm_at: float | None = None
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
            bh = str(log_entry.get("blockHash", "")).lower()
            if not blk or not bh:
                # no block number = no timestamp = no key ever; no block
                # hash = no reorg check — either way this log can never
                # emit safely, and buffering it would crash or blind the
                # finalize path (fleet r1: wedged-loop + silent-reorg)
                self.bump("s1.abstain.no_block")
                return
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
            e["blocks"][blk] = bh
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

    # ── persisted state (read-only load; fail-closed until loaded) ──
    async def _load_state(self, pool: Any, now: float) -> None:
        """Adopt the persisted trip/arm BEFORE anything can certify or
        emit. Until this succeeds the emitter stays disarmed with
        cert_reason='state_unloaded' — a restarted sticky-tripped
        process must never arm in the window before its first flush
        (fleet round 2, confirmed major x3)."""
        try:
            raw = await pool.fetchval(SQL_READ, STATE_KEY, timeout=6)
        except Exception:  # noqa: BLE001 — stay unloaded, stay closed
            self.bump("s1.errors")
            return
        try:
            doc = raw if isinstance(raw, dict) else (
                json.loads(raw) if raw else {})
            if not isinstance(doc, dict):
                doc = {}
            if doc.get("tripped"):
                self.tripped = self._trip_str(doc["tripped"])
                self.armed = False
            elif doc.get("armed"):
                aa = doc.get("armed_at")
                self._pending_arm_at = (float(aa) if isinstance(
                    aa, (int, float)) else now)
            c = doc.get("counters")
            self.counters = c if isinstance(c, dict) else {}
        except Exception:  # noqa: BLE001 — a corrupt row must not wedge
            # the loop; fail-closed with nothing adopted (round 3)
            self.bump("s1.state_corrupt")
            self.tripped = self.tripped or "state_corrupt"
        self._state_loaded = True

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
        if not self._state_loaded:
            self.cert_green = False
            self.cert_reason = "state_unloaded"
            return
        try:
            raw = await pool.fetchval(SQL_READ, SHADOW_KEY, timeout=6)
            doc = raw if isinstance(raw, dict) else (
                json.loads(raw) if raw else None)
        except Exception:  # noqa: BLE001
            # a transient READ failure is not evidence of anything: it
            # must not consume a pending arm, disarm a live one, or
            # fabricate trip counters (fleet round 2). Emission is
            # fail-closed anyway via cert_green.
            self.bump("s1.errors")
            self.cert_green = False
            self.cert_reason = "cert_read_failed"
            return
        green, reason = self._judge_cert(doc, now)
        self.cert_green, self.cert_reason = green, reason
        pend = getattr(self, "_pending_arm_at", None)
        if pend is not None:
            # a persisted arm survives a restart only if the window it
            # was granted under is still standing
            self._pending_arm_at = None
            ws = (doc or {}).get("window_start")
            if green and self.tripped is None and \
                    isinstance(ws, (int, float)) and ws <= pend:
                self.armed = True
                self.armed_at = pend
            else:
                self.bump("s1.trip.window_reset")
                log.warning("S1 persisted arm NOT adopted: %s",
                            reason if not green else "window_reset_while_down")
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
        self.tripped_at = time.time()
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
        if not e["ts"]:
            # cannot happen while observe refuses block-less logs, but a
            # crash here wedges the whole run loop — guard, never trust
            self.bump("s1.abstain.no_block")
            return True
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
            if wallet in e.setdefault("done_wallets", set()):
                continue                      # a retry never re-runs a
                                              # completed group (fleet r2:
                                              # straggler re-representation)
            done = await self._emit_group(pool, tx, wallet, g, e, now)
            if done is None:
                return False                  # v3 outcome pending — retry
            e["done_wallets"].add(wallet)
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
                # burn-in: idempotent per (wallet, asset) — no view in
                # the mark (a straggler can flip the representative
                # record's view across a retry) and the armed path marks
                # too, so a disarm mid-retry cannot re-count an emitted
                # fill as would_emit (fleet r2)
                mark = (wallet, rec["asset"])
                if mark not in e.setdefault("counted", set()):
                    e["counted"].add(mark)
                    self.bump("s1.would_emit")
                emitted = True                # burn-in counts as handled
                continue
            # CLAIM BEFORE ANY AWAIT and honor refusal: awaiting the
            # probe between the registry read and the claim opened a
            # window where the receipt path could claim-and-ingest a
            # key-divergent row (fleet r1, confirmed CRITICAL)
            if not _claim(tx, wallet, "emitter"):
                self.bump("s1.abstain.v3_ingested")
                continue
            try:
                rows = await pool.fetch(SQL_PROBE, tx, rec["whale_id"],
                                        rec["asset"], timeout=6)
            except Exception:  # noqa: BLE001
                self.bump("s1.errors")
                return False                  # no probe = no emit
            # a receipt-path row for this (tx, whale, asset) is the
            # key-divergent-twin risk — abstain. Our own earlier s1 row
            # with THIS key is a dup — skip. An s1 row with a DIFFERENT
            # key is a sibling fill in the same market (fleet round 2:
            # the conflation forfeited the whale's second fill) —
            # proceed; the dedupe collapses anything truly identical.
            srcs = {str(r["source"]) for r in (rows or [])}
            stored_keys = {str(r["dedupe_key"]) for r in (rows or [])}
            if "chain" in srcs:
                self.bump("s1.abstain.chain_row_preexists")
                continue
            if keys[0][1] in stored_keys:
                self.bump("s1.emit_dup")
                continue
            from .pipeline import TradeEvent, ingest_trade_result
            size = float(Decimal(rec["size_units"]) / Decimal(10 ** 6))
            price = rec_prices(rec)[0]
            # source='s1', not 'chain': the shadow buckets evidence rows
            # strictly by 'chain'/'poll', so the instrument never sees
            # the emitter's own rows as coverage — a wrong emission can
            # no longer silence the orphan GATING alarms that would
            # catch it (fleet r1: self-certification kill). Downstream
            # the pipeline/executor are source-agnostic.
            ev = TradeEvent(
                whale_id=rec["whale_id"],
                whale_username=rec.get("username"),
                tx_hash=tx, asset=rec["asset"], side=rec["side"],
                size=size, price=price, ts_epoch=rec["ts"],
                source="s1")
            if ev.dedupe_key != keys[0][1]:
                # the built event does not reproduce the certified key —
                # the one condition that must never be guessed around
                self.bump("s1.abstain.key_selfcheck")
                self._trip("key_selfcheck")
                return False
            try:
                _tid, was_new = await ingest_trade_result(ev)
            except Exception:  # noqa: BLE001
                self.bump("s1.errors")
                return False
            e.setdefault("counted", set()).add((wallet, rec["asset"]))
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

    # ── the corroboration sweep ─────────────────────────────────────
    async def _corroboration_sweep(self, pool: Any) -> None:
        try:
            rows = await pool.fetch(SQL_SWEEP, float(CORROBORATE_S),
                                    timeout=10)
        except Exception:  # noqa: BLE001
            self.bump("s1.errors")
            return
        if not rows:
            self.unjudged_backlog = 0
            return
        confirmed_ids, judged_ids = [], []
        tripped_key = None
        deferred = 0
        for r in rows:
            if r["ok"]:
                confirmed_ids.append(r["id"])
                continue
            if not r["pollable"]:
                # roster departure is routinely TRANSIENT (fleet r4):
                # never stamp — the row is re-judged when the whale
                # returns; a permanently-departed whale's rows stay a
                # visible backlog, never a silent amnesty
                deferred += 1
                continue
            try:
                ran = await pool.fetchrow(SQL_RECON_SINCE,
                                          r["detected_at"], timeout=6)
            except Exception:  # noqa: BLE001
                self.bump("s1.errors")
                return
            if ran is None:
                deferred += 1          # the backstop has not had its
                continue               # chance yet — defer, never trip
            self.bump("s1.uncorroborated")
            judged_ids.append(r["id"])
            tripped_key = str(r["dedupe_key"])[:16]
            log.error("S1 UNCORROBORATED row id=%s key=%s — the venue's "
                      "feed never showed this fill", r["id"], tripped_key)
        self.unjudged_backlog = deferred
        # STAMP BEFORE COUNTING (fleet r4): a failed stamp must not
        # inflate s1.confirmed forever, and the trip only lands when
        # its row is durably marked judged
        ids = confirmed_ids + judged_ids
        if ids:
            try:
                await pool.execute(SQL_MARK, ids, timeout=10)
            except Exception:  # noqa: BLE001
                self.bump("s1.errors")
                return                 # nothing counted, nothing tripped
        self.bump("s1.confirmed", len(confirmed_ids))
        if tripped_key is not None:
            self._trip("uncorroborated")

    # ── flush (own state key; observability, not evidence) ──────────
    @staticmethod
    def _trip_str(v: Any) -> str:
        # must reproduce Postgres ->> for the CAS comparison: text as
        # itself, other JSON scalars in JSON form (round 3: a boolean
        # trip made str(True) != 'true' and starved every flush)
        if v is None:
            return ""
        return v if isinstance(v, str) else json.dumps(v)

    async def _flush(self, pool: Any, now: float) -> None:
        self.last_flush_at = now
        if not self._state_loaded:
            # writing before the persisted state is known can erase a
            # pending arm or a foreign trip (round 3) — deltas simply
            # accumulate until the loader succeeds
            return
        try:
            raw = await pool.fetchval(SQL_READ, STATE_KEY, timeout=6)
            doc = raw if isinstance(raw, dict) else (
                json.loads(raw) if raw else {})
            if not isinstance(doc, dict):
                doc = {}
        except Exception:  # noqa: BLE001
            self.bump("s1.flush_failures")
            return
        stored_trip = self._trip_str(doc.get("tripped"))
        cleared_at = doc.get("trip_cleared_at")
        if (self.tripped is not None and not stored_trip
                and isinstance(cleared_at, (int, float))
                and cleared_at > self.tripped_at):
            # a human explicitly cleared the trip AFTER it fired: adopt
            # the clear (stay disarmed until re-armed through cert).
            # Without this, every running process silently re-asserted
            # its in-memory trip over any manual recovery (fleet r4).
            log.warning("S1 trip '%s' cleared by operator at %s",
                        self.tripped, cleared_at)
            self.tripped = None
        if stored_trip and self.tripped is None:
            # another process tripped since our last read: a sticky
            # trip is global — adopt it, never clobber it (fleet r1/r2)
            self.tripped = stored_trip
            self.armed = False
        counters = doc.get("counters") or {}
        if not isinstance(counters, dict):
            counters = {}
        snap, self.deltas = self.deltas, {}
        merged = dict(counters)
        for k, v in snap.items():
            base = merged.get(k)
            merged[k] = (base if isinstance(base, (int, float)) else 0) + v
        # a pending (not-yet-validated) arm is still an arm on disk —
        # persisting armed=false before validation erased legitimate
        # arms across boot blips (round 3)
        armed_out = bool((self.armed or self._pending_arm_at is not None)
                         and not self.tripped)
        payload = {"counters": merged, "armed": armed_out,
                   "armed_at": (self.armed_at if self.armed
                                else (self._pending_arm_at or 0.0)),
                   "tripped": self.tripped or stored_trip or None,
                   "tripped_at": self.tripped_at,
                   "trip_cleared_at": cleared_at,
                   "unjudged_backlog": self.unjudged_backlog,
                   "cert_green": self.cert_green,
                   "cert_reason": self.cert_reason,
                   "decoder_fp": DECODER_FP,
                   "updated_at_epoch": now}
        try:
            got = await pool.fetchval(SQL_WRITE, STATE_KEY,
                                      json.dumps(payload), stored_trip,
                                      timeout=6)
        except Exception:  # noqa: BLE001
            # AMBIGUOUS: the write may have committed and lost its ack.
            # Restoring the snap would double-count on the next flush,
            # so the snap is DROPPED — undercount-only, the same choice
            # the shadow's ack protocol makes (round 3)
            self.bump("s1.flush_failures")
            self.bump("s1.snap_dropped_ambiguous")
            return
        if got is None:
            # unambiguous: the CAS WHERE refused, nothing was written —
            # restore the deltas and adopt the foreign trip next read
            for k, v in snap.items():
                self.deltas[k] = self.deltas.get(k, 0) + v
            self.bump("s1.flush_failures")
            return
        self.counters = merged

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
                if not self._state_loaded:
                    await self._load_state(pool, now)
                if now - last_cert >= CERT_EVERY_S:
                    last_cert = now
                    await self._check_cert(pool, now)
                    if self._state_loaded:
                        await self._corroboration_sweep(pool)
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
                "unjudged": st.unjudged_backlog,
                "last_emit_age_s": (round(time.time() - st.last_emit_at)
                                    if st.last_emit_at else None),
                "err_unflushed": st.deltas.get("s1.errors", 0)}
    except Exception:  # noqa: BLE001
        return {"enabled": False}
