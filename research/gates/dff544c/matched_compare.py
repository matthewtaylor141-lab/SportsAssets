"""Structured comparison of a baseline and candidate gate report (read-only).

Usage: matched_compare.py BASE_DIR CAND_DIR CRITICAL_LIST
Prints: run metadata side by side, collection differences, collection/setup
errors, skips, missing tests, failure identities with first error line, and the
outcome of every declared critical test in both runs.
"""
import json
import sys

bd, cd, crit_path = sys.argv[1:4]


def load(d):
    return (json.load(open(d + "/head_report.json")),
            json.load(open(d + "/head_meta.json")))


(B, bm), (C, cm) = load(bd), load(cd)
print("== METADATA (baseline | candidate)")
for k in sorted(set(bm) | set(cm)):
    if k in ("console_path", "report_path", "console_bytes"):
        continue
    mark = "" if bm.get(k) == cm.get(k) else "   <-- differs"
    print("  %-22s %s | %s%s" % (k, bm.get(k), cm.get(k), mark))
for k in ("exitstatus", "collected_count", "executed_count"):
    print("  %-22s %s | %s" % (k, B.get(k), C.get(k)))
print("  counts                 %s | %s" % (B.get("counts"), C.get("counts")))

bcol, ccol = set(B.get("collected") or ()), set(C.get("collected") or ())
print("\n== COLLECTION")
print("  only in candidate: %d   only in baseline: %d" % (len(ccol - bcol), len(bcol - ccol)))
files = sorted({x.split("::")[0] for x in ccol - bcol})
print("  candidate-only files:", files)
if bcol - ccol:
    print("  baseline-only (MISSING in candidate):", sorted(bcol - ccol)[:20])

print("\n== COLLECTION / SETUP ERRORS")
print("  baseline collect_errors:", B.get("collect_errors"))
print("  candidate collect_errors:", C.get("collect_errors"))
bn, cn = B["nodes"], C["nodes"]
for lab, n in (("baseline", bn), ("candidate", cn)):
    se = [k for k, v in n.items() if (v.get("phases") or {}).get("setup") == "failed"]
    print("  %s setup-phase failures: %d %s" % (lab, len(se), se[:5]))

print("\n== SKIPS")
bs = {k for k, v in bn.items() if v.get("outcome") == "skipped"}
cs = {k for k, v in cn.items() if v.get("outcome") == "skipped"}
print("  baseline %d, candidate %d, candidate-only %s" % (len(bs), len(cs), sorted(cs - bs)[:10]))


def reason(v):
    lr = v.get("longrepr") or ""
    e = [l for l in lr.splitlines() if l.startswith("E ")]
    return (e[0] if e else lr.splitlines()[-1] if lr else "")[:160]


bf = {k for k, v in bn.items() if v.get("outcome") == "failed"}
cf = {k for k, v in cn.items() if v.get("outcome") == "failed"}
print("\n== FAILURES  baseline %d, candidate %d, both %d" % (len(bf), len(cf), len(bf & cf)))
print("  candidate-only failures (%d):" % len(cf - bf))
for k in sorted(cf - bf):
    print("    %s\n        %s" % (k, reason(cn[k])))
print("  baseline-only failures (%d):" % len(bf - cf))
for k in sorted(bf - cf):
    print("    %s\n        %s" % (k, reason(bn[k])))
same_reason = sum(1 for k in bf & cf if reason(bn[k]) == reason(cn[k]))
print("  shared failures with identical first error line: %d of %d" % (same_reason, len(bf & cf)))

print("\n== CRITICAL TESTS")
crit = [l.strip() for l in open(crit_path) if l.strip() and not l.startswith("#")]


def crit_outcomes(n, col):
    out = {}
    for c in crit:
        ids = [k for k in col if k == c or k.startswith(c + "::")]
        out[c] = sorted({n.get(k, {}).get("outcome", "NOT_RUN") for k in ids}) or ["NOT_COLLECTED"]
    return out


bo, co = crit_outcomes(bn, bcol), crit_outcomes(cn, ccol)
bad = [c for c in crit if co[c] != ["passed"]]
print("  declared critical entries: %d; candidate entries not all-passed: %d" % (len(crit), len(bad)))
for c in bad:
    print("    %s  candidate=%s baseline=%s" % (c, co[c], bo[c]))
