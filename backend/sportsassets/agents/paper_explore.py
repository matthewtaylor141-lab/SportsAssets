"""PINNACLE_EXPLORATION_PAPER -- THE BOUNDED PAPER EXPLORATION STRATEGY.

OWNER-AUTHORIZED 2026-10-01, PAPER ONLY. A SEPARATE training strategy that
generates FORWARD EXPERIENCE now -- simulated fills, Xavier's management,
settlement, Audrey's audit -- by taking small positions that MAY FAIL the
investment policy's 0.5 pp edge or positive-after-fees requirement. The
investment policy (PINNACLE_COMPLETED_GAME_PAPER) and its entry requirements
are unchanged; nothing here is a profitable opportunity, and negative
expected value is an explicit RESEARCH COST, never investment performance.
Positions are labelled "Training / simulated execution".

WHAT IS RELAXED. Only the selection criteria: the edge threshold and the
after-fee expected-profit requirement. Every decision still RECORDS its
estimated gross edge, fee per contract, expected profit after fees (which
may be negative), its selection probability and its training purpose.

WHAT IS NOT RELAXED -- the data and execution safeguards, all of them:
  * correct fixture, participant, outcome, market, line and grading-period
    mapping (`paper_benchmark.completed_game_match`, unweakened);
  * a FRESH Pinnacle reading under the existing 30 s rule, re-aged at the
    decision instant;
  * a CURRENT observed book (<= 10 s, our receipt instant) with displayed
    depth; the price is that book's best level, never the valuation quote;
  * realistic fees (the simulator's schedule, taker side) and the
    simulator's delay, depth walk and conservative partial fills; a stale
    or unreadable book never fills;
  * idempotent accounting on the ONE shared ledger.

THE OWNER'S CAPITAL POLICY (V3): around $1,000 per new entry including
fees, with no aggregate training exposure cap or realized-loss stop. Available
simulated cash is checked under the account lock. Price quality, idempotency
and consumed-liquidity checks continue to apply. In the main account there
is no per-fixture allocation limit; $1,000 is a sizing objective, not a ceiling.

SELECTION, RECORDED. Every eligible main-account valuation is admitted to
selection (probability 1); existing decision/order idempotency still prevents
reprocessing the same observation. Isolated accounts retain the deterministic
sport-balanced sampling method. The method and probability are recorded for
Audrey. Only full-game moneylines have an established mapping; other market
types remain excluded until their mapping is established.

UNREACHABLE FROM REAL MONEY. Imports only the paper ledger, the simulator,
the pure policy helpers and the paper benchmark / Derek helpers.
"""
from __future__ import annotations

from .. import bettor_paper_limits as LIMITS

import hashlib
import json
import math
import time
from typing import Any

from .. import bettor_capital_authority as CA
from .. import bettor_paper_ledger as L
from .. import decision_hooks as DH
from .. import bettor_paper_simulator as SIM
from . import derek_policy as DP
from . import paper_benchmark as PB
from . import paper_derek as PD

POL = PB.EXPLORE_POLICY
STRATEGY = PB.EXPLORE_STRATEGY
VERSION = PB.EXPLORE_VERSION
DISCLOSURE = PB.EXPLORE_DISCLOSURE
LABEL = PB.EXPLORE_LABEL

#: Training V3: owner removed aggregate exposure and realized-loss limits.
#: Entries remain $1,000 including fees and spend only available simulated cash.
TARGET_ENTRY_COST_USD = 1000.0
MAX_ENTRY_COST_USD = None
MAX_AGGREGATE_EXPOSURE_USD = None
LOSS_STOP_USD = None

#: THE SAMPLING METHOD.
SELECTION_METHOD = "HASHED_BERNOULLI_INVERSE_SPORT_FREQUENCY_V2"
# Preserve the original fixture draw so broader inclusion never reshuffles
# previously eligible fixtures out of the sample.
SAMPLING_DRAW_VERSION = "PINNACLE_EXPLORATION_PAPER_V1"
SAMPLING_TARGET_PER_SPORT = 12.0      # K; was 6
SAMPLING_FLOOR = 0.70                # was .35
SAMPLING_WINDOW_S = 6 * 3600.0

ECONOMICS_LABEL = "EXPLORATION_RESEARCH_COST_NOT_INVESTMENT_PERFORMANCE"
TRAINING_PURPOSE = (
    "FORWARD_EXPERIENCE: exercise the complete paper chain on live data -- "
    "entry at the observed best level, simulated fill (delay, depth walk, "
    "partials, fees), Xavier's handoff, protection and exit decisions, "
    "settlement and Audrey's audit and ledger reconciliation -- and measure "
    "fill realism, fee drag and the Pinnacle probability against settled "
    "outcomes across sports. Not an investment decision.")

R_LOSS_STOP = "EXPLORATION_REALIZED_LOSS_STOP_REACHED"
R_AGGREGATE = "EXPLORATION_AGGREGATE_EXPOSURE_LIMIT"
R_FIXTURE_TAKEN = "EXPLORATION_ALREADY_HOLDS_THIS_FIXTURE"
R_NOT_SAMPLED = "FIXTURE_NOT_SELECTED_BY_THE_EXPLORATION_SAMPLE"
R_TOO_DEAR = "ONE_CONTRACT_EXCEEDS_THE_EXPLORATION_ENTRY_BUDGET"
R_LIMITS_UNREADABLE = "EXPLORATION_LIMITS_UNREADABLE"

# ── NO ENTRY XAVIER CANNOT MANAGE (capital-readiness xavier_complete RED,
# production 2026-10-08 02:10Z, release 08828d04) ─────────────────────────
#
# Exploration admitted positions Xavier's management path could never make
# complete, and every one of them then held the strategy's management
# integrity -- and the xavier_complete gate -- RED until it settled:
#   * atc-brb-csc-cri-2026-10-08-csc LONG: every held read NO_FEED_EVENT.
#     The entry's probability came from the metered provider (fixture key
#     event:87416e0f..., whole-second stamps) and Xavier's held read
#     (pinnapi_feed_runtime.held_moneyline -> pinnapi_census.contract_match,
#     exact structured venue names; the entry-proven fixture only for a
#     pinnapi:<id> key) resolves no Brazil Serie B fixture at all: no change
#     ever triggers a review and no held read is ever fresh.
#   * atc-idnsl-pke-mau-2026-10-09-pke SHORT: bought at 0.99 for 346.74 /
#     350 contracts. paper_xavier.protective_price has no cent <= 0.99 that
#     recovers cost + sale fees + the buffer, so the standing protection can
#     never be placed: NO_VALID_ACTIVE_PROTECTION for the life of the
#     position, whatever the evidence.
# Both are decidable AT ENTRY with the very code management runs, so the
# entry now asks it: the SAME held reader Xavier's review calls
# (paper_xavier._held_feed, with the identity the entry valuation proves)
# and the SAME protective price. Nothing here reads or moves a freshness,
# risk, settlement or profitability threshold; it only refuses.
R_XAVIER_CANNOT_PRICE = "XAVIER_HELD_READ_CANNOT_PRICE_THIS_CONTRACT"
R_XAVIER_CANNOT_PROTECT = "XAVIER_CANNOT_PROTECT_THIS_ENTRY_NO_PROTECTIVE_PRICE"
#: the held read raised (a DB error inside the read): nothing was proven
R_HELD_READ_FAILED = "XAVIER_HELD_READ_RAISED_AT_ENTRY"


def _held_reasons_that_do_not_refuse() -> frozenset:
    """The held read's refusals that do NOT say the contract is beyond
    Xavier: (a) CURRENCY ONLY -- the read resolved the provider fixture, the
    market and the payout outcome and stopped at the unchanged 30 s rule
    (no observed change / older than the limit / a clock disagreement / a
    previous connection's quote): the same read is fresh the moment the
    provider moves or confirms the price, so Xavier can manage it; (b) NOT
    EVALUATED -- no feed owner / no synced epoch in this process, or the
    bounded read did not complete: nothing about the contract was learned.
    Every other refusal (no feed event, ambiguous, participants not two,
    sport out of scope, venue type not a held family, outcome unmapped,
    the de-vig's own refusal, ...) is the management path saying it cannot
    price this contract."""
    from .. import pinnapi_census as C
    from .. import pinnapi_feed as F
    from .. import pinnapi_feed_runtime as FR
    return frozenset((
        F.R_NO_CHANGE_TIME, F.R_STALE, F.R_FUTURE, F.R_OLD_EPOCH,
        F.R_NO_AUTHORITY, F.R_NOT_SYNCED, C.S_FEED_NOT_SYNCED,
        F.R_LAST_RECORD_UNPARSED, FR.R_HELD_LOOKUP_TIMEOUT,
        FR.R_ON_DEMAND_TIMEOUT, FR.R_CATALOGUE_UNREADABLE,
        R_HELD_READ_FAILED))


async def xavier_can_price(conn, *, cand: dict, at: float,
                           max_age_s: float) -> dict:
    """WOULD XAVIER'S HELD READ PRICE THIS CONTRACT? The review's own reader
    (paper_xavier._held_feed, read only, bounded to 1 s) with exactly the
    identity paper_benchmark.xavier_measure hands it for a held position:
    the entry valuation's payout outcome and complement, its provider event
    key (the entry-proven fixture) and, for a line, its line. The read runs
    in its own savepoint that is ALWAYS rolled back (it writes nothing), so
    a read the 1 s budget cancelled mid-statement can never leave the
    caller's transaction aborted.

    THE PROVIDER FIXTURE IS THE ONE MANAGEMENT WILL HAND THE READ (RC6
    xavier-records, xavier_held_fixture): the entry valuation's PinnAPI key,
    or -- for a metered event key -- the fixture the PinnAPI matcher
    recorded for this same contract, resolved in its own savepoint before
    the held read's. The entry asks the reader exactly what Xavier's review
    will ask it: no stricter, no looser."""
    from .. import bettor_market_family as MF
    from .. import xavier_held_fixture as XHF
    from . import paper_xavier as PX
    is_line = str(cand.get("market") or "") in MF.LINE_FAMILIES
    fx = await XHF.held_key(conn, us_market_slug=cand.get("us_market_slug"),
                            entry_event_key=cand.get("event_key"), at=at)
    pos = {"us_market_slug": cand.get("us_market_slug"),
           "entry_event_key": fx.get("event_key", cand.get("event_key")),
           "entry_line": cand.get("line") if is_line else None}
    try:
        sp = conn.transaction()
        await sp.start()
        try:
            got = await PX._held_feed(
                conn, pos=pos, payout_event=cand.get("payout_event"),
                payout_is_complement=bool(cand.get("payout_is_complement")),
                at=float(at), max_age_s=float(max_age_s))
        finally:
            await sp.rollback()
    except Exception as exc:                                    # noqa: BLE001
        got = {"ok": False, "reason": R_HELD_READ_FAILED,
               "error": type(exc).__name__}
    got = got or {}
    reason = None if got.get("ok") else str(got.get("reason")
                                            or R_HELD_READ_FAILED)
    can = reason is None or reason in _held_reasons_that_do_not_refuse()
    return {"manageable": can, "held_read_reason": reason,
            "basis": ("HELD_READ_OK" if reason is None else
                      "HELD_READ_REFUSED_ONLY_FOR_CURRENCY_OR_NOT_EVALUATED"
                      if can else "HELD_READ_CANNOT_PRICE_THIS_CONTRACT"),
            "reader": "paper_xavier._held_feed",
            "held_fixture": fx,
            "held_read": {k: got.get(k) for k in (
                "sport_id", "feed_event_id", "market_key", "designation",
                "identity_basis", "error") if got.get(k) is not None}}


def xavier_can_protect(*, econ: dict, fee_fn, at) -> dict:
    """COULD XAVIER PLACE THE STANDING PROTECTION? paper_xavier.
    protective_price -- the price the review places -- for the quantity this
    entry would acquire at the cost basis it would carry (the walked
    acquisition plus its buy fees, as the ledger books a fill). Pure but for
    the fee function."""
    from . import paper_xavier as PX
    qty = float(econ.get("qty") or 0.0)
    cost = float(econ.get("acquisition_cost_usd") or 0.0) + float(
        econ.get("fees_usd") or 0.0)
    got = PX.protective_price(qty=qty, cost_basis=cost, fee_fn=fee_fn, at=at)
    return {"protectable": bool(got.get("ok")), "qty": qty,
            "cost_basis_usd": round(cost, 6),
            "protective_price": got.get("price"),
            "refusal": got.get("refusal"),
            "rule": "paper_xavier.protective_price (unchanged)"}


# ═════════════════════════════════════════════════════════════════════
# THE LIMITS (read on the caller's connection; re-checked under the lock)
# ═════════════════════════════════════════════════════════════════════

async def _groups(conn, account_id: str) -> set:
    return {r["group_id"] for r in await conn.fetch(
        "SELECT DISTINCT group_id FROM paper_orders WHERE account_id=$1 "
        "   AND strategy=$2", account_id, STRATEGY)}


async def limits_state(conn, account_id: str) -> dict:
    """THE EXPLORATION BUDGET NOW: aggregate exposure (open BUY reservations
    + open cost basis), cumulative realized losses (gross) and net realized
    P&L. Read-only."""
    res = await conn.fetchval(
        "SELECT coalesce(sum(reserved_remaining_usd), 0) FROM paper_orders "
        " WHERE account_id=$1 AND strategy=$2 AND direction='BUY' "
        "   AND state = ANY($3::text[])", account_id, STRATEGY,
        list(L.OPEN_STATES))
    groups = await _groups(conn, account_id)
    pos = [p for p in await L.positions(conn, account_id,
                                        include_closed=True)
           if p["group_id"] in groups]
    open_basis = sum(float(p["cost_basis_usd"]) for p in pos
                     if p["open_qty"] > 1e-9)
    losses = sum(-float(p["realized_pnl_usd"]) for p in pos
                 if float(p["realized_pnl_usd"]) < 0)
    net = sum(float(p["realized_pnl_usd"]) for p in pos)
    exposure = float(res) + open_basis
    cash = await L.cash_state(conn, account_id)
    return {"exposure_usd": round(exposure, 6),
            "open_reservations_usd": round(float(res), 6),
            "open_cost_basis_usd": round(open_basis, 6),
            "headroom_usd": round(max(0.0, float(cash["available"])), 6),
            "headroom_basis": "AVAILABLE_SIMULATED_CASH",
            "realized_losses_usd": round(losses, 6),
            "realized_net_pnl_usd": round(net, 6),
            "loss_stop_reached": False,
            "positions": len(pos),
            "open_positions": sum(1 for p in pos if p["open_qty"] > 1e-9),
            "limits": {"max_entry_cost_usd": MAX_ENTRY_COST_USD,
                       "target_entry_cost_usd": TARGET_ENTRY_COST_USD,
                       "max_aggregate_exposure_usd":
                           MAX_AGGREGATE_EXPOSURE_USD,
                       "loss_stop_usd": LOSS_STOP_USD,
                       "one_position_per_fixture": not await LIMITS.uses_account_policy(conn, account_id)}}


async def fixture_taken(conn, account_id: str, fixture, slug) -> bool:
    """An exploration entry on this fixture that is open or has filled."""
    if await LIMITS.uses_account_policy(conn, account_id):
        return False
    return bool(await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM paper_orders WHERE account_id=$1 "
        "   AND strategy=$2 AND role='ENTRY' AND (us_market_slug=$3 OR "
        "   ($4::text IS NOT NULL AND fixture=$4)) AND "
        "   (state = ANY($5::text[]) OR filled_qty > 0))", account_id,
        STRATEGY, slug, fixture, list(L.OPEN_STATES)))


def locked_check_for(fixture, slug):
    """The owner's limits, evaluated UNDER THE ACCOUNT LOCK by
    `submit_order` just before the reservation is written."""
    async def check(conn, o: dict, reserve) -> dict | None:
        st = await limits_state(conn, o["account_id"])
        if MAX_ENTRY_COST_USD is not None and float(reserve) > MAX_ENTRY_COST_USD + 1e-9:
            return {"refusal": R_TOO_DEAR, "limits": st}
        if await fixture_taken(conn, o["account_id"], fixture, slug):
            return {"refusal": R_FIXTURE_TAKEN}
        return None
    return check


# ═════════════════════════════════════════════════════════════════════
# THE SAMPLE
# ═════════════════════════════════════════════════════════════════════

def draw(fixture) -> float:
    """u in [0, 1): preserve the V1 fixture draw through the V2 ramp."""
    h = hashlib.sha256(("%s:%s" % (SAMPLING_DRAW_VERSION, fixture)).encode()).hexdigest()
    return int(h[:15], 16) / float(16 ** 15)


def inclusion_probability(n_sport: int) -> float:
    n = max(1, int(n_sport or 0))
    return round(min(1.0, max(SAMPLING_FLOOR,
                              SAMPLING_TARGET_PER_SPORT / n)), 6)


async def selection(conn, *, cand: dict, at: float, account_id=None) -> dict:
    fam = str(cand.get("sport_family") or "UNKNOWN")
    n = await conn.fetchval(
        "SELECT count(DISTINCT coalesce(condition_id, event_key, "
        "                                us_market_slug)) "
        "  FROM external_valuations WHERE experiment_id=$1 "
        "   AND coalesce(sport_family, 'UNKNOWN') = $2 "
        "   AND decided_at > to_timestamp($3) AND us_market_slug IS NOT NULL",
        PB.EXPERIMENT_ID, fam, at - SAMPLING_WINDOW_S)
    owner = await LIMITS.uses_account_policy(conn, account_id)
    p = 1.0 if owner else inclusion_probability(int(n or 0))
    u = draw(cand.get("fixture"))
    return {"method": "ALL_ELIGIBLE_VALUATIONS_V3" if owner else SELECTION_METHOD, "policy_version": VERSION,
            "draw_version": SAMPLING_DRAW_VERSION,
            "sport_family": fam, "n_sport_fixtures_in_window": int(n or 0),
            "window_s": SAMPLING_WINDOW_S,
            "target_per_sport": None if owner else SAMPLING_TARGET_PER_SPORT,
            "floor": 1.0 if owner else SAMPLING_FLOOR, "selection_probability": p,
            "draw": round(u, 9), "selected": u < p,
            "unit": "ELIGIBLE_VALUATION" if owner else "FIXTURE (one draw per fixture, deterministic)",
            "contract_choice": ("every eligible valuation; repeated decision keys stay idempotent" if owner
                                else "first eligible contract of selected fixture; known order effect"),
            "market_types": ("full-game moneylines only: the one market "
                             "type whose mapping is established")}


# ═════════════════════════════════════════════════════════════════════
# THE DECISION
# ═════════════════════════════════════════════════════════════════════

def size_entry(levels: list, *, consumed: dict, fee_fn, at: float,
               budget_usd: float) -> dict:
    """At the BEST level only (the observed executable price; no walk into
    worse levels): whole contracts such that qty x price + the maximum fee
    for that qty stays within the budget, capped by the level's displayed
    depth not already consumed. Pure but for the fee function."""
    if not levels:
        return {"qty": 0, "why": "NO_LEVELS"}
    best = levels[0]
    px = float(best["price"])
    avail = max(0.0, float(best["qty"]) - float(consumed.get(
        SIM._wk(best["wire"]), 0.0)))
    per = px + float(L.max_fee_for(1, px, at=at, fee_fn=fee_fn))
    q = math.floor(min(avail, budget_usd / per if per > 0 else 0.0) + 1e-9)
    while q >= 1 and float(L.reservation_for(q, px, at=at, fee_fn=fee_fn)) \
            > budget_usd + 1e-9:
        q -= 1
    return {"qty": int(max(q, 0)), "limit": px, "wire": best["wire"],
            "depth_at_best": round(avail, 6), "budget_usd": budget_usd,
            "reservation_usd": (float(L.reservation_for(
                q, px, at=at, fee_fn=fee_fn)) if q >= 1 else 0.0),
            "why": None if q >= 1 else (R_TOO_DEAR if avail >= 1
                                        else "NO_DEPTH_AT_THE_BEST_LEVEL")}


async def decide_one(conn, ctx: dict, row: dict, pol=None) -> dict:
    """ONE EXPLORATION DECISION, persisted first; a selected, eligible,
    affordable candidate then submits ONE marketable IOC paper order at the
    observed best level (the simulator fills it after the delay)."""
    cfg = ctx["config"]
    ent, sim_cfg = cfg["entry"], cfg["simulator"]
    clock = ctx.get("clock") or (lambda: float(ctx["now"]))
    at = float(clock())
    fee_fn = ctx.get("fee_fn")
    for k in ("last_book_source", "last_book_age_s", "last_book_cooldown_s"):
        ctx.pop(k, None)
    row = dict(row)
    cand = DP.candidate_from_row(row)
    side = PD.holding_side_of(cand.get("side"))
    did = PB.decision_id_for(ctx["session_id"], cand["valuation_id"], POL)
    cat = await DP.catalogue_row(conn, cand.get("us_market_slug"))
    fxr = await DP.fixture_row(conn, cand.get("condition_id"))
    label = dict(DP.instrument_label(cand, catalogue_row=cat,
                                     fixture_row=fxr),
                 strategy=STRATEGY, disclosure=DISCLOSURE,
                 book_currency="NOT_ESTABLISHED", policy_version=VERSION,
                 economics_label=ECONOMICS_LABEL, position_label=LABEL,
                 training=True)
    refusals: list = []
    # ── THE SAFEGUARDS (never relaxed) ──────────────────────────────
    match = PB.completed_game_match(cand, row, catalogue=cat)
    refusals.extend(match["refusals"])
    real = DP._realism(cand, cat)
    if real["status"] == DP.FAIL:
        refusals.append(PB.R_NOT_REAL)
    pin = PD._pinnacle(cand, at=at, max_age=float(ent["pinnacle_max_age_s"]))
    pin.update(decided_via=ctx.get("decided_via") or PD.DECIDED_VIA_PASS,
               decided_at=at, strategy=STRATEGY, contract_match=match,
               real_event=real, displayed_quote_used_as_price=False)
    if pin.get("refusal"):
        refusals.append(pin["refusal"])
    # R30A: an NFL line's P(win | no tie) is valued as the venue contract
    # (tie pays 0.50) at the worst cited tie rate, before any edge or EV.
    conv_refusal = PB.apply_venue_conversion(pin, match)
    if conv_refusal and conv_refusal not in refusals:
        refusals.append(conv_refusal)
    cross = await PB.cross_strategy_exposure(
        conn, account_id=ctx["account_id"], strategy=STRATEGY,
        slug=cand.get("us_market_slug"), fixture=cand.get("fixture"))
    pin["cross_strategy_exposure"] = cross
    if cross["held"]:
        refusals.append(PB.R_CROSS_STRATEGY)
    # ── THE OWNER'S LIMITS AND THE SAMPLE (before any book read) ────
    try:
        lim = await limits_state(conn, ctx["account_id"])
    except Exception as exc:                                    # noqa: BLE001
        lim = {"error": type(exc).__name__}
        refusals.append(R_LIMITS_UNREADABLE)
    sel = await selection(conn, cand=cand, at=at, account_id=ctx["account_id"])
    if not refusals:
        if float(lim.get("headroom_usd") or 0.0) <= 0.0:
            refusals.append(L.R_INSUFFICIENT)
        elif await fixture_taken(conn, ctx["account_id"],
                                 cand.get("fixture"),
                                 cand.get("us_market_slug")):
            refusals.append(R_FIXTURE_TAKEN)
        elif await LIMITS.uses_account_policy(conn, ctx["account_id"]) and \
                await L.same_contract_held(conn, ctx["account_id"], STRATEGY,
                                           cand.get("us_market_slug"), side):
            refusals.append(L.R_SAME_CONTRACT_HELD)
        elif not sel["selected"]:
            refusals.append(R_NOT_SAMPLED)
    # ── XAVIER MUST BE ABLE TO PRICE WHAT IS ENTERED (before any book read)
    mgmt_price = None
    if not refusals:
        mgmt_price = await xavier_can_price(
            conn, cand=cand, at=at, max_age_s=float(ent["pinnacle_max_age_s"]))
        if not mgmt_price["manageable"]:
            refusals.append(R_XAVIER_CANNOT_PRICE)
    mgmt_protect = None
    p = pin.get("p")
    obs, md, levels = None, None, []
    sized: dict = {"qty": 0, "limit": None, "wire": None}
    econ: dict | None = None
    capital: dict | None = None
    book_age = None
    if not refusals:
        bk = await PB.book_for(conn, ctx, cand["us_market_slug"],
                               basis=STRATEGY)
        if bk.get("deferred"):
            return {"deferred": True, "why": "BOOK_READ_BUDGET"}
        got, obs = bk["got"], bk["obs"]
        md = obs.get("market_data")
        lv = SIM.levels_for(md, direction="BUY", holding_side=side)
        levels = lv["levels"]
        book_age = round(max(0.0, float(clock()) - float(obs["observed_at"])),
                         3)
        ctx["last_book_source"] = PB.book_source(bk)
        ctx["last_book_age_s"] = book_age
        if PD.book_deadline_refusal(got):
            retry = PB.book_retry_plan(ctx, got, pin)
            ctx["last_book_cooldown_s"] = retry.get("cooldown_s")
            if retry["retry"]:
                return {"deferred": True, "why": "BOOK_RETRY",
                        "retry_after_s": retry["after_s"], "retry": retry}
            refusals.append(PD.R_BOOK_DEADLINE)
        elif obs.get("error") or not levels:
            refusals.append(PD.no_book_refusal(obs, lv))
        elif book_age > PB.BOOK_MAX_AGE_S:
            refusals.append(PB.R_BOOK_NOT_CURRENT)
        else:
            consumed = await SIM._consumed(conn, cand["us_market_slug"],
                                           lv["side"], obs["obs_id"])
            budget = min(TARGET_ENTRY_COST_USD,
                         float(lim.get("headroom_usd") or 0.0))
            sized = size_entry(levels, consumed=consumed, fee_fn=fee_fn,
                               at=at, budget_usd=budget)
            if sized["qty"] < 1:
                refusals.append(sized.get("why") or PB.R_NO_QTY)
            else:
                walk = SIM.walk(levels, consumed=consumed,
                                limit=sized["limit"], qty=sized["qty"],
                                direction="BUY", allow_partial=True)
                econ = PB.conditional_economics(p=p, takes=walk["takes"],
                                                fee_fn=fee_fn, at=at)
                econ["exceptional_settlement"] = PB.exceptional_scenarios(
                    cand=cand, row=row, qty=econ["qty"], vwap=econ["vwap"])
                if not econ["fees_ok"]:
                    refusals.append(PB.R_FEES)
                else:
                    # PAPER CAPITAL AUTHORITY (migration 305): an exploration
                    # ENTRY also needs POSITIVE executable EV after the
                    # depth walk, per-level fees and the adverse-selection
                    # estimate (bettor_capital_eligibility, the same gate as
                    # every policy); a negative-EV "research cost" entry is
                    # no longer given paper capital.
                    capital = CA.evaluate_executable(
                        p=p, levels=[{"price": t["price"], "qty": t["take"]}
                                     for t in walk["takes"]],
                        qty=sized["qty"], limit=sized["limit"],
                        fee_fn=fee_fn, at=at,
                        settlement={"compatibility": (
                            "COMPATIBLE" if match.get("established") is True
                            else "NOT_ESTABLISHED_BY_THE_MATCH")},
                        identity={"us_market_slug": cand.get(
                            "us_market_slug"),
                            "payout_event": cand.get("payout_event"),
                            "fixture": cand.get("fixture"),
                            "holding_side": side})
                    econ["capital_eligibility"] = {k: capital.get(k) for k in (
                        "capital_eligible", "qty", "total_executable_ev_usd",
                        "fees_usd", "adverse_selection_usd", "refusals")}
                    if not capital.get("capital_eligible"):
                        refusals.extend(r for r in capital["refusals"]
                                        if r not in refusals)
                    # ── XAVIER MUST BE ABLE TO PROTECT WHAT IS ENTERED
                    mgmt_protect = xavier_can_protect(econ=econ,
                                                      fee_fn=fee_fn, at=at)
                    econ["xavier_protection"] = mgmt_protect
                    if not mgmt_protect["protectable"]:
                        refusals.append(R_XAVIER_CANNOT_PROTECT)
    PD.recheck_primary_reference(cand, pin, ctx, refusals)
    verdict = DP.ENTER if not refusals else DP.REFUSE
    best_px = levels[0]["price"] if levels else None
    gross = (None if best_px is None or p is None
             else round(DP.gross_edge(p, best_px) * 100.0, 9))
    fpc = (None if best_px is None else
           round(PB.fee_per_contract(fee_fn, best_px, at), 9))
    estimate = {
        "gross_edge_pp_at_best": gross,
        "fee_per_contract_usd": fpc,
        "net_edge_pp_at_best": (None if gross is None or fpc is None
                                else round(gross - fpc * 100.0, 9)),
        "expected_net_profit_usd": (econ or {}).get(
            "expected_net_profit_usd"),
        "fees_usd": (econ or {}).get("fees_usd"),
        "passes_investment_edge_threshold": (
            None if gross is None else gross + 1e-9 >= PB.CG_MIN_EDGE_PP_V2),
        "passes_positive_after_fees": (
            None if econ is None else bool(econ.get("net_ev_positive"))),
        "label": ECONOMICS_LABEL,
        "negative_ev_is": "an explicit research cost, not investment "
                          "performance",
        "conditional_on": "ORDINARY_COMPLETION"}
    economics_rec = {
        "strategy": STRATEGY, "version": VERSION, "disclosure": DISCLOSURE,
        "label": ECONOMICS_LABEL, "position_label": LABEL,
        "training_purpose": TRAINING_PURPOSE, "probability": p,
        "probability_basis": "PINNACLE_ONLY_DEVIGGED_STORED",
        "estimate": estimate, "selection": sel, "limits": lim,
        "limit_price": sized.get("limit"), "proposed_qty": sized.get("qty"),
        "sizing": sized, "price_rule": ("the observed book's BEST level only "
                                        "(no walk into worse levels)"),
        "book_age_s": book_age, "book_max_age_s": PB.BOOK_MAX_AGE_S,
        "book_currency": PB.BOOK_CURRENCY, "acquisition": econ,
        "exceptional_terms": match.get("exceptional_terms"),
        "xavier_management": {"price": mgmt_price,
                              "protection": mgmt_protect},
        "refusals": refusals,
        # the keys the readbacks and the investment-policy views read
        "best_level_edge_pp": gross, "threshold_edge_pp": None}
    conditions = [
        {"condition": "contract_outcome_settlement_match",
         "passed": match["established"], "refusals": match["refusals"]},
        {"condition": "pinnacle_fresh_at_the_decision_instant",
         "passed": pin.get("qualified") is True, "value": pin.get("age_s"),
         "threshold": pin.get("limit_s"), "units": "seconds",
         "refusal": pin.get("refusal")},
        {"condition": "exploration_limits_and_sample",
         "passed": not any(r in refusals for r in (
             R_LOSS_STOP, R_AGGREGATE, R_FIXTURE_TAKEN, R_NOT_SAMPLED)),
         "value": {"exposure_usd": lim.get("exposure_usd"),
                   "realized_losses_usd": lim.get("realized_losses_usd"),
                   "selected": sel["selected"]}},
        {"condition": "xavier_held_read_can_price_this_contract",
         "passed": None if mgmt_price is None else mgmt_price["manageable"],
         "value": None if mgmt_price is None
         else mgmt_price["held_read_reason"],
         "refusal": (R_XAVIER_CANNOT_PRICE if mgmt_price is not None
                     and not mgmt_price["manageable"] else None)},
        {"condition": "xavier_can_place_the_standing_protection",
         "passed": None if mgmt_protect is None
         else mgmt_protect["protectable"],
         "value": None if mgmt_protect is None
         else mgmt_protect["protective_price"],
         "refusal": (R_XAVIER_CANNOT_PROTECT if mgmt_protect is not None
                     and not mgmt_protect["protectable"] else None)},
        {"condition": "current_paper_book_with_depth",
         "passed": (None if obs is None else bool(levels) and
                    book_age is not None and book_age <= PB.BOOK_MAX_AGE_S),
         "value": book_age, "threshold": PB.BOOK_MAX_AGE_S,
         "units": "seconds"},
        {"condition": "entry_cost_incl_fees_against_target",
         "passed": None if not sized.get("qty") else True,
         "value": sized.get("reservation_usd"),
         "threshold": MAX_ENTRY_COST_USD, "target": TARGET_ENTRY_COST_USD, "units": "USD"}]
    policy_decision = {
        "strategy": STRATEGY, "policy_version": VERSION,
        "disclosure": DISCLOSURE, "economics_label": ECONOMICS_LABEL,
        "training": True, "training_purpose": TRAINING_PURPOSE,
        "position_label": LABEL, "p_internal": None, "p_blended": None,
        "p_pinnacle": p, "conditions": conditions, "estimate": estimate,
        "gross_edge_pp": gross, "fees_usd": estimate["fees_usd"],
        "net_expected_profit_usd": estimate["expected_net_profit_usd"],
        "selection": sel, "limits": lim, "threshold_edge_pp": None,
        "investment_policy_threshold_pp_not_applied": PB.CG_MIN_EDGE_PP_V2,
        "admitted": verdict == DP.ENTER,
        "refusal": refusals[0] if refusals else None, "refusals": refusals}
    internal_rec = {"available": False, "p": None, "strategy": STRATEGY,
                    "reason": "no internal model by design"}
    book = None if obs is None else {
        "book_obs_id": obs["obs_id"], "observed_at": obs["observed_at"],
        "observed_at_is": "OUR_RECEIPT_INSTANT",
        "age_at_decision_s": book_age, "error": obs.get("error"),
        "side_consumed": SIM.side_consumed("BUY", side or "LONG"),
        "levels": levels[:10], "depth_levels": len(levels),
        "book_currency": PB.BOOK_CURRENCY,
        "basis": "OBSERVED_PAPER_BOOK_LEVELS_NOT_THE_VALUATION_QUOTE"}
    gaps = PB.qualification_gaps(cand=cand)
    alts = PB._alternatives(md, side=side or "LONG", p=p)
    optimistic = (SIM.optimistic_fill(md, direction="BUY", holding_side=side,
                                      qty=sized["qty"], limit=sized["limit"])
                  if verdict == DP.ENTER else None)
    provenance = PD.decision_provenance(
        strategy=STRATEGY, code_version=VERSION, policy_version=VERSION,
        row=row, session=ctx.get("session"), verdict=verdict,
        refusals=refusals, policy_decision=policy_decision,
        internal_model=internal_rec, pinnacle=pin, book=book,
        limit_price=sized.get("limit"), qty=sized.get("qty"),
        alternatives=alts, optimistic=optimistic,
        simulator_version=cfg["simulator_version"],
        decided_via=pin.get("decided_via"), at=at)
    rec = {"decision_id": did, "verdict": verdict, "strategy": STRATEGY,
           "refusal": refusals[0] if refusals else None,
           "refusals": refusals, "selection": {
               "p": sel["selection_probability"], "u": sel["draw"],
               "selected": sel["selected"]},
           "book_source": ctx.get("last_book_source"),
           "book_age_s": ctx.get("last_book_age_s"),
           "cooldown_s": ctx.get("last_book_cooldown_s")}
    inserted = await conn.fetchval(
        "INSERT INTO paper_decisions (decision_id, session_id, account_id, "
        " decided_at, valuation_id, us_market_slug, holding_side, intent, "
        " fixture, label, verdict, refusal, refusals, p_internal, "
        " internal_model, p_pinnacle, pinnacle, p_blended, book_obs_id, "
        " book, proposed_qty, limit_price, economics, qualification_gaps, "
        " policy_version, policy_decision, alternatives, optimistic, "
        " simulator_version, strategy, provenance) VALUES ($1,$2,$3,$4,$5,"
        " $6,$7,$8,$9,$10::jsonb,$11,$12,$13,NULL,$14::jsonb,$15,$16::jsonb,"
        " NULL,$17,$18::jsonb,$19,$20,$21::jsonb,$22::jsonb,$23,$24::jsonb,"
        " $25::jsonb,$26::jsonb,$27,$28,$29::jsonb) ON CONFLICT DO NOTHING "
        " RETURNING decision_id",
        did, ctx["session_id"], ctx["account_id"], L._ts(at),
        cand["valuation_id"], cand.get("us_market_slug"), side,
        cand.get("side"), cand.get("fixture"),
        json.dumps(label, default=str), verdict, rec["refusal"], refusals,
        json.dumps(internal_rec, default=str), p,
        json.dumps(pin, default=str),
        None if obs is None else obs["obs_id"],
        None if book is None else json.dumps(book, default=str),
        (None if not sized.get("qty") else L.D(sized["qty"])),
        (None if sized.get("limit") is None else L.D(sized["limit"])),
        json.dumps(economics_rec, default=str), json.dumps(gaps, default=str),
        VERSION, json.dumps(policy_decision, default=str),
        json.dumps(alts, default=str),
        None if optimistic is None else json.dumps(optimistic, default=str),
        cfg["simulator_version"], STRATEGY,
        json.dumps(provenance, default=str))
    if inserted is None:
        return dict(rec, duplicate=True)
    cevidence = CA.capital_evidence(
        capital, p=p, limit=sized.get("limit"),
        threshold_edge_pp=PB.CG_MIN_EDGE_PP_V2, basis="EXPLORATION_ENTRY",
        levels=levels, book_obs_id=None if obs is None else obs.get("obs_id"),
        book_observed_at=None if obs is None else obs.get("observed_at"))
    # THE DECISION'S CONTROL INPUTS, carried to the ledger's profitability
    # bind (control 25; this policy has no decision-stage bind): the
    # probability's and the book's observation instants and the settlement
    # verdict the capital evaluation resolved. The bind re-checks them under
    # the account lock and refuses by name when one is missing.
    from .. import bettor_paper_profitability_bind as PBIND
    cevidence = PBIND.carry_inputs(
        cevidence, evaluated_at=at, p_observed_at=pin.get("at"),
        book_observed_at=None if obs is None else obs.get("observed_at"),
        settlement={"compatibility": (
            "COMPATIBLE" if match.get("established") is True
            else "NOT_ESTABLISHED_BY_THE_MATCH")})
    if verdict != DP.ENTER:
        # THE ENTRY-REFUSAL CENSUS (migration 305): evidence only.
        await CA.record_refusal(
            conn, account_id=ctx["account_id"], strategy=STRATEGY,
            stage="DECISION", refusal=rec["refusal"], refusals=refusals,
            decision_id=did, slug=cand.get("us_market_slug"),
            holding_side=side, fixture=cand.get("fixture"),
            line=cand.get("line"), scope=cand.get("scope"), p=p,
            best_price=best_px, threshold_edge_pp=PB.CG_MIN_EDGE_PP_V2,
            evidence=cevidence, expected_fees_usd=(econ or {}).get(
                "fees_usd"),
            executable_ev_usd=(econ or {}).get("expected_net_profit_usd"),
            qty=sized.get("qty"), limit_price=sized.get("limit"), at=at)
        return rec
    # THE ENTER IS RECORDED: its paper order is owed (PD.bounded_decision).
    PD.enter_recorded(ctx, did)
    # ── ONE DECISION -> ONE EXECUTION INTENT: PAPER ONLY ───────────────
    # Exploration is training: the executing process's hook records the
    # intent with live_eligible = false (STRATEGY_NOT_LIVE_ELIGIBLE); the
    # actual lane never sees it and nothing is dispatched.
    rec["execution_intent_id"], rec["actual_lane"] = None, "PAPER_ONLY"
    hook = DH.DECISION_HOOK
    if hook is not None:
        try:
            got_i = await hook(conn, {
                "decision_id": did, "valuation_id": cand["valuation_id"],
                "strategy": STRATEGY, "policy_version": VERSION,
                "slug": cand["us_market_slug"], "order_intent": cand.get("side"),
                "holding_side": side, "group_id": PB.group_id_for(did),
                "order_type": "MARKETABLE", "time_in_force": "IOC",
                "paper_target_qty": sized["qty"], "limit_price": sized["limit"],
                "wire_price": sized["wire"],
                "book_obs_id": None if obs is None else obs["obs_id"],
                "book_observed_at": (None if obs is None
                                     else float(obs["observed_at"])),
                "decided_at": at,
                "evidence": {"valuation_id": cand["valuation_id"], "training": True},
                "timeline": {"decision_complete": {"utc_s": at}}}) or {}
            rec["execution_intent_id"] = got_i.get("intent_id")
            rec["actual_lane"] = got_i.get("actual_lane") or "PAPER_ONLY"
        except Exception as exc:                                # noqa: BLE001
            rec["actual_lane"] = "EXECUTION_HOOK_FAILED:%s" % type(exc).__name__
    # ── ONLY NOW, THE PAPER ORDER (limits re-checked under the lock) ──
    delay = float(sim_cfg["decision_to_execution_delay_s"])
    order = {"idempotency_key": "%s:ENTRY" % did,
             "account_id": ctx["account_id"],
             "session_id": ctx["session_id"],
             "group_id": PB.group_id_for(did), "role": "ENTRY",
             "direction": "BUY", "holding_side": side,
             "intent": cand.get("side"),
             "us_market_slug": cand["us_market_slug"],
             "fixture": cand.get("fixture"), "label": label,
             "order_type": "MARKETABLE", "time_in_force": "IOC",
             "allow_partial": True, "qty": sized["qty"],
             "limit_price": sized["limit"], "wire_price": sized["wire"],
             "decision_id": did, "decided_at": at,
             "eligible_at": at + delay,
             "expires_at": at + float(sim_cfg["marketable_ttl_s"]),
             "simulator_version": cfg["simulator_version"],
             "strategy": STRATEGY, "capital_evidence": cevidence}
    got = await L.submit_order(
        conn, order, caps=cfg["risk"], fee_fn=fee_fn, now=at,
        exclusive_fixture=True,
        locked_check=locked_check_for(cand.get("fixture"),
                                      cand["us_market_slug"]))
    rec["order"] = {k: got.get(k) for k in ("ok", "refusal", "duplicate")}
    if got.get("ok"):
        rec["order_id"] = got["order"]["order_id"]
        rec["eligible_at"] = at + delay
    else:
        rec["order_refusal"] = got.get("refusal")
        await PD._finding(conn, ctx, kind=PB.R_ORDER_REFUSED, subject=did,
                          detail={"refusal": got.get("refusal"),
                                  "decision_id": did, "at": at,
                                  "strategy": STRATEGY,
                                  "disclosure": DISCLOSURE,
                                  **{k: v for k, v in got.items()
                                     if k not in ("ok",)}})
    return rec


async def step(conn, ctx: dict) -> dict:
    """THE EXPLORATION STRATEGY'S PAPER STEP (its own kill-switch row)."""
    return await PB.step(conn, ctx, POL, decide=decide_one)


async def decide_for_hook(conn, ctx: dict, row: dict, *,
                          timeout_s: float) -> dict:
    return await PB.decide_for_hook(conn, ctx, row, timeout_s=timeout_s,
                                    pol=POL, decide=decide_one)


def describe() -> dict:
    return {"strategy": STRATEGY, "version": VERSION, "label": LABEL,
            "disclosure": DISCLOSURE, "economics_label": ECONOMICS_LABEL,
            "training_purpose": TRAINING_PURPOSE,
            "limits": {"max_entry_cost_usd_incl_fees": MAX_ENTRY_COST_USD,
                       "target_entry_cost_usd": TARGET_ENTRY_COST_USD,
                       "max_aggregate_exposure_usd":
                           MAX_AGGREGATE_EXPOSURE_USD,
                       "loss_stop_realized_usd": LOSS_STOP_USD,
                       "positions_per_fixture": None,
                       "overlap_with_other_strategies": "allowed on main account"},
            "selection": {"method": "ALL_ELIGIBLE_VALUATIONS_V3",
                          "draw_version": SAMPLING_DRAW_VERSION,
                          "target_per_sport": None,
                          "floor": 1.0,
                          "window_s": SAMPLING_WINDOW_S}}
