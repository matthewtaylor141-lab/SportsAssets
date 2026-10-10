"""AUDREY ON THE PAPER BOOK: CONTINUOUS MONITORING AND THE DAILY REPORT.

Everything reconciles to ONE paper ledger (`paper_ledger`): the report's cash
figures are the ledger's sums, the acquisition volume is the simulated cost
of FILLED purchases (and equals the ledger's FILL debits for the day), sale
proceeds are the ledger's SALE credits and are never acquisition, and each
fill carries exactly one role, so Derek's entries and Xavier's hedges and
sales are never double counted. The report states each reconciliation check
with both sides and `reconciles` is true only when all pass.

THE DAY is the America/New_York calendar day. A report is versioned: a new
version is written only when its content changes. A reported day is closed
by ONE `final` version, written at the first pass after the day is over (the
next day, or days later after a gap in which no pass ran): its window is the
whole day (through the day's last millisecond, recorded again if its content
is unchanged), while its balance figures are the ledger's sums when that
closing version was written (the row's `recorded_at`), as every version's
are at its writing. Nothing is appended to a day after its final version. A
day with no report (no pass ran on it) gets none, final or otherwise; the
days before the session's first closed day are history and are not closed
retroactively. Final never implies `reconciles`.

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

from .. import bettor_paper_epoch as EP
from .. import bettor_paper_ledger as L
from .. import bettor_paper_simulator as SIM

VERSION = "PAPER_AUDREY_V1"
#: The decision-quality figures are the original two-model strategy's
#: (migration 182); the benchmark has its own section.
TWO_MODEL = "DEREK_ENTRY_POLICY_V2"
TASK_KIND = "PAPER_AUDIT_FINDING"
EVIDENCE_CATEGORY = "SIMULATED_WITH_DISCLOSED_ASSUMPTIONS"
REPORT_EVERY_S = 900.0
#: The class of the transaction-scoped advisory lock that serializes the
#: report writes of one (session, reporting day): pg_advisory_xact_lock(
#: REPORT_LOCK_CLASS, hashtext('<session>:<day>')). The two-key space never
#: meets the paper pass's single-key lock.
REPORT_LOCK_CLASS = 0x41554452


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


def day_window(day: _dt.date, tz: str = "America/New_York") -> tuple:
    """(local date, start epoch, end epoch) of the reporting day `day`."""
    z = ZoneInfo(tz)
    start = _dt.datetime.combine(day, _dt.time(0, 0), tzinfo=z)
    end = _dt.datetime.combine(day + _dt.timedelta(days=1), _dt.time(0, 0),
                               tzinfo=z)
    return day, start.timestamp(), end.timestamp()


async def _sum(conn, sql, *args) -> float:
    return float(await conn.fetchval(sql, *args) or 0)


def management_checks(book: dict, *, ledger_cash_now,
                      ledger_cash_at_epoch) -> list:
    """AUDREY'S OWN DERIVATION of the management book from two ledger sums
    she reads herself: management cash (incl. reserved) = ledger cash now -
    (ledger cash at the epoch - opening cash) - post-epoch ledger cash held
    outside. Each check states both sides."""
    op = book.get("opening") or {}
    opening = float(book.get("opening_equity_usd") or 0)
    sides = (float(op.get("available_cash_usd") or 0)
             + float(op.get("reserved_usd") or 0)
             + float(op.get("carried_position_mark_value_usd") or 0))
    held = float((book.get("ledger_reconciliation") or {}).get(
        "post_epoch_cash_held_outside_usd") or 0)
    audrey_cash = (float(ledger_cash_now) - (float(ledger_cash_at_epoch)
                   - float(op.get("cash_including_reserved_usd") or 0))
                   - held)
    eq = float(book.get("equity_usd") or 0)
    pnl = (float(book.get("realized_pnl_usd") or 0)
           + float(book.get("unrealized_pnl_usd") or 0))
    audrey_equity = (audrey_cash
                     + float(book.get("marked_open_position_value_usd") or 0)
                     + float(book.get("unmarked_carried_at_basis_usd") or 0))
    return [
        {"check": "MANAGEMENT_OPENING_EQUALS_OPENING_EQUITY",
         "available_plus_reserved_plus_carried_usd": round(sides, 6),
         "opening_equity_usd": opening,
         "passed": abs(sides - opening) < 0.01},
        {"check": "MANAGEMENT_CASH_EQUALS_THE_REBASED_LEDGER",
         "management_cash_including_reserved_usd":
             book.get("cash_including_reserved_usd"),
         "audrey_from_ledger_sums_usd": round(audrey_cash, 6),
         "passed": abs(float(book.get("cash_including_reserved_usd") or 0)
                       - audrey_cash) < 0.01},
        {"check": "MANAGEMENT_EQUITY_EQUALS_OPENING_PLUS_PNL",
         "management_equity_usd": eq,
         "audrey_equity_from_ledger_usd": round(audrey_equity, 6),
         "opening_plus_realized_plus_unrealized_usd": round(opening + pnl, 6),
         "passed": (abs(eq - audrey_equity) < 0.01
                    and abs(eq - (opening + pnl)) < 0.01)}]


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
    # ── THE MANAGEMENT EPOCH, RECONCILED TO THE SAME LEDGER ──────────
    management = None
    if float(now) >= EP.EPOCH_START:
        management = await EP.read(conn, account_id, bal=bal, now=until)
        e0 = await _sum(
            conn, "SELECT coalesce(sum(cash_delta_usd),0) FROM paper_ledger "
                  " WHERE account_id=$1 AND (committed_at < $2 "
                  "   OR kind = 'INITIAL_FUNDING')", account_id,
            L._ts(EP.EPOCH_START))
        checks.extend(management_checks(management, ledger_cash_now=cash_at_end,
                                        ledger_cash_at_epoch=e0))
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
        "management_epoch": (None if management is None else {
            k: management.get(k) for k in (
                "epoch_id", "epoch_start", "label", "opening_equity_usd",
                "opening", "equity_usd", "cash_usd", "reserved_usd",
                "marked_open_position_value_usd", "realized_pnl_usd",
                "unrealized_pnl_usd", "total_pnl_usd", "return_pct",
                "drawdown_usd", "carried_positions", "carried_unverified",
                "status", "ledger_reconciliation")}),
    }
    # THE EXPERIMENTAL PINNACLE_ONLY_PAPER_BENCHMARK, reported in its own
    # labelled section (the decision figures above are the two-model
    # strategy's; equity, P&L, acquisition, breadth and the reconciliation
    # stay account-wide on the one ledger). Present only once the benchmark
    # has recorded anything, so a report without it is unchanged.
    from . import paper_benchmark as PB
    for pol in PB.POLICIES:
        if await PB.has_records(conn, account_id, pol):
            rep[pol["report_key"]] = await PB.report_section(
                conn, account_id=account_id, t0=t0, t1=t1, pol=pol)
    return rep


async def write_report(conn, *, session: dict, account_id: str,
                       now: float, closed_at: float | None = None) -> dict:
    """THE REPORT OF THE DAY OF `now`, AS FAR AS `now`, versioned.

    FINAL IS JUDGED AGAINST THE DAY THE REPORT COVERS. `closed_at` is the
    instant the caller observed after that day (step() passes its report
    clock when it writes a day's closing version with `now` at the day's
    last millisecond); the version is final only when `closed_at` is at or
    after the end of the covered day. An intra-day version (no `closed_at`,
    or one inside the day) is never final. `final` says the day is closed,
    never that it reconciles: `reconciles` is the report's own
    reconciliation either way.

    THE TABLE IS APPEND-ONLY, so a stored version cannot be marked final
    afterwards: a closing write whose content is unchanged since the day's
    last version records that same content again as the final version.

    ONE FINAL VERSION PER DAY, AND NOTHING AFTER IT. A day with a final
    version in ANY of its versions is closed: every later write for it,
    final or not, is refused (ALREADY_FINAL) and nothing is appended. A
    closing write for a day that has no version is refused
    (NO_REPORT_TO_CLOSE): a day no pass reported gets no report.

    ATOMIC PER (session, day). The build, the read of the day's versions and
    the insert run in one transaction under pg_advisory_xact_lock(
    REPORT_LOCK_CLASS, hashtext('<session>:<day>')), so two writers of one
    day are serialized (the second sees the first's version), and `written`
    is true only when the insert stored the row: an insert that stored
    nothing (a writer outside the lock took the version) returns written =
    false with what the day now holds (VERSION_TAKEN, or ALREADY_FINAL)."""
    tz = session.get("reporting_tz") or "America/New_York"
    sid = session["session_id"]
    day, start, end = day_bounds(now, tz)
    final = closed_at is not None and float(closed_at) >= end

    def out(written, why, version, rep, **kw):
        return dict({"written": written, "report_day": str(day),
                     "version": version, "why": why, "report": rep}, **kw)

    state_sql = (
        "SELECT max(version) AS version, "
        "       coalesce(bool_or(final), false) AS closed, "
        "       max(version) FILTER (WHERE final) AS final_version, "
        "       (array_agg(digest ORDER BY version DESC))[1] AS digest "
        "  FROM paper_audrey_reports WHERE session_id=$1 AND report_day=$2")
    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock($1, hashtext($2))",
                           REPORT_LOCK_CLASS, f"{sid}:{day}")
        # built under the lock, so a closing version is built after every
        # earlier version of the day is visible
        rep = await build_report(conn, session=session,
                                 account_id=account_id, day=day, start=start,
                                 end=end, now=now)
        body = dict(rep)
        digest = hashlib.sha256(json.dumps(
            {k: v for k, v in body.items() if k != "window"}, sort_keys=True,
            default=str).encode()).hexdigest()
        st = await conn.fetchrow(state_sql, sid, day)
        if st["closed"]:
            return out(False, "ALREADY_FINAL", st["final_version"], rep,
                       final=True)
        if final and st["version"] is None:
            return out(False, "NO_REPORT_TO_CLOSE", None, rep, final=False)
        if st["version"] is not None and not final \
                and st["digest"] == digest:
            return out(False, "NO_CHANGE", st["version"], rep, final=False)
        ver = 1 if st["version"] is None else int(st["version"]) + 1
        rid = "paperrep:" + _h(sid, day, ver)
        got = await conn.fetchval(
            "INSERT INTO paper_audrey_reports (report_id, session_id, "
            " account_id, report_day, reporting_tz, version, generated_at, "
            " final, reconciles, report, digest) VALUES ($1,$2,$3,$4,$5,$6,"
            " $7,$8,$9,$10::jsonb,$11) ON CONFLICT DO NOTHING "
            " RETURNING report_id", rid, sid, account_id, day,
            rep["reporting_tz"], ver, L._ts(now), final,
            rep["reconciliation"]["reconciles"],
            json.dumps(rep, default=str), digest)
    if got is None:
        st = await conn.fetchrow(state_sql, sid, day)
        if st["closed"]:
            return out(False, "ALREADY_FINAL", st["final_version"], rep,
                       final=True)
        return out(False, "VERSION_TAKEN", st["version"], rep, final=False)
    return out(True, None, ver, rep, report_id=rid, final=final)


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
            "PAPER_RISK_REFUSED_THE_ORDER": "DEREK",
            # THE EVENT AUDITS (agents/paper_learning.py, migration 185)
            "PAPER_EVENT_FIRST_FILL": "DEREK",
            "PAPER_EVENT_HANDOFF": "XAVIER",
            "PAPER_EVENT_MANAGEMENT_FILL": "XAVIER",
            "PAPER_EVENT_SETTLEMENT": "AUDREY",
            "PAPER_EVENT_SETTLED_AT_VENUE_PRICE": "AUDREY",
            "PAPER_EVENT_EXCEPTIONAL_OUTCOME": "DEREK",
            "PAPER_EVENT_LEDGER_INCONSISTENCY": "AUDREY"}


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
    for pol in PB.POLICIES:
        if await PB.has_records(conn, acct, pol):
            out.extend(await PB.audit_fills(conn, ctx, finding, pol))
    return out


async def days_to_close(conn, *, session: dict, closed_at: float) -> list:
    """THE REPORTED DAYS THAT ARE OVER AND HAVE NO FINAL VERSION, oldest
    first: every day after the session's newest closed day that has a
    version and no final version, and whose end is at or before `closed_at`.
    A session with no closed day yet closes only its newest reported day:
    the days before it were reported before any day was closed and are
    history (no retroactive closing). A day with no version is never a
    candidate (no pass reported it, so it gets no report)."""
    tz = session.get("reporting_tz") or "America/New_York"
    rows = await conn.fetch(
        "WITH d AS (SELECT report_day, bool_or(final) AS closed "
        "             FROM paper_audrey_reports WHERE session_id=$1 "
        "            GROUP BY report_day) "
        "SELECT report_day FROM d WHERE NOT closed AND report_day > "
        "       coalesce((SELECT max(report_day) FROM d WHERE closed), "
        "                (SELECT max(report_day) FROM d) - 1) "
        " ORDER BY report_day", session["session_id"])
    return [w for w in (day_window(r["report_day"], tz) for r in rows)
            if w[2] <= float(closed_at)]


async def close_days(conn, *, session: dict, account_id: str,
                     closed_at: float) -> list:
    """WRITE THE ONE FINAL VERSION OF EVERY REPORTED DAY THAT IS OVER
    (days_to_close), oldest first: the day's report through its last
    millisecond, closed at `closed_at`. Stops at the first day whose closing
    version was not stored and is not already final (a writer outside the
    lock took its version), so a later day is never closed past an open
    one; the next pass retries it."""
    out = []
    for day, _start, end in await days_to_close(conn, session=session,
                                                closed_at=closed_at):
        got = await write_report(conn, session=session,
                                 account_id=account_id, now=end - 0.001,
                                 closed_at=closed_at)
        out.append({k: got.get(k) for k in ("report_day", "written",
                                            "version", "why", "final")})
        if not got.get("final"):
            break
    return out


#: why a family account's day close could not run (its session is absent):
#: a reporting state, the pass is not failed for it
FAMILY_SESSION_UNAVAILABLE = "PAPER_ARCHIVE_SESSION_UNAVAILABLE"
#: (rc6.3 pr5-port) the outgoing account's switch-day version was not
#: stored (a closing write or the version itself was taken by a writer
#: outside the per-day lock): the switch is refused, nothing moves
SWITCH_VERSION_NOT_STORED = "PAPER_SWITCH_AUDREY_VERSION_NOT_STORED"


async def last_activity_at(conn, account_id: str) -> float | None:
    """THE INSTANT OF THE ACCOUNT'S LAST RECORDED ECONOMIC ACTIVITY: the
    latest of its last simulated fill (`filled_at`), its last settlement
    version (`settled_at`, a correction included) and its last ledger entry
    (`committed_at`). Fills and settlement versions carry the clock of the
    pass that recorded them, ledger entries the database clock; every
    ledger entry that moves cash or P&L has a fill or a settlement version,
    so a revision is seen on either clock."""
    return L._epoch(await conn.fetchval(
        "SELECT greatest((SELECT max(filled_at) FROM paper_fills "
        "                  WHERE account_id=$1), "
        "                (SELECT max(settled_at) FROM paper_settlements "
        "                  WHERE account_id=$1), "
        "                (SELECT max(committed_at) FROM paper_ledger "
        "                  WHERE account_id=$1))", account_id))


async def write_switch_version(conn, *, account_id: str, at: float) -> dict:
    """THE OUTGOING ACCOUNT'S REPORT AT A PAPER SELECTOR SWITCH.

    After an activation or a rollback the paper pass runs this step only for
    the newly selected account, so the outgoing account's day would keep its
    last REPORT_EVERY_S version and miss everything recorded after it (a
    late settlement, its last realized loss) -- in no Audrey report at all.
    The switch (bettor_paper_day_one, under the PAPER lock and the account's
    own lock, before the selector moves) therefore does for the outgoing
    session what step() does at a pass: close every reported day that is
    over, then write the version of its current day as far as the later of
    the switch instant and the account's last activity (last_activity_at) --
    everything recorded on the account up to the switch. That version is an
    intra-day one (never final); the day's ONE final version is written once
    the day is over by the selected account's step (close_family_days).

    A VERSION THAT IS NOT STORED REFUSES THE SWITCH: a closing write that
    stops on a day it could not close, or a current-day write that a writer
    outside the per-day lock took (VERSION_TAKEN / ALREADY_FINAL), raises
    SWITCH_VERSION_NOT_STORED, so the caller refuses the switch by name. A
    write that stores nothing because the day's last version already holds
    exactly this content (NO_CHANGE) is the report as far as the switch."""
    from .. import bettor_paper_session as S
    session = await S.active_session(conn, account_id)
    if session is None:
        raise ValueError(FAMILY_SESSION_UNAVAILABLE + ":" + account_id)
    # A REPORT CLOCK AHEAD OF THE SWITCH (a pass whose clock ran ahead of
    # the database's) is never reported backwards: the version's instant is
    # at least the newest report's, and a day that is already final moves it
    # to the next day's first instant (the balances are the ledger's sums at
    # the writing either way).
    newest = L._epoch(await conn.fetchval(
        "SELECT max(generated_at) FROM paper_audrey_reports "
        " WHERE session_id=$1", session["session_id"]))
    now = max(float(at), await last_activity_at(conn, account_id) or 0.0,
              newest or 0.0)
    closing = await close_days(conn, session=session, account_id=account_id,
                               closed_at=now)
    if closing and not closing[-1].get("final"):
        raise ValueError(SWITCH_VERSION_NOT_STORED + ":CLOSING:"
                         + str(closing[-1].get("why")))
    tz = session.get("reporting_tz") or "America/New_York"
    if await conn.fetchval(
            "SELECT coalesce(bool_or(final), false) FROM paper_audrey_reports"
            " WHERE session_id=$1 AND report_day=$2", session["session_id"],
            day_bounds(now, tz)[0]):
        now = day_bounds(now, tz)[2]
    rep = await write_report(conn, session=session, account_id=account_id,
                             now=now)
    if not rep.get("written") and rep.get("why") != "NO_CHANGE":
        raise ValueError(SWITCH_VERSION_NOT_STORED + ":" + str(rep.get("why")))
    return {"account_id": account_id, "session_id": session["session_id"],
            "at": now, "closing": closing, "report_held": None,
            "report": {k: rep.get(k) for k in ("written", "report_id",
                                               "report_day", "version",
                                               "why", "final")}}


async def family_activity_version(conn, *, session: dict, account_id: str,
                                  closed_at: float) -> dict | None:
    """A FAMILY ACCOUNT'S CURRENT-DAY VERSION FOR ACTIVITY AFTER ITS LAST
    REPORT. Xavier's production settlement pass keeps applying settlement
    revisions to every account of the family (paper_xavier.step_settle walks
    risk_history_accounts), so an archived or rolled-back account can record
    a correction after its last reported day is closed. Without a version
    for it that correction -- a realized gain or loss on the ledger -- would
    be in no Audrey report. When the account's last activity
    (last_activity_at) is later than its newest report's `generated_at`,
    this writes the version of its current day as far as the later of
    `closed_at` and that activity (an intra-day version, never final); the
    family day close then writes that day's one final version once it is
    over, exactly as for the switch day. A report clock behind the newest
    report's day writes nothing (as in step()). None when nothing is due."""
    act = await last_activity_at(conn, account_id)
    last = await conn.fetchrow(
        "SELECT generated_at, report_day FROM paper_audrey_reports "
        " WHERE session_id=$1 ORDER BY generated_at DESC LIMIT 1",
        session["session_id"])
    if act is None or last is None or act <= L._epoch(last["generated_at"]):
        return None
    now = max(float(closed_at), act)
    day, _s, _e = day_bounds(now, session.get("reporting_tz")
                             or "America/New_York")
    if last["report_day"] > day:
        return {"written": False,
                "why": "REPORT_CLOCK_BEHIND_THE_NEWEST_REPORT"}
    rep = await write_report(conn, session=session, account_id=account_id,
                             now=now)
    return {k: rep.get(k) for k in ("written", "report_id", "report_day",
                                    "version", "why", "final")}


async def close_family_days(conn, *, account_id: str,
                            closed_at: float) -> dict:
    """THE DAY CLOSE OF EVERY OTHER ACCOUNT OF THE SELECTED ACCOUNT'S EPOCH
    FAMILY (simulated_account_context.risk_history_accounts: the archived
    source, rolled-back children, sibling epochs). A deselected account's
    session no longer gets step(), so its last reported day -- the day of
    the switch -- would never get its final version. Each family session's
    reported days that are over by `closed_at` get their one final version
    exactly as the selected session's do (days_to_close / close_days, the
    same per-(session, day) lock and append-only rules). THEN, when the
    family account recorded activity after its newest report (a settlement
    revision Xavier's family settle applied after the switch day closed),
    its current day gets a version carrying it (family_activity_version),
    which this same day close finalizes once that day is over. Nothing else
    is written for a family account (no monitoring, no task, and no version
    while it records nothing new). An account outside the family is never
    touched; before any epoch exists the family is the selected account
    alone, so this writes nothing."""
    from ..simulated_account_context import risk_history_accounts
    from .. import bettor_paper_session as S
    out = {}
    for aid in await risk_history_accounts(conn, account_id):
        if aid == account_id:
            continue
        sess = await S.active_session(conn, aid)
        if sess is None:
            out[aid] = [{"written": False, "why": FAMILY_SESSION_UNAVAILABLE}]
            continue
        out[aid] = await close_days(conn, session=sess, account_id=aid,
                                    closed_at=closed_at)
        if out[aid] and not out[aid][-1].get("final"):
            continue        # a day it could not close: retried next pass
        got = await family_activity_version(conn, session=sess,
                                            account_id=aid,
                                            closed_at=closed_at)
        if got is not None:
            out[aid] = out[aid] + [dict(got, activity_version=True)]
    return out


async def step(conn, ctx: dict) -> dict:
    """EVERY PASS: monitor, and close every reported day that is over (its
    one final version) -- the selected account's and every other account of
    its epoch family's (close_family_days). EVERY REPORT_EVERY_S (and at the
    day's turn): the daily report. WARNING/CRITICAL findings and shortfalls
    open tasks."""
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
    # THE CLOSING VERSIONS FIRST: every reported day that is over by the
    # report clock -- the day before, or the last reported day after a gap
    # of whole days in which no pass ran -- gets its one final version.
    closing = await close_days(conn, session=sess,
                               account_id=ctx["account_id"],
                               closed_at=rep_now)
    held = bool(closing) and not closing[-1].get("final")
    last = await conn.fetchrow(
        "SELECT generated_at, report_day FROM paper_audrey_reports "
        " WHERE session_id=$1 ORDER BY generated_at DESC LIMIT 1",
        sess["session_id"])
    day, _start, _ = day_bounds(rep_now, sess.get("reporting_tz")
                                or "America/New_York")
    # A REPORT CLOCK BEHIND THE NEWEST REPORT'S DAY (a clock stepped back,
    # or a host behind the one that ran the last pass) writes nothing: the
    # day it reads is already reported past, or closed.
    behind = last is not None and last["report_day"] > day
    rep = None
    due = not held and not behind and (
        last is None or rep_now - L._epoch(last["generated_at"])
        >= REPORT_EVERY_S or last["report_day"] != day)
    if due:
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
    # THE FAMILY'S DAY CLOSE: a deselected account's reported day that is
    # over gets its one final version here (its own step no longer runs),
    # and activity it recorded after its newest report gets a version
    family = await close_family_days(conn, account_id=ctx["account_id"],
                                     closed_at=rep_now)
    return {"findings": len(findings),
            "family_closing": family,
            "family_days_closed": sum(1 for v in family.values() for c in v
                                      if c.get("written")
                                      and not c.get("activity_version")),
            "family_activity_versions": sum(
                1 for v in family.values() for c in v
                if c.get("written") and c.get("activity_version")),
            "new_findings": sum(1 for f in findings if f["new"]),
            "tasks_opened": sum(1 for t in tasks if t.get("created")),
            # the closing writes of this pass (each day's outcome), and the
            # number of days it closed -- a scalar, so the pass digest and
            # the heartbeat carry it
            "closing": closing,
            "days_closed": sum(1 for c in closing if c.get("written")),
            "report_held": ("CLOSING_VERSION_NOT_STORED" if held else
                            "REPORT_CLOCK_BEHIND_THE_NEWEST_REPORT"
                            if behind else None),
            "report": (None if rep is None else {
                k: rep.get(k) for k in ("written", "report_id", "report_day",
                                        "version", "why")})}
