"""THE MANAGEMENT EPOCH: the PAPER book re-based for management reporting.

OWNER-APPROVED MANAGEMENT RESET (relayed in the PM directive of 2026-10-05;
no approver identity or approval timestamp is recorded here because none
was supplied): management P&L starts at

    MANAGEMENT_EPOCH_START = 2026-10-05 00:00:00 America/New_York
    OPENING_EQUITY         = $500,000.00

THIS IS A READ MODEL, NOT A LEDGER EVENT. The paper ledger (migration 171)
stays exactly as it is: append-only, funded once, no reset, no deposit. Every
pre-epoch trade, fill, fee, settlement and correction stays queryable and is
reported as PRE-MANAGEMENT HISTORY. Nothing here feeds sizing, caps or any
risk control: the simulator keeps spending the ledger's own available cash.

THE RULES (each is a test in tests/test_paper_management_epoch.py):

  1. Anything fully settled (or fully sold) before the epoch is HISTORY and
     contributes nothing to management P&L or equity -- including a later
     correction of that settlement.
  2. A position open at the epoch is CARRIED at its epoch mark: the exit
     price of the last error-free book observed at or before the epoch and
     no more than EPOCH_MARK_MAX_AGE_S (the ledger's own mark staleness
     bound) before it. Only post-epoch movement is management P&L; the
     original cost stays for audit.
  3. A position opened after the epoch uses its actual entry cost and fees.
  4. A post-epoch sale or settlement is management realized P&L: a carried
     position realizes against its epoch basis, a new one against its entry
     basis (average cost, the ledger's own method; a carried position that
     was added to after the epoch averages the two).
  5. At the epoch:  CASH + RESERVED + CARRIED_MARK_VALUE = OPENING_EQUITY,
     where CASH is available cash (the ledger's `reserved` is part of its
     cash), so opening available cash is derived, never invented.
  6. A carried position with no defensible epoch mark is
     EPOCH_OPEN_MARK_UNVERIFIED: no mark is invented; the position and every
     post-epoch cash flow it has are held OUTSIDE the management figures
     (its risk is listed separately) until it is reconciled.

TIMING: an event belongs to the side of the epoch its LEDGER ENTRY was
committed on (paper_ledger.committed_at), so management cash reconciles to
the ledger exactly: management cash = ledger cash - (ledger cash at the
epoch - opening cash) - every post-epoch ledger cash flow held outside.
"""
from __future__ import annotations

import datetime as _dt
from decimal import Decimal
from zoneinfo import ZoneInfo

from . import bettor_paper_ledger as L

VERSION = "PAPER_MANAGEMENT_EPOCH_V1"
EPOCH_ID = "MGMT_2026_10_05"
NY = ZoneInfo("America/New_York")
EPOCH_START_LOCAL = "2026-10-05 00:00:00 America/New_York"
EPOCH_START = _dt.datetime(2026, 10, 5, 0, 0, 0, tzinfo=NY).timestamp()
OPENING_EQUITY_USD = Decimal("500000.00")
LABEL = "MANAGEMENT START: OCT 5, 2026 · OPENING EQUITY $500,000"
SOURCE = ("OWNER-APPROVED MANAGEMENT RESET, relayed in the PM directive of "
          "2026-10-05 (no approver identity or approval timestamp recorded)")
EPOCH_MARK_MAX_AGE_S = float(L.MARK_STALE_AFTER_S)

R_UNVERIFIED = "EPOCH_OPEN_MARK_UNVERIFIED"
R_NO_EPOCH_BOOK = "NO_ERROR_FREE_BOOK_AT_OR_BEFORE_THE_EPOCH_WITHIN_%dS" % int(
    EPOCH_MARK_MAX_AGE_S)

HISTORICAL = "HISTORICAL"
CARRIED = "CARRIED"
OPENED = "OPENED_AFTER_EPOCH"
UNVERIFIED = "CARRIED_UNVERIFIED"

ZERO = Decimal(0)
EPS = Decimal("0.000001")


def D(v) -> Decimal:
    return L.D(v if v is not None else 0)


def f2(v) -> float | None:
    return None if v is None else round(float(v), 2)


def f6(v) -> float | None:
    return None if v is None else round(float(v), 6)


# ═════════════════════════════════════════════════════════════════════
# PURE: the management book from ledger-timed events
# ═════════════════════════════════════════════════════════════════════

def management_book(*, epoch_at: float, opening_equity, fills: list,
                    settlement_entries: list, settled_qty: dict,
                    epoch_marks: dict, now_marks: dict, ledger_epoch: dict,
                    ledger_now: dict, meta: dict | None = None,
                    hwm_points: list | None = None) -> dict:
    """`fills`: [{position_key, direction, qty, gross_usd, fee_usd, at}] with
    `at` = the ledger commit time of the FILL / SALE entry.
    `settlement_entries`: [{position_key, kind, cash_usd, at}] -- every
    SETTLEMENT and CORRECTION ledger entry.
    `settled_qty`: {position_key: qty settled} (latest settlement version).
    `epoch_marks`: {position_key: {price, observed_at, source} | {why}}.
    `now_marks`: {position_key: the ledger's current mark dict}.
    `ledger_epoch` / `ledger_now`: {cash_usd, reserved_usd} -- ledger sums of
    entries committed before the epoch / all entries.
    `hwm_points`: [(at, ledger_equity_usd)] equity snapshots after the epoch
    with every position marked (used only when the re-base is exact)."""
    E = float(epoch_at)
    opening = D(opening_equity)
    meta = meta or {}
    pos: dict = {}

    def P(pk):
        return pos.setdefault(pk, {
            "bought_pre": ZERO, "sold_pre": ZERO, "buy_cost_pre": ZERO,
            "fees_pre": ZERO, "bought_post": ZERO, "sold_post": ZERO,
            "buy_cost_post": ZERO, "proceeds_post": ZERO, "fees_post": ZERO,
            "settle_at": None, "payout_post": ZERO, "post_settle_cash": ZERO,
            "post_cash": ZERO})

    for x in fills:
        p = P(x["position_key"])
        qty, gross, fee = D(x["qty"]), D(x["gross_usd"]), D(x["fee_usd"])
        post = float(x["at"]) >= E
        if x["direction"] == "BUY":
            if post:
                p["bought_post"] += qty
                p["buy_cost_post"] += gross + fee
                p["post_cash"] -= gross + fee
            else:
                p["bought_pre"] += qty
                p["buy_cost_pre"] += gross + fee
        else:
            if post:
                p["sold_post"] += qty
                p["proceeds_post"] += gross - fee
                p["post_cash"] += gross - fee
            else:
                p["sold_pre"] += qty
        if post:
            p["fees_post"] += fee
        else:
            p["fees_pre"] += fee
    for x in settlement_entries:
        if x["kind"] == "SETTLEMENT":
            P(x["position_key"])["settle_at"] = float(x["at"])
    hist_corrections = ZERO
    for x in settlement_entries:
        p = P(x["position_key"])
        if float(x["at"]) < E:
            continue
        if p["settle_at"] is not None and p["settle_at"] < E:
            # a post-epoch correction of a pre-epoch settlement: history
            hist_corrections += D(x["cash_usd"])
            continue
        p["post_settle_cash"] += D(x["cash_usd"])
        p["post_cash"] += D(x["cash_usd"])

    rows, unverified, historical_keys = [], [], []
    carried_value = ZERO
    excluded_post_cash = hist_corrections
    in_scope_cash = ZERO
    realized = unreal = ZERO
    marked_value = basis_open = unmarked_basis = ZERO
    fees_post_in = ZERO
    n_carried = n_opened = n_settled_post = n_unmarked = n_stale = 0
    for pk, p in sorted(pos.items()):
        sq = D(settled_qty.get(pk))
        settled_pre = p["settle_at"] is not None and p["settle_at"] < E
        settled_post = p["settle_at"] is not None and p["settle_at"] >= E
        qty_e = p["bought_pre"] - p["sold_pre"] - (sq if settled_pre else ZERO)
        has_post = (p["bought_post"] > 0 or p["sold_post"] > 0
                    or settled_post or p["post_settle_cash"] != 0)
        m = meta.get(pk) or {}
        if qty_e <= EPS and not (p["bought_post"] > 0):
            # closed (sold or settled) before the epoch: history. A post-epoch
            # correction of its settlement stays history too.
            historical_keys.append(pk)
            excluded_post_cash += p["post_cash"]
            continue
        if qty_e > EPS:
            em = epoch_marks.get(pk) or {}
            if em.get("price") is None:
                excluded_post_cash += p["post_cash"]
                open_now = (qty_e + p["bought_post"] - p["sold_post"]
                            - (sq if settled_post else ZERO))
                nm = now_marks.get(pk) or {}
                unverified.append({
                    "position_key": pk, "state": R_UNVERIFIED,
                    "why": em.get("why") or R_NO_EPOCH_BOOK,
                    "market": m.get("us_market_slug"),
                    "side": m.get("holding_side"),
                    "strategy": m.get("strategy"),
                    "qty_at_epoch": f6(qty_e), "open_qty_now": f6(open_now),
                    "historical_cost_usd": f2(
                        p["buy_cost_pre"] / p["bought_pre"] * qty_e
                        if p["bought_pre"] > 0 else None),
                    "current_mark_price": f6(nm.get("price")),
                    "current_marked_value_usd": f2(
                        D(nm["price"]) * open_now
                        if nm.get("price") is not None and open_now > EPS
                        else None),
                    "post_epoch_cash_held_outside_usd": f2(p["post_cash"])})
                continue
            kind = CARRIED
            mark_e = D(em["price"])
            carry = D(qty_e * mark_e)
            carried_value += carry
            n_carried += 1
        else:
            kind = OPENED
            mark_e = None
            carry = ZERO
            qty_e = ZERO
            n_opened += 1
        basis_qty = qty_e + p["bought_post"]
        basis_total = carry + p["buy_cost_post"]
        avg = (basis_total / basis_qty) if basis_qty > 0 else ZERO
        settled_q = sq if settled_post else ZERO
        r = p["proceeds_post"] - avg * p["sold_post"]
        if settled_post:
            r += p["post_settle_cash"] - avg * settled_q
            n_settled_post += 1
        realized += r
        fees_post_in += p["fees_post"]
        in_scope_cash += p["post_cash"]
        open_now = basis_qty - p["sold_post"] - settled_q
        u = mv = None
        state = "CLOSED"
        if open_now > EPS:
            nm = now_marks.get(pk) or {}
            basis_open += avg * open_now
            if nm.get("price") is None:
                state = "UNMARKED"
                n_unmarked += 1
                unmarked_basis += avg * open_now
            else:
                mv = D(open_now * D(nm["price"]))
                u = mv - avg * open_now
                marked_value += mv
                unreal += u
                state = "STALE_MARK" if nm.get("stale") else "MARKED"
                n_stale += 1 if nm.get("stale") else 0
        rows.append({
            "position_key": pk, "kind": kind,
            "market": m.get("us_market_slug"), "side": m.get("holding_side"),
            "strategy": m.get("strategy"),
            "qty_at_epoch": f6(qty_e),
            "epoch_mark_price": f6(mark_e),
            "epoch_mark_observed_at": (epoch_marks.get(pk) or {}).get(
                "observed_at") if kind == CARRIED else None,
            "epoch_mark_source": (epoch_marks.get(pk) or {}).get("source")
            if kind == CARRIED else None,
            "epoch_basis_usd": f2(carry) if kind == CARRIED else None,
            "historical_cost_at_epoch_usd": f2(
                p["buy_cost_pre"] / p["bought_pre"] * qty_e
                if kind == CARRIED and p["bought_pre"] > 0 else None),
            "bought_after_epoch": f6(p["bought_post"]),
            "sold_after_epoch": f6(p["sold_post"]),
            "settled_after_epoch": f6(settled_q),
            "management_avg_basis_per_contract": f6(avg),
            "open_qty": f6(open_now if open_now > EPS else ZERO),
            "mark_state": state,
            "marked_value_usd": f2(mv),
            "realized_pnl_usd": f2(r), "unrealized_pnl_usd": f2(u),
            "fees_after_epoch_usd": f2(p["fees_post"])})

    # ── THE CASH, TWO WAYS ──────────────────────────────────────────────
    cash_e_ledger = D(ledger_epoch.get("cash_usd"))
    reserved_e = D(ledger_epoch.get("reserved_usd"))
    opening_cash = opening - carried_value          # cash incl. reserved
    opening_available = opening_cash - reserved_e
    flows_in = in_scope_cash
    cash_by_flows = opening_cash + flows_in
    cash_now_ledger = D(ledger_now.get("cash_usd"))
    reserved_now = D(ledger_now.get("reserved_usd"))
    cash_by_ledger = (cash_now_ledger - (cash_e_ledger - opening_cash)
                      - excluded_post_cash)
    cash = cash_by_flows
    equity = cash + marked_value + unmarked_basis
    total = realized + unreal
    identity_gap = equity - (opening + total)
    opening_gap = (opening_available + reserved_e + carried_value) - opening

    # ── HIGH-WATER MARK ────────────────────────────────────────────────
    exact = not unverified and excluded_post_cash == 0
    offset = opening_cash - cash_e_ledger
    hwm, hwm_at, hwm_basis = opening, epoch_at, "OPENING_EQUITY"
    used = 0
    if exact:
        for at, eq in hwm_points or []:
            v = D(eq) + offset
            used += 1
            if v > hwm:
                hwm, hwm_at, hwm_basis = v, at, "EQUITY_SNAPSHOT"
    if equity > hwm:
        hwm, hwm_at, hwm_basis = equity, None, "CURRENT_EQUITY"
    dd = hwm - equity
    return {
        "epoch_id": EPOCH_ID, "version": VERSION,
        "epoch_start": EPOCH_START_LOCAL, "epoch_start_at": epoch_at,
        "label": LABEL, "source": SOURCE,
        "opening_equity_usd": f2(opening),
        "opening": {
            "available_cash_usd": f2(opening_available),
            "reserved_usd": f2(reserved_e),
            "carried_position_mark_value_usd": f2(carried_value),
            "cash_including_reserved_usd": f2(opening_cash),
            "identity": "AVAILABLE_CASH + RESERVED + CARRIED_MARK_VALUE = "
                        "OPENING_EQUITY",
            "identity_gap_usd": f6(opening_gap),
            "identity_holds": abs(opening_gap) <= EPS,
            "ledger_cash_at_epoch_usd": f2(cash_e_ledger),
            "rebase_offset_usd": f2(offset)},
        "carried_positions": n_carried,
        "carried_unverified": len(unverified),
        "opened_after_epoch": n_opened,
        "settled_after_epoch": n_settled_post,
        "equity_usd": f2(equity),
        "cash_usd": f2(cash - reserved_now),
        "cash_including_reserved_usd": f2(cash),
        "reserved_usd": f2(reserved_now),
        "marked_open_position_value_usd": f2(marked_value),
        "unmarked_carried_at_basis_usd": f2(unmarked_basis),
        "exposure": {"basis_usd": f2(basis_open),
                     "marked_value_usd": f2(marked_value),
                     "unmarked_basis_usd": f2(unmarked_basis),
                     "open_positions": sum(
                         1 for r in rows if r["mark_state"] != "CLOSED"),
                     "unmarked": n_unmarked, "stale_marks": n_stale},
        "realized_pnl_usd": f2(realized),
        "unrealized_pnl_usd": f2(unreal),
        "total_pnl_usd": f2(total),
        "return_pct": (round(float(total / opening * 100), 4)
                       if opening else None),
        "fees_after_epoch_usd": f2(fees_post_in),
        "rebase_exact": exact,
        "high_water_mark_usd": f2(hwm), "high_water_mark_at": hwm_at,
        "high_water_mark_basis": hwm_basis,
        "high_water_mark_points_used": used,
        "high_water_mark_rule": (
            "max of the opening equity, every post-epoch equity snapshot "
            "re-based by the epoch offset, and the current equity"
            if exact else
            "max of the opening equity and the current equity only: the "
            "snapshots cannot be re-based exactly while %d carried "
            "position(s) are %s or $%s of post-epoch ledger cash is held "
            "outside" % (len(unverified), R_UNVERIFIED,
                         format(float(excluded_post_cash), ",.2f"))),
        "drawdown_usd": f2(dd),
        "drawdown_pct": (round(float(dd / hwm * 100), 4) if hwm else None),
        "identity": {
            "rule": "EQUITY = OPENING_EQUITY + REALIZED + UNREALIZED",
            "gap_usd": f6(identity_gap),
            "holds": abs(identity_gap) <= Decimal("0.01")},
        "ledger_reconciliation": {
            "rule": ("cash by per-position post-epoch flows = ledger cash - "
                     "(ledger cash at the epoch - opening cash) - post-epoch "
                     "ledger cash held outside"),
            "cash_by_flows_usd": f6(cash_by_flows),
            "cash_by_ledger_usd": f6(cash_by_ledger),
            "gap_usd": f6(cash_by_flows - cash_by_ledger),
            "reconciles": abs(cash_by_flows - cash_by_ledger) <= Decimal(
                "0.01"),
            "ledger_cash_now_usd": f2(cash_now_ledger),
            "post_epoch_cash_held_outside_usd": f2(excluded_post_cash)},
        "unverified_positions": unverified,
        "historical_positions": len(historical_keys),
        "rows": rows,
        "feeds_risk_controls": False,
        "what_this_is": ("a management reporting view over the unchanged "
                         "append-only paper ledger; the simulator, its caps "
                         "and its available cash are not re-based"),
    }


# ═════════════════════════════════════════════════════════════════════
# THE READ (one connection; read-only)
# ═════════════════════════════════════════════════════════════════════

FILLS_SQL = """
SELECT f.group_id, f.us_market_slug, f.holding_side, f.direction, f.qty,
       f.gross_usd, f.fee_usd, coalesce(l.committed_at, f.recorded_at) AS at
  FROM paper_fills f
  LEFT JOIN paper_ledger l ON l.fill_id = f.fill_id
                          AND l.kind IN ('FILL', 'SALE')
 WHERE f.account_id = $1
"""

SETTLE_SQL = """
SELECT position_key, kind, cash_delta_usd, committed_at
  FROM paper_ledger
 WHERE account_id = $1 AND kind IN ('SETTLEMENT', 'CORRECTION')
   AND position_key IS NOT NULL
"""

SETTLED_QTY_SQL = """
SELECT DISTINCT ON (position_key) position_key, qty
  FROM paper_settlements WHERE account_id = $1
 ORDER BY position_key, version DESC
"""

LEDGER_SUMS_SQL = """
SELECT coalesce(sum(cash_delta_usd) FILTER (
           WHERE committed_at < $2 OR kind = 'INITIAL_FUNDING'), 0) AS cash_e,
       coalesce(sum(reserved_delta_usd) FILTER (WHERE committed_at < $2), 0)
           AS reserved_e,
       coalesce(sum(cash_delta_usd), 0) AS cash_now,
       coalesce(sum(reserved_delta_usd), 0) AS reserved_now
  FROM paper_ledger WHERE account_id = $1
"""

EPOCH_BOOKS_SQL = """
SELECT DISTINCT ON (us_market_slug) us_market_slug, obs_id, observed_at,
       bids, offers
  FROM paper_book_observations
 WHERE us_market_slug = ANY($1::text[]) AND error IS NULL
   AND observed_at <= $2 AND observed_at >= $3
 ORDER BY us_market_slug, observed_at DESC
"""

META_SQL = """
SELECT group_id, us_market_slug, holding_side, max(strategy) AS strategy
  FROM paper_fills WHERE account_id = $1
 GROUP BY group_id, us_market_slug, holding_side
"""


def _at(v) -> float:
    return float(v.timestamp()) if hasattr(v, "timestamp") else float(v)


async def epoch_marks(conn, slugs_sides: dict, *, epoch_at: float) -> dict:
    """{position_key: {price, observed_at, source} | {why}} -- the exit
    price of the last error-free book at or before the epoch and no older
    than EPOCH_MARK_MAX_AGE_S. Absent such a book: the reason, never a
    price."""
    from . import bettor_book_snapshot as BS
    slugs = sorted({s for s, _side in slugs_sides.values()})
    books = {}
    if slugs:
        e = _dt.datetime.fromtimestamp(epoch_at, _dt.timezone.utc)
        lo = _dt.datetime.fromtimestamp(epoch_at - EPOCH_MARK_MAX_AGE_S,
                                        _dt.timezone.utc)
        for r in await conn.fetch(EPOCH_BOOKS_SQL, slugs, e, lo):
            books[r["us_market_slug"]] = r
    out = {}
    for pk, (slug, side) in slugs_sides.items():
        r = books.get(slug)
        if r is None:
            out[pk] = {"why": R_NO_EPOCH_BOOK}
            continue
        md = {"bids": L._j(r["bids"]) or [], "offers": L._j(r["offers"]) or []}
        ex = BS.exit_ladder(md, held_intent=(
            "ORDER_INTENT_BUY_LONG" if side == "LONG"
            else "ORDER_INTENT_BUY_SHORT"))
        if not ex.get("ok") or ex.get("best_exit_price") is None:
            out[pk] = {"why": "EPOCH_BOOK_HAS_NO_EXIT_SIDE: %s"
                       % (ex.get("refusal") or "no bid")}
            continue
        out[pk] = {"price": ex["best_exit_price"],
                   "observed_at": _at(r["observed_at"]),
                   "age_at_epoch_s": round(epoch_at - _at(r["observed_at"]),
                                           3),
                   "source": "paper_book_observations:%s" % r["obs_id"]}
    return out


async def read(conn, account_id: str, *, bal: dict, now: float,
               epoch_at: float = EPOCH_START,
               opening_equity=OPENING_EQUITY_USD) -> dict:
    """The management book for `account_id` from the ledger tables, with
    current marks taken from `bal` (bettor_paper_ledger.balances)."""
    if now < epoch_at:
        return {"status": "NOT_STARTED", "epoch_id": EPOCH_ID,
                "epoch_start": EPOCH_START_LOCAL, "label": LABEL}
    acct = account_id

    def pk_of(r):
        return L.position_key(account_id=acct, group_id=r["group_id"],
                              slug=r["us_market_slug"],
                              holding_side=r["holding_side"])

    fills = [{"position_key": pk_of(r), "direction": r["direction"],
              "qty": r["qty"], "gross_usd": r["gross_usd"],
              "fee_usd": r["fee_usd"], "at": _at(r["at"])}
             for r in await conn.fetch(FILLS_SQL, acct)]
    sets = [{"position_key": r["position_key"], "kind": r["kind"],
             "cash_usd": r["cash_delta_usd"], "at": _at(r["committed_at"])}
            for r in await conn.fetch(SETTLE_SQL, acct)]
    sq = {r["position_key"]: r["qty"]
          for r in await conn.fetch(SETTLED_QTY_SQL, acct)}
    e = _dt.datetime.fromtimestamp(epoch_at, _dt.timezone.utc)
    s = await conn.fetchrow(LEDGER_SUMS_SQL, acct, e)
    meta = {pk_of(r): {"us_market_slug": r["us_market_slug"],
                       "holding_side": r["holding_side"],
                       "strategy": r["strategy"]}
            for r in await conn.fetch(META_SQL, acct)}
    # positions open at the epoch need an epoch mark
    held: dict = {}
    for x in fills:
        if x["at"] < epoch_at:
            q = D(x["qty"])
            held[x["position_key"]] = held.get(x["position_key"], ZERO) + (
                q if x["direction"] == "BUY" else -q)
    for x in sets:
        if x["kind"] == "SETTLEMENT" and x["at"] < epoch_at:
            held[x["position_key"]] = held.get(x["position_key"], ZERO) - D(
                sq.get(x["position_key"]))
    need = {pk: (meta[pk]["us_market_slug"], meta[pk]["holding_side"])
            for pk, q in held.items() if q > EPS and pk in meta}
    em = await epoch_marks(conn, need, epoch_at=epoch_at)
    now_marks = {p["position_key"]: p.get("mark") or {}
                 for p in bal.get("open_positions") or []}
    points = []
    if await conn.fetchval(
            "SELECT to_regclass('paper_equity_snapshots') IS NOT NULL"):
        points = [(_at(r["at"]), r["equity_usd"]) for r in await conn.fetch(
            "SELECT at, equity_usd FROM paper_equity_snapshots "
            " WHERE account_id = $1 AND at >= $2 AND equity_usd IS NOT NULL "
            "   AND unmarked_positions = 0 ORDER BY at", acct, e)]
    book = management_book(
        epoch_at=epoch_at, opening_equity=opening_equity, fills=fills,
        settlement_entries=sets, settled_qty=sq, epoch_marks=em,
        now_marks=now_marks,
        ledger_epoch={"cash_usd": s["cash_e"], "reserved_usd": s["reserved_e"]},
        ledger_now={"cash_usd": s["cash_now"],
                    "reserved_usd": s["reserved_now"]},
        meta=meta, hwm_points=points)
    ok = (book["identity"]["holds"] and book["opening"]["identity_holds"]
          and book["ledger_reconciliation"]["reconciles"])
    book["status"] = "OK" if ok else "DOES_NOT_RECONCILE"
    book["why"] = None if ok else (
        "equity identity gap %s, opening gap %s, ledger gap %s" % (
            book["identity"]["gap_usd"], book["opening"]["identity_gap_usd"],
            book["ledger_reconciliation"]["gap_usd"]))
    book["pre_management_history"] = {
        "what": ("the paper ledger since its funding: every trade, fill, fee, "
                 "settlement and correction, unchanged and queryable; not "
                 "mixed into the management figures"),
        "ledger_realized_pnl_usd": f2(bal.get("realized_pnl_usd")),
        "ledger_fees_paid_usd": f2(bal.get("fees_paid_usd")),
        "ledger_cash_usd": f2(bal.get("cash_usd")),
        "ledger_available_usd": f2(bal.get("available_usd")),
        "positions_closed_before_epoch": book["historical_positions"]}
    book["computed_at"] = now
    return book
