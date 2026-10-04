"""THE CAPITAL-CRITICAL CI VERDICT: ONE EXACT SHA, NO UNEXPECTED FAILURE.

Owner, program section 21: "Unexpected failures = REJECT". The release gate
(`gate_verdict.py`) compares a run with a matched BASELINE and accepts the
failures the baseline already had. That is the right instrument for "did
this candidate make anything worse", and the wrong one for "may real capital
run on this SHA": a baseline of 91 red tests -- some of them on the retry,
429, applicability and policy-sha paths -- was being accepted run after run
because it was the baseline. This verdict has no baseline. A run is
ACCEPTED only when every failure and error it contains is named, in advance,
in a machine-readable quarantine that expires; and the capital-critical tests
may never be quarantined at all.

INPUTS (stdlib only -- the verdict must not depend on the environment it
judges):

  --junit       pytest's junit XML for the WHOLE suite (`-o junit_family=
                xunit1` so each testcase carries its file; without it the
                file is resolved against --root)
  --quarantine  backend/tests/quarantine.json: a JSON LIST of
                {test, owner, reason, date, expiry, issue}
                  test    a pytest node id (tests/....py::name[...])
                  owner   who answers for it
                  reason  why it fails and why it cannot be fixed now
                  date    ISO date it was quarantined (not in the future)
                  expiry  ISO date, >= date and at most 14 days after it
                  issue   https://github.com/<owner>/<repo>/issues/<n>
  --critical    backend/tools/capital_critical_tests.txt (files or node ids)
  --sha         the exact commit the run tested
  --out         where the verdict JSON is written (also printed)

REJECT (exit 1) WHEN ANY OF:
  * a failure or error is not quarantined                    (unexpected)
  * a quarantine entry is malformed                          (malformed)
  * a quarantine entry is past its expiry                    (expired)
  * a quarantined test PASSED, was skipped, or did not run   (stale: the
    list must shrink, never silently carry dead entries)
  * a capital-critical test failed, errored, was skipped or xfailed, a
    critical file collected nothing, or any quarantine entry names a
    capital-critical test                                    (critical)
  * nothing was collected, or the report is missing / unreadable
ACCEPT (exit 0) otherwise. Exit 2 is a usage error (also not an accept).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

SCHEMA = "CI_VERDICT_V1"
ACCEPT = "ACCEPT"
REJECT = "REJECT"

#: The longest a quarantine entry may live. Owner: "expiry".
MAX_QUARANTINE_DAYS = 14

QUARANTINE_FIELDS = ("test", "owner", "reason", "date", "expiry", "issue")
#: A reason has to say something: an empty or one-word reason is how a
#: quarantine list turns into a place failures go to be forgotten.
MIN_REASON_CHARS = 20
ISSUE_URL = re.compile(r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/issues/[0-9]+$")
NODE_ID = re.compile(r"^[A-Za-z0-9_./-]+\.py(::[^:\s][^\n]*)?$")

PASSED, FAILED, ERROR, SKIPPED, XFAILED = (
    "passed", "failed", "error", "skipped", "xfailed")
#: worst first: a testcase with a failure AND a teardown error is an error
_RANK = {ERROR: 4, FAILED: 3, XFAILED: 2, SKIPPED: 1, PASSED: 0}


# ── node ids ────────────────────────────────────────────────────────

def split_node_id(node_id: str) -> tuple[str, list[str], str]:
    """'tests/t.py::C::test_x[a::b]' -> ('tests/t.py', ['C'], 'test_x[a::b]').

    The parameter part is never split: an id may carry '::' inside its
    brackets."""
    head, bracket, params = node_id.partition("[")
    parts = head.split("::")
    path = parts[0]
    rest = parts[1:]
    if not rest:
        return path, [], ""
    name = rest[-1] + (bracket + params if bracket else "")
    return path, rest[:-1], name


def _norm_path(path: str) -> str:
    path = path.replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    if path.startswith("backend/"):
        path = path[len("backend/"):]
    return path


def _module_of(path: str) -> str:
    return _norm_path(path)[:-3].replace("/", ".") if path.endswith(".py") else path


def testcase_node_id(case: ET.Element, root: str | None) -> tuple[str, str]:
    """(node id, file) for one junit <testcase>."""
    classname = case.get("classname") or ""
    name = case.get("name") or ""
    fpath = case.get("file")
    if not classname:
        # pytest writes a collection error as a testcase with no classname
        # and the module (dotted) as its name
        guess = name.replace(".", "/") + ".py" if name and "/" not in name else name
        return _norm_path(guess), _norm_path(guess)
    parts = classname.split(".")
    if fpath:
        fpath = _norm_path(fpath)
        mod = _module_of(fpath).split(".")
        classes = []
        for i in range(len(parts) - len(mod) + 1):
            if parts[i:i + len(mod)] == mod:
                classes = parts[i + len(mod):]
                break
    elif root is not None:
        # the longest dotted prefix that is a file under the root
        fpath, classes = "/".join(parts) + ".py", []
        for k in range(len(parts), 0, -1):
            cand = "/".join(parts[:k]) + ".py"
            if os.path.isfile(os.path.join(root, cand)):
                fpath, classes = cand, parts[k:]
                break
    else:
        # no file attribute and no root: a class name starts upper-case
        k = next((i for i, p in enumerate(parts) if p[:1].isupper()), len(parts))
        fpath, classes = "/".join(parts[:k]) + ".py", parts[k:]
    return "::".join([fpath] + classes + [name]), fpath


# ── the junit report ────────────────────────────────────────────────

def outcome_of(case: ET.Element) -> str:
    worst = PASSED
    for child in case:
        tag = child.tag
        if tag == "error":
            got = ERROR
        elif tag == "failure":
            got = FAILED
        elif tag == "skipped":
            got = XFAILED if (child.get("type") or "") == "pytest.xfail" else SKIPPED
        else:
            continue
        if _RANK[got] > _RANK[worst]:
            worst = got
    return worst


def read_junit(path: str, root: str | None = None) -> dict:
    """node id -> {"outcome", "file"}. Raises ValueError when unreadable."""
    try:
        tree = ET.parse(path)
    except (OSError, ET.ParseError) as exc:
        raise ValueError("the junit report is missing or unreadable: %s" % exc)
    out: dict = {}
    for case in tree.getroot().iter("testcase"):
        nid, fpath = testcase_node_id(case, root)
        got = outcome_of(case)
        prev = out.get(nid)
        if prev is None or _RANK[got] > _RANK[prev["outcome"]]:
            out[nid] = {"outcome": got, "file": fpath}
    return out


# ── the quarantine ──────────────────────────────────────────────────

def _iso_date(value) -> dt.date | None:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def validate_entry(entry, today: dt.date) -> list[str]:
    """Every reason this entry is malformed. Empty means well-formed."""
    if not isinstance(entry, dict):
        return ["the entry is not an object"]
    bad = []
    missing = [f for f in QUARANTINE_FIELDS if f not in entry]
    if missing:
        bad.append("missing field(s) %s" % ", ".join(missing))
    extra = sorted(set(entry) - set(QUARANTINE_FIELDS))
    if extra:
        bad.append("unknown field(s) %s" % ", ".join(extra))
    test = entry.get("test")
    if not isinstance(test, str) or not NODE_ID.match(test) or "::" not in test:
        bad.append("test is not a pytest node id: %r" % (test,))
    owner = entry.get("owner")
    if not isinstance(owner, str) or not owner.strip():
        bad.append("owner is empty")
    reason = entry.get("reason")
    if not isinstance(reason, str) or len(reason.strip()) < MIN_REASON_CHARS:
        bad.append("reason must say why (>= %d characters)" % MIN_REASON_CHARS)
    issue = entry.get("issue")
    if not isinstance(issue, str) or not ISSUE_URL.match(issue):
        bad.append("issue is not a GitHub issue URL: %r" % (issue,))
    date = _iso_date(entry.get("date"))
    expiry = _iso_date(entry.get("expiry"))
    if date is None:
        bad.append("date is not an ISO date: %r" % (entry.get("date"),))
    if expiry is None:
        bad.append("expiry is not an ISO date: %r" % (entry.get("expiry"),))
    if date is not None and date > today:
        bad.append("date %s is in the future" % date)
    if date is not None and expiry is not None:
        if expiry < date:
            bad.append("expiry %s is before date %s" % (expiry, date))
        elif (expiry - date).days > MAX_QUARANTINE_DAYS:
            bad.append("expiry %s is more than %d days after date %s"
                       % (expiry, MAX_QUARANTINE_DAYS, date))
    return bad


def read_quarantine(path: str, today: dt.date) -> tuple[list, list, list]:
    """(valid entries, malformed [{entry, problems}], expired entries)."""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        return [], [{"entry": None, "problems": ["the quarantine file does not exist: %s" % path]}], []
    except json.JSONDecodeError as exc:
        return [], [{"entry": None, "problems": ["the quarantine file is not JSON: %s" % exc]}], []
    if not isinstance(doc, list):
        return [], [{"entry": None, "problems": ["the quarantine file must be a JSON list"]}], []
    valid, malformed, expired, seen = [], [], [], set()
    for entry in doc:
        problems = validate_entry(entry, today)
        if not problems and entry["test"] in seen:
            problems = ["duplicate entry for %s" % entry["test"]]
        if problems:
            malformed.append({"entry": entry, "problems": problems})
            continue
        seen.add(entry["test"])
        if _iso_date(entry["expiry"]) < today:
            expired.append(entry)
        else:
            valid.append(entry)
    return valid, malformed, expired


# ── the capital-critical list ───────────────────────────────────────

def read_critical(path: str) -> list[str]:
    with open(path, encoding="utf-8") as fh:
        lines = [ln.split("#", 1)[0].strip() for ln in fh]
    out = []
    for ln in lines:
        if ln and ln not in out:
            out.append(_norm_path(ln))
    return out


def _critical_match(nid: str, file: str, critical: list[str]) -> str | None:
    for c in critical:
        if "::" in c:
            if nid == c or nid.startswith(c + "::") or nid.startswith(c + "["):
                return c
        elif file == c:
            return c
    return None


# ── the verdict ─────────────────────────────────────────────────────

def evaluate(results: dict, quarantine: tuple[list, list, list],
             critical: list[str], sha: str) -> dict:
    valid, malformed, expired = quarantine
    counts = {k: 0 for k in (PASSED, FAILED, ERROR, SKIPPED, XFAILED)}
    for r in results.values():
        counts[r["outcome"]] += 1
    counts["tests"] = len(results)
    reasons = []
    if not results:
        reasons.append("NOTHING_COLLECTED: the report holds zero tests")

    q_by_test = {e["test"]: e for e in valid}
    # entries for tests that are not failing in this run cannot be honoured
    stale, quarantined = [], []
    for test, e in q_by_test.items():
        r = results.get(test)
        if r is None:
            stale.append({"test": test, "why": "MATCHES_NO_TEST_IN_THIS_RUN"})
        elif r["outcome"] in (FAILED, ERROR):
            quarantined.append({"test": test, "outcome": r["outcome"],
                                "owner": e["owner"], "expiry": e["expiry"],
                                "issue": e["issue"]})
        else:
            stale.append({"test": test, "why": "NOW_%s" % r["outcome"].upper()})

    unexpected = sorted(n for n, r in results.items()
                        if r["outcome"] in (FAILED, ERROR) and n not in q_by_test)

    # the capital-critical tests: every one RUN and PASSED, none quarantined
    crit_violations, crit_tests = [], 0
    crit_seen = {c: 0 for c in critical}
    for nid, r in sorted(results.items()):
        c = _critical_match(nid, r["file"], critical)
        if c is None:
            continue
        crit_seen[c] += 1
        crit_tests += 1
        if r["outcome"] != PASSED:
            crit_violations.append({"test": nid, "outcome": r["outcome"],
                                    "why": "A_CAPITAL_CRITICAL_TEST_MUST_PASS"})
    all_entries = valid + expired + [m["entry"] for m in malformed
                                     if isinstance(m["entry"], dict)]
    for e in all_entries:
        t = e.get("test")
        if not isinstance(t, str):
            continue
        path, _cls, _name = split_node_id(t)
        if _critical_match(t, _norm_path(path), critical):
            crit_violations.append({"test": t, "outcome": "quarantined",
                                    "why": "A_CAPITAL_CRITICAL_TEST_CAN_NEVER_BE_QUARANTINED"})
    empty = sorted(c for c, n in crit_seen.items() if n == 0)
    for c in empty:
        crit_violations.append({"test": c, "outcome": "not_collected",
                                "why": "A_CAPITAL_CRITICAL_FILE_COLLECTED_NOTHING"})

    if unexpected:
        reasons.append("UNEXPECTED: %d failure(s)/error(s) not quarantined" % len(unexpected))
    if malformed:
        reasons.append("MALFORMED_QUARANTINE: %d entr(y/ies)" % len(malformed))
    if expired:
        reasons.append("EXPIRED_QUARANTINE: %d entr(y/ies)" % len(expired))
    if stale:
        reasons.append("STALE_QUARANTINE: %d entr(y/ies) no longer failing" % len(stale))
    if crit_violations:
        reasons.append("CRITICAL: %d capital-critical violation(s)" % len(crit_violations))

    return {
        "schema": SCHEMA,
        "verdict": REJECT if reasons else ACCEPT,
        "sha": sha,
        "counts": counts,
        "unexpected": unexpected,
        "quarantined": quarantined,
        "expired": [{"test": e["test"], "expiry": e["expiry"], "issue": e["issue"]} for e in expired],
        "malformed": malformed,
        "stale": stale,
        "critical": {"files": len(critical), "tests": crit_tests,
                     "violations": crit_violations, "empty_files": empty},
        "reasons": reasons,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--junit", required=True)
    ap.add_argument("--quarantine", required=True)
    ap.add_argument("--critical", required=True)
    ap.add_argument("--sha", required=True)
    ap.add_argument("--out")
    ap.add_argument("--root", help="the directory node ids are relative to")
    ap.add_argument("--today", help="ISO date (tests); default: today UTC")
    try:
        args = ap.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    today = (_iso_date(args.today) if args.today
             else dt.datetime.now(dt.timezone.utc).date())
    if today is None:
        print("usage: --today must be an ISO date", file=sys.stderr)
        return 2
    try:
        results = read_junit(args.junit, args.root)
    except ValueError as exc:
        verdict = {"schema": SCHEMA, "verdict": REJECT, "sha": args.sha,
                   "counts": {"tests": 0}, "unexpected": [], "quarantined": [],
                   "expired": [], "malformed": [], "stale": [],
                   "critical": {"files": 0, "tests": 0, "violations": [], "empty_files": []},
                   "reasons": ["INVALID_REPORT: %s" % exc]}
    else:
        try:
            critical = read_critical(args.critical)
        except OSError as exc:
            print("usage: the critical list is unreadable: %s" % exc, file=sys.stderr)
            return 2
        verdict = evaluate(results, read_quarantine(args.quarantine, today),
                           critical, args.sha)
    text = json.dumps(verdict, indent=2, sort_keys=True)
    print("CI_VERDICT %s sha=%s tests=%s unexpected=%d quarantined=%d expired=%d "
          "stale=%d malformed=%d critical_violations=%d"
          % (verdict["verdict"], verdict["sha"], verdict["counts"].get("tests"),
             len(verdict["unexpected"]), len(verdict["quarantined"]),
             len(verdict["expired"]), len(verdict["stale"]),
             len(verdict["malformed"]), len(verdict["critical"]["violations"])))
    print(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    return 0 if verdict["verdict"] == ACCEPT else 1


if __name__ == "__main__":
    sys.exit(main())
