"""PAPER READ MODELS: what /api/command/paper/* returns. Read-only.

Every payload carries `data_label` ("LIVE MARKET DATA / SIMULATED
EXECUTION"), `labels`, `as_of` and `last_updated_at`, and every section is
independently {"status": OK | EMPTY | UNAVAILABLE, "why", "data"}. Nothing
here reads a funded table: paper totals are never mixed with funded totals.
The shapes are documented in docs/PAPER_TRADING_READ_MODELS.md.
"""
from __future__ import annotations

import time
from typing import Any

from . import bettor_paper_ledger as L
from . import bettor_paper_session as S


async def _section(coro, *, empty_why: str, is_empty=None) -> dict:
    try:
        data = await coro
    except Exception as exc:                                    # noqa: BLE001
        return {"status": "UNAVAILABLE",
                "why": "%s: %s" % (type(exc).__name__, str(exc)[:160]),
                "data": None}
    empty = is_empty(data) if is_empty is not None else not data
    if empty:
        return {"status": "EMPTY", "why": empty_why, "data": data}
    return {"status": "OK", "why": None, "data": data}


def _base(now: float | None = None) -> dict:
    return {"data_label": L.DATA_LABEL, "labels": dict(L.LABELS),
            "as_of": float(now if now is not None else time.time()),
            "paper_only": ("paper totals; never mixed with funded totals")}


def _row(r) -> dict:
    out = {}
    for k, v in dict(r).items():
        if hasattr(v, "as_tuple"):
            out[k] = float(v)
        elif hasattr(v, "timestamp"):
            out[k] = float(v.timestamp())
        elif hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        elif isinstance(v, str) and v[:1] in "[{":
            j = L._j(v)
            out[k] = v if j is None else j
        else:
            out[k] = v
    return out


async def _last_updated(conn, account_id: str) -> float | None:
    return L._epoch(await conn.fetchval(
        "SELECT max(committed_at) FROM paper_ledger WHERE account_id=$1",
        account_id))


# ═════════════════════════════════════════════════════════════════════
# EQUITY SNAPSHOTS AND DRAWDOWN
# ═════════════════════════════════════════════════════════════════════

async def snapshot_equity(conn, *, session_id: str, account_id: str,
                          now: float) -> dict:
    b = await L.balances(conn, account_id, now=now)
    await conn.execute(
        "INSERT INTO paper_equity_snapshots (session_id, account_id, at, "
        " cash_usd, reserved_usd, marked_value_usd, equity_usd, "
        " equity_excluding_unmarked_usd, unmarked_positions, "
        " realized_pnl_usd, unrealized_pnl_usd, last_sequence) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12) "
        "ON CONFLICT DO NOTHING",
        session_id, account_id, L._ts(now), L.D(b["cash_usd"]),
        L.D(b["reserved_usd"]),
        L.D(b["open_position_value_marked_only_usd"]),
        (None if b["total_equity_usd"] is None
         else L.D(b["total_equity_usd"])),
        L.D(b["equity_excluding_unmarked_usd"]),
        len(b["unmarked_positions"]), L.D(b["realized_pnl_usd"]),
        (None if b["unrealized_pnl_usd"] is None
         else L.D(b["unrealized_pnl_usd"])), b["last_sequence"])
    return {"at": now, "equity_usd": b["total_equity_usd"],
            "marks_complete": b["marks_complete"]}


async def drawdown(conn, account_id: str = L.ACCOUNT_ID, *,
                   since: float | None = None,
                   until: float | None = None) -> dict:
    """PEAK-TO-TROUGH on the recorded equity snapshots (starting cash is the
    first peak). A snapshot with incomplete marks has no equity and is
    skipped, and counted."""
    rows = await conn.fetch(
        "SELECT at, equity_usd FROM paper_equity_snapshots "
        " WHERE account_id=$1 AND ($2::timestamptz IS NULL OR at >= $2) "
        "   AND ($3::timestamptz IS NULL OR at <= $3) ORDER BY at",
        account_id, None if since is None else L._ts(since),
        None if until is None else L._ts(until))
    peak = float(L.STARTING_CASH_USD) if since is None else None
    max_dd, max_dd_pct, skipped, cur = 0.0, 0.0, 0, None
    for r in rows:
        if r["equity_usd"] is None:
            skipped += 1
            continue
        e = float(r["equity_usd"])
        cur = e
        peak = e if peak is None else max(peak, e)
        dd = peak - e
        if dd > max_dd:
            max_dd = dd
            max_dd_pct = dd / peak if peak else 0.0
    return {"snapshots": len(rows), "skipped_incomplete_marks": skipped,
            "peak_equity_usd": peak, "current_equity_usd": cur,
            "current_drawdown_usd": (None if cur is None or peak is None
                                     else round(peak - cur, 6)),
            "max_drawdown_usd": round(max_dd, 6),
            "max_drawdown_pct": round(max_dd_pct * 100.0, 6),
            "basis": ("peak-to-trough over paper_equity_snapshots (one per "
                      "paper pass), marks as in bettor_paper_ledger."
                      "MARK_METHOD")}


# ═════════════════════════════════════════════════════════════════════
# THE PAYLOADS
# ═════════════════════════════════════════════════════════════════════

async def session_payload(conn, *, account_id: str = L.ACCOUNT_ID,
                          now: float | None = None) -> dict:
    out = _base(now)
    sess = await S.active_session(conn, account_id)
    en = await S.enablement(conn)
    out["enablement"] = {"status": "OK", "why": None, "data": en}
    if sess is None:
        out["session"] = {"status": "EMPTY",
                          "why": ("NO_ACTIVE_PAPER_SESSION: %s"
                                  % (en.get("refusal")
                                     or "the scheduled pass has not run")),
                          "data": None}
        out["health"] = {"status": "EMPTY", "why": "NO_ACTIVE_PAPER_SESSION",
                         "data": None}
        out["last_updated_at"] = await _last_updated(conn, account_id)
        return out
    out["session"] = {"status": "OK", "why": None, "data": sess}
    out["health"] = await _section(
        S.health(conn, sess["session_id"]),
        empty_why="NO_HEALTH_RECORD_YET")
    h = out["health"].get("data") or {}
    out["mutation_attempts"] = h.get("mutation_attempts")
    out["heartbeats"] = h.get("recent_heartbeats")
    out["last_updated_at"] = h.get("heartbeat_at")
    return out


async def derek_payload(conn, *, account_id: str = L.ACCOUNT_ID,
                        limit: int = 100, now: float | None = None) -> dict:
    out = _base(now)

    async def decisions():
        rows = await conn.fetch(
            "SELECT decision_id, session_id, decided_at, valuation_id, "
            "       us_market_slug, holding_side, intent, fixture, label, "
            "       verdict, refusal, refusals, p_internal, internal_model, "
            "       p_pinnacle, pinnacle, p_blended, book_obs_id, book, "
            "       proposed_qty, limit_price, economics, "
            "       qualification_gaps, policy_version, alternatives, "
            "       optimistic, simulator_version, strategy, "
            "       policy_decision "
            "  FROM paper_decisions WHERE account_id=$1 "
            " ORDER BY decided_at DESC LIMIT $2", account_id, limit)
        return [_with_explanation(_row(r)) for r in rows]

    async def summary():
        rows = await conn.fetch(
            "SELECT verdict, coalesce(refusal, 'ENTER') AS reason, "
            "       strategy, count(*) AS n FROM paper_decisions "
            " WHERE account_id=$1 "
            "   AND decided_at > now() - interval '24 hours' "
            " GROUP BY 1, 2, 3 ORDER BY 4 DESC", account_id)
        return [_row(r) for r in rows]

    async def orders():
        rows = await conn.fetch(
            "SELECT * FROM paper_orders WHERE account_id=$1 "
            "   AND role IN ('ENTRY') ORDER BY created_at DESC LIMIT $2",
            account_id, limit)
        return [L.order_view(r) for r in rows]

    async def fills():
        rows = await conn.fetch(
            "SELECT * FROM paper_fills WHERE account_id=$1 AND role='ENTRY' "
            " ORDER BY filled_at DESC LIMIT $2", account_id, limit)
        return [_row(r) for r in rows]

    async def handoffs():
        rows = await conn.fetch(
            "SELECT * FROM paper_handoffs WHERE account_id=$1 "
            " ORDER BY created_at DESC LIMIT $2", account_id, limit)
        return [_row(r) for r in rows]

    out["opportunities"] = await _section(
        decisions(), empty_why=("NO_PAPER_DECISION_YET: Derek records one "
                                "per evaluated market once the session runs"))
    out["refusal_summary_24h"] = await _section(
        summary(), empty_why="NO_PAPER_DECISION_IN_THE_LAST_24_HOURS")
    out["orders"] = await _section(
        orders(), empty_why=("NO_PAPER_ENTRY_ORDER: no decision said ENTER, "
                             "or none has been simulated yet"))
    out["fills"] = await _section(
        fills(), empty_why="NO_SIMULATED_ENTRY_FILL")
    out["handoffs"] = await _section(
        handoffs(), empty_why="NO_HANDOFF: Xavier takes a group from its "
                              "first simulated fill")
    out["last_updated_at"] = await _last_updated(conn, account_id)
    return out


async def xavier_payload(conn, *, account_id: str = L.ACCOUNT_ID,
                         limit: int = 100, now: float | None = None) -> dict:
    at = float(now if now is not None else time.time())
    out = _base(at)

    async def positions():
        b = await L.balances(conn, account_id, now=at)
        return b.get("open_positions") or []

    async def standing():
        rows = await conn.fetch(
            "SELECT * FROM paper_orders WHERE account_id=$1 "
            "   AND role IN ('STANDING_PROTECTION', 'HEDGE', 'EXIT', "
            "                'REDUCE') ORDER BY created_at DESC LIMIT $2",
            account_id, limit)
        return [L.order_view(r) for r in rows]

    async def recs():
        rows = await conn.fetch(
            "SELECT DISTINCT ON (group_id) * FROM paper_xavier_reviews "
            " WHERE account_id=$1 ORDER BY group_id, reviewed_at DESC "
            " LIMIT $2", account_id, limit)
        return [_row(r) for r in rows]

    out["positions"] = await _section(
        positions(), empty_why="NO_OPEN_PAPER_POSITION")
    out["standing_orders"] = await _section(
        standing(), empty_why="NO_PAPER_MANAGEMENT_ORDER")
    out["recommendations"] = await _section(
        recs(), empty_why="NO_XAVIER_PAPER_REVIEW_YET")
    out["last_updated_at"] = await _last_updated(conn, account_id)
    return out


async def audrey_payload(conn, *, account_id: str = L.ACCOUNT_ID,
                         limit: int = 50, now: float | None = None) -> dict:
    out = _base(now)

    async def reports():
        rows = await conn.fetch(
            "SELECT DISTINCT ON (report_day) * FROM paper_audrey_reports "
            " WHERE account_id=$1 ORDER BY report_day DESC, version DESC "
            " LIMIT $2", account_id, limit)
        return [_row(r) for r in rows]

    async def findings():
        rows = await conn.fetch(
            "SELECT * FROM paper_audrey_findings WHERE account_id=$1 "
            " ORDER BY found_at DESC LIMIT $2", account_id, limit)
        return [_row(r) for r in rows]

    out["daily_reports"] = await _section(
        reports(), empty_why="NO_PAPER_DAILY_REPORT_YET")
    out["audit_entries"] = await _section(
        findings(), empty_why="NO_PAPER_AUDIT_FINDING")
    out["last_updated_at"] = await _last_updated(conn, account_id)
    return out


#: THE STRATEGY OF A RECORD (migration 182) and a one-line explanation of
#: its decision, on every decision the Derek panel reads.
TWO_MODEL = "DEREK_ENTRY_POLICY_V2"


def explanation(d: dict, pd: dict | None) -> str:
    """WHY THIS DECISION, in one line, from what the record holds."""
    pd = pd if isinstance(pd, dict) else {}
    strategy = d.get("strategy") or TWO_MODEL
    if strategy == "PINNACLE_EXPLORATION_PAPER":
        est = pd.get("estimate") or {}
        sel = pd.get("selection") or {}
        return ("%s%s. TRAINING / SIMULATED EXECUTION: estimated gross edge "
                "%s pp, fee %s per contract, expected profit after fees %s "
                "(may be negative: a research cost, not investment "
                "performance); selection probability %s (%s)" % (
                    d.get("verdict"), "" if not d.get("refusal")
                    else " " + str(d.get("refusal")),
                    est.get("gross_edge_pp_at_best"),
                    est.get("fee_per_contract_usd"),
                    est.get("expected_net_profit_usd"),
                    sel.get("selection_probability"), sel.get("method")))
    if strategy == "PINNACLE_COMPLETED_GAME_MAKER_PAPER":
        return ("%s%s. RESTING ENTRY: %s Threshold %s pp; taker fee charged, "
                "maker rebate not assumed; an order is not a fill" % (
                    d.get("verdict"), "" if not d.get("refusal")
                    else " " + str(d.get("refusal")),
                    pd.get("rationale") or "", pd.get("threshold_edge_pp")))
    if strategy == "PINNACLE_COMPLETED_GAME_PAPER":
        sh = pd.get("shortfall") or {}
        thr = float(pd.get("threshold_edge_pp") or sh.get(
            "edge_threshold_pp") or 0.5)
        head = ("ENTER (conditional on ordinary completion): p_pinnacle %.4f "
                "vs the paper book; best level edge %.2f pp >= %.2f pp at "
                "every level used; modelled profit after fees $%.2f IF the "
                "game is ordinarily completed"
                % (float(d.get("p_pinnacle") or 0),
                   float(pd.get("gross_edge_pp") or 0), thr,
                   float(pd.get("net_expected_profit_usd") or 0))
                if d.get("verdict") == "ENTER" else
                "REFUSE %s: edge %s pp vs %s, conditional EV after fees %s, "
                "depth %s, Pinnacle age %s s (limit %s s), book age %s s" % (
                    d.get("refusal"), sh.get("edge_pp"), thr,
                    sh.get("ev_after_fees_usd"), sh.get("depth_within_limit"),
                    sh.get("pinnacle_age_s"), sh.get("pinnacle_limit_s"),
                    sh.get("book_age_s")))
        return ("%s. PINNACLE_COMPLETED_GAME_PAPER: conditional, experimental "
                "economics -- not risk-adjusted, not proven positive EV; "
                "postponement/abandonment/suspension terms are disclosed "
                "research risks with unmeasured frequency" % head)
    if strategy == "PINNACLE_ONLY_PAPER_BENCHMARK":
        sh = pd.get("shortfall") or {}
        head = ("ENTER: p_pinnacle %.4f vs the paper book; best level edge "
                "%.2f pp >= 5.00 pp at every level used; EV after fees $%.2f"
                % (float(d.get("p_pinnacle") or 0),
                   float(pd.get("gross_edge_pp") or 0),
                   float(pd.get("net_expected_profit_usd") or 0))
                if d.get("verdict") == "ENTER" else
                "REFUSE %s: edge %s pp vs 5.0, EV after fees %s, depth %s, "
                "Pinnacle age %s s (limit %s s), book age %s s" % (
                    d.get("refusal"), sh.get("edge_pp"),
                    sh.get("ev_after_fees_usd"), sh.get("depth_within_limit"),
                    sh.get("pinnacle_age_s"), sh.get("pinnacle_limit_s"),
                    sh.get("book_age_s")))
        return ("%s. PINNACLE_ONLY_PAPER_BENCHMARK: experimental execution, "
                "not evidence of qualified or proven profitability; book "
                "currency NOT_ESTABLISHED (P5)" % head)
    if d.get("refusal") == "STRATEGY_ENTRIES_DISABLED":
        return ("REFUSE STRATEGY_ENTRIES_DISABLED: the two-model policy "
                "admitted this entry but its paper entry switch is off (only "
                "the benchmark opens new paper entries); %s"
                % (pd.get("rationale") or ""))
    return str(pd.get("rationale") or (
        "%s%s" % (d.get("verdict"), "" if not d.get("refusal")
                  else " " + str(d.get("refusal")))))


def _with_explanation(d: dict) -> dict:
    pd = d.pop("policy_decision", None)
    if isinstance(pd, str):
        pd = L._j(pd)
    d["strategy"] = d.get("strategy") or TWO_MODEL
    d["explanation"] = explanation(d, pd)
    return d


async def benchmark_payload(conn, *, account_id: str = L.ACCOUNT_ID,
                            limit: int = 100, now: float | None = None,
                            strategy: str | None = None) -> dict:
    """THE PINNACLE_ONLY_PAPER_BENCHMARK: its decisions (refusals with their
    shortfalls), orders, fills and handoffs, every row labelled with the
    strategy and the disclosure that it is experimental execution, not
    evidence of qualified or proven profitability."""
    from .agents import paper_benchmark as _PB

    class PB:                                                   # noqa: N801
        """The selected policy's identity (strict by default)."""
        pol = _PB.policy_for(strategy) or _PB.STRICT_POLICY
        STRATEGY = pol["strategy"]
        DISCLOSURE = pol["disclosure"]
    out = _base(now)
    out["strategy"] = PB.STRATEGY
    out["policy_version"] = PB.pol["version"]
    out["disclosure"] = PB.DISCLOSURE
    out["book_currency"] = _PB.BOOK_CURRENCY
    out["enablement"] = await _PB.enablement(conn, PB.pol)
    out["policies"] = [{"strategy": p["strategy"], "version": p["version"]}
                       for p in _PB.POLICIES]
    if PB.pol["kind"] == "COMPLETED_GAME":
        out["economics_label"] = _PB.ECONOMICS_LABEL

    async def decisions():
        rows = await conn.fetch(
            "SELECT decision_id, session_id, decided_at, valuation_id, "
            "       us_market_slug, holding_side, intent, fixture, label, "
            "       verdict, refusal, refusals, p_internal, internal_model, "
            "       p_pinnacle, pinnacle, p_blended, book_obs_id, book, "
            "       proposed_qty, limit_price, economics, "
            "       qualification_gaps, policy_version, policy_decision, "
            "       alternatives, optimistic, simulator_version, strategy "
            "  FROM paper_decisions WHERE account_id=$1 AND strategy=$2 "
            " ORDER BY decided_at DESC LIMIT $3", account_id, PB.STRATEGY,
            limit)
        return [dict(_row(r), disclosure=PB.DISCLOSURE) for r in rows]

    async def orders():
        rows = await conn.fetch(
            "SELECT * FROM paper_orders WHERE account_id=$1 AND strategy=$2 "
            " ORDER BY created_at DESC LIMIT $3", account_id, PB.STRATEGY,
            limit)
        return [dict(L.order_view(r), disclosure=PB.DISCLOSURE)
                for r in rows]

    async def fills():
        rows = await conn.fetch(
            "SELECT f.* FROM paper_fills f JOIN paper_orders o "
            "    ON o.order_id = f.order_id WHERE f.account_id=$1 "
            "   AND o.strategy=$2 ORDER BY f.filled_at DESC LIMIT $3",
            account_id, PB.STRATEGY, limit)
        return [dict(_row(r), strategy=PB.STRATEGY) for r in rows]

    async def handoffs():
        rows = await conn.fetch(
            "SELECT * FROM paper_handoffs WHERE account_id=$1 AND strategy=$2"
            " ORDER BY created_at DESC LIMIT $3", account_id, PB.STRATEGY,
            limit)
        return [_row(r) for r in rows]

    out["decisions"] = await _section(
        decisions(), empty_why=("NO_BENCHMARK_DECISION_YET: the benchmark "
                                "runs only with PAPER_BENCHMARK=on, its "
                                "control row and the paper session enabled"))
    out["orders"] = await _section(orders(), empty_why="NO_BENCHMARK_ORDER")
    out["fills"] = await _section(fills(),
                                  empty_why="NO_SIMULATED_BENCHMARK_FILL")
    out["handoffs"] = await _section(handoffs(),
                                     empty_why="NO_BENCHMARK_HANDOFF")

    async def ledger():
        rows = await conn.fetch(
            "SELECT * FROM paper_ledger WHERE account_id=$1 AND group_id IN "
            " (SELECT group_id FROM paper_orders WHERE account_id=$1 "
            "   AND strategy=$2) ORDER BY seq DESC LIMIT $3", account_id,
            PB.STRATEGY, limit)
        return [dict(L.entry_view(r), strategy=PB.STRATEGY) for r in rows]
    out["ledger"] = await _section(
        ledger(), empty_why=("NO_BENCHMARK_LEDGER_ENTRY: the benchmark "
                             "shares the account's one cash ledger"))
    out["last_updated_at"] = await _last_updated(conn, account_id)
    return out
