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
quoted as a measurement of anything but the scheduler.
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


def eligible(now, obs, horizon, *, early_s):
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
    return (horizon - early_s) <= lag <= (horizon + 600)


def run(*, ticks=60, spacing=72.0, budget_fn=None, arrivals_per_tick=2.6,
        early_s=0, rotate=False, per_horizon_cap=False,
        request_duration_s=1.0, rate_limit_every=0):
    """One deterministic run. Returns per-horizon outcomes and the
    request ledger.

    budget_fn(tick_index) -> total reads permitted this tick. The
    scheduler may never exceed it; the ledger proves it did not.
    """
    budget_fn = budget_fn or (lambda i: 3)
    obs, now, ledger = [], 0.0, []
    served = {h: {"on_time": 0, "late": 0} for h in HORIZONS}
    horizon_first_served = {h: None for h in HORIZONS}

    for i in range(ticks):
        now += spacing
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
        # sampling pass: each read creates an observation
        n_new = min(samp, int(arrivals_per_tick) + (1 if i % 2 else 0))
        for _ in range(n_new):
            if spent >= total:
                break
            obs.append(Observation(now))
            spent += 1

        # ── follow-up pass ───────────────────────────────────────────
        fu_budget = min(fu_reserve + max(0, samp - n_new), total - spent)
        order = list(HORIZONS)
        if rotate:
            # ROTATING START. Without it the first horizon in the list
            # claims the whole budget every tick and the rest are
            # deterministically starved -- the W1 shape: 10 reads at
            # 60s, zero at 300s, 900s and 3600s.
            k = i % len(order)
            order = order[k:] + order[:k]

        cap = max(1, fu_budget // len(HORIZONS)) if per_horizon_cap \
            else fu_budget

        for h in order:
            if fu_budget <= 0:
                break
            taken = 0
            # ON-TIME OPPORTUNITIES FIRST. Among eligible observations
            # for this horizon, those still inside the tolerance band
            # are served before those that can only be late recoveries.
            cands = [o for o in obs if eligible(now, o, h, early_s=early_s)]
            cands.sort(key=lambda o: (not on_time(now - o.t0, h),
                                      o.t0))
            for o in cands:
                if fu_budget <= 0 or taken >= cap or spent >= total:
                    break
                lag = now - o.t0 + request_duration_s
                o.done[h] = lag
                served[h]["on_time" if on_time(lag, h) else "late"] += 1
                if horizon_first_served[h] is None:
                    horizon_first_served[h] = i
                fu_budget -= 1
                taken += 1
                spent += 1

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
        "notEvidenceAboutMarkets": (
            "this exercises the allocation rule only. Nothing here "
            "observes a venue and no output may be quoted as a "
            "measurement of market behaviour"),
    }
