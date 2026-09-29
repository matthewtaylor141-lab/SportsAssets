"""ONE DECISION COMPARISON: the existing EV policy, plus an indirect candidate.

── THE CORRECTION THIS MODULE IS ─────────────────────────────────────

`bettor_funded_indirect_pair.rank_actions` sorted by highest NET WORST CASE and
contained no probability at all. That is a MINIMAX policy, and it is not the
policy this system has. `bettor_mgmt_select` already states why, in the module
that has been deciding for some time:

    "Live, HOLD has no value and DIRECT_EXIT does. Ranking by whatever number is
     available selects the exit every time -- not because exiting is good but
     because it is the only action carrying a figure. That is an engine that
     liquidates the book for want of a settlement model."

A minimax ranking has exactly that shape pointed a different way: a holding with
positive expected value whose worst case is losing the stake loses to an immediate
sale every time, so liquidation wins for the wrong reason. Letting my ranking
stand beside `rank_with_hold` would have been two policies, and the one that ran
would depend on which caller you read.

SO THIS MODULE DOES NOT REPLACE `rank_with_hold`. It CALLS it -- unchanged -- for
HOLD, DIRECT_EXIT, REDUCE and the complement actions, and adds
`ACQUIRE_INDIRECT_HEDGE` as one more candidate scored in the SAME unit: expected
net value in dollars over the position. One comparison, one ranking.

── WORST CASE IS A CONSTRAINT, NOT THE SORT KEY ──────────────────────

The worst case still matters, and it is what `bettor_indirect_structures` and
`bettor_funded_indirect_pair` compute. It enters here as a DOWNSIDE LIMIT: a
candidate whose worst case is worse than the owner-approved limit is moved to
`not_rankable` with a named refusal. It is not down-ranked and it is not
penalised by some weighting -- a breach is a breach. Nothing is selected on the
strength of its downside.

WITH NO APPROVED LIMIT, NOTHING IS FILTERED AND THAT IS SAID. Inventing a limit
would be inventing risk authority, and silently applying none would let an
unbounded candidate rank. The report states which it was.

── THE FIVE QUANTITIES STAY SEPARATE ─────────────────────────────────

expected_net_usd, downside_usd, incremental_capital_usd, capital_duration_h and
evidence_quality are reported per candidate and never combined into one score.
Ferrari's merge economics were +$3.84M against a -$4.44M settled residual; one
blended figure hides both terms. Ranking uses expected_net_usd alone, and the
other four are reported for the reader and used as constraints where the owner has
set one.

── AND THE HOLD GUARD IS INHERITED, NOT WORKED AROUND ────────────────

When HOLD is not priced, `rank_with_hold` selects NOTHING. This module keeps that:
an indirect acquisition is not selected either, because a comparison missing the
value of continuing to hold cannot show that spending new money is better than
doing nothing. Manufacturing a preference by omitting HOLD is the precise failure
the guard exists for.

NOTHING HERE PLACES, SIZES OR FUNDS AN ORDER.
"""

from __future__ import annotations

import math

VERSION = "FUNDED_DECISION_V1"

NOT_ESTABLISHED = "NOT_ESTABLISHED"

ACTION_ACQUIRE_INDIRECT_HEDGE = "ACQUIRE_INDIRECT_HEDGE"

R_HOLD_NOT_PRICED = "HOLD_IS_NOT_PRICED_SO_NOTHING_IS_SELECTED"
R_NO_REGION_PROBABILITIES = "NO_PROBABILITY_OVER_THE_FIXTURES_OUTCOME_REGIONS"
R_INVALID_PROBABILITY = "INVALID_OUTCOME_PROBABILITY"
R_PROBABILITY_PARTITION = "PROBABILITIES_DO_NOT_MATCH_THE_OUTCOME_PARTITION"
R_REQUIRED_RISK_MEASUREMENT = "A_REQUIRED_RISK_MEASUREMENT_IS_UNAVAILABLE"
R_PROBABILITIES_DO_NOT_SUM = "THE_REGION_PROBABILITIES_DO_NOT_SUM_TO_ONE"
R_STRUCTURE_IS_UNESTABLISHABLE = "THE_STRUCTURE_ITSELF_IS_UNESTABLISHABLE"
R_DEPTH_NOT_ESTABLISHED = "THE_BOOKS_DEPTH_AT_THAT_PRICE_IS_NOT_ESTABLISHED"
R_FEES_NOT_PRICED = "THE_FEE_SCHEDULE_WOULD_NOT_PRICE_THIS"
R_DOWNSIDE_LIMIT_BREACHED = "THE_WORST_CASE_BREACHES_THE_APPROVED_DOWNSIDE_LIMIT"
R_INCREMENTAL_CAPITAL_LIMIT_BREACHED = \
    "THE_NEW_CAPITAL_BREACHES_THE_APPROVED_LIMIT"

#: How a probability over the outcome regions was established. Reported per
#: candidate as `evidence_quality`, never folded into the score.
EVIDENCE_EXTERNAL_LABELLED = "EXTERNAL_LABELLED_PROBABILITY"
EVIDENCE_VENUE_IMPLIED = "VENUE_IMPLIED_FROM_ITS_OWN_PRICES"
EVIDENCE_NOT_ESTABLISHED = NOT_ESTABLISHED

#: Applied only between candidates of EQUAL expected net value.
TIE_BREAK = ("at equal expected net value: the higher worst-case net "
             "(`worst_case_net_usd`, else `downside_usd`; unknown sorts last), "
             "then the smaller incremental capital")


def describe() -> dict:
    return {
        "version": VERSION,
        "policy": "EXPECTED NET VALUE, in dollars over the position",
        "ranks_on": "expected_net_usd",
        "worst_case_is": ("a CONSTRAINT -- a downside limit that moves a "
                          "breaching candidate to not_rankable. It is never the "
                          "sort key"),
        "why_not_minimax": (
            "a holding can have positive expected value while its worst case is "
            "losing the stake, so ranking on worst case makes immediate "
            "liquidation win for the wrong reason -- the same failure "
            "bettor_mgmt_select names as liquidating the book for want of a "
            "settlement model, pointed the other way"),
        "existing_policy_is_called_not_replaced":
            "sportsassets.bettor_mgmt_select.rank_with_hold",
        "separate_quantities": ["expected_net_usd", "downside_usd",
                                "incremental_capital_usd",
                                "capital_duration_h", "evidence_quality"],
        "never_blended": (
            "Ferrari's merge economics were +$3.84M against a -$4.44M settled "
            "residual; one figure hides both terms"),
        "hold_guard": (
            "when HOLD is not priced NOTHING is selected, including an indirect "
            "acquisition. A comparison missing the value of holding cannot show "
            "that spending new money beats doing nothing"),
        "this_module_sends_nothing": True,
    }


def _regions_expected_cents(table, probabilities) -> dict:
    """Expected joint payout over the fixture's OWN regions, in cents.

    `probabilities` maps a region label from `bettor_indirect_structures`'
    payoff table to its probability. Regions the caller does not price make the
    expectation UNAVAILABLE -- they are not treated as probability zero, which
    would quietly assume the unpriced outcomes cannot happen and is how an
    expectation becomes an assertion.
    """
    out: dict = {"version": VERSION}
    rows = [dict(r) for r in (table or [])]
    if not rows:
        return dict(out, ok=False, refusal=R_NO_REGION_PROBABILITIES,
                    why="the payoff table is empty")
    try:
        raw_probs = dict(probabilities or {})
        if any(not isinstance(k, str) for k in raw_probs):
            return dict(out, ok=False, refusal=R_PROBABILITY_PARTITION)
        probs = {k: float(v) for k, v in raw_probs.items()}
    except (TypeError, ValueError, OverflowError):
        return dict(out, ok=False, refusal=R_INVALID_PROBABILITY,
                    why="every outcome probability must be a finite number")
    invalid = [k for k, v in probs.items()
               if isinstance(raw_probs[k], bool) or not math.isfinite(v)
               or not 0 <= v <= 1]
    if invalid:
        return dict(out, ok=False, refusal=R_INVALID_PROBABILITY,
                    invalid_regions=invalid,
                    why="probabilities must be finite and between zero and one")
    labels = [str(r.get("region", "")) for r in rows]
    if len(set(labels)) != len(labels) or "" in labels or set(probs) - set(labels):
        return dict(out, ok=False, refusal=R_PROBABILITY_PARTITION,
                    why="probabilities must name exactly one value for each outcome region")
    if not probs:
        return dict(out, ok=False, refusal=R_NO_REGION_PROBABILITIES,
                    why=("an expectation needs a probability over the "
                         "fixture's own outcome regions. Without one there is "
                         "no expected value, and a worst case is not a "
                         "substitute for it"))
    unpriced = [r["region"] for r in rows if r["region"] not in probs]
    if unpriced:
        return dict(out, ok=False, refusal=R_NO_REGION_PROBABILITIES,
                    unpriced_regions=unpriced,
                    why=("%d region(s) carry no probability. Treating them as "
                         "zero would assume those outcomes cannot happen, which "
                         "turns an expectation into an assertion"
                         % len(unpriced)))
    undetermined = [r["region"] for r in rows if not r.get("determined")]
    if undetermined:
        return dict(out, ok=False, refusal=R_STRUCTURE_IS_UNESTABLISHABLE,
                    undetermined_regions=undetermined,
                    why=("a region with no determined payout cannot contribute "
                         "to an expectation"))
    total_p = round(sum(probs[r["region"]] for r in rows), 9)
    if abs(total_p - 1.0) > 1e-6:
        return dict(out, ok=False, refusal=R_PROBABILITIES_DO_NOT_SUM,
                    sums_to=total_p,
                    why=("the regions partition the fixture's outcomes, so "
                         "their probabilities must sum to 1. %s does not, and "
                         "an expectation over a mis-normalised measure is not "
                         "an expectation" % total_p))
    try:
        payouts = [float(r["joint_cents"]) for r in rows]
    except (TypeError, ValueError, KeyError, OverflowError):
        return dict(out, ok=False, refusal=R_STRUCTURE_IS_UNESTABLISHABLE)
    if any(not math.isfinite(v) or v < 0 for v in payouts):
        return dict(out, ok=False, refusal=R_STRUCTURE_IS_UNESTABLISHABLE)
    exp_cents = sum(probs[r["region"]] * payout
                    for r, payout in zip(rows, payouts))
    return dict(out, ok=True, refusal=None,
                expected_joint_cents=round(exp_cents, 6),
                regions=len(rows), probabilities_sum_to=total_p)


def indirect_candidate(*, structure, region_probabilities,
                       evidence_quality: str, fee_usd=None, depth=None,
                       incremental=None, capital_duration_h=None,
                       worst_case=None, position_value=None) -> dict:
    """THE INDIRECT ACQUISITION, AS ONE CANDIDATE IN THE SAME UNIT.

    `value_usd` is EXPECTED net value over the position -- expected joint payout
    minus what the structure costs minus fees -- so it is directly comparable
    with the HOLD candidate `rank_with_hold` produces. The worst case travels
    alongside as `downside_usd` and is used as a constraint, not as the score.

    EVERY REFUSAL IS EXPLICIT AND NONE IS A ZERO. An unpriced fee, an unread
    depth, an unestablished structure and an unpriced region all make this
    candidate NOT RANKABLE with its own reason -- because an alternative scored
    without its fees or its depth is a preference manufactured by omission.
    """
    from . import bettor_funded_indirect_pair as FIP
    from . import bettor_indirect_structures as IS

    d = structure if isinstance(structure, dict) else structure.to_dict()
    out: dict = {"action": ACTION_ACQUIRE_INDIRECT_HEDGE,
                 "taxonomy": d.get("taxonomy"),
                 "units": d.get("units"),
                 "legs": list(d.get("legs") or ()),
                 "classified_by": "bettor_indirect_structures",
                 "evidence_quality": evidence_quality or EVIDENCE_NOT_ESTABLISHED}

    def _no(refusal, why, **extra):
        return dict(out, rankable=False, blocker=refusal, value_usd=None,
                    why=why, **extra)

    if str(d.get("taxonomy")) == IS.UNESTABLISHABLE or d.get("missing_facts") \
            or d.get("undetermined_regions"):
        return _no(R_STRUCTURE_IS_UNESTABLISHABLE,
                   ("the classifier did not establish this structure, so "
                    "neither its expectation nor its floor can be computed"),
                   missing_facts=list(d.get("missing_facts") or ()),
                   undetermined_regions=list(d.get("undetermined_regions") or ()))
    if d.get("cost_cents") is None:
        return _no(R_FEES_NOT_PRICED, "the structure's cost is not stated")
    try:
        fee_valid = fee_usd is not None and not isinstance(fee_usd, bool) and math.isfinite(float(fee_usd))
    except (TypeError, ValueError, OverflowError):
        fee_valid = False
    if not fee_valid:
        return _no(R_FEES_NOT_PRICED,
                   ("an alternative scored without its fee is a preference "
                    "manufactured by omission. At these margins the fee decides "
                    "the sign"))
    if depth is None or not depth.get("ok"):
        return _no(R_DEPTH_NOT_ESTABLISHED,
                   ((depth or {}).get("why")
                    or "the book's depth for the second leg is not established"))
    if not depth.get("fully_supported"):
        return _no(R_DEPTH_NOT_ESTABLISHED,
                   ("the book supplies %s of the %s this acquisition needs. A "
                    "partial hedge is a different position from the one valued"
                    % (depth.get("supportable_qty"), depth.get("wanted_qty"))),
                   shortfall_qty=depth.get("shortfall_qty"))
    if incremental is None or incremental.get("incremental_capital_usd") is None:
        return _no(R_FEES_NOT_PRICED,
                   "the new capital this would consume is not known")

    table = d.get("table") or ()
    if position_value is not None:
        if not position_value.get("ok"):
            return _no(R_STRUCTURE_IS_UNESTABLISHABLE,
                       "the complete position, including uncovered inventory, was not valued")
        table = [{"region": r["region"], "determined": True,
                  "joint_cents": float(r["payout_usd"]) * 100}
                 for r in position_value.get("regions", ())]
        # An unresolved state has no terminal payout. It can be excluded from a
        # terminal expectation only when the supplied measure explicitly gives
        # it zero mass; missing probability is still refused below.
        for region in position_value.get("unresolved_states", ()):
            table.append({"region": region, "determined": (
                (region_probabilities or {}).get(region) == 0), "joint_cents": 0})
    exp = _regions_expected_cents(table, region_probabilities)
    if not exp.get("ok"):
        return _no(exp["refusal"], exp.get("why"),
                   **{k: v for k, v in exp.items()
                      if k in ("unpriced_regions", "sums_to",
                               "undetermined_regions")})

    # ── THE SCALE, REPAIRED BEFORE THE FIRST RANKING EVER RAN ────────
    #
    # `rank_with_hold` scores HOLD, EXIT and REDUCE over the WHOLE position.
    # `bettor_indirect_structures` reports its payouts and costs PER UNIT --
    # `cost_cents` is literally the two legs' `cost_cents_per_unit` added. This
    # function divided those cents by 100 and ranked the result directly against
    # whole-position candidates, so a ten-contract middle worth $1.70 entered the
    # comparison at $0.17: an alternative understated by a factor of its own size,
    # which is exactly the "preference manufactured by omission" this module
    # exists to prevent. A structure whose per-unit figure happened to be larger
    # would have been overstated instead.
    #
    # MEASURED, NOT REASONED ABOUT: found by building the candidate for the
    # Bears/Panthers middle at ten units and reading the two numbers side by side.
    # The fee argument is already whole-position, so the old arithmetic produced a
    # figure at neither scale. Everything below is WHOLE_POSITION and says so.
    units = int(d.get("units") or 0)
    cost_usd_per_unit = round(int(d["cost_cents"]) / 100.0, 6)
    cost_usd = round(cost_usd_per_unit * units, 6)
    payout_per_unit = round(exp["expected_joint_cents"] / 100.0, 6)
    expected_payout = round(payout_per_unit * units, 6)
    if position_value is not None:
        cost_usd = float(position_value["cost_usd"])
        expected_payout = round(exp["expected_joint_cents"] / 100.0, 6)
        payout_per_unit = None
    fees = round(float(fee_usd), 6)
    expected_net = round(expected_payout - cost_usd - fees, 6)
    wc = ((position_value or {}).get("whole_position_usd")
          if position_value is not None else
          (worst_case or {}).get("worst_case_usd"))
    return dict(out, rankable=True, blocker=None,
                scale=FIP.SCALE_WHOLE_POSITION,
                units_valued=units,
                value_usd=expected_net,
                expected_net_usd=expected_net,
                expected_payout_usd=expected_payout,
                expected_payout_usd_per_unit=payout_per_unit,
                cost_usd_per_unit=cost_usd_per_unit,
                cost_usd=cost_usd, fees_usd=fees,
                position_includes_uncovered_inventory=position_value is not None,
                scale_note=("comparable with `rank_with_hold`'s candidates, "
                            "which are scored over the whole position. The "
                            "classifier's cents are per unit and are multiplied "
                            "by the %d matched unit(s) here" % units),
                downside_usd=wc,
                incremental_capital_usd=incremental["incremental_capital_usd"],
                capital_duration_h=capital_duration_h,
                execution_secured=False,
                execution_note=("this candidate REQUIRES A FILL on the second "
                                "leg. Its value is conditional on acquiring it"),
                depth={k: depth.get(k) for k in
                       ("wanted_qty", "available_qty", "fully_supported")},
                basis=("expected joint payout over the fixture's own outcome "
                       "regions, minus cost, minus fees"),
                probability_regions=exp["regions"])


def _limit(limits: dict | None, key: str):
    v = dict(limits or {}).get(key)
    if v is None:
        return None
    if isinstance(v, bool) or not math.isfinite(float(v)) or float(v) < 0:
        raise ValueError("invalid risk limit: " + key)
    return float(v)


def decide(*, hold_ranking: dict, indirect=None, indirect_candidates=None, limits: dict | None = None,
           capital_duration_h=None) -> dict:
    """ONE COMPARISON. `hold_ranking` is a `rank_with_hold` result, UNCHANGED.

    The indirect candidate joins that result's own candidate list and the whole
    set is ranked on expected net value. Then the constraints apply.

    ORDER MATTERS AND IS DELIBERATE: constraints are applied BEFORE the choice,
    so a breaching candidate is never selected and never merely out-ranked. A
    filter applied afterwards would let the best-scoring illegal action be
    reported as the decision with a note.
    """
    out: dict = {"version": VERSION, "policy": "EXPECTED_NET_VALUE",
                 "worst_case_is": "A_CONSTRAINT_NOT_THE_SORT_KEY",
                 "limits_applied": {}, "candidates": [], "not_rankable": []}
    hr = dict(hold_ranking or {})
    out["hold_ranking_version"] = hr.get("version")

    existing = [dict(c) for c in (hr.get("candidates") or [])]
    blocked = [dict(c) for c in (hr.get("not_rankable") or [])]

    # ── THE INHERITED GUARD ──────────────────────────────────────────
    def finite(v):
        try:
            return not isinstance(v, bool) and v is not None and math.isfinite(float(v))
        except (TypeError, ValueError, OverflowError):
            return False

    hold_priced = any(c.get("action") == "HOLD"
                      and finite(c.get("value_usd")) for c in existing)
    out["hold_is_priced"] = hold_priced

    alternatives = list(indirect_candidates or ())
    if indirect is not None:
        alternatives.append(indirect)
    for candidate in alternatives:
        if candidate.get("rankable"):
            existing.append(dict(candidate, qty=candidate.get("qty", candidate.get("units"))))
        else:
            blocked.append(dict(candidate, value_usd=None))

    # ── CONSTRAINTS, APPLIED BEFORE THE CHOICE ───────────────────────
    try:
        downside_limit = _limit(limits, "max_downside_usd")
        capital_limit = _limit(limits, "max_incremental_capital_usd")
    except (TypeError, ValueError, OverflowError):
        return dict(out, selected=None, refusal="INVALID_RISK_LIMIT",
                    selection_reason="a supplied risk limit is not a finite nonnegative amount")
    out["limits_applied"] = {
        "max_downside_usd": downside_limit,
        "max_incremental_capital_usd": capital_limit,
        "no_limit_means": ("NOTHING IS FILTERED on that dimension, and that is "
                           "stated rather than silently applied. Inventing a "
                           "limit would be inventing risk authority"),
    }
    admitted = []
    for c in existing:
        ds = c.get("downside_usd")
        ic = c.get("incremental_capital_usd")
        if c.get("action") == ACTION_ACQUIRE_INDIRECT_HEDGE:
            missing = []
            for key, limit, value in (("downside_usd", downside_limit, ds),
                                      ("incremental_capital_usd", capital_limit, ic)):
                if limit is not None:
                    try:
                        valid = value is not None and math.isfinite(float(value))
                    except (TypeError, ValueError, OverflowError):
                        valid = False
                    if not valid:
                        missing.append(key)
            if missing:
                blocked.append(dict(c, blocker=R_REQUIRED_RISK_MEASUREMENT,
                                    missing=missing, value_usd=None))
                continue
        if downside_limit is not None and ds is not None and float(ds) < -abs(
                downside_limit):
            blocked.append(dict(c, blocker=R_DOWNSIDE_LIMIT_BREACHED,
                                value_usd=None,
                                why=("worst case %s is beyond the approved "
                                     "downside limit of %s. A breach is a "
                                     "breach: this candidate is removed, not "
                                     "out-ranked" % (ds, -abs(downside_limit))
                                     )))
            continue
        if capital_limit is not None and ic is not None and float(ic) > \
                capital_limit:
            blocked.append(dict(c, blocker=R_INCREMENTAL_CAPITAL_LIMIT_BREACHED,
                                value_usd=None,
                                why=("new capital %s is beyond the approved "
                                     "limit of %s" % (ic, capital_limit))))
            continue
        admitted.append(c)

    scored = []
    for c in admitted:
        try:
            finite_value = c.get("value_usd") is not None and math.isfinite(float(c["value_usd"]))
        except (TypeError, ValueError, OverflowError):
            finite_value = False
        if finite_value:
            scored.append(c)
        elif c.get("value_usd") is not None:
            blocked.append(dict(c, value_usd=None, blocker="NON_FINITE_ACTION_VALUE"))
    # ── THE TIE-BREAK, DOCUMENTED: WORST CASE, THEN NEW CAPITAL ──────
    #
    # The ranking is EXPECTED VALUE and stays so. Only among candidates of
    # EQUAL expected value does the worst case decide: the one whose worst
    # outcome loses less goes first (a known worst case beats an unknown one),
    # and then the one committing less new capital. Before this, equal-EV
    # candidates fell back on list order, which is how a digest-less copy of
    # an action came to win over the executable one.
    def _worst(c):
        w = c.get("worst_case_net_usd", c.get("downside_usd"))
        try:
            return float(w) if w is not None and math.isfinite(float(w)) \
                else float("-inf")
        except (TypeError, ValueError, OverflowError):
            return float("-inf")

    scored.sort(key=lambda c: (-float(c["value_usd"]), -_worst(c),
                               float(c.get("incremental_capital_usd") or 0.0)))
    out["tie_break"] = TIE_BREAK
    out["candidates"] = scored
    out["not_rankable"] = blocked
    out["unscored_admitted"] = [c for c in admitted
                                if c.get("value_usd") is None]

    if not hold_priced:
        out.update(selected=None, refusal=R_HOLD_NOT_PRICED,
                   selection_reason=(
                       "HOLD is not priced, so NOTHING is selected -- including "
                       "the indirect acquisition. Ranking the candidates that do "
                       "carry a figure selects an action for want of a "
                       "settlement model, and adding a new-money action to that "
                       "comparison makes it worse, not better"))
        return out
    if not scored:
        out.update(selected=None, refusal="NO_CANDIDATE_IS_RANKABLE",
                   selection_reason=(
                       "every action was refused: %s" % "; ".join(
                           "%s (%s)" % (b.get("action"), b.get("blocker"))
                           for b in blocked) or "no candidates at all"))
        return out

    best = scored[0]
    runner = scored[1] if len(scored) > 1 else None
    out.update(
        selected=best["action"], refusal=None,
        selected_candidate=best,
        margin_over_runner_up=(None if runner is None else
                               round(float(best["value_usd"])
                                     - float(runner["value_usd"]), 6)),
        selection_reason=(
            "%s has the highest EXPECTED net value (%s)%s. The worst case was "
            "applied as a constraint, not as the ranking"
            % (best["action"], best["value_usd"],
               "" if runner is None else
               ", ahead of %s at %s" % (runner["action"], runner["value_usd"]))),
        the_five_quantities={
            k: {q: c.get(q) for q in ("expected_net_usd", "downside_usd",
                                      "incremental_capital_usd",
                                      "capital_duration_h",
                                      "evidence_quality")}
            for k, c in ((c.get("candidate_id") or c["action"], c) for c in scored)},
        what_this_is_not=(
            "an authorisation. A selected ACQUIRE_INDIRECT_HEDGE means the "
            "expected economics are best on these inputs and the constraints "
            "were met -- not that capital may be committed, which needs a "
            "funded grant and the owner's approval"),
    )
    if capital_duration_h is not None:
        out["capital_duration_h"] = capital_duration_h
    return out
