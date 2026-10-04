"""THE ONLY LOADER OF THE PROFITABILITY LAYER. SELECT only, bounded.

Every function here issues SELECTs and nothing else (pinned by
tests/test_profitability_is_research_only.py). Every read has a lookback
window and/or an id list and a row LIMIT. A table that does not exist in
this database is read as absent (and the consumer names that), never as
an error that stops the cycle.

POSITION RECORDS (the shape economics.compute_position consumes):

  {book, position_key, venue, account_id, group_id, us_market_slug,
   holding_side, strategy,
   events:      [{t, kind BUY|SELL, qty, price (cost space, excl. fee),
                  fee_usd, ref}],
   reservation: {t, usd, ref} | None    (PAPER entry order's cash lock)
   settlement:  {t, payout_per_contract, outcome, ref, basis} | None
   contract_settlement: the same contract's settlement (any position) for
                the hold-to-settlement counterfactual
   probability, probability_basis, decision_id, decided_at,
   event_start_at, event_start_basis,
   src: {fill_ids, order_ids, book_obs_ids, settlement_ids, decision_ids,
         intent_ids, mirror_ids, handoff_ids, simulator_versions}}

PAPER: paper_fills (+ paper_orders for the reservation and the decision,
paper_settlements for the payout, latest version). ACTUAL: execmirror_fills
(Polymarket US; LONG-side wire prices converted to cost space by the fill
intent's side) and kalshi_live_fills (the traded leg's own price), each
paid by the paper settlement of the SAME contract (same group, market and
side; else the same market and side) -- the same contract's outcome.
"""
from __future__ import annotations

from ..intel import attribution as AT
from ..intel import common as IC
from ..intel import reads as IR
from . import common as C

MAX_ROWS = 20000
LOOKBACK_DAYS = 90.0
PAPER_OPEN_STATES = ["PENDING_SIMULATION", "RESTING", "PARTIALLY_FILLED",
                     "CANCEL_PENDING"]
PAPER_TERMINAL_STATES = ["FILLED", "EXPIRED", "CANCELED", "REJECTED"]
RATE_DAYS = 14.0
CAPACITY_LOOKBACK_H = 24.0
CAPACITY_MAX_CANDIDATES = 1500


async def has(conn, table: str) -> bool:
    return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL",
                                    table))


def _ep(v):
    return C.epoch(v)


# ═════════════════════════════════════════════════════════════════════
# THE SLEEVES (migration 223's durable classification; migration 227)
# ═════════════════════════════════════════════════════════════════════

R_NO_SLEEVE_SCHEMA = "MIGRATION_223_NOT_APPLIED"
R_NO_DURABLE = "NO_DURABLE_CLASSIFICATION"


async def sleeve_classes(conn, gids) -> dict | None:
    """{group_id: {sleeve, strategy, policy_version, classifier_version,
    basis}} -- each group's CURRENT durable classification
    (paper_sleeve_current_v). None when migration 223 is absent (every
    position then reads UNCLASSIFIED, never INVESTMENT)."""
    if not await has(conn, "paper_sleeve_current_v"):
        return None
    gids = sorted({g for g in gids if g})
    if not gids:
        return {}
    rows = await conn.fetch(
        "SELECT group_id, sleeve, strategy, policy_version, "
        "       classifier_version, basis "
        "  FROM paper_sleeve_current_v WHERE group_id = ANY($1::text[])",
        gids)
    return {r["group_id"]: dict(r) for r in rows}


def attach_sleeve(pos: dict, classes, *, strategy_fallback=False,
                  policy_version=None) -> dict:
    """Stamp a position record with its sleeve, the classification basis,
    the deciding policy version and the classifier version: the group's
    durable migration-223 classification, else UNCLASSIFIED -- for PAPER
    AND ACTUAL alike.

    (R30A review) An ACTUAL position whose group has no durable
    classification used to take the classifier's sleeve of the strategy the
    live lane recorded -- INVESTMENT for the completed-game policy or Derek
    -- while the twin's ladder read the same open ACTUAL positions as
    UNCLASSIFIED. Migration 223's own reason for a group without a durable
    row is NO_DURABLE_CLASSIFICATION, i.e. UNCLASSIFIED, and UNCLASSIFIED
    never counts as INVESTMENT. So it is UNCLASSIFIED everywhere now; with
    `strategy_fallback` (the ACTUAL readers) the strategy map's sleeve is
    kept beside it as `strategy_map_sleeve` -- information, never the
    sleeve."""
    c = (classes or {}).get(pos.get("group_id"))
    extra = {}
    if c is not None:
        sleeve = c["sleeve"] if c["sleeve"] in C.SLEEVES else C.UNCLASSIFIED
        basis = c.get("basis")
        pv = policy_version or c.get("policy_version")
        cv = c.get("classifier_version")
    else:
        sleeve, pv, cv = C.UNCLASSIFIED, policy_version, None
        basis = R_NO_SLEEVE_SCHEMA if classes is None else R_NO_DURABLE
        if strategy_fallback and pos.get("strategy"):
            extra["strategy_map_sleeve"] = C.strategy_sleeve(pos["strategy"])
            extra["strategy_map_role"] = (
                "INFORMATION ONLY: the classifier's sleeve of the strategy "
                "the live lane recorded; without a durable classification "
                "the position is UNCLASSIFIED (never INVESTMENT)")
    pos.update(sleeve=sleeve, sleeve_basis=basis, policy_version=pv,
               classifier_version=cv, **extra)
    return pos


def _sleeve_case_sql(group_col: str, strategy_col: str, *, book_col: str,
                     have_view: bool) -> str:
    """SQL expression: the sleeve of an economics row -- the durable
    classification of its group, else UNCLASSIFIED (PAPER and ACTUAL alike:
    R30A review, see attach_sleeve). `strategy_col` / `book_col` are kept
    for the callers' signature; neither can make a row INVESTMENT."""
    del strategy_col, book_col
    if not have_view:
        return "'UNCLASSIFIED'"
    return ("coalesce((SELECT s.sleeve FROM paper_sleeve_current_v s "
            "           WHERE s.group_id = %s), 'UNCLASSIFIED')" % group_col)


# ═════════════════════════════════════════════════════════════════════
# PAPER
# ═════════════════════════════════════════════════════════════════════

async def paper_positions(conn, *, now, account_id=C.PAPER_ACCOUNT,
                          days=LOOKBACK_DAYS) -> dict:
    cut = float(now) - days * 86400.0
    fills = [dict(r) for r in await conn.fetch(
        "SELECT fill_id, order_id, group_id, role, direction, holding_side, "
        "       us_market_slug, qty, price, fee_usd, book_obs_id, "
        "       simulator_version, strategy, "
        "       extract(epoch FROM filled_at)::float8 AS t "
        "  FROM paper_fills WHERE account_id = $1 AND group_id IN ("
        "       SELECT DISTINCT group_id FROM paper_fills "
        "        WHERE account_id = $1 AND filled_at >= to_timestamp($2)) "
        " ORDER BY filled_at LIMIT $3", account_id, cut, MAX_ROWS)]
    gids = sorted({f["group_id"] for f in fills})
    if not gids:
        return {"positions": [], "group_ids": [], "decisions": {}}
    orders = [dict(r) for r in await conn.fetch(
        "SELECT order_id, group_id, role, direction, holding_side, "
        "       us_market_slug, reserved_usd, state, decision_id, "
        "       extract(epoch FROM created_at)::float8 AS created_at "
        "  FROM paper_orders WHERE account_id = $1 "
        "   AND group_id = ANY($2::text[]) LIMIT $3",
        account_id, gids, MAX_ROWS)]
    setts = [dict(r) for r in await conn.fetch(
        "SELECT settlement_id, position_key, version, group_id, "
        "       us_market_slug, holding_side, qty, outcome, "
        "       payout_per_contract, payout_usd, evidence_source, "
        "       extract(epoch FROM settled_at)::float8 AS t "
        "  FROM paper_settlements WHERE account_id = $1 "
        "   AND group_id = ANY($2::text[]) LIMIT $3",
        account_id, gids, MAX_ROWS)]
    dids = sorted({o["decision_id"] for o in orders if o.get("decision_id")})
    decisions = await decisions_by_id(conn, dids)
    by_pos: dict = {}
    for f in fills:
        k = (f["group_id"], f["us_market_slug"], str(f["holding_side"]))
        by_pos.setdefault(k, []).append(f)
    slugs = sorted({k[1] for k in by_pos})
    cset = await contract_settlements(conn, slugs)
    starts = await event_starts(conn, slugs, gids)
    classes = await sleeve_classes(conn, gids)
    sets_by_key: dict = {}
    for s in setts:
        sets_by_key.setdefault(s["position_key"], []).append(s)
    out = []
    for (g, slug, side), fs in by_pos.items():
        pk = "paperpos:%s:%s:%s:%s" % (account_id, g, slug, side)
        po = [o for o in orders if o["group_id"] == g
              and o["us_market_slug"] == slug
              and str(o["holding_side"]) == side]
        entry = sorted([o for o in po if o["direction"] == "BUY"],
                       key=lambda o: o["created_at"] or 0)
        res = None
        if entry and C.num(entry[0].get("reserved_usd")):
            res = {"t": entry[0]["created_at"],
                   "usd": C.num(entry[0]["reserved_usd"]),
                   "ref": entry[0]["order_id"]}
        dec_id = next((o["decision_id"] for o in entry
                       if o.get("decision_id")), None)
        if dec_id is None:
            dec_id = next((o["decision_id"] for o in orders
                           if o["group_id"] == g and o.get("decision_id")
                           and o["role"] == "ENTRY"), None)
        dec = decisions.get(dec_id) or {}
        p, pb = AT.decision_probability(dec)
        sv = sorted(sets_by_key.get(pk, []), key=lambda s: s["version"])
        st = None
        if sv:
            last = sv[-1]
            st = {"t": last["t"],
                  "payout_per_contract": C.num(last["payout_per_contract"]),
                  "outcome": last["outcome"], "ref": last["settlement_id"],
                  "basis": "PAPER_SETTLEMENT_LATEST_VERSION"}
        ev, evb = starts.get((g, slug), starts.get((None, slug), (None, None)))
        out.append(attach_sleeve({
            "book": "PAPER", "position_key": pk,
            # the paper book SIMULATES fills against the Polymarket US book;
            # book=PAPER is what marks it simulated (never a venue named
            # PAPER_SIMULATED, which no position model resolves)
            "venue": "POLYMARKET_US", "account_id": account_id,
            "group_id": g, "us_market_slug": slug, "holding_side": side,
            "strategy": fs[0].get("strategy") or dec.get("strategy"),
            "events": [{"t": f["t"], "kind": f["direction"],
                        "qty": C.num(f["qty"]), "price": C.num(f["price"]),
                        "fee_usd": C.num(f["fee_usd"]), "ref": f["fill_id"]}
                       for f in fs],
            "reservation": res, "settlement": st,
            "contract_settlement": cset.get((slug, side)),
            "probability": p, "probability_basis": pb,
            "decision_id": dec_id, "decided_at": _ep(dec.get("decided_at")),
            "event_start_at": ev, "event_start_basis": evb,
            "src": {
                "fill_ids": [f["fill_id"] for f in fs],
                "order_ids": sorted({o["order_id"] for o in po}
                                    | {f["order_id"] for f in fs
                                       if f.get("order_id")}),
                "book_obs_ids": sorted({int(f["book_obs_id"]) for f in fs
                                        if f.get("book_obs_id") is not None}),
                "settlement_ids": [s["settlement_id"] for s in sv],
                "decision_ids": sorted({o["decision_id"] for o in po
                                        if o.get("decision_id")}
                                       | ({dec_id} if dec_id else set())),
                "simulator_versions": sorted({f["simulator_version"]
                                              for f in fs
                                              if f.get("simulator_version")})}},
            classes, policy_version=dec.get("policy_version")))
    return {"positions": out, "group_ids": gids, "decisions": decisions,
            "sleeve_schema": classes is not None}


async def paper_capital(conn, *, account_id=C.PAPER_ACCOUNT) -> dict:
    eq = await conn.fetchrow(
        "SELECT equity_usd, extract(epoch FROM at)::float8 AS at "
        "  FROM paper_equity_snapshots WHERE account_id = $1 "
        " ORDER BY at DESC LIMIT 1", account_id)
    res = await conn.fetchval(
        "SELECT sum(reserved_remaining_usd) FROM paper_orders "
        " WHERE account_id = $1 AND state = ANY($2::text[])",
        account_id, PAPER_OPEN_STATES)
    out = {"open_reservations_usd": C.num(res) if res is not None else 0.0,
           "reservations_why": None}
    if eq is not None and C.num(eq["equity_usd"]) is not None:
        out.update(account_capital=C.num(eq["equity_usd"]),
                   basis="paper_equity_snapshots.equity_usd at %s" % eq["at"])
        return out
    start = await conn.fetchval(
        "SELECT starting_cash_usd FROM paper_accounts WHERE account_id = $1",
        account_id)
    out.update(account_capital=C.num(start),
               basis=("paper_accounts.starting_cash_usd (no equity snapshot)"
                      if start is not None else None))
    return out


# ═════════════════════════════════════════════════════════════════════
# ACTUAL
# ═════════════════════════════════════════════════════════════════════

async def actual_positions(conn, *, now, days=LOOKBACK_DAYS) -> dict:
    cut = float(now) - days * 86400.0
    raw = []
    if await has(conn, "execmirror_fills"):
        for r in await conn.fetch(
                "SELECT f.fill_key, f.mirror_id, f.group_id, "
                "       f.us_market_slug, f.intent, f.qty, f.price, "
                "       f.fee_usd, o.role, o.execution_intent_id, "
                "       o.strategy, "
                "       extract(epoch FROM f.observed_at)::float8 AS t "
                "  FROM execmirror_fills f "
                "  LEFT JOIN execmirror_orders o ON o.mirror_id = f.mirror_id "
                " WHERE f.group_id IN (SELECT DISTINCT group_id "
                "        FROM execmirror_fills "
                "       WHERE observed_at >= to_timestamp($1)) "
                " ORDER BY f.observed_at LIMIT $2", cut, MAX_ROWS):
            r = dict(r)
            side = IC.side_of_intent(r["intent"])
            raw.append({"venue": "POLYMARKET_US", "group_id": r["group_id"],
                        "slug": r["us_market_slug"], "side": side,
                        "kind": "BUY" if IC.is_buy_intent(r["intent"])
                        else "SELL", "t": r["t"], "qty": C.num(r["qty"]),
                        "price": IC.cost_space(r["price"], side),
                        "fee_usd": C.num(r["fee_usd"]),
                        "ref": r["fill_key"], "mirror_id": r["mirror_id"],
                        "intent_id": r.get("execution_intent_id"),
                        "decision_id": None, "strategy": r.get("strategy")})
    if await has(conn, "kalshi_live_fills") and await has(
            conn, "kalshi_live_intents"):
        for r in await conn.fetch(
                "SELECT k.trade_id, k.link_id, i.group_id, i.us_market_slug,"
                "       k.action, i.holding, k.count, k.price, k.fee_usd, "
                "       i.paper_decision_id, "
                "       extract(epoch FROM k.observed_at)::float8 AS t "
                "  FROM kalshi_live_fills k "
                "  JOIN kalshi_live_intents i ON i.link_id = k.link_id "
                " WHERE k.observed_at >= to_timestamp($1) "
                " ORDER BY k.observed_at LIMIT $2", cut, MAX_ROWS):
            r = dict(r)
            side = "SHORT" if str(r.get("holding") or "").upper() == \
                "SHORT" else "LONG"
            raw.append({"venue": "KALSHI", "group_id": r["group_id"],
                        "slug": r["us_market_slug"], "side": side,
                        "kind": "BUY" if str(r["action"]).lower() == "buy"
                        else "SELL", "t": r["t"], "qty": C.num(r["count"]),
                        "price": C.num(r["price"]),
                        "fee_usd": C.num(r["fee_usd"]), "ref": r["trade_id"],
                        "mirror_id": r["link_id"], "intent_id": None,
                        "decision_id": r.get("paper_decision_id"),
                        "strategy": None})
    raw = [r for r in raw if r.get("group_id") and r.get("slug")]
    gids = sorted({r["group_id"] for r in raw})
    if not gids:
        return {"positions": [], "group_ids": [], "decisions": {}}
    intents = [dict(r) for r in await conn.fetch(
        "SELECT intent_id, decision_id, valuation_id, policy_version, "
        "       group_id, us_market_slug, order_intent, actual_mirror_id, "
        "       book_obs_id, strategy, "
        "       extract(epoch FROM decided_at)::float8 AS decided_at "
        "  FROM execution_intents WHERE group_id = ANY($1::text[]) "
        " LIMIT $2", gids, MAX_ROWS)] if await has(
        conn, "execution_intents") else []
    dids = sorted({i["decision_id"] for i in intents if i.get("decision_id")}
                  | {r["decision_id"] for r in raw if r.get("decision_id")})
    decisions = await decisions_by_id(conn, dids)
    handoffs = [dict(r) for r in await conn.fetch(
        "SELECT handoff_id, venue, group_id, us_market_slug, state "
        "  FROM smalllive_handoffs WHERE group_id = ANY($1::text[]) "
        " LIMIT $2", gids, MAX_ROWS)] if await has(
        conn, "smalllive_handoffs") else []
    slugs = sorted({r["slug"] for r in raw})
    cset = await contract_settlements(conn, slugs)
    gset = await group_settlements(conn, gids)
    starts = await event_starts(conn, slugs, gids)
    classes = await sleeve_classes(conn, gids)
    by_pos: dict = {}
    for r in raw:
        by_pos.setdefault((r["venue"], r["group_id"], r["slug"], r["side"]),
                          []).append(r)
    out = []
    for (venue, g, slug, side), fs in by_pos.items():
        gi = [i for i in intents if i["group_id"] == g]
        dec_id = next((i["decision_id"] for i in gi
                       if i.get("decision_id")
                       and i.get("us_market_slug") == slug), None) or next(
            (i["decision_id"] for i in gi if i.get("decision_id")), None) \
            or next((r["decision_id"] for r in fs if r.get("decision_id")),
                    None)
        dec = decisions.get(dec_id) or {}
        p, pb = AT.decision_probability(dec)
        st = gset.get((g, slug, side)) or cset.get((slug, side))
        settlement = None
        if st:
            settlement = dict(st)
            settlement["basis"] = (
                "PAPER_SETTLEMENT_OF_THE_SAME_CONTRACT_SAME_GROUP"
                if (g, slug, side) in gset else
                "PAPER_SETTLEMENT_OF_THE_SAME_CONTRACT")
        ev, evb = starts.get((g, slug), starts.get((None, slug), (None, None)))
        hs = [h for h in handoffs if h["group_id"] == g
              and h.get("us_market_slug") in (slug, None)]
        ipv = sorted({i["policy_version"] for i in gi
                      if i.get("policy_version")})
        out.append(attach_sleeve({
            "book": "ACTUAL",
            "position_key": "actualpos:%s:%s:%s:%s" % (venue, g, slug, side),
            "venue": venue, "account_id": None, "group_id": g,
            "us_market_slug": slug, "holding_side": side,
            "strategy": next((r["strategy"] for r in fs if r.get("strategy")),
                             None) or dec.get("strategy"),
            "events": [{"t": r["t"], "kind": r["kind"], "qty": r["qty"],
                        "price": r["price"], "fee_usd": r["fee_usd"],
                        "ref": r["ref"]} for r in fs],
            "reservation": None, "settlement": settlement,
            "contract_settlement": settlement,
            "probability": p, "probability_basis": pb,
            "decision_id": dec_id, "decided_at": _ep(dec.get("decided_at")),
            "event_start_at": ev, "event_start_basis": evb,
            "src": {
                "fill_ids": [r["ref"] for r in fs],
                "order_ids": sorted({r["mirror_id"] for r in fs
                                     if r.get("mirror_id")}),
                "intent_ids": sorted({i["intent_id"] for i in gi}),
                "book_obs_ids": sorted({int(i["book_obs_id"]) for i in gi
                                        if i.get("book_obs_id") is not None}),
                "settlement_ids": [st["ref"]] if st else [],
                "decision_ids": sorted({i["decision_id"] for i in gi
                                        if i.get("decision_id")}
                                       | ({dec_id} if dec_id else set())),
                "policy_versions": sorted({i["policy_version"] for i in gi
                                           if i.get("policy_version")}),
                "valuation_ids": sorted({int(i["valuation_id"]) for i in gi
                                         if i.get("valuation_id")
                                         is not None}),
                "handoff_ids": sorted({h["handoff_id"] for h in hs})}},
            classes, strategy_fallback=True,
            policy_version=(ipv[0] if len(ipv) == 1
                            else dec.get("policy_version"))))
    return {"positions": out, "group_ids": gids, "decisions": decisions,
            "handoffs": handoffs, "sleeve_schema": classes is not None}


async def actual_capital(conn) -> dict:
    out = {"open_reservations_usd": None,
           "reservations_why": "VENUE_OPEN_ORDER_RESERVATIONS_NOT_IN_THESE_"
                               "RECORDS", "account_capital": None,
           "basis": None}
    if not await has(conn, "execmirror_snapshots"):
        return out
    r = await conn.fetchrow(
        "SELECT balances, extract(epoch FROM at)::float8 AS at "
        "  FROM execmirror_snapshots ORDER BY at DESC LIMIT 1")
    if r is None:
        return out
    bal = C.jload(r["balances"]) or []
    if bal and isinstance(bal[0], dict):
        cb = C.num(bal[0].get("currentBalance"))
        an = C.num(bal[0].get("assetNotional"))
        if cb is not None and an is not None:
            out.update(account_capital=cb + an, basis=(
                "execmirror_snapshots currentBalance + assetNotional at %s "
                "(Polymarket US mirror account only; the Kalshi lane has no "
                "equity series)" % r["at"]))
    return out


# ═════════════════════════════════════════════════════════════════════
# SHARED
# ═════════════════════════════════════════════════════════════════════

async def decisions_by_id(conn, ids) -> dict:
    ids = sorted({i for i in ids if i})
    if not ids:
        return {}
    rows = await conn.fetch(
        "SELECT decision_id, decided_at, valuation_id, us_market_slug, "
        "       holding_side, verdict, p_pinnacle, p_blended, p_internal, "
        "       limit_price, economics, policy_version, simulator_version, "
        "       strategy, book_obs_id, internal_model->>'version' AS "
        "       model_version, (internal_model IS NOT NULL AND "
        "       internal_model::text <> '{}') AS has_features "
        "  FROM paper_decisions WHERE decision_id = ANY($1::text[])", ids)
    return {r["decision_id"]: dict(r) for r in rows}


async def contract_settlements(conn, slugs) -> dict:
    """{(slug, side): latest settlement of that contract, any position}."""
    slugs = sorted({s for s in slugs if s})
    if not slugs:
        return {}
    rows = await conn.fetch(
        "SELECT DISTINCT ON (us_market_slug, holding_side) settlement_id, "
        "       us_market_slug, holding_side, outcome, payout_per_contract, "
        "       extract(epoch FROM settled_at)::float8 AS t "
        "  FROM paper_settlements WHERE us_market_slug = ANY($1::text[]) "
        " ORDER BY us_market_slug, holding_side, settled_at DESC, "
        "          version DESC", slugs)
    return {(r["us_market_slug"], str(r["holding_side"])): {
        "t": r["t"], "payout_per_contract": C.num(r["payout_per_contract"]),
        "outcome": r["outcome"], "ref": r["settlement_id"],
        "basis": "PAPER_SETTLEMENT_OF_THE_SAME_CONTRACT"} for r in rows}


async def group_settlements(conn, gids) -> dict:
    gids = sorted({g for g in gids if g})
    if not gids:
        return {}
    rows = await conn.fetch(
        "SELECT DISTINCT ON (group_id, us_market_slug, holding_side) "
        "       settlement_id, group_id, us_market_slug, holding_side, "
        "       outcome, payout_per_contract, "
        "       extract(epoch FROM settled_at)::float8 AS t "
        "  FROM paper_settlements WHERE group_id = ANY($1::text[]) "
        " ORDER BY group_id, us_market_slug, holding_side, version DESC",
        gids)
    return {(r["group_id"], r["us_market_slug"], str(r["holding_side"])): {
        "t": r["t"], "payout_per_contract": C.num(r["payout_per_contract"]),
        "outcome": r["outcome"], "ref": r["settlement_id"]} for r in rows}


async def event_starts(conn, slugs, gids) -> dict:
    """{(None, slug) | (group, slug): (event start epoch, basis)}: the
    venue's pre-map game start, else Xavier's entry thesis event start."""
    out = {}
    pm = await IR.premap(conn, slugs)
    for s, r in pm.items():
        if C.num(r.get("game_start")) is not None:
            out[(None, s)] = (float(r["game_start"]), "US_PREMAP_GAME_START")
    if gids and await has(conn, "xavier_entry_theses"):
        for r in await conn.fetch(
                "SELECT group_id, us_market_slug, "
                "       extract(epoch FROM event_start_at)::float8 AS ev "
                "  FROM xavier_entry_theses WHERE group_id = ANY($1::text[]) "
                "   AND event_start_at IS NOT NULL", sorted(gids)):
            if (None, r["us_market_slug"]) not in out:
                out[(r["group_id"], r["us_market_slug"])] = (
                    float(r["ev"]), "XAVIER_ENTRY_THESIS_EVENT_START")
    return out


async def settlement_lag_samples(conn, *, now, days=LOOKBACK_DAYS * 2) -> list:
    """[(recorded_at, settled_at - game start)] -- the first ordinary
    settlement of each market against its pre-map game start."""
    rows = await conn.fetch(
        "SELECT extract(epoch FROM s.recorded_at)::float8 AS rec, "
        "       extract(epoch FROM s.settled_at - g.game_start)::float8 "
        "       AS lag "
        "  FROM (SELECT DISTINCT ON (us_market_slug) us_market_slug, "
        "               recorded_at, settled_at FROM paper_settlements "
        "         WHERE settled_at >= to_timestamp($1) "
        "           AND outcome IN ('WON', 'LOST') "
        "         ORDER BY us_market_slug, settled_at) s "
        "  JOIN LATERAL (SELECT game_start FROM us_premap "
        "                 WHERE market_slug = s.us_market_slug "
        "                   AND game_start IS NOT NULL "
        "                 ORDER BY updated_at DESC NULLS LAST LIMIT 1) g "
        "    ON true LIMIT $2", float(now) - days * 86400.0, MAX_ROWS)
    return [(C.num(r["rec"]), C.num(r["lag"])) for r in rows]


# ═════════════════════════════════════════════════════════════════════
# LINEAGE CONTEXT (by the ids of the positions just loaded)
# ═════════════════════════════════════════════════════════════════════

async def _ids(conn, table, sql, *args):
    if not await has(conn, table):
        return None
    return [dict(r) for r in await conn.fetch(sql, *args)]


async def lineage_context(conn, *, positions: list, decisions: dict,
                          account_id, now) -> dict:
    """Everything a position's lineage references beyond its own fills,
    orders and settlements. Each entry is a list of rows, or None when the
    table is absent in this database (a named gap, never an empty list)."""
    gids = sorted({p["group_id"] for p in positions if p.get("group_id")})
    dids = sorted({d for p in positions for d in p["src"]["decision_ids"]})
    keys = sorted({p["position_key"] for p in positions})
    ids = sorted(set(gids) | set(dids) | set(keys))
    vids = sorted({int(d["valuation_id"]) for d in decisions.values()
                   if d.get("valuation_id") is not None}
                  | {v for p in positions
                     for v in p["src"].get("valuation_ids", [])})
    ctx = {}
    ctx["valuations"] = await _ids(
        conn, "external_valuations",
        "SELECT id, experiment_id, version, provider, devig_method, "
        "       record_purpose, event_key, "
        "       (raw_odds IS NOT NULL AND raw_odds::text NOT IN ('{}', "
        "        'null')) AS has_raw_inputs, outcome_known, outcome_basis, "
        "       extract(epoch FROM observed_at)::float8 AS observed_at, "
        "       extract(epoch FROM received_at)::float8 AS received_at "
        "  FROM external_valuations WHERE id = ANY($1::bigint[])", vids) \
        if vids else []
    ctx["intents"] = await _ids(
        conn, "execution_intents",
        "SELECT intent_id, group_id, decision_id, policy_version "
        "  FROM execution_intents WHERE group_id = ANY($1::text[]) "
        "    OR decision_id = ANY($2::text[]) LIMIT $3", gids, dids, MAX_ROWS)
    ctx["reviews"] = await _ids(
        conn, "paper_xavier_reviews",
        "SELECT review_id, group_id, recommendation, "
        "       (action IS NOT NULL) AS acted, "
        "       extract(epoch FROM reviewed_at)::float8 AS at "
        "  FROM paper_xavier_reviews WHERE group_id = ANY($1::text[]) "
        " LIMIT $2", gids, MAX_ROWS)
    ctx["actual_reviews"] = await _ids(
        conn, "smalllive_reviews",
        "SELECT r.review_id, h.group_id, r.action "
        "  FROM smalllive_reviews r JOIN smalllive_handoffs h "
        "    ON h.handoff_id = r.handoff_id "
        " WHERE h.group_id = ANY($1::text[]) LIMIT $2", gids, MAX_ROWS) \
        if await has(conn, "smalllive_handoffs") else None
    ctx["paper_handoffs"] = await _ids(
        conn, "paper_handoffs",
        "SELECT handoff_id, group_id FROM paper_handoffs "
        " WHERE group_id = ANY($1::text[]) LIMIT $2", gids, MAX_ROWS)
    ctx["theses"] = await _ids(
        conn, "xavier_entry_theses",
        "SELECT thesis_id, position_kind, group_id, us_market_slug, "
        "       (counterfactuals IS NOT NULL) AS has_cf "
        "  FROM xavier_entry_theses WHERE group_id = ANY($1::text[]) "
        " LIMIT $2", gids, MAX_ROWS)
    ctx["assessments"] = await _ids(
        conn, "xavier_management_assessments",
        "SELECT assessment_id, position_kind, group_id "
        "  FROM xavier_management_assessments "
        " WHERE group_id = ANY($1::text[]) LIMIT $2", gids, MAX_ROWS)
    ctx["value_add"] = await _ids(
        conn, "xavier_value_add",
        "SELECT value_add_id, position_kind, group_id "
        "  FROM xavier_value_add WHERE group_id = ANY($1::text[]) LIMIT $2",
        gids, MAX_ROWS)
    ctx["postmortems"] = await _ids(
        conn, "position_postmortems",
        "SELECT book, position_key, group_id, us_market_slug, holding_side "
        "  FROM position_postmortems WHERE group_id = ANY($1::text[]) "
        " LIMIT $2", gids, MAX_ROWS)
    ctx["challenges"] = await _ids(
        conn, "karen_challenges",
        "SELECT challenge_id, target_id FROM karen_challenges "
        " WHERE target_id = ANY($1::text[]) LIMIT $2", ids, MAX_ROWS)
    ctx["findings"] = await _ids(
        conn, "paper_audrey_findings",
        "SELECT finding_id, subject FROM paper_audrey_findings "
        " WHERE subject = ANY($1::text[]) LIMIT $2", ids, MAX_ROWS)
    ctx["agent_decisions"] = await _ids(
        conn, "agent_decisions",
        "SELECT decision_ref, subject FROM agent_decisions "
        " WHERE subject = ANY($1::text[]) LIMIT $2", ids, MAX_ROWS)
    ctx["ledger"] = await _ids(
        conn, "paper_ledger",
        "SELECT seq, group_id, position_key FROM paper_ledger "
        " WHERE account_id = $1 AND group_id = ANY($2::text[]) LIMIT $3",
        account_id, gids, MAX_ROWS)
    ctx["attribution"] = await _ids(
        conn, "intel_attribution",
        "SELECT book, subject_id FROM intel_attribution "
        " WHERE subject_id = ANY($1::text[])", gids)
    ctx["sizing"] = await _ids(
        conn, "intel_sizing",
        "SELECT decision_id FROM intel_sizing "
        " WHERE decision_id = ANY($1::text[])", dids)
    ctx["allocations"] = await _ids(
        conn, "intel_allocations",
        "SELECT DISTINCT ON (decision_id) decision_id, run_id, candidate_id "
        "  FROM intel_allocations WHERE decision_id = ANY($1::text[]) "
        " ORDER BY decision_id, computed_at", dids)
    dts = [C.epoch(d.get("decided_at")) for d in decisions.values()
           if d.get("decided_at") is not None]
    ctx["regimes"] = await _ids(
        conn, "intel_regime_states",
        "SELECT run_id, extract(epoch FROM computed_at)::float8 AS at "
        "  FROM intel_regime_states WHERE computed_at >= to_timestamp($1) "
        "   AND computed_at <= to_timestamp($2) ORDER BY computed_at "
        " LIMIT $3", (min(dts) - 86400.0) if dts else float(now),
        float(now), MAX_ROWS)
    ctx["capacity"] = await _ids(
        conn, "pos_capacity",
        "SELECT DISTINCT ON (candidate_id) candidate_id, capacity_id "
        "  FROM pos_capacity WHERE candidate_id = ANY($1::text[]) "
        " ORDER BY candidate_id, computed_at DESC", dids)
    ctx["previous"] = await _ids(
        conn, "pos_lineage",
        "SELECT * FROM pos_lineage_latest WHERE position_key = ANY($1::text[])",
        keys)
    return ctx


# ═════════════════════════════════════════════════════════════════════
# CAPACITY
# ═════════════════════════════════════════════════════════════════════

async def capacity_candidates(conn, *, now, account_id=None,
                              hours=CAPACITY_LOOKBACK_H) -> list:
    """Recent decisions not yet assessed (an assessment is immutable: the
    recorded book at the decision does not change)."""
    rows = await conn.fetch(
        "SELECT d.decision_id, d.us_market_slug, d.holding_side, "
        "       d.p_blended, d.p_pinnacle, d.p_internal, d.book_obs_id, "
        "       d.strategy, d.policy_version, "
        "       CASE WHEN jsonb_typeof(d.book->'age_at_decision_s') = "
        "            'number' THEN (d.book->>'age_at_decision_s')::float8 "
        "       END AS decision_book_age_s, "
        "       extract(epoch FROM d.decided_at)::float8 AS decided_at "
        "  FROM paper_decisions d "
        " WHERE d.decided_at >= to_timestamp($1) "
        "   AND ($2::text IS NULL OR d.account_id = $2) "
        "   AND NOT EXISTS (SELECT 1 FROM pos_capacity c "
        "                    WHERE c.candidate_id = d.decision_id) "
        " ORDER BY d.decided_at DESC LIMIT $3",
        float(now) - hours * 3600.0, account_id, CAPACITY_MAX_CANDIDATES)
    out = []
    for r in rows:
        r = dict(r)
        p, pb = AT.decision_probability(r)
        out.append({"candidate_id": r["decision_id"],
                    "decided_at": r["decided_at"],
                    "us_market_slug": r["us_market_slug"],
                    "holding_side": r["holding_side"],
                    "strategy": r["strategy"], "probability": p,
                    "probability_basis": pb,
                    "book_obs_id": r["book_obs_id"],
                    "policy_version": r["policy_version"],
                    "decision_book_age_s": r["decision_book_age_s"]})
    return out


async def capacity_books(conn, cands: list) -> dict:
    """{candidate_id: {obs_id, observed_at, bids, offers}}: the decision's
    own book observation, else the latest readable observation of the
    market at or before the decision (within the capacity model's bound)."""
    from .capacity import MAX_BOOK_AGE_S

    out = {}
    oids = sorted({int(c["book_obs_id"]) for c in cands
                   if c.get("book_obs_id") is not None})
    by_obs = {}
    if oids:
        for r in await conn.fetch(
                "SELECT obs_id, bids, offers, error, "
                "       extract(epoch FROM observed_at)::float8 AS t "
                "  FROM paper_book_observations "
                " WHERE obs_id = ANY($1::bigint[])", oids):
            if r["error"] is None:
                by_obs[int(r["obs_id"])] = {
                    "obs_id": int(r["obs_id"]), "observed_at": r["t"],
                    "bids": r["bids"], "offers": r["offers"]}
    rest = []
    for c in cands:
        b = by_obs.get(int(c["book_obs_id"])) if c.get(
            "book_obs_id") is not None else None
        if b:
            out[c["candidate_id"]] = b
        elif c.get("us_market_slug") and c.get("decided_at") is not None:
            rest.append(c)
    if rest:
        for r in await conn.fetch(
                "SELECT c.id, b.obs_id, b.bids, b.offers, "
                "       extract(epoch FROM b.observed_at)::float8 AS t "
                "  FROM unnest($1::text[], $2::text[], $3::float8[]) "
                "       AS c(id, slug, at) "
                "  JOIN LATERAL (SELECT obs_id, bids, offers, observed_at "
                "                  FROM paper_book_observations "
                "                 WHERE us_market_slug = c.slug "
                "                   AND error IS NULL "
                "                   AND observed_at <= to_timestamp(c.at) "
                "                   AND observed_at >= to_timestamp(c.at - $4)"
                "                 ORDER BY observed_at DESC LIMIT 1) b "
                "    ON true",
                [c["candidate_id"] for c in rest],
                [c["us_market_slug"] for c in rest],
                [float(c["decided_at"]) for c in rest],
                float(MAX_BOOK_AGE_S)):
            out[r["id"]] = {"obs_id": int(r["obs_id"]), "observed_at": r["t"],
                            "bids": r["bids"], "offers": r["offers"]}
    return out


async def capacity_rates(conn, *, now, account_id=None,
                         days=RATE_DAYS, strategies=None) -> dict:
    """Book-level empirical rates for the capacity model (named basis).
    `strategies` None = every paper strategy (the RESEARCH rates); a list =
    only those strategies' orders (the PRODUCTION rates pass the INVESTMENT
    strategies, so a TRAINING or BENCHMARK fill never moves them)."""
    from . import capacity as CP

    cut = float(now) - days * 86400.0
    strat = None if strategies is None else sorted(strategies)
    tag = "" if strat is None else "_INVESTMENT_STRATEGIES"
    r = await conn.fetchrow(
        "SELECT count(*) AS n, sum(qty) AS q, sum(filled_qty) AS f "
        "  FROM paper_orders WHERE role = 'ENTRY' "
        "   AND state = ANY($1::text[]) AND created_at >= to_timestamp($2) "
        "   AND ($3::text IS NULL OR account_id = $3) "
        "   AND ($4::text[] IS NULL OR strategy = ANY($4::text[]))",
        PAPER_TERMINAL_STATES, cut, account_id, strat)
    fp = CP.rate(C.num(r["f"]) or 0.0, C.num(r["q"]) or 0.0, int(r["n"]),
                 min_n=20, basis="PAPER_SIMULATED_ENTRY_ORDERS_FILLED_QTY_"
                                 "SHARE_%dD%s" % (int(days), tag),
                 why="FEWER_THAN_20_TERMINAL_PAPER_ENTRY_ORDERS%s" % tag)
    lat = await conn.fetchrow(
        "SELECT count(*) AS n, percentile_cont(0.5) WITHIN GROUP (ORDER BY "
        "       extract(epoch FROM f.first - o.decided_at)) AS med "
        "  FROM paper_orders o JOIN LATERAL (SELECT min(filled_at) AS first "
        "       FROM paper_fills WHERE order_id = o.order_id) f "
        "    ON f.first IS NOT NULL "
        " WHERE o.role = 'ENTRY' AND o.created_at >= to_timestamp($1) "
        "   AND o.decided_at IS NOT NULL "
        "   AND ($2::text IS NULL OR o.account_id = $2) "
        "   AND ($3::text[] IS NULL OR o.strategy = ANY($3::text[]))",
        cut, account_id, strat)
    ex = await conn.fetchrow(
        "SELECT count(*) AS n, percentile_cont(0.5) WITHIN GROUP (ORDER BY "
        "       extract(epoch FROM f.first - o.created_at)) AS med "
        "  FROM paper_orders o JOIN LATERAL (SELECT min(filled_at) AS first "
        "       FROM paper_fills WHERE order_id = o.order_id) f "
        "    ON f.first IS NOT NULL "
        " WHERE o.role IN ('EXIT', 'REDUCE') "
        "   AND o.created_at >= to_timestamp($1) "
        "   AND ($2::text IS NULL OR o.account_id = $2) "
        "   AND ($3::text[] IS NULL OR o.strategy = ANY($3::text[]))",
        cut, account_id, strat)

    def med(row, min_n, basis, why):
        n = int(row["n"])
        if n < min_n or row["med"] is None:
            return {"value": None, "n": n, "basis": basis, "why": why}
        return {"value": C.rnd(C.num(row["med"]), 3), "n": n,
                "basis": basis, "why": None}

    out = {"fill_probability": fp,
           "deployment_time_s": med(
               lat, 5, "MEDIAN_PAPER_ENTRY_DECISION_TO_FIRST_FILL_%dD%s"
               % (int(days), tag), "FEWER_THAN_5_FILLED_PAPER_ENTRY_ORDERS"),
           "time_to_exit_s": med(
               ex, 5, "MEDIAN_PAPER_EXIT_ORDER_TO_FIRST_FILL_%dD%s"
               % (int(days), tag), "FEWER_THAN_5_FILLED_PAPER_EXIT_ORDERS"),
           "strategies": strat}
    if await has(conn, "execmirror_orders"):
        a = await conn.fetchrow(
            "SELECT count(*) AS n, sum(live_qty) AS q, sum(cum_qty) AS f "
            "  FROM execmirror_orders WHERE live_qty > 0 "
            "   AND accepted_at IS NOT NULL "
            "   AND created_at >= to_timestamp($1)", cut)
        out["actual_fill_probability"] = CP.rate(
            C.num(a["f"]) or 0.0, C.num(a["q"]) or 0.0, int(a["n"]),
            min_n=20, basis="ACTUAL_MIRROR_ACCEPTED_ORDERS_FILLED_QTY_SHARE_"
                            "%dD" % int(days),
            why="FEWER_THAN_20_ACCEPTED_ACTUAL_ORDERS")
    else:
        out["actual_fill_probability"] = {
            "value": None, "n": 0, "basis": None,
            "why": "EXECMIRROR_ORDERS_ABSENT"}
    return out


async def capacity_recent(conn, *, now, hours=CAPACITY_LOOKBACK_H) -> list:
    """The latest assessment per recent candidate, with what the executable
    freshness check needs: the assessment's book (id, age at the decision)
    and the decision's own recorded book (id, age the decision saw)."""
    rows = await conn.fetch(
        "SELECT c.candidate_id, c.us_market_slug, c.holding_side, c.status, "
        "       c.why, c.strategy, c.book_obs_id, c.book_age_s, "
        "       c.theoretical_opportunity_dollars, "
        "       c.executable_opportunity_dollars, c.executable_capacity_usd, "
        "       c.capacity_ceiling_usd, "
        "       d.book_obs_id AS decision_book_obs_id, "
        "       d.policy_version, "
        "       CASE WHEN jsonb_typeof(d.book->'age_at_decision_s') = "
        "            'number' THEN (d.book->>'age_at_decision_s')::float8 "
        "       END AS decision_book_age_s, "
        "       extract(epoch FROM c.decided_at)::float8 AS decided_at "
        "  FROM pos_capacity_latest c "
        "  LEFT JOIN paper_decisions d ON d.decision_id = c.candidate_id "
        " WHERE c.decided_at >= to_timestamp($1) "
        " ORDER BY c.decided_at DESC LIMIT $2",
        float(now) - hours * 3600.0, MAX_ROWS)
    return [dict(r) for r in rows]


# ═════════════════════════════════════════════════════════════════════
# THE LAYER'S OWN HISTORY
# ═════════════════════════════════════════════════════════════════════

async def previous_metrics(conn, *, now, min_age_h) -> dict:
    """{(book, sleeve, strategy, metric): the latest SCOPED observation at
    least `min_age_h` old}. Pre-227 book-wide rows (sleeve NULL) are never
    a scoped metric's trend base."""
    rows = await conn.fetch(
        "SELECT DISTINCT ON (book, sleeve, strategy, metric) book, sleeve, "
        "       strategy, metric, value, status, "
        "       extract(epoch FROM computed_at)::float8 AS computed_at "
        "  FROM pos_metric_observations "
        " WHERE computed_at <= to_timestamp($1) AND sleeve IS NOT NULL "
        " ORDER BY book, sleeve, strategy, metric, computed_at DESC",
        float(now) - min_age_h * 3600.0)
    return {(r["book"], r["sleeve"], r["strategy"], r["metric"]): dict(r)
            for r in rows}


async def latest_metric_shas(conn) -> dict:
    rows = await conn.fetch(
        "SELECT DISTINCT ON (book, sleeve, strategy, metric) book, sleeve, "
        "       strategy, metric, content_sha256 "
        "  FROM pos_metric_observations WHERE sleeve IS NOT NULL "
        " ORDER BY book, sleeve, strategy, metric, computed_at DESC")
    return {(r["book"], r["sleeve"], r["strategy"], r["metric"]):
            r["content_sha256"] for r in rows}


async def forecast_scores(conn, book, sleeve=None,
                          strategy=C.ALL_STRATEGIES) -> list:
    """The scores of THIS scope's own forecasts (a forecast is validated
    only by its own scored history: an INVESTMENT forecast is never
    validated by a TRAINING forecast's scores, nor by a pre-227 book-wide
    forecast's)."""
    rows = await conn.fetch(
        "SELECT s.inside_p10_p90, s.brier_positive "
        "  FROM pos_forecast_scores s "
        "  JOIN pos_forecasts f ON f.forecast_id = s.forecast_id "
        " WHERE s.book = $1 AND f.sleeve IS NOT DISTINCT FROM $2 "
        "   AND f.strategy IS NOT DISTINCT FROM $3 "
        " ORDER BY s.scored_at LIMIT $4", book, sleeve,
        strategy if sleeve is not None else None, MAX_ROWS)
    return [dict(r) for r in rows]


async def unscored_forecasts(conn, *, now) -> list:
    rows = await conn.fetch(
        "SELECT f.forecast_id, f.book, f.sleeve, f.strategy, "
        "       f.confidence_scope, f.quantiles, f.prob_positive, "
        "       f.p10_pnl_usd, f.p50_pnl_usd, f.p90_pnl_usd, "
        "       extract(epoch FROM f.horizon_start)::float8 AS hs, "
        "       extract(epoch FROM f.horizon_end)::float8 AS he "
        "  FROM pos_forecasts f "
        " WHERE f.status <> 'UNAVAILABLE' "
        "   AND f.horizon_end <= to_timestamp($1) "
        "   AND NOT EXISTS (SELECT 1 FROM pos_forecast_scores s "
        "                    WHERE s.forecast_id = f.forecast_id) "
        " ORDER BY f.issued_at LIMIT 200", float(now))
    return [dict(r) for r in rows]


async def realized_between(conn, *, book, start, end, sleeve=None,
                           strategy=None) -> tuple:
    """(realized net, positions) of the book's positions released inside
    [start, end). `sleeve` None = every sleeve (a pre-227 book-wide
    forecast is scored against the whole book it forecast); a sleeve =
    only positions of that sleeve (the group's durable classification;
    UNCLASSIFIED never counts as INVESTMENT); `strategy` None / ALL = every
    strategy of the sleeve."""
    have_view = await has(conn, "paper_sleeve_current_v")
    sleeve_sql = _sleeve_case_sql("e.group_id", "e.strategy",
                                  book_col="e.book", have_view=have_view)
    r = await conn.fetchrow(
        "SELECT count(*) AS n, sum(e.net_profit_usd) AS s "
        "  FROM pos_economics_latest e WHERE e.book = $1 "
        "   AND e.state = 'CLOSED' AND e.net_profit_usd IS NOT NULL "
        "   AND e.released_at >= to_timestamp($2) "
        "   AND e.released_at < to_timestamp($3) "
        "   AND ($4::text IS NULL OR " + sleeve_sql + " = $4) "
        "   AND ($5::text IS NULL OR $5 = 'ALL' OR e.strategy = $5)",
        book, float(start), float(end), sleeve, strategy)
    return (C.num(r["s"]) or 0.0), int(r["n"])
