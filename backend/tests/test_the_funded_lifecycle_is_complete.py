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


class _Markets:
    def __init__(self, bids=None, offers=None, raise_on_book=False):
        self._bids, self._offers = bids, offers
        self._raise = raise_on_book

    def retrieve_by_slug(self, slug):
        return {"market": {"marketSides": [
            {"identifier": slug + "-a", "description": "A"},
            {"identifier": slug + "-b", "description": "B"}]}}

    def book(self, slug):
        if self._raise:
            raise RuntimeError("the venue's book feed is unreachable")
        if self._bids is None and self._offers is None:
            return {}
        return {"marketData": {"bids": list(self._bids or []),
                               "offers": list(self._offers or [])}}


class _Client:
    def __init__(self, sent, *, bids=None, offers=None, raise_on_book=False,
                 **kw):
        self.orders = _Orders(sent, **kw)
        self.markets = _Markets(bids=bids, offers=offers,
                                raise_on_book=raise_on_book)


def _transport(monkeypatch, **kw):
    from sportsassets import pmus
    sent: list = []
    client = _Client(sent, **kw)
    monkeypatch.setattr(pmus, "_get_client", lambda: client)
    monkeypatch.setattr(pmus._gate, "authorize", lambda *a, **k: {"ok": True})
    return pmus, sent, client


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
                 fill_qty=None, commission=None):
    coll = FX.collateral_for(price, qty, FX.LONG)
    got = await FB.record_intent(
        conn, intent_id=intent_id, account_id=ACCT, venue=VENUE,
        venue_class=FA.VENUE_FUNDED, us_market_slug=SLUG, event_key=EVENT,
        order_intent=FX.LONG, limit_price=price, quantity=qty,
        collateral_usd=coll, effective_digest="d",
        payout_event=payout_event, held_is_long=True)
    if not got.get("ok"):
        return got
    await FB.record_acknowledgement(conn, intent_id, venue_order_id=vo,
                                    status="open")
    f = {"qty": float(qty if fill_qty is None else fill_qty), "price": price,
         "venue_fill_id": fill_id}
    if commission is not None:
        f["commission_usd"] = commission
    return dict(got, fills=await FB.ingest_fills(conn, intent_id, [f]))


async def _probability(conn, *, p=0.55, slug=SLUG, at=None,
                       eligibility="ELIGIBLE"):
    """AN ELIGIBLE EXTERNAL VALUATION ROW, in the shape `ev_hold` reads."""
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
        " ineligible_reason) "
        "VALUES ('EXP','v','EXTERNAL_BOOKMAKER_VALUATION','PINNACLE','pinnacle','multiplicative',"
        " $1,$2,'basketball','WINNER','{}'::jsonb,2,2,'BUY',TRUE,'t',"
        " ARRAY[]::text[],$3,$4,$5,$5,FALSE,$6,to_timestamp($7),"
        " to_timestamp($7),0.5,$8,to_timestamp($7),0.5,0.5,0.05,$2,$9)",
        "PMUS_TEST_COMPLETE", PAYS_ON, slug, float(p), PAYS_ON, FX.LONG,
        float(when), eligibility,
        (None if eligibility == "ELIGIBLE" else "HELD_BY_A_TEST"))


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
                           adapter=pmus, venue=VENUE),
            FM.submit_exit(b, intent_id="fpi-a", limit_price=0.70,
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
        assert "Refused INSIDE the lock" in refused[0]["why"]
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
                                   quantity=4, adapter=pmus, venue=VENUE)
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
        got = await FM.select_exit(conn, pos, client=client)
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
        got = await FM.select_exit(conn, pos, client=broken)
        assert got["refusal"] == FM.R_BOOK_UNREADABLE

        # (c) A BOOK WITH NO EXIT SIDE.
        _, _, empty = _transport(monkeypatch, bids=[])
        got = await FM.select_exit(conn, pos, client=empty)
        assert got["refusal"] == FM.R_NO_EXIT_SIDE

        # (d) NO ELIGIBLE PROBABILITY ROW.
        _, _, ok_book = _transport(monkeypatch, bids=[_level(0.70, 50)])
        got = await FM.select_exit(conn, pos, client=ok_book)
        assert got["refusal"] == FM.R_NO_PROBABILITY

        # (e) A HELD ROW IS NEVER USED FOR A DECISION, and that is reported as
        #     a containment action rather than a missing feed.
        await _probability(conn, p=0.55, eligibility="HELD")
        got = await FM.select_exit(conn, pos, client=ok_book)
        assert got["refusal"] == FM.R_NO_PROBABILITY
        assert got["probability_read"]["eligibility"] == "HELD"

        # (f) A STALE ELIGIBLE ROW: EV_HOLD refuses on its OWN freshness
        #     bound, which this lane does not widen.
        await conn.execute("DELETE FROM external_valuations WHERE venue=$1",
                           "PMUS_TEST_COMPLETE")
        await _probability(conn, p=0.55, at=time.time() - 86400)
        got = await FM.select_exit(conn, pos, client=ok_book)
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
        got = await FM.select_exit(conn, pos, client=client)
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
        monkeypatch.delenv("EDGE_ODDS_API_KEY", raising=False)
        monkeypatch.setattr(L, "_running", lambda c: _stopped())

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
        assert svc["exits"] and svc["exits"][0]["submitted"] is True
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
