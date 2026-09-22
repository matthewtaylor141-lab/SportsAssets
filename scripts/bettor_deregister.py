"""THE MINIMAL FORWARD DEREGISTRATION -- tier 2 of the rollback.

Usage:  python scripts/bettor_deregister.py                  apply + verify
        python scripts/bettor_deregister.py --verify-only    verify only


Removes the registration line and its import from
`workers/all.py`. Nothing else in the release is reverted: the
repairs, the telemetry, the stop receipt and every unrelated change
stay exactly where they are, and no collected evidence is disturbed.

THE SUPERVISOR CANNOT START A LOOP IT IS NOT HANDED. `run_forever`
iterates the registration list; a name absent from it is never
scheduled, so this stops the worker without touching a trading control
or a database row.

Reach for this only when the database control is not enough — when
`obs-stop` cannot be written, or a running process is not honouring
it. For every ordinary stop, `render-ops sql obs-stop confirm=DO` is
faster and needs no deploy.

Verification is by AST, not by reading the diff: the module is parsed
and the whole tree is searched for the name. A commented-out line that
still left the import, or a second registration elsewhere, would pass
a visual check and fail this one.
"""
import ast
import pathlib
import sys

P = pathlib.Path(__file__).resolve().parents[1] / \
    "backend" / "sportsassets" / "workers" / "all.py"

OLD_IMPORT = """from . import (analytics, bettor_live_loop, bettor_state,"""
NEW_IMPORT = """from . import (analytics, bettor_state,"""

OLD_REG = """    ("bettor_live", bettor_live_loop.main),\n"""
NEW_REG = ""


def verify(path: pathlib.Path) -> int:
    src = path.read_text()
    tree = ast.parse(src)
    hits = [n for n in ast.walk(tree)
            if isinstance(n, ast.Name) and n.id == "bettor_live_loop"]
    hits += [a.name for n in ast.walk(tree)
             if isinstance(n, ast.ImportFrom)
             for a in n.names if a.name == "bettor_live_loop"]
    # FIND THE ACTUAL LIST, AND FAIL IF IT IS NOT FOUND.
    #
    # The first version of this walker only handled `ast.Assign` and
    # `LOOPS` is an `ast.AnnAssign` (`LOOPS: list[...] = [...]`), so it
    # found nothing and reported "bettor_live registered: False" --
    # vacuously true, and exactly the kind of check that passes without
    # testing anything. A detector that cannot locate the list must say
    # so rather than return a clean answer about an empty one.
    loops = None
    for n in ast.walk(tree):
        tgt = None
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            tgt = n.target.id
        elif isinstance(n, ast.Assign) and len(n.targets) == 1 \
                and isinstance(n.targets[0], ast.Name):
            tgt = n.targets[0].id
        if tgt == "LOOPS" and isinstance(getattr(n, "value", None), ast.List):
            loops = [e for e in n.value.elts if isinstance(e, ast.Tuple)]
    if loops is None:
        print("VERIFY FAILED -- could not locate the LOOPS list; this "
              "check would otherwise pass vacuously")
        return 1
    names = []
    for e in loops:
        if e.elts and isinstance(e.elts[0], ast.Constant):
            names.append(e.elts[0].value)
    if not names:
        print("VERIFY FAILED -- LOOPS parsed to zero named entries")
        return 1
    print("registered loops: %d" % len(names))
    print("bettor_live registered: %s" % ("bettor_live" in names))
    print("bettor_live_loop referenced anywhere: %s" % bool(hits))
    if hits or "bettor_live" in names:
        print("VERIFY FAILED")
        return 1
    print("VERIFY OK -- the supervisor cannot start a loop it is not handed")
    return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--verify-only":
        return verify(P)
    s = P.read_text()
    if OLD_IMPORT not in s or OLD_REG not in s:
        print("ANCHOR MISSING -- refusing to patch blindly")
        return 2
    s = s.replace(OLD_IMPORT, NEW_IMPORT).replace(OLD_REG, NEW_REG)
    P.write_text(s)
    print("deregistered")
    return verify(P)


if __name__ == "__main__":
    sys.exit(main())
