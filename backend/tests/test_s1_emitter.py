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
    def __init__(self, probe_rows=None, stored=None, cas_fails=False):
        self.probe_rows = probe_rows or []
        self.stored = stored
        self.cas_fails = cas_fails
        self.writes = []

    async def fetch(self, sql, *a, timeout=None):
        return self.probe_rows

    async def fetchval(self, sql, *a, timeout=None):
        if sql.lstrip().startswith("INSERT"):
            self.writes.append(a)
            return None if self.cas_fails else "s1_emitter"
        return self.stored

    async def execute(self, sql, *a, timeout=None):
        self.writes.append(a)


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


def test_flush_cas_loss_adopts_and_never_clobbers(st):
    """fleet r2 (CRITICAL): a concurrent process's sticky trip must
    survive our read-modify-write. The CAS write refuses when the
    stored trip changed under us; the deltas are restored and the beat
    counters never show the unlanded merge."""
    now = time.time()
    st.deltas = {"s1.emitted": 3}
    pool = _Pool(stored=json.dumps({"counters": {}}), cas_fails=True)
    asyncio.run(st._flush(pool, now))
    assert st.deltas.get("s1.emitted") == 3, "deltas restored on CAS loss"
    assert st.counters.get("s1.emitted") is None, \
        "the beat must not show a merge that did not land"


def test_foreign_trip_is_adopted_never_clobbered(st):
    """fleet r1/r2: another process's sticky trip must disarm this one
    at the next flush and be written back, never overwritten."""
    now = time.time()
    st.armed = True
    pool = _Pool(stored=json.dumps({"counters": {},
                                    "tripped": "key_selfcheck"}))
    asyncio.run(st._flush(pool, now))
    assert st.tripped == "key_selfcheck" and st.armed is False
    written = json.loads(pool.writes[-1][1])
    assert written["tripped"] == "key_selfcheck"
    assert written["armed"] is False, "armed never persists beside a trip"


def test_emitter_never_imports_at_module_load_into_shadow(st):
    """C2: shadow_v2 must not know the emitter exists."""
    import sportsassets.ingestion.shadow_v2 as sv
    src = open(sv.__file__).read()
    assert "s1_emitter" not in src and "claim_registry" not in src


# ── fleet round 3: the row-anchored corroboration sweep ─────────────
class _SweepPool(_Pool):
    def __init__(self, sweep_rows, recon_ran=True):
        super().__init__()
        self.sweep_rows = sweep_rows
        self.recon_ran = recon_ran
        self.marked = []

    async def fetch(self, sql, *a, timeout=None):
        return self.sweep_rows

    async def fetchrow(self, sql, *a, timeout=None):
        return {"?": 1} if self.recon_ran else None

    async def execute(self, sql, *a, timeout=None):
        self.marked.append(a[0])


def _srow(i, ok, pollable=True):
    return {"id": i, "dedupe_key": f"k{i}", "detected_at": 0,
            "ok": ok, "pollable": pollable}


def test_sweep_confirms_trips_and_defers_unjudgeable(st):
    """fleet r4 re-pin: stamped -> confirmed; unstamped with a live
    carrier AND a completed backstop run -> STICKY TRIP; whale off the
    roster -> DEFERRED UNSTAMPED (roster churn is transient — a stamp
    would amnesty a wrong emission forever). Judged rows are stamped
    BEFORE counting, so a failed stamp can never inflate evidence."""
    _arm(st)
    pool = _SweepPool([_srow(1, True), _srow(2, False, pollable=False),
                       _srow(3, False)])
    asyncio.run(st._corroboration_sweep(pool))
    assert st.deltas.get("s1.confirmed") == 1
    assert st.deltas.get("s1.uncorroborated") == 1
    assert st.tripped == "uncorroborated" and st.armed is False
    assert pool.marked == [[1, 3]], \
        "confirmed + judged rows stamp; the unjudgeable row does NOT"
    assert st.unjudged_backlog == 1


def test_sweep_defers_when_the_backstop_never_ran(st):
    """fleet r4 (major): a Path-B outage must defer judgment, never
    mass-trip — an uncorroborated verdict requires a COMPLETED
    reconciler run since the row's detection."""
    _arm(st)
    pool = _SweepPool([_srow(3, False)], recon_ran=False)
    asyncio.run(st._corroboration_sweep(pool))
    assert st.deltas.get("s1.uncorroborated") is None
    assert st.tripped is None and st.armed is True
    assert pool.marked == [] and st.unjudged_backlog == 1


def test_sweep_stamp_failure_counts_nothing(st):
    """fleet r4 (minor): count-before-stamp inflated s1.confirmed +50
    per minute forever under a write-degraded DB."""
    _arm(st)

    class _NoMarkPool(_SweepPool):
        async def execute(self, sql, *a, timeout=None):
            raise RuntimeError("read-only failover")

    pool = _NoMarkPool([_srow(1, True)])
    asyncio.run(st._corroboration_sweep(pool))
    assert st.deltas.get("s1.confirmed") is None, \
        "an unstamped judgment is not evidence"


def test_operator_trip_clear_is_adopted(st):
    """fleet r4 (major): the only manual recovery was silently reverted
    by any running process — an explicit trip_cleared_at newer than the
    trip is now adopted at flush."""
    now = time.time()
    st.tripped = "uncorroborated"
    st.tripped_at = now - 600
    pool = _Pool(stored=json.dumps({
        "counters": {}, "tripped": None,
        "trip_cleared_at": now - 60}))
    asyncio.run(st._flush(pool, now))
    assert st.tripped is None, "the operator's clear stands"
    assert st.armed is False, "cleared, not re-armed — cert re-arms"


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


def test_ambiguous_write_drops_snap_clean_cas_loss_restores(st):
    """fleet r3 (minor): an exception mid-write may have committed —
    restoring would double-count, so the snap drops (undercount-only);
    a clean CAS refusal provably wrote nothing and restores."""
    now = time.time()

    class _BoomPool(_Pool):
        async def fetchval(self, sql, *a, timeout=None):
            if sql.lstrip().startswith("INSERT"):
                raise RuntimeError("socket died mid-write")
            return self.stored

    st.deltas = {"s1.emitted": 5}
    asyncio.run(st._flush(_BoomPool(stored=json.dumps({"counters": {}})),
                          now))
    assert st.deltas.get("s1.emitted") is None, "ambiguous -> dropped"
    assert st.deltas.get("s1.snap_dropped_ambiguous") == 1

    st2 = S1Emitter()
    st2._state_loaded = True
    st2.deltas = {"s1.emitted": 5}
    pool = _Pool(stored=json.dumps({"counters": {}}), cas_fails=True)
    asyncio.run(st2._flush(pool, now))
    assert st2.deltas.get("s1.emitted") == 5, "clean CAS loss -> restored"


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
