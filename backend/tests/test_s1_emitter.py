"""S1 emitter pins — the design-attack panel's kills, made permanent.

Every confirmed kill from the three-design adversarial panel is pinned
here against the synthesis that neutralized it, plus the collision
protocol, the certification gate, and the burn-in mode. The fixture
events are the p382s verbatim pair shared with test_shadow_v2.
"""

from __future__ import annotations

import asyncio
import json
import time
import weakref

import pytest

import sportsassets.ingestion.claim_registry as cr
import sportsassets.ingestion.s1_emitter as s1
from sportsassets.ingestion.chain import FILL_V3_TOPIC
from sportsassets.ingestion.s1_emitter import S1Emitter

MAKER = "0x2005d16a" + "aa" * 16
TAKER = "0x40841cc0" + "bb" * 16
EXCH = "0xe2222d279d744050d28e00520010520000310f59"
TOKEN_INT = int("8b435bed" + "22" * 28, 16)
TX = "0x" + "f1" * 32
TS0 = 1_724_000_000
BLK = 65_000_000


def _topic(addr: str) -> str:
    return "0x" + "00" * 12 + addr[2:]


def _ev(owner, cpty, w0, token, gave, got, fee=0, tx=TX, log_index=1,
        block=BLK, address=EXCH):
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


class _Listener:
    def __init__(self, roster=None):
        self._roster = roster or {}
        self._addresses = [EXCH]
        self._http_url = "http://rpc.test"
        self._shadow_replay = False


class _Resp:
    def __init__(self, code=200, payload=None):
        self.status_code = code
        self._p = payload

    def json(self):
        return self._p


class _FakeClient:
    def __init__(self, resp):
        self.resp = resp
        self.calls = []

    async def post(self, url, json=None):
        self.calls.append(json)
        return self.resp


class _Pool:
    def __init__(self, probe_row=None, stored=None):
        self.probe_row = probe_row
        self.stored = stored
        self.executes = []

    async def fetchrow(self, sql, *a, timeout=None):
        return self.probe_row

    async def fetchval(self, sql, *a, timeout=None):
        return self.stored

    async def execute(self, sql, *a, timeout=None):
        self.executes.append((sql, a))


@pytest.fixture(autouse=True)
def _clean_registry():
    cr._reset_for_tests()
    yield
    cr._reset_for_tests()


@pytest.fixture
def st(monkeypatch):
    monkeypatch.setenv("S1_EMITTER", "on")
    e = S1Emitter()
    e.head = BLK + s1.CONFIRM_DEPTH + 1
    e._client = _FakeClient(_Resp(200, {"result": {"timestamp": hex(TS0)}}))
    e.http_url = "http://rpc.test"
    e._state_loaded = True
    return e


def _arm(e):
    e.armed = True
    e.armed_at = time.time()
    e.cert_green = True
    e.cert_reason = "green"


def _wire(e, listener):
    e.listener = weakref.ref(listener)


def _observe_all(e, listener, events):
    for le in events:
        e.observe(listener, le)
    for entry in e.pending.values():
        entry["last_seen"] = time.time() - s1.DEBOUNCE_S - 1


def _capture_ingest(monkeypatch):
    calls = []

    async def fake_ingest(ev, notify=True):
        calls.append(ev)
        return (len(calls), True)

    import sportsassets.ingestion.pipeline as pipeline
    monkeypatch.setattr(pipeline, "ingest_trade_result", fake_ingest)
    return calls


# ── the emit set ────────────────────────────────────────────────────
def test_maker_own_event_emits_one_exec_owner_row(st, monkeypatch):
    lst = _Listener(roster={MAKER: {"id": 7, "username": "mk"}})
    _wire(st, lst)
    _arm(st)
    calls = _capture_ingest(monkeypatch)
    _observe_all(st, lst, [MAKER_EV])
    done = asyncio.run(st._finalize_tx(_Pool(), TX, st.pending[TX],
                                       time.time()))
    assert done is True
    assert len(calls) == 1
    ev = calls[0]
    assert ev.source == "chain" and ev.whale_id == 7
    assert ev.tx_hash == TX and ev.ts_epoch == TS0
    assert st.deltas.get("s1.emitted") == 1
    assert st.deltas.get("s1.emitted_exec_owner") == 1


def test_taker_pair_emits_only_the_aggregate(st, monkeypatch):
    lst = _Listener(roster={TAKER: {"id": 9, "username": "tk"}})
    _wire(st, lst)
    _arm(st)
    calls = _capture_ingest(monkeypatch)
    _observe_all(st, lst, [MAKER_EV, TAKER_EV])
    done = asyncio.run(st._finalize_tx(_Pool(), TX, st.pending[TX],
                                       time.time()))
    assert done is True
    assert len(calls) == 1, "one venue row per taker fill — the agg"
    assert st.deltas.get("s1.emitted_agg") == 1
    assert st.deltas.get("s1.emitted_exec_owner") is None


# ── panel kill 1: lost taker agg must NOT emit the complement leg ───
def test_counter_only_group_never_emits(st, monkeypatch):
    lst = _Listener(roster={TAKER: {"id": 9, "username": "tk"}})
    _wire(st, lst)
    _arm(st)
    calls = _capture_ingest(monkeypatch)
    # only the MAKER's event arrives; the taker's own agg event is lost.
    # TAKER appears solely as exec_counter — the raw complement-token
    # leg, which carries the WRONG asset/side for the whale's fill.
    _observe_all(st, lst, [MAKER_EV])
    done = asyncio.run(st._finalize_tx(_Pool(), TX, st.pending[TX],
                                       time.time()))
    assert done is True
    assert calls == [], "a counter-only group must never reach ingest"
    assert st.deltas.get("s1.abstain.counter_only") == 1


# ── panel kill 2: price-variant divergence abstains ─────────────────
def test_price_variant_divergence_abstains(st, monkeypatch):
    lst = _Listener(roster={MAKER: {"id": 7, "username": "mk"}})
    _wire(st, lst)
    _arm(st)
    calls = _capture_ingest(monkeypatch)
    real = s1.rec_keys
    monkeypatch.setattr(s1, "rec_keys",
                        lambda rec: real(rec) + [("hup", "otherkey")])
    _observe_all(st, lst, [MAKER_EV])
    done = asyncio.run(st._finalize_tx(_Pool(), TX, st.pending[TX],
                                       time.time()))
    assert done is True
    assert calls == [], "two key variants = the venue might store either"
    assert st.deltas.get("s1.abstain.price_variant") == 1


# ── key self-check: a key the pipeline would not reproduce trips ────
def test_key_selfcheck_failure_trips_sticky(st, monkeypatch):
    lst = _Listener(roster={MAKER: {"id": 7, "username": "mk"}})
    _wire(st, lst)
    _arm(st)
    calls = _capture_ingest(monkeypatch)
    monkeypatch.setattr(s1, "rec_keys",
                        lambda rec: [("round", "not-what-pipeline-computes")])
    _observe_all(st, lst, [MAKER_EV])
    asyncio.run(st._finalize_tx(_Pool(), TX, st.pending[TX], time.time()))
    assert calls == []
    assert st.tripped == "key_selfcheck" and st.armed is False
    assert st.deltas.get("s1.trip.key_selfcheck") == 1


# ── burn-in: enabled but unarmed counts, never writes ───────────────
def test_burn_in_counts_would_emit_and_writes_nothing(st, monkeypatch):
    lst = _Listener(roster={MAKER: {"id": 7, "username": "mk"}})
    _wire(st, lst)
    st.cert_green = True          # green but NOT armed
    calls = _capture_ingest(monkeypatch)
    _observe_all(st, lst, [MAKER_EV])
    done = asyncio.run(st._finalize_tx(_Pool(), TX, st.pending[TX],
                                       time.time()))
    assert done is True and calls == []
    assert st.deltas.get("s1.would_emit") == 1
    assert st.deltas.get("s1.emitted") is None


# ── collision protocol ──────────────────────────────────────────────
def test_receipt_ingested_outcome_abstains(st, monkeypatch):
    lst = _Listener(roster={MAKER: {"id": 7, "username": "mk"}})
    _wire(st, lst)
    _arm(st)
    calls = _capture_ingest(monkeypatch)
    cr.claim(TX, MAKER, "receipt")
    cr.finish(TX, MAKER, "receipt", "ingested")
    _observe_all(st, lst, [MAKER_EV])
    done = asyncio.run(st._finalize_tx(_Pool(), TX, st.pending[TX],
                                       time.time()))
    assert done is True and calls == []
    assert st.deltas.get("s1.abstain.v3_ingested") == 1


def test_receipt_refused_outcome_proceeds(st, monkeypatch):
    lst = _Listener(roster={MAKER: {"id": 7, "username": "mk"}})
    _wire(st, lst)
    _arm(st)
    calls = _capture_ingest(monkeypatch)
    cr.claim(TX, MAKER, "receipt")
    cr.finish(TX, MAKER, "receipt", "refused")
    _observe_all(st, lst, [MAKER_EV])
    done = asyncio.run(st._finalize_tx(_Pool(), TX, st.pending[TX],
                                       time.time()))
    assert done is True and len(calls) == 1
    assert st.deltas.get("s1.emitted") == 1


def test_receipt_pending_waits_then_abstains(st, monkeypatch):
    lst = _Listener(roster={MAKER: {"id": 7, "username": "mk"}})
    _wire(st, lst)
    _arm(st)
    calls = _capture_ingest(monkeypatch)
    cr.claim(TX, MAKER, "receipt")           # never finished
    _observe_all(st, lst, [MAKER_EV])
    e = st.pending[TX]
    done = asyncio.run(st._finalize_tx(_Pool(), TX, e, time.time()))
    assert done is False, "an unresolved receipt claim means wait"
    e["v3_wait_started"] = time.time() - s1.V3_WAIT_S - 1
    done = asyncio.run(st._finalize_tx(_Pool(), TX, e, time.time()))
    assert done is True and calls == []
    assert st.deltas.get("s1.abstain.v3_unknown") == 1


def test_preexisting_chain_row_abstains(st, monkeypatch):
    lst = _Listener(roster={MAKER: {"id": 7, "username": "mk"}})
    _wire(st, lst)
    _arm(st)
    calls = _capture_ingest(monkeypatch)
    _observe_all(st, lst, [MAKER_EV])
    done = asyncio.run(st._finalize_tx(_Pool(probe_row={"?": 1}), TX,
                                       st.pending[TX], time.time()))
    assert done is True and calls == []
    assert st.deltas.get("s1.abstain.chain_row_preexists") == 1


# ── the strict resolver and freshness ───────────────────────────────
def test_reorged_block_purges_the_tx(st, monkeypatch):
    lst = _Listener(roster={MAKER: {"id": 7, "username": "mk"}})
    _wire(st, lst)
    _arm(st)
    calls = _capture_ingest(monkeypatch)
    ev = dict(MAKER_EV, blockHash="0x" + "ab" * 32)
    _observe_all(st, lst, [ev])
    st._client = _FakeClient(_Resp(200, {"result": {
        "timestamp": hex(TS0), "hash": "0x" + "cd" * 32}}))
    done = asyncio.run(st._finalize_tx(_Pool(), TX, st.pending[TX],
                                       time.time()))
    assert done is True and calls == []
    assert st.deltas.get("s1.abstain.reorged") == 1


def test_unresolved_ts_abstains_after_budget_never_wallclock(st, monkeypatch):
    lst = _Listener(roster={MAKER: {"id": 7, "username": "mk"}})
    _wire(st, lst)
    _arm(st)
    calls = _capture_ingest(monkeypatch)
    st._client = _FakeClient(_Resp(200, {"result": {}}))   # no timestamp
    _observe_all(st, lst, [MAKER_EV])
    e = st.pending[TX]
    done = asyncio.run(st._finalize_tx(_Pool(), TX, e, time.time()))
    assert done is False, "inside the budget the resolver retries"
    e["ts_started"] = time.time() - s1.TS_BUDGET_S - 1
    done = asyncio.run(st._finalize_tx(_Pool(), TX, e, time.time()))
    assert done is True and calls == []
    assert st.deltas.get("s1.abstain.ts_unresolved") == 1


def test_stale_block_ts_abstains_too_old(st, monkeypatch):
    # freshness is judged on BLOCK ts: push the frozen clock past the
    # emit-age ceiling and the same fixture fill refuses as too old
    monkeypatch.setattr(s1.time, "time",
                        lambda: TS0 + s1.EMIT_MAX_AGE_S + 20.0)
    lst = _Listener(roster={MAKER: {"id": 7, "username": "mk"}})
    _wire(st, lst)
    _arm(st)
    calls = _capture_ingest(monkeypatch)
    _observe_all(st, lst, [MAKER_EV])
    done = asyncio.run(st._finalize_tx(_Pool(), TX, st.pending[TX],
                                       time.time()))
    assert done is True and calls == []
    assert st.deltas.get("s1.abstain.too_old") == 1


# The fixture events carry the verbatim p382s block ts (TS0, 2024). The
# whole file runs on a clock frozen just past TS0 so those fills read
# as FRESH — freshness is judged on block ts, which this pins.
@pytest.fixture(autouse=True)
def _fresh_ts(monkeypatch):
    monkeypatch.setattr(s1.time, "time", lambda: TS0 + 10.0)
    yield


# ── observe-side rules ──────────────────────────────────────────────
def test_replay_never_buffers(st):
    lst = _Listener(roster={MAKER: {"id": 7, "username": "mk"}})
    lst._shadow_replay = True
    st.observe(lst, MAKER_EV)
    assert st.pending == {}
    assert st.deltas.get("s1.abstain.replay") == 1


def test_removed_log_purges_and_counts(st):
    lst = _Listener(roster={MAKER: {"id": 7, "username": "mk"}})
    st.observe(lst, MAKER_EV)
    assert TX in st.pending
    st.observe(lst, dict(MAKER_EV, removed=True))
    assert TX not in st.pending
    assert st.deltas.get("s1.abstain.reorged") == 1


def test_overflow_evicts_fifo_and_counts(st):
    lst = _Listener(roster={MAKER: {"id": 7, "username": "mk"}})
    for i in range(s1.PENDING_CAP + 3):
        st.observe(lst, dict(MAKER_EV, transactionHash="0x" + f"{i:064x}"))
    assert len(st.pending) == s1.PENDING_CAP
    assert st.deltas.get("s1.abstain.overflow") == 3


def test_self_trade_abstains(st, monkeypatch):
    lst = _Listener(roster={MAKER: {"id": 7, "username": "mk"}})
    _wire(st, lst)
    _arm(st)
    calls = _capture_ingest(monkeypatch)
    ev = _ev(MAKER, MAKER, 0, TOKEN_INT, 0x747548, 0xBBD5F0)
    _observe_all(st, lst, [ev])
    done = asyncio.run(st._finalize_tx(_Pool(), TX, st.pending[TX],
                                       time.time()))
    assert done is True and calls == []
    assert st.deltas.get("s1.abstain.self_trade") == 1


# ── certification gate ──────────────────────────────────────────────
def _green_doc(now):
    return {"window_start": now - s1.CERT_WINDOW_S - 10,
            "health_start": now - s1.CERT_HEALTH_S - 10,
            "decoder_fp": s1.DECODER_FP,
            "counters": {"sim_ven_suppressed": 600, "decoded_agg": 80},
            "at_window": {"sim_ven_suppressed": 0, "decoded_agg": 0}}


def test_cert_green_requires_all_criteria(st):
    now = time.time()
    doc = _green_doc(now)
    assert st._judge_cert(doc, now) == (True, "green")
    assert st._judge_cert(None, now)[1] == "no_shadow_state"
    young = dict(doc, window_start=now - 3600)
    assert st._judge_cert(young, now)[1] == "window_young"
    skew = dict(doc, decoder_fp="somethingelse")
    assert st._judge_cert(skew, now)[1] == "decoder_fp_mismatch"
    quiet = dict(doc, counters={"sim_ven_suppressed": 10, "decoded_agg": 80})
    assert st._judge_cert(quiet, now)[1] == "volume_floor_ven"
    sick = dict(doc, health_start=now - 60)
    assert st._judge_cert(sick, now)[1] == "health_young"


def test_window_reset_after_arming_auto_disarms(st):
    now = time.time()
    st.armed = True
    st.armed_at = now - 3600
    doc = _green_doc(now)
    doc["window_start"] = now - 60          # reset AFTER arming
    pool = _Pool(stored=json.dumps(doc))
    asyncio.run(st._check_cert(pool, now))
    assert st.armed is False
    assert st.deltas.get("s1.trip.window_reset") == 1


def test_arm_env_is_honoured_only_while_green(st, monkeypatch):
    now = time.time()
    monkeypatch.setenv("S1_ARM", "on")
    pool = _Pool(stored=json.dumps({"window_start": now - 60}))
    asyncio.run(st._check_cert(pool, now))
    assert st.armed is False, "RED state must refuse the arm request"
    pool2 = _Pool(stored=json.dumps(_green_doc(now)))
    asyncio.run(st._check_cert(pool2, now))
    assert st.armed is True


def test_tripped_state_refuses_arming(st, monkeypatch):
    now = time.time()
    monkeypatch.setenv("S1_ARM", "on")
    st.tripped = "key_selfcheck"
    pool = _Pool(stored=json.dumps(_green_doc(now)))
    asyncio.run(st._check_cert(pool, now))
    assert st.armed is False, "a sticky trip requires manual clearing"


# ── registry semantics ──────────────────────────────────────────────
def test_registry_cross_owner_claim_refused():
    assert cr.claim(TX, MAKER, "receipt") is True
    assert cr.claim(TX, MAKER, "emitter") is False
    assert cr.get(TX, MAKER)["owner"] == "receipt"
    cr.finish(TX, MAKER, "emitter", "ingested")   # wrong owner: no-op
    assert cr.get(TX, MAKER)["done"] is False
    cr.finish(TX, MAKER, "receipt", "refused")
    assert cr.get(TX, MAKER) == {"owner": "receipt", "done": True,
                                 "outcome": "refused"}


def test_registry_emitter_claim_survives_receipt_reclaim():
    assert cr.claim(TX, MAKER, "emitter") is True
    assert cr.claim(TX, MAKER, "receipt") is False
    assert cr.get(TX, MAKER)["owner"] == "emitter"


# ── flag-off inertness ──────────────────────────────────────────────
def test_disabled_emitter_buffers_nothing(monkeypatch):
    monkeypatch.setenv("S1_EMITTER", "off")
    e = S1Emitter()
    e.observe(_Listener(), MAKER_EV)
    assert e.pending == {} and e.deltas == {}


def test_emitter_never_imports_at_module_load_into_shadow(st):
    """C2: shadow_v2 must not know the emitter exists."""
    import sportsassets.ingestion.shadow_v2 as sv
    src = open(sv.__file__).read()
    assert "s1_emitter" not in src and "claim_registry" not in src
