from sportsassets import bettor_schedule_sim as sim

# MEASURED from production, run 180 at 23:53:41Z, 60-minute span.
M = dict(intake_per_min=1.43, newly_eligible_per_min=5.85,
         completed_per_min=2.03, expired_per_min=4.02, ticks_per_min=0.87)
SPACING = 60.0 / M["ticks_per_min"]          # 69.0s
ARRIVALS = M["intake_per_min"] / M["ticks_per_min"]   # 1.64 obs/tick
TICKS = 180                                   # ~3.4 simulated hours

def budget(floor):
    # The deployed pacing arithmetic, with the floor as the variable.
    # pacing oscillates 1.0..5.7 as production did.
    def fn(i):
        pacing = 1.0 + 4.7 * ((i % 10) / 9.0)
        return max(floor, int(10 * min(1.0, 1.0 / pacing)))
    return fn

def go(floor, policy, arrivals=ARRIVALS, label=""):
    r = sim.run(ticks=TICKS, spacing=SPACING, budget_fn=budget(floor),
                arrivals_per_tick=arrivals, early_s=30, rotate=True,
                per_horizon_cap=True, rotate_on_service=True,
                order_policy=policy, request_duration_s=1.0)
    mins = r["elapsedMinutes"]
    on = sum(r["onTimeResults"].values()); late = sum(r["lateResults"].values())
    reqs = sum(l["spent"] for l in r["ledger"])
    return dict(label=label, floor=floor, policy=policy,
                newEligPerMin=round(r["tasksNewlyEligible"]/mins, 2),
                donePerMin=round((on+late)/mins, 2),
                expPerMin=round(r["tasksExpiredUnread"]/mins, 2),
                onTimePerMin=round(on/mins, 2),
                onTimePct=round(100.0*on/max(1, on+late), 1),
                reqPerTick=round(reqs/TICKS, 2),
                starved=r["starvedHorizons"],
                perH={h: (r["allocatedAttempts"][h], r["onTimeResults"][h])
                      for h in sim.HORIZONS})

print("SIMULATION (allocation rule only; observes no venue).")
print("Calibrated to measured rates: intake %.2f/min, ticks %.2f/min, spacing %.1fs"
      % (M["intake_per_min"], M["ticks_per_min"], SPACING))
print()
hdr = "%-34s %6s %8s %8s %8s %8s %8s %7s %s"
print(hdr % ("scenario","floor","newElig","done","expired","onTime","onTime%","req/tk","starved"))
rows = [
  go(2, sim.ORDER_OLDEST_FIRST,  label="A DEPLOYED (floor2, oldest-first)"),
  go(1, sim.ORDER_OLDEST_FIRST,  label="B floor1 only"),
  go(2, sim.ORDER_ON_TIME_FIRST, label="C on-time-first only"),
  go(1, sim.ORDER_ON_TIME_FIRST, label="D floor1 + on-time-first"),
]
for f in (0.75, 0.5, 0.35):
    rows.append(go(1, sim.ORDER_ON_TIME_FIRST, arrivals=ARRIVALS*f,
                   label="E intake x%.2f + D" % f))
for r in rows:
    print(hdr % (r["label"], r["floor"], r["newEligPerMin"], r["donePerMin"],
                 r["expPerMin"], r["onTimePerMin"], r["onTimePct"],
                 r["reqPerTick"], r["starved"] or "none"))
print()
print("PRODUCTION MEASURED (same units):")
print(hdr % ("  actual last 60 min", "2", M["newly_eligible_per_min"],
             M["completed_per_min"], M["expired_per_min"], 0.02, 0.8, "-", "none"))
