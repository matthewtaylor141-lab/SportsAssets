"""THE EXTERNAL-VALUATION SHADOW EXPERIMENT, kept apart from everything.

It is its own experiment id, its own table and its own status tile. It
shares the entry gate, the fee schedule and the shadow order engine,
because sharing those is the point -- a valuation that only works through
a bespoke path has not been integrated. It shares NOTHING with RN1-seeded
management: different experiment, different rows, and the frozen pairing
and second-half exit policy are not consulted here at all.

WHAT IT DOES, in one line: compare an external bookmaker's de-vigged
probability against the SAME-VENUE executable ask after costs, and record
the comparison whether or not it clears.

WHAT IT REFUSES TO DO:

  * assume a resting fill. The comparison is against the ASK, crossing,
    because a resting price invents a queue position we never held. The
    execution estimate must be supplied and `p_fill` is never defaulted.
  * invent a second-half signal. This experiment only prices FULL-GAME
    markets; `period` must match explicitly and a segment contract is
    refused by PINNACLE_DEVIG_V1 rather than priced off the full game.
  * touch management's frozen exit policy. There is no exit logic here.
    An admitted entry is recorded as a shadow decision; the RN1X pairing
    and 16% trigger are a different experiment on different rows.
  * submit an order. `order_submitted` is always False and there is no
    code path that sets it True.

WHY IT IS SEPARATE FROM `bettor_fair_value`. That module reports
FV_BETTOR_INDEPENDENT = NOT_IDENTIFIED because the internal challenger was
measured WORSE than the venue price. This experiment does not revisit that
result, does not blend with it, and does not claim to supersede it. It is
a different question: not "is our model better than the market" but "does
a sharp book disagree with this venue by more than the cost of crossing".
"""

from __future__ import annotations

from . import bettor_entry_gate as gate
from . import bettor_pinnacle_devig as devig
from . import bettor_valuation_purpose as vp

EXPERIMENT_ID = "EXT_PINNACLE_DEVIG_V1_SHADOW"

#: WHAT A RECORD IS FOR. See `bettor_valuation_purpose`: an ENTRY_DECISION is
#: the lane's ordinary record; a CALIBRATION_ONLY record is written when the
#: venue read refused for book currency, so the odds source can still be
#: scored, and it can never become an entry.
PURPOSE_ENTRY_DECISION = vp.ENTRY_DECISION
PURPOSE_CALIBRATION_ONLY = vp.CALIBRATION_ONLY
R_CALIBRATION_ONLY = vp.R_CALIBRATION_ONLY
LABEL = ("EXTERNAL BOOKMAKER VALUATION, EXPERIMENTAL SHADOW. Not a trained "
         "proprietary model, not an internally qualified settlement model, "
         "and not shown to be profitable")

#: The control row that enables it. Absence is NOT permission, matching
#: every other loop in this stack.
CONTROL_KEY = "ext_pinnacle_shadow"

#: Minimum net edge per contract, in dollars, before an entry is
#: admissible. Deliberately > 0: at exactly zero the trade is a coin flip
#: that pays the fee, and the engine's own band study says the smallest
#: apparent edges are where thin consensus lives.
MIN_NET_EDGE_PER_CONTRACT = 0.01

#: Require the anchor book plus at least this many distinct sharp books on
#: the SAME outcome. The feed module's note is the reason: "a 1c edge
#: agreed by six books is a signal; the same 1c from one book is a
#: rounding error", and its per-OUTCOME depth exists because event-level
#: depth answered the wrong question (the winner's curse audit).
MIN_OUTCOME_BOOKS = 2

R_CONTROL_OFF = "EXPERIMENT_NOT_ARMED"
R_THIN_OUTCOME = "OUTCOME_DEPTH_BELOW_FLOOR"
R_NO_CREDENTIAL = "ODDS_CREDENTIAL_NOT_PRESENT_ON_THIS_SERVICE"


def credential_present(env=None) -> dict:
    """Is the odds key readable HERE? Reported, never printed.

    The key exists: `EDGE_ODDS_API_KEY` (32 chars) is provisioned on
    edge-shadow and is a repository secret. It is NOT on
    sportsassets-api, where this decision path runs -- env-keys at
    2026-09-23T22:10:54Z lists no odds-feed key at all. Copying a
    production credential between services is refused, so provisioning it
    on the target service is an owner action and this reports the absence
    by name instead of pretending to a feed it cannot reach.
    """
    import os

    src = os.environ if env is None else env
    present = bool(str(src.get("EDGE_ODDS_API_KEY") or "").strip())
    return {
        "present": present,
        "variable": "EDGE_ODDS_API_KEY",
        "refusal": None if present else R_NO_CREDENTIAL,
        "known_locations": ["edge-shadow (Render env)",
                            "EDGE_ODDS_API_KEY (repository secret)"],
        "why": (None if present else
                ("the valuation source is wired and fail-closed: it refuses "
                 "by name rather than reaching for a feed it has no key "
                 "for. Provisioning the key on this service is an owner "
                 "action; a credential is never copied between services "
                 "from here")),
    }


def evaluate(*, contract, quote, market_state, execution_estimate, size,
             risk, fee_fn, now, method=devig.DEFAULT_METHOD,
             outcome_books=None, armed=False,
             min_net_edge_per_contract=MIN_NET_EDGE_PER_CONTRACT,
             extra_refusals=None, payout_is_complement=False,
             execution_plan=None, record_purpose=vp.ENTRY_DECISION,
             calibration_only_evidence=None, corroboration=None) -> dict:
    """One contract, end to end, through the REAL gate.

    Returns a record that is persisted whether or not it clears, because
    the refusals are the deliverable when nothing clears.

    ── `record_purpose` ─────────────────────────────────────────────
    ENTRY_DECISION (the default) leaves every step below exactly as it was.
    CALIBRATION_ONLY runs the SAME valuation and the SAME gate -- so the
    record carries every refusal it would meet -- and is then SEALED by
    `_seal_calibration_only`: never admissible, no executable price, no
    size, no plan, the venue price it was compared at moved into
    `calibration_only_evidence` and flagged unusable for orders, and
    R_CALIBRATION_ONLY added to its refusals. Nothing a caller passes can
    unseal it.

    ── `execution_plan`, AND WHY THE ORDER MATTERS ──────────────────
    The execution estimate, the size and the price all depend on the
    VALUATION: a marketable order's limit is the break-even price the
    belief implies, and the size is the frozen notional at that limit.
    But the valuation is computed HERE, once, including the single
    complement inversion. A caller that wanted to size properly had two
    bad options: recompute the de-vig itself (two valuations that can
    drift), or pass placeholders (which is what the scheduled loop did
    -- `size=1.0` and a hand-written `risk={"permitted": True}`).

    So a caller may instead hand in a CALLABLE, invoked with the fair
    value this function computed, returning the estimate, the size, the
    risk verdict and the price to evaluate at:

        execution_plan(fair_value=float, contract=dict) -> {
            "execution_estimate": {...}, "size": float|None,
            "risk": {...}, "ask": float|None, "detail": {...}}

    One valuation, one inversion, and the sizing still happens outside
    this module. A plan that cannot answer returns None for `size` or
    `ask` and the gate refuses by name -- it is never a reason to fall
    back to the placeholder it replaced.
    """
    val = devig.valuation(contract=contract, quote=quote, now=now,
                          method=method)
    # THE EVENT THE CONTRACT ACTUALLY PAYS ON.
    #
    # The de-vig prices the SELECTION. On this venue a BUY_SHORT leg of
    # the same market pays on the COMPLEMENT of that selection, so the
    # probability to compare against its cost is 1 - p(selection).
    #
    # INVERTED EXACTLY ONCE, HERE. The caller supplies the flag and does
    # not pre-invert; the acquisition price it passes is already in cost
    # space (bettor_book_snapshot.acquisition_ladder). Inverting in both
    # places would silently restore the original outcome and look like a
    # working edge.
    #
    # The de-vig normalises over the COMPLETE outcome set and refuses a
    # partial one, so on a three-way book 1 - p(home) is exactly
    # p(away) + p(draw). It is NOT p(away): "NO home win" includes the
    # draw, and substituting the other team's price would be a different
    # event wearing the same number.
    _p_sel = val.get("probability")
    _p_pay = (None if _p_sel is None
              else (1.0 - float(_p_sel)) if payout_is_complement
              else float(_p_sel))
    rec: dict = {
        "experiment_id": EXPERIMENT_ID,
        "label": LABEL,
        "version": devig.VERSION,
        "source_class": devig.SOURCE_CLASS,
        "devig_method": method,
        "contract": dict(contract),
        "valuation": val,
        "probability": _p_pay,
        "probability_of_selection": _p_sel,
        "payout_is_complement": bool(payout_is_complement),
        "payout_event": (("NOT(%s)" % contract.get("selection"))
                         if payout_is_complement
                         else contract.get("selection")),
        "complement_note": (
            "probability is the probability of the event THIS CONTRACT "
            "PAYS ON. probability_of_selection is the de-vig's number for "
            "the selection itself. On a three-way book the complement of "
            "one outcome is the other two together, never the opposing "
            "team alone"),
        "raw_odds": val.get("raw_odds"),
        "observed_at": val.get("observed_at"),
        "received_at": val.get("received_at"),
        "age_s": val.get("age_s"),
        "mapped_outcome": val.get("mapped_outcome"),
        "order_submitted": False,
        "shadow_only": True,
        "refusals": list(val.get("refusals") or []),
    }

    # REFUSALS THE CALLER ALREADY ESTABLISHED, carried in rather than
    # short-circuited. The runtime loop can only learn some things --
    # whether the venue's settlement rule is established, for instance --
    # after it has done work this function would otherwise repeat. Handing
    # them in keeps the RECORD complete: the row still carries the odds,
    # the mapping, the venue quote, the probability and the costs, so
    # management can see what the engine was looking at when it refused,
    # instead of the row not existing at all.
    for code in (extra_refusals or []):
        if code not in rec["refusals"]:
            rec["refusals"].append(str(code))
    rec["caller_refusals"] = [str(c) for c in (extra_refusals or [])]
    if corroboration is not None:
        # Attached before anything can return, so every record that was
        # handed corroboration evidence persists it (migration 195). The
        # PinnAPI probability is the one de-vig above, never recomputed.
        rec["corroboration"] = corroboration
        pin = corroboration.setdefault("pinnapi", {})
        pin["probability_of_selection"] = _p_sel
        pin["probability"] = _p_pay

    if not armed:
        # Checked BEFORE anything else consumes budget or claims a
        # decision: an unarmed experiment records why and stops.
        rec["refusals"].insert(0, R_CONTROL_OFF)
        rec["decision"] = "NO_TRADE"
        rec["why"] = "the experiment's control row is not true"
        return _with_purpose(rec, record_purpose, calibration_only_evidence)

    # PER-OUTCOME depth, not per-event. The anchor alone is a rounding
    # error; the feed module's own audit is cited in MIN_OUTCOME_BOOKS.
    books = outcome_books if outcome_books is None else int(outcome_books)
    rec["outcome_books"] = books
    # ── CORROBORATION (valuation_corroboration, migration 195) ──────────
    #
    # THE FLOOR IS NOT LOWERED AND THIS CHECK IS NOT WEAKENED. A PinnAPI read
    # is Pinnacle alone and keeps `outcome_books` = 1 on the record. A
    # SEPARATE, independent multi-book observation may satisfy the SAME
    # `MIN_OUTCOME_BOOKS` floor only when its verdict qualified (exact
    # identity, current within PINNACLE_MAX_AGE_S, not the same source) and
    # its own count, under its own name, reaches the floor here. Otherwise the
    # record keeps R_THIN_OUTCOME and gains the corroboration's specific
    # refusal beside it. `corroborating_outcome_books` is never copied into
    # `outcome_books`.
    corr_books, corr_ok = None, False
    if corroboration is not None:
        corr_books = corroboration.get("corroborating_outcome_books")
        corr_ok = (corroboration.get("qualified") is True
                   and isinstance(corr_books, int)
                   and not isinstance(corr_books, bool)
                   and corr_books >= MIN_OUTCOME_BOOKS
                   and corroboration.get("refusal") is None
                   and (corroboration.get("identity") or {}).get("matched")
                   is True
                   and str((corroboration.get("identity") or {})
                           .get("outcome")) == str(contract.get("selection")))
    floor_by_this_read = books is not None and books >= MIN_OUTCOME_BOOKS
    rec["outcome_depth"] = {
        "outcome_books": books,
        "outcome_books_is": "THIS_READ_ONLY",
        "corroborating_outcome_books": corr_books,
        "corroboration_qualified": bool(corr_ok),
        "min_outcome_books": MIN_OUTCOME_BOOKS,
        "floor_satisfied_by": ("THIS_READ" if floor_by_this_read else
                               "INDEPENDENT_CORROBORATION" if corr_ok
                               else None)}
    if _p_pay is not None and not floor_by_this_read and not corr_ok:
        rec["refusals"].append(R_THIN_OUTCOME)
        if corroboration is not None:
            code = str(corroboration.get("refusal")
                       or "CORROBORATION_IDENTITY_MISMATCH")
            if code not in rec["refusals"]:
                rec["refusals"].append(code)

    # THE PLAN, BUILT ON THE VALUATION THIS FUNCTION JUST COMPUTED.
    # Invoked after the complement inversion and before anything reads a
    # price, so the sizing, the limit and the risk verdict all describe
    # the same payout event as the probability does. A plan that raises
    # is recorded as a refusal rather than allowed to abort the record --
    # the row, with its odds and its costs, is the deliverable.
    market_state = dict(market_state or {})
    plan_fee_per = None
    if execution_plan is not None:
        if _p_pay is None:
            rec["execution_plan"] = {
                "built": False,
                "why": ("no probability, so there is no break-even limit "
                        "to size against and no plan was attempted")}
        else:
            try:
                plan = execution_plan(fair_value=float(_p_pay),
                                      contract=dict(contract)) or {}
            except Exception as exc:                           # noqa: BLE001
                plan = {}
                rec["refusals"].append("EXECUTION_PLAN_FAILED:%s"
                                       % type(exc).__name__)
            rec["execution_plan"] = plan.get("detail") or {
                "built": bool(plan)}
            # THE PLAN'S OWN NAMED REASONS, kept. The gate can only say
            # EXECUTION_ESTIMATE_NOT_IDENTIFIED; it cannot say whether
            # the ladder was unreadable or was simply priced beyond
            # break-even, and those are different problems for whoever
            # reads the cycle report.
            rec["plan_refusals"] = [str(c)
                                    for c in (plan.get("refusals") or [])]
            for code in rec["plan_refusals"]:
                if code not in rec["refusals"]:
                    rec["refusals"].append(code)
            execution_estimate = plan.get("execution_estimate",
                                          execution_estimate)
            size = plan.get("size", size)
            risk = plan.get("risk", risk)
            if plan.get("ask") is not None:
                # THE PRICE OF THE QUANTITY ACTUALLY CLAIMED. Pricing a
                # multi-level size off level one understates the cost and
                # turns depth into edge.
                #
                # WHICH price this is, is the plan's to say, not ours. The
                # acquisition cost of the walked quantity, the limit that
                # would be submitted, and any worst-case reservation are
                # three different numbers, and a hard-coded label here
                # once asserted the first while the loop handed in the
                # second -- the economic comparison then measured the
                # break-even price against itself and found no edge.
                market_state["ask"] = plan["ask"]
                market_state["ask_basis"] = str(
                    plan.get("ask_basis")
                    or "ACQUISITION_PRICE_BASIS_NOT_STATED_BY_THE_PLAN")
            if plan.get("fee_per_contract") is not None:
                plan_fee_per = abs(float(plan["fee_per_contract"]))
            # THE OTHER TWO PRICES, kept and never compared. Recording
            # them beside the acquisition cost is what makes a later
            # reader able to tell that the order went out at one price
            # and the economics were measured at another.
            for _k in ("submitted_limit", "worst_case_cost_per_contract"):
                if plan.get(_k) is not None:
                    rec[_k] = float(plan[_k])

    ask = (market_state or {}).get("ask")
    fee_per = None
    if plan_fee_per is not None:
        # PER-LEVEL FEES AS ACTUALLY WALKED. A fee re-derived at one
        # price is the right number only for a single-level fill; a
        # multi-level walk pays a different fee at each level.
        fee_per = plan_fee_per
        rec["cost_per_contract_basis"] = ("SUM_OF_PER_LEVEL_FEES_OVER_THE"
                                          "_WALKED_QUANTITY")
    elif ask is not None and fee_fn is not None:
        try:
            fee_per = abs(float(fee_fn(qty=1.0, price=float(ask))))
            rec["cost_per_contract_basis"] = "RE_DERIVED_AT_THE_ASK"
        except Exception:                                      # noqa: BLE001
            fee_per = None
    rec["executable_price"] = None if ask is None else float(ask)
    rec["executable_price_basis"] = (market_state or {}).get("ask_basis")
    rec["cost_per_contract"] = fee_per
    if _p_pay is not None and ask is not None \
            and fee_per is not None:
        # THE COMPARISON, stated once: the probability of the event this
        # contract PAYS ON, against the ACQUISITION price of that same
        # contract, after the cost of crossing. Both sides of this
        # subtraction describe the same payout event.
        rec["estimated_edge_per_contract"] = (
            float(_p_pay) - float(ask) - fee_per)
    else:
        rec["estimated_edge_per_contract"] = None

    admitted = gate.admit(
        action_table=None,
        model=None,
        fair_value=({"value": _p_pay,
                     "kind": devig.SOURCE_CLASS}
                    if _p_pay is not None else None),
        execution_estimate=execution_estimate,
        size=size, risk=risk, market_state=market_state, fee_fn=fee_fn,
        # SAME FEE ON BOTH SIDES. Without this the gate re-derives the
        # fee at the acquisition price while this module used the walked
        # per-level fees, and the two edges disagree by the difference.
        fee_per_contract=fee_per,
        min_net_edge_per_contract=min_net_edge_per_contract,
        external_source=val if _p_pay is not None else None,
        external_enabled=True)
    rec["gate"] = admitted
    for code in admitted.get("refusals", []):
        if code not in rec["refusals"]:
            rec["refusals"].append(code)

    # A thin outcome is a refusal of OURS, so it must veto admission even
    # though the gate knows nothing about book depth. The same is true of
    # every refusal the caller handed in: a record that carries a refusal
    # and is still admissible would make the refusal decorative.
    rec["admissible"] = bool(admitted.get("admissible")) and \
        R_THIN_OUTCOME not in rec["refusals"] and \
        not rec["caller_refusals"] and \
        not rec.get("plan_refusals")
    rec["decision"] = "BUY" if rec["admissible"] else "NO_TRADE"
    rec["proposed_size"] = size if rec["admissible"] else None
    rec["why"] = (("external probability %.4f on %s vs acquisition "
                   "%.4f less cost %.4f"
                   % (_p_pay, rec["payout_event"], float(ask), fee_per))
                  if rec["admissible"] else
                  ("; ".join(rec["refusals"]) or "no reason recorded"))
    return _with_purpose(rec, record_purpose, calibration_only_evidence)


def _with_purpose(rec: dict, purpose, evidence) -> dict:
    """Stamp the record's purpose and, for anything but an entry decision,
    seal it. An ENTRY_DECISION record is returned with the purpose added and
    nothing else touched."""
    p = str(purpose if purpose is not None else vp.ENTRY_DECISION)
    rec["record_purpose"] = p
    if p == vp.ENTRY_DECISION and evidence is None:
        return rec
    if p not in vp.PURPOSES:
        # AN UNRECOGNISED PURPOSE IS REFUSED, NOT DEFAULTED. `persist` refuses
        # to write it at all; this makes the in-memory record say so too.
        if vp.R_UNKNOWN_PURPOSE not in rec["refusals"]:
            rec["refusals"].append(vp.R_UNKNOWN_PURPOSE)
        rec.update(admissible=False, decision="NO_TRADE", proposed_size=None,
                   why="; ".join(rec["refusals"]))
        return rec
    # An ENTRY_DECISION handed calibration evidence is a contradiction; the
    # evidence wins, because it is only ever attached by the calibration path
    # and reading it as an entry is the direction that could trade.
    rec["record_purpose"] = vp.CALIBRATION_ONLY
    return _seal_calibration_only(rec, evidence)


def _seal_calibration_only(rec: dict, evidence) -> dict:
    """THE CALIBRATION-ONLY RECORD, MADE STRUCTURALLY UNABLE TO TRADE.

    The valuation and the gate ran exactly as for an entry, so the record
    carries every refusal a real candidate would meet. What changes is what
    the record may CLAIM:

      * the venue price it was compared at was DISPLAYED on a book whose
        currency was not established. It is moved out of `executable_price`
        -- a column readers take as the same-venue ask -- into the evidence,
        with the cost and edge computed at it, and labelled unusable for
        orders. The row's economics columns are NULL;
      * no size, no execution estimate, no risk verdict, no exposure: a plan
        handed in by mistake is discarded, and the evidence says so;
      * never admissible, whatever the gate said, and R_CALIBRATION_ONLY is
        one of its refusals so every census can count it.

    Migration 144 enforces the same on the row, so a later edit to this
    function cannot write a calibration-only row that could trade.
    """
    ev = dict(evidence or {})
    displayed = dict(ev.get("displayed_quote") or {})
    displayed["usable_for_orders"] = False
    displayed.setdefault("what_this_is", vp.DISPLAYED_NOT_AN_ORDER_PRICE)
    ev["displayed_quote"] = displayed
    ev["compared_at_the_displayed_price"] = {
        "price": rec.get("executable_price"),
        "price_basis": rec.get("executable_price_basis"),
        "cost_per_contract": rec.get("cost_per_contract"),
        "cost_per_contract_basis": rec.get("cost_per_contract_basis"),
        "edge_per_contract": rec.get("estimated_edge_per_contract"),
        "usable_for_orders": False,
        "what_this_is": (
            "the edge the gate computed against the DISPLAYED price, kept so "
            "a real candidate is traced through the economics too. It is not "
            "an executable edge and no reader may size against it")}
    ev["gate_refusals"] = list((rec.get("gate") or {}).get("refusals") or [])
    if rec.pop("execution_plan", None) is not None:
        ev["execution_plan_discarded"] = (
            "a plan was handed to a calibration-only record and discarded: "
            "such a record carries no size, estimate or risk verdict")
    for k in ("submitted_limit", "worst_case_cost_per_contract",
              "plan_refusals"):
        rec.pop(k, None)
    ev["record_purpose"] = vp.CALIBRATION_ONLY
    ev["usable_for_orders"] = False
    ev["why"] = vp.WHY_CALIBRATION_ONLY_CANNOT_TRADE
    if isinstance(rec.get("gate"), dict):
        rec["gate"] = dict(rec["gate"],
                           priced_against=("A_DISPLAYED_PRICE_NOT_USABLE_"
                                           "FOR_ORDERS"))
    rec.update(executable_price=None, executable_price_basis=None,
               cost_per_contract=None, cost_per_contract_basis=None,
               estimated_edge_per_contract=None, proposed_size=None,
               admissible=False, decision="NO_TRADE",
               record_purpose=vp.CALIBRATION_ONLY,
               calibration_only_evidence=ev)
    if vp.R_CALIBRATION_ONLY not in rec["refusals"]:
        rec["refusals"].append(vp.R_CALIBRATION_ONLY)
    rec["why"] = "; ".join(rec["refusals"])
    return rec


def describe() -> dict:
    return {
        "experiment_id": EXPERIMENT_ID,
        "label": LABEL,
        "control_key": CONTROL_KEY,
        "source": devig.describe(),
        "min_net_edge_per_contract": MIN_NET_EDGE_PER_CONTRACT,
        "min_outcome_books": MIN_OUTCOME_BOOKS,
        "assumes_resting_fills": False,
        "prices_segments": False,
        "touches_frozen_exit_policy": False,
        "submits_orders": False,
        "separate_from_rn1_seeded_management": True,
        "credential": credential_present(),
    }


# ── persistence ─────────────────────────────────────────────────────

INSERT = """
    INSERT INTO external_valuations
        (experiment_id, version, source_class, provider, book, devig_method,
         venue, condition_id, us_market_slug, contract_identity_basis,
         contract_selection, sport_family, market,
         period, line, settlement_rule, event_key,
         raw_odds, outcomes_priced, expected_outcomes, overround,
         observed_at, received_at, age_s, outcome_books,
         mapped_outcome, mapping_match,
         probability, executable_price, cost_per_contract,
         estimated_edge_per_contract,
         decision, admissible, refusals, why, proposed_size,
         payout_event, payout_event_basis, probability_event,
         payout_is_complement, buy_intent, matched_side_norm,
         resolver_asked_for, ladder_side,
         -- THE REST OF THE DECISION. See migration 116: without these the
         -- production row could not answer what the fill estimate was,
         -- what size the policy chose, which rail passed or what the
         -- settlement comparison found.
         execution_estimate, risk_verdict, exposure_observed,
         settlement_comparison,
         -- WHAT THE ROW IS FOR (migration 144): ENTRY_DECISION with no
         -- evidence -- exactly the column default -- or CALIBRATION_ONLY with
         -- the refused read and the displayed price it was compared at.
         record_purpose, calibration_only_evidence)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,
            $18::jsonb,$19,$20,$21,
            CASE WHEN $22::double precision IS NULL THEN NULL
                 ELSE to_timestamp($22) END,
            CASE WHEN $23::double precision IS NULL THEN NULL
                 ELSE to_timestamp($23) END,
            $24,$25,$26,$27,$28,$29,$30,$31,$32,$33,$34,$35,$36,
            $37,$38,$39,$40,$41,$42,$43,$44,
            $45::jsonb,$46::jsonb,$47::jsonb,$48::jsonb,
            $49,$50::jsonb)
    -- BARE `DO NOTHING`, deliberately. Migration 105's uniqueness is an
    -- EXPRESSION index (coalesce over the nullable key columns), and
    -- `ON CONFLICT ON CONSTRAINT` cannot name an index, while inferring
    -- it would mean repeating the whole coalesce list here and keeping
    -- two copies in step. The only unique things on this table are the
    -- serial primary key -- which this statement never supplies -- and
    -- that index, so an untargeted DO NOTHING can only mean "this
    -- observation is already recorded".
    --
    -- A skipped insert RETURNS NO ROW, so `persist` returns None and the
    -- caller must not count it as written.
    ON CONFLICT DO NOTHING
    RETURNING id
"""

#: ONE STATEMENT FOR BOTH PURPOSES, with every value explicit. An
#: ENTRY_DECISION row goes down with `record_purpose = 'ENTRY_DECISION'` and
#: no evidence -- exactly what the column default would have written, so the
#: row an entry decision produces is the row it always produced. On a database
#: where migration 144 has not run, the write fails on the missing column and
#: is counted by name: a calibration-only record can never land in a table
#: that cannot say what it is for, where it would read as an entry decision.

JOIN_OUTCOME = """
    UPDATE external_valuations
       SET outcome_known = TRUE, outcome = $2,
           outcome_at = to_timestamp($3), realised_net_usd = $4
     WHERE id = $1 AND outcome_known = FALSE
"""


def _plan_json(rec: dict, section: str):
    """One section of the entry plan as JSON, or None if it was not built.

    None and `{}` mean different things here: the first says the lane
    never got far enough to compute this, the second would say it computed
    an empty answer. A row that cannot tell them apart cannot be used to
    find out where a cycle stopped.
    """
    import json as _json

    got = (rec.get("execution_plan") or {}).get(section)
    return None if got is None else _json.dumps(got, default=str)


async def persist(conn, rec: dict) -> int | None:
    """Store the record -- admissible or refused, both.

    Returns the row id. A refused record is stored with its full refusal
    list because that list is what management inspects to see WHY the
    engine did not buy.

    The record's PURPOSE goes on the row: an entry decision with no
    evidence, or a calibration-only record with its evidence. A
    calibration-only record that is admissible, or that lost its evidence,
    is refused here before the database refuses it too. An unrecognised
    purpose is never written.
    """
    import json

    purpose = vp.purpose_of(rec)
    if purpose not in vp.PURPOSES:
        raise ValueError("%s: %r" % (vp.R_UNKNOWN_PURPOSE, purpose))
    evidence_json = None
    if purpose == vp.CALIBRATION_ONLY:
        ev = rec.get("calibration_only_evidence")
        if rec.get("admissible") or not isinstance(ev, dict) \
                or ev.get("usable_for_orders") is not False:
            raise ValueError(
                "%s: a calibration-only record must be inadmissible and carry "
                "its evidence, flagged unusable for orders"
                % vp.R_CALIBRATION_ONLY)
        evidence_json = json.dumps(ev, default=str)
    c = rec.get("contract") or {}
    v = rec.get("valuation") or {}
    corroboration = rec.get("corroboration")
    if corroboration is not None:
        # A RECORD CARRYING CORROBORATION EVIDENCE IS WRITTEN WITH ITS ROW, in
        # one transaction (migration 195): the valuation never exists without
        # the separate, explicit record of what did or did not corroborate it.
        # The valuation row itself is the unchanged statement below.
        from . import valuation_corroboration as corr
        async with conn.transaction():
            row_id = await persist(conn, {k: x for k, x in rec.items()
                                          if k != "corroboration"})
            if row_id is not None:
                await conn.execute(corr.INSERT,
                                   *corr.insert_args(row_id, corroboration))
        return row_id
    return await conn.fetchval(
        INSERT,
        rec.get("experiment_id") or EXPERIMENT_ID,
        rec.get("version") or devig.VERSION,
        rec.get("source_class") or devig.SOURCE_CLASS,
        ("pinnapi.com/raw-websocket" if rec.get("provider") ==
         "pinnapi.com/raw-websocket" else devig.PROVIDER), devig.BOOK,
        rec.get("devig_method") or devig.DEFAULT_METHOD,
        str(c.get("venue") or ""), c.get("condition_id"),
        # THE VENUE-NATIVE IDENTITY, beside the global one and never
        # derived from it. A row that carries only the global id cannot be
        # looked up at the venue, which is the defect this fixes.
        c.get("us_market_slug"),
        (c.get("contract_identity_basis")
         or ("BOTH_PRESENT_AND_INDEPENDENTLY_SOURCED"
             if c.get("condition_id") and c.get("us_market_slug")
             else "VENUE_NATIVE_US_SLUG" if c.get("us_market_slug")
             else "GLOBAL_CONDITION_ID" if c.get("condition_id")
             else "NEITHER_IDENTITY_RECORDED")),
        str(c.get("selection") or ""), str(c.get("sport_family") or ""),
        str(c.get("market") or ""),
        (None if c.get("period") is None else str(c["period"])),
        (None if c.get("line") is None else float(c["line"])),
        (None if c.get("settlement_rule") is None
         else str(c["settlement_rule"])),
        (None if c.get("event_key") is None else str(c["event_key"])),
        json.dumps(v.get("raw_odds") or {}),
        int(v.get("outcomes_priced") or 0),
        int(v.get("expected_outcomes") or 0),
        (None if v.get("overround") is None else float(v["overround"])),
        rec.get("observed_at"), rec.get("received_at"),
        (None if rec.get("age_s") is None else float(rec["age_s"])),
        rec.get("outcome_books"),
        rec.get("mapped_outcome"), v.get("mapping_match"),
        (None if rec.get("probability") is None
         else float(rec["probability"])),
        rec.get("executable_price"), rec.get("cost_per_contract"),
        rec.get("estimated_edge_per_contract"),
        rec.get("decision") or "NO_TRADE",
        bool(rec.get("admissible")),
        list(rec.get("refusals") or []),
        str(rec.get("why") or "")[:2000],
        rec.get("proposed_size"),
        # ── THE PAYOUT IDENTITY, ON THE ROW ──────────────────────────
        #
        # A row that records `contract_selection` and `probability` but
        # not which EVENT the probability describes cannot be re-checked,
        # which is why every row written before migration 108 is held
        # rather than cleared. These columns end that: the event the
        # contract pays on, the basis for saying so, the event the
        # probability describes, and the venue side actually matched.
        #
        # `buy_intent` and `ladder_side` are stored too, and they are
        # deliberately NOT the source of the payout event -- they are the
        # evidence that the intent only ever chose a ladder.
        rec.get("payout_event") or (c.get("payout_event")
                                    or c.get("selection")),
        (c.get("payout_event_basis")
         or "RESOLVER_MATCHED_A_SIDE_FOR_THE_REQUESTED_OUTCOME"),
        rec.get("probability_event") or (c.get("probability_event")
                                         or c.get("selection")),
        bool(rec.get("payout_is_complement")),
        c.get("buy_intent"),
        c.get("matched_side_norm"),
        c.get("resolver_asked_for") or c.get("selection"),
        c.get("ladder_side"),
        # ── THE ENTRY PLAN, AS IT WAS COMPUTED ───────────────────────
        # `_plan_json` returns None rather than {} when a section was
        # never built, so an absent estimate is visibly absent instead of
        # reading as an empty one.
        _plan_json(rec, "execution"),
        _plan_json(rec, "risk"),
        _plan_json(rec, "exposure"),
        (None if rec.get("settlement_comparison") is None
         else json.dumps(rec["settlement_comparison"], default=str)),
        # ── WHAT THE ROW IS FOR (migration 144) ───────────────────────
        purpose, evidence_json)


#: THE CENSUS READS BELOW ARE REPORTING, over EVERY record purpose (migration
#: 144): they count what the lane recorded and act on nothing. A
#: calibration-only row carries its venue-read refusal first and
#: CALIBRATION_ONLY_RECORD_IS_NOT_AN_ENTRY_CANDIDATE among its refusals, so it
#: is attributed to 2_FRESHNESS and never reads as admissible; SUMMARY counts
#: it apart. Readers that SELECT candidates filter record_purpose instead.
REFUSAL_CENSUS = """
    SELECT unnest(refusals) AS refusal, count(*) AS n
      FROM external_valuations
     WHERE experiment_id = $1 AND decided_at >= now() - ($2 || ' hours')::interval
     GROUP BY 1 ORDER BY 2 DESC
"""

SUMMARY = """
    SELECT count(*) AS evaluated,
           count(*) FILTER (WHERE admissible) AS admissible,
           count(*) FILTER (WHERE NOT admissible) AS refused,
           count(*) FILTER (WHERE probability IS NOT NULL) AS priced,
           count(*) FILTER (WHERE outcome_known) AS settled,
           min(decided_at) AS first_at, max(decided_at) AS last_at,
           -- WHICH IDENTITY EACH ROW CARRIES. A count of valuations says
           -- nothing about whether the contracts can be looked up at the
           -- venue, and that distinction is the whole of run 24's finding.
           count(*) FILTER (WHERE us_market_slug IS NOT NULL)
               AS with_venue_native_identity,
           count(*) FILTER (WHERE condition_id IS NOT NULL)
               AS with_global_condition_id,
           count(*) FILTER (WHERE us_market_slug IS NOT NULL
                              AND condition_id IS NOT NULL)
               AS with_both,
           -- WHAT EACH ROW IS FOR (migration 144). A calibration-only row is
           -- a valuation recorded while the venue read refused; counting it
           -- among "evaluated" without saying so would overstate the entry
           -- lane's reach.
           count(*) FILTER (WHERE record_purpose = 'CALIBRATION_ONLY')
               AS calibration_only
      FROM external_valuations
     WHERE experiment_id = $1
"""

#: THE SAME TOTALS, INSIDE THE WINDOW THE REST OF THE CENSUS USES.
#:
#: THE MIS-READING THIS PREVENTS, AND IT NEARLY CAUGHT ME. `SUMMARY` takes
#: no window: it is ALL TIME. `REFUSAL_CENSUS` and `STAGE_CENSUS` both take
#: `hours`. So a report printing `evaluated 386` beside `4_SETTLEMENT_SCOPE
#: 63` was putting an all-time numerator next to a six-hour one, and the 63
#: + 15 that legitimately add to the window's 78 candidates looked like they
#: had lost 308 rows somewhere. Two denominators side by side with no label
#: is exactly the shape of the overstatement the stage breakdown exists to
#: stop, so the windowed totals are computed and reported as their own
#: block rather than left for a reader to reconcile.
SUMMARY_IN_WINDOW = """
    SELECT count(*) AS evaluated,
           count(*) FILTER (WHERE admissible) AS admissible,
           count(*) FILTER (WHERE NOT admissible) AS refused,
           count(*) FILTER (WHERE probability IS NOT NULL) AS priced,
           count(*) FILTER (WHERE record_purpose = 'CALIBRATION_ONLY')
               AS calibration_only,
           min(decided_at) AS first_at, max(decided_at) AS last_at
      FROM external_valuations
     WHERE experiment_id = $1
       AND decided_at >= now() - ($2 || ' hours')::interval
"""

#: The identity split on its own, so the census can show it per basis.
IDENTITY_CENSUS = """
    SELECT coalesce(contract_identity_basis, 'UNLABELLED') AS basis,
           count(*) AS n,
           count(*) FILTER (WHERE admissible) AS admissible,
           max(decided_at) AS last_at
      FROM external_valuations
     WHERE experiment_id = $1
     GROUP BY 1 ORDER BY 1
"""


# ── WHERE IN THE PIPELINE A CANDIDATE STOPPED ────────────────────────
#
# THE CLAIM THIS CORRECTS, AND IT WAS MINE. I reported that every
# production candidate "lacked profitable depth" because each row showed
# empty walk and vwap fields. That does not follow. A candidate refused at
# the SETTLEMENT SCOPE stage never reaches execution estimation at all, so
# its walk fields are empty because the walk was never attempted -- not
# because the book was thin. And `NO_ACTION_HAS_POSITIVE_NET_EDGE` can
# fire on a candidate with no execution estimate, because the gate then
# compares the probability against the observed best ask; that is a real
# negative edge at the TOP of the book, which is a narrower statement than
# "no profitable depth exists".
#
# So the refusals are attributed to STAGES, in the order the lane runs
# them, and a candidate is counted at the EARLIEST stage that refused it.
# A candidate carries several refusals; the earliest one is the only one
# that describes where it actually stopped.
STAGES = (
    ("1_PROBABILITY", (
        "INDEPENDENT_FAIR_VALUE_NOT_ESTABLISHED",
        "NO_QUALIFIED_MODEL",
        "THIN_OUTCOME_COVERAGE",
        R_THIN_OUTCOME,
        # WHY AN INDEPENDENT OBSERVATION DID NOT SATISFY THE OUTCOME-DEPTH
        # FLOOR FOR A PINNAPI READ (valuation_corroboration). Each rides
        # beside R_THIN_OUTCOME, never instead of it.
        "CORROBORATION_UNAVAILABLE",
        "CORROBORATION_NOT_CURRENT",
        "CORROBORATION_FUTURE_STAMPED",
        "CORROBORATION_IDENTITY_MISMATCH",
        "CORROBORATION_SAME_SOURCE",
        "CORROBORATION_BELOW_FLOOR",
        "CORROBORATION_READ_BUDGET",
        # NO PROVIDER PRICE AT ALL: the book does not quote this event yet.
        # No catalogue or alias can repair it, which is why it is a
        # probability-stage refusal and never an identity one (map4 D9).
        "NO_PINNACLE_ON_EVENT")),
    ("2_FRESHNESS", (
        "QUOTE_STALE",
        "VENUE_BOOK_STALE",
        "ONE_CLOCK_IS_NOT_MEASURED",
        # Lever A: already past the 30 s rule before any venue read.
        "QUOTE_STALE_ON_ARRIVAL",
        # THE VENUE READ'S OWN FRESHNESS REFUSALS. They used to stop the lane
        # before any row existed, so no row carried them; a calibration-only
        # record now does, and without these the census would attribute it
        # to whichever LATER stage also refused it.
        "VENUE_BOOK_CURRENCY_NOT_ESTABLISHED",
        "VENUE_BOOK_CURRENCY_CONTRADICTED_BY_CONTRACT",
        "OUR_OWN_PROCESSING_DELAY_EXCEEDED_BEFORE_THE_DECISION")),
    ("3_IDENTITY", (
        # THE GLOBAL CATALOGUE COULD NOT NAME ONE MONEYLINE ROW (map4 D9).
        "VENUE_MAPPING_AMBIGUOUS",
        "VENUE_CONTRACT_IS_A_LINE_MARKET_NOT_A_MONEYLINE",
        # THE VENUE'S OWN CATALOGUE, asked by `bettor_venue_native_identity`
        # when the global one had no usable row.
        "NO_VENUE_NATIVE_EVENT_FOR_FIXTURE",
        "VENUE_NATIVE_EVENT_AMBIGUOUS",
        "VENUE_NATIVE_EVENT_MATCHES_ONLY_ONE_TEAM",
        "VENUE_NATIVE_TEAM_ASSIGNMENT_AMBIGUOUS",
        "VENUE_NATIVE_CONTRACT_FOR_THE_PRICED_TEAM_NOT_FOUND",
        "VENUE_NATIVE_CONTRACT_FOR_THE_PRICED_TEAM_AMBIGUOUS",
        "VENUE_NATIVE_CONTRACT_SIDES_NOT_ESTABLISHED",
        "VENUE_NATIVE_FAMILY_NOT_SUPPORTED",
        "VENUE_NATIVE_PROVIDER_EVENT_NOT_MATCHABLE",
        "VENUE_NATIVE_CATALOGUE_READ_FAILED",
        "VENUE_NATIVE_CANDIDATE_READ_TRUNCATED",
        "VENUE_NATIVE_COMPETITION_NOT_ESTABLISHED",
        "VENUE_NATIVE_MATCH_RAISED",
        "VENUE_DOES_NOT_LIST_THIS_FIXTURE",
        "NO_PREMAP_CONTRACT_FOR_THIS_FIXTURE",
        "PAYOUT_OUTCOME_INDEX_NOT_BOUND_TO_A_TOKEN",
        "PAYOUT_OUTCOME_DISAGREES_WITH_THE_VENUE_INTENT",
        # WHICH PERIOD THE CONTRACT PAYS ON IS PART OF ITS IDENTITY. A
        # full-match probability priced against an inning-six payout is
        # the wrong contract, not a stale one.
        "VENUE_CONTRACT_PERIOD_NOT_ESTABLISHED",
        # THE THREE SPECIFIC WAYS IT IS NOT ESTABLISHED, each with its own
        # remedy: an unsupported market family, a slug carrying something
        # between its event and its side, and a field of entrants rather
        # than two participants.
        "VENUE_CONTRACT_KIND_IS_NOT_A_CONFIRMED_MONEYLINE",
        "VENUE_SLUG_DOES_NOT_DECOMPOSE_INTO_EVENT_AND_SIDE",
        "VENUE_EVENT_IS_NOT_A_TWO_PARTICIPANT_MATCH",
        "VENUE_EVENT_TITLE_NOT_CAPTURED",
        "VENUE_MARKET_TYPE_METADATA_NOT_RETAINED",
        "VENUE_MARKET_TYPE_IS_UNSPECIFIED",
        "VENUE_MARKET_TYPE_IS_NOT_A_WINNER_STRUCTURE",
        "VENUE_MARKET_SCOPE_NOT_ESTABLISHED",
        "VENUE_MARKET_SCOPE_IS_A_SEGMENT",
        "VENUE_MARKET_SCOPE_CONFLICTS_WITH_THE_IDENTIFIER")),
    ("4_SETTLEMENT_SCOPE", (
        "VOID_ABANDONMENT_RULE_NOT_ESTABLISHED",
        "OVERTIME_RULE_NOT_ESTABLISHED",
        "SETTLEMENT_TERMS_CONFLICT",
        "SETTLEMENT_SCOPE_NOT_ESTABLISHED",
        "UNRESOLVED_SETTLEMENT_SEMANTICS")),
    ("5_EXECUTION_ESTIMATE", (
        "NO_OBSERVED_DEPTH_INSIDE_THE_BREAK_EVEN_LIMIT",
        "EXECUTION_ESTIMATE_NOT_IDENTIFIED",
        "VENUE_BOOK_NOT_READ",
        "P_FILL_NOT_IDENTIFIED")),
    ("6_SIZING", (
        "SIZING_POLICY_NOT_APPLICABLE",
        # SIZING NOW ASKS THE RAILS FOR HEADROOM BEFORE IT SPENDS, so a
        # book with no room left refuses HERE rather than proposing a
        # position that 7_RISK then rejects. The distinction matters to
        # the census: "the book is full" is a portfolio state, while
        # RISK_GATE_BLOCKED at stage 7 is a gate on the candidate.
        "NO_RAIL_HEADROOM_FOR_ANY_POSITION",
        "RAIL_HEADROOM_NOT_MEASURED",
        "UNFILLED_NOTIONAL",)),
    ("7_RISK", (
        "RISK_GATE_BLOCKED",
        "ACTION_EXPOSURE_EFFECT_NOT_IDENTIFIED",
        "OPEN_SHADOW_BOOK_NOT_READ")),
    ("8_ECONOMICS", (
        "NO_ACTION_HAS_POSITIVE_NET_EDGE",)),
)

STAGE_OF = {code: name for name, codes in STAGES for code in codes}
STAGE_ORDER = [name for name, _ in STAGES]
STAGE_UNCLASSIFIED = "9_UNCLASSIFIED_REFUSAL"


# ── IS THIS A DECISION OR AN INABILITY? ─────────────────────────────
#
# THE QUESTION THIS ANSWERS. A cycle that ends NO_TRADE can mean two
# completely different things, and the census could not tell them apart:
#
#   * the engine EVALUATED the candidates and declined them -- no positive
#     edge, a rail with no headroom, a stated payout conflict, a quote whose
#     age was measured and too old. That is a working engine reporting that
#     there was nothing to do.
#
#   * the engine COULD NOT EVALUATE them -- a book it never read, a fair
#     value it could not establish, a contract whose period was never
#     bound, a clock that was not measured. That is not an absence of
#     opportunity. It is an absence of an answer, and reporting it as
#     "no opportunity" is the single most misleading thing this lane could
#     do.
#
# So every refusal is classified, and a cycle carries a verdict built from
# the classification rather than from the trade count. An unclassified code
# is reported BY NAME as UNCLASSIFIED -- never folded into either bucket,
# because a drifted table must not quietly become "the engine was fine".

#: The engine reached a judgement on evidence it had. NO_TRADE here is an
#: answer.
DECIDED = "DECIDED_ON_THE_EVIDENCE"
#: The engine lacked an input it needs. NO_TRADE here is not an answer.
COULD_NOT_EVALUATE = "COULD_NOT_EVALUATE"
#: A subclass of COULD_NOT_EVALUATE: the missing input is SOMEONE ELSE'S --
#: the venue or the provider refused, errored or does not carry it. Separated
#: because the remedy is a conversation with them, not a code change here.
EXTERNAL_DEPENDENCY = "EXTERNAL_DEPENDENCY"
EVALUABILITY_UNCLASSIFIED = "UNCLASSIFIED_REFUSAL"

#: THE CLASSIFICATION, by refusal code. Each entry is a judgement about
#: whether the lane HAD what it needed, and the comment says why.
EVALUABILITY_OF = {
    # ── DECIDED: evidence was present and the answer was no ──────────
    "NO_ACTION_HAS_POSITIVE_NET_EDGE": DECIDED,
    "RISK_GATE_BLOCKED": DECIDED,
    "NO_RAIL_HEADROOM_FOR_ANY_POSITION": DECIDED,
    "NO_OBSERVED_DEPTH_INSIDE_THE_BREAK_EVEN_LIMIT": DECIDED,
    "UNFILLED_NOTIONAL": DECIDED,
    # A MEASURED AGE PAST THE LIMIT IS A DECISION. The clock was read and
    # the price was too old; nothing was missing.
    "QUOTE_STALE": DECIDED,
    "VENUE_BOOK_STALE": DECIDED,
    "VENUE_QUOTE_STALE": DECIDED,
    # A STATED PAYOUT CONFLICT IS A DECISION, and the most important one in
    # this table: both sides published a rule and the payouts differ. It is
    # preserved as a refusal on purpose and must never be relaxed into a
    # capability gap to make the funnel look better.
    "SETTLEMENT_TERMS_CONFLICT": DECIDED,
    "VOID_ABANDONMENT_RULE_CONFLICTS_WITH_BOOK_RULE": DECIDED,
    "OVERTIME_RULE_CONFLICTS_WITH_BOOK_RULE": DECIDED,
    # A SEGMENT SCOPE IS A DECISION: the contract pays on part of the
    # fixture, and this lane prices whole fixtures.
    "VENUE_MARKET_SCOPE_IS_A_SEGMENT": DECIDED,
    "PAYOUT_OUTCOME_DISAGREES_WITH_THE_VENUE_INTENT": DECIDED,
    # THE RESPONSE'S OWN HEADERS PUT IT PAST THE BOUND, and our own delay was
    # MEASURED past its bound: both are judgements on evidence that was read.
    "VENUE_BOOK_CURRENCY_CONTRADICTED_BY_CONTRACT": DECIDED,
    "OUR_OWN_PROCESSING_DELAY_EXCEEDED_BEFORE_THE_DECISION": DECIDED,

    # ── COULD NOT EVALUATE: an input we need was not established ──────
    # A CALIBRATION-ONLY RECORD WAS NEVER EVALUATED AS AN ENTRY: its venue
    # price was displayed, not executable, so there was no size, estimate or
    # risk verdict to judge. The venue refusal beside it says why.
    "CALIBRATION_ONLY_RECORD_IS_NOT_AN_ENTRY_CANDIDATE": COULD_NOT_EVALUATE,
    "INDEPENDENT_FAIR_VALUE_NOT_ESTABLISHED": COULD_NOT_EVALUATE,
    "NO_QUALIFIED_MODEL": COULD_NOT_EVALUATE,
    "THIN_OUTCOME_COVERAGE": COULD_NOT_EVALUATE,
    "ONE_CLOCK_IS_NOT_MEASURED": COULD_NOT_EVALUATE,
    "PAYOUT_OUTCOME_INDEX_NOT_BOUND_TO_A_TOKEN": COULD_NOT_EVALUATE,
    "VENUE_CONTRACT_PERIOD_NOT_ESTABLISHED": COULD_NOT_EVALUATE,
    "VENUE_CONTRACT_KIND_IS_NOT_A_CONFIRMED_MONEYLINE": COULD_NOT_EVALUATE,
    "VENUE_SLUG_DOES_NOT_DECOMPOSE_INTO_EVENT_AND_SIDE": COULD_NOT_EVALUATE,
    "VENUE_EVENT_IS_NOT_A_TWO_PARTICIPANT_MATCH": COULD_NOT_EVALUATE,
    "VENUE_EVENT_TITLE_NOT_CAPTURED": COULD_NOT_EVALUATE,
    "VENUE_MARKET_TYPE_METADATA_NOT_RETAINED": COULD_NOT_EVALUATE,
    "VENUE_MARKET_TYPE_IS_UNSPECIFIED": COULD_NOT_EVALUATE,
    "VENUE_MARKET_TYPE_IS_NOT_A_WINNER_STRUCTURE": COULD_NOT_EVALUATE,
    "VENUE_MARKET_SCOPE_NOT_ESTABLISHED": COULD_NOT_EVALUATE,
    "VENUE_MARKET_SCOPE_CONFLICTS_WITH_THE_IDENTIFIER": COULD_NOT_EVALUATE,
    "VOID_ABANDONMENT_RULE_NOT_ESTABLISHED": COULD_NOT_EVALUATE,
    "OVERTIME_RULE_NOT_ESTABLISHED": COULD_NOT_EVALUATE,
    # WE DO NOT HOLD THE BOOK'S OWN RULE. The venue published terms; the
    # BOOKMAKER's side is silent or was never captured, so
    # `bettor_venue_settlement` returns established=False with
    # evidence_class=EV_NONE. That is OUR gap -- more capture could close it
    # -- and it is emphatically NOT the same as the two sides stating
    # payouts that differ, which is a DECISION on real evidence. The live
    # census of 2026-09-26 22:11 carried 1 of these alongside 22 genuine
    # conflicts, and the classifier correctly refused to guess which it was.
    "VOID_ABANDONMENT_BOOK_RULE_NOT_HELD": COULD_NOT_EVALUATE,
    # The remaining settlement refusals, for the same reason: each names a
    # comparison that could not be MADE, not one that came out against us.
    "VENUE_SETTLEMENT_RULE_NOT_ESTABLISHED": COULD_NOT_EVALUATE,
    "DRAW_HANDLING_NOT_RECONCILED": COULD_NOT_EVALUATE,
    "SETTLEMENT_SCOPE_NOT_ESTABLISHED": COULD_NOT_EVALUATE,
    "UNRESOLVED_SETTLEMENT_SEMANTICS": COULD_NOT_EVALUATE,
    "EXECUTION_ESTIMATE_NOT_IDENTIFIED": COULD_NOT_EVALUATE,
    "P_FILL_NOT_IDENTIFIED": COULD_NOT_EVALUATE,
    "RAIL_HEADROOM_NOT_MEASURED": COULD_NOT_EVALUATE,
    "ACTION_EXPOSURE_EFFECT_NOT_IDENTIFIED": COULD_NOT_EVALUATE,
    "OPEN_SHADOW_BOOK_NOT_READ": COULD_NOT_EVALUATE,
    "SIZING_POLICY_NOT_APPLICABLE": COULD_NOT_EVALUATE,

    # A MEASURED AGE PAST THE LIMIT, measured before the venue read instead
    # of after it. The same decision as QUOTE_STALE, taken earlier.
    "QUOTE_STALE_ON_ARRIVAL": DECIDED,
    # THE GLOBAL MATCH FOUND TWO ROWS AND COULD NOT CHOOSE. Ours: the match
    # ignores dates, which is why the venue-native path exists.
    "VENUE_MAPPING_AMBIGUOUS": COULD_NOT_EVALUATE,
    # THE VENUE-NATIVE MATCHER'S OWN INABILITIES. Each names a comparison it
    # could not make safely -- two candidates, a swappable assignment, one
    # team named, a contract whose sides are not shown -- not a verdict on
    # the fixture.
    "VENUE_NATIVE_EVENT_AMBIGUOUS": COULD_NOT_EVALUATE,
    "VENUE_NATIVE_EVENT_MATCHES_ONLY_ONE_TEAM": COULD_NOT_EVALUATE,
    "VENUE_NATIVE_TEAM_ASSIGNMENT_AMBIGUOUS": COULD_NOT_EVALUATE,
    "VENUE_NATIVE_CONTRACT_FOR_THE_PRICED_TEAM_AMBIGUOUS": COULD_NOT_EVALUATE,
    "VENUE_NATIVE_CONTRACT_SIDES_NOT_ESTABLISHED": COULD_NOT_EVALUATE,
    "VENUE_NATIVE_FAMILY_NOT_SUPPORTED": COULD_NOT_EVALUATE,
    "VENUE_NATIVE_CATALOGUE_READ_FAILED": COULD_NOT_EVALUATE,
    "VENUE_NATIVE_CANDIDATE_READ_TRUNCATED": COULD_NOT_EVALUATE,
    "VENUE_NATIVE_COMPETITION_NOT_ESTABLISHED": COULD_NOT_EVALUATE,
    "VENUE_NATIVE_MATCH_RAISED": COULD_NOT_EVALUATE,

    # ── EXTERNAL DEPENDENCY: the missing input is theirs ──────────────
    # THE PROVIDER DOES NOT QUOTE IT (yet). Measured 2026-09-29: UNL fixtures
    # 42-117 h out and Serie B at ~95 h simply carry no Pinnacle h2h.
    "NO_PINNACLE_ON_EVENT": EXTERNAL_DEPENDENCY,
    # The catalogue lists only line markets for this fixture: no moneyline
    # exists there to price, the same kind of absence as no contract at all.
    "VENUE_CONTRACT_IS_A_LINE_MARKET_NOT_A_MONEYLINE": EXTERNAL_DEPENDENCY,
    # The venue's own catalogue does not list the fixture, or lists no
    # contract for the priced team on it, or the provider's own event cannot
    # be matched (no two distinct teams, no readable start).
    "NO_VENUE_NATIVE_EVENT_FOR_FIXTURE": EXTERNAL_DEPENDENCY,
    "VENUE_NATIVE_CONTRACT_FOR_THE_PRICED_TEAM_NOT_FOUND": EXTERNAL_DEPENDENCY,
    "VENUE_NATIVE_PROVIDER_EVENT_NOT_MATCHABLE": EXTERNAL_DEPENDENCY,
    "VENUE_BOOK_NOT_READ": EXTERNAL_DEPENDENCY,
    "VENUE_BOOK_READ_FAILED": EXTERNAL_DEPENDENCY,
    "VENUE_BOOK_READ_RETURNED_ERROR": EXTERNAL_DEPENDENCY,
    "VENUE_DOES_NOT_LIST_THIS_FIXTURE": EXTERNAL_DEPENDENCY,
    "NO_VENUE_CONTRACT_FOR_EVENT": EXTERNAL_DEPENDENCY,
    "NO_VENUE_NATIVE_CONTRACT_IN_PREMAP": EXTERNAL_DEPENDENCY,
    "NO_PREMAP_CONTRACT_FOR_THIS_FIXTURE": EXTERNAL_DEPENDENCY,
    # THE VENUE DOES NOT DOCUMENT ITS MARKET-DATA TIMING (bettor_stream_currency
    # P5), so no mechanism can establish that a book is current. Missing
    # evidence that only the venue can supply.
    "VENUE_BOOK_CURRENCY_NOT_ESTABLISHED": EXTERNAL_DEPENDENCY,
}


def evaluability(refusals) -> str | None:
    """Did the lane DECIDE, or could it not evaluate? None when nothing
    refused.

    THE WORST CASE WINS. A candidate that both could not be valued and had
    no edge is reported as COULD_NOT_EVALUATE, because the edge was computed
    from an input that was not established. Order of severity:
    EXTERNAL_DEPENDENCY, then COULD_NOT_EVALUATE, then DECIDED.
    """
    codes = [str(c) for c in (refusals or [])]
    if not codes:
        return None
    seen = {EVALUABILITY_OF.get(c, EVALUABILITY_UNCLASSIFIED) for c in codes}
    for worst in (EVALUABILITY_UNCLASSIFIED, EXTERNAL_DEPENDENCY,
                  COULD_NOT_EVALUATE, DECIDED):
        if worst in seen:
            return worst
    return None


#: What a cycle is allowed to conclude, and when.
V_NO_OPPORTUNITY = "NO_OPPORTUNITY_ON_A_FUNCTIONING_EVALUATION"
V_EVALUATION_INCOMPLETE = "EVALUATION_INCOMPLETE"
V_BLOCKED_EXTERNALLY = "BLOCKED_ON_AN_EXTERNAL_DEPENDENCY"
V_ADMITTED = "AT_LEAST_ONE_CANDIDATE_WAS_ADMITTED"
V_NOTHING_REACHED_EVALUATION = "NOTHING_REACHED_EVALUATION"
V_NO_OPPORTUNITY_WITH_GAPS = ("NO_OPPORTUNITY_AMONG_THOSE_EVALUATED_"
                              "WITH_SOME_NOT_EVALUABLE")
V_CLASSIFICATION_HAS_DRIFTED = "REFUSAL_CLASSIFICATION_HAS_DRIFTED"


def cycle_evaluability(per_candidate) -> dict:
    """THE CYCLE'S OWN VERDICT, from the classification and not the count.

    `per_candidate` is an iterable of refusal lists (one per candidate that
    reached the mapping). The verdict distinguishes an engine that worked
    and found nothing from an engine that could not answer -- which is the
    difference between "wait for a better market" and "fix something".
    """
    rows = [list(r or []) for r in (per_candidate or [])]
    counts = {DECIDED: 0, COULD_NOT_EVALUATE: 0, EXTERNAL_DEPENDENCY: 0,
              EVALUABILITY_UNCLASSIFIED: 0}
    admitted = 0
    for r in rows:
        k = evaluability(r)
        if k is None:
            admitted += 1
        else:
            counts[k] += 1
    evaluated = counts[DECIDED] + admitted
    out = {"candidates": len(rows), "admitted": admitted, "counts": counts,
           "evaluated_to_a_judgement": evaluated,
           "could_not_be_evaluated": (counts[COULD_NOT_EVALUATE]
                                      + counts[EXTERNAL_DEPENDENCY]
                                      + counts[EVALUABILITY_UNCLASSIFIED])}
    if not rows:
        return dict(out, verdict=V_NOTHING_REACHED_EVALUATION,
                    why=("no candidate reached the mapping, so the engine "
                         "was not exercised at all this cycle"))
    gaps = out["could_not_be_evaluated"]
    unknown_codes = sorted({str(c) for r in rows for c in r
                            if str(c) not in EVALUABILITY_OF})
    out["unclassified_codes"] = unknown_codes
    if unknown_codes:
        # A DRIFTED TABLE IS ITS OWN FINDING. Folding an unknown code into
        # "external" would let a new refusal quietly become somebody else's
        # fault, and folding it into "decided" would let it become "no
        # opportunity". Neither is knowable, so it is named.
        return dict(out, verdict=V_CLASSIFICATION_HAS_DRIFTED,
                    why=("%d refusal code(s) are not in this lane's "
                         "evaluability table (%s), so whether the engine "
                         "decided or could not evaluate is UNKNOWN for them"
                         % (len(unknown_codes), ", ".join(unknown_codes))))
    if admitted:
        return dict(out, verdict=V_ADMITTED)
    if evaluated and not gaps:
        return dict(out, verdict=V_NO_OPPORTUNITY,
                    why=("every candidate was evaluated on evidence the "
                         "lane had, and each was declined. This is a "
                         "working engine reporting no opportunity"))
    if evaluated and gaps:
        # PROPORTIONATE, NOT EITHER EXTREME. Saying "blocked" when 45 of 53
        # candidates were judged is as wrong as saying "no opportunity" when
        # 8 were never evaluated. Both counts are in the verdict.
        return dict(out, verdict=V_NO_OPPORTUNITY_WITH_GAPS,
                    why=("%d candidate(s) were evaluated on evidence the "
                         "lane had and declined -- that part of the engine "
                         "worked -- and %d could not be evaluated at all "
                         "(%d of those on an external dependency). The "
                         "second group says NOTHING about opportunity"
                         % (evaluated, gaps, counts[EXTERNAL_DEPENDENCY])))
    if counts[EXTERNAL_DEPENDENCY] >= counts[COULD_NOT_EVALUATE]:
        return dict(out, verdict=V_BLOCKED_EXTERNALLY,
                    why=("NOTHING was evaluated, and the largest group "
                         "failed because the venue or the provider did not "
                         "supply an input. NO_TRADE here is not a statement "
                         "about opportunity at all"))
    return dict(out, verdict=V_EVALUATION_INCOMPLETE,
                why=("nothing reached a judgement: %d candidate(s) could "
                     "not be evaluated. A cycle in this state must NOT be "
                     "read as 'no opportunity'" % gaps))


def first_stage(refusals) -> str | None:
    """The EARLIEST pipeline stage any of these refusals belongs to.

    None when the list is empty. `9_UNCLASSIFIED_REFUSAL` when refusals
    exist but none is a code this map knows -- reported by name rather
    than silently folded into a stage, because an unmapped code means this
    table has drifted from the lane.
    """
    codes = [str(c) for c in (refusals or [])]
    if not codes:
        return None
    hits = [STAGE_OF[c] for c in codes if c in STAGE_OF]
    if not hits:
        return STAGE_UNCLASSIFIED
    return min(hits, key=lambda n: STAGE_ORDER.index(n))


#: WHAT THE SETTLEMENT COMPARISON ACTUALLY RETURNED, COUNTED.
#:
#: WHY A COUNT AND NOT A SAMPLE. The per-candidate evidence read returns 25
#: rows; the question "does ANY supported market have a compatible payoff"
#: is about the whole window. Silence (UNKNOWN) and a stated conflict
#: (INCOMPATIBLE) are different answers with different remedies -- more
#: reading fixes one and nothing fixes the other -- so they are counted
#: apart, together with which conditions carried the conflict.
#: ONE ROW PER VERDICT. No join, no unnest -- a row must be counted once.
#: (The first version of this unnested `mismatched_conditions` in the same
#: query, which counts a row with two mismatched conditions twice. The
#: conditions are asked for separately below.)
SETTLEMENT_COVERAGE = """
    SELECT coalesce(settlement_comparison::jsonb ->> 'compatibility',
                    'NOT_RECORDED') AS verdict,
           sport_family,
           count(*) AS n,
           count(*) FILTER (WHERE admissible) AS admissible,
           count(DISTINCT us_market_slug) AS slugs
      FROM external_valuations
     WHERE experiment_id = $1
       AND decided_at >= now() - ($2 || ' hours')::interval
     GROUP BY 1, 2 ORDER BY 3 DESC
"""

#: WHICH conditions carried a conflict, and on how many rows. Separate, so
#: neither number distorts the other.
SETTLEMENT_MISMATCHES = """
    SELECT m AS condition, count(*) AS rows_carrying_it
      FROM external_valuations ev,
           LATERAL jsonb_array_elements_text(
             coalesce(ev.settlement_comparison::jsonb
                        -> 'mismatched_conditions', '[]'::jsonb)) AS m
     WHERE ev.experiment_id = $1
       AND ev.decided_at >= now() - ($2 || ' hours')::interval
     GROUP BY 1 ORDER BY 2 DESC
"""

#: Per-candidate stage attribution, with the two facts that decide whether
#: "no profitable depth" is even sayable about a row: did the execution
#: estimate SUCCEED, and did a walk actually take levels.
STAGE_CENSUS = """
    SELECT refusals,
           admissible,
           sport_family,
           COALESCE(
             (execution_estimate::jsonb ->> 'ok') = 'true', FALSE)
               AS estimate_ok,
           COALESCE(jsonb_array_length(
             COALESCE(execution_estimate::jsonb -> 'levels_taken',
                      '[]'::jsonb)), 0) AS levels_taken,
           (execution_estimate::jsonb ->> 'vwap')::float8 AS vwap,
           executable_price::float8 AS executable_price,
           probability::float8 AS probability,
           estimated_edge_per_contract::float8 AS edge
      FROM external_valuations
     WHERE experiment_id = $1
       AND decided_at >= now() - ($2 || ' hours')::interval
"""


def stage_report(rows) -> dict:
    """Stage-specific counts, and what each one does and does not support.

    Pure, so the attribution can be checked without a database.
    """
    out = {"candidates": 0, "admissible": 0,
           "by_first_stage": {}, "reached_execution_estimate": 0,
           "walk_took_levels": 0,
           "negative_edge_with_a_walk": 0,
           "negative_edge_without_a_walk": 0,
           # THE COUNT NOBODY PRINTED, so "none was positive" had to be
           # inferred from two negative counts and a total. An opportunity
           # assessment answers "how many candidates showed positive net
           # edge", and that answer has to be a number in its own right --
           # including when it is zero.
           "positive_edge": 0,
           "edge_not_computed": 0,
           "by_sport": {},
           # WHICH EXECUTION THE EDGE ASSUMES. The lane buys by crossing the
           # ask and pays the TAKER fee (`fee_fn(..., maker=False)`), so
           # every edge counted here is a CROSSING edge. A passive/maker
           # strategy has different fill assumptions and a different fee
           # side; none of its numbers are in this census.
           "execution_mode": "CROSSING_THE_ASK_AT_THE_TAKER_FEE",
           "excludes": "ANY_PASSIVE_OR_MAKER_FILL_ASSUMPTION",
           "stage_order": list(STAGE_ORDER)}
    for r in rows or []:
        out["candidates"] += 1
        fam = str(r.get("sport_family") or "UNLABELLED")
        sp = out["by_sport"].setdefault(
            fam, {"candidates": 0, "admissible": 0, "priced": 0,
                  "positive_edge": 0, "negative_or_zero_edge": 0,
                  "by_first_stage": {}})
        sp["candidates"] += 1
        if r.get("admissible"):
            out["admissible"] += 1
            sp["admissible"] += 1
        st = first_stage(r.get("refusals"))
        if st is not None:
            out["by_first_stage"][st] = out["by_first_stage"].get(st, 0) + 1
            sp["by_first_stage"][st] = sp["by_first_stage"].get(st, 0) + 1
        ok = bool(r.get("estimate_ok"))
        took = int(r.get("levels_taken") or 0)
        if ok:
            out["reached_execution_estimate"] += 1
        if took > 0:
            out["walk_took_levels"] += 1
        edge = r.get("edge")
        if edge is None:
            out["edge_not_computed"] += 1
        else:
            sp["priced"] += 1
            if float(edge) > 0:
                out["positive_edge"] += 1
                sp["positive_edge"] += 1
            else:
                sp["negative_or_zero_edge"] += 1
                if took > 0:
                    out["negative_edge_with_a_walk"] += 1
                else:
                    out["negative_edge_without_a_walk"] += 1
    out["what_this_supports"] = {
        "negative_edge_with_a_walk": (
            "a book was walked and the volume-weighted cost of the sized "
            "quantity, plus the per-level fees, exceeded the probability. "
            "This is the only class about which 'no profitable depth' is "
            "sayable"),
        "negative_edge_without_a_walk": (
            "no execution estimate existed, so the gate compared the "
            "probability against the OBSERVED BEST ASK. A real negative "
            "edge at the top of the book -- which does NOT establish "
            "anything about depth further down, because none was read"),
        "refused_before_5_EXECUTION_ESTIMATE": (
            "the walk was never attempted. An empty vwap on these rows is "
            "the absence of a measurement, not a thin book"),
    }
    return out


async def census(conn, *, hours: int = 24) -> dict:
    """What the engine did and, mostly, why it did not.

    THE REFUSAL DISTRIBUTION IS THE DELIVERABLE when nothing clears, so it
    is a first-class read rather than something to be reconstructed from
    rows by hand.
    """
    rows = await conn.fetch(REFUSAL_CENSUS, EXPERIMENT_ID, str(int(hours)))
    summ = await conn.fetchrow(SUMMARY, EXPERIMENT_ID)
    ident = await conn.fetch(IDENTITY_CENSUS, EXPERIMENT_ID)
    out = {
        "experiment_id": EXPERIMENT_ID,
        "window_hours": int(hours),
        "refusals": {r["refusal"]: int(r["n"]) for r in rows},
        # ALL TIME, AND LABELLED AS SUCH. `refusals` and `stages` below are
        # windowed; this is not, and an unlabelled pair of totals on two
        # different denominators is how a report invites the wrong
        # conclusion.
        "summary": (dict(summ) if summ is not None else {}),
        "summary_window": "ALL_TIME_NOT_THE_window_hours_ABOVE",
        # THE TWO IDENTITIES, REPORTED APART. `us_market_slug` is what the
        # venue accepts; `condition_id` is the global catalogue's id. A row
        # carrying only the latter is a valuation of a contract no venue
        # read can reach.
        "contract_identity": [dict(r) for r in ident],
        "identity_note": (
            "VENUE_NATIVE_US_SLUG is us_premap.market_slug, which "
            "pmus.book_read accepts. GLOBAL_CONDITION_ID is the global "
            "catalogue's id, which it does not. BOTH means each was "
            "sourced independently -- never one derived from the other"),
        "credential": credential_present(),
    }
    # ── DOES ANY SUPPORTED MARKET HAVE A COMPARABLE PAYOFF? ──────────
    # Counted over the whole window rather than sampled, because "none"
    # is the answer that matters and a 25-row sample cannot establish it.
    try:
        cov = await conn.fetch(SETTLEMENT_COVERAGE, EXPERIMENT_ID,
                               str(int(hours)))
        mis = await conn.fetch(SETTLEMENT_MISMATCHES, EXPERIMENT_ID,
                               str(int(hours)))
        # AGGREGATED, AND SPLIT BY SPORT. One number over both sports
        # cannot answer "does the OTHER supported sport have a compatible
        # payoff" -- and that was the open question this read was used to
        # answer. The verdict rollup is kept so the old reading still works.
        by_verdict: dict = {}
        for r in cov:
            slot = by_verdict.setdefault(
                r["verdict"], {"rows": 0, "admissible": 0,
                               "distinct_slugs": 0, "by_sport": {}})
            slot["rows"] += int(r["n"])
            slot["admissible"] += int(r["admissible"])
            # DISTINCT PER SPORT, SUMMED. A slug belongs to one sport, so
            # the sum is exact; it is NOT a distinct count across sports in
            # general and is not presented as one.
            slot["distinct_slugs"] += int(r["slugs"])
            slot["by_sport"][str(r["sport_family"])] = {
                "rows": int(r["n"]), "admissible": int(r["admissible"]),
                "distinct_slugs": int(r["slugs"])}
        out["settlement_coverage"] = {
            "by_verdict": by_verdict,
            "mismatched_conditions": {r["condition"]:
                                      int(r["rows_carrying_it"])
                                      for r in mis},
            "compatible_rows": sum(int(r["n"]) for r in cov
                                   if r["verdict"] == "COMPATIBLE"),
            "reading": (
                "COMPATIBLE means every applicable condition is stated by "
                "BOTH sides and pays the same. INCOMPATIBLE means at least "
                "one is stated by both and differs -- more reading cannot "
                "fix that one. UNKNOWN means somebody is silent. A count of "
                "zero COMPATIBLE rows over the window is the measured "
                "answer to whether a tradable payoff exists here"),
        }
    except Exception as exc:                                   # noqa: BLE001
        out["settlement_coverage"] = {"computed": False,
                                      "error": type(exc).__name__}
    try:
        srows = await conn.fetch(STAGE_CENSUS, EXPERIMENT_ID,
                                 str(int(hours)))
        out["stages"] = stage_report([dict(r) for r in srows])
        # THE WINDOW'S OWN TOTALS, so the stage counts have a denominator
        # that belongs to them. `summary` above is all time.
        win = await conn.fetchrow(SUMMARY_IN_WINDOW, EXPERIMENT_ID,
                                  str(int(hours)))
        out["summary_in_window"] = (dict(win) if win is not None else {})
        out["stages"]["reconciles_with_window"] = (
            int((out["summary_in_window"] or {}).get("refused") or 0)
            == sum(out["stages"].get("by_first_stage", {}).values()))
    except Exception as exc:                                   # noqa: BLE001
        # A FAILED ATTRIBUTION IS NOT AN EMPTY ONE. The refusal counts
        # above stand on their own; this says the breakdown could not be
        # computed rather than reporting zeros for every stage.
        out["stages"] = {"computed": False,
                         "error": type(exc).__name__,
                         "why": ("the stage attribution read failed, so no "
                                 "stage-specific count is reported")}
    return out
