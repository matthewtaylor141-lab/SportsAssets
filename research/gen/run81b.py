#!/usr/bin/env python3
"""RUN 81B -- THE EVENT-LEVEL REPRICING COUNTERFACTUAL.

    python3 research/gen/run81b.py

NO DATABASE CONNECTION. That is a property of the program, not a rule someone
has to keep: this script opens the three committed sealed artifacts and nothing
else, and there is no live table in scope to fall back to.

THE ONE QUESTION 81B ANSWERS
    For the exact settlement-analyzable U2 BUY events, what realized settlement
    P&L corresponds to RN1's source fill price, and what would that SAME
    quantity's realized settlement P&L have been if entry occurred instead at
    the first retained observable ask?

IT IS NOT: a reconstruction of RN1's complete strategy; a matched-book
reconstruction; expected value; alpha; arbitrage edge. It is an EVENT-LEVEL
REPRICING COUNTERFACTUAL and the names it prints say so.

WHAT IS NEVER DONE HERE
  * No matched-book economics. FULL_RN1_MATCHED_BOOK_RECONSTRUCTION is NOT
    IDENTIFIABLE FROM CURRENT SEALED INPUTS and is reported that way.
  * No new U0 price/size artifact is drawn or implied.
  * No extrapolation or imputation over exhausted depth. A row whose retained
    book cannot cover q is FULL_Q_EXECUTION_PNL = NOT IDENTIFIED, full stop --
    it is not an upper bound, not a lower bound, and never folded into an
    aggregate as if known.
  * No fee or rebate adjustment. FEE_AND_REBATE_ADJUSTED_NET_ECONOMICS = NOT
    IDENTIFIED, and gross is NOT called a bound in either direction.
  * No latency claim. Deterioration measured at the first retained observation
    does NOT establish that physical latency caused it.

The scenario definitions, the eligibility ladder and the depth walk are carried
verbatim from research/gen/run81a_s.py, which carried them verbatim from
research/rn1_run81a_drag.sql. Arithmetic is IEEE double there and here, so the
two runs differ by population and not by numeric model.
"""
import hashlib
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run81a_s import walk  # noqa: E402  -- the depth walk, one definition only
from run81b_population_gate import (  # noqa: E402
    SealedInputs, control_checks, population_controls, verify_sealed_hashes)

# Closure threshold. These are IEEE doubles; the identities below are exact in
# real arithmetic, so any residual is rounding. The threshold is absolute and
# the MAXIMUM OBSERVED RESIDUAL is printed beside every count, so a pass is
# never just a threshold that was set generously enough to produce one.
CLOSURE_THRESHOLD = 1e-6
PAYOUT_TOL = 1e-9

# The selection disclosure. Sources: research/snapshots/manifest_v1.txt and
# research/REACHABILITY_LADDER.md section F.
U2_EVENTS, U2_NOTIONAL = 214609, 48327293.76
UNRESOLVED_EVENTS, UNRESOLVED_NOTIONAL = 78587, 19910944.73

SELECTION_WORDING = (
    "This population is settlement-selected. It represents the subset of\n"
    "U2_SNAPSHOT_V1 that was linkable, structurally eligible, resolved in the\n"
    "sealed settlement metadata state, passed the stronger timing quarantine,\n"
    "and had unambiguous payout mapping. It is not established as\n"
    "representative of all U2 events or all RN1 activity.")

SCENARIOS = ("Q_A", "Q_B", "Q_C")
SCENARIO_ROLE = {"Q_A": "PRIMARY -- 10% of RN1 source fill shares",
                 "Q_B": "secondary -- RN1 source fill shares",
                 "Q_C": "STRESS ONLY -- 1000 / p_h shares; NOT realistic "
                        "BETTOR sizing"}

PRICE_BANDS = [(0.00, 0.05), (0.05, 0.10), (0.10, 0.25), (0.25, 0.50),
               (0.50, 0.75), (0.75, 0.90), (0.90, 0.95), (0.95, 1.00)]
SIZE_BANDS = [(0, 100), (100, 1000), (1000, 10000), (10000, 100000),
              (100000, float("inf"))]


def band(x, bands, fmt):
    for lo, hi in bands:
        if lo <= x < hi:
            return fmt(lo, hi)
    return "(out of band)"


def px_band(p):
    return band(p, PRICE_BANDS, lambda lo, hi: f"[{lo:.2f},{hi:.2f})")


def sz_band(s):
    def f(lo, hi):
        return f"[{lo:,.0f},inf)" if hi == float("inf") else f"[{lo:,.0f},{hi:,.0f})"
    return band(s, SIZE_BANDS, f)


def ratio(num, den):
    return None if not den else num / den


def fmt_r(x, dp=4):
    return "        n/a" if x is None else f"{x:>{7 + dp}.{dp}f}"


class TopAcc:
    """BRIDGE 1 -- source fill against the first retained TOP OF BOOK."""

    __slots__ = ("n", "conds", "q", "cost_src", "cost_fot", "pnl_src",
                 "pnl_fot", "det", "pos_det", "max_resid", "viol", "sup")

    def __init__(self):
        self.n = 0
        self.conds = set()
        self.q = self.cost_src = self.cost_fot = 0.0
        self.pnl_src = self.pnl_fot = self.det = 0.0
        self.pos_det = 0
        self.sup = 0            # rows fully depth-supported at this q
        self.max_resid = 0.0
        self.viol = 0

    def add(self, cond, q, ph, ask, S, supported):
        pnl_src = q * (S - ph)
        pnl_fot = q * (S - ask)
        det = q * (ask - ph)
        # THE BRIDGE 1 REQUIREMENT, checked on every single event rather than
        # trusted from the algebra: the difference of the two counterfactuals
        # must BE the deterioration.
        resid = abs((pnl_src - pnl_fot) - det)
        self.max_resid = max(self.max_resid, resid)
        if resid > CLOSURE_THRESHOLD:
            self.viol += 1
        self.n += 1
        self.conds.add(cond)
        self.q += q
        self.cost_src += q * ph
        self.cost_fot += q * ask
        self.pnl_src += pnl_src
        self.pnl_fot += pnl_fot
        self.det += det
        if det > 0:
            self.pos_det += 1
        if supported:
            self.sup += 1


class DepthAcc:
    """BRIDGE 2 -- source fill against the DEPTH-AWARE first observation."""

    __slots__ = ("n", "conds", "q", "cost_src", "cost_fod", "pnl_src",
                 "pnl_fod", "top", "dep", "total", "max_resid_total",
                 "max_resid_decomp", "viol_total", "viol_decomp")

    def __init__(self):
        self.n = 0
        self.conds = set()
        self.q = self.cost_src = self.cost_fod = 0.0
        self.pnl_src = self.pnl_fod = 0.0
        self.top = self.dep = self.total = 0.0
        self.max_resid_total = self.max_resid_decomp = 0.0
        self.viol_total = self.viol_decomp = 0

    def add(self, cond, q, ph, ask, S, vwap):
        pnl_src = q * (S - ph)
        pnl_fod = q * (S - vwap)
        total = q * (vwap - ph)
        top = q * (ask - ph)
        dep = q * (vwap - ask)
        r_total = abs((pnl_src - pnl_fod) - total)
        r_decomp = abs((top + dep) - total)
        self.max_resid_total = max(self.max_resid_total, r_total)
        self.max_resid_decomp = max(self.max_resid_decomp, r_decomp)
        if r_total > CLOSURE_THRESHOLD:
            self.viol_total += 1
        if r_decomp > CLOSURE_THRESHOLD:
            self.viol_decomp += 1
        self.n += 1
        self.conds.add(cond)
        self.q += q
        self.cost_src += q * ph
        self.cost_fod += q * vwap
        self.pnl_src += pnl_src
        self.pnl_fod += pnl_fod
        self.top += top
        self.dep += dep
        self.total += total


def money(x):
    return f"{x:>18,.2f}"


def line(label, value):
    print(f"  {label:<52}{value}")


def main():
    print("=" * 74)
    print("RUN 81B -- EVENT-LEVEL REPRICING COUNTERFACTUAL")
    print("on SETTLEMENT_ANALYZABLE_STRONG, from sealed bytes only")
    print("=" * 74)
    print("No database. No matched-book economics. No fee adjustment.")
    print("No latency claim. mirror_live=false. ai_trades / TRUEEDGE untouched.\n")

    # ---------------------------------------------------------- HALT GATES
    print("== HALT GATE 1 -- SEALED HASH IDENTITY ==")
    hashes = verify_sealed_hashes()
    for name, exp, got, nlines, ok in hashes:
        print(f"  {name:<24}{nlines:>10,} lines  "
              f"{'MATCH' if ok else '*** MISMATCH ***'}")
        if not ok:
            print(f"      expected {exp}\n      got      {got}")
    if not all(ok for *_, ok in hashes):
        print("\n== HALT -- sealed hash failure ==")
        return 1
    print()

    sealed = SealedInputs()
    n_pop, conds_pop, notional, sides, sources, u2_by_cond = \
        population_controls(sealed)
    print("== HALT GATE 2 -- POPULATION CONTROLS ==")
    checks = control_checks(n_pop, conds_pop, notional)
    for nm, got, exp in checks:
        g = f"{got:,.2f}" if not isinstance(got, int) else f"{got:,}"
        e = f"{exp:,.2f}" if not isinstance(exp, int) else f"{exp:,}"
        print(f"  {nm:<18}{g:>18}   expected {e:>18}   "
              f"{'MATCH' if got == exp else '*** MISMATCH ***'}")
    if any(got != exp for _, got, exp in checks):
        print("\n== HALT -- population-control failure ==")
        return 1
    print()

    # ---------------------------------------------------------- derive rows
    rows = []
    payout_odd = 0
    seen_S = defaultdict(int)
    probe_ids = []
    for r, S in sealed.population():
        probe_ids.append(int(r["probe_id"]))
        if abs(S - 1.0) > PAYOUT_TOL and abs(S) > PAYOUT_TOL:
            payout_odd += 1
        seen_S[round(S, 9)] += 1
        ph = float(r["price"])
        size = float(r["size"])
        ask = r["best_ask"]
        ask = float(ask) if ask is not None else None
        rows.append({
            "cond": r["condition_id_effective"],
            "S": S,
            "ph": ph,
            "size": size,
            "ask": ask,
            "ask_valid": ask is not None and 0 < ask <= 1,
            # q definability, carried verbatim from 81A-S
            "q_ok": 0 < ph < 1 and size > 0,
            "levels": [(float(p), float(s)) for p, s in (r["depth"] or [])
                       if p is not None and s is not None],
            "source": r["source"],
            "sport": r["sport"] or "(blank)",
        })

    print("== HALT GATE 3 -- PAYOUT UNAMBIGUITY ON THE COHORT ==")
    print(f"  distinct sealed payout values for the purchased outcome: "
          f"{len(seen_S)}")
    for v in sorted(seen_S):
        print(f"    S = {v:<12.9g}{seen_S[v]:>12,} events")
    print(f"  events whose S is neither 0 nor 1 within {PAYOUT_TOL:g}: "
          f"{payout_odd:,}")
    if payout_odd:
        print("\n== HALT -- payout ambiguity ==")
        return 1
    print("  Every event's purchased outcome carries a sealed terminal payout")
    print("  of exactly 0 or exactly 1. STRUCTURAL WELL-FORMEDNESS IS NOT")
    print("  EXTERNAL CORRECTNESS -- see the PAYOUT LIMITATION below.\n")

    # THE EVENT IDENTITY DIGEST. Matching aggregate controls do not prove two
    # programs read the same events; this does. The independent validator
    # recomputes it, so an agreement on the money is an agreement about the
    # same rows and not a coincidence of totals.
    digest = hashlib.sha256(
        ",".join(str(p) for p in sorted(probe_ids)).encode("ascii")).hexdigest()
    print(f"== EVENT_IDENTITY_DIGEST (sha256 over sorted probe_ids) ==\n"
          f"  {digest}\n")

    # ------------------------------------------------ scenario eligibility
    print("== ELIGIBILITY LADDER AND EXPLICIT EXCLUSIONS ==")
    no_ask = sum(1 for r in rows if not r["ask_valid"])
    no_q = sum(1 for r in rows if not r["q_ok"])
    neither = sum(1 for r in rows if not r["ask_valid"] and not r["q_ok"])
    elig = [r for r in rows if r["ask_valid"] and r["q_ok"]]
    line("population events", f"{len(rows):>18,}")
    line("excluded: no valid retained best ask (0 < ask <= 1)", f"{no_ask:>18,}")
    line("excluded: q not definable (needs 0 < p_h < 1, size > 0)",
         f"{no_q:>18,}")
    line("  of which excluded on both grounds", f"{neither:>18,}")
    line("ELIGIBLE for the bridges", f"{len(elig):>18,}")
    line("eligible share of the population",
         f"{100.0 * len(elig) / len(rows):>17.3f}%")
    print()

    # --------------------------------------------------- accumulate bridges
    qfun = {"Q_A": lambda r: 0.10 * r["size"],
            "Q_B": lambda r: r["size"],
            "Q_C": lambda r: 1000.0 / r["ph"]}

    top = {s: TopAcc() for s in SCENARIOS}
    dep = {s: DepthAcc() for s in SCENARIOS}
    exhausted = {s: 0 for s in SCENARIOS}
    # Segment accumulators, Q_A only (the primary scenario).
    seg_top = defaultdict(lambda: defaultdict(TopAcc))
    seg_dep = defaultdict(lambda: defaultdict(DepthAcc))
    # Win / loss split, Q_A.
    wl_top = {1: TopAcc(), 0: TopAcc()}
    wl_dep = {1: DepthAcc(), 0: DepthAcc()}

    for r in elig:
        keys = (("source lane", r["source"]), ("sport", r["sport"]),
                ("source price band", px_band(r["ph"])),
                ("RN1 source fill-size band", sz_band(r["size"])))
        for sc in SCENARIOS:
            q = qfun[sc](r)
            if q <= 0:
                continue
            total_sh, cost = walk(r["levels"], q)
            supported = cost is not None
            if not supported:
                exhausted[sc] += 1
            top[sc].add(r["cond"], q, r["ph"], r["ask"], r["S"], supported)
            if supported:
                vwap = cost / q
                dep[sc].add(r["cond"], q, r["ph"], r["ask"], r["S"], vwap)
            if sc != "Q_A":
                continue
            w = 1 if r["S"] > 0.5 else 0
            wl_top[w].add(r["cond"], q, r["ph"], r["ask"], r["S"], supported)
            for dim, key in keys:
                seg_top[dim][key].add(r["cond"], q, r["ph"], r["ask"], r["S"],
                                      supported)
            if supported:
                vwap = cost / q
                wl_dep[w].add(r["cond"], q, r["ph"], r["ask"], r["S"], vwap)
                for dim, key in keys:
                    seg_dep[dim][key].add(r["cond"], q, r["ph"], r["ask"],
                                          r["S"], vwap)

    # ------------------------------------------------- HALT GATES 4 AND 5
    print("== HALT GATE 4 -- BRIDGE 1 ARITHMETIC CLOSURE, PER EVENT ==")
    print("   required: (SOURCE_CF_PNL - FIRST_OBSERVED_TOP_CF_PNL) "
          "== q*(best_ask - p_h)")
    print(f"{'scenario':10}{'witnesses':>12}{'violations':>12}"
          f"{'max residual':>18}{'verdict':>16}")
    for sc in SCENARIOS:
        a = top[sc]
        v = "NOT TESTED" if a.n == 0 else ("PASS" if a.viol == 0 else "FAIL")
        print(f"{sc:10}{a.n:>12,}{a.viol:>12,}{a.max_resid:>18.3e}{v:>16}")
    print()

    print("== HALT GATE 5 -- BRIDGE 2 CLOSURE AND DEPTH DECOMPOSITION ==")
    print("   required: (SOURCE_CF_PNL - FIRST_OBSERVED_DEPTH_CF_PNL) "
          "== q*(depth_vwap - p_h)")
    print("   required: TOP_COMPONENT + DEPTH_COMPONENT "
          "== TOTAL_OBSERVED_PNL_DETERIORATION")
    print(f"{'scenario':10}{'witnesses':>12}{'viol total':>12}"
          f"{'viol decomp':>13}{'max r total':>15}{'max r decomp':>15}"
          f"{'verdict':>12}")
    for sc in SCENARIOS:
        a = dep[sc]
        v = ("NOT TESTED" if a.n == 0
             else ("PASS" if a.viol_total == 0 and a.viol_decomp == 0
                   else "FAIL"))
        print(f"{sc:10}{a.n:>12,}{a.viol_total:>12,}{a.viol_decomp:>13,}"
              f"{a.max_resid_total:>15.3e}{a.max_resid_decomp:>15.3e}{v:>12}")
    bad = [sc for sc in SCENARIOS
           if top[sc].viol or dep[sc].viol_total or dep[sc].viol_decomp]
    if bad:
        print(f"\n== HALT -- arithmetic closure failure on {bad} ==")
        return 1
    print("\n  A zero violation count is reported only beside a non-zero")
    print("  witness count; a zero-witness cell reads NOT TESTED, never PASS.\n")

    # --------------------------------------------- SELECTION DISCLOSURE
    print("=" * 74)
    print("SELECTION DISCLOSURE -- CARRIED BESIDE EVERY FIGURE BELOW")
    print("=" * 74)
    print(f"  U2_SNAPSHOT_V1                  {U2_EVENTS:>10,} events   "
          f"${U2_NOTIONAL:>16,.2f} source notional")
    print(f"  SETTLEMENT_ANALYZABLE_STRONG    {n_pop:>10,} events   "
          f"${float(notional):>16,.2f} source notional")
    print(f"  {'':34}{len(conds_pop):>10,} conditions")
    print(f"  UNRESOLVED first-fail           {UNRESOLVED_EVENTS:>10,} events   "
          f"${UNRESOLVED_NOTIONAL:>16,.2f}")
    print()
    print(SELECTION_WORDING)
    print()

    # --------------------------------------------------------- VIEW A
    print("=" * 74)
    print("VIEW A -- BRIDGE 1, TOP OF BOOK, ALL VALID EVENTS -- Q_A (PRIMARY)")
    print("=" * 74)
    print(f"  scenario role: {SCENARIO_ROLE['Q_A']}\n")
    view_a(top["Q_A"])

    # --------------------------------------------------------- VIEW B
    print("=" * 74)
    print("VIEW B -- BRIDGE 2, DEPTH_SUPPORTED_SUBSET -- Q_A (PRIMARY)")
    print("=" * 74)
    view_b(top["Q_A"], dep["Q_A"], exhausted["Q_A"])

    # ----------------------------------------------------- Q_B and Q_C
    for sc in ("Q_B", "Q_C"):
        print("=" * 74)
        print(f"SECONDARY SCENARIO {sc} -- {SCENARIO_ROLE[sc]}")
        print("=" * 74)
        print("-- top of book, all valid events --")
        view_a(top[sc])
        print("-- depth-supported subset --")
        view_b(top[sc], dep[sc], exhausted[sc])
    print("Q_C is a STRESS SCENARIO. It is not realistic BETTOR sizing and it")
    print("does not drive any conclusion below.\n")

    # ---------------------------------------------------- WIN / LOSS SPLIT
    print("=" * 74)
    print("WIN / LOSS SPLIT AT Q_A -- DESCRIPTIVE ONLY")
    print("=" * 74)
    print("Settlement-realized counterfactual P&L depends on S, so the split is")
    print("reported. Deterioration itself does NOT depend on S -- it is")
    print("q*(best_ask - p_h) -- so its split is a split of where the events")
    print("landed, not evidence that deterioration behaves differently on")
    print("winners and losers.\n")
    print("-- BRIDGE 1, top of book --")
    print(f"{'S':>4}{'events':>12}{'scen src cost':>18}{'source CF P&L':>18}"
          f"{'first-obs CF P&L':>19}{'deterioration':>17}")
    comb = TopAcc()
    for w in (1, 0):
        a = wl_top[w]
        print(f"{w:>4}{a.n:>12,}{money(a.cost_src)}{money(a.pnl_src)}"
              f"{a.pnl_fot:>19,.2f}{a.det:>17,.2f}")
        comb.n += a.n
        comb.cost_src += a.cost_src
        comb.pnl_src += a.pnl_src
        comb.pnl_fot += a.pnl_fot
        comb.det += a.det
    print(f"{'ALL':>4}{comb.n:>12,}{money(comb.cost_src)}{money(comb.pnl_src)}"
          f"{comb.pnl_fot:>19,.2f}{comb.det:>17,.2f}")
    print()
    print("-- BRIDGE 2, depth-supported subset --")
    print(f"{'S':>4}{'events':>12}{'scen src cost':>18}{'source CF P&L':>18}"
          f"{'first-obs CF P&L':>19}{'deterioration':>17}")
    cd = DepthAcc()
    for w in (1, 0):
        a = wl_dep[w]
        print(f"{w:>4}{a.n:>12,}{money(a.cost_src)}{money(a.pnl_src)}"
              f"{a.pnl_fod:>19,.2f}{a.total:>17,.2f}")
        cd.n += a.n
        cd.cost_src += a.cost_src
        cd.pnl_src += a.pnl_src
        cd.pnl_fod += a.pnl_fod
        cd.total += a.total
    print(f"{'ALL':>4}{cd.n:>12,}{money(cd.cost_src)}{money(cd.pnl_src)}"
          f"{cd.pnl_fod:>19,.2f}{cd.total:>17,.2f}")
    print()

    # -------------------------------------------------------- SEGMENTATION
    print("=" * 74)
    print("SEGMENTATION AT Q_A -- DESCRIPTIVE ONLY, NEVER CAUSAL")
    print("=" * 74)
    print("Segments differ in composition as well as in execution. Nothing")
    print("here identifies a cause, and no segment difference is attributed to")
    print("any mechanism.\n")
    for dim in ("source lane", "sport", "source price band",
                "RN1 source fill-size band"):
        segment_block(dim, seg_top[dim], seg_dep[dim])

    # ------------------------------------------------------ MATCHED REGISTER
    print("=" * 74)
    print("MATCHED REGISTER -- NOT COMPUTED")
    print("=" * 74)
    u0_ev = sum(sealed.u0_by_cond[c] for c in conds_pop)
    print("FULL_RN1_MATCHED_BOOK_RECONSTRUCTION =")
    print("    NOT IDENTIFIABLE FROM CURRENT SEALED INPUTS")
    print()
    print(f"Reason: U2 contains {n_pop:,} of {u0_ev:,} U0 events on these")
    print(f"conditions ({100.0 * n_pop / u0_ev:.3f}%), and the sealed U0 timing")
    print("witness lacks price and size. Therefore complete RN1 condition-level")
    print("acquisition pools cannot be reconstructed from the current sealed")
    print("inputs. No matched-book economics are computed in 81B.\n")

    # --------------------------------------------------------- LIMITATIONS
    print("=" * 74)
    print("PERMANENT LIMITATIONS")
    print("=" * 74)
    print("FEE_AND_REBATE_ADJUSTED_NET_ECONOMICS = NOT IDENTIFIED.")
    print("All figures are BEFORE UNIDENTIFIED FEES / REBATES / REWARDS. Gross")
    print("P&L is NOT called an upper bound and NOT called a lower bound. No")
    print("historical fee formula is imported.\n")
    print("PAYOUT LIMITATION: the sealed payout vectors are structurally")
    print("well-formed and map unambiguously on this cohort. Their independent")
    print("external correctness has not been established.\n")
    print("DEPTH LIMITATION: on DEPTH_EXHAUSTED rows FULL_Q_EXECUTION_PNL =")
    print("NOT IDENTIFIED. No extrapolation. No imputation. Not a bound.\n")
    print("This does NOT establish that physical latency caused the")
    print("deterioration. 81B measures the first RETAINED observation, which is")
    print("an artifact of what was probed and stored, not a measured clock.\n")

    answers(top, dep, exhausted, n_pop, len(conds_pop), float(notional))
    return 0


def view_a(a):
    line("events", f"{a.n:>18,}")
    line("conditions", f"{len(a.conds):>18,}")
    line("scenario requested shares", f"{a.q:>18,.4f}")
    line("scenario source acquisition cost  (sum q*p_h)", money(a.cost_src))
    line("first-observed-top acquisition cost (sum q*ask)", money(a.cost_fot))
    line("SETTLEMENT_REALIZED_SOURCE_COUNTERFACTUAL_PNL", money(a.pnl_src))
    line("FIRST_RETAINED_OBSERVATION_COUNTERFACTUAL_PNL", money(a.pnl_fot))
    line("TOP_OF_BOOK_PNL_DETERIORATION", money(a.det))
    line("source counterfactual ROI  (on source cost)",
         fmt_r(ratio(a.pnl_src, a.cost_src)))
    line("first-observed-top counterfactual ROI  (on its own cost)",
         fmt_r(ratio(a.pnl_fot, a.cost_fot)))
    line("  same, on the source cost basis for a like-for-like read",
         fmt_r(ratio(a.pnl_fot, a.cost_src)))
    d = ratio(a.det, a.cost_src)
    line("deterioration as % of scenario source acquisition cost",
         "        n/a" if d is None else f"{100.0 * d:>17.4f}%")
    line("events with positive per-event deterioration",
         f"{a.pos_det:>18,}")
    line("  share of events", f"{100.0 * a.pos_det / a.n:>17.3f}%"
         if a.n else "        n/a")
    print()


def view_b(a_top, a, n_exhausted):
    req = a_top.n
    line("requested events", f"{req:>18,}")
    line("fully depth-supported events", f"{a.n:>18,}")
    line("DEPTH_EXHAUSTED events (FULL_Q_EXECUTION_PNL = NOT IDENTIFIED)",
         f"{n_exhausted:>18,}")
    line("support rate", f"{100.0 * a.n / req:>17.3f}%" if req else "     n/a")
    line("requested shares (all eligible rows)", f"{a_top.q:>18,.4f}")
    line("supported shares", f"{a.q:>18,.4f}")
    line("conditions on supported rows", f"{len(a.conds):>18,}")
    line("scenario source acquisition cost on supported rows", money(a.cost_src))
    line("first-observed-depth acquisition cost (sum q*vwap)", money(a.cost_fod))
    line("SOURCE_DEPTH_SUBSET_COUNTERFACTUAL_PNL", money(a.pnl_src))
    line("FIRST_OBSERVED_DEPTH_COUNTERFACTUAL_PNL", money(a.pnl_fod))
    line("TOP_COMPONENT", money(a.top))
    line("DEPTH_COMPONENT", money(a.dep))
    line("TOTAL_OBSERVED_PNL_DETERIORATION", money(a.total))
    line("source counterfactual ROI  (on source cost)",
         fmt_r(ratio(a.pnl_src, a.cost_src)))
    line("first-observed-depth counterfactual ROI  (on its own cost)",
         fmt_r(ratio(a.pnl_fod, a.cost_fod)))
    line("  same, on the source cost basis for a like-for-like read",
         fmt_r(ratio(a.pnl_fod, a.cost_src)))
    d = ratio(a.total, a.cost_src)
    line("deterioration as % of supported source acquisition cost",
         "        n/a" if d is None else f"{100.0 * d:>17.4f}%")
    line("TOP_COMPONENT / TOTAL", fmt_r(ratio(a.top, a.total)))
    line("DEPTH_COMPONENT / TOTAL", fmt_r(ratio(a.dep, a.total)))
    if a.total == 0:
        line("  (denominator is zero -- the two shares are not reported)", "")
    print()


def segment_block(dim, tops, deps):
    print(f"-- SEGMENT BY {dim.upper()} -- top of book, all valid events --")
    print(f"{dim[:24]:<26}{'events':>9}{'scen src cost':>16}"
          f"{'src CF P&L':>15}{'src ROI':>9}{'fo-top P&L':>15}"
          f"{'fo ROI':>9}{'ToB deteri':>14}{'dep sup':>9}")
    for key in sorted(tops, key=lambda k: -tops[k].cost_src):
        a = tops[key]
        print(f"{str(key)[:24]:<26}{a.n:>9,}{a.cost_src:>16,.2f}"
              f"{a.pnl_src:>15,.2f}{fmt_r(ratio(a.pnl_src, a.cost_src), 3):>9}"
              f"{a.pnl_fot:>15,.2f}"
              f"{fmt_r(ratio(a.pnl_fot, a.cost_fot), 3):>9}"
              f"{a.det:>14,.2f}"
              f"{100.0 * a.sup / a.n:>8.1f}%")
    print()
    print(f"-- SEGMENT BY {dim.upper()} -- depth-supported rows only --")
    print(f"{dim[:24]:<26}{'events':>9}{'total deteri':>16}"
          f"{'top component':>16}{'depth component':>18}{'top share':>11}")
    for key in sorted(deps, key=lambda k: -deps[k].cost_src):
        a = deps[key]
        print(f"{str(key)[:24]:<26}{a.n:>9,}{a.total:>16,.2f}"
              f"{a.top:>16,.2f}{a.dep:>18,.2f}"
              f"{fmt_r(ratio(a.top, a.total), 3):>11}")
    print()


def answers(top, dep, exhausted, n_pop, n_cond, notional):
    a, d = top["Q_A"], dep["Q_A"]
    print("=" * 74)
    print("THE SEVEN QUESTIONS, ANSWERED EXACTLY")
    print("=" * 74)
    print("1. Realized settlement counterfactual P&L at RN1's source fill")
    print("   prices, on SETTLEMENT_ANALYZABLE_STRONG:")
    print(f"      at Q_A (10% of his shares)  {a.pnl_src:>18,.2f}")
    print(f"      at Q_B (his shares)         {top['Q_B'].pnl_src:>18,.2f}")
    print("   SETTLEMENT_REALIZED_SOURCE_COUNTERFACTUAL_PNL. Not expected")
    print("   value, not alpha, not guaranteed edge, not RN1 gross edge.\n")

    print("2. Same-event P&L at the first retained top-of-book ask, Q_A:")
    print(f"      FIRST_RETAINED_OBSERVATION_COUNTERFACTUAL_PNL "
          f"{a.pnl_fot:>18,.2f}\n")

    print("3. On Q_A depth-supported events, after retained depth walking:")
    print(f"      FIRST_OBSERVED_DEPTH_COUNTERFACTUAL_PNL "
          f"{d.pnl_fod:>18,.2f}")
    print(f"      (on the {d.n:,} supported events, whose source-price")
    print(f"       counterfactual P&L is {d.pnl_src:,.2f}; the other")
    print(f"       {exhausted['Q_A']:,} events are NOT IDENTIFIED)\n")

    print("4. Q_A P&L deterioration:")
    print(f"      before depth walking (all valid events) {a.det:>18,.2f}")
    print(f"      before depth walking (supported subset) {d.top:>18,.2f}")
    print(f"      from depth walking   (supported subset) {d.dep:>18,.2f}")
    print(f"      total                (supported subset) {d.total:>18,.2f}")
    print("   The first line and the rest are on DIFFERENT populations and are")
    print("   not summed. Only the supported-subset lines decompose.\n")

    ts = ratio(d.top, d.total)
    ds = ratio(d.dep, d.total)
    print("5. Fraction of measured Q_A deterioration, on the depth-supported")
    print("   subset where the decomposition is defined:")
    print(f"      top-of-book movement {fmt_r(ts)}   "
          f"({'n/a' if ts is None else f'{100 * ts:.3f}%'})")
    print(f"      depth walking        {fmt_r(ds)}   "
          f"({'n/a' if ds is None else f'{100 * ds:.3f}%'})\n")

    # THE DECISION RULE, STATED BEFORE THE VERDICT AND NOT TUNED TO IT. It is
    # my rule, not an owner-approved one, and the raw numbers above let any
    # other rule be applied instead. It is NOT a blind rule: run 81A already
    # established that top-of-book movement is positive on about 89.5% of U2
    # events, so the direction was known before the threshold was written.
    frac_lost = ratio(d.total, d.pnl_src) if d.pnl_src > 0 else None
    share_pos = ratio(a.pos_det, a.n)
    if d.total <= 0:
        verdict = "CONTRADICTED"
    elif (frac_lost is not None and frac_lost >= 0.10
          and share_pos is not None and share_pos > 0.50):
        verdict = "SUPPORTED"
    else:
        verdict = "INDETERMINATE"
    print("6. The narrow hypothesis:")
    print('      "By the first retained observation, the economics available')
    print('       to a reactive copier are materially worse than RN1\'s')
    print('       source-fill economics."')
    print()
    print("   DECISION RULE, stated here and applied as written: SUPPORTED")
    print("   requires (a) total deterioration > 0, (b) it removes at least")
    print("   10% of the source-price counterfactual P&L on the same rows,")
    print("   and (c) more than half of events show positive per-event")
    print("   deterioration. Deterioration <= 0 is CONTRADICTED. Anything")
    print("   else is INDETERMINATE.")
    print(f"      (a) total deterioration            {d.total:>18,.2f}")
    print(f"      (b) share of source CF P&L removed "
          f"{'n/a' if frac_lost is None else f'{100 * frac_lost:17.3f}%'}")
    print(f"      (c) share of events deteriorating  "
          f"{'n/a' if share_pos is None else f'{100 * share_pos:17.3f}%'}")
    print(f"\n   VERDICT ON THIS SELECTED COHORT: {verdict}")
    print("   This does NOT establish that physical latency caused the")
    print("   deterioration.\n")

    print("7. What remains unidentified before deciding whether reactive")
    print("   copying is engineering-fixable, fundamentally too late, or")
    print("   unresolved:")
    for i, t in enumerate([
        "FEE_AND_REBATE_ADJUSTED_NET_ECONOMICS -- no fee or rebate column "
        "exists in any retained artifact.",
        "THE CLOCK. TIMESTAMP_INTEGRITY is unresolved: four clock domains and "
        "nothing retained for the venue's own. The first RETAINED observation "
        "is a property of the probe cadence, not a measured elapsed time, so "
        "no part of this deterioration is attributed to latency.",
        "WHAT A COPIER WOULD ACTUALLY HAVE BEEN FILLED AT. This run reprices "
        "at an observed ask and an observed book; it does not model queue "
        "position, partial fills, cancellation, or the copier's own market "
        "impact.",
        "THE UNSELECTED 47.6% of U2 events and 48.8% of its notional, "
        "dominated by UNRESOLVED at the draw instant. Nothing here may be "
        "rescaled to U2 or to RN1's whole book.",
        "RN1'S COMPLETE BOOK. U2 holds 67.248% of U0 on these conditions and "
        "the population is 100% BUY, so his sells and his matched structure "
        "are outside every figure above.",
        "EXTERNAL PAYOUT CORRECTNESS. The vectors are well-formed and map "
        "unambiguously; whether the named winner is the true winner has no "
        "independent retained source.",
        "DEPTH BEYOND WHAT WAS RETAINED. On DEPTH_EXHAUSTED rows the full-q "
        "execution P&L is NOT IDENTIFIED, and at Q_B and Q_C that is a large "
        "share of the requested rows.",
    ], 1):
        print(f"   {i}. {t}")
    print()
    print(f"All of the above is on {n_pop:,} events / {n_cond:,} conditions /")
    print(f"${notional:,.2f} source notional -- a settlement-selected cohort.")


if __name__ == "__main__":
    raise SystemExit(main())
