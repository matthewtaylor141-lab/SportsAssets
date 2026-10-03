"""THE INSTITUTIONAL MARKET-DATA PATH IS STRUCTURALLY INCAPABLE OF ORDERS.

The production PMX credential holds `write:orders` (scopes read 2026-09-19,
research/institutional/PRODUCTION_VERIFICATION_20260919.md). A granted scope
is not a capability; these tests prove the capability is absent:

  §1  the vendored package defines ONE service, MarketDataSubscriptionAPI, with
      two server-streaming read RPCs; no service, method or message anywhere
      in it is named for orders, trades, cancels, replaces or executions; the
      only RPC paths in the generated stubs are those two
  §2  no other order/trading stub exists anywhere in the package tree
  §3  the stream client (AST): one stub class, one RPC, every request built
      in exactly two builders, no unsubscribe, no other method on the stub;
      pmx_institutional reached only for its token and scales
  §4  at run time `_outbound` refuses any command but subscribe (non-empty)
      and keepalive -- with the REAL generated messages
  §5  the identity mapper reads no book, sends nothing, imports no transport
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

from sportsassets import institutional_contract_map as ICM
from sportsassets import institutional_stream as IS
from sportsassets import pmx_institutional as pmx
from sportsassets.vendor.pmx_proto import marketdatasubscription_pb2 as pb2
from sportsassets.vendor.pmx_proto import marketdatasubscription_pb2_grpc as pb2_grpc

BACKEND = pathlib.Path(__file__).resolve().parents[1]
PKG = BACKEND / "sportsassets"
VENDOR = PKG / "vendor" / "pmx_proto"

#: Names an order path would carry. Matched against SERVICE, METHOD and
#: MESSAGE names (statistics FIELDS such as last_trade_px are market data).
ORDER_WORDS = re.compile(
    r"order|trade|trading|cancel|replace|amend|modify|execution|execute|"
    r"fill|quote|rfq|position|funding|withdraw|deposit|account|preview|"
    r"insert|submit|place", re.I)

READ_RPCS = {"CreateMarketDataSubscription", "BiDirectionalStreamMarketData"}


# ── §1 the vendored package ─────────────────────────────────────────────

def test_the_only_service_is_market_data_subscription():
    fd = pb2.DESCRIPTOR
    assert list(fd.services_by_name) == ["MarketDataSubscriptionAPI"]
    svc = fd.services_by_name["MarketDataSubscriptionAPI"]
    assert svc.full_name == "polymarket.v1.MarketDataSubscriptionAPI"
    assert {m.name for m in svc.methods} == READ_RPCS
    for m in svc.methods:
        assert m.server_streaming is True           # both are read streams
        assert not ORDER_WORDS.search(m.name), m.name


def test_no_message_or_enum_is_named_for_an_order_path():
    fd = pb2.DESCRIPTOR
    for name in list(fd.message_types_by_name) + list(fd.enum_types_by_name):
        assert not ORDER_WORDS.search(name), name


def test_the_generated_stubs_carry_only_the_two_read_paths():
    src = (VENDOR / "marketdatasubscription_pb2_grpc.py").read_text()
    paths = set(re.findall(r"'/([\w.]+)/(\w+)'", src))
    assert paths == {("polymarket.v1.MarketDataSubscriptionAPI", m)
                     for m in READ_RPCS}
    stubs = {n for n in dir(pb2_grpc) if n.endswith("Stub")}
    assert stubs == {"MarketDataSubscriptionAPIStub"}


def test_the_vendored_sources_declare_no_order_service():
    for path in VENDOR.rglob("*.proto"):
        text = re.sub(r"//[^\n]*", "", path.read_text())
        services = re.findall(r"\bservice\s+(\w+)", text)
        rpcs = re.findall(r"\brpc\s+(\w+)", text)
        assert services == ["MarketDataSubscriptionAPI"], path
        assert set(rpcs) == READ_RPCS, path
        for name in re.findall(r"\b(?:message|enum)\s+(\w+)", text):
            assert not ORDER_WORDS.search(name), (path, name)


def test_only_the_market_data_files_are_vendored():
    files = sorted(p.relative_to(VENDOR).as_posix()
                   for p in VENDOR.rglob("*") if p.is_file()
                   and "__pycache__" not in p.parts)
    assert files == ["README.md", "__init__.py",
                     "marketdatasubscription.proto",
                     "marketdatasubscription_pb2.py",
                     "marketdatasubscription_pb2_grpc.py",
                     "upstream/marketdatasubscription.proto"]


# ── §2 nothing else in the tree ─────────────────────────────────────────

def test_no_order_stub_exists_anywhere_in_the_package():
    forbidden = re.compile(
        r"OrderEntryAPI|OrderAPIStub|trading_pb2|orders_pb2|rfq_pb2|"
        r"combo_pb2|positions_pb2|funding_pb2|InsertOrderRequest|"
        r"CancelOrderRequest")
    hits = []
    for path in PKG.rglob("*.py"):
        if forbidden.search(path.read_text(errors="ignore")):
            hits.append(str(path.relative_to(BACKEND)))
    assert hits == []


# ── §3 the stream client, by its syntax tree ────────────────────────────

def _tree():
    return ast.parse(pathlib.Path(IS.__file__).read_text())


def _enclosing_functions(tree):
    owner = {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(fn):
                owner.setdefault(id(node), fn.name)
    return owner


def test_one_stub_class_and_one_rpc_are_ever_touched():
    tree = _tree()
    attrs = [n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)]
    assert {a for a in attrs if a.endswith("Stub")} == {
        "MarketDataSubscriptionAPIStub"}
    assert "BiDirectionalStreamMarketData" in attrs
    assert "CreateMarketDataSubscription" not in attrs
    assert "UnsubscribeCommand" not in attrs
    owner = _enclosing_functions(tree)
    for n in ast.walk(tree):
        if not isinstance(n, ast.Attribute):
            continue
        if re.search(r"order|trade|cancel|replace|execut", n.attr, re.I):
            # The one allowed hit: the watchdog CANCELS ITS OWN gRPC CALL
            # (grpc.Future.cancel) when the stream goes silent. It ends a
            # read stream; it is not an order cancel and reaches no order.
            assert (n.attr, owner.get(id(n))) == ("cancel", "_watchdog"), \
                (n.attr, owner.get(id(n)))


def test_every_request_is_built_in_one_of_two_builders():
    tree = _tree()
    owner = _enclosing_functions(tree)
    sites = [owner.get(id(n)) for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "BiDirectionalStreamMarketDataRequest"]
    assert sorted(sites) == ["_keepalive_request", "_subscribe_request"]
    commands = [owner.get(id(n)) for n in ast.walk(tree)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr in ("SubscribeCommand", "KeepAliveCommand")]
    assert sorted(commands) == ["_keepalive_request", "_subscribe_request"]


def test_no_unsubscribe_key_is_ever_written():
    tree = _tree()
    for n in ast.walk(tree):
        if isinstance(n, ast.keyword):
            assert n.arg != "unsubscribe"
        if isinstance(n, ast.Dict):
            for k in n.keys:
                assert not (isinstance(k, ast.Constant)
                            and k.value == "unsubscribe")
        if isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant):
            assert n.slice.value != "unsubscribe"


def test_the_transport_exposes_no_sending_method_but_subscribe():
    public = {n for n in vars(IS.GrpcBidiTransport)
              if not n.startswith("_") and callable(
                  getattr(IS.GrpcBidiTransport, n))}
    assert public == {"mods", "subscribe", "run_once", "run", "start",
                      "stop"}
    for n in vars(IS.GrpcBidiTransport):
        assert not re.search(r"order|trade|cancel|replace|execut", n, re.I)


def test_pmx_institutional_is_reached_only_for_token_and_scales():
    tree = _tree()
    used = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        aliases = {a.asname or a.name for node in ast.walk(fn)
                   if isinstance(node, ast.ImportFrom)
                   for a in node.names if a.name == "pmx_institutional"}
        for node in ast.walk(fn):
            if isinstance(node, ast.Attribute) and \
                    isinstance(node.value, ast.Name) and \
                    node.value.id in aliases:
                used.add(node.attr)
    assert used == {"scales_of", "Institutional"}
    src = pathlib.Path(IS.__file__).read_text()
    # the Institutional client is used for its token and nothing else
    assert re.findall(r"client\.(\w+)", src) == ["token", "invalidate_token"]
    for name in ("request_for", "READ_ONLY_PATHS", ".read(", ".session"):
        assert name not in src


def test_pmx_institutional_itself_has_no_order_path():
    assert set(pmx.READ_ONLY_PATHS) == {"instruments", "bbo", "book"}
    assert pmx.ORDER_SUBMISSION_IMPLEMENTATION == "NONE"
    for name in ("orders", "insert", "cancel", "replace", "preview"):
        with pytest.raises(pmx.NotAReadPath):
            pmx.request_for(name, "sym")


# ── §4 the outbound gate, on the real messages ──────────────────────────

def test_outbound_refuses_everything_but_subscribe_and_keepalive():
    ok_sub = pb2.BiDirectionalStreamMarketDataRequest(
        subscribe=pb2.SubscribeCommand(symbols=["aec-mlb-sd-mil-2026-10-03"]))
    ok_ka = pb2.BiDirectionalStreamMarketDataRequest(
        keepalive=pb2.KeepAliveCommand())
    assert IS._outbound(ok_sub) is ok_sub
    assert IS._outbound(ok_ka) is ok_ka
    unsub = pb2.BiDirectionalStreamMarketDataRequest(
        unsubscribe=pb2.UnsubscribeCommand(symbols=["x"]))
    for bad in (unsub,
                pb2.BiDirectionalStreamMarketDataRequest(),        # no command
                pb2.BiDirectionalStreamMarketDataRequest(
                    subscribe=pb2.SubscribeCommand(symbols=[])),   # = ALL
                pb2.BiDirectionalStreamMarketDataRequest(
                    subscribe=pb2.SubscribeCommand(symbols=[""]))):
        with pytest.raises(IS.OutboundRefused):
            IS._outbound(bad)


def test_the_builders_refuse_an_empty_symbol_set():
    t = IS.GrpcBidiTransport(IS.ResidentBooks(), lambda: "t",
                             sleep=lambda s: None)
    for empty in ([], None, ["", "  "]):
        with pytest.raises(IS.OutboundRefused):
            t._subscribe_request(empty)
    req = t._subscribe_request(["a"], first=True)
    assert req.WhichOneof("command") == "subscribe" and req.depth == IS.DEPTH
    assert t._keepalive_request().WhichOneof("command") == "keepalive"


def test_the_production_channel_is_tls_to_the_documented_target():
    assert IS.GRPC_TARGET == "grpc-api.prod.polymarketexchange.com:443"
    src = pathlib.Path(IS.__file__).read_text()
    assert "grpc.secure_channel(self.target, grpc.ssl_channel_credentials()" \
        in src
    assert "insecure_channel" not in src


# ── §5 the identity mapper ──────────────────────────────────────────────

def test_the_identity_mapper_sends_nothing_and_reads_no_book():
    tree = ast.parse(pathlib.Path(ICM.__file__).read_text())
    imported = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            imported |= {a.name for a in n.names}
        if isinstance(n, ast.Import):
            imported |= {a.name for a in n.names}
    assert imported <= {"annotations", "Decimal", "InvalidOperation",
                        "shadow_contract_family", "shadow_identity",
                        "shadow_identity_resolver"}
    called = {n.func.attr for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert not called & {"current", "read", "request", "send", "post", "get_"
                         "book", "put", "subscribe", "want"}
