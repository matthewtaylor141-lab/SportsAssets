#!/usr/bin/env python3
"""What would ACTUALLY ship if this SHA deployed? Read before approving.

WHY THIS EXISTS. On 2026-09-21 a commit carrying the R5 fail-closed
repair was pushed with [deploy-approved]. It deployed the R5 repair --
and also cc5d3e5, the capacity-aware collector admission change, which
had been sitting unshipped on the branch because every tip since had
carried [skip render].

Nobody decided to deploy the collector that day. It shipped because it
was an ancestor. Within seven ticks it had stopped data collection
entirely, and the approval that carried it was for a two-file safety
fix.

RENDER DEPLOYS A SHA, NOT A COMMIT. The unit of deployment is the whole
tree at the tip: every commit between what is running and what is being
pushed, whatever each one's message said about its own intent. Reading
the latest commit tells you what the last author was thinking. It does
not tell you what is about to run.

So this takes the SHA currently deployed (from `render-ops deploys`) and
the SHA about to be, and prints every commit and every changed file
between them, grouped by the component each path belongs to, with the
order-capable and scheduler paths called out because those are the two
that have surprised us.

    python3 scripts/deploy_manifest.py <deployed-sha> [proposed-sha]

Exit code is 0 always: this reports, it does not gate. The gate is a
person reading it.
"""

from __future__ import annotations

import subprocess
import sys

# Path prefix -> component. First match wins, so order matters: the
# narrow order-capable paths are listed before the broad ones they sit
# inside.
COMPONENTS = [
    ("backend/sportsassets/live_executor.py", "ORDER-CAPABLE: live executor"),
    ("backend/sportsassets/pmus.py", "ORDER-CAPABLE: venue adapter"),
    ("backend/sportsassets/workers/whale_exits.py", "ORDER-CAPABLE: whale exits"),
    ("backend/sportsassets/workers/mirror_live.py", "ORDER-CAPABLE: mirror reconciler"),
    ("backend/sportsassets/workers/underdog.py", "ORDER-CAPABLE: underdog"),
    ("backend/sportsassets/calibration_execute.py", "ORDER-CAPABLE: calibration"),
    ("backend/sportsassets/calibration_adapter.py", "ORDER-CAPABLE: calibration"),
    ("backend/sportsassets/execution_gate.py", "ORDER-CAPABLE: execution gate"),
    ("backend/sportsassets/workers/bettor_state.py", "COLLECTOR: scheduler"),
    ("backend/sportsassets/bettor_state_store.py", "COLLECTOR: store"),
    ("backend/sportsassets/bettor_state_capture.py", "COLLECTOR: sampling rule"),
    ("backend/sportsassets/bettor_", "COLLECTOR: other"),
    ("backend/migrations/", "MIGRATIONS"),
    ("backend/sportsassets/api/", "API"),
    ("backend/sportsassets/workers/", "WORKERS: other"),
    ("backend/tests/", "tests"),
    ("backend/", "backend: other"),
    (".github/workflows/", "workflows (not deployed by Render)"),
    ("research/", "research (not deployed)"),
    ("scripts/", "scripts"),
]

# Components whose appearance in a diff should stop the reader.
LOUD = ("ORDER-CAPABLE", "COLLECTOR", "MIGRATIONS")


def sh(*args: str) -> str:
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("git failed: %s\n%s" % (" ".join(args),
                                                 r.stderr.strip()[:400]))
    return r.stdout


def component(path: str) -> str:
    for prefix, name in COMPONENTS:
        if path.startswith(prefix):
            return name
    return "other"


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 0
    deployed = argv[1]
    proposed = argv[2] if len(argv) > 2 else "HEAD"

    base = sh("git", "rev-parse", deployed).strip()
    tip = sh("git", "rev-parse", proposed).strip()

    print("DEPLOY MANIFEST")
    print("  running now : %s" % base[:12])
    print("  proposed    : %s" % tip[:12])

    if base == tip:
        print("\nNothing to deploy: same commit.")
        return 0

    commits = [l for l in sh("git", "log", "--oneline", "--no-decorate",
                             "%s..%s" % (base, tip)).split("\n") if l]
    print("\n%d COMMIT(S) WOULD SHIP" % len(commits))
    for c in commits:
        print("  %s" % c)

    # A commit whose own message suppressed its build still ships when
    # it is an ancestor of an approved tip. That is exactly how the
    # collector went out, so name them.
    passengers = [c for c in commits
                  if "[skip render]" in c or "[deploy-approved]" not in c]
    if passengers:
        print("\n%d OF THESE NEVER ASKED TO DEPLOY:" % len(passengers))
        for c in passengers:
            print("  %s" % c)
        print("  They ship anyway -- Render deploys the tree at the tip,")
        print("  and these are ancestors of it.")

    files = [l for l in sh("git", "diff", "--name-only",
                           "%s..%s" % (base, tip)).split("\n") if l]
    groups: dict[str, list[str]] = {}
    for f in files:
        groups.setdefault(component(f), []).append(f)

    print("\n%d FILE(S) CHANGED, BY COMPONENT" % len(files))
    for name in sorted(groups, key=lambda n: (n.split(":")[0] not in
                                              [x for x in LOUD], n)):
        mark = "  <-- READ THIS" if name.startswith(LOUD) else ""
        print("\n  %s (%d)%s" % (name, len(groups[name]), mark))
        for f in sorted(groups[name]):
            stat = sh("git", "diff", "--numstat",
                      "%s..%s" % (base, tip), "--", f).strip()
            add, dele = (stat.split("\t")[:2] if stat else ("?", "?"))
            print("      +%-6s -%-6s %s" % (add, dele, f))

    loud = [n for n in groups if n.startswith(LOUD)]
    print("\n" + "=" * 62)
    if loud:
        print("THIS DEPLOY TOUCHES:")
        for n in sorted(loud):
            print("  - %s" % n)
        print("\nConfirm each one is intended. The collector shipped as an")
        print("ancestor of a safety fix on 2026-09-21 and stopped intake.")
    else:
        print("No order-capable, collector or migration paths in this diff.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
