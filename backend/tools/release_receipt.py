"""THE RELEASE RECEIPT: ONE IMMUTABLE, CONTENT-ADDRESSED RECORD PER GATE RUN.

    python tools/release_receipt.py --gate f33b          # from gates.json
    python tools/release_receipt.py --gate-dir DIR --gate-id ID \\
        --candidate NAME --sha SHA --base-sha SHA [--evidence FILE]

WHAT IT ANSWERS. Is this exact commit acceptable to release, and why --
from the gate's own artifacts and git, never from a summary someone typed.
It reads the gate output directory (head_report.json, head_meta.json,
timeline.txt, head.migrate.log, the console and the streamed failures), the
matched baseline reports, the capital-critical list AT THE CANDIDATE SHA, and
git; it writes `<gate>__<sha12>.json` whose `receipt_sha256` is the sha256 of
its own canonical JSON (every field except that one).

THE STATES, IN PRECEDENCE ORDER -- the first that applies wins:

    VOID          the run's environment broke: a disk-full (errno 28 /
                  ENOSPC / DiskFullError / "No space left on device") or a
                  database restart appears anywhere in its artifacts, or the
                  migration did not complete. Nothing in a void run is
                  evidence, including its passes.
    INCOMPLETE    no completed report yet (still running, or killed) and no
                  void signature. Not evidence.
    INVALID       a report exists but cannot support a comparison (the
                  gate_verdict validity rules), the SHA that ran is not the
                  SHA named, or a critical proof did not execute.
    REJECTED      a completed, valid run with a failure identity a baseline
                  lacks, or a critical proof that did not PASS.
    GATED_PENDING_SIGNOFF
                  the gate accepted the run; open items (provenance
                  exceptions, PENDING deploy/readback slots, no approver)
                  are listed and nothing is waived silently.
    ACCEPTED      gated, every blocking exception resolved or waived BY A
                  NAMED APPROVER in the evidence file, and an approver
                  recorded.

STAGES ARE NEVER COLLAPSED. BUILT / TESTED / GATED / DEPLOYED /
PRODUCTION_READBACK / FORWARD_VALIDATED each carry their own status and
evidence; a slot with no evidence is PENDING with the reason, never guessed.
A deploy slot is filled only from an evidence file entry that names its
source (a run URL, a log line); a 7-character SHA is recorded as a PREFIX
match, never promoted to a full one.

HOW GITHUB CI RELATES (read 2026-10-04 through the GitHub API, recorded per
receipt under `github_ci` from evidence/<gate>.json). Three workflows run on
every push of a candidate SHA:
  * commit-guard   -- RED on fd6cc5b and on 8e62749: the candidate commits
                      carry neither [skip render] nor [deploy-approved]. This
                      receipt re-runs that rule locally over base..sha as
                      `commit_guard` and treats a FAIL as a BLOCKING
                      provenance exception until a named approver waives it.
  * backend-tests  -- RED: 92 failed on both SHAs, under Python 3.11 with NO
                      database (1,502 / 1,537 skipped). It is not the gate's
                      environment (3.12.3 + migrated Postgres, 48 skipped),
                      so its failure set is not the gate's 70-failure
                      baseline, and until c28 its log kept only `tail -30`, so
                      the failing node ids were UNREADABLE. The one visible
                      failure (render-ops headroom) is fixed in c28. From c28
                      the workflow prints every FAILED id and uploads a junit
                      report, so the next candidate's CI set can be compared.
  * engine-diagnostic -- green; a live-engine probe, not a test suite.
CI is recorded and shown, never used as the acceptance authority, and never
hidden: a red CI run is a non-blocking exception on the receipt.

IMMUTABILITY. A receipt file is never rewritten. Re-running with the same
inputs reproduces the same content and writes nothing new; new evidence (a
deploy readback, an approver) produces a NEW receipt that names the one it
supersedes. `index.json` lists them; the API verifies each hash on read.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import subprocess
import sys

SCHEMA = "BETTOR_RELEASE_RECEIPT_V1"
TOOLS = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(TOOLS)
REPO = os.path.dirname(BACKEND)
RECEIPT_DIR = os.path.join(BACKEND, "sportsassets", "release_receipts")
MANIFEST = os.path.join(RECEIPT_DIR, "gates.json")
DEFAULT_BASELINES = ("/tmp/claude-0/gate_explore_out",
                     "/tmp/claude-0/gate_talk_out")

#: Signatures that void a run. Each is an environment failure, not a test
#: outcome: once one appears, a pass is as untrustworthy as a fail.
VOID_SIGNATURES = (
    ("DISK_FULL", re.compile(r"No space left on device|\[Errno 28\]|"
                             r"\berrno 28\b|DiskFullError|\bENOSPC\b|"
                             r"could not extend file", re.I)),
    ("DATABASE_RESTARTED", re.compile(
        r"the database system is (?:shutting down|starting up|in recovery "
        r"mode)|terminating connection due to administrator command", re.I)),
)

#: Every pushed commit must declare its deploy intent (commit-guard.yml).
DEPLOY_INTENT = ("[skip render]", "[deploy-approved]")

STAGES = ("BUILT", "TESTED", "GATED", "DEPLOYED", "PRODUCTION_READBACK",
          "FORWARD_VALIDATED")
PENDING = "PENDING"


# ═════════════════════════════════════════════════════════════════════
# small readers
# ═════════════════════════════════════════════════════════════════════

def _sha256_file(path):
    if not path or not os.path.isfile(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def _json(path):
    txt = _read(path)
    if txt is None:
        return None, "absent: %s" % path
    try:
        return json.loads(txt), None
    except json.JSONDecodeError as exc:
        return None, "not valid JSON (%s): %s" % (exc, path)


def _git(*args, repo=REPO):
    r = subprocess.run(["git", "-C", repo] + list(args),
                       capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def canonical(doc) -> bytes:
    return json.dumps(doc, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def receipt_hash(doc) -> str:
    body = {k: v for k, v in doc.items() if k != "receipt_sha256"}
    return hashlib.sha256(canonical(body)).hexdigest()


def verify(doc) -> bool:
    return isinstance(doc, dict) and doc.get("receipt_sha256") == receipt_hash(doc)


def _pending(why):
    return {"status": PENDING, "why": why}


# ═════════════════════════════════════════════════════════════════════
# the gate run
# ═════════════════════════════════════════════════════════════════════

def failure_signature(longrepr):
    """The failure's last `E ` line, with addresses and long numbers masked,
    so a baseline and a head failure can be compared for IDENTITY of cause,
    not only identity of node id."""
    if not longrepr:
        return None
    lines = [ln.strip() for ln in str(longrepr).splitlines()
             if ln.strip().startswith("E ")]
    sig = lines[-1] if lines else str(longrepr).strip().splitlines()[-1]
    sig = re.sub(r"0x[0-9a-fA-F]+", "0x#", sig)
    sig = re.sub(r"\d{4,}", "#", sig)
    sig = re.sub(r"/tmp/[^\s'\"]+", "/tmp/#", sig)
    return sig[:240]


def scan_void(gate_dir, report_doc):
    """Every void signature in every artifact of the run."""
    hits = []
    for fname in ("head_console.txt", "head.run.log", "head.migrate.log",
                  "head_report.json.failures.jsonl", "timeline.txt"):
        txt = _read(os.path.join(gate_dir, fname))
        if not txt:
            continue
        for code, rx in VOID_SIGNATURES:
            ms = list(rx.finditer(txt))
            if ms:
                m = ms[0]
                line = txt[txt.rfind("\n", 0, m.start()) + 1:
                           txt.find("\n", m.end())][:300]
                hits.append({"code": code, "artifact": fname,
                             "occurrences": len(ms), "first": line})
    if report_doc:
        for nid, v in (report_doc.get("nodes") or {}).items():
            for code, rx in VOID_SIGNATURES:
                if v.get("longrepr") and rx.search(str(v["longrepr"])):
                    hits.append({"code": code, "artifact": "head_report.json",
                                 "node": nid, "occurrences": 1,
                                 "first": failure_signature(v["longrepr"])})
    return hits


def read_timeline(gate_dir):
    txt = _read(os.path.join(gate_dir, "timeline.txt")) or ""
    out = {"present": bool(txt), "start": None, "pytest_exit": None,
           "finished": None, "verdict_exits": {}}
    for ln in txt.splitlines():
        m = re.match(r"=== head (\S+) start", ln)
        if m:
            out["start"] = m.group(1)
        m = re.match(r"=== head (\S+) pytest-exit=(\d+)", ln)
        if m:
            out["finished"], out["pytest_exit"] = m.group(1), int(m.group(2))
        m = re.match(r"=== verdict vs (\S+) exit=(\d+)", ln)
        if m:
            out["verdict_exits"][m.group(1)] = int(m.group(2))
    return out


def read_migrate_log(gate_dir):
    txt = _read(os.path.join(gate_dir, "head.migrate.log"))
    if txt is None:
        return {"present": False, "completed": False, "applied": []}
    return {"present": True,
            "completed": "migrations complete" in txt
                         and "Traceback" not in txt,
            "applied": re.findall(r"applying (\S+\.sql)", txt),
            "drift_warnings": len(re.findall(r"CHANGED-AFTER-APPLY|WAS "
                                             r"ALREADY APPLIED", txt))}


def _gate_verdict_module():
    sys.path.insert(0, TOOLS)
    try:
        import gate_verdict  # noqa: WPS433
    finally:
        sys.path.pop(0)
    return gate_verdict


def critical_outcomes(head, critical_entries, gv):
    """The gate_verdict critical rules, recorded rather than printed."""
    nodes = head.get("nodes") or {}
    desel = set(head.get("deselected") or ())
    coll = set(head.get("collected") or ())
    expanded, not_run, not_passed = [], [], []
    for c in critical_entries:
        if "::" in c:
            expanded.append(c)
            continue
        mine = sorted(n for n in (coll | desel) if n.split("::", 1)[0] == c)
        if not mine:
            not_run.append({"node": c, "why": "COLLECTED_NOTHING"})
        expanded.extend(mine)
    passed = 0
    for c in expanded:
        v = nodes.get(c)
        if c in desel:
            not_run.append({"node": c, "why": "DESELECTED"})
        elif c not in coll:
            not_run.append({"node": c, "why": "NOT_COLLECTED"})
        elif v is None:
            not_run.append({"node": c, "why": "NO_PHASE_RECORD"})
        elif v.get("outcome") in gv.NOT_RUN:
            not_run.append({"node": c, "why": "SKIPPED"})
        elif v.get("outcome") != gv.CRITICAL_PASS:
            not_passed.append({"node": c, "outcome": v.get("outcome"),
                               "signature": failure_signature(
                                   v.get("longrepr"))})
        elif any((v.get("phases") or {}).get(p) != gv.CRITICAL_PASS
                 for p in gv.CRITICAL_PHASES):
            not_run.append({"node": c, "why": "INCOMPLETE_PHASES"})
        else:
            passed += 1
    return {"entries": len(critical_entries), "expanded_nodes": len(expanded),
            "passed": passed, "not_run": not_run, "not_passed": not_passed}


# ═════════════════════════════════════════════════════════════════════
# provenance
# ═════════════════════════════════════════════════════════════════════

def migrations_at(sha):
    out = _git("ls-tree", "--name-only", sha, "backend/migrations/")
    if out is None:
        return None
    return sorted(os.path.basename(p) for p in out.split()
                  if p.endswith(".sql"))


def commit_guard(base_sha, sha):
    """commit-guard.yml's rule, run locally over base..sha: every commit in
    the range declares its deploy intent. Render deploys a service branch on
    push, so an undeclared commit pushed to it may have deployed itself."""
    if not base_sha:
        return _pending("no base SHA named, so there is no range to check")
    out = _git("rev-list", "%s..%s" % (base_sha, sha))
    if out is None:
        return {"status": "UNREADABLE",
                "why": "git could not list %s..%s" % (base_sha[:12], sha[:12])}
    bad = []
    shas = out.split()
    for c in shas:
        msg = _git("log", "-1", "--format=%B", c) or ""
        if not any(t in msg for t in DEPLOY_INTENT):
            bad.append({"sha": c, "subject": (_git("log", "-1", "--format=%s",
                                                   c) or "").strip()[:120]})
    return {"status": "PASS" if not bad else "FAIL",
            "rule": "every commit in base..sha carries [skip render] or "
                    "[deploy-approved] (.github/workflows/commit-guard.yml)",
            "range": "%s..%s" % (base_sha, sha), "commits": len(shas),
            "undeclared": bad}


def _slot(evidence, key, sha, kind):
    """A deploy/readback slot, filled only from evidence that names a source."""
    e = (evidence or {}).get(key)
    if not e:
        return _pending("no evidence recorded for %s" % kind)
    src = e.get("source") or {}
    if not (src.get("url") or src.get("path")) or not src.get("lines"):
        return _pending("evidence for %s names no source URL/path and quoted "
                        "lines, so it is not accepted" % kind)
    out = dict(e)
    seen = str(e.get("sha") or "").lower()
    if seen:
        if not re.fullmatch(r"[0-9a-f]{7,40}", seen):
            out["match"] = "NOT_A_SHA"
        elif len(seen) == 40:
            out["match"] = "EXACT" if seen == sha else "DIFFERENT_BUILD"
        else:
            out["match"] = ("PREFIX_%d" % len(seen) if sha.startswith(seen)
                            else "DIFFERENT_BUILD")
    out.setdefault("status", "RECORDED")
    return out


# ═════════════════════════════════════════════════════════════════════
# the receipt
# ═════════════════════════════════════════════════════════════════════

def build(gate_id, gate_dir, candidate, sha, base_sha=None, baselines=(),
          evidence=None, evidence_path=None, run_script=None,
          generated_at=None, supersedes=None, critical_entries=None):
    gv = _gate_verdict_module()
    sha = sha.lower()
    head_path = os.path.join(gate_dir, "head_report.json")
    meta, meta_err = _json(os.path.join(gate_dir, "head_meta.json"))
    head, head_err = (gv.load(head_path) if os.path.isfile(head_path)
                      else (None, "absent: %s" % head_path))
    timeline = read_timeline(gate_dir)
    migrate = read_migrate_log(gate_dir)
    void_hits = scan_void(gate_dir, head)

    problems = []
    if head is not None:
        problems += gv.validate(head, "HEAD")
    if meta and meta.get("commit") and meta["commit"].lower() != sha:
        problems.append("the gate ran %s, not the named %s"
                        % (meta["commit"], sha))
    if head is not None and meta and not meta.get("report_present", True):
        problems.append("head_meta says the report was not present")

    # ── baselines: who already failed, and identically? ─────────────
    head_fail = gv.failing(head) if head else set()
    hnodes = (head or {}).get("nodes") or {}
    base_recs, new_by_baseline = [], {}
    base_docs = []
    for bdir in baselines:
        bpath = os.path.join(bdir, "head_report.json")
        bmeta, _ = _json(os.path.join(bdir, "head_meta.json"))
        bdoc, berr = gv.load(bpath)
        bprob = gv.validate(bdoc, "BASELINE") if bdoc else [berr]
        rec = {"dir": bdir, "report_path": bpath,
               "report_sha256": _sha256_file(bpath),
               "sha": (bmeta or {}).get("commit"),
               "migration_max": (bmeta or {}).get("migration_max"),
               "valid": not bprob, "problems": bprob,
               "counts": (bdoc or {}).get("counts")}
        base_recs.append(rec)
        base_docs.append((rec, bdoc))
        if bdoc and head:
            new_by_baseline[bdir] = sorted(head_fail - gv.failing(bdoc))
        if bprob:
            problems += ["baseline %s: %s" % (bdir, p) for p in bprob]
    if not baselines:
        problems.append("no baseline named: an identity comparison needs one")

    new_regressions = sorted(set().union(*new_by_baseline.values())) \
        if new_by_baseline else []
    known = []
    for nid in sorted(head_fail):
        hsig = failure_signature(hnodes.get(nid, {}).get("longrepr"))
        why = []
        for rec, bdoc in base_docs:
            bv = ((bdoc or {}).get("nodes") or {}).get(nid)
            if bv and bv.get("outcome") in gv.FAILING:
                bsig = failure_signature(bv.get("longrepr"))
                why.append({"baseline_sha": rec["sha"],
                            "report_path": rec["report_path"],
                            "baseline_signature": bsig,
                            "signature_identical": bsig == hsig})
        if why and len(why) == len(base_docs):
            known.append({"node": nid, "head_signature": hsig,
                          "baselines": why,
                          "identical_in_every_baseline":
                              all(w["signature_identical"] for w in why)})
    changed_cause = [k["node"] for k in known
                     if not k["identical_in_every_baseline"]]

    # ── per test file outcomes: what a requirement's tests did at this SHA
    test_files = {}
    for nid, v in hnodes.items():
        f = nid.split("::", 1)[0]
        row = test_files.setdefault(f, {"passed": 0, "failed": 0,
                                        "skipped": 0, "xfailed": 0,
                                        "xpassed": 0})
        o = v.get("outcome")
        if o in row:
            row[o] += 1

    # ── critical list, read AT THE CANDIDATE SHA ────────────────────
    crit_txt = ("\n".join(critical_entries) if critical_entries is not None
                else _git("show", "%s:backend/tools/capital_critical_tests.txt"
                          % sha))
    crit = None
    if crit_txt is None:
        problems.append("the critical list could not be read at %s" % sha[:12])
    else:
        entries = [ln.strip() for ln in crit_txt.splitlines()
                   if ln.strip() and not ln.startswith("#")]
        crit = {"path": "backend/tools/capital_critical_tests.txt@%s" % sha[:12],
                "sha256": hashlib.sha256(crit_txt.encode()).hexdigest()}
        if head:
            crit.update(critical_outcomes(head, entries, gv))

    # ── the state ───────────────────────────────────────────────────
    completed = head is not None and head.get("session_complete")
    if void_hits or (migrate["present"] and not migrate["completed"]):
        state = "VOID"
    elif head is None:
        state = "INCOMPLETE"
    elif problems or (crit and crit.get("not_run")):
        state = "INVALID"
    elif new_regressions or (crit and crit.get("not_passed")):
        state = "REJECTED"
    else:
        state = "GATED"

    void_conditions = [
        {"condition": "disk full (errno 28 / ENOSPC / DiskFullError / No "
                      "space left on device) in any artifact",
         "tripped": any(h["code"] == "DISK_FULL" for h in void_hits)},
        {"condition": "database restarted during the run",
         "tripped": any(h["code"] == "DATABASE_RESTARTED" for h in void_hits)},
        {"condition": "migration log absent or did not complete",
         "tripped": not migrate["completed"]},
    ]

    # ── provenance and deploy slots ─────────────────────────────────
    mig_head, mig_base = migrations_at(sha), (migrations_at(base_sha)
                                              if base_sha else None)
    guard = commit_guard(base_sha, sha)
    ev = evidence or {}
    api = _slot(ev, "api_deploy", sha, "the API deploy")
    workers = _slot(ev, "worker_deploy", sha, "the worker deploy")
    frontend = _slot(ev, "frontend_deploy", sha, "the frontend deploy")
    schema = _slot(ev, "production_schema", sha, "the production schema")
    readbacks = ev.get("production_readbacks") or []
    readbacks = [r for r in readbacks
                 if (r.get("source") or {}).get("url")
                 or (r.get("source") or {}).get("path")]
    approver = ev.get("approver")
    if not (approver and approver.get("name") and approver.get("at")
            and approver.get("source")):
        approver = _pending("no named approver recorded with time and source")
    ci = ev.get("github_ci") or _pending("no GitHub CI evidence recorded")

    def _live(slot):
        return (slot.get("status") == "LIVE"
                and slot.get("match") in ("EXACT",) + tuple(
                    "PREFIX_%d" % n for n in range(7, 40)))

    stages = {
        "BUILT": {"status": "PASS" if _git("cat-file", "-e", sha + "^{commit}")
                  is not None else "FAIL",
                  "evidence": "commit %s exists in this repository" % sha},
        "TESTED": {"status": ("VOID" if state == "VOID" else "PASS" if completed
                              else PENDING if head is None else "FAIL"),
                   "evidence": head_path},
        "GATED": {"status": {"GATED": "PASS", "VOID": "VOID",
                             "INCOMPLETE": PENDING}.get(state, "FAIL"),
                  "evidence": "matched-baseline comparison vs %d baseline(s)"
                              % len(baselines)},
        "DEPLOYED": {"status": ("PASS" if _live(api) and _live(workers)
                                else "PARTIAL" if _live(api) or _live(workers)
                                else PENDING),
                     "api": api.get("status"), "workers": workers.get("status")},
        "PRODUCTION_READBACK": ({"status": "RECORDED", "count": len(readbacks)}
                                if readbacks else
                                _pending("no production readback recorded")),
        "FORWARD_VALIDATED": _pending(
            "forward validation is a later, separate observation; nothing "
            "recorded"),
    }

    blockers = []
    if state != "GATED":
        blockers.append({"code": "GATE_" + state, "blocking": True})
    if guard.get("status") == "FAIL":
        blockers.append({"code": "COMMIT_GUARD_FAIL", "blocking": True,
                         "detail": "%d of %d commits in %s declare no deploy "
                                   "intent" % (len(guard["undeclared"]),
                                               guard["commits"], guard["range"])})
    if changed_cause:
        blockers.append({"code": "BASELINE_FAILURE_CAUSE_CHANGED",
                         "blocking": False, "nodes": changed_cause,
                         "detail": "same node id fails in the baseline, but "
                                   "with a different last error line"})
    if isinstance(ci, list) and any(r.get("conclusion") == "failure"
                                    for r in ci):
        blockers.append({"code": "GITHUB_CI_RED", "blocking": False,
                         "detail": "see github_ci; CI runs a different "
                                   "environment and its failing ids are not "
                                   "comparable to the gate (see notes)"})
    waivers = {w.get("code"): w for w in (ev.get("waivers") or [])
               if w.get("by") and w.get("at")}
    for b in blockers:
        if b["code"] in waivers:
            b["waived_by"] = waivers[b["code"]]
    open_blocking = [b for b in blockers
                     if b["blocking"] and "waived_by" not in b]
    if state != "GATED":
        acceptance = state
    elif open_blocking or approver.get("status") == PENDING:
        acceptance = "GATED_PENDING_SIGNOFF"
    else:
        acceptance = "ACCEPTED"

    doc = {
        "schema": SCHEMA,
        "gate_id": gate_id,
        "candidate": candidate,
        "sha": sha,
        "base_sha": base_sha,
        "state": state,
        "acceptance": acceptance,
        "blockers": blockers,
        "stages": stages,
        "gate": {
            "dir": gate_dir,
            "run_script": run_script,
            "run_script_sha256": _sha256_file(run_script),
            "report_path": head_path,
            "report_sha256": _sha256_file(head_path),
            "report_error": head_err if head is None else None,
            "meta": ({k: meta.get(k) for k in (
                "commit", "database", "pytest_exitstatus", "python",
                "pytest", "pip_freeze_sha256_16", "migration_max",
                "public_tables", "funded_tables")} if meta else None),
            "meta_error": meta_err if meta is None else None,
            "timeline": timeline,
            "migrate": migrate,
            "suites": ((head or {}).get("environment") or {}).get("argv"),
            "collected": (head or {}).get("collected_count"),
            "executed": (head or {}).get("executed_count"),
            "counts": (head or {}).get("counts"),
            "session_complete": (head or {}).get("session_complete"),
            "exitstatus": (head or {}).get("exitstatus"),
            "validity_problems": problems,
            "verdict_files": {
                f: next((ln for ln in (_read(os.path.join(gate_dir, f)) or "")
                         .splitlines() if ln.startswith("VERDICT:")), None)
                for f in sorted(os.listdir(gate_dir))
                if f.startswith("verdict_vs_")} if os.path.isdir(gate_dir)
            else {},
        },
        "void_conditions": void_conditions,
        "void_hits": void_hits,
        "baselines": base_recs,
        "new_regressions": new_regressions,
        "new_regressions_by_baseline": new_by_baseline,
        "known_baseline_failures": known,
        "test_files": test_files,
        "critical": crit,
        "migrations": {
            "at_sha": mig_head,
            "at_base": mig_base,
            "new_vs_base": (sorted(set(mig_head or ()) - set(mig_base or ()))
                            if mig_head is not None and mig_base is not None
                            else None),
            "max_at_sha": (mig_head or [None])[-1],
            "applied_by_gate": migrate["applied"],
        },
        "commit_guard": guard,
        "github_ci": ci,
        "deploy": {"api": api, "workers": workers, "frontend": frontend},
        "production_schema": schema,
        "production_readbacks": readbacks or _pending(
            "no production readback recorded"),
        "approver": approver,
        "evidence_file": evidence_path,
        "evidence_sha256": _sha256_file(evidence_path),
        "supersedes": supersedes,
        "generated_at": generated_at or _dt.datetime.now(_dt.timezone.utc)
        .isoformat(timespec="seconds"),
        "tool": "backend/tools/release_receipt.py",
    }
    doc["receipt_sha256"] = receipt_hash(doc)
    return doc


# ═════════════════════════════════════════════════════════════════════
# write, immutably, and index
# ═════════════════════════════════════════════════════════════════════

def _same_content(a, b):
    strip = ("receipt_sha256", "generated_at", "supersedes")
    return ({k: v for k, v in a.items() if k not in strip}
            == {k: v for k, v in b.items() if k not in strip})


def existing(out_dir, gate_id):
    out = []
    for f in sorted(os.listdir(out_dir)) if os.path.isdir(out_dir) else ():
        if f.startswith(gate_id + "__") and f.endswith(".json"):
            d, _ = _json(os.path.join(out_dir, f))
            if d:
                out.append((f, d))
    return sorted(out, key=lambda t: t[1].get("generated_at") or "")


def write(doc, out_dir=RECEIPT_DIR):
    """Write a new receipt unless one with identical content exists.
    Returns (path, written: bool)."""
    os.makedirs(out_dir, exist_ok=True)
    prior = existing(out_dir, doc["gate_id"])
    for f, d in prior:
        if _same_content(d, doc):
            return os.path.join(out_dir, f), False
    if prior and not doc.get("supersedes"):
        doc["supersedes"] = prior[-1][1].get("receipt_sha256")
        doc["receipt_sha256"] = receipt_hash(doc)
    name = "%s__%s.json" % (doc["gate_id"], doc["receipt_sha256"][:12])
    path = os.path.join(out_dir, name)
    if os.path.exists(path):
        raise SystemExit("refusing to overwrite an existing receipt: %s" % path)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True, ensure_ascii=False)
        fh.write("\n")
    write_index(out_dir)
    return path, True


def write_index(out_dir=RECEIPT_DIR):
    rows = []
    for f in sorted(os.listdir(out_dir)):
        if "__" not in f or not f.endswith(".json"):
            continue
        d, _ = _json(os.path.join(out_dir, f))
        if not d or d.get("schema") != SCHEMA:
            continue
        rows.append({"file": f, "gate_id": d["gate_id"],
                     "candidate": d["candidate"], "sha": d["sha"],
                     "state": d["state"], "acceptance": d["acceptance"],
                     "generated_at": d["generated_at"],
                     "receipt_sha256": d["receipt_sha256"],
                     "supersedes": d.get("supersedes"),
                     "hash_verified": verify(d)})
    rows.sort(key=lambda r: (r["generated_at"], r["gate_id"]))
    with open(os.path.join(out_dir, "index.json"), "w", encoding="utf-8") as fh:
        json.dump({"schema": SCHEMA + "_INDEX", "receipts": rows}, fh,
                  indent=1, sort_keys=True)
        fh.write("\n")
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--gate", help="a gate id listed in gates.json")
    ap.add_argument("--gate-id")
    ap.add_argument("--gate-dir")
    ap.add_argument("--candidate")
    ap.add_argument("--sha")
    ap.add_argument("--base-sha")
    ap.add_argument("--baseline", action="append")
    ap.add_argument("--evidence")
    ap.add_argument("--run-script")
    ap.add_argument("--out-dir", default=RECEIPT_DIR)
    ap.add_argument("--print", action="store_true",
                    help="print the receipt and write nothing")
    a = ap.parse_args(argv)
    spec = {}
    if a.gate:
        man, err = _json(MANIFEST)
        if man is None:
            raise SystemExit(err)
        spec = next((g for g in man["gates"] if g["gate_id"] == a.gate), None)
        if spec is None:
            raise SystemExit("gate %s is not in %s" % (a.gate, MANIFEST))
    gid = a.gate_id or spec.get("gate_id")
    gdir = a.gate_dir or spec.get("gate_dir") or "/tmp/claude-0/gate_%s_out" % gid
    ev_path = a.evidence or spec.get("evidence")
    if ev_path and not os.path.isabs(ev_path):
        ev_path = os.path.join(REPO, ev_path)
    evidence = None
    if ev_path:
        evidence, err = _json(ev_path)
        if evidence is None:
            raise SystemExit(err)
    for need in ("candidate", "sha"):
        if not (getattr(a, need) or spec.get(need)):
            raise SystemExit("--%s is required (or list the gate in gates.json)"
                             % need)
    doc = build(gid, gdir, a.candidate or spec["candidate"],
                a.sha or spec["sha"], a.base_sha or spec.get("base_sha"),
                a.baseline or spec.get("baselines") or DEFAULT_BASELINES,
                evidence=evidence, evidence_path=ev_path and os.path.relpath(
                    ev_path, REPO),
                run_script=a.run_script or spec.get("run_script")
                or "/tmp/claude-0/gate_%s_run.sh" % gid)
    if a.print:
        print(json.dumps(doc, indent=1, sort_keys=True))
        return 0
    path, written = write(doc, a.out_dir)
    print("%s %s  state=%s acceptance=%s new_regressions=%d"
          % ("WROTE" if written else "UNCHANGED (identical receipt exists)",
             os.path.relpath(path, REPO), doc["state"], doc["acceptance"],
             len(doc["new_regressions"])))
    for b in doc["blockers"]:
        print("  blocker %-32s blocking=%s %s" % (b["code"], b["blocking"],
                                                   b.get("detail", "")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
