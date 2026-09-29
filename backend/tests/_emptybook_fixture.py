"""THE SHARED FIXTURE FOR THE EMPTY-BOOK LIFECYCLE PROOF.

Kept in its own module so the proof and any diagnostic run build exactly the
same world. Nothing here decides anything: it writes the catalogue through the
premap worker's own writer, stands up a venue and an odds provider at their
transport boundaries, and names every input it supplies that production does
not have.

WHAT IS SUBSTITUTED -- external services only:

  * the odds provider's HTTP (`fetch_sport_catalogue`, `fetch_odds`,
    `fetch_scores`) -- it has no lower seam in-process;
  * the league's schedule HTTP (`_fetch_schedule_blocking`), so the REAL scope
    acquisition writes `fixture_metadata` from the league's own payload;
  * the venue, at `pmus._get_client`: its listing (settlement prose), its book,
    and its order surface;
  * `pmus._gate.authorize`, the adapter's venue-boundary gate, as every
    lifecycle test does.

WHAT IS SUPPLIED THAT PRODUCTION DOES NOT HAVE -- named, not hidden:

  1. BOOK CURRENCY (external evidence). `book_currency_evidence` is the one seam
     through which a freshness mechanism reaches either lane, and in production
     it supplies none: the venue documents no timing contract for market data
     (bettor_stream_currency P5) and no resynchronisation procedure (P6). This
     fixture supplies a live-subscription reading, which is the shape a
     qualifying M1 mechanism would produce. It is the assumption the proof runs
     under, not something the proof establishes.
  2. CALIBRATION (external evidence). A measurement of the odds source against
     resolved outcomes; production has none, and MODEL_TRUST_DRIFT blocks. The
     row carries the current evaluator (the gate counts nothing else) and
     `supplied_by: TEST_FIXTURE`.
  3. ACTIVATION (owner input). An account row, owner-approved limits, an
     authorization and the account binding, for a DEMONSTRATION account. In
     production all three activation records are absent (research-sql run 271).
  4. THE THREE SUBMISSION SWITCHES, turned on in-process. Off in shipped code.
"""

from __future__ import annotations

import datetime as _dt
import json
import time

from sportsassets import bettor_entry_execution as EX
from sportsassets import bettor_entry_inventory as inv
from sportsassets import bettor_external_shadow as ext
from sportsassets import bettor_funded_activation as FA
from sportsassets import bettor_funded_execution as FX
from sportsassets import bettor_pinnacle_devig as devig
from sportsassets.workers import ext_pinnacle_loop as loop
from sportsassets.workers import premap as pm

#: THE FOUR INPUTS THIS PROOF SUPPLIES THAT PRODUCTION DOES NOT HAVE, each with
#: the production check that refuses without it. Machine-read by
#: `test_every_supplied_assumption_is_named_and_production_refuses_without_it`,
#: so an input cannot be supplied here without being declared, and a declared
#: one cannot quietly stop being refused in production.
SUPPLIED_ASSUMPTIONS = (
    {"name": "BOOK_CURRENCY", "kind": "EXTERNAL_EVIDENCE",
     "supplied_as": "a live-subscription reading through "
                    "ext_pinnacle_loop.book_currency_evidence",
     "production": "no establishing mechanism: the venue publishes no timing "
                   "contract (bettor_stream_currency P5)",
     "refused_by": "ext_pinnacle_loop.venue_quote -> "
                   "VENUE_BOOK_CURRENCY_NOT_ESTABLISHED"},
    {"name": "CALIBRATION", "kind": "EXTERNAL_EVIDENCE",
     "supplied_as": "one external_source_calibration row, supplied_by "
                    "TEST_FIXTURE",
     "production": "no current-evaluator measurement exists",
     "refused_by": "ext_pinnacle_loop.source_calibration -> measured False, "
                   "so MODEL_TRUST_DRIFT is not evaluable and blocks"},
    {"name": "ACTIVATION", "kind": "OWNER_INPUT",
     "supplied_as": "a DEMONSTRATION account row, approved limits, an "
                    "authorization and the account binding",
     "production": "none of the activation records exists",
     "refused_by": "ext_pinnacle_loop._funded_attempt / _funded_service "
                   "return None without the binding; authorize() refuses "
                   "without limits and authorization"},
    {"name": "SUBMISSION_SWITCHES", "kind": "CODE_CONSTANTS",
     "supplied_as": "the three switches turned on in-process by monkeypatch",
     "production": "all three False in shipped code",
     "refused_by": "bettor_funded_execution.FUNDED_SUBMISSION_ENABLED, "
                   "bettor_entry_execution.REAL_ORDER_SUBMISSION_ENABLED, "
                   "bettor_funded_management.FUNDED_EXIT_SUBMISSION_ENABLED"},
)

GAME = "2026-10-02"
EVENT_SLUG = "mlb-sea-hou-%s" % GAME
GLOBAL_SLUG = EVENT_SLUG
US_SLUG = "aec-mlb-sea-hou-%s" % GAME
CONDITION = "c-emptybook-sea-hou"
ODDS_EVENT = "odds-emptybook-sea-hou"
GAME_PK = 990001
HOME, AWAY = "Houston Astros", "Seattle Mariners"
SPORTS_TYPE = "baseball_team_full_game_winner"
ACCT = "acct-funded-DEMONSTRATION-emptybook"
VENUE = "PMUS"
LIMITS = {"capital_usd": 400, "per_order_usd": 60, "event_exposure_usd": 60,
          "max_exposure_usd": 200, "daily_loss_stop_usd": 40}

#: Venue prose agreeing with Pinnacle's captured pre-game baseball terms on
#: every applicable terminal condition. The same text the entry-lane proof
#: uses; the comparison against Pinnacle's terms is production code.
VENUE_PROSE = (
    "This market settles on the final result of the game, including "
    "any extra innings. A game completed in regulation settles on "
    "the final score. If the game is called (ended) after at least "
    "five innings the market settles on the score at the end of the "
    "last completed inning, unless it is called in the bottom half "
    "and the home team has taken the lead, in which case the actual "
    "score is used. If the game is stopped before five innings the "
    "market is void and stakes are returned. If the game is "
    "suspended and resumed within the window it settles on the "
    "final score. If the game is suspended more than the window it "
    "settles on the score at the end of the last completed inning. "
    "If the game is abandoned or postponed and never completed the "
    "market is void and stakes are returned.")

#: The Astros ask ladder -- the side a BUY_LONG acquires.
OFFERS = [(0.62, 400), (0.64, 300), (0.66, 200)]
BIDS = [(0.60, 400), (0.58, 300)]


def _iso(t):
    return (_dt.datetime.fromtimestamp(t, _dt.timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z")


def venue_event():
    """The venue's own event payload, in the SDK shape `_market_rows` reads."""
    side = {"identifier": US_SLUG}
    return {"slug": EVENT_SLUG, "title": "%s vs. %s" % (AWAY, HOME),
            "markets": [{
                "slug": US_SLUG, "question": "%s vs. %s" % (AWAY, HOME),
                "sportsMarketType": SPORTS_TYPE,
                "gameStartTime": "%sT23:10:00Z" % GAME, "closed": False,
                "marketSides": [
                    dict(side, description="Mariners", long=False,
                         team={"abbreviation": "sea", "name": "Mariners",
                               "safeName": "Seattle", "league": "mlb",
                               "id": 136}),
                    dict(side, description="Astros", long=True,
                         team={"abbreviation": "hou", "name": "Astros",
                               "safeName": "Houston", "league": "mlb",
                               "id": 117})]}]}


def league_schedule(state="Pre-Game"):
    """The league's own schedule payload, in the shape `parse_games` reads."""
    return {"dates": [{"date": GAME, "games": [{
        "gamePk": GAME_PK, "gameType": "R", "scheduledInnings": 9,
        "doubleHeader": "N", "gameNumber": 1, "officialDate": GAME,
        "gameDate": "%sT23:10:00Z" % GAME,
        "status": {"detailedState": state, "abstractGameState": "Preview",
                   "codedGameState": "P"},
        "teams": {"home": {"team": {"name": HOME}},
                  "away": {"team": {"name": AWAY}}}}]}]}


def odds_event(at, *, home_price=1.36, away_price=3.55, stamp_age_s=2.0):
    stamp = _dt.datetime.fromtimestamp(at - stamp_age_s, _dt.timezone.utc
                                       ).strftime("%Y-%m-%dT%H:%M:%SZ")
    prices = [{"name": HOME, "price": home_price},
              {"name": AWAY, "price": away_price}]
    return {"id": ODDS_EVENT, "home_team": HOME, "away_team": AWAY,
            "commence_time": "%sT23:10:00Z" % GAME,
            "bookmakers": [
                {"key": "pinnacle", "last_update": stamp,
                 "markets": [{"key": "h2h", "last_update": stamp,
                              "outcomes": prices}]},
                {"key": "smarkets", "last_update": stamp,
                 "markets": [{"key": "h2h", "outcomes": prices}]}]}


# ── THE VENUE, AT ITS TRANSPORT BOUNDARY ────────────────────────────

class _Markets:
    def __init__(self, venue):
        self.v = venue

    def list(self, params=None):
        slugs = list((params or {}).get("slug") or [])
        self.v.sent.append(("markets.list", slugs))
        return {"markets": [{"slug": s, "description": VENUE_PROSE,
                             "sportsMarketType": SPORTS_TYPE}
                            for s in slugs if s == US_SLUG]}

    def book(self, slug):
        self.v.sent.append(("markets.book", slug))
        if slug != US_SLUG:
            return {}
        lvl = lambda p, q: {"px": {"value": "%.2f" % p, "currency": "USD"},
                            "qty": str(q)}
        return {"marketData": {
            "offers": [lvl(p, q) for p, q in self.v.offers],
            "bids": [lvl(p, q) for p, q in self.v.bids],
            "transactTime": _iso(time.time() - 2.0)}}

    def retrieve_by_slug(self, slug):
        return {"market": {"slug": slug, "marketSides": [
            {"identifier": slug, "description": "Mariners", "long": False},
            {"identifier": slug, "description": "Astros", "long": True}]}}


class _Orders:
    def __init__(self, venue):
        self.v = venue

    def preview(self, body):
        req = (body or {}).get("request") or {}
        self.v.sent.append(("preview", req))
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
        """THE VENUE'S MATCHING, FAITHFUL TO THE ORDER'S TIME IN FORCE.

        FILL_OR_KILL fills the whole quantity or nothing -- a venue cannot
        partially fill it, and an earlier version of this fake did, which
        broke the venue's contract rather than testing ours. The fill may still
        arrive as SEVERAL executions (`split`), each with its own venue fill
        id, walking the book level by level; that is the partial-fill shape a
        FOK order really produces. IMMEDIATE_OR_CANCEL takes what rests at or
        inside the limit and cancels the rest.
        """
        self.v.sent.append(("create", dict(params)))
        self.v.creates += 1
        limit = float((params.get("price") or {}).get("value") or 0)
        qty = int(params.get("quantity") or 0)
        tif = str(params.get("tif") or "")
        intent = params.get("intent")
        is_long = intent == "ORDER_INTENT_BUY_LONG"
        is_sell_long = intent == "ORDER_INTENT_SELL_LONG"
        # What rests at or inside the limit, best level first. A BUY_LONG lifts
        # the offers at or below its limit; a SELL_LONG -- closing a long --
        # hits the bids at or above its limit, which is its MINIMUM price.
        if is_long:
            levels = [(p, q) for p, q in self.v.offers if p <= limit + 1e-9]
        elif is_sell_long:
            levels = sorted(((p, q) for p, q in self.v.bids
                             if p >= limit - 1e-9), reverse=True)
        else:
            levels = []
        available = sum(q for _, q in levels)
        if self.v.depth_cap is not None:
            available = min(available, self.v.depth_cap)
        if "FILL_OR_KILL" in tif and available < qty:
            fills = []
        else:
            want = min(qty, available)
            fills, left = [], want
            for p, q in levels:
                if left <= 0:
                    break
                take = min(q, left)
                fills.append((p, take))
                left -= take
        # SPLIT THE MATCH INTO SEVERAL EXECUTIONS where asked, each at a price
        # the book actually offered.
        execs, n = [], 0
        for p, take in fills:
            parts = self.v.split or [take]
            done = 0
            for part in parts:
                part = min(part, take - done)
                if part <= 0:
                    break
                n += 1
                execs.append({"id": "vf-emptybook-%d-%d" % (self.v.creates, n),
                              "type": "EXECUTION_TYPE_FILL",
                              "lastPx": {"value": "%.2f" % p,
                                         "currency": "USD"},
                              "lastShares": part,
                              "order": {"state": "ORDER_STATE_PARTIALLY_FILLED"}})
                done += part
            if done < take:
                n += 1
                execs.append({"id": "vf-emptybook-%d-%d" % (self.v.creates, n),
                              "type": "EXECUTION_TYPE_FILL",
                              "lastPx": {"value": "%.2f" % p,
                                         "currency": "USD"},
                              "lastShares": take - done,
                              "order": {"state": "ORDER_STATE_PARTIALLY_FILLED"}})
        filled = sum(e["lastShares"] for e in execs)
        if execs:
            execs[-1]["order"]["state"] = ("ORDER_STATE_FILLED"
                                           if filled >= qty else
                                           "ORDER_STATE_CANCELED")
        oid = "venue-emptybook-%d" % self.v.creates
        vwap = (sum(float(e["lastPx"]["value"]) * e["lastShares"]
                    for e in execs) / filled) if filled else limit
        self.v.orders[oid] = {"qty": qty, "filled": filled, "px": vwap,
                              "limit": limit, "intent": params.get("intent"),
                              "killed": filled == 0,
                              "executions": [dict(e) for e in execs]}
        return {"id": oid, "executions": execs,
                **({} if execs else {"order": {"state":
                                               "ORDER_STATE_CANCELED"}})}

    def list(self, params=None):
        self.v.sent.append(("orders.list", dict(params or {})))
        return {"orders": []}

    def retrieve(self, order_id):
        """The venue's own record of an order it received, in the
        GetOrderResponse shape `pmus.order_status` reads; nothing for an order
        it never saw -- which is the case recovery must not read as absence."""
        self.v.sent.append(("orders.retrieve", order_id))
        o = self.v.orders.get(str(order_id))
        if o is None:
            return None
        state = ("ORDER_STATE_FILLED" if o["filled"] >= o["qty"] else
                 "ORDER_STATE_CANCELED")
        return {"order": {
            "id": order_id, "marketSlug": US_SLUG, "intent": o["intent"],
            "price": {"value": "%.2f" % o["limit"], "currency": "USD"},
            "quantity": o["qty"], "cumQuantity": o["filled"],
            "leavesQuantity": 0, "state": state,
            "avgPx": {"value": "%.4f" % o["px"], "currency": "USD"}},
            "executions": list(o["executions"])}

    def cancel(self, order_id, body=None):
        self.v.sent.append(("orders.cancel", order_id))
        return {}


class _Portfolio:
    """The account's positions, derived from the fills THIS venue executed --
    so the account read the execution gate consumes agrees with what was
    actually sent, and an empty book reads as empty."""

    def __init__(self, venue):
        self.v = venue

    def positions(self, params=None):
        self.v.sent.append(("portfolio.positions", dict(params or {})))
        book: dict = {}
        for o in self.v.orders.values():
            if not o["filled"]:
                continue
            p = book.setdefault(US_SLUG, {"netPosition": 0.0, "cost": 0.0,
                                          "baseCost": 0.0})
            if o["intent"] == "ORDER_INTENT_SELL_LONG":
                # A sale closes long inventory: the net falls and the cost
                # basis falls pro rata with it.
                if p["netPosition"] > 0:
                    per = p["cost"] / p["netPosition"]
                    p["cost"] -= per * o["filled"]
                    p["baseCost"] = p["cost"]
                p["netPosition"] -= o["filled"]
                continue
            sign = 1.0 if o["intent"] == "ORDER_INTENT_BUY_LONG" else -1.0
            cost = (o["px"] if sign > 0 else 1.0 - o["px"]) * o["filled"]
            p["netPosition"] += sign * o["filled"]
            p["cost"] += cost
            p["baseCost"] += cost
        return {"positions": book, "eof": True}


class Venue:
    """One venue, shared by every cycle of a test, so a restart sees the same
    orders the first process sent."""

    def __init__(self, *, split=None, depth_cap=None, offers=None):
        self.sent: list = []
        self.creates = 0
        #: executions per matched level, e.g. [50, 35]; None = one each
        self.split = list(split) if split else None
        #: the depth the venue will actually match, when less than it shows
        self.depth_cap = depth_cap
        self.offers = list(offers or OFFERS)
        self.bids = list(BIDS)
        self.orders: dict = {}
        self.markets = _Markets(self)
        self.orders_api = _Orders(self)
        self.portfolio = _Portfolio(self)

    @property
    def client(self):
        v = self

        class _C:
            markets = v.markets
            orders = v.orders_api
            portfolio = v.portfolio
        return _C()

    def creates_sent(self):
        return [c[1] for c in self.sent if c[0] == "create"]


def substitute(monkeypatch, venue: Venue, *, schedule_state="Pre-Game",
               odds=None):
    """Stand up every external service. Returns nothing it decided."""
    from sportsassets import pmus

    monkeypatch.setenv("EDGE_ODDS_API_KEY", "x" * 32)
    client = venue.client
    monkeypatch.setattr(pmus, "_get_client", lambda: client)
    monkeypatch.setattr(pmus._gate, "authorize", lambda *a, **k: {"ok": True})

    async def fake_catalogue(*, api_key, timeout=20.0):
        return {"ok": True, "status": 200, "sports": [
            {"key": "baseball_mlb", "group": "Baseball", "active": True}]}

    async def fake_odds(sport_key, *, api_key, timeout=20.0):
        now = time.time()
        got = odds(now) if odds else odds_event(now)
        events = ((got if isinstance(got, list) else [got])
                  if sport_key == "baseball_mlb" else [])
        return {"ok": True, "events": events, "received_at": now,
                "credits_used": "1", "credits_remaining": "9"}

    async def fake_scores(sport_key, *, api_key, timeout=20.0):
        return {"ok": True, "events": [], "received_at": time.time()}

    def fake_schedule(date_str):
        return {"ok": True, "url": "substituted-league-schedule",
                "payload": (league_schedule(schedule_state)
                            if date_str == GAME else {"dates": []})}

    monkeypatch.setattr(loop, "fetch_sport_catalogue", fake_catalogue)
    monkeypatch.setattr(loop, "fetch_odds", fake_odds)
    monkeypatch.setattr(loop, "fetch_scores", fake_scores)
    monkeypatch.setattr(loop, "_fetch_schedule_blocking", fake_schedule)

    # ── (1) BOOK CURRENCY: THE NAMED EXTERNAL DEPENDENCY ─────────────
    real_bce = loop.book_currency_evidence

    def supplied_currency(slug=None):
        got = dict(real_bce(slug))
        now = time.time()
        got["subscription"] = {"alive_at": now - 0.5,
                               "last_update_at": now - 1.0}
        got["SUPPLIED_BY_A_TEST"] = (
            "P5/P6 are not documented by the venue; this reading is the "
            "assumption the lifecycle proof runs under")
        return got

    monkeypatch.setattr(loop, "book_currency_evidence", supplied_currency)
    loop.rules_cache_reset()
    # ── (4) THE THREE SWITCHES ────────────────────────────────────────
    monkeypatch.setattr(EX, "REAL_ORDER_SUBMISSION_ENABLED", True)
    monkeypatch.setattr(FX, "FUNDED_SUBMISSION_ENABLED", True)
    from sportsassets import bettor_funded_management as FM
    monkeypatch.setattr(FM, "FUNDED_EXIT_SUBMISSION_ENABLED", True)


# ── THE DATABASE ────────────────────────────────────────────────────

async def seed(conn):
    await pm._ensure_table(conn)
    await conn.execute("DELETE FROM us_premap WHERE event_slug=$1", EVENT_SLUG)
    ev = venue_event()
    keys = pm.event_keys_for(ev["title"], ev["slug"])
    rows = [r for m in ev["markets"] for r in pm._market_rows(ev, m)]
    keys = sorted(set(keys) | pm.venue_kick_keys(rows))
    for r in rows:
        await pm._upsert(conn, r, pm.keys_for_row(keys, r))
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1,'true') "
        "ON CONFLICT (key) DO UPDATE SET value='true'", loop.CONTROL_KEY)
    await conn.execute(
        "INSERT INTO markets (condition_id, title, event_title, slug, sport, "
        " closed, resolved) VALUES ($1,$2,$3,$4,'MLB',false,false) "
        "ON CONFLICT (condition_id) DO UPDATE SET sport='MLB', closed=FALSE, "
        " resolved=FALSE, title=EXCLUDED.title, "
        " event_title=EXCLUDED.event_title, slug=EXCLUDED.slug, "
        " updated_at=now()",
        CONDITION, "Will %s beat %s?" % (AWAY, HOME),
        "%s vs. %s" % (AWAY, HOME), GLOBAL_SLUG)
    await conn.execute(
        "INSERT INTO market_tokens (token_id, condition_id, outcome, "
        " outcome_index) VALUES ($1,$2,$3,0),($4,$2,$5,1) "
        "ON CONFLICT (token_id) DO UPDATE SET outcome=EXCLUDED.outcome, "
        " outcome_index=EXCLUDED.outcome_index",
        "tok-emptybook-hou", CONDITION, HOME, "tok-emptybook-sea", AWAY)
    # (2) CALIBRATION -- supplied, and labelled in its own row.
    await conn.execute(
        "INSERT INTO external_source_calibration (source_version, "
        "measured_at, window_start, window_end, sample_size, metric, "
        "score, tolerance, within_tolerance, measured_by, provenance) "
        "VALUES ($1, now(), now() - interval '30 days', now(), 412, "
        "'BRIER', 0.2104, 0.2400, TRUE, 'EMPTY_BOOK_LIFECYCLE_TEST', "
        "$2::jsonb) "
        "ON CONFLICT (source_version, measured_at) DO NOTHING",
        devig.VERSION,
        # THE EVALUATOR THE GATE REQUIRES, stated beside the label that this
        # row was supplied by a test and never measured in production.
        __import__("json").dumps({
            "evaluator": __import__(
                "sportsassets.bettor_source_calibration",
                fromlist=["VERSION"]).VERSION,
            "supplied_by": "TEST_FIXTURE",
            "note": "supplied by a test, not measured in production"}))
    # (3) ACTIVATION -- a DEMONSTRATION account, approved limits, a live
    # authorization, and the binding the scheduled caller reads.
    await conn.execute("DELETE FROM bettor_desk_accounts WHERE account_id=$1",
                       ACCT)
    await conn.execute(
        "INSERT INTO bettor_desk_accounts (account_id, desk_id, status, "
        " paused, accounting_status, opening_balance, note) VALUES "
        "($1,'desk-emptybook','ACTIVE',false,'RECONCILED',0,"
        " 'DEMONSTRATION account for the empty-book lifecycle proof')", ACCT)
    now = time.time()
    eff = EX.effective_limits(LIMITS)
    for key, value in (
            (FA.LIMITS_KEY, {"proposed": LIMITS, "approved": True,
                             "approved_by": "OWNER_OF_A_TEST"}),
            (FA.AUTHORIZATION_KEY, {
                "account_id": ACCT, "venue": VENUE,
                "venue_class": FA.VENUE_FUNDED, "by": "test", "at": now,
                "expires_at": now + 3600, "revoked": False,
                "effective_limits": eff["effective"],
                "effective_digest": eff["effective_digest"]}),
            (FA.ACCOUNT_KEY, {"account_id": ACCT, "venue": VENUE})):
        await conn.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1,$2) "
            "ON CONFLICT (key) DO UPDATE SET value=$2", key,
            json.dumps(value))


async def clean(conn):
    """Everything this fixture created, and nothing else. Run in `finally`."""
    if await conn.fetchval(
            "SELECT to_regclass('ext_candidate_outcomes') IS NOT NULL"):
        await conn.execute(
            "DELETE FROM ext_candidate_outcomes "
            " WHERE provider_event_id LIKE 'odds-emptybook%'")
    for sql, args in (
            ("DELETE FROM bettor_funded_leg_reservations WHERE group_id IN "
             "(SELECT group_id FROM bettor_funded_portfolio_groups "
             " WHERE account_id=$1)", (ACCT,)),
            ("DELETE FROM bettor_funded_discrepancies WHERE intent_id IN "
             "(SELECT intent_id FROM bettor_funded_intents "
             " WHERE account_id=$1)", (ACCT,)),
            ("DELETE FROM bettor_funded_economics WHERE intent_id IN "
             "(SELECT intent_id FROM bettor_funded_intents "
             " WHERE account_id=$1)", (ACCT,)),
            ("DELETE FROM bettor_funded_fills WHERE intent_id IN "
             "(SELECT intent_id FROM bettor_funded_intents "
             " WHERE account_id=$1)", (ACCT,)),
            ("DELETE FROM bettor_funded_decision_outcomes WHERE decision_id "
             "IN (SELECT decision_id FROM bettor_funded_decisions "
             " WHERE account_id=$1)", (ACCT,)),
            ("DELETE FROM bettor_funded_decisions WHERE account_id=$1",
             (ACCT,)),
            ("DELETE FROM bettor_funded_intents WHERE account_id=$1 "
             " AND kind <> 'ENTRY'", (ACCT,)),
            ("DELETE FROM bettor_funded_intents WHERE account_id=$1", (ACCT,)),
            ("DELETE FROM bettor_funded_portfolio_groups WHERE account_id=$1",
             (ACCT,)),
            ("DELETE FROM rn1x_outcomes WHERE position_id IN (SELECT "
             "position_id FROM rn1x_positions WHERE condition_id=$1)",
             (CONDITION,)),
            ("DELETE FROM rn1x_fills WHERE order_id IN (SELECT order_id FROM "
             "rn1x_orders WHERE position_id IN (SELECT position_id FROM "
             "rn1x_positions WHERE condition_id=$1))", (CONDITION,)),
            ("DELETE FROM rn1x_orders WHERE position_id IN (SELECT "
             "position_id FROM rn1x_positions WHERE condition_id=$1)",
             (CONDITION,)),
            ("DELETE FROM rn1x_decisions WHERE position_id IN (SELECT "
             "position_id FROM rn1x_positions WHERE condition_id=$1)",
             (CONDITION,)),
            ("DELETE FROM rn1x_positions WHERE condition_id=$1", (CONDITION,)),
            ("DELETE FROM external_valuations WHERE condition_id=$1",
             (CONDITION,)),
            ("DELETE FROM fixture_metadata WHERE condition_id=$1",
             (CONDITION,)),
            ("DELETE FROM market_tokens WHERE condition_id=$1", (CONDITION,)),
            ("DELETE FROM markets WHERE condition_id=$1", (CONDITION,)),
            ("DELETE FROM us_premap WHERE event_slug=$1", (EVENT_SLUG,)),
            ("DELETE FROM external_source_calibration WHERE measured_by=$1",
             ("EMPTY_BOOK_LIFECYCLE_TEST",)),
            ("DELETE FROM bettor_desk_accounts WHERE account_id=$1", (ACCT,)),
    ):
        try:
            await conn.execute(sql, *args)
        except Exception:                                      # noqa: BLE001
            pass
    for key in (FA.AUTHORIZATION_KEY, FA.LIMITS_KEY, FA.ACCOUNT_KEY):
        await conn.execute("DELETE FROM ingestion_state WHERE key=$1", key)
