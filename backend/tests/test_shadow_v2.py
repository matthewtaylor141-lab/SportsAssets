"""Phase S0 shadow decoder — every tournament kill pinned.

The fixtures are the two verbatim p382s.log:4452-4476 events (maker BUY
7.6322/12.31 = 0.62 fee 0; taker aggregate SELL with fee 145010 isolated
in word4) plus a synthetic mixed mint tx modeled on probe411's
0x5f69a892: one normal sell-side maker and one mint-side maker whose
complement-token leg must be TRANSFORMED (asset := agg asset, side :=
agg side, usdc := size - maker_usdc) before the aggregate ties out
integer-exact.

Each test names the tournament kill or invariant it pins — see
tasks/decoder_spec.txt sections 1 and 7.
"""

import ast
import asyncio
import json
import time
from decimal import Decimal
from pathlib import Path

import pytest

import sportsassets.ingestion.shadow_v2 as sv
from sportsassets.ingestion.chain import FILL_V3_TOPIC, BlockTimestampCache
from sportsassets.ingestion.dedupe import _num, make_dedupe_key
from sportsassets.ingestion.shadow_v2 import (
    ShadowV2, agg_tieout, classify_mints, decode_shadow_views,
    rec_keys, rec_prices, shadow_observe, ensure_shadow_task,
)

# ── fixture constants: the p382s pair, verbatim word values ─────────
MAKER = "0x2005d16a" + "aa" * 16
TAKER = "0x40841cc0" + "bb" * 16
EXCH = "0xe2222d279d744050d28e00520010520000310f59"   # the v3 emitter
TOKEN_INT = int("8b435bed" + "22" * 28, 16)
TX = "0x" + "f1" * 32
TS0 = 1_724_000_000


def _topic(addr: str) -> str:
    return "0x" + "00" * 12 + addr[2:]


def _ev(owner, cpty, w0, token, gave, got, fee=0, tx=TX, log_index=1,
        block=65_000_000, address=EXCH):
    return {
        "topics": [FILL_V3_TOPIC, "0x" + "11" * 32, _topic(owner), _topic(cpty)],
        "data": "0x" + "".join(format(v, "064x")
                               for v in (w0, token, gave, got, fee)),
        "transactionHash": tx,
        "blockNumber": hex(block),
        "logIndex": hex(log_index),
        "address": address,
    }


MAKER_EV = _ev(MAKER, TAKER, 0, TOKEN_INT, 0x747548, 0xBBD5F0, 0)
TAKER_EV = _ev(TAKER, EXCH, 1, TOKEN_INT, 0xBBD5F0, 0x747548, 0x23672,
               log_index=2)


def _roster(*pairs):
    return {a: {"id": i, "username": u} for a, i, u in pairs}


class _Listener:
    def __init__(self, roster=None, addresses=None, blocks=None,
                 http_url="http://rpc.test"):
        self._roster = roster or {}
        self._addresses = addresses if addresses is not None else [EXCH]
        self._http_url = http_url
        if blocks is not None:
            self._blocks = blocks


class _Resp:
    def __init__(self, code=200, payload=None):
        self.status_code = code
        self._p = payload

    def json(self):
        return self._p


class _FakeClient:
    def __init__(self, resp):
        self.resp = resp

    async def post(self, url, json=None):
        return self.resp


class _FakePool:
    def __init__(self, stored=None, rows=None):
        self.stored = stored
        self.rows = rows or []
        self.executes = []
        self.fail_execute = False

    async def fetchval(self, sql, *args, timeout=None):
        return self.stored

    async def fetch(self, sql, *args, timeout=None):
        return self.rows

    async def execute(self, sql, *args, timeout=None):
        if self.fail_execute:
            raise RuntimeError("db down")
        self.executes.append((sql, args))


@pytest.fixture
def st(monkeypatch):
    monkeypatch.delenv("SHADOW_V2", raising=False)
    s = ShadowV2()
    monkeypatch.setattr(sv, "_STATE", s)
    return s


def _mkrec(wallet=MAKER, view="exec_counter", side="BUY", asset="777",
           size_units=4_000_000, usdc_units=2_480_000, ts=TS0,
           tx=TX, log_index=1, whale_id=1, fee=0, replay=False,
           seen_at=None, owner_side=None):
    return {"tx": tx, "block": 65_000_000, "wallet": wallet,
            "whale_id": whale_id, "username": "w", "view": view,
            "side": side, "owner_side": owner_side or side, "asset": asset,
            "size_units": size_units, "usdc_units": usdc_units,
            "fee_units": fee, "emitter": EXCH, "log_index": log_index,
            "replay": replay,
            "seen_at": seen_at if seen_at is not None else time.time(),
            "ts": ts, "ts_tries": 0}


def _poll_row(rec, source="poll", ts=None, key=None, side=None, asset=None,
              size=None, price=None, det_lag=120.0):
    ts = ts if ts is not None else rec["ts"]
    size_d = (Decimal(rec["size_units"]) / Decimal(10 ** 6)
              if size is None else Decimal(str(size)))
    price_v = rec_prices(rec)[0] if price is None else price
    side = side or rec["side"]
    asset = asset if asset is not None else rec["asset"]
    key = key or make_dedupe_key(rec["tx"], asset, side, size_d, price_v, ts)
    return {"tx": rec["tx"], "whale_id": rec["whale_id"], "asset": asset,
            "side": side, "size": str(size_d), "price": str(price_v),
            "ts_epoch": ts, "source": source, "dedupe_key": key,
            "det_epoch": ts + det_lag}


# ── 1. A1: the hot hook does zero I/O ───────────────────────────────
def test_observe_is_sync_and_does_no_io(st, monkeypatch):
    assert not asyncio.iscoroutinefunction(shadow_observe)

    async def _boom(*a, **k):
        raise AssertionError("hot path performed I/O")

    import httpx
    import sportsassets.db as db
    monkeypatch.setattr(httpx.AsyncClient, "post", _boom)
    monkeypatch.setattr(db, "get_pool", _boom)
    lst = _Listener(roster=_roster((MAKER, 1, "makerwhale")))
    shadow_observe(lst, MAKER_EV)          # would raise if it touched either
    assert len(st.pending) == 1
    assert st.deltas.get("events_seen") == 1
    assert st.deltas.get("shadow_errors") is None


# ── 2. Exception wall: dereference-free, _handle_v3 untouched ───────
def test_wall_is_dereference_free_and_v3_still_runs(st, monkeypatch):
    from sportsassets.ingestion.chain import ChainListener

    def _die(*a, **k):
        raise RuntimeError("decoder exploded")

    monkeypatch.setattr(sv, "decode_shadow_views", _die)
    lst = ChainListener()
    calls = []

    async def _rec_v3(entry):
        calls.append(entry)

    lst._handle_v3 = _rec_v3
    asyncio.run(lst._handle_log(MAKER_EV))
    assert len(calls) == 1, "the shadow must never eat the live dispatch"
    assert st.deltas.get("shadow_errors", 0) >= 1

    class _Broken:
        def __getattr__(self, name):
            raise RuntimeError("state gone")

    monkeypatch.setattr(sv, "_STATE", _Broken())
    asyncio.run(lst._handle_log(MAKER_EV))
    assert len(calls) == 2, "call-site pass must protect even a broken _STATE"


# ── 3. C1: one task per process across supervisor rebuilds ──────────
def test_single_task_across_listener_instances(st):
    async def _go():
        sv._TASK = None
        try:
            l1 = _Listener(roster=_roster((MAKER, 1, "m")))
            l2 = _Listener(roster=_roster((MAKER, 1, "m")))
            ensure_shadow_task(l1)
            t1 = sv._TASK
            assert t1 is not None and not t1.done()
            st.pending[("t", 1, "w")] = {"marker": True}
            ensure_shadow_task(l2)
            assert sv._TASK is t1, "a rebuilt listener must NOT spawn a zombie"
            assert ("t", 1, "w") in st.pending, "state survives listener swap"
            assert st.listener() is l2, "weakref rebinds to the live instance"
        finally:
            if sv._TASK is not None:
                sv._TASK.cancel()
                try:
                    await sv._TASK
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
                sv._TASK = None
    asyncio.run(_go())


# ── 4. C1: writer fence — a second writer skips, never corrupts ─────
def test_writer_fence_skips_conflicting_flush(st):
    now = time.time()
    st.bump("events_seen")
    pool = _FakePool(stored=json.dumps(
        {"writer": "deadbeef", "updated_at_epoch": now - 10, "counters": {}}))
    asyncio.run(st._flush(pool))
    assert pool.executes == [], "a live foreign writer means NO write"
    assert st.deltas.get("writer_conflict") == 1
    assert st.deltas.get("events_seen") == 1, "delta must survive the skip"

    st2 = ShadowV2()
    st2.bump("events_seen")
    pool2 = _FakePool(stored=json.dumps(
        {"writer": "deadbeef", "updated_at_epoch": now - 3600, "counters": {}}))
    asyncio.run(st2._flush(pool2))
    assert len(pool2.executes) == 1, "a stale writer is taken over"


# ── 5. C1: delta-only flush — restart can undercount, never double ──
def test_delta_only_flush_never_double_counts(st):
    pool = _FakePool(stored=json.dumps(
        {"counters": {"sim_exec_suppressed": 7}}))
    st.deltas = {"sim_exec_suppressed": 2}
    asyncio.run(st._flush(pool))
    assert len(pool.executes) == 1
    written = json.loads(pool.executes[0][1][1])
    assert written["counters"]["sim_exec_suppressed"] == 9
    assert "sim_exec_suppressed" not in st.deltas, "flushed delta is cleared"

    st2 = ShadowV2()
    st2.deltas = {"sim_exec_suppressed": 2}
    pool2 = _FakePool(stored=json.dumps({"counters": {}}))
    pool2.fail_execute = True
    asyncio.run(st2._flush(pool2))
    assert st2.deltas.get("sim_exec_suppressed") == 2, \
        "a failed execute must keep the delta (undercount-only discipline)"
    assert st2.deltas.get("flush_failures") == 1


# ── 6. Layout ground truth: the p382s pair decodes verbatim ─────────
def test_p382s_events_decode_verbatim():
    recs, reason, involved = decode_shadow_views(
        MAKER_EV, _roster((MAKER, 1, "makerwhale")), {EXCH})
    assert reason is None and involved
    (r,) = recs
    assert r["view"] == "exec_owner" and r["side"] == "BUY"
    assert r["size_units"] == 12_310_000 and r["usdc_units"] == 7_632_200
    assert r["fee_units"] == 0
    assert rec_prices(r)[0] == 0.62

    recs2, _, _ = decode_shadow_views(
        MAKER_EV, _roster((TAKER, 2, "takerwhale")), {EXCH})
    (c,) = recs2
    assert c["view"] == "exec_counter" and c["side"] == "SELL", \
        "the counterparty view flips the owner side"

    recs3, _, _ = decode_shadow_views(
        TAKER_EV, _roster((TAKER, 2, "takerwhale")), {EXCH})
    (a,) = recs3
    assert a["view"] == "agg" and a["side"] == "SELL"
    assert a["fee_units"] == 145_010, "fee lives in word4, price excludes it"
    assert rec_prices(a)[0] == 0.62

    c["ts"] = TS0
    variant, key = rec_keys(c)[0]
    assert variant == "round"
    assert key == make_dedupe_key(TX, str(TOKEN_INT), "SELL",
                                  Decimal("12.310000"), 0.62, TS0), \
        "the shadow's would-be key must equal the production construction"


# ── 7. Aggregate view is self-describing (topics[3] == emitter) ─────
def test_agg_classified_by_emitter_not_env():
    recs, _, _ = decode_shadow_views(
        TAKER_EV, _roster((TAKER, 2, "t")), set())     # env drift: empty set
    assert recs[0]["view"] == "agg", \
        "topics[3] == log.address classifies agg without any env help"
    assert not recs[0].get("agg_by_set_only")

    other_exch = "0x" + "77" * 20
    ev = _ev(TAKER, other_exch, 1, TOKEN_INT, 0xBBD5F0, 0x747548,
             address=EXCH, log_index=9)
    recs2, _, _ = decode_shadow_views(
        ev, _roster((TAKER, 2, "t")), {EXCH, other_exch})
    assert recs2[0]["view"] == "agg" and recs2[0]["agg_by_set_only"] is True


# ── 8. B1/D1: the mixed mint tx transforms and ties out ─────────────
def _mint_tx_recs(roster):
    m1 = "0x" + "31" * 20
    m2 = "0x" + "32" * 20
    agg_ev = _ev(MAKER, EXCH, 0, 1111, 6_200_000, 10_000_000, log_index=1)
    m1_ev = _ev(m1, MAKER, 1, 1111, 4_000_000, 2_480_000, log_index=2)
    m2_ev = _ev(m2, MAKER, 0, 2222, 2_280_000, 6_000_000, log_index=3)
    out = []
    for ev in (agg_ev, m1_ev, m2_ev):
        recs, reason, _ = decode_shadow_views(ev, roster, {EXCH})
        assert reason is None
        out.extend(recs)
    return out


def test_mixed_mint_tx_transforms_and_ties_out(st):
    roster = _roster((MAKER, 7, "whale7"))
    recs = _mint_tx_recs(roster)
    assert len(recs) == 3
    groups = classify_mints(recs)
    g = groups[MAKER]
    assert g["flags"] == {"mint_transformed": 1}
    views = sorted(e["view"] for e in g["execs"])
    assert views == ["exec_counter", "exec_mint"]
    mint = next(e for e in g["execs"] if e["view"] == "exec_mint")
    assert mint["asset"] == "1111" and mint["side"] == "BUY"
    assert mint["usdc_units"] == 3_720_000, \
        "one share-pair costs exactly 1.000000 collateral: 6.0 - 2.28 = 3.72"
    assert agg_tieout(g) == "ok", "2.48 + 3.72 == 6.2 and 4 + 6 == 10, exact"

    for i, r in enumerate(g["execs"] + g["aggs"]):
        r.update(log_index=10 + i, replay=False, seen_at=time.time() - 1000,
                 ts=TS0, ts_tries=0)
    rows = [_poll_row(e) for e in g["execs"]]
    st._match_wallet(TX, MAKER, g, g["execs"] + g["aggs"], rows, [], 1000.0, time.time(), 0)
    assert st.deltas.get("sim_exec_suppressed") == 2
    assert st.deltas.get("sim_exec_residual_dup") is None, \
        "the flipped per-exec path would re-ingest NOTHING on this tx"

    # pure-mint variant (single mint maker, 0xdddfa2e0 shape)
    m2 = "0x" + "32" * 20
    agg_ev = _ev(MAKER, EXCH, 0, 1111, 3_720_000, 6_000_000, log_index=1)
    m2_ev = _ev(m2, MAKER, 0, 2222, 2_280_000, 6_000_000, log_index=2)
    recs2 = []
    for ev in (agg_ev, m2_ev):
        r, reason, _ = decode_shadow_views(ev, roster, {EXCH})
        assert reason is None
        recs2.extend(r)
    g2 = classify_mints(recs2)[MAKER]
    assert g2["flags"] == {"mint_transformed": 1}
    assert agg_tieout(g2) == "ok"


# ── 9. B1 honesty: anomalies refused, never guessed through ─────────
def test_mint_anomaly_refused_not_guessed():
    agg = _mkrec(view="agg", side="BUY", asset="1111",
                 size_units=10_000_000, usdc_units=6_200_000)
    bad = _mkrec(view="exec_counter", side="BUY", owner_side="SELL",
                 asset="2222", size_units=6_000_000, usdc_units=2_280_000,
                 log_index=2)
    g = classify_mints([agg, bad])[MAKER]
    assert g["flags"] == {"mint_side_anomaly": 1}
    assert g["execs"] == [], "an anomalous mint leg must NOT enter the sim"
    assert agg_tieout(g) == "skip"

    agg2 = _mkrec(view="agg", side="BUY", asset="3333",
                  size_units=1_000_000, usdc_units=500_000, log_index=3)
    mintish = _mkrec(view="exec_counter", side="SELL", owner_side="BUY",
                     asset="2222", size_units=6_000_000,
                     usdc_units=2_280_000, log_index=4)
    g2 = classify_mints([agg, agg2, mintish])[MAKER]
    assert g2["flags"] == {"per_exec_ambiguous": 1}
    assert agg_tieout(g2) == "skip"


# ── 10. A2/B2: the two policies are simulated apart ─────────────────
def test_policy_simulation_separates_views(st):
    e1 = _mkrec(size_units=4_000_000, usdc_units=2_480_000, log_index=1,
                seen_at=time.time() - 1000)
    e2 = _mkrec(size_units=6_000_000, usdc_units=3_720_000, log_index=2,
                seen_at=time.time() - 1000)
    ag = _mkrec(view="agg", size_units=10_000_000, usdc_units=6_200_000,
                log_index=3, seen_at=time.time() - 1000)
    g = {"execs": [e1, e2], "aggs": [ag], "flags": {}}
    rows = [_poll_row(e1), _poll_row(e2)]
    st._match_wallet(TX, MAKER, g, g["execs"] + g["aggs"], rows, [], 1000.0, time.time(), 0)
    assert st.deltas.get("sim_exec_suppressed") == 2
    assert st.deltas.get("sim_agg_residual_dup") == 2, \
        "an aggregate-only S1 would re-ingest both per-exec poll rows"
    assert st.deltas.get("poll_mult.eq") == 1

    st.deltas = {}
    st.finalized.clear()
    g2 = {"execs": [dict(e1), dict(e2)], "aggs": [dict(ag)], "flags": {}}
    rows2 = [_poll_row(ag)]
    # partial exec coverage no longer finalizes early (fleet-2 kill: a
    # late-landing divergent sibling must stay testable) — the aggregate
    # verdict is counted at the deadline pass with complete evidence
    st._match_wallet(TX, MAKER, g2, g2["execs"] + g2["aggs"], rows2, [],
                     sv.ORPHAN_FINAL_S + 10, time.time(), 0)
    assert st.deltas.get("sim_agg_suppressed") == 1
    assert st.deltas.get("sim_exec_residual_dup") == 1, \
        "a per-exec S1 would re-ingest the venue's aggregate row"
    assert st.deltas.get("poll_mult.one") == 1


# ── 11. B3: identical execs collapse exactly like ON CONFLICT ───────
def test_identical_execs_collapse_like_on_conflict(st):
    execs = [_mkrec(log_index=i, seen_at=time.time() - 1000)
             for i in range(1, 6)]
    g = {"execs": execs, "aggs": [], "flags": {}}
    rows = [_poll_row(execs[0])]
    st._match_wallet(TX, MAKER, g, g["execs"] + g["aggs"], rows, [], 1000.0, time.time(), 0)
    assert st.deltas.get("dup_exec") == 4, "five identical orders, one key"
    assert st.deltas.get("sim_exec_suppressed") == 1
    assert st.deltas.get("sim_exec_residual_dup") is None
    assert st.deltas.get("orphan_no_row") is None
    assert st.deltas.get("orphan_excess_exec") is None, \
        "N identical execs vs 1 row is the DB's collapse, not an extra"


# ── 12. A3/C2: asset and side are falsifiable compared FIELDS ───────
def test_asset_and_side_are_falsifiable_fields(st):
    c = _mkrec(asset="999", seen_at=time.time() - 1000)
    row = _poll_row(c, asset="111")          # venue truth says a different asset
    g = {"execs": [c], "aggs": [], "flags": {}}
    st._match_wallet(TX, MAKER, g, g["execs"] + g["aggs"], [row], [],
                     sv.ORPHAN_FINAL_S + 10, time.time(), 0)
    assert st.deltas.get("div.asset") == 1, "wrong asset must diverge, loudly"
    assert st.deltas.get("orphan_no_row") is None

    st.deltas = {}
    st.finalized.clear()
    c2 = _mkrec(seen_at=time.time() - 1000)
    row2 = _poll_row(c2, side="SELL")
    g2 = {"execs": [c2], "aggs": [], "flags": {}}
    st._match_wallet(TX, MAKER, g2, g2["execs"] + g2["aggs"], [row2], [],
                     sv.ORPHAN_FINAL_S + 10, time.time(), 0)
    assert st.deltas.get("div.side") == 1, "a flipped side must diverge"

    st.deltas = {}
    st.finalized.clear()
    c3 = _mkrec(seen_at=time.time() - sv.ORPHAN_FINAL_S - 10)
    chain_row = _poll_row(c3, source="chain", side="SELL")
    g3 = {"execs": [c3], "aggs": [], "flags": {}}
    st._match_wallet(TX, MAKER, g3, g3["execs"] + g3["aggs"], [], [chain_row],
                     sv.ORPHAN_FINAL_S + 10, time.time(), 0)
    assert st.deltas.get("orphan_chain_mismatch") == 1
    assert st.deltas.get("orphan_chain_exact") is None, \
        "a different-side chain row must NOT launder the orphan"


# ── 13. C2: orphan_chain_exact demands the full mechanism ───────────
def test_orphan_chain_exact_requires_full_field_match(st):
    c = _mkrec()
    exact = _poll_row(c, source="chain")
    assert st._chain_exact(c, [dict(exact)]) is True
    for field, val in (("asset", "42"), ("side", "SELL"),
                       ("size", "9.000000"), ("price", "0.999999")):
        assert st._chain_exact(c, [dict(exact, **{field: val})]) is False, \
            f"perturbed {field} must break the exact claim"
    assert st._chain_exact(c, [dict(exact, ts_epoch=TS0 + 1)]) is False


# ── 14. A5/D3: the resolver records truth, never invents wallclock ──
def test_resolver_never_invents_wallclock(st):
    st.http_url = "http://rpc.test"
    rec = _mkrec(ts=None, seen_at=time.time() - 3)
    rec["block"] = 123
    st.pending[(rec["tx"], rec["log_index"], rec["wallet"])] = rec

    st._client = _FakeClient(_Resp(200, {"result": None}))
    asyncio.run(st._resolve_ts())
    assert rec["ts"] is None, "an empty RPC result must NOT become a ts"
    assert st.deltas.get("ts_rpc_empty_first") == 1

    asyncio.run(st._resolve_ts())
    assert st.deltas.get("ts_rpc_empty_retry") == 1
    assert rec["ts"] is None

    st._client = _FakeClient(_Resp(429))
    asyncio.run(st._resolve_ts())
    assert st.deltas.get("ts_rpc_backoffs") == 1
    assert st.rpc_backoff_until > time.time()
    assert rec["ts"] is None
    assert not st.ts_cache, "no invented value may enter the shadow cache"


# ── 15. A4: the live BlockTimestampCache is never touched ───────────
def test_shadow_never_touches_live_blocks_cache(st):
    bc = BlockTimestampCache(None, "http://x")
    bc._cache[65_000_000] = 111
    before = dict(bc._cache)
    cache_obj = bc._cache
    lst = _Listener(roster=_roster((MAKER, 1, "m")), blocks=bc)
    shadow_observe(lst, MAKER_EV)
    assert st.deltas.get("live_cache_hit") == 1, "membership peek only"

    ev2 = _ev(MAKER, TAKER, 0, TOKEN_INT, 0x747548, 0xBBD5F0,
              block=65_000_001, log_index=5, tx="0x" + "f2" * 32)
    shadow_observe(lst, ev2)
    assert st.deltas.get("live_cache_miss") == 1

    st.http_url = "http://rpc.test"
    st._client = _FakeClient(_Resp(200, {"result": {"timestamp": hex(TS0)}}))
    for r in st.pending.values():
        r["seen_at"] = time.time() - 3
    asyncio.run(st._resolve_ts())
    assert bc._cache is cache_obj and bc._cache == before, \
        "the shadow resolved its OWN ts without writing the live cache"
    assert 65_000_001 not in bc._cache


# ── 16. A5/B4: availability and delta histograms ────────────────────
def test_ts_avail_histogram_and_delta_histogram(st):
    now = time.time()
    ages = {2: "le3s", 5: "le6s", 30: "le60s"}
    recs = [_mkrec(ts=None, log_index=i, seen_at=now - age)
            for i, age in enumerate(ages)]
    st._assign_ts(777, int(now), recs)
    for b in ages.values():
        assert st.deltas.get("ts_avail." + b) == 1

    c = _mkrec(ts=TS0 + 2, seen_at=now - 1000)
    row = _poll_row(c, ts=TS0,
                    key=make_dedupe_key(TX, c["asset"], c["side"],
                                        Decimal("4.000000"), 0.62, TS0))
    st._diagnose_pair(TX, MAKER, row, c, 0)
    assert st.deltas.get("div.ts") == 1
    assert st.deltas.get("ts_delta.2") == 1, \
        "a +2s systematic offset must be visible as an offset, not jitter"


# ── 17. A6: refusals partitioned roster vs foreign ──────────────────
def test_foreign_vs_roster_undecodable_partition(st):
    m1, m2 = "0x" + "41" * 20, "0x" + "42" * 20
    ev = _ev(m1, m2, 0, 1111, 0, 1_000_000, log_index=11)
    shadow_observe(_Listener(roster={}), ev)
    assert st.deltas.get("undecodable_foreign.zero_amount") == 1
    assert not any(k.startswith("undecodable_roster.") for k in st.deltas)

    ev2 = _ev(m1, m2, 0, 1111, 0, 1_000_000, log_index=12,
              tx="0x" + "f3" * 32)
    shadow_observe(_Listener(roster=_roster((m2, 3, "w3"))), ev2)
    assert st.deltas.get("undecodable_roster.zero_amount") == 1, \
        "only the roster bucket may gate the flip"


# ── 18. Replay tagging, dup delivery, stale replay drop ─────────────
def test_replay_and_dup_handling(st):
    lst = _Listener(roster=_roster((MAKER, 1, "m")))
    shadow_observe(lst, MAKER_EV)
    shadow_observe(lst, MAKER_EV)            # resubscribe re-delivery
    assert st.deltas.get("dup_event") == 1
    assert len(st.pending) == 1

    lst._shadow_replay = True
    ev2 = _ev(MAKER, TAKER, 0, TOKEN_INT, 0x747548, 0xBBD5F0,
              log_index=8, tx="0x" + "f4" * 32)
    shadow_observe(lst, ev2)
    rep = st.pending[("0x" + "f4" * 32, 8, MAKER, "exec_owner")]
    assert rep["replay"] is True and st.deltas.get("replay_seen") == 1

    rep["ts"] = TS0
    st._record_latency(rep, _poll_row(rep))
    assert not st.lags, "replay records must not pollute the lag reservoir"

    rep["ts"] = None
    st._assign_ts(65_000_000, int(time.time()) - 10_801, [rep])
    assert ("0x" + "f4" * 32, 8, MAKER, "exec_owner") not in st.pending
    assert st.deltas.get("replay_stale_dropped") == 1


# ── 19. Self-trade and whale-vs-whale ───────────────────────────────
def test_self_trade_and_whale_vs_whale(st):
    ev = _ev(MAKER, MAKER, 0, 1111, 620_000, 1_000_000, log_index=21)
    shadow_observe(_Listener(roster=_roster((MAKER, 1, "m"))), ev)
    assert st.deltas.get("self_trade") == 1
    assert len(st.pending) == 2, \
        "self-trade yields BOTH view records — the venue books both sides"
    sides_st = sorted(r["side"] for r in st.pending.values())
    assert sides_st == ["BUY", "SELL"]

    recs, _, _ = decode_shadow_views(
        MAKER_EV, _roster((MAKER, 1, "m"), (TAKER, 2, "t")), {EXCH})
    assert len(recs) == 2
    sides = {r["wallet"]: r["side"] for r in recs}
    assert sides[MAKER] == "BUY" and sides[TAKER] == "SELL", \
        "whale-vs-whale: two records, opposite sides, matched per wallet"


# ── 20. Bounded memory with counted eviction ────────────────────────
def test_bounds_all_counted(st):
    for i in range(sv.PENDING_CAP):
        st.pending[(f"0xpre{i}", 1, "w")] = {"i": i}
    for i in range(sv.SEEN_TX_CAP):
        st.seen_tx[f"0xtx{i}"] = time.time()
    for i in range(sv.SEEN_EVENTS_CAP):
        st.seen_events[(f"0xev{i}", 1)] = time.time()
    lst = _Listener(roster=_roster((MAKER, 1, "m")))
    for i in range(10):
        ev = _ev(MAKER, TAKER, 0, TOKEN_INT, 0x747548, 0xBBD5F0,
                 log_index=1, tx="0x" + format(i, "064x"))
        shadow_observe(lst, ev)
    assert len(st.pending) == sv.PENDING_CAP
    assert st.deltas.get("pending_overflow_dropped") == 10
    assert len(st.seen_tx) == sv.SEEN_TX_CAP
    assert st.deltas.get("seen_tx_evicted") == 10
    assert len(st.seen_events) == sv.SEEN_EVENTS_CAP

    for i in range(sv.TS_CACHE_CAP + 10):
        st._assign_ts(i, int(time.time()), [])
    assert len(st.ts_cache) == sv.TS_CACHE_CAP


# ── 21. Reverse probe: honesty about what it cannot see ─────────────
def test_reverse_probe_honesty(st):
    now = time.time()
    st.boot_at = now - sv.REVERSE_LOOKBACK_S - 1     # past warmup
    for i in range(sv.SEEN_TX_CAP - 8):
        st.seen_tx[f"0xh{i}"] = now          # young horizon at (near) cap
    asyncio.run(st._reverse_probe(_FakePool()))
    assert st.deltas.get("reverse_probe_skipped") == 1

    st2 = ShadowV2()
    st2.boot_at = now - sv.REVERSE_LOOKBACK_S - 1
    lst = _Listener(roster=_roster((MAKER, 1, "m")))
    st2.listener = __import__("weakref").ref(lst)
    gap_ts = int(now) - 300
    st2.gaps.append({"from": gap_ts - 30, "to": gap_ts + 30,
                     "kind": "quiet_or_outage"})
    rows = [
        {"tx": "0xingap", "whale_id": 1, "source": "poll",
         "dedupe_key": "k1", "ts_epoch": gap_ts},
        {"tx": "0xnowhere", "whale_id": 1, "source": "poll",
         "dedupe_key": "k2", "ts_epoch": int(now) - 60},
        {"tx": "0xchained", "whale_id": 1, "source": "poll",
         "dedupe_key": "k3", "ts_epoch": int(now) - 61},
        {"tx": "0xchained", "whale_id": 1, "source": "chain",
         "dedupe_key": "k4", "ts_epoch": int(now) - 61},
    ]
    asyncio.run(st2._reverse_probe(_FakePool(rows=rows)))
    assert st2.deltas.get("poll_uncovered_quiet_gap") == 1
    assert st2.deltas.get("poll_uncovered_unexplained") == 1
    assert st2.deltas.get("poll_uncovered_chainrow") == 1
    assert st2.deltas.get("poll_rows_seen") == 3


# ── 22. A1: the SQL rides the partial index, verbatim ───────────────
def test_sql_uses_partial_index_predicate():
    for sql in (sv.SQL_TX, sv.SQL_REVERSE):
        assert "source IN ('chain','poll')" in sql, \
            "migrations/009's predicate is load-bearing — it cannot drift"
        assert "detected_at > now() - interval" in sql


# ── 23. Money-gate adjacency: no ingestion surface imported ─────────
def test_no_money_path_imports():
    src = Path(sv.__file__).read_text()
    tree = ast.parse(src)
    forbidden_calls = {"ingest_trade", "ingest_trade_result", "publish",
                       "execute_copy", "probe_trade"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            names = {a.name for a in node.names}
            assert "pipeline" not in mod and "bus" not in mod, \
                f"shadow_v2 must not import {mod}"
            assert not (forbidden_calls & names), \
                f"shadow_v2 must not import {forbidden_calls & names}"
        if isinstance(node, ast.Import):
            for a in node.names:
                assert "pipeline" not in a.name and "bus" not in a.name
        if isinstance(node, ast.Call):
            fn = node.func
            name = (fn.id if isinstance(fn, ast.Name)
                    else fn.attr if isinstance(fn, ast.Attribute) else "")
            assert name not in forbidden_calls, \
                f"shadow_v2 must never call {name}"
    # literal scan over CODE (docstrings state the prohibition, so they
    # are stripped before scanning — the AST walk above covers calls)
    code = src
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                code = code.replace(doc, "")
    for lit in ("notification_outbox", "INSERT INTO trades",
                "UPDATE trades", "DELETE FROM trades"):
        assert lit not in code, f"shadow_v2 must not touch {lit!r}"


# ── 24. Simulator self-check: the loudest possible alarm ────────────
def test_key_impl_mismatch_alarm(st):
    c = _mkrec(seen_at=time.time() - 1000)
    row = _poll_row(c, key="deadbeef" * 8)   # doctored: fields equal, key not
    g = {"execs": [c], "aggs": [], "flags": {}}
    st._match_wallet(TX, MAKER, g, g["execs"] + g["aggs"], [row], [],
                     sv.ORPHAN_FINAL_S + 10, time.time(), 0)
    assert st.deltas.get("key_impl_mismatch") == 1, \
        "all five fields equal yet key mismatch = our key model is WRONG"
    assert st.deltas.get("sim_exec_residual_dup") == 1


# ════════════════════════════════════════════════════════════════════
# Implementation-fleet kills (round 1) — each executed, confirmed by an
# independent skeptic, fixed, and pinned here permanently.
# ════════════════════════════════════════════════════════════════════

class _AckPool(_FakePool):
    """Commits the write server-side, then loses the ack (cancellation
    mid-execute) — exactly once. Models asyncpg cancel-after-commit."""

    def __init__(self, stored=None):
        super().__init__(stored=stored)
        self.lose_ack_once = True

    async def execute(self, sql, *args, timeout=None):
        self.executes.append((sql, args))
        self.stored = args[1]          # the INSERT reached the server
        if self.lose_ack_once:
            self.lose_ack_once = False
            raise asyncio.CancelledError()


# ── fleet kill 0: lost-ack flush must not double-count ──────────────
def test_lost_ack_flush_never_double_counts(st):
    pool = _AckPool(stored=json.dumps({"counters": {}}))
    st.deltas = {"sim_exec_suppressed": 7, "div.side": 1}
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(st._flush(pool))
    assert st._await_ack is not None, "unacked write must stay armed"
    first = json.loads(pool.executes[0][1][1])
    assert first["counters"]["sim_exec_suppressed"] == 7

    asyncio.run(st._flush(pool))       # retry reads the committed row back
    final = json.loads(pool.executes[-1][1][1])
    assert final["counters"]["sim_exec_suppressed"] == 7, \
        "the committed-but-unacked snap must be reconciled, never re-added"
    assert final["counters"]["div.side"] == 1
    assert "sim_exec_suppressed" not in st.deltas


def test_failed_send_still_retains_deltas(st):
    # the mirror case: execute raises and NOTHING committed — deltas stay
    pool = _FakePool(stored=json.dumps({"counters": {}}))
    pool.fail_execute = True
    st.deltas = {"sim_exec_suppressed": 3}
    asyncio.run(st._flush(pool))
    assert st.deltas.get("sim_exec_suppressed") == 3
    pool.fail_execute = False
    asyncio.run(st._flush(pool))       # stored has no matching snap_id
    written = json.loads(pool.executes[-1][1][1])
    assert written["counters"]["sim_exec_suppressed"] == 3, \
        "an uncommitted snap is retained and lands exactly once"


# ── fleet kill 1: a hanging resolver cannot starve reconcile/flush ──
def test_hanging_resolver_cannot_starve_reconcile(st, monkeypatch):
    monkeypatch.setattr(sv, "TS_RESOLVE_BUDGET_S", 0.05)

    async def _hang():
        await asyncio.sleep(60)

    st._resolve_ts = _hang
    recons = []

    async def _recon():
        recons.append(1)

    st._reconcile = _recon
    st.tick_n = sv.RECONCILE_EVERY - 1

    async def _go():
        await st.tick()
    asyncio.run(_go())
    assert st.tick_n == sv.RECONCILE_EVERY, "tick_n advances BEFORE the resolver"
    assert recons == [1], "reconcile fires even when the resolver hangs"
    assert st.deltas.get("ts_resolve_timeout") == 1
    assert st.rpc_backoff_until > time.time(), "hang-type failure backs off too"


# ── fleet kill 2: logIndex-less events must not collapse ────────────
def test_logindex_less_events_do_not_collapse(st):
    lst = _Listener(roster=_roster((MAKER, 1, "m")))
    e1 = _ev(MAKER, TAKER, 0, TOKEN_INT, 0x747548, 0xBBD5F0)
    e2 = _ev(MAKER, TAKER, 0, TOKEN_INT, 0x747548, 0xBBD5F0 + 1_000_000)
    del e1["logIndex"], e2["logIndex"]
    shadow_observe(lst, e1)
    shadow_observe(lst, e2)
    assert len(st.pending) == 2, \
        "two same-tx fills without logIndex are DIFFERENT fills, both kept"
    assert st.deltas.get("dup_record") is None
    assert st.deltas.get("log_index_missing") == 2
    assert "log_index_missing" in sv.HEALTH, \
        "degraded input quality must reset the instrument-health window"


# ── fleet kill 3: the fallback pick compares size to SIZE ───────────
def test_diagnose_fallback_compares_size_to_size(st):
    right = _mkrec(size_units=10_400_000, usdc_units=6_448_000,   # 10.4 @ 0.62
                   log_index=1, seen_at=time.time() - 1000)
    decoy = _mkrec(view="exec_counter", side="SELL", asset="222",
                   size_units=620_000, usdc_units=310_000,        # 0.62 shares
                   log_index=2, seen_at=time.time() - 1000)
    row = _poll_row(right, size="10.500000")   # true divergence: size only
    pairs = st._assign_pairs([row], [right, decoy])
    assert pairs[0][1] is right, "assignment must pair size to SIZE"
    st._diagnose_pair(TX, MAKER, row, pairs[0][1], 0)
    assert st.deltas.get("div.size") == 1
    assert st.deltas.get("div.side") is None, \
        "a size-only divergence must never fabricate a side divergence"
    assert st.deltas.get("div.asset") is None


# ── fleet kill 4: a mature tx is counted once, at finalization ──────
def test_mature_tx_counted_once_at_finalize(st):
    def _grp():
        e1 = _mkrec(log_index=1, seen_at=time.time() - 1000)
        e2 = _mkrec(size_units=6_000_000, usdc_units=3_720_000,
                    log_index=2, seen_at=time.time() - 1000)
        ag = _mkrec(view="agg", size_units=10_000_000, usdc_units=6_200_000,
                    log_index=3, seen_at=time.time() - 1000)
        return {"execs": [e1, e2], "aggs": [ag], "flags": {}}

    for _ in range(3):   # three reconcile passes before rows land
        g = _grp()
        st._match_wallet(TX, MAKER, g, g["execs"] + g["aggs"],
                         [], [], 1000.0, time.time(), 0)
    assert st.deltas.get("agg_tieout_ok") is None, "no rows yet = no counting"
    assert st.deltas.get(f"pw.{MAKER}.n") is None
    assert st.deltas.get("poll_mult.other") is None

    g = _grp()
    rows = [_poll_row(e) for e in g["execs"]]
    st._match_wallet(TX, MAKER, g, g["execs"] + g["aggs"],
                     rows, [], 1000.0, time.time(), 0)
    assert st.deltas.get("agg_tieout_ok") == 1, "counted exactly once"
    assert st.deltas.get(f"pw.{MAKER}.n") == 2
    assert st.deltas.get("compared_execs") == 2


# ── fleet kill 5: anomalous records are popped at finalization ──────
def test_anomalous_records_are_popped_at_finalize(st):
    agg = _mkrec(view="agg", side="BUY", asset="1111",
                 size_units=10_000_000, usdc_units=6_200_000, log_index=1,
                 seen_at=time.time() - sv.ORPHAN_FINAL_S - 10)
    bad = _mkrec(view="exec_counter", side="BUY", owner_side="SELL",
                 asset="2222", size_units=6_000_000, usdc_units=2_280_000,
                 log_index=2, seen_at=time.time() - sv.ORPHAN_FINAL_S - 10)
    for r in (agg, bad):
        st.pending[(r["tx"], r["log_index"], r["wallet"], r["view"])] = r
    rows_by = {}
    st._reconcile_tx(TX, [agg, bad], rows_by, time.time(), 0)
    assert not st.pending, \
        "classifier-dropped records must not leak in pending forever"
    assert st.deltas.get("mint_side_anomaly") == 1
    # a second sweep has nothing to re-classify: by_tx is built from
    # pending, which is now empty — the flag can never re-bump


# ── fleet kill 6: whale-vs-whale counter execs stay in the sim ──────
def test_whale_vs_whale_counter_is_simulated_without_agg(st):
    recs, _, _ = decode_shadow_views(
        MAKER_EV, _roster((TAKER, 2, "takerwhale")), {EXCH})
    (c,) = recs   # TAKER's exec_counter SELL — no agg view for this wallet
    g = classify_mints(recs)[TAKER]
    assert len(g["execs"]) == 1, \
        "an exec_counter without an aggregate is a REAL fill, not agg_missing refuse"
    assert g["execs"][0]["view"] == "exec_counter"
    assert g["flags"].get("agg_missing") == 1, "condition stays counted"

    c2 = g["execs"][0]
    c2.update(log_index=5, replay=False, seen_at=time.time() - 1000,
              ts=TS0, ts_tries=0)
    row = _poll_row(c2)
    st._match_wallet(TX, TAKER, g, g["execs"] + g["aggs"],
                     [row], [], 1000.0, time.time(), 0)
    assert st.deltas.get("sim_exec_suppressed") == 1, \
        "the whale-vs-whale poll row is key-tested, not silently skipped"


# ── fleet kill 7: valid-JSON-but-non-object state must self-heal ────
def test_non_dict_state_row_recovers(st):
    for corrupt in ("null", "[1,2]", "42", '"str"',
                    json.dumps({"counters": "nope"})):
        s = ShadowV2()
        s.deltas = {"events_seen": 5}
        pool = _FakePool(stored=corrupt)
        asyncio.run(s._flush(pool))
        assert len(pool.executes) == 1, \
            f"stored={corrupt!r} must be reset and rewritten, not fail forever"
        written = json.loads(pool.executes[0][1][1])
        assert written["counters"]["events_seen"] == 5
        assert written["writer"] == sv.WRITER_ID


# ── fleet kill 8: the reverse probe warms up after every boot ───────
def test_reverse_probe_warms_up_after_boot(st):
    lst = _Listener(roster=_roster((MAKER, 1, "m")))
    st.listener = __import__("weakref").ref(lst)
    row = {"tx": "0xprebootfill", "whale_id": 1, "source": "poll",
           "dedupe_key": "kpre", "ts_epoch": int(time.time()) - 60}
    asyncio.run(st._reverse_probe(_FakePool(rows=[row])))
    assert st.deltas.get("reverse_probe_warmup") == 1
    assert st.deltas.get("poll_uncovered_unexplained") is None, \
        "a pre-boot fill must never read as uncovered (false GATING reset)"

    st.boot_at = time.time() - sv.REVERSE_LOOKBACK_S - 1
    asyncio.run(st._reverse_probe(_FakePool(rows=[row])))
    assert st.deltas.get("poll_uncovered_unexplained") == 1, \
        "past warmup the same hole is reported honestly"


# ── fleet kill 10: per_whale evidence survives restarts ─────────────
def test_per_whale_rebuilt_from_counters(st):
    lst = _Listener(roster=_roster((MAKER, 1, "0x076daa87")))
    st.listener = __import__("weakref").ref(lst)
    stored = json.dumps({"counters": {f"pw.{MAKER}.n": 120,
                                      f"pw.{MAKER}.supp": 118,
                                      f"pw.{MAKER}.div": 2}})
    st.deltas = {"events_seen": 1}
    pool = _FakePool(stored=stored)
    asyncio.run(st._flush(pool))    # fresh process: per_whale_mem is EMPTY
    written = json.loads(pool.executes[0][1][1])
    pw = written["per_whale"][MAKER]
    assert pw["n"] == 120 and pw["supp"] == 118 and pw["div"] == 2, \
        "TARGET evidence is rebuilt from counters, not process memory"
    assert pw["username"] == "0x076daa87", "username resolves via the roster"
    assert pw["lag_p50_s"] is None


# ── fleet kill 11: the atomic fence rejects the racing write ────────
def test_atomic_fence_rejects_racing_write(st):
    class _RacePool(_FakePool):
        async def execute(self, sql, *args, timeout=None):
            self.executes.append((sql, args))
            return "INSERT 0 0"      # conditional upsert matched nothing

    pool = _RacePool(stored=None)    # empty read: fence passes in python
    st.deltas = {"div.side": 1}
    asyncio.run(st._flush(pool))
    assert st.deltas.get("writer_conflict") == 1, \
        "UPDATE 0 means a foreign writer won the race after our read"
    assert st.deltas.get("div.side") == 1, \
        "the losing writer's GATING increment must survive to the next flush"
    assert st._await_ack is None
    assert "WHERE" in sv.SQL_FLUSH and "writer" in sv.SQL_FLUSH, \
        "the flush upsert must stay conditional"


# ════════════════════════════════════════════════════════════════════
# Implementation-fleet kills (round 2) — the fixed build re-attacked;
# 20 confirmed kills, each fixed and pinned here permanently.
# ════════════════════════════════════════════════════════════════════

# ── fleet2 K0/K16: unverifiable ack DROPS, never re-adds ────────────
def test_unverifiable_ack_drops_snap_never_doubles(st):
    st._await_ack = ("aaaa1111", {"sim_exec_suppressed": 10, "div.side": 1})
    st.deltas = {"sim_exec_suppressed": 10, "div.side": 1, "events_seen": 3}
    pool = _FakePool(stored=json.dumps(
        {"writer": "deadbeef", "snap_id": "bbbb2222",
         "updated_at_epoch": time.time() - 3600,
         "counters": {"sim_exec_suppressed": 10, "div.side": 1}}))
    asyncio.run(st._flush(pool))
    written = json.loads(pool.executes[-1][1][1])
    assert written["counters"]["sim_exec_suppressed"] == 10, \
        "a takeover-inherited snap must NOT be re-added (undercount-only)"
    assert written["counters"]["div.side"] == 1
    assert written["counters"]["events_seen"] == 3
    assert st.deltas.get("ack_dropped_unverified") is None  # flushed through
    assert written["counters"]["ack_dropped_unverified"] == 1
    assert "ack_dropped_unverified" in sv.HEALTH


def test_never_committed_ack_keeps_deltas(st):
    # mirror: our own row WITHOUT our snap_id = provably never landed
    st._await_ack = ("aaaa1111", {"sim_exec_suppressed": 5})
    st.deltas = {"sim_exec_suppressed": 5}
    pool = _FakePool(stored=json.dumps(
        {"writer": sv.WRITER_ID, "snap_id": "older999",
         "updated_at_epoch": time.time() - 30, "counters": {}}))
    asyncio.run(st._flush(pool))
    written = json.loads(pool.executes[-1][1][1])
    assert written["counters"]["sim_exec_suppressed"] == 5, \
        "an uncommitted snap lands exactly once on retry"


# ── fleet2 K1: keepalive stays under the takeover threshold ─────────
def test_keepalive_beats_takeover_threshold(st):
    assert sv.FLUSH_IDLE_S + 90 < sv.TAKEOVER_S, \
        "max idle (keepalive + reconcile cadence) must undercut takeover"
    now = time.time()
    st.deltas = {}
    st._await_ack = None
    st.last_flush_ok = now - sv.FLUSH_IDLE_S - 1
    pool = _FakePool(stored=json.dumps({"counters": {}}))
    asyncio.run(st._flush(pool))
    assert len(pool.executes) == 1, "idle keepalive fires past FLUSH_IDLE_S"

    st2 = ShadowV2()
    st2.bump("events_seen")
    pool2 = _FakePool(stored=json.dumps(
        {"writer": "deadbeef", "updated_at_epoch": now - 360, "counters": {}}))
    asyncio.run(st2._flush(pool2))
    assert pool2.executes == [], \
        "a 360s-old row is a HEALTHY writer's row, not abandoned"
    assert st2.deltas.get("writer_conflict") == 1


# ── fleet2 K2/K17: TARGET evidence cannot be hex-sorted away ────────
def test_per_whale_ranks_roster_and_activity_not_hex(st):
    target = "0xffff" + "ff" * 18    # sorts LAST lexicographically
    lst = _Listener(roster={target: {"id": 9, "username": "0x076daa87"}})
    st.listener = __import__("weakref").ref(lst)
    counters = {}
    for i in range(70):              # 70 dead wallets that sort before it
        counters[f"pw.0x{i:040x}.n"] = 1
    counters[f"pw.{target}.n"] = 120
    counters[f"pw.{target}.div"] = 3
    st.deltas = {"events_seen": 1}
    pool = _FakePool(stored=json.dumps({"counters": counters}))
    asyncio.run(st._flush(pool))
    written = json.loads(pool.executes[-1][1][1])
    assert target in written["per_whale"], \
        "the roster/target whale must NEVER be evicted by hex order"
    assert written["per_whale"][target]["div"] == 3
    assert written["counters"].get("pw_pruned", 0) > 0, \
        "immortal dead-wallet pw.* keys are pruned, not hoarded"


# ── fleet2 K3: seen_tx recency tracks position ──────────────────────
def test_seen_tx_refresh_moves_to_end(st):
    lst = _Listener(roster=_roster((MAKER, 1, "m")))
    e_old = _ev(MAKER, TAKER, 0, TOKEN_INT, 0x747548, 0xBBD5F0,
                tx="0x" + "aa" * 32, log_index=1)
    e_new = _ev(MAKER, TAKER, 0, TOKEN_INT, 0x747548, 0xBBD5F0,
                tx="0x" + "bb" * 32, log_index=2)
    shadow_observe(lst, e_old)
    shadow_observe(lst, e_new)
    e_old2 = _ev(MAKER, TAKER, 0, TOKEN_INT, 0x747548, 0xBBD5F0,
                 tx="0x" + "aa" * 32, log_index=3)
    shadow_observe(lst, e_old2)      # re-touch the OLD tx
    assert next(iter(st.seen_tx)) == "0x" + "bb" * 32, \
        "position 0 must be the true oldest or the skip guard lies"


# ── fleet2 K4: gap ledger merges flaps and outlives the lookback ────
def test_gap_ledger_merges_and_prunes_by_age(st):
    now = time.time()
    lst = _Listener(roster=_roster((MAKER, 1, "m")))
    st.last_ensure_at = now - 70
    st.last_observe_at = now - 70
    ensure_shadow_task_backup = sv._TASK

    async def _go():
        sv._TASK = None
        try:
            for i in range(20):      # sustained flapping: 20 reconnects
                st.last_ensure_at = time.time() - 70
                st.last_observe_at = time.time() - 70
                sv.ensure_shadow_task(lst)
        finally:
            if sv._TASK is not None:
                sv._TASK.cancel()
                try:
                    await sv._TASK
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            sv._TASK = ensure_shadow_task_backup
    asyncio.run(_go())
    assert len(st.gaps) == 1, \
        "contiguous flap gaps merge into one span, not one entry each"
    st.gaps.appendleft({"from": now - sv.REVERSE_LOOKBACK_S - 400,
                        "to": now - sv.REVERSE_LOOKBACK_S - 350,
                        "kind": "reconnect"})
    st.last_ensure_at = time.time() - 70
    st.last_observe_at = time.time() - 70

    async def _go2():
        sv._TASK = None
        try:
            sv.ensure_shadow_task(lst)
        finally:
            if sv._TASK is not None:
                sv._TASK.cancel()
                try:
                    await sv._TASK
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            sv._TASK = None
    asyncio.run(_go2())
    assert all(g["to"] >= time.time() - sv.REVERSE_LOOKBACK_S - 300
               for g in st.gaps), "gaps older than the lookback are pruned"


# ── fleet2 K5: exchange_set keyed by value, immune to id() reuse ────
def test_exchange_set_survives_id_reuse(st):
    l1 = _Listener(addresses=["0xAAA1"])
    s1 = st.exchange_set(l1)
    assert s1 == {"0xaaa1"}
    l2 = _Listener(addresses=["0xBBB2"])
    s2 = st.exchange_set(l2)
    assert s2 == {"0xbbb2"}, "a different address list must never hit stale cache"


# ── fleet2 K6: logIndex-less redelivery dedupes by content ──────────
def test_logindex_less_redelivery_dedupes_by_content(st):
    lst = _Listener(roster=_roster((MAKER, 1, "m")))
    e1 = _ev(MAKER, TAKER, 0, TOKEN_INT, 0x747548, 0xBBD5F0)
    del e1["logIndex"]
    shadow_observe(lst, e1)
    shadow_observe(lst, dict(e1))    # exact redelivery
    assert st.deltas.get("dup_event") == 1, \
        "same content redelivered = dup, even without logIndex"
    assert len(st.pending) == 1
    e2 = _ev(MAKER, TAKER, 0, TOKEN_INT, 0x747548, 0xBBD5F0 + 1_000_000)
    del e2["logIndex"]
    shadow_observe(lst, e2)          # DIFFERENT fill, same tx
    assert len(st.pending) == 2, "distinct fills still both kept"


# ── fleet2 K7: an agg-matched row fabricates no field divergence ────
def test_agg_matched_row_is_granularity_not_divergence(st):
    e1 = _mkrec(log_index=1, seen_at=time.time() - sv.ORPHAN_FINAL_S - 10)
    e2 = _mkrec(size_units=6_000_000, usdc_units=3_720_000, log_index=2,
                seen_at=time.time() - sv.ORPHAN_FINAL_S - 10)
    ag = _mkrec(view="agg", size_units=10_000_000, usdc_units=6_200_000,
                log_index=3, seen_at=time.time() - sv.ORPHAN_FINAL_S - 10)
    g = {"execs": [e1, e2], "aggs": [ag], "flags": {}}
    rows = [_poll_row(ag)]           # venue published AGGREGATE granularity
    st._match_wallet(TX, MAKER, g, g["execs"] + g["aggs"], rows, [],
                     sv.ORPHAN_FINAL_S + 10, time.time(), 0)
    assert st.deltas.get("div.size") is None, \
        "aggregate granularity is N3 evidence, NOT a decode divergence"
    assert st.deltas.get("orphan_excess_exec") is None, \
        "legs that tie out against an agg-matched row are not extras"
    assert st.deltas.get("exec_covered_by_agg_row") == 2
    assert st.deltas.get("granularity_row") == 1
    assert st.deltas.get("sim_agg_suppressed") == 1


# ── fleet2 K8: a late-landing divergent sibling is never invisible ──
def test_late_sibling_row_still_tested(st):
    def _grp(seen_at):
        e1 = _mkrec(log_index=1, seen_at=seen_at)
        e2 = _mkrec(size_units=6_000_000, usdc_units=3_720_000,
                    log_index=2, seen_at=seen_at)
        return {"execs": [e1, e2], "aggs": [], "flags": {}}

    early = time.time() - 1000
    g = _grp(early)
    first_row = _poll_row(g["execs"][0])
    st._match_wallet(TX, MAKER, g, g["execs"] + g["aggs"],
                     [first_row], [], 1000.0, time.time(), 0)
    assert st.deltas.get("compared_execs") is None, \
        "ONE landed row of two must NOT finalize — the sibling is pending"

    g2 = _grp(early)
    late_row = _poll_row(g2["execs"][1], size="6.500000")   # DIVERGENT
    st._match_wallet(TX, MAKER, g2, g2["execs"] + g2["aggs"],
                     [first_row, late_row], [], sv.ORPHAN_FINAL_S + 10,
                     time.time(), 0)
    assert st.deltas.get("div.size") == 1, \
        "the late divergent row MUST be tested — this was the one shape " \
        "that could read the flip green on bad evidence"


# ── fleet2 K9: tombstones block recount after seen_events eviction ──
def test_finalized_tombstone_blocks_recount(st):
    r1 = _mkrec(log_index=1, seen_at=time.time() - 1000)
    row = _poll_row(r1)
    g = {"execs": [r1], "aggs": [], "flags": {}}
    st._match_wallet(TX, MAKER, g, [r1], [row], [], 1000.0, time.time(), 0)
    assert st.deltas.get("compared_execs") == 1
    assert (TX, MAKER) in st.finalized

    r2 = _mkrec(log_index=1, seen_at=time.time() - 1000)
    st.pending[(r2["tx"], r2["log_index"], r2["wallet"], r2["view"])] = r2
    st._reconcile_tx(TX, [r2], {(TX, 1): [row]}, time.time(), 0)
    assert st.deltas.get("compared_execs") == 1, \
        "a re-pended finalized fill must never count twice"
    assert st.deltas.get("refinalize_blocked") == 1
    assert not st.pending


# ── fleet2 K11: no-coverage is not a residual dup ───────────────────
def test_uncovered_policy_rows_do_not_gate(st):
    c = _mkrec(view="exec_owner", seen_at=time.time() - 1000)  # maker fill
    row = _poll_row(c)
    g = {"execs": [c], "aggs": [], "flags": {}}
    st._match_wallet(TX, MAKER, g, [c], [row], [], 1000.0, time.time(), 0)
    assert st.deltas.get("sim_agg_residual_dup") is None, \
        "no aggregate view exists — the agg policy ingests NOTHING here"
    assert st.deltas.get("sim_agg_uncovered") == 1
    assert st.deltas.get("sim_exec_suppressed") == 1


# ── fleet2 K12: every orphan_excess_exec instance is attributed ─────
def test_orphan_excess_exec_writes_example(st):
    c1 = _mkrec(log_index=1, seen_at=time.time() - sv.ORPHAN_FINAL_S - 10)
    c2 = _mkrec(size_units=9_000_000, usdc_units=5_580_000, log_index=2,
                seen_at=time.time() - sv.ORPHAN_FINAL_S - 10)
    g = {"execs": [c1, c2], "aggs": [], "flags": {}}
    row = _poll_row(c1)
    st._match_wallet(TX, MAKER, g, [c1, c2], [row], [],
                     sv.ORPHAN_FINAL_S + 10, time.time(), 0)
    assert st.deltas.get("orphan_excess_exec") == 1
    assert any(e["kind"] == "orphan_excess_exec" for e in st.examples), \
        "N4 demands every excess instance attributed in examples"


# ── fleet2 K13: global assignment beats greedy stealing ─────────────
def test_global_assignment_prevents_candidate_stealing(st):
    a = _mkrec(size_units=10_000_000, usdc_units=5_000_000, log_index=1)  # 10@0.50
    b = _mkrec(size_units=10_000_000, usdc_units=6_000_000, log_index=2)  # 10@0.60
    r1 = _poll_row(a, price=0.58)            # closest to B but belongs to A's slot
    r2 = _poll_row(b, ts=TS0 + 2)            # exact price match to B, ts differs
    pairs = dict((id(row), pick) for row, pick in st._assign_pairs([r1, r2], [a, b]))
    assert pairs[id(r2)] is b, "the exact-price row keeps ITS candidate"
    assert pairs[id(r1)] is a, "the noisy row takes the remaining one"


# ── fleet2 K14: price sanity honours both rounding variants ─────────
def test_price_sanity_uses_both_variants(st):
    ev = _ev(MAKER, TAKER, 0, TOKEN_INT, 1, 2_000_000, log_index=31)
    recs, reason, _ = decode_shadow_views(ev, _roster((MAKER, 1, "m")), {EXCH})
    assert reason is None, \
        "round() ties-to-even 0.0 must not refuse what HALF_UP keeps"
    assert recs and recs[0].get("price_variant_disagree") is True


# ── fleet2 K15: residuals of BOTH policies reset the window ─────────
def test_both_residuals_and_leading_flip_reset_window(st):
    pool = _FakePool(stored=json.dumps(
        {"counters": {"sim_exec_suppressed": 100, "sim_agg_suppressed": 1},
         "window_start": 1000.0, "health_start": 1000.0,
         "leading_policy": "exec", "writer": None}))
    st.deltas = {"sim_agg_residual_dup": 1}   # NON-leading policy residual
    asyncio.run(st._flush(pool))
    written = json.loads(pool.executes[-1][1][1])
    assert written["window_start"] != 1000.0, \
        "a residual of EITHER policy must restart the evidence clock"
    # round-3 kill: gating + watermarked = Δ structurally 0 in the very
    # flush that moved it — residuals render cumulatively instead
    assert "sim_exec_residual_dup" not in sv.VOLUME_KEYS
    assert "sim_agg_residual_dup" not in sv.VOLUME_KEYS

    pool2 = _FakePool(stored=json.dumps(
        {"counters": {"sim_exec_suppressed": 1, "sim_agg_suppressed": 100},
         "at_window": {"sim_exec_suppressed": 1, "sim_agg_suppressed": 0},
         "window_start": 1000.0, "health_start": 1000.0,
         "leading_policy": "exec", "writer": None}))
    st2 = ShadowV2()
    st2.deltas = {"events_seen": 1}
    asyncio.run(st2._flush(pool2))
    written2 = json.loads(pool2.executes[-1][1][1])
    assert written2["leading_policy"] == "agg", \
        "a DECISIVE since-window inversion flips leading"
    assert written2["window_start"] != 1000.0, \
        "a leading-policy crossover restarts the clock too"
    assert written2["counters"].get("leading_flip") == 1


def test_leading_does_not_flap_on_cumulative_tie(st):
    # round-3 kill: cumulative >= tie-break reset the window every flush
    # on balanced traffic; hysteresis on window deltas must hold steady
    pool = _FakePool(stored=json.dumps(
        {"counters": {"sim_exec_suppressed": 100, "sim_agg_suppressed": 101},
         "at_window": {"sim_exec_suppressed": 98, "sim_agg_suppressed": 99},
         "window_start": 1000.0, "health_start": 1000.0,
         "leading_policy": "exec", "writer": None}))
    st.deltas = {"events_seen": 1}
    asyncio.run(st._flush(pool))
    written = json.loads(pool.executes[-1][1][1])
    assert written["leading_policy"] == "exec", \
        "a knife-edge cumulative crossover must NOT flip leading"
    assert written["window_start"] == 1000.0, \
        "balanced mixed traffic must not reset the window every flush"


# ── fleet2 K18: partial corruption resets whole-window coherently ───
def test_partial_corruption_resets_coherently(st):
    pool = _FakePool(stored=json.dumps(
        {"counters": "clobbered", "window_start": 1000.0,
         "at_window": {"sim_exec_suppressed": 85},
         "writer": None}))
    st.deltas = {"sim_exec_suppressed": 2}
    asyncio.run(st._flush(pool))
    written = json.loads(pool.executes[-1][1][1])
    assert written["counters"]["sim_exec_suppressed"] == 2
    assert written["at_window"].get("sim_exec_suppressed", 0) <= 2, \
        "old watermarks must not survive a counter reset (negative deltas)"
    assert written["counters"]["corrupt_reset"] == 1, "the wipe is VISIBLE"
    assert "corrupt_reset" in sv.HEALTH

    pool2 = _FakePool(stored=json.dumps(
        {"counters": {}, "window_start": "garbage", "writer": None}))
    st2 = ShadowV2()
    st2.deltas = {"events_seen": 1}
    asyncio.run(st2._flush(pool2))
    written2 = json.loads(pool2.executes[-1][1][1])
    assert isinstance(written2["window_start"], (int, float)), \
        "a non-numeric window_start heals instead of poisoning jq forever"


# ════════════════════════════════════════════════════════════════════
# Implementation-fleet kills (round 3) — 11 confirmed on the twice-
# fixed build, each fixed and pinned here permanently.
# ════════════════════════════════════════════════════════════════════

# ── fleet3 K0: late rows after early finalize are STILL key-tested ──
def test_late_row_after_early_finalize_is_tested(st):
    e1 = _mkrec(log_index=1, seen_at=time.time() - 1000)
    e2 = _mkrec(size_units=6_000_000, usdc_units=3_720_000, log_index=2,
                seen_at=time.time() - 1000)
    g = {"execs": [e1, e2], "aggs": [], "flags": {}}
    rows = [_poll_row(e1), _poll_row(e2)]
    st._match_wallet(TX, MAKER, g, [e1, e2], rows, [], 1000.0, time.time(), 0)
    assert st.deltas.get("sim_exec_suppressed") == 2
    assert (TX, MAKER) in st.watch, "early finalize must arm a row watch"

    late_extra = _poll_row(e1, size="6.500000")   # divergent LATE sibling
    rows_by = {(TX, 1): rows + [late_extra]}
    st._watch_pass(rows_by, time.time())
    assert st.deltas.get("sim_exec_residual_dup") == 1, \
        "a poll row landing AFTER the finalizing pass must still be " \
        "key-tested — the shape that could read the flip green"
    assert st.deltas.get("late_row_seen") == 1
    st._watch_pass(rows_by, time.time())
    assert st.deltas.get("sim_exec_residual_dup") == 1, \
        "the same late row is scored exactly once"

    # deadline expiry drops the watch
    st.watch[(TX, MAKER)]["until"] = time.time() - 1
    st._watch_pass({}, time.time())
    assert (TX, MAKER) not in st.watch


def test_agg_only_wallet_late_rows_watched(st):
    ag = _mkrec(view="agg", size_units=10_000_000, usdc_units=6_200_000,
                log_index=3, seen_at=time.time() - 1000)
    g = {"execs": [], "aggs": [ag], "flags": {}}
    first = _poll_row(ag)
    st._match_wallet(TX, MAKER, g, [ag], [first], [], 1000.0, time.time(), 0)
    assert (TX, MAKER) in st.watch, \
        "one landed row proves nothing about an agg-only wallet's siblings"
    late1 = _poll_row(ag, size="4.000000")
    late2 = _poll_row(ag, size="6.000000")
    st._watch_pass({(TX, 1): [first, late1, late2]}, time.time())
    assert st.deltas.get("sim_agg_residual_dup") == 2, \
        "late per-exec-granularity legs are agg-policy residuals"


# ── fleet3 K1/K5: ts gating is per-record; block=0 dropped early ────
def test_unresolved_sibling_does_not_poison_resolved_records(st):
    good = _mkrec(log_index=1, seen_at=time.time() - sv.ORPHAN_FINAL_S - 10)
    bad = _mkrec(log_index=2, ts=None,
                 seen_at=time.time() - sv.ORPHAN_FINAL_S - 10)
    for r in (good, bad):
        st.pending[(r["tx"], r["log_index"], r["wallet"], r["view"])] = r
    row = _poll_row(good)
    st._reconcile_tx(TX, [good, bad], {(TX, 1): [row]}, time.time(), 0)
    assert st.deltas.get("ts_never_resolved_live") == 1, \
        "only the truly unresolved record counts (GATING must not lie)"
    assert st.deltas.get("sim_exec_suppressed") == 1, \
        "the resolved sibling's evidence is evaluated, not discarded"
    assert not st.pending


def test_blockless_record_dropped_at_observe(st):
    lst = _Listener(roster=_roster((MAKER, 1, "m")))
    ev = _ev(MAKER, TAKER, 0, TOKEN_INT, 0x747548, 0xBBD5F0, log_index=41)
    ev["blockNumber"] = "not-hex"
    shadow_observe(lst, ev)
    assert st.deltas.get("block_missing") == 1
    assert not st.pending, \
        "a record that can never resolve a ts must not poison its tx"


# ── fleet3 K2: agg-row absorption covers tie legs ONLY ──────────────
def test_agg_absorption_excludes_non_tie_execs(st):
    own = _mkrec(view="exec_owner", side="BUY",
                 size_units=5_000_000, usdc_units=3_100_000, log_index=1,
                 seen_at=time.time() - sv.ORPHAN_FINAL_S - 10)
    leg = _mkrec(view="exec_counter", side="SELL", owner_side="BUY",
                 size_units=10_000_000, usdc_units=6_200_000, log_index=2,
                 seen_at=time.time() - sv.ORPHAN_FINAL_S - 10)
    ag = _mkrec(view="agg", side="BUY",
                size_units=10_000_000, usdc_units=6_200_000, log_index=3,
                seen_at=time.time() - sv.ORPHAN_FINAL_S - 10)
    g = {"execs": [own, leg], "aggs": [ag], "flags": {}}
    rows = [_poll_row(ag)]
    st._match_wallet(TX, MAKER, g, [own, leg, ag], rows, [],
                     sv.ORPHAN_FINAL_S + 10, time.time(), 0)
    assert st.deltas.get("exec_covered_by_agg_row") == 1, \
        "only the tie-out LEG is proven by the agg-matched row"
    assert st.deltas.get("orphan_excess_exec") == 1, \
        "the exec_owner outside the tie is a would-be extra, not covered"


# ── fleet3 K3: the mint transform must prove itself ─────────────────
def test_unprovable_mint_transform_reverts_to_raw(st):
    agg = _mkrec(view="agg", side="BUY", asset="1111",
                 size_units=10_000_000, usdc_units=6_200_000, log_index=1)
    cross = _mkrec(view="exec_counter", side="SELL", owner_side="BUY",
                   asset="2222", size_units=500_000, usdc_units=310_000,
                   log_index=2)   # a DIFFERENT market's fill, agg lost
    g = classify_mints([agg, cross])[MAKER]
    assert g["flags"].get("mint_transformed") is None, \
        "a transform that does not tie out is a GUESS and must revert"
    assert g["flags"].get("mint_unresolved") == 1
    assert g["execs"][0]["asset"] == "2222", "the RAW record survives"
    assert g["execs"][0]["side"] == "SELL"
    assert agg_tieout(g) == "skip", \
        "a reverted transform must not read as a layout failure"

    row = _poll_row(g["execs"][0])
    for r in g["execs"] + g["aggs"]:
        r.update(replay=False, seen_at=time.time() - sv.ORPHAN_FINAL_S - 10,
                 ts_tries=0)
    st._match_wallet(TX, MAKER, g, g["execs"] + g["aggs"], [row], [],
                     sv.ORPHAN_FINAL_S + 10, time.time(), 0)
    assert st.deltas.get("sim_exec_suppressed") == 1, \
        "the raw cross-market record key-tests against its real poll row"
    assert st.deltas.get("div.side") is None
    assert st.deltas.get("agg_tieout_fail") is None

    # the genuine mint (ties out) still transforms — pinned in test 8,
    # re-asserted here against the new revert path
    roster = _roster((MAKER, 7, "whale7"))
    recs = _mint_tx_recs(roster)
    g2 = classify_mints(recs)[MAKER]
    assert g2["flags"] == {"mint_transformed": 1}
    assert agg_tieout(g2) == "ok"


# ── fleet3 K4: the agg-only no-row orphan is visible and gates ──────
def test_agg_only_orphan_no_row_fires(st):
    ag = _mkrec(view="agg", size_units=10_000_000, usdc_units=6_200_000,
                log_index=1, seen_at=time.time() - sv.ORPHAN_FINAL_S - 10)
    g = {"execs": [], "aggs": [ag], "flags": {}}
    st._match_wallet(TX, MAKER, g, [ag], [], [],
                     sv.ORPHAN_FINAL_S + 10, time.time(), 0)
    assert st.deltas.get("orphan_agg_no_row") == 1, \
        "'the venue never published it' must be visible for agg-only fills"
    assert "orphan_agg_no_row" in sv.GATING
    assert any(e["kind"] == "orphan_agg_no_row" for e in st.examples)


# ── fleet3 K6: partial corruption of at_window heals ────────────────
def test_at_window_corruption_is_caught(st):
    for bad_aw in ("nope", {"pw": "clobbered"},
                   {"sim_exec_suppressed": "many"}):
        s = ShadowV2()
        s.deltas = {"events_seen": 1}
        pool = _FakePool(stored=json.dumps(
            {"counters": {}, "window_start": 1000.0,
             "at_window": bad_aw, "writer": None}))
        asyncio.run(s._flush(pool))
        written = json.loads(pool.executes[0][1][1])
        assert written["counters"].get("corrupt_reset") == 1, \
            f"at_window={bad_aw!r} must trigger a visible corrupt reset"
        assert written["window_start"] != 1000.0


# ── fleet3 K7: prune drops the watermark with the counter ───────────
def test_prune_drops_at_window_watermark_too(st):
    target = "0xffff" + "ff" * 18
    lst = _Listener(roster={target: {"id": 9, "username": "0x076daa87"}})
    st.listener = __import__("weakref").ref(lst)
    counters = {f"pw.0x{i:040x}.n": 1 for i in range(70)}
    counters[f"pw.{target}.n"] = 120
    aw_pw = {k: v for k, v in counters.items()}
    stored = {"counters": counters, "window_start": 1000.0,
              "health_start": 1000.0,
              "at_window": {"pw": aw_pw}, "writer": None}
    st.deltas = {"events_seen": 1}
    pool = _FakePool(stored=json.dumps(stored))
    asyncio.run(st._flush(pool))
    written = json.loads(pool.executes[-1][1][1])
    pruned = [k for k in aw_pw
              if k not in written["counters"]
              and k in (written["at_window"].get("pw") or {})]
    assert not pruned, \
        "a pruned counter must take its at_window watermark with it — " \
        "a stale high watermark renders negative TARGET deltas forever"


def test_prune_never_runs_without_a_roster(st):
    st.listener = None     # listener GC'd: roster unknown, prune must wait
    counters = {f"pw.0x{i:040x}.n": 1 for i in range(70)}
    st.deltas = {"events_seen": 1}
    pool = _FakePool(stored=json.dumps({"counters": counters, "writer": None}))
    asyncio.run(st._flush(pool))
    written = json.loads(pool.executes[-1][1][1])
    kept = [k for k in counters if k in written["counters"]]
    assert len(kept) == len(counters), \
        "an empty roster view must never nuke roster whales' history"


# ── fleet3 K8: within-roster ranking is by activity ─────────────────
def test_roster_cohort_ranked_by_activity(st):
    roster = {}
    counters = {}
    for i in range(40):    # 40 ROSTER wallets, low-hex, low activity
        w = f"0x{i:040x}"
        roster[w] = {"id": i, "username": f"w{i}"}
        counters[f"pw.{w}.n"] = 1
    target = "0xffff" + "ff" * 18   # roster, sorts LAST, most active
    roster[target] = {"id": 99, "username": "0x076daa87"}
    counters[f"pw.{target}.n"] = 400
    lst = _Listener(roster=roster)
    st.listener = __import__("weakref").ref(lst)
    st.deltas = {"events_seen": 1}
    pool = _FakePool(stored=json.dumps({"counters": counters, "writer": None}))
    asyncio.run(st._flush(pool))
    written = json.loads(pool.executes[-1][1][1])
    assert target in written["per_whale"], \
        "the most-active roster whale must survive a 33+ roster"
    assert written["counters"].get("per_whale_truncated", 0) >= 1
