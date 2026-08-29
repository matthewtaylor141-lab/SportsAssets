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
        "blockHash": "0x" + "77" * 32,
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
    def __init__(self, probe_rows=None, stored=None):
        self.probe_rows = probe_rows or []
        self.stored = stored
        self.writes = []          # flush doc writes (SQL_WRITE)
        self.trip_writes = []     # durable trip unions (SQL_TRIP)

    async def fetch(self, sql, *a, timeout=None):
        return self.probe_rows

    async def fetchval(self, sql, *a, timeout=None):
        if a and a[0] == "s1_arm_override":
            # the override switch has its own key; without dispatch the
            # shadow doc (any truthy JSON) would read as override-on in
            # every cert test
            return getattr(self, "override_stored", None)
        return self.stored

    async def execute(self, sql, *a, timeout=None):
        self.writes.append(a)

    async def fetchrow(self, sql, *a, timeout=None):
        if "prev.admit" in sql:               # SQL_TRIP (r29 shape)
            self.trip_writes.append(a)
            return {"had": False, "wrote": True}
        return None


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
    assert ev.source == "s1" and ev.whale_id == 7
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
    pool = _Pool(probe_rows=[{"source": "chain", "dedupe_key": "zzz"}])
    done = asyncio.run(st._finalize_tx(pool, TX,
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
    st._trip("key_selfcheck")
    pool = _Pool(stored=json.dumps(_green_doc(now)))
    asyncio.run(st._check_cert(pool, now))
    assert st.armed is False, "a sticky trip requires manual clearing"


# ── owner-override arm (owner order 2026-08-29, "get it live now") ──
class TestOwnerOverrideArm:
    """The override bypasses ONLY the certification clock. Sticky
    trips keep full authority: they refuse the arm, disarm a live
    one, and gate every emit."""

    def test_override_arms_through_a_young_window(self, st):
        now = time.time()
        pool = _Pool(stored=json.dumps({"window_start": now - 60}))
        pool.override_stored = json.dumps(True)
        asyncio.run(st._check_cert(pool, now))
        assert st.armed is True
        assert st.cert_green is False, \
            "the override arms; it must never fake the cert verdict"

    def test_override_never_crosses_a_sticky_trip(self, st):
        now = time.time()
        st._trip("key_selfcheck")
        pool = _Pool(stored=json.dumps(_green_doc(now)))
        pool.override_stored = json.dumps(True)
        asyncio.run(st._check_cert(pool, now))
        assert st.armed is False

    def test_trip_disarms_an_override_arm(self, st):
        now = time.time()
        pool = _Pool(stored=json.dumps({"window_start": now - 60}))
        pool.override_stored = json.dumps(True)
        asyncio.run(st._check_cert(pool, now))
        assert st.armed is True
        st._trip("key_divergent:9")
        assert st.armed is False

    def test_override_arm_survives_a_window_reset(self, st):
        # deploys reset the window several times a day — exactly what
        # the owner overrode; the reset ratchet must not disarm.
        now = time.time()
        pool = _Pool(stored=json.dumps({"window_start": now - 60}))
        pool.override_stored = json.dumps(True)
        asyncio.run(st._check_cert(pool, now))
        assert st.armed is True
        later = now + 120
        pool2 = _Pool(stored=json.dumps({"window_start": later - 1}))
        pool2.override_stored = json.dumps(True)
        asyncio.run(st._check_cert(pool2, later))
        assert st.armed is True

    def test_switch_off_returns_to_the_certified_regime(self, st):
        now = time.time()
        pool = _Pool(stored=json.dumps({"window_start": now - 60}))
        pool.override_stored = json.dumps(True)
        asyncio.run(st._check_cert(pool, now))
        assert st.armed is True
        pool2 = _Pool(stored=json.dumps({"window_start": now - 60}))
        pool2.override_stored = json.dumps(False)
        asyncio.run(st._check_cert(pool2, now + 60))
        assert st.armed is False, \
            "override off + window young = certified regime disarms"

    def test_override_emits_without_cert_green(self, st, monkeypatch):
        lst = _Listener(roster={MAKER: {"id": 7, "username": "mk"}})
        _wire(st, lst)
        st.armed = True
        st.armed_at = time.time()
        st.arm_override = True
        st.cert_green = False              # window young — override path
        calls = _capture_ingest(monkeypatch)
        _observe_all(st, lst, [MAKER_EV])
        asyncio.run(st._finalize_tx(_Pool(), TX, st.pending[TX],
                                    time.time()))
        assert len(calls) == 1, "armed via override must actually emit"
        assert st.deltas.get("s1.emitted", 0) == 1

    def test_tripped_override_stays_burn_in_at_the_emit_gate(
            self, st, monkeypatch):
        lst = _Listener(roster={MAKER: {"id": 7, "username": "mk"}})
        _wire(st, lst)
        st.armed = True                    # stale flag: trip landed
        st.arm_override = True             # after the arm decision
        st.trips["key_selfcheck"] = time.time()
        calls = _capture_ingest(monkeypatch)
        _observe_all(st, lst, [MAKER_EV])
        asyncio.run(st._finalize_tx(_Pool(), TX, st.pending[TX],
                                    time.time()))
        assert not calls, "a trip gates the emit even under override"

    def test_read_failure_keeps_the_last_known_override(self, st):
        now = time.time()
        pool = _Pool(stored=json.dumps({"window_start": now - 60}))
        pool.override_stored = json.dumps(True)
        asyncio.run(st._check_cert(pool, now))
        assert st.armed is True

        class _BoomPool(_Pool):
            async def fetchval(self, sql, *a, timeout=None):
                if a and a[0] == "s1_arm_override":
                    raise RuntimeError("db blip")
                return self.stored

        pool2 = _BoomPool(stored=json.dumps({"window_start": now - 60}))
        asyncio.run(st._check_cert(pool2, now + 60))
        assert st.arm_override is True and st.armed is True, \
            "a transient read failure is not evidence — no flap"


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
def test_default_is_burn_in_on_but_never_armed(monkeypatch):
    """Owner directive 2026-08-28: the emitter tests itself in
    production by default — and the default can never write: armed
    requires cert green + persisted arm + S1_ARM, none of which a
    fresh process has."""
    monkeypatch.delenv("S1_EMITTER", raising=False)
    monkeypatch.delenv("S1_ARM", raising=False)
    e = S1Emitter()
    assert e.enabled is True, "burn-in runs by default"
    assert e.armed is False and e.cert_green is False
    assert e.tripped is None


def test_disabled_emitter_buffers_nothing(monkeypatch):
    monkeypatch.setenv("S1_EMITTER", "off")
    e = S1Emitter()
    e.observe(_Listener(), MAKER_EV)
    assert e.pending == {} and e.deltas == {}


# ── fleet round 1 pins ──────────────────────────────────────────────
class _MarketPool:
    """Models the trades table with per-(tx, whale, asset) chain rows —
    what the asset-scoped probe actually queries. Rows are stored as
    (tx, whale_id, asset) -> (source, dedupe_key)."""

    def __init__(self):
        self.rows = {}

    async def fetch(self, sql, *a, timeout=None):
        hit = self.rows.get((str(a[0]).lower(), a[1], a[2]))
        return [{"source": hit[0], "dedupe_key": hit[1]}] if hit else []

    async def fetchval(self, sql, *a, timeout=None):
        return None

    async def execute(self, sql, *a, timeout=None):
        pass


OTHER = "0x" + "cc" * 20
TOKEN_B = int("bbbb" + "22" * 30, 16)


def test_multimarket_tx_emits_every_market(st, monkeypatch):
    """fleet r1 (major): the (tx,whale)-scoped probe self-collided
    after the first market's emit and forfeited the rest — the exact
    bundle class S1 exists for. The probe is asset-scoped now."""
    lst = _Listener(roster={TAKER: {"id": 9, "username": "tk"}})
    _wire(st, lst)
    _arm(st)
    pool = _MarketPool()
    calls = []

    async def fake_ingest(ev, notify=True):
        pool.rows[(ev.tx_hash.lower(), ev.whale_id, ev.asset)] = \
            ("s1", ev.dedupe_key)
        calls.append(ev)
        return (len(calls), True)

    import sportsassets.ingestion.pipeline as pipeline
    monkeypatch.setattr(pipeline, "ingest_trade_result", fake_ingest)
    # taker agg in market A + maker own event in market B, one tx
    ev_b = _ev(TAKER, OTHER, 0, TOKEN_B, 0x747548, 0xBBD5F0, log_index=3)
    _observe_all(st, lst, [MAKER_EV, TAKER_EV, ev_b])
    done = asyncio.run(st._finalize_tx(pool, TX, st.pending[TX],
                                       time.time()))
    assert done is True
    assert sorted(c.asset for c in calls) == \
        sorted([str(TOKEN_INT), str(TOKEN_B)]), \
        "every market of the bundle must emit, not just the first"
    assert st.deltas.get("s1.abstain.chain_row_preexists") is None


def test_emitter_rows_carry_source_s1_for_instrument_independence(st,
                                                                  monkeypatch):
    """fleet r1 (major): rows the emitter writes must be invisible to
    the shadow's evidence buckets ('chain'/'poll'), or a wrong emission
    silences the very orphan alarms that would catch it."""
    lst = _Listener(roster={MAKER: {"id": 7, "username": "mk"}})
    _wire(st, lst)
    _arm(st)
    calls = _capture_ingest(monkeypatch)
    _observe_all(st, lst, [MAKER_EV])
    asyncio.run(st._finalize_tx(_Pool(), TX, st.pending[TX], time.time()))
    assert calls and calls[0].source == "s1"


def test_emit_claims_before_probe_and_registry_shows_emitter(st,
                                                             monkeypatch):
    """fleet r1 (CRITICAL): the claim must be taken synchronously
    BEFORE the awaited probe, so the receipt path can never interleave
    a key-divergent ingest inside that window."""
    lst = _Listener(roster={MAKER: {"id": 7, "username": "mk"}})
    _wire(st, lst)
    _arm(st)
    seen_at_probe = {}

    class _ProbePool(_Pool):
        async def fetch(self, sql, *a, timeout=None):
            seen_at_probe["claim"] = cr.get(TX, MAKER)
            return []

    calls = _capture_ingest(monkeypatch)
    _observe_all(st, lst, [MAKER_EV])
    asyncio.run(st._finalize_tx(_ProbePool(), TX, st.pending[TX],
                                time.time()))
    assert len(calls) == 1
    assert seen_at_probe["claim"] is not None and \
        seen_at_probe["claim"]["owner"] == "emitter", \
        "the emitter claim must already be held while the probe awaits"
    assert cr.claim(TX, MAKER, "receipt") is False


def test_burnin_would_emit_is_idempotent_across_retries(st, monkeypatch):
    """fleet r1 (minor): the collision-wait retry re-ran whole groups
    and re-counted would_emit, overstating armed coverage."""
    lst = _Listener(roster={MAKER: {"id": 7, "username": "mk"}})
    _wire(st, lst)
    st.cert_green = True                     # burn-in: green, unarmed
    _capture_ingest(monkeypatch)
    _observe_all(st, lst, [MAKER_EV])
    e = st.pending[TX]
    asyncio.run(st._finalize_tx(_Pool(), TX, e, time.time()))
    asyncio.run(st._finalize_tx(_Pool(), TX, e, time.time()))
    assert st.deltas.get("s1.would_emit") == 1


def test_blockless_and_hashless_logs_never_buffer(st):
    """fleet r1 (major/minor): a log with no blockNumber crashed the
    finalize path and wedged the loop; a log with no blockHash blinded
    the reorg check. Neither may buffer."""
    lst = _Listener(roster={MAKER: {"id": 7, "username": "mk"}})
    _wire(st, lst)
    no_blk = {k: v for k, v in MAKER_EV.items() if k != "blockNumber"}
    st.observe(lst, no_blk)
    no_hash = {k: v for k, v in MAKER_EV.items() if k != "blockHash"}
    st.observe(lst, no_hash)
    assert st.pending == {}
    assert st.deltas.get("s1.abstain.no_block") == 2
    # and the finalize guard holds even if such an entry appeared
    st.pending[TX] = {"logs": [MAKER_EV], "first_seen": 0, "last_seen": 0,
                      "evicted": False, "blocks": {}, "ts": {},
                      "ts_started": None, "v3_wait_started": None}
    done = asyncio.run(st._finalize_tx(_Pool(), TX, st.pending[TX],
                                       time.time()))
    assert done is True
    assert st.deltas.get("s1.abstain.no_block") == 3


def test_persisted_arm_requires_standing_window_at_boot(st):
    """fleet r1/r2: a stale persisted armed=true must not emit under a
    window that reset while the process was down, and nothing arms
    before the state row is actually loaded."""
    now = time.time()
    st._state_loaded = False
    doc = {"counters": {}, "armed": True, "armed_at": now - 3600,
           "tripped": None}
    asyncio.run(st._load_state(_Pool(stored=json.dumps(doc)), now))
    assert st.armed is False, "adoption is provisional until cert"
    good = _green_doc(now)
    good["window_start"] = now - s1.CERT_WINDOW_S - 10   # ws <= armed_at
    asyncio.run(st._check_cert(_Pool(stored=json.dumps(good)), now))
    assert st.armed is True and st.armed_at == now - 3600

    st2 = S1Emitter()
    st2._state_loaded = False
    asyncio.run(st2._load_state(_Pool(stored=json.dumps(doc)), now))
    reset = _green_doc(now)
    reset["window_start"] = now - 60          # reset AFTER armed_at
    asyncio.run(st2._check_cert(_Pool(stored=json.dumps(reset)), now))
    assert st2.armed is False
    assert st2.deltas.get("s1.trip.window_reset") == 1


def test_boot_trip_is_loaded_before_anything_can_arm(st, monkeypatch):
    """fleet r2 (major x3): a restarted sticky-tripped emitter must not
    arm via S1_ARM in the window before its first flush — the trip is
    loaded by _load_state, and _check_cert refuses to certify while the
    state row is unloaded (fail-closed on load failure too)."""
    now = time.time()
    monkeypatch.setenv("S1_ARM", "on")
    st._state_loaded = False
    # cert BEFORE load: refuses outright
    asyncio.run(st._check_cert(_Pool(stored=json.dumps(_green_doc(now))),
                               now))
    assert st.armed is False and st.cert_reason == "state_unloaded"
    # load adopts the trip and disarms
    doc = {"counters": {}, "armed": True, "armed_at": now - 60,
           "tripped": "key_selfcheck"}
    asyncio.run(st._load_state(_Pool(stored=json.dumps(doc)), now))
    assert st._state_loaded and st.tripped == "key_selfcheck"
    assert st.armed is False
    # even with cert green + S1_ARM, the trip blocks arming
    asyncio.run(st._check_cert(_Pool(stored=json.dumps(_green_doc(now))),
                               now))
    assert st.armed is False


def test_cert_read_failure_never_consumes_the_arm(st):
    """fleet r2 (major): a transient shadow-state read failure must not
    destroy a pending or live arm, nor fabricate trip evidence."""
    now = time.time()

    class _FailPool(_Pool):
        async def fetchval(self, sql, *a, timeout=None):
            raise RuntimeError("db blip")

    st._pending_arm_at = now - 3600
    asyncio.run(st._check_cert(_FailPool(), now))
    assert st.cert_reason == "cert_read_failed"
    assert st._pending_arm_at == now - 3600, "pending arm survives"
    assert st.deltas.get("s1.trip.window_reset") is None
    _arm(st)
    asyncio.run(st._check_cert(_FailPool(), now))
    assert st.armed is True, "a read blip must not disarm"
    assert st.cert_green is False, "but emission stays fail-closed"


def test_flush_write_never_carries_trip_state(st):
    """fleet r6 (major x3): the round-2 CAS guarded only the OLDEST
    reason, so full-document flushes silently erased concurrent trips
    and operator clears. The flush payload now structurally cannot
    name trip state — trips are maintained solely by the atomic
    SQL_TRIP / SQL_CLEAR server-side operations — and counters ship
    as DELTAS the server adds under the row lock."""
    now = time.time()
    st.trips = {"key_selfcheck": now - 100}
    st.deltas = {"s1.emitted": 3}
    pool = _Pool(stored=json.dumps(
        {"counters": {"s1.emitted": 40}, "trips": {}}))
    asyncio.run(st._flush(pool, now))
    written = json.loads(pool.writes[-1][1])
    for forbidden in ("trips", "tripped", "trips_cleared",
                      "trip_cleared_at", "trip_cleared_reason"):
        assert forbidden not in written, forbidden
    assert written["counters"] == {"s1.emitted": 3}, \
        "deltas, not absolutes — the server composes concurrent flushes"
    assert st.counters.get("s1.emitted") == 43, "local mirror = stored+snap"
    # and the SQL itself: no scalar CAS, server-side counter addition,
    # armed forced false whenever the stored trips are non-empty
    assert "->>'tripped'" not in s1.SQL_WRITE
    assert "jsonb_each" in s1.SQL_WRITE
    assert "'{armed}', 'false'" in s1.SQL_WRITE


def test_trip_is_durable_the_moment_it_fires(st):
    """fleet r6: SQL_TRIP is one atomic union — existing timestamp
    wins, armed is forced false in the same statement, and the WHERE
    refuses a stale re-persist of a reason whose tombstone is newer
    (the resurrection class)."""
    pool = _Pool()
    st._trip("key_selfcheck")
    ok = asyncio.run(st._persist_trip(pool, "key_selfcheck"))
    assert ok == "landed" and len(pool.trip_writes) == 1
    key, reason, at = pool.trip_writes[0]
    assert key == s1.STATE_KEY and reason == "key_selfcheck"
    assert at == st.trips["key_selfcheck"]
    assert "'armed', false" in s1.SQL_TRIP
    assert "trips_cleared" in s1.SQL_TRIP, "tombstone-guarded"

    class _DownPool(_Pool):
        async def fetchrow(self, sql, *a, timeout=None):
            raise RuntimeError("db down")

    st2 = S1Emitter()
    st2._trip("key_selfcheck")
    ok = asyncio.run(st2._persist_trip(_DownPool(), "key_selfcheck"))
    assert ok == "error" and "key_selfcheck" in st2._unpersisted, \
        "an unpersisted trip is retried at every flush until durable"

    class _RefusePool(_Pool):
        async def fetchrow(self, sql, *a, timeout=None):
            # the tombstone admit won: the row answered, wrote nothing
            return {"had": False, "wrote": False}

    st3 = S1Emitter()
    st3._trip("key_selfcheck")
    ok = asyncio.run(st3._persist_trip(_RefusePool(), "key_selfcheck"))
    assert ok == "refused", \
        "round 10: a 0-row tombstone refusal is a DECISION, never " \
        "reported as landed"
    assert "key_selfcheck" not in st3._unpersisted, \
        "a refusal is not a transient fault — no retry queue"


def test_foreign_trip_is_adopted_never_clobbered(st):
    """fleet r1/r2/r6: another process's sticky trip must disarm this
    one at the next flush read; the write carries no trip fields, and
    armed never persists beside a trip."""
    now = time.time()
    st.armed = True
    pool = _Pool(stored=json.dumps({"counters": {},
                                    "tripped": "key_selfcheck"}))
    asyncio.run(st._flush(pool, now))
    assert st.tripped == "key_selfcheck" and st.armed is False
    written = json.loads(pool.writes[-1][1])
    assert "tripped" not in written and "trips" not in written
    assert written["armed"] is False, "armed never persists beside a trip"


def test_emitter_never_imports_at_module_load_into_shadow(st):
    """C2: shadow_v2 must not know the emitter exists."""
    import sportsassets.ingestion.shadow_v2 as sv
    src = open(sv.__file__).read()
    assert "s1_emitter" not in src and "claim_registry" not in src


# ── fleet round 3: the row-anchored corroboration sweep ─────────────
class _SweepPool(_Pool):
    """Two-phase sweep fixture (round 6): the wallets query, then a
    per-wallet rows window. sweep_rows may be a flat list (one wallet,
    id 1, address '0xw') or a dict whale_id -> rows; recon_ran may be
    a bool or a dict address -> bool."""

    def __init__(self, sweep_rows, recon_ran=True, wallets=None):
        super().__init__()
        self.sweep_rows = sweep_rows
        self.recon_ran = recon_ran
        if wallets is None:
            has = sweep_rows if isinstance(sweep_rows, dict) else \
                {1: sweep_rows}
            wallets = [{"whale_id": wid, "address": "0xw%s" % wid
                        if isinstance(sweep_rows, dict) else "0xw"}
                       for wid, rr in has.items() if rr]
        self.wallets = wallets
        self.marked = []

    async def fetch(self, sql, *a, timeout=None):
        if "s1_suspect_at = now()" in sql:         # r42 suspect stamp
            self.suspected = getattr(self, "suspected", [])
            self.suspected.append(list(a[0]))
            return [{"id": i} for i in a[0]]
        if sql.lstrip().startswith("UPDATE trades"):
            # SQL_MARK is a RETURNING transition (r8): this fixture
            # grants every stamp; _PartialMarkPool models losing some
            self.marked.append(list(a[0]))
            return [{"id": i} for i in a[0]]
        if "GROUP BY w.id" in sql:
            return self.wallets
        if isinstance(self.sweep_rows, dict):
            return self.sweep_rows.get(a[1], [])
        return self.sweep_rows

    async def fetchval(self, sql, *a, timeout=None):
        if a and a[0] == "s1_arm_override":        # own key, own slot
            return getattr(self, "override_stored", None)
        if "extract(epoch from now())" in sql:
            # r12: the sweep reads SQL_NOW for the clock offset — the
            # fixture leaves it unlearned (None) so stamps stay pinned
            # to the frozen app clock unless a test overrides this
            return None
        if "count(DISTINCT t.whale_id)" in sql:      # r42 burst census
            return getattr(self, "suspect_wallets", 0)
        if "AS live" in sql:                       # r42 index-live
            return getattr(self, "index_live", True)
        return self.stored

    async def fetchrow(self, sql, *a, timeout=None):
        if "prev.admit" in sql:
            # SQL_TRIP (r29 shape): a fresh transition that lands
            self.trip_writes.append(a)
            return {"had": False, "wrote": True}
        if "lower(v.tx_hash)" in sql:              # r42 key twin
            return getattr(self, "key_twin", None)
        if "FROM trades WHERE id" in sql:
            # SQL_RECHECK (r15): the verdict's last look at the row —
            # False by default so judgment paths behave as before
            return {"ok": getattr(self, "recheck_ok", False)}
        ran = (self.recon_ran.get(a[1], True)
               if isinstance(self.recon_ran, dict) else self.recon_ran)
        # r20: coverage is a COUNT — two runs cover, fewer defer
        return {"n": 2} if ran else {"n": 0}

    async def execute(self, sql, *a, timeout=None):
        if sql.lstrip().startswith("UPDATE trades"):
            self.marked.append(a[0])
        else:
            self.writes.append(a)


def _srow(i, ok, suspect_at=float(TS0) - 4 * 3600):
    # suspect_at defaults PRE-HELD (r42): the legacy pins assert the
    # verdict machinery past the hold; the lifecycle pins pass None
    return {"id": i, "dedupe_key": f"k{i}", "detected_at": 0, "ts": 0,
            "s1_suspect_at": suspect_at, "ok": ok}


def test_sweep_confirms_and_trips_row_specific(st):
    """fleet r4/r5 re-pin: stamped -> confirmed; unstamped with a
    fill-covering backstop run -> STICKY TRIP under a ROW-SPECIFIC
    reason, made durable via SQL_TRIP BEFORE the row is stamped.
    Unjudgeable (departed-whale) rows never enter the window — the SQL
    filters them — and the backlog gauge counts everything unjudged."""
    _arm(st)
    pool = _SweepPool([_srow(1, True), _srow(3, False)])
    pool.stored = 5                        # SQL_BACKLOG count
    asyncio.run(st._corroboration_sweep(pool))
    assert st.deltas.get("s1.confirmed") == 1
    assert st.deltas.get("s1.uncorroborated") == 1
    assert st.trips and "uncorroborated:3" in st.trips
    assert st.armed is False
    # r16: confirmed and judged stamp through separate statements
    assert pool.marked == [[1], [3]]
    assert [w[1] for w in pool.trip_writes] == ["uncorroborated:3"], \
        "the trip is durable via its own atomic write, not the flush"
    assert st.unjudged_backlog == 5


def test_sweep_defers_when_the_backstop_never_ran(st):
    """fleet r4/r5 (major): no completed, FILL-COVERING reconciler
    run since detection = defer, never trip."""
    _arm(st)
    pool = _SweepPool([_srow(3, False)], recon_ran=False)
    asyncio.run(st._corroboration_sweep(pool))
    assert st.deltas.get("s1.uncorroborated") is None
    assert st.trips == {} and st.armed is True
    assert pool.marked == []


def test_recon_coverage_requires_depth_and_lag(st):
    """fleet r6 (major x2): the covering run must have provably had a
    chance at THE FILL — started after the venue's indexing lag, and
    with a /trades window that either exhausted the feed or reached at
    or below the fill's own timestamp. Completion alone proves neither
    (the depth-500 truncation and the t0+30s run were both counted as
    coverage and falsely tripped correct emissions)."""
    q = s1.SQL_RECON_SINCE
    assert "$1::timestamptz + make_interval(secs => $4)" in q, \
        "round 7 CRITICAL: untyped $1 resolved as interval at prepare " \
        "and the statement could never execute"
    assert "'cov:' || $2" in q
    assert "'oldest'" in q and "jsonb_typeof" in q
    assert "'complete'" not in q, \
        "round 8: complete=true from a truncated feed waived the " \
        "fill-span check and false-tripped a correct emission — " \
        "coverage always requires the walk to reach the fill's time"
    # and the reconciler records that evidence
    import inspect
    from sportsassets.ingestion import reconciler as rec
    src = inspect.getsource(rec)
    assert '"cov:" + whale["address"]' in src
    assert '"complete": complete, "oldest": oldest_ts' in src
    assert src.count("complete = True") == 2, \
        "complete only when the feed was exhausted inside the depth " \
        "(short/empty page, or r17's only-the-witness-came-back page)"
    assert "complete = offset > 0" in src, \
        "round 7: an empty FIRST page is venue degradation, not " \
        "'feed exhausted' — it must never brand a fill uncorroborated"


def test_one_deferring_wallet_cannot_starve_another(st):
    """fleet r6 (major): rows deferring at the coverage check re-
    entered a single global LIMIT window at the front and could pin it
    forever — a genuinely wrong emission behind them was never judged.
    Per-wallet windows: X defers only X."""
    _arm(st)
    pool = _SweepPool(
        {1: [_srow(1, False)], 2: [_srow(9, False)]},
        recon_ran={"0xw1": False, "0xw2": True},
        wallets=[{"whale_id": 1, "address": "0xw1"},
                 {"whale_id": 2, "address": "0xw2"}])
    asyncio.run(st._corroboration_sweep(pool))
    assert "uncorroborated:9" in st.trips, \
        "wallet 2's verdict lands despite wallet 1 deferring"
    assert "uncorroborated:1" not in st.trips
    assert pool.marked == [[9]]


def test_sweep_stamp_failure_counts_nothing(st):
    """fleet r4 (minor): count-before-stamp inflated s1.confirmed +50
    per minute forever under a write-degraded DB."""
    _arm(st)

    class _NoMarkPool(_SweepPool):
        async def fetch(self, sql, *a, timeout=None):
            if sql.lstrip().startswith("UPDATE trades"):
                raise RuntimeError("read-only failover")
            return await _SweepPool.fetch(self, sql, *a, timeout=timeout)

    pool = _NoMarkPool([_srow(1, True)])
    asyncio.run(st._corroboration_sweep(pool))
    assert st.deltas.get("s1.confirmed") is None, \
        "an unstamped judgment is not evidence"


def test_only_the_stamp_winner_counts(st):
    """fleet r8 (minor x2): two overlapping sweeps both counted the
    same judgment, and the server-side delta merge made the inflation
    durable. Counting now follows the RETURNING set — the rows THIS
    process actually transitioned."""
    _arm(st)

    class _PartialMarkPool(_SweepPool):
        async def fetch(self, sql, *a, timeout=None):
            if sql.lstrip().startswith("UPDATE trades"):
                self.marked.append(list(a[0]))
                # the other process already stamped row 1
                return [{"id": i} for i in a[0] if i != 1]
            return await _SweepPool.fetch(self, sql, *a, timeout=timeout)

    pool = _PartialMarkPool([_srow(1, True), _srow(3, False)])
    asyncio.run(st._corroboration_sweep(pool))
    assert st.deltas.get("s1.confirmed") is None, \
        "a stamp another process won is not our count"
    assert st.deltas.get("s1.uncorroborated") == 1


def test_trip_persist_failure_defers_the_stamp(st):
    """fleet r6: the stamp makes the verdict permanent, so it must
    never land before the trip is durable — a crash between the two
    silenced the alarm forever. Persist fails -> row stays unstamped
    and re-judges next sweep; the in-memory trip still disarms."""
    _arm(st)

    class _TripDownPool(_SweepPool):
        async def fetchrow(self, sql, *a, timeout=None):
            if "prev.admit" in sql:
                raise RuntimeError("db blip")
            return await _SweepPool.fetchrow(self, sql, *a,
                                             timeout=timeout)

    pool = _TripDownPool([_srow(3, False)])
    asyncio.run(st._corroboration_sweep(pool))
    assert pool.marked == [], "no durable trip, no stamp"
    assert st.deltas.get("s1.uncorroborated") is None
    assert "uncorroborated:3" in st.trips and st.armed is False
    assert "uncorroborated:3" in st._unpersisted


def test_operator_clear_releases_exactly_one_reason(st):
    """fleet r4/r5 (major): a clear names the reason it releases — its
    PER-REASON tombstone frees exactly that verdict in every process
    and can never absolve a sibling row's trip or another class."""
    now = time.time()
    st.trips = {"uncorroborated:11": now - 600,
                "key_selfcheck": now - 300}
    pool = _Pool(stored=json.dumps({
        "counters": {}, "trips": {"key_selfcheck": now - 300},
        "trips_cleared": {"uncorroborated:11": now - 60}}))
    asyncio.run(st._flush(pool, now))
    assert "uncorroborated:11" not in st.trips, "the named clear stands"
    assert "key_selfcheck" in st.trips, \
        "an unrelated trip survives the clear"
    assert st.armed is False


def test_second_clear_never_resurrects_the_first(st):
    """fleet r6 (major): the single trip_cleared_* slot forgot every
    clear but the last, so a process that missed one flush cycle
    unioned an already-cleared trip back and the fleet re-disarmed on
    a verdict the operator had released. The tombstone DICT remembers
    every clear; only a genuinely NEW trip (newer than its tombstone)
    survives."""
    now = time.time()
    st.trips = {"uncorroborated:100": now - 900,   # cleared at now-500
                "uncorroborated:101": now - 800,   # cleared at now-400
                "uncorroborated:102": now - 100}   # NEW: post-clear
    pool = _Pool(stored=json.dumps({
        "counters": {}, "trips": {},
        "trips_cleared": {"uncorroborated:100": now - 500,
                          "uncorroborated:101": now - 400,
                          "uncorroborated:102": now - 300}}))
    asyncio.run(st._flush(pool, now))
    assert set(st.trips) == {"uncorroborated:102"}, \
        "every recorded clear holds; a post-clear re-trip stands"


def test_concurrent_trips_merge_never_overwrite(st):
    """fleet r5 (major): two processes' different trips union in
    memory instead of last-writer-wins; disk union is SQL_TRIP's job
    (the flush write carries no trip state at all, r6)."""
    now = time.time()
    st.trips = {"key_selfcheck": now - 100}
    pool = _Pool(stored=json.dumps({
        "counters": {},
        "trips": {"uncorroborated:7": now - 200}}))
    asyncio.run(st._flush(pool, now))
    assert set(st.trips) == {"key_selfcheck", "uncorroborated:7"}
    written = json.loads(pool.writes[-1][1])
    assert "trips" not in written


def test_duplicate_ws_delivery_never_doubles_the_group(st):
    """fleet r5 (major): one duplicated live delivery of a leg made
    classify abstain the whole certified emission."""
    lst = _Listener(roster={TAKER: {"id": 9, "username": "tk"}})
    _wire(st, lst)
    st.observe(lst, MAKER_EV)
    st.observe(lst, TAKER_EV)
    st.observe(lst, dict(TAKER_EV))          # duplicate delivery
    assert len(st.pending[TX]["logs"]) == 2
    assert st.deltas.get("s1.dup_event") == 1


def test_probe_error_mid_group_retries_not_forfeits(st, monkeypatch):
    """fleet r5 (major): a transient DB error mid-group must retry the
    tx, not permanently forfeit the bundle's remaining markets."""
    lst = _Listener(roster={MAKER: {"id": 7, "username": "mk"}})
    _wire(st, lst)
    _arm(st)
    _capture_ingest(monkeypatch)

    class _FlakyPool(_Pool):
        async def fetch(self, sql, *a, timeout=None):
            raise RuntimeError("db blip")

    _observe_all(st, lst, [MAKER_EV])
    done = asyncio.run(st._finalize_tx(_FlakyPool(), TX, st.pending[TX],
                                       time.time()))
    assert done is False, "the tx stays pending for the next tick"


def test_sweep_empty_is_free(st):
    pool = _SweepPool([])
    asyncio.run(st._corroboration_sweep(pool))
    assert st.deltas == {} and pool.marked == []


def test_flush_never_writes_before_state_loads(st):
    """fleet r3 (major): writing armed=false while the persisted state
    is unknown erased legitimate arms across boot blips."""
    st._state_loaded = False
    st.deltas = {"s1.emitted": 2}
    pool = _Pool(stored=json.dumps({"armed": True, "counters": {}}))
    asyncio.run(st._flush(pool, time.time()))
    assert pool.writes == [], "no write before the loader succeeds"
    assert st.deltas.get("s1.emitted") == 2


def test_pending_arm_survives_the_flush_write(st):
    """A not-yet-validated arm is still an arm on disk."""
    now = time.time()
    st._pending_arm_at = now - 3600
    asyncio.run(st._flush(_Pool(stored=json.dumps({"counters": {}})), now))
    # fixture note: _state_loaded is True here, so the write happens


def test_ambiguous_write_drops_snap(st):
    """fleet r3 (minor): an exception mid-write may have committed —
    restoring would double-count, so the snap drops (undercount-only).
    With deltas added server-side there is no CAS-refusal path left to
    restore from (r6)."""
    now = time.time()

    class _BoomPool(_Pool):
        async def execute(self, sql, *a, timeout=None):
            raise RuntimeError("socket died mid-write")

    st.deltas = {"s1.emitted": 5}
    asyncio.run(st._flush(_BoomPool(stored=json.dumps({"counters": {}})),
                          now))
    assert st.deltas.get("s1.emitted") is None, "ambiguous -> dropped"
    assert st.deltas.get("s1.snap_dropped_ambiguous") == 1


def test_boolean_trip_in_state_row_does_not_starve_the_cas(st):
    """fleet r3 (minor): ->> renders a JSON boolean as 'true'; the CAS
    comparison must reproduce that, not str(True)."""
    assert S1Emitter._trip_str(True) == "true"
    assert S1Emitter._trip_str("key_selfcheck") == "key_selfcheck"
    assert S1Emitter._trip_str(None) == ""


def test_corrupt_state_row_fails_closed_not_wedged(st):
    """fleet r3 (minor): a non-numeric armed_at must not crash the run
    loop forever — the loader fails closed with a visible counter."""
    st._state_loaded = False
    doc = {"armed": True, "armed_at": {"bad": "shape"}, "counters": []}
    asyncio.run(st._load_state(_Pool(stored=json.dumps(doc)),
                               time.time()))
    assert st._state_loaded is True
    assert st.armed is False


# ── fleet round 6: post-finalize re-entry, RPC economics ────────────
def test_preexisting_s1_row_from_another_entry_forbids_emission(st,
                                                                monkeypatch):
    """fleet r6 (CRITICAL x2): once a tx finalizes and pops, a deep-
    reorg re-add (new block ts = new key) or a straggler leg of the
    same tx forms a FRESH entry with no memory, and the old different-
    key-means-sibling rule waved the double emission through. An s1
    row this entry did not write forbids the view; the poller carries
    anything real."""
    lst = _Listener(roster={MAKER: {"id": 7, "username": "mk"}})
    _wire(st, lst)
    _arm(st)
    calls = _capture_ingest(monkeypatch)
    _observe_all(st, lst, [MAKER_EV])
    pool = _Pool(probe_rows=[{"source": "s1",
                              "dedupe_key": "reorg-shifted-key"}])
    done = asyncio.run(st._finalize_tx(pool, TX, st.pending[TX],
                                       time.time()))
    assert done is True and calls == []
    assert st.deltas.get("s1.abstain.s1_row_preexists") == 1


def test_own_entry_sibling_rows_still_proceed(st, monkeypatch):
    """The r2 sibling case survives the r6 rule: a row THIS entry
    wrote (a bundle's earlier market, or an earlier retry pass) is in
    e['ingested_keys'] and never blocks the remaining records."""
    lst = _Listener(roster={MAKER: {"id": 7, "username": "mk"}})
    _wire(st, lst)
    _arm(st)
    calls = _capture_ingest(monkeypatch)
    _observe_all(st, lst, [MAKER_EV])
    e = st.pending[TX]
    e["ingested_keys"] = {"our-own-earlier-sibling-key"}
    pool = _Pool(probe_rows=[{"source": "s1",
                              "dedupe_key": "our-own-earlier-sibling-key"}])
    done = asyncio.run(st._finalize_tx(pool, TX, e, time.time()))
    assert done is True and len(calls) == 1
    assert st.deltas.get("s1.abstain.s1_row_preexists") is None


def test_burnin_count_survives_the_entry_pop(st, monkeypatch):
    """fleet r6 (minor): the per-entry counted set died at pop, so a
    post-finalize redelivery re-counted would_emit and the gauge the
    flip decision reads diverged from what armed would do. The mark
    now lives in a process-level LRU keyed (tx, wallet, asset)."""
    lst = _Listener(roster={MAKER: {"id": 7, "username": "mk"}})
    _wire(st, lst)
    st.cert_green = True                     # burn-in
    _capture_ingest(monkeypatch)
    _observe_all(st, lst, [MAKER_EV])
    asyncio.run(st._finalize_tx(_Pool(), TX, st.pending[TX], time.time()))
    assert st.deltas.get("s1.would_emit") == 1
    st.pending.pop(TX)                       # the run loop pops on done
    _observe_all(st, lst, [MAKER_EV])        # post-finalize redelivery
    asyncio.run(st._finalize_tx(_Pool(), TX, st.pending[TX], time.time()))
    assert st.deltas.get("s1.would_emit") == 1, \
        "one fill, one count — across entries, not per entry"


def test_foreign_tx_costs_zero_rpc(st):
    """fleet r6 (major): the buffer is overwhelmingly foreign txs (the
    WS subscribes by exchange address); resolving their timestamps
    before the decode discarded them starved the budget 30-to-1. The
    decode is pure and free — a foreign tx must never spend a token."""
    lst = _Listener(roster={})               # nobody we track
    _wire(st, lst)
    _observe_all(st, lst, [MAKER_EV])
    done = asyncio.run(st._finalize_tx(_Pool(), TX, st.pending[TX],
                                       time.time()))
    assert done is True
    assert st.deltas.get("s1.rpc_calls") is None, "zero RPC spent"
    assert st._client.calls == []


def test_block_ts_cache_one_rpc_per_block(st, monkeypatch):
    """fleet r6 (major): the block timestamp is per-BLOCK, not per-tx —
    two roster txs in one block must cost one resolution. The cache
    key includes the blockHash so a reorged sibling can never borrow
    a stale timestamp."""
    lst = _Listener(roster={MAKER: {"id": 7, "username": "mk"}})
    _wire(st, lst)
    st.cert_green = True                     # burn-in: no pool traffic
    _capture_ingest(monkeypatch)
    tx2 = "0x" + "f2" * 32
    _observe_all(st, lst, [MAKER_EV, dict(MAKER_EV, transactionHash=tx2)])
    asyncio.run(st._finalize_tx(_Pool(), TX, st.pending[TX], time.time()))
    asyncio.run(st._finalize_tx(_Pool(), tx2, st.pending[tx2], time.time()))
    assert st.deltas.get("s1.rpc_calls") == 1, "second tx hits the cache"
    assert st._ts_cache.get((BLK, "0x" + "77" * 32)) == TS0


def test_head_poll_fires_only_when_the_ws_goes_quiet(st):
    """fleet r6 (major): under live traffic there is always a tx
    younger than CONFIRM_DEPTH, so the un-gated poll burned a token
    every tick (60/min demand vs 30/min refill) with priority over ts
    resolution. observe() advances the head from every WS log; the
    poll exists for when that feed goes QUIET."""
    now = time.time()
    st.pending[TX] = {"blocks": {BLK: "0x77"}, "logs": []}
    st.head = BLK                            # waiting on confirmation
    st.head_advanced_at = now - 1.0
    assert st._should_poll_head(now) is False, "the WS is feeding us"
    st.head_advanced_at = now - s1.HEAD_QUIET_S - 1
    st.last_head_poll_at = now - 1.0
    assert st._should_poll_head(now) is False, "polls are spaced"
    st.last_head_poll_at = now - s1.HEAD_POLL_MIN_S - 1
    assert st._should_poll_head(now) is True
    st.head = BLK + s1.CONFIRM_DEPTH + 1
    assert st._should_poll_head(now) is False, "nobody is waiting"


# ── fleet round 7 pins ──────────────────────────────────────────────
def test_legacy_scalar_folds_beside_the_dict(st):
    """fleet r7 (major): the first round-6 trip created the trips dict
    and the dict-is-authoritative rule silently shadowed an uncleared
    pre-round-6 scalar trip out of existence — the fleet re-armed past
    it. The scalar now folds IN beside the dict and is queued for
    durable migration."""
    now = time.time()
    st._state_loaded = False
    doc = {"counters": {}, "tripped": "key_selfcheck",
           "trips": {"uncorroborated:55": now - 100}}
    asyncio.run(st._load_state(_Pool(stored=json.dumps(doc)), now))
    assert set(st.trips) == {"key_selfcheck", "uncorroborated:55"}
    assert st.armed is False
    assert "key_selfcheck" in st._unpersisted, \
        "the scalar migrates into the dict via the next persist"
    # a tombstoned scalar stays released
    st2 = S1Emitter()
    st2._state_loaded = False
    doc2 = {"tripped": "key_selfcheck", "trips": {},
            "trips_cleared": {"key_selfcheck": now}}
    asyncio.run(st2._load_state(_Pool(stored=json.dumps(doc2)), now))
    assert st2.trips == {}


def test_flush_folds_the_scalar_beside_the_dict(st):
    now = time.time()
    pool = _Pool(stored=json.dumps({
        "counters": {}, "tripped": "key_selfcheck",
        "trips": {"uncorroborated:7": now - 200}}))
    asyncio.run(st._flush(pool, now))
    assert set(st.trips) == {"key_selfcheck", "uncorroborated:7"}


def test_clear_strips_the_scalar_only_for_its_own_reason():
    """fleet r7 (major): SQL_CLEAR's unconditional #- '{tripped}' let a
    clear for reason A destroy uncleared reason B's only durable
    record. The strip is now scoped to equality; behavior itself is
    pinned end-to-end in test_s1_sql_real_pg."""
    assert "WHEN x.v->>'tripped' = $2::text" in s1.SQL_CLEAR
    assert "jsonb_typeof(value) = 'object'" in s1.SQL_CLEAR


def test_armed_path_refuses_a_borrowed_timestamp(st, monkeypatch):
    """fleet r7 (major): a cached (block, hash) timestamp skipped the
    live reorg check, so a sibling tx after a deep reorg could emit a
    fill from an orphaned block. Burn-in may borrow (gauge economics);
    an ARMED emission re-earns its check or abstains."""
    lst = _Listener(roster={MAKER: {"id": 7, "username": "mk"}})
    _wire(st, lst)
    _arm(st)
    calls = _capture_ingest(monkeypatch)
    st._ts_cache[(BLK, "0x" + "77" * 32)] = TS0
    _observe_all(st, lst, [MAKER_EV])
    done = asyncio.run(st._finalize_tx(_Pool(), TX, st.pending[TX],
                                       time.time()))
    assert done is True and calls == []
    assert st.deltas.get("s1.abstain.ts_cached") == 1
    assert st.deltas.get("s1.rpc_calls") is None, "the cache was used"
    # burn-in on identical traffic still counts the fill
    st2 = S1Emitter()
    st2.head = BLK + s1.CONFIRM_DEPTH + 1
    st2.http_url = "http://rpc.test"
    st2._state_loaded = True
    st2.cert_green = True
    _wire(st2, lst)
    st2._ts_cache[(BLK, "0x" + "77" * 32)] = TS0
    _observe_all(st2, lst, [MAKER_EV])
    asyncio.run(st2._finalize_tx(_Pool(), TX, st2.pending[TX],
                                 time.time()))
    assert st2.deltas.get("s1.would_emit") == 1


def test_removed_notice_purges_every_earned_timestamp(st):
    """fleet r7: a reorg at block B rewrites the whole suffix — every
    cached timestamp at or above B is dropped the moment any reorg
    signal arrives. r8: INCLUDING copies already resolved into pending
    entries, or a retry pass would emit the orphaned block without
    ever re-verifying it. r31 (major): evidence AT height B only
    proves the fork is AT OR BELOW B — the lower-height survivors
    this pin used to assert were exactly the armed orphaned-chain
    emission the fleet executed, so the purge now voids EVERYTHING
    and re-resolution re-earns each height against its recorded hash
    (the strict resolver refuses what the live chain rewrote)."""
    lst = _Listener(roster={})
    h = "0xabc"
    st._ts_cache[(99, h)] = 1
    st._ts_cache[(100, h)] = 2
    st._ts_cache[(101, h)] = 3
    other_tx = "0x" + "ee" * 32
    st.pending[other_tx] = {"logs": [], "blocks": {101: h},
                            "ts": {99: 1, 101: 3}, "evicted": False}
    st.observe(lst, dict(MAKER_EV, removed=True,
                         blockNumber=hex(100)))
    assert st._ts_cache == {}, \
        "r31: nothing below the evidence height is provably safe"
    assert st.pending[other_tx]["ts"] == {}, \
        "the entry-local copies are purged with the shared cache"
    assert st.pending[other_tx].get("reorg_gen", 0) >= 1


def test_removed_entry_mid_finalize_never_emits(st, monkeypatch):
    """fleet r8 (major): a WS reorg burst on the same event loop can
    pop the pending entry while _finalize_tx awaits the probe — the
    fill was counted abstain.reorged yet emitted anyway. Membership is
    re-checked after every awaited section."""
    lst = _Listener(roster={MAKER: {"id": 7, "username": "mk"}})
    _wire(st, lst)
    _arm(st)
    calls = _capture_ingest(monkeypatch)

    class _ReorgPool(_Pool):
        async def fetch(self, sql, *a, timeout=None):
            # the reorg burst lands during the probe await
            st.pending.pop(TX, None)
            return []

    _observe_all(st, lst, [MAKER_EV])
    done = asyncio.run(st._finalize_tx(_ReorgPool(), TX, st.pending[TX],
                                       time.time()))
    assert done is True and calls == [], \
        "an entry popped mid-finalize must never reach ingest"
    assert st.deltas.get("s1.abstain.reorged") == 1
    assert st.deltas.get("s1.emitted") is None


def test_sweep_windows_rotate_instead_of_starving():
    """fleet r7 (major x2): oldest-first ordering let 20 deferring
    wallets own the wallet list, and a wallet's own 10 oldest
    deferring rows own its window, forever. Both windows now order by
    a per-sweep salted hash — deferral defers, it cannot block."""
    assert "ORDER BY md5(w.address || $2::text)" in s1.SQL_SWEEP_WALLETS
    assert "ORDER BY md5(t.id::text || $3::text)" in s1.SQL_SWEEP
    assert "ORDER BY oldest" not in s1.SQL_SWEEP_WALLETS
    assert "ORDER BY t.detected_at" not in s1.SQL_SWEEP


# ── fleet round 10 pins ─────────────────────────────────────────────
def test_sweep_never_stamps_on_a_refused_persist(st):
    """fleet r10 (major): SQL_TRIP's tombstone refusal came back as
    'landed' and the sweep stamped a re-judged row permanently with no
    durable trip anywhere. A refusal at a judgment site means fresh
    evidence vs an old tombstone: refresh past it and re-persist; only
    a LANDED trip stamps."""
    _arm(st)

    class _AlwaysRefusedPool(_SweepPool):
        async def fetchrow(self, sql, *a, timeout=None):
            if "prev.admit" in sql:
                self.trip_writes.append(a)
                return {"had": False, "wrote": False}
            return await _SweepPool.fetchrow(self, sql, *a,
                                             timeout=timeout)

    st.trips["uncorroborated:3"] = float(TS0)   # judged earlier at T1
    pool = _AlwaysRefusedPool([_srow(3, False)])
    asyncio.run(st._corroboration_sweep(pool))
    assert pool.marked == [], "no landed trip, no stamp — ever"
    assert st.deltas.get("s1.uncorroborated") is None
    assert len(pool.trip_writes) == 2, "one refresh retry, then stop"
    assert pool.trip_writes[1][2] > pool.trip_writes[0][2], \
        "the retry carries a FRESH timestamp past the tombstone"

    class _ThenLandsPool(_SweepPool):
        async def fetchrow(self, sql, *a, timeout=None):
            if "prev.admit" in sql:
                self.trip_writes.append(a)
                return {"had": False,
                        "wrote": len(self.trip_writes) > 1}
            return await _SweepPool.fetchrow(self, sql, *a,
                                             timeout=timeout)

    st2 = S1Emitter()
    st2._state_loaded = True
    st2.armed = True
    st2.cert_green = True
    pool2 = _ThenLandsPool([_srow(3, False)])
    asyncio.run(st2._corroboration_sweep(pool2))
    assert pool2.marked == [[3]], "the refreshed re-trip lands and stamps"
    assert st2.deltas.get("s1.uncorroborated") == 1


def test_same_height_remine_voids_the_earned_timestamp(st):
    """fleet r10 (major): a new hash for a KNOWN block number silently
    overwrote the buffered hash while the timestamp earned against the
    old hash survived — the fill emitted with the orphaned block's ts.
    A same-height re-mine is the same reorg evidence as a new height."""
    lst = _Listener(roster={MAKER: {"id": 7, "username": "mk"}})
    _wire(st, lst)
    st.observe(lst, MAKER_EV)
    e = st.pending[TX]
    e["ts"][BLK] = TS0                    # earned against 77…77
    st._ts_cache[(BLK, "0x" + "77" * 32)] = TS0
    st.observe(lst, dict(MAKER_EV, blockHash="0x" + "99" * 32,
                         logIndex="0x9"))
    assert e["ts"] == {}, "the earned timestamp is void"
    assert (BLK, "0x" + "77" * 32) not in st._ts_cache
    assert e["blocks"][BLK] == "0x" + "99" * 32, \
        "resolution re-earns against the NEW hash"


# ── fleet round 9 pins ──────────────────────────────────────────────
def test_migration_persist_keeps_the_epoch_zero_timestamp(st):
    """fleet r9 (major): `or time.time()` rewrote the legacy scalar's
    falsy 0.0 migration timestamp to now(), which outruns any
    tombstone — a cleared trip durably resurrected fleet-wide. The
    persist carries the true timestamp; the tombstone then refuses the
    stale re-persist exactly as designed."""
    pool = _Pool()
    st.trips = {"key_selfcheck": 0.0}
    ok = asyncio.run(st._persist_trip(pool, "key_selfcheck"))
    assert ok == "landed"
    assert pool.trip_writes[0][2] == 0.0, \
        "the falsy epoch-zero timestamp survives the persist"


def test_unusable_rows_never_testify_for_coverage():
    """fleet r9 (major): the cov span floor updated BEFORE the
    validity skip, so a degraded-index stub row (ts=1, no tx, zero
    size) set oldest=1.0 — a universal span waiver that false-tripped
    STICKY on a correct emission. Validity gates evidence; a sentinel
    floor rejects absurd timestamps outright."""
    import inspect
    from sportsassets.ingestion import reconciler as rec
    src = inspect.getsource(rec)
    skip = src.index("if not usable:")
    span = src.index("oldest_ts = float(ev.ts_epoch)")
    assert skip < span, "validity check precedes the span update"
    assert "ev.ts_epoch > 1e9" in src, "sentinel-timestamp floor"


def test_second_block_for_one_tx_voids_every_earned_timestamp(st):
    """fleet r9 (major): one tx lives in one canonical block — a
    re-mined fill joined the still-pending entry and its strictly
    earned pre-reorg timestamp was never re-verified, emitting a
    divergent-key twin. A second block number is reorg evidence in
    itself: earned timestamps void, and the old block's re-resolution
    then fails its hash check and abstains the whole tx."""
    lst = _Listener(roster={MAKER: {"id": 7, "username": "mk"}})
    _wire(st, lst)
    st.observe(lst, MAKER_EV)
    e = st.pending[TX]
    e["ts"][BLK] = TS0                      # strictly earned, pre-reorg
    st._ts_cache[(BLK, "0x" + "77" * 32)] = TS0
    st.observe(lst, dict(MAKER_EV, blockNumber=hex(BLK + 2),
                         blockHash="0x" + "88" * 32, logIndex="0x9"))
    assert e["ts"] == {}, "every earned timestamp is void"
    assert (BLK, "0x" + "77" * 32) not in st._ts_cache
    assert set(e["blocks"]) == {BLK, BLK + 2}


def test_observe_stamps_ws_head_advances(st):
    lst = _Listener(roster={})
    st.head = 0                              # fixture pre-advances it
    assert st.head_advanced_at == 0.0
    st.observe(lst, MAKER_EV)
    assert st.head_advanced_at > 0.0
    at = st.head_advanced_at
    st.observe(lst, dict(MAKER_EV, logIndex="0x9"))   # same block: no move
    assert st.head_advanced_at == at


# ── fleet round 11 pins ─────────────────────────────────────────────
def test_same_height_remine_drops_the_orphaned_logs(st, monkeypatch):
    """fleet r11 (CRITICAL): round 10 voided the timestamps but left
    the orphaned block-version's LOG in the buffer — a re-mined copy
    with a shifted logIndex passed the lix-only dup check, the single
    (blk, new-hash) resolution vouched for BOTH logs, and the armed
    path emitted the canonical fill AND a phantom that exists only on
    the orphaned chain (key-divergent double emission). Only logs
    carrying the new canonical hash survive the height; lix_seen
    rebuilds from the survivors; the decode cache invalidates."""
    lst = _Listener(roster={MAKER: {"id": 7, "username": "mk"}})
    _wire(st, lst)
    _arm(st)
    calls = _capture_ingest(monkeypatch)
    st.observe(lst, MAKER_EV)                      # hash 77…77, lix 1
    e = st.pending[TX]
    e["ts"][BLK] = TS0
    e["recs"], e["recs_n"] = [{"stale": True}], 1  # decode cache primed
    # the same fill re-mined at the same height: new hash, the tx
    # order changed (new logIndex), the book replayed differently
    # (different amounts — the phantom's would-be row)
    remined = dict(_ev(MAKER, TAKER, 0, TOKEN_INT, 0xE8EA90, 0x177ABE0),
                   blockHash="0x" + "99" * 32, logIndex="0x9")
    st.observe(lst, remined)
    assert len(e["logs"]) == 1, "the orphaned version's log is dropped"
    assert e["logs"][0]["blockHash"] == "0x" + "99" * 32
    assert e["lix_seen"] == {s1._lix_key(remined)}, \
        "lix_seen rebuilds from survivors (r15: keyed by block+hash)"
    assert "recs" not in e and "recs_n" not in e, "decode invalidated"
    # r37: the buffer has now seen TWO hashes at this height — a
    # proven contested height abstains outright (a lagging replica
    # must not arbitrate which side was real); the poller carries.
    # The r11 law this pin exists for still holds one level down:
    # the orphaned log is gone, so no path could decode the phantom.
    for entry in st.pending.values():
        entry["last_seen"] = time.time() - s1.DEBOUNCE_S - 1
    done = asyncio.run(st._finalize_tx(_Pool(), TX, e, time.time()))
    assert done is True and calls == [], \
        "a proven two-hash height abstains — the phantom AND the " \
        "canonical twin; the poller carries whatever was real"
    assert st.deltas.get("s1.abstain.contested") == 1


def test_midawait_remine_never_writes_back_the_orphaned_ts(st, monkeypatch):
    """fleet r11 (major): the resolution loop iterates a pre-await
    snapshot — a same-height re-mine landing DURING the resolve await
    was written back into e['ts'] AFTER observe()'s purge, silently
    undoing it, and the armed path emitted with the orphaned block's
    timestamp. The write-back now requires the hash it earned against
    to still be the entry's current hash; otherwise the block stays
    unresolved and re-earns against the new hash."""
    lst = _Listener(roster={MAKER: {"id": 7, "username": "mk"}})
    _wire(st, lst)
    _arm(st)
    calls = _capture_ingest(monkeypatch)
    _observe_all(st, lst, [MAKER_EV])
    e = st.pending[TX]
    remined = dict(MAKER_EV, blockHash="0x" + "99" * 32, logIndex="0x9")

    async def resolve_with_remine(blk, want_hash):
        st.observe(lst, remined)   # the WS burst shares the event loop
        return TS0                 # the node answered for the OLD hash

    monkeypatch.setattr(st, "_resolve_block", resolve_with_remine)
    done = asyncio.run(st._finalize_tx(_Pool(), TX, e, time.time()))
    assert calls == [], "nothing emits off the orphaned answer"
    assert done is False, "the block re-earns on a later pass"
    assert BLK not in e["ts"], "ts(old hash) is never written back"
    assert BLK not in e.get("ts_src", {})
    assert e["blocks"][BLK] == "0x" + "99" * 32


def test_midawait_reorg_generation_blocks_the_writeback(st, monkeypatch):
    """fleet r11 hardening: a NEW-height re-mine (and a sibling tx's
    removed notice) voids timestamps WITHOUT moving this height's
    buffered hash — the hash compare alone would let the post-await
    write-back restore a voided, pre-reorg-earned ts. Every purge that
    touches an entry's suffix advances its reorg generation; a write-
    back is valid only when the generation it captured before the
    await is still current."""
    lst = _Listener(roster={MAKER: {"id": 7, "username": "mk"}})
    _wire(st, lst)
    _arm(st)
    calls = _capture_ingest(monkeypatch)
    _observe_all(st, lst, [MAKER_EV])
    e = st.pending[TX]
    new_height = dict(MAKER_EV, blockNumber=hex(BLK + 2),
                      blockHash="0x" + "88" * 32, logIndex="0x9")

    async def resolve_with_reorg(blk, want_hash):
        st.observe(lst, new_height)   # voids ts; BLK's hash unchanged
        return TS0

    monkeypatch.setattr(st, "_resolve_block", resolve_with_reorg)
    done = asyncio.run(st._finalize_tx(_Pool(), TX, e, time.time()))
    assert calls == [] and done is False
    assert BLK not in e["ts"], \
        "the generation guard blocks the write-back even though the " \
        "buffered hash at this height never changed"
    assert e.get("reorg_gen", 0) >= 1

    # a SIBLING tx's removed notice at or below the height: same rule
    st2 = S1Emitter()
    st2.head = BLK + s1.CONFIRM_DEPTH + 1
    st2.http_url = "http://rpc.test"
    st2._state_loaded = True
    st2.armed = True
    st2.cert_green = True
    _wire(st2, lst)
    _observe_all(st2, lst, [MAKER_EV])
    e2 = st2.pending[TX]
    sibling_removed = dict(MAKER_EV, removed=True,
                           transactionHash="0x" + "ab" * 32)

    async def resolve_with_sibling_removed(blk, want_hash):
        st2.observe(lst, sibling_removed)
        return TS0

    monkeypatch.setattr(st2, "_resolve_block",
                        resolve_with_sibling_removed)
    done = asyncio.run(st2._finalize_tx(_Pool(), TX, e2, time.time()))
    assert calls == [] and done is False
    assert BLK not in e2["ts"], \
        "a sibling's removed notice mid-await voids this earn too"


def test_remine_during_probe_await_never_emits_the_orphaned_ts(
        st, monkeypatch):
    """fleet r11 (major, window E): rec['ts'] is copied out of e['ts']
    BEFORE the probe await, so observe()'s purge could not reach it —
    the membership check passes (a re-mine never pops the entry) and
    the emission carried the orphaned block's timestamp, a key-
    divergent twin of the venue's poll row. The emit path now re-
    verifies, after the await, that the record's timestamp is still
    the entry's own AND was earned against the entry's CURRENT hash."""
    lst = _Listener(roster={MAKER: {"id": 7, "username": "mk"}})
    _wire(st, lst)
    _arm(st)
    calls = _capture_ingest(monkeypatch)
    _observe_all(st, lst, [MAKER_EV])
    e = st.pending[TX]
    remined = dict(MAKER_EV, blockHash="0x" + "99" * 32, logIndex="0x9")

    class _ReminePool(_Pool):
        async def fetch(self, sql, *a, timeout=None):
            st.observe(lst, remined)   # lands during the probe await
            return []

    done = asyncio.run(st._finalize_tx(_ReminePool(), TX, e, time.time()))
    assert done is True and calls == [], \
        "the orphaned timestamp never reaches ingest"
    assert st.deltas.get("s1.abstain.reorged") == 1
    assert st.deltas.get("s1.emitted") is None


def test_dirty_walks_never_cover():
    """fleet r11 (major): round 9 stopped an unusable row testifying
    FOR coverage — but a walk that skipped the fill's OWN degraded
    stub still claimed clean span coverage off its healthy neighbors,
    and the sweep branded a correct, venue-visible emission 'never
    shown by the feed' — a permanent false STICKY trip. The reconciler
    counts every skipped-unusable row into cov->dirty; SQL_RECON_SINCE
    treats anything but a clean zero (absent = pre-round-11 format) as
    non-covering. Executed semantics: test_s1_sql_real_pg."""
    import inspect
    from sportsassets.ingestion import reconciler as rec
    src = inspect.getsource(rec)
    assert "dirty += 1" in src and '"dirty": dirty' in src
    assert "'dirty'" in s1.SQL_RECON_SINCE
    assert "ELSE false END" in s1.SQL_RECON_SINCE, \
        "a malformed dirty shape DEFERS — fail-safe, never fail-open"


def test_refused_refresh_reads_the_tombstone_clock(st):
    """fleet r11 (minor): the judgment-site refresh stamped an APP-
    clock timestamp against a tombstone written with PG now() — with
    the PG clock ahead the refresh lost every cycle, the flush's
    tombstone merge then released the in-memory trip, and the next
    sweep re-bumped the same verdict's counter durably, once per
    cycle for the life of the skew. The refresh now reads the
    tombstone's own clock (SQL_NOW), so fresh evidence always outruns
    the tombstone it answers."""
    _arm(st)

    class _SkewPool(_SweepPool):
        DB_NOW = float(TS0 + 999)

        async def fetchval(self, sql, *a, timeout=None):
            if "extract(epoch from now())" in sql:
                return self.DB_NOW
            return await _SweepPool.fetchval(self, sql, *a,
                                             timeout=timeout)

        async def fetchrow(self, sql, *a, timeout=None):
            if "prev.admit" in sql:
                self.trip_writes.append(a)
                return {"had": False,
                        "wrote": len(self.trip_writes) > 1}
            return await _SweepPool.fetchrow(self, sql, *a,
                                             timeout=timeout)

    pool = _SkewPool([_srow(3, False)])
    asyncio.run(st._corroboration_sweep(pool))
    assert len(pool.trip_writes) == 2
    assert pool.trip_writes[1][2] == _SkewPool.DB_NOW, \
        "the refresh timestamp comes from the DB clock, not time.time()"
    assert pool.marked == [[3]], "the same-clock refresh lands and stamps"
    assert st.deltas.get("s1.uncorroborated") == 1


# ── fleet round 12 pins ─────────────────────────────────────────────
def test_multi_block_entry_abstains_the_whole_tx(st, monkeypatch):
    """fleet r12 (CRITICAL): a two-height entry trusted per-block
    strict re-resolution — but an eventually-consistent HTTP provider
    verified the OLD height against the old chain on one finalize
    pass and the NEW height against the new chain on the next, giving
    one entry two strictly-earned timestamps from OPPOSITE sides of
    the reorg; the armed path then emitted a key-divergent twin of
    one fill. A second height in the buffer IS the reorg verdict: the
    whole tx abstains at finalize with ZERO resolution attempts."""
    lst = _Listener(roster={MAKER: {"id": 7, "username": "mk"}})
    _wire(st, lst)
    _arm(st)
    calls = _capture_ingest(monkeypatch)
    _observe_all(st, lst, [MAKER_EV])
    st.observe(lst, dict(MAKER_EV, blockNumber=hex(BLK + 2),
                         blockHash="0x" + "88" * 32, logIndex="0x9"))
    e = st.pending[TX]
    assert set(e["blocks"]) == {BLK, BLK + 2}

    resolved = []

    async def never_resolve(blk, want_hash):
        resolved.append(blk)
        raise AssertionError("a split entry must never reach the RPC")

    monkeypatch.setattr(st, "_resolve_block", never_resolve)
    done = asyncio.run(st._finalize_tx(_Pool(), TX, e, time.time()))
    assert done is True and calls == [] and resolved == [], \
        "two heights = reorg verdict: abstain outright, zero RPC"
    assert st.deltas.get("s1.abstain.reorged") == 1
    assert st.deltas.get("s1.emitted") is None


def test_fresh_trips_stamp_the_db_clock(st):
    """fleet r12 (minor): trips stamped on the raw app clock outran
    PG-written tombstones under app-ahead skew — an operator clear
    reported success but never released the in-memory copy, and a
    queued persist retry durably resurrected the cleared trip. The
    sweep learns the DB clock offset from SQL_NOW and every fresh
    trip stamps PG's 'now', both skew directions."""
    _arm(st)

    class _DbClockPool(_SweepPool):
        DB_NOW = float(TS0 + 999)

        async def fetchval(self, sql, *a, timeout=None):
            if "extract(epoch from now())" in sql:
                return self.DB_NOW
            return await _SweepPool.fetchval(self, sql, *a,
                                             timeout=timeout)

    pool = _DbClockPool([_srow(3, False)])
    asyncio.run(st._corroboration_sweep(pool))
    assert pool.trip_writes, "the verdict trips"
    # r13: the anchor advances by MONOTONIC elapsed, so the stamp sits
    # within real elapsed test time of the DB reading, never exact
    assert abs(pool.trip_writes[0][2] - _DbClockPool.DB_NOW) < 2.0, \
        "a FRESH trip's timestamp is PG's clock, not the app host's"
    assert abs(st.trips["uncorroborated:3"] - _DbClockPool.DB_NOW) < 2.0


def test_ts_mangled_rows_are_unusable_everywhere():
    """fleet r12 (major x2): a stub with tx and size intact but a
    mangled timestamp passed validity, ingested as a key-divergent
    1970-dated row (ts is a dedupe-key component) that could never
    stamp the real fill's venue_seen_at, and left the walk's dirty
    count at 0 — clean coverage claimed over a span whose fill row
    was garbage, false STICKY on a correct emission. The ts sentinel
    floor is part of validity in BOTH carriers."""
    import inspect
    from sportsassets.ingestion import dedupe as dd
    from sportsassets.ingestion import poller as pol
    from sportsassets.ingestion import reconciler as rec
    for src in (inspect.getsource(rec), inspect.getsource(pol)):
        assert "key_fields_valid(ev)" in src, \
            "ONE shared validity gate guards ingest in both carriers"
    assert "ts <= 1e9" in inspect.getsource(dd), "ts sentinel floor"


# ── fleet round 13 pins ─────────────────────────────────────────────
def test_validity_floor_covers_every_dedupe_key_field():
    """fleet r13 (major) + r14 (major x3): the validity gate must
    judge each dedupe-key field AS THE KEY NORMALIZES IT — raw checks
    let whitespace tx/asset (truthy, strips to ''), sub-quantum
    size/price (1e-7 > 0, quantizes to 0.000000), NaN (every ordered
    comparison is False) and Infinity (raises inside the key, killing
    the whole wallet's poll batch) through. Functional, not grepped:
    every hostile shape is refused, no shape raises."""
    from types import SimpleNamespace as NS

    from sportsassets.ingestion.dedupe import key_fields_valid

    good = dict(tx_hash="0x" + "ab" * 32, asset="123", side="BUY",
                size=10.0, price=0.5, ts_epoch=1_724_000_000)
    assert key_fields_valid(NS(**good)) is True
    hostile = [
        {"tx_hash": ""}, {"tx_hash": "   "},
        {"asset": ""}, {"asset": "  "}, {"asset": None},
        {"side": ""}, {"side": "HOLD"},
        {"size": 0.0}, {"size": -1.0}, {"size": 1e-7},
        {"size": float("nan")}, {"size": float("inf")},
        {"price": 0.0}, {"price": 4e-7}, {"price": float("nan")},
        {"price": float("-inf")},
        {"ts_epoch": 0}, {"ts_epoch": 1}, {"ts_epoch": int(1e9)},
        {"ts_epoch": float("nan")}, {"ts_epoch": float("inf")},
        {"ts_epoch": None},
        # r15: storability bounds — column overflow and ms-scaled
        # epochs raised INSIDE ingest, past the r14 gate
        {"price": 420000.0}, {"size": 1e19},
        {"ts_epoch": 1_756_350_000_000},
    ]
    for mut in hostile:
        ev = NS(**{**good, **mut})
        assert key_fields_valid(ev) is False, f"must refuse {mut}"
    # a lowercase side is venue formatting, not degradation — the key
    # uppercases it and so does the gate
    assert key_fields_valid(NS(**{**good, "side": "buy"})) is True


TOKEN_B_INT = int("9c546cfe" + "33" * 28, 16)


def test_bundle_retry_after_remine_never_reemits_an_asset(
        st, monkeypatch):
    """fleet r13 (CRITICAL): a two-market bundle's second probe hit a
    transient DB error, the tx retried per r5's design, a benign
    same-height re-mine landed between the passes, and pass 2's
    re-earned block ts shifted the first market's key — emit_dup
    missed on the new key and the r6 own-sibling allowance (built for
    DIFFERENT-asset legs) whitelisted the entry's own earlier row of
    the SAME asset: one fill, two key-divergent trades rows. The
    entry now tracks (wallet, asset) it has ingested and refuses a
    re-emission structurally, whatever the timestamp did."""
    lst = _Listener(roster={MAKER: {"id": 7, "username": "mk"}})
    _wire(st, lst)
    _arm(st)
    calls = _capture_ingest(monkeypatch)
    ev_a = MAKER_EV
    ev_b = _ev(MAKER, TAKER, 0, TOKEN_B_INT, 0x30D40, 0x61A80,
               log_index=2)
    _observe_all(st, lst, [ev_a, ev_b])
    e = st.pending[TX]

    class _FlakyProbePool(_Pool):
        def __init__(self):
            super().__init__()
            self.n = 0

        async def fetch(self, sql, *a, timeout=None):
            self.n += 1
            if self.n == 2:          # second market's probe, pass 1
                raise RuntimeError("transient DB blip")
            return []

    pool = _FlakyProbePool()
    done = asyncio.run(st._finalize_tx(pool, TX, e, time.time()))
    assert done is False, "transient probe error = retry, not forfeit"
    assert len(calls) == 1, "pass 1 ingested exactly the first market"
    # between the passes: a benign same-height re-mine of the SAME tx
    # (new hash, shifted log indexes) + the node re-serving a SHIFTED
    # block timestamp for the new hash
    st.observe(lst, dict(ev_a, blockHash="0x" + "99" * 32,
                         logIndex="0x9"))
    st.observe(lst, dict(ev_b, blockHash="0x" + "99" * 32,
                         logIndex="0xa"))
    st._client = _FakeClient(_Resp(200, {"result": {
        "timestamp": hex(TS0 + 3)}}))
    done = asyncio.run(st._finalize_tx(pool, TX, e, time.time()))
    assert done is True
    # r37 evolution: the mid-retry re-mine put TWO hashes at one
    # height through this entry — a proven contested height, and pass
    # 2 abstains outright rather than letting one replica arbitrate.
    # The r13 law this pin exists for holds a fortiori: the first
    # market ingested exactly once on pass 1, and NOTHING re-emits —
    # the second market defers to the poller with the height.
    assert len(calls) == 1, \
        "the pass-1 row stands alone; the re-mined height never " \
        "produces a second row of the same asset"
    assert st.deltas.get("s1.abstain.contested") == 1, \
        "pass 2 abstains the contested height under its honest reason"


def test_second_distinct_same_asset_fill_defers_by_design():
    """fleet r14 (minor, DOCUMENTED DESIGN): the r13 per-asset cap
    also defers a genuine second distinct same-asset exec_owner fill
    of one tx (taker sweep filling two resting orders of one whale in
    one market). No re-mine-stable identity separates that rare shape
    from the r13 key-divergent twin — logIndex, ts and amounts can
    ALL shift across a re-mine — and admitting it re-opens the double
    emission class. The second fill defers to the poller under its
    own named reason; the decision is written at the refusal site."""
    import inspect

    import sportsassets.ingestion.s1_emitter as mod
    src = inspect.getsource(mod)
    assert "s1.abstain.same_asset_entry" in src
    assert "DESIGN DECISION (fleet round 14" in src, \
        "the trade-off must stay documented where it is enforced"


# ── fleet round 15 pins ─────────────────────────────────────────────
def test_new_height_remine_with_reused_logindex_still_abstains(
        st, monkeypatch):
    """fleet r15 (CRITICAL): a re-mined copy at a NEW height that
    reused the buffered logIndex hit the lix-only dup check and
    returned BEFORE the second height was recorded — the entry stayed
    single-block, the round-12 two-heights gate never fired, and a
    stale replica re-verified the orphaned block and emitted it. The
    new height is now recorded the moment the branch sees it, and
    frame-dedupe is keyed by (block, hash, index) — a different block
    version is never a duplicate frame."""
    lst = _Listener(roster={MAKER: {"id": 7, "username": "mk"}})
    _wire(st, lst)
    _arm(st)
    calls = _capture_ingest(monkeypatch)
    _observe_all(st, lst, [MAKER_EV])
    e = st.pending[TX]
    # the re-mined copy: new height, new hash, SAME logIndex 0x1
    st.observe(lst, dict(MAKER_EV, blockNumber=hex(BLK + 2),
                         blockHash="0x" + "88" * 32))
    assert set(e["blocks"]) == {BLK, BLK + 2}, \
        "reorg evidence is recorded even when the logIndex collides"

    resolved = []

    async def never_resolve(blk, want_hash):
        resolved.append(blk)
        raise AssertionError("a split entry must never reach the RPC")

    monkeypatch.setattr(st, "_resolve_block", never_resolve)
    done = asyncio.run(st._finalize_tx(_Pool(), TX, e, time.time()))
    assert done is True and calls == [] and resolved == []
    assert st.deltas.get("s1.abstain.reorged") == 1
    # and a genuinely DUPLICATED frame (same block, hash, index) is
    # still refused as a dup
    st.observe(lst, MAKER_EV)
    assert st.deltas.get("s1.dup_event") == 1


def test_sweep_last_look_confirms_a_just_stamped_row(st):
    """fleet r15 (major): the sweep's SQL_SWEEP snapshot read
    ok=false, a covering reconcile run finished INSIDE the sweep's
    awaited gap — stamping the row AND becoming the coverage evidence
    — and the verdict, judged off the stale snapshot, branded a
    venue-corroborated row uncorroborated permanently. The verdict
    now re-reads the row after coverage is found: stamped now =
    confirmed."""
    _arm(st)
    pool = _SweepPool([_srow(3, False)])
    pool.recheck_ok = True             # the covering run stamped it
    asyncio.run(st._corroboration_sweep(pool))
    assert st.deltas.get("s1.confirmed") == 1, \
        "a row stamped by the covering run itself CONFIRMS"
    assert st.deltas.get("s1.uncorroborated") is None
    assert st.trips == {} and st.armed is True
    assert pool.marked == [[3]], "confirmed rows still stamp judged"


def test_ingest_containment_is_per_row_in_both_carriers():
    """fleet r15 (major): a gate-passing row can still fail INSIDE
    ingest (side CHECK constraint on the raw value, NUMERIC overflow,
    datetime range) — and the uncontained await killed the wallet's
    whole poll batch / reconciler walk, re-opening the round-14 class
    one call later. The parse now stores the gate's own normalized
    values, the gate mirrors column bounds, and the ingest await is
    contained per row: the poller skips the row, the reconciler
    counts it dirty and keeps walking."""
    import inspect
    from sportsassets.ingestion import poller as pol
    from sportsassets.ingestion import reconciler as rec
    psrc = inspect.getsource(pol)
    assert '.upper().strip()' in psrc, "side stored as judged"
    assert psrc.count("except Exception") >= 2, \
        "poller contains the ingest await, not just the parse"
    rsrc = inspect.getsource(rec)
    assert rsrc.count("dirty += 1") >= 2, \
        "a row that cannot land counts into dirty — the walk " \
        "continues and still never claims clean coverage"


# ── fleet round 16 pins ─────────────────────────────────────────────
def test_judged_stamp_is_conditional_on_still_unseen():
    """fleet r16 (major): the round-15 last look read the row BEFORE
    the trip — a venue stamp committing between the recheck and
    SQL_TRIP still became a permanent false verdict. The judged stamp
    now carries venue_seen_at IS NULL in its own WHERE: verdict-time
    and permanence-time are one atomic instant. Executed semantics:
    test_s1_sql_real_pg."""
    assert "venue_seen_at IS NULL" in s1.SQL_MARK_JUDGED
    assert "s1_checked_at IS NULL" in s1.SQL_MARK_JUDGED
    assert "venue_seen_at" not in s1.SQL_MARK, \
        "the confirmed stamp stays unconditional"


def test_judged_stamp_lost_to_a_venue_stamp_self_clears(st):
    """fleet r16 (major): when the judged stamp is refused because the
    VENUE stamped the row inside the recheck→trip gap, the trip just
    persisted brands a corroborated row — the sweep releases it
    itself (SQL_CLEAR + in-memory), counts nothing, and leaves the
    row unstamped for the next sweep to CONFIRM."""
    _arm(st)

    class _VenueRacePool(_SweepPool):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.rechecks = 0
            self.cleared: list[str] = []

        async def fetchrow(self, sql, *a, timeout=None):
            if "trips_cleared" in sql and "prev.admit" not in sql and \
                    sql.lstrip().startswith("WITH prev"):
                # SQL_CLEAR (round 26 shape): reports whether THIS
                # call transitioned the reason out
                self.cleared.append(a[1])
                return {"removed": True, "trips": {}, "cleared": {}}
            if "FROM trades WHERE id" in sql:
                # unseen at the pre-trip recheck; SEEN at the post-
                # mark recheck — the venue stamped inside the gap
                self.rechecks += 1
                return {"ok": self.rechecks > 1}
            return await _SweepPool.fetchrow(self, sql, *a,
                                             timeout=timeout)

        async def fetch(self, sql, *a, timeout=None):
            if "venue_seen_at IS NULL RETURNING" in sql:
                self.marked.append(list(a[0]))
                return []            # the conditional stamp refused
            return await _SweepPool.fetch(self, sql, *a,
                                          timeout=timeout)

    pool = _VenueRacePool([_srow(3, False)])
    asyncio.run(st._corroboration_sweep(pool))
    assert pool.trip_writes, "the trip persisted before the stamp"
    assert pool.cleared == ["uncorroborated:3"], \
        "the race-lost verdict is released durably, by the sweep"
    assert st.trips == {}, "and released in-memory"
    assert st.deltas.get("s1.uncorroborated") is None, \
        "a verdict that lost to its own evidence counts nothing"
    assert st.deltas.get("s1.trip_self_cleared") == 1


# ── fleet round 17 pins ─────────────────────────────────────────────
def test_orphaned_uncorroborated_trip_heals_on_the_next_sweep(st):
    """fleet r17 (major): the round-16 self-clear could fail on one
    transient error, and its claimed retry path was unreachable — the
    row confirms through the ok path, which never touches trip state,
    so the false trip stood forever against a database contradicting
    it. Every sweep now reconciles the live trip set against the
    evidence: an 'uncorroborated:<id>' whose row the venue has since
    corroborated is released, durably and in-memory, whatever orphan
    path created it."""
    _arm(st)
    st.trips["uncorroborated:3"] = float(TS0)   # orphaned somewhere
    st.armed = False

    class _HealPool(_SweepPool):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.cleared: list[str] = []
            self.recheck_ok = True

        async def fetchrow(self, sql, *a, timeout=None):
            if "trips_cleared" in sql and "prev.admit" not in sql and \
                    sql.lstrip().startswith("WITH prev"):
                # SQL_CLEAR (round 26 shape): reports whether THIS
                # call transitioned the reason out
                self.cleared.append(a[1])
                return {"removed": True, "trips": {}, "cleared": {}}
            return await _SweepPool.fetchrow(self, sql, *a,
                                             timeout=timeout)

    pool = _HealPool([], wallets=[])
    asyncio.run(st._corroboration_sweep(pool))
    assert pool.cleared == ["uncorroborated:3"], \
        "the healer releases the orphan durably"
    assert st.trips == {}, "and in-memory"
    assert st.deltas.get("s1.trip_self_cleared") == 1


def test_genuinely_unseen_trips_never_heal(st):
    """The healer keys strictly on the evidence: a row the venue has
    NOT corroborated keeps its trip — healing is never a loophole."""
    _arm(st)
    st.trips["uncorroborated:3"] = float(TS0)
    st.armed = False
    pool = _SweepPool([], wallets=[])       # recheck default: False
    asyncio.run(st._corroboration_sweep(pool))
    assert "uncorroborated:3" in st.trips, "unseen = the trip stands"
    assert st.deltas.get("s1.trip_self_cleared") is None


def test_walk_pages_overlap_and_a_boundary_shift_dirties():
    """fleet r17 (major): offset pagination issued a fresh query per
    page — an unstable tiebreak on two same-second rows straddling a
    boundary re-served the tie-mate and the S1 fill was NEVER walked,
    while every page was individually valid: clean coverage claimed
    over a skipped fill, permanent false STICKY. Pages now overlap by
    one row as a continuity witness; a boundary that does not re-serve
    the witness (or an overlap row that vanishes) dirties the walk."""
    import inspect
    from sportsassets.ingestion import reconciler as rec
    src = inspect.getsource(rec)
    assert "idents[:k] == witness" in src, \
        "the witness run must return verbatim and in order"
    assert "offset += max(1, len(page) - len(witness))" in src, \
        "each page re-requests the previous tail rows (positional " \
        "advance over the page AS SERVED — r19)"
    assert src.count("dirty += 1") >= 5, \
        "validity, ingest-failure, boundary mismatch, vanished " \
        "overlap AND ambiguous witness all dirty the walk"


def test_witness_is_a_run_of_rows_and_twins_make_it_ambiguous():
    """fleet r18 (major): a RAW-IDENTICAL twin (equal legs of one
    same-second taker bundle) could impersonate a single-row witness
    after a shift by exactly the twin distance — the walk resumed
    below the skipped fill with dirty=0 and clean coverage claimed.
    The witness is now OVERLAP_K=3 consecutive rows (masking needs
    three consecutive rows each with an aligned ident-twin), and a
    witness row with a twin visible in its own page dirties the
    boundary before it is crossed. The residual fails toward defer."""
    import inspect
    from sportsassets.ingestion import reconciler as rec
    assert rec.OVERLAP_K >= 3, "a 1-row witness is impersonable"
    src = inspect.getsource(rec)
    assert "idents.count(w) > 1" in src, \
        "visible twins make the boundary ambiguous — dirty"
    assert "witness = idents[-OVERLAP_K:]" in src


def test_uncorroborated_needs_two_independent_covering_runs():
    """fleet r20 (major): round 20 executed the round-18 'improbable
    residual' — an aligned twin TRIPLE plus an exact-distance shift
    masked the 3-row witness and ONE walk claimed clean coverage over
    a skipped fill. Content identity cannot prove positional
    continuity against raw-identical rows, so no single walk is
    trusted: the verdict requires TWO distinct clean covering runs,
    whose feed geometry decorrelates between hourly walks. Failure
    direction is pure deferral. Executed: test_s1_sql_real_pg."""
    # r39: the decorrelation premise is explicit in the statement —
    # coverage counts DISTINCT newest testimony, so a frozen or
    # poisoned feed serving byte-identical geometry twice defers
    assert "LIMIT 64" in s1.SQL_RECON_SINCE
    assert "ORDER BY started_at DESC" in s1.SQL_RECON_SINCE, \
        "r42 F3 + r44 (major): unordered starved nondeterministically; " \
        "ASC anchored the window to the 64 EARLIEST runs forever — an " \
        "ordinary 64h quiet spell then silenced the alarm for the " \
        "life of the row. The window is the most RECENT runs."
    assert "count(DISTINCT q.nv)" in s1.SQL_RECON_SINCE
    import inspect
    src = inspect.getsource(s1)
    assert 'int(ran["n"] or 0) < 2' in src, \
        "the sweep defers below two covering runs"


def test_non_dict_batch_elements_cost_one_row_not_the_walk():
    """fleet r19 (major): the witness build ran _row_ident over the
    RAW batch before the per-row containment — one JSON null raised
    AttributeError, the wallet-level handler booked failed:<addr>,
    and the wallet's entire hourly walk (the sole backstop for fills
    beyond the poll window) died on every run while the heartbeat
    said 'ok'. Non-dict elements are now pre-filtered and counted
    dirty; a non-list body defers without aborting the wallet."""
    import inspect
    from sportsassets.ingestion import reconciler as rec
    src = inspect.getsource(rec)
    assert "isinstance(page, list)" in src, "non-list body defers"
    assert "[r for r in page if isinstance(r, dict)]" in src, \
        "non-dict elements are excluded before ANY per-element code"
    assert "dirty += len(page) - len(batch)" in src, \
        "and each exclusion counts into dirty — never a clean claim"
    idx_filter = src.index("[r for r in page if isinstance(r, dict)]")
    idx_idents = src.index("idents = [_row_ident(r) for r in batch]")
    assert idx_filter < idx_idents, \
        "the filter must precede the witness build it protects"


def test_db_clock_anchor_survives_wall_clock_steps(st, monkeypatch):
    """fleet r13 (minor): the round-12 offset was wall-clock-relative,
    so an NTP/VM step landing between the sweep's SQL_NOW read and a
    between-sweeps trip stamped the trip into PG's FUTURE — a stamp
    no tombstone could outrun (clear wedged in-memory; a queued
    persist retry resurrected the cleared trip durably). The anchor
    now advances by time.monotonic, immune to any wall-clock step."""
    import time as _t
    st._db_anchor = (float(TS0 + 500), _t.monotonic())
    monkeypatch.setattr(s1.time, "time", lambda: TS0 + 99_999.0)
    assert abs(st._now_db() - (TS0 + 500)) < 2.0, \
        "a +99999s wall-clock step must not move the trip clock"


# ── fleet round 21 pins ─────────────────────────────────────────────
def test_finalize_pop_requires_entry_identity(st, monkeypatch):
    """fleet r21 (major): run() popped self.pending by TX KEY after
    _finalize_tx returned done — but finalize awaits, and a removed-
    notice arriving mid-await pops the entry while the canonical
    re-mined log creates a FRESH entry under the same key. The key-
    only pop destroyed that fresh entry with zero accounting: the
    canonical fill silently never finalized (poller-recovered at
    best, minutes later, and invisible to every S1 counter). The pop
    now requires ENTRY IDENTITY — pending.get(tx) must be the exact
    entry finalize ran on; a successor entry is someone else's work.
    Functional: the real run() loop drives a finalize that swaps in
    a successor entry mid-call, and the successor must survive."""
    import inspect

    import sportsassets.db as db

    monkeypatch.setattr(s1, "TICK_S", 0.001)
    pool = _Pool()

    async def fake_pool():
        return pool

    monkeypatch.setattr(db, "get_pool", fake_pool)

    async def noop(*a, **k):
        return None

    st._check_cert = noop
    st._corroboration_sweep = noop
    st._flush = noop
    st.listener = None
    st.last_flush_at = TS0 + 10.0
    monkeypatch.setattr(
        S1Emitter, "_should_poll_head", lambda self, now: False)

    e1 = {"last_seen": 0.0, "first_seen": 0.0, "blocks": {}}
    e2 = {"last_seen": TS0 + 10_000.0, "first_seen": TS0 + 10.0,
          "blocks": {}}
    st.pending[TX] = e1
    ran = []

    async def fake_finalize(pool_, tx, entry, now):
        # mid-finalize: a removed notice popped THIS entry and the
        # canonical re-mined log registered a fresh one at the key
        ran.append(entry)
        st.pending[TX] = e2
        return True

    st._finalize_tx = fake_finalize

    async def drive():
        task = asyncio.ensure_future(st.run())
        for _ in range(400):
            await asyncio.sleep(0.005)
            if ran:
                break
        await asyncio.sleep(0.02)      # let any post-finalize pop land
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(drive())
    assert ran and ran[0] is e1, "finalize ran on the original entry"
    assert st.pending.get(TX) is e2, \
        "a successor entry under the same tx key survives the pop"
    src_run = inspect.getsource(S1Emitter.run)
    assert "self.pending.get(tx) is e" in src_run, \
        "the pop is identity-guarded, never key-guarded"


def test_redundant_clear_never_bumps_the_release_counter(st):
    """fleet r26 (minor): every process holding an adopted copy of a
    trip bumped s1.trip_self_cleared on its own redundant clear —
    SQL_CLEAR carried no transition signal and the delta-merge made
    the inflation durable (N bumps for one release across N
    processes), violating the round-8 count-only-what-THIS-process-
    transitioned law. SQL_CLEAR now RETURNs whether the reason was
    actually present pre-update, and both call sites gate the bump
    on it."""
    _arm(st)
    st.trips["uncorroborated:9"] = TS0 + 1.0

    class _AdoptedPool(_SweepPool):
        async def fetchrow(self, sql, *a, timeout=None):
            if "trips_cleared" in sql and "prev.admit" not in sql and \
                    sql.lstrip().startswith("WITH prev"):
                # another process already released this reason: the
                # UPDATE matched but removed nothing
                return {"removed": False, "trips": {}, "cleared": {}}
            if "FROM trades WHERE id" in sql:
                return {"ok": True}          # venue-stamped
            return await _SweepPool.fetchrow(self, sql, *a,
                                             timeout=timeout)

    pool = _AdoptedPool([], wallets=[])
    asyncio.run(st._corroboration_sweep(pool))
    assert "uncorroborated:9" not in st.trips, \
        "the local memory still releases"
    assert st.deltas.get("s1.trip_self_cleared") is None, \
        "but a transition another process made is never re-counted"
    assert "removed" in s1.SQL_CLEAR, "the statement carries the signal"


# ── fleet round 29 pins ─────────────────────────────────────────────
def test_trip_firing_counts_only_this_process_transition(st):
    """fleet r29 (minor): the s1.trip.* firing bump lived in _trip,
    guarded only by the in-process trips dict — every process
    re-judging or adopting the SAME standing verdict counted one
    firing each, and the server-side delta merge made the inflation
    durable (the round-8/round-26 count-only-what-THIS-process-
    transitioned law, violated on the firing side). The bump now
    rides the persist, gated on SQL_TRIP's own transition signal."""
    pool = _Pool()
    st._trip("key_selfcheck")
    assert st.deltas.get("s1.trip.key_selfcheck") is None, \
        "the in-memory trip alone never counts — the statement decides"
    ok = asyncio.run(st._persist_trip(pool, "key_selfcheck"))
    assert ok == "landed"
    assert st.deltas.get("s1.trip.key_selfcheck") == 1, \
        "a landed FIRST recording of the reason is the one firing"

    class _AdoptedPool(_Pool):
        async def fetchrow(self, sql, *a, timeout=None):
            if "prev.admit" in sql:
                # the reason already stands on disk — a sibling
                # process landed it; the union merges, transitions
                # nothing
                return {"had": True, "wrote": True}
            return None

    st2 = S1Emitter()
    st2._trip("key_selfcheck")
    ok = asyncio.run(st2._persist_trip(_AdoptedPool(), "key_selfcheck"))
    assert ok == "landed"
    assert st2.deltas.get("s1.trip.key_selfcheck") is None, \
        "a firing another process already counted is never re-counted"
    assert "prev.had AS had" in s1.SQL_TRIP and \
        "prev.admit AS wrote" in s1.SQL_TRIP, \
        "the statement carries the transition signal"


def test_cert_metrics_answer_when_without_moving_the_bar(st):
    """Owner ask 2026-08-28 ('can we get S1 live right now?'): the
    cert reason alone could not answer WHEN. _judge_cert now stashes
    every judged quantity — window age, the green epoch, floor
    progress — while the judgment order and every bar stay
    byte-identical. The status beat carries them."""
    now = float(TS0)
    doc = {"window_start": now - 3 * 86400, "health_start": now - 2 * 86400,
           "decoder_fp": s1.DECODER_FP,
           "counters": {"sim_ven_suppressed": 700, "decoded_agg": 90},
           "at_window": {"sim_ven_suppressed": 100, "decoded_agg": 10}}
    green, reason = st._judge_cert(doc, now)
    assert (green, reason) == (False, "window_young")
    m = st.cert_metrics
    assert m["window_age_d"] == 3.0 and m["window_needs_d"] == 7.0
    assert m["green_at_epoch"] == round(now + 4 * 86400)
    assert m["ven_suppressed"] == 600 and m["agg_decoded"] == 80
    # an aged window with the same floors goes green — bars unmoved
    doc["window_start"] = now - 8 * 86400
    assert st._judge_cert(doc, now) == (True, "green")
    from sportsassets.ingestion.s1_emitter import emitter_beat
    assert "cert_metrics" in emitter_beat()


# ── fleet round 30 pins ─────────────────────────────────────────────
def test_sibling_frame_hash_conflict_voids_the_earned_timestamp(st):
    """fleet r30 (major): a DIFFERENT tx's frame carrying (height,
    new-hash) against a pending sibling's recorded hash at that
    height is delivered reorg evidence — two hashes at one height
    cannot both be canonical — but observe() filed it only under its
    own entry: no purge, no reorg_gen bump, and the sibling's
    strictly-earned old-chain timestamp sailed through every
    post-await self-comparison into an ARMED ingest of a fill that
    exists only on the orphaned chain. The identical information
    delivered as a removed notice already purged (round 7). The frame
    channel now purges too; strict re-resolution then refuses the
    orphaned side."""
    lst = _Listener(roster={MAKER: {"id": 7, "username": "mk"}})
    _wire(st, lst)
    st.observe(lst, MAKER_EV)
    e = st.pending[TX]
    e["ts"][BLK] = TS0                    # earned against 77…77
    e.setdefault("ts_src", {})[BLK] = "0x" + "77" * 32
    st._ts_cache[(BLK, "0x" + "77" * 32)] = TS0
    gen0 = e.get("reorg_gen", 0)

    # corroboration first: a sibling frame carrying the SAME hash at
    # the height is agreement, never evidence — nothing is voided
    st.observe(lst, dict(MAKER_EV, transactionHash="0x" + "e2" * 32,
                         logIndex="0x7"))
    assert e["ts"].get(BLK) == TS0, "agreement voids nothing"
    assert st.deltas.get("s1.sibling_hash_conflict") is None

    # the kill: a sibling frame at the same height, DIFFERENT hash
    st.observe(lst, dict(MAKER_EV, transactionHash="0x" + "e3" * 32,
                         blockHash="0x" + "99" * 32, logIndex="0x8"))
    assert e["ts"] == {}, \
        "the sibling's earned timestamp is void — re-resolution " \
        "must re-earn against the recorded hash, which the live " \
        "chain now refuses"
    assert (BLK, "0x" + "77" * 32) not in st._ts_cache
    assert e.get("reorg_gen", 0) > gen0, \
        "the write-back guard must see a new generation"
    assert st.deltas.get("s1.sibling_hash_conflict") == 1
    # the conflicting frame's own entry records ITS hash — but round
    # 32 proved a single replica must not arbitrate a proven two-hash
    # height, so the height is contested and BOTH sides abstain at
    # finalize; the poller carries whatever was real
    assert st.pending["0x" + "e3" * 32]["blocks"][BLK] == "0x" + "99" * 32
    assert BLK in st.contested, "the conflict marks the height"


# ── fleet round 31 pins ─────────────────────────────────────────────
def test_reorg_evidence_above_voids_the_lower_earned_timestamp(st):
    """fleet r31 (major, executed by the fleet through the REAL run
    loop): a sibling hash conflict at height C purged only >= C, so a
    pending fill strictly earned at B < C sailed through every
    post-await self-comparison into an ARMED ingest of a fill that
    exists only on the orphaned chain — the fork point is never
    delivered, so evidence at C makes every lower height suspect too.
    All reorg-evidence channels now void every earned timestamp; the
    strict resolver re-earns each height and refuses whatever the
    live chain rewrote."""
    lst = _Listener(roster={MAKER: {"id": 7, "username": "mk"}})
    _wire(st, lst)
    st.observe(lst, MAKER_EV)                 # fill at BLK, hash 77…
    e = st.pending[TX]
    e["ts"][BLK] = TS0
    e.setdefault("ts_src", {})[BLK] = "0x" + "77" * 32
    st._ts_cache[(BLK, "0x" + "77" * 32)] = TS0
    gen0 = e.get("reorg_gen", 0)
    # a sibling pair at C = BLK+1 delivers a hash conflict ABOVE the
    # earned height: old-chain frame, then new-chain frame
    st.observe(lst, dict(MAKER_EV, transactionHash="0x" + "d1" * 32,
                         blockNumber=hex(BLK + 1),
                         blockHash="0x" + "aa" * 32, logIndex="0x5"))
    st.observe(lst, dict(MAKER_EV, transactionHash="0x" + "d2" * 32,
                         blockNumber=hex(BLK + 1),
                         blockHash="0x" + "bb" * 32, logIndex="0x6"))
    assert st.deltas.get("s1.sibling_hash_conflict") == 1
    assert e["ts"] == {}, \
        "the fork may sit at or below B — the earned ts must re-earn"
    assert (BLK, "0x" + "77" * 32) not in st._ts_cache
    assert e.get("reorg_gen", 0) > gen0, \
        "an in-flight write-back below the evidence height dies too"


# ── fleet round 32 pins ─────────────────────────────────────────────
def test_blockless_removed_notice_still_purges(st):
    """fleet r32 (major): the removed channel popped the named tx as
    reorg evidence unconditionally but called the purge only `if
    rblk:` — a notice with an ABSENT or null blockNumber skipped the
    purge entirely and a sibling's old-chain timestamp armed-ingested
    an orphaned fill. The round-31 purge is height-agnostic, so the
    height's parseability cannot gate whether evidence counts."""
    lst = _Listener(roster={})
    h = "0xabc"
    other_tx = "0x" + "ee" * 32

    for bad_bn in ("ABSENT", None):
        st._ts_cache[(99, h)] = 1
        st.pending[other_tx] = {"logs": [], "blocks": {99: h},
                                "ts": {99: 1}, "evicted": False}
        ev = dict(MAKER_EV, removed=True)
        if bad_bn == "ABSENT":
            ev.pop("blockNumber", None)
        else:
            ev["blockNumber"] = bad_bn
        st.observe(lst, ev)
        assert st._ts_cache == {}, bad_bn
        assert st.pending[other_tx]["ts"] == {}, \
            "a removed notice is reorg evidence whatever its height " \
            "field parses to"


def test_contested_height_abstains_every_side(st, monkeypatch):
    """fleet r32 (major): purge-and-re-earn arbitrated a PROVEN
    two-hash height by whichever replica answered first — a stale one
    re-verified the orphaned side and armed S1 ingested it next to
    the canonical twin. Round 12's law extends across entries: every
    tx buffered at a contested height abstains, canonical side
    included; the poller carries whatever was real."""
    lst = _Listener(roster={MAKER: {"id": 7, "username": "mk"}})
    _wire(st, lst)
    _arm(st)
    calls = _capture_ingest(monkeypatch)
    _observe_all(st, lst, [MAKER_EV])
    e = st.pending[TX]
    st._mark_contested(BLK)
    done = asyncio.run(st._finalize_tx(_Pool(), TX, e, time.time()))
    assert done is True and calls == [], \
        "a proven two-hash height is never decided by one replica"
    assert st.deltas.get("s1.abstain.contested") == 1
    assert st.deltas.get("s1.emitted") is None


def test_parent_hash_conflict_is_reorg_evidence(st):
    """fleet r32 (major): a SUCCESSFUL resolution's response body can
    carry the only delivered reorg proof — parentHash contradicting a
    sibling's recorded hash at blk-1 — and it was discarded unread.
    The resolver now marks the height contested and voids every
    earned timestamp; its own resolution still returns (the caller's
    generation guard refuses the stale write-back)."""
    sib_tx = "0x" + "aa" * 32
    st.pending[sib_tx] = {"logs": [],
                          "blocks": {BLK - 1: "0x" + "77" * 32},
                          "ts": {BLK - 1: float(TS0)}, "evicted": False}
    st._client = _FakeClient(_Resp(200, {"result": {
        "timestamp": hex(TS0 + 2), "hash": "0x" + "99" * 32,
        "parentHash": "0x" + "88" * 32}}))
    ts = asyncio.run(st._resolve_block(BLK, "0x" + "99" * 32))
    assert ts == TS0 + 2, "the strict earn itself still resolves"
    assert st.deltas.get("s1.parent_hash_conflict") == 1
    assert (BLK - 1) in st.contested
    assert st.pending[sib_tx]["ts"] == {}, \
        "the contradicted sibling's earned ts is void"
    # agreement is not evidence: a parentHash matching the recorded
    # hash marks nothing
    st2_tx = "0x" + "ab" * 32
    st.contested.clear()
    st.deltas.pop("s1.parent_hash_conflict", None)
    st.pending[st2_tx] = {"logs": [],
                          "blocks": {BLK - 1: "0x" + "88" * 32},
                          "ts": {}, "evicted": False}
    st.pending.pop(sib_tx)
    ts = asyncio.run(st._resolve_block(BLK, "0x" + "99" * 32))
    assert ts == TS0 + 2
    assert st.deltas.get("s1.parent_hash_conflict") is None
    assert st.contested == {}


def test_contested_registry_is_bounded(st):
    for i in range(s1.CONTESTED_CAP + 50):
        st._mark_contested(1000 + i)
    assert len(st.contested) == s1.CONTESTED_CAP
    assert 1000 not in st.contested, "oldest heights fall off first"
    assert (1000 + s1.CONTESTED_CAP + 49) in st.contested


# ── fleet round 33 pins ─────────────────────────────────────────────
def test_contested_eviction_applies_the_verdict_first(st):
    """fleet r33 (major): flooding the registry past CONTESTED_CAP
    evicted a still-live contested mark, downgrading a PROVEN
    two-hash height back to re-earnable — a stale replica then
    emitted the orphaned side. Eviction now applies the verdict
    before forgetting it: entries still buffered at the evicted
    height die with the mark, counted, and the poller carries."""
    lst = _Listener(roster={MAKER: {"id": 7, "username": "mk"}})
    _wire(st, lst)
    st.observe(lst, MAKER_EV)                     # entry at BLK
    assert TX in st.pending
    st._mark_contested(BLK)                       # proven contested
    for i in range(s1.CONTESTED_CAP):             # flood higher marks
        st._mark_contested(BLK + 1 + i)
    assert BLK not in st.contested, "the flood evicted the mark"
    assert TX not in st.pending, \
        "the verdict was applied before the mark was forgotten"
    assert st.deltas.get("s1.abstain.contested") == 1, \
        "the abstention is visible, not silent"


def test_fat_entry_cannot_amplify_past_the_blocks_cap(st):
    """fleet r33 (major): one tx served frames at thousands of
    heights — the verdict seals at the SECOND height, but the blocks
    dict kept growing and became the flood amplifier. Past the cap
    the frame's evidence value (the purge) still banks; the frame
    itself is dropped, visibly."""
    lst = _Listener(roster={})
    st.observe(lst, MAKER_EV)
    e = st.pending[TX]
    for i in range(s1.BLOCKS_PER_TX_CAP + 10):
        st.observe(lst, dict(MAKER_EV, blockNumber=hex(BLK + 1 + i),
                             blockHash="0x" + "9a" * 32,
                             logIndex=hex(16 + i)))
    assert len(e["blocks"]) == s1.BLOCKS_PER_TX_CAP, \
        "the verdict needs two heights, never thousands"
    assert st.deltas.get("s1.frames_capped") == 11
    assert len(e["blocks"]) > 1, "the round-12 verdict stays sealed"


# ── fleet round 34 pins ─────────────────────────────────────────────
def test_evicted_verdict_becomes_the_floor(st, monkeypatch):
    """fleet r34 (major): PENDING_CAP overflow discarded the buffered
    entries BEFORE the round-33 eviction pop could apply the verdict,
    and a lone redelivery of the orphaned frame — no mark, no
    sibling, stale replica — armed-ingested. Forgetting a mark now
    WIDENS abstention: eviction raises a permanent floor and
    everything at or below it abstains at finalize, buffered now or
    redelivered later."""
    lst = _Listener(roster={MAKER: {"id": 7, "username": "mk"}})
    _wire(st, lst)
    _arm(st)
    st._mark_contested(BLK)                       # proven contested
    for i in range(s1.CONTESTED_CAP):             # flood evicts BLK
        st._mark_contested(BLK + 1 + i)
    assert BLK not in st.contested
    assert st.contested_floor >= BLK, \
        "eviction never forgets — it widens"
    # the redelivered lone frame builds a fresh entry at BLK
    calls = _capture_ingest(monkeypatch)
    _observe_all(st, lst, [MAKER_EV])
    e = st.pending[TX]
    done = asyncio.run(st._finalize_tx(_Pool(), TX, e, time.time()))
    assert done is True and calls == [], \
        "below the floor is forgotten contested ground — abstain"
    assert st.deltas.get("s1.abstain.contested_floor") == 1


def test_contested_state_survives_a_restart(st):
    """fleet r34: the registry was in-memory only — a boot between
    the conflict and the redelivery forgot the proof. The flush
    carries marks + floor; _load_state adopts them before anything
    can finalize; marks past the flush cap raise the PERSISTED floor
    (over-abstain, never forget)."""
    now = time.time()
    st._state_loaded = True
    st._mark_contested(BLK)
    st.contested_floor = 7
    pool = _Pool(stored=json.dumps({"counters": {}}))
    asyncio.run(st._flush(pool, now))
    written = json.loads(pool.writes[-1][1])
    assert written["contested"] == {str(BLK): st.contested[BLK]}
    assert written["contested_floor"] == 7

    st2 = S1Emitter()
    pool2 = _Pool(stored=json.dumps({
        "counters": {}, "contested": {str(BLK): now, "junk": now},
        "contested_floor": 12345}))
    asyncio.run(st2._load_state(pool2, now))
    assert BLK in st2.contested and st2.contested_floor == 12345, \
        "adopted before anything can finalize"

    # spill: marks beyond the flush cap raise the persisted floor
    st3 = S1Emitter()
    st3._state_loaded = True
    for i in range(s1.CONTESTED_FLUSH_CAP + 10):
        st3._mark_contested(1000 + i)
    pool3 = _Pool(stored=json.dumps({"counters": {}}))
    asyncio.run(st3._flush(pool3, now))
    w3 = json.loads(pool3.writes[-1][1])
    assert len(w3["contested"]) == s1.CONTESTED_FLUSH_CAP
    assert w3["contested_floor"] == 1009, \
        "what the flush cannot carry raises the persisted floor"


# ── fleet round 35 pins ─────────────────────────────────────────────
def test_flush_adopts_a_sibling_processes_floor(st):
    """fleet r35 (major x2): SQL_WRITE's top-level || let a process
    that never saw the flood clobber the persisted floor to 0, and a
    later boot re-emitted a proven-contested height. The disk copy is
    now folded server-side (GREATEST/union — executed semantics in
    test_s1_sql_real_pg); this pin covers the local half: every flush
    READ adopts the stored floor and marks, so this process's own
    finalize gate learns a sibling's verdict within one cycle."""
    now = time.time()
    st._state_loaded = True
    pool = _Pool(stored=json.dumps({
        "counters": {}, "contested_floor": 999,
        "contested": {"888": 1.0, "junk": 2.0}}))
    asyncio.run(st._flush(pool, now))
    assert st.contested_floor == 999, "the stored floor is adopted"
    assert 888 in st.contested and len(st.contested) == 1, \
        "stored marks are adopted; junk keys are not"
    assert "GREATEST" in s1.SQL_WRITE and \
        "- 'contested' - 'contested_floor'" in s1.SQL_WRITE, \
        "the disk fold is server-side, never a payload overwrite"


# ── fleet round 37 pins ─────────────────────────────────────────────
def test_own_entry_flap_marks_the_height_contested(st):
    """fleet r37 (major): an A→B→A flap delivered two hashes at one
    height THROUGH ONE ENTRY — the sibling scan is gated sib_tx !=
    tx, the same-height re-mine branch did purge-and-RE-EARN with no
    contested mark, and a lagging replica re-verified the re-flipped
    orphaned belief into an armed ingest. Two hashes at one height
    are contested whichever entry delivered them."""
    lst = _Listener(roster={MAKER: {"id": 7, "username": "mk"}})
    _wire(st, lst)
    st.observe(lst, MAKER_EV)                     # (BLK, 77…) — A
    st.observe(lst, dict(MAKER_EV, blockHash="0x" + "88" * 32,
                         logIndex="0x9"))         # re-mine — B
    assert BLK in st.contested, \
        "the second hash at the height marks it, sibling or not"
    st.observe(lst, dict(MAKER_EV, blockHash="0x" + "77" * 32,
                         logIndex="0xa"))         # the flap back — A
    e = st.pending[TX]
    done = asyncio.run(st._finalize_tx(_Pool(), TX, e, time.time()))
    assert done is True
    assert st.deltas.get("s1.abstain.contested") == 1, \
        "no single replica arbitrates a proven two-hash height"


# ── fleet round 39 pins ─────────────────────────────────────────────
def test_removed_tx_replay_never_rebuffers(st):
    """fleet r39 (CRITICAL): the removed-notice pop forgot the
    verdict — a lagging WS backend redelivered the orphaned frame
    after the pop, it re-buffered into a fresh entry with no mark and
    no sibling, and a stale replica re-earned it into an armed
    key-divergent ingest. A removed notice is a PROVEN orphan verdict
    for the tx it names: the redelivered frame refuses to re-buffer,
    and the parseable height is contested ground (durable via the
    round-34 floor)."""
    lst = _Listener(roster={MAKER: {"id": 7, "username": "mk"}})
    _wire(st, lst)
    st.observe(lst, MAKER_EV)
    assert TX in st.pending
    st.observe(lst, dict(MAKER_EV, removed=True))
    assert TX not in st.pending
    assert BLK in st.contested, \
        "the removed height is contested ground, durably"
    # the lagging shard replays the orphaned frame
    st.observe(lst, MAKER_EV)
    assert TX not in st.pending, "a proven orphan never re-buffers"
    assert st.deltas.get("s1.abstain.removed_replay") == 1
    assert len(st.removed_txs) == 1


def test_removed_tx_registry_is_bounded(st):
    lst = _Listener(roster={})
    for i in range(s1.REMOVED_TX_CAP + 10):
        st.observe(lst, dict(MAKER_EV, removed=True,
                             transactionHash="0x%064x" % i))
    assert len(st.removed_txs) == s1.REMOVED_TX_CAP


# ── fleet round 42 pins (design round: the suspect lifecycle) ───────
def test_first_qualification_records_suspect_not_a_trip(st):
    """r42 P4: a row that satisfies the full composed coverage rule
    for the FIRST time records a durable, counted, NON-disarming
    suspect — zero trips, zero SQL_TRIP writes, no judged stamp."""
    _arm(st)
    pool = _SweepPool([_srow(3, False, suspect_at=None)])
    asyncio.run(st._corroboration_sweep(pool))
    assert st.trips == {} and pool.trip_writes == [], \
        "detection is not arrest"
    assert st.armed is True, "a suspect never disarms"
    assert getattr(pool, "suspected", []) == [[3]], \
        "the suspicion is stamped durably on the row"
    assert st.deltas.get("s1.suspect") == 1
    assert pool.marked == [], "no judged stamp at suspicion time"


def test_suspicion_surviving_the_hold_trips(st):
    """r42 P6: past SUSPECT_HOLD_S with a fresh covering run and a
    live index, the sticky trip and judged stamp land exactly as the
    pre-r42 machinery pinned them (the _srow default is pre-held)."""
    _arm(st)
    pool = _SweepPool([_srow(3, False)])
    asyncio.run(st._corroboration_sweep(pool))
    assert "uncorroborated:3" in st.trips and st.armed is False
    assert pool.marked == [[3]]


def test_index_not_live_defers_forever(st):
    """r42 P8: SQL_INDEX_LIVE false holds the verdict indefinitely —
    deferral defers, it never alarms."""
    _arm(st)
    pool = _SweepPool([_srow(3, False)])
    pool.index_live = False
    asyncio.run(st._corroboration_sweep(pool))
    assert st.trips == {} and st.armed is True
    assert pool.marked == []


def test_multi_wallet_suspect_burst_bypasses_the_hold(st):
    """r42 P7: >= SUSPECT_BURST_WALLETS distinct wallets holding live
    suspects is the wallet-agnostic signature of a wrong DECODE — the
    arrest path pays zero added latency (index-live not consulted)."""
    _arm(st)
    pool = _SweepPool([_srow(3, False,
                             suspect_at=float(TS0) - 60)])  # young
    pool.suspect_wallets = s1.SUSPECT_BURST_WALLETS
    pool.index_live = False          # burst must not consult it
    asyncio.run(st._corroboration_sweep(pool))
    assert "uncorroborated:3" in st.trips, \
        "systematic wrongness alarms at today's latency"


def test_suspicion_healed_inside_the_hold_never_trips(st):
    """r42 P5 — the pin that encodes the design decision: the venue
    stamps the row mid-hold; the next sweep confirms via the ok path
    and trips stays empty, armed never forced false."""
    _arm(st)
    pool = _SweepPool([_srow(3, True)])   # venue stamped mid-hold
    asyncio.run(st._corroboration_sweep(pool))
    assert st.trips == {} and st.armed is True
    assert pool.marked == [[3]], "the row confirms, judged never runs"
    assert st.deltas.get("s1.confirmed") == 1


def test_key_divergent_twin_is_an_immediate_named_trip(st):
    """r42 (prove-first R2): the venue's own row for the identical
    (whale, tx, asset) under a DIFFERENT dedupe key is a key-fidelity
    failure with a duplicate already in the executor's table — an
    immediate sticky under its honest name, no hold, because
    venue_seen_at can never stamp across a key mismatch."""
    _arm(st)
    pool = _SweepPool([_srow(3, False, suspect_at=None)])
    pool.key_twin = {"id": 99, "dedupe_key": "other"}
    asyncio.run(st._corroboration_sweep(pool))
    assert "key_divergent:3" in st.trips and st.armed is False
    assert st.deltas.get("s1.key_divergent") == 1
    assert getattr(pool, "suspected", []) == [], \
        "a twin skips the suspect lifecycle entirely"


# ── fleet round 43 pins ─────────────────────────────────────────────
def test_burst_census_is_contemporaneous_and_roster_gated():
    """r43 (major x2): zombie suspects on banned/departed whales —
    whose rows the sweep can never re-judge — armed the bypass
    FOREVER, converting the next single-wallet suspect into an
    instant false sticky trip. The census now joins the live roster
    and bounds suspect age to the burst window."""
    assert "JOIN whales w" in s1.SQL_SUSPECT_WALLETS
    assert "w.active AND NOT w.banned" in s1.SQL_SUSPECT_WALLETS
    assert "s1_suspect_at > now() - make_interval" in \
        s1.SQL_SUSPECT_WALLETS
    assert s1.SUSPECT_BURST_WINDOW_S <= 12 * 3600, \
        "burst evidence is contemporaneous, never debris"


def test_hold_recheck_floor_is_explicit_not_inherited(st):
    """r43 (major): the r42 indexing-time floor rode $1/$4 and the
    hold recheck inherited newest >= suspect_at — a phantom whose
    wallet went quiet after qualification could never re-cover and
    the promised alarm was silenced forever. The floor is an explicit
    $6 now; the hold recheck passes the fill's own ts."""
    assert ">= $6" in s1.SQL_RECON_SINCE
    assert "extract(epoch from ($1" not in s1.SQL_RECON_SINCE
    import inspect

    src = inspect.getsource(s1.S1Emitter._sweep_wallet)
    assert "det_epoch + RECON_VENUE_LAG_S" in src, \
        "the primary call carries the detection+lag floor"
    assert "ts_epoch, ts_epoch, timeout=6" in src, \
        "the hold recheck demands only fill-inside-span"


def test_key_twin_requires_identical_economics(st):
    """r43 (major): the twin test fired on the design's OWN r13/r14
    same-tx same-asset second leg (S1 emits leg 1, the poller carries
    leg 2 at a different price/size — BOTH correct). The R2 class is
    an IDENTICAL fill under a ts-shifted key, so the twin must match
    size and price exactly."""
    assert "v.size = s.size" in s1.SQL_KEY_TWIN
    assert "v.price = s.price" in s1.SQL_KEY_TWIN


def test_key_divergent_heals_when_the_venue_stamps(st):
    """r43 (major): the trip premise ('venue_seen_at can never stamp
    across a key mismatch') was falsified executed — the straggling
    identical-key venue row lands later and stamps the s1 row. The
    healer now releases key_divergent exactly like uncorroborated."""
    _arm(st)
    st.trips["key_divergent:3"] = float(TS0)
    st.armed = False

    class _HealPool2(_SweepPool):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.cleared: list[str] = []
            self.recheck_ok = True

        async def fetchrow(self, sql, *a, timeout=None):
            if "trips_cleared" in sql and "prev.admit" not in sql and \
                    sql.lstrip().startswith("WITH prev"):
                self.cleared.append(a[1])
                return {"removed": True, "trips": {}, "cleared": {}}
            return await _SweepPool.fetchrow(self, sql, *a,
                                             timeout=timeout)

    pool = _HealPool2([], wallets=[])
    asyncio.run(st._corroboration_sweep(pool))
    assert pool.cleared == ["key_divergent:3"], \
        "a venue-stamped row refutes the key-divergent trip too"
    assert st.trips == {}


def test_key_divergent_counter_counts_stamp_winners_only(st):
    """r43 (minor): the kd counter bumped per judgment PASS — a
    transient stamp error re-judged the row and durably double-
    counted one twin. It now counts stamp winners, the round-8 law."""
    _arm(st)
    pool = _SweepPool([_srow(3, False, suspect_at=None)])
    pool.key_twin = {"id": 99, "dedupe_key": "other"}
    asyncio.run(st._corroboration_sweep(pool))
    assert st.deltas.get("s1.key_divergent") == 1
    assert st.deltas.get("s1.uncorroborated") is None, \
        "a kd row never double-counts as plain uncorroborated"


# ── fleet round 44 pins ─────────────────────────────────────────────
def test_kd_race_lost_self_clear_releases_its_own_reason(st):
    """r44 (minor x3): the round-16 race-lost branch hardcoded
    'uncorroborated:' while a key-divergent row's persisted trip was
    'key_divergent:<id>' — the self-clear popped a reason that never
    tripped and the refuted kd trip survived the sweep that proved it
    false. The release names the row's own reason."""
    _arm(st)

    class _KdRacePool(_SweepPool):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.rechecks = 0
            self.cleared: list[str] = []

        async def fetchrow(self, sql, *a, timeout=None):
            if "trips_cleared" in sql and "prev.admit" not in sql and \
                    sql.lstrip().startswith("WITH prev"):
                self.cleared.append(a[1])
                return {"removed": True, "trips": {}, "cleared": {}}
            if "FROM trades WHERE id" in sql:
                self.rechecks += 1
                return {"ok": self.rechecks > 1}
            return await _SweepPool.fetchrow(self, sql, *a,
                                             timeout=timeout)

        async def fetch(self, sql, *a, timeout=None):
            if "venue_seen_at IS NULL RETURNING" in sql:
                self.marked.append(list(a[0]))
                return []            # the conditional stamp refused
            return await _SweepPool.fetch(self, sql, *a,
                                          timeout=timeout)

    pool = _KdRacePool([_srow(3, False, suspect_at=None)])
    pool.key_twin = {"id": 99, "dedupe_key": "other"}
    asyncio.run(st._corroboration_sweep(pool))
    assert pool.cleared == ["key_divergent:3"], \
        "the race-lost release names the reason that actually tripped"
    assert st.trips == {}, "released in-memory in the SAME sweep"


# ── fleet round 47 pins ─────────────────────────────────────────────
def test_multi_height_verdict_is_durable_contested_ground(st, monkeypatch):
    """r47 (major): the two-height reorg verdict died with the run
    loop's pop — a redelivered orphaned frame re-buffered freely and
    a stale replica re-earned it into an armed orphaned ingest. Both
    heights are contested the moment the second is seen (the
    round-15 law), durable via the r34 floor, exactly like the
    same-height/sibling/removed channels."""
    lst = _Listener(roster={MAKER: {"id": 7, "username": "mk"}})
    _wire(st, lst)
    _arm(st)
    calls = _capture_ingest(monkeypatch)
    st.observe(lst, MAKER_EV)                       # height BLK
    st.observe(lst, dict(MAKER_EV, blockNumber=hex(BLK + 2),
                         blockHash="0x" + "88" * 32, logIndex="0x9"))
    assert BLK in st.contested and (BLK + 2) in st.contested, \
        "the verdict is recorded at evidence time"
    e = st.pending[TX]
    done = asyncio.run(st._finalize_tx(_Pool(), TX, e, time.time()))
    assert done is True and calls == []
    st.pending.pop(TX, None)                        # the run-loop pop
    # the lagging shard redelivers the orphaned frame — fresh entry,
    # but its height is contested ground
    _observe_all(st, lst, [MAKER_EV])
    e2 = st.pending[TX]
    done = asyncio.run(st._finalize_tx(_Pool(), TX, e2, time.time()))
    assert done is True and calls == [], \
        "the redelivered orphan abstains — no replica arbitrates"
    assert st.deltas.get("s1.abstain.contested", 0) >= 1
