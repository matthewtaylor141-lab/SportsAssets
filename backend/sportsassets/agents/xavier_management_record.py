"""XAVIER'S MANAGEMENT RECORD: ONE ACCOUNTABLE, CURRENT RECORD PER HELD
PAPER POSITION, IN THE MANAGEMENT READBACK. Read only -- this module places,
cancels and changes NO order and grants no authority.

THE GAP (RC6 xavier-records; production pm-acceptance 37836393458,
2026-10-08 20:03Z, xavier_management.json). The three held PAPER positions
(asc-nfl-tb-dal-2026-10-08-pos-9pt5, atc-brb-csc-cri-2026-10-08-csc,
atc-idnsl-pke-mau-2026-10-09-pke) each showed an evidence state, a thesis,
alternatives and a gated recommendation -- and nothing else an accountable
manager needs. Every one of the following was persisted on the review row
(paper_xavier_reviews) or readable from the book, and none reached the
readback:
  * WHY the probability was not current: `probability_limitation` and the
    held PinnAPI read's refusal (FEED_OWNERSHIP_NOT_HELD,
    FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE, NO_FEED_EVENT ...) were on
    the review's measure and never on the assessment or the readback;
  * the probability's three instants kept apart -- the provider's source
    stamp, our receipt, and the review instant that verified it against its
    own limit;
  * the position's ORDERS with their ACTUAL states now (an EXPIRED or
    CANCELED protective sale is not protection), the protection-continuity
    state read live, the protective price the review could or could not set
    (PKE: NO_PROTECTIVE_PRICE_BELOW_ONE_DOLLAR), and the residual exposure
    (only FILLED protection counts);
  * the action the review TOOK, and the next review instant or the price
    condition that would act;
  * the packet's state NOW by the gate's own rule (bettor_paper_freshness.
    packet_currency with the live protection: the capital-readiness gate
    `xavier_complete` reads the same), and every blocker by name.

THE RECORD. `build` (pure) composes it from those reads; `records_for`
reads them for the OPEN paper positions only (bounded per position). The
packet-currency rule, the protection rule and every limit are the existing
ones (bettor_paper_freshness.PACKET_CURRENCY_RULE, protection_state,
SLA_S, the probability's own recorded limit); nothing here defines a
threshold, and a record that cannot be read says so -- it is never assumed
complete.
"""
from __future__ import annotations

import json

from .. import bettor_paper_ledger as L

VERSION = "XAVIER_MANAGEMENT_RECORD_V1"
#: the newest orders of each held position shown on its record (bounded)
ORDERS_PER_POSITION = 12
#: incomplete positions listed by name in the summary (the count is whole)
SUMMARY_LISTED = 50
#: the newest review rows read per group (a group's positions are reviewed
#: at one instant, one row per position)
REVIEWS_PER_GROUP = 4

R_NO_REVIEW = "NO_REVIEW_RECORDED"
R_RECORD_UNREAD = "MANAGEMENT_RECORD_UNREAD"
#: the three clocks of a probability, never substituted for one another
CLOCK_RULE = (
    "source_event_at is the provider's own stamp of the price (its last "
    "observed change, or a provider-stamped confirmation of the unchanged "
    "price), received_at is OUR receipt of it, verified_at is the review "
    "instant that judged it against its own freshness limit; a missing "
    "stamp is null with its basis, never filled from another clock")
PROTECTION_RULE = (
    "the packet's protection element is present only in PROTECTED_RESTING "
    "(one RESTING / PARTIALLY_FILLED standing sale, unexpired now, remaining "
    "qty == open qty); an EXPIRED, CANCELED or REJECTED order, a cancel in "
    "flight or a quantity mismatch is not. For EXPOSURE only FILLED "
    "protective quantity counts: a resting sale reduces no exposure until "
    "it fills")

LATEST_REVIEWS_SQL = """
    SELECT x.review_id, x.group_id, x.reviewed_at, x.trigger,
           x.recommendation, x.refusal, x.selection, x.measure, x.action,
           x.standing, x.exposure
      FROM unnest($2::text[]) AS g(group_id)
      CROSS JOIN LATERAL (
           SELECT r.review_id, r.group_id, r.reviewed_at, r.trigger,
                  r.recommendation, r.refusal, r.selection, r.measure,
                  r.action, r.standing, r.exposure
             FROM paper_xavier_reviews r
            WHERE r.account_id = $1 AND r.group_id = g.group_id
            ORDER BY r.reviewed_at DESC, r.review_id DESC
            LIMIT %d) x
""" % REVIEWS_PER_GROUP

ORDERS_SQL = """
    SELECT x.* FROM unnest($2::text[]) AS g(group_id)
      CROSS JOIN LATERAL (
           SELECT o.order_id, o.group_id, o.us_market_slug, o.holding_side,
                  o.role, o.direction, o.state, o.qty, o.filled_qty,
                  o.limit_price, o.time_in_force, o.created_at,
                  o.expires_at, o.terminal_at, o.terminal_reason
             FROM paper_orders o
            WHERE o.account_id = $1 AND o.group_id = g.group_id
            ORDER BY o.created_at DESC, o.order_id DESC
            LIMIT $3) x
"""

FILLED_PROTECTION_SQL = """
    SELECT group_id, us_market_slug, holding_side,
           coalesce(sum(qty), 0) AS qty
      FROM paper_fills
     WHERE account_id = $1 AND group_id = ANY($2::text[])
       AND role = 'STANDING_PROTECTION'
     GROUP BY 1, 2, 3
"""

OPEN_EXIT_INTENTS_SQL = """
    SELECT intent_id, group_id, position_key, selection, state, decided_at,
           cancel_deadline_at, revalidate_by
      FROM paper_exit_intents
     WHERE account_id = $1 AND group_id = ANY($2::text[])
       AND state = ANY($3::text[])
"""


def _j(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


def _ep(v):
    if v is None:
        return None
    if hasattr(v, "timestamp"):
        return float(v.timestamp())
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _num(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def review_of(rows: list, position_key: str) -> dict | None:
    """The newest review row of ONE position from its group's newest rows
    (pure): the row whose packet names the position, else the newest."""
    best = None
    for r in rows or []:
        sel = _j(r.get("selection")) or {}
        pk = (((sel.get("management_packet") or {}).get("position") or {})
              .get("position_key")) if isinstance(sel, dict) else None
        if pk == position_key and (best is None or (
                _ep(r.get("reviewed_at")) or 0) > (
                _ep(best.get("reviewed_at")) or 0)):
            best = r
    if best is not None:
        return best
    rows = sorted(rows or [], key=lambda r: _ep(r.get("reviewed_at")) or 0,
                  reverse=True)
    return rows[0] if rows else None


def order_view(o: dict, *, now: float, protection: dict) -> dict:
    """One order of the position as it stands NOW (pure)."""
    state = o.get("state")
    qty, filled = _num(o.get("qty")) or 0.0, _num(o.get("filled_qty")) or 0.0
    exp = _ep(o.get("expires_at"))
    live = state in L.OPEN_STATES
    return {"order_id": o.get("order_id"), "role": o.get("role"),
            "direction": o.get("direction"), "state": state,
            "qty": qty, "filled_qty": filled,
            "remaining_qty": round(qty - filled, 6) if live else 0.0,
            "limit_price": _num(o.get("limit_price")),
            "time_in_force": o.get("time_in_force"),
            "created_at": _ep(o.get("created_at")), "expires_at": exp,
            "terminal_at": _ep(o.get("terminal_at")),
            "terminal_reason": o.get("terminal_reason"),
            # live = still able to trade on the simulated book now
            "live_now": bool(live and state != "CANCEL_PENDING"
                             and (exp is None or float(now) < exp)),
            "protects_now": bool(
                (protection or {}).get("state") == "PROTECTED_RESTING"
                and (protection or {}).get("order_id") == o.get("order_id"))}


def _alternatives(review: dict | None, assessment: dict | None) -> list:
    """Every alternative considered, compact: the assessment's completed
    set (xavier_freshness.complete_alternatives) when present."""
    alts = (assessment or {}).get("alternatives")
    out = []
    for a in alts if isinstance(alts, list) else []:
        if not isinstance(a, dict):
            continue
        out.append({"action": a.get("option") or a.get("action"),
                    "rankable": a.get("rankable"),
                    "value_usd": a.get("value_usd"),
                    "value_is_current": a.get("value_is_current"),
                    "blocker": a.get("blocker")})
    return out


def build(*, pos: dict, review: dict | None, packet: dict | None,
          protection: dict | None, mark: dict | None, orders: list,
          filled_protection_qty: float, exit_intent: dict | None,
          assessment: dict | None, now: float, review_due_at=None,
          display: dict | None = None) -> dict:
    """THE MANAGEMENT RECORD OF ONE HELD POSITION (pure). `review` the
    position's newest paper_xavier_reviews row, `packet` its group's
    packet_record (bettor_paper_freshness.LATEST_PACKET_SQL, the gate's own
    read), `protection` the protection_state read live at `now`, `mark` its
    mark classification now, `orders` its newest orders, `assessment` the
    group's newest assessment view, `display` the read-time gated
    recommendation (xavier_freshness.validity)."""
    from .. import bettor_paper_freshness as PMF
    from . import paper_xavier as PX
    now = float(now)
    rv = dict(review) if review else None
    sel = (_j((rv or {}).get("selection")) or {}) if rv else {}
    m = (_j((rv or {}).get("measure")) or {}) if rv else {}
    act = (_j((rv or {}).get("action")) or {}) if rv else {}
    std = (_j((rv or {}).get("standing")) or {}) if rv else {}
    sel = sel if isinstance(sel, dict) else {}
    m = m if isinstance(m, dict) else {}
    act = act if isinstance(act, dict) else {}
    std = std if isinstance(std, dict) else {}
    mp = sel.get("management_packet") or {}
    gate = mp.get("gate") or {}
    val = sel.get("valuation") or {}
    feed = m.get("feed") if isinstance(m.get("feed"), dict) else {}
    fdet = (m.get("feed_detail") if isinstance(m.get("feed_detail"), dict)
            else {})
    reviewed_at = _ep((rv or {}).get("reviewed_at"))
    ev_state = m.get("evidence_state")
    expires = _num(val.get("expires_at"))
    prot = dict(protection or {})
    pstate = prot.get("state")
    q = float(pos.get("open_qty") or 0.0)

    # ── the packet NOW, by the gate's own rule ─────────────────────────
    cur = PMF.packet_currency(packet, now=now,
                              last_fill_at=pos.get("last_fill_at"),
                              protection_state=pstate)
    mclass = (mark or {}).get("class")
    applicable = mclass != PMF.EXTERNAL_UNAVAILABLE

    # ── orders, protection and exposure, as they stand NOW ─────────────
    mine = [o for o in orders or []
            if o.get("us_market_slug") == pos.get("us_market_slug")
            and o.get("holding_side") == pos.get("holding_side")]
    ovs = [order_view(o, now=now, protection=prot) for o in mine]
    resting = sum(o["remaining_qty"] for o in ovs
                  if o["role"] == "STANDING_PROTECTION" and o["live_now"])
    exposure = PX.exposure_view(
        {"open_qty": q, "cost_basis_usd": pos.get("cost_basis_usd")},
        resting_qty=resting, filled_protection_qty=filled_protection_qty)
    pprice = std.get("protective_price") if isinstance(
        std.get("protective_price"), dict) else None
    price_condition, no_condition = None, None
    if pstate == PMF.PS_PROTECTED:
        price_condition = {
            "kind": "STANDING_PROTECTIVE_SALE_RESTING",
            "order_id": prot.get("order_id"),
            "sells_qty": prot.get("remaining_qty"),
            "at_or_above": prot.get("limit_price"),
            "until": prot.get("expires_at"),
            "is": ("a resting simulated sale that sells the open quantity "
                   "if the book reaches this price; protection only when "
                   "it fills")}
    else:
        no_condition = ("PROTECTIVE_PRICE:%s" % pprice.get("refusal")
                        if pprice and pprice.get("ok") is False
                        else "PROTECTION:%s" % pstate)

    # ── every blocker, by name ───────────────────────────────────────
    blockers: list = []

    def add(code):
        if code and code not in blockers:
            blockers.append(code)
    if rv is None:
        add(R_NO_REVIEW)
    for code in gate.get("missing") or []:
        add(code)
    if not cur["current"]:
        add(cur["why"])
    if m.get("feed_refusal"):
        add("PINNAPI_HELD_READ:%s" % m["feed_refusal"])
    if pstate != PMF.PS_PROTECTED:
        add("PROTECTION:%s" % pstate)
        if pprice and pprice.get("ok") is False:
            add("PROTECTIVE_PRICE:%s" % pprice.get("refusal"))
    if exit_intent:
        add("EXIT_INTENT_OPEN:%s" % exit_intent.get("state"))
    if mclass not in PMF.FRESHLY_MANAGEABLE:
        add("MARK_CLASS:%s" % mclass)

    th = (assessment or {}).get("thesis_detail") or {}
    rationale = [x for x in (
        sel.get("selection_reason"), (th or {}).get("why")
        if isinstance(th, dict) else None,
        m.get("probability_limitation"),
        (rv or {}).get("refusal")) if x]
    disp = display or {}
    return {
        "version": VERSION, "as_of": now,
        "position": {
            "position_key": pos.get("position_key"),
            "group_id": pos.get("group_id"),
            "us_market_slug": pos.get("us_market_slug"),
            "holding_side": pos.get("holding_side"),
            "strategy": pos.get("strategy"),
            "remaining_qty": q,
            "bought_qty": pos.get("bought_qty"),
            "sold_qty": pos.get("sold_qty"),
            "settled_qty": pos.get("settled_qty"),
            "avg_cost_per_contract_incl_fees": pos.get(
                "avg_cost_per_contract_incl_fees"),
            "cost_basis_usd": pos.get("cost_basis_usd"),
            "first_fill_at": pos.get("first_fill_at"),
            "last_fill_at": pos.get("last_fill_at"),
            "label": "PAPER POSITION (SIMULATED)"},
        "review": None if rv is None else {
            "review_id": rv.get("review_id"), "reviewed_at": reviewed_at,
            "trigger": rv.get("trigger"),
            "assessment_id": (assessment or {}).get("assessment_id")},
        "probability": None if rv is None else {
            "evidence_state": ev_state,
            "probability": (_num(m.get("probability"))
                            if m.get("probability") is not None
                            else _num(val.get("probability"))),
            "source": val.get("source") or m.get("probability_source"),
            "source_event_at": _num(val.get("source_at")),
            "source_event_at_basis": val.get("source_at_basis"),
            "received_at": _num(val.get("observed_at")),
            "verified_at": reviewed_at,
            "freshness_basis": feed.get("freshness_basis"),
            "limit_s": _num(val.get("limit_s")),
            "expires_at": expires,
            "current_now": bool(ev_state == PX.E_FRESH
                                and expires is not None and now <= expires),
            "valuation_id": val.get("valuation_id"),
            "valuation_store": val.get("valuation_store"),
            "limitation": m.get("probability_limitation"),
            "feed_refusal": m.get("feed_refusal"),
            "feed_identity_basis": (feed.get("identity_basis")
                                    or fdet.get("identity_basis")),
            "held_fixture": (feed.get("held_fixture")
                             or fdet.get("held_fixture")),
            "clock_rule": CLOCK_RULE},
        "decision": None if rv is None else {
            "recorded_recommendation": rv.get("recommendation"),
            "display_recommendation": disp.get("display_recommendation"),
            "recommendation_state": disp.get("recommendation_state"),
            "mechanical_selection": sel.get("mechanical_selection"),
            "selection_reason": sel.get("selection_reason"),
            "refusal": rv.get("refusal"),
            "action_taken": act.get("taken"),
            "action_detail": {k: act.get(k) for k in (
                "orders", "order_id", "why", "refusal", "exit_intent_id",
                "exit_intent_state", "exit_intent_resolution")
                if act.get(k) is not None},
            "alternatives_considered": _alternatives(rv, assessment),
            "rationale": rationale},
        "next": {
            "review_due_at": review_due_at,
            "probability_expires_at": (expires if ev_state == PX.E_FRESH
                                       else None),
            "price_condition": price_condition,
            "price_condition_absent_because": no_condition},
        "orders": ovs,
        "orders_shown": len(ovs), "orders_bound": ORDERS_PER_POSITION,
        "protection": {
            "state": pstate, "valid_now": pstate == PMF.PS_PROTECTED,
            "order_id": prot.get("order_id"),
            "order_state": prot.get("order_state"),
            "limit_price": prot.get("limit_price"),
            "remaining_qty": prot.get("remaining_qty"),
            "expires_at": prot.get("expires_at"),
            "protective_price_at_review": pprice,
            "rule": PROTECTION_RULE},
        "exposure": exposure,
        "exit_intent": exit_intent,
        "packet": {
            "recorded_complete": bool(gate.get("complete")) if rv else None,
            "recorded_missing": list(gate.get("missing") or []),
            "complete_now": bool(cur["current"]),
            "why_not_complete_now": cur["why"],
            "mark_class_now": mclass,
            "counted_by_the_gate": applicable,
            "rule": PMF.PACKET_CURRENCY_RULE},
        "complete_current_packet": bool(cur["current"]),
        "blockers": blockers}


async def records_for(conn, refs: list, *, now: float) -> dict:
    """{(group_id, us_market_slug, holding_side): record} for the OPEN paper
    refs [{account_id, group_id, market, holding_side, review_due_at,
    assessment, display}]. Bounded per position; each account read in its
    own savepoint. A record that could not be read is
    {"unread": R_RECORD_UNREAD, "why": ...} -- never assumed complete."""
    from .. import bettor_paper_freshness as PMF
    from . import paper_exit_intents as XI
    out: dict = {}
    by_acct: dict = {}
    for r in refs:
        by_acct.setdefault(r.get("account_id"), []).append(r)
    for acct, rs in by_acct.items():
        keys = {(r["group_id"], r["market"], r["holding_side"]): r
                for r in rs}
        try:
            async with conn.transaction():
                pos = [p for p in await L.positions(conn, acct)
                       if (p["group_id"], p["us_market_slug"],
                           p["holding_side"]) in keys]
                groups = sorted({p["group_id"] for p in pos})
                if not groups:
                    continue
                reviews: dict = {}
                for r in await conn.fetch(LATEST_REVIEWS_SQL, acct, groups):
                    reviews.setdefault(r["group_id"], []).append(dict(r))
                packets = {r["group_id"]: PMF.packet_record(r)
                           for r in await conn.fetch(PMF.LATEST_PACKET_SQL,
                                                     acct, groups)}
                orders: dict = {}
                for o in await conn.fetch(ORDERS_SQL, acct, groups,
                                          ORDERS_PER_POSITION):
                    orders.setdefault(o["group_id"], []).append(dict(o))
                filled = {(r["group_id"], r["us_market_slug"],
                           r["holding_side"]): float(r["qty"])
                          for r in await conn.fetch(FILLED_PROTECTION_SQL,
                                                    acct, groups)}
                intents: dict = {}
                if await conn.fetchval(
                        "SELECT to_regclass('paper_exit_intents') "
                        "IS NOT NULL"):
                    for x in await conn.fetch(OPEN_EXIT_INTENTS_SQL, acct,
                                              groups, list(XI.OPEN)):
                        intents[x["position_key"]] = {
                            k: (_ep(v) if hasattr(v, "timestamp") else v)
                            for k, v in dict(x).items()}
                prot = await PMF.protections(conn, acct, pos, now=now)
                marks = {c["position_key"]: c for c in
                         await PMF.classify_positions(conn, acct, now=now,
                                                      positions=pos)}
        except Exception as exc:                                # noqa: BLE001
            for k in keys:
                out[k] = {"unread": R_RECORD_UNREAD,
                          "why": "%s: %s" % (type(exc).__name__,
                                             str(exc)[:160])}
            continue
        for p in pos:
            k = (p["group_id"], p["us_market_slug"], p["holding_side"])
            ref = keys.get(k) or {}
            out[k] = build(
                pos=p,
                review=review_of(reviews.get(p["group_id"]),
                                 p["position_key"]),
                packet=packets.get(p["group_id"]),
                protection=prot.get(p["position_key"]),
                mark=marks.get(p["position_key"]),
                orders=orders.get(p["group_id"]) or [],
                filled_protection_qty=filled.get(k, 0.0),
                exit_intent=intents.get(p["position_key"]),
                assessment=ref.get("assessment"), now=now,
                review_due_at=ref.get("review_due_at"),
                display=ref.get("display"))
    return out


def summary(records: list) -> dict:
    """THE HELD BOOK'S MANAGEMENT RECORDS, COUNTED (pure). Whole counts;
    only the listing of incomplete positions is bounded."""
    recs = [r for r in records if isinstance(r, dict)]
    unread = [r for r in recs if r.get("unread")]
    good = [r for r in recs if not r.get("unread")]
    complete = [r for r in good if r.get("complete_current_packet")]
    counted = [r for r in good if (r.get("packet") or {}).get(
        "counted_by_the_gate")]
    incomplete = [r for r in good if not r.get("complete_current_packet")]
    by_blocker: dict = {}
    for r in incomplete:
        for b in r.get("blockers") or []:
            by_blocker[b] = by_blocker.get(b, 0) + 1
    return {
        "version": VERSION,
        "open_positions": len(recs),
        "records_unread": len(unread),
        "complete_current_packet": len(complete),
        "counted_by_the_xavier_complete_gate": len(counted),
        "complete_current_packet_counted_by_the_gate": sum(
            1 for r in complete if (r.get("packet") or {}).get(
                "counted_by_the_gate")),
        "incomplete_count": len(incomplete),
        "incomplete_by_blocker": by_blocker,
        "incomplete": [{
            "position_key": (r.get("position") or {}).get("position_key"),
            "group_id": (r.get("position") or {}).get("group_id"),
            "blockers": r.get("blockers")}
            for r in incomplete][:SUMMARY_LISTED],
        "rule": ("complete_current_packet is bettor_paper_freshness."
                 "packet_currency with the live protection -- the rule the "
                 "capital-readiness gate xavier_complete applies; an "
                 "unread record is counted as incomplete, never complete")}


def describe() -> dict:
    return {"version": VERSION, "clock_rule": CLOCK_RULE,
            "protection_rule": PROTECTION_RULE,
            "orders_per_position": ORDERS_PER_POSITION}
