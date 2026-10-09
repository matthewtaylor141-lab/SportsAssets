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
  2. A position open at the epoch is CARRIED at its epoch mark (below).
     Only post-epoch movement is management P&L; the original cost stays
     for audit.
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
  7. THE EXACT RECONCILIATION (`reconciliation`): OPENING_EQUITY + every
     post-epoch change, itemised per position = MANAGEMENT EQUITY, and
     MANAGEMENT EQUITY + every itemised difference = LEDGER EQUITY (ledger
     cash + every open position at the value this view gives it). Nothing is
     netted away: the pre-management ledger result (losses included), each
     held-outside position's whole ledger result, each post-epoch correction
     of a pre-epoch settlement, and any ledger cash no position explains are
     each a line. Today's equity is never forced to $500,000 and history is
     never hidden: it is the difference, stated.

THE EPOCH MARK, FROM RECORDED EVIDENCE AT THE EPOCH INSTANT ONLY. In order:
  a. BOOK_AT_THE_EPOCH: the exit price of the last error-free book observed
     at or before the epoch and no more than EPOCH_MARK_MAX_AGE_S (the
     ledger's own mark staleness bound) before it -- unchanged.
  b. SETTLEMENT_RECORDED_BY_THE_EPOCH: no such book, but the position's
     first settlement rests on venue outcome reads EVERY one of which was
     recorded at or before the epoch: its value at the epoch was that payout.
  Otherwise UNVERIFIED, with the exact reason read from the same records:
  no book ever observed by the epoch; the last error-free book older than
  the bound (its age stated); the last book in a TERMINAL venue state (the
  market had ended, and the held-mark refresh does not re-read ended
  markets by design -- a terminal TRADING state is not a recorded outcome,
  so the later settlement is shown beside it and is NOT used as a mark);
  every read inside the window failed; or the window book had no exit side.

WHY CARRIED POSITIONS READ UNVERIFIED IN PRODUCTION (Command UI on RC4
7fd4574e, 2026-10-08: "the snapshots cannot be re-based exactly while 4
carried position(s) are EPOCH_OPEN_MARK_UNVERIFIED or $141.80 of post-epoch
ledger cash is held outside"; their current marked value $0). The only mark
source was a book inside the 300 s before midnight, and on 2026-10-05 the
paper path refreshed about 6 held markets a minute against 150+ held
markets ("158 stale marks > 300s, 12 unmarked, 71/241 fresh",
agents/paper_mark_refresh), while ended markets are not re-read at all.
The bound is not widened and no mark is invented: each position now names
which reason applies and carries the evidence. And because a held-outside
flow is timed by its ledger sequence, every equity snapshot taken after the
last unverified position CLOSED re-bases exactly, so the high-water mark is
no longer "the opening and the current equity only" once they have closed.

TIMING: an event belongs to the side of the epoch its LEDGER ENTRY was
committed on (paper_ledger.committed_at), so management cash reconciles to
the ledger exactly: management cash = ledger cash - (ledger cash at the
epoch - opening cash) - every post-epoch ledger cash flow held outside.
"""
from __future__ import annotations

import datetime as _dt
import json
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
#: migration 171's paper_ledger_rules_ck: INITIAL_FUNDING is exactly this
LEDGER_FUNDING_USD = L.STARTING_CASH_USD

R_UNVERIFIED = "EPOCH_OPEN_MARK_UNVERIFIED"
R_NO_EPOCH_BOOK = "NO_ERROR_FREE_BOOK_AT_OR_BEFORE_THE_EPOCH_WITHIN_%dS" % int(
    EPOCH_MARK_MAX_AGE_S)
#: THE PRECISE REASONS an epoch mark is unverified, each read from recorded
#: observations (R_NO_EPOCH_BOOK stays the family they refine)
R_NEVER_OBSERVED = "EPOCH_MARK_NO_BOOK_OF_THIS_MARKET_OBSERVED_BY_THE_EPOCH"
R_LAST_BOOK_TOO_OLD = "EPOCH_MARK_LAST_ERROR_FREE_BOOK_OLDER_THAN_THE_BOUND"
R_MARKET_ENDED = "EPOCH_MARK_MARKET_IN_A_TERMINAL_STATE_BEFORE_THE_EPOCH"
R_WINDOW_READS_FAILED = "EPOCH_MARK_EVERY_READ_IN_THE_WINDOW_FAILED"
R_NO_EXIT_SIDE = "EPOCH_BOOK_HAS_NO_EXIT_SIDE"
#: the verified bases of an epoch mark
B_BOOK = "BOOK_AT_THE_EPOCH"
B_SETTLEMENT = "SETTLEMENT_RECORDED_BY_THE_EPOCH"
#: bettor_paper_freshness.TERMINAL_MARKET_STATES, restated (pinned equal by
#: test) so the epoch read imports no freshness classifier
TERMINAL_MARKET_STATES = frozenset({
    "MARKET_STATE_EXPIRED", "MARKET_STATE_CLOSED", "MARKET_STATE_TERMINATED",
    "MARKET_STATE_MATCH_AND_CLOSE_AUCTION", "MARKET_STATE_SETTLED",
    "MARKET_STATE_RESOLVED", "EXPIRED", "CLOSED", "SETTLED", "RESOLVED"})

HISTORICAL = "HISTORICAL"
CARRIED = "CARRIED"
OPENED = "OPENED_AFTER_EPOCH"
UNVERIFIED = "CARRIED_UNVERIFIED"

#: the reconciliation's itemised differences, MANAGEMENT -> LEDGER
I_FUNDING = "LEDGER_FUNDING_MINUS_OPENING_EQUITY"
I_PRE_REALIZED = "PRE_EPOCH_REALIZED_RESULT_OF_POSITIONS_FLAT_AT_THE_EPOCH"
I_PRE_CARRIED = "PRE_EPOCH_MARK_TO_MARKET_RESULT_OF_CARRIED_POSITIONS"
I_UNVERIFIED = "UNVERIFIED_CARRIED_POSITION_WHOLE_LEDGER_RESULT"
I_HIST_CORRECTION = "POST_EPOCH_CORRECTION_OF_A_PRE_EPOCH_SETTLEMENT"
I_HIST_POST_CASH = "POST_EPOCH_CASH_ON_A_POSITION_FLAT_AT_THE_EPOCH"
I_UNATTRIBUTED = "LEDGER_CASH_NOT_ATTRIBUTED_TO_ANY_POSITION"

ZERO = Decimal(0)
EPS = Decimal("0.000001")


def D(v) -> Decimal:
    return L.D(v if v is not None else 0)


def f2(v) -> float | None:
    return None if v is None else round(float(v), 2)


def f6(v) -> float | None:
    return None if v is None else round(float(v), 6)


def _iso(at) -> str | None:
    return None if at is None else _dt.datetime.fromtimestamp(
        float(at), _dt.timezone.utc).isoformat()


# ═════════════════════════════════════════════════════════════════════
# PURE: the epoch mark from the records at the epoch instant
# ═════════════════════════════════════════════════════════════════════

def _evidence_times(evidence) -> list:
    """Every venue-outcome read instant a settlement's evidence names
    (paper_xavier.step_settle writes rows[*].outcome_at for an outcome
    settlement and evidence[*].settlement_read_at for a venue-price one).
    A read with no instant is None: it can never prove 'by the epoch'."""
    ev = evidence
    if isinstance(ev, str):
        try:
            ev = json.loads(ev)
        except ValueError:
            ev = None
    if not isinstance(ev, dict):
        return []
    out = []
    for r in ev.get("rows") or []:
        if isinstance(r, dict):
            out.append(r.get("outcome_at"))
    for r in ev.get("evidence") or []:
        if isinstance(r, dict):
            out.append(r.get("settlement_read_at"))
    return [None if t is None else float(t) for t in out]


def settlement_by_the_epoch(settlements: list, *, epoch_at: float) -> dict:
    """The position's FIRST settlement version, judged as evidence at the
    epoch instant: {recorded_by_epoch, payout_per_contract, ...}. Pure."""
    rows = sorted((s for s in settlements or [] if s),
                  key=lambda s: int(s.get("version") or 0))
    if not rows:
        return {"recorded_by_epoch": False, "why": "NOT_SETTLED"}
    s0 = rows[0]
    times = _evidence_times(s0.get("evidence"))
    known = [t for t in times if t is not None]
    out = {"settlement_id": s0.get("settlement_id"),
           "outcome": s0.get("outcome"),
           "payout_per_contract": f6(s0.get("payout_per_contract")),
           "settled_at": s0.get("settled_at"),
           "versions": len(rows),
           "outcome_reads": len(times),
           "first_outcome_read_at": min(known) if known else None,
           "last_outcome_read_at": max(known) if known else None}
    if not times:
        return dict(out, recorded_by_epoch=False,
                    why="THE_SETTLEMENT_EVIDENCE_NAMES_NO_OUTCOME_READ_INSTANT")
    if len(known) != len(times):
        return dict(out, recorded_by_epoch=False,
                    why="AN_OUTCOME_READ_OF_THE_SETTLEMENT_HAS_NO_INSTANT")
    if max(known) > float(epoch_at):
        return dict(out, recorded_by_epoch=False,
                    why="THE_OUTCOME_WAS_FIRST_FULLY_RECORDED_AFTER_THE_EPOCH")
    return dict(out, recorded_by_epoch=True, why=None)


def classify_epoch_mark(*, epoch_at: float, holding_side: str,
                        window_book: dict | None, last_book: dict | None,
                        window_reads: dict | None, first_book_after: dict | None,
                        settlements: list | None) -> dict:
    """ONE carried position's epoch mark: {price, observed_at, source, basis}
    from recorded evidence at the epoch instant, else {why, evidence} with
    the exact reason. Pure.

    `window_book`: the last error-free book within EPOCH_MARK_MAX_AGE_S at or
    before the epoch {obs_id, observed_at, bids, offers, market_state};
    `last_book`: the last error-free book at or before the epoch at any age;
    `window_reads`: {total, failed, failed_example} inside that window;
    `first_book_after`: the first error-free book after the epoch;
    `settlements`: the position's settlement versions."""
    from . import bettor_book_snapshot as BS
    E = float(epoch_at)
    no_exit = None
    if window_book is not None:
        md = {"bids": L._j(window_book.get("bids")) or [],
              "offers": L._j(window_book.get("offers")) or []}
        ex = BS.exit_ladder(md, held_intent=(
            "ORDER_INTENT_BUY_LONG" if holding_side == "LONG"
            else "ORDER_INTENT_BUY_SHORT"))
        if ex.get("ok") and ex.get("best_exit_price") is not None:
            at = float(window_book["observed_at"])
            return {"price": ex["best_exit_price"], "observed_at": at,
                    "age_at_epoch_s": round(E - at, 3),
                    "source": "paper_book_observations:%s"
                              % window_book.get("obs_id"),
                    "basis": B_BOOK}
        no_exit = ex.get("refusal") or "no bid"
    st = settlement_by_the_epoch(settlements or [], epoch_at=E)
    if st.get("recorded_by_epoch") and st.get("payout_per_contract") \
            is not None:
        return {"price": st["payout_per_contract"],
                "observed_at": st["last_outcome_read_at"],
                "age_at_epoch_s": round(E - st["last_outcome_read_at"], 3),
                "source": "paper_settlements:%s" % st.get("settlement_id"),
                "basis": B_SETTLEMENT, "settlement": st}
    wr = dict(window_reads or {})
    lb = dict(last_book or {}) if last_book else None
    state = str((lb or {}).get("market_state") or "").upper() or None
    if no_exit is not None:
        why = "%s: %s" % (R_NO_EXIT_SIDE, no_exit)
    elif lb is None and int(wr.get("failed") or 0) == 0:
        why = R_NEVER_OBSERVED
    elif int(wr.get("failed") or 0) > 0 and \
            int(wr.get("failed") or 0) == int(wr.get("total") or 0):
        why = R_WINDOW_READS_FAILED
    elif state in TERMINAL_MARKET_STATES:
        why = R_MARKET_ENDED
    elif lb is None:
        why = R_NEVER_OBSERVED
    else:
        why = R_LAST_BOOK_TOO_OLD
    lb_at = None if lb is None else float(lb["observed_at"])
    fa_at = (None if not first_book_after
             else float(first_book_after["observed_at"]))
    return {"why": why, "family": R_NO_EPOCH_BOOK, "evidence": {
        "bound_s": EPOCH_MARK_MAX_AGE_S,
        "last_error_free_book_at": _iso(lb_at),
        "last_error_free_book_age_at_epoch_s": (
            None if lb_at is None else round(E - lb_at, 3)),
        "last_error_free_book_obs_id": (lb or {}).get("obs_id"),
        "last_error_free_book_market_state": state,
        "reads_in_window": int(wr.get("total") or 0),
        "failed_reads_in_window": int(wr.get("failed") or 0),
        "failed_read_example": wr.get("failed_example"),
        "first_error_free_book_after_epoch_at": _iso(fa_at),
        "first_error_free_book_after_epoch_delay_s": (
            None if fa_at is None else round(fa_at - E, 3)),
        "settlement": st,
        "settlement_not_used_because": st.get("why")}}


# ═════════════════════════════════════════════════════════════════════
# PURE: the management book from ledger-timed events
# ═════════════════════════════════════════════════════════════════════

def management_book(*, epoch_at: float, opening_equity, fills: list,
                    settlement_entries: list, settled_qty: dict,
                    epoch_marks: dict, now_marks: dict, ledger_epoch: dict,
                    ledger_now: dict, meta: dict | None = None,
                    hwm_points: list | None = None) -> dict:
    """`fills`: [{position_key, direction, qty, gross_usd, fee_usd, at,
    seq?}] with `at` = the ledger commit time of the FILL / SALE entry (and
    `seq` its ledger sequence when read from the ledger).
    `settlement_entries`: [{position_key, kind, cash_usd, at, seq?}] -- every
    SETTLEMENT and CORRECTION ledger entry.
    `settled_qty`: {position_key: qty settled} (latest settlement version).
    `epoch_marks`: {position_key: {price, observed_at, source} | {why}}.
    `now_marks`: {position_key: the ledger's current mark dict}.
    `ledger_epoch` / `ledger_now`: {cash_usd, reserved_usd, funding_usd?} --
    ledger sums of entries committed before the epoch / all entries
    (`funding_usd`: the INITIAL_FUNDING sum; absent = migration 171's fixed
    amount).
    `hwm_points`: [(at, ledger_equity_usd[, last_sequence])] equity
    snapshots after the epoch with every position marked."""
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
            "post_cash": ZERO, "pre_cash": ZERO, "flows": [],
            "last_post_at": None})

    def flow(p, at, seq, cash):
        p["flows"].append((float(at), seq, D(cash)))
        if p["last_post_at"] is None or float(at) > p["last_post_at"]:
            p["last_post_at"] = float(at)

    for x in fills:
        p = P(x["position_key"])
        qty, gross, fee = D(x["qty"]), D(x["gross_usd"]), D(x["fee_usd"])
        post = float(x["at"]) >= E
        cash = -(gross + fee) if x["direction"] == "BUY" else gross - fee
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
            flow(p, x["at"], x.get("seq"), cash)
        else:
            p["fees_pre"] += fee
            p["pre_cash"] += cash
    for x in settlement_entries:
        if x["kind"] == "SETTLEMENT":
            P(x["position_key"])["settle_at"] = float(x["at"])
    hist_corrections = ZERO
    hist_items, hist_flows = [], []
    for x in settlement_entries:
        p = P(x["position_key"])
        if float(x["at"]) < E:
            p["pre_cash"] += D(x["cash_usd"])
            continue
        if p["settle_at"] is not None and p["settle_at"] < E:
            # a post-epoch correction of a pre-epoch settlement: history
            hist_corrections += D(x["cash_usd"])
            hist_flows.append((float(x["at"]), x.get("seq"),
                               D(x["cash_usd"])))
            hist_items.append({
                "item": I_HIST_CORRECTION, "position_key": x["position_key"],
                "market": (meta.get(x["position_key"]) or {}).get(
                    "us_market_slug"),
                "amount_usd": f6(D(x["cash_usd"])),
                "committed_at": _iso(x["at"]), "ledger_seq": x.get("seq"),
                "why": ("rule 1: a later correction of a settlement made "
                        "before the epoch stays history")})
            continue
        p["post_settle_cash"] += D(x["cash_usd"])
        p["post_cash"] += D(x["cash_usd"])
        flow(p, x["at"], x.get("seq"), x["cash_usd"])

    rows, unverified, historical_keys = [], [], []
    carried_value = ZERO
    excluded_post_cash = hist_corrections
    in_scope_cash = ZERO
    realized = unreal = ZERO
    marked_value = basis_open = unmarked_basis = ZERO
    fees_post_in = ZERO
    n_carried = n_opened = n_settled_post = n_unmarked = n_stale = 0
    # the reconciliation's per-kind sums and lines
    pre_flat = pre_carried = ZERO
    n_pre_flat = 0
    unverified_value = ZERO
    u_value: dict = {}
    held_flows = list(hist_flows)
    for pk, p in sorted(pos.items()):
        sq = D(settled_qty.get(pk))
        settled_pre = p["settle_at"] is not None and p["settle_at"] < E
        settled_post = p["settle_at"] is not None and p["settle_at"] >= E
        qty_e = p["bought_pre"] - p["sold_pre"] - (sq if settled_pre else ZERO)
        m = meta.get(pk) or {}
        if qty_e <= EPS and not (p["bought_post"] > 0):
            # closed (sold or settled) before the epoch: history. A post-epoch
            # correction of its settlement stays history too.
            historical_keys.append(pk)
            excluded_post_cash += p["post_cash"]
            pre_flat += p["pre_cash"]
            n_pre_flat += 1
            if p["post_cash"] != 0:
                held_flows.extend(p["flows"])
                hist_items.append({
                    "item": I_HIST_POST_CASH, "position_key": pk,
                    "market": m.get("us_market_slug"),
                    "amount_usd": f6(p["post_cash"]),
                    "why": ("ledger cash after the epoch on a position that "
                            "held nothing at the epoch and bought nothing "
                            "after it")})
            continue
        if qty_e > EPS:
            em = epoch_marks.get(pk) or {}
            if em.get("price") is None:
                excluded_post_cash += p["post_cash"]
                held_flows.extend(p["flows"])
                open_now = (qty_e + p["bought_post"] - p["sold_post"]
                            - (sq if settled_post else ZERO))
                nm = now_marks.get(pk) or {}
                cost_e = (p["buy_cost_pre"] / p["bought_pre"] * qty_e
                          if p["bought_pre"] > 0 else None)
                still_open = open_now > EPS
                if not still_open:
                    value_now, value_basis = ZERO, "CLOSED"
                elif nm.get("price") is not None:
                    value_now = D(D(nm["price"]) * open_now)
                    value_basis = "CURRENT_LEDGER_MARK"
                else:
                    # unmarked: at its average historical cost (stated)
                    bq = p["bought_pre"] + p["bought_post"]
                    value_now = (D((p["buy_cost_pre"] + p["buy_cost_post"])
                                   / bq * open_now) if bq > 0 else ZERO)
                    value_basis = "UNMARKED_AT_AVERAGE_LEDGER_COST"
                unverified_value += value_now
                u_value[pk] = value_now
                unverified.append({
                    "position_key": pk, "state": R_UNVERIFIED,
                    "why": em.get("why") or R_NO_EPOCH_BOOK,
                    "why_family": R_NO_EPOCH_BOOK,
                    "epoch_mark_evidence": em.get("evidence"),
                    "market": m.get("us_market_slug"),
                    "side": m.get("holding_side"),
                    "strategy": m.get("strategy"),
                    "qty_at_epoch": f6(qty_e), "open_qty_now": f6(open_now),
                    "historical_cost_usd": f2(cost_e),
                    "current_mark_price": f6(nm.get("price")),
                    "current_marked_value_usd": f2(
                        D(nm["price"]) * open_now
                        if nm.get("price") is not None and open_now > EPS
                        else None),
                    "post_epoch_cash_held_outside_usd": f2(p["post_cash"]),
                    "pre_epoch_ledger_cash_usd": f2(p["pre_cash"]),
                    "value_now_usd": f2(value_now),
                    "value_now_basis": value_basis,
                    "whole_ledger_result_usd": f2(
                        p["pre_cash"] + p["post_cash"] + value_now),
                    "closed": not still_open,
                    "closed_at": None if still_open else p["last_post_at"]})
                continue
            kind = CARRIED
            mark_e = D(em["price"])
            carry = D(qty_e * mark_e)
            carried_value += carry
            pre_carried += p["pre_cash"] + carry
            n_carried += 1
        else:
            kind = OPENED
            mark_e = None
            carry = ZERO
            qty_e = ZERO
            # a pre-epoch round trip before this post-epoch entry is history
            pre_flat += p["pre_cash"]
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
            "epoch_mark_basis": ((epoch_marks.get(pk) or {}).get("basis")
                                 or B_BOOK) if kind == CARRIED else None,
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

    # ── THE EXACT RECONCILIATION (rule 7) ──────────────────────────────
    funding_given = ledger_epoch.get("funding_usd")
    funding = D(LEDGER_FUNDING_USD if funding_given is None
                else funding_given)
    pre_unverified = sum((pos[u["position_key"]]["pre_cash"]
                          for u in unverified), ZERO)
    pre_attributed = pre_flat + pre_carried - carried_value + pre_unverified
    pre_unattributed = (cash_e_ledger - funding) - pre_attributed
    post_unattributed = ((cash_now_ledger - cash_e_ledger) - in_scope_cash
                         - excluded_post_cash)
    items = [
        {"item": I_FUNDING, "amount_usd": f6(funding - opening),
         "why": ("the ledger's INITIAL_FUNDING (%s, %s) minus the management "
                 "opening equity" % (
                     f2(funding), "read from the ledger"
                     if funding_given is not None
                     else "migration 171's fixed amount"))},
        {"item": I_PRE_REALIZED, "amount_usd": f6(pre_flat),
         "positions": n_pre_flat + sum(
             1 for x in rows if x["kind"] == OPENED
             and pos[x["position_key"]]["pre_cash"] != 0),
         "why": ("the realized ledger result, fees included, of every "
                 "position holding nothing at the epoch: PRE-MANAGEMENT "
                 "HISTORY, kept in the ledger and stated here")},
        {"item": I_PRE_CARRIED, "amount_usd": f6(pre_carried),
         "positions": n_carried,
         "why": ("each carried position's pre-epoch ledger cash plus its "
                 "verified epoch mark value: its result up to the epoch")}]
    for u in unverified:
        pu = pos[u["position_key"]]
        items.append({
            "item": I_UNVERIFIED, "position_key": u["position_key"],
            "market": u["market"],
            "amount_usd": f6(pu["pre_cash"] + pu["post_cash"]
                             + u_value[u["position_key"]]),
            "pre_epoch_ledger_cash_usd": u["pre_epoch_ledger_cash_usd"],
            "post_epoch_ledger_cash_usd":
                u["post_epoch_cash_held_outside_usd"],
            "value_now_usd": u["value_now_usd"],
            "value_now_basis": u["value_now_basis"],
            "why": "%s (%s): no epoch basis splits its result" % (
                R_UNVERIFIED, u["why"])})
    items.extend(hist_items)
    items.append({
        "item": I_UNATTRIBUTED,
        "amount_usd": f6(pre_unattributed + post_unattributed),
        "before_epoch_usd": f6(pre_unattributed),
        "after_epoch_usd": f6(post_unattributed),
        "why": ("ledger cash no fill, sale, settlement or correction of a "
                "position explains; must be 0.00")})
    item_sum = sum((D(i["amount_usd"] or 0) for i in items), ZERO)
    in_scope_value = marked_value + unmarked_basis
    ledger_equity = cash_now_ledger + in_scope_value + unverified_value
    attributed = (abs(pre_unattributed) <= Decimal("0.01")
                  and abs(post_unattributed) <= Decimal("0.01"))
    reconciliation = {
        "rule": ("OPENING_EQUITY + itemised post-epoch changes = MANAGEMENT "
                 "EQUITY; MANAGEMENT EQUITY + itemised differences = LEDGER "
                 "EQUITY (ledger cash + every open position at this view's "
                 "value)"),
        "opening_equity_usd": f2(opening),
        "changes": {
            "realized_usd": f6(realized), "unrealized_usd": f6(unreal),
            "fees_after_epoch_usd": f6(fees_post_in),
            # itemised per position in the book's own `rows` (realized_pnl_
            # usd / unrealized_pnl_usd / fees_after_epoch_usd each), never
            # copied: the live payload carries every row once
            "positions": len(rows), "itemised_in": "rows"},
        "management_equity_usd": f6(equity),
        "management_gap_usd": f6(identity_gap),
        "differences_to_ledger": items,
        "differences_total_usd": f6(item_sum),
        "ledger_equity_usd": f6(ledger_equity),
        "ledger_equity_is": (
            "ledger cash now (%s, reserved included) + in-scope open value "
            "(%s marked + %s unmarked at management basis) + held-outside "
            "open value (%s)" % (f2(cash_now_ledger), f2(marked_value),
                                 f2(unmarked_basis), f2(unverified_value))),
        "ledger_gap_usd": f6(equity + item_sum - ledger_equity),
        "exact": (abs(equity + item_sum - ledger_equity) <= Decimal("0.01")
                  and abs(identity_gap) <= Decimal("0.01")),
        "fully_attributed": attributed,
        "pre_management_result_usd": f6(
            (funding - opening) + pre_flat + pre_carried + pre_unverified
            + pre_unattributed),
        "pre_management_result_is": (
            "ledger equity at the epoch (verified carried marks; held-outside "
            "positions at zero) minus the opening equity: what the owner's "
            "reset re-based away, stated, never hidden"),
        "forces_equity_to_opening": False}

    # ── HIGH-WATER MARK ────────────────────────────────────────────────
    # A snapshot's equity is the LEDGER's (cash + every open mark). Once no
    # held-outside position is open, management equity at that snapshot is
    # exactly its equity + the re-base offset - the held-outside cash its
    # ledger already contained (by ledger sequence when both carry one).
    open_unverified = [u for u in unverified if not u["closed"]]
    offset = opening_cash - cash_e_ledger
    exact_from = None
    if not open_unverified:
        exact_from = max([E] + [float(u["closed_at"]) for u in unverified
                                if u["closed_at"] is not None])

    def held_by(at, seq) -> Decimal:
        tot = ZERO
        for f_at, f_seq, amt in held_flows:
            if seq is not None and f_seq is not None:
                tot += amt if int(f_seq) <= int(seq) else ZERO
            elif f_at <= float(at):
                tot += amt
        return tot

    hwm, hwm_at, hwm_basis = opening, epoch_at, "OPENING_EQUITY"
    used = skipped = 0
    if exact_from is not None:
        for pt in hwm_points or []:
            at, eq = pt[0], pt[1]
            seq = pt[2] if len(pt) > 2 else None
            if float(at) < exact_from:
                skipped += 1
                continue
            v = D(eq) + offset - held_by(at, seq)
            used += 1
            if v > hwm:
                hwm, hwm_at, hwm_basis = v, at, "EQUITY_SNAPSHOT"
    if equity > hwm:
        hwm, hwm_at, hwm_basis = equity, None, "CURRENT_EQUITY"
    dd = hwm - equity
    exact = not unverified and excluded_post_cash == 0
    held_total = format(float(excluded_post_cash), ",.2f")
    if exact:
        hwm_rule = ("max of the opening equity, every post-epoch equity "
                    "snapshot re-based by the epoch offset, and the current "
                    "equity")
    elif exact_from is None:
        hwm_rule = (
            "max of the opening equity and the current equity only: the "
            "snapshots cannot be re-based exactly while %d carried "
            "position(s) are %s and still open (their value at each snapshot "
            "is inside its equity with no epoch basis); $%s of post-epoch "
            "ledger cash is held outside" % (
                len(open_unverified), R_UNVERIFIED, held_total))
    elif not unverified:
        hwm_rule = ("max of the opening equity, every post-epoch equity "
                    "snapshot re-based by the epoch offset less the "
                    "held-outside ledger cash it already contained ($%s in "
                    "all), and the current equity" % held_total)
    else:
        hwm_rule = (
            "max of the opening equity, every equity snapshot from %s -- when "
            "the last of %d %s carried position(s) closed -- re-based by the "
            "epoch offset less the held-outside ledger cash it already "
            "contained ($%s in all), and the current equity; %d snapshot(s) "
            "before it hold a position with no epoch basis and are not used"
            % (_iso(exact_from), len(unverified), R_UNVERIFIED, held_total,
               skipped))
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
        "carried_unverified_open": len(open_unverified),
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
        "high_water_mark_points_before_exact_from": skipped,
        "high_water_mark_exact_from": exact_from,
        "high_water_mark_rule": hwm_rule,
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
        "reconciliation": reconciliation,
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
       f.gross_usd, f.fee_usd, coalesce(l.committed_at, f.recorded_at) AS at,
       l.seq
  FROM paper_fills f
  LEFT JOIN paper_ledger l ON l.fill_id = f.fill_id
                          AND l.kind IN ('FILL', 'SALE')
 WHERE f.account_id = $1
"""

SETTLE_SQL = """
SELECT position_key, kind, cash_delta_usd, committed_at, seq
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
       coalesce(sum(reserved_delta_usd), 0) AS reserved_now,
       coalesce(sum(cash_delta_usd) FILTER (WHERE kind = 'INITIAL_FUNDING'),
                0) AS funding
  FROM paper_ledger WHERE account_id = $1
"""

EPOCH_BOOKS_SQL = """
SELECT DISTINCT ON (us_market_slug) us_market_slug, obs_id, observed_at,
       bids, offers, market_state
  FROM paper_book_observations
 WHERE us_market_slug = ANY($1::text[]) AND error IS NULL
   AND observed_at <= $2 AND observed_at >= $3
 ORDER BY us_market_slug, observed_at DESC
"""

#: the last error-free book at or before the epoch at ANY age (the reason
#: the window held none)
LAST_BOOK_SQL = """
SELECT DISTINCT ON (us_market_slug) us_market_slug, obs_id, observed_at,
       market_state
  FROM paper_book_observations
 WHERE us_market_slug = ANY($1::text[]) AND error IS NULL
   AND observed_at <= $2
 ORDER BY us_market_slug, observed_at DESC
"""

WINDOW_READS_SQL = """
SELECT us_market_slug, count(*) AS total,
       count(*) FILTER (WHERE error IS NOT NULL) AS failed,
       min(error) AS failed_example
  FROM paper_book_observations
 WHERE us_market_slug = ANY($1::text[])
   AND observed_at <= $2 AND observed_at >= $3
 GROUP BY us_market_slug
"""

FIRST_AFTER_SQL = """
SELECT DISTINCT ON (us_market_slug) us_market_slug, obs_id, observed_at
  FROM paper_book_observations
 WHERE us_market_slug = ANY($1::text[]) AND error IS NULL
   AND observed_at > $2
 ORDER BY us_market_slug, observed_at ASC
"""

SETTLEMENTS_SQL = """
SELECT position_key, settlement_id, version, outcome, payout_per_contract,
       evidence, settled_at
  FROM paper_settlements
 WHERE account_id = $1 AND position_key = ANY($2::text[])
 ORDER BY position_key, version
"""

META_SQL = """
SELECT group_id, us_market_slug, holding_side, max(strategy) AS strategy
  FROM paper_fills WHERE account_id = $1
 GROUP BY group_id, us_market_slug, holding_side
"""


def _at(v) -> float:
    return float(v.timestamp()) if hasattr(v, "timestamp") else float(v)


async def epoch_marks(conn, slugs_sides: dict, *, epoch_at: float,
                      account_id: str | None = None) -> dict:
    """{position_key: {price, observed_at, source, basis} | {why, evidence}}
    -- classify_epoch_mark over the records at the epoch instant: the
    window book, then a settlement whose every outcome read was recorded by
    the epoch; absent both, the exact reason with its evidence, never a
    price."""
    slugs = sorted({s for s, _side in slugs_sides.values()})
    if not slugs:
        return {}
    e = _dt.datetime.fromtimestamp(epoch_at, _dt.timezone.utc)
    lo = _dt.datetime.fromtimestamp(epoch_at - EPOCH_MARK_MAX_AGE_S,
                                    _dt.timezone.utc)
    window = {r["us_market_slug"]: dict(r)
              for r in await conn.fetch(EPOCH_BOOKS_SQL, slugs, e, lo)}
    last = {r["us_market_slug"]: dict(r)
            for r in await conn.fetch(LAST_BOOK_SQL, slugs, e)}
    reads = {r["us_market_slug"]: dict(r)
             for r in await conn.fetch(WINDOW_READS_SQL, slugs, e, lo)}
    after = {r["us_market_slug"]: dict(r)
             for r in await conn.fetch(FIRST_AFTER_SQL, slugs, e)}
    sets: dict = {}
    if account_id is not None:
        for r in await conn.fetch(SETTLEMENTS_SQL, account_id,
                                  sorted(slugs_sides)):
            d = dict(r)
            d["settled_at"] = _at(d["settled_at"])
            sets.setdefault(r["position_key"], []).append(d)
    for d in (window, last, after):
        for r in d.values():
            r["observed_at"] = _at(r["observed_at"])
    out = {}
    for pk, (slug, side) in slugs_sides.items():
        out[pk] = classify_epoch_mark(
            epoch_at=epoch_at, holding_side=side,
            window_book=window.get(slug), last_book=last.get(slug),
            window_reads=reads.get(slug), first_book_after=after.get(slug),
            settlements=sets.get(pk))
    return out


async def read(conn, account_id: str, *, bal: dict, now: float,
               epoch_at: float = EPOCH_START,
               opening_equity=OPENING_EQUITY_USD) -> dict:
    """The management book for `account_id` from the ledger tables, with
    current marks taken from `bal` (bettor_paper_ledger.balances)."""
    if await conn.fetchval("SELECT to_regclass('paper_account_epochs') IS NOT NULL") and await conn.fetchval('SELECT EXISTS(SELECT 1 FROM paper_account_epochs WHERE account_id=$1)', account_id):
        return await read_account_epoch(conn, account_id, bal=bal)
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
              "fee_usd": r["fee_usd"], "at": _at(r["at"]), "seq": r["seq"]}
             for r in await conn.fetch(FILLS_SQL, acct)]
    sets = [{"position_key": r["position_key"], "kind": r["kind"],
             "cash_usd": r["cash_delta_usd"], "at": _at(r["committed_at"]),
             "seq": r["seq"]}
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
    em = await epoch_marks(conn, need, epoch_at=epoch_at, account_id=acct)
    now_marks = {p["position_key"]: p.get("mark") or {}
                 for p in bal.get("open_positions") or []}
    points = []
    if await conn.fetchval(
            "SELECT to_regclass('paper_equity_snapshots') IS NOT NULL"):
        points = [(_at(r["at"]), r["equity_usd"], r["last_sequence"])
                  for r in await conn.fetch(
            "SELECT at, equity_usd, last_sequence FROM paper_equity_snapshots "
            " WHERE account_id = $1 AND at >= $2 AND equity_usd IS NOT NULL "
            "   AND unmarked_positions = 0 ORDER BY at", acct, e)]
    book = management_book(
        epoch_at=epoch_at, opening_equity=opening_equity, fills=fills,
        settlement_entries=sets, settled_qty=sq, epoch_marks=em,
        now_marks=now_marks,
        ledger_epoch={"cash_usd": s["cash_e"], "reserved_usd": s["reserved_e"],
                      "funding_usd": s["funding"]},
        ledger_now={"cash_usd": s["cash_now"],
                    "reserved_usd": s["reserved_now"]},
        meta=meta, hwm_points=points)
    rc = book["reconciliation"]
    ok = (book["identity"]["holds"] and book["opening"]["identity_holds"]
          and book["ledger_reconciliation"]["reconciles"]
          and rc["exact"] and rc["fully_attributed"])
    book["status"] = "OK" if ok else "DOES_NOT_RECONCILE"
    book["why"] = None if ok else (
        "equity identity gap %s, opening gap %s, ledger gap %s, bridge gap "
        "%s, unattributed ledger cash %s" % (
            book["identity"]["gap_usd"], book["opening"]["identity_gap_usd"],
            book["ledger_reconciliation"]["gap_usd"], rc["ledger_gap_usd"],
            next((i["amount_usd"] for i in rc["differences_to_ledger"]
                  if i["item"] == I_UNATTRIBUTED), None)))
    book["pre_management_history"] = {
        "what": ("the paper ledger since its funding: every trade, fill, fee, "
                 "settlement and correction, unchanged and queryable; not "
                 "mixed into the management figures"),
        "ledger_realized_pnl_usd": f2(bal.get("realized_pnl_usd")),
        "ledger_fees_paid_usd": f2(bal.get("fees_paid_usd")),
        "ledger_cash_usd": f2(bal.get("cash_usd")),
        "ledger_available_usd": f2(bal.get("available_usd")),
        "positions_closed_before_epoch": book["historical_positions"],
        "pre_management_result_usd": rc["pre_management_result_usd"],
        "pre_management_result_is": rc["pre_management_result_is"]}
    book["computed_at"] = now
    return book


async def read_account_epoch(conn, account=None, *, bal=None):
    account = account or await L.selected_account(conn)
    if not await conn.fetchval("SELECT to_regclass('paper_account_epochs') IS NOT NULL"):
        return {'account_id': account, 'day_one': False, 'status': 'UNAVAILABLE', 'why': 'MIGRATION_317_NOT_APPLIED'}
    epoch = await conn.fetchrow('SELECT * FROM paper_account_epochs WHERE account_id=$1', account)
    if not epoch:
        return {'account_id': account, 'day_one': False}
    bal = bal or await L.balances(conn, account)
    snapshots = await conn.fetch('SELECT equity_usd FROM paper_equity_snapshots WHERE account_id=$1 ORDER BY at', account)
    peak, current = float(OPENING_EQUITY_USD), None
    for point in snapshots:
        if point['equity_usd'] is not None:
            current = float(point['equity_usd'])
            peak = max(peak, current)
    dd = {'current_drawdown_usd': None if current is None else peak-current,
          'peak_equity_usd': peak}
    cs = await L.cash_state(conn, account)
    equity = bal.get('total_equity_usd')
    total = None if equity is None else float(Decimal(str(equity)) - OPENING_EQUITY_USD)
    gap = None if equity is None or bal.get('unrealized_pnl_usd') is None else float(Decimal(str(equity)) - OPENING_EQUITY_USD - Decimal(str(bal['realized_pnl_usd'])) - Decimal(str(bal['unrealized_pnl_usd'])))
    turnover = await conn.fetchval('SELECT coalesce(sum(qty*price),0) FROM paper_fills WHERE account_id=$1', account)
    current_dd = dd.get('current_drawdown_usd')
    peak = dd.get('peak_equity_usd')
    opening = L._j(epoch['opening_receipt'])
    return {'account_id': account, 'day_one': True, 'label': 'BETTOR PAPER — DAY ONE',
            'read_model': 'ACCOUNT_BACKED_EPOCH', 'drawdown_basis': 'RECORDED_NEW_ACCOUNT_EQUITY_SNAPSHOTS',
            'epoch_id': epoch['epoch_id'], 'opened_at': L._epoch(epoch['opened_at']),
            'opening_equity_usd': 500000.0, 'opening_verified': True,
            'status': 'OK' if cs['running_balance_agrees'] and gap is not None and abs(gap) < .005 else 'DOES_NOT_RECONCILE',
            'epoch_start': epoch['opened_at'].isoformat(), 'epoch_start_at': L._epoch(epoch['opened_at']),
            'equity_usd': equity, 'cash_usd': bal.get('available_usd'),
            'cash_including_reserved_usd': bal.get('cash_usd'), 'reserved_usd': bal.get('reserved_usd'),
            'marked_open_position_value_usd': bal.get('open_position_value_usd'),
            'unmarked_carried_at_basis_usd': 0, 'carried_positions': 0, 'carried_unverified': 0,
            'realized_pnl_usd': bal.get('realized_pnl_usd'), 'unrealized_pnl_usd': bal.get('unrealized_pnl_usd'),
            'total_pnl_usd': total, 'return_pct': None if total is None else total/5000,
            'trading_turnover_usd': float(turnover), 'drawdown_usd': current_dd,
            'drawdown_pct': None if current_dd is None or not peak else current_dd/peak*100,
            'opening': {'available_cash_usd': opening['balances']['available_usd'],
                        'reserved_usd': opening['balances']['reserved_usd'],
                        'cash_including_reserved_usd': opening['balances']['cash_usd'],
                        'carried_position_mark_value_usd': 0},
            'exposure': {'basis_usd': sum(float(p['cost_basis_usd']) for p in bal.get('open_positions', [])),
                         'unmarked': len(bal.get('unmarked_positions', []))},
            'identity': {'gap_usd': gap, 'holds': gap is not None and abs(gap) < .005},
            'ledger_reconciliation': {'reconciles': cs['running_balance_agrees'], 'gap_usd': 0 if cs['running_balance_agrees'] else None,
                                      'post_epoch_cash_held_outside_usd': 0},
            'pre_management_history': {'account_id': epoch['previous_account_id'], 'archived': True,
                                       'balances_at_cutover': L._j(epoch['historical_receipt'])['balances']},
            'opening_receipt': L._j(epoch['opening_receipt']), 'balances': bal,
            'historical_account_id': epoch['previous_account_id'],
            'historical_receipt': L._j(epoch['historical_receipt']),
            'real_money_submission': 'DISABLED'}
