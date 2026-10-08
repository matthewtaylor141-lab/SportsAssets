"""APPLIED MIGRATIONS ARE IMMUTABLE -- against production's own record.

Production 08828d04 booted with "126_funded_inventory_exits_and_fees.sql WAS
ALREADY APPLIED AND ITS CONTENT HAS SINCE CHANGED ... The change has NOT run
and never will": a comment-only edit (527bd45c) made after the file ran.
scripts/migrate.py only WARNS at boot (a failed boot would take the API
down), so the guard belongs here, before a build ships: every migration
production has applied is pinned to the content hash production recorded
(migrations/APPLIED_MIGRATIONS.sha256, read back from schema_migrations),
and a change to one fails CI. The difference always goes in a NEW
migration."""
from __future__ import annotations

import hashlib
import pathlib
import re

MIG = pathlib.Path(__file__).resolve().parents[1] / "migrations"
MANIFEST = MIG / "APPLIED_MIGRATIONS.sha256"


def manifest() -> dict:
    out = {}
    for line in MANIFEST.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        sha, name = line.split()
        assert re.fullmatch(r"[0-9a-f]{64}", sha), line
        assert name not in out, "duplicate manifest line: %s" % name
        out[name] = sha
    return out


def sha(path: pathlib.Path) -> str:
    # exactly scripts/migrate.content_sha: sha256 of the file's text
    return hashlib.sha256(path.read_text().encode("utf-8")).hexdigest()


def test_every_applied_migration_is_byte_identical_to_what_production_ran():
    m = manifest()
    assert len(m) >= 223
    changed = sorted(n for n, h in m.items()
                     if (MIG / n).exists() and sha(MIG / n) != h)
    assert changed == [], (
        "APPLIED MIGRATIONS CHANGED AFTER THEY RAN (the change will never "
        "execute in production; put it in a NEW migration): %s" % changed)


def test_no_applied_migration_was_deleted_or_renamed():
    gone = sorted(n for n in manifest() if not (MIG / n).exists())
    assert gone == [], gone


def test_no_new_migration_is_inserted_below_the_applied_frontier():
    """A new file numbered below an applied one would run out of order."""
    m = manifest()
    frontier = max(m)
    late = sorted(p.name for p in MIG.glob("*.sql")
                  if p.name not in m and p.name < frontier)
    assert late == [], late


def test_the_hash_is_the_runners_own():
    from sportsassets.scripts import migrate
    p = MIG / "001_init.sql"
    assert migrate.content_sha(p.read_text()) == sha(p)


def test_a_mutated_applied_migration_is_caught(tmp_path):
    """The guard is not vacuous: an edit of an applied file is reported."""
    m = manifest()
    name = "126_funded_inventory_exits_and_fees.sql"
    edited = (MIG / name).read_text() + "\n-- a comment added later\n"
    assert hashlib.sha256(edited.encode()).hexdigest() != m[name]
    # and the restored file is the exact applied version (git 714680bb)
    assert m[name] == ("ae8ef15e2f2779e877d8619a6e565f8b798ebd9b9daccff6"
                       "6a4cded26172e8a3")
    assert sha(MIG / name) == m[name]


def test_manifest_lines_are_sha256sum_format_with_no_null_hash():
    """`cd backend/migrations && grep -v '^#' APPLIED_MIGRATIONS.sha256 |
    sha256sum -c` must work as an independent check, and a NULL recorded
    hash is never adopted into the manifest."""
    bad = [ln for ln in MANIFEST.read_text().splitlines()
           if ln.strip() and not ln.startswith("#")
           and not re.fullmatch(r"[0-9a-f]{64}  \d{3}_[A-Za-z0-9_]+\.sql", ln)]
    assert bad == [], bad


def test_the_refresh_query_ships_with_the_manifest_and_is_read_only():
    """The manifest names the query that produced it; that query is in the
    same lineage and passes research-sql's read-only keyword guard."""
    q = MIG.parents[1] / "research" / "rt_schema_migrations_manifest.sql"
    assert q.exists(), q
    sql = "\n".join(re.sub(r"--.*$", "", ln) for ln in q.read_text()
                    .splitlines() if not ln.lstrip().startswith("\\echo"))
    assert not re.search(
        r"\b(insert|update|delete|drop|alter|truncate|grant|revoke|create|"
        r"copy|vacuum|reindex|refresh|call|do|merge|lock|set\s+role)\b",
        sql, re.I)
    assert "FROM schema_migrations" in sql and "content_sha" in sql
    assert "research/rt_schema_migrations_manifest.sql" in MANIFEST.read_text()
