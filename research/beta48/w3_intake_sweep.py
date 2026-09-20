from sportsassets import bettor_schedule_sim as sim
SPACING, TICKS = 69.0, 240
BASE_ARRIVALS = 1.43 / 0.87     # measured intake / measured ticks per min

def budget(floor):
    def fn(i):
        pacing = 1.0 + 4.7 * ((i % 10) / 9.0)
        return max(floor, int(10 * min(1.0, 1.0 / pacing)))
    return fn

print("SIMULATION -- allocation rule only, observes no venue.")
print("Repair candidate: floor=1 (original allowance) + on-time-first ordering")
print("                  + rotation + per-horizon cap + early eligibility\n")
print("%-9s %8s %9s %9s %9s %10s %9s %8s" % (
    "intake x","obs/min","tasks/min","done/min","onTime/min","onTimeCov%","exp/min","req/tick"))
for f in (1.0, 0.8, 0.6, 0.5, 0.4, 0.3, 0.2):
    r = sim.run(ticks=TICKS, spacing=SPACING, budget_fn=budget(1),
                arrivals_per_tick=BASE_ARRIVALS*f, early_s=30, rotate=True,
                per_horizon_cap=True, rotate_on_service=True,
                order_policy=sim.ORDER_ON_TIME_FIRST)
    m = r["elapsedMinutes"]
    on = sum(r["onTimeResults"].values()); late = sum(r["lateResults"].values())
    tasks = r["tasksNewlyEligible"]
    reqs = sum(l["spent"] for l in r["ledger"])
    print("%-9.2f %8.2f %9.2f %9.2f %10.2f %10.1f %9.2f %8.2f" % (
        f, r["observations"]/m, tasks/m, (on+late)/m, on/m,
        100.0*on/max(1,tasks), r["tasksExpiredUnread"]/m, reqs/TICKS))
print()
print("Per-horizon on-time at intake x1.00 and x0.30 (repair candidate):")
for f in (1.0, 0.3):
    r = sim.run(ticks=TICKS, spacing=SPACING, budget_fn=budget(1),
                arrivals_per_tick=BASE_ARRIVALS*f, early_s=30, rotate=True,
                per_horizon_cap=True, rotate_on_service=True,
                order_policy=sim.ORDER_ON_TIME_FIRST)
    print("  x%.2f  " % f + "  ".join(
        "%ds: att=%d on=%d" % (h, r["allocatedAttempts"][h], r["onTimeResults"][h])
        for h in sim.HORIZONS) + "   starved=%s" % (r["starvedHorizons"] or "none"))
