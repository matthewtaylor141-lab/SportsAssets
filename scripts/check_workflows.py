#!/usr/bin/env python3
"""Validate every workflow file. Size first, then structure, then shell.

WHY THIS EXISTS. On 2026-09-21 render-ops.yml -- the lever that operates
Render, reads the production database and holds the trading kill switch
-- was disabled by an eight-line comment about an environment variable.

GitHub caps a workflow file at 512,000 bytes. render-ops.yml stood at
511,808, which is 192 bytes under. The comment took it to 512,388, and
every dispatch after that returned startup_failure. Two runs were
dispatched against the dead lever before anyone noticed, because a
workflow that will not start looks exactly like a workflow that has not
started yet.

`yaml.safe_load` passed the whole time. It was never going to catch
this: the file was valid YAML and always had been. Size is not a syntax
property, and neither are half the things GitHub rejects. So this
checks three separate things, because each one catches failures the
others cannot see:

    SIZE       against the 512,000-byte ceiling, with headroom that has
               to be real rather than incidental
    STRUCTURE  the keys GitHub requires, which valid YAML need not have
    SHELL      `bash -n` over every run: block, because a broken script
               inside a valid workflow fails at 3am instead of at review

Run it directly, or from commit-guard on every push.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

try:
    import yaml
except ImportError:                                        # pragma: no cover
    print("PyYAML is required: pip install pyyaml", file=sys.stderr)
    raise SystemExit(2)

# GitHub's documented hard ceiling for a single workflow file.
LIMIT = 512_000

# HEADROOM IS THE POINT, NOT THE CEILING. render-ops was *under* the
# limit and still one comment from death. A file with 192 bytes spare is
# not passing, it is pending. 32 KiB is roughly eighty comment lines --
# enough that documenting a change can never be what takes the lever
# down.
MIN_HEADROOM = 32_768

WORKFLOW_DIR = os.path.join(".github", "workflows")


def _iter_workflows(root: str):
    d = os.path.join(root, WORKFLOW_DIR)
    for name in sorted(os.listdir(d)):
        if name.endswith((".yml", ".yaml")):
            yield name, os.path.join(d, name)


def check_size(path: str) -> list[str]:
    n = os.path.getsize(path)
    head = LIMIT - n
    if n >= LIMIT:
        return ["%d bytes: OVER the %d-byte ceiling by %d. GitHub will "
                "refuse to start this workflow and every dispatch will "
                "return startup_failure." % (n, LIMIT, n - LIMIT)]
    if head < MIN_HEADROOM:
        return ["%d bytes: only %d bytes of headroom, below the %d-byte "
                "minimum. This file is close enough to the ceiling that "
                "an ordinary comment can disable it." % (n, head,
                                                         MIN_HEADROOM)]
    return []


def check_structure(path: str, doc) -> list[str]:
    """The keys GitHub requires. Valid YAML need not have any of them."""
    bad: list[str] = []
    if not isinstance(doc, dict):
        return ["top level is %s, not a mapping" % type(doc).__name__]

    # PyYAML resolves the bare key `on` to the boolean True (YAML 1.1),
    # so a workflow's trigger block arrives under True, not "on". Look
    # for both rather than reporting every workflow in the repo as
    # missing its triggers.
    if "on" not in doc and True not in doc:
        bad.append("no `on:` trigger block")
    jobs = doc.get("jobs")
    if not isinstance(jobs, dict) or not jobs:
        bad.append("no `jobs:` mapping")
        return bad
    for jid, job in jobs.items():
        if not isinstance(job, dict):
            bad.append("job %s is not a mapping" % jid)
            continue
        if "uses" in job:
            continue                     # a reusable-workflow call
        if "runs-on" not in job:
            bad.append("job %s has no runs-on" % jid)
        steps = job.get("steps")
        if not isinstance(steps, list) or not steps:
            bad.append("job %s has no steps" % jid)
            continue
        for i, st in enumerate(steps):
            if not isinstance(st, dict):
                bad.append("job %s step %d is not a mapping" % (jid, i))
            elif "uses" not in st and "run" not in st:
                bad.append("job %s step %d has neither run nor uses"
                           % (jid, i))
    return bad


def check_shell(path: str, doc) -> list[str]:
    """`bash -n` every run: block.

    Expression interpolation is substituted with a placeholder first:
    `${{ ... }}` is not shell, and left alone it turns every templated
    script into a syntax error. The placeholder is a bare word, so a
    script that was well-formed stays well-formed.
    """
    bad: list[str] = []
    jobs = doc.get("jobs") if isinstance(doc, dict) else None
    if not isinstance(jobs, dict):
        return bad
    for jid, job in jobs.items():
        if not isinstance(job, dict):
            continue
        shell_default = ((job.get("defaults") or {}).get("run") or {}
                         ).get("shell")
        for i, st in enumerate(job.get("steps") or []):
            if not isinstance(st, dict) or "run" not in st:
                continue
            shell = st.get("shell") or shell_default or "bash"
            if not str(shell).startswith(("bash", "sh")):
                continue                  # python/pwsh: not ours to lint
            script = str(st["run"])
            out = []
            k = 0
            while k < len(script):
                j = script.find("${{", k)
                if j < 0:
                    out.append(script[k:])
                    break
                out.append(script[k:j])
                e = script.find("}}", j)
                if e < 0:
                    out.append(script[j:])
                    break
                out.append("GHA_EXPR")
                k = e + 2
            cleaned = "".join(out)
            with tempfile.NamedTemporaryFile("w", suffix=".sh",
                                             delete=False) as fh:
                fh.write(cleaned)
                tmp = fh.name
            try:
                r = subprocess.run(["bash", "-n", tmp],
                                   capture_output=True, text=True)
                if r.returncode != 0:
                    msg = (r.stderr or "").strip().replace(tmp, "<run>")
                    bad.append("job %s step %d (%s): %s"
                               % (jid, i, st.get("name", "unnamed"),
                                  msg[:300]))
            finally:
                os.unlink(tmp)
    return bad


def main(argv: list[str]) -> int:
    root = argv[1] if len(argv) > 1 else "."
    failures: dict[str, list[str]] = {}
    rows = []

    for name, path in _iter_workflows(root):
        problems: list[str] = []
        problems += check_size(path)
        doc = None
        try:
            with open(path) as fh:
                doc = yaml.safe_load(fh)
        except Exception as exc:                          # noqa: BLE001
            problems.append("YAML did not parse: %s"
                            % str(exc).replace("\n", " ")[:200])
        if doc is not None:
            problems += check_structure(path, doc)
            problems += check_shell(path, doc)
        n = os.path.getsize(path)
        rows.append((name, n, LIMIT - n, "FAIL" if problems else "ok"))
        if problems:
            failures[name] = problems

    w = max(len(r[0]) for r in rows)
    print("%-*s %9s %9s  %s" % (w, "workflow", "bytes", "headroom",
                                "status"))
    for name, n, head, status in sorted(rows, key=lambda r: -r[1]):
        print("%-*s %9d %9d  %s" % (w, name, n, head, status))

    if failures:
        print("\n%d workflow(s) FAILED\n" % len(failures))
        for name, problems in failures.items():
            print("  %s" % name)
            for p in problems:
                print("    - %s" % p)
        return 1

    tightest = min(rows, key=lambda r: r[2])
    print("\nAll %d workflows pass. Tightest headroom: %s with %d bytes "
          "spare (minimum %d)." % (len(rows), tightest[0], tightest[2],
                                   MIN_HEADROOM))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
