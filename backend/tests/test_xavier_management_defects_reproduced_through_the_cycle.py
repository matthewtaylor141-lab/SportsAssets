"""FIVE MANAGEMENT DEFECTS, EACH REPRODUCED THROUGH THE SCHEDULED CYCLE.

These are regressions for defects in the management path that the released
build aca3564 carries. Each test drives `ext_pinnacle_loop.cycle(conn)` -- the
scheduled entry point, which runs `_funded_service` -> `manage(defer_dispatch)`
-> `pass_once` (the production supplier `funded_pair_inputs`, `FD.decide`,
`FL.record_decision`, bound-plan dispatch) -- against a migrated database with a
bound TEST-shaped funded account. ONLY the venue transport (`pmus._get_client`)
and the book-currency seam are substituted; submission switches are patched True
inside the test only, and the fake adapter records what it was asked to send.
All market data here is SYNTHETIC.

WHY THE EARLIER ACCEPTANCE TESTS MISSED THEM
  * The binding tests (`test_the_decision_to_execution_boundary_holds.py`,
    `test_autonomy_completion_boundaries.py`) bound HAND-BUILT candidates that
    already carried a plan digest, so the supplier's second, digest-less REDUCE
    never existed in them (defect 1).
  * The pair-cycle tests (`test_the_scheduled_pair_lifecycle.py` `_pair_facts`)
    call `pass_once` with a HAND-BUILT supplier result -- a finished
    `hold_ranking`, finished plans and a `hedge_decision_record` with a payout
    event -- so the production supplier `funded_pair_inputs`, which drops the
    selector's DIRECT_EXIT, loses management's `not_rankable`, and passes
    `hedge_decision_record=None`, was never on their path (defects 2 and 4).
  * The scheduled-path test (`test_the_funded_lifecycle_is_complete.py`) went
    through `cycle()` but only asserted the in-memory dispatch result; it never
    read the persisted ledger row's `bound_filled_qty` or the exit intent's
    `decision_ref` (defect 5), and its position was filled at its own limit, so
    a basis read from the limit instead of the fills could not differ (defect 3).
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

ACCT = "acct-xavier-defects-test"
VENUE = "PMUS"
SLUG = "aec-mlb-hou-sea-2026-09-29"
EVENT = "ev-hou-sea-2026-09-29"
PAYS_ON = "HOUSTON_ASTROS"
PROB_VENUE = "PMUS_TEST_XDEFECTS"
LIMITS = {"capital_usd": 400, "per_order_usd": 60, "event_exposure_usd": 60,
          "max_exposure_usd": 200, "daily_loss_stop_usd": 40}


def _level(px, qty):
    return {"px": {"value": "%.2f" % px, "currency": "USD"}, "qty": str(qty)}


def _venue_clock(age_s=0.0):
    import datetime as _dt
    t = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=age_s)
    return t.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class _Orders:
    """The venue's order surface; records every request."""

    def __init__(self, sent):
        self.sent = sent
        self._n = 0

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
        self._n += 1
        qty = int(params.get("quantity") or 0)
        px = float((params.get("price") or {}).get("value") or 0)
        return {"id": "vo-x-%d" % self._n,
                "executions": [{"id": "vfx-%d" % self._n,
                                "type": "EXECUTION_TYPE_FILL",
                                "lastPx": {"value": "%.2f" % px,
                                           "currency": "USD"},
                                "lastShares": qty,
                                "order": {"state": "ORDER_STATE_FILLED"}}]}

    def list(self, params=None):
        self.sent.append(("list", dict(params or {})))
        return {"orders": []}

    def retrieve(self, order_id):
        self.sent.append(("retrieve", order_id))
        return None

    def cancel(self, order_id, body=None):
        self.sent.append(("cancel", order_id))
        return {}


class _Markets:
    def __init__(self, bids):
        self._bids = bids

    def retrieve_by_slug(self, slug):
        return {"market": {"marketSides": [
            {"identifier": slug + "-a", "description": "A"},
            {"identifier": slug + "-b", "description": "B"}]}}

    def book(self, slug):
        return {"marketData": {"bids": list(self._bids), "offers": [],
                               "transactTime": _venue_clock(0.0)}}


class _Client:
    def __init__(self, sent, bids):
        self.orders = _Orders(sent)
        self.markets = _Markets(bids)


def _transport(monkeypatch, *, bids):
    from sportsassets import pmus
    sent: list = []
    monkeypatch.setattr(pmus, "_get_client", lambda: _Client(sent, bids))
    monkeypatch.setattr(pmus._gate, "authorize", lambda *a, **k: {"ok": True})
    return sent


def _live():
    at = time.time()
    return {"alive_at": at - 0.5, "last_update_at": at - 1.0}


async def _stopped():
    return False, "the observation stop is engaged"


SHORT = "ORDER_INTENT_BUY_SHORT"

SETTLED_RULE = {"overall_established": True, "unmet": [],
                "attested": ["DRAW", "OVERTIME", "PUSH", "VOID"],
                "book_rule": "MONEYLINE_REGULATION_PLUS_OVERTIME",
                "venue_rules_text_read": True,
                "venue_rules_field": "rulesText"}


async def _clean(conn):
    async with conn.transaction():
        # Xavier's records are append-only by trigger; test rows are removed
        # with triggers disabled for this transaction only.
        await conn.execute("SET LOCAL session_replication_role = replica")
        for t in ("bettor_xavier_execution_events", "bettor_xavier_decisions"):
            if await conn.fetchval("SELECT to_regclass($1)", t) is not None:
                await conn.execute("DELETE FROM %s WHERE account_id=$1" % t,
                                   ACCT)
    await conn.execute(
        "DELETE FROM bettor_funded_decision_outcomes WHERE decision_id IN "
        "(SELECT decision_id FROM bettor_funded_decisions WHERE account_id=$1)",
        ACCT)
    await conn.execute("DELETE FROM bettor_funded_decisions WHERE account_id=$1",
                       ACCT)
    for t in ("bettor_funded_group_results", "bettor_funded_leg_reservations"):
        await conn.execute(
            "DELETE FROM %s WHERE group_id IN (SELECT group_id FROM "
            " bettor_funded_portfolio_groups WHERE account_id=$1)" % t, ACCT)
    for t in ("bettor_funded_economics", "bettor_funded_discrepancies",
              "bettor_funded_fills"):
        await conn.execute(
            "DELETE FROM %s WHERE intent_id IN (SELECT intent_id FROM "
            " bettor_funded_intents WHERE account_id=$1)" % t, ACCT)
    await conn.execute("DELETE FROM bettor_funded_intents WHERE account_id=$1",
                       ACCT)
    await conn.execute(
        "DELETE FROM bettor_funded_portfolio_groups WHERE account_id=$1", ACCT)
    await conn.execute("DELETE FROM external_valuations WHERE venue=$1",
                       PROB_VENUE)
    await conn.execute("DELETE FROM bettor_desk_accounts WHERE account_id=$1",
                       ACCT)
    for k in (FA.AUTHORIZATION_KEY, FA.LIMITS_KEY, FA.ACCOUNT_KEY):
        await conn.execute("DELETE FROM ingestion_state WHERE key=$1", k)


async def _seed(conn):
    await conn.execute(
        "INSERT INTO bettor_desk_accounts (account_id, desk_id, status, "
        " paused, accounting_status, opening_balance, note) VALUES "
        "($1,'desk-xdefects','ACTIVE',FALSE,'RECONCILED',0,'defect test')",
        ACCT)
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1,$2::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value=$2::jsonb", FA.LIMITS_KEY,
        json.dumps({"proposed": LIMITS, "approved": True,
                    "approved_by": "OWNER"}))
    now = time.time()
    eff = EX.effective_limits(LIMITS)
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1,$2::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value=$2::jsonb",
        FA.AUTHORIZATION_KEY,
        json.dumps({"account_id": ACCT, "venue": VENUE,
                    "venue_class": FA.VENUE_FUNDED, "by": "test",
                    "at": now, "expires_at": now + 3600, "revoked": False,
                    "effective_limits": eff["effective"],
                    "effective_digest": eff["effective_digest"]}))
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1,$2::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value=$2::jsonb", FA.ACCOUNT_KEY,
        json.dumps({"account_id": ACCT, "venue": VENUE, "approved": True}))


async def _entry(conn, *, intent_id="xdf-a", qty=10, limit=0.60,
                 fill_price=None, intent=None):
    opened = intent or FX.LONG
    got = await FB.record_intent(
        conn, intent_id=intent_id, account_id=ACCT, venue=VENUE,
        venue_class=FA.VENUE_FUNDED, us_market_slug=SLUG, event_key=EVENT,
        order_intent=opened, limit_price=limit, quantity=qty,
        collateral_usd=FX.collateral_for(limit, qty, opened),
        effective_digest="d", payout_event=PAYS_ON,
        held_is_long=(opened == FX.LONG))
    assert got.get("ok"), got
    await FB.record_acknowledgement(conn, intent_id, venue_order_id="vo-e",
                                    status="open")
    await FB.ingest_fills(conn, intent_id, [
        {"qty": float(qty),
         "price": float(limit if fill_price is None else fill_price),
         "venue_fill_id": "vfe-1"}])
    return got


async def _probability(conn, *, p):
    when = time.time()
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
        "VALUES ('EXP','v','EXTERNAL_BOOKMAKER_VALUATION','PINNACLE',"
        " 'pinnacle','multiplicative',$1,$2,'baseball','WINNER','{}'::jsonb,"
        " 2,2,'BUY',TRUE,'t',ARRAY[]::text[],$3,$4,$5,$5,FALSE,$6,"
        " to_timestamp($7),to_timestamp($7),0.5,'ELIGIBLE',to_timestamp($7),"
        " 0.5,0.5,0.05,$2,NULL,$8::jsonb,$9::jsonb)",
        PROB_VENUE, PAYS_ON, SLUG, float(p), PAYS_ON, FX.LONG, float(when),
        json.dumps(SETTLED_RULE),
        json.dumps({"verdict": "COMPATIBLE",
                    "fixture_event_state": "IN_PROGRESS",
                    "fixture_read": True}))


async def _premap(conn):
    """The venue catalogue's two sides of the held instrument, graded as a
    full-game winner (a first-five type is not in the graded list)."""
    await conn.execute("DELETE FROM us_premap WHERE market_slug=$1", SLUG)
    for intent, side in (("ORDER_INTENT_BUY_LONG", "hou"),
                         (SHORT, "sea")):
        await conn.execute(
            "INSERT INTO us_premap (identifier, event_slug, market_slug, "
            " kind, side_norm, question, event_title, sports_type, intent, "
            " team_abbr) VALUES ($1,$2,$3,'moneyline',$4,$5,$6,"
            " 'baseball_team_full_game_winner',$7,$8) ON CONFLICT DO NOTHING",
            SLUG + "-" + side, "mlb-hou-sea-2026-09-29", SLUG, side,
            "Will Houston win?", "Houston Astros vs. Seattle Mariners",
            intent, side.upper())


async def _prose(slug):
    return {"ok": True, "rules_field": "rulesText", "source": "SYNTHETIC",
            "rules_text": ("This market resolves to Houston if the Houston "
                           "Astros win the game, including extra innings. If "
                           "the game is postponed and not completed within 48 "
                           "hours the market resolves 50-50.")}


async def _run_cycle(conn, monkeypatch, *, bids):
    from sportsassets.workers import ext_pinnacle_loop as L
    monkeypatch.setattr(FM, "FUNDED_EXIT_SUBMISSION_ENABLED", True)
    monkeypatch.setattr(EX, "REAL_ORDER_SUBMISSION_ENABLED", True)
    sent = _transport(monkeypatch, bids=bids)
    monkeypatch.delenv("EDGE_ODDS_API_KEY", raising=False)
    monkeypatch.setattr(L, "_running", lambda c: _stopped())
    monkeypatch.setattr(
        L, "book_currency_evidence",
        lambda slug=None: {"subscription": _live(), "revalidation": None})
    out = await L.cycle(conn)
    return out, sent


async def _ledger(conn, intent_id="xdf-a"):
    row = await conn.fetchrow(
        "SELECT * FROM bettor_funded_decisions WHERE account_id=$1 "
        " AND decision_id LIKE $2 ORDER BY decided_at DESC LIMIT 1",
        ACCT, "dec:%" + intent_id + ":%")
    assert row is not None, "no persisted decision for the position"
    d = dict(row)
    for k in ("ranked", "unrankable", "inputs_missing", "inputs_present"):
        if isinstance(d.get(k), str):
            d[k] = json.loads(d[k])
    return d


def _creates(sent):
    return [p for k, p in sent if k == "create"]


#: A LADDER: 4 contracts bid at 0.75, 3 at 0.70, then a large bid at 0.40.
#: Holding is worth 0.55 per contract. DIRECT_EXIT takes the top level only
#: (4 @ 0.75, the rest held); REDUCE takes every level that pays more than
#: holding (4 @ 0.75 + 3 @ 0.70, 3 held), which is worth 3 x (0.70 - 0.55)
#: more before fees: REDUCE 7 is the best action.
REDUCE_BOOK = [_level(0.75, 4), _level(0.70, 3), _level(0.40, 400)]
#: A DEEP BOOK at 0.75: selling everything wins (DIRECT_EXIT).
EXIT_BOOK = [_level(0.75, 400)]


# ── 1 · REDUCE IS DISPATCHED WITH ITS BOUND PLAN ──────────────────────

@pg
@pytest.mark.asyncio
async def test_a_winning_reduce_is_sent_as_its_bound_plan(monkeypatch):
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        await _entry(conn, qty=10, limit=0.60)
        await _probability(conn, p=0.55)
        out, sent = await _run_cycle(conn, monkeypatch, bids=REDUCE_BOOK)
        svc = out["funded_servicing"]
        assert svc and svc["ok"] is True, svc
        led = await _ledger(conn)
        # THE PERSISTED WINNER IS REDUCE ...
        assert led["action"] == "REDUCE", led["action"]
        pcx = (svc.get("pair_cycle") or {}).get("exits") or []
        # ... AND IT WAS SENT, BOUND TO THE RANKED PLAN (aca3564: the
        # digest-less selector copy won the sort and binding refused).
        assert pcx and pcx[0]["submitted"] is True, json.dumps(
            svc.get("pair_cycle"), default=str)[:3000]
        assert pcx[0]["order_binding"]["ok"] is True, pcx[0]
        cr = _creates(sent)
        assert len(cr) == 1 and cr[0]["intent"] == "ORDER_INTENT_SELL_LONG"
        assert int(cr[0]["quantity"]) == 7
    finally:
        await _clean(conn)
        await conn.close()


# ── 2 · EVERY ALTERNATIVE REACHES THE PERSISTED DECISION ──────────────

@pg
@pytest.mark.asyncio
async def test_every_management_alternative_reaches_the_persisted_decision(
        monkeypatch):
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        await _entry(conn, qty=10, limit=0.60)
        await _probability(conn, p=0.55)
        out, _ = await _run_cycle(conn, monkeypatch, bids=REDUCE_BOOK)
        led = await _ledger(conn)
        ranked = {c.get("action") for c in (led["ranked"] or [])}
        unrank = {(u.get("action") or u.get("kind")): u
                  for u in (led["unrankable"] or [])}
        seen = ranked | set(unrank)
        # THE SELECTOR PRICED A FULL EXIT; the persisted decision must show
        # it -- ranked with a plan, or not rankable WITH ITS BLOCKER
        # (aca3564: silently dropped from both).
        assert "DIRECT_EXIT" in seen or "EXIT" in seen, (ranked, unrank)
        # MANAGEMENT'S OWN BLOCKERS reach the record (aca3564: `select_exit`
        # put them at the top level and `funded_pair_inputs` read them from
        # the ranking projection, so none arrived).
        # These three exist ONLY in `select_exit`'s top-level `not_rankable`
        # (no executable route at all), so their presence proves the
        # management blockers were carried into the decision.
        mgmt_blocked = {"HOLD_TO_SETTLEMENT", "POST_COMPLEMENT", "MERGE"}
        assert mgmt_blocked & set(unrank), sorted(unrank)
        for a in mgmt_blocked & set(unrank):
            assert unrank[a].get("blocker") or unrank[a].get("refusal"), \
                unrank[a]
    finally:
        await _clean(conn)
        await conn.close()


# ── 3 · ONE BASIS FOR THE SAME LEG IN EVERY VALUATION ─────────────────

@pg
@pytest.mark.asyncio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
async def test_the_hedge_valuation_uses_the_basis_hold_uses(side):
    """The production supplier builds the held leg the hedge is valued
    against; its cost must be the same per-contract basis HOLD, EXIT and REDUCE
    use (`FB.remaining_basis` from the fills), converted to the side held. The
    entry here filled BETTER than its limit, and on the SHORT side the wire
    price is the YES price (aca3564: the limit price, unconverted)."""
    asyncpg = pytest.importorskip("asyncpg")
    from sportsassets import bettor_funded_hedge_supply as HSUP
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        await _premap(conn)
        if side == "LONG":
            await _entry(conn, qty=10, limit=0.62, fill_price=0.58)
        else:
            await _entry(conn, qty=10, limit=0.40, fill_price=0.38,
                         intent=SHORT)
        pos = [p for p in await FB.open_entry_positions(
            conn, account_id=ACCT, venue=VENUE)
               if p["intent_id"] == "xdf-a"][0]
        rb = await FB.remaining_basis(conn, "xdf-a")
        per_contract_cents = 100.0 * float(rb["basis_per_contract"])
        # THE SUPPLIER THE SCHEDULED PASS CALLS, with only the venue's rules
        # prose (transport) substituted.
        got = await HSUP.held_leg_for(conn, position=pos,
                                      prose_reader=_prose)
        assert got.get("ok") and got.get("leg") is not None, got
        cost = got["leg"].cost_cents_per_unit
        assert float(cost) == pytest.approx(per_contract_cents, abs=0.5), (
            side, cost, per_contract_cents, rb)
    finally:
        await conn.execute("DELETE FROM us_premap WHERE market_slug=$1", SLUG)
        await _clean(conn)
        await conn.close()


# ── 4 · AN ACQUIRED HEDGE LEG CARRIES ITS PAYOUT EVENT ────────────────

@pg
@pytest.mark.asyncio
async def test_an_acquired_hedge_leg_is_manageable(monkeypatch):
    """The hedge leg the pass acquires must carry the payout event of the
    contract it bought, so the next servicing pass can value it for HOLD /
    EXIT / REDUCE like any held leg.

    WHY THIS IS NOT DRIVEN THROUGH `cycle()` ON aca3564: there, no hedge can
    be acquired on the scheduled path at all -- `funded_pair_inputs` supplies
    no `outside_split`, so every indirect candidate refuses R_NO_OUTSIDE_SPLIT
    -- which makes this defect latent behind that one. It is reproduced through
    `pass_once` with the supplier contract the production supplier returns for
    the hedge record (`hedge_decision_record=None`, `ext_pinnacle_loop.py`
    `funded_pair_inputs`), then the production `manage`. The full-cycle
    demonstration (acquire through `cycle()`, then manage the hedge leg next
    cycle) is the integrated acceptance test for Xavier."""
    asyncpg = pytest.importorskip("asyncpg")
    from sportsassets import bettor_funded_pair_cycle as PC
    from tests import test_the_scheduled_pair_lifecycle as SPL
    conn = await asyncpg.connect(DSN)
    try:
        await SPL._clean(conn)
        await SPL._seed(conn)
        await SPL._primary(conn)
        _, sent, _ = SPL._transport(monkeypatch, order_id="venue-hedge")
        got = await PC.pass_once(
            conn, account_id=SPL.ACCT, venue=SPL.VENUE,
            pair_inputs=SPL._pair_facts(SPL._exit_is_the_standalone_winner()),
            venue_positions=SPL.EMPTY_VENUE)
        assert got["ok"] is True and len(got["acquisitions"]) == 1, got
        hedge = await conn.fetchrow(
            "SELECT intent_id, payout_event, leg_role FROM "
            " bettor_funded_intents WHERE account_id=$1 AND leg_role='HEDGE'",
            SPL.ACCT)
        assert hedge is not None
        # aca3564: None -- `hedge_admission_record` took it from the supplier's
        # hedge record, which the production supplier sets to None.
        assert hedge["payout_event"], dict(hedge)
        mg = await FM.manage(conn, account_id=SPL.ACCT, venue=SPL.VENUE,
                             defer_dispatch=True)
        refusals = [s.get("refusal") for s in (mg.get("selection") or [])
                    if s.get("intent_id") == hedge["intent_id"]]
        assert FM.R_NO_PAYOUT_EVENT not in refusals, refusals
    finally:
        await SPL._clean(conn)
        await conn.close()


# ── 5 · THE DECISION BINDS THE FILLED QUANTITY; THE ORDER NAMES IT ────

@pg
@pytest.mark.asyncio
async def test_the_order_names_the_decision_and_the_filled_quantity_is_bound(
        monkeypatch):
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        await _entry(conn, qty=10, limit=0.60)
        await _probability(conn, p=0.55)
        out, sent = await _run_cycle(conn, monkeypatch, bids=EXIT_BOOK)
        led = await _ledger(conn)
        assert led["action"] in ("EXIT", "REDUCE"), led["action"]
        # aca3564: `pass_once` read `pos.get("filled_qty")`, a column the
        # intents table does not have, so this was None on every decision.
        assert led["bound_filled_qty"] is not None
        assert float(led["bound_filled_qty"]) == pytest.approx(10.0)
        winner = [c for c in led["ranked"]
                  if c.get("action") in ("DIRECT_EXIT", "REDUCE", "EXIT")]
        digest = winner[0].get("plan_digest") if winner else None
        assert digest, led["ranked"]
        ex = await conn.fetchrow(
            "SELECT decision_ref FROM bettor_funded_intents "
            " WHERE parent_intent_id='xdf-a' AND kind='EXIT'")
        assert ex is not None and _creates(sent), "no exit was sent"
        ref = ex["decision_ref"]
        ref = json.loads(ref) if isinstance(ref, str) else (ref or {})
        # aca3564: {"servicing": true, "reduces_exposure": true} -- no link.
        assert ref.get("decision_id") == led["decision_id"], ref
        assert ref.get("plan_digest") == digest, ref
    finally:
        await _clean(conn)
        await conn.close()
