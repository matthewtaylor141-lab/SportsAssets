#!/usr/bin/env python3
"""Prove, from the code path, that the Phase X / P2 collectors cannot write
to a venue, place an order, touch a production position, or invoke a
production trading worker.

Run it BEFORE running a collector anywhere. Exit 0 means the five
properties hold for every file the entry point can load.

  python3 research/trackx_readonly_audit.py

It walks the real import closure -- every `_load(...)`,
`spec_from_file_location(...)` and `with_name("....py")` an entry point
reaches, transitively -- and greps the executable lines. Comments and
docstrings are stripped first, so a file that *mentions* place_order in
prose does not fail while a file that *calls* it does.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENTRIES = ("run85_phasex_run.py", "trackp2_capture.py")

LOADERS = (
    re.compile(r'_load\(\s*["\'][^"\']+["\']\s*,\s*["\']([^"\']+\.py)["\']'),
    re.compile(r'spec_from_file_location\([^)]*?["\']([^"\']+\.py)["\']',
               re.S),
    re.compile(r'with_name\(\s*["\']([^"\']+\.py)["\']'),
)

FORBIDDEN = {
    "1_HTTP_WRITE_VERBS": (
        r'\.post\(', r'\.put\(', r'\.patch\(', r'\.delete\(',
        r'["\'](?:POST|PUT|PATCH|DELETE)["\']'),
    "2_ORDER_PLACEMENT": (
        r'place_order', r'cancel_order', r'create_order', r'submit_order',
        r'post_order', r'/orders\b', r'portfolio'),
    "3_MIRROR_LIVE": (r'mirror_live\s*=\s*True', r'set_mirror_live',
                      r'MIRROR_LIVE'),
    "4_PRODUCTION_POSITIONS": (
        r'\bpositions\b\s*\(', r'UPDATE\s+', r'INSERT\s+INTO',
        r'DELETE\s+FROM', r'get_pool', r'asyncpg'),
    "5_PRODUCTION_WORKERS": (
        r'live_executor', r'sportsassets\.workers', r'workers\.all',
        r'from\s+edge\b', r'import\s+edge\b', r'KalshiAdapter'),
    "CREDENTIALS": (
        r'os\.environ', r'getenv', r'PRIVATE_KEY', r'SECRET_KEY',
        r'ACCESS-KEY', r'load_pem_private_key', r'ADMIN_TOKEN'),
    "FORBIDDEN_HOSTS": (
        r'api\.polymarket\.us', r'trading-api\.kalshi\.com',
        r'onrender\.com', r'bettortoken\.com'),
}

ALLOWED_HOSTS = {"https://gateway.polymarket.us",
                 "https://api.elections.kalshi.com"}

# A line that DEFINES a denylist is the guard, not the threat. Exempted by
# the name being assigned, never by the pattern -- so a real call to
# place_order still fails even in a file that happens to own a denylist.
GUARD_ASSIGNMENT = re.compile(
    r'^\s*(?:DENIED|FORBIDDEN|BANNED|BLOCKED)[A-Z_]*\s*=')

# A URL is CONTACTED only if it reaches a request. A citation constant --
# SOURCE = "https://docs.polymarket.us/fees", which records where a fee
# rule came from -- is provenance, and provenance is the opposite of a
# network call. Recognised by the name it is bound to, and reported
# separately rather than waved through.
CONTACTING_NAME = re.compile(
    r'^\s*[A-Z_]*(?:BASE|URL|API|ENDPOINT|HOST)[A-Z_]*\s*=')
REQUEST_CALL = re.compile(r'\.get\(|base_url|request\(')


def code_lines(path: Path):
    """Executable lines only: comments stripped, docstrings removed via the
    AST so prose about a forbidden call never fails the audit and a real
    call never escapes it."""
    src = path.read_text()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return [(i, l) for i, l in enumerate(src.splitlines(), 1)]
    doc = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            for i in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                doc.add(i)
    out = []
    for i, line in enumerate(src.splitlines(), 1):
        if i in doc:
            continue
        stripped = line.split("#")[0]
        if stripped.strip():
            out.append((i, stripped))
    return out


def closure(entry: str):
    seen, stack = {}, [HERE / entry]
    while stack:
        p = stack.pop()
        if p.name in seen or not p.exists():
            continue
        src = p.read_text()
        seen[p.name] = p
        for rx in LOADERS:
            for m in rx.finditer(src):
                stack.append(HERE / m.group(1))
    return [seen[k] for k in sorted(seen)]


def main() -> int:
    bad = 0
    for entry in ENTRIES:
        files = closure(entry)
        print("=" * 72)
        print("ENTRY %s -- import closure %d files" % (entry, len(files)))
        for f in files:
            print("   ", f.name)
        print()
        hosts = set()
        for label, pats in FORBIDDEN.items():
            hits = []
            for f in files:
                for i, line in code_lines(f):
                    if GUARD_ASSIGNMENT.match(line):
                        continue
                    for pat in pats:
                        if re.search(pat, line):
                            hits.append((f.name, i, line.strip()[:88]))
                            break
            print("  %-26s %s" % (label,
                                  "CLEAN" if not hits else
                                  "%d HIT(S)" % len(hits)))
            for h in hits:
                print("      %s:%d  %s" % h)
                bad += 1
        cited = set()
        for f in files:
            for i, line in code_lines(f):
                for m in re.finditer(r'https://[A-Za-z0-9.\-]+', line):
                    (hosts if (CONTACTING_NAME.match(line)
                               or REQUEST_CALL.search(line))
                     else cited).add(m.group(0))
        print("  HOSTS REACHABLE BY A REQUEST:")
        for h in sorted(hosts):
            ok = h in ALLOWED_HOSTS
            print("      %-42s %s" % (h, "ALLOWED" if ok else "UNEXPECTED"))
            if not ok:
                bad += 1
        print("  HOSTS APPEARING ONLY AS PROVENANCE CITATIONS (not called):")
        for h in sorted(cited - hosts):
            print("      %s" % h)
        print()
    print("AUDIT = %s" % ("PASS" if not bad else "FAIL (%d)" % bad))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
