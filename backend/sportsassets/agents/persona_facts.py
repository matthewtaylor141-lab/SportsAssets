"""THE FACTS THE THREE AGENTS TALK FROM -- read from the database, numbered.

READ-ONLY. `gather` returns ONE fact list for a question; Derek, Xavier and
Audrey are all given the SAME list, so their answers cite the same record ids
and the same numbers and differ only in perspective. Each fact is

    {"fact_id": "F3", "source": <table>, "record_id": <primary key>,
     "field": <column / json path>, "value": <as recorded>, "text": <label>}

WHERE FACTS COME FROM, in order
  1. the DEMONSTRATION position -- only when the caller asks for it
     (context {"demonstration": true} or position_id "DEMONSTRATION"). Every
     fact is labelled DEMONSTRATION and none is a record in any table.
  2. the PAPER LEDGER, when its tables exist in this database; otherwise the
     answer says "paper ledger not in this build". For a question about the
     book (no named position) these are the paper experiment as it stands,
     from `paper_brief.summary` -- the live balances the Command Centre strip
     shows (`bettor_paper_ledger.balances`), the active session and its
     health, and today's paper decisions by verdict and refusal. For a named
     position every paper table (`paper_*` / `*_paper_*`) is searched for it.
  3. the funded book's positions (`bettor_funded_intents`, `_fills`,
     `_economics`) and the LEGACY desk accounts' cash
     (`bettor_desk_account_state`) -- labelled as the legacy desk account
     with its as-of time; never presented as the paper account's cash.
  4. the agents' own records: Derek's entry decisions with the stored V2
     policy decision (`derek_entry_decisions.evidence.policy_decision`),
     Xavier's decisions (`bettor_xavier_decisions`) and standing protective
     orders with their per-state payoff table
     (`bettor_standing_order_plans.floor.regions`), Audrey's audit reports
     (`audrey_audit_reports`) and the agents' status (`agent_status`).

A question naming a team ("the Yankees position") is matched on the team's
nickname and venue abbreviation; when nothing matches, `found` is False and
`checked` lists every source that was read -- the answer then says so and
never substitutes another position. Nothing is computed that a record did not
store, except the arithmetic the demonstration states explicitly.
"""

from __future__ import annotations

import datetime as _dt
import decimal
import json
import re
from typing import Any

DEMO_LABEL = "DEMONSTRATION"
DEMO_POSITION_ID = "DEMONSTRATION-NYY-ML"
PAPER_NOT_IN_BUILD = "paper ledger not in this build"

#: nickname -> venue abbreviations (the search terms besides the nickname)
TEAM_CODES = {
    "yankees": ("nyy",), "red sox": ("bos",), "mets": ("nym",),
    "dodgers": ("lad",), "padres": ("sd",), "astros": ("hou",),
    "mariners": ("sea",), "braves": ("atl",), "cubs": ("chc",),
    "white sox": ("cws", "chw"), "blue jays": ("tor",), "orioles": ("bal",),
    "rays": ("tb",), "phillies": ("phi",), "giants": ("sf",),
    "cardinals": ("stl",), "brewers": ("mil",), "twins": ("min",),
    "guardians": ("cle",), "tigers": ("det",), "royals": ("kc",),
    "angels": ("laa",), "rangers": ("tex",), "rockies": ("col",),
    "pirates": ("pit",), "reds": ("cin",), "marlins": ("mia",),
    "nationals": ("wsh",), "athletics": ("oak", "ath"),
    "diamondbacks": ("ari",),
}

_LIVE_STATES = ("INTENT_RECORDED", "SEND_ATTEMPTED", "ACKNOWLEDGED",
                "PARTIALLY_FILLED", "FILLED", "UNRESOLVED")


def _jsonable(v):
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (_dt.datetime, _dt.date)):
        return v.isoformat()
    return v


def _obj(v, default=None):
    if v is None:
        return default
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return default


async def _regclass(conn, name: str) -> bool:
    try:
        return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL",
                                        name))
    except Exception:                                           # noqa: BLE001
        return False


def subject_of(question: str) -> dict | None:
    """The team a question names, with its search terms, or None."""
    q = " %s " % re.sub(r"[^a-z0-9 ]", " ", str(question or "").lower())
    for nick in sorted(TEAM_CODES, key=len, reverse=True):
        if " %s " % nick in q:
            return {"label": nick.title(), "nickname": nick,
                    "terms": [nick] + list(TEAM_CODES[nick])}
    return None


def _likes(terms: list) -> list:
    out = []
    for t in terms:
        t = str(t).replace("\\", "\\\\").replace("%", "\\%").replace("_",
                                                                     "\\_")
        # a short code matches as a slug token, not inside another word
        out.append("%" + t + "%" if len(t) > 3 else "%-" + t + "-%")
    return out


class Facts:
    def __init__(self):
        self.items: list[dict] = []
        self.checked: list[dict] = []
        self.missing: list[str] = []

    def add(self, source, record_id, field, value, text) -> str:
        fid = "F%d" % (len(self.items) + 1)
        self.items.append({"fact_id": fid, "source": source,
                           "record_id": str(record_id), "field": field,
                           "value": _jsonable(value), "text": text})
        return fid

    def check(self, source, status, matches=0, why=None):
        self.checked.append({"source": source, "status": status,
                             "matches": int(matches), "why": why})

    def miss(self, text):
        if text not in self.missing:
            self.missing.append(text)


# ═════════════════════════════════════════════════════════════════════
# THE DEMONSTRATION POSITION (clearly labelled; never a record)
# ═════════════════════════════════════════════════════════════════════

DEMO = {
    "entry_id": "DEMONSTRATION:derek_entry_decisions:demo-nyy-ml-entry",
    "hedge_id": "DEMONSTRATION:bettor_xavier_decisions:demo-nyy-ml-hedge",
    "plan_id": "DEMONSTRATION:bettor_standing_order_plans:demo-bos-p25-plan",
    "stake_usd": 1000.0, "price": 0.50, "qty": 2000,
    "p_internal": 0.60, "p_pinnacle": 0.58, "p_blended": 0.59,
    "edge_pp": 9.0, "threshold_pp": 5.0, "ev_before_fees_usd": 180.0,
    "hedge_usd": 800.0, "hedge_price": 0.40, "hedge_qty": 2000,
    "total_cost_usd": 1800.0,
    "floors": [("Yankees win by 3 or more", 200.0),
               ("Yankees win by 1 or 2", 2200.0),
               ("Red Sox win", 200.0)],
    "unhedged_loss_usd": -1000.0, "unhedged_win_usd": 1000.0,
}


def demonstration_facts() -> Facts:
    f = Facts()
    e, h, p = DEMO["entry_id"], DEMO["hedge_id"], DEMO["plan_id"]
    f.add(DEMO_LABEL, e, "label", DEMO_LABEL,
          "DEMONSTRATION position -- not a production or paper record")
    f.add(DEMO_LABEL, e, "instrument", "Yankees moneyline (YES = Yankees "
          "win)", "instrument: Yankees moneyline")
    f.add(DEMO_LABEL, e, "stake_usd", DEMO["stake_usd"], "stake $1,000")
    f.add(DEMO_LABEL, e, "executable_price", DEMO["price"],
          "entry price $0.50 per contract")
    f.add(DEMO_LABEL, e, "qty", DEMO["qty"], "2,000 contracts")
    f.add(DEMO_LABEL, e, "p_internal", DEMO["p_internal"],
          "internal probability 0.60")
    f.add(DEMO_LABEL, e, "p_pinnacle", DEMO["p_pinnacle"],
          "Pinnacle probability 0.58")
    f.add(DEMO_LABEL, e, "p_blended", DEMO["p_blended"],
          "blended probability 0.59 = (0.60 + 0.58) / 2")
    f.add(DEMO_LABEL, e, "gross_edge_pp", DEMO["edge_pp"],
          "edge 9 pp = 0.59 - 0.50")
    f.add(DEMO_LABEL, e, "threshold_gross_edge_pp", DEMO["threshold_pp"],
          "policy minimum edge 5 pp (DEREK_ENTRY_POLICY_V2)")
    f.add(DEMO_LABEL, e, "expected_gross_profit_usd",
          DEMO["ev_before_fees_usd"],
          "expected profit $180 before fees = 2,000 x 0.09")
    f.add(DEMO_LABEL, h, "hedge", "Red Sox +2.5",
          "hedge instrument: Red Sox +2.5")
    f.add(DEMO_LABEL, h, "hedge_cost_usd", DEMO["hedge_usd"],
          "hedge cost $800")
    f.add(DEMO_LABEL, h, "hedge_price", DEMO["hedge_price"],
          "hedge price $0.40 per contract")
    f.add(DEMO_LABEL, h, "hedge_qty", DEMO["hedge_qty"],
          "2,000 hedge contracts")
    f.add(DEMO_LABEL, p, "total_cost_usd", DEMO["total_cost_usd"],
          "total cost $1,800 = $1,000 + $800")
    for state, net in DEMO["floors"]:
        f.add(DEMO_LABEL, p, "floor:" + state, net,
              "%s: $%s before fees" % (state, format(int(net), ",")))
    f.add(DEMO_LABEL, h, "unhedged_worst_case_usd",
          DEMO["unhedged_loss_usd"],
          "unhedged, a Red Sox win loses the $1,000 stake")
    f.add(DEMO_LABEL, h, "unhedged_best_case_usd", DEMO["unhedged_win_usd"],
          "unhedged, a Yankees win pays $1,000 net before fees")
    f.miss("fees on either leg (every figure is before fees)")
    f.miss("a settlement (no result exists, so nothing is realised)")
    f.miss("the probability of the Yankees winning by exactly 1 or 2 runs, "
           "so the hedged pair's expected value cannot be graded")
    f.check(DEMO_LABEL, "DEMONSTRATION", len(f.items))
    return f


# ═════════════════════════════════════════════════════════════════════
# THE DATABASE
# ═════════════════════════════════════════════════════════════════════

async def paper_tables(conn) -> list:
    try:
        rows = await conn.fetch(
            "SELECT table_name FROM information_schema.tables WHERE "
            " table_schema = current_schema() AND table_type = 'BASE TABLE' "
            " AND (table_name LIKE 'paper\\_%' OR table_name LIKE "
            " '%\\_paper\\_%' OR table_name LIKE '%\\_paper') "
            " ORDER BY table_name")
        return [r["table_name"] for r in rows]
    except Exception:                                           # noqa: BLE001
        return []


def _scalar_fields(row: dict, limit: int = 12) -> list:
    out = []
    for k, v in row.items():
        if isinstance(v, (int, float, str, bool)) and v not in ("", None) \
                and len(str(v)) <= 120:
            out.append((k, v))
        if len(out) >= limit:
            break
    return out


def _row_key(row: dict) -> str:
    for k in ("position_id", "trade_id", "entry_id", "id", "intent_id",
              "order_id", "account_id"):
        if row.get(k) not in (None, ""):
            return str(row[k])
    return str(next(iter(row.values()), "?"))


async def _paper(conn, f: Facts, *, likes, context_ids, limit=5) -> bool:
    tables = await paper_tables(conn)
    if not tables:
        f.check("paper_ledger", "NOT_IN_THIS_BUILD", 0, PAPER_NOT_IN_BUILD)
        f.miss(PAPER_NOT_IN_BUILD)
        return False
    found = False
    for t in tables:
        try:
            if likes or context_ids:
                rows = await conn.fetch(
                    'SELECT to_jsonb(x) AS j FROM "%s" x WHERE '
                    " to_jsonb(x)::text ILIKE ANY($1::text[]) OR "
                    " to_jsonb(x)::text LIKE ANY($2::text[]) LIMIT $3"
                    % t.replace('"', ''), likes or [],
                    ["%" + i + "%" for i in context_ids], int(limit))
            else:
                rows = await conn.fetch(
                    'SELECT to_jsonb(x) AS j FROM "%s" x LIMIT $1'
                    % t.replace('"', ''), int(limit))
        except Exception as exc:                                # noqa: BLE001
            f.check(t, "READ_FAILED", 0, type(exc).__name__)
            continue
        f.check(t, "MATCHED" if rows else "NO_MATCH", len(rows))
        for r in rows:
            row = _obj(r["j"], {}) or {}
            rid = _row_key(row)
            for k, v in _scalar_fields(row):
                f.add(t, rid, k, v, "paper %s %s = %s" % (t, k, v))
            found = True
    return found


def _money(v) -> str:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "unknown"
    s = "$" + format(abs(x), ",.2f")
    if s.endswith(".00"):
        s = s[:-3]
    return ("-" if x < 0 else "") + s


async def _positions(conn, f: Facts, *, likes, context_ids,
                     limit=5) -> list:
    """The funded book's positions matching the subject (or, with no
    subject, the live ones). Returns the intent ids."""
    if not await _regclass(conn, "bettor_funded_intents"):
        f.check("bettor_funded_intents", "TABLE_ABSENT")
        return []
    where = ["state = ANY($1::text[])"]
    args: list[Any] = [list(_LIVE_STATES)]
    if likes or context_ids:
        args += [likes or [], context_ids or []]
        where.append("(us_market_slug ILIKE ANY($2::text[]) OR event_key "
                     "ILIKE ANY($2::text[]) OR intent_id = ANY($3::text[]) "
                     "OR decision_ref->>'derek_decision_id' = "
                     "ANY($3::text[]))")
    args.append(int(limit))
    rows = await conn.fetch(
        "SELECT intent_id, account_id, us_market_slug, event_key, "
        " order_intent, limit_price, quantity, collateral_usd, state, "
        " created_at FROM bettor_funded_intents WHERE %s ORDER BY created_at "
        " DESC LIMIT $%d" % (" AND ".join(where), len(args)), *args)
    f.check("bettor_funded_intents", "MATCHED" if rows else "NO_MATCH",
            len(rows))
    ids = []
    for r in rows:
        d = _jsonable(dict(r))
        iid = d["intent_id"]
        ids.append(iid)
        f.add("bettor_funded_intents", iid, "position",
              "%s %s" % (d["us_market_slug"], d["order_intent"]),
              "funded position %s on %s (%s), state %s" % (
                  iid, d["us_market_slug"],
                  "long" if d["order_intent"].endswith("LONG") else "short",
                  d["state"]))
        f.add("bettor_funded_intents", iid, "quantity", d["quantity"],
              "ordered %s contracts at limit %s" % (d["quantity"],
                                                    d["limit_price"]))
        f.add("bettor_funded_intents", iid, "collateral_usd",
              d["collateral_usd"], "collateral committed %s"
              % _money(d["collateral_usd"]))
    if ids and await _regclass(conn, "bettor_funded_fills"):
        for r in await conn.fetch(
                "SELECT intent_id, sum(qty)::float8 AS qty, "
                " sum(cash_usd)::float8 AS cash, sum(fee_usd)::float8 AS fee "
                " FROM bettor_funded_fills WHERE intent_id = ANY($1::text[]) "
                " GROUP BY intent_id", ids):
            f.add("bettor_funded_fills", r["intent_id"], "filled_qty",
                  r["qty"], "filled %s contracts for %s plus %s fees" % (
                      format(r["qty"], ",g"), _money(r["cash"]),
                      _money(r["fee"])))
    if ids and await _regclass(conn, "bettor_funded_economics"):
        for r in await conn.fetch(
                "SELECT intent_id, sum(amount_usd)::float8 AS net, "
                " bool_or(provisional) AS provisional FROM "
                " bettor_funded_economics WHERE intent_id = ANY($1::text[]) "
                " GROUP BY intent_id", ids):
            f.add("bettor_funded_economics", r["intent_id"], "net_usd",
                  r["net"], "booked net %s%s" % (
                      _money(r["net"]),
                      " (provisional)" if r["provisional"] else ""))
    return ids


LEGACY_DESK = "LEGACY desk account"
LEGACY_NOTE = "bettor_desk_account_state; NOT the paper account"


async def _cash(conn, f: Facts, limit=10) -> None:
    """The LEGACY desk accounts (`bettor_desk_account_state`). Every fact is
    labelled as such, with its as-of time: this is not the paper account,
    and it never stands in for the paper ledger's balances."""
    if not await _regclass(conn, "bettor_desk_account_state"):
        f.check("bettor_desk_account_state", "TABLE_ABSENT")
        return
    rows = await conn.fetch(
        "SELECT account_id, cash_usd, starting_cash_usd, realized_pnl_usd, "
        " fees_usd, updated_at FROM bettor_desk_account_state "
        " ORDER BY account_id LIMIT $1", int(limit))
    f.check("bettor_desk_account_state", "MATCHED" if rows else "NO_MATCH",
            len(rows))
    for r in rows:
        d = _jsonable(dict(r))
        f.add("bettor_desk_account_state", d["account_id"],
              "legacy_desk_cash_usd", d["cash_usd"],
              "%s %s (%s) cash %s as of %s (started %s, realised P&L %s, "
              "fees %s)" % (
                  LEGACY_DESK, d["account_id"], LEGACY_NOTE,
                  _money(d["cash_usd"]), d["updated_at"] or "an unknown time",
                  _money(d["starting_cash_usd"]),
                  _money(d["realized_pnl_usd"]), _money(d["fees_usd"])))


PAPER_LIVE_SOURCES = ("paper_ledger", "paper_sessions", "paper_decisions",
                      "paper_orders", "paper_xavier_reviews")


async def _paper_live(conn, f: Facts, *, now: float | None) -> dict:
    """THE PAPER EXPERIMENT NOW, as numbered facts: the live ledger balances
    (the same `bettor_paper_ledger.balances` the Command Centre strip shows),
    the active session and its health, and today's paper decisions. Every
    count and total the question may need is a fact, so an answer never has
    to tally rows or do arithmetic of its own."""
    import time as _time

    from . import paper_brief as PB
    s = await PB.summary(conn, now=float(now if now is not None
                                         else _time.time()))
    if not s["present"]:
        f.check("paper_ledger", "NOT_IN_THIS_BUILD", 0, PAPER_NOT_IN_BUILD)
        f.miss(PAPER_NOT_IN_BUILD)
        return s
    for u in s["unavailable"]:
        f.check(u.split(":")[0], "READ_FAILED", 0, u)
    a = s.get("account") or {}
    acct = s["account_id"]
    if a.get("ok"):
        rid = "%s#seq%s" % (acct, a.get("last_sequence"))
        f.check("paper_ledger", "MATCHED", 1)
        f.add("paper_ledger", rid, "paper_cash_usd", a["cash_usd"],
              "paper account %s cash %s (from the paper ledger)"
              % (acct, _money(a["cash_usd"])))
        f.add("paper_ledger", rid, "paper_reserved_usd", a["reserved_usd"],
              "paper account reserved %s (part of cash, not extra)"
              % _money(a["reserved_usd"]))
        f.add("paper_ledger", rid, "paper_available_usd", a["available_usd"],
              "paper account available %s" % _money(a["available_usd"]))
        if a.get("total_equity_usd") is not None:
            f.add("paper_ledger", rid, "paper_equity_usd",
                  a["total_equity_usd"], "paper account total equity %s "
                  "(cash plus marked open positions)"
                  % _money(a["total_equity_usd"]))
        else:
            f.add("paper_ledger", rid, "paper_equity_usd", None,
                  "paper account total equity NOT STATED: %s (marked-only "
                  "equity %s)" % (a.get("equity_basis"), _money(
                      a.get("equity_excluding_unmarked_usd"))))
        f.add("paper_ledger", rid, "paper_realized_pnl_usd",
              a["realized_pnl_usd"], "paper realised P&L %s"
              % _money(a["realized_pnl_usd"]))
        f.add("paper_ledger", rid, "paper_unrealized_pnl_usd",
              a.get("unrealized_pnl_usd"),
              "paper unrealised P&L %s" % (
                  _money(a["unrealized_pnl_usd"])
                  if a.get("unrealized_pnl_usd") is not None else
                  "unknown (an open position has no mark)"))
        f.add("paper_ledger", rid, "paper_open_positions",
              a["open_positions"], "paper open positions %s"
              % a["open_positions"])
        f.add("paper_ledger", rid, "paper_last_sequence",
              a.get("last_sequence"), "paper ledger last sequence %s, last "
              "updated %s (starting cash %s)" % (
                  a.get("last_sequence"), a.get("last_updated_at") or "never",
                  _money(a.get("starting_cash_usd"))))
    else:
        f.check("paper_ledger", "NO_MATCH", 0, a.get("refusal"))
        f.miss("the paper account's balances (%s)" % (
            a.get("refusal") or "the paper ledger could not be read"))
    se = s.get("session") or {}
    if se.get("active"):
        f.check("paper_sessions", "MATCHED", 1)
        f.add("paper_sessions", se["session_id"], "paper_session",
              se["session_id"], "active paper session %s, status %s, "
              "started %s%s" % (
                  se["session_id"], se.get("status"), se.get("started_at"),
                  "" if se.get("enabled") else
                  "; scheduled paper passes are NOT enabled (%s)"
                  % se.get("refusal")))
        if se.get("health_recorded"):
            f.add("paper_sessions", se["session_id"], "paper_session_health",
                  se.get("passes"), "paper session health: last heartbeat "
                  "%s, %s passes, %s errors, %s venue mutation attempts%s" % (
                      se.get("last_heartbeat_at") or "never",
                      se.get("passes"), se.get("errors"),
                      se.get("mutation_attempts"),
                      ("; last error %s" % se["last_error"])
                      if se.get("last_error") else ""))
    elif s.get("session") is not None:
        f.check("paper_sessions", "NO_MATCH", 0, se.get("why"))
        f.add("paper_sessions", acct, "paper_session", None,
              "no paper session is active (%s)" % se.get("why"))
    d = s.get("decisions_today")
    if d is not None and not d.get("why"):
        f.check("paper_decisions", "MATCHED" if d["total"] else "NO_MATCH",
                d["total"])
        bv = d["by_verdict"]
        rid = (d["newest"][0]["decision_id"] if d["newest"]
               else "%s@%s" % (acct, d["day"]))
        f.add("paper_decisions", rid, "paper_decisions_today", d["total"],
              "paper decisions today (%s, UTC), all strategies together: %s "
              "recorded -- %s ENTER, %s REFUSE%s%s" % (
                  d["day"], d["total"], bv.get("ENTER", 0),
                  bv.get("REFUSE", 0),
                  ("; newest at %s" % d["newest_decided_at"])
                  if d["newest_decided_at"] else "",
                  ("; by strategy: %s" % ", ".join(
                      "%s %s" % (k, v["total"]) for k, v in sorted(
                          (d.get("by_strategy") or {}).items())))
                  if d.get("by_strategy") else ""))
        # EACH STRATEGY APART (migration 182): a benchmark decision is never
        # one of Derek's, and the two are never summed under one owner.
        for st, g in sorted((d.get("by_strategy") or {}).items()):
            gv = g["by_verdict"]
            f.add("paper_decisions", g["newest_decision_id"],
                  "paper_decisions_today@%s" % st, g["total"],
                  "%s: %s paper decision%s today -- %s ENTER, %s REFUSE "
                  "(newest %s; shown at %s)" % (
                      g["label"], g["total"],
                      "" if g["total"] == 1 else "s", gv.get("ENTER", 0),
                      gv.get("REFUSE", 0), g["newest_at"], g["href"]))
        for g in d["by_reason"]:
            st = g.get("strategy") or PB.DEFAULT_STRATEGY
            f.add("paper_decisions", g["newest_decision_id"],
                  ("paper_decisions_today:%s" % g["reason"]
                   if st == PB.DEFAULT_STRATEGY else
                   "paper_decisions_today@%s:%s" % (st, g["reason"])),
                  g["count"],
                  "%s paper decision%s today by %s %s%s (newest %s)" % (
                      g["count"], "" if g["count"] == 1 else "s", st,
                      "ENTER" if g["verdict"] == "ENTER" else "REFUSED: ",
                      "" if g["verdict"] == "ENTER" else g["reason"],
                      g["newest_at"]))
        for n in d["newest"]:
            # each of today's decisions, by id (newest first)
            f.add("paper_decisions", n["decision_id"],
                  "paper_decision:%s" % n["decision_id"], n["verdict"],
                  "paper decision %s (strategy %s) at %s in session %s on "
                  "%s: %s%s" % (
                      n["decision_id"],
                      n.get("strategy") or PB.DEFAULT_STRATEGY,
                      n["decided_at"], n["session_id"],
                      n["market"], n["verdict"], (" -- refused: %s"
                                                   % n["refusal"])
                      if n["refusal"] else ""))
    elif d is not None:
        f.check("paper_decisions", "TABLE_ABSENT", 0, d.get("why"))
    mg = s.get("management")
    if mg is not None:
        by_role = mg.get("open_orders_by_role") or {}
        f.check("paper_orders", "MATCHED" if by_role else "NO_MATCH",
                sum(by_role.values()))
        f.add("paper_orders", "%s:open-orders" % acct, "paper_management",
              mg.get("open_management_orders"),
              "what Xavier manages on paper: %s open paper position(s), %s "
              "open paper management order(s) (standing protection, hedge, "
              "exit, reduce), %s open paper entry order(s), %s handoff(s) "
              "from Derek%s" % (
                  mg.get("open_positions"), mg.get("open_management_orders"),
                  mg.get("open_entry_orders"),
                  mg.get("handoffs") if mg.get("handoffs") is not None
                  else "unknown",
                  " -- nothing for Xavier to manage"
                  if mg.get("nothing_to_manage") else ""))
    rc = s.get("reconciliation")
    if rc is not None and rc.get("present") and "checks" in rc:
        f.add("paper_ledger", "%s#seq%s" % (acct, rc.get("last_seq")),
              "paper_reconciliation", bool(rc["reconciled"]),
              "paper ledger %s: %s entries (sequence %s to %s), %s "
              "INITIAL_FUNDING at sequence %s; sum of cash deltas %s against "
              "cash %s; sum of reserved deltas %s against reserved %s; "
              "available %s = cash minus reserved; running balance %s%s" % (
                  "RECONCILES" if rc["reconciled"] else "DOES NOT RECONCILE",
                  rc["entries_count"], rc["first_seq"], rc["last_seq"],
                  next((c["count"] for c in rc["checks"]
                        if c["check"] == "EXACTLY_ONE_INITIAL_FUNDING"), "?"),
                  rc.get("initial_funding_seq"),
                  _money(rc["checks"][0]["ledger_sum_usd"]),
                  _money(rc["checks"][0]["balance_usd"]),
                  _money(rc["checks"][1]["ledger_sum_usd"]),
                  _money(rc["checks"][1]["balance_usd"]),
                  _money(rc["checks"][2]["balance_usd"]),
                  "agrees" if rc["checks"][3]["ok"] else "DISAGREES",
                  "" if rc["reconciled"] else "; failed: %s"
                  % ", ".join(rc["failed_checks"])))
    rv = s.get("xavier_reviews_today")
    if rv is not None:
        f.add("paper_xavier_reviews", rv.get("newest_review_id")
              or "%s@%s" % (acct, rv["day"]), "paper_reviews_today",
              rv["total"], "Xavier's paper reviews today (%s, UTC): %s%s" % (
                  rv["day"], rv["total"], ("; newest at %s" % rv["newest_at"])
                  if rv.get("newest_at") else ""))
    return s


PD_FIELDS = (("verdict", None), ("policy_version", None),
             ("p_internal", "internal probability"),
             ("p_pinnacle", "Pinnacle probability"),
             ("p_blended", "blended probability"),
             ("gross_edge_pp", "edge (pp)"),
             ("threshold_gross_edge_pp", "policy minimum edge (pp)"),
             ("executable_price", "executable price"),
             ("qty", "contracts"),
             ("acquisition_cost_usd", "acquisition cost"),
             ("expected_gross_profit_usd", "expected profit before fees"),
             ("fees_usd", "fees"),
             ("net_expected_profit_usd", "expected profit after fees"))


async def _derek(conn, f: Facts, *, likes, context_ids, limit=2) -> list:
    if not await _regclass(conn, "derek_entry_decisions"):
        f.check("derek_entry_decisions", "TABLE_ABSENT")
        return []
    if likes or context_ids:
        rows = await conn.fetch(
            "SELECT decision_id, valuation_id, fixture, us_market_slug, side, "
            " decided_at, policy_version, verdict, refusal, pinnacle_p, "
            " model_p, gross_edge_pp, executable_price, qty, "
            " expected_gross_profit_usd, fees_usd, expected_net_profit_usd, "
            " evidence->'policy_decision' AS pd FROM derek_entry_decisions "
            " WHERE decision_id = ANY($2::text[]) OR fixture ILIKE "
            " ANY($1::text[]) OR us_market_slug ILIKE ANY($1::text[]) OR "
            " (evidence->'policy_decision'->'instrument')::text ILIKE "
            " ANY($1::text[]) ORDER BY (verdict = 'ENTER') DESC, decided_at "
            " DESC LIMIT $3", likes or [], context_ids or [], int(limit))
    else:
        rows = await conn.fetch(
            "SELECT decision_id, valuation_id, fixture, us_market_slug, side, "
            " decided_at, policy_version, verdict, refusal, pinnacle_p, "
            " model_p, gross_edge_pp, executable_price, qty, "
            " expected_gross_profit_usd, fees_usd, expected_net_profit_usd, "
            " evidence->'policy_decision' AS pd FROM derek_entry_decisions "
            " ORDER BY decided_at DESC LIMIT $1", int(limit))
    f.check("derek_entry_decisions", "MATCHED" if rows else "NO_MATCH",
            len(rows))
    ids = []
    for r in rows:
        d = _jsonable(dict(r))
        did = d["decision_id"]
        ids.append(did)
        pd = _obj(d.get("pd"), {}) or {}
        f.add("derek_entry_decisions", did, "decision",
              "%s %s" % (d.get("verdict"), d.get("fixture")),
              "Derek's entry decision %s on %s (%s) at %s: %s%s" % (
                  did, d.get("fixture") or d.get("us_market_slug"),
                  d.get("side"), d.get("decided_at"), d.get("verdict"),
                  (" -- refused: %s" % d["refusal"]) if d.get("refusal")
                  else ""))
        for k, label in PD_FIELDS:
            if label is None:
                continue
            v = pd.get(k)
            if v is None:
                v = {"p_pinnacle": d.get("pinnacle_p"),
                     "p_internal": d.get("model_p"),
                     "executable_price": d.get("executable_price"),
                     "qty": d.get("qty"),
                     "expected_gross_profit_usd":
                         d.get("expected_gross_profit_usd"),
                     "fees_usd": d.get("fees_usd"),
                     "net_expected_profit_usd":
                         d.get("expected_net_profit_usd")}.get(k)
            if v is None:
                continue
            txt = ("%s %s" % (label, _money(v)) if k.endswith("_usd")
                   else "%s %s" % (label, v))
            f.add("derek_entry_decisions", did, k, v, txt)
        if pd.get("inputs_are"):
            f.add("derek_entry_decisions", did, "inputs_are",
                  pd["inputs_are"], "what the inputs are: %s"
                  % str(pd["inputs_are"])[:200])
    return ids


async def _xavier(conn, f: Facts, *, likes, intents, context_ids,
                  limit=2) -> list:
    if not await _regclass(conn, "bettor_xavier_decisions"):
        f.check("bettor_xavier_decisions", "TABLE_ABSENT")
        return []
    if likes or intents or context_ids:
        rows = await conn.fetch(
            "SELECT xavier_decision_id, intent_id, account_id, "
            " us_market_slug, decided_at, chosen_action, "
            " execution_eligibility, alternatives, reasoning, "
            " expected_economics, residual_exposure FROM "
            " bettor_xavier_decisions WHERE intent_id = ANY($2::text[]) OR "
            " xavier_decision_id = ANY($3::text[]) OR us_market_slug ILIKE "
            " ANY($1::text[]) ORDER BY decided_at DESC LIMIT $4",
            likes or [], intents or [], context_ids or [], int(limit))
    else:
        rows = await conn.fetch(
            "SELECT xavier_decision_id, intent_id, account_id, "
            " us_market_slug, decided_at, chosen_action, "
            " execution_eligibility, alternatives, reasoning, "
            " expected_economics, residual_exposure FROM "
            " bettor_xavier_decisions ORDER BY decided_at DESC LIMIT $1",
            int(limit))
    f.check("bettor_xavier_decisions", "MATCHED" if rows else "NO_MATCH",
            len(rows))
    ids = []
    for r in rows:
        d = _jsonable(dict(r))
        xid = d["xavier_decision_id"]
        ids.append(xid)
        f.add("bettor_xavier_decisions", xid, "chosen_action",
              d.get("chosen_action"),
              "Xavier's decision %s on position %s (%s) at %s: chose %s; "
              "eligibility %s" % (xid, d.get("intent_id"),
                                  d.get("us_market_slug"), d.get("decided_at"),
                                  d.get("chosen_action") or "nothing",
                                  d.get("execution_eligibility")))
        why = (_obj(d.get("reasoning"), {}) or {}).get("why")
        if why:
            f.add("bettor_xavier_decisions", xid, "reasoning.why", why,
                  "his recorded reasoning: %s" % str(why)[:240])
        econ = _obj(d.get("expected_economics"), {}) or {}
        if econ.get("expected_net_usd") is not None:
            f.add("bettor_xavier_decisions", xid,
                  "expected_economics.expected_net_usd",
                  econ["expected_net_usd"], "expected net of the chosen "
                  "action %s" % _money(econ["expected_net_usd"]))
        res = _obj(d.get("residual_exposure"), {}) or {}
        if res.get("unpaired_qty") is not None:
            f.add("bettor_xavier_decisions", xid,
                  "residual_exposure.unpaired_qty", res["unpaired_qty"],
                  "unpaired contracts %s" % res["unpaired_qty"])
        for a in (_obj(d.get("alternatives"), []) or [])[:6]:
            if not isinstance(a, dict):
                continue
            act = a.get("action")
            if a.get("blocker"):
                f.add("bettor_xavier_decisions", xid, "alternative:%s" % act,
                      a["blocker"], "alternative %s blocked: %s"
                      % (act, a["blocker"]))
            elif a.get("value_usd") is not None:
                f.add("bettor_xavier_decisions", xid, "alternative:%s" % act,
                      a["value_usd"], "alternative %s valued %s"
                      % (act, _money(a["value_usd"])))
    return ids


async def _standing(conn, f: Facts, *, intents, xids, limit=2) -> None:
    if not (intents or xids):
        return
    if not await _regclass(conn, "bettor_standing_order_plans"):
        f.check("bettor_standing_order_plans", "TABLE_ABSENT")
        return
    rows = await conn.fetch(
        "SELECT plan_id, venue_slug, quantity, cost_price, floor_class, "
        " floor, created_at FROM bettor_standing_order_plans WHERE "
        " primary_intent_id = ANY($1::text[]) OR xavier_decision_id = "
        " ANY($2::text[]) ORDER BY created_at DESC LIMIT $3",
        intents or [], xids or [], int(limit))
    f.check("bettor_standing_order_plans", "MATCHED" if rows else "NO_MATCH",
            len(rows))
    for r in rows:
        d = _jsonable(dict(r))
        pid = d["plan_id"]
        f.add("bettor_standing_order_plans", pid, "plan",
              "%s x %s @ %s" % (d["venue_slug"], d["quantity"],
                                d["cost_price"]),
              "standing protective order %s: %s contracts of %s at %s; "
              "floor class %s" % (pid, d["quantity"], d["venue_slug"],
                                  d["cost_price"], d["floor_class"]))
        fl = _obj(d.get("floor"), {}) or {}
        for reg in (fl.get("regions") or [])[:6]:
            if reg.get("net_usd") is not None:
                f.add("bettor_standing_order_plans", pid,
                      "floor.regions:%s" % reg.get("region"),
                      reg["net_usd"], "payoff if %s: %s" % (
                          reg.get("region"), _money(reg["net_usd"])))


async def _audits(conn, f: Facts, *, ids, limit=1) -> None:
    if not await _regclass(conn, "audrey_audit_reports"):
        f.check("audrey_audit_reports", "TABLE_ABSENT")
        return
    if ids:
        rows = await conn.fetch(
            "SELECT report_id, version, audit_day, summary FROM "
            " audrey_audit_reports WHERE report::text LIKE ANY($1::text[]) "
            " ORDER BY audit_day DESC, version DESC LIMIT $2",
            ["%" + i + "%" for i in ids], int(limit))
    else:
        rows = await conn.fetch(
            "SELECT report_id, version, audit_day, summary FROM "
            " audrey_audit_reports ORDER BY audit_day DESC, version DESC "
            " LIMIT $1", int(limit))
    f.check("audrey_audit_reports", "MATCHED" if rows else "NO_MATCH",
            len(rows))
    for r in rows:
        f.add("audrey_audit_reports", "%s/v%s" % (r["report_id"],
                                                  r["version"]),
              "summary", r["summary"], "Audrey's audit %s (v%s): %s" % (
                  r["audit_day"], r["version"], str(r["summary"])[:300]))


async def _status(conn, f: Facts) -> None:
    if not await _regclass(conn, "agent_status"):
        f.check("agent_status", "TABLE_ABSENT")
        return
    rows = await conn.fetch(
        "SELECT agent_id, state, activity, last_heartbeat_at FROM "
        " agent_status ORDER BY agent_id")
    f.check("agent_status", "MATCHED" if rows else "NO_MATCH", len(rows))
    for r in rows:
        d = _jsonable(dict(r))
        f.add("agent_status", d["agent_id"], "state", d.get("state"),
              "%s is %s%s (last heartbeat %s)" % (
                  d["agent_id"], d.get("state"),
                  (" -- %s" % d["activity"]) if d.get("activity") else "",
                  d.get("last_heartbeat_at") or "never"))


def _context_ids(context: dict | None) -> list:
    c = context or {}
    out = []
    for k in ("position_id", "decision_id", "intent_id",
              "xavier_decision_id"):
        v = c.get(k)
        if isinstance(v, str) and v.strip() and len(v) <= 200:
            out.append(v.strip())
    return out


def wants_demonstration(context: dict | None) -> bool:
    c = context or {}
    return c.get("demonstration") is True or str(
        c.get("position_id") or "").upper().startswith(DEMO_LABEL)


async def gather(conn, *, question: str, context: dict | None = None,
                 now: float | None = None) -> dict:
    """The one fact list for this question (see the module docstring)."""
    subj = subject_of(question)
    if wants_demonstration(context):
        f = demonstration_facts()
        return {"subject": {"label": "Yankees", "terms": ["yankees", "nyy"]},
                "demonstration": True, "found": True, "scope": "POSITION",
                "facts": f.items, "checked": f.checked, "missing": f.missing,
                "paper": {"present": False, "why": "not read for the "
                          "DEMONSTRATION position"}}
    f = Facts()
    ctx_ids = _context_ids(context)
    likes = _likes(subj["terms"]) if subj else []
    scoped = bool(subj or ctx_ids)
    live = None
    if scoped:
        # a named position: the paper tables are searched for it
        paper = await _paper(conn, f, likes=likes, context_ids=ctx_ids)
    else:
        # the book: the paper experiment as it stands (balances, session,
        # today's decisions) -- never an unordered dump of paper rows
        live = await _paper_live(conn, f, now=now)
        paper = bool(live.get("present") and (live.get("account") or {})
                     .get("ok"))
    intents = await _positions(conn, f, likes=likes, context_ids=ctx_ids)
    dids = await _derek(conn, f, likes=likes, context_ids=ctx_ids)
    xids = await _xavier(conn, f, likes=likes, intents=intents,
                         context_ids=ctx_ids)
    await _standing(conn, f, intents=intents, xids=xids)
    if scoped:
        found = bool(f.items)
        if found:
            await _audits(conn, f, ids=dids + xids + intents)
    else:
        await _cash(conn, f)
        await _audits(conn, f, ids=[])
        await _status(conn, f)
        found = bool(f.items)
        if not any(x["source"] == "bettor_desk_account_state"
                   for x in f.items):
            f.miss("legacy desk account cash (no bettor_desk_account_state "
                   "row)")
        if not intents:
            f.miss("open funded positions (none recorded)")
    if scoped and found and not any(x["source"] == "bettor_funded_economics"
                                    for x in f.items):
        f.miss("a booked result (no settlement is recorded for this "
               "position, so nothing is realised)")
    tables = await paper_tables(conn)
    return {"subject": subj, "demonstration": False, "found": found,
            "scope": "POSITION" if scoped else "BOOK", "facts": f.items,
            "checked": f.checked, "missing": f.missing,
            "paper": {"present": bool(tables), "tables": tables,
                      "why": None if tables else PAPER_NOT_IN_BUILD,
                      "matched": paper, "live": live}}
