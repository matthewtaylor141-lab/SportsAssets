"""THE FROZEN MANAGEMENT POLICY FOR THE RN1-SEEDED EXPERIMENT.

FROZEN BEFORE THE FORWARD TEST. Every threshold below is fixed here, at
this commit, before any forward evaluation window opens. Changing one
produces a NEW policy id, not a new version of this one, and the old
verdict survives the change.

WHAT THIS POLICY DOES AND DOES NOT DECIDE. It manages a position it was
HANDED. Entry is RN1's; we do not select it, do not claim we could have
obtained RN1's fill, and the assigned basis is labelled as assigned
everywhere it appears.

════════════════════════════════════════════════════════════════════
THE RESEARCH TRACE. Located before this policy was written, and
preserved here so nothing below can be mistaken for new research.

FERRARI_INSPIRED_DEV_V1 is `bettor_desk.Policy`. Its own maturity
declaration reads:

    entry_band        HAND_WRITTEN
    pair_completion   HAND_WRITTEN
    residual_exit     LEARNED
    fill_probability  ASSUMED

  BUY     `Policy.entry_lo/entry_hi` = 0.40-0.65, bettor_desk.py:887.
          HAND_WRITTEN. Deliberately does NOT buy the 0.05-0.40 tail
          Ferrari kept buying, on a measured ~5c fair-value deficit.
          NOT USED HERE: RN1 supplies the entry.

  PAIR    `clears_below = 1.0 - avg - min_clear`, bettor_desk.py:808.
          HAND_WRITTEN. It refuses every completion above par, which
          makes loss-limiting completion structurally impossible. NOT
          USED HERE, and its absence from this path is asserted by test.

  EXIT    `Policy.ev_hold(price)` -> `edge = net_exit - ev_hold`,
          `act = EXIT if edge > exit_edge`, bettor_desk.py:844-848.
          Backed by a LEARNED isotonic curve: PAV with a shared-n
          shrink, feature = the leg's volume-weighted purchase price,
          target = that leg's realised payout in {0, 1}.

          *** ITS PRE-REGISTERED FORECAST TEST WAS NOT SUPPORTED. ***
          On the later 40% of resolved markets the paired log-loss
          difference against the identity was +0.0052, 95% interval
          [-0.0017, +0.0120]. desk_replay.py records the verdict
          verbatim: "It is not established as a better forecast."
          THE CURVE IS NOT ACTIVATED BY THIS POLICY. It is retained as a
          separately labelled RESEARCH CANDIDATE (see CURVE_CANDIDATE)
          and its forecast test is kept distinct from any economic test.

  REDUCE  NOT IMPLEMENTED. `REDUCE` exists as a string constant and
          inside one `alternatives=[...]` list. It is never selected,
          sized or placed. It remained descriptive research and never
          became an executable policy.

WHY THE PRODUCTION EXIT RULE CANNOT FIRE, verified numerically rather
than inferred from an assumption about the fee's sign -- which is a real
hazard here, because `theta_maker` is -0.0125, a REBATE.

    Production builds `DK.Policy()` (bettor_desk_loop.py:624), so
    `curve is None` and `ev_hold(px)` returns px itself, basis
    IDENTITY_PRICE_IS_PROBABILITY. Then

        edge = (px - fee/qty) - px = -fee/qty

    and EXIT needs edge > exit_edge = 0.02, i.e. a REBATE of more than
    2 cents per contract.

    TWO INDEPENDENT REASONS IT NEVER HAPPENS.
    1. The call site hardcodes the TAKER side --
       `self.fee_fn(held["qty"], px, False)` -- and theta_taker is
       +0.0695. Measured: qty 100 at 0.45 gives fee +1.7200, edge
       -0.017200 -> HOLD. Also HOLD at 0.05, 0.95, qty 1 and qty 2000.
    2. EVEN IF IT USED THE MAKER SIDE, the rebate is theta*p*(1-p) per
       contract and p(1-p) <= 0.25, so it is bounded by
       0.0125 * 0.25 = 0.003125 per contract -- an order of magnitude
       below the 0.02 threshold. Measured: +0.0031/contract at p=0.50.

    So with no curve loaded, EXIT is UNREACHABLE for any quantity, any
    price and either fee side. The desk has never exited a residual on
    the learned rule and structurally could not have.
════════════════════════════════════════════════════════════════════

SO EXIT TIMING HERE IS A DECLARED RULE, AND IT REPLACES NOTHING
VALIDATED. There is no operating timing logic to displace: the learned
one is unreachable and its forecast test was not supported. The rule
below is a stopgap that makes position management TESTABLE without
waiting on a settlement forecast, and a successful forecast is
explicitly NOT a prerequisite for that test.
"""

from __future__ import annotations

POLICY_ID = "RN1X_MGMT_FROZEN_V1"
FROZEN_AT = "2026-09-23"

NOT_IDENTIFIED = "NOT_IDENTIFIED"

# ── the frozen thresholds ────────────────────────────────────────────
# DECLARED, not fitted, and not tuned against any evaluation window.
ADVERSE_MOVE_FRACTION = 0.20      # of assigned basis, on last OBSERVED price
MAX_SECONDS_OPEN = 86_400.0       # one day of unpaired exposure
QUEUE_SHARE = 0.25                # execution ASSUMPTION, swept in reports
ORDER_EXPIRY_S = 900.0

# Every input this policy can read, with its SOURCE CLASS. A decision
# record cites these, so no figure in a trace is unattributed.
SOURCE_CLASS = {
    "assigned_basis": "OBSERVED_INPUT (RN1's own executed fill price)",
    "last_price": "OBSERVED_INPUT (a print on the tape)",
    "bid / complement_ask": "OBSERVED_INPUT (the venue's own book)",
    "depth": "OBSERVED_INPUT",
    "fees": "OBSERVED_INPUT (the published PMUS schedule)",
    "seconds_open": "OBSERVED_INPUT (clock arithmetic on observed stamps)",
    "adverse_move_threshold": "DECLARED_RULE",
    "max_seconds_open": "DECLARED_RULE",
    "exit_method_ranking": "DECLARED_RULE over exact arithmetic",
    "queue_share": "EXECUTION_ASSUMPTION",
    "our_fill": "EXECUTION_ASSUMPTION (print-through; P_FILL NOT_IDENTIFIED)",
    "ev_hold": "MODEL_ESTIMATE -- NOT USED, see CURVE_CANDIDATE",
    "settlement_payout": "OBSERVED_INPUT, available only for SCORING",
}

# The rejected curve, kept visible and inert.
CURVE_CANDIDATE = {
    "id": "FERRARI_RESIDUAL_EXIT_ISOTONIC",
    "status": "RESEARCH_CANDIDATE_NOT_ACTIVATED",
    "forecast_test": ("NOT_SUPPORTED. Paired log-loss difference against "
                      "the identity +0.0052, 95% interval [-0.0017, "
                      "+0.0120], on the later 40% of resolved markets"),
    "forecast_test_is_distinct_from": (
        "any economic test of a management policy. A curve can forecast "
        "no better than the price and still sit inside a policy that "
        "manages well or badly; the two questions do not substitute for "
        "each other and are never reported as one result"),
    "activated_by_this_policy": False,
    "how_it_would_be_activated": (
        "explicitly, by constructing DK.Policy(curve=...) -- which "
        "happens today only in tools/desk_replay.py and "
        "tools/desk_experiments.py, both offline. Nothing in this policy "
        "or in bettor_mgmt_lifecycle does it"),
}

BENCHMARKS = {
    "HOLD_TO_SETTLEMENT": ("the same assigned inventory, carried to the "
                           "observed payout. No action, no fees"),
    "RN1_MANAGEMENT": ("what the source account itself did next on the "
                       "same condition. OBSERVED, at their size and "
                       "their order policy -- WHALE_ORDER_POLICY is "
                       "NOT_IDENTIFIED, so it is a benchmark and not an "
                       "achievable return"),
}

MUST_REPORT = (
    "realised P&L", "open inventory at COST, separately",
    "fees", "matched and residual quantities",
    "unpaired exposure", "capital committed",
    "unresolved positions", "losing positions",
    "inventory discrepancies",
)

DOES_NOT_ESTABLISH = (
    "our fill probability -- P_FILL is NOT_IDENTIFIED",
    "profitability -- one venue, an assumed execution model, no "
    "capacity limit, no adverse selection against our own presence",
    "that RN1's prices were available to us: this is ASSIGNED-ENTRY "
    "testing, not executable replication",
    "that the learned curve forecasts better -- its test said otherwise "
    "and this policy does not consult it",
)


def describe() -> dict:
    return {
        "policy_id": POLICY_ID, "frozen_at": FROZEN_AT,
        "manages": "a position it was HANDED; entry is RN1's",
        "thresholds": {
            "adverse_move_fraction": ADVERSE_MOVE_FRACTION,
            "max_seconds_open": MAX_SECONDS_OPEN,
            "queue_share": QUEUE_SHARE,
            "order_expiry_s": ORDER_EXPIRY_S,
        },
        "thresholds_are": "DECLARED, frozen before the forward test, "
                          "not fitted and not tuned on any window",
        "source_class": dict(SOURCE_CLASS),
        "curve_candidate": dict(CURVE_CANDIDATE),
        "benchmarks": dict(BENCHMARKS),
        "must_report": list(MUST_REPORT),
        "does_not_establish": list(DOES_NOT_ESTABLISH),
        "replaces_no_validated_timing_logic": (
            "the learned residual-exit rule is unreachable in production "
            "(no curve is loaded, and EXIT would need a rebate above 2c "
            "per contract that the schedule cannot produce) and its "
            "forecast test was NOT_SUPPORTED. This rule displaces "
            "nothing that was working"),
    }
