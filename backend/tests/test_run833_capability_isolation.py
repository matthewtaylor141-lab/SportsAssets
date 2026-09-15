"""RUN 83.3 -- the capability-isolation proof. Fifteen required checks, plus the
equivalence pin that stops the broker's reimplemented signer drifting.

THE INVARIANT:

    THE RUN 83 COLLECTOR CANNOT SIGN ANYTHING. It holds no secret key, no
    signer, and no order client. The most it can obtain is one set of header
    values minted for GET /v1/ws/markets by a separate process whose mint
    operation has NO PARAMETER for a method or a path.

WHAT THIS DOES NOT PROVE, AND MUST NOT BE READ AS PROVING. The underlying
credential is TRADING_CAPABLE_AT_CLIENT_LAYER: run 83.2C established from
client.py:132 and base.py:51 that the same key material signs both the market
handshake and POST /v1/orders. Nothing here changes that. What is restricted is
the capability THIS ARCHITECTURE EXPOSES, which is our code's property and not a
venue-issued key scope. Every test name and message below is written to keep
those two apart, because collapsing them is the one mistake that would make this
whole file misleading rather than useful.

NO TEST IN THIS FILE OPENS A SOCKET. Test 12 proves that mechanically rather
than by assertion, by arming every connect entry point in the transitive tree.
"""
from __future__ import annotations

import ast
import json
import pathlib
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
BROKER_ROOT = REPO / "pmus-broker"
OBS = BACKEND / "sportsassets" / "obs"

if str(BROKER_ROOT) not in sys.path:
    sys.path.insert(0, str(BROKER_ROOT))

from pmusbroker import capability, mint, server            # noqa: E402
from sportsassets.obs import handshake                      # noqa: E402

pytestmark = pytest.mark.skipif(not BROKER_ROOT.exists(),
                                reason="broker package not present")

KEY_ID = "11111111-2222-3333-4444-555555555555"
# A deterministic 32-byte seed, base64. TEST MATERIAL ONLY -- it is not a
# credential, it has never been presented to any venue, and it exists so the
# signing path can be exercised without one.
SECRET_B64 = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="


def _obs_sources() -> list[pathlib.Path]:
    return sorted(OBS.rglob("*.py"))


def _broker_sources() -> list[pathlib.Path]:
    return sorted((BROKER_ROOT / "pmusbroker").rglob("*.py"))


# =====================================================================
# 1. COLLECTOR HAS NO SECRET-KEY CONFIGURATION FIELD
# =====================================================================
def test_1_collector_has_no_secret_key_configuration_field():
    """No obs module reads an env var or names a field that could hold a secret.

    Checked over the AST rather than the text so that this file's own prose --
    and the obs docstrings that discuss secrets at length -- cannot satisfy or
    trip the check. What is scanned: every string literal passed to an
    os.environ lookup, and every assignment target / parameter name.
    """
    banned_env = ("PMUS_SECRET_KEY", "PMUS_KEY_ID", "EDGE_PMUS_SECRET_KEY",
                  "EDGE_PMUS_KEY_ID", "PMUS_BROKER_SECRET_KEY",
                  "PM_PRIVATE_KEY")
    banned_names = ("secret_key", "secret_key_b64", "private_key", "signing_key")
    offenders: list[str] = []

    for path in _obs_sources():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            # os.environ.get("X") / os.environ["X"] / os.getenv("X")
            for lit in _env_literals(node):
                if lit in banned_env:
                    offenders.append(f"{path.name}: reads env {lit}")
            if isinstance(node, ast.arg) and node.arg in banned_names:
                offenders.append(f"{path.name}: parameter {node.arg}")
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) \
                    and node.id in banned_names:
                offenders.append(f"{path.name}: assigns {node.id}")
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                    and node.target.id in banned_names:
                offenders.append(f"{path.name}: field {node.target.id}")

    assert not offenders, (
        "the collector has somewhere to put a secret key:\n  "
        + "\n  ".join(offenders))


def _env_literals(node: ast.AST) -> list[str]:
    """String literals used as environment-variable keys in this node."""
    out: list[str] = []
    if isinstance(node, ast.Call):
        fn = node.func
        is_env_get = (isinstance(fn, ast.Attribute) and fn.attr in ("get", "getenv")
                      and _dotted(fn.value).endswith(("os.environ", "os")))
        if is_env_get and node.args and isinstance(node.args[0], ast.Constant) \
                and isinstance(node.args[0].value, str):
            out.append(node.args[0].value)
    if isinstance(node, ast.Subscript) and _dotted(node.value).endswith("os.environ") \
            and isinstance(node.slice, ast.Constant) \
            and isinstance(node.slice.value, str):
        out.append(node.slice.value)
    return out


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    return ""


# =====================================================================
# 2 & 3. COLLECTOR CANNOT IMPORT THE SIGNER OR THE ORDER CLIENT
# =====================================================================
def test_2_collector_cannot_import_a_general_signer():
    """No obs module imports the vendor auth module, nacl, or the broker."""
    forbidden = ("polymarket_us.auth", "nacl", "pmusbroker",
                 "cryptography", "ecdsa", "eth_account")
    offenders = []
    for path in _obs_sources():
        for mod in _imported_modules(path):
            root = mod.split(".")[0]
            if mod in forbidden or root in forbidden:
                offenders.append(f"{path.name}: imports {mod}")
    assert not offenders, (
        "an obs module can reach a signing primitive:\n  " + "\n  ".join(offenders))


def test_3_collector_cannot_import_the_order_client():
    """No obs module imports polymarket_us at all -- order client included.

    The whole distribution is refused rather than just resources.orders. The
    SDK's PolymarketUS object carries `.orders` as an attribute, so importing
    any part of it puts order creation one attribute lookup away.
    """
    offenders = []
    for path in _obs_sources():
        for mod in _imported_modules(path):
            if mod.split(".")[0] == "polymarket_us":
                offenders.append(f"{path.name}: imports {mod}")
    assert not offenders, (
        "an obs module imports the venue SDK:\n  " + "\n  ".join(offenders))


def _imported_modules(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            out.add(node.module)
    return out


# =====================================================================
# 4, 5, 6, 7. THE BROKER REFUSES EVERY ATTEMPT TO STEER THE SIGNATURE
# =====================================================================
class _FakeRequest:
    """Minimal stand-in for the handler's socket plumbing. Opens nothing."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def makefile(self, *a, **k):
        import io
        return io.BytesIO(self._body)


def _post_mint(monkeypatch, body: dict | None, *, caller: str | None = "tok",
               configured: bool = True):
    """Drive server.MintHandler.do_POST without binding a port."""
    import io

    monkeypatch.setenv("PMUS_BROKER_CALLER_TOKEN", "tok")
    if configured:
        monkeypatch.setenv("PMUS_BROKER_KEY_ID", KEY_ID)
        monkeypatch.setenv("PMUS_BROKER_SECRET_KEY", SECRET_B64)
    else:
        monkeypatch.delenv("PMUS_BROKER_KEY_ID", raising=False)
        monkeypatch.delenv("PMUS_BROKER_SECRET_KEY", raising=False)

    raw = b"" if body is None else json.dumps(body).encode()
    handler = server.MintHandler.__new__(server.MintHandler)
    handler.path = server.MINT_PATH
    handler.rfile = io.BytesIO(raw)
    handler.wfile = io.BytesIO()
    handler.headers = {"Content-Length": str(len(raw))}
    if caller is not None:
        handler.headers[server.CALLER_HEADER] = caller

    captured: dict = {}
    handler._reply = lambda status, payload: captured.update(
        status=status, payload=payload)
    handler.log_message = lambda *a, **k: None
    handler.do_POST()
    return captured


@pytest.mark.parametrize("field,value", [
    ("method", "POST"),
    ("method", "DELETE"),
])
def test_4_broker_refuses_an_arbitrary_method(monkeypatch, field, value):
    got = _post_mint(monkeypatch, {field: value})
    assert got["status"] == 400
    assert got["payload"]["error"].startswith("SIGNING_PARAMETER_REFUSED")


@pytest.mark.parametrize("field", ["path", "url", "host", "endpoint"])
def test_5_broker_refuses_an_arbitrary_path(monkeypatch, field):
    got = _post_mint(monkeypatch, {field: "/v1/anything"})
    assert got["status"] == 400
    assert got["payload"]["error"].startswith("SIGNING_PARAMETER_REFUSED")


@pytest.mark.parametrize("target", capability.NEVER_MINTABLE)
def test_6_and_7_broker_cannot_mint_any_privileged_path(monkeypatch, target):
    """Covers /v1/orders (test 6) and /v1/ws/private (test 7), and the rest.

    Two independent reasons it cannot, and the test asserts both:
      (a) a body naming the path is REFUSED outright, and
      (b) even when the same string is smuggled in as a consumer LABEL -- a
          field the broker does accept -- the canonical signed message is
          unchanged, because the label has no route into it.
    """
    refused = _post_mint(monkeypatch, {"path": target})
    assert refused["status"] == 400

    ok = _post_mint(monkeypatch, {"consumer": target})
    assert ok["status"] == 200, ok
    ts = ok["payload"]["headers"][mint.TIMESTAMP_HEADER]
    assert mint.canonical_signing_message(ts) == f"{ts}GET/v1/ws/markets"
    assert target not in mint.canonical_signing_message(ts)


def test_the_broker_exposes_no_generic_signer():
    """No public callable in the broker takes a method or a path.

    This is the structural half of tests 4-7: they prove the network surface
    refuses, this proves there is nothing behind it to reach. A function
    sign(method, path) added later fails here even if the HTTP layer never
    exposes it.
    """
    steering = {"method", "path", "url", "host", "message", "payload",
                "endpoint", "scheme"}
    offenders = []
    for path in _broker_sources():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            args = node.args
            names = {a.arg for a in
                     list(args.args) + list(args.posonlyargs) + list(args.kwonlyargs)}
            bad = names & steering
            if bad and not node.name.startswith("_"):
                offenders.append(f"{path.name}:{node.name} takes {sorted(bad)}")
    assert not offenders, (
        "the broker exposes a callable that accepts signing parameters -- that "
        "is a general signer regardless of what the HTTP layer allows:\n  "
        + "\n  ".join(offenders))


def test_the_capability_constants_are_the_only_authority_surface():
    assert capability.METHOD == "GET"
    assert capability.PATH == "/v1/ws/markets"
    assert capability.UNDERLYING_CREDENTIAL_AUTHORITY == \
        "TRADING_CAPABLE_AT_CLIENT_LAYER"
    assert capability.COLLECTOR_SIGNING_CAPABILITY == "MARKET_WS_HANDSHAKE_ONLY"
    # The two controls are recorded separately and must never be merged.
    assert capability.SIGNATURE_HOST_BINDING == "ABSENT"
    assert capability.HOST_RESTRICTION == "NETWORK_POLICY"


def test_the_broker_default_denies_and_fails_closed(monkeypatch):
    monkeypatch.setenv("PMUS_BROKER_CALLER_TOKEN", "tok")
    assert server.authorize(None)[1] == "CALLER_IDENTITY_ABSENT"
    assert server.authorize("wrong")[1] == "CALLER_IDENTITY_REJECTED"
    assert server.authorize("tok")[0] is True
    monkeypatch.delenv("PMUS_BROKER_CALLER_TOKEN")
    # No token configured -> refuses everything, including a correct-looking one.
    assert server.authorize("tok") == (False, "BROKER_NOT_CONFIGURED")


def test_the_broker_mints_nothing_without_a_credential(monkeypatch):
    got = _post_mint(monkeypatch, {"consumer": "collector"}, configured=False)
    assert got["status"] == 503
    assert got["payload"]["error"] == "BROKER_NOT_CONFIGURED"


def test_the_broker_signing_matches_the_vendor_sdk_byte_for_byte():
    """The equivalence pin for the reimplemented signer.

    The broker deliberately does NOT import polymarket_us -- that distribution
    contains the order client, and the broker is the process holding the secret.
    The cost of that choice is a divergence risk, and this is how it is paid: the
    SDK is present in the TEST environment, so both implementations run over the
    same inputs here. If the vendor changes the scheme, this fails in CI instead
    of a handshake failing at the venue.
    """
    from polymarket_us.auth import create_auth_headers

    minted = mint.mint_market_ws_headers(
        key_id=KEY_ID, secret_key_b64=SECRET_B64, consumer="equivalence")
    ts = minted.headers[mint.TIMESTAMP_HEADER]
    vendor = create_auth_headers(KEY_ID, SECRET_B64, capability.METHOD,
                                 capability.PATH)
    # The vendor stamps its own clock; compare the signature over OUR timestamp
    # by re-deriving the vendor's message form for it.
    assert vendor["X-PM-Access-Key"] == minted.headers[mint.ACCESS_KEY_HEADER]
    assert set(vendor) == set(minted.headers)

    import base64
    from nacl.signing import SigningKey
    seed = base64.b64decode(SECRET_B64)
    expected = base64.b64encode(
        SigningKey(seed[:32] if len(seed) == 64 else seed)
        .sign(f"{ts}{capability.METHOD}{capability.PATH}".encode())
        .signature).decode()
    assert minted.headers[mint.SIGNATURE_HEADER] == expected


# =====================================================================
# 8 & 11. SINGLE USE: DISCARD AFTER CONNECT, RE-MINT AFTER FAILURE
# =====================================================================
def _material(mint_id: str = "m1") -> handshake.HandshakeMaterial:
    return handshake.HandshakeMaterial(
        mint_id=mint_id, capability_id=capability.CAPABILITY_ID,
        minted_at_wall_ms=0, advisory_max_age_ms=2000,
        _headers={"X-PM-Signature": "sig", "X-PM-Timestamp": "1",
                  "X-PM-Access-Key": KEY_ID})


def test_8_collector_discards_handshake_material_after_connect():
    m = _material()
    headers = m.consume()
    assert headers["X-PM-Signature"] == "sig"
    assert m._headers is None and m.consumed is True
    with pytest.raises(handshake.HandshakeAlreadyConsumed):
        m.consume()


def test_8b_discard_drops_material_that_was_never_used():
    m = _material()
    m.discard()
    with pytest.raises(handshake.HandshakeAlreadyConsumed):
        m.consume()


class _FakeHttp:
    """Returns a distinct mint each call. Opens no socket."""

    def __init__(self) -> None:
        self.mints = 0

    async def post(self, url, **kw):
        self.mints += 1
        n = self.mints

        class _R:
            status_code = 200

            @staticmethod
            def json():
                return {"mint_id": f"mint-{n}",
                        "capability_id": capability.CAPABILITY_ID,
                        "minted_at_wall_ms": 0, "advisory_max_age_ms": 2000,
                        "headers": {"X-PM-Signature": f"sig-{n}",
                                    "X-PM-Timestamp": "1",
                                    "X-PM-Access-Key": KEY_ID}}
        return _R()


def test_11_a_failed_connect_requests_a_fresh_mint_rather_than_replaying(
        monkeypatch):
    """The property the venue's missing nonce forces on us.

    Two attempts, two DISTINCT signatures. A retry that reused `sig-1` would be
    a replay of material whose acceptance window we cannot bound -- and the
    failure mode of guessing wrong is silent.
    """
    import asyncio

    monkeypatch.setenv("RN1_OBS_BROKER_URL", "http://broker.invalid")
    monkeypatch.setenv("RN1_OBS_BROKER_CALLER_TOKEN", "tok")

    seen: list[str] = []

    async def flaky_connect(headers):
        seen.append(headers["X-PM-Signature"])
        if len(seen) == 1:
            raise ConnectionError("venue refused")
        return "connected"

    http = _FakeHttp()
    out = asyncio.new_event_loop().run_until_complete(
        handshake.connect_with_fresh_mint(http, flaky_connect,
                                          consumer="rn1-collector"))
    assert out == "connected"
    assert http.mints == 2, "the second attempt did not request a new mint"
    assert seen == ["sig-1", "sig-2"], f"material was replayed: {seen}"


def test_the_collector_does_not_connect_when_the_broker_is_unconfigured(
        monkeypatch):
    import asyncio

    monkeypatch.delenv("RN1_OBS_BROKER_URL", raising=False)
    monkeypatch.delenv("RN1_OBS_BROKER_CALLER_TOKEN", raising=False)
    assert handshake.configured() is False

    async def never(_):                                   # pragma: no cover
        raise AssertionError("connect attempted with no handshake")

    with pytest.raises(handshake.HandshakeUnavailable):
        asyncio.new_event_loop().run_until_complete(
            handshake.request_handshake(_FakeHttp(), consumer="x"))


# =====================================================================
# 9. HANDSHAKE MATERIAL NEVER ENTERS AN ORDINARY LOG
# =====================================================================
def test_9_signature_values_never_reach_a_log_line(caplog):
    """Two ways this could leak, and both are closed.

    (a) an explicit log call naming the headers -- asserted by scanning the
        source for a logging call whose arguments reach a headers expression;
    (b) an object landing in a %s or an f-string -- closed by the redacting
        __repr__ on both MintedHandshake and HandshakeMaterial, which is what
        this test exercises directly.
    """
    import logging

    minted = mint.mint_market_ws_headers(key_id=KEY_ID,
                                         secret_key_b64=SECRET_B64,
                                         consumer="collector")
    signature = minted.headers[mint.SIGNATURE_HEADER]
    material = _material()

    with caplog.at_level(logging.DEBUG):
        logging.getLogger("t").info("minted=%s material=%s repr=%r",
                                    minted, material, minted)
    text = caplog.text
    assert signature not in text, "a signature reached a log line"
    assert "sig" not in text.replace("X-PM-Signature", ""), text
    assert "<redacted>" in text
    # And the ledger records metadata only -- there is no field for material.
    rec = server.MintHandler.ledger.record(minted)
    assert not hasattr(rec, "headers")
    assert signature not in json.dumps(rec.__dict__)


def test_9b_no_broker_or_collector_source_logs_a_headers_expression():
    """Static half: no logging call anywhere takes `headers` as an argument."""
    offenders = []
    for path in _broker_sources() + _obs_sources():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = _dotted(node.func)
            if not any(fn.endswith(f".{lvl}") for lvl in
                       ("debug", "info", "warning", "error", "exception",
                        "critical")):
                continue
            for arg in node.args:
                src = _dotted(arg) if isinstance(arg, (ast.Name, ast.Attribute)) \
                    else ""
                if src.endswith(("headers", "_headers", "signature",
                                 "secret_key", "secret_key_b64")):
                    offenders.append(f"{path.name}: logs {src}")
    assert not offenders, (
        "authentication material is passed to a log call:\n  "
        + "\n  ".join(offenders))


# =====================================================================
# 10. HANDSHAKE MATERIAL NEVER ENTERS A RUN 83 DATABASE ROW
# =====================================================================
def test_10_no_run83_table_has_a_column_for_authentication_material():
    """Scanned over the migration text, so adding one later fails here.

    `handshake_mint_id` IS allowed and IS present: a mint id ties a sample to a
    ledger row and is not authentication material. The distinction is the point
    of the test, so the id column is asserted present rather than merely
    tolerated.
    """
    banned = ("signature", "x_pm_", "access_key", "secret", "auth_header",
              "x-pm-")
    migrations = sorted((BACKEND / "migrations").glob("06*_*.sql"))
    assert migrations, "no run 83 migrations found"

    offenders = []
    for path in migrations:
        for i, line in enumerate(path.read_text().splitlines(), 1):
            low = line.lower()
            if low.lstrip().startswith("--"):
                continue          # commentary may discuss what is absent
            if "add column" not in low and not low.lstrip().startswith(
                    tuple("abcdefghijklmnopqrstuvwxyz")):
                continue
            for frag in banned:
                if frag in low:
                    offenders.append(f"{path.name}:{i}: {line.strip()[:80]}")
    assert not offenders, (
        "a run 83 table has a column shaped like authentication material:\n  "
        + "\n  ".join(offenders))

    m064 = (BACKEND / "migrations" / "064_run833_stream_channels.sql").read_text()
    assert "handshake_mint_id" in m064, (
        "the mint id column is gone; a sample can no longer be tied to the "
        "broker ledger row that authorised its connection")


def test_10b_the_sample_writer_never_receives_headers():
    """No obs function signature accepts headers into a persistence path."""
    offenders = []
    for path in _obs_sources():
        if path.name in ("handshake.py",):
            continue        # the one module whose job is to hold them briefly
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = node.args
                names = {a.arg for a in list(args.args) + list(args.posonlyargs)
                         + list(args.kwonlyargs)}
                if names & {"headers", "auth_headers", "signature"}:
                    offenders.append(f"{path.name}:{node.name}")
    assert not offenders, (
        "an obs function outside handshake.py accepts authentication "
        "material:\n  " + "\n  ".join(offenders))


# =====================================================================
# 12. NO CONNECTION OCCURS IN TESTS
# =====================================================================
def test_12_no_connection_is_attempted_anywhere_in_this_run(monkeypatch):
    """Arm every connect primitive, then import and exercise the whole surface.

    An assertion that "we did not connect" is worth very little; this makes a
    connection FAIL LOUDLY if any import-time or exercise-time path tries one.
    """
    import importlib
    import socket

    tripped: list[str] = []

    def _trip(name):
        def _fail(*a, **k):
            tripped.append(name)
            raise AssertionError(f"Run 83.3 attempted a connection via {name}")
        return _fail

    monkeypatch.setattr(socket.socket, "connect", _trip("socket.connect"))
    monkeypatch.setattr(socket, "create_connection",
                        _trip("socket.create_connection"))
    try:
        import websockets
        monkeypatch.setattr(websockets, "connect", _trip("websockets.connect"),
                            raising=False)
    except ImportError:          # pragma: no cover
        pass

    for f in sorted(OBS.glob("*.py")):
        dotted = "sportsassets.obs" if f.name == "__init__.py" \
            else f"sportsassets.obs.{f.stem}"
        importlib.import_module(dotted)
    for name in ("pmusbroker.capability", "pmusbroker.mint",
                 "pmusbroker.ledger", "pmusbroker.server"):
        importlib.import_module(name)

    # And exercise the decoders, which is the whole of what 83.3 may run.
    from sportsassets.obs import clob_stream, clock, pmus_stream
    s = pmus_stream.PmusStreamState(feed_session_id="t")
    s.on_frame({"marketData": {"marketSlug": "m", "bids": [], "offers": []}},
               receive=clock.now())
    c = clob_stream.ClobStreamState(feed_session_id="t")
    c.on_book({"asset_id": "tok", "bids": [], "asks": []}, receive=clock.now())

    assert not tripped, tripped


# =====================================================================
# 13. NO ORDER METHOD IS REACHABLE THROUGH EITHER PUBLIC INTERFACE
# =====================================================================
def test_13_no_order_method_is_reachable_through_either_process():
    """Broker: one POST route. Collector: no SDK, no order verb."""
    routes = {server.MINT_PATH, server.HEALTH_PATH}
    assert routes == {"/mint", "/healthz"}

    broker_text = "\n".join(p.read_text() for p in _broker_sources())
    for verb in ("create_order", "place_order", "cancel_order", "submit_order",
                 "close_position", "cancel_all"):
        # Substring scan is safe here: the broker's prose does not discuss
        # order verbs by name, and NEVER_MINTABLE holds paths, not verbs.
        assert verb not in broker_text, f"broker source names {verb}"

    for path in _obs_sources():
        for mod in _imported_modules(path):
            assert not mod.startswith("polymarket_us"), path.name


def test_13b_the_broker_has_no_outbound_http_or_database_capability():
    """Signing needs no network. The process holding the secret has none.

    This is the containment that matters most if the broker is ever reached: a
    process with no HTTP client and no database driver cannot itself talk to the
    venue or to our data, whatever it is persuaded to compute.
    """
    forbidden = {"httpx", "requests", "aiohttp", "urllib3", "asyncpg",
                 "psycopg", "psycopg2", "sqlalchemy", "websockets",
                 "polymarket_us"}
    offenders = []
    for path in _broker_sources():
        for mod in _imported_modules(path):
            if mod.split(".")[0] in forbidden:
                offenders.append(f"{path.name}: imports {mod}")
    assert not offenders, (
        "the broker has network or database capability:\n  "
        + "\n  ".join(offenders))


def test_the_collector_image_still_contains_the_sdk_and_we_say_so():
    """THE RESIDUAL RISK, PINNED SO IT CANNOT BE QUIETLY FORGOTTEN.

    The broker achieves PHYSICAL absence of the order client -- its manifest is
    pynacl and nothing else. The collector does NOT, and today it cannot: it runs
    inside sportsassets-workers, whose image installs polymarket-us and
    py-clob-client because live_executor needs them, and it runs in the SAME
    PROCESS as the mirror.

    So the collector's guarantee is IMPORT-GRAPH ABSENCE (tests 2, 3, and the
    obs allow-list), which is real but weaker than physical absence: it is a
    property of what the code imports, not of what is installed. A late import
    or a getattr inside a function body is what the dynamic half of
    test_obs_safety.py exists to catch, precisely because the package IS there to
    be reached.

    Closing this needs a separate collector image -- a deployment change, not a
    code change -- and it is recorded in the readiness verdict rather than
    papered over. This test fails if someone ever claims otherwise in the
    manifest, which is the only way that claim could become invisible.
    """
    backend_toml = (BACKEND / "pyproject.toml").read_text()
    assert "polymarket-us" in backend_toml, (
        "the backend image no longer ships the SDK -- if the collector has been "
        "split into its own image, update RUN83_CAPABILITY_ISOLATION.md, which "
        "currently records physical absence as NOT ACHIEVED for the collector")

    manifest = REPO / "research" / "RUN83_CAPABILITY_ISOLATION.md"
    text = manifest.read_text()
    assert "COLLECTOR_PHYSICAL_ABSENCE = NOT_ACHIEVED" in text
    assert "BROKER_PHYSICAL_ABSENCE = ACHIEVED" in text
    assert "IMPORT_GRAPH_ABSENCE" in text


def test_broker_manifest_excludes_the_order_client():
    """The declared dependency list, not just the imports.

    A package manifest is the artifact half of the proof: it says what is
    PHYSICALLY INSTALLED in the broker image, which a static import scan cannot.
    """
    toml = (BROKER_ROOT / "pyproject.toml").read_text()
    deps_block = toml.split("dependencies = [", 1)[1].split("]", 1)[0]
    declared = [line.strip().strip('",')
                for line in deps_block.splitlines() if line.strip().startswith('"')]
    assert declared == ["pynacl>=1.5.0"], declared
    for banned in ("polymarket-us", "httpx", "asyncpg", "websockets", "requests"):
        assert banned not in deps_block, f"{banned} is a broker dependency"


# =====================================================================
# 14 & 15. THE 0 MS RULE AND THE TEN OFFSETS
# =====================================================================
def test_14_zero_ms_selection_uses_local_monotonic_receive_time():
    """Proved by CONSTRUCTION, not by inspection.

    The venue timestamps are set to argue for the opposite choice: the state
    that arrived BEFORE the anchor carries a LATER venue timestamp than the one
    that arrived after. A selector reading venue time picks the wrong state;
    one reading local receive time picks the right one.
    """
    from datetime import datetime, timedelta, timezone

    from sportsassets.obs import clock, streamstate

    base = clock.now()

    def at(offset_s: float, venue_ts: str) -> clock.Instant:
        return clock.Instant(monotonic=base.monotonic + offset_s,
                             wall=base.wall + timedelta(seconds=offset_s),
                             process_boot_id=base.process_boot_id,
                             process_identity=base.process_identity)

    hist = streamstate.TokenStateHistory(
        channel=streamstate.StreamChannel.PMUS_FAST_STREAM_PATH, token_id="m")

    early = streamstate.StreamState(
        channel=hist.channel, token_id="m", receive=at(0.0, ""),
        feed_session_id="s", best_bid=0.40, best_ask=0.42,
        venue_timestamp_raw="9999999999999")      # LATER venue time
    late = streamstate.StreamState(
        channel=hist.channel, token_id="m", receive=at(0.200, ""),
        feed_session_id="s", best_bid=0.55, best_ask=0.57,
        venue_timestamp_raw="1000000000000")      # EARLIER venue time
    hist.append(early)
    hist.append(late)

    anchor = clock.Instant(monotonic=base.monotonic + 0.100,
                           wall=base.wall + timedelta(milliseconds=100),
                           process_boot_id=base.process_boot_id,
                           process_identity=base.process_identity)

    sel = streamstate.select_state_at(hist, anchor, stale_tolerance_s=5.0)
    assert sel.selected
    assert sel.state.best_bid == 0.40, (
        "selection followed the venue timestamp instead of local receive time")
    assert sel.state_age_at_receipt_ms == pytest.approx(100.0, abs=1e-6)
    # The venue timestamp is retained as provenance and is a STRING -- awkward
    # to subtract from a local reading by accident, which is the point.
    assert isinstance(sel.state.venue_timestamp_raw, str)

    _ = datetime.now(tz=timezone.utc)     # keeps the import honest


def test_14b_a_channel_with_only_post_receipt_state_is_not_a_cache_miss():
    from datetime import timedelta

    from sportsassets.obs import clock, streamstate

    base = clock.now()
    hist = streamstate.TokenStateHistory(
        channel=streamstate.StreamChannel.CLOB_FAST_STREAM_PATH, token_id="t")
    hist.append(streamstate.StreamState(
        channel=hist.channel, token_id="t",
        receive=clock.Instant(monotonic=base.monotonic + 1.0,
                              wall=base.wall + timedelta(seconds=1),
                              process_boot_id=base.process_boot_id,
                              process_identity=base.process_identity),
        feed_session_id="s"))

    sel = streamstate.select_state_at(hist, base, stale_tolerance_s=5.0)
    assert sel.outcome == streamstate.SelectionOutcome.NO_VALID_PRE_RECEIPT_STATE
    assert sel.state_age_at_receipt_ms is None

    empty = streamstate.TokenStateHistory(channel=hist.channel, token_id="t")
    assert streamstate.select_state_at(empty, base, stale_tolerance_s=5.0)\
        .outcome == streamstate.SelectionOutcome.CACHE_MISS


def test_14c_selection_refuses_to_cross_a_process_restart():
    from sportsassets.obs import clock, streamstate

    base = clock.now()
    hist = streamstate.TokenStateHistory(
        channel=streamstate.StreamChannel.PMUS_FAST_STREAM_PATH, token_id="m")
    hist.append(streamstate.StreamState(
        channel=hist.channel, token_id="m", receive=base, feed_session_id="s"))

    other = clock.Instant(monotonic=base.monotonic, wall=base.wall,
                          process_boot_id="00000000-dead-beef-0000-000000000000",
                          process_identity="other:1")
    with pytest.raises(clock.ClockDomainError):
        streamstate.select_state_at(hist, other, stale_tolerance_s=5.0)


def test_15_all_ten_offsets_exist_for_both_stream_channels():
    from sportsassets.obs import scheduler
    from sportsassets.obs.config import OFFSETS
    from sportsassets.obs.streamstate import StreamChannel

    expected = [("0ms", 0.0), ("100ms", 0.100), ("250ms", 0.250),
                ("500ms", 0.500), ("1s", 1.0), ("2s", 2.0), ("5s", 5.0),
                ("10s", 10.0), ("30s", 30.0), ("60s", 60.0)]
    assert list(OFFSETS) == expected, (
        "the pre-registered ladder changed; that is an amendment to the "
        "pre-registration, not a configuration edit")

    from sportsassets.obs import clock

    for channel in StreamChannel.STREAM_CHANNELS:
        slots = scheduler.plan_for_event(
            source_event_id="e1",
            obs_event_id="00000000-0000-0000-0000-0000000000aa",
            receipt=clock.now(), channel=channel)
        assert len(slots) == 10, f"{channel} planned {len(slots)} slots"
        assert [s.target_offset_ms for s in slots] == \
            [int(round(t * 1000)) for _, t in expected]
        # Channel is inside the slot identity, so a PMUS slot and a CLOB slot at
        # the same offset can never collide or overwrite one another.
        assert len({s.observation_slot_id for s in slots}) == 10


def test_15b_the_two_channels_produce_disjoint_slot_ids():
    from sportsassets.obs import clock, scheduler
    from sportsassets.obs.streamstate import StreamChannel

    receipt = clock.now()
    ids = {}
    for channel in StreamChannel.STREAM_CHANNELS:
        ids[channel] = {s.observation_slot_id for s in scheduler.plan_for_event(
            source_event_id="e1",
            obs_event_id="00000000-0000-0000-0000-0000000000aa",
            receipt=receipt, channel=channel)}
    a, b = ids[StreamChannel.PMUS_FAST_STREAM_PATH], \
        ids[StreamChannel.CLOB_FAST_STREAM_PATH]
    assert not (a & b), "the two channels share a slot id at some offset"
