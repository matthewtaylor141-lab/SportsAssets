"""THE UPGRADE-PATH RECEIPT: THIS RELEASE'S MIGRATIONS APPLIED TO A DATABASE
BUILT AT THE PREVIOUS RELEASE'S SCHEMA, WITH REPRESENTATIVE ROWS (RC6 lane E,
owner directive 2E).

Why. The fresh-database receipt (tools/fresh_db_receipt.py) proves that the
release builds an EMPTY database to production's fingerprint. Production is
never empty: it was built by the previous release and holds rows, and a
migration that is harmless on an empty table can fail on a populated one
(ADD COLUMN ... NOT NULL without a default, a new UNIQUE / CHECK over
existing data) or silently delete what it should keep. And the previous
release must be able to run again on the new schema, or a rollback is not
a rollback. Neither was checked before a deploy.

What `run` does (capital-critical, on the release's own tree, after the
fresh build, against an empty database of the job's PostgreSQL 16):

  1. builds the database with the BASE tree's own runner over the base
     tree's migrations (`sportsassets.scripts.migrate` imported from the
     base checkout, us_premap first exactly as the fresh build does);
  2. seeds REPRESENTATIVE ROWS: one synthesized row in every base table
     that can take one (NOT NULL columns filled by type, CHECK literals and
     bounds read from the constraint, foreign keys from the seeded parent;
     nullable columns left NULL, the adversarial case for a later NOT
     NULL); a table that cannot be seeded is named with the database's own
     refusal, never hidden;
  3. applies THIS tree's migrations with THIS tree's runner;
  4. checks: the runner exited 0; no applied migration changed (the runner
     reported none changed-after-apply, every base version is in this tree
     with the same content hash, the immutability manifest
     APPLIED_MIGRATIONS.sha256 keeps every base line byte for byte and
     every line matches its file); the database now holds exactly this
     tree's migrations (fingerprint == the fresh build's); no seeded row
     was lost and no table holding rows was dropped; every existing table
     a NEW migration touches held a row when it ran (otherwise the receipt
     is INCOMPLETE, not PASSED-with-a-hole);
  5. ROLLBACK COMPATIBILITY: compares the base schema with the upgraded
     one for everything that existed at the base (tables, columns and
     their types, NOT NULL / defaults, constraints, unique indexes,
     triggers) and re-runs the BASE runner on the upgraded database (the
     previous release's boot migration step on the new schema): COMPATIBLE
     only when nothing the base code uses was dropped, retyped or
     tightened and nothing new can refuse its writes; otherwise every
     blocking or unproven item is named.

It writes PASSED or FAILED, never nothing; capital-critical attests it.
`readback` (pm-acceptance, judge commit, standard library only) records
the receipt of the gate run with its provenance and attestation, like the
fresh-database readback; the scorecard binds it (tools/scorecard_14.py
upgrade_path / rollback_ready).

    python tools/upgrade_path_receipt.py run --dsn DSN --sha SHA
        --base-sha BASE --base-backend DIR [--candidate-backend DIR]
        [--run-id N --run-attempt N] --out R.json
    python3 -I tools/upgrade_path_receipt.py readback --run-id N
        --run RUN.json --receipt R.json --attestation-verified true
        --out acc/upgrade_path.json

It connects only to the DSN it is given (the job's own scratch database).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fresh_db_receipt as FD  # noqa: E402

VERSION = "UPGRADE_PATH_RECEIPT_V1"
READBACK_VERSION = "UPGRADE_PATH_READBACK_V1"
WORKFLOW = ".github/workflows/capital-critical.yml"
RUNNER = "python -m sportsassets.scripts.migrate"
MANIFEST = "APPLIED_MIGRATIONS.sha256"
PASSED, FAILED = "PASSED", "FAILED"
COMPLETE, INCOMPLETE = "COMPLETE", "INCOMPLETE"
COMPATIBLE, NOT_PROVEN = "COMPATIBLE", "NOT_PROVEN_COMPATIBLE"
_SHA = re.compile(r"^[0-9a-f]{40}$")
DRIFT_LINE = "WAS ALREADY APPLIED AND ITS CONTENT HAS SINCE CHANGED"

# ── why an upgrade path is not PASSED (evidence reasons, never refusals of
#    a trading path) ──────────────────────────────────────────────────────
R_NOT_A_FULL_SHA = "UPGRADE_NOT_A_FULL_SHA"
R_BASE_BUILD_FAILED = "UPGRADE_BASE_BUILD_FAILED"
R_BASE_TREE_UNREADABLE = "UPGRADE_BASE_TREE_UNREADABLE"
R_MIGRATION_FAILED = "UPGRADE_MIGRATION_FAILED"
R_RUNNER_REPORTED_DRIFT = "UPGRADE_RUNNER_REPORTED_CHANGED_AFTER_APPLY"
R_APPLIED_CHANGED = "UPGRADE_APPLIED_MIGRATION_CHANGED"
R_APPLIED_REMOVED = "UPGRADE_APPLIED_MIGRATION_REMOVED"
R_NOT_APPLIED = "UPGRADE_MIGRATION_NOT_APPLIED"
R_APPLIED_NOT_IN_TREE = "UPGRADE_APPLIED_NOT_IN_TREE"
R_FINGERPRINT_DIFFERS = "UPGRADE_FINGERPRINT_DIFFERS_FROM_FRESH_BUILD"
R_MANIFEST_LINE_EDITED = "UPGRADE_MANIFEST_LINE_EDITED"
R_MANIFEST_LINE_REMOVED = "UPGRADE_MANIFEST_LINE_REMOVED"
R_MANIFEST_REMOVED = "UPGRADE_MANIFEST_REMOVED"
R_MANIFEST_FILE_DIFFERS = "UPGRADE_MANIFEST_LINE_DIFFERS_FROM_FILE"
R_ROWS_LOST = "UPGRADE_ROWS_LOST"
R_TABLE_DROPPED_WITH_ROWS = "UPGRADE_TABLE_DROPPED_WITH_ROWS"
R_TOUCHED_TABLE_NOT_SEEDED = "UPGRADE_TOUCHED_TABLE_NOT_SEEDED"
# rollback compatibility (blocking = the base code certainly cannot run as
# it did; unproven = it might be refused)
R_TABLE_DROPPED = "ROLLBACK_TABLE_DROPPED"
R_COLUMN_DROPPED = "ROLLBACK_COLUMN_DROPPED"
R_COLUMN_RETYPED = "ROLLBACK_COLUMN_TYPE_CHANGED"
R_COLUMN_TIGHTENED = "ROLLBACK_COLUMN_NOT_NULL_WITHOUT_DEFAULT"
R_DEFAULT_DROPPED = "ROLLBACK_DEFAULT_DROPPED_ON_NOT_NULL_COLUMN"
R_NEW_REQUIRED_COLUMN = "ROLLBACK_NEW_NOT_NULL_COLUMN_WITHOUT_DEFAULT"
R_CONSTRAINT_ADDED = "ROLLBACK_CONSTRAINT_ADDED_ON_EXISTING_TABLE"
R_CONSTRAINT_CHANGED = "ROLLBACK_CONSTRAINT_CHANGED"
R_UNIQUE_INDEX_ADDED = "ROLLBACK_UNIQUE_INDEX_ADDED_ON_EXISTING_TABLE"
R_TRIGGER_ADDED = "ROLLBACK_TRIGGER_ADDED_ON_EXISTING_TABLE"
R_TRIGGER_CHANGED = "ROLLBACK_TRIGGER_CHANGED"
R_BASE_RUNNER_FAILED = "ROLLBACK_BASE_RUNNER_FAILED_ON_NEW_SCHEMA"
R_BASE_RUNNER_DRIFT = "ROLLBACK_BASE_RUNNER_REPORTS_CHANGED_AFTER_APPLY"
# the readback (pm-acceptance)
R_NO_GATE_RUN = "UPGRADE_NO_CAPITAL_CRITICAL_RUN_FOR_THE_SHA"
R_RECEIPT_NOT_IN_ARTIFACT = "UPGRADE_RECEIPT_NOT_IN_THE_RUN_ARTIFACT"
R_RECEIPT_NOT_JSON = "UPGRADE_RECEIPT_NOT_JSON"
R_RUN_CRASHED = "UPGRADE_RUN_CRASHED"

#: the runner of a tree, run FROM that tree over a given migrations
#: directory (production's own `main`; us_premap first, as the fresh build)
RUNNER_SHIM = r"""
import asyncio, pathlib, sys
from sportsassets.scripts import migrate as M
M.MIGRATIONS_DIR = pathlib.Path(sys.argv[1]).resolve()
async def run():
    from sportsassets import db
    if sys.argv[2] == "premap":
        from sportsassets.workers import premap
        await premap._ensure_table(await db.get_pool())
    await M.main()
    try:
        await db.close_pool()
    except Exception:
        pass
asyncio.run(run())
"""


# ── pure parts (unit-tested without a database) ───────────────────────────

def manifest_lines(directory) -> dict | None:
    """{file: sha} of a tree's APPLIED_MIGRATIONS.sha256, or None."""
    p = pathlib.Path(directory) / MANIFEST
    if not p.is_file():
        return None
    out = {}
    for ln in p.read_text().splitlines():
        if ln.strip() and not ln.startswith("#"):
            parts = ln.split()
            if len(parts) == 2:
                out[parts[1]] = parts[0]
    return out


def immutability(base_applied: dict, base_dir, cand_dir,
                 cand_tree: dict) -> list:
    """No applied migration changed between the base and this tree: every
    version the base database holds is in this tree with the same content
    hash; the manifest keeps every base line and every line matches its
    file."""
    out = []
    for v, h in sorted(base_applied.items()):
        if v not in cand_tree:
            out.append("%s:%s" % (R_APPLIED_REMOVED, v))
        elif h is not None and cand_tree[v] != h:
            out.append("%s:%s" % (R_APPLIED_CHANGED, v))
    bm, cm = manifest_lines(base_dir), manifest_lines(cand_dir)
    if bm is not None and cm is None:
        out.append(R_MANIFEST_REMOVED)
    for name, h in sorted((bm or {}).items()):
        if cm is not None and name not in cm:
            out.append("%s:%s" % (R_MANIFEST_LINE_REMOVED, name))
        elif cm is not None and cm[name] != h:
            out.append("%s:%s" % (R_MANIFEST_LINE_EDITED, name))
    for name, h in sorted((cm or {}).items()):
        if name in cand_tree and cand_tree[name] != h:
            out.append("%s:%s" % (R_MANIFEST_FILE_DIFFERS, name))
    return out


_TOUCH = re.compile(
    r"\b(?:ALTER\s+TABLE(?:\s+IF\s+EXISTS)?(?:\s+ONLY)?|UPDATE(?:\s+ONLY)?|"
    r"INSERT\s+INTO|DELETE\s+FROM(?:\s+ONLY)?|TRUNCATE(?:\s+TABLE)?(?:\s+ONLY)?|"
    r"DROP\s+TABLE(?:\s+IF\s+EXISTS)?|"
    r"CREATE\s+(?:UNIQUE\s+)?INDEX(?:\s+CONCURRENTLY)?(?:\s+IF\s+NOT\s+EXISTS)?"
    r"(?:\s+\w+)?\s+ON(?:\s+ONLY)?|"
    r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:CONSTRAINT\s+)?TRIGGER\s+\w+\s+"
    r"(?:BEFORE|AFTER|INSTEAD\s+OF)\s+[\w\s,]+?\s+ON)\s+"
    r"(?:public\.)?\"?([A-Za-z_][A-Za-z0-9_]*)\"?", re.I)


def touched_tables(sql: str) -> set:
    """Tables a migration's statements act on (comments stripped)."""
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    return {m.group(1).lower() for m in _TOUCH.finditer(sql)}


def compatibility(before: dict, after: dict) -> dict:
    """What the base code meets on the upgraded schema, for every object
    that existed at the base. snapshot = {table: {columns: {c: {type,
    notnull, default}}, constraints: {name: def}, unique_indexes: {name:
    def}, triggers: {name: def}}}."""
    blocking, unproven = [], []
    for t, b in sorted(before.items()):
        a = after.get(t)
        if a is None:
            blocking.append("%s:%s" % (R_TABLE_DROPPED, t))
            continue
        for c, bc in sorted(b["columns"].items()):
            ac = a["columns"].get(c)
            if ac is None:
                blocking.append("%s:%s.%s" % (R_COLUMN_DROPPED, t, c))
                continue
            if ac["type"] != bc["type"]:
                blocking.append("%s:%s.%s:%s->%s" % (
                    R_COLUMN_RETYPED, t, c, bc["type"], ac["type"]))
            if ac["notnull"] and not bc["notnull"] and not ac["default"]:
                blocking.append("%s:%s.%s" % (R_COLUMN_TIGHTENED, t, c))
            if ac["notnull"] and bc["default"] and not ac["default"]:
                blocking.append("%s:%s.%s" % (R_DEFAULT_DROPPED, t, c))
        for c, ac in sorted(a["columns"].items()):
            if c not in b["columns"] and ac["notnull"] and not ac["default"]:
                blocking.append("%s:%s.%s" % (R_NEW_REQUIRED_COLUMN, t, c))
        for kind, added, changed in (
                ("constraints", R_CONSTRAINT_ADDED, R_CONSTRAINT_CHANGED),
                ("unique_indexes", R_UNIQUE_INDEX_ADDED, R_CONSTRAINT_CHANGED),
                ("triggers", R_TRIGGER_ADDED, R_TRIGGER_CHANGED)):
            for name, d in sorted(a[kind].items()):
                if name not in b[kind]:
                    unproven.append("%s:%s:%s" % (added, t, name))
                elif b[kind][name] != d:
                    unproven.append("%s:%s:%s" % (changed, t, name))
    return {"verdict": COMPATIBLE if not blocking and not unproven
            else NOT_PROVEN, "blocking": blocking, "unproven": unproven}


def runner_lines(text: str) -> dict:
    """What a runner run said: the versions it applied and the
    changed-after-apply versions it warned about."""
    applied = re.findall(r"applying ([\w.-]+\.sql)", text or "")
    drift = re.findall(r"([\w.-]+\.sql) " + DRIFT_LINE, text or "")
    return {"applied": applied, "drift": drift}


# ── the database parts ────────────────────────────────────────────────────

def _runner(backend_dir, migrations_dir, dsn, premap: bool) -> dict:
    """Run a tree's own migration runner over a migrations directory."""
    env = dict(os.environ, DATABASE_URL=dsn, PYTHONPATH=str(backend_dir),
               PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run([sys.executable, "-c", RUNNER_SHIM,
                        str(migrations_dir), "premap" if premap else "-"],
                       cwd=str(backend_dir), env=env, capture_output=True,
                       text=True, timeout=1800)
    text = (r.stdout or "") + (r.stderr or "")
    return dict(runner_lines(text), rc=r.returncode,
                tail=text[-1500:] if r.returncode else "")


SNAPSHOT_SQL = r"""
SELECT c.relname AS t,
  (SELECT coalesce(json_object_agg(a.attname, json_build_object(
      'type', format_type(a.atttypid, a.atttypmod),
      'notnull', a.attnotnull,
      'default', (a.atthasdef OR a.attidentity <> '' OR a.attgenerated <> ''))),
      '{}'::json)
     FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attnum > 0
      AND NOT a.attisdropped) AS columns,
  (SELECT coalesce(json_object_agg(k.conname, pg_get_constraintdef(k.oid)),
      '{}'::json) FROM pg_constraint k WHERE k.conrelid = c.oid
      AND k.contype IN ('c', 'u', 'f', 'x', 'p')) AS constraints,
  (SELECT coalesce(json_object_agg(i.relname, pg_get_indexdef(x.indexrelid)),
      '{}'::json) FROM pg_index x JOIN pg_class i ON i.oid = x.indexrelid
     WHERE x.indrelid = c.oid AND x.indisunique
      AND NOT EXISTS (SELECT 1 FROM pg_constraint k
                      WHERE k.conindid = x.indexrelid)) AS unique_indexes,
  (SELECT coalesce(json_object_agg(g.tgname, pg_get_triggerdef(g.oid)),
      '{}'::json) FROM pg_trigger g WHERE g.tgrelid = c.oid
      AND NOT g.tgisinternal) AS triggers
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
  AND NOT c.relispartition
"""


async def snapshot(conn) -> dict:
    out = {}
    for r in await conn.fetch(SNAPSHOT_SQL):
        out[r["t"]] = {k: json.loads(r[k]) if isinstance(r[k], str) else
                       (r[k] or {}) for k in ("columns", "constraints",
                                              "unique_indexes", "triggers")}
    return out


async def applied_map(conn) -> dict:
    if not await conn.fetchval(
            "SELECT to_regclass('schema_migrations') IS NOT NULL"):
        return {}
    return {r["version"]: r["content_sha"] for r in await conn.fetch(
        "SELECT version, content_sha FROM schema_migrations")}


async def counts(conn, tables) -> dict:
    out = {}
    for t in sorted(tables):
        if await conn.fetchval("SELECT to_regclass($1) IS NOT NULL",
                               "public.%s" % t):
            out[t] = await conn.fetchval('SELECT count(*) FROM public."%s"'
                                         % t.replace('"', '""'))
        else:
            out[t] = None
    return out


# representative-row synthesis ------------------------------------------------

_LIT = re.compile(r"'((?:[^']|'')*)'::")
_NUM = re.compile(r"(?<![\w.'])(-?\d+(?:\.\d+)?)(?![\w.'])")
_CLASS_N = re.compile(r"'\^([A-Za-z0-9_:./-]*)\[([^\]]+)\]\{(\d+)\}\$'")
_LEN_N = re.compile(r"(?:char_)?length\(\w+\) = (\d+)")
_TEXT = ("text", "character varying", "character", "citext", "name")


def _q(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def candidates(col: dict, checks: list) -> list:
    """SQL expressions to try for one NOT NULL column, most specific first:
    literals and bounds from the CHECK constraints that name it, then a
    value of its type."""
    typ, base = col["type"], col["type"].split("(")[0].strip()
    out = []
    if col.get("enum"):
        out += [_q(x) for x in col["enum"]]
    for d in checks:
        if base in _TEXT or base.startswith("character"):
            for pre, cls, n in _CLASS_N.findall(d):
                ch = "a" if "a-f" in cls or "a-z" in cls else (
                    "0" if "0-9" in cls else cls[0])
                out.append(_q(pre + ch * int(n)))
            out += [_q("a" * int(n)) for n in _LEN_N.findall(d)]
            out += ["'%s'" % m for m in _LIT.findall(d)]
        elif base in ("integer", "bigint", "smallint", "numeric", "real",
                      "double precision") or base.startswith("numeric"):
            nums = sorted({float(n) for n in _NUM.findall(d)})
            out += ["%r" % ((a + b) / 2) for a, b in zip(nums, nums[1:])]
            for n in nums:
                out += ["%r" % n, "%r" % (n + 1), "%r" % (n - 1)]
        elif base in ("jsonb", "json"):
            keys = [m for m in _LIT.findall(d)
                    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", m)
                    and m not in ("array", "object", "string", "number",
                                  "boolean", "null")]
            if keys:
                out.append(_q(json.dumps({k: "seed" for k in keys})))
            if "'array'" in d:
                out.append("'[{}, {}]'")
    if typ.endswith("[]"):
        out.append("'{}'")
    elif base in ("boolean",):
        out += ["false", "true"]
    elif base in ("integer", "bigint", "smallint"):
        out += ["1", "0", "2"]
    elif base.startswith("numeric") or base in ("real", "double precision"):
        out += ["1", "0", "0.5", "2"]
    elif base in ("timestamp with time zone", "timestamp without time zone"):
        # ordered by column position: start-before-end checks hold as
        # declared; the reverse order is the alternative
        n = int(col.get("num") or 1)
        out += ["now() + interval '%d minutes'" % n,
                "now() - interval '%d minutes'" % n, "now()"]
    elif base == "date":
        out += ["current_date"]
    elif base.startswith("time"):
        out += ["'00:00'"]
    elif base == "interval":
        out += ["'1 second'"]
    elif base in ("jsonb", "json"):
        out += ["'{}'", "'[]'", "'\"seed\"'", "'1'"]
    elif base == "uuid":
        out += ["'00000000-0000-4000-8000-000000000001'"]
    elif base == "bytea":
        out += ["'\\x00'"]
    elif base in ("inet", "cidr"):
        out += ["'127.0.0.1'"]
    elif base == "tsvector":
        out += ["''"]
    else:
        out += ["'seed'", "'S'", "'1'"]
    seen, uniq = set(), []
    for x in out:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq[:24]


SEED_META_SQL = r"""
SELECT c.oid, c.relname,
  (SELECT json_agg(json_build_object('name', a.attname,
      'type', format_type(a.atttypid, a.atttypmod), 'num', a.attnum,
      'notnull', a.attnotnull,
      'auto', (a.atthasdef OR a.attidentity <> '' OR a.attgenerated <> ''),
      'enum', (SELECT json_agg(e.enumlabel ORDER BY e.enumsortorder)
               FROM pg_enum e WHERE e.enumtypid = a.atttypid))
      ORDER BY a.attnum)
     FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attnum > 0
      AND NOT a.attisdropped) AS cols,
  (SELECT json_agg(json_build_object('name', k.conname,
      'def', pg_get_constraintdef(k.oid), 'cols', k.conkey))
     FROM pg_constraint k WHERE k.conrelid = c.oid AND k.contype = 'c')
     AS checks,
  (SELECT json_agg(json_build_object('parent', p.relname, 'cols', k.conkey,
      'pcols', k.confkey))
     FROM pg_constraint k JOIN pg_class p ON p.oid = k.confrelid
     WHERE k.conrelid = c.oid AND k.contype = 'f') AS fks
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
  AND NOT c.relispartition
ORDER BY c.relname
"""


def _order(meta: dict) -> list:
    """Tables parents first (foreign keys), cycles broken by name."""
    done, out = set(), []

    def visit(t, stack):
        if t in done or t in stack or t not in meta:
            return
        stack.add(t)
        for fk in meta[t]["fks"]:
            if fk["parent"] != t:
                visit(fk["parent"], stack)
        stack.discard(t)
        done.add(t)
        out.append(t)
    for t in sorted(meta):
        visit(t, set())
    return out


async def seed(conn, *, attempts: int = 40) -> dict:
    """One representative row in every table that can take one. Returns
    {inserted: [...], preexisting: [...], failed: {table: refusal}}."""
    import asyncpg
    meta = {}
    for r in await conn.fetch(SEED_META_SQL):
        meta[r["relname"]] = {
            "cols": json.loads(r["cols"]) if r["cols"] else [],
            "checks": json.loads(r["checks"]) if r["checks"] else [],
            "fks": json.loads(r["fks"]) if r["fks"] else []}
    inserted, pre, failed = [], [], {}
    await conn.execute("SET statement_timeout = '10s'")
    for t in _order(meta):
        m = meta[t]
        qt = '"%s"' % t.replace('"', '""')
        if await conn.fetchval("SELECT EXISTS (SELECT 1 FROM public.%s)" % qt):
            pre.append(t)
            continue
        bynum = {c["num"]: c for c in m["cols"]}
        fixed, skip = {}, None
        for fk in m["fks"]:
            kids = [bynum[n] for n in fk["cols"] if n in bynum]
            if not any(k["notnull"] for k in kids) or fk["parent"] == t:
                continue
            if fk["parent"] not in inserted and fk["parent"] not in pre:
                skip = "PARENT_NOT_SEEDED:%s" % fk["parent"]
                break
            pm = {c["num"]: c["name"] for c in meta[fk["parent"]]["cols"]}
            names = [pm[n] for n in fk["pcols"]]
            row = await conn.fetchrow("SELECT %s FROM public.\"%s\" LIMIT 1" % (
                ", ".join('"%s"::text' % n for n in names), fk["parent"]))
            for k, n in zip(kids, names):
                fixed[k["name"]] = None if row[n] is None else _q(str(row[n]))
        if skip:
            failed[t] = skip
            continue
        need = [c for c in m["cols"] if c["notnull"] and not c["auto"]
                and c["name"] not in fixed]
        cands = {c["name"]: candidates(c, [k["def"] for k in m["checks"]
                                           if c["num"] in (k["cols"] or [])])
                 for c in need}
        idx = {c["name"]: 0 for c in need}
        types = {c["name"]: c["type"] for c in m["cols"]}
        last = None
        for _ in range(attempts):
            vals = dict(fixed)
            vals.update({n: cands[n][idx[n]] for n in idx})
            cols = sorted(vals)
            sql = "INSERT INTO public.%s (%s) VALUES (%s)" % (
                qt, ", ".join('"%s"' % c for c in cols),
                ", ".join("CAST(%s AS %s)" % (vals[c], types[c])
                          if vals[c] is not None else "NULL" for c in cols)) \
                if cols else "INSERT INTO public.%s DEFAULT VALUES" % qt
            try:
                async with conn.transaction():
                    await conn.execute(sql)
                inserted.append(t)
                last = None
                break
            except asyncpg.CheckViolationError as exc:
                last = "CHECK:%s" % exc.constraint_name
                k = next((k for k in m["checks"]
                          if k["name"] == exc.constraint_name), None)
                names = [bynum[n]["name"] for n in (k or {}).get("cols") or []
                         if n in bynum and bynum[n]["name"] in idx]
                moved = False
                for n in names:
                    if idx[n] + 1 < len(cands[n]):
                        idx[n] += 1
                        moved = True
                        break
                    idx[n] = 0
                if not moved:
                    break
            except asyncpg.NotNullViolationError as exc:
                last = "NOT_NULL:%s" % exc.column_name
                c = next((c for c in m["cols"]
                          if c["name"] == exc.column_name), None)
                if c is None or c["name"] in idx or c["name"] in fixed:
                    break
                cands[c["name"]] = candidates(c, [])
                idx[c["name"]] = 0
            except (asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
                last = "%s:%s" % (type(exc).__name__, str(exc)[:120])
                bad = [n for n in idx if n in str(exc)]
                if "invalid input" in str(exc) and idx:
                    # a literal the type refuses: next candidate everywhere
                    for n in idx:
                        if idx[n] + 1 < len(cands[n]):
                            idx[n] += 1
                    continue
                if bad and idx[bad[0]] + 1 < len(cands[bad[0]]):
                    idx[bad[0]] += 1
                    continue
                break
        if last is not None and t not in inserted:
            failed[t] = last
    await conn.execute("RESET statement_timeout")
    return {"inserted": inserted, "preexisting": pre, "failed": failed}


async def _connect(dsn):
    import asyncpg
    return await asyncpg.connect(dsn, timeout=30)


def run(*, dsn, sha, base_sha, base_backend, candidate_backend,
        base_migrations=None, candidate_migrations=None, run_id=None,
        run_attempt=None) -> dict:
    """The whole upgrade path against an EMPTY database at `dsn`."""
    import asyncio
    base_backend = pathlib.Path(base_backend)
    candidate_backend = pathlib.Path(candidate_backend)
    bmig = pathlib.Path(base_migrations or base_backend / "migrations")
    cmig = pathlib.Path(candidate_migrations or candidate_backend /
                        "migrations")
    reasons = []
    if not _SHA.match(str(sha or "")) or not _SHA.match(str(base_sha or "")):
        reasons.append(R_NOT_A_FULL_SHA)
    base_tree = FD.tree_migrations(bmig) if bmig.is_dir() else {}
    cand_tree = FD.tree_migrations(cmig) if cmig.is_dir() else {}
    out = {"version": VERSION, "sha": sha, "run_id": run_id,
           "run_attempt": run_attempt, "workflow": WORKFLOW,
           "runner": RUNNER, "server_version": None,
           "base": {"sha": base_sha, "migrations_count": len(base_tree),
                    "migrations_fingerprint": FD.fingerprint(base_tree)
                    if base_tree else None},
           "candidate": {"migrations_count": len(cand_tree),
                         "migrations_fingerprint": FD.fingerprint(
                             cand_tree)},
           "new_migrations": sorted(set(cand_tree) - set(base_tree))}
    if not base_tree:
        reasons.append(R_BASE_TREE_UNREADABLE)
        return dict(out, result=FAILED, reasons=reasons,
                    representative=INCOMPLETE, seeded=None,
                    rollback_compatibility=None)

    b = _runner(base_backend, bmig, dsn, premap=True)
    out["base"].update(build_rc=b["rc"], build_tail=b["tail"])

    async def phase1():
        c = await _connect(dsn)
        try:
            out["server_version"] = await c.fetchval("SHOW server_version")
            ba = await applied_map(c)
            before = await snapshot(c)
            s = await seed(c)
            rows = await counts(c, before)
            return ba, before, s, rows
        finally:
            await c.close()
    base_applied, before, seeded, rows_before = asyncio.run(phase1())
    out["base"]["applied_count"] = len(base_applied)
    if b["rc"] != 0 or set(base_applied) != set(base_tree):
        reasons.append("%s:rc=%s:applied=%d/%d" % (
            R_BASE_BUILD_FAILED, b["rc"], len(base_applied), len(base_tree)))
    with_rows = sorted(t for t, n in rows_before.items() if n)
    out["seeded"] = {
        "tables": len(before), "with_rows": len(with_rows),
        "inserted": len(seeded["inserted"]),
        "preexisting": len(seeded["preexisting"]),
        "not_seeded": len(seeded["failed"]),
        "coverage": round(len(with_rows) / len(before), 4) if before else None,
        "refusals": dict(sorted(seeded["failed"].items())[:60])}

    reasons += immutability(base_applied, bmig, cmig, cand_tree)
    c = _runner(candidate_backend, cmig, dsn, premap=False)
    out["candidate"].update(apply_rc=c["rc"], applied=c["applied"],
                            tail=c["tail"])
    if c["rc"] != 0:
        reasons.append("%s:%s" % (R_MIGRATION_FAILED, (c["applied"] or
                                                       ["?"])[-1]))
    reasons += ["%s:%s" % (R_RUNNER_REPORTED_DRIFT, v) for v in c["drift"]]

    async def phase2():
        c2 = await _connect(dsn)
        try:
            return (await applied_map(c2), await snapshot(c2),
                    await counts(c2, with_rows))
        finally:
            await c2.close()
    after_applied, after, rows_after = asyncio.run(phase2())
    missing = sorted(set(cand_tree) - set(after_applied))
    extra = sorted(set(after_applied) - set(cand_tree))
    reasons += ["%s:%s" % (R_NOT_APPLIED, v) for v in missing[:20]]
    reasons += ["%s:%s" % (R_APPLIED_NOT_IN_TREE, v) for v in extra[:20]]
    fp = FD.fingerprint({k: v for k, v in after_applied.items()})
    out["after"] = {"applied_count": len(after_applied), "fingerprint": fp,
                    "fresh_build_fingerprint": FD.fingerprint(cand_tree)}
    if fp != FD.fingerprint(cand_tree) and not missing and not extra:
        reasons.append(R_FINGERPRINT_DIFFERS)
    lost = []
    for t in with_rows:
        n0, n1 = rows_before[t], rows_after.get(t)
        if n1 is None:
            lost.append("%s:%s" % (R_TABLE_DROPPED_WITH_ROWS, t))
        elif n1 < n0:
            lost.append("%s:%s:%d->%d" % (R_ROWS_LOST, t, n0, n1))
    reasons += lost
    out["rows"] = {"tables_with_rows": len(with_rows), "lost": lost[:40]}

    touched = set()
    for v in out["new_migrations"]:
        touched |= touched_tables((cmig / v).read_text())
    touched_existing = sorted(t for t in touched if t in before)
    unseeded = sorted(t for t in touched_existing if not rows_before.get(t))
    out["touched_tables"] = touched_existing
    out["unseeded_touched_tables"] = unseeded
    out["representative"] = INCOMPLETE if unseeded else COMPLETE

    compat = compatibility(before, after)
    rb = _runner(base_backend, bmig, dsn, premap=False)
    compat["base_runner_on_new_schema"] = {"rc": rb["rc"],
                                           "applied": rb["applied"],
                                           "drift": rb["drift"]}
    if rb["rc"] != 0:
        compat["blocking"].append(R_BASE_RUNNER_FAILED)
    compat["blocking"] += ["%s:%s" % (R_BASE_RUNNER_DRIFT, v)
                           for v in rb["drift"]]
    if compat["blocking"] or compat["unproven"]:
        compat["verdict"] = NOT_PROVEN
    out["rollback_compatibility"] = compat
    return dict(out, result=PASSED if not reasons else FAILED,
                reasons=reasons)


def readback(*, run_id, run: dict | None, receipt_path,
             attestation_verified) -> dict:
    """acc/upgrade_path.json: the receipt of the gate run as downloaded,
    with where it came from. It decides nothing."""
    run = run if isinstance(run, dict) else {}
    receipt, reason, digest = None, None, None
    if not re.fullmatch(r"[0-9]{1,20}", str(run_id or "")):
        reason = R_NO_GATE_RUN
    elif not receipt_path or not pathlib.Path(receipt_path).is_file():
        reason = R_RECEIPT_NOT_IN_ARTIFACT
    else:
        raw = pathlib.Path(receipt_path).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        try:
            receipt = json.loads(raw)
        except ValueError:
            reason = R_RECEIPT_NOT_JSON
    return {"version": READBACK_VERSION, "reason": reason,
            "provenance": {
                "run_id": str(run_id) if run_id else None,
                "workflow_path": run.get("path"),
                "repository": (run.get("repository") or {}).get("full_name")
                if isinstance(run.get("repository"), dict) else None,
                "head_sha": run.get("head_sha"), "event": run.get("event"),
                "status": run.get("status"),
                "conclusion": run.get("conclusion"),
                "signer_workflow": WORKFLOW,
                "attestation_verified": attestation_verified is True,
                "receipt_sha256": digest if receipt is not None else None},
            "receipt": receipt}


def _json(path):
    try:
        return json.loads(pathlib.Path(path).read_text())
    except (OSError, ValueError, TypeError):
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="mode", required=True)
    r = sub.add_parser("run")
    r.add_argument("--dsn", required=True)
    r.add_argument("--sha", required=True)
    r.add_argument("--base-sha", required=True)
    r.add_argument("--base-backend", required=True)
    r.add_argument("--candidate-backend",
                   default=str(pathlib.Path(__file__).resolve().parents[1]))
    r.add_argument("--run-id")
    r.add_argument("--run-attempt")
    r.add_argument("--out", required=True)
    b = sub.add_parser("readback")
    b.add_argument("--run-id", default="")
    b.add_argument("--run", default="")
    b.add_argument("--receipt", default="")
    b.add_argument("--attestation-verified", default="false")
    b.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if a.mode == "run":
        try:
            out = run(dsn=a.dsn, sha=a.sha, base_sha=a.base_sha,
                      base_backend=a.base_backend,
                      candidate_backend=a.candidate_backend,
                      run_id=a.run_id, run_attempt=a.run_attempt)
        except Exception as exc:          # a crashed run is FAILED, named
            out = {"version": VERSION, "sha": a.sha, "run_id": a.run_id,
                   "run_attempt": a.run_attempt, "workflow": WORKFLOW,
                   "base": {"sha": a.base_sha}, "result": FAILED,
                   "representative": INCOMPLETE,
                   "rollback_compatibility": None,
                   "reasons": ["%s:%s:%s" % (
                       R_RUN_CRASHED, type(exc).__name__, str(exc)[:300])]}
        summary = {k: out.get(k) for k in ("sha", "result", "representative",
                                           "new_migrations", "seeded")}
        summary["base"] = {k: (out.get("base") or {}).get(k) for k in (
            "sha", "migrations_count", "applied_count")}
        summary["rollback"] = (out.get("rollback_compatibility") or {}).get(
            "verdict")
        summary["reasons"] = (out.get("reasons") or [])[:10]
    else:
        out = readback(run_id=a.run_id, run=_json(a.run) if a.run else None,
                       receipt_path=a.receipt or None,
                       attestation_verified=a.attestation_verified == "true")
        rec = out["receipt"] if isinstance(out["receipt"], dict) else {}
        summary = {"reason": out["reason"], "provenance": out["provenance"],
                   "result": rec.get("result"), "sha": rec.get("sha")}
    pathlib.Path(a.out).write_text(json.dumps(out, indent=1, sort_keys=True,
                                              default=str))
    print("upgrade path %s: %s" % (a.mode, json.dumps(summary, default=str)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
