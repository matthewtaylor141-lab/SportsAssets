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


async def ledger_positions(conn, account_id: str) -> list:
    from .. import bettor_paper_ledger as L
    return await L.positions(conn, account_id, include_closed=True)


async def ledger_total(conn, account_id: str) -> D:
    return sum((D(str(p["realized_pnl_usd"])) for p in
                await ledger_positions(conn, account_id)), D(0))


def ledger_reconciliation(claimed: list, positions: list, *,
                          tolerance: D) -> dict:
    """The scoreboard's realized P&L against the PAPER LEDGER'S OWN realized
    P&L, group by group: for every entry group the scoreboard counts, the
    sum of its attributed cash P&L must equal the ledger's realized P&L over
    every position of that group (hedge legs included) once the group holds
    nothing open. A group still open in the ledger is reported, never
    counted as reconciled."""
    by_led: dict = {}
    for p in positions:
        g = by_led.setdefault(p["group_id"], {"realized": D(0), "open": D(0)})
        g["realized"] += D(str(p["realized_pnl_usd"]))
        g["open"] += D(str(p["open_qty"]))
    by_sb: dict = {}
    for r in claimed:
        gid = r.get("group_id")
        by_sb[gid] = by_sb.get(gid, D(0)) + D(str(r.get("cash_pnl_usd") or 0))
    matched, mismatched, still_open, absent = 0, [], [], []
    sb_total = led_total = D(0)
    for gid, v in sorted(by_sb.items(), key=lambda t: str(t[0])):
        led = by_led.get(gid)
        if led is None:
            absent.append(gid)
            continue
        if led["open"] > 0:
            still_open.append(gid)
            continue
        sb_total += v
        led_total += led["realized"]
        if abs(v - led["realized"]) <= tolerance:
            matched += 1
        else:
            mismatched.append({"group_id": gid, "scoreboard": str(v),
                               "ledger": str(led["realized"])})
    return {"green": not mismatched and not absent,
            "groups_counted": len(by_sb), "groups_reconciled": matched,
            "groups_mismatched": mismatched[:50],
            "groups_absent_from_ledger": absent[:50],
            "groups_still_open_in_ledger": len(still_open),
            "scoreboard_realized_usd": str(sb_total),
            "ledger_realized_usd": str(led_total),
            "residual_usd": str(sb_total - led_total),
            "tolerance_per_group_usd": str(tolerance),
            "basis": "PAPER ledger positions (bettor_paper_ledger.positions)"}


async def read(conn, *, now: float, account_id: str = "paper_acct_main",
               attributed=None, fixtures=None, read=None) -> dict:
    """`attributed` / `fixtures` / `read`: controls.attributed_positions'
    rows (EVERY PAPER position) and its read figures. A read that left out
    a position of the forward cohort is no scoreboard: UNAVAILABLE by name
    (ATTRIBUTION_READ_TRUNCATED), never one over a subset."""
    th = thresholds()
    since = cohort_start(th)
    if attributed is None:
        read = {}
        attributed, fixtures = await C.attributed_positions(conn, now=now,
                                                            detail=read)
    cohort_read = C.population_read(read, since=since, window_days=None)
    if cohort_read is not None:
        # the cohort's rule: every position since the frozen_at date (a
        # constant, never the clock)
        cohort_read["population_since"] = since
    if cohort_read is not None and not cohort_read["complete"]:
        return {"status": "UNAVAILABLE", "why": C.B_READ_TRUNCATED,
                "version": VERSION, "forward_cohort_start": since,
                "as_of": now, "positions_read": cohort_read}
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
    led = await ledger_positions(conn, account_id)
    total = sum((D(str(p["realized_pnl_usd"])) for p in led), D(0))
    ledrec = ledger_reconciliation(claim, led, tolerance=D(str(
        th["global"]["pnl_reconciliation_tolerance_usd"])))
    decision_rows = len([r for r in attributed
                         if float(r.get("decided_at") or 0) >= since])
    mechs, states = all_mechanisms(head, th)
    return {"version": VERSION, "thresholds_version": th["version"],
            "forward_cohort_start": since, "as_of": now,
            "decision_rows": decision_rows,
            "positions_read": cohort_read,
            "independent_events": {k: v.get("independent_events")
                                   for k, v in mechs.items()},
            "mechanisms": _s(mechs),
            "mechanism_states": _s(states),
            "routing": _s(head["routing"]),
            "routing_counts": routing_counts(pairs),
            "arbitrage": _s(head["arbitrage"]),
            "reconciliation": _s(rec),
            "ledger_reconciliation": ledrec,
            "paper_ledger_realized_total_usd": str(total),
            "theoretical_arb_is_not_realized": True,
            "authority": "READ_ONLY_EVIDENCE_NO_LEDGER"}


MECHANISMS = ("DIRECTIONAL", "SAME_VENUE_ARB", "CROSS_VENUE_ARB",
              "ROUTING_SAVINGS", "EXECUTION_ALPHA", "ALLOCATION_ALPHA",
              "XAVIER_MANAGEMENT")


def all_mechanisms(head: dict, th: dict) -> tuple:
    """Every one of the seven mechanisms, ALWAYS reported and never blended:
    the attribution mechanisms from their event rows (zero events = shadow
    only, never omitted), the arbitrage mechanisms from Adriana's claim-first
    records (theoretical, never realized), routing savings from the route
    receipts, allocation alpha zero (Allie resizes nothing)."""
    m = dict(head["mechanisms"])
    st = dict(head["mechanism_states"])
    for k in ("DIRECTIONAL", "EXECUTION_ALPHA", "XAVIER_MANAGEMENT"):
        if k not in m:
            m[k] = {"independent_events": 0, "realized_pnl": D(0),
                    "expected_pnl": D(0), "forward_pnl_lcb_per_event": None}
            st[k] = SB.classify_mechanism(k, m[k], th)
    for k, topo in (("SAME_VENUE_ARB", "SAME_VENUE"),
                    ("CROSS_VENUE_ARB", "CROSS_VENUE")):
        a = head["arbitrage"].get(topo) or {"opportunities": 0}
        m[k] = dict(a, independent_events=a.get("independent_events", 0),
                    realized_pnl=D(0),
                    basis="THEORETICAL_ONLY_SHADOW_NEVER_EXECUTION_LOCKED")
        st[k] = {"status": "SHADOW_ONLY",
                 "blockers": ("ADRIANA_SHADOW_ONLY_NO_SUBMIT_AUTHORITY",)}
    r = head["routing"]
    m["ROUTING_SAVINGS"] = dict(r, independent_events=None,
                                realized_pnl=D(0),
                                basis="SAVINGS_VS_NEXT_BEST_ROUTE_NOT_PNL")
    st["ROUTING_SAVINGS"] = {"status": "SHADOW_ONLY", "blockers": (
        "ROUTE_SAVINGS_ARE_COUNTERFACTUAL_NOT_REALIZED",)}
    m["ALLOCATION_ALPHA"] = {"independent_events": 0, "realized_pnl": D(0),
                             "basis": "ALLIE_SHADOW_WEIGHTS_RESIZE_NOTHING"}
    st["ALLOCATION_ALPHA"] = {"status": "SHADOW_ONLY", "blockers": (
        "NO_ALLOCATION_AUTHORITY",)}
    return {k: m[k] for k in MECHANISMS}, {k: st[k] for k in MECHANISMS}


def _s(x):
    if isinstance(x, dict):
        return {k: _s(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_s(v) for v in x]
    if isinstance(x, D):
        return str(x)
    return x
