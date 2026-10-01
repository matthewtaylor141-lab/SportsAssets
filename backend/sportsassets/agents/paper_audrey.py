"""AUDREY ON THE PAPER BOOK: CONTINUOUS MONITORING AND THE DAILY REPORT.

Everything reconciles to ONE paper ledger (`paper_ledger`): the report's cash
figures are the ledger's sums, the acquisition volume is the simulated cost
of FILLED purchases (and equals the ledger's FILL debits for the day), sale
proceeds are the ledger's SALE credits and are never acquisition, and each
fill carries exactly one role, so Derek's entries and Xavier's hedges and
sales are never double counted. The report states each reconciliation check
with both sides and `reconciles` is true only when all pass.

THE DAY is the America/New_York calendar day. A report is versioned: a new
version is written only when its content changes; the day's last version
after midnight is `final`.

DEFINITIONS, ENFORCED IN CODE:
  acquisition volume   sum(qty x price + fee) over BUY fills of the day,
                       ENTRY and HEDGE reported separately; SELL fills (exit,
                       reduce, standing protection) are sale proceeds
  distinct markets     count(DISTINCT us_market_slug) of the day's BUY fills
                       (repeat orders and both sides of one market count
                       once)
  distinct fixtures    count(DISTINCT fixture) of the same, separately
  net P&L              ending equity - starting equity (both from the one
                       derived-figures function on the ledger and marks);
                       realized and unrealized shown separately, with the
                       valuation method

DECISION QUALITY IS NOT HINDSIGHT. For every decision the report keeps what
was known at the decision (expected net EV, edge, the alternatives captured
then) apart from what happened (settled P&L), and never scores one with the
other.

SETTLEMENT uses authoritative evidence with correction history
(`paper_settlements` versions, CORRECTION entries), counted in the report.

THE IMPROVEMENT HOOK. A WARNING or CRITICAL finding, and every objective
shortfall, opens an agent task (kind PAPER_AUDIT_FINDING, evidence category
SIMULATED_WITH_DISCLOSED_ASSUMPTIONS) through the existing improvement
workflow's `create_task`, assigned to the agent that owns the cause. Nothing
is promoted: these tasks are not IMPROVEMENT candidates, and research
challengers stay separate.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import time
from typing import Any
from zoneinfo import ZoneInfo

from .. import bettor_paper_ledger as L
from .. import bettor_paper_simulator as SIM

VERSION = "PAPER_AUDREY_V1"
#: The decision-quality figures are the original two-model strategy's
#: (migration 182); the benchmark has its own section.
TWO_MODEL = "DEREK_ENTRY_POLICY_V2"
TASK_KIND = "PAPER_AUDIT_FINDING"
EVIDENCE_CATEGORY = "SIMULATED_WITH_DISCLOSED_ASSUMPTIONS"
REPORT_EVERY_S = 900.0


def _h(*parts) -> str:
    return hashlib.sha256(":".join(str(p) for p in parts).encode()
                          ).hexdigest()[:24]


def day_bounds(at: float, tz: str = "America/New_York") -> tuple:
    """(local date, start epoch, end epoch) of the reporting day of `at`."""
    z = ZoneInfo(tz)
    local = _dt.datetime.fromtimestamp(float(at), z)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + _dt.timedelta(days=1)
    return start.date(), start.timestamp(), end.timestamp()


async def _sum(conn, sql, *args) -> float:
    return float(await conn.fetchval(sql, *args) or 0)


async def build_report(conn, *, session: dict, account_id: str, day,
                       start: float, end: float, now: float) -> dict:
    """THE DAILY REPORT FOR [start, end), as far as `now`."""
    from .. import bettor_paper_readmodel as RM
    cfg = session["config"]
    obj = cfg["objectives"]
    until = min(float(now), end)
    # [start, until] inclusive of an instant stamped exactly `until`.
    t0, t1 = L._ts(start), L._ts(until + 1e-3 if until < end else end)
    # ── EQUITY ─────────────────────────────────────────────────────────
    s0 = await conn.fetchrow(
        "SELECT equity_usd, at FROM paper_equity_snapshots WHERE account_id=$1"
        "   AND at <= $2 AND equity_usd IS NOT NULL ORDER BY at DESC LIMIT 1",
        account_id, t0)
    start_equity = (float(s0["equity_usd"]) if s0 is not None else None)
    start_basis = ("the last complete equity snapshot at or before the "
                   "day's start" if s0 is not None else None)
    if s0 is None:
        first = await conn.fetchval(
            "SELECT min(committed_at) FROM paper_ledger WHERE account_id=$1",
            account_id)
        if first is None or L._epoch(first) >= start:
            start_equity = float(L.STARTING_CASH_USD)
            start_basis = "the account's starting cash (no activity before)"
    bal = await L.balances(conn, account_id, now=until)
    end_equity = bal["total_equity_usd"]
    # ── THE LEDGER FOR THE DAY ─────────────────────────────────────────
    led = {r["kind"]: {"cash": float(r["cash"]), "reserved":
                       float(r["res"]), "n": int(r["n"])}
           for r in await conn.fetch(
               "SELECT kind, sum(cash_delta_usd) AS cash, "
               "       sum(reserved_delta_usd) AS res, count(*) AS n "
               "  FROM paper_ledger WHERE account_id=$1 AND committed_at >= $2"
               "   AND committed_at < $3 GROUP BY kind", account_id, t0, t1)}
    # AS OF GENERATION: the balances below are the ledger's current sums.
    cash_at_end = await _sum(
        conn, "SELECT coalesce(sum(cash_delta_usd),0) FROM paper_ledger "
              " WHERE account_id=$1", account_id)
    # ── FILLS BY ROLE (each fill has exactly one role) ────────────────
    by_role = {r["role"]: {"direction": r["direction"],
                           "gross_usd": float(r["gross"]),
                           "fees_usd": float(r["fees"]), "qty": float(r["q"]),
                           "fills": int(r["n"])}
               for r in await conn.fetch(
                   "SELECT role, direction, sum(gross_usd) AS gross, "
                   "       sum(fee_usd) AS fees, sum(qty) AS q, count(*) AS n"
                   "  FROM paper_fills WHERE account_id=$1 "
                   "   AND filled_at >= $2 AND filled_at < $3 "
                   " GROUP BY role, direction", account_id, t0, t1)}

    def acq(role):
        r = by_role.get(role) or {}
        if r.get("direction") != "BUY":
            return 0.0
        return round(r["gross_usd"] + r["fees_usd"], 6)
    acq_entries, acq_hedges = acq("ENTRY"), acq("HEDGE")
    acq_total = round(acq_entries + acq_hedges, 6)
    sales = {r: round((by_role.get(r) or {}).get("gross_usd", 0.0)
                      - (by_role.get(r) or {}).get("fees_usd", 0.0), 6)
             for r in ("EXIT", "REDUCE", "STANDING_PROTECTION")}
    fees = round(sum(r["fees_usd"] for r in by_role.values()), 6)
    breadth = await conn.fetchrow(
        "SELECT count(DISTINCT us_market_slug) AS m, "
        "       count(DISTINCT fixture) AS f FROM paper_fills "
        " WHERE account_id=$1 AND direction='BUY' AND filled_at >= $2 "
        "   AND filled_at < $3", account_id, t0, t1)
    markets, fixtures = int(breadth["m"]), int(breadth["f"])
    # ── RECONCILIATION TO THE ONE LEDGER ──────────────────────────────
    # THE SAME FILLS, READ FROM THE LEDGER BY THEIR IDS (not by timestamp,
    # so a fill simulated a moment before midnight and committed after it
    # is compared with itself).
    pair = await conn.fetchrow(
        "SELECT coalesce(-sum(l.cash_delta_usd) FILTER (WHERE l.kind='FILL'),"
        "                0) AS debits, "
        "       coalesce(sum(l.cash_delta_usd) FILTER (WHERE l.kind='SALE'), "
        "                0) AS credits "
        "  FROM paper_ledger l JOIN paper_fills f ON f.fill_id = l.fill_id "
        " WHERE f.account_id=$1 AND f.filled_at >= $2 AND f.filled_at < $3",
        account_id, t0, t1)
    fill_debits = float(pair["debits"])
    sale_credits = float(pair["credits"])
    # SETTLEMENTS: every position's LATEST settlement version equals the
    # ledger's SETTLEMENT + CORRECTION credits (all time, through the end).
    sp = await conn.fetchrow(
        "SELECT coalesce(sum(payout_usd), 0) AS p FROM (SELECT DISTINCT ON "
        " (position_key, settlement_event_key) payout_usd FROM "
        " paper_settlements WHERE account_id=$1 "
        " ORDER BY position_key, settlement_event_key, version DESC) x",
        account_id)
    sl = await conn.fetchval(
        "SELECT coalesce(sum(cash_delta_usd), 0) FROM paper_ledger "
        " WHERE account_id=$1 AND kind IN ('SETTLEMENT', 'CORRECTION')",
        account_id)
    checks = [
        {"check": "ACQUISITION_EQUALS_LEDGER_FILL_DEBITS",
         "acquisition_from_fills_usd": acq_total,
         "ledger_fill_debits_for_these_fills_usd": round(fill_debits, 6),
         "passed": abs(acq_total - fill_debits) < 0.01},
        {"check": "SALE_PROCEEDS_EQUAL_LEDGER_SALE_CREDITS",
         "sale_proceeds_from_fills_usd": round(sum(sales.values()), 6),
         "ledger_sale_credits_for_these_fills_usd": round(sale_credits, 6),
         "passed": abs(sum(sales.values()) - sale_credits) < 0.01},
        {"check": "CASH_EQUALS_THE_LEDGER_SUM",
         "balances_cash_usd": bal["cash_usd"],
         "ledger_sum_usd": round(cash_at_end, 6),
         "passed": abs(bal["cash_usd"] - cash_at_end) < 0.01,
         "note": "both as of the report's generation"},
        {"check": "SETTLEMENTS_EQUAL_LEDGER_SETTLEMENT_AND_CORRECTIONS",
         "latest_settlement_payouts_usd": round(float(sp["p"]), 6),
         "ledger_settlement_and_correction_credits_usd": round(float(sl), 6),
         "passed": abs(float(sp["p"]) - float(sl)) < 0.01,
         "note": "all settlements to date, as of the report's generation"},
        {"check": "RUNNING_BALANCES_AGREE_WITH_THE_SUM",
         "passed": bool(bal["ledger_consistent"])},
        {"check": "EVERY_FILL_HAS_ONE_ROLE_NO_DOUBLE_COUNT",
         "fills_by_role": {k: v["fills"] for k, v in by_role.items()},
         "fills_total": int(await conn.fetchval(
             "SELECT count(*) FROM paper_fills WHERE account_id=$1 "
             "   AND filled_at >= $2 AND filled_at < $3", account_id, t0,
             t1)),
         "passed": True}]
    checks[-1]["passed"] = (sum(checks[-1]["fills_by_role"].values())
                            == checks[-1]["fills_total"])
    reconciles = all(c["passed"] for c in checks)
    # ── DECISIONS: QUALITY AT DECISION TIME, APART FROM HINDSIGHT ────
    dec = await conn.fetch(
        "SELECT verdict, coalesce(refusal, 'ENTER') AS reason, count(*) AS n,"
        "       count(DISTINCT us_market_slug) AS markets "
        "  FROM paper_decisions WHERE account_id=$1 AND decided_at >= $2 "
        "   AND decided_at < $3 AND strategy = $4 GROUP BY 1, 2 "
        " ORDER BY 3 DESC", account_id, t0, t1, TWO_MODEL)
    decisions = [{"strategy": TWO_MODEL, "verdict": r["verdict"],
                  "reason": r["reason"], "decisions": int(r["n"]),
                  "markets": int(r["markets"])} for r in dec]
    quality = await conn.fetchrow(
        "SELECT count(*) AS n, "
        "       sum((policy_decision->>'net_expected_profit_usd')::float8) "
        "         AS ev, avg((policy_decision->>'gross_edge_pp')::float8) "
        "         AS edge, "
        "       sum((optimistic->>'gross_usd')::float8) AS opt_gross, "
        "       sum((optimistic->>'filled_qty')::float8) AS opt_qty "
        "  FROM paper_decisions WHERE account_id=$1 AND verdict='ENTER' "
        "   AND decided_at >= $2 AND decided_at < $3 AND strategy = $4",
        account_id, t0, t1, TWO_MODEL)
    # AGAINST THE ALTERNATIVES CAPTURED AT DECISION TIME (knowable then)
    alt_rows = await conn.fetch(
        "SELECT verdict, alternatives, "
        "       (policy_decision->>'gross_edge_pp')::float8 AS edge_pp "
        "  FROM paper_decisions WHERE account_id=$1 AND decided_at >= $2 "
        "   AND decided_at < $3 AND alternatives IS NOT NULL "
        "   AND strategy = $4 LIMIT 2000", account_id, t0, t1, TWO_MODEL)
    better_opp, compared = 0, 0
    for r in alt_rows:
        opp = (L._j(r["alternatives"]) or {}).get(
            "OPPOSITE_SIDE_SAME_MARKET") or {}
        oe = opp.get("gross_edge_pp")
        if oe is None or r["edge_pp"] is None:
            continue
        compared += 1
        if float(oe) * 100.0 > float(r["edge_pp"]) + 1e-9:
            better_opp += 1
    vs_alternatives = {
        "compared": compared,
        "opposite_side_edge_exceeded_the_evaluated_side": better_opp,
        "no_trade_chosen": sum(1 for r in alt_rows
                               if r["verdict"] == "REFUSE"),
        "basis": ("alternatives captured AT the decision (NO_TRADE and the "
                  "opposite side of the same market on the same measure); a "
                  "question of decision quality, answerable without "
                  "outcomes")}
    settled = await conn.fetch(
        "SELECT s.group_id, s.outcome, s.payout_usd, s.version "
        "  FROM paper_settlements s WHERE s.account_id=$1 "
        "   AND s.settled_at >= $2 AND s.settled_at < $3", account_id, t0, t1)
    hindsight = {"settlements": len(settled),
                 "corrections": sum(1 for s in settled if s["version"] > 1),
                 "won": sum(1 for s in settled if s["outcome"] == "WON"),
                 "lost": sum(1 for s in settled if s["outcome"] == "LOST"),
                 "void": sum(1 for s in settled
                             if s["outcome"] == "VOID_REFUND"),
                 "payout_usd": round(sum(float(s["payout_usd"])
                                         for s in settled), 6)}
    reviews = await conn.fetch(
        "SELECT coalesce(recommendation, 'NONE') AS rec, trigger, "
        "       count(*) AS n FROM paper_xavier_reviews WHERE account_id=$1 "
        "   AND reviewed_at >= $2 AND reviewed_at < $3 GROUP BY 1, 2",
        account_id, t0, t1)
    # ── OBJECTIVES AND SHORTFALL CAUSES ───────────────────────────────
    net = (None if end_equity is None or start_equity is None
           else round(end_equity - start_equity, 6))
    lo_m, hi_m = obj["distinct_markets_per_day"]
    lo_p, hi_p = obj["net_pnl_usd_per_day"]
    target = {
        "acquisition_volume": {
            "objective_usd": obj["filled_acquisition_volume_usd_per_day"],
            "actual_usd": acq_total,
            "met": acq_total >= obj["filled_acquisition_volume_usd_per_day"],
            "shortfall_usd": round(max(
                0.0, obj["filled_acquisition_volume_usd_per_day"]
                - acq_total), 6)},
        "distinct_markets": {"objective": [lo_m, hi_m], "actual": markets,
                             "met": lo_m <= markets},
        "distinct_fixtures": {"actual": fixtures,
                              "counted_separately": True},
        "net_pnl": {"objective_usd": [lo_p, hi_p], "actual_usd": net,
                    "met": net is not None and net >= lo_p},
        "binding": False, "rule": obj.get("rule")}
    refusals = [d for d in decisions if d["verdict"] == "REFUSE"]
    causes = []
    if not decisions:
        causes.append({"cause": "NO_CANDIDATE_VALUATIONS_WERE_DECIDED",
                       "why": ("no entry-experiment valuation reached the "
                               "paper session this day")})
    for d in refusals[:8]:
        causes.append({"cause": d["reason"], "decisions": d["decisions"],
                       "markets": d["markets"]})
    risk = await conn.fetch(
        "SELECT detail->>'refusal' AS r, count(*) AS n FROM "
        " paper_audrey_findings WHERE account_id=$1 AND kind=$2 "
        "   AND found_at >= $3 AND found_at < $4 GROUP BY 1", account_id,
        "PAPER_RISK_REFUSED_THE_ORDER", t0, t1)
    for r in risk:
        causes.append({"cause": "PAPER_RISK:%s" % r["r"],
                       "orders": int(r["n"])})
    # ── DATA GAPS AND EXECUTION ASSUMPTIONS ───────────────────────────
    book_err = await conn.fetchrow(
        "SELECT count(*) AS n, count(*) FILTER (WHERE error IS NOT NULL) "
        "       AS bad FROM paper_book_observations WHERE observed_at >= $1 "
        "   AND observed_at < $2", t0, t1)
    passes = await conn.fetchval(
        "SELECT passes FROM paper_session_health WHERE session_id=$1",
        session["session_id"])
    gaps = {"book_reads": int(book_err["n"]),
            "unreadable_books": int(book_err["bad"]),
            "unmarked_positions": bal["unmarked_positions"],
            "stale_marks": bal["stale_marks"],
            "session_passes_total": passes,
            "qualification_gaps_every_decision": [
                "MODEL_APPROVAL", "SOURCE_CALIBRATION",
                "QUOTE_TIMING_UNCERTAINTY_P5", "EXECUTION_MODEL_ASSUMPTIONS",
                "SETTLEMENT_INTERPRETATION"]}
    dd = await RM.drawdown(conn, account_id, since=start, until=until)
    rep = {
        "version": VERSION, "report_day": str(day),
        "reporting_tz": session.get("reporting_tz", "America/New_York"),
        "window": {"start": start, "end": end, "through": until},
        "data_label": L.DATA_LABEL, "labels": dict(L.LABELS),
        "paper_fills_are": "SIMULATED (PAPER_SIM_V1), not verified execution",
        "equity": {"starting_usd": start_equity,
                   "starting_basis": start_basis,
                   "ending_usd": end_equity,
                   "ending_basis": bal["equity_basis"],
                   "ending_excluding_unmarked_usd":
                       bal["equity_excluding_unmarked_usd"],
                   "cash_usd": bal["cash_usd"],
                   "reserved_usd": bal["reserved_usd"],
                   "available_usd": bal["available_usd"]},
        "pnl": {"net_usd": net,
                "realized_total_usd": bal["realized_pnl_usd"],
                "unrealized_usd": bal["unrealized_pnl_usd"],
                "valuation_method": bal["mark_method"],
                "fees_usd": fees,
                "floors_are_not_realized": True},
        "drawdown": dd,
        "open_exposure": {
            "open_positions": len(bal["open_positions"]),
            "cost_basis_usd": round(sum(p["cost_basis_usd"]
                                        for p in bal["open_positions"]), 6),
            "reserved_for_open_orders_usd": bal["reserved_usd"]},
        "acquisition_volume": {"entries_usd": acq_entries,
                               "hedges_usd": acq_hedges,
                               "total_usd": acq_total,
                               "definition": obj["definitions"][
                                   "acquisition_volume"]},
        "sale_proceeds": dict(sales, total_usd=round(sum(sales.values()), 6),
                              never_acquisition=True),
        "breadth": {"distinct_markets": markets,
                    "distinct_fixtures": fixtures,
                    "definition": obj["definitions"]["distinct_markets"]},
        "targets": target, "shortfall_causes": causes,
        "decisions": decisions,
        "decision_quality_at_decision_time": {
            "strategy": TWO_MODEL,
            "entries": int(quality["n"] or 0),
            "expected_net_usd": quality["ev"],
            "mean_gross_edge_pp": quality["edge"],
            "basis": "what was known at each decision; not scored on "
                     "outcomes"},
        "decisions_vs_alternatives_at_decision_time": vs_alternatives,
        "hindsight": dict(hindsight, basis=(
            "authoritative settlements booked this day, with corrections; "
            "never used to re-score a decision")),
        "optimistic_sensitivity": {
            "gross_usd": quality["opt_gross"],
            "filled_qty": quality["opt_qty"],
            "primary_acquisition_usd": acq_total,
            "basis": ("decision-time book, no delay, no consumption ledger, "
                      "touch fills: a bound, never the primary result")},
        "xavier_reviews": [{"recommendation": r["rec"],
                            "trigger": r["trigger"], "n": int(r["n"])}
                           for r in reviews],
        "data_gaps": gaps,
        "execution_assumptions": SIM.ASSUMPTIONS,
        "ledger_by_kind": led,
        "reconciliation": {"checks": checks, "reconciles": reconciles,
                           "one_ledger": "paper_ledger"},
    }
    # THE EXPERIMENTAL PINNACLE_ONLY_PAPER_BENCHMARK, reported in its own
    # labelled section (the decision figures above are the two-model
    # strategy's; equity, P&L, acquisition, breadth and the reconciliation
    # stay account-wide on the one ledger). Present only once the benchmark
    # has recorded anything, so a report without it is unchanged.
    from . import paper_benchmark as PB
    if await PB.has_records(conn, account_id):
        rep["pinnacle_only_paper_benchmark"] = await PB.report_section(
            conn, account_id=account_id, t0=t0, t1=t1)
    return rep


async def write_report(conn, *, session: dict, account_id: str,
                       now: float) -> dict:
    day, start, end = day_bounds(now, session.get("reporting_tz")
                                 or "America/New_York")
    rep = await build_report(conn, session=session, account_id=account_id,
                             day=day, start=start, end=end, now=now)
    body = dict(rep)
    digest = hashlib.sha256(json.dumps(
        {k: v for k, v in body.items() if k != "window"}, sort_keys=True,
        default=str).encode()).hexdigest()
    last = await conn.fetchrow(
        "SELECT version, digest FROM paper_audrey_reports WHERE "
        " session_id=$1 AND report_day=$2 ORDER BY version DESC LIMIT 1",
        session["session_id"], day)
    if last is not None and last["digest"] == digest:
        return {"written": False, "report_day": str(day),
                "version": last["version"], "why": "NO_CHANGE",
                "report": rep}
    ver = 1 if last is None else int(last["version"]) + 1
    rid = "paperrep:" + _h(session["session_id"], day, ver)
    await conn.execute(
        "INSERT INTO paper_audrey_reports (report_id, session_id, account_id,"
        " report_day, reporting_tz, version, generated_at, final, reconciles,"
        " report, digest) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11)"
        " ON CONFLICT DO NOTHING", rid, session["session_id"], account_id,
        day, rep["reporting_tz"], ver, L._ts(now), float(now) >= end,
        rep["reconciliation"]["reconciles"], json.dumps(rep, default=str),
        digest)
    return {"written": True, "report_id": rid, "report_day": str(day),
            "version": ver, "report": rep}


# ═════════════════════════════════════════════════════════════════════
# CONTINUOUS MONITORING AND THE IMPROVEMENT HOOK
# ═════════════════════════════════════════════════════════════════════

OWNER_OF = {"LEDGER_INCONSISTENT": "AUDREY",
            "VENUE_MUTATION_ATTEMPTED": "AUDREY",
            "UNMARKED_OPEN_POSITION": "XAVIER",
            "STALE_MARK": "XAVIER",
            "GROUP_WITHOUT_AN_OWNER": "XAVIER",
            "ORDER_PAST_EXPIRY_STILL_OPEN": "AUDREY",
            "REPORT_DOES_NOT_RECONCILE": "AUDREY",
            "CONFLICTING_SETTLEMENT_EVIDENCE": "AUDREY",
            "OBJECTIVE_SHORTFALL": "DEREK",
            "PAPER_RISK_REFUSED_THE_ORDER": "DEREK"}


async def finding(conn, ctx: dict, *, kind: str, subject: str,
                  detail: dict, severity: str, scope: str = "") -> dict:
    fid = "paperfind:" + _h(ctx["session_id"], kind, subject, scope)
    got = await conn.fetchval(
        "INSERT INTO paper_audrey_findings (finding_id, session_id, "
        " account_id, found_at, kind, severity, subject, detail) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb) ON CONFLICT DO NOTHING "
        "RETURNING finding_id", fid, ctx["session_id"], ctx["account_id"],
        L._ts(ctx["now"]), kind, severity, subject,
        json.dumps(detail, default=str))
    return {"finding_id": fid, "new": got is not None, "kind": kind,
            "severity": severity}


async def open_task(conn, ctx: dict, f: dict, *, detail: dict) -> dict:
    """THE IMPROVEMENT HOOK: one task per finding, idempotent, never an
    IMPROVEMENT candidate (nothing is promoted from here)."""
    from . import improvement as IMP
    tid = "paper-task:%s" % f["finding_id"].split(":", 1)[1]
    got = await IMP.create_task(
        conn, assignee=OWNER_OF.get(f["kind"], "AUDREY"), created_by="AUDREY",
        kind=TASK_KIND, title="Paper audit: %s" % f["kind"],
        spec={"finding_id": f["finding_id"], "kind": f["kind"],
              "severity": f["severity"], "session_id": ctx["session_id"],
              "evidence_category": EVIDENCE_CATEGORY,
              "promotion": "NONE: a paper finding opens investigation work; "
                           "research challengers stay separate",
              "detail": detail},
        evidence=[{"kind": "paper_audrey_findings",
                   "id": f["finding_id"],
                   "href": "/api/command/paper/audrey"}],
        task_id=tid, now=ctx["now"])
    if got.get("ok"):
        await conn.execute(
            "UPDATE paper_audrey_findings SET improvement_task_id=$2 "
            " WHERE finding_id=$1 AND improvement_task_id IS NULL",
            f["finding_id"], tid)
    return {"task_id": tid, "ok": got.get("ok"),
            "created": got.get("created"), "refusal": got.get("refusal")}


async def monitor(conn, ctx: dict) -> list:
    acct = ctx["account_id"]
    now = float(ctx["now"])
    out = []
    bal = await L.balances(conn, acct, now=now)
    if not bal["ledger_consistent"]:
        out.append(await finding(conn, ctx, kind="LEDGER_INCONSISTENT",
                                 subject=acct, severity="CRITICAL",
                                 detail={"last_sequence":
                                         bal["last_sequence"]},
                                 scope=str(bal["last_sequence"])))
    h = await conn.fetchrow(
        "SELECT mutation_attempts, last_mutation_attempt FROM "
        " paper_session_health WHERE session_id=$1", ctx["session_id"])
    if h is not None and int(h["mutation_attempts"]) > 0:
        out.append(await finding(
            conn, ctx, kind="VENUE_MUTATION_ATTEMPTED", subject=acct,
            severity="CRITICAL",
            detail={"mutation_attempts": int(h["mutation_attempts"]),
                    "last": L._j(h["last_mutation_attempt"]),
                    "refused_before_transmission": True},
            scope=str(h["mutation_attempts"])))
    for pk in bal["unmarked_positions"]:
        out.append(await finding(conn, ctx, kind="UNMARKED_OPEN_POSITION",
                                 subject=pk, severity="WARNING",
                                 detail={"why": "no available mark; equity "
                                         "is stated as incomplete"}))
    for pk in bal["stale_marks"]:
        out.append(await finding(conn, ctx, kind="STALE_MARK", subject=pk,
                                 severity="INFO",
                                 detail={"stale_after_s":
                                         L.MARK_STALE_AFTER_S}))
    for r in await conn.fetch(
            "SELECT DISTINCT f.group_id FROM paper_fills f WHERE "
            " f.account_id=$1 AND f.role='ENTRY' AND NOT EXISTS (SELECT 1 "
            " FROM paper_handoffs h WHERE h.group_id=f.group_id)", acct):
        out.append(await finding(conn, ctx, kind="GROUP_WITHOUT_AN_OWNER",
                                 subject=r["group_id"], severity="WARNING",
                                 detail={"why": "filled entry, no handoff"}))
    for r in await conn.fetch(
            "SELECT order_id, expires_at FROM paper_orders WHERE "
            " account_id=$1 AND state = ANY($2::text[]) AND expires_at < "
            " to_timestamp($3) - interval '10 minutes'", acct,
            list(L.OPEN_STATES), now):
        out.append(await finding(
            conn, ctx, kind="ORDER_PAST_EXPIRY_STILL_OPEN",
            subject=r["order_id"], severity="WARNING",
            detail={"expires_at": L._epoch(r["expires_at"])}))
    # EVERY FILLED PINNACLE_ONLY_PAPER_BENCHMARK ENTRY: its fills each have
    # their ledger entry and the group was handed to Xavier (nothing to do
    # while the benchmark has recorded nothing).
    from . import paper_benchmark as PB
    if await PB.has_records(conn, acct):
        out.extend(await PB.audit_fills(conn, ctx, finding))
    return out


async def step(conn, ctx: dict) -> dict:
    """EVERY PASS: monitor. EVERY REPORT_EVERY_S (and at the day's turn): the
    daily report. WARNING/CRITICAL findings and shortfalls open tasks."""
    findings = await monitor(conn, ctx)
    sess = ctx["session"]
    # THE REPORT COVERS EVERYTHING RECORDED SO FAR: its instant is the later
    # of the decision clock and the last simulated fill (a fill simulated
    # after the delay is stamped after the pass began).
    last_fill = L._epoch(await conn.fetchval(
        "SELECT max(filled_at) FROM paper_fills WHERE account_id=$1",
        ctx["account_id"]))
    clock = ctx.get("clock") or (lambda: float(ctx["now"]))
    rep_now = max(float(clock()), last_fill or 0.0)
    last = await conn.fetchrow(
        "SELECT generated_at, report_day FROM paper_audrey_reports "
        " WHERE session_id=$1 ORDER BY generated_at DESC LIMIT 1",
        sess["session_id"])
    day, start, _ = day_bounds(rep_now, sess.get("reporting_tz")
                               or "America/New_York")
    rep = None
    due = (last is None or rep_now - L._epoch(
        last["generated_at"]) >= REPORT_EVERY_S or last["report_day"] != day)
    if due:
        # The previous day's final version first, when the day turned.
        if last is not None and last["report_day"] != day:
            await write_report(conn, session=sess,
                               account_id=ctx["account_id"],
                               now=start - 0.001)
        rep = await write_report(conn, session=sess,
                                 account_id=ctx["account_id"], now=rep_now)
        r = rep["report"]
        if not r["reconciliation"]["reconciles"]:
            findings.append(await finding(
                conn, ctx, kind="REPORT_DOES_NOT_RECONCILE",
                subject=str(day), severity="CRITICAL",
                detail=r["reconciliation"], scope=str(rep.get("version"))))
        t = r["targets"]
        short = [k for k in ("acquisition_volume", "distinct_markets",
                             "net_pnl") if not t[k].get("met")]
        if short and rep.get("written"):
            findings.append(await finding(
                conn, ctx, kind="OBJECTIVE_SHORTFALL", subject=str(day),
                severity="WARNING",
                detail={"short": short, "targets": t,
                        "causes": r["shortfall_causes"][:8],
                        "binding": False}))
    tasks = []
    for f in findings:
        if f["new"] and f["severity"] in ("WARNING", "CRITICAL"):
            tasks.append(await open_task(conn, ctx, f, detail={
                "kind": f["kind"]}))
    return {"findings": len(findings),
            "new_findings": sum(1 for f in findings if f["new"]),
            "tasks_opened": sum(1 for t in tasks if t.get("created")),
            "report": (None if rep is None else {
                k: rep.get(k) for k in ("written", "report_id", "report_day",
                                        "version", "why")})}
