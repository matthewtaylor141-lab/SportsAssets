# `bettor-probe.yml` — three claimed safety properties that do not hold

**NOT FOR USE** until repaired. The live probe of 2026-09-22 was run with
the previously reviewed `render-ops` operator sequence instead.

I presented three "structural guarantees" for this workflow. A reviewer
asked for each to be verified rather than asserted. **None of the three
is established, and one of them is plainly false.** The claims are
recorded here beside what the file actually does.

---

## P1. "One authorization cannot arm twice" — FALSE

What I wrote in the file:

```yaml
concurrency:
  # ONE PROBE AT A TIME, EVER. A second dispatch waits; it does not run
  # beside the first and arm over its allowance.
  group: bettor-probe
  cancel-in-progress: false
```

`cancel-in-progress: false` makes a second dispatch **queue and then
run**. Queuing is not preventing. The second run reaches `obs-arm` and
arms a second probe with a second identity and a fresh allowance —
exactly the thing the authorization forbids.

There is **no idempotency token** anywhere in the workflow: nothing
records that an authorization has been consumed, and nothing refuses a
repeat. The comment describes an intention, not a mechanism.

**What a fix requires.** A durable single-use token — for example, the
approved SHA plus a nonce written into `ingestion_state` and checked
under the same `SELECT ... FOR UPDATE` transaction that arms, refusing
if the token is already present. The check and the arm must be one
transaction, or two dispatches race it.

## P2. "`expect_sha` checks the actual running services" — NOT ESTABLISHED

```bash
LIVE=$(get "/services/$ID/deploys?limit=20" \
       | jq -r '[.[]?.deploy | select(.status=="live")][0].commit.id // empty')
```

Two unverified assumptions:

1. **Ordering.** It takes index `[0]` of the filtered list. The Render
   API's ordering is not part of any contract I checked. It *appears*
   newest-first in every observation made today, but "appears" is not
   "established", and a silent reordering would compare against the
   wrong deploy while reporting success.
2. **A deploy record is not a running process.** `status == "live"` is a
   statement about the deploy, not about what the instance is currently
   executing. Mid-rollout, after a manual restart, or with an instance
   that failed to pick up a new image, the two can differ.

**What a fix requires.** Read the service object's own
currently-deployed commit field rather than reconstructing it from the
deploy list, and additionally confirm the running code from the worker's
own output — the loop already logs its version and effective config, and
that line comes from the process itself.

## P3. "Stop verification follows cleanup" — INVERTED

The verification of the shutdown (acquisition halted within 58 s, the
transport closed within 90 s) lives **inside** the main step. The
`if: always()` cleanup that guarantees `obs-stop` is a **later,
separate** step.

So on any failure path before the main step reaches its own `obs-stop` —
a preflight failure, a psql timeout, a cancelled run — the cleanup stops
observation and **nothing verifies the shutdown**, because the code that
would verify it exited with the step. The verification is skipped
precisely in the cases where it matters most.

**What a fix requires.** The cleanup must run first and the verification
must be its own `if: always()` step that runs after it, reading the
counters from the database rather than from variables held in a step
that may never have executed.

---

## What was used instead

The `render-ops` operator sequence, which was reviewed previously and
whose every state change is an explicit, separately authorized dispatch
carrying `confirm=DO`:

```
sql obs-budget      read and preserve the prior probe
sql obs-state       confirm stopped
sql pause-state     confirm trading paused
deploys / services  confirm both services' running SHA
logs                confirm effective config FROM THE WORKER ITSELF
sql obs-arm  DO     arm once
sql obs-budget      read back before running
sql obs-run  DO     start
sql obs-budget      watch
sql obs-stop DO     stop
sql obs-budget      verify acquisition halted
```

Arming is a human-authorized dispatch, so "cannot arm twice" is enforced
by the authorization itself rather than by a property of a script I
wrote and then mis-described.
