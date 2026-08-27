"""Phase S0 — shadow per-event decoder for the 0xd543adfd fill event.

Observes every 0xd543adfd log the listener receives, decodes it per-event
with the empirically verified v2 layout (chain.py:68-81, tied out against
p382s.log:4452-4476 to the cent), INGESTS NOTHING, and measures — against
the poller's authoritative rows in the trades table — whether a per-event
Path A could replace the v3 receipt path without changing one ingested
field. The decisive output is a dedupe-outcome SIMULATION: the exact
would-be insert-key set per tx under each candidate ingest policy, tested
against every real poll row's stored dedupe_key.

Hard rules (each enforced by backend/tests/test_shadow_v2.py):
  * shadow_observe: sync, zero I/O, never raises.
  * no pipeline/bus import; never calls ingest_trade; never writes
    trades/notification_outbox; never touches BlockTimestampCache values.
  * one reconcile task per process; delta-only writer-fenced flushes.
  * all in-memory structures FIFO-bounded with counted eviction.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
import weakref
from collections import OrderedDict, deque
from decimal import ROUND_HALF_UP, Decimal
from statistics import median
from typing import Any

import httpx

from .dedupe import _num, make_dedupe_key  # byte-identical normalization on purpose

log = logging.getLogger(__name__)

STATE_KEY = "shadow_v2_fill"
SCHEMA_V = 1
WRITER_ID = uuid.uuid4().hex[:12]

PENDING_CAP = 8000        # prod: pend=1642 at 45min uptime BEFORE deferred
                          # finalize; overflow is HEALTH, so undersizing it
                          # would reset the health window on routine volume
SEEN_EVENTS_CAP = 8192
SEEN_TX_CAP = 16384
TS_CACHE_CAP = 1024
PER_WHALE_CAP = 32
EXAMPLES_CAP = 12
GAPS_CAP = 64             # sized with time-pruning to outlive the reverse lookback
LAG_RESERVOIR = 512
FINALIZED_CAP = 8192      # tombstones: a finalized (tx, wallet) never re-counts
WATCH_CAP = 4096          # early-finalized txs stay row-watched to the deadline

MATURE_S = 900.0          # venue publication lag measured 90-500s; evaluate past it
ORPHAN_FINAL_S = 4200.0   # one deferral window covering the hourly reconciler
REPLAY_STALE_S = 7200.0   # replayed events older than the join window: drop, count
TS_FIRST_TRY_S = 1.5      # hot-path-like first resolution attempt
TS_RESOLVE_BUDGET_S = 12.0  # a trickling RPC must never starve reconcile/flush
TICK_S = 2.0
RECONCILE_EVERY = 30      # ticks  -> ~60s
REVERSE_EVERY = 5         # reconciles -> ~5min
REVERSE_LOOKBACK_S = 1200.0
MAX_TX_PER_RECONCILE = 200
RPC_PER_TICK = 4
RPC_PER_MIN = 40
RPC_BACKOFF_S = 120.0
DIVERGE_LOG_PER_CYCLE = 10
LATE_POLL_ROW_S = 900.0   # detected_at - ts beyond this = reconciler artifact
# The keepalive must keep a HEALTHY writer's row strictly younger than the
# takeover threshold — 300/300 let a second process steal a live row in the
# (300, ~360)s phase window and ping-pong it forever.
FLUSH_IDLE_S = 180.0      # max idle between keepalive flushes
TAKEOVER_S = 450.0        # a row older than this may be taken over

# counters that reset the measurement window when they move
GATING = ("div.side", "div.asset", "div.size", "div.price", "div.ts",
          "key_impl_mismatch", "agg_tieout_fail", "mint_side_anomaly",
          "per_exec_ambiguous", "orphan_no_row", "orphan_agg_no_row",
          "orphan_chain_mismatch",
          "poll_uncovered_unexplained", "ts_never_resolved_live")
# counters that reset the instrument-health window when they move
HEALTH = ("shadow_errors", "cycle_errors", "db_errors", "flush_failures",
          "writer_conflict", "pending_overflow_dropped", "log_index_missing",
          "ts_resolve_timeout", "ack_dropped_unverified", "corrupt_reset",
          "watch_expired_unfetched", "finalized_evicted")
# watermarked at window reset so the probe reads deltas-since-window
# NB: the residual-dup counters are deliberately NOT here — they gate, and
# a gating counter that is also watermarked reads Δ==0 in the very flush
# that moved it (round-3 kill): they render cumulatively instead.
VOLUME_KEYS = ("compared_execs", "sim_exec_suppressed", "sim_agg_suppressed",
               "sim_exec_supp_hup_only", "mint_transformed", "poll_rows_seen",
               "decoded_exec_counter", "decoded_exec_owner", "decoded_agg",
               "sim_ven_suppressed")

SQL_TX = """
SELECT lower(tx_hash) AS tx, whale_id, asset, side,
       size::text  AS size, price::text AS price,
       extract(epoch FROM ts)::bigint AS ts_epoch, source, dedupe_key,
       extract(epoch FROM detected_at) AS det_epoch
FROM trades
WHERE source IN ('chain','poll')                      -- partial idx 009 predicate
  AND detected_at > now() - interval '2 hours'
  AND lower(tx_hash) = ANY($1::text[])
"""

SQL_REVERSE = """
SELECT lower(tx_hash) AS tx, whale_id, source, dedupe_key,
       extract(epoch FROM ts)::bigint AS ts_epoch
FROM trades
WHERE source IN ('chain','poll')
  AND detected_at > now() - interval '20 minutes'
  AND whale_id = ANY($1::bigint[])
"""

# The upsert is CONDITIONAL so two processes racing past the in-python
# fence cannot silently discard each other's merge: the write lands only
# if the row is ours, unowned, or stale. CASE guarantees the epoch cast
# is only attempted on a numeric value (a corrupt row must not abort the
# statement — it matches the writer-IS-NULL arm after corrupt_reset).
SQL_FLUSH = """
INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb)
ON CONFLICT (key) DO UPDATE SET value = $2::jsonb
WHERE ingestion_state.value->>'writer' IS NULL
   OR ingestion_state.value->>'writer' = $3
   OR (CASE WHEN jsonb_typeof(ingestion_state.value->'updated_at_epoch') = 'number'
            THEN (ingestion_state.value->>'updated_at_epoch')::float < $4
            ELSE true END)
"""


def _addr(topic: Any) -> str:
    return "0x" + str(topic)[-40:].lower()


def decode_shadow_views(log_entry: dict[str, Any], roster: dict[str, dict],
                        exchange_addrs: set[str]) -> tuple[list[dict], str | None, bool]:
    """Pure per-event decode of BOTH views of one 0xd543adfd log.

    Returns (records, refusal_reason, roster_involved). Layout per the
    verified spec: t1 orderHash, t2 order owner, t3 counterparty-or-
    exchange; w0 side flag (0=owner bought), w1 token id, w2 gave,
    w3 got, w4 fee in its own word. The aggregate/taker view is
    self-describing: topics[3] == the emitting exchange address.
    THIS FUNCTION IS THE S1 PRODUCTION DECODER (flip contract C1)."""
    topics = log_entry.get("topics") or []
    if len(topics) < 4:
        return [], "short_topics", False
    owner, cpty = _addr(topics[2]), _addr(topics[3])
    involved = owner in roster or cpty in roster
    data = str(log_entry.get("data", "0x"))[2:]
    if len(data) < 4 * 64:
        return [], "short_data", involved
    words = [int(data[i * 64:(i + 1) * 64], 16)
             for i in range(min(len(data) // 64, 5))]
    side_flag, token, gave, got = words[0], words[1], words[2], words[3]
    fee = words[4] if len(words) > 4 else 0
    if side_flag not in (0, 1):
        return [], "bad_flag", involved
    if gave == 0 or got == 0:
        return [], "zero_amount", involved
    if side_flag == 0:
        o_side, usdc_units, size_units = "BUY", gave, got
    else:
        o_side, size_units, usdc_units = "SELL", gave, got
    price_r = round(usdc_units / size_units, 6)   # bit-exact replica of chain.py:205
    price_h = (Decimal(usdc_units) / Decimal(size_units)
               ).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    in_r, in_h = 0 < price_r < 1, 0 < price_h < 1
    if not in_r and not in_h:
        # refuse only when BOTH rounding variants are out of range — a
        # round()-ties-to-even zero must not hide a fill HALF_UP keeps
        return [], "price_oob", involved
    variant_disagree = in_r != in_h
    emitter = str(log_entry.get("address", "")).lower()
    tx = str(log_entry.get("transactionHash", "")).lower()
    try:
        blk = int(str(log_entry.get("blockNumber", "0x0")), 16)
    except ValueError:
        blk = 0
    recs: list[dict] = []

    def _rec(wallet: str, view: str, side: str, f_units: int) -> dict:
        w = roster[wallet]
        return {"tx": tx, "block": blk, "wallet": wallet, "whale_id": w["id"],
                "username": w.get("username"), "view": view, "side": side,
                "owner_side": o_side, "asset": str(token),
                "size_units": size_units, "usdc_units": usdc_units,
                "fee_units": f_units, "emitter": emitter}

    if owner in roster:
        is_agg = cpty == emitter or cpty in exchange_addrs
        r = _rec(owner, "agg" if is_agg else "exec_owner", o_side, fee)
        if is_agg and cpty != emitter:
            r["agg_by_set_only"] = True
        recs.append(r)
    if (cpty in roster and cpty != emitter
            and cpty not in exchange_addrs):
        # cpty == owner is a SELF-TRADE: the venue books both sides, so
        # both view records exist and each is key-tested independently —
        # suppressing one fabricated div.side / phantom residuals.
        recs.append(_rec(cpty, "exec_counter",
                         "SELL" if o_side == "BUY" else "BUY", 0))
    if variant_disagree:
        for r in recs:
            r["price_variant_disagree"] = True
    return recs, None, involved


def classify_mints(recs_for_tx: list[dict]) -> dict[str, dict]:
    """Group one tx's records per wallet and resolve exec_counter records
    against the wallet's aggregate event. Returns wallet -> {"aggs": [...],
    "execs": [...], "flags": {counter: n}} where execs contains exec_owner,
    normal exec_counter, and mint-TRANSFORMED records:
      mint leg (maker traded the COMPLEMENT token, matched via mint/merge):
      asset := agg.asset, side := agg.side,
      usdc_units := size_units - maker_usdc_units   (exact: one share pair
      costs exactly 1.000000 collateral). Detected by asset != agg.asset
      with the maker's raw side EQUAL to the aggregate side.
    Anomalies are counted, never guessed through. Pure."""
    out: dict[str, dict] = {}
    for wallet in {r["wallet"] for r in recs_for_tx}:
        aggs = [r for r in recs_for_tx if r["wallet"] == wallet and r["view"] == "agg"]
        raw = [r for r in recs_for_tx if r["wallet"] == wallet
               and r["view"] in ("exec_owner", "exec_counter")]
        agg_assets = {a["asset"] for a in aggs}
        execs, flags = [], {}
        dropped: list[dict] = []
        for r in raw:
            if r["view"] == "exec_counter" and r["asset"] not in agg_assets:
                if not aggs:
                    # No aggregate view exists for this wallet (whale-vs-
                    # whale maker match, or the taker log was lost). The
                    # counter decode is self-contained, so the record
                    # STAYS an exec and gets key-tested against the
                    # wallet's poll rows — dropping it here would hide a
                    # whole fill class from the simulation. agg_missing
                    # remains as the diagnostic count of the condition.
                    flags["agg_missing"] = flags.get("agg_missing", 0) + 1
                    execs.append(r)
                    continue
                if len(aggs) > 1:
                    flags["per_exec_ambiguous"] = flags.get("per_exec_ambiguous", 0) + 1
                    dropped.append(r)
                    continue
                a = aggs[0]
                if r["owner_side"] != a["side"] or r["usdc_units"] >= r["size_units"]:
                    flags["mint_side_anomaly"] = flags.get("mint_side_anomaly", 0) + 1
                    dropped.append(r)
                    continue
                execs.append(dict(r, view="exec_mint", asset=a["asset"], side=a["side"],
                                  usdc_units=r["size_units"] - r["usdc_units"],
                                  _mint_raw=r))
                flags["mint_transformed"] = flags.get("mint_transformed", 0) + 1
            else:
                execs.append(r)
        g = {"aggs": aggs, "execs": execs, "flags": flags,
             "dropped": dropped}
        # THE TRANSFORM MUST PROVE ITSELF (round-3 kill): the mint
        # predicate cannot tell a complement-token mint leg from a fill
        # in a DIFFERENT market whose own agg event was lost — token ids
        # are opaque and usdc<size holds at every price<1. A transform
        # that does not tie out integer-exact is a guess: revert to the
        # RAW records (they stay key-testable against their real poll
        # rows) and count mint_unresolved instead of fabricating
        # divergences and a tie-out failure on a self-consistent tx.
        if flags.get("mint_transformed") and agg_tieout(g) != "ok":
            # Prove per-SUBSET before reverting everything: one genuine
            # complement-token mint plus one cross-market guess must
            # keep the proven transform and revert only the guess —
            # all-or-nothing revert fabricated divergences on the
            # genuine leg (round-4 fleet). k is tiny; the unique subset
            # that ties out integer-exact wins; ambiguity reverts all.
            from itertools import combinations
            tr_idx = [i for i, e in enumerate(execs) if e.get("_mint_raw")]
            winner = None
            for keep_n in range(len(tr_idx) - 1, -1, -1):
                ok_subsets = []
                for keep in combinations(tr_idx, keep_n):
                    kset = set(keep)
                    trial = [e if (i in kset or not e.get("_mint_raw"))
                             else e["_mint_raw"] for i, e in enumerate(execs)]
                    if agg_tieout({"aggs": aggs, "execs": trial,
                                   "flags": {}}) == "ok":
                        ok_subsets.append(kset)
                if len(ok_subsets) == 1:
                    winner = ok_subsets[0]
                    break
                if ok_subsets:
                    break   # ambiguous at this cardinality: revert all
            kept = winner or set()
            g["execs"] = [e if (i in kept or not e.get("_mint_raw"))
                          else e["_mint_raw"] for i, e in enumerate(execs)]
            reverted = len(tr_idx) - len(kept)
            if reverted:
                flags["mint_unresolved"] = reverted
            if kept:
                flags["mint_transformed"] = len(kept)
            else:
                flags.pop("mint_transformed", None)
        for e in g["execs"]:
            e.pop("_mint_raw", None)
        out[wallet] = g
    return out


def agg_tieout(group: dict) -> str:
    """'ok' | 'fail' | 'skip' — mint-aware integer identity: the taker
    aggregate must equal the sum of its per-exec legs exactly, with mint
    legs contributing their TRANSFORMED usdc (s - maker_usdc). Pure."""
    aggs = group["aggs"]
    if len(aggs) != 1 or group["flags"].get("per_exec_ambiguous") \
            or group["flags"].get("mint_side_anomaly") or group["flags"].get("agg_missing") \
            or (group["flags"].get("mint_unresolved")
                and not group["flags"].get("mint_transformed")):
        return "skip"
    a = aggs[0]
    # legs of THIS aggregate only: a cross-market fill sharing the tx
    # (its own agg lost) is not a leg of this market's aggregate, and
    # summing it broke provable ties (round-4 fleet)
    legs = [e for e in group["execs"]
            if e["view"] in ("exec_counter", "exec_mint")
            and e["asset"] == a["asset"]]
    if not legs:
        return "skip"
    if (sum(e["size_units"] for e in legs) == a["size_units"]
            and sum(e["usdc_units"] for e in legs) == a["usdc_units"]):
        return "ok"
    return "fail"


def rec_prices(rec: dict) -> tuple[Any, Any]:
    """(price_round, price_halfup) — the naive-S1 float rounding
    (chain.py:205 replica) and the Decimal HALF_UP variant. Both are
    simulated so the rounding question is settled in one window."""
    pr = round(rec["usdc_units"] / rec["size_units"], 6)
    ph = (Decimal(rec["usdc_units"]) / Decimal(rec["size_units"])
          ).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    return pr, ph


def rec_keys(rec: dict) -> list[tuple[str, str]]:
    """[('round', key)] plus ('hup', key) when the variants differ.
    Requires rec['ts']. Size is exact from integer micro-units."""
    size = Decimal(rec["size_units"]) / Decimal(10 ** 6)
    pr, ph = rec_prices(rec)
    out = [("round", make_dedupe_key(rec["tx"], rec["asset"], rec["side"],
                                     size, pr, rec["ts"]))]
    if _num(pr) != _num(ph):
        out.append(("hup", make_dedupe_key(rec["tx"], rec["asset"], rec["side"],
                                           size, ph, rec["ts"])))
    return out


class ShadowV2:
    def __init__(self) -> None:
        self.enabled = os.getenv("SHADOW_V2", "on").lower() not in ("off", "0", "false")
        self.listener: Any = None                     # weakref.ref
        self.http_url = ""
        self.commit = (os.environ.get("RENDER_GIT_COMMIT") or "?")[:7]
        self.pending: OrderedDict[tuple, dict] = OrderedDict()
        self.seen_events: OrderedDict[tuple, float] = OrderedDict()
        self.seen_tx: OrderedDict[str, float] = OrderedDict()
        self.ts_cache: OrderedDict[int, int] = OrderedDict()
        self.reverse_counted: OrderedDict[str, float] = OrderedDict()
        self.deltas: dict[str, int] = {}
        self.per_whale_mem: OrderedDict[str, dict] = OrderedDict()  # wallet -> lag deques
        self.examples: deque = deque(maxlen=EXAMPLES_CAP)
        self.lags: deque = deque(maxlen=LAG_RESERVOIR)
        self.gaps: deque = deque(maxlen=GAPS_CAP)
        self.last_observe_at = 0.0
        self.last_ensure_at = 0.0
        self.last_flush_ok = 0.0
        self.boot_at = time.time()      # reverse probe warms up past this
        self._await_ack: tuple[str, dict] | None = None  # (snap_id, snap)
        self.finalized: OrderedDict[tuple, float] = OrderedDict()  # tombstones
        self.watch: OrderedDict[tuple, dict] = OrderedDict()  # late-row watches
        self.rpc_tokens = float(RPC_PER_MIN)
        self.rpc_token_at = time.time()
        self.rpc_backoff_until = 0.0
        self.tick_n = 0
        self.reconcile_n = 0
        self.booted = False
        self._client: httpx.AsyncClient | None = None
        self._exch_cache: tuple[int, set[str]] | None = None

    # ── tiny helpers ────────────────────────────────────────────────
    def bump(self, key: str, n: int = 1) -> None:
        if n:
            self.deltas[key] = self.deltas.get(key, 0) + n

    def exchange_set(self, listener: Any) -> set[str]:
        addrs = getattr(listener, "_addresses", None) or []
        key = tuple(addrs)   # value key: id() can be recycled by the allocator
        if self._exch_cache and self._exch_cache[0] == key:
            return self._exch_cache[1]
        s = {str(a).lower() for a in addrs}
        self._exch_cache = (key, s)
        return s

    def note_example(self, kind: str, **kw: Any) -> None:
        self.examples.append({"kind": kind, "at": int(time.time()), **kw})

    def whale_mem(self, wallet: str, username: str | None) -> dict:
        m = self.per_whale_mem.get(wallet)
        if m is None:
            m = {"username": username, "sh": deque(maxlen=64), "po": deque(maxlen=64)}
            self.per_whale_mem[wallet] = m
            while len(self.per_whale_mem) > PER_WHALE_CAP:
                self.per_whale_mem.popitem(last=False)
        else:
            self.per_whale_mem.move_to_end(wallet)   # LRU, not FIFO
        return m

    # ── ts resolution (own client, own cache, explicit None) ────────
    async def _resolve_ts(self) -> None:
        now = time.time()
        if not self.http_url or now < self.rpc_backoff_until:
            return
        due: dict[int, list[dict]] = {}
        for r in self.pending.values():
            if r["ts"] is None and r["block"] and now - r["seen_at"] >= TS_FIRST_TRY_S:
                due.setdefault(r["block"], []).append(r)
        for blk in list(due):
            if blk in self.ts_cache:
                self._assign_ts(blk, self.ts_cache[blk], due.pop(blk))
        if not due:
            return
        self.rpc_tokens = min(float(RPC_PER_MIN), self.rpc_tokens
                              + (now - self.rpc_token_at) * RPC_PER_MIN / 60.0)
        self.rpc_token_at = now
        for blk in sorted(due)[:RPC_PER_TICK]:
            if self.rpc_tokens < 1:
                self.bump("ts_resolve_deferred")
                break
            self.rpc_tokens -= 1
            ts = await self._fetch_block_ts(blk, due[blk])
            if ts is not None:
                self._assign_ts(blk, ts, due[blk])

    async def _fetch_block_ts(self, blk: int, recs: list[dict]) -> int | None:
        first = all(r["ts_tries"] == 0 for r in recs)
        for r in recs:
            r["ts_tries"] += 1
        self.bump("ts_rpc_calls")
        try:
            if self._client is None:
                self._client = httpx.AsyncClient(timeout=4)
            resp = await self._client.post(self.http_url, json={
                "jsonrpc": "2.0", "id": 1, "method": "eth_getBlockByNumber",
                "params": [hex(blk), False]})
            if resp.status_code == 429 or resp.status_code >= 500:
                self.rpc_backoff_until = time.time() + RPC_BACKOFF_S
                self.bump("ts_rpc_backoffs")
                return None
            raw = ((resp.json() or {}).get("result") or {}).get("timestamp")
            if raw is None:
                # the condition chain.py:291 papers over with wallclock —
                # the shadow records the truth instead of inventing one
                self.bump("ts_rpc_empty_first" if first else "ts_rpc_empty_retry")
                return None
            return int(str(raw), 16)
        except Exception:  # noqa: BLE001
            self.bump("ts_rpc_errors")
            return None

    def _assign_ts(self, blk: int, ts: int, recs: list[dict]) -> None:
        self.ts_cache[blk] = ts
        while len(self.ts_cache) > TS_CACHE_CAP:
            self.ts_cache.popitem(last=False)
        now = time.time()
        for r in recs:
            r["ts"] = ts
            if not r["replay"]:
                age = now - r["seen_at"]
                b = ("le3s" if age <= 3 else "le6s" if age <= 6 else
                     "le15s" if age <= 15 else "le60s" if age <= 60 else "gt60s")
                self.bump("ts_avail." + b)
            if now - ts > REPLAY_STALE_S:
                self.pending.pop((r["tx"], r["log_index"], r["wallet"], r["view"]), None)
                self.bump("replay_stale_dropped" if r["replay"] else "stale_live_dropped")

    # ── the reconciler ──────────────────────────────────────────────
    async def tick(self) -> None:
        # tick_n advances FIRST so a hanging resolver (httpx read timeout
        # is between-bytes; a trickling provider outlives any deadline)
        # can never starve reconcile/flush — the instrument must keep
        # measuring during exactly the degraded regimes it characterizes.
        self.tick_n += 1
        due_reconcile = self.tick_n % RECONCILE_EVERY == 0
        try:
            await asyncio.wait_for(self._resolve_ts(),
                                   timeout=TS_RESOLVE_BUDGET_S)
        except asyncio.TimeoutError:
            self.bump("ts_resolve_timeout")
            self.rpc_backoff_until = time.time() + RPC_BACKOFF_S
        if due_reconcile:
            await self._reconcile()

    async def _reconcile(self) -> None:
        from ..db import get_pool  # lazy: no import cycle, no import-time DB
        self.reconcile_n += 1
        now = time.time()
        by_tx: dict[str, list[dict]] = {}
        for r in self.pending.values():
            by_tx.setdefault(r["tx"], []).append(r)
        mature = sorted(
            (tx for tx, rs in by_tx.items()
             if now - min(x["seen_at"] for x in rs) >= MATURE_S),
            key=lambda tx: min(x["seen_at"] for x in by_tx[tx]))[:MAX_TX_PER_RECONCILE]
        pool = None
        try:
            pool = await get_pool()
        except Exception:  # noqa: BLE001
            self.bump("db_errors")
        # earliest-expiring watches fetch FIRST: oldest-first starved a
        # short-lived watch behind 100 older ones until it expired with
        # its late residual unscored and uncounted (round-4 fleet)
        watch_order = sorted(self.watch.items(), key=lambda kv: kv[1]["until"])
        watch_txs = []
        for (t, _wal), _w in watch_order:
            if t not in mature and t not in watch_txs:
                watch_txs.append(t)
            if len(watch_txs) >= 100:
                break
        fetch_txs = mature + watch_txs
        if pool is not None and fetch_txs:
            try:
                rows = await pool.fetch(SQL_TX, fetch_txs, timeout=5.0)
                fetch_set = set(fetch_txs)
                for (t, _wal), w_e in self.watch.items():
                    if t in fetch_set and (
                            now < w_e["until"]
                            or now - w_e.get("armed_at", now) < 90):
                        # only a fetch that actually RETURNED counts, and
                        # never the terminal pass of a LONG-starved watch
                        # — but a watch born with less than one reconcile
                        # spacing of life has no pre-expiry opportunity,
                        # and its successful terminal fetch is coverage,
                        # not starvation
                        w_e["fetched"] = True
                rows_by: dict[tuple, list[dict]] = {}
                for row in rows:
                    rows_by.setdefault((row["tx"], row["whale_id"]), []).append(dict(row))
                logged = 0
                for tx in mature:
                    logged = self._reconcile_tx(tx, by_tx[tx], rows_by, now, logged)
                self._watch_pass(rows_by, now)
            except Exception:  # noqa: BLE001
                self.bump("db_errors")
                log.info("SHADOW-V2 join failed", exc_info=True)
        if pool is not None and self.reconcile_n % REVERSE_EVERY == 0:
            try:
                await self._reverse_probe(pool)
            except Exception:  # noqa: BLE001
                self.bump("db_errors")
        if pool is not None:
            await self._flush(pool)

    def _reconcile_tx(self, tx: str, recs: list[dict],
                      rows_by: dict, now: float, logged: int) -> int:
        oldest = now - min(r["seen_at"] for r in recs)
        unresolved = [r for r in recs if r["ts"] is None]
        if unresolved:
            if oldest < ORPHAN_FINAL_S:
                return logged  # not evaluable yet — wait for ts
            # deadline: count ONLY the truly unresolved records — the old
            # per-tx bump counted resolved siblings as never-resolved
            # (GATING over-report) and discarded their testable evidence
            for r in unresolved:
                self.pending.pop((r["tx"], r["log_index"], r["wallet"], r["view"]), None)
                self.bump("ts_never_resolved_replay" if r["replay"]
                          else "ts_never_resolved_live")
            recs = [r for r in recs if r["ts"] is not None]
            if not recs:
                return logged
        groups = classify_mints(recs)
        by_wallet: dict[str, list[dict]] = {}
        for r in recs:
            by_wallet.setdefault(r["wallet"], []).append(r)
        for wallet, g in groups.items():
            if (tx, wallet) in self.finalized:
                # a re-delivered event past seen_events eviction re-pended
                # an already-counted fill: drop it, never count twice.
                # But a genuinely NEW late fill event of a watched
                # (tx, wallet) lands here too — merge its keys into the
                # watch FIRST so its poll row scores suppressed instead
                # of a fabricated gating residual (round-4 fleet). If the
                # row landed before this merge, the residual already
                # counted — conservative direction, accepted.
                w = self.watch.get((tx, wallet))
                if w is not None:
                    # merge the CLASSIFIED records (a mint leg's venue row
                    # arrives in TRANSFORMED shape) PLUS the classifier-
                    # DROPPED raws: an anomaly-flagged fill is provably
                    # NOT a mint leg, so the venue books its RAW shape —
                    # unmerged, its late row fabricated a gating residual.
                    # Its flags stay uncounted here (a redelivery must
                    # never double-bump GATING) — undercount, by design.
                    for c in g["execs"] + g["aggs"]:
                        if c.get("ts") is None:
                            continue
                        tgt = w["agg_keys"] if c["view"] == "agg" else w["exec_keys"]
                        for var, key in rec_keys(c):
                            tgt.setdefault(key, var)
                        if c["view"] == "agg":
                            w["agg_assets"].add(c["asset"])
                            w["has_aggs"] = True   # the ven arm's mode
                            # switch must follow the merge (mini-round)
                        else:
                            w["has_execs"] = True
                            w.setdefault("exec_assets", set()).add(c["asset"])
                    for c in g.get("dropped", []):
                        # dropped raws are outside the flip's insert set:
                        # their rows score dropped coverage, not
                        # suppression (semantic parity with first delivery)
                        if c.get("ts") is not None:
                            for _var, key in rec_keys(c):
                                w.setdefault("dropped_keys", set()).add(key)
                for c in by_wallet[wallet]:
                    self.pending.pop((c["tx"], c["log_index"], c["wallet"], c["view"]), None)
                self.bump("refinalize_blocked")
                continue
            whale_id = (g["execs"] + g["aggs"])[0]["whale_id"] if (g["execs"] or g["aggs"]) else None
            rows = rows_by.get((tx, whale_id), [])
            poll_rows = [x for x in rows if x["source"] == "poll"]
            chain_rows = [x for x in rows if x["source"] == "chain"]
            logged = self._match_wallet(tx, wallet, g, by_wallet[wallet],
                                        poll_rows, chain_rows, oldest, now, logged)
        return logged

    def _match_wallet(self, tx: str, wallet: str, g: dict, all_recs: list[dict],
                      poll_rows: list[dict], chain_rows: list[dict],
                      oldest: float, now: float, logged: int) -> int:
        execs, aggs = g["execs"], g["aggs"]
        agg_assets_set = {a["asset"] for a in aggs}
        # a classifier-DROPPED record is outside the flip's insert set
        # (S1 ingests the post-classification set), so its venue row is
        # neither suppressed nor a double-ingest — it must never score a
        # gating residual, on ANY delivery path (rounds 6+7)
        dropped_keys: set = set()
        for c in g.get("dropped", []):
            if c.get("ts") is not None:
                for _var, key in rec_keys(c):
                    dropped_keys.add(key)
        ins_exec: dict[str, tuple[str, dict]] = {}
        for c in execs:
            for variant, key in rec_keys(c):
                ins_exec.setdefault(key, (variant, c))
        ins_agg: dict[str, tuple[str, dict]] = {}
        for a in aggs:
            for variant, key in rec_keys(a):
                ins_agg.setdefault(key, (variant, a))
        # Scan first, COUNT NOTHING: a mature tx is re-scanned every ~60s
        # until it finalizes, and per-pass bumping inflated tieout/pw/
        # poll_mult/flag counters up to ~55x per tx. All counting for a
        # (tx, wallet) happens exactly once, at the pass that finalizes it.
        consumed: set[int] = set()
        hits: list[tuple[dict, Any, Any]] = []
        residual_rows: list[dict] = []
        dropped_rows: set[str] = set()
        for row in poll_rows:
            hit = ins_exec.get(row["dedupe_key"])
            hit_a = ins_agg.get(row["dedupe_key"])
            if hit is None and hit_a is None and row["dedupe_key"] in dropped_keys:
                dropped_rows.add(row["dedupe_key"])
                continue   # scored as dropped coverage, never residual
            hits.append((row, hit, hit_a))
            if hit:
                consumed.add(id(hit[1]))
            elif not hit_a:
                residual_rows.append(row)
            # an agg-matched row is GRANULARITY evidence, not a field
            # divergence: feeding it to the diagnoser fabricated div.size
            # (GATING/N6) on perfectly-decoded aggregate-granularity txs
        # FINALIZE only on full coverage or the deadline. "Any row landed"
        # finalized on the FIRST row and popped everything — a late-landing
        # divergent sibling (venue lag has a documented >15min tail) was
        # then never key-tested: the one shape that could read GREEN on
        # bad evidence. Rows stay fetchable for 2h, past ORPHAN_FINAL_S.
        poll_keys = {r["dedupe_key"] for r in poll_rows}
        all_covered = bool(execs) and all(
            any(k in poll_keys for _, k in rec_keys(c)) for c in execs)
        finalize = (oldest >= ORPHAN_FINAL_S or all_covered
                    or (not execs and bool(poll_rows)))
        if not finalize:
            return logged   # evidence incomplete — wait, count nothing
        self.finalized[(tx, wallet)] = now
        horizon = now - (ORPHAN_FINAL_S + REPLAY_STALE_S)
        while self.finalized and next(iter(self.finalized.values())) < horizon:
            self.finalized.popitem(last=False)   # age-expired by design
        while len(self.finalized) > FINALIZED_CAP:
            self.finalized.popitem(last=False)
            self.bump("finalized_evicted")   # a live tombstone lost = a
            # redelivery can double-count; HEALTH so the window shows it
        if oldest < ORPHAN_FINAL_S:
            # EARLY finalize: coverage was exec->row only, so a poll row
            # landing after this pass (the documented >15min lag tail)
            # would otherwise never be key-tested — the round-2 critical
            # surviving through the coverage shortcut. The tx stays
            # row-WATCHED until the deadline: every later-landing key is
            # still scored, so both residual counters keep their teeth.
            wid = (execs + aggs)[0]["whale_id"] if (execs or aggs) else None
            self.watch[(tx, wallet)] = {
                "until": now + (ORPHAN_FINAL_S - oldest),
                "whale_id": wid,
                "exec_keys": {key: var for key, (var, _c) in ins_exec.items()},
                "agg_keys": {key: var for key, (var, _a) in ins_agg.items()},
                "seen": {r["dedupe_key"] for r in poll_rows}
                        | {r["dedupe_key"] for r in chain_rows},
                "has_execs": bool(execs), "has_aggs": bool(aggs),
                "agg_assets": set(agg_assets_set),
                "exec_assets": {c["asset"] for c in execs},
                "dropped_keys": set(dropped_keys),
                "armed_at": now,
            }
            while len(self.watch) > WATCH_CAP:
                self.watch.popitem(last=False)
                self.bump("watch_evicted")
        for k, n in g["flags"].items():
            self.bump(k, n)
            if k in ("mint_side_anomaly", "per_exec_ambiguous"):
                self.note_example(k, tx=tx[:14], wallet=wallet[:12])
        tie = agg_tieout(g)
        if tie != "skip":
            self.bump("agg_tieout_" + tie)
            if tie == "fail":
                self.note_example("agg_tieout_fail", tx=tx[:14], wallet=wallet[:12])
                log.warning("SHADOW-V2 AGG-TIEOUT tx=%s wallet=%s", tx[:14], wallet[:12])
        prim = [rec_keys(c)[0][1] for c in execs]
        self.bump("dup_exec", len(prim) - len(set(prim)))
        self.bump("dropped_row_seen", len(dropped_rows))
        agg_row_matched = False
        # THE VENUE-SHAPED POLICY (third candidate, measured like the
        # others): production settled into MIXED granularity (eq=33 /
        # one=44 at 2026-08-27T14:13Z) — N3 blocks both pure policies.
        # The venue's own shape is 'aggregate row for a taker fill,
        # per-exec rows otherwise', so S1's natural candidate ingests
        # the agg view when one exists and the exec set when none does.
        # sim_ven_residual_dup is the would-be double-ingest count for
        # THAT policy, and it gates exactly like the other two.
        # per-MARKET, because the venue publishes per FILL: a taker fill
        # (agg view) yields one aggregate row for ITS market; maker fills
        # in OTHER markets of the same tx still publish per-exec rows.
        exec_assets_set = {c["asset"] for c in execs}
        for row, hit, hit_a in hits:
            r_asset = str(row["asset"])
            ven_hit = (row["dedupe_key"] in ins_agg
                       or (row["dedupe_key"] in ins_exec
                           and r_asset not in agg_assets_set))
            if ven_hit:
                self.bump("sim_ven_suppressed")
            elif r_asset in agg_assets_set or r_asset in exec_assets_set:
                self.bump("sim_ven_residual_dup")
            else:
                self.bump("sim_ven_uncovered")
            if hit_a:
                self.bump("sim_agg_suppressed")
                agg_row_matched = True
            elif str(row["asset"]) in agg_assets_set:
                self.bump("sim_agg_residual_dup")
            else:
                # no aggregate view exists for this row's MARKET — an agg
                # for a DIFFERENT market in the same tx proves nothing;
                # the agg policy would ingest NOTHING here (no-coverage,
                # must not gate)
                self.bump("sim_agg_uncovered")
            if hit:
                self.bump("sim_exec_suppressed")
                if hit[0] == "hup":
                    self.bump("sim_exec_supp_hup_only")
                self._record_latency(hit[1], row)
            elif execs:
                self.bump("sim_exec_residual_dup")
                if hit_a:
                    self.bump("granularity_row")
            else:
                self.bump("sim_exec_uncovered")
        # field diagnosis for rows the exec policy failed to suppress —
        # GLOBAL assignment across the (tiny) sets: per-row greedy pairing
        # misattributed which fields diverged when rows stole candidates
        free = [c for c in execs if id(c) not in consumed]
        for row, pick in self._assign_pairs(residual_rows, free):
            if pick is not None:
                free.remove(pick)
            logged = self._diagnose_pair(tx, wallet, row, pick, logged)
        # granularity histogram — covered rows only: a dropped raw's
        # diverted row padded an aggregate-granularity tx into 'eq',
        # masquerading as per-exec in the very histogram that settles
        # the policy question
        n_rows = len(poll_rows) - len(dropped_rows)
        if len(execs) >= 2:
            b = ("eq" if n_rows == len(execs)
                 else "one" if n_rows == 1 else "other")
            self.bump("poll_mult." + b)
        # per-whale rollup
        self.bump(f"pw.{wallet}.n", len(execs))
        self.bump(f"pw.{wallet}.supp",
                  sum(1 for c in execs if id(c) in consumed))
        # sim extra: would-be inserts with no row at all (checked at finalization)
        chain_keys = {r["dedupe_key"] for r in chain_rows}
        all_row_keys = {r["dedupe_key"] for r in poll_rows} | chain_keys
        # pop EVERY pending record of this (tx, wallet) — including
        # classifier-dropped anomalies, which previously leaked in
        # pending forever and re-bumped their flags every minute
        for c in all_recs:
            self.pending.pop((c["tx"], c["log_index"], c["wallet"], c["view"]), None)
        # The live chain path (decode_fill_v3_receipt) writes ONE
        # AGGREGATE row per (tx, wallet), key-identical to the shadow's
        # agg record — and it SUPPRESSES the poll row at the poller's
        # pre-dedupe, so poll evidence never lands. Tie-proven legs must
        # be absorbed by the CHAIN aggregate exactly as by a poll one,
        # or every multi-leg chain-won tx reads orphan_chain_mismatch
        # (GATING) on a perfect decode (round-4 critical).
        agg_key_in_chain = any(k in chain_keys for k in ins_agg)
        exec_sum_covered = (agg_row_matched and tie == "ok")
        chain_sum_covered = ((agg_key_in_chain and tie == "ok")
                             or (not aggs and not poll_rows
                                 and self._chain_sum_covers(execs, chain_rows)))
        for key, (variant, c) in ins_exec.items():
            if variant == "round" and key not in all_row_keys and id(c) not in consumed:
                if (exec_sum_covered
                        and c["view"] in ("exec_counter", "exec_mint")
                        and c["asset"] in agg_assets_set):
                    # ONLY the legs agg_tieout actually summed are proven
                    # by the agg-matched row — the tie is asset-scoped,
                    # so the absorption must be too: a reverted
                    # cross-market guess (different asset) and an
                    # exec_owner both fall through to the orphan ladder
                    self.bump("exec_covered_by_agg_row")
                elif chain_sum_covered and (
                        (aggs and c["view"] in ("exec_counter", "exec_mint")
                         and c["asset"] in agg_assets_set)
                        or not aggs):
                    # not-aggs arm: _chain_sum_covers enforced a single
                    # asset+side across ALL execs, so no cross-market
                    # leg can reach it
                    self.bump("exec_covered_by_chain_agg_row")
                elif not poll_rows and not chain_rows:
                    self.bump("orphan_no_row")
                    self.note_example("orphan_no_row", tx=tx[:14], wallet=wallet[:12],
                                      view=c["view"], side=c["side"])
                    log.info("SHADOW-V2 ORPHAN tx=%s wallet=%s no rows", tx[:14], wallet[:12])
                elif self._chain_exact(c, chain_rows):
                    self.bump("orphan_chain_exact")
                elif chain_rows and not poll_rows:
                    self.bump("orphan_chain_mismatch")
                    self.note_example("orphan_chain_mismatch", tx=tx[:14],
                                      wallet=wallet[:12], side=c["side"], asset=c["asset"][:12])
                else:
                    self.bump("orphan_excess_exec")
                    self.note_example("orphan_excess_exec", tx=tx[:14],
                                      wallet=wallet[:12], view=c["view"],
                                      side=c["side"])   # N4: every instance attributed
        # symmetric sweep (round-3 kill): a wallet whose ONLY decoded
        # view is the aggregate, with zero venue rows in two hours, is
        # the loudest possible pre-flip alarm for the agg policy — the
        # exec-only sweep above could never raise it
        matched_agg_ids = {id(h[2][1]) for h in hits if h[2]}
        for key, (variant, a) in ins_agg.items():
            if (variant == "round" and key not in all_row_keys
                    and id(a) not in matched_agg_ids
                    and not poll_rows and not chain_rows):
                self.bump("orphan_agg_no_row")
                self.note_example("orphan_agg_no_row", tx=tx[:14],
                                  wallet=wallet[:12], side=a["side"])
                log.info("SHADOW-V2 AGG-ORPHAN tx=%s wallet=%s no rows",
                         tx[:14], wallet[:12])
        self.bump("compared_execs", len(execs))
        self.bump("txs_reconciled")
        return logged

    def _watch_pass(self, rows_by: dict, now: float) -> None:
        for (tx, wallet), w in list(self.watch.items()):
            for row in rows_by.get((tx, w["whale_id"]), []):
                if row["source"] != "poll":
                    continue
                k = row["dedupe_key"]
                if k in w["seen"]:
                    continue
                w["seen"].add(k)
                self.bump("late_row_seen")
                if (k in w.get("dropped_keys", ())
                        and k not in w["exec_keys"]
                        and k not in w["agg_keys"]):
                    # finalize-order parity: a key that ALSO lives in the
                    # insert set is factual suppression, not dropped
                    # coverage — the two paths must never disagree
                    self.bump("dropped_row_seen")
                    continue
                if k in w["exec_keys"]:
                    self.bump("sim_exec_suppressed")
                    if w["exec_keys"][k] == "hup":
                        self.bump("sim_exec_supp_hup_only")
                elif w["has_execs"]:
                    self.bump("sim_exec_residual_dup")
                    self.note_example("late_residual", tx=tx[:14],
                                      wallet=wallet[:12], key=k[:16])
                    log.warning("SHADOW-V2 LATE-RESIDUAL tx=%s wallet=%s",
                                tx[:14], wallet[:12])
                else:
                    self.bump("sim_exec_uncovered")
                if k in w["agg_keys"]:
                    self.bump("sim_agg_suppressed")
                elif str(row["asset"]) in w["agg_assets"]:
                    self.bump("sim_agg_residual_dup")
                else:
                    # no aggregate exists for this row's MARKET — the agg
                    # policy would ingest nothing for it (no-coverage)
                    self.bump("sim_agg_uncovered")
                r_asset = str(row["asset"])
                ven_hit = (k in w["agg_keys"]
                           or (k in w["exec_keys"]
                               and r_asset not in w["agg_assets"]))
                if ven_hit:
                    self.bump("sim_ven_suppressed")
                elif (r_asset in w["agg_assets"]
                      or r_asset in w.get("exec_assets", ())):
                    self.bump("sim_ven_residual_dup")
                else:
                    self.bump("sim_ven_uncovered")
            if now >= w["until"]:
                if not w.get("fetched"):
                    self.bump("watch_expired_unfetched")
                self.watch.pop((tx, wallet), None)

    @staticmethod
    def _assign_pairs(rows: list[dict],
                      cands: list[dict]) -> list[tuple[dict, dict | None]]:
        """Globally assign residual rows to free candidates by total field
        distance (sets are tiny). Greedy over the sorted pair list is the
        optimal assignment often enough here, and — unlike per-row greedy —
        can never let an early row steal a later row's exact match."""
        scored = []
        for ri, row in enumerate(rows):
            rs, rp = _num(row["size"]), _num(row["price"])
            for ci, c in enumerate(cands):
                d = 0.0
                if _num(Decimal(c["size_units"]) / Decimal(10 ** 6)) != rs:
                    d += 10 + float(abs(Decimal(c["size_units"]) / Decimal(10 ** 6)
                                        - Decimal(rs)))
                pr, ph = rec_prices(c)
                if _num(pr) != rp and _num(ph) != rp:
                    d += min(abs(float(pr) - float(rp)), 1.0)
                if c["side"] != row["side"]:
                    d += 0.5
                if c["asset"] != str(row["asset"]):
                    d += 0.5
                if c["ts"] is not None and int(c["ts"]) != int(row["ts_epoch"]):
                    d += 0.1
                scored.append((d, ri, ci))
        picks: dict[int, dict] = {}
        if rows and cands and len(rows) <= 6 and len(cands) <= 6:
            # exact min-total-cost assignment: sorted-greedy still lets a
            # noisy row steal another row's exact match when its own bad
            # pairing happens to score lowest
            from itertools import permutations
            cost = {(ri, ci): d for d, ri, ci in scored}
            best, best_pairs = None, None
            if len(rows) <= len(cands):
                for perm in permutations(range(len(cands)), len(rows)):
                    pairing = list(enumerate(perm))
                    tot = sum(cost[p] for p in pairing)
                    if best is None or tot < best:
                        best, best_pairs = tot, pairing
            else:
                for perm in permutations(range(len(rows)), len(cands)):
                    pairing = [(ri, ci) for ci, ri in enumerate(perm)]
                    tot = sum(cost[p] for p in pairing)
                    if best is None or tot < best:
                        best, best_pairs = tot, pairing
            for ri, ci in best_pairs or []:
                picks[ri] = cands[ci]
        else:
            scored.sort(key=lambda t: (t[0], t[1], t[2]))
            row_used: set[int] = set()
            cand_used: set[int] = set()
            for d, ri, ci in scored:
                if ri in row_used or ci in cand_used:
                    continue
                row_used.add(ri)
                cand_used.add(ci)
                picks[ri] = cands[ci]
        return [(row, picks.get(ri)) for ri, row in enumerate(rows)]

    def _chain_sum_covers(self, execs: list[dict],
                          chain_rows: list[dict]) -> bool:
        """True when some chain row IS the aggregate of the decoded
        legs — one asset, sizes summing exactly, price matching either
        rounding of the summed ratio. The v3 receipt path books exactly
        this shape for a maker's multi-leg tx."""
        if not execs or not chain_rows:
            return False
        if len({c["asset"] for c in execs}) != 1:
            return False
        if len({c["side"] for c in execs}) != 1:
            return False
        tot_size = sum(c["size_units"] for c in execs)
        tot_usdc = sum(c["usdc_units"] for c in execs)
        if not tot_size:
            return False
        pr = round(tot_usdc / tot_size, 6)
        ph = (Decimal(tot_usdc) / Decimal(tot_size)
              ).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
        size = _num(Decimal(tot_size) / Decimal(10 ** 6))
        for row in chain_rows:
            if (row["asset"] == execs[0]["asset"]
                    and row["side"] == execs[0]["side"]
                    and _num(row["size"]) == size
                    and _num(row["price"]) in (_num(pr), _num(ph))):
                return True
        return False

    def _chain_exact(self, c: dict, chain_rows: list[dict]) -> bool:
        """orphan_chain_exact requires the FULL mechanism: only a chain row
        equal on asset+side+size+price+ts could have key-suppressed the
        poll row at poller.py:116-126. Anything else must not hide here."""
        size = _num(Decimal(c["size_units"]) / Decimal(10 ** 6))
        pr, ph = rec_prices(c)
        for row in chain_rows:
            if (row["asset"] == c["asset"] and row["side"] == c["side"]
                    and _num(row["size"]) == size
                    and _num(row["price"]) in (_num(pr), _num(ph))
                    and int(row["ts_epoch"]) == int(c["ts"])):
                return True
        return False

    def _diagnose_pair(self, tx: str, wallet: str, row: dict,
                       pick: dict | None, logged: int) -> int:
        if pick is None:
            return logged  # excess poll row for this wallet — granularity signal
        rs, rp = _num(row["size"]), _num(row["price"])
        fields = []
        if _num(Decimal(pick["size_units"]) / Decimal(10 ** 6)) != rs:
            fields.append("size")
        pr, ph = rec_prices(pick)
        if pick["side"] != row["side"]:
            fields.append("side")
        if pick["asset"] != str(row["asset"]):
            fields.append("asset")
        if not fields or fields == ["size"]:
            if _num(pr) != rp and _num(ph) != rp:
                fields.append("price")
                if pick["fee_units"]:
                    self.bump("div.price_feepos")
            if int(pick["ts"]) != int(row["ts_epoch"]):
                fields.append("ts")
                d = int(pick["ts"]) - int(row["ts_epoch"])
                self.bump("ts_delta." + (str(d) if -3 <= d <= 3 else "other"))
        for f in fields:
            self.bump("div." + f)
        self.bump(f"pw.{wallet}.div", 1 if fields else 0)
        if not fields:
            # every field equal yet the key did not match: our key
            # construction model is wrong — the loudest possible alarm
            self.bump("key_impl_mismatch")
            fields = ["key"]
        self.note_example("diverge", tx=tx[:14], wallet=wallet[:12],
                          fields=fields, view=pick["view"],
                          shadow={"side": pick["side"], "asset": pick["asset"][:16],
                                  "size_u": pick["size_units"], "pr": float(pr),
                                  "ph": str(ph), "ts": pick["ts"], "fee_u": pick["fee_units"]},
                          poll={"side": row["side"], "asset": str(row["asset"])[:16],
                                "size": row["size"], "price": row["price"],
                                "ts": int(row["ts_epoch"])})
        if logged < DIVERGE_LOG_PER_CYCLE:
            log.warning("SHADOW-V2 DIVERGE tx=%s wallet=%s fields=%s shadow=%s/%s/%s "
                        "poll=%s/%s/%s", tx[:14], wallet[:12], ",".join(fields),
                        pick["side"], pick["asset"][:12], float(pr),
                        row["side"], str(row["asset"])[:12], row["price"])
            logged += 1
        return logged

    def _record_latency(self, rec: dict, row: dict) -> None:
        if rec["replay"] or rec["ts"] is None:
            return
        det, ts = float(row["det_epoch"]), int(row["ts_epoch"])
        if det - ts > LATE_POLL_ROW_S:
            self.bump("late_poll_row")
            return
        sh = rec["seen_at"] - rec["ts"]
        po = det - ts
        self.lags.append((sh, po))
        m = self.whale_mem(rec["wallet"], rec.get("username"))
        m["sh"].append(sh)
        m["po"].append(po)
        self.bump(f"pw.{rec['wallet']}.matched")

    # ── reverse probe: what did the shadow NOT see ──────────────────
    async def _reverse_probe(self, pool: Any) -> None:
        if time.time() - self.boot_at < REVERSE_LOOKBACK_S:
            # seen_tx/gaps are process memory: right after a restart every
            # pre-boot poll row would read "uncovered" and falsely reset
            # the 7-day window. Warm up for one full lookback first.
            self.bump("reverse_probe_warmup")
            return
        if self.seen_tx:
            oldest_seen = next(iter(self.seen_tx.values()))
            if time.time() - oldest_seen < REVERSE_LOOKBACK_S and \
                    len(self.seen_tx) >= SEEN_TX_CAP - 8:
                self.bump("reverse_probe_skipped")
                return
        lst = self.listener() if self.listener else None
        roster = getattr(lst, "_roster", {}) or {}
        ids = [w["id"] for w in roster.values()]
        if not ids:
            return
        rows = [dict(r) for r in await pool.fetch(SQL_REVERSE, ids, timeout=5.0)]
        chain_txs = {r["tx"] for r in rows if r["source"] == "chain"}
        for row in rows:
            if row["source"] != "poll" or row["dedupe_key"] in self.reverse_counted:
                continue
            self.reverse_counted[row["dedupe_key"]] = time.time()
            while len(self.reverse_counted) > 4096:
                self.reverse_counted.popitem(last=False)
            self.bump("poll_rows_seen")
            if row["tx"] in self.seen_tx:
                self.bump("poll_covered")
            elif row["tx"] in chain_txs:
                self.bump("poll_uncovered_chainrow")   # legacy topic / v3 partial
            else:
                ts = int(row["ts_epoch"])
                gap = next((gp for gp in self.gaps
                            if gp["from"] - 2 <= ts <= gp["to"] + 2), None)
                if gap:
                    self.bump("poll_uncovered_quiet_gap" if gap["kind"] == "quiet_or_outage"
                              else "poll_uncovered_ws_gap")
                else:
                    self.bump("poll_uncovered_unexplained")
                    self.note_example("poll_uncovered", tx=row["tx"][:14],
                                      whale_id=row["whale_id"])
                    log.warning("SHADOW-V2 UNCOVERED tx=%s whale=%s",
                                row["tx"][:14], row["whale_id"])

    # ── persistence: delta-only, writer-fenced, window bookkeeping ──
    async def _flush(self, pool: Any) -> None:
        nowf = time.time()
        if (not self.deltas and self._await_ack is None
                and self.last_flush_ok and nowf - self.last_flush_ok < FLUSH_IDLE_S):
            return
        try:
            raw = await pool.fetchval(
                "SELECT value FROM ingestion_state WHERE key=$1", STATE_KEY, timeout=5.0)
            stored: dict = {}
            corrupt = False
            if raw is not None:
                parsed: Any = None
                try:
                    parsed = json.loads(raw) if isinstance(raw, str) else raw
                except (TypeError, ValueError):
                    parsed = None
                if isinstance(parsed, dict):
                    stored = dict(parsed)
                else:
                    # jsonb 'null', arrays, scalars: valid JSON that is
                    # NOT our document. Reset instead of failing forever.
                    corrupt = True
            # field-shape validation: a PARTIALLY corrupted row must not
            # half-heal (fresh counters under old watermarks = negative
            # probe deltas beneath a fake unbroken window)
            if stored:
                if "counters" in stored and not isinstance(stored.get("counters"), dict):
                    corrupt = True
                if isinstance(stored.get("counters"), dict) and any(
                        not isinstance(v, (int, float))
                        for v in stored["counters"].values()):
                    # silently filtering a junk VALUE erased gating and
                    # volume evidence with zero signal (round-4 fleet)
                    corrupt = True
                if (stored.get("window_start") is not None
                        and not isinstance(stored.get("counters"), dict)):
                    # a window without counters is a half-corrupt doc: fresh
                    # counters under old watermarks render negative deltas
                    corrupt = True
                aw_chk = stored.get("at_window")
                if aw_chk is not None:
                    if not isinstance(aw_chk, dict):
                        corrupt = True
                    else:
                        for k2, v2 in aw_chk.items():
                            if k2 in ("pw", "emitter"):
                                if not isinstance(v2, dict) or any(
                                        not isinstance(x, (int, float))
                                        for x in v2.values()):
                                    corrupt = True
                            elif not isinstance(v2, (int, float)):
                                corrupt = True
                for f in ("window_start", "health_start", "updated_at_epoch"):
                    if not isinstance(stored.get(f), (int, float, type(None))):
                        corrupt = True
            if corrupt:
                stored = {"corrupt_reset": int(nowf)}
                self.bump("corrupt_reset")
                log.warning("SHADOW-V2 CORRUPT-RESET stored state row discarded")
            # lost-ack reconciliation (undercount-only guarantee): if the
            # previous write COMMITTED but its ack was lost to a timeout
            # or cancellation, the snap_id in the stored row proves it —
            # decrement that snap now instead of re-adding it (which
            # doubled every counter in it, including the pro-flip ones).
            if self._await_ack is not None:
                sid, prev = self._await_ack
                verified = (stored.get("writer") == WRITER_ID
                            and stored.get("snap_id") == sid)
                foreign = (stored.get("writer") not in (None, WRITER_ID)
                           or "corrupt_reset" in stored)
                if verified or foreign:
                    # verified: the write committed — decrement so it is
                    # never re-added. foreign/corrupt: the outcome is
                    # UNKNOWABLE (a takeover may have inherited our
                    # committed counters) — DROP the snap anyway: a
                    # deliberate undercount preserves the undercount-only
                    # guarantee, where keeping it doubled every counter
                    # after takeover-then-reclaim.
                    for k, v in prev.items():
                        left = self.deltas.get(k, 0) - v
                        if left:
                            self.deltas[k] = left
                        else:
                            self.deltas.pop(k, None)
                    if not verified:
                        self.bump("ack_dropped_unverified")
                # writer None / our writer with a different snap_id: the
                # write provably never landed — deltas stay for retry
                self._await_ack = None
            w = stored.get("writer")
            try:
                stored_epoch = float(stored.get("updated_at_epoch") or 0)
            except (TypeError, ValueError):
                stored_epoch = 0.0
            if w and w != WRITER_ID and nowf - stored_epoch < TAKEOVER_S:
                self.bump("writer_conflict")
                log.warning("SHADOW-V2 WRITER-CONFLICT theirs=%s mine=%s", w, WRITER_ID)
                return
            snap = dict(self.deltas)
            raw_counters = stored.get("counters")
            counters = {k: int(v)
                        for k, v in (raw_counters.items()
                                     if isinstance(raw_counters, dict) else ())
                        if isinstance(v, (int, float))}
            if not self.booted:
                snap["boots"] = snap.get("boots", 0) + 1
                self.deltas["boots"] = self.deltas.get("boots", 0) + 1
                self.booted = True
            for k, v in snap.items():
                counters[k] = counters.get(k, 0) + v
            window_start = stored.get("window_start")
            health_start = stored.get("health_start")
            at_window = stored.get("at_window")
            at_window = dict(at_window) if isinstance(at_window, dict) else {}
            if stored.get("commit") not in (None, self.commit):
                window_start = health_start = None
            stored_leading = (stored.get("leading_policy")
                              if stored.get("leading_policy") in ("exec", "agg")
                              else None)
            if stored_leading is None:
                leading = ("exec" if counters.get("sim_exec_suppressed", 0)
                           >= counters.get("sim_agg_suppressed", 0) else "agg")
            else:
                # LEADING flips on SINCE-WINDOW deltas with hysteresis —
                # a cumulative >= comparison is a random walk around the
                # tie that reset the window on every recross forever
                dw_e = (counters.get("sim_exec_suppressed", 0)
                        - (at_window.get("sim_exec_suppressed") or 0))
                dw_a = (counters.get("sim_agg_suppressed", 0)
                        - (at_window.get("sim_agg_suppressed") or 0))
                leading = stored_leading
                ch, inc = ((dw_a, dw_e) if stored_leading == "exec"
                           else (dw_e, dw_a))
                if ch > inc * 1.1 + 5:
                    leading = "agg" if stored_leading == "exec" else "exec"
                    # direct write: snap was taken already, and this is
                    # flush-time derived bookkeeping like pw_pruned
                    counters["leading_flip"] = counters.get("leading_flip", 0) + 1
            # BOTH residual counters gate: watching only the leading
            # policy's residuals let the eventually-chosen policy's
            # double-ingests hide inside an unbroken 7-day window, and a
            # decisive leading crossover restarts the evidence clock too.
            gating_hit = (any(snap.get(k) for k in GATING)
                          or snap.get("sim_exec_residual_dup")
                          or snap.get("sim_agg_residual_dup")
                          or snap.get("sim_ven_residual_dup")
                          or (stored_leading is not None
                              and leading != stored_leading))
            if window_start is None or gating_hit:
                window_start = nowf
                at_window = {k: counters.get(k, 0) for k in VOLUME_KEYS}
                at_window["pw"] = {k: v for k, v in counters.items() if k.startswith("pw.")}
                at_window["emitter"] = {k: v for k, v in counters.items()
                                        if k.startswith("emitter.")}
            if health_start is None or any(snap.get(k) for k in HEALTH):
                health_start = nowf
            # per_whale is built from the pw.* COUNTERS (which survive
            # restarts), not from process memory — otherwise the TARGET
            # evidence vanished on every deploy and never existed for a
            # whale whose rows only diverge. Memory overlays lag medians.
            lst = self.listener() if self.listener else None
            roster = getattr(lst, "_roster", {}) or {}
            pw_wallets = {k.split(".")[1] for k in counters if k.startswith("pw.")}
            # hex-sort-first-32 silently evicted live TARGET whales behind
            # dead wallets: EVERY cohort ranks by activity (roster first),
            # and long-gone wallets' immortal keys are pruned outright.
            # Prune only with a live roster (a GC'd listener must not nuke
            # roster whales), and drop the at_window.pw watermark with the
            # counter — a stale high watermark under restarted counters
            # renders negative TARGET deltas forever.
            aw_pw = at_window.get("pw") if isinstance(at_window.get("pw"), dict) else None
            if roster and len(pw_wallets) > PER_WHALE_CAP * 2:
                for w_old in sorted(
                        (x for x in pw_wallets if x not in roster),
                        key=lambda x: counters.get(f"pw.{x}.n", 0))[
                        :len(pw_wallets) - PER_WHALE_CAP * 2]:
                    for suf in ("n", "matched", "supp", "div"):
                        counters.pop(f"pw.{w_old}.{suf}", None)
                        if aw_pw is not None:
                            aw_pw.pop(f"pw.{w_old}.{suf}", None)
                    pw_wallets.discard(w_old)
                    counters["pw_pruned"] = counters.get("pw_pruned", 0) + 1
            roster_cohort = sorted(
                (x for x in pw_wallets if x in roster),
                key=lambda x: (-counters.get(f"pw.{x}.n", 0), x))
            if len(roster_cohort) > PER_WHALE_CAP:
                counters["per_whale_truncated"] = \
                    counters.get("per_whale_truncated", 0) + 1
            wallets = (roster_cohort
                       + sorted((x for x in pw_wallets if x not in roster),
                                key=lambda x: (-counters.get(f"pw.{x}.n", 0), x))
                       )[:PER_WHALE_CAP]
            per_whale = {}
            for wallet in wallets:
                m = self.per_whale_mem.get(wallet)
                uname = ((m or {}).get("username")
                         or (roster.get(wallet) or {}).get("username"))
                per_whale[wallet] = {
                    "username": uname,
                    "n": counters.get(f"pw.{wallet}.n", 0),
                    "matched": counters.get(f"pw.{wallet}.matched", 0),
                    "supp": counters.get(f"pw.{wallet}.supp", 0),
                    "div": counters.get(f"pw.{wallet}.div", 0),
                    "lag_p50_s": (round(median(m["sh"]), 2)
                                  if m and m["sh"] else None),
                    "poll_lag_p50_s": (round(median(m["po"]), 2)
                                       if m and m["po"] else None),
                }
            sid_new = uuid.uuid4().hex[:8]
            value = {
                "schema_v": SCHEMA_V, "writer": WRITER_ID, "commit": self.commit,
                "snap_id": sid_new,
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(nowf)),
                "updated_at_epoch": int(nowf),
                "window_start": window_start, "health_start": health_start,
                "leading_policy": leading, "at_window": at_window,
                "counters": counters, "per_whale": per_whale,
                "pending": len(self.pending),
                "lag_p50_s": round(median(x[0] for x in self.lags), 2) if self.lags else None,
                "poll_lag_p50_s": round(median(x[1] for x in self.lags), 2) if self.lags else None,
                "examples": list(self.examples),
                "gaps": [dict(g) for g in self.gaps][-8:],
            }
            # arm the ack BEFORE the write so a cancellation or timeout
            # that lands after the server committed is reconciled by
            # snap_id on the next flush instead of double-counted
            self._await_ack = (sid_new, snap)
            status = await pool.execute(SQL_FLUSH, STATE_KEY, json.dumps(value),
                                        WRITER_ID, nowf - TAKEOVER_S, timeout=5.0)
            if isinstance(status, str) and status.split()[-1] == "0":
                # conditional write rejected: a live foreign writer won
                # the race after our fence read — nothing committed
                self._await_ack = None
                self.bump("writer_conflict")
                log.warning("SHADOW-V2 WRITER-CONFLICT (atomic) mine=%s", WRITER_ID)
                return
            self._await_ack = None
            for k, v in snap.items():
                left = self.deltas.get(k, 0) - v
                if left:
                    self.deltas[k] = left
                else:
                    self.deltas.pop(k, None)
            self.last_flush_ok = nowf
            log.info("SHADOW-V2 recon: pend=%d supp=+%d res=+%d div=+%d orphan=+%d",
                     len(self.pending), snap.get("sim_exec_suppressed", 0),
                     snap.get("sim_exec_residual_dup", 0),
                     sum(snap.get("div." + f, 0) for f in ("side", "asset", "size", "price", "ts")),
                     snap.get("orphan_no_row", 0) + snap.get("orphan_chain_mismatch", 0))
        except Exception:  # noqa: BLE001
            self.bump("flush_failures")
            log.info("SHADOW-V2 flush failed", exc_info=True)


# ── module singleton + hot-path entry points ────────────────────────
_STATE = ShadowV2()
_TASK: asyncio.Task | None = None


def shadow_observe(listener: Any, log_entry: dict[str, Any]) -> None:
    """Hot-path hook (chain.py _handle_log, BEFORE the _handle_v3 dispatch
    so the _v3_seen per-tx early-return at chain.py:405-406 cannot hide
    events 2..N). Synchronous, zero I/O, NEVER raises."""
    try:
        st = _STATE
        if not st.enabled:
            return
        st.last_observe_at = time.time()
        st.bump("events_seen")
        if log_entry.get("removed"):
            st.bump("reorg_removed")
            return
        tx = str(log_entry.get("transactionHash", "")).lower()
        tps0 = log_entry.get("topics") or []
        if tx:
            if tx in st.seen_tx:
                st.seen_tx.move_to_end(tx)   # position must track recency or
                # the reverse-probe skip guard reads a refreshed value at
                # position 0 as "young horizon" and disables coverage
            st.seen_tx[tx] = time.time()
            while len(st.seen_tx) > SEEN_TX_CAP:
                st.seen_tx.popitem(last=False)
                st.bump("seen_tx_evicted")
        try:
            lix: int | None = int(str(log_entry.get("logIndex")), 16)
        except (TypeError, ValueError):
            lix = None
            st.bump("log_index_missing")
        if lix is None:
            # content-derived identity: unique for distinct fills, STABLE
            # for redeliveries — a monotonic counter deduped nothing and a
            # shared sentinel collapsed real fills
            lix = -2 - (abs(hash((tx, str(log_entry.get("data", "")),
                                  tuple(str(t) for t in tps0)))) % (2 ** 31))
        if tx:
            ek = (tx, lix)
            if ek in st.seen_events:
                st.bump("dup_event")
                return
            st.seen_events[ek] = time.time()
            while len(st.seen_events) > SEEN_EVENTS_CAP:
                st.seen_events.popitem(last=False)
                st.bump("seen_events_evicted")
        roster = getattr(listener, "_roster", {}) or {}
        recs, reason, involved = decode_shadow_views(
            log_entry, roster, st.exchange_set(listener))
        if reason is not None:
            st.bump(("undecodable_roster." if involved else "undecodable_foreign.")
                    + reason)
            return
        if not recs:
            st.bump("non_roster")
            return
        tps = log_entry.get("topics") or []
        if len(tps) >= 4 and _addr(tps[2]) == _addr(tps[3]):
            st.bump("self_trade")
        replay = bool(getattr(listener, "_shadow_replay", False))
        if replay:
            st.bump("replay_seen")
        now = time.time()
        try:  # read-only membership peek: what cache-warmth would the
            # flipped live path have had? (never .get(), never write)
            blocks = getattr(listener, "_blocks", None)
            warm = recs[0]["block"] in getattr(blocks, "_cache", {})
            st.bump("live_cache_hit" if warm else "live_cache_miss")
        except Exception:  # noqa: BLE001
            pass
        for r in recs:
            if not r["block"]:
                # no block number = no timestamp ever = no key ever; one
                # such record must not poison its whole tx for 70 minutes
                st.bump("block_missing")
                continue
            st.bump("decoded_" + ("agg" if r["view"] == "agg" else r["view"]))
            if r.get("agg_by_set_only"):
                st.bump("agg_by_set_only")
            if r.get("price_variant_disagree"):
                st.bump("price_variant_disagree")
            ek2 = "emitter." + r["emitter"]
            if ek2 in st.deltas or sum(1 for k in st.deltas if k.startswith("emitter.")) < 8:
                st.bump(ek2)
            else:
                st.bump("emitter.other")
            r.update(log_index=lix, replay=replay, seen_at=now,
                     ts=None, ts_tries=0)
            k = (r["tx"], r["log_index"], r["wallet"], r["view"])
            if k in st.pending:
                st.bump("dup_record")
                continue
            st.pending[k] = r
            while len(st.pending) > PENDING_CAP:
                st.pending.popitem(last=False)
                st.bump("pending_overflow_dropped")
    except Exception:  # noqa: BLE001 — the wall
        try:
            _STATE.deltas["shadow_errors"] = _STATE.deltas.get("shadow_errors", 0) + 1
        except Exception:  # noqa: BLE001
            pass


def ensure_shadow_task(listener: Any) -> None:
    """Called at the top of every ChainListener.run() iteration. Idempotent,
    never raises. ONE task per process (module-level handle) — supervisor-
    built replacement listener instances rebind the weakref and inherit the
    same shadow state, so no zombies and no concurrent writers. Also records
    the reconnect-gap ledger the reverse probe classifies against."""
    global _TASK
    try:
        st = _STATE
        if not st.enabled:
            return
        now = time.time()
        prev = st.last_ensure_at
        st.last_ensure_at = now
        if prev and now - prev > 5:
            since = max(st.last_observe_at, prev)
            kind = "quiet_or_outage" if now - since > 55 else "reconnect"
            last = st.gaps[-1] if st.gaps else None
            if last and since <= last["to"] + 5 and last["kind"] == kind:
                last["to"] = now   # sustained flapping is ONE span, not
                # sixteen entries racing the 20-minute reverse lookback
            else:
                st.gaps.append({"from": since, "to": now, "kind": kind})
            st.bump("gap_" + kind)
            while st.gaps and st.gaps[0]["to"] < now - REVERSE_LOOKBACK_S - 300:
                st.gaps.popleft()   # age-pruned; maxlen stays the backstop
        st.listener = weakref.ref(listener)
        st.http_url = getattr(listener, "_http_url", "") or st.http_url
        if _TASK is None or _TASK.done():
            _TASK = asyncio.get_running_loop().create_task(_run(st))
    except Exception:  # noqa: BLE001
        try:
            _STATE.deltas["shadow_errors"] = _STATE.deltas.get("shadow_errors", 0) + 1
        except Exception:  # noqa: BLE001
            pass


async def _run(st: ShadowV2) -> None:
    while True:
        await asyncio.sleep(TICK_S)
        try:
            await asyncio.wait_for(st.tick(), timeout=45)
        except Exception:  # noqa: BLE001
            st.bump("cycle_errors")
            log.info("SHADOW-V2 cycle error", exc_info=True)


def beat_summary() -> dict:
    """Additive heartbeat gauge. Never raises; {} when unavailable."""
    try:
        st = _STATE
        if not st.enabled:
            return {"enabled": False}
        return {"pend": len(st.pending),
                "recon_n": st.reconcile_n,
                "flush_age_s": (round(time.time() - st.last_flush_ok)
                                if st.last_flush_ok else None),
                "err_unflushed": st.deltas.get("shadow_errors", 0)
                + st.deltas.get("cycle_errors", 0)}
    except Exception:  # noqa: BLE001
        return {}
