"""submit_fok's post_only / good_till kwargs (mirror lane, owner order
2026-09-02, phase P1, build step 2).

Two promises, both pinned here:

1. With both kwargs omitted the params dict handed to the SDK is
   BYTE-IDENTICAL to what every existing caller sends today. The
   fixtures below were captured from the pre-change code path at HEAD
   1c5bcde (a stub client recording orders.preview / orders.create)
   and are written out as literals on purpose, so a change to the
   dict shape fails this file rather than being rebuilt by a helper
   that mirrors the implementation.

2. Only under post_only=True is a 4xx raised by orders.create read
   as the venue's refusal ('post_only_rejected'). Every other raise
   propagates: a timeout, a dropped connection, a 5xx, and every
   raise when the flag is off. A lost response is never mistaken for
   a refusal; the caller's lost-order search is the only safe reading
   of it.
"""

import inspect

import httpx
import pytest
from polymarket_us import (APIConnectionError, APIStatusError,
                           APITimeoutError, BadRequestError,
                           InternalServerError)

from sportsassets import pmus

SLUG = "aec-atp-a-b-2026-09-03"
GTD = "2026-09-03T12:00:00Z"

FOK = "TIME_IN_FORCE_FILL_OR_KILL"
IOC = "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"
GTC = "TIME_IN_FORCE_GOOD_TILL_CANCEL"
GTDT = "TIME_IN_FORCE_GOOD_TILL_DATE"
FLAG = "participateDontInitiate"


# ── the pre-change fixture (HEAD 1c5bcde), one literal per caller shape ──

_PRICE = {"value": "0.30", "currency": "USD"}

# (kwargs to submit_fok, expected preview calls, expected create calls)
_FIXTURE = {
    "buy_fok_default": (
        dict(),
        [{"request": {"marketSlug": SLUG, "intent": "ORDER_INTENT_BUY_LONG",
                      "type": "ORDER_TYPE_LIMIT", "price": _PRICE,
                      "quantity": 100, "tif": FOK}}],
        [{"marketSlug": SLUG, "intent": "ORDER_INTENT_BUY_LONG",
          "type": "ORDER_TYPE_LIMIT", "price": _PRICE, "quantity": 100,
          "tif": FOK, "synchronousExecution": True}],
    ),
    "buy_fok_intent": (
        dict(intent="ORDER_INTENT_BUY_LONG"),
        [{"request": {"marketSlug": SLUG, "intent": "ORDER_INTENT_BUY_LONG",
                      "type": "ORDER_TYPE_LIMIT", "price": _PRICE,
                      "quantity": 100, "tif": FOK}}],
        [{"marketSlug": SLUG, "intent": "ORDER_INTENT_BUY_LONG",
          "type": "ORDER_TYPE_LIMIT", "price": _PRICE, "quantity": 100,
          "tif": FOK, "synchronousExecution": True}],
    ),
    "buy_ioc": (
        dict(sell=False, tif=IOC, intent="ORDER_INTENT_BUY_LONG"),
        [{"request": {"marketSlug": SLUG, "intent": "ORDER_INTENT_BUY_LONG",
                      "type": "ORDER_TYPE_LIMIT", "price": _PRICE,
                      "quantity": 100, "tif": IOC}}],
        [{"marketSlug": SLUG, "intent": "ORDER_INTENT_BUY_LONG",
          "type": "ORDER_TYPE_LIMIT", "price": _PRICE, "quantity": 100,
          "tif": IOC, "synchronousExecution": True}],
    ),
    "buy_gtc": (
        dict(sell=False, tif=GTC, intent="ORDER_INTENT_BUY_LONG"),
        [{"request": {"marketSlug": SLUG, "intent": "ORDER_INTENT_BUY_LONG",
                      "type": "ORDER_TYPE_LIMIT", "price": _PRICE,
                      "quantity": 100, "tif": GTC}}],
        [{"marketSlug": SLUG, "intent": "ORDER_INTENT_BUY_LONG",
          "type": "ORDER_TYPE_LIMIT", "price": _PRICE, "quantity": 100,
          "tif": GTC, "synchronousExecution": True}],
    ),
    "sell_fok_intent": (
        dict(sell=True, intent="ORDER_INTENT_BUY_LONG"),
        [],
        [{"marketSlug": SLUG, "intent": "ORDER_INTENT_SELL_LONG",
          "type": "ORDER_TYPE_LIMIT", "price": _PRICE, "quantity": 100,
          "tif": FOK, "synchronousExecution": True}],
    ),
    "sell_ioc_intent": (
        dict(sell=True, tif=IOC, intent="ORDER_INTENT_BUY_LONG"),
        [],
        [{"marketSlug": SLUG, "intent": "ORDER_INTENT_SELL_LONG",
          "type": "ORDER_TYPE_LIMIT", "price": _PRICE, "quantity": 100,
          "tif": IOC, "synchronousExecution": True}],
    ),
    "sell_gtc_intent": (
        dict(sell=True, tif=GTC, intent="ORDER_INTENT_BUY_LONG"),
        [],
        [{"marketSlug": SLUG, "intent": "ORDER_INTENT_SELL_LONG",
          "type": "ORDER_TYPE_LIMIT", "price": _PRICE, "quantity": 100,
          "tif": GTC, "synchronousExecution": True}],
    ),
    "sell_fok_no_intent_long_position": (
        dict(sell=True),
        [],
        [{"marketSlug": SLUG, "intent": "ORDER_INTENT_SELL_LONG",
          "type": "ORDER_TYPE_LIMIT", "price": _PRICE, "quantity": 100,
          "tif": FOK, "synchronousExecution": True}],
    ),
    "sell_gtc_short": (
        dict(sell=True, tif=GTC, intent="ORDER_INTENT_BUY_SHORT"),
        [],
        [{"marketSlug": SLUG, "intent": "ORDER_INTENT_SELL_SHORT",
          "type": "ORDER_TYPE_LIMIT", "price": _PRICE, "quantity": 100,
          "tif": GTC, "synchronousExecution": True}],
    ),
}


# ── the stub venue ───────────────────────────────────────────────────

def _agreeing_preview(_params):
    return {"order": {"cashOrderQty": {"value": "30.00", "currency": "USD"}}}


def _rested(_params):
    return {"id": "o1", "executions": []}


class _Orders:
    def __init__(self, create=None, preview=None):
        self.previews, self.created = [], []
        self._create = create or _rested
        self._preview = preview or _agreeing_preview

    def preview(self, params):
        self.previews.append(params)
        return self._preview(params)

    def create(self, params):
        self.created.append(params)
        return self._create(params)


class _Markets:
    """The safe two-sided shape (distinct side identifiers) for the
    no-intent backstop; the ambiguous shape has its own tests."""

    def retrieve_by_slug(self, slug):
        return {"market": {"slug": slug, "marketSides": [
            {"identifier": slug, "description": "A"},
            {"identifier": slug + "-b", "description": "B"}]}}


def _install(monkeypatch, orders):
    monkeypatch.setattr(
        pmus, "_get_client",
        lambda: type("C", (), {"orders": orders, "markets": _Markets()})())
    # The no-intent SELL derives its side from the venue position; a
    # long position names SELL_LONG. Fixed here so the fixture is
    # deterministic and no test touches the portfolio endpoint.
    monkeypatch.setattr(pmus, "position_side", lambda slug: 5.0)


def _status_error(code, message="post-only order would cross",
                  body=None, cls=None):
    resp = httpx.Response(code, request=httpx.Request(
        "POST", "https://venue.invalid/v1/order"))
    cls = cls or (BadRequestError if code == 400 else APIStatusError)
    return cls(message, response=resp, body=body)


def _raising(exc):
    def _create(_params):
        raise exc
    return _create


# ── 1. omitted kwargs: byte-identical params ─────────────────────────

@pytest.mark.parametrize("case", sorted(_FIXTURE))
def test_omitted_kwargs_send_todays_params(monkeypatch, case):
    kwargs, previews, created = _FIXTURE[case]
    orders = _Orders()
    _install(monkeypatch, orders)
    pmus.submit_fok(SLUG, 0.30, 100, **kwargs)
    assert orders.previews == previews
    assert orders.created == created
    # Belt for the two new keys by name: never present unless named.
    for sent in orders.created:
        assert FLAG not in sent
        assert "goodTillTime" not in sent
    for pv in orders.previews:
        assert FLAG not in pv["request"]
        assert "goodTillTime" not in pv["request"]


def test_the_kwargs_sit_after_intent_with_off_defaults():
    """Every existing caller passes up to six positionals (slug, price,
    qty, sell, tif, intent); the two new ones must come after and
    default to off, or a positional caller's tif/intent would shift."""
    params = list(inspect.signature(pmus.submit_fok).parameters.values())
    names = [p.name for p in params]
    assert names == ["us_market_slug", "limit_price", "quantity", "sell",
                     "tif", "intent", "post_only", "good_till"]
    assert params[names.index("post_only")].default is False
    assert params[names.index("good_till")].default is None


# ── 2. post_only: the flag rides in both requests ────────────────────

def test_post_only_flags_the_preview_and_the_create(monkeypatch):
    orders = _Orders()
    _install(monkeypatch, orders)
    r = pmus.submit_fok(SLUG, 0.30, 100, False, GTC,
                        "ORDER_INTENT_BUY_LONG", post_only=True)
    assert r["order_id"] == "o1"
    _, previews, created = _FIXTURE["buy_gtc"]
    assert orders.previews == [{"request": {**previews[0]["request"],
                                            FLAG: True}}]
    assert orders.created == [{**created[0], FLAG: True}]


def test_post_only_on_a_sell_flags_the_create(monkeypatch):
    orders = _Orders()
    _install(monkeypatch, orders)
    pmus.submit_fok(SLUG, 0.30, 100, True, GTC,
                    "ORDER_INTENT_BUY_LONG", post_only=True)
    _, _, created = _FIXTURE["sell_gtc_intent"]
    assert orders.previews == []
    assert orders.created == [{**created[0], FLAG: True}]


@pytest.mark.parametrize("tif", [FOK, IOC, GTC])
@pytest.mark.parametrize("sell", [False, True])
def test_the_flag_is_never_set_unless_named(monkeypatch, tif, sell):
    """The IOC/FOK copy paths and the GTC rest lane never ask for it,
    so it never appears; post_only=False is the same as omitting it."""
    orders = _Orders()
    _install(monkeypatch, orders)
    pmus.submit_fok(SLUG, 0.30, 100, sell, tif, "ORDER_INTENT_BUY_LONG")
    pmus.submit_fok(SLUG, 0.30, 100, sell, tif, "ORDER_INTENT_BUY_LONG",
                    post_only=False)
    assert len(orders.created) == 2
    for sent in orders.created:
        assert FLAG not in sent
        assert sent["tif"] == tif
    assert orders.created[0] == orders.created[1]


# ── 3. good_till: the dated rest ─────────────────────────────────────

def test_good_till_switches_tif_and_carries_the_time(monkeypatch):
    orders = _Orders()
    _install(monkeypatch, orders)
    pmus.submit_fok(SLUG, 0.30, 100, False, GTC, "ORDER_INTENT_BUY_LONG",
                    good_till=GTD)
    _, previews, created = _FIXTURE["buy_gtc"]
    assert orders.created == [{**created[0], "tif": GTDT,
                               "goodTillTime": GTD}]
    assert orders.previews == [{"request": {**previews[0]["request"],
                                            "tif": GTDT,
                                            "goodTillTime": GTD}}]
    assert FLAG not in orders.created[0]


def test_good_till_and_post_only_together(monkeypatch):
    orders = _Orders()
    _install(monkeypatch, orders)
    pmus.submit_fok(SLUG, 0.30, 100, False, GTC, "ORDER_INTENT_BUY_LONG",
                    post_only=True, good_till=GTD)
    _, _, created = _FIXTURE["buy_gtc"]
    assert orders.created == [{**created[0], "tif": GTDT,
                               "goodTillTime": GTD, FLAG: True}]


@pytest.mark.parametrize("intent, exit_intent, net", [
    ("ORDER_INTENT_BUY_LONG", "ORDER_INTENT_SELL_LONG", 5.0),
    ("ORDER_INTENT_BUY_SHORT", "ORDER_INTENT_SELL_SHORT", 5.0),
    (None, "ORDER_INTENT_SELL_LONG", 5.0),
    (None, "ORDER_INTENT_SELL_SHORT", -5.0),
])
def test_a_sell_with_good_till_keeps_exit_intent(monkeypatch, intent,
                                                 exit_intent, net):
    orders = _Orders()
    _install(monkeypatch, orders)
    monkeypatch.setattr(pmus, "position_side", lambda slug: net)
    pmus.submit_fok(SLUG, 0.30, 100, True, GTC, intent, good_till=GTD)
    assert orders.previews == []
    sent = orders.created[0]
    assert sent["intent"] == exit_intent
    assert sent["tif"] == GTDT
    assert sent["goodTillTime"] == GTD


# ── 4. the refusal-vs-lost distinction ───────────────────────────────

def test_a_4xx_under_post_only_is_the_named_refusal(monkeypatch):
    exc = _status_error(400, "post-only order would cross",
                        body={"message": "would cross", "code": 7})
    orders = _Orders(create=_raising(exc))
    _install(monkeypatch, orders)
    r = pmus.submit_fok(SLUG, 0.30, 100, False, GTC,
                        "ORDER_INTENT_BUY_LONG", post_only=True)
    raw = r.pop("raw")
    assert r == {"ok": False, "order_id": None,
                 "status": "post_only_rejected",
                 "fill_price": None, "filled_shares": 0}
    assert raw["status_code"] == 400
    assert raw["error"] == "post-only order would cross"
    assert raw["body"] == {"message": "would cross", "code": 7}
    assert raw["preview"] == _agreeing_preview(None)["order"]
    # The venue was asked exactly once and never retried.
    assert len(orders.created) == 1
    assert orders.created[0][FLAG] is True


@pytest.mark.parametrize("code", [400, 403, 409, 422, 429])
def test_every_4xx_under_post_only_is_a_refusal_not_a_fill(monkeypatch,
                                                            code):
    """A 4xx that is not the crossing text (balance, rate limit) is
    still a refusal: the venue read the order and nothing rests. The
    code rides in raw so a reader that must back off on 429 can."""
    orders = _Orders(create=_raising(_status_error(code, "no")))
    _install(monkeypatch, orders)
    r = pmus.submit_fok(SLUG, 0.30, 100, False, GTC,
                        "ORDER_INTENT_BUY_LONG", post_only=True)
    assert r["status"] == "post_only_rejected"
    assert r["ok"] is False and r["order_id"] is None
    assert r["filled_shares"] == 0
    assert r["raw"]["status_code"] == code


def test_a_4xx_without_post_only_raises(monkeypatch):
    """The catch is reachable ONLY under the flag: every existing
    caller sees exactly the raise it sees today."""
    exc = _status_error(400)
    for kwargs in (dict(), dict(post_only=False)):
        orders = _Orders(create=_raising(exc))
        _install(monkeypatch, orders)
        with pytest.raises(BadRequestError):
            pmus.submit_fok(SLUG, 0.30, 100, False, GTC,
                            "ORDER_INTENT_BUY_LONG", **kwargs)
        assert len(orders.created) == 1


@pytest.mark.parametrize("post_only", [True, False])
def test_a_5xx_raises_in_both(monkeypatch, post_only):
    """A 5xx says nothing about whether the order stands; only the
    caller's lost-order search may read it."""
    exc = _status_error(503, "upstream", cls=InternalServerError)
    orders = _Orders(create=_raising(exc))
    _install(monkeypatch, orders)
    with pytest.raises(InternalServerError):
        pmus.submit_fok(SLUG, 0.30, 100, False, GTC,
                        "ORDER_INTENT_BUY_LONG", post_only=post_only)


@pytest.mark.parametrize("post_only", [True, False])
@pytest.mark.parametrize("exc", [
    APITimeoutError(),
    APIConnectionError(),
    httpx.ReadTimeout("read timed out"),
    httpx.ConnectError("connection reset"),
    RuntimeError("socket closed mid-response"),
])
def test_a_timeout_or_connection_error_raises_in_both(monkeypatch,
                                                      post_only, exc):
    orders = _Orders(create=_raising(exc))
    _install(monkeypatch, orders)
    with pytest.raises(type(exc)):
        pmus.submit_fok(SLUG, 0.30, 100, False, GTC,
                        "ORDER_INTENT_BUY_LONG", post_only=post_only)


def test_a_4xx_from_the_preview_raises_even_under_post_only(monkeypatch):
    """Only orders.create is wrapped: the preview is the second
    opinion on cost, and its failure keeps today's meaning."""
    def _bad_preview(_params):
        raise _status_error(400, "bad preview")
    orders = _Orders(preview=_bad_preview)
    _install(monkeypatch, orders)
    with pytest.raises(BadRequestError):
        pmus.submit_fok(SLUG, 0.30, 100, False, GTC,
                        "ORDER_INTENT_BUY_LONG", post_only=True)
    assert orders.created == []


def test_the_preview_guard_still_refuses_before_create_under_post_only(
        monkeypatch):
    def _overspend(_params):
        return {"order": {"cashOrderQty": {"value": "90.00",
                                           "currency": "USD"}}}
    orders = _Orders(preview=_overspend)
    _install(monkeypatch, orders)
    r = pmus.submit_fok(SLUG, 0.30, 100, False, GTC,
                        "ORDER_INTENT_BUY_LONG", post_only=True,
                        good_till=GTD)
    assert r["status"] == "preview_mismatch"
    assert orders.created == []


def test_the_helper_refuses_only_a_4xx_status_error():
    """Direct truth table for the seam the create wrapper relies on:
    None means 'propagate'."""
    assert pmus._post_only_refusal(RuntimeError("x"), {}) is None
    assert pmus._post_only_refusal(APITimeoutError(), {}) is None
    assert pmus._post_only_refusal(_status_error(500, cls=InternalServerError),
                                   {}) is None
    assert pmus._post_only_refusal(_status_error(399), {}) is None
    out = pmus._post_only_refusal(_status_error(400, body=object()),
                                  {"quantity": 1})
    assert out["status"] == "post_only_rejected"
    assert out["raw"]["preview"] == {"quantity": 1}
    assert isinstance(out["raw"]["body"], str)  # JSON-safe for the row
