#!/usr/bin/env python3
"""The probe driver's own boundary, proved by AST scan.

`test_tape_capture.py` already gates the capturer -- get-only, one
host, no credential vocabulary, no environment read -- by walking the
syntax tree with docstrings stripped. It has to be an AST scan rather
than a grep, because the capturer's own docstring says "No API key, no
Auth0 token, no client assertion", and a grep would refuse the module
for promising exactly what it delivers.

`tape_probe.py` drives that capturer, so it is held to the same two
rules: it may not carry a mutating verb, and it may not name a URL of
its own. Its only network access is the capturer's.
"""
from __future__ import annotations

import ast
import pathlib
import sys

MUTATING = {"post", "put", "patch", "delete", "request"}


def check(path="tape_probe.py") -> list:
    tree = ast.parse(pathlib.Path(path).read_text())
    problems = []
    verbs = sorted({n.attr for n in ast.walk(tree)
                    if isinstance(n, ast.Attribute) and n.attr in MUTATING})
    if verbs:
        problems.append("mutating verb(s): %s" % ", ".join(verbs))
    hosts = sorted({n.value for n in ast.walk(tree)
                    if isinstance(n, ast.Constant)
                    and isinstance(n.value, str) and "://" in n.value})
    if hosts:
        problems.append("names a URL of its own: %s" % ", ".join(hosts))
    return problems


def main(argv=None):
    path = (argv or sys.argv[1:] or ["tape_probe.py"])[0]
    problems = check(path)
    for p in problems:
        print("refused: %s" % p)
    if problems:
        return 1
    print("guard: %s is get-free and names no host -- OK" % path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
