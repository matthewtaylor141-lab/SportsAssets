"""ROLLBACK READINESS: THE PREVIOUS RELEASE, PER SERVICE, AND WHETHER IT CAN
BE PUT BACK ON THE NEW SCHEMA (RC6 lane E, owner directive 2E).

Production evidence (pm-acceptance 37836393458 on release 69a8a07e): the
release commit says "Previous running release 7fd4574e kept for rollback",
and Render's deploy history agrees for all three services (api, workers
and market plane: the newest deactivated deploy before the live one is
7fd4574e9ac8) -- but nothing CHECKED it: not that the three agree, not that
the target's own gates were green, not that the target's code can run on
the schema the release migrated to, and the deploy commands lived in
nobody's packet. 69a8a07e's git parent is 93dc6f41, an interim release
commit that never ran: the rollback target is what RAN, not the parent.

What this file records (acc/rollback.json), from files the pm-acceptance
judge collected -- it calls nothing and deploys nothing:

  * per service: the live deploy and commit (Render's deploy list, checked
    against render.json), and the PREVIOUS commit that was live -- the
    newest `deactivated` deploy older than the live one whose commit
    differs from it (a redeploy of the same commit is not a previous
    release);
  * the rollback target: that commit when every service ON THE RELEASE
    names the same one, otherwise none, named (see below: a service off
    the release has no say and gets no command);
  * whether the target is an ancestor of the release (git, by the job),
    and the target's four gate conclusions on its own SHA (GitHub, by the
    job, gates.json shape);
  * the migration sets of the target's tree and the release's tree
    ({file: sha256 of its text}, the runner's content_sha), their
    fingerprints (the API control's), and what the release adds, removes
    or changes: IDENTICAL sets mean the target runs on exactly the schema
    it ran on; any difference needs the attested upgrade-path receipt's
    compatibility check (tools/upgrade_path_receipt.py), whose base must
    be this target;
  * the deploy command per service (documented, never run here) and the
    written procedure (docs/closeout/ROLLBACK_PROCEDURE.md).

EACH SERVICE GOES BACK TO ITS OWN PREVIOUS (rc6.3 rollback-fix). Production
evidence, approved-judge pm-acceptance 38002788631 on release 16d23450: api
and workers were live on 16d23450, the market plane on 732cc0c6 (moved back
there at ~15:59Z on 2026-10-09 after RC6.1 3d5af039's Kalshi client hung
it). The plane's own history names 3d5af039 as the deploy before 732cc0c6,
so all three "agreed" on 3d5af039, and the record wrote ONE command per
service from that single release-wide target -- including
`market-plane.yml ... -f arg=3d5af039... -f confirm=DO`, which would have
redeployed the hang onto a service that was never on the release. Now
`rollback_by_service` decides per service, from that service's own deploy
list only:

  * DEPLOY_PREVIOUS -- the service is live on the release: back to ITS
    previous live commit (the newest `deactivated` deploy older than its
    live one whose commit differs), never another service's;
  * NONE -- the service is not on the release: it stays on its current
    commit (`stay_on`), named ROLLBACK_SERVICE_NOT_ON_THE_RELEASE, and no
    command is written for it (the release-level NOT_READY reason stays);
  * REFUSED -- its history is unreadable, has no single live deploy,
    disagrees with render.json, or shows no previous live commit; or, where
    it decides what is live or what ran before, carries a deploy status
    Render does not document (ROLLBACK_DEPLOY_STATUS_UNKNOWN: only the
    statuses in NEVER_SERVED are skipped as never served) or a served
    deploy newer than the live one
    (ROLLBACK_SERVED_DEPLOY_NEWER_THAN_THE_LIVE_DEPLOY): named, and no
    command.

A command is written only for DEPLOY_PREVIOUS and only for a commit that
service's own deploy list shows was live on it before. `target_sha` (the
commit whose gates, ancestry and migrations the judge reads) is the one
commit every service on the release goes back to; when they differ there
is none, named, and each still gets only its own command.

The scorecard re-judges every fact (tools/scorecard_14.rollback_ready);
this file's own status is a convenience, not the verdict.

    python3 -I tools/rollback_readiness.py target ACC [--sha SHA]
    python3 -I tools/rollback_readiness.py build ACC --sha SHA
        --target-migrations DIR --release-migrations DIR
        --target-is-ancestor true|false|unknown --out ACC/rollback.json

Standard library only.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

# the fingerprint and the tree hash are fresh_db_receipt's (the API
# control's, pinned by test): one definition, imported from beside this file
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fresh_db_receipt as FD  # noqa: E402

VERSION = "ROLLBACK_READINESS_V1"
SERVICES = ("sportsassets-api", "sportsassets-workers",
            "sportsassets-market-plane")
GATES = ("backend_tests", "capital_critical", "commit_guard",
         "engine_diagnostic")
PROCEDURE = "docs/closeout/ROLLBACK_PROCEDURE.md"
READY, NOT_READY = "READY", "NOT_READY"
SCHEMA_BY_RECEIPT = "SCHEMA_BY_UPGRADE_RECEIPT"
#: one service's rollback action (rollback_by_service.<svc>.action)
DEPLOY_PREVIOUS, NONE, REFUSED = "DEPLOY_PREVIOUS", "NONE", "REFUSED"
_SHA = re.compile(r"^[0-9a-f]{40}$")

#: the documented deploy-by-commit action of each service (render-ops
#: deploy-api-commit / workers-commit-deploy, market-plane deploy-commit);
#: written into the record, executed only by a person who decides to
COMMANDS = {
    "sportsassets-api":
        "gh workflow run render-ops.yml --ref claude/p0-closeout "
        "-f action=deploy-api-commit -f service=sportsassets-api "
        "-f arg=%s -f confirm=DO",
    "sportsassets-workers":
        "gh workflow run render-ops.yml --ref claude/p0-closeout "
        "-f action=workers-commit-deploy -f service=sportsassets-workers "
        "-f arg=%s -f confirm=DO",
    "sportsassets-market-plane":
        "gh workflow run market-plane.yml --ref claude/release-api "
        "-f action=deploy-commit -f arg=%s -f confirm=DO",
}

# ── why a rollback is not ready (evidence reasons, never trading refusals)
R_DEPLOYS_UNREADABLE = "ROLLBACK_DEPLOY_HISTORY_UNREADABLE"
R_NO_SINGLE_LIVE_DEPLOY = "ROLLBACK_NO_SINGLE_LIVE_DEPLOY"
R_LIVE_DIFFERS_FROM_RENDER = "ROLLBACK_LIVE_DEPLOY_DIFFERS_FROM_RENDER_SUMMARY"
R_NO_PREVIOUS_DEPLOY = "ROLLBACK_NO_PREVIOUS_DEPLOY"
R_TARGET_DIFFERS_BY_SERVICE = "ROLLBACK_TARGET_DIFFERS_BY_SERVICE"
R_SERVICE_NOT_ON_RELEASE = "ROLLBACK_SERVICE_NOT_ON_THE_RELEASE"
R_TARGET_NOT_ANCESTOR = "ROLLBACK_TARGET_NOT_AN_ANCESTOR_OF_THE_RELEASE"
R_TARGET_GATE_NOT_GREEN = "ROLLBACK_TARGET_GATE_NOT_GREEN"
R_TARGET_MIGRATIONS_UNREADABLE = "ROLLBACK_TARGET_MIGRATIONS_UNREADABLE"
R_RELEASE_MIGRATIONS_UNREADABLE = "ROLLBACK_RELEASE_MIGRATIONS_UNREADABLE"
R_COMMIT_NOT_PREVIOUSLY_LIVE = \
    "ROLLBACK_COMMIT_NOT_PREVIOUSLY_LIVE_ON_THE_SERVICE"
#: a deploy status Render does not document sits where it decides what is
#: live or what ran before: whether that deploy served cannot be known
R_DEPLOY_STATUS_UNKNOWN = "ROLLBACK_DEPLOY_STATUS_UNKNOWN"
#: a deploy that served (`deactivated`) is newer in the list than the live
#: deploy: the list is not newest-first as Render writes it, so what ran
#: before the live deploy cannot be read from its order
R_SERVED_NEWER_THAN_LIVE = "ROLLBACK_SERVED_DEPLOY_NEWER_THAN_THE_LIVE_DEPLOY"
#: a service's history is not usable at all: no action can be derived
_HISTORY_UNUSABLE = (R_DEPLOYS_UNREADABLE, R_NO_SINGLE_LIVE_DEPLOY,
                     R_DEPLOY_STATUS_UNKNOWN, R_SERVED_NEWER_THAN_LIVE)

#: Render's deploy statuses (API `deploy.status`, exact strings): `live`
#: serves now, `deactivated` served and was replaced, and these never
#: served. Any other status is UNKNOWN -- never taken to mean "never
#: served" (a `Deactivated` or `succeeded` row skipped as moot would roll a
#: service back past what it last ran).
LIVE, DEACTIVATED = "live", "deactivated"
NEVER_SERVED = frozenset((
    "build_failed", "update_failed", "canceled", "pre_deploy_failed",
    "created", "queued", "build_in_progress", "update_in_progress",
    "pre_deploy_in_progress"))


def _json(path):
    try:
        return json.loads(pathlib.Path(path).read_text())
    except (OSError, ValueError, TypeError):
        return None


def _sha(v):
    v = v.strip().lower() if isinstance(v, str) else None
    return v if v and _SHA.match(v) else None


def _deploy(row):
    """One row of Render's deploy list as its `deploy` object, or None when
    the row cannot be read: not {"deploy": {...}}, or a status that is not
    a string (whether it ever served cannot be known)."""
    d = row.get("deploy") if isinstance(row, dict) else None
    return d if isinstance(d, dict) and isinstance(d.get("status"), str) \
        else None


def _commit(d):
    """A deploy's commit SHA, or None: a `commit` that is not an object (a
    string, a list, a number) or names no 40-hex id is unreadable, and is
    never guessed at."""
    c = d.get("commit")
    return _sha(c.get("id")) if isinstance(c, dict) else None


def _newer_than_live(deps) -> str | None:
    """Why the rows NEWER than the live deploy (Render lists newest first)
    leave what is live, or what ran before it, unknown -- or None when
    every one is a deploy that never served (a failed, cancelled, queued or
    still-building one changes nothing). The first such row, in list order,
    names it: a row it cannot read (it may be what is live), a status
    Render does not document (it may have served), or a deploy that served
    (`deactivated`) newer than the live one (the list is not in Render's
    order, so the deploy before the live one cannot be read from it)."""
    for d in deps:
        if d is None:
            return R_DEPLOYS_UNREADABLE
        if d["status"] in NEVER_SERVED:
            continue
        if d["status"] == DEACTIVATED:
            return R_SERVED_NEWER_THAN_LIVE
        return R_DEPLOY_STATUS_UNKNOWN
    return None


def service(acc: pathlib.Path, svc: str) -> dict:
    """The live and the previous commit of one service, from the job's own
    Render deploy list (newest first) and render.json.

    A history it cannot read is refused by name (R_DEPLOYS_UNREADABLE),
    never crashed on and never read around (rc6.3 rollback-fix review): a
    row it cannot read newer than the live deploy (it may be what is live),
    a live deploy whose commit it cannot read, or a row it cannot read
    between the live deploy and the previous live commit (it may be what
    ran before -- skipping it would roll back to an OLDER commit than the
    one the service last ran). A row it cannot read older than the
    previous live commit is not counted as previously live.

    The same holds for a status Render does not document (NEVER_SERVED is
    an allowlist, never "anything but deactivated"): newer than the live
    deploy, or between it and the previous live commit, it is refused by
    name (R_DEPLOY_STATUS_UNKNOWN); older, it is not counted. A deploy that
    served (`deactivated`) newer than the live deploy is refused by name
    (R_SERVED_NEWER_THAN_LIVE), never ignored."""
    raw = _json(acc / ("deploys_%s.json" % svc))
    ren = _json(acc / "render.json")
    row = ren.get(svc) if isinstance(ren, dict) else None
    summary = _sha(row.get("live_commit")) if isinstance(row, dict) else None
    out = {"live_commit": None, "live_deploy_id": None,
           "previous_commit": None, "previous_deploy_id": None,
           "previous_finished_at": None, "render_summary_commit": summary,
           "previously_live_commits": [], "reasons": []}
    if not isinstance(raw, list):
        out["reasons"].append(R_DEPLOYS_UNREADABLE)
        return out
    deps = [_deploy(x) for x in raw]        # None = a row it cannot read
    live = [i for i, d in enumerate(deps)
            if d is not None and d["status"] == LIVE]
    if len(live) != 1:
        out["reasons"].append("%s:%d" % (R_NO_SINGLE_LIVE_DEPLOY, len(live)))
        return out
    i = live[0]
    lc = _commit(deps[i])
    if lc is None:
        # a live deploy that names no readable commit: nothing to go back
        # FROM
        out["reasons"].append(R_DEPLOYS_UNREADABLE)
        return out
    newer = _newer_than_live(deps[:i])
    if newer:
        # a newer row that may be what is live, or that shows the list is
        # not in the order the deploy before the live one is read from
        out["reasons"].append(newer)
        return out
    out.update(live_commit=lc, live_deploy_id=deps[i].get("id"))
    if summary != lc:
        out["reasons"].append(R_LIVE_DIFFERS_FROM_RENDER)
    # every commit THIS service was live on before its live deploy (a
    # `deactivated` deploy was live and then replaced; a status in
    # NEVER_SERVED never served), newest first: the only commits a rollback
    # command may ever name for it
    seen = out["previously_live_commits"]
    for d in deps[i + 1:]:
        st = d["status"] if d is not None else None
        if st in NEVER_SERVED:
            continue                    # never served: its commit is moot
        c = _commit(d) if st == DEACTIVATED else None
        if c is None:
            if out["previous_commit"] is None:
                # what ran before the live deploy cannot be read (a row or
                # a served deploy's commit it cannot read, or a status
                # Render does not document -- it may have served): refused
                # by name, never skipped over to an older commit
                out["reasons"].append(
                    R_DEPLOY_STATUS_UNKNOWN if st not in (None, DEACTIVATED)
                    else R_DEPLOYS_UNREADABLE)
                return out
            continue                    # older: not counted as live
        if c != lc:
            if out["previous_commit"] is None:
                out.update(previous_commit=c, previous_deploy_id=d.get("id"),
                           previous_finished_at=d.get("finishedAt"))
            if c not in seen:
                seen.append(c)
    if out["previous_commit"] is None:
        out["reasons"].append(R_NO_PREVIOUS_DEPLOY)
    return out


def plan(rec: dict, sha=None) -> dict:
    """ONE service's rollback action, from ITS OWN record only (never from
    another service's history): DEPLOY_PREVIOUS to its previous live
    commit, NONE (stay on its current commit) when it is not on the release
    `sha`, or REFUSED by name. With sha None (the `target` CLI without
    --sha) every service is treated as on the release."""
    why = [r for r in rec.get("reasons") or []
           if r.split(":")[0] in _HISTORY_UNUSABLE]
    if why:
        return {"action": REFUSED, "reason": why[0]}
    live = rec.get("live_commit")
    if sha is not None and live != sha:
        return {"action": NONE, "stay_on": live,
                "reason": R_SERVICE_NOT_ON_RELEASE}
    if R_LIVE_DIFFERS_FROM_RENDER in (rec.get("reasons") or []):
        # its deploy list and Render's summary disagree on what is live:
        # neither is trusted to say what was live before
        return {"action": REFUSED, "reason": R_LIVE_DIFFERS_FROM_RENDER}
    prev = rec.get("previous_commit")
    if not prev:
        return {"action": REFUSED, "reason": R_NO_PREVIOUS_DEPLOY}
    if prev == live or prev not in (rec.get("previously_live_commits")
                                    or []):
        return {"action": REFUSED, "reason": R_COMMIT_NOT_PREVIOUSLY_LIVE}
    return {"action": DEPLOY_PREVIOUS, "from": live, "to": prev}


def command(svc: str, p: dict, rec: dict):
    """The documented deploy command for one service's plan, or None: only
    DEPLOY_PREVIOUS writes one, and only for a commit that service's own
    deploy list shows it was live on before (checked again here, so no
    later edit of plan() can write a command for any other commit)."""
    if p.get("action") != DEPLOY_PREVIOUS:
        return None
    to = _sha(p.get("to"))
    if not to or to == rec.get("live_commit") or \
            to not in (rec.get("previously_live_commits") or []):
        return None
    return COMMANDS[svc] % to


def _resolve(acc: pathlib.Path, sha=None) -> tuple:
    """(target or None, {svc: record}, {svc: plan}, reasons). The target
    is the one commit every service that goes back (not NONE) names; a
    service whose action is NONE has no say in it."""
    svcs = {s: service(pathlib.Path(acc), s) for s in SERVICES}
    plans = {s: plan(svcs[s], sha) for s in SERVICES}
    reasons = ["%s:%s" % (r, s) for s, v in svcs.items()
               for r in v["reasons"]]
    back = [s for s in SERVICES if plans[s]["action"] != NONE]
    prev = sorted({plans[s].get("to") for s in back}, key=lambda x: x or "")
    if len(prev) == 1 and prev[0]:
        return prev[0], svcs, plans, reasons
    if len(prev) > 1:
        reasons.append("%s:%s" % (R_TARGET_DIFFERS_BY_SERVICE, ",".join(
            "%s=%s" % (s, (plans[s].get("to") or "NONE")[:12])
            for s in back)))
    return None, svcs, plans, reasons


def target(acc: pathlib.Path, sha=None) -> tuple:
    """(target sha or None, {service: record}, reasons). With `sha` (the
    release) only services live on it count; without it, all three."""
    tgt, svcs, _plans, reasons = _resolve(acc, sha)
    return tgt, svcs, reasons


def migrations(target_dir, release_dir) -> dict:
    """The two trees' migration sets, as the runner hashes them."""
    def read(d):
        p = pathlib.Path(d) if d else None
        if p is None or not p.is_dir():
            return None
        m = FD.tree_migrations(p)
        return m or None
    t, r = read(target_dir), read(release_dir)
    out = {"target": None if t is None else {
               "count": len(t), "fingerprint": FD.fingerprint(t)},
           "release": None if r is None else {
               "count": len(r), "fingerprint": FD.fingerprint(r)},
           "added": [], "removed": [], "changed": [], "identical": False,
           "reasons": []}
    if t is None:
        out["reasons"].append(R_TARGET_MIGRATIONS_UNREADABLE)
    if r is None:
        out["reasons"].append(R_RELEASE_MIGRATIONS_UNREADABLE)
    if t is None or r is None:
        return out
    out["added"] = sorted(set(r) - set(t))
    out["removed"] = sorted(set(t) - set(r))
    out["changed"] = sorted(v for v in set(t) & set(r) if t[v] != r[v])
    out["identical"] = (not out["added"] and not out["removed"]
                        and not out["changed"]
                        and FD.fingerprint(t) == FD.fingerprint(r))
    return out


def gates_not_green(doc, sha) -> list:
    runs = (doc or {}).get("runs") if isinstance(doc, dict) else None
    runs = runs if isinstance(runs, dict) else {}
    bad = []
    for k in GATES:
        r = runs.get(k) if isinstance(runs.get(k), dict) else {}
        if not (r.get("status") == "completed" and
                r.get("conclusion") == "success" and sha and
                r.get("head_sha") == sha):
            bad.append("%s=%s" % (k, r.get("conclusion")))
    return bad


def build(acc, *, sha, target_migrations=None, release_migrations=None,
          target_is_ancestor=None) -> dict:
    acc = pathlib.Path(acc)
    tgt, svcs, plans, reasons = _resolve(acc, sha)
    for s, v in svcs.items():
        if v["live_commit"] != sha:
            # strict: a service off the release keeps the rollback
            # NOT_READY (its own action is NONE, no command)
            reasons.append("%s:%s" % (R_SERVICE_NOT_ON_RELEASE, s))
    reasons += ["%s:%s" % (p["reason"], s) for s, p in plans.items()
                if p["action"] == REFUSED and
                "%s:%s" % (p["reason"], s) not in reasons]
    gates = _json(acc / "gates_rollback_target.json")
    if tgt:
        if target_is_ancestor is not True:
            reasons.append(R_TARGET_NOT_ANCESTOR)
        reasons += ["%s:%s" % (R_TARGET_GATE_NOT_GREEN, b)
                    for b in gates_not_green(gates, tgt)]
    mig = migrations(target_migrations, release_migrations) if tgt else \
        migrations(None, release_migrations)
    reasons += mig.pop("reasons")
    status = NOT_READY if reasons or not tgt else (
        READY if mig["identical"] else SCHEMA_BY_RECEIPT)
    return {"version": VERSION, "sha": sha, "target_sha": tgt,
            "services": svcs, "target_is_ancestor_of_release":
            target_is_ancestor, "target_gates": gates if isinstance(
                gates, dict) else None, "migrations": mig,
            "rollback_by_service": plans,
            "commands": {s: command(s, plans[s], svcs[s]) for s in SERVICES},
            "procedure": PROCEDURE, "status": status,
            "reasons": reasons, "deploys_nothing": True}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="mode", required=True)
    t = sub.add_parser("target")
    t.add_argument("acc")
    t.add_argument("--sha", default="",
                   help="the release: only services live on it count")
    b = sub.add_parser("build")
    b.add_argument("acc")
    b.add_argument("--sha", required=True)
    b.add_argument("--target-migrations", default="")
    b.add_argument("--release-migrations", default="")
    b.add_argument("--target-is-ancestor", default="unknown")
    b.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if a.mode == "target":
        if a.sha and _sha(a.sha) is None:
            print("")           # a release that is not a sha: no target
            return 0
        tgt, _, _ = target(pathlib.Path(a.acc), _sha(a.sha))
        print(tgt or "")
        return 0
    anc = {"true": True, "false": False}.get(a.target_is_ancestor)
    out = build(a.acc, sha=a.sha, target_migrations=a.target_migrations or
                None, release_migrations=a.release_migrations or None,
                target_is_ancestor=anc)
    pathlib.Path(a.out).write_text(json.dumps(out, indent=1, sort_keys=True))
    print("rollback readiness for %s: %s target=%s identical=%s actions=%s "
          "reasons=%s" % (a.sha, out["status"], out["target_sha"],
                          out["migrations"]["identical"], json.dumps({
                              s: p["action"] for s, p in
                              out["rollback_by_service"].items()}),
                          json.dumps(out["reasons"][:8])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
