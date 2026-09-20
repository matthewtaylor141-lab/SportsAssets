"""OFFLINE SCHEDULER SIMULATION. Validates W3 before it is deployed.

Owner 2026-09-20: "Validate the combined W3 scheduler offline before
deployment. Include sustained backlog, small budgets, all four
horizons, actual tick spacing, request duration and rate limiting.
Demonstrate that no horizon is permanently starved, requests remain
within budget, and on-time opportunities receive appropriate priority
over late recovery."

WHY A SIMULATOR AND NOT ANOTHER PRODUCTION WINDOW. Every scheduling
defect so far was found by deploying and measuring for thirty minutes:
starvation-by-leftover-budget, horizon starvation by loop order, and a
pacing regression I introduced while claiming pacing was preserved.
That loop costs a deploy and a window per hypothesis, and it tests one
arrival pattern -- whichever one the venue happened to produce. This
runs the same scheduler against a sustained backlog, a starved budget
and rate limiting, deterministically, in milliseconds.

IT IS NOT EVIDENCE ABOUT MARKETS. It exercises the allocation rule
only. Arrival times and tick spacing come from W1's recorded values;
nothing here observes a venue, and no output of this module may be
quoted as a measurement of anything but the scheduler. Reproducing
W1's qualitative shape is not reproducing W1's production behaviour.
"""

from __future__ import annotations

# Measured from W1's own tick records (24 intervals), not assumed.
W1_TICK_SPACING = {"min": 65.0, "p50": 72.0, "p90": 76.0, "max": 82.0}

HORIZONS = (60, 300, 900, 3600)
TOLERANCE_S = 30


class Observation:
    __slots__ = ("t0", "done", "attempts")

    def __init__(self, t0):
        self.t0 = t0
        self.done = {}          # horizon -> lag at which it was read
        self.attempts = 0


def on_time(lag, horizon):
    """The scientific definition, unchanged. |lag - horizon| <= 30."""
    return abs(lag - horizon) <= TOLERANCE_S


def eligible(now, obs, horizon, *, early_s, expiry_s=600):
    """Is this observation selectable for this horizon right now?

    `early_s` is the VERSIONED SAMPLING CHANGE: how far before the
    horizon an observation may be selected. It is NOT a change to the
    tolerance -- a read taken at lag 45 for a 60s horizon is on time by
    the unchanged rule; the question is only whether the scheduler was
    allowed to pick it up yet.
    """
    if horizon in obs.done:
        return False
    lag = now - obs.t0
    return (horizon - early_s) <= lag <= (horizon + expiry_s)


ORDER_OLDEST_FIRST = "oldest_first"      # what production actually does
ORDER_ON_TIME_FIRST = "on_time_first"    # a candidate, NOT deployed

# ── A DEFECT IN THIS SIMULATOR, FOUND BY W3 ─────────────────────────
#
# Until 2026-09-21 this module sorted candidates on-time-first and had
# no way to express anything else. Production's mids_due orders
#     ORDER BY o.observed_at
# which is OLDEST FIRST, unconditionally. So the offline validation of
# W3 modelled an ordering rule that was never deployed, and it did so
# on exactly the axis it was being used to predict: timing. It forecast
# on-time reads; production returned zero.
#
# The default is now production's rule. On-time-first is available as a
# CANDIDATE and must be labelled as one wherever it is quoted.
ORDERING_DEFAULT_IS_PRODUCTION = (
    "oldest_first is what mids_due does. Any result quoted from "
    "on_time_first is a proposal, not a description of the system")


def run(*, ticks=60, spacing=72.0, budget_fn=None, arrivals_per_tick=2.6,
        early_s=0, rotate=False, per_horizon_cap=False,
        request_duration_s=1.0, rate_limit_every=0,
        rotate_on_service=False, order_policy=ORDER_OLDEST_FIRST,
        expiry_s=600):
    """One deterministic run. Returns per-horizon outcomes and the
    request ledger.

    budget_fn(tick_index) -> total reads permitted this tick. The
    scheduler may never exceed it; the ledger proves it did not.

    `rotate_on_service` advances the rotation on ACTUAL FOLLOW-UP
    SERVICE OPPORTUNITIES rather than on the tick index. See
    `rotationHeadOnServingTicks` in the result for why that distinction
    decides whether a horizon is reachable at all.
    """
    budget_fn = budget_fn or (lambda i: 3)
    # SPACING MAY BE A SEQUENCE, CYCLED DETERMINISTICALLY. Production
    # ticks are not evenly spaced -- W1 measured 65 to 82 seconds -- and
    # a single fixed value silently decides which horizons are even
    # REACHABLE on time. At exactly 72.0s, 900/72 = 12.5, so the nearest
    # attainable lags are 864 and 936 and both miss the +-30 band; at
    # 65, 76 and 82 the same horizon is reachable. A conclusion drawn
    # from one spacing is a conclusion about that spacing.
    gaps = (spacing,) if isinstance(spacing, (int, float)) \
        else tuple(spacing)
    obs, now, ledger = [], 0.0, []
    served = {h: {"on_time": 0, "late": 0} for h in HORIZONS}
    horizon_first_served = {h: None for h in HORIZONS}

    # ── measurement, separate from the allocation ────────────────────
    # DEMAND is counted before any budget is applied, so it is a real
    # denominator and not the tautology the production worker recorded
    # in W1 (fu_due computed after the LIMIT).
    demand_ticks = {h: 0 for h in HORIZONS}       # (tick, obs) pairs
    demand_obs = {h: set() for h in HORIZONS}     # distinct observations
    attempts = {h: 0 for h in HORIZONS}
    head_on_serving = {h: 0 for h in HORIZONS}
    # A TICK INSIDE THE BAND IS NOT A REQUEST COMPLETING INSIDE IT.
    # The first counts scheduling chances; the second subtracts the
    # request's own duration from them. They are reported apart.
    tick_in_band = {h: set() for h in HORIZONS}
    tick_in_band_completable = {h: set() for h in HORIZONS}
    # AND THE ONLY ONE THAT CAN BECOME AN OUTCOME: a tick inside the
    # band that was also a tick able to issue a follow-up at all.
    serving_tick_in_band = {h: set() for h in HORIZONS}
    serving_ticks = 0
    service_ops = 0
    arrival_credit = 0.0
    expired = set()
    expired_by_h = {h: 0 for h in HORIZONS}
    newly_eligible = set()

    for i in range(ticks):
        now += gaps[i % len(gaps)]
        total = budget_fn(i)
        limited = rate_limit_every and (i % rate_limit_every == 0)

        # ── the allocation under test ────────────────────────────────
        if total <= 1:
            # THE CASE THE OWNER NAMED. total//2 == 0, so a static split
            # starves one side. Alternate instead: the single read goes
            # to sampling on even ticks and follow-ups on odd ones, so
            # over any two ticks each side gets one.
            fu_reserve = 1 if (i % 2) else 0
            samp = total - fu_reserve
        else:
            fu_reserve = max(1, total // 2)
            samp = max(1, total - fu_reserve)
        if limited:
            fu_reserve = max(0, fu_reserve // 2)

        spent = 0
        # sampling pass: each read creates an observation.
        # FRACTIONAL ARRIVALS, accumulated deterministically. int() on
        # the per-tick rate silently quantised intake: 1.64, 1.23 and
        # 0.82 arrivals/tick all truncate to 1, so an intake sweep
        # produced identical rows and looked like intake did not matter.
        arrival_credit += arrivals_per_tick
        n_new = min(samp, int(arrival_credit))
        arrival_credit -= n_new
        for _ in range(n_new):
            if spent >= total:
                break
            obs.append(Observation(now))
            spent += 1

        # ── follow-up pass ───────────────────────────────────────────
        fu_budget = min(fu_reserve + max(0, samp - n_new), total - spent)

        # Demand and timing opportunities, measured for EVERY horizon
        # whether or not the allocation loop ever reaches it.
        cands_by_h = {}
        for h in HORIZONS:
            c = [o for o in obs if eligible(now, o, h, early_s=early_s,
                                            expiry_s=expiry_s)]
            if order_policy == ORDER_ON_TIME_FIRST:
                # THE CANDIDATE RULE. Among eligible tasks, those still
                # inside the tolerance band go before those that can
                # only ever be late recoveries.
                c.sort(key=lambda o: (not on_time(now - o.t0, h), o.t0))
            else:
                # PRODUCTION: ORDER BY observed_at, oldest first. Under
                # overload this always selects the task nearest its
                # expiry, so every completion is a recovery.
                c.sort(key=lambda o: o.t0)
            cands_by_h[h] = c
            demand_ticks[h] += len(c)
            for o in c:
                demand_obs[h].add(id(o))
                # counted ONCE, at first eligibility -- never re-counted
                # while it waits, which was the defect in summing fu_due
                newly_eligible.add((h, id(o)))
            for o in obs:
                if h in o.done:
                    continue
                if on_time(now - o.t0, h):
                    tick_in_band[h].add(id(o))
                    if on_time(now - o.t0 + request_duration_s, h):
                        tick_in_band_completable[h].add(id(o))
                    if fu_budget > 0:
                        serving_tick_in_band[h].add(id(o))

        order = list(HORIZONS)
        if rotate:
            # ROTATING START. Without it the first horizon in the list
            # claims the whole budget every tick and the rest are
            # deterministically starved -- the W1 shape: 10 reads at
            # 60s, zero at 300s, 900s and 3600s.
            #
            # WHICH COUNTER DRIVES THE ROTATION IS NOT COSMETIC. Keying
            # it to the tick index aliases against the budget=1 parity
            # rule above: follow-ups run only on odd ticks, so only odd
            # values of i % 4 are ever used and two of the four horizons
            # are never at the head of the order. Keying it to actual
            # service opportunities cannot alias, because it advances
            # exactly once per tick that can serve anything.
            k = (service_ops if rotate_on_service else i) % len(order)
            order = order[k:] + order[:k]
        if fu_budget > 0:
            serving_ticks += 1
            service_ops += 1
            head_on_serving[order[0]] += 1

        cap = max(1, fu_budget // len(HORIZONS)) if per_horizon_cap \
            else fu_budget

        for h in order:
            if fu_budget <= 0:
                break
            taken = 0
            # ON-TIME OPPORTUNITIES FIRST. Among eligible observations
            # for this horizon, those still inside the tolerance band
            # are served before those that can only be late recoveries.
            for o in cands_by_h[h]:
                if fu_budget <= 0 or taken >= cap or spent >= total:
                    break
                lag = now - o.t0 + request_duration_s
                o.done[h] = lag
                served[h]["on_time" if on_time(lag, h) else "late"] += 1
                attempts[h] += 1
                if horizon_first_served[h] is None:
                    horizon_first_served[h] = i
                fu_budget -= 1
                taken += 1
                spent += 1

        # EXPIRY, counted once per task at the moment it passes its
        # recovery deadline unread. A task counted as expired is never
        # counted again, and it is never counted as new demand.
        for o in obs:
            for h in HORIZONS:
                if h in o.done or (h, id(o)) in expired:
                    continue
                if now - o.t0 > h + expiry_s:
                    expired.add((h, id(o)))
                    expired_by_h[h] += 1

        ledger.append({"tick": i, "total": total, "spent": spent,
                       "within": spent <= total})

    return {
        "ticks": ticks,
        "spacing": spacing,
        "observations": len(obs),
        "served": served,
        "horizonFirstServedAtTick": horizon_first_served,
        "starvedHorizons": [h for h, v in horizon_first_served.items()
                            if v is None],
        "ledger": ledger,
        "anyTickOverBudget": [l for l in ledger if not l["within"]],
        # ── the per-horizon accounting the owner asked to see apart ──
        "eligibleDemandTickOpportunities": dict(demand_ticks),
        "eligibleDemandDistinctObservations": {
            h: len(v) for h, v in demand_obs.items()},
        "allocatedAttempts": dict(attempts),
        "onTimeResults": {h: served[h]["on_time"] for h in HORIZONS},
        "lateResults": {h: served[h]["late"] for h in HORIZONS},
        # A tick falling inside the band is a chance to read; a request
        # completing inside it is an outcome. Never the same number.
        "observationsWithATickInBand": {
            h: len(v) for h, v in tick_in_band.items()},
        "observationsWhereThatTickCouldAlsoComplete": {
            h: len(v) for h, v in tick_in_band_completable.items()},
        "observationsWithASERVINGTickInBand": {
            h: len(v) for h, v in serving_tick_in_band.items()},
        # THE ALIASING DIAGNOSTIC. A horizon that is never at the head
        # of the order on a tick that can serve anything is not losing
        # to capacity; it is unreachable by construction.
        "elapsedMinutes": round(now / 60.0, 2),
        "tasksNewlyEligible": len(newly_eligible),
        "tasksExpiredUnread": sum(expired_by_h.values()),
        "expiredByHorizon": dict(expired_by_h),
        "orderPolicy": order_policy,
        "ticksWithFollowUpBudget": serving_ticks,
        "rotationHeadOnServingTicks": dict(head_on_serving),
        "horizonsNeverAtRotationHead": [
            h for h, n in head_on_serving.items() if n == 0],
        "notEvidenceAboutMarkets": (
            "this exercises the allocation rule only. Nothing here "
            "observes a venue and no output may be quoted as a "
            "measurement of market behaviour"),
    }
