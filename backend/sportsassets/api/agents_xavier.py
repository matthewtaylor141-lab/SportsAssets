"""XAVIER'S WORKSPACE: `GET /api/command/agents/xavier` (+ one decision).

The three-agent workspace contract: `{"agent", "read_at", "read_only",
"sections": {name: {"status": OK|EMPTY|UNAVAILABLE, "why", "data",
"evidence"}}}`. Sections: status, versions, positions, reviews, ladder,
alternatives, payout_tables, execution, recovery, performance,
servicing_cadence.

EVERYTHING HERE IS A READ of an authoritative record: the funded book
(`bettor_funded_intents` / `_fills` / `_economics`), Xavier's decision rows
(`bettor_xavier_decisions`, written by the scheduled servicing pass before
anything is dispatched) and their execution events, the handoff table when
core's migration is present, and the servicing heartbeat. No economics are
recomputed; no mutating statement, venue client or order path is imported.

TRUTHFUL EMPTY STATES. With no funded position -- the state today -- a
section is EMPTY and names why (no account bound, no fill, the table
missing); an empty table is never shown as success. A read that fails is
UNAVAILABLE with the exception's type, never a page of zeros.
"""
from __future__ import annotations

import json
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from ..db import get_pool

router = APIRouter()

VERSION = "AGENTS_XAVIER_WORKSPACE_V1"
OK, EMPTY, UNAVAILABLE = "OK", "EMPTY", "UNAVAILABLE"
DECISION_LIMIT = 50
SERVICING_KEY = "ext_pinnacle_last_servicing"
SECTIONS = ("status", "versions", "positions", "reviews", "ladder",
            "alternatives", "payout_tables", "execution", "recovery",
            "performance", "servicing_cadence")


async def require_read(request: Request) -> str:
    from . import app as A
    return A.require_command(
        bt_command=request.cookies.get("bt_command", ""),
        x_desk_token=request.headers.get("x-desk-token", ""),
        x_admin_token=request.headers.get("x-admin-token", ""))


def _sec(status, why=None, data=None, evidence=None) -> dict:
    return {"status": status, "why": why, "data": data,
            "evidence": list(evidence or [])}


def _href(xid: str) -> str:
    return "/api/command/agents/xavier/decisions/%s" % xid


def _ev(xid: str) -> dict:
    return {"kind": "bettor_xavier_decisions", "id": xid, "href": _href(xid)}


def _epoch(v):
    if v is None:
        return None
    if hasattr(v, "timestamp"):
        return v.timestamp()
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


async def _regclass(conn, name: str) -> bool:
    return await conn.fetchval("SELECT to_regclass($1)", name) is not None


async def _bound(conn) -> dict:
    from .. import bettor_funded_activation as FA
    b = FA._obj(await FA._state(conn, FA.ACCOUNT_KEY)) or {}
    acct = str(b.get("account_id") or "").strip()
    return {"account_id": acct or None, "venue": b.get("venue"),
            "bound": bool(acct and b.get("venue"))}


async def _guard(name, fn, sections):
    try:
        sections[name] = await fn()
    except Exception as exc:                                    # noqa: BLE001
        sections[name] = _sec(UNAVAILABLE, type(exc).__name__)


async def workspace(conn, *, now: float | None = None) -> dict:
    """THE WORKSPACE. Each section is read on its own; one failing section
    is UNAVAILABLE and the others still answer."""
    from .. import bettor_xavier as XV

    at = float(now if now is not None else time.time())
    sections: dict[str, Any] = {}
    ctx: dict[str, Any] = {}

    async def _load():
        # THE BOUND ACCOUNT'S RECORDS ONLY: Xavier owns the positions of the
        # funded account the servicing pass manages. Records of an account
        # not bound now are counted and named, never shown as its work.
        ctx["bound"] = await _bound(conn)
        b = ctx["bound"]
        if b["bound"]:
            got = await XV.latest_decisions(conn, account_id=b["account_id"],
                                            venue=b["venue"],
                                            limit=DECISION_LIMIT)
        else:
            got = {"ok": await XV.has_schema(conn), "positions": [],
                   "refusal": None}
            if got["ok"]:
                ctx["unbound_records"] = int(await conn.fetchval(
                    "SELECT count(*) FROM bettor_xavier_decisions") or 0)
            else:
                got["refusal"] = XV.R_SCHEMA
        ctx["decisions_ok"] = bool(got.get("ok"))
        ctx["decisions_refusal"] = got.get("refusal")
        ctx["decisions"] = list(got.get("positions") or [])
        ctx["resp"] = (await XV.responsibilities(
            conn, account_id=b["account_id"], venue=b["venue"], now=at)
            if b["bound"] else None)
    try:
        await _load()
        load_err = None
    except Exception as exc:                                    # noqa: BLE001
        load_err = type(exc).__name__
    ctx.setdefault("decisions", [])
    ctx.setdefault("bound", {"bound": False, "account_id": None,
                             "venue": None})

    async def status():
        hb = await _heartbeat(conn)
        row = None
        if await _regclass(conn, "agent_status"):
            r = await conn.fetchrow(
                "SELECT * FROM agent_status WHERE agent_id='XAVIER'")
            row = None if r is None else {k: (v if not hasattr(v, "timestamp")
                                              else v.timestamp())
                                          for k, v in dict(r).items()}
        resp = ctx.get("resp") or {}
        data = {"identity": XV.describe(), "agent_status_row": row,
                "agent_status_row_why": (None if row is not None else
                                         "agent_status is absent or holds "
                                         "no XAVIER row (core migration "
                                         "152)"),
                "account": ctx["bound"],
                "responsibility": ({"positions": len(resp.get(
                    "positions") or []), "reconciled": resp.get(
                    "reconciled"), "ok": resp.get("ok"),
                    "refusal": resp.get("refusal")} if resp else None),
                "last_servicing_state": (hb or {}).get("state"),
                "last_servicing_at": (hb or {}).get("at")}
        return _sec(OK, None, data)

    async def versions():
        from .. import bettor_funded_decision as FD
        from ..agents import xavier_ladder as XL
        from ..agents import xavier_policy as XP
        pol = await XP.load(conn)
        return _sec(OK, None, {
            "xavier_record": XV.VERSION, "ladder": XL.VERSION,
            "decision_policy": FD.VERSION,
            "management_policy": {k: pol.get(k) for k in (
                "policy_key", "version", "source", "why", "params",
                "approved_by")},
            "workspace": VERSION})

    async def positions():
        if load_err:
            return _sec(UNAVAILABLE, load_err)
        b = ctx["bound"]
        if not b["bound"]:
            return _sec(EMPTY, "NO_FUNDED_ACCOUNT_IS_BOUND: the scheduled "
                        "servicing pass returns before it manages anything, "
                        "so Xavier owns no position")
        resp = ctx.get("resp") or {}
        if not resp.get("ok"):
            return _sec(UNAVAILABLE, resp.get("refusal") or "UNREADABLE")
        if not resp.get("positions"):
            return _sec(EMPTY, "NO_FUNDED_POSITION_HAS_RECEIVED_A_FILL_OR_"
                        "ALL_ARE_RECONCILED (%d reconciled)"
                        % int(resp.get("reconciled") or 0))
        groups: dict[str, dict] = {}
        for p in resp["positions"]:
            key = XV.group_key(p)
            g = groups.setdefault(key, {"group": key, "legs": [],
                                        "primary_qty": 0.0,
                                        "hedge_qty": 0.0})
            g["legs"].append({k: p.get(k) for k in (
                "intent_id", "leg_role", "us_market_slug", "state",
                "filled_qty", "residual_qty", "obligations")})
            role = str(p.get("leg_role") or "PRIMARY")
            g["hedge_qty" if role == "HEDGE" else "primary_qty"] += float(
                p.get("residual_qty") or 0.0)
        from . import command_xavier as CX
        for g in groups.values():
            g["matched_qty"] = min(g["primary_qty"], g["hedge_qty"])
            g["residual_unpaired_qty"] = abs(g["primary_qty"]
                                             - g["hedge_qty"])
            first = g["legs"][0]
            try:
                g["book"] = await CX.book_for(
                    conn, intent_id=first["intent_id"],
                    group_id=(None if g["group"].startswith("intent:")
                              else g["group"]))
            except Exception as exc:                            # noqa: BLE001
                g["book"] = {"unreadable": type(exc).__name__}
        handoffs = None
        if await _regclass(conn, "agent_position_handoffs"):
            ids = [l_["intent_id"] for g in groups.values()
                   for l_ in g["legs"]]
            handoffs = [dict(r) for r in await conn.fetch(
                "SELECT * FROM agent_position_handoffs "
                " WHERE entry_intent_id = ANY($1::text[])", ids)]
        try:
            exp = await _exposure(conn, b)
        except Exception as exc:                                # noqa: BLE001
            exp = {"unreadable": type(exc).__name__}
        return _sec(OK, None, {
            "groups": list(groups.values()), "current_exposure": exp,
            "handoffs": handoffs,
            "handoffs_why": (None if handoffs is not None else
                             "agent_position_handoffs is absent (core "
                             "migration 152); the funded book is the "
                             "record")},
            [{"kind": "bettor_funded_intents", "id": l_["intent_id"],
              "href": "/api/command/xavier/%s" % l_["intent_id"]}
             for g in groups.values() for l_ in g["legs"]])

    def _no_decisions_why():
        if not ctx.get("decisions_ok", True):
            return ctx.get("decisions_refusal")
        if not ctx["bound"]["bound"]:
            n = int(ctx.get("unbound_records") or 0)
            return ("NO_FUNDED_ACCOUNT_IS_BOUND: no funded position exists, "
                    "so no Xavier review is this workspace's"
                    + ("" if not n else
                       " (%d Xavier record(s) exist for accounts not bound "
                       "now; they are not shown as this account's work)" % n))
        return ("NO_XAVIER_REVIEW_IS_RECORDED: no funded position has "
                "received a fill")

    async def reviews():
        if load_err:
            return _sec(UNAVAILABLE, load_err)
        ds = ctx["decisions"]
        if not ds:
            return _sec(EMPTY, _no_decisions_why())
        rows = [{"xavier_decision_id": d["xavier_decision_id"],
                 "intent_id": d.get("intent_id"),
                 "portfolio_group_id": d.get("portfolio_group_id"),
                 "decided_at": _epoch(d.get("decided_at")),
                 "responsibility_state": d.get("responsibility_state"),
                 "chosen_action": d.get("chosen_action"),
                 "execution_eligibility": d.get("execution_eligibility"),
                 "next_review_at": _epoch(d.get("next_review_at")),
                 "policy": ((d.get("reasoning") or {}).get(
                     "xavier_ladder") or {}).get("policy"),
                 "decision_policy": (d.get("reasoning") or {}).get(
                     "decision_policy"),
                 "shadow_comparison": (d.get("reasoning") or {}).get(
                     "shadow_comparison"),
                 "selection_scope": (d.get("reasoning") or {}).get(
                     "selection_scope"),
                 "search_completeness": _search(d),
                 "evidence": [_ev(d["xavier_decision_id"])]}
                for d in ds]
        nxt = [r["next_review_at"] for r in rows
               if r["next_review_at"] is not None]
        return _sec(OK, None, {"current": rows,
                               "next_review_at": min(nxt) if nxt else None},
                    [_ev(d["xavier_decision_id"]) for d in ds])

    def _per_decision(pick, name):
        async def _f():
            if load_err:
                return _sec(UNAVAILABLE, load_err)
            ds = ctx["decisions"]
            if not ds:
                return _sec(EMPTY, _no_decisions_why())
            out = []
            for d in ds:
                v = pick(d)
                if v:
                    out.append({"xavier_decision_id": d["xavier_decision_id"],
                                "intent_id": d.get("intent_id"), name: v,
                                "search_completeness": _search(d)})
            if not out:
                return _sec(EMPTY, "THE_RECORDED_REVIEWS_CARRY_NO_%s "
                            "(written before the ladder existed, or no "
                            "alternative was valued)" % name.upper())
            return _sec(OK, None, out,
                        [_ev(d["xavier_decision_id"]) for d in ds])
        return _f

    def _search(d):
        sc = dict(((d.get("reasoning") or {}).get("xavier_ladder") or {})
                  .get("search_completeness") or {})
        if not sc:
            return None
        return {k: sc.get(k) for k in (
            "complete", "stop_reason", "comparison_scope", "discovered",
            "examined", "excluded", "unexamined", "budget_statement",
            "every_admitted_reached_the_comparison")}

    def _ladder(d):
        return ((d.get("reasoning") or {}).get("xavier_ladder") or {}).get(
            "ladder")

    def _alts(d):
        keep = ("action", "candidate_id", "leg_role", "rankable", "blocker",
                "alternative_class", "expected_net_usd",
                "increment_vs_hold_usd", "worst_case_net_usd",
                "worst_case_established_usd", "p_net_profit",
                "p_both_legs_win", "capital_required_usd",
                "capital_released_usd", "fees_usd", "unpaired_residual_qty",
                "exposure_after", "sensitivity", "eligibility",
                "evidence_age_and_expiry", "qty_executable_at_limit")
        return [{k: a.get(k) for k in keep if k in a}
                for a in d.get("alternatives") or []]

    def _tables(d):
        return [{"action": a.get("action"),
                 "candidate_id": a.get("candidate_id"),
                 "payout_table": a.get("payout_table")}
                for a in d.get("alternatives") or [] if a.get("payout_table")]

    async def execution():
        from . import command_xavier as CX
        if load_err:
            return _sec(UNAVAILABLE, load_err)
        ds = ctx["decisions"]
        if not ds:
            return _sec(EMPTY, _no_decisions_why(),
                        {"submission_switches": CX._switches()})
        return _sec(OK, None, {
            "submission_switches": CX._switches(),
            "decisions": [{"xavier_decision_id": d["xavier_decision_id"],
                           "chosen_action": d.get("chosen_action"),
                           "chosen_plan_digest": d.get("chosen_plan_digest"),
                           "execution_eligibility": d.get(
                               "execution_eligibility"),
                           "execution": d.get("execution")} for d in ds]},
            [_ev(d["xavier_decision_id"]) for d in ds])

    async def recovery():
        unresolved = await XV.unresolved_claims(conn)
        inv = None
        if await _regclass(conn, "bettor_funded_investigations"):
            inv = [dict(r) for r in await conn.fetch(
                "SELECT intent_id, state, count(*) OVER () AS n "
                "  FROM bettor_funded_investigations WHERE state='OPEN' "
                " ORDER BY intent_id LIMIT 50")]
        data = {"scope": ("EVERY_ACCOUNT: an unanswered send is exposure "
                          "whichever account it belongs to, so none is "
                          "hidden here (each row names its account)"),
                "unresolved_dispatch_claims": unresolved,
                "open_investigations": inv}
        if not unresolved and not inv:
            return _sec(EMPTY, "NOTHING_TO_RECOVER: no dispatch claim is "
                        "unanswered and no investigation is open", data)
        return _sec(OK, None, data,
                    [_ev(u["xavier_decision_id"]) for u in unresolved])

    async def performance():
        from .. import bettor_xavier_review as XR
        from . import command_xavier as CX
        resp = ctx.get("resp") or {}
        owned = bool(resp.get("positions"))
        review = await XR.latest_review(conn)
        if not owned and not ctx["decisions"]:
            return _sec(EMPTY, "NO_OWNED_INVENTORY: management contribution "
                        "is measured only against positions Xavier managed, "
                        "and there are none")
        dig = CX.review_digest(review)
        if not dig.get("available"):
            return _sec(EMPTY, "NO_SETTLED_MANAGED_POSITION_HAS_BEEN_"
                        "REVIEWED_YET: the daily review supplies the "
                        "supported benchmarks (HOLD and each recorded "
                        "alternative, labelled hypothetical)", dig)
        return _sec(OK, None, {"benchmarks": "the daily review's per-action "
                               "forecast error and the recorded alternatives, "
                               "labelled HYPOTHETICAL_ESTIMATE",
                               "daily_review": dig})

    async def servicing_cadence():
        hb = await _heartbeat(conn)
        if hb is None:
            return _sec(EMPTY, "NO_SERVICING_HEARTBEAT: the servicing task "
                        "has not written %s in this database" % SERVICING_KEY)
        return _sec(OK, None, {k: hb.get(k) for k in (
            "at", "state", "refusal", "source", "pass_at", "elapsed_s",
            "slow_half_ran", "review_interval_s", "servicing_cadence")},
            [{"kind": "ingestion_state", "id": SERVICING_KEY,
              "href": "/api/command/agents/xavier"}])

    for name, fn in (("status", status), ("versions", versions),
                     ("positions", positions), ("reviews", reviews),
                     ("ladder", _per_decision(_ladder, "ladder")),
                     ("alternatives", _per_decision(_alts, "alternatives")),
                     ("payout_tables", _per_decision(_tables,
                                                     "payout_tables")),
                     ("execution", execution), ("recovery", recovery),
                     ("performance", performance),
                     ("servicing_cadence", servicing_cadence)):
        await _guard(name, fn, sections)
    agent = {"agent_id": "XAVIER", "display_name": "Xavier",
             "mandate": ("position management, capital preservation within "
                         "the approved policy, indirect pairing"),
             "status": sections["status"].get("status")}
    return {"agent": agent, "read_at": at, "read_only": True,
            "version": VERSION, "sections": sections}


async def _heartbeat(conn) -> dict | None:
    v = await conn.fetchval("SELECT value FROM ingestion_state WHERE key=$1",
                            SERVICING_KEY)
    if v is None:
        return None
    return json.loads(v) if isinstance(v, str) else dict(v)


async def _exposure(conn, bound: dict) -> dict:
    from .. import bettor_funded_book as FB
    got = await FB.exposure(conn, account_id=bound["account_id"],
                            venue=bound["venue"])
    return {k: got.get(k) for k in (
        "total_exposure_usd", "held_basis_usd", "outstanding_orders",
        "unresolved", "ok", "refusal") if k in got} or {
        "keys": sorted(got)[:20]}


async def decision(conn, xavier_decision_id: str) -> dict | None:
    from .. import bettor_xavier as XV
    if not await XV.has_schema(conn):
        return None
    r = await conn.fetchrow(
        "SELECT * FROM bettor_xavier_decisions WHERE xavier_decision_id=$1",
        str(xavier_decision_id))
    if r is None:
        return None
    d = XV._row(r)
    for k in ("decided_at", "next_review_at", "created_at"):
        if k in d:
            d[k] = _epoch(d[k])
    d["execution"] = await XV.execution_state(
        conn, xavier_decision_id=str(xavier_decision_id))
    d["execution_events"] = [
        {k: (_epoch(v) if hasattr(v, "timestamp") else v)
         for k, v in e.items()}
        for e in await XV.execution_events(
            conn, xavier_decision_id=str(xavier_decision_id))]
    return {"read_only": True, "read_at": time.time(),
            "decision": d, "evidence": [_ev(str(xavier_decision_id))]}


async def _pool():
    try:
        return await get_pool()
    except Exception as exc:                                    # noqa: BLE001
        raise HTTPException(status_code=503, detail={
            "reason": "NO_DATABASE_POOL", "detail": type(exc).__name__})


@router.get("/api/command/agents/xavier",
            dependencies=[Depends(require_read)])
async def xavier_workspace(response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    pool = await _pool()
    async with pool.acquire() as conn:
        return await workspace(conn)


@router.get("/api/command/agents/xavier/decisions/{xavier_decision_id}",
            dependencies=[Depends(require_read)])
async def xavier_decision(xavier_decision_id: str, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    pool = await _pool()
    async with pool.acquire() as conn:
        got = await decision(conn, xavier_decision_id)
    if got is None:
        raise HTTPException(status_code=404, detail={
            "reason": "NO_XAVIER_DECISION_WITH_THAT_ID"})
    return got
