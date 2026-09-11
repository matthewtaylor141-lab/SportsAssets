#!/usr/bin/env python3
"""Pre-flight for research/*.sql, run locally before dispatching a run.

WHY THIS EXISTS. Run 65 died on statement 2 with

    ERROR: column a.resid_shares_fifo_check does not exist

after ~80 s of database time. The file had passed a local `pglast.parse_sql`
check, because PARSING IS NOT NAME RESOLUTION -- a syntactically perfect
reference to a column that no CTE projects parses fine. A column had been
renamed in one CTE and one reader of it was left behind.

So this adds the cheap half of name resolution: for every reference of the form
`alias.column` where `alias` is bound to a COMMON TABLE EXPRESSION in the same
statement, check that the CTE actually projects that column. Base tables are
skipped -- their columns are not knowable without a catalogue -- and any CTE
whose target list contains `*` is skipped too, since its projection is only
knowable by resolving what it selected from.

It also replicates the two guards the research-sql workflow applies, so a file
that would be refused on the runner is refused here first, for free.

Usage:  python3 research/check_sql.py research/<file>.sql [...]
Exit 0 if every file passes, 1 otherwise.
"""
import re
import sys

try:
    import pglast
    from pglast import ast
    from pglast.enums import MinMaxOp
except ImportError:  # pragma: no cover
    sys.exit("pglast is required: pip install pglast")

KW = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|grant|revoke|create|copy|"
    r"vacuum|reindex|refresh|call|do|merge|lock|set\s+role)\b",
    re.I,
)


def workflow_guards(path, text):
    """Replicate research-sql.yml's two refusal checks."""
    problems = []
    for i, line in enumerate(text.splitlines(), 1):
        s = line.lstrip()
        if s.startswith("\\") and not re.match(r"\\echo(\s|$)", s):
            problems.append(f"{path}:{i}: psql meta-command other than \\echo: {s[:60]}")
    # the runner strips `--` comments and deletes \echo lines, then scans
    stripped = "\n".join(
        "" if re.match(r"^\s*\\echo", ln) else re.sub(r"--.*$", "", ln)
        for ln in text.splitlines()
    )
    for i, line in enumerate(stripped.splitlines(), 1):
        m = KW.search(line)
        if m:
            problems.append(
                f"{path}:{i}: mutating keyword {m.group(0)!r} in executable SQL"
            )
    return problems


def cte_columns(cte):
    """Output column names of a CTE, or None if they cannot be determined.

    An explicit column alias list wins. Otherwise the target list is read:
    a ResTarget with a name uses it, a bare ColumnRef uses its last field, and
    a `*` anywhere makes the projection unknowable -> None.
    """
    if cte.aliascolnames:
        return {str(c.sval) for c in cte.aliascolnames}
    q = cte.ctequery
    while isinstance(q, ast.SelectStmt) and q.larg is not None:
        q = q.larg  # a set operation projects its left arm's names
    if not isinstance(q, ast.SelectStmt) or not q.targetList:
        return None
    cols = set()
    for t in q.targetList:
        if t.name:
            cols.add(t.name)
            continue
        v = t.val
        if isinstance(v, ast.ColumnRef):
            last = v.fields[-1]
            if isinstance(last, ast.A_Star):
                return None
            cols.add(str(last.sval))
        else:
            return None  # an unaliased expression: name unknown, be silent
    return cols


def walk(node, fn):
    if isinstance(node, ast.Node):
        fn(node)
        for name in node:
            walk(getattr(node, name, None), fn)
    elif isinstance(node, (list, tuple)):
        for x in node:
            walk(x, fn)


def check_duplicate_ctes(stmt, path, idx):
    """A CTE name declared twice in one statement.

    WHY. Run 77 died on statement 12 with

        ERROR: WITH query name "g" specified more than once

    after ten statements and eight minutes of database time. The shared event
    core declares a CTE `g`, and a later statement appended its own `g`. Every
    existing layer passed it: it parses, and the column resolver only asks
    whether a referenced column exists in SOME CTE of that name -- with two, it
    silently resolved against the first. A generated file that pastes a common
    prefix into every statement makes this collision easy and invisible, so it
    gets its own check rather than being left to the runner.
    """
    names, dupes = {}, []
    def collect(n):
        if isinstance(n, ast.WithClause):
            seen = set()
            for cte in (n.ctes or []):
                if cte.ctename in seen:
                    dupes.append(cte.ctename)
                seen.add(cte.ctename)
    walk(stmt, collect)
    return [f"{path}: statement {idx}: CTE name {d!r} is declared more than once "
            f"in the same WITH clause -- PostgreSQL refuses this at run time"
            for d in sorted(set(dupes))]


def check_statement(stmt, path, idx):
    ctes = {}
    def collect(n):
        if isinstance(n, ast.CommonTableExpr):
            ctes[n.ctename] = cte_columns(n)
    walk(stmt, collect)
    if not ctes:
        return []

    # alias -> every relation it is bound to ANYWHERE in the statement.
    #
    # The map is deliberately statement-global rather than scope-aware, so it
    # must be read conservatively: the same short alias is routinely reused at
    # different query levels (`trades t` inside a CTE, `tok t` in the outer
    # query), and treating those as one binding produces a flood of false
    # positives -- which is exactly what the first version of this file did.
    # An alias is therefore checked ONLY when every binding of it in the
    # statement is the same CTE. Anything ambiguous, or bound to a base table,
    # is skipped: this check is here to catch renames, not to be exhaustive.
    bindings = {}
    def bind(n):
        if isinstance(n, ast.RangeVar):
            key = n.alias.aliasname if n.alias else n.relname
            target = n.relname if (n.relname in ctes and n.schemaname is None) else None
            bindings.setdefault(key, set()).add(target)
    walk(stmt, bind)
    aliases = {
        a: next(iter(t)) for a, t in bindings.items()
        if len(t) == 1 and next(iter(t)) is not None
    }
    if not aliases:
        return []

    problems = []
    seen = set()
    def refs(n):
        if not isinstance(n, ast.ColumnRef) or len(n.fields) != 2:
            return
        a, c = n.fields
        if isinstance(a, ast.A_Star) or isinstance(c, ast.A_Star):
            return
        alias, col = str(a.sval), str(c.sval)
        cte = aliases.get(alias)
        if cte is None:
            return
        cols = ctes.get(cte)
        if cols is None or col in cols:
            return
        key = (alias, col, cte)
        if key in seen:
            return
        seen.add(key)
        problems.append(
            f"{path}: statement {idx}: {alias}.{col} -- CTE {cte!r} projects "
            f"no such column (it has: {', '.join(sorted(cols)) or '<none>'})"
        )
    walk(stmt, refs)
    return problems



# ---------------------------------------------------------------------------
# THE MISSING-LEG SEMANTIC GUARD
#
# WHY THIS EXISTS. Run 68's telescoping self-check failed because of
#
#     LEAST(sum(sh) FILTER (WHERE outcome_index = 0),
#           sum(sh) FILTER (WHERE outcome_index = 1))
#
# PostgreSQL's LEAST and GREATEST IGNORE NULL arguments -- the result is NULL
# only if EVERY argument is NULL. So on a condition with only one outcome leg,
# one FILTER aggregate is NULL and LEAST silently returns THE OTHER LEG'S FULL
# QUANTITY where the intended value is zero. Nothing errors; a wrong number is
# produced and propagates.
#
# The name-resolution guard above catches renames. This is its counterpart for
# MISSING-LEG ARITHMETIC: wherever absence of a leg means ZERO economically,
# the aggregate must say so explicitly rather than leaving NULL to be swallowed.
#
# Two aggregate shapes are nullable over a NON-EMPTY group, and they are the
# ones that matter here:
#     agg(...) FILTER (WHERE ...)      -- NULL when no row passes the filter
#     max/min(CASE WHEN ... THEN ...)  -- NULL when no row matches and there is
#                                         no ELSE branch
# By contrast sum(CASE WHEN ... THEN x ELSE 0 END) is non-null over a non-empty
# group, which is why the walk was correct while its yardstick was not.
#
# Flagged only inside LEAST/GREATEST, where the swallowing is silent. A nullable
# leg aggregate used in ordinary arithmetic propagates NULL instead, which is
# visible rather than wrong -- a different and far less dangerous failure.

NULLABLE_AGGS = {"max", "min", "sum", "count", "avg", "bool_or", "bool_and"}


def _fname(node):
    try:
        return ".".join(str(x.sval) for x in node.funcname).lower()
    except Exception:
        return ""


def _is_nullable_leg_agg(node, cte_nullable, aliases):
    """True if this expression can be NULL because a leg is absent."""
    if isinstance(node, ast.CoalesceExpr):
        return False                      # explicitly made zero-safe
    if isinstance(node, ast.TypeCast):
        return _is_nullable_leg_agg(node.arg, cte_nullable, aliases)
    if isinstance(node, ast.FuncCall):
        name = _fname(node)
        if name == "coalesce":
            return False
        if node.agg_filter is not None:
            return True                   # agg(...) FILTER (...)
        if name in ("max", "min") and node.args:
            a = node.args[0]
            if isinstance(a, ast.CaseExpr) and a.defresult is None:
                return True               # max/min(CASE WHEN ... THEN ... END)
        return False
    if isinstance(node, ast.ColumnRef) and len(node.fields) == 2:
        a, c = node.fields
        if isinstance(a, ast.A_Star) or isinstance(c, ast.A_Star):
            return False
        cte = aliases.get(str(a.sval))
        if cte is None:
            return False
        return cte_nullable.get(cte, {}).get(str(c.sval), False)
    return False


def check_missing_leg(stmt, path, idx, ctes, aliases, allow_lines=frozenset()):
    # nullability of each CTE's output columns, resolved in declaration order so
    # a later CTE can see an earlier one's columns
    cte_nullable = {}
    order = []

    def collect(n):
        if isinstance(n, ast.CommonTableExpr):
            order.append(n)
    walk(stmt, collect)

    for cte in order:
        q = cte.ctequery
        while isinstance(q, ast.SelectStmt) and q.larg is not None:
            q = q.larg
        cols = {}
        if isinstance(q, ast.SelectStmt) and q.targetList:
            inner = {}
            def bind_inner(n):
                if isinstance(n, ast.RangeVar) and n.schemaname is None:
                    key = n.alias.aliasname if n.alias else n.relname
                    inner.setdefault(key, set()).add(
                        n.relname if n.relname in cte_nullable else None)
            walk(q.fromClause, bind_inner)
            inner_alias = {a: next(iter(t)) for a, t in inner.items()
                           if len(t) == 1 and next(iter(t)) is not None}
            for t in q.targetList:
                nm = t.name
                if nm is None and isinstance(t.val, ast.ColumnRef):
                    last = t.val.fields[-1]
                    if not isinstance(last, ast.A_Star):
                        nm = str(last.sval)
                if nm:
                    cols[nm] = _is_nullable_leg_agg(t.val, cte_nullable, inner_alias)
        cte_nullable[cte.ctename] = cols

    problems = []
    seen = set()

    # LEAST/GREATEST are NOT FuncCall nodes. PostgreSQL parses them into a
    # dedicated MinMaxExpr (op IS_LEAST / IS_GREATEST). The first version of
    # this guard looked for FuncCall and therefore COULD NEVER FIRE -- it
    # reported every file clean, including the one carrying the run-68 defect.
    # That is the vacuous-filter error in tooling form, so the regression test
    # below is not optional: a guard is not installed until it has been seen to
    # fail on the bug it was written for.
    def visit(n):
        if not isinstance(n, ast.MinMaxExpr) or not n.args:
            return
        op = "LEAST" if int(n.op) == int(MinMaxOp.IS_LEAST) else "GREATEST"
        bad = [i for i, a in enumerate(n.args)
               if _is_nullable_leg_agg(a, cte_nullable, aliases)]
        if not bad:
            return
        # An intentional reproduction of the defect -- needed to measure a
        # correction against the published figure -- is marked in the SQL with
        # `lint: allow-missing-leg` on the construct's line or just above it.
        # Without this, the only way to measure the bug would be to delete the
        # guard.
        if getattr(n, "location", None) is not None and n.location in allow_lines:
            return
        key = (idx, str(n.args[0])[:120], tuple(bad))
        if key in seen:
            return
        seen.add(key)
        problems.append(
            f"{path}: statement {idx}: MISSING-LEG HAZARD -- {op}() argument(s) "
            f"{bad} can be NULL when a leg is absent, and {op} IGNORES NULL, so "
            f"the other argument is returned where zero is meant. Wrap each "
            f"operand in COALESCE(..., 0)."
        )
    walk(stmt, visit)
    return problems



# ---------------------------------------------------------------------------
# THE BASE-TABLE COLUMN GUARD
#
# WHY THIS EXISTS. The CTE guard above says, in its own header, "Base tables are
# skipped -- their columns are not knowable without a catalogue". That sentence
# was true and the gap it admits is real: run 74 was written referencing
# live_orders.created_at, which does not exist (the column is placed_at). Every
# layer passed it -- it parses, and no CTE is involved -- and it would have died
# on the runner after minutes of database time, exactly as run 65 did.
#
# There IS a catalogue in this repository: backend/migrations/*.sql. It is
# parsed here with pglast rather than regex, so CREATE TABLE and
# ALTER TABLE ... ADD COLUMN are read the way PostgreSQL reads them.
#
# CONSERVATIVE BY CONSTRUCTION, because a false positive here teaches the next
# person to switch the guard off:
#   * only tables the migrations actually define are checked; anything else
#     (views, tables made elsewhere) is skipped entirely
#   * an alias bound to more than one thing anywhere in the statement is skipped
#   * an unqualified column is checked ONLY at a query level whose FROM has
#     exactly one known base table and, besides it, only CTEs whose projections
#     are fully known -- otherwise the column could belong to something else
#   * output aliases of the level are admitted, since GROUP BY / ORDER BY may
#     legitimately name them rather than any table column
#   * the walk stops at a nested SelectStmt or SubLink, so an inner scope's
#     columns are never judged against an outer scope's tables

MIGRATIONS = "backend/migrations"


ALTER_ELSEWHERE = re.compile(
    r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?[\"']?(\w+)[\"']?\s+ADD\s+COLUMN", re.I)


def load_schema(root=MIGRATIONS, repo="backend"):
    """table -> set of column names, from the repository's own migrations.

    A table is KNOWN only if (a) its CREATE TABLE is in the migrations, so the
    full column list was seen, and (b) no CREATE TABLE for it exists anywhere
    else in the tree. Both conditions are load-bearing:

      * a table reached only by ALTER TABLE ... ADD COLUMN yields a PARTIAL
        column set, and checking against a partial set invents defects. That is
        real here: us_premap is created in workers/premap.py and only extended
        by migrations 031 and 055, so the migrations know 8 of its columns and
        the first version of this guard reported four healthy queries broken.
      * a table whose columns are ALTERed outside the migrations has a column
        set the migrations cannot see, so it is dropped too.

    The second rule is deliberately narrow. An earlier version excluded any
    table with a CREATE TABLE anywhere else in the tree, which removed
    live_orders and mirror_orders themselves -- test fixtures re-create them,
    which says nothing about whether the migrations' definition is complete --
    and left the guard unable to fire on the very bug it was written for.

    Skipping is always the safe direction: an unknown table is simply not
    checked, which is where this guard stood before it existed.
    """
    import os
    schema, created_here = {}, set()
    if not os.path.isdir(root):
        return schema
    for fn in sorted(os.listdir(root)):
        if not fn.endswith(".sql"):
            continue
        try:
            stmts = pglast.parse_sql(open(os.path.join(root, fn)).read())
        except Exception:
            continue                      # a migration we cannot parse is skipped
        for raw in stmts:
            n = raw.stmt
            if isinstance(n, ast.CreateStmt) and n.relation is not None:
                created_here.add(n.relation.relname)
                cols = schema.setdefault(n.relation.relname, set())
                for el in (n.tableElts or []):
                    if isinstance(el, ast.ColumnDef) and el.colname:
                        cols.add(el.colname)
            elif isinstance(n, ast.AlterTableStmt) and n.relation is not None:
                cols = schema.setdefault(n.relation.relname, set())
                for cmd in (n.cmds or []):
                    d = getattr(cmd, "def_", None) or getattr(cmd, "def", None)
                    if isinstance(d, ast.ColumnDef) and d.colname:
                        cols.add(d.colname)
    altered_elsewhere = set()
    for dirpath, dirnames, filenames in os.walk(repo):
        if "migrations" in dirpath:
            continue
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in filenames:
            if not fn.endswith((".py", ".sql")):
                continue
            try:
                body = open(os.path.join(dirpath, fn), errors="ignore").read()
            except OSError:
                continue
            altered_elsewhere.update(
                m.group(1) for m in ALTER_ELSEWHERE.finditer(body))
    return {t: c for t, c in schema.items()
            if t in created_here and t not in altered_elsewhere}


def walk_level(node, fn):
    """walk(), but stopping at anything that opens a new naming scope."""
    if isinstance(node, ast.Node):
        if isinstance(node, (ast.SelectStmt, ast.SubLink)):
            return
        fn(node)
        for name in node:
            walk_level(getattr(node, name, None), fn)
    elif isinstance(node, (list, tuple)):
        for x in node:
            walk_level(x, fn)


def check_base_columns(stmt, path, idx, ctes, schema):
    if not schema:
        return []
    problems, seen = [], set()

    # statement-global alias map, same conservative rule as the CTE guard
    bindings = {}
    def bind(n):
        if isinstance(n, ast.RangeVar) and n.schemaname is None:
            key = n.alias.aliasname if n.alias else n.relname
            bindings.setdefault(key, set()).add(n.relname)
    walk(stmt, bind)
    base_alias = {
        a: next(iter(t)) for a, t in bindings.items()
        if len(t) == 1 and next(iter(t)) not in ctes and next(iter(t)) in schema
    }

    def report(alias, col, table):
        key = (idx, alias, col, table)
        if key in seen:
            return
        seen.add(key)
        near = sorted(c for c in schema[table]
                      if c.startswith(col[:4]) or col.startswith(c[:4]))
        problems.append(
            f"{path}: statement {idx}: {alias}.{col} -- table {table!r} has no "
            f"such column"
            + (f" (did you mean: {', '.join(near[:4])}?)" if near else "")
        )

    # qualified: alias.column
    def qualified(n):
        if not isinstance(n, ast.ColumnRef) or len(n.fields) != 2:
            return
        a, c = n.fields
        if isinstance(a, ast.A_Star) or isinstance(c, ast.A_Star):
            return
        alias, col = str(a.sval), str(c.sval)
        table = base_alias.get(alias)
        if table and col not in schema[table]:
            report(alias, col, table)
    walk(stmt, qualified)

    # unqualified, only where the level's FROM leaves exactly one candidate
    levels = []
    def collect(n):
        if isinstance(n, ast.SelectStmt):
            levels.append(n)
    walk(stmt, collect)

    for sel in levels:
        rels = []
        walk_level(sel.fromClause, lambda n: rels.append(n)
                   if isinstance(n, ast.RangeVar) else None)
        if not rels:
            continue
        tables = [r.relname for r in rels if r.schemaname is None]
        base = [t for t in tables if t not in ctes]
        if len(base) != 1 or base[0] not in schema:
            continue
        others = [t for t in tables if t in ctes]
        if any(ctes.get(t) is None for t in others):
            continue                       # an unknown CTE projection: cannot tell
        allowed = set(schema[base[0]])
        for t in others:
            allowed |= ctes[t]
        for t in (sel.targetList or []):    # output aliases are legal references
            if t.name:
                allowed.add(t.name)
        def unqualified(n):
            if not isinstance(n, ast.ColumnRef) or len(n.fields) != 1:
                return
            f = n.fields[0]
            if isinstance(f, ast.A_Star):
                return
            col = str(f.sval)
            if col not in allowed:
                report(base[0], col, base[0])
        # NOT walk_level(sel, ...): walk_level stops AT a SelectStmt, so handing
        # it this level's own node makes it return immediately and the check
        # cannot fire. The first version did exactly that and reported the
        # live_orders.created_at file clean -- the vacuous guard again, in the
        # tool written to prevent vacuous guards. The level's clauses are walked
        # individually instead, which is also what keeps a nested scope out.
        for clause in (sel.targetList, sel.fromClause, sel.whereClause,
                       sel.groupClause, sel.havingClause, sel.sortClause,
                       sel.distinctClause, sel.windowClause):
            walk_level(clause, unqualified)
    return problems


def _cte_and_aliases(stmt):
    """CTE names present, and the conservative alias -> CTE map (see above)."""
    ctes = {}
    def collect(n):
        if isinstance(n, ast.CommonTableExpr):
            ctes[n.ctename] = cte_columns(n)
    walk(stmt, collect)
    bindings = {}
    def bind(n):
        if isinstance(n, ast.RangeVar):
            key = n.alias.aliasname if n.alias else n.relname
            target = n.relname if (n.relname in ctes and n.schemaname is None) else None
            bindings.setdefault(key, set()).add(target)
    walk(stmt, bind)
    aliases = {a: next(iter(t)) for a, t in bindings.items()
               if len(t) == 1 and next(iter(t)) is not None}
    return ctes, aliases


def main(paths):
    bad = False
    schema = load_schema()
    for path in paths:
        text = open(path).read()
        problems = workflow_guards(path, text)
        sql = re.sub(r"^\s*\\echo.*$", "", text, flags=re.M)
        # character offsets of every construct on (or just below) a line
        # carrying the allow pragma, so the AST node's `location` can be matched
        allowed = set()
        starts, off = [], 0
        for ln in sql.splitlines(True):
            starts.append(off)
            off += len(ln)
        lines = sql.splitlines()
        for i, ln in enumerate(lines):
            if "lint: allow-missing-leg" not in ln:
                continue
            for j in (i, i + 1, i + 2, i + 3):
                if j < len(lines):
                    allowed.update(range(starts[j], starts[j] + len(lines[j]) + 1))
        try:
            stmts = pglast.parse_sql(sql)
        except Exception as exc:
            print(f"{path}: PARSE FAILED: {exc}")
            bad = True
            continue
        for i, raw in enumerate(stmts, 1):
            cte_map, alias_map = _cte_and_aliases(raw.stmt)
            problems += check_duplicate_ctes(raw.stmt, path, i)
            problems += check_statement(raw.stmt, path, i)
            problems += check_missing_leg(raw.stmt, path, i, cte_map, alias_map,
                                          allow_lines=allowed)
            problems += check_base_columns(raw.stmt, path, i, cte_map, schema)
        if problems:
            bad = True
            for p in problems:
                print(p)
        else:
            print(f"{path}: OK -- {len(stmts)} statements, guards pass, CTE and "
                  f"base-table column references resolve "
                  f"({len(schema)} tables known)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
