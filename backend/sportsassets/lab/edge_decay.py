"""EDGE DECAY & LATENCY ECONOMICS (LAB-A, SHADOW / RESEARCH ONLY) -- pure core.

THE OWNER'S QUESTION: how fast does an opportunity's EXECUTABLE edge decay
after BETTOR first detects it? A binary fresh/stale rule cannot answer it.

WHAT IS MEASURED, AND FROM WHAT (recorded observations only, never
interpolated, never fabricated; every row passes the lab's point-in-time
accessor `sportsassets.lab.pit` at the instant it is used):

  QUALIFIED OPPORTUNITIES  the completed-game INVESTMENT strategy's own
      pre-trade qualification (PINNACLE_COMPLETED_GAME_PAPER, every policy
      version), exactly these `paper_decisions` rows:
        ENTER      verdict ENTER (the strategy read a book and every check
                   passed);
        NEAR_MISS  verdict REFUSE, a book WAS read, every refusal is economic
                   (ECONOMIC_REFUSALS: below the minimum gross edge, fees
                   consume the edge, net EV not positive, no sized quantity)
                   and the best level's recorded gross edge was positive.
      DEREK_ENTRY_POLICY_V2 (the other INVESTMENT strategy) is COUNTED and
      reported NOT_MEASURABLE: its fair value is a blend with an internal
      model whose score at later prices is never recorded, so its edge at a
      later instant cannot be reconstructed without inventing it.

  DETECTION t0  the earliest instant BETTOR held BOTH the qualified
      probability and the executable book of the decision: the latest of the
      decision clock (paper_decisions.decided_at) and its own book's known
      instant (paper_book_observations observed_at / recorded_at). Measured
      on production (research-sql run 37231482741, 564 qualified rows): the
      decision's book was OBSERVED a median 0.15 s BEFORE the decision clock
      (range -4.35 .. +6.42 s) and RECORDED a median 0.22 s after it was
      observed (561 of 564 recorded later), so t0 is usually the book's
      recorded instant, a fraction of a second after the decision clock.

  EXECUTABLE ECONOMICS AT AN INSTANT tau  from the latest recorded book of the
      same market known at tau, and the latest Pinnacle probability of the
      same contract orientation known at tau that the LANE ITSELF QUALIFIED
      (the completed-game match's `probability_qualified_by_the_lane` and
      identity checks: no probability-stage or identity-stage lane refusal on
      the valuation row, with the policy's own not-applied rule for a
      PinnAPI sole-authority read -- `lane_probability_check`), which must be
      FRESH under the lane's own rule at tau (tau - provider stamp <= the
      decision's recorded limit_s, 30 s; never loosened). A later valuation
      whose lane refusals were not recorded in the evidence is NOT used
      (fail closed, counted). Two measures:
        TOP_NET_EDGE     per contract at the best level: p - price - fee per
                         contract (the deployed fee schedule), probability
                         points. Every qualified opportunity.
        DECIDED_ORDER_EV the DECIDED IOC order (its quantity and limit) walked
                         on that book: sum take x (p - price) - fees per fill,
                         dollars. ENTER only (a near miss decided no order).
      A book whose consumed side is EMPTY is a measured zero (nothing
      executable), not a missing value.
      A third measure isolates the venue side: VENUE_BOOK_AT_DETECTION_P --
      the same book priced at the DETECTION probability. It is a research
      decomposition (how fast the book moves away from the price BETTOR
      detected), labelled NOT_EXECUTABLE: it never stands in for a fresh
      probability and is never an input to any decision.

  HORIZONS  0, +1, +2, +5, +10, +15, +30, +60, +120, +300, +600 s. The
      evidence for horizon h_i is the LATEST book observed in
      (t0 + h_{i-1}, t0 + h_i] and known by t0 + h_i: the STATED TOLERANCE of
      a horizon is the gap to the previous one (1, 1, 3, 5, 5, 15, 30, 60,
      180, 300 s). No book is carried forward from an earlier window; a
      window with no recorded book is UNAVAILABLE (NO_RECORDED_BOOK_IN_
      WINDOW) and counted. Horizon 0 is the decision's own book.

  DECAY TIMES  (the vendored reference, sportsassets.lab.reference.
      edge_decay, called unchanged) on the samples [(seconds since t0, edge)]
      from EVERY recorded book known in (t0, t0 + 600 s] -- a sample's instant
      is the book's KNOWN instant (its latest recorded stamp), so a row
      recorded after it was observed counts only from then: 75% retention, half-life
      and time to zero executable edge. The DETECTION sample is always the
      first sample (t = 0); a later book is never placed at t = 0 (its offset
      keeps microsecond precision, at least 1 us), so it can never replace
      the detection edge. A crossing is the FIRST recorded
      observation at or below the target -- an UPPER bound; the previous
      observation is the LOWER bound. No crossing by the last observation is
      RIGHT_CENSORED at it; no observation after t0 is UNOBSERVED.

  LATENCY CHAIN  provider stamp -> our receipt -> valuation -> Derek's
      decision start -> Derek's decision complete -> Karen -> Allie -> Eddie
      -> canonical intent -> adapter (plus the decision book's receipt,
      `book_observed`, an auxiliary stamp: on production it usually precedes
      the decision clock). Historically Karen / Allie / Eddie were not
      pre-trade (they reviewed decisions after the fact; migration 225 is not
      deployed), so those stages are UNAVAILABLE with that reason. FORWARD,
      the reader consumes the canonical intent's stage stamps (`latency_
      stages`, the intent stream's column: pinnacle_observed_at, ingest_at,
      probability_qualified_at, book_observed_at, decision_start_at) and the
      PAPER adapter record's `refs.stages` (intent_recorded_at,
      paper_submit_at) whenever they are present (STAGE_KEYS below); a stage
      nobody stamps (Karen / Allie / Eddie completion today) stays
      UNAVAILABLE with its reason.

  EV LOST DURING BETTOR PROCESSING  (ENTER) the decided order's EV at
      detection minus its EV at the ORDER HANDOFF (the adapter stamp, else
      Derek's decision-complete stamp), on the latest readable book recorded
      INSIDE (t0, handoff] with the probability fresh then, in dollars at the
      decided quantity. No book recorded inside that window (the usual case:
      the window is a fraction of a second) is UNAVAILABLE
      (NO_BOOK_RECORDED_INSIDE_THE_PROCESSING_WINDOW) -- never a zero.

  DETECTION -> SIMULATED EXECUTION  (ENTER, a DIFFERENT window, labelled as
      such) the decided order's EV at detection minus its EV on the paper
      simulator's execution book -- the first recorded book at or after the
      order's recorded eligible instant (the simulator's configured venue
      delay) -- with the probability fresh then (when the simulator filled,
      the book it filled on). It spans the configured delay AND the paper
      path's book-read cadence, so it is NOT BETTOR processing. An ENTER with
      no paper order, an order the simulator expired because its first book
      after eligibility was UNREADABLE, an order with no readable book in its
      window, or an execution book observed before detection has NO
      execution evidence: UNAVAILABLE and counted by reason.

Nothing here decides, sizes, gates or sends anything. It imports nothing from
any order, venue, execution or funded module, and no decision path imports it
(tests/test_lab_edge_decay_import_guard.py).
"""
from __future__ import annotations

import datetime as _dt
import math
from decimal import Decimal, InvalidOperation

from . import pit as PIT
from . import stats as ST
from .reference import edge_decay as REF

VERSION = "LAB_EDGE_DECAY_V1"
AUTHORITY = "SHADOW_RESEARCH_ONLY"

CG_STRATEGY = "PINNACLE_COMPLETED_GAME_PAPER"
DEREK_V2 = "DEREK_ENTRY_POLICY_V2"
#: the INVESTMENT sleeve's strategies (pinned equal to
#: canonical_intent.STRATEGY_SLEEVE by a test)
INVESTMENT_STRATEGIES = (CG_STRATEGY, DEREK_V2)
#: the economic refusals of the completed-game decision (pinned equal to
#: paper_benchmark / derek_policy constants by a test)
ECONOMIC_REFUSALS = frozenset({
    "BELOW_MIN_GROSS_EDGE", "GROSS_EDGE_CLEARS_THRESHOLD_BUT_FEES_CONSUME_IT",
    "NET_EV_NOT_POSITIVE_AFTER_FEES", "NO_SIZED_QUANTITY"})

ENTER, NEAR_MISS = "ENTER", "NEAR_MISS"
HORIZONS_S = (0, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600)
MAX_HORIZON_S = HORIZONS_S[-1]
#: the lane's probability rule (pinned equal to
#: workers.ext_pinnacle_loop.PINNACLE_MAX_AGE_S); a decision's own recorded
#: limit_s is used when present and never a LARGER one
PROBABILITY_LIMIT_S = 30.0
#: the paper simulator's configured decision -> execution delay and
#: marketable time to live (bettor_paper_session defaults; pinned equal by a
#: test). REFERENCE VALUES ONLY: they are reported as the configuration and
#: NEVER substituted for an order's missing recorded eligible / expiry
#: instant (an ENTER without a paper order, or an order without its window,
#: is UNAVAILABLE with its reason -- never a synthesized window).
SIM_DELAY_S = 2.0
SIM_TTL_S = 90.0
#: THE LANE'S OWN PROBABILITY QUALIFICATION of a valuation row, as the
#: completed-game match applies it (paper_benchmark.completed_game_match:
#: `probability_qualified_by_the_lane` passes only with no probability-stage
#: lane refusal on the row, and C_IDENTITY only with no identity-stage lane
#: refusal; the policy does not apply OUTCOME_DEPTH_BELOW_FLOOR to a PinnAPI
#: sole-authority read). The stage of a code is read from the lane's own
#: classifier (bettor_external_shadow.STAGE_OF, a pure module the decision
#: code itself uses through derek_policy._lane_stage); the two policy
#: constants below are pinned equal to paper_benchmark's by a test, so the
#: lab never imports the paper path.
LANE_BLOCKING_STAGES = ("1_PROBABILITY", "3_IDENTITY")
THIN_OUTCOME = "OUTCOME_DEPTH_BELOW_FLOOR"
PINNAPI_PROVIDER = "pinnapi.com/raw-websocket"
#: the collector's quote-context vocabulary (bettor_settlement_terms
#: CTX_PRE_GAME / CTX_LIVE; pinned equal by a test), mapped BY EQUALITY. A
#: scheduled start (us_premap.game_start) never establishes the context --
#: the settlement-terms module refuses that inference by name -- so a row
#: without a recorded context is phase UNAVAILABLE, never guessed.
QUOTE_CONTEXT_PHASE = {"PRE_GAME": "PREGAME", "IN_PLAY": "LIVE"}
FEE_BLOCK = 10000
#: the paper simulator's terminal reasons that mean the order never met an
#: execution book (bettor_paper_simulator R_BOOK_UNREADABLE /
#: R_NO_BOOK_IN_WINDOW; pinned equal by a test)
SIM_NO_EXECUTION = {
    "THE_OBSERVED_BOOK_WAS_UNREADABLE":
        "FIRST_BOOK_AFTER_ELIGIBLE_WAS_UNREADABLE_ORDER_EXPIRED",
    "NO_BOOK_OBSERVED_BEFORE_THE_ORDER_EXPIRED":
        "NO_BOOK_OBSERVED_IN_THE_ORDER_WINDOW_ORDER_EXPIRED"}

MEASURED = "MEASURED"
UNAVAILABLE = "UNAVAILABLE"
FRESH, STALE, ABSENT = "FRESH", "STALE", "ABSENT"

TOP_NET_EDGE = "TOP_NET_EDGE"
DECIDED_ORDER_EV = "DECIDED_ORDER_EV"
VENUE_AT_P0 = "VENUE_BOOK_AT_DETECTION_P"

#: THE OWNER'S STAGE ORDER and, per stage, the stamp keys the reader looks
#: for (first present wins) in the canonical intent's `latency_stages` / the
#: PAPER adapter record's `refs.stages`.
STAGES = ("provider_observed", "bettor_receipt", "valuation_complete",
          "decision_start", "derek_complete", "karen_complete",
          "allie_complete", "eddie_complete", "canonical_intent_complete",
          "adapter_receipt")
#: the decision book's receipt: recorded, but NOT in the owner's sequence --
#: on production it usually precedes the decision clock (median -0.15 s)
AUX_STAGES = ("book_observed",)
#: the stamp keys, first present wins. The intent stream's
#: canonical_decision_intents.latency_stages carries pinnacle_observed_at,
#: ingest_at, probability_qualified_at, book_observed_at and
#: decision_start_at (plus a `basis` dict); the PAPER adapter record's
#: refs.stages carries intent_recorded_at and paper_submit_at (or
#: paper_submit_why). The *_complete_at keys are the per-component
#: completion stamps no writer produces yet -- consumed the moment one does.
STAGE_KEYS = {
    "provider_observed": ("pinnacle_observed_at",),
    "bettor_receipt": ("ingest_at", "pinnacle_received_at"),
    "valuation_complete": ("probability_qualified_at", "valuation_complete_at"),
    "decision_start": ("decision_start_at",),
    "book_observed": ("book_observed_at",),
    "derek_complete": ("derek_complete_at", "decision_complete_at"),
    "karen_complete": ("karen_complete_at",),
    "allie_complete": ("allie_complete_at",),
    "eddie_complete": ("eddie_complete_at",),
    "canonical_intent_complete": ("intent_built_at", "intent_recorded_at"),
    "adapter_receipt": ("adapter_receipt_at", "paper_submit_at"),
}
#: the stamps the canonical intent stream records today; a stage outside it
#: is a per-component completion stamp the intent stream does not write
INTENT_STREAM_STAMPS = ("pinnacle_observed_at", "ingest_at",
                        "probability_qualified_at", "book_observed_at",
                        "decision_start_at", "intent_recorded_at",
                        "paper_submit_at")
NOT_PRE_TRADE = ("NOT_PRE_TRADE_HISTORICALLY: Karen, Allie and Eddie reviewed "
                 "decisions after the fact; no canonical intent (migration "
                 "225) carried a pre-trade stage stamp")
NOT_STAMPED = "STAGE_NOT_STAMPED_ON_THE_CANONICAL_INTENT"


class FeeUnavailable(Exception):
    """The deployed schedule refused to price a fill (a blocker is never a
    zero fee)."""


# ─────────────────────────── small helpers ─────────────────────────────

def _f(v):
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else x


def _d(v):
    if v is None:
        return None
    if isinstance(v, dict):
        v = v.get("value")
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _cent(wire: float) -> bool:
    return abs(round(float(wire) * 100) - float(wire) * 100) < 1e-9


def _r(x, n=9):
    return None if x is None else round(float(x), n)


# ─────────────────────────── qualification ─────────────────────────────

def qualify(d: dict) -> dict:
    """THE QUALIFIED-OPPORTUNITY RULE on one paper_decisions row (pure)."""
    strat = d.get("strategy")
    if strat == DEREK_V2:
        return {"class": None, "why": "NOT_MEASURABLE_BLENDED_FAIR_VALUE",
                "detail": ("the fair value blends an internal model whose "
                           "score at a later price is never recorded")}
    if strat != CG_STRATEGY:
        return {"class": None, "why": "NOT_AN_INVESTMENT_STRATEGY"}
    if d.get("book_obs_id") is None:
        return {"class": None, "why": "NO_BOOK_READ_BY_THE_STRATEGY"}
    if d.get("verdict") == "ENTER":
        return {"class": ENTER, "why": "ENTER"}
    refusals = [str(x) for x in (d.get("refusals") or [])]
    econ = d.get("economics") or {}
    edge = _f(econ.get("best_level_edge_pp"))
    if refusals and set(refusals) <= ECONOMIC_REFUSALS:
        if edge is not None and edge > 0:
            return {"class": NEAR_MISS, "why": refusals[0]}
        return {"class": None, "why": "ECONOMIC_REFUSAL_WITHOUT_POSITIVE_"
                                      "GROSS_EDGE"}
    first = next((r for r in refusals if r not in ECONOMIC_REFUSALS),
                 refusals[0] if refusals else "NO_REFUSAL_RECORDED")
    return {"class": None, "why": "NON_ECONOMIC_REFUSAL:%s" % first}


# ─────────────────────────── the book ──────────────────────────────────

def to_levels(raw, holding_side: str) -> list:
    """The consumed side as [{price (our cost), wire, qty}], cheapest first,
    whole-cent wire prices only -- the paper simulator's `levels_for` rule
    (pinned equal by a test). `raw` holds venue levels ({"px": {"value"},
    "qty"}) or extraction pairs [px, qty]."""
    from .. import bettor_book_snapshot as BS
    short = str(holding_side or "").upper() == "SHORT"
    out = []
    for e in raw or []:
        if isinstance(e, dict):
            px, q, _ = BS._level(e)
        elif isinstance(e, (list, tuple)) and len(e) >= 2:
            px, q = _d(e[0]), _d(e[1])
        else:
            continue
        if px is None or q is None or q <= 0:
            continue
        wire = float(px)
        if not _cent(wire):
            continue
        price = (1.0 - wire) if short else wire
        out.append({"price": round(price, 6), "wire": wire, "qty": float(q)})
    out.sort(key=lambda x: x["price"])
    return out


def side_of(md: dict | None, holding_side: str):
    """The consumed side of a venue marketData for a BUY of `holding_side`."""
    if not isinstance(md, dict):
        return None
    return md.get("bids") if str(holding_side).upper() == "SHORT" \
        else md.get("offers")


# ─────────────────────────── fees ──────────────────────────────────────

def production_fee(qty, price, at=None):
    """THE DEPLOYED SCHEDULE exactly as the paper ledger's default fee
    function prices a fill (bettor_paper_ledger.default_fee_fn ->
    bettor_funded_book.fee_for -> calibration_fees.expected_fee, dated by
    the fill instant). Read here from calibration_fees itself (pure), so the
    lab imports no funded module; a test pins the two equal."""
    from .. import calibration_fees as CF
    when = at
    if when is not None and not isinstance(when, str):
        when = _dt.datetime.fromtimestamp(float(when),
                                          _dt.timezone.utc).date().isoformat()
    got = CF.expected_fee(price, qty, at=when) or {}
    if got.get("BLOCKER") or got.get("FEE") is None:
        raise FeeUnavailable(got.get("BLOCKER") or "NO_CHARGE_STATED")
    return (float(got["FEE"]), "calibration_fees.expected_fee")


def fee(fee_fn, qty, price, at) -> float:
    fn = fee_fn or production_fee
    try:
        got = fn(float(qty), float(price), at=at)
    except TypeError:
        got = fn(float(qty), float(price))
    charge = got[0] if isinstance(got, tuple) else got
    return abs(float(charge))


def fee_per_contract(fee_fn, price, at) -> float:
    return fee(fee_fn, FEE_BLOCK, price, at) / FEE_BLOCK


# ─────────────────────────── executable economics (pure) ───────────────

def top_net_edge(levels: list, *, p, at, fee_fn=None) -> dict:
    """p - best price - fee per contract (probability points)."""
    if p is None:
        return {"status": UNAVAILABLE, "why": "NO_PROBABILITY"}
    if not levels:
        return {"status": MEASURED, "edge": 0.0, "empty_side": True,
                "why": "EMPTY_SIDE_NO_EXECUTABLE_LIQUIDITY"}
    best = levels[0]
    try:
        fpc = fee_per_contract(fee_fn, best["price"], at)
    except FeeUnavailable as exc:
        return {"status": UNAVAILABLE, "why": "FEE_SCHEDULE_REFUSED:%s" % exc}
    return {"status": MEASURED, "edge": _r(float(p) - best["price"] - fpc),
            "best_price": best["price"], "best_qty": best["qty"],
            "fee_per_contract": _r(fpc), "empty_side": False}


def order_ev(levels: list, *, p, limit, qty, at, fee_fn=None) -> dict:
    """THE DECIDED IOC ORDER on one book: walk best first within `limit` up
    to `qty`; EV = sum take x (p - price) - fees per fill (dollars). Nothing
    within the limit is a measured EV of zero."""
    if p is None:
        return {"status": UNAVAILABLE, "why": "NO_PROBABILITY"}
    if limit is None or not qty:
        return {"status": UNAVAILABLE, "why": "NO_DECIDED_ORDER"}
    left, takes = float(qty), []
    for lv in levels or []:
        if left <= 1e-9 or lv["price"] > float(limit) + 1e-12:
            break
        t = min(lv["qty"], left)
        takes.append((lv["price"], t))
        left -= t
    filled = float(qty) - left
    try:
        fees = sum(fee(fee_fn, t, px, at) for px, t in takes)
    except FeeUnavailable as exc:
        return {"status": UNAVAILABLE, "why": "FEE_SCHEDULE_REFUSED:%s" % exc}
    gross = sum(t * (float(p) - px) for px, t in takes)
    cost = sum(t * px for px, t in takes)
    return {"status": MEASURED, "ev_usd": _r(gross - fees, 6),
            "gross_usd": _r(gross, 6), "fees_usd": _r(fees, 6),
            "filled_qty": _r(filled, 6), "complete": left <= 1e-9,
            "vwap": _r(cost / filled) if filled > 0 else None,
            "levels_used": len(takes)}


# ─────────────────────────── the probability at an instant ─────────────

def lane_probability_check(v: dict) -> dict:
    """IS THIS VALUATION'S PROBABILITY ONE THE STRATEGY WOULD ACCEPT? (pure)
    The completed-game match passes `probability_qualified_by_the_lane` only
    when the row carries no probability-stage lane refusal, and its identity
    check only when it carries no identity-stage lane refusal; the policy
    does not apply OUTCOME_DEPTH_BELOW_FLOOR to a PinnAPI sole-authority read
    (provider PinnAPI with a recorded outcome count >= 1). A row whose lane
    refusals were not recorded in the evidence is NOT qualified (None):
    fail closed, never assumed clean."""
    if v.get("lane_qualified_basis") == "THE_DECISION_ITSELF":
        return {"qualified": True, "basis": "THE_DECISION_ITSELF"}
    if not v.get("refusals_recorded"):
        return {"qualified": None,
                "why": "LANE_REFUSALS_NOT_RECORDED_IN_THE_EVIDENCE"}
    from .. import bettor_external_shadow as EXT
    try:
        books = int(v.get("outcome_books")) \
            if v.get("outcome_books") is not None else None
    except (TypeError, ValueError):
        books = None
    sole = v.get("provider") == PINNAPI_PROVIDER and books is not None \
        and books >= 1
    blocking = []
    for code in v.get("refusals") or []:
        base = str(code).split(":")[0]
        if EXT.STAGE_OF.get(base) not in LANE_BLOCKING_STAGES:
            continue
        if sole and base == THIN_OUTCOME:
            continue                      # the policy's own not-applied rule
        blocking.append(str(code))
    if blocking:
        return {"qualified": False, "why": "LANE_REFUSED_THE_PROBABILITY",
                "refusals": blocking}
    return {"qualified": True, "basis": "NO_BLOCKING_LANE_REFUSAL_ON_THE_ROW",
            "pinnapi_sole_authority": sole}


def detection_valuation(d: dict, v0: dict | None) -> dict:
    """The decision's own probability as a valuation-shaped row (the PIT
    accessor's external_valuations stamps). It is lane-qualified by the
    decision itself: a qualified opportunity's refusals are all economic,
    so its probability check passed."""
    pin = d.get("pinnacle") or {}
    v0 = v0 or {}
    return {"valuation_id": v0.get("id") or d.get("valuation_id"),
            "lane_qualified_basis": "THE_DECISION_ITSELF",
            "p": _f(d.get("p_pinnacle")),
            "observed_at": _f(pin.get("at")) if pin.get("at") is not None
            else _f(v0.get("observed_at")),
            "received_at": _f(pin.get("received_at"))
            if pin.get("received_at") is not None
            else _f(v0.get("received_at")),
            "decided_at": _f(pin.get("valuation_decided_at"))
            if pin.get("valuation_decided_at") is not None
            else _f(v0.get("decided_at"))}


def probability_at(cands: list, tau: float, limit_s: float) -> dict:
    """THE PROBABILITY KNOWN AT tau: of the valuations visible at tau (PIT)
    that the LANE QUALIFIED (lane_probability_check), the one with the
    newest provider stamp; FRESH when 0 <= tau - stamp <= limit_s (the
    lane's rule), else STALE. Visible rows the lane refused, or whose lane
    refusals were not recorded, are never used and are counted."""
    vis = PIT.visible("external_valuations", cands, tau)
    vis = [v for v in vis if v.get("p") is not None
           and v.get("observed_at") is not None]
    excluded: dict = {}
    ok = []
    for v in vis:
        chk = lane_probability_check(v)
        if chk.get("qualified"):
            ok.append(v)
        else:
            k = chk.get("why") or "NOT_QUALIFIED"
            excluded[k] = excluded.get(k, 0) + 1
    if not ok:
        return {"status": ABSENT, "p": None,
                "why": ("NO_LANE_QUALIFIED_VALUATION_KNOWN_AT_THE_INSTANT"
                        if excluded else "NO_VALUATION_KNOWN_AT_THE_INSTANT"),
                "excluded_valuations": excluded}
    v = max(ok, key=lambda x: (float(x["observed_at"]),
                               float(x.get("decided_at") or 0)))
    age = float(tau) - float(v["observed_at"])
    if age < -PIT.EPS_S:
        return {"status": STALE, "p": None, "age_s": _r(age, 3),
                "why": "PROVIDER_STAMP_AFTER_THE_INSTANT",
                "valuation_id": v.get("valuation_id"),
                "excluded_valuations": excluded}
    fresh = age <= float(limit_s) + 1e-9
    return {"status": FRESH if fresh else STALE,
            "p": float(v["p"]) if fresh else None,
            "p_recorded": float(v["p"]), "age_s": _r(age, 3),
            "limit_s": float(limit_s), "valuation_id": v.get("valuation_id"),
            "excluded_valuations": excluded}


# ─────────────────────────── one opportunity ───────────────────────────

def _book_row(b) -> dict | None:
    """A later book as a dict (extraction rows are [obs_id, observed_at,
    recorded_at, levels])."""
    if b is None:
        return None
    if isinstance(b, (list, tuple)):
        return {"obs_id": b[0], "observed_at": _f(b[1]),
                "recorded_at": _f(b[2]), "levels": b[3] or []}
    return dict(b)


def _val_row(v) -> dict:
    """A later valuation as a dict. Extraction rows are [id, observed_at,
    received_at, decided_at, p] (V1: no lane refusals recorded) or the same
    followed by [refusals, provider, outcome_books] (V2)."""
    if isinstance(v, (list, tuple)):
        out = {"valuation_id": v[0], "observed_at": _f(v[1]),
               "received_at": _f(v[2]), "decided_at": _f(v[3]),
               "p": _f(v[4])}
        if len(v) >= 8:
            out.update(refusals=[str(x) for x in (v[5] or [])],
                       refusals_recorded=True, provider=v[6],
                       outcome_books=v[7])
        return out
    v = dict(v)
    v.setdefault("valuation_id", v.get("id"))
    if "p" not in v:
        v["p"] = _f(v.get("probability"))
    return v


def limit_of(d: dict) -> float:
    """The decision's own recorded freshness limit, never above the lane's
    rule (a larger recorded value would loosen it)."""
    rec = _f((d.get("pinnacle") or {}).get("limit_s"))
    if rec is None or rec <= 0:
        return PROBABILITY_LIMIT_S
    return min(rec, PROBABILITY_LIMIT_S)


#: a post-detection sample is never placed at t = 0 (it would compete with
#: the detection sample); its offset keeps microsecond precision
MIN_SAMPLE_OFFSET_S = 1e-6


def _offset(off: float) -> float:
    return max(round(float(off), 6), MIN_SAMPLE_OFFSET_S)


def decay(samples: list) -> dict:
    """75% retention, half-life and time to zero on [(t, edge)], through the
    vendored reference functions, with bounds and censoring. THE DETECTION
    SAMPLE IS THE FIRST t = 0 SAMPLE GIVEN and is never replaced: the later
    samples are ordered by time only (never by edge), and any further t = 0
    sample is dropped and counted rather than allowed to become the initial
    edge."""
    pts = [(float(t), float(e)) for t, e in samples if e is not None]
    det = next((p for p in pts if p[0] == 0.0), None)
    if det is None:
        return {"status": UNAVAILABLE, "why": "NO_INITIAL_EDGE"}
    dropped = sum(1 for p in pts if p[0] == 0.0) - 1
    later = sorted((p for p in pts if p[0] > 0.0), key=lambda p: p[0])
    s = [det] + later
    e0 = det[1]
    if e0 <= 0:
        return {"status": UNAVAILABLE, "why": "INITIAL_EDGE_NOT_POSITIVE",
                "initial_edge": e0}
    if len(s) == 1:
        return {"status": "UNOBSERVED_AFTER_DETECTION", "initial_edge": e0,
                "t75": None, "half_life": None, "time_to_zero": None,
                "dropped_zero_offset_samples": dropped}
    last = s[-1][0]

    def bound(t):
        if t is None:
            return {"status": "RIGHT_CENSORED", "at_least_s": last}
        prev = max(x for x, _ in s if x < t)
        return {"status": "CROSSED", "upper_s": t, "lower_s": prev}
    return {"status": MEASURED, "initial_edge": e0, "samples": len(s),
            "last_observed_s": last,
            "dropped_zero_offset_samples": dropped,
            "t75": bound(REF.first_crossing(s, 0.75)),
            "half_life": bound(REF.edge_half_life(s)),
            "time_to_zero": bound(REF.time_to_zero(s)),
            "retention_last": _r(REF.retention(e0, s[-1][1]))}


def _stamp(st: dict, key: str):
    """A stage stamp from a stage dict (epoch seconds, or {'utc_s'|'utc_ns'})."""
    v = (st or {}).get(key)
    if isinstance(v, dict):
        if v.get("utc_ns") is not None:
            return float(v["utc_ns"]) / 1e9
        return _f(v.get("utc_s"))
    return PIT.epoch(v)


def latency_chain(rec: dict) -> dict:
    """THE STAGE STAMPS AND SPANS of one decision. Forward stamps (the
    canonical intent's `latency_stages` and the PAPER adapter record's
    `refs.stages`) are consumed when present; the historical record supplies
    the stamps it has; Karen / Allie / Eddie are UNAVAILABLE historically."""
    d = rec.get("d") or {}
    pin = d.get("pinnacle") or {}
    xi = rec.get("xi") or {}
    tl = xi.get("timeline") or {}
    intent = rec.get("intent") or {}
    fwd = dict(intent.get("latency_stages") or {})
    fwd.update(intent.get("adapter_stages") or {})
    stamps, basis = {}, {}

    def put(stage, value, src):
        if value is not None and stage not in stamps:
            stamps[stage] = float(value)
            basis[stage] = src
    for stage in STAGES:
        for k in STAGE_KEYS[stage]:
            v = _stamp(fwd, k)
            if v is not None:
                put(stage, v, "canonical_intent:%s" % k)
                break
    put("provider_observed", _f(pin.get("at")), "paper_decisions.pinnacle.at")
    put("bettor_receipt", _f(pin.get("received_at")),
        "paper_decisions.pinnacle.received_at")
    put("valuation_complete", _f(pin.get("valuation_decided_at")),
        "paper_decisions.pinnacle.valuation_decided_at")
    dc = _stamp(tl, "decision_complete")
    put("derek_complete", dc, "execution_intents.timeline.decision_complete"
        ".utc_ns (wall clock when the decision handed off)")
    att = rec.get("att") or {}
    if "derek_complete" not in stamps and d.get("decided_at") is not None \
            and att.get("elapsed_s") is not None:
        put("derek_complete", float(d["decided_at"]) + float(att["elapsed_s"]),
            "paper_decisions.decided_at + paper_evaluation_attempts.elapsed_s "
            "(the attempt's wall time, an upper bound)")
    ic = _stamp(tl, "intent_created")
    put("adapter_receipt", ic, "execution_intents.timeline.intent_created "
        "(the ACTUAL lane's intent: the pre-R30 adapter seam)")
    why = {}
    hist = not intent
    for stage in STAGES:
        if stage in stamps:
            continue
        if stage in ("karen_complete", "allie_complete", "eddie_complete",
                     "canonical_intent_complete"):
            why[stage] = NOT_PRE_TRADE if hist else NOT_STAMPED
        else:
            why[stage] = "%s_NOT_RECORDED" % stage.upper()
    spans = {}
    order = list(STAGES)
    for a, b in zip(order, order[1:]):
        name = "%s_to_%s" % (a, b)
        if a in stamps and b in stamps:
            spans[name] = {"status": MEASURED, "s": _r(stamps[b] - stamps[a], 6)}
        else:
            miss = a if a not in stamps else b
            spans[name] = {"status": UNAVAILABLE, "why": why.get(miss)}
    # the measurable composite spans (a missing middle stage does not hide
    # the end-to-end span when both ends are stamped)
    for name, a, b in (("total_provider_to_adapter", "provider_observed",
                        "adapter_receipt"),
                       ("total_receipt_to_decision", "bettor_receipt",
                        "derek_complete"),
                       ("total_provider_to_decision", "provider_observed",
                        "derek_complete"),
                       ("decision_to_adapter", "derek_complete",
                        "adapter_receipt")):
        if a in stamps and b in stamps:
            spans[name] = {"status": MEASURED, "s": _r(stamps[b] - stamps[a], 6)}
        else:
            spans[name] = {"status": UNAVAILABLE,
                           "why": why.get(a if a not in stamps else b)}
    return {"stamps": {k: _r(v, 6) for k, v in stamps.items()},
            "basis": basis, "unavailable": why, "spans": spans,
            "source": "FORWARD_CANONICAL_INTENT" if not hist
            else "HISTORICAL_RECORD"}


def _segments(rec: dict, levels0: list, q: dict) -> dict:
    d = rec.get("d") or {}
    v0 = rec.get("v0") or {}
    lab = d.get("label") or {}
    econ = d.get("economics") or {}
    best = levels0[0]["price"] if levels0 else None
    band = (None if best is None else
            "<0.20" if best < 0.2 else "0.20-0.40" if best < 0.4 else
            "0.40-0.60" if best < 0.6 else "0.60-0.80" if best < 0.8
            else ">=0.80")
    depth = _f(econ.get("depth_within_limit"))
    top_usd = None if not levels0 else levels0[0]["qty"] * levels0[0]["price"]
    liq = (None if top_usd is None else "<$100" if top_usd < 100 else
           "$100-1k" if top_usd < 1000 else "$1k-10k" if top_usd < 10000
           else ">=$10k")
    ctx = v0.get("quote_context")
    t0 = rec.get("_t0")
    pm = rec.get("pm") or {}
    start = None
    if pm.get("game_start") is not None and t0 is not None:
        # us_premap is rewritten in place: its start is used only when the
        # row's last write was already known at detection (PIT)
        vis = PIT.visible("us_premap", [pm], t0)
        start = _f(vis[0]["game_start"]) if vis else None
    tte = None if start is None or t0 is None else (start - t0) / 3600.0
    tte_b = (None if tte is None else "LIVE_OR_STARTED" if tte <= 0 else
             "<1h" if tte < 1 else "1-6h" if tte < 6 else "6-24h"
             if tte < 24 else ">=24h")
    phase = ("LIVE" if (ctx and "LIVE" in str(ctx).upper()) else
             "PREGAME" if (ctx and "PRE" in str(ctx).upper()) else None)
    if phase is None and tte is not None:
        phase = "LIVE" if tte <= 0 else "PREGAME"
    return {"sport": econ.get("sport_family") or v0.get("sport_family"),
            "league": lab.get("competition"),
            "market": lab.get("market_type") or v0.get("market"),
            "phase": phase, "price_band": band, "liquidity_top_usd": liq,
            "strategy": "%s/%s" % (d.get("strategy"), d.get("policy_version")),
            "class": q.get("class"),
            "time_to_event": tte_b,
            "execution_style": ("%s/%s" % ((rec.get("orders") or [{}])[0].get(
                "order_type"), (rec.get("orders") or [{}])[0].get(
                "time_in_force")) if rec.get("orders") else
                "MARKETABLE/IOC (policy entry form; no order decided)"),
            "_depth_within_limit": depth}


def evaluate(rec: dict, *, fee_fn=None) -> dict:
    """ONE QUALIFIED OPPORTUNITY'S EDGE-DECAY RECORD (pure). `rec` holds the
    decision `d`, its book `b0`, its valuation `v0`, later `books` and
    `vals`, and (ENTER) `orders`, `fills`, `xi`; optional `att`, `pm`,
    `intent` (a canonical intent with stage stamps)."""
    d = rec.get("d") or {}
    q = qualify(d)
    out = {"decision_id": d.get("decision_id"), "qualification": q,
           "version": VERSION}
    if q["class"] is None:
        return out
    side = d.get("holding_side")
    b0 = rec.get("b0") or {}
    if b0.get("error"):
        return dict(out, status=UNAVAILABLE, why="DETECTION_BOOK_UNREADABLE")
    known_b0 = PIT.known_at("paper_book_observations", b0)
    dec_at = _f(d.get("decided_at"))
    if dec_at is None or known_b0 is None:
        return dict(out, status=UNAVAILABLE,
                    why="DETECTION_STAMPS_NOT_RECORDED")
    t0 = max(dec_at, known_b0)
    rec = dict(rec, _t0=t0)
    lim = limit_of(d)
    vals = [detection_valuation(d, rec.get("v0"))] + \
        [_val_row(v) for v in rec.get("vals") or []]
    p0 = _f(d.get("p_pinnacle"))
    lv0 = to_levels(b0.get("levels"), side)
    prob0 = probability_at(vals, t0, lim)
    qty = _f(d.get("proposed_qty"))
    limit = _f(d.get("limit_price"))
    enter = q["class"] == ENTER
    e0 = top_net_edge(lv0, p=prob0.get("p"), at=t0, fee_fn=fee_fn)
    o0 = (order_ev(lv0, p=prob0.get("p"), limit=limit, qty=qty, at=t0,
                   fee_fn=fee_fn) if enter else None)
    econ = d.get("economics") or {}
    recorded_ev = _f(econ.get("expected_net_profit_usd"))
    out.update(
        status=MEASURED, t0=_r(t0, 6), decided_at=dec_at,
        detection_basis=("max(decision clock, its book's known instant)"),
        probability_limit_s=lim, p_detection=p0,
        probability_at_detection=prob0,
        remaining_freshness_at_detection_s=(
            None if prob0.get("age_s") is None
            else _r(lim - float(prob0["age_s"]), 3)),
        initial={"top_net_edge": e0, "decided_order": o0,
                 "recorded_expected_net_profit_usd": recorded_ev,
                 "reconciliation_usd": (
                     None if o0 is None or o0.get("ev_usd") is None
                     or recorded_ev is None
                     else _r(o0["ev_usd"] - recorded_ev, 6)),
                 "reconciliation_basis": (
                     "the lab walks the recorded book without the paper "
                     "ledger's consumed-liquidity netting; a non-zero "
                     "difference is that netting or the fee function")})
    out["segments"] = _segments(rec, lv0, q)
    books = [_book_row(b) for b in rec.get("books") or []]
    books = [b for b in books if b and b.get("observed_at") is not None
             and b.get("obs_id") != b0.get("obs_id")]

    # ── the horizons ────────────────────────────────────────────────
    hz = []
    for i, h in enumerate(HORIZONS_S):
        hi = t0 + h
        row = {"h_s": h}
        if h == 0:
            book, lo = b0, None
        else:
            lo = t0 + HORIZONS_S[i - 1]
            vis = PIT.visible("paper_book_observations", books, hi)
            inwin = [b for b in vis if lo < float(b["observed_at"]) <= hi]
            book = max(inwin, key=lambda b: float(b["observed_at"])) \
                if inwin else None
        row["window_s"] = [None if lo is None else _r(lo - t0, 3), h]
        if book is None:
            row.update(status=UNAVAILABLE, why="NO_RECORDED_BOOK_IN_WINDOW")
            hz.append(row)
            continue
        lv = lv0 if h == 0 else to_levels(book.get("levels"), side)
        pr = prob0 if h == 0 else probability_at(vals, hi, lim)
        row.update(book_obs_id=book.get("obs_id"),
                   book_offset_s=_r(float(book["observed_at"]) - t0, 3),
                   probability=pr)
        venue = top_net_edge(lv, p=p0, at=hi, fee_fn=fee_fn)
        row[VENUE_AT_P0] = {"edge": venue.get("edge"),
                            "status": venue.get("status"),
                            "label": "NOT_EXECUTABLE_RESEARCH_DECOMPOSITION"}
        if enter:
            ov = order_ev(lv, p=p0, limit=limit, qty=qty, at=hi, fee_fn=fee_fn)
            row[VENUE_AT_P0]["order_ev_usd"] = ov.get("ev_usd")
        if pr["status"] != FRESH:
            row.update(status=UNAVAILABLE,
                       why="PROBABILITY_NOT_FRESH_AT_HORIZON:%s"
                       % pr["status"])
            hz.append(row)
            continue
        te = top_net_edge(lv, p=pr["p"], at=hi, fee_fn=fee_fn)
        row[TOP_NET_EDGE] = te
        if te["status"] != MEASURED:
            row.update(status=UNAVAILABLE, why=te.get("why"))
            hz.append(row)
            continue
        row["status"] = MEASURED
        row["retention"] = REF.retention(e0.get("edge"), te["edge"])
        if enter:
            ov = order_ev(lv, p=pr["p"], limit=limit, qty=qty, at=hi,
                          fee_fn=fee_fn)
            row[DECIDED_ORDER_EV] = ov
            if o0 and o0.get("ev_usd") is not None and \
                    ov.get("ev_usd") is not None:
                row["order_ev_retention"] = REF.retention(o0["ev_usd"],
                                                          ov["ev_usd"])
        hz.append(row)
    out["horizons"] = hz

    # ── every recorded book after detection: the decay samples ─────────
    fresh_s, venue_s, order_s = [], [], []
    if e0.get("status") == MEASURED:
        fresh_s.append((0.0, e0["edge"]))
        venue_s.append((0.0, e0["edge"]))
    if o0 and o0.get("ev_usd") is not None:
        order_s.append((0.0, o0["ev_usd"]))
    unavailable_samples = {}
    for b in sorted(books, key=lambda x: float(x["observed_at"])):
        k = PIT.known_at("paper_book_observations", b)
        if k is None or float(b["observed_at"]) <= t0:
            continue
        # THE SAMPLE'S INSTANT IS WHEN THE BOOK WAS KNOWN (its latest stamp),
        # not merely when it was observed: a row recorded later than it was
        # observed becomes evidence only at its recorded instant.
        off = k - t0
        if off <= 0 or off > MAX_HORIZON_S:
            continue
        lv = to_levels(b.get("levels"), side)
        v = top_net_edge(lv, p=p0, at=k, fee_fn=fee_fn)
        if v.get("status") == MEASURED:
            venue_s.append((round(off, 3), v["edge"]))
        pr = probability_at(vals, k, lim)
        if pr["status"] != FRESH:
            unavailable_samples[pr["status"]] = \
                unavailable_samples.get(pr["status"], 0) + 1
            continue
        te = top_net_edge(lv, p=pr["p"], at=k, fee_fn=fee_fn)
        if te.get("status") == MEASURED:
            fresh_s.append((round(off, 3), te["edge"]))
        if enter:
            ov = order_ev(lv, p=pr["p"], limit=limit, qty=qty, at=k,
                          fee_fn=fee_fn)
            if ov.get("ev_usd") is not None:
                order_s.append((round(off, 3), ov["ev_usd"]))
    out["samples"] = {"fresh": fresh_s, "venue_at_p0": venue_s,
                      "order_ev": order_s,
                      "books_after_detection": sum(
                          1 for b in books if 0 < float(b["observed_at"]) - t0
                          <= MAX_HORIZON_S),
                      "samples_without_fresh_probability":
                          unavailable_samples}
    out["decay"] = {TOP_NET_EDGE: decay(fresh_s), VENUE_AT_P0: decay(venue_s)}
    if enter:
        out["decay"][DECIDED_ORDER_EV] = decay(order_s)
    # the evidence life: how long after detection a FRESH probability was
    # recorded at all (the hard limit on executable life under BETTOR's own
    # rule, whatever the book does)
    fresh_until = t0 + (lim - float(prob0["age_s"])) \
        if prob0.get("age_s") is not None else None
    later = sorted((v for v in vals[1:] if v.get("observed_at") is not None
                    and PIT.known_at("external_valuations", v) is not None),
                   key=lambda v: PIT.known_at("external_valuations", v))
    for v in later:
        k = PIT.known_at("external_valuations", v)
        if k is not None and fresh_until is not None and \
                k <= fresh_until + PIT.EPS_S and k > t0:
            fresh_until = max(fresh_until, float(v["observed_at"]) + lim)
    out["probability_evidence_life_s"] = (
        None if fresh_until is None else _r(fresh_until - t0, 3))
    out["latency"] = latency_chain(rec)
    if enter:
        out["execution"] = execution(rec, t0=t0, lv0=lv0, o0=o0, vals=vals,
                                     lim=lim, books=books, fee_fn=fee_fn)
    return out


def execution(rec: dict, *, t0, lv0, o0, vals, lim, books, fee_fn) -> dict:
    """THE DECIDED ORDER AT ITS EXECUTION INSTANT and the EV lost between
    detection and execution (ENTER). The execution book is the first
    recorded book at or after the order's eligible instant and before its
    expiry -- the paper simulator's own rule -- known by then (PIT)."""
    d = rec.get("d") or {}
    o = (rec.get("orders") or [None])[0] or {}
    dec = _f(d.get("decided_at"))
    eligible = _f(o.get("eligible_at")) or (None if dec is None
                                            else dec + SIM_DELAY_S)
    expires = _f(o.get("expires_at")) or (None if dec is None
                                          else dec + SIM_TTL_S)
    qty, limit = _f(d.get("proposed_qty")), _f(d.get("limit_price"))
    out = {"eligible_at": eligible, "expires_at": expires,
           "decision_to_eligible_s": (None if eligible is None or dec is None
                                      else _r(eligible - dec, 3)),
           "order_state": o.get("state"),
           "order_terminal_reason": o.get("terminal_reason")}
    fills = rec.get("fills") or []
    fq = sum(_f(f[2]) or 0 for f in fills) if fills else 0.0
    fcost = sum((_f(f[2]) or 0) * (_f(f[3]) or 0) for f in fills)
    ffee = sum(_f(f[4]) or 0 for f in fills)
    p0 = _f(d.get("p_pinnacle"))
    out["realized_fill"] = {"filled_qty": _r(fq, 6),
                            "vwap": _r(fcost / fq) if fq else None,
                            "fees_usd": _r(ffee, 6),
                            "fills": len(fills),
                            # the simulated fills valued at the DETECTION
                            # probability (the paper ledger's own expected
                            # value of what it actually bought)
                            "expected_ev_at_detection_p_usd": (
                                None if p0 is None else
                                _r(fq * p0 - fcost - ffee, 6)),
                            "ev_at_detection_usd": (o0 or {}).get("ev_usd"),
                            "basis": "paper_fills (SIMULATOR)"}
    # THE SIMULATOR EXPIRES AN ORDER WHOSE FIRST BOOK AFTER ITS ELIGIBLE
    # INSTANT WAS UNREADABLE (bettor_paper_simulator: "an unreadable book is
    # evidence of nothing"; it never retries on a later book). Such an order
    # has NO execution book, whatever readable book came later -- pricing it
    # on a later book would be a book the paper path never executed on.
    reason = o.get("terminal_reason")
    if reason in SIM_NO_EXECUTION:
        out.update(status=UNAVAILABLE, why=SIM_NO_EXECUTION[reason],
                   detail=("the paper order expired unfilled: %s -- a fact "
                           "of the paper pipeline (its post-decision book "
                           "read), not a market measurement of decay"
                           % reason))
        return out
    cand = [b for b in books if eligible is not None
            and eligible - PIT.EPS_S <= float(b["observed_at"]) <= expires]
    cand.sort(key=lambda b: float(b["observed_at"]))
    exe = None
    fill_books = {f[5] for f in fills if len(f) > 5 and f[5] is not None}
    for b in cand:
        k = PIT.known_at("paper_book_observations", b)
        if k is None or k > expires + PIT.EPS_S:
            continue
        if fill_books and b.get("obs_id") not in fill_books:
            continue                    # the simulator filled on another book
        exe = b
        break
    if exe is None:
        out.update(status=UNAVAILABLE,
                   why="NO_RECORDED_READABLE_BOOK_IN_THE_ORDER_WINDOW",
                   detail=("the paper order had no recorded readable book "
                           "between its eligible instant and its expiry; it "
                           "expires unfilled on the paper ledger -- a "
                           "simulation fact, not a market measurement of "
                           "decay"))
        return out
    k = PIT.known_at("paper_book_observations", exe)
    lv = to_levels(exe.get("levels"), d.get("holding_side"))
    pr = probability_at(vals, k, lim)
    out.update(execution_book_obs_id=exe.get("obs_id"),
               execution_offset_s=_r(float(exe["observed_at"]) - t0, 3),
               book_acquisition_after_eligible_s=_r(
                   float(exe["observed_at"]) - eligible, 3),
               probability=pr)
    venue = order_ev(lv, p=_f(d.get("p_pinnacle")), limit=limit, qty=qty,
                     at=k, fee_fn=fee_fn)
    out["order_ev_at_detection_p"] = {
        "ev_usd": venue.get("ev_usd"), "filled_qty": venue.get("filled_qty"),
        "label": "NOT_EXECUTABLE_RESEARCH_DECOMPOSITION"}
    if pr["status"] != FRESH:
        out.update(status=UNAVAILABLE,
                   why="PROBABILITY_NOT_FRESH_AT_EXECUTION:%s" % pr["status"])
        return out
    ex = order_ev(lv, p=pr["p"], limit=limit, qty=qty, at=k, fee_fn=fee_fn)
    out["order_ev_at_execution"] = ex
    if o0 and o0.get("ev_usd") is not None and ex.get("ev_usd") is not None:
        out.update(status=MEASURED,
                   ev_at_detection_usd=o0["ev_usd"],
                   ev_at_execution_usd=ex["ev_usd"],
                   ev_lost_usd=_r(REF.pipeline_loss(o0["ev_usd"],
                                                    ex["ev_usd"]), 6),
                   ev_retention=REF.retention(o0["ev_usd"], ex["ev_usd"]))
    else:
        out.update(status=UNAVAILABLE, why="ORDER_EV_UNPRICEABLE")
    return out


# ─────────────────────────── aggregation ───────────────────────────────

SEGMENT_KEYS = ("sport", "league", "market", "phase", "price_band",
                "liquidity_top_usd", "strategy", "class", "time_to_event",
                "execution_style")
SEGMENT_MIN_N = 10


def _km_units(rows: list, measure: str, key: str) -> list:
    units = []
    for r in rows:
        dd = ((r.get("decay") or {}).get(measure) or {})
        if dd.get("status") != MEASURED:
            continue
        b = dd.get(key) or {}
        if b.get("status") == "CROSSED":
            units.append((b["upper_s"], True))
        elif b.get("status") == "RIGHT_CENSORED":
            units.append((b["at_least_s"], False))
    return units


def _median_km(rows, measure="TOP_NET_EDGE", key="half_life"):
    return ST.km_quantile(_km_units(rows, measure, key), 0.5)


def _clusters(rows: list) -> dict:
    out: dict = {}
    for r in rows:
        out.setdefault(r.get("_cluster") or r.get("decision_id"), []).append(r)
    return out


def summarize(results: list, *, pipeline_latency_s: float | None = None,
              level_family: float = 0.95) -> dict:
    """THE AGGREGATE over evaluated opportunities (pure)."""
    q = [r for r in results if (r.get("qualification") or {}).get("class")]
    ok = [r for r in q if r.get("status") == MEASURED]
    out = {"version": VERSION, "authority": AUTHORITY,
           "qualified": len(q),
           "qualified_by_class": {c: sum(1 for r in q if r["qualification"]
                                         ["class"] == c)
                                  for c in (ENTER, NEAR_MISS)},
           "not_qualified": _count(r["qualification"]["why"] for r in results
                                   if not r["qualification"].get("class")),
           "evaluated": len(ok),
           "unavailable": _count(r.get("why") for r in q
                                 if r.get("status") != MEASURED)}
    # horizons: availability and retention
    hz = []
    for i, h in enumerate(HORIZONS_S):
        rows = [(r, r["horizons"][i]) for r in ok]
        meas = [(r, x) for r, x in rows if x.get("status") == MEASURED]
        ret = [x.get("retention") for _, x in meas
               if x.get("retention") is not None]
        edges = [(x.get(TOP_NET_EDGE) or {}).get("edge") for _, x in meas]
        ev = [(x.get(DECIDED_ORDER_EV) or {}).get("ev_usd") for _, x in meas
              if x.get(DECIDED_ORDER_EV)]
        ven = [(x.get(VENUE_AT_P0) or {}).get("edge") for _, x in rows
               if (x.get(VENUE_AT_P0) or {}).get("edge") is not None]
        e0s = {r["decision_id"]: (r["initial"]["top_net_edge"] or {}).get(
            "edge") for r in ok}
        vret = [REF.retention(e0s.get(r["decision_id"]),
                              (x.get(VENUE_AT_P0) or {}).get("edge"))
                for r, x in rows if (x.get(VENUE_AT_P0) or {}).get("edge")
                is not None]
        vret = [v for v in vret if v is not None]
        cl = _clusters([dict(r, _ret=x.get("retention")) for r, x in meas
                        if x.get("retention") is not None])
        ci = (ST.cluster_bootstrap(
            cl, lambda us: ST.median([u["_ret"] for u in us]),
            level=level_family) if len(ret) >= SEGMENT_MIN_N else
            {"status": UNAVAILABLE, "why": "N_BELOW_%d" % SEGMENT_MIN_N})
        hz.append({"h_s": h, "measured": len(meas),
                   "unavailable": _count(x.get("why") for _, x in rows
                                         if x.get("status") != MEASURED),
                   "top_net_edge": ST.quartiles(edges),
                   "retention": dict(ST.quartiles(ret), median_ci=ci),
                   "decided_order_ev_usd": dict(ST.quartiles(ev),
                                                total=_r(sum(v for v in ev
                                                             if v is not None),
                                                         6)),
                   "venue_book_at_detection_p": {
                       "with_book": len(ven), "edge": ST.quartiles(ven),
                       "retention": ST.quartiles(vret),
                       "label": "NOT_EXECUTABLE_RESEARCH_DECOMPOSITION"}})
    out["horizons"] = hz
    # decay times
    dec = {}
    for m in (TOP_NET_EDGE, VENUE_AT_P0, DECIDED_ORDER_EV):
        st = _count(((r.get("decay") or {}).get(m) or {}).get("status")
                    for r in ok if (r.get("decay") or {}).get(m))
        dm = {"status_counts": st}
        for key in ("t75", "half_life", "time_to_zero"):
            units = _km_units(ok, m, key)
            km = ST.km_quartiles(units)
            if km["n"] >= SEGMENT_MIN_N and km["p50"] is not None:
                rows = [r for r in ok if ((r.get("decay") or {}).get(m) or {})
                        .get("status") == MEASURED]
                km["p50_ci"] = ST.cluster_bootstrap(
                    _clusters(rows), lambda us, m=m, key=key: _median_km(
                        us, m, key), level=level_family)
            else:
                km["p50_ci"] = {"status": UNAVAILABLE,
                                "why": ("N_BELOW_%d" % SEGMENT_MIN_N
                                        if km["n"] < SEGMENT_MIN_N else
                                        "MEDIAN_NOT_REACHED_UNDER_CENSORING")}
            dm[key] = km
        dec[m] = dm
    out["decay"] = dec
    # evidence life and freshness at detection
    out["remaining_freshness_at_detection_s"] = ST.quartiles(
        [r.get("remaining_freshness_at_detection_s") for r in ok])
    out["probability_evidence_life_s"] = ST.quartiles(
        [r.get("probability_evidence_life_s") for r in ok])
    out["books_after_detection"] = ST.quartiles(
        [(r.get("samples") or {}).get("books_after_detection") for r in ok])
    # latency
    lat = {}
    span_names = set()
    for r in ok:
        span_names.update(((r.get("latency") or {}).get("spans") or {}).keys())
    for name in sorted(span_names):
        vals, why = [], {}
        for r in ok:
            sp = (r.get("latency") or {}).get("spans", {}).get(name) or {}
            if sp.get("status") == MEASURED:
                vals.append(sp["s"])
            else:
                w = sp.get("why") or "NOT_COMPUTED"
                why[w] = why.get(w, 0) + 1
        lat[name] = dict(ST.quartiles(vals), unavailable=why)
    out["latency"] = lat
    out["latency_dominant_stages"] = dominant_stages(lat)
    # EV lost during processing (ENTER)
    ex = [r.get("execution") or {} for r in ok
          if r["qualification"]["class"] == ENTER]
    m_ex = [e for e in ex if e.get("status") == MEASURED]
    out["ev_lost_during_processing"] = {
        "enter_evaluated": len(ex), "measured": len(m_ex),
        "unavailable": _count(e.get("why") for e in ex
                              if e.get("status") != MEASURED),
        "ev_at_detection_usd_total": _r(sum(e["ev_at_detection_usd"]
                                            for e in m_ex), 6),
        "ev_at_execution_usd_total": _r(sum(e["ev_at_execution_usd"]
                                            for e in m_ex), 6),
        "ev_lost_usd_total": _r(sum(e["ev_lost_usd"] for e in m_ex), 6),
        "ev_lost_usd": ST.quartiles([e["ev_lost_usd"] for e in m_ex]),
        "ev_retention": ST.quartiles([e.get("ev_retention") for e in m_ex]),
        "decision_to_eligible_s": ST.quartiles(
            [e.get("decision_to_eligible_s") for e in ex]),
        "book_acquisition_after_eligible_s": ST.quartiles(
            [e.get("book_acquisition_after_eligible_s") for e in ex]),
        "orders_without_execution_evidence": sum(
            1 for e in ex if e.get("why") in (
                "NO_RECORDED_READABLE_BOOK_IN_THE_ORDER_WINDOW",
                *SIM_NO_EXECUTION.values())),
        "order_terminal_reasons": _count(e.get("order_terminal_reason")
                                         for e in ex),
        "realized_filled_orders": sum(
            1 for e in ex if ((e.get("realized_fill") or {}).get("filled_qty")
                              or 0) > 0),
        "paper_pipeline_realization": realization(ex),
        "method": ("decided IOC order walked on the detection book and on "
                   "the first recorded book at/after its eligible instant, "
                   "each with the probability fresh at that instant; "
                   "dollars at the decided quantity")}
    # half-life vs pipeline latency
    out["half_life_vs_pipeline_latency"] = half_life_vs_latency(
        ok, pipeline_latency_s)
    out["segments"] = segment_table(ok, level_family=level_family)
    return out


def realization(ex: list) -> dict:
    """WHAT THE PAPER PIPELINE REALIZED OF THE DETECTED EV (ENTER): the
    detection EV of every decided order, the simulated fills valued at the
    same detection probability, and the gap attributed to each order's
    terminal reason (an order that expired unfilled realized nothing)."""
    det = [e for e in ex if (e.get("realized_fill") or {}).get(
        "ev_at_detection_usd") is not None]
    tot = sum(e["realized_fill"]["ev_at_detection_usd"] for e in det)
    real = sum(e["realized_fill"].get("expected_ev_at_detection_p_usd") or 0.0
               for e in det)
    by: dict = {}
    for e in det:
        k = str(e.get("order_terminal_reason"))
        g = by.setdefault(k, {"orders": 0, "ev_at_detection_usd": 0.0,
                              "realized_usd": 0.0})
        g["orders"] += 1
        g["ev_at_detection_usd"] += e["realized_fill"]["ev_at_detection_usd"]
        g["realized_usd"] += e["realized_fill"].get(
            "expected_ev_at_detection_p_usd") or 0.0
    for g in by.values():
        g["ev_at_detection_usd"] = _r(g["ev_at_detection_usd"], 6)
        g["realized_usd"] = _r(g["realized_usd"], 6)
        g["gap_usd"] = _r(g["ev_at_detection_usd"] - g["realized_usd"], 6)
    return {"orders": len(det), "ev_at_detection_usd_total": _r(tot, 6),
            "realized_at_detection_p_usd_total": _r(real, 6),
            "gap_usd_total": _r(tot - real, 6),
            "by_terminal_reason": dict(sorted(
                by.items(), key=lambda kv: -kv[1]["gap_usd"])),
            "basis": ("detection EV of the decided order vs its simulated "
                      "fills, both at the detection probability; the gap is "
                      "the paper pipeline's realization loss (mostly orders "
                      "that never met a readable execution book), NOT a "
                      "market measurement")}


def _count(xs) -> dict:
    out: dict = {}
    for x in xs:
        k = str(x)
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def dominant_stages(lat: dict) -> list:
    """The consecutive stage spans ranked by their median (MEASURED only)."""
    names = ["%s_to_%s" % (a, b) for a, b in zip(STAGES, STAGES[1:])]
    rows = [(n, (lat.get(n) or {}).get("p50"), (lat.get(n) or {}).get("n"))
            for n in names]
    meas = [r for r in rows if r[1] is not None]
    tot = sum(max(0.0, r[1]) for r in meas) or None
    return [{"span": n, "p50_s": p, "n": k,
             "share_of_measured_p50_sum": (None if tot is None
                                           else _r(max(0.0, p) / tot, 4))}
            for n, p, k in sorted(meas, key=lambda r: -r[1])] + \
        [{"span": n, "status": UNAVAILABLE,
          "why": next(iter((lat.get(n) or {}).get("unavailable") or {}), None)}
         for n, p, k in rows if p is None]


def half_life_vs_latency(rows: list, pipeline_latency_s) -> dict:
    """% of opportunities whose half-life is shorter than the pipeline
    latency. Per opportunity the latency is its own measured post-detection
    span (detection -> order eligible) unless a forward pipeline latency is
    given; a censored or interval-straddling half-life is INDETERMINATE."""
    shorter = longer = indet = unobs = 0
    for r in rows:
        dd = ((r.get("decay") or {}).get(TOP_NET_EDGE) or {})
        hl = dd.get("half_life") or {}
        lat = pipeline_latency_s
        if lat is None:
            ex = r.get("execution") or {}
            lat = ex.get("decision_to_eligible_s")
            if lat is None:
                lat = SIM_DELAY_S
        if dd.get("status") != MEASURED:
            unobs += 1
        elif hl.get("status") == "CROSSED":
            if hl["upper_s"] < lat:
                shorter += 1
            elif hl["lower_s"] >= lat:
                longer += 1
            else:
                indet += 1
        elif hl.get("status") == "RIGHT_CENSORED":
            if hl["at_least_s"] >= lat:
                longer += 1
            else:
                indet += 1
    det = shorter + longer
    return {"shorter": shorter, "longer": longer, "indeterminate": indet,
            "unobserved": unobs,
            "share_shorter_of_determined": (None if det == 0
                                            else _r(shorter / det, 4)),
            "latency_basis": ("the given forward pipeline latency %.3f s"
                              % pipeline_latency_s
                              if pipeline_latency_s is not None else
                              "each opportunity's own detection -> order "
                              "eligible span (the paper simulator's delay)")}


def segment_table(rows: list, *, level_family: float = 0.95) -> dict:
    """Per segment value with n >= SEGMENT_MIN_N: KM half-life quartiles and
    the median half-life's cluster-bootstrap interval at the BONFERRONI
    level for all reported segments (multiple-testing control)."""
    groups: dict = {}
    for r in rows:
        seg = r.get("segments") or {}
        for k in SEGMENT_KEYS:
            v = seg.get(k)
            groups.setdefault((k, str(v) if v is not None else
                               "UNAVAILABLE"), []).append(r)
    big = {k: v for k, v in groups.items() if len(v) >= SEGMENT_MIN_N}
    lvl = ST.bonferroni_level(len(big), level_family)
    out = {"min_n": SEGMENT_MIN_N, "reported": len(big),
           "suppressed_below_min_n": len(groups) - len(big),
           "interval_level_each": _r(lvl, 6),
           "multiple_testing": ("Bonferroni: each interval at 1 - 0.05/k for "
                                "the k segment values reported"),
           "rows": []}
    for (k, v), rs in sorted(big.items()):
        units = _km_units(rs, TOP_NET_EDGE, "half_life")
        km = ST.km_quartiles(units)
        ci = (ST.cluster_bootstrap(_clusters([r for r in rs if (
            (r.get("decay") or {}).get(TOP_NET_EDGE) or {}).get("status")
            == MEASURED]), lambda us: _median_km(us), level=lvl)
            if km["n"] >= SEGMENT_MIN_N and km["p50"] is not None else
            {"status": UNAVAILABLE, "why": "N_BELOW_MIN_OR_MEDIAN_NOT_REACHED"})
        ex = [r.get("execution") or {} for r in rs]
        lost = [e["ev_lost_usd"] for e in ex if e.get("status") == MEASURED]
        out["rows"].append({"segment": k, "value": v, "n": len(rs),
                            "half_life_km": dict(km, p50_ci=ci),
                            "ev_lost_usd_total": _r(sum(lost), 6)
                            if lost else None,
                            "ev_lost_measured": len(lost)})
    return out


def realized_vs_latency(rows: list) -> dict:
    """EVIDENCE THAT FASTER PROCESSING WOULD OR WOULD NOT HELP: ENTER
    decisions split at the median of their measured internal latency
    (provider stamp -> decision), compared on execution-evidence rate,
    realized fill rate and EV retention at execution, within league strata
    (Mantel-Haenszel-style pooled differences weight each stratum by its
    size; a stratum needs both arms)."""
    ent = [r for r in rows if (r.get("qualification") or {}).get("class")
           == ENTER and r.get("status") == MEASURED]

    def lat(r):
        sp = ((r.get("latency") or {}).get("spans") or {}).get(
            "total_provider_to_decision") or {}
        return sp.get("s") if sp.get("status") == MEASURED else None
    have = [(lat(r), r) for r in ent if lat(r) is not None]
    if len(have) < 2 * 5:
        return {"status": UNAVAILABLE, "why": "FEWER_THAN_10_ENTER_DECISIONS_"
                "WITH_A_MEASURED_INTERNAL_LATENCY", "n": len(have)}
    cut = ST.median([x for x, _ in have])

    def stats(rs):
        ex = [r.get("execution") or {} for r in rs]
        meas = [e for e in ex if e.get("status") == MEASURED]
        filled = [e for e in ex if ((e.get("realized_fill") or {}).get(
            "filled_qty") or 0) > 0]
        return {"n": len(rs),
                "execution_evidence_rate": _r(len(meas) / len(rs), 4)
                if rs else None,
                "realized_fill_rate": _r(len(filled) / len(rs), 4)
                if rs else None,
                "ev_retention": ST.quartiles([e.get("ev_retention")
                                              for e in meas])}
    fast = [r for x, r in have if x <= cut]
    slow = [r for x, r in have if x > cut]
    strata: dict = {}
    for x, r in have:
        strata.setdefault((r.get("segments") or {}).get("league") or "?",
                          {"fast": [], "slow": []})[
            "fast" if x <= cut else "slow"].append(r)
    num = den = 0.0
    for s in strata.values():
        if s["fast"] and s["slow"]:
            w = len(s["fast"]) * len(s["slow"]) / (len(s["fast"]) +
                                                    len(s["slow"]))
            rf = stats(s["fast"])["realized_fill_rate"] or 0.0
            rs_ = stats(s["slow"])["realized_fill_rate"] or 0.0
            num += w * (rf - rs_)
            den += w
    return {"status": MEASURED, "split_at_internal_latency_s": cut,
            "fast": stats(fast), "slow": stats(slow),
            "league_stratified_fill_rate_difference": (
                None if den == 0 else _r(num / den, 4)),
            "strata_with_both_arms": sum(1 for s in strata.values()
                                         if s["fast"] and s["slow"]),
            "caution": ("observational; small n; latency is not randomized, "
                        "so this is evidence of association only")}


def evaluate_all(records: list, *, fee_fn=None) -> list:
    out = []
    for rec in records:
        r = evaluate(rec, fee_fn=fee_fn)
        r["_cluster"] = (rec.get("d") or {}).get("fixture") or \
            r.get("decision_id")
        out.append(r)
    return out


def pm_answers(summary: dict, realized: dict | None = None) -> dict:
    """THE OWNER'S PM QUESTIONS for this track, answered from the summary
    with the exact missing evidence named where a number does not exist.

      A  How much executable EV is lost to BETTOR's internal latency?
      B  What is the typical edge half-life by league / strategy?"""
    ev = summary.get("ev_lost_during_processing") or {}
    lat = summary.get("latency") or {}
    dec = (summary.get("decay") or {}).get(TOP_NET_EDGE) or {}
    seg = (summary.get("segments") or {}).get("rows") or []

    def span(n):
        x = lat.get(n) or {}
        return {k: x.get(k) for k in ("n", "p25", "p50", "p75")}
    a = {
        "measured_post_decision_loss": {
            "ev_lost_usd_total": ev.get("ev_lost_usd_total"),
            "ev_at_detection_usd_total": ev.get("ev_at_detection_usd_total"),
            "orders_measured": ev.get("measured"),
            "orders_evaluated": ev.get("enter_evaluated"),
            "unavailable": ev.get("unavailable"),
            "per_order_ev_lost_usd": ev.get("ev_lost_usd"),
            "window": ("detection -> the first recorded book at/after the "
                       "order's eligible instant (the paper simulator's "
                       "decision -> execution delay)")},
        "pre_detection_latency_s": {
            "provider_to_receipt": span(
                "provider_observed_to_bettor_receipt"),
            "receipt_to_valuation": span(
                "bettor_receipt_to_valuation_complete"),
            "valuation_to_decision": span(
                "valuation_complete_to_derek_complete"),
            "provider_to_decision": span("total_provider_to_decision")},
        "pre_detection_loss_usd": {
            "status": UNAVAILABLE,
            "why": ("no venue book is recorded at the Pinnacle receipt or "
                    "valuation instant (the strategy reads the book only "
                    "after it qualifies the probability), so the EV the "
                    "pre-detection stages cost cannot be measured; what they "
                    "measurably cost is probability freshness: "
                    "remaining_freshness_at_detection_s")},
        "remaining_freshness_at_detection_s":
            summary.get("remaining_freshness_at_detection_s"),
        "karen_allie_eddie_loss_usd": {
            "status": UNAVAILABLE,
            "why": NOT_PRE_TRADE},
        "orders_without_execution_evidence":
            ev.get("orders_without_execution_evidence"),
        "order_terminal_reasons": ev.get("order_terminal_reasons"),
        "paper_pipeline_realization": ev.get("paper_pipeline_realization"),
        "post_decision_book_acquisition_s":
            ev.get("book_acquisition_after_eligible_s"),
    }
    by = {}
    for r in seg:
        if r.get("segment") in ("league", "strategy", "class"):
            km = r.get("half_life_km") or {}
            by.setdefault(r["segment"], []).append({
                "value": r["value"], "n": r["n"],
                "half_life_p25_s": km.get("p25"),
                "half_life_p50_s": km.get("p50"),
                "half_life_p75_s": km.get("p75"),
                "crossed": km.get("crossed"), "censored": km.get("censored"),
                "p50_ci": km.get("p50_ci")})
    b = {"overall_executable_half_life_s": {
            k: dec.get("half_life", {}).get(k)
            for k in ("n", "crossed", "censored", "p25", "p50", "p75",
                      "p50_ci")},
         "overall_venue_book_half_life_s": {
            k: ((summary.get("decay") or {}).get(VENUE_AT_P0) or {}).get(
                "half_life", {}).get(k)
            for k in ("n", "crossed", "censored", "p25", "p50", "p75")},
         "by_segment": by,
         "segments_below_min_n_suppressed": (summary.get("segments") or {})
         .get("suppressed_below_min_n"),
         "min_n": SEGMENT_MIN_N}
    return {"A_ev_lost_to_internal_latency": a,
            "B_edge_half_life_by_league_strategy": b,
            "faster_processing_evidence": realized,
            "status": "RETROSPECTIVE_ONLY",
            "never": "FORWARD_VALIDATED from retrospective evidence"}
