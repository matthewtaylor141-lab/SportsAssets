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

print()
print("NO-FROM LAYER (added after run 80.5b died on a SELECT with no FROM clause)")


def nf(sql):
    out = []
    for i, raw in enumerate(pglast.parse_sql(sql), 1):
        out += check_sql.check_no_from(raw.stmt, "<fixture>", i)
    return out


def expect_nf(name, sql, should_fire, must_mention=None):
    got = nf(sql)
    ok = bool(got) == should_fire
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


# THE RUN-80.5b BUG, verbatim in shape: a CTE is declared, the final SELECT
# references its columns, and the FROM clause is simply absent. All four earlier
# layers pass it -- with no relation in scope there is nothing for them to check.
expect_nf("7 the run-80.5b shape: CTE columns with no FROM",
          "WITH cond AS (SELECT 1 AS anomalous, true AS midnight_utc) "
          "SELECT count(*) FILTER (WHERE anomalous) AS n;",
          should_fire=True, must_mention="anomalous")

expect_nf("8 the same query WITH its FROM restored",
          "WITH cond AS (SELECT 1 AS anomalous) "
          "SELECT count(*) FILTER (WHERE anomalous) AS n FROM cond;",
          should_fire=False)

expect_nf("9 a literal-only SELECT is legitimate",
          "SELECT 'markets.raw' AS field, 'NOT RETAINED' AS availability;",
          should_fire=False)

expect_nf("10 a scalar subquery carries its own FROM",
          "SELECT (SELECT count(*) FROM live_orders) AS n;",
          should_fire=False)

print()
if FAILURES:
    print(f"{len(FAILURES)} FIXTURE(S) FAILED: {', '.join(FAILURES)}")
    sys.exit(1)
print("all fixtures pass, base-table and no-FROM layers")

print()
print("GROUP BY LAYER (added after run 80.5b's rerun died on statement 5)")


def gb(sql):
    out = []
    for i, raw in enumerate(pglast.parse_sql(sql), 1):
        _, aliases = check_sql._cte_and_aliases(raw.stmt)
        out += check_sql.check_group_by(raw.stmt, "<fixture>", i, aliases)
    return out


def expect_gb(name, sql, should_fire, must_mention=None):
    got = gb(sql)
    ok = bool(got) == should_fire
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


# THE RUN-80.5b STATEMENT-5 BUG, verbatim in shape: a CTE column is selected
# outside an aggregate while the GROUP BY names only its siblings.
expect_gb("11 the run-80.5b shape: an ungrouped CTE column beside an aggregate",
          "WITH r AS (SELECT 1 AS condition_id, now() AS resolved_at), "
          "t AS (SELECT 1 AS condition_id, now() AS ts) "
          "SELECT r.condition_id, (max(t.ts) > r.resolved_at) AS anomalous "
          "FROM r JOIN t ON t.condition_id = r.condition_id "
          "GROUP BY r.condition_id;",
          should_fire=True, must_mention="r.resolved_at")

expect_gb("12 the same query with resolved_at added to the GROUP BY",
          "WITH r AS (SELECT 1 AS condition_id, now() AS resolved_at), "
          "t AS (SELECT 1 AS condition_id, now() AS ts) "
          "SELECT r.condition_id, (max(t.ts) > r.resolved_at) AS anomalous "
          "FROM r JOIN t ON t.condition_id = r.condition_id "
          "GROUP BY r.condition_id, r.resolved_at;",
          should_fire=False)

# THE FALSE POSITIVE THAT COST 15 FINDINGS. A FILTER predicate is evaluated per
# input row, exactly like an aggregate's arguments, so an ungrouped column in it
# is legal. If this ever starts firing the guard has regressed to the version
# that condemned files which had already run clean.
expect_gb("13 a FILTER predicate may reference an ungrouped column",
          "WITH c AS (SELECT 1 AS lane, true AS anomalous) "
          "SELECT c.lane, count(*) FILTER (WHERE c.anomalous) AS n "
          "FROM c GROUP BY c.lane;",
          should_fire=False)

# THE SECOND FALSE POSITIVE. ROLLUP/CUBE/GROUPING SETS wrap the column in a
# GroupingSet node, so a top-level-only read of the clause does not see it.
expect_gb("14 GROUP BY ROLLUP still counts as grouped",
          "WITH c AS (SELECT 'NFL' AS sport, 1 AS n) "
          "SELECT c.sport, sum(c.n) FROM c GROUP BY ROLLUP (c.sport);",
          should_fire=False)

expect_gb("15 a base-table column is never judged (functional dependency)",
          "SELECT o.id, o.lane, count(*) FROM live_orders o GROUP BY o.id;",
          should_fire=False)

expect_gb("16 GROUP BY by ordinal position: abstain rather than guess",
          "WITH c AS (SELECT 1 AS a, 2 AS b) "
          "SELECT c.a, c.b, count(*) FROM c GROUP BY 1;",
          should_fire=False)

print()
if FAILURES:
    print(f"{len(FAILURES)} FIXTURE(S) FAILED: {', '.join(FAILURES)}")
    sys.exit(1)
print("all fixtures pass: base-table, no-FROM and GROUP BY layers")
