# Rollback procedure (checked, not just written)

Owner directive 2E, RC6 lane E. This procedure is *checked by machine* on every
pm-acceptance run: `backend/tools/rollback_readiness.py` writes
`acc/rollback.json`, and the 14-category scorecard's Deployment unit
`rollback_ready` re-judges every fact below (its own status is not trusted).
Nothing in pm-acceptance deploys, restarts or changes anything; the commands
here are run only by a person who has decided to roll back.

## 1. What "the previous release" is

The rollback target is the commit that **ran** before the current release, read
from Render's deploy history of all three services (the newest `deactivated`
deploy older than the live one whose commit differs from it). It is not the
release commit's git parent: release 69a8a07e's parent is 93dc6f41, an interim
release commit that never ran; Render shows 7fd4574e on every service.

Each service's previous commit is read from **that service's own** deploy list
(`deploys_<svc>.json`), and each service gets its own plan in
`services.<svc>.action` (rc6.3, pm-acceptance 38002788631):

| Action | When | Command |
|---|---|---|
| `DEPLOY_PREVIOUS` | the service is live on the release | to `services.<svc>.rollback_commit` = its OWN previous live commit |
| `NONE` | the service is NOT on the release (rolling the release back does not touch it) | none: it stays on its own live commit, named `ROLLBACK_SERVICE_NOT_ON_THE_RELEASE` |
| `NO_TARGET` | on the release, but no previous live deploy, an unreadable deploy list, or a previous commit it was rolled back FROM (`ROLLBACK_PREVIOUS_COMMIT_WAS_ROLLED_BACK_FROM`: its live commit ran before that commit too, or Render's trigger says rollback) | none |

No command is ever written to a commit the service was not live on, and none
for a service the release did not touch. Why: in pm-acceptance 38002788631
(release 16d23450 on api and workers, the market plane still on 732cc0c6) one
shared target wrote `market-plane.yml deploy-commit 3d5af039` for the plane --
the RC6.1 commit the plane had hung on and been rolled back from at 15:59Z.

The target is usable only when, in `acc/rollback.json`:

| Fact | Field | Why |
|---|---|---|
| all three services are live on the release (a service left off it is `NONE` and the rollback stays NOT_READY) | `services.<svc>.live_commit` | rollback starts from a consistent release |
| every service on the release names the SAME own previous commit, not one it was rolled back from | `target_sha`, `services.<svc>.previous_commit`, `services.<svc>.previous_rolled_back_from` | one release goes back, not three |
| the target is an ancestor of the release | `target_is_ancestor_of_release` | it is on the release line |
| its four gates are green on its own SHA | `target_gates` (backend-tests, capital-critical, commit-guard, engine-diagnostic) | it passed the same gates |
| it runs on the release's schema | `migrations` (identical) or the upgrade-path receipt's `rollback_compatibility` = `COMPATIBLE` | migrations are never rolled back on production |
| every service the rollback moves has its deploy command written down, to its own previous live commit (the scorecard re-reads each service's own deploy list) | `commands` | no improvisation under pressure |

## 2. Migration compatibility (the schema stays)

Production migrations are append-only and are **not** reversed by a rollback
(`backend/migrations/rollback/*.down.sql` exist for some migrations; running one
on production is an owner decision, never part of this procedure). The previous
release therefore has to run on the NEW schema. Two ways this is proven:

* **Identical migration sets.** The release adds, removes and changes no
  migration against the target (same files, same content hashes, same
  fingerprint): the target runs on exactly the schema it ran on. Example: RC6
  (tree f3c3359a) against 69a8a07e, 224 migrations, fingerprint f442188e... on
  both.
* **The upgrade-path receipt** (capital-critical, attested,
  `tools/upgrade_path_receipt.py`), from a base whose migration set IS the
  target's: the base built by the base's own runner, representative rows
  seeded, the release's migrations applied, then for everything that existed at
  the base: no table or column dropped, no type changed, no column tightened to
  NOT NULL without a default, no default dropped from a NOT NULL column, no new
  NOT NULL column without a default, and no new constraint, unique index or
  trigger on an existing table (each named if present); and the base runner,
  re-run on the upgraded database, applies nothing and reports no
  changed-after-apply file. Example (measured locally in lane E): 7fd4574e ->
  69a8a07e adds only 316 (four new tables), PASSED, `COMPATIBLE`, 281 of 356
  base tables holding a row.

Anything else is `NOT_PROVEN_COMPATIBLE` with the blocking items named; the
rollback is then not ready until a person has decided what to do about each.

## 3. The commands (deploy by commit id, one service at a time)

Run exactly the commands in `commands` of the latest pm-acceptance packet's
`rollback.json` (a service whose command is `null` is NOT deployed: its
action is `NONE` or `NO_TARGET`). When every service is on the release they
read, with `T` = `target_sha`, in the release order (API, workers, market
plane):

```
gh workflow run render-ops.yml --ref claude/p0-closeout -f action=deploy-api-commit -f service=sportsassets-api -f arg=$T -f confirm=DO
gh workflow run render-ops.yml --ref claude/p0-closeout -f action=workers-commit-deploy -f service=sportsassets-workers -f arg=$T -f confirm=DO
gh workflow run market-plane.yml --ref claude/release-api -f action=deploy-commit -f arg=$T -f confirm=DO
```

`workers-commit-deploy` refuses unless the workers' autoDeploy is `no`
(`render-ops action=api-branch-get` reads it). Each action prints the other
service's deploys before and after, so a deploy that reached the wrong service
is visible in its own log.

## 4. Verify (read only)

1. `gh workflow run render-ops.yml --ref claude/p0-closeout -f action=deploys -f service=<svc>` for each service: the target is `live`.
2. Dispatch pm-acceptance on the target SHA (`post_receipt` off). Its
   Deployment row reads every service on the target, and `deployment_health`
   in the packet shows each service's own reported commit beside Render's.
3. The PAPER history fingerprint is unchanged: a rollback changes code, never
   rows (pm-acceptance's PRE/POST receipts).

## 5. Lineage after a rollback

`claude/release-api` still names the rolled-back release, so the RELEASE
control reads `RELEASE_SHA_DIFFERS_FROM_TESTED_SHA` for the target until the
lineage is restored. Never force-push. Restore it with a rollback release
commit whose tree is the target's and whose single parent is the branch tip:

```
git commit-tree "$T^{tree}" -p origin/claude/release-api -m "Release: rollback to $T (tree of $T) [deploy-approved]"
git push origin <new sha>:claude/release-api        # fast-forward only
```

Its four gates run on the push; deploy THAT commit by id with the commands
above (its tree is identical to the target's, so the schema facts are
unchanged), then dispatch pm-acceptance on it.

## 6. What a rollback never changes

SMALL LIVE = SHADOW, Kalshi live money NOT ACTIVATED, Adriana SHADOW ONLY, no
capital authority, no limit, no credential, no setting, and no historical PAPER
row. A rollback is a code deployment of an earlier, already-gated release.
