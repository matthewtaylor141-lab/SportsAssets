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


# ── THE PROSE 4c20f8c MOVED OUT, PUT BACK FOR THE TESTS THAT READ IT ──
#
# R30A ci (2026-10-04). On 2026-09-21 render-ops.yml stood 192 bytes under
# GitHub's workflow-file ceiling and a comment took it over (every dispatch
# `startup_failure`); 4c20f8c moved 67 comment blocks, verbatim, into
# .github/workflows/RENDER_OPS_NOTES.md and left one anchor line in each place:
#
#     # [R62] see .github/workflows/RENDER_OPS_NOTES.md
#
# A dozen preset tests slice the workflow BY THOSE COMMENTS ("# THE EXIT
# BAND", "# REST VS TAKE, BY DECISION AND BAND", ...) or pin what the comment
# says, so every one has raised `ValueError: substring not found` since
# 4c20f8c (CI 37223385978) -- with the SQL they guard unchanged. The fact that
# changed is WHERE the prose lives, not what it says or what runs.
#
# `render_ops_documented_text()` is the effective script (monolith_text) with
# every anchor replaced by its note, each line re-prefixed with the anchor's
# indentation and `# `. Checked against 4c20f8c^: the reconstruction equals the
# pre-move file except 4c20f8c's one intended edit (the FLAGS comment) and five
# lines whose extra indentation after `#` the extraction dropped.
NOTES = os.path.join(WORKFLOWS, "RENDER_OPS_NOTES.md")
_ANCHOR = re.compile(
    r"^(?P<ind>[ ]*)# \[(?P<key>R\d+)\] see "
    r"\.github/workflows/RENDER_OPS_NOTES\.md$", re.M)
_NOTE = re.compile(r"^## \[(R\d+)\]\n(.*?)(?=^## \[R\d+\]\n|\Z)", re.S | re.M)
_DOCUMENTS = re.compile(r"^\nDocuments:\n\n```\n.*?\n```\n\n", re.S)


def render_ops_notes(text: str | None = None) -> dict:
    """Anchor key -> the verbatim prose it stands for."""
    if text is None:
        text = _read_repo(os.path.join(".github", "workflows",
                                       "RENDER_OPS_NOTES.md"))
    return {m.group(1): _DOCUMENTS.sub("", m.group(2)).rstrip("\n")
            for m in _NOTE.finditer(text)}


def reinline_notes(text: str, notes: dict) -> str:
    """Pure form: replace every anchor in `text` with its note."""
    def put_back(m):
        ind = m.group("ind")
        return "\n".join((ind + "# " + ln) if ln else (ind + "#")
                         for ln in notes[m.group("key")].split("\n"))
    return _ANCHOR.sub(put_back, text)


def render_ops_documented_text() -> str:
    return reinline_notes(monolith_text(RENDER_OPS), render_ops_notes())


# ── THE E-SERIES PRESETS, HASHED APART FROM THE FILE AROUND THEM ─────
#
# R30A ci (2026-10-04). Several E-lane tests pinned "render-ops.yml as this
# lane leaves it" as ONE sha256 of the whole workflow (0ad40506f7460915, last
# re-cut 2026-09-10 for data-audit). That hash is what the squashed tree
# 14e65d2 (2026-09-19) produces. It moved on every later change to ANY part of
# the file -- 39 presets added by later lanes (rn1x-*, pause-*, obs-*,
# evidence-*, desk-*, learn-*), the deploy-api-commit action, the FLAGS list,
# the psql runner's HEAD/PFMT, 4c20f8c's comment move and f28ba22's script
# move -- so it has been red since 2026-09-19 while every preset those lanes
# wrote stood untouched.
#
# The property the E-lanes pinned is that THEIR presets do not move. This
# hashes exactly those: every `sql)` case label that existed at 14e65d2 (the
# 80 below), each preset's full text (its label line and any continuation
# lines), in this order. Measured: 14e65d2 and 0ebdd33 give the same digest,
# and no one of the 80 differs by a byte.
E_SERIES_PRESETS = (
    "activity", "tables", "sizes", "indexes", "analyze", "index053",
    "mirror-on", "mirror-off", "mirror-state", "mirror-preflight",
    "mirror-rearm", "mirror-post-only-rearm", "loss-breaker", "mirror-tick",
    "tick-ring", "mirror-refusals", "mirror-pnl", "his-recent", "mirror-why",
    "copy-lane", "c3-rows", "c4-rows", "c4-shadow", "cfb-his", "cfb-venue",
    "nfl-rows", "nfl-team", "mirror-by-league", "his-matched", "two-legged",
    "outcome-census", "epl-rows", "gap-soccer-his", "gap-soccer-venue",
    "planned-unopened", "unread-rows", "coverage-gap", "cand-refusals",
    "mirror-hand-release", "mirror-register", "mirror-frozen", "frozen-detail",
    "resolution-lag", "his-day", "his-board", "league-census", "league-rows",
    "league-rows-por", "prefix-rows", "paired-day", "paired-ratio",
    "latency-census", "fills-answered", "close-rows", "fills-missed",
    "on-target-why", "verify-day", "traded-day", "traded-48h", "premap-rows",
    "esports-chi-rows", "book-drift", "drift-16", "fills-vs-venue",
    "books-new", "flow-books", "nf-his", "nf-venue", "exits-paired",
    "take-band", "maker-rests", "exits-band", "closed-while-he-traded",
    "fill-answers", "sleeve-48h", "sleeve-vs-him-48h", "rests-and-fees",
    "round-trips", "data-audit", "hourly",
)
_PRESET_LABEL = re.compile(r"^ {16}([a-z0-9-]+)\) ")


def sql_presets(text: str) -> dict:
    """label -> the preset's text, for the `sql)` case of render-ops."""
    seg = text[text.index("            sql)"):]
    seg = seg[:seg.index('*) echo "sql: arg must be one of')]
    out, cur = {}, None
    for ln in seg.split("\n"):
        m = _PRESET_LABEL.match(ln)
        if m:
            cur = m.group(1)
            out[cur] = [ln]
        elif ln.strip() == "" or re.match(r"^ {16}#", ln):
            cur = None
        elif cur and ln.startswith(" " * 16):
            out[cur].append(ln)
    return {k: "\n".join(v) for k, v in out.items()}


def e_series_presets_sha(text: str | None = None) -> str:
    presets = sql_presets(text if text is not None
                          else monolith_text(RENDER_OPS))
    missing = [k for k in E_SERIES_PRESETS if k not in presets]
    assert not missing, "E-series presets gone: %s" % missing
    h = hashlib.sha256()
    for k in E_SERIES_PRESETS:
        h.update(k.encode() + b"\0" + presets[k].encode() + b"\0")
    return h.hexdigest()[:16]
