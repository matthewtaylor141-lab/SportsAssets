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

THIS IS AN EXECUTION FAILURE, NOT A SET OF CORRECT PARTS.

Each component behaved as specified in isolation. That is not a defence
and it is not the finding. The SYSTEM was asked to collect an ET date of
order-book depth and it collected NOTHING -- zero frames, zero rows,
no journal table. "Every component worked" is a sentence that can be
true of a system that delivered nothing, which is exactly why it is not
an acceptable description of one.

The failures were mine and they were three:

  1. THE CONFIGURATION WAS NEVER SET. The release was deployed on
     2026-09-22 and reported as ready to run. The one variable that
     selects the incentive observer was never set on the worker, and I
     did not check for it before reporting readiness.

  2. THE READINESS CHECK COULD NOT FAIL. I verified "the release is
     deployed" and "the manifest is in the image". Both were true. Neither
     could detect that the loop which reads them was switched off. A check
     that cannot fail is not a check, and this programme has now produced
     that same defect more than once.

  3. STARTUP WAS NOT VERIFIED AGAINST PERSISTED DATA. I set the control
     and treated the readback of `true` as progress. The only evidence
     that matters is a persisted frame, and there was never one.

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

---

## RESOLUTION, AND A TIMING CORRECTION THAT MATTERS

Authorized 2026-09-23. `BETTOR_INCENTIVE_MANIFEST` set on
sportsassets-workers to

    /app/research/beta48/acceptance/incentive_manifest.json

at 10:15:05Z (readback: 55 chars, which is that path exactly). Render
restarts the service on an env change. One variable and no other: the
ET date falls back to the manifest's own `2026-09-23`
(`bettor_incentive_observe.py:131`), so no second variable is needed.

THE TIMING IS NOT WHAT THE APPROVAL ASSUMED, AND SAYING SO IS THE POINT.
The blocker was put to the owner at about 02:57Z, when starting
immediately would still have covered the whole window. The session then
sat idle awaiting the answer and the answer arrived at 10:15Z.

    window opens        2026-09-23T04:00:00Z
    answer received     2026-09-23T10:15Z
    already elapsed     6h 15m of 24h, unobserved and unrecoverable

So the option chosen ("run the window") is being executed, but its
premise no longer holds. What this run can produce is AT MOST about
17h45m of a 24h ET date, and the first 6h15m is a coverage gap that
existed before the collector ever opened a socket.

This is recorded here rather than absorbed quietly, because "the window
ran" and "the window was covered" are different claims and only the
first one will be true. Every coverage figure from this run must be
stated against the full 24h denominator, with the pre-start gap shown
as part of it. The reward arithmetic is scored per scoring instant over
the whole ET date; a run that observed three quarters of those instants
cannot report a full-day figure.

THE ARM IS STILL VALID. probe_id d5e9ae3d-257f-4948-a808-90d1bd3c5e48,
deadline 2026-09-24T04:35:48Z, which still covers the fixed end at
2026-09-24T04:00:00Z. No re-arm is needed or permitted, and no counter
has moved: HTTP 0 of 8, connects 0 of 20, subscribes 0 of 40.

---

## AND A FOURTH FAILURE, FOUND WHILE FIXING THE THIRD

Setting the variable was not enough, twice over:

    10:15:05Z  env-set     HTTP 200. render-ops prints "Render redeploys
                           the service on an env change".
    10:21:18Z  env-keys    BETTOR_INCENTIVE_MANIFEST (55 chars) -- STORED.
    10:22:20Z  worker log  still `bettor_live_loop` general mode.
    10:23:13Z  deploys     NO new deploy row. The auto-redeploy the tool
                           promised never happened.
    10:23:34Z  restart     HTTP 200, accepted.
    10:25:45Z  worker log  STILL general mode after the restart.
    10:26:55Z  deploy      dep-dapqirrbc2fs73bms6fg, commit=7f76fd9.

TWO SEPARATE THINGS THAT LOOK LIKE "APPLYING CONFIGURATION" AND ARE NOT:

  * an env-set returns 200 and the key lists, and the RUNNING PROCESS
    still holds its old environment;
  * a restart returns 200 and cycles the process, and it comes back with
    the environment of the DEPLOY it belongs to -- which was created
    before the variable existed.

Only a DEPLOY re-reads the service's environment. `claude/session-njaewf`
HEAD is 7f76fd90ad6862b7440abafd024d04e1e89c1b5f, which is 7f76fd9, the
approved release already live -- so this deploy rebuilds the SAME COMMIT
with the new environment and the deployed identity does not move.

THE RULE THIS ESTABLISHES, and it is the same rule as the third failure
above: a stored configuration value is not a configured process, and a
successful API call is not an applied change. The only acceptable
evidence that the worker is in incentive mode is its own log line
`bettor_live_loop: delegating to BETTOR_INCENTIVE_OBSERVE_*`, and the
only acceptable evidence of collection is a persisted frame.

This is recorded because the codebase already carried this exact
warning, from 2026-09-21: "BETTOR_LIVE_LOOP=off was stored,
acknowledged, and survived a demonstrated restart without ever reaching
the process that was supposed to read it." It was written down, it was
true, and it happened again.
