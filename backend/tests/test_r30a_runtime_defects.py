"""R30A RUNTIME DEFECTS: the research tick names its failing step, and every
heartbeat writer serializes what JSON cannot carry.

PRODUCTION EVIDENCE (render-ops `logs`, 2026-10-04 15:47-18:47Z):
  * API: `agent research tick failed: TimeoutError` x17 (run 37225748641),
    logged WITHOUT a traceback, so which of the tick's bounded steps failed
    is not on record. The cause is INFERRED, not shown: the failures fall in
    the minutes of the API loop's recorded 2-3 s stalls and of the other two
    timeout classes (test_r30a_loop_stalls.py), and the steps' server-side
    statements are fast (pg_stat_statements, research-sql run 37231484481).
    The phase now rides the exception and is recorded in loop health, so
    the next failure names its step.
  * workers: `bettor_state: heartbeat write failed: TypeError: Object of type
    datetime is not JSON serializable` on EVERY bettor_state tick (~ every
    100 s) on the deployed 191b299. dea1b2e (in this branch, not yet deployed)
    fixed db.heartbeat; the guard below proves no OTHER heartbeat writer can
    fail the same way.
"""
from __future__ import annotations

import ast
import asyncio
import pathlib
import warnings
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

PKG = pathlib.Path(__file__).resolve().parents[1] / "sportsassets"


# ── the research tick names the step that failed ───────────────────────

def test_a_starved_claim_is_reported_as_the_claim_step(monkeypatch):
    from sportsassets.agents import capability_runtime as R
    from sportsassets.agents import capability_work as W

    @asynccontextmanager
    async def ok_conn():
        yield MagicMock()

    @asynccontextmanager
    async def starved():
        await asyncio.sleep(10)          # a pool with no free slot
        yield MagicMock()

    calls = {"n": 0}

    def acquire(*a, **kw):
        calls["n"] += 1
        # CONTROL and ADMIT get a connection; CLAIM waits on a dry pool
        return ok_conn() if calls["n"] <= 2 else starved()

    pool = MagicMock()
    pool.acquire = acquire
    monkeypatch.setattr(W, "schema", AsyncMock(return_value=True))
    monkeypatch.setattr(W, "control", AsyncMock(return_value={"enabled": True}))
    monkeypatch.setattr(R, "admit", AsyncMock(return_value=0))
    real_timeout = asyncio.timeout
    monkeypatch.setattr(R.asyncio, "timeout",
                        lambda s: real_timeout(min(s, 0.05)))

    async def main():
        with pytest.raises(R.TickPhaseFailed) as got:
            await R.tick(pool)
        return got.value
    err = asyncio.run(main())
    assert err.phase == "CLAIM"
    assert isinstance(err.cause, TimeoutError)


def test_the_run_loop_logs_the_phase(monkeypatch, caplog):
    from sportsassets.agents import capability_runtime as R

    async def failing(pool):
        raise R.TickPhaseFailed("HEARTBEAT", TimeoutError())

    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)
        raise asyncio.CancelledError

    monkeypatch.setattr(R, "tick", failing)
    monkeypatch.setattr(R.asyncio, "sleep", fake_sleep)

    async def get_pool():
        return object()

    with caplog.at_level("WARNING"):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(R.run(get_pool))
    assert "agent research tick failed in HEARTBEAT: TimeoutError" in caplog.text


# ── every heartbeat writer can carry a datetime ────────────────────────

def _heartbeat_writers():
    """(file, function) for every function whose name names a heartbeat /
    beat and that serializes with json.dumps."""
    out = []
    for path in PKG.rglob("*.py"):
        if "vendor" in path.parts:
            continue
        try:
            with warnings.catch_warnings():
                # a pre-existing '\\d' in a docstring elsewhere is not ours
                warnings.simplefilter("ignore", SyntaxWarning)
                tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            name = node.name.lower()
            if "heartbeat" not in name and not name.endswith("beat"):
                continue
            dumps = [c for c in ast.walk(node)
                     if isinstance(c, ast.Call)
                     and isinstance(c.func, ast.Attribute)
                     and c.func.attr == "dumps"]
            if dumps:
                out.append((path, node, dumps))
    return out


def test_every_heartbeat_writer_serializes_non_json_values():
    """A bare json.dumps in a heartbeat raised on the first datetime in the
    detail (bettor_state, every tick). Every heartbeat writer's dumps must
    carry a `default=` (db.heartbeat_json's, or default=str)."""
    writers = _heartbeat_writers()
    assert len(writers) >= 6, "the scan found too few writers to be real"
    bare = []
    for path, node, dumps in writers:
        for c in dumps:
            if not any(k.arg == "default" for k in c.keywords):
                bare.append("%s:%s %s" % (path.relative_to(PKG), c.lineno,
                                          node.name))
    assert not bare, "heartbeat writers with a bare json.dumps: %s" % bare


def test_service_heartbeats_has_one_writer():
    """Only db.heartbeat writes service_heartbeats, so its one serializer
    (db.heartbeat_json) covers every service beat."""
    writers = []
    for path in PKG.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "INSERT INTO service_heartbeats" in text:
            writers.append(path.relative_to(PKG).as_posix())
    assert writers == ["db.py"]


def test_db_heartbeat_carries_the_production_bettor_state_shape():
    from sportsassets import db as DB

    class _Con:
        def __init__(self):
            self.args = None

        async def execute(self, sql, *args, **kw):
            self.args = args

    con = _Con()
    stats = {"bucket": datetime(2026, 10, 4, 17, 0, tzinfo=UTC),
             "captured": 12, "refused": {"NO_BOOK": 3}}
    asyncio.run(DB.heartbeat("bettor_state", "ok", stats, con=con))
    assert '"bucket": "2026-10-04T17:00:00+00:00"' in con.args[2]


# ── R30A review: the heartbeat guard scans the WRITES, not function names ──

_SAFE_CALLS = {"str", "bool", "int", "float", "len", "round"}


_MODULE_CONSTANTS: set = set()


def _safe_value(v) -> bool:
    if isinstance(v, ast.Constant):
        return True
    if isinstance(v, ast.Name) and v.id in _MODULE_CONSTANTS:
        return True             # a module-level literal constant
    if isinstance(v, ast.Call) and isinstance(v.func, ast.Name) \
            and v.func.id in _SAFE_CALLS:
        return True
    if isinstance(v, (ast.Compare, ast.BoolOp)):
        return True
    if isinstance(v, ast.Subscript):
        return _safe_value(v.value)
    return False


def _safe_dumps(call) -> bool:
    """A json.dumps that cannot raise on a datetime: it carries default=,
    or it serialises a dict literal of constants / str() / bool() only."""
    if any(k.arg == "default" for k in call.keywords):
        return True
    a0 = call.args[0] if call.args else None
    return isinstance(a0, ast.Dict) and all(_safe_value(v)
                                            for v in a0.values)


def _is_dumps(node) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "dumps")


def _heartbeat_inserts():
    """(path, enclosing function, call, payload) for EVERY INSERT INTO
    ingestion_state whose key argument is heartbeat-shaped (a name or literal
    that says HEARTBEAT, or the servicing heartbeat's key), whatever the
    enclosing function is called. capability_runtime.tick's write was the
    one the function-name scan missed."""
    out = []
    for path in PKG.rglob("*.py"):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        funcs = [n for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for fn in funcs:
            for node in ast.walk(fn):
                if not isinstance(node, ast.Call) or len(node.args) < 3:
                    continue
                a0 = node.args[0]
                sql = a0.value if isinstance(a0, ast.Constant) else ""
                if not isinstance(sql, str) or \
                        "INSERT INTO ingestion_state" not in sql:
                    continue
                key = ast.unparse(node.args[1])
                if "HEARTBEAT" not in key.upper() and \
                        key not in ("SERVICING_KEY",):
                    continue
                out.append((path, fn, node, node.args[2], tree))
    return out


def _payload_is_safe(fn, payload, tree) -> bool:
    _MODULE_CONSTANTS.clear()
    _MODULE_CONSTANTS.update(
        t.id for n in tree.body if isinstance(n, ast.Assign)
        and isinstance(n.value, ast.Constant)
        for t in n.targets if isinstance(t, ast.Name))
    if _is_dumps(payload):
        return _safe_dumps(payload)
    if isinstance(payload, ast.Subscript):
        return False            # a slice of serialised JSON is not JSON
    if isinstance(payload, ast.Name):
        assigned = [n.value for n in ast.walk(fn) if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == payload.id
                            for t in n.targets)]
        return bool(assigned) and all(_is_dumps(v) and _safe_dumps(v)
                                      for v in assigned)
    if isinstance(payload, ast.Call) and isinstance(payload.func, ast.Name):
        helper = [n for n in ast.walk(tree)
                  if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                  and n.name == payload.func.id]
        if not helper:
            return False
        dumps = [c for c in ast.walk(helper[0]) if _is_dumps(c)]
        subs = [c for c in ast.walk(helper[0]) if isinstance(c, ast.Subscript)
                and _is_dumps(c.value)]
        return bool(dumps) and all(_safe_dumps(c) for c in dumps) \
            and not subs
    return False


def test_every_heartbeat_insert_serialises_non_json_values():
    """R30A REVIEW: the function-name scan missed capability_runtime.tick's
    ingestion_state heartbeat (a bare json.dumps). This scan finds every
    INSERT INTO ingestion_state with a heartbeat-shaped key and proves its
    payload is a json.dumps with default= (directly, through a local name,
    or through a helper) -- and never a SLICE of serialised JSON, which
    fails the jsonb cast (paper_runtime's [:60000], fixed here too)."""
    found = _heartbeat_inserts()
    names = {(p.relative_to(PKG).as_posix(), fn.name) for p, fn, *_ in found}
    for expected in (("agents/capability_runtime.py", "tick"),
                     ("agents/paper_runtime.py", "write_heartbeat"),
                     ("pinnapi_feed_runtime.py", "_write_heartbeat"),
                     ("workers/ext_pinnacle_loop.py", "_heartbeat"),
                     ("workers/ext_pinnacle_loop.py", "_servicing_heartbeat"),
                     ("workers/rn1x_model_loop.py", "_heartbeat")):
        assert expected in names, (expected, sorted(names))
    bad = ["%s:%s %s" % (p.relative_to(PKG), call.lineno, fn.name)
           for p, fn, call, payload, tree in found
           if not _payload_is_safe(fn, payload, tree)]
    assert not bad, "heartbeat inserts that can fail on a value: %s" % bad


def test_the_paper_heartbeat_is_bounded_on_content_not_cut():
    import json as _json

    from sportsassets.agents import paper_runtime as PR
    big = {"ran": True, "refusal": None, "why": None,
           "pass": {"rows": ["x" * 100] * 2000}}
    body = PR._heartbeat_body(big)
    got = _json.loads(body)                     # always valid JSON
    assert len(body) <= PR.HEARTBEAT_MAX_CHARS
    small = _json.loads(PR._heartbeat_body({"ran": True, "refusal": "R"}))
    assert small["refusal"] == "R"
    if len(_json.dumps(PR._digest(big), default=str)) > \
            PR.HEARTBEAT_MAX_CHARS:
        assert got["heartbeat_truncated"] is True


def test_a_failed_tick_is_recorded_in_loop_health(monkeypatch):
    from sportsassets import loop_health as LH
    from sportsassets.agents import capability_runtime as R

    async def failing(pool):
        raise R.TickPhaseFailed("CONTROL", TimeoutError())

    recorded = []

    async def record(target, name, **kw):
        recorded.append((name, kw.get("phase"), kw.get("error")))
        return True

    async def fake_sleep(s):
        raise asyncio.CancelledError

    monkeypatch.setattr(R, "tick", failing)
    monkeypatch.setattr(LH, "record", record)
    monkeypatch.setattr(R.asyncio, "sleep", fake_sleep)

    async def get_pool():
        return object()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(R.run(get_pool))
    assert recorded == [("agents.capability_runtime", LH.ERROR,
                         "CONTROL: TimeoutError")]


# ── R30A: the gamma paginator stops at the offset the API serves ───────

class _Resp:
    def __init__(self, status, body):
        import json as _json
        self.status_code = status
        self.text = body if isinstance(body, str) else _json.dumps(body)
        self._body = body
        self.request = None

    def json(self):
        return self._body


class _GammaHttp:
    """Serves `n` open markets in pages and 422s past `max_offset`, with
    the production body (render-ops logs, workers, 2026-10-04 20:02:11Z)."""

    def __init__(self, n, max_offset=2000):
        self.n, self.max_offset, self.calls = n, max_offset, []

    async def get(self, path, params=None):
        p = dict(params or {})
        self.calls.append(p)
        off, lim = int(p.get("offset", 0)), int(p.get("limit", 100))
        if off > self.max_offset:
            return _Resp(422, {"type": "validation error",
                               "error": "offset too large, use "
                                        "/markets/keyset for deeper "
                                        "pagination"})
        return _Resp(200, [{"id": i} for i in range(off, min(off + lim,
                                                             self.n))])

    async def aclose(self):
        return None


def _client(http):
    from sportsassets import gamma as G
    c = G.GammaClient.__new__(G.GammaClient)
    c._http, c._open_params, c.last_paging = http, None, {}
    return c


def test_gamma_paging_never_asks_past_the_offset_ceiling():
    """PRODUCTION: offset 2100 -> 422 on every metadata cycle, a WARNING and
    a param re-probe each time. Now the paginator stops at offset 2000; a
    full last page there is a NAMED truncation, not a refused request."""
    from sportsassets import gamma as G
    http = _GammaHttp(n=5000)
    c = _client(http)
    got = asyncio.run(c.fetch_active_sports_markets())
    offsets = [p["offset"] for p in http.calls if p.get("limit") == 100]
    assert max(offsets) == G.GAMMA_MAX_OFFSET == 2000
    assert len(got) == 2100
    assert c.last_paging == {"pages": 21, "markets": 2100,
                             "stopped": "OFFSET_CEILING", "max_offset": 2000,
                             "truncated": True}
    assert c._open_params is not None, "no needless param re-probe"


def test_gamma_paging_a_short_catalogue_is_complete():
    http = _GammaHttp(n=350)
    c = _client(http)
    got = asyncio.run(c.fetch_active_sports_markets())
    assert len(got) == 350
    assert c.last_paging["stopped"] == "SHORT_PAGE"
    assert c.last_paging["truncated"] is False


def test_gamma_paging_a_lower_ceiling_is_end_of_pages_not_a_variant_failure():
    """If the API lowers its ceiling below ours, its 422 is the end of the
    pages it serves: counted, named, the markets kept, the param variant
    NOT reset (it was accepted)."""
    http = _GammaHttp(n=5000, max_offset=1000)
    c = _client(http)
    got = asyncio.run(c.fetch_active_sports_markets())
    assert len(got) == 1100
    assert c.last_paging["stopped"] == "OFFSET_REFUSED_BY_API"
    assert c.last_paging["truncated"] is True
    assert c._open_params is not None


def test_the_metadata_heartbeat_carries_the_paging_outcome():
    import inspect

    from sportsassets.workers import metadata_refresher as M
    src = inspect.getsource(M.main)
    assert 'detail["gamma_paging"] = dict(client.last_paging)' in src
