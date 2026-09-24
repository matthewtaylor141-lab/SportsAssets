# The command centre release — one concrete release, held until the stop

**Release commit:** see `RELEASE_SHA` below, on branch `claude/command-center`.
**Status:** PREPARED, NOT EXECUTED. Nothing in this document has been run
against production. The observation window ends at **2026-09-24T04:00:00Z**
and the release waits for it.

---

## 0. The thing that makes this release dangerous, stated first

`render-ops services`, read 2026-09-23T11:34:45Z:

```
srv-d9gcv6urnols73ce6er0  sportsassets-api      web_service        branch=claude/session-njaewf  autoDeploy=yes
srv-d9gcv6urnols73ce6erg  sportsassets-workers  background_worker  branch=claude/session-njaewf  autoDeploy=yes
srv-d9gilegn5bic73escs5g  edge-shadow           background_worker  branch=claude/session-njaewf  autoDeploy=yes  (suspended)
```

**The API and the collector share an auto-deploy branch.** Being separate
services buys nothing here: one push to `claude/session-njaewf` deploys the
web service *and restarts the collector*. That is not hypothetical — it is
what happened at 10:28:59Z today, when a deploy of the worker ended boot
`7435b23a98d049f3` after 29.8 seconds and cost the run a 135.191-second
`PROCESS_REPLACED` gap.

So the release must never touch that branch while the window is open. Every
step below is built around that.

### A worker deploy has exactly three triggers

| # | Trigger | Does this release do it? |
|---|---------|--------------------------|
| a | A push to the branch the worker tracks (`claude/session-njaewf`) | **No.** Every commit is pushed to `claude/command-center` only. |
| b | A Blueprint sync — a `render.yaml` change on that branch | **No.** `render.yaml` is untouched by this branch: `git diff c8b9704..HEAD -- render.yaml` is empty. |
| c | An explicit Render API call naming `srv-d9gcv6urnols73ce6erg` | **No.** The only deploy instrument used is `deploy-api-commit`, in which the service is a literal, not a parameter. |

### Why `deploy-api-commit` cannot reach a worker

The action (`.github/workflows/render-ops.yml`, `[R82]`):

* **ignores the `service` input entirely** and says so in the log when it is
  set to anything else;
* resolves the name `sportsassets-api`, a string literal in the workflow;
* **asserts** the resolved service's `name` is `sportsassets-api` *and* its
  `type` is `web_service`, and exits before any write if either is wrong;
* deploys by **`commitId`**, so the release commit never has to be pushed to
  a tracked branch;
* prints the worker's last three deploys **before and after** the call, so
  "no worker deploy was created" is evidence in the log rather than an
  assertion in a document.

There is no code path in that action that can POST to a background worker's
id. Read it and check.

---

## 1. RELEASE_SHA

```
RELEASE_SHA = <the head of claude/command-center at release time>
```

Pin it at release time with:

```
git rev-parse claude/command-center
```

and record the 40-hex value here before step 4. `deploy-api-commit` refuses
anything that is not a full 40-character lowercase hex sha, so an
abbreviated or stale value cannot be deployed by accident.

The command-centre change set relative to the branch point `c8b9704`:

| File | What it is |
|---|---|
| `backend/sportsassets/bettor_command_center.py` | the pure read model — five views, no I/O |
| `backend/sportsassets/api/command_center.py` | the reads, and the only place SQL is issued |
| `backend/sportsassets/bettor_evidence_store.py` | the append-only artifact table and its writer |
| `backend/sportsassets/api/app.py` | two routes behind the existing `require_command` |
| `frontend/public/command/center.{html,js,css}` | the page |
| `backend/tests/test_bettor_command_center.py` | 84 tests |
| `backend/tests/test_bettor_evidence_store.py` | 39 tests |
| `backend/tools/*` | preview, shots, reconciliation, the publisher, the export loader |
| `.github/workflows/render-ops.yml` | read-only journal statements, the evidence DDL, `deploy-api-commit` |
| `research/beta48/acceptance/*` | the incident record, the design record, this file, the journal export |

`render.yaml` is **not** on that list, and that is load-bearing.

---

## 2. The order, and why it is this order

1. **Migration first, code second.** The table is additive and the API reads
   it with a fallback, so a schema that exists before the code is harmless
   and a code that arrives before the schema would render `UNKNOWN`
   provenance for as long as the gap lasted.
2. **Publication third.** Artifacts are published from a named commit after
   the table exists.
3. **Branch repoint last, and optional.** It ends the shared-branch coupling
   permanently; it is separated so that a failure there cannot leave the
   release half-applied.

---

## 3. Step 1 — migration

```
render-ops: action=sql  service=sportsassets-db  arg=evidence-schema  confirm=DO
```

Creates `bettor_evidence_artifact` plus its two indexes, then **reads the
catalog back** and prints every column and index. A `CREATE` that returned no
error is not evidence the table is right; `information_schema` is.

Expected: 12 columns —
`id, name, kind, source_sha, content_type, digest, size_bytes, body,
published_at, note, attestation, superseded_by` — and three indexes
(`..._pkey`, `..._name`, `..._ident`).

`CREATE TABLE IF NOT EXISTS` is idempotent, so re-running it is safe.

**Rollback of step 1**

```
render-ops: action=sql  service=sportsassets-db  arg=evidence-drop  confirm=DO
```

It **refuses while any row exists**, by design. Published evidence is
append-only and superseded versions are kept deliberately; a rollback able to
erase them would be a worse outcome than whatever it was undoing. To roll the
*code* back, roll the code back (step 4's rollback) — the API degrades to the
working-tree fallback when the table is absent and says so in its provenance
line, so an orphaned empty table costs nothing.

---

## 4. Step 2 — the API deployment

```
render-ops: action=deploy-api-commit  arg=<RELEASE_SHA>  confirm=DO
```

Check in the log, in this order:

1. `note: service=... is IGNORED` if `service` was filled in at all;
2. the **worker deploys BEFORE** list;
3. `HTTP 201` and the new API deploy id, with `commit=` the first 7 of
   `RELEASE_SHA`;
4. the **worker deploys AFTER** list — **it must be identical to BEFORE**.

Then confirm the running identity:

```
render-ops: action=deploys  service=sportsassets-api
render-ops: action=deploys  service=sportsassets-workers   # unchanged
```

**Verification that the page is actually live** — open
`/command/center.html`, unlock through the existing Command sign-in, and
check that `deployed_sha` on the Live-operation view reads `RELEASE_SHA`.
In the preview that cell is `UNKNOWN`, correctly: the preview process
carries no deploy stamp and the page refuses to guess the running commit
from a working tree. Deployment is what fills it in, and a page that still
says `UNKNOWN` after this step means the deploy did not take.

**Rollback of step 2**

```
render-ops: action=deploy-api-commit  arg=<the previous API commit>  confirm=DO
```

Read the previous commit from `action=deploys service=sportsassets-api`
before step 4 and write it down. The rollback uses the same instrument, so it
carries the same guarantee: it cannot reach the worker either.

---

## 5. Step 3 — publication

From a checkout of `RELEASE_SHA` with database access:

```
python backend/tools/publish_evidence.py --dry-run          # writes nothing
python backend/tools/publish_evidence.py --from-git --confirm DO
```

`--from-git` refuses a dirty tree rather than attributing modified bytes to a
commit that does not contain them. Publication is idempotent on
`(name, digest)`: re-running it returns the existing rows instead of
manufacturing a version history that describes nothing.

Then:

```
render-ops: action=sql  service=sportsassets-db  arg=evidence-inventory
```

which lists every artifact and version with its `source_sha`, digest prefix,
size and supersession, and no bodies.

**What publication does and does not establish.** A digest proves the bytes
have not changed since they were stored. It does **not** prove those bytes
came from testing that commit: `source_sha` is publisher-supplied. The page
labels this class `RECORDED`. `ATTESTED` is reserved for artifacts carrying
all three of `ci_run`, `checked_out_sha` and `artifact_ref`, and nothing in
this release publishes one.

**Rollback of step 3**

There is none, and that is the design. Supersession is how a wrong artifact
is corrected: publish the right bytes, and the old row stays with
`superseded_by` pointing at its replacement. The run history stays auditable
because nothing is ever removed.

---

## 6. Step 4 — end the coupling (optional, after the window)

```
render-ops: action=api-branch-get                                    # read first
render-ops: action=api-branch-set  arg=<the API's own branch>  confirm=DO
render-ops: action=api-branch-get                                    # read back
```

`api-branch-set` patches **only** `sportsassets-api` — the service is a
literal there too — and prints the worker's branch before and after so the
separation is visible. After this, a command-centre push can never reach the
collector, because the collector does not watch that branch.

Until it is done, the rule stands: **nothing is pushed to
`claude/session-njaewf` while a collection window is open.**

---

## 7. What this release deliberately does not do

* It does not restart, redeploy, suspend or resume `sportsassets-workers`.
* It does not change any control row, allowance counter or deadline.
* It does not change `render.yaml`, so no Blueprint sync can occur.
* It does not activate funded trading, move credentials, or alter any
  accounting record.
* It publishes no artifact claiming `ATTESTED` provenance.
