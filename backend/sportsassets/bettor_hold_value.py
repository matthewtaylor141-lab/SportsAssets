"""EV_HOLD -- THE VALUE OF DOING NOTHING, OR A NAMED REFUSAL.

Owner directive, "MAKE THE FULL EXIT POLICY OPERATIONAL" §3:

    "For HOLD valuation, use a current, correctly mapped probability
    source when available, including the external Pinnacle source. Label
    its provenance and uncertainty. Fix the probability-event inversion
    identified in 42a68c4 before using it here... Do not turn an unknown
    probability into zero, manufacture passive-fill probabilities, or
    describe a rule as validated EV optimization."

WHY THIS IS THE ONE MISSING NUMBER. Every refusal in the exit stack
traces back to the same absence, and all three modules say so in their
own words:

    bettor_exit_engine   "ranking requires EV_HOLD, the value of doing
                          nothing, which requires an independent fair
                          value. FV_BETTOR_INDEPENDENT is NOT_IDENTIFIED"
    bettor_mgmt_select   "HOLD is NOT comparable -- it needs a settlement
                          model that does not exist"
    bettor_mgmt_router   "EV_VS_HOLD: NOT_IDENTIFIED -- needs an
                          independent fair value"

`bettor_mgmt_select.hold_to_settlement_usd` already prices a hold
EXACTLY -- against an OBSERVED payout. That is arithmetic and it is
scoring, not deciding; using it at decision time would be look-ahead and
the docstring says so. This module is the decision-time counterpart, and
the difference is the whole point: it carries an ESTIMATE with its
provenance and its uncertainty attached, never a fact.

THE SOURCE, AND WHY IT IS ADMISSIBLE NOW AND WAS NOT BEFORE.
`external_valuations` holds de-vigged Pinnacle probabilities. Until
migration 108 a row could not be used for this at all: the payout event
was read off the venue's ORDER INTENT, so a BUY_SHORT match on "Chicago
Cubs" was recorded as paying on NOT(Cubs) and the stored probability
described the wrong event. Migration 108 holds every such row
INELIGIBLE_PAYOUT_IDENTITY_UNVERIFIED and rows written since carry
`payout_event`, `probability_event` and `payout_is_complement`
explicitly. THIS MODULE READS ELIGIBLE ROWS ONLY, and checks the
identity on the row rather than trusting that it was checked upstream.

WHAT IT IS NOT.

  * NOT a validated estimator. Pinnacle's closing line is the best
    single public price this repository has evidence for, and that is a
    borrowed forecast, not one we fitted. P_BETTOR_INDEPENDENT_V3
    measured OUR blend WORSE than the venue price by 0.00926 log loss
    (CI [-0.00222, +0.02036], INCREMENTAL_SIGNAL_STATUS NOT_DETECTED),
    which is exactly why the source is external.
  * NOT an EV optimisation. A rule that acts on this number is a
    DECLARED RULE using a LABELLED EXTERNAL FORECAST. Calling the result
    optimal would claim a validation nobody performed.
  * NOT a licence to default. A missing, stale, ineligible or
    wrongly-mapped probability returns NOT_IDENTIFIED with the reason.
    Zero is a specific claim -- that the position is worthless -- and it
    is the claim most likely to trigger a liquidation.

THE FRESHNESS BOUND IS THE POINT OF THE TWO STAMPS. `observed_at` is the
bookmaker's own instant and `received_at` is when we got it. Ageing
against receipt makes a stale quote look fresh the moment we happen to
fetch it, so the age used here is measured from the OBSERVATION.
"""

from __future__ import annotations

VERSION = "BETTOR_HOLD_VALUE_V1"

NOT_IDENTIFIED = "NOT_IDENTIFIED"

# ── FRESHNESS IS BOUNDED BY THE EVENT'S STATE, NOT BY A COMMENT ──────
#
# THE DEFECT THIS REPLACES, found by independent inspection of deployed
# 5b19bc5. A single `MAX_PROBABILITY_AGE_S = 1800.0` was justified in its
# own comment by PRE-MATCH line stability -- and the function enforced no
# pre-match restriction whatsoever. A 29-minute-old moneyline was
# therefore admissible during play, when a goal can move the true
# probability by tens of points inside a minute. A comment about pre-match
# stability cannot authorise a 30-minute-old probability in play.
#
# THE BOUNDS ARE NOW THE REPOSITORY'S OWN, keyed to a DECLARED event
# state, and the absence of a state is not permission:
#
#   IN_PLAY            120 s -- bettor_progress_feed.MAX_AGE_S, the bound
#                      this stack already applies to every in-play
#                      observation before it may license an exit
#   PRE_MATCH          1800 s, and ONLY when the caller supplies evidence
#                      that the event has not started
#   BREAK / SUSPENDED  120 s. Play is stopped but the market is not: the
#                      next restart reprices it, so this is not pre-match
#   UNKNOWN            120 s. FAIL-CLOSED. An event whose state nobody
#                      established is treated as the strictest case,
#                      because the alternative is the defect above
#   FINAL / ABANDONED  refused outright -- a settled or void event is not
#                      a hold to value from a bookmaker's line
EVENT_IN_PLAY = "IN_PLAY"
EVENT_PRE_MATCH = "PRE_MATCH"
EVENT_BREAK = "BREAK"
EVENT_SUSPENDED = "SUSPENDED"
EVENT_FINAL = "FINAL"
EVENT_ABANDONED = "ABANDONED"
EVENT_UNKNOWN = "UNKNOWN"

#: The strict bound, and the default. Equal to
#: `bettor_progress_feed.MAX_AGE_S` and `bettor_rn1x_policy
#: .PROGRESS_MAX_AGE_S`, deliberately: this is the same question those
#: two already answer for an in-play observation, and a probability is
#: not allowed to be staler than the progress reading beside it.
MAX_PROBABILITY_AGE_S = 120.0

#: Available ONLY on a declared, evidenced PRE_MATCH state.
PRE_MATCH_MAX_AGE_S = 1800.0

AGE_BOUND_BY_STATE = {
    EVENT_IN_PLAY: MAX_PROBABILITY_AGE_S,
    EVENT_BREAK: MAX_PROBABILITY_AGE_S,
    EVENT_SUSPENDED: MAX_PROBABILITY_AGE_S,
    EVENT_UNKNOWN: MAX_PROBABILITY_AGE_S,
    EVENT_PRE_MATCH: PRE_MATCH_MAX_AGE_S,
}

REFUSED_STATES = (EVENT_FINAL, EVENT_ABANDONED)

R_EVENT_SETTLED = "EVENT_IS_FINAL_OR_ABANDONED"

FRESHNESS_CONTRACT = {
    "default_bound_s": MAX_PROBABILITY_AGE_S,
    "pre_match_bound_s": PRE_MATCH_MAX_AGE_S,
    "by_state": dict(AGE_BOUND_BY_STATE),
    "refused_states": list(REFUSED_STATES),
    "unknown_is_strict": (
        "an event whose state nobody established gets the IN_PLAY bound. "
        "Absence of evidence is not evidence of pre-match"),
    "pre_match_needs_evidence": (
        "the longer bound applies only when the caller DECLARES "
        "PRE_MATCH. It is never inferred from a comment, a kickoff "
        "estimate or the quote's own age"),
    "aged_against": "the BOOKMAKER'S observation stamp, never our receipt",
    "rechecked": "on EVERY decision, against that decision's own clock",
}


def bound_for(event_state=None) -> dict:
    """The freshness bound this event state permits, or a refusal."""
    st = str(event_state or EVENT_UNKNOWN).strip().upper() or EVENT_UNKNOWN
    if st in REFUSED_STATES:
        return {"ok": False, "state": st, "refusal": R_EVENT_SETTLED,
                "bound_s": None,
                "why": ("the event is %s. A bookmaker's pre-settlement "
                        "line does not value a hold on a decided or void "
                        "event" % st)}
    if st not in AGE_BOUND_BY_STATE:
        st = EVENT_UNKNOWN
    return {"ok": True, "state": st, "bound_s": AGE_BOUND_BY_STATE[st],
            "is_the_strict_bound": AGE_BOUND_BY_STATE[st] ==
                                   MAX_PROBABILITY_AGE_S,
            "why": ("%s permits %.0f s" % (st, AGE_BOUND_BY_STATE[st]))}


#: A probability this close to certainty is reported with the bound
#: named, because de-vig error is largest at the tails and an EV built on
#: 0.99 is arithmetically dominated by the 0.01.
TAIL_BAND = 0.02

R_NO_SOURCE = "NO_PROBABILITY_SOURCE_ROW"
R_INELIGIBLE = "PROBABILITY_ROW_HELD_INELIGIBLE"
R_STALE = "PROBABILITY_STALE_BEYOND_BOUND"
R_NO_PROBABILITY = "ROW_CARRIES_NO_PROBABILITY"
R_IDENTITY_UNSTATED = "PAYOUT_IDENTITY_NOT_STATED_ON_THE_ROW"
R_PAYOUT_MISMATCH = "ROW_PRICES_A_DIFFERENT_PAYOUT_EVENT"
R_NO_POSITION = "NO_POSITION_SUPPLIED"

PROVENANCE = {
    "class": "EXTERNAL_BOOKMAKER_VALUATION",
    "is_a_model_we_fitted": False,
    "why_external": (
        "our own blend measured WORSE than the venue price -- "
        "P_BETTOR_INDEPENDENT_V3, delta log loss +0.00926, CI "
        "[-0.00222, +0.02036], INCREMENTAL_SIGNAL_STATUS NOT_DETECTED. "
        "A borrowed forecast that is labelled is better evidence than "
        "one of ours that was rejected"),
    "known_uncertainty": (
        "de-vigging assigns the overround across outcomes by a declared "
        "method (power or multiplicative) and which one calibrates "
        "better is an OPEN QUESTION in this repository. No calibration "
        "interval is established for this source on these markets, so "
        "the number carries no error bar -- which is itself the "
        "uncertainty being reported, not an absence of one"),
    "what_would_narrow_it": (
        "a prospective calibration of de-vigged Pinnacle probabilities "
        "against observed settlements on the markets actually traded. "
        "external_valuations.outcome is the column that will support it "
        "and it is populated only after events resolve"),
}

# ── reading the source ───────────────────────────────────────────────
#
# ELIGIBLE ROWS ONLY, and the freshest one per contract. The predicate is
# on the row's own eligibility column so a held row cannot reach a
# decision by being the only one available.

LATEST_PROBABILITY_SQL = """
    SELECT id, experiment_id, version, provider, book, devig_method,
           venue, condition_id, us_market_slug, contract_selection,
           probability, probability_event, payout_event,
           payout_is_complement, buy_intent, matched_side_norm,
           resolver_asked_for, ladder_side,
           executable_price, estimated_edge_per_contract,
           observed_at, received_at, age_s, outcomes_priced,
           expected_outcomes, overround, outcome_books,
           mapped_outcome, mapping_match, decided_at,
           eligibility, ineligible_reason
      FROM external_valuations
     WHERE us_market_slug = $1
       AND eligibility = 'ELIGIBLE'
     ORDER BY observed_at DESC NULLS LAST, id DESC
     LIMIT 1
"""

# The SAME read WITHOUT the eligibility predicate, used only to
# distinguish "no row at all" from "a row exists and is held". Those are
# different facts about the pipeline and collapsing them would hide a
# containment action behind an apparent data gap.
LATEST_ANY_SQL = LATEST_PROBABILITY_SQL.replace(
    "       AND eligibility = 'ELIGIBLE'\n", "")


async def latest_probability(conn, *, us_market_slug) -> dict:
    """The freshest ELIGIBLE probability row for this venue contract."""
    row = await conn.fetchrow(LATEST_PROBABILITY_SQL, us_market_slug)
    if row is not None:
        return {"found": True, "row": dict(row)}
    held = await conn.fetchrow(LATEST_ANY_SQL, us_market_slug)
    if held is None:
        return {"found": False, "refusal": R_NO_SOURCE,
                "why": ("no external valuation row exists for %s. The "
                        "valuation loop has not priced this contract"
                        % us_market_slug)}
    h = dict(held)
    return {"found": False, "refusal": R_INELIGIBLE,
            "held_row_id": h.get("id"),
            "eligibility": h.get("eligibility"),
            "why": ("the only rows for %s are held: %s. A held row is "
                    "never used for a decision, and its existence is "
                    "reported so a containment action is not mistaken "
                    "for a missing feed"
                    % (us_market_slug,
                       h.get("ineligible_reason") or h.get("eligibility")))}


def _epoch(v):
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        pass
    try:
        return v.timestamp()
    except AttributeError:
        return None


def ev_hold(*, qty, basis_per_contract, probability_row=None, now,
            payout_event_held, event_state=None, max_age_s=None) -> dict:
    """The value of holding `qty` to settlement, or why it is unknown.

    `payout_event_held` is the event OUR position pays on, established
    from the requested outcome and the venue side that was matched --
    never from the order intent. The row's own `payout_event` must agree
    with it, and a disagreement is a refusal rather than a silent
    complement.
    """
    # THE BOUND COMES FROM THE EVENT STATE, and an explicit `max_age_s`
    # may only make it STRICTER. A caller cannot widen the window by
    # passing a bigger number: that is how 1800 s reached a live market.
    bnd = bound_for(event_state)
    limit = bnd.get("bound_s")
    if max_age_s is not None and limit is not None:
        limit = min(float(limit), float(max_age_s))
    out = {"version": VERSION, "provenance": PROVENANCE,
           "status": NOT_IDENTIFIED, "ev_hold_usd": None,
           "probability": None, "probability_event": None,
           "payout_event_held": payout_event_held,
           "input_available": False,
           "is_a_deliberate_hold": False,
           "freshness_contract": FRESHNESS_CONTRACT,
           "event_state": bnd.get("state"),
           "age_bound_s": limit,
           "asked_at": float(now)}
    if not bnd["ok"]:
        out.update(refusal=bnd["refusal"], why=bnd["why"])
        return out

    if qty is None or basis_per_contract is None:
        out.update(refusal=R_NO_POSITION,
                   why="no position was supplied to value")
        return out
    q = abs(float(qty))
    basis_usd = float(basis_per_contract) * q
    out.update(qty=q, basis_per_contract=float(basis_per_contract),
               basis_usd=basis_usd)

    if not probability_row:
        out.update(refusal=R_NO_SOURCE,
                   why=("no probability row was supplied. EV_HOLD is "
                        "NOT_IDENTIFIED -- which is NOT zero: zero would "
                        "assert the position is worthless, and that is "
                        "the assertion most likely to trigger an exit"))
        return out

    row = dict(probability_row)
    out["source_row_id"] = row.get("id")
    out["source"] = {k: row.get(k) for k in
                     ("provider", "book", "devig_method", "version",
                      "experiment_id", "us_market_slug", "mapping_match",
                      "mapped_outcome", "overround", "outcomes_priced",
                      "expected_outcomes", "outcome_books")}

    if str(row.get("eligibility") or "ELIGIBLE") != "ELIGIBLE":
        out.update(refusal=R_INELIGIBLE,
                   eligibility=row.get("eligibility"),
                   why=("the row is held: %s"
                        % (row.get("ineligible_reason") or "no reason")))
        return out

    # ── the payout identity, checked on the row ──────────────────────
    row_event = row.get("payout_event")
    prob_event = row.get("probability_event")
    out["identity"] = {
        "row_payout_event": row_event,
        "row_probability_event": prob_event,
        "row_payout_is_complement": row.get("payout_is_complement"),
        "buy_intent": row.get("buy_intent"),
        "matched_side_norm": row.get("matched_side_norm"),
        "resolver_asked_for": row.get("resolver_asked_for"),
        "ladder_side": row.get("ladder_side"),
        "rule": ("the intent selects which ladder supplies acquisition "
                 "cost. The payout event comes from the requested "
                 "outcome and the matched venue side. A probability is "
                 "complemented only when its source event is "
                 "demonstrably the complement of the payout event"),
    }
    if row_event is None or prob_event is None:
        out.update(refusal=R_IDENTITY_UNSTATED,
                   why=("the row does not state which event it prices, "
                        "so it cannot be checked against the event this "
                        "position pays on. Rows written before "
                        "migration 108 are in exactly this condition "
                        "and are held for it"))
        return out
    if str(row_event) != str(payout_event_held):
        out.update(refusal=R_PAYOUT_MISMATCH,
                   why=("the row prices %r and this position pays on "
                        "%r. It is refused rather than complemented: "
                        "inferring the relationship between two event "
                        "names is the defect migration 108 exists for"
                        % (row_event, payout_event_held)))
        return out

    p = row.get("probability")
    if p is None:
        out.update(refusal=R_NO_PROBABILITY,
                   why=("the row carries no probability -- the de-vig "
                        "refused it, most often for an incomplete "
                        "outcome set. NOT_IDENTIFIED, never 0.0"))
        return out
    p = float(p)

    # ── freshness, measured from the BOOKMAKER'S stamp ───────────────
    obs = _epoch(row.get("observed_at"))
    rec = _epoch(row.get("received_at"))
    age_obs = None if obs is None else float(now) - obs
    age_rec = None if rec is None else float(now) - rec
    out["freshness"] = {
        "observed_at": obs, "received_at": rec,
        "age_from_observation_s": age_obs,
        "age_from_receipt_s": age_rec,
        "bound_s": float(limit),
        "bound_from_event_state": bnd.get("state"),
        "bound_is_the_strict_one": bool(bnd.get("is_the_strict_bound")),
        "aged_against": "OBSERVATION",
        "why": ("ageing against receipt makes a stale quote look fresh "
                "the moment we happen to fetch it. The bound is applied "
                "to the bookmaker's own instant"),
        "bound_basis": "DECLARED, not fitted",
    }
    if age_obs is None:
        out.update(refusal=R_STALE,
                   why=("the row carries no observation stamp, so its "
                        "age cannot be established. An unknown age is "
                        "not a fresh one"))
        return out
    if age_obs > float(limit):
        out.update(refusal=R_STALE,
                   why=("the probability was observed %.0f s ago, past "
                        "the %.0f s bound this event state (%s) permits. "
                        "A hold valued on a stale line is a decision "
                        "made about a market that has moved"
                        % (age_obs, float(limit), bnd.get("state"))))
        return out
    if age_obs < 0:
        out.update(refusal=R_STALE,
                   why=("the observation stamp is %.0f s in the FUTURE "
                        "relative to the decision clock, so the two "
                        "clocks disagree and the age is not usable"
                        % (-age_obs,)))
        return out

    # ── the arithmetic, which is the easy part ───────────────────────
    #
    # A contract pays 1.00 on its payout event and 0.00 otherwise, so
    # holding q contracts bought at `basis` is worth p*q - basis*q.
    ev = p * q - basis_usd
    out.update(
        status="IDENTIFIED", input_available=True,
        probability=p, probability_event=prob_event,
        ev_hold_usd=ev,
        payout_if_event_usd=1.0 * q,
        payout_if_not_usd=0.0,
        arithmetic=("%.6f x %.4g - basis %.4f = %.4f"
                    % (p, q, basis_usd, ev)),
        cash_now_usd=0.0,
        cash_at_settlement_usd=p * q,
        cash_note=("HOLD returns NO cash now. Its value arrives at "
                   "settlement and only if the event occurs, which is "
                   "why it is not comparable to a sale on net outcome "
                   "alone"),
        uncertainty={
            "point_estimate_only": True,
            "interval": NOT_IDENTIFIED,
            "why_no_interval": PROVENANCE["known_uncertainty"],
            "in_tail_band": bool(p <= TAIL_BAND or p >= 1.0 - TAIL_BAND),
            "tail_band": TAIL_BAND,
            "tail_note": ("de-vig error is largest at the tails, where "
                          "an EV is dominated by the small side of the "
                          "probability"),
            "devig_method_is_an_open_question": True,
        },
        is_not=("a validated expected value. It is an EXTERNAL "
                "bookmaker's de-vigged price, labelled, used by a "
                "DECLARED RULE. A rule that acts on it is not thereby an "
                "EV optimiser"))
    return out


def deliberate_hold(reason, *, ev=None) -> dict:
    """A HOLD that was CHOSEN, marked as distinct from a missing input.

    §5: "HOLD needs a reason too. A missing input must be
    distinguishable from a deliberate decision to hold." Both end with
    the position unchanged and they are not the same event: one is the
    policy working, the other is the policy blind.
    """
    return {"version": VERSION, "selected": "HOLD",
            "is_a_deliberate_hold": True,
            "input_available": bool(ev and ev.get("input_available")),
            "ev_hold_usd": (ev or {}).get("ev_hold_usd"),
            "reason": reason,
            "distinguished_from": ("HOLD_FOR_MISSING_INPUT, which is the "
                                  "same position with no decision behind "
                                  "it")}


def describe() -> dict:
    return {
        "version": VERSION,
        "supplies": "EV_HOLD, the value the exit engine says ranking needs",
        "source": "external_valuations, ELIGIBLE rows only",
        "provenance": PROVENANCE,
        "freshness_contract": FRESHNESS_CONTRACT,
        "refusals": [R_NO_SOURCE, R_INELIGIBLE, R_STALE, R_NO_PROBABILITY,
                     R_IDENTITY_UNSTATED, R_PAYOUT_MISMATCH, R_NO_POSITION],
        "never": ("defaults an unknown probability to zero, invents a "
                  "fill probability, or describes the rule that consumes "
                  "it as a validated EV optimisation"),
    }
