"""AUDREY'S AUTOMATIC POSTMORTEMS: EVERY CLOSED POSITION, RECONSTRUCTED.

PAPER and ACTUAL are reconstructed SEPARATELY (`book`), never summed:

  PAPER    closed positions of the paper ledger (bettor_paper_ledger.
           positions with nothing left open), their entry decision
           (paper_orders.decision_id -> paper_decisions), Xavier's reviews
           (paper_xavier_reviews) and management fills, and the settlement.
  ACTUAL   closed small-live positions (smalllive_handoffs state CLOSED)
           from the venue's own fills (execmirror_fills for Polymarket US,
           kalshi_live_fills for Kalshi), referenced to the SAME paper
           decision the actual order was derived from, and Xavier's actual
           reviews (smalllive_reviews).

THE DECOMPOSITION (per position; q = contracts bought, P = the decision's
probability that the held side pays, p_d = the decision's expected
acquisition price per contract, p_f = the realized average fill price, Y =
what one held contract paid / would have paid at settlement):

    selection_edge      q x (P - p_d)          the edge the decision priced
    execution_slippage  -q x (p_f - p_d)       paid above (-) / below (+) it
    fees                -(buy fees + sale fees)
    outcome_variance    q x (Y - P)            the outcome against the
                                               decision's probability
    management          realized_gross - q x (Y - p_f)
                                               Xavier's actions against
                                               holding everything to
                                               settlement (0 when nothing
                                               was sold before settlement)
    unexplained         realized - every measured component

and selection + slippage + variance + management + fees = realized_gross -
fees = realized net P&L exactly (an identity, asserted per row and by a
database CHECK). A component whose input is unmeasured -- no decision
probability, no decision price, an outcome not known for a position sold
before settlement -- is NULL with its reason, and its share lands in
`unexplained`, so the measured parts never pretend to sum on their own.

EDGE AT DECISION VS OUTCOME is kept apart: `selection_edge` uses only what
was known at the decision; the outcome enters only `outcome_variance`.

Y, IN ORDER OF AUTHORITY: this position's own settlement
(paper_settlements.payout_per_contract); a settlement of the same contract
and side on another paper position; the complement of a WON/LOST settlement
of the opposite side. Nothing else: an outcome is never inferred.

Writes only `position_postmortems` (migration 209). Never places, cancels or
changes an order.
"""
from __future__ import annotations

import hashlib
import json
import time
from decimal import Decimal
from typing import Any

VERSION = "AUDREY_POSTMORTEM_V1"
WATERMARK_KEY = "audrey_postmortems_last"
RUN_EVERY_S = 900.0
#: The PAPER book's label in `position_postmortems.venue` (a book label, not
#: a trading venue).
PAPER_BOOK_LABEL = "PAPER_SIMULATED"
COMPONENTS = ("selection_edge_usd", "execution_slippage_usd", "fees_usd",
              "management_value_usd", "outcome_variance_usd")

R_NO_DECISION = "NO_ENTRY_DECISION_LINKED"
R_NO_P = "DECISION_PROBABILITY_NOT_RECORDED"
R_NO_PD = "DECISION_PRICE_NOT_RECORDED"
R_NO_Y = "OUTCOME_NOT_KNOWN_FOR_THIS_CONTRACT"
R_NO_Q = "NOTHING_BOUGHT"


def _f(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and abs(x) != float("inf") else None


def _j(v):
    if isinstance(v, (str, bytes)):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


def _ep(v):
    return v.timestamp() if hasattr(v, "timestamp") else _f(v)


# ═════════════════════════════════════════════════════════════════════
# THE DECOMPOSITION (pure)
# ═════════════════════════════════════════════════════════════════════

def decompose(*, qty, decision_prob, decision_price, fill_price, buy_fees,
              sale_fees, realized_gross, payout_per_contract,
              missing: dict | None = None) -> dict:
    """The five components + unexplained, each with its basis or the
    reason it is unmeasured. Pure; sums to realized net by construction."""
    q = _f(qty) or 0.0
    P, pd_, pf = _f(decision_prob), _f(decision_price), _f(fill_price)
    Y = _f(payout_per_contract)
    bf, sf = _f(buy_fees) or 0.0, _f(sale_fees) or 0.0
    rg = _f(realized_gross) or 0.0
    realized = rg - bf - sf
    miss = dict(missing or {})
    comp: dict[str, dict] = {}

    def put(name, value, basis, why):
        comp[name] = ({"value": round(value, 6), "basis": basis, "why": None}
                      if value is not None else
                      {"value": None, "basis": basis, "why": why})

    if q <= 0:
        for c in COMPONENTS:
            put(c, None, None, R_NO_Q)
        comp["fees_usd"] = {"value": round(-(bf + sf), 6),
                            "basis": "buy fees + sale fees", "why": None}
    else:
        put("selection_edge_usd",
            q * (P - pd_) if (P is not None and pd_ is not None) else None,
            "q x (P_decision - price_decision)",
            miss.get("P") or (R_NO_P if P is None else
                              miss.get("pd") or R_NO_PD))
        put("execution_slippage_usd",
            -q * (pf - pd_) if (pd_ is not None and pf is not None) else None,
            "-q x (fill_avg_price - price_decision)",
            miss.get("pd") or R_NO_PD)
        comp["fees_usd"] = {"value": round(-(bf + sf), 6),
                            "basis": "-(buy fees + sale fees)", "why": None}
        put("outcome_variance_usd",
            q * (Y - P) if (Y is not None and P is not None) else None,
            "q x (payout_per_contract - P_decision)",
            R_NO_Y if Y is None else (miss.get("P") or R_NO_P))
        put("management_value_usd",
            rg - q * (Y - pf) if (Y is not None and pf is not None) else None,
            "realized_gross - q x (payout_per_contract - fill_avg_price): "
            "the actions taken against holding all q to settlement",
            R_NO_Y)
    measured = sum(c["value"] for c in comp.values()
                   if c["value"] is not None)
    unexplained = round(realized - measured, 6)
    if abs(unexplained) < 5e-7:
        unexplained = 0.0
    complete = all(comp[c]["value"] is not None for c in COMPONENTS)
    return {"realized_pnl_usd": round(realized, 6),
            "realized_gross_usd": round(rg, 6),
            "components": comp, "unexplained_usd": unexplained,
            "decomposition_complete": complete,
            "sums_to_realized": abs(measured + unexplained - realized) <= 1e-6,
            "inputs": {"qty": q, "decision_prob": P, "decision_price": pd_,
                       "fill_price": pf, "payout_per_contract": Y,
                       "buy_fees_usd": bf, "sale_fees_usd": sf},
            "edge_at_decision_per_contract": (
                None if P is None or pd_ is None else round(P - pd_, 6)),
            "outcome_per_contract": Y}


# ═════════════════════════════════════════════════════════════════════
# THE SHARED READS
# ═════════════════════════════════════════════════════════════════════

async def _regclass(conn, name: str) -> bool:
    try:
        return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL",
                                        name))
    except Exception:                                           # noqa: BLE001
        return False


def decision_terms(d: dict | None) -> dict:
    """(P, p_d, expected fees, reasons) from a paper_decisions row."""
    if not d:
        return {"P": None, "pd": None, "fees": None,
                "missing": {"P": R_NO_DECISION, "pd": R_NO_DECISION}}
    eco = _j(d.get("economics")) or {}
    acq = eco.get("acquisition") or {}
    P = _f(d.get("p_blended"))
    pbasis = "p_blended"
    if P is None:
        P, pbasis = _f(eco.get("probability")), "economics.probability"
    if P is None:
        P, pbasis = _f(d.get("p_pinnacle")), "p_pinnacle"
    pd_ = _f(acq.get("vwap"))
    dbasis = "economics.acquisition.vwap"
    if pd_ is None:
        pd_, dbasis = _f(d.get("limit_price")), "limit_price"
    return {"P": P, "P_basis": pbasis if P is not None else None,
            "pd": pd_, "pd_basis": dbasis if pd_ is not None else None,
            "fees": _f(acq.get("fees_usd")),
            "edge_pp_at_decision": _f(acq.get("edge_at_vwap_pp")),
            "missing": {}}


async def entry_decision(conn, *, group_id: str, slug: str,
                         holding_side: str | None = None,
                         account_id: str | None = None) -> dict | None:
    r = await conn.fetchrow(
        "SELECT pd.* FROM paper_orders po JOIN paper_decisions pd "
        "    ON pd.decision_id = po.decision_id "
        " WHERE po.group_id = $1 AND po.us_market_slug = $2 "
        "   AND po.role = 'ENTRY' AND po.direction = 'BUY' "
        "   AND ($3::text IS NULL OR po.holding_side = $3) "
        "   AND ($4::text IS NULL OR po.account_id = $4) "
        " ORDER BY po.created_at LIMIT 1",
        group_id, slug, holding_side, account_id)
    return dict(r) if r else None


async def outcome_payout(conn, *, account_id: str | None, position_key: str
                         | None, slug: str, holding_side: str) -> dict:
    """Y per contract with its basis, or None with the reason."""
    if position_key:
        r = await conn.fetchrow(
            "SELECT outcome, payout_per_contract FROM paper_settlements "
            " WHERE position_key=$1 ORDER BY version DESC LIMIT 1",
            position_key)
        if r is not None and r["payout_per_contract"] is not None:
            return {"Y": _f(r["payout_per_contract"]),
                    "basis": "THIS_POSITION_SETTLEMENT:%s" % r["outcome"]}
    rows = await conn.fetch(
        "SELECT DISTINCT ON (position_key) outcome, payout_per_contract, "
        "       holding_side FROM paper_settlements "
        " WHERE us_market_slug=$1 ORDER BY position_key, version DESC",
        slug)
    for r in rows:
        if r["holding_side"] == holding_side and \
                r["payout_per_contract"] is not None:
            return {"Y": _f(r["payout_per_contract"]),
                    "basis": "SAME_CONTRACT_SIDE_SETTLEMENT:%s" % r["outcome"]}
    for r in rows:
        if r["holding_side"] != holding_side and r["outcome"] in ("WON",
                                                                  "LOST"):
            return {"Y": 0.0 if r["outcome"] == "WON" else 1.0,
                    "basis": "COMPLEMENT_OF_OPPOSITE_SIDE_SETTLEMENT:%s"
                             % r["outcome"]}
    return {"Y": None, "basis": None, "why": R_NO_Y}


# ═════════════════════════════════════════════════════════════════════
# PAPER
# ═════════════════════════════════════════════════════════════════════

async def paper_postmortems(conn, account_id: str) -> list:
    from .. import bettor_paper_ledger as L
    out = []
    for p in await L.positions(conn, account_id, include_closed=True):
        if (p["open_qty"] or 0) > 1e-9 or (p["bought_qty"] or 0) <= 0:
            continue
        q = float(p["bought_qty"])
        buy_fees = float(p["buy_fees_usd"] or 0)
        buy_gross = float(p["acquisition_cost_usd"] or 0) - buy_fees
        sale_fees = float(p["sale_fees_usd"] or 0)
        sale_gross = float(p["sale_proceeds_net_usd"] or 0) + sale_fees
        st = p.get("settlement") or {}
        payout = float(st.get("payout_usd") or 0)
        realized_gross = sale_gross + payout - buy_gross
        d = await entry_decision(conn, group_id=p["group_id"],
                                 slug=p["us_market_slug"],
                                 holding_side=p["holding_side"],
                                 account_id=account_id)
        terms = decision_terms(d)
        y = await outcome_payout(conn, account_id=account_id,
                                 position_key=p["position_key"],
                                 slug=p["us_market_slug"],
                                 holding_side=p["holding_side"])
        dec = decompose(qty=q, decision_prob=terms["P"],
                        decision_price=terms["pd"],
                        fill_price=buy_gross / q if q else None,
                        buy_fees=buy_fees, sale_fees=sale_fees,
                        realized_gross=realized_gross,
                        payout_per_contract=y["Y"],
                        missing=terms["missing"])
        mgmt = await paper_management(conn, account_id, p)
        closed_at = max(x for x in (p.get("last_fill_at"),
                                    st.get("settled_at"), 0) if x is not None)
        out.append(_record(
            book="PAPER", venue=PAPER_BOOK_LABEL, position_key=p["position_key"],
            account_id=account_id, group_id=p["group_id"],
            slug=p["us_market_slug"], holding_side=p["holding_side"],
            fixture=p.get("fixture"), strategy=p.get("strategy"),
            decision_id=(d or {}).get("decision_id"),
            opened_at=p.get("first_fill_at"), closed_at=closed_at or None,
            qty=q, dec=dec, detail={
                "decision": {k: terms.get(k) for k in (
                    "P", "P_basis", "pd", "pd_basis", "edge_pp_at_decision")},
                "outcome": y,
                "execution": {
                    "fill_avg_price": dec["inputs"]["fill_price"],
                    "decision_price": terms["pd"],
                    "expected_entry_fees_usd": terms.get("fees"),
                    "actual_entry_fees_usd": buy_fees,
                    "fee_deviation_usd": (None if terms.get("fees") is None
                                          else round(buy_fees - terms["fees"],
                                                     6))},
                "management": mgmt,
                "settlement": st or None,
                "ledger_realized_pnl_usd": p.get("realized_pnl_usd")}))
    return out


async def paper_management(conn, account_id: str, p: dict) -> dict:
    """Xavier's reviews and management fills on the position's group."""
    revs = await conn.fetch(
        "SELECT recommendation, count(*) AS n, min(reviewed_at) AS first_at,"
        "       max(reviewed_at) AS last_at "
        "  FROM paper_xavier_reviews WHERE account_id=$1 AND group_id=$2 "
        " GROUP BY recommendation", account_id, p["group_id"])
    fills = await conn.fetch(
        "SELECT role, sum(qty) AS qty, sum(gross_usd) AS gross, "
        "       sum(fee_usd) AS fees FROM paper_fills "
        " WHERE account_id=$1 AND group_id=$2 AND us_market_slug=$3 "
        "   AND holding_side=$4 AND direction='SELL' GROUP BY role",
        account_id, p["group_id"], p["us_market_slug"], p["holding_side"])
    return {"reviews": {r["recommendation"] or "NONE": int(r["n"])
                        for r in revs},
            "first_review_at": min((_ep(r["first_at"]) for r in revs),
                                   default=None),
            "sales": {r["role"]: {"qty": _f(r["qty"]),
                                  "gross_usd": _f(r["gross"]),
                                  "fees_usd": _f(r["fees"])} for r in fills},
            "held_to_settlement": not fills}


# ═════════════════════════════════════════════════════════════════════
# ACTUAL
# ═════════════════════════════════════════════════════════════════════

def held_side_price(intent: str, price) -> float | None:
    """A wire price as the held side's price (a short trades the
    complement)."""
    p = _f(price)
    if p is None:
        return None
    return 1.0 - p if str(intent or "").endswith("_SHORT") else p


async def actual_fills(conn, h: dict) -> list:
    """[{dir, qty, price (held side), fee}] from the venue's own fills."""
    if h["venue"] == "POLYMARKET":
        if not await _regclass(conn, "execmirror_fills"):
            return []
        rows = await conn.fetch(
            "SELECT intent, qty, price, fee_usd FROM execmirror_fills "
            " WHERE group_id=$1 AND us_market_slug=$2", h["group_id"],
            h["us_market_slug"])
        return [{"dir": "BUY" if "_BUY_" in str(r["intent"]) else "SELL",
                 "qty": _f(r["qty"]) or 0.0,
                 "price": held_side_price(r["intent"], r["price"]),
                 "fee": _f(r["fee_usd"]) or 0.0} for r in rows]
    if not await _regclass(conn, "kalshi_live_fills"):
        return []
    rows = await conn.fetch(
        "SELECT f.action, f.count, f.price, f.fee_usd FROM kalshi_live_fills f"
        "  JOIN kalshi_live_intents i ON i.link_id = f.link_id "
        " WHERE i.group_id=$1", h["group_id"])
    # Kalshi reports the traded leg's own price (kalshi_orders.fee_for)
    return [{"dir": "BUY" if str(r["action"]).lower() == "buy" else "SELL",
             "qty": _f(r["count"]) or 0.0, "price": _f(r["price"]),
             "fee": _f(r["fee_usd"]) or 0.0} for r in rows]


async def actual_postmortems(conn) -> list:
    if not await _regclass(conn, "smalllive_handoffs"):
        return []
    out = []
    for h in [dict(r) for r in await conn.fetch(
            "SELECT * FROM smalllive_handoffs WHERE state='CLOSED' "
            " ORDER BY updated_at")]:
        fills = await actual_fills(conn, h)
        buys = [f for f in fills if f["dir"] == "BUY"]
        sells = [f for f in fills if f["dir"] == "SELL"]
        q = sum(f["qty"] for f in buys)
        if q <= 0:
            continue
        buy_gross = sum(f["qty"] * (f["price"] or 0) for f in buys)
        sale_gross = sum(f["qty"] * (f["price"] or 0) for f in sells)
        buy_fees = sum(f["fee"] for f in buys)
        sale_fees = sum(f["fee"] for f in sells)
        held_side = ("SHORT" if str(h.get("opened_intent") or "")
                     .endswith("_SHORT") else "LONG")
        d = await entry_decision(conn, group_id=h["group_id"],
                                 slug=h["us_market_slug"])
        terms = decision_terms(d)
        y = await outcome_payout(conn, account_id=None, position_key=None,
                                 slug=h["us_market_slug"],
                                 holding_side=held_side)
        # a CLOSED actual position holds nothing: no settlement payout
        dec = decompose(qty=q, decision_prob=terms["P"],
                        decision_price=terms["pd"], fill_price=buy_gross / q,
                        buy_fees=buy_fees, sale_fees=sale_fees,
                        realized_gross=sale_gross - buy_gross,
                        payout_per_contract=y["Y"], missing=terms["missing"])
        revs = await conn.fetch(
            "SELECT action, count(*) AS n FROM smalllive_reviews "
            " WHERE handoff_id=$1 GROUP BY action", h["handoff_id"]) \
            if await _regclass(conn, "smalllive_reviews") else []
        out.append(_record(
            book="ACTUAL", venue=h["venue"], position_key=h["handoff_id"],
            account_id=None, group_id=h["group_id"], slug=h["us_market_slug"],
            holding_side=held_side, fixture=None, strategy=None,
            decision_id=(d or {}).get("decision_id"),
            opened_at=_ep(h.get("first_live_fill_at")),
            closed_at=_ep(h.get("updated_at")), qty=q, dec=dec, detail={
                "decision": {k: terms.get(k) for k in (
                    "P", "P_basis", "pd", "pd_basis", "edge_pp_at_decision")},
                "decision_basis": "the paper decision the actual order was "
                                  "derived from (same group)",
                "outcome": y,
                "execution": {"fill_avg_price": buy_gross / q,
                              "decision_price": terms["pd"],
                              "fills": len(fills),
                              "actual_entry_fees_usd": buy_fees},
                "management": {"reviews": {r["action"]: int(r["n"])
                                           for r in revs},
                               "sold_qty": sum(f["qty"] for f in sells)},
                "venue_fill_source": ("execmirror_fills"
                                      if h["venue"] == "POLYMARKET"
                                      else "kalshi_live_fills")}))
    return out


# ═════════════════════════════════════════════════════════════════════
# RECORD + PERSIST
# ═════════════════════════════════════════════════════════════════════

def _record(*, book, venue, position_key, account_id, group_id, slug,
            holding_side, fixture, strategy, decision_id, opened_at,
            closed_at, qty, dec, detail) -> dict:
    comps = dec["components"]
    return {"book": book, "venue": venue, "position_key": position_key,
            "account_id": account_id, "group_id": group_id,
            "us_market_slug": slug, "holding_side": holding_side,
            "fixture": fixture, "strategy": strategy,
            "decision_id": decision_id, "opened_at": opened_at,
            "closed_at": closed_at, "qty": qty,
            "realized_pnl_usd": dec["realized_pnl_usd"],
            **{c: comps[c]["value"] for c in COMPONENTS},
            "unexplained_usd": dec["unexplained_usd"],
            "decomposition_complete": dec["decomposition_complete"],
            "sums_to_realized": dec["sums_to_realized"],
            "components": comps,
            "detail": dict(detail, inputs=dec["inputs"],
                           realized_gross_usd=dec["realized_gross_usd"],
                           edge_at_decision_per_contract=dec[
                               "edge_at_decision_per_contract"],
                           outcome_per_contract=dec["outcome_per_contract"])}


def _sha(rec: dict) -> str:
    return hashlib.sha256(json.dumps(
        {k: v for k, v in rec.items() if k not in ("closed_at",)},
        sort_keys=True, default=str).encode()).hexdigest()[:24]


def _ts(v):
    return None if v is None else float(v)


async def persist(conn, rec: dict, *, now: float) -> str:
    """Upsert; the revision moves only when the content changed."""
    sha = _sha(rec)
    got = await conn.fetchval(
        "INSERT INTO position_postmortems (book, position_key, venue, "
        " account_id, group_id, us_market_slug, holding_side, fixture, "
        " strategy, decision_id, opened_at, closed_at, qty, realized_pnl_usd,"
        " selection_edge_usd, execution_slippage_usd, fees_usd, "
        " management_value_usd, outcome_variance_usd, unexplained_usd, "
        " decomposition_complete, components, detail, content_sha, version,"
        " computed_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,"
        " CASE WHEN $11::float8 IS NULL THEN NULL ELSE to_timestamp($11) END,"
        " CASE WHEN $12::float8 IS NULL THEN NULL ELSE to_timestamp($12) END,"
        " $13,$14,$15,$16,$17,$18,$19,$20,$21,$22::jsonb,$23::jsonb,$24,$25,"
        " to_timestamp($26)) "
        "ON CONFLICT (book, position_key) DO UPDATE SET "
        " venue=EXCLUDED.venue, decision_id=EXCLUDED.decision_id, "
        " closed_at=EXCLUDED.closed_at, qty=EXCLUDED.qty, "
        " realized_pnl_usd=EXCLUDED.realized_pnl_usd, "
        " selection_edge_usd=EXCLUDED.selection_edge_usd, "
        " execution_slippage_usd=EXCLUDED.execution_slippage_usd, "
        " fees_usd=EXCLUDED.fees_usd, "
        " management_value_usd=EXCLUDED.management_value_usd, "
        " outcome_variance_usd=EXCLUDED.outcome_variance_usd, "
        " unexplained_usd=EXCLUDED.unexplained_usd, "
        " decomposition_complete=EXCLUDED.decomposition_complete, "
        " components=EXCLUDED.components, detail=EXCLUDED.detail, "
        " content_sha=EXCLUDED.content_sha, version=EXCLUDED.version, "
        " computed_at=EXCLUDED.computed_at, "
        " revision=position_postmortems.revision + 1 "
        " WHERE position_postmortems.content_sha <> EXCLUDED.content_sha "
        "RETURNING revision",
        rec["book"], rec["position_key"], rec["venue"], rec["account_id"],
        rec["group_id"], rec["us_market_slug"], rec["holding_side"],
        rec["fixture"], rec["strategy"], rec["decision_id"],
        _ts(rec["opened_at"]), _ts(rec["closed_at"]),
        Decimal(str(rec["qty"])), Decimal(str(rec["realized_pnl_usd"])),
        *[None if rec[c] is None else Decimal(str(rec[c]))
          for c in COMPONENTS],
        Decimal(str(rec["unexplained_usd"])), rec["decomposition_complete"],
        json.dumps(rec["components"], default=str),
        json.dumps(rec["detail"], default=str), sha, VERSION, float(now))
    return "WRITTEN" if got is not None else "UNCHANGED"


async def run(conn, *, account_id: str, now: float | None = None) -> dict:
    """Reconstruct and persist every closed position. Never raises."""
    at = float(now if now is not None else time.time())
    out: dict[str, Any] = {"version": VERSION, "at": at,
                           "paper": {}, "actual": {}, "errors": {}}
    if not await _regclass(conn, "position_postmortems"):
        return dict(out, ran=False, why="MIGRATION_209_NOT_APPLIED")
    for book, fn in (("paper", lambda: paper_postmortems(conn, account_id)),
                     ("actual", lambda: actual_postmortems(conn))):
        try:
            recs = await fn()
        except Exception as exc:                                # noqa: BLE001
            out["errors"][book] = "%s: %s" % (type(exc).__name__,
                                              str(exc)[:160])
            continue
        tally: dict[str, int] = {}
        for rec in recs:
            try:
                async with conn.transaction():
                    s = await persist(conn, rec, now=at)
            except Exception as exc:                            # noqa: BLE001
                s = "ERROR"
                out["errors"][rec["position_key"]] = type(exc).__name__
            tally[s] = tally.get(s, 0) + 1
        out[book] = dict(tally, positions=len(recs))
    out["ran"] = True
    return out


async def step(conn, ctx: dict) -> dict:
    """THE SCHEDULED HOOK (paper pass): at most every RUN_EVERY_S, on the
    main paper account only. Never raises."""
    from .. import bettor_paper_ledger as L
    clock = ctx.get("clock") or (lambda: float(ctx["now"]))
    at = float(clock())
    if ctx.get("account_id") != L.ACCOUNT_ID:
        return {"ran": False, "why": "NOT_THE_MAIN_PAPER_ACCOUNT"}
    try:
        last = L._j(await conn.fetchval(
            "SELECT value FROM ingestion_state WHERE key=$1",
            WATERMARK_KEY)) or {}
        if last.get("at") is not None and at - float(last["at"]) < RUN_EVERY_S:
            return {"ran": False, "why": "NOT_DUE", "last_at": last["at"]}
        res = await run(conn, account_id=ctx["account_id"], now=at)
        await conn.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
            WATERMARK_KEY, json.dumps({"at": at, "version": VERSION}))
        return {k: res.get(k) for k in ("ran", "paper", "actual", "errors")}
    except Exception as exc:                                    # noqa: BLE001
        return {"ran": False, "error": "%s: %s" % (type(exc).__name__,
                                                   str(exc)[:200])}


# ═════════════════════════════════════════════════════════════════════
# THE READ (endpoint)
# ═════════════════════════════════════════════════════════════════════

def _row_out(r) -> dict:
    d = dict(r)
    for k in ("components", "detail"):
        d[k] = _j(d.get(k)) or {}
    for k, v in list(d.items()):
        if isinstance(v, Decimal):
            d[k] = float(v)
        elif hasattr(v, "timestamp"):
            d[k] = v.timestamp()
    return d


def summarize(rows: list) -> dict:
    """Per-component totals over measured rows only; counts of unmeasured
    stated beside them (never summed as zero)."""
    # a book with no closed position has no realized P&L: null with its
    # reason, never an invented 0
    out: dict[str, Any] = {"positions": len(rows),
                           "realized_pnl_usd": round(sum(
                               r["realized_pnl_usd"] for r in rows), 6)
                           if rows else None,
                           "unexplained_usd": round(sum(
                               r["unexplained_usd"] for r in rows), 6)
                           if rows else None,
                           "why_null": None if rows
                           else "NO_CLOSED_POSITION_IN_THIS_BOOK",
                           "complete": sum(1 for r in rows
                                           if r["decomposition_complete"])}
    for c in COMPONENTS:
        vals = [r[c] for r in rows if r.get(c) is not None]
        out[c] = {"sum": round(sum(vals), 6) if vals else None,
                  "measured": len(vals), "unmeasured": len(rows) - len(vals)}
    return out


async def postmortems_payload(conn, *, book: str | None = None,
                              limit: int = 100,
                              now: float | None = None) -> dict:
    at = float(now if now is not None else time.time())
    out: dict[str, Any] = {"version": VERSION, "as_of": at,
                           "components": list(COMPONENTS),
                           "identity": ("selection + slippage + fees + "
                                        "management + variance + unexplained"
                                        " = realized net P&L"),
                           "books_never_summed": True}
    if not await _regclass(conn, "position_postmortems"):
        out.update(status="UNAVAILABLE", why="MIGRATION_209_NOT_APPLIED",
                   paper=None, actual=None)
        return out
    limit = max(1, min(int(limit or 100), 1000))
    last = None
    try:
        raw = await conn.fetchval(
            "SELECT value FROM ingestion_state WHERE key=$1", WATERMARK_KEY)
        last = (_j(raw) or {}).get("at")
    except Exception:                                           # noqa: BLE001
        pass
    for b in ("PAPER", "ACTUAL"):
        if book and book.upper() != b:
            continue
        rows = [_row_out(r) for r in await conn.fetch(
            "SELECT * FROM position_postmortems WHERE book=$1 "
            " ORDER BY closed_at DESC NULLS LAST LIMIT $2", b, limit)]
        allrows = [_row_out(r) for r in await conn.fetch(
            "SELECT book, realized_pnl_usd, unexplained_usd, "
            "  decomposition_complete, %s FROM position_postmortems "
            " WHERE book=$1" % ", ".join(COMPONENTS), b)]
        out[b.lower()] = {
            "status": "OK" if rows else "EMPTY",
            "why": None if rows else "NO_CLOSED_%s_POSITION_RECONSTRUCTED"
            % b, "summary": summarize(allrows), "positions": rows}
    out["status"] = "OK"
    out["last_run_at"] = last
    out["schedule"] = "paper pass step 'audrey_postmortems', every %d s" \
        % RUN_EVERY_S
    return out
