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
    release) -- each from THAT service's own deploy list;
  * per service, what a rollback of the release does to it (rc6.3,
    pm-acceptance 38002788631): a service ON the release goes back to its
    OWN previous live commit (DEPLOY_PREVIOUS); a service NOT on the
    release is not touched by rolling the release back (NONE: it stays on
    its own live commit, named ROLLBACK_SERVICE_NOT_ON_THE_RELEASE, and the
    rollback stays NOT_READY for it); a service on the release with no
    previous live commit, or whose previous commit is one it was rolled
    back FROM (its live commit ran before that commit too, or Render's
    trigger says rollback), has NO_TARGET, named. Only DEPLOY_PREVIOUS
    writes a command, always to that service's own previous live commit:
    no command ever deploys a service to a commit it was not live on;
  * the rollback target: the one commit every service on the release
    goes back to, otherwise none, named;
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
  * the deploy command for every service the rollback moves (documented,
    never run here; None for a service that stays or has no target) and
    the written procedure (docs/closeout/ROLLBACK_PROCEDURE.md).

The defect this shape closes (pm-acceptance 38002788631, release 16d23450
on api and workers, the market plane still on 732cc0c6): all three deploy
lists named 3d5af039 (RC6.1) as the previous commit -- the plane ran it
14:42Z-15:59Z, hung on its Kalshi client and was put back on 732cc0c6 --
and ONE shared target wrote a deploy command for every service, so the
record carried a ready-to-run `market-plane.yml deploy-commit 3d5af039`
for a service the release never touched, to the commit it had been
rolled back from.

The scorecard re-judges every fact (tools/scorecard_14.rollback_ready);
this file's own status is a convenience, not the verdict.

    python3 -I tools/rollback_readiness.py target ACC [--sha SHA]
        (the release is --sha, else acc/lineage.json's tested sha; with
        neither, every service counts as on the release)
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
#: the previous commit is one the service was rolled back FROM (its live
#: commit ran before it too: live -> previous -> live, or Render's trigger
#: on the live deploy is `rollback`): never a target
R_PREVIOUS_ROLLED_BACK = "ROLLBACK_PREVIOUS_COMMIT_WAS_ROLLED_BACK_FROM"

# ── what a rollback of the release does to one service ───────────────────
#: on the release: back to its OWN previous live commit (the only action
#: that writes a command)
DEPLOY_PREVIOUS = "DEPLOY_PREVIOUS"
#: not on the release: rolling the release back does not touch it; it stays
#: on its own live commit (no command)
STAY = "NONE"
#: on the release, but no previous live commit it can go back to (none in
#: its deploy list, its live deploy unreadable, or the previous one was
#: rolled back from); no command
NO_TARGET = "NO_TARGET"


def _json(path):
    try:
        return json.loads(pathlib.Path(path).read_text())
    except (OSError, ValueError, TypeError):
        return None


def _sha(v):
    v = v.strip().lower() if isinstance(v, str) else None
    return v if v and _SHA.match(v) else None


def _commit(d):
    return _sha((d.get("commit") or {}).get("id"))


def service(acc: pathlib.Path, svc: str) -> dict:
    """The live and the previous commit of one service, from the job's own
    Render deploy list of THAT service (newest first) and render.json, and
    whether the previous commit is one the service was rolled back from."""
    raw = _json(acc / ("deploys_%s.json" % svc))
    ren = _json(acc / "render.json")
    summary = _sha(((ren or {}).get(svc) or {}).get("live_commit")) \
        if isinstance(ren, dict) else None
    out = {"live_commit": None, "live_deploy_id": None,
           "previous_commit": None, "previous_deploy_id": None,
           "previous_finished_at": None, "previous_rolled_back_from": None,
           "render_summary_commit": summary, "reasons": []}
    if not isinstance(raw, list):
        out["reasons"].append(R_DEPLOYS_UNREADABLE)
        return out
    deps = [x.get("deploy") for x in raw
            if isinstance(x, dict) and isinstance(x.get("deploy"), dict)]
    live = [i for i, d in enumerate(deps) if d.get("status") == "live"]
    if len(live) != 1:
        out["reasons"].append("%s:%d" % (R_NO_SINGLE_LIVE_DEPLOY, len(live)))
        return out
    i = live[0]
    lc = _commit(deps[i])
    out.update(live_commit=lc, live_deploy_id=deps[i].get("id"))
    if summary != lc:
        out["reasons"].append(R_LIVE_DIFFERS_FROM_RENDER)
    for j in range(i + 1, len(deps)):
        d = deps[j]
        c = _commit(d)
        if d.get("status") == "deactivated" and c and c != lc:
            # rolled back FROM it: the live commit was live before it too
            # (live -> previous -> live again), or Render calls the live
            # deploy a rollback
            back = deps[i].get("trigger") == "rollback" or any(
                o.get("status") == "deactivated" and _commit(o) == lc
                for o in deps[j + 1:])
            out.update(previous_commit=c, previous_deploy_id=d.get("id"),
                       previous_finished_at=d.get("finishedAt"),
                       previous_rolled_back_from=bool(back))
            break
    if out["previous_commit"] is None:
        out["reasons"].append(R_NO_PREVIOUS_DEPLOY)
    return out


def plan(rec: dict, sha) -> tuple:
    """(action, commit, reason): what rolling release `sha` back does to
    one service, from that service's own record. `sha` None (the release
    unknown) counts the service as on the release."""
    live, prev = rec.get("live_commit"), rec.get("previous_commit")
    if live is None:
        return NO_TARGET, None, (rec.get("reasons") or
                                 [R_DEPLOYS_UNREADABLE])[0]
    if sha is not None and live != sha:
        return STAY, live, R_SERVICE_NOT_ON_RELEASE
    if prev is None:
        return NO_TARGET, None, R_NO_PREVIOUS_DEPLOY
    if rec.get("previous_rolled_back_from"):
        return NO_TARGET, None, R_PREVIOUS_ROLLED_BACK
    return DEPLOY_PREVIOUS, prev, None


def release_sha(acc: pathlib.Path):
    """The release under test as the judge's lineage step wrote it
    (acc/lineage.json `sha`, the dispatched SHA), else None."""
    lin = _json(pathlib.Path(acc) / "lineage.json")
    return _sha(lin.get("sha")) if isinstance(lin, dict) else None


def target(acc: pathlib.Path, sha=None) -> tuple:
    """(target sha or None, {service: record}, reasons).

    Each service's record carries its own plan (on_release, action,
    rollback_commit, action_reason). The target is the one commit every
    service ON the release goes back to (DEPLOY_PREVIOUS); a service not on
    the release stays and never contributes its previous commit."""
    acc = pathlib.Path(acc)
    sha = _sha(sha) or release_sha(acc)
    svcs = {s: service(acc, s) for s in SERVICES}
    reasons = ["%s:%s" % (r, s) for s, v in svcs.items()
               for r in v["reasons"]]
    for s, v in svcs.items():
        action, commit, why = plan(v, sha)
        v.update(on_release=None if sha is None else v["live_commit"] == sha,
                 action=action, rollback_commit=commit, action_reason=why)
        if why == R_PREVIOUS_ROLLED_BACK:
            reasons.append("%s:%s:%s" % (R_PREVIOUS_ROLLED_BACK, s,
                                         v["previous_commit"][:12]))
    moving = {s: v for s, v in svcs.items() if v["action"] != STAY}
    if not moving or any(v["action"] != DEPLOY_PREVIOUS
                         for v in moving.values()):
        return None, svcs, reasons
    commits = sorted({v["rollback_commit"] for v in moving.values()})
    if len(commits) == 1:
        return commits[0], svcs, reasons
    reasons.append("%s:%s" % (R_TARGET_DIFFERS_BY_SERVICE, ",".join(
        "%s=%s" % (s, v["rollback_commit"][:12])
        for s, v in moving.items())))
    return None, svcs, reasons


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
    tgt, svcs, reasons = target(acc, sha)
    for s, v in svcs.items():
        # strict: a service left off the release keeps the rollback
        # NOT_READY (its own plan is NONE: it stays where it is)
        if v["live_commit"] != sha:
            reasons.append("%s:%s" % (R_SERVICE_NOT_ON_RELEASE, s))
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
            # per service, to ITS OWN previous live commit, and only for a
            # service the rollback moves: never a command for a service the
            # release did not touch, never to a commit it was not live on
            "commands": {s: (COMMANDS[s] % svcs[s]["rollback_commit"])
                         if svcs[s]["action"] == DEPLOY_PREVIOUS else None
                         for s in SERVICES},
            "procedure": PROCEDURE, "status": status,
            "reasons": reasons, "deploys_nothing": True}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="mode", required=True)
    t = sub.add_parser("target")
    t.add_argument("acc")
    t.add_argument("--sha", default="")
    b = sub.add_parser("build")
    b.add_argument("acc")
    b.add_argument("--sha", required=True)
    b.add_argument("--target-migrations", default="")
    b.add_argument("--release-migrations", default="")
    b.add_argument("--target-is-ancestor", default="unknown")
    b.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if a.mode == "target":
        tgt, _, _ = target(pathlib.Path(a.acc), a.sha or None)
        print(tgt or "")
        return 0
    anc = {"true": True, "false": False}.get(a.target_is_ancestor)
    out = build(a.acc, sha=a.sha, target_migrations=a.target_migrations or
                None, release_migrations=a.release_migrations or None,
                target_is_ancestor=anc)
    pathlib.Path(a.out).write_text(json.dumps(out, indent=1, sort_keys=True))
    print("rollback readiness for %s: %s target=%s identical=%s reasons=%s"
          % (a.sha, out["status"], out["target_sha"],
             out["migrations"]["identical"], json.dumps(out["reasons"][:8])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
