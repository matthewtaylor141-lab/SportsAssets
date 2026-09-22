"""The economic verdict: does any declared candidate qualify?

WHAT THIS CLOSES. `bettor_policy_final` ran C4 at a reward of ZERO and
reported the BREAK-EVEN rate, because the venue's incentive terms had
never been retrieved. They have been now -- unauthenticated, on
2026-09-22, from `GET /v1/incentives` -- so the hurdle can be compared
against a real schedule instead of left open.

THE THREE KINDS OF EVIDENCE, KEPT APART AND NEVER ADDED:

  ACTUAL ACCOUNT      what the venue's own records say happened. Fees
                      and rebates on 3,285 real executions; the
                      account's current position state.
  DEVELOPMENT REPLAY  policies run over a captured corpus with
                      tape-backed execution. Simulated. The corpus is
                      fully consumed, so nothing here is out of sample.
  PROSPECTIVE         what a future measurement could establish, and
                      what it cannot.

THE REWARD IS COMPUTED, NOT ASSUMED. `bettor_incentive_score`
implements the documented rules -- the discounted walk to Target Size,
per-side normalisation, our own order's effect on eligibility -- and is
pinned against the venue's own worked example. What is assumed here is
ONE thing, stated plainly and carried into every output:

    THE BOOK IS TAKEN AT ENTRY AND HELD CONSTANT for the episode's
    life. The corpus has one book per episode entry, not a second-by-
    second ladder, so a reward integrated over real snapshots cannot be
    computed from it. This is exactly the measurement the observation
    release exists to take, and until it is taken the reward figure
    here is an ESTIMATE UNDER A STATED ASSUMPTION, not a measurement.

AND THE CORPUS MARKETS ARE NOT THE PROGRAMME MARKETS. The 12-market
corpus was captured for a different purpose; the retrieved programmes
run on culture, crypto and eFootball markets. Applying those terms to
these books is a TRANSFER, and a transfer is an assumption too.

Run:  python research/beta48/bettor_economic_verdict.py
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
import bettor_policy_final as pf                             # noqa: E402
import bettor_prints as prints_mod                           # noqa: E402

OUT = os.path.join(HERE, "acceptance", "economic_verdict.json")

# ── the retrieved programme terms ────────────────────────────────────
#
# Retrieved 2026-09-22T18:14Z from the unauthenticated
# `GET /v1/incentives`. These are the venue's numbers, not ours.
PROGRAMMES = {
    "culture_daily": inc.Program(
        market_slug="(transferred)", program_id="culture_low_20260921",
        period="daily_event", reward_pool=50.0, discount_factor=0.25,
        target_size=500),
    "crypto_1h": inc.Program(
        market_slug="(transferred)", program_id="crypto_1h",
        period="daily_event", reward_pool=30.0, discount_factor=0.25,
        target_size=500),
    "efootball_live": inc.Program(
        market_slug="(transferred)", program_id="efootball_live",
        period="daily_event", reward_pool=100.0, discount_factor=0.50,
        target_size=5000),
}

# The documented minimum payout, and the three candidate aggregations
# the documentation does NOT settle between.
MIN_PAYOUT = 1.00
A1 = "PER_MARKET_PER_DATE"      # each (market, date) must clear $1
A2 = "PER_DATE_ALL_MARKETS"     # a day's total across markets must clear
A3 = "PER_PROGRAMME_PERIOD"     # the whole programme period must clear

CLIP = 100.0                    # contracts per side, as C4 quotes


def _t(s):
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


# ── WHAT THE CORPUS ACTUALLY CARRIES, AND WHAT IT DOES NOT ───────────
#
# MEASURED, NOT ASSUMED. An episode's `entry_book` is
#
#     {"bid": 0.72, "ask": 0.73, "spread": 0.01, "spread_ticks": 1,
#      "mid": 0.725, "bid_at_touch": true, "offer_at_touch": true}
#
# -- PRICES ONLY, and the conclusion drawn from that was WRONG.
#
# WHAT THIS MODULE USED TO SAY, and why it was a mistake worth naming:
# "there are no sizes at any level, on either side ... the reward is
# NOT COMPUTABLE FROM THIS CORPUS." That was a fact about the dict
# above -- one summary field on the episode record -- and it was
# reported as a fact about the CAPTURE. It is not. All 30,590 tape rows
# carry touch depth AND a full ladder, median 5 bid levels, median join
# age 2.5s, `no_ladder: 0`. The sizes were there the whole time.
#
# `entry_book` now carries `bid_depth`, `ask_depth` and a bounded
# `ladder`, so the walk the reward formula needs can be performed.
#
# WHAT IS ACTUALLY ABSENT is narrower and is a different kind of
# absence: a PROGRAMME. None of the five sports markets in this corpus
# was observed in any incentive programme, so their reward is ZERO --
# a finding, not a missing input -- and every share figure quoted for
# them is a transferred scenario from the culture / crypto / eFootball
# programmes, which cover different instruments entirely.
NOT_COMPUTABLE = "REWARD_NOT_COMPUTABLE_FROM_THIS_CORPUS"
NOT_APPLICABLE = "NO_INCENTIVE_PROGRAMME_OBSERVED_ON_THESE_MARKETS"

# The competing sizes the sensitivity is run at. These are SCENARIO
# PARAMETERS, not observations -- the whole point is that the corpus
# cannot supply the real ones.
COMPETING_DEPTHS = (0.0, 100.0, 250.0, 400.0, 500.0, 1000.0, 2500.0)


def corpus_supplies_depth(eps) -> dict:
    """Does any episode carry a size anywhere in its book? Checked.

    The key list is reported whatever the answer, because the previous
    version of this check looked for names the record did not use and
    concluded the capture had no depth. A check that reports what it
    DID find cannot make that mistake silently twice.
    """
    keys, with_touch, with_ladder, levels = set(), 0, 0, []
    for e in eps:
        b = e.get("entry_book")
        if not isinstance(b, dict):
            continue
        keys |= set(b)
        if b.get("bid_depth") is not None or b.get("ask_depth") is not None:
            with_touch += 1
        lad = b.get("ladder")
        if isinstance(lad, dict) and (lad.get("bids") or lad.get("offers")):
            with_ladder += 1
            levels.append(len(lad.get("bids") or ()))
    return {"episodes": len(eps),
            "episodes_with_touch_depth": with_touch,
            "episodes_with_ladder": with_ladder,
            "median_bid_levels": (sorted(levels)[len(levels) // 2]
                                  if levels else None),
            "entry_book_keys": sorted(keys),
            "depth_available": with_ladder > 0}


def period_exposure(eps) -> dict:
    """SCORING EXPOSURE, from RESTING QUANTITY over time.

    THE CORRECTION THIS MAKES. The first version multiplied a pool by
    an episode's wall-clock fraction of a day, as though the full clip
    rested on both sides for the whole episode. It does not:

      A FILL REMOVES RESTING SIZE.   Contracts that trade are no longer
          in the book and no longer score. A partial fill of 52 of 100
          leaves 48 resting, not 100 -- and partials are the NORMAL
          case in this corpus.
      A CANCELLATION REMOVES IT ENTIRELY. C3 cancels the opposite leg
          on the first fill; from `cancel_requested_at_obs` onward that
          side rests nothing at all.
      TWO SIDES ARE TWO EXPOSURES.  The pool is normalised per side,
          so a one-sided quote earns on one side only.

    So exposure is integrated as SIZE-WEIGHTED TIME and then expressed
    as a fraction of a full-clip day. `qty_fraction` is the share of
    the clip still resting; an episode that fills half its bid and
    cancels its offer contributes far less than its wall clock says.

    WHAT IS STILL APPROXIMATE, AND IT IS SAID RATHER THAN HIDDEN: the
    corpus records fill SIZES but not fill TIMESTAMPS, so a fill is
    attributed to the episode's cancel instant where one exists and to
    the episode midpoint otherwise. That places the reduction in time
    approximately; it does not change its magnitude.
    """
    period_s = 86400.0
    total_frac, market_days = 0.0, set()
    detail = {"episodes": 0, "with_partial": 0, "with_cancel": 0,
              "full_clip_side_seconds": 0.0, "resting_side_seconds": 0.0}
    for e in eps:
        t0, t1 = _t(e.get("t0")), _t(e.get("t_end"))
        if t0 is None or t1 is None or t1 <= t0:
            continue
        detail["episodes"] += 1
        market_days.add((e.get("slug"), (e.get("t0") or "")[:10]))
        span = t1 - t0
        clip = float(e.get("size") or CLIP)
        sides = [s for s in ("quote_bid", "quote_offer") if e.get(s)]
        n_sides = len(sides) or 1
        detail["full_clip_side_seconds"] += span * n_sides

        filled = sum(abs(float(x)) for x in (e.get("fill_sizes") or ()))
        entry_filled = min(filled, clip) if filled else 0.0
        if entry_filled and entry_filled < clip - 1e-9:
            detail["with_partial"] += 1

        # When the resting size was reduced. A cancel instant is
        # recorded as an OBSERVATION INDEX, so it is converted through
        # the episode's own observation count.
        obs = max(1, int(e.get("observations") or 1))
        ci = e.get("cancel_requested_at_obs")
        cut = (t0 + span * min(1.0, float(ci) / obs)) if ci is not None \
            else (t0 + span * 0.5 if entry_filled else t1)
        if ci is not None:
            detail["with_cancel"] += 1

        for side in sides:
            # Before the cut the side rests the whole clip; after it,
            # the clip less what filled -- and nothing at all once the
            # policy cancelled it.
            before = (cut - t0)
            after = (t1 - cut)
            rest_after = 0.0 if ci is not None else max(
                0.0, clip - entry_filled) / clip
            detail["resting_side_seconds"] += before * 1.0 + after * rest_after
    frac = detail["resting_side_seconds"] / (2.0 * period_s)
    total_frac = frac
    return {"summed_period_fractions": round(total_frac, 4),
            "market_days": len(market_days),
            "mean_fraction_per_market_day":
                round(total_frac / len(market_days), 4)
                if market_days else None,
            "basis": "SIZE-WEIGHTED resting time across BOTH sides, "
                     "reduced by partial fills and by cancellations; "
                     "expressed as full-clip two-sided days",
            "naive_wall_clock_side_days":
                round(detail["full_clip_side_seconds"] / (2.0 * period_s), 4),
            "detail": detail,
            "approximation": "fill TIMESTAMPS are absent from the corpus; "
                             "a reduction is placed at the cancel instant "
                             "where one exists and at the episode midpoint "
                             "otherwise"}


def required_share(loss_usd, prog, exposure) -> dict:
    """The share a TRANSFERRED programme would have to pay on THIS corpus.

    NOT A MEASURED QUALIFICATION HURDLE, and the label travels with the
    number. Three things make it a scenario:

      1. THE PROGRAMME IS TRANSFERRED. These terms run on culture,
         crypto and eFootball markets; this corpus is 12 markets
         captured for another purpose. No programme has ever been
         observed on these books.
      2. THE LOSS IS A SIMULATED REPLAY LOSS, not an account result.
      3. REWARDS AND LOSSES MUST BE COMPARED ON THE SAME MARKETS UNDER
         THE SAME POLICY. Here they are not: the loss is C4 on this
         corpus and the pool is a programme on other markets. A real
         qualification requires both sides measured together.

    It inverts the formula rather than evaluating it, so it needs no
    depth: reward = pool x share x resting-exposure.
    """
    denom = prog.reward_pool * exposure["summed_period_fractions"]
    if denom <= 0:
        return {"required_share": None, "why": "NO_RESTING_TIME"}
    share = loss_usd / denom
    return {"label": "TRANSFERRED SCENARIO -- not a measured hurdle",
            "same_market_same_policy": False,
            "why_not_measured": "the programme terms come from other "
                                "markets, the loss is a simulated replay "
                                "on this corpus, and a qualification "
                                "requires both measured together",
            "pool_per_day": prog.reward_pool,
            "resting_exposure_full_clip_days":
                exposure["summed_period_fractions"],
            "exposure_basis": exposure["basis"],
            "pool_dollars_addressable": round(denom, 4),
            "loss_to_cover_usd": round(loss_usd, 4),
            "required_share": round(share, 6),
            "required_share_pct": round(100.0 * share, 4),
            "feasible_at_all": share <= 1.0,
            "why": "OK" if share <= 1.0 else
                   "EXCEEDS_100_PCT_OF_THE_ADDRESSABLE_POOL"}


def share_at_depth(prog, our_price, our_size, competing, walk=None) -> float:
    """Our share when the rest of the side holds `competing` at our price.

    SCENARIO. The competing size is a parameter here, not an
    observation, and the returned share inherits that status.
    """
    levels = [(our_price, competing)] if competing > 0 else []
    r = inc.snapshot_share(levels, "BID", prog, our_price, our_size,
                           walk or inc.WALK_WHOLE_LEVEL)
    return r["share"] if r["qualifies"] else 0.0


def apply_floor(rows, aggregation):
    """The $1 minimum, applied to the unit the aggregation names.

    WHICH UNIT IS NOT DOCUMENTED. A response grouped by market and date
    does not prove earnings are combined at that grain before the
    minimum bites, so all three candidates are carried and the verdict
    reports whether they disagree.
    """
    if aggregation == A1:
        key = lambda r: (r["slug"], r["date"])          # noqa: E731
    elif aggregation == A2:
        key = lambda r: (r["date"],)                    # noqa: E731
    else:
        key = lambda r: ("PROGRAMME",)                  # noqa: E731
    buckets = collections.defaultdict(float)
    for r in rows:
        buckets[key(r)] += r["reward_gross"]
    paid = sum(v for v in buckets.values() if v >= MIN_PAYOUT)
    dropped = sum(v for v in buckets.values() if v < MIN_PAYOUT)
    return {"aggregation": aggregation, "units": len(buckets),
            "units_paid": sum(1 for v in buckets.values()
                              if v >= MIN_PAYOUT),
            "reward_paid": round(paid, 4),
            "reward_forfeited_to_floor": round(dropped, 4)}


def drawdown(eps):
    """Peak-to-trough on the episode cash series, in entry order."""
    ordered = sorted((e for e in eps if e.get("t0")), key=lambda e: e["t0"])
    run, peak, dd = 0.0, 0.0, 0.0
    for e in ordered:
        run += e.get("total_if_residual_realises", 0.0)
        peak = max(peak, run)
        dd = min(dd, run - peak)
    return round(dd, 4)


def residual(eps):
    """What is still open when the replay ends, kept apart from cash."""
    contracts = sum(abs(e.get("residual_contracts", {})
                        .get("net_directional", 0.0)) for e in eps)
    value = sum(e.get("residual_value", 0.0) for e in eps
                if not e.get("residual_is_cash"))
    unresolved = sum(1 for e in eps
                     if abs(e.get("residual_contracts", {})
                            .get("net_directional", 0.0)) > 1e-9)
    return {"open_episodes": unresolved,
            "net_directional_contracts": round(contracts, 2),
            "residual_value_usd": round(value, 4),
            "note": "residual value is a MARK, not cash. It is reported "
                    "beside net P&L and never inside it."}


def turnover(eps, days):
    """Contracts and notional the replay actually transacted."""
    ctr = sum(sum(abs(x) for x in e.get("fill_sizes") or ()) for e in eps)
    notional = 0.0
    for e in eps:
        for px, sz in zip(e.get("fill_prices") or (),
                          e.get("fill_sizes") or ()):
            try:
                notional += abs(float(px) * float(sz))
            except (TypeError, ValueError):
                pass
    return {"contracts": round(ctr, 2), "notional_usd": round(notional, 2),
            "days": days,
            "notional_per_day": round(notional / days, 2) if days else None}


def main():
    if not prints_mod.tape_dir():
        print("NO TAPE -- refusing to report a tape-backed result without "
              "the tape.")
        return 1

    print("=" * 78)
    print("ECONOMIC VERDICT -- DEVELOPMENT REPLAY (simulated execution)")
    print("These are NOT account results. See the ACTUAL section below.")
    print("=" * 78)

    out = {"verdict": "BETTOR_ECONOMIC_VERDICT_V1",
           "assumptions": {
               "book": "ENTRY BOOK HELD CONSTANT for the episode's life -- "
                       "the corpus has one book per episode, not a ladder "
                       "time series",
               "transfer": "the retrieved programme terms are applied to a "
                           "corpus captured on OTHER markets",
               "execution": "tape-backed fills, queue fraction swept",
               "corpus": "fully consumed -- nothing here is out of sample"},
           "programmes_retrieved_at": "2026-09-22T18:14Z (unauthenticated)",
           "candidates": []}

    # ── the two declared candidates the directive names ──────────────
    wanted = {"C3  inventory-aware", "C4  incentive-aware LP"}
    for name, pol in pf.CANDIDATES:
        if name not in wanted:
            continue
        for q in pf.QFRACS:
            p = epi.dataclasses.replace(pol, queue_ahead_fraction=q)
            eps = epi.run_all(size=CLIP, rebates_on=True, policy=p,
                              use_tape=True)
            s = pf.summarise(eps)
            assert s["snapshot_intervals"] == 0, "snapshot fallback leaked"

            days = len({(e.get("t0") or "")[:10] for e in eps if e.get("t0")})
            row = {"candidate": name, "qfrac": q,
                   "net_usd": s["net_usd"],
                   "capital_hours": s["capital_hours"],
                   "per_capital_hour": s["per_capital_hour"],
                   "drawdown_usd": drawdown(eps),
                   "residual": residual(eps),
                   "turnover": turnover(eps, days),
                   "episodes": s["episodes"], "any_fill": s["any_fill"]}

            if name.startswith("C4"):
                row["incentive"] = _incentive_block(eps, s)
            out["candidates"].append(row)

    _report(out)
    with open(OUT, "w") as fh:
        json.dump(out, fh, indent=2, default=str)
    print("\nwritten: %s" % OUT)
    return 0


def _incentive_block(eps, s):
    """C4 against the retrieved terms -- and what the corpus cannot say.

    Three separate statements, never merged:
      1. whether the reward is COMPUTABLE from this corpus (it is not,
         and why);
      2. the REQUIRED SHARE, which inverts the formula and needs no
         depth;
      3. a labelled SENSITIVITY over competing depth, which is a
         scenario and says so.
    """
    depth = corpus_supplies_depth(eps)
    exposure = period_exposure(eps)
    loss = -s["net_usd"]
    block = {"computable": depth["depth_available"],
             "why_not": None if depth["depth_available"] else NOT_COMPUTABLE,
             # THE QUESTION THIS BLOCK USED TO GET WRONG, settled.
             # It reported the reward as NOT COMPUTABLE FROM THIS
             # CORPUS on the strength of one summary field, and that
             # was a statement about the field rather than about the
             # capture. The ladder is in every tape row and now travels
             # with the episode, so the walk can be performed.
             #
             # What remains absent is a PROGRAMME, which is a different
             # kind of absence and produces a different answer: a
             # reward of ZERO, which is a finding.
             "scope_of_the_absence": {
                 "depth": "PRESENT. entry_book now carries bid_depth, "
                          "ask_depth and a bounded ladder; the earlier "
                          "'no sizes in this corpus' claim was about "
                          "the summary field, not the capture",
                 "programme": NOT_APPLICABLE,
                 "consequence": "the reward for these five sports "
                                "markets is ZERO because no programme "
                                "covered them -- not unknown. Every "
                                "share figure quoted for them is "
                                "TRANSFERRED from the culture / crypto "
                                "/ eFootball programmes, which cover "
                                "different instruments."},
             "corpus_depth_check": depth,
             "period_exposure": exposure,
             "programmes": {}}
    if not depth["depth_available"]:
        block["detail"] = (
            "entry_book carries %s. No ladder reached this calculation, "
            "so no share can be formed. Re-run the replay: the ladder is "
            "in the tape and is emitted onto the episode record."
            % ", ".join(depth["entry_book_keys"]))
    else:
        block["detail"] = (
            "depth IS available -- %d of %d episodes carry a ladder, "
            "median %s bid levels. The reward is nevertheless ZERO for "
            "these markets, because %s."
            % (depth["episodes_with_ladder"], depth["episodes"],
               depth["median_bid_levels"], NOT_APPLICABLE))

    for pname, prog in PROGRAMMES.items():
        req = required_share(loss, prog, exposure)
        sens = []
        # The clip rests at the touch, so our price is the best price;
        # only the competing size at that level is unknown.
        for c in COMPETING_DEPTHS:
            sh = share_at_depth(prog, 0.50, CLIP, c)
            reward = (prog.reward_pool * sh
                      * exposure["summed_period_fractions"])
            per_unit = (reward / exposure["market_days"]
                        if exposure["market_days"] else 0.0)
            sens.append({
                "competing_size_at_our_price": c,
                "our_share": round(sh, 6),
                "reward_usd_if_that_held_throughout": round(reward, 4),
                "covers_loss": reward >= loss,
                "mean_per_market_day": round(per_unit, 4),
                "clears_1_dollar_floor_per_market_day":
                    per_unit >= MIN_PAYOUT,
                "label": "SCENARIO -- competing depth is a parameter, "
                         "not an observation"})
        block["programmes"][pname] = {
            "pool_per_day": prog.reward_pool,
            "discount_factor": prog.discount_factor,
            "target_size": prog.target_size,
            "required_to_break_even": req,
            "sensitivity_over_competing_depth": sens,
        }
    return block


def _report(out):
    print()
    print("%-22s %5s %9s %9s %10s %9s %11s" % (
        "candidate", "qfrac", "net$", "drawdn$", "cap_hrs", "resid_ct",
        "per_cap_hr"))
    print("-" * 82)
    for r in out["candidates"]:
        print("%-22s %5.2f %9.2f %9.2f %10.1f %9.1f %11s" % (
            r["candidate"], r["qfrac"], r["net_usd"], r["drawdown_usd"],
            r["capital_hours"],
            r["residual"]["net_directional_contracts"],
            ("%.6f" % r["per_capital_hour"])
            if r["per_capital_hour"] is not None else "-"))

    print()
    print("=" * 78)
    print("C4 AGAINST THE RETRIEVED PROGRAMME TERMS")
    print("=" * 78)
    for r in out["candidates"]:
        if "incentive" not in r:
            continue
        b = r["incentive"]
        print("\nqfrac %.2f   trading net $%.2f   capital-hours %.1f"
              % (r["qfrac"], r["net_usd"], r["capital_hours"]))
        if not b["computable"]:
            print("  REWARD NOT COMPUTABLE FROM THIS CORPUS (%s)"
                  % b["why_not"])
            print("  %s" % b["detail"])
        ex = b["period_exposure"]
        print("  resting exposure: %.4f summed period-fractions over %d "
              "market-days (mean %.4f of a day each)"
              % (ex["summed_period_fractions"], ex["market_days"],
                 ex["mean_fraction_per_market_day"] or 0.0))
        for pname, p in b["programmes"].items():
            q = p["required_to_break_even"]
            print("  %-16s pool $%-5.0f -> addressable $%8.4f; "
                  "REQUIRED SHARE %.2f%%  %s"
                  % (pname, p["pool_per_day"], q["pool_dollars_addressable"],
                     q["required_share_pct"],
                     "" if q["feasible_at_all"] else "** IMPOSSIBLE **"))
            print("      scenario: competing size at our price -> share -> "
                  "reward (covers? / clears $1/market-day?)")
            for sv in p["sensitivity_over_competing_depth"]:
                print("        %7.0f  share %7.4f  $%8.4f   %-7s %s"
                      % (sv["competing_size_at_our_price"], sv["our_share"],
                         sv["reward_usd_if_that_held_throughout"],
                         "COVERS" if sv["covers_loss"] else "no",
                         "floor-ok" if
                         sv["clears_1_dollar_floor_per_market_day"]
                         else "below-$1"))


if __name__ == "__main__":
    sys.exit(main())
