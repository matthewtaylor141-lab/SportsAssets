"""THE PAPER ACCOUNT AND ITS ONE AUTHORITATIVE LEDGER (migration 171).

OWNER AUTHORIZATION, EXACT SCOPE: a $500,000 FICTIONAL bankroll. No real
money, no real venue orders, no funded activation. Every figure here is
LIVE MARKET DATA / SIMULATED EXECUTION: the market data is real, the orders
and fills are simulated (bettor_paper_simulator), and nothing here is
verified execution.

── THE RULES, ENFORCED HERE AND BY THE SCHEMA ─────────────────────────────

INITIALIZATION. Exactly $500,000, ONCE: `ensure_account` inserts the account
by its unique key and one INITIAL_FUNDING entry under the idempotency key
INITIAL_FUNDING:<account_key> (migration 171 uses the same key). Re-running
migrations, deploys or restarts never re-funds; there is no reset, no
replenishment and no DEPOSIT kind.

ONE LEDGER. `paper_ledger` is append-only with unique idempotency keys, so a
duplicate event, a retry or a restart can never debit or credit twice:

    ORDER_SUBMITTED       reserve limit x qty + max fees. Available cash
                          falls; cash does not. It is NOT a purchase.
    FILL                  debit the FILLED cost + fees only; release the
                          filled share of the reservation (the last fill of
                          an order releases exactly what remains).
    RESERVATION_RELEASED  cancel / expire of the unfilled remainder: the
                          reservation is released; no cash credit.
    SALE                  credit proceeds - fees.
    SETTLEMENT            credit the payout EXACTLY ONCE per position +
                          settlement event (unique settlement_key). A loser
                          credits 0 (the entry still records that it settled).
    CORRECTION            a separate entry, pointing at what it corrects, so
                          the history stays.
    PRICE MOVEMENT        marks, unrealized P&L and equity only -- never cash.

CONCURRENCY. Every ledger write happens in one transaction that first takes
the account row lock (`_lock`), and the table's own trigger takes it again
before numbering the entry. Two concurrent orders therefore cannot spend the
same available cash: the second waits, then sees the first's reservation.

DERIVED FIGURES: `balances` -- ONE function, used by every read model and
the live stream:
    cash; reserved (part of cash, not extra); available = cash - reserved;
    open-position value, marked, with each mark's source, time and staleness
    (an unavailable mark is flagged and NEVER counted as zero); total equity
    = cash + marked open-position value (None when a mark is unavailable,
    with the marked-only figure beside it); realized and unrealized P&L.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import time
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

VERSION = "PAPER_LEDGER_V1"
ACCOUNT_ID = "paper_acct_main"
ACCOUNT_KEY = "BETTORTOKEN_PAPER_MAIN"
STARTING_CASH_USD = Decimal("500000")
CURRENCY = "SIMULATED_USD"
DATA_LABEL = "LIVE MARKET DATA / SIMULATED EXECUTION"
LABELS = {"market_data": "LIVE_MARKET_DATA",
          "execution": "SIMULATED_EXECUTION",
          "money": "FICTIONAL_USD_NOT_REAL_MONEY",
          "real_money_submission": "DISABLED"}

#: A mark older than this is shown, and flagged STALE.
MARK_STALE_AFTER_S = 300.0
MARK_METHOD = ("TOP_OF_BOOK_EXIT_PRICE: what closing one contract would "
               "receive at the best displayed level of the side a close "
               "consumes (bettor_book_snapshot.exit_ladder), from the paper "
               "path's latest observed book; no depth adjustment")

K_INITIAL = "INITIAL_FUNDING"
K_SUBMITTED = "ORDER_SUBMITTED"
K_FILL = "FILL"
K_RELEASED = "RESERVATION_RELEASED"
K_SALE = "SALE"
K_SETTLEMENT = "SETTLEMENT"
K_CORRECTION = "CORRECTION"
KINDS = (K_INITIAL, K_SUBMITTED, K_FILL, K_RELEASED, K_SALE, K_SETTLEMENT,
         K_CORRECTION)

SRC_LEDGER = "PAPER_LEDGER"
SRC_SIMULATOR = "SIMULATOR"
SRC_SETTLEMENT = "AUTHORITATIVE_SETTLEMENT_EVIDENCE"

#: The paper strategy of a row that names none (migration 182's default):
#: the original two-model strategy.
DEFAULT_STRATEGY = "DEREK_ENTRY_POLICY_V2"

OPEN_STATES = ("PENDING_SIMULATION", "RESTING", "PARTIALLY_FILLED",
               "CANCEL_PENDING")
TERMINAL_STATES = ("FILLED", "EXPIRED", "CANCELED", "REJECTED")

#: ── REFUSALS ─────────────────────────────────────────────────────────
from . import bettor_paper_limits as _LIMITS  # noqa: E402

R_NO_ACCOUNT = "THE_PAPER_ACCOUNT_DOES_NOT_EXIST"
R_INSUFFICIENT = "INSUFFICIENT_AVAILABLE_PAPER_CASH"
R_HEDGE_RESERVE = "AN_ENTRY_MAY_NOT_SPEND_THE_HEDGE_RESERVE"
R_PER_ORDER = "ABOVE_THE_PER_ORDER_CAP"
R_PER_MARKET = "ABOVE_THE_PER_MARKET_CONCENTRATION_CAP"
R_PER_FIXTURE = "ABOVE_THE_PER_FIXTURE_CONCENTRATION_CAP"
R_MAX_GROUPS = "ABOVE_THE_MAXIMUM_CONCURRENT_GROUPS"
R_NOT_HELD = "A_SALE_NEEDS_UNCOMMITTED_HELD_INVENTORY"
R_BAD_ORDER = "THE_ORDER_IS_MALFORMED"
R_NOT_PAPER = "NOT_A_PAPER_IDENTIFIER"
R_ORDER_NOT_OPEN = "THE_ORDER_IS_NOT_OPEN"
R_OVERFILL = "THE_FILL_EXCEEDS_THE_ORDER_REMAINDER"

Q6 = Decimal("0.000001")


def D(v) -> Decimal:
    """Exact decimal at the ledger's 6 places."""
    if isinstance(v, Decimal):
        return v.quantize(Q6, rounding=ROUND_HALF_UP)
    return Decimal(str(v)).quantize(Q6, rounding=ROUND_HALF_UP)


def f(v) -> float | None:
    return None if v is None else float(v)


def _epoch(v) -> float | None:
    if v is None:
        return None
    if hasattr(v, "timestamp"):
        return float(v.timestamp())
    return float(v)


def _ts(epoch: float):
    return _dt.datetime.fromtimestamp(float(epoch), _dt.timezone.utc)


def _j(v):
    if v is None or isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return None


def is_paper_id(v) -> bool:
    return isinstance(v, str) and v.startswith("paper")


def position_key(*, account_id: str, group_id: str, slug: str,
                 holding_side: str) -> str:
    return "paperpos:%s:%s:%s:%s" % (account_id, group_id, slug,
                                     holding_side)


def default_fee_fn(qty: float, price: float, *, at=None) -> tuple:
    """The deployed schedule (`bettor_funded_book.fee_for`, a pure function
    of quantity, price and date -- it reads and writes no table)."""
    from . import bettor_funded_book as FB
    return FB.fee_for(qty, price, at=at)


def _fee(fee_fn, qty, price, at) -> Decimal:
    fn = fee_fn or default_fee_fn
    try:
        got = fn(float(qty), float(price), at=at)
    except TypeError:
        got = fn(float(qty), float(price))
    charge = got[0] if isinstance(got, tuple) else got
    return D(abs(float(charge)))


def max_fee_for(qty, limit_price, *, at=None, fee_fn=None) -> Decimal:
    """The largest fee a BUY of `qty` at any price up to `limit_price` can
    be charged. The schedule is theta x C x p x (1 - p), largest at p = 0.5,
    so the bound is the fee at min(limit, 0.5)."""
    p = min(float(limit_price), 0.5)
    return _fee(fee_fn, qty, p, at)


def reservation_for(qty, limit_price, *, at=None, fee_fn=None) -> Decimal:
    """limit x qty + max fees: the cash an open BUY holds back."""
    return D(D(qty) * D(limit_price)) + max_fee_for(qty, limit_price, at=at,
                                                    fee_fn=fee_fn)


# ═════════════════════════════════════════════════════════════════════
# THE ACCOUNT
# ═════════════════════════════════════════════════════════════════════

async def ensure_account(conn, *, account_id: str = ACCOUNT_ID,
                         account_key: str = ACCOUNT_KEY) -> dict:
    """THE IDEMPOTENT INITIALIZER. Creates the account by its unique key and
    funds it with exactly $500,000 ONCE. A second call -- a restart, a
    deploy, a re-run -- finds both and changes nothing."""
    if not is_paper_id(account_id):
        return {"ok": False, "refusal": R_NOT_PAPER}
    async with conn.transaction():
        await conn.execute(
            "INSERT INTO paper_accounts (account_id, account_key, "
            " starting_cash_usd) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
            account_id, account_key, STARTING_CASH_USD)
        row = await conn.fetchrow(
            "SELECT * FROM paper_accounts WHERE account_id = $1", account_id)
        if row is None or row["account_key"] != account_key:
            return {"ok": False, "refusal": "ACCOUNT_KEY_MISMATCH"}
        seq = await conn.fetchval(
            "INSERT INTO paper_ledger (seq, account_id, idempotency_key, kind,"
            " cash_delta_usd, reserved_delta_usd, cash_after_usd, "
            " reserved_after_usd, event_source, detail) "
            "SELECT 0, $1, $2, 'INITIAL_FUNDING', $3, 0, 0, 0, $4, $5::jsonb "
            " WHERE NOT EXISTS (SELECT 1 FROM paper_ledger WHERE account_id=$1"
            "                    AND kind='INITIAL_FUNDING') "
            "ON CONFLICT DO NOTHING RETURNING seq",
            account_id, "%s:%s" % (K_INITIAL, account_key), STARTING_CASH_USD,
            SRC_LEDGER, json.dumps({"why": "owner-authorized fictional "
                                           "bankroll, funded once"}))
    return {"ok": True, "account_id": account_id, "account_key": account_key,
            "funded_now": seq is not None,
            "starting_cash_usd": float(row["starting_cash_usd"])}


async def _lock(conn, account_id: str):
    """THE ACCOUNT ROW LOCK. Caller holds a transaction."""
    return await conn.fetchrow(
        "SELECT * FROM paper_accounts WHERE account_id = $1 FOR UPDATE",
        account_id)


async def cash_state(conn, account_id: str) -> dict:
    """cash / reserved / available, DERIVED by summing the ledger, with the
    last entry's running balance cross-checked against the sum."""
    r = await conn.fetchrow(
        "SELECT coalesce(sum(cash_delta_usd), 0) AS cash, "
        "       coalesce(sum(reserved_delta_usd), 0) AS reserved, "
        "       max(seq) AS last_seq, count(*) AS n "
        "  FROM paper_ledger WHERE account_id = $1", account_id)
    last = await conn.fetchrow(
        "SELECT seq, cash_after_usd, reserved_after_usd, committed_at "
        "  FROM paper_ledger WHERE account_id = $1 ORDER BY seq DESC LIMIT 1",
        account_id)
    cash, res = D(r["cash"]), D(r["reserved"])
    return {"cash": cash, "reserved": res, "available": cash - res,
            "last_seq": r["last_seq"], "entries": int(r["n"]),
            "last_committed_at": (None if last is None
                                  else _epoch(last["committed_at"])),
            "running_balance_agrees": (
                last is None or (D(last["cash_after_usd"]) == cash
                                 and D(last["reserved_after_usd"]) == res))}


async def _append(conn, *, account_id: str, kind: str, key: str,
                  cash_delta=0, reserved_delta=0, session_id=None,
                  order_id=None, fill_id=None, group_id=None,
                  position_key_=None, settlement_key=None, corrects_seq=None,
                  source=SRC_LEDGER, simulator_version=None,
                  detail: dict | None = None) -> dict:
    """ONE ENTRY. Idempotent by `key`: a repeat returns the stored entry with
    duplicate=True and writes nothing. Caller holds the transaction."""
    got = await conn.fetchrow(
        "INSERT INTO paper_ledger (seq, account_id, session_id, "
        " idempotency_key, kind, cash_delta_usd, reserved_delta_usd, "
        " cash_after_usd, reserved_after_usd, order_id, fill_id, group_id, "
        " position_key, settlement_key, corrects_seq, event_source, "
        " simulator_version, detail) "
        "VALUES (0,$1,$2,$3,$4,$5,$6,0,0,$7,$8,$9,$10,$11,$12,$13,$14,"
        "        $15::jsonb) ON CONFLICT (idempotency_key) DO NOTHING "
        "RETURNING *",
        account_id, session_id, key, kind, D(cash_delta), D(reserved_delta),
        order_id, fill_id, group_id, position_key_, settlement_key,
        corrects_seq, source, simulator_version,
        json.dumps(detail or {}, default=str))
    if got is None:
        prior = await conn.fetchrow(
            "SELECT * FROM paper_ledger WHERE idempotency_key = $1", key)
        return dict(entry_view(prior), duplicate=True)
    return dict(entry_view(got), duplicate=False)


def entry_view(r) -> dict:
    if r is None:
        return {}
    d = dict(r)
    return {"sequence": d["seq"], "kind": d["kind"],
            "idempotency_key": d["idempotency_key"],
            "account_id": d["account_id"], "session_id": d.get("session_id"),
            "cash_delta_usd": f(d["cash_delta_usd"]),
            "reserved_delta_usd": f(d["reserved_delta_usd"]),
            "cash_after_usd": f(d["cash_after_usd"]),
            "reserved_after_usd": f(d["reserved_after_usd"]),
            "available_after_usd": f(D(d["cash_after_usd"])
                                     - D(d["reserved_after_usd"])),
            "order_id": d.get("order_id"), "fill_id": d.get("fill_id"),
            "group_id": d.get("group_id"),
            "position_key": d.get("position_key"),
            "settlement_key": d.get("settlement_key"),
            "corrects_seq": d.get("corrects_seq"),
            "event_source": d.get("event_source"),
            "simulator_version": d.get("simulator_version"),
            "data_label": d.get("data_label"),
            "detail": _j(d.get("detail")) or {},
            "committed_at": _epoch(d.get("committed_at"))}


# ═════════════════════════════════════════════════════════════════════
# ORDERS: THE RESERVATION
# ═════════════════════════════════════════════════════════════════════

ORDER_COLUMNS = (
    "order_id", "idempotency_key", "account_id", "session_id", "group_id",
    "role", "direction", "holding_side", "intent", "us_market_slug",
    "fixture", "label", "order_type", "time_in_force", "allow_partial",
    "qty", "limit_price", "wire_price", "decision_id", "decided_at",
    "eligible_at", "expires_at", "queue_ahead_qty", "queue_basis",
    "simulator_version")


def order_id_for(idempotency_key: str) -> str:
    return "paperord:" + hashlib.sha256(
        idempotency_key.encode()).hexdigest()[:24]


async def _exposure(conn, account_id: str, *, slug=None,
                    fixture=None) -> Decimal:
    """Cost basis of open inventory plus the reservation of open BUY orders,
    on one market or one fixture."""
    where, arg = (("us_market_slug = $2", slug) if slug is not None
                  else ("fixture = $2", fixture))
    res = await conn.fetchval(
        "SELECT coalesce(sum(reserved_remaining_usd), 0) FROM paper_orders "
        " WHERE account_id = $1 AND %s AND direction = 'BUY' "
        "   AND state = ANY($3::text[])" % where, account_id, arg,
        list(OPEN_STATES))
    pos = [p for p in await positions(conn, account_id)
           if (p["us_market_slug"] == slug if slug is not None
               else p["fixture"] == fixture)]
    return D(res) + sum((D(p["cost_basis_usd"]) for p in pos), Decimal(0))


async def _open_groups(conn, account_id: str) -> int:
    rows = await positions(conn, account_id)
    held = {p["group_id"] for p in rows}
    pend = await conn.fetch(
        "SELECT DISTINCT group_id FROM paper_orders WHERE account_id = $1 "
        "   AND state = ANY($2::text[])", account_id, list(OPEN_STATES))
    return len(held | {r["group_id"] for r in pend})


R_FIXTURE_OWNED = "ANOTHER_STRATEGY_HOLDS_EXPOSURE_TO_THIS_FIXTURE"
R_SAME_STRATEGY_LIVE = "THIS_STRATEGY_ALREADY_HAS_A_LIVE_ENTRY_ON_THIS_FIXTURE"
#: Main account under the owner's capital policy: no fixture-count, exposure
#: or concentration limit, but a strategy does not re-enter the exact contract
#: and side it already holds (working entry or open position). Each new
#: valuation of a held contract is a new decision key, so without this a lane
#: would add another ~$1,000 to the same position on every valuation; that is
#: an add to an existing position, not a new ~$1,000 initial position.
R_SAME_CONTRACT_HELD = "THIS_STRATEGY_ALREADY_HOLDS_THIS_CONTRACT"
#: THE OTHER SIDE OF A CONTRACT THIS STRATEGY HOLDS: RECORDED, NOT REFUSED.
#: Since each valuation also values the other side of its binary contract
#: (bettor_complement_valuation), a strategy holding one side can be handed
#: the other. The first version of the incident branch refused that entry
#: (at the decision and under the lock) -- a new paper risk-admission rule
#: nobody approved (review of 7bd084b; owner 2026-10-04: do NOT change risk
#: limits). The decision RECORDS the held other side instead
#: (`opposite_side_held`), so the owner can decide whether to add such a
#: rule; the account lock applies only its existing checks.
OPPOSITE_SIDE_HELD_IS = (
    "RECORDED_FOR_THE_OWNER_GATES_NOTHING: this strategy already holds the "
    "other side of this binary contract. Holding both pays exactly 1 at "
    "settlement, so the pair locks in (1 - the two costs - fees) -- a gain "
    "when the later side is cheap enough, a loss otherwise -- and the later "
    "entry acts as a partial exit of the first. No rule refuses it: adding "
    "one is a risk-admission change for the owner to decide.")


def other_side(holding_side) -> str | None:
    return {"LONG": "SHORT", "SHORT": "LONG"}.get(str(holding_side or ""))


async def same_contract_held(conn, account_id: str, strategy, slug,
                             holding_side) -> list:
    """This strategy's working entries or open positions on this exact
    contract and side, on this account. Read-only; [] when none."""
    if not strategy or not slug:
        return []
    rows = await conn.fetch(
        "SELECT DISTINCT group_id, state FROM paper_orders "
        " WHERE account_id=$1 AND strategy=$2 AND role='ENTRY' "
        "   AND us_market_slug=$3 AND holding_side=$4 "
        "   AND (state = ANY($5::text[]) OR filled_qty > 0)",
        account_id, str(strategy), slug, holding_side, list(OPEN_STATES))
    if not rows:
        return []
    live = [r for r in rows if r["state"] in OPEN_STATES]
    rest = {r["group_id"] for r in rows if r["state"] not in OPEN_STATES}
    held = []
    if rest:
        held = [g for g in rest if g in {
            p["group_id"] for p in await positions(conn, account_id)
            if p["open_qty"] > 1e-9}]
    return ([{"group_id": r["group_id"], "state": r["state"]} for r in live]
            + [{"group_id": g, "state": "OPEN_POSITION"} for g in sorted(held)])


async def fixture_owner_refusal(conn, o: dict, *,
                                same_strategy_live: bool = False):
    """FIXTURE OWNERSHIP, read UNDER THE ACCOUNT LOCK (submit_order calls it
    after taking the lock, so two contenders for one fixture are serialized
    and the second sees the first's order).

    Refuses an ENTRY when ANOTHER strategy on this account holds the same
    contract or fixture -- an entry order still working, or a filled entry
    whose position is still open (the rule of the pre-lock
    `cross_strategy_exposure` read, which stays as the early, cheap refusal).
    With `same_strategy_live`, also refuses when THIS strategy already has a
    live (working) entry on it. Returns a refusal dict or None."""
    from . import bettor_paper_limits as LIMITS
    if LIMITS.uses_owner_policy(o["account_id"]):
        return None
    acct, strat = o["account_id"], o.get("strategy")
    slug, fixture = o.get("us_market_slug"), o.get("fixture")
    rows = await conn.fetch(
        "SELECT DISTINCT strategy, group_id, us_market_slug, state "
        "  FROM paper_orders WHERE account_id=$1 AND role='ENTRY' "
        "   AND (us_market_slug = $2 OR ($3::text IS NOT NULL "
        "        AND fixture = $3)) "
        "   AND (state = ANY($4::text[]) OR filled_qty > 0)",
        acct, slug, fixture, list(OPEN_STATES))
    if not rows:
        return None
    others = [r for r in rows if r["strategy"] != strat]
    open_groups = None
    if others and any(r["state"] not in OPEN_STATES for r in others):
        open_groups = {p["group_id"] for p in await positions(conn, acct)
                       if p["open_qty"] > 1e-9}
    by = [{"strategy": r["strategy"], "group_id": r["group_id"],
           "us_market_slug": r["us_market_slug"], "state": r["state"]}
          for r in others if r["state"] in OPEN_STATES
          or r["group_id"] in (open_groups or set())]
    if by:
        return {"refusal": R_FIXTURE_OWNED, "under_lock": True, "by": by}
    if same_strategy_live:
        mine = [r for r in rows if r["strategy"] == strat
                and r["state"] in OPEN_STATES]
        if mine:
            return {"refusal": R_SAME_STRATEGY_LIVE, "under_lock": True,
                    "by": [{"group_id": r["group_id"], "state": r["state"]}
                           for r in mine]}
    return None


async def submit_order(conn, order: dict, *, caps: dict | None = None,
                       fee_fn=None, now: float | None = None,
                       locked_check=None, exclusive_fixture: bool = False,
                       one_live_entry_per_fixture: bool = False) -> dict:
    """RECORD A PAPER ORDER AND RESERVE ITS CASH, ATOMICALLY.

    One transaction: the account lock, the idempotency check, the caps, the
    order row, its SUBMITTED event (source SIMULATOR) and the ORDER_SUBMITTED
    ledger entry. A BUY reserves limit x qty + max fees; a SELL reserves the
    held inventory it would sell (never cash) and is refused when the group's
    uncommitted inventory is short. Idempotent on the order's key.

    `locked_check` (optional): an async callable (conn, order, reserve) that
    runs UNDER THE ACCOUNT LOCK after the caps and returns a refusal dict to
    refuse the order, or None. A strategy's own aggregate limits (e.g. the
    exploration strategy's total exposure and loss stop) are checked there,
    so two concurrent decisions can never both pass a limit only one fits.

    `exclusive_fixture` (ENTRY BUY orders): FIXTURE OWNERSHIP checked under
    the same lock (`fixture_owner_refusal`) -- no two strategies commit the
    same fixture, however their decisions interleave.
    `one_live_entry_per_fixture` adds this strategy's own live entry."""
    o = dict(order)
    at = float(now if now is not None else time.time())
    caps = dict(caps or {})
    key = str(o.get("idempotency_key") or "")
    if not key:
        return {"ok": False, "refusal": R_BAD_ORDER, "why": "no key"}
    o.setdefault("order_id", order_id_for(key))
    for k in ("order_id", "account_id", "session_id"):
        if not is_paper_id(o.get(k)):
            return {"ok": False, "refusal": R_NOT_PAPER, "field": k}
    try:
        qty, limit = D(o["qty"]), D(o["limit_price"])
    except Exception:                                           # noqa: BLE001
        return {"ok": False, "refusal": R_BAD_ORDER, "why": "qty/limit"}
    if qty <= 0 or not (0 < limit < 1):
        return {"ok": False, "refusal": R_BAD_ORDER, "qty": f(qty),
                "limit_price": f(limit)}
    acct = o["account_id"]
    async with conn.transaction():
        if await _lock(conn, acct) is None:
            return {"ok": False, "refusal": R_NO_ACCOUNT}
        prior = await conn.fetchrow(
            "SELECT * FROM paper_orders WHERE idempotency_key = $1", key)
        if prior is not None:
            if prior["account_id"] != acct:
                return {"ok": False,
                        "refusal": "IDEMPOTENCY_KEY_BELONGS_TO_ANOTHER_ACCOUNT"}
            return {"ok": True, "duplicate": True, "order": order_view(prior)}
        cs = await cash_state(conn, acct)
        reserve = Decimal(0)
        if o["direction"] == "BUY":
            reserve = reservation_for(qty, limit, at=at, fee_fn=fee_fn)
            chk = await _check_caps(conn, o, reserve=reserve, cs=cs,
                                    caps=caps)
            if chk:
                return dict(chk, ok=False, reservation_usd=f(reserve),
                            available_usd=f(cs["available"]))
            if o.get("role") == "ENTRY" and _LIMITS.uses_owner_policy(acct):
                held = await same_contract_held(
                    conn, acct, o.get("strategy"), o.get("us_market_slug"),
                    o.get("holding_side"))
                if held:
                    return {"ok": False, "refusal": R_SAME_CONTRACT_HELD,
                            "under_lock": True, "by": held,
                            "reservation_usd": f(reserve),
                            "available_usd": f(cs["available"])}
            if exclusive_fixture and o.get("role") == "ENTRY":
                chk = await fixture_owner_refusal(
                    conn, o, same_strategy_live=one_live_entry_per_fixture)
                if chk:
                    return dict(chk, ok=False, reservation_usd=f(reserve),
                                available_usd=f(cs["available"]))
            if locked_check is not None:
                chk = await locked_check(conn, o, reserve)
                if chk:
                    return dict(chk, ok=False, reservation_usd=f(reserve),
                                available_usd=f(cs["available"]))
        else:
            held = await held_uncommitted(
                conn, acct, group_id=o["group_id"],
                slug=o["us_market_slug"], holding_side=o["holding_side"])
            if held < qty:
                return {"ok": False, "refusal": R_NOT_HELD,
                        "held_uncommitted": f(held), "qty": f(qty)}
        vals = {k: o.get(k) for k in ORDER_COLUMNS}
        vals["label"] = json.dumps(o.get("label") or {}, default=str)
        vals["queue_basis"] = (None if o.get("queue_basis") is None else
                               json.dumps(o["queue_basis"], default=str))
        for k in ("decided_at", "eligible_at", "expires_at"):
            vals[k] = _ts(float(vals[k]))
        vals["qty"], vals["limit_price"] = qty, limit
        vals["wire_price"] = D(vals["wire_price"])
        if vals.get("queue_ahead_qty") is not None:
            vals["queue_ahead_qty"] = D(vals["queue_ahead_qty"])
        state = ("RESTING" if o["order_type"] == "RESTING"
                 else "PENDING_SIMULATION")
        cols = list(ORDER_COLUMNS) + ["reserved_usd",
                                      "reserved_remaining_usd", "state"]
        args = [vals[k] for k in ORDER_COLUMNS] + [reserve, reserve, state]
        if o.get("strategy"):
            # THE STRATEGY KEY (migration 182), only when the order names
            # one; otherwise the column default (the two-model label).
            cols.append("strategy")
            args.append(str(o["strategy"]))
        casts = {"label": "::jsonb", "queue_basis": "::jsonb"}
        ph = ",".join("$%d%s" % (i + 1, casts.get(c, ""))
                      for i, c in enumerate(cols))
        row = await conn.fetchrow(
            "INSERT INTO paper_orders (%s) VALUES (%s) RETURNING *"
            % (",".join(cols), ph), *args)
        await event(conn, order_id=o["order_id"], kind="SUBMITTED", at=at,
                    simulator_version=o["simulator_version"],
                    detail={"reservation_usd": f(reserve),
                            "state": state})
        if o["order_type"] == "RESTING":
            await event(conn, order_id=o["order_id"], kind="ACKNOWLEDGED",
                        at=at, simulator_version=o["simulator_version"],
                        detail={"resting": True,
                                "queue_ahead_qty": f(vals.get(
                                    "queue_ahead_qty"))})
        entry = None
        if reserve > 0:
            entry = await _append(
                conn, account_id=acct, kind=K_SUBMITTED,
                key="%s:%s" % (K_SUBMITTED, o["order_id"]),
                reserved_delta=reserve, session_id=o["session_id"],
                order_id=o["order_id"], group_id=o["group_id"],
                source=SRC_LEDGER, simulator_version=o["simulator_version"],
                detail={"role": o["role"], "qty": f(qty),
                        "limit_price": f(limit),
                        "reservation_is": "limit x qty + max fees; not a "
                                          "purchase"})
    return {"ok": True, "duplicate": False, "order": order_view(row),
            "ledger_entry": entry}


async def _check_caps(conn, o: dict, *, reserve: Decimal, cs: dict,
                      caps: dict) -> dict | None:
    acct = o["account_id"]
    from . import bettor_paper_limits as LIMITS
    caps = LIMITS.effective_caps(caps, acct, o.get("role"))
    if reserve > cs["available"]:
        return {"refusal": R_INSUFFICIENT}
    if caps.get("per_order_cap_usd") is not None and \
            reserve > D(caps["per_order_cap_usd"]):
        return {"refusal": R_PER_ORDER, "cap": caps["per_order_cap_usd"]}
    if o.get("role") == "ENTRY":
        frac = D(caps.get("hedge_reserve_fraction") or 0)
        keep = D(cs["cash"] * frac)
        if cs["available"] - reserve < keep:
            return {"refusal": R_HEDGE_RESERVE, "hedge_reserve_usd": f(keep)}
        if caps.get("max_concurrent_groups") is not None:
            n = await _open_groups(conn, acct)
            held_already = await conn.fetchval(
                "SELECT count(*) FROM paper_orders WHERE account_id=$1 "
                "   AND group_id=$2", acct, o["group_id"])
            if not held_already and n >= int(caps["max_concurrent_groups"]):
                return {"refusal": R_MAX_GROUPS, "open_groups": n}
    if caps.get("per_market_cap_usd") is not None:
        ex = await _exposure(conn, acct, slug=o["us_market_slug"])
        if ex + reserve > D(caps["per_market_cap_usd"]):
            return {"refusal": R_PER_MARKET, "exposure_usd": f(ex),
                    "cap": caps["per_market_cap_usd"]}
    if caps.get("per_fixture_cap_usd") is not None and o.get("fixture"):
        ex = await _exposure(conn, acct, fixture=o["fixture"])
        if ex + reserve > D(caps["per_fixture_cap_usd"]):
            return {"refusal": R_PER_FIXTURE, "exposure_usd": f(ex),
                    "cap": caps["per_fixture_cap_usd"]}
    return None


async def event(conn, *, order_id: str, kind: str, at: float,
                simulator_version: str, detail: dict | None = None,
                source: str = SRC_SIMULATOR) -> None:
    await conn.execute(
        "INSERT INTO paper_order_events (order_id, kind, event_source, "
        " simulator_version, at, detail) VALUES ($1,$2,$3,$4,$5,$6::jsonb)",
        order_id, kind, source, simulator_version, _ts(at),
        json.dumps(detail or {}, default=str))


def order_view(r) -> dict:
    if r is None:
        return {}
    d = dict(r)
    out = {}
    for k, v in d.items():
        if isinstance(v, Decimal):
            out[k] = float(v)
        elif hasattr(v, "timestamp"):
            out[k] = float(v.timestamp())
        elif k in ("label", "queue_basis"):
            out[k] = _j(v)
        else:
            out[k] = v
    out["remaining_qty"] = round(out["qty"] - out["filled_qty"], 6)
    out["data_label"] = DATA_LABEL
    return out


# ═════════════════════════════════════════════════════════════════════
# FILLS, RELEASES, SALES (called by the simulator inside its transaction)
# ═════════════════════════════════════════════════════════════════════

def fill_id_for(key: str) -> str:
    return "paperfill:" + hashlib.sha256(key.encode()).hexdigest()[:24]


async def apply_fill_locked(conn, *, order: dict, qty, price, wire_price,
                            fee, filled_at: float, basis: str,
                            book_obs_id=None, book_observed_at=None,
                            evidence: dict | None = None,
                            key: str, event_detail: dict | None = None
                            ) -> dict:
    """ONE SIMULATED FILL. The caller holds the transaction AND the account
    lock (`bettor_paper_simulator` does). Idempotent on `key`.

    `event_detail` is added to the FILL event's detail (never over the fill's
    own fields): the simulator names there the unreadable observations it
    skipped before the book this fill was taken on.

    BUY: FILL debits cost + fees of the filled qty and releases the filled
    share of the reservation; the fill that completes the order releases
    exactly what remains. SELL: SALE credits proceeds - fees."""
    prior = await conn.fetchrow(
        "SELECT * FROM paper_fills WHERE idempotency_key = $1", key)
    if prior is not None:
        return {"ok": True, "duplicate": True, "fill_id": prior["fill_id"]}
    cur = await conn.fetchrow(
        "SELECT * FROM paper_orders WHERE order_id = $1 FOR UPDATE",
        order["order_id"])
    if cur is None or cur["state"] not in OPEN_STATES:
        return {"ok": False, "refusal": R_ORDER_NOT_OPEN,
                "state": None if cur is None else cur["state"]}
    q, px, fe = D(qty), D(price), D(fee)
    remaining = D(cur["qty"]) - D(cur["filled_qty"])
    if q <= 0 or q > remaining:
        return {"ok": False, "refusal": R_OVERFILL, "qty": f(q),
                "remaining": f(remaining)}
    gross = D(q * px)
    fid = fill_id_for(key)
    completes = q == remaining
    await conn.execute(
        "INSERT INTO paper_fills (fill_id, idempotency_key, order_id, "
        " account_id, session_id, group_id, role, direction, holding_side, "
        " us_market_slug, fixture, label, qty, price, wire_price, fee_usd, "
        " gross_usd, book_obs_id, book_observed_at, filled_at, basis, "
        " evidence, simulator_version, strategy) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb,$13,$14,$15,"
        "        $16,$17,$18,$19,$20,$21,$22::jsonb,$23,$24)",
        fid, key, cur["order_id"], cur["account_id"], cur["session_id"],
        cur["group_id"], cur["role"], cur["direction"], cur["holding_side"],
        cur["us_market_slug"], cur["fixture"],
        json.dumps(_j(cur["label"]) or {}, default=str), q, px,
        D(wire_price), fe, gross, book_obs_id,
        (None if book_observed_at is None else _ts(book_observed_at)),
        _ts(filled_at), basis, json.dumps(evidence or {}, default=str),
        cur["simulator_version"],
        # THE ORDER'S STRATEGY (migration 182), so the position it builds
        # carries it.
        dict(cur).get("strategy") or DEFAULT_STRATEGY)
    release = Decimal(0)
    if cur["direction"] == "BUY":
        rem_res = D(cur["reserved_remaining_usd"])
        release = rem_res if completes else min(
            rem_res, D(D(cur["reserved_usd"]) * q / D(cur["qty"])))
        entry = await _append(
            conn, account_id=cur["account_id"], kind=K_FILL,
            key="%s:%s" % (K_FILL, fid), cash_delta=-(gross + fe),
            reserved_delta=-release, session_id=cur["session_id"],
            order_id=cur["order_id"], fill_id=fid, group_id=cur["group_id"],
            position_key_=position_key(
                account_id=cur["account_id"], group_id=cur["group_id"],
                slug=cur["us_market_slug"],
                holding_side=cur["holding_side"]),
            source=SRC_SIMULATOR, simulator_version=cur["simulator_version"],
            detail={"role": cur["role"], "qty": f(q), "price": f(px),
                    "fee_usd": f(fe), "basis": basis,
                    "reservation_released_usd": f(release)})
    else:
        entry = await _append(
            conn, account_id=cur["account_id"], kind=K_SALE,
            key="%s:%s" % (K_SALE, fid), cash_delta=gross - fe,
            session_id=cur["session_id"], order_id=cur["order_id"],
            fill_id=fid, group_id=cur["group_id"],
            position_key_=position_key(
                account_id=cur["account_id"], group_id=cur["group_id"],
                slug=cur["us_market_slug"],
                holding_side=cur["holding_side"]),
            source=SRC_SIMULATOR, simulator_version=cur["simulator_version"],
            detail={"role": cur["role"], "qty": f(q), "price": f(px),
                    "fee_usd": f(fe), "basis": basis,
                    "proceeds_are": "sale proceeds, never acquisition"})
    new_state = "FILLED" if completes else "PARTIALLY_FILLED"
    await conn.execute(
        "UPDATE paper_orders SET filled_qty = filled_qty + $2, state = $3, "
        " reserved_remaining_usd = reserved_remaining_usd - $4, "
        " terminal_at = CASE WHEN $3 = 'FILLED' THEN $5 ELSE terminal_at "
        "               END, "
        " terminal_reason = CASE WHEN $3 = 'FILLED' THEN 'FILLED' ELSE "
        "               terminal_reason END, updated_at = now() "
        " WHERE order_id = $1",
        cur["order_id"], q, new_state, release, _ts(filled_at))
    await event(conn, order_id=cur["order_id"], kind="FILL", at=filled_at,
                simulator_version=cur["simulator_version"],
                detail=dict(event_detail or {},
                            fill_id=fid, qty=f(q), price=f(px),
                            fee_usd=f(fe), basis=basis,
                            book_obs_id=book_obs_id))
    return {"ok": True, "duplicate": False, "fill_id": fid, "qty": f(q),
            "price": f(px), "fee_usd": f(fe), "state": new_state,
            "ledger_entry": entry, "first_fill": D(cur["filled_qty"]) == 0}


async def release_remainder_locked(conn, *, order_id: str, reason: str,
                                   at: float, state: str = "EXPIRED",
                                   detail: dict | None = None) -> dict:
    """CANCEL / EXPIRE THE UNFILLED REMAINDER: the order goes terminal and
    its remaining reservation is released. No cash credit. Caller holds the
    transaction and the account lock."""
    cur = await conn.fetchrow(
        "SELECT * FROM paper_orders WHERE order_id = $1 FOR UPDATE", order_id)
    if cur is None or cur["state"] not in OPEN_STATES:
        return {"ok": False, "refusal": R_ORDER_NOT_OPEN,
                "state": None if cur is None else cur["state"]}
    rem = D(cur["reserved_remaining_usd"])
    await conn.execute(
        "UPDATE paper_orders SET state = $2, reserved_remaining_usd = 0, "
        " terminal_at = $3, terminal_reason = $4, updated_at = now() "
        " WHERE order_id = $1", order_id, state, _ts(at), reason)
    await event(conn, order_id=order_id, kind=("CANCELED" if state ==
                                               "CANCELED" else "EXPIRED"),
                at=at, simulator_version=cur["simulator_version"],
                detail=dict(detail or {}, reason=reason,
                            released_usd=f(rem)))
    entry = None
    if rem > 0:
        entry = await _append(
            conn, account_id=cur["account_id"], kind=K_RELEASED,
            key="%s:%s" % (K_RELEASED, order_id), reserved_delta=-rem,
            session_id=cur["session_id"], order_id=order_id,
            group_id=cur["group_id"], source=SRC_LEDGER,
            simulator_version=cur["simulator_version"],
            detail={"reason": reason, "state": state,
                    "unfilled_qty": f(D(cur["qty"]) - D(cur["filled_qty"]))})
    return {"ok": True, "released_usd": f(rem), "ledger_entry": entry,
            "state": state}


async def release_remainder(conn, *, order_id: str, reason: str, at: float,
                            state: str = "EXPIRED",
                            detail: dict | None = None) -> dict:
    async with conn.transaction():
        acct = await conn.fetchval(
            "SELECT account_id FROM paper_orders WHERE order_id = $1",
            order_id)
        if acct is None:
            return {"ok": False, "refusal": R_ORDER_NOT_OPEN}
        await _lock(conn, acct)
        return await release_remainder_locked(
            conn, order_id=order_id, reason=reason, at=at, state=state,
            detail=detail)


# ═════════════════════════════════════════════════════════════════════
# POSITIONS, DERIVED FROM FILLS AND SETTLEMENTS
# ═════════════════════════════════════════════════════════════════════

POSITIONS_SQL = """
    WITH f AS (
        SELECT group_id, us_market_slug, holding_side,
               max(fixture) AS fixture, max(strategy) AS strategy,
               (array_agg(label ORDER BY filled_at))[1] AS label,
               sum(qty) FILTER (WHERE direction='BUY') AS bought,
               sum(gross_usd) FILTER (WHERE direction='BUY') AS buy_gross,
               sum(fee_usd) FILTER (WHERE direction='BUY') AS buy_fees,
               sum(qty) FILTER (WHERE direction='SELL') AS sold,
               sum(gross_usd) FILTER (WHERE direction='SELL') AS sale_gross,
               sum(fee_usd) FILTER (WHERE direction='SELL') AS sale_fees,
               min(filled_at) AS first_fill_at, max(filled_at) AS last_fill_at
          FROM paper_fills WHERE account_id = $1
         GROUP BY group_id, us_market_slug, holding_side),
    s AS (
        SELECT DISTINCT ON (position_key) position_key, qty, payout_usd,
               outcome, version, settled_at
          FROM paper_settlements WHERE account_id = $1
         ORDER BY position_key, version DESC)
    SELECT f.*, s.qty AS settled_qty, s.payout_usd, s.outcome AS settled,
           s.version AS settlement_version, s.settled_at
      FROM f LEFT JOIN s ON s.position_key =
           'paperpos:' || $1 || ':' || f.group_id || ':' || f.us_market_slug
           || ':' || f.holding_side
"""


def _position_from(account_id: str, r) -> dict:
    bought = D(r["bought"] or 0)
    buy_cost = D(r["buy_gross"] or 0) + D(r["buy_fees"] or 0)
    sold = D(r["sold"] or 0)
    proceeds = D(r["sale_gross"] or 0) - D(r["sale_fees"] or 0)
    settled = D(r["settled_qty"] or 0)
    payout = D(r["payout_usd"] or 0)
    avg = (buy_cost / bought) if bought > 0 else Decimal(0)
    open_qty = bought - sold - settled
    realized = (proceeds - D(avg * sold)) + (payout - D(avg * settled)
                                             if settled > 0 else Decimal(0))
    return {
        "position_key": position_key(
            account_id=account_id, group_id=r["group_id"],
            slug=r["us_market_slug"], holding_side=r["holding_side"]),
        "group_id": r["group_id"], "us_market_slug": r["us_market_slug"],
        "holding_side": r["holding_side"], "fixture": r["fixture"],
        "label": _j(r["label"]) or {},
        # ONE STRATEGY PER GROUP (migration 182): the entry's, for life.
        "strategy": (r.get("strategy") if hasattr(r, "get") else None)
        or DEFAULT_STRATEGY,
        "bought_qty": f(bought), "sold_qty": f(sold),
        "settled_qty": f(settled), "open_qty": f(open_qty),
        "avg_cost_per_contract_incl_fees": f(D(avg)),
        "acquisition_cost_usd": f(buy_cost),
        "buy_fees_usd": f(D(r["buy_fees"] or 0)),
        "sale_proceeds_net_usd": f(proceeds),
        "sale_fees_usd": f(D(r["sale_fees"] or 0)),
        "settlement": (None if r["settled"] is None else {
            "outcome": r["settled"], "payout_usd": f(payout),
            "version": r["settlement_version"],
            "settled_at": _epoch(r["settled_at"])}),
        "cost_basis_usd": f(D(avg * open_qty)),
        "realized_pnl_usd": f(D(realized)),
        "first_fill_at": _epoch(r["first_fill_at"]),
        "last_fill_at": _epoch(r["last_fill_at"])}


async def positions(conn, account_id: str, *,
                    include_closed: bool = False) -> list:
    rows = [_position_from(account_id, r)
            for r in await conn.fetch(POSITIONS_SQL, account_id)]
    return rows if include_closed else [p for p in rows
                                        if p["open_qty"] > 1e-9]


async def held_uncommitted(conn, account_id: str, *, group_id: str,
                           slug: str, holding_side: str) -> Decimal:
    """Held inventory minus what open SELL orders already commit."""
    pk = position_key(account_id=account_id, group_id=group_id, slug=slug,
                      holding_side=holding_side)
    held = next((D(p["open_qty"]) for p in await positions(conn, account_id)
                 if p["position_key"] == pk), Decimal(0))
    committed = D(await conn.fetchval(
        "SELECT coalesce(sum(qty - filled_qty), 0) FROM paper_orders "
        " WHERE account_id=$1 AND group_id=$2 AND us_market_slug=$3 "
        "   AND holding_side=$4 AND direction='SELL' "
        "   AND state = ANY($5::text[])", account_id, group_id, slug,
        holding_side, list(OPEN_STATES)))
    return held - committed


# ═════════════════════════════════════════════════════════════════════
# SETTLEMENT, EXACTLY ONCE, WITH CORRECTIONS
# ═════════════════════════════════════════════════════════════════════

async def settle(conn, *, account_id: str, group_id: str, slug: str,
                 holding_side: str, settlement_event_key: str, outcome: str,
                 evidence: dict, evidence_source: str, at: float,
                 session_id: str | None = None,
                 void_refund_per_contract=None,
                 price_per_contract=None) -> dict:
    """CREDIT A POSITION'S SETTLEMENT PAYOUT EXACTLY ONCE.

    `outcome` is WON (pays $1 per contract), LOST (0), VOID_REFUND (the
    purchase price back, only on a venue-declared void) or
    SETTLED_AT_VENUE_PRICE (the venue's OWN published settlement price per
    contract, passed explicitly as `price_per_contract` -- never inferred). Unique per position + settlement event: a second
    call with the same event is a no-op returning the first. A DIFFERENT
    outcome for the same event is a correction (`correct_settlement`)."""
    pk = position_key(account_id=account_id, group_id=group_id, slug=slug,
                      holding_side=holding_side)
    skey = "%s:%s" % (pk, settlement_event_key)
    async with conn.transaction():
        await _lock(conn, account_id)
        prior = await conn.fetchrow(
            "SELECT * FROM paper_settlements WHERE position_key=$1 AND "
            " settlement_event_key=$2 ORDER BY version DESC LIMIT 1", pk,
            settlement_event_key)
        if prior is not None:
            return {"ok": True, "duplicate": True,
                    "settlement_id": prior["settlement_id"],
                    "outcome": prior["outcome"]}
        pos = next((p for p in await positions(conn, account_id)
                    if p["position_key"] == pk), None)
        if pos is None or pos["open_qty"] <= 1e-9:
            return {"ok": False, "refusal": "NO_OPEN_POSITION_TO_SETTLE"}
        qty = D(pos["open_qty"])
        per = _payout_per(outcome, pos, void_refund_per_contract,
                          price_per_contract)
        payout = D(qty * per)
        sid = "paperset:" + hashlib.sha256(
            ("%s:1" % skey).encode()).hexdigest()[:24]
        await conn.execute(
            "INSERT INTO paper_settlements (settlement_id, account_id, "
            " position_key, settlement_event_key, version, group_id, "
            " us_market_slug, holding_side, qty, outcome, "
            " payout_per_contract, payout_usd, evidence, evidence_source, "
            " settled_at) VALUES ($1,$2,$3,$4,1,$5,$6,$7,$8,$9,$10,$11,"
            " $12::jsonb,$13,$14)",
            sid, account_id, pk, settlement_event_key, group_id, slug,
            holding_side, qty, outcome, per, payout,
            json.dumps(evidence, default=str), evidence_source, _ts(at))
        entry = await _append(
            conn, account_id=account_id, kind=K_SETTLEMENT,
            key="%s:%s" % (K_SETTLEMENT, skey), cash_delta=payout,
            session_id=session_id, group_id=group_id, position_key_=pk,
            settlement_key=skey, source=SRC_SETTLEMENT,
            detail={"outcome": outcome, "qty": f(qty),
                    "payout_per_contract": f(per),
                    "evidence_source": evidence_source,
                    "settlement_id": sid})
    return {"ok": True, "duplicate": False, "settlement_id": sid,
            "payout_usd": f(payout), "ledger_entry": entry}


def _payout_per(outcome: str, pos: dict, void_refund,
                price=None) -> Decimal:
    if outcome == "SETTLED_AT_VENUE_PRICE":
        # THE VENUE'S PUBLISHED PRICE, OR NOTHING. No default: a settlement
        # at an unpublished price is not a settlement.
        if price is None:
            raise ValueError("SETTLED_AT_VENUE_PRICE needs the venue's "
                             "published price per contract")
        p = D(price)
        if p < 0 or p > 1:
            raise ValueError("a venue price per contract is in [0, 1]")
        return p
    if outcome == "WON":
        return Decimal(1)
    if outcome == "LOST":
        return Decimal(0)
    if outcome == "VOID_REFUND":
        if void_refund is not None:
            return D(void_refund)
        bought = D(pos["bought_qty"] or 0)
        gross = D(pos["acquisition_cost_usd"]) - D(pos["buy_fees_usd"])
        return D(gross / bought) if bought > 0 else Decimal(0)
    raise ValueError("unknown settlement outcome %r" % outcome)


async def correct_settlement(conn, *, account_id: str, group_id: str,
                             slug: str, holding_side: str,
                             settlement_event_key: str, outcome: str,
                             evidence: dict, evidence_source: str,
                             at: float, session_id: str | None = None,
                             void_refund_per_contract=None) -> dict:
    """A CORRECTED SETTLEMENT: a new version of the settlement record and a
    CORRECTION ledger entry of the payout difference, pointing at the entry
    it corrects. The original stays."""
    pk = position_key(account_id=account_id, group_id=group_id, slug=slug,
                      holding_side=holding_side)
    skey = "%s:%s" % (pk, settlement_event_key)
    async with conn.transaction():
        await _lock(conn, account_id)
        prior = await conn.fetchrow(
            "SELECT * FROM paper_settlements WHERE position_key=$1 AND "
            " settlement_event_key=$2 ORDER BY version DESC LIMIT 1", pk,
            settlement_event_key)
        if prior is None:
            return {"ok": False, "refusal": "NOTHING_SETTLED_TO_CORRECT"}
        if prior["outcome"] == outcome:
            return {"ok": True, "duplicate": True, "unchanged": True}
        pos = {"bought_qty": None, "acquisition_cost_usd": 0,
               "buy_fees_usd": 0}
        allp = await positions(conn, account_id, include_closed=True)
        pos = next((p for p in allp if p["position_key"] == pk), pos)
        per = _payout_per(outcome, pos, void_refund_per_contract)
        qty = D(prior["qty"])
        payout = D(qty * per)
        diff = payout - D(prior["payout_usd"])
        ver = int(prior["version"]) + 1
        sid = "paperset:" + hashlib.sha256(
            ("%s:%d" % (skey, ver)).encode()).hexdigest()[:24]
        await conn.execute(
            "INSERT INTO paper_settlements (settlement_id, account_id, "
            " position_key, settlement_event_key, version, supersedes, "
            " group_id, us_market_slug, holding_side, qty, outcome, "
            " payout_per_contract, payout_usd, evidence, evidence_source, "
            " settled_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,"
            " $13,$14::jsonb,$15,$16)",
            sid, account_id, pk, settlement_event_key, ver,
            prior["settlement_id"], group_id, slug, holding_side, qty,
            outcome, per, payout, json.dumps(evidence, default=str),
            evidence_source, _ts(at))
        orig = await conn.fetchval(
            "SELECT seq FROM paper_ledger WHERE settlement_key = $1 "
            "   AND kind = 'SETTLEMENT'", skey)
        entry = await _append(
            conn, account_id=account_id, kind=K_CORRECTION,
            key="%s:%s:v%d" % (K_CORRECTION, skey, ver), cash_delta=diff,
            session_id=session_id, group_id=group_id, position_key_=pk,
            corrects_seq=orig, source=SRC_SETTLEMENT,
            detail={"from_outcome": prior["outcome"], "to_outcome": outcome,
                    "version": ver, "settlement_id": sid,
                    "payout_difference_usd": f(diff)})
    return {"ok": True, "settlement_id": sid, "version": ver,
            "payout_difference_usd": f(diff), "ledger_entry": entry}


# ═════════════════════════════════════════════════════════════════════
# MARKS AND THE ONE DERIVED-FIGURES FUNCTION
# ═════════════════════════════════════════════════════════════════════

async def latest_marks(conn, slugs: list, *, now: float) -> dict:
    """{slug: {holding_side: mark}} from the latest observed paper book per
    market. A market with no observation is UNAVAILABLE, never zero."""
    from . import bettor_book_snapshot as BS
    out: dict = {}
    if not slugs:
        return out
    rows = await conn.fetch(
        "SELECT DISTINCT ON (us_market_slug) us_market_slug, obs_id, "
        "       observed_at, bids, offers, error "
        "  FROM paper_book_observations "
        " WHERE us_market_slug = ANY($1::text[]) AND error IS NULL "
        " ORDER BY us_market_slug, observed_at DESC", list(set(slugs)))
    for r in rows:
        md = {"bids": _j(r["bids"]) or [], "offers": _j(r["offers"]) or []}
        at = _epoch(r["observed_at"])
        age = round(float(now) - at, 3)
        per = {}
        for side, intent in (("LONG", "ORDER_INTENT_BUY_LONG"),
                             ("SHORT", "ORDER_INTENT_BUY_SHORT")):
            ex = BS.exit_ladder(md, held_intent=intent)
            per[side] = ({"status": ("STALE" if age > MARK_STALE_AFTER_S
                                     else "OK"),
                          "price": ex["best_exit_price"],
                          "source": "paper_book_observations:%s"
                                    % r["obs_id"],
                          "observed_at": at, "age_s": age,
                          "stale": age > MARK_STALE_AFTER_S,
                          "method": MARK_METHOD}
                         if ex.get("ok") else
                         {"status": "UNAVAILABLE", "price": None,
                          "source": "paper_book_observations:%s"
                                    % r["obs_id"],
                          "observed_at": at, "age_s": age,
                          "why": ex.get("refusal") or "NO_EXIT_SIDE"})
        out[r["us_market_slug"]] = per
    return out


async def balances(conn, account_id: str = ACCOUNT_ID, *,
                   now: float | None = None,
                   marks: dict | None = None) -> dict:
    """THE DERIVED FIGURES -- the one function every read model and the live
    stream use. Cash-level figures are sums over the ledger; position value
    is marked from observed books with each mark's source, time and
    staleness; an unavailable mark is flagged and never counted as zero."""
    at = float(now if now is not None else time.time())
    acct = await conn.fetchrow(
        "SELECT * FROM paper_accounts WHERE account_id = $1", account_id)
    if acct is None:
        return {"ok": False, "refusal": R_NO_ACCOUNT,
                "data_label": DATA_LABEL, "labels": LABELS}
    cs = await cash_state(conn, account_id)
    pos = await positions(conn, account_id, include_closed=True)
    open_pos = [p for p in pos if p["open_qty"] > 1e-9]
    if marks is None:
        marks = await latest_marks(conn, [p["us_market_slug"]
                                          for p in open_pos], now=at)
    value = Decimal(0)
    unmarked, stale = [], []
    unmarked_cost = Decimal(0)
    unreal = Decimal(0)
    views = []
    for p in open_pos:
        m = dict(((marks.get(p["us_market_slug"]) or {}).get(
            p["holding_side"])) or {"status": "UNAVAILABLE", "price": None,
                                    "why": "NO_OBSERVED_BOOK_FOR_THIS_MARKET"})
        v = dict(p, mark=m)
        if m.get("price") is None:
            unmarked.append(p["position_key"])
            unmarked_cost += D(p["cost_basis_usd"])
            v.update(marked_value_usd=None, unrealized_pnl_usd=None)
        else:
            mv = D(D(p["open_qty"]) * D(m["price"]))
            value += mv
            u = mv - D(p["cost_basis_usd"])
            unreal += u
            v.update(marked_value_usd=f(mv), unrealized_pnl_usd=f(u))
            if m.get("stale"):
                stale.append(p["position_key"])
        views.append(v)
    complete = not unmarked
    realized = sum((D(p["realized_pnl_usd"]) for p in pos), Decimal(0))
    fees = D(await conn.fetchval(
        "SELECT coalesce(sum(fee_usd), 0) FROM paper_fills "
        " WHERE account_id = $1", account_id))
    last_at = cs["last_committed_at"]
    return {
        "ok": True, "version": VERSION, "account_id": account_id,
        "data_label": DATA_LABEL, "labels": LABELS,
        "currency": CURRENCY, "as_of": at,
        "last_updated_at": last_at, "last_sequence": cs["last_seq"],
        "starting_cash_usd": f(D(acct["starting_cash_usd"])),
        "cash_usd": f(cs["cash"]),
        "reserved_usd": f(cs["reserved"]),
        "reserved_is": "part of cash, not extra",
        "available_usd": f(cs["available"]),
        "open_positions": views,
        "open_position_value_usd": f(value) if complete else None,
        "open_position_value_marked_only_usd": f(value),
        "marks_complete": complete,
        "unmarked_positions": unmarked,
        "unmarked_cost_basis_usd": f(unmarked_cost),
        "stale_marks": stale,
        "total_equity_usd": f(cs["cash"] + value) if complete else None,
        "equity_excluding_unmarked_usd": f(cs["cash"] + value),
        "equity_basis": ("cash + marked open-position value" if complete
                         else "INCOMPLETE: %d open position(s) have no "
                              "available mark; total equity is not stated "
                              "and the marked-only figure excludes them"
                              % len(unmarked)),
        "realized_pnl_usd": f(realized),
        "unrealized_pnl_usd": f(unreal) if complete else None,
        "unrealized_pnl_marked_only_usd": f(unreal),
        "fees_paid_usd": f(fees),
        "mark_method": MARK_METHOD,
        "mark_stale_after_s": MARK_STALE_AFTER_S,
        "ledger_entries": cs["entries"],
        "ledger_consistent": cs["running_balance_agrees"],
        "real_money_submission": "DISABLED",
    }


async def ledger_after(conn, account_id: str = ACCOUNT_ID, *,
                       after_seq: int = 0, limit: int = 200) -> list:
    rows = await conn.fetch(
        "SELECT * FROM paper_ledger WHERE account_id = $1 AND seq > $2 "
        " ORDER BY seq LIMIT $3", account_id, int(after_seq), int(limit))
    return [entry_view(r) for r in rows]


async def latest_entries(conn, account_id: str = ACCOUNT_ID, *,
                         limit: int = 50) -> list:
    rows = await conn.fetch(
        "SELECT * FROM paper_ledger WHERE account_id = $1 "
        " ORDER BY seq DESC LIMIT $2", account_id, int(limit))
    return [entry_view(r) for r in rows]


def describe() -> dict:
    return {"version": VERSION, "account_id": ACCOUNT_ID,
            "account_key": ACCOUNT_KEY,
            "starting_cash_usd": float(STARTING_CASH_USD),
            "currency": CURRENCY, "data_label": DATA_LABEL, "labels": LABELS,
            "kinds": list(KINDS), "mark_method": MARK_METHOD}
