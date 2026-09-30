"""XAVIER, DEMONSTRATED THROUGH THE REAL SCHEDULED PATH.

WHAT RUNS. Every demonstration drives `ext_pinnacle_loop.cycle(conn)` -- the
function the worker runs on its schedule, which services the funded book
first (`_funded_service` -> `manage(defer_dispatch=True)` -> `pass_once`,
Xavier's record, the one-measure gate, bound-plan dispatch, recovery,
investigations, settlement re-reads, the learning pass and the daily review)
-- against a migrated PostgreSQL database with a bound funded account whose
approved limits, owner authorization and system authorization are written
through the REAL writers (`bettor_desk_controls.set_account` / `set_limits`,
the limit approval as the admin route applies it, `bettor_owner_
authorization.record_owner_authorization`, `bettor_funded_activation.
authorize`).

WHAT IS SUBSTITUTED -- THE VENUE TRANSPORT AND NOTHING ABOVE IT. The pmus
client (`pmus._get_client`: book reads, the rules prose on `markets.list`, the
order adapter's `orders.*`, the account read's `portfolio.positions`) and its
boundary gate; the settlement probe (`bettor_venue_settlement_probe.probe`);
and the scheduler's book-currency seam (`book_currency_evidence`, which today
supplies no mechanism in production -- a stated assumption, as every
scheduled-lane test states it). Candidates, rankings, decisions and
valuations are never substituted. The submission switches are patched True
INSIDE these tests only, and the fake adapter records what it was asked to
send.

EVIDENCE THE DEMONSTRATIONS REST ON, ALL SYNTHETIC AND LABELLED SO. The
Red Sox / Yankees fixture, its books and its settlement prose; the external
probability row (from a TEST source version with a SYNTHETIC passing
calibration row); the conditional model KEY_HEDGE_GIVEN_PRIMARY, trained on
SYNTHETIC observations through the production recorder and labeller and
approved ONLY by `promote` with a named approver on prospective observations
(`tests/approved_conditional_model`); and the void rate those observations
measure (71 settled fixtures, 3 void -> rate 0.042, Wilson upper 95% 0.117).
NOTHING HERE IS EVIDENCE ABOUT ANY MARKET.

THE FIXTURE. A baseball moneyline including extra innings cannot end level;
the hedge is the opponent's +1.5 run line in the same game and period -- a
settlement-compatible MIDDLE (Red Sox win by exactly one: both legs pay).
(The spec's example is an NFL moneyline + spread; an NFL moneyline can end
level, which the payout-state distribution refuses to price without a tie
probability, so the baseball analogue is used.)
"""
from __future__ import annotations

import json
import os
import time

import pytest

from sportsassets import bettor_desk_controls as CTL
from sportsassets import bettor_entry_execution as EX
from sportsassets import bettor_funded_activation as FA
from sportsassets import bettor_funded_book as FB
from sportsassets import bettor_funded_decision as FD
from sportsassets import bettor_funded_execution as FX
from sportsassets import bettor_funded_management as FM
from sportsassets import bettor_funded_pair_cycle as PC
from sportsassets import bettor_owner_authorization as OA
from sportsassets import bettor_venue_settlement_probe as SP
from sportsassets import bettor_xavier as XV

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

LABEL = "SYNTHETIC -- ENGINEERING DEMONSTRATION, NOT EVIDENCE ABOUT ANY MARKET"

#: THE DEMONSTRATION MARK keeps this account out of strategy performance.
ACCT = "acct-funded-DEMONSTRATION-xavier-xc"
VENUE = "PMUS"
EVENT = "mlb-bos-nyy-2026-10-06"
HELD = "aec-mlb-bos-nyy-2026-10-06"                  # the moneyline
SIB = "asc-mlb-bos-nyy-2026-10-06-neg-1pt5"          # BOS -1.5 / NYY +1.5
PAYS_ON = "BOSTON_RED_SOX"
PROB_VENUE = "PMUS_TEST_XAVIER_XC"
HELD_ID = "fpi-xavier-xc-held"
#: The measured-void-rate helper's fixture prefix (tests/measured_void_rate).
VOID_PREFIX = "xc-void"
HEDGE_CID = SIB + "#ORDER_INTENT_BUY_SHORT"          # the NYY +1.5 side
OPERATOR = "synthetic-xc-owner"
AUTH = {"admin_token_verified": True, "resolution_key_verified": True,
        "operator": OPERATOR, "route": "test"}
LIMITS = {"capital_usd": 400, "per_order_usd": 60, "event_exposure_usd": 60,
          "max_exposure_usd": 200, "daily_loss_stop_usd": 40}
#: The venue's settlement prose for both contracts (SYNTHETIC): extra
#: innings included, so the moneyline cannot end level; a cancelled game
#: refunds every stake, so the VOID cell is established.
PROSE = ("Resolves on the final score and includes any extra innings played. "
         "A tie resolves 50-50. If the game is cancelled all stakes are "
         "refunded.")


# ════════════════════════════════════════════════════════════════════
# THE VENUE, AT ITS TRANSPORT BOUNDARY
# ════════════════════════════════════════════════════════════════════

def _iso(t):
    import datetime as _dt
    return (_dt.datetime.fromtimestamp(t, _dt.timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z")


def _lvl(p, q):
    return {"px": {"value": "%.2f" % p, "currency": "USD"}, "qty": str(q)}


class Venue:
    """One venue shared by every cycle of a test (a restart sees the orders
    the first process sent). Books, prose and matching are SYNTHETIC.

    `create` fills a BUY at or inside its limit against the displayed book
    (a SHORT buy consumes bids at 1 - bid), a SELL_LONG against the bids, up
    to `fill_cap` contracts; `raise_on_create` makes the send raise (no
    answer). Every request is recorded in `sent`."""

    def __init__(self, *, books, holdings):
        self.books = {s: {"bids": list(b.get("bids") or ()),
                          "offers": list(b.get("offers") or ())}
                      for s, b in books.items()}
        self.holdings = dict(holdings)       # slug -> (net, cost)
        self.sent: list = []
        self.orders: dict = {}
        self.creates = 0
        self.fill_cap = None
        self.raise_on_create = None
        v = self

        class _Markets:
            def list(self, params=None):
                slugs = list((params or {}).get("slug") or [])
                v.sent.append(("markets.list", slugs))
                return {"markets": [{"slug": s, "description": PROSE}
                                    for s in slugs if s in v.books]}

            def book(self, slug):
                v.sent.append(("markets.book", slug))
                b = v.books.get(slug)
                if b is None:
                    return {}
                return {"marketData": {
                    "bids": [_lvl(p, q) for p, q in b["bids"]],
                    "offers": [_lvl(p, q) for p, q in b["offers"]],
                    "transactTime": _iso(time.time() - 1.0)}}

            def retrieve_by_slug(self, slug):
                return {"market": {"slug": slug, "marketSides": [
                    {"identifier": slug, "description": "Yankees",
                     "long": False},
                    {"identifier": slug, "description": "Red Sox",
                     "long": True}]}}

        class _Orders:
            def preview(self, body):
                req = (body or {}).get("request") or {}
                v.sent.append(("preview", req))
                px = float((req.get("price") or {}).get("value") or 0)
                qty = int(req.get("quantity") or 0)
                cost = ((1.0 - px) * qty
                        if req.get("intent") == "ORDER_INTENT_BUY_SHORT"
                        else px * qty)
                return {"order": {"price": req.get("price"),
                                  "quantity": req.get("quantity"),
                                  "cashOrderQty": {"value": "%.4f" % cost,
                                                   "currency": "USD"}}}

            def create(self, params):
                v.sent.append(("create", dict(params)))
                v.creates += 1
                if v.raise_on_create is not None:
                    raise v.raise_on_create
                return v._match(params)

            def list(self, params=None):
                v.sent.append(("orders.list", dict(params or {})))
                return {"orders": []}

            def retrieve(self, order_id):
                v.sent.append(("orders.retrieve", order_id))
                o = v.orders.get(str(order_id))
                return None if o is None else o["record"]

            def cancel(self, order_id, body=None):
                v.sent.append(("orders.cancel", order_id))
                return {}

        class _Portfolio:
            def positions(self, params=None):
                v.sent.append(("portfolio.positions", dict(params or {})))
                return {"positions": {
                    s: {"netPosition": n, "cost": c}
                    for s, (n, c) in v.holdings.items() if n}, "eof": True}

        class _Client:
            markets = _Markets()
            orders = _Orders()
            portfolio = _Portfolio()
        self.client = _Client()

    def _match(self, params):
        slug = params.get("marketSlug")
        intent = params.get("intent")
        limit = float((params.get("price") or {}).get("value") or 0)
        qty = int(params.get("quantity") or 0)
        b = self.books.get(slug) or {"bids": [], "offers": []}
        if intent == "ORDER_INTENT_BUY_LONG":
            levels = [(p, q) for p, q in b["offers"] if p <= limit + 1e-9]
        else:      # BUY_SHORT and SELL_LONG both trade against the bids
            levels = [(p, q) for p, q in b["bids"] if p >= limit - 1e-9]
        avail = sum(q for _, q in levels)
        if self.fill_cap is not None:
            avail = min(avail, self.fill_cap)
        take = min(qty, avail)
        execs, left, n = [], take, 0
        for p, q in levels:
            if left <= 0:
                break
            t = min(q, left)
            n += 1
            execs.append({"id": "vf-xc-%d-%d" % (self.creates, n),
                          "type": "EXECUTION_TYPE_FILL",
                          "lastPx": {"value": "%.2f" % p, "currency": "USD"},
                          "lastShares": t,
                          "order": {"state": "ORDER_STATE_PARTIALLY_FILLED"}})
            left -= t
        if execs:
            execs[-1]["order"]["state"] = ("ORDER_STATE_FILLED" if take >= qty
                                           else "ORDER_STATE_CANCELED")
        oid = "venue-xc-%d" % self.creates
        net, cost = self.holdings.get(slug, (0.0, 0.0))
        for e in execs:
            px = float(e["lastPx"]["value"])
            q = float(e["lastShares"])
            if intent == "ORDER_INTENT_SELL_LONG":
                per = cost / net if net else 0.0
                net, cost = net - q, cost - per * q
            elif intent == "ORDER_INTENT_BUY_SHORT":
                net, cost = net - q, cost + (1.0 - px) * q
            else:
                net, cost = net + q, cost + px * q
        self.holdings[slug] = (net, cost)
        self.orders[oid] = {"record": {
            "order": {"id": oid, "marketSlug": slug, "intent": intent,
                      "price": {"value": "%.2f" % limit, "currency": "USD"},
                      "quantity": qty, "cumQuantity": take,
                      "leavesQuantity": 0,
                      "state": ("ORDER_STATE_FILLED" if take >= qty
                                else "ORDER_STATE_CANCELED")},
            "executions": [dict(e) for e in execs]}}
        return {"id": oid, "executions": execs,
                **({} if execs else {"order": {"state":
                                               "ORDER_STATE_CANCELED"}})}

    def creates_sent(self):
        return [p for k, p in self.sent if k == "create"]


def substitute(monkeypatch, venue: Venue, *, settlements=None):
    """THE TRANSPORT (and the clock-free seams every scheduled test states).

    `settlements`: slug -> the settlement probe's answer (SYNTHETIC); a slug
    not named reads PENDING."""
    from sportsassets import pmus
    from sportsassets.workers import ext_pinnacle_loop as L

    monkeypatch.setattr(pmus, "_get_client", lambda: venue.client)
    monkeypatch.setattr(pmus._gate, "authorize", lambda *a, **k: {"ok": True})
    table = dict(settlements or {})

    def probe(client, slug):
        return dict(table.get(slug) or {
            "terminal_reading": SP.R_PENDING,
            "why": "SYNTHETIC: the market has not settled"})
    monkeypatch.setattr(SP, "probe", probe)

    real_bce = L.book_currency_evidence

    def supplied_currency(slug=None):
        got = dict(real_bce(slug))
        now = time.time()
        got["subscription"] = {"alive_at": now - 0.5,
                               "last_update_at": now - 1.0}
        got["SUPPLIED_BY_A_TEST"] = ("the venue documents no timing contract "
                                     "(P5); this is the stated assumption")
        return got
    monkeypatch.setattr(L, "book_currency_evidence", supplied_currency)
    monkeypatch.delenv("EDGE_ODDS_API_KEY", raising=False)

    async def _stopped(conn):
        return False, "the entry lane is stopped; servicing runs regardless"
    monkeypatch.setattr(L, "_running", _stopped)
    L.rules_cache_reset()
    # THE THREE SUBMISSION SWITCHES, IN THIS TEST ONLY.
    monkeypatch.setattr(EX, "REAL_ORDER_SUBMISSION_ENABLED", True)
    monkeypatch.setattr(FX, "FUNDED_SUBMISSION_ENABLED", True)
    monkeypatch.setattr(FM, "FUNDED_EXIT_SUBMISSION_ENABLED", True)


def reset_process_state():
    """A RESTART: every process-local cache or state the modules on this path
    keep is dropped, so the next cycle rebuilds everything from the
    database."""
    from sportsassets.workers import ext_pinnacle_loop as L
    L.rules_cache_reset()
    L._LAST_OBSERVATION_PASS[0] = 0.0
    L._LAST_CALIBRATION_MEASURE[0] = 0.0


# ════════════════════════════════════════════════════════════════════
# THE DATABASE: THE ACCOUNT, THROUGH THE REAL WRITERS
# ════════════════════════════════════════════════════════════════════

_STATE_KEYS = [FA.ACCOUNT_KEY, FA.LIMITS_KEY, FA.AUTHORIZATION_KEY,
               FA.OWNER_AUTH_KEY, "ext_pinnacle_last_cycle"]


async def authorize(conn) -> dict:
    """Bind the account, record and approve the limits, sign the owner's
    authorization and issue the system authorization -- each through the
    writer production uses. Returns the system authorization."""
    from sportsassets import bettor_account_onboarding as ON
    from tests import test_funded_activation_is_a_real_path as ACT

    await conn.execute(
        "INSERT INTO bettor_desk_accounts (account_id, desk_id, status, "
        " opening_balance, opened_at, note, provenance, paused, "
        " accounting_status, accounting_detail) VALUES ($1,'desk-xavier-xc',"
        " 'ACTIVE',0,now(),$2,'{}'::jsonb,FALSE,'CLEAN','{}'::jsonb)",
        ACCT, LABEL)
    got = await CTL.set_account(conn, by="xc-test", account={
        "account_id": ACCT, "name": "SYNTHETIC XC", "venue": VENUE})
    assert got["ok"], got
    got = await CTL.set_limits(conn, by="xc-test", proposed=dict(LIMITS))
    assert got["ok"], got
    # THE APPROVAL, EXACTLY AS `POST /api/admin/funded-limits/approve`
    # APPLIES IT (the route's own body; it needs the service pool).
    rec = json.loads(await conn.fetchval(
        "SELECT value FROM ingestion_state WHERE key=$1", FA.LIMITS_KEY))
    eff = EX.effective_limits(FA.normalise_limit_keys(rec["proposed"]))
    rec.update(approved=True, approved_by="OWNER", approved_at=time.time(),
               enforced=True, effective_when_approved=eff["effective"],
               effective_digest=eff["effective_digest"],
               tightened=eff["tightened"],
               ignored_because_looser=eff["ignored_because_looser"])

    async def _write():
        await conn.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1,$2::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value=$2::jsonb",
            FA.LIMITS_KEY, json.dumps(rec))
    moved = await OA.apply_scope_change(
        conn, reason=OA.SCOPE_LIMITS_APPROVED, write=_write, by="OWNER")
    assert moved["ok"], moved
    # READINESS: a current venue reconciliation for THIS account and the
    # activation suite's own evidence rows (SYNTHETIC fixture rows).
    await ACT._record_reconciliation_evidence(conn, account_id=ACCT,
                                              venue=VENUE)
    await ACT._seed_evidence(conn, waived=False, reconciled=False)
    signed = await OA.record_owner_authorization(
        conn, account_id=ACCT, venue=VENUE,
        effective_digest=eff["effective_digest"],
        statement=("SYNTHETIC TEST: I authorise %s at %s under the approved "
                   "limits" % (ACCT, VENUE)),
        confirm=ACCT, operator=OPERATOR, auth=dict(AUTH))
    assert signed["ok"], signed
    auth = await FA.authorize(conn, account_id=ACCT, venue=VENUE,
                              by="xc-test")
    assert auth["ok"] is True, auth
    assert auth["owner_authorization_validated"] is True
    del ON
    return auth["applied"]


async def catalogue(conn):
    """The venue catalogue's rows for the fixture: the moneyline's two sides
    and the run line's two sides (BOS -1.5 LONG, NYY +1.5 SHORT)."""
    for col, typ in (("team_abbr", "text"), ("team_name", "text"),
                     ("game_start", "timestamptz"), ("sports_type", "text"),
                     ("signed", "text"), ("intent", "text")):
        await conn.execute("ALTER TABLE us_premap ADD COLUMN IF NOT EXISTS "
                           "%s %s" % (col, typ))
    await conn.execute("DELETE FROM us_premap WHERE event_slug=$1", EVENT)
    rows = [
        (HELD + ":bos", HELD, "baseball_team_full_game_winner",
         "boston red sox", "bos", "00", None, "ORDER_INTENT_BUY_LONG"),
        (HELD + ":nyy", HELD, "baseball_team_full_game_winner",
         "new york yankees", "nyy", "00", None, "ORDER_INTENT_BUY_SHORT"),
        (SIB + ":bos", SIB, "baseball_team_full_game_spread",
         "yes", "bos", "1.5", "-1.5", "ORDER_INTENT_BUY_LONG"),
        (SIB + ":nyy", SIB, "baseball_team_full_game_spread",
         "no", "nyy", "1.5", "+1.5", "ORDER_INTENT_BUY_SHORT"),
    ]
    for ident, slug, st, side, abbr, line, signed, intent in rows:
        await conn.execute(
            "INSERT INTO us_premap (identifier, event_slug, event_title,"
            " market_slug, question, kind, line, side_norm, intent, signed,"
            " team_abbr, team_name, sports_type, game_start)"
            " VALUES ($1,$2,$3,$4,$5,'side',$6,$7,$8,$9,$10,$11,$12,"
            "         now() + interval '3 hours')",
            ident, EVENT, "Boston Red Sox vs. New York Yankees", slug,
            "Who will win?", line, side, intent, signed, abbr, abbr, st)


async def held_position(conn, *, qty=10, price=0.50, intent_id=HELD_ID):
    """The entry, recorded, acknowledged and filled through the book's own
    writers -- in a group a second leg may join."""
    got = await FB.record_intent(
        conn, intent_id=intent_id, account_id=ACCT, venue=VENUE,
        venue_class=FA.VENUE_FUNDED, us_market_slug=HELD, event_key=EVENT,
        order_intent=FX.LONG, limit_price=price, quantity=qty,
        collateral_usd=FX.collateral_for(price, qty, FX.LONG),
        effective_digest="d-xc", payout_event=PAYS_ON, held_is_long=True,
        portfolio_group_id=None, leg_role="PRIMARY",
        group_structure="INDIRECT_MIDDLE")
    assert got.get("ok"), got
    await FB.record_acknowledgement(conn, intent_id,
                                    venue_order_id="venue-entry-xc",
                                    status="open")
    await FB.ingest_fills(conn, intent_id, [
        {"qty": float(qty), "price": price, "venue_fill_id": "vf-entry-xc"}])
    return got


#: The settlement rule the external valuation row carries (SYNTHETIC).
SETTLED_RULE = {"overall_established": True, "unmet": [],
                "attested": ["DRAW", "OVERTIME", "PUSH", "VOID"],
                "book_rule": "MONEYLINE_REGULATION_PLUS_OVERTIME",
                "venue_rules_text_read": True,
                "venue_rules_field": "description"}


async def probability(conn, *, p):
    """AN ELIGIBLE EXTERNAL VALUATION ROW for the held contract, from the
    TEST source version whose SYNTHETIC calibration row
    `approved_conditional_model.approve` seeds."""
    from tests import approved_conditional_model as ACM
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
        "VALUES ('EXP',$1,'EXTERNAL_BOOKMAKER_VALUATION','PINNACLE',"
        " 'pinnacle','multiplicative',$2,$3,'baseball','WINNER','{}'::jsonb,"
        " 2,2,'BUY',TRUE,$4,ARRAY[]::text[],$5,$6,$7,$7,FALSE,$8,"
        " to_timestamp($9),to_timestamp($9),0.5,'ELIGIBLE',to_timestamp($9),"
        " 0.5,0.5,0.05,$3,NULL,$10::jsonb,$11::jsonb)",
        ACM.CAL_SOURCE, PROB_VENUE, PAYS_ON, LABEL, HELD, float(p), PAYS_ON,
        FX.LONG, float(when), json.dumps(SETTLED_RULE),
        json.dumps({"verdict": "COMPATIBLE",
                    "fixture_event_state": "IN_PROGRESS",
                    "fixture_read": True}))


async def clean(conn):
    from sportsassets import bettor_account_onboarding as ON
    from sportsassets import bettor_external_shadow as ext
    from tests import approved_conditional_model as ACM

    async with conn.transaction():
        # Append-only records: THIS account's rows removed with triggers
        # suspended for this transaction only.
        await conn.execute("SET LOCAL session_replication_role = replica")
        for t in ("bettor_xavier_execution_events", "bettor_xavier_decisions"):
            await conn.execute("DELETE FROM %s WHERE account_id=$1" % t, ACCT)
        for t in ("bettor_funded_settlement_rechecks",
                  "bettor_funded_settlement_corrections",
                  "bettor_funded_correction_audit"):
            await conn.execute(
                "DELETE FROM %s WHERE intent_id IN (SELECT intent_id FROM "
                " bettor_funded_intents WHERE account_id=$1)" % t, ACCT)
        await conn.execute(
            "DELETE FROM bettor_funded_investigations WHERE account_id=$1",
            ACCT)
    await conn.execute(
        "DELETE FROM bettor_funded_decision_outcomes WHERE decision_id IN "
        "(SELECT decision_id FROM bettor_funded_decisions WHERE account_id=$1)",
        ACCT)
    await conn.execute("DELETE FROM bettor_funded_decisions WHERE account_id=$1",
                       ACCT)
    await conn.execute(
        "DELETE FROM bettor_funded_operation_evidence WHERE operation_id IN "
        "(SELECT operation_id FROM bettor_funded_leg_reservations WHERE "
        " group_id IN (SELECT group_id FROM bettor_funded_portfolio_groups "
        " WHERE account_id=$1))", ACCT)
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
    await conn.execute("DELETE FROM external_valuations "
                       " WHERE experiment_id=$1", ext.EXPERIMENT_ID)
    await conn.execute("DELETE FROM bettor_desk_accounts WHERE account_id=$1",
                       ACCT)
    await conn.execute("DELETE FROM ingestion_state WHERE key = ANY($1)",
                       _STATE_KEYS + [ON.RECONCILIATION_KEY])
    await conn.execute("DELETE FROM us_premap WHERE event_slug=$1", EVENT)
    await ACM.purge(conn)
    from tests import measured_void_rate as MVR
    await MVR.purge(conn, prefix=VOID_PREFIX)


async def _connect():
    import asyncpg
    return await asyncpg.connect(DSN)


async def start(conn, *, p, qty=10, price=0.50, with_catalogue=True,
                approve_model=True):
    """The account (real writers), the catalogue, the held entry, the
    approved conditional model (which also measures the void rate) -- or,
    without a model, a measured void rate alone -- and HOLD's probability."""
    await clean(conn)
    auth = await authorize(conn)
    if with_catalogue:
        await catalogue(conn)
    await held_position(conn, qty=qty, price=price)
    if approve_model:
        from tests import approved_conditional_model as ACM
        await ACM.approve(conn)
    else:
        from tests import measured_void_rate as MVR
        await MVR.seed(conn, prefix=VOID_PREFIX)
    await probability(conn, p=p)
    return auth


async def run_cycle(conn):
    from sportsassets.workers import ext_pinnacle_loop as L
    return await L.cycle(conn)


def step_of(out, intent_id=HELD_ID):
    pcy = (out.get("funded_servicing") or {}).get("pair_cycle") or {}
    return next((s for s in pcy.get("considered") or []
                 if str(s.get("intent_id")) == str(intent_id)), None)


async def xavier_records(conn, intent_id=HELD_ID):
    return (await XV.history(conn, intent_id=intent_id, limit=50))[
        "decisions"]


async def ledger_row(conn, decision_id):
    row = dict(await conn.fetchrow(
        "SELECT * FROM bettor_funded_decisions WHERE decision_id=$1",
        decision_id))
    for k in ("ranked", "unrankable", "predicted", "features"):
        if isinstance(row.get(k), str):
            row[k] = json.loads(row[k])
    return row


def books(*, held_bids, hedge_bid=None, hedge_depth=500,
          other_side_offer=0.90):
    """The held moneyline's bid ladder, and the run line's book: the NYY
    +1.5 side (SHORT) costs 1 - bid; the BOS -1.5 side (LONG) is offered at
    `other_side_offer` (0.90 -- not the cheap leg; it stays in the
    comparison and loses on its value)."""
    out = {HELD: {"bids": list(held_bids), "offers": [(0.99, 5)]}}
    if hedge_bid is not None:
        out[SIB] = {"bids": [(hedge_bid, hedge_depth)],
                    "offers": [(other_side_offer, 500)]}
    return out


# ════════════════════════════════════════════════════════════════════
# SHARED ASSERTIONS ON THE PERSISTED ROWS
# ════════════════════════════════════════════════════════════════════

def alternative(rec, action, candidate_id=None):
    got = [a for a in rec["alternatives"] if a.get("action") == action
           and (candidate_id is None or a.get("candidate_id") == candidate_id)]
    assert len(got) == 1, (action, candidate_id, rec["alternatives"])
    return got[0]


def assert_whole_position_economics(alt):
    """Every rankable alternative carries the same whole-position fields."""
    for k in ("expected_net_usd", "increment_vs_hold_usd",
              "worst_case_remaining_loss_usd", "capital_required_usd",
              "capital_released_usd", "fees_usd", "execution_uncertainty",
              "unpaired_residual_qty"):
        assert k in alt, (k, alt)
    assert alt["expected_net_usd"] is not None, alt


def assert_sent_the_persisted_plan(venue, rec):
    """THE ORDER THE ADAPTER RECEIVED IS THE PERSISTED PLAN, FIELD FOR FIELD,
    and it was the only order."""
    plan = rec["evidence"]["chosen_plan"]
    assert plan["digest"] == rec["chosen_plan_digest"]
    cr = venue.creates_sent()
    assert len(cr) == 1, venue.sent
    c = cr[0]
    assert c["marketSlug"] == plan["us_market_slug"]
    assert c["intent"] == plan["order_intent"]
    assert int(c["quantity"]) == int(plan["quantity"])
    assert float(c["price"]["value"]) == pytest.approx(plan["limit_price"])
    return plan, c


#: THE HELD MONEYLINE'S BID LADDER in (a)/(b): 4 at 0.62 and 3 at 0.58
#: (both above the 0.50 entry and above HOLD's 0.55 -- a profitable
#: DIRECT_EXIT of 4 and a REDUCE of 7), then 0.40 deep.
PROFIT_LADDER = [(0.62, 4), (0.58, 3), (0.40, 400)]


# ════════════════════════════════════════════════════════════════════
# (a) AN INDIRECT HEDGE BEATS A PROFITABLE DIRECT EXIT
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_a_an_indirect_hedge_beats_a_profitable_direct_exit(monkeypatch):
    """Held: 10 Red Sox moneyline bought at 0.50; HOLD is valued on p = 0.55
    (HOLD +0.50). The book bids 0.62 for 4 and 0.58 for 3 -- a profitable
    DIRECT_EXIT (4 at 0.62) and a REDUCE (7, every level above HOLD's value)
    both exist and are ranked. The Yankees +1.5 run line in the same game
    costs 0.45 with 500 displayed.

    Xavier's persisted decision selects ACQUIRE_INDIRECT_HEDGE over
    DIRECT_EXIT, REDUCE and HOLD on whole-position expected value; the one-
    measure valuation selects the same fixed action at BOTH ends of the
    measured void rate's range, so it is dispatched; and the fake adapter
    receives exactly the bound plan."""
    conn = await _connect()
    try:
        await start(conn, p=0.55)
        venue = Venue(books=books(held_bids=PROFIT_LADDER, hedge_bid=0.55),
                      holdings={HELD: (10.0, 5.0)})
        substitute(monkeypatch, venue)
        out = await run_cycle(conn)
        svc = out["funded_servicing"]
        assert svc and svc["ok"] is True, svc
        step = step_of(out)
        assert step["decision"]["action"] == PC.ACTION_ACQUIRE, step

        # ── XAVIER'S PERSISTED DECISION ──────────────────────────────
        recs = await xavier_records(conn)
        assert len(recs) == 1
        rec = recs[0]
        assert rec["chosen_action"] == "ACQUIRE_HEDGE"
        assert rec["execution_eligibility"] == XV.E_DISPATCHED
        assert rec["responsibility_state"] == XV.HELD
        acq = alternative(rec, FD.ACTION_ACQUIRE_INDIRECT_HEDGE, HEDGE_CID)
        hold = alternative(rec, "HOLD")
        ex = alternative(rec, "DIRECT_EXIT")
        rd = alternative(rec, "REDUCE")
        for a in (acq, hold, ex, rd):
            assert a["rankable"] is True, a
            assert_whole_position_economics(a)
        assert acq["plan_digest"] == rec["chosen_plan_digest"]
        # THE PROFITABLE EXIT IS REAL AND RANKED, AND IT LOSES ON ITS NUMBER
        assert ex["expected_net_usd"] > hold["expected_net_usd"]
        assert rd["expected_net_usd"] > hold["expected_net_usd"]
        assert acq["expected_net_usd"] > max(ex["expected_net_usd"],
                                             rd["expected_net_usd"])
        assert hold["expected_net_usd"] == pytest.approx(10 * 0.55 - 5.0)
        # the acquisition's whole-position economics: hedge capital and fees
        # in, nothing left uncovered, its increment over HOLD stated
        assert acq["capital_required_usd"] == pytest.approx(
            4.50 + acq["fees_usd"], abs=1e-6)
        assert acq["fees_usd"] > 0
        assert acq["unpaired_residual_qty"] == pytest.approx(0.0)
        assert acq["increment_vs_hold_usd"] == pytest.approx(
            acq["expected_net_usd"] - hold["expected_net_usd"], abs=1e-6)
        assert rec["reasoning"]["increment_vs_hold_usd"] == pytest.approx(
            acq["increment_vs_hold_usd"])
        assert rec["evidence"]["region_probabilities_came_from"].startswith(
            "APPROVED_DISTRIBUTION:%s@" % PC.FMD.KEY_HEDGE_GIVEN_PRIMARY)

        # ── THE FUNDED DECISIONS LEDGER: ONE COMPARISON, ALL FOUR ────
        led = await ledger_row(conn, rec["decision_id"])
        assert led["action"] == "ACQUIRE_HEDGE"
        ranked = {c["action"] for c in led["ranked"]}
        assert {"HOLD", "DIRECT_EXIT", "REDUCE",
                FD.ACTION_ACQUIRE_INDIRECT_HEDGE} <= ranked, led["ranked"]
        basis = led["predicted"]["distribution_basis"]
        assert basis["void"]["used"] is True
        assert basis["primary"]["p_win"] == 0.55

        # ── THE ONE-MEASURE GATE: ROBUST OVER THE MEASURED RANGE ─────
        cv = step["common_valuation"]
        assert cv["selection_basis"] == "ROBUST_ACROSS_THE_VOID_RATE_RANGE"
        assert cv["void_rate_status"] == "MEASURED"
        lo, hi = cv["void_range"]
        assert lo == 0.0 and 0.0 < hi < 0.2
        assert cv["winner_at_range_low"] == cv["winner_at_range_high"]
        assert cv["winner_at_range_low"][:2] == [
            "ACQUIRE_INDIRECT_HEDGE", HEDGE_CID]
        by = {tuple(v["fixed_action"][:2]): v for v in cv["valued"]}
        w = by[("ACQUIRE_INDIRECT_HEDGE", HEDGE_CID)]
        for k, v in by.items():
            if k != ("ACQUIRE_INDIRECT_HEDGE", HEDGE_CID) and v["rankable"]:
                assert w["value_at_range_low"] > v["value_at_range_low"], k
                assert w["value_at_range_high"] > v["value_at_range_high"], k
        assert step["funded_dispatch_gate"]["permitted"] is True

        # ── EXACTLY THE BOUND PLAN WAS SENT ──────────────────────────
        plan, c = assert_sent_the_persisted_plan(venue, rec)
        assert plan["kind"] == "ACQUISITION"
        assert c["marketSlug"] == SIB
        assert c["intent"] == "ORDER_INTENT_BUY_SHORT"
        assert float(c["price"]["value"]) == pytest.approx(0.55)   # 1 - 0.45
        assert int(c["quantity"]) == 10
        # THE HEDGE INTENT NAMES THE DECISION AND THE PLAN IT EXECUTES
        h = await conn.fetchrow(
            "SELECT intent_id, decision_ref, payout_event, state, leg_role "
            "  FROM bettor_funded_intents WHERE account_id=$1 "
            "   AND leg_role='HEDGE'", ACCT)
        ref = json.loads(h["decision_ref"])
        assert ref["decision_id"] == rec["decision_id"]
        assert ref["plan_digest"] == rec["chosen_plan_digest"]
        assert ref["xavier_decision_id"] == rec["xavier_decision_id"]
        assert h["payout_event"] and h["state"] == "FILLED"
        ev = await XV.execution_events(
            conn, xavier_decision_id=rec["xavier_decision_id"])
        assert ev[0]["event_kind"] == XV.K_CLAIMED
        assert ev[0]["plan_digest"] == rec["chosen_plan_digest"]
    finally:
        await clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_counterpart_a_choice_that_flips_within_the_void_range_sends_nothing(
        monkeypatch):
    """THE NON-ROBUST CASE. Held 10 at 0.80; HOLD is valued on p = 0.40;
    the book bids 0.43. A loss-taking exit beats HOLD if the fixture never
    voids (0.43 less fees > 0.40) -- but a void REFUNDS THE 0.80 BASIS, so at
    the measured rate's upper bound HOLD is worth more than the exit. The
    ranking (which values HOLD on P(win) alone) selects the exit; the
    one-measure valuation sees the choice change within the range, and
    NOTHING IS SENT. The refusal is on the step and the record."""
    conn = await _connect()
    try:
        await start(conn, p=0.40, price=0.80)
        venue = Venue(books=books(held_bids=[(0.43, 400)]),
                      holdings={HELD: (10.0, 8.0)})
        substitute(monkeypatch, venue)
        out = await run_cycle(conn)
        step = step_of(out)
        assert step["decision"]["action"] in ("EXIT", "REDUCE"), step
        assert step["refusal"] == \
            "THE_SELECTED_ACTION_CHANGES_WITHIN_THE_VOID_RATES_UNCERTAINTY"
        cv = step["common_valuation"]
        assert cv["selection_basis"] == \
            "CONDITIONAL_RESEARCH_VALUATION_NOT_FOR_FUNDED_DISPATCH"
        assert cv["winner_at_range_low"][0] in ("DIRECT_EXIT", "REDUCE")
        assert cv["winner_at_range_high"][0] == "HOLD"
        assert venue.creates_sent() == []
        rec = (await xavier_records(conn))[0]
        assert rec["chosen_action"] in ("EXIT", "REDUCE")
        # THE RECORD NAMES THE GATE THAT STOPPED IT (it used to say
        # DISPATCHED: the gate was wired after the record).
        assert rec["execution_eligibility"] == "%s:%s:%s" % (
            XV.E_BLOCKED, XV.G_COMMON_VALUATION,
            "THE_SELECTED_ACTION_CHANGES_WITHIN_THE_VOID_RATES_UNCERTAINTY")
        assert rec["reasoning"]["eligibility"]["underlying"][
            "eligibility"] == XV.E_DISPATCHED
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_intents WHERE account_id=$1"
            " AND kind='EXIT'", ACCT) == 0
        ev = await XV.execution_events(
            conn, xavier_decision_id=rec["xavier_decision_id"])
        assert [e for e in ev if e["event_kind"] == XV.K_CLAIMED] == []
    finally:
        await clean(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# (b) HOLD BEATS AN UNATTRACTIVE HEDGE
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_b_hold_beats_an_unattractive_hedge(monkeypatch):
    """The same position (10 at 0.50, HOLD on p = 0.55), the book bids 0.52
    (an exit barely above the basis, worth less than holding), and the
    Yankees +1.5 now costs 0.80: its whole-position increment over HOLD is
    NEGATIVE. HOLD is selected; the hedge is on the record with its negative
    increment; no order is sent."""
    conn = await _connect()
    try:
        await start(conn, p=0.55)
        venue = Venue(books=books(held_bids=[(0.52, 400)], hedge_bid=0.20),
                      holdings={HELD: (10.0, 5.0)})
        substitute(monkeypatch, venue)
        out = await run_cycle(conn)
        step = step_of(out)
        assert step["decision"]["action"] == PC.ACTION_HOLD, step
        rec = (await xavier_records(conn))[0]
        assert rec["chosen_action"] == "HOLD"
        assert rec["execution_eligibility"] == XV.E_HOLD
        assert rec["chosen_plan_digest"] is None
        acq = alternative(rec, FD.ACTION_ACQUIRE_INDIRECT_HEDGE, HEDGE_CID)
        hold = alternative(rec, "HOLD")
        assert acq["rankable"] is True
        assert_whole_position_economics(acq)
        # THE HEDGE IS VISIBLE WITH ITS NEGATIVE INCREMENT
        assert acq["increment_vs_hold_usd"] < 0, acq
        assert acq["expected_net_usd"] < hold["expected_net_usd"]
        assert acq["capital_required_usd"] == pytest.approx(
            8.00 + acq["fees_usd"], abs=1e-6)
        led = await ledger_row(conn, rec["decision_id"])
        assert led["action"] == "HOLD"
        assert FD.ACTION_ACQUIRE_INDIRECT_HEDGE in {
            c["action"] for c in led["ranked"]}
        # NOTHING WAS SENT OR RESERVED
        assert venue.creates_sent() == []
        assert [k for k, _ in venue.sent if k == "preview"] == []
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_intents WHERE account_id=$1"
            " AND intent_id <> $2", ACCT, HELD_ID) == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_leg_reservations WHERE "
            " group_id=$1", "grp:" + HELD_ID) == 0
    finally:
        await clean(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# (c) A LOSS-TAKING REDUCTION IMPROVES THE FORWARD OUTCOME
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_c_a_loss_taking_reduction_is_chosen_and_dispatched(monkeypatch):
    """Held 10 at 0.60; the forward probability is now 0.30 (HOLD -3.00).
    The book bids 0.45 for 4 and 0.42 for 3, then 0.10: every level above
    HOLD's value is sold -- a REDUCE of 7 AT A REALISED LOSS -- and it is
    worth more than holding them. The void rate is measured (the void
    refunds the 0.60 basis, so HOLD's value rises with it; at the measured
    upper bound REDUCE still wins). The REDUCE is dispatched with its bound
    plan (the REDUCE-dispatch defect, fixed, on the scheduled path)."""
    conn = await _connect()
    try:
        await start(conn, p=0.30, price=0.60, approve_model=False)
        venue = Venue(books=books(held_bids=[(0.45, 4), (0.42, 3),
                                             (0.10, 400)]),
                      holdings={HELD: (10.0, 6.0)})
        substitute(monkeypatch, venue)
        out = await run_cycle(conn)
        step = step_of(out)
        assert step["decision"]["action"] == "REDUCE", step
        rec = (await xavier_records(conn))[0]
        assert rec["chosen_action"] == "REDUCE"
        assert rec["execution_eligibility"] == XV.E_DISPATCHED
        rd = alternative(rec, "REDUCE")
        hold = alternative(rec, "HOLD")
        assert_whole_position_economics(rd)
        assert rd["plan_digest"] == rec["chosen_plan_digest"]
        # FORWARD VALUE, NOT HISTORY: the reduction realises a loss on the
        # 0.60 basis and is still worth more than holding those contracts
        assert rd["expected_net_usd"] > hold["expected_net_usd"]
        assert hold["expected_net_usd"] == pytest.approx(10 * 0.30 - 6.0)
        assert "history_is_not_a_reason" in rec["reasoning"]
        cv = step["common_valuation"]
        assert cv["selection_basis"] == "ROBUST_ACROSS_THE_VOID_RATE_RANGE"
        assert cv["winner_at_range_low"][:3] == ["REDUCE", "REDUCE", 7.0]
        assert cv["winner_at_range_high"][:3] == ["REDUCE", "REDUCE", 7.0]
        # DISPATCHED WITH ITS BOUND PLAN
        plan, c = assert_sent_the_persisted_plan(venue, rec)
        assert plan["kind"] == "EXIT" and plan["action"] == "REDUCE"
        assert c["intent"] == "ORDER_INTENT_SELL_LONG"
        assert int(c["quantity"]) == 7
        pcx = out["funded_servicing"]["pair_cycle"]["exits"][0]
        assert pcx["submitted"] is True and pcx["order_binding"]["ok"] is True
        ex = await conn.fetchrow(
            "SELECT intent_id, decision_ref, quantity FROM "
            " bettor_funded_intents WHERE parent_intent_id=$1 AND "
            " kind='EXIT'", HELD_ID)
        ref = json.loads(ex["decision_ref"])
        assert ref["decision_id"] == rec["decision_id"]
        assert ref["plan_digest"] == rec["chosen_plan_digest"]
        assert ref["action"] == "REDUCE"
        # THE LOSS IS REALISED AND BOOKED; 3 CONTRACTS REMAIN HELD
        pos = [p_ for p_ in await FB.open_entry_positions(
            conn, account_id=ACCT, venue=VENUE)
            if p_["intent_id"] == HELD_ID][0]
        assert float(pos["residual_qty"]) == pytest.approx(3.0)
        pl = await FB.pnl(conn, account_id=ACCT, venue=VENUE)
        assert pl["exit_proceeds_usd"] == pytest.approx(4 * 0.45 + 3 * 0.42)
        assert pl["realised_pnl_usd"] < 0, pl
    finally:
        await clean(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# (d) A PARTIAL HEDGE IS VALUED AND MANAGED CORRECTLY
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_d_a_partial_hedge_is_valued_whole_and_managed_as_one_group(
        monkeypatch):
    """The Yankees +1.5 book shows 6 at 0.45 against 10 held. Cycle 1: the
    6-contract hedge is valued on the WHOLE position with 4 uncovered (never
    the matched slice's floor as the position's floor), wins, and fills 6.
    Cycle 2: Xavier's record for the group shows matched 6 / unpaired 4 and
    ONE group decision; no second hedge leg is opened."""
    conn = await _connect()
    try:
        await start(conn, p=0.55)
        venue = Venue(books=books(held_bids=PROFIT_LADDER, hedge_bid=0.55,
                                  hedge_depth=6),
                      holdings={HELD: (10.0, 5.0)})
        substitute(monkeypatch, venue)
        one = await run_cycle(conn)
        s1 = step_of(one)
        assert s1["decision"]["action"] == PC.ACTION_ACQUIRE, s1
        rec1 = (await xavier_records(conn))[0]
        acq = alternative(rec1, FD.ACTION_ACQUIRE_INDIRECT_HEDGE, HEDGE_CID)
        # VALUED WITH THE 4 IT DOES NOT COVER
        assert acq["unpaired_residual_qty"] == pytest.approx(4.0), acq
        assert acq["unpaired_value_at_risk_usd"] == pytest.approx(4 * 0.50)
        row = next(r for r in s1["hedge_candidate_ranking"]["ranked"]
                   if r["condition_id"] == HEDGE_CID)
        assert row["covered_qty"] == pytest.approx(6.0)
        assert row["uncovered_qty"] == pytest.approx(4.0)
        # the position's worst case is NOT the matched slice's floor: the
        # 4 uncovered contracts can lose their basis
        assert acq["worst_case_remaining_loss_usd"] > \
            -row["matched_slice_usd"] + 1.0, (acq, row["matched_slice_usd"])
        plan, c = assert_sent_the_persisted_plan(venue, rec1)
        assert int(c["quantity"]) == 6 and c["marketSlug"] == SIB
        hedge = await conn.fetchrow(
            "SELECT intent_id, residual_qty::float8 AS r FROM "
            " bettor_funded_intents WHERE account_id=$1 AND "
            " leg_role='HEDGE'", ACCT)
        assert hedge["r"] == pytest.approx(6.0)

        # ── CYCLE 2: ONE GROUP DECISION ──────────────────────────────
        before = len(venue.creates_sent())
        two = await run_cycle(conn)
        s2 = step_of(two)
        g = s2["group"]
        assert g["matched_units"] == pytest.approx(6.0)
        assert g["unpaired_qty"] == pytest.approx(4.0)
        assert g["unpaired_role"] == "PRIMARY"
        assert s2["acquisition_ineligible"] == \
            XV.R_GROUP_ALREADY_HOLDS_A_HEDGE_LEG
        sh = step_of(two, hedge["intent_id"])
        assert sh["refusal"] == XV.R_DECIDED_WITH_THE_GROUP
        rec2 = (await xavier_records(conn))[0]
        assert rec2["xavier_decision_id"] != rec1["xavier_decision_id"]
        rg = rec2["residual_exposure"]["group"]
        assert rg["matched_units"] == pytest.approx(6.0)
        assert rg["unpaired_qty"] == pytest.approx(4.0)
        hrec = (await xavier_records(conn, hedge["intent_id"]))[0]
        assert hrec["execution_eligibility"] == XV.E_DECIDED_BY_GROUP
        # NO SECOND HEDGE LEG: nothing new on the run line
        assert all(c_["marketSlug"] != SIB
                   for c_ in venue.creates_sent()[before:])
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_intents WHERE account_id=$1"
            " AND leg_role='HEDGE'", ACCT) == 1
        # the unpaired remainder is decided on its merits: whatever the group
        # decision is, it is one decision with its alternatives on the record
        assert rec2["chosen_action"] is not None or rec2["alternatives"]
    finally:
        await clean(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# (e) RESTART AND A LOST ACKNOWLEDGEMENT PRODUCE NO DUPLICATE ORDER
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_e_a_lost_answer_and_a_restart_send_nothing_twice(monkeypatch):
    """The winning hedge's send raises -- the venue's answer never arrives.
    The intent is UNRESOLVED with its exposure counted. THE RESTART: the
    connection is closed and every process-local cache dropped. The next
    cycle sends no second order for the position, Xavier records
    ORDER_UNRESOLVED with the exact blocker, and the lost-acknowledgement
    investigation is open."""
    conn = await _connect()
    try:
        await start(conn, p=0.55)
        venue = Venue(books=books(held_bids=PROFIT_LADDER, hedge_bid=0.55),
                      holdings={HELD: (10.0, 5.0)})
        venue.raise_on_create = TimeoutError("the answer never came back")
        substitute(monkeypatch, venue)
        one = await run_cycle(conn)
        s1 = step_of(one)
        assert s1["decision"]["action"] == PC.ACTION_ACQUIRE, s1
        assert len(venue.creates_sent()) == 1
        assert s1["acquisition"]["refusal"] == FX.R_LOST_ACKNOWLEDGEMENT
        hedge = await conn.fetchrow(
            "SELECT intent_id, state FROM bettor_funded_intents WHERE "
            " account_id=$1 AND leg_role='HEDGE'", ACCT)
        assert hedge["state"] == "UNRESOLVED"
        exp = await FB.exposure(conn, account_id=ACCT, venue=VENUE)
        assert any(r["us_market_slug"] == SIB
                   for r in exp["outstanding_orders"]), exp
        rec1 = (await xavier_records(conn))[0]
        assert (await XV.execution_state(
            conn, xavier_decision_id=rec1["xavier_decision_id"]))[
            "status"] == XV.X_UNRESOLVED

        # ── THE RESTART ──────────────────────────────────────────────
        await conn.close()
        reset_process_state()
        conn = await _connect()
        venue.raise_on_create = None          # the venue answers again
        two = await run_cycle(conn)
        assert len(venue.creates_sent()) == 1, venue.sent  # NOTHING RESENT
        assert two["funded_servicing"]["pair_cycle"][
            "resubmitted_anything"] is False
        rec2 = (await xavier_records(conn))[0]
        assert rec2["xavier_decision_id"] != rec1["xavier_decision_id"]
        assert rec2["responsibility_state"] == XV.ORDER_UNRESOLVED
        names = {o["obligation"] for o in rec2["obligations"]}
        assert XV.OB_CLAIM_UNRESOLVED in names, rec2["obligations"]
        assert XV.OB_DISPATCH_UNRESOLVED in names, rec2["obligations"]
        s2 = step_of(two)
        if rec2["chosen_action"] not in (None, "HOLD"):
            # whatever now wins is gated, by name, until the order resolves
            assert s2["refusal"] == XV.R_GROUP_ORDER_IN_FLIGHT, s2
            assert rec2["execution_eligibility"].startswith(
                "%s:%s" % (XV.E_BLOCKED, XV.G_GROUP_ORDER_IN_FLIGHT))
        # THE INVESTIGATION IS OPEN for the send nobody answered
        inv = await conn.fetchrow(
            "SELECT state, intent_id FROM bettor_funded_investigations "
            " WHERE intent_id=$1", hedge["intent_id"])
        assert inv is not None and inv["state"] == "OPEN", inv
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_intents WHERE account_id=$1"
            " AND leg_role='HEDGE'", ACCT) == 1
    finally:
        await clean(conn)
        await conn.close()
