#!/usr/bin/env python3
"""Regression fixtures for the two missing-leg defect families.

Run:  python3 research/test_null_semantics.py
Exit 0 if every fixture passes.

WHY TWO FAMILIES. Both defects came from the same root -- a leg that is absent
produces NULL rather than zero -- but they do damage by different mechanisms,
and a guard for one does not catch the other.

  FAMILY 1  LEAST/GREATEST SWALLOW NULL. LEAST(NULL, x) = x, so a single-leg
            condition yields the other leg's full quantity where zero is meant.
            A wrong number is produced. Caught statically by check_sql.py.

  FAMILY 2  SUM() SKIPS NULL ROWS. A matched term that is NULL makes the whole
            arithmetic expression NULL, so sum() drops that row from BOTH a
            numerator and a denominator -- silently changing the population
            rather than the value. NO number looks wrong; the row is simply
            gone. This CANNOT be caught by reading a single expression, because
            the defect is the interaction between NULL propagation and the
            aggregate. It needs evaluation, which is what this file provides.

WHY A MODEL RATHER THAN A DATABASE. There is no local PostgreSQL here, so the
two semantics are modelled explicitly below. The model is not guesswork: both
behaviours were confirmed against production output. LEAST-ignores-NULL was
measured in run 69 statement 3 (the old reference read 56,805,911.793 against
the correct 47,634,973.280, a gap of exactly the single-leg quantity), and
sum()-skips-NULL is documented SQL aggregate behaviour that the same run
depended on. If this model is ever suspected, re-derive it on the real surface
rather than trusting the file.
"""
import sys

NULL = None


def sql_least(*args):
    """PostgreSQL LEAST: NULLs ignored; NULL only if every argument is NULL."""
    vals = [a for a in args if a is not NULL]
    return min(vals) if vals else NULL


def sql_sum(rows):
    """PostgreSQL sum(): NULL rows are skipped; NULL if every row is NULL."""
    vals = [r for r in rows if r is not NULL]
    return sum(vals) if vals else NULL


def mul(a, b):
    return NULL if a is NULL or b is NULL else a * b


def sub(a, b):
    return NULL if a is NULL or b is NULL else a - b


def leg_agg(fills, idx, field):
    """max(CASE WHEN outcome_index = idx THEN ... END) -- NULL if no such leg."""
    vals = [f[field] for f in fills if f["oi"] == idx]
    return sum(vals) if vals else NULL


def condition(fills, payout_yes):
    """Build one condition the way the published SQL did, and the corrected way."""
    qy_raw, qn_raw = leg_agg(fills, 0, "q"), leg_agg(fills, 1, "q")
    cy_raw, cn_raw = leg_agg(fills, 0, "c"), leg_agg(fills, 1, "c")
    qy = 0 if qy_raw is NULL else qy_raw
    qn = 0 if qn_raw is NULL else qn_raw
    cy = 0 if cy_raw is NULL else cy_raw
    cn = 0 if cn_raw is NULL else cn_raw

    acq = cy + cn
    pair_cost = (cy / qy + cn / qn) if (qy > 0 and qn > 0) else NULL  # CASE guard

    mq_old = sql_least(qy_raw, qn_raw)      # <- FAMILY 1 defect
    mq_new = sql_least(qy, qn)              # <- corrected, zero-safe

    py, pn = payout_yes, 1 - payout_yes
    trading = py * qy + pn * qn - acq

    matched_old = mul(mq_old, NULL if pair_cost is NULL else 1 - pair_cost)
    matched_new = 0 if pair_cost is NULL else mq_new * (1 - pair_cost)

    return {
        "qy": qy, "qn": qn, "acq": acq, "trading": trading,
        "mq_old": mq_old, "mq_new": mq_new,
        "matched_old": matched_old, "matched_new": matched_new,
        "remainder_old": sub(trading, matched_old),   # <- FAMILY 2 defect
        "remainder_new": trading - matched_new,
        "single_leg": qy == 0 or qn == 0,
    }


TWO_LEG = [{"oi": 0, "q": 100.0, "c": 48.0}, {"oi": 1, "q": 60.0, "c": 30.0}]
YES_ONLY = [{"oi": 0, "q": 80.0, "c": 40.0}]
NO_ONLY = [{"oi": 1, "q": 70.0, "c": 21.0}]

FAILURES = []


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" -- {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


print("FIXTURE 1 -- family 1: LEAST/GREATEST swallow NULL")
for label, fills in (("YES-only", YES_ONLY), ("NO-only", NO_ONLY)):
    c = condition(fills, 1.0)
    present = c["qy"] if c["qn"] == 0 else c["qn"]
    check(f"{label}: the DEFECT is reproduced (mq_old returns the present leg)",
          c["mq_old"] == present, f"mq_old={c['mq_old']} present_leg={present}")
    check(f"{label}: CORRECTED mq is 0", c["mq_new"] == 0, f"mq_new={c['mq_new']}")
c = condition(TWO_LEG, 1.0)
check("two-leg: both forms agree", c["mq_old"] == c["mq_new"] == 60.0,
      f"old={c['mq_old']} new={c['mq_new']}")

print()
print("FIXTURE 2 -- family 2: a NULL matched term makes sum() drop a valid row")
print("  requiring  total = matched + remainder  for each leg shape")
for label, fills, payout in (("two-leg ", TWO_LEG, 1.0),
                             ("YES-only", YES_ONLY, 1.0),
                             ("NO-only ", NO_ONLY, 0.0)):
    c = condition(fills, payout)
    ok = abs(c["trading"] - (c["matched_new"] + c["remainder_new"])) < 1e-9
    check(f"{label}: CORRECTED closes (total = matched + remainder)", ok,
          f"total={c['trading']:.4f} matched={c['matched_new']:.4f} "
          f"remainder={c['remainder_new']:.4f}")
    if c["single_leg"]:
        check(f"{label}: M = 0", c["mq_new"] == 0)
        check(f"{label}: MATCHED_GROSS_PNL = 0", c["matched_new"] == 0)
        check(f"{label}: REMAINDER = TOTAL",
              abs(c["remainder_new"] - c["trading"]) < 1e-9)

print()
print("  and the OLD form must be shown to LOSE those rows from an aggregate")
conds = [condition(TWO_LEG, 1.0), condition(YES_ONLY, 1.0), condition(NO_ONLY, 0.0)]
tot = sql_sum([c["trading"] for c in conds])
rem_old = sql_sum([c["remainder_old"] for c in conds])
rem_new = sql_sum([c["remainder_new"] for c in conds])
den_old = sql_sum([sub(c["acq"], mul(c["mq_old"], NULL)) for c in conds])
dropped = [c for c in conds if c["remainder_old"] is NULL]
check("the OLD remainder silently drops every single-leg row",
      len(dropped) == 2, f"dropped={len(dropped)} of {len(conds)}")
check("the OLD denominator drops the same rows (population mismatch, not a "
      "value error)", den_old is NULL or len(dropped) == 2)
check("OLD and CORRECTED aggregate remainders differ",
      abs((rem_old or 0) - rem_new) > 1e-9,
      f"old={rem_old} new={rem_new}")
check("only the CORRECTED aggregate closes against total",
      abs(tot - (sql_sum([c["matched_new"] for c in conds]) + rem_new)) < 1e-9,
      f"total={tot:.4f}")

print()
if FAILURES:
    print(f"{len(FAILURES)} FIXTURE(S) FAILED: {', '.join(FAILURES)}")
    sys.exit(1)
print("all fixtures pass")
