"""THE EVIDENCE STORE'S REFUSALS AND ITS VERSIONING.

Every fixture here is SYNTHETIC. The fake pool is a dictionary with a
SQL-shaped front door; it is not Postgres and it is not asserted to
behave like Postgres beyond the four statements this module issues.

Run:  python -m pytest backend/tests/test_bettor_evidence_store.py -q
"""
from __future__ import annotations

import asyncio
import json
import re

import pytest

from sportsassets import bettor_evidence_store as ES
from sportsassets.api import command_center as IO


# ── a fake pool. SYNTHETIC. ──────────────────────────────────────────

class FakePool:
    """Just enough Postgres to exercise publish/supersede/read."""

    def __init__(self):
        self.rows = []
        self._next = 1
        self.schema_ok = True

    # -- asyncpg surface ---------------------------------------------
    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return _Con(pool)

            async def __aexit__(self, *a):
                return False
        return _Ctx()

    async def execute(self, sql, *args):
        return "OK"

    async def fetch(self, sql, *args):
        if "information_schema.columns" in sql:
            if not self.schema_ok:
                return []
            return [{"column_name": c} for c in ES.REQUIRED_COLUMNS]
        if "DISTINCT ON (name)" in sql:
            names = set(args[0])
            out = {}
            for r in sorted(self.rows, key=lambda r: -r["published_at"]):
                if r["name"] in names and r["superseded_by"] is None:
                    out.setdefault(r["name"], r)
            return list(out.values())
        if "ORDER BY published_at DESC LIMIT $2" in sql:
            hits = [r for r in sorted(self.rows,
                                      key=lambda r: -r["published_at"])
                    if r["name"] == args[0]][:args[1]]
            # HONOUR THE PROJECTION. The real statement does not select
            # `body`; a fake that returns it anyway would let a test
            # assert something the production SQL does not do.
            cols = [c for c in ES.REQUIRED_COLUMNS
                    if re.search(r"\b%s\b" % c, sql.split("FROM")[0])]
            return [{c: r[c] for c in cols} for r in hits]
        if "GROUP BY name" in sql:
            names = sorted({r["name"] for r in self.rows})
            return [{"name": n,
                     "versions": sum(1 for r in self.rows if r["name"] == n),
                     "newest_at": max(r["published_at"] for r in self.rows
                                      if r["name"] == n),
                     "current": sum(1 for r in self.rows
                                    if r["name"] == n
                                    and r["superseded_by"] is None)}
                    for n in names]
        return []

    async def fetchrow(self, sql, *args):
        if "AND digest = $2" in sql:
            for r in self.rows:
                if r["name"] == args[0] and r["digest"] == args[1]:
                    return r
            return None
        if "superseded_by IS NULL" in sql and "LIMIT 1" in sql:
            cur = [r for r in self.rows
                   if r["name"] == args[0] and r["superseded_by"] is None]
            return max(cur, key=lambda r: r["published_at"]) if cur else None
        if "ORDER BY published_at DESC LIMIT 1" in sql:
            return (max(self.rows, key=lambda r: r["published_at"])
                    if self.rows else None)
        return None


class _Con:
    def __init__(self, pool):
        self.pool = pool

    def transaction(self):
        class _T:
            async def __aenter__(self_inner):
                return None

            async def __aexit__(self_inner, *a):
                return False
        return _T()

    async def execute(self, sql, *args):
        if "SET superseded_by" in sql:
            new_id, name = args[0], args[1]
            for r in self.pool.rows:
                if (r["name"] == name and r["id"] != new_id
                        and r["superseded_by"] is None):
                    r["superseded_by"] = new_id
        return "OK"

    async def fetchrow(self, sql, *args):
        if sql.strip().upper().startswith("INSERT"):
            rid = self.pool._next
            self.pool._next += 1
            self.pool.rows.append({
                "id": rid, "name": args[0], "kind": args[1],
                "source_sha": args[2], "content_type": args[3],
                "digest": args[4], "size_bytes": args[5], "body": args[6],
                "published_at": args[7], "note": args[8],
                "superseded_by": None})
            return {"id": rid}
        return None


def pub(pool, **kw):
    base = {"name": "release_tests.xml", "kind": "junit",
            "source_sha": "d630d3d", "body": "<testsuites/>",
            "content_type": "text/xml"}
    base.update(kw)
    return asyncio.run(ES.publish(pool, **base))


# ── refusals at the write ────────────────────────────────────────────

class TestRefusals:

    def test_a_name_may_not_be_a_path(self):
        for bad in ("../etc/passwd", "a/b.xml", "/abs.xml", "", "  "):
            got = pub(FakePool(), name=bad)
            assert got["ok"] is False
            assert got["why"] == "BAD_NAME"

    def test_an_artifact_needs_a_source_commit(self):
        for bad in ("", "HEAD", "not-a-sha", "zzzzzzz", "abc"):
            got = pub(FakePool(), source_sha=bad)
            assert got["ok"] is False
            assert got["why"] == "BAD_SOURCE_SHA"

    def test_an_empty_artifact_is_absent_not_stored(self):
        got = pub(FakePool(), body="")
        assert got["ok"] is False
        assert got["why"] == "EMPTY"

    def test_an_oversized_artifact_is_refused(self):
        got = pub(FakePool(), body="x" * (ES.MAX_BYTES + 1))
        assert got["ok"] is False
        assert got["why"] == "TOO_LARGE"

    def test_a_pem_private_key_is_refused(self):
        body = ("<testsuites>-----BEGIN RSA PRIVATE KEY-----\nAAAA\n"
                "-----END RSA PRIVATE KEY-----</testsuites>")
        got = pub(FakePool(), body=body)
        assert got["ok"] is False
        assert got["why"] == "REFUSED_CREDENTIAL_SHAPED"

    def test_a_bearer_token_is_refused(self):
        got = pub(FakePool(),
                  body="log: Authorization: Bearer "
                       "abcdefghijklmnopqrstuvwxyz0123456789")
        assert got["ok"] is False
        assert got["why"] == "REFUSED_CREDENTIAL_SHAPED"

    def test_a_credential_shaped_assignment_is_refused(self):
        for key in ("api_key", "PMUS_SECRET_KEY", "database_url",
                    "admin_token", "client_secret"):
            body = "%s = AKIAIOSFODNN7EXAMPLEKEYVALUE123" % key
            got = pub(FakePool(), body=body)
            assert got["ok"] is False, key
            assert got["why"] == "REFUSED_CREDENTIAL_SHAPED", key

    def test_the_guard_never_echoes_what_it_found(self):
        """A guard that quotes the secret to explain itself has
        published the secret, into a log instead of a table."""
        secret = "SUPERSECRETVALUE0123456789abcdef"
        got = pub(FakePool(), body="api_key = %s" % secret)
        assert secret not in json.dumps(got)
        assert "deliberately NOT echoed" in got["note"]

    def test_ordinary_junit_and_json_are_accepted(self):
        p = FakePool()
        assert pub(p, body="<testsuites><testsuite/></testsuites>")["ok"]
        assert pub(p, name="evaluation.json", kind="economics",
                   content_type="application/json",
                   body=json.dumps({"cut": "2026-09-17", "eval": []}))["ok"]


# ── versioning ───────────────────────────────────────────────────────

class TestVersioning:

    def test_republishing_identical_bytes_creates_no_new_version(self):
        p = FakePool()
        a = pub(p, body="<testsuites a='1'/>")
        b = pub(p, body="<testsuites a='1'/>")
        assert a["ok"] and b["ok"]
        assert b["unchanged"] is True
        assert b["id"] == a["id"]
        assert len(p.rows) == 1

    def test_new_bytes_supersede_the_old_row_without_deleting_it(self):
        p = FakePool()
        a = pub(p, body="<testsuites a='1'/>")
        b = pub(p, body="<testsuites a='2'/>", source_sha="4b83924")
        assert len(p.rows) == 2, "the old row must survive"
        old = [r for r in p.rows if r["id"] == a["id"]][0]
        assert old["superseded_by"] == b["id"]
        assert old["body"] == "<testsuites a='1'/>"

    def test_latest_returns_the_unsuperseded_row(self):
        p = FakePool()
        pub(p, body="<testsuites a='1'/>")
        pub(p, body="<testsuites a='2'/>", source_sha="4b83924")
        got = asyncio.run(ES.latest(p, "release_tests.xml"))
        assert got["body"] == "<testsuites a='2'/>"
        assert got["source_sha"] == "4b83924"

    def test_history_keeps_every_version_and_omits_bodies(self):
        p = FakePool()
        pub(p, body="<testsuites a='1'/>")
        pub(p, body="<testsuites a='2'/>", source_sha="4b83924")
        hist = asyncio.run(ES.history(p, "release_tests.xml"))
        assert len(hist) == 2
        assert all("body" not in r for r in hist)

    def test_every_row_carries_commit_digest_and_time(self):
        p = FakePool()
        pub(p, body="<testsuites/>")
        r = p.rows[0]
        assert r["source_sha"] == "d630d3d"
        assert r["digest"] == ES.digest_of("<testsuites/>")
        assert r["published_at"] > 0

    def test_a_different_sha_alone_is_still_the_same_bytes(self):
        """Publishing the same bytes from a different commit does not
        manufacture a version; the digest is the identity."""
        p = FakePool()
        pub(p, body="<testsuites/>", source_sha="d630d3d")
        again = pub(p, body="<testsuites/>", source_sha="4b83924")
        assert again["unchanged"] is True
        assert len(p.rows) == 1


# ── the command centre reads it ──────────────────────────────────────

def _store_hit(name, body, sha="4b83924", digest=None):
    return {"name": name, "kind": "junit", "source_sha": sha,
            "content_type": "text/xml",
            "digest": digest or ES.digest_of(body),
            "size_bytes": len(body), "body": body,
            "published_at": 1790159309.0, "note": None,
            "superseded_by": None}


class TestCommandCentreIntegration:

    XML = ('<?xml version="1.0"?><testsuites><testsuite name="pytest" '
           'errors="0" failures="1" skipped="0" tests="5" '
           'timestamp="2026-09-21T18:58:24+00:00">'
           '<testcase classname="t.A" name="bad">'
           '<failure message="boom">tb</failure></testcase>'
           '</testsuite></testsuites>')

    def test_a_stored_suite_is_parsed_identically_to_a_file(self, tmp_path):
        p = tmp_path / "s.xml"
        p.write_text(self.XML)
        from_file = IO.parse_junit(str(p), name="s", kind="UNIT",
                                   sha="abc", environment="ci")
        from_store = IO.parse_junit(None, body=self.XML, name="s",
                                    kind="UNIT", sha="abc",
                                    environment="ci",
                                    artifact_label="store")
        for k in ("complete", "total", "failure_ids"):
            assert from_file[k] == from_store[k]
        assert from_file["passed"]["value"] == from_store["passed"]["value"]
        assert from_file["failed"]["value"] == from_store["failed"]["value"]

    def test_the_store_sha_replaces_the_declared_one(self):
        store = {IO.SUITE_MANIFEST[0]["file"]:
                 _store_hit(IO.SUITE_MANIFEST[0]["file"], self.XML,
                            sha="4b83924")}
        rows = IO.load_suites(store)
        row = rows[0]
        assert row["tested_sha"] == "4b83924"
        assert row["provenance"]["source"] == IO.STORE_FIRST
        assert row["provenance"]["declared_sha"] == \
            IO.SUITE_MANIFEST[0]["sha"]
        assert row["provenance"]["sha_matches_declared"] is False

    def test_a_tree_read_is_labelled_as_unverified(self, monkeypatch,
                                                   tmp_path):
        monkeypatch.setenv("BETTOR_EVIDENCE_ROOT", str(tmp_path))
        rows = IO.load_suites({})
        for r in rows:
            assert r["provenance"]["source"] == IO.TREE_FALLBACK
            assert r["provenance"]["source_sha"] is None
            assert r["provenance"]["digest"] is None

    def test_absent_from_both_sources_is_genuinely_unavailable(
            self, monkeypatch, tmp_path):
        monkeypatch.setenv("BETTOR_EVIDENCE_ROOT", str(tmp_path))
        arts = IO.load_artifacts({})
        assert arts["present"] == {"evaluation": False, "opportunity": False,
                                   "manifest": False}
        why = arts["provenance"]["evaluation"]["why"]
        assert "not published to the evidence store" in why
        assert "not present on disk" in why

    def test_a_stored_economic_artifact_carries_its_commit(self):
        body = json.dumps({"cut": "2026-09-17T00:00:00+00:00", "eval": []})
        store = {"evaluation.json": dict(_store_hit("evaluation.json", body),
                                         kind="economics",
                                         content_type="application/json")}
        arts = IO.load_artifacts(store)
        assert arts["evaluation"]["cut"] == "2026-09-17T00:00:00+00:00"
        assert arts["provenance"]["evaluation"]["source"] == IO.STORE_FIRST
        assert arts["provenance"]["evaluation"]["source_sha"] == "4b83924"
        assert arts["from_store"] == ["evaluation"]

    def test_unparseable_stored_bytes_fall_back_rather_than_crash(
            self, monkeypatch, tmp_path):
        monkeypatch.setenv("BETTOR_EVIDENCE_ROOT", str(tmp_path))
        store = {"evaluation.json": _store_hit("evaluation.json",
                                               "{not json")}
        arts = IO.load_artifacts(store)
        assert arts["evaluation"] is None
        assert arts["present"]["evaluation"] is False

    def test_an_unreachable_store_is_not_an_error(self):
        class Boom:
            async def fetch(self, *a):
                raise OSError("no connection")
        got = asyncio.run(IO.read_store(Boom(), ["x"]))
        assert got == {"__error__": "OSError"}

    def test_no_pool_means_no_store_and_no_exception(self):
        assert asyncio.run(IO.read_store(None, ["x"])) == {}


# ── deployed identity ────────────────────────────────────────────────

class TestDeployedIdentity:

    def _clear(self, monkeypatch):
        for v in ES.IDENTITY_ENVS:
            monkeypatch.delenv(v, raising=False)
        monkeypatch.setattr(ES, "BUILD_STAMP_PATHS", ())

    def test_the_environment_answers_when_it_is_stamped(self, monkeypatch):
        self._clear(monkeypatch)
        monkeypatch.setenv("RENDER_GIT_COMMIT", "7f76fd90ad6862b7")
        got = asyncio.run(ES.deployed_identity(None))
        assert got["running_sha"] == "7f76fd90ad6862b7"
        assert "RENDER_GIT_COMMIT" in got["source"]

    def test_an_unstamped_process_is_unknown_not_the_working_tree(
            self, monkeypatch):
        self._clear(monkeypatch)
        got = asyncio.run(ES.deployed_identity(None))
        assert got["running_sha"] is None
        assert "not what is deployed" in got["why"]

    def test_a_junk_env_value_is_not_accepted_as_a_sha(self, monkeypatch):
        self._clear(monkeypatch)
        monkeypatch.setenv("RENDER_GIT_COMMIT", "not-a-real-sha")
        got = asyncio.run(ES.deployed_identity(None))
        assert got["running_sha"] is None

    def test_the_build_stamp_is_used_when_the_env_is_silent(
            self, monkeypatch, tmp_path):
        self._clear(monkeypatch)
        stamp = tmp_path / "BUILD_SHA"
        stamp.write_text("4b839247c1\n")
        monkeypatch.setattr(ES, "BUILD_STAMP_PATHS", (str(stamp),))
        got = asyncio.run(ES.deployed_identity(None))
        assert got["running_sha"] == "4b839247c1"
        assert "build stamp" in got["source"]

    def test_env_and_stamp_disagreement_is_reported_not_hidden(
            self, monkeypatch, tmp_path):
        self._clear(monkeypatch)
        stamp = tmp_path / "BUILD_SHA"
        stamp.write_text("aaaaaaa")
        monkeypatch.setattr(ES, "BUILD_STAMP_PATHS", (str(stamp),))
        monkeypatch.setenv("RENDER_GIT_COMMIT", "bbbbbbb")
        got = asyncio.run(ES.deployed_identity(None))
        assert got["env_and_stamp_agree"] is False
        assert got["running_sha"] == "bbbbbbb"

    def test_the_evidence_sha_is_never_reported_as_the_running_one(
            self, monkeypatch):
        self._clear(monkeypatch)
        p = FakePool()
        pub(p, body="<testsuites/>", source_sha="4b83924")
        got = asyncio.run(ES.deployed_identity(p))
        assert got["evidence_published_from_sha"] == "4b83924"
        assert got["running_sha"] is None
        assert "never reported as one" in got["note"]

    def test_evidence_and_running_mismatch_is_surfaced(self, monkeypatch):
        self._clear(monkeypatch)
        monkeypatch.setenv("RENDER_GIT_COMMIT", "7f76fd9")
        p = FakePool()
        pub(p, body="<testsuites/>", source_sha="4b83924")
        got = asyncio.run(ES.deployed_identity(p))
        assert got["evidence_matches_running"] is False


# ── the write path is not reachable from the API ─────────────────────

class TestWriteIsolation:

    def test_the_api_reader_never_calls_publish(self):
        src = open(IO.__file__, encoding="utf-8").read()
        assert "ES.publish" not in src
        assert "publish(" not in src

    def test_the_api_reader_issues_no_ddl_and_no_mutation(self):
        import ast

        tree = ast.parse(open(IO.__file__, encoding="utf-8").read())
        sql = [n.value for n in ast.walk(tree)
               if isinstance(n, ast.Constant) and isinstance(n.value, str)
               and any(v in n.value.upper() for v in
                       ("SELECT ", "INSERT ", "UPDATE ", "CREATE "))]
        for frag in sql:
            u = frag.upper()
            for verb in ("INSERT ", "UPDATE ", "DELETE ", "CREATE ",
                         "DROP ", "ALTER "):
                assert verb not in u, "%r in the reader: %s" % (verb, frag)

    def test_ensure_schema_is_not_called_by_the_reader(self):
        src = open(IO.__file__, encoding="utf-8").read()
        assert "ensure_schema" not in src
