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
# The head poll exists for when the logs-only WS goes QUIET — round 6
# measured the un-gated version burning 60 tokens/min under live
# traffic (there is always a tx younger than CONFIRM_DEPTH) and
# starving timestamp resolution 30-to-1. observe() already advances
# the head from every WS log; the poll fires only after the WS has
# not advanced it for HEAD_QUIET_S, at most once per HEAD_POLL_MIN_S.
HEAD_QUIET_S = 6.0
HEAD_POLL_MIN_S = 5.0
TS_CACHE_CAP = 1024                 # block ts is per-BLOCK, not per-tx
COUNTED_CAP = 8192                  # burn-in marks survive entry pop
RECON_VENUE_LAG_S = 600.0           # data-api indexing lag margin
RECON_TS_MARGIN_S = 300.0           # venue ts vs block ts skew margin
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
# The pollable filter lives in the SQL (fleet round 5): unjudgeable
# rows re-entering the window at the FRONT of the oldest-first order
# could pin the LIMIT forever and starve every judgable row behind
# them of its alarm. Departed-whale rows stay unstamped and invisible
# to this query until the whale returns; the backlog gauge counts them
# separately.
# ROTATING windows (fleet round 7): deterministic oldest-first order
# rebuilt the starvation one tier up TWICE — 20 permanently-deferring
# wallets owned the wallet list forever, and a wallet's own 10 oldest
# deferring rows owned its window forever, so a younger judgable
# wrong emission behind either was never examined. Both windows now
# order by a per-sweep salted hash: every wallet and every row has
# equal probability of entering each 60s sweep, so nothing can be
# starved indefinitely — deferral defers, it no longer blocks.
SQL_SWEEP_WALLETS = """
SELECT w.id AS whale_id, w.address
FROM trades t JOIN whales w ON w.id = t.whale_id
WHERE t.source = 's1' AND t.s1_checked_at IS NULL
  AND t.detected_at < now() - make_interval(secs => $1)
  AND w.active AND NOT w.banned
GROUP BY w.id, w.address
ORDER BY md5(w.address || $2::text)
LIMIT 200
"""
# PER-WALLET windows (fleet round 6): rows DEFERRING at the coverage
# check defer only their own whale; rotation (round 7) stops them
# pinning even that.
SQL_SWEEP = """
SELECT t.id, t.dedupe_key, t.detected_at, t.ts,
       (t.venue_seen_at IS NOT NULL) AS ok
FROM trades t
WHERE t.whale_id = $2 AND t.source = 's1' AND t.s1_checked_at IS NULL
  AND t.detected_at < now() - make_interval(secs => $1)
ORDER BY md5(t.id::text || $3::text)
LIMIT 10
"""
SQL_BACKLOG = """
SELECT count(*) FROM trades
WHERE source = 's1' AND s1_checked_at IS NULL
  AND detected_at < now() - make_interval(secs => $1)
"""
# The stamp is a TRANSITION, not an overwrite (fleet round 8): only
# rows still unstamped move, and the winners come back — counting from
# the returned set is what makes judgment exactly-once across
# concurrent processes, not merely across restarts.
SQL_MARK = ("UPDATE trades SET s1_checked_at = now() "
            "WHERE id = ANY($1::bigint[]) AND s1_checked_at IS NULL "
            "RETURNING id")
# The verdict is conditional on the backstop having actually had its
# chance AT THIS FILL (fleet rounds 5+6). Round 5 required the wallet
# present as a success key and absent as a failure key; round 6 proved
# two ways a "covering" run never actually saw the fill:
#   - DEPTH: a busy whale does 500+ venue fills between the s1 fill
#     and the run, the /trades sweep fetches only the newest `depth`
#     rows, and no future run reaches deeper — a permanent false trip
#     on a venue-visible fill. The run now records cov:<addr> with
#     either complete=true (feed exhausted) or the oldest venue ts it
#     actually reached; coverage requires that window to provably span
#     the fill's own timestamp (with a margin for venue-vs-block skew).
#   - LAG: a run STARTED seconds after detection sits inside the venue
#     data-api's indexing lag and cannot possibly contain the fill.
#     The covering run must start RECON_VENUE_LAG_S after detection.
# A run without the cov key (old format) never covers; rows defer.
# $1 is CAST (fleet round 7, CRITICAL): untyped, Postgres resolved
# '$1 + interval' as interval+interval at prepare and the statement
# could never execute — the alarm was structurally silent, which only
# a real-Postgres execution pin catches (tests/test_s1_sql_real_pg).
# complete NO LONGER waives the span (fleet round 8, major): a venue
# whose per-wallet index degrades to its newest slice ends the walk
# early with an ordinary short/empty page, records complete=true, and
# the waiver false-tripped STICKY on a correct emission. Coverage now
# always requires the walk to have provably reached at or below the
# fill's own timestamp; a phantom fill older than the whale's entire
# walked history therefore DEFERS instead of alarming — the safe
# direction, and the poller/venue-stamp path remains primary. The
# oldest cast is shape-guarded (round 8: an unguarded tombstone cast
# in SQL_TRIP wedged persists — same lesson applies here).
SQL_RECON_SINCE = """
SELECT 1 FROM reconciliation_runs
WHERE started_at > $1::timestamptz + make_interval(secs => $4)
  AND finished_at IS NOT NULL
  AND details->'per_wallet' ? $2
  AND NOT (details->'per_wallet' ? ('failed:' || $2))
  AND (CASE WHEN jsonb_typeof(
              details->'per_wallet'->('cov:' || $2)->'oldest') = 'number'
            THEN (details->'per_wallet'->('cov:' || $2)->>'oldest')::float8
            ELSE 'Infinity'::float8 END) <= $3
LIMIT 1
"""
SQL_READ = "SELECT value FROM ingestion_state WHERE key = $1"
# THE FLUSH NEVER TOUCHES TRIP STATE (fleet round 6). The round-2 CAS
# guarded only the scalar 'tripped' — the OLDEST reason — so once trips
# became a dict (round 5), two processes sharing the oldest reason
# passed each other's CAS while their trip SETS diverged, and a full-
# document overwrite silently erased a concurrent process's sticky
# trip (and any operator clear committed mid-flush). Trips now live
# under server-side atomic jsonb operations only (SQL_TRIP/SQL_CLEAR);
# this write merges the payload's top-level keys, adds counter DELTAS
# server-side (concurrent flushes can no longer clobber each other's
# counts), and structurally cannot name 'trips'/'trips_cleared'. If
# the stored trips are non-empty after the merge, armed is forced
# false in the same statement.
# Hostile-shape guards (fleet round 7, minor): a stored value or a
# stored counters field that is valid JSON but not an OBJECT made
# jsonb_set/jsonb_each raise on every flush forever — a silent gauge
# blackhole, because the failure counter's own persist is what fails.
# A non-object doc is replaced by the payload (fail-visible under
# 'state_repaired'); a non-object counters field is treated as empty.
SQL_WRITE = """
INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb)
ON CONFLICT (key) DO UPDATE SET value = (
  SELECT CASE WHEN COALESCE(nv.v->'trips', '{}'::jsonb) <> '{}'::jsonb
              THEN jsonb_set(nv.v, '{armed}', 'false'::jsonb)
              ELSE nv.v END
  FROM (SELECT CASE
          WHEN jsonb_typeof(ingestion_state.value) <> 'object'
          THEN $2::jsonb || jsonb_build_object('state_repaired', true)
          ELSE jsonb_set(
          ingestion_state.value || ($2::jsonb - 'counters'),
          '{counters}',
          (SELECT COALESCE(jsonb_object_agg(m.k, to_jsonb(m.a + m.b)),
                           '{}'::jsonb)
           FROM (SELECT COALESCE(s.k, d.k) AS k,
                        CASE WHEN jsonb_typeof(s.v) = 'number'
                             THEN (s.v)::numeric ELSE 0 END AS a,
                        CASE WHEN jsonb_typeof(d.v) = 'number'
                             THEN (d.v)::numeric ELSE 0 END AS b
                 FROM jsonb_each(CASE
                        WHEN jsonb_typeof(
                          ingestion_state.value->'counters') = 'object'
                        THEN ingestion_state.value->'counters'
                        ELSE '{}'::jsonb END) s(k, v)
                 FULL OUTER JOIN jsonb_each(COALESCE(
                        $2::jsonb->'counters', '{}'::jsonb)) d(k, v)
                   ON s.k = d.k) m)) END AS v) nv)
"""
# A trip is durable the moment it fires: one atomic union (existing
# timestamp wins), disarming in the same statement. The WHERE refuses
# a stale re-persist of a reason an operator already cleared (the
# tombstone is newer than the trip) — the round-6 resurrection class.
# Round 7: a non-object stored doc is replaced wholesale (the trip
# must land — fail-visible via 'state_repaired'); a non-object trips
# field is treated as empty; and when the legacy 'tripped' scalar
# names THIS reason it is stripped in the same statement, completing
# the scalar's migration into the dict.
SQL_TRIP = """
INSERT INTO ingestion_state (key, value)
VALUES ($1, jsonb_build_object(
    'trips', jsonb_build_object($2::text, $3::float8), 'armed', false))
ON CONFLICT (key) DO UPDATE SET value = (
  SELECT CASE WHEN nv.v->>'tripped' = $2::text
              THEN nv.v #- '{tripped}' ELSE nv.v END
  FROM (SELECT CASE
          WHEN jsonb_typeof(ingestion_state.value) <> 'object'
          THEN jsonb_build_object(
            'trips', jsonb_build_object($2::text, $3::float8),
            'armed', false, 'state_repaired', true)
          ELSE jsonb_set(
            jsonb_set(ingestion_state.value, '{trips}',
              jsonb_build_object($2::text, $3::float8)
                || CASE WHEN jsonb_typeof(
                          ingestion_state.value->'trips') = 'object'
                        THEN ingestion_state.value->'trips'
                        ELSE '{}'::jsonb END),
            '{armed}', 'false'::jsonb) END AS v) nv)
WHERE jsonb_typeof(ingestion_state.value) <> 'object'
   OR (CASE WHEN jsonb_typeof(
              ingestion_state.value->'trips_cleared'->$2) = 'number'
            THEN (ingestion_state.value->'trips_cleared'->>$2)::float8
            ELSE -1 END) < $3::float8
"""
# The operator clear: atomic removal of exactly one reason plus a
# PER-REASON tombstone (round 6: the single trip_cleared_* slot forgot
# every clear but the last, so a late-merging process resurrected an
# already-cleared trip). The legacy 'tripped' scalar is removed ONLY
# when it names the cleared reason (fleet round 7: the unconditional
# strip destroyed a DIFFERENT uncleared sticky trip's only durable
# record — a clear must never clear more than the reason it names).
# Non-object docs are refused (0 rows) rather than raising.
SQL_CLEAR = """
UPDATE ingestion_state SET value = jsonb_set(
  (CASE WHEN value->>'tripped' = $2::text
        THEN value #- '{tripped}' ELSE value END)
    #- ARRAY['trips', $2::text],
  '{trips_cleared}',
  (CASE WHEN jsonb_typeof(value->'trips_cleared') = 'object'
        THEN value->'trips_cleared' ELSE '{}'::jsonb END)
    || jsonb_build_object($2::text,
         to_jsonb(extract(epoch from now())::float8)))
WHERE key = $1 AND jsonb_typeof(value) = 'object'
RETURNING value->'trips' AS trips, value->'trips_cleared' AS cleared
"""


class S1Emitter:
    def __init__(self) -> None:
        # BURN-IN BY DEFAULT (owner directive 2026-08-28): the full
        # pipeline runs against live events and counts s1.would_emit,
        # writing NOTHING — emission stays quadruple-gated behind cert
        # GREEN + the 7-day window + the persisted arm + S1_ARM. Set
        # S1_EMITTER=off to silence the emitter entirely.
        self.enabled = os.getenv("S1_EMITTER", "on").lower() not in ("off", "0", "false")
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
        # STICKY TRIPS are a merged, row-specific SET (fleet round 5):
        # a single-slot cell let two live trips CAS-overwrite each
        # other and let one operator clear release BOTH processes. Each
        # reason (e.g. 'uncorroborated:1234') is cleared individually.
        self.trips: dict[str, float] = {}
        self.unjudged_backlog = 0
        self.cert_green = False
        self.cert_reason = "never_checked"
        self.cert_checked_at = 0.0
        self.last_flush_at = 0.0
        self.last_emit_at = 0.0
        self.rpc_tokens = float(RPC_PER_MIN)
        self.rpc_token_at = time.time()
        self.rpc_backoff_until = 0.0
        self.head_advanced_at = 0.0          # last WS-driven head move
        self.last_head_poll_at = 0.0
        self._ts_cache: OrderedDict[tuple[int, str], int] = OrderedDict()
        # burn-in / armed (tx, wallet, asset) marks OUTLIVE the pending
        # entry (round 6: the per-entry set died at pop, so a post-
        # finalize redelivery double-counted would_emit)
        self.counted_marks: OrderedDict[tuple, bool] = OrderedDict()
        self._unpersisted: set[str] = set()  # trips awaiting SQL_TRIP
        self._client: httpx.AsyncClient | None = None
        self._state_loaded = False
        self._pending_arm_at: float | None = None
        self._exch_cache: tuple[tuple, set[str]] | None = None

    # ── tiny helpers ────────────────────────────────────────────────
    def bump(self, key: str, n: int = 1) -> None:
        if n:
            self.deltas[key] = self.deltas.get(key, 0) + n

    def _purge_ts_cache(self, from_blk: int) -> None:
        """A reorg at block B rewrites the whole suffix — drop every
        cached timestamp at or above it (fleet round 7), INCLUDING the
        copies already resolved into pending entries (fleet round 8: an
        entry-local ts survived the purge and a retry pass emitted the
        orphaned block with zero re-verification; dropping it forces
        re-resolution against the buffered hash, which the strict
        resolver then refuses as reorged)."""
        for k in [k for k in self._ts_cache if k[0] >= from_blk]:
            self._ts_cache.pop(k, None)
        for e in self.pending.values():
            for blk in [b for b in e.get("ts", {}) if b >= from_blk]:
                e["ts"].pop(blk, None)

    def _mark_counted(self, tx: str, wallet: str, asset: str) -> bool:
        """True the first time a (tx, wallet, asset) is handled. Lives
        OUTSIDE the pending entry (round 6): the per-entry set died at
        pop, so a post-finalize redelivery — deep-reorg re-add or a
        late duplicate frame — re-counted would_emit for the same fill
        and the burn-in gauge diverged from what armed would do."""
        k = (tx, wallet, asset)
        if k in self.counted_marks:
            self.counted_marks.move_to_end(k)
            return False
        self.counted_marks[k] = True
        while len(self.counted_marks) > COUNTED_CAP:
            self.counted_marks.popitem(last=False)
        return True

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
                # a removed notice rewrites the chain suffix — every
                # cached block timestamp at or above it is now suspect
                # (fleet round 7: a sibling tx borrowing a stale
                # (blk, oldhash) entry skipped the reorg check)
                try:
                    rblk = int(str(log_entry.get("blockNumber", "0x0")), 16)
                except (TypeError, ValueError):
                    rblk = 0
                if rblk:
                    self._purge_ts_cache(rblk)
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
            now = time.time()
            if blk > self.head:
                self.head = blk
                self.head_advanced_at = now   # the WS is our head feed
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
            lix = str(log_entry.get("logIndex", ""))
            if lix and lix in e.setdefault("lix_seen", set()):
                # one duplicated WS delivery of either leg was making
                # classify see two aggs / doubled legs and abstain the
                # whole certified emission (fleet r5)
                self.bump("s1.dup_event")
                return
            if lix:
                e["lix_seen"].add(lix)
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
            lt = doc.get("trips")
            tc = doc.get("trips_cleared")
            cleared = tc if isinstance(tc, dict) else {}
            adopted: dict[str, float] = {}
            if isinstance(lt, dict):
                for k, v in lt.items():
                    at = v if isinstance(v, (int, float)) else 0.0
                    ca = cleared.get(str(k))
                    if isinstance(ca, (int, float)) and ca > at:
                        continue
                    adopted[str(k)] = at
            # The legacy scalar folds IN, it never merely falls back
            # (fleet round 7): the first round-6 trip created the dict
            # and silently shadowed an uncleared pre-round-6 sticky
            # trip out of existence. A live scalar is adopted alongside
            # the dict — tombstone-aware — and queued for durable
            # migration into the dict via _persist_trip.
            legacy = doc.get("tripped")
            if legacy:
                reason = self._trip_str(legacy)
                ca = cleared.get(reason)
                if reason not in adopted and not (
                        isinstance(ca, (int, float)) and ca > 0.0):
                    adopted[reason] = 0.0
                    self._unpersisted.add(reason)
            if adopted:
                self.trips = adopted
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
            self.trips.setdefault("state_corrupt", 0.0)
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
            if green and not self.trips and \
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
        elif (green and not self.trips
              and os.getenv("S1_ARM", "").lower() in ("on", "1", "true")):
            self.armed = True
            self.armed_at = now
            log.warning("S1 ARMED at %s (cert green)", now)

    @property
    def tripped(self) -> str | None:
        """First live trip reason, oldest first (beat/back-compat)."""
        if not self.trips:
            return None
        return min(self.trips, key=self.trips.__getitem__)

    def _trip(self, reason: str) -> None:
        self.armed = False
        if reason in self.trips:
            return                    # idempotent: a re-judged row must
                                      # not double-bump (round 6)
        self.trips[reason] = time.time()
        self.bump("s1.trip." + reason.split(":")[0])
        log.error("S1 STICKY TRIP: %s — manual clear of THIS reason "
                  "required", reason)

    async def _persist_trip(self, pool: Any, reason: str) -> bool:
        """One atomic server-side union — durable the moment the trip
        fires, never carried by the flush (round 6: the flush's scalar
        CAS let concurrent full-document writes erase a trip with zero
        failures on either side). False = not yet durable; the reason
        is retried at every flush until it lands."""
        at = self.trips.get(reason) or time.time()
        try:
            await pool.execute(SQL_TRIP, STATE_KEY, reason, float(at),
                               timeout=6)
        except Exception:  # noqa: BLE001
            self.bump("s1.errors")
            self._unpersisted.add(reason)
            return False
        self._unpersisted.discard(reason)
        return True

    # ── finalize one tx ─────────────────────────────────────────────
    async def _finalize_tx(self, pool: Any, tx: str, e: dict,
                           now: float) -> bool:
        """True = done with this tx (emitted/abstained); False = retry."""
        lst = self.listener() if self.listener else None
        if lst is None:
            return False
        if e["evicted"]:
            return True                       # counted at eviction time
        # DECODE FIRST — it is pure and free (round 6): the buffer is
        # overwhelmingly foreign txs (the WS subscribes by exchange
        # address, not wallet), and resolving their timestamps before
        # the decode discarded them starved the RPC budget 30-to-1 at
        # observed production rates. A foreign tx now costs zero RPC.
        if e.get("recs_n") != len(e["logs"]):
            roster = getattr(lst, "_roster", {}) or {}
            exch = self.exchange_set(lst)
            recs: list[dict] = []
            for le in e["logs"]:
                got, reason, _inv = decode_shadow_views(le, roster, exch)
                if reason is not None:
                    self.bump("s1.abstain.decode_refusal")
                    return True               # one bad event = whole tx
                tps = le.get("topics") or []
                if len(tps) >= 4 and str(tps[2]).lower()[-40:] == \
                        str(tps[3]).lower()[-40:]:
                    self.bump("s1.abstain.self_trade")
                    return True
                for r in got:
                    if not r["block"]:
                        self.bump("s1.abstain.decode_refusal")
                        return True
                    recs.append(r)
            e["recs"], e["recs_n"] = recs, len(e["logs"])
        if not e["recs"]:
            return True                       # foreign tx — zero RPC
        # ts resolution for every block the tx touched (usually one);
        # the block->ts cache means N roster txs in one block cost one
        # RPC, and the cache key includes the blockHash so a reorged
        # sibling can never borrow a stale timestamp
        if e["ts_started"] is None:
            e["ts_started"] = now
        for blk, want_hash in list(e["blocks"].items()):
            if blk in e["ts"]:
                continue
            cached = self._ts_cache.get((blk, want_hash))
            if cached is not None:
                # a cached ts skipped the LIVE canonical-hash check, so
                # the entry is marked: burn-in may use it freely, the
                # ARMED path refuses it structurally (fleet round 7 —
                # a sibling tx borrowing a stale entry after a deep
                # reorg would emit a fill from an orphaned block)
                e["ts"][blk] = cached
                e["ts_cached"] = True
                continue
            if not self._take_token():
                break
            got = await self._resolve_block(blk, want_hash)
            if got == "reorged":
                self.bump("s1.abstain.reorged")
                self._purge_ts_cache(blk)
                return True
            if got is None:
                break
            e["ts"][blk] = got
            self._ts_cache[(blk, want_hash)] = got
            while len(self._ts_cache) > TS_CACHE_CAP:
                self._ts_cache.popitem(last=False)
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
        # the awaits above can interleave a WS reorg burst on the same
        # loop: a removed notice pops the entry, and continuing with
        # the raw dict would emit an orphaned-block fill (fleet r8) —
        # membership is re-checked after every awaited section
        if self.pending.get(tx) is not e:
            return True                # the removed branch counted it
        # freshness on BLOCK time, never first_seen
        newest_ts = max(e["ts"].values())
        if now - newest_ts > EMIT_MAX_AGE_S:
            self.bump("s1.abstain.too_old")
            return True
        for r in e["recs"]:
            r["ts"] = e["ts"].get(r["block"])
        groups = classify_mints(e["recs"])
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
            if not self.armed or not self.cert_green or self.trips:
                # burn-in: idempotent per (tx, wallet, asset) — no view
                # in the mark (a straggler can flip the representative
                # record's view across a retry), the armed path marks
                # too (a disarm mid-retry cannot re-count an emitted
                # fill, fleet r2), and the mark OUTLIVES the entry so a
                # post-finalize redelivery cannot re-count (fleet r6)
                if self._mark_counted(tx, wallet, rec["asset"]):
                    self.bump("s1.would_emit")
                emitted = True                # burn-in counts as handled
                continue
            if e.get("ts_cached"):
                # ARMED emission never trusts a borrowed timestamp: the
                # cache exists for the burn-in gauge's RPC economics;
                # a real emission re-earns its reorg check or abstains
                # (fleet round 7 — the poller carries what this skips)
                self.bump("s1.abstain.ts_cached")
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
                # RETRY, not forfeit (fleet r5): one transient DB error
                # mid-group was permanently losing every remaining
                # market of an armed bundle. Already-ingested records
                # skip via emit_dup on the retry pass.
                self.bump("s1.errors")
                return None
            # a receipt-path row for this (tx, whale, asset) is the
            # key-divergent-twin risk — abstain. Our own earlier s1 row
            # with THIS key is a dup — skip. An s1 row with a DIFFERENT
            # key is a sibling ONLY if THIS entry wrote it (round 2's
            # conflation forfeited the whale's second fill in a bundle;
            # e['ingested_keys'] keeps that case working). Any OTHER s1
            # row is a post-finalize re-entry — a deep-reorg re-add
            # whose new block ts shifts the key, or a straggler leg of
            # an already-emitted tx judged in isolation (fleet round 6,
            # both CRITICAL: key-divergent double emission) — and per
            # this probe's own rule an earlier emitter row equally
            # forbids a second view. The poller carries anything real
            # that this refuses.
            srcs = {str(r["source"]) for r in (rows or [])}
            stored_keys = {str(r["dedupe_key"]) for r in (rows or [])}
            if "chain" in srcs:
                self.bump("s1.abstain.chain_row_preexists")
                continue
            if keys[0][1] in stored_keys:
                self.bump("s1.emit_dup")
                continue
            own_keys = e.setdefault("ingested_keys", set())
            if any(str(r["source"]) == "s1"
                   and str(r["dedupe_key"]) not in own_keys
                   for r in (rows or [])):
                self.bump("s1.abstain.s1_row_preexists")
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
                await self._persist_trip(pool, "key_selfcheck")
                return False
            if self.pending.get(tx) is not e:
                # a removed notice popped this entry during the probe
                # await — the fill is off the canonical chain (r8)
                self.bump("s1.abstain.reorged")
                return False
            try:
                _tid, was_new = await ingest_trade_result(ev)
            except Exception:  # noqa: BLE001
                self.bump("s1.errors")
                return None                  # retry; emit_dup skips any
                                             # record that did commit
            self._mark_counted(tx, wallet, rec["asset"])
            e.setdefault("ingested_keys", set()).add(ev.dedupe_key)
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
    async def _sweep_wallet(self, pool: Any, whale_id: int,
                            address: str, salt: str) -> None:
        try:
            rows = await pool.fetch(SQL_SWEEP, float(CORROBORATE_S),
                                    whale_id, salt, timeout=10)
        except Exception:  # noqa: BLE001
            self.bump("s1.errors")
            return
        confirmed_ids, judged = [], []
        for r in rows or []:
            if r["ok"]:
                confirmed_ids.append(r["id"])
                continue
            ts = r["ts"]
            ts_epoch = (ts.timestamp() if hasattr(ts, "timestamp")
                        else float(ts or 0))
            try:
                ran = await pool.fetchrow(
                    SQL_RECON_SINCE, r["detected_at"], address,
                    ts_epoch - RECON_TS_MARGIN_S,
                    float(RECON_VENUE_LAG_S), timeout=6)
            except Exception:  # noqa: BLE001
                self.bump("s1.errors")
                return
            if ran is None:
                continue               # no run provably covered THIS
                                       # fill's depth and lag — defer
            judged.append(r)
            log.error("S1 UNCORROBORATED row id=%s key=%s — the venue's "
                      "feed never showed this fill", r["id"],
                      str(r["dedupe_key"])[:16])
        # TRIP BEFORE STAMP (round 6): the stamp makes the verdict
        # permanent (the row is never re-judged), so a durable trip
        # must exist first — a crash between stamp and trip silenced
        # the alarm forever. The reverse order is idempotent: if the
        # stamp then fails, the row re-judges and the trip unions.
        ok_judged = []
        for r in judged:
            reason = "uncorroborated:%s" % r["id"]
            self._trip(reason)
            if await self._persist_trip(pool, reason):
                ok_judged.append(r)
        # STAMP BEFORE COUNTING (fleet r4): a failed stamp must not
        # inflate s1.confirmed forever
        ids = confirmed_ids + [r["id"] for r in ok_judged]
        won: set = set()
        if ids:
            try:
                rows_won = await pool.fetch(SQL_MARK, ids, timeout=10)
                won = {r["id"] for r in rows_won or []}
            except Exception:  # noqa: BLE001
                self.bump("s1.errors")
                return                 # nothing counted; rows re-judge
        # count only rows THIS process transitioned (fleet round 8:
        # two overlapping sweeps both counted the same judgment and
        # the server-side delta merge made the inflation durable)
        self.bump("s1.confirmed",
                  len([i for i in confirmed_ids if i in won]))
        self.bump("s1.uncorroborated",
                  len([r for r in ok_judged if r["id"] in won]))

    async def _corroboration_sweep(self, pool: Any) -> None:
        # per-sweep rotation salt (round 7): every wallet and row gets
        # an equal shot at each sweep — deferral can defer, not block
        salt = str(int(time.time()))
        try:
            wallets = await pool.fetch(SQL_SWEEP_WALLETS,
                                       float(CORROBORATE_S), salt,
                                       timeout=10)
            backlog = await pool.fetchval(SQL_BACKLOG,
                                          float(CORROBORATE_S), timeout=10)
        except Exception:  # noqa: BLE001
            self.bump("s1.errors")
            return
        self.unjudged_backlog = int(backlog or 0)
        for w in wallets or []:
            # per-wallet windows (round 6): a whale whose reconciler
            # sweep keeps failing defers only its OWN rows
            await self._sweep_wallet(pool, w["whale_id"], w["address"],
                                     salt)

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
        # trips that failed their immediate SQL_TRIP retry here first —
        # durability is never left to chance across flush cycles
        for reason in list(self._unpersisted):
            await self._persist_trip(pool, reason)
        try:
            raw = await pool.fetchval(SQL_READ, STATE_KEY, timeout=6)
            doc = raw if isinstance(raw, dict) else (
                json.loads(raw) if raw else {})
            if not isinstance(doc, dict):
                doc = {}
        except Exception:  # noqa: BLE001
            self.bump("s1.flush_failures")
            return
        # READ-ONLY trip reconciliation (round 6): the disk trip set is
        # maintained solely by SQL_TRIP/SQL_CLEAR server-side; this
        # merge only decides what THIS process believes. An operator
        # clear names exactly one reason (r5) and its PER-REASON
        # tombstone releases stale in-memory copies in every process —
        # a second clear can no longer forget the first (r6).
        lt = doc.get("trips")
        stored_trips = dict(lt) if isinstance(lt, dict) else {}
        # the legacy scalar FOLDS IN beside the dict (fleet round 7:
        # falling back only when the dict was absent let the first
        # round-6 trip shadow an uncleared pre-round-6 trip)
        if doc.get("tripped"):
            stored_trips.setdefault(self._trip_str(doc["tripped"]), 0.0)
        tc = doc.get("trips_cleared")
        cleared = tc if isinstance(tc, dict) else {}
        merged_trips: dict[str, float] = {}
        for src in (stored_trips, self.trips):
            for reason, at in src.items():
                if not isinstance(at, (int, float)):
                    at = 0.0
                ca = cleared.get(reason)
                if isinstance(ca, (int, float)) and ca > at:
                    continue
                cur = merged_trips.get(reason)
                merged_trips[reason] = at if cur is None else min(cur, at)
        if merged_trips != self.trips:
            adopted = set(merged_trips) - set(self.trips)
            released = set(self.trips) - set(merged_trips)
            if adopted:
                # a foreign trip is global — adopt, never clobber
                self.armed = False
            for reason in released:
                self._unpersisted.discard(reason)
                log.warning("S1 trip '%s' cleared by operator", reason)
            self.trips = merged_trips
        c = doc.get("counters")
        stored_counters = c if isinstance(c, dict) else {}
        snap, self.deltas = self.deltas, {}
        # a pending (not-yet-validated) arm is still an arm on disk —
        # persisting armed=false before validation erased legitimate
        # arms across boot blips (round 3)
        armed_out = bool((self.armed or self._pending_arm_at is not None)
                         and not self.trips)
        # counters ship as DELTAS — the server adds them under the row
        # lock, so concurrent flushes compose instead of clobbering,
        # and the payload structurally cannot name trip state (r6)
        payload = {"counters": snap, "armed": armed_out,
                   "armed_at": (self.armed_at if self.armed
                                else (self._pending_arm_at or 0.0)),
                   "unjudged_backlog": self.unjudged_backlog,
                   "cert_green": self.cert_green,
                   "cert_reason": self.cert_reason,
                   "decoder_fp": DECODER_FP,
                   "updated_at_epoch": now}
        try:
            await pool.execute(SQL_WRITE, STATE_KEY, json.dumps(payload),
                               timeout=6)
        except Exception:  # noqa: BLE001
            # AMBIGUOUS: the write may have committed and lost its ack.
            # Restoring the snap would double-count on the next flush,
            # so the snap is DROPPED — undercount-only, the same choice
            # the shadow's ack protocol makes (round 3)
            self.bump("s1.flush_failures")
            self.bump("s1.snap_dropped_ambiguous")
            return
        # local mirror for the beat: stored view + what we just added
        merged_view = dict(stored_counters)
        for k, v in snap.items():
            base = merged_view.get(k)
            merged_view[k] = (base if isinstance(base, (int, float))
                              else 0) + v
        self.counters = merged_view

    def _should_poll_head(self, now: float) -> bool:
        """The poll is for when the logs-only WS goes QUIET (round 6:
        the un-gated version fired every tick under live traffic —
        there is always a tx younger than CONFIRM_DEPTH — and starved
        ts resolution of the shared RPC budget). Someone must be
        waiting on confirmation, the WS must not have advanced the
        head recently, and polls are spaced HEAD_POLL_MIN_S apart."""
        if now - self.head_advanced_at <= HEAD_QUIET_S:
            return False
        if now - self.last_head_poll_at < HEAD_POLL_MIN_S:
            return False
        return any(
            max(e["blocks"], default=0) + CONFIRM_DEPTH > self.head
            for e in self.pending.values() if e["blocks"])

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
                if self._should_poll_head(now) and self._take_token():
                    self.last_head_poll_at = now
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
