"""CAPITAL-CRITICAL (R30A, audit P0 #2): ONE ORIGIN OF LIVE EXPOSURE.

Three generations of live execution exist in the code base. This file proves
that none of them can originate new real-money exposure outside a canonical
decision intent -- and that in this (SHADOW) release none can originate at
all, because the canonical SMALL LIVE adapter never issues its
authorization:

  §1  the authorization: SHADOW never issues one; a forged object, a dict,
      a truthy value or even a LiveAuthorization built outside
      issue_live_authorization is never accepted; only live_parity
      constructs one; the PRODUCTION venue adapter (execmirror.Venue.place)
      refuses every new order before its client, while cancel / close (risk
      reducing) still reach it
  §2  live_parity.authorize_live_exposure, every refusal in order: no
      canonical intent, a sha that does not verify, an execution that names
      another intent, an order that differs from its intent (every matched
      field), an expired / underivable validity window, each LIVE policy
      failure mode (row missing or a labelled PAPER fallback, sha mismatch,
      version not approved, a non-human approver), a live gate unapproved,
      and finally SHADOW: no authorization even when everything is approved
  §3  the ACTUAL sibling (execution_intent.ActualLane) against the
      production adapter: refused BEFORE its claim for each of those causes;
      no execmirror_orders row, the client never called
  §4  execmirror origination is retired: a hedge copied from a paper order
      by Mirror.tick reaches the production adapter, is refused before its
      client and recorded REJECTED; a later tick does not retry it
  §5  the funded stack: with both code switches flipped in-test (transport
      substituted), a funded BUY without a canonical intent is refused before
      anything is written or sent; a SELL does not pass the boundary at all
  §6  census: authorize_live_exposure is called only by those two lanes,
      the one client orders.create in execmirror is inside Venue.place, and
      the ACTUAL lane hands the venue only the canonical token

Fake venue / recording clients only. No credential, no network, no capital.
Database cases restore what they change.
"""
from __future__ import annotations

import ast
import inspect
import json
import pathlib
import time
import uuid
from decimal import Decimal

import pytest

from sportsassets import bettor_funded_execution as FX
from sportsassets import canonical_intent as CI
from sportsassets import execmirror as M
from sportsassets import execution_intent as EI
from sportsassets import live_approvals as LAP
from sportsassets import live_parity as LP
from sportsassets.agents import paper_benchmark as PB

from tests import paper_harness as H
from tests import test_live_parity as TLP

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
ROOT = pathlib.Path(LP.__file__).resolve().parent

#: the real authorization check, captured before any test's monkeypatch
_REAL_AUTHORIZE = LP.authorize_live_exposure


class _RecordingOrders:
    def __init__(self):
        self.created, self.cancelled, self.closed = [], [], []

    def create(self, params):
        self.created.append(params)
        return {"id": "must-never-happen"}

    def cancel(self, vid, params):
        self.cancelled.append((vid, params))

    def cancel_all(self, params):
        return {"canceledOrderIds": []}

    def close_position(self, params):
        self.closed.append(params)
        return {"id": "close"}


class RecordingClient:
    """Stands where the venue SDK client stands under execmirror.Venue."""
    def __init__(self):
        self.orders = _RecordingOrders()


def _production_adapter():
    client = RecordingClient()
    v = M.Venue(client=client)
    v._pace = lambda: None
    return v, client


class _Forged:
    issued_by = LP.LIVE_ADAPTER_VERSION
    intent_id = "cdi_forged"
    content_sha = "0" * 64


def _intent(**over):
    return TLP._intent(**over)


def _all_approved(it):
    return {"approved_policy_shas": frozenset({it["policy"]["policy_sha"]}),
            "approved_gates": frozenset(LAP.GATES)}


# ═════════════════════════════════════════════════════════════════════
# §1 THE AUTHORIZATION AND THE PRODUCTION ADAPTER
# ═════════════════════════════════════════════════════════════════════

def test_shadow_never_issues_a_live_authorization():
    assert LP.SMALL_LIVE_MODE == LP.MODE_SHADOW
    it = _intent()
    gov = LP.governance_verdict(it, _all_approved(it))
    assert gov["admissible"] is True
    assert LP.issue_live_authorization(it, governance=gov,
                                       now=it["created_at"]) is None


def test_no_object_built_outside_the_adapter_is_an_authorization():
    it = _intent()
    direct = LP.LiveAuthorization(intent_id=it["intent_id"],
                                  content_sha=it["content_sha"],
                                  issued_at=it["created_at"])
    for tok in (None, _Forged(), {"issued_by": LP.LIVE_ADAPTER_VERSION},
                "token", True, 1, direct):
        assert LP.canonical_live_authorized(tok) is False, tok
        assert M._canonical_live_authorized(tok) is False, tok


def test_outside_shadow_only_a_governed_intent_would_be_authorized(
        monkeypatch):
    """PURE, IN MEMORY: what the adapter would do in a future LIVE release.
    No venue, no client, no database is involved. A governance refusal still
    issues nothing, and a forged object is still refused."""
    monkeypatch.setattr(LP, "SMALL_LIVE_MODE", "LIVE_NOT_IN_THIS_RELEASE")
    it = _intent()
    refused = LP.governance_verdict(it, None)
    assert refused["admissible"] is False
    assert LP.issue_live_authorization(it, governance=refused, now=1.0) is None
    tok = LP.issue_live_authorization(
        it, governance=LP.governance_verdict(it, _all_approved(it)), now=1.0)
    assert isinstance(tok, LP.LiveAuthorization)
    assert tok.intent_id == it["intent_id"]
    assert tok.content_sha == it["content_sha"]
    assert LP.canonical_live_authorized(tok) is True
    assert LP.canonical_live_authorized(_Forged()) is False


def test_only_live_parity_constructs_a_live_authorization():
    builders = set()
    for f in ROOT.rglob("*.py"):
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = getattr(fn, "attr", None) or getattr(fn, "id", None)
                if name == "LiveAuthorization":
                    builders.add(str(f.relative_to(ROOT)))
    assert builders == {"live_parity.py"}, builders
    src = inspect.getsource(LP.issue_live_authorization)
    assert "MODE_SHADOW" in src and "admissible" in src


def test_the_production_adapter_refuses_every_new_order_before_its_client():
    v, client = _production_adapter()
    it = _intent()
    direct = LP.LiveAuthorization(intent_id=it["intent_id"],
                                  content_sha=it["content_sha"],
                                  issued_at=1.0)
    params = {"marketSlug": "s", "intent": "ORDER_INTENT_BUY_LONG",
              "quantity": 1, "price": {"value": "0.55", "currency": "USD"}}
    for tok in (None, _Forged(), direct):
        with pytest.raises(M.LegacyOriginationRetired):
            v.place(dict(params), canonical_live_authorization=tok)
    with pytest.raises(M.LegacyOriginationRetired):
        v.place(dict(params))
    assert client.orders.created == []
    assert M._classify(M.LegacyOriginationRetired("x")) == "REJECTED"
    # RISK-REDUCING CALLS ARE UNAFFECTED
    v.cancel("vid-1", "s")
    v.close("s")
    assert client.orders.cancelled == [("vid-1", {"marketSlug": "s"})]
    assert client.orders.closed and client.orders.closed[0]["marketSlug"] == "s"


# ═════════════════════════════════════════════════════════════════════
# §2 authorize_live_exposure, EVERY REFUSAL IN ORDER
# ═════════════════════════════════════════════════════════════════════

class _Tx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _RowConn:
    """Serves one canonical intent row (or none, or an error)."""
    def __init__(self, row=None, *, fail=False):
        self.row, self.fail = row, fail

    def transaction(self):
        return _Tx()

    async def fetchrow(self, sql, *args):
        if self.fail:
            raise RuntimeError("db down")
        return self.row


def _named(it):
    return {"intent_id": it["intent_id"], "content_sha": it["content_sha"]}


def _order(it):
    return {"us_market_slug": it["us_market_slug"],
            "order_intent": it["order_intent"],
            "holding_side": it["holding_side"], "strategy": it["strategy"],
            "strategy_version": it["strategy_version"],
            "time_in_force": it["time_in_force"],
            "order_type": it["order_type"], "wire_price": it["wire_price"],
            "limit_price": it["limit_price"], "target_qty": it["target_qty"]}


async def _ask(it, *, governance="ALL", named=None, order=None, now=None,
               monkeypatch=None, conn=None):
    if monkeypatch is not None:
        async def gin(conn_):
            if governance == "RAISE":
                raise RuntimeError("approvals unreadable")
            return _all_approved(it) if governance == "ALL" else governance
        monkeypatch.setattr(LP, "governance_in_force", gin)
    return await LP.authorize_live_exposure(
        conn or _RowConn(it), decision_id=it["decision_id"],
        named=_named(it) if named is None else named,
        order=_order(it) if order is None else order,
        now=(it["created_at"] + 1.0) if now is None else now)


@pytest.mark.asyncio
async def test_no_canonical_intent_refuses(monkeypatch):
    it = _intent()
    for conn in (_RowConn(None), _RowConn(fail=True)):
        got = await _ask(it, conn=conn, monkeypatch=monkeypatch)
        assert got["ok"] is False and got["token"] is None
        assert got["refusal"] == LP.R_NO_CANONICAL
    got = await LP.authorize_live_exposure(_RowConn(it))   # names nothing
    assert got["refusal"] == LP.R_NO_CANONICAL


@pytest.mark.asyncio
async def test_a_canonical_intent_whose_sha_does_not_verify_refuses(
        monkeypatch):
    it = _intent()
    for k, v in (("target_qty", Decimal("2401")), ("holding_side", "SHORT"),
                 ("expires_at", it["expires_at"] + 60.0)):
        got = await _ask(it, conn=_RowConn(dict(it, **{k: v})),
                         monkeypatch=monkeypatch)
        assert got["refusal"] == LP.R_CANONICAL_SHA, k


@pytest.mark.asyncio
async def test_an_execution_that_names_another_intent_refuses(monkeypatch):
    it = _intent()
    for named in ({}, {"intent_id": it["intent_id"]},
                  {"intent_id": it["intent_id"], "content_sha": "0" * 64},
                  {"intent_id": "cdi_other", "content_sha": it["content_sha"]}):
        got = await _ask(it, named=named, monkeypatch=monkeypatch)
        assert got["refusal"] == LP.R_CANONICAL_NOT_NAMED, named


@pytest.mark.parametrize("field,value", [
    ("us_market_slug", "aec-nfl-other"), ("order_intent", "ORDER_INTENT_BUY_SHORT"),
    ("holding_side", "SHORT"), ("strategy", "SOME_OTHER_STRATEGY"),
    ("strategy_version", "V9"), ("time_in_force", "GTC"),
    ("order_type", "LIMIT"), ("wire_price", Decimal("0.56")),
    ("limit_price", Decimal("0.54")), ("target_qty", Decimal("2401"))])
@pytest.mark.asyncio
async def test_an_order_that_differs_from_its_intent_refuses(monkeypatch,
                                                            field, value):
    it = _intent()
    assert set(LP.ORIGINATION_MATCH) == set(_order(it))
    got = await _ask(it, order=dict(_order(it), **{field: value}),
                     monkeypatch=monkeypatch)
    assert got["refusal"] == LP.R_CANONICAL_MISMATCH
    assert [m["field"] for m in got["detail"]["mismatches"]] == [field]
    # numbers compare as decimals: the same price written differently is equal
    same = await _ask(it, order=dict(_order(it), wire_price="0.550"),
                      monkeypatch=monkeypatch)
    assert same["refusal"] != LP.R_CANONICAL_MISMATCH


@pytest.mark.asyncio
async def test_an_expired_or_underivable_window_refuses(monkeypatch):
    it = _intent()
    assert it["expires_at"] == pytest.approx(it["created_at"] + 9.0)
    got = await _ask(it, now=it["expires_at"] + 0.001, monkeypatch=monkeypatch)
    assert got["refusal"] == LP.R_INTENT_EXPIRED == CI.R_INTENT_EXPIRED
    no_window = _intent(probability=None)
    assert no_window["expires_at"] is None
    got = await _ask(no_window, monkeypatch=monkeypatch)
    assert got["refusal"] == CI.R_EXPIRY_UNAVAILABLE


@pytest.mark.asyncio
async def test_each_live_policy_failure_refuses(monkeypatch):
    P = TLP.PARAMS_V2

    def with_params(params):
        return _intent(policy=CI.policy_block(
            strategy=TLP.CG, strategy_version=TLP.CG3, params=params))
    cases = [
        # 1 · the row missing / unreadable: the labelled PAPER fallback ...
        (with_params(dict(P, source=CI.POLICY_FALLBACK,
                          fallback_reason="PARAMETER_READ_FAILED:db")),
         CI.R_POLICY_MISSING),
        # ... and a policy with no parameter row at all
        (with_params(None), CI.R_POLICY_MISSING),
        # 2 · the stored sha is not the sha of the values used
        (with_params(dict(P, params_sha256="0" * 64)), CI.R_POLICY_SHA),
        # 3 · the version approver is not a named human
        (with_params(dict(P, approved_by="system")), CI.R_POLICY_UNAPPROVED),
    ]
    for it, code in cases:
        # every approval in force INCLUDING this policy's own sha: the
        # failure is the policy row itself
        got = await _ask(it, monkeypatch=monkeypatch)
        assert got["ok"] is False and got["refusal"] == code, (code, got)
    # 3 · no LIVE approval of this policy sha (a paper-only authorization of
    # the parameter version is not one)
    it = _intent()
    got = await _ask(it, governance={"approved_policy_shas": frozenset(),
                                     "approved_gates": frozenset(LAP.GATES)},
                     monkeypatch=monkeypatch)
    assert got["refusal"] == CI.R_POLICY_UNAPPROVED
    # approvals unreadable -> nothing approved
    got = await _ask(it, governance="RAISE", monkeypatch=monkeypatch)
    assert got["refusal"] == CI.R_POLICY_UNAPPROVED


@pytest.mark.asyncio
async def test_an_unapproved_live_gate_refuses(monkeypatch):
    it = _intent()
    sha = it["policy"]["policy_sha"]
    for gates in (frozenset(), frozenset({LAP.GATE_BOOK}),
                  frozenset({LAP.GATE_SETTLEMENT})):
        got = await _ask(it, governance={"approved_policy_shas": {sha},
                                         "approved_gates": gates},
                         monkeypatch=monkeypatch)
        assert got["refusal"] == LP.R_GATE_APPROVAL, gates


@pytest.mark.asyncio
async def test_everything_approved_is_still_refused_in_shadow(monkeypatch):
    it = _intent()
    got = await _ask(it, monkeypatch=monkeypatch)
    assert got == {"ok": False, "refusal": LP.R_NO_LIVE_AUTHORIZATION,
                   "token": None, "detail": got["detail"]}
    assert got["detail"]["mode"] == LP.MODE_SHADOW


# ═════════════════════════════════════════════════════════════════════
# §3 THE ACTUAL SIBLING AGAINST THE PRODUCTION ADAPTER
# ═════════════════════════════════════════════════════════════════════

async def _lane_env(monkeypatch):
    """The fake-venue lane of tests/test_actual_admission.py (test rule and
    settlement gate approved for the test) WITHOUT the canonical stand-in,
    and the venue's place() replaced by the PRODUCTION adapter's place over
    a recording client."""
    from tests.test_actual_admission import _env
    e = await _env(monkeypatch)
    monkeypatch.setattr(LP, "authorize_live_exposure", _REAL_AUTHORIZE)
    real, e.client = _production_adapter()
    e.venue.place = lambda params, **kw: real.place(params, **kw)
    return e


def _canonical_for(decision_id, slug, *, now, qty=2702, wire=0.55,
                   probability_age_s=5.0):
    """A canonical intent for exactly the order _execution() writes."""
    return TLP._intent(
        now=now, decision_id=decision_id, us_market_slug=slug,
        strategy=PB.CG_STRATEGY, strategy_version=PB.CG_VERSION,
        policy=CI.policy_block(strategy=PB.CG_STRATEGY,
                               strategy_version=PB.CG_VERSION,
                               params=TLP.PARAMS_V2),
        order_type="MARKETABLE", time_in_force="IOC", limit_price=wire,
        wire_price=wire, target_qty=qty,
        probability={"status": "MEASURED", "value": 0.62,
                     "source": "pinnapi.com/raw-websocket",
                     "source_version": "PINNACLE_DEVIG_V1",
                     "observed_at": now - probability_age_s,
                     "received_at": now - probability_age_s + 0.5,
                     "age_at_decision_s": probability_age_s, "limit_s": 30.0})


async def _execution(conn, slug, *, decision_id, named, qty=2702, wire=0.55):
    from tests import admission_fixture as AF
    now = time.time()
    ev = {"admission_facts": AF.admissible_facts(slug=slug)}
    if named is not None:
        ev["canonical_intent"] = named
    return await EI.create(
        conn, decision_id=decision_id, valuation_id=None,
        strategy=PB.CG_STRATEGY, policy_version=PB.CG_VERSION, slug=slug,
        order_intent="ORDER_INTENT_BUY_LONG", holding_side="LONG",
        group_id="grp_" + decision_id, order_type="MARKETABLE",
        time_in_force="IOC", paper_target_qty=qty, limit_price=wire,
        wire_price=wire, book_obs_id=None, book_observed_at=now - 0.3,
        decided_at=now - 0.3, evidence=ev, timeline={})


async def _refused_before_the_claim(e, it, code):
    assert it["live_eligible"] is True, it["live_eligibility"]
    assert it["actual_state"] == EI.A_DISPATCHED
    got = await e.lane._run(e.conn, it["intent_id"])
    assert got["state"] == EI.A_REFUSED and got["refusal"] == code, got
    assert await e.conn.fetchval(
        "SELECT count(*) FROM execmirror_orders WHERE execution_intent_id=$1",
        it["intent_id"]) == 0                       # never claimed
    row = await e.conn.fetchrow(
        "SELECT actual_state, actual_refusal FROM execution_intents "
        " WHERE intent_id=$1", it["intent_id"])
    assert row["actual_state"] == EI.A_REFUSED and row["actual_refusal"] == code
    assert e.client.orders.created == []
    assert e.venue.placed == []
    return got


@pg
async def test_the_actual_lane_refuses_without_a_canonical_intent(monkeypatch):
    from tests.test_actual_admission import _close
    e = await _lane_env(monkeypatch)
    try:
        slug = "mlb-conv-%s" % uuid.uuid4().hex[:6]
        did = "dec_conv_%s" % uuid.uuid4().hex[:12]
        it = await _execution(e.conn, slug, decision_id=did, named=None)
        await _refused_before_the_claim(e, it, LP.R_NO_CANONICAL)
    finally:
        await _close(e)


@pg
async def test_the_actual_lane_refuses_every_canonical_failure_before_its_claim(
        monkeypatch):
    from tests.test_actual_admission import _close
    e = await _lane_env(monkeypatch)
    tx = e.conn.transaction()
    await tx.start()
    try:
        def ids():
            return ("mlb-conv-%s" % uuid.uuid4().hex[:6],
                    "dec_conv_%s" % uuid.uuid4().hex[:12])

        async def canonical(did, slug, **kw):
            ci = _canonical_for(did, slug, now=time.time(), **kw)
            assert await LP.record_decision_intent(e.conn, ci) is True
            return ci

        # the execution names no canonical intent although one exists
        slug, did = ids()
        ci = await canonical(did, slug)
        it = await _execution(e.conn, slug, decision_id=did, named=None)
        await _refused_before_the_claim(e, it, LP.R_CANONICAL_NOT_NAMED)
        # the execution's order is not the intent's (another quantity)
        slug, did = ids()
        ci = await canonical(did, slug)
        it = await _execution(e.conn, slug, decision_id=did, named=_named(ci),
                              qty=2703)
        got = await _refused_before_the_claim(e, it, LP.R_CANONICAL_MISMATCH)
        assert [m["field"] for m in got["canonical"]["mismatches"]] == [
            "target_qty"]
        # the intent's validity window has passed (a 29 s old probability:
        # the 30 s rule ends it one second after the decision)
        slug, did = ids()
        ci = await canonical(did, slug, probability_age_s=29.0)
        it = await _execution(e.conn, slug, decision_id=did, named=_named(ci))
        await __import__("asyncio").sleep(1.1)
        await _refused_before_the_claim(e, it, CI.R_INTENT_EXPIRED)
        # a matching, unexpired, named intent: the LIVE policy is not approved
        # for live (nothing in live_approvals) -> refused
        slug, did = ids()
        ci = await canonical(did, slug)
        it = await _execution(e.conn, slug, decision_id=did, named=_named(ci))
        await _refused_before_the_claim(e, it, CI.R_POLICY_UNAPPROVED)
        # EVERYTHING approved (stated for this intent): SHADOW issues no
        # authorization -> still refused before the claim
        slug, did = ids()
        ci = await canonical(did, slug)

        async def gin(conn_):
            return _all_approved(ci)
        monkeypatch.setattr(LP, "governance_in_force", gin)
        it = await _execution(e.conn, slug, decision_id=did, named=_named(ci))
        await _refused_before_the_claim(e, it, LP.R_NO_LIVE_AUTHORIZATION)
    finally:
        await tx.rollback()
        await _close(e)


# ═════════════════════════════════════════════════════════════════════
# §4 EXECMIRROR ORIGINATION IS RETIRED
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_a_copied_paper_order_never_reaches_the_production_client(
        monkeypatch):
    from tests import test_execmirror as TE
    conn = await TE._conn()
    before = dict(await conn.fetchrow(
        "SELECT * FROM execmirror_control WHERE id = 1"))
    try:
        # the lane mechanics up to a live entry fill, on the FakeVenue
        # (tests/test_execmirror.py states the canonical authorization as an
        # assumption for that part)
        acct, venue, mirror = await TE._setup(conn, monkeypatch)
        po = await TE._paper_order(conn, acct, qty=2702)
        venue.behaviour = [{"fill": 3}]
        await mirror.tick(conn)
        assert len(venue.placed) == 1
        # FROM HERE THE PRODUCTION ADAPTER: Venue.place over a recording
        # client, as execmirror.run builds it
        real, client = _production_adapter()
        venue.place = lambda params, **kw: real.place(params, **kw)
        hedge = await TE._paper_order(conn, acct, role="HEDGE",
                                      group=po["group_id"],
                                      intent="ORDER_INTENT_BUY_SHORT",
                                      qty=2702)
        await mirror.tick(conn)
        h = await TE._row(conn, hedge["order_id"])
        assert h["state"] == "REJECTED", h
        err = h["error"]
        err = json.loads(err) if isinstance(err, str) else err
        assert err["error"] == "LegacyOriginationRetired"
        assert client.orders.created == []
        assert len(venue.placed) == 1
        # nothing is retried
        await mirror.tick(conn)
        assert (await TE._row(conn, hedge["order_id"]))["state"] == "REJECTED"
        assert client.orders.created == []
    finally:
        await conn.execute(
            """UPDATE execmirror_control SET enabled = $1, stopped = $2,
                 cutover_at = $3, account_fingerprint = $4,
                 max_order_usd = $5 WHERE id = 1""",
            before["enabled"], before["stopped"], before["cutover_at"],
            before["account_fingerprint"], before["max_order_usd"])
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# §5 THE FUNDED STACK
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_canonical_origination_needs_a_named_canonical_intent():
    plan = {"us_market_slug": "s", "intent": FX.LONG}
    for rec in (None, {}, {"canonical_intent": None},
                {"canonical_intent": {"content_sha": "x"}}):
        got = await FX.canonical_origination(_RowConn(None), rec=rec,
                                             plan=plan)
        assert got["ok"] is False and got["refusal"] == LP.R_NO_CANONICAL
    # named, but no such intent
    got = await FX.canonical_origination(
        _RowConn(None), rec={"canonical_intent": {"intent_id": "cdi_x",
                                                  "content_sha": "0" * 64}},
        plan=plan)
    assert got["refusal"] == LP.R_NO_CANONICAL
    # named and present: SHADOW still issues nothing
    it = _intent()
    got = await FX.canonical_origination(
        _RowConn(it), rec={"canonical_intent": _named(it)},
        plan={"us_market_slug": it["us_market_slug"],
              "intent": it["order_intent"]})
    assert got["ok"] is False and got["token"] is None
    assert got["refusal"] in (CI.R_POLICY_UNAPPROVED, LP.R_GATE_APPROVAL,
                              LP.R_NO_LIVE_AUTHORIZATION,
                              CI.R_INTENT_EXPIRED)


@pg
@pytest.mark.asyncio
async def test_a_funded_buy_without_a_canonical_intent_is_refused_before_anything_is_written(
        monkeypatch):
    """Both code switches flipped IN THIS TEST ONLY, the transport
    substituted at pmus._get_client (tests/test_the_funded_path_is_the_real_
    adapter.py): the acquisition is refused at the canonical boundary --
    nothing written, nothing sent."""
    asyncpg = pytest.importorskip("asyncpg")
    from sportsassets import bettor_entry_execution as EX
    from tests import test_the_funded_path_is_the_real_adapter as RA
    conn = await asyncpg.connect(RA.DSN)
    try:
        await RA._seed(conn)
        pmus, sent = RA._substitute_transport(monkeypatch)
        monkeypatch.setattr(FX, "FUNDED_SUBMISSION_ENABLED", True)
        monkeypatch.setattr(EX, "REAL_ORDER_SUBMISSION_ENABLED", True)
        for rec in (RA._decision(),
                    RA._decision(canonical_intent={
                        "intent_id": "cdi_does_not_exist",
                        "content_sha": "0" * 64})):
            got = await FX.submit_for_decision(
                conn, rec, account_id=RA.ACCT, venue=RA.VENUE,
                venue_positions=RA.EMPTY_VENUE)
            assert got["ok"] is False
            assert got["refusal"] == LP.R_NO_CANONICAL, got
            assert got["nothing_was_written"] is True
            assert got["exposure"] == "NONE"
            assert got["canonical_origination"]["ok"] is False
            assert sent == []
            assert await conn.fetchval(
                "SELECT count(*) FROM bettor_funded_intents") == 0
    finally:
        await RA._clean(conn)
        await conn.close()


def test_a_funded_sell_does_not_pass_the_acquisition_boundary():
    src = inspect.getsource(FX.submit_for_decision)
    i_switch = src.index("if not FUNDED_SUBMISSION_ENABLED")
    i_guard = src.index('if not plan.get("sell"):')
    i_canon = src.index("canonical_origination(conn, rec=rec, plan=plan)")
    i_adapter = src.index("mod = _adapter(adapter)")
    assert i_switch < i_guard < i_canon < i_adapter
    # the call sits inside the BUY-only branch
    guard_block = src[i_guard:i_adapter]
    assert "canonical_origination" in guard_block
    assert src.count("canonical_origination(") == 1


# ═════════════════════════════════════════════════════════════════════
# §6 CENSUS
# ═════════════════════════════════════════════════════════════════════

def test_only_the_two_lanes_ask_for_live_exposure():
    callers = set()
    for f in ROOT.rglob("*.py"):
        if "authorize_live_exposure(" in f.read_text():
            callers.add(str(f.relative_to(ROOT)))
    assert callers == {"live_parity.py", "execution_intent.py",
                       "bettor_funded_execution.py"}, callers


def test_the_one_client_create_in_execmirror_is_inside_venue_place():
    src = (ROOT / "execmirror.py").read_text()
    assert src.count("orders.create(") == 1
    place = inspect.getsource(M.Venue.place)
    assert "orders.create(" in place
    assert place.index("_canonical_live_authorized") < place.index(
        "orders.create(")


def test_the_actual_lane_hands_the_venue_only_the_canonical_token():
    src = inspect.getsource(EI.ActualLane._run)
    i_auth = src.index("LP.authorize_live_exposure(")
    i_claim = src.index("INSERT INTO execmirror_orders")
    i_place = src.index(".place,")
    assert i_auth < i_claim < i_place
    assert '"canonical_live_authorization": canon["token"]' in src
