#!/usr/bin/env python3
"""THE EVIDENCE-TIER PRECONDITION GATE -- the code-side stages, and the registry.

THE RULE, PERMANENT AND NOT TO BE COLLAPSED:

    COLUMN EXISTS
      -> PRODUCTION WRITE SITE EXISTS
        -> HISTORICAL POPULATION EXISTS
          -> SEMANTIC CONTENT VERIFIED
            -> ELIGIBLE FOR ATTRIBUTION

Five stages, each a separate question, each able to fail on its own. Collapsing
any two of them is how a column that merely EXISTS becomes a primary tier.
Stages 1-2 and the semantic REGISTRY live here; stages 3-5 are measured by
statement 0 of research/rn1_causal_ledger.sql, which is generated from the
registry below so the two cannot drift.

Run:  python3 research/evidence_tiers.py
Exit 0 if every field used as causal linkage has at least one write site.

WHY THIS EXISTS. Run 76 built its primary attribution tier on
mirror_orders.trigger_trade_id, because migration 049 creates the column and
documents exactly what it means. The column has NEVER BEEN WRITTEN: there is no
write site anywhere in the worker or app code. So TIER_1_DIRECT could not match
a single row, and "zero directly linked liquidations" was not a measurement --
it was a filter that cannot fire, reported as a finding. That is the same
vacuity failure this work has now hit five times.

A SCHEMA COLUMN IS NOT EVIDENCE MERELY BECAUSE IT EXISTS.

So before any field is used as causal linkage it must pass a gate with two
halves, and BOTH have to be checked because either can be empty on its own:

    CODE SIDE  (this file)   is there a write site at all?
    DATA SIDE  (statement 0  how many rows are actually populated, over what
                of the SQL)  span, as a share of the relevant universe?

A field is UNAVAILABLE if either half is empty, and an UNAVAILABLE tier does
not emit a zero-result finding. Its result is NOT IDENTIFIABLE FROM RETAINED
DATA -- a different statement with a different meaning, and one that cannot be
mistaken for evidence of absence.

WHAT COUNTS AS A WRITE SITE. The field name appearing inside a SQL string
literal that also contains INSERT or UPDATE. That is deliberately narrow: a
field merely SELECTed, or named in a comment, or read by a census, is not
written by anything and must not count. Reads are listed separately so the
difference between "we read this" and "we write this" is visible rather than
inferred.
"""
import ast
import os
import re
import sys

ROOT = "backend"

# Every field this research uses, or could use, as causal linkage. Adding a
# field to an attribution tier without adding it here is the defect this file
# exists to prevent.
# SEMANTIC VERIFICATION -- stage 4. A field is UNVERIFIED until something has
# established WHAT ITS VALUES NAME. Population is not semantics: a column can be
# 100% populated with identifiers that join to nothing we can use. Promoting an
# unverified field into a tier is how a join gets invented.
#
# "verified_by" must cite the evidence, not an intention. Anything else is
# UNVERIFIED and stays out of causal attribution.
SEMANTICS = {
    "intent":          ("VERIFIED", "migration 050 defines the four wire values "
                                    "and the CHECK constraint enforces them"),
    "kind":            ("VERIFIED", "migration 047 CHECK constraint enumerates "
                                    "the six roles"),
    "target":          ("VERIFIED", "migration 046: ratio x his_net, whole "
                                    "shares, as production computed it"),
    "ledger_net":      ("VERIFIED", "migration 046/047: long-token shares by "
                                    "our own booking"),
    "his_net":         ("VERIFIED", "migration 046: long minus other, in "
                                    "long-token shares"),
    "mark":            ("VERIFIED", "migration 046: the venue quote at the tick"),
    "target_at_place": ("VERIFIED", "migration 047: the target the order was "
                                    "placed against"),
    "ledger_at_place": ("VERIFIED", "migration 047: our ledger at placement"),
    "bid_at_place":    ("VERIFIED", "migration 047: the book as we saw it"),
    "ask_at_place":    ("VERIFIED", "migration 047: the book as we saw it"),
    "his_fill_id":     ("UNVERIFIED", "a write site exists, but what the value "
                                      "NAMES is not established -- statement 0b "
                                      "probes format, join to trades.id, "
                                      "coverage and cardinality"),
    "trigger_trade_id": ("UNVERIFIED", "no population to verify"),
    "his_fill_ts":      ("UNVERIFIED", "no population to verify"),
    "first_fill_at":    ("UNVERIFIED", "no population to verify"),
}

FIELDS = [
    ("mirror_orders", "trigger_trade_id", "tier 1 direct source-fill lineage"),
    ("mirror_orders", "his_fill_id", "tier 1 alternative source-fill lineage"),
    ("mirror_orders", "his_fill_ts", "source-fill timestamp for reaction timing"),
    ("mirror_orders", "first_fill_at", "our own time-to-fill"),
    ("mirror_orders", "target_at_place", "tier 2/3 target lineage"),
    ("mirror_orders", "ledger_at_place", "tier 2 ledger lineage"),
    ("mirror_orders", "bid_at_place", "execution-quality reference"),
    ("mirror_orders", "ask_at_place", "execution-quality reference"),
    ("mirror_orders", "intent", "signed wire direction -- economic meaning"),
    ("mirror_orders", "kind", "order role"),
    ("mirror_shadow", "target", "the recorded commanded target"),
    ("mirror_shadow", "ledger_net", "the recorded pre-command position"),
    ("mirror_shadow", "his_net", "RN1 signed state as production read it"),
    ("mirror_shadow", "mark", "valuation horizon price"),
]

def sql_strings(text):
    """Every string literal in the module, via the PARSER.

    NOT a regex. The first version of this file matched quote pairs with a
    regular expression and produced FALSE "no write site" results: a single
    apostrophe in a `#` comment ("don't") opens a match that swallows code
    until the next quote, so the pairing drifts and real INSERT strings are
    never seen. It reported target_at_place as unwritten when mirror_live.py
    plainly writes it. A gate that wrongly marks real evidence UNAVAILABLE is
    worse than the bug it was written to catch, so the tokenizer does the
    tokenizing.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if len(node.value) > 12:
                yield node.value


def scan():
    writes, reads = {}, {}
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        if "migrations" in dirpath or "tests" in dirpath:
            continue                      # migrations define; tests are not production
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            try:
                text = open(path, errors="ignore").read()
            except OSError:
                continue
            for body in sql_strings(text):
                upper = body.upper()
                mutating = "INSERT" in upper or "UPDATE" in upper
                for _tbl, field, _why in FIELDS:
                    if not re.search(r"\b%s\b" % re.escape(field), body):
                        continue
                    (writes if mutating else reads).setdefault(field, set()).add(path)
    return writes, reads


def main():
    writes, reads = scan()
    print("EVIDENCE-TIER PRECONDITION GATE -- CODE SIDE")
    print("a write site is the field inside a SQL string that also has "
          "INSERT or UPDATE\n")
    print(f"{'field':<20} {'write':<6} {'read':<5} {'stage 2':<14} "
          f"{'stage 4 semantics':<12}  purpose")
    print("-" * 110)
    missing = []
    for tbl, field, why in FIELDS:
        w, r = len(writes.get(field, ())), len(reads.get(field, ()))
        if w == 0:
            missing.append((tbl, field, why))
        stage2 = "WRITE_SITE" if w else "NO_WRITE_SITE"
        stage4 = SEMANTICS.get(field, ("UNVERIFIED", ""))[0]
        print(f"{field:<20} {w:<6} {r:<5} {stage2:<14} {stage4:<12}  {why}")
    print()
    print("stages 3 and 5 (historical population, final eligibility) are "
          "measured by")
    print("statement 0 of research/rn1_causal_ledger.sql -- a write site with "
          "no rows is")
    print("still UNAVAILABLE, and a populated field with UNVERIFIED semantics "
          "is still")
    print("ineligible for attribution.")
    print()
    if missing:
        print("NO WRITE SITE ANYWHERE IN PRODUCTION CODE -- these fields are "
              "UNAVAILABLE as evidence:")
        for tbl, field, why in missing:
            print(f"  {tbl}.{field:<18} ({why})")
        print()
        print("A tier built on any of these must report NOT IDENTIFIABLE FROM "
              "RETAINED DATA.")
        print("It must NOT report a zero: a filter that cannot fire has "
              "measured nothing.")
        return 1
    print("every linkage field has at least one write site")
    return 0


if __name__ == "__main__":
    sys.exit(main())
