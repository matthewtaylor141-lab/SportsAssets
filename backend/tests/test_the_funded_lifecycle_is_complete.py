"""THE FIVE CORRECTIONS THE REVIEW OF acd7cae ASKED FOR.

Each was real, and each was a case where the previous round's fix was aimed at
the right thing and stopped short of it:

  1. SERVICING STILL DEPENDED ON ENTRY PERMISSION. `submit_exit` required an
     AFFIRMATIVE `authorize_submission` -- the entry gate -- so an expired or
     revoked grant, or `REAL_ORDER_SUBMISSION_ENABLED` being off, blocked the
     exit as well. The separate servicing switch bought nothing. And
     `_funded_service` ran at the BOTTOM of `cycle()`, after three early
     returns, so a paused entry loop or a missing odds credential stopped the
     funded book being reconciled at all.

  2. EXITS HAD NO INVENTORY RESERVATION. EXIT rows are deliberately outside the
     one-open-position index, so nothing at all sat between reading the residual
     and inserting the exit. Two concurrent full exits both sold the whole
     position; so did a retry after an ambiguous answer, because the first
     attempt's UNRESOLVED exit consumed no fills and so did not appear in the
     residual.

  3. RECOVERY STILL INFERRED OWNERSHIP. Slug AND side AND exact limit AND exact
     clip identify an order with our TERMS. One manual order satisfies all four.
     The timestamp check fell back to those four terms whenever either
     timestamp was unreadable -- which is the common case for an order the venue
     never acknowledged.

  4. ACCOUNTING DID NOT SURVIVE INTERRUPTION. The fill went in, then its cash
     and fee events, as separate statements. Stop between them and the
     redelivery hit ON CONFLICT DO NOTHING and skipped the events permanently.
     A provisional fee could never be replaced by the venue's later charge. And
     a void refunded the parent's whole entry cost, ignoring the exit children.

  5. `manage()` NEVER SELECTED AN ACTION. It reconciled, checked settlement and
     emitted `needs_a_decision`. That is servicing infrastructure, not exit
     management.
"""

from __future__ import annotations

import json
import time

import pytest

from sportsassets import bettor_funded_pair_cycle as PC

from sportsassets import bettor_entry_execution as EX
from sportsassets import bettor_funded_activation as FA
from sportsassets import bettor_funded_book as FB
from sportsassets import bettor_funded_execution as FX
from sportsassets import bettor_funded_management as FM

DSN = __import__("os").environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

ACCT = "acct-funded-complete-test"
VENUE = "PMUS"
SLUG = "aec-nba-bos-lal-2026-09-27"
EVENT = "ev-bos-lal-2026-09-27"
PAYS_ON = "BOSTON_CELTICS"
LIMITS = {"capital_usd": 400, "per_order_usd": 60, "event_exposure_usd": 60,
          "max_exposure_usd": 200, "daily_loss_stop_usd": 40}


# ── THE VENUE'S TRANSPORT, SUBSTITUTED AT `pmus._get_client` ────────

def _level(px, qty):
    """One ladder level in the VENUE's own shape."""
    return {"px": {"value": "%.2f" % px, "currency": "USD"}, "qty": str(qty)}


class _Orders:
    def __init__(self, sent, *, execs=None, order_id="venue-ord-1",
                 resting=(), retrieve=None, raise_on_create=None,
                 exec_by_call=None):
        self.sent = sent
        self._execs = execs
        self._oid = order_id
        self._resting = list(resting)
        self._retrieve = retrieve or {}
        self._raise = raise_on_create
        self._by_call = list(exec_by_call or [])
        self._creates = 0

    def preview(self, body):
        req = (body or {}).get("request") or {}
        self.sent.append(("preview", req))
        px = float((req.get("price") or {}).get("value") or 0)
        qty = int(req.get("quantity") or 0)
        cost = ((1.0 - px) * qty
                if req.get("intent") == "ORDER_INTENT_BUY_SHORT" else px * qty)
        return {"order": {"price": req.get("price"),
                          "quantity": req.get("quantity"),
                          "cashOrderQty": {"value": "%.4f" % cost,
                                           "currency": "USD"}}}

    def create(self, params):
        self.sent.append(("create", dict(params)))
        self._creates += 1
        if self._raise is not None:
            raise self._raise
        qty = int(params.get("quantity") or 0)
        px = float((params.get("price") or {}).get("value") or 0)
        if self._by_call:
            execs = self._by_call[min(self._creates - 1,
                                      len(self._by_call) - 1)]
        elif self._execs is not None:
            execs = self._execs
        else:
            execs = [{"id": "vf-%d" % self._creates,
                      "type": "EXECUTION_TYPE_FILL",
                      "lastPx": {"value": "%.2f" % px, "currency": "USD"},
                      "lastShares": qty,
                      "order": {"state": "ORDER_STATE_FILLED"}}]
        return {"id": "%s-%d" % (self._oid, self._creates),
                "executions": execs}

    def list(self, params=None):
        self.sent.append(("list", dict(params or {})))
        return {"orders": list(self._resting)}

    def retrieve(self, order_id):
        self.sent.append(("retrieve", order_id))
        return self._retrieve.get(str(order_id))

    def cancel(self, order_id, body=None):
        self.sent.append(("cancel", order_id))
        return {}


#: "the caller said nothing", as distinct from "the venue sent no clock".
_UNSET_TS = object()


def _venue_clock(age_s=0.0):
    """A `marketData.transactTime` the supported parser accepts: ISO 8601 UTC
    with a bare Z. This is the ONLY thing that establishes the book's age --
    our own read clock would make every book fresh by construction."""
    import datetime as _dt

    t = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=age_s)
    return t.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class _Markets:
    def __init__(self, bids=None, offers=None, raise_on_book=False,
                 book_age_s=0.0, transact_time=_UNSET_TS):
        self._bids, self._offers = bids, offers
        self._raise = raise_on_book
        self._age = book_age_s
        self._ts = transact_time

    def retrieve_by_slug(self, slug):
        return {"market": {"marketSides": [
            {"identifier": slug + "-a", "description": "A"},
            {"identifier": slug + "-b", "description": "B"}]}}

    def list(self, params=None):
        # THE LISTING ROW'S TICK, as the venue publishes it on every market
        # (SYNTHETIC 0.01). The exit ladder is restricted to the executable
        # grid, which refuses a market whose tick was not read.
        return {"markets": [{"slug": s, "orderPriceMinTickSize": "0.01"}
                            for s in (params or {}).get("slug") or []]}

    def book(self, slug):
        if self._raise:
            raise RuntimeError("the venue's book feed is unreachable")
        if self._bids is None and self._offers is None:
            return {}
        md = {"bids": list(self._bids or []),
              "offers": list(self._offers or [])}
        # THE VENUE'S OWN CLOCK. `transact_time=None` models a venue that sent
        # none, which is UNMEASURED and refuses -- not a stale book.
        if self._ts is _UNSET_TS:
            md["transactTime"] = _venue_clock(self._age)
        elif self._ts is not None:
            md["transactTime"] = self._ts
        return {"marketData": md}


class _Client:
    def __init__(self, sent, *, bids=None, offers=None, raise_on_book=False,
                 book_age_s=0.0, transact_time=_UNSET_TS, **kw):
        self.orders = _Orders(sent, **kw)
        self.markets = _Markets(bids=bids, offers=offers,
                                raise_on_book=raise_on_book,
                                book_age_s=book_age_s,
                                transact_time=transact_time)


def _transport(monkeypatch, **kw):
    from sportsassets import pmus
    sent: list = []
    client = _Client(sent, **kw)
    monkeypatch.setattr(pmus, "_get_client", lambda: client)
    monkeypatch.setattr(pmus._gate, "authorize", lambda *a, **k: {"ok": True})
    return pmus, sent, client


def _live(now=None):
    """THE MECHANISM, SUPPLIED EXPLICITLY, and that is the point.

    Since the freshness rule became shared, a venue book is admitted only when a
    mechanism with a published contract establishes that it is current -- a live
    market-data subscription (M1) or a conditional revalidation answered 304
    (M2). A stub venue returning a payload establishes NOTHING, so every test
    whose subject is the lifecycle downstream of admission has to say which
    contract admitted the book. Passing this is that statement.

    A test that omits it is testing the refusal, and several deliberately do.
    """
    at = time.time() if now is None else float(now)
    return {"alive_at": at - 0.5, "last_update_at": at - 1.0}


async def _seed(conn, *, limits=None, expires_in=3600.0, revoked=False,
                paused=False, accounting="RECONCILED"):
    await conn.execute("DELETE FROM bettor_desk_accounts WHERE account_id=$1",
                       ACCT)
    await conn.execute(
        "INSERT INTO bettor_desk_accounts (account_id, desk_id, status, "
        " paused, accounting_status, opening_balance, note) VALUES "
        "($1,'desk-complete-test','ACTIVE',$2,$3,0,'complete test')",
        ACCT, paused, accounting)
    lim = dict(limits or LIMITS)
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1,$2::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value=$2::jsonb", FA.LIMITS_KEY,
        json.dumps({"proposed": lim, "approved": True,
                    "approved_by": "OWNER"}))
    now = time.time()
    eff = EX.effective_limits(lim)
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1,$2::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value=$2::jsonb",
        FA.AUTHORIZATION_KEY,
        json.dumps({"account_id": ACCT, "venue": VENUE,
                    "venue_class": FA.VENUE_FUNDED, "by": "test",
                    "at": now, "expires_at": now + expires_in,
                    "revoked": revoked,
                    "effective_limits": eff["effective"],
                    "effective_digest": eff["effective_digest"]}))


async def _clean(conn):
    await conn.execute("DELETE FROM bettor_funded_discrepancies")
    await conn.execute("DELETE FROM bettor_funded_economics")
    await conn.execute("DELETE FROM bettor_funded_fills")
    await conn.execute("DELETE FROM bettor_funded_intents")
    await conn.execute("DELETE FROM external_valuations WHERE venue=$1",
                       "PMUS_TEST_COMPLETE")
    await conn.execute("DELETE FROM bettor_desk_accounts WHERE account_id=$1",
                       ACCT)
    for k in (FA.AUTHORIZATION_KEY, FA.LIMITS_KEY, FA.ACCOUNT_KEY):
        await conn.execute("DELETE FROM ingestion_state WHERE key=$1", k)


async def _entry(conn, *, intent_id="fpi-a", qty=10, price=0.62,
                 payout_event=PAYS_ON, vo="vo-1", fill_id="vf-1",
                 fill_qty=None, commission=None, intent=None):
    opened = intent or FX.LONG
    coll = FX.collateral_for(price, qty, opened)
    got = await FB.record_intent(
        conn, intent_id=intent_id, account_id=ACCT, venue=VENUE,
        venue_class=FA.VENUE_FUNDED, us_market_slug=SLUG, event_key=EVENT,
        order_intent=opened, limit_price=price, quantity=qty,
        collateral_usd=coll, effective_digest="d",
        payout_event=payout_event, held_is_long=(opened == FX.LONG))
    if not got.get("ok"):
        return got
    await FB.record_acknowledgement(conn, intent_id, venue_order_id=vo,
                                    status="open")
    f = {"qty": float(qty if fill_qty is None else fill_qty), "price": price,
         "venue_fill_id": fill_id}
    if commission is not None:
        f["commission_usd"] = commission
    return dict(got, fills=await FB.ingest_fills(conn, intent_id, [f]))


#: AN ESTABLISHED TERMINAL RULE, in the shape `bettor_venue_settlement.attest`
#: produces and `bettor_hold_value._terminal_rule` reads. `overall_established`
#: with no `unmet` is what makes the compatibility ESTABLISHED -- which a
#: FUNDED exit requires. Anything less is UNKNOWN, which the shadow book
#: retains as conditional and this lane refuses.
SETTLED_RULE = {
    "overall_established": True,
    "unmet": [],
    "attested": ["DRAW", "OVERTIME", "PUSH", "VOID"],
    "book_rule": "MONEYLINE_REGULATION_PLUS_OVERTIME",
    "venue_rules_text_read": True,
    "venue_rules_field": "rulesText",
}
#: AND THE UNESTABLISHED ONE: the venue published nothing to compare, so the
#: four conditions are unmet. This is the common production case.
UNSETTLED_RULE = {
    "overall_established": False,
    "unmet": ["DRAW_NOT_STATED", "OVERTIME_NOT_STATED"],
    "attested": [],
    "venue_rules_text_read": False,
}


async def _probability(conn, *, p=0.55, slug=SLUG, at=None,
                       eligibility="ELIGIBLE", settled=True,
                       event_state="IN_PROGRESS"):
    """AN ELIGIBLE EXTERNAL VALUATION ROW, in the shape `ev_hold` reads --
    carrying the fixture and settlement evidence the entry decision had."""
    when = at if at is not None else time.time()
    await conn.execute(
        "INSERT INTO external_valuations (experiment_id, version, "
        " source_class, provider, book, devig_method, venue, "
        " contract_selection, sport_family, market, raw_odds, "
        " outcomes_priced, expected_outcomes, decision, admissible, why, "
        " refusals, us_market_slug, probability, probability_event, "
        " payout_event, payout_is_complement, buy_intent, observed_at, "
        " received_at, age_s, eligibility, decided_at, executable_price, "
        " cost_per_contract, estimated_edge_per_contract, mapped_outcome, "
        " ineligible_reason, settlement_rule, settlement_comparison) "
        "VALUES ('EXP','v','EXTERNAL_BOOKMAKER_VALUATION','PINNACLE','pinnacle','multiplicative',"
        " $1,$2,'basketball','WINNER','{}'::jsonb,2,2,'BUY',TRUE,'t',"
        " ARRAY[]::text[],$3,$4,$5,$5,FALSE,$6,to_timestamp($7),"
        " to_timestamp($7),0.5,$8,to_timestamp($7),0.5,0.5,0.05,$2,$9,"
        " $10::jsonb,$11::jsonb)",
        "PMUS_TEST_COMPLETE", PAYS_ON, slug, float(p), PAYS_ON, FX.LONG,
        float(when), eligibility,
        (None if eligibility == "ELIGIBLE" else "HELD_BY_A_TEST"),
        json.dumps(SETTLED_RULE if settled else UNSETTLED_RULE),
        json.dumps({"verdict": ("COMPATIBLE" if settled else "UNKNOWN"),
                    "fixture_event_state": event_state,
                    "fixture_read": True}))


# ════════════════════════════════════════════════════════════════════
# 1 · SERVICING DOES NOT DEPEND ON ENTRY PERMISSION
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
@pytest.mark.parametrize("broken", ["EXPIRED", "REVOKED", "PAUSED_ACCOUNT",
                                    "SUBMISSION_DISABLED"])
async def test_an_exit_survives_every_entry_side_lapse(monkeypatch, broken):
    """EVERY WAY ENTRY PERMISSION CAN LAPSE, AND THE EXIT STILL REACHES ITS
    OWN SWITCH. Each of these used to refuse the exit before the servicing
    switch was ever consulted."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn,
                    expires_in=(-60.0 if broken == "EXPIRED" else 3600.0),
                    revoked=(broken == "REVOKED"),
                    paused=(broken == "PAUSED_ACCOUNT"))
        await _entry(conn)
        # SUBMISSION_DISABLED is the shipped state and needs no monkeypatch.
        got = await FM.submit_exit(conn, intent_id="fpi-a", limit_price=0.70)
        # IT REACHES THE SERVICING SWITCH -- the last thing standing.
        assert got["refusal"] == FM.R_EXIT_DISABLED, (broken, got["refusal"])
        assert got["servicing_gate"]["ok"] is True
        assert got["servicing_gate"]["needs_a_live_submission_grant"] is False
        rec = got["submission_authority_for_the_record"]
        assert rec["does_not_gate_this_exit"] is True
        if broken in ("EXPIRED", "REVOKED"):
            assert rec["valid_for_new_exposure"] is False
        assert rec["affirmative"] is False
        # AND NEW EXPOSURE IS STILL REFUSED, which is the other half.
        pmus, sent, _ = _transport(monkeypatch)
        monkeypatch.setattr(FX, "FUNDED_SUBMISSION_ENABLED", True)
        monkeypatch.setattr(EX, "REAL_ORDER_SUBMISSION_ENABLED", True)
        entry = await FX.submit_for_decision(
            conn, {"admissible": True, "us_market_slug": SLUG,
                   "event_key": EVENT, "order_intent": FX.LONG,
                   "payout_event": PAYS_ON,
                   "execution_plan": {"execution": {
                       "size": 5, "vwap": 0.62, "limit_price": 0.62}}},
            account_id=ACCT, venue=VENUE)
        assert entry["ok"] is False, broken
        assert [k for k, _ in sent if k == "create"] == []
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_an_old_stamp_with_a_live_subscription_is_admitted(monkeypatch):
    """THE OTHER SIDE OF THE SAME CORRECTION, ON THE FUNDED LANE.

    Ten minutes since the venue stamped this book, and a market-data
    subscription proven alive one second ago reporting this market's last update
    one second before that. The stamp is recorded as past the bound and it
    refuses nothing: an unresolved field cannot be read as evidence of
    staleness. The mechanism governs, and the exit proceeds to its economics.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        await _entry(conn, qty=10, price=0.60)
        await _probability(conn, p=0.55)
        pos = (await FM.open_positions(conn, account_id=ACCT,
                                       venue=VENUE))[0]
        _, sent, client = _transport(monkeypatch, bids=[_level(0.75, 40)],
                                    book_age_s=600.0)
        got = await FM.select_exit(conn, pos, client=client,
                                   subscription=_live())
        age = got["venue_book_age"]
        assert age["ok"] is True, age
        assert age["stamp_beyond_the_bound"] is True
        assert age["book_currency"]["mechanism"] == (
            "M1_LIVE_MARKET_DATA_SUBSCRIPTION")
        assert got["refusal"] != FM.R_BOOK_NOT_FRESH, got
        # AND NOTHING WAS SENT: submission stays disabled regardless.
        assert [k for k, _ in sent if k == "create"] == []
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_servicing_refuses_another_accounts_position():
    """A LAPSED GRANT IS NOT A LICENCE TO SERVICE SOMEBODY ELSE'S ROW.
    Ownership comes from the position's own row, not the authorization
    record -- which is a single replaceable row."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        await _entry(conn)
        good = await FB.check_servicing(conn, intent_id="fpi-a",
                                       account_id=ACCT, venue=VENUE)
        assert good["ok"] is True and good["owns_the_row"] is True
        bad = await FB.check_servicing(conn, intent_id="fpi-a",
                                      account_id="acct-somebody-else",
                                      venue=VENUE)
        assert bad["ok"] is False
        assert bad["refusal"] == FB.R_NOT_OUR_ROW
        assert bad["row_belongs_to"]["account_id"] == ACCT
        wrong_venue = await FB.check_servicing(conn, intent_id="fpi-a",
                                              account_id=ACCT,
                                              venue="PMUS_TEST")
        assert wrong_venue["ok"] is False
    finally:
        await _clean(conn)
        await conn.close()


def test_the_scheduled_servicing_runs_before_every_entry_gate():
    """STRUCTURAL, ON THE PARSED SOURCE. `_funded_service` must be called
    before the observation stop, the table check and the odds credential --
    each of which is a reason to add nothing and none of which is a reason to
    stop managing an open position."""
    import inspect

    from sportsassets.workers import ext_pinnacle_loop as L

    src = inspect.getsource(L.cycle)
    i_svc = src.index("_funded_service(conn")
    for gate in ("running, why = await _running",
                 "await _table_ready(conn)",
                 "cred = ext.credential_present()"):
        assert i_svc < src.index(gate), gate
    # AND ITS RESULT RIDES OUT ON EVERY EARLY RETURN, so a stopped cycle
    # still reports what servicing did.
    assert src.count("\"funded_servicing\": funded_service") >= 3
    # IT IS CALLED EXACTLY ONCE.
    assert src.count("await _funded_service(conn") == 1


@pg
@pytest.mark.asyncio
async def test_the_stopped_cycle_still_services_the_funded_book(monkeypatch):
    """THE SAME PROPERTY, EXERCISED. The entry loop is stopped and the odds
    credential is absent; the funded book is still reconciled and measured."""
    asyncpg = pytest.importorskip("asyncpg")
    from sportsassets.workers import ext_pinnacle_loop as L

    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        await _entry(conn)
        await conn.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1,$2::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value=$2::jsonb",
            FA.ACCOUNT_KEY,
            json.dumps({"account_id": ACCT, "venue": VENUE,
                        "approved": True}))
        _transport(monkeypatch, bids=[_level(0.58, 40)])
        monkeypatch.delenv("EDGE_ODDS_API_KEY", raising=False)
        # the observation stop: OFF
        monkeypatch.setattr(L, "_running",
                            lambda c: _stopped(), raising=True)
        got = await L.cycle(conn)
        assert got["ran"] is False
        assert got["state"] == "STOPPED"
        svc = got["funded_servicing"]
        assert svc is not None, "servicing did not run"
        assert svc["ok"] is True
        assert svc["exposure"]["contracts_held"] == pytest.approx(10.0)
        assert svc["opened_anything"] is False
        assert "stop managing" in got["servicing_ran_anyway"]
    finally:
        await _clean(conn)
        await conn.close()


async def _stopped():
    return False, "the observation stop is engaged"


# ════════════════════════════════════════════════════════════════════
# 2 · AN EXIT RESERVES THE INVENTORY IT WILL SELL
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_two_simultaneous_full_exits_cannot_both_sell(monkeypatch):
    """TWO CONCURRENT FULL EXITS. Both read 10 held; only one may reserve it.

    The reservation is the INSERT, taken under `FOR UPDATE` on the parent, so
    the second caller reads an availability the first has already committed
    against. Without it both inserted an EXIT for 10 and the venue sold 20.
    """
    asyncpg = pytest.importorskip("asyncpg")
    import asyncio

    a = await asyncpg.connect(DSN)
    b = await asyncpg.connect(DSN)
    try:
        await _clean(a)
        await _seed(a)
        await _entry(a)
        monkeypatch.setattr(FM, "FUNDED_EXIT_SUBMISSION_ENABLED", True)
        monkeypatch.setattr(EX, "REAL_ORDER_SUBMISSION_ENABLED", True)
        pmus, sent, _ = _transport(monkeypatch, order_id="vo-exit",
                                   exec_by_call=[[], []])
        r1, r2 = await asyncio.gather(
            FM.submit_exit(a, intent_id="fpi-a", limit_price=0.70,
                           inputs_expire_at=time.time() + 30.0,
                           adapter=pmus, venue=VENUE),
            FM.submit_exit(b, intent_id="fpi-a", limit_price=0.70,
                           inputs_expire_at=time.time() + 30.0,
                           adapter=pmus, venue=VENUE),
            return_exceptions=True)
        got = [r for r in (r1, r2) if isinstance(r, dict)]
        assert len(got) == 2, (r1, r2)
        reserved = [g for g in got if (g.get("reservation") or {}).get("ok")]
        refused = [g for g in got if not (g.get("reservation")
                                          or {}).get("ok")]
        assert len(reserved) == 1, [g.get("reservation") for g in got]
        assert len(refused) == 1
        assert refused[0]["refusal"] == FM.R_OVER_RESIDUAL
        # EITHER OF THE TWO SIZING CHECKS MAY FIRE, AND BOTH ARE SAFE.
        #
        # This asserted the LOCKED check's wording specifically, which made the
        # test depend on which check won a race: when the first caller's insert
        # has already committed, the second is refused by the unlocked hint and
        # never reaches the lock; when it has not, the locked check refuses it.
        # Adding a read to the top of `submit_exit` was enough to flip which one
        # got there first, and the test failed while the PROPERTY it exists for
        # -- exactly one reservation, exactly one order, no oversell -- still
        # held on both sides. The property is asserted; the refusal site is
        # named but not pinned.
        assert ("Refused INSIDE the lock" in refused[0]["why"]
                or "already reserved by an exit" in refused[0]["why"]), \
            refused[0]["why"]
        # AND THE LOCKED CHECK IS NOT LEFT UNTESTED BY THAT: the retry-after-an-
        # ambiguous-answer test below reaches it deterministically, because
        # there the reserving exit is already UNRESOLVED when the second call
        # starts.
        # EXACTLY ONE EXIT EXISTS, FOR EXACTLY THE INVENTORY HELD.
        rows = await a.fetch(
            "SELECT quantity FROM bettor_funded_intents WHERE kind='EXIT'")
        assert [int(r["quantity"]) for r in rows] == [10]
        # ... and exactly one order was sent.
        assert len([k for k, _ in sent if k == "create"]) == 1
    finally:
        await _clean(a)
        await a.close()
        await b.close()


@pg
@pytest.mark.asyncio
async def test_a_retry_after_an_ambiguous_answer_cannot_oversell(monkeypatch):
    """THE RETRY CASE, WHICH IS WORSE THAN THE CONCURRENT ONE.

    The first exit's answer was lost, so it is UNRESOLVED -- and it consumed no
    fills, so it does NOT appear in the residual. The residual still reads 10.
    A naive retry therefore sold another 10 against contracts already committed
    to an order that may well have executed.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        await _entry(conn)
        monkeypatch.setattr(FM, "FUNDED_EXIT_SUBMISSION_ENABLED", True)
        monkeypatch.setattr(EX, "REAL_ORDER_SUBMISSION_ENABLED", True)
        pmus, sent, _ = _transport(
            monkeypatch, raise_on_create=TimeoutError("read timed out"))
        first = await FM.submit_exit(conn, intent_id="fpi-a",
                                    limit_price=0.70, adapter=pmus,
                                    inputs_expire_at=time.time() + 30.0,
                                    venue=VENUE)
        assert first["refusal"] == FM.R_LOST_ACKNOWLEDGEMENT, first
        xid = first["exit_intent_id"]
        assert await conn.fetchval(
            "SELECT state FROM bettor_funded_intents WHERE intent_id=$1",
            xid) == "UNRESOLVED"
        # THE RESIDUAL IS UNCHANGED -- that is exactly the trap.
        assert float(await conn.fetchval(
            "SELECT residual_qty FROM bettor_funded_intents "
            " WHERE intent_id='fpi-a'")) == pytest.approx(10.0)
        # AND THE AVAILABILITY IS NOT.
        avail = float(await conn.fetchval(
            "SELECT bettor_funded_available_to_exit('fpi-a')::float8"))
        assert avail == pytest.approx(0.0)
        pmus2, sent2, _ = _transport(monkeypatch, order_id="vo-retry")
        retry = await FM.submit_exit(conn, intent_id="fpi-a",
                                    limit_price=0.70, adapter=pmus2,
                                    inputs_expire_at=time.time() + 30.0,
                                    venue=VENUE)
        assert retry["ok"] is False
        assert retry["refusal"] == FM.R_OVER_RESIDUAL, retry
        assert [k for k, _ in sent2 if k == "create"] == []
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_intents "
            " WHERE kind='EXIT'") == 1
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_an_oversell_becomes_a_discrepancy_not_a_tidy_zero():
    """`max(0, ...)` MUST NOT HIDE AN OVER-EXIT. A negative residual clamped to
    zero is the one number that reads as flat and finished. The clamp still
    protects the column; the discrepancy is what stops it being the story."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        await _entry(conn, qty=10)
        # Force the state the reservation exists to prevent, by writing an
        # exit fill larger than the entry directly.
        await conn.execute(
            "INSERT INTO bettor_funded_intents (intent_id, account_id, venue,"
            " venue_class, us_market_slug, event_key, order_intent,"
            " limit_price, quantity, collateral_usd, effective_digest, state,"
            " provenance, kind, parent_intent_id) VALUES "
            "($1,$2,$3,$4,$5,$6,$7,0.70,14,0,'d','FILLED',$8,'EXIT','fpi-a')",
            "fpi-over", ACCT, VENUE, FA.VENUE_FUNDED, SLUG, EVENT, FX.LONG,
            FB.PROVENANCE)
        await FB.record_acknowledgement(conn, "fpi-over",
                                       venue_order_id="vo-over",
                                       status="open")
        await FB.ingest_fills(conn, "fpi-over", [
            {"qty": 14.0, "price": 0.70, "venue_fill_id": "vx-over"}],
            direction="EXIT")
        stored = float(await conn.fetchval(
            "SELECT residual_qty FROM bettor_funded_intents "
            " WHERE intent_id='fpi-a'"))
        assert stored == pytest.approx(0.0)      # the column's CHECK holds
        d = await conn.fetchrow(
            "SELECT kind, detail FROM bettor_funded_discrepancies "
            " WHERE kind=$1", FB.D_OVERSOLD)
        assert d is not None, "the oversell was silently clamped"
        detail = json.loads(d["detail"])
        assert detail["unclamped_residual"] == pytest.approx(-4.0)
        assert detail["stored_residual"] == pytest.approx(0.0)
        assert "not mistaken for flat" in detail["what_it_means"]
        # AND IT REACHES THE COMMAND CENTRE.
        cc = await FB.command_center(conn)
        kinds = {x["kind"] for x in cc["unresolved_discrepancies"]}
        assert FB.D_OVERSOLD in kinds
    finally:
        await _clean(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# 3 · RECOVERY DOES NOT INFER OWNERSHIP
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_one_unrelated_order_with_identical_terms_is_not_adopted(
        monkeypatch):
    """THE COUNTEREXAMPLE THE FOUR-TERM RULE FAILED: exactly ONE order, agreeing
    on slug, side, limit and clip, with NO readable timestamp. Nothing
    distinguishes it from ours and nothing can, so it is not adopted."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        # ONE resting order, our exact terms, no createTime at all.
        pmus, sent, _ = _transport(monkeypatch, resting=[{
            "id": "somebody-elses-order", "marketSlug": SLUG,
            "intent": FX.LONG, "price": {"value": "0.62"}, "quantity": 10,
            "cumQuantity": 0, "leavesQuantity": 10,
            "state": "ORDER_STATE_NEW"}])
        await FB.record_intent(
            conn, intent_id="fpi-lost", account_id=ACCT, venue=VENUE,
            venue_class=FA.VENUE_FUNDED, us_market_slug=SLUG, event_key=EVENT,
            order_intent=FX.LONG, limit_price=0.62, quantity=10,
            collateral_usd=6.2, effective_digest="d", payout_event=PAYS_ON)
        await FB.mark_send_attempted(conn, "fpi-lost")

        got = await FB.recover(conn, pmus, account_id=ACCT, venue=VENUE)
        assert got["reconciled"] == [], got
        u = got["unresolved"][0]
        corr = u["correlation"]
        assert corr["adopt"] is None
        assert corr["refusal"] == FB.R_NO_DURABLE_IDENTITY
        assert corr["term_match_only"] == "somebody-elses-order"
        assert corr["created_after_our_send"] is None
        assert u["exposure"] == "PRESERVED"
        assert await conn.fetchval(
            "SELECT venue_order_id FROM bettor_funded_intents "
            " WHERE intent_id='fpi-lost'") is None
        # THE LEAD IS RECORDED FOR A HUMAN, as a discrepancy.
        d = await conn.fetchrow(
            "SELECT kind, detail FROM bettor_funded_discrepancies "
            " WHERE kind=$1", FB.D_TERM_MATCH_NOT_OWNERSHIP)
        assert d is not None
        assert json.loads(d["detail"])["venue_order_id"] == \
            "somebody-elses-order"
        # NOTHING WAS SENT, AND NO ORDER WAS CANCELLED EITHER.
        assert [k for k, _ in sent if k in ("create", "cancel")] == []
    finally:
        await _clean(conn)
        await conn.close()


def test_the_venue_supports_no_client_order_identity():
    """THE FACT THE WHOLE RULE RESTS ON, asserted against the INSTALLED SDK
    rather than described. If this ever becomes false, adoption becomes
    provable and `CLIENT_ORDER_IDENTITY_SUPPORTED` is the one line to flip."""
    import inspect

    import polymarket_us.types.orders as O

    params = inspect.getsource(O.CreateOrderParams)
    for name in ("clientOrderId", "clOrdId", "clOrdID", "clientId",
                 "externalId", "idempotencyKey", "clientRef"):
        assert name not in params, name
    assert FB.CLIENT_ORDER_IDENTITY_SUPPORTED is False
    assert FB.describe()["recovery_requires"]["a_durable_identity"] is False


# ════════════════════════════════════════════════════════════════════
# 4 · ACCOUNTING SURVIVES INTERRUPTION AND LATER EVIDENCE
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
@pytest.mark.parametrize("stop_after", ["FILL", "CASH_EVENT"])
async def test_an_interruption_between_the_fill_and_its_events_is_repaired(
        stop_after):
    """INTERRUPTION AT EACH BOUNDARY. The fill is present and its economic
    events are not; the redelivery must finish the write rather than conclude
    "already held" and skip it forever."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        await _entry(conn)
        fid = await conn.fetchval("SELECT fill_id FROM bettor_funded_fills")
        # SIMULATE THE INTERRUPTION by removing what the interrupted write
        # would not have reached, and clearing the flag that says it did.
        if stop_after == "FILL":
            await conn.execute("DELETE FROM bettor_funded_economics")
        else:
            await conn.execute(
                "DELETE FROM bettor_funded_economics WHERE event_id=$1",
                "fev:%s:FEE" % fid)
        await conn.execute(
            "UPDATE bettor_funded_fills SET economics_written=FALSE")
        before = await FB.pnl(conn, account_id=ACCT, venue=VENUE)
        missing_before = 2 - int(await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_economics"))
        assert missing_before >= 1
        if stop_after == "FILL":
            # NEITHER EVENT LANDED: the cost of contracts we hold is in no
            # ledger at all.
            assert before["open_position_net_cash_usd"] == pytest.approx(0.0)

        # ── THE REDELIVERY ─────────────────────────────────────────
        again = await FB.ingest_fills(conn, "fpi-a", [
            {"qty": 10.0, "price": 0.62, "venue_fill_id": "vf-1"}])
        assert again["written"] == []
        assert again["already_held"][0]["repaired_economics"] is True
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_economics") == 2
        assert await conn.fetchval(
            "SELECT economics_written FROM bettor_funded_fills") is True
        # AND THE GAP IS RECORDED, because an understated realised total is
        # not a thing to fix silently.
        d = await conn.fetchrow(
            "SELECT detail FROM bettor_funded_discrepancies WHERE kind=$1",
            FB.D_ECONOMICS_MISSING)
        assert d is not None
        after = await FB.pnl(conn, account_id=ACCT, venue=VENUE)
        assert after["open_position_net_cash_usd"] < before[
            "open_position_net_cash_usd"] - 1e-9
        # THE COST AND THE FEE ARE BOTH THERE NOW.
        assert after["open_position_net_cash_usd"] < -6.2
        # STILL EXACTLY ONE FILL: the repair writes events, never rows.
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_fills") == 1
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_sweep_repairs_a_book_written_by_the_old_path():
    """REPLAY-REPAIRABLE WITHOUT A REDELIVERY. Every fill that predates the
    atomic write starts with `economics_written` false; the sweep settles each
    one and is a read for a book that is already consistent."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        await _entry(conn)
        await conn.execute("DELETE FROM bettor_funded_economics")
        await conn.execute(
            "UPDATE bettor_funded_fills SET economics_written=FALSE")
        got = await FB.repair_missing_economics(conn, account_id=ACCT,
                                               venue=VENUE)
        assert got["examined"] == 1
        assert len(got["repaired"]) == 1
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_economics") == 2
        # RUNNING IT AGAIN IS A READ.
        twice = await FB.repair_missing_economics(conn, account_id=ACCT,
                                                  venue=VENUE)
        assert twice["examined"] == 0
        assert twice["is_a_read_when_the_book_is_consistent"] is True
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_provisional_fee_is_replaced_by_the_venues_later_charge():
    """THE FEE THAT ARRIVES SECOND. The first delivery carried no commission,
    so the booked fee was the schedule's estimate and the cash was not final.
    A later delivery carrying the venue's actual charge must move the cash by
    exactly the difference -- idempotently, and without rewriting the original
    event behind a reader's back."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        await _entry(conn)                      # no commission stated
        fill = await conn.fetchrow("SELECT * FROM bettor_funded_fills")
        assert fill["fee_state"] == FB.FEE_PROVISIONAL
        expected = float(fill["expected_fee_usd"])
        assert float(fill["fee_usd"]) == pytest.approx(expected)
        p0 = await FB.pnl(conn, account_id=ACCT, venue=VENUE)
        assert p0["fills_with_provisional_fees"] == 1

        charged = round(expected + 0.31, 4)
        again = await FB.ingest_fills(conn, "fpi-a", [
            {"qty": 10.0, "price": 0.62, "venue_fill_id": "vf-1",
             "commission_usd": charged}])
        rep = again["already_held"][0]
        assert rep["fee_reconciled_late"] is True
        assert rep["was_provisional"] is True
        assert rep["adjustment_usd"] == pytest.approx(-0.31, abs=1e-6)

        after = await conn.fetchrow("SELECT * FROM bettor_funded_fills")
        assert after["fee_state"] == FB.FEE_DISAGREES
        assert float(after["observed_fee_usd"]) == pytest.approx(charged)
        assert float(after["fee_usd"]) == pytest.approx(charged)
        # THE ADJUSTMENT IS ITS OWN EVENT, and the original is no longer
        # provisional.
        adj = await conn.fetchrow(
            "SELECT amount_usd::float8 AS amt, provisional "
            "  FROM bettor_funded_economics WHERE kind='FEE_ADJUSTMENT'")
        assert adj is not None
        assert float(adj["amt"]) == pytest.approx(-0.31, abs=1e-6)
        orig = await conn.fetchrow(
            "SELECT provisional FROM bettor_funded_economics "
            " WHERE kind='FEE'")
        assert orig["provisional"] is False
        p1 = await FB.pnl(conn, account_id=ACCT, venue=VENUE)
        assert p1["fills_with_provisional_fees"] == 0
        assert p1["fees_usd"] == pytest.approx(charged)
        assert p1["fee_variance_usd"] == pytest.approx(0.31, abs=1e-6)

        # A THIRD DELIVERY OF THE SAME CHARGE MOVES NOTHING.
        third = await FB.ingest_fills(conn, "fpi-a", [
            {"qty": 10.0, "price": 0.62, "venue_fill_id": "vf-1",
             "commission_usd": charged}])
        assert third["already_held"][0]["fee_reconciled_late"] is False
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_economics "
            " WHERE kind='FEE_ADJUSTMENT'") == 1
        p2 = await FB.pnl(conn, account_id=ACCT, venue=VENUE)
        assert p2["fees_usd"] == pytest.approx(p1["fees_usd"])
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_void_after_a_partial_exit_refunds_only_what_is_still_held(
        monkeypatch):
    """PARTIAL EXIT, THEN VOID. The refund is the basis of the CONTRACTS STILL
    HELD, not the original acquisition cost of a clip already half sold.

    The old query read the parent's own fills and none of the exit children's,
    so it refunded the whole entry cost on top of the exit proceeds already
    booked -- and a void came out PROFITABLE.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        await _entry(conn, qty=10, price=0.60)        # cost 6.00
        monkeypatch.setattr(FM, "FUNDED_EXIT_SUBMISSION_ENABLED", True)
        monkeypatch.setattr(EX, "REAL_ORDER_SUBMISSION_ENABLED", True)
        pmus, sent, _ = _transport(monkeypatch, order_id="vo-x", execs=[
            {"id": "vx-1", "type": "EXECUTION_TYPE_FILL",
             "lastPx": {"value": "0.50"}, "lastShares": 4,
             "order": {"state": "ORDER_STATE_FILLED"}}])
        part = await FM.submit_exit(conn, intent_id="fpi-a", limit_price=0.50,
                                   quantity=4, adapter=pmus, venue=VENUE,
                                   inputs_expire_at=time.time() + 30.0)
        assert part["submitted"] is True, part
        assert part["position_after"]["residual_qty"] == pytest.approx(6.0)

        rb = await FB.remaining_basis(conn, "fpi-a")
        assert rb["entry_cash_usd"] == pytest.approx(6.0)
        assert rb["basis_per_contract"] == pytest.approx(0.60)
        assert rb["remaining_basis_usd"] == pytest.approx(3.6)

        got = await FM.reconcile_settlement(
            conn, intent_id="fpi-a",
            probe=lambda c, s: {"terminal_reading": "EXPLICIT_VOID",
                                "authoritative_payout_present": True,
                                "why": "the venue declared a void"})
        assert got["closed"] is True
        # 6 contracts at 0.60, NOT the original 6.00.
        assert got["settlement_usd"] == pytest.approx(3.6)
        assert got["remaining_basis"]["residual_qty"] == pytest.approx(6.0)

        pnl = await FB.pnl(conn, account_id=ACCT, venue=VENUE)
        # entry -6.00, exit +2.00, void +3.60, minus fees -> a small LOSS.
        assert pnl["realised_pnl_usd"] < 0.0, pnl["realised_pnl_usd"]
        assert pnl["realised_pnl_usd"] > -1.0
        settle = await conn.fetchval(
            "SELECT amount_usd::float8 FROM bettor_funded_economics "
            " WHERE kind='SETTLEMENT'")
        assert float(settle) == pytest.approx(3.6)
    finally:
        await _clean(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# 5 · MANAGEMENT SELECTS AND EXECUTES
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_every_missing_exit_input_is_named_one_at_a_time(monkeypatch):
    """"NEEDS A DECISION" WAS ONE ANSWER TO FIVE PROBLEMS. Each now has its own
    refusal, so the next engineering step is identified rather than guessed."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        # (a) NO PAYOUT EVENT: the position cannot be valued at all.
        await _entry(conn, intent_id="fpi-nopay", payout_event=None)
        pos = (await FM.open_positions(conn, account_id=ACCT,
                                       venue=VENUE))[0]
        pmus, sent, client = _transport(monkeypatch, bids=[_level(0.70, 50)])
        got = await FM.select_exit(conn, pos, client=client,
                                    subscription=_live())
        assert got["refusal"] == FM.R_NO_PAYOUT_EVENT
        assert got["missing_input"] == "bettor_funded_intents.payout_event"
        await FB.mark_position_closed(conn, "fpi-nopay",
                                     "EXITED_IN_THE_MARKET")

        # (b) AN UNREADABLE BOOK is not a reason to sell.
        await _entry(conn, intent_id="fpi-nobook", vo="vo-2", fill_id="vf-2")
        pos = [p for p in await FM.open_positions(conn, account_id=ACCT,
                                                  venue=VENUE)
               if p["intent_id"] == "fpi-nobook"][0]
        _, _, broken = _transport(monkeypatch, raise_on_book=True)
        got = await FM.select_exit(conn, pos, client=broken,
                                   subscription=_live())
        assert got["refusal"] == FM.R_BOOK_UNREADABLE

        # (c) A BOOK WITH NO EXIT SIDE.
        _, _, empty = _transport(monkeypatch, bids=[])
        got = await FM.select_exit(conn, pos, client=empty,
                                   subscription=_live())
        assert got["refusal"] == FM.R_NO_EXIT_SIDE

        # (d) NO ELIGIBLE PROBABILITY ROW.
        _, _, ok_book = _transport(monkeypatch, bids=[_level(0.70, 50)])
        got = await FM.select_exit(conn, pos, client=ok_book,
                                   subscription=_live())
        assert got["refusal"] == FM.R_NO_PROBABILITY

        # (e) A HELD ROW IS NEVER USED FOR A DECISION, and that is reported as
        #     a containment action rather than a missing feed.
        await _probability(conn, p=0.55, eligibility="HELD")
        got = await FM.select_exit(conn, pos, client=ok_book,
                                   subscription=_live())
        assert got["refusal"] == FM.R_NO_PROBABILITY
        assert got["probability_read"]["eligibility"] == "HELD"

        # (f) A STALE ELIGIBLE ROW: EV_HOLD refuses on its OWN freshness
        #     bound, which this lane does not widen.
        await conn.execute("DELETE FROM external_valuations WHERE venue=$1",
                           "PMUS_TEST_COMPLETE")
        await _probability(conn, p=0.55, at=time.time() - 86400)
        got = await FM.select_exit(conn, pos, client=ok_book,
                                   subscription=_live())
        assert got["refusal"] == FM.R_HOLD_NOT_PRICED
        assert "ENGINEERING INPUT" in got["what_is_missing"]
        assert got["ev_hold"]["status"] == "NOT_IDENTIFIED"
        assert [k for k, _ in sent if k in ("create", "cancel")] == []
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_selector_holds_when_the_book_does_not_beat_holding(
        monkeypatch):
    """A DECISION, NOT A GAP. The book pays 0.50 and holding is worth 0.80, so
    HOLD is SELECTED by a named rule -- and nothing is sold."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        await _entry(conn, price=0.60)
        await _probability(conn, p=0.80)
        pos = (await FM.open_positions(conn, account_id=ACCT,
                                       venue=VENUE))[0]
        _, sent, client = _transport(monkeypatch, bids=[_level(0.50, 50)])
        got = await FM.select_exit(conn, pos, client=client,
                                    subscription=_live())
        assert got["ok"] is True
        assert got["is_an_evidenced_exit"] is False
        assert got["selected"] == "HOLD"
        assert got["ev_hold"]["status"] == "IDENTIFIED"
        assert got["ev_hold"]["probability"] == pytest.approx(0.80)
        assert "HOLD" in (got["why"] or "")
        assert [k for k, _ in sent if k == "create"] == []
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_scheduled_path_selects_executes_recovers_and_reports(
        monkeypatch):
    """THE WHOLE THING, THROUGH THE SCHEDULED PATH -- not by calling helpers.

    `ext_pinnacle_loop.cycle` -> `_funded_service` -> `manage` -> `select_exit`
    -> `submit_exit` -> `pmus.submit_fok` with the transport substituted. Then a
    restart, recovery of the exit's own fill, and the reconciled funded P&L in
    the command centre.
    """
    asyncpg = pytest.importorskip("asyncpg")
    from sportsassets.workers import ext_pinnacle_loop as L

    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        await _entry(conn, qty=10, price=0.60)
        # The book pays 0.75; holding is worth 0.55. Selling wins.
        await _probability(conn, p=0.55)
        await conn.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1,$2::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value=$2::jsonb",
            FA.ACCOUNT_KEY,
            json.dumps({"account_id": ACCT, "venue": VENUE,
                        "approved": True}))

        monkeypatch.setattr(FM, "FUNDED_EXIT_SUBMISSION_ENABLED", True)
        monkeypatch.setattr(EX, "REAL_ORDER_SUBMISSION_ENABLED", True)
        # THE EXIT'S ANSWER CARRIES NO EXECUTIONS -- the venue acknowledged and
        # said nothing more. The fill arrives on the recovery read, which is
        # the restart case.
        pmus, sent, _ = _transport(
            monkeypatch, order_id="vo-exit", exec_by_call=[[]],
            bids=[_level(0.75, 40)],
            retrieve={"vo-exit-1": {
                "order": {"id": "vo-exit-1", "marketSlug": SLUG,
                          "intent": "ORDER_INTENT_SELL_LONG",
                          "price": {"value": "0.75"}, "quantity": 10,
                          "cumQuantity": 10, "leavesQuantity": 0,
                          "state": "ORDER_STATE_FILLED"},
                "executions": [{"id": "vx-late",
                                "type": "EXECUTION_TYPE_FILL",
                                "lastPx": {"value": "0.75"},
                                "lastShares": 10,
                                "commissionNotionalTotalCollected": {
                                    "value": "0.1100"}}]}})
        # THE HELD CONTRACT'S VENUE TERMS (SYNTHETIC; tests/held_contract_terms):
        # since 880377f the exit is sent only when the one-measure valuation
        # selects it robustly, which needs the contract's own void payout.
        from tests import held_contract_terms as HCT
        HCT.substitute_held_leg_read(monkeypatch, slug=SLUG, event=EVENT)
        monkeypatch.delenv("EDGE_ODDS_API_KEY", raising=False)
        monkeypatch.setattr(L, "_running", lambda c: _stopped())
        # THE SCHEDULER'S FRESHNESS SEAM, SUPPLIED FOR THIS TEST.
        # `book_currency_evidence` returns no mechanism in production, so the
        # scheduled path refuses on an unestablished book currency -- correctly,
        # and that is the named blocker the operating view reports. This test's
        # subject is the lifecycle DOWNSTREAM of admission, so it states which
        # published contract admitted the book rather than pretending none is
        # needed. A separate test pins the production default refusing.
        monkeypatch.setattr(
            L, "book_currency_evidence",
            lambda slug=None: {"subscription": _live(), "revalidation": None})

        # ── CYCLE 1: SELECT AND EXECUTE, through the scheduler ──────
        one = await L.cycle(conn)
        svc = one["funded_servicing"]
        assert svc is not None and svc["ok"] is True
        assert svc["selection"] and svc["selection"][0]["ok"] is True
        pick = svc["selection"][0]
        assert pick["is_an_evidenced_exit"] is True
        assert pick["selected"] in ("DIRECT_EXIT", "REDUCE")
        assert pick["limit_price"] == pytest.approx(0.75)
        assert pick["price_source"].startswith("the best level")
        # ── THE SEND MOVED, AND THE DISPATCH IS STILL MADE ─────────
        #
        # `manage` no longer submits: it selects and DEFERS, so the exit joins
        # the one ranking that also holds the indirect hedge and is dispatched
        # from there. That is the ordering repair, and it is why `svc["exits"]`
        # is empty while the order still goes out.
        #
        # THE SUBJECT OF THIS TEST IS UNCHANGED -- select, execute, recover,
        # report -- so the assertion follows the dispatch to its new home
        # rather than being dropped.
        assert svc["exits"] == [], (
            "manage submitted from step 4; the ranking never got to compare "
            "the exit against the hedge")
        assert svc["defer_dispatch"] is True
        assert svc["deferred_exits"] and (
            svc["deferred_exits"][0]["selected"] in ("DIRECT_EXIT", "REDUCE"))
        pcx = (svc.get("pair_cycle") or {}).get("exits") or []
        import json as _j
        assert pcx and pcx[0]["submitted"] is True, _j.dumps(
            svc.get("pair_cycle"), default=str, indent=1)[:2500]
        assert pcx[0]["action"] in PC.LEDGER_EXIT_ACTIONS, pcx[0]
        assert pcx[0]["selection_action"] in ("DIRECT_EXIT", "REDUCE")
        # AND THE ORDER WAS BOUND TO THE CANDIDATE THE RANKING CHOSE,
        # not merely fetched by position id.
        assert pcx[0]["order_binding"]["ok"] is True, pcx[0]
        # AND IT WAS DISPATCHED AT THE SELECTION'S OWN NUMBERS, not at
        # anything the dispatcher recomputed.
        assert pcx[0]["limit_price"] == pick["limit_price"]
        assert pcx[0]["quantity"] == pick["selected_qty"]
        # THE ADAPTER WAS ASKED TO SELL, with the side we hold named.
        create = [p for k, p in sent if k == "create"][-1]
        assert create["intent"] == "ORDER_INTENT_SELL_LONG"
        assert create["quantity"] == pick["selected_qty"]
        # ... and nothing has filled yet, so the inventory is still reserved.
        assert float(await conn.fetchval(
            "SELECT bettor_funded_available_to_exit('fpi-a')::float8")) == \
            pytest.approx(0.0)

        # ── RESTART, THEN CYCLE 2: RECOVER THE EXIT'S FILL ──────────
        await conn.close()
        conn = await asyncpg.connect(DSN)
        two = await L.cycle(conn)
        svc2 = two["funded_servicing"]
        assert svc2["recovered"]["ok"] is True
        rec = svc2["recovered"]["reconciled"][0]
        assert rec["executions_read"] == 1
        assert rec["fills_written"] == 1
        pos_after = await conn.fetchrow(
            "SELECT residual_qty::float8 AS r, closed_reason "
            "  FROM bettor_funded_intents WHERE intent_id='fpi-a'")
        assert float(pos_after["r"]) == pytest.approx(0.0)
        assert pos_after["closed_reason"] == "EXITED_IN_THE_MARKET"

        # ── THE RECONCILED FUNDED P&L, IN THE COMMAND CENTRE ───────
        cc = await FB.command_center(conn)
        book = cc["books"][0]
        assert book["exposure"]["contracts_held"] == pytest.approx(0.0)
        # bought 10 @ 0.60, sold 10 @ 0.75 -> +1.50 before fees.
        assert book["pnl"]["realised_pnl_usd"] > 1.0
        assert book["pnl"]["closed_positions"] == 1
        assert book["pnl"]["exit_proceeds_usd"] == pytest.approx(7.5)
        assert book["pnl"]["cost_basis_usd"] == pytest.approx(6.0)
        assert book["pnl"]["max_drawdown_usd"] == pytest.approx(0.0)
        # THE VENUE'S OWN COMMISSION ON THE EXIT was read from the recovery.
        assert book["pnl"]["fees_usd"] > 0
        assert cc["section"] == "Funded book"
    finally:
        await _clean(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# 6 · PROCEEDS AND WIRE PRICE ARE DIFFERENT NUMBERS
# ════════════════════════════════════════════════════════════════════

def test_the_ladder_keeps_proceeds_and_wire_apart_on_both_sides():
    """THE ARITHMETIC, PURE. A long-side ask of 0.20 is 0.80 of proceeds to a
    short holder, and 0.80 sent as a short wire limit accepts 0.20. The two
    spaces are complements on a short and coincide on a long."""
    from sportsassets import bettor_book_snapshot as BS

    md = {"bids": [_level(0.18, 30)], "offers": [_level(0.20, 40)]}
    short = BS.exit_ladder(md, held_intent="ORDER_INTENT_BUY_SHORT")
    assert short["best_exit_price"] == pytest.approx(0.80)   # cash received
    assert short["best_api_price"] == pytest.approx(0.20)    # the wire
    # THE INVERSION, SHOWN: proceeds passed as a wire halve-and-flip.
    bad = FM.safe_exit_cent(short["best_exit_price"], "ORDER_INTENT_BUY_SHORT")
    assert FM.exit_proceeds(1, bad, "ORDER_INTENT_BUY_SHORT") == \
        pytest.approx(0.20)
    # THE WIRE, CORRECTLY: api_price recovers the proceeds.
    good = FM.safe_exit_cent(short["best_api_price"], "ORDER_INTENT_BUY_SHORT")
    assert FM.exit_proceeds(1, good, "ORDER_INTENT_BUY_SHORT") == \
        pytest.approx(0.80)

    long_ = BS.exit_ladder(md, held_intent=FX.LONG)
    # ON A LONG THEY COINCIDE, which is why the bug hid.
    assert long_["best_exit_price"] == pytest.approx(0.18)
    assert long_["best_api_price"] == pytest.approx(0.18)
    w = FM.safe_exit_cent(long_["best_api_price"], FX.LONG)
    assert FM.exit_proceeds(1, w, FX.LONG) >= 0.18 - 1e-9


@pg
@pytest.mark.asyncio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
async def test_the_submitted_limit_cannot_accept_less_than_the_selection(
        monkeypatch, side):
    """THE SCHEDULED SELECTOR THROUGH THE REAL ADAPTER, BOTH SIDES.

    `select_exit` chooses on PROCEEDS and submits a WIRE; the order that
    reaches `pmus.submit_fok` must not permit less cash per contract than the
    level the action was selected on.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        if side == "LONG":
            # Held long at 0.60; the bid pays 0.75.
            opened, entry_px = FX.LONG, 0.60
            bids, offers = [_level(0.75, 40)], [_level(0.77, 40)]
            p = 0.55
        else:
            # Held short at 0.60 of collateral (wire 0.40); the long-side ask
            # is 0.20, so closing the short receives 0.80.
            opened, entry_px = "ORDER_INTENT_BUY_SHORT", 0.40
            bids, offers = [_level(0.18, 40)], [_level(0.20, 40)]
            p = 0.05          # the payout event is unlikely -> holding is poor
        await _entry(conn, qty=10, price=entry_px, intent=opened)
        await _probability(conn, p=p)
        pos = (await FM.open_positions(conn, account_id=ACCT,
                                       venue=VENUE))[0]
        monkeypatch.setattr(FM, "FUNDED_EXIT_SUBMISSION_ENABLED", True)
        monkeypatch.setattr(EX, "REAL_ORDER_SUBMISSION_ENABLED", True)
        pmus, sent, client = _transport(monkeypatch, order_id="vo-x",
                                       bids=bids, offers=offers,
                                       exec_by_call=[[]])
        pick = await FM.select_exit(conn, pos, client=client,
                                    subscription=_live())
        assert pick["ok"] is True, pick
        assert pick["is_an_evidenced_exit"] is True, pick.get("why")
        # THE TWO NUMBERS ARE BOTH REPORTED AND THEY ARE THE RIGHT WAY ROUND.
        assert pick["price_space"] == "VENUE_WIRE_CONTRACT_PRICE"
        if side == "SHORT":
            assert pick["proceeds_per_contract"] == pytest.approx(0.80)
            assert pick["limit_price"] == pytest.approx(0.20)
            assert pick["rounding"] == "FLOOR"
        else:
            assert pick["proceeds_per_contract"] == pytest.approx(0.75)
            assert pick["limit_price"] == pytest.approx(0.75)
            assert pick["rounding"] == "CEIL"
        # ── THROUGH THE REAL ADAPTER ────────────────────────────────
        ex = await FM.submit_exit(
            conn, intent_id=pos["intent_id"], limit_price=pick["limit_price"],
            quantity=pick["selected_qty"],
            expect_proceeds_per_contract=pick["proceeds_per_contract"],
            # THE SELECTOR'S OWN DEADLINE, as the scheduled path passes it.
            inputs_expire_at=pick["inputs_expire_at"],
            adapter=pmus, venue=VENUE)
        assert ex["submitted"] is True, ex
        create = [q for k, q in sent if k == "create"][-1]
        wire_sent = float((create["price"] or {}).get("value"))
        # THE ASSERTION THAT MATTERS: what the venue was told cannot receive
        # less per contract than what the decision was made on.
        got_per = FM.exit_proceeds(1, wire_sent, opened)
        assert got_per >= pick["proceeds_per_contract"] - 1e-9, (
            "wire %s receives %s against a selection on %s"
            % (wire_sent, got_per, pick["proceeds_per_contract"]))
        assert create["intent"] == (
            "ORDER_INTENT_SELL_SHORT" if side == "SHORT"
            else "ORDER_INTENT_SELL_LONG")
        if side == "SHORT":
            assert wire_sent == pytest.approx(0.20)
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_submit_exit_refuses_a_wire_that_would_accept_less(monkeypatch):
    """THE GUARD, DIRECTLY. Hand `submit_exit` the PROCEEDS where the wire
    belongs on a short -- the exact old mistake -- and it refuses instead of
    sending an inverted bound."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        await _entry(conn, qty=10, price=0.40,
                     intent="ORDER_INTENT_BUY_SHORT")
        monkeypatch.setattr(FM, "FUNDED_EXIT_SUBMISSION_ENABLED", True)
        monkeypatch.setattr(EX, "REAL_ORDER_SUBMISSION_ENABLED", True)
        pmus, sent, _ = _transport(monkeypatch)
        got = await FM.submit_exit(
            conn, intent_id="fpi-a",
            limit_price=0.80,                      # proceeds, not the wire
            expect_proceeds_per_contract=0.80,
            inputs_expire_at=time.time() + 30.0,
            adapter=pmus, venue=VENUE)
        assert got["ok"] is False
        assert got["refusal"] == FM.R_EXIT_WIRE_UNREPRESENTABLE, got
        assert got["proceeds_per_contract"] == pytest.approx(0.20)
        assert got["selected_on_proceeds_per_contract"] == pytest.approx(0.80)
        assert [k for k, _ in sent if k == "create"] == []
    finally:
        await _clean(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# 7 · THE EVIDENCE THE COMPONENTS EXPECT, FROM THE DATABASE
# ════════════════════════════════════════════════════════════════════

def test_the_funded_book_age_policy_is_the_entry_lanes_own():
    """ONE BOUND, NOT TWO. Two lanes admitting the same venue book on two
    different ages is exactly the drift a shared declaration prevents."""
    from sportsassets.workers import ext_pinnacle_loop as L

    from sportsassets import bettor_venue_currency as VC

    assert FA.MAX_VENUE_BOOK_AGE_S == L.MAX_VENUE_QUOTE_AGE_S
    assert FA.MAX_VENUE_BOOK_AGE_S == VC.MAX_BOOK_STATE_AGE_S
    # ONE RULE TOO, NOT ONLY ONE BOUND. Both lanes reach the same verdict
    # function, so they cannot disagree about what admits a book either.
    assert FA.FRESHNESS_BASIS_ESTABLISHED == tuple(VC.ESTABLISHING_MECHANISMS)
    # AND `VENUE_TRANSACT_TIME` IS NO LONGER ON THAT LIST. This test used to
    # assert it WAS an establishing basis. Parsing the stamp establishes that
    # the value was readable; what it denotes is unresolved, and a cache
    # replaying one representation yields a readable stamp too.
    assert "VENUE_TRANSACT_TIME" not in FA.FRESHNESS_BASIS_ESTABLISHED
    assert "VENUE_TRANSACT_TIME" in FA.FRESHNESS_BASIS_UNESTABLISHED


@pg
@pytest.mark.asyncio
@pytest.mark.parametrize("book", ["STALE", "NO_CLOCK", "UNPARSEABLE"])
async def test_a_book_whose_age_is_not_established_refuses_the_exit(
        monkeypatch, book):
    """PROBABILITY FRESHNESS IS NOT BOOK FRESHNESS. The probability is current
    in all three of these; the book is not admissible in any of them."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        await _entry(conn, qty=10, price=0.60)
        await _probability(conn, p=0.55)
        pos = (await FM.open_positions(conn, account_id=ACCT,
                                       venue=VENUE))[0]
        kw = {"bids": [_level(0.75, 40)]}
        if book == "STALE":
            kw["book_age_s"] = 600.0
        elif book == "NO_CLOCK":
            kw["transact_time"] = None
        else:
            kw["transact_time"] = "not-a-timestamp"
        _, sent, client = _transport(monkeypatch, **kw)
        # NO MECHANISM IS SUPPLIED, DELIBERATELY. That is this lane's production
        # state, and it is what makes all three of these refuse. A stamp past
        # the bound WITH a live subscription is a different case, and the next
        # test covers it.
        got = await FM.select_exit(conn, pos, client=client)
        assert got["ok"] is False
        assert got["refusal"] == FM.R_BOOK_NOT_FRESH, got
        age = got["venue_book_age"]
        if book == "STALE":
            # THE CORRECTED READING. A `transactTime` ten minutes old is
            # recorded and does NOT by itself say the book is stale -- what the
            # field denotes is unresolved. The exit still refuses, under the
            # accurate name: no mechanism established that this book is
            # current. Nothing is loosened; the refusal is renamed to what it
            # actually is, and `stamp_beyond_the_bound` keeps the observation.
            assert age["basis"] == "VENUE_TRANSACT_TIME"
            assert age["refusal"] == FA.R_BOOK_CURRENCY_NOT_ESTABLISHED
            assert age["age_s"] > 30.0
            assert age["stamp_beyond_the_bound"] is True
            assert age["unmeasured"] is True
            assert "missing evidence" in age["this_is_not_a_stale_book"]
        else:
            # UNMEASURED IS NOT STALE, and the two are not collapsed.
            assert age["refusal"] == FA.R_BOOK_AGE_UNMEASURED
            assert age["basis"] in ("VENUE_CLOCK_NOT_PROVIDED",
                                    "VENUE_CLOCK_UNPARSEABLE")
        assert [k for k, _ in sent if k == "create"] == []
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_an_unestablished_settlement_rule_restricts_the_funded_exit(
        monkeypatch):
    """`IDENTIFIED` IS NOT ENOUGH. The hold-value component retains an UNKNOWN
    terminal rule as a CONDITIONAL valuation and ranks it -- right for a shadow
    decision, not for a funded one. The restriction is explicit."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        await _entry(conn, qty=10, price=0.60)
        # The venue published nothing to compare: UNKNOWN, not INCOMPATIBLE.
        await _probability(conn, p=0.55, settled=False)
        pos = (await FM.open_positions(conn, account_id=ACCT,
                                       venue=VENUE))[0]
        _, sent, client = _transport(monkeypatch, bids=[_level(0.75, 40)])
        got = await FM.select_exit(conn, pos, client=client,
                                    subscription=_live())
        assert got["ok"] is False
        assert got["refusal"] == FM.R_SETTLEMENT_NOT_ESTABLISHED, got
        # THE VALUATION ITSELF WAS IDENTIFIED -- that is the whole point.
        assert got["ev_hold"]["status"] == "IDENTIFIED"
        assert got["terminal_rule"]["compatibility"] == "UNKNOWN"
        assert got["terminal_rule"][
            "unknown_is_retained_as_conditional"] is True
        assert "not admissible here" in got["funded_restriction"]
        assert [k for k, _ in sent if k == "create"] == []

        # AND WITH THE RULE ESTABLISHED, the same position proceeds.
        await conn.execute("DELETE FROM external_valuations WHERE venue=$1",
                           "PMUS_TEST_COMPLETE")
        await _probability(conn, p=0.55, settled=True)
        ok = await FM.select_exit(conn, pos, client=client,
                                    subscription=_live())
        assert ok["ok"] is True, ok
        assert ok["terminal_rule"]["compatibility"] == "ESTABLISHED"
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_evidence_comes_from_the_database_projection(monkeypatch):
    """NOT A HAND-BUILT POSITION. `open_positions()` is the projection the
    scheduler uses, and it carries no `event_state` and no `settlement_rule`
    -- so the selector must find them itself, on the valuation row that priced
    the entry. Reading them off the position was always None."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        await _entry(conn, qty=10, price=0.60)
        await _probability(conn, p=0.55, event_state="IN_PROGRESS")
        rows = await FM.open_positions(conn, account_id=ACCT, venue=VENUE)
        pos = rows[0]
        # THE PROJECTION DOES NOT CARRY THEM, and that is correct: decision
        # evidence does not belong on an inventory row.
        assert "event_state" not in pos
        assert "settlement_rule" not in pos
        got = await FM.select_exit(
            conn, pos, client=_transport(monkeypatch,
                                        bids=[_level(0.75, 40)])[2],
            subscription=_live())
        assert got["ok"] is True, got
        ev = got["decision_evidence"]
        assert ev["read"] is True
        assert ev["event_state_raw"] == "IN_PROGRESS"
        assert ev["event_state"] == "IN_PLAY"
        assert ev["settlement_supplied"] is True
        # THE BOUND IS THE ESTABLISHED ONE FOR THAT STATE.
        assert got["ev_hold"]["event_state"] == "IN_PLAY"
        assert got["ev_hold"]["age_bound_s"] == pytest.approx(30.0)
    finally:
        await _clean(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# 8 · ONE REDELIVERY DOES BOTH REPAIRS
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_one_redelivery_repairs_the_events_and_books_the_only_fee():
    """THE `elif` DEFECT. A redelivery that both finishes an interrupted write
    AND carries the venue's actual commission must do both -- the venue states
    a commission once, so the branch that skipped it discarded the only copy.

    After this single delivery there is no further venue contact, and the fee,
    the adjustment, the provisional status and the P&L must all be final.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        await _entry(conn)                        # no commission stated
        fid = await conn.fetchval("SELECT fill_id FROM bettor_funded_fills")
        expected = float(await conn.fetchval(
            "SELECT expected_fee_usd FROM bettor_funded_fills"))
        # THE INTERRUPTION: the fill landed, the events did not.
        await conn.execute("DELETE FROM bettor_funded_economics")
        await conn.execute(
            "UPDATE bettor_funded_fills SET economics_written=FALSE")

        charged = round(expected + 0.27, 4)
        again = await FB.ingest_fills(conn, "fpi-a", [
            {"qty": 10.0, "price": 0.62, "venue_fill_id": "vf-1",
             "commission_usd": charged}])
        rep = again["already_held"][0]
        # BOTH, from ONE delivery.
        assert rep["repaired_economics"] is True
        assert rep["fee_reconciled_late"] is True, rep
        assert rep["adjustment_usd"] == pytest.approx(-0.27, abs=1e-6)

        fill = await conn.fetchrow("SELECT * FROM bettor_funded_fills")
        assert fill["fee_state"] == FB.FEE_DISAGREES
        assert float(fill["observed_fee_usd"]) == pytest.approx(charged)
        assert float(fill["fee_usd"]) == pytest.approx(charged)
        assert fill["economics_written"] is True
        # THE LEDGER: cash, the expected fee, and the adjustment to actual.
        kinds = {r["kind"]: float(r["amount_usd"]) for r in await conn.fetch(
            "SELECT kind, amount_usd FROM bettor_funded_economics")}
        assert set(kinds) == {"ENTRY_COST", "FEE", "FEE_ADJUSTMENT"}
        assert kinds["FEE"] + kinds["FEE_ADJUSTMENT"] == \
            pytest.approx(-charged)
        assert await conn.fetchval(
            "SELECT bool_and(NOT provisional) FROM bettor_funded_economics"
        ) is True

        pnl = await FB.pnl(conn, account_id=ACCT, venue=VENUE)
        assert pnl["fills_with_provisional_fees"] == 0
        assert pnl["fees_usd"] == pytest.approx(charged)
        assert pnl["fee_variance_usd"] == pytest.approx(0.27, abs=1e-6)
        assert pnl["realised_is_provisional"] is False

        # ── AND A REPLAY CHANGES NOTHING ───────────────────────────
        for _ in range(3):
            more = await FB.ingest_fills(conn, "fpi-a", [
                {"qty": 10.0, "price": 0.62, "venue_fill_id": "vf-1",
                 "commission_usd": charged}])
            assert more["already_held"][0]["fee_reconciled_late"] is False
            assert more["already_held"][0]["repaired_economics"] is False
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_economics") == 3
        after = await FB.pnl(conn, account_id=ACCT, venue=VENUE)
        assert after["fees_usd"] == pytest.approx(pnl["fees_usd"])
        assert after["cost_basis_usd"] == pytest.approx(pnl["cost_basis_usd"])
    finally:
        await _clean(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# 9 · THE INVENTORY TRANSITION IS INSIDE THE COMMIT
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_an_interruption_inside_the_fill_commit_leaves_no_open_slot():
    """THE WINDOW THIS CLOSES. The state transition to FILLED and the residual
    recompute used to be two statements AFTER the fill's transaction. A crash
    between them leaves an entry marked FILLED whose residual is still the 0 it
    was inserted with -- which reads as "not outstanding and nothing held", so
    the one-open-position slot is RELEASED while the contracts are owned.

    The interruption is injected at exactly that boundary, and a second
    connection then attempts another entry.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    other = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        await FB.record_intent(
            conn, intent_id="fpi-a", account_id=ACCT, venue=VENUE,
            venue_class=FA.VENUE_FUNDED, us_market_slug=SLUG, event_key=EVENT,
            order_intent=FX.LONG, limit_price=0.62, quantity=10,
            collateral_usd=6.2, effective_digest="d", payout_event=PAYS_ON)
        await FB.record_acknowledgement(conn, "fpi-a", venue_order_id="vo-1",
                                       status="open")
        assert float(await conn.fetchval(
            "SELECT residual_qty FROM bettor_funded_intents "
            " WHERE intent_id='fpi-a'")) == pytest.approx(0.0)

        # THE INJECTION: fail the residual recompute, which is the statement
        # that used to run after the commit.
        import sportsassets.bettor_funded_book as _fb
        real = _fb._recompute_residual

        async def _die(c, iid):
            raise RuntimeError("interrupted between FILLED and the residual")

        _fb._recompute_residual = _die
        try:
            with pytest.raises(RuntimeError):
                await FB.ingest_fills(conn, "fpi-a", [
                    {"qty": 10.0, "price": 0.62, "venue_fill_id": "vf-1"}])
        finally:
            _fb._recompute_residual = real

        # ── NOTHING PARTIAL SURVIVED ───────────────────────────────
        row = await conn.fetchrow(
            "SELECT state, residual_qty::float8 AS r, "
            "       bettor_funded_position_is_open(state, residual_qty, "
            "           closed_at) AS open "
            "  FROM bettor_funded_intents WHERE intent_id='fpi-a'")
        # The whole ingest rolled back: the order is NOT FILLED, and the
        # position is still open on its outstanding order -- so the slot is
        # held either way and no exposure was invented or lost.
        assert row["state"] == "ACKNOWLEDGED"
        assert row["open"] is True
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_fills") == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_economics") == 0

        # ── AND A SECOND ENTRY FROM ANOTHER CONNECTION IS REFUSED ──
        second = await FB.record_intent(
            other, intent_id="fpi-b", account_id=ACCT, venue=VENUE,
            venue_class=FA.VENUE_FUNDED, us_market_slug=SLUG, event_key=EVENT,
            order_intent=FX.LONG, limit_price=0.62, quantity=10,
            collateral_usd=6.2, effective_digest="d", payout_event=PAYS_ON)
        assert second["ok"] is False
        assert second["refusal"] == FB.R_ANOTHER_INTENT_IS_LIVE

        # ── THE REDELIVERY THEN COMPLETES IT, ATOMICALLY ───────────
        ok = await FB.ingest_fills(conn, "fpi-a", [
            {"qty": 10.0, "price": 0.62, "venue_fill_id": "vf-1"}])
        assert len(ok["written"]) == 1
        row = await conn.fetchrow(
            "SELECT state, residual_qty::float8 AS r, "
            "       bettor_funded_position_is_open(state, residual_qty, "
            "           closed_at) AS open "
            "  FROM bettor_funded_intents WHERE intent_id='fpi-a'")
        assert row["state"] == "FILLED"
        assert float(row["r"]) == pytest.approx(10.0)
        assert row["open"] is True          # FILLED, and the stock is held
        still = await FB.record_intent(
            other, intent_id="fpi-c", account_id=ACCT, venue=VENUE,
            venue_class=FA.VENUE_FUNDED, us_market_slug=SLUG, event_key=EVENT,
            order_intent=FX.LONG, limit_price=0.62, quantity=10,
            collateral_usd=6.2, effective_digest="d", payout_event=PAYS_ON)
        assert still["ok"] is False
        assert still["refusal"] == FB.R_ANOTHER_INTENT_IS_LIVE
    finally:
        await _clean(conn)
        await conn.close()
        await other.close()


def test_the_fill_commit_covers_the_inventory_transition():
    """STRUCTURAL. The state transition, the residual recompute and the closure
    must all sit inside the locked transaction, not after it."""
    import inspect

    src = inspect.getsource(FB.ingest_fills)
    assert "FOR UPDATE" in src
    assert "_ingest_locked" in src
    body = inspect.getsource(FB._ingest_locked)
    for stmt in ("state='FILLED'", "_recompute_residual",
                 "mark_position_closed"):
        assert stmt in body, stmt
    # and the lock is on the POSITION, which is what an exit reservation
    # locks too -- same row, same order, so they serialise.
    assert "econ_intent" in src


# ════════════════════════════════════════════════════════════════════
# 10 · A FUTURE TIMESTAMP IS NOT A FRESH ONE
# ════════════════════════════════════════════════════════════════════

def _stamp(offset_s):
    """A venue transactTime `offset_s` from now, in the shape the parser
    accepts. Positive is in the FUTURE."""
    import datetime as _dt

    t = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=offset_s)
    return t.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _md(offset_s):
    return {"transactTime": _stamp(offset_s), "bids": [_level(0.75, 40)],
            "offers": [_level(0.77, 40)]}


def test_a_timestamp_an_hour_ahead_gets_no_fresh_certificate():
    """THE DEFECT THIS CLOSES. The first version recorded a negative age, noted
    the venue's clock was ahead, and returned ok=True -- "a book cannot be too
    fresh". A negative age is not a fresher book: it is two clocks that
    disagree, and a clock we cannot reconcile cannot establish an age. An hour
    ahead would have been certified fresh."""
    now = time.time()
    got = FA.venue_book_age(_md(3600), decision_at=now + 0.1,
                            received_at=now, requested_at=now - 0.2)
    assert got["ok"] is False
    assert got["refusal"] == FA.R_BOOK_CLOCK_INCONSISTENT
    assert got["stamp_after_our_receipt_s"] > 3500
    assert "not a fresh book" in got["why"]
    # AND WITH NO RECEIPT INSTANT the weaker check still refuses rather than
    # certifying an arbitrarily future stamp.
    bare = FA.venue_book_age(_md(3600), decision_at=now)
    assert bare["ok"] is False
    assert bare["refusal"] == FA.R_BOOK_CLOCK_INCONSISTENT
    assert "no received_at was supplied" in bare["weaker_check"]


def test_slow_acquisition_ages_the_book_against_the_unchanged_bound():
    """A 20-SECOND-OLD SNAPSHOT IS 40 SECONDS OLD TWENTY SECONDS LATER, and the
    30-second bound does not move to accommodate the delay. Aging against a
    pre-request instant is what made acquisition free."""
    now = time.time()
    md = _md(-20)                       # 20 s old when it arrived
    # A MECHANISM IS SUPPLIED SO THE PROPERTY UNDER TEST IS REACHABLE. Without
    # one the currency is unestablished and the answer says so before any age
    # arithmetic matters -- which is correct, and is not what this test is about.
    def _sub(at):
        return {"alive_at": at - 0.5, "last_update_at": at - 1.0}

    quick = FA.venue_book_age(md, decision_at=now + 0.2, received_at=now,
                              requested_at=now - 0.3,
                              subscription=_sub(now + 0.2))
    assert quick["ok"] is True
    assert 19.5 < quick["age_s"] < 21.5
    # THE STAMP IS ALREADY PAST THE BOUND HERE AND IT DOES NOT REFUSE, because
    # a mechanism established the state and the stamp's meaning is unresolved.
    assert quick["stamp_beyond_the_bound"] is False
    slow = FA.venue_book_age(md, decision_at=now + 20.5, received_at=now,
                             requested_at=now - 0.3,
                             subscription=_sub(now + 20.5))
    # THE ACQUISITION DELAY IS STILL MEASURED AT THE DECISION INSTANT and still
    # recorded against the unchanged bound. What changed is that the refusal for
    # an old STAMP is now the accurate one -- and here a live subscription
    # establishes the state, so the stamp's age is an observation.
    assert slow["age_s"] > FA.MAX_VENUE_BOOK_AGE_S
    assert slow["stamp_beyond_the_bound"] is True
    assert slow["bound_s"] == pytest.approx(FA.MAX_VENUE_BOOK_AGE_S)
    assert slow["decision_lag_after_receipt_s"] > 20.0
    assert slow["age_evaluated_at"] == "decision_at"
    # AND WITH NO MECHANISM -- the state the lane is actually in -- the same
    # slow acquisition refuses, under the accurate name.
    none_ = FA.venue_book_age(md, decision_at=now + 20.5, received_at=now,
                              requested_at=now - 0.3)
    assert none_["ok"] is False
    assert none_["refusal"] == FA.R_BOOK_CURRENCY_NOT_ESTABLISHED


def test_a_stamp_between_request_and_receipt_is_ordinary():
    """AND THE CORRECTION TO MY OWN CLAIM. A response timestamp landing after
    the instant we captured BEFORE asking is not evidence of clock skew -- it is
    the ordinary case. It is assessed against the later decision instant, where
    it is legitimately in the past."""
    now = time.time()
    md = {"transactTime": _stamp(0.05), "bids": [_level(0.75, 40)]}
    # WITHOUT A MECHANISM the currency is unestablished and it refuses -- but the
    # point of this test is WHICH refusal, and it must not be the skew one.
    bare = FA.venue_book_age(md, decision_at=now + 0.4,
                             received_at=now + 0.2, requested_at=now)
    assert bare["refusal"] != FA.R_BOOK_CLOCK_INCONSISTENT, bare
    assert bare["refusal"] == FA.R_BOOK_CURRENCY_NOT_ESTABLISHED
    # WITH ONE, the ordinary stamp is admitted and the skew arm stays silent.
    got = FA.venue_book_age(
        md, decision_at=now + 0.4, received_at=now + 0.2, requested_at=now,
        subscription={"alive_at": now + 0.3, "last_update_at": now + 0.1})
    assert got["ok"] is True, got
    assert got["refusal"] is None
    assert got["age_s"] > 0                      # past, at the decision
    assert got["stamp_after_our_receipt_s"] < FA.MAX_VENUE_CLOCK_SKEW_AHEAD_S
    assert got["acquisition_s"] >= 0
    # THE SKEW ALLOWANCE IS EXPLICIT AND BOUNDED, not "any future is fine".
    assert FA.MAX_VENUE_CLOCK_SKEW_AHEAD_S > 0
    assert FA.MAX_VENUE_CLOCK_SKEW_AHEAD_S < 10
    assert set(FA.AGE_INSTANTS) == {"requested_at", "received_at",
                                    "decision_at"}


@pg
@pytest.mark.asyncio
async def test_the_selector_ages_both_clocks_at_the_decision_instant(
        monkeypatch):
    """ONE INSTANT FOR BOTH INPUTS, TAKEN AFTER ACQUISITION. The book and the
    probability must be aged at the same instant, and it must be later than
    every read -- otherwise a slow acquisition carries an expired probability
    into a live comparison."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        await _entry(conn, qty=10, price=0.60)
        await _probability(conn, p=0.55)
        pos = (await FM.open_positions(conn, account_id=ACCT,
                                       venue=VENUE))[0]
        _, _, client = _transport(monkeypatch, bids=[_level(0.75, 40)])
        got = await FM.select_exit(conn, pos, client=client,
                                    subscription=_live())
        assert got["ok"] is True, got
        ins = got["instants"]
        # THE ORDER OF THE FOUR INSTANTS IS THE WHOLE POINT.
        assert ins["pass_started_at"] <= ins["book_requested_at"]
        assert ins["book_requested_at"] <= ins["book_received_at"]
        assert ins["book_received_at"] <= ins["decision_at"]
        assert ins["decision_at"] > ins["pass_started_at"]
        # BOTH CLOCKS AGED AT `decision_at`.
        assert got["venue_book_age"]["instants"]["decision_at"] == \
            pytest.approx(ins["decision_at"])
        assert got["assessed_at"] == pytest.approx(ins["decision_at"])
        assert got["ev_hold"]["age_bound_s"] == pytest.approx(30.0)
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_delayed_submission_cannot_reuse_an_expired_assessment(
        monkeypatch):
    """MATERIAL DELAY BETWEEN SELECTION AND SUBMISSION, against the INPUTS'
    OWN DEADLINE.

    WHAT THIS TEST USED TO ASSERT, AND WHY THAT WAS WRONG. It checked that the
    submission refused once the ASSESSMENT was older than 30 s -- a bound the
    selector granted at its own decision instant. That let selection restart
    both clocks: a book observed 29 s ago bought a further 30 s by being looked
    at. The deadline now comes from the observations themselves, so what is
    asserted here is that the order refuses after THEIR bound, not after a fresh
    one handed out at selection.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        await _entry(conn, qty=10, price=0.60)
        await _probability(conn, p=0.55)
        pos = (await FM.open_positions(conn, account_id=ACCT,
                                       venue=VENUE))[0]
        monkeypatch.setattr(FM, "FUNDED_EXIT_SUBMISSION_ENABLED", True)
        monkeypatch.setattr(EX, "REAL_ORDER_SUBMISSION_ENABLED", True)
        pmus, sent, client = _transport(monkeypatch, order_id="vo-x",
                                       bids=[_level(0.75, 40)],
                                       exec_by_call=[[]])
        pick = await FM.select_exit(conn, pos, client=client,
                                    subscription=_live())
        assert pick["ok"] is True
        # THE DEADLINE IS THE EARLIER OBSERVATION'S, NOT decision_at + 30.
        dl = pick["inputs_expiry"]
        assert dl["ok"] is True, dl
        assert pick["inputs_expire_at"] == pytest.approx(dl["expires_at"])
        assert dl["expires_at"] == pytest.approx(
            min(dl["book"]["observed_at"] + dl["book"]["bound_s"],
                dl["probability"]["observed_at"]
                + dl["probability"]["bound_s"]))
        assert dl["expires_at"] < pick["assessed_at"] + 30.0 + 1e-6

        # ── PAST THE DEADLINE: refused, nothing reserved, nothing sent ─
        late = pick["inputs_expire_at"] + 2.0
        stale = await FM.submit_exit(
            conn, intent_id=pos["intent_id"], limit_price=pick["limit_price"],
            quantity=pick["selected_qty"],
            expect_proceeds_per_contract=pick["proceeds_per_contract"],
            assessed_at=pick["assessed_at"],
            inputs_expire_at=pick["inputs_expire_at"], adapter=pmus,
            venue=VENUE, now=late)
        assert stale["ok"] is False
        assert stale["refusal"] == FM.R_ASSESSMENT_EXPIRED, stale
        assert stale["venue_calls"] == 0
        assert stale["inputs_expiry"]["remaining_s"] < 0
        assert [k for k, _ in sent if k == "create"] == []
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_intents "
            " WHERE kind='EXIT'") == 0

        # ── AND WITHIN IT, IT PROCEEDS ──────────────────────────────
        ok = await FM.submit_exit(
            conn, intent_id=pos["intent_id"], limit_price=pick["limit_price"],
            quantity=pick["selected_qty"],
            expect_proceeds_per_contract=pick["proceeds_per_contract"],
            assessed_at=pick["assessed_at"],
            inputs_expire_at=pick["inputs_expire_at"], adapter=pmus,
            venue=VENUE)
        assert ok["submitted"] is True, ok
        assert ok["venue_calls"] == 1
    finally:
        await _clean(conn)
        await conn.close()


# ── 11 · THE INPUTS' REMAINING LIFETIME IS WHAT EXPIRES ──────────────
#
# THE DEFECT THIS SECTION CLOSES. `select_exit` set
# `assessment_expires_at = decision_at + 30` and `submit_exit` measured the age
# of the ASSESSMENT. Both clocks therefore restarted at selection, and the three
# counterexamples below all passed under that arrangement:
#
#   * a book already 29 s old under a 30 s bound, submitted 2 s later;
#   * a probability that expires before the book, ignored in favour of the book;
#   * a reservation that blocks on the position lock past the deadline and then
#     sends anyway.
#
# What expires is the OBSERVATION. The deadline is computed from the two
# observation stamps under the policies that admitted them, carried through the
# automated path, and re-checked at the last instant before the send.


@pg
@pytest.mark.asyncio
async def test_a_book_already_twenty_nine_seconds_old_expires_in_one(
        monkeypatch):
    """COUNTEREXAMPLE 1. The book is 29 s old at the decision instant, so a
    submission two seconds later is past the 30 s bound that admitted it --
    even though the assessment itself is two seconds old."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        await _entry(conn, qty=10, price=0.60)
        await _probability(conn, p=0.55)
        pos = (await FM.open_positions(conn, account_id=ACCT,
                                       venue=VENUE))[0]
        monkeypatch.setattr(FM, "FUNDED_EXIT_SUBMISSION_ENABLED", True)
        monkeypatch.setattr(EX, "REAL_ORDER_SUBMISSION_ENABLED", True)
        pmus, sent, client = _transport(monkeypatch, bids=[_level(0.75, 40)],
                                       book_age_s=29.0, exec_by_call=[[]])
        pick = await FM.select_exit(conn, pos, client=client,
                                    subscription=_live())
        assert pick["ok"] is True, pick
        # ADMITTED -- 29 s is inside the 30 s bound -- with ~1 s of life left.
        assert pick["venue_book_age"]["ok"] is True
        assert 0.0 < pick["remaining_lifetime_s"] < 2.0, pick
        assert pick["inputs_expiry"]["governed_by"] == "VENUE_BOOK"

        got = await FM.submit_exit(
            conn, intent_id=pos["intent_id"], limit_price=pick["limit_price"],
            quantity=pick["selected_qty"],
            expect_proceeds_per_contract=pick["proceeds_per_contract"],
            assessed_at=pick["assessed_at"],
            inputs_expire_at=pick["inputs_expire_at"], adapter=pmus,
            venue=VENUE, now=pick["assessed_at"] + 2.0)
        assert got["ok"] is False
        assert got["refusal"] == FM.R_ASSESSMENT_EXPIRED, got
        assert got["venue_calls"] == 0
        assert [k for k, _ in sent if k == "create"] == []
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_probability_determines_the_deadline_when_it_expires_first(
        monkeypatch):
    """COUNTEREXAMPLE 2. A fresh book and a nearly-expired probability: the
    probability governs, because the assessment is only as fresh as its
    stalest input."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        await _entry(conn, qty=10, price=0.60)
        # OBSERVED 27 s AGO: inside `ev_hold`'s 30 s bound, and expiring before
        # a book read a moment ago.
        await _probability(conn, p=0.55, at=time.time() - 27.0)
        pos = (await FM.open_positions(conn, account_id=ACCT,
                                       venue=VENUE))[0]
        _, _, client = _transport(monkeypatch, bids=[_level(0.75, 40)],
                                  book_age_s=0.0)
        pick = await FM.select_exit(conn, pos, client=client,
                                    subscription=_live())
        assert pick["ok"] is True, pick
        dl = pick["inputs_expiry"]
        assert dl["governed_by"] == "PROBABILITY", dl
        assert dl["probability_expires_at"] < dl["book_expires_at"]
        assert pick["inputs_expire_at"] == pytest.approx(
            dl["probability_expires_at"])
        assert 0.0 < pick["remaining_lifetime_s"] < 4.0, pick
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_lock_wait_past_the_deadline_releases_and_sends_nothing(
        monkeypatch):
    """COUNTEREXAMPLE 3, and the one the pre-reservation check cannot catch.

    `_reserve_exit` takes `SELECT ... FOR UPDATE` on the position. Another
    connection holds that lock here, so the submission genuinely waits -- past
    the deadline. It must then release the reservation it took and make ZERO
    venue calls, leaving the inventory available for the re-assessment that
    should replace it.
    """
    import asyncio

    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    other = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        await _entry(conn, qty=10, price=0.60)
        await _probability(conn, p=0.55)
        pos = (await FM.open_positions(conn, account_id=ACCT,
                                       venue=VENUE))[0]
        monkeypatch.setattr(FM, "FUNDED_EXIT_SUBMISSION_ENABLED", True)
        monkeypatch.setattr(EX, "REAL_ORDER_SUBMISSION_ENABLED", True)
        pmus, sent, client = _transport(monkeypatch, bids=[_level(0.75, 40)],
                                       exec_by_call=[[]])
        pick = await FM.select_exit(conn, pos, client=client,
                                    subscription=_live())
        assert pick["ok"] is True

        # THE LOCK, HELD BY SOMEBODY ELSE.
        tx = other.transaction()
        await tx.start()
        await other.fetchrow(
            "SELECT intent_id FROM bettor_funded_intents "
            " WHERE intent_id=$1 AND kind='ENTRY' FOR UPDATE",
            pos["intent_id"])

        async def _release_after(delay):
            await asyncio.sleep(delay)
            await tx.rollback()

        # A DEADLINE THAT PASSES DURING THE WAIT. It is live when the
        # submission starts -- the pre-reservation check passes -- and expired by
        # the time the lock is granted.
        deadline = time.time() + 0.4
        submit = FM.submit_exit(
            conn, intent_id=pos["intent_id"], limit_price=pick["limit_price"],
            quantity=pick["selected_qty"],
            expect_proceeds_per_contract=pick["proceeds_per_contract"],
            assessed_at=pick["assessed_at"], inputs_expire_at=deadline,
            adapter=pmus, venue=VENUE)
        got, _ = await asyncio.gather(submit, _release_after(1.0))

        assert got["ok"] is False
        assert got["refusal"] == FM.R_ASSESSMENT_EXPIRED, got
        # ZERO VENUE CALLS. This is the assertion the whole test exists for.
        assert got["venue_calls"] == 0
        assert [k for k, _ in sent if k == "create"] == []
        # THE RESERVATION WAS TAKEN AND RELEASED, so the inventory is free.
        assert got["reservation"]["ok"] is True
        assert got["reservation_released"] is True
        assert got["inputs_expiry"]["waited_for_the_lock_s"] > 0.3
        assert got["available_to_exit_after_release"] == pytest.approx(10.0)
        assert await conn.fetchval(
            "SELECT bettor_funded_available_to_exit($1)::float8",
            pos["intent_id"]) == pytest.approx(10.0)
        # AND THE ROW ITSELF IS NO LONGER OUTSTANDING, which is the reason
        # the availability above came back: `abandon_proven_not_sent` is the
        # same path the venue-boundary denial uses, and the reservation
        # predicate subtracts only outstanding or unresolved exits.
        row = await conn.fetchrow(
            "SELECT state FROM bettor_funded_intents WHERE intent_id=$1",
            got["exit_intent_id"])
        assert row["state"] == "ABANDONED", dict(row)
        assert await conn.fetchval(
            "SELECT bettor_funded_order_is_outstanding($1)",
            row["state"]) is False
    finally:
        try:
            await other.close()
        except Exception:                                      # noqa: BLE001
            pass
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_submission_with_no_deadline_at_all_is_refused(monkeypatch):
    """NO DEADLINE IS NOT PERMISSION TO SEND. A caller that cannot say when its
    prices expire has not established that they are current, and the refusal
    happens on the path that would otherwise reach the venue."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        await _entry(conn, qty=10, price=0.60)
        monkeypatch.setattr(FM, "FUNDED_EXIT_SUBMISSION_ENABLED", True)
        monkeypatch.setattr(EX, "REAL_ORDER_SUBMISSION_ENABLED", True)
        pmus, sent, _ = _transport(monkeypatch, exec_by_call=[[]])
        got = await FM.submit_exit(conn, intent_id="fpi-a", limit_price=0.70,
                                   quantity=5, adapter=pmus, venue=VENUE)
        assert got["ok"] is False
        assert got["refusal"] == FM.R_NO_INPUT_DEADLINE, got
        assert got["venue_calls"] == 0
        assert [k for k, _ in sent if k == "create"] == []
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_intents "
            " WHERE kind='EXIT'") == 0
    finally:
        await _clean(conn)
        await conn.close()


def test_a_missing_stamp_makes_the_expiry_unmeasured_not_distant():
    """A PURE CHECK ON THE RULE. An absent stamp or bound cannot produce a
    deadline, and the answer is UNMEASURED rather than a lifetime invented out
    of the instant somebody happened to ask."""
    ok = FM.input_deadline(
        {"parsed_epoch_s": 1000.0, "bound_s": 30.0,
         "basis": "VENUE_TRANSACT_TIME"},
        {"freshness": {"observed_at": 1005.0, "bound_s": 30.0}})
    assert ok["ok"] is True
    assert ok["expires_at"] == pytest.approx(1030.0)
    assert ok["governed_by"] == "VENUE_BOOK"

    for book, hold in (
            ({"bound_s": 30.0}, {"freshness": {"observed_at": 1.0,
                                               "bound_s": 30.0}}),
            ({"parsed_epoch_s": 1.0, "bound_s": 30.0},
             {"freshness": {"bound_s": 30.0}}),
            ({"parsed_epoch_s": 1.0}, {"freshness": {"observed_at": 1.0,
                                                     "bound_s": 30.0}}),
            ({"parsed_epoch_s": 1.0, "bound_s": 30.0},
             {"freshness": {"observed_at": 1.0}})):
        bad = FM.input_deadline(book, hold)
        assert bad["ok"] is False, (book, hold)
        assert bad["refusal"] == FM.R_INPUT_EXPIRY_UNMEASURED
        assert bad["expires_at"] is None
        assert bad["missing"]


@pg
@pytest.mark.asyncio
async def test_the_scheduled_path_carries_the_deadline_it_computed(
        monkeypatch):
    """THE AUTOMATED PATH, not a hand-assembled call. `manage` must pass the
    selector's deadline into the submission -- if it dropped it, `submit_exit`
    would refuse with R_NO_INPUT_DEADLINE rather than send on an unbounded
    assessment, and the exit record would say so."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        await _entry(conn, qty=10, price=0.60)
        await _probability(conn, p=0.55)
        monkeypatch.setattr(FM, "FUNDED_EXIT_SUBMISSION_ENABLED", True)
        monkeypatch.setattr(EX, "REAL_ORDER_SUBMISSION_ENABLED", True)
        pmus, sent, client = _transport(
            monkeypatch, order_id="vo-sched", bids=[_level(0.75, 40)],
            retrieve={"vo-1": {"order": {"state": "ORDER_STATE_FILLED"},
                               "executions": []}})
        got = await FM.manage(conn, account_id=ACCT, venue=VENUE,
                              subscription=_live(),
                              adapter=pmus, client=client)
        assert got["exits"], got
        ex = got["exits"][0]
        assert ex["inputs_expire_at"] is not None, ex
        assert ex["inputs_expiry_governed_by"] in ("VENUE_BOOK",
                                                   "PROBABILITY")
        # the pick and the submission agree on the same instant
        pick = [s for s in got["selection"]
                if s.get("intent_id") == "fpi-a"] or got["selection"]
        assert ex["venue_calls"] == 1, ex
        assert [k for k, _ in sent if k == "create"], sent
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_evidence_says_which_row_it_actually_read(monkeypatch):
    """THE DESCRIPTION, CORRECTED WITHOUT TOUCHING THE QUERY.

    `latest_probability` returns the freshest ELIGIBLE row -- the CURRENT one
    selected for this decision, NOT necessarily the historical row that priced
    the entry. An earlier docstring said it was the entry's. The query is right
    and unchanged; what it returns is now named accurately, with the row's own
    id and observation time so a reader can see exactly which observation the
    valuation rests on.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        await _entry(conn, qty=10, price=0.60)
        # TWO observations: an older one, then a newer one. The entry was
        # notionally taken against the older; the exit uses the NEWER.
        await _probability(conn, p=0.40, at=time.time() - 300)
        older = await conn.fetchval(
            "SELECT id FROM external_valuations ORDER BY id DESC LIMIT 1")
        await _probability(conn, p=0.55)
        newer = await conn.fetchval(
            "SELECT id FROM external_valuations ORDER BY id DESC LIMIT 1")
        assert newer > older

        pos = (await FM.open_positions(conn, account_id=ACCT,
                                       venue=VENUE))[0]
        _, _, client = _transport(monkeypatch, bids=[_level(0.75, 40)])
        got = await FM.select_exit(conn, pos, client=client,
                                    subscription=_live())
        assert got["ok"] is True, got
        ev = got["decision_evidence"]
        # IT IS THE CURRENT ROW, AND IT SAYS SO.
        assert ev["valuation_row_id"] == newer
        assert ev["valuation_row_id"] != older
        assert ev["valuation_observed_at"] is not None
        assert "NOT necessarily the historical row" in ev["which_row_this_is"]
        assert got["ev_hold"]["probability"] == pytest.approx(0.55)
        # THE DOCSTRING NO LONGER CLAIMS THE ENTRY'S ROW.
        import inspect
        doc = inspect.getdoc(FM._decision_evidence)
        assert "NOT necessarily the historical row that priced the entry" in doc
    finally:
        await _clean(conn)
        await conn.close()


# ── 12 · SCHEMA READINESS IS AN ENFORCED RELEASE CONDITION ───────────
#
# THE PRODUCTION STATE THIS REPRODUCES. On 2026-09-27 migration 126 rolled back
# on PostgreSQL 18, `start.sh` printed `migrate failed -- serving anyway`, and
# the API served a build whose funded code named columns the database did not
# have. Availability was correct and CAPABILITY was not: nothing stopped the
# funded lane, and with the submission switches flipped an order would have gone
# out from a process that could not record the fill.
#
# The three tests below put this database into that state -- inside a
# transaction that is rolled back, so the drop is real and temporary -- and
# assert what the release condition now requires: the general reads keep
# working, the funded capability reads BLOCKED with the missing objects named,
# and no funded submission path can reach a venue.


async def _break_the_funded_schema(conn):
    """Drop what migration 126 added, exactly as a rolled-back migration
    leaves it: the table is there, the column is not."""
    await conn.execute("ALTER TABLE bettor_funded_intents "
                       "  DROP COLUMN residual_qty CASCADE")
    await conn.execute("DELETE FROM schema_migrations "
                       " WHERE version LIKE '126%' OR version LIKE '128%'")


@pg
@pytest.mark.asyncio
async def test_the_funded_capability_is_blocked_when_the_schema_is_absent():
    """THE READ SAYS BLOCKED AND NAMES WHAT IS MISSING -- and says it about the
    objects, not about the migration ledger, because the objects are what the
    queries run against."""
    asyncpg = pytest.importorskip("asyncpg")
    from sportsassets import bettor_funded_schema as FS

    conn = await asyncpg.connect(DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        before = await FS.readiness(conn)
        assert before["ok"] is True, before
        assert before["capability"] == FS.CAPABILITY_AVAILABLE

        await _break_the_funded_schema(conn)
        after = await FS.readiness(conn)
        assert after["ok"] is False, after
        assert after["capability"] == FS.CAPABILITY_BLOCKED
        assert after["refusal"] == FS.R_SCHEMA_NOT_READY
        assert "residual_qty" in after["missing_columns"][
            "bettor_funded_intents"], after["missing_columns"]
        # THE LEDGER GAP IS REPORTED, NOT THE REASON.
        assert any(m.startswith("126") for m in after["missing_migrations"])
        assert after["what_stays_available"]
        # AND A LEDGER GAP ALONE DOES NOT BLOCK: the objects decide.
        await tx.rollback()
        tx = conn.transaction()
        await tx.start()
        await conn.execute("DELETE FROM schema_migrations "
                           " WHERE version LIKE '126%'")
        ledger_only = await FS.readiness(conn)
        assert ledger_only["ok"] is True, ledger_only
        assert ledger_only["capability"] == FS.CAPABILITY_AVAILABLE
        assert any(m.startswith("126")
                   for m in ledger_only["missing_migrations"])
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_no_funded_submission_is_possible_without_the_schema(
        monkeypatch):
    """BOTH SUBMISSION PATHS REFUSE, WITH BOTH SWITCHES FLIPPED ON.

    This is the test that matters: the switches are the release's other
    protection, so they are deliberately turned OFF as protection here -- i.e.
    turned ON -- to show the schema condition alone stops the venue being
    reached.
    """
    asyncpg = pytest.importorskip("asyncpg")
    from sportsassets import bettor_funded_schema as FS

    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        await _entry(conn, qty=10, price=0.60)
        tx = conn.transaction()
        await tx.start()
        try:
            await _break_the_funded_schema(conn)
            monkeypatch.setattr(FX, "FUNDED_SUBMISSION_ENABLED", True)
            monkeypatch.setattr(FM, "FUNDED_EXIT_SUBMISSION_ENABLED", True)
            monkeypatch.setattr(EX, "REAL_ORDER_SUBMISSION_ENABLED", True)
            pmus, sent, _ = _transport(monkeypatch)

            entry = await FX.submit_for_decision(
                conn, {"admissible": True, "us_market_slug": SLUG,
                       "event_key": EVENT, "order_intent": FX.LONG,
                       "payout_event": PAYS_ON,
                       "execution_plan": {"execution": {
                           "size": 5, "vwap": 0.62, "limit_price": 0.62}}},
                account_id=ACCT, venue=VENUE, adapter=pmus)
            assert entry["ok"] is False
            assert entry["refusal"] == FS.R_SCHEMA_NOT_READY, entry
            assert entry["funded_capability"] == FS.CAPABILITY_BLOCKED
            assert entry["nothing_was_sent"] is True

            ex = await FM.submit_exit(
                conn, intent_id="fpi-a", limit_price=0.70, quantity=5,
                inputs_expire_at=time.time() + 30.0, adapter=pmus,
                venue=VENUE)
            assert ex["ok"] is False
            assert ex["refusal"] == FS.R_SCHEMA_NOT_READY, ex
            assert ex["venue_calls"] == 0

            cancel = await FM.cancel_outstanding(conn, intent_id="fpi-a",
                                                 adapter=pmus)
            assert cancel["ok"] is False
            assert cancel["refusal"] == FS.R_SCHEMA_NOT_READY, cancel

            # NOT ONE VENUE CALL OF ANY KIND.
            assert [k for k, _ in sent
                    if k in ("create", "preview", "cancel")] == [], sent
        finally:
            await tx.rollback()
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_general_reads_keep_working_while_funded_is_blocked(
        monkeypatch):
    """AVAILABILITY IS PRESERVED. The command centre's funded section reports
    BLOCKED instead of raising -- which is what keeps the desk read serving --
    the scheduled servicing pass reports it instead of a traceback, and ordinary
    queries are untouched."""
    asyncpg = pytest.importorskip("asyncpg")
    from sportsassets import bettor_funded_schema as FS

    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        tx = conn.transaction()
        await tx.start()
        try:
            await _break_the_funded_schema(conn)

            # 1 . THE SECTION THE DESK READ ASSEMBLES: a capability statement,
            #     not an exception and not a book of zeros.
            sec = await FB.command_center(conn)
            assert sec["funded_capability"] == FS.CAPABILITY_BLOCKED, sec
            assert sec["refusal"] == FS.R_SCHEMA_NOT_READY
            assert sec["book_count"] is None, "zeros would be a lie here"
            assert sec["unresolved_discrepancy_count"] is None
            assert sec["schema_readiness"]["missing_columns"]
            assert sec["the_rest_of_the_service_is_unaffected"] is True

            # 2 . THE SCHEDULED PASS: reported, not raised.
            pmus, sent, client = _transport(monkeypatch)
            mg = await FM.manage(conn, account_id=ACCT, venue=VENUE,
                              subscription=_live(),
                                 adapter=pmus, client=client)
            assert mg["ok"] is False
            assert mg["funded_capability"] == FS.CAPABILITY_BLOCKED, mg
            assert mg["nothing_was_reconciled_settled_or_sent"] is True
            assert sent == [], sent

            # 3 . AND THE DATABASE IS OTHERWISE FINE.
            assert await conn.fetchval("SELECT 1") == 1
            assert await conn.fetchval(
                "SELECT count(*) FROM external_valuations") >= 0
        finally:
            await tx.rollback()
    finally:
        await _clean(conn)
        await conn.close()
