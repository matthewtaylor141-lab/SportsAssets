"""DETECTABLE EDGE AND REQUIRED COVERAGE -- computed, not asserted.

"500 episodes" was a proposed cap. It was never a power calculation,
and a cap is not evidence that a design can see anything. This file
computes what the design can actually detect, from the dispersion the
episodes themselves exhibit.

THE UNIT IS THE EVENT, NOT THE EPISODE. Episodes inside one event
share a book, a counterparty population and a news process. The
episode run shows the cost of getting this wrong: the naive standard
error is 48x smaller than the cluster standard error on the same
numbers. A design powered on episodes is powered on a denominator that
does not exist.

WHAT IS COMPUTED

  sigma_event   the standard deviation ACROSS event-level mean net
                cash per contract, measured from the episode run
  n_required    events needed to detect a given true edge at 80% power,
                two-sided, with a Bonferroni correction for the number
                of variants actually tried
  mde           the minimum detectable edge at a given number of events

Two policy shapes are costed, because they have very different
dispersion:

  AS RUN            inventory may carry to settlement. One carried
                    episode swings +-0.40 per contract.
  HARD FLATTEN      inventory is always crossed out before expiry.
                    The settlement tail is removed by construction,
                    at the cost of paying the taker fee every time.

Run:  python research/beta48/bettor_power.py
"""
from __future__ import annotations

import collections
import json
import math
import os
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bettor_episodes as epi                                   # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "acceptance", "power.json")

# Variants ACTUALLY TRIED in this programme, counted so the correction
# is not invented after the fact:
#   4  policy grid cells (rebates on/off x two queue models)
#   8  sensitivity cells (4 horizons x 2 sizes)
#   1  the maker/taker round trip
#   1  the maker-entry/taker-completion sequential policy
#   1  the taker complementary pair
#   1  hold-to-settlement (refused, but tried)
#   1  the copy policy
VARIANTS_TRIED = 4 + 8 + 5

Z80 = 0.8416                      # one-sided z for 80% power


def z_two_sided(alpha):
    """Inverse normal CDF at 1 - alpha/2, via Acklam's rational fit.

    Good to ~1e-9 over the range used here, and avoids a SciPy
    dependency that this repository does not carry.
    """
    p = 1.0 - alpha / 2.0
    a = [-3.969683028665376e+01, 2.209460984245205e+02,
         -2.759285104469687e+02, 1.383577518672690e+02,
         -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02,
         -1.556989798598866e+02, 6.680131188771972e+01,
         -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01,
         -2.400758277161838e+00, -2.549732539343734e+00,
         4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01,
         2.445134137142996e+00, 3.754408661907416e+00]
    pl, ph = 0.02425, 1 - 0.02425
    if p < pl:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > ph:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def event_means(eps, size, flatten_tail=False):
    """Per-EVENT mean net cash per contract.

    `flatten_tail` recosts the carried episodes as if the policy had
    crossed out before expiry: the settlement payoff is replaced by
    the realised cash plus a conservative flat exit at the observed
    touch, which is what `FLAT_EXITED_TAKER` episodes already record.
    Rather than re-simulate, this substitutes the MEDIAN taker-exit
    result for each carried episode -- an approximation, and labelled
    one.
    """
    taker = [e["total_if_residual_realises"] for e in eps
             if e["status"] == epi.FLAT_EXITED_TAKER]
    sub = st.median(taker) if taker else 0.0
    by = collections.defaultdict(list)
    for e in eps:
        v = e["total_if_residual_realises"]
        if flatten_tail and e["status"] in (epi.CARRIED_SETTLED,
                                            epi.CARRIED_OPEN):
            v = sub
        by[e["event"]].append(v / size)
    return {k: st.fmean(v) for k, v in by.items()}, len(eps)


def design(sigma, alpha, edges, events_available):
    z = z_two_sided(alpha)
    rows = []
    for d in edges:
        n = ((z + Z80) * sigma / d) ** 2
        rows.append({"edge_per_contract": d,
                     "events_required_80pct_power": math.ceil(n)})
    mde = (z + Z80) * sigma / math.sqrt(events_available)
    return {"alpha": alpha, "z": round(z, 4), "sigma_event": round(sigma, 6),
            "rows": rows,
            "events_in_hand": events_available,
            "mde_at_events_in_hand": round(mde, 6)}


def main():
    size = 100.0
    eps = epi.run_all(size=size, rebates_on=True,
                      queue_model="QUEUE_FRONT_IF_INSIDE")
    res = {"variants_tried": VARIANTS_TRIED,
           "bonferroni_alpha": round(0.05 / VARIANTS_TRIED, 6),
           "episodes": len(eps)}

    edges = [0.0005, 0.001, 0.0025, 0.005, 0.01, 0.02]
    print("=" * 74)
    print("DETECTABLE EDGE AND REQUIRED COVERAGE")
    print("=" * 74)
    print("variants actually tried in this programme: %d" % VARIANTS_TRIED)
    print("Bonferroni alpha: 0.05 / %d = %.6f" % (
        VARIANTS_TRIED, 0.05 / VARIANTS_TRIED))

    for label, flatten in (("AS RUN (inventory may carry)", False),
                           ("HARD FLATTEN (never carry to expiry)", True)):
        means, n_eps = event_means(eps, size, flatten)
        vals = list(means.values())
        sigma = st.stdev(vals) if len(vals) > 1 else float("nan")
        k = len(vals)
        print()
        print("-" * 74)
        print("%s" % label)
        print("  events observed              %d" % k)
        print("  per-event mean, per contract %s" % (
            "  ".join("%+0.4f" % v for v in sorted(vals))))
        print("  sigma across events          %0.6f" % sigma)
        block = {}
        for alpha, aname in ((0.05, "uncorrected"),
                             (0.05 / VARIANTS_TRIED, "Bonferroni")):
            d = design(sigma, alpha, edges, k)
            block[aname] = d
            print("  alpha=%.6f (%s)  MDE at %d events: %+0.6f/contract"
                  % (alpha, aname, k, d["mde_at_events_in_hand"]))
            for r in d["rows"]:
                print("      to detect %+0.4f/contract: %s events" % (
                    r["edge_per_contract"],
                    "{:,}".format(r["events_required_80pct_power"])))
        res["AS_RUN" if not flatten else "HARD_FLATTEN"] = {
            "events": k, "sigma_event": round(sigma, 6),
            "event_means": [round(v, 6) for v in sorted(vals)],
            "designs": block}

    print()
    print("=" * 74)
    print("WHAT THIS MEANS FOR A 500-EPISODE CAP")
    print("=" * 74)
    per_event = len(eps) / max(1, len(set(e["event"] for e in eps)))
    print("  this corpus averaged %.0f episodes per event" % per_event)
    print("  500 episodes at that rate is about %.1f EVENTS" % (
        500 / per_event))
    sig = res["AS_RUN"]["sigma_event"]
    z = z_two_sided(0.05 / VARIANTS_TRIED)
    mde500 = (z + Z80) * sig / math.sqrt(max(1.0, 500 / per_event))
    print("  MDE at that coverage, Bonferroni-corrected: %+0.4f/contract"
          % mde500)
    print("  the edge being hunted is ONE HALF-SPREAD: +0.0025/contract")
    print("  ratio: the design would need the true edge to be %.0fx the"
          % (mde500 / 0.0025))
    print("  half-spread before it could see it at 80%% power.")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(res, f, indent=2, sort_keys=True)
    print("\nwritten: %s" % os.path.relpath(OUT))


if __name__ == "__main__":
    main()
