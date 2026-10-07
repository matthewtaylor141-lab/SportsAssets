"""THE PAPER LOSS, ATTRIBUTED (closeout; history immutable, READ ONLY).

The PAPER book's realized loss (about -$33.7K on the 2026-10-06 scoreboard)
split by cause, per position, on the additive identity intel.attribution
already reconciles to cash:

  model edge + execution (slippage + fees) + management + settlement
      + outcome variance  =  realized P&L

and, beside it, the decompositions the identity does not carry:

  spread paid          q (v - mid): the fill VWAP against the MID of the
                       entry decision's own book (its book_obs_id), both in
                       cost space -- the half-spread crossed, apart from
                       slippage against plan
  model error vs       sum q (pi - p) over ORDINARY settlements, against its
  variance             binomial standard error sqrt(sum q^2 p (1 - p)): a
                       |z| <= 2 is consistent with variance; beyond it the
                       model's probabilities were wrong (calibration)
  management coverage  the realized P&L of positions Xavier NEVER had a
                       complete management packet for (no review could rank
                       an action), of positions with some complete reviews,
                       and of positions every review of which was complete
  missed exits         positions where a review RANKED EXIT / REDUCE but the
                       packet was incomplete (refused), held to settlement:
                       the ranked exit's value at that review minus what the
                       open quantity was paid at settlement (+ = the exit
                       would have been better)
  correlation          realized P&L of fixtures carrying more than one
                       position (one game, several bets) apart from single-
                       position fixtures; the ten worst fixtures named
  stale information    entered decisions whose Pinnacle age at decision was
                       over its limit (the entry gate refuses them: counted,
                       expected 0)

A component that cannot be measured is null with its reason; nothing is
estimated or invented, and no history row is touched.
"""
from __future__ import annotations

import collections
import math

VERSION = "PAPER_LOSS_ATTRIBUTION_V1"
HISTORY_DAYS = 3650.0
MAX_POSITIONS = 20000
Z_VARIANCE = 2.0


def _f(v):
    try:
        x = None if v is None else float(v)
        return x if x is not None and math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _sum(rows, k):
    vals = [_f(r.get(k)) for r in rows]
    vals = [v for v in vals if v is not None]
    return (round(sum(vals), 2) if vals else None), len(vals)


def mid_cost(bids, offers, holding_side) -> float | None:
    """The mid of the holding side's executable book in COST space (the
    acquisition price and the exit price of the same contract), or None."""
    from . import bettor_paper_simulator as SIM
    md = {"bids": bids or [], "offers": offers or []}
    buy = SIM.levels_for(md, direction="BUY", holding_side=holding_side)
    sell = SIM.levels_for(md, direction="SELL", holding_side=holding_side)
    if not buy["levels"] or not sell["levels"]:
        return None
    return (float(buy["levels"][0]["price"])
            + float(sell["levels"][0]["price"])) / 2.0


def model_vs_variance(rows) -> dict:
    """Pure: realized outcome minus the model over ORDINARY settlements,
    against its binomial standard error."""
    diff, var, n = 0.0, 0.0, 0
    for r in rows:
        q, p, pi = (_f(r.get("entry_qty")), _f(r.get("p_decision")),
                    _f(r.get("payoff_per_contract")))
        if r.get("settlement_outcome") not in ("WON", "LOST") or None in (
                q, p, pi) or not (0.0 < p < 1.0):
            continue
        diff += q * (pi - p)
        var += q * q * p * (1.0 - p)
        n += 1
    if n == 0:
        return {"positions": 0, "why": "NO_ORDINARY_SETTLEMENT_WITH_P"}
    se = math.sqrt(var)
    z = None if se <= 0 else diff / se
    return {"positions": n, "outcome_minus_model_usd": round(diff, 2),
            "binomial_se_usd": round(se, 2),
            "z": None if z is None else round(z, 2),
            "verdict": (None if z is None else
                        "CONSISTENT_WITH_VARIANCE" if abs(z) <= Z_VARIANCE
                        else "MODEL_PROBABILITIES_MISCALIBRATED"),
            "rule": "|z| <= %.1f is variance" % Z_VARIANCE}


def build(rows: list, *, coverage: dict, missed: dict, spreads: dict,
          fixtures: dict, stale_entries: int) -> dict:
    """THE ATTRIBUTION (pure). `rows` intel.attribution PAPER rows;
    `coverage` {group: (reviews, complete)}; `missed` {group: exit value};
    `spreads` {group: spread usd}; `fixtures` {group: fixture}."""
    closed = [r for r in rows if _f(r.get("realized_pnl_usd")) is not None]
    comp = {}
    for k in ("realized_pnl_usd", "model_edge_usd", "slippage_usd",
              "fees_usd", "execution_edge_usd", "management_usd",
              "settlement_usd", "outcome_variance_usd"):
        v, n = _sum(closed, k)
        comp[k] = {"usd": v, "measured_positions": n}
    sp = [spreads[r["group_id"]] for r in closed
          if r.get("group_id") in spreads]
    comp["spread_paid_usd"] = {
        "usd": round(sum(sp), 2) if sp else None,
        "measured_positions": len(sp),
        "why_unmeasured": None if sp else "NO_ENTRY_BOOK_WITH_BOTH_SIDES"}
    by_strategy = collections.defaultdict(list)
    for r in closed:
        by_strategy[r.get("strategy") or "UNKNOWN"].append(r)
    strat = {s: {"positions": len(rs),
                 "realized_pnl_usd": _sum(rs, "realized_pnl_usd")[0],
                 "fees_usd": _sum(rs, "fees_usd")[0],
                 "model_edge_usd": _sum(rs, "model_edge_usd")[0],
                 "outcome_variance_usd": _sum(rs, "outcome_variance_usd")[0],
                 "management_usd": _sum(rs, "management_usd")[0],
                 "settlement_usd": _sum(rs, "settlement_usd")[0]}
             for s, rs in sorted(by_strategy.items())}
    cls = collections.defaultdict(list)
    for r in closed:
        rv, ok = coverage.get(r.get("group_id"), (0, 0))
        k = ("NO_REVIEW" if rv == 0 else "NEVER_A_COMPLETE_PACKET"
             if ok == 0 else "ALWAYS_COMPLETE" if ok == rv
             else "SOMETIMES_COMPLETE")
        cls[k].append(r)
    mgmt = {k: {"positions": len(rs),
                "realized_pnl_usd": _sum(rs, "realized_pnl_usd")[0]}
            for k, rs in sorted(cls.items())}
    mx = []
    for r in closed:
        g = r.get("group_id")
        if g not in missed:
            continue
        q_open, pi = _f(r.get("entry_qty")), _f(r.get("payoff_per_contract"))
        ev = _f(missed[g].get("exit_value_usd"))
        if r.get("sell_fills") or None in (q_open, pi, ev):
            continue
        mx.append({"group_id": g, "exit_value_at_review_usd": ev,
                   "held_paid_usd": round(q_open * pi, 2),
                   "exit_minus_hold_usd": round(ev - q_open * pi, 2),
                   "review_id": missed[g].get("review_id")})
    fx = collections.defaultdict(list)
    for r in closed:
        fx[fixtures.get(r.get("group_id")) or ("group:%s" % r.get(
            "group_id"))].append(r)
    multi = [r for k, rs in fx.items() if len(rs) > 1 for r in rs]
    single = [r for k, rs in fx.items() if len(rs) == 1 for r in rs]
    worst = sorted(((k, _sum(rs, "realized_pnl_usd")[0] or 0.0, len(rs))
                    for k, rs in fx.items()), key=lambda t: t[1])[:10]
    return {
        "version": VERSION, "label": "READ_ONLY_HISTORY_IMMUTABLE",
        "positions": len(rows), "closed_positions": len(closed),
        "open_positions_excluded": len(rows) - len(closed),
        "reconciling": sum(1 for r in closed if r.get("reconciles")),
        "not_reconciling": sum(1 for r in closed
                               if r.get("reconciles") is False),
        "components": comp,
        "identity": ("model edge + execution edge + management + "
                     "settlement + outcome variance = realized P&L "
                     "(intel.attribution, reconciled to cash per position)"),
        "model_error_vs_variance": model_vs_variance(closed),
        "by_strategy": strat,
        "management_coverage": mgmt,
        "loss_on_positions_xavier_could_not_manage_usd": (
            mgmt.get("NEVER_A_COMPLETE_PACKET") or {}).get(
                "realized_pnl_usd"),
        "missed_exits": {
            "positions": len(mx),
            "exit_minus_hold_usd": round(sum(
                m["exit_minus_hold_usd"] for m in mx), 2) if mx else None,
            "sample": sorted(mx, key=lambda m: -m["exit_minus_hold_usd"])[
                :10],
            "rule": ("a review ranked EXIT / REDUCE on an INCOMPLETE packet "
                     "(refused); the position was then held to settlement "
                     "with no sale")},
        "correlation": {
            "multi_position_fixtures": sum(1 for rs in fx.values()
                                           if len(rs) > 1),
            "multi_position_pnl_usd": _sum(multi, "realized_pnl_usd")[0],
            "single_position_pnl_usd": _sum(single, "realized_pnl_usd")[0],
            "worst_fixtures": [{"fixture": k, "pnl_usd": round(v, 2),
                                "positions": n} for k, v, n in worst]},
        "stale_information": {
            "entries_with_probability_over_limit": stale_entries,
            "rule": ("the entry gate refuses a probability over its limit;"
                     " any nonzero count is a defect")},
        "adverse_selection": {
            "usd": None,
            "why_unmeasured": ("needs a post-fill mark series per position;"
                               " not reconstructed from history here")},
    }


COVERAGE_SQL = """
    SELECT group_id, count(*) AS reviews,
           count(*) FILTER (WHERE (selection->'management_packet'->'gate'
                                   ->>'complete')::boolean) AS complete
      FROM paper_xavier_reviews
     WHERE group_id = ANY($1::text[])
     GROUP BY group_id
"""
MISSED_SQL = """
    SELECT DISTINCT ON (r.group_id) r.group_id, r.review_id,
           (SELECT (c->>'value_usd')::float8
              FROM jsonb_array_elements(coalesce(
                     r.alternatives->'candidates', '[]'::jsonb)) c
             WHERE c->>'action' = r.selection->>'mechanical_selection'
             LIMIT 1) AS exit_value_usd
      FROM paper_xavier_reviews r
     WHERE r.group_id = ANY($1::text[])
       AND r.selection->>'mechanical_selection' IN ('EXIT', 'REDUCE')
       AND NOT coalesce((r.selection->'management_packet'->'gate'
                         ->>'complete')::boolean, false)
     ORDER BY r.group_id, r.reviewed_at
"""
ENTRY_BOOK_SQL = """
    SELECT o.group_id, d.fixture, d.holding_side, b.bids, b.offers,
           (d.pinnacle->>'age_s')::float8 AS pin_age,
           (d.pinnacle->>'limit_s')::float8 AS pin_limit
      FROM paper_decisions d
      JOIN paper_orders o ON o.decision_id = d.decision_id
                         AND o.role = 'ENTRY'
      LEFT JOIN paper_book_observations b ON b.obs_id = d.book_obs_id
     WHERE o.group_id = ANY($1::text[]) AND d.verdict = 'ENTER'
"""


async def read(conn, *, now: float) -> dict:
    from . import bettor_paper_ledger as L
    from .intel import attribution as A
    rows = await A.load_paper(conn, now=now, days=HISTORY_DAYS,
                              limit=MAX_POSITIONS)
    gids = sorted({r["group_id"] for r in rows if r.get("group_id")})
    cov, missed, spreads, fixtures, stale = {}, {}, {}, {}, 0
    if gids:
        for r in await conn.fetch(COVERAGE_SQL, gids):
            cov[r["group_id"]] = (int(r["reviews"]), int(r["complete"]))
        for r in await conn.fetch(MISSED_SQL, gids):
            if r["exit_value_usd"] is not None:
                missed[r["group_id"]] = dict(r)
        vwap = {r["group_id"]: (_f(r.get("fill_vwap")),
                                _f(r.get("entry_qty"))) for r in rows}
        for r in await conn.fetch(ENTRY_BOOK_SQL, gids):
            g = r["group_id"]
            fixtures.setdefault(g, r["fixture"])
            if r["pin_age"] is not None and r["pin_limit"] is not None \
                    and r["pin_age"] > r["pin_limit"]:
                stale += 1
            if g in spreads:
                continue
            mid = mid_cost(L._j(r["bids"]), L._j(r["offers"]),
                           str(r["holding_side"]))
            v, q = vwap.get(g, (None, None))
            if mid is not None and v is not None and q is not None:
                spreads[g] = round(q * (v - mid), 6)
    return build(rows, coverage=cov, missed=missed, spreads=spreads,
                 fixtures=fixtures, stale_entries=stale)
