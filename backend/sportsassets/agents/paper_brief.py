"""THE PAPER EXPERIMENT IN ONE READ -- for the agents' conversations.

READ-ONLY: every statement here is a SELECT. Nothing is written, locked or
reserved; no ledger, session or decision writer is called.

`summary(conn, now=...)` returns what the Command Centre shows about the
paper experiment, from the SAME sources it uses:

  * account   -- `bettor_paper_ledger.balances` (the account strip and
                 `/api/command/paper/account`): cash, reserved, available,
                 equity, realised / unrealised P&L, fees, the last ledger
                 sequence and when it was committed.
  * session   -- `bettor_paper_session.active_session` / `.health` /
                 `.enablement` (the session banner and
                 `/api/command/paper/session`): id, start, status, last
                 heartbeat, passes, errors, venue mutation attempts.
  * decisions -- `paper_decisions` (Derek's paper decisions, as in
                 `/api/command/paper/derek`) for the UTC day of `now`: the
                 count by verdict and by refusal reason, and the newest.
  * reviews   -- `paper_xavier_reviews` for the same day: the count and the
                 newest, when the table exists.
  * management -- what Xavier has to manage: open paper positions (from
                 the same balances), open paper orders by role, handoffs.

`reconcile(conn, now=...)` is the ledger read Audrey audits: entries by kind
with their sequences, the latest entries, and the reconciliation checks
(sum of cash deltas = cash, sum of reserved deltas = reserved, available =
cash - reserved, the last entry's running balance agrees, exactly one
INITIAL_FUNDING equal to the starting cash and first in sequence, the entry
count agrees).

Persona chat turns this into numbered facts (`persona_facts`); Audrey's
management chat serves it as the read-only `paper_account` tool. The legacy
desk account (`bettor_desk_account_state`) is NOT read here: it is not the
paper account and never stands in for it.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from .. import bettor_paper_ledger as L
from .. import bettor_paper_session as S

#: the paper account the conversations describe; tests substitute their own
ACCOUNT_ID = L.ACCOUNT_ID
DAY_BASIS = "UTC calendar day of the question"
#: today's decisions listed one by one (counts cover all of them)
NEWEST_DECISIONS = 20
LEDGER_ENTRIES_SHOWN = 50
MANAGEMENT_ROLES = ("STANDING_PROTECTION", "HEDGE", "EXIT", "REDUCE")


def iso(epoch) -> str | None:
    if epoch is None:
        return None
    try:
        return _dt.datetime.fromtimestamp(
            float(epoch), _dt.timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError, OverflowError):
        return None


def _iso_any(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, _dt.datetime):
        return v.astimezone(_dt.timezone.utc).isoformat(timespec="seconds")
    return iso(v)


async def _regclass(conn, name: str) -> bool:
    try:
        return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL",
                                        name))
    except Exception:                                           # noqa: BLE001
        return False


def utc_day(now: float) -> tuple:
    d = _dt.datetime.fromtimestamp(float(now), _dt.timezone.utc).date()
    start = _dt.datetime(d.year, d.month, d.day, tzinfo=_dt.timezone.utc)
    return d.isoformat(), start, start + _dt.timedelta(days=1)


async def _account(conn, acct: str, now: float) -> dict:
    b = await L.balances(conn, acct, now=now)
    if not b.get("ok"):
        return {"ok": False, "refusal": b.get("refusal")}
    return {
        "ok": True, "account_id": acct, "source": "bettor_paper_ledger."
        "balances (the Command Centre account strip)",
        "currency": b.get("currency"),
        "starting_cash_usd": b.get("starting_cash_usd"),
        "cash_usd": b.get("cash_usd"), "reserved_usd": b.get("reserved_usd"),
        "available_usd": b.get("available_usd"),
        "total_equity_usd": b.get("total_equity_usd"),
        "equity_excluding_unmarked_usd": b.get(
            "equity_excluding_unmarked_usd"),
        "equity_basis": b.get("equity_basis"),
        "marks_complete": b.get("marks_complete"),
        "realized_pnl_usd": b.get("realized_pnl_usd"),
        "unrealized_pnl_usd": b.get("unrealized_pnl_usd"),
        "fees_paid_usd": b.get("fees_paid_usd"),
        "open_positions": len(b.get("open_positions") or []),
        "last_sequence": b.get("last_sequence"),
        "ledger_entries": b.get("ledger_entries"),
        "ledger_consistent": b.get("ledger_consistent"),
        "last_updated_at": iso(b.get("last_updated_at")),
        "real_money_submission": b.get("real_money_submission")}


async def _session(conn, acct: str) -> dict:
    en = await S.enablement(conn)
    sess = await S.active_session(conn, acct)
    out: dict[str, Any] = {"active": False, "session_id": None,
                           "enabled": bool(en.get("enabled")),
                           "refusal": en.get("refusal")}
    if sess is None:
        out["why"] = "NO_ACTIVE_PAPER_SESSION: %s" % (
            en.get("refusal") or "the scheduled pass has not run")
        return out
    h = await S.health(conn, sess["session_id"]) or {}
    out.update(active=True, session_id=sess["session_id"],
               status=sess.get("status"),
               started_at=iso(sess.get("started_at")),
               simulator_version=sess.get("simulator_version"),
               reporting_tz=sess.get("reporting_tz"),
               health_recorded=bool(h),
               last_heartbeat_at=iso(h.get("heartbeat_at")),
               passes=h.get("passes"), errors=h.get("errors"),
               mutation_attempts=h.get("mutation_attempts"),
               last_error=(str(h["last_error"])[:200]
                           if h.get("last_error") else None))
    return out


async def _decisions(conn, acct: str, now: float) -> dict:
    day, start, end = utc_day(now)
    out: dict[str, Any] = {"day": day, "day_basis": DAY_BASIS, "total": 0,
                           "by_verdict": {}, "by_reason": [],
                           "newest_decided_at": None, "newest": []}
    if not await _regclass(conn, "paper_decisions"):
        out["why"] = "PAPER_DECISIONS_TABLE_ABSENT"
        return out
    rows = await conn.fetch(
        "SELECT verdict, coalesce(refusal, 'ENTER') AS reason, "
        "       count(*)::int AS n, max(decided_at) AS newest_at, "
        "       (array_agg(decision_id ORDER BY decided_at DESC))[1] "
        "         AS newest_id "
        "  FROM paper_decisions WHERE account_id = $1 "
        "   AND decided_at >= $2 AND decided_at < $3 "
        " GROUP BY 1, 2 ORDER BY 3 DESC, 2", acct, start, end)
    for r in rows:
        out["total"] += r["n"]
        out["by_verdict"][r["verdict"]] = \
            out["by_verdict"].get(r["verdict"], 0) + r["n"]
        out["by_reason"].append({"verdict": r["verdict"],
                                 "reason": r["reason"], "count": r["n"],
                                 "newest_at": _iso_any(r["newest_at"]),
                                 "newest_decision_id": r["newest_id"]})
    newest = await conn.fetch(
        "SELECT decision_id, session_id, decided_at, verdict, refusal, "
        "       fixture, us_market_slug, holding_side "
        "  FROM paper_decisions WHERE account_id = $1 "
        "   AND decided_at >= $2 AND decided_at < $3 "
        " ORDER BY decided_at DESC, decision_id DESC LIMIT $4", acct, start,
        end, NEWEST_DECISIONS)
    out["newest"] = [{"decision_id": r["decision_id"],
                      "session_id": r["session_id"],
                      "decided_at": _iso_any(r["decided_at"]),
                      "verdict": r["verdict"], "refusal": r["refusal"],
                      "market": r["fixture"] or r["us_market_slug"],
                      "holding_side": r["holding_side"]} for r in newest]
    if out["newest"]:
        out["newest_decided_at"] = out["newest"][0]["decided_at"]
    return out


async def _reviews(conn, acct: str, now: float) -> dict | None:
    if not await _regclass(conn, "paper_xavier_reviews"):
        return None
    day, start, end = utc_day(now)
    r = await conn.fetchrow(
        "SELECT count(*)::int AS n, max(reviewed_at) AS newest_at, "
        "       (array_agg(review_id ORDER BY reviewed_at DESC))[1] "
        "         AS newest_id "
        "  FROM paper_xavier_reviews WHERE account_id = $1 "
        "   AND reviewed_at >= $2 AND reviewed_at < $3", acct, start, end)
    return {"day": day, "total": int(r["n"] or 0),
            "newest_at": _iso_any(r["newest_at"]),
            "newest_review_id": r["newest_id"]}


async def _management(conn, acct: str, account: dict | None) -> dict:
    out: dict[str, Any] = {
        "open_positions": (account or {}).get("open_positions"),
        "open_orders_by_role": {}, "open_management_orders": 0,
        "open_entry_orders": 0, "handoffs": None}
    if await _regclass(conn, "paper_orders"):
        for r in await conn.fetch(
                "SELECT role, count(*)::int AS n FROM paper_orders "
                " WHERE account_id = $1 AND state = ANY($2::text[]) "
                " GROUP BY role ORDER BY role", acct, list(L.OPEN_STATES)):
            out["open_orders_by_role"][r["role"]] = r["n"]
            if r["role"] in MANAGEMENT_ROLES:
                out["open_management_orders"] += r["n"]
            elif r["role"] == "ENTRY":
                out["open_entry_orders"] += r["n"]
    if await _regclass(conn, "paper_handoffs"):
        out["handoffs"] = int(await conn.fetchval(
            "SELECT count(*) FROM paper_handoffs WHERE account_id = $1",
            acct) or 0)
    out["nothing_to_manage"] = bool(
        not out["open_positions"] and not out["open_management_orders"]
        and not out["open_entry_orders"] and not out["handoffs"])
    return out


def _d(v):
    return L.D(v if v is not None else 0)


async def reconcile(conn, *, now: float, account_id: str | None = None,
                    entries: int = LEDGER_ENTRIES_SHOWN) -> dict:
    """THE LEDGER, READ AND RECONCILED (read-only). Every check names the
    two figures it compares; `reconciled` is True only when all pass."""
    acct = account_id or ACCOUNT_ID
    out: dict[str, Any] = {"present": False, "account_id": acct,
                           "as_of": iso(now), "data_label": L.DATA_LABEL}
    if not await _regclass(conn, "paper_ledger"):
        out["why"] = "MIGRATION_171_IS_NOT_APPLIED"
        return out
    out["present"] = True
    b = await L.balances(conn, acct, now=now)
    if not b.get("ok"):
        out["why"] = b.get("refusal")
        return out
    sess = await S.active_session(conn, acct)
    out["session_id"] = (sess or {}).get("session_id")
    agg = await conn.fetchrow(
        "SELECT count(*)::int AS n, coalesce(sum(cash_delta_usd), 0) AS cash, "
        "       coalesce(sum(reserved_delta_usd), 0) AS reserved, "
        "       min(seq) AS first_seq, max(seq) AS last_seq, "
        "       count(*) FILTER (WHERE kind = 'INITIAL_FUNDING')::int "
        "         AS n_initial, "
        "       coalesce(sum(cash_delta_usd) FILTER (WHERE kind = "
        "         'INITIAL_FUNDING'), 0) AS initial_cash, "
        "       min(seq) FILTER (WHERE kind = 'INITIAL_FUNDING') "
        "         AS initial_seq "
        "  FROM paper_ledger WHERE account_id = $1", acct)
    kinds = await conn.fetch(
        "SELECT kind, count(*)::int AS n, min(seq) AS first_seq, "
        "       max(seq) AS last_seq, sum(cash_delta_usd) AS cash, "
        "       sum(reserved_delta_usd) AS reserved "
        "  FROM paper_ledger WHERE account_id = $1 "
        " GROUP BY kind ORDER BY min(seq)", acct)
    cash, res = _d(b["cash_usd"]), _d(b["reserved_usd"])
    avail, start = _d(b["available_usd"]), _d(b["starting_cash_usd"])
    checks = [
        {"check": "CASH_EQUALS_SUM_OF_CASH_DELTAS",
         "ledger_sum_usd": L.f(_d(agg["cash"])), "balance_usd": L.f(cash),
         "ok": _d(agg["cash"]) == cash},
        {"check": "RESERVED_EQUALS_SUM_OF_RESERVED_DELTAS",
         "ledger_sum_usd": L.f(_d(agg["reserved"])),
         "balance_usd": L.f(res), "ok": _d(agg["reserved"]) == res},
        {"check": "AVAILABLE_EQUALS_CASH_MINUS_RESERVED",
         "cash_minus_reserved_usd": L.f(cash - res),
         "balance_usd": L.f(avail), "ok": cash - res == avail},
        {"check": "LAST_ENTRY_RUNNING_BALANCE_AGREES",
         "ok": bool(b.get("ledger_consistent"))},
        {"check": "EXACTLY_ONE_INITIAL_FUNDING", "count": agg["n_initial"],
         "ok": agg["n_initial"] == 1},
        {"check": "INITIAL_FUNDING_EQUALS_STARTING_CASH",
         "initial_funding_usd": L.f(_d(agg["initial_cash"])),
         "starting_cash_usd": L.f(start),
         "ok": _d(agg["initial_cash"]) == start},
        {"check": "INITIAL_FUNDING_IS_THE_FIRST_ENTRY",
         "initial_funding_seq": agg["initial_seq"],
         "first_seq": agg["first_seq"],
         "ok": agg["initial_seq"] is not None
         and agg["initial_seq"] == agg["first_seq"]},
        {"check": "ENTRY_COUNT_AGREES", "ledger_count": agg["n"],
         "balances_count": b.get("ledger_entries"),
         "ok": agg["n"] == b.get("ledger_entries")}]
    latest = await L.latest_entries(conn, acct, limit=int(entries))
    out.update(
        reconciled=all(c["ok"] for c in checks),
        failed_checks=[c["check"] for c in checks if not c["ok"]],
        checks=checks, entries_count=agg["n"], first_seq=agg["first_seq"],
        last_seq=agg["last_seq"], initial_funding_seq=agg["initial_seq"],
        balances={k: b.get(k) for k in (
            "cash_usd", "reserved_usd", "available_usd", "starting_cash_usd",
            "total_equity_usd", "realized_pnl_usd", "unrealized_pnl_usd",
            "last_sequence", "ledger_consistent")},
        last_updated_at=iso(b.get("last_updated_at")),
        by_kind=[{"kind": r["kind"], "count": r["n"],
                  "first_seq": r["first_seq"], "last_seq": r["last_seq"],
                  "cash_delta_usd": L.f(_d(r["cash"])),
                  "reserved_delta_usd": L.f(_d(r["reserved"]))}
                 for r in kinds],
        latest_entries=[{
            "seq": e["sequence"], "kind": e["kind"],
            "session_id": e.get("session_id"),
            "cash_delta_usd": e["cash_delta_usd"],
            "reserved_delta_usd": e["reserved_delta_usd"],
            "cash_after_usd": e["cash_after_usd"],
            "reserved_after_usd": e["reserved_after_usd"],
            "available_after_usd": e["available_after_usd"],
            "order_id": e.get("order_id"), "fill_id": e.get("fill_id"),
            "committed_at": iso(e.get("committed_at"))} for e in latest],
        latest_entries_shown=len(latest))
    return out


async def summary(conn, *, now: float, account_id: str | None = None) -> dict:
    """The paper experiment now (see the module docstring). Never raises: a
    part that cannot be read is named in `unavailable`."""
    acct = account_id or ACCOUNT_ID
    out: dict[str, Any] = {
        "present": False, "account_id": acct, "as_of": iso(now),
        "data_label": L.DATA_LABEL,
        "paper_only": "paper figures; never mixed with funded or legacy "
                      "desk-account figures",
        "account": None, "session": None, "decisions_today": None,
        "xavier_reviews_today": None, "management": None,
        "reconciliation": None, "unavailable": [], "why": None}
    if not await _regclass(conn, "paper_ledger"):
        out["why"] = "MIGRATION_171_IS_NOT_APPLIED"
        return out
    out["present"] = True
    for key, coro in (("account", _account(conn, acct, now)),
                      ("session", _session(conn, acct)),
                      ("decisions_today", _decisions(conn, acct, now)),
                      ("xavier_reviews_today", _reviews(conn, acct, now))):
        try:
            out[key] = await coro
        except Exception as exc:                                # noqa: BLE001
            out["unavailable"].append("%s: READ_FAILED %s"
                                      % (key, type(exc).__name__))
    for key, coro in (("management", _management(conn, acct, out["account"])),
                      ("reconciliation", reconcile(conn, now=now,
                                                   account_id=acct,
                                                   entries=0))):
        try:
            out[key] = await coro
        except Exception as exc:                                # noqa: BLE001
            out["unavailable"].append("%s: READ_FAILED %s"
                                      % (key, type(exc).__name__))
    return out


def citations(s: dict) -> list:
    """[{kind, id}] for the records a summary rests on."""
    out = []
    a = s.get("account") or {}
    if a.get("ok"):
        out.append({"kind": "paper_ledger", "id": "%s#seq%s" % (
            a["account_id"], a.get("last_sequence")),
            "href": "/api/command/paper/account"})
    se = s.get("session") or {}
    if se.get("session_id"):
        out.append({"kind": "paper_sessions", "id": se["session_id"],
                    "href": "/api/command/paper/session"})
    for d in ((s.get("decisions_today") or {}).get("newest") or [])[:10]:
        out.append({"kind": "paper_decisions", "id": d["decision_id"],
                    "href": "/api/command/paper/derek"})
    rv = s.get("xavier_reviews_today") or {}
    if rv.get("newest_review_id"):
        out.append({"kind": "paper_xavier_reviews",
                    "id": rv["newest_review_id"],
                    "href": "/api/command/paper/xavier"})
    return out
