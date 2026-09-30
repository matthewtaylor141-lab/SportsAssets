"""THE COMMAND CENTRE PAGES' EXTRA READS. READ-ONLY, BEHIND THE COMMAND READ.

  GET /api/command/agents/derek/orders
      Derek's complete order and decision history, searched and paginated ON
      THE SERVER. `view` is `orders` (every ENTRY order in the funded order
      ledger, whether or not it names a Derek decision), `decisions` (every
      Derek entry decision, with the order that names it, if any) or
      `opportunities` (ENTER decisions inside the qualifying window). Each row
      is normalised into the eight field groups the page shows (FIELD_GROUPS);
      a field the records do not carry is `null` with its reason under
      `not_recorded`, never a zero and never recomputed here.
  GET /api/command/agents/xavier/standing-orders
      The standing protective order records, when this build's schema has
      them; otherwise UNAVAILABLE by name. Every other spread on Xavier's
      latest reviews is listed as a MONITORED ALTERNATIVE -- never as a venue
      order.
  GET /api/command/agents/xavier/payoff-demonstration
      A CONTROLLED DEMONSTRATION position (a demonstration account, never a
      production record) valued by the backend's own whole-position payoff
      code (`xavier_ladder.group_table` over `bettor_indirect_structures`
      legs). `hedge_filled` recomputes the whole position for a partial hedge.
  GET /api/command/agents/audrey/performance
      The ONE authoritative company P&L: the funded book as
      `bettor_funded_book.command_center` reads it (per account; demonstration
      books separate and never added), realised by period from that same
      realised curve, and the account reconciliation reports.

NOTHING HERE WRITES. Every statement is a SELECT; no venue client, order path
or submission switch is imported. The COMMAND read credential is resolved at
request time through `api.app` (importing this module never imports the app).
"""
from __future__ import annotations

import json
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from ..db import get_pool

router = APIRouter()

VERSION = "COMMAND_CENTRE_READS_V1"
OK, EMPTY, UNAVAILABLE = "OK", "EMPTY", "UNAVAILABLE"
DEREK_ORDERS = "/api/command/agents/derek/orders"
XAVIER_STANDING = "/api/command/agents/xavier/standing-orders"
XAVIER_PAYOFF = "/api/command/agents/xavier/payoff-demonstration"
AUDREY_PERFORMANCE = "/api/command/agents/audrey/performance"

#: What "not in this build" means for the standing protective orders: the
#: records are added by a separate change (migration 157); until it is merged
#: and applied, the section says so rather than drawing an empty success.
STANDING_TABLES = ("bettor_standing_order_plans",
                   "bettor_hedge_group_selection",
                   "bettor_standing_order_events",
                   "bettor_standing_capacity_reservations")
STANDING_NOT_IN_BUILD = "standing-order records not in this build"
MONITORED = "MONITORED ALTERNATIVE"

#: The eight field groups each Derek row carries, in the page's order.
FIELD_GROUPS = {
    "instrument": ("event", "market", "side", "settlement_period"),
    "purchase": ("price", "quantity", "dollars_committed", "dollars_filled"),
    "internal": ("probability", "model_version", "at"),
    "pinnacle": ("fair_probability", "at"),
    "combined": ("value", "policy_version", "combination_rule",
                 "gross_edge_vs_price_pp", "rule"),
    "economics": ("gross_edge_pp", "fees_usd", "expected_gross_profit_usd",
                  "expected_net_profit_usd", "expected_return_before_fees",
                  "expected_return_after_fees"),
    "execution": ("order_state", "filled_qty", "average_price",
                  "remaining_qty"),
    "explanation": ("verdict", "why_selected", "alternatives_considered",
                    "blocking_condition", "checks"),
}

#: The funded order ledger's states, grouped the way the page filters them.
STATE_GROUPS = {
    "pending": ("INTENT_RECORDED", "SEND_ATTEMPTED", "ACKNOWLEDGED"),
    "partial": ("PARTIALLY_FILLED",),
    "filled": ("FILLED",),
    "cancelled": ("CANCELLED",),
    "rejected": ("REJECTED", "ABANDONED"),
    "unresolved": ("UNRESOLVED",),
}
LIVE_STATES = ("INTENT_RECORDED", "SEND_ATTEMPTED", "ACKNOWLEDGED",
               "PARTIALLY_FILLED", "UNRESOLVED")

#: Where a V2 (blended) policy records its combined estimate. Read from the
#: decision record; never recomputed here or in the page.
COMBINED_KEYS = ("blended_p", "combined_p", "blended_probability",
                 "combined_probability", "blended_estimate",
                 "combined_estimate")
V2_RULE = ("blended = (internal + Pinnacle) / 2; gross edge = blended - "
           "executable purchase price; enter at >= 5 pp gross edge AND "
           "positive net EV after costs, subject to approved sizing, capital "
           "and model qualification")
ALTERNATIVE_WINDOW_S = 300
MAX_PAGE_SIZE = 100


async def require_read(request: Request) -> str:
    from . import app as A
    return A.require_command(
        bt_command=request.cookies.get("bt_command", ""),
        x_desk_token=request.headers.get("x-desk-token", ""),
        x_admin_token=request.headers.get("x-admin-token", ""))


async def _pool():
    try:
        return await get_pool()
    except Exception as exc:                                    # noqa: BLE001
        raise HTTPException(status_code=503, detail={
            "reason": "NO_DATABASE_POOL", "detail": type(exc).__name__})


async def _regclass(conn, name: str) -> bool:
    return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL",
                                    name))


def _j(v):
    if v is None or isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return v


def _f(v):
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _ep(v):
    """A JSON timestamp (ISO text or epoch) as epoch seconds, or None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if hasattr(v, "timestamp"):
        return v.timestamp()
    try:
        import datetime as _dt
        s = str(v).replace("Z", "+00:00")
        d = _dt.datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=_dt.timezone.utc)
        return d.timestamp()
    except (TypeError, ValueError):
        return None


def _nr(group: dict, reasons: dict, field: str, why: str) -> None:
    group[field] = None
    reasons[field] = why


# ═════════════════════════════════════════════════════════════════════
# DEREK: ONE ROW, EIGHT FIELD GROUPS
# ═════════════════════════════════════════════════════════════════════

def _checks(decision: dict) -> list:
    got = _j((decision or {}).get("checks")) or []
    return [c for c in got if isinstance(c, dict)] if isinstance(got, list) \
        else []


def _check(decision: dict, name: str) -> dict | None:
    for c in _checks(decision):
        if c.get("check") == name:
            return c
    return None


def _find_combined(decision: dict) -> tuple[Any, str | None]:
    """The combined (blended) estimate AS THE RECORD CARRIES IT, and where."""
    d = decision or {}
    for k in COMBINED_KEYS:
        if d.get(k) is not None:
            return d[k], k
    ev = _j(d.get("evidence"))
    if isinstance(ev, dict):
        for k in COMBINED_KEYS:
            if ev.get(k) is not None:
                return ev[k], "evidence.%s" % k
        est = ev.get("estimates")
        if isinstance(est, dict):
            for name in ("combined", "blended"):
                e = est.get(name)
                if isinstance(e, dict):
                    for pk in ("p", "probability", "value"):
                        if e.get(pk) is not None:
                            return e[pk], "evidence.estimates.%s.%s" % (
                                name, pk)
                elif isinstance(e, (int, float)):
                    return e, "evidence.estimates.%s" % name
    for c in _checks(d):
        cev = c.get("evidence")
        if isinstance(cev, dict):
            for k in COMBINED_KEYS:
                if cev.get(k) is not None:
                    return cev[k], "checks[%s].evidence.%s" % (c.get("check"),
                                                               k)
    return None, None


def _combination_rule(decision: dict) -> str | None:
    d = decision or {}
    if d.get("combination_policy"):
        return str(d["combination_policy"])
    ev = _j(d.get("evidence"))
    if isinstance(ev, dict) and ev.get("combination_policy"):
        return str(ev["combination_policy"])
    for c in _checks(d):
        cev = c.get("evidence")
        if isinstance(cev, dict) and cev.get("combination"):
            return str(cev["combination"])
    return None


def normalise_row(*, decision: dict | None, intent: dict | None,
                  fills: dict | None = None,
                  alternatives: list | None = None,
                  label: dict | None = None) -> dict:
    """ONE DEREK ROW FROM ITS RECORDS. Pure (the tests drive it directly).

    `decision` is a derek_entry_decisions row as JSON, `intent` a
    bettor_funded_intents row as JSON (either may be absent), `fills` the
    aggregate of that intent's ENTRY fills. Nothing is invented: a field the
    records do not carry is null with its reason under `not_recorded`."""
    d = dict(decision or {})
    i = dict(intent or {})
    f = dict(fills or {})
    nr: dict[str, dict] = {g: {} for g in FIELD_GROUPS}
    has_d, has_i = bool(d), bool(i)
    no_dec = "this order names no Derek entry decision"
    no_ord = "no entry order names this decision"

    ident = _check(d, "fixture_participants_date_side_period") or {}
    idev = ident.get("evidence") if isinstance(ident.get("evidence"),
                                               dict) else {}

    # ── instrument ──────────────────────────────────────────────────
    ins: dict[str, Any] = {}
    ev_ = d.get("fixture") or i.get("event_key")
    if ev_:
        ins["event"] = ev_
    else:
        _nr(ins, nr["instrument"], "event", "neither the decision nor the "
            "order records a fixture or event key")
    mk = d.get("us_market_slug") or i.get("us_market_slug")
    if mk:
        ins["market"] = mk
    else:
        _nr(ins, nr["instrument"], "market", "no market slug recorded")
    side = d.get("side") or i.get("order_intent")
    if side:
        ins["side"] = side
    else:
        _nr(ins, nr["instrument"], "side", "no side recorded")
    per = idev.get("period")
    if per:
        ins["settlement_period"] = {"period": per,
                                    "basis": idev.get("period_basis"),
                                    "payout_event": idev.get("payout_event")}
    else:
        _nr(ins, nr["instrument"], "settlement_period",
            no_dec if not has_d else
            "the decision's identity check records no settlement period")

    # ── purchase ────────────────────────────────────────────────────
    pur: dict[str, Any] = {}
    if has_i and i.get("limit_price") is not None:
        pur["price"] = _f(i["limit_price"])
        pur["price_basis"] = "the order's limit price"
    elif d.get("executable_price") is not None:
        pur["price"] = _f(d["executable_price"])
        pur["price_basis"] = "the executable price at decision time"
    else:
        _nr(pur, nr["purchase"], "price", "no executable or limit price "
            "recorded")
    if has_i and i.get("quantity") is not None:
        pur["quantity"] = _f(i["quantity"])
        pur["quantity_basis"] = "contracts ordered"
    elif d.get("qty") is not None:
        pur["quantity"] = _f(d["qty"])
        pur["quantity_basis"] = "contracts the decision sized (no order)"
    else:
        _nr(pur, nr["purchase"], "quantity", "no quantity recorded (the "
            "decision priced no quantity)")
    if has_i and i.get("collateral_usd") is not None:
        pur["dollars_committed"] = _f(i["collateral_usd"])
    elif d.get("executable_price") is not None and d.get("qty") is not None:
        pur["dollars_committed"] = None
        pur["cost_at_decision_usd"] = round(
            float(d["executable_price"]) * float(d["qty"]), 6)
        nr["purchase"]["dollars_committed"] = (
            no_ord + ": nothing was committed; cost_at_decision_usd is the "
            "decision's price x quantity")
    else:
        _nr(pur, nr["purchase"], "dollars_committed", no_ord)
    if has_i:
        fq = _f(f.get("filled_qty")) or 0.0
        pur["dollars_filled"] = round(_f(f.get("filled_notional")) or 0.0, 6)
        pur["dollars_filled_basis"] = ("sum of fill qty x fill price over "
                                       "this order's ENTRY fills (%d)"
                                       % len(f.get("fill_ids") or []))
        pur["fill_fees_usd"] = _f(f.get("fill_fees"))
        if not fq:
            pur["dollars_filled_basis"] = "no fill is recorded on this order"
    else:
        _nr(pur, nr["purchase"], "dollars_filled", no_ord)

    # ── internal and Pinnacle estimates ─────────────────────────────
    it: dict[str, Any] = {}
    if d.get("model_p") is not None:
        it["probability"] = _f(d["model_p"])
        it["model_version"] = d.get("model_version")
        it["at"] = d.get("model_at")
        it["qualification"] = d.get("model_qualification")
    else:
        for k in ("probability", "model_version", "at"):
            _nr(it, nr["internal"], k, no_dec if not has_d else (
                "no approved internal estimate on this decision (%s)"
                % (d.get("model_qualification") or "qualification not "
                   "recorded")))
    pn: dict[str, Any] = {}
    if d.get("pinnacle_p") is not None:
        pn["fair_probability"] = _f(d["pinnacle_p"])
        pn["at"] = d.get("pinnacle_at")
        pn["qualification"] = d.get("pinnacle_qualification")
    else:
        for k in ("fair_probability", "at"):
            _nr(pn, nr["pinnacle"], k, no_dec if not has_d else (
                "no qualified Pinnacle probability on this decision (%s)"
                % (d.get("pinnacle_qualification") or "qualification not "
                   "recorded")))

    # ── combined estimate: from the record, never recomputed ────────
    cb: dict[str, Any] = {}
    val, src = _find_combined(d)
    rule_name = _combination_rule(d)
    cb["policy_version"] = d.get("policy_version") if has_d else None
    if not has_d:
        nr["combined"]["policy_version"] = no_dec
    cb["combination_rule"] = rule_name
    if rule_name is None:
        nr["combined"]["combination_rule"] = (
            no_dec if not has_d else "the record names no combination rule")
    if val is not None:
        cb["value"] = _f(val)
        cb["source"] = src
        price = pur.get("price")
        rec_edge = _f(d.get("gross_edge_pp"))
        if rec_edge is not None:
            cb["gross_edge_vs_price_pp"] = round(rec_edge * 100.0, 6)
            cb["gross_edge_basis"] = ("the decision record's gross_edge_pp "
                                      "(blended - executable price)")
        else:
            _nr(cb, nr["combined"], "gross_edge_vs_price_pp",
                "the record carries no gross edge")
        cb["executable_price"] = price
        cb["rule"] = V2_RULE
    else:
        _nr(cb, nr["combined"], "value", no_dec if not has_d else (
            "the decision record carries no combined estimate: policy %s "
            "records the internal and Pinnacle estimates separately "
            "(combination rule: %s)" % (d.get("policy_version"),
                                         rule_name or "not recorded")))
        _nr(cb, nr["combined"], "gross_edge_vs_price_pp",
            "no combined estimate on this record")
        _nr(cb, nr["combined"], "rule", "no combined estimate on this "
            "record, so the blended rule was not the one applied")

    # ── economics ───────────────────────────────────────────────────
    ec: dict[str, Any] = {}
    ge = _f(d.get("gross_edge_pp"))
    if ge is not None:
        ec["gross_edge_pp"] = round(ge * 100.0, 6)
    else:
        _nr(ec, nr["economics"], "gross_edge_pp", no_dec if not has_d else
            "the decision priced no edge (%s)" % (d.get("refusal") or
                                                  "no reason recorded"))
    for k in ("fees_usd", "expected_gross_profit_usd",
              "expected_net_profit_usd"):
        if d.get(k) is not None:
            ec[k] = _f(d[k])
        else:
            _nr(ec, nr["economics"], k, no_dec if not has_d else
                "not recorded on the decision (no priced quantity)")
    cost = None
    if d.get("executable_price") is not None and d.get("qty") is not None:
        cost = float(d["executable_price"]) * float(d["qty"])
    if ec.get("expected_gross_profit_usd") is not None and cost:
        ec["expected_return_before_fees"] = round(
            ec["expected_gross_profit_usd"] / cost, 6)
        ec["expected_return_before_fees_basis"] = (
            "recorded expected gross profit / (recorded executable price x "
            "recorded quantity)")
    else:
        _nr(ec, nr["economics"], "expected_return_before_fees",
            no_dec if not has_d else "no gross profit or cost recorded")
    if d.get("expected_net_roi") is not None:
        ec["expected_return_after_fees"] = _f(d["expected_net_roi"])
        ec["expected_return_after_fees_basis"] = (
            "the record's expected_net_roi: net / (acquisition cost + fees)")
    else:
        _nr(ec, nr["economics"], "expected_return_after_fees",
            no_dec if not has_d else "not recorded on the decision")
    sett = _check(d, "applicable_settlement_rules")
    if sett is not None:
        ec["settlement_states"] = {"status": sett.get("status"),
                                   "detail": sett.get("detail"),
                                   "evidence": sett.get("evidence")}

    # ── execution ───────────────────────────────────────────────────
    ex: dict[str, Any] = {}
    if has_i:
        q = _f(i.get("quantity")) or 0.0
        fq = _f(f.get("filled_qty")) or 0.0
        ex["order_state"] = i.get("state")
        ex["filled_qty"] = round(fq, 6)
        if fq > 0 and f.get("filled_notional") is not None:
            ex["average_price"] = round(float(f["filled_notional"]) / fq, 6)
        else:
            _nr(ex, nr["execution"], "average_price", "no fill recorded")
        ex["remaining_qty"] = round(max(0.0, q - fq), 6)
        ex["remaining_is_live"] = i.get("state") in LIVE_STATES
        if i.get("state") == "UNRESOLVED":
            ex["unresolved_reason"] = i.get("unresolved_reason")
        ex["venue_order_id"] = i.get("venue_order_id")
        ex["sent_at"] = i.get("sent_at")
        ex["created_at"] = i.get("created_at")
    else:
        for k in FIELD_GROUPS["execution"]:
            _nr(ex, nr["execution"], k, no_ord)

    # ── explanation: the recorded rationale only ────────────────────
    xp: dict[str, Any] = {}
    xp["verdict"] = d.get("verdict") if has_d else None
    if not has_d:
        nr["explanation"]["verdict"] = no_dec
    checks = _checks(d)
    if d.get("verdict") == "ENTER":
        passing = [c for c in checks if c.get("status") == "PASS"
                   and c.get("blocks") == "POLICY"]
        why = [str(c.get("detail")) for c in checks
               if c.get("check") in ("conservative_agreement_min_gross_edge",
                                     "positive_net_ev_after_fees",
                                     "blended_min_gross_edge")
               and c.get("status") == "PASS" and c.get("detail")]
        xp["why_selected"] = ("every policy check passed (%d); %s"
                              % (len(passing), "; ".join(why) or
                                 "no check detail recorded"))
    else:
        _nr(xp, nr["explanation"], "why_selected",
            no_dec if not has_d else "not selected: the policy refused it")
    if alternatives is not None and has_d:
        xp["alternatives_considered"] = alternatives
        xp["alternatives_basis"] = (
            "every other decision Derek recorded on the same fixture within "
            "%d s of this one" % ALTERNATIVE_WINDOW_S)
    else:
        _nr(xp, nr["explanation"], "alternatives_considered",
            no_dec if not has_d else "not read for this row")
    blocking = []
    if d.get("refusal"):
        first = next((c for c in checks if c.get("refusal") == d["refusal"]),
                     None)
        blocking.append({"blocks": "POLICY", "refusal": d["refusal"],
                         "detail": (first or {}).get("detail"),
                         "dependency": (first or {}).get("dependency")})
    for c in checks:
        if c.get("blocks") == "EXECUTION_AUTHORITY" and \
                c.get("status") != "PASS":
            blocking.append({"blocks": "EXECUTION_AUTHORITY",
                             "check": c.get("check"),
                             "refusal": c.get("refusal"),
                             "detail": c.get("detail"),
                             "dependency": c.get("dependency")})
    if has_i and i.get("state") in ("REJECTED", "ABANDONED", "UNRESOLVED"):
        blocking.append({"blocks": "ORDER", "state": i.get("state"),
                         "detail": i.get("unresolved_reason")})
    if blocking:
        xp["blocking_condition"] = blocking
    elif has_d or has_i:
        xp["blocking_condition"] = []
        xp["blocking_note"] = "no blocking condition is recorded"
    else:
        _nr(xp, nr["explanation"], "blocking_condition", "no record")
    xp["checks"] = [{k: c.get(k) for k in ("check", "status", "blocks",
                                            "detail", "refusal",
                                            "dependency")} for c in checks]

    evidence = []
    if has_d:
        evidence.append({"kind": "derek_entry_decisions",
                         "id": d.get("decision_id"),
                         "href": "/api/command/agents/derek/decisions/%s"
                                 % d.get("decision_id")})
    if has_i:
        evidence.append({"kind": "bettor_funded_intents",
                         "id": i.get("intent_id"),
                         "href": "/api/command/xavier/%s" % i.get("intent_id")})
    book = None
    if has_i:
        try:
            from .. import bettor_funded_book as FB
            book = FB.classify_book(i.get("account_id"))["book_class"]
        except Exception:                                       # noqa: BLE001
            book = None
    return {
        "record": {"decision_id": d.get("decision_id"),
                   "intent_id": i.get("intent_id"),
                   "decided_at": d.get("decided_at"),
                   "created_at": i.get("created_at"),
                   "decided_by": d.get("decided_by"),
                   "book_class": book},
        "instrument": ins, "purchase": pur, "internal": it, "pinnacle": pn,
        "combined": cb, "economics": ec, "execution": ex, "explanation": xp,
        "not_recorded": {g: v for g, v in nr.items() if v},
        "evidence": evidence, "label": label or label_inputs_of(
            instrument=ins, purchase=pur, execution=ex)}


def label_inputs_of(*, instrument: dict, purchase: dict,
                    execution: dict) -> dict:
    """What the shared label resolver needs from one row (the resolved label
    replaces this when the catalogue is read)."""
    fq = execution.get("filled_qty") if isinstance(execution, dict) else None
    filled = isinstance(fq, (int, float)) and fq > 0
    side = instrument.get("side")
    return {"pending": True, "market_slug": instrument.get("market"),
            "intent": side if str(side or "").startswith("ORDER_INTENT_")
            else None,
            "contracts": fq if filled else purchase.get("quantity"),
            "avg_price": (execution.get("average_price") if filled
                          else purchase.get("price")),
            "invested": (purchase.get("dollars_filled") if filled else
                         purchase.get("dollars_committed")
                         if purchase.get("dollars_committed") is not None
                         else purchase.get("cost_at_decision_usd"))}


async def label_rows(conn, rows: list) -> None:
    """Replace each row's label inputs with the shared resolver's label."""
    from .. import market_labels as ML
    todo = [r for r in rows if isinstance(r.get("label"), dict)
            and r["label"].get("pending")]
    if not todo:
        return
    got = await ML.resolve_many(conn, [r["label"] for r in todo])
    for r, lbl in zip(todo, got):
        r["label"] = lbl


_DEC_COLS = "to_jsonb(d) - 'features'"
_INT_COLS = "to_jsonb(i) - 'raw' - 'effective_digest'"
_FILLS = ("LEFT JOIN LATERAL (SELECT sum(qty)::float8 AS filled_qty, "
          "  sum(qty * price)::float8 AS filled_notional, "
          "  sum(fee_usd)::float8 AS fill_fees, "
          "  array_agg(fill_id ORDER BY at, fill_id) AS fill_ids "
          "  FROM bettor_funded_fills ff WHERE ff.intent_id = i.intent_id "
          "   AND ff.direction = 'ENTRY') f ON true")


def _like(q: str) -> str:
    q = (q or "").strip()[:200]
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace(
        "_", "\\_") + "%"


async def derek_orders(conn, *, view: str = "orders", q: str = "",
                       state: str = "all", verdict: str = "all",
                       page: int = 1, page_size: int = 25,
                       window_min: int = 30) -> dict:
    page = max(1, int(page or 1))
    page_size = max(1, min(MAX_PAGE_SIZE, int(page_size or 25)))
    window_min = max(1, min(1440, int(window_min or 30)))
    out: dict[str, Any] = {
        "read_only": True, "read_at": time.time(), "version": VERSION,
        "view": view, "q": q, "state": state, "verdict": verdict,
        "page": page, "page_size": page_size, "fields": FIELD_GROUPS,
        "state_groups": STATE_GROUPS, "rows": [], "total": 0, "pages": 0}
    has_i = await _regclass(conn, "bettor_funded_intents")
    has_f = await _regclass(conn, "bettor_funded_fills")
    has_d = await _regclass(conn, "derek_entry_decisions")
    counts: dict[str, Any] = {}
    if has_i:
        counts["orders_by_state"] = {r["state"]: int(r["n"]) for r in
                                     await conn.fetch(
            "SELECT state, count(*) AS n FROM bettor_funded_intents "
            " WHERE kind = 'ENTRY' GROUP BY state")}
    if has_d:
        counts["decisions_by_verdict"] = {r["verdict"]: int(r["n"]) for r in
                                          await conn.fetch(
            "SELECT verdict, count(*) AS n FROM derek_entry_decisions "
            " GROUP BY verdict")}
    out["counts"] = counts
    like = _like(q)
    off = (page - 1) * page_size
    fills = _FILLS if has_f else (
        "LEFT JOIN LATERAL (SELECT NULL::float8 AS filled_qty, "
        "NULL::float8 AS filled_notional, NULL::float8 AS fill_fees, "
        "NULL::text[] AS fill_ids) f ON true")
    if view == "orders":
        if not has_i:
            out.update(status=UNAVAILABLE, why=(
                "bettor_funded_intents is absent: the funded order ledger "
                "(migration 125) is not applied here"))
            return out
        states = None
        if state in STATE_GROUPS:
            states = list(STATE_GROUPS[state])
        join = ("LEFT JOIN derek_entry_decisions d "
                "  ON d.decision_id = i.decision_ref->>'derek_decision_id'"
                if has_d else "")
        dcols = _DEC_COLS if has_d else "NULL::jsonb"
        text = ("concat_ws(' ', i.intent_id, i.venue_order_id, i.state, "
                "i.us_market_slug, i.event_key, i.order_intent, "
                "i.decision_ref->>'derek_decision_id'%s)"
                % (", d.fixture, d.side, d.verdict, d.refusal, "
                   "d.policy_version" if has_d else ""))
        rows = await conn.fetch(
            "SELECT %s AS intent, %s AS decision, f.*, "
            "       count(*) OVER () AS total "
            "  FROM bettor_funded_intents i %s %s "
            " WHERE i.kind = 'ENTRY' "
            "   AND ($1::text[] IS NULL OR i.state = ANY($1::text[])) "
            "   AND %s ILIKE $2 "
            " ORDER BY i.created_at DESC, i.intent_id "
            " LIMIT $3 OFFSET $4" % (_INT_COLS, dcols, join, fills, text),
            states, like, page_size, off)
        empty_why = ("no entry order has been recorded in the funded order "
                     "ledger (funded submission is disabled)"
                     if not counts.get("orders_by_state") else
                     "no entry order matches this filter")
    elif view in ("decisions", "opportunities"):
        if not has_d:
            out.update(status=UNAVAILABLE, why=(
                "derek_entry_decisions is absent: migration 153 is not "
                "applied here"))
            return out
        verd = None
        if view == "opportunities":
            verd = "ENTER"
        elif verdict in ("ENTER", "REFUSE"):
            verd = verdict
        win = ("AND d.decided_at > now() - make_interval(mins => $5::int)"
               if view == "opportunities" else "AND $5::int IS NOT NULL")
        ij = ("LEFT JOIN LATERAL (SELECT * FROM bettor_funded_intents ii "
              "  WHERE ii.kind = 'ENTRY' "
              "    AND ii.decision_ref->>'derek_decision_id' = d.decision_id "
              "  ORDER BY ii.created_at DESC LIMIT 1) i ON true"
              if has_i else
              "LEFT JOIN LATERAL (SELECT NULL::text AS intent_id) i ON true")
        icols = _INT_COLS if has_i else "NULL::jsonb"
        text = ("concat_ws(' ', d.decision_id, d.fixture, d.us_market_slug, "
                "d.side, d.verdict, d.refusal, d.policy_version, "
                "d.model_version%s)"
                % (", i.intent_id, i.venue_order_id, i.state"
                   if has_i else ""))
        rows = await conn.fetch(
            "SELECT %s AS decision, %s AS intent, f.*, "
            "       count(*) OVER () AS total "
            "  FROM derek_entry_decisions d %s %s "
            " WHERE ($1::text IS NULL OR d.verdict = $1) %s "
            "   AND %s ILIKE $2 "
            " ORDER BY d.decided_at DESC, d.decision_id "
            " LIMIT $3 OFFSET $4"
            % (_DEC_COLS, icols, ij,
               fills if has_i else fills.replace("i.intent_id",
                                                 "NULL::text"),
               win, text),
            verd, like, page_size, off, window_min)
        if view == "opportunities":
            empty_why = ("no ENTER decision in the last %d minutes: no "
                         "candidate currently qualifies" % window_min)
        else:
            empty_why = ("no entry decision has been recorded"
                         if not counts.get("decisions_by_verdict") else
                         "no decision matches this filter")
    else:
        raise HTTPException(status_code=422, detail={
            "reason": "UNKNOWN_VIEW", "views": ["orders", "decisions",
                                                "opportunities"]})
    total = int(rows[0]["total"]) if rows else 0
    alts: dict[str, list] = {}
    dec_ids = [(_j(r["decision"]) or {}).get("decision_id") for r in rows]
    dec_ids = [x for x in dec_ids if x]
    if dec_ids and has_d:
        for a in await conn.fetch(
                "SELECT x.id AS for_id, d2.decision_id, d2.us_market_slug, "
                "       d2.side, d2.verdict, d2.refusal, d2.gross_edge_pp, "
                "       d2.decided_at "
                "  FROM unnest($1::text[]) AS x(id) "
                "  JOIN derek_entry_decisions d ON d.decision_id = x.id "
                "  JOIN LATERAL (SELECT * FROM derek_entry_decisions d2 "
                "   WHERE d2.fixture = d.fixture "
                "     AND d2.decision_id <> d.decision_id "
                "     AND d2.decided_at BETWEEN "
                "         d.decided_at - make_interval(secs => $2) AND "
                "         d.decided_at + make_interval(secs => $2) "
                "   ORDER BY abs(extract(epoch FROM d2.decided_at "
                "                              - d.decided_at)) "
                "   LIMIT 6) d2 ON true", dec_ids,
                float(ALTERNATIVE_WINDOW_S)):
            alts.setdefault(a["for_id"], []).append({
                "decision_id": a["decision_id"],
                "market": a["us_market_slug"], "side": a["side"],
                "verdict": a["verdict"], "refusal": a["refusal"],
                "gross_edge_pp": (None if a["gross_edge_pp"] is None
                                  else round(float(a["gross_edge_pp"])
                                             * 100.0, 6)),
                "decided_at": a["decided_at"].isoformat(),
                "href": "/api/command/agents/derek/decisions/%s"
                        % a["decision_id"]})
    for r in rows:
        dec = _j(r["decision"]) or None
        it = _j(r["intent"]) or None
        if it is not None and not it.get("intent_id"):
            it = None
        fl = {"filled_qty": r["filled_qty"],
              "filled_notional": r["filled_notional"],
              "fill_fees": r["fill_fees"],
              "fill_ids": list(r["fill_ids"] or [])}
        out["rows"].append(normalise_row(
            decision=dec, intent=it, fills=fl,
            alternatives=(alts.get((dec or {}).get("decision_id"), [])
                          if dec else None)))
    try:
        await label_rows(conn, out["rows"])
    except Exception as exc:                                    # noqa: BLE001
        out["label_error"] = type(exc).__name__
    out["total"] = total
    out["pages"] = (total + page_size - 1) // page_size if total else 0
    out["evidence"] = [e for row in out["rows"] for e in row["evidence"]]
    if not out["rows"]:
        out.update(status=EMPTY, why=empty_why)
    else:
        out.update(status=OK, why=None)
    return out


@router.get(DEREK_ORDERS, dependencies=[Depends(require_read)])
async def derek_orders_route(
        response: Response,
        view: str = Query(default="orders",
                          pattern="^(orders|decisions|opportunities)$"),
        q: str = Query(default="", max_length=200),
        state: str = Query(default="all", max_length=20),
        verdict: str = Query(default="all", max_length=10),
        page: int = Query(default=1, ge=1, le=100000),
        page_size: int = Query(default=25, ge=1, le=MAX_PAGE_SIZE),
        window_min: int = Query(default=30, ge=1, le=1440)) -> dict:
    response.headers["Cache-Control"] = "no-store"
    pool = await _pool()
    async with pool.acquire() as conn:
        return await derek_orders(conn, view=view, q=q, state=state,
                                  verdict=verdict, page=page,
                                  page_size=page_size, window_min=window_min)


# ═════════════════════════════════════════════════════════════════════
# XAVIER: STANDING PROTECTIVE ORDERS, TRUTHFULLY
# ═════════════════════════════════════════════════════════════════════

def _monitored(decisions: list) -> list:
    """Every spread alternative on the latest reviews, labelled MONITORED
    ALTERNATIVE: a valued candidate, never a venue order."""
    out = []
    for d in decisions or []:
        for a in d.get("alternatives") or []:
            if a.get("action") != "ACQUIRE_INDIRECT_HEDGE":
                continue
            sc = dict(a.get("settlement_compatibility") or {})
            out.append({"label": MONITORED,
                        "is_a_venue_order": False,
                        "xavier_decision_id": d.get("xavier_decision_id"),
                        "portfolio_group_id": d.get("portfolio_group_id"),
                        "candidate_id": a.get("candidate_id"),
                        "line": sc.get("line"), "backs": sc.get("backs"),
                        "expected_net_usd": a.get("expected_net_usd"),
                        "increment_vs_hold_usd": a.get(
                            "increment_vs_hold_usd"),
                        "worst_case_established_usd": a.get(
                            "worst_case_established_usd"),
                        "p_both_legs_win": a.get("p_both_legs_win"),
                        "rankable": a.get("rankable"),
                        "blocker": a.get("blocker"),
                        "href": "/api/command/agents/xavier/decisions/%s"
                                % d.get("xavier_decision_id")})
    return out


def _row(r) -> dict:
    out = {}
    for k, v in dict(r).items():
        if hasattr(v, "isoformat"):
            v = v.isoformat()
        elif v is not None and type(v).__name__ == "Decimal":
            v = float(v)
        elif isinstance(v, str) and k in ("evidence", "policy", "capacity",
                                          "floor", "desirability",
                                          "release_evidence"):
            v = _j(v)
        out[k] = v
    return out


async def xavier_standing(conn) -> dict:
    out: dict[str, Any] = {"read_only": True, "read_at": time.time(),
                           "version": VERSION,
                           "rule": ("at most ONE live-or-potentially-live "
                                    "protective hedge order per group; every "
                                    "other spread is a MONITORED ALTERNATIVE; "
                                    "the first partial hedge fill pins the "
                                    "instrument; cancel-pending and "
                                    "ambiguous orders consume capacity until "
                                    "reconciled")}
    # THE MONITORED ALTERNATIVES come from Xavier's own latest decisions,
    # whichever way the standing records answer.
    decisions: list = []
    try:
        from .. import bettor_xavier as XV
        if await XV.has_schema(conn):
            got = await XV.latest_decisions(conn, limit=50)
            decisions = list(got.get("positions") or [])
    except Exception as exc:                                    # noqa: BLE001
        out["monitored_why"] = type(exc).__name__
    out["monitored_alternatives"] = _monitored(decisions)
    missing = [t for t in STANDING_TABLES if not await _regclass(conn, t)]
    if missing:
        out.update(status=UNAVAILABLE, why=STANDING_NOT_IN_BUILD,
                   detail=("these tables are absent here: %s -- the standing "
                           "protective order change (migration 157) is not "
                           "merged into this build" % ", ".join(missing)),
                   groups=[])
        return out
    plans = [_row(r) for r in await conn.fetch(
        "SELECT p.plan_id, p.group_id, p.primary_intent_id, "
        "       p.xavier_decision_id, p.policy_version, p.candidate_id, "
        "       p.venue_slug, p.order_intent, p.wire_limit_price, "
        "       p.cost_price, p.quantity, p.tif, p.good_till_time, "
        "       p.floor_class, p.hedge_intent_id, p.created_at, "
        "       i.state AS order_state, i.venue_order_id, "
        "       (SELECT e.lifecycle_state FROM bettor_standing_order_events e"
        "         WHERE e.plan_id = p.plan_id "
        "         ORDER BY e.event_id DESC LIMIT 1) AS lifecycle_state, "
        "       (SELECT e.occurred_at FROM bettor_standing_order_events e "
        "         WHERE e.plan_id = p.plan_id "
        "         ORDER BY e.event_id DESC LIMIT 1) AS lifecycle_at "
        "  FROM bettor_standing_order_plans p "
        "  LEFT JOIN bettor_funded_intents i ON i.intent_id = "
        "       p.hedge_intent_id "
        " ORDER BY p.created_at DESC LIMIT 200")]
    sel = [_row(r) for r in await conn.fetch(
        "SELECT group_id, selection_seq, candidate_id, venue_slug, "
        "       hedge_intent_id, plan_id, selected_by, first_fill_id, "
        "       selected_at FROM bettor_hedge_group_selection "
        " ORDER BY group_id, selection_seq")]
    cap = [_row(r) for r in await conn.fetch(
        "SELECT reservation_id, group_id, plan_id, hedge_intent_id, "
        "       reserved_qty, reserved_collateral_usd, state, opened_at, "
        "       released_at, release_reason "
        "  FROM bettor_standing_capacity_reservations "
        " ORDER BY opened_at DESC LIMIT 200")]
    groups: dict[str, dict] = {}
    for p in plans:
        g = groups.setdefault(p["group_id"], {
            "group_id": p["group_id"], "plans": [], "pinned": None,
            "capacity": [], "live_order_count": 0})
        live = p.get("order_state") in LIVE_STATES
        p["counts_as_live"] = live
        g["live_order_count"] += 1 if live else 0
        p["label"] = ("STANDING PROTECTIVE ORDER" if p.get("hedge_intent_id")
                      else "PLANNED, NOT SENT")
        p["href"] = ("/api/command/xavier/%s" % p["hedge_intent_id"]
                     if p.get("hedge_intent_id") else
                     "/api/command/agents/xavier/decisions/%s"
                     % p["xavier_decision_id"])
        g["plans"].append(p)
    for s in sel:
        g = groups.setdefault(s["group_id"], {
            "group_id": s["group_id"], "plans": [], "pinned": None,
            "capacity": [], "live_order_count": 0})
        if s.get("selected_by") == "FIRST_FILL" and g["pinned"] is None:
            g["pinned"] = s
        g.setdefault("selections", []).append(s)
    for c in cap:
        g = groups.setdefault(c["group_id"], {
            "group_id": c["group_id"], "plans": [], "pinned": None,
            "capacity": [], "live_order_count": 0})
        g["capacity"].append(c)
    for g in groups.values():
        held = [c for c in g["capacity"] if c.get("state") == "RESERVED"]
        g["capacity_consumed"] = {
            "reserved_qty": round(sum(float(c.get("reserved_qty") or 0)
                                      for c in held), 6),
            "reserved_collateral_usd": round(sum(float(
                c.get("reserved_collateral_usd") or 0) for c in held), 6),
            "why": ("a reservation is released only when the venue confirms "
                    "the order terminal (or it was never sent): a "
                    "cancel-pending or ambiguous order keeps consuming it")}
        g["invariant_holds"] = g["live_order_count"] <= 1
    try:
        from .. import market_labels as ML
        lbls = await ML.resolve_many(conn, [{
            "market_slug": p.get("venue_slug"), "intent": p.get("order_intent"),
            "contracts": p.get("quantity"), "avg_price": p.get("wire_limit_price"),
            "invested": (float(p["quantity"]) * float(p["wire_limit_price"])
                         if p.get("quantity") is not None
                         and p.get("wire_limit_price") is not None else None)}
            for p in plans])
        for p, lbl in zip(plans, lbls):
            p["label_resolved"] = lbl
    except Exception as exc:                                    # noqa: BLE001
        out["label_error"] = type(exc).__name__
    out["groups"] = list(groups.values())
    if not out["groups"]:
        out.update(status=EMPTY, why=("no standing protective order has "
                                      "been planned: no group holds a "
                                      "position to protect"))
    else:
        out.update(status=OK, why=None)
    return out


@router.get(XAVIER_STANDING, dependencies=[Depends(require_read)])
async def xavier_standing_route(response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    pool = await _pool()
    async with pool.acquire() as conn:
        return await xavier_standing(conn)


# ═════════════════════════════════════════════════════════════════════
# XAVIER: THE PAYOFF DEMONSTRATION (backend payoff code, demo account)
# ═════════════════════════════════════════════════════════════════════

DEMO_ACCOUNT = "DEMONSTRATION-COMMAND-CENTRE"
DEMO_LABEL = ("DEMONSTRATION · CONTROLLED EXAMPLE POSITION ON A "
              "DEMONSTRATION ACCOUNT · NOT A PRODUCTION RECORD · NO ORDER")
DEMO_POSITION = {
    "account_id": DEMO_ACCOUNT,
    "fixture": "demonstration: Yankees (A) v Red Sox (B), MLB full game",
    "primary": {"what": "Yankees moneyline", "price": 0.50, "qty": 2000},
    "hedge": {"what": "Red Sox +2.5", "price": 0.40, "qty": 2000},
}


def _demo_legs():
    from fractions import Fraction
    from .. import bettor_indirect_structures as IS
    prim = IS.Leg(condition_id="demonstration:yankees-moneyline",
                  fixture_id="demonstration:nyy-bos", kind=IS.KIND_MONEYLINE,
                  period=IS.PERIOD_FULL, overtime=IS.OT_INCLUDED, backs="A",
                  quantity=1, cost_cents_per_unit=50)
    hedge = IS.Leg(condition_id="demonstration:red-sox-plus-2.5",
                   fixture_id="demonstration:nyy-bos", kind=IS.KIND_SPREAD,
                   period=IS.PERIOD_FULL, overtime=IS.OT_INCLUDED, backs="B",
                   line=Fraction(-5, 2), quantity=1, cost_cents_per_unit=40)
    return prim, hedge


def _outcome_of(cents) -> str | None:
    c = list(cents or [])
    if c == [100, 0]:
        return "Yankees win by 3+"
    if c == [100, 100]:
        return "Yankees win by 1-2"
    if c == [0, 100]:
        return "Red Sox win"
    if c == [0, 0]:
        return "neither leg pays"
    return None


def payoff_demonstration(hedge_filled: int = 2000) -> dict:
    """The demonstration position valued by `xavier_ladder.group_table`. Pure
    (no database). The hedge quantity is what has CONFIRMED fills; the table
    is recomputed for the whole position at those quantities."""
    from ..agents import xavier_ladder as XL
    hq = max(0, min(int(hedge_filled), int(DEMO_POSITION["hedge"]["qty"])))
    pq = int(DEMO_POSITION["primary"]["qty"])
    pp, hp = DEMO_POSITION["primary"]["price"], DEMO_POSITION["hedge"]["price"]
    basis = round(pq * pp + hq * hp, 6)
    prim, hedge = _demo_legs()
    table = XL.group_table({"held_leg": prim, "hedge_held_leg": hedge,
                            "sport_permits_tie": False,
                            "fixture_can_void": True},
                           kept_p=pq, kept_h=hq, cash=0.0, total_basis=basis)
    outcomes: dict[str, dict] = {}
    for r in table.get("rows") or []:
        if r.get("state") != "REGULAR" or not r.get("established"):
            continue
        name = _outcome_of(r.get("per_leg_cents_per_unit"))
        if name is None:
            continue
        o = outcomes.setdefault(name, {
            "outcome": name, "payout_usd": r["combined_payout_usd"],
            "profit_before_fees_usd": r["net_pnl_usd"], "regions": []})
        o["regions"].append(r["region"])
    order = ["Yankees win by 3+", "Yankees win by 1-2", "Red Sox win"]
    rows = [outcomes[k] for k in order if k in outcomes]
    ordinary = [r["profit_before_fees_usd"] for r in rows]
    matched = min(pq, hq)
    return {
        "read_only": True, "label": DEMO_LABEL, "book_class":
        "CONTROLLED_DEMONSTRATION", "account_id": DEMO_ACCOUNT,
        "production": False, "position": DEMO_POSITION,
        "confirmed_fills": {"primary_qty": pq, "hedge_qty": hq},
        "cost": {"primary_usd": round(pq * pp, 6),
                 "hedge_usd": round(hq * hp, 6),
                 "total_before_fees_usd": basis},
        "outcomes": rows,
        "minimum_ordinary_settlement_profit_before_fees_usd": (
            min(ordinary) if ordinary else None),
        "both_win_profit_before_fees_usd": (
            outcomes.get("Yankees win by 1-2") or {}).get(
                "profit_before_fees_usd"),
        "matched_qty": matched, "unpaired_qty": abs(pq - hq),
        "fully_protected": hq >= pq,
        "fees": ("not included: every profit figure here is BEFORE fees; "
                 "the venue's fee schedule is applied to real fills"),
        "is_a_settlement_payoff_floor_not_realised_cash": True,
        "exceptional_settlement": table.get("unestablished_states"),
        "exceptional_note": ("a cancelled, abandoned or postponed fixture is "
                             "outside the ordinary table: its payout is not "
                             "established by these legs' captured rules, so "
                             "it is shown separately as a bounded range and "
                             "never assumed"),
        "table": table,
        "computed_by": "sportsassets.agents.xavier_ladder.group_table",
        "protection_note": (
            "full protection is shown only when confirmed hedge fills match "
            "the primary quantity; a partial hedge leaves the unpaired "
            "primary in every row")}


@router.get(XAVIER_PAYOFF, dependencies=[Depends(require_read)])
async def xavier_payoff_route(response: Response,
                              hedge_filled: int = Query(default=2000, ge=0,
                                                        le=2000)) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return payoff_demonstration(hedge_filled)


# ═════════════════════════════════════════════════════════════════════
# AUDREY: THE ONE AUTHORITATIVE COMPANY P&L
# ═════════════════════════════════════════════════════════════════════

PERIODS = (("daily", 86400.0), ("weekly", 7 * 86400.0),
           ("monthly", 30 * 86400.0))


def periods_from_curve(curve: list, *, now: float) -> dict:
    """Realised by period from the book's own realised curve. Pure."""
    out = {}
    for name, span in PERIODS:
        tot, n = 0.0, 0
        for c in curve or []:
            at = _ep(c.get("at"))
            if at is not None and at >= now - span:
                tot += float(c.get("net_usd") or 0.0)
                n += 1
        out[name] = {"realised_usd": round(tot, 6), "booked_results": n,
                     "window_s": span}
    out["cumulative"] = {"realised_usd": round(sum(
        float(c.get("net_usd") or 0.0) for c in curve or []), 6),
        "booked_results": len(curve or [])}
    return out


def _book_digest(b: dict, *, now: float) -> dict:
    p = b.get("pnl") or {}
    e = b.get("exposure") or {}
    return {"account_id": b.get("account_id"), "venue": b.get("venue"),
            "book_class": b.get("book_class"),
            "counts_toward_strategy_performance": b.get(
                "counts_toward_strategy_performance"),
            "realised_pnl_usd": p.get("realised_pnl_usd"),
            "realised_is_provisional": p.get("realised_is_provisional"),
            "realised_basis": p.get("realised_basis"),
            "unrealised_pnl_usd": p.get("unrealised_pnl_usd"),
            "unrealised_basis": p.get("unrealised_basis"),
            "fees_usd": p.get("fees_usd"),
            "fills_with_provisional_fees": p.get(
                "fills_with_provisional_fees"),
            "max_drawdown_usd": p.get("max_drawdown_usd"),
            "exposure": {k: e.get(k) for k in (
                "total_exposure_usd", "held_basis_usd", "outstanding_orders",
                "reserved_usd", "reserved_collateral_usd", "unresolved",
                "ok", "refusal") if k in e},
            "periods": periods_from_curve(p.get("realised_curve") or [],
                                          now=now),
            "closed_positions": p.get("closed_positions"),
            "open_position_net_cash_usd": p.get("open_position_net_cash_usd"),
            "href": "/api/command/agents/audrey/performance"}


async def audrey_performance(conn, *, now: float | None = None) -> dict:
    at = float(now if now is not None else time.time())
    out: dict[str, Any] = {
        "read_only": True, "read_at": at, "version": VERSION,
        "authority": ("ONE company P&L: the funded book and its economics "
                      "ledger (bettor_funded_book.command_center), per "
                      "account. A position's result is booked once, in the "
                      "book; it is never counted for both Derek and Xavier. "
                      "Demonstration books are listed separately and never "
                      "added")}
    if not await _regclass(conn, "bettor_funded_intents"):
        out.update(status=UNAVAILABLE, why=(
            "the funded order ledger is absent here (migration 125)"))
        return out
    try:
        from .. import bettor_funded_book as FB
        cc = await FB.command_center(conn)
    except Exception as exc:                                    # noqa: BLE001
        out.update(status=UNAVAILABLE, why="the funded book read failed: %s"
                   % type(exc).__name__)
        return out
    out["label"] = cc.get("label")
    out["funded_books"] = [_book_digest(b, now=at)
                           for b in cc.get("funded_books") or []]
    out["demonstration_books"] = [dict(_book_digest(b, now=at),
                                       label="DEMONSTRATION")
                                  for b in cc.get("demonstration_books")
                                  or []]
    out["unresolved_discrepancies"] = cc.get("unresolved_discrepancies")
    out["unresolved_discrepancy_count"] = cc.get(
        "unresolved_discrepancy_count")
    out["funded_capability"] = cc.get("funded_capability")
    recon = []
    if await _regclass(conn, "bettor_account_reconciliation_reports"):
        recon = [_row(r) for r in await conn.fetch(
            "SELECT DISTINCT ON (account_id, venue) report_id, account_id, "
            "       venue, recorded_at, recorded_by, authoritative, "
            "       would_be_eligible, blocking_count, report_sha "
            "  FROM bettor_account_reconciliation_reports "
            " ORDER BY account_id, venue, recorded_at DESC")]
        for r in recon:
            try:
                r["book_class"] = FB.classify_book(r["account_id"])[
                    "book_class"]
            except Exception:                                   # noqa: BLE001
                r["book_class"] = None
        out["reconciliation"] = recon
        out["reconciliation_why"] = (None if recon else
                                     "no account reconciliation report has "
                                     "been recorded")
    else:
        out["reconciliation"] = None
        out["reconciliation_why"] = ("bettor_account_reconciliation_reports "
                                     "is absent (migration 145)")
    if not out["funded_books"]:
        out.update(status=EMPTY, why=(
            "no funded book exists: no funded order has been recorded, so "
            "there is no company P&L to report (this is not a zero result)"))
    else:
        out.update(status=OK, why=None)
    return out


@router.get(AUDREY_PERFORMANCE, dependencies=[Depends(require_read)])
async def audrey_performance_route(response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    pool = await _pool()
    async with pool.acquire() as conn:
        return await audrey_performance(conn)


# ═════════════════════════════════════════════════════════════════════
# THE SHARED LABELS, FOR ANY MARKET OR ORDER A PAGE SHOWS
# ═════════════════════════════════════════════════════════════════════

LABELS = "/api/command/agents/labels"
MAX_LABEL_ITEMS = 60


async def labels_for(conn, items: list[str]) -> dict:
    """`intent:<intent_id>` or `slug:<market_slug>|<order intent>` -> the
    shared resolver's label, keyed by the item as asked."""
    from .. import market_labels as ML
    items = [str(i)[:300] for i in items if i][:MAX_LABEL_ITEMS]
    by_intent = await ML.items_for_intents(
        conn, [i[7:] for i in items if i.startswith("intent:")])
    asks, keys = [], []
    for it in items:
        if it.startswith("intent:"):
            got = by_intent.get(it[7:])
            if got is None:
                continue
            asks.append(got)
        elif it.startswith("slug:"):
            slug, _, intent = it[5:].partition("|")
            asks.append({"market_slug": slug, "intent": intent or None})
        else:
            continue
        keys.append(it)
    got = await ML.resolve_many(conn, asks)
    out = {k: v for k, v in zip(keys, got)}
    missing = [i for i in items if i not in out]
    return {"read_only": True, "read_at": time.time(), "version": ML.VERSION,
            "labels": out, "unresolved_items": missing,
            "logos": ML.LOGO_NOTE}


@router.get(LABELS, dependencies=[Depends(require_read)])
async def labels_route(response: Response,
                       item: list[str] = Query(default=[])) -> dict:
    response.headers["Cache-Control"] = "no-store"
    pool = await _pool()
    async with pool.acquire() as conn:
        return await labels_for(conn, item)
