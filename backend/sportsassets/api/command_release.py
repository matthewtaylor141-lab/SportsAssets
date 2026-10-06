"""RELEASE TRUTH: WHAT IS RUNNING, WHAT SCHEMA IT HAS, AND WHAT WAS ACCEPTED.

    GET /api/command/release

Read-only, COMMAND session auth (agents_core.require_read -> 401 without a
session), GET only. The database reads run inside ONE `BEGIN READ ONLY`
transaction with a bounded `statement_timeout`, each in its own savepoint,
so a missing table is reported UNAVAILABLE with its reason and never aborts
the other sections.

WHAT IT REPORTS, EACH WITH ITS SOURCE:

  api        the SHA this API process was built from -- RENDER_GIT_COMMIT,
             the variable every build stamp in this service already reads.
             Absent => UNAVAILABLE with that reason, never a guess.
  workers    the workers_boot row in ingestion_state (written by
             workers/all.py at every worker boot): commit_sha, boot time,
             venue-write lock. Absent => UNAVAILABLE.
  alignment  API vs workers: EXACT when both are 40-hex and equal, PREFIX_n
             when one side is shorter, MISALIGNED when they differ, UNKNOWN
             when either is missing.
  schema     max(version) of schema_migrations, and every migration numbered
             216..264 that exists in THIS build or in the database, with its
             applied_at (or NOT_APPLIED).
  receipts   the release receipts committed under sportsassets/
             release_receipts (written by tools/release_receipt.py), each
             re-hashed here: a receipt whose sha256 does not verify is shown
             as TAMPERED, not trusted. The full receipt is in `receipts.items`.

WHAT THIS MODULE CANNOT DO, BY CONSTRUCTION. It imports no order, venue,
execution, ledger, paper or funded module (tests/test_command_release_
authority.py walks its imports), issues only SELECTs inside a READ ONLY
transaction, writes nothing, and reads files only from its own package.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response

from .agents_core import _pool, require_read

router = APIRouter()

VERSION = "COMMAND_RELEASE_V1"
STATEMENT_TIMEOUT_MS = 3000
#: INCIDENT RELEASE: 216..264 (was 216..226), so the release check sees the
#: R30A and incident migrations (227, 229, 248, 249, 251, 260, 261, 264);
#: 216..265 since ADRIANA (migration 265); 216..301 since the R30 tails
#: (R30C exec 300, R30B agents 301 -- the streams' 233 / 234 renumbered into
#: the 300-309 block), and 216..302 since 302 carried the EDDIE -> ARCHER
#: rename (266) into 301's objects (it must sort after 301). In between:
#: the rename 266, paper mark freshness 270 and PAPER TURNAROUND 290.
#: 216..305 since the PAPER CAPITAL AUTHORITY (305: shadow counterfactuals
#: and the entry-refusal census); 216..306 since the P0 closeout (303
#: Xavier probability snapshots, 306 paper market-data telemetry; 304 unused).
#: 216..309 since the PAPER PROFITABILITY BIND (309: learned models, per-
#: entry all-in evaluations, explicit CASH decisions; 307 / 308 reserved by
#: parallel work, absent from this build); 216..310 since the CAPITAL
#: READINESS LAB (310: append-only RESEARCH / SHADOW_NO_AUTHORITY evidence);
#: 216..311 since the PAPER PROFITABILITY STACK (311: learned management /
#: exit cost and the counterfactual variant ledger).
#: Numbers inside the range that no migration uses (reserved stream
#: slots) are reported in `numbers_absent`, never hidden.
TRACKED_FROM, TRACKED_TO = 216, 311
RECEIPT_SCHEMA = "BETTOR_RELEASE_RECEIPT_V1"
_PKG = pathlib.Path(__file__).resolve().parents[1]
RECEIPT_DIR = _PKG / "release_receipts"
MIGRATIONS_DIR = _PKG.parent / "migrations"
_HEX = re.compile(r"^[0-9a-f]+$")


def _iso(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc).isoformat(timespec="seconds")
    return str(v)


def _unavailable(why, **extra):
    return {"status": "UNAVAILABLE", "why": why} | extra


# ── the API's own build ──────────────────────────────────────────────

def api_build() -> dict:
    sha = (os.environ.get("RENDER_GIT_COMMIT") or "").strip().lower()
    src = "env RENDER_GIT_COMMIT (set by Render for the running build)"
    if not sha:
        return _unavailable("RENDER_GIT_COMMIT is not set in this process",
                            source=src, sha=None)
    if not _HEX.match(sha):
        return _unavailable("RENDER_GIT_COMMIT is not hex", source=src,
                            sha=None, raw=sha[:64])
    return {"status": "OK", "sha": sha, "short": sha[:7], "source": src}


def alignment(a, w) -> dict:
    a, w = (a or "").lower(), (w or "").lower()
    if not a or not w or not _HEX.match(a) or not _HEX.match(w):
        return {"verdict": "UNKNOWN",
                "why": "API or worker SHA unavailable, so no comparison"}
    n = min(len(a), len(w))
    if n < 7:
        return {"verdict": "UNKNOWN", "why": "a SHA shorter than 7 characters"}
    same = a[:n] == w[:n]
    return {"verdict": "ALIGNED" if same else "MISALIGNED",
            "matched_how": "EXACT" if same and n == 40 else
            ("PREFIX_%d" % n if same else None)}


# ── receipts, verified ───────────────────────────────────────────────

def receipt_hash(doc: dict) -> str:
    body = {k: v for k, v in doc.items() if k != "receipt_sha256"}
    return hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False).encode("utf-8")).hexdigest()


def _summary(doc: dict, fname: str) -> dict:
    verified = doc.get("receipt_sha256") == receipt_hash(doc)
    crit = doc.get("critical") or {}
    return {
        "file": fname,
        "gate_id": doc.get("gate_id"), "candidate": doc.get("candidate"),
        "sha": doc.get("sha"), "base_sha": doc.get("base_sha"),
        "state": doc.get("state"), "acceptance": doc.get("acceptance"),
        "hash_verified": verified,
        "integrity": "VERIFIED" if verified else "TAMPERED",
        "receipt_sha256": doc.get("receipt_sha256"),
        "generated_at": doc.get("generated_at"),
        "supersedes": doc.get("supersedes"),
        "stages": doc.get("stages"),
        "blockers": doc.get("blockers"),
        "counts": (doc.get("gate") or {}).get("counts"),
        "new_regressions": doc.get("new_regressions"),
        "known_baseline_failures": len(doc.get("known_baseline_failures")
                                       or ()),
        "critical": {k: crit.get(k) for k in ("entries", "expanded_nodes",
                                              "passed")}
        | {"not_run": len(crit.get("not_run") or ()),
           "not_passed": len(crit.get("not_passed") or ())},
        "void_hits": doc.get("void_hits"),
        "migrations_new_vs_base": (doc.get("migrations") or {})
        .get("new_vs_base"),
        "migration_max_at_sha": (doc.get("migrations") or {}).get("max_at_sha"),
        "commit_guard": {k: (doc.get("commit_guard") or {}).get(k)
                         for k in ("status", "range", "commits")}
        | {"undeclared": len((doc.get("commit_guard") or {})
                             .get("undeclared") or ())},
        "deploy": doc.get("deploy"),
        "production_schema": doc.get("production_schema"),
        "production_readbacks": doc.get("production_readbacks"),
        "approver": doc.get("approver"),
        "github_ci": doc.get("github_ci"),
    }


def read_receipts(directory: pathlib.Path = RECEIPT_DIR) -> dict:
    if not directory.is_dir():
        return _unavailable("no release_receipts directory in this build",
                            path=str(directory), items=[])
    items, bad = [], []
    for p in sorted(directory.glob("*__*.json")):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            bad.append({"file": p.name, "why": type(exc).__name__})
            continue
        if not isinstance(doc, dict) or doc.get("schema") != RECEIPT_SCHEMA:
            bad.append({"file": p.name, "why": "NOT_A_RECEIPT"})
            continue
        items.append(_summary(doc, p.name))
    items.sort(key=lambda r: r.get("generated_at") or "")
    latest = {}
    for r in items:
        latest[r["gate_id"]] = r["file"]
    return {"status": "OK" if items else "EMPTY",
            "source": "files committed under sportsassets/release_receipts "
                      "(tools/release_receipt.py)",
            "items": items, "unreadable": bad, "latest_by_gate": latest}


# ── database reads ───────────────────────────────────────────────────

async def _section(conn, fn):
    try:
        async with conn.transaction():
            return await fn(conn)
    except Exception as exc:                                    # noqa: BLE001
        return _unavailable(type(exc).__name__)


async def workers_boot(conn) -> dict:
    async def fn(c):
        row = await c.fetchrow(
            "SELECT value FROM ingestion_state WHERE key = 'workers_boot'")
        if row is None:
            return _unavailable("no workers_boot row: no worker has booted "
                                "against this database since it was written")
        v = row["value"]
        v = json.loads(v) if isinstance(v, str) else (v or {})
        sha = str(v.get("commit_sha") or v.get("commit") or "").lower()
        return {"status": "OK" if _HEX.match(sha or "x") and sha else
                "UNAVAILABLE",
                "why": None if sha and _HEX.match(sha) else
                "the row carries no hex commit",
                "sha": sha or None, "short": (sha or "")[:7] or None,
                "boot_at": v.get("at"),
                "venue_writes": v.get("venue_writes"),
                "source": "ingestion_state.workers_boot (workers/all.py)"}
    return await _section(conn, fn)


def build_migrations() -> list:
    try:
        return sorted(p.name for p in MIGRATIONS_DIR.glob("*.sql"))
    except OSError:
        return []


def _num(name):
    m = re.match(r"^(\d+)_", name or "")
    return int(m.group(1)) if m else None


async def schema(conn) -> dict:
    files = build_migrations()

    async def fn(c):
        mx = await c.fetchrow(
            "SELECT max(version) AS v, count(*) AS n FROM schema_migrations")
        rows = await c.fetch(
            "SELECT version, applied_at FROM schema_migrations "
            "WHERE version >= $1 AND version < $2 ORDER BY version",
            "%03d" % TRACKED_FROM, "%03d" % (TRACKED_TO + 1))
        applied = {r["version"]: _iso(r["applied_at"]) for r in rows}
        names = sorted(set(applied) | {f for f in files
                                       if TRACKED_FROM <= (_num(f) or 0)
                                       <= TRACKED_TO})
        tracked = [{"version": n, "number": _num(n),
                    "in_this_build": n in files,
                    "applied_at": applied.get(n),
                    "status": "APPLIED" if n in applied else "NOT_APPLIED"}
                   for n in names]
        build_max = files[-1] if files else None
        return {"status": "OK", "max_version": mx["v"],
                "applied_count": mx["n"], "build_max": build_max,
                "build_ahead_of_database": [f for f in files
                                            if f > (mx["v"] or "")],
                "tracked_range": [TRACKED_FROM, TRACKED_TO],
                "tracked": tracked,
                "numbers_absent": [k for k in range(TRACKED_FROM,
                                                    TRACKED_TO + 1)
                                   if k not in {t["number"] for t in tracked}],
                "source": "schema_migrations (scripts/migrate.py) + this "
                          "build's migrations directory"}
    return await _section(conn, fn)


async def _read_only(fn):
    pool = await _pool()
    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True):
            await conn.execute("SET LOCAL statement_timeout = %d"
                               % STATEMENT_TIMEOUT_MS)
            return await fn(conn)


async def build_release(conn) -> dict:
    api = api_build()
    workers = await workers_boot(conn)
    sch = await schema(conn)
    receipts = read_receipts()
    running = api.get("sha")
    matching = [r for r in receipts.get("items") or ()
                if running and r.get("sha") and r["sha"].startswith(running)]
    return {
        "version": VERSION, "read_only": True,
        "generated_at": _iso(datetime.now(timezone.utc)),
        "generated_at_epoch": time.time(),
        "api": api, "workers": workers,
        "alignment": alignment(api.get("sha"), workers.get("sha")),
        "schema": sch,
        "receipts": receipts,
        "running_build_receipts": [r["file"] for r in matching],
        "running_build_gated": (
            None if not running else
            any(r["state"] == "GATED" and r["hash_verified"]
                for r in matching)),
    }


@router.get("/api/command/release", dependencies=[Depends(require_read)])
async def release_index(response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await _read_only(build_release)
    except HTTPException:
        raise
    except Exception as exc:                                    # noqa: BLE001
        raise HTTPException(status_code=503, detail={
            "reason": "RELEASE_READ_FAILED", "detail": type(exc).__name__})
