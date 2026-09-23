# THE OBSERVATION RUN DID NOT START. Here is exactly why.

Recorded 2026-09-23. Every line below is quoted from a durable record —
a psql readback through `render-ops sql`, a Render env-key listing, or
the worker's own log. Nothing here is inferred from an absence.

## WHAT WAS DONE, AND IT WAS DONE CORRECTLY

    02:34:39Z  obs-incentive      control false · max_distinct 40 (GENERAL
                                  shape) · deadline 2026-09-22T14:35:09Z
                                  (expired ~12h) · journal table absent
                                  => genuinely UNARMED. Arm exactly once.

    02:35:19Z  deploys            sportsassets-workers: live 7f76fd9 since
                                  2026-09-22T22:49:23Z. Confirmed.

    02:35:48Z  obs-arm-incentive  ARMED ONCE.
                                  probe_id     d5e9ae3d-257f-4948-a808-90d1bd3c5e48
                                  started_at   2026-09-23T02:35:48+00:00
                                  deadline_at  2026-09-24T04:35:48+00:00
                                  max_distinct 0
                                  incentive    manifest 4 / recheck 2 / retry 2
                                  socket       connect 20 / subscribe 40
                                  every *_reserved counter 0

           The deadline covers the required window end of
           2026-09-24T04:00:00Z with 35m48s to spare. The 02:10Z wait
           existed precisely to make `now + 26h` reach past that end,
           and it did.

    02:36:14Z  obs-run            bettor_live_observation = true. Readback
                                  confirmed `true`.

## WHAT HAPPENED NEXT, 21 SECONDS LATER

From the worker's own log, verbatim:

    02:36:35  bettor_live_loop: not observing
              (BUDGET_EXHAUSTED: 0 of 0 distinct-market slots reserved)
    02:36:35  bettor_live_control: DISARMED --
              bettor_live_observation set to false (BUDGET_EXHAUSTED)

At 02:42:14Z `obs-incentive` read `control false`, and the journal table
still did not exist.

## THE CAUSE, IN ONE LINE

`BETTOR_INCENTIVE_MANIFEST` is not set on sportsassets-workers.

Read directly at 02:43:24Z, `render-ops env-keys sportsassets-workers`
lists 43 keys. `BETTOR_INCENTIVE_MANIFEST` is not among them.

## THE CHAIN, EACH LINK CHECKABLE

1. `bettor_live_loop._incentive_mode()` is
   `bool(os.environ.get("BETTOR_INCENTIVE_MANIFEST", "").strip())`
   — `bettor_live_loop.py:144`.

2. With it unset, `main()` never reaches its delegation at
   `bettor_live_loop.py:1823`, so `bettor_incentive_observe.run()` is
   never called. The GENERAL loop runs instead. This is visible in the
   log: every line is `bettor_live_loop`, and the
   `delegating to BETTOR_INCENTIVE_OBSERVE...` line the delegation
   would emit never appears.

3. The general loop's budget gate reads `max_distinct` from the same
   `bettor_live_probe_state` row — `bettor_live_loop.py:1889`.

4. The INCENTIVE arm deliberately sets `max_distinct = 0`, to zero the
   general discovery loop. That is correct behaviour for the incentive
   arm and it is what the arm is supposed to do.

5. To the general loop, `max_distinct = 0` reads as
   `BUDGET_EXHAUSTED: 0 of 0`.

6. On `B_EXHAUSTED` the general loop calls `ctl.disarm()`
   (`bettor_live_loop.py:1894-1900`), which writes the control to false
   — `bettor_live_control.py:771-799`. That is also correct behaviour:
   it exists so a spent allowance survives the supervisor's restart.

So every component did exactly what it was built to do. The arm zeroed
the general loop; the general loop, being the one that was running,
correctly read that as "no allowance" and correctly shut itself down.
THE ONLY THING WRONG IS WHICH LOOP WAS RUNNING.

## WHAT THIS COST

Nothing irreversible, and no allowance:

    run HTTP            0 of 8      (read back 02:42:14Z)
    socket connects     0 of 20
    socket subscribes   0 of 40
    preflight           10 of 10 spent — untouched, no capture attempted
    venue requests      none: the general loop refused before acquiring
    arms consumed       1 of 1

The probe row remains armed and valid, with its deadline at
2026-09-24T04:35:48Z. The control is false. This is a safe idle state
and it needs no repair.

## WHAT IS REQUIRED TO START, AND WHY IT NEEDS A DECISION

One environment variable on sportsassets-workers:

    BETTOR_INCENTIVE_MANIFEST=research/beta48/acceptance/incentive_manifest.json

That path is present in the image — `backend/Dockerfile` copies exactly
that one acceptance file, narrowly and on purpose, and the build fails
if it is absent.

THE CONFLICT, STATED PLAINLY. Setting an environment variable through
`render-ops env-set` causes Render to redeploy the service. Two
standing instructions bear on that and they do not agree:

  * the deployment authorization of 2026-09-22 explicitly covers "the
    worker configuration required for this one observation run";

  * the most recent observation instruction says no additional
    deployment is authorized, and the command-centre instruction says
    not to alter the collector's configuration.

The second pair was written on the understanding that deployment was
finished. It was not: the release shipped, the manifest shipped, and
the one variable that selects the incentive loop was never set.

I am not resolving that conflict unilaterally. The run is held, the
allowance is intact, and the arm is still valid until
2026-09-24T04:35:48Z.

## THE TIMING, IF IT IS AUTHORIZED

The measurement window is [2026-09-23T04:00:00Z, 2026-09-24T04:00:00Z).
Setting the variable and letting the redeploy settle takes roughly ten
to fifteen minutes. Authorized by about 03:40Z, the window opens with
the collector already running and the measurement period is complete.
Later than that, the window is entered late and the shortfall must be
reported as a coverage gap rather than hidden.

If it is not authorized, the honest outcome is: no observation was
collected for ET date 2026-09-23, the arm expires unused at
2026-09-24T04:35:48Z, and the frozen manifest for this date is spent.

## WHAT WOULD HAVE CAUGHT THIS EARLIER

A check that the running worker is in incentive mode — not that the
release is deployed, and not that the manifest is in the image, both of
which were verified and both of which were true. The distinguishing
evidence was available the whole time and costs one log read: the
delegation logs `bettor_live_loop: delegating to BETTOR_INCENTIVE_OBSERVE_*`
on entry, and that line has never appeared.

This is the same failure this programme keeps finding: a check that
could not fail. "The release is deployed" and "the manifest is in the
image" were both true and neither could have detected that the loop
selecting them was switched off.
