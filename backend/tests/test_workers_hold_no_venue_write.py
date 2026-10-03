"""cand21: the workers service holds no venue write.

WHAT WAS FOUND (2026-10-03, read-only, from production). sportsassets-workers
ran 47086de with the FUNDED retail key, LIVE_TRADING_ENABLED=1,
LIVE_COPY_HALT=0 and PMUS_MIRROR=on (mirror_live's heartbeat: mode "exits").
copy_sweep ran maybe_execute over 150 rows per sweep and was refused
`sleeve_paused`; the ingestion loops hand every fresh fill to execute_copy;
mirror_live read an order on the funded account every 30 s with its cancel
path open. The ONE thing between a whale fill and a funded order was the
database row live_trading_paused = true.

WHAT IS PINNED HERE, layer by layer, each layer tested ALONE:

  1. registration -- the four venue-write loops are registered but never
     started, and main() locks the process BEFORE it binds the gate, writes
     the boot marker or starts a loop;
  2. the gate -- a locked process is refused submit, close and the CLOB path
     even on a snapshot where every switch says yes, and refused CANCEL,
     while an unlocked process (the API) keeps cancellation ungated;
  3. the adapters -- pmus.cancel_order refuses before a client exists,
     pmus.submit_fok raises before a client exists, pmx refuses its order
     endpoints before a token is minted, and the retail transport refuses
     every non-read request whatever called it;
  4. ingestion -- a fresh fill in a locked process is stored and measured and
     handed to NOTHING in the copy lane;
  5. the static surface -- from every loop the workers start, the only
     modules that reach a venue write are live_executor, pmus and the
     pipeline's guarded hand-off; pmx, execmirror, kalshi_orders and the
     calibration executor are not reachable at all.
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import pathlib
import sys
import time
import types

import pytest

# workers/all.py pulls in the notification stack (the same stub
# test_workers_boot_stagger and test_retention use)
sys.modules.setdefault("pywebpush", types.SimpleNamespace(
    webpush=None, WebPushException=Exception))

from sportsassets import execution_gate as gate  # noqa: E402
from sportsassets import pmus  # noqa: E402
from sportsassets import venue_request_gate as vrg  # noqa: E402
from sportsassets.ingestion import pipeline  # noqa: E402
from sportsassets.workers import all as all_mod  # noqa: E402

BACKEND = pathlib.Path(__file__).resolve().parents[1]
PKG = BACKEND / "sportsassets"

WRITE_LOOPS = {"copy_sweep", "underdog", "whale_exits", "mirror_live"}


@pytest.fixture
def locked():
    gate.lock_process("test: workers lock")
    yield
    gate._unlock_process_for_tests()


def _yes_snapshot():
    """Every switch says yes: the lock must refuse on its own."""
    gate._install_snapshot_for_tests(gate.Snapshot(
        paused=False, venue="polymarket-us", copy_halted=False,
        loss_stop=False, overspend=False, read_at=time.time(), ok=True,
        why="test: everything authorized"))


# ── 1. registration ──────────────────────────────────────────────────

def test_the_four_write_loops_are_registered_and_named_exactly():
    names = [n for n, _fn in all_mod.LOOPS]
    assert set(all_mod.VENUE_WRITE_LOOPS) == WRITE_LOOPS
    assert WRITE_LOOPS <= set(names), "a filtered name must be a real loop"


def test_startable_loops_drops_exactly_the_write_loops_and_keeps_order():
    started = all_mod.startable_loops()
    assert [n for n, _ in started] == [n for n, _ in all_mod.LOOPS
                                       if n not in WRITE_LOOPS]
    assert started[0][0] == "poller" and started[-1][0] == "memory"
    assert len(started) == len(all_mod.LOOPS) - 4


def test_main_locks_before_it_binds_marks_or_starts_anything(monkeypatch):
    seen: list = []

    async def _bind():
        seen.append(("bind", gate.process_lock()))

    async def _boot():
        seen.append(("boot", gate.process_lock()))

    async def _supervise(name, factory, *, boot_delay=0.0):
        seen.append(("loop:" + name, gate.process_lock()))

    monkeypatch.setattr(all_mod, "_bind_execution_gate", _bind)
    monkeypatch.setattr(all_mod, "_record_boot", _boot)
    monkeypatch.setattr(all_mod, "supervise", _supervise)
    assert gate.process_lock() is None
    asyncio.run(all_mod.main())

    assert seen[0][0] == "bind"
    assert all(why == all_mod.PROCESS_LOCK_REASON for _what, why in seen), seen
    started = {w[5:] for w, _ in seen if w.startswith("loop:")}
    assert not started & WRITE_LOOPS
    assert started == {n for n, _ in all_mod.startable_loops()}


def test_the_lock_is_in_main_before_the_gate_binding_in_the_source():
    """Read as text too (the integration suite's stance): the call order in
    main() is the control, so it is pinned where a reader sees it."""
    src = (PKG / "workers" / "all.py").read_text()
    body = src[src.index("async def main()"):]
    assert body.index("_lock_venue_writes()") < body.index("await _bind_execution_gate()")
    assert "enumerate(startable_loops())" in body
    assert "enumerate(LOOPS)" not in body


def test_the_boot_marker_reads_the_lock_back_and_carries_the_full_sha(monkeypatch):
    sha = "17156263358f8e32c97240be2858ca1b8ef8508b"
    monkeypatch.setenv("RENDER_GIT_COMMIT", sha)
    m = all_mod._boot_marker("2026-10-03T00:00:00+00:00")
    assert m["venue_writes"] == "NOT_LOCKED" and m["lock_reason"] is None
    all_mod._lock_venue_writes()
    m = all_mod._boot_marker("2026-10-03T00:00:00+00:00")
    assert m["commit"] == sha[:7] and m["commit_sha"] == sha
    assert m["venue_writes"] == "LOCKED"
    assert m["lock_reason"] == all_mod.PROCESS_LOCK_REASON
    assert m["not_started"] == sorted(WRITE_LOOPS)
    assert not set(m["started"]) & WRITE_LOOPS
    json.dumps(m)                                  # it is the jsonb row


# ── 2. the gate ──────────────────────────────────────────────────────

def test_a_locked_process_is_refused_every_submission_on_a_yes_snapshot(locked):
    _yes_snapshot()
    for op in ("submit", "close_position", "submit_clob"):
        for lane in ("copy", "manual", "mirror", "unknown"):
            with pytest.raises(gate.Denied) as e:
                gate.authorize(op, lane=lane, slug="s")
            assert e.value.reason == "process_locked"


def test_the_async_path_is_refused_too(locked):
    with pytest.raises(gate.Denied) as e:
        asyncio.run(gate.authorize_async("submit", lane="manual", slug="s"))
    assert e.value.reason == "process_locked"


def test_cancel_is_refused_when_locked_and_untouched_when_not():
    gate.authorize_cancel("cancel", slug="s")        # unlocked: a no-op
    gate.lock_process("test")
    try:
        with pytest.raises(gate.Denied) as e:
            gate.authorize_cancel("cancel", slug="s")
        assert e.value.reason == "process_locked"
        assert gate.describe()["process_lock"] == "test"
    finally:
        gate._unlock_process_for_tests()


def test_the_lock_is_one_way_and_keeps_its_first_reason(monkeypatch):
    gate.lock_process("first")
    gate.lock_process("second")
    assert gate.process_lock() == "first"
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    with pytest.raises(RuntimeError):
        gate._unlock_process_for_tests()
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    gate._unlock_process_for_tests()
    assert gate.process_lock() is None


def test_unlocked_the_yes_snapshot_still_authorizes():
    """The control: the lock, not the snapshot, is what refused above."""
    _yes_snapshot()
    snap = gate.authorize("submit", lane="manual", slug="s")
    assert snap.ok is True


# ── 3. the adapters ──────────────────────────────────────────────────

def _no_client(monkeypatch):
    def _boom():
        raise AssertionError("a venue client was built in a locked process")
    monkeypatch.setattr(pmus, "_get_client", _boom)


def test_pmus_cancel_refuses_before_a_client_exists(locked, monkeypatch):
    _no_client(monkeypatch)
    out = pmus.cancel_order("OID", "aec-x")
    assert out["ok"] is False and "process_locked" in out["error"]


def test_pmus_submit_raises_before_a_client_exists(locked, monkeypatch):
    _yes_snapshot()
    _no_client(monkeypatch)
    with pytest.raises(gate.Denied) as e:
        pmus.submit_fok("aec-x", 0.5, 1, intent="ORDER_INTENT_BUY_LONG")
    assert e.value.reason == "process_locked"


def test_pmus_close_position_raises_before_a_client_exists(locked, monkeypatch):
    _yes_snapshot()
    _no_client(monkeypatch)
    with pytest.raises(gate.Denied):
        pmus.close_position("aec-x", slippage_bips=300)


def test_pmx_order_endpoints_refuse_before_a_token(locked, monkeypatch):
    from sportsassets import pmx

    def _boom():
        raise AssertionError("a token was minted in a locked process")
    monkeypatch.setattr(pmx, "_headers", _boom)
    for path in ("/v1/trading/orders", "/v1/trading/orders/cancel",
                 "/v1/trading/orders/preview"):
        with pytest.raises(gate.Denied):
            pmx._request("POST", path, json_body={})
    assert pmx.cancel_order("OID", "aec-x")["ok"] is False


class _Inner:
    def __init__(self):
        self.sent = []

    def handle_request(self, request):
        self.sent.append(request.method)

        class _R:
            status_code = 200
        return _R()


@pytest.mark.skipif(vrg.PacedTransport is None, reason="httpx unavailable")
def test_the_transport_refuses_every_non_read_when_locked():
    import httpx
    inner = _Inner()
    t = vrg.PacedTransport(inner, pace=None)
    get = httpx.Request("GET", "https://api.example/v1/order/1")
    t.handle_request(get)
    gate.lock_process("test")
    try:
        before = vrg.totals()["refused_write_locked"]
        for path in ("/v1/orders", "/v1/order/1/cancel", "/v1/order/1/modify",
                     "/v1/orders/open/cancel", "/v1/order/close-position",
                     "/v1/order/preview"):
            with pytest.raises(gate.Denied):
                t.handle_request(httpx.Request("POST", "https://api.example" + path))
        with pytest.raises(gate.Denied):
            t.handle_request(httpx.Request("DELETE", "https://api.example/v1/x"))
        t.handle_request(get)                      # reads still go
        assert vrg.totals()["refused_write_locked"] == before + 7
    finally:
        gate._unlock_process_for_tests()
    t.handle_request(httpx.Request("POST", "https://api.example/v1/orders"))
    assert inner.sent == ["GET", "GET", "POST"], "unlocked, a write passes"


# ── 4. ingestion ─────────────────────────────────────────────────────

class _IngestPool:
    async def fetchrow(self, sql, *a):
        return {"id": 7, "was_insert": True}

    async def execute(self, sql, *a):
        return "UPDATE 1"


def _ingest_fresh(monkeypatch):
    executed, probed = [], []
    pool = _IngestPool()

    async def _get_pool():
        return pool

    async def _noop(*a, **kw):
        return None

    async def _execute(payload):
        executed.append(payload)

    async def _probe(payload):
        probed.append(payload)

    import sportsassets.copy_probe as cp
    import sportsassets.live_executor as le
    monkeypatch.setattr(pipeline, "get_pool", _get_pool)
    monkeypatch.setattr(pipeline, "publish", _noop)
    monkeypatch.setattr(pipeline, "_write_outbox", _noop)
    monkeypatch.setattr(pipeline, "_shadow_observe", _noop)
    monkeypatch.setattr(pipeline, "_mirror_wake", lambda *a: None)
    monkeypatch.setattr(le, "execute_copy", _execute)
    monkeypatch.setattr(cp, "probe_trade", _probe)
    ev = pipeline.TradeEvent(
        whale_id=1, whale_username="rn1", tx_hash="0xtx", asset="123",
        side="BUY", size=10.0, price=0.31, ts_epoch=int(time.time()),
        source="poll", condition_id="0xc", outcome="x", outcome_index=1)

    async def _go():
        out = await pipeline.ingest_trade_result(ev)
        await asyncio.sleep(0)                     # let spawned tasks run
        await asyncio.sleep(0)
        return out
    return asyncio.run(_go()), executed, probed


def test_a_locked_process_ingests_and_measures_a_fresh_fill_and_executes_nothing(locked, monkeypatch):
    before = pipeline.COPY_SKIPPED_LOCKED["n"]
    (tid, new), executed, probed = _ingest_fresh(monkeypatch)
    assert (tid, new) == (7, True)
    assert executed == [], "the copy lane was handed a fill in a locked process"
    assert len(probed) == 1, "measurement still runs"
    assert pipeline.COPY_SKIPPED_LOCKED["n"] == before + 1


def test_unlocked_the_same_fill_reaches_the_copy_lane(monkeypatch):
    """The control: the harness has teeth, and the API path is unchanged."""
    (_tid, _new), executed, _probed = _ingest_fresh(monkeypatch)
    assert len(executed) == 1


# ── 5. the static surface ────────────────────────────────────────────

_SINKS = {"submit_fok", "close_position", "cancel_order", "_submit_fok",
          "post_order", "cancel_all", "execute_copy", "maybe_execute",
          "mirror_exit", "cancel_manual_open", "_reap_stale_resting_bids",
          "_reap_stale_submitting"}


def _modules():
    out = {}
    for p in PKG.rglob("*.py"):
        if "tests" in p.parts:
            continue
        rel = p.relative_to(BACKEND).with_suffix("")
        name = ".".join(rel.parts)
        out[name[:-9] if name.endswith(".__init__") else name] = p
    return out


def _imports(mods, name, tree):
    is_pkg = mods[name].name == "__init__.py"
    found = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            found |= {a.name for a in n.names if a.name in mods}
        elif isinstance(n, ast.ImportFrom):
            if n.level:
                base = name.split(".") if is_pkg else name.split(".")[:-1]
                base = base[:len(base) - (n.level - 1)]
                pre = ".".join(base + ([n.module] if n.module else []))
            elif (n.module or "").startswith("sportsassets"):
                pre = n.module
            else:
                continue
            found |= {m for m in [pre] + [pre + "." + a.name for a in n.names]
                      if m in mods}
    return found


def _closure(mods, trees, start):
    seen, stack = set(), [start]
    while stack:
        x = stack.pop()
        if x in seen or x not in mods:
            continue
        seen.add(x)
        parts = x.split(".")
        stack += [".".join(parts[:i]) for i in range(1, len(parts))]
        stack += list(_imports(mods, x, trees[x]))
    return seen


def _sink_hits(tree):
    hits = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            nm = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
            if nm in _SINKS:
                hits.add(nm)
            for a in n.args:
                nm = getattr(a, "attr", None) or getattr(a, "id", None)
                if isinstance(a, (ast.Attribute, ast.Name)) and nm in _SINKS:
                    hits.add(nm)
    return hits


def test_from_every_started_loop_only_the_locked_layers_reach_a_venue_write():
    mods = _modules()
    trees = {m: ast.parse(p.read_text(encoding="utf-8")) for m, p in mods.items()}
    reach = set()
    for name, fn in all_mod.startable_loops():
        if name == "memory":                       # defined in all.py itself
            continue
        mod = fn.__module__
        assert mod in mods, (name, mod)
        reach |= _closure(mods, trees, mod)
    callers = {m: _sink_hits(trees[m]) for m in reach if _sink_hits(trees[m])}
    # every route from a started loop to a venue write passes through one
    # of these, and each is covered by a layer pinned above:
    #   live_executor   -> pmus.submit_fok / close_position / cancel_order
    #                      and the CLOB path: the gate's lock (layer 2)
    #   pmus            -> its own client: the gate and the transport (3)
    #   pipeline        -> execute_copy: not spawned when locked (layer 4)
    assert set(callers) <= {"sportsassets.live_executor", "sportsassets.pmus",
                            "sportsassets.ingestion.pipeline"}, callers
    assert callers.get("sportsassets.ingestion.pipeline", set()) <= {"execute_copy"}
    for unreachable in ("sportsassets.pmx", "sportsassets.execmirror",
                        "sportsassets.kalshi_orders",
                        "sportsassets.calibration_execute",
                        "sportsassets.workers.mirror_live",
                        "sportsassets.workers.copy_sweep",
                        "sportsassets.workers.underdog",
                        "sportsassets.workers.whale_exits"):
        assert unreachable not in reach, unreachable


def test_the_pipeline_spawns_execute_copy_only_on_the_unlocked_branch():
    src = (PKG / "ingestion" / "pipeline.py").read_text()
    i = src.index("if copy_execution_locked():")
    j = src.index("create_task(execute_copy(payload))")
    assert i < j and "else:" in src[i:j]
    assert src.count("create_task(execute_copy(") == 1
