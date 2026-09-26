"""THE FUNDED PATH, TESTED AS THE PATH -- NOT AS A SIMULATOR.

WHAT WAS WRONG WITH THE EARLIER PROOF. `bettor_test_venue_executor` was
offered as the execution evidence. Its `ALLOWED_VENUE_CLASSES` is
`(VENUE_TEST,)`: it REFUSES a funded venue, and the thing it drives is
`InternalOrderLifecycleSimulator`, code in this repository that holds no
credential and opens no socket. It proves the order lifecycle. It proves
nothing about the path a funded order would take, and the EV lane had no such
path at all -- `api/app.py` records that the scheduled worker "reaches
`pmus.book_read` and nothing else on that module".

WHERE THIS TEST CUTS. At the TRANSPORT SEAM, `pmus._get_client`, and nowhere
else. Everything above it is the production code: `submit_for_decision` reads
the canonical account row, the owner-approved limit set, the effective rails
and the authorization; and `pmus.submit_fok` itself then runs -- its
`execution_gate.authorize('submit')` call, its intent/side rules, its
`_amount` price scaling, its integer quantity, its `orders.preview` cost
comparison against `PREVIEW_COST_TOLERANCE`, and its `orders.create`. The
double substitutes the venue's HTTP, and the double alone.

WHAT THE SWITCHES DO HERE. `FUNDED_SUBMISSION_ENABLED` is monkeypatched TRUE
in exactly the tests that need to see the adapter run, and the SHIPPED value is
asserted False by its own test. No test enables anything in the deployed
build, and no order leaves this process: the transport is a list.
"""

from __future__ import annotations

import time

import pytest

from sportsassets import bettor_entry_execution as EX
from sportsassets import bettor_funded_activation as FA
from sportsassets import bettor_funded_execution as FX

DSN = __import__("os").environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

ACCT = "acct-funded-pilot-test"
VENUE = "PMUS"
SLUG = "aec-mlb-lad-sf-2026-09-26"
LIMITS = {"capital_usd": 250, "per_order_usd": 25,
          "max_exposure_usd": 100, "daily_loss_stop_usd": 50}


def _decision(**over):
    """A QUALIFYING DECISION, in the shape `bettor_external_shadow.evaluate`
    returns and `bettor_entry_inventory.plan_entry` already consumes."""
    rec = {"admissible": True, "refusals": [],
           "us_market_slug": SLUG,
           "order_intent": FX.LONG,
           "execution_plan": {"execution": {
               "size": 20, "vwap": 0.62, "limit_price": 0.63}}}
    rec.update(over)
    return rec


# ── THE VENUE'S HTTP, AND NOTHING ELSE, REPLACED ────────────────────

class _Orders:
    """The two calls `submit_fok` makes on the venue, recording what it sent.

    The preview answers in the venue's OWN shape (`order.cashOrderQty` as an
    amount object), because the production code reads it with `_order_cost`
    and refuses when it cannot -- a double that answered in our shape would
    skip the guard this test exists to run.
    """

    def __init__(self, sent, *, preview_cost=None, executions=None,
                 preview_states_nothing=False):
        self.sent = sent
        self._cost = preview_cost
        self._execs = executions
        self._silent = preview_states_nothing

    def preview(self, body):
        req = (body or {}).get("request") or {}
        self.sent.append(("preview", req))
        if self._silent:
            return {"order": {}}
        cost = self._cost
        if cost is None:
            px = float((req.get("price") or {}).get("value") or 0)
            cost = px * int(req.get("quantity") or 0)
        return {"order": {"price": req.get("price"),
                          "quantity": req.get("quantity"),
                          "cashOrderQty": {"value": "%.2f" % cost,
                                           "currency": "USD"}}}

    def create(self, params):
        self.sent.append(("create", dict(params)))
        qty = int(params.get("quantity") or 0)
        px = float((params.get("price") or {}).get("value") or 0)
        execs = self._execs
        if execs is None:
            execs = [{"type": "EXECUTION_TYPE_FILL",
                      "lastPx": {"value": "%.2f" % px, "currency": "USD"},
                      "lastShares": qty,
                      "order": {"state": "ORDER_STATE_FILLED"}}]
        return {"id": "venue-ord-1", "executions": execs}


class _Markets:
    def retrieve_by_slug(self, slug):
        # two sides with DISTINCT identifiers, so the adapter's ambiguity
        # refusal is not what this test is measuring
        return {"market": {"marketSides": [
            {"identifier": slug + "-a", "description": "Los Angeles Dodgers"},
            {"identifier": slug + "-b", "description": "San Francisco Giants"},
        ]}}


class _Client:
    def __init__(self, sent, **kw):
        self.orders = _Orders(sent, **kw)
        self.markets = _Markets()


def _substitute_transport(monkeypatch, **kw):
    """THE ONLY SEAM. `pmus._get_client` is what turns credentials into an
    HTTP session; replacing it leaves every line of `submit_fok` in place."""
    from sportsassets import pmus

    sent: list = []
    monkeypatch.setattr(pmus, "_get_client", lambda: _Client(sent, **kw))
    # the adapter's own gate is a separate boundary and is exercised in its
    # own test below; here it is allowed so the ORDER path can be reached
    monkeypatch.setattr(pmus._gate, "authorize",
                        lambda *a, **k: {"ok": True, "allowed": True})
    return pmus, sent


async def _seed(conn, *, approved=True, paused=False, accounting="CLEAN",
                expires_in=3600.0, digest_limits=None):
    import json as _json

    await conn.execute("DELETE FROM bettor_desk_accounts WHERE account_id=$1",
                       ACCT)
    await conn.execute(
        "INSERT INTO bettor_desk_accounts (account_id, desk_id, status, "
        " paused, accounting_status, opening_balance, note) "
        "VALUES ($1,$2,'ACTIVE',$3,$4,0,'funded path test')",
        ACCT, "desk-funded-test", paused, accounting)
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1,$2::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value=$2::jsonb", FA.LIMITS_KEY,
        _json.dumps({"proposed": dict(LIMITS), "approved": approved,
                     "approved_by": "OWNER"}))
    now = time.time()
    eff = EX.effective_limits(digest_limits if digest_limits is not None
                              else LIMITS)
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1,$2::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value=$2::jsonb",
        FA.AUTHORIZATION_KEY,
        _json.dumps({"account_id": ACCT, "venue": VENUE,
                     "venue_class": FA.VENUE_FUNDED, "by": "test",
                     "at": now, "expires_at": now + expires_in,
                     "revoked": False,
                     "effective_limits": eff["effective"],
                     "effective_digest": eff["effective_digest"],
                     "funded_submission": "DISABLED"}))


async def _clean(conn):
    await conn.execute("DELETE FROM bettor_desk_accounts WHERE account_id=$1",
                       ACCT)
    for k in (FA.AUTHORIZATION_KEY, FA.LIMITS_KEY):
        await conn.execute("DELETE FROM ingestion_state WHERE key=$1", k)


# ── 1 · THE CONNECTION IS THE REAL ADAPTER, NOT THE SIMULATOR ───────

def test_the_two_executors_are_deliberately_disjoint():
    """THE CORRECTION THIS FILE EXISTS FOR. The demonstrated executor refuses
    a funded venue; this connection refuses a test venue. Neither can stand in
    for the other, and that is checked rather than described."""
    from sportsassets import bettor_test_venue_executor as TX

    assert TX.ALLOWED_VENUE_CLASSES == (FA.VENUE_TEST,)
    assert FX.ALLOWED_VENUE_CLASSES == (FA.VENUE_FUNDED,)
    assert not set(TX.ALLOWED_VENUE_CLASSES) & set(FX.ALLOWED_VENUE_CLASSES)
    # AND THE SIMULATOR IS NOT REACHABLE FROM THE FUNDED CONNECTION. What
    # matters is imports and calls, not prose: the module names the test
    # executor in a refusal message on purpose, to say where a TEST venue
    # belongs. So the check is on the parsed module, not on its text.
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path(FX.__file__).read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported.update(a.name for a in node.names)
    for banned in ("bettor_test_venue_executor",
                   "InternalOrderLifecycleSimulator", "SimulatedTestVenue"):
        assert banned not in imported, banned
    # and no attribute access reaches it either
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "TX" not in names
    # the only importlib target is the adapter constant
    assert FX.ADAPTER_MODULE not in ("sportsassets.bettor_test_venue_executor",)
    # the funded path names the EXISTING adapter
    assert FX.ADAPTER_MODULE == "sportsassets.pmus"
    assert "submit_fok" in FX.ADAPTER_SURFACE


def test_the_shipped_switches_are_all_off():
    """WHAT REMAINS DISABLED, asserted on the SHIPPED values."""
    assert FX.FUNDED_SUBMISSION_ENABLED is False
    assert EX.REAL_ORDER_SUBMISSION_ENABLED is False
    d = {x["what"]: x for x in FX.disablements()}
    assert set(d) == {"FUNDED_SUBMISSION_ENABLED",
                      "REAL_ORDER_SUBMISSION_ENABLED",
                      "execution_gate", "PMUS_KEY_ID / PMUS_SECRET_KEY"}
    assert d["FUNDED_SUBMISSION_ENABLED"]["value"] is False
    assert d["REAL_ORDER_SUBMISSION_ENABLED"]["value"] is False


def test_the_plan_is_read_off_the_decision_and_refuses_what_it_cannot_read():
    plan = FX.plan_from_decision(_decision())
    assert plan["ok"] is True
    assert plan["us_market_slug"] == SLUG
    assert plan["intent"] == FX.LONG
    assert plan["limit_price"] == 0.63
    assert plan["quantity"] == 20
    assert plan["notional_usd"] == pytest.approx(12.6)

    # A REFUSED DECISION IS NOT AN ORDER
    assert FX.plan_from_decision(
        _decision(admissible=False))["refusal"] == FX.R_NOT_ADMISSIBLE
    # NO SIZE, NO ORDER
    assert FX.plan_from_decision(_decision(
        execution_plan={"execution": {"vwap": 0.6}}))["refusal"] == \
        FX.R_NO_SIZED_PLAN
    # A SUB-CONTRACT SIZE ROUNDS DOWN TO NOTHING, and that is a refusal
    assert FX.plan_from_decision(_decision(
        execution_plan={"execution": {"size": 0.4, "vwap": 0.6,
                                      "limit_price": 0.6}}))["refusal"] == \
        FX.R_NO_SIZED_PLAN
    # NO CONTRACT, NO ADDRESS
    d = _decision()
    d.pop("us_market_slug")
    assert FX.plan_from_decision(d)["refusal"] == FX.R_NO_VENUE_CONTRACT
    # AND AN UNNAMED SIDE IS THE VENUE CHOOSING FOR US
    assert FX.plan_from_decision(
        _decision(order_intent=None))["refusal"] == FX.R_NO_INTENT


# ── 2 · THE PRODUCTION PATH, WITH TRANSPORT SUBSTITUTED ─────────────

@pg
@pytest.mark.asyncio
async def test_the_funded_path_stops_at_the_code_switch_with_everything_else_met():
    """EVERY OWNER INPUT SATISFIED, and the adapter is still never called.

    This is the state the pilot would be in the instant before activation: a
    clean canonical account, an owner-approved limit set, a valid
    authorization whose digest matches, an order inside the rails -- and a
    code constant between it and the venue.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _seed(conn)
        import unittest.mock as _m
        from sportsassets import pmus
        sent: list = []
        with _m.patch.object(pmus, "_get_client",
                             lambda: _Client(sent)):
            got = await FX.submit_for_decision(
                conn, _decision(), account_id=ACCT, venue=VENUE)
        assert got["ok"] is False
        assert got["refusal"] == FX.R_FUNDED_DISABLED, got
        assert got["submitted"] is False
        # EVERY EARLIER GATE PASSED, which is what makes this the last one
        assert got["account_selection"]["ok"] is True
        assert got["authorization"]["authorization_consumed"] is True
        assert got["authorization"]["refusal"] == EX.R_SUBMISSION_DISABLED
        assert got["plan"]["ok"] is True
        # and the venue was NOT spoken to
        assert sent == [], sent
        # the call it would have made is stated, so the wiring is inspectable
        assert got["would_send"]["callable"] == "sportsassets.pmus.submit_fok"
        assert got["would_send"]["args"][0] == SLUG
        assert got["would_send"]["kwargs"]["intent"] == FX.LONG
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_with_the_switch_flipped_the_real_adapter_runs_and_places_it(
        monkeypatch):
    """THE EXACT PRODUCTION PATH, transport substituted at `_get_client`.

    The switch is flipped IN THIS TEST ONLY. What runs is the real
    `pmus.submit_fok`: its gate call, its side rules, its `_amount` scaling to
    a two-decimal string, its integer quantity, its preview cost comparison
    and its create. The venue is a list of the requests it received.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _seed(conn)
        pmus, sent = _substitute_transport(monkeypatch)
        monkeypatch.setattr(FX, "FUNDED_SUBMISSION_ENABLED", True)
        got = await FX.submit_for_decision(
            conn, _decision(), account_id=ACCT, venue=VENUE)

        assert got["submitted"] is True, got
        assert got["ok"] is True, got
        assert got["order"]["venue_order_id"] == "venue-ord-1"
        assert got["order"]["filled_shares"] == pytest.approx(20.0)
        assert got["order"]["fill_price"] == pytest.approx(0.63)

        # THE ADAPTER'S OWN STEPS RAN, IN ORDER
        assert [k for k, _ in sent] == ["preview", "create"], sent
        prev, create = sent[0][1], sent[1][1]
        # the venue was addressed by slug, side named, integer quantity,
        # price scaled to the venue's two-decimal amount object
        assert create["marketSlug"] == SLUG
        assert create["intent"] == FX.LONG
        assert create["quantity"] == 20 and isinstance(create["quantity"], int)
        assert create["price"] == {"value": "0.63", "currency": "USD"}
        assert create["tif"] == FX.TIF
        assert create.get("participateDontInitiate") is None
        assert create["synchronousExecution"] is True
        # the preview is the SAME order minus the synchronous flag, which is
        # how the venue's cost can be compared to ours at all
        assert "synchronousExecution" not in prev
        assert prev["price"] == create["price"]
        assert prev["quantity"] == create["quantity"]
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_adapters_own_preview_guard_still_refuses_on_this_path(
        monkeypatch):
    """THE MONEY GUARD INSIDE THE ADAPTER IS NOT BYPASSED by coming through
    this connection. A venue that quotes MORE than our expectation refuses,
    and a venue that states NO cost refuses -- the second is the one that
    used to fail open and let five overspends through."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _seed(conn)
        # THE VENUE WANTS MORE THAN WE AUTHORISED (0.63 x 20 = 12.60; the
        # tolerance is 2%, so 20.00 is far outside)
        pmus, sent = _substitute_transport(monkeypatch, preview_cost=20.00)
        monkeypatch.setattr(FX, "FUNDED_SUBMISSION_ENABLED", True)
        got = await FX.submit_for_decision(
            conn, _decision(), account_id=ACCT, venue=VENUE)
        assert got["submitted"] is True          # the adapter WAS reached
        assert got["ok"] is False
        assert got["refusal"] == "preview_mismatch", got
        assert [k for k, _ in sent] == ["preview"], "it must not have created"

        # AND A PREVIEW THAT STATES NOTHING IS NOT AGREEMENT
        pmus, sent2 = _substitute_transport(
            monkeypatch, preview_states_nothing=True)
        got2 = await FX.submit_for_decision(
            conn, _decision(), account_id=ACCT, venue=VENUE)
        assert got2["ok"] is False
        assert got2["refusal"] == "preview_unreadable", got2
        assert [k for k, _ in sent2] == ["preview"]
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_adapters_execution_gate_is_a_separate_boundary(monkeypatch):
    """DENIAL AT THE VENUE BOUNDARY RAISES, and it is reached even with this
    connection's own authorization satisfied. Four boundaries, not one."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _seed(conn)
        from sportsassets import pmus
        sent: list = []
        monkeypatch.setattr(pmus, "_get_client", lambda: _Client(sent))
        monkeypatch.setattr(FX, "FUNDED_SUBMISSION_ENABLED", True)

        def _deny(*a, **k):
            raise RuntimeError("EXECUTION_GATE_DENIED_submit")
        monkeypatch.setattr(pmus._gate, "authorize", _deny)

        with pytest.raises(RuntimeError, match="EXECUTION_GATE_DENIED"):
            await FX.submit_for_decision(
                conn, _decision(), account_id=ACCT, venue=VENUE)
        assert sent == [], "the gate must deny BEFORE any venue call"
    finally:
        await _clean(conn)
        await conn.close()


# ── 3 · EVERY OWNER-SIDE PRECONDITION REFUSES BY ITS OWN NAME ───────

@pg
@pytest.mark.asyncio
async def test_each_owner_side_precondition_refuses_and_sends_nothing(
        monkeypatch):
    """THE FOUR THINGS THE OWNER CONTROLS, each refusing on its own, with the
    switch FLIPPED so that only the precondition can be what stops it."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        pmus, sent = _substitute_transport(monkeypatch)
        monkeypatch.setattr(FX, "FUNDED_SUBMISSION_ENABLED", True)

        # A PAUSED ACCOUNT
        await _seed(conn, paused=True)
        r = await FX.submit_for_decision(conn, _decision(),
                                         account_id=ACCT, venue=VENUE)
        assert r["refusal"] == FA.R_ACCOUNT_PAUSED, r
        # UNCERTAIN ACCOUNTING
        await _seed(conn, accounting="UNCERTAIN")
        r = await FX.submit_for_decision(conn, _decision(),
                                         account_id=ACCT, venue=VENUE)
        assert r["refusal"] == FA.R_ACCOUNTING_UNCERTAIN, r
        # LIMITS RECORDED BUT NOT APPROVED
        await _seed(conn, approved=False)
        r = await FX.submit_for_decision(conn, _decision(),
                                         account_id=ACCT, venue=VENUE)
        assert r["refusal"] == FX.R_LIMITS_NOT_APPROVED, r
        # AN AUTHORIZATION GRANTED AGAINST DIFFERENT LIMITS
        await _seed(conn, digest_limits=dict(LIMITS, per_order_usd=99))
        r = await FX.submit_for_decision(conn, _decision(),
                                         account_id=ACCT, venue=VENUE)
        assert r["refusal"] == FX.R_NOT_AUTHORIZED, r
        assert r["authorization"]["refusal"] == EX.R_AUTH_LIMITS
        # AN EXPIRED AUTHORIZATION
        await _seed(conn, expires_in=-60.0)
        r = await FX.submit_for_decision(conn, _decision(),
                                         account_id=ACCT, venue=VENUE)
        assert r["refusal"] == FX.R_NOT_AUTHORIZED, r
        assert r["authorization"]["refusal"] == EX.R_AUTH_EXPIRED

        # AN ORDER OVER THE APPROVED PER-ORDER RAIL. $25 approved; 60 x 0.63
        # is $37.80.
        await _seed(conn)
        big = _decision(execution_plan={"execution": {
            "size": 60, "vwap": 0.62, "limit_price": 0.63}})
        r = await FX.submit_for_decision(conn, big, account_id=ACCT,
                                         venue=VENUE)
        assert r["refusal"] == FX.R_OVER_RAIL, r
        assert r["over"]["rail"] == "MAX_MARKET_EXPOSURE"
        assert r["over"]["limit"] == pytest.approx(25.0)

        # A TEST-CLASS VENUE ON THE FUNDED PATH
        r = await FX.submit_for_decision(conn, _decision(), account_id=ACCT,
                                         venue="PMUS_TEST")
        assert r["refusal"] == FX.R_VENUE_CLASS, r

        # NOT ONE OF THEM SPOKE TO THE VENUE
        assert sent == [], sent
    finally:
        await _clean(conn)
        await conn.close()
