"""THE FORWARD PROFITABILITY SCOREBOARD (PM Evidence Pack, component 2) --
a READ layer over BETTOR's existing truth, never a second ledger:

  DIRECTIONAL / EXECUTION_ALPHA / XAVIER_MANAGEMENT
      per settled PAPER position, the existing attribution identity
      (intel.attribution: model edge + execution + management + settlement
      + outcome variance = realized = the ledger's cash), one value per
      independent canonical event (fixture)
  ROUTING_SAVINGS
      canonical_route_receipts: chosen all-in vs the next-best route, qty,
      Kalshi NO over Kalshi YES / Kalshi over PMUS / PMUS over Kalshi
  SAME_VENUE_ARB / CROSS_VENUE_ARB
      Adriana's claim-first opportunities (migration 265) with the
      sentinel's pair receipts (315): theoretical profit only; matched 0,
      never execution-locked, never realized P&L
  ALLOCATION_ALPHA
      Allie resizes nothing (SHADOW weights): no realized dollar

The thresholds are the pack's frozen V1 (data/thresholds.json); the forward
cohort starts at its frozen_at date. P&L reconciles to the PAPER ledger
within the frozen tolerance or the receipt says it does not.
"""
from __future__ import annotations

import json
import pathlib
from datetime import datetime, timezone
from decimal import Decimal

from ..pm_evidence.scoreboard import scoreboard as SB
from ..redteam import controls as C

VERSION = "FORWARD_SCOREBOARD_BIND_V1"
DATA = pathlib.Path(__file__).resolve().parents[1] / "pm_evidence" / "data"
D = Decimal


def thresholds() -> dict:
    return json.loads((DATA / "thresholds.json").read_text())


def cohort_start(th: dict) -> float:
    return datetime.fromisoformat(th["frozen_at"]).replace(
        tzinfo=timezone.utc).timestamp()


def event_rows(attributed: list, fixtures: dict, *, since: float) -> list:
    out = []
    for r in attributed:
        if not r.get("identity_claimed") or r.get("realized_pnl_usd") is None:
            continue
        if float(r.get("decided_at") or 0) < since:
            continue
        ev = fixtures.get(r.get("group_id")) or r.get("us_market_slug") or "?"
        fees_ok = r.get("fees_usd") is not None
        me, var = D(str(r.get("model_edge_usd") or 0)), D(str(
            r.get("outcome_variance_usd") or 0))
        for mech, exp, rea in (
                ("DIRECTIONAL", me, me + var),
                ("EXECUTION_ALPHA", D(0),
                 D(str(r.get("execution_edge_usd") or 0))),
                ("XAVIER_MANAGEMENT", D(0),
                 D(str(r.get("management_usd") or 0)))):
            out.append(SB.EventRow(event_key=ev, mechanism=mech,
                                   expected_pnl=exp, realized_pnl=rea,
                                   settlement_proven=True,
                                   fees_complete=fees_ok, fresh=True))
    return out


async def routing_rows(conn, *, since: float) -> tuple:
    """([RoutingRow], [(chosen, runner_up)])"""
    if not await C._has(conn, "canonical_route_receipts"):
        return [], []
    out, pairs = [], []
    for r in await conn.fetch(
            "SELECT receipt_id, event_key, claim_fingerprint, qty, "
            "       best_single, runner_up FROM canonical_route_receipts "
            " WHERE computed_at >= to_timestamp($1) AND best_single IS NOT "
            " NULL ORDER BY computed_at DESC LIMIT 2000", since):
        b, n = C._j(r["best_single"]) or {}, C._j(r["runner_up"]) or {}
        if not b.get("all_in_per_contract"):
            continue
        out.append(SB.RoutingRow(
            decision_id=r["receipt_id"], event_key=r["event_key"],
            chosen_route="%s:%s:%s" % (b.get("venue"), b.get("market_id"),
                                       b.get("side")),
            chosen_all_in=D(str(b["all_in_per_contract"])),
            next_best_all_in=(D(str(n["all_in_per_contract"]))
                              if n.get("all_in_per_contract") else None),
            qty=D(int(r["qty"] or 0)), claim_key=r["claim_fingerprint"],
            fresh=True))
        pairs.append((b, n))
    return out, pairs


def routing_counts(pairs: list) -> dict:
    """How often a Kalshi NO beat the equivalent Kalshi YES, Kalshi beat
    PMUS, PMUS beat Kalshi (chosen vs runner-up of one claim)."""
    c = {"kalshi_no_beat_kalshi_yes": 0, "kalshi_beat_pmus": 0,
         "pmus_beat_kalshi": 0, "comparable": 0}
    for b, n in pairs:
        if not n:
            continue
        c["comparable"] += 1
        bv, nv = b.get("venue"), n.get("venue")
        if bv == "KALSHI" and nv == "KALSHI" and b.get("side") == "NO" \
                and n.get("side") == "YES":
            c["kalshi_no_beat_kalshi_yes"] += 1
        if bv == "KALSHI" and nv == "POLYMARKET_US":
            c["kalshi_beat_pmus"] += 1
        if bv == "POLYMARKET_US" and nv == "KALSHI":
            c["pmus_beat_kalshi"] += 1
    return c


async def arb_rows(conn, *, since: float) -> list:
    if not await C._has(conn, "adriana_arb_opportunities"):
        return []
    sent = {}
    if await C._has(conn, "red_team_pair_execution_receipts"):
        for r in await conn.fetch(
                "SELECT DISTINCT ON (pair_id) pair_id, target_qty, "
                " matched_qty, execution_locked, state FROM "
                " red_team_pair_execution_receipts ORDER BY pair_id, "
                " observed_at DESC"):
            sent[r["pair_id"]] = dict(r)
    out = []
    for r in await conn.fetch(
            "SELECT o.opportunity_id, o.scan_id, o.event_key, o.max_qty, "
            "       o.net_profit_usd, o.economics FROM "
            "adriana_arb_opportunities o JOIN adriana_arb_scans s USING "
            "(scan_id) WHERE s.started_at >= to_timestamp($1) AND "
            "o.scan_id LIKE 'adr-claims-%' LIMIT 2000", since):
        eco = C._j(r["economics"]) or {}
        cp = eco.get("claim_pair") or {}
        from ..redteam import sentinel as S
        pid = S.pair_id_of({"economics": eco}, r["scan_id"])
        st = sent.get(pid) or {}
        out.append(SB.ArbRow(
            opportunity_id=r["opportunity_id"], event_key=r["event_key"],
            topology=cp.get("topology") or "?",
            theoretical_profit=D(str(r["net_profit_usd"] or 0)),
            matched_qty=D(str(st.get("matched_qty") or 0)),
            target_qty=D(str(st.get("target_qty") or r["max_qty"] or 0)),
            realized_or_shadow_pnl=D(0),
            payoff_floor_proven=True,
            settlement_proven=bool(cp.get("rules")),
            fees_complete=all(x.get("fee") is not None
                              for x in eco.get("legs") or []),
            books_synchronized=True,
            execution_locked=bool(st.get("execution_locked"))))
    return out


async def ledger_total(conn, account_id: str) -> D:
    from .. import bettor_paper_ledger as L
    pos = await L.positions(conn, account_id, include_closed=True)
    return sum((D(str(p["realized_pnl_usd"])) for p in pos), D(0))


async def read(conn, *, now: float, account_id: str = "paper_acct_main",
               attributed=None, fixtures=None) -> dict:
    th = thresholds()
    since = cohort_start(th)
    if attributed is None:
        attributed, fixtures = await C.attributed_positions(conn, now=now)
    ev = event_rows(attributed, fixtures or {}, since=since)
    ro, pairs = await routing_rows(conn, since=since)
    ar = await arb_rows(conn, since=since)
    head = SB.headline_scoreboard(ev, ro, ar, th)
    # the accounting identity over the SAME positions, to the ledger cash
    claim = [r for r in attributed if r.get("identity_claimed")
             and r.get("realized_pnl_usd") is not None
             and float(r.get("decided_at") or 0) >= since]

    def s(k):
        return sum((D(str(r.get(k) or 0)) for r in claim), D(0))
    rec = SB.reconcile_total(
        mechanism_realized={"DIRECTIONAL": s("model_edge_usd"),
                            "EXECUTION_ALPHA": s("execution_edge_usd"),
                            "XAVIER_MANAGEMENT": s("management_usd"),
                            "SAME_VENUE_ARB": D(0), "CROSS_VENUE_ARB": D(0),
                            "ROUTING_SAVINGS": D(0),
                            "ALLOCATION_ALPHA": D(0)},
        settlement_adjustment=s("settlement_usd"),
        outcome_variance=s("outcome_variance_usd"),
        reported_total=s("cash_pnl_usd"),
        tolerance=D(str(th["global"]["pnl_reconciliation_tolerance_usd"])))
    total = await ledger_total(conn, account_id)
    decision_rows = len([r for r in attributed
                         if float(r.get("decided_at") or 0) >= since])
    mechs = head["mechanisms"]
    return {"version": VERSION, "thresholds_version": th["version"],
            "forward_cohort_start": since, "as_of": now,
            "decision_rows": decision_rows,
            "independent_events": {k: v.get("independent_events")
                                   for k, v in mechs.items()},
            "mechanisms": _s(mechs),
            "mechanism_states": _s(head["mechanism_states"]),
            "routing": _s(head["routing"]),
            "routing_counts": routing_counts(pairs),
            "arbitrage": _s(head["arbitrage"]),
            "reconciliation": _s(rec),
            "paper_ledger_realized_total_usd": str(total),
            "theoretical_arb_is_not_realized": True,
            "authority": "READ_ONLY_EVIDENCE_NO_LEDGER"}


def _s(x):
    if isinstance(x, dict):
        return {k: _s(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_s(v) for v in x]
    if isinstance(x, D):
        return str(x)
    return x
