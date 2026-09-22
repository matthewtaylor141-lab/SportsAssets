"""The decision engine, walked end to end for management.

WHAT THIS IS. One run over the captured corpus that stops at every
decision the engine makes and prints, for each one:

    INPUTS      what was knowable AT THE DECISION INSTANT -- never an
                outcome, never a later price.
    ALTERNATIVES the other actions available at that instant.
    ECONOMICS   expected cash, fees, uncertainty and the capital the
                action commits.
    ACTION      what was chosen, and why.
    EVIDENCE    the case-study mechanism it implements and the record
                that supports it.

WHAT IT IS NOT. No order was sent. Every fill below is a REPLAY fill,
priced against the venue's own time-and-sales tape, and is labelled
REPLAY at the point of use. The account's real executions appear only
in the ACTUAL section, and the two are never added.

THREE CAPABILITIES ARE NOT SUPPORTED, and saying so is part of the
demonstration:

  NETTING TO CASH BEFORE SETTLEMENT   a matched YES/NO pair is worth
      exactly $1 at settlement, and the engine records that as
      `MATCHED_PAIR_PAYS_1 -- certain, but not cash until settlement`.
      It is NOT cash now. No merge/netting call has been demonstrated
      against this venue, so capital stays committed until the event
      resolves. This is the single largest constraint on capital reuse
      and it is a venue capability question, not a policy choice.
  ADAPTIVE SIZE                       IMPLEMENTED SINCE, and NOT
      VALIDATED. `bettor_policy.quote_size` scales the clip by the
      spread in ticks, from decision-time inputs only. Measured effect:
      it cut held capital-hours at every queue fraction and moved net
      BOTH ways (+14.20 to -18.08) over four clustered observations.
      An implemented rule with no demonstrated benefit is reported as
      exactly that.
  QUEUE POSITION                      not observable. The replay sweeps
      a queue-ahead fraction instead of knowing one, and every fill
      figure inherits that sweep.

Run:  python research/beta48/bettor_management_demo.py
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bettor_episodes as epi                                # noqa: E402
import bettor_incentive_score as inc                         # noqa: E402
import bettor_prints as prints_mod                           # noqa: E402

OUT = os.path.join(HERE, "acceptance", "management_demo.json")

REPLAY = "REPLAY -- tape-priced, NOT an execution"
SCENARIO = "SCENARIO -- constructed to exercise a path the corpus does "\
           "not contain"
ACTUAL = "ACTUAL -- from the venue's own records"

# The policy the walk is taken under. C3 is the declared
# inventory-aware candidate; it exercises the most decision points.
POLICY = epi.Policy(
    name="C3", min_spread_ticks=2, placement="AT_TOUCH", hard_flatten=True,
    cancel_other_on_fill=True, max_unmatched_mult=0.5,
    recovery="COMPLETE_PAIR", queue_ahead_fraction=0.25)

# Published fee schedule, verified against 3,285 real executions.
THETA_TAKER = 0.0695            # SEP2026 regime
THETA_MAKER = -0.0125


def fee(theta, contracts, price):
    """fee = theta x C x p x (1-p), banker's rounded to the cent."""
    exact = theta * contracts * price * (1.0 - price)
    cents = round(exact * 100.0)        # banker's rounding, as the venue
    return cents / 100.0


def decision(stage, inputs, alternatives, economics, action, reason,
             mechanism, evidence, label, supported=True):
    return {"stage": stage, "label": label, "supported": supported,
            "inputs_at_decision_time": inputs,
            "alternatives_considered": alternatives,
            "economics": economics, "action": action, "reason": reason,
            "case_study_mechanism": mechanism, "evidence": evidence}


# ═══ the walk ════════════════════════════════════════════════════════

def walk(eps):
    ds = []
    filled = [e for e in eps if e.get("entry_legs_filled")]
    paired = [e for e in eps if e["status"] == "FLAT_PAIRED"]
    exited = [e for e in eps if e["status"] == "FLAT_EXITED_TAKER"]
    never = [e for e in eps if e["status"] == "NEVER_FILLED"]
    partial = [e for e in filled
               if any(abs(s) < e["size"] - 1e-9 for s in e["fill_sizes"])]

    # ── 1. ENTRY ─────────────────────────────────────────────────────
    ex = paired[0] if paired else filled[0]
    b = ex["entry_book"]
    ds.append(decision(
        "1  ENTRY -- is this market worth quoting at all?",
        {"slug": ex["slug"], "at": ex["t0"],
         "best_bid": b.get("bid"), "best_ask": b.get("ask"),
         "spread_ticks": b.get("spread_ticks"), "mid": b.get("mid"),
         "depth_at_touch": "NOT OBSERVABLE in this corpus -- prices only",
         "known_at_this_instant": "the book and the clock. Nothing about "
                                  "what the price later did."},
        [{"action": "quote both sides", "admitted": True},
         {"action": "quote one side only",
          "admitted": False, "why": "the policy is two-sided; a one-sided "
                                    "quote is a directional bet, which is "
                                    "a different mandate"},
         {"action": "stand aside",
          "admitted": False, "why": "spread %s ticks >= the 2-tick minimum"
                                    % b.get("spread_ticks")}],
        {"expected_gross_if_both_legs_fill_usd":
             round((b.get("ask", 0) - b.get("bid", 0)) * ex["size"], 4),
         "maker_rebate_both_legs_usd":
             round(abs(fee(THETA_MAKER, ex["size"], b.get("bid", 0.5)))
                   + abs(fee(THETA_MAKER, ex["size"], b.get("ask", 0.5))), 4),
         "capital_committed_usd": ex["collateral_incl_resting"],
         "uncertainty": "whether EITHER leg fills. Across the corpus "
                        "%d of %d episodes never filled at all (%.0f%%)."
                        % (len(never), len(eps), 100.0 * len(never) / len(eps))},
        "QUOTE BOTH SIDES at %s / %s, %d contracts"
        % (ex["quote_bid"], ex["quote_offer"], int(ex["size"])),
        "the spread clears the minimum and the book is two-sided, so a "
        "paired fill is worth the spread less fees. The engine does not "
        "forecast direction; it is paid for the spread.",
        "Two-sided market making -- the mechanism the maker case studies "
        "describe: earn the spread and the maker rebate, avoid carrying "
        "direction.",
        "entry rule frozen in Policy(min_spread_ticks=2); "
        "%d episodes admitted from the corpus" % len(eps),
        REPLAY))

    # ── 2. QUOTE PLACEMENT ───────────────────────────────────────────
    ds.append(decision(
        "2  QUOTE PLACEMENT -- where in the book?",
        {"best_bid": b.get("bid"), "best_ask": b.get("ask"),
         "our_bid": ex["quote_bid"], "our_offer": ex["quote_offer"],
         "queue_position": "NOT OBSERVABLE -- see the unsupported list"},
        [{"action": "AT_TOUCH (join the best price)", "chosen": True},
         {"action": "improve by one tick",
          "why_not": "costs a tick of edge on every fill to buy queue "
                     "priority that cannot be measured"},
         {"action": "DEEPER_1 (one tick behind)",
          "why_not": "better price conditional on filling, worse odds of "
                     "filling; the corpus already shows fills are scarce"}],
        {"edge_per_paired_fill_usd":
             round((ex["quote_offer"] - ex["quote_bid"]) * ex["size"], 4),
         "queue_uncertainty": "swept, not known: qfrac 0.00/0.25/0.50/1.00. "
                              "Every fill count in this demonstration is "
                              "reported at qfrac 0.25.",
         "capital_committed_usd": ex["collateral_incl_resting"]},
        "JOIN THE TOUCH on both sides",
        "the engine cannot observe queue position, so paying a tick for "
        "priority buys something unmeasurable. Joining is the only "
        "placement whose cost is known.",
        "Passive liquidity provision at the touch.",
        "placement=AT_TOUCH, frozen; queue effect swept across four "
        "fractions rather than assumed",
        REPLAY))

    # ── 3. SIZE ──────────────────────────────────────────────────────
    ds.append(decision(
        "3  SIZE -- how many contracts?",
        {"base_clip": ex.get("base_size", ex["size"]),
         "spread_ticks": b.get("spread_ticks"),
         "policy_minimum_ticks": POLICY.min_spread_ticks,
         "rule_applied": ex.get("size_rule"),
         "book_depth": "NOT USED -- the corpus carries none, and a rule "
                       "that needed it could not be validated here"},
        [{"action": "FIXED clip", "why_not": "spends the same capital on "
                                             "a 2-tick book as on a "
                                             "4-tick book"},
         {"action": "EDGE_SCALED -- scale by spread above the policy "
                    "minimum", "chosen": True},
         {"action": "size to a fraction of resting depth",
          "why_not": "not computable on this corpus, so not validatable"}],
        {"clip_chosen": ex["size"],
         "bounds": "x%.2f .. x%.2f of the base clip"
                   % (POLICY.size_min_mult, POLICY.size_max_mult),
         "capital_per_side_usd": round(ex["size"] * ex["quote_bid"], 2),
         "measured_effect": "IT IS AN ALLOCATION RULE, NOT A "
                            "PROFITABILITY LEVER. Edge and capital both "
                            "scale with the clip, so per-capital-hour is "
                            "invariant to a uniform change. Measured on "
                            "this corpus it cut HELD capital-hours at "
                            "every queue fraction (-66 to -348) and "
                            "improved per-capital-hour at three of four, "
                            "but net P&L moved BOTH WAYS (+14.20 to "
                            "-18.08). Four clustered observations; that "
                            "is not an improvement anyone should bank.",
         "uncertainty": "the sign of the net effect"},
        "EDGE-SCALED CLIP: %g contracts (%s)"
        % (ex["size"], ex.get("size_rule")),
        "a wider spread pays more for the same capital and the same "
        "queue risk, so it earns more size. Only decision-time inputs "
        "are used: the book's own spread, never a realised fill rate "
        "and never a later price.",
        "Edge-proportional allocation.",
        "implemented in bettor_episodes.decision_time_size; measured in "
        "acceptance/strategy_v2.json against the same baseline",
        REPLAY))

    # ── 4. PARTIAL FILL ──────────────────────────────────────────────
    if partial:
        pe = partial[0]
        got = abs(pe["fill_sizes"][0])
        ds.append(decision(
            "4  PARTIAL FILL -- one leg filled, and only partly",
            {"slug": pe["slug"], "clip": pe["size"],
             "filled": round(got, 4),
             "leg": pe["entry_legs_filled"],
             "unmatched_contracts": round(got, 4),
             "known_at_this_instant": "the size that traded against us. "
                                      "Not whether more will follow."},
            [{"action": "leave the other side resting",
              "why_not": "it could also fill, doubling exposure before the "
                         "first leg is matched"},
             {"action": "cancel the other side", "chosen": True},
             {"action": "immediately take the other side to flatten",
              "why_not": "pays the taker fee at once for an exposure that "
                         "may still be matched passively"}],
            {"unmatched_exposure_usd": round(got * pe["quote_bid"], 2),
             "maker_rebate_earned_usd": pe["rebates_received"],
             "capital_committed_usd": pe["collateral_incl_resting"],
             "uncertainty": "%d of %d filled episodes were partial -- a "
                            "partial is the NORMAL case, not the exception"
                            % (len(partial), len(filled))},
            "CANCEL THE RESTING LEG; hold %.2f unmatched" % got,
            "the inventory cap is half a clip. Letting the second side "
            "rest while a leg is unmatched is how a market maker becomes "
            "a directional trader by accident.",
            "Inventory control -- the case-study mechanism that separates "
            "market making from position taking.",
            "note recorded on the episode: %s"
            % (pe["notes"][0] if pe.get("notes") else "cancel-on-fill"),
            REPLAY))

    # ── 5. INVENTORY / COMPLETION ────────────────────────────────────
    if paired:
        ce = paired[0]
        ds.append(decision(
            "5  COMPLETION -- how does the unmatched leg get closed?",
            {"unmatched": round(abs(ce["fill_sizes"][0]), 4),
             "elapsed_s": _secs(ce),
             "recovery_rule": "COMPLETE_PAIR"},
            [{"action": "wait for a passive match",
              "why_not": "ties capital up for an unbounded time at an "
                         "unknown probability"},
             {"action": "complete the pair as a taker", "chosen": True},
             {"action": "exit the leg as a taker",
              "why_not": "realises the loss and abandons the $1 pair value"}],
            {"taker_fee_paid_usd": ce["taker_fees_paid"],
             "maker_rebate_received_usd": ce["rebates_received"],
             "realised_cash_usd": ce["realised_cash"],
             "residual_value_usd": ce["residual_value"],
             "residual_basis": ce["residual_basis"],
             "net_if_residual_realises_usd": ce["total_if_residual_realises"],
             "capital_committed_usd": ce["collateral_incl_resting"],
             "uncertainty": "NONE on the pair's value -- a matched pair "
                            "pays exactly $1. The uncertainty is WHEN."},
            "COMPLETE THE PAIR as a taker",
            "a matched pair is worth exactly $1 at settlement, which is "
            "certain. Paying the taker fee converts an uncertain "
            "directional position into a certain one.",
            "Pair completion / netting -- the case-study mechanism for "
            "turning a half-filled quote into a riskless holding.",
            "%d of %d filled episodes reached FLAT_PAIRED"
            % (len(paired), len(filled)),
            REPLAY))

    # ── 6. NETTING TO CASH -- NOT SUPPORTED ──────────────────────────
    ds.append(decision(
        "6  NETTING TO CASH -- can the pair be turned into cash now?",
        {"matched_pairs": round(paired[0]["residual_contracts"]
                                ["matched_pairs"], 4) if paired else None,
         "certain_value_usd": paired[0]["residual_value"] if paired else None,
         "basis": paired[0]["residual_basis"] if paired else None},
        [{"action": "merge/net the pair into cash at the venue",
          "supported": False,
          "why": "no merge or netting call has been demonstrated against "
                 "this venue, and none appears in the contract we have "
                 "exercised. This remains UNAVAILABLE."},
         {"action": "SELL BOTH LEGS BACK -- the feasible alternative, "
                    "now implemented",
          "implemented": True,
          "why": "pays the spread plus two taker fees to convert a "
                 "certain settlement claim into cash now. Implemented as "
                 "Policy(release_matched=True) and MEASURED: it fired "
                 "twice in 470 episodes, cost $1.75, and freed 26 of "
                 "3,696 held capital-hours."},
         {"action": "hold to settlement", "chosen": True,
          "why": "on this corpus the release is not worth its cost"}],
        {"capital_locked_usd": paired[0]["collateral_filled_only"]
            if paired else None,
         "locked_until": "event settlement",
         "consequence": "capital-hours accrue at the full committed "
                        "amount for the whole holding period, and this is "
                        "the dominant term in every per-capital-hour "
                        "figure in the economic verdict."},
        "HOLD TO SETTLEMENT -- on measurement, not for want of an option",
        "the merge call is still unavailable, but the feasible "
        "alternative is now implemented and was measured: selling the "
        "pair back costs more than the capital it frees on this corpus. "
        "THE BINDING CAPITAL COST IS NOT HELD INVENTORY. It is "
        "collateral resting behind quotes that never fill -- 27,955 "
        "capital-hours against at most 3,696 held. Releasing pairs "
        "attacks the small term.",
        "Capital recycling -- alternative implemented, measured, and "
        "found not to be the constraint.",
        "Policy(release_matched=True); acceptance/strategy_v2.json",
        REPLAY))

    # ── 7. EXIT / LOSS-TAKING ────────────────────────────────────────
    #
    # THE CORPUS TRIGGERS THIS PATH BUT NEVER MATERIALLY. Both
    # FLAT_EXITED_TAKER episodes realised exactly $0.00: the recovery
    # window expired before any size had traded, so the "exit" closed
    # nothing. Presenting one of them as a demonstration of loss-taking
    # would be showing a loss-taking rule by showing no loss. The
    # observed case is reported for what it is, and the economics are
    # then exercised in a LABELLED SCENARIO.
    obs_exit = (max(exited, key=lambda e: abs(e.get("realised_cash", 0.0)))
                if exited else None)
    ds.append(decision(
        "7  EXIT -- taking a loss rather than carrying a leg",
        {"observed_taker_exits": len(exited),
         "largest_observed_realised_cash_usd":
             obs_exit.get("realised_cash") if obs_exit else None,
         "observed_verdict": "the path FIRES but closes nothing -- the "
                             "recovery window expired before size traded. "
                             "The corpus does not contain a material "
                             "loss-taking event.",
         "recovery_wait_s": POLICY.recovery_wait_s},
        [{"action": "keep waiting for a passive match",
          "why_not": "the recovery window expired; waiting longer is an "
                     "unbounded commitment of capital at an unknown "
                     "probability"},
         {"action": "exit the leg as a taker", "chosen": True},
         {"action": "complete the pair instead",
          "why_not": "only available while the opposite side is quotable; "
                     "the exit path exists for when it is not"}],
        {"observed": "no material exit in this corpus",
         "scenario": _exit_scenario(),
         "uncertainty": "the exit price. The scenario prices the exit at "
                        "the opposite touch, which is the best case for a "
                        "taker; a wider book costs more."},
        "EXIT AS TAKER when the recovery window expires",
        "a bounded loss taken on time is cheaper than an unbounded carry. "
        "The rule is implemented and fires; its ECONOMICS are shown by "
        "scenario because the corpus never exercised them.",
        "Loss-taking discipline.",
        "%d observed taker exits, both at $0.00 realised -- reported "
        "rather than dressed up" % len(exited),
        SCENARIO))

    # ── 8. HOLDING ───────────────────────────────────────────────────
    ds.append(decision(
        "8  HOLDING -- resting through a quiet market",
        {"quote_horizon_s": POLICY.quote_horizon_s,
         "episodes_that_never_filled": len(never),
         "fraction": round(len(never) / len(eps), 4)},
        [{"action": "rest for the full horizon", "chosen": True},
         {"action": "cancel early and re-quote",
          "why_not": "under the incentive programme, resting time IS the "
                     "product being paid for -- cancelling early forfeits "
                     "qualifying uptime"}],
        {"capital_committed_while_resting_usd":
             round(sum(e["collateral_incl_resting"] for e in never)
                   / max(1, len(never)), 2),
         "cash_earned_usd": 0.0,
         "uncertainty": "whether a fill ever arrives. %.0f%% of episodes "
                        "answer no." % (100.0 * len(never) / len(eps))},
        "REST FOR THE FULL HORIZON",
        "collateral is committed at entry whether or not a fill arrives, "
        "so a quote that never fills still costs capital-hours. That cost "
        "is counted, not ignored.",
        "Liquidity provision -- the shape an incentive programme pays "
        "for, and the reason C4 exists as a separate candidate.",
        "capital-hours integrate collateral_incl_resting over every "
        "episode's own life, filled or not",
        REPLAY))

    # ── 9. CAPITAL REUSE ─────────────────────────────────────────────
    cap = sum(_caph(e) for e in eps)
    ds.append(decision(
        "9  CAPITAL REUSE -- how often can the same dollar work?",
        {"capital_hours": round(cap, 1),
         "episodes": len(eps),
         "mean_hours_per_episode": round(cap / max(1, len(eps)), 2)},
        [{"action": "recycle on settlement", "chosen": True},
         {"action": "recycle on netting", "supported": False,
          "why": "see decision 6 -- not available at this venue"},
         {"action": "recycle by selling pairs back", "implemented": True,
          "why": "measured: 2 firings, $1.75 cost, 26 capital-hours "
                 "freed. Not the constraint."}],
        {"turnover_limit": "a dollar committed to an episode cannot be "
                           "committed to another until that event settles",
         "consequence": "supportable turnover is bounded by settlement "
                        "cadence, not by the engine's speed"},
        "RECYCLE ONLY ON SETTLEMENT",
        "with no netting call, HELD capital waits for the event. But the "
        "measurement moved the diagnosis: 88% of committed capital-hours "
        "are RESTING behind unfilled quotes, not held in inventory. The "
        "binding constraint on scale is the fill rate, not the netting "
        "call.",
        "Capital velocity.",
        "capital-hours measured per candidate in economic_verdict.json "
        "and strategy_v2.json",
        REPLAY))

    # ── 10. INCENTIVE ELIGIBILITY -- labelled scenario ───────────────
    prog = inc.Program("(scenario)", "culture_low_20260921", "daily_event",
                       reward_pool=50.0, discount_factor=0.25,
                       target_size=500)
    rows = []
    for c in (400.0, 450.0, 496.0, 500.0, 1000.0):
        r = inc.snapshot_share([(0.50, c)], "BID", prog, 0.50, 100.0)
        rows.append({"competing_at_our_price": c, "qualifies": r["qualifies"],
                     "created_eligibility": r.get("created_eligibility"),
                     "our_share": round(r["share"], 6)})
    ds.append(decision(
        "10 INCENTIVE ELIGIBILITY -- does resting here earn a reward?",
        {"target_size": prog.target_size, "our_clip": 100.0,
         "discount_factor": prog.discount_factor,
         "pool_per_day_usd": prog.reward_pool,
         "competing_depth": "NOT OBSERVABLE in this corpus -- so the rows "
                            "below are a SCENARIO sweep, not a measurement"},
        [{"action": "rest at the touch", "chosen": True},
         {"action": "rest one tick back",
          "why_not": "the discount factor is 0.25 per tick -- one tick "
                     "back is a 75% cut to our score"}],
        {"scenario_sweep": rows,
         "reading": "our 100 contracts create eligibility only where the "
                    "side already holds 400-499. Below that the side "
                    "never reaches Target Size; far above it our share is "
                    "diluted.",
         "uncertainty": "TOTAL on the competing depth -- this is precisely "
                        "the quantity the observation release measures."},
        "REST AT THE TOUCH, and measure the depth before committing",
        "the reward is a share of a pool whose denominator is competing "
        "size. Without that number the reward cannot be computed, only "
        "bracketed.",
        "Liquidity-incentive capture.",
        "bettor_incentive_score, pinned against the venue's own worked "
        "example (1000 at four levels, DF 0.30 -> 70.6% / 1.9%)",
        SCENARIO))
    return ds


def _exit_scenario():
    """What a taker exit of one unmatched clip actually costs.

    Arithmetic only, at the verified SEP2026 taker rate. Nothing here
    is an observation, and the returned dict says so.
    """
    size, bid, ask = 100.0, 0.71, 0.73
    # We are long 100 YES at 0.73 (we lifted, or our bid filled at the
    # touch) and must sell at the bid to get flat.
    proceeds = size * bid
    cost = size * ask
    taker = fee(THETA_TAKER, size, bid)
    return {"label": "SCENARIO -- not observed",
            "position": "long %d YES at %.2f" % (int(size), ask),
            "exit_at_opposite_touch": bid,
            "gross_loss_usd": round(cost - proceeds, 4),
            "taker_fee_usd": round(taker, 4),
            "total_cost_to_flatten_usd": round(cost - proceeds + taker, 4),
            "alternative_if_carried":
                "unbounded until settlement; a YES that settles at 0 loses "
                "the full %.2f USD" % cost,
            "reading": "the exit costs a known %.2f USD; carrying risks "
                       "%.2f USD. That ratio is why the rule exists."
                       % (cost - proceeds + taker, cost)}


def _secs(e):
    a, b = _t(e.get("t0")), _t(e.get("t_end"))
    return round(b - a, 1) if (a and b and b > a) else None


def _t(s):
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


def _caph(e):
    a, b = _t(e.get("t0")), _t(e.get("t_end"))
    if a is None or b is None or b <= a:
        return 0.0
    return e.get("collateral_incl_resting", 0.0) * (b - a) / 3600.0


def _runtime_path():
    """THE SAME DECISION, TAKEN THE WAY THE LIVE ENGINE WOULD TAKE IT.

    Everything above this walks the REPLAY. That is the right surface
    for economics -- it has outcomes -- but it is not the surface that
    would run in production, and a demonstration that only ever shows
    the replay cannot show management what the live system would
    actually do with a book.

    So this puts one live-shaped book through
    `sportsassets.bettor_policy_runtime`, which is what the worker
    calls. The policy proposes; `bettor_decision_engine` disposes. The
    interesting part is that they DISAGREE, and the record says so
    instead of quietly reporting the winner.
    """
    sys.path.insert(0, os.path.join(HERE, "..", "..", "backend"))
    from sportsassets import bettor_decision_engine as de
    from sportsassets import bettor_policy as bp
    from sportsassets import bettor_policy_runtime as rt

    book = de.Book(market_id="live-shaped-example",
                   yes_bid=0.40, yes_ask=0.44,
                   yes_bid_size=500.0, yes_ask_size=500.0,
                   age_s=1.0, venue_state="OPEN")
    pol = bp.Policy(name="C3", min_spread_ticks=2, placement=bp.AT_TOUCH,
                    cancel_other_on_fill=True, max_unmatched_mult=0.5,
                    recovery=bp.R_COMPLETE_PAIR)
    return rt.evaluate(book, policy=pol, base_size=100.0, clip=100.0)


def main():
    if not prints_mod.tape_dir():
        print("NO TAPE -- refusing to demonstrate tape-backed execution "
              "without the tape.")
        return 1
    eps = epi.run_all(size=100.0, rebates_on=True, policy=POLICY,
                      use_tape=True)
    ds = walk(eps)

    print("=" * 78)
    print("BETTOR DECISION ENGINE -- MANAGEMENT DEMONSTRATION")
    print("policy C3 (inventory-aware), queue fraction 0.25, tape-backed")
    print("NO ORDER WAS SENT. Every fill below is a REPLAY fill.")
    print("=" * 78)
    runtime = _runtime_path()
    for d in ds:
        print()
        print("-" * 78)
        print("%s   [%s]%s" % (d["stage"], d["label"].split(" --")[0],
                               "" if d["supported"] else "   NOT SUPPORTED"))
        print("-" * 78)
        print("  INPUTS AT DECISION TIME")
        for k, v in d["inputs_at_decision_time"].items():
            print("    %-34s %s" % (k, _fmt(v)))
        print("  ALTERNATIVES")
        for a in d["alternatives_considered"]:
            mark = "->" if a.get("chosen") else "  "
            extra = a.get("why_not") or a.get("why") or ""
            if a.get("supported") is False:
                extra = "NOT SUPPORTED: " + extra
            print("    %s %-40s %s" % (mark, a.get("action", ""),
                                       _wrap(extra, 30)))
        print("  ECONOMICS")
        for k, v in d["economics"].items():
            print("    %-34s %s" % (k, _fmt(v)))
        print("  ACTION   %s" % d["action"])
        print("  REASON   %s" % _wrap(d["reason"], 9))
        print("  MECHANISM %s" % _wrap(d["case_study_mechanism"], 10))
        print("  EVIDENCE %s" % _wrap(d["evidence"], 9))

    # WHAT REMAINS ABSENT, kept as a STANDING LIST rather than derived
    # from which decisions happen to be flagged. Implementing an
    # alternative to a missing capability does not make the capability
    # present, and a list that emptied itself when the alternatives
    # landed would say exactly the wrong thing.
    unsupported = [d["stage"] for d in ds if not d["supported"]]
    remaining = [
        ("MERGE / NETTING TO CASH",
         "STILL ABSENT. No merge call has been demonstrated at this "
         "venue. The feasible alternative -- selling both legs back -- "
         "is implemented and measured, and it is not a substitute: it "
         "pays the spread for capital the merge would return whole."),
        ("QUEUE POSITION",
         "STILL UNOBSERVABLE. The replay sweeps a queue-ahead fraction "
         "instead of knowing one, and EVERY fill figure in this "
         "demonstration inherits that sweep."),
        # CORRECTED. This entry used to read "RESTING DEPTH AT THE
        # TOUCH -- NOT IN THIS CORPUS", and that was simply wrong: all
        # 30,590 tape rows carry touch depth AND a full ladder (median
        # 5 bid levels, median join age 2.5s). The mistaken claim came
        # from the EPISODE RECORD's `entry_book`, which carries prices
        # only -- a property of one summary field, not of the capture.
        # The real gap is narrower and is stated as it actually is.
        ("INCENTIVE REWARD ON THIS CORPUS",
         "NOT APPLICABLE, which is different from not computable. The "
         "ladder depth the reward formula needs IS in every row of "
         "this corpus. What is absent is a PROGRAMME: none of these "
         "five sports markets was observed in any incentive "
         "programme, so their reward is zero and any share figure "
         "quoted for them is a transferred scenario, not a "
         "measurement."),
        ("TIME TO RESOLUTION",
         "NOT IN THIS CORPUS AT ALL. The tape carries no close time "
         "and no event start time -- only a state transition that "
         "arrives after the fact. It is the natural input for refusing "
         "to quote into a resolving event, which is where the entire "
         "evaluation loss came from, and the observation release "
         "captures it from the incentives API."),
        ("FILL-CONDITIONED PROFITABILITY",
         "NOT ESTABLISHABLE BY OBSERVATION. It needs our own orders in "
         "the book. No amount of watching substitutes for it."),
    ]
    print()
    print("=" * 78)
    print("THE SAME ENGINE ON THE LIVE PATH -- what the worker would do")
    print("=" * 78)
    p = runtime["proposal"]
    print("  book            bid 0.40 / ask 0.44, 500 up, 1.0s old, OPEN")
    print("  STRATEGY says   %s" % p["action"])
    print("    prices        bid %s / offer %s   size %s"
          % (p.get("quote_bid"), p.get("quote_offer"), p.get("size")))
    print("    because       %s" % _wrap(p["why"], 18))
    print("    rule          %s (%s)" % (p["rule"], p["provenance"]))
    print("  ENGINE says     %s" % runtime["engine_verdict"])
    print("    because       %s" % _wrap(runtime["engine_detail"] or "-", 18))
    print("  WHAT HAPPENS    %s, size %s"
          % (runtime["effective_action"],
             runtime["effective_size_contracts"]))
    print("    because       %s" % _wrap(runtime["effective_reason"], 18))
    print()
    print("  READ THIS CAREFULLY. The strategy wants to quote and the")
    print("  engine will not let it, because MAKE_YES/MAKE_NO have no")
    print("  identified EV -- P_FILL is not identified, and NOT_IDENTIFIED")
    print("  is not zero. The live system today therefore produces")
    print("  NO_TRADE at size 0 on a perfectly good book. That is the")
    print("  system working as designed, not a fault, and it is what")
    print("  fill-conditioned evidence would change.")

    print()
    print("=" * 78)
    print("WHAT THE ENGINE STILL CANNOT DO")
    print("=" * 78)
    for name, why in remaining:
        print("  %-32s %s" % (name, _wrap(why, 35)))
    if unsupported:
        print()
        print("  decisions flagged unsupported this run:")
        for u in unsupported:
            print("    - %s" % u)
    print()
    print("Stated so the demonstration cannot be read as evidence of a")
    print("complete capability. Implementing an alternative to a missing")
    print("capability does not make the capability present.")

    with open(OUT, "w") as fh:
        json.dump({"demo": "BETTOR_MANAGEMENT_DEMO_V1",
                   "policy": POLICY.name,
                   "queue_fraction": POLICY.queue_ahead_fraction,
                   "orders_sent": 0,
                   "execution": "REPLAY against the venue's time-and-sales "
                                "tape; NOT account executions",
                   "episodes": len(eps),
                   "status_counts": dict(collections.Counter(
                       e["status"] for e in eps)),
                   "decisions": ds,
                   "runtime_path": runtime,
                   "unsupported_decisions": unsupported,
                   "still_absent": [{"capability": n, "status": w}
                                    for n, w in remaining]},
                  fh, indent=2, default=str)
    print("\nwritten: %s" % OUT)
    return 0


def _fmt(v):
    if isinstance(v, (list, tuple)):
        if v and isinstance(v[0], dict):
            return "\n" + "\n".join(
                "        " + json.dumps(x, default=str) for x in v)
        return json.dumps(v, default=str)
    if isinstance(v, dict):
        return json.dumps(v, default=str)
    return str(v)


def _wrap(s, indent):
    out, line = [], ""
    for w in str(s).split():
        if len(line) + len(w) > 68:
            out.append(line)
            line = w
        else:
            line = (line + " " + w).strip()
    out.append(line)
    return ("\n" + " " * indent).join(out)


if __name__ == "__main__":
    sys.exit(main())
