"""XAVIER ON THE PAPER BOOK: HANDOFF FROM THE FIRST FILL, REVIEWS, STANDING
PROTECTION, AND SETTLEMENT FROM AUTHORITATIVE EVIDENCE.

HANDOFF. A paper group passes to Xavier from its FIRST partial simulated fill
(`paper_handoffs`, one owner per group, unique): confirmed quantity is the
filled quantity, the rest of the entry is outstanding. Replays cannot create
a second owner.

REVIEWS. Each held group is reviewed immediately on its first fill, then on a
new fill (FILL_EVENT), on a market event (a newly observed book whose best
exit moved) and on the scheduled backstop (`cadence.xavier_backstop_s`). The
due list is reviewed most urgent first (first fill, fill, market, backstop;
then longest waiting) within the pass budget OR Xavier's reserved budget
(`cadence.xavier_reserved_budget_s`, RESERVED_BUDGET_S), whichever is later:
the decision steps that run first can no longer leave the step with no time,
and no group is starved by its id. Every review also writes its management
assessment (agents.xavier_management: latency against the bound, thesis
state, shadow REALLOCATE, the management-policy record). A
review compares HOLD, EXIT, REDUCE, NETTING and the INDIRECT HEDGE on the
SAME settlement measure through the existing comparison
(`agents.xavier_policy.run` -> `bettor_funded_decision.decide`, unchanged):
both legs of the group, the fees of every action, unmatched inventory,
exceptional states (an unreadable or empty book, a stale measure) and
incomplete searches are all on the record.

    measure     P(the held side pays) = (research model at the current best
                price + the latest Pinnacle reading) / 2, when a Pinnacle
                reading for the same contract is on a valuation within the
                lookback; otherwise the entry decision's blended probability,
                labelled ENTRY_TIME_MEASURE (stale, stated). Void is not
                applied (conditional on the fixture being played).
    evidence    every review records `measure.evidence_state`
                (FRESH_CURRENT_PROBABILITY / STALE_ENTRY_TIME_PROBABILITY /
                PROBABILITY_UNAVAILABLE) with the probability's source, its
                source and receipt stamps, age and the freshness limit; on
                anything but fresh it states `probability_limitation`, ranks
                no discretionary sale and shows the hold value as entry-time,
                never as the current expected value.
    HOLD        value = q x p (the payout expected from here)
    EXIT        value = walked proceeds of a sale of q into the observed bids
                (after fees) + the unsold remainder held at q' x p
    REDUCE      the same for half the position
    NETTING     on this venue one signed net position means buying the
                complement IS the sale: shown, not rankable, never counted
                twice
    INDIRECT    a hedge on a sibling contract of the fixture: the paper book
                does not run the hedge search, so it is NOT_RANKABLE with
                the search recorded as INCOMPLETE -- never assumed absent

STANDING PROTECTION. When the review holds, Xavier keeps ONE resting
protective sale per group at the protective price: the lowest cent at which
selling the whole open quantity recovers its cost basis, the sale fees and
the policy buffer (a positive floor on the matched quantity). The one
live-or-potentially-live order per group rule is the funded rule
(`bettor_xavier_standing_orders.second_live_hedge_permitted`) and a partial
unique index in migration 171. A position that changes (partial fills of the
entry, a partial fill of the protection) is reconciled: cancel -> the
simulator confirms the terminal state -> the replacement is placed on a later
pass. A floor is NEVER realized P&L until a sale or a settlement books it.

SETTLEMENT. An open position settles only on authoritative evidence: the
venue's own settlement joined onto `external_valuations` (outcome_basis
VENUE_SETTLEMENT_PRICE / VENUE_REPORTED_OUTCOME for a 0/1, CONFIRMED_VOID for
a refund), unanimous across the rows for the contract. A later disagreement
with what was settled books a CORRECTION; conflicting evidence settles
nothing and is recorded as a finding.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
from decimal import ROUND_DOWN, Decimal
from typing import Any

from .. import bettor_paper_ledger as L
from .. import bettor_paper_simulator as SIM
from .. import canonical_intent as CI
from .. import decision_hooks as DH
from .. import xavier_freshness as XF
from . import derek_policy as DP
from . import paper_exit_intents as XI

VERSION = "PAPER_XAVIER_V1"
T_FIRST = "FIRST_FILL"
T_FILL = "FILL_EVENT"
T_MARKET = "MARKET_EVENT"
T_BACKSTOP = "SCHEDULED_BACKSTOP"
#: THE REQUEUE TRIGGERS ADDED FOR THE FRESHNESS TRUTH (owner P0; migration
#: 222 admits them on paper_xavier_reviews): a management order of the group
#: reached a terminal state (protection changed / cancelled), a newer stored
#: valuation of the contract, the event started, the last review's fresh
#: probability expired (source stamp + the existing limit).
T_ORDER = "ORDER_EVENT"
T_VALUATION = "VALUATION_CHANGE"
T_GAME = "GAME_STATE_CHANGE"
T_EXPIRY = "FRESHNESS_EXPIRY"
#: an open EXIT intent's own deadline (cancel confirmation, or the bounded
#: revalidation window after a terminal cancel) passed: re-review at once
#: so the intent is continued or explicitly abandoned (migration 313)
T_EXIT_INTENT = "EXIT_INTENT_DEADLINE"

A_HOLD, A_EXIT, A_REDUCE = "HOLD", "EXIT", "REDUCE"
#: WHERE A PERSISTED valuation_id LIVES (xavier_packet: a FRESH probability
#: is management evidence only with a persisted valuation id).
VALUATION_STORE_EXTERNAL = "external_valuations"
VALUATION_STORE_SNAPSHOT = "xavier_probability_snapshots"
#: the standing protection's quantity grain: the ledger's numeric(18,6)
PROTECTION_QTY_GRAIN = Decimal("0.000001")
A_NETTING, A_INDIRECT = "NETTING", "ACQUIRE_INDIRECT_HEDGE"
B_NETTING_IS_EXIT = "IDENTICAL_TO_EXIT_ON_A_ONE_NET_POSITION_VENUE"
B_INDIRECT_NOT_SEARCHED = "INDIRECT_HEDGE_SEARCH_NOT_RUN_ON_THE_PAPER_BOOK"
B_NO_BIDS = "NO_EXECUTABLE_EXIT_DEPTH_IN_THE_OBSERVED_BOOK"
R_NO_MEASURE = "NO_SETTLEMENT_MEASURE_FOR_THIS_POSITION"
#: A DISCRETIONARY SALE (EXIT / REDUCE) IS NEVER RANKED ON A STALE OR ABSENT
#: MEASURE: its value is priced against `p`, so a stale probability would be
#: an economic action taken on evidence older than the 30 s rule. The
#: position is held and its cost-recovery protection (priced from quantity,
#: basis and fees only, never `p`) is maintained instead.
B_STALE_MEASURE = "MEASURE_STALE_OR_ABSENT_NO_DISCRETIONARY_SALE"
#: A DISCRETIONARY SALE IS NEVER RANKED ON AN INCOMPLETE MANAGEMENT PACKET
#: (xavier_packet): a fresh probability alone is not enough -- the qty must
#: reconcile, the book must be current with exit depth, the settlement
#: identity and the protection state known.
B_PACKET_INCOMPLETE = "MANAGEMENT_PACKET_INCOMPLETE_NO_DISCRETIONARY_SALE"
#: NO RANKABLE MANAGEMENT ACTION WITHOUT A FRESH EXECUTABLE EXIT WALK: the
#: book the alternatives are walked on must be readable, inside the 300 s
#: executable-mark SLA and show exit depth.
B_NO_FRESH_EXIT_WALK = "NO_FRESH_EXECUTABLE_EXIT_WALK"
#: 18 · THE MANAGEMENT DEADBAND (bettor_paper_profitability_bind.
#: management_economics): a discretionary EXIT / REDUCE whose value does not
#: beat HOLD by the minimum expected improvement per contract sold is not
#: rankable -- HOLD stands (the cost-recovery protection is unaffected)
B_BELOW_MIN_IMPROVEMENT = "MANAGEMENT_ACTION_BELOW_MINIMUM_EXPECTED_IMPROVEMENT"
#: (= xavier_packet.P_PROTECTION; the packet module is imported lazily)
XPK_P_PROTECTION = "NO_VALID_ACTIVE_PROTECTION"
#: a book stamped this far AFTER the review instant is still clock skew
WALK_MAX_FUTURE_SKEW_S = 5.0
#: THE EVIDENCE STATE OF THE PROBABILITY EVERY REVIEW STANDS ON, exactly
#: one, on `measure.evidence_state`. FRESH only when the measure is current
#: AND its own source stamp is within the Pinnacle freshness limit
#: (`pinnacle_max_age_s` = ext_pinnacle_loop.PINNACLE_MAX_AGE_S) at the
#: review instant; any older probability -- a stale reading or the entry
#: decision's -- is STALE_ENTRY_TIME (the source says which); none at all is
#: UNAVAILABLE. Unavailable is null, never 0, and is never a reason to sell.
E_FRESH = "FRESH_CURRENT_PROBABILITY"
E_STALE = "STALE_ENTRY_TIME_PROBABILITY"
E_NONE = "PROBABILITY_UNAVAILABLE"
PROTECTION_BUFFER_USD_PER_CONTRACT = 0.01
LABEL_BASES = ("VENUE_SETTLEMENT_PRICE", "VENUE_REPORTED_OUTCOME")
VOID_BASIS = "CONFIRMED_VOID"


def _h(*parts) -> str:
    return hashlib.sha256(":".join(str(p) for p in parts).encode()
                          ).hexdigest()[:24]


async def _strategy(conn, group_id: str) -> str:
    """The group's strategy (its entry order's, migration 182), carried onto
    every management order: a position never switches policy."""
    from . import paper_benchmark as PB
    return await PB.group_strategy(conn, group_id)


def _clock(ctx) -> float:
    c = ctx.get("clock")
    return float(c()) if c else float(ctx["now"])


# ═════════════════════════════════════════════════════════════════════
# HANDOFF
# ═════════════════════════════════════════════════════════════════════

async def step_handoff(conn, ctx: dict) -> dict:
    """ONE OWNER PER GROUP FROM ITS FIRST SIMULATED FILL; later fills update
    the same row."""
    acct = ctx["account_id"]
    rows = await conn.fetch(
        "SELECT o.order_id, o.group_id, o.decision_id, o.qty, o.filled_qty, "
        "       o.state, o.strategy, (SELECT f.fill_id FROM paper_fills f "
        "                  WHERE f.order_id = o.order_id "
        "                  ORDER BY f.filled_at, f.fill_id LIMIT 1) AS ff, "
        "       (SELECT min(f.filled_at) FROM paper_fills f "
        "                  WHERE f.order_id = o.order_id) AS ffa "
        "  FROM paper_orders o WHERE o.account_id = $1 AND o.role = 'ENTRY' "
        "   AND o.filled_qty > 0", acct)
    created, updated = [], 0
    for r in rows:
        outstanding = (Decimal(0) if r["state"] in L.TERMINAL_STATES
                       else r["qty"] - r["filled_qty"])
        hid = "paperhand:" + _h(r["group_id"])
        got = await conn.fetchval(
            "INSERT INTO paper_handoffs (handoff_id, session_id, account_id, "
            " group_id, decision_id, entry_order_id, first_fill_id, "
            " first_fill_at, confirmed_qty, outstanding_qty, strategy) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11) "
            "ON CONFLICT (group_id) DO UPDATE SET "
            " confirmed_qty = EXCLUDED.confirmed_qty, "
            " outstanding_qty = EXCLUDED.outstanding_qty, updated_at = now() "
            " WHERE paper_handoffs.confirmed_qty <> EXCLUDED.confirmed_qty "
            "    OR paper_handoffs.outstanding_qty <> EXCLUDED.outstanding_qty"
            " RETURNING (xmax = 0) AS inserted",
            hid, ctx["session_id"], acct, r["group_id"], r["decision_id"],
            r["order_id"], r["ff"], r["ffa"], r["filled_qty"], outstanding,
            # THE ENTRY'S STRATEGY (migration 182): the group keeps it.
            r["strategy"])
        if got is True:
            created.append(r["group_id"])
        elif got is False:
            updated += 1
    ctx["new_handoffs"] = created
    # THE ENTRY THESIS (migration 206), written AT ENTRY -- the handoff --
    # never later: a handoff of this window whose thesis write did not land
    # is retried; an older one stays NO_ENTRY_THESIS (no hindsight).
    theses = await _entry_theses(conn, ctx, created)
    return {"handoffs_created": len(created), "handoffs_updated": updated,
            "theses_written": theses}


async def _entry_theses(conn, ctx: dict, created: list) -> int:
    from . import xavier_management as XM
    if not await XM.has_schema(conn):
        return 0
    at = _clock(ctx)
    try:
        todo = set(created) | {r["group_id"] for r in await conn.fetch(
            "SELECT h.group_id FROM paper_handoffs h WHERE h.account_id=$1 "
            "   AND h.first_fill_at > to_timestamp($2) AND NOT EXISTS ("
            "   SELECT 1 FROM xavier_entry_theses t "
            "    WHERE t.position_kind='PAPER' AND t.group_id=h.group_id)",
            ctx["account_id"], at - XM.THESIS_ENTRY_WINDOW_S)}
    except Exception:                                           # noqa: BLE001
        todo = set(created)
    n = 0
    for g in sorted(todo):
        got = await XM.record_paper_thesis(conn, ctx, g)
        n += 1 if got.get("created") else 0
    return n


# ═════════════════════════════════════════════════════════════════════
# THE MEASURE AND THE ALTERNATIVES (pure given inputs)
# ═════════════════════════════════════════════════════════════════════

def _exit_walk(levels: list, qty: float, fee_fn, at) -> dict:
    left, proceeds, fees, takes = float(qty), 0.0, 0.0, []
    for lv in levels:
        if left <= 1e-9:
            break
        t = min(float(lv["qty"]), left)
        g = t * float(lv["price"])
        fe = float(L._fee(fee_fn, t, lv["price"], at))
        proceeds += g
        fees += fe
        takes.append({"price": lv["price"], "wire": lv["wire"], "qty": t})
        left -= t
    sold = float(qty) - left
    return {"sold": round(sold, 6), "proceeds_usd": round(proceeds, 6),
            "fees_usd": round(fees, 6), "unsold": round(left, 6),
            "takes": takes,
            "worst_price": takes[-1]["price"] if takes else None,
            "worst_wire": takes[-1]["wire"] if takes else None}


def alternatives(*, pos: dict, levels: list, p: float | None, fee_fn,
                 at: float, hold_haircut_per_contract: float = 0.0,
                 economics: dict | None = None) -> dict:
    """HOLD / EXIT / REDUCE / NETTING / INDIRECT on ONE measure, in the unit
    `bettor_funded_decision.decide` ranks: expected dollars from here over
    the whole position (the cost basis is sunk and common to all).

    THE SAME ALL-IN NET ECONOMICS AS THE ENTRY (bettor_paper_profitability_bind.
    management_economics, migration 309): `p` is the calibrated probability
    (min(raw, calibrated) -- never inflated) and every contract held -- the
    whole position on HOLD, the unsold remainder on EXIT / REDUCE -- is
    valued at p minus the strategy's residual haircut per contract; every
    sale is valued on the walked bids AFTER its fees. Holding to settlement
    pays no trading fee."""
    q = float(pos["open_qty"])
    basis = float(pos["cost_basis_usd"])
    cands, blocked = [], []
    hc = max(0.0, float(hold_haircut_per_contract or 0.0))
    if p is not None:
        p = max(0.0, float(p) - hc)
    hold_value = None if p is None else q * p
    min_impr = max(0.0, float((economics or {}).get(
        "min_improvement_per_contract_usd") or 0.0))
    if p is None:
        blocked.append({"action": A_HOLD, "blocker": R_NO_MEASURE,
                        "value_usd": None})
    else:
        cands.append({"action": A_HOLD, "candidate_id": "HOLD", "qty": q,
                      "value_usd": round(q * p, 6),
                      "expected_net_usd": round(q * p - basis, 6),
                      "worst_case_net_usd": round(-basis, 6),
                      "downside_usd": round(-basis, 6),
                      "incremental_capital_usd": 0.0,
                      "fees_usd": 0.0,
                      "evidence_quality": "MEASURE_%s" % (
                          "PRESENT" if p is not None else "ABSENT")})
    for action, sell in ((A_EXIT, q), (A_REDUCE, math.floor(q / 2))):
        if sell < 1:
            blocked.append({"action": action, "blocker": "NOTHING_TO_SELL",
                            "value_usd": None})
            continue
        w = _exit_walk(levels, sell, fee_fn, at)
        if w["sold"] <= 0:
            blocked.append({"action": action, "blocker": B_NO_BIDS,
                            "value_usd": None})
            continue
        kept = q - w["sold"]
        cash = w["proceeds_usd"] - w["fees_usd"]
        value = None if (p is None and kept > 1e-9) else round(
            cash + kept * (p or 0.0), 6)
        c = {"action": action, "candidate_id": action, "qty": w["sold"],
             "value_usd": value,
             "expected_net_usd": (None if value is None
                                  else round(value - basis, 6)),
             "worst_case_net_usd": round(cash - basis, 6),
             "downside_usd": round(cash - basis, 6),
             "incremental_capital_usd": 0.0, "fees_usd": w["fees_usd"],
             "walk": w, "unmatched_after_qty": round(kept, 6),
             "evidence_quality": "OBSERVED_BOOK_DEPTH"}
        if value is not None and hold_value is not None and min_impr > 0 \
                and value - hold_value < min_impr * w["sold"] - 1e-12:
            blocked.append(dict(c, blocker=B_BELOW_MIN_IMPROVEMENT,
                                improvement_usd=round(value - hold_value, 6),
                                required_usd=round(min_impr * w["sold"], 6)))
            continue
        (cands if value is not None else blocked).append(
            c if value is not None else dict(c, blocker=R_NO_MEASURE))
    blocked.append({"action": A_NETTING, "blocker": B_NETTING_IS_EXIT,
                    "value_usd": None,
                    "why": ("buying the complement on the same market is the "
                            "same order as the sale on a one-signed-net "
                            "venue; valued once, as EXIT")})
    blocked.append({"action": A_INDIRECT, "blocker": B_INDIRECT_NOT_SEARCHED,
                    "value_usd": None, "rankable": False,
                    "why": ("no sibling-contract hedge search runs on the "
                            "paper book; the comparison is INCOMPLETE on "
                            "this alternative, never assumed empty")})
    return {"candidates": cands, "not_rankable": blocked,
            "version": "PAPER_HOLD_RANKING_V1",
            "economics": dict(economics or {}, hold_value_per_contract=(
                None if p is None else round(p, 9)),
                residual_haircut_per_contract=round(hc, 9),
                exit_fees="charged per walked level (bettor_paper_ledger."
                          "_fee); HOLD to settlement pays none"),
            "incomplete_search": {"complete": False,
                                  "missing": [A_INDIRECT],
                                  "why": B_INDIRECT_NOT_SEARCHED}}


def probability_evidence(measure: dict, *, at: float, limit_s: float,
                         qty=None) -> dict:
    """Currency is recomputed from the source timestamp at use, not cached age."""
    from ..xavier_measure_refresh import evidence
    return evidence(measure, at=at, limit_s=limit_s, qty=qty)


async def actual_position_evidence(conn, *, group_id: str,
                                   us_market_slug: str, holding_side: str,
                                   at: float, qty, entry: dict) -> dict:
    """THE EVIDENCE STATE for an ACTUAL (small-live) position of a paper
    group, on the paper review's own measure for the group's strategy
    (`paper_benchmark.xavier_measure`: a valuation for the same contract and
    payout outcome within the freshness limit, else this process's PinnAPI
    feed cache, else the entry decision's probability, labelled stale).
    Record only -- the actual position's action follows the paper decision.
    Never raises: a failed read is PROBABILITY_UNAVAILABLE, null."""
    from . import paper_benchmark as PB
    limit = float(entry["pinnacle_max_age_s"])
    try:
        strategy = await PB.group_strategy(conn, group_id)
        if strategy not in PB.BENCHMARK_STRATEGIES:
            m = {"p": None, "stale": True,
                 "why": "STRATEGY_HAS_NO_PINNACLE_MEASURE"}
        else:
            m = await PB.xavier_measure(
                conn, {"now": at, "config": {"entry": entry}},
                pos={"group_id": group_id, "holding_side": holding_side,
                     "us_market_slug": us_market_slug},
                strategy=strategy, feed=_held_feed)
    except Exception as exc:                                    # noqa: BLE001
        m = {"p": None, "stale": True, "why": "PROBABILITY_READ_FAILED",
             "error": type(exc).__name__}
    out = probability_evidence(m, at=at, limit_s=limit,
                               qty=None if qty is None else float(qty))
    out.update({k: m[k] for k in ("feed_refusal", "valuation_id", "why",
                                  "error") if m.get(k) is not None})
    return out


async def live_position_evidence(conn, h: dict, *, at: float,
                                 qty=None) -> dict:
    """THE READER handed to the execution mirror (execmirror.run) for an
    actual position's handoff row: its holding side from the opened intent,
    the paper session's Pinnacle freshness limit."""
    from .. import bettor_paper_session as S
    side = ("LONG" if str(h.get("opened_intent") or "").endswith("BUY_LONG")
            else "SHORT")
    return await actual_position_evidence(
        conn, group_id=h["group_id"], us_market_slug=h["us_market_slug"],
        holding_side=side, at=at, qty=qty,
        entry=S.default_config()["entry"])


def protective_price(*, qty: float, cost_basis: float, fee_fn, at,
                     buffer_per_contract: float =
                     PROTECTION_BUFFER_USD_PER_CONTRACT) -> dict:
    """THE LOWEST CENT at which selling `qty` recovers the cost basis, the
    sale fees and the buffer: a positive floor on the matched quantity if
    it fills. Pure but for the fee function."""
    need = float(cost_basis) + float(buffer_per_contract) * float(qty)
    for cents in range(1, 100):
        px = cents / 100.0
        fee = float(L._fee(fee_fn, qty, px, at))
        if qty * px - fee >= need - 1e-9:
            return {"ok": True, "price": px, "fee_usd": fee,
                    "floor_usd": round(qty * px - fee - float(cost_basis), 6),
                    "floor_is": ("NOT REALIZED: a floor on the matched "
                                 "quantity only if the protection fills")}
    return {"ok": False, "refusal": "NO_PROTECTIVE_PRICE_BELOW_ONE_DOLLAR"}


# ═════════════════════════════════════════════════════════════════════
# THE REVIEW
# ═════════════════════════════════════════════════════════════════════

async def management_packet(conn, ctx: dict, *, pos: dict, measure: dict,
                            standing, at: float) -> tuple:
    """(packet, gate) for one position (xavier_packet). Each read in its
    own savepoint; a read that fails leaves its element MISSING (never
    assumed present)."""
    from .. import bettor_paper_freshness as PMF
    from .. import xavier_packet as XPK
    acct = ctx["account_id"]
    key = pos["position_key"]
    res = ident = None
    mk = {"class": None, "mark": {}}
    try:
        async with conn.transaction():
            res = (await PMF.residuals(conn, acct, [pos])).get(key)
    except Exception:                                           # noqa: BLE001
        res = None
    try:
        async with conn.transaction():
            mk = await PMF.position_mark(
                conn, account_id=acct, slug=pos["us_market_slug"],
                holding_side=pos["holding_side"], now=at)
    except Exception:                                           # noqa: BLE001
        mk = {"class": None, "mark": {}}
    try:
        async with conn.transaction():
            ident = (await PMF.identities(conn, acct, [pos])).get(key)
    except Exception:                                           # noqa: BLE001
        ident = None
    try:
        prot = PMF.protection_state([dict(s) for s in standing],
                                    pos["open_qty"], now=at)
    except Exception:                                           # noqa: BLE001
        prot = None
    packet = XPK.build(
        residual=res, evidence_state=measure.get("evidence_state"),
        probability_source=measure.get("probability_source")
        or measure.get("source"),
        valuation_id=measure.get("valuation_id"), mark=mk.get("mark"),
        mark_class=mk.get("class"), settlement=ident, protection=prot)
    packet["book"]["reason"] = mk.get("reason")
    packet["probability"].update({
        k: measure.get(k) for k in ("probability_age_s",
                                    "probability_source_at",
                                    "probability_received_at",
                                    "probability_limit_s", "feed_refusal")
        if measure.get(k) is not None})
    packet["position"] = await packet_position(
        conn, acct, pos=pos, mark=mk.get("mark") or {}, standing=standing,
        at=at)
    return packet, XPK.gate(packet)


async def packet_position(conn, acct: str, *, pos: dict, mark: dict,
                          standing, at: float) -> dict:
    """THE POSITION BLOCK OF THE PACKET (closeout): the canonical identity,
    entry, cost, P&L at the current executable bid, horizon, correlated
    exposure and standing orders. INFORMATIONAL: it gates nothing (the six
    elements of xavier_packet do). Each read in its own savepoint; a failed
    read is null with its reason, never assumed."""
    q = float(pos.get("open_qty") or 0.0)
    bid = mark.get("bid")
    out = {"position_key": pos.get("position_key"),
           "group_id": pos.get("group_id"), "venue": "PMUS",
           "strategy": pos.get("strategy"),
           "us_market_slug": pos.get("us_market_slug"),
           "holding_side": pos.get("holding_side"),
           "fixture": pos.get("fixture"), "label": pos.get("label"),
           "open_qty": q,
           "entry_price_incl_fees": pos.get("avg_cost_per_contract_incl_fees"),
           "entry_at": pos.get("first_fill_at"),
           "cost_basis_usd": pos.get("cost_basis_usd"),
           "realized_pnl_usd": pos.get("realized_pnl_usd"),
           "unrealized_at_bid_usd": (
               None if bid is None or pos.get("cost_basis_usd") is None
               else round(q * float(bid) - float(pos["cost_basis_usd"]), 6)),
           "unrealized_basis": "OPEN_QTY_AT_THE_CURRENT_BID_BEFORE_EXIT_FEES",
           "standing_orders": len(list(standing or ()))}
    try:
        async with conn.transaction():
            gs = await conn.fetchval(
                "SELECT extract(epoch FROM game_start)::float8 FROM "
                " us_premap WHERE market_slug = $1 LIMIT 1",
                pos.get("us_market_slug"))
        out["event_start"] = gs
        out["seconds_to_start"] = (None if gs is None
                                   else round(float(gs) - float(at), 1))
    except Exception as exc:                                    # noqa: BLE001
        out["event_start"] = None
        out["event_start_unread"] = type(exc).__name__
    try:
        async with conn.transaction():
            out["correlated_groups_in_fixture"] = int(await conn.fetchval(
                "SELECT count(DISTINCT o.group_id) FROM paper_orders o "
                " WHERE o.account_id = $1 AND o.fixture = $2 "
                "   AND o.group_id <> $3 AND o.role = 'ENTRY' "
                "   AND EXISTS (SELECT 1 FROM paper_fills f "
                "                WHERE f.group_id = o.group_id) "
                "   AND NOT EXISTS (SELECT 1 FROM paper_settlements s "
                "                    WHERE s.group_id = o.group_id)",
                acct, pos.get("fixture"), pos.get("group_id")) or 0)
    except Exception as exc:                                    # noqa: BLE001
        out["correlated_groups_in_fixture"] = None
        out["correlated_unread"] = type(exc).__name__
    return out


async def persist_probability_snapshot(conn, *, account_id: str, pos: dict,
                                       measure: dict, at: float):
    """A FRESH probability that carries no persisted valuation (the
    in-process PinnAPI feed reading) is written to
    xavier_probability_snapshots (migration 303) and the measure then
    carries that row's id. Only a FRESH reading with a probability is
    written; anything else, or a failed write, leaves valuation_id None --
    and the packet's probability element MISSING (never assumed present).
    Returns the snapshot id or None."""
    if measure.get("evidence_state") != E_FRESH or \
            measure.get("valuation_id") is not None or \
            measure.get("p") is None:
        return None
    feed = measure.get("feed") if isinstance(measure.get("feed"), dict) \
        else {}
    src_at = measure.get("probability_source_at") or measure.get(
        "pinnacle_at")
    rcv_at = measure.get("probability_received_at") or measure.get(
        "pinnacle_received_at")
    try:
        async with conn.transaction():
            sid = await conn.fetchval(
                "INSERT INTO xavier_probability_snapshots (account_id, "
                " group_id, us_market_slug, holding_side, source, "
                " probability, source_at, received_at, limit_s, "
                " payout_event, payout_is_complement, evidence) VALUES "
                " ($1,$2,$3,$4,$5,$6, CASE WHEN $7::float8 IS NULL THEN NULL"
                " ELSE to_timestamp($7) END, CASE WHEN $8::float8 IS NULL "
                " THEN NULL ELSE to_timestamp($8) END, $9,$10,$11,"
                " $12::jsonb) RETURNING snapshot_id",
                account_id, pos["group_id"], pos["us_market_slug"],
                pos["holding_side"],
                str(measure.get("probability_source")
                    or measure.get("source") or "UNKNOWN"),
                float(measure["p"]),
                None if src_at is None else float(src_at),
                None if rcv_at is None else float(rcv_at),
                measure.get("probability_limit_s")
                or measure.get("pinnacle_limit_s"),
                feed.get("payout_event"), feed.get("payout_is_complement"),
                json.dumps({"feed": feed, "recorded_for_review_at": at,
                            "age_s": measure.get("probability_age_s")},
                           default=str))
    except Exception:                                           # noqa: BLE001
        return None
    measure["valuation_id"] = sid
    measure["valuation_store"] = VALUATION_STORE_SNAPSHOT
    return sid


async def _latest_book(conn, slug: str):
    return await conn.fetchrow(
        "SELECT * FROM paper_book_observations WHERE us_market_slug=$1 "
        " ORDER BY observed_at DESC LIMIT 1", slug)


async def _held_feed(conn, *, pos: dict, payout_event, payout_is_complement,
                     at: float, max_age_s: float) -> dict:
    """THE ON-DEMAND READ BEFORE A REVIEW: the held contract on THIS
    process's PinnAPI cache (read only: no socket, no network), bounded to
    pinnapi_feed_runtime.HELD_ON_DEMAND_BUDGET_S (1 s) end to end; fresh only
    when the quote's source change is within the 30 s limit and the event,
    full-game moneyline market, period and side identity match.
    FEED_OWNERSHIP_NOT_HELD when no owner runs here."""
    from .. import pinnapi_feed_runtime as FR
    try:
        async with asyncio.timeout(FR.HELD_ON_DEMAND_BUDGET_S):
            return await FR.held_moneyline(
                conn, us_market_slug=pos["us_market_slug"],
                payout_event=payout_event,
                payout_is_complement=payout_is_complement, at=at,
                max_age_s=max_age_s,
                entry_event_key=pos.get("entry_event_key"),
                entry_line=pos.get("entry_line"))
    except TimeoutError:
        return {"ok": False, "reason": FR.R_ON_DEMAND_TIMEOUT}


async def _measure(conn, ctx, *, pos: dict, levels_buy: list) -> dict:
    """Preserve each strategy's measure; refresh Derek's held blend as well.

    Benchmarks still use their own probability policy. The original two-model
    policy uses its existing scorer and blend, but can now obtain a current
    probability for its ENTRY-PROVEN contract through the exact held reader.
    No stale entry probability is ever relabelled current.
    """
    from . import paper_benchmark as PB
    from . import paper_derek as PD
    from .. import xavier_measure_refresh as MR
    strat = await PB.group_strategy(conn, pos["group_id"])
    if strat in PB.BENCHMARK_STRATEGIES:
        return await PB.xavier_measure(conn, ctx, pos=pos, strategy=strat,
                                       feed=_held_feed)
    at = _clock(ctx)
    ent = ctx["config"]["entry"]
    lookback = float(ent["valuation_lookback_s"])
    max_age = float(ent["pinnacle_max_age_s"])
    intent = DP.LONG if pos["holding_side"] == "LONG" else DP.SHORT
    contract = await conn.fetchrow(
        "SELECT v.id,v.us_market_slug,v.payout_event,v.payout_is_complement,"
        "       v.event_key,v.market,v.line,v.observed_at,v.received_at "
        "FROM external_valuations v JOIN paper_decisions d ON d.valuation_id=v.id "
        "JOIN paper_orders o ON o.decision_id=d.decision_id "
        "WHERE o.group_id=$1 AND o.us_market_slug=$2 AND o.holding_side=$3 AND o.account_id=$4 "
        "AND o.role='ENTRY' ORDER BY o.created_at,o.order_id LIMIT 1",
        pos["group_id"],pos["us_market_slug"],pos["holding_side"],ctx["account_id"])
    v = None
    if contract is not None:
        v = await conn.fetchrow(
            "SELECT id,probability,observed_at,received_at FROM external_valuations "
            "WHERE us_market_slug=$1 AND buy_intent=$2 AND probability IS NOT NULL "
            "AND payout_event=$3 AND payout_is_complement=$4 "
            "AND market IS NOT DISTINCT FROM $5 AND line IS NOT DISTINCT FROM $6 "
            "AND event_key IS NOT DISTINCT FROM $7 "
            "AND decided_at>to_timestamp($8) ORDER BY decided_at DESC,id DESC LIMIT 1",
            pos["us_market_slug"],intent,contract["payout_event"],
            contract["payout_is_complement"],contract["market"],contract["line"],
            contract["event_key"],at-lookback)
    model = (ctx.get("derek") or {}).get("model")
    if model is None:
        model = await PD.research_model(conn, at=at, verify=False)
    # THE PROVIDER FIXTURE HANDED TO THE HELD READ (RC6 xavier-records,
    # xavier_held_fixture): the entry's PinnAPI key, or for a metered entry
    # key the fixture the PinnAPI matcher recorded for this same contract.
    # Only the held read's input; the stored-valuation lookup above keeps
    # the entry's own event key.
    held_fx = None
    held_contract = dict(contract) if contract is not None else None
    if held_contract is not None:
        from .. import xavier_held_fixture as XHF
        held_fx = await XHF.held_key(
            conn, us_market_slug=pos["us_market_slug"],
            entry_event_key=held_contract.get("event_key"), at=at)
        held_contract["held_event_key"] = held_fx.get("event_key")
    got = await MR.refresh(
        conn,pos=pos,contract=held_contract,
        stored=dict(v) if v is not None else None,model=model,levels_buy=levels_buy,
        at=at,max_age_s=max_age,score=PD.score,blend=DP.blend,held_feed=_held_feed,
        clock=lambda: _clock(ctx))
    if got.get("ok"):
        if isinstance(got.get("feed"), dict):
            # the held read priced it: the fixture it was handed, and why
            got = dict(got, feed=dict(got["feed"], held_fixture=held_fx))
        return dict(got,model_label=PD.MODEL_LABEL)
    detail = got.get("feed_detail")
    if held_fx is not None:
        detail = dict(detail or {}, held_fixture=held_fx)
    d = await conn.fetchrow(
        "SELECT d.p_blended,d.decided_at FROM paper_decisions d "
        "JOIN paper_orders o ON o.decision_id=d.decision_id "
        "WHERE o.group_id=$1 AND o.us_market_slug=$2 AND o.holding_side=$3 AND o.account_id=$4 "
        "AND o.role='ENTRY' AND d.p_blended IS NOT NULL "
        "ORDER BY o.created_at,o.order_id LIMIT 1",
        pos["group_id"],pos["us_market_slug"],pos["holding_side"],ctx["account_id"])
    if d is not None:
        # THE ENTRY READING'S OWN STAMPS (RC6 xavier-records): the blend was
        # taken on the entry valuation's Pinnacle reading, so its source and
        # receipt instants are that row's -- recorded beside the decision
        # instant, never substituted for it (production TB-DAL showed
        # source_at NOT_RECORDED on every stale review of a Derek position;
        # the benchmark measure has always carried these)
        e_obs = None if contract is None else contract.get("observed_at")
        e_rcv = None if contract is None else contract.get("received_at")
        return {"p":float(d["p_blended"]),"source":"ENTRY_TIME_MEASURE",
                "at":L._epoch(d["decided_at"]),"stale":True,"void_applied":False,
                "entry_pinnacle_at":None if e_obs is None else L._epoch(e_obs),
                "entry_pinnacle_received_at":(None if e_rcv is None
                                              else L._epoch(e_rcv)),
                "why":got.get("why"),"feed_refusal":got.get("feed_refusal"),
                "feed_detail":detail}
    return {"p":None,"source":None,"stale":True,"why":R_NO_MEASURE,
            "feed_refusal":got.get("feed_refusal"),"refresh_detail":got.get("why"),
            "feed_detail":detail}


def last_evidence_expiry(last: dict | None) -> float | None:
    """When the last review's FRESH probability stops being current: its
    own source stamp + the limit it recorded (the existing 30 s rule).
    None when the last review was not fresh (nothing to expire) or carried
    no stamp. Pure."""
    m = (last or {}).get("measure") or {}
    if m.get("evidence_state") != E_FRESH:
        return None
    return XF.valuation_block(m, assessed_at=(last or {}).get(
        "reviewed_at")).get("expires_at")


def _trigger(*, group: str, new_handoffs: list, last: dict | None,
             last_fill_at, book_at, best_exit, at: float,
             backstop_s: float, feed_change_at=None, order_event_at=None,
             valuation_at=None, event_start_at=None,
             exit_intent_due_at=None) -> str | None:
    """WHICH REVIEW A HELD GROUP IS DUE. Every requeue is bounded: each
    trigger fires once per change (it compares with the last review's
    instant), so nothing loops."""
    if group in new_handoffs or last is None:
        return T_FIRST
    if last_fill_at is not None and last_fill_at > last["reviewed_at"]:
        return T_FILL
    # AN OPEN EXIT INTENT'S DEADLINE PASSED since the last review: the
    # position may be unprotected -- continue or abandon it now
    if exit_intent_due_at is not None and exit_intent_due_at <= at and \
            exit_intent_due_at > last["reviewed_at"]:
        return T_EXIT_INTENT
    # THE HELD MARKET MOVED ON THE PINNAPI FEED since the last review
    # (pinnapi_held): a fresh probability exists for ~30 s from now on
    if feed_change_at is not None and feed_change_at > last["reviewed_at"]:
        return T_MARKET
    # A MANAGEMENT ORDER OF THE GROUP REACHED A TERMINAL STATE (cancelled /
    # expired / filled protection) since the last review: the protection
    # changed, so the position is re-assessed now
    if order_event_at is not None and order_event_at > last["reviewed_at"]:
        return T_ORDER
    # A NEWER STORED VALUATION OF THE CONTRACT than the last review saw
    m = last.get("measure") or {}
    seen = XF.valuation_block(m, assessed_at=last["reviewed_at"]).get(
        "source_at")
    if valuation_at is not None and valuation_at > last["reviewed_at"] and \
            (seen is None or valuation_at > float(seen) + 1e-6):
        return T_VALUATION
    # THE GAME STATE CHANGED: the event started after the last review
    if event_start_at is not None and \
            last["reviewed_at"] < event_start_at <= at:
        return T_GAME
    # THE LAST REVIEW'S FRESH PROBABILITY HAS EXPIRED: re-assess at once
    # (fresh again if a newer probability exists; otherwise the review
    # records WAITING_FOR_FRESH_EVIDENCE -- never a stale HOLD)
    exp = last_evidence_expiry(last)
    if exp is not None and at >= exp and exp > last["reviewed_at"]:
        return T_EXPIRY
    prev_exit = m.get("best_exit_at_review")
    if book_at is not None and book_at > last["reviewed_at"] and \
            best_exit is not None and prev_exit is not None and \
            abs(float(best_exit) - float(prev_exit)) > 1e-9:
        return T_MARKET
    if at - last["reviewed_at"] >= backstop_s:
        return T_BACKSTOP
    return None


async def review_group(conn, ctx: dict, group_id: str, *,
                       trigger: str, due_at=None) -> dict:
    from . import xavier_management as XM
    from . import xavier_policy as XP
    from . import xavier_small_live_policy as XSP
    from .. import bettor_xavier_standing_orders as SPO
    at = _clock(ctx)
    fee_fn = ctx.get("fee_fn")
    acct = ctx["account_id"]
    allpos = [p for p in await L.positions(conn, acct)
              if p["group_id"] == group_id]
    # THE MANAGEMENT POLICY THIS REVIEW RUNS UNDER, as recorded: the owner-
    # approved artifact (exact id / version / sha256 / approver) only when
    # approved with a matching hash; otherwise READY_FOR_OWNER_APPROVAL.
    mpol = await XSP.load_review_record(conn) if allpos else None
    reviews = []
    for pos in allpos:
        obs = await _latest_book(conn, pos["us_market_slug"])
        md = None if obs is None or obs["error"] else {
            "bids": L._j(obs["bids"]) or [], "offers": L._j(obs["offers"])
            or []}
        exit_lv = SIM.levels_for(md, direction="SELL",
                                 holding_side=pos["holding_side"])["levels"]
        buy_lv = SIM.levels_for(md, direction="BUY",
                                holding_side=pos["holding_side"])["levels"]
        measure = await _measure(conn, ctx, pos=pos, levels_buy=buy_lv)
        measure["best_exit_at_review"] = (exit_lv[0]["price"] if exit_lv
                                          else None)
        measure["book_obs_id"] = None if obs is None else obs["obs_id"]
        measure.update(probability_evidence(
            measure, at=at, qty=pos["open_qty"],
            limit_s=float(ctx["config"]["entry"]["pinnacle_max_age_s"])))
        # a fresh reading with no persisted valuation (the in-process feed)
        # is persisted first; if that fails it stays without an id and the
        # packet's probability element is MISSING
        await persist_probability_snapshot(conn, account_id=acct, pos=pos,
                                           measure=measure, at=at)
        fresh = measure["evidence_state"] == E_FRESH
        # THE EXIT WALK MUST BE FRESH: the book the alternatives are walked
        # on is current within the 300 s executable-mark SLA, readable, and
        # shows exit depth -- never the newest row of any age
        # (a receipt stamp a moment after the review instant is clock
        # skew, clamped to 0 as the mark classifier does; one more than
        # WALK_MAX_FUTURE_SKEW_S ahead is a clock disagreement: not fresh)
        walk_age = (None if obs is None or obs["error"]
                    else at - L._epoch(obs["observed_at"]))
        walk_fresh = (walk_age is not None
                      and -WALK_MAX_FUTURE_SKEW_S <= walk_age
                      <= L.MARK_STALE_AFTER_S
                      and bool(exit_lv))
        if walk_age is not None and walk_age < 0:
            walk_age = 0.0 if walk_fresh else walk_age
        measure["exit_walk"] = {"book_obs_id": measure["book_obs_id"],
                                "age_s": None if walk_age is None
                                else round(walk_age, 3),
                                "sla_s": L.MARK_STALE_AFTER_S,
                                "levels": len(exit_lv), "fresh": walk_fresh}
        # ── THE MANAGEMENT PACKET (xavier_packet) ───────────────────────
        # A management action (HOLD / EXIT / REDUCE / a hedge) needs the
        # reconciled qty, a FRESH probability, a current executable book
        # with exit depth, the settlement identity and the protection
        # state. Missing any -> a recorded refusal naming each element; the
        # cost-recovery protection is still maintained, no sale is ranked.
        standing = await conn.fetch(
            "SELECT * FROM paper_orders WHERE group_id=$1 "
            "   AND us_market_slug=$2 AND holding_side=$3 "
            "   AND role='STANDING_PROTECTION' AND state = ANY($4::text[])",
            group_id, pos["us_market_slug"], pos["holding_side"],
            list(L.OPEN_STATES))
        packet, pgate = await management_packet(
            conn, ctx, pos=pos, measure=measure, standing=standing, at=at)
        manageable = fresh and pgate["complete"]
        rid = "paperrev:" + _h(group_id, pos["position_key"], at, trigger)
        # THE PERSISTED EXIT INTENT (migration 313): an EXIT that cancelled
        # this position's protection is continued from its own row, never
        # inferred from "the latest review" and never from the deciding
        # review's (now expired) probability
        xi = await XI.load_open(conn, acct, group_id, pos["position_key"])
        xphase = None if xi is None else await XI.advance(
            conn, xi, standing=standing, at=at, review_id=rid,
            window_s=float(ctx["config"]["entry"]["pinnacle_max_age_s"]))
        revalidating = (xphase or {}).get("phase") == XI.PH_REVALIDATE
        if xphase is not None:
            measure["exit_intent"] = dict(xphase, intent_id=xi["intent_id"],
                                          state=xi["state"])
        # THE EXIT CONTINUATION: the cancel is terminal and the only packet
        # gap is the protection that EXIT cancelled; every other element --
        # a fresh probability with a persisted valuation, the book, depth,
        # qty, identity -- is present NOW, at this review
        cont = None
        if revalidating and fresh and walk_fresh and \
                not pgate["complete"] and \
                pgate["missing"] == [XPK_P_PROTECTION]:
            cont = {"intent_id": xi["intent_id"],
                    "decided_review_id": xi["decided_review_id"],
                    "cancelled_orders": xi["cancel_orders"],
                    "cancel_terminal_at": xi.get("cancel_terminal_at"),
                    "revalidate_by": xi.get("revalidate_by"),
                    "probability_source_at": measure.get(
                        "probability_source_at")}
        if cont is not None:
            measure["exit_continuation"] = cont
            manageable = True
        # RANKABLE only on a fresh probability with a persisted valuation, a
        # complete packet AND a fresh executable exit walk
        rankable = manageable and walk_fresh
        measure["management_packet"] = {
            "complete": pgate["complete"], "missing": pgate["missing"],
            "mark_class": packet["book"]["mark_class"],
            "version": packet["version"]}
        # THE SAME ALL-IN ECONOMICS AS THE ENTRY: the calibrated HOLD
        # probability and the residual haircut (migration 309)
        from .. import bettor_paper_profitability_bind as PBIND
        mecon = await PBIND.management_economics(
            conn, account_id=acct, pos=pos, p_raw=measure.get("p"), at=at)
        measure["management_economics"] = {
            k: mecon.get(k) for k in ("p_raw", "p_hold",
                                      "haircut_per_contract", "status",
                                      "min_improvement_per_contract_usd")}
        alts = alternatives(
            pos=pos, levels=exit_lv,
            p=(mecon.get("p_hold") if measure.get("p") is not None
               else None),
            fee_fn=fee_fn, at=at,
            hold_haircut_per_contract=mecon.get("haircut_per_contract")
            or 0.0, economics=measure["management_economics"])
        if not rankable or measure.get("stale") or \
                measure.get("p") is None:
            # STALE / INCOMPLETE EVIDENCE: HOLD, EXIT AND REDUCE ALL LEAVE THE
            # RANKABLE SET BEFORE THE SELECTOR RUNS -- XP.run ranks none of
            # them (a HOLD priced on a stale p is not a decision either). The
            # cost-recovery protection is still maintained (management_action
            # on non-fresh evidence -> PROTECT).
            blocker = (B_STALE_MEASURE if (not fresh or measure.get("stale")
                                           or measure.get("p") is None)
                       else B_PACKET_INCOMPLETE if not pgate["complete"]
                       else B_NO_FRESH_EXIT_WALK)
            extra = ({} if blocker == B_STALE_MEASURE else
                     {"packet_missing": pgate["missing"]}
                     if blocker == B_PACKET_INCOMPLETE else
                     {"exit_walk": measure["exit_walk"]})
            gated = (A_HOLD, A_EXIT, A_REDUCE)
            keep = [c for c in alts["candidates"]
                    if c["action"] not in gated]
            def _label(c):
                # a HOLD valued on non-current evidence keeps its value only
                # as the labelled entry-time figure, never as current EV
                if c["action"] != A_HOLD or fresh:
                    return c
                return dict(c, ev_basis=measure["evidence_state"],
                            ev_is_current=False, expected_net_usd=None,
                            entry_time_expected_net_usd=c.get(
                                "expected_net_usd"))
            alts["not_rankable"] = alts["not_rankable"] + [
                dict(_label(c), blocker=blocker, **extra)
                for c in alts["candidates"] if c["action"] in gated]
            alts["candidates"] = keep
        policy = await XP.load(conn)
        sel = XP.run(policy, hold_ranking=alts, limits=None)
        # THE HOLD VALUE IS LABELLED BY ITS EVIDENCE (after ranking, record
        # only): a value priced on a stale probability is never shown as
        # the current expected value.
        alts["candidates"] = [
            c if c["action"] != A_HOLD else dict(
                c, ev_basis=measure["evidence_state"], ev_is_current=fresh,
                **({} if fresh else {
                    "expected_net_usd": None,
                    "entry_time_expected_net_usd": c.get(
                        "expected_net_usd")}))
            for c in alts["candidates"]]
        exceptional = []
        if obs is None:
            exceptional.append("NO_BOOK_OBSERVED_FOR_THIS_MARKET")
        elif obs["error"]:
            exceptional.append("LATEST_BOOK_UNREADABLE")
        if not exit_lv:
            exceptional.append(B_NO_BIDS)
        if measure.get("stale"):
            exceptional.append("MEASURE_%s" % (measure.get("source")
                                               or "ABSENT"))
        if not fresh:
            exceptional.append(measure["evidence_state"])
        if not pgate["complete"] and cont is None:
            exceptional.append(pgate["refusal"])
            exceptional.extend(pgate["missing"])
        if cont is not None:
            exceptional.append("EXIT_CONTINUATION_AFTER_TERMINAL_CANCEL")
        # ── STANDING PROTECTION AND THE ACTION ──────────────────────
        action: dict[str, Any] = {"taken": "NONE"}
        chosen = sel.get("selected")
        prot = protective_price(qty=pos["open_qty"],
                                cost_basis=pos["cost_basis_usd"],
                                fee_fn=fee_fn, at=at)
        # ── R30 · THE ONE CANONICAL MANAGEMENT INTENT ───────────────────
        # Xavier's review decides ONE action (live_parity.management_action);
        # it is recorded immutably and BOTH adapters consume it: the paper
        # book below and the SMALL LIVE adapter (SHADOW) after it.
        decided = CI.management_action(
            chosen=chosen, fresh=rankable,
            stale=bool(measure.get("stale")),
            p_missing=measure.get("p") is None,
            protection_ok=bool(prot.get("ok")), standing_live=bool(standing),
            candidate=sel.get("selected_candidate"), protective=prot,
            open_qty=pos["open_qty"])
        # R30A · EVERY ALTERNATIVE, VALUED OR WITH ITS REASON (section 8).
        # Xavier's REALLOCATE comparison is made HERE, once, before the
        # intent (and handed to the assessment below, which records the
        # same comparison): HOLD, SELL_EXIT, SELL_REDUCE, CANCEL_PROTECTION_
        # BEFORE_EXIT, MAINTAIN_STANDING_PROTECTION, INDIRECT_HEDGE,
        # REALLOCATE and NO_ORDER all enter the intent. Nothing here changes
        # the action decided above: management_action is unchanged (its
        # choice is consistent with its own values -- a sale only on fresh
        # evidence and only when the selector ranked it highest, protection
        # otherwise), and the set only RECORDS what each alternative was
        # worth or why it could not be valued.
        realloc = await XM.paper_reallocation(
            conn, ctx, group_id=group_id, pos=pos, trigger=trigger, at=at,
            measure=measure, exit_levels=exit_lv)
        try:
            alt_set = CI.management_alternatives(
                alts=alts, decided=decided, mechanical_selection=chosen,
                standing_live=bool(standing), protective=prot,
                open_qty=pos["open_qty"],
                reallocate=(realloc.get("reallocate")
                            if realloc.get("reallocate") is not None else
                            {"blocker": realloc.get("why")
                             or "REALLOCATE_NOT_COMPARED"}))
        except Exception:                                       # noqa: BLE001
            # never fails the review: the intent then builds the set from
            # the ranking alone (REALLOCATE UNAVAILABLE: not compared)
            alt_set = None
        mgmt_policy = {
            "status": "RECORDED",
            "small_live_management_policy": mpol,
            "selection_policy": {k: (policy or {}).get(k) for k in (
                "policy_key", "version", "source", "why", "approved_by")},
            "new_exposure": False,
            "rule": ("management actions here only reduce exposure (a sale "
                     "or a resting protective sale of held inventory); the "
                     "LIVE fail-closed policy rule governs NEW exposure")}
        # WHAT THE REVIEW RECOMMENDS (owner P0): the selection only on FRESH
        # evidence AND a complete management packet. On stale / absent
        # evidence the selector's HOLD is merely what was left after the
        # sales were blocked, so the recorded recommendation is
        # WAITING_FOR_FRESH_EVIDENCE (or MANAGEMENT_UNAVAILABLE_STALE_INPUT);
        # on a fresh probability with an incomplete packet it is
        # MANAGEMENT_UNAVAILABLE_STALE_INPUT with the packet refusal recorded
        # -- the protection is still maintained, no discretionary sale.
        recorded = XF.recorded_recommendation(
            evidence_state=measure["evidence_state"], selected=chosen)
        if fresh and not rankable:
            recorded = XF.REC_UNAVAILABLE
        mintent = None
        try:
            mintent = CI.build_management_intent(
                review_id=rid, group_id=group_id,
                position_key=pos["position_key"],
                strategy=pos.get("strategy") or await _strategy(conn, group_id),
                valuation=XF.valuation_block(
                    measure, assessed_at=at,
                    limit_s=float(ctx["config"]["entry"]["pinnacle_max_age_s"])),
                evidence_state=measure["evidence_state"],
                recommendation=recorded,
                mechanical_selection=chosen, decided=decided,
                us_market_slug=pos["us_market_slug"],
                holding_side=pos["holding_side"], alternatives=alts,
                reason={"selection_reason": sel.get("selection_reason"),
                        "refusal": sel.get("refusal"),
                        "margin_over_runner_up": sel.get(
                            "margin_over_runner_up"),
                        "exceptional": list(exceptional),
                        "management_packet": pgate},
                created_at=at, alternative_set=alt_set, policy=mgmt_policy)
            rec_hook = DH.CANONICAL_MANAGEMENT_RECORD
            if rec_hook is None or not await rec_hook(conn, mintent):
                mintent = None
        except Exception:                                       # noqa: BLE001
            mintent = None
        act = decided["action"]
        tl = decided.get("target_limit") or {}
        sched = ctx.get("schedule_review_at")
        if act == CI.ACT_CANCEL_FIRST:
            # THE EXIT WAITS FOR THE PROTECTION TO BE TERMINAL: its
            # inventory is committed to the resting sale.
            res = {}
            for s in standing:
                if s["state"] != "CANCEL_PENDING":
                    res[s["order_id"]] = await SIM.request_cancel(
                        conn, s["order_id"], now=at,
                        reason="EXIT_WAITS_FOR_STANDING_TERMINAL")
            action = {"taken": "CANCEL_STANDING_BEFORE_EXIT",
                      "orders": [s["order_id"] for s in standing]}
            if xi is None:
                xi = await XI.record(
                    conn, account_id=acct, pos=pos, review_id=rid,
                    selection=chosen, at=at,
                    probability_source_at=measure.get(
                        "probability_source_at"),
                    orders=action["orders"], cancel_results=res)
                if res and not any(r.get("ok") for r in res.values()) and \
                        not any(s["state"] == "CANCEL_PENDING"
                                for s in standing):
                    # no cancel was accepted: the protection stands (or
                    # is already terminal); the intent cannot proceed
                    await XI.resolve(conn, xi, state=XI.S_ABANDONED,
                                     resolution=XI.R_CANCEL_REFUSED, at=at,
                                     review_id=rid, detail={"results": res})
            action["exit_intent_id"] = None if xi is None else \
                xi["intent_id"]
            if sched is not None and xi is not None and \
                    xi["state"] == XI.S_CANCEL_REQUESTED:
                try:
                    sched(pos["us_market_slug"], xi["cancel_deadline_at"])
                except Exception:                               # noqa: BLE001
                    pass
        elif act in (CI.ACT_EXIT, CI.ACT_REDUCE):
            action = await _submit_sale(
                conn, ctx, pos=pos, role=chosen, qty=decided["target_qty"],
                limit=tl.get("limit_price"), wire=tl.get("wire_price"),
                review_key="%s:%s:%s" % (group_id, pos["position_key"], at))
            if action.get("ok") and action.get("order_id"):
                # THE UNSOLD REMAINDER STAYS PROTECTED, IN THIS REVIEW: a
                # REDUCE sells floor(q/2) and an EXIT only what the book
                # absorbs; the cancel-first left the rest bare until a later
                # review. Only the inventory the sale does NOT commit is
                # protected (held_uncommitted, under the ledger lock): never
                # a second sale of the same contracts, nothing when the
                # sale commits the whole position.
                action["remainder_protection"] = await _maintain_standing(
                    conn, ctx, pos=pos, standing=[dict(s) for s in standing],
                    prot=prot, md=md, at=at, SPO=SPO)
            if revalidating:
                if action.get("ok") and action.get("order_id"):
                    await XI.resolve(
                        conn, xi, state=XI.S_EXIT_SUBMITTED,
                        resolution=XI.R_EXIT_ORDER_CREATED, at=at,
                        review_id=rid, exit_order_id=action["order_id"],
                        protection_order_id=(action.get(
                            "remainder_protection") or {}).get("order_id"))
                else:
                    # THE EXIT NO LONGER QUALIFIES (refused): abandon it
                    # explicitly and restore the protection now
                    prot_act = await _maintain_standing(
                        conn, ctx, pos=pos, standing=[], prot=prot, md=md,
                        at=at, SPO=SPO)
                    await XI.resolve(
                        conn, xi, state=XI.S_ABANDONED,
                        resolution=XI.R_EXIT_REFUSED, at=at, review_id=rid,
                        protection_order_id=prot_act.get("order_id"),
                        detail={"refusal": action.get("refusal")})
                    action["protection_restored"] = prot_act
        elif act == CI.ACT_PROTECT and revalidating and not rankable and \
                at < float(xi["revalidate_by"]):
            # THE CANCEL IS TERMINAL, THE FRESH PROBABILITY / BOOK IS NOT
            # HERE YET: the intent waits -- bounded by revalidate_by (the
            # terminal instant + the 30 s probability limit) -- for its own
            # fresh evidence; a review is scheduled for that instant
            action = {"taken": "EXIT_REVALIDATION_PENDING",
                      "exit_intent_id": xi["intent_id"],
                      "revalidate_by": xi["revalidate_by"],
                      "missing": pgate["missing"],
                      "evidence_state": measure["evidence_state"],
                      "walk_fresh": walk_fresh}
            if sched is not None:
                try:
                    sched(pos["us_market_slug"], float(xi["revalidate_by"]))
                except Exception:                               # noqa: BLE001
                    pass
        elif act == CI.ACT_PROTECT:
            action = await _maintain_standing(
                conn, ctx, pos=pos, standing=[dict(s) for s in standing],
                prot=prot, md=md, at=at, SPO=SPO)
            if revalidating:
                why = (XI.R_NOT_EXIT if rankable else
                       XI.R_NO_FRESH if not fresh else
                       XI.R_NO_DEPTH if not walk_fresh else XI.R_PACKET)
                await XI.resolve(
                    conn, xi, state=XI.S_ABANDONED, resolution=why, at=at,
                    review_id=rid, protection_order_id=action.get("order_id"),
                    detail={"selected": chosen, "missing": pgate["missing"],
                            "evidence_state": measure["evidence_state"],
                            "walk": measure["exit_walk"]})
        elif chosen in (A_EXIT, A_REDUCE):
            action = {"taken": "NONE", "why": tl.get("why")
                      or "NO_WALKABLE_SALE"}
            if revalidating:
                prot_act = await _maintain_standing(
                    conn, ctx, pos=pos, standing=[dict(s) for s in standing],
                    prot=prot, md=md, at=at, SPO=SPO)
                await XI.resolve(
                    conn, xi, state=XI.S_ABANDONED, resolution=XI.R_NO_DEPTH,
                    at=at, review_id=rid,
                    protection_order_id=prot_act.get("order_id"),
                    detail={"why": action["why"]})
                action["protection_restored"] = prot_act
        elif revalidating:
            # NOTHING SELECTABLE: abandoned and recorded -- never left open.
            # This branch runs for ANY no-order decision, not only "no
            # protective price": a fresh revalidation whose selector chose
            # nothing used to abandon the intent and leave the position
            # bare although a price existed. The protection the EXIT
            # cancelled is restored whenever one can be priced
            # (_maintain_standing records NONE with the reason otherwise).
            prot_act = await _maintain_standing(
                conn, ctx, pos=pos, standing=[dict(s) for s in standing],
                prot=prot, md=md, at=at, SPO=SPO)
            action["protection_restored"] = prot_act
            await XI.resolve(conn, xi, state=XI.S_ABANDONED,
                             resolution=XI.R_PACKET, at=at, review_id=rid,
                             protection_order_id=prot_act.get("order_id"),
                             detail={"decided": decided.get("action"),
                                     "protection_ok": bool(prot.get("ok"))})
        if xi is not None:
            action.setdefault("exit_intent_id", xi["intent_id"])
            action["exit_intent_state"] = xi["state"]
            if xi.get("resolution"):
                action["exit_intent_resolution"] = xi["resolution"]
        if mintent is not None:
            action["canonical_intent_id"] = mintent["intent_id"]
            ad = DH.CANONICAL_MANAGEMENT_ADAPTERS
            if ad is not None:
                try:
                    action["live_parity"] = await ad(
                        conn, mintent, taken=dict(action),
                        open_qty=pos["open_qty"])
                except Exception as exc:                        # noqa: BLE001
                    action["live_parity"] = {"error": type(exc).__name__}
        # THE REVIEWED POSITION'S OWN FILLED PROTECTION (RC6 archer-
        # lifecycle): the simulated fills of the standing sales of THIS
        # (account, group, market, holding side) -- the same key the standing
        # orders above are read by. It was summed over the whole group, so a
        # group holding two positions (a hedge leg, a second market) would
        # credit one position with the other's protective sales.
        confirmed = await conn.fetchval(
            "SELECT coalesce(sum(qty), 0) FROM paper_fills WHERE account_id=$1"
            "   AND group_id=$2 AND us_market_slug=$3 AND holding_side=$4"
            "   AND role='STANDING_PROTECTION'", acct, group_id,
            pos["us_market_slug"], pos["holding_side"])
        live = [dict(s) for s in standing]
        resting_qty = sum(float(s["qty"]) - float(s["filled_qty"])
                          for s in live)
        exposure = exposure_view(pos, resting_qty=resting_qty,
                                 filled_protection_qty=float(confirmed))
        valuation = XF.valuation_block(
            measure, assessed_at=at,
            limit_s=float(ctx["config"]["entry"]["pinnacle_max_age_s"]))
        await conn.execute(
            "INSERT INTO paper_xavier_reviews (review_id, session_id, "
            " account_id, group_id, reviewed_at, trigger, recommendation, "
            " refusal, alternatives, selection, exposure, standing, "
            " confirmed_protection, incomplete_search, exceptional, measure,"
            " action, strategy) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,"
            " $10::jsonb,$11::jsonb,$12::jsonb,$13::jsonb,$14::jsonb,"
            " $15::jsonb,$16::jsonb,$17::jsonb,$18) ON CONFLICT DO NOTHING",
            rid, ctx["session_id"], acct, group_id, L._ts(at), trigger,
            recorded, (pgate["refusal"] if not pgate["complete"]
                       and cont is None else sel.get("refusal")),
            json.dumps(alts, default=str),
            json.dumps(dict({k: sel.get(k) for k in (
                "selected", "refusal", "selection_reason",
                "margin_over_runner_up", "decision_policy", "tie_break",
                "hold_is_priced", "limits_applied")},
                management_policy=mpol,
                mechanical_selection=chosen,
                management_packet=dict(packet, gate=pgate),
                recommendation_state=XF.write_state(
                    evidence_state=measure["evidence_state"],
                    recommendation=recorded),
                valuation=valuation), default=str),
            json.dumps(exposure, default=str),
            json.dumps({"live_orders": [L.order_view(s) for s in standing],
                        "protective_price": prot,
                        "invariant": SPO.WHY_ONE_LIVE_ORDER},
                       default=str),
            json.dumps({"filled_protection_qty": float(confirmed),
                        "basis": "simulated fills of the standing sale",
                        # the column's name predates the lanes: this is
                        # PAPER (SIMULATOR) fills of THIS position, never a
                        # venue confirmation
                        "scope": "POSITION",
                        "position_key": pos.get("position_key"),
                        "execution_environment": "PAPER_SIMULATED"},
                       default=str),
            json.dumps(alts["incomplete_search"], default=str),
            json.dumps(exceptional), json.dumps(measure, default=str),
            json.dumps(action, default=str), pos.get("strategy")
            or L.DEFAULT_STRATEGY)
        # THE MANAGEMENT ASSESSMENT (migration 206): latency against the
        # bound, thesis state, every alternative incl. the SHADOW
        # REALLOCATE, the policy record. Record only -- the action above
        # is already decided and nothing here changes it.
        mg = await XM.paper_review_hook(
            conn, ctx, group_id=group_id, pos=pos, review_id=rid,
            trigger=trigger, at=at, measure=measure, alts=alts,
            recommendation=(chosen if rankable else recorded),
            exit_levels=exit_lv, policy=mpol,
            due_at=due_at, precomputed=realloc)
        if not pgate["complete"] and cont is None:
            # THE REFUSAL, RECORDED (migration 270) with every missing
            # element -- never a silent HOLD on stale data.
            from .. import bettor_paper_freshness as PMF
            await PMF.record_refusal(
                conn, account_id=acct, kind=PMF.K_PACKET,
                refusal=pgate["refusal"], at=at,
                strategy=pos.get("strategy"), group_id=group_id,
                position_key=pos["position_key"],
                us_market_slug=pos["us_market_slug"], review_id=rid,
                missing=pgate["missing"], detail=packet)
        # FRESHNESS EXPIRY IS A REVIEW TRIGGER: a fresh probability expires
        # at its own source stamp + the limit; a review is scheduled for
        # that instant (paper_runtime.schedule_expiry_review via the
        # runtime's ctx hook: one timer per market, bounded) and the pass's
        # own _trigger also fires FRESHNESS_EXPIRY on the next pass.
        if sched is not None and measure["evidence_state"] == E_FRESH \
                and valuation.get("expires_at") is not None:
            try:
                sched(pos["us_market_slug"], float(valuation["expires_at"]))
            except Exception:                                   # noqa: BLE001
                pass
        # FRESH-EVIDENCE WORK (owner R30, migration 226): a review that
        # could not decide on fresh evidence enqueues the acquisitions it
        # needs (probability, venue book, game state, re-review); a later
        # review closes what it satisfies. Never raises.
        from . import work_queue as WQ
        await WQ.after_review(conn, ctx, group_id=group_id, pos=pos,
                              review_id=rid, recommendation=recorded,
                              measure=measure, at=at)
        reviews.append({"review_id": rid, "position": pos["position_key"],
                        "recommendation": recorded,
                        "mechanical_selection": chosen,
                        "packet_complete": pgate["complete"],
                        "packet_missing": pgate["missing"],
                        "recommendation_state": mg.get(
                            "recommendation_state"),
                        "valuation_expires_at": valuation.get("expires_at"),
                        "trigger": trigger,
                        "action": action.get("taken"),
                        "thesis_state": mg.get("thesis_state"),
                        "review_latency_s": mg.get("review_latency_s"),
                        "within_bound": mg.get("within_bound")})
    return {"group_id": group_id, "reviews": reviews}


def exposure_view(pos: dict, *, resting_qty: float,
                  filled_protection_qty: float) -> dict:
    """THE REVIEW'S EXPOSURE RECORD (pure). ONLY FILLED QUANTITY COUNTS AS
    PROTECTION (owner rule): a RESTING protective sale is not protection
    until it fills. A filled protective sale has already left `open_qty`
    (open = bought - sold - settled), so none of the quantity still held is
    protected: unmatched = open - filled protection still held against it =
    open. The resting quantity is reported apart (`standing_qty`), never
    subtracted. Before this, unmatched was open - resting, which counted a
    resting order as matched protection."""
    q = float(pos["open_qty"])
    return {"open_qty": pos["open_qty"],
            "cost_basis_usd": pos["cost_basis_usd"],
            "max_loss_usd": pos["cost_basis_usd"],
            "standing_qty": round(float(resting_qty), 6),
            "resting_protection_qty": round(float(resting_qty), 6),
            "resting_is_protection": False,
            "filled_protection_qty": round(float(filled_protection_qty), 6),
            "filled_protection_held_against_open_qty": 0.0,
            "unmatched_inventory_qty": round(q, 6),
            "unmatched_basis": ("open - filled protection held against it "
                                "(0: a filled protective sale has already "
                                "left open_qty); resting protection is NOT "
                                "protection until it fills"),
            "remaining_exposure_usd": pos["cost_basis_usd"],
            "floors_are_not_realized_pnl": True}


async def _submit_sale(conn, ctx, *, pos, role, qty, limit, wire,
                       review_key) -> dict:
    if not qty or limit is None:
        return {"taken": "NONE", "why": "NO_WALKABLE_SALE"}
    sim = ctx["config"]["simulator"]
    at = _clock(ctx)
    o = {"idempotency_key": "paper:%s:%s" % (role, _h(review_key)),
         "account_id": ctx["account_id"], "session_id": ctx["session_id"],
         "group_id": pos["group_id"], "role": role, "direction": "SELL",
         "holding_side": pos["holding_side"],
         "intent": SIM.intent_of("SELL", pos["holding_side"]),
         "us_market_slug": pos["us_market_slug"], "fixture": pos["fixture"],
         "label": pos.get("label") or {}, "order_type": "MARKETABLE",
         "time_in_force": "IOC", "allow_partial": True, "qty": qty,
         "limit_price": limit, "wire_price": wire, "decision_id": None,
         "decided_at": at,
         "eligible_at": at + float(sim["decision_to_execution_delay_s"]),
         "expires_at": at + float(sim["marketable_ttl_s"]),
         "simulator_version": ctx["config"]["simulator_version"]}
    o["strategy"] = await _strategy(conn, pos["group_id"])
    got = await L.submit_order(conn, o, caps=ctx["config"]["risk"],
                               fee_fn=ctx.get("fee_fn"), now=at)
    return {"taken": "SUBMIT_%s" % role, "ok": got.get("ok"),
            "refusal": got.get("refusal"),
            "order_id": (got.get("order") or {}).get("order_id"),
            "requested": {k: o.get(k) for k in (
                "qty", "limit_price", "wire_price", "time_in_force",
                "order_type", "intent")}}


#: no cent recovers cost + sale fees + buffer: nothing can be placed, and an
#: order already resting is never cancelled for want of a price to compare
R_NO_PROTECTIVE_PRICE = "NO_PROTECTIVE_PRICE"


async def _committed_sale_qty(conn, account_id: str, pos: dict) -> float:
    """What a pending EXIT / REDUCE of this position already commits (its
    unfilled remainder): the standing protection covers the rest only."""
    return float(await conn.fetchval(
        "SELECT coalesce(sum(qty - filled_qty), 0) FROM paper_orders "
        " WHERE account_id=$1 AND group_id=$2 AND us_market_slug=$3 "
        "   AND holding_side=$4 AND direction='SELL' "
        "   AND role <> 'STANDING_PROTECTION' AND state = ANY($5::text[])",
        account_id, pos["group_id"], pos["us_market_slug"],
        pos["holding_side"], list(L.OPEN_STATES)) or 0)


async def _maintain_standing(conn, ctx, *, pos, standing, prot, md, at,
                             SPO) -> dict:
    """ONE live-or-potentially-live protective sale per group position.

    ITS TARGET is the position less what a pending EXIT / REDUCE already
    commits (the sale and the protection never overlap; a protection that
    covers exactly the uncommitted remainder is kept, not churned). Without
    a protective price (`prot` not ok) nothing is placed and a resting
    order is kept -- the reason is recorded, never a KeyError that leaves
    the review unwritten and an EXIT intent open."""
    priced = bool((prot or {}).get("ok")) and \
        (prot or {}).get("price") is not None
    want_qty = float(pos["open_qty"]) - await _committed_sale_qty(
        conn, ctx["account_id"], pos)
    live = [s for s in standing if s["state"] != "CANCEL_PENDING"]
    pending = [s for s in standing if s["state"] == "CANCEL_PENDING"]
    if not live and not priced:
        return {"taken": "NONE",
                "why": (prot or {}).get("refusal") or R_NO_PROTECTIVE_PRICE}
    if live:
        s = live[0]
        # THE DETERMINISTIC LIFECYCLE: GTD expiry -> terminal confirmation
        # (the simulator's next step marks it EXPIRED and releases it) ->
        # replacement on the next feasible pass. An order past its expiry
        # is neither kept as protection nor replaced alongside -- it is
        # still potentially live until terminal.
        exp = s.get("expires_at")
        exp = None if exp is None else (float(exp) if isinstance(
            exp, (int, float)) else L._epoch(exp))
        if exp is not None and float(at) >= exp:
            return {"taken": "WAIT_FOR_TERMINAL_EXPIRY",
                    "order_id": s["order_id"], "expired_at": exp,
                    "why": ("GTD expiry -> terminal confirmation -> "
                            "replacement on the next feasible pass")}
        if not priced:
            # no price to compare with: the resting protection is KEPT --
            # never cancelled merely because a replacement cannot be priced
            return {"taken": "KEEP_STANDING", "order_id": s["order_id"],
                    "why": (prot or {}).get("refusal")
                    or R_NO_PROTECTIVE_PRICE}
        remaining = float(s["qty"]) - float(s["filled_qty"])
        if abs(remaining - want_qty) < 1e-9 and \
                abs(float(s["limit_price"]) - prot["price"]) < 1e-9:
            return {"taken": "KEEP_STANDING", "order_id": s["order_id"]}
        await SIM.request_cancel(conn, s["order_id"], now=at,
                                 reason="POSITION_OR_PROTECTIVE_PRICE_CHANGED")
        return {"taken": "CANCEL_FOR_REPLACEMENT", "order_id": s["order_id"],
                "why": ("cancel -> confirmed terminal -> replacement on a "
                        "later pass; no second live order meanwhile")}
    gate = SPO.second_live_hedge_permitted(
        live_or_potentially_live=len(standing))
    if not gate["permitted"]:
        return {"taken": "WAIT_FOR_TERMINAL", "gate": gate,
                "orders": [s["order_id"] for s in pending]}
    held = await L.held_uncommitted(conn, ctx["account_id"],
                                    group_id=pos["group_id"],
                                    slug=pos["us_market_slug"],
                                    holding_side=pos["holding_side"])
    # THE WHOLE HELD QUANTITY, at the ledger's own grain (numeric(18,6),
    # rounded DOWN so it never exceeds what is held). Entries fill
    # fractional book sizes (1454.12, 0.39); a whole-contract floor left a
    # sub-contract remainder that could never be protected, kept the
    # position open forever and churned cancel/replace (remaining 1454 vs
    # open 1454.12 never matched).
    qty = float(L.D(held).quantize(PROTECTION_QTY_GRAIN, rounding=ROUND_DOWN))
    if not L.is_open(qty):
        return {"taken": "NONE", "why": "NO_UNCOMMITTED_INVENTORY"}
    q = await SIM.queue_ahead_at_placement(
        conn, slug=pos["us_market_slug"], direction="SELL",
        holding_side=pos["holding_side"], limit=prot["price"],
        market_data=md, account_id=ctx["account_id"])
    wire = (prot["price"] if pos["holding_side"] == "LONG"
            else round(1.0 - prot["price"], 6))
    last_obs = await conn.fetchval(
        "SELECT max(obs_id) FROM paper_book_observations "
        " WHERE us_market_slug=$1", pos["us_market_slug"])
    sim = ctx["config"]["simulator"]
    o = {"idempotency_key": "paper:STANDING:%s" % _h(
            pos["position_key"], qty, prot["price"], at),
         "account_id": ctx["account_id"], "session_id": ctx["session_id"],
         "group_id": pos["group_id"], "role": "STANDING_PROTECTION",
         "direction": "SELL", "holding_side": pos["holding_side"],
         "intent": SIM.intent_of("SELL", pos["holding_side"]),
         "us_market_slug": pos["us_market_slug"], "fixture": pos["fixture"],
         "label": pos.get("label") or {}, "order_type": "RESTING",
         "time_in_force": "GTD", "allow_partial": True, "qty": qty,
         "limit_price": prot["price"], "wire_price": wire,
         "decision_id": None, "decided_at": at, "eligible_at": at,
         "expires_at": at + float(sim["resting_gtd_s"]),
         "queue_ahead_qty": q["queue_ahead_qty"],
         "queue_basis": dict(q, placement_obs_id=last_obs or 0),
         "simulator_version": ctx["config"]["simulator_version"]}
    o["strategy"] = await _strategy(conn, pos["group_id"])
    try:
        got = await L.submit_order(conn, o, now=at)
    except Exception as exc:                                    # noqa: BLE001
        # the partial unique index: a racing second placement is refused
        return {"taken": "PLACEMENT_REFUSED", "why": type(exc).__name__,
                "gate": "ONE_LIVE_STANDING_ORDER_PER_GROUP_INDEX"}
    return {"taken": "PLACE_STANDING", "ok": got.get("ok"),
            "refusal": got.get("refusal"),
            "order_id": (got.get("order") or {}).get("order_id"),
            "protective_price": prot["price"],
            "floor_if_filled_usd": prot.get("floor_usd"),
            "floor_is": prot.get("floor_is")}


#: THE TRIGGERS' PRIORITY: a position never reviewed goes first, then one
#: with a new fill, a market event, the scheduled backstop.
TRIGGER_PRIORITY = {T_FIRST: 0, T_FILL: 1, T_EXIT_INTENT: 1.1, T_ORDER: 1.2,
                    T_VALUATION: 1.4, T_GAME: 1.6, T_EXPIRY: 1.8, T_MARKET: 2, T_BACKSTOP: 3}
#: a held market that just moved on the feed is reviewed right after the
#: first reviews: its fresh probability lasts only the 30 s limit
FEED_CHANGE_PRIORITY = 0.5
#: XAVIER'S RESERVED BUDGET (s): the pass budget is shared with the steps
#: that run first (books, simulation, every entry strategy); when they
#: spend it, Xavier still reviews the due positions -- most urgent first --
#: for at least this long instead of reviewing nothing.
RESERVED_BUDGET_S = 10.0


def _due_at(trig: str, *, first_fill_at, last: dict | None, last_fill_at,
            book_at, backstop_s: float):
    """When a review with this trigger became due (the latency's origin)."""
    if trig == T_FIRST:
        return first_fill_at
    if trig == T_FILL:
        return last_fill_at
    if trig == T_MARKET:
        return book_at
    return None if last is None else last["reviewed_at"] + backstop_s


async def _requeue_evidence(conn, ctx: dict, groups: list, *,
                            at: float) -> dict:
    """WHAT CHANGED SINCE EACH GROUP'S LAST REVIEW, in three batched reads
    per pass (never per group): the latest terminal instant of a management
    order (protection cancelled / expired / filled), the latest stored
    valuation of the group's own contract (xavier_management.
    LATEST_VALUATION_SQL, the measure's identity, within the measure's
    lookback) and the event start of a pre-event thesis. A failed read
    leaves that trigger out (the backstop and the expiry still run)."""
    from . import xavier_management as XM
    out: dict = {g: {} for g in groups}
    if not groups:
        return out
    try:
        for r in await conn.fetch(
                "SELECT group_id, max(terminal_at) AS t FROM paper_orders "
                " WHERE account_id = $1 AND group_id = ANY($2::text[]) "
                "   AND role <> 'ENTRY' AND terminal_at IS NOT NULL "
                " GROUP BY group_id", ctx["account_id"], groups):
            out[r["group_id"]]["order_event_at"] = L._epoch(r["t"])
    except Exception:                                           # noqa: BLE001
        pass
    try:
        lb = float(ctx["config"]["entry"].get("valuation_lookback_s")
                   or XM.CONTEXT_VALUATION_LOOKBACK_S)
        for r in await conn.fetch(XM.LATEST_VALUATION_SQL, groups, at - lb):
            out[r["group_id"]]["valuation_at"] = L._epoch(r["observed_at"])
    except Exception:                                           # noqa: BLE001
        pass
    try:
        if await XM.has_schema(conn):
            for r in await conn.fetch(
                    "SELECT group_id, event_start_at FROM xavier_entry_theses"
                    " WHERE position_kind = 'PAPER' "
                    "   AND group_id = ANY($1::text[]) "
                    "   AND thesis_expires_at IS NOT NULL", groups):
                out[r["group_id"]]["event_start_at"] = L._epoch(
                    r["event_start_at"])
    except Exception:                                           # noqa: BLE001
        pass
    return out


async def step(conn, ctx: dict, *, only_groups=None) -> dict:
    """REVIEW EVERY HELD GROUP THAT IS DUE (first fill, fill event, market
    event or the scheduled backstop): the due list is built first and
    reviewed most urgent first (FIRST_FILL, FILL_EVENT, MARKET_EVENT,
    SCHEDULED_BACKSTOP; then the longest overdue), within the pass budget
    or Xavier's reserved budget, whichever is later. Due groups left over
    are listed (`deferred`) with how long they have waited."""
    acct = ctx["account_id"]
    at = _clock(ctx)
    cad = ctx["config"]["cadence"]
    backstop = float(cad.get("xavier_backstop_s", 60.0))
    reserve = float(cad.get("xavier_reserved_budget_s", RESERVED_BUDGET_S))
    deadline = max(float(ctx["deadline"]), time.monotonic() + reserve)
    from .. import pinnapi_held as PH
    groups = sorted({p["group_id"] for p in await L.positions(conn, acct)})
    if only_groups is not None:
        groups = [g for g in groups if g in set(only_groups)]
    handed = {r["group_id"]: L._epoch(r["first_fill_at"]) for r in
              await conn.fetch("SELECT group_id, first_fill_at FROM "
                               " paper_handoffs WHERE account_id=$1", acct)}
    out = {"reviews": 0, "groups_held": len(groups), "by_trigger": {},
           "not_handed_off": sorted(set(groups) - set(handed)),
           "due": 0, "deferred": [], "first_review_latency_s": []}
    ev = await _requeue_evidence(conn, ctx, [g for g in groups
                                             if g in handed], at=at)
    # EXIT INTENTS: one whose position closed is resolved; the rest give
    # their deadline to the trigger
    held_keys = [(p["group_id"], p["position_key"])
                 for p in await L.positions(conn, acct)]
    out["exit_intents_closed"] = await XI.close_orphans(
        conn, acct, held_keys, at=at)
    xdue = await XI.due_at_by_group(conn, acct, groups)
    due = []
    for g in groups:
        if g not in handed:
            continue
        last = await conn.fetchrow(
            "SELECT reviewed_at, measure FROM paper_xavier_reviews "
            " WHERE group_id=$1 ORDER BY reviewed_at DESC LIMIT 1", g)
        lastd = None if last is None else {
            "reviewed_at": L._epoch(last["reviewed_at"]),
            "measure": L._j(last["measure"]) or {}}
        lf = L._epoch(await conn.fetchval(
            "SELECT max(filled_at) FROM paper_fills WHERE group_id=$1", g))
        slug = await conn.fetchval(
            "SELECT us_market_slug FROM paper_orders WHERE group_id=$1 "
            "   AND role='ENTRY' LIMIT 1", g)
        book = None if slug is None else await _latest_book(conn, slug)
        best_exit = None
        if book is not None and not book["error"]:
            side = await conn.fetchval(
                "SELECT holding_side FROM paper_orders WHERE group_id=$1 "
                "   AND role='ENTRY' LIMIT 1", g)
            lv = SIM.levels_for({"bids": L._j(book["bids"]) or [],
                                 "offers": L._j(book["offers"]) or []},
                                direction="SELL", holding_side=side)
            best_exit = lv["levels"][0]["price"] if lv["levels"] else None
        book_at = None if book is None else L._epoch(book["observed_at"])
        # the held market's provider time: its last change or its latest
        # provider-stamped confirmation (the held read admits either within
        # the same 30 s) -- a fresh frame triggers the review at once
        fc = None if slug is None else PH.fresh_at(slug)
        gev = ev.get(g) or {}
        trig = _trigger(group=g, new_handoffs=ctx.get("new_handoffs") or [],
                        last=lastd, last_fill_at=lf, book_at=book_at,
                        best_exit=best_exit, at=at, backstop_s=backstop,
                        feed_change_at=fc,
                        order_event_at=gev.get("order_event_at"),
                        valuation_at=gev.get("valuation_at"),
                        event_start_at=gev.get("event_start_at"),
                        exit_intent_due_at=xdue.get(g))
        if trig is None:
            continue
        by_feed = (trig == T_MARKET and fc is not None and lastd is not None
                   and fc > lastd["reviewed_at"])
        d_at = (fc if by_feed else
                gev.get("order_event_at") if trig == T_ORDER else
                xdue.get(g) if trig == T_EXIT_INTENT else
                gev.get("valuation_at") if trig == T_VALUATION else
                gev.get("event_start_at") if trig == T_GAME else
                last_evidence_expiry(lastd) if trig == T_EXPIRY else
                _due_at(trig, first_fill_at=handed[g], last=lastd,
                        last_fill_at=lf, book_at=book_at,
                        backstop_s=backstop))
        due.append((FEED_CHANGE_PRIORITY if by_feed else
                    TRIGGER_PRIORITY[trig],
                    d_at if d_at is not None else at, g, trig, d_at))
    due.sort()
    out["due"] = len(due)
    for i, (_, _, g, trig, d_at) in enumerate(due):
        if time.monotonic() > deadline:
            out["budget_exhausted"] = True
            out["deferred"] = [
                {"group_id": dg, "trigger": dt_,
                 "waiting_s": None if da is None else round(at - da, 3)}
                for (_, _, dg, dt_, da) in due[i:]]
            break
        # ONE GROUP'S FAILED REVIEW NEVER STARVES THE REST: before this a
        # review that raised (e.g. the feed-provenance TypeError fixed in
        # paper_benchmark.xavier_measure) aborted the whole step, so every
        # group due after it -- held markets that had just moved first of
        # all (FEED_CHANGE_PRIORITY) -- went unreviewed in that pass. The
        # failure is named per group; nothing is assumed reviewed.
        try:
            got = await review_group(conn, ctx, g, trigger=trig, due_at=d_at)
        except asyncio.CancelledError:
            raise
        except Exception as exc:                                # noqa: BLE001
            out.setdefault("review_errors", []).append(
                {"group_id": g, "trigger": trig,
                 "error": "%s: %s" % (type(exc).__name__, str(exc)[:160])})
            continue
        out["reviews"] += len(got["reviews"])
        out["by_trigger"][trig] = out["by_trigger"].get(trig, 0) + 1
        if trig == T_FIRST and d_at is not None:
            out["first_review_latency_s"].append(round(at - d_at, 3))
    return out


# ═════════════════════════════════════════════════════════════════════
# SETTLEMENT FROM AUTHORITATIVE EVIDENCE
# ═════════════════════════════════════════════════════════════════════

#: THE VENUE'S OWN NON-BINARY SETTLEMENT, as the outcome join recorded it
#: (`settlement_read`, left unjoined: neither side was paid in full).
VENUE_PRICE_SQL = """
    SELECT id, buy_intent, settlement_read, settlement_read_at,
           settlement_comparison->>'venue_rules_text' AS rules
      FROM external_valuations
     WHERE us_market_slug = $1 AND outcome_basis IS NULL
       AND settlement_read IS NOT NULL AND settlement_read_at IS NOT NULL
     ORDER BY id
"""


def venue_price_settlement(rows: list, *, holding_side: str) -> dict:
    """THE VENUE'S PUBLISHED PRICE FOR A CONTRACT IT SETTLED AT A PRICE.
    Pure. Established only when (1) the venue's recorded settlement for the
    contract is a price strictly between 0 and 1, (2) every read agrees,
    and (3) the contract's OWN rules text states a price settlement (the
    last fair market price). The long side is paid that price per contract,
    the short side its complement. Otherwise: no payout, the position stays
    open and pending -- a refund is never assumed."""
    from .. import bettor_settlement_terms as ST
    from .. import bettor_nfl_settlement as NFL
    prices, ev, stated, tie_stated = set(), [], False, False
    for r in rows:
        try:
            sp = float(str(r.get("settlement_read")).strip())
        except (TypeError, ValueError):
            continue
        if not (0.0 < sp < 1.0):
            continue
        prices.add(round(sp, 9))
        ev.append({"valuation_id": r.get("id"),
                   "settlement_read": r.get("settlement_read"),
                   "settlement_read_at": L._epoch(r.get("settlement_read_at"))})
        terms = ST.read_terms(r.get("rules") or "").get("terms") or {}
        if ST.PAY_LAST_FAIR_MARKET_PRICE in terms.values():
            stated = True
        # R30A: THE NFL TIE. "If the game ends in a tie, the market will
        # settle to $0.50." is a stated price settlement of an ORDINARILY
        # COMPLETED game, read from the contract's own text.
        if NFL.venue_tie_payout(r.get("rules")).get("payout") == 0.5:
            tie_stated = True
    if not ev:
        return {"price": None, "why": "NO_VENUE_PRICE_SETTLEMENT_RECORDED"}
    if len(prices) > 1:
        return {"price": None, "why": "CONFLICTING_VENUE_SETTLEMENT_PRICES",
                "evidence": ev}
    tie_price = tie_stated and prices == {0.5}
    if not stated and not tie_price:
        return {"price": None, "evidence": ev,
                "why": ("VENUE_SETTLED_AT_A_PRICE_BUT_THE_CONTRACT_TEXT_HELD_"
                        "STATES_NO_PRICE_SETTLEMENT")}
    long_px = prices.pop()
    per = long_px if holding_side == "LONG" else round(1.0 - long_px, 9)
    if tie_price:
        # WHICH STATE PAID 0.50 IS RECORDED ONLY AS FAR AS IT IS KNOWN (R30A
        # review). The venue's settlement read is a PRICE, not a score. When
        # the contract's text states BOTH the tie settlement and the
        # last-fair-market-price clause (every captured NFL listing does), a
        # 0.50 can be an ordinary tied game OR a postponed / suspended game
        # whose last fair price was 0.50 -- an EXCEPTIONAL state. Writing
        # TIE_AFTER_OVERTIME / ORDINARY there would state an unverified fact
        # and move an exceptional settlement into the ordinary class, which
        # undercounts the exceptional risk that is measured apart. No outcome
        # source read here reports a final tied score, so the state is named
        # as not distinguished. Only when the text states the tie settlement
        # and NO other price settlement is 0.50 the tie by the contract's own
        # terms. The payout is the venue's published price either way.
        if stated:
            state, cls = NFL.S_TIE_OR_LAST_FAIR_PRICE, NFL.AMBIGUOUS
            why = ("the contract states both a $0.50 tie settlement and a "
                   "last-fair-market-price settlement; the venue's read is a "
                   "price only, and no final score is read here, so which "
                   "state paid is not established")
        else:
            state, cls = "TIE_AFTER_OVERTIME", NFL.ORDINARY
            why = ("the contract's only stated price settlement is the $0.50 "
                   "tie settlement")
        return {"price": per, "venue_long_price": long_px, "evidence": ev,
                "rule": ("the contract's stated $0.50 settlement: each side "
                         "is paid 0.50 per contract"),
                "settlement_state": state, "state_class": cls,
                "state_basis": why,
                "would_distinguish": ("a final score from an outcome source "
                                      "(tied after overtime -> "
                                      "TIE_AFTER_OVERTIME, ordinary; game not "
                                      "completed -> exceptional)"),
                "quote": NFL.Q_VENUE_TIE}
    return {"price": per, "venue_long_price": long_px, "evidence": ev,
            "rule": "the contract's stated last-fair-market-price settlement"}


def outcome_for(rows: list, *, holding_side: str) -> dict:
    """Our side's settlement from the venue-joined valuation rows. Pure."""
    ours = DP.LONG if holding_side == "LONG" else DP.SHORT
    seen = set()
    ev = []
    for r in rows:
        basis = r.get("outcome_basis")
        if basis == VOID_BASIS:
            seen.add("VOID_REFUND")
        elif basis in LABEL_BASES and r.get("outcome_known") and \
                r.get("outcome") in (0, 1):
            won = (int(r["outcome"]) == 1) == (r.get("buy_intent") == ours)
            seen.add("WON" if won else "LOST")
        else:
            continue
        ev.append({"valuation_id": r.get("id"), "outcome": r.get("outcome"),
                   "outcome_basis": basis, "buy_intent": r.get("buy_intent"),
                   "outcome_at": L._epoch(r.get("outcome_at"))})
    if not ev:
        return {"outcome": None, "why": "NO_AUTHORITATIVE_SETTLEMENT_YET"}
    if len(seen) > 1:
        return {"outcome": None, "why": "CONFLICTING_SETTLEMENT_EVIDENCE",
                "evidence": ev}
    return {"outcome": seen.pop(), "evidence": ev}


async def step_settle(conn, ctx: dict) -> dict:
    """Reconcile selected and archived epochs without moving their cash."""
    from ..simulated_account_context import account_lineage
    from .. import bettor_paper_session as S
    out = {"settled": 0, "corrected": 0, "conflicts": 0, "waiting": 0}
    accounts = {}
    for aid in await account_lineage(conn, ctx["account_id"]):
        session = ctx if aid == ctx['account_id'] else await S.active_session(conn, aid)
        if not session:
            raise ValueError("PAPER_ARCHIVE_SESSION_UNAVAILABLE:" + aid)
        got = await _step_settle_account(conn, dict(ctx, account_id=aid,
                                                   session_id=session["session_id"]))
        accounts[aid] = got
        for key in out:
            out[key] += got.get(key, 0)
    return dict(out, accounts=accounts)


async def _step_settle_account(conn, ctx: dict) -> dict:
    acct = ctx["account_id"]
    at = _clock(ctx)
    out = {"settled": 0, "corrected": 0, "conflicts": 0, "waiting": 0}
    try:
        has = await conn.fetchval(
            "SELECT count(*) FROM information_schema.columns WHERE "
            " table_name='external_valuations' AND column_name='outcome_basis'")
    except Exception:                                           # noqa: BLE001
        has = 0
    if not has:
        return dict(out, refusal="OUTCOME_EVIDENCE_COLUMNS_ABSENT")
    allpos = await L.positions(conn, acct, include_closed=True)
    for p in allpos:
        rows = [dict(r) for r in await conn.fetch(
            "SELECT id, buy_intent, outcome, outcome_known, outcome_basis, "
            "       outcome_at FROM external_valuations "
            " WHERE us_market_slug=$1 AND outcome_basis IS NOT NULL "
            " ORDER BY id", p["us_market_slug"])]
        got = outcome_for(rows, holding_side=p["holding_side"])
        key = "venue-final:%s" % p["us_market_slug"]
        vp = None
        if got["outcome"] is None and got.get("why") == \
                "NO_AUTHORITATIVE_SETTLEMENT_YET" and p["open_qty"] > 1e-9 \
                and p.get("settlement") is None:
            # THE COMPLETED-GAME POLICY'S EXCEPTIONAL SETTLEMENT: paid at
            # the venue's own published price, never an assumed refund.
            from . import paper_benchmark as PB
            if await PB.group_strategy(conn, p["group_id"]) in \
                    PB.COMPLETED_GAME_STRATEGIES:
                vrows = [dict(r) for r in await conn.fetch(
                    VENUE_PRICE_SQL, p["us_market_slug"])]
                vp = venue_price_settlement(vrows,
                                            holding_side=p["holding_side"])
                if vp.get("price") is not None:
                    r = await L.settle(
                        conn, account_id=acct, group_id=p["group_id"],
                        slug=p["us_market_slug"],
                        holding_side=p["holding_side"],
                        settlement_event_key=key,
                        outcome="SETTLED_AT_VENUE_PRICE",
                        evidence=dict(vp, policy=(PB.policy_for(
                            await PB.group_strategy(conn, p["group_id"]))
                            or PB.CG_POLICY)["version"]),
                        evidence_source="external_valuations."
                                        "settlement_read",
                        at=at, session_id=ctx["session_id"],
                        price_per_contract=vp["price"])
                    out["settled"] += 1 if r.get("ok") and not r.get(
                        "duplicate") else 0
                    out.setdefault("settled_at_venue_price", 0)
                    out["settled_at_venue_price"] += 1
                    continue
                out.setdefault("pending_reasons", {})
                out["pending_reasons"][vp.get("why")] = \
                    out["pending_reasons"].get(vp.get("why"), 0) + 1
        if got["outcome"] is None:
            if got["why"] == "CONFLICTING_SETTLEMENT_EVIDENCE":
                out["conflicts"] += 1
                from .paper_derek import _finding
                await _finding(conn, ctx, kind="CONFLICTING_SETTLEMENT_"
                               "EVIDENCE", subject=p["position_key"],
                               detail=got, severity="WARNING")
            elif p["open_qty"] > 1e-9:
                out["waiting"] += 1
            continue
        evidence = {"rows": got["evidence"], "rule": "unanimous venue-joined "
                    "outcome rows for this contract"}
        if p["open_qty"] > 1e-9 and p.get("settlement") is None:
            r = await L.settle(conn, account_id=acct, group_id=p["group_id"],
                               slug=p["us_market_slug"],
                               holding_side=p["holding_side"],
                               settlement_event_key=key,
                               outcome=got["outcome"], evidence=evidence,
                               evidence_source="external_valuations."
                               "outcome_basis", at=at,
                               session_id=ctx["session_id"])
            out["settled"] += 1 if r.get("ok") and not r.get(
                "duplicate") else 0
        elif p.get("settlement") and p["settlement"]["outcome"] != \
                got["outcome"]:
            r = await L.correct_settlement(
                conn, account_id=acct, group_id=p["group_id"],
                slug=p["us_market_slug"], holding_side=p["holding_side"],
                settlement_event_key=key, outcome=got["outcome"],
                evidence=evidence, evidence_source="external_valuations."
                "outcome_basis", at=at, session_id=ctx["session_id"])
            out["corrected"] += 1 if r.get("ok") else 0
    # Open orders on a settled market can never fill: release them.
    for o in await conn.fetch(
            "SELECT o.order_id FROM paper_orders o WHERE o.account_id=$1 "
            "   AND o.state = ANY($2::text[]) AND EXISTS (SELECT 1 FROM "
            "   paper_settlements s WHERE s.account_id=o.account_id "
            "   AND s.us_market_slug=o.us_market_slug)", acct,
            list(L.OPEN_STATES)):
        await L.release_remainder(conn, order_id=o["order_id"],
                                  reason="MARKET_SETTLED", at=at,
                                  state="CANCELED")
    return out
