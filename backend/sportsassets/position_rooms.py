"""THE POSITION ROOM: ONE CORRELATED ECONOMIC POSITION ON ONE SCREEN.

Owner requirement (2026-10-04): "Management must be able to open EVERY active
position and understand the entire correlated economic position from one
screen." A room links ENTRY -> current holding -> correlated/complementary
instruments -> proposed / standing / partially filled / filled orders ->
remaining exposure -> current venue markets -> Xavier's assessment -> Archer's
execution assessment -> Audrey -> Karen -> game state.

WHAT A ROOM IS. Every leg (a held slice: paper group x venue market x held
side) and every order is placed by ESTABLISHED IDENTITY only:

  * the venue's own catalogue row for the market (`us_premap`, the row the
    entry path already resolved through `bettor_venue_native_identity`),
  * the market's settlement variable is a FULL-GAME / FULL-TIME WINNER
    (`bettor_venue_native_identity.FAMILY_WINNER_TYPES`), and
  * the held side's payout outcome is read off the venue's own rows: on a
    two-way contract (baseball / football) the row of THAT intent names its
    own team; on a per-side contract (soccer) the LONG pays the contract's
    team (or the draw) and the SHORT pays its complement.

Legs that share the venue event (`us_premap.event_slug`) under that winner
settlement variable are ONE room: YES Yankees and YES Red Sox on the same game
are the complementary legs of one economic position, and the scenario table
pays each outcome across both. A leg whose identity is NOT established is its
own UNGROUPED room keyed by its exact market, with the reason named -- never
merged by a title that looks alike. A single binary contract is still priced
exactly by its own two outcomes (the contract resolves YES / NO), because
that much is the contract's definition, not an inference.

BOOKS NEVER MIX. PAPER (simulated execution on live market data) and ACTUAL
are built separately and never summed. ACTUAL is per venue: Polymarket US
(execmirror_*) and Kalshi (kalshi_live_*), each its own room.

NOTHING IS RECOMPUTED THAT AN AGENT DECIDED. Xavier's recommendation,
alternatives, evidence state and thesis come from the persisted rows
(xavier_management_assessments, xavier_entry_theses, paper_xavier_reviews /
smalllive_reviews). What this module computes is arithmetic on recorded
quantities and prices: the average-cost position (the same method as
`bettor_paper_ledger._position_from`), the top-of-book mark (the paper
ledger's MARK_METHOD), the payout per settlement outcome, and the
consequence of a standing order filling at its own limit. Every derived
figure names its basis; a figure whose input is missing is null WITH the
reason.

READ-ONLY. Plain SELECTs inside a READ ONLY transaction with a statement
timeout (see the route module). Nothing here imports an order, venue-submit,
funded or paper-writer module; the authority test pins that.
"""
from __future__ import annotations

import datetime as _dt
import json
import time

from . import bettor_book_snapshot as BS
from . import bettor_venue_native_identity as VNI
from . import execution_evidence as EE
from . import order_state_truth as OST
from .open_position_canon import CANONICAL_OPEN_POSITIONS_SQL
from . import xavier_freshness as XF

VERSION = "POSITION_ROOMS_V1"

B_PAPER, B_ACTUAL = "PAPER", "ACTUAL"
V_PM, V_KALSHI = "POLYMARKET", "KALSHI"
BOOK_CODES = {(B_PAPER, V_PM): "PAPER",
              (B_ACTUAL, V_PM): "ACTUAL-POLYMARKET",
              (B_ACTUAL, V_KALSHI): "ACTUAL-KALSHI"}
CODE_BOOK = {v: k for k, v in BOOK_CODES.items()}
K_EVT, K_MKT = "EVT", "MKT"

#: Restated from bettor_paper_ledger (a paper writer module is not imported
#: here); tests/test_position_rooms.py pins each to its source.
PAPER_ACCOUNT_ID = "paper_acct_main"
MARK_STALE_AFTER_S = 300.0
MARK_METHOD = ("TOP_OF_BOOK_EXIT_PRICE: what closing one contract would "
               "receive at the best displayed level of the side a close "
               "consumes (bettor_book_snapshot.exit_ladder), from the paper "
               "path's latest observed book; no depth adjustment")
#: bettor_fixture_store.MAX_ROW_AGE_S / bettor_soccer_fixture.MAX_ROW_AGE_S
GAME_STATE_MAX_AGE_S = 300.0
#: paper_sessions.config.cadence.xavier_backstop_s default, and
#: execmirror.MANAGEMENT_EVERY_S (pinned by test; execmirror is an order
#: module and is NOT imported).
PAPER_BACKSTOP_DEFAULT_S = 60.0
ACTUAL_MANAGEMENT_EVERY_S = 60.0
#: A Derek ENTER decision with no order yet is PROPOSED only inside this
#: window; after it, an un-ordered decision is not shown as live.
PROPOSED_WINDOW_S = 900.0
#: Bounds on what one read loads.
MAX_GROUPS = 400
MAX_ORDERS = 4000
STATEMENT_TIMEOUT_MS = 8000

# ── CANONICAL ORDER STATES: the ONE shared mapping (order_state_truth) ──
# Every raw paper / mirror / Kalshi state is mapped by
# order_state_truth.order_state -- the single source of truth -- to one of
# the nine canonical states (or the explicit UNKNOWN). A cancel requested
# but not confirmed is CANCEL_PENDING: it can still fill. A mirror row
# excluded before submission: REJECTED with sub_state
# EXCLUDED_BEFORE_SUBMISSION.
S_PROPOSED, S_SUBMITTED, S_UNKNOWN = OST.PROPOSED, OST.SUBMITTED, OST.UNKNOWN
S_RESTING, S_PARTIAL, S_FILLED = OST.RESTING, OST.PARTIAL, OST.FILLED
S_CANCELLED, S_EXPIRED, S_REJECTED = (OST.CANCELLED, OST.EXPIRED,
                                      OST.REJECTED)
S_CANCEL_PENDING = OST.CANCEL_PENDING
LIVE_STATES = OST.LIVE_STATES
#: An order that can still fill without a new decision.
STANDING_STATES = OST.STANDING_STATES

PAPER_STATE_MAP = {r: st for r, (st, _s) in
                   OST.RAW_MAPS[OST.SRC_PAPER].items()}
#: execmirror_orders and kalshi_live_intents share one state machine
#: (migrations 192 / 196, kalshi_orders.TRANSITIONS).
MIRROR_STATE_MAP = {r: st for r, (st, _s) in
                    OST.RAW_MAPS[OST.SRC_MIRROR].items()}
PAPER_OPEN_RAW = ("PENDING_SIMULATION", "RESTING", "PARTIALLY_FILLED",
                  "CANCEL_PENDING")
MIRROR_OPEN_RAW = ("PLANNED", "SUBMITTING", "UNKNOWN", "OPEN",
                   "PARTIALLY_FILLED", "CANCEL_REQUESTED")

STATE_MEANING = dict(OST.MEANING)

# ── NAMED REASONS ─────────────────────────────────────────────────────
R_NO_PREMAP = "NO_VENUE_CATALOGUE_ROW_FOR_THIS_MARKET"
R_NOT_WINNER = "SETTLEMENT_VARIABLE_IS_NOT_A_FULL_GAME_WINNER"
R_SIDE_ROW = "VENUE_ROW_FOR_THIS_SIDE_NOT_FOUND"
R_TEAM_NOT_PARTICIPANT = "SIDE_TEAM_IS_NOT_ONE_OF_THE_TWO_EVENT_PARTICIPANTS"
R_PARTICIPANTS = "EVENT_DOES_NOT_NAME_EXACTLY_TWO_PARTICIPANTS"
R_TWO_WAY_SIDES = "TWO_WAY_CONTRACT_SIDES_NOT_ESTABLISHED"
R_NO_EVENT = "NO_VENUE_EVENT_IDENTITY_ON_THE_CATALOGUE_ROW"
R_KALSHI_SLUG = "KALSHI_INTENT_CARRIES_NO_MIRRORED_POLYMARKET_SLUG"
R_NO_BOOK = "NO_OBSERVED_BOOK_FOR_THIS_MARKET"
R_BOOK_SIDE = "THE_SIDE_THIS_PRICE_NEEDS_IS_EMPTY_OR_UNREADABLE"
R_KALSHI_BOOK = ("NO_PERSISTED_KALSHI_ORDER_BOOK: the Polymarket book is a "
                 "different venue and is never used for a Kalshi leg")
R_NO_LIMIT = "THE_ORDER_CARRIES_NO_LIMIT_PRICE"
R_NO_OUTCOMES = "SETTLEMENT_OUTCOMES_NOT_ESTABLISHED_FOR_THIS_ROOM"
R_FEES_HYPO = ("FEES_NOT_ESTIMATED_FOR_A_HYPOTHETICAL_FILL: maker/taker and "
               "the fill date are unknown until it fills")
R_ARCHER = "ARCHER_NOT_DEPLOYED"
R_NO_SCORE = ("NO_AUTHORITATIVE_LIVE_SCORE_SOURCE: the league schedule read "
              "(bettor_fixture_metadata.parse_games / bettor_soccer_fixture) "
              "carries the event STATE but no score, inning, period or clock; "
              "the PinnAPI feed carries prices only; the venue market data "
              "carries no event-state object (bettor_progress_providers)")
R_NO_GAME_ROW = "NO_LEAGUE_REPORTED_EVENT_STATE_ROW_FOR_THIS_EVENT"
R_NO_XAVIER = "NO_XAVIER_ASSESSMENT_RECORDED_FOR_THIS_POSITION"
R_206 = "MIGRATION_206_NOT_APPLIED"


# ═════════════════════════════════════════════════════════════════════
# SMALL HELPERS (pure)
# ═════════════════════════════════════════════════════════════════════

def _f(v, nd: int = 6):
    try:
        return None if v is None else round(float(v), nd)
    except (TypeError, ValueError):
        return None


def _epoch(v):
    if v is None:
        return None
    if hasattr(v, "timestamp"):
        return float(v.timestamp())
    if isinstance(v, str):
        try:
            return _dt.datetime.fromisoformat(
                v.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def iso(v):
    e = _epoch(v)
    if e is None:
        return None
    return _dt.datetime.fromtimestamp(e, _dt.timezone.utc).isoformat()


def _j(v):
    if isinstance(v, (str, bytes)):
        try:
            return json.loads(v)
        except (TypeError, ValueError):
            return None
    return v


def held_price(wire, side: str):
    """A venue (long / wire) price in the HELD side's space: a long holds
    the price, a short its complement."""
    w = _f(wire)
    if w is None:
        return None
    return round(w if side == "LONG" else 1.0 - w, 6)


def side_of_intent(intent) -> str | None:
    s = str(intent or "").upper()
    if s.endswith("_LONG"):
        return "LONG"
    if s.endswith("_SHORT"):
        return "SHORT"
    return None


def direction_of_intent(intent) -> str | None:
    s = str(intent or "").upper()
    if "_BUY_" in s or s.startswith("BUY"):
        return "BUY"
    if "_SELL_" in s or s.startswith("SELL"):
        return "SELL"
    return None


def freshness(at, now: float, max_age_s: float) -> dict:
    a = _epoch(at)
    if a is None:
        return {"at": None, "age_s": None, "state": "UNAVAILABLE"}
    age = round(max(0.0, float(now) - a), 1)
    return {"at": iso(a), "age_s": age,
            "state": "FRESH" if age <= max_age_s else "STALE",
            "max_age_s": max_age_s}


def group_key(book: str, venue: str, kind: str, ident: str) -> str:
    return "%s:%s:%s" % (BOOK_CODES[(book, venue)], kind, ident)


def parse_group_key(key: str) -> dict | None:
    parts = str(key or "").split(":", 2)
    if len(parts) != 3 or parts[0] not in CODE_BOOK \
            or parts[1] not in (K_EVT, K_MKT) or not parts[2]:
        return None
    book, venue = CODE_BOOK[parts[0]]
    return {"book": book, "venue": venue, "kind": parts[1],
            "ident": parts[2]}


def canonical_state(raw_state, *, table: str, filled_qty=None) -> str:
    """order_state_truth.canonical_order_state: an unmapped raw state is the
    explicit UNKNOWN, never FILLED."""
    return OST.canonical_order_state(raw_state, source=table,
                                     filled_qty=filled_qty)


# ═════════════════════════════════════════════════════════════════════
# 1 · IDENTITY (pure): which outcome does each held side pay on?
# ═════════════════════════════════════════════════════════════════════

WINNER_TYPES = frozenset(t for ts in VNI.FAMILY_WINNER_TYPES.values()
                         for t in ts)
DRAW = "DRAW"
VOID = "VOID"


def _display(row: dict) -> str | None:
    from . import market_labels as ML
    return ML._team_display(row)


def _row_team_key(row: dict) -> str | None:
    t = str(row.get("team_name") or "").strip().lower()
    return t or None


def event_participants(event_rows: list) -> dict:
    """The event's two participants from the venue's own per-side team
    fields, and whether a draw contract is listed. Pure."""
    teams: dict[str, dict] = {}
    has_draw = False
    for r in event_rows:
        if str(r.get("sports_type") or "") not in WINNER_TYPES:
            continue
        k = _row_team_key(r)
        if k is None:
            if str(r.get("market_slug") or "").endswith("-draw"):
                has_draw = True
            continue
        teams.setdefault(k, {"key": k, "name": _display(r) or k.title(),
                             "abbr": r.get("team_abbr"),
                             "team_id": r.get("team_id"),
                             "league": r.get("team_league")})
    if any(str(r.get("sports_type") or "").startswith("soccer")
           for r in event_rows):
        has_draw = True
    return {"teams": list(teams.values()), "has_draw": has_draw}


def resolve_identity(slug: str, side: str, premap_by_slug: dict,
                     premap_by_event: dict) -> dict:
    """The established identity of one held side, or UNGROUPED with the
    reason. Pure; never raises."""
    out = {"status": "UNGROUPED", "reason": None, "slug": slug,
           "side": side, "event_slug": None, "event_title": None,
           "settlement_class": None, "pays_on": None, "label": None,
           "outcomes": None, "contract_shape": None, "game_start": None,
           "sports_type": None,
           "basis": ("us_premap (the venue's own catalogue rows) via "
                     "bettor_venue_native_identity.FAMILY_WINNER_TYPES")}
    rows = premap_by_slug.get(str(slug or "").lower()) or []
    if not rows:
        out["reason"] = R_NO_PREMAP
        return out
    st = str(rows[0].get("sports_type") or "")
    out["sports_type"] = st or None
    ev = rows[0].get("event_slug")
    out["event_slug"] = ev
    out["event_title"] = rows[0].get("event_title")
    out["game_start"] = iso(rows[0].get("game_start"))
    if st not in WINNER_TYPES:
        out["reason"] = R_NOT_WINNER
        out["settlement_class"] = "MARKET:%s" % slug
        return out
    if not ev:
        out["reason"] = R_NO_EVENT
        return out
    parts = event_participants(premap_by_event.get(ev) or rows)
    if len(parts["teams"]) != 2:
        out["reason"] = R_PARTICIPANTS
        out["participants_seen"] = [t["name"] for t in parts["teams"]]
        return out
    keys = [t["key"] for t in parts["teams"]]
    outcomes = list(keys) + ([DRAW] if parts["has_draw"] else [])
    want = "ORDER_INTENT_BUY_%s" % side
    srow = next((r for r in rows if str(r.get("intent")) == want), None)
    if srow is None:
        out["reason"] = R_SIDE_ROW
        return out
    sn = str(srow.get("side_norm") or "").strip().lower()
    if sn in ("yes", "no"):
        # PER-SIDE CONTRACT: the contract names one team (or the draw);
        # the LONG pays it, the SHORT pays every other played outcome.
        tk = _row_team_key(srow)
        if tk is None and not str(slug).endswith("-draw"):
            out["reason"] = R_SIDE_ROW
            return out
        subject = tk if tk is not None else DRAW
        if subject != DRAW and subject not in keys:
            out["reason"] = R_TEAM_NOT_PARTICIPANT
            return out
        pays = [subject] if side == "LONG" else [o for o in outcomes
                                                 if o != subject]
        out["contract_shape"] = "PER_SIDE"
    else:
        # TWO-WAY CONTRACT: one LONG row and one SHORT row, each naming its
        # own participant; that pairing IS the side semantics.
        other = next((r for r in rows if str(r.get("intent")) ==
                      "ORDER_INTENT_BUY_%s" % (
                          "SHORT" if side == "LONG" else "LONG")), None)
        tk, ok_ = _row_team_key(srow), _row_team_key(other or {})
        if tk is None or ok_ is None or tk == ok_:
            out["reason"] = R_TWO_WAY_SIDES
            return out
        if tk not in keys or ok_ not in keys:
            out["reason"] = R_TEAM_NOT_PARTICIPANT
            return out
        pays = [tk]
        out["contract_shape"] = "TWO_WAY"
    names = {t["key"]: t["name"] for t in parts["teams"]}
    names[DRAW] = "Draw"

    def _lbl(o):
        return names.get(o, o)
    out.update(status="ESTABLISHED", reason=None,
               settlement_class="WINNER_FULL_GAME",
               pays_on=pays, outcomes=outcomes,
               outcome_labels={o: _lbl(o) for o in outcomes},
               teams=parts["teams"],
               label=("YES " + _lbl(pays[0]) if len(pays) == 1 else
                      "NOT " + " / ".join(_lbl(o) for o in outcomes
                                          if o not in pays)))
    return out


def market_outcomes(slug: str) -> dict:
    """An UNGROUPED room's outcomes: the binary contract itself (exact)."""
    return {"outcomes": ["RESOLVES_YES", "RESOLVES_NO"],
            "outcome_labels": {"RESOLVES_YES": "Contract resolves YES "
                                               "(LONG pays)",
                               "RESOLVES_NO": "Contract resolves NO "
                                              "(SHORT pays)"},
            "basis": ("BINARY_CONTRACT_DEFINITION: a LONG pays when %s "
                      "resolves YES, a SHORT when it resolves NO" % slug)}


def pays_on_for(leg_identity: dict, room_kind: str, side: str) -> list | None:
    if room_kind == K_EVT:
        return leg_identity.get("pays_on")
    return ["RESOLVES_YES"] if side == "LONG" else ["RESOLVES_NO"]


# ═════════════════════════════════════════════════════════════════════
# 2 · MARKET (pure): top of book in the held side's space
# ═════════════════════════════════════════════════════════════════════

def top_of_book(obs: dict | None, side: str, *, now: float,
                venue: str = V_PM) -> dict:
    """Bid (what a close receives), ask (what a buy pays) and the mark (the
    bid: the paper ledger's MARK_METHOD), all in the held side's price
    space, with the observation's age. Pure."""
    out = {"bid": None, "ask": None, "mark": None, "bid_qty": None,
           "ask_qty": None, "source": None, "observed_at": None,
           "age_s": None, "freshness": "UNAVAILABLE", "reason": None,
           "mark_method": MARK_METHOD}
    if venue == V_KALSHI:
        out["reason"] = R_KALSHI_BOOK
        return out
    if not obs:
        out["reason"] = R_NO_BOOK
        return out
    md = {"bids": _j(obs.get("bids")) or [],
          "offers": _j(obs.get("offers")) or []}
    intent = "ORDER_INTENT_BUY_%s" % side
    acq = BS.acquisition_ladder(md, intent=intent)
    ext = BS.exit_ladder(md, held_intent=intent)
    lv_a = (acq.get("levels") or []) if acq.get("ok") else []
    lv_e = (ext.get("levels") or []) if ext.get("ok") else []
    if lv_a:
        out["ask"] = _f(lv_a[0].get("acquisition_price"))
        out["ask_qty"] = _f(lv_a[0].get("qty"))
    if lv_e:
        out["bid"] = _f(lv_e[0].get("exit_price"))
        out["bid_qty"] = _f(lv_e[0].get("qty"))
    out["mark"] = out["bid"]
    fr = freshness(obs.get("observed_at"), now, MARK_STALE_AFTER_S)
    out.update(source="paper_book_observations#%s (%s)" % (
        obs.get("obs_id"), obs.get("source") or "read-only market data"),
        observed_at=fr["at"], age_s=fr["age_s"], freshness=fr["state"],
        market_state=obs.get("market_state"))
    if out["bid"] is None or out["ask"] is None:
        out["reason"] = R_BOOK_SIDE
    return out


def distance_to_threshold(direction: str, limit, tob: dict) -> dict:
    """How far the market must move for the order's limit to be reached:
    a BUY needs the ask at or below its limit, a SELL the bid at or above."""
    lim = _f(limit)
    if lim is None:
        return {"distance": None, "reason": R_NO_LIMIT}
    if direction == "BUY":
        ref, basis = tob.get("ask"), "ASK_MINUS_LIMIT"
        if ref is None:
            return {"distance": None, "reason": tob.get("reason") or R_NO_BOOK,
                    "basis": basis}
        d = round(float(ref) - lim, 6)
        need = "ask must fall %.1f¢" % (d * 100) if d > 0 else None
    else:
        ref, basis = tob.get("bid"), "LIMIT_MINUS_BID"
        if ref is None:
            return {"distance": None, "reason": tob.get("reason") or R_NO_BOOK,
                    "basis": basis}
        d = round(lim - float(ref), 6)
        need = "bid must rise %.1f¢" % (d * 100) if d > 0 else None
    return {"distance": d, "basis": basis, "reference_price": _f(ref),
            "crossable_now": d <= 0, "needs": need or "AT_OR_THROUGH_NOW",
            "book_freshness": tob.get("freshness")}


# ═════════════════════════════════════════════════════════════════════
# 3 · HOLDINGS (pure): the average-cost slice, as the paper ledger has it
# ═════════════════════════════════════════════════════════════════════

def slice_from_fills(fills: list, settlement: dict | None = None) -> dict:
    """bettor_paper_ledger._position_from on held-side prices: average cost
    INCLUDING buy fees, realised = sale proceeds net of fees - avg x sold
    (+ settlement payout - avg x settled)."""
    bought = sum(float(f["qty"]) for f in fills if f["direction"] == "BUY")
    buy_gross = sum(float(f["qty"]) * float(f["price"]) for f in fills
                    if f["direction"] == "BUY")
    fee_known = all(f.get("fee_usd") is not None for f in fills)
    buy_fees = sum(float(f.get("fee_usd") or 0) for f in fills
                   if f["direction"] == "BUY")
    sold = sum(float(f["qty"]) for f in fills if f["direction"] == "SELL")
    sale_gross = sum(float(f["qty"]) * float(f["price"]) for f in fills
                     if f["direction"] == "SELL")
    sale_fees = sum(float(f.get("fee_usd") or 0) for f in fills
                    if f["direction"] == "SELL")
    settled = float((settlement or {}).get("qty") or 0)
    payout = float((settlement or {}).get("payout_usd") or 0)
    cost = buy_gross + buy_fees
    avg = cost / bought if bought > 0 else 0.0
    avg_gross = buy_gross / bought if bought > 0 else 0.0
    open_qty = bought - sold - settled
    realized = (sale_gross - sale_fees - avg * sold) + (
        payout - avg * settled if settled > 0 else 0.0)
    ats = [_epoch(f.get("at")) for f in fills if _epoch(f.get("at"))]
    return {"bought_qty": _f(bought), "sold_qty": _f(sold),
            "settled_qty": _f(settled), "open_qty": _f(open_qty),
            "avg_cost_incl_fees": _f(avg) if bought > 0 else None,
            "avg_entry_price": _f(avg_gross) if bought > 0 else None,
            "acquisition_cost_usd": _f(cost),
            "buy_fees_usd": _f(buy_fees), "sale_fees_usd": _f(sale_fees),
            "fees_usd": _f(buy_fees + sale_fees),
            "fees_known": fee_known,
            "sale_proceeds_net_usd": _f(sale_gross - sale_fees),
            "cost_basis_usd": _f(avg * open_qty),
            "realized_pnl_usd": _f(realized),
            "settlement": settlement,
            "first_fill_at": iso(min(ats)) if ats else None,
            "last_fill_at": iso(max(ats)) if ats else None,
            "method": ("AVERAGE_COST_INCL_BUY_FEES "
                       "(bettor_paper_ledger._position_from)")}


# ═════════════════════════════════════════════════════════════════════
# 4 · SCENARIOS AND "IF IT FILLS" (pure)
# ═════════════════════════════════════════════════════════════════════

def _book_state(legs: list) -> list:
    """The mutable working copy a scenario applies hypothetical fills to."""
    return [{"leg_key": lg["leg_key"], "pays_on": lg.get("pays_on"),
             "open_qty": float(lg["holding"]["open_qty"] or 0),
             "cost_basis": float(lg["holding"]["cost_basis_usd"] or 0),
             "avg_gross": float(lg["holding"].get("avg_entry_price") or 0),
             "gross_basis": float(lg["holding"]["open_qty"] or 0) * float(
                 lg["holding"].get("avg_entry_price") or 0),
             "realized": float(lg["holding"]["realized_pnl_usd"] or 0)}
            for lg in legs]


def _apply_fill(state: list, *, leg_key: str, pays_on, direction: str,
                qty: float, price: float) -> dict:
    """One hypothetical fill at `price` (held space, gross of fees: the fee
    of a fill that has not happened is not known). Returns the realised
    P&L of the filled part."""
    s = next((x for x in state if x["leg_key"] == leg_key), None)
    if s is None:
        s = {"leg_key": leg_key, "pays_on": pays_on, "open_qty": 0.0,
             "cost_basis": 0.0, "avg_gross": 0.0, "gross_basis": 0.0,
             "realized": 0.0}
        state.append(s)
    if direction == "BUY":
        s["open_qty"] += qty
        s["cost_basis"] += qty * price
        s["gross_basis"] += qty * price
        return {"realized_usd": 0.0, "qty_applied": qty, "capped": False}
    q = min(qty, s["open_qty"])
    avg = s["cost_basis"] / s["open_qty"] if s["open_qty"] > 0 else 0.0
    avg_g = s["gross_basis"] / s["open_qty"] if s["open_qty"] > 0 else 0.0
    realized = q * (price - avg)
    s["open_qty"] -= q
    s["cost_basis"] -= q * avg
    s["gross_basis"] -= q * avg_g
    s["realized"] += realized
    return {"realized_usd": round(realized, 6), "qty_applied": q,
            "capped": q < qty}


def payout_table(state: list, outcomes: list, probs: dict | None = None
                 ) -> dict:
    """Per settlement outcome: payout of the held contracts, P&L from here
    (payout - remaining cost basis) and total (with realised). VOID refunds
    the purchase price (bettor_paper_ledger._payout_per VOID_REFUND)."""
    rows = []
    realized = sum(s["realized"] for s in state)
    basis = sum(s["cost_basis"] for s in state)
    for o in list(outcomes) + [VOID]:
        if o == VOID:
            pay = sum(s["gross_basis"] for s in state)
        else:
            pay = sum(s["open_qty"] for s in state
                      if s.get("pays_on") and o in s["pays_on"])
        rows.append({"outcome": o, "payout_usd": round(pay, 6),
                     "pnl_from_here_usd": round(pay - basis, 6),
                     "pnl_total_usd": round(pay - basis + realized, 6)})
    played = [r for r in rows if r["outcome"] != VOID]
    expected = None
    if probs and probs.get("p") and played and all(
            r["outcome"] in probs["p"] for r in played):
        expected = round(sum(probs["p"][r["outcome"]] * r["pnl_total_usd"]
                             for r in played), 6)
    worst = min(played, key=lambda r: r["pnl_total_usd"]) if played else None
    best = max(played, key=lambda r: r["pnl_total_usd"]) if played else None
    return {"rows": rows, "remaining_cost_basis_usd": round(basis, 6),
            "realized_usd": round(realized, 6),
            "worst": worst and {"outcome": worst["outcome"],
                                "pnl_total_usd": worst["pnl_total_usd"]},
            "best": best and {"outcome": best["outcome"],
                              "pnl_total_usd": best["pnl_total_usd"]},
            "capital_at_risk_usd": (round(max(0.0, -min(
                r["pnl_from_here_usd"] for r in played)), 6)
                if played else None),
            "locked_pnl_usd": (min(r["pnl_total_usd"] for r in played)
                               if played else None),
            "locked_basis": ("the P&L every played outcome pays at least "
                             "(min over outcomes); the rest is conditional "
                             "on the outcome"),
            "expected_pnl_usd": expected,
            "expected_basis": (None if expected is None else
                               "sum of p(outcome) x P&L; p from %s (%s, %s s "
                               "old)" % (probs.get("source"),
                                         probs.get("evidence_state"),
                                         probs.get("age_now_s"))),
            "expected_reason": (None if expected is not None else
                                (probs or {}).get("reason")
                                or "NO_PROBABILITY_FOR_EVERY_OUTCOME"),
            "void_basis": ("VOID_REFUND returns the purchase price "
                           "(gross average entry x held), per "
                           "bettor_paper_ledger._payout_per")}


def remaining_qty(o: dict) -> float:
    return max(0.0, float(o.get("qty") or 0) - float(o.get("filled_qty") or 0))


def if_it_fills(order: dict, legs: list, outcomes: list | None,
                probs: dict | None = None) -> dict:
    """THE ECONOMIC CONSEQUENCE IF THIS STANDING ORDER FILLS at its own
    limit for its whole remaining quantity. Null with the reason when an
    input is missing."""
    out = {"order_ref": order["order_ref"], "state": order["state"],
           "remaining_qty": _f(remaining_qty(order)),
           "at_price": order.get("limit"), "fees": R_FEES_HYPO,
           "available": False, "reason": None}
    if order.get("limit") is None:
        out["reason"] = R_NO_LIMIT
        return out
    if not outcomes:
        out["reason"] = R_NO_OUTCOMES
        return out
    if not order.get("pays_on"):
        out["reason"] = order.get("identity_reason") or R_NO_OUTCOMES
        return out
    q = remaining_qty(order)
    if q <= 0:
        out["reason"] = "NOTHING_REMAINS_TO_FILL"
        return out
    before = payout_table(_book_state(legs), outcomes, probs)
    st = _book_state(legs)
    got = _apply_fill(st, leg_key=order["leg_key"], pays_on=order["pays_on"],
                      direction=order["direction"], qty=q,
                      price=float(order["limit"]))
    after = payout_table(st, outcomes, probs)
    leg = next((x for x in st if x["leg_key"] == order["leg_key"]), None)
    lq = leg["open_qty"] if leg else 0.0
    out["resulting_inventory"] = {
        "leg_key": order["leg_key"], "open_qty": _f(lq),
        "avg_basis_per_contract": (_f(leg["cost_basis"] / lq)
                                   if leg and lq > 1e-9 else None),
        "cost_basis_usd": _f(leg["cost_basis"]) if leg else 0.0,
        "basis": ("average cost incl. recorded fees; the hypothetical fill "
                  "is added gross")}
    out["contracts_paying_on_after"] = {
        o: _f(sum(x["open_qty"] for x in st
                  if x.get("pays_on") and o in x["pays_on"]))
        for o in outcomes}
    out.update(
        available=True,
        realized_on_fill_usd=got["realized_usd"],
        realized_basis=("OPENING_FILL_REALISES_NOTHING"
                        if order["direction"] == "BUY" else
                        "QTY x (LIMIT - AVERAGE COST INCL FEES)"),
        sell_capped_to_held=got["capped"],
        qty_applied=_f(got["qty_applied"]),
        net_exposure_before=_net(before), net_exposure_after=_net(after),
        worst_after=after["worst"], best_after=after["best"],
        capital_at_risk_after_usd=after["capital_at_risk_usd"],
        remaining_exposure_after_usd=after["capital_at_risk_usd"],
        locked_pnl_after_usd=after["locked_pnl_usd"],
        locked_pnl_before_usd=before["locked_pnl_usd"],
        expected_pnl_before_usd=before["expected_pnl_usd"],
        expected_pnl_after_usd=after["expected_pnl_usd"],
        expected_reason=after["expected_reason"],
        expected_basis=after["expected_basis"],
        scenario_after=after["rows"])
    if got["capped"]:
        out["note"] = ("the order's remainder exceeds the held quantity of "
                       "its leg; applied only up to what is held")
    return out


def _net(tbl: dict) -> dict:
    return {"worst": tbl["worst"], "best": tbl["best"],
            "locked_pnl_usd": tbl["locked_pnl_usd"],
            "expected_pnl_usd": tbl["expected_pnl_usd"],
            "capital_at_risk_usd": tbl["capital_at_risk_usd"],
            "remaining_cost_basis_usd": tbl["remaining_cost_basis_usd"]}


def executable_exit(legs: list) -> dict:
    """WHAT CLOSING THE HELD CONTRACTS WOULD RECEIVE NOW at the best
    displayed exit level of each open leg (no depth beyond the top level is
    assumed: a quantity above the top level's size is named, not priced)."""
    open_legs = [lg for lg in legs if lg.get("open")]
    rows, total, complete, unavailable = [], 0.0, True, []
    for lg in open_legs:
        c = lg.get("current") or {}
        held = float(lg["holding"]["open_qty"] or 0)
        bid, bq = c.get("bid"), c.get("bid_qty")
        if bid is None:
            unavailable.append(lg["leg_key"])
            rows.append({"leg_key": lg["leg_key"], "held_qty": _f(held),
                         "price": None, "reason": c.get("reason") or R_NO_BOOK,
                         "freshness": c.get("freshness")})
            continue
        q = held if bq is None else min(held, float(bq))
        complete = complete and bq is not None and held <= float(bq) + 1e-9
        total += q * float(bid)
        rows.append({"leg_key": lg["leg_key"], "held_qty": _f(held),
                     "price": _f(bid), "qty_at_top_level": _f(q),
                     "beyond_top_level_qty": _f(max(0.0, held - q)),
                     "proceeds_at_top_level_usd": _f(q * float(bid)),
                     "freshness": c.get("freshness"), "age_s": c.get("age_s"),
                     "source": c.get("source"),
                     "observed_at": c.get("observed_at")})
    ok = bool(open_legs) and not unavailable
    return {"available": ok,
            "proceeds_at_top_level_usd": _f(total) if ok else None,
            "reason": (None if ok else "NOTHING_HELD" if not open_legs
                       else "A_HELD_LEG_HAS_NO_EXIT_PRICE"),
            "covers_whole_position": complete if ok else None,
            "stale": any(r.get("freshness") == "STALE" for r in rows),
            "legs": rows,
            "basis": ("held qty x best displayed exit price (top level only, "
                      "gross of exit fees); quantity beyond the top level's "
                      "size is shown as beyond_top_level_qty, not priced")}


def room_protection(legs: list, orders: list, outcomes: list | None
                    ) -> dict:
    """THE ROOM'S PROTECTION LEDGER: position quantity, unprotected
    quantity, standing order quantity, filled protection quantity, the
    CONDITIONAL floor IF_FILLED, the realized floor, the current executable
    exit and the current worst-case exposure. Only FILLED quantity is
    protection (order_state_truth.protection_summary). Pure."""
    primary = [lg for lg in legs if not lg.get("is_hedge_leg")]
    held = sum(float(lg["holding"]["open_qty"] or 0) for lg in primary)
    prot = [o for o in orders if OST.is_protective(o)]
    cond = realized_floor = worst = car = None
    cond_applied, cond_skipped = [], []
    floor_reason = None
    if outcomes:
        now_tbl = payout_table(_book_state(legs), outcomes)
        realized_floor = now_tbl["locked_pnl_usd"]
        worst, car = now_tbl["worst"], now_tbl["capital_at_risk_usd"]
        st = _book_state(legs)
        for o in prot:
            if o["state"] not in STANDING_STATES:
                continue
            if o.get("limit") is None or not o.get("pays_on"):
                cond_skipped.append(o["order_ref"])
                continue
            q = remaining_qty(o)
            if q > 0:
                _apply_fill(st, leg_key=o["leg_key"], pays_on=o["pays_on"],
                            direction=o["direction"], qty=q,
                            price=float(o["limit"]))
                cond_applied.append(o["order_ref"])
        if cond_applied and not cond_skipped:
            cond = payout_table(st, outcomes)["locked_pnl_usd"]
        elif cond_skipped:
            floor_reason = "A_STANDING_PROTECTIVE_ORDER_HAS_NO_LIMIT_OR_OUTCOME"
    else:
        floor_reason = R_NO_OUTCOMES
    # REALIZED PROTECTION: what the filled protective SALES realised
    # (average cost incl. fees); hedge fills realise nothing -- they are
    # in the realized floor as held contracts.
    sell_refs = {o["order_ref"] for o in prot
                 if str(o.get("direction")) == "SELL"}
    realized_prot, fees_known = 0.0, True
    for lg in legs:
        avg = lg["holding"].get("avg_cost_incl_fees")
        for f in lg.get("fills") or []:
            if f.get("order_ref") not in sell_refs or f["direction"] != "SELL":
                continue
            if f.get("fee_usd") is None:
                fees_known = False
            realized_prot += float(f["qty"]) * (float(f["price"]) - float(
                avg or 0)) - float(f.get("fee_usd") or 0)
    ps = OST.protection_summary(
        held_qty=held, orders=prot,
        conditional_floor_if_filled_usd=cond,
        realized_protection_usd=round(realized_prot, 6),
        realized_floor_usd=realized_floor)
    if ps["conditional_floor_if_filled_usd"] is None and \
            ps["standing_order_qty"] > 0:
        ps["conditional_floor_reason"] = floor_reason or "UNAVAILABLE"
    ex = executable_exit(legs)
    return dict(
        ps,
        hedge_legs=[lg["leg_key"] for lg in legs if lg.get("is_hedge_leg")],
        protected_legs=[lg["leg_key"] for lg in primary],
        conditional_floor_basis=(
            "IF_FILLED: the minimum total P&L over the played outcomes if "
            "every STANDING protective order's unfilled remainder fills at "
            "its own limit (gross of the hypothetical fill's fees). "
            "CONDITIONAL -- nothing of it is realized"),
        conditional_floor_orders=cond_applied,
        realized_floor_basis=("the minimum total P&L over the played "
                              "outcomes of the FILLED holdings only (standing "
                              "orders excluded)"),
        realized_protection_basis=(
            "filled protective SALES: qty x (fill price - average cost incl. "
            "fees) - sale fees; hedge fills are held contracts, counted in "
            "the realized floor"),
        realized_protection_fees_known=fees_known,
        current_executable_exit=ex,
        current_worst_case={"worst_outcome": worst,
                            "capital_at_risk_usd": car,
                            "basis": ("FILLED holdings only: -min(payout - "
                                      "remaining cost basis) over the played "
                                      "outcomes")},
        current_worst_case_exposure_usd=car)


def scenarios(legs: list, orders: list, outcomes: list | None,
              probs: dict | None = None) -> dict:
    """(a) current filled holdings; (b) plus every standing order filled at
    its limit; (c) plus proposed orders too, when any exist."""
    if not outcomes:
        return {"available": False, "reason": R_NO_OUTCOMES}
    a = payout_table(_book_state(legs), outcomes, probs)
    st = _book_state(legs)
    applied, skipped = [], []
    for o in orders:
        if o["state"] not in STANDING_STATES:
            continue
        if o.get("limit") is None or not o.get("pays_on"):
            skipped.append({"order_ref": o["order_ref"],
                            "reason": R_NO_LIMIT if o.get("limit") is None
                            else (o.get("identity_reason") or R_NO_OUTCOMES)})
            continue
        q = remaining_qty(o)
        if q > 0:
            _apply_fill(st, leg_key=o["leg_key"], pays_on=o["pays_on"],
                        direction=o["direction"], qty=q,
                        price=float(o["limit"]))
            applied.append(o["order_ref"])
    b = payout_table(st, outcomes, probs)
    res = {"available": True, "outcomes": outcomes,
           "current": a, "with_standing_filled": b,
           "current_label": "FILLED HOLDINGS ONLY (realized basis)",
           "with_standing_filled_label": (
               "IF_FILLED: CONDITIONAL on every standing order filling at "
               "its limit -- nothing of it is realized"),
           "conditional": {"with_standing_filled": OST.IF_FILLED,
                           "with_standing_and_proposed_filled":
                               OST.IF_FILLED},
           "standing_orders_applied": applied, "skipped": skipped,
           "probabilities": probs,
           "fees": ("filled legs carry their recorded fees; hypothetical "
                    "fills are gross: " + R_FEES_HYPO)}
    prop = [o for o in orders if o["state"] == S_PROPOSED
            and o.get("limit") is not None and o.get("pays_on")]
    if prop:
        for o in prop:
            _apply_fill(st, leg_key=o["leg_key"], pays_on=o["pays_on"],
                        direction=o["direction"], qty=remaining_qty(o),
                        price=float(o["limit"]))
        res["with_standing_and_proposed_filled"] = payout_table(
            st, outcomes, probs)
        res["proposed_applied"] = [o["order_ref"] for o in prop]
    return res


def outcome_probabilities(panels: list, legs: list, outcomes) -> dict:
    """p(outcome) for the expected-value line, from Xavier's RECORDED
    probability only: the newest assessment whose group's open legs all pay
    on ONE outcome, in a room with exactly two played outcomes (so the other
    is its complement). Anything else is null with the reason."""
    if not outcomes:
        return {"p": None, "reason": R_NO_OUTCOMES}
    if len(outcomes) != 2:
        return {"p": None, "reason": ("THREE_WAY_EVENT: one recorded "
                                      "probability does not split the other "
                                      "two outcomes")}
    best = None
    for x in panels:
        ev = x.get("evidence") or {}
        if x.get("status") != "OK" or ev.get("probability") is None:
            continue
        pays = {tuple(lg.get("pays_on") or ()) for lg in legs
                if lg.get("group_id") == x["group_id"] and lg.get("open")}
        if len(pays) != 1:
            continue
        po = list(next(iter(pays)))
        if len(po) != 1 or po[0] not in outcomes:
            continue
        if best is None or (x.get("assessed_at") or "") > (
                best[0].get("assessed_at") or ""):
            best = (x, po[0])
    if best is None:
        return {"p": None, "reason": "NO_RECORDED_XAVIER_PROBABILITY_FOR_A_"
                                     "HELD_OUTCOME_IN_THIS_ROOM"}
    x, o = best
    p = float(x["evidence"]["probability"])
    other = next(k for k in outcomes if k != o)
    return {"p": {o: round(p, 6), other: round(1.0 - p, 6)},
            "source": "xavier_management_assessments#%s (%s)" % (
                x.get("assessment_id"), x["evidence"].get("source")),
            "evidence_state": x["evidence"].get("state"),
            "age_now_s": x["evidence"].get("age_now_s"),
            "assessed_at": x.get("assessed_at"),
            "complement_basis": "two played outcomes: p(other) = 1 - p",
            "stale": x["evidence"].get("state") != "FRESH_CURRENT_PROBABILITY"}


# ═════════════════════════════════════════════════════════════════════
# 5 · XAVIER, KAREN, AUDREY, ARCHER, GAME STATE (pure projections)
# ═════════════════════════════════════════════════════════════════════

ALT_NAMES = {"VERIFIED_HEDGE": "HEDGE"}


def xavier_panel(*, group_id: str, kind: str, assessment: dict | None,
                 thesis: dict | None, review: dict | None,
                 standing_orders: list, cadence_s: float, now: float,
                 schema_present: bool = True, group_orders: list | None = None,
                 group_legs: list | None = None, venue: str = V_PM,
                 latest_valuation: dict | None = None,
                 limit_s: float | None = None) -> dict:
    """XAVIER AS DECISION MAKER, from the persisted rows only. The stored
    recommendation is RE-JUDGED AT `now` (xavier_freshness.of_assessment):
    `recommendation` / `display_recommendation` carry the action only while
    CURRENT, and the state (STALE / INVALID / WAITING_FOR_FRESH_EVIDENCE /
    MANAGEMENT_UNAVAILABLE_STALE_INPUT) otherwise -- a stale HOLD never
    renders as the current recommendation (owner P0)."""
    base = {"group_id": group_id, "position_kind": kind,
            "source": "xavier_management_assessments (migration 206)",
            "protection": protection_view(group_orders or [],
                                          group_legs or []),
            "position": position_mark_view(group_legs or [], venue)}
    if not schema_present:
        return dict(base, status="UNAVAILABLE", why=R_206)
    if assessment is None:
        return dict(base, status="UNAVAILABLE", why=R_NO_XAVIER,
                    thesis=_thesis_view(thesis, None, now))
    a = assessment
    at = _epoch(a.get("assessed_at"))
    ve0 = _j(a.get("venue_economics")) or {}
    fr = XF.of_assessment(
        dict(a, assessed_at=at, valuation=_j(a.get("valuation"))), now=now,
        limit_s=(_f((thesis or {}).get("probability_limit_s"))
                 or _f(limit_s)),
        latest_valuation=latest_valuation,
        event_start_at=_epoch((thesis or {}).get("event_start_at")),
        mark_at_assessment=(ve0.get("best_exit")
                            if isinstance(ve0, dict) else None))
    alts = XF.complete_alternatives(_j(a.get("alternatives")) or [],
                                    evidence_state=a.get("evidence_state"))
    ranked = sorted(
        [x for x in alts if isinstance(x, dict)],
        key=lambda x: (not x.get("rankable"),
                       -(float(x["value_usd"]) if x.get("value_usd")
                         is not None else -1e18)))
    recorded = a.get("recommendation")
    # only a CURRENT recommendation is a recommendation now
    rec = fr["current_recommendation"]
    rows = []
    for i, x in enumerate(ranked):
        rows.append({"rank": i + 1 if x.get("rankable") else None,
                     "action": ALT_NAMES.get(x.get("action"), x.get("action")),
                     "recorded_action": x.get("action"),
                     "value_usd": _f(x.get("value_usd")),
                     "expected_net_usd": _f(x.get("expected_net_usd")),
                     "fees_usd": _f(x.get("fees_usd")), "qty": _f(x.get("qty")),
                     "rankable": bool(x.get("rankable")),
                     "blocker": x.get("blocker"), "mode": x.get("mode"),
                     "option": x.get("option"),
                     "missing_evidence": x.get("missing_evidence"),
                     "is_recommendation": rec is not None
                     and x.get("action") == rec,
                     "ev_basis": x.get("ev_basis")})
    # PROTECTION is the standing order (if any): it is an order, not a
    # valued alternative, and it is not protection until it fills.
    prot = [o for o in standing_orders if OST.is_protective(o)]
    pv = base["protection"]
    rows.append({"rank": None, "action": "PROTECTION",
                 "recorded_action": None,
                 "value_usd": None, "rankable": False,
                 "blocker": None if prot else "NO_STANDING_PROTECTION_ORDER",
                 "state": ", ".join(sorted({o["state"] for o in prot}))
                 or None,
                 "orders": [o["order_ref"] for o in prot],
                 "filled_protection_qty": pv.get("filled_protection_qty"),
                 "standing_order_qty": pv.get("standing_order_qty"),
                 "note": (("STANDING %s - NOT PROTECTION UNTIL FILLED; "
                           "FILLED PROTECTION %s" % (
                               OST.fmt_qty(pv.get("standing_order_qty")),
                               OST.fmt_qty(pv.get("filled_protection_qty"))))
                          if prot else None)})
    rankable = [r for r in rows if r["rankable"] and r["value_usd"]
                is not None]
    why = None
    sel = _j((review or {}).get("selection")) or {}
    if sel.get("selection_reason"):
        why = {"text": sel.get("selection_reason"),
               "margin_over_runner_up": sel.get("margin_over_runner_up"),
               "decision_policy": sel.get("decision_policy"),
               "source": "paper_xavier_reviews.selection"}
    elif len(rankable) >= 2:
        lead, nxt = rankable[0], rankable[1]
        why = {"text": "%s is valued %.2f USD against %s at %.2f USD" % (
            lead["action"], lead["value_usd"], nxt["action"],
            nxt["value_usd"]),
            "margin_over_runner_up": round(lead["value_usd"]
                                           - nxt["value_usd"], 6),
            "source": "DERIVED_FROM_THE_RECORDED_ALTERNATIVE_VALUES"}
    if not fr["is_current"]:
        why = {"text": ("no current management recommendation: %s%s. A held "
                        "position is not a HOLD recommendation; Xavier "
                        "re-assesses on fresh evidence" % (
                            fr["recommendation_state"],
                            (" (" + ", ".join(fr["reasons"]) + ")")
                            if fr["reasons"] else "")),
               "recorded_recommendation": recorded,
               "source": "xavier_freshness.validity at read time"}
    p_age = _f(a.get("probability_age_s"), 1)
    age_now = None if p_age is None or at is None else round(
        p_age + max(0.0, now - at), 1)
    evidence = {"state": a.get("evidence_state"),
                "probability": _f(a.get("probability")),
                "source": a.get("probability_source"),
                "age_at_assessment_s": p_age, "age_now_s": age_now,
                "discretionary_permitted": a.get("discretionary_permitted")}
    warnings = []
    if a.get("evidence_state") != "FRESH_CURRENT_PROBABILITY":
        warnings.append({"what": "PROBABILITY_NOT_FRESH",
                         "detail": a.get("evidence_state"),
                         "source": "xavier_management_assessments."
                                   "evidence_state"})
    if age_now is not None and age_now > 30:
        warnings.append({"what": "PROBABILITY_OLDER_THAN_30S_NOW",
                         "detail": "%.0f s old now" % age_now,
                         "source": "probability_age_s + time since review"})
    th = _thesis_view(thesis, a, now)
    for k in ("evidence_expires_at", "thesis_expires_at"):
        e = _epoch((thesis or {}).get(k))
        if e is not None and e < now:
            warnings.append({"what": k.replace("_expires_at", "").upper()
                             + "_EXPIRY_PASSED",
                             "detail": iso(e),
                             "source": "xavier_entry_theses." + k})
    due = None if at is None else at + float(cadence_s)
    waiting = []
    if a.get("evidence_state") != "FRESH_CURRENT_PROBABILITY":
        waiting.append({"what": "A_FRESH_PROBABILITY",
                        "why": ("discretionary actions need "
                                "FRESH_CURRENT_PROBABILITY"),
                        "source": "xavier_management_assessments"})
    for o in standing_orders:
        waiting.append({"what": "STANDING_ORDER_TO_FILL",
                        "order_ref": o["order_ref"], "state": o["state"],
                        "limit": o.get("limit"),
                        "distance": (o.get("distance") or {}).get("distance"),
                        "source": o.get("source")})
    for r in rows:
        if r["blocker"] and r["action"] not in ("PROTECTION",):
            waiting.append({"what": "BLOCKER_CLEARED",
                            "action": r["action"], "blocker": r["blocker"],
                            "source": "xavier_management_assessments."
                                      "alternatives"})
    review_view = None
    if review:
        review_view = {
            "review_id": review.get("review_id"),
            "reviewed_at": iso(review.get("reviewed_at")),
            # the review's stored word is RECORDED; what it means now is
            # the gated recommendation above (never a stale HOLD as current)
            "recommendation": (review.get("recommendation")
                               if fr["is_current"] else
                               fr["recommendation_state"]),
            "recorded_recommendation": review.get("recommendation"),
            "refusal": review.get("refusal"),
            "action": _j(review.get("action")),
            "exposure": _j(review.get("exposure")),
            "exceptional": _j(review.get("exceptional")),
            "source": review.get("_table") or "paper_xavier_reviews"}
    hold = next((r for r in rows if r["recorded_action"] == "HOLD"), None)
    taken = str(((review_view or {}).get("action") or {}).get("taken") or "")
    if rec is None:
        # STALE / INVALID / WAITING_FOR_FRESH_EVIDENCE /
        # MANAGEMENT_UNAVAILABLE_STALE_INPUT / NO_RECOMMENDATION
        disp = fr["recommendation_state"]
    elif rec == "HOLD" and "STANDING" in taken:
        disp = "PROTECT"
    else:
        disp = ALT_NAMES.get(rec, rec)
    return dict(
        base, status="OK",
        assessment_id=a.get("assessment_id"), review_id=a.get("review_id"),
        assessed_at=iso(at), trigger=a.get("trigger"),
        recommendation=rec, display_recommendation=disp,
        recommendation_state=fr["recommendation_state"],
        management_state=fr["management_state"],
        recorded_recommendation=recorded,
        freshness=fr,
        decision=XF.decision(fr, review_id=a.get("review_id"),
                             reviewed_at=at),
        display_basis=("recorded recommendation %s, %s at read time; only a "
                       "CURRENT recommendation is shown as one; PROTECT = "
                       "a CURRENT HOLD while the review maintains a standing "
                       "protective order (%s)" % (
                           recorded, fr["recommendation_state"],
                           taken or "none")),
        current_ev_usd=(hold["value_usd"] if hold is not None
                        and fr["is_current"] else None),
        hold_value_at_assessment_usd=(None if hold is None
                                      else hold["value_usd"]),
        current_ev_basis=("HOLD alternative's recorded value (held qty x "
                          "recorded probability) at %s, evidence %s; shown "
                          "as current only while the recommendation is "
                          "CURRENT (now %s)" % (
                              iso(at), a.get("evidence_state"),
                              fr["recommendation_state"])),
        entry_ev_usd=_f((thesis or {}).get("entry_ev_usd")),
        evidence=evidence,
        venue_economics=_j(a.get("venue_economics")),
        alternatives=rows, why_leader_wins=why, waiting_for=waiting,
        next_trigger={
            "events": (["FILL_EVENT (any standing order fills)"]
                       if standing_orders else []) + (
                ["MARKET_EVENT (held market price change)"]
                if kind == "PAPER" else []),
            "scheduled_backstop_at": iso(due),
            "cadence_s": float(cadence_s),
            "basis": ("last assessed_at + cadence (paper_sessions.config."
                      "cadence.xavier_backstop_s / execmirror."
                      "MANAGEMENT_EVERY_S)")},
        next_review_due_at=iso(due),
        review_overdue=None if due is None else now > due + float(cadence_s),
        latency={"latency_s": a.get("review_latency_s"),
                 "bound_s": a.get("latency_bound_s"),
                 "within_bound": a.get("within_bound")},
        thesis=th, stale_evidence=warnings,
        reallocate=_j(a.get("reallocate")),
        policy_status=(_j(a.get("policy")) or {}).get("status"),
        latest_review=review_view)


def protection_view(group_orders: list, group_legs: list | None = None
                    ) -> dict:
    """FILLED protection vs STANDING (resting / partial remainder) orders,
    never merged: order_state_truth.protection_summary on the group's
    orders, with the held quantity of its protected (non-hedge) legs."""
    held = None
    if group_legs is not None:
        held = sum(float(lg["holding"]["open_qty"] or 0)
                   for lg in group_legs if not lg.get("is_hedge_leg"))
    ps = OST.protection_summary(held_qty=held, orders=group_orders)
    return dict(ps,
                # the earlier field names, kept for readers of this payload
                unfilled_resting_protection_qty=ps["standing_order_qty"],
                rule=OST.RULE)


def position_mark_view(group_legs: list, venue: str) -> dict:
    """The managed position's mark and both venues' current prices:
    Polymarket from the observed book, Kalshi UNAVAILABLE (no persisted
    Kalshi book) -- never implied from the other venue."""
    out = []
    for lg in group_legs:
        c = lg.get("current") or {}
        pm = {k: c.get(k) for k in ("bid", "ask", "mark", "observed_at",
                                    "age_s", "freshness", "reason",
                                    "source")}
        out.append({
            "leg_key": lg["leg_key"],
            "instrument": lg["instrument"]["label"],
            "open_qty": lg["holding"]["open_qty"],
            "mark": c.get("mark"),
            "mark_value_usd": (_f(float(lg["holding"]["open_qty"] or 0)
                                  * float(c["mark"]))
                               if c.get("mark") is not None else None),
            "venue_prices": {
                "POLYMARKET": pm if venue == V_PM else {
                    "status": "UNAVAILABLE",
                    "reason": "NOT_THIS_BOOK'S_VENUE"},
                "KALSHI": ({"status": "UNAVAILABLE",
                            "reason": R_KALSHI_BOOK} if venue == V_PM
                           else dict(pm, status="UNAVAILABLE"
                                     if pm.get("bid") is None else "OK"))}})
    return {"legs": out}


def _thesis_view(thesis: dict | None, a: dict | None, now: float) -> dict:
    t = thesis or {}
    return {"thesis_id": t.get("thesis_id"),
            "state": (a or {}).get("thesis_state")
            or ("NO_ENTRY_THESIS" if not thesis else None),
            "detail": _j((a or {}).get("thesis_detail")),
            "entry_probability": _f(t.get("entry_probability")),
            "probability_source": t.get("probability_source"),
            "entry_ev_usd": _f(t.get("entry_ev_usd")),
            "entered_at": iso(t.get("entered_at")),
            "evidence_expires_at": iso(t.get("evidence_expires_at")),
            "thesis_expires_at": iso(t.get("thesis_expires_at")),
            "expiry_basis": t.get("expiry_basis"),
            "source": "xavier_entry_theses"}


def karen_view(rows: list) -> dict:
    items = [{"challenge_id": r.get("challenge_id"),
              "detector": r.get("detector"),
              "target_agent": r.get("target_agent"),
              "target": "%s:%s" % (r.get("target_kind"), r.get("target_id")),
              "severity": r.get("severity"), "state": r.get("state"),
              "claim": r.get("claim"), "outcome": r.get("outcome"),
              "challenged_at": iso(r.get("challenged_at")),
              "response_stance": r.get("response_stance")}
             for r in rows]
    open_ = [i for i in items if i["state"] in ("OPEN", "RESPONDED")]
    return {"status": "OPEN_CHALLENGE" if open_ else (
        "NONE" if not items else "RESOLVED"),
        "open": len(open_), "total": len(items), "items": items[:10],
        "source": "karen_challenges (migration 207), matched by the "
                  "room's decision ids, Xavier review ids and actual "
                  "group ids"}


def audrey_view(findings: list, recon: list, postmortems: list) -> dict:
    bad = [f for f in findings if str(f.get("severity")) in
           ("WARNING", "CRITICAL")]
    disc = [r for r in recon if r.get("status") == "DISCREPANCY"]
    status = ("DISCREPANCY" if disc else "FINDINGS" if bad else
              "RECONCILED" if recon and all(
                  r.get("status") == "MATCHED" for r in recon) else
              "NO_FINDINGS")
    return {"status": status,
            "findings": [{"finding_id": f.get("finding_id"),
                          "kind": f.get("kind"),
                          "severity": f.get("severity"),
                          "subject": f.get("subject"),
                          "found_at": iso(f.get("found_at"))}
                         for f in findings[:10]],
            "reconciliation": [{"group_id": r.get("group_id"),
                                "venue": r.get("venue"),
                                "status": r.get("status"),
                                "reconciled_at": iso(r.get("reconciled_at")),
                                "discrepancies": len(_j(r.get(
                                    "discrepancies")) or [])}
                               for r in recon],
            "postmortems": [{"position_key": p.get("position_key"),
                             "realized_pnl_usd": _f(p.get(
                                 "realized_pnl_usd")),
                             "unexplained_usd": _f(p.get("unexplained_usd")),
                             "complete": p.get("decomposition_complete"),
                             "closed_at": iso(p.get("closed_at"))}
                            for p in postmortems[:10]],
            "source": ("paper_audrey_findings (subject = a room group / "
                       "order / decision / position key), "
                       "smalllive_reconciliations, position_postmortems")}


def archer_view(present: bool, rows: list) -> dict:
    if not present:
        return {"status": "UNAVAILABLE", "why": R_ARCHER,
                "source": "eddie_execution_estimates (migration 217) is "
                          "not in this database"}
    if not rows:
        return {"status": "NO_ESTIMATE",
                "why": "no Archer execution estimate names this room's "
                       "decisions or markets",
                "source": "eddie_execution_estimates"}
    keep = ("estimate_id", "decision_id", "us_market_slug", "holding_side",
            "recommendation", "recommendation_reason", "execution_style",
            "expected_fill_probability", "expected_time_to_fill_s",
            "expected_net_executable_edge_pp", "expected_execution_loss_pp",
            "spread_cost_pp", "expected_fees_pp", "expected_slippage_pp",
            "max_executable_qty", "expected_executable_ev_usd",
            "book_age_s", "authority", "unmeasured")
    # R30C: every fill probability shown says what it was fitted on (the
    # paper simulator's rate is never displayed as live execution quality)
    return {"status": "OK", "source": "eddie_execution_estimates",
            "estimates": [dict({k: (iso(r.get(k)) if k.endswith("_at")
                                    else r.get(k)) for k in keep},
                               estimated_at=iso(r.get("estimated_at")),
                               fill_probability_evidence=(
                                   EE.fill_probability_label(r)))
                          for r in rows[:10]]}


GAME_STATUS = {
    # MLB Stats API detailedState (bettor_fixture_metadata.STATE_PLAY_BEGUN)
    "scheduled": "SCHEDULED", "pre-game": "SCHEDULED", "warmup": "SCHEDULED",
    "delayed start": "SCHEDULED", "in progress": "LIVE", "delayed": "DELAYED",
    "suspended": "SUSPENDED", "game over": "FINAL", "final": "FINAL",
    "completed early": "FINAL", "postponed": "POSTPONED",
    "cancelled": "CANCELLED", "canceled": "CANCELLED",
    # UEFA match API status (bettor_soccer_fixture)
    "upcoming": "SCHEDULED", "live": "LIVE", "half_time": "BREAK",
    "finished": "FINAL", "played": "FINAL", "interrupted": "SUSPENDED",
    "abandoned": "ABANDONED",
}


def game_state_view(row: dict | None, *, teams: list, now: float,
                    event_slug: str | None, game_start=None) -> dict:
    """The league-REPORTED event state with its age. Score, period and
    clock are UNAVAILABLE: no authoritative source carries them. A stale
    row is never presented as live."""
    out = {"event_slug": event_slug, "teams": teams,
           "scheduled_start": iso(game_start),
           "score": None, "period": None, "clock": None, "situation": None,
           "score_status": "UNAVAILABLE", "score_reason": R_NO_SCORE,
           "live": False}
    if not row:
        out.update(status="UNAVAILABLE", reason=R_NO_GAME_ROW,
                   source=None, observed_at=None, age_s=None,
                   freshness="UNAVAILABLE")
        return out
    raw = str(row.get("event_state_raw") or "").strip()
    reported = GAME_STATUS.get(raw.lower(), "UNKNOWN")
    fr = freshness(row.get("retrieved_at"), now, GAME_STATE_MAX_AGE_S)
    stale = fr["state"] != "FRESH"
    out.update(reported_status=reported, event_state_raw=raw or None,
               play_has_begun=row.get("play_has_begun"),
               status=("STALE" if stale else reported),
               live=(reported == "LIVE" and not stale),
               source="%s (%s)" % (row.get("source"), row.get("_table")),
               source_url=row.get("source_url"),
               observed_at=fr["at"], age_s=fr["age_s"],
               freshness=fr["state"], max_age_s=GAME_STATE_MAX_AGE_S,
               reason=(("the last league report (%s) is %.0f s old, beyond "
                        "%.0f s: shown as STALE, never as live"
                        % (raw or "?", fr["age_s"] or 0,
                           GAME_STATE_MAX_AGE_S)) if stale else None),
               home_team=row.get("home_team"), away_team=row.get("away_team"),
               identity_binding=row.get("_binding"))
    out["situation_status"] = "UNAVAILABLE"
    out["situation_reason"] = ("possession / base state / down-and-distance "
                               "are not carried by any authoritative source "
                               "in this system; " + R_NO_SCORE)
    return out


# ═════════════════════════════════════════════════════════════════════
# 6 · ROOM ASSEMBLY (pure)
# ═════════════════════════════════════════════════════════════════════

def _leg_key(group_id, slug, side) -> str:
    return "%s|%s|%s" % (group_id or "-", slug, side)


def account_of(book: str, venue: str, account_id=None) -> dict:
    if book == B_PAPER:
        return {"book": B_PAPER, "venue": venue,
                "account": account_id or PAPER_ACCOUNT_ID,
                "label": "PAPER account (fictional $500,000, simulated "
                         "execution)"}
    return {"book": B_ACTUAL, "venue": venue,
            "account": ("execmirror" if venue == V_PM else
                        "kalshi_smalllive"),
            "label": ("ACTUAL Polymarket US small-live mirror account"
                      if venue == V_PM else
                      "ACTUAL Kalshi small-live account")}


def contract_of(slug, side, ident: dict, *, venue: str, ticker=None) -> dict:
    return {"venue": venue, "market_slug": slug, "ticker": ticker,
            "held_side": side, "buy_intent": "ORDER_INTENT_BUY_%s" % side,
            "event_slug": ident.get("event_slug"),
            "settlement_class": ident.get("settlement_class"),
            "contract_shape": ident.get("contract_shape"),
            "pays_on": ident.get("pays_on"),
            "identity_status": ident.get("status"),
            "identity_reason": ident.get("reason"),
            "basis": ident.get("basis")}


def _protective_note(o: dict) -> str | None:
    """A protective order's standing remainder is never called protection."""
    if not OST.is_protective(o):
        return None
    st = o["state"]
    f = float(o.get("counts_as_filled_qty") or 0)
    rem = float(o.get("standing_qty") or 0) + float(o.get("pending_qty")
                                                   or 0)
    if st in (S_RESTING, S_SUBMITTED, S_PROPOSED, S_UNKNOWN):
        return "%s - NOT PROTECTION UNTIL FILLED" % st
    if st == S_PARTIAL:
        return ("PARTIAL - ONLY THE FILLED %s COUNTS AS PROTECTION; %s STILL "
                "RESTING, NOT PROTECTION" % (OST.fmt_qty(f),
                                             OST.fmt_qty(rem)))
    if st == S_CANCEL_PENDING:
        return ("CANCEL_PENDING - MAY STILL FILL; ITS %s UNFILLED ARE NOT "
                "PROTECTION%s" % (OST.fmt_qty(rem), (
                    "; ONLY THE FILLED %s COUNTS" % OST.fmt_qty(f))
                    if f > 0 else ""))
    if st in (S_CANCELLED, S_EXPIRED, S_REJECTED) and f <= 0:
        return "%s UNFILLED - CONTRIBUTES NO PROTECTION" % st
    if st in (S_CANCELLED, S_EXPIRED) and f > 0:
        # A TERMINAL ORDER THAT FILLED IN PART (production 2026-10-09: 7
        # EXPIRED and 4 CANCELED protective sales, e.g. one EXPIRED at 2,695
        # of 2,702): its filled part is protection; its unfilled remainder
        # is gone and protects nothing. Before this it carried no note, so
        # the expired remainder was never named.
        q = _f(o.get("qty"))
        gone = None if q is None else max(0.0, q - f)
        return ("%s - ONLY THE FILLED %s COUNTS AS PROTECTION; ITS UNFILLED "
                "%s %s - CONTRIBUTES NO PROTECTION" % (
                    st, OST.fmt_qty(f), OST.fmt_qty(gone),
                    "EXPIRED" if st == S_EXPIRED else "WAS CANCELLED"))
    return None


def normalize_order(o: dict) -> dict:
    """THE ONE MAPPING, applied to every order a room shows: the canonical
    state, sub-state and the quantities that count, from the record's raw
    state and its own source table (order_state_truth.order_state). Pure."""
    t = OST.order_state(o.get("raw_state"), source=o.get("source"),
                        qty=o.get("qty"), filled_qty=o.get("filled_qty"))
    return dict(o, state=t["state"], sub_state=t["sub_state"],
                counts_as_filled_qty=t["filled_qty"],
                standing_qty=t["standing_qty"],
                pending_qty=t["pending_qty"])


def _order_view(o: dict, *, ident: dict, room_kind: str, tob: dict,
                now: float, account_id=None) -> dict:
    side = o["holding_side"]
    rem = remaining_qty(o)
    pays = pays_on_for(ident, room_kind, side)
    pnl = None
    pnl_basis = None
    if o.get("avg_fill") is not None and float(o.get("filled_qty") or 0) > 0:
        if o["direction"] == "BUY" and tob.get("mark") is not None:
            pnl = round(float(o["filled_qty"]) * (
                float(tob["mark"]) - float(o["avg_fill"]))
                - float(o.get("fees_usd") or 0), 6)
            pnl_basis = ("MARK_TO_MARKET of the filled quantity: "
                         "(mark - fill) x filled - recorded fees")
    return {
        "order_ref": o["order_ref"], "source": o["source"],
        "book": o["book"], "venue": o["venue"], "group_id": o.get("group_id"),
        "account": account_of(o["book"], o["venue"], account_id),
        "contract": contract_of(o["slug"], side, ident, venue=o["venue"],
                                ticker=o.get("ticker")),
        "leg_key": _leg_key(o.get("group_id"), o["slug"], side),
        "role": o.get("role"), "instrument": {
            "slug": o["slug"], "side": side,
            "label": ident.get("label") if room_kind == K_EVT else (
                "%s %s" % (side, o["slug"])),
            "ticker": o.get("ticker")},
        "direction": o["direction"], "holding_side": side,
        "side_label": "%s %s" % (o["direction"], ident.get("label")
                                  or side),
        "qty": _f(o.get("qty")), "filled_qty": _f(o.get("filled_qty")),
        "remaining_qty": _f(rem),
        "limit": _f(o.get("limit")), "wire_price": _f(o.get("wire_price")),
        "avg_fill_price": _f(o.get("avg_fill")),
        "fees_usd": _f(o.get("fees_usd")),
        "state": o["state"], "raw_state": o.get("raw_state"),
        "sub_state": o.get("sub_state"),
        "state_meaning": STATE_MEANING.get(o["state"]),
        # ONLY the filled quantity counts (order_state_truth.order_state)
        "counts_as_filled_qty": o.get("counts_as_filled_qty"),
        "standing_qty": o.get("standing_qty"),
        "protective": OST.is_protective(o),
        "order_type": o.get("order_type"), "tif": o.get("tif"),
        "current": {k: tob.get(k) for k in ("bid", "ask", "mark",
                                            "observed_at", "age_s",
                                            "freshness", "reason",
                                            "source")},
        "distance": (distance_to_threshold(o["direction"], o.get("limit"),
                                           tob)
                     if o["state"] in LIVE_STATES else None),
        "pnl_usd": pnl, "pnl_basis": pnl_basis,
        "pays_on": pays, "identity_reason": ident.get("reason"),
        "created_at": iso(o.get("created_at")),
        "updated_at": iso(o.get("updated_at")),
        "expires_at": iso(o.get("expires_at")),
        "terminal_reason": o.get("terminal_reason"),
        "decision_id": o.get("decision_id"),
        "strategy": o.get("strategy"),
        "freshness": freshness(o.get("updated_at") or o.get("created_at"),
                               now, 1e18),
        "note": _protective_note(o),
        "live_scale": o.get("live_scale")}


def build_rooms(raw: dict) -> list:
    """Every room of ONE book (and venue) from the loaded rows. Pure."""
    now = float(raw["now"])
    book, venue = raw["book"], raw["venue"]
    by_slug, by_event = {}, {}
    for r in raw.get("premap") or []:
        by_slug.setdefault(str(r.get("market_slug") or "").lower(),
                           []).append(r)
        if r.get("event_slug"):
            by_event.setdefault(r["event_slug"], []).append(r)
    ident_cache: dict = {}

    def ident_of(slug, side):
        k = (slug, side)
        if k not in ident_cache:
            if not slug:
                ident_cache[k] = {"status": "UNGROUPED",
                                  "reason": R_KALSHI_SLUG, "slug": slug}
            else:
                ident_cache[k] = resolve_identity(slug, side, by_slug,
                                                  by_event)
        return ident_cache[k]

    def room_of(slug, side):
        idn = ident_of(slug, side)
        if idn.get("status") == "ESTABLISHED":
            return group_key(book, venue, K_EVT, idn["event_slug"]), K_EVT
        return group_key(book, venue, K_MKT, slug or "unmapped"), K_MKT

    # HOLDINGS: one slice per (group, market, held side)
    fills_by = {}
    for f in raw.get("fills") or []:
        fills_by.setdefault(_leg_key(f.get("group_id"), f["slug"],
                                     f["holding_side"]), []).append(f)
    sett = {_leg_key(s.get("group_id"), s["slug"], s["holding_side"]): s
            for s in raw.get("settlements") or []}
    rooms: dict[str, dict] = {}

    def room(key, kind, slug, side):
        if key not in rooms:
            idn = ident_of(slug, side)
            rooms[key] = {"group_key": key, "kind": kind, "book": book,
                          "venue": venue, "identity": idn, "legs": [],
                          "orders": [], "groups": set(), "slugs": set()}
        return rooms[key]

    for lk, fl in fills_by.items():
        g, slug, side = fl[0].get("group_id"), fl[0]["slug"], \
            fl[0]["holding_side"]
        key, kind = room_of(slug, side)
        rm = room(key, kind, slug, side)
        h = slice_from_fills(fl, sett.get(lk))
        idn = ident_of(slug, side)
        rm["legs"].append({"leg_key": lk, "group_id": g, "slug": slug,
                           "holding_side": side, "holding": h,
                           "identity": idn,
                           "pays_on": pays_on_for(idn, kind, side),
                           "fills": sorted(fl, key=lambda x: _epoch(
                               x.get("at")) or 0)})
        rm["groups"].add(g)
        rm["slugs"].add(slug)
    for o in raw.get("orders") or []:
        o = normalize_order(o)
        key, kind = room_of(o["slug"], o["holding_side"])
        rm = room(key, kind, o["slug"], o["holding_side"])
        rm["orders"].append(o)
        if o.get("group_id"):
            rm["groups"].add(o["group_id"])
        rm["slugs"].add(o["slug"])

    out = []
    for key, rm in rooms.items():
        out.append(_finish_room(rm, raw, ident_of, now))
    out.sort(key=lambda r: (not r["active"], r["kind"] != K_EVT,
                            r["identity"].get("event_slug") or r["group_key"]))
    return out


def _finish_room(rm: dict, raw: dict, ident_of, now: float) -> dict:
    book, venue, kind = rm["book"], rm["venue"], rm["kind"]
    books = raw.get("books") or {}
    tob_cache = {}

    def tob(slug, side):
        if (slug, side) not in tob_cache:
            tob_cache[(slug, side)] = top_of_book(books.get(slug), side,
                                                  now=now, venue=venue)
        return tob_cache[(slug, side)]

    idn0 = rm["identity"]
    if kind == K_EVT:
        outcomes = idn0.get("outcomes")
        labels = dict(idn0.get("outcome_labels") or {})
    else:
        mo = market_outcomes(next(iter(rm["slugs"])))
        outcomes, labels = mo["outcomes"], mo["outcome_labels"]
    labels[VOID] = "Void (venue-declared)"
    role_by_ref = {o["order_ref"]: o.get("role") for o in rm["orders"]}
    # LEGS
    legs = []
    for lg in sorted(rm["legs"], key=lambda x: x["holding"].get(
            "first_fill_at") or ""):
        t = tob(lg["slug"], lg["holding_side"])
        h = lg["holding"]
        oq = float(h["open_qty"] or 0)
        unreal = None
        if t.get("mark") is not None and oq > 0:
            unreal = round(oq * float(t["mark"])
                           - float(h["cost_basis_usd"] or 0), 6)
        legs.append({
            "leg_key": lg["leg_key"], "group_id": lg["group_id"],
            "book": book, "venue": venue,
            "account": account_of(book, venue, raw.get("account_id")),
            "contract": contract_of(lg["slug"], lg["holding_side"],
                                    lg["identity"], venue=venue),
            "holding_side": lg["holding_side"],
            "instrument": {"slug": lg["slug"], "side": lg["holding_side"],
                           "label": (lg["identity"].get("label")
                                     if kind == K_EVT else
                                     "%s %s" % (lg["holding_side"],
                                                lg["slug"]))},
            "identity": {k: lg["identity"].get(k) for k in (
                "status", "reason", "event_slug", "settlement_class",
                "contract_shape", "pays_on", "basis")},
            "pays_on": lg["pays_on"],
            "orientation": ("economic exposure to %s"
                            % " / ".join(labels.get(o, o) for o in
                                         (lg["pays_on"] or []))
                            if lg["pays_on"] else None),
            "holding": h, "open": oq > 1e-9,
            "current": {k: t.get(k) for k in (
                "bid", "ask", "mark", "bid_qty", "ask_qty", "observed_at",
                "age_s", "freshness", "reason", "source", "market_state",
                "mark_method")},
            "unrealized_pnl_usd": unreal,
            "unrealized_basis": ("open x mark - cost basis (mark = top-of-"
                                 "book exit, gross of exit fees)")
            if unreal is not None else (t.get("reason") or
                                        "NOTHING_HELD"),
            "fills": [{"fill_ref": f.get("fill_ref"),
                       "order_ref": f.get("order_ref"),
                       "direction": f["direction"], "qty": _f(f["qty"]),
                       "price": _f(f["price"]), "fee_usd": _f(f.get(
                           "fee_usd")), "at": iso(f.get("at")),
                       "source": f.get("source")} for f in lg["fills"]],
            "role_of_first_fill": next(
                (role_by_ref.get(f.get("order_ref")) for f in lg["fills"]
                 if f["direction"] == "BUY"), None),
            # A HEDGE LEG: every contract of it was bought by a HEDGE order.
            # It is filled protection of the room's other legs, not a
            # position that itself needs protecting.
            "is_hedge_leg": bool([f for f in lg["fills"]
                                  if f["direction"] == "BUY"]) and all(
                role_by_ref.get(f.get("order_ref")) == "HEDGE"
                for f in lg["fills"] if f["direction"] == "BUY")})
    # ORDERS
    orders = []
    for o in sorted(rm["orders"], key=lambda x: _epoch(
            x.get("created_at")) or 0):
        idn = ident_of(o["slug"], o["holding_side"])
        orders.append(_order_view(o, ident=idn, room_kind=kind,
                                  tob=tob(o["slug"], o["holding_side"]),
                                  now=now, account_id=raw.get("account_id")))
    standing = [o for o in orders if o["state"] in STANDING_STATES]
    groups = sorted(g for g in rm["groups"] if g)
    # AGENT PANELS (first: the expected-value line reads Xavier's record)
    xa = raw.get("xavier") or {}
    xpanels = []
    for g in groups:
        xpanels.append(xavier_panel(
            group_id=g, kind=book, assessment=(xa.get("assessments")
                                               or {}).get(g),
            thesis=(xa.get("theses") or {}).get(g),
            review=(xa.get("reviews") or {}).get(g),
            standing_orders=[o for o in standing if o.get("group_id") == g],
            group_orders=[o for o in orders if o.get("group_id") == g],
            group_legs=[lg for lg in legs if lg["group_id"] == g],
            venue=venue,
            latest_valuation=(xa.get("latest_valuations") or {}).get(g),
            limit_s=xa.get("limit_s"),
            cadence_s=float(xa.get("cadence_s") or (
                PAPER_BACKSTOP_DEFAULT_S if book == B_PAPER
                else ACTUAL_MANAGEMENT_EVERY_S)),
            now=now, schema_present=xa.get("schema_present", True)))
    probs = outcome_probabilities(xpanels, legs, outcomes) \
        if kind == K_EVT else {"p": None, "reason": (
            "UNGROUPED_ROOM: no recorded probability is oriented to the "
            "contract's YES/NO here")}
    for o in standing + [o for o in orders if o["state"] == S_PROPOSED]:
        o["if_it_fills"] = if_it_fills(o, legs, outcomes, probs)
    sc = scenarios(legs, orders, outcomes, probs)
    if sc.get("available"):
        sc["outcome_labels"] = labels
    protection = room_protection(legs, orders, outcomes)
    # each group's own ledger, priced on the room's outcomes (Xavier panel)
    for x in xpanels:
        g = x["group_id"]
        gp = room_protection([lg for lg in legs if lg["group_id"] == g],
                             [o for o in orders if o.get("group_id") == g],
                             outcomes)
        x["protection"] = dict(
            gp, unfilled_resting_protection_qty=gp["standing_order_qty"])
    open_legs = [lg for lg in legs if lg["open"]]
    marks_ok = all(lg["unrealized_pnl_usd"] is not None for lg in open_legs)
    unreal = (round(sum(lg["unrealized_pnl_usd"] for lg in open_legs), 6)
              if open_legs and marks_ok else None)
    realized = round(sum(float(lg["holding"]["realized_pnl_usd"] or 0)
                         for lg in legs), 6)
    basis = round(sum(float(lg["holding"]["cost_basis_usd"] or 0)
                      for lg in open_legs), 6)
    cur = sc.get("current") if sc.get("available") else None
    stale_marks = [lg["leg_key"] for lg in open_legs
                   if lg["current"]["freshness"] == "STALE"]
    economic = {
        "net_exposure": (None if cur is None else {
            "per_outcome": cur["rows"], "worst": cur["worst"],
            "best": cur["best"]}),
        "net_exposure_reason": None if cur else sc.get("reason"),
        "directional": _directional(legs, outcomes, labels)
        if kind == K_EVT else None,
        "mark_value_usd": (round(sum(float(lg["holding"]["open_qty"] or 0)
                                     * float(lg["current"]["mark"])
                                     for lg in open_legs), 6)
                           if open_legs and marks_ok else None),
        "unrealized_pnl_usd": unreal,
        "unrealized_reason": (None if unreal is not None else
                              "NOTHING_HELD" if not open_legs else
                              "A_HELD_LEG_HAS_NO_MARK"),
        "realized_pnl_usd": realized,
        "cost_basis_usd": basis,
        "capital_at_risk_usd": None if cur is None else cur[
            "capital_at_risk_usd"],
        "locked_pnl_usd": None if cur is None else cur["locked_pnl_usd"],
        "expected_pnl_usd": None if cur is None else cur["expected_pnl_usd"],
        "expected_reason": None if cur is None else cur["expected_reason"],
        "capital_at_risk_basis": ("the largest loss from here over the "
                                  "played outcomes: -min(payout - remaining "
                                  "cost basis)"),
        "stale_marks": stale_marks,
        "fees_usd": round(sum(float(lg["holding"]["fees_usd"] or 0)
                              for lg in legs), 6),
        "fees_known": all(lg["holding"]["fees_known"] for lg in legs),
        "money_label": ("FICTIONAL USD - SIMULATED EXECUTION" if book ==
                        B_PAPER else "REAL USD - %s ACCOUNT" % venue)}
    by_state: dict[str, list] = {}
    for o in orders:
        by_state.setdefault(o["state"], []).append(o["order_ref"])
    entry = next((o for o in orders if o.get("role") == "ENTRY"
                  and float(o.get("filled_qty") or 0) > 0), None)
    chain = {
        "entry": None if entry is None else {
            "order_ref": entry["order_ref"],
            "instrument": entry["instrument"]["label"],
            "avg_fill_price": entry["avg_fill_price"],
            "filled_qty": entry["filled_qty"],
            "decision_id": entry.get("decision_id"),
            "at": entry["created_at"]},
        "holdings": [lg["leg_key"] for lg in open_legs],
        "correlated_instruments": sorted({"%s %s" % (
            lg["instrument"]["slug"], lg["holding_side"]) for lg in legs}
            | {"%s %s" % (o["instrument"]["slug"], o["holding_side"])
               for o in orders}),
        "proposed": by_state.get(S_PROPOSED, []),
        "submitted": by_state.get(S_SUBMITTED, []),
        "unknown": by_state.get(S_UNKNOWN, []),
        "standing": by_state.get(S_RESTING, []),
        "partially_filled": by_state.get(S_PARTIAL, []),
        "cancel_pending": by_state.get(S_CANCEL_PENDING, []),
        "filled": by_state.get(S_FILLED, []),
        "terminal_unfilled": (by_state.get(S_CANCELLED, [])
                              + by_state.get(S_EXPIRED, [])
                              + by_state.get(S_REJECTED, []))}
    active = bool(open_legs) or any(o["state"] in LIVE_STATES
                                    for o in orders)
    dec_ids = {o.get("decision_id") for o in orders if o.get("decision_id")}
    k_rows = [r for r in raw.get("karen") or []
              if (r.get("target_kind") == "paper_decisions"
                  and r.get("target_id") in dec_ids)
              or (r.get("target_kind") in ("paper_xavier_reviews",)
                  and r.get("_group_id") in groups)
              or (r.get("target_kind") == "smalllive_reconciliations"
                  and r.get("target_id") in groups)]
    au = raw.get("audrey") or {}
    subjects = set(groups) | dec_ids | {o["order_ref"] for o in orders}
    a_find = [f for f in au.get("findings") or []
              if f.get("subject") in subjects]
    a_rec = [r for r in au.get("reconciliations") or []
             if r.get("group_id") in groups and r.get("venue") == venue]
    a_pm = [p for p in au.get("postmortems") or []
            if p.get("group_id") in groups]
    ed = raw.get("archer") or {}
    slugs_sides = {(o["instrument"]["slug"], o["holding_side"])
                   for o in orders} | {(lg["instrument"]["slug"],
                                        lg["holding_side"]) for lg in legs}
    e_rows = [e for e in ed.get("rows") or []
              if e.get("decision_id") in dec_ids
              or (e.get("us_market_slug"), e.get("holding_side"))
              in slugs_sides]
    # GAME STATE
    ev = idn0.get("event_slug")
    teams = []
    mu = raw.get("matchups") or {}
    for s in sorted(rm["slugs"]):
        if mu.get(str(s).lower()):
            teams = mu[str(s).lower()]
            break
    gs = game_state_view((raw.get("game_state") or {}).get(ev or "") or
                         (raw.get("game_state") or {}).get(
                             "slug:" + next(iter(sorted(rm["slugs"])), "")),
                         teams=teams, now=now, event_slug=ev,
                         game_start=idn0.get("game_start"))
    freshest = [lg["current"]["age_s"] for lg in legs
                if lg["current"]["age_s"] is not None]
    return {
        "version": VERSION, "group_key": rm["group_key"], "kind": kind,
        "book": book, "venue": venue,
        "grouping": ("ESTABLISHED_EVENT_AND_SETTLEMENT_IDENTITY"
                     if kind == K_EVT else "UNGROUPED"),
        "ungrouped_reason": None if kind == K_EVT else (
            idn0.get("reason") or R_NO_PREMAP),
        "identity": {k: idn0.get(k) for k in (
            "status", "reason", "event_slug", "event_title",
            "settlement_class", "sports_type", "game_start", "basis",
            "teams")},
        "event": {"event_slug": ev, "title": idn0.get("event_title"),
                  "sports_type": idn0.get("sports_type"),
                  "game_start": idn0.get("game_start")},
        "outcomes": outcomes, "outcome_labels": labels,
        "active": active, "groups": groups,
        "legs": legs, "orders": orders, "chain": chain,
        "standing_orders": [o["order_ref"] for o in standing],
        "protection": protection,
        "economic": economic, "scenarios": sc,
        "xavier": xpanels, "karen": karen_view(k_rows),
        "audrey": audrey_view(a_find, a_rec, a_pm),
        "archer": archer_view(bool(ed.get("present")), e_rows),
        "game_state": gs,
        "freshness": {"youngest_book_age_s": min(freshest)
                      if freshest else None,
                      "oldest_book_age_s": max(freshest)
                      if freshest else None,
                      "stale_marks": stale_marks},
        "read_only": True}


def _directional(legs: list, outcomes, labels) -> dict | None:
    """Held contracts paying on each played outcome: the lean of the room
    (YES Yankees 100 vs YES Red Sox 40 = 60 net on the Yankees)."""
    if not outcomes:
        return None
    per = {o: 0.0 for o in outcomes}
    for lg in legs:
        q = float(lg["holding"]["open_qty"] or 0)
        for o in lg.get("pays_on") or []:
            if o in per:
                per[o] += q
    return {"contracts_paying_on": {labels.get(o, o): round(v, 6)
                                    for o, v in per.items()},
            "basis": "open held contracts whose payout outcome is each "
                     "outcome"}


def summarize(room: dict) -> dict:
    """One list row."""
    x = next((p for p in room["xavier"] if p.get("status") == "OK"), None)
    eco = room["economic"]
    return {
        "group_key": room["group_key"], "book": room["book"],
        "venue": room["venue"], "kind": room["kind"],
        "grouping": room["grouping"],
        "ungrouped_reason": room["ungrouped_reason"],
        "event": room["event"],
        "outcome_labels": room.get("outcome_labels"),
        "teams": room["game_state"].get("teams") or [],
        "legs": len(room["legs"]),
        "open_legs": sum(1 for lg in room["legs"] if lg["open"]),
        "orders_live": sum(1 for o in room["orders"]
                           if o["state"] in LIVE_STATES),
        "orders_by_state": {s: sum(1 for o in room["orders"]
                                   if o["state"] == s)
                            for s in sorted({o["state"]
                                             for o in room["orders"]})},
        "instruments": sorted({lg["instrument"]["label"] or "?"
                               for lg in room["legs"]}),
        "net_exposure": {"worst": (eco["net_exposure"] or {}).get("worst"),
                         "best": (eco["net_exposure"] or {}).get("best")}
        if eco["net_exposure"] else None,
        "net_exposure_reason": eco["net_exposure_reason"],
        "mark_value_usd": eco["mark_value_usd"],
        "unrealized_pnl_usd": eco["unrealized_pnl_usd"],
        "unrealized_reason": eco["unrealized_reason"],
        "realized_pnl_usd": eco["realized_pnl_usd"],
        "capital_at_risk_usd": eco["capital_at_risk_usd"],
        "money_label": eco["money_label"],
        "protection": {k: (room.get("protection") or {}).get(k) for k in (
            "position_qty", "unprotected_qty", "standing_order_qty",
            "pending_order_qty", "filled_protection_qty",
            "conditional_floor_if_filled_usd", "conditional_floor_label",
            "conditional_floor_reason", "realized_floor_usd",
            "realized_protection_usd", "current_worst_case_exposure_usd",
            "line", "rule")},
        "executable_exit_usd": ((room.get("protection") or {}).get(
            "current_executable_exit") or {}).get(
                "proceeds_at_top_level_usd"),
        "xavier": None if x is None else {
            # the action only while CURRENT; the state otherwise (owner P0)
            "recommendation": x.get("recommendation") or x.get(
                "recommendation_state"),
            "recommendation_state": x.get("recommendation_state"),
            "recorded_recommendation": x.get("recorded_recommendation"),
            "freshness_expires_at": ((x.get("freshness") or {}).get(
                "valuation") or {}).get("expires_at"),
            "evidence_state": (x.get("evidence") or {}).get("state"),
            "thesis_state": (x.get("thesis") or {}).get("state"),
            "assessed_at": x.get("assessed_at"),
            "next_review_due_at": x.get("next_review_due_at")},
        "xavier_reason": None if x is not None else (
            (room["xavier"][0].get("why") if room["xavier"] else
             "NO_GROUP_IN_THIS_ROOM_IS_MANAGED_BY_XAVIER_YET")),
        "game_status": room["game_state"].get("status"),
        "game_live": room["game_state"].get("live"),
        "freshness": room["freshness"], "active": room["active"],
        "karen": room["karen"]["status"], "audrey": room["audrey"]["status"],
        "archer": room["archer"]["status"]}


# ═════════════════════════════════════════════════════════════════════
# 7 · READS (bounded SELECTs; the route wraps them READ ONLY + timeout)
# ═════════════════════════════════════════════════════════════════════

async def _exists(conn, table: str) -> bool:
    return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL",
                                    table))


async def _paper(conn, now: float, account_id: str) -> dict:
    out = {"orders": [], "fills": [], "settlements": [], "available": True}
    if not await _exists(conn, "paper_orders"):
        out.update(available=False, why="PAPER_TABLES_NOT_PRESENT")
        return out
    groups = [r["group_id"] for r in await conn.fetch(
        """WITH o AS (SELECT group_id, max(updated_at) AS at
                        FROM paper_orders
                       WHERE account_id = $1 AND state = ANY($2::text[])
                       GROUP BY group_id),
                h AS (SELECT f.group_id, max(f.filled_at) AS at
                        FROM paper_fills f
                        JOIN (""" + CANONICAL_OPEN_POSITIONS_SQL + """) c
                          ON c.account_id = f.account_id
                         AND c.group_id = f.group_id
                         AND c.us_market_slug = f.us_market_slug
                         AND c.holding_side = f.holding_side
                       WHERE f.account_id = $1
                       GROUP BY f.group_id, f.us_market_slug,
                                f.holding_side)
           SELECT group_id, max(at) AS at FROM (
               SELECT * FROM o UNION ALL SELECT * FROM h) x
            GROUP BY group_id ORDER BY max(at) DESC LIMIT $3""",
        account_id, list(PAPER_OPEN_RAW), MAX_GROUPS)]
    out["groups"] = groups
    if groups:
        for r in await conn.fetch(
                """SELECT order_id, group_id, role, direction, holding_side,
                          us_market_slug, order_type, time_in_force, qty,
                          filled_qty, limit_price, wire_price, state,
                          decision_id, decided_at, expires_at, terminal_at,
                          terminal_reason, created_at, updated_at, strategy,
                          (SELECT sum(f.qty * f.price) / nullif(sum(f.qty), 0)
                             FROM paper_fills f
                            WHERE f.order_id = o.order_id) AS avg_fill,
                          (SELECT sum(f.fee_usd) FROM paper_fills f
                            WHERE f.order_id = o.order_id) AS fees
                     FROM paper_orders o
                    WHERE account_id = $1 AND group_id = ANY($2::text[])
                    ORDER BY created_at LIMIT $3""",
                account_id, groups, MAX_ORDERS):
            out["orders"].append({
                "order_ref": r["order_id"], "source": "paper_orders",
                "book": B_PAPER, "venue": V_PM, "group_id": r["group_id"],
                "role": r["role"], "direction": r["direction"],
                "holding_side": r["holding_side"],
                "slug": r["us_market_slug"], "order_type": r["order_type"],
                "tif": r["time_in_force"], "qty": _f(r["qty"]),
                "filled_qty": _f(r["filled_qty"]),
                # paper limit_price is already OUR cost space (held side)
                "limit": _f(r["limit_price"]),
                "wire_price": _f(r["wire_price"]),
                "raw_state": r["state"],
                "state": canonical_state(r["state"], table="paper_orders"),
                "decision_id": r["decision_id"],
                "created_at": _epoch(r["created_at"]),
                "updated_at": _epoch(r["updated_at"]),
                "expires_at": _epoch(r["expires_at"]),
                "terminal_reason": r["terminal_reason"],
                "strategy": r["strategy"], "avg_fill": _f(r["avg_fill"]),
                "fees_usd": _f(r["fees"]) if r["fees"] is not None else 0.0})
        for r in await conn.fetch(
                """SELECT fill_id, order_id, group_id, direction,
                          holding_side, us_market_slug, qty, price, fee_usd,
                          filled_at
                     FROM paper_fills
                    WHERE account_id = $1 AND group_id = ANY($2::text[])
                    ORDER BY filled_at""", account_id, groups):
            out["fills"].append({
                "fill_ref": r["fill_id"], "order_ref": r["order_id"],
                "group_id": r["group_id"], "direction": r["direction"],
                "holding_side": r["holding_side"],
                "slug": r["us_market_slug"], "qty": float(r["qty"]),
                "price": float(r["price"]), "fee_usd": float(r["fee_usd"]),
                "at": _epoch(r["filled_at"]),
                "source": "paper_fills (SIMULATOR)"})
        for r in await conn.fetch(
                """SELECT DISTINCT ON (position_key) position_key, group_id,
                          us_market_slug, holding_side, qty, payout_usd,
                          outcome, version, settled_at
                     FROM paper_settlements
                    WHERE account_id = $1 AND group_id = ANY($2::text[])
                    ORDER BY position_key, version DESC""",
                account_id, groups):
            out["settlements"].append({
                "group_id": r["group_id"], "slug": r["us_market_slug"],
                "holding_side": r["holding_side"], "qty": float(r["qty"]),
                "payout_usd": float(r["payout_usd"]),
                "outcome": r["outcome"], "version": r["version"],
                "settled_at": iso(r["settled_at"])})
    # PROPOSED: Derek's ENTER decisions with no order yet, inside the window,
    # that no ORDER_REFUSED finding names.
    for r in await conn.fetch(
            """SELECT d.decision_id, d.us_market_slug, d.holding_side,
                      d.intent, d.proposed_qty, d.limit_price, d.decided_at,
                      d.strategy
                 FROM paper_decisions d
                WHERE d.account_id = $1 AND d.verdict = 'ENTER'
                  AND d.decided_at >= to_timestamp($2)
                  AND d.us_market_slug IS NOT NULL
                  AND d.proposed_qty IS NOT NULL
                  AND NOT EXISTS (SELECT 1 FROM paper_orders o
                                   WHERE o.decision_id = d.decision_id)
                  AND NOT EXISTS (SELECT 1 FROM paper_audrey_findings a
                                   WHERE a.subject = d.decision_id)
                ORDER BY d.decided_at DESC LIMIT 200""",
            account_id, now - PROPOSED_WINDOW_S):
        side = r["holding_side"] or side_of_intent(r["intent"])
        if side not in ("LONG", "SHORT"):
            continue
        out["orders"].append({
            "order_ref": r["decision_id"],
            "source": "paper_decisions (Derek ENTER, no order yet)",
            "book": B_PAPER, "venue": V_PM, "group_id": None,
            "role": "ENTRY", "direction": "BUY", "holding_side": side,
            "slug": r["us_market_slug"], "order_type": None, "tif": None,
            "qty": _f(r["proposed_qty"]), "filled_qty": 0.0,
            "limit": _f(r["limit_price"]), "wire_price": None,
            "raw_state": "DECIDED_ENTER_NOT_ORDERED", "state": S_PROPOSED,
            "decision_id": r["decision_id"],
            "created_at": _epoch(r["decided_at"]),
            "updated_at": _epoch(r["decided_at"]),
            "strategy": r["strategy"], "avg_fill": None, "fees_usd": None})
    return out


async def _actual_pm(conn, now: float) -> dict:
    out = {"orders": [], "fills": [], "settlements": [], "available": True,
           "handoffs": {}}
    if not await _exists(conn, "execmirror_orders"):
        out.update(available=False, why="EXECMIRROR_TABLES_NOT_PRESENT")
        return out
    has_h = await _exists(conn, "smalllive_handoffs")
    if has_h:
        sql = """SELECT coalesce(group_id, 'mirror:' || mirror_id) AS g
                   FROM execmirror_orders WHERE state = ANY($1::text[])
                 UNION
                 SELECT group_id FROM smalllive_handoffs
                  WHERE venue = 'POLYMARKET' AND state = 'OPEN'
                 LIMIT $2"""
    else:
        sql = """SELECT DISTINCT coalesce(group_id, 'mirror:' || mirror_id)
                        AS g
                   FROM execmirror_orders WHERE state = ANY($1::text[])
                  LIMIT $2"""
    groups = [r["g"] for r in await conn.fetch(sql, list(MIRROR_OPEN_RAW),
                                                MAX_GROUPS)]
    out["groups"] = groups
    if not groups:
        return out
    for r in await conn.fetch(
            """SELECT mirror_id, paper_order_id, group_id, role, strategy,
                      us_market_slug, intent, order_type, tif, wire_price,
                      good_till, live_qty, live_qty_exact, state, exclusion,
                      venue_state,
                      cum_qty, avg_px, fees_usd, created_at, updated_at,
                      accepted_at, execution_intent_id
                 FROM execmirror_orders
                WHERE coalesce(group_id, 'mirror:' || mirror_id)
                      = ANY($1::text[])
                ORDER BY created_at LIMIT $2""", groups, MAX_ORDERS):
        side = side_of_intent(r["intent"])
        if side is None:
            continue
        out["orders"].append({
            "order_ref": r["mirror_id"], "source": "execmirror_orders",
            "book": B_ACTUAL, "venue": V_PM, "group_id": r["group_id"],
            "role": r["role"], "direction": direction_of_intent(r["intent"]),
            "holding_side": side, "slug": r["us_market_slug"],
            "order_type": r["order_type"], "tif": r["tif"],
            # exact (rc6.3 pmus-sizing, migration 368): a fractional order
            "qty": float(r["live_qty_exact"] if r["live_qty_exact"] is not None
                         else (r["live_qty"] or 0)),
            "filled_qty": float(r["cum_qty"] or 0),
            "limit": held_price(r["wire_price"], side),
            "wire_price": _f(r["wire_price"]),
            "raw_state": r["state"],
            "state": canonical_state(r["state"], table="execmirror_orders"),
            "decision_id": None, "paper_order_id": r["paper_order_id"],
            "created_at": _epoch(r["created_at"]),
            "updated_at": _epoch(r["updated_at"]),
            "expires_at": _epoch(r["good_till"]),
            "terminal_reason": r["exclusion"], "strategy": r["strategy"],
            "avg_fill": held_price(r["avg_px"], side)
            if r["avg_px"] is not None else None,
            "fees_usd": _f(r["fees_usd"]), "live_scale": "1:1000 of paper"})
    for r in await conn.fetch(
            """SELECT f.fill_key, f.mirror_id, f.group_id, f.us_market_slug,
                      f.intent, f.qty, f.price, f.fee_usd, f.observed_at
                 FROM execmirror_fills f
                WHERE coalesce(f.group_id, 'mirror:' || f.mirror_id)
                      = ANY($1::text[])
                ORDER BY f.observed_at""", groups):
        side = side_of_intent(r["intent"])
        if side is None:
            continue
        out["fills"].append({
            "fill_ref": r["fill_key"], "order_ref": r["mirror_id"],
            "group_id": r["group_id"],
            "direction": direction_of_intent(r["intent"]),
            "holding_side": side, "slug": r["us_market_slug"],
            "qty": float(r["qty"]), "price": held_price(r["price"], side),
            "fee_usd": float(r["fee_usd"]), "at": _epoch(r["observed_at"]),
            "source": "execmirror_fills (VENUE_ORDER_RECORD)"})
    if has_h:
        for r in await conn.fetch(
                """SELECT group_id, live_held, state, avg_entry_px,
                          first_live_fill_at
                     FROM smalllive_handoffs
                    WHERE venue = 'POLYMARKET' AND group_id = ANY($1)""",
                groups):
            out["handoffs"][r["group_id"]] = dict(r)
    return out


async def _actual_kalshi(conn, now: float) -> dict:
    out = {"orders": [], "fills": [], "settlements": [], "available": True}
    if not await _exists(conn, "kalshi_live_intents"):
        out.update(available=False, why="KALSHI_TABLES_NOT_PRESENT")
        return out
    rows = await conn.fetch(
        """SELECT link_id, mirror_id, paper_order_id, paper_decision_id,
                  group_id, role, us_market_slug, ticker, holding, action,
                  intent, rounded_qty, kalshi_price, cum_qty, state,
                  exclusion, created_at, updated_at, fee_estimate
             FROM kalshi_live_intents
            WHERE coalesce(group_id, 'link:' || link_id) IN (
                  SELECT coalesce(group_id, 'link:' || link_id)
                    FROM kalshi_live_intents
                   WHERE state = ANY($1::text[])
                  UNION
                  SELECT group_id FROM smalllive_handoffs
                   WHERE venue = 'KALSHI' AND state = 'OPEN')
            ORDER BY created_at LIMIT $2""",
        list(MIRROR_OPEN_RAW), MAX_ORDERS) if await _exists(
        conn, "smalllive_handoffs") else []
    links = []
    for r in rows:
        side = r["holding"]
        if side not in ("LONG", "SHORT"):
            continue
        links.append(r["link_id"])
        out["orders"].append({
            "order_ref": r["link_id"], "source": "kalshi_live_intents",
            "book": B_ACTUAL, "venue": V_KALSHI, "group_id": r["group_id"],
            "role": r["role"],
            "direction": "BUY" if r["action"] == "buy" else "SELL",
            "holding_side": side, "slug": r["us_market_slug"],
            "ticker": r["ticker"], "order_type": None, "tif": None,
            "qty": float(r["rounded_qty"] or 0),
            "filled_qty": float(r["cum_qty"] or 0),
            # kalshi_price is the LEG price on the mapped YES ticker, i.e.
            # already the held side's price (kalshi_orders.plan)
            "limit": _f(r["kalshi_price"]), "wire_price": None,
            "raw_state": r["state"],
            "state": canonical_state(r["state"],
                                     table="kalshi_live_intents"),
            "decision_id": r["paper_decision_id"],
            "created_at": _epoch(r["created_at"]),
            "updated_at": _epoch(r["updated_at"]),
            "terminal_reason": r["exclusion"], "avg_fill": None,
            "fees_usd": None, "live_scale": "1:1000 of paper"})
    if links:
        by_link = {o["order_ref"]: o for o in out["orders"]}
        for r in await conn.fetch(
                """SELECT trade_id, link_id, count, price, fee_usd,
                          created_time, observed_at
                     FROM kalshi_live_fills WHERE link_id = ANY($1)
                    ORDER BY observed_at""", links):
            o = by_link.get(r["link_id"])
            if o is None:
                continue
            out["fills"].append({
                "fill_ref": r["trade_id"], "order_ref": r["link_id"],
                "group_id": o["group_id"], "direction": o["direction"],
                "holding_side": o["holding_side"], "slug": o["slug"],
                "qty": float(r["count"]), "price": float(r["price"]),
                "fee_usd": None if r["fee_usd"] is None else float(
                    r["fee_usd"]),
                "at": _epoch(r["created_time"] or r["observed_at"]),
                "source": "kalshi_live_fills (VENUE_FILLS_ENDPOINT)"})
        for o in out["orders"]:
            fl = [f for f in out["fills"] if f["order_ref"] == o["order_ref"]]
            q = sum(f["qty"] for f in fl)
            if q > 0:
                o["avg_fill"] = round(sum(f["qty"] * f["price"]
                                          for f in fl) / q, 6)
                fees = [f["fee_usd"] for f in fl]
                o["fees_usd"] = (None if any(x is None for x in fees)
                                 else round(sum(fees), 6))
    return out


async def _premap(conn, slugs: list) -> list:
    if not slugs or not await _exists(conn, "us_premap"):
        return []
    rows = await conn.fetch(
        """SELECT market_slug, intent, event_slug, event_title, kind,
                  sports_type, team_abbr, team_name, team_safe_name, team_id,
                  team_league, side_norm, line, game_start
             FROM us_premap
            WHERE event_slug IN (SELECT event_slug FROM us_premap
                                  WHERE market_slug = ANY($1::text[]))
               OR market_slug = ANY($1::text[])
            LIMIT 2000""", [str(s).lower() for s in slugs])
    return [dict(r) for r in rows]


async def _books(conn, slugs: list) -> dict:
    if not slugs or not await _exists(conn, "paper_book_observations"):
        return {}
    rows = await conn.fetch(
        """SELECT o.* FROM unnest($1::text[]) s(slug)
           CROSS JOIN LATERAL (
               SELECT obs_id, us_market_slug, observed_at, source, bids,
                      offers, market_state
                 FROM paper_book_observations b
                WHERE b.us_market_slug = s.slug AND b.error IS NULL
                ORDER BY b.observed_at DESC LIMIT 1) o""", slugs)
    return {r["us_market_slug"]: dict(r) for r in rows}


async def _xavier(conn, book: str, groups: list) -> dict:
    out = {"assessments": {}, "theses": {}, "reviews": {},
           "schema_present": True}
    # the same three-table check as agents.xavier_management.has_schema,
    # inlined so this read path imports no agent module
    if not (await _exists(conn, "xavier_management_assessments")
            and await _exists(conn, "xavier_entry_theses")):
        out["schema_present"] = False
        return out
    if not groups:
        return out
    for r in await conn.fetch(
            """SELECT DISTINCT ON (group_id) * FROM
                      xavier_management_assessments
                WHERE position_kind = $1 AND group_id = ANY($2::text[])
                ORDER BY group_id, assessed_at DESC, assessment_id DESC""",
            book, groups):
        out["assessments"][r["group_id"]] = dict(r)
    for r in await conn.fetch(
            "SELECT * FROM xavier_entry_theses WHERE position_kind = $1 "
            "   AND group_id = ANY($2::text[])", book, groups):
        out["theses"][r["group_id"]] = dict(r)
    if book == B_PAPER:
        for r in await conn.fetch(
                """SELECT DISTINCT ON (group_id) review_id, group_id,
                          reviewed_at, trigger, recommendation, refusal,
                          selection, exposure, standing, exceptional, action
                     FROM paper_xavier_reviews
                    WHERE group_id = ANY($1::text[])
                    ORDER BY group_id, reviewed_at DESC""", groups):
            out["reviews"][r["group_id"]] = dict(r, _table=(
                "paper_xavier_reviews"))
        # THE NEWER VALUATION OF EACH GROUP'S OWN CONTRACT, for the
        # read-time validity of Xavier's recommendation (owner P0): a
        # changed primary valuation makes the stored recommendation INVALID
        out["latest_valuations"] = {}
        try:
            if await _exists(conn, "external_valuations"):
                for r in await conn.fetch(
                        XF.LATEST_VALUATION_SQL, groups,
                        time.time() - XF.CONTEXT_VALUATION_LOOKBACK_S):
                    out["latest_valuations"][r["group_id"]] = {
                        "id": r["id"], "probability": _f(r["probability"]),
                        "observed_at": _epoch(r["observed_at"])}
        except Exception as exc:                                # noqa: BLE001
            out["latest_valuations_unchecked"] = type(exc).__name__
        try:
            # the paper session's OWN freshness limit (entry.
            # pinnacle_max_age_s): the existing threshold, read, for rows
            # that did not record theirs
            lim = await conn.fetchval(
                "SELECT (config->'entry'->>'pinnacle_max_age_s')::float8 "
                "  FROM paper_sessions ORDER BY started_at DESC LIMIT 1")
            out["limit_s"] = None if lim is None else float(lim)
        except Exception:                                       # noqa: BLE001
            out["limit_s"] = None
        try:
            b = await conn.fetchval(
                "SELECT (config->'cadence'->>'xavier_backstop_s')::float8 "
                "  FROM paper_sessions ORDER BY started_at DESC LIMIT 1")
            out["cadence_s"] = float(b) if b is not None else (
                PAPER_BACKSTOP_DEFAULT_S)
        except Exception:                                       # noqa: BLE001
            out["cadence_s"] = PAPER_BACKSTOP_DEFAULT_S
    else:
        out["cadence_s"] = ACTUAL_MANAGEMENT_EVERY_S
        if await _exists(conn, "smalllive_reviews"):
            for r in await conn.fetch(
                    """SELECT DISTINCT ON (h.group_id) r.review_id,
                              h.group_id, r.reviewed_at, r.action,
                              r.paper_recommendation AS recommendation,
                              r.detail
                         FROM smalllive_reviews r
                         JOIN smalllive_handoffs h USING (handoff_id)
                        WHERE h.group_id = ANY($1::text[])
                        ORDER BY h.group_id, r.reviewed_at DESC""", groups):
                out["reviews"][r["group_id"]] = dict(
                    r, _table="smalllive_reviews",
                    action={"taken": r["action"]})
    return out


async def _karen(conn, decision_ids: list, groups: list) -> list:
    if not await _exists(conn, "karen_challenges"):
        return []
    rows = await conn.fetch(
        """SELECT k.challenge_id, k.detector, k.target_agent, k.target_kind,
                  k.target_id, k.severity, k.state, k.claim, k.outcome,
                  k.challenged_at, k.response_stance, r.group_id AS _group_id
             FROM karen_challenges k
             LEFT JOIN paper_xavier_reviews r
               ON k.target_kind = 'paper_xavier_reviews'
              AND r.review_id = k.target_id
            WHERE (k.target_kind = 'paper_decisions'
                   AND k.target_id = ANY($1::text[]))
               OR (k.target_kind = 'paper_xavier_reviews'
                   AND r.group_id = ANY($2::text[]))
               OR (k.target_kind = 'smalllive_reconciliations'
                   AND k.target_id = ANY($2::text[]))
            ORDER BY k.challenged_at DESC LIMIT 200""",
        decision_ids, groups)
    return [dict(r) for r in rows]


async def _audrey(conn, subjects: list, groups: list) -> dict:
    out = {"findings": [], "reconciliations": [], "postmortems": []}
    if subjects and await _exists(conn, "paper_audrey_findings"):
        out["findings"] = [dict(r) for r in await conn.fetch(
            """SELECT finding_id, kind, severity, subject, found_at
                 FROM paper_audrey_findings
                WHERE subject = ANY($1::text[])
                ORDER BY found_at DESC LIMIT 200""", subjects)]
    if groups and await _exists(conn, "smalllive_reconciliations"):
        out["reconciliations"] = [dict(r) for r in await conn.fetch(
            """SELECT group_id, venue, status, reconciled_at, discrepancies
                 FROM smalllive_reconciliations
                WHERE group_id = ANY($1::text[])""", groups)]
    if groups and await _exists(conn, "position_postmortems"):
        out["postmortems"] = [dict(r) for r in await conn.fetch(
            """SELECT position_key, group_id, realized_pnl_usd,
                      unexplained_usd, decomposition_complete, closed_at
                 FROM position_postmortems
                WHERE group_id = ANY($1::text[])
                ORDER BY closed_at DESC NULLS LAST LIMIT 100""", groups)]
    return out


async def _archer(conn, decision_ids: list, slugs: list) -> dict:
    if not await _exists(conn, "eddie_execution_estimates"):
        return {"present": False, "rows": []}
    rows = await conn.fetch(
        """SELECT to_jsonb(e) AS j FROM eddie_execution_estimates e
            WHERE e.decision_id = ANY($1::text[])
               OR e.us_market_slug = ANY($2::text[])
            ORDER BY e.estimated_at DESC LIMIT 200""",
        decision_ids, slugs)
    return {"present": True, "rows": [_j(r["j"]) or {} for r in rows]}


async def _game_state(conn, event_slugs: list, decision_ids: list) -> dict:
    out: dict = {}
    if event_slugs and await _exists(conn, "venue_fixture_metadata"):
        for r in await conn.fetch(
                """SELECT venue_fixture_key, event_state_raw, play_has_begun,
                          home_team, away_team, source, source_url,
                          retrieved_at
                     FROM venue_fixture_metadata
                    WHERE venue = 'PMUS'
                      AND venue_fixture_key = ANY($1::text[])""",
                ["event:%s" % e for e in event_slugs]):
            out[r["venue_fixture_key"][len("event:"):]] = dict(
                r, _table="venue_fixture_metadata",
                _binding=("venue_fixture_metadata keyed by the room's own "
                          "venue event identity %s" % r["venue_fixture_key"]))
    # A GLOBAL-CONDITION FIXTURE ROW, reached only through the room's own
    # decision -> valuation link (never a slug scan of external_valuations)
    if decision_ids and await _exists(conn, "fixture_metadata") and \
            await _exists(conn, "paper_decisions"):
        for r in await conn.fetch(
                """SELECT DISTINCT ON (d.us_market_slug) d.us_market_slug,
                          fm.event_state_raw, fm.play_has_begun,
                          fm.home_team, fm.away_team, fm.source,
                          fm.source_url, fm.retrieved_at
                     FROM paper_decisions d
                     JOIN external_valuations v ON v.id = d.valuation_id
                     JOIN fixture_metadata fm
                       ON fm.condition_id = v.condition_id
                    WHERE d.decision_id = ANY($1::text[])
                    ORDER BY d.us_market_slug, fm.retrieved_at DESC""",
                decision_ids):
            out["slug:%s" % r["us_market_slug"]] = dict(
                r, _table="fixture_metadata",
                _binding=("fixture_metadata of the global condition on the "
                          "room's own entry decision -> valuation link "
                          "(market %s)" % r["us_market_slug"]))
    return out


async def load(conn, *, book: str, venue: str, now: float | None = None,
               account_id: str | None = None) -> dict:
    """Every row one book's rooms are built from. Read only."""
    at = float(now if now is not None else time.time())
    account_id = account_id or PAPER_ACCOUNT_ID
    if book == B_PAPER:
        base = await _paper(conn, at, account_id)
    elif venue == V_PM:
        base = await _actual_pm(conn, at)
    else:
        base = await _actual_kalshi(conn, at)
    raw = {"book": book, "venue": venue, "now": at,
           "account_id": account_id if book == B_PAPER else None,
           **base}
    slugs = sorted({o["slug"] for o in base["orders"] if o.get("slug")}
                   | {f["slug"] for f in base["fills"] if f.get("slug")})
    groups = sorted({o["group_id"] for o in base["orders"]
                     if o.get("group_id")}
                    | {f["group_id"] for f in base["fills"]
                       if f.get("group_id")})
    dec = sorted({o["decision_id"] for o in base["orders"]
                  if o.get("decision_id")})
    raw["premap"] = await _premap(conn, slugs)
    raw["books"] = await _books(conn, slugs) if venue == V_PM else {}
    raw["xavier"] = await _xavier(conn, book, groups)
    raw["karen"] = await _karen(conn, dec, groups)
    subjects = sorted(set(groups) | set(dec)
                      | {o["order_ref"] for o in base["orders"]})
    raw["audrey"] = await _audrey(conn, subjects, groups)
    raw["archer"] = await _archer(conn, dec, slugs)
    events = sorted({r["event_slug"] for r in raw["premap"]
                     if r.get("event_slug")})
    raw["game_state"] = await _game_state(conn, events, dec)
    from . import team_logos as TL
    raw["matchups"] = await TL.matchups(conn, slugs)
    return raw


BOOK_VENUES = {B_PAPER: (V_PM,), B_ACTUAL: (V_PM, V_KALSHI)}

VENUE_LABEL = {V_PM: "POLYMARKET US", V_KALSHI: "KALSHI"}
#: Each ACTUAL venue's own control row: connected = it holds the venue's
#: account / key fingerprint (the same test as api/command_equity.lane_of).
#: Never inferred from the other venue.
VENUE_CONTROL = {V_PM: ("execmirror_control", "account_fingerprint"),
                 V_KALSHI: ("kalshi_smalllive_control", "key_fingerprint")}


def venue_connection(venue: str, *, table_present: bool,
                     row: dict | None) -> dict:
    """ONE venue's connection, from ITS OWN control row only. Pure."""
    label = VENUE_LABEL[venue]
    table = VENUE_CONTROL[venue][0]
    if not table_present:
        st, why = "NOT_CONNECTED", "%s_ABSENT" % table.upper()
    elif row is None:
        st, why = "NOT_CONNECTED", "%s_ROW_MISSING" % table.upper()
    elif not row.get("keyed"):
        st, why = "NOT_CONNECTED", ("%s holds no %s" % (
            table, VENUE_CONTROL[venue][1]))
    else:
        st = ("STOPPED" if row.get("stopped") else
              "ENABLED" if row.get("enabled") else "CONNECTED_DISABLED")
        why = None
    return {"venue": venue, "label": label,
            "connected": st != "NOT_CONNECTED",
            "status": st, "why": why,
            "display": "%s \u2014 %s" % (label, st),
            "source": "%s (this venue's own control row; never inferred "
                      "from the other venue)" % table}


async def _venue_connection(conn, venue: str) -> dict:
    table, col = VENUE_CONTROL[venue]
    present = await _exists(conn, table)
    row = None
    if present:
        r = await conn.fetchrow(
            "SELECT (%s IS NOT NULL) AS keyed, enabled, stopped FROM %s "
            " WHERE id = 1" % (col, table))
        row = None if r is None else dict(r)
    return venue_connection(venue, table_present=present, row=row)


async def rooms_payload(conn, *, book: str, now: float | None = None,
                        include_inactive: bool = False) -> dict:
    """GET /api/command/positions/rooms?book=PAPER|ACTUAL."""
    at = float(now if now is not None else time.time())
    venues = {}
    for venue in BOOK_VENUES[book]:
        raw = await load(conn, book=book, venue=venue, now=at)
        rooms = build_rooms(raw)
        items = [summarize(r) for r in rooms
                 if r["active"] or include_inactive]
        conn_state = (await _venue_connection(conn, venue)
                      if book == B_ACTUAL else None)
        venues[venue] = {
            "connection": conn_state,
            "display": (conn_state or {}).get("display") or (
                "PAPER \u00b7 %s (simulated)" % VENUE_LABEL[venue]),
            "available": raw.get("available", True),
            "why": raw.get("why"),
            "rooms": items, "count": len(items),
            "empty_reason": (None if items else (
                raw.get("why") or (
                    "%s_NOT_CONNECTED" % venue if conn_state is not None
                    and not conn_state["connected"] else
                    "NO_ACTIVE_POSITION_OR_LIVE_ORDER_IN_THIS_"
                    "BOOK_AND_VENUE"))),
            "money_label": ("FICTIONAL USD - SIMULATED EXECUTION"
                            if book == B_PAPER else
                            "REAL USD - %s ACCOUNT" % venue)}
    return {"version": VERSION, "as_of": iso(at), "book": book,
            "venues": venues,
            "never_summed": ("PAPER and ACTUAL are separate reads; ACTUAL "
                             "venues are separate sections and no figure "
                             "is added across them"),
            "grouping_rule": ("a room is one venue event under one "
                              "full-game winner settlement variable, with "
                              "each leg's payout outcome read off the "
                              "venue's own catalogue rows; anything else is "
                              "UNGROUPED with its reason"),
            "read_only": True,
            "room_route": "/api/command/positions/room/{group_key}"}


async def room_payload(conn, *, key: str,
                       now: float | None = None) -> dict | None:
    """GET /api/command/positions/room/{group_key}. None when not found."""
    p = parse_group_key(key)
    if p is None:
        return None
    at = float(now if now is not None else time.time())
    raw = await load(conn, book=p["book"], venue=p["venue"], now=at)
    for r in build_rooms(raw):
        if r["group_key"] == key:
            return dict(r, as_of=iso(at), connection=(
                await _venue_connection(conn, p["venue"])
                if p["book"] == B_ACTUAL else None))
    return None


def describe() -> dict:
    return {"version": VERSION, "states": STATE_MEANING,
            "paper_state_map": PAPER_STATE_MAP,
            "mirror_state_map": MIRROR_STATE_MAP,
            "order_state_truth": OST.describe(),
            "mark_method": MARK_METHOD,
            "game_state_max_age_s": GAME_STATE_MAX_AGE_S,
            "score": R_NO_SCORE}
