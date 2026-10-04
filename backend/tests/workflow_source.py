"""THE EFFECTIVE SOURCE OF A WORKFLOW WHOSE SCRIPTS LIVE IN VERSIONED FILES.

WHY THIS EXISTS (c28, 2026-10-04). render-ops.yml reached 510,399 bytes
against GitHub's 500 KB (512,000-byte) workflow-file ceiling -- 1,601 bytes
of headroom, the same position that took the release lever down on
2026-09-21. Its one 500-kilobyte `run:` body now lives at
.github/render-ops/ops.sh, and command-verify.yml's entry-run step bodies at
.github/command-verify/*.sh. Each workflow runs its file through the SAME
shell invocation GitHub used for the inline body (`shell: bash` ->
`bash --noprofile --norc -eo pipefail`, no shell -> `bash -e`).

The move is byte-for-byte: `monolith_text()` re-inlines every script into
its wrapper and removes the fetch step, which reproduces the pre-move file
exactly (tests/test_workflow_headroom.py proves it against the recorded
sha256 of the original). The dozens of existing tests that read a preset,
a guard or a help line out of render-ops.yml read THIS text, so they keep
testing what actually runs -- not the 20-line wrapper.

A wrapper is recognised only by its marker line
`# versioned step script: <path>` directly under `run: |`; anything else is
left untouched.
"""
from __future__ import annotations

import hashlib
import os
import re
import tempfile

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
WORKFLOWS = os.path.join(REPO, ".github", "workflows")

FETCH_STEP = re.compile(
    r"^(?P<ind>[ ]*)- name: Fetch the versioned step scripts\n"
    r"(?:(?P=ind)  .*\n)+", re.M)
WRAPPER = re.compile(
    r"^(?P<ind>[ ]*)run: \|\n"
    r"(?P=ind)  # versioned step script: (?P<path>\S+)\n"
    r"(?:(?P=ind)  .*\n)*", re.M)


def workflow_path(name: str) -> str:
    return os.path.join(WORKFLOWS, name)


def script_paths(name: str) -> list:
    """Repo-relative script paths a workflow's wrappers run, in file order."""
    with open(workflow_path(name), encoding="utf-8") as fh:
        return [m.group("path") for m in WRAPPER.finditer(fh.read())]


def _indent(body: str, pad: str) -> str:
    return "".join((pad + ln) if ln.strip("\n") else ln
                   for ln in body.splitlines(keepends=True))


def inline_scripts(text: str, read) -> str:
    """Pure form of `monolith_text`: `read(repo_relative_path) -> str`."""
    def inline(m):
        ind = m.group("ind")
        return "%srun: |\n%s" % (ind, _indent(read(m.group("path")), ind + "  "))

    return FETCH_STEP.sub("", WRAPPER.sub(inline, text))


def _read_repo(rel: str) -> str:
    with open(os.path.join(REPO, rel), encoding="utf-8") as fh:
        return fh.read()


def monolith_text(name: str) -> str:
    """The workflow as it read before its scripts were moved out (render-ops:
    byte for byte; command-verify: identical once parsed -- nine blank lines
    inside its bodies carried indentation-only whitespace that YAML drops)."""
    return inline_scripts(_read_repo(os.path.join(".github", "workflows", name)),
                          _read_repo)


def monolith_path(name: str) -> str:
    """A file holding `monolith_text(name)`, for readers that need a path
    (open(), Path.read_text(), yaml.safe_load(open(...)))."""
    text = monolith_text(name)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    d = os.path.join(tempfile.gettempdir(), "workflow_monolith")
    os.makedirs(d, exist_ok=True)
    out = os.path.join(d, "%s.%s" % (digest, name))
    if not os.path.exists(out):
        tmp = out + ".%d.tmp" % os.getpid()
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, out)
    return out


RENDER_OPS = "render-ops.yml"
COMMAND_VERIFY = "command-verify.yml"


def render_ops_path() -> str:
    return monolith_path(RENDER_OPS)


def render_ops_file():
    """`render_ops_path()` as a pathlib.Path (read_text / read_bytes / open)."""
    import pathlib
    return pathlib.Path(render_ops_path())


def command_verify_path() -> str:
    return monolith_path(COMMAND_VERIFY)


# ── THE DISPATCH CONTRACT: what an operator can ask for, and the guards ──

def _on(doc):
    return doc.get("on", doc.get(True))


def contract(doc: dict) -> dict:
    """Everything about a workflow except the bodies of its scripts: triggers,
    inputs (descriptions, defaults, choice options), concurrency,
    permissions, every job's condition/runner/env, every step's name,
    condition, shell, env, uses/with. Two files with the same contract offer
    the same modes behind the same guards."""
    jobs = {}
    for jn, j in (doc.get("jobs") or {}).items():
        steps = []
        for s in j.get("steps") or []:
            if s.get("name") == "Fetch the versioned step scripts":
                continue
            steps.append({k: v for k, v in s.items() if k != "run"}
                         | {"has_run": "run" in s})
        jobs[jn] = {k: v for k, v in j.items() if k != "steps"} | {
            "steps": steps}
    return {"name": doc.get("name"), "on": _on(doc),
            "concurrency": doc.get("concurrency"),
            "permissions": doc.get("permissions"), "env": doc.get("env"),
            "jobs": jobs}
