"""THE INDEPENDENT ENTRY GATE. One place, six requirements, no flag.

WHAT WAS ACTUALLY WRONG. `shadow_bettor.decide` computed the whole action
table and then called `shadow_lanes.not_yet_eligible`, which hard-sets
`proposedAction = NO_TRADE`. Its own docstring said so: "This function has
no path that produces a BUY or a SELL." So the refusal was
UNCONDITIONAL -- not a verdict that happened to be negative, but a branch
that did not exist. No commit replaced it; the audit was right, and this
module is the missing branch.

WHAT THIS DOES NOT DO. It does not lower any requirement so that
something can pass. Every condition below already had to be true before a
real entry would be defensible; the only change is that they are now
EVALUATED and named individually instead of standing behind one sentence.
With production's current inputs the gate still refuses -- and it refuses
for three separately reported reasons rather than one.

THE SIX REQUIREMENTS, ALL OF THEM, EVERY TIME.

  QUALIFIED_MODEL       a registered model whose target is a SETTLEMENT
                        outcome, whose status is frozen/promoted, and
                        whose predictions are valid AS OF ENTRY TIME.
                        A complement-target model is not a settlement
                        model and does not qualify by being present.
  INDEPENDENT_FAIR_VALUE a fair value that is not the venue's own price.
                        The venue midpoint is the BENCHMARK; using it as
                        the belief makes the test circular.
  EXECUTION_ESTIMATE    a fill estimate for THE SPECIFIC ACTION. `P_FILL`
                        being NOT_IDENTIFIED is a refusal, not a default.
  SIZING_POLICY         an explicit size. The book's available depth is
                        not our intended size.
  RISK_PERMITTED        the existing risk engine's verdict, consulted not
                        assumed.
  READABLE_BOOK         a readable two-sided book with established depth.

A MISSING INPUT IS A REFUSAL, NEVER A DEFAULT. Each check answers from a
supplied value or refuses by name; none of them has a fallback.
"""

from __future__ import annotations

NOT_IDENTIFIED = "NOT_IDENTIFIED"

REQUIREMENTS = (
    "QUALIFIED_MODEL",
    "INDEPENDENT_FAIR_VALUE",
    "EXECUTION_ESTIMATE",
    "SIZING_POLICY",
    "RISK_PERMITTED",
    "READABLE_BOOK",
)

# Refusal codes, one per requirement plus the economic ones.
R_NO_QUALIFIED_MODEL = "NO_QUALIFIED_MODEL"
R_MODEL_TARGET_MISMATCH = "MODEL_TARGET_IS_NOT_SETTLEMENT"
R_MODEL_NOT_FROZEN = "MODEL_NOT_FROZEN_OR_PROMOTED"
R_PREDICTIONS_INVALID_AS_ENTRY = "PREDICTIONS_INVALID_AS_ENTRY_TIME"
R_NO_INDEPENDENT_FV = "INDEPENDENT_FAIR_VALUE_NOT_ESTABLISHED"
R_FV_IS_VENUE_PRICE = "FAIR_VALUE_IS_THE_VENUE_BENCHMARK"
R_NO_EXECUTION_ESTIMATE = "EXECUTION_ESTIMATE_NOT_IDENTIFIED"
R_NO_SIZING = "SIZING_POLICY_NOT_APPLICABLE"
R_RISK_BLOCKED = "RISK_GATE_BLOCKED"
R_BOOK_UNREADABLE = "MARKET_STATE_UNREADABLE"
R_NO_DEPTH = "DEPTH_NOT_ESTABLISHED"
R_NO_POSITIVE_EDGE = "NO_ACTION_HAS_POSITIVE_NET_EDGE"
R_NO_ASK = "NO_EXECUTABLE_ASK"
R_NO_FEE_FUNCTION = "FEE_SCHEDULE_NOT_SUPPLIED"

# A settlement target is the only target that licenses an ENTRY. A model
# forecasting RN1's next complement is forecasting somebody's behaviour,
# not the outcome we would be paid on.
SETTLEMENT_TARGETS = ("SETTLEMENT_OUTCOME", "SETTLES_YES", "PAYOUT")

# THE PROJECT'S OWN VOCABULARY, not a new one. `shadow.ACTIONS` is the
# admitted set and `shadow.decision_record` refuses anything outside it,
# which is how a made-up action name gets caught at the boundary instead
# of reaching a row.
ENTRY_ACTION = "BUY"
ADMISSIBLE_MODEL_STATUS = ("FROZEN", "PROMOTED", "CHAMPION")


def qualify_model(model: dict | None) -> dict:
    """Does this registered model qualify to drive an ENTRY?

    Three independent ways to fail, reported separately, because "no
    model" and "a model aimed at the wrong thing" need different work.
    """
    if not model:
        return {"qualified": False, "refusals": [R_NO_QUALIFIED_MODEL],
                "why": "no model was supplied to the gate"}
    refusals = []
    target = str(model.get("target") or "").upper()
    status = str(model.get("status") or "").upper()
    if not any(t in target for t in SETTLEMENT_TARGETS):
        refusals.append(R_MODEL_TARGET_MISMATCH)
    if status not in ADMISSIBLE_MODEL_STATUS:
        refusals.append(R_MODEL_NOT_FROZEN)
    # PREDICTION VALIDITY IS NOT THE MODEL'S STATUS. A frozen model whose
    # recorded predictions are all INVALID_AS_ENTRY_TIME has never made a
    # usable call, and registering it did not change that.
    validity = str(model.get("prediction_validity") or "").upper()
    if validity != "VALID_AS_ENTRY_TIME":
        refusals.append(R_PREDICTIONS_INVALID_AS_ENTRY)
    return {"qualified": not refusals, "refusals": refusals,
            "model_key": model.get("model_key"),
            "version": model.get("version"),
            "target": model.get("target"), "status": model.get("status"),
            "prediction_validity": model.get("prediction_validity"),
            "why": ("qualifies" if not refusals else
                    "; ".join(refusals))}


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


R_EXTERNAL_NOT_ENABLED = "EXTERNAL_SOURCE_NOT_ENABLED_FOR_THIS_EXPERIMENT"
R_EXTERNAL_NO_PROBABILITY = "EXTERNAL_SOURCE_PRODUCED_NO_PROBABILITY"

#: The only source classes that may stand in for a qualified model, and
#: they do NOT become one. An external bookmaker valuation satisfies the
#: "what do you believe" requirement under an explicitly enabled
#: experiment; it never satisfies QUALIFIED_MODEL, and the output says so
#: in both directions so nothing downstream can confuse them.
EXTERNAL_SOURCE_CLASSES = ("EXTERNAL_BOOKMAKER_VALUATION",)


def admit(*, action_table=None, model=None, fair_value=None,
          execution_estimate=None, size=None, risk=None,
          market_state=None, fee_fn=None, fee_per_contract=None,
          min_net_edge_per_contract=0.0,
          external_source=None, external_enabled=False) -> dict:
    """Return an admissible entry, or every reason there is not one.

    `action_table` is `bettor_ev_bridge.evaluate()`'s table, unchanged.
    Everything else is an explicit input; nothing is inferred.
    """
    refusals: list[str] = []
    detail: dict = {}

    # ── the book ────────────────────────────────────────────────────
    ms = market_state or {}
    if not ms.get("readable"):
        refusals.append(R_BOOK_UNREADABLE)
    depth = (ms.get("depth") if ms.get("depth") is not None
             else (ms.get("microstructure") or {}).get("depth"))
    if depth in (None, NOT_IDENTIFIED) or _num(depth) in (None, 0):
        refusals.append(R_NO_DEPTH)

    # ── the model ───────────────────────────────────────────────────
    q = qualify_model(model)
    detail["model_qualification"] = q
    # ── the belief's PROVENANCE, and the two ways to have one ────────
    #
    # A qualified internal settlement model, or -- under an explicitly
    # enabled experiment -- a declared EXTERNAL source. The second is not
    # a promotion of the first: `qualified_model` stays False, the source
    # class is recorded by name, and every other requirement below is
    # unchanged. Without `external_source` this branch does nothing and
    # the gate behaves exactly as before; a test asserts that.
    ext = external_source or None
    ext_class = str((ext or {}).get("source_class") or "")
    if ext is None:
        refusals.extend(q["refusals"])
    elif not external_enabled:
        # A source supplied but the experiment not switched on is a
        # refusal, not a silent acceptance.
        refusals.append(R_EXTERNAL_NOT_ENABLED)
    elif ext_class not in EXTERNAL_SOURCE_CLASSES:
        refusals.append(R_NO_QUALIFIED_MODEL)
        detail["external_rejected_class"] = ext_class
    elif _num(ext.get("probability")) is None:
        refusals.append(R_EXTERNAL_NO_PROBABILITY)
        detail["external_refusals"] = list(ext.get("refusals") or [])
    else:
        detail["external_valuation"] = {
            "source_class": ext_class,
            "version": ext.get("version"),
            "label": ext.get("label"),
            "probability": _num(ext.get("probability")),
            "is_a_qualified_settlement_model": False,
            "satisfies": ("the INDEPENDENT_FAIR_VALUE requirement under a "
                          "labelled experiment, and QUALIFIED_MODEL not at "
                          "all"),
        }
    detail["qualified_model"] = bool(q["qualified"])
    detail["belief_provenance"] = (
        "QUALIFIED_INTERNAL_MODEL" if q["qualified"]
        else (ext_class or "NONE"))

    # ── the belief, which may not be the benchmark ──────────────────
    fv = fair_value or {}
    fv_value = _num(fv.get("value"))
    fv_kind = str(fv.get("kind") or "").upper()
    if fv_value is None:
        refusals.append(R_NO_INDEPENDENT_FV)
    elif "VENUE" in fv_kind or "MIDPOINT" in fv_kind or "BENCHMARK" in fv_kind:
        # THE CIRCULARITY GUARD. A benchmark cannot be evidence against
        # itself, so a venue-derived fair value never licenses an entry.
        refusals.append(R_FV_IS_VENUE_PRICE)

    # ── execution ───────────────────────────────────────────────────
    p_fill = _num((execution_estimate or {}).get("p_fill"))
    if p_fill is None or not 0.0 < p_fill <= 1.0:
        refusals.append(R_NO_EXECUTION_ESTIMATE)

    # ── size ────────────────────────────────────────────────────────
    qty = _num(size)
    if qty is None or qty <= 0:
        refusals.append(R_NO_SIZING)

    # ── risk ────────────────────────────────────────────────────────
    if not (risk or {}).get("permitted"):
        refusals.append(R_RISK_BLOCKED)

    # ── the economics ───────────────────────────────────────────────
    #
    # WHY THIS IS COMPUTED HERE AND NOT JUST READ. `bettor_ev_bridge`
    # never marks a BUY row IDENTIFIED, because settlement EV requires an
    # independent fair value and none is registered -- that is the whole
    # blocker. So reading its table alone can never yield an entry, and a
    # gate that only read it would be unreachable by construction: a
    # different unconditional refusal wearing a conditional shape.
    #
    # The missing arithmetic is the one the fair value unlocks:
    #
    #     edge per contract = fair_value - ask - fee(ask)
    #
    # The engine's table is still PREFERRED when it does carry an
    # identified row, so the day a fair value is registered there this
    # gate uses the engine's number rather than its own.
    best = None
    for row in (action_table or []):
        if row.get("status") != "IDENTIFIED":
            continue
        if not str(row.get("action", "")).upper().startswith(("BUY", "TAKE",
                                                              "POST")):
            continue
        edge = _num(row.get("expectedNetDollarsPerContract"))
        if edge is None:
            continue
        if best is None or edge > _num(best.get(
                "expectedNetDollarsPerContract")):
            best = row
    detail["engine_identified_row"] = best

    if best is None and fv_value is not None and fee_fn is not None:
        ask = _num(ms.get("ask"))
        if ask is None or ask <= 0:
            refusals.append(R_NO_ASK)
        else:
            # A FEE FUNCTION IS REQUIRED, NOT DEFAULTED. This project has
            # already shipped a lane that booked every fill free because a
            # zero-fee lambda was the fallback.
            #
            # THE REALISED FEE WINS WHEN THE CALLER HAS ONE. Re-deriving it
            # at one price assumes the whole quantity traded there. A
            # marketable order that walked .62/.64/.66 paid three different
            # fees, and the caller that walked the ladder knows their sum;
            # this function does not and must not guess it from an average.
            fee_per = (abs(float(fee_per_contract))
                       if fee_per_contract is not None
                       else abs(float(fee_fn(qty=1.0, price=ask))))
            detail["fee_per_contract_basis"] = (
                "REALISED_PER_LEVEL_SUPPLIED_BY_THE_CALLER"
                if fee_per_contract is not None
                else "RE_DERIVED_AT_THE_SUPPLIED_PRICE")
            edge = fv_value - ask - fee_per
            best = {"action": ENTRY_ACTION, "leg": ms.get("leg") or "YES",
                    "status": "IDENTIFIED_BY_ENTRY_GATE",
                    "price": ask,
                    "expectedNetDollarsPerContract": edge,
                    "components": {"fair_value": fv_value, "ask": ask,
                                   "fee_per_contract": fee_per},
                    "basis": ("computed by the entry gate from the supplied "
                              "independent fair value; the engine does not "
                              "identify settlement EV without one")}
            detail["gate_computed_row"] = best
    elif best is None and fee_fn is None and fv_value is not None:
        refusals.append(R_NO_FEE_FUNCTION)

    if best is None or _num(best.get("expectedNetDollarsPerContract")) is None \
            or _num(best["expectedNetDollarsPerContract"]) <= min_net_edge_per_contract:
        refusals.append(R_NO_POSITIVE_EDGE)
    detail["selected_row"] = best

    # DEDUPLICATED BUT ORDERED, so the report reads in the order the
    # requirements are declared rather than in check order.
    seen, ordered = set(), []
    for code in refusals:
        if code not in seen:
            seen.add(code)
            ordered.append(code)

    if ordered:
        return {"admissible": False, "action": None, "size": None,
                "refusals": ordered, "requirements": list(REQUIREMENTS),
                "detail": detail,
                "why": ("an entry requires ALL of %s; %d refusal(s): %s"
                        % (", ".join(REQUIREMENTS), len(ordered),
                           ", ".join(ordered)))}

    return {
        "admissible": True,
        "action": best["action"],
        "leg": best.get("leg"),
        "size": qty,
        "limit_price": _num(best.get("price")) or _num(best.get("limitPrice")),
        "expected_net_per_contract": _num(
            best["expectedNetDollarsPerContract"]),
        "p_fill": p_fill,
        "fair_value": fv_value,
        "fair_value_kind": fv.get("kind"),
        "model": {"model_key": q.get("model_key"), "version": q.get("version")},
        "refusals": [], "requirements": list(REQUIREMENTS),
        "detail": detail,
        "conditional_on_our_fill": {
            "execution_secured": False,
            "note": ("an admissible entry is a DECISION, not an execution. "
                     "p_fill is an estimate and the order is modelled"),
        },
        "why": "every declared requirement is satisfied",
    }
