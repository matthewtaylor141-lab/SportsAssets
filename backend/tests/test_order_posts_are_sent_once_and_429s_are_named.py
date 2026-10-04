"""ORDER CLIENTS ADD NO HIDDEN RETRY; A 429 IS RAISED BY NAME AND TRIPS THE
CIRCUIT; AN AMBIGUOUS POST IS NEVER RE-SENT (program section 21's
capital-critical behaviours).

Every assertion here is about REQUESTS ON THE WIRE, counted inside an
`httpx.MockTransport` under the INSTALLED venue SDK (pinned 1.0.2) -- not about
what a source file says. The SDK is the shipped build, its retry loop and its
error mapping are its own; only the socket is replaced. No credential (a
throwaway all-zero Ed25519 seed the signer accepts and no venue would), no
network, and no order reaches anything but the mock.

  1  THE SDK ITSELF never retries a POST, even at its own default
     (max_retries=2): 429, 5xx, a read timeout after the request left and a
     connection dropped mid-response each make exactly ONE dispatch -- while
     the same default DOES retry a GET, which is why every client below must
     turn it off.
  2  EVERY CLIENT THIS REPOSITORY BUILDS FROM AN ORDER CREDENTIAL is built
     with the SDK's retries off (`venue_sdk.client_kwargs()`): the funded /
     copy adapter (pmus), the execution mirror's venue (execmirror.Venue, the
     state machine the SMALL LIVE adapter would drive) and its read-only probe
     reader. A 429 on any of them is one dispatch and a named RateLimitError.
     execmirror.Venue was built with max_retries=1 until R30A (fixed with the
     first test of this file failing on it).
  3  A 429 at the execution mirror's venue trips venue_pace's shared circuit
     and the lane's own pacing honours the doubled gap.
  4  pmus.submit_fok -- the one adapter every funded / copy / desk order goes
     through -- sends exactly ONE create per call whatever the venue answers,
     and RAISES the venue's answer by name (RateLimitError, InternalServerError,
     APITimeoutError, APIConnectionError) for its caller's lost-response
     handling; it never swallows an ambiguous outcome into a retry.
  5  The order adapters contain no loop around their one create / close
     (static), and no module anywhere builds the SDK with retries switched on.
  6  Kalshi's transport is a plain requests.Session: no retry adapter.

THE COPY LANE'S OWN 429 HANDLING (workers/mirror_live) and the protected
worker's listing budget (test_bettor_live_loop) are proved in their own files
and are not restated here.
"""
from __future__ import annotations

import ast
import base64
import inspect
import pathlib
import threading

import httpx
import pytest

from sportsassets import execmirror as M
from sportsassets import execmirror_probe as EP
from sportsassets import pmus
from sportsassets import venue_cooldown_store as VCS
from sportsassets import venue_http_observer as VHO
from sportsassets import venue_pace as VP
from sportsassets import venue_request_gate as GRT
from sportsassets import venue_sdk

try:    # pytest collects tests/ as a package; unittest discovery does not
    from tests import admission_fixture as AF
except ImportError:                                           # pragma: no cover
    import admission_fixture as AF

sdk = pytest.importorskip("polymarket_us")
from polymarket_us import errors as SE  # noqa: E402

PKG = pathlib.Path(__file__).resolve().parents[1] / "sportsassets"
SLUG = "aec-mlb-chc-sd-2026-10-04"
#: a syntactically valid Ed25519 seed (32 zero bytes): the signer accepts it,
#: the mock never checks it, and it authenticates against nothing
FAKE_KEY_ID = "00000000-0000-0000-0000-000000000000"
FAKE_SECRET = base64.b64encode(bytes(32)).decode()


class Wire:
    """A scripted venue that records every request that reaches the socket."""

    def __init__(self, script=None, *, default=None):
        self.script = list(script or [])
        self.default = default
        self.seen: list = []
        self.lock = threading.Lock()

    def handler(self, request: httpx.Request) -> httpx.Response:
        with self.lock:
            self.seen.append((request.method, request.url.path))
            step = self.script.pop(0) if self.script else self.default
        if step is None:
            return httpx.Response(418, json={"message": "SCRIPT_EXHAUSTED"},
                                  request=request)
        if isinstance(step, BaseException):
            raise step
        if callable(step):
            return step(request)
        status, body = step
        return httpx.Response(status, json=body, request=request)

    def count(self, method: str, path: str | None = None) -> int:
        return sum(1 for m, p in self.seen
                   if m == method and (path is None or p == path))


def _mock(client, wire: Wire):
    """Replace ONLY the socket under an SDK client."""
    client._http = httpx.Client(transport=httpx.MockTransport(wire.handler))
    return client


def _sdk_client(**kw):
    return sdk.PolymarketUS(key_id=FAKE_KEY_ID, secret_key=FAKE_SECRET,
                            gateway_base_url="https://gateway.test",
                            api_base_url="https://api.test", **kw)


@pytest.fixture(autouse=True)
def _clean_venue_state(monkeypatch):
    """Every process-wide venue control this file can touch, cleared both
    ways, and every `time.sleep` (the SDK's back-off AND the pacers' gaps:
    one module object) recorded instead of slept. The sleep list is asserted
    only where no pacer is installed, so an entry there is the SDK's own."""
    from polymarket_us import client as sdk_client
    slept: list = []
    # `sdk_client.time` IS the process-wide time module: patching its sleep
    # patches every thread's. Only THIS test's thread (where the SDK and the
    # pacers run here) is recorded; any other thread -- a background loop an
    # earlier test left behind -- still really sleeps (in the full suite it
    # otherwise spun, and its 0.25 s gaps landed in this list)
    me = threading.get_ident()
    real_sleep = sdk_client.time.sleep

    def record(s):
        if threading.get_ident() == me:
            slept.append(s)
        else:
            real_sleep(s)
    monkeypatch.setattr(sdk_client.time, "sleep", record)

    def reset():
        GRT.clear_hold()
        VP.resume_cooldown(0)
        VP._penalty_until = 0.0
        VP._penalty_until_epoch = 0.0
        VHO.reset_attempts()
        VHO.reset()
        VCS.reset_pending()
    reset()
    yield slept
    reset()


def _timeout(request):
    raise httpx.ReadTimeout("the response never came", request=request)


def _dropped(request):
    raise httpx.RemoteProtocolError(
        "server disconnected without sending a response", request=request)


ORDER = {"marketSlug": SLUG, "intent": "ORDER_INTENT_BUY_LONG",
         "type": "ORDER_TYPE_LIMIT", "price": {"value": "0.5500", "currency": "USD"},
         "quantity": 3, "tif": "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL",
         "synchronousExecution": True}

AMBIGUOUS = [
    ((429, {"message": "Too Many Requests"}), SE.RateLimitError),
    ((503, {"message": "unavailable"}), SE.InternalServerError),
    (_timeout, SE.APITimeoutError),
    (_dropped, SE.APIConnectionError),
]


# ═══════════════ 1 · THE SDK NEVER RETRIES A POST ═══════════════════════

@pytest.mark.parametrize("answer,raised", AMBIGUOUS,
                         ids=["429", "503", "read-timeout", "dropped"])
def test_the_pinned_sdk_never_retries_a_post_even_at_its_own_default(
        answer, raised, _clean_venue_state):
    wire = Wire([answer, answer, answer])
    client = _mock(_sdk_client(), wire)
    assert client.max_retries == 2                 # the SDK's own default
    with pytest.raises(raised):
        client.orders.create(dict(ORDER))
    assert wire.count("POST", "/v1/orders") == 1, wire.seen
    assert _clean_venue_state == []                # no back-off sleep either


def test_the_same_default_retries_a_get_which_is_why_ours_turn_it_off(
        _clean_venue_state):
    wire = Wire([(429, {}), (429, {}), (429, {})])
    client = _mock(_sdk_client(), wire)
    with pytest.raises(SE.RateLimitError):
        client.orders.retrieve("v1")
    assert wire.count("GET") == 3, wire.seen       # 1 + max_retries 2
    assert len(_clean_venue_state) == 2            # two hidden sleeps
    # ours: exactly one
    wire = Wire([(429, {}), (429, {}), (429, {})])
    ours = _mock(_sdk_client(**venue_sdk.client_kwargs()), wire)
    with pytest.raises(SE.RateLimitError):
        ours.orders.retrieve("v1")
    assert wire.count("GET") == 1


def test_the_installed_build_is_the_one_the_rules_were_read_from():
    v = venue_sdk.verdict()
    assert v["ok"] is True, v["refusals"]
    assert v["sdk_retry_facts"]["post_is_retried"] is False
    assert venue_sdk.client_kwargs() == {"max_retries": 0}


# ═══════════════ 2 · EVERY ORDER-CREDENTIAL CLIENT: RETRIES OFF ═════════

def _execmirror_venue(monkeypatch) -> M.Venue:
    monkeypatch.setenv(EP.KEY_ID_ENV, FAKE_KEY_ID)
    monkeypatch.setenv(EP.SECRET_ENV, FAKE_SECRET)
    return M.Venue()


def _probe_reader():
    return EP._Reader(FAKE_KEY_ID, FAKE_SECRET)


def _pmus_client(monkeypatch):
    class Cfg:
        pmus_key_id = FAKE_KEY_ID
        pmus_secret_key = FAKE_SECRET
    monkeypatch.setattr(pmus, "settings", lambda: Cfg())
    monkeypatch.setattr(pmus, "_client", None, raising=False)
    return pmus._get_client()


def test_the_execution_mirror_venue_is_built_with_the_sdk_retries_off(monkeypatch):
    v = _execmirror_venue(monkeypatch)
    assert v._c.max_retries == 0, (
        "execmirror.Venue built the SDK with max_retries=%s: every GET that "
        "met a 429/5xx/timeout was retried inside the SDK, invisible to the "
        "lane's pacing" % v._c.max_retries)


def test_the_execution_mirror_probe_reader_is_built_with_the_sdk_retries_off():
    assert _probe_reader()._c.max_retries == 0


def test_the_funded_and_copy_adapter_is_built_with_the_sdk_retries_off(monkeypatch):
    c = _pmus_client(monkeypatch)
    assert c.max_retries == 0
    # and its transport carries the not-before gate that counts every dispatch
    assert isinstance(c._http._transport, GRT.PacedTransport)


@pytest.mark.parametrize("build", ["execmirror", "probe", "pmus"])
def test_a_429_on_any_order_credential_client_is_one_dispatch_and_named(
        build, monkeypatch, _clean_venue_state):
    wire = Wire(default=(429, {"message": "Too Many Requests"}))
    if build == "execmirror":
        v = _execmirror_venue(monkeypatch)
        _mock(v._c, wire)
        call = lambda: v.order("v1")                       # noqa: E731
    elif build == "probe":
        r = _probe_reader()
        _mock(r._c, wire)
        call = r.open_orders
    else:
        c = _pmus_client(monkeypatch)
        _mock(c, wire)
        call = lambda: c.orders.retrieve("v1")             # noqa: E731
    with pytest.raises(SE.RateLimitError) as exc:
        call()
    assert exc.value.status_code == 429
    assert len(wire.seen) == 1, wire.seen


# ═══════════════ 3 · THE MIRROR'S 429 TRIPS THE CIRCUIT ═════════════════

def test_a_429_at_the_execution_mirror_venue_trips_the_shared_circuit(monkeypatch):
    v = _execmirror_venue(monkeypatch)
    wire = Wire(default=(429, {"message": "Too Many Requests"}))
    _mock(v._c, wire)
    assert VP.penalty_left() == 0.0
    for call in (lambda: v.order("v1"), lambda: v.cancel("v1", SLUG),
                 lambda: v.open_orders([SLUG]), v.balances,
                 lambda: v.own_trades(SLUG, 0.0)):
        VP._penalty_until = 0.0
        with pytest.raises(SE.RateLimitError):
            call()
        assert VP.penalty_left() > 0, "a 429 left the shared circuit untouched"
    # the lane's own pacing honours the doubled gap while it holds
    assert VP.effective_gap(M.PACE_S) == pytest.approx(M.PACE_S * VP.PENALTY_MULT)
    # every call was one dispatch: five calls, five requests
    assert len(wire.seen) == 5, wire.seen


def test_a_non_429_failure_does_not_trip_the_circuit(monkeypatch):
    v = _execmirror_venue(monkeypatch)
    _mock(v._c, Wire(default=(503, {})))
    with pytest.raises(SE.InternalServerError):
        v.order("v1")
    assert VP.penalty_left() == 0.0


def test_the_execution_mirror_never_reaches_the_wire_with_a_new_order(monkeypatch):
    """SMALL LIVE is SHADOW: the venue's place() refuses before the socket,
    whatever is presented, so nothing is dispatched at all."""
    v = _execmirror_venue(monkeypatch)
    wire = Wire(default=(200, {"id": "should-never-exist"}))
    _mock(v._c, wire)
    with pytest.raises(M.LegacyOriginationRetired):
        v.place(dict(ORDER))
    assert wire.seen == []
    assert M._classify(M.LegacyOriginationRetired("x")) == "REJECTED"
    # and a 429 on the (impossible) create would be UNKNOWN to reconcile,
    # never REJECTED-and-forgotten
    assert M._classify(type("E", (Exception,), {"status_code": 429})()) == "UNKNOWN"


# ═══════════════ 4 · pmus.submit_fok: ONE CREATE PER CALL ═══════════════

def _install_pmus(monkeypatch, wire: Wire, *,
                  canonical_authorization_assumed: bool = True):
    """The installed SDK over the counting wire, behind the adapter's real
    request gate.

    THE ONE-ORIGIN GATE (integration of the R30A intent and chaos streams).
    Since the intent stream, pmus.submit_fok refuses every BUY without the
    canonical SMALL LIVE adapter's LiveAuthorization (require_canonical_
    origination, before the client is built), which SHADOW never issues --
    so on the merged release candidate every BUY below was refused before
    the wire and the send-once / 429 properties were never reached (12
    failures). Those properties are about the ONE create the adapter sends
    once it is authorized, so the authorization is stated as an assumption
    with the intent stream's own test-only helper
    (tests/admission_fixture.assume_canonical_venue_authorization, as in
    tests/test_pmus and tests/test_pmus_post_only); the execution gate, the
    preview guard, the request gate and the transport all still run. The
    refusal itself is proven on this same wire below
    (test_without_the_canonical_authorization_a_buy_never_reaches_the_wire)
    and in tests/test_live_parity_convergence.py §7. A SELL / close is not
    gated and needs no assumption."""
    if canonical_authorization_assumed:
        AF.assume_canonical_venue_authorization(monkeypatch)
    client = _mock(_sdk_client(**venue_sdk.client_kwargs()), wire)
    got = pmus._install_request_gate(client)
    assert got["installed"] is True, got
    monkeypatch.setattr(pmus, "_client", client, raising=False)
    return client


PREVIEW = (200, {"order": {"price": {"value": "0.55"}, "quantity": 3}})


@pytest.mark.parametrize("post_only", [False, True], ids=["ioc", "post_only"])
def test_without_the_canonical_authorization_a_buy_never_reaches_the_wire(
        post_only, monkeypatch, _clean_venue_state):
    """BOTH OWNER REQUIREMENTS HOLD TOGETHER: with no canonical authorization
    (production: SHADOW issues none) a BUY through the real adapter is
    refused by name before ANY request -- no preview, no create -- and the
    shared 429 circuit is untouched. The send-once proofs above and below
    therefore measure the adapter BEHIND the gate, never a path around it."""
    from sportsassets import execution_gate as EG
    wire = Wire(default=(200, {"id": "should-never-exist"}))
    _install_pmus(monkeypatch, wire, canonical_authorization_assumed=False)
    kw = (dict(post_only=True, tif="TIME_IN_FORCE_GOOD_TILL_DATE",
               good_till="2026-10-04T23:00:00Z") if post_only else
          dict(tif="TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"))
    with pytest.raises(EG.Denied) as exc:
        pmus.submit_fok(SLUG, 0.55, 3, intent="ORDER_INTENT_BUY_LONG", **kw)
    assert exc.value.reason == pmus.R_CANONICAL_ORIGINATION
    assert wire.seen == []
    assert VP.penalty_left() == 0.0


@pytest.mark.parametrize("answer,raised", AMBIGUOUS,
                         ids=["429", "503", "read-timeout", "dropped"])
def test_submit_fok_sends_one_create_and_raises_the_answer_by_name(
        answer, raised, monkeypatch, _clean_venue_state):
    wire = Wire([PREVIEW, answer, answer, answer])
    _install_pmus(monkeypatch, wire)
    with pytest.raises(raised):
        pmus.submit_fok(SLUG, 0.55, 3, intent="ORDER_INTENT_BUY_LONG",
                        tif="TIME_IN_FORCE_IMMEDIATE_OR_CANCEL")
    assert wire.count("POST", "/v1/order/preview") == 1
    assert wire.count("POST", "/v1/orders") == 1, wire.seen
    assert len(wire.seen) == 2


def test_submit_fok_post_only_names_a_429_on_its_refusal_and_never_retries(
        monkeypatch, _clean_venue_state):
    """Under post_only (the copy lane's rest) a 4xx is the venue's refusal --
    nothing rests -- and is returned rather than raised. A 429 is one of
    them, so it must arrive NAMED: status_code 429 and error_type
    RateLimitError on the raw, which is what the copy lane's _raw_rate_limit
    reads to back off and trip the circuit (never '429' inside the text). One
    create, never a second."""
    wire = Wire([PREVIEW, (429, {"message": "Too Many Requests"})])
    _install_pmus(monkeypatch, wire)
    out = pmus.submit_fok(SLUG, 0.55, 3, intent="ORDER_INTENT_BUY_LONG",
                          post_only=True, tif="TIME_IN_FORCE_GOOD_TILL_DATE",
                          good_till="2026-10-04T23:00:00Z")
    assert out["ok"] is False and out["status"] == "post_only_rejected"
    assert out["raw"]["status_code"] == 429
    assert out["raw"]["error_type"] == "RateLimitError"
    assert wire.count("POST", "/v1/orders") == 1, wire.seen
    # a 5xx under post_only says nothing about whether the order stands: it
    # is RAISED for the caller's lost-order search, still one create
    wire = Wire([PREVIEW, (503, {"message": "unavailable"})])
    _install_pmus(monkeypatch, wire)
    with pytest.raises(SE.InternalServerError):
        pmus.submit_fok(SLUG, 0.55, 3, intent="ORDER_INTENT_BUY_LONG",
                        post_only=True, tif="TIME_IN_FORCE_GOOD_TILL_DATE",
                        good_till="2026-10-04T23:00:00Z")
    assert wire.count("POST", "/v1/orders") == 1, wire.seen


def test_close_position_sends_one_close_and_names_a_429(monkeypatch,
                                                        _clean_venue_state):
    wire = Wire([(429, {"message": "Too Many Requests"}), (200, {"id": "x"})])
    _install_pmus(monkeypatch, wire)
    out = pmus.close_position(SLUG, slippage_bips=300)
    assert out["ok"] is False and out["status"] == "close_failed"
    assert out["raw"]["status_code"] == 429
    assert out["raw"]["error_type"] == "RateLimitError"
    assert wire.count("POST", "/v1/order/close-position") == 1, wire.seen


# ═══════════════ 4b · A 429 ON ANY ORDER REQUEST TRIPS THE CIRCUIT ══════
#
# R30A REVIEW (MAJOR): section 21 asks that a 429 be raised by name AND trip
# the circuit. The first pass proved and fixed that for execmirror.Venue only.
# pmus.submit_fok -- every funded / copy / desk order -- raised a create's
# 429 by name and left venue_pace untripped (PENALTY_LEFT 0.0 after the 429),
# close_position folded it into `close_failed` the same way, and the funded
# lane's send boundary (bettor_funded_execution boundary 3b) records any
# raised send as a lost acknowledgement without touching the circuit. Only
# the copy worker (workers/mirror_live._rate_limited) tripped it, at its own
# layer. The adapter's transport (venue_request_gate.PacedTransport, which
# every request on the funded credential passes) now trips the shared circuit
# on an order request's 429 -- decided by the response's own status code,
# never a text -- for every caller, and the answer goes back exactly as
# before: raised by name, or (post_only / close) returned as the named
# refusal. submit_fok and close_position themselves are unchanged (their
# sources are pinned by tests/test_e31_maker_only). One request, never a
# second.

RATE_LIMITED = (429, {"message": "Too Many Requests"})


@pytest.mark.parametrize("where", ["preview", "create"])
@pytest.mark.parametrize("post_only", [False, True], ids=["ioc", "post_only"])
def test_a_429_on_submit_fok_trips_the_shared_circuit(where, post_only,
                                                      monkeypatch,
                                                      _clean_venue_state):
    script = [RATE_LIMITED] if where == "preview" else [PREVIEW, RATE_LIMITED]
    wire = Wire(script)
    _install_pmus(monkeypatch, wire)
    assert VP.penalty_left() == 0.0
    kw = (dict(post_only=True, tif="TIME_IN_FORCE_GOOD_TILL_DATE",
               good_till="2026-10-04T23:00:00Z") if post_only else
          dict(tif="TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"))
    if post_only and where == "create":
        out = pmus.submit_fok(SLUG, 0.55, 3, intent="ORDER_INTENT_BUY_LONG", **kw)
        assert out["status"] == "post_only_rejected"
        assert out["raw"]["status_code"] == 429
    else:
        with pytest.raises(SE.RateLimitError):
            pmus.submit_fok(SLUG, 0.55, 3, intent="ORDER_INTENT_BUY_LONG", **kw)
    assert VP.penalty_left() > 0, (
        "a 429 on submit_fok's %s left the shared circuit untouched" % where)
    assert wire.count("POST", "/v1/orders") == (1 if where == "create" else 0)
    assert len(wire.seen) == len(script), wire.seen


def test_a_429_on_close_position_trips_the_shared_circuit(monkeypatch,
                                                          _clean_venue_state):
    wire = Wire([RATE_LIMITED, (200, {"id": "x"})])
    _install_pmus(monkeypatch, wire)
    out = pmus.close_position(SLUG, slippage_bips=300)
    assert out["status"] == "close_failed" and out["raw"]["status_code"] == 429
    assert VP.penalty_left() > 0
    assert wire.count("POST", "/v1/order/close-position") == 1


@pytest.mark.parametrize("answer,raised", AMBIGUOUS[1:],
                         ids=["503", "read-timeout", "dropped"])
def test_an_order_failure_that_is_not_a_429_does_not_trip_the_circuit(
        answer, raised, monkeypatch, _clean_venue_state):
    wire = Wire([PREVIEW, answer])
    _install_pmus(monkeypatch, wire)
    with pytest.raises(raised):
        pmus.submit_fok(SLUG, 0.55, 3, intent="ORDER_INTENT_BUY_LONG",
                        tif="TIME_IN_FORCE_IMMEDIATE_OR_CANCEL")
    assert VP.penalty_left() == 0.0
    # a 400 whose text carries '429' is a refusal, not a rate limit
    wire = Wire([PREVIEW, (400, {"message": "429 contracts exceeds the maximum"})])
    _install_pmus(monkeypatch, wire)
    with pytest.raises(SE.APIStatusError):
        pmus.submit_fok(SLUG, 0.55, 3, intent="ORDER_INTENT_BUY_LONG",
                        tif="TIME_IN_FORCE_IMMEDIATE_OR_CANCEL")
    assert VP.penalty_left() == 0.0


def test_a_read_429_is_left_to_the_read_paths_own_cooldown():
    """The transport trips the circuit for ORDER requests only; a GET's 429
    is armed by pmus.paced_read's own measured cooldown (_arm_cooldown_from),
    unchanged."""
    assert GRT.trip_circuit_on_order_429("GET", "/v1/orders/v1") is False
    assert VP.penalty_left() == 0.0
    assert GRT.trip_circuit_on_order_429("DELETE", "/v1/order/v1/cancel") is True
    assert VP.penalty_left() > 0


def test_the_funded_lane_sends_through_the_adapter_that_trips_the_circuit():
    """The funded lane's acquisitions and exits reach the venue only through
    pmus.submit_fok / pmus.close_position (so a create's 429 trips the shared
    circuit at the adapter); its send boundary keeps the conservative record
    of a raised send (UNRESOLVED / AMBIGUOUS, never resent) unchanged."""
    for mod in ("bettor_funded_execution.py", "bettor_funded_management.py"):
        src = (PKG / mod).read_text()
        tree = ast.parse(src)
        direct = [n.lineno for n in ast.walk(tree)
                  if isinstance(n, ast.Attribute)
                  and n.attr in ("create", "close_position")
                  and isinstance(n.value, ast.Attribute)
                  and n.value.attr == "orders"]
        assert direct == [], (mod, direct)
        assert "submit_fok" in src or "close_position" in src, mod


# ═══════════════ 5 · NO LOOP AROUND A CREATE; NO RETRIES SWITCHED ON ════

def _func(tree, qualname: str):
    parts = qualname.split(".")
    nodes = tree.body
    found = None
    for p in parts:
        found = next(n for n in nodes
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                                       ast.ClassDef)) and n.name == p)
        nodes = found.body
    return found


def _calls_under_a_loop(fn, attr_chain: tuple) -> tuple[int, int]:
    """(references to `...a.b` in fn -- called directly, or handed to the
    adapter's one paced `_call` -- and how many sit inside a for/while)."""
    total = looped = 0

    def chain_of(node):
        names = []
        while isinstance(node, ast.Attribute):
            names.append(node.attr)
            node = node.value
        return tuple(reversed(names))

    def visit(node, in_loop):
        nonlocal total, looped
        if isinstance(node, ast.Attribute) and \
                chain_of(node)[-len(attr_chain):] == attr_chain:
            total += 1
            looped += in_loop
            return
        for child in ast.iter_child_nodes(node):
            visit(child, in_loop or isinstance(
                node, (ast.For, ast.While, ast.AsyncFor)))
    visit(fn, False)
    return total, looped


@pytest.mark.parametrize("module,qualname,chain", [
    ("pmus.py", "submit_fok", ("orders", "create")),
    ("pmus.py", "close_position", ("orders", "close_position")),
    ("execmirror.py", "Venue.place", ("orders", "create")),
    ("execmirror.py", "Venue.close", ("orders", "close_position")),
])
def test_an_order_adapter_has_no_loop_around_its_one_send(module, qualname, chain):
    tree = ast.parse((PKG / module).read_text())
    total, looped = _calls_under_a_loop(_func(tree, qualname), chain)
    assert total >= 1, "the adapter no longer sends through %s" % ".".join(chain)
    assert looped == 0, "%s.%s re-sends inside a loop" % (module, qualname)


def test_kalshi_submit_is_one_send_with_no_loop():
    tree = ast.parse((PKG / "kalshi_venue.py").read_text())
    fn = _func(tree, "KalshiClient.submit")
    total, looped = _calls_under_a_loop(fn, ("_send",))
    assert total == 1 and looped == 0


ORDER_ENDPOINTS = ("create", "cancel", "cancel_all", "close_position")


def sends_orders(tree) -> bool:
    """Does this module reach an order endpoint -- `<x>.orders.create` (and
    cancel, cancel_all, close_position) -- CALLED or HANDED ON as a
    reference? The first pass matched the substring '.orders.create(' with
    its parenthesis, and once execmirror passed `self._c.orders.create` to
    its paced `_call` the scan stopped classifying execmirror.py as an order
    module at all (a review mutation that removed its client_kwargs() still
    passed the scan). Attribute chains, by AST, see both forms."""
    return any(isinstance(n, ast.Attribute) and n.attr in ORDER_ENDPOINTS
               and isinstance(n.value, ast.Attribute) and n.value.attr == "orders"
               for n in ast.walk(tree))


def test_the_order_module_scan_classifies_the_known_order_senders():
    for mod in ("execmirror.py", "pmus.py"):
        assert sends_orders(ast.parse((PKG / mod).read_text())), mod
    # and the reference form the first scan missed
    assert sends_orders(ast.parse("self._call(self._c.orders.create, p)"))
    assert not sends_orders(ast.parse("client.markets.bbo(slug)"))


def test_the_scan_catches_a_credentialed_order_client_built_without_our_kwargs(
        monkeypatch):
    """Mutation check: execmirror.Venue's client built WITHOUT
    venue_sdk.client_kwargs() must be reported."""
    src = (PKG / "execmirror.py").read_text()
    assert "**venue_sdk.client_kwargs()" in src
    mutated = src.replace("**venue_sdk.client_kwargs()", "")
    assert _credentialed_order_clients_without_kwargs(ast.parse(mutated)), \
        "the scan did not see execmirror.Venue built with the SDK's retries"
    assert not _credentialed_order_clients_without_kwargs(ast.parse(src))


def _credentialed_order_clients_without_kwargs(tree) -> list:
    if not sends_orders(tree):
        return []
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(
                node.func, "id", getattr(node.func, "attr", None)) == "PolymarketUS":
            credentialed = any(kw.arg in ("key_id", "secret_key")
                               for kw in node.keywords)
            if credentialed and not any(kw.arg is None or kw.arg == "max_retries"
                                        for kw in node.keywords):
                out.append(node.lineno)
    return out


def test_no_module_builds_the_venue_sdk_with_its_retries_switched_on():
    """A `PolymarketUS(...)` built with a literal max_retries other than 0
    is a hidden retry loop; a CREDENTIALED client in a module that can
    create, cancel or close orders must be built from
    `venue_sdk.client_kwargs()`. (Read-only account readers in api/ build
    credentialed clients with the SDK default; they hold no order path and
    are outside this order-safety rule.)"""
    offenders, order_modules_without_kwargs = [], []
    for path in sorted(PKG.rglob("*.py")):
        src = path.read_text()
        if "PolymarketUS(" not in src or path.name == "venue_sdk.py":
            continue
        tree = ast.parse(src)
        sends = sends_orders(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and getattr(
                    node.func, "id", getattr(node.func, "attr", None))
                    == "PolymarketUS"):
                continue
            for kw in node.keywords:
                if kw.arg == "max_retries" and not (
                        isinstance(kw.value, ast.Constant) and kw.value.value == 0):
                    offenders.append("%s:%d" % (path.relative_to(PKG), node.lineno))
            # a client built WITHOUT a credential cannot sign an order (the
            # SDK refuses authenticated calls locally); only a credentialed
            # client in a module that sends orders must carry our kwargs
            credentialed = any(kw.arg in ("key_id", "secret_key")
                               for kw in node.keywords)
            if sends and credentialed and not any(
                    kw.arg is None or kw.arg == "max_retries"
                    for kw in node.keywords):
                order_modules_without_kwargs.append(
                    "%s:%d" % (path.relative_to(PKG), node.lineno))
    assert offenders == [], offenders
    assert order_modules_without_kwargs == [], order_modules_without_kwargs


# ═══════════════ 6 · KALSHI: A PLAIN SESSION ═══════════════════════════

def test_the_kalshi_transport_mounts_no_retry_adapter():
    requests = pytest.importorskip("requests")
    from sportsassets import kalshi_venue as KV
    t = KV.RequestsTransport()
    for prefix, adapter in t._s.adapters.items():
        r = adapter.max_retries
        assert r.total == 0 and not r.read, (prefix, r)
    src = inspect.getsource(KV.RequestsTransport)
    assert "Retry(" not in src and "mount(" not in src
    assert requests  # imported for the adapter classes
