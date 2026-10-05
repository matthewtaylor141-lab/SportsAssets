"""THE E-SERIES MIGRATION PINS, RE-EXPRESSED (R30A ci, 2026-10-04).

Twelve FILL / coverage lane tests (E12, E12b, E18, E24, E25, E27, E28, E29,
E30, T2, C7, C9, C10, C11, C16) carried the line

    assert files[-1] == "064_run833_stream_channels.sql"  # re-pinned 2026-09-12 (run 83.3) ...

It pinned the GLOBALLY newest migration. That was true for the week after
run 83.3 landed 064 and became false the first time an unrelated lane added
065; with 225 and 226 in the tree it has been red on every run (CI
37223385978), asserting something about other people's work. e23 already
re-expressed its own copy (test_e23_cancel_fill_adopt.py); these helpers give
the rest the same treatment, and NEVER drop the property:

  * a lane that SHIPPED a migration (057, 058, 059, 060, 061): its file exists
    exactly once, sorts immediately after its predecessor (the tests keep
    their own `files[i + 1]` lines for that), is additive (they keep their
    statement pins), and every later lane only APPENDS after it -- numbering
    strictly increasing, nothing inserted beside or before a landed file --
    with 064 still present after it.
  * a lane that shipped NO migration: the e-series epoch is intact (064
    present, append-only numbering) and NO migration file anywhere names the
    lane's own concepts -- the direct form of "this lane adds no migration".
"""
from __future__ import annotations

import pathlib

MIG_DIR = pathlib.Path(__file__).resolve().parents[1] / "migrations"

#: The newest migration when the E-series closed (run 83.3, 2026-09-12).
E_SERIES_NEWEST = "064_run833_stream_channels.sql"


def migration_files() -> list[str]:
    return sorted(p.name for p in MIG_DIR.glob("*.sql"))


def _number(name: str) -> int:
    return int(name.split("_", 1)[0])


def assert_append_only(files: list[str]) -> None:
    """Every migration carries a distinct number and sorting by name is
    sorting by number: a later lane can only append."""
    nums = [_number(f) for f in files]
    assert nums == sorted(nums) and len(set(nums)) == len(nums), files
    assert E_SERIES_NEWEST in files


def assert_later_lanes_append_after(own: str, files: list[str] | None = None) -> None:
    files = migration_files() if files is None else files
    assert files.count(own) == 1, own
    assert_append_only(files)
    assert files.index(own) <= files.index(E_SERIES_NEWEST)
    assert all(_number(f) > _number(own) for f in files[files.index(own) + 1:])


def assert_lane_added_no_migration(*concepts: str) -> None:
    assert concepts, "name the lane's own concepts"
    files = migration_files()
    assert_append_only(files)
    for name in files:
        body = (MIG_DIR / name).read_text()
        for c in concepts:
            assert c not in name and c not in body, (name, c)
