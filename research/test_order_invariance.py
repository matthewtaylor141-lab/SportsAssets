#!/usr/bin/env python3
"""Brute-force verification of the tied-block order-invariance classification.

Run:  python3 research/test_order_invariance.py

WHY THIS EXISTS. I proposed that a tied block whose reachable interval
[L, U] = [D0 - Ntot, D0 + Ytot] spans zero is order-sensitive. THAT IS WRONG,
and the owner's counterexample is decisive: if D0 > 0 and D1 < 0 then EVERY
ordering must cross from positive to negative, so the block spans zero and
FLIPPED is invariantly TRUE. Spanning zero says a crossing is REACHABLE, not
that it is OPTIONAL.

So the rule below is derived properly and then checked against exhaustive
enumeration of every permutation on small blocks, rather than reasoned about
and trusted.

THE FLIP DEFINITION, corrected first, because the block proof must be derived
against the same definition it will be used with.

  A flip is a change in the LAST NONZERO sign of the directional difference
  D = qY - qN. Touching zero is NOT a flip:
      + -> 0 -> -   flip
      - -> 0 -> +   flip
      + -> 0 -> +   no flip
      - -> 0 -> -   no flip
  The previous classifier compared adjacent signs and required both nonzero,
  so a path through exact flat was missed and landed in REDUCED_NOT_FLIPPED.

  Equivalently, and this is the form the proof uses:
      FLIPPED  <=>  the path visits some D > 0  AND  some D < 0.
  Visiting both nonzero signs is exactly what makes the compressed
  nonzero-sign sequence contain a change.

THE CLASSIFICATION. Block boundary states are FIXED regardless of within-block
ordering, because a block's total contribution to each leg is fixed. So:

  MaxGuaranteed / MinGuaranteed  = max / min over the FIXED boundary states,
                                   visited under every ordering
  MaxPossible / MinPossible      = max / min over every reachable state,
                                   i.e. including each block's U and L

  PROVABLY_FLIPPED   MaxGuaranteed > 0 AND MinGuaranteed < 0
  PROVABLY_NO_FLIP   NOT (MaxPossible > 0 AND MinPossible < 0)
  ORDER_DEPENDENT    otherwise

A block with a boundary state exactly at zero is reported separately as
ZERO_BOUNDARY_UNRESOLVED rather than forced into one of the three.

NAMING, per instruction: the set that is not proven invariant is
ORDER_NOT_PROVEN_INVARIANT. It is NOT called PROVEN_ORDER_SENSITIVE, because
failing to prove invariance is not the same as proving sensitivity.
"""
import itertools
import sys

FAILURES = []


def flipped(path):
    """FLIPPED <=> the path visits both a strictly positive and a strictly
    negative D. Verified below to agree with last-nonzero-sign carry."""
    return any(d > 1e-12 for d in path) and any(d < -1e-12 for d in path)


def flipped_by_sign_carry(path):
    """The definition stated in words: a change in the last nonzero sign."""
    last = 0
    for d in path:
        s = 0 if abs(d) < 1e-12 else (1 if d > 0 else -1)
        if s == 0:
            continue
        if last != 0 and s != last:
            return True
        last = s
    return False


def walk(d0, fills):
    """States visited after each fill, starting from (but not repeating) d0."""
    d, out = d0, [d0]
    for leg, q in fills:
        d += q if leg == 0 else -q
        out.append(d)
    return out


def classify(d0, ys, ns):
    """Classify one tied block without enumerating orderings."""
    d1 = d0 + sum(ys) - sum(ns)
    U, L = d0 + sum(ys), d0 - sum(ns)
    # boundary states are fixed under every ordering
    max_guar, min_guar = max(d0, d1), min(d0, d1)
    max_poss, min_poss = U, L
    if abs(d0) < 1e-12 or abs(d1) < 1e-12:
        return "ZERO_BOUNDARY_UNRESOLVED"
    if max_guar > 1e-12 and min_guar < -1e-12:
        return "PROVABLY_FLIPPED"
    if not (max_poss > 1e-12 and min_poss < -1e-12):
        return "PROVABLY_NO_FLIP"
    return "ORDER_DEPENDENT"


def brute(d0, ys, ns):
    """Ground truth: enumerate every ordering of the block's fills."""
    fills = [(0, q) for q in ys] + [(1, q) for q in ns]
    outcomes = {flipped(walk(d0, list(p))) for p in itertools.permutations(fills)}
    if outcomes == {True}:
        return "PROVABLY_FLIPPED"
    if outcomes == {False}:
        return "PROVABLY_NO_FLIP"
    return "ORDER_DEPENDENT"


# ---- 1. the two flip definitions must agree -------------------------------
print("1. flip definition: visits-both-signs == last-nonzero-sign-carry")
paths = [[0, 5, 0, -3], [0, 5, 0, 5], [0, -4, 0, -4], [0, -4, 0, 2],
         [0, 3, 7, 2], [0, 0, 0], [0, 2, -1, 3], [0, -1], [0, 1]]
bad = [p for p in paths if flipped(p) != flipped_by_sign_carry(p)]
print(f"   {'PASS' if not bad else 'FAIL'}  {len(paths)} paths, {len(bad)} disagreements")
if bad:
    FAILURES.append("flip definitions disagree")
    for p in bad:
        print(f"      {p}: visits={flipped(p)} carry={flipped_by_sign_carry(p)}")

# the owner's stated cases, checked explicitly
for path, want, label in (([0, 5, 0, -3], True, "+ -> 0 -> -  is a flip"),
                          ([0, -5, 0, 3], True, "- -> 0 -> +  is a flip"),
                          ([0, 5, 0, 3], False, "+ -> 0 -> +  is not"),
                          ([0, -5, 0, -3], False, "- -> 0 -> -  is not")):
    ok = flipped_by_sign_carry(path) == want
    print(f"   {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        FAILURES.append(label)

# ---- 2. the classification must match exhaustive enumeration --------------
print()
print("2. block classification vs exhaustive permutation enumeration")
QS = [1.0, 2.0, 5.0]
cases = agree = zero_b = 0
mismatch = []
for d0 in (-6.0, -3.0, -1.0, 0.0, 1.0, 3.0, 6.0):
    for ny in range(0, 3):
        for nn in range(0, 3):
            if ny + nn == 0 or ny + nn > 4:
                continue
            for ys in itertools.product(QS, repeat=ny):
                for ns in itertools.product(QS, repeat=nn):
                    cases += 1
                    got = classify(d0, list(ys), list(ns))
                    if got == "ZERO_BOUNDARY_UNRESOLVED":
                        zero_b += 1
                        continue
                    truth = brute(d0, list(ys), list(ns))
                    if got == truth:
                        agree += 1
                    else:
                        mismatch.append((d0, ys, ns, got, truth))
print(f"   {cases} blocks enumerated, {zero_b} zero-boundary (excluded from the "
      f"comparison by design)")
print(f"   {'PASS' if not mismatch else 'FAIL'}  {agree} agree, {len(mismatch)} mismatch")
for m in mismatch[:8]:
    print(f"      d0={m[0]} ys={m[1]} ns={m[2]} classified={m[3]} truth={m[4]}")
if mismatch:
    FAILURES.append("classification disagrees with enumeration")

# ---- 3. the owner's counterexample must NOT be called order-dependent -----
print()
print("3. the counterexample: D0 > 0, D1 < 0 spans zero but is invariantly FLIPPED")
got = classify(3.0, [1.0], [5.0])          # d1 = -1, L = -2, U = 4 -> spans zero
truth = brute(3.0, [1.0], [5.0])
ok = got == "PROVABLY_FLIPPED" == truth
print(f"   {'PASS' if ok else 'FAIL'}  classified={got} truth={truth}")
if not ok:
    FAILURES.append("counterexample misclassified")
print("   (the naive rule 'spans zero => order-sensitive' would have called this "
      "ORDER_DEPENDENT)")

# ---- 4. condition level: several blocks, boundaries fixed between them ----
#
# A subtlety that the single-block test does NOT cover, and that I got wrong
# first: U and L of the SAME block are not jointly achievable -- one ordering
# cannot both lead with Y and lead with N. Extremes from DIFFERENT blocks are
# jointly achievable, because each block's ordering is chosen independently and
# the boundary states between blocks are fixed.
#
# So the condition-level rule is stated on reachability of each SIGN, and where
# both signs are reachable only through a single block it is deliberately
# CONSERVATIVE: it reports ORDER_NOT_PROVEN_INVARIANT rather than claiming
# invariance. That is why the set carries that name and not
# PROVEN_ORDER_SENSITIVE -- failing to prove invariance is not proving
# sensitivity, and the approximation runs in the honest direction.
print()
print("4. condition level: multi-block, vs exhaustive enumeration")


def classify_condition(blocks):
    """blocks: list of (ys, ns). Boundary states are derived, not given."""
    d, bounds, us, ls = 0.0, [0.0], [], []
    for ys, ns in blocks:
        us.append(d + sum(ys))
        ls.append(d - sum(ns))
        d += sum(ys) - sum(ns)
        bounds.append(d)
    gpos = any(b > 1e-12 for b in bounds)
    gneg = any(b < -1e-12 for b in bounds)
    ppos = gpos or any(u > 1e-12 for u in us)
    pneg = gneg or any(l < -1e-12 for l in ls)
    if gpos and gneg:
        return "PROVABLY_FLIPPED"
    if not (ppos and pneg):
        return "PROVABLY_NO_FLIP"
    return "ORDER_NOT_PROVEN_INVARIANT"


def brute_condition(blocks):
    outs = set()
    perms = []
    for ys, ns in blocks:
        fills = [(0, q) for q in ys] + [(1, q) for q in ns]
        perms.append(list(itertools.permutations(fills)))
    for combo in itertools.product(*perms):
        d, path = 0.0, [0.0]
        for block in combo:
            for leg, q in block:
                d += q if leg == 0 else -q
                path.append(d)
        outs.add(flipped(path))
    if outs == {True}:
        return "PROVABLY_FLIPPED"
    if outs == {False}:
        return "PROVABLY_NO_FLIP"
    return "ORDER_DEPENDENT"


QS2 = [1.0, 3.0]
cases = exact = conservative = wrong = 0
examples = []
for nblocks in (1, 2):
    shapes = [(ny, nn) for ny in range(0, 3) for nn in range(0, 3)
              if 0 < ny + nn <= 3]
    for combo in itertools.product(shapes, repeat=nblocks):
        for qsel in itertools.product(QS2, repeat=sum(a + b for a, b in combo)):
            i, blocks = 0, []
            for ny, nn in combo:
                blocks.append((list(qsel[i:i + ny]), list(qsel[i + ny:i + ny + nn])))
                i += ny + nn
            cases += 1
            got, truth = classify_condition(blocks), brute_condition(blocks)
            if got == truth or (got == "ORDER_NOT_PROVEN_INVARIANT"
                                and truth == "ORDER_DEPENDENT"):
                exact += 1
            elif got == "ORDER_NOT_PROVEN_INVARIANT":
                conservative += 1          # safe: did not claim invariance
                if len(examples) < 3:
                    examples.append((blocks, truth))
            else:
                wrong += 1                 # UNSAFE: claimed a proof that is false
                if len(examples) < 3:
                    examples.append((blocks, got, truth))
print(f"   {cases} conditions enumerated")
print(f"   exact {exact} | conservative (said NOT PROVEN, truth was provable) "
      f"{conservative} | UNSAFE {wrong}")
print(f"   {'PASS' if wrong == 0 else 'FAIL'}  no condition is claimed provable "
      f"when enumeration disagrees")
if wrong:
    FAILURES.append("condition-level rule claims a false proof")
for e in examples[:3]:
    print(f"      e.g. {e}")

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {', '.join(FAILURES)}")
    sys.exit(1)
print("all order-invariance checks pass (condition level included)")
