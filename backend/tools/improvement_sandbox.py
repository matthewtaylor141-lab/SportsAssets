#!/usr/bin/env python3
"""THE IMPROVEMENT SANDBOX: turn an improvement task into a REAL candidate
artifact -- a commit on its own branch, its tests run, a deterministic
replay, an evaluation report -- and record it in improvement_candidates.

WHERE IT RUNS. A development or CI checkout. NEVER production: it refuses
when the environment is a deploy (RENDER / SPORTSASSETS_PRODUCTION set) or
the checkout is not a git repository -- and the production image carries
neither `tools/` nor `.git`. It NEVER pushes, merges or deploys: there is
no remote operation anywhere in this file, and the branch it writes stays
local until a person decides otherwise.

WHAT IT DOES.
  1. Reads the task spec (a JSON file, or `--task-id` read from agent_tasks
     through `--dsn`).
  2. Refuses a protected change by name: an unregistered or protected
     change class, a protected key, a diff touching a protected path (risk
     limits, credentials, account authority, approval controls, submission
     switches, the gate tools, the improvement evaluator itself) or
     REMOVING a line of capital_critical_tests.txt.
  3. `git worktree add -b improve/<task>` from the given base commit into a
     temporary directory (the checkout you run it from is never modified).
  4. Generates the change: POLICY_PARAMETER edits the versioned default
     (the class's `code_default`, or `target` in the spec) and writes a
     policy-version artifact; CODE applies the supplied unified diff.
  5. Adds a generated bounds test for a parameter change, runs the
     specified tests, runs the replay (from the DSN's recorded evidence, or
     from samples supplied in the spec) and judges it against the CLASS's
     harm and success metrics (never the spec's).
  6. Writes the evaluation report JSON into the branch, commits, and
     records the candidate (branch@sha, diff, test results, report) via the
     DSN. The candidate is PROPOSED: an evaluator that is not the proposer
     reviews it in-process; nothing here approves or releases.

USAGE
    python backend/tools/improvement_sandbox.py --spec task.json \\
        --repo . --base <commit> [--dsn postgresql://...] [--out report.json]
    python backend/tools/improvement_sandbox.py --task-id <task_id> \\
        --dsn postgresql://... --base <commit> [--diff-file change.diff]

Exit status: 0 candidate recorded (or written with --no-record); 2
refused; 3 the change could not be applied; 4 environment refusal.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve()
BACKEND = HERE.parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sportsassets.agents import improvement as IMP  # noqa: E402

VERSION = "IMPROVEMENT_SANDBOX_V1"
PROPOSER_PREFIX = "improvement_sandbox:"

R_PRODUCTION = "THE_SANDBOX_DOES_NOT_RUN_IN_PRODUCTION"
R_NOT_A_REPO = "THE_CHECKOUT_IS_NOT_A_GIT_REPOSITORY"
R_PROTECTED_PATH = "THE_DIFF_TOUCHES_A_PROTECTED_PATH"
R_CRITICAL_REMOVAL = "THE_DIFF_REMOVES_A_CAPITAL_CRITICAL_TEST"
R_SWITCH = "THE_DIFF_TURNS_ON_A_SUBMISSION_SWITCH"
R_PROTECTED_KEY_IN_DIFF = "THE_DIFF_EDITS_A_PROTECTED_KEY"
R_NO_CHANGE = "THE_SPEC_CARRIES_NO_CHANGE"
R_BRANCH_EXISTS = "THE_CANDIDATE_BRANCH_ALREADY_EXISTS"
R_APPLY = "THE_CHANGE_DID_NOT_APPLY"
R_TARGET = "THE_VERSIONED_DEFAULT_WAS_NOT_FOUND"

#: PATHS NO CANDIDATE DIFF MAY TOUCH (matched against every file the diff
#: names, with or without the leading `backend/`).
PROTECTED_PATHS = (
    r"(^|/)sportsassets/config\.py$",
    r"(^|/)sportsassets/bettor_risk_engine\.py$",
    r"(^|/)sportsassets/bettor_account_exposure\.py$",
    r"(^|/)sportsassets/bettor_funded_activation\.py$",
    r"(^|/)sportsassets/bettor_owner_authorization\.py$",
    r"(^|/)sportsassets/bettor_desk_controls\.py$",
    r"(^|/)sportsassets/bettor_live_control\.py$",
    r"(^|/)sportsassets/bettor_learning_authority\.py$",
    r"(^|/)sportsassets/bettor_funded_execution\.py$",
    r"(^|/)sportsassets/bettor_entry_execution\.py$",
    r"(^|/)sportsassets/shadow\.py$",
    r"(^|/)sportsassets/provider_key_proxy\.py$",
    r"(^|/)sportsassets/api/app\.py$",
    r"(^|/)sportsassets/agents/improvement\.py$",
    r"(^|/)tools/improvement_sandbox\.py$",
    r"(^|/)tools/gate_[^/]*$",
    r"(^|/)tools/run_gate\.sh$",
    r"(^|/)tools/trace_the_gates\.py$",
    r"(^|/)tools/assert_tests_ran\.py$",
    r"(^|/)assert_tests_ran\.py$",
    r"(^|/)\.github/",
    r"(^|/)\.env[^/]*$",
    r"secret", r"credential",
)
CRITICAL_LIST = "capital_critical_tests.txt"
SWITCH_RE = re.compile(
    r"^\+.*\b(FUNDED_SUBMISSION_ENABLED|REAL_ORDER_SUBMISSION_ENABLED|"
    r"FUNDED_EXIT_SUBMISSION_ENABLED)\b\s*=\s*(?!False\b)")


# ═════════════════════════════════════════════════════════════════════
# 0 · REFUSALS
# ═════════════════════════════════════════════════════════════════════

class Refused(Exception):
    def __init__(self, refusal: str, **detail):
        super().__init__(refusal)
        self.refusal = refusal
        self.detail = detail


def environment_check(repo: Path) -> None:
    if os.getenv("RENDER") or os.getenv("SPORTSASSETS_PRODUCTION"):
        raise Refused(R_PRODUCTION, why=(
            "RENDER or SPORTSASSETS_PRODUCTION is set: this is a deploy, "
            "and a candidate is never generated where it would serve"))
    got = subprocess.run(["git", "-C", str(repo), "rev-parse",
                          "--is-inside-work-tree"], capture_output=True,
                         text=True)
    if got.returncode != 0 or got.stdout.strip() != "true":
        raise Refused(R_NOT_A_REPO, repo=str(repo))


def diff_files(diff: str) -> list:
    files = set()
    for line in diff.splitlines():
        m = re.match(r"^(?:\+\+\+|---) (?:[ab]/)?(\S+)", line)
        if m and m.group(1) != "/dev/null":
            files.add(m.group(1))
        m = re.match(r"^diff --git a/(\S+) b/(\S+)", line)
        if m:
            files.update(m.groups())
    return sorted(files)


def check_diff(diff: str) -> dict:
    """EVERY PROTECTED THING THE DIFF TOUCHES, BY NAME. Raises Refused."""
    files = diff_files(diff)
    for f in files:
        for pat in PROTECTED_PATHS:
            if re.search(pat, f, flags=re.IGNORECASE):
                raise Refused(R_PROTECTED_PATH, path=f, pattern=pat)
    current = None
    for line in diff.splitlines():
        m = re.match(r"^\+\+\+ (?:b/)?(\S+)", line)
        if m:
            current = m.group(1)
            continue
        if line.startswith("---"):
            continue
        if current and current.endswith(CRITICAL_LIST) and \
                line.startswith("-") and line[1:].strip() and \
                not line[1:].lstrip().startswith("#"):
            raise Refused(R_CRITICAL_REMOVAL, line=line[1:].strip())
        if SWITCH_RE.match(line):
            raise Refused(R_SWITCH, line=line[1:].strip())
    for line in diff.splitlines():
        if line[:1] in "+-" and not line.startswith(("+++", "---")):
            for k in IMP.PROTECTED_KEYS:
                if re.search(r"\b%s\b" % re.escape(k), line):
                    raise Refused(R_PROTECTED_KEY_IN_DIFF, key=k,
                                  line=line.strip()[:200])
    return {"files": files}


# ═════════════════════════════════════════════════════════════════════
# 1 · GIT
# ═════════════════════════════════════════════════════════════════════

def git(repo, *args, check=True, env=None) -> str:
    got = subprocess.run(["git", "-C", str(repo),
                          "-c", "user.name=improvement-sandbox",
                          "-c", "user.email=improvement-sandbox@localhost",
                          "-c", "commit.gpgsign=false", *args],
                         capture_output=True, text=True, env=env)
    if check and got.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args),
                                                  got.stderr.strip()[:400]))
    return got.stdout


def branch_for(task_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._/-]+", "-", str(task_id)).strip("-/.")
    return "improve/%s" % safe


def safe_name(task_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", str(task_id)).strip("_")


# ═════════════════════════════════════════════════════════════════════
# 2 · THE CHANGE
# ═════════════════════════════════════════════════════════════════════

def edit_constant(path: Path, constant: str, value) -> dict:
    """Replace `CONSTANT = <old>` (module level) with the new value,
    keeping any trailing comment. Exactly one assignment must match."""
    text = path.read_text()
    pat = re.compile(r"^(%s\s*=\s*)([^#\n]+?)(\s*(#.*)?)$"
                     % re.escape(constant), re.MULTILINE)
    hits = list(pat.finditer(text))
    if len(hits) != 1:
        raise Refused(R_TARGET, path=str(path), constant=constant,
                      matches=len(hits))
    h = hits[0]
    old = h.group(2).strip()
    new = repr(value) if not isinstance(value, bool) else str(value)
    text = text[:h.start()] + h.group(1) + new + (h.group(3) or "") + \
        text[h.end():]
    path.write_text(text)
    return {"path": str(path), "constant": constant, "old": old, "new": new}


GENERATED_TEST = '''"""GENERATED BY tools/improvement_sandbox.py for {task_id}.

The candidate's versioned default is the proposed value and inside the
change class's registered bounds. It pins the artifact, not the benefit:
the benefit is the replay in the evaluation report."""
import importlib
import importlib.util
import pathlib

TARGET = pathlib.Path(__file__).resolve().parents[{depth}] / {rel!r}
MODULE = {module!r}


def _load():
    if MODULE:
        return importlib.import_module(MODULE)
    spec = importlib.util.spec_from_file_location("candidate_target", TARGET)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_versioned_default_is_the_proposed_value():
    assert getattr(_load(), {constant!r}) == {value!r}


def test_the_value_is_inside_the_class_bounds():
    v = getattr(_load(), {constant!r})
    assert {lo!r} <= v <= {hi!r}
'''


def module_name(path: Path, cwd: Path) -> str | None:
    """The dotted module when the target sits in a package under the test
    directory (relative imports then work), else None (loaded by path)."""
    try:
        rel = path.resolve().relative_to(cwd.resolve())
    except ValueError:
        return None
    parts = list(rel.with_suffix("").parts)
    pkg = cwd
    for part in parts[:-1]:
        pkg = pkg / part
        if not (pkg / "__init__.py").is_file():
            return None
    return ".".join(parts) if len(parts) > 1 else None


def run_tests(cwd: Path, paths: list, *, timeout_s: int) -> dict:
    if not paths:
        return {"passed": None, "why": "no tests were specified",
                "tests": []}
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
           *paths]
    env = {k: v for k, v in os.environ.items()
           if k not in ("RENDER", "SPORTSASSETS_PRODUCTION")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    t0 = time.monotonic()
    try:
        got = subprocess.run(cmd, cwd=str(cwd), capture_output=True,
                             text=True, timeout=timeout_s, env=env)
        rc, outp = got.returncode, (got.stdout + got.stderr)
    except subprocess.TimeoutExpired as exc:
        rc, outp = -1, "TIMEOUT after %ss: %s" % (timeout_s, exc)
    tail = outp.strip().splitlines()[-15:]
    counts = {}
    for k in ("passed", "failed", "error", "errors", "skipped"):
        m = re.search(r"(\d+) %s\b" % k, "\n".join(tail))
        if m:
            counts[k] = int(m.group(1))
    return {"passed": rc == 0, "returncode": rc,
            "command": " ".join(cmd[1:]), "cwd": str(cwd),
            "tests": list(paths), "counts": counts, "summary": tail,
            "elapsed_s": round(time.monotonic() - t0, 3)}


# ═════════════════════════════════════════════════════════════════════
# 3 · THE REPLAY AND THE JUDGEMENT (the CLASS's criteria)
# ═════════════════════════════════════════════════════════════════════

async def _db_replay(dsn: str, cls, params: dict, spec: dict,
                     current: dict) -> dict:
    import asyncpg
    conn = await asyncpg.connect(dsn)
    try:
        now = float(spec.get("evaluation_boundary") or time.time())
        start, tb, eb = IMP._boundaries(spec, now, holdout_days=float(
            spec.get("holdout_days") or 2))
        if cls.name == "COLLECTION_PASS_LIMIT":
            samples = await IMP._pass_samples(conn, tb, eb)
            p90 = await IMP._attempt_p90(conn, start, tb)
            return {"new": IMP.replay_pass_limit(
                samples, limit=int(params["candidates_per_pass"]),
                per_attempt_s=p90),
                "baseline": IMP.replay_pass_limit(
                    samples, limit=int(current.get("candidates_per_pass")
                                       or 3), per_attempt_s=p90),
                "source": "DSN:audrey_collection_samples"}
        if cls.name == "DEREK_ENTRY_THRESHOLD":
            rows = await IMP.derek_rows(conn, start=start, end=eb)
            sp = IMP.split_rows(rows, training_boundary=tb,
                                evaluation_boundary=eb,
                                salt=IMP.DEREK_HOLDOUT_SALT,
                                percent=IMP.DEREK_HOLDOUT_PERCENT)
            return {"new": IMP.replay_derek_threshold(
                sp["holdout"],
                threshold=float(params["min_net_edge_per_contract"])),
                "baseline": IMP.replay_derek_threshold(
                    sp["holdout"], threshold=float(current.get(
                        "min_net_edge_per_contract") or 0.01)),
                "excluded": sp["excluded"],
                "source": "DSN:external_valuations"}
    finally:
        await conn.close()
    return {}


def replay(cls, params: dict | None, spec: dict, dsn: str | None,
           current: dict) -> dict | None:
    rp = spec.get("replay")
    if params is None or rp is False:
        return None
    if isinstance(rp, dict) and rp.get("samples") is not None and \
            cls.name == "COLLECTION_PASS_LIMIT":
        per = rp.get("per_attempt_s")
        return {"new": IMP.replay_pass_limit(
            rp["samples"], limit=int(params["candidates_per_pass"]),
            per_attempt_s=per),
            "baseline": IMP.replay_pass_limit(
                rp["samples"], limit=int(current.get("candidates_per_pass")
                                         or 3), per_attempt_s=per),
            "source": "SPEC_SUPPLIED_RECORDED_SAMPLES"}
    if isinstance(rp, dict) and rp.get("rows") is not None and \
            cls.name == "DEREK_ENTRY_THRESHOLD":
        return {"new": IMP.replay_derek_threshold(
            rp["rows"], threshold=float(params["min_net_edge_per_contract"])),
            "source": "SPEC_SUPPLIED_RECORDED_ROWS"}
    if dsn:
        got = asyncio.run(_db_replay(dsn, cls, params, spec, current))
        return got or None
    return None


def judge_harm(cls, rp: dict | None) -> dict:
    if not rp or not rp.get("new"):
        return {}
    m = rp["new"]
    out = {}
    for k, rule in (cls.harm_metrics or {}).items():
        if not isinstance(rule, dict):
            continue
        v = m.get(k)
        op, lim = next(iter(rule.items()))
        breached = None if v is None else not {
            "<=": v <= lim, ">=": v >= lim, "==": v == lim}[op]
        out[k] = {"value": v, "rule": rule, "breached": breached}
    return out


# ═════════════════════════════════════════════════════════════════════
# 4 · THE RUN
# ═════════════════════════════════════════════════════════════════════

async def _task_spec(dsn: str, task_id: str) -> dict:
    import asyncpg
    conn = await asyncpg.connect(dsn)
    try:
        t = await IMP.read_task(conn, task_id)
    finally:
        await conn.close()
    if t is None:
        raise Refused("NO_SUCH_TASK", task_id=task_id)
    spec = dict(t.get("spec") or {})
    spec.setdefault("task_id", task_id)
    spec.setdefault("assigned_agent", t.get("assignee"))
    return spec


async def _record(dsn: str, *, spec: dict, cls, params, diff, sha, branch,
                  base, tests, report, now) -> dict:
    import asyncpg
    conn = await asyncpg.connect(dsn)
    try:
        got = await IMP.propose(
            conn, task_id=spec["task_id"], change_class=cls.name,
            proposed_by=report["proposed_by"],
            hypothesis=spec.get("hypothesis") or cls.description,
            evidence=dict(spec.get("evidence") or {}, sandbox_report=report),
            affected_behavior=spec.get("affected_behavior")
            or cls.description, params=params, diff=diff,
            touched_keys=spec.get("touched_keys"),
            training_boundary=report["training_boundary"],
            evaluation_boundary=report["evaluation_boundary"],
            artifact_ref="%s@%s" % (branch, sha), base_commit=base,
            test_results=tests, variant={"artifact": sha}, now=now)
        if got.get("ok") and await IMP.tasks_available(conn) and \
                await IMP.read_task(conn, spec["task_id"]) is not None:
            await IMP.task_event(
                conn, spec["task_id"], kind="CANDIDATE_READY",
                actor=report["proposed_by"],
                detail={"candidate_id": got["candidate_id"],
                        "artifact_ref": "%s@%s" % (branch, sha)},
                status="CANDIDATE_READY", now=now)
        return {k: v for k, v in got.items() if k != "candidate"}
    finally:
        await conn.close()


def run(args) -> dict:
    repo = Path(args.repo).resolve()
    environment_check(repo)
    if args.spec:
        spec = json.loads(Path(args.spec).read_text())
    elif args.task_id:
        if not args.dsn:
            raise Refused("A_TASK_ID_NEEDS_A_DSN")
        spec = asyncio.run(_task_spec(args.dsn, args.task_id))
    else:
        raise Refused("A_SPEC_OR_A_TASK_ID_IS_REQUIRED")
    if args.diff_file:
        spec["diff"] = Path(args.diff_file).read_text()
    task_id = str(spec.get("task_id") or "").strip()
    if not task_id:
        raise Refused("THE_SPEC_NAMES_NO_TASK")
    chk = IMP.check_class(spec.get("change_class"))
    if not chk["ok"]:
        raise Refused(chk["refusal"], change_class=spec.get("change_class"))
    cls = chk["cls"]
    params = spec.get("params")
    diff_in = spec.get("diff")
    hit = IMP.protected_touched(list(spec.get("touched_keys") or [])
                                + list((params or {}).keys()))
    if hit:
        raise Refused(IMP.R_PROTECTED_KEY, protected=hit)
    if params is not None:
        pc = IMP.check_params(cls, params)
        if not pc["ok"]:
            raise Refused(pc["refusal"], **{k: v for k, v in pc.items()
                                            if k not in ("ok", "refusal")})
    if params is None and not diff_in:
        raise Refused(R_NO_CHANGE)
    if diff_in:
        check_diff(diff_in)
    target = spec.get("target") or (
        {"path": cls.code_default[0], "constant": cls.code_default[1]}
        if cls.code_default else None)
    if params is not None and cls.kind in (IMP.K_POLICY,) and not target:
        raise Refused(R_TARGET, why="the class names no versioned default "
                                    "and the spec no target")
    if target:
        for pat in PROTECTED_PATHS:
            if re.search(pat, target["path"], flags=re.IGNORECASE):
                raise Refused(R_PROTECTED_PATH, path=target["path"])
    base = git(repo, "rev-parse", "--verify", "%s^{commit}" % (
        args.base or "HEAD")).strip()
    branch = branch_for(task_id)
    exists = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify",
                             "--quiet", "refs/heads/" + branch],
                            capture_output=True, text=True)
    if exists.returncode == 0:
        raise Refused(R_BRANCH_EXISTS, branch=branch,
                      sha=exists.stdout.strip())
    work = Path(tempfile.mkdtemp(prefix="improve-", dir=args.workdir))
    wt = work / "wt"
    git(repo, "worktree", "add", "-b", branch, str(wt), base)
    now = float(args.now if args.now is not None else time.time())
    try:
        edits = []
        test_cwd = wt / (spec.get("test_cwd") if spec.get("test_cwd")
                         is not None else ("backend" if (wt / "backend")
                                           .is_dir() else ""))
        tests = list(spec.get("tests") or [])
        current = IMP.code_default_params(cls)
        if target and params is not None:
            key, val = next(iter(params.items()))
            tpath = wt / target["path"]
            if not tpath.is_file():
                raise Refused(R_TARGET, path=target["path"])
            e = edit_constant(tpath, target["constant"], val)
            try:
                old = json.loads(e["old"].replace("'", '"'))
                if isinstance(old, (int, float)):
                    current = {key: old}
            except ValueError:
                pass
            edits.append(e)
            # THE GENERATED BOUNDS TEST, beside the report
            rdir = test_cwd / "research" / "improvements" / safe_name(
                task_id)
            rdir.mkdir(parents=True, exist_ok=True)
            lo, hi, _ = cls.bounds[key]
            rel = os.path.relpath(tpath, wt)
            depth = len(rdir.relative_to(wt).parts)
            gen = rdir / ("test_candidate_%s.py" % safe_name(task_id))
            gen.write_text(GENERATED_TEST.format(
                task_id=task_id, depth=depth, rel=rel,
                module=module_name(tpath, test_cwd),
                constant=target["constant"], value=val, lo=lo, hi=hi))
            tests.append(os.path.relpath(gen, test_cwd))
            (rdir / "policy_version.json").write_text(json.dumps({
                "agent_id": cls.agent, "policy_key": cls.policy_key,
                "params": params, "change_class": cls.name,
                "task_id": task_id, "state": "CANDIDATE",
                "note": ("a policy version is ACTIVATED only by the class's "
                         "acceptance and canary rule (pre-authorized) or by "
                         "a person; this file is the candidate's artifact")},
                indent=2, sort_keys=True) + "\n")
        if diff_in:
            pf = work / "change.diff"
            pf.write_text(diff_in if diff_in.endswith("\n")
                          else diff_in + "\n")
            chk_apply = subprocess.run(
                ["git", "-C", str(wt), "apply", "--check", str(pf)],
                capture_output=True, text=True)
            if chk_apply.returncode != 0:
                raise Refused(R_APPLY, stderr=chk_apply.stderr[:400])
            git(wt, "apply", str(pf))
            edits.append({"applied_diff_files": diff_files(diff_in)})
        test_results = run_tests(test_cwd, tests,
                                 timeout_s=int(args.test_timeout))
        rp = replay(cls, params, spec, args.dsn, current)
        harm = judge_harm(cls, rp)
        git(wt, "add", "-A")
        diff = git(wt, "diff", "--cached", base)
        # A GENERATED FILE MUST NOT SMUGGLE A PROTECTED EDIT EITHER
        check_diff(diff)
        proposer = spec.get("proposed_by") or (PROPOSER_PREFIX + str(
            spec.get("assigned_agent") or cls.agent))
        report = {
            "version": VERSION, "task_id": task_id,
            "change_class": cls.name, "change_kind": cls.kind,
            "assigned_agent": spec.get("assigned_agent") or cls.agent,
            "proposed_by": proposer,
            "hypothesis": spec.get("hypothesis") or cls.description,
            "evidence": spec.get("evidence") or {},
            "affected_behavior": spec.get("affected_behavior")
            or cls.description,
            "training_boundary": spec.get("training_boundary_detail") or {
                "end": spec.get("training_boundary")},
            "evaluation_boundary": spec.get("evaluation_boundary_detail")
            or {"end": spec.get("evaluation_boundary")},
            "success_metrics": cls.success_metrics,
            "harm_metrics": cls.harm_metrics,
            "params": params, "edits": edits, "diff": diff,
            "test_results": test_results,
            "replay": rp, "harm": harm,
            "release_scope": (IMP.SCOPE_PREAUTH if cls.pre_authorized
                              else IMP.SCOPE_APPROVAL),
            "rollback": cls.rollback or ("revert the %s commit" % branch),
            "base_commit": base, "branch": branch,
            "generated_at": _dt.datetime.fromtimestamp(
                now, _dt.timezone.utc).isoformat(),
            "never": ["pushed", "merged", "deployed",
                      "modified the serving process"]}
        rdir = test_cwd / "research" / "improvements" / safe_name(task_id)
        rdir.mkdir(parents=True, exist_ok=True)
        (rdir / "evaluation.json").write_text(
            json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
        git(wt, "add", "-A")
        git(wt, "commit", "-q", "-m",
            "improve(%s): %s candidate\n\nHypothesis: %s\nTests passed: %s\n"
            "Generated by tools/improvement_sandbox.py; not pushed, not "
            "deployed." % (task_id, cls.name, report["hypothesis"][:200],
                           test_results.get("passed")))
        sha = git(wt, "rev-parse", "HEAD").strip()
        report["commit"] = sha
        report["artifact_ref"] = "%s@%s" % (branch, sha)
        report["diff_sha256"] = hashlib.sha256(diff.encode()).hexdigest()
        recorded = None
        if args.dsn and not args.no_record:
            recorded = asyncio.run(_record(
                args.dsn, spec=spec, cls=cls, params=params, diff=diff,
                sha=sha, branch=branch, base=base, tests=test_results,
                report={k: v for k, v in report.items() if k != "diff"},
                now=now))
        report["recorded"] = recorded
        return report
    finally:
        if not args.keep_worktree:
            subprocess.run(["git", "-C", str(repo), "worktree", "remove",
                            "--force", str(wt)], capture_output=True)
            shutil.rmtree(work, ignore_errors=True)
            subprocess.run(["git", "-C", str(repo), "worktree", "prune"],
                           capture_output=True)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--spec")
    p.add_argument("--task-id")
    p.add_argument("--dsn", default=None)
    p.add_argument("--repo", default=str(BACKEND.parent))
    p.add_argument("--base", default=None)
    p.add_argument("--diff-file", default=None)
    p.add_argument("--out", default=None)
    p.add_argument("--workdir", default=None)
    p.add_argument("--now", type=float, default=None)
    p.add_argument("--test-timeout", default=600)
    p.add_argument("--keep-worktree", action="store_true")
    p.add_argument("--no-record", action="store_true")
    args = p.parse_args(argv)
    try:
        report = run(args)
        code = 0
    except Refused as r:
        report = {"ok": False, "refusal": r.refusal, "detail": r.detail}
        code = 4 if r.refusal in (R_PRODUCTION, R_NOT_A_REPO) else (
            3 if r.refusal in (R_APPLY, R_TARGET) else 2)
    text = json.dumps(report, indent=2, sort_keys=True, default=str)
    if args.out:
        Path(args.out).write_text(text + "\n")
    print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
