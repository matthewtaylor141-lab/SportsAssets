"""A LOST ACKNOWLEDGEMENT: INVESTIGATED BY THE SCHEDULE, CLOSED ONLY BY AN
AUTHENTICATED, AUDITED DECISION THAT THE CODE RE-READS AT THE VENUE.

ENGINEERING PROOF ON SUBSTITUTED TRANSPORT. The venue client is replaced at
`pmus._get_client`, so every layer above it is the deployed one: the strict
open-orders reader, the strict own-trades walk, `pmus.order_status`, the book,
the reservation machine and the evidence table. The lost acknowledgement itself
is produced the way production produces one -- `acquire_second_leg` with a send
that raises -- not written by hand.

WHAT IS PINNED:

  * the scheduled pass opens a durable investigation, records what it read as
    READ_ESTABLISHED_NOTHING evidence, never adopts a term-matched order, and
    changes neither the intent nor the reservation;
  * the route needs both factors, and the attestation must be signed by the
    identity the resolution key authenticates;
  * every attempt that reaches the resolution logic is audited, accepted or
    refused, and the audit is append-only;
  * NAME_THE_ORDER needs the venue to return that order with every term of the
    request, created inside its window, held by no other intent;
  * NO_EXPOSURE_EXISTS needs the request to be old enough, NO resting order on
    the market and NO own execution there since the send -- two complete reads,
    not an empty open-orders list -- and is recorded under a kind that no venue
    absence check reads;
  * a decision cites the read it was made on; a newer read makes it stale;
  * the same decision retried is idempotent, a different one is refused;
  * nothing is ever submitted.
"""
from __future__ import annotations

import datetime as _dt
import json
import time

import pytest

from sportsassets import bettor_funded_book as FB
from sportsassets import bettor_funded_execution as FX
from sportsassets import bettor_funded_investigation as FI
from sportsassets import bettor_funded_pair_cycle as PC
from sportsassets import bettor_funded_reservations as RSV
from tests import test_the_scheduled_pair_lifecycle as PL

DSN = __import__("os").environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

OPERATOR = "owner-of-the-account"
AUTH = {"admin_token_verified": True, "resolution_key_verified": True,
        "operator": OPERATOR, "route": "test"}
STATEMENT = ("checked the venue's order history and activity for this market "
             "in the account view")


# ════════════════════════════════════════════════════════════════════
# THE SUBSTITUTED VENUE
# ════════════════════════════════════════════════════════════════════

def _iso(epoch: float) -> str:
    return _dt.datetime.fromtimestamp(epoch, _dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ")


def _amt(v):
    return {"value": "%.4f" % v, "currency": "USD"}


class _VOrders:
    def __init__(self, venue):
        self.v = venue

    def list(self, params=None):
        self.v.reads.append("orders.list")
        if self.v.resting_raises:
            raise RuntimeError("the open-orders read failed")
        return {"orders": list(self.v.resting)}

    def retrieve(self, order_id):
        self.v.reads.append(("orders.retrieve", order_id))
        return self.v.by_id.get(order_id)

    def create(self, params):
        self.v.creates += 1
        raise AssertionError("an investigation must never submit")

    def cancel(self, order_id, body=None):
        raise AssertionError("an investigation must never cancel")


class _VPortfolio:
    def __init__(self, venue):
        self.v = venue

    def activities(self, params=None):
        self.v.reads.append("portfolio.activities")
        if self.v.activity_page is not None:
            return self.v.activity_page
        return {"activities": list(self.v.trades), "eof": True}


class _Venue:
    def __init__(self):
        self.resting, self.trades, self.by_id = [], [], {}
        self.resting_raises = False
        self.activity_page = None
        self.reads, self.creates = [], 0
        self.orders = _VOrders(self)
        self.portfolio = _VPortfolio(self)


def _install(monkeypatch, venue):
    from sportsassets import pmus
    monkeypatch.setattr(pmus, "_get_client", lambda: venue)
    return venue


def _raw_order(oid, *, slug=PL.SLUG_HEDGE, intent=FX.LONG, price=PL.HEDGE_PX,
               qty=PL.HEDGE_QTY, filled=0, created=None,
               state="ORDER_STATE_NEW"):
    return {"id": oid, "marketSlug": slug, "intent": intent, "state": state,
            "price": _amt(price), "quantity": qty, "cumQuantity": filled,
            "leavesQuantity": qty - filled,
            **({"createTime": _iso(created)} if created else {})}


def _own_trade(tid, ts, *, slug=PL.SLUG_HEDGE, own="some-order"):
    return {"type": "ACTIVITY_TYPE_TRADE",
            "trade": {"id": tid, "marketSlug": slug, "createTime": _iso(ts),
                      "qty": "4", "isAggressor": True,
                      "aggressorExecution": {"order": {"id": own}}}}


# ════════════════════════════════════════════════════════════════════
# A REAL LOST ACKNOWLEDGEMENT
# ════════════════════════════════════════════════════════════════════

async def _lost_ack(conn, monkeypatch):
    await PL._clean(conn)
    await PL._seed(conn)
    await PL._primary(conn)
    _, _, client = PL._transport(
        monkeypatch, order_id="venue-hedge",
        raise_on_create=TimeoutError("the answer never came back"))
    got = await PC.acquire_second_leg(
        conn, operation_id=PL.OP_HEDGE, group_id=PL.GROUP,
        us_market_slug=PL.SLUG_HEDGE, quantity=PL.HEDGE_QTY,
        limit_price=PL.HEDGE_PX,
        collateral_usd=FX.collateral_for(PL.HEDGE_PX, PL.HEDGE_QTY, FX.LONG),
        decision_record=PL._hedge_decision_record(), account_id=PL.ACCT,
        venue=PL.VENUE, venue_positions=PL.EMPTY_VENUE)
    assert got["refusal"] == FX.R_LOST_ACKNOWLEDGEMENT, got
    assert client.orders.creates == 1
    intent = dict(await conn.fetchrow(
        "SELECT * FROM bettor_funded_intents WHERE intent_id=$1",
        got["intent_id"]))
    assert intent["state"] == "UNRESOLVED"
    assert intent["venue_order_id"] is None
    return intent, client


async def _investigate(conn, *, now):
    got = await FI.investigate_all(conn, account_id=PL.ACCT, venue=PL.VENUE,
                                   now=now)
    assert got["ok"] is True, got
    return got


async def _inv(conn, intent_id):
    r = await conn.fetchrow("SELECT * FROM bettor_funded_investigations "
                            " WHERE intent_id=$1", intent_id)
    return dict(r) if r else None


async def _audits(conn, intent_id):
    return [dict(r) for r in await conn.fetch(
        "SELECT * FROM bettor_funded_resolution_audit WHERE intent_id=$1 "
        " ORDER BY audit_id", intent_id)]


def _body(intent, inv, request, **over):
    b = {"request": request, "confirm": intent["intent_id"],
         "attested_by": OPERATOR, "statement": STATEMENT,
         "seen_read_sha": inv["last_read_sha"]}
    b.update(over)
    return b


async def _unchanged(conn, intent):
    row = await conn.fetchrow(
        "SELECT state, venue_order_id FROM bettor_funded_intents "
        " WHERE intent_id=$1", intent["intent_id"])
    assert row["state"] == "UNRESOLVED" and row["venue_order_id"] is None
    res = await RSV.get(conn, PL.OP_HEDGE)
    assert res["reservation"]["state"] == RSV.AMBIGUOUS
    inv = await _inv(conn, intent["intent_id"])
    assert inv["state"] == FI.OPEN


def _sent(intent) -> float:
    return intent["sent_at"].timestamp()


# ════════════════════════════════════════════════════════════════════
# 1 · THE SCHEDULED INVESTIGATION
# ════════════════════════════════════════════════════════════════════

@pg
async def test_the_schedule_investigates_records_and_changes_nothing(
        monkeypatch):
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        intent, client = await _lost_ack(conn, monkeypatch)
        venue = _install(monkeypatch, _Venue())
        t0 = _sent(intent) + 30
        # A resting order with EVERY TERM of our request: a lead, not ours.
        venue.resting = [_raw_order("venue-maybe-ours", created=t0 - 20)]
        got = await _investigate(conn, now=t0)
        assert got["lost_acknowledgements"] == 1
        one = got["open"][0]
        assert one["resting_on_this_market"] == ["venue-maybe-ours"]
        assert one["exposure"] == "PRESERVED"
        await _unchanged(conn, intent)
        inv = await _inv(conn, intent["intent_id"])
        assert inv["reads"] == 1 and inv["operation_id"] == PL.OP_HEDGE
        ev = [dict(r) for r in await conn.fetch(
            "SELECT kind FROM bettor_funded_operation_evidence "
            " WHERE operation_id=$1", PL.OP_HEDGE)]
        assert [e["kind"] for e in ev] == [RSV.EV_ESTABLISHED_NOTHING]

        # THE SAME PICTURE AGAIN: read, counted, no new evidence row
        await _investigate(conn, now=t0 + 120)
        assert (await _inv(conn, intent["intent_id"]))["reads"] == 2
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_operation_evidence "
            " WHERE operation_id=$1", PL.OP_HEDGE) == 1

        # THE PAIR PASS'S RECOVERY REACHES THE SAME INVESTIGATION and reuses
        # the read the servicing pass just made; the reservation still waits.
        n_reads = len(venue.reads)
        rec = await PC.recover_reservations(
            conn, account_id=PL.ACCT, venue_reader=FI.reservation_reader(),
            now=t0 + 121)
        assert rec["resubmitted_anything"] is False
        assert PL.OP_HEDGE in {w["operation_id"]
                               for w in rec["awaiting_evidence"]}
        assert rec["reads"][0]["read"]["refreshed"] is False
        assert len(venue.reads) == n_reads, "a fresh read is reused, not redone"
        await _unchanged(conn, intent)

        # THE PICTURE CHANGES: a new evidence row, and still nothing adopted
        venue.resting = []
        await _investigate(conn, now=t0 + 300)
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_operation_evidence "
            " WHERE operation_id=$1", PL.OP_HEDGE) == 2
        await _unchanged(conn, intent)
        assert venue.creates == 0 and client.orders.creates == 1
    finally:
        await PL._clean(conn)
        await conn.close()


def test_the_servicing_pass_runs_the_investigation_and_hands_its_reader_on():
    import inspect

    from sportsassets.workers import ext_pinnacle_loop as L
    src = inspect.getsource(L._funded_service)
    assert "_FI.investigate_all(" in src
    assert "venue_reader=_FI.reservation_reader()" in src


# ════════════════════════════════════════════════════════════════════
# 2 · THE ROUTE'S TWO FACTORS AND THE AUTHENTICATED IDENTITY
# ════════════════════════════════════════════════════════════════════

class _Cfg:
    admin_token = "admin-secret-for-the-resolution-test"
    desk_password = "desk"
    operator_password = "operator"
    command_read_password = "desk"
    funded_resolution_key = ""
    funded_resolution_operator = ""


@pytest.fixture()
def client(monkeypatch):
    starlette = pytest.importorskip("starlette.testclient")
    from sportsassets.api import app as A
    cfg = _Cfg()
    monkeypatch.setattr(A, "settings", lambda: cfg, raising=False)
    return starlette.TestClient(A.app, raise_server_exceptions=False), cfg


ROUTE = "/api/admin/funded-investigations/fpi-x/resolve"


def test_the_route_refuses_without_both_factors(client):
    c, cfg = client
    body = {"request": "NO_EXPOSURE_EXISTS", "confirm": "fpi-x"}
    assert c.post(ROUTE, json=body).status_code == 401
    admin = {"X-Admin-Token": cfg.admin_token}
    # the key is not configured: refused by name, not waved through
    r = c.post(ROUTE, json=body, headers=admin)
    assert r.status_code == 503
    assert r.json()["detail"]["reason"] == \
        "FUNDED_RESOLUTION_KEY_NOT_CONFIGURED"
    # a key with no identity bound to it is not configured either
    cfg.funded_resolution_key = "the-owners-resolution-key"
    assert c.post(ROUTE, json=body, headers=admin).status_code == 503
    cfg.funded_resolution_operator = OPERATOR
    r = c.post(ROUTE, json=body, headers=dict(admin, **{
        "X-Resolution-Key": "a-guess"}))
    assert r.status_code == 401
    # the key alone, without the admin token
    r = c.post(ROUTE, json=body, headers={
        "X-Resolution-Key": cfg.funded_resolution_key})
    assert r.status_code == 401


def test_the_route_passes_the_configured_identity_not_the_typed_one():
    import inspect

    from sportsassets.api import app as A
    src = inspect.getsource(A.admin_resolve_funded_investigation)
    assert '"funded_resolution_operator"' in src
    assert "resolve_audited(" in src
    assert "require_resolution_key" in inspect.getsource(A)


# ════════════════════════════════════════════════════════════════════
# 3 · NAMING THE ORDER
# ════════════════════════════════════════════════════════════════════

@pg
async def test_naming_an_order_is_refused_unless_the_venue_confirms_it(
        monkeypatch):
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        intent, _ = await _lost_ack(conn, monkeypatch)
        venue = _install(monkeypatch, _Venue())
        sent = _sent(intent)
        # NOT YET INVESTIGATED: nothing to cite
        early = await FI.resolve_audited(
            conn, intent_id=intent["intent_id"], auth=AUTH,
            body={"request": FI.REQ_NAME_THE_ORDER,
                  "confirm": intent["intent_id"], "attested_by": OPERATOR,
                  "statement": STATEMENT, "venue_order_id": "x",
                  "seen_read_sha": "0" * 64}, now=sent + 60)
        assert early["refusal"] == FI.R_NOT_YET_INVESTIGATED, early
        await _investigate(conn, now=sent + 60)
        inv = await _inv(conn, intent["intent_id"])
        venue.by_id = {
            "o-qty": {"order": _raw_order("o-qty", qty=11,
                                          created=sent + 2)},
            "o-old": {"order": _raw_order("o-old", created=sent - 3600)},
            "o-late": {"order": _raw_order("o-late", created=sent + 7200)},
            "o-notime": {"order": _raw_order("o-notime")},
            "o-side": {"order": _raw_order("o-side", intent="ORDER_INTENT_BUY_SHORT",
                                           created=sent + 2)},
            "o-rej": {"order": _raw_order("o-rej", created=sent + 2,
                                          state="ORDER_STATE_REJECTED")},
        }
        cases = [
            (dict(attested_by="someone-else"),
             FI.R_ATTESTER_NOT_AUTHENTICATED),
            (dict(confirm="fpi-another"), FI.R_CONFIRM),
            (dict(statement="ok"), FI.R_STATEMENT),
            (dict(seen_read_sha=None), FI.R_EVIDENCE_NOT_REFERENCED),
            (dict(seen_read_sha="f" * 64), FI.R_STALE_EVIDENCE),
            (dict(venue_order_id="o-missing"), FI.R_ORDER_UNREADABLE),
            (dict(venue_order_id="o-qty"), FI.R_ORDER_TERMS_DIFFER),
            (dict(venue_order_id="o-side"), FI.R_ORDER_TERMS_DIFFER),
            (dict(venue_order_id="o-old"), FI.R_ORDER_OUTSIDE_WINDOW),
            (dict(venue_order_id="o-late"), FI.R_ORDER_OUTSIDE_WINDOW),
            (dict(venue_order_id="o-notime"), FI.R_ORDER_TIME_UNREADABLE),
            (dict(venue_order_id="o-rej"), FI.R_ORDER_WAS_REJECTED),
            (dict(request="ADOPT_IT"), FI.R_UNKNOWN_REQUEST),
        ]
        for over, refusal in cases:
            body = _body(intent, inv, FI.REQ_NAME_THE_ORDER,
                         venue_order_id="o-qty")
            body.update(over)
            got = await FI.resolve_audited(conn, intent_id=intent["intent_id"],
                                           body=body, auth=AUTH,
                                           now=sent + 90)
            assert got["ok"] is False, (over, got)
            assert got["refusal"] == refusal, (over, got)
            assert got["submitted_anything"] is False
            await _unchanged(conn, intent)
        # A DIFFERENT INTENT ALREADY HOLDS THE ORDER
        await conn.execute(
            "UPDATE bettor_funded_intents SET venue_order_id='o-held' "
            " WHERE intent_id=$1", PL.PRIMARY_INTENT)
        venue.by_id["o-held"] = {"order": _raw_order("o-held",
                                                     created=sent + 2)}
        got = await FI.resolve_audited(
            conn, intent_id=intent["intent_id"], auth=AUTH, now=sent + 90,
            body=_body(intent, inv, FI.REQ_NAME_THE_ORDER,
                       venue_order_id="o-held"))
        assert got["refusal"] == FI.R_ORDER_CLAIMED, got
        # EVERY ONE OF THOSE ATTEMPTS IS ON THE AUDIT, REFUSED, BY NAME
        audits = await _audits(conn, intent["intent_id"])
        assert [a["refusal"] for a in audits] == \
            [FI.R_NOT_YET_INVESTIGATED] + [r for _, r in cases] \
            + [FI.R_ORDER_CLAIMED]
        assert {a["outcome"] for a in audits} == {"REFUSED"}
        assert audits[-1]["requested"] == FI.REQ_NAME_THE_ORDER
        assert audits[-2]["requested"] == FI.REQ_UNRECOGNISED
        auth = audits[-1]["authenticated_by"]
        auth = json.loads(auth) if isinstance(auth, str) else auth
        assert auth["operator"] == OPERATOR
        assert venue.creates == 0
    finally:
        await PL._clean(conn)
        await conn.close()


@pg
async def test_a_named_order_is_booked_consumed_and_audited_once(monkeypatch):
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        intent, client = await _lost_ack(conn, monkeypatch)
        venue = _install(monkeypatch, _Venue())
        sent = _sent(intent)
        venue.resting = [_raw_order("venue-ours", filled=4, created=sent + 3,
                                    state="ORDER_STATE_PARTIALLY_FILLED")]
        await _investigate(conn, now=sent + 60)
        inv = await _inv(conn, intent["intent_id"])
        venue.by_id["venue-ours"] = {
            "order": _raw_order("venue-ours", filled=4, created=sent + 3,
                                state="ORDER_STATE_PARTIALLY_FILLED"),
            "executions": [{"id": "vf-ours-1", "type": "EXECUTION_TYPE_FILL",
                            "lastPx": _amt(PL.HEDGE_PX), "lastShares": 4,
                            "order": {"state":
                                      "ORDER_STATE_PARTIALLY_FILLED"}}]}
        body = _body(intent, inv, FI.REQ_NAME_THE_ORDER,
                     venue_order_id="venue-ours")
        got = await FI.resolve_audited(conn, intent_id=intent["intent_id"],
                                       body=body, auth=AUTH, now=sent + 90)
        assert got["ok"] is True, got
        assert got["investigation_state"] == FI.RESOLVED_ORDER_NAMED
        row = await conn.fetchrow(
            "SELECT state, venue_order_id, residual_qty FROM "
            " bettor_funded_intents WHERE intent_id=$1", intent["intent_id"])
        assert row["venue_order_id"] == "venue-ours"
        assert row["state"] == "PARTIALLY_FILLED"
        assert float(row["residual_qty"]) == pytest.approx(4.0)
        fills = await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_fills WHERE intent_id=$1",
            intent["intent_id"])
        assert fills == 1
        res = await RSV.get(conn, PL.OP_HEDGE)
        assert res["reservation"]["state"] == RSV.CONSUMED
        named = dict(await conn.fetchrow(
            "SELECT * FROM bettor_funded_operation_evidence "
            " WHERE operation_id=$1 AND kind=$2", PL.OP_HEDGE, RSV.EV_NAMED))
        scope = named["search_scope"]
        scope = json.loads(scope) if isinstance(scope, str) else scope
        assert scope["named_by"] == "OPERATOR"
        assert named["venue_order_id"] == "venue-ours"
        [acc] = [a for a in await _audits(conn, intent["intent_id"])
                 if a["outcome"] == "ACCEPTED"]
        assert str(acc["audit_id"]) == str(scope["audit_id"])

        # THE SAME DECISION RETRIED: idempotent, audited, nothing re-booked
        again = await FI.resolve_audited(conn, intent_id=intent["intent_id"],
                                         body=body, auth=AUTH, now=sent + 95)
        assert again["ok"] is True and again["already"] is True
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_fills WHERE intent_id=$1",
            intent["intent_id"]) == 1
        # A DIFFERENT DECISION ON THE SAME INTENT: refused, not "already"
        other = await FI.resolve_audited(
            conn, intent_id=intent["intent_id"], auth=AUTH, now=sent + 2000,
            body=_body(intent, inv, FI.REQ_NO_EXPOSURE_EXISTS))
        assert other["ok"] is False
        assert other["refusal"] == FI.R_RESOLVED_DIFFERENTLY
        outcomes = [a["outcome"] for a in await _audits(conn,
                                                        intent["intent_id"])]
        assert outcomes == ["ACCEPTED", "ALREADY_RESOLVED", "REFUSED"]
        assert venue.creates == 0 and client.orders.creates == 1
    finally:
        await PL._clean(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# 4 · ATTESTING THAT NO EXPOSURE EXISTS
# ════════════════════════════════════════════════════════════════════

@pg
async def test_absence_is_refused_while_anything_could_still_be_ours(
        monkeypatch):
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        intent, _ = await _lost_ack(conn, monkeypatch)
        venue = _install(monkeypatch, _Venue())
        sent = _sent(intent)
        await _investigate(conn, now=sent + 60)
        inv = await _inv(conn, intent["intent_id"])
        body = _body(intent, inv, FI.REQ_NO_EXPOSURE_EXISTS)
        late = sent + FI.SETTLE_S + 60

        async def _try(now):
            got = await FI.resolve_audited(conn, intent_id=intent["intent_id"],
                                           body=body, auth=AUTH, now=now)
            assert got["ok"] is False, got
            await _unchanged(conn, intent)
            return got["refusal"]

        # 1 · too soon for absence to mean anything
        assert await _try(sent + 60) == FI.R_TOO_SOON
        # 2 · ANY order resting on the market -- ours or not -- blocks it
        venue.resting = [_raw_order("manual-at-another-price", price=0.55,
                                    created=sent - 7200)]
        assert await _try(late) == FI.R_RESTING_ON_MARKET
        venue.resting = []
        # 3 · the account executed on the market after the send
        venue.trades = [_own_trade("t-after", sent + 5)]
        assert await _try(late) == FI.R_EXECUTED_SINCE_SEND
        venue.trades = []
        # 4 · the reads do not establish anything
        venue.resting_raises = True
        assert await _try(late) == FI.R_RESTING_UNREADABLE
        venue.resting_raises = False
        venue.activity_page = {"activities": None, "eof": True}
        assert await _try(late) == FI.R_EXECUTIONS_UNREADABLE
        venue.activity_page = {"activities": [], "eof": False}
        assert await _try(late) == FI.R_EXECUTIONS_UNREADABLE
        venue.activity_page = {"activities": [], "eof": "true"}
        assert await _try(late) == FI.R_EXECUTIONS_UNREADABLE
        assert venue.creates == 0
    finally:
        await PL._clean(conn)
        await conn.close()


@pg
async def test_an_attested_absence_releases_the_leg_and_is_never_a_venue_statement(
        monkeypatch):
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        intent, client = await _lost_ack(conn, monkeypatch)
        venue = _install(monkeypatch, _Venue())
        sent = _sent(intent)
        # An order on ANOTHER market, and a trade on another market: neither
        # says anything about this request.
        venue.resting = [_raw_order("elsewhere", slug="aec-other-market",
                                    created=sent)]
        venue.trades = [_own_trade("t-else", sent + 5, slug="aec-other")]
        late = sent + FI.SETTLE_S + 60
        await _investigate(conn, now=late - 30)
        inv = await _inv(conn, intent["intent_id"])
        exposure_before = await FB.exposure(conn, account_id=PL.ACCT,
                                            venue=PL.VENUE)
        got = await FI.resolve_audited(
            conn, intent_id=intent["intent_id"], auth=AUTH, now=late,
            body=_body(intent, inv, FI.REQ_NO_EXPOSURE_EXISTS))
        assert got["ok"] is True, got
        assert got["investigation_state"] == FI.RESOLVED_NO_EXPOSURE
        row = await conn.fetchrow(
            "SELECT state, venue_order_id FROM bettor_funded_intents "
            " WHERE intent_id=$1", intent["intent_id"])
        assert row["state"] == "ABANDONED" and row["venue_order_id"] is None
        res = (await RSV.get(conn, PL.OP_HEDGE))["reservation"]
        assert res["state"] == RSV.RELEASED
        assert res["resolution"].startswith(
            RSV.WHY_OPERATOR_ATTESTED_NO_EXPOSURE)
        # RECORDED AS AN ATTESTATION, AND NOTHING READS IT AS THE VENUE'S WORD
        kinds = [r["kind"] for r in await conn.fetch(
            "SELECT kind FROM bettor_funded_operation_evidence "
            " WHERE operation_id=$1 ORDER BY recorded_at", PL.OP_HEDGE)]
        assert RSV.EV_ATTESTED_NO_EXPOSURE in kinds
        assert RSV.EV_NO_SUCH_ORDER not in kinds
        venue_absence = await RSV._usable_evidence(
            conn, operation_id=PL.OP_HEDGE, kind=RSV.EV_NO_SUCH_ORDER,
            reservation=res)
        assert venue_absence["ok"] is False
        # THE EXPOSURE THE LOST SEND HELD IS GONE, AND ONLY IT
        after = await FB.exposure(conn, account_id=PL.ACCT, venue=PL.VENUE)
        assert after != exposure_before
        live = [r["intent_id"] for r in await FB.live_intents(
            conn, account_id=PL.ACCT, venue=PL.VENUE)]
        assert intent["intent_id"] not in live
        # THE LEG CAN BE RESERVED AGAIN -- the claim is released, not ignored
        again = await RSV.hold(
            conn, operation_id="op:after-attestation", group_id=PL.GROUP,
            leg_role="HEDGE", us_market_slug=PL.SLUG_HEDGE,
            quantity=PL.HEDGE_QTY, limit_price=PL.HEDGE_PX,
            collateral_usd=FX.collateral_for(PL.HEDGE_PX, PL.HEDGE_QTY,
                                             FX.LONG))
        assert again["ok"] is True, again
        await RSV.release(conn, operation_id="op:after-attestation",
                          why="test")
        # A BOOLEAN IS NOT AN ATTESTATION: the database refuses the kind
        # without an attester and an audit id
        with pytest.raises(Exception):
            await conn.execute(
                "INSERT INTO bettor_funded_operation_evidence (evidence_id, "
                " operation_id, account_id, venue, us_market_slug, kind, "
                " search_endpoint, covered_terminal_orders, read_at) VALUES "
                " ('ev:bare', $1, $2, $3, $4, $5, 'none', false, now())",
                PL.OP_HEDGE, PL.ACCT, PL.VENUE, PL.SLUG_HEDGE,
                RSV.EV_ATTESTED_NO_EXPOSURE)
        assert venue.creates == 0 and client.orders.creates == 1
    finally:
        await conn.execute("DELETE FROM bettor_funded_operation_evidence "
                           " WHERE operation_id='op:after-attestation'")
        await PL._clean(conn)
        await conn.close()


@pg
async def test_an_effect_that_fails_half_way_leaves_nothing_and_is_audited(
        monkeypatch):
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        intent, _ = await _lost_ack(conn, monkeypatch)
        _install(monkeypatch, _Venue())
        sent = _sent(intent)
        late = sent + FI.SETTLE_S + 60
        await _investigate(conn, now=late - 30)
        inv = await _inv(conn, intent["intent_id"])

        async def _refuse(conn, **kw):
            return {"ok": False, "refusal": "SIMULATED"}
        monkeypatch.setattr(RSV, "release_on_attestation", _refuse)
        got = await FI.resolve_audited(
            conn, intent_id=intent["intent_id"], auth=AUTH, now=late,
            body=_body(intent, inv, FI.REQ_NO_EXPOSURE_EXISTS))
        assert got["ok"] is False and got["rolled_back"] is True, got
        # THE INTENT WAS ABANDONED INSIDE THE TRANSACTION, AND ROLLED BACK
        await _unchanged(conn, intent)
        [a] = await _audits(conn, intent["intent_id"])
        assert a["outcome"] == "REFUSED"
        assert a["refusal"] == FI.R_RESERVATION_NOT_MOVED
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_operation_evidence "
            " WHERE operation_id=$1 AND kind=$2", PL.OP_HEDGE,
            RSV.EV_ATTESTED_NO_EXPOSURE) == 0
    finally:
        await PL._clean(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# 5 · THE RECORD CANNOT BE REWRITTEN
# ════════════════════════════════════════════════════════════════════

@pg
async def test_the_audit_and_a_resolved_investigation_are_not_rewritten(
        monkeypatch):
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        intent, _ = await _lost_ack(conn, monkeypatch)
        _install(monkeypatch, _Venue())
        sent = _sent(intent)
        late = sent + FI.SETTLE_S + 60
        await _investigate(conn, now=late - 30)
        inv = await _inv(conn, intent["intent_id"])
        got = await FI.resolve_audited(
            conn, intent_id=intent["intent_id"], auth=AUTH, now=late,
            body=_body(intent, inv, FI.REQ_NO_EXPOSURE_EXISTS))
        assert got["ok"] is True, got
        aid = got["audit_id"]
        for sql in ("UPDATE bettor_funded_resolution_audit SET "
                    " attested_by='someone' WHERE audit_id=$1",
                    "DELETE FROM bettor_funded_resolution_audit "
                    " WHERE audit_id=$1"):
            with pytest.raises(asyncpg.exceptions.RaiseError):
                await conn.execute(sql, aid)
        for sql in ("UPDATE bettor_funded_investigations SET state='OPEN', "
                    " resolved_at=NULL, resolution=NULL WHERE intent_id=$1",
                    "DELETE FROM bettor_funded_investigations "
                    " WHERE intent_id=$1"):
            with pytest.raises(asyncpg.exceptions.RaiseError):
                await conn.execute(sql, intent["intent_id"])
    finally:
        await PL._clean(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# 6 · AN EXIT IS MATCHED ON WHAT IT SELLS
# ════════════════════════════════════════════════════════════════════

def test_an_exit_lost_ack_is_matched_on_its_sell_intent():
    exit_long = {"kind": "EXIT", "order_intent": FX.LONG,
                 "us_market_slug": "aec-x", "limit_price": 0.61,
                 "quantity": 5}
    exit_short = dict(exit_long, order_intent="ORDER_INTENT_BUY_SHORT")
    assert FI.venue_intent_of(exit_long) == "ORDER_INTENT_SELL_LONG"
    assert FI.venue_intent_of(exit_short) == "ORDER_INTENT_SELL_SHORT"
    assert FI.venue_intent_of(dict(exit_long, kind="ENTRY")) == FX.LONG
    sell = {"us_market_slug": "aec-x", "intent": "ORDER_INTENT_SELL_LONG",
            "price": 0.61, "quantity": 5}
    assert FI._terms_disagree(exit_long, sell) == []
    assert FI._terms_disagree(exit_long, dict(sell, intent=FX.LONG)) == \
        ["order_intent"]
    assert FI._terms_disagree(exit_short, sell) == ["order_intent"]
