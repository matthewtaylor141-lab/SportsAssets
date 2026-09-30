"""THE SHIPPED RUNTIME IS THE GATED RUNTIME.

* `requirements.lock` covers every [project].dependencies entry, each pinned
  exactly.
* The image is built from the gate's interpreter (by digest) and installs the
  lock, then the package with --no-deps: a fresh hosting build resolves
  nothing of its own.
* The environment running THIS test holds exactly the locked set and the
  locked interpreter -- so a gate run on a drifted environment fails here.
* The serving process checks itself against the lock at boot and logs one
  RUNTIME_MANIFEST line.
"""
from __future__ import annotations

import inspect
import pathlib
import tomllib

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from sportsassets import runtime_manifest as RM

BACKEND = pathlib.Path(__file__).resolve().parents[1]


def test_the_lock_pins_every_declared_dependency_exactly():
    lock = RM.read_lock(BACKEND / "requirements.lock")
    assert len(lock) >= 80
    assert all(v and v[0].isdigit() for v in lock.values())
    deps = tomllib.loads((BACKEND / "pyproject.toml").read_text())[
        "project"]["dependencies"]
    for d in deps:
        name = canonicalize_name(Requirement(d).name)
        assert name in lock, name
        assert Requirement(d).specifier.contains(lock[name]), (d, lock[name])
    assert lock["anthropic"] == "1.9.0"


def test_the_image_installs_the_lock_without_resolving():
    df = (BACKEND / "Dockerfile").read_text()
    froms = [l for l in df.splitlines() if l.startswith("FROM ")]
    assert froms == ["FROM python:%s-slim@sha256:" % RM.EXPECTED_PYTHON
                     + froms[0].split("@sha256:")[1]]
    assert len(froms[0].split("@sha256:")[1]) == 64
    assert "COPY backend/pyproject.toml backend/requirements.lock ./" in df
    assert "pip install --no-cache-dir -r requirements.lock" in df
    assert "pip install --no-cache-dir --no-deps ." in df
    assert "pip install --no-cache-dir ." not in df


def test_this_environment_is_the_locked_one():
    got = RM.check()
    assert got["ok"] is True, RM.log_line(got)


def test_a_drifted_package_is_named():
    got = RM.check({"anthropic": "0.0.1", "no-such-dist-xyz": "1.0"})
    assert got["ok"] is False
    assert got["mismatched"][0]["package"] == "anthropic"
    assert got["missing"] == ["no-such-dist-xyz"]
    assert "anthropic:" in RM.log_line(got)


def test_the_serving_process_checks_itself_at_boot():
    from sportsassets.api import app as A
    src = inspect.getsource(A.lifespan)
    assert "runtime_manifest" in src and "_rm.check()" in src
