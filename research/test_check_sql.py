#!/usr/bin/env python3
"""Fixtures for the base-table column layer of research/check_sql.py.

Run:  python3 research/test_check_sql.py
Exit 0 if every fixture behaves as specified.

WHY THESE SIX AND NOT FEWER. The layer was written after run 74 was drafted
against live_orders.created_at, a column that does not exist. Writing a guard is
the easy half; the hard half is knowing it FIRES where it should and STAYS
SILENT where it should, and that split is exactly where this one went wrong
twice before landing:

  * the first version handed walk_level() the query level's own SelectStmt,
    which walk_level stops AT, so it returned immediately and pronounced the
    offending file clean -- a guard that cannot fail, in the tool written to
    stop guards that cannot fail;
  * the second version excluded any table with a CREATE TABLE anywhere in the
    tree, which removed live_orders and mirror_orders themselves (test fixtures
    re-create them) and again left it unable to fire.

Both were caught by running the guard against the bug, not by reading it. These
fixtures make that permanent. THREE OF THEM MUST STAY SILENT -- a guard is only
useful if a clean file stays clean, and an over-eager guard is switched off by
the next person, which is worse than no guard.

    1  valid qualified base column         -> SILENT
    2  invalid qualified base column       -> FIRES
    3  valid unqualified base column       -> SILENT
    4  invalid unqualified base column     -> FIRES   (the run-74 bug's shape)
    5  CTE-derived column                  -> SILENT  (never judged against the
                                                       migration catalogue)
    6  table ALTERed outside the migrations-> SILENT  (unresolved, never
                                                       falsely validated)

Fixture 6 is the one that is easiest to get wrong in the permissive direction.
us_premap is CREATEd in workers/premap.py and only EXTENDED by migrations 031
and 055, so the migrations know a fraction of its columns. Checking against a
partial catalogue does not find defects, it invents them. The correct behaviour
is to treat the table as UNRESOLVED and say nothing at all.
"""
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])

try:
    import pglast
    from pglast import ast  # noqa: F401  (imported for the check module's use)
except ImportError:  # pragma: no cover
    sys.exit("pglast is required: pip install pglast")

import check_sql

FAILURES = []


def findings(sql):
    """Base-column findings only, so a fixture cannot pass or fail on another
    layer's output."""
    schema = check_sql.load_schema()
    out = []
    for i, raw in enumerate(pglast.parse_sql(sql), 1):
        ctes, _ = check_sql._cte_and_aliases(raw.stmt)
        out += check_sql.check_base_columns(raw.stmt, "<fixture>", i, ctes, schema)
    return out


def expect(name, sql, should_fire, must_mention=None):
    got = findings(sql)
    fired = bool(got)
    ok = fired == should_fire
    if ok and should_fire and must_mention:
        ok = any(must_mention in g for g in got)
    verb = "FIRES" if should_fire else "stays silent"
    print(f"  {'PASS' if ok else 'FAIL'}  {name} -- must {verb}")
    if not ok:
        FAILURES.append(name)
        for g in got:
            print(f"        got: {g}")
        if should_fire and not got:
            print("        got: nothing")


SCHEMA = check_sql.load_schema()
print(f"catalogue: {len(SCHEMA)} tables resolved from the migrations")
if "live_orders" not in SCHEMA:
    print("  FAIL  live_orders must be in the catalogue or fixtures 1-4 are vacuous")
    FAILURES.append("catalogue missing live_orders")
if "us_premap" in SCHEMA:
    print("  FAIL  us_premap must NOT be in the catalogue (created outside the "
          "migrations, so its column set is partial)")
    FAILURES.append("catalogue wrongly includes us_premap")
print()

print("BASE-TABLE COLUMN LAYER")

expect("1 valid qualified base column",
       "SELECT o.placed_at FROM live_orders o;",
       should_fire=False)

expect("2 invalid qualified base column",
       "SELECT o.created_at FROM live_orders o;",
       should_fire=True, must_mention="created_at")

expect("3 valid unqualified base column",
       "SELECT count(*) FROM live_orders WHERE lane = 'mirror' "
       "AND placed_at > now();",
       should_fire=False)

# THE RUN-74 BUG, verbatim in shape. If this ever stops firing the guard has
# regressed to the version that reported it clean.
expect("4 invalid unqualified base column (the run-74 shape)",
       "SELECT count(*) FROM live_orders WHERE lane = 'mirror' "
       "AND created_at > now();",
       should_fire=True, must_mention="created_at")

expect("5 CTE-derived column is not judged against the catalogue",
       "WITH w AS (SELECT placed_at AS whenever FROM live_orders) "
       "SELECT w.whenever FROM w;",
       should_fire=False)

expect("6 table ALTERed outside the migrations is unresolved, not validated",
       "SELECT u.identifier, u.signed FROM us_premap u;",
       should_fire=False)

print()
print("SUPPORTING BEHAVIOUR, so the silences above are not silence for the "
      "wrong reason")

expect("an output alias named in ORDER BY is a legal reference",
       "SELECT placed_at AS day FROM live_orders ORDER BY day;",
       should_fire=False)

expect("a nested scope is not judged against the outer scope's table",
       "SELECT o.placed_at FROM live_orders o WHERE o.id IN "
       "(SELECT b.id FROM mirror_books b WHERE b.opened_at > now());",
       should_fire=False)

expect("a real column error inside a CTE body still fires",
       "WITH w AS (SELECT o.created_at FROM live_orders o) SELECT * FROM w;",
       should_fire=True, must_mention="created_at")

print()
if FAILURES:
    print(f"{len(FAILURES)} FIXTURE(S) FAILED: {', '.join(FAILURES)}")
    sys.exit(1)
print("all base-table column fixtures pass")
