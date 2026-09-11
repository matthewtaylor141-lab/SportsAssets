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


def main(paths):
    bad = False
    for path in paths:
        text = open(path).read()
        problems = workflow_guards(path, text)
        sql = re.sub(r"^\s*\\echo.*$", "", text, flags=re.M)
        try:
            stmts = pglast.parse_sql(sql)
        except Exception as exc:
            print(f"{path}: PARSE FAILED: {exc}")
            bad = True
            continue
        for i, raw in enumerate(stmts, 1):
            problems += check_statement(raw.stmt, path, i)
        if problems:
            bad = True
            for p in problems:
                print(p)
        else:
            print(f"{path}: OK -- {len(stmts)} statements, guards pass, "
                  f"CTE column references resolve")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
