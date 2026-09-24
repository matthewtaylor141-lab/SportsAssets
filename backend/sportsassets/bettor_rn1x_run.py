"""ONE REAL RN1-SEEDED POSITION, MANAGED END TO END.

THE CHAIN, each step naming the module that does the work. Nothing here
implements a book, an exit engine or an accounting model:

    1 SOURCE      a real `trades` row              OBSERVED_INPUT
    2 CLASSIFY    bettor_entry_kind                OBSERVED_INPUT
    3 SEED        bettor_mgmt_lifecycle.Managed    ASSIGNED
    4 WHETHER     bettor_mgmt_select.exposure_trigger   DECLARED_RULE
    5 HOW         bettor_mgmt_select.rank_priced_actions DECLARED_RULE
    6 ORDER       bettor_desk.Order via Managed.place
    7 FILL        Managed.on_print                 EXECUTION_ASSUMPTION
    8 SETTLE      Managed.settle_at_observed_payout  OBSERVED, SCORING ONLY
    9 COMPARE     hold-to-settlement and RN1's own path

EVERY ACTION NAMES ITS SOURCE. `source_class` is attached to each figure
in the trace, drawn from `bettor_rn1x_policy.SOURCE_CLASS`, so no number
in a published trace is unattributed.

WHAT THE POLICY MAY SEE AT STEP 4 AND 5. Only evidence at or before the
instant being decided. Not the settlement payout -- it enters at step 8
alone. Not RN1's later actions; those are the benchmark, reconstructed
separately at step 9 and never fed to our decision.

HISTORICAL REPLAY AND FORWARD SHADOW ARE LABELLED SEPARATELY. `mode` is
HISTORICAL_REPLAY when the condition has already resolved and the run is
being scored, FORWARD_SHADOW when it has not. A scored historical result
and a live one are never summed.
"""

from __future__ import annotations

from . import bettor_entry_kind as ek
from . import bettor_mgmt_lifecycle as lc
from . import bettor_rn1x_policy as pol

VERSION = "BETTOR_RN1X_RUN_V1"

HISTORICAL = "HISTORICAL_REPLAY"
FORWARD = "FORWARD_SHADOW"

#: How far a fill's own timestamp may run AHEAD of our detection stamp and
#: still be treated as clock skew rather than a broken timestamp. Measured
#: basis: over 7 days the chain lane's worst observed skew on any tracked
#: account is -1.3 s (RN1: 77,712 fills, median -0.6, p95 +0.3). 120 s is
#: two orders of magnitude of headroom over what the feed actually does,
#: and still refuses anything that is not a clock disagreement.
CLOCK_SKEW_TOLERANCE_S = 120.0

#: How the decision instant was obtained. This is recorded per position
#: because the two are not interchangeable evidence.
#:
#:   REPLAY_AT_AVAILABILITY  a counterfactual replay. decision_ts is the
#:                           instant the evidence became available. Valid
#:                           for the HISTORICAL lane, where the whole
#:                           point is to ask what the policy would have
#:                           done; it is NOT a prospective decision.
#:   RUNTIME_WALL_CLOCK      the clock was read at the moment the policy
#:                           actually ran. This is the only basis that
#:                           supports a prospective claim.
BASIS_REPLAY = "REPLAY_AT_AVAILABILITY"
BASIS_RUNTIME = "RUNTIME_WALL_CLOCK"

#: Beyond this, a prospective decision is REFUSED rather than recorded.
#:
#: WHY A LIMIT EXISTS AT ALL. If the cycle that decides runs long after
#: the evidence became available, the order it creates did not exist
#: during the interval in between -- so every print in that window is a
#: print it could not have consumed. Modelling those fills is look-ahead
#: no matter how the timestamps are stored. The order-creation filter
#: below removes them; this limit refuses the position outright once the
#: gap is large enough that what remains is a different experiment from
#: the one being claimed.
MAX_PROSPECTIVE_DECISION_LAG_S = 300.0

R_DECISION_PRECEDES_AVAILABILITY = "DECISION_PRECEDES_AVAILABILITY"
R_PROSPECTIVE_LAG_EXCEEDED = "PROSPECTIVE_DECISION_LAG_EXCEEDED"
R_RUNTIME_CLOCK_NOT_SUPPLIED = "RUNTIME_CLOCK_NOT_SUPPLIED"


def _fee_fn():
    """The production schedule, taker side for our own executions."""
    from . import bettor_fee_schedule as FEES
    from decimal import Decimal

    def fee(qty, price, maker=False):
        return float(FEES.LATEST.fill_fee(Decimal(str(round(float(qty), 6))),
                                          Decimal(str(round(float(price), 6))),
                                          maker=bool(maker)))
    return fee


#: Which decision policy manages the position. The two are NOT variants
#: of one policy and are never summed:
#:
#:   CHAMPION    MANAGEMENT_PAIR_091_STOP_16_V1 through
#:               `Managed.manage_policy`. FROZEN at 2026-09-23. This is
#:               the benchmark and nothing in this change touches it.
#:   CHALLENGER  SHADOW_CHALLENGER_HOLD_RANKED_V1 through
#:               `Managed.decide_challenger`. Ranks every available
#:               action INCLUDING hold, which it can only do because
#:               `bettor_hold_value` supplies EV_HOLD from a labelled
#:               external probability whose payout identity migration
#:               108 made checkable.
CHAMPION = "CHAMPION"
CHALLENGER = "CHALLENGER"


def run(*, rows, payouts=None, resolved_at=None, source_whale_id,
        queue_share=0.25, condition_id=None, fee_fn=None,
        fee_basis="TRANSFERRED_PMUS_LATEST_SCENARIO",
        initial_inventory_verified=False, policy_params=None,
        now=None, decision_basis=BASIS_REPLAY,
        decision_policy=CHAMPION, challenger_inputs=None):
    """`rows` are the condition's fills, ours and others', in any order.

    Each row: id, whale_id, outcome_index, side, size, price, ts,
    detected_at. `payouts` maps outcome_index -> observed payout and is
    used at step 8 ONLY.

    `now` is the caller's wall clock and `decision_basis` says what to do
    with it. A prospective caller passes BASIS_RUNTIME and its own
    `time.time()`; a replay passes neither and gets the availability
    instant, labelled as a replay. The default is the replay basis
    because that is what an unlabelled caller is actually doing.

    `decision_policy` selects the FROZEN benchmark or the CHALLENGER.
    `challenger_inputs` is a callable `(at) -> dict | None` supplying the
    contemporaneous inputs the challenger needs at that instant --
    ev_hold, bid/ask and their depth, the sale ladder, the venue and the
    slug. IT IS A CALLABLE, NOT A DICT, because the inputs are different
    at every instant and a single snapshot reused across a walk would be
    the same market state pretending to be several. Returning None means
    NO INPUTS WERE AVAILABLE at that instant, which the challenger
    records as a missing input rather than as a decision to hold.
    """
    fee = fee_fn or _fee_fn()
    qs = float(queue_share)
    if not 0 <= qs <= 1:
        raise ValueError("queue_share must be between zero and one")
    # A retrospective replay of an unresolved market is not prospective.
    mode = HISTORICAL
    unique = {}
    for row in rows:
        key = int(row["id"])
        if key in unique and unique[key] != row:
            raise ValueError("conflicting records for trade %s" % key)
        unique[key] = row
    rows = sorted(unique.values(), key=lambda r: (float(r["detected_at"]), int(r["id"])))
    mine = [r for r in rows if int(r["whale_id"]) == int(source_whale_id)]

    # WHICH POLICY THIS RUN IS, ON THE RUN. `bettor_rn1x_store` derives
    # `rn1x_positions.policy` -- and through it the position_id -- from
    # `out["policy"]["policy_id"]`. Leaving the champion's description
    # here for a challenger run labelled every challenger row
    # MANAGEMENT_PAIR_091_STOP_16_V1, so the two arms were
    # indistinguishable in the one column that separates them and both
    # arms collided on the same derived position_id. The benchmark's
    # description is kept alongside, because a challenger result is only
    # meaningful next to what it is challenging.
    if decision_policy == CHALLENGER:
        from . import bettor_mgmt_lifecycle as _lc
        from . import bettor_mgmt_select as _sel
        _policy = {
            "policy_id": _sel.CHALLENGER_ID,
            "policy_class": _sel.CHALLENGER_CLASS,
            "lifecycle_version": _lc.CHALLENGER_VERSION,
            "objective": _sel.CHALLENGER_OBJECTIVE,
            "fallback_when_hold_unpriced": _sel.FALLBACK_DECLARATION,
            "hold_value_from": "bettor_hold_value.ev_hold",
            "is_not": ("an EV optimisation. The probability is an "
                       "external bookmaker's de-vigged price with no "
                       "established calibration interval on these "
                       "markets"),
        }
    else:
        _policy = pol.describe()

    out = {"version": VERSION, "mode": mode,
           "policy": _policy,
           "benchmark_policy": pol.describe(),
           "steps": {},
           "condition_id": condition_id, "queue_share": qs,
           "fee_basis": fee_basis, "prospective": False,
           # None is the FROZEN champion. Anything else is a challenger
           # arm and is labelled as one on the run, not only per decision.
           "policy_params": dict(policy_params) if policy_params else None,
           "decision_policy": decision_policy,
           # THE ARM NOW HAS TWO REASONS TO BE A CHALLENGER: a varied
           # PARAMETER on the frozen policy, or a DIFFERENT POLICY
           # entirely. Both are challengers and neither is the champion.
           "arm": (CHAMPION if (decision_policy == CHAMPION
                                and not policy_params) else CHALLENGER)}

    # ── 1 SOURCE ────────────────────────────────────────────────────
    if not mine:
        out["failed_step"] = "SOURCE"
        out["steps"]["SOURCE"] = {"ok": False,
                                  "why": "no fill by the source account"}
        return out
    seed_row = mine[0]
    # THE CHAIN LANE'S DETECTION STAMP PRECEDES THE FILL'S OWN STAMP, and
    # not rarely -- RN1's chain median lag over 7 days is -0.6 s (77,712
    # fills, p95 +0.3, min -1.3). The venue's clock simply runs a little
    # ahead of ours. `rn1x_clocks_ordered` requires
    # detected_ts >= source_ts, so the PROSPECTIVE lane -- which reads the
    # freshest chain rows, exactly where this shows -- died on a
    # CheckViolationError at its first candidate and its cursor correctly
    # refused to advance. Observed 22:56:30Z:
    #   "P": {"moved": false, "stopped": 221561719, "written": 0,
    #         "examined": 400, "refusals": {"ERROR:CheckViolationError": 1}}
    # The historical lane never hit it because backfill rows postdate
    # their fills by hours.
    #
    # THE FIX IS THIS REPOSITORY'S OWN ESTABLISHED CONVENTION, not a new
    # rule: `learn/dataset.available_at` already uses max(ts, detected_at)
    # and cites Run 82's finding about chain-lane differences. Taking the
    # LATER of the two can only ever DELAY when we treat a fill as known,
    # so it cannot grant look-ahead -- which is the one thing the CHECK
    # exists to prevent. Clamping the other way, or relaxing the
    # constraint, would.
    #
    # BOUNDED, because a small skew and a broken timestamp are different
    # facts. Beyond the tolerance the row is refused by name below rather
    # than silently repaired.
    src_ts = float(seed_row["ts"])
    det_raw = float(seed_row["detected_at"])
    skew = src_ts - det_raw
    if skew > CLOCK_SKEW_TOLERANCE_S:
        out["failed_step"] = "SOURCE"
        out["steps"]["SOURCE"] = {
            "ok": False, "refusal": "SOURCE_POSTDATES_DETECTION",
            "source_ts": src_ts, "detected_at": det_raw,
            "skew_s": skew, "tolerance_s": CLOCK_SKEW_TOLERANCE_S,
            "why": ("the fill's own timestamp is %.1f s AFTER we recorded "
                    "seeing it, beyond the %.0f s clock-skew tolerance. A "
                    "second of venue clock skew is one thing; this is a "
                    "broken timestamp, and treating it as observed "
                    "evidence would date the decision before the event"
                    % (skew, CLOCK_SKEW_TOLERANCE_S))}
        return out
    # THE TWO OBSERVED CLOCKS ARE KEPT AS OBSERVED. The previous version
    # wrote `detected_ts = max(src_ts, det_raw)`, which put a DERIVED
    # value in a column named after an observation and threw the real
    # receipt instant away. The conservative availability timestamp is
    # still computed -- it is needed, and it is this repository's own
    # convention (`learn/dataset.available_at`, Run 82) -- but it is now
    # a THIRD value stored separately, so nothing that was measured is
    # overwritten by something that was inferred.
    detected_ts = det_raw
    available_at = max(src_ts, det_raw)
    out["steps"]["SOURCE"] = {
        "ok": True, "source_class": "OBSERVED_INPUT",
        "trade_id": seed_row["id"], "whale_id": seed_row["whale_id"],
        "outcome_index": seed_row["outcome_index"],
        "side": seed_row["side"], "size": float(seed_row["size"]),
        "price": float(seed_row["price"]),
        "source_ts": src_ts,
        "detected_ts": detected_ts,
        "available_at": available_at,
        "clock_skew_s": skew,
        "clocks": {
            "source_ts": "OBSERVED: the venue's own instant for the fill",
            "detected_ts": ("OBSERVED: when our pipeline recorded seeing "
                            "it. Preserved verbatim, including when it "
                            "precedes source_ts, which the chain lane's "
                            "measured -0.6 s median makes ordinary"),
            "available_at": ("DERIVED: max(source_ts, detected_ts). Taking "
                             "the later of two observations can only delay "
                             "when we treat the fill as known, never "
                             "advance it, so it cannot grant look-ahead"),
            "decision_ts": ("OBSERVED at step 3, from the clock the policy "
                            "actually ran on -- never copied from a "
                            "detection stamp to satisfy a constraint")}}

    # ── 2 CLASSIFY, over the account's OWN prior fills only ─────────
    hist = [ek.Fill(source_ts=float(r["ts"]),
                    detected_ts=float(r["detected_at"]),
                    outcome_index=r["outcome_index"], side=r["side"],
                    size=float(r["size"]), price=float(r["price"]),
                    trade_id=int(r["id"]))
            for r in mine]
    if not initial_inventory_verified:
        out["failed_step"] = "CLASSIFY"
        out["steps"]["CLASSIFY"] = {
            "ok": False, "kind": ek.UNKNOWN,
            "why": "initial flat inventory/history completeness not verified"}
        return out
    # Classify only the available seed; future or late-received history
    # cannot retroactively establish inventory at this decision.
    cls = ek.classify_condition(hist[:1])
    first = cls[0]
    out["steps"]["CLASSIFY"] = {"ok": True,
                                "source_class": "OBSERVED_INPUT",
                                **first.to_dict(),
                                "census": ek.census(cls)}
    if first.kind == ek.UNKNOWN:
        out["failed_step"] = "SEED"
        out["steps"]["SEED"] = {
            "ok": False, "unknown_reason": first.unknown_reason,
            "why": ("prior inventory cannot be established, so no "
                    "position is assigned. Seeding here would invent the "
                    "position the classifier just said we cannot see")}
        return out
    if seed_row["side"] != "BUY":
        out["failed_step"] = "SEED"
        out["steps"]["SEED"] = {"ok": False,
                                "why": "the source event is a SELL"}
        return out

    # ── 3 SEED ──────────────────────────────────────────────────────
    #
    # THE DECISION INSTANT IS NOW OBSERVED, NOT ASSIGNED. What stood here
    # was `decision_ts = detected_ts`, with a comment saying it existed so
    # the CHECK would pass. That is a constraint being satisfied by
    # construction, and it backdated every prospective decision to the
    # instant its evidence arrived -- so the traced prospective position
    # showed three identical timestamps and looked like a sub-second
    # round trip that never happened.
    #
    # Two bases, and which one applies is recorded on the row:
    #
    #   RUNTIME_WALL_CLOCK      `now` was read by the caller at the moment
    #                           it ran this policy. The only basis that
    #                           supports a prospective claim.
    #   REPLAY_AT_AVAILABILITY  a counterfactual replay at the instant the
    #                           evidence became available. Legitimate for
    #                           the historical lane and labelled as a
    #                           replay, not as a decision made in time.
    if decision_basis == BASIS_RUNTIME:
        if now is None:
            out["failed_step"] = "SEED"
            out["steps"]["SEED"] = {
                "ok": False, "refusal": R_RUNTIME_CLOCK_NOT_SUPPLIED,
                "why": ("a RUNTIME_WALL_CLOCK basis was asked for but no "
                        "clock was supplied. Falling back to the "
                        "availability instant is exactly the substitution "
                        "this refusal exists to prevent")}
            return out
        decision_ts = float(now)
    else:
        decision_ts = available_at
    decision_lag_s = decision_ts - available_at
    out["decision_basis"] = decision_basis
    out["decision_lag_s"] = decision_lag_s

    if decision_ts < available_at:
        # Only reachable on a RUNTIME basis with a clock behind the feed.
        out["failed_step"] = "SEED"
        out["steps"]["SEED"] = {
            "ok": False, "refusal": R_DECISION_PRECEDES_AVAILABILITY,
            "decision_ts": decision_ts, "available_at": available_at,
            "lag_s": decision_lag_s,
            "why": ("the runtime clock reads BEFORE the instant this "
                    "evidence became available, so the decision would "
                    "predate what it is based on")}
        return out

    if decision_basis == BASIS_RUNTIME and \
            decision_lag_s > MAX_PROSPECTIVE_DECISION_LAG_S:
        out["failed_step"] = "SEED"
        out["steps"]["SEED"] = {
            "ok": False, "refusal": R_PROSPECTIVE_LAG_EXCEEDED,
            "decision_ts": decision_ts, "available_at": available_at,
            "lag_s": decision_lag_s,
            "limit_s": MAX_PROSPECTIVE_DECISION_LAG_S,
            "why": ("the cycle reached this candidate %.0f s after its "
                    "evidence became available, past the %.0f s limit. An "
                    "order created now did not exist during that window, "
                    "so the prints inside it are prints it could not have "
                    "consumed. Recording this as a prospective entry "
                    "would credit the policy with a window it never had"
                    % (decision_lag_s, MAX_PROSPECTIVE_DECISION_LAG_S))}
        return out

    # SETTLED BEFORE WE SAW THE ENTRY -- REFUSED HERE, BEFORE SEEDING.
    #
    # This was a `raise` at step 8 and in production it WEDGED the
    # historical lane: the cursor rightly refuses to advance past a failed
    # row, so one such condition blocked every later one behind it. Trade
    # 295 held the lane at cursor 294 while examining 400 candidates a
    # cycle and writing nothing.
    #
    # It is a REAL data shape, not a corner case. A backfill row's
    # `detected_at` is when we backfilled it, which can easily postdate the
    # market's resolution -- the same pathology I earlier mis-generalised
    # into a feed-wide blocker.
    #
    # AND IT IS REFUSED BEFORE THE SEED, not at settlement. Refusing at
    # step 8 left MANAGE already run and would have persisted a position
    # with no orders and no accounting -- a half-written record, which is
    # the shape that made the desk's book uncertain. If the answer was
    # known before we saw the entry there is no experiment here at all.
    if resolved_at is not None and float(resolved_at) < decision_ts:
        out["failed_step"] = "SEED"
        out["steps"]["SEED"] = {
            "ok": False, "refusal": "SETTLED_BEFORE_DETECTION",
            "resolved_at": float(resolved_at), "decision_ts": decision_ts,
            "gap_hours": round((decision_ts - float(resolved_at)) / 3600.0, 2),
            "why": ("the market's observed resolution precedes the instant "
                    "we detected the seed fill, so there is no window in "
                    "which this policy could have decided. Replaying it "
                    "would be deciding after the answer was known")}
        return out
    m = lc.Managed(condition_id=condition_id or "c",
                   outcome_index=int(seed_row["outcome_index"]),
                   seed_qty=float(seed_row["size"]),
                   seed_price=float(seed_row["price"]),
                   at=decision_ts, fee_fn=fee, queue_share=qs,
                   expiry_s=900.0, account="rn1x-%s" % seed_row["id"])
    out["steps"]["SEED"] = {
        "ok": True, "source_class": "ASSIGNED",
        "entry_kind": first.kind, "qty": m.seed_qty,
        "price": m.seed_price, "basis_usd": m.seed_basis,
        "decision_ts": decision_ts,
        "label": ("ASSIGNED ENTRY at RN1's own fill price. NOT evidence "
                  "that we could have obtained this fill")}

    # ── 4-7, walking forward. Only rows after the decision instant. ──
    #
    # THE ORDER-CREATION FLOOR. An order cannot consume a print that
    # happened before the order existed. Two instants matter and they are
    # not the same:
    #
    #   `at`  = r["detected_at"], when WE received the print. Gates
    #           whether the information had reached us.
    #   ts    = r["ts"], the print's own instant. Gates whether our order
    #           was alive when the execution occurred.
    #
    # The old code checked only the first, against a decision_ts that had
    # been backdated to the availability instant -- so on a delayed cycle
    # every print between the evidence arriving and the cycle running was
    # treated as consumable by an order that did not yet exist. On the
    # RUNTIME basis the floor is the real creation instant, so that window
    # is excluded by the print's own clock as well as by ours.
    order_created_ts = decision_ts
    out["order_created_ts"] = order_created_ts
    events = []
    skipped_pre_creation = 0
    input_misses = 0

    def _decide(at, decision_id):
        """One decision, by whichever policy this run is exercising.

        THE TWO POLICIES SHARE THE ORDER LIFECYCLE AND NOTHING ELSE.
        Both write through the same `Managed`, the same
        `bettor_desk.Order` state machine and the same Portfolio, so a
        difference between their results is a difference in DECISIONS
        and never in accounting.
        """
        nonlocal input_misses
        if decision_policy != CHALLENGER:
            return m.manage_policy(at=at, decision_id=decision_id,
                                   policy_params=policy_params)
        ci = challenger_inputs(at) if challenger_inputs else None
        if not ci:
            input_misses += 1
            ci = {}
        return m.decide_challenger(
            at=at, decision_id=decision_id,
            ev_hold=ci.get("ev_hold"),
            bid=ci.get("bid"), bid_size=ci.get("bid_size"),
            complement_ask=ci.get("complement_ask"),
            complement_ask_size=ci.get("complement_ask_size"),
            sale_ladder=ci.get("sale_ladder"),
            venue=ci.get("venue"),
            us_market_slug=ci.get("us_market_slug"),
            held_is_long=bool(ci.get("held_is_long", True)),
            settlement_semantics=ci.get("settlement_semantics"),
            last_price=ci.get("last_price"),
            seconds_open=(at - decision_ts),
            inputs=ci.get("input_labels") or {})

    _decide(decision_ts, "seed:%s" % seed_row["id"])
    for r in rows:
        at = float(r["detected_at"])
        if at <= decision_ts:
            continue
        if float(r["ts"]) < order_created_ts:
            # Received after we decided, but EXECUTED before our order
            # existed. Late receipt does not create an execution
            # opportunity, and counting it is the look-ahead.
            skipped_pre_creation += 1
            continue
        if resolved_at and at > float(resolved_at):
            continue
        oi = int(r["outcome_index"])
        px = float(r["price"])
        eid = "trade:%s" % r["id"]
        m.expire(at)
        # Only an order predating the source print can consume it. Late
        # receipt does not create a new execution opportunity.
        fills = m.on_print(at=float(r["ts"]), outcome_index=oi, price=px,
                           size=float(r["size"]), evidence_id=eid)
        m.acknowledge_cancels(at)
        # Tape prints are not bids, asks, depth or event progress.
        d = _decide(at, "rn1x-%s" % r["id"])
        if d.get("acted") or fills:
            events.append({"at": at, "evidence_id": eid,
                           "by_source_account":
                               int(r["whale_id"]) == int(source_whale_id),
                           "decision": d, "fills": fills})
    out["steps"]["MANAGE"] = {
        "ok": True, "decisions": len(m.decisions),
        "acted": sum(1 for d in m.decisions if d.get("acted")),
        "events": events,
        "order_created_ts": order_created_ts,
        "decision_policy": decision_policy,
        # DECISIONS TAKEN BLIND, COUNTED SEPARATELY. A challenger cycle
        # that had no contemporaneous inputs did not decide to hold; it
        # could not see. Folding these into the HOLD count would make a
        # blind policy look like a patient one.
        "decisions_without_inputs": input_misses,
        "prints_skipped_before_order_creation": skipped_pre_creation,
        "why_skipped": ("prints we received after deciding but which "
                        "EXECUTED before our order existed. Modelling a "
                        "fill against one of these would be filling "
                        "against the past"),
        "source_class": {"trigger": pol.SOURCE_CLASS["loss_trigger_fraction"],
                         "method": pol.SOURCE_CLASS["pair_target_cost"],
                         "fills": pol.SOURCE_CLASS["our_fill"]}}

    # ── 8 SETTLE, the only place a payout is read ────────────────────
    st_before = m.state()
    settled = None
    if payouts:
        payouts = {int(k): float(v) for k, v in payouts.items()}
        settled = m.settle_at_observed_payout(
            {int(k): float(v) for k, v in payouts.items()},
            float(resolved_at or decision_ts))
    final = m.state()
    out["steps"]["SETTLE"] = {
        "ok": True, "source_class": pol.SOURCE_CLASS["settlement_payout"],
        "settled": settled,
        "note": ("the payout is read HERE and nowhere earlier. Steps 4 "
                 "and 5 never saw it"),
        "before_settlement": {
            "matched_qty": st_before["matched_qty"],
            "residual_qty": st_before["residual_qty"],
            "inventory_cost_usd":
                st_before["portfolio"]["inventory_cost_usd"]}}

    # ── 9 COMPARE, from the IDENTICAL assigned inventory ─────────────
    hold_net = None
    if payouts:
        p = float(payouts.get(int(seed_row["outcome_index"]), 0.0)
                  if isinstance(payouts, dict) else 0.0)
        hold_net = p * m.seed_qty - m.seed_basis
    rn1_after = [r for r in mine if float(r["ts"]) > decision_ts]
    out["steps"]["COMPARE"] = {
        "ok": True,
        "ours_net_usd": final["portfolio"]["realized_pnl_usd"],
        "hold_to_settlement_net_usd": hold_net,
        "rn1_subsequent_actions": len(rn1_after),
        "rn1_benchmark": (
            pol.BENCHMARKS["RN1_MANAGEMENT"] if rn1_after else
            "NOT RECONSTRUCTIBLE: the source account took no further "
            "action on this condition inside the observed window, so "
            "there is no management path of theirs to compare against"),
        "identical_starting_inventory": True}

    out["final_state"] = final
    # EVERY DECISION, NOT JUST THE ONES THAT ACTED. `steps.MANAGE.events`
    # carries only the instants where an order moved or a fill landed,
    # which is the activity-looking subset; a persisted record built from
    # it alone would show the experiment doing things and hide the state
    # it is in most of the time, which for this policy is
    # HOLD_NO_FEASIBLE_PAIR. The store writes this list.
    out["all_decisions"] = list(m.decisions)
    out["accounting"] = {
        "realized_pnl_usd": final["portfolio"]["realized_pnl_usd"],
        "fees_usd": final["portfolio"]["fees_usd"],
        "open_inventory_at_cost_usd":
            final["portfolio"]["inventory_cost_usd"],
        "open_inventory_mark": "NOT_IDENTIFIED",
        "matched_qty": final["matched_qty"],
        "residual_qty": final["residual_qty"],
        "inventory_discrepancy_qty": final["inventory_discrepancy_qty"],
        "invariant": final["invariant"],
        "reconciles": bool(final["invariant"].get("ok")),
    }
    out["does_not_establish"] = list(pol.DOES_NOT_ESTABLISH)
    return out


# ── MANAGING A POSITION THAT IS ALREADY OPEN ──────────────────────────
#
# `run()` above opens a position and walks it once, inside one call. That
# is an ENTRY path, and on the challenger lane it was also the only path:
# the worker skipped any seed already written, so a position was decided
# exactly once and then never looked at again. No refreshed book, no
# re-decision, no later fills, no cancellation follow-through.
#
# `manage_open_position` is the CONTINUING path. It takes the ledger's
# rows, rebuilds the position by replaying them, refreshes the inputs,
# re-decides, consumes any prints that have arrived since, and returns a
# record shaped exactly like `run()`'s so `bettor_rn1x_store.persist_run`
# writes it with no second writer.
#
# THE TIMESTAMPS ARE REAL. `now` is the caller's wall clock at the moment
# this cycle ran; the decision is stamped with it, not with the position's
# original decision instant, and the input snapshot carries its own
# `read_at`. A management cycle that re-used the entry stamp would make
# every later decision look like it happened at entry.

MANAGE_VERSION = "BETTOR_RN1X_MANAGE_V1"


def manage_open_position(*, position, orders=(), fills=(), prints=(),
                         inputs=None, unavailable=None, now, fee_fn=None,
                         queue_share=0.25, decisions_so_far=0):
    """One management cycle on an EXISTING position. Returns a run record.

    `inputs` is the refreshed `challenger_inputs_for` snapshot, or None
    when nothing was available -- in which case the cycle still runs and
    records a missing-input decision, because "we could not see" is a
    fact about this instant and must be stored, not skipped.
    """
    from . import bettor_mgmt_lifecycle as lc

    fee = fee_fn or _fee_fn()
    m = lc.reload_managed(position=position, orders=orders, fills=fills,
                          fee_fn=fee, queue_share=float(queue_share))
    before = m.state()

    # ── prints that landed since the last cycle ─────────────────────
    #
    # ORDER MATTERS AND SO DOES THE FLOOR. A print may only fill an order
    # that already existed when the print EXECUTED, which is the same
    # rule the entry path applies -- late receipt does not create an
    # execution opportunity.
    applied, skipped_pre_creation = [], 0
    for r in sorted(prints or (), key=lambda x: (float(x["ts"]),
                                                 int(x["id"]))):
        at = float(r["ts"])
        oldest = min((o.placed_at for o in m.open_orders()), default=None)
        if oldest is None or at < oldest:
            skipped_pre_creation += 1
            continue
        m.expire(at)
        got = m.on_print(at=at, outcome_index=int(r["outcome_index"]),
                         price=float(r["price"]), size=float(r["size"]),
                         evidence_id="trade:%s" % r["id"])
        m.acknowledge_cancels(at)
        if got:
            applied.append({"at": at, "evidence_id": "trade:%s" % r["id"],
                            "fills": got})

    # ── the decision, at the CALLER'S clock ─────────────────────────
    ci = dict(inputs or {})
    # WHY THE CYCLE WAS BLIND GOES ON THE DECISION ROW. Without this the
    # reason lived only on the run record and a reader of
    # `rn1x_decisions` saw HOLD with no input and no explanation -- which
    # is the distinction §5 asks to be visible.
    labels = dict(ci.get("input_labels") or {})
    if not ci:
        labels["input_availability"] = "NONE_AVAILABLE_AT_THIS_INSTANT"
    if (unavailable or {}).get("reason"):
        labels["input_refusal"] = unavailable["reason"]
        labels["input_refusal_why"] = unavailable.get("why")
    d = m.decide_challenger(
        at=float(now),
        decision_id="mgmt:%s:%d" % (position["position_id"], int(now)),
        ev_hold=ci.get("ev_hold"),
        bid=ci.get("bid"), bid_size=ci.get("bid_size"),
        complement_ask=ci.get("complement_ask"),
        complement_ask_size=ci.get("complement_ask_size"),
        sale_ladder=ci.get("sale_ladder"),
        venue=ci.get("venue"), us_market_slug=ci.get("us_market_slug"),
        held_is_long=bool(ci.get("held_is_long", True)),
        settlement_semantics=ci.get("settlement_semantics"),
        last_price=ci.get("last_price"),
        seconds_open=float(now) - float(position["decision_ts"]),
        inputs=labels)

    final = m.state()
    # SHAPED LIKE A RUN RECORD, so the existing store writes it. The SEED
    # step is marked as REPLAYED rather than re-asserted: this cycle did
    # not open the position and must not claim to have.
    out = {
        "version": MANAGE_VERSION, "mode": FORWARD,
        "cycle": "CONTINUING_MANAGEMENT",
        # THE POSITION'S OWN POLICY, AND IT IS LOAD BEARING.
        # `bettor_rn1x_store.persist_run` derives the position_id from
        # (experiment, policy, trade) via `out["policy"]["policy_id"]`.
        # Omitting it resolved to "UNKNOWN_POLICY", so every continuing
        # cycle wrote its decisions under a SECOND, fabricated position
        # row -- the managed position looked untouched and a duplicate
        # accumulated beside it. Caught on the first two-cycle run.
        "policy": {"policy_id": position.get("policy")
                                or "SHADOW_CHALLENGER_HOLD_RANKED_V1"},
        "decision_policy": CHALLENGER, "arm": CHALLENGER,
        "condition_id": position["condition_id"],
        "queue_share": float(queue_share),
        "decision_basis": BASIS_RUNTIME,
        "order_created_ts": float(now),
        "reloaded": m.reloaded,
        "decisions_so_far": int(decisions_so_far),
        "steps": {
            # THE CLOCKS ARE THE ONES ALREADY STORED, replayed. A
            # continuing cycle observes no new source fill and must not
            # mint a source or detection instant for one; `persist_run`
            # re-asserts the position row ON CONFLICT DO NOTHING, so these
            # are the position's own recorded observations going back in.
            "SOURCE": {"ok": True, "source_class": "OBSERVED_INPUT",
                       "trade_id": position.get("source_trade_id"),
                       "outcome_index": int(position["outcome_index"]),
                       "source_ts": float(position.get("source_ts")
                                          or position["decision_ts"]),
                       "detected_ts": float(position.get("detected_ts")
                                            or position["decision_ts"]),
                       "available_at": float(position.get("available_at")
                                             or position["decision_ts"]),
                       "replayed": True},
            "SEED": {"ok": True, "source_class": "ASSIGNED",
                     "entry_kind": position.get("entry_kind") or "NEW",
                     "replayed_not_reopened": True,
                     "qty": m.seed_qty, "price": m.seed_price,
                     "basis_usd": m.seed_basis,
                     "decision_ts": float(position["decision_ts"])},
            "MANAGE": {
                "ok": True, "decisions": 1,
                "acted": 1 if d.get("acted") else 0,
                "events": applied,
                "prints_applied": len(applied),
                "prints_skipped_before_order_creation":
                    skipped_pre_creation,
                "decision_policy": CHALLENGER,
                "decisions_without_inputs": 0 if inputs else 1,
            },
            "SETTLE": {"ok": True, "settled": None,
                       "note": "no payout is read on a continuing cycle"},
        },
        "all_decisions": [d],
        "final_state": final,
        "inventory_before": {"residual": before["residual_qty"],
                             "held": before["held"]},
        "inventory_after": {"residual": final["residual_qty"],
                            "held": final["held"]},
        "accounting": {
            "realized_pnl_usd": final["portfolio"]["realized_pnl_usd"],
            "fees_usd": final["portfolio"]["fees_usd"],
            "open_inventory_at_cost_usd":
                final["portfolio"]["inventory_cost_usd"],
            "open_inventory_mark": "NOT_IDENTIFIED",
            "matched_qty": final["matched_qty"],
            "residual_qty": final["residual_qty"],
            "inventory_discrepancy_qty":
                final["inventory_discrepancy_qty"],
            "invariant": final["invariant"],
            "reconciles": bool(final["invariant"].get("ok")),
        },
        "input_unavailable": dict(unavailable or {}) or None,
        "input_snapshot": {k: ci.get(k) for k in
                           ("available", "book_available", "reason", "why",
                            "read_at", "valuation_row_id",
                            "us_market_slug", "venue", "payout_event",
                            "bid", "complement_ask",
                            "identity_matched_independently",
                            "price_denomination")},
    }
    return out
