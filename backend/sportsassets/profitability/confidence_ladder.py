"""THE CONFIDENCE LADDER (owner audit 2026-10-04, R30A). Pure; no I/O.

THE LEVELS, exactly as the owner's audit names them:

  0  RESEARCH                       the strategy exists in the records
  1  INVESTMENT_PAPER_ONLY          the strategy trades in the paper book's
                                    INVESTMENT sleeve (migration 223), and at
                                    least one INVESTMENT paper position exists
  2  PAPER_STATISTICALLY_SUPPORTED  its FORWARD INVESTMENT paper evidence is
                                    statistically supported using independent
                                    (event-clustered) confidence intervals
  3  LIVE_SHADOW_PARITY             LIVE SHADOW parity with zero unexplained
                                    logic divergence (live_parity)
  4  TINY_LIVE_EXECUTION_VALIDATION real-money execution validated at tiny
                                    size
  5  LIVE_ECONOMIC_VALIDATION       actual live economic validation after
                                    fees, slippage, drawdown and capital-hours
  6  SCALE                          scale

For each strategy and overall (the INVESTMENT sleeve), the ladder reports
the HIGHEST level whose evidence is met -- a level never counts when a lower
one fails -- and the blockers of the next level. Every figure comes from real
records the caller read; an input that is missing makes its level
UNAVAILABLE with the reason, never a manufactured pass.

LEVEL 2 -- NO INVENTED SAMPLE COUNT. The evidence is
profitability/validation.py's forward, event-clustered verdict
(`validation.verdict_rule`: positions on one fixture -- else one market --
are ONE independent outcome; one-sided 95% Student-t and bootstrap lower
bounds of the mean per-EVENT net; realized net after fees; drawdown;
marked-open net; forward-only). The ladder's gate is that verdict's
STATISTICAL checks, re-run on a variance the PAYOFFS can actually have,
plus POWER on that same variance:

  * the t and bootstrap lower bounds of mean per-event net are > 0, net
    after fees > 0, net including marked open positions > 0, drawdown
    within the validation bound, forward only (validation's own checks);
  * THE PAYOFF VARIANCE FLOOR (R30A review). A binary contract's outcome is
    win-or-lose: a small all-winning sample has an observed variance that
    reflects only stake-size noise and says nothing about the loss it did
    not happen to draw. Two winning events of +10 and +11 had an observed sd
    of 0.7 and passed a gate built on observed variance alone, while
    validation's own verdict said POSITIVE_BUT_INSUFFICIENT_SAMPLE and
    live_parity's readiness said NOT_READY. So each event's standard
    deviation is floored at the LARGEST a ZERO-EDGE outcome on its recorded
    payoff range can have: a position that bought q contracts for an
    acquisition cost A (fees included) settles in [-(A + sale fees),
    q * $1 - A]; the most variable mean-zero outcome on [lo, hi] is the
    two-point one, sd = sqrt(hi * -lo) (= q * sqrt(c * (1 - c)) for a held
    binary at a fair price c); when hi <= 0 no mean-zero outcome exists and
    Popoviciu's bound (hi - lo) / 2 is used. The positions of one event are
    summed sd by sd (Minkowski: the sd of a sum never exceeds the sum of the
    sds, whatever their dependence). The per-event sd the gate uses is
        sd_eff = max(observed per-event sd, sqrt(mean of floored event
                     variances))
    and the floored one-sided 95% t lower bound m - t_(n-1) * sd_eff /
    sqrt(n) must be > 0. This is a NULL (zero-edge) variance, conservative
    for a real edge and for exits before settlement; a position without a
    recorded payoff range (bought quantity, acquisition cost) makes the
    floor -- and so level 2 -- UNAVAILABLE, never a pass on observed
    variance alone;
  * POWER: the independent events observed are at least the events a
    one-sided alpha = 0.05 test needs for POWER_TARGET power at the OBSERVED
    mean and sd_eff:
        n_power = ceil(((z_(1-alpha) + z_power) * sd_eff / mean)^2)
    -- the sample needed is derived from the evidence, not declared.
    Computed at the observed mean it is NOT a pre-study power analysis: it
    is algebraically the requirement sqrt(n) * mean / sd_eff >= z_(1-alpha)
    + z_power (= 2.487), a stricter-than-95% bound on the floored variance,
    and it is reported as exactly that;
  * a variance needs two independent outcomes (UNAVAILABLE, not a pass).

validation.py's own fixed minimums (VERDICT_MIN_RESOLVED /
VERDICT_MIN_INDEPENDENT_EVENTS = 30) are reported beside the ladder's
result, NOT used as its gate: the owner accepted 30 decision / 30
management observations ONLY as a minimum PARITY sample (level 3), never as
a profitability sample size. Where the ladder's level 2 is MET while
validation's verdict is still POSITIVE_BUT_INSUFFICIENT_SAMPLE (the fixed
minimums only), the level says so (`validation_agreement`), and
live_parity's tiny-live readiness -- which requires that verdict -- is
carried forward as a level-4 blocker: no endpoint reports "only live capital
blocks tiny live" while the existing readiness gate is NOT_READY.

LEVEL 3 -- live_parity.readiness_report's parity gate (computed by the
caller): a production cutover is recorded, SMALL LIVE is not halted, zero
LOGIC_DIVERGENCE in the forward sample, and the owner-accepted minimum
parity sample (live_parity.MIN_DECISION_SAMPLE decision intents and
MIN_MANAGEMENT_SAMPLE management intents, INVESTMENT sleeve).

LEVELS 4-6 -- NOT_REACHED while no live capital exists: SMALL LIVE is SHADOW
(the database CHECKs small_live_control.mode = 'SHADOW'); nothing here can
change that, and nothing here reads or writes a control. Level 4's blockers
also carry live_parity.readiness's verdict for tiny live (the parity gate's
blockers plus PROFITABILITY:<validation verdict> unless it is
SUPPORTED_BY_FORWARD_EVIDENCE -- the readiness rule exactly), so the next
step's real prerequisites are all listed.

RESEARCH / SHADOW: this module computes over rows a caller read. It has no
venue, order, sizing, limit, threshold, gate or capital authority.
"""
from __future__ import annotations

import math

from . import common as C
from . import validation as V

#: V2 (R30A review): level 2's variance is floored at the payoff-implied
#: zero-edge variance; level 4 carries live_parity's tiny-live readiness
VERSION = "CONFIDENCE_LADDER_V2"
LEVELS = (
    (0, "RESEARCH"),
    (1, "INVESTMENT_PAPER_ONLY"),
    (2, "PAPER_STATISTICALLY_SUPPORTED"),
    (3, "LIVE_SHADOW_PARITY"),
    (4, "TINY_LIVE_EXECUTION_VALIDATION"),
    (5, "LIVE_ECONOMIC_VALIDATION"),
    (6, "SCALE"),
)
CRITERIA = {
    0: "the strategy exists in the records (a decision, a position or a "
       "parity row)",
    1: "the strategy is an INVESTMENT-sleeve strategy (migration 223) and "
       "at least one INVESTMENT paper position exists",
    2: ("FORWARD INVESTMENT paper evidence statistically supported: "
        "validation.verdict_rule's event-clustered checks (t and bootstrap "
        "lower bounds of mean per-event net > 0, net after fees > 0, net incl. "
        "marked open > 0, drawdown within bound, forward only) AND the t "
        "lower bound on the per-event sd floored at the payoffs' zero-edge "
        "sd > 0 AND independent events >= the power requirement from the "
        "observed mean and that floored sd"),
    3: ("LIVE SHADOW parity: production cutover recorded, SMALL LIVE not "
        "halted, zero LOGIC_DIVERGENCE, and the owner-accepted minimum parity "
        "sample (decision and management intents, INVESTMENT sleeve)"),
    4: "real-money execution validated at tiny size (requires live capital)",
    5: ("actual live economic validation after fees, slippage, drawdown and "
        "capital-hours (requires live capital)"),
    6: "scale (requires level 5 and the owner's capital decision)",
}
MET, NOT_MET, NOT_REACHED, UNAVAILABLE = (
    "MET", "NOT_MET", "NOT_REACHED", "UNAVAILABLE")
ALPHA = 0.05                       # one-sided, the validation verdict's own
POWER_TARGET = 0.80
Z_ALPHA = 1.6448536                # z_(1 - 0.05)
Z_POWER = 0.8416212                # z_(0.80)
#: validation's checks that are STATISTICAL / ECONOMIC (the ladder's gate)
STAT_CHECKS = ("REALIZED_NET_AFTER_FEES_POSITIVE", "T_LOWER_BOUND_POSITIVE",
               "BOOTSTRAP_LOWER_BOUND_POSITIVE", "DRAWDOWN_WITHIN_BOUND",
               "NET_INCLUDING_MARKED_OPEN_POSITIVE", "FORWARD_ONLY")
#: validation's FIXED minimums: reported, never the ladder's gate
FIXED_COUNT_CHECKS = ("MIN_RESOLVED", "MIN_INDEPENDENT_EVENTS")
R_NO_CUTOVER = V.R_NO_CUTOVER
R_NO_LIVE_CAPITAL = "NO_LIVE_CAPITAL"
R_NO_PAYOFF_RANGE = "PAYOFF_RANGE_UNAVAILABLE"
R_FLOORED_T = "PAYOFF_FLOORED_T_LOWER_BOUND_NOT_POSITIVE"
R_TINY_LIVE = "TINY_LIVE_READINESS_NOT_READY"
#: what one contract pays when it wins (the venue's $1 binary)
PAYOUT_PER_CONTRACT = 1.0
#: live_parity.readiness's profitability blocker and the verdict it needs
#: (copied, not imported: this module imports nothing outside the
#: profitability package; tests/test_confidence_ladder.py pins the copy to
#: live_parity.readiness's own output)
PROFITABILITY_BLOCKER = "PROFITABILITY"
READY_FOR_TINY_PILOT, NOT_READY = "READY_FOR_TINY_PILOT", "NOT_READY"
#: the presence keys that are record counts (level 0's evidence)
PRESENCE_COUNTS = ("decisions_recent", "parity_rows", "positions",
                   "investment_positions")


def _level(lv, status, *, evidence=None, blockers=(), basis=None) -> dict:
    return {"level": lv, "name": dict(LEVELS)[lv], "criterion": CRITERIA[lv],
            "status": status, "met": status == MET,
            "evidence": evidence or {}, "blockers": list(blockers),
            "basis": basis}


def _num(v):
    return V._num(v)


# ═════════════════════════════════════════════════════════════════════
# LEVEL 2: THE EVENT-CLUSTERED VERDICT + POWER FROM OBSERVED VARIANCE
# ═════════════════════════════════════════════════════════════════════

def _events(pos: list) -> list:
    """The RESOLVED positions grouped into independent events (validation.
    event_key), in the order the events' outcomes became known: [{net,
    released, payoff_sd}] -- payoff_sd None when any of the event's
    positions has no recorded payoff range."""
    ev: dict = {}
    for p in pos:
        if not V._resolved(p) or _num(p.get("realized_pnl_usd")) is None:
            continue
        k = V.event_key(p)
        e = ev.setdefault(k, {"key": k, "net": 0.0, "released": 0.0,
                              "payoff_sd": 0.0, "positions": 0})
        e["net"] += _num(p["realized_pnl_usd"])
        e["released"] = max(e["released"], _num(p.get("released_at")) or 0.0)
        e["positions"] += 1
        sd = payoff_sd(p)
        # Minkowski: sd(sum) <= sum of sds, whatever the dependence
        e["payoff_sd"] = None if (sd is None or e["payoff_sd"] is None) \
            else e["payoff_sd"] + sd
    return sorted(ev.values(), key=lambda e: (e["released"], e["key"]))


def event_series(pos: list) -> list:
    """Per-EVENT realized net of the RESOLVED positions, in the order the
    events' outcomes became known -- exactly validation.verdict_rule's
    clustering (validation.event_key)."""
    return [e["net"] for e in _events(pos)]


def payoff_sd(p: dict):
    """The LARGEST standard deviation a ZERO-EDGE outcome of this position
    can have, from its recorded payoff range, or None when the range is not
    recorded. A position that bought q contracts for an acquisition cost A
    (buy fees included) ends in [lo, hi] = [-(A + sale fees), q * $1 - A]
    (every contract paying $1 at best, nothing coming back at worst). The
    most variable mean-zero outcome on [lo, hi] is the two-point one, sd =
    sqrt(hi * -lo) -- q * sqrt(c * (1 - c)) for a binary held to settlement
    at a fair price c; with hi <= 0 no mean-zero outcome exists and the
    largest sd of ANY outcome on the range, (hi - lo) / 2 (Popoviciu), is
    used."""
    q, a = _num(p.get("bought_qty")), _num(p.get("acquisition_cost_usd"))
    if q is None or a is None or q <= 0 or a < 0:
        return None
    sf = max(0.0, _num(p.get("sale_fees_usd")) or 0.0)
    hi = q * PAYOUT_PER_CONTRACT - a
    lo = -(a + sf)
    if hi > 0 and lo < 0:
        return math.sqrt(hi * -lo)
    return max(0.0, (hi - lo) / 2.0)


def power(xs: list, floor_sds) -> dict:
    """Level 2's sample test over the per-event nets `xs` and the events'
    payoff-implied zero-edge standard deviations `floor_sds` (one per event,
    same order; None when any event's payoff range is unrecorded):
      sd_eff = max(observed per-event sd, sqrt(mean of floor_sds^2));
      the floored one-sided 95% t lower bound m - t_(n-1) sd_eff / sqrt(n)
      must be > 0; and n >= n_power = ceil(((z_(1-a) + z_p) sd_eff / m)^2).
    Every figure is derived from the sample and its recorded payoffs;
    nothing is declared."""
    n = len(xs)
    out = {"independent_events": n, "alpha_one_sided": ALPHA,
           "power_target": POWER_TARGET,
           "method": (
               "sd_eff = max(observed per-event sd, rms of the events' "
               "zero-edge payoff sd); floored t lower bound = mean - "
               "t_(n-1) * sd_eff / sqrt(n) > 0; n_power = ceil(((z_(1-alpha) "
               "+ z_power) * sd_eff / mean)^2) -- at the OBSERVED mean, i.e. "
               "sqrt(n) * mean / sd_eff >= %.3f (a stricter-than-95%% bound, "
               "not a pre-study power analysis); n_ci = smallest n whose "
               "floored t lower bound clears 0" % (Z_ALPHA + Z_POWER))}
    floors_ok = (floor_sds is not None and len(floor_sds) == n
                 and all(f is not None for f in floor_sds))
    sd_floor = (math.sqrt(sum(f * f for f in floor_sds) / n)
                if floors_ok and n else None)
    out["sd_payoff_floor"] = C.rnd(sd_floor, 9)
    if n < 2:
        return dict(out, status=UNAVAILABLE, mean=(C.rnd(xs[0], 9) if n
                                                   else None), sd=None,
                    sd_observed=None, sd_eff=None, t_lower_95_floored=None,
                    n_power=None, n_ci=None,
                    why="FEWER_THAN_TWO_INDEPENDENT_RESOLVED_EVENTS_NO_"
                        "VARIANCE")
    m = sum(xs) / n
    sd_obs = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    out.update(mean=C.rnd(m, 9), sd_observed=C.rnd(sd_obs, 9))
    if not floors_ok:
        # never a pass on the observed variance alone
        return dict(out, status=UNAVAILABLE, sd=None, sd_eff=None,
                    t_lower_95_floored=None, n_power=None, n_ci=None,
                    why="%s: a resolved forward position carries no bought "
                        "quantity or acquisition cost, so the zero-edge "
                        "variance its payoffs allow cannot be bounded"
                        % R_NO_PAYOFF_RANGE)
    sd = max(sd_obs, sd_floor)
    out.update(sd=C.rnd(sd, 9), sd_eff=C.rnd(sd, 9),
               sd_basis=("PAYOFF_FLOOR" if sd_floor > sd_obs
                         else "OBSERVED"))
    if sd <= 0:
        return dict(out, status=UNAVAILABLE, t_lower_95_floored=None,
                    n_power=None, n_ci=None,
                    why="ZERO_VARIANCE_ACROSS_EVENTS_POWER_NOT_ESTIMABLE")
    t_lo = m - V.t_crit_95(n - 1) * sd / math.sqrt(n)
    out["t_lower_95_floored"] = C.rnd(t_lo, 9)
    if m <= 0:
        return dict(out, status=NOT_MET, n_power=None, n_ci=None,
                    why="OBSERVED_MEAN_NET_PER_EVENT_NOT_POSITIVE")
    n_power = max(2, int(math.ceil(((Z_ALPHA + Z_POWER) * sd / m) ** 2)))
    # t_(n-1) >= z, so the normal-approximation n is a lower bound of n_ci:
    # search upward from it (t converges to z, so the walk is short)
    n_ci = None
    k = max(2, int(math.ceil((Z_ALPHA * sd / m) ** 2)))
    for k in range(k, k + 100_000):
        if m - V.t_crit_95(k - 1) * sd / math.sqrt(k) > 0:
            n_ci = k
            break
    whys = []
    if t_lo <= 0:
        whys.append("%s: %.6f" % (R_FLOORED_T, t_lo))
    if n < n_power:
        whys.append("INDEPENDENT_EVENTS_%d_BELOW_THE_%d_THE_FLOORED_VARIANCE_"
                    "REQUIRES_FOR_%d%%_POWER" % (n, n_power,
                                                 int(POWER_TARGET * 100)))
    return dict(out, status=NOT_MET if whys else MET, n_power=n_power,
                n_ci=n_ci, shortfall=max(0, n_power - n),
                why="; ".join(whys) or None)


def verdict_seed(pos: list) -> int:
    """validation.compute's own verdict seed over these positions, so the
    ladder's bootstrap bound is the validation endpoint's exactly."""
    return C.seed_of([V.VERSION, "VERDICT", sorted(
        (p.get("position_key") or "", _num(p.get("realized_pnl_usd")))
        for p in pos)])


def level_two(pos: list, *, cutover, now: float, seed=None) -> dict:
    """Level 2 for ONE scope: `pos` are that scope's INVESTMENT paper
    positions (validation's position rows, all windows); the forward window
    starts at the production cutover."""
    if cutover is None:
        return _level(2, NOT_MET, blockers=[R_NO_CUTOVER], evidence={
            "why": "no production cutover is recorded, so no forward "
                   "window exists (validation.forward_since)",
            "validation_verdict": V.NOT_ESTABLISHED,
            "validation_why": R_NO_CUTOVER},
            basis="profitability.validation")
    since = V.forward_since(None, cutover)
    groups = V.scope_groups(pos, V.INVESTMENT, since)
    fpos = [p for p in pos if p.get("group_id") in groups]
    verdict = V.verdict_rule(
        fpos, since=since, cutover=cutover,
        seed=verdict_seed(fpos) if seed is None else seed)
    evs = _events(fpos)
    pw = power([e["net"] for e in evs], [e["payoff_sd"] for e in evs])
    stat = {k: verdict["checks"].get(k) for k in STAT_CHECKS}
    failed = [k for k, ok in stat.items() if not ok]
    blockers = list(failed)
    if pw["status"] != MET:
        blockers.append("POWER:%s" % pw["why"])
    status = MET if not blockers else NOT_MET
    supported = verdict["verdict"] == V.SUPPORTED
    agreement = {
        "validation_verdict": verdict["verdict"],
        "ladder_level_2": status,
        "agrees": (status == MET) == supported,
        "why": (None if (status == MET) == supported else
                "the ladder's gate is the variance-derived requirement; "
                "validation's verdict also needs its fixed minimums (%s), "
                "which the owner accepted only as a PARITY sample. "
                "live_parity's tiny-live readiness requires that verdict "
                "and is carried as a level-4 blocker" % ", ".join(
                    k for k in FIXED_COUNT_CHECKS
                    if not verdict["checks"].get(k))
                if status == MET else
                "validation's verdict is SUPPORTED but the ladder's floored "
                "variance / power requirement is not met")}
    return _level(2, status, blockers=blockers, evidence={
        "forward_since": since,
        "forward_positions": len(fpos),
        "statistical_checks": stat,
        "power": pw,
        "events": [{"event": e["key"], "net_usd": C.rnd(e["net"], 9),
                    "payoff_sd_usd": C.rnd(e["payoff_sd"], 9),
                    "positions": e["positions"]} for e in evs[-200:]],
        "validation_verdict": verdict["verdict"],
        "validation_why": verdict["why"],
        "validation_evidence": verdict["evidence"],
        "validation_agreement": agreement,
        "validation_fixed_minimums": {
            k: {"passed": verdict["checks"].get(k), "rule":
                verdict["rule"]["checks"].get(k)}
            for k in FIXED_COUNT_CHECKS},
        "fixed_minimums_role": (
            "reported, not the ladder's gate: the owner accepted 30/30 only "
            "as a minimum PARITY sample (level 3); level 2's sample is "
            "the requirement derived from the observed mean and the "
            "payoff-floored variance")},
        basis="profitability.validation.verdict_rule (forward, event-"
              "clustered) + the t bound and power on the per-event sd "
              "floored at the payoffs' zero-edge sd")


# ═════════════════════════════════════════════════════════════════════
# LEVEL 3: LIVE SHADOW PARITY (live_parity.readiness_report)
# ═════════════════════════════════════════════════════════════════════

def level_three(parity: dict | None, *, why_unavailable=None) -> dict:
    """`parity` is live_parity.readiness (for one scope) or readiness_report
    (overall). Level 3 is the PARITY gate only: readiness's PROFITABILITY
    blocker is not a parity fact -- level 2 measures the profitability
    evidence, and the readiness verdict for tiny live (parity gate AND the
    validation verdict) is carried, whole, into level 4's blockers
    (tiny_live_readiness)."""
    if parity is None:
        return _level(3, UNAVAILABLE, blockers=[
            why_unavailable or "PARITY_LEDGER_NOT_READ"],
            basis="live_parity.readiness_report")
    blockers = [b for b in (parity.get("blockers") or [])
                if not str(b).startswith("PROFITABILITY")]
    if parity.get("logic_divergences"):
        if not any(str(b).startswith("LOGIC_DIVERGENCES") for b in blockers):
            blockers.append("LOGIC_DIVERGENCES_IN_SAMPLE:%d"
                            % parity["logic_divergences"])
    status = MET if not blockers else NOT_MET
    return _level(3, status, blockers=blockers, evidence={
        k: parity.get(k) for k in (
            "version", "sleeve", "candidate_count", "management_count",
            "exact_decision_match", "xavier_management_match", "matched",
            "expected_scale_differences", "venue_only_differences",
            "logic_divergences", "parity_gate", "since", "observation_1",
            "rule")}, basis="live_parity.readiness_report (the parity gate; "
                            "its profitability blocker is carried to level "
                            "4 with the scope's validation verdict)")


# ═════════════════════════════════════════════════════════════════════
# LEVELS 4-6: NO LIVE CAPITAL
# ═════════════════════════════════════════════════════════════════════

def tiny_live_readiness(parity: dict | None, verdict: str | None, *,
                        why_unavailable=None) -> dict:
    """live_parity.readiness's verdict for tiny live, for one scope: the
    parity gate's blockers, plus PROFITABILITY:<validation verdict> unless
    that verdict is SUPPORTED_BY_FORWARD_EVIDENCE -- readiness's own rule
    (live_parity.readiness appends "PROFITABILITY:%s" whenever the verdict
    is not SUPPORTED). The caller's readiness was computed without a verdict
    (its PROFITABILITY:NOT_EVALUATED is replaced by the scope's own)."""
    if parity is None:
        blockers = [why_unavailable or "PARITY_LEDGER_NOT_READ"]
    else:
        blockers = [str(b) for b in (parity.get("blockers") or [])
                    if not str(b).startswith(PROFITABILITY_BLOCKER)]
    if verdict != V.SUPPORTED:
        blockers.append("%s:%s" % (PROFITABILITY_BLOCKER,
                                   verdict or "NOT_EVALUATED"))
    return {"recommendation": NOT_READY if blockers else READY_FOR_TINY_PILOT,
            "blockers": blockers, "profitability_verdict": verdict,
            "rule": "live_parity.readiness: the parity gate AND the "
                    "INVESTMENT forward verdict SUPPORTED_BY_FORWARD_EVIDENCE",
            "capital_activation": "NOT_AUTHORIZED: SMALL LIVE stays SHADOW "
                                  "until the gate passes AND the owner gives "
                                  "explicit activation approval"}


def live_levels(live: dict, readiness: dict | None = None) -> list:
    """Levels 4, 5 and 6 from the recorded live state. SMALL LIVE is SHADOW
    (database CHECK): no real-money execution exists to validate. Level 4
    also lists live_parity's tiny-live readiness blockers (`readiness`,
    tiny_live_readiness), so "only live capital" is never the whole story
    while that existing gate is NOT_READY."""
    mode = (live or {}).get("small_live_mode")
    ev = {k: (live or {}).get(k) for k in (
        "small_live_mode", "small_live_halted", "live_venue_order_events",
        "canonical_live_executions_by_mode", "execmirror_enabled",
        "execmirror_stopped", "execmirror_venue_orders_by_strategy", "why")}
    ev["execmirror_role"] = (
        "REPORTED, NOT COUNTED: the legacy execution mirror's venue orders "
        "are not the canonical intent path, and no level-4 validation rule "
        "over them (parity of fills against the canonical intent, live fees "
        "and slippage) is declared here; level 4 is the canonical SMALL LIVE "
        "adapter's real-money execution")
    ev["tiny_live_readiness"] = readiness
    why = ("%s: SMALL LIVE mode is %s -- the canonical path sends no "
           "real-money order, so there is nothing to validate" % (
               R_NO_LIVE_CAPITAL, mode or "UNREAD"))
    b4 = [why] + ["%s: %s" % (R_TINY_LIVE, b)
                  for b in ((readiness or {}).get("blockers") or [])]
    out = [_level(4, NOT_REACHED, evidence=ev, blockers=b4,
                  basis="small_live_control (mode CHECKed SHADOW), "
                        "small_live_order_events, execmirror_control, "
                        "live_parity.readiness (tiny live)")]
    out.append(_level(5, NOT_REACHED, evidence={}, blockers=[
        "%s: requires level 4 (live fills, live fees, live slippage, live "
        "drawdown and capital-hours)" % R_NO_LIVE_CAPITAL],
        basis="requires level 4"))
    out.append(_level(6, NOT_REACHED, evidence={}, blockers=[
        "%s: requires level 5 and the owner's explicit capital decision"
        % R_NO_LIVE_CAPITAL], basis="requires level 5"))
    return out


# ═════════════════════════════════════════════════════════════════════
# ONE SCOPE, AND THE WHOLE LADDER
# ═════════════════════════════════════════════════════════════════════

def scope_ladder(*, scope: str, strategy, positions: list, cutover,
                 parity, parity_why, live: dict, now: float,
                 presence: dict) -> dict:
    """The ladder of ONE scope: `strategy` None = overall (the INVESTMENT
    sleeve); `positions` are validation position rows (any sleeve);
    `presence` counts the scope's records ({decisions_recent, positions,
    parity_rows})."""
    if strategy is None:
        sleeve = C.INVESTMENT
        mine = [p for p in positions if C.sleeve_of(p) == C.INVESTMENT]
    else:
        sleeve = C.strategy_sleeve(strategy)
        mine = [p for p in positions if p.get("strategy") == strategy]
    inv = [p for p in mine if C.sleeve_of(p) == C.INVESTMENT]
    # only RECORD COUNTS make a strategy exist (a window length is not one)
    present = bool(mine) or any(
        isinstance((presence or {}).get(k), (int, float))
        and presence[k] > 0 for k in PRESENCE_COUNTS)
    levels = [_level(0, MET if present else NOT_MET, evidence=dict(
        presence or {}, positions=len(mine)),
        blockers=[] if present else ["NO_RECORD_OF_THIS_STRATEGY"],
        basis="paper decisions (recent), paper positions, the parity "
              "ledger")]
    b1 = []
    if sleeve != C.INVESTMENT:
        b1.append("SLEEVE_IS_%s: never production evidence (migration 223)"
                  % sleeve)
    if not inv:
        b1.append("NO_INVESTMENT_PAPER_POSITION")
    levels.append(_level(1, MET if not b1 else NOT_MET, blockers=b1,
                         evidence={"sleeve": sleeve,
                                   "investment_positions": len(inv),
                                   "other_sleeve_positions":
                                       len(mine) - len(inv)},
                         basis="paper_sleeve_classifications (migration "
                               "223); UNCLASSIFIED is never INVESTMENT"))
    # the bootstrap seed is validation's own (verdict_seed): the ladder's
    # verdict for the overall scope is the validation endpoint's exactly
    l2 = level_two(inv, cutover=cutover, now=now)
    levels.append(l2)
    levels.append(level_three(parity, why_unavailable=parity_why))
    levels.extend(live_levels(live, tiny_live_readiness(
        parity, l2["evidence"].get("validation_verdict"),
        why_unavailable=parity_why)))
    highest = -1
    for lv in levels:
        if lv["status"] != MET:
            break
        highest = lv["level"]
    nxt = next((lv for lv in levels if lv["level"] == highest + 1), None)
    pvs = sorted({str(p["policy_version"]) for p in mine
                  if p.get("policy_version")})
    return {
        "scope": scope, "strategy": strategy, "sleeve": sleeve,
        "book": "PAPER",
        "policy_versions": pvs,
        "policy_version": pvs[0] if len(pvs) == 1 else None,
        "policy_version_why": (
            None if len(pvs) == 1 else
            "NO_CLASSIFIED_POSITION_NAMES_A_POLICY_VERSION" if not pvs else
            "MORE_THAN_ONE_POLICY_VERSION_IN_SCOPE"),
        "classifier_versions": sorted({str(p["classifier_version"])
                                       for p in mine
                                       if p.get("classifier_version")}),
        "confidence_scope": (C.PRODUCTION if sleeve == C.INVESTMENT
                             else C.RESEARCH_SCOPE),
        "level": highest,
        "level_name": dict(LEVELS).get(highest, "NONE"),
        "next_level": None if nxt is None else nxt["level"],
        "next_level_name": None if nxt is None else nxt["name"],
        "blockers_for_next_level": [] if nxt is None else nxt["blockers"],
        "levels": levels,
        "rule": "the highest level whose evidence is met, with every lower "
                "level met; never a skipped level"}


def compute(*, positions: list, strategies, cutover, parity_overall,
            parity_by_strategy: dict, parity_why, live: dict,
            now: float, presence: dict | None = None) -> dict:
    """The whole ladder: overall (INVESTMENT sleeve) and per strategy.
    `presence` = {strategy: {decisions_recent, parity_rows}} (positions are
    counted here)."""
    strategies = sorted({s for s in strategies if s})
    presence = presence or {}

    def pres(s):
        p = dict(presence.get(s) or {})
        p["positions"] = sum(1 for x in positions
                             if s is None or x.get("strategy") == s)
        return p
    tot = {"decisions_recent": sum((presence.get(s) or {}).get(
        "decisions_recent") or 0 for s in strategies
        if C.strategy_sleeve(s) == C.INVESTMENT),
        "investment_positions": sum(1 for x in positions
                                    if C.sleeve_of(x) == C.INVESTMENT)}
    overall = scope_ladder(
        scope="OVERALL_INVESTMENT_SLEEVE", strategy=None,
        positions=positions, cutover=cutover, parity=parity_overall,
        parity_why=parity_why, live=live, now=now, presence=tot)
    per = {}
    for s in strategies:
        per[s] = scope_ladder(
            scope="STRATEGY", strategy=s, positions=positions,
            cutover=cutover, parity=parity_by_strategy.get(s),
            parity_why=parity_why or (
                None if s in parity_by_strategy else
                "NO_PARITY_ROW_FOR_THIS_STRATEGY_SINCE_THE_CUTOVER"),
            live=live, now=now, presence=pres(s))
    return {"version": VERSION, "computed_at": now,
            "levels_spec": [{"level": lv, "name": n, "criterion": CRITERIA[lv]}
                            for lv, n in LEVELS],
            "cutover": cutover,
            "overall": overall, "strategies": per,
            "power_rule": {"alpha_one_sided": ALPHA,
                           "power_target": POWER_TARGET,
                           "basis": "observed per-event mean; per-event sd = "
                                    "max(observed, the payoffs' zero-edge "
                                    "sd); no declared sample count"},
            "label": C.LABEL, "authority": C.AUTHORITY}
