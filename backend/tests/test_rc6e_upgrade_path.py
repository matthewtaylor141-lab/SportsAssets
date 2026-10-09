"""THE UPGRADE PATH: THIS RELEASE'S MIGRATIONS ONTO THE PREVIOUS RELEASE'S
DATABASE WITH REPRESENTATIVE ROWS, AND THE PREVIOUS RELEASE ON THE NEW SCHEMA
(RC6 lane E, owner directive 2E).

The fresh-database receipt proves an EMPTY database builds to production's
fingerprint. Production is not empty. These tests run tools/
upgrade_path_receipt.py against real PostgreSQL (RN1X_TEST_DSN; capital-
critical's own PostgreSQL 16 in CI) with production's runner and hold:

  * a migration that is harmless on an empty table but fails on a populated
    one (ADD COLUMN ... NOT NULL without a default) passes the fresh build
    and FAILS the upgrade path -- the representative rows are what catch it;
  * a migration that deletes seeded rows, an edited applied migration (the
    runner's own changed-after-apply warning AND the content hashes), an
    edited immutability-manifest line: FAILED, each named;
  * rollback compatibility: a dropped column blocks, a new CHECK on an
    existing table is unproven, an additive migration is COMPATIBLE, and
    the previous release's runner re-run on the new schema applies nothing;
  * a table a new migration acts on that held no row makes the receipt
    INCOMPLETE (never PASSED-with-a-hole);
  * the real repository: the newest migration applied over the previous
    migration set with ~80% of tables holding a row, PASSED, COMPATIBLE;
    and capital-critical's step as written, from the previous release
    commit (7fd4574e) to this tree, when that commit is in the clone.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys
import uuid
from urllib.parse import urlparse

import pytest
import yaml

BACKEND = pathlib.Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
CC_WF = ROOT / ".github" / "workflows" / "capital-critical.yml"
DSN = os.environ.get("RN1X_TEST_DSN", "")


def _tool(name):
    spec = importlib.util.spec_from_file_location(
        "rc6e_up_" + name, BACKEND / "tools" / ("%s.py" % name))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


UP = _tool("upgrade_path_receipt")
FD = _tool("fresh_db_receipt")
REL = "69a8a07e5335864bd3d70f7160494aed13305fcc"
PREV = "7fd4574e9ac8b95c355035a5bd4a9927d01c29ea"

BASE = {
    "001_up_a.sql": "CREATE TABLE up_a (id text PRIMARY KEY, "
                    "status text NOT NULL CHECK (status IN ('OPEN','CLOSED')), "
                    "qty numeric NOT NULL CHECK (qty > 0 AND qty < 1), "
                    "note text);\n",
    "002_up_b.sql": "CREATE TABLE up_b (id bigserial PRIMARY KEY, "
                    "a_id text NOT NULL REFERENCES up_a(id), "
                    "at timestamptz NOT NULL);\n",
    "003_up_x.sql": "CREATE TABLE up_x (v text NOT NULL "
                    "CHECK (v = md5('q')));\n",
}


def _tree(path, files, manifest=None):
    path.mkdir(parents=True)
    for n, t in files.items():
        (path / n).write_text(t)
    if manifest is not None:
        (path / UP.MANIFEST).write_text("".join(
            "%s  %s\n" % (h, n) for n, h in sorted(manifest.items())))
    return path


# ── pure parts ───────────────────────────────────────────────────────────

def test_touched_tables_are_read_from_the_statements_not_the_comments():
    sql = ("-- ALTER TABLE ghost ADD x int;\n/* UPDATE ghost2 SET x=1 */\n"
           "ALTER TABLE IF EXISTS ONLY up_a ADD COLUMN c int;\n"
           "update up_b set at = now();\nINSERT INTO public.up_c VALUES (1);\n"
           "CREATE UNIQUE INDEX IF NOT EXISTS ix ON up_d (x);\n"
           "CREATE TRIGGER t BEFORE INSERT OR UPDATE ON up_e FOR EACH ROW "
           "EXECUTE FUNCTION f();\nDELETE FROM up_f;\nDROP TABLE IF EXISTS "
           "up_g;\nTRUNCATE TABLE up_h;\nCREATE TABLE fresh (id int);\n")
    assert UP.touched_tables(sql) == {"up_a", "up_b", "up_c", "up_d", "up_e",
                                      "up_f", "up_g", "up_h"}


def _snap(cols, **kw):
    return {"columns": cols, "constraints": kw.get("constraints", {}),
            "unique_indexes": kw.get("unique_indexes", {}),
            "triggers": kw.get("triggers", {})}


def _c(t="text", nn=False, d=False):
    return {"type": t, "notnull": nn, "default": d}


@pytest.mark.parametrize("after,kind,needle", [
    ({}, "blocking", "ROLLBACK_TABLE_DROPPED:t"),
    ({"t": _snap({"a": _c()})}, "blocking", "ROLLBACK_COLUMN_DROPPED:t.b"),
    ({"t": _snap({"a": _c("integer"), "b": _c(nn=True, d=True)})},
     "blocking", "ROLLBACK_COLUMN_TYPE_CHANGED:t.a:text->integer"),
    ({"t": _snap({"a": _c(nn=True), "b": _c(nn=True, d=True)})},
     "blocking", "ROLLBACK_COLUMN_NOT_NULL_WITHOUT_DEFAULT:t.a"),
    ({"t": _snap({"a": _c(), "b": _c(nn=True)})},
     "blocking", "ROLLBACK_DEFAULT_DROPPED_ON_NOT_NULL_COLUMN:t.b"),
    ({"t": _snap({"a": _c(), "b": _c(nn=True, d=True), "n": _c(nn=True)})},
     "blocking", "ROLLBACK_NEW_NOT_NULL_COLUMN_WITHOUT_DEFAULT:t.n"),
    ({"t": _snap({"a": _c(), "b": _c(nn=True, d=True)},
                 constraints={"t_ck": "CHECK (a <> '')"})},
     "unproven", "ROLLBACK_CONSTRAINT_ADDED_ON_EXISTING_TABLE:t:t_ck"),
    ({"t": _snap({"a": _c(), "b": _c(nn=True, d=True)},
                 unique_indexes={"t_u": "CREATE UNIQUE INDEX"})},
     "unproven", "ROLLBACK_UNIQUE_INDEX_ADDED_ON_EXISTING_TABLE:t:t_u"),
    ({"t": _snap({"a": _c(), "b": _c(nn=True, d=True)},
                 triggers={"t_tg": "CREATE TRIGGER"})},
     "unproven", "ROLLBACK_TRIGGER_ADDED_ON_EXISTING_TABLE:t:t_tg"),
])
def test_what_the_previous_code_meets_on_the_new_schema(after, kind, needle):
    before = {"t": _snap({"a": _c(), "b": _c(nn=True, d=True)})}
    r = UP.compatibility(before, after)
    assert r["verdict"] == UP.NOT_PROVEN and needle in r[kind], r


def test_additive_changes_are_compatible():
    before = {"t": _snap({"a": _c()})}
    after = {"t": _snap({"a": _c(), "n": _c(nn=True, d=True),
                         "m": _c()}), "new_table": _snap({"z": _c(nn=True)})}
    assert UP.compatibility(before, after) == {
        "verdict": UP.COMPATIBLE, "blocking": [], "unproven": []}


def test_immutability_reads_hashes_and_the_manifest(tmp_path):
    b = _tree(tmp_path / "b", {}, manifest={"001_up_a.sql": "1" * 64,
                                            "002_up_b.sql": "2" * 64})
    c = _tree(tmp_path / "c", {}, manifest={"001_up_a.sql": "1" * 64,
                                            "002_up_b.sql": "9" * 64})
    out = UP.immutability({"001_up_a.sql": "1" * 64, "002_up_b.sql": "2" * 64,
                           "000_gone.sql": "0" * 64}, b, c,
                          {"001_up_a.sql": "1" * 64,
                           "002_up_b.sql": "3" * 64})
    assert "UPGRADE_APPLIED_MIGRATION_REMOVED:000_gone.sql" in out
    assert "UPGRADE_APPLIED_MIGRATION_CHANGED:002_up_b.sql" in out
    assert "UPGRADE_MANIFEST_LINE_EDITED:002_up_b.sql" in out
    assert "UPGRADE_MANIFEST_LINE_DIFFERS_FROM_FILE:002_up_b.sql" in out
    d = _tree(tmp_path / "d", {})
    assert UP.R_MANIFEST_REMOVED in UP.immutability({}, b, d, {})


def test_the_runner_output_is_read_for_applies_and_drift():
    text = ("INFO:__main__:applying 316_x.sql\nWARNING:__main__:126_y.sql "
            "WAS ALREADY APPLIED AND ITS CONTENT HAS SINCE CHANGED. The ...")
    assert UP.runner_lines(text) == {"applied": ["316_x.sql"],
                                     "drift": ["126_y.sql"]}


def test_the_readback_records_and_decides_nothing(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"version": UP.VERSION, "result": "FAILED"}))
    d = UP.readback(run_id="5", run={"path": UP.WORKFLOW, "head_sha": REL},
                    receipt_path=str(p), attestation_verified=False)
    assert d["receipt"]["result"] == "FAILED"
    assert d["provenance"]["attestation_verified"] is False
    assert d["provenance"]["workflow_path"] == UP.WORKFLOW
    assert UP.readback(run_id="", run=None, receipt_path=None,
                       attestation_verified=True)["reason"] == UP.R_NO_GATE_RUN
    p.write_text("{x")
    assert UP.readback(run_id="5", run={}, receipt_path=str(p),
                       attestation_verified=True)["reason"] == \
        UP.R_RECEIPT_NOT_JSON


# ── against real PostgreSQL ──────────────────────────────────────────────

@pytest.fixture()
def empty_db():
    if not DSN:
        pytest.skip("needs RN1X_TEST_DSN")
    import asyncpg
    name = "rc6e_up_" + uuid.uuid4().hex[:10]

    async def admin(sql):
        c = await asyncpg.connect(DSN, timeout=10)
        try:
            await c.execute(sql)
        finally:
            await c.close()
    asyncio.run(admin('CREATE DATABASE "%s"' % name))
    try:
        yield DSN.rsplit("/", 1)[0] + "/" + name
    finally:
        asyncio.run(admin('DROP DATABASE IF EXISTS "%s" WITH (FORCE)' % name))


def _up(tmp_path, dsn, candidate_files, *, base_files=None, base_manifest=None,
        cand_manifest=None):
    b = _tree(tmp_path / "base_mig", base_files or BASE, base_manifest)
    c = _tree(tmp_path / "cand_mig", candidate_files, cand_manifest)
    return UP.run(dsn=dsn, sha=REL, base_sha=PREV, base_backend=BACKEND,
                  candidate_backend=BACKEND, base_migrations=b,
                  candidate_migrations=c, run_id="7", run_attempt="1")


def test_an_additive_upgrade_passes_with_rows_and_is_compatible(
        tmp_path, empty_db):
    r = _up(tmp_path, empty_db, dict(BASE, **{
        "004_add.sql": "ALTER TABLE up_a ADD COLUMN tag text NOT NULL "
                       "DEFAULT 'x';\nCREATE TABLE up_c (id int PRIMARY KEY);\n"
                       "UPDATE up_b SET at = at;\n"}))
    assert r["result"] == UP.PASSED, r["reasons"]
    assert r["reasons"] == [] and r["representative"] == UP.COMPLETE
    assert r["new_migrations"] == ["004_add.sql"]
    assert r["touched_tables"] == ["up_a", "up_b"]
    # the synthesized rows satisfied CHECK literals, bounds and the FK
    assert r["seeded"]["inserted"] >= 2 and r["rows"]["lost"] == []
    assert r["base"]["applied_count"] == 3 == r["base"]["migrations_count"]
    assert r["after"]["fingerprint"] == r["after"]["fresh_build_fingerprint"]
    rc = r["rollback_compatibility"]
    assert rc["verdict"] == UP.COMPATIBLE, rc
    assert rc["base_runner_on_new_schema"] == {"rc": 0, "applied": [],
                                               "drift": []}
    assert r["server_version"]


def test_a_not_null_column_fails_on_rows_the_empty_build_never_had(
        tmp_path, empty_db):
    """The point of representative rows: the same migration applies to an
    empty database (the fresh build) and fails on a populated one."""
    add = {"004_required.sql": "ALTER TABLE up_a ADD COLUMN must text "
                               "NOT NULL;\n"}
    r = _up(tmp_path, empty_db, dict(BASE, **add))
    assert r["result"] == UP.FAILED
    assert "UPGRADE_MIGRATION_FAILED:004_required.sql" in r["reasons"]
    assert any(x.startswith(UP.R_NOT_APPLIED + ":004_required.sql")
               for x in r["reasons"])


def test_the_same_migration_builds_an_empty_database(tmp_path, empty_db):
    cand = _tree(tmp_path / "only", dict(BASE, **{
        "004_required.sql": "ALTER TABLE up_a ADD COLUMN must text "
                            "NOT NULL;\n"}))
    built = UP._runner(BACKEND, cand, empty_db, premap=True)
    assert built["rc"] == 0 and "004_required.sql" in built["applied"]


def test_a_migration_that_deletes_rows_fails(tmp_path, empty_db):
    r = _up(tmp_path, empty_db, dict(BASE, **{
        "004_purge.sql": "DELETE FROM up_b;\n"}))
    assert r["result"] == UP.FAILED
    assert "UPGRADE_ROWS_LOST:up_b:1->0" in r["reasons"]


def test_an_edited_applied_migration_fails_twice_over(tmp_path, empty_db):
    r = _up(tmp_path, empty_db, dict(BASE, **{
        "001_up_a.sql": BASE["001_up_a.sql"] + "-- a comment added later\n"}))
    assert r["result"] == UP.FAILED
    assert "UPGRADE_APPLIED_MIGRATION_CHANGED:001_up_a.sql" in r["reasons"]
    assert "UPGRADE_RUNNER_REPORTED_CHANGED_AFTER_APPLY:001_up_a.sql" in \
        r["reasons"]


def test_an_edited_manifest_line_fails(tmp_path, empty_db):
    tree = {n: hashlib.sha256(t.encode()).hexdigest()
            for n, t in BASE.items()}
    edited = dict(tree, **{"001_up_a.sql": "0" * 64})
    r = _up(tmp_path, empty_db, dict(BASE), base_manifest=tree,
            cand_manifest=edited)
    assert r["result"] == UP.FAILED
    assert "UPGRADE_MANIFEST_LINE_EDITED:001_up_a.sql" in r["reasons"]
    assert "UPGRADE_MANIFEST_LINE_DIFFERS_FROM_FILE:001_up_a.sql" in \
        r["reasons"]


def test_a_dropped_column_passes_the_upgrade_and_blocks_the_rollback(
        tmp_path, empty_db):
    r = _up(tmp_path, empty_db, dict(BASE, **{
        "004_drop.sql": "ALTER TABLE up_a DROP COLUMN note;\n"
                        "ALTER TABLE up_b ADD CONSTRAINT up_b_at_ck "
                        "CHECK (at > '2000-01-01');\n"}))
    assert r["result"] == UP.PASSED, r["reasons"]
    rc = r["rollback_compatibility"]
    assert rc["verdict"] == UP.NOT_PROVEN
    assert "ROLLBACK_COLUMN_DROPPED:up_a.note" in rc["blocking"]
    assert "ROLLBACK_CONSTRAINT_ADDED_ON_EXISTING_TABLE:up_b:up_b_at_ck" in \
        rc["unproven"]


def test_a_touched_table_without_a_row_is_incomplete_never_passed_silently(
        tmp_path, empty_db):
    r = _up(tmp_path, empty_db, dict(BASE, **{
        "004_x.sql": "ALTER TABLE up_x ADD COLUMN w int;\n"}))
    assert "up_x" in r["seeded"]["refusals"]
    assert r["representative"] == UP.INCOMPLETE
    assert r["unseeded_touched_tables"] == ["up_x"]


def test_the_repository_upgrades_from_its_previous_migration_set(
        tmp_path, empty_db):
    """Every migration but the newest, built by production's runner,
    seeded, then the full tree: PASSED, nothing lost, COMPATIBLE."""
    mig = BACKEND / "migrations"
    files = sorted(p.name for p in mig.glob("*.sql"))
    base = tmp_path / "base_mig"
    base.mkdir()
    for n in files[:-1]:
        shutil.copy2(mig / n, base / n)
    r = UP.run(dsn=empty_db, sha=REL, base_sha=PREV, base_backend=BACKEND,
               candidate_backend=BACKEND, base_migrations=base,
               candidate_migrations=mig)
    assert r["result"] == UP.PASSED, r["reasons"]
    assert r["new_migrations"] == [files[-1]]
    assert r["base"]["applied_count"] == len(files) - 1
    assert r["after"]["applied_count"] == len(files)
    assert r["seeded"]["coverage"] >= 0.7, r["seeded"]
    assert r["rows"]["lost"] == []
    assert r["rollback_compatibility"]["verdict"] == UP.COMPATIBLE, \
        r["rollback_compatibility"]


def test_the_capital_critical_step_as_written(tmp_path, empty_db):
    """capital-critical's step, run by bash as written from the repository
    root's backend/, base = the previous release commit 7fd4574e (git
    archive of its own tree and runner), candidate = this tree."""
    have = subprocess.run(["git", "-C", str(ROOT), "cat-file", "-e",
                           PREV + "^{commit}"], capture_output=True)
    if have.returncode != 0:
        pytest.skip("the previous release commit is not in this clone "
                    "(capital-critical checks out full history)")
    st = next(s for s in yaml.safe_load(CC_WF.read_text())["jobs"]["suite"][
        "steps"] if s.get("name", "").startswith("The upgrade-path receipt"))
    u = urlparse(empty_db)
    name = u.path.lstrip("/")
    script = st["run"]
    conn = "-h 127.0.0.1 -U postgres -d postgres"
    assert script.count(conn) == 2
    script = script.replace(conn, "-h %s -p %s -U %s -d postgres" % (
        u.hostname, u.port or 5432, u.username))
    # the scratch database is the fixture's (created empty, dropped by it)
    for a, b in (("DROP DATABASE IF EXISTS upgrade_path WITH (FORCE)",
                  "SELECT 1"),
                 ("DROP DATABASE IF EXISTS upgrade_path", "SELECT 1"),
                 ("CREATE DATABASE upgrade_path", "SELECT 1"),
                 ("postgresql://postgres:postgres@127.0.0.1:5432/upgrade_path",
                  empty_db)):
        assert a in script
        script = script.replace(a, b)
    assert name not in script.replace(empty_db, "")
    shim = tmp_path / "bin"
    shim.mkdir()
    # `python` is this interpreter with its packages (asyncpg), as the
    # job's setup-python interpreter has the image's lock
    (shim / "python").write_text('#!/bin/sh\nexec "%s" "$@"\n' %
                                 sys.executable)
    (shim / "python").chmod(0o755)
    out, summ = tmp_path / "gh_output", tmp_path / "summary"
    env = {"PATH": "%s:%s" % (shim, os.environ["PATH"]),
           "HOME": str(tmp_path), "RUNNER_TEMP": str(tmp_path),
           "GITHUB_OUTPUT": str(out), "GITHUB_STEP_SUMMARY": str(summ),
           "GITHUB_SHA": REL, "GITHUB_RUN_ID": "7", "GITHUB_RUN_ATTEMPT": "1",
           "UPGRADE_BASE": PREV}
    if u.password:
        env["PGPASSWORD"] = u.password
    r = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail",
                        "-c", script], cwd=ROOT, env=env, capture_output=True,
                       text=True, timeout=900)
    assert r.returncode == 0, (r.stdout[-2000:], r.stderr[-2000:])
    assert out.read_text().strip() == "receipt=%s" % (
        tmp_path / "upgrade-path-receipt.json")
    rec = json.loads((tmp_path / "upgrade-path-receipt.json").read_text())
    assert rec["base"]["sha"] == PREV
    assert rec["base"]["migrations_count"] == 223
    assert rec["result"] == UP.PASSED, rec["reasons"]
    assert "316_trader_live_game_display.sql" in rec["new_migrations"]
    assert rec["rollback_compatibility"]["verdict"] == UP.COMPATIBLE
    assert "upgrade path **PASSED**" in summ.read_text()
