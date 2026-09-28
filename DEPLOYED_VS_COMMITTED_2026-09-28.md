# Deployed versus committed — manifest, 2026-09-28

**The owner's requirement:** *"The current tree is newer than the gated SHA. Keep a manifest showing which fixes are deployed and which remain committed only."*

| | |
|---|---|
| **Serving build** | **`c3d0cfc303fa92e182786d40b4b27a4d05322b32`** |
| Established by | Render deploy list: `live c3d0cfc`, all others `deactivated`; `pending deploys for another commit: 0`; `autoDeploy: no` |
| Deploy id | `dep-dat6bd3tqb8s739uppcg`, `HTTP 201`, by commit id |
| Branch tip | `a487fbb` (12 commits ahead) |
| Gated | Yes — matched run against `cd01701`, all three DSNs configured |

---

## 1 · DEPLOYED and in force on `c3d0cfc`

| Fix | Evidence |
|---|---|
| **Partial-exit P&L** — `realised_on_sold()` separating proceeds, allocated basis, both fee shares, result, residual, remaining basis | 14 tests; wired into every `RESIDUAL_INVENTORY_STILL_HELD` row in `command_center` |
| **The dropped venue commission** — `executions_of` now reads `commissionNotionalCollected` / `commissionNotionalTotalCollected` via the adapter's own parser | `FEE_DISAGREES` can fire on a funded exit for the first time |
| **REDUCE bounded at the marginal level** — `select_exit` no longer refuses every multi-level REDUCE | commit `9827791`, ancestor of `c3d0cfc` |
| **`locks_a_loss` on DIRECT_EXIT and REDUCE** | was set only on `TAKE_COMPLEMENT` |
| **Servicing digest on the cycle row** — `carries_a_servicing_decision` | reads the keys `manage` actually emits |
| **Action register** with the four statuses and the enumerated venue API surface | 38 tests |
| **Book-evidence outage behaviour** — refuses by name, inventory untouched, reconciliation ungated | 10 tests (the M1 *wording* fix is NOT here — see §2) |
| **`EVENTS_PER_ODDS_FETCH` knob** | present but **INACTIVE**: defaults to `MAX_PER_CYCLE`, so no extra fetch is ever issued and the production delay is unchanged |

## 2 · COMMITTED ONLY — not in the serving build

**Exactly one commit changes code that ships in the image.** Everything else since `c3d0cfc` is research, documentation or evidence.

| Commit | Change | Ships in image? | Status |
|---|---|:--:|---|
| **`5562bfb`** | `book_currency_evidence.why_none` / `what_would_change_it` restated from `bettor_stream_currency` instead of a stale parallel account; the test that **pinned the withdrawn premise** replaced; combo payoff correction in `bettor_action_inventory` | **YES** — `workers/ext_pinnacle_loop.py`, `bettor_action_inventory.py` | **NOT DEPLOYED. Needs its own gate.** |
| `d73bb8e` | `command-verify` S4b desk-credential verification | no (workflow) | in force — it ran in this release |
| *(this change)* | `command-verify` S3 serving-SHA check repaired | no (workflow) | takes effect next rotation |
| `a487fbb`, `bbefed1`, `d78d931`, `a48e91c`, `e6ccc57`, `4ebbf2e`, `8fc0e35`, `b2119c8`, `bc6a931`, `599dbc9` | evidence, documents, research SQL, report pipeline | no¹ | committed |

¹ **One qualification I checked rather than assumed.** The Dockerfile copies `research/beta48/*.json`, so `bc6a931`'s `wk39_inputs.json` **is** an image-surface file. It is inert data with no importer, so it changes no behaviour — but "only documentation changed" would have been the wrong reason to call the image unchanged, and the *correct* reason is that the deployed SHA predates the file entirely.

**Consequence to state plainly:** the operator-facing explanation of why book currency is unavailable is **still the stale one in production**. It names sequencing and snapshot/increment identification — premises `bettor_stream_currency` had already withdrawn. The verdict it accompanies is correct; the reason given is not.

---

## 3 · The rotation's terminal result, stage by stage

The owner asked for these **separately**, not as one verdict.

| Question | Answer | Evidence |
|---|:--:|---|
| **Environment changes occurred** | **YES** | `PUT OPERATOR_PASSWORD 200`, `PUT DESK_PASSWORD 200`, `PUT SESSION_EPOCH 200`. Each step fails closed, and the run reached S2b, so all three landed. |
| **Credentials rotated** | **YES, both** | Generated on the runner (`openssl rand`), never a workflow input, masked in every log line, delivered as artifact `operator-credential` (248 bytes, 1-day retention). |
| **Sessions invalidated** | **YES** | `SESSION_EPOCH <unset, treated as 1> → 2`. Every outstanding desk, wall and control token stops verifying. The signing key was **not** rotated, so the verification workflows keep working. |
| **Intended SHA deployed** | **YES** | `deploy c3d0cfc by id HTTP 201`; deploy list shows `live c3d0cfc` with `f9f63d8` and 8 others `deactivated`; `pending deploys for another commit: 0`; `autoDeploy` still `no`. |
| **Desk sign-in passed** | **YES** | `HTTP 200`, **body `ok: true`** (checked, because that route answers 200 with `ok:false` on a wrong password), 75-char token masked, the token opened a desk-gated read `HTTP 200`, a **deliberately wrong** password returned `ok=false`, and `/healthz` now reports `auth_all_configured: true`, `auth_not_configured: []`. |
| **Scheduled cycle completed on that build** | **NOT ESTABLISHED BY THIS RUN** | The workflow's step 22, *"External valuation — arm, cycle, and read production back"*, was **SKIPPED**. Verified separately — see §4. |

**Also true, and it is not success:** the `verify` job failed at steps 20 and 25 (both RN1X acceptance-position steps, a different lane). Those are not part of the rotation and I have not yet established whether they are pre-existing.

### The defect this investigation found — and why the 10 minutes mattered

S3 ("wait until the reviewed build is serving") **ran 60 iterations, matched nothing, and fell through in silence.** Two causes, both real:

1. **It polled `/api/health`, which does not exist.** The service publishes `/healthz` and `/api/health/services`. Every poll took a 404 and `jq` read `"?"` sixty times.
2. **The comparison was inverted.** `/healthz` reports `commit: RENDER_GIT_COMMIT[:7]` — **seven** characters — while `case "$SERVING" in "$OPERATOR_COMMIT"*)` requires the served value to *begin with the full 40-hex SHA*. A 7-char prefix can never satisfy that, so the check would have failed even against the right URL.

So **the step that exists to prove serving-SHA identity has never worked.** The release is still established — by S3b reading the live commit from Render's own deploy list (which fails closed) and by the sign-in working against the new build — but I am **not** inferring it from the health endpoint, because the health endpoint said nothing.

Repaired in this change: the real endpoint, the comparison in the direction that is true when a short commit identifies a long one, a minimum length so `""` cannot prefix-match, and **an explicit warning when the readback is unconfirmed** — because for as long as this defect existed, "nothing printed" read as success.
