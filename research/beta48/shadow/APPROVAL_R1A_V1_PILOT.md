# Approval record — frozen substantive public-book capture, V1 pilot

Recorded BEFORE collection. Nothing in the pinned collector is altered by
this file: it is scope evidence, carried into the run's manifest, not code.

## Authorization

    INDIRECT_ISOLATION_REGIME            = DISCLOSED_RESIDUAL
    INDIRECT_BETTOR_PMUS_LOAD_ISOLATION  = NOT_ESTABLISHED
    INDIRECT_CONFOUND_MAGNITUDE          = NOT_IDENTIFIED
    AUTHORIZATION_SCOPE                  = OBSERVATIONAL_RESEARCH_ONLY
    RESERVATION                          = R1A_IDLE_START
    SCHEDULE_CHANGE_AUTHORIZED           = NONE

Indirect API and worker traffic is an **unmeasured residual**, disclosed and
carried, not controlled and not waived.

## What may and may not be said about indirect traffic

Neither claim is available from the evidence, and neither will be made:

- NOT: "indirect requests occurred during the window" — nothing observed them.
- NOT: "indirect requests were absent during the window" — nothing could have.

The honest statement is the one above: magnitude `NOT_IDENTIFIED`.

## PERMITTED INTERPRETATION

- Observed public-book behaviour under the recorded operating conditions.
- Capture quality against the frozen thresholds.
- Eligible horizon labels, per the existing target-specific gates.
- Exploratory baseline comparisons.
- Shadow decision diagnostics.

## NOT ESTABLISHED by this capture, whatever it returns

- Complete BETTOR traffic isolation.
- Causal attribution of latency or throttling.
- Standalone venue rate capacity.
- Actual fills.
- Realized trading economics.
- Durable or scalable edge.

Not authorized by this approval: trading, isolated rate-capacity
certification, model promotion, capital deployment.

## Frozen and unchanged

    COLLECTOR_CODE_SHA  bbce49dae0f20d9bad86bfc8f3322f28dc097859
    CAPTURE_SECONDS     5400
    REQUEST_INTERVAL_S  4.0
    RATE_RPS            0.25
    FROZEN_QUALITY_THRESHOLDS_SHA
        70e6ddda88ad4141ae3ed58691d8322cea4bf37a83c89efd481792c47243e7fb
    CONCURRENCY_GROUP   pmus-public-read-global
    CANCEL_IN_PROGRESS  false

No selection, pacing, horizon or quality-threshold change. No new model, no
training, no tuning. No research orders, no capital, no automatic production
promotion. mirror_live=false. Reconciliation, cancellations and
risk-reducing exits stay running and are not suspended for the capture.

## Gate, unchanged and unbypassed

Before dispatch, a COMPLETE paginated census is assembled by `run_census.py`
and supplied to the existing `venue_domain.domain_idle()`. All of
`queued`, `pending`, `requested`, `waiting` and `in_progress` count, because
GitHub reports a concurrency-held run as `pending` and the Actions status
filter cannot express `pending`. An incomplete or errored retrieval is
refused: a short list reads exactly like an idle domain.

    ACTIVE_DIRECT_CONFLICTS   = 0
    PENDING_DIRECT_CONFLICTS  = 0
    DOMAIN_AUDIT              = PASS
    thresholds_intact()["INTACT"] = True

Acquisition is then verified by a job existing with a `created_at`. A
dispatch acknowledgement is not acquisition. If another collector takes the
slot, it is not displaced and the gate is not bypassed; the changed state is
reported and the next idle moment is awaited.
