"""KALSHI SHADOW PLANNER + READ-ONLY ACCOUNT RECONCILIATION WRITER (rc6.3
kalshi-shadow; Issue #6 gates 2 / 3 / 6). SHADOW ONLY -- IT NEVER SUBMITS.

WHAT IT DOES, every pass (INTERVAL_S), in the API process beside the 1:1,000
execution mirror's runner:

  1. GATE. It runs only while Kalshi live money is OFF everywhere it is
     switched: the Kalshi small-live control (migration 196) disabled, the
     KALSHI_SMALLLIVE_ENABLED switch off in this process, and SMALL LIVE in
     SHADOW (migration 225). Any of them on, or a table absent, and it
     records nothing and names why (stand-down, heartbeat only).
  2. PLAN. Every LINKED PAPER decision of the mirrored paper account
     (execmirror.PAPER_ACCOUNT, as the 1:1,000 mirror scopes it; a decision
     of any other paper account gets no row and is counted in the pass) not
     yet seen -- an ENTER decision with its ENTRY BUY paper order -- gets
     exactly ONE kalshi_shadow_intents row (migration 367), PLANNED or
     EXCLUDED with the named reason, idempotent (one row per decision,
     append-only):
       eligibility  FIRST, the owner's live-eligibility allowlist, the one
                    every live-shaped path applies (execmirror.
                    live_eligibility: execmirror.plan_new, execution_intent,
                    live_parity): only an allowlisted strategy at an
                    allowlisted policy version (the decision's own
                    policy_version) may be planned. Exploration, Derek's
                    lane, the maker experiment, the benchmark, any unknown
                    strategy and any unpromoted version are PAPER ONLY:
                    EXCLUDED as STRATEGY_NOT_LIVE_ELIGIBLE with the class
                    and the allowlist, and nothing else is evaluated for
                    them (no counterpart, no plan, no EV).
       counterpart  the decision's held side must be a settlement-certified
                    canonical claim (canonical_claim_aliases: POLYMARKET_US,
                    market = the slug, LONG = YES / SHORT = NO, certificate
                    CERTIFIED, mapping ESTABLISHED, settlement PROVEN) that
                    a CERTIFIED Kalshi YES alias carries too (same claim
                    fingerprint: payoff vectors equal in every settlement
                    state). Otherwise EXCLUDED: NO_CERTIFIED_COUNTERPART
                    (which part is missing is named), COUNTERPART_ONLY_A_NO
                    _LEG (kalshi_orders buys YES legs only) or AMBIGUOUS.
       plan         kalshi_orders.plan, unchanged: 1:<scale> of the paper
                    quantity (the control's scale, 1000), whole contracts
                    half-to-even, never rounded up past the venue minimum
                    (1 contract), the 1-cent tick rounded against us, the
                    control's per-order cap, the buying power of a fresh
                    complete read-only reconciliation when one exists.
       freshness    the decision must be inside the mirror's own intent age
                    (execmirror.MAX_INTENT_AGE_S, the Pinnacle 30 s rule), its
                    paper order not past its own expires_at (the mirror's
                    INTENT_STALE), and the Kalshi book inside its SLA
                    (kalshi_market_data.BOOK_SLA_S) -- a plan is never made on
                    a stale decision, an expired order or a stale book. The
                    paper order's STATE is recorded, never decisive: the live
                    ENTRY path (execution_intent.ActualLane) dispatches before
                    the paper order exists and never reads its simulated
                    outcome, and the paper simulator records a partly filled
                    IOC as CANCELED (real paper exposure), so judging a Kalshi
                    plan on the PMUS paper fill would be look-ahead.
       execution    the current Kalshi YES asks walked at the plan's limit for
                    the planned contracts (best executable price first);
                    none at or under the limit, or too few, is EXCLUDED.
       all-in       the published fee in force for the market (kalshi_fees
                    over migration-315 kalshi_fee_terms, per fill level);
                    unknown terms are EXCLUDED, never a zero fee.
       EV           against the decision's own fair probability of the held
                    side (p_blended, else p_pinnacle): net EV per contract
                    must be > 0; EXCLUDED as FEE_MAKES_EV_NEGATIVE when the
                    price alone cleared and the fee did not.
     A PLANNED row carries the would-be V2 body as EVIDENCE (plan_payload);
     the table has no venue order id, no submit time and no state beyond
     PLANNED / EXCLUDED, by CHECK. PLANNED is not a live admission: no
     Kalshi live book-currency or settlement-gate approval exists, and the
     row says so (detail.live_admission).
     COVERAGE. The first passes after boot read back BACKFILL_S; once a pass
     has COMMITTED a short page the steady window is LOOKBACK_S, widened to
     reach back to the last committed short page whenever passes failed or
     were skipped (an outage up to BACKFILL_S loses no decision).
  3. RECONCILE (once per RECON_EVERY_S window). With a Kalshi credential
     PRESENT in this process, a read-only account reconciliation
     (kalshi_account.snapshot over GET balance / positions / resting orders
     / fills / settlements) through kalshi_account.read_only_client -- an
     account reader with no submit / cancel method, over a transport that
     raises on anything but GET -- written to migration 196's
     kalshi_account_reconciliations (baseline never accepted here). With
     none, a named UNAVAILABLE row in kalshi_shadow_account_reads; nothing
     is written to kalshi_account_reconciliations without a real read.

WHAT IT CANNOT DO. It never calls a submit, cancel or order endpoint: no
statement here names one (tests/test_kalshi_isolation.py pins it in the
source; tests/test_rc63_kalshi_shadow.py runs whole passes with every
network-mutating primitive patched to raise). It changes no control, no
credential, no environment, no PAPER record (it reads paper_decisions /
paper_orders only) and none of migration 196's live tables except the
read-only reconciliation it is the writer of.

Kill switch: KALSHI_SHADOW_PLANNER=off (default on).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation

from . import canonical_claims_db as CCDB
from . import execmirror as EM
from . import kalshi_account as KA
from . import kalshi_fees as KF
from . import kalshi_linkage as KL
from . import kalshi_mapping as KM
from . import kalshi_market_data as KMD
from . import kalshi_orders as KO

log = logging.getLogger(__name__)

VERSION = "KALSHI_SHADOW_PLANNER_V1"
SERVICE = "kalshi_shadow"
MODE = "SHADOW"
ENV_FLAG = "KALSHI_SHADOW_PLANNER"
INTERVAL_S = 15.0
FIRST_DELAY_S = 150.0
PASS_TIMEOUT_S = 60.0
#: the first passes after boot read back this far (newest first, bounded by
#: MAX_DECISIONS_PER_PASS) until the window is drained; then LOOKBACK_S
BACKFILL_S = 7 * 86400.0
LOOKBACK_S = 3600.0
#: a decision stamped later than now by more than this is not read (a clock
#: anomaly is never planned against the current book)
CLOCK_SKEW_S = 5.0
MAX_DECISIONS_PER_PASS = 200
#: the mirror's own admissibility of a paper decision (Pinnacle's 30 s rule)
MAX_DECISION_AGE_S = EM.MAX_INTENT_AGE_S
BOOK_SLA_S = KMD.BOOK_SLA_S
#: a read-only reconciliation is written at most once per window; its age
#: bound for buying power is the submission gate's own (kalshi_venue.
#: RECONCILIATION_MAX_AGE_S, 15 min; pinned equal by a test)
RECON_EVERY_S = 900.0
BUYING_POWER_MAX_AGE_S = 900.0
RECON_TIMEOUT_S = 60.0
RECON_MAX_PAGES = 3
RECON_ACTOR = "kalshi_shadow (read-only reconciliation writer, SHADOW)"
LOCK_KEY = 0x4B534831                          # 'KSH1'
ACCOUNT_LOCK_KEY = 0x4B534832                  # 'KSH2'

PMUS_VENUE = "POLYMARKET_US"
KALSHI_VENUE = "KALSHI"
#: the mirrored PAPER account: the 1:1,000 execution mirror's own scope
PAPER_ACCOUNT = EM.PAPER_ACCOUNT
#: this planner plans NEW exposure only (the ENTRY BUY of a decision); the
#: allowlist's version check applies to it (execmirror.BUY_ROLES)
PLAN_ROLE = "ENTRY"

# ── named exclusions (refusal_taxonomy_table: rc6.3 kalshi-shadow) ────
R_KSH_STRATEGY_NOT_LIVE_ELIGIBLE = "KALSHI_SHADOW_STRATEGY_NOT_LIVE_ELIGIBLE"
R_KSH_PAPER_ORDER_EXPIRED_BEFORE_PLAN = \
    "KALSHI_SHADOW_PAPER_ORDER_EXPIRED_BEFORE_PLAN"
R_KSH_NO_CERTIFIED_COUNTERPART = "KALSHI_SHADOW_NO_CERTIFIED_COUNTERPART"
R_KSH_COUNTERPART_ONLY_A_NO_LEG = "KALSHI_SHADOW_COUNTERPART_ONLY_A_NO_LEG"
R_KSH_COUNTERPART_AMBIGUOUS = "KALSHI_SHADOW_COUNTERPART_AMBIGUOUS"
R_KSH_BELOW_VENUE_MINIMUM = "KALSHI_SHADOW_BELOW_VENUE_MINIMUM"
R_KSH_ABOVE_ORDER_CAP = "KALSHI_SHADOW_ABOVE_ORDER_CAP"
R_KSH_INSUFFICIENT_CASH = "KALSHI_SHADOW_INSUFFICIENT_CASH"
R_KSH_UNSUPPORTED_ORDER = "KALSHI_SHADOW_UNSUPPORTED_ORDER"
R_KSH_PRICE_OUT_OF_RANGE = "KALSHI_SHADOW_PRICE_OUT_OF_RANGE"
R_KSH_POST_ONLY_WOULD_CROSS = "KALSHI_SHADOW_POST_ONLY_WOULD_CROSS"
R_KSH_POST_ONLY_BOOK_UNKNOWN = "KALSHI_SHADOW_POST_ONLY_BOOK_UNKNOWN"
R_KSH_MAPPING_NOT_ESTABLISHED = "KALSHI_SHADOW_MAPPING_NOT_ESTABLISHED"
R_KSH_PLAN_EXCLUDED = "KALSHI_SHADOW_PLAN_EXCLUDED"
R_KSH_NO_FAIR_PROBABILITY = "KALSHI_SHADOW_NO_FAIR_PROBABILITY"
R_KSH_DECISION_STALE_AT_PLAN = "KALSHI_SHADOW_DECISION_STALE_AT_PLAN"
R_KSH_BOOK_UNAVAILABLE = "KALSHI_SHADOW_BOOK_UNAVAILABLE"
R_KSH_BOOK_STALE = "KALSHI_SHADOW_BOOK_STALE"
R_KSH_NOT_EXECUTABLE_AT_LIMIT = "KALSHI_SHADOW_NOT_EXECUTABLE_AT_LIMIT"
R_KSH_INSUFFICIENT_DEPTH_AT_LIMIT = "KALSHI_SHADOW_INSUFFICIENT_DEPTH_AT_LIMIT"
R_KSH_FEE_TERMS_UNKNOWN = "KALSHI_SHADOW_FEE_TERMS_UNKNOWN"
R_KSH_FEE_MAKES_EV_NEGATIVE = "KALSHI_SHADOW_FEE_MAKES_EV_NEGATIVE"
R_KSH_EV_NOT_POSITIVE = "KALSHI_SHADOW_EV_NOT_POSITIVE"
# the runner's own stand-downs and the account read's (heartbeat / reads)
R_KSH_STOOD_DOWN_CONTROL_ENABLED = "KALSHI_SHADOW_STOOD_DOWN_CONTROL_ENABLED"
R_KSH_STOOD_DOWN_ENV_SWITCH_ON = "KALSHI_SHADOW_STOOD_DOWN_ENV_SWITCH_ON"
R_KSH_STOOD_DOWN_SMALL_LIVE_NOT_SHADOW = \
    "KALSHI_SHADOW_STOOD_DOWN_SMALL_LIVE_NOT_SHADOW"
R_KSH_SCHEMA_ABSENT = "KALSHI_SHADOW_SCHEMA_ABSENT"
R_KSH_ACCOUNT_READ_TIMEOUT = "KALSHI_SHADOW_ACCOUNT_READ_TIMEOUT"

#: kalshi_orders.plan's own exclusion -> this lane's named exclusion
PLAN_CODES = {
    KO.BELOW_VENUE_MINIMUM: R_KSH_BELOW_VENUE_MINIMUM,
    KO.ABOVE_ORDER_CAP: R_KSH_ABOVE_ORDER_CAP,
    KO.INSUFFICIENT_CASH: R_KSH_INSUFFICIENT_CASH,
    KO.UNSUPPORTED_ORDER: R_KSH_UNSUPPORTED_ORDER,
    KO.PRICE_OUT_OF_RANGE: R_KSH_PRICE_OUT_OF_RANGE,
    KO.POST_ONLY_WOULD_CROSS: R_KSH_POST_ONLY_WOULD_CROSS,
    KO.POST_ONLY_BOOK_UNKNOWN: R_KSH_POST_ONLY_BOOK_UNKNOWN,
    KO.MAPPING_NOT_ESTABLISHED: R_KSH_MAPPING_NOT_ESTABLISHED,
}

# which part of the certified pair is missing (detail.why)
W_PMUS_ALIAS_ABSENT = "NO_CANONICAL_ALIAS_FOR_THE_HELD_SIDE"
W_PMUS_ALIAS_NOT_CERTIFIED = "THE_HELD_SIDE_ALIAS_IS_NOT_A_CERTIFIED_CLAIM"
W_NO_KALSHI_ALIAS = "NO_CERTIFIED_KALSHI_ALIAS_CARRIES_THE_CLAIM"

#: the would-be fee for a RESTING (post-only) plan: Kalshi's maker fee is
#: never assumed (kalshi_fees: "maker not priced ... never assumed zero"),
#: so the published TAKER schedule is the conservative bound, as
#: kalshi_orders.MAKER_COEFFICIENT_DEFAULT already does
FEE_BASIS_TAKER = "KALSHI_PUBLISHED_TAKER_SCHEDULE"
FEE_BASIS_MAKER_BOUND = "KALSHI_PUBLISHED_TAKER_SCHEDULE_AS_THE_MAKER_BOUND"

#: drained: a pass has COMMITTED a short page (the backfill is done);
#: complete_through: the `now` of the last such pass -- every linked
#: decision of the mirrored account decided by then has its row
STATE = {"drained": False, "complete_through": None}


def window_s(now: float) -> float:
    """How far back this pass reads: BACKFILL_S until a pass has committed a
    short page; then LOOKBACK_S, widened to reach back to the last committed
    short page (a failed, skipped or full pass never shortens the reach),
    never beyond BACKFILL_S."""
    if not STATE["drained"]:
        return BACKFILL_S
    since = STATE.get("complete_through")
    if since is None:
        return LOOKBACK_S
    return min(BACKFILL_S, max(LOOKBACK_S, float(now) - float(since)
                               + LOOKBACK_S))


def note_committed(counts: dict, *, now: float) -> None:
    """Called only AFTER the plan transaction committed: a short page means
    every decision in the window has its row."""
    if counts.get("seen", MAX_DECISIONS_PER_PASS) < MAX_DECISIONS_PER_PASS:
        STATE["drained"] = True
        STATE["complete_through"] = float(now)


def enabled(env=None) -> bool:
    env = os.environ if env is None else env
    return str(env.get(ENV_FLAG, "on")).strip().lower() not in (
        "off", "0", "false", "no")


def _d(v) -> Decimal | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


_6DP = Decimal("0.000001")


def q6(v: Decimal, rounding) -> Decimal:
    return Decimal(v).quantize(_6DP, rounding=rounding)


def _j(v) -> str:
    return json.dumps(v, default=str, sort_keys=True)


def _jl(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


def shadow_id(decision_id: str) -> str:
    return "kshadow:%s" % decision_id


def side_of(holding: str) -> str:
    return "NO" if str(holding).upper() == "SHORT" else "YES"


# ═════════════════════════════════════════════════════════════════════
# THE COUNTERPART (pure)
# ═════════════════════════════════════════════════════════════════════

def certified(alias: dict) -> bool:
    return bool(alias.get("claim_fingerprint")
                and alias.get("certificate_status") == "CERTIFIED"
                and alias.get("mapping_status") == "ESTABLISHED"
                and alias.get("settlement_status") == "PROVEN")


def _alias_summary(a: dict) -> dict:
    return {k: a.get(k) for k in ("alias_key", "venue", "market_id", "side",
                                  "claim_fingerprint", "certificate_status",
                                  "mapping_status", "settlement_status",
                                  "rules_sha256", "updated_at")}


def counterpart(slug: str, holding: str, pmus_aliases: list,
                kalshi_aliases: list) -> dict:
    """The settlement-certified Kalshi YES market carrying the same claim as
    the held side of this PMUS contract, or the named reason there is none.
    Fail-closed: a NULL certificate is NOT certified."""
    side = side_of(holding)
    mine = [a for a in pmus_aliases
            if a.get("venue") == PMUS_VENUE and a.get("market_id") == slug
            and a.get("side") == side]
    out = {"pmus_side": side, "pmus_aliases": [_alias_summary(a) for a in mine]}
    if not mine:
        return dict(out, ok=False, code=R_KSH_NO_CERTIFIED_COUNTERPART,
                    why=W_PMUS_ALIAS_ABSENT)
    cert = [a for a in mine if certified(a)]
    if not cert:
        return dict(out, ok=False, code=R_KSH_NO_CERTIFIED_COUNTERPART,
                    why=W_PMUS_ALIAS_NOT_CERTIFIED)
    fps = {a["claim_fingerprint"] for a in cert}
    ks = [a for a in kalshi_aliases
          if a.get("venue") == KALSHI_VENUE
          and a.get("claim_fingerprint") in fps and certified(a)]
    out["kalshi_aliases"] = [_alias_summary(a) for a in ks]
    out["claim_fingerprints"] = sorted(fps)
    if not ks:
        return dict(out, ok=False, code=R_KSH_NO_CERTIFIED_COUNTERPART,
                    why=W_NO_KALSHI_ALIAS)
    yes = [a for a in ks if a.get("side") == "YES"]
    if not yes:
        return dict(out, ok=False, code=R_KSH_COUNTERPART_ONLY_A_NO_LEG,
                    why="kalshi_orders buys the YES leg of a market only; the "
                        "claim is carried by Kalshi NO legs only")
    tickers = sorted({a["market_id"] for a in yes})
    if len(tickers) != 1:
        return dict(out, ok=False, code=R_KSH_COUNTERPART_AMBIGUOUS,
                    why="%d Kalshi YES markets carry the claim" % len(tickers),
                    tickers=tickers)
    k = next(a for a in yes if a["market_id"] == tickers[0])
    return dict(out, ok=True, code=None, ticker=tickers[0],
                claim_fingerprint=k["claim_fingerprint"],
                kalshi_alias=_alias_summary(k))


def target_for(cp: dict, *, slug: str, holding: str) -> KM.MappingVerdict:
    """The kalshi_mapping verdict kalshi_orders.plan takes: ESTABLISHED for
    the certified pair (the certificate is the structured evidence)."""
    return KM.MappingVerdict(
        KM.ESTABLISHED, holding, cp["ticker"], slug,
        notes=["CANONICAL_CLAIM_CERTIFIED:%s" % cp["claim_fingerprint"]])


# ═════════════════════════════════════════════════════════════════════
# THE BOOK, THE WALK, THE FEE (pure)
# ═════════════════════════════════════════════════════════════════════

def levels(raw) -> list:
    """[(price, qty)] from a persisted ladder ([[price, qty], ...]); an
    unreadable level is dropped, never guessed."""
    out = []
    for x in _jl(raw) or []:
        try:
            p, q = Decimal(str(x[0])), int(Decimal(str(x[1])))
        except Exception:                                       # noqa: BLE001
            continue
        if q > 0 and Decimal(0) < p < Decimal(1):
            out.append((p, q))
    return out


def walk_asks(asks: list, limit: Decimal, qty: int) -> tuple:
    """(fills [(price, qty)], filled): the asks at or under `limit`, best
    (lowest) first, for at most `qty` contracts."""
    fills, need = [], int(qty)
    for p, q in sorted(asks):
        if need <= 0 or p > limit:
            break
        take = min(int(q), need)
        fills.append((p, take))
        need -= take
    return fills, int(qty) - need


def fee_of(fills: list, terms: dict) -> Decimal:
    """kalshi_fees.taker_fee per fill level (each level is its own trade);
    raises ValueError when the terms do not price."""
    return sum((KF.taker_fee(q, p, terms) for p, q in fills), Decimal(0))


def fair_probability(decision: dict) -> tuple:
    """(p, basis) of the HELD side as the decision recorded it: p_blended
    (Derek's blend), else p_pinnacle (the exploration / completed-game
    policies record the held side's Pinnacle probability there)."""
    for key in ("p_blended", "p_pinnacle"):
        p = _d(decision.get(key))
        if p is not None and Decimal(0) < p < Decimal(1):
            return p, key
    return None, None


# ═════════════════════════════════════════════════════════════════════
# ONE DECISION -> ONE ROW (pure)
# ═════════════════════════════════════════════════════════════════════

def _epoch(v) -> float | None:
    if v is None:
        return None
    if hasattr(v, "timestamp"):
        return v.timestamp()
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def base_row(decision: dict, order: dict, *, control: dict,
             buying_power: dict, now: float) -> dict:
    """The decision's identity and paper figures, EXCLUDED until a plan
    says otherwise (evaluate fills the rest)."""
    did = decision["decision_id"]
    slug = order.get("us_market_slug") or decision.get("us_market_slug")
    holding = str(order.get("holding_side") or decision.get("holding_side")
                  or "").upper()
    decided = _epoch(decision.get("decided_at"))
    age = None if decided is None else max(0.0, now - decided)
    qty, wire, lim = (_d(order.get("qty")), _d(order.get("wire_price")),
                      _d(order.get("limit_price")))
    scale = _d(control.get("scale")) or Decimal(1000)
    expires = _epoch(order.get("expires_at"))
    return {
        "shadow_id": shadow_id(did), "paper_decision_id": did,
        "paper_order_id": order["order_id"],
        "account_id": decision.get("account_id") or order.get("account_id"),
        "strategy": strategy_of(decision, order),
        "us_market_slug": slug, "holding": holding,
        "intent": KO.norm_intent(order.get("intent")),
        "paper_decided_at": decided, "planned_at": now,
        "decision_age_s": None if age is None else round(age, 3),
        "state": "EXCLUDED", "exclusion": None, "plan_exclusion": None,
        "ticker": None, "claim_fingerprint": None, "counterpart": {},
        "paper_qty": qty, "paper_wire_price": wire,
        "paper_notional_usd": (qty * lim) if qty is not None
        and lim is not None else None,
        "scale": scale, "raw_scaled_qty": None, "live_qty": 0,
        "rounding_delta": None, "venue_minimum": KO.VENUE_MINIMUM,
        "limit_price": None, "exec_vwap": None, "fee_usd": None,
        "all_in_usd": None, "all_in_per_contract": None,
        "fair_p": None, "fair_basis": None, "ev_per_contract": None,
        "ev_usd": None, "book": None, "fee_terms": None,
        "buying_power": buying_power, "plan_payload": None,
        "detail": {"counterpart_evaluated_at": now,
                   "max_decision_age_s": MAX_DECISION_AGE_S,
                   "book_sla_s": BOOK_SLA_S,
                   "policy_version": policy_version_of(decision, order),
                   # recorded, never decisive (module docstring: freshness)
                   "paper_order_state_at_plan": order.get("state"),
                   "paper_order_filled_qty_at_plan": (
                       None if _d(order.get("filled_qty")) is None
                       else str(_d(order.get("filled_qty")))),
                   "paper_order_expires_at": expires,
                   "paper_notional_over_scale": (
                       str((qty * lim) / scale) if qty is not None
                       and lim is not None else None)},
        "planner_version": VERSION}


def strategy_of(decision: dict, order: dict):
    return decision.get("strategy") or order.get("strategy")


def policy_version_of(decision: dict, order: dict):
    return decision.get("policy_version") or order.get("policy_version")


def eligibility(decision: dict, order: dict) -> tuple:
    """(eligible, evidence) under the owner's allowlist -- execmirror.
    live_eligibility itself, never a copy -- for the decision's ENTRY BUY
    (the version check applies: new exposure)."""
    return EM.live_eligibility({
        "strategy": strategy_of(decision, order),
        "decision_policy_version": policy_version_of(decision, order),
        "role": PLAN_ROLE})


def evaluate(decision: dict, order: dict, *, pmus_aliases: list,
             kalshi_aliases: list, book: dict | None, fee_terms: dict | None,
             control: dict, buying_power: dict, now: float) -> dict:
    """The kalshi_shadow_intents row for one linked paper decision. Never
    raises on its inputs: an input it cannot read is a named exclusion."""
    row = base_row(decision, order, control=control,
                   buying_power=buying_power, now=now)
    did, slug, holding = row["paper_decision_id"], row["us_market_slug"], \
        row["holding"]
    qty, wire = row["paper_qty"], row["paper_wire_price"]
    scale = row["scale"]
    decided = row["paper_decided_at"]
    age = None if decided is None else max(0.0, now - decided)

    def excluded(code, **detail):
        row["state"], row["exclusion"] = "EXCLUDED", code
        row["live_qty"], row["plan_payload"] = 0, None
        row["detail"].update(detail)
        return row

    # 1 · THE OWNER'S LIVE-ELIGIBILITY ALLOWLIST, before anything else: a
    #     PAPER-ONLY strategy or an unpromoted version is never given a
    #     counterpart, a plan, a price or an EV
    eligible, why_not = eligibility(decision, order)
    row["detail"]["live_eligibility"] = why_not
    if not eligible:
        return excluded(R_KSH_STRATEGY_NOT_LIVE_ELIGIBLE,
                        why="the owner's live-eligibility allowlist "
                            "(execmirror.live_eligibility): %s" %
                            why_not.get("class"))
    if holding not in ("LONG", "SHORT") or not slug:
        return excluded(R_KSH_MAPPING_NOT_ESTABLISHED,
                        why="the paper order names no held side or market")
    cp = counterpart(slug, holding, pmus_aliases, kalshi_aliases)
    row["counterpart"] = cp
    if not cp["ok"]:
        return excluded(cp["code"], why=cp.get("why"))
    row["ticker"], row["claim_fingerprint"] = cp["ticker"], cp[
        "claim_fingerprint"]
    target = target_for(cp, slug=slug, holding=holding)
    bk = book or {}
    asks, bids = levels(bk.get("yes_asks")), levels(bk.get("yes_bids"))
    b_at = _epoch(bk.get("observed_at"))
    b_age = None if b_at is None else max(0.0, now - b_at)
    fresh = bool(bk.get("readable")) and b_age is not None \
        and b_age <= BOOK_SLA_S
    row["book"] = None if not bk else {
        "ticker": bk.get("ticker"), "readable": bk.get("readable"),
        "basis": bk.get("book_basis"), "observed_at": b_at,
        "age_s": None if b_age is None else round(b_age, 3),
        "best_ask": str(min(asks)[0]) if asks else None,
        "best_bid": str(max(bids)[0]) if bids else None,
        "asks_levels": len(asks), "error": bk.get("error")}
    ref = None
    if fresh:
        ref = {"best_ask": min(asks)[0] if asks else None,
               "best_bid": max(bids)[0] if bids else None}
    plan_order = {
        "mirror_id": row["shadow_id"], "order_id": order["order_id"],
        "intent": order.get("intent"), "wire_price": wire,
        "qty": qty, "time_in_force": order.get("time_in_force"),
        "order_type": order.get("order_type"),
        "expires_at": order.get("expires_at"), "role": order.get("role"),
        "us_market_slug": slug, "decision_id": did,
        "decided_at": decision.get("decided_at")}
    if qty is None or wire is None:
        return excluded(R_KSH_PLAN_EXCLUDED,
                        why="the paper order's quantity or wire price is "
                            "unreadable")
    try:
        plan = KO.plan(plan_order, target, scale=scale,
                       max_order_usd=_d(control.get("max_order_usd")) or 25,
                       buying_power=buying_power.get("usd"), book=ref)
    except Exception as exc:                                    # noqa: BLE001
        return excluded(R_KSH_PLAN_EXCLUDED,
                        why="kalshi_orders.plan raised on this order",
                        error=type(exc).__name__)
    link = KL.build_link(dict(plan_order, decision_id=did), plan,
                         paper_decision_id=did, mirror_id=row["shadow_id"])
    px0 = _d(plan.get("price"))
    row.update(raw_scaled_qty=link.get("raw_scaled_qty"),
               rounding_delta=link.get("rounding_delta"),
               venue_minimum=link.get("venue_minimum") or KO.VENUE_MINIMUM,
               # the column holds a price on the venue's range only; an
               # off-range price (PRICE_OUT_OF_RANGE) stays in detail.plan
               limit_price=px0 if px0 is not None
               and KO.MIN_PRICE <= px0 <= KO.MAX_PRICE else None)
    row["detail"]["plan"] = {k: plan.get(k) for k in (
        "state", "exclusion", "price", "leg_price_exact",
        "price_rounding_delta", "live_qty", "live_notional", "fee_estimate",
        "post_only", "detail")}
    if plan.get("state") != "PLANNED":
        row["plan_exclusion"] = plan.get("exclusion")
        return excluded(PLAN_CODES.get(plan.get("exclusion"),
                                       R_KSH_PLAN_EXCLUDED),
                        plan_live_qty=plan.get("live_qty"))
    live, px = int(plan["live_qty"]), Decimal(str(plan["price"]))
    fair, basis = fair_probability(decision)
    row["fair_p"], row["fair_basis"] = fair, basis
    if fair is None:
        return excluded(R_KSH_NO_FAIR_PROBABILITY, plan_live_qty=live)
    if age is None or age > MAX_DECISION_AGE_S:
        return excluded(R_KSH_DECISION_STALE_AT_PLAN, plan_live_qty=live)
    # the paper order's own expiry (the mirror's INTENT_STALE): judged with
    # the decision's freshness, after the counterpart and the plan, so the
    # row still says what the venue side would have been
    expires = row["detail"].get("paper_order_expires_at")
    if expires is not None and expires <= now:
        return excluded(R_KSH_PAPER_ORDER_EXPIRED_BEFORE_PLAN,
                        plan_live_qty=live,
                        why="the paper order expired before the plan",
                        expired_s_before_plan=round(now - expires, 3))
    if not bk or not bk.get("readable") or b_at is None:
        return excluded(R_KSH_BOOK_UNAVAILABLE, plan_live_qty=live)
    if not fresh:
        return excluded(R_KSH_BOOK_STALE, plan_live_qty=live)
    if plan.get("post_only"):
        # a resting order rests at its limit (plan checked it does not cross)
        fills, filled, basis_fee = [(px, live)], live, FEE_BASIS_MAKER_BOUND
    else:
        fills, filled = walk_asks(asks, px, live)
        basis_fee = FEE_BASIS_TAKER
        if not fills:
            return excluded(R_KSH_NOT_EXECUTABLE_AT_LIMIT, plan_live_qty=live)
        if filled < live:
            return excluded(R_KSH_INSUFFICIENT_DEPTH_AT_LIMIT,
                            plan_live_qty=live, depth_at_limit=filled)
    row["fee_terms"] = fee_terms
    if not fee_terms or not fee_terms.get("priced"):
        return excluded(R_KSH_FEE_TERMS_UNKNOWN, plan_live_qty=live)
    try:
        fee = fee_of(fills, fee_terms)
    except ValueError as exc:
        return excluded(R_KSH_FEE_TERMS_UNKNOWN, plan_live_qty=live,
                        fee_error=str(exc))
    principal = sum((p * q for p, q in fills), Decimal(0))
    all_in = principal + fee
    # six decimals, the columns' scale, every rounding AGAINST the plan: the
    # cost per contract up, the fair probability down -- so the EV stored is
    # the EV judged, and a sub-micro-dollar edge is never PLANNED
    per = q6(all_in / live, ROUND_CEILING)
    vwap = q6(principal / live, ROUND_CEILING)
    fair6 = q6(fair, ROUND_FLOOR)
    row["fair_p"] = fair6
    ev_gross, ev_net = fair6 - vwap, fair6 - per
    row.update(exec_vwap=vwap, fee_usd=fee, all_in_usd=all_in,
               all_in_per_contract=per, ev_per_contract=ev_net,
               ev_usd=ev_net * live)
    row["detail"].update(fills=[[str(p), q] for p, q in fills],
                         fee_basis=basis_fee,
                         ev_gross_per_contract=str(ev_gross))
    if ev_net <= 0:
        return excluded(R_KSH_FEE_MAKES_EV_NEGATIVE if ev_gross > 0
                        else R_KSH_EV_NOT_POSITIVE, plan_live_qty=live)
    row.update(state="PLANNED", exclusion=None, live_qty=live,
               plan_payload=plan["payload"])
    row["detail"]["live_admission"] = LIVE_ADMISSION_NOTE
    return row


#: a PLANNED SHADOW row is a would-be order, never a live admission
LIVE_ADMISSION_NOTE = {
    "evaluated": False,
    "why": "SHADOW plan only: no Kalshi live book-currency rule or Kalshi "
           "settlement-gate approval exists (actual_admission governs the "
           "Polymarket US lane), Kalshi live money is OFF and nothing here "
           "can submit"}


# ═════════════════════════════════════════════════════════════════════
# THE DATABASE PARTS
# ═════════════════════════════════════════════════════════════════════

async def _has(conn, t: str) -> bool:
    return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", t))


async def gate(conn, *, env=None, transport=None) -> dict:
    """Stand down unless every Kalshi live-money switch is OFF and the
    tables exist. Reads only."""
    out = {"stand_down": None}
    for t in ("kalshi_shadow_intents", "kalshi_smalllive_control",
              "small_live_control", "paper_decisions", "paper_orders"):
        if not await _has(conn, t):
            out.update(stand_down=R_KSH_SCHEMA_ABSENT, missing=t)
            return out
    ctl = await conn.fetchrow(
        "SELECT enabled, stopped, scale, max_order_usd, kalshi_env, "
        "       (key_fingerprint IS NOT NULL) AS has_key "
        "  FROM kalshi_smalllive_control WHERE id = 1")
    if ctl is None:
        out.update(stand_down=R_KSH_SCHEMA_ABSENT,
                   missing="kalshi_smalllive_control row")
        return out
    out["control"] = dict(ctl)
    if ctl["enabled"]:
        out["stand_down"] = R_KSH_STOOD_DOWN_CONTROL_ENABLED
        return out
    mode = await conn.fetchval("SELECT mode FROM small_live_control "
                               "WHERE id = 1")
    out["small_live_mode"] = mode
    if mode != "SHADOW":
        out["stand_down"] = R_KSH_STOOD_DOWN_SMALL_LIVE_NOT_SHADOW
        return out
    cred = credential(env=env, transport=transport)
    out["credential"] = cred
    if cred.get("smalllive_enabled_env"):
        out["stand_down"] = R_KSH_STOOD_DOWN_ENV_SWITCH_ON
    return out


def credential(*, env=None, transport=None) -> dict:
    """Presence / shape / fingerprint only (kalshi_venue.credential_state
    through the read-only client): never a value, length or prefix."""
    cs = KA.read_only_client(env, transport=transport).credential_state()
    return {"state": cs.get("state"), "environment": cs.get("environment"),
            "key_fingerprint": cs.get("key_fingerprint"),
            "names": cs.get("names"),
            "smalllive_enabled_env": bool(cs.get("smalllive_enabled_env"))}


#: the decision's strategy and DECIDING POLICY VERSION, read exactly as
#: execmirror.plan_new reads them for live_eligibility (the version from the
#: decision, else the paper order's label), scoped to the mirrored account
POPULATION_SQL = """
SELECT d.decision_id, d.account_id, coalesce(d.strategy, o.strategy) AS strategy,
       coalesce(d.policy_version, o.label->>'policy_version') AS policy_version,
       d.decided_at, d.us_market_slug, d.holding_side, d.p_blended, d.p_pinnacle,
       o.order_id, o.account_id AS order_account_id, o.intent, o.qty,
       o.wire_price, o.limit_price, o.time_in_force, o.order_type,
       o.expires_at, o.role, o.holding_side AS order_holding_side,
       o.us_market_slug AS order_slug, o.strategy AS order_strategy,
       o.state AS order_state, o.filled_qty AS order_filled_qty
  FROM paper_decisions d
  JOIN LATERAL (SELECT x.order_id, x.account_id, x.intent, x.qty, x.wire_price,
                       x.limit_price, x.time_in_force, x.order_type,
                       x.expires_at, x.role, x.holding_side, x.us_market_slug,
                       x.strategy, x.label, x.state, x.filled_qty
                  FROM paper_orders x
                 WHERE x.decision_id = d.decision_id AND x.role = 'ENTRY'
                   AND x.direction = 'BUY'
                 ORDER BY x.created_at, x.order_id LIMIT 1) o ON true
 WHERE d.verdict = 'ENTER' AND d.decided_at > to_timestamp($1)
   AND d.decided_at <= to_timestamp($3) AND d.account_id = $4
   AND NOT EXISTS (SELECT 1 FROM kalshi_shadow_intents s
                    WHERE s.paper_decision_id = d.decision_id)
 ORDER BY d.decided_at DESC, d.decision_id
 LIMIT $2"""


UNLINKED_SQL = """
SELECT count(*) FROM paper_decisions d
 WHERE d.verdict = 'ENTER' AND d.decided_at > to_timestamp($1)
   AND d.decided_at <= to_timestamp($2) AND d.account_id = $3
   AND NOT EXISTS (SELECT 1 FROM paper_orders x
                    WHERE x.decision_id = d.decision_id AND x.role = 'ENTRY'
                      AND x.direction = 'BUY')"""

#: ENTER decisions of any other paper account in the window: not mirrored,
#: never given a row, counted so the scope is never silent
OTHER_ACCOUNTS_SQL = """
SELECT count(*) FROM paper_decisions d
 WHERE d.verdict = 'ENTER' AND d.decided_at > to_timestamp($1)
   AND d.decided_at <= to_timestamp($2) AND d.account_id <> $3"""


async def population(conn, *, now: float, window_s: float,
                     paper_account: str = PAPER_ACCOUNT,
                     limit: int = MAX_DECISIONS_PER_PASS) -> list:
    out = []
    for r in await conn.fetch(POPULATION_SQL, now - window_s, limit,
                              now + CLOCK_SKEW_S, paper_account):
        r = dict(r)
        decision = {"decision_id": r["decision_id"],
                    "account_id": r["account_id"], "strategy": r["strategy"],
                    "policy_version": r["policy_version"],
                    "decided_at": r["decided_at"],
                    "us_market_slug": r["us_market_slug"],
                    "holding_side": r["holding_side"],
                    "p_blended": r["p_blended"], "p_pinnacle": r["p_pinnacle"]}
        order = {"order_id": r["order_id"],
                 "account_id": r["order_account_id"], "intent": r["intent"],
                 "qty": r["qty"], "wire_price": r["wire_price"],
                 "limit_price": r["limit_price"],
                 "time_in_force": r["time_in_force"],
                 "order_type": r["order_type"], "expires_at": r["expires_at"],
                 "role": r["role"], "holding_side": r["order_holding_side"],
                 "us_market_slug": r["order_slug"],
                 "strategy": r["order_strategy"], "state": r["order_state"],
                 "filled_qty": r["order_filled_qty"]}
        out.append((decision, order))
    return out


async def aliases(conn, slugs: list) -> tuple:
    """(PMUS aliases of these slugs, the Kalshi aliases sharing their claim
    fingerprints). Without migration 315's certificate column no alias is
    certified (fail-closed)."""
    if not slugs or not await _has(conn, "canonical_claim_aliases"):
        return [], []
    cols = {r["column_name"] for r in await conn.fetch(
        "SELECT column_name FROM information_schema.columns WHERE "
        "table_name = 'canonical_claim_aliases'")}
    extra = ", certificate_status, rules_sha256" if {
        "certificate_status", "rules_sha256"} <= cols else \
        ", NULL::text AS certificate_status, NULL::text AS rules_sha256"
    base = ("SELECT alias_key, venue, market_id, side, claim_fingerprint, "
            "mapping_status, settlement_status, updated_at%s "
            "FROM canonical_claim_aliases " % extra)
    pm = [dict(r) for r in await conn.fetch(
        base + "WHERE venue = $1 AND market_id = ANY($2::text[])",
        PMUS_VENUE, sorted(set(slugs)))]
    fps = sorted({a["claim_fingerprint"] for a in pm if certified(a)})
    ks = [dict(r) for r in await conn.fetch(
        base + "WHERE venue = $1 AND claim_fingerprint = ANY($2::text[])",
        KALSHI_VENUE, fps)] if fps else []
    return pm, ks


async def books(conn, tickers: list) -> dict:
    if not tickers or not await _has(conn, "kalshi_books_current"):
        return {}
    return {r["ticker"]: dict(r) for r in await conn.fetch(
        "SELECT ticker, event_ticker, series_ticker, yes_asks, yes_bids, "
        "       readable, error, book_basis, observed_at "
        "  FROM kalshi_books_current WHERE ticker = ANY($1::text[])",
        sorted(set(tickers)))}


async def buying_power(conn, *, now: float) -> dict:
    """The balance of the newest COMPLETE read-only reconciliation when it is
    inside the gate's own age bound; otherwise unread (plan then applies no
    cash check, and the row says so)."""
    if not await _has(conn, "kalshi_account_reconciliations"):
        return {"usd": None, "status": "UNREAD",
                "why": "kalshi_account_reconciliations absent"}
    r = await conn.fetchrow(
        "SELECT reconciliation_id, at, balance_usd, verdict "
        "  FROM kalshi_account_reconciliations WHERE complete "
        "   AND at <= to_timestamp($1::float8) "
        " ORDER BY at DESC LIMIT 1", now + CLOCK_SKEW_S)
    if r is None:
        return {"usd": None, "status": "UNREAD",
                "why": "NO_COMPLETE_READ_ONLY_RECONCILIATION"}
    age = now - r["at"].timestamp()
    if age > BUYING_POWER_MAX_AGE_S or age < -CLOCK_SKEW_S \
            or r["balance_usd"] is None:
        return {"usd": None, "status": "UNREAD",
                "why": "NO_FRESH_COMPLETE_READ_ONLY_RECONCILIATION",
                "reconciliation_id": r["reconciliation_id"],
                "age_s": round(age, 3)}
    return {"usd": r["balance_usd"], "status": "READ",
            "reconciliation_id": r["reconciliation_id"],
            "verdict": r["verdict"], "age_s": round(age, 3)}


COLUMNS = (
    "shadow_id", "paper_decision_id", "paper_order_id", "account_id",
    "strategy", "us_market_slug", "holding", "intent", "paper_decided_at",
    "planned_at", "decision_age_s", "state", "exclusion", "plan_exclusion",
    "ticker", "claim_fingerprint", "counterpart", "paper_qty",
    "paper_wire_price", "paper_notional_usd", "scale", "raw_scaled_qty",
    "live_qty", "rounding_delta", "venue_minimum", "limit_price",
    "exec_vwap", "fee_usd", "all_in_usd", "all_in_per_contract", "fair_p",
    "fair_basis", "ev_per_contract", "ev_usd", "book", "fee_terms",
    "buying_power", "plan_payload", "detail", "planner_version")
_JSON = {"counterpart", "book", "fee_terms", "buying_power", "plan_payload",
         "detail"}
_TS = {"paper_decided_at", "planned_at"}


def _sql_value(c, v):
    if c in _JSON:
        return None if v is None else _j(v)
    if c in _TS:
        return v
    if c in ("live_qty", "venue_minimum"):
        return None if v is None else int(v)
    if isinstance(v, (Decimal, int, float)) and not isinstance(v, bool) \
            and c not in ("shadow_id",):
        return Decimal(str(v))
    return v


INSERT_SQL = "INSERT INTO kalshi_shadow_intents (%s, mode, production_effect) "\
    "VALUES (%s, 'SHADOW', 'NONE') ON CONFLICT DO NOTHING RETURNING shadow_id" % (
        ", ".join(COLUMNS),
        ", ".join(("to_timestamp($%d::float8)" % (i + 1)) if c in _TS
                  else ("$%d::jsonb" % (i + 1)) if c in _JSON
                  else "$%d" % (i + 1) for i, c in enumerate(COLUMNS)))


async def write_row(conn, row: dict) -> bool:
    got = await conn.fetchval(INSERT_SQL, *[_sql_value(c, row.get(c))
                                            for c in COLUMNS])
    return got is not None


async def _fee_terms(conn, key: tuple, *, now: float, counts: dict):
    """The fee terms of one (series, event), read in its own savepoint: a
    read that fails is that market's FEE_TERMS_UNKNOWN (the error named on
    the row), never the whole pass's transaction."""
    try:
        async with conn.transaction():
            return await CCDB.kalshi_fee_terms(conn, key[0], key[1], now=now)
    except asyncio.CancelledError:
        raise
    except Exception as exc:                                    # noqa: BLE001
        counts["read_errors"].append(type(exc).__name__)
        return {"priced": False, "why": "FEE_TERMS_READ_FAILED",
                "error": type(exc).__name__, "series_ticker": key[0],
                "event_ticker": key[1]}


async def plan_pass(conn, *, now: float, control: dict,
                    paper_account: str = PAPER_ACCOUNT) -> dict:
    """Plan every linked decision of the mirrored paper account not yet
    planned (bounded), inside the caller's transaction. Returns the pass's
    counts; the caller records coverage (note_committed) only after its
    transaction commits."""
    window = window_s(now)
    pop = await population(conn, now=now, window_s=window,
                           paper_account=paper_account)
    # the allowlist first: a paper-only decision is never given a
    # counterpart, a book read or a fee read
    elig = [eligibility(d, o)[0] for d, o in pop]
    pm, ks = await aliases(conn, [o.get("us_market_slug") or d.get(
        "us_market_slug") for (d, o), ok in zip(pop, elig) if ok])
    pre = [(d, o, counterpart(o.get("us_market_slug") or d["us_market_slug"],
                              str(o.get("holding_side") or "").upper(),
                              pm, ks) if ok else {"ok": False})
           for (d, o), ok in zip(pop, elig)]
    bks = await books(conn, [cp["ticker"] for _d, _o, cp in pre if cp["ok"]])
    bp = await buying_power(conn, now=now)
    terms_cache: dict = {}
    counts = {"seen": len(pop), "window_s": window,
              "paper_account": paper_account, "written": 0,
              "planned": 0, "excluded": {}, "errors": [], "read_errors": [],
              "not_live_eligible": elig.count(False),
              # ENTER decisions with no ENTRY BUY paper order are not linked
              # (nothing to mirror): counted here, never given a row
              "enter_without_entry_order": await conn.fetchval(
                  UNLINKED_SQL, now - window, now + CLOCK_SKEW_S,
                  paper_account),
              # ENTER decisions of other paper accounts: out of scope
              "enter_other_accounts": await conn.fetchval(
                  OTHER_ACCOUNTS_SQL, now - window, now + CLOCK_SKEW_S,
                  paper_account)}
    for d, o, cp in pre:
        bk, terms = None, None
        if cp["ok"]:
            bk = bks.get(cp["ticker"])
            if bk is not None:
                key = (bk.get("series_ticker"), bk.get("event_ticker"))
                if key not in terms_cache:
                    terms_cache[key] = await _fee_terms(conn, key, now=now,
                                                        counts=counts)
                terms = terms_cache[key]
        try:
            row = evaluate(d, o, pmus_aliases=pm, kalshi_aliases=ks, book=bk,
                           fee_terms=terms, control=control, buying_power=bp,
                           now=now)
        except Exception as exc:                                # noqa: BLE001
            row = _failed_row(d, o, control=control, buying_power=bp,
                              now=now, error=type(exc).__name__)
        # ONE DECISION NEVER STALLS THE PASS: each write in its own
        # savepoint; a row the database refuses is replaced by the bare
        # named exclusion, and only if that too is refused is the decision
        # left for the next pass (counted)
        written = None
        for attempt in (row, None):
            if attempt is None:
                attempt = _failed_row(d, o, control=control, buying_power=bp,
                                      now=now, error=counts["errors"][-1]
                                      if counts["errors"] else None)
            try:
                async with conn.transaction():
                    written = await write_row(conn, attempt)
                row = attempt
                break
            except asyncio.CancelledError:
                raise
            except Exception as exc:                            # noqa: BLE001
                counts["errors"].append(type(exc).__name__)
        if written:
            counts["written"] += 1
            if row["state"] == "PLANNED":
                counts["planned"] += 1
            else:
                counts["excluded"][row["exclusion"]] = \
                    counts["excluded"].get(row["exclusion"], 0) + 1
    return counts


def _failed_row(decision: dict, order: dict, *, control: dict,
                buying_power: dict, now: float, error) -> dict:
    """The bare named exclusion for a decision whose evaluation or write
    failed: its identity, R_KSH_PLAN_EXCLUDED and the error's type."""
    row = base_row(decision, order, control=control,
                   buying_power=buying_power, now=now)
    row.update(state="EXCLUDED", exclusion=R_KSH_PLAN_EXCLUDED, live_qty=0,
               plan_payload=None)
    row["detail"].update(why="the plan could not be evaluated or recorded",
                         error=error)
    return row


# ── the read-only reconciliation writer ──────────────────────────────

def read_id(now: float) -> str:
    return "kshadow-acct-%d" % int(now // RECON_EVERY_S)


async def _write_unavailable(conn, rid: str, now: float, reason: str,
                             cred: dict, detail: dict | None = None) -> bool:
    got = await conn.fetchval(
        "INSERT INTO kalshi_shadow_account_reads (read_id, at, outcome, "
        " reason, credential, detail, planner_version) VALUES ($1, "
        " to_timestamp($2::float8), 'UNAVAILABLE', $3, $4::jsonb, $5::jsonb,"
        " $6) ON CONFLICT DO NOTHING RETURNING read_id",
        rid, now, reason, _j(cred), _j(detail or {}), VERSION)
    return got is not None


async def account_read(pool, *, now: float, env=None, transport=None) -> dict:
    """Once per RECON_EVERY_S window: a read-only reconciliation when a
    Kalshi credential is PRESENT in this process, else a named UNAVAILABLE
    row. The network read holds no database connection; the write is one
    transaction under its own advisory lock, re-checking the window."""
    rid = read_id(now)
    async with pool.acquire() as conn:
        if not await _has(conn, "kalshi_shadow_account_reads"):
            return {"outcome": None, "why": R_KSH_SCHEMA_ABSENT}
        if await conn.fetchval("SELECT 1 FROM kalshi_shadow_account_reads "
                               "WHERE read_id = $1", rid):
            return {"outcome": "WINDOW_DONE", "read_id": rid}
    client = KA.read_only_client(env, transport=transport)
    cs = client.credential_state()
    cred = {"state": cs.get("state"), "environment": cs.get("environment"),
            "key_fingerprint": cs.get("key_fingerprint"),
            "names": cs.get("names")}
    snap, reason = None, None
    if cs.get("state") != "PRESENT":
        reason = cs.get("state") or "KALSHI_CREDENTIAL_ABSENT"
    else:
        def _read():
            return KA.snapshot(KA.fetch_responses(client,
                                                  max_pages=RECON_MAX_PAGES),
                               key_fingerprint=cs.get("key_fingerprint"),
                               kalshi_env=cs.get("environment"), at=now)
        try:
            snap = await asyncio.wait_for(asyncio.to_thread(_read),
                                          RECON_TIMEOUT_S)
        except TimeoutError:
            reason = R_KSH_ACCOUNT_READ_TIMEOUT
    async with pool.acquire() as conn:
        async with conn.transaction():
            if not await conn.fetchval("SELECT pg_try_advisory_xact_lock($1)",
                                       ACCOUNT_LOCK_KEY):
                return {"outcome": "LOCK_HELD", "read_id": rid}
            if await conn.fetchval("SELECT 1 FROM kalshi_shadow_account_reads"
                                   " WHERE read_id = $1", rid):
                return {"outcome": "WINDOW_DONE", "read_id": rid}
            if snap is None:
                await _write_unavailable(conn, rid, now, reason, cred)
                return {"outcome": "UNAVAILABLE", "reason": reason,
                        "read_id": rid}
            rec = KA.reconciliation_record(snap, baseline_accepted=False,
                                           actor=RECON_ACTOR)
            recon_id = await conn.fetchval(
                "INSERT INTO kalshi_account_reconciliations (at, "
                " key_fingerprint, kalshi_env, verdict, complete, read_only, "
                " balance_usd, positions, resting_orders, fills_recent, "
                " settlements_recent, errors, baseline_accepted, actor) "
                "VALUES (to_timestamp($1::float8), $2, $3, $4, $5, true, $6, "
                " $7::jsonb, $8::jsonb, $9, $10, $11::jsonb, false, $12) "
                "RETURNING reconciliation_id",
                float(rec["at"]), rec["key_fingerprint"], rec["kalshi_env"],
                rec["verdict"], rec["complete"], _d(rec["balance_usd"]),
                _j(rec["positions"]), _j(rec["resting_orders"]),
                rec["fills_recent"], rec["settlements_recent"],
                _j(rec["errors"]), rec["actor"])
            await conn.execute(
                "INSERT INTO kalshi_shadow_account_reads (read_id, at, "
                " outcome, reconciliation_id, verdict, credential, detail, "
                " planner_version) VALUES ($1, to_timestamp($2::float8), "
                " 'RECORDED', $3, $4, $5::jsonb, $6::jsonb, $7)",
                rid, now, recon_id, rec["verdict"], _j(cred),
                _j({"errors": rec["errors"], "complete": rec["complete"]}),
                VERSION)
            return {"outcome": "RECORDED", "reconciliation_id": recon_id,
                    "verdict": rec["verdict"], "read_id": rid}


# ── the pass and the loop ────────────────────────────────────────────

async def totals(conn) -> dict:
    out = {"PLANNED": 0, "EXCLUDED": {}}
    for r in await conn.fetch(
            "SELECT state, exclusion, count(*) AS n FROM kalshi_shadow_intents"
            " GROUP BY 1, 2"):
        if r["state"] == "PLANNED":
            out["PLANNED"] += int(r["n"])
        else:
            out["EXCLUDED"][r["exclusion"]] = int(r["n"])
    return out


async def _heartbeat(conn, status: str, detail: dict) -> None:
    try:
        from . import db as _db
        # Decimals and datetimes as text: db.heartbeat_json refuses any
        # other non-JSON type, which would lose the beat
        await _db.heartbeat(SERVICE, status, json.loads(_j(detail)),
                            con=conn)
    except Exception:                                           # noqa: BLE001
        log.warning("kalshi shadow: heartbeat failed", exc_info=True)


async def pass_once(pool, *, now: float | None = None, env=None,
                    transport=None, paper_account: str = PAPER_ACCOUNT) -> dict:
    """One SHADOW pass: gate, plan (one transaction under the planner's
    advisory lock; coverage recorded only once it commits), the window's
    account read, heartbeat."""
    now = time.time() if now is None else float(now)
    summary = {"version": VERSION, "mode": MODE, "at": now,
               "phase_errors": {}}
    async with pool.acquire() as conn:
        g = await gate(conn, env=env, transport=transport)
        summary["gate"] = {k: v for k, v in g.items() if k != "credential"}
        summary["credential_state"] = (g.get("credential") or {}).get("state")
        if g["stand_down"]:
            summary["status"] = "STOOD_DOWN"
            summary["stand_down"] = g["stand_down"]
            await _heartbeat(conn, "stood_down", summary)
            return summary
        try:
            async with conn.transaction():
                if await conn.fetchval("SELECT pg_try_advisory_xact_lock($1)",
                                       LOCK_KEY):
                    summary["planner"] = await plan_pass(
                        conn, now=now, control=g["control"],
                        paper_account=paper_account)
                else:
                    summary["planner"] = {"skipped": "LOCK_HELD"}
            # COMMITTED: only now may the window move forward
            if "skipped" not in summary["planner"]:
                note_committed(summary["planner"], now=now)
        except asyncio.CancelledError:
            raise
        except Exception as exc:                                # noqa: BLE001
            summary["phase_errors"]["plan"] = type(exc).__name__
            log.warning("kalshi shadow: plan phase failed", exc_info=True)
    try:
        summary["account_read"] = await account_read(
            pool, now=now, env=env, transport=transport)
    except asyncio.CancelledError:
        raise
    except Exception as exc:                                    # noqa: BLE001
        summary["phase_errors"]["account_read"] = type(exc).__name__
        log.warning("kalshi shadow: account read failed", exc_info=True)
    async with pool.acquire() as conn:
        try:
            summary["totals"] = await totals(conn)
        except Exception as exc:                                # noqa: BLE001
            summary["phase_errors"]["totals"] = type(exc).__name__
        summary["status"] = "ERROR" if summary["phase_errors"] else "OK"
        await _heartbeat(conn, "error" if summary["phase_errors"] else "ok",
                         summary)
    return summary


async def run(get_pool, *, interval_s: float = INTERVAL_S,
              first_delay_s: float = FIRST_DELAY_S) -> None:
    """The API-process loop (armed in api/app.py's lifespan beside the
    execution mirror's runner). Never raises into the API."""
    if not enabled():
        log.info("kalshi shadow: planner disabled (%s)", ENV_FLAG)
        return
    await asyncio.sleep(first_delay_s)
    while True:
        try:
            pool = await get_pool()
            async with asyncio.timeout(PASS_TIMEOUT_S + RECON_TIMEOUT_S):
                await pass_once(pool)
        except asyncio.CancelledError:
            raise
        except Exception as exc:                                # noqa: BLE001
            log.warning("kalshi shadow: pass failed (%s)", type(exc).__name__)
        await asyncio.sleep(interval_s)
