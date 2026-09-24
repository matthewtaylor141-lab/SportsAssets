"""Publish acceptance artifacts into the evidence store.

THE ONLY WRITER. `bettor_evidence_store.publish` is reachable from here
and from nothing in the API -- a test asserts the API surface never
calls it. Evidence enters the store deliberately, from a named commit,
or it does not enter.

    # what would be published, and from which commit -- writes nothing
    python tools/publish_evidence.py --dry-run

    # publish, attributing every artifact to an explicit commit
    python tools/publish_evidence.py --sha 4b83924 --confirm DO

THE COMMIT IS REQUIRED AND IS NOT GUESSED FROM THE WORKING TREE BY
DEFAULT. `--sha` names the commit the artifacts describe. With
`--from-git` it is read from `git rev-parse HEAD`, which is only
correct when the tree is clean -- so that path refuses a dirty tree
rather than attributing modified bytes to a commit that does not
contain them.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sportsassets import bettor_evidence_store as ES        # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
ACCEPT = os.path.join(REPO, "research", "beta48", "acceptance")

# WHAT IS EVIDENCE, and what each thing is evidence OF. A file not on
# this list is not published: the store is a curated read path for the
# command centre, not a mirror of a directory.
PUBLISH = [
    ("release_tests.xml", "junit", "text/xml",
     "the release suite at the deployed commit"),
    ("repair_tests.xml", "junit", "text/xml",
     "the repair suite; superseded by the release suite"),
    ("gate_tests_against_defective_code.xml", "junit", "text/xml",
     "the gate run against DELIBERATELY DEFECTIVE code -- its failures "
     "are the evidence that the gate fails when the code is wrong"),
    ("evaluation.json", "economics", "application/json",
     "the prespecified evaluation; a DEVELOPMENT DIAGNOSTIC, not an "
     "independent holdout"),
    ("incentive_opportunity.json", "economics", "application/json",
     "hypothetical reward share on an observed ladder; no order was "
     "placed and no reward was earned"),
    ("incentive_manifest.json", "manifest", "application/json",
     "the frozen 12-market programme manifest for ET date 2026-09-23"),
]


def git_head() -> str:
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=REPO,
                           capture_output=True, text=True).stdout.strip()
    if dirty:
        raise SystemExit(
            "refusing --from-git on a dirty tree: the bytes on disk are "
            "not the bytes in HEAD, and attributing them to HEAD would "
            "put a false commit on every figure they produce.\n"
            "Commit first, or pass --sha explicitly.")
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO,
                          capture_output=True, text=True).stdout.strip()


async def run(sha: str, *, dry_run: bool) -> int:
    rows = []
    for fname, kind, ctype, note in PUBLISH:
        path = os.path.join(ACCEPT, fname)
        try:
            with open(path, encoding="utf-8") as fh:
                body = fh.read()
        except Exception as exc:                               # noqa: BLE001
            rows.append({"name": fname, "ok": False,
                         "why": "UNREADABLE", "detail": type(exc).__name__})
            continue
        v = ES.validate(name=fname, kind=kind, source_sha=sha, body=body,
                        content_type=ctype)
        rows.append({"name": fname, "kind": kind, "bytes": len(body),
                     "digest": v.get("digest"), "ok": v["ok"],
                     "why": v.get("why"), "detail": v.get("detail")})

    bad = [r for r in rows if not r["ok"]]
    for r in rows:
        mark = "OK " if r["ok"] else "XX "
        print("%s%-42s %-10s %8s  %s" % (
            mark, r["name"], r.get("kind", "-"), r.get("bytes", "-"),
            (r.get("digest") or "")[:16] or r.get("why", "")))
    if bad:
        print("\n%d artifact(s) would be REFUSED:" % len(bad))
        for r in bad:
            print("  %-42s %s -- %s" % (r["name"], r["why"],
                                        r.get("detail", "")))

    if dry_run:
        print("\nDRY RUN. Nothing was written. source_sha would be %s" % sha)
        return 1 if bad else 0
    if bad:
        print("\nREFUSING TO PUBLISH: fix the refusals above first. "
              "A partial publish would leave the store describing a "
              "commit it does not hold the evidence for.")
        return 2

    from sportsassets.db import get_pool
    pool = await get_pool()
    schema = await ES.ensure_schema(pool)
    if not schema.get("ok"):
        print("schema unavailable: %s" % json.dumps(schema))
        return 3

    print()
    for fname, kind, ctype, note in PUBLISH:
        with open(os.path.join(ACCEPT, fname), encoding="utf-8") as fh:
            body = fh.read()
        got = await ES.publish(pool, name=fname, kind=kind, source_sha=sha,
                               body=body, content_type=ctype, note=note)
        print("%-42s %s" % (
            fname,
            "unchanged (already stored)" if got.get("unchanged")
            else "published id=%s digest=%s" % (got.get("id"),
                                                (got.get("digest") or "")[:16])
            if got.get("ok") else "REFUSED %s" % got.get("why")))

    print("\ninventory:")
    for r in await ES.inventory(pool):
        print("  %-42s versions=%-3s current=%s" % (
            r["name"], r["versions"], r["current"]))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sha", help="the commit these artifacts describe")
    ap.add_argument("--from-git", action="store_true",
                    help="read the commit from git HEAD; refuses a dirty tree")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--confirm", default="",
                    help="type DO to actually write")
    a = ap.parse_args()

    sha = a.sha or (git_head() if a.from_git else None)
    if not sha:
        raise SystemExit("--sha or --from-git is required: an artifact "
                         "with no source commit cannot be attributed.")
    if not a.dry_run and a.confirm != "DO":
        raise SystemExit("this writes to the production evidence store; "
                         "pass --confirm DO, or --dry-run to preview.")
    return asyncio.run(run(sha.strip().lower(), dry_run=a.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
