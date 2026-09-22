# Consolidated handoff

Three things were asked for. All three are here, and one of them
changes the diagnosis.

---

## 1. Runnable observation image, manifest, SHA, approval scope

### The manifest delivery defect — real, and in two places

The declared runtime path did not exist in the image. **Two** rules had
to agree and neither did:

| | |
|---|---|
| `.dockerignore` | `research/beta48/*` removed `acceptance/` from the **build context** |
| `backend/Dockerfile` | `COPY research/beta48/*.json` is **top-level only** and never descended into `acceptance/` |

The worker would have refused to start with `MANIFEST_ABSENT` on the
first boot after deployment, while the file sat in the repository. You
were right that a repository-file check cannot see this — and it would
not have been enough to fix only the `COPY`: the build then failed with
`"/research/beta48/acceptance/incentive_manifest.json": not found`,
because the context never contained it.

Fixed narrowly — the manifest alone, not `acceptance/` (4.9 MB of
patches and suite output that has no place in a production image). A
`COPY` of a missing literal path is a **build error**, so a missing
manifest now fails the build rather than producing a worker that starts
and refuses.

### Verified by building and loading through the real startup path

```
docker build -f backend/Dockerfile -t bettor-verify:manifest .     -> OK
docker run -e BETTOR_INCENTIVE_MANIFEST=research/beta48/acceptance/incentive_manifest.json ...

cwd                : /app
resolves to        : /app/research/beta48/acceptance/incentive_manifest.json
exists in image    : True
mode switch on     : True          <- bll._incentive_mode()
man.load() ok      : True | OK     <- the real loader
worker sees path   : research/beta48/acceptance/incentive_manifest.json
```

Pinned by `test_the_manifest_path_survives_dockerignore_and_the_copy`,
which asserts both halves.

### The capture is BLOCKED — external dependency, stated precisely

The manifest in the tree is a **failed capture**: 0 programmes, and the
run would correctly return `INSUFFICIENT COVERAGE`. It is committed
because a refusal that is reviewable is better than an absence.

`gateway.polymarket.us` answers **403 to CONNECT** from this container.
Not inferred — the agent proxy's own status endpoint reports
`connect_rejected … policy denial` for that host, twice. Two attempts
were made and **neither left the network boundary**, so nothing reached
the venue.

Two routes were tried and both are closed:

| route | outcome |
|---|---|
| `incentive-manifest.yml` (written, committed) | **404 on dispatch** — `workflow_dispatch` resolves the file on the **default branch**, and this workflow is only on the release branch. Putting it on the default branch is a push to the auto-deploy branch, i.e. part of the deployment itself |
| `fetch-docs.yml` (already on the default branch) | dispatched, ran, **published nothing** — it accepts docs hosts only |

**Smallest concrete action: authorize step 1.** The merge carries
`incentive-manifest.yml` onto the default branch, after which one
dispatch captures and commits the manifest. That produces the final SHA.

### Request allowance — complete and explicit

| allowance | cap | status |
|---|---|---|
| **preflight, public** | **6** | 2 attempted, **0 reached the venue** (403 at the proxy). 4 remain |
| **run HTTP, public** | **8** | 4 manifest / 2 recheck / 2 retry, in the armed row |
| **socket connect** | **20** | separate; initial, failed and reconnect all count |
| **socket subscribe** | **40** | separate; a batch is two messages |

Preflight requests are **not free**. They are bounded in-process, and
the count is recorded inside the artifact rather than asserted beside it.

### The SHA

Current: **`47bd3d8`**. The manifest commit necessarily creates one
more; the final deployment SHA is that commit and I will report it the
moment the capture can run. It is **not** `47bd3d8`.

### Approval covers exactly three things

1. **Three-service deployment** — `sportsassets-api`, `sportsassets-workers`, `edge-shadow` all restart (all track the branch, `autoDeploy=yes`). Behaviour identical to `ba87076`: mode unconfigured, control false.
2. **Configuration** on `sportsassets-workers`.
3. **One observation run** — one ET date, one arm, one `obs-run`.

Not covered: any order, any trading-control change, any credential
movement, any second run.

---

## 2. Strategy implementation — both capabilities built and measured

### Decision-time sizing

`Policy(size_rule="EDGE_SCALED")` in `bettor_episodes.decision_time_size`.
The clip scales with the spread the book is offering, in ticks above the
policy's own minimum, bounded ×0.5 … ×2.0.

Uses **only decision-time inputs**: the book's spread. Not depth (the
corpus has none), not realised fill rates (an outcome), not a later
price. A live engine can run it at the instant it quotes.

### Capital release — the feasible alternative

`Policy(release_matched=True)`. The merge call is still unavailable, so
the substitute is implemented: sell **both** legs back through the real
ladder, paying the spread and two taker fees to convert a certain
settlement claim into cash now. It can fail partially — `_taker_exit`
sweeps the actual ladder, so a thin book leaves part of the pair
unliquidated, and that is reported.

### Measured, against the same baseline, same corpus, same assumptions

| variant | qfrac | net $ | Δ vs V0 | held cap-hrs | Δ |
|---|---|---|---|---|---|
| V0 baseline | 0.25 | −87.24 | | 754 | |
| V1 sizing | 0.00 | −86.37 | **+3.85** | 3,565 | −132 |
| V1 sizing | 0.25 | −85.35 | **+1.89** | 688 | −66 |
| V1 sizing | 0.50 | −76.82 | **−18.08** | 463 | −348 |
| V1 sizing | 1.00 | −61.64 | **+14.20** | 434 | −128 |
| V2 release | 0.00 | −91.97 | **−1.75** | 3,670 | −26 |
| V2 release | 0.25/0.50/1.00 | unchanged | 0.00 | unchanged | 0 |

**Sizing is an allocation rule, not a profitability lever.** Edge and
capital both scale with the clip, so per-capital-hour is invariant to a
uniform change. It cut held capital-hours at every queue fraction and
improved per-capital-hour at three of four — but **net P&L moved both
ways**, +14.20 to −18.08, across four clustered observations. That is
not an improvement anyone should bank, and I am not presenting it as one.

**Capital release fired twice in 470 episodes, cost $1.75, and freed 26
of 3,696 held capital-hours.** At three of four queue fractions it never
fired at all.

### The finding that changes the diagnosis

Everyone — me included — has been treating the missing merge call as the
constraint on capital velocity. The measurement says otherwise:

> **88% of committed capital-hours are RESTING behind quotes that never
> fill, not held in inventory.** 27,955 capital-hours committed against
> at most 3,696 ever held. 85% of episodes never fill at all.

Releasing matched pairs attacks the small term. **The binding constraint
on scale is the fill rate, not the netting call.** That reframes D3 from
a blocker to a second-order question, and it points the next experiment
at fills rather than at venue capabilities.

### What the engine still cannot do

A standing list, not one derived from which decisions happen to be
flagged — implementing an alternative to a missing capability does not
make the capability present.

| | |
|---|---|
| **merge / netting to cash** | still absent; the alternative is not a substitute |
| **queue position** | still unobservable; every fill figure inherits the sweep |
| **resting depth at the touch** | not in this corpus; the observation release measures it |
| **fill-conditioned profitability** | not establishable by observation at all |

---

## 3. Economic results and the remaining validation requirement

Unchanged in verdict, now with the new variants:

**No candidate qualifies.** C0, C2, C3 negative on trading alone. C4
negative with its offset **not computable** from this corpus — the
reduced episode output carries prices only, no sizes, and the reward's
denominator is a size walk. V1 and V3 do not change that.

Required-share figures remain **transferred scenarios**, not measured
hurdles: the terms come from other markets, the loss is a simulated
replay, and a qualification needs both measured **on the same markets
under the same policy** (blocker B10).

### The specific remaining execution-validation requirement

**Our own resting orders, filled or not filled, in programme markets.**
Precisely:

1. resting depth at the touch, per second, in markets carrying a live
   programme — the reward denominator. *The observation run supplies
   this.*
2. our queue position and realised fill rate at that depth — **requires
   our orders in the book.** Observation cannot supply it.
3. an authenticated `/v1/incentives/earnings` read after a period we
   earned in — ~7 business days after qualifying activity.

(1) is one authorization and ~2 days away. (2) and (3) need a funded
experiment, which is a separate authorization and is not requested here.

---

## Reproduce

```
python research/beta48/bettor_strategy_v2.py        # sizing + release
python research/beta48/bettor_management_demo.py    # ten decisions
python research/beta48/bettor_economic_verdict.py   # the verdict
python research/beta48/incentive_durability_proof.py
python research/beta48/incentive_rehearsal.py
docker build -f backend/Dockerfile -t bettor-verify .
```

Focused verification reused, not rerun: socket allowance **16 passed**,
socket + release + stream + live_loop **288 passed**, durability proof
**16/16**, rehearsal **11/11**, and the matched regression comparison
(`ba87076` 982 passed / candidate 1046 passed, identical failure set).

**No production deployment. No funded orders. No trading-control
changes. M4 and M5 unexecuted.**
