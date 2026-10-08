"""CAPITAL-CRITICAL: THE RELEASE-TRUTH READ HAS NO AUTHORITY, AND TELLS THE TRUTH.

GET /api/command/release (api/command_release.py) reports the API build SHA,
the workers' boot SHA, schema_migrations 216-264 and the committed release
receipts. This proves the boundary the way test_command_floor_authority.py
does for the floor:

  §1 STATIC. The module imports only the standard library, FastAPI and the
     command read dependency (agents_core._pool / require_read) -- no order,
     venue, execution, ledger, paper, funded or submit module. Its SQL is
     SELECT-only; the only SET is the transaction-local statement timeout.
  §2 ROUTES. The route is GET only and answers 401 without a command session.
  §3 READ ONLY AT RUNTIME. The request path opens `BEGIN READ ONLY` with a
     bounded statement timeout; a write inside it is refused by Postgres.
  §4 TRUTH. A missing build stamp or boot row is UNAVAILABLE with its reason
     (never a guessed SHA); a 7-character match is PREFIX_7, not EXACT; a
     receipt whose hash does not verify is TAMPERED; the committed f31/f33
     receipts are served with their real states.
  §5 LISTED. This file is on the capital-critical list.
"""
from __future__ import annotations

import ast
import json
import os
import pathlib
import re
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "sportsassets" / "api" / "command_release.py"
DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

STDLIB = {"__future__", "hashlib", "json", "os", "pathlib", "re", "time",
          "datetime", "typing"}
ALLOWED = {"fastapi", "sportsassets.api.agents_core"}
FORBIDDEN = ("execmirror", "kalshi", "pmus", "venue", "clob", "executor",
             "execution", "ledger", "simulator", "bettor_funded", "funded",
             "paper_", "smalllive", "order", "submit", "live_",
             "actual_admission", "pinnapi", "slack_bridge", "xavier_policy",
             "derek_policy", "karen_runner", "intel.runner", "render")
WRITE = re.compile(r"\b(INSERT\s+INTO|UPDATE\s+[a-z_]+\s+SET|DELETE\s+FROM|"
                   r"TRUNCATE|ALTER\s+|DROP\s+|CREATE\s+|COPY\s+|GRANT\s+|"
                   r"SET\s+ROLE|SET\s+SESSION)", re.I)


def _imports() -> set:
    out = set()
    for node in ast.walk(ast.parse(SRC.read_text())):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = "sportsassets.api".split(".")
                base = base[:len(base) - (node.level - 1)]
                mod = ".".join(base + ([node.module] if node.module else []))
            else:
                mod = node.module
            out.add(mod)
            for a in node.names:
                out.add("%s.%s" % (mod, a.name))
    return out


# ── §1 static ────────────────────────────────────────────────────────

def test_the_release_read_imports_no_order_venue_ledger_or_paper_module():
    imps = _imports()
    for imp in imps:
        top = imp.split(".")[0]
        if top == "sportsassets":
            assert imp in {"sportsassets.api.agents_core",
                           "sportsassets.api.agents_core._pool",
                           "sportsassets.api.agents_core.require_read"}, imp
        else:
            assert top in STDLIB or top in ALLOWED, imp
        assert not any(f in imp.lower() for f in FORBIDDEN), imp
    assert "sportsassets.api.agents_core.require_read" in imps


def test_the_release_sql_is_select_only():
    sqls = [n.value for n in ast.walk(ast.parse(SRC.read_text()))
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and re.search(r"\b(SELECT|FROM)\b", n.value)]
    assert len(sqls) >= 3
    for s in sqls:
        assert not WRITE.search(s), s[:120]
    src = SRC.read_text()
    sets = re.findall(r"\bSET\s+[A-Z_]+[^\"']*", src)
    assert sets and all(x.startswith("SET LOCAL statement_timeout")
                        for x in sets), sets
    assert "transaction(readonly=True)" in src
    from sportsassets.api import command_release as R
    assert 0 < R.STATEMENT_TIMEOUT_MS <= 10000
    # it opens no file outside its own package directory
    assert "open(" not in src and "write_text" not in src


# ── §2 routes ────────────────────────────────────────────────────────

def test_the_route_is_get_only_and_requires_a_command_session():
    from sportsassets.api import app as APP

    paths, stack = {}, list(APP.app.routes)
    while stack:
        r = stack.pop()
        if hasattr(r, "original_router"):
            stack.extend(r.original_router.routes)
        elif getattr(r, "path", "").startswith("/api/command/release"):
            paths[r.path] = set(getattr(r, "methods", set()) or set())
    assert set(paths) == {"/api/command/release"}
    for p, methods in paths.items():
        assert methods <= {"GET", "HEAD"}, (p, methods)
    client = TestClient(APP.app, raise_server_exceptions=False)
    assert client.get("/api/command/release").status_code == 401
    assert client.post("/api/command/release").status_code in (401, 405)


# ── §3 read only at runtime ──────────────────────────────────────────

class _Pool:
    def __init__(self, conn):
        self.conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


@pg
async def test_the_request_path_is_a_read_only_transaction(monkeypatch):
    import asyncpg

    from sportsassets.api import command_release as R

    conn = await asyncpg.connect(DSN)
    try:
        async def pool():
            return _Pool(conn)

        monkeypatch.setattr(R, "_pool", pool)

        async def write(c):
            assert await c.fetchval("SHOW transaction_read_only") == "on"
            assert await c.fetchval(
                "SELECT setting::int FROM pg_settings "
                " WHERE name='statement_timeout'") == R.STATEMENT_TIMEOUT_MS
            await c.execute("INSERT INTO ingestion_state (key, value) "
                            "VALUES ('x_release_probe', '{}'::jsonb)")

        with pytest.raises(asyncpg.ReadOnlySQLTransactionError):
            await R._read_only(write)

        class Resp:
            headers: dict = {}
        out = await R.release_index(Resp())
        assert out["read_only"] is True
        assert out["schema"]["status"] == "OK"
        assert out["receipts"]["status"] == "OK"
    finally:
        await conn.close()


# ── §4 truth ─────────────────────────────────────────────────────────

def test_a_missing_build_stamp_is_unavailable_not_guessed(monkeypatch):
    from sportsassets.api import command_release as R
    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    a = R.api_build()
    assert a["status"] == "UNAVAILABLE" and a["sha"] is None
    assert "RENDER_GIT_COMMIT" in a["why"]
    monkeypatch.setenv("RENDER_GIT_COMMIT", "zz-not-hex")
    assert R.api_build()["status"] == "UNAVAILABLE"
    monkeypatch.setenv("RENDER_GIT_COMMIT", "FD6CC5B1390DC181BB34A8E441F3EF4FD2AC29AD")
    assert R.api_build()["sha"] == "fd6cc5b1390dc181bb34a8e441f3ef4fd2ac29ad"


def test_alignment_never_promotes_a_prefix():
    from sportsassets.api import command_release as R
    full = "fd6cc5b1390dc181bb34a8e441f3ef4fd2ac29ad"
    assert R.alignment(full, full) == {"verdict": "ALIGNED",
                                       "matched_how": "EXACT"}
    assert R.alignment(full, full[:7])["matched_how"] == "PREFIX_7"
    assert R.alignment(full, "8e62749")["verdict"] == "MISALIGNED"
    assert R.alignment(full, None)["verdict"] == "UNKNOWN"
    assert R.alignment(full, "fd6")["verdict"] == "UNKNOWN"


def test_a_tampered_receipt_is_shown_as_tampered(tmp_path):
    from sportsassets.api import command_release as R
    src = sorted((ROOT / "sportsassets" / "release_receipts").glob("f31__*.json"))[0]
    doc = json.loads(src.read_text())
    (tmp_path / src.name).write_text(json.dumps(doc))
    doc["state"] = "ACCEPTED"
    (tmp_path / "f31__000000000000.json").write_text(json.dumps(doc))
    out = R.read_receipts(tmp_path)
    by = {r["file"]: r for r in out["items"]}
    assert by[src.name]["integrity"] == "VERIFIED"
    assert by["f31__000000000000.json"]["integrity"] == "TAMPERED"
    assert R.read_receipts(tmp_path / "nope")["status"] == "UNAVAILABLE"


def test_the_committed_receipts_are_served_with_their_real_states():
    from sportsassets.api import command_release as R
    out = R.read_receipts()
    states = {r["gate_id"]: (r["state"], r["integrity"]) for r in out["items"]}
    assert states["f31"] == ("GATED", "VERIFIED")
    assert states["f33"] == ("VOID", "VERIFIED")
    f31 = next(r for r in out["items"] if r["gate_id"] == "f31")
    assert f31["acceptance"] == "GATED_PENDING_SIGNOFF"
    assert f31["new_regressions"] == []
    assert f31["commit_guard"]["status"] == "FAIL"


@pg
async def test_workers_and_schema_come_from_the_database(monkeypatch):
    import asyncpg

    from sportsassets.api import command_release as R

    sha = "8e627493cd40e7b63f0b57af23a84019c02b044b"
    monkeypatch.setenv("RENDER_GIT_COMMIT", sha)
    conn = await asyncpg.connect(DSN)
    tr = conn.transaction()
    await tr.start()
    try:
        await conn.execute("DELETE FROM ingestion_state WHERE key='workers_boot'")
        out = await R.build_release(conn)
        assert out["workers"]["status"] == "UNAVAILABLE"
        assert out["alignment"]["verdict"] == "UNKNOWN"
        await conn.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ('workers_boot', "
            "$1::jsonb)", json.dumps({"commit": sha[:7], "commit_sha": sha,
                                      "at": "2026-10-04T12:00:00+00:00",
                                      "venue_writes": "LOCKED"}))
        out = await R.build_release(conn)
        assert out["api"]["sha"] == sha and out["workers"]["sha"] == sha
        assert out["alignment"] == {"verdict": "ALIGNED",
                                    "matched_how": "EXACT"}
        sch = out["schema"]
        assert sch["status"] == "OK"
        tracked = {t["number"]: t for t in sch["tracked"]}
        for n in (216, 217, 218, 219, 220, 221):
            assert tracked[n]["status"] == "APPLIED", n
            assert tracked[n]["applied_at"]
        assert sch["max_version"] >= "221"
        assert 224 in sch["numbers_absent"] or tracked.get(224)
        # THE INCIDENT RELEASE'S MIGRATIONS ARE TRACKED (216..264), and the
        # reserved-but-unused stream slots are REPORTED ABSENT, not hidden:
        # this database carries every migration of this build, so the absent
        # numbers are exactly the ones no file in the build uses
        assert sch["tracked_range"] == [216, 315]
        for n in (248, 249, 251, 260, 261, 264, 265, 266, 270, 290, 300,
                  301, 302, 303, 305, 306, 309, 310, 311, 312, 313,
                  314, 315):
            assert tracked[n]["status"] == "APPLIED", n
            assert tracked[n]["in_this_build"] is True, n
        assert sch["numbers_absent"] == EXPECTED_ABSENT_216_315
        assert out["running_build_gated"] is False    # f33 is VOID
        assert out["running_build_receipts"]
    finally:
        await tr.rollback()
        await conn.close()


#: 216..264 with no migration file in this build: reserved stream slots the
#: streams did not use (the R30A brief's assignments: 228 NFL, 230 intent,
#: 231 chaos, 232-247 R30B/R30C/addendum/LAB, 250 router, 252 NFL
#: continuation, 253-259 control plane, 262 inc-sim, 263 inc-families).
EXPECTED_ABSENT_216_264 = ([228] + list(range(230, 248)) + [250]
                           + list(range(252, 260)) + [262, 263])
#: (R30 tails) 216..302: the above plus 266..299 less the numbers wave-1
#: lanes took (266 the EDDIE -> ARCHER rename, 270 paper mark freshness, 290
#: PAPER TURNAROUND), the slots between ADRIANA (265) and the R30 tails
#: block (300 R30C exec, 301 agents, 302 ARCHER across 301's objects)
EXPECTED_ABSENT_216_301 = EXPECTED_ABSENT_216_264 + [
    n for n in range(266, 300) if n not in (266, 270, 290)]
#: (305) PAPER CAPITAL AUTHORITY: 216..305, the above plus 303 and 304 (the
#: block's slots other wave work reserved; no file in this build)
EXPECTED_ABSENT_216_305 = EXPECTED_ABSENT_216_301 + [303, 304]
#: (P0 closeout) 216..306: 303 (Xavier probability snapshots) and 306 (paper
#: market-data telemetry) are present; only 304 is unused
EXPECTED_ABSENT_216_306 = EXPECTED_ABSENT_216_301 + [304]
#: (P1 profitability bind) 216..309: 309 present; 307 / 308 are reserved by
#: parallel work and absent from this build
EXPECTED_ABSENT_216_309 = EXPECTED_ABSENT_216_306 + [307, 308]
#: (CAPITAL READINESS LAB) 216..310: 310 present, nothing new absent
EXPECTED_ABSENT_216_310 = EXPECTED_ABSENT_216_309
#: (P1 profitability stack) 216..311: 310 and 311 present, nothing new absent
EXPECTED_ABSENT_216_311 = EXPECTED_ABSENT_216_310
#: (UNIVERSAL MARKET PLANE) 216..312: 312 present, nothing new absent
EXPECTED_ABSENT_216_312 = EXPECTED_ABSENT_216_311
#: (PERSISTED EXIT INTENT) 216..313: 313 present, nothing new absent
EXPECTED_ABSENT_216_313 = EXPECTED_ABSENT_216_312
#: (KALSHI CANONICAL VENUE) 216..314: 314 present, nothing new absent
EXPECTED_ABSENT_216_314 = EXPECTED_ABSENT_216_313
#: (RED TEAM CLOSEOUT) 216..315: 315 present, nothing new absent
EXPECTED_ABSENT_216_315 = EXPECTED_ABSENT_216_314


def test_the_tracked_range_covers_every_migration_in_this_build():
    """INCIDENT RELEASE: TRACKED_FROM / TRACKED_TO were 216 / 226, so the
    release check could not see migrations 227-264 at all. The range now
    reaches the build's highest migration, and the absent numbers inside it
    are exactly the unused reserved slots (30 of them)."""
    from sportsassets.api import command_release as R
    assert (R.TRACKED_FROM, R.TRACKED_TO) == (216, 315)
    nums = sorted(R._num(f) for f in R.build_migrations())
    assert nums[-1] == R.TRACKED_TO, "a migration above the tracked range"
    present = {n for n in nums if R.TRACKED_FROM <= n <= R.TRACKED_TO}
    absent = [k for k in range(R.TRACKED_FROM, R.TRACKED_TO + 1)
              if k not in present]
    assert absent == EXPECTED_ABSENT_216_315
    assert len(absent) == 30 + 31 + 1 + 2


# ── §5 listed ────────────────────────────────────────────────────────

def test_this_proof_is_capital_critical():
    listed = (ROOT / "tools" / "capital_critical_tests.txt").read_text()
    assert "tests/test_command_release_authority.py" in listed.splitlines()
