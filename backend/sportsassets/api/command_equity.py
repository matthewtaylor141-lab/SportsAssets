"""THE LIVE EQUITY WALL: /api/command/equity/* (GET only, COMMAND auth via
agents_core.require_read -> api.app.require_command). READ ONLY.

TWO BOOKS, NEVER SUMMED.
  paper   the fictional $500,000 PAPER EXPERIMENT (paper_acct_main): cash and
          realized P&L are sums over the paper ledger, open positions are
          marked from the paper path's own observed books -- exactly
          `bettor_paper_ledger.balances` (MARK_METHOD), the one function every
          paper read model uses.
  actual  the LEGACY MIRROR VALIDATION accounts (the old 1:1,000 execution
          mirror -- NOT the target Small Live system), kept PER VENUE and
          never added across venues:
            polymarket_us  the 1:1,000 execution-mirror account (its own
                           credential), from the worker's recorded venue
                           account snapshots (execmirror_snapshots) and the
                           control row (execmirror_control). No venue call is
                           made here; the worker's snapshot IS the cache.
            kalshi         the Kalshi small-live lane, from its recorded
                           read-only account reconciliations
                           (kalshi_account_reconciliations). No reconciliation
                           -> UNAVAILABLE with the exact reason.
  No key anywhere in a response combines the two books or the two venues.

  paper.sleeves   the paper book split by ECONOMIC SLEEVE (migration 223,
          bettor_paper_sleeves): INVESTMENT / TRAINING / BENCHMARK /
          UNCLASSIFIED, each with realized, unrealized, exposure, mark
          freshness and its last GENUINE mark update, and the accounting that
          reconciles starting cash + every sleeve's contribution to the
          account equity (P&L-only: one shared cash ledger).
  small_live_bettor   SMALL LIVE -- BETTOR ORIGINATED (bettor_originated_status):
          its status (NOT_CONFIGURED / SHADOW / READY_NOT_ACTIVATED / ACTIVE
          / DEGRADED / STOPPED) from stored evidence; legacy mirror orders
          never count.
  NO FAKE TICKER. A paper `last_change_at` is the ledger's last commit or
  the last GENUINE mark change (the mark PRICE moved between recorded
  observations) -- a book re-read at the same price is not a change.

EVERY ACCOUNT
    {status: OK | STALE | UNAVAILABLE, why, equity_usd, equity_treatment,
     cash_usd, available_usd, exposure{cost_basis_usd, marked_value_usd,
     unmarked_cost_basis_usd}, realized_pnl_usd, unrealized_pnl_usd,
     day_change{usd, pct, since, basis_*}, session_change{...},
     open_positions{count, marked, unmarked, stale_marks, rows},
     marks_as_of{oldest_at, newest_at, oldest_age_s, newest_age_s},
     source, source_at, last_change_at, stale_after_s, lane{...}}
  * UNREALIZED P&L COMES ONLY FROM REAL MARKS. A position with no mark is
    UNMARKED: it is listed as such, it contributes nothing to unrealized P&L,
    and the equity carries it at its cost basis -- said in
    `equity_treatment`, with the marked-only equity beside it. Never silent.
  * STALE means the source stopped updating (the paper runtime's heartbeat or
    marks; the mirror worker's account snapshot; the Kalshi reconciliation),
    with the age. UNAVAILABLE is never a zero.

THE POLLING CONTRACT. /equity/live answers an `ETag` (a hash of the content
WITHOUT the ages, which the page derives from the timestamps) and a `seq`
that rises whenever that content changes; `If-None-Match` -> 304. Built at
most once per LIVE_CACHE_S per process (single flight), every read inside
a READ ONLY transaction under a statement timeout. A page polling every ~5 s
therefore costs a header comparison until a genuine input changes.

/equity/curve?book=PAPER|ACTUAL&venue=polymarket_us|kalshi&window=1d|7d|30d
returns only the points where the equity changed in the recorded series
(paper_equity_snapshots, one per paper pass; execmirror_snapshots; Kalshi
reconciliations), each tagged with its cause (LEDGER: a fill / settlement
moved the ledger; MARK: only marks moved; ACCOUNT: the venue account read
changed). No interpolation, no synthetic point. No migration: those three
append-only series already exist.

This module imports no order, venue, execution or funded module and makes
no venue call.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import hashlib
import json
import math
import os
import time
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Request, Response

from .agents_core import _pool, require_read

router = APIRouter()
BASE = "/api/command/equity"
SCHEMA = "bt.equity.v1"

NY = ZoneInfo("America/New_York")
STATEMENT_TIMEOUT_MS = 4000
LIVE_CACHE_S = 2.0
CURVE_CACHE_S = 10.0
POLL_AFTER_S = 5.0
MAX_ROWS = 50                 # position rows per account in the live payload
MAX_POINTS = 1500             # curve points after de-duplication
CURVE_SCAN_LIMIT = 50000

PAPER_LABEL = "$500,000 PAPER EXPERIMENT"
ACTUAL_LABEL = ("LEGACY MIRROR VALIDATION (the old 1:1,000 execution mirror; "
                "not the target Small Live system)")
#: The paper runtime records one heartbeat and one equity snapshot per pass
#: (about one a minute); five missed passes is a stopped source.
PAPER_RUNTIME_STALE_AFTER_S = 300.0
#: The mirror worker snapshots the venue account every 60 s while RUNNING
#: (execmirror.SNAPSHOT_EVERY_S); three missed snapshots is stale.
PM_SNAPSHOT_STALE_AFTER_S = 180.0
#: A Kalshi reconciliation is a one-off read-only account read.
KALSHI_STALE_AFTER_S = 600.0
#: Names only -- the presence of the Kalshi credential in THIS service is
#: read as a boolean; no value is read into a response.
KALSHI_KEY_ENV = "KALSHI_API_KEY_ID"
KALSHI_PEM_ENVS = ("KALSHI_PRIVATE_KEY_PEM", "KALSHI_PRIVATE_KEY_PATH")

DISCLOSURE = (
    "Read-only observation. PAPER is fictional money with simulated "
    "execution, split by economic sleeve. The LEGACY MIRROR VALIDATION "
    "accounts are real money at a 1:1,000 scale, per venue (the old "
    "execution mirror, not the target Small Live system). SMALL LIVE -- "
    "BETTOR ORIGINATED is reported apart with its own status. Books are "
    "never summed and venues are never combined. This interface has no "
    "order authority.")


# ═════════════════════════════════════════════════════════════════════
# SMALL PURE HELPERS
# ═════════════════════════════════════════════════════════════════════

def num(v) -> float | None:
    """A finite float or None -- a missing amount is never a zero."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, dict):
        return num(v.get("value"))
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def r2(v) -> float | None:
    x = num(v)
    return None if x is None else round(x + 0.0, 2)


def r6(v) -> float | None:
    x = num(v)
    return None if x is None else round(x + 0.0, 6)


def epoch(v) -> float | None:
    if v is None:
        return None
    if hasattr(v, "timestamp"):
        try:
            return float(v.timestamp())
        except (ValueError, OSError, TypeError):
            return None
    return num(v)


def iso(t) -> str | None:
    t = num(t)
    if t is None:
        return None
    return _dt.datetime.fromtimestamp(t, _dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def jload(v):
    if isinstance(v, (bytes, str)):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


def age(now: float, t) -> float | None:
    t = num(t)
    return None if t is None else round(max(0.0, float(now) - t), 1)


def ny_day_start(now: float) -> float:
    """00:00 America/New_York of the session day containing `now`."""
    d = _dt.datetime.fromtimestamp(float(now), NY)
    return d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def human_age(s) -> str:
    s = num(s)
    if s is None:
        return "unknown age"
    if s < 90:
        return "%ds" % int(s)
    if s < 5400:
        return "%dm" % int(s // 60)
    if s < 172800:
        return "%.1fh" % (s / 3600.0)
    return "%.1fd" % (s / 86400.0)


def change(now_equity, basis_equity, *, since, basis_at, basis, why=None) -> dict:
    """{usd, pct, since, basis_equity_usd, basis_at, basis, why}; null with
    the reason when either side is unknown."""
    e, b = num(now_equity), num(basis_equity)
    if e is None or b is None:
        return {"usd": None, "pct": None, "since": iso(since),
                "basis_equity_usd": r2(b), "basis_at": iso(basis_at),
                "basis": basis,
                "why": why or ("equity unknown" if e is None
                               else "no recorded equity at the basis time")}
    return {"usd": r2(e - b), "pct": (round((e - b) / b * 100.0, 4)
                                      if b else None),
            "since": iso(since), "basis_equity_usd": r2(b),
            "basis_at": iso(basis_at), "basis": basis, "why": None}


def _unavailable(book: str, label: str, why: str, *, venue=None,
                 source=None, lane=None, currency="USD") -> dict:
    return {"book": book, "venue": venue, "label": label,
            "currency": currency, "status": "UNAVAILABLE", "why": why,
            "equity_usd": None, "equity_treatment": None,
            "equity_marked_only_usd": None, "cash_usd": None,
            "available_usd": None, "reserved_usd": None,
            "exposure": {"cost_basis_usd": None, "marked_value_usd": None,
                         "unmarked_cost_basis_usd": None},
            "realized_pnl_usd": None, "unrealized_pnl_usd": None,
            "day_change": None, "session_change": None,
            "open_positions": {"count": None, "marked": None,
                               "unmarked": None, "stale_marks": None,
                               "rows": []},
            "resting_orders": None,
            "marks_as_of": None, "source": source, "source_at": None,
            "source_age_s": None, "last_change_at": None,
            "stale_after_s": None, "lane": lane}


def _treatment(n_unmarked: int, unmarked_cost, marked_only) -> dict:
    if not n_unmarked:
        return {"rule": "ALL_OPEN_POSITIONS_MARKED", "unmarked_positions": 0,
                "unmarked_carried_at_cost_usd": 0.0,
                "equity_marked_only_usd": r2(marked_only),
                "text": "cash + marked value of every open position"}
    return {"rule": "UNMARKED_CARRIED_AT_COST",
            "unmarked_positions": n_unmarked,
            "unmarked_carried_at_cost_usd": r2(unmarked_cost),
            "equity_marked_only_usd": r2(marked_only),
            "text": ("cash + marked value + %d UNMARKED position(s) carried at "
                     "their cost basis ($%s); they add nothing to unrealized "
                     "P&L. Marked-only equity: $%s"
                     % (n_unmarked, format(r2(unmarked_cost) or 0, ",.2f"),
                        format(r2(marked_only) or 0, ",.2f")))}


# ═════════════════════════════════════════════════════════════════════
# PAPER (pure: bettor_paper_ledger.balances output -> account)
# ═════════════════════════════════════════════════════════════════════

def paper_account(bal: dict | None, *, now: float, error: str | None = None,
                  session: dict | None = None, day_basis: dict | None = None,
                  session_basis: dict | None = None,
                  stale_mark_after_s: float = 300.0,
                  genuine_mark_at: float | None = None,
                  sleeves: dict | None = None) -> dict:
    """`session`: {active, session_id, started_at, heartbeat_at, reason}.
    `day_basis` / `session_basis`: {equity_usd, at, basis} or {why}.
    `genuine_mark_at`: the last time an open position's mark PRICE changed
    (bettor_paper_sleeves.mark_changes); None = no genuine change measured.
    A book re-read at the same price is NOT a change, so it never moves
    `last_change_at`. `sleeves`: bettor_paper_sleeves.sleeve_book output."""
    src = ("paper_ledger + paper_fills + paper_settlements (cash, realized); "
           "paper_book_observations (marks, bettor_paper_ledger.MARK_METHOD)")
    if error:
        return _unavailable("PAPER", PAPER_LABEL, error, source=src,
                            currency="SIMULATED_USD")
    if not bal or not bal.get("ok"):
        return _unavailable("PAPER", PAPER_LABEL,
                            (bal or {}).get("refusal")
                            or "THE_PAPER_ACCOUNT_WAS_NOT_READ", source=src,
                            currency="SIMULATED_USD")
    cash = num(bal.get("cash_usd"))
    rows, marked_value, unmarked_cost, cost_basis, unreal = [], 0.0, 0.0, 0.0, 0.0
    n_marked = n_unmarked = n_stale = 0
    mark_ats = []
    for p in bal.get("open_positions") or []:
        m = p.get("mark") or {}
        cb = num(p.get("cost_basis_usd")) or 0.0
        cost_basis += cb
        price = num(m.get("price"))
        if price is None:
            n_unmarked += 1
            unmarked_cost += cb
            state = "UNMARKED"
        else:
            n_marked += 1
            marked_value += num(p.get("marked_value_usd")) or 0.0
            unreal += num(p.get("unrealized_pnl_usd")) or 0.0
            at = num(m.get("observed_at"))
            if at is not None:
                mark_ats.append(at)
            stale = bool(m.get("stale")) or (
                at is not None and now - at > stale_mark_after_s)
            n_stale += 1 if stale else 0
            state = "STALE_MARK" if stale else "MARKED"
        rows.append({
            "key": p.get("position_key"), "market": p.get("us_market_slug"),
            "side": p.get("holding_side"), "strategy": p.get("strategy"),
            "open_qty": r6(p.get("open_qty")), "cost_basis_usd": r2(cb),
            "mark_state": state, "mark_price": r6(price),
            "marked_value_usd": (None if price is None
                                 else r2(p.get("marked_value_usd"))),
            "unrealized_pnl_usd": (None if price is None
                                   else r2(p.get("unrealized_pnl_usd"))),
            "mark_observed_at": iso(m.get("observed_at")),
            "mark_source": m.get("source"),
            "why_unmarked": (m.get("why") if price is None else None)})
    rows.sort(key=lambda r: -(r["cost_basis_usd"] or 0.0))
    equity = None if cash is None else cash + marked_value + unmarked_cost
    marked_only = None if cash is None else cash + marked_value
    ledger_at = num(bal.get("last_updated_at"))
    newest = max(mark_ats) if mark_ats else None
    oldest = min(mark_ats) if mark_ats else None
    sess = session or {}
    hb = num(sess.get("heartbeat_at"))
    stale_why = []
    if not sess.get("active"):
        stale_why.append("the paper session is not running (%s)"
                         % (sess.get("reason") or "no active session"))
    if hb is None:
        stale_why.append("the paper runtime has never recorded a heartbeat")
    elif now - hb > PAPER_RUNTIME_STALE_AFTER_S:
        stale_why.append("the paper runtime's last heartbeat was %s ago"
                         % human_age(now - hb))
    if newest is not None and now - newest > stale_mark_after_s:
        stale_why.append("no position mark has refreshed for %s"
                         % human_age(now - newest))
    status = "STALE" if stale_why else "OK"
    src_at = max([t for t in (ledger_at, newest, hb) if t is not None],
                 default=None)
    # NO FAKE TICKER: only a ledger commit or a GENUINE mark change moves
    # the last change; the newest book RE-READ does not.
    genuine = num(genuine_mark_at)
    last_change = max([t for t in (ledger_at, genuine) if t is not None],
                      default=None)
    no_new_mark = bool(n_marked) and (
        genuine is None or now - genuine > stale_mark_after_s)
    start_cash = num(bal.get("starting_cash_usd"))
    db = day_basis or {}
    sb = session_basis or {}
    return {
        "book": "PAPER", "venue": None, "label": PAPER_LABEL,
        "currency": "SIMULATED_USD", "money": "FICTIONAL_USD_NOT_REAL_MONEY",
        "data_label": bal.get("data_label"),
        "status": status, "why": "; ".join(stale_why) or None,
        "equity_usd": r2(equity),
        "equity_treatment": _treatment(n_unmarked, unmarked_cost, marked_only),
        "equity_marked_only_usd": r2(marked_only),
        "cash_usd": r2(cash), "available_usd": r2(bal.get("available_usd")),
        "reserved_usd": r2(bal.get("reserved_usd")),
        "starting_cash_usd": r2(start_cash),
        "exposure": {"cost_basis_usd": r2(cost_basis),
                     "marked_value_usd": r2(marked_value),
                     "unmarked_cost_basis_usd": r2(unmarked_cost)},
        "realized_pnl_usd": r2(bal.get("realized_pnl_usd")),
        "unrealized_pnl_usd": r2(unreal),
        "unrealized_basis": ("marked positions only (%d of %d); UNMARKED "
                             "positions contribute nothing"
                             % (n_marked, n_marked + n_unmarked)),
        "fees_paid_usd": r2(bal.get("fees_paid_usd")),
        "day_change": change(equity, db.get("equity_usd"),
                             since=ny_day_start(now), basis_at=db.get("at"),
                             basis=db.get("basis"), why=db.get("why")),
        "session_change": change(equity, sb.get("equity_usd"),
                                 since=sb.get("at"), basis_at=sb.get("at"),
                                 basis=sb.get("basis"), why=sb.get("why")),
        "since_inception": change(equity, start_cash, since=None,
                                  basis_at=None,
                                  basis="starting cash of the fictional "
                                        "$500,000 account"),
        "open_positions": {"count": n_marked + n_unmarked,
                           "marked": n_marked, "unmarked": n_unmarked,
                           "stale_marks": n_stale,
                           "rows": rows[:MAX_ROWS],
                           "rows_truncated": len(rows) > MAX_ROWS},
        "resting_orders": None,
        "marks_as_of": {"oldest_at": iso(oldest), "newest_at": iso(newest),
                        "oldest_age_s": age(now, oldest),
                        "newest_age_s": age(now, newest),
                        "stale_mark_after_s": stale_mark_after_s,
                        "method": bal.get("mark_method"),
                        "newest_is": "the newest book RE-READ (not a price "
                                     "change)"},
        "last_genuine_mark_update_at": iso(genuine),
        "last_genuine_mark_update_age_s": age(now, genuine),
        "no_new_mark": no_new_mark,
        "no_new_mark_rule": ("no open position's mark PRICE changed in the "
                             "last %ds: the equity chart freezes at the last "
                             "genuine change (NO NEW MARK)"
                             % int(stale_mark_after_s)),
        "sleeves": sleeves if sleeves is not None else {
            "status": "UNAVAILABLE", "why": "SLEEVES_NOT_READ"},
        "source": src, "source_at": iso(src_at),
        "source_age_s": age(now, src_at),
        "ledger_last_committed_at": iso(ledger_at),
        "ledger_sequence": bal.get("last_sequence"),
        "last_change_at": iso(last_change),
        "stale_after_s": PAPER_RUNTIME_STALE_AFTER_S,
        "lane": {"state": ("NOT_RUNNING" if not sess.get("active") else
                           "RUNNING" if hb is not None
                           and now - hb <= PAPER_RUNTIME_STALE_AFTER_S
                           else "NO_HEARTBEAT"),
                 "session_id": sess.get("session_id"),
                 "session_started_at": iso(sess.get("started_at")),
                 "heartbeat_at": iso(hb), "heartbeat_age_s": age(now, hb),
                 "real_money_submission": "DISABLED"},
    }


# ═════════════════════════════════════════════════════════════════════
# POLYMARKET US SMALL LIVE (pure: control row + venue account snapshot)
# ═════════════════════════════════════════════════════════════════════

def lane_of(ctl: dict | None, *, connected_key: str = "account_fingerprint") -> dict:
    if not ctl:
        return {"state": "UNAVAILABLE", "why": "control row missing"}
    state = ("STOPPED" if ctl.get("stopped") else
             "ENABLED" if ctl.get("enabled") else "DISABLED")
    return {"state": state, "enabled": bool(ctl.get("enabled")),
            "stopped": bool(ctl.get("stopped")),
            "stop_done_at": iso(epoch(ctl.get("stop_done_at"))),
            "scale": num(ctl.get("scale")),
            "cap_usd_per_order": num(ctl.get("max_order_usd")),
            "cutover_at": iso(epoch(ctl.get("cutover_at"))),
            "control_changed_at": iso(epoch(ctl.get("updated_at"))),
            "connected": bool(ctl.get(connected_key))}


def pm_equity(snap: dict | None) -> dict:
    """One venue account snapshot -> its figures. Cash = currentBalance,
    available = buyingPower; each open position (netPosition != 0, not
    expired) is MARKED by the venue's cashValue at that read, or UNMARKED
    (carried at its cost) when the venue gave no cashValue."""
    if not snap:
        return {"ok": False}
    bal = jload(snap.get("balances")) or []
    first = bal[0] if bal and isinstance(bal[0], dict) else {}
    pos = jload(snap.get("positions")) or []
    cash = num(first.get("currentBalance"))
    rows, mv, uc, cb, unreal, realized = [], 0.0, 0.0, 0.0, 0.0, 0.0
    n_m = n_u = 0
    realized_known = False
    for p in pos if isinstance(pos, list) else []:
        if not isinstance(p, dict):
            continue
        rz = num(p.get("realized"))
        if rz is not None:
            realized += rz
            realized_known = True
        net = num(p.get("netPosition"))
        if not net or p.get("expired"):
            continue
        cost = num(p.get("cost"))
        val = num(p.get("cashValue"))
        cb += cost or 0.0
        if val is None:
            n_u += 1
            uc += cost or 0.0
        else:
            n_m += 1
            mv += val
            if cost is not None:
                unreal += val - cost
        rows.append({"market": p.get("slug"), "net_position": net,
                     "cost_basis_usd": r2(cost),
                     "mark_state": "MARKED" if val is not None else "UNMARKED",
                     "marked_value_usd": r2(val),
                     "unrealized_pnl_usd": (None if val is None or cost is None
                                            else r2(val - cost)),
                     "realized_pnl_usd": r2(rz),
                     "venue_update_time": p.get("updateTime")})
    rows.sort(key=lambda r: -(r["cost_basis_usd"] or 0.0))
    return {"ok": cash is not None, "cash": cash,
            "available": num(first.get("buyingPower")),
            "reservation": num(first.get("balanceReservation")),
            "unsettled": num(first.get("unsettledFunds")),
            "marked_value": mv, "unmarked_cost": uc, "cost_basis": cb,
            "unrealized": unreal, "realized": realized if realized_known else None,
            "positions_listed": len(pos) if isinstance(pos, list) else 0,
            "marked": n_m, "unmarked": n_u, "rows": rows,
            "equity": None if cash is None else cash + mv + uc,
            "marked_only": None if cash is None else cash + mv,
            "open_orders": snap.get("open_orders"),
            "reconciliation": jload(snap.get("reconciliation")) or {},
            "at": epoch(snap.get("at"))}


def pm_baseline(ctl: dict | None) -> dict:
    """The equity at the mirror's cutover, from the baseline the enable
    action recorded -- only when that baseline held no position."""
    base = jload((ctl or {}).get("baseline")) or {}
    if not base:
        return {"why": "no cutover baseline is recorded"}
    bal = base.get("balances") or []
    first = bal[0] if bal and isinstance(bal[0], dict) else {}
    cash = num(first.get("currentBalance"))
    held = {k: v for k, v in (base.get("positions_net") or {}).items()
            if num(v)}
    if cash is None:
        return {"why": "the cutover baseline carries no currentBalance"}
    if held:
        return {"why": ("the cutover baseline held %d position(s) without a "
                        "recorded value" % len(held))}
    return {"equity_usd": cash, "at": epoch(ctl.get("cutover_at"))
            or num(base.get("at")),
            "basis": "cutover baseline (venue balance when the mirror was "
                     "enabled; no position held)"}


def pm_account(ctl: dict | None, snap: dict | None, *, now: float,
               fills: int | None = None, last_fill_at=None,
               day_snap: dict | None = None, error: str | None = None) -> dict:
    src = ("execmirror_snapshots (the mirror worker's venue account read: "
           "currentBalance, buyingPower, positions cost/cashValue/realized) + "
           "execmirror_control + execmirror_fills")
    # THE LEGACY MIRROR, LABELLED FROM ITS REAL CONTROL ROW: it is not the
    # target Small Live system (bettor_originated_status); STOPPED only when the
    # row says so, RUNNING when it is enabled and not stopped.
    from .. import bettor_originated_status as SLT
    label = "Polymarket US · " + SLT.legacy_label(ctl)
    if error:
        return _unavailable("ACTUAL", label, error, venue="polymarket_us",
                            source=src)
    lane = lane_of(ctl)
    lane["account"] = ("LEGACY MIRROR account (the old execution mirror's own "
                       "credential), 1:%s" % (
                           int(lane["scale"]) if lane.get("scale") else "?"))
    lane["legacy_mirror"] = True
    lane["legacy_label"] = SLT.legacy_label(ctl)
    if not ctl:
        return _unavailable("ACTUAL", label,
                            "EXECMIRROR_CONTROL_ROW_MISSING (migration 192)",
                            venue="polymarket_us", source=src, lane=lane)
    if not snap:
        return _unavailable("ACTUAL", label,
                            "NO_ACCOUNT_SNAPSHOT: the mirror worker has never "
                            "recorded a venue account read",
                            venue="polymarket_us", source=src, lane=lane)
    e = pm_equity(snap)
    if not e["ok"]:
        out = _unavailable("ACTUAL", label,
                           "the latest account snapshot carries no "
                           "currentBalance", venue="polymarket_us",
                           source=src, lane=lane)
        out["source_at"] = iso(e["at"])
        return out
    a = age(now, e["at"])
    stale_why = None
    if a is not None and a > PM_SNAPSHOT_STALE_AFTER_S:
        if lane["state"] == "STOPPED":
            stale_why = ("account last read %s ago: the mirror is STOPPED%s, and "
                         "its worker reads the venue account only while running"
                         % (human_age(a), (" (stop completed %s)"
                                           % lane["stop_done_at"])
                            if lane.get("stop_done_at") else ""))
        elif lane["state"] == "DISABLED":
            stale_why = ("account last read %s ago: the mirror is DISABLED, and "
                         "its worker reads the venue account only while running"
                         % human_age(a))
        else:
            stale_why = ("account last read %s ago while the mirror is ENABLED: "
                         "the worker's 60 s account snapshot has stopped"
                         % human_age(a))
    if e["realized"] is not None:
        realized, rbasis = e["realized"], ("venue position records: sum of "
                                           "`realized` over listed positions")
    elif not fills:
        realized, rbasis = 0.0, ("no venue fill since the cutover "
                                 "(execmirror_fills is empty)")
    else:
        realized, rbasis = None, ("%d venue fill(s) recorded but the venue "
                                  "lists no position carrying realized P&L"
                                  % fills)
    unreal = e["unrealized"] if (e["marked"] or not e["unmarked"]) else None
    day = pm_equity(day_snap) if day_snap else {"ok": False}
    base = pm_baseline(ctl)
    return {
        "book": "ACTUAL", "venue": "polymarket_us", "label": label,
        "currency": "USD", "money": "REAL_MONEY",
        "status": "STALE" if stale_why else "OK", "why": stale_why,
        "equity_usd": r2(e["equity"]),
        "equity_treatment": _treatment(e["unmarked"], e["unmarked_cost"],
                                       e["marked_only"]),
        "equity_marked_only_usd": r2(e["marked_only"]),
        "cash_usd": r2(e["cash"]), "available_usd": r2(e["available"]),
        "reserved_usd": r2(e["reservation"]),
        "unsettled_usd": r2(e["unsettled"]),
        "exposure": {"cost_basis_usd": r2(e["cost_basis"]),
                     "marked_value_usd": r2(e["marked_value"]),
                     "unmarked_cost_basis_usd": r2(e["unmarked_cost"])},
        "realized_pnl_usd": r2(realized), "realized_basis": rbasis,
        "unrealized_pnl_usd": r2(unreal),
        "unrealized_basis": "venue cashValue - cost of marked open positions",
        "day_change": change(
            e["equity"], day["equity"] if day.get("ok") else None,
            since=ny_day_start(now), basis_at=day.get("at"),
            basis="last venue account snapshot before 00:00 America/New_York",
            why=None if day.get("ok") else
            "no venue account snapshot was recorded before today's session "
            "open (00:00 America/New_York)"),
        "session_change": change(e["equity"], base.get("equity_usd"),
                                 since=base.get("at"),
                                 basis_at=base.get("at"),
                                 basis=base.get("basis"), why=base.get("why")),
        "open_positions": {"count": e["marked"] + e["unmarked"],
                           "marked": e["marked"], "unmarked": e["unmarked"],
                           "stale_marks": (e["marked"] if stale_why else 0),
                           "rows": e["rows"][:MAX_ROWS],
                           "rows_truncated": len(e["rows"]) > MAX_ROWS},
        "resting_orders": e["open_orders"],
        "resting_orders_note": "RESTING orders at the venue are not fills",
        "fills_since_cutover": fills,
        "last_fill_at": iso(epoch(last_fill_at)),
        "reconciliation": {"reconciled": e["reconciliation"].get("reconciled"),
                           "differences": len(e["reconciliation"].get(
                               "differences") or {})},
        "marks_as_of": {"oldest_at": iso(e["at"]), "newest_at": iso(e["at"]),
                        "oldest_age_s": a, "newest_age_s": a,
                        "method": "venue cashValue at the account snapshot"},
        "source": src, "source_at": iso(e["at"]), "source_age_s": a,
        "last_change_at": iso(e["at"]),
        "stale_after_s": PM_SNAPSHOT_STALE_AFTER_S,
        "lane": lane,
    }


# ═════════════════════════════════════════════════════════════════════
# KALSHI SMALL LIVE (pure: control row + latest reconciliation)
# ═════════════════════════════════════════════════════════════════════

def kalshi_credential_in_this_service(env=None) -> bool:
    env = os.environ if env is None else env
    return bool(str(env.get(KALSHI_KEY_ENV) or "").strip()) and any(
        str(env.get(k) or "").strip() for k in KALSHI_PEM_ENVS)


def kalshi_account(ctl: dict | None, recon: dict | None, *, now: float,
                   schema: bool = True, credential_present: bool = False,
                   fills: int | None = None, error: str | None = None) -> dict:
    src = ("kalshi_account_reconciliations (read-only Kalshi account reads) + "
           "kalshi_smalllive_control + kalshi_live_fills")
    label = "Kalshi · LEGACY MIRROR LANE"
    if error:
        return _unavailable("ACTUAL", label, error, venue="kalshi", source=src)
    if not schema:
        return _unavailable("ACTUAL", label,
                            "MIGRATION_196_NOT_APPLIED: no Kalshi small-live "
                            "tables exist", venue="kalshi", source=src)
    lane = lane_of(ctl, connected_key="key_fingerprint")
    if ctl and not ctl.get("key_fingerprint"):
        lane["state"] = "NOT_CONNECTED"
    if not recon:
        why = ["NO_KALSHI_ACCOUNT: no read-only Kalshi account reconciliation "
               "has ever been recorded, so no Kalshi balance or position "
               "exists in these records"]
        if ctl is not None and not ctl.get("key_fingerprint"):
            why.append("the Kalshi small-live control holds no key")
        why.append("%s is %s in this API service"
                   % (KALSHI_KEY_ENV, "configured" if credential_present
                      else "not configured"))
        if fills:
            why.append("%d Kalshi fill(s) are recorded without an account "
                       "read" % fills)
        return _unavailable("ACTUAL", label, "; ".join(why), venue="kalshi",
                            source=src, lane=lane)
    at = epoch(recon.get("at"))
    if recon.get("verdict") == "UNREADABLE" or not recon.get("complete"):
        out = _unavailable("ACTUAL", label,
                           "the latest Kalshi account read was UNREADABLE: %s"
                           % json.dumps(jload(recon.get("errors")) or {},
                                        sort_keys=True)[:200],
                           venue="kalshi", source=src, lane=lane)
        out["source_at"] = iso(at)
        return out
    cash = num(recon.get("balance_usd"))
    pos = jload(recon.get("positions")) or []
    rows, cost = [], 0.0
    for p in pos:
        c = num(p.get("exposure_usd"))
        cost += c or 0.0
        rows.append({"market": p.get("ticker"),
                     "net_position": num(p.get("position")),
                     "cost_basis_usd": r2(c), "mark_state": "UNMARKED",
                     "marked_value_usd": None, "unrealized_pnl_usd": None,
                     "why_unmarked": "a Kalshi account reconciliation "
                                     "records exposure, not a mark"})
    a = age(now, at)
    stale = a is not None and a > KALSHI_STALE_AFTER_S
    resting = jload(recon.get("resting_orders")) or []
    return {
        "book": "ACTUAL", "venue": "kalshi", "label": label,
        "currency": "USD", "money": "REAL_MONEY",
        "status": "STALE" if stale else "OK",
        "why": (("the latest Kalshi account read is %s old (reads are one-off "
                 "reconciliations, not a stream)" % human_age(a))
                if stale else None),
        "equity_usd": r2(None if cash is None else cash + cost),
        "equity_treatment": _treatment(len(rows), cost, cash),
        "equity_marked_only_usd": r2(cash),
        "cash_usd": r2(cash), "available_usd": None,
        "available_why": "a Kalshi reconciliation does not record buying power",
        "reserved_usd": None,
        "exposure": {"cost_basis_usd": r2(cost), "marked_value_usd": 0.0
                     if not rows else None,
                     "unmarked_cost_basis_usd": r2(cost)},
        "realized_pnl_usd": None,
        "realized_basis": "a Kalshi reconciliation does not record realized P&L",
        "unrealized_pnl_usd": 0.0 if not rows else None,
        "unrealized_basis": ("no open position" if not rows else
                             "no Kalshi position carries a mark here"),
        "day_change": None, "session_change": None,
        "open_positions": {"count": len(rows), "marked": 0,
                           "unmarked": len(rows), "stale_marks": 0,
                           "rows": rows[:MAX_ROWS]},
        "resting_orders": len(resting),
        "resting_orders_note": "RESTING orders at the venue are not fills",
        "fills_since_cutover": fills,
        "verdict": recon.get("verdict"), "kalshi_env": recon.get("kalshi_env"),
        "marks_as_of": None,
        "source": src, "source_at": iso(at), "source_age_s": a,
        "last_change_at": iso(at), "stale_after_s": KALSHI_STALE_AFTER_S,
        "lane": lane,
    }


# ═════════════════════════════════════════════════════════════════════
# THE ENVELOPE, ETag AND seq
# ═════════════════════════════════════════════════════════════════════

VOLATILE = ("computed_at", "computed_at_iso", "seq", "etag", "as_of")


def _strip(v):
    if isinstance(v, dict):
        return {k: _strip(x) for k, x in v.items()
                if k not in VOLATILE and not k.endswith("age_s")}
    if isinstance(v, list):
        return [_strip(x) for x in v]
    return v


def content_etag(payload: dict) -> str:
    """A hash of everything but the clock: ages are derived on the page
    from the timestamps, so a 304 means no genuine input changed (a status
    that flips to STALE does change the hash)."""
    blob = json.dumps(_strip(payload), sort_keys=True, default=str,
                      separators=(",", ":"))
    return 'W/"eq-%s"' % hashlib.sha256(blob.encode()).hexdigest()[:24]


_SEQ = {"etag": None, "seq": 0, "boot": int(time.time())}


def stamp(payload: dict, *, now: float) -> dict:
    tag = content_etag(payload)
    if tag != _SEQ["etag"]:
        _SEQ["etag"] = tag
        _SEQ["seq"] += 1
    payload.update(etag=tag, seq=_SEQ["seq"], seq_epoch=_SEQ["boot"],
                   computed_at=round(float(now), 3), computed_at_iso=iso(now))
    return payload


def envelope(paper: dict, pm: dict, kalshi: dict,
             small_live: dict | None = None) -> dict:
    return {
        "schema": SCHEMA, "read_only": True, "authority": "NONE",
        "poll_after_s": POLL_AFTER_S,
        "books_summed": False, "venues_summed": False,
        "paper": paper,
        "actual": {"label": ACTUAL_LABEL, "venues_summed": False,
                   "scale": "1:1,000", "legacy_mirror": True,
                   "is_target_small_live": False,
                   "venues": {"polymarket_us": pm, "kalshi": kalshi}},
        "small_live_bettor": small_live if small_live is not None else {
            "title": "SMALL LIVE — BETTOR ORIGINATED", "status": None,
            "why": "SMALL_LIVE_TRUTH_NOT_READ"},
        "disclosure": DISCLOSURE,
    }


# ═════════════════════════════════════════════════════════════════════
# READS (inside a READ ONLY transaction with a statement timeout)
# ═════════════════════════════════════════════════════════════════════

async def _readonly(conn):
    """A READ ONLY transaction with a statement timeout. (Inside a caller's
    open transaction -- only a test harness does that -- asyncpg can open
    only a savepoint, which cannot change the access mode; the statement
    timeout still applies.)"""
    tr = conn.transaction() if conn.is_in_transaction() else \
        conn.transaction(readonly=True)
    await tr.start()
    await conn.execute("SET LOCAL statement_timeout = %d"
                       % int(STATEMENT_TIMEOUT_MS))
    return tr


async def _exists(conn, table: str) -> bool:
    return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL",
                                    table))


async def read_paper(conn, *, now: float, account_id: str | None = None) -> dict:
    from .. import bettor_paper_ledger as L
    acct = account_id or L.ACCOUNT_ID
    try:
        if not await _exists(conn, "paper_ledger"):
            return paper_account(None, now=now,
                                 error="MIGRATION_171_IS_NOT_APPLIED")
        bal = await L.balances(conn, acct, now=now)
        sess = await conn.fetchrow(
            "SELECT s.session_id, s.started_at, h.heartbeat_at "
            "  FROM paper_sessions s "
            "  LEFT JOIN paper_session_health h ON h.session_id = s.session_id"
            " WHERE s.account_id = $1 AND s.status = 'ACTIVE' "
            " ORDER BY s.started_at DESC LIMIT 1", acct)
        # THE RUNNING PROOF IS THE HEARTBEAT, not a flag: the session row
        # says a session is ACTIVE, the runtime's heartbeat says it is
        # actually passing (judged in paper_account).
        session = {"active": sess is not None,
                   "session_id": sess["session_id"] if sess else None,
                   "started_at": epoch(sess["started_at"]) if sess else None,
                   "heartbeat_at": epoch(sess["heartbeat_at"]) if sess else None,
                   "reason": None if sess is not None
                   else "NO_ACTIVE_PAPER_SESSION"}
        day_start = ny_day_start(now)
        snap_ok = await _exists(conn, "paper_equity_snapshots")
        day_basis = {"why": "no paper equity snapshot table"}
        session_basis = {"why": "no active paper session"}
        if snap_ok:
            d = await conn.fetchrow(
                "SELECT at, equity_usd FROM paper_equity_snapshots "
                " WHERE account_id = $1 AND at < to_timestamp($2) "
                "   AND equity_usd IS NOT NULL ORDER BY at DESC LIMIT 1",
                acct, day_start)
            created = epoch(await conn.fetchval(
                "SELECT created_at FROM paper_accounts WHERE account_id = $1",
                acct))
            if d is not None:
                day_basis = {"equity_usd": num(d["equity_usd"]),
                             "at": epoch(d["at"]),
                             "basis": "last recorded paper equity (all marks) "
                                      "before 00:00 America/New_York"}
            elif created is not None and created >= day_start:
                day_basis = {"equity_usd": num(bal.get("starting_cash_usd")),
                             "at": created,
                             "basis": "starting cash: the account opened today"}
            else:
                day_basis = {"why": "no complete paper equity snapshot before "
                                    "today's session open (00:00 "
                                    "America/New_York)"}
            if sess is not None:
                s0 = await conn.fetchrow(
                    "SELECT at, equity_usd FROM paper_equity_snapshots "
                    " WHERE session_id = $1 AND equity_usd IS NOT NULL "
                    " ORDER BY at LIMIT 1", sess["session_id"])
                session_basis = ({"equity_usd": num(s0["equity_usd"]),
                                  "at": epoch(s0["at"]),
                                  "basis": "first recorded equity of paper "
                                           "session %s" % sess["session_id"]}
                                 if s0 is not None else
                                 {"why": "the active session has recorded no "
                                         "complete equity snapshot yet"})
        sleeves, genuine = await read_sleeves(conn, acct, now=now, bal=bal)
        out = paper_account(bal, now=now, session=session,
                            day_basis=day_basis, session_basis=session_basis,
                            stale_mark_after_s=float(L.MARK_STALE_AFTER_S),
                            genuine_mark_at=genuine, sleeves=sleeves)
        out["management"] = await read_management(conn, acct, now=now,
                                                  bal=bal, paper=out)
        out["freshness"] = await read_freshness(conn, acct, now=now)
        return out
    except Exception as exc:                                    # noqa: BLE001
        return paper_account(None, now=now, error="PAPER_READ_FAILED: %s: %s"
                             % (type(exc).__name__, str(exc)[:160]))


async def read_management(conn, acct: str, *, now: float, bal: dict,
                          paper: dict) -> dict:
    """THE MANAGEMENT EPOCH (bettor_paper_epoch): the PAPER book re-based to
    $500,000 at 2026-10-05 00:00 America/New_York, with the full ledger
    history beside it. A failed read is UNAVAILABLE with its reason and never
    breaks the account figures."""
    from .. import bettor_paper_epoch as EP
    try:
        async with conn.transaction():
            m = await EP.read(conn, acct, bal=bal, now=now)
    except Exception as exc:                                    # noqa: BLE001
        return {"status": "UNAVAILABLE", "epoch_id": EP.EPOCH_ID,
                "label": EP.LABEL,
                "why": "MANAGEMENT_EPOCH_READ_FAILED: %s: %s"
                       % (type(exc).__name__, str(exc)[:160])}
    hist = m.get("pre_management_history")
    if isinstance(hist, dict):
        hist.update(ledger_equity_usd=paper.get("equity_usd"),
                    since_funding=paper.get("since_inception"),
                    ledger_unrealized_pnl_usd=paper.get("unrealized_pnl_usd"))
    return m


async def read_freshness(conn, acct: str, *, now: float) -> dict:
    """EVERY OPEN PAPER POSITION IN EXACTLY ONE MARK-FRESHNESS CLASS
    (bettor_paper_freshness): the counts with their rules, the fresh rate and
    the stale-management rate over the MARKABLE positions, by strategy, and
    the latest held-mark refresh run. A failed read is UNAVAILABLE with its
    reason -- never zero counts. The per-position rows are served by
    GET /api/command/paper/freshness."""
    from .. import bettor_paper_freshness as PMF
    try:
        got = await PMF.read(conn, acct, now=now, rows_limit=0)
    except Exception as exc:                                    # noqa: BLE001
        return {"status": "UNAVAILABLE", "counts": None,
                "why": "FRESHNESS_READ_FAILED: %s: %s"
                       % (type(exc).__name__, str(exc)[:160])}
    got.pop("positions", None)
    got.pop("positions_truncated", None)
    got["positions_route"] = "/api/command/paper/freshness"
    return got


async def read_sleeves(conn, acct: str, *, now: float, bal: dict) -> tuple:
    """(sleeves, last genuine mark change) -- a failed sleeve read is
    UNAVAILABLE with its reason and never breaks the account figures."""
    from .. import bettor_paper_sleeves as SL
    try:
        # its own savepoint: a failed sleeve read never aborts the
        # transaction the other accounts are read in
        async with conn.transaction():
            sb = await SL.sleeve_book(conn, acct, now=now, bal=bal)
    except Exception as exc:                                    # noqa: BLE001
        return ({"status": "UNAVAILABLE", "why": "SLEEVE_READ_FAILED: %s: %s"
                 % (type(exc).__name__, str(exc)[:160])}, None)
    return sb, sb.get("last_genuine_mark_update_at")


async def read_small_live(conn, *, now: float) -> dict:
    from .. import bettor_originated_status as SLT
    try:
        got = await SLT.read_isolated(conn, now=now)
    except Exception as exc:                                    # noqa: BLE001
        return {"title": SLT.TITLE, "status": None,
                "why": "SMALL_LIVE_READ_FAILED: %s: %s"
                       % (type(exc).__name__, str(exc)[:160])}
    small = dict(got["small_live"])
    small["legacy_mirror"] = got["legacy_mirror"]
    small["archer_funnel"] = got["archer_funnel"]
    return small


async def read_pm(conn, *, now: float) -> dict:
    try:
        if not await _exists(conn, "execmirror_control"):
            return pm_account(None, None, now=now,
                              error="MIGRATION_192_NOT_APPLIED: no execution "
                                    "mirror tables exist")
        ctl = await conn.fetchrow(
            "SELECT enabled, stopped, stop_done_at, scale, max_order_usd, "
            "       cutover_at, baseline, updated_at, "
            "       (account_fingerprint IS NOT NULL) AS account_fingerprint "
            "  FROM execmirror_control WHERE id = 1")
        snap = await conn.fetchrow(
            "SELECT at, balances, positions, open_orders, reconciliation "
            "  FROM execmirror_snapshots ORDER BY at DESC LIMIT 1")
        day = await conn.fetchrow(
            "SELECT at, balances, positions, open_orders, reconciliation "
            "  FROM execmirror_snapshots WHERE at < to_timestamp($1) "
            " ORDER BY at DESC LIMIT 1", ny_day_start(now))
        f = await conn.fetchrow(
            "SELECT count(*) AS n, max(observed_at) AS last_at "
            "  FROM execmirror_fills")
        return pm_account(dict(ctl) if ctl else None,
                          dict(snap) if snap else None, now=now,
                          fills=int(f["n"]), last_fill_at=f["last_at"],
                          day_snap=dict(day) if day else None)
    except Exception as exc:                                    # noqa: BLE001
        return pm_account(None, None, now=now, error="POLYMARKET_US_READ_FAILED:"
                          " %s: %s" % (type(exc).__name__, str(exc)[:160]))


async def read_kalshi(conn, *, now: float) -> dict:
    try:
        if not await _exists(conn, "kalshi_smalllive_control"):
            return kalshi_account(None, None, now=now, schema=False)
        ctl = await conn.fetchrow(
            "SELECT enabled, stopped, scale, max_order_usd, cutover_at, "
            "       updated_at, kalshi_env, "
            "       (key_fingerprint IS NOT NULL) AS key_fingerprint "
            "  FROM kalshi_smalllive_control WHERE id = 1")
        recon = await conn.fetchrow(
            "SELECT at, kalshi_env, verdict, complete, balance_usd, positions, "
            "       resting_orders, errors "
            "  FROM kalshi_account_reconciliations ORDER BY at DESC LIMIT 1")
        fills = await conn.fetchval("SELECT count(*) FROM kalshi_live_fills") \
            if await _exists(conn, "kalshi_live_fills") else None
        return kalshi_account(dict(ctl) if ctl else None,
                              dict(recon) if recon else None, now=now,
                              credential_present=kalshi_credential_in_this_service(),
                              fills=None if fills is None else int(fills))
    except Exception as exc:                                    # noqa: BLE001
        return kalshi_account(None, None, now=now, error="KALSHI_READ_FAILED: "
                              "%s: %s" % (type(exc).__name__, str(exc)[:160]))


async def live_payload(conn, *, now: float | None = None) -> dict:
    now = time.time() if now is None else float(now)
    tr = await _readonly(conn)
    try:
        paper = await read_paper(conn, now=now)
        pm = await read_pm(conn, now=now)
        kalshi = await read_kalshi(conn, now=now)
        small = await read_small_live(conn, now=now)
    finally:
        await tr.rollback()
    return stamp(envelope(paper, pm, kalshi, small), now=now)


# ═════════════════════════════════════════════════════════════════════
# THE CURVE: only the points where a genuine input changed
# ═════════════════════════════════════════════════════════════════════

WINDOWS = {"1d": 86400.0, "7d": 7 * 86400.0, "30d": 30 * 86400.0}


def keep_changes(points: list) -> list:
    """[(t, v, cause)] -> the first point and every point whose value
    differs from the one before it (a null is a gap, kept once)."""
    out, prev = [], object()
    for t, v, cause in points:
        vv = None if v is None else round(float(v), 2)
        if vv != prev:
            out.append({"t": round(float(t), 3), "v": vv,
                        "cause": "GAP" if vv is None else cause})
            prev = vv
    return out


def thin(points: list, max_n: int = MAX_POINTS) -> tuple:
    """At most `max_n` points: the last point of each equal time bucket,
    always the first and last. Returns (points, thinned?)."""
    if len(points) <= max_n:
        return points, False
    t0, t1 = points[0]["t"], points[-1]["t"]
    width = max((t1 - t0) / float(max_n - 2), 1e-9)
    buckets: dict = {}
    for p in points[1:-1]:
        buckets[int((p["t"] - t0) // width)] = p
    return [points[0]] + [buckets[k] for k in sorted(buckets)] + [
        points[-1]], True


async def paper_curve(conn, *, since: float, until: float,
                      account_id: str | None = None) -> dict:
    from .. import bettor_paper_ledger as L
    acct = account_id or L.ACCOUNT_ID
    if not await _exists(conn, "paper_equity_snapshots"):
        return {"status": "UNAVAILABLE", "why": "MIGRATION_172_IS_NOT_APPLIED",
                "points": []}
    anchor = await conn.fetchrow(
        "SELECT extract(epoch FROM at)::float8 AS t, equity_usd, last_sequence"
        "  FROM paper_equity_snapshots WHERE account_id = $1 "
        "   AND at < to_timestamp($2) ORDER BY at DESC LIMIT 1", acct, since)
    rows = await conn.fetch(
        "WITH s AS ("
        "  SELECT at, equity_usd, last_sequence, "
        "         lag(equity_usd) OVER w AS prev_e, "
        "         row_number() OVER w AS rn "
        "    FROM paper_equity_snapshots "
        "   WHERE account_id = $1 AND at >= to_timestamp($2) "
        "     AND at <= to_timestamp($3) WINDOW w AS (ORDER BY at)) "
        "SELECT extract(epoch FROM at)::float8 AS t, equity_usd, "
        "       last_sequence "
        "  FROM s WHERE rn = 1 OR equity_usd IS DISTINCT FROM prev_e "
        " ORDER BY at LIMIT $4", acct, since, until, CURVE_SCAN_LIMIT)
    scanned = await conn.fetchval(
        "SELECT count(*) FROM paper_equity_snapshots WHERE account_id = $1 "
        "   AND at >= to_timestamp($2) AND at <= to_timestamp($3)",
        acct, since, until)
    raw = []
    if anchor is not None:
        raw.append((since, num(anchor["equity_usd"]), "WINDOW_OPEN"))
    # CAUSE, against the previous KEPT point: the ledger sequence moved (a
    # fill, a sale, a settlement) or only the marks did.
    prev_seq = anchor["last_sequence"] if anchor is not None else None
    for r in rows:
        cause = ("FIRST_RECORD" if prev_seq is None else
                 "LEDGER" if r["last_sequence"] != prev_seq else "MARK")
        raw.append((r["t"], num(r["equity_usd"]), cause))
        prev_seq = r["last_sequence"]
    pts, thinned = thin(keep_changes(raw))
    return {"status": "OK" if pts else "EMPTY",
            "why": None if pts else "no paper equity snapshot in this window",
            "points": pts, "scanned": int(scanned or 0), "thinned": thinned,
            "basis": ("paper_equity_snapshots: the paper runtime's recorded "
                      "equity once per pass (cash + marked value, "
                      "bettor_paper_ledger.balances). Only changes are "
                      "returned; a pass whose marks were incomplete has no "
                      "stated equity and is a GAP, never a zero.")}


async def pm_curve(conn, *, since: float, until: float) -> dict:
    if not await _exists(conn, "execmirror_snapshots"):
        return {"status": "UNAVAILABLE", "why": "MIGRATION_192_NOT_APPLIED",
                "points": []}
    anchor = await conn.fetchrow(
        "SELECT extract(epoch FROM at)::float8 AS t, balances, positions "
        "  FROM execmirror_snapshots WHERE at < to_timestamp($1) "
        " ORDER BY at DESC LIMIT 1", since)
    rows = await conn.fetch(
        "WITH s AS ("
        "  SELECT at, balances, positions, "
        "         md5(balances::text || positions::text) AS h, "
        "         lag(md5(balances::text || positions::text)) OVER w AS ph, "
        "         row_number() OVER w AS rn "
        "    FROM execmirror_snapshots "
        "   WHERE at >= to_timestamp($1) AND at <= to_timestamp($2) "
        "  WINDOW w AS (ORDER BY at)) "
        "SELECT extract(epoch FROM at)::float8 AS t, balances, positions "
        "  FROM s WHERE rn = 1 OR h IS DISTINCT FROM ph ORDER BY at LIMIT $3",
        since, until, CURVE_SCAN_LIMIT)
    scanned = await conn.fetchval(
        "SELECT count(*) FROM execmirror_snapshots "
        " WHERE at >= to_timestamp($1) AND at <= to_timestamp($2)",
        since, until)
    raw = []
    if anchor is not None:
        raw.append((since, pm_equity(dict(anchor))["equity"], "WINDOW_OPEN"))
    for r in rows:
        raw.append((r["t"], pm_equity(dict(r))["equity"], "ACCOUNT"))
    pts, thinned = thin(keep_changes(raw))
    return {"status": "OK" if pts else "EMPTY",
            "why": None if pts else
            "no venue account snapshot of the mirror account in this window",
            "points": pts, "scanned": int(scanned or 0), "thinned": thinned,
            "basis": ("execmirror_snapshots: the mirror worker's venue "
                      "account reads (currentBalance + cashValue of marked "
                      "open positions + cost of unmarked ones). Only changes "
                      "are returned. The worker reads the account only while "
                      "the mirror runs, so a STOPPED mirror has no points "
                      "after its last read.")}


async def kalshi_curve(conn, *, since: float, until: float) -> dict:
    if not await _exists(conn, "kalshi_account_reconciliations"):
        return {"status": "UNAVAILABLE", "why": "MIGRATION_196_NOT_APPLIED",
                "points": []}
    rows = await conn.fetch(
        "SELECT extract(epoch FROM at)::float8 AS t, balance_usd, positions, "
        "       verdict FROM kalshi_account_reconciliations "
        " WHERE at <= to_timestamp($2) AND (at >= to_timestamp($1) OR "
        "       reconciliation_id = (SELECT max(reconciliation_id) FROM "
        "       kalshi_account_reconciliations WHERE at < to_timestamp($1)))"
        " ORDER BY at LIMIT $3", since, until, CURVE_SCAN_LIMIT)
    if not rows:
        total = await conn.fetchval(
            "SELECT count(*) FROM kalshi_account_reconciliations")
        return {"status": "UNAVAILABLE",
                "why": ("NO_KALSHI_ACCOUNT: no read-only Kalshi account "
                        "reconciliation has ever been recorded"
                        if not total else
                        "no Kalshi account read in this window"),
                "points": [], "scanned": 0, "thinned": False}
    raw = []
    for r in rows:
        if r["verdict"] == "UNREADABLE":
            raw.append((max(r["t"], since), None, "UNREADABLE"))
            continue
        cash = num(r["balance_usd"])
        cost = sum(num(p.get("exposure_usd")) or 0.0
                   for p in (jload(r["positions"]) or []))
        raw.append((max(r["t"], since), None if cash is None else cash + cost,
                    "ACCOUNT"))
    pts, thinned = thin(keep_changes(raw))
    return {"status": "OK", "why": None, "points": pts,
            "scanned": len(rows), "thinned": thinned,
            "basis": ("kalshi_account_reconciliations: each read-only account "
                      "read (balance + open exposure at cost). Reads are "
                      "one-off, so the line holds between reads.")}


async def curve_payload(conn, *, book: str, venue: str | None, window: str,
                        now: float | None = None) -> dict:
    now = time.time() if now is None else float(now)
    since, until = now - WINDOWS[window], now
    tr = await _readonly(conn)
    try:
        try:
            if book == "PAPER":
                got = await paper_curve(conn, since=since, until=until)
                label = PAPER_LABEL
            elif venue == "kalshi":
                got = await kalshi_curve(conn, since=since, until=until)
                label = ACTUAL_LABEL + " · Kalshi"
            else:
                got = await pm_curve(conn, since=since, until=until)
                label = ACTUAL_LABEL + " · Polymarket US"
        except Exception as exc:                                # noqa: BLE001
            got = {"status": "UNAVAILABLE", "points": [],
                   "why": "CURVE_READ_FAILED: %s: %s"
                          % (type(exc).__name__, str(exc)[:160])}
    finally:
        await tr.rollback()
    out = {"schema": SCHEMA, "read_only": True, "book": book,
           "venue": None if book == "PAPER" else venue, "label": label,
           "window": window, "since": iso(since), "until": iso(until),
           "books_summed": False, "interpolated": False,
           "synthetic_points": 0}
    out.update(got)
    # the window-open anchor moves with the clock; the hash ignores its time
    pts = [dict(p, t=None) if p.get("cause") == "WINDOW_OPEN" else p
           for p in out.get("points") or []]
    tag = content_etag(dict({k: v for k, v in out.items()
                             if k not in ("since", "until")}, points=pts))
    out.update(etag=tag, computed_at=round(now, 3), computed_at_iso=iso(now))
    return out


# ═════════════════════════════════════════════════════════════════════
# ROUTES (GET only)
# ═════════════════════════════════════════════════════════════════════

_LIVE = {"at": 0.0, "payload": None}
_LIVE_LOCK = asyncio.Lock()
_CURVES: dict = {}


def _not_modified(request: Request, tag: str) -> bool:
    got = request.headers.get("if-none-match") or ""
    return bool(tag) and tag in [t.strip() for t in got.split(",")]


def _headers(response: Response, tag: str) -> None:
    response.headers["ETag"] = tag
    response.headers["Cache-Control"] = "private, no-cache"


async def _cached_live() -> dict:
    async with _LIVE_LOCK:
        mono = time.monotonic()
        if _LIVE["payload"] is not None and mono - _LIVE["at"] < LIVE_CACHE_S:
            return _LIVE["payload"]
        pool = await _pool()
        async with pool.acquire() as conn:
            payload = await live_payload(conn)
        _LIVE.update(at=mono, payload=payload)
        return payload


@router.get(BASE + "/live", dependencies=[Depends(require_read)])
async def equity_live(request: Request, response: Response):
    payload = await _cached_live()
    tag = payload["etag"]
    if _not_modified(request, tag):
        return Response(status_code=304, headers={
            "ETag": tag, "Cache-Control": "private, no-cache",
            "X-Equity-Seq": str(payload["seq"])})
    _headers(response, tag)
    response.headers["X-Equity-Seq"] = str(payload["seq"])
    return payload


@router.get(BASE + "/curve", dependencies=[Depends(require_read)])
async def equity_curve(request: Request, response: Response,
                       book: str = Query("PAPER", pattern="^(PAPER|ACTUAL)$"),
                       venue: str | None = Query(
                           None, pattern="^(polymarket_us|kalshi)$"),
                       window: str = Query("1d", pattern="^(1d|7d|30d)$")):
    if book == "ACTUAL" and venue is None:
        venue = "polymarket_us"
    key = (book, venue if book == "ACTUAL" else None, window)
    hit = _CURVES.get(key)
    mono = time.monotonic()
    if hit and mono - hit[0] < CURVE_CACHE_S:
        out = hit[1]
    else:
        pool = await _pool()
        async with pool.acquire() as conn:
            out = await curve_payload(conn, book=book, venue=key[1],
                                      window=window)
        _CURVES[key] = (mono, out)
    if _not_modified(request, out["etag"]):
        return Response(status_code=304, headers={
            "ETag": out["etag"], "Cache-Control": "private, no-cache"})
    _headers(response, out["etag"])
    return out
