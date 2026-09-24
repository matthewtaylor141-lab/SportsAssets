"""EVERY LITERAL `COPY` PATH MUST SURVIVE `.dockerignore`.

THE FAILURE THIS EXISTS FOR. `29b25a5` added
`COPY research/beta48/bettor_incentive_score.py` to the Dockerfile. The file
was committed and present in the working tree, 180 tests passed, and the
build failed in 24 seconds with "not found" -- because `.dockerignore` says

    research/beta48/*
    !research/beta48/shadow
    !research/beta48/*.json

so a `.py` file beside them is not in the build CONTEXT at all. Render
reported `build_failed`, the previous image kept serving, and the production
read that followed measured the OLD build: two new endpoints answered 404
while their tests were green. `.dockerignore` documents this exact trap one
paragraph above where I fell into it.

A repository-file check passes. A build does not. This test is the
difference, implemented as Docker's own last-match-wins rule so it can run
without a daemon.
"""
import fnmatch
import os
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "backend" / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"

pytestmark = pytest.mark.skipif(
    not DOCKERFILE.exists() or not DOCKERIGNORE.exists(),
    reason="Dockerfile or .dockerignore not in this tree")


def _patterns():
    out = []
    for raw in DOCKERIGNORE.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        neg = line.startswith("!")
        out.append((neg, line[1:].strip() if neg else line))
    return out


def _matches(pattern: str, path: str) -> bool:
    """Docker matches the WHOLE relative path, and a directory pattern
    covers everything beneath it."""
    pattern = pattern.rstrip("/")
    if fnmatch.fnmatch(path, pattern):
        return True
    # a pattern naming a parent directory excludes its contents
    if path.startswith(pattern + "/"):
        return True
    # `**` crossing separators
    if "**" in pattern:
        rx = re.escape(pattern).replace(r"\*\*", ".*").replace(r"\*", "[^/]*")
        if re.fullmatch(rx, path):
            return True
    return False


def admitted(path: str) -> bool:
    """Is `path` in the build context? LAST matching rule wins."""
    verdict = True
    for neg, pat in _patterns():
        if _matches(pat, path):
            verdict = neg
    return verdict


def _copy_paths():
    """Every literal source path in a COPY, line continuations joined."""
    text = DOCKERFILE.read_text().replace("\\\n", " ")
    found = []
    for line in text.splitlines():
        line = line.strip()
        if not line.upper().startswith("COPY "):
            continue
        parts = line.split()[1:]
        parts = [p for p in parts if not p.startswith("--")]
        if len(parts) < 2:
            continue
        for src in parts[:-1]:          # last token is the destination
            found.append(src)
    return found


def test_the_dockerfile_has_copies_to_check():
    paths = _copy_paths()
    assert len(paths) >= 5, paths


def test_every_copy_source_is_in_the_build_context():
    """The check that was missing. A glob is satisfied if ANY file it
    matches is admitted; a literal path must itself be admitted."""
    problems = []
    for src in _copy_paths():
        if any(ch in src for ch in "*?["):
            hits = sorted(str(p.relative_to(ROOT))
                          for p in ROOT.glob(src) if p.is_file())
            if hits and not any(admitted(h) for h in hits):
                problems.append("%s (glob: %d files, none admitted)"
                                % (src, len(hits)))
            continue
        target = ROOT / src
        if not target.exists():
            problems.append("%s (not in the repository at all)" % src)
        elif target.is_file() and not admitted(src):
            problems.append("%s (excluded by .dockerignore)" % src)
    assert not problems, (
        "these COPY sources would fail the build:\n  " + "\n  ".join(problems))


def test_the_scorer_and_the_manifest_are_both_admitted():
    """The two narrow re-admissions this image depends on, named so a
    future tidy-up of .dockerignore cannot quietly drop them."""
    for path in ("research/beta48/acceptance/incentive_manifest.json",
                 "research/beta48/bettor_incentive_score.py",
                 "research/beta48/bettor_incentive_opportunity.py"):
        assert (ROOT / path).exists(), path
        assert admitted(path), "%s is not in the build context" % path


def test_the_big_trees_are_still_excluded():
    """The re-admissions must stay narrow: the context exists to be small,
    and `acceptance/` is 4.9 MB of evidence artefacts."""
    for path in ("research/beta48/acceptance/suite.xml",
                 "frontend/index.html",
                 "docs/anything.md",
                 "edge-engine/x.py"):
        assert not admitted(path), "%s should not be in the context" % path


def test_the_matcher_agrees_with_the_rule_that_caught_this():
    """A guard on the guard: the bare rule must exclude a .py file beside
    the admitted ones, which is precisely what broke the build."""
    saved = DOCKERIGNORE.read_text()
    assert "research/beta48/*" in saved
    # a file NOT re-admitted must read as excluded
    assert not admitted("research/beta48/some_other_module.py")
