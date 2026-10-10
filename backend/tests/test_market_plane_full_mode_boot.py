"""THE DEDICATED MARKET PLANE BOOTS ITS FULL GRAPH ORDERLESS (RC5, PM
requirement: the real full-mode import graph, not a pruned one).

`python -m sportsassets.workers.universal_market_plane` is main(): it installs
market_plane_guard (UMP_AND_KALSHI_WS) and gathers the universal market plane
run loop, the Kalshi WebSocket book runtime and the plane's boot record. This
test runs THAT main() -- the real modules, the real guard, the real
TokenKeeper, refdata planner, subscribe-all gRPC transport, Kalshi catalogue
walk, Kalshi WebSocket subscriber and limits read -- in a subprocess, for a
few passes, with only the transports replaced at their lowest layer:

  requests.Session        (PMX Auth0 token mint, PMX refdata, Kalshi GETs)
  grpc.secure_channel     (the PMX market-data channel and its stub calls)
  httpx.AsyncClient       (the Kalshi account-limits read)
  websockets.connect      (the Kalshi WebSocket handshake and frames)

Every transport records what reached it. The test asserts that
authentication, reference data and both subscriptions reached the mocks, and
then, still under the guard, attempts every submit / cancel / funding path
the process could name (the order modules, execution_gate submit / close /
cancel, Kalshi REST POST / DELETE portfolio orders through the gated
transport, PMX / PMUS order and transfer endpoints, PMX order reads by name,
non-subscribe gRPC commands, a non-GET on the Kalshi catalogue transport):
each is REFUSED, and the mocks record ZERO such requests -- refused before
any byte could leave. The database is the test database, on one connection
inside a transaction rolled back at the end (nothing the boot wrote stays).
No credential: the RSA and Ed25519 keys are generated for the test.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys

import pytest

from sportsassets import market_plane_guard as G

BACKEND = pathlib.Path(__file__).resolve().parents[1]
DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

BOOT = r'''
import asyncio, base64, importlib, json, os, re, sys, threading, time

REC = {"requests": [], "grpc": [], "httpx": [], "ws_connect": [],
       "ws_sent": [], "beats": []}
STOP = threading.Event()

# ── keys generated here (no credential exists in this process) ──────────
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
rsa_pem = rsa.generate_private_key(public_exponent=65537, key_size=2048
    ).private_bytes(serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption())
ed_pem = ed25519.Ed25519PrivateKey.generate().private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption()).decode()
os.environ.update({
    "PMX_CLIENT_ID": "boot-test-client-NOT-REAL", "PMX_KEY_ID": "boot-kid",
    "PMX_PRIVATE_KEY_B64": base64.b64encode(rsa_pem).decode(),
    "KALSHI_API_KEY_ID": "boot-test-kalshi-NOT-REAL",
    "KALSHI_PRIVATE_KEY_PEM": ed_pem,
    "UNIVERSAL_MARKET_PLANE": "on", "INSTITUTIONAL_MD_STREAM": "on",
    "UMP_SUBSCRIBE_ALL": "on", "UMP_RUNTIME": "DEDICATED_READ_ONLY",
    "KALSHI_CATALOGUE": "on"})

# ── requests.Session: PMX token + refdata, Kalshi catalogue GETs ───────
import requests

def _tok():
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()
                                            ).decode().rstrip("=")
    return "%s.%s.sig" % (enc({"alg": "none"}),
                          enc({"scope": "read:marketdata"}))

class _Resp:
    def __init__(self, status, body):
        self.status_code, self._b = status, body
    def json(self):
        return json.loads(json.dumps(self._b))

class FakeSession:
    def __init__(self, *a, **k):
        pass
    def request(self, method, url, **kw):
        REC["requests"].append([str(method).upper(), str(url)])
        if url.endswith("/oauth/token"):
            return _Resp(200, {"access_token": _tok(), "expires_in": 180})
        if url.endswith("/v1/refdata/instruments"):
            return _Resp(200, {"instruments": [{
                "symbol": "boot-sym-1", "priceScale": "1000",
                "fractionalQtyScale": "100",
                "state": "INSTRUMENT_STATE_OPEN"}], "eof": True})
        if url.endswith("/series"):
            return _Resp(200, {"series": [{"ticker": "KXBOOTGAME",
                                           "title": "Boot", "tags": [],
                                           "category": "Sports"}]})
        if url.endswith("/markets"):
            return _Resp(200, {"markets": [{
                "ticker": "KXBOOTGAME-26OCT12A-A", "event_ticker":
                "KXBOOTGAME-26OCT12A", "status": "active",
                "market_type": "binary", "rules_primary": "If A wins, Yes."
                }], "cursor": None})
        return _Resp(404, {})
    def post(self, url, **kw):
        return self.request("POST", url, **kw)
    def get(self, url, **kw):
        return self.request("GET", url, **kw)
requests.Session = FakeSession

# ── grpc.secure_channel: the PMX market-data channel ────────────────────
import grpc
from sportsassets.vendor.pmx_proto import marketdatasubscription_pb2 as pb2

class FakeCall:
    def __init__(self, method, reqs, metadata):
        self.method, self.reqs, self.cancelled = method, reqs, False
        first = next(reqs)
        REC["grpc"].append({
            "method": method,
            "metadata": sorted(k for k, _v in (metadata or ())),
            "command": first.WhichOneof("command"),
            "symbols": list(first.subscribe.symbols),
            "depth": first.depth})
        self.n = 0
    def __iter__(self):
        return self
    def __next__(self):
        if self.cancelled or STOP.is_set():
            raise StopIteration
        self.n += 1
        if self.n > 1:
            STOP.wait(0.5)
            if self.cancelled or STOP.is_set():
                raise StopIteration
        return pb2.BiDirectionalStreamMarketDataResponse(
            heartbeat=pb2.Heartbeat())
    def cancel(self):
        self.cancelled = True

class FakeChannel:
    def __init__(self, target):
        self.target = target
    def stream_stream(self, method, *a, **k):
        return lambda reqs, metadata=None, **kw: FakeCall(method, reqs,
                                                         metadata)
    def unary_stream(self, method, *a, **k):
        def call(*aa, **kk):
            REC["grpc"].append({"method": method, "command": None})
            raise RuntimeError("never used by this lane")
        return call
    def close(self):
        pass
grpc.secure_channel = lambda target, creds=None, options=None: \
    FakeChannel(target)

# ── httpx.AsyncClient: the Kalshi account-limits read ───────────────────
import httpx
_RealAsync = httpx.AsyncClient

def _hx(request):
    REC["httpx"].append([request.method, str(request.url),
                         sorted(k for k in request.headers.keys()
                                if k.lower().startswith("kalshi"))])
    return httpx.Response(200, json={"usage_tier": "basic",
                                     "read": {"refill_rate": 20,
                                              "bucket_capacity": 20}})

class RecAsync(_RealAsync):
    def __init__(self, *a, **kw):
        kw["transport"] = httpx.MockTransport(_hx)
        super().__init__(*a, **kw)
httpx.AsyncClient = RecAsync

# ── websockets.connect: the Kalshi WebSocket ────────────────────────────
import websockets

class FakeWS:
    def __init__(self):
        self.q = asyncio.Queue()
        self.seq = 0
    async def send(self, raw):
        m = json.loads(raw)
        REC["ws_sent"].append(m.get("cmd"))
        if m.get("cmd") == "subscribe":
            for t in m["params"]["market_tickers"]:
                self.seq += 1
                await self.q.put(json.dumps({
                    "type": "orderbook_snapshot", "sid": 1, "seq": self.seq,
                    "msg": {"market_ticker": t,
                            "yes_dollars_fp": [["0.45", "10.00"]],
                            "no_dollars_fp": [["0.50", "12.00"]]}}))
    async def recv(self):
        return await self.q.get()
    async def close(self):
        pass

async def fake_connect(url, **kw):
    REC["ws_connect"].append([url, sorted(
        (kw.get("additional_headers") or {}).keys())])
    await asyncio.sleep(0.3)          # a TLS handshake takes this long
    return FakeWS()
websockets.connect = fake_connect

# ── one database connection, every acquire a savepoint, all rolled back ─
import asyncpg
from sportsassets import db as DB

class _Acq:
    def __init__(self, pool):
        self.pool = pool
    async def __aenter__(self):
        await self.pool.lock.acquire()
        self.tr = self.pool.conn.transaction()
        await self.tr.start()
        return self.pool.conn
    async def __aexit__(self, et, e, tb):
        try:
            if et is None:
                await self.tr.commit()
            else:
                await self.tr.rollback()
        finally:
            self.pool.lock.release()
        return False

class OnePool:
    def __init__(self, conn):
        self.conn, self.lock = conn, asyncio.Lock()
    def acquire(self, timeout=None):
        return _Acq(self)

async def boot():
    conn = await asyncpg.connect(os.environ["BOOT_DSN"])
    outer = conn.transaction()
    await outer.start()
    try:
        await conn.execute(
            "INSERT INTO kalshi_fixtures_current (event_ticker, "
            " series_ticker, team_tickers, mapping_status, start_at) VALUES "
            " ('KXBOOTWS-1', 'KXBOOTWS', ARRAY['KXBOOTWS-1-A','KXBOOTWS-1-B'],"
            " 'ESTABLISHED', now() + interval '1 hour') "
            "ON CONFLICT (event_ticker) DO NOTHING")
        # one PMX-listed contract, so the plane assigns a shard and opens
        # its one subscribe-all stream
        await conn.execute(
            "INSERT INTO market_plane_registry (contract_id, venue, active, "
            " desired_subscription, updated_at, priority, refdata) VALUES "
            " ('boot-sym-1', 'POLYMARKET_US', true, true, now(), 80, "
            " $1::jsonb) ON CONFLICT (contract_id) DO UPDATE SET "
            " active = true, refdata = excluded.refdata",
            json.dumps({"symbol": "boot-sym-1", "priceScale": "1000",
                        "fractionalQtyScale": "100",
                        "state": "INSTRUMENT_STATE_OPEN"}))
        DB._pool = OnePool(conn)
        from sportsassets.workers import universal_market_plane as W
        W.INTERVAL_S = 0.2
        real_hb = W.heartbeat
        async def hb(service, status="ok", detail=None, con=None):
            REC["beats"].append([service, status])
            if service == W.PLANE_SERVICE:
                REC["guard"] = (detail or {}).get("guard")
            await real_hb(service, status, detail, con=con)
        W.heartbeat = hb
        task = asyncio.ensure_future(W.main())
        def ready():
            urls = [u for _m, u in REC["requests"]]
            return (sum(1 for s, st in REC["beats"]
                        if s == W.SERVICE and st != "error") >= 3
                    and any(u.endswith("/oauth/token") for u in urls)
                    and any(u.endswith("/refdata/instruments") for u in urls)
                    and any(u.endswith("/markets") for u in urls)
                    and REC["grpc"] and "subscribe" in REC["ws_sent"]
                    and REC["httpx"])
        end = time.time() + 120
        while time.time() < end and not ready() and not task.done():
            await asyncio.sleep(0.1)
        REC["ready"] = bool(ready())
        if task.done():
            REC["main_ended"] = repr(task.exception())
        task.cancel()
        try:
            await task
        except BaseException:
            pass
        STOP.set()
    finally:
        await outer.rollback()
        await conn.close()

asyncio.run(boot())

# ── still under the guard: every write path, refused before transmission ─
from sportsassets import market_plane_guard as G
from sportsassets import execution_gate as EG
from sportsassets import venue_request_gate as VG
from sportsassets import kalshi_catalogue as KC
from sportsassets import kalshi_ws as KWS
from sportsassets import pmx_institutional as PMXI
from sportsassets import institutional_stream as IS
OUT = {"refused": {}, "leaked": []}

def expect(name, fn, exc):
    n0 = {k: len(v) for k, v in REC.items() if isinstance(v, list)}
    try:
        fn()
        OUT["refused"][name] = "NOT_REFUSED"
    except exc as e:
        OUT["refused"][name] = type(e).__name__
    except Exception as e:
        OUT["refused"][name] = "WRONG_ERROR:%s" % type(e).__name__
    n1 = {k: len(v) for k, v in REC.items() if isinstance(v, list)}
    if n1 != n0:
        OUT["leaked"].append(name)

for m in sorted(G.ORDER_MODULES):
    expect("import:" + m, lambda m=m: importlib.import_module(m),
           G.OrderModuleBlocked)
for op in ("submit", "close_position"):
    expect("execution_gate.authorize:" + op,
           lambda op=op: EG.authorize(op, lane="manual"), EG.Denied)
    expect("execution_gate.authorize_async:" + op,
           lambda op=op: asyncio.run(EG.authorize_async(op, lane="manual")),
           EG.Denied)
expect("execution_gate.refuse_if_locked:cancel",
       lambda: EG.refuse_if_locked("cancel"), EG.Denied)

inner_calls = []
class Inner(httpx.BaseTransport):
    def handle_request(self, request):
        inner_calls.append([request.method, str(request.url)])
        return httpx.Response(200, json={})
gated = httpx.Client(transport=VG.PacedTransport(Inner()))
WRITES = [
    ("POST", KWS.REST_BASE + "/trade-api/v2/portfolio/orders"),
    ("POST", KWS.REST_BASE + "/trade-api/v2/portfolio/orders/batched"),
    ("DELETE", KWS.REST_BASE + "/trade-api/v2/portfolio/orders/ord-1"),
    ("POST", KWS.REST_BASE + "/trade-api/v2/portfolio/orders/ord-1/amend"),
    ("POST", KC.BASE + "/portfolio/orders"),
    ("POST", PMXI.REST_BASE + "/v1/orders"),
    ("POST", PMXI.REST_BASE + "/v1/orders/cancel"),
    ("DELETE", PMXI.REST_BASE + "/v1/orders/ord-1"),
    ("POST", "https://api.polymarket.us/v1/orders"),
    ("POST", "https://api.polymarket.us/v1/order/cancel"),
    ("POST", PMXI.REST_BASE + "/v1/transfers"),
    ("POST", PMXI.REST_BASE + "/v1/funding/withdraw"),
    ("POST", KWS.REST_BASE + "/trade-api/v2/portfolio/transfers")]
for method, url in WRITES:
    expect("gated_transport:%s %s" % (method, url),
           lambda method=method, url=url: gated.request(method, url,
                                                         json={}),
           EG.Denied)
OUT["inner_transport_calls"] = inner_calls

catalogue = KC.GetOnlyTransport(session=FakeSession())
for method in ("POST", "DELETE", "PUT", "PATCH"):
    expect("kalshi_catalogue:%s" % method,
           lambda method=method: catalogue.request(
               method, KC.BASE + "/portfolio/orders"), KC.NonGetRefused)
for name in ("insert_order", "cancel_order", "replace_order", "preview",
             "transfer", "funding", "withdraw", "positions"):
    expect("pmx_read_by_name:" + name,
           lambda name=name: PMXI.request_for(name, "boot-sym-1"),
           PMXI.NotAReadPath)
for cmd in (pb2.BiDirectionalStreamMarketDataRequest(
                unsubscribe=pb2.UnsubscribeCommand(symbols=["boot-sym-1"])),
            pb2.BiDirectionalStreamMarketDataRequest(
                subscribe=pb2.SubscribeCommand(symbols=[]))):
    expect("grpc_outbound:%s" % cmd.WhichOneof("command"),
           lambda cmd=cmd: IS._outbound(cmd), IS.OutboundRefused)
OUT["grpc_services"] = sorted(pb2.DESCRIPTOR.services_by_name)
OUT["grpc_methods"] = sorted(
    m.name for m in pb2.DESCRIPTOR.services_by_name[
        "MarketDataSubscriptionAPI"].methods)
OUT["kalshi_ws_commands"] = sorted(
    k for k in vars(KWS.Commands) if not k.startswith("_"))
OUT["kalshi_ws_update_actions"] = sorted(KWS.UPDATE_ACTIONS)
OUT["process_lock"] = EG.process_lock()
OUT["order_modules_loaded"] = sorted(
    m for m in G.ORDER_MODULES if m in sys.modules)
print("BOOT_RESULT " + json.dumps({"rec": {k: v for k, v in REC.items()},
                                   "out": OUT}, default=str))
'''


def _boot() -> dict:
    env = {k: v for k, v in os.environ.items()
           if k not in G.FORBIDDEN_ENV and not k.startswith(("KALSHI",
                                                            "PMX_"))}
    env.update({"PYTHONPATH": str(BACKEND), "BOOT_DSN": DSN,
                "DATABASE_URL": DSN})
    r = subprocess.run([sys.executable, "-c", BOOT], cwd=str(BACKEND),
                       env=env, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-4000:]
    line = [ln for ln in r.stdout.splitlines()
            if ln.startswith("BOOT_RESULT ")]
    assert line, (r.stdout[-2000:], r.stderr[-4000:])
    got = json.loads(line[-1][len("BOOT_RESULT "):])
    # names only ever: no key material printed by the boot
    assert "PRIVATE KEY" not in r.stdout + r.stderr
    return got


@pytest.fixture(scope="module")
def booted():
    if not DSN:
        pytest.skip("needs RN1X_TEST_DSN")
    return _boot()


WRITE_PATH = r"/orders|/order/|cancel|transfer|fund|withdraw|deposit"


@pg
def test_the_full_graph_boots_and_reaches_every_read_transport(booted):
    rec = booted["rec"]
    assert rec["ready"] is True, rec
    assert "main_ended" not in rec, rec.get("main_ended")
    # authentication: the PMX Auth0 token mint reached the mock ...
    assert ["POST", "https://pmx-prod.us.auth0.com/oauth/token"] in \
        rec["requests"]
    # ... reference data: the batched instruments read ...
    assert ["POST", "https://api.prod.polymarketexchange.com/v1/refdata/"
            "instruments"] in rec["requests"]
    # ... the Kalshi catalogue (GET-only) ...
    assert any(m == "GET" and u.endswith("/series")
               for m, u in rec["requests"])
    # ... the PMX subscribe-all stream: ONE bidi call, bearer in metadata,
    # its first request the empty-list subscribe at depth 10 ...
    (call,) = [g for g in rec["grpc"]]
    assert call["method"] == ("/polymarket.v1.MarketDataSubscriptionAPI/"
                              "BiDirectionalStreamMarketData")
    assert call["metadata"] == ["authorization"]
    assert (call["command"], call["symbols"], call["depth"]) == (
        "subscribe", [], 10)
    # ... the Kalshi WebSocket: a signed handshake and a subscribe ...
    (ws,) = rec["ws_connect"]
    assert ws[0].startswith("wss://") and ws[1] == [
        "KALSHI-ACCESS-KEY", "KALSHI-ACCESS-SIGNATURE",
        "KALSHI-ACCESS-TIMESTAMP"]
    assert set(rec["ws_sent"]) == {"subscribe"}
    # ... and the signed Kalshi limits read
    assert rec["httpx"] and all(
        h[0] == "GET" and h[1].endswith("/trade-api/v2/account/limits")
        and h[2] == ["kalshi-access-key", "kalshi-access-signature",
                     "kalshi-access-timestamp"] for h in rec["httpx"])
    beats = {s for s, st in rec["beats"]}
    assert {"universal_market_plane", "market_plane"} <= beats
    # main() ran under the guard it installed first, in the full mode
    g = rec["guard"]
    assert g["mode"] == "UMP_AND_KALSHI_WS" and g["refused"] is None
    assert g["process_locked"] is True
    assert g["order_modules_already_loaded"] == []
    assert g["forbidden_env_present"] == []


@pg
def test_every_transmission_that_left_was_a_read_or_the_token(booted):
    rec = booted["rec"]
    for method, url in rec["requests"]:
        assert (method == "GET" or url.endswith("/oauth/token")
                or url.endswith("/v1/refdata/instruments")), (method, url)
        assert not re.search(WRITE_PATH, url), url
    for h in rec["httpx"]:
        assert h[0] == "GET" and not re.search(WRITE_PATH, h[1])
    assert {g["method"].rsplit("/", 1)[-1] for g in rec["grpc"]} == {
        "BiDirectionalStreamMarketData"}
    assert set(rec["ws_sent"]) <= {"subscribe", "unsubscribe"}


@pg
def test_every_submit_cancel_and_funding_path_is_refused_before_the_wire(
        booted):
    out = booted["out"]
    ref = out["refused"]
    assert out["process_lock"] and out["order_modules_loaded"] == []
    for m in G.ORDER_MODULES:
        assert ref["import:" + m] == "OrderModuleBlocked", m
    for k in ("execution_gate.authorize:submit",
              "execution_gate.authorize:close_position",
              "execution_gate.authorize_async:submit",
              "execution_gate.authorize_async:close_position",
              "execution_gate.refuse_if_locked:cancel"):
        assert ref[k] == "Denied", k
    gated = {k: v for k, v in ref.items() if k.startswith("gated_transport")}
    assert len(gated) == 13 and set(gated.values()) == {"Denied"}, gated
    assert out["inner_transport_calls"] == []          # nothing dispatched
    for m in ("POST", "DELETE", "PUT", "PATCH"):
        assert ref["kalshi_catalogue:%s" % m] == "NonGetRefused"
    for n in ("insert_order", "cancel_order", "replace_order", "preview",
              "transfer", "funding", "withdraw", "positions"):
        assert ref["pmx_read_by_name:" + n] == "NotAReadPath"
    assert ref["grpc_outbound:unsubscribe"] == "OutboundRefused"
    assert ref["grpc_outbound:subscribe"] == "OutboundRefused"  # empty list
    assert out["grpc_services"] == ["MarketDataSubscriptionAPI"]
    assert out["grpc_methods"] == ["BiDirectionalStreamMarketData",
                                   "CreateMarketDataSubscription"]
    # (RC6.2) the documented command set: ONE subscribe per connection,
    # then update_subscription (add_markets / delete_markets / get_snapshot)
    # on its sid; no unsubscribe at all (it was ["subscribe", "unsubscribe"])
    assert out["kalshi_ws_commands"] == ["subscribe", "update_subscription"]
    assert out["kalshi_ws_update_actions"] == ["add_markets",
                                               "delete_markets",
                                               "get_snapshot"]
    # ZERO such requests reached any mock transport
    assert out["leaked"] == [], out["leaked"]
    assert set(ref.values()) & {"NOT_REFUSED"} == set()
    assert not any(v.startswith("WRONG_ERROR") for v in ref.values()), ref
