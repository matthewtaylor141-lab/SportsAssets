"""THE DEDICATED MARKET PLANE IS ORDERLESS BY STRUCTURE (closeout 2026-10-08).

Kalshi documents no read-only API key class, so the existing (account-wide)
Kalshi key may authenticate the WebSocket order-book feed only in a process
that CANNOT place an order: market_plane_guard locks the process against
venue writes, blocks every order-client module from import, and refuses to
run beside an order-capable credential. Kalshi's mechanism (WebSocket
primary or REST fallback) is derived from records, never assumed. Every
guard installation runs in a SUBPROCESS: it mutates sys.meta_path and the
process lock, which must not leak into the rest of the suite."""
from __future__ import annotations

import ast
import asyncio
import json
import os
import pathlib
import subprocess
import sys

import pytest

from sportsassets import kalshi_ws as KWS
from sportsassets import market_plane_guard as G

BACKEND = pathlib.Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
SPEC = ROOT / "ops" / "render_market_plane_provision.json"
YAML = ROOT / "ops" / "render_market_plane_service.yaml"


def _py(code: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items()
           if k not in G.FORBIDDEN_ENV and not k.startswith("KALSHI")}
    env.update(env_extra or {})
    env["PYTHONPATH"] = str(BACKEND)
    return subprocess.run([sys.executable, "-c", code], cwd=str(BACKEND),
                          env=env, capture_output=True, text=True,
                          timeout=120)


# ── the guard ─────────────────────────────────────────────────────────

def test_every_blocked_module_exists_and_holds_an_order_client():
    for m in G.ORDER_MODULES:
        rel = m.replace("sportsassets.", "").replace(".", "/") + ".py"
        assert (BACKEND / "sportsassets" / rel).exists(), m
    # the venue order clients themselves are on the list
    assert {"sportsassets.kalshi_venue", "sportsassets.kalshi_orders",
            "sportsassets.live_executor", "sportsassets.pmus",
            "sportsassets.pmx"} <= G.ORDER_MODULES


def test_after_install_no_order_module_can_be_imported():
    r = _py(
        "import importlib, json\n"
        "from sportsassets import market_plane_guard as G\n"
        "rep = G.install(env={})\n"
        "out = {}\n"
        "for m in sorted(G.ORDER_MODULES):\n"
        "    try:\n"
        "        importlib.import_module(m); out[m] = 'IMPORTED'\n"
        "    except G.OrderModuleBlocked:\n"
        "        out[m] = 'BLOCKED'\n"
        "print(json.dumps({'rep': rep, 'out': out}))\n")
    assert r.returncode == 0, r.stderr
    d = json.loads(r.stdout.strip().splitlines()[-1])
    assert set(d["out"].values()) == {"BLOCKED"}, d["out"]
    assert d["rep"]["process_locked"] is True and d["rep"]["refused"] is None


def test_the_plane_and_the_kalshi_ws_runtime_import_under_the_guard():
    r = _py(
        "import sys, json\n"
        "from sportsassets import market_plane_guard as G\n"
        "G.install(env={})\n"
        "import sportsassets.workers.universal_market_plane as U\n"
        "import sportsassets.workers.kalshi_ws_market_data as K\n"
        "import sportsassets.kalshi_ws, sportsassets.pmx_institutional\n"
        "import sportsassets.workers.kalshi_market_data\n"
        "loaded = sorted(m for m in G.ORDER_MODULES if m in sys.modules)\n"
        "print(json.dumps(loaded))\n")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout.strip().splitlines()[-1]) == []


def test_the_process_lock_refuses_every_venue_write_at_the_transport():
    r = _py(
        "from sportsassets import market_plane_guard as G\n"
        "from sportsassets import venue_request_gate as V, execution_gate as E\n"
        "G.install(env={})\n"
        "V.check_write_lock('GET', '/trade-api/v2/markets')\n"
        "for m in ('POST', 'DELETE', 'PUT', 'PATCH'):\n"
        "    try:\n"
        "        V.check_write_lock(m, '/trade-api/v2/portfolio/orders')\n"
        "        print('ALLOWED', m)\n"
        "    except E.Denied:\n"
        "        print('DENIED', m)\n"
        "try:\n"
        "    E.refuse_if_locked('cancel'); print('ALLOWED cancel')\n"
        "except E.Denied:\n"
        "    print('DENIED cancel')\n")
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == ["DENIED", "POST", "DENIED", "DELETE",
                                "DENIED", "PUT", "DENIED", "PATCH",
                                "DENIED", "cancel"]


def test_an_order_capable_credential_makes_the_plane_refuse():
    for k in ("PMUS_SECRET_KEY", "LIVE_TRADING_ENABLED",
              "PMUS_EXECMIRROR_KEY_ID", "EDGE_KALSHI_PRIVATE_KEY"):
        assert G.forbidden_env_present({k: "x"}) == [k]
    assert G.forbidden_env_present({"LIVE_TRADING_ENABLED": "false"}) == []
    assert G.forbidden_env_present({
        "DATABASE_URL": "x", "KALSHI_API_KEY_ID": "x",
        "KALSHI_PRIVATE_KEY_PEM": "x", "UNIVERSAL_MARKET_PLANE": "off"}) == []
    r = _py("import json\n"
            "from sportsassets import market_plane_guard as G\n"
            "print(json.dumps(G.install()))\n",
            {"PMUS_SECRET_KEY": "not-a-real-key",
             "UNIVERSAL_MARKET_PLANE": "off"})
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout.strip().splitlines()[-1])
    assert rep["refused"] == G.R_FORBIDDEN_ENV
    assert rep["forbidden_env_present"] == ["PMUS_SECRET_KEY"]
    assert rep["mode"] == "KALSHI_WS_ONLY"
    assert "not-a-real-key" not in r.stdout + r.stderr     # names only


def test_main_installs_the_guard_before_any_runtime():
    src = (BACKEND / "sportsassets" / "workers" /
           "universal_market_plane.py").read_text()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "main")
    body = [ast.unparse(s) for s in fn.body
            if not (isinstance(s, ast.Expr) and isinstance(s.value,
                                                           ast.Constant))]
    assert body[0] == "from .. import market_plane_guard as GUARD"
    assert body[1] == "guard = GUARD.install()"
    assert body[2] == "from . import kalshi_ws_market_data as KWSMD"


def test_a_refused_plane_starts_no_runtime_and_says_why(monkeypatch):
    from sportsassets import market_plane_guard as GUARD
    from sportsassets.workers import kalshi_ws_market_data as KWSMD
    from sportsassets.workers import universal_market_plane as U
    calls = []

    async def no(*a, **k):
        calls.append("RUNTIME_STARTED")

    async def beat(guard, *, status="ok", forever=True):
        calls.append(("beat", status, guard["refused"]))
    monkeypatch.setattr(GUARD, "install", lambda: {
        "refused": G.R_FORBIDDEN_ENV, "mode": "KALSHI_WS_ONLY"})
    monkeypatch.setattr(U, "run", no)
    monkeypatch.setattr(KWSMD, "run", no)
    monkeypatch.setattr(U, "plane_beat", beat)
    asyncio.run(U.main())
    assert calls == [("beat", "blocked", G.R_FORBIDDEN_ENV)]

    calls.clear()
    monkeypatch.setattr(GUARD, "install", lambda: {"refused": None})
    asyncio.run(U.main())
    assert calls.count("RUNTIME_STARTED") == 2
    assert ("beat", "ok", None) in calls


def test_the_plane_boot_record_carries_commit_and_guard(monkeypatch):
    from sportsassets.workers import universal_market_plane as U
    got = []

    async def hb(service, status="ok", detail=None, con=None):
        got.append((service, status, detail))
    monkeypatch.setattr(U, "heartbeat", hb)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "f" * 40)
    asyncio.run(U.plane_beat({"refused": None, "mode": "KALSHI_WS_ONLY"},
                             forever=False))
    (svc, st, d), = got
    assert svc == U.PLANE_SERVICE == "market_plane" and st == "ok"
    assert d["commit"] == "f" * 40 and d["guard"]["mode"] == "KALSHI_WS_ONLY"


# ── which mechanism serves the Kalshi books ────────────────────────────

NOW = 1_000_000.0


def _rest(ws=0, rest=0, den=120):
    return {"freshness": {"numerator": ws + rest, "denominator": den,
                          "rate": round((ws + rest) / den, 4),
                          "current_by_source": {"WS": ws, "REST": rest},
                          "sla_s": 30.0}}


def _ws(*, connected=True, current=100, state=None, age=5.0):
    d = {"ws": {"connected": connected, "current": current, "markets": 110,
                "by_state": {"CURRENT": current}},
         "freshness": {"numerator": current, "denominator": 110},
         "subscribed_markets": 110, "resubscribes": 2, "connections": 1,
         "account_limits": {"status": "OK"}}
    if state:
        d = {"state": state, "why": "KALSHI_WS_NO_CREDENTIAL"}
    return {"status": "ok", "detail": d, "at": NOW - age}


def _plane(refused=None, age=10.0):
    return {"status": "ok", "at": NOW - age,
            "detail": {"commit": "e" * 40, "guard": {
                "refused": refused, "mode": "KALSHI_WS_ONLY",
                "process_locked": True}}}


def test_ws_primary_only_when_plane_ws_and_tracked_books_agree():
    m = KWS.mechanism(_rest(ws=110, rest=5), _ws(), _plane(), now=NOW)
    assert m["mechanism"] == KWS.KALSHI_WS and m["why"] == []
    assert m["tracked"] == {"denominator": 120, "current_ws": 110,
                            "current_rest": 5, "numerator": 115,
                            "rate": 0.9583, "ws_share": 0.9167,
                            "sla_s": 30.0}
    assert m["plane"]["commit"] == "e" * 40
    assert m["plane"]["process_locked"] is True


@pytest.mark.parametrize("rest,ws,plane,why", [
    (_rest(rest=120), None, None, ["MARKET_PLANE_ABSENT",
                                   "KALSHI_WS_HEARTBEAT_ABSENT",
                                   "NO_TRACKED_BOOK_CURRENT_ON_WS_BASIS"]),
    (_rest(ws=1), _ws(), _plane(age=500), ["MARKET_PLANE_HEARTBEAT_STALE"]),
    (_rest(ws=1), _ws(), _plane(refused=G.R_FORBIDDEN_ENV),
     ["MARKET_PLANE_REFUSED:" + G.R_FORBIDDEN_ENV]),
    (_rest(ws=1), _ws(state="OWNER_ACTION_REQUIRED"), _plane(),
     ["KALSHI_WS_KALSHI_WS_NO_CREDENTIAL"]),
    (_rest(ws=1), _ws(connected=False, current=0), _plane(),
     ["KALSHI_WS_NOT_CONNECTED", "KALSHI_WS_NO_CURRENT_BOOK"]),
    (_rest(ws=1), _ws(age=500), _plane(), ["KALSHI_WS_HEARTBEAT_STALE"]),
    (_rest(rest=100), _ws(), _plane(),
     ["NO_TRACKED_BOOK_CURRENT_ON_WS_BASIS"]),
])
def test_rest_fallback_names_every_reason(rest, ws, plane, why):
    m = KWS.mechanism(rest, ws, plane, now=NOW)
    assert m["mechanism"] == KWS.KALSHI_REST_FALLBACK
    assert m["why"] == why


def test_rest_is_counted_apart_never_merged_into_ws():
    m = KWS.mechanism(_rest(ws=0, rest=120), _ws(), _plane(), now=NOW)
    assert m["tracked"]["current_ws"] == 0
    assert m["tracked"]["current_rest"] == 120
    assert m["tracked"]["ws_share"] == 0.0
    assert KWS.mechanism(None, None, None, now=NOW)["tracked"][
        "ws_share"] is None


class _Conn:
    def __init__(self, beats):
        self.beats = beats

    async def fetchrow(self, sql, *args):
        b = self.beats.get(args[0]) if args else None
        if b is None:
            return None
        return {"status": b["status"], "detail": json.dumps(b["detail"]),
                "at": b["at"]}


def test_the_venues_readback_reports_the_mechanism(monkeypatch):
    from sportsassets.api import command_venues as V
    conn = _Conn({"kalshi_market_data": dict(_rest(ws=110, rest=5),
                                             status="ok", at=NOW - 3,
                                             detail=_rest(ws=110, rest=5)),
                  "kalshi_ws_market_data": _ws(),
                  "market_plane": _plane()})
    h = asyncio.run(V.kalshi_health(conn, now=NOW))
    assert h["state"] == "OK" and h["mechanism"]["mechanism"] == KWS.KALSHI_WS
    assert h["freshness"]["current_by_source"] == {"WS": 110, "REST": 5}
    absent = asyncio.run(V.kalshi_health(_Conn({}), now=NOW))
    assert absent["state"] == "NOT_RUNNING"
    assert absent["mechanism"]["mechanism"] == KWS.KALSHI_REST_FALLBACK


def test_the_red_team_venue_read_carries_the_mechanism_without_a_new_gate():
    from sportsassets.redteam import venue_health as VH
    beats = {"kalshi_market_data": {"status": "ok", "at": NOW - 3,
                                    "detail": _rest(rest=120)}}
    out = asyncio.run(VH.read(_Conn(beats), held={"markable": 0},
                              priority=None, total=None, now=NOW))
    assert out["kalshi_mechanism"]["mechanism"] == KWS.KALSHI_REST_FALLBACK
    assert "MARKET_PLANE_ABSENT" in out["kalshi_mechanism"]["why"]
    # KALSHI venue freshness is still current books of ANY basis
    k = out["venues"][VH.KALSHI]
    assert (k["numerator"], k["denominator"], k["green"]) == (120, 120, True)


# ── the provisioning spec and workflow ─────────────────────────────────

def test_the_provision_spec_is_the_owner_spec_and_holds_no_order_capability():
    import yaml
    s = json.loads(SPEC.read_text())
    names = set(s["env"]) | set(s["copy_secrets"])
    assert not names & set(G.FORBIDDEN_ENV)
    assert set(s["copy_secrets"]) <= set(G.ALLOWED_SECRET_ENV)
    assert not any(n.startswith(("PMUS", "EDGE")) for n in names)
    # exactly the owner's dedicated-service spec: same command, same plain
    # switches, same secret NAMES (values copied, never written anywhere)
    svc = next(x for x in yaml.safe_load(YAML.read_text())["services"]
               if x["name"] == s["name"])
    env = {e["key"]: e for e in svc["envVars"]}
    secret = {k for k, e in env.items()
              if e.get("sync") is False or "fromDatabase" in e}
    assert set(s["copy_secrets"]) == secret
    assert s["env"] == {k: e["value"] for k, e in env.items()
                        if k not in secret}
    assert s["dockerCommand"] == svc["dockerCommand"]
    assert s["plan"] == svc["plan"] == "standard"
    # every value comes from the service that already holds it
    assert set(s["copy_secrets"].values()) == {"sportsassets-workers"}
    assert s["branch"] == "claude/release-api" and s["autoDeploy"] == "no"
    assert "sportsassets-market-plane" not in (ROOT / "render.yaml"
                                               ).read_text()


def _arms(body):
    import re
    out = {}
    for m in re.finditer(r"^  ([a-z|-]+)\)\n", body, re.M):
        end = body.index("\n    ;;\n", m.end())
        out[m.group(1)] = body[m.end():end]
    return out


def test_the_workflow_mutates_only_behind_confirm_and_never_prints_a_secret():
    body = (ROOT / ".github" / "market-plane" / "plane.sh").read_text()
    arms = _arms(body)
    assert set(arms) == {"plan", "status", "create", "deploy-commit",
                         "suspend"}
    for a in ("create", "deploy-commit", "suspend"):
        first = [ln.strip() for ln in arms[a].splitlines()
                 if ln.strip() and not ln.strip().startswith("#")][0]
        assert first == "need_confirm", a
    for a in ("plan", "status"):
        assert "send " not in arms[a] and "send POST" not in arms[a], a
    assert "send POST /services " in arms["create"]
    # values are masked line by line and only their lengths are printed;
    # they travel Render JSON -> jq -> body byte for byte, never through a
    # shell variable (production plan run 37715995613: $(...) stripped the
    # Kalshi PEM's trailing newline, 119 -> 118 chars)
    assert 'mask_value "$(jq -r' in body and "| length' " in body
    assert '--slurpfile sec "$TMP/secrets.jsonl"' in body
    for bad in ('echo "$v"', "echo $v", 'export "SEC_', '$ENV["SEC_',
                'cat "$TMP/body', "set -x"):
        assert bad not in body, bad
    assert "create body names differ from the spec: refused" in body
    wf = (ROOT / ".github" / "workflows" / "market-plane.yml").read_text()
    assert "run: bash .github/market-plane/plane.sh" in wf
    assert "${{" not in body


def _closure(starts, stop=frozenset()):
    """Every sportsassets module reachable from `starts` by ANY import
    statement (top level or inside a function), with the importing edge;
    modules in `stop` are reached but never expanded (under the guard they
    cannot load, so nothing behind them can be reached through them)."""
    def modfile(m):
        p = BACKEND.joinpath(*m.split("."))
        for c in (p.with_suffix(".py"), p / "__init__.py"):
            if c.exists():
                return c
        return None

    def targets(cur, node):
        if isinstance(node, ast.Import):
            return [a.name for a in node.names
                    if a.name.startswith("sportsassets")]
        if node.level:
            base = cur.split(".")
            pkg = base if modfile(cur).name == "__init__.py" else base[:-1]
            pkg = pkg[:len(pkg) - (node.level - 1)]
            mod = ".".join(pkg + ([node.module] if node.module else []))
        else:
            mod = node.module or ""
        if not mod.startswith("sportsassets"):
            return []
        return [mod] + [mod + "." + a.name for a in node.names
                        if modfile(mod + "." + a.name)]
    edges, seen, todo = set(), set(starts), list(starts)
    while todo:
        m = todo.pop()
        f = modfile(m)
        if f is None:
            continue
        for n in ast.walk(ast.parse(f.read_text())):
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                for t in targets(m, n):
                    edges.add((m, t))
                    if t not in seen:
                        seen.add(t)
                        if t not in stop:
                            todo.append(t)
    return seen, edges


def test_the_only_road_to_an_order_module_is_a_lazy_import_the_guard_blocks():
    """The plane's whole import closure (lazy imports included) reaches an
    order client by exactly one edge: execution_gate.read_state's in-function
    `from . import live_executor` (the submit-time kill-switch read, never
    called by the plane). Under the guard that import raises; any NEW edge
    into an order module fails this test."""
    seen, edges = _closure(["sportsassets.workers.universal_market_plane",
                            "sportsassets.workers.kalshi_ws_market_data",
                            "sportsassets.market_plane_guard"],
                           stop=G.ORDER_MODULES)
    into = {(a, b) for a, b in edges if b in G.ORDER_MODULES}
    assert into == {("sportsassets.execution_gate",
                     "sportsassets.live_executor")}, into
    src = (BACKEND / "sportsassets" / "execution_gate.py").read_text()
    top = [n for n in ast.parse(src).body
           if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not any("live_executor" in ast.unparse(n) for n in top)
    assert len(seen) > 50
