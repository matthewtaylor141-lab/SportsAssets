"""THE PAPER EXPERIMENT, AS MANAGEMENT SEES IT -- FROM PERSISTED RECORDS ONLY.

One read (`experiment`) behind the Command Centre homepage and the agent
pages: the active session, database and feed freshness, the opportunities
evaluated, the refusal breakdown, the closest opportunities, Derek's standing
entry orders, Xavier's protection orders, every simulated fill, the open and
closed positions (per strategy, exploration labelled "Training / simulated
execution"), the reconciled account, each agent's latest activity, Audrey's
operational recommendations and the acquisition throughput.

CONNECTED IS NOT THE SAME AS FILLED. If this read answers, the database is
connected and every figure below is a record in it; "no filled positions"
is then a measured state with its causes (the funnel and the refusals), never
"disconnected". A failed read is reported by the caller as UNAVAILABLE with
its reason -- never as zeros.

Every section is {status: OK | EMPTY | UNAVAILABLE, why, data}. Read-only.
"""
from __future__ import annotations

import os
import time
from decimal import Decimal
from typing import Any

from . import bettor_paper_ledger as L

VERSION = "PAPER_EXPERIMENT_READ_V1"
CG = "PINNACLE_COMPLETED_GAME_PAPER"
MAKER = "PINNACLE_COMPLETED_GAME_MAKER_PAPER"
EXPLORE = "PINNACLE_EXPLORATION_PAPER"
EXPERIMENT_ID = "EXT_PINNACLE_DEVIG_V1_SHADOW"
STRATEGY_TITLES = {
    "DEREK_ENTRY_POLICY_V2": "Derek two-model research (entries off)",
    "PINNACLE_ONLY_PAPER_BENCHMARK": "Strict benchmark (entries off)",
    CG: "Investment policy (0.5 pp, taker)",
    MAKER: "Investment policy, resting bids (maker entry)",
    EXPLORE: "Training / simulated execution (exploration)"}
#: freshness limits for the verdicts shown beside each stamp
STALE_AFTER_S = {"valuation": 1200.0, "decision": 1200.0, "pass": 300.0,
                 "collector": 1200.0, "book": 900.0, "ledger": None}


def _f(v):
    if isinstance(v, Decimal):
        return float(v)
    return v


def _row(r) -> dict:
    out = {}
    for k, v in dict(r).items():
        if isinstance(v, Decimal):
            out[k] = float(v)
        elif hasattr(v, "timestamp"):
            out[k] = float(v.timestamp())
        elif k in ("label", "queue_basis", "detail", "evidence", "baseline",
                   "economics", "policy_decision"):
            out[k] = L._j(v)
        else:
            out[k] = v
    return out


async def _sec(coro, *, empty_why: str, is_empty=None) -> dict:
    try:
        data = await coro
    except Exception as exc:                                    # noqa: BLE001
        return {"status": "UNAVAILABLE",
                "why": "%s: %s" % (type(exc).__name__, str(exc)[:160]),
                "data": None}
    empty = is_empty(data) if is_empty is not None else not data
    return {"status": "EMPTY" if empty else "OK",
            "why": empty_why if empty else None, "data": data}


def _age(at, now):
    return None if at is None else round(float(now) - float(at), 1)


async def _epoch(conn, sql, *args):
    v = await conn.fetchval(sql, *args)
    return None if v is None else float(v)


# ═════════════════════════════════════════════════════════════════════
# SECTIONS
# ═════════════════════════════════════════════════════════════════════

async def session(conn, now: float, acct: str = L.ACCOUNT_ID) -> dict:
    s = await conn.fetchrow(
        "SELECT session_id, account_id, status, "
        " extract(epoch FROM started_at) AS started_at, config_sha, "
        " simulator_version, config->>'config_version' AS config_version "
        " FROM paper_sessions WHERE status='ACTIVE' AND account_id=$1 "
        " ORDER BY started_at DESC LIMIT 1", acct)
    ctl = {r["control_key"]: {"enabled": r["enabled"], "why": r["why"]}
           for r in await conn.fetch(
               "SELECT control_key, enabled, why FROM paper_control")}
    hb = L._j(await conn.fetchval(
        "SELECT value FROM ingestion_state WHERE key="
        "'paper_session_last_pass'")) or {}
    cyc = L._j(await conn.fetchval(
        "SELECT value FROM ingestion_state WHERE key="
        "'ext_pinnacle_last_cycle'")) or {}
    return {
        "session": None if s is None else _row(s),
        "controls": {k: ctl.get(k) for k in (
            "PAPER_SESSION", CG, MAKER, EXPLORE,
            "PINNACLE_ONLY_PAPER_BENCHMARK",
            "PAPER_ENTRIES:DEREK_ENTRY_POLICY_V2") if k in ctl},
        "paper_pass": {"at": hb.get("written_at") or hb.get("at"),
                       "age_s": _age(hb.get("written_at") or hb.get("at"),
                                     now),
                       "ran": hb.get("ran"), "refusal": hb.get("refusal"),
                       "errors": hb.get("errors"),
                       "elapsed_s": hb.get("elapsed_s"),
                       "trigger": hb.get("trigger")},
        "collector": {"at": cyc.get("at"), "age_s": _age(cyc.get("at"), now),
                      "state": cyc.get("state"),
                      "label": cyc.get("cycle_label"),
                      "elapsed_s": cyc.get("elapsed_s"),
                      "build": (cyc.get("writer") or {}).get("build"),
                      "refusals": cyc.get("refusals") or {}},
        "real_money": "DISABLED"}


async def freshness(conn, now: float) -> dict:
    stamps = {
        "valuation": await _epoch(
            conn, "SELECT extract(epoch FROM max(decided_at)) FROM "
                  "external_valuations WHERE experiment_id=$1",
            EXPERIMENT_ID),
        "decision": await _epoch(
            conn, "SELECT extract(epoch FROM max(decided_at)) FROM "
                  "paper_decisions"),
        "book": await _epoch(
            conn, "SELECT extract(epoch FROM max(observed_at)) FROM "
                  "paper_book_observations"),
        "ledger": await _epoch(
            conn, "SELECT extract(epoch FROM max(committed_at)) FROM "
                  "paper_ledger"),
        "pass": (L._j(await conn.fetchval(
            "SELECT value FROM ingestion_state WHERE key="
            "'paper_session_last_pass'")) or {}).get("written_at"),
        "collector": (L._j(await conn.fetchval(
            "SELECT value FROM ingestion_state WHERE key="
            "'ext_pinnacle_last_cycle'")) or {}).get("at")}
    out = {}
    for k, at in stamps.items():
        lim = STALE_AFTER_S.get(k)
        age = _age(at, now)
        out[k] = {"at": at, "age_s": age, "stale_after_s": lim,
                  "verdict": ("NONE_YET" if at is None else
                              "NO_LIMIT" if lim is None else
                              "CURRENT" if age <= lim else "STALE")}
    return {"database": "CONNECTED", "read_at": now, "stamps": out,
            "note": ("this read answered from the database; an unchanged "
                     "ledger stamp means no money moved, not a lost "
                     "connection")}


async def opportunities(conn, now: float) -> dict:
    v = await conn.fetchrow(
        "SELECT count(*) AS valuations, count(DISTINCT coalesce("
        " condition_id, event_key, us_market_slug)) AS fixtures, "
        " count(DISTINCT sport_family) AS sports FROM external_valuations "
        " WHERE experiment_id=$1 AND us_market_slug IS NOT NULL "
        "   AND decided_at > now() - interval '24 hours'", EXPERIMENT_ID)
    d = await conn.fetch(
        "SELECT strategy, verdict, count(*) AS n FROM paper_decisions "
        " WHERE decided_at > now() - interval '24 hours' GROUP BY 1, 2")
    by = {}
    for r in d:
        s = by.setdefault(r["strategy"], {"title": STRATEGY_TITLES.get(
            r["strategy"], r["strategy"]), "decisions": 0, "enter": 0})
        s["decisions"] += int(r["n"])
        if r["verdict"] == "ENTER":
            s["enter"] += int(r["n"])
    att = {}
    try:
        for r in await conn.fetch(
                "SELECT outcome, coalesce(book_source, 'NO_BOOK_READ') AS "
                " src, count(*) AS n FROM paper_evaluation_attempts WHERE "
                " at > now() - interval '6 hours' GROUP BY 1, 2"):
            att.setdefault(r["outcome"], {})[r["src"]] = int(r["n"])
    except Exception:                                           # noqa: BLE001
        att = None
    return {"window": "24 h", "valuations": int(v["valuations"] or 0),
            "fixtures": int(v["fixtures"] or 0),
            "sports": int(v["sports"] or 0), "by_strategy": by,
            "attempts_6h": att}


async def refusals(conn, now: float) -> dict:
    words = {}
    try:
        from . import bettor_paper_ops as OPS
        words = OPS.REFUSAL_WORDS
    except Exception:                                           # noqa: BLE001
        pass
    out = {}
    for r in await conn.fetch(
            "SELECT strategy, coalesce(refusal, 'ENTER') AS reason, "
            " count(*) AS n FROM paper_decisions WHERE decided_at > now() - "
            " interval '24 hours' GROUP BY 1, 2 ORDER BY 3 DESC"):
        out.setdefault(r["strategy"], []).append(
            {"reason": r["reason"], "n": int(r["n"]),
             "words": words.get(r["reason"])})
    return out


async def closest(conn, now: float, acct: str = L.ACCOUNT_ID) -> list:
    """The closest investment-policy evaluations of the last 24 h: the
    LATEST decision per market and recorded policy version (not one row per
    cycle), decisions under the SERVING version first, then historical
    versions, each ranked by gross edge. Every row carries its recorded
    policy_version, whether that is the serving version, and its age -- a
    historical refusal describes the rules in force when it was made."""
    # the serving version as the operations read names it (the policy
    # module's own constant, read through bettor_paper_ops -- this read
    # model never imports the benchmark module)
    from . import bettor_paper_ops as OPS
    serving = OPS._policy_meta()[OPS.COMPLETED_GAME]["version"]
    rows = await conn.fetch(
        "SELECT * FROM (SELECT DISTINCT ON (us_market_slug, policy_version) "
        "       decision_id, strategy, policy_version, "
        "       extract(epoch FROM decided_at) AS at, "
        "       us_market_slug, fixture, p_pinnacle, refusal, verdict, "
        "       (economics->>'best_level_edge_pp')::float8 AS gross_pp, "
        "       (economics->'fee_stop'->>'fee_per_contract_usd')::float8 AS "
        "         fee_pc, (economics->'fee_stop'->>'net_edge_pp')::float8 AS "
        "         net_pp, (economics->'levels'->0->>'price')::float8 AS price,"
        "       (economics->>'threshold_edge_pp')::float8 AS threshold_pp, "
        "       label->>'participant' AS participant "
        "  FROM paper_decisions WHERE strategy=$1 AND account_id=$3 "
        "   AND decided_at > now() - interval '24 hours' "
        "   AND economics->>'best_level_edge_pp' IS NOT NULL "
        " ORDER BY us_market_slug, policy_version, decided_at DESC) x "
        " ORDER BY (policy_version = $2) DESC, gross_pp DESC LIMIT 8",
        CG, serving, acct)
    out = []
    for r in rows:
        d = _row(r)
        d["serving_version"] = serving
        d["historical"] = d.get("policy_version") != serving
        d["age_s"] = (None if d.get("at") is None
                      else round(max(0.0, float(now) - float(d["at"])), 1))
        out.append(d)
    return out


async def standing_orders(conn, now: float,
                          acct: str = L.ACCOUNT_ID) -> dict:
    rows = await conn.fetch(
        "SELECT order_id, strategy, role, us_market_slug, fixture, "
        "       holding_side, order_type, time_in_force, qty, filled_qty, "
        "       limit_price, reserved_remaining_usd, state, "
        "       extract(epoch FROM created_at) AS created_at, "
        "       extract(epoch FROM expires_at) AS expires_at, "
        "       queue_ahead_qty, decision_id, label "
        "  FROM paper_orders WHERE account_id=$1 AND state = ANY($2::text[]) "
        " ORDER BY created_at DESC LIMIT 100", acct,
        list(L.OPEN_STATES))
    entries, management = [], []
    for r in rows:
        o = _row(r)
        lb = o.pop("label") or {}
        o.update(participant=lb.get("participant"),
                 rationale=lb.get("rationale"),
                 cancel_conditions=lb.get("cancel_conditions"),
                 position_label=lb.get("position_label"),
                 training=bool(lb.get("training")))
        (entries if o["role"] == "ENTRY" else management).append(o)
    return {"entry_orders": entries, "management_orders": management,
            "an_order_is_not_a_fill": True}


async def fills(conn, now: float, acct: str = L.ACCOUNT_ID) -> list:
    rows = await conn.fetch(
        "SELECT f.fill_id, f.strategy, f.role, f.direction, f.us_market_slug,"
        "       f.holding_side, f.qty, f.price, f.fee_usd, f.gross_usd, "
        "       f.basis, extract(epoch FROM f.filled_at) AS filled_at, "
        "       f.label->>'participant' AS participant, l.seq AS ledger_seq,"
        "       l.cash_delta_usd, l.cash_after_usd "
        "  FROM paper_fills f LEFT JOIN paper_ledger l ON l.fill_id = "
        "       f.fill_id WHERE f.account_id=$1 "
        " ORDER BY f.filled_at DESC LIMIT 25", acct)
    return [_row(r) for r in rows]


async def positions_and_account(conn, now: float,
                                acct: str = L.ACCOUNT_ID) -> dict:
    b = await L.balances(conn, acct, now=now)
    allpos = await L.positions(conn, acct, include_closed=True)
    marks = {p.get("position_key"): p for p in (b.get("open_positions")
                                                 or [])}
    by_strat: dict[str, Any] = {}
    open_rows, closed_rows = [], []
    for p in allpos:
        s = p.get("strategy")
        a = by_strat.setdefault(s, {"title": STRATEGY_TITLES.get(s, s),
                                    "positions": 0, "open": 0,
                                    "realized_pnl_usd": 0.0,
                                    "unrealized_pnl_usd": 0.0,
                                    "fees_usd": 0.0,
                                    "open_cost_basis_usd": 0.0})
        a["positions"] += 1
        a["realized_pnl_usd"] = round(a["realized_pnl_usd"]
                                      + float(p["realized_pnl_usd"]), 6)
        a["fees_usd"] = round(a["fees_usd"] + float(p["buy_fees_usd"])
                              + float(p["sale_fees_usd"]), 6)
        m = marks.get(p["position_key"]) or {}
        view = {"position_key": p["position_key"], "group_id":
                p["group_id"], "strategy": s,
                "label": ("Training / simulated execution"
                          if s == EXPLORE else STRATEGY_TITLES.get(s, s)),
                "market": p["us_market_slug"],
                "participant": (p.get("label") or {}).get("participant"),
                "side": p["holding_side"], "open_qty": p["open_qty"],
                "avg_cost_incl_fees": p["avg_cost_per_contract_incl_fees"],
                "cost_basis_usd": p["cost_basis_usd"],
                "realized_pnl_usd": p["realized_pnl_usd"],
                "settlement": p.get("settlement"),
                "mark": m.get("mark"), "marked_value_usd":
                    m.get("marked_value_usd"),
                "unrealized_pnl_usd": m.get("unrealized_pnl_usd"),
                "first_fill_at": p["first_fill_at"]}
        if p["open_qty"] > 1e-9:
            a["open"] += 1
            a["open_cost_basis_usd"] = round(
                a["open_cost_basis_usd"] + float(p["cost_basis_usd"]), 6)
            a["unrealized_pnl_usd"] = round(
                a["unrealized_pnl_usd"] + float(
                    m.get("unrealized_pnl_usd") or 0.0), 6)
            open_rows.append(view)
        else:
            closed_rows.append(view)
    explore = None
    try:
        from .agents import paper_explore as PEX
        explore = await PEX.limits_state(conn, acct)
    except Exception as exc:                                    # noqa: BLE001
        explore = {"error": type(exc).__name__}
    keys = ("cash_usd", "reserved_usd", "available_usd", "total_equity_usd",
            "open_position_value_usd", "realized_pnl_usd",
            "unrealized_pnl_usd", "fees_paid_usd", "ledger_consistent",
            "marks_complete", "starting_cash_usd")
    return {"account": {k: _f(b.get(k)) for k in keys if k in b},
            "by_strategy": by_strat, "open_positions": open_rows,
            "closed_positions": closed_rows[:25],
            "exploration_limits": explore,
            "attribution": ("each strategy's P&L from its own fills and "
                            "settlements on the ONE ledger; the strategies "
                            "sum to the account totals")}


async def agents(conn, now: float) -> dict:
    d = await conn.fetchrow(
        "SELECT decision_id, strategy, verdict, refusal, us_market_slug, "
        " extract(epoch FROM decided_at) AS at FROM paper_decisions "
        " ORDER BY decided_at DESC LIMIT 1")
    x = await conn.fetchrow(
        "SELECT count(*) AS reviews, extract(epoch FROM max(reviewed_at)) "
        " AS last_at FROM paper_xavier_reviews WHERE reviewed_at > now() - "
        " interval '24 hours'")
    h = await conn.fetchval("SELECT count(*) FROM paper_handoffs")
    f = await conn.fetch(
        "SELECT finding_id, kind, severity, subject, "
        " extract(epoch FROM found_at) AS found_at FROM paper_audrey_findings"
        " ORDER BY found_at DESC LIMIT 8")
    recs = []
    try:
        for r in await conn.fetch(
                "SELECT recommendation_id, owner_agent, category, kind, "
                " metric, baseline, recommendation, status, "
                " extract(epoch FROM created_at) AS created_at FROM "
                " paper_recommendations ORDER BY created_at DESC LIMIT 12"):
            rec = _row(r)
            rec["events"] = [_row(e) for e in await conn.fetch(
                "SELECT actor, kind, body, extract(epoch FROM at) AS at "
                "  FROM paper_recommendation_events WHERE "
                "  recommendation_id=$1 ORDER BY event_id DESC LIMIT 4",
                r["recommendation_id"])]
            # A TEMPLATE IS NOT A RESPONSE: genuine agent reviews and the
            # automated acknowledgement are counted apart.
            cnt = await conn.fetchrow(
                "SELECT count(*) FILTER (WHERE kind='RESPONSE') AS resp, "
                "       count(*) FILTER (WHERE kind="
                "         'AUTOMATED_ACKNOWLEDGEMENT') AS ack, "
                "       count(*) FILTER (WHERE kind='MEASUREMENT') AS meas "
                "  FROM paper_recommendation_events "
                " WHERE recommendation_id=$1", r["recommendation_id"])
            rec["agent_responses"] = int(cnt["resp"] or 0)
            rec["automated_acknowledgements"] = int(cnt["ack"] or 0)
            rec["measurements"] = int(cnt["meas"] or 0)
            recs.append(rec)
    except Exception:                                           # noqa: BLE001
        recs = None
    return {"derek": {"latest_decision": None if d is None else _row(d)},
            "xavier": {"reviews_24h": int(x["reviews"] or 0),
                       "last_review_at": _f(x["last_at"]),
                       "handoffs": int(h or 0)},
            "audrey": {"latest_findings": [_row(r) for r in f],
                       "recommendations": recs,
                       "category_rule": ("OPERATIONAL recommendations are "
                                         "never reported as profitable "
                                         "learning")}}


async def throughput(conn, now: float) -> dict:
    rows = await conn.fetch(
        "SELECT date_trunc('hour', observed_at) AS hour, "
        " count(*) AS reads, "
        " count(*) FILTER (WHERE source LIKE '%:SHARED_READ') AS shared, "
        " count(*) FILTER (WHERE error IS NOT NULL) AS failed "
        "  FROM paper_book_observations WHERE observed_at > now() - "
        "  interval '6 hours' GROUP BY 1 ORDER BY 1 DESC")
    cuts = {r["hour"]: int(r["n"]) for r in await conn.fetch(
        "SELECT date_trunc('hour', decided_at) AS hour, count(*) AS n FROM "
        " paper_decisions WHERE refusal = 'BOOK_READ_DID_NOT_FINISH_INSIDE_"
        "THE_DECISION_DEADLINE' AND decided_at > now() - interval '6 hours' "
        " GROUP BY 1")}
    return {"hours": [{"hour": float(r["hour"].timestamp()),
                       "book_reads": int(r["reads"]),
                       "shared_reads": int(r["shared"]),
                       "failed_reads": int(r["failed"]),
                       "decisions_cut_by_deadline": cuts.get(r["hour"], 0)}
                      for r in rows]}


# ═════════════════════════════════════════════════════════════════════
# THE READ
# ═════════════════════════════════════════════════════════════════════

async def experiment(conn, *, now: float | None = None,
                     account_id: str = L.ACCOUNT_ID) -> dict:
    at = float(now if now is not None else time.time())
    out: dict[str, Any] = {
        "version": VERSION, "as_of": at, "paper_only": True,
        "data_label": L.DATA_LABEL,
        "serving_build": os.environ.get("RENDER_GIT_COMMIT"),
        "real_money": "DISABLED", "account_id": account_id}
    out["session"] = await _sec(session(conn, at, account_id),
                              empty_why="no session")
    out["freshness"] = await _sec(freshness(conn, at), empty_why="none")
    out["opportunities"] = await _sec(
        opportunities(conn, at), empty_why="no valuations in 24 h",
        is_empty=lambda d: not d.get("valuations"))
    out["refusals"] = await _sec(refusals(conn, at),
                                 empty_why="no decisions in 24 h")
    out["closest"] = await _sec(closest(conn, at, account_id),
                                empty_why="no priced decisions in 24 h")
    out["orders"] = await _sec(
        standing_orders(conn, at, account_id), empty_why="no open orders",
        is_empty=lambda d: not d["entry_orders"] and not d[
            "management_orders"])
    out["fills"] = await _sec(fills(conn, at, account_id),
                              empty_why="no simulated fills yet")
    out["positions"] = await _sec(positions_and_account(conn, at, account_id),
                                  empty_why="no positions")
    out["agents"] = await _sec(agents(conn, at), empty_why="none")
    out["throughput"] = await _sec(
        throughput(conn, at), empty_why="no book reads in 6 h",
        is_empty=lambda d: not d["hours"])
    # both teams of each record's event, logos only where verified by venue
    # team id + league (team_logos); a read decoration, never a decision input
    from . import team_logos as TL
    od = out["orders"].get("data") or {}
    await TL.decorate(conn, (out["closest"].get("data") or [])
                      + (od.get("entry_orders") or [])
                      + (od.get("management_orders") or [])
                      + (out["fills"].get("data") or []))
    await TL.decorate(conn, (out["positions"].get("data") or {}).get(
        "open_positions") or [], slug_key="market")
    pos = (out["positions"].get("data") or {})
    n_open = len(pos.get("open_positions") or [])
    n_fills = len(out["fills"].get("data") or [])
    n_orders = len(((out["orders"].get("data") or {}).get("entry_orders")
                    or []))
    out["state"] = {
        "connected": True,
        "filled_positions_open": n_open,
        "fills_recorded": n_fills,
        "open_entry_orders": n_orders,
        "headline": (
            "%d open filled position%s" % (n_open, "" if n_open == 1
                                           else "s")
            if n_open else
            "Connected — no filled positions yet%s" % (
                " (%d open entry order%s waiting)" % (
                    n_orders, "" if n_orders == 1 else "s")
                if n_orders else ""))}
    return out
