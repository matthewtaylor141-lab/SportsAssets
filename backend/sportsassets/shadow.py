"""SHADOW TRADING: the contract. No network, no database, no orders.

Owner directive 2026-09-19. BETTOR makes PROSPECTIVE decisions in real
time and management watches in COMMAND what those decisions would have
done. SHADOW_MODE is TRUE, REAL_ORDER_SUBMISSION is DISABLED,
CAPITAL_AT_RISK is 0, mirror_live stays false.

THIS IS NOT A BACKTEST, and the difference is the whole point. A
backtest chooses its entries knowing how the market moved. Every record
here is written BEFORE the outcome exists, and migration 068 makes the
decision table refuse UPDATE and DELETE at the database, so nothing
learned later can revise what was claimed at T0.

THE FOUR REFUSALS THIS MODULE EXISTS TO ENFORCE
===============================================

1.  A PRICE TOUCHED IS NOT A FILL. The live mirror learned this the
    expensive way. "The market traded through my level" and "I was
    filled" are different claims, and the second one requires queue
    position we do not have. Passive actions therefore produce
    COUNTERFACTUAL_QUOTE_MARKOUT and QUEUE_MODEL_ESTIMATED_FILL, which
    are research outputs, and ACTUAL_BETTOR_FILL, which is NONE because
    no order exists. `passive_outcome` returns NOT_IDENTIFIED unless a
    queue model declares itself decision-grade, and `economics` refuses
    to put an unidentified passive fill into realized P&L.

2.  SIMULATED AND OBSERVED ECONOMICS NEVER SUM. Every figure carries its
    EXECUTION CLASS. `economics` returns a mapping keyed by class and
    has no "total" -- because the one number management would read first
    is the one number that would be a lie.

3.  A DECISION DOES NOT EXECUTE AT ITS OWN BOOK. T_SHADOW_ARRIVAL is
    T_DECISION plus latency, and the fill is walked against the book
    observed AT ARRIVAL. Where execution latency has not been measured,
    `LATENCY_SCENARIOS` is a PREDECLARED grid and every result is
    labelled SCENARIO -- never OBSERVED, and never one flattering value
    picked after the fact.

4.  YES AND NO ARE DIFFERENT POSITIONS. They are never netted. That
    netting defect is why book 1177's reading of a whale's net flapped
    between 8,597 and 3,262 shares and traded on the difference.

WHAT IS NOT ESTABLISHED SAYS SO. `None` means not established and never
zero, exactly as `command_snapshot` already requires: an unread balance
and a zero balance look identical on a dashboard and mean opposite
things.
"""
from __future__ import annotations

from typing import Any, Iterable

# ── the standing labels, carried into every record and every screen ───

SHADOW_MODE = True
REAL_ORDER_SUBMISSION_ENABLED = False
CAPITAL_AT_RISK = 0

DISCLOSURE = ("SHADOW TRADING · NO REAL CAPITAL · "
              "COUNTERFACTUAL / SIMULATED EXECUTION")

# ── 2. THE ACTION VOCABULARY ──────────────────────────────────────────

NO_TRADE = "NO_TRADE"
HOLD = "HOLD"
BUY = "BUY"
SELL = "SELL"
CASH_OUT = "CASH_OUT"
PAIR = "PAIR"
COMPLETE_COMPLEMENT = "COMPLETE_COMPLEMENT"
CANCEL = "CANCEL"
REPRICE = "REPRICE"
REDUCE = "REDUCE"
HOLD_TO_SETTLEMENT = "HOLD_TO_SETTLEMENT"

ACTIONS = (NO_TRADE, HOLD, BUY, SELL, CASH_OUT, PAIR, COMPLETE_COMPLEMENT,
           CANCEL, REPRICE, REDUCE, HOLD_TO_SETTLEMENT)

# Actions that CROSS the spread and can therefore be reconstructed from
# observed depth. Everything else rests, and a resting order's fill is
# not observable without queue position.
MARKETABLE_ACTIONS = frozenset({BUY, SELL, CASH_OUT, PAIR,
                                COMPLETE_COMPLEMENT, REDUCE})
PASSIVE_ACTIONS = frozenset({REPRICE})
NO_EXECUTION_ACTIONS = frozenset({NO_TRADE, HOLD, HOLD_TO_SETTLEMENT,
                                  CANCEL})

# ── execution classes. The thing that keeps the books apart ───────────

MARKETABLE_RECONSTRUCTED = "MARKETABLE_RECONSTRUCTED"
PASSIVE_COUNTERFACTUAL_MARKOUT = "PASSIVE_COUNTERFACTUAL_MARKOUT"
PASSIVE_QUEUE_MODEL_ESTIMATE = "PASSIVE_QUEUE_MODEL_ESTIMATE"
ACTUAL_FILL = "ACTUAL_FILL"

EXECUTION_CLASSES = (MARKETABLE_RECONSTRUCTED,
                     PASSIVE_COUNTERFACTUAL_MARKOUT,
                     PASSIVE_QUEUE_MODEL_ESTIMATE,
                     ACTUAL_FILL)

# Only ONE of those classes may ever be presented as money that was
# actually made or lost, and it is empty by construction today.
REALIZABLE_CLASSES = frozenset({ACTUAL_FILL})
RESEARCH_CLASSES = frozenset({PASSIVE_COUNTERFACTUAL_MARKOUT,
                              PASSIVE_QUEUE_MODEL_ESTIMATE})

FILLED = "FILLED"
PARTIAL = "PARTIAL"
UNFILLED = "UNFILLED"
NOT_IDENTIFIED = "NOT_IDENTIFIED"

OBSERVED = "OBSERVED"
SCENARIO = "SCENARIO"

# ── 3. LATENCY ────────────────────────────────────────────────────────
#
# PREDECLARED, so that no one picks the flattering one after seeing the
# result. These are scenarios and are labelled as such until production
# execution latency is actually measured.

LATENCY_SCENARIOS_MS = (50, 100, 250, 500, 1000)


class ShadowRefusal(RuntimeError):
    """A shadow claim that would misrepresent what is known."""


def latency(data_ms=None, compute_ms=None, execution_ms=None,
            scenario_ms=None) -> dict:
    """The components, kept apart, and a total only when it is real.

    "Never use one blended latency number when its components are
    known." So the total is the sum of the components when all three are
    present and OBSERVED; when execution latency has not been measured a
    scenario stands in, the basis says SCENARIO, and the components that
    ARE known are still reported beside it rather than absorbed.
    """
    parts = {"dataLatencyMs": data_ms,
             "decisionComputeMs": compute_ms,
             "executionLatencyMs": execution_ms}
    known = [v for v in parts.values() if v is not None]

    # A TOTAL IS ONLY REPORTED WHEN EVERY COMPONENT IS ACCOUNTED FOR.
    # Summing the two parts you happen to have and calling it the
    # decision-to-arrival latency understates it by exactly the part you
    # did not measure, which is the part that hurts.
    if execution_ms is not None:
        basis = OBSERVED
        used = execution_ms
        total = sum(known) if len(known) == 3 else None
    elif scenario_ms is not None:
        if scenario_ms not in LATENCY_SCENARIOS_MS:
            raise ShadowRefusal(
                "refused: %r is not one of the predeclared latency "
                "scenarios %r. Inventing a value after the fact is how a "
                "grid becomes a flattering assumption."
                % (scenario_ms, list(LATENCY_SCENARIOS_MS)))
        basis = SCENARIO
        used = scenario_ms
        total = (data_ms + compute_ms + scenario_ms
                 if data_ms is not None and compute_ms is not None
                 else None)
    else:
        basis = NOT_IDENTIFIED
        used = None
        total = None

    return {**parts,
            "latencyBasis": basis,
            "latencyScenarioMs": scenario_ms if basis == SCENARIO else None,
            "appliedLatencyMs": used,
            "totalDecisionToArrivalMs": total}


# ── 4. MARKETABLE SHADOW FILLS ────────────────────────────────────────

def _levels(book, side: str) -> list:
    """Depth on the side we would TAKE from, best price first.

    A buy lifts asks; a sell hits bids. Getting this backwards would
    manufacture free money, so it is one function and it is tested.
    """
    if not isinstance(book, dict):
        return []
    key = "asks" if side == BUY else "bids"
    rows = book.get(key)
    if not isinstance(rows, list):
        return []
    out = []
    for row in rows:
        if isinstance(row, dict):
            price, qty = row.get("price"), row.get("qty", row.get("size"))
        elif isinstance(row, (list, tuple)) and len(row) >= 2:
            price, qty = row[0], row[1]
        else:
            continue
        try:
            price, qty = float(price), float(qty)
        except (TypeError, ValueError):
            continue
        if qty > 0:
            out.append((price, qty))
    out.sort(key=lambda pq: pq[0], reverse=(side != BUY))
    return out


def marketable_fill(side: str, quantity, limit_price, arrival_book,
                    decision_price=None) -> dict:
    """Walk the OBSERVED book at arrival. Never more than is there.

    Returns a NOT_IDENTIFIED result rather than a guess whenever the
    book state needed to answer honestly is absent. "Do not interpolate
    a profitable fill."
    """
    if side not in (BUY, SELL):
        raise ShadowRefusal("refused: %r is not a fillable side" % side)

    levels = _levels(arrival_book, side)
    if not levels:
        return {"status": NOT_IDENTIFIED,
                "executionClass": MARKETABLE_RECONSTRUCTED,
                "shadowFilledQty": None, "vwap": None,
                "slippage": None, "spreadCost": None,
                "unfilledQty": None,
                "why": "no observed depth at arrival; a fill here would "
                       "be interpolation, not reconstruction"}

    try:
        want = float(quantity)
    except (TypeError, ValueError):
        raise ShadowRefusal("refused: quantity %r is not a number"
                            % quantity)
    if want <= 0:
        raise ShadowRefusal("refused: quantity must be positive")

    limit = None if limit_price is None else float(limit_price)
    filled = 0.0
    notional = 0.0
    for price, qty in levels:
        # NEVER FILL AT A PRICE THAT NO LONGER EXISTS. A limit is a
        # limit: a buy takes only at or below it.
        if limit is not None:
            if side == BUY and price > limit:
                break
            if side == SELL and price < limit:
                break
        take = min(qty, want - filled)
        if take <= 0:
            break
        filled += take
        notional += take * price
        if filled >= want:
            break

    if filled <= 0:
        return {"status": UNFILLED,
                "executionClass": MARKETABLE_RECONSTRUCTED,
                "shadowFilledQty": 0.0, "vwap": None,
                "slippage": None, "spreadCost": None,
                "unfilledQty": want,
                "why": "the arrival book had no depth inside the limit"}

    vwap = notional / filled
    ref = decision_price if decision_price is not None else limit
    slippage = None
    if ref is not None:
        # positive slippage = worse than intended, both directions
        slippage = (vwap - float(ref)) if side == BUY else (float(ref) - vwap)

    top = levels[0][0]
    spread_cost = (vwap - top) if side == BUY else (top - vwap)

    return {"status": FILLED if filled >= want else PARTIAL,
            "executionClass": MARKETABLE_RECONSTRUCTED,
            "shadowFilledQty": filled,
            "vwap": vwap,
            "slippage": slippage,
            "spreadCost": spread_cost,
            "unfilledQty": max(0.0, want - filled),
            "why": None}


# ── 5. PASSIVE ORDERS: THREE TRACKS, NEVER CONFLATED ──────────────────

def passive_outcome(quote_markout=None, queue_model=None) -> dict:
    """A resting shadow intention.

    `queue_model` is honoured only if it declares itself decision-grade
    AND says so explicitly. Anything else leaves PASSIVE_FILL_STATUS at
    NOT_IDENTIFIED, which `economics` refuses to count as realized.
    """
    grade = bool(isinstance(queue_model, dict)
                 and queue_model.get("decisionGrade") is True)
    estimate = queue_model.get("estimatedFillQty") if grade else None

    return {
        # research output: where the quote went, which is NOT a fill
        "counterfactualQuoteMarkout": quote_markout,
        # research output: only when a model stands behind it
        "queueModelEstimatedFill": estimate,
        "queueModelDecisionGrade": grade,
        # the only one that could ever be money, and it is None
        "actualBettorFill": None,
        "passiveFillStatus": (FILLED if estimate else NOT_IDENTIFIED),
        "executionClass": (PASSIVE_QUEUE_MODEL_ESTIMATE if grade
                           else PASSIVE_COUNTERFACTUAL_MARKOUT),
        "why": None if grade else (
            "no decision-grade queue model: a price trading through our "
            "level is not evidence that we were filled"),
    }


# ── 6. PAIRING / COMPLEMENT ───────────────────────────────────────────

def pair_economics(leg_shares, leg_avg_cost, complement_price,
                   pair_basis=1.0) -> dict:
    """What completing the complement locks in.

    `pair_basis` is what the completed pair settles to -- 1.0 for a
    YES/NO pair on a binary contract. A loss-lock is NOT automatically an
    error, so this reports the number and leaves the judgement to the
    policy, which must record why it chose it.
    """
    for name, value in (("leg_shares", leg_shares),
                        ("leg_avg_cost", leg_avg_cost),
                        ("complement_price", complement_price)):
        if value is None:
            return {"pairCost": None, "lockedValue": None,
                    "lockedPnl": None, "capitalReleased": None,
                    "status": NOT_IDENTIFIED,
                    "why": "%s is not established" % name}

    shares = float(leg_shares)
    cost = float(leg_avg_cost)
    comp = float(complement_price)
    basis = float(pair_basis)

    pair_cost = shares * comp
    locked_value = shares * basis
    locked_pnl = locked_value - (shares * cost) - pair_cost
    return {"pairCost": pair_cost,
            "lockedValue": locked_value,
            "lockedPnl": locked_pnl,
            "capitalReleased": locked_value,
            "status": "IDENTIFIED",
            "why": None}


# ── 7. CASH-OUT: COMPARE, DO NOT ASSUME ───────────────────────────────

EXIT_ALTERNATIVES = (HOLD, CASH_OUT, PAIR, REPRICE, HOLD_TO_SETTLEMENT)


def choose_exit(alternatives: dict, uncertainty=None) -> dict:
    """Pick the permitted action with the strongest CONSERVATIVE EV.

    "Do not assume CASH_OUT is good because it reduces risk." Every
    alternative whose EV is not established stays unknown and is not
    silently treated as zero -- a zero would make it beat any negative
    option on arithmetic alone.
    """
    identified, unknown = {}, []
    for name, ev in (alternatives or {}).items():
        if name not in EXIT_ALTERNATIVES:
            raise ShadowRefusal("refused: %r is not an exit alternative"
                                % name)
        if ev is None:
            unknown.append(name)
        else:
            identified[name] = float(ev)

    if not identified:
        return {"chosen": None, "status": NOT_IDENTIFIED,
                "evaluated": {}, "unknown": sorted(unknown),
                "why": "no alternative had an established EV; choosing "
                       "among unknowns would be a coin toss wearing a "
                       "number"}

    # conservative: shade every EV by the stated uncertainty, so a
    # marginal winner does not beat a confident one on noise
    shade = 0.0 if uncertainty is None else abs(float(uncertainty))
    scored = {k: v - shade for k, v in identified.items()}
    best = max(scored, key=lambda k: (scored[k], k))
    return {"chosen": best,
            "status": "IDENTIFIED",
            "evaluated": identified,
            "conservative": scored,
            "unknown": sorted(unknown),
            "uncertaintyApplied": shade,
            "why": "strongest conservative EV after costs among the "
                   "alternatives whose EV was established"}


# ── 10. MANAGEMENT ECONOMICS, BUCKETED BY CLASS ───────────────────────

def economics(rows: Iterable[dict]) -> dict:
    """Money, grouped by HOW it was arrived at, with no grand total.

    "Do not combine simulated and observed economics into one unlabeled
    number." There is deliberately no key here that sums across classes,
    because that key is the one a reader would quote.
    """
    buckets: dict[str, dict[str, Any]] = {
        cls: {"pnl": 0.0, "rows": 0, "identified": 0}
        for cls in EXECUTION_CLASSES}
    unidentified = 0

    for row in rows or []:
        cls = (row or {}).get("executionClass")
        if cls not in buckets:
            raise ShadowRefusal(
                "refused: %r is not an execution class. Money without a "
                "class cannot be kept apart from money with one." % cls)
        bucket = buckets[cls]
        bucket["rows"] += 1
        status = row.get("status") or row.get("passiveFillStatus")
        pnl = row.get("pnl")
        if status == NOT_IDENTIFIED or pnl is None:
            unidentified += 1
            continue
        if cls == ACTUAL_FILL:
            raise ShadowRefusal(
                "refused: an ACTUAL_FILL carries P&L while "
                "REAL_ORDER_SUBMISSION is disabled and no order exists")
        bucket["pnl"] += float(pnl)
        bucket["identified"] += 1

    return {
        "byExecutionClass": buckets,
        "unidentifiedRows": unidentified,
        # named so no one has to infer it
        "actualFillPnl": None,
        "realizedFromRealOrders": None,
        "shadowMode": SHADOW_MODE,
        "capitalAtRisk": CAPITAL_AT_RISK,
        "disclosure": DISCLOSURE,
        "note": ("no total is reported across classes on purpose: "
                 "reconstructed, counterfactual and actual economics are "
                 "different claims and must not be added"),
    }


# ── 9. SCORING HORIZONS ───────────────────────────────────────────────

HORIZONS = ("5S", "30S", "60S", "300S", "SETTLEMENT")


def horizon_observable(horizon: str, source_interval_s=None) -> dict:
    """5S is unavailable where the source cannot support it.

    The existing horizon observability rule, kept: a five-second markout
    from a feed that ticks every thirty seconds is an interpolation
    wearing a timestamp.
    """
    if horizon not in HORIZONS:
        raise ShadowRefusal("refused: %r is not a scoring horizon" % horizon)
    if horizon == "SETTLEMENT":
        return {"observable": True, "why": None}
    seconds = float(horizon.rstrip("S"))
    if source_interval_s is None:
        return {"observable": False,
                "why": "source sampling interval is not established"}
    if float(source_interval_s) > seconds:
        return {"observable": False,
                "why": "source ticks every %gs, which cannot resolve a "
                       "%gs markout" % (float(source_interval_s), seconds)}
    return {"observable": True, "why": None}


# ── the record builder, which refuses a malformed claim ───────────────

REQUIRED_DECISION_FIELDS = (
    "shadowDecisionId", "symbol", "outcomeLeg", "modelVersion",
    "policyVersion", "evidenceSource", "decisionTs", "proposedAction")


def decision_record(**fields) -> dict:
    """One prospective decision, validated before it can be written.

    EVIDENCE SOURCE IS REQUIRED. "Do not merge sources silently" is only
    enforceable if every row knows which book it came from.
    """
    missing = [f for f in REQUIRED_DECISION_FIELDS if not fields.get(f)]
    if missing:
        raise ShadowRefusal("refused: a shadow decision needs %s"
                            % ", ".join(missing))
    action = fields["proposedAction"]
    if action not in ACTIONS:
        raise ShadowRefusal("refused: %r is not in the action vocabulary"
                            % action)
    if action not in NO_EXECUTION_ACTIONS and not fields.get("reasonCodes"):
        raise ShadowRefusal(
            "refused: %s must record WHY it dominated the alternatives"
            % action)

    record = dict(fields)
    record.setdefault("reasonCodes", [])
    record.setdefault("gateResults", {})
    record.setdefault("blockers", [])
    record.setdefault("alternatives", [])
    record["shadowMode"] = SHADOW_MODE
    record["capitalAtRisk"] = CAPITAL_AT_RISK
    record["realOrderSubmissionEnabled"] = REAL_ORDER_SUBMISSION_ENABLED
    record["disclosure"] = DISCLOSURE
    return record


def execution_class_for(action: str) -> str | None:
    """Which book an action's economics belong in, or None if it makes
    no execution at all."""
    if action in MARKETABLE_ACTIONS:
        return MARKETABLE_RECONSTRUCTED
    if action in PASSIVE_ACTIONS:
        return PASSIVE_COUNTERFACTUAL_MARKOUT
    if action in NO_EXECUTION_ACTIONS:
        return None
    raise ShadowRefusal("refused: %r is not in the action vocabulary"
                        % action)
