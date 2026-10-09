"""THE GRADING-LOGIC DIFF REPORT: THE PINNED PRIOR EVALUATOR AND THE CURRENT
ONE ON THE SAME INPUTS, EVERY UNIT WHOSE VERDICT DIFFERS LISTED (RC6 lane E,
owner directive 2E).

Why. The judge (pm-acceptance, its own commit) grades the release, and the
judge's code changes between releases. A release whose acceptance turns
green because the grading logic changed -- not because the system did --
would approve itself silently. So every run also executes a FIXED earlier
evaluator (the workflow input prior_evaluator_sha; default 2fadc8dc, the
RC5 judge that graded 69a8a07e in pm-acceptance 37836393458) over a copy of
the very same packet with the very same arguments, and this file compares
the two scorecards unit by unit:

  * LIFTED (failed before, passes now), LOWERED (the reverse),
    RECLASSIFIED (same pass / fail, another class: FAIL / FORWARD /
    READ_UNAVAILABLE / UNMEASURED), ADDED and REMOVED units, and every
    category whose readiness, pass or components moved;
  * each difference carries the current evaluator's DECLARED reason
    (scorecard_14.GRADING_CHANGES, written into scorecard_14.json) or
    UNDECLARED -- a difference is never dropped for lacking a reason;
  * units whose verdict is unchanged but whose detail differs are named
    too (detail_only_changes).

The inputs are identical by construction (a byte copy of acc/, the same
--release-sha / --frontend-preview), so every difference is the grading
logic's. The prior evaluator's source is read from its commit (git show
<sha>:backend/tools/scorecard_14.py, by the workflow) and its sha256 is
recorded; it runs under `python3 -I` in a subprocess and writes only into
the copy. Nothing here changes the current verdict: the report is
evidence, attested with the packet.

    python3 -I tools/grading_diff.py run --acc ACC --release-sha SHA
        --prior-source FILE --prior-sha SHA [--frontend-preview P]
        [--current-sha SHA] --out ACC/grading_diff.json

Standard library only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

VERSION = "GRADING_DIFF_V1"
NO_DIFFERENCE = "NO_DIFFERENCE"
DECLARED = "DECLARED_DIFFERENCES"
UNDECLARED = "UNDECLARED_DIFFERENCES"
PRIOR_UNAVAILABLE = "PRIOR_EVALUATOR_UNAVAILABLE"
LIFTED, LOWERED, RECLASSIFIED, ADDED, REMOVED = (
    "LIFTED", "LOWERED", "RECLASSIFIED", "ADDED", "REMOVED")
#: files the current run wrote after (or from) the scorecard: never inputs
NOT_INPUTS = ("scorecard_14.json", "scorecard_14_prior.json",
              "grading_diff.json", "evidence_packet.json", "SHA256SUMS",
              "signed_acceptance.json")


def _sha256(path) -> str | None:
    if not path:
        return None
    try:
        return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def _units(sc) -> dict:
    out = {}
    for cat in (sc or {}).get("categories") or []:
        for u in cat.get("units") or []:
            out[(cat.get("category"), u.get("unit"))] = u
    return out


def _cats(sc) -> dict:
    return {c.get("category"): c for c in (sc or {}).get("categories") or []}


def _declared(changes, cat, unit, cur) -> list:
    ids = []
    for g in changes or []:
        for c, u in g.get("units") or []:
            if (c, u) == (cat, unit) or (c == "*" and u == "*"):
                need = g.get("when_current_detail_contains")
                if need and need not in json.dumps((cur or {}).get(
                        "detail"), default=str):
                    continue
                ids.append(g.get("id"))
                break
    return ids


def diff(prior: dict | None, current: dict) -> dict:
    """Every verdict difference between two scorecards of the same
    packet. prior None = the prior evaluator did not produce one."""
    changes = list(current.get("grading_changes") or [])
    reasons = {g.get("id"): g.get("reason") for g in changes}
    if not isinstance(prior, dict):
        return {"verdict": PRIOR_UNAVAILABLE, "differences": [],
                "detail_only_changes": [], "categories": [],
                "undeclared": [], "lifted": [], "lifted_undeclared": []}
    pu, cu = _units(prior), _units(current)
    diffs, detail_only = [], []
    for key in sorted(set(pu) | set(cu), key=lambda k: (str(k[0]),
                                                        str(k[1]))):
        p, c = pu.get(key), cu.get(key)
        if p is None:
            kind = ADDED
        elif c is None:
            kind = REMOVED
        elif bool(p.get("passed")) != bool(c.get("passed")):
            kind = LIFTED if c.get("passed") else LOWERED
        elif p.get("class") != c.get("class"):
            kind = RECLASSIFIED
        else:
            if json.dumps(p.get("detail"), sort_keys=True, default=str) != \
                    json.dumps(c.get("detail"), sort_keys=True, default=str):
                detail_only.append("%s / %s" % key)
            continue
        ids = _declared(changes, key[0], key[1], c)
        diffs.append({
            "category": key[0], "unit": key[1], "change": kind,
            "prior": None if p is None else {
                "passed": bool(p.get("passed")), "class": p.get("class")},
            "current": None if c is None else {
                "passed": bool(c.get("passed")), "class": c.get("class")},
            "declared": ids,
            "reason": "; ".join(reasons.get(i) or i for i in ids)
            if ids else "UNDECLARED"})
    pc, cc = _cats(prior), _cats(current)
    cats = []
    for name in sorted(set(pc) | set(cc), key=str):
        p, c = pc.get(name) or {}, cc.get(name) or {}
        comp = lambda x: [(k.get("component"), k.get("numerator"),  # noqa
                           k.get("denominator")) for k in
                          x.get("components") or []]
        changed = (p.get("readiness") != c.get("readiness") or
                   p.get("passes") != c.get("passes") or comp(p) != comp(c))
        cats.append({"category": name, "changed": changed,
                     "prior": {"readiness": p.get("readiness"),
                               "passes": p.get("passes"),
                               "components": comp(p)},
                     "current": {"readiness": c.get("readiness"),
                                 "passes": c.get("passes"),
                                 "components": comp(c)}})
    undeclared = ["%s / %s" % (d["category"], d["unit"]) for d in diffs
                  if not d["declared"]]
    lifted = ["%s / %s" % (d["category"], d["unit"]) for d in diffs
              if d["change"] == LIFTED or (d["change"] == ADDED and
                                           (d["current"] or {}).get(
                                               "passed"))]
    lifted_und = [x for x in lifted if x in undeclared]
    return {"verdict": NO_DIFFERENCE if not diffs else (
                UNDECLARED if undeclared else DECLARED),
            "units_compared": len(set(pu) | set(cu)),
            "differences": diffs, "detail_only_changes": detail_only,
            "categories": cats, "categories_changed": [
                x["category"] for x in cats if x["changed"]],
            "undeclared": undeclared, "lifted": lifted,
            "lifted_undeclared": lifted_und,
            "passing": {"prior": prior.get("passing"),
                        "current": current.get("passing")}}


def run_prior(prior_source, acc, *, release_sha, frontend_preview=None,
              timeout=600) -> tuple:
    """(prior scorecard or None, error or None): the prior evaluator over
    a byte copy of the packet with the same arguments."""
    acc = pathlib.Path(acc)
    if not prior_source or not pathlib.Path(prior_source).is_file():
        return None, "PRIOR_SOURCE_ABSENT"
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="prior_acc_"))
    try:
        for p in acc.iterdir():
            if p.is_file() and p.name not in NOT_INPUTS:
                shutil.copy2(p, tmp / p.name)
        args = [sys.executable, "-I", str(prior_source), str(tmp)]
        if release_sha:
            args += ["--release-sha", release_sha]
        if frontend_preview:
            fp = pathlib.Path(frontend_preview)
            inside = fp.resolve().parent == acc.resolve()
            args += ["--frontend-preview",
                     str(tmp / fp.name) if inside else str(fp)]
        r = subprocess.run(args, capture_output=True, text=True,
                           timeout=timeout, cwd=str(tmp))
        out = tmp / "scorecard_14.json"
        if r.returncode != 0 or not out.is_file():
            return None, "PRIOR_EVALUATOR_FAILED:rc=%s:%s" % (
                r.returncode, (r.stderr or "")[-400:])
        try:
            return json.loads(out.read_text()), None
        except ValueError:
            return None, "PRIOR_SCORECARD_NOT_JSON"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def build(*, acc, release_sha, prior_source, prior_sha, current_sha=None,
          frontend_preview=None) -> tuple:
    acc = pathlib.Path(acc)
    try:
        current = json.loads((acc / "scorecard_14.json").read_text())
    except (OSError, ValueError):
        current = None
    prior, err = run_prior(prior_source, acc, release_sha=release_sha,
                           frontend_preview=frontend_preview)
    if not isinstance(current, dict):
        rep = {"verdict": "CURRENT_SCORECARD_ABSENT", "differences": []}
    else:
        rep = diff(prior, current)
    pin = (current or {}).get("pinning") or {}
    rep.update({
        "version": VERSION,
        "inputs": "a byte copy of the same packet, the same --release-sha "
                  "and --frontend-preview",
        "release_sha": release_sha,
        "prior": {"sha": prior_sha, "source_sha256": _sha256(prior_source),
                  "version": (prior or {}).get("version"),
                  "passing": (prior or {}).get("passing"), "error": err},
        "current": {"sha": current_sha or (pin.get("evaluator") or {}).get(
                        "sha"),
                    "version": (current or {}).get("version"),
                    "source_sha256": (pin.get("evaluator") or {}).get(
                        "source_sha256"),
                    "passing": (current or {}).get("passing")}})
    return rep, prior


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="mode", required=True)
    r = sub.add_parser("run")
    r.add_argument("--acc", required=True)
    r.add_argument("--release-sha", default="")
    r.add_argument("--prior-source", default="")
    r.add_argument("--prior-sha", default="")
    r.add_argument("--current-sha", default="")
    r.add_argument("--frontend-preview", default="")
    r.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    rep, prior = build(acc=a.acc, release_sha=a.release_sha or None,
                       prior_source=a.prior_source or None,
                       prior_sha=a.prior_sha or None,
                       current_sha=a.current_sha or None,
                       frontend_preview=a.frontend_preview or None)
    if prior is not None:
        (pathlib.Path(a.acc) / "scorecard_14_prior.json").write_text(
            json.dumps(prior, indent=1, default=str))
    pathlib.Path(a.out).write_text(json.dumps(rep, indent=1, sort_keys=True,
                                              default=str))
    print("grading diff (prior %s -> current %s): %s; %d difference(s), "
          "%d undeclared, lifted %s" % (
              (a.prior_sha or "?")[:12], (rep["current"]["sha"] or "?")[:12],
              rep["verdict"], len(rep.get("differences") or []),
              len(rep.get("undeclared") or []),
              ", ".join(rep.get("lifted") or []) or "none"))
    for d in rep.get("differences") or []:
        print("  %-9s %s / %s: %s -> %s  [%s]" % (
            d["change"], d["category"], d["unit"],
            (d["prior"] or {}).get("class"), (d["current"] or {}).get(
                "class"), d["reason"][:160]))
    if rep.get("prior", {}).get("error"):
        print("  prior evaluator: %s" % rep["prior"]["error"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
