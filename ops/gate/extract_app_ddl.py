#!/usr/bin/env python3
"""EXTRACT THE DDL THE APPLICATION CREATES AT RUNTIME, not via a migration.

WHY THIS EXISTS. `us_premap` is created by `workers.premap._ensure_table` when
the app starts, and migrations 031 and 055 ALTER it. Apply the migration set to
an empty database and those two fail with "relation us_premap does not exist".

That was invisible while the gate ran migrations with `ON_ERROR_STOP=0` and sent
errors to `/dev/null`. Hardening the gate so any migration failure voids the run
then turned a benign, expected, every-time condition into a gate that could never
produce a result -- which is worse than no gate, because the first person to see
it concludes the check is noise.

WHY IT EXTRACTS RATHER THAN RESTATES. A copy of the DDL in this file would drift
from the application's the first time a column changed there, and the drift would
be silent: the gate's schema and production's would differ and every test would
still pass. So the statements are read out of the application source, verbatim,
and a change in one place cannot leave the other behind.

IT PRINTS NOTHING RATHER THAN GUESSING. If the enclosing function cannot be
found, or it holds no DDL, output is empty and the exit status is non-zero -- the
gate voids on that, because a missing bootstrap IS the invalid environment the
check exists to catch.

    usage: extract_app_ddl.py <path-to-premap.py> [more.py ...]
"""

from __future__ import annotations

import re
import sys

#: The functions whose bodies hold runtime DDL. Named rather than discovered, so
#: an unrelated function that happens to contain a CREATE cannot be swept in.
DDL_FUNCTIONS = ("_ensure_table", "ensure_schema")

#: One statement per match, kept verbatim. CREATE TYPE is included because an
#: enum the app defines would fail a later ALTER the same way a table does.
#:
#: MATCHED INSIDE THE SQL STRING LITERALS, NOT ACROSS THE WHOLE FUNCTION. A
#: naive `CREATE ...[^;]*;` over the function body runs PAST the statement's
#: closing `;` -- the DDL sits in a triple-quoted string followed by ordinary
#: Python, and the first `;` after it may be hundreds of characters into code.
#: The first version of this file did exactly that and emitted Python source as
#: SQL, which psql then rejected. So the string literals are isolated first.
_STMT = re.compile(
    r'(CREATE\s+(?:TABLE|UNIQUE\s+INDEX|INDEX|TYPE)\b[^;]*;)',
    re.S | re.I)

#: Does a literal begin with DDL? Cheaper and more exact than trying to find the
#: statement's end, which the application does not mark.
_STARTS_WITH_DDL = re.compile(
    r'^\s*CREATE\s+(?:TABLE|UNIQUE\s+INDEX|INDEX|TYPE)\b', re.I)

#: The triple-quoted literals in a function body, which is where the SQL lives.
_SQL_LITERAL = re.compile(r'"""(.*?)"""|\'\'\'(.*?)\'\'\'', re.S)


def _function_bodies(src: str) -> list[str]:
    """The source of each named DDL function, by indentation rather than regex
    lookahead -- a nested `def` inside the body would end the match early."""
    out = []
    lines = src.split("\n")
    for i, line in enumerate(lines):
        m = re.match(r'^(\s*)(?:async\s+)?def\s+(\w+)\s*\(', line)
        if not m or m.group(2) not in DDL_FUNCTIONS:
            continue
        indent = len(m.group(1))
        body = []
        for nxt in lines[i + 1:]:
            if nxt.strip() and (len(nxt) - len(nxt.lstrip())) <= indent:
                break
            body.append(nxt)
        out.append("\n".join(body))
    return out


def extract(paths) -> list[str]:
    stmts: list[str] = []
    for path in paths:
        try:
            src = open(path, encoding="utf-8", errors="replace").read()
        except OSError as exc:
            print("-- could not read %s: %s" % (path, exc), file=sys.stderr)
            continue
        for body in _function_bodies(src):
            # ONLY WHAT IS INSIDE A SQL STRING LITERAL. Anything else in the
            # body is Python and must never reach psql.
            for a, b in _SQL_LITERAL.findall(body):
                lit = (a or b).strip()
                if not _STARTS_WITH_DDL.match(lit):
                    continue
                # A SEMICOLON IS OPTIONAL IN THE APPLICATION'S SOURCE, because
                # `pool.execute()` takes one statement and does not need one.
                # So the literal IS the statement: splitting on `;` here would
                # find none and emit nothing, which is how the previous version
                # reported "no DDL found" on a file that plainly has some.
                for part in (lit.split(";") if ";" in lit else [lit]):
                    st = part.strip()
                    if not st or not _STARTS_WITH_DDL.match(st):
                        continue
                    st = st + ";"
                    # DEDUPLICATED BY TEXT. The same DDL appears in more than
                    # one module here (the replay harness carries a copy), and
                    # applying it twice is harmless but noisy.
                    if st not in stmts:
                        stmts.append(st)
    return stmts


def main(argv) -> int:
    if len(argv) < 2:
        print("usage: extract_app_ddl.py <file.py> [...]", file=sys.stderr)
        return 2
    stmts = extract(argv[1:])
    if not stmts:
        print("-- NO APP-MANAGED DDL FOUND. Looked for %s in: %s"
              % (", ".join(DDL_FUNCTIONS), " ".join(argv[1:])),
              file=sys.stderr)
        return 1
    print("-- app-managed DDL, extracted verbatim from the application source.")
    print("-- Applied BEFORE the migrations, because migrations 031 and 055")
    print("-- ALTER a table the application creates at runtime.")
    for st in stmts:
        print(st)
    print("-- statements: %d" % len(stmts), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
