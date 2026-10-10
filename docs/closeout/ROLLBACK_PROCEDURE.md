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

**Each service goes back to its own previous, from its own history (rc6.3).**
`rollback_by_service.<svc>` decides per service, from that service's deploy
list only, never from another service's:

| Action | When | Command |
|---|---|---|
| `DEPLOY_PREVIOUS` (`from`, `to`) | the service is live on the release | its own previous live commit (`to`) only |
| `NONE` (`stay_on`) | the service is NOT on the release (`ROLLBACK_SERVICE_NOT_ON_THE_RELEASE`) | none: it stays on its current commit |
| `REFUSED` (`reason`) | its history is unreadable (also one row of the wrong shape where it decides what is live or what ran before: `ROLLBACK_DEPLOY_HISTORY_UNREADABLE`, never read past to an older commit), has no single live deploy, disagrees with `render.json`, or shows no previous live commit; or, newer than the live deploy or between it and the previous live commit, a deploy status Render does not document (`ROLLBACK_DEPLOY_STATUS_UNKNOWN`), or a served (`deactivated`) deploy newer than the live one (`ROLLBACK_SERVED_DEPLOY_NEWER_THAN_THE_LIVE_DEPLOY`) | none |

"Never served" is an allowlist of Render's own statuses: `build_failed`,
`update_failed`, `canceled`, `pre_deploy_failed`, `created`, `queued`,
`build_in_progress`, `update_in_progress`, `pre_deploy_in_progress`. Only
those are skipped as moot. Any other status (`Deactivated`, `succeeded`, an
empty string) is never read as "never served", because skipping a deploy that
did serve would send the service back past what it last ran. If such a row is
newer than the live deploy, or between it and the previous live commit, the
service is refused by name. If it is older than the previous live commit, it
is not counted as previously live. The tool and the judge use the same
allowlist, and a test pins it.

No command is ever written for a commit the service's own history does not
show it was live on before. Production evidence: approved-judge pm-acceptance
38002788631 on release 16d23450 had api and workers on 16d23450 and the market
plane on 732cc0c6 (moved back there at ~15:59Z on 2026-10-09 after RC6.1
3d5af039's Kalshi client hung it). The record wrote one command per service
from the single target 3d5af039, including a market-plane deploy of 3d5af039
that would have redeployed the hang; the plane's action is now `NONE`, stay on
732cc0c6, and no market-plane command is written. The rollback stays
`NOT_READY` while any service is off the release.

The target is usable only when, in `acc/rollback.json`:

| Fact | Field | Why |
|---|---|---|
| all three services are live on the release | `services.<svc>.live_commit` | rollback starts from a consistent release |
| all three name the SAME previous commit | `target_sha`, `services.<svc>.previous_commit` | one release goes back, not three |
| the target is an ancestor of the release | `target_is_ancestor_of_release` | it is on the release line |
| its four gates are green on its own SHA | `target_gates` (backend-tests, capital-critical, commit-guard, engine-diagnostic) | it passed the same gates |
| it runs on the release's schema | `migrations` (identical) or the upgrade-path receipt's `rollback_compatibility` = `COMPATIBLE` | migrations are never rolled back on production |
| every service's deploy command is written down, each naming that service's own previous live commit | `commands`, `rollback_by_service` | no improvisation under pressure |

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

**Run a command only when the unit is `READY`.** "The unit" is the
scorecard's `rollback_ready` unit, and `READY` means it passed. The judge
lists per-service commands in `detail.commands` even when the unit is
`NOT_READY`. Those commands are there so the gate can check each one against
its own service's history. They are not for running. Example: in packet
38002788631 (release 16d23450) the unit is `NOT_READY` because the market
plane is off the release. Its `detail.commands` still lists the api and
workers commands for 3d5af039, and neither one is run. Rolling back while the
unit is `NOT_READY` is an owner decision. It is not part of this procedure.

A `READY` unit means all three services are on the release and every one is
`DEPLOY_PREVIOUS` to the same commit (`target_sha`). If any service is off the
release, is refused, or goes back to a different commit, the unit is
`NOT_READY` and names why. When the unit is `READY`, run exactly the commands
it lists. The judge re-derives them from each service's own deploy history.
Never type a command from `target_sha`. Only a service whose action is
`DEPLOY_PREVIOUS` has a command. A service whose action is `NONE` or
`REFUSED` has none: leave it where it is. The command forms are below, in
release order (API, workers, market plane). `$T_<svc>` means
`rollback_by_service.<svc>.to` for that service:

```
gh workflow run render-ops.yml --ref claude/p0-closeout -f action=deploy-api-commit -f service=sportsassets-api -f arg=$T_api -f confirm=DO
gh workflow run render-ops.yml --ref claude/p0-closeout -f action=workers-commit-deploy -f service=sportsassets-workers -f arg=$T_workers -f confirm=DO
gh workflow run market-plane.yml --ref claude/release-api -f action=deploy-commit -f arg=$T_market_plane -f confirm=DO
```

`workers-commit-deploy` refuses unless the workers' autoDeploy is `no`
(`render-ops action=api-branch-get` reads it). Each action prints the other
service's deploys before and after, so a deploy that reached the wrong service
is visible in its own log.

## 4. Verify (read only)

1. `gh workflow run render-ops.yml --ref claude/p0-closeout -f action=deploys -f service=<svc>` for each service. A service that was `DEPLOY_PREVIOUS` shows its own `$T_<svc>` as `live`. A service that got no command still shows the commit it was on.
2. Dispatch pm-acceptance on the target SHA (`post_receipt` off). Its
   Deployment row reads every service on the target, and `deployment_health`
   in the packet shows each service's own reported commit beside Render's.
3. The PAPER history fingerprint is unchanged: a rollback changes code, never
   rows (pm-acceptance's PRE/POST receipts).

## 5. Lineage after a rollback

`claude/release-api` still names the rolled-back release, so the RELEASE
control reads `RELEASE_SHA_DIFFERS_FROM_TESTED_SHA` for the target until the
lineage is restored. Never force-push.

**Do this per service, and only for services whose action was
`DEPLOY_PREVIOUS`.** The lineage step covers the same services that section 3
rolled back. Each one uses its own target, `$T_<svc>` =
`rollback_by_service.<svc>.to`. Never apply this step to a service whose
action was `NONE` or `REFUSED`. That service stays on the commit it runs
(`stay_on` for `NONE`). The lineage commit is never deployed to it, even
though the commit carries the tree the other services went back to.

Example: in packet 38002788631, api and workers have target 3d5af039, and the
market plane is `NONE` and stays on 732cc0c6. A lineage commit with
3d5af039's tree, deployed to the plane, would put back the tree whose Kalshi
client hung it. (That packet is `NOT_READY`, so under section 3 none of its
commands are run at all.)

Section 3 runs commands only when the unit is `READY`. So every service that
went back went to one commit, `$T` = `target_sha`, and each `$T_<svc>` equals
it. Check this before you build the lineage commit. If the `$T_<svc>` values
differ (`ROLLBACK_TARGET_DIFFERS_BY_SERVICE`), there is no single tree to
restore. Stop: that is an owner decision. Otherwise, restore the lineage with
a rollback release commit whose tree is `$T`'s and whose single parent is the
branch tip:

```
git commit-tree "$T^{tree}" -p origin/claude/release-api -m "Release: rollback to $T (tree of $T) [deploy-approved]"
git push origin <new sha>:claude/release-api        # fast-forward only
```

Its four gates run on the push. Then deploy THAT commit by id, using each
service's own command form from section 3 with `arg=<new sha>`. Deploy it
only to services whose action was `DEPLOY_PREVIOUS` and whose `$T_<svc>` is
`$T`, one service at a time, in the same order. Its tree is identical to
`$T`'s, so the schema facts for those services do not change. Then dispatch
pm-acceptance on it.

## 6. What a rollback never changes

SMALL LIVE = SHADOW, Kalshi live money NOT ACTIVATED, Adriana SHADOW ONLY, no
capital authority, no limit, no credential, no setting, and no historical PAPER
row. A rollback is a code deployment of an earlier, already-gated release.
