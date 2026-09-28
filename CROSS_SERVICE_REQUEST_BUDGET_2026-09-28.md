# The cross-service venue request budget — a concrete plan

**Owner requirement:** *"The discovered process-local pacer also needs a concrete cross-service request-budget plan that preserves the protected worker. Do not assume the two services coordinate because both have a pacer."*

---

## 0 · A correction to my own first draft, before anything else

I wrote sections 1–6 below assuming **both lanes use `venue_pace`** and therefore both present ≈2.86 req/s. **That is wrong, and I found it by reading the pinned worker rather than reasoning about it.** Two facts change the whole plan:

**The protected worker does not use `venue_pace` at all.** It uses `bettor_universe_probe.Pacer`, configured by `configured_pace()`:

| | our lane (`ext_pinnacle_loop`) | protected worker (`bettor_live_loop`, `f5d1c05`) |
|---|---|---|
| Mechanism | `venue_pace.pace()` — threading gate | `bettor_universe_probe.Pacer` — asyncio, `_next_at` |
| Rate | `MIN_GAP_S 0.35` → **≈2.86 req/s** | `PROBE_MAX_RPS 0.25`, concurrency 1 → **≈0.25 req/s** |
| Demonstrated envelope | not stated in those terms | **0.285 req/s**, from 61,178 reads, densest 1 s window = 1 request |
| Settable by env | no | yes — `BETTOR_PROBE_MAX_RPS`, `above_demonstrated_envelope` flags it |

**So the worker consumes roughly a tenth of what I assumed.** Against an observed ≈3 req/s account threshold, the arithmetic is `3.0 − 0.25 ≈ 2.75` available to us, against our current 2.86 — near parity, a mild overrun, not the 5.7-versus-3 emergency the draft described.

**This matters because Phase 1 as drafted would have been actively harmful.** It proposed tripling our gap from 0.35 s to 1.0 s to reserve 2.0 req/s for the worker. The worker does not want 2.0 req/s and never asked for it. Tripling our gap against a reservation ten times too large would have made staleness dramatically worse — our own processing delay is already the larger half of the problem at 28.7 s median against a 30 s limit — **to solve a contention problem of a size I had not measured.** That is precisely the error the owner's instruction warned about: *do not assume the two services coordinate because both have a pacer.* I assumed something worse — that both have the *same* pacer.

**And Option B does not currently exist.** The worker DOES compute `pacer.telemetry()` and put it in its selection dict beside `configured_pace()` — but that dict is returned to a caller, and I can find no write of it to `ingestion_state`. It is the same shape as the two defects already fixed this session (the servicing digest, then `odds_freshness`): a measurement computed and dropped at the next boundary. So reading the worker's consumption from a shared artefact **requires a worker edit after all**, which the pin forbids.

**Revised recommendation, replacing Phase 1 below:** the reservation is **0.3 req/s** (the worker's demonstrated 0.285 rounded up), not 2.0. That puts our budget at ≈2.7 req/s and our gap at **0.37 s** — a 6% increase from 0.35 s, not a 186% one. Small enough to ship without waiting on the levers' measured effect, and it removes the overrun. Everything in section 2 about preserving the worker still holds; the numbers in Phase 1 do not.

---

## 1 · What is actually true today

| | |
|---|---|
| `venue_pace.MIN_GAP_S` | **0.35 s** → ≈2.86 req/s per process |
| Scope of the gate | **`threading.Condition` + module globals `_last`, `_busy`, `_normal`, `_priority`** — process-local, full stop |
| 429 circuit | `PENALTY_MULT 2.0` for `PENALTY_S 600` after any 429 **that this process saw** |
| Priority starvation bound | `PACE_PRIORITY_BURST 12` |
| Venue behaviour | 429s above **≈3 req/s**, and the limit is **per account**, not per process |

**The arithmetic that matters — as corrected in section 0.** Two processes each honouring 0.35 s *would* present up to ≈5.7 req/s to an account limited near 3. But the worker does not honour 0.35 s; it runs its own pacer at ≈0.25 req/s. The real combined figure is **≈3.1 req/s against a ≈3 threshold** — a mild overrun, and the gate is doing its job perfectly within each process. Nothing in either process can observe the other's rate, and that remains true regardless of the magnitude.

**And the two lanes are separate Render services.** `ext_pinnacle_loop` runs in the API service; `bettor_live_loop` is the protected worker, pinned at `f5d1c05`, in its own service with its own container, its own interpreter, and therefore its own `_last = 0.0`.

**The 429 circuit does not help across the boundary either — and this is the sharper problem.** When the venue 429s, only the process that *received* the 429 doubles its gap. The other keeps reading at 0.35 s. So under contention the well-behaved process backs off and the other one consumes the headroom it just freed. Back-off without shared state is not merely ineffective; it is **unfair in the wrong direction**.

**What I have not established, and will not assert:** whether the venue's limit is enforced per API key, per account, or per source IP. The observed 429 threshold (~3 req/s) is consistent with all three. This matters because a per-key limit could be sidestepped by separate keys, and I have no evidence that is permitted. **Not a recommendation — a question for the venue.**

---

## 2 · The constraint that rules out the obvious designs

> **The protected worker stays unchanged (pinned `f5d1c05`).**

That is not a preference I can trade away for a cleaner design, and it eliminates every plan whose first step is "add a shared limiter to both services":

| Design | Why it is out |
|---|---|
| Shared Redis/Postgres token bucket in both services | Requires editing the protected worker |
| A limiter sidecar both call | Same |
| Raise `MIN_GAP_S` in both | Same |
| Route all venue traffic through one proxy service | Same — the worker's egress would have to change |

**So the only admissible plans change exactly one side: ours.** The worker keeps its ~2.86 req/s. We fit underneath whatever is left.

---

## 3 · The plan

### Phase 0 — Measure the worker's actual rate before budgeting against a guess *(no code in either service)*

`venue_pace` already counts claims and wait per lane, process-wide since import. The worker is a separate process, so we cannot read its counters — **but the venue's own 429s and our own read latencies are shared evidence.**

The measurement, from our side only:

1. `lat["venue_requests"]` per cycle — already implemented and now persisted on the heartbeat.
2. 429 incidence on our reads, and the `_penalty_until` transitions, from the refusal ledger.
3. `venue_pace.stats()` deltas for our own queue wait — if our wait rises while our own claim rate is flat, we are queueing behind the **venue**, not behind ourselves, which is the signature of external contention.

**What this can establish:** whether contention is real and roughly how much headroom we actually have. **What it cannot:** the worker's exact rate. That needs either the worker's own telemetry (an edit) or a venue-side rate report (a request to the venue). **State it as unknown until then.**

### Phase 1 — A static reservation, ours alone *(our service only)*

Set our effective gap from a declared **account budget** and a declared **worker reservation**, both constants in our code:

```
ACCOUNT_BUDGET_RPS       = 3.0    # observed 429 threshold, NOT a published figure
WORKER_RESERVATION_RPS   = 2.0    # what we leave the protected worker untouched
OUR_BUDGET_RPS           = ACCOUNT_BUDGET_RPS - WORKER_RESERVATION_RPS   # 1.0
OUR_MIN_GAP_S            = 1.0 / OUR_BUDGET_RPS                          # 1.0 s
```

**This is a real cost and I am not going to hide it.** Our gap roughly triples, from 0.35 s to 1.0 s. Since our own processing delay is the larger half of the staleness problem (28.7 s median against a 30 s limit), tripling the per-read gap makes staleness **worse** unless the levers already landed absorb it. That is why the ordering matters:

- Lever A (skip already-stale) removes reads entirely — the saving scales with the gap, so it gets *more* valuable at 1.0 s.
- Lever C (refuse the duplicate instrument) removes reads entirely — likewise.
- Lever B (freshest first) reorders — unaffected by the gap.

**So Phase 1 must not ship until the levers' effect is measured.** If skipping and deduplication cut venue reads per cycle by enough, a 1.0 s gap costs less total delay than 0.35 s did with the wasted reads. If they do not, Phase 1 trades a rate-limit risk for a staleness certainty, and **that trade needs to be made explicitly rather than discovered.**

### Phase 2 — An observed budget, still ours alone *(our service only)*

Replace the static reservation with one derived from evidence:

- On a 429, apply the existing penalty **and** durably record it (`ingestion_state`), so a restart does not forget that the account was overrun. Today the penalty lives in a module global and dies with the process — a crash-loop resets the back-off every restart.
- Read that row at startup and resume the penalty if it has not expired. **This is the one piece I would ship first**, because it is small, it is entirely ours, and it fixes a real fail-open: a restarting process currently begins at full rate immediately after the account was rate-limited.
- Adapt `OUR_BUDGET_RPS` downward on sustained 429s and recover it slowly. Additive-increase/multiplicative-decrease, with the floor at one read per cycle so the lane never silently stops.

### Phase 3 — Shared state, and the honest precondition

A true account-wide budget needs both services to consult one counter. The ONLY way to do that without editing the worker:

**Option A — the worker is unpinned by the owner.** Then a shared Postgres token bucket in both. Requires an explicit owner decision to change `f5d1c05`, and that is not mine to make or to assume.

**Option B — infer the worker's consumption from a shared artefact it already writes. CHECKED, AND IT DOES NOT.** The worker computes `pacer.telemetry()` and `configured_pace()` into its selection dict (line 1721–1722 of the pinned module) — measured starts, grants and waited seconds, exactly what a budget would need. But that dict is returned to a caller and I can find no write of it to `ingestion_state`; the worker reads `bettor_live_observation` and writes no telemetry row. So the numbers exist for the duration of one call and are then discarded — the same pattern as the servicing digest and `odds_freshness`, both fixed this session. **Option B therefore requires a worker edit, which the pin forbids.** Ruled out, on evidence rather than on assumption.

**Option C — ask the venue for a higher documented limit, or a second credential with its own bucket.** Needs an owner conversation with the venue and, per the standing constraint, **I will not request or invent credentials.**

---

## 4 · What preserves the protected worker, in every phase

- Phases 0–2 touch **only** `ext_pinnacle_loop` and our own `venue_pace` call sites. The worker's image, pin and rate are unchanged.
- The reservation is **subtracted from ours, never from the worker's.** If the account budget turns out lower than 3 req/s, our budget shrinks to zero and our lane refuses by name before the worker gives up a single read.
- No phase raises our rate above today's. Every lever already shipped **reduces** venue reads; the counter `lat["venue_requests"]` increments in exactly one place so that claim is checkable, and a test asserts it.

---

## 5 · What I am not claiming

- **Not** that 3 req/s is the venue's real limit. It is an observed 429 threshold, and I have not found a published figure.
- **Not** that the two services currently coordinate. They do not, and the pacer's docstring says "process" for a reason.
- **Not** that the levers have reduced anything in production. That is `research/latency_effect_readback.sql`, against a deployed build, and the lane is currently evaluating **zero** candidates (`ZERO_EVALUATED__INPUT_PATH_BLOCKED`) — so there are no latency samples yet and nulls in that readback mean the upstream blocker, not a fast cycle.
- **Not** that Phase 1 is safe to ship now. It is explicitly gated on the levers' measured effect, and shipping it blind would trade a rate-limit risk for a staleness certainty.

## 6 · The step I said I would take next — taken

**Done, and it changed the plan twice.** Reading the pinned worker established (a) that it uses a different pacer at roughly a tenth of the rate I had assumed, which shrank the problem from an emergency to a 6% gap adjustment, and (b) that its request telemetry is computed and dropped rather than persisted, which rules Option B out on evidence.

**What remains genuinely unknown, and I will not guess at it:**

1. **Whether the venue's limit is per key, per account, or per source IP.** The observed ~3 req/s threshold is consistent with all three. This is a question for the venue, not an inference I can make, and it decides whether a second credential would even be legitimate — which, per the standing constraint, I will not request or invent.
2. **The worker's *actual* rate in production**, as opposed to its configured one. `BETTOR_PROBE_MAX_RPS` can raise it above the demonstrated 0.285 envelope, and `configured_pace()` flags that with `above_demonstrated_envelope` — but reading the deployed worker service's environment is the one thing I cannot do from here. **This is the one piece of UI information worth asking for: the value of `BETTOR_PROBE_MAX_RPS` on the worker service, or a redacted screenshot of that service's environment list showing whether the variable is set at all.** Not a secret, not a key — a rate number. If it is unset, the worker runs at 0.25 and the 0.37 s gap in section 0 is right. If it has been raised, our budget has to come down and I would want to know by how much before shipping anything.

**The next step I would take without asking:** ship the durable 429 penalty from Phase 2. It is small, it is entirely in our service, and it closes a real fail-open — the penalty currently lives in a module global and dies with the process, so a crash-looping process begins at full rate immediately after the account was rate-limited. That is worth fixing whatever the budget turns out to be.
