"""E · ALPHA VS EXECUTION ATTRIBUTION (SHADOW). Where a position's money
came from: the model, the execution, the management, or the settlement.

ONE ADDITIVE IDENTITY, PER POSITION, RECONCILED TO CASH. With q the entry
quantity, p the decision's probability, d the price the decision planned to
pay (its acquisition VWAP), v the entry fills' VWAP, f the entry fees and
pi the per-contract payoff of the held contract (settlement, or the
venue-verified outcome when the position was sold out before settling):

  model edge         q (p - d)            what the model said was there
  slippage           q (v - d)            paid above (+) / below (-) plan
  fees               f
  execution edge     -(slippage + fees)   what execution added or cost
  executable edge    q (p - v)            the edge at the fill VWAP
  management         sum over SELL fills of (net proceeds - qty * pi)
                     + sum over HEDGE legs of (payout - cost)
                     -- Xavier's actions versus holding to settlement; a
                     position with no action is a MEASURED 0 (held)
  settlement         q (pi - p) when the settlement is EXCEPTIONAL
                     (VOID_REFUND, SETTLED_AT_VENUE_PRICE), else 0
  outcome variance   q (pi - p) when the settlement is ORDINARY (WON/LOST)
                     -- the coin, not anybody's skill

  model + execution + management + settlement + variance
      = q (pi - v) - f + management = realized P&L

`cash_pnl_usd` is the same P&L from the cash legs alone (payouts + sale
proceeds - purchase costs) and `reconciles` says whether the two agree to a
cent. A component that cannot be measured is null with its reason; the
identity is then not claimed.

PAPER positions come from paper_decisions -> paper_orders (ENTRY) ->
paper_fills / paper_settlements. ACTUAL positions come from
execution_intents -> execmirror_orders -> execmirror_fills (venue fills,
LONG-side wire prices converted to cost space by the intent's side); their
payoff is the paper settlement of the SAME contract (same group, market and
side), because it is the same contract's outcome.
"""
from __future__ import annotations

from . import common as C

VERSION = "INTEL_ATTRIBUTION_V1"
ORDINARY = ("WON", "LOST")
EXCEPTIONAL = ("VOID_REFUND", "SETTLED_AT_VENUE_PRICE")
SELL_ROLES = ("EXIT", "REDUCE", "STANDING_PROTECTION")
RECONCILE_TOLERANCE_USD = 0.01


def decision_probability(dec: dict):
    for k, basis in (("p_blended", "P_BLENDED"), ("p_pinnacle", "P_PINNACLE"),
                     ("p_internal", "P_INTERNAL")):
        v = C.num(dec.get(k))
        if v is not None:
            return v, basis
    return None, None


def decision_price(dec: dict):
    """(price per contract in cost space, basis) the decision planned."""
    econ = C.jload(dec.get("economics")) or {}
    acq = econ.get("acquisition") if isinstance(econ, dict) else None
    v = C.num((acq or {}).get("vwap")) if isinstance(acq, dict) else None
    if v is not None:
        return v, "DECISION_PLANNED_ACQUISITION_VWAP"
    lv = (econ.get("levels") or []) if isinstance(econ, dict) else []
    if lv and isinstance(lv[0], dict) and C.num(lv[0].get("price")) is not None:
        return C.num(lv[0]["price"]), "DECISION_BEST_LEVEL_PRICE"
    v = C.num(dec.get("limit_price"))
    if v is not None:
        return v, "DECISION_LIMIT_PRICE"
    return None, None


def payoff_of(settlement: dict | None, valuation: dict | None):
    """(payoff per contract, settlement outcome/class, basis)."""
    if settlement:
        pp = C.num(settlement.get("payout_per_contract"))
        if pp is not None:
            return pp, str(settlement.get("outcome")), "PAPER_SETTLEMENT"
    if valuation and valuation.get("outcome_known") and valuation.get(
            "outcome") in (0, 1) and valuation.get("outcome_basis") in (
            "VENUE_SETTLEMENT_PRICE", "VENUE_REPORTED_OUTCOME"):
        o = int(valuation["outcome"])
        return float(o), ("WON" if o == 1 else "LOST"), (
            "VENUE_VERIFIED_VALUATION_OUTCOME")
    return None, None, None


def attribute(*, subject_id: str, book: str, p, p_basis, d, d_basis,
              entry_fills: list, sell_fills: list, hedge_legs: list,
              payoff, settlement_outcome, payoff_basis, actions: int = 0,
              extra: dict | None = None) -> dict:
    """The pure decomposition. Fills are dicts {qty, price (cost space,
    excl. fee), fee_usd}; hedge legs are {qty, cost_usd (incl. fees),
    payoff_per_contract or None}."""
    out = C.Out(book=book, subject_id=subject_id, version=VERSION,
                label=C.LABEL)
    out.update(extra or {})
    q = sum(C.num(f["qty"]) or 0.0 for f in entry_fills)
    out["entry_qty"] = C.rnd(q)
    out.put("p_decision", C.rnd(p), "DECISION_CARRIES_NO_PROBABILITY")
    out["p_basis"] = p_basis
    out.put("decision_price", C.rnd(d), "DECISION_CARRIES_NO_PLANNED_PRICE")
    out["decision_price_basis"] = d_basis
    if q <= 0:
        why = "NO_ENTRY_FILL"
        for k in ("fill_vwap", "model_edge_pc", "executable_edge_pc",
                  "slippage_pc", "fees_usd", "model_edge_usd",
                  "executable_edge_usd", "slippage_usd", "execution_edge_usd",
                  "management_usd", "settlement_usd", "outcome_variance_usd",
                  "realized_pnl_usd", "cash_pnl_usd"):
            out.put(k, None, why)
        out["reconciles"] = None
        out["settlement_class"] = None
        return out
    v = sum((C.num(f["qty"]) or 0.0) * (C.num(f["price"]) or 0.0)
            for f in entry_fills) / q
    fees = sum(C.num(f.get("fee_usd")) or 0.0 for f in entry_fills)
    out.put("fill_vwap", C.rnd(v))
    out.put("fees_usd", C.rnd(fees))
    out["fee_pc"] = C.rnd(fees / q)
    no_p = "DECISION_CARRIES_NO_PROBABILITY"
    no_d = "DECISION_CARRIES_NO_PLANNED_PRICE"
    out.put("executable_edge_pc", None if p is None else C.rnd(p - v), no_p)
    out.put("executable_edge_usd", None if p is None else C.rnd(q * (p - v)),
            no_p)
    out.put("model_edge_pc", None if (p is None or d is None)
            else C.rnd(p - d), no_p if p is None else no_d)
    out.put("model_edge_usd", None if (p is None or d is None)
            else C.rnd(q * (p - d)), no_p if p is None else no_d)
    out.put("slippage_pc", None if d is None else C.rnd(v - d), no_d)
    out.put("slippage_usd", None if d is None else C.rnd(q * (v - d)), no_d)
    out.put("execution_edge_usd", None if d is None
            else C.rnd(-(q * (v - d)) - fees), no_d)

    # ── management: Xavier's actions vs holding to settlement ──────────
    out["management_actions"] = int(actions)
    out["sell_fills"] = len(sell_fills)
    out["hedge_legs"] = len(hedge_legs)
    mgmt = 0.0
    mgmt_why = None
    for s in sell_fills:
        if payoff is None:
            mgmt_why = "POSITION_NOT_SETTLED_HOLD_COUNTERFACTUAL_UNKNOWN"
            break
        sq = C.num(s["qty"]) or 0.0
        mgmt += sq * (C.num(s["price"]) or 0.0) - (C.num(s.get("fee_usd"))
                                                   or 0.0) - sq * payoff
    for h in hedge_legs:
        hp = C.num(h.get("payoff_per_contract"))
        if hp is None:
            mgmt_why = mgmt_why or "HEDGE_LEG_NOT_SETTLED"
            continue
        mgmt += (C.num(h["qty"]) or 0.0) * hp - (C.num(h["cost_usd"]) or 0.0)
    if mgmt_why:
        out.put("management_usd", None, mgmt_why)
    else:
        out.put("management_usd", C.rnd(mgmt))
        out["management_basis"] = ("NO_MANAGEMENT_ACTION_HELD_TO_SETTLEMENT"
                                   if not sell_fills and not hedge_legs
                                   else "ACTIONS_VS_HOLD_TO_SETTLEMENT")

    # ── settlement vs ordinary, and the coin ──────────────────────────
    out["settlement_outcome"] = settlement_outcome
    out["payoff_per_contract"] = C.rnd(payoff)
    out["payoff_basis"] = payoff_basis
    if payoff is None:
        out["settlement_class"] = None
        out.put("settlement_usd", None, "POSITION_NOT_SETTLED")
        out.put("outcome_variance_usd", None, "POSITION_NOT_SETTLED")
    elif p is None:
        out["settlement_class"] = ("EXCEPTIONAL" if settlement_outcome
                                   in EXCEPTIONAL else "ORDINARY")
        out.put("settlement_usd", None, no_p)
        out.put("outcome_variance_usd", None, no_p)
    elif settlement_outcome in EXCEPTIONAL:
        out["settlement_class"] = "EXCEPTIONAL"
        out.put("settlement_usd", C.rnd(q * (payoff - p)))
        out.put("outcome_variance_usd", 0.0)
    else:
        out["settlement_class"] = "ORDINARY"
        out.put("settlement_usd", 0.0)
        out.put("outcome_variance_usd", C.rnd(q * (payoff - p)))

    # ── realized, two ways ────────────────────────────────────────────
    if payoff is None or out["management_usd"] is None:
        out.put("realized_pnl_usd", None,
                out["unmeasured"].get("management_usd")
                or "POSITION_NOT_SETTLED")
        out.put("cash_pnl_usd", None, "POSITION_NOT_SETTLED")
        out["reconciles"] = None
        return out
    realized = q * (payoff - v) - fees + out["management_usd"]
    out.put("realized_pnl_usd", C.rnd(realized))
    sold = sum(C.num(s["qty"]) or 0.0 for s in sell_fills)
    cash = ((q - sold) * payoff
            + sum((C.num(s["qty"]) or 0.0) * (C.num(s["price"]) or 0.0)
                  - (C.num(s.get("fee_usd")) or 0.0) for s in sell_fills)
            - (q * v + fees)
            + sum((C.num(h["qty"]) or 0.0) * C.num(h["payoff_per_contract"])
                  - (C.num(h["cost_usd"]) or 0.0) for h in hedge_legs))
    out.put("cash_pnl_usd", C.rnd(cash))
    parts = [out.get(k) for k in ("model_edge_usd", "execution_edge_usd",
                                  "management_usd", "settlement_usd",
                                  "outcome_variance_usd")]
    if any(x is None for x in parts):
        out["reconciles"] = abs(realized - cash) <= RECONCILE_TOLERANCE_USD
        out["identity_claimed"] = False
    else:
        out["identity_claimed"] = True
        out["reconciles"] = (abs(sum(parts) - realized)
                             <= RECONCILE_TOLERANCE_USD
                             and abs(realized - cash)
                             <= RECONCILE_TOLERANCE_USD)
    return out


def summarize(rows: list) -> dict:
    """Totals per book over measured values only, with counts of nulls."""
    out = {}
    keys = ("model_edge_usd", "execution_edge_usd", "slippage_usd",
            "fees_usd", "management_usd", "settlement_usd",
            "outcome_variance_usd", "realized_pnl_usd")
    for book in C.BOOKS:
        mine = [r for r in rows if r["book"] == book]
        s = {"positions": len(mine),
             "reconciling": sum(1 for r in mine if r.get("reconciles")),
             "not_reconciling": sum(1 for r in mine
                                    if r.get("reconciles") is False)}
        for k in keys:
            vals = [r[k] for r in mine if r.get(k) is not None]
            s[k] = C.rnd(sum(vals)) if vals else None
            s[k + "_measured_n"] = len(vals)
            if not vals:
                s.setdefault("unmeasured", {})[k] = (
                    "NO_POSITION_WITH_A_MEASURED_VALUE")
        out[book] = s
    out["summed_across_books"] = False
    return out


# ═════════════════════════════════════════════════════════════════════
# THE READ
# ═════════════════════════════════════════════════════════════════════

#: THE POPULATION `load_paper` attributes: ENTER decisions that became a
#: position (an ENTRY paper order), newest first. RC6.2 (p-evcontrols): the
#: bound used to count EVERY ENTER decision, and a quarantined strategy's
#: ENTER verdicts the ledger refuses (production 2026-10-07..09: 961, 542
#: and 636 a day, 0 orders) crowded the actual positions out of the newest
#: 5,000 -- `paper_rows` discards an orderless decision anyway, so the bound
#: now counts positions only (paper_orders_decision_role_idx serves the
#: probe). `$1` NULL = no window (a population no window declares). `$2`
#: (rc6.3 pr5-port) the PAPER accounts read, always named by the caller: the
#: selected account, or the risk controls' epoch lineage
#: (simulated_account_context.risk_history_accounts). There is no default
#: account: before Day One every caller that omitted it read paper_acct_main
#: and kept reading the archive after an activation.
PAPER_DECISIONS_SQL = """
    SELECT d.decision_id, d.decided_at, d.valuation_id, d.us_market_slug,
           d.holding_side, d.p_pinnacle, d.p_blended, d.p_internal,
           d.limit_price, d.economics, d.strategy
      FROM paper_decisions d
     WHERE d.verdict = 'ENTER'
       AND ($1::float8 IS NULL OR d.decided_at >= to_timestamp($1))
       AND d.account_id = ANY($2::text[])
       AND EXISTS (SELECT 1 FROM paper_orders o
                    WHERE o.decision_id = d.decision_id
                      AND o.role = 'ENTRY')
     ORDER BY d.decided_at DESC, d.decision_id DESC LIMIT $3
"""
PAPER_WINDOW_DAYS = 60.0
PAPER_POPULATION = ("PAPER ENTER decisions with an ENTRY paper order "
                    "(positions), newest first")
PAPER_READ_ORDER = "decided_at, decision_id descending (newest first)"

#: THE POPULATION IS READ WHOLE, A PAGE AT A TIME (RC6.2 p-evcontrols,
#: review rework 2). redteam.controls.attributed_positions reads EVERY
#: position (days=None): the MULTIPLE_TESTING study's frozen plan declares
#: no window, and the forward scoreboard's cohort is every position since
#: the thresholds' frozen_at. Both only grow. Under the earlier 5,000 bound
#: a cut read fails closed (ATTRIBUTION_READ_TRUNCATED), so from the 5,001st
#: lifetime position the study would have been RED for good, whatever the
#: data said, and the scoreboard UNAVAILABLE once 5,000 positions followed
#: 2026-10-07 -- a software RED that scorecard_14 labels forward evidence.
#: The read now runs through a server-side cursor PAPER_PAGE_POSITIONS at a
#: time. Each page's fills, settlements, Xavier review counts and
#: valuations are fetched for that page alone and attributed on the CPU
#: lane, so one page's decision payload is in memory at a time.
#: PAPER_POSITIONS_LIMIT is a SAFETY STOP, not a sample size.
#: research-sql 37979576920 (2026-10-09T19:20Z, SELECT/EXPLAIN only)
#: measured production over its whole population (681 positions, every one
#: within 60 days, 0 since 2026-10-07; 9/40/61/61/508/2 a day 10-01..06):
#:   - the unwindowed decision scan: 6.5 ms;
#:   - entry groups 2.8 ms, fills 1.5 ms, settlements 2.7 ms, valuations
#:     4.1 ms, the fixture read 1.8 ms (each net of its array build);
#:   - the Xavier review count 172 ms -- 172,313 review rows, 253 per
#:     position on average (p95 1,116, max 2,693): the dominant cost;
#:   - about 0.28 ms of server time per position in all;
#:   - each decision row carries 7,425 bytes on average (max 19,310: the
#:     economics); fills 233, settlements 78, valuations 187 per position.
#: LOCAL, 20,000 synthetic positions carrying production's 7.4 KB decision
#: payload and a fill each (no Xavier reviews; their cost is measured
#: above): the paged read takes 2.0-2.3 s warm end to end against 2.2 s for
#: the one-shot read, and peaks at 51 MB of Python allocations against
#: 220 MB; both retain 35 MB of attributed rows. paper_rows alone costs
#: 58-86 us per position.
#: At the stop the read therefore costs about 5.6 s of production database
#: time spread over 20 pages of about 0.28 s each, plus the decode and
#: attribution (about 2 s locally, several times that on production's
#: shared CPU), and holds about 35 MB of rows. The stop is 29 times
#: production's whole population. A read the stop cuts still fails closed,
#: by name (`truncated`, and `newest_unread_at`).
PAPER_PAGE_POSITIONS = 1000
PAPER_POSITIONS_LIMIT = 20000
#: the intel shadow cycle's own read (runner.py: a display snapshot that
#: names a cut read; no control reads it) keeps its earlier bound, so the
#: cycle's attribution write volume is unchanged by the safety stop above
INTEL_CYCLE_POSITIONS_LIMIT = 5000


def read_complete_since(read: dict | None, since: float | None) -> bool:
    """Whether a `load_paper` read (its `meta`) holds EVERY position decided
    at or after `since` (None = every position). The first position past
    the bound is read too, so the answer is exact: complete when the read
    was not truncated, or the newest position it left out is older than
    `since`."""
    if not read or not read.get("truncated"):
        return True
    cut = read.get("newest_unread_at")
    return since is not None and cut is not None and float(cut) < float(
        since)


def within(rows: list, since: float | None) -> list:
    """The rows decided at or after `since` -- the window `load_paper`'s SQL
    applies, in pure code (None = all)."""
    if since is None:
        return list(rows)
    out = []
    for r in rows:
        at = C.epoch(r.get("decided_at"))
        if at is not None and at >= since:
            out.append(r)
    return out


#: (rc6.3 pr5-port) load_paper was called without naming the PAPER
#: account(s) it reads: refused, never a silent default account
R_ACCOUNT_NOT_NAMED = "ATTRIBUTION_PAPER_ACCOUNT_NOT_NAMED"


def paper_accounts(account_id) -> list:
    """The PAPER accounts a read names: one account id, or a non-empty list
    of them (an epoch lineage, nearest first). Anything else refuses by name
    (R_ACCOUNT_NOT_NAMED)."""
    if isinstance(account_id, str) and account_id:
        return [account_id]
    if isinstance(account_id, (list, tuple)) and account_id and all(
            isinstance(a, str) and a for a in account_id):
        return list(dict.fromkeys(account_id))
    raise ValueError(R_ACCOUNT_NOT_NAMED)


async def load_paper(conn, *, now, account_id, days=PAPER_WINDOW_DAYS,
                     limit=PAPER_POSITIONS_LIMIT,
                     meta: dict | None = None,
                     page=PAPER_PAGE_POSITIONS) -> list:
    """PAPER positions attributed (the population above), newest first: the
    WHOLE population, a page at a time through a server-side cursor. `days`
    None reads every position. `limit` is a safety stop: one position past
    it is asked for, so a cut is known, not guessed. `meta`, when given,
    receives what was read and whether the stop left a position out
    (`truncated`, and `newest_unread_at`, the decided_at of the newest one
    it left out), so a caller never takes a subset for the population.

    `account_id` IS REQUIRED (rc6.3 pr5-port): one PAPER account or a list
    of them (paper_accounts). It had a paper_acct_main default, and the
    red-team controls, the forward scoreboard's claims and the Command loss
    attribution called it without one, so after an activation they kept
    reading the archive. `meta` names the accounts read."""
    from . import reads as R

    accounts = paper_accounts(account_id)
    since = None if days is None else float(now) - float(days) * 86400.0
    limit, page = int(limit), max(1, int(page))
    out: list = []
    read = pages = 0
    truncated = False
    newest_unread_at = oldest_read_at = None
    # a cursor lives in a transaction: inside a readback section (its own
    # savepoint, statement timeout per statement and per FETCH) this is a
    # nested savepoint. The cursor reads ONE snapshot of the population.
    async with conn.transaction():
        cur = await conn.cursor(PAPER_DECISIONS_SQL, since, accounts,
                                limit + 1)
        while True:
            decs = [dict(r) for r in await cur.fetch(page)]
            fetched = len(decs)
            if fetched > limit - read:
                truncated = True
                newest_unread_at = C.epoch(decs[limit - read]["decided_at"])
                decs = decs[:limit - read]
            if decs:
                pages += 1
                read += len(decs)
                oldest_read_at = C.epoch(decs[-1]["decided_at"])
                groups = await R.entry_groups(
                    conn, [d["decision_id"] for d in decs])
                gids = sorted(set(groups.values()))
                fills = [dict(r) for r in await conn.fetch(
                    "SELECT group_id, role, direction, holding_side, "
                    "       us_market_slug, qty, price, fee_usd, gross_usd "
                    "  FROM paper_fills WHERE group_id = ANY($1::text[])",
                    gids)] if gids else []
                setts = await R.latest_settlements(conn, group_ids=gids)
                sidx = {(s["group_id"], s["us_market_slug"],
                         str(s["holding_side"])): s for s in setts.values()}
                acts = {r["group_id"]: int(r["n"]) for r in await conn.fetch(
                    "SELECT group_id, count(*) AS n "
                    "  FROM paper_xavier_reviews "
                    " WHERE group_id = ANY($1::text[]) "
                    "   AND action IS NOT NULL GROUP BY group_id",
                    gids)} if gids else {}
                vals = await R.valuations_by_id(
                    conn, [d.get("valuation_id") for d in decs])
                # OFF THE LOOP (RC6): one page of positions attributed fill
                # by fill on the CPU lane (see calibration.load_records for
                # the production stalls). Pure; each position's row depends
                # only on its own decision, group, fills, settlement and
                # valuation, so pages concatenate to the one-shot result.
                out += await C.offload(paper_rows, decs, groups, fills, sidx,
                                       acts, vals)
            if truncated or fetched < page:
                break
    if meta is not None:
        meta.update({
            "population": PAPER_POPULATION, "accounts": accounts,
            "window_days": days,
            "since": since, "limit": limit, "positions_read": read,
            "truncated": truncated, "newest_unread_at": newest_unread_at,
            "oldest_read_at": oldest_read_at, "order": PAPER_READ_ORDER,
            "pages": pages, "page_positions": page})
    return out


def paper_rows(decs, groups, fills, sidx, acts, vals) -> list:
    """`load_paper`'s reads -> attribution rows. PURE: it runs in a worker
    thread, and it is exactly the loop that ran inline before."""
    by_group: dict = {}
    for f in fills:
        by_group.setdefault(f["group_id"], []).append(f)
    out = []
    for d in decs:
        g = groups.get(d["decision_id"])
        if not g:
            continue
        gf = by_group.get(g, [])
        entry = [f for f in gf if f["role"] == "ENTRY"
                 and f["direction"] == "BUY"]
        slug = entry[0]["us_market_slug"] if entry else d.get(
            "us_market_slug")
        side = str(entry[0]["holding_side"]) if entry else str(
            d.get("holding_side"))
        sells = [f for f in gf if f["direction"] == "SELL"
                 and f["us_market_slug"] == slug
                 and str(f["holding_side"]) == side]
        legs: dict = {}
        for f in gf:
            if f["role"] == "HEDGE" and f["direction"] == "BUY":
                k = (f["us_market_slug"], str(f["holding_side"]))
                lg = legs.setdefault(k, {"qty": 0.0, "cost_usd": 0.0})
                lg["qty"] += C.num(f["qty"]) or 0.0
                lg["cost_usd"] += (C.num(f["gross_usd"]) or 0.0) + (
                    C.num(f["fee_usd"]) or 0.0)
        hedge_legs = []
        for (hs, hside), lg in legs.items():
            st = sidx.get((g, hs, hside))
            lg["payoff_per_contract"] = (C.num(st["payout_per_contract"])
                                         if st else None)
            hedge_legs.append(lg)
        val = vals.get(int(d["valuation_id"])) if d.get(
            "valuation_id") is not None else None
        payoff, outcome, pbasis = payoff_of(sidx.get((g, slug, side)), val)
        p, p_basis = decision_probability(d)
        dp, d_basis = decision_price(d)
        out.append(attribute(
            subject_id=g, book="PAPER", p=p, p_basis=p_basis, d=dp,
            d_basis=d_basis,
            entry_fills=[{"qty": f["qty"], "price": f["price"],
                          "fee_usd": f["fee_usd"]} for f in entry],
            sell_fills=[{"qty": f["qty"], "price": f["price"],
                         "fee_usd": f["fee_usd"]} for f in sells],
            hedge_legs=hedge_legs, payoff=payoff,
            settlement_outcome=outcome, payoff_basis=pbasis,
            actions=acts.get(g, 0),
            extra={"decision_id": d["decision_id"], "group_id": g,
                   "strategy": d.get("strategy"), "us_market_slug": slug,
                   "holding_side": side,
                   "decided_at": C.epoch(d.get("decided_at"))}))
    return out


async def load_actual(conn, *, now, days=60.0, limit=5000) -> list:
    """ACTUAL (execution mirror) positions attributed against the decision
    each execution intent copied."""
    from . import reads as R

    rows = [dict(r) for r in await conn.fetch(
        "SELECT i.intent_id, i.decision_id, i.valuation_id, i.group_id, "
        "       i.us_market_slug, i.order_intent, i.holding_side, "
        "       i.limit_price, i.wire_price, i.decided_at, i.strategy "
        "  FROM execution_intents i "
        " WHERE i.decided_at >= to_timestamp($1) "
        "   AND i.actual_mirror_id IS NOT NULL "
        " ORDER BY i.decided_at DESC LIMIT $2",
        float(now) - days * 86400.0, int(limit))]
    if not rows:
        return []
    gids = sorted({r["group_id"] for r in rows if r.get("group_id")})
    fills = [dict(r) for r in await conn.fetch(
        "SELECT f.group_id, f.us_market_slug, f.intent, f.qty, f.price, "
        "       f.fee_usd, o.role "
        "  FROM execmirror_fills f JOIN execmirror_orders o "
        "    ON o.mirror_id = f.mirror_id "
        " WHERE f.group_id = ANY($1::text[])", gids)] if gids else []
    decs = {r["decision_id"]: dict(r) for r in await conn.fetch(
        "SELECT decision_id, p_pinnacle, p_blended, p_internal, economics, "
        "       limit_price FROM paper_decisions "
        " WHERE decision_id = ANY($1::text[])",
        [r["decision_id"] for r in rows if r.get("decision_id")])}
    setts = await R.latest_settlements(conn, group_ids=gids)
    sidx = {(s["group_id"], s["us_market_slug"], str(s["holding_side"])): s
            for s in setts.values()}
    vals = await R.valuations_by_id(conn, [r.get("valuation_id")
                                           for r in rows])
    # OFF THE LOOP (RC6), as load_paper. Pure.
    return await C.offload(actual_rows, rows, fills, decs, sidx, vals)


def actual_rows(rows, fills, decs, sidx, vals) -> list:
    """`load_actual`'s reads -> attribution rows. PURE: it runs in a worker
    thread. A group's fills come from ONE index built here, in the fills'
    own order -- the same list the per-intent scan of every fill built
    (`[f for f in fills if f["group_id"] == g]`), which was intents x fills
    work at the 5,000-intent bound."""
    by_group: dict = {}
    for f in fills:
        by_group.setdefault(f["group_id"], []).append(f)
    out = []
    seen = set()
    for it in rows:
        g = it.get("group_id")
        if not g or g in seen:
            continue
        seen.add(g)
        side = C.side_of_intent(it.get("order_intent")) if it.get(
            "order_intent") else str(it.get("holding_side") or "LONG")
        slug = it.get("us_market_slug")
        gall = by_group.get(g, [])
        gf = [f for f in gall if f["us_market_slug"] == slug]
        entry = [{"qty": f["qty"], "price": C.cost_space(f["price"], side),
                  "fee_usd": f["fee_usd"]} for f in gf
                 if C.is_buy_intent(f["intent"]) and f.get("role") in (
                     "ENTRY", None)]
        sells = [{"qty": f["qty"], "price": C.cost_space(f["price"], side),
                  "fee_usd": f["fee_usd"]} for f in gf
                 if not C.is_buy_intent(f["intent"])]
        legs: dict = {}
        for f in gall:
            if f.get("role") == "HEDGE" and C.is_buy_intent(f["intent"]):
                hside = C.side_of_intent(f["intent"])
                lg = legs.setdefault((f["us_market_slug"], hside),
                                     {"qty": 0.0, "cost_usd": 0.0})
                q_ = C.num(f["qty"]) or 0.0
                lg["qty"] += q_
                lg["cost_usd"] += q_ * (C.cost_space(f["price"], hside)
                                        or 0.0) + (C.num(f["fee_usd"]) or 0.0)
        hedge_legs = []
        for (hs, hside), lg in legs.items():
            st = sidx.get((g, hs, hside))
            lg["payoff_per_contract"] = (C.num(st["payout_per_contract"])
                                         if st else None)
            hedge_legs.append(lg)
        dec = decs.get(it.get("decision_id")) or {}
        p, p_basis = decision_probability(dec)
        dp = C.cost_space(it.get("wire_price"), side)
        d_basis = "EXECUTION_INTENT_DECISION_WIRE"
        if dp is None:
            dp, d_basis = decision_price(dec)
        val = vals.get(int(it["valuation_id"])) if it.get(
            "valuation_id") is not None else None
        payoff, outcome, pbasis = payoff_of(sidx.get((g, slug, side)), val)
        out.append(attribute(
            subject_id=g, book="ACTUAL", p=p, p_basis=p_basis, d=dp,
            d_basis=d_basis, entry_fills=entry, sell_fills=sells,
            hedge_legs=hedge_legs, payoff=payoff, settlement_outcome=outcome,
            payoff_basis=pbasis,
            extra={"decision_id": it.get("decision_id"), "group_id": g,
                   "strategy": it.get("strategy"), "us_market_slug": slug,
                   "holding_side": side,
                   "fill_price_convention":
                       "VENUE_WIRE_PRICE_CONVERTED_BY_INTENT_SIDE",
                   "decided_at": C.epoch(it.get("decided_at"))}))
    return out
