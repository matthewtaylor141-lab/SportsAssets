"""THE EVIDENCE STORE. Test and economic artifacts the application can read.

WHY THIS EXISTS. The command centre was rendering UNKNOWN for every test
result and every replay figure in production, with the reason stated:
`backend/Dockerfile` ships exactly one file out of `acceptance/` and the
suite XML and economics JSON are not in the image. UNKNOWN was honest,
but honest about an UNFINISHED INTEGRATION -- the evidence exists, it is
just not reachable from the process that has to display it. "The
artifact is not in the image" is a build decision, not a measurement.

SO THE ARTIFACTS GO WHERE THE RUN'S OWN EVIDENCE ALREADY GOES: Postgres.
Not into the image, for the same reason the 4.9 MB `acceptance/`
directory was deliberately kept out of it -- a production image is not
an evidence archive, and shipping one couples every evidence update to a
redeploy.

APPEND-ONLY, AND SUPERSEDED ROWS SURVIVE. Publishing a new version of an
artifact INSERTS; it never updates bytes in place. The previous row for
that name has `superseded_by` set to the new row's id and is otherwise
untouched, so "preserve superseded evidence with its replacement
identified" is a property of the schema rather than a habit. `history()`
returns the whole chain.

VERSIONED MEANS THREE THINGS TOGETHER, and all three are stored on every
row: the SOURCE COMMIT the artifact was produced at, the SHA-256 DIGEST
of its exact bytes, and the instant it was published. A figure on the
page can therefore name the commit it describes, and two artifacts that
disagree can be told apart by digest rather than by filename.

WHAT THE DIGEST PROVES, AND WHAT IT DOES NOT. It proves CONTENT
INTEGRITY: these bytes are the bytes that were published, unaltered
since. It proves NOTHING about where they came from. `source_sha` is
supplied by whoever ran the publisher; it is a RECORDED CLAIM, not a
verified fact, and someone can publish last week's XML against today's
commit without the store noticing. So attribution here is labelled
RECORDED PROVENANCE, never "verified".

VERIFIED provenance would need three things this store does not yet
have, and `attestation` is the field reserved for them:

    ci_run          the CI run id that produced the artifact
    checked_out_sha the commit that run actually checked out, as
                    reported by the runner rather than by a publisher
    artifact_ref    the association between that run and these bytes,
                    from the CI system rather than from the uploader

Until an attestation is present and checked, `provenance_class` is
RECORDED and the page says so. Calling it verified would assert a chain
of custody that does not exist.

WHAT IT REFUSES TO STORE. `scan_for_secrets()` runs on every publish and
rejects anything carrying a credential-shaped key or a PEM block. An
evidence-publishing path is a general-purpose way to move bytes from a
developer's disk into a database that a web process reads, which is
exactly the shape of an accidental credential leak, so the guard is at
the write and is not optional.

READS ARE SELECT-ONLY AND GO THROUGH `require_command` LIKE EVERYTHING
ELSE. This module holds the writer too, but nothing in the API imports
the writer -- `publish()` is reachable only from
`backend/tools/publish_evidence.py`, and a test asserts the API surface
does not call it.

Run:  python -m pytest backend/tests/test_bettor_evidence_store.py
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time

log = logging.getLogger(__name__)

STORE_VERSION = "BETTOR_EVIDENCE_STORE_V1"
TABLE = "bettor_evidence_artifact"

# Its own advisory key, distinct from the store's 930_930_093 and the
# journal's 930_930_094.
SCHEMA_LOCK_KEY = 930_930_095
SCHEMA_LOCK_TIMEOUT_MS = 10_000
SCHEMA_ATTEMPTS = 3

# A CEILING ON ONE ARTIFACT. The largest thing that legitimately belongs
# here is a full JUnit XML, which is ~300 KB. 4 MB is generous and still
# small enough that a mistaken publish of a binary cannot fill the disk
# the collector is writing its journal to.
MAX_BYTES = 4 * 1024 * 1024

DDL = """
CREATE TABLE IF NOT EXISTS bettor_evidence_artifact (
    id            BIGSERIAL        PRIMARY KEY,
    name          TEXT             NOT NULL,
    kind          TEXT             NOT NULL,
    source_sha    TEXT             NOT NULL,
    content_type  TEXT             NOT NULL,
    digest        TEXT             NOT NULL,
    size_bytes    INTEGER          NOT NULL,
    body          TEXT             NOT NULL,
    published_at  DOUBLE PRECISION NOT NULL,
    note          TEXT,
    attestation   JSONB,
    superseded_by BIGINT
);
CREATE INDEX IF NOT EXISTS bettor_evidence_artifact_name
    ON bettor_evidence_artifact (name, published_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS bettor_evidence_artifact_ident
    ON bettor_evidence_artifact (name, digest);
"""

REQUIRED_COLUMNS = ("id", "name", "kind", "source_sha", "content_type",
                    "digest", "size_bytes", "body", "published_at",
                    "note", "attestation", "superseded_by")

# What a name is allowed to look like. Deliberately narrow: a name is a
# logical key, not a path, so a traversal cannot be smuggled through it.
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")

# A SHA is 7 to 64 hex characters, or one of a few honest placeholders.
SHA_RE = re.compile(r"^[0-9a-f]{7,64}$")


def digest_of(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


P_RECORDED = "RECORDED"
P_ATTESTED = "ATTESTED"

ATTESTATION_FIELDS = ("ci_run", "checked_out_sha", "artifact_ref")


def provenance_class(row: dict) -> dict:
    """RECORDED or ATTESTED, and exactly what each one licenses.

    A digest establishes that the bytes are unchanged since publish. It
    establishes nothing about which commit was tested to produce them --
    `source_sha` is whatever the publisher passed. Those are different
    claims and collapsing them is how a page ends up asserting a chain
    of custody it never had.
    """
    att = (row or {}).get("attestation") or {}
    have = [f for f in ATTESTATION_FIELDS if att.get(f)]
    if len(have) == len(ATTESTATION_FIELDS):
        return {"class": P_ATTESTED, "attestation": att,
                "integrity": "sha256 of the stored bytes",
                "attribution": "the CI run that produced these bytes "
                               "reported the commit it checked out",
                "licenses": "attributing these results to that commit"}
    return {
        "class": P_RECORDED,
        "missing_attestation": [f for f in ATTESTATION_FIELDS
                                if f not in have],
        "integrity": "sha256 of the stored bytes -- these ARE the bytes "
                     "that were published, unaltered",
        "attribution": "source_sha was supplied by the publisher. Nothing "
                       "here checked that these bytes came from testing "
                       "that commit.",
        "licenses": "saying WHICH BYTES are on screen. NOT attributing "
                    "the results to a commit as a verified fact.",
    }


# ── the refusal at the write ─────────────────────────────────────────

_SECRET_KEYS = (
    "private_key", "privatekey", "secret_key", "secretkey", "api_key",
    "apikey", "access_token", "refresh_token", "client_secret",
    "password", "passwd", "authorization", "database_url", "redis_url",
    "admin_token", "desk_password", "vapid_private", "pmx_private",
    "pmus_secret", "aws_secret",
)
_PEM = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{20,}")

# A key-looking assignment with a long opaque value.
#
# THE BOUNDARY IS NOT `\b`, AND THAT MATTERS. `\b` does not fire across
# an underscore, because `_` is a word character -- so `\bsecret_key\b`
# does NOT match inside `PMUS_SECRET_KEY`, which is the literal name of
# a credential this repository actually holds. The first version of this
# guard had that bug and would have let exactly the real variable names
# through while passing a test built from tidier ones. The key is
# therefore matched as a substring of a surrounding identifier run.
_ASSIGN = re.compile(
    r"(?i)[A-Za-z0-9_]*(" + "|".join(_SECRET_KEYS) + r")[A-Za-z0-9_]*"
    r"\s*[:=]\s*[\"']?([A-Za-z0-9/+=._\-]{16,})")


def scan_for_secrets(body: str) -> list:
    """Credential-shaped content, by name. Returns reasons, not values.

    NEVER RETURNS THE MATCH. A guard that echoes what it found in order
    to explain itself has published the thing it was guarding, into a
    log this time instead of a table.
    """
    hits = []
    if _PEM.search(body):
        hits.append("a PEM private-key block")
    if _BEARER.search(body):
        hits.append("a bearer token")
    for m in _ASSIGN.finditer(body):
        hits.append("an assignment to %r with a long opaque value"
                    % m.group(1).lower())
    # Distinct reasons only; a file with forty of them is one problem.
    out = []
    for h in hits:
        if h not in out:
            out.append(h)
    return out


def validate(*, name, kind, source_sha, body, content_type) -> dict:
    """Everything that must be true BEFORE a byte reaches the database."""
    if not NAME_RE.match(str(name or "")):
        return {"ok": False, "why": "BAD_NAME",
                "detail": "a name is a logical key matching %s, not a path"
                          % NAME_RE.pattern}
    if not str(kind or "").strip():
        return {"ok": False, "why": "BAD_KIND",
                "detail": "every artifact declares what it is evidence OF"}
    sha = str(source_sha or "").strip().lower()
    if not SHA_RE.match(sha):
        return {"ok": False, "why": "BAD_SOURCE_SHA",
                "detail": "an artifact with no source commit cannot be "
                          "attributed to anything; 7-64 hex characters"}
    if not isinstance(body, str):
        return {"ok": False, "why": "BAD_BODY",
                "detail": "the body is text; binaries do not belong here"}
    size = len(body.encode("utf-8"))
    if size == 0:
        return {"ok": False, "why": "EMPTY",
                "detail": "an empty artifact is an absent artifact, and "
                          "absent renders UNKNOWN without being stored"}
    if size > MAX_BYTES:
        return {"ok": False, "why": "TOO_LARGE",
                "detail": "%d bytes exceeds the %d-byte ceiling"
                          % (size, MAX_BYTES)}
    leaks = scan_for_secrets(body)
    if leaks:
        return {"ok": False, "why": "REFUSED_CREDENTIAL_SHAPED",
                "detail": "refusing to store: " + "; ".join(leaks),
                "note": "the matched values are deliberately NOT echoed"}
    if not str(content_type or "").strip():
        return {"ok": False, "why": "BAD_CONTENT_TYPE"}
    return {"ok": True, "size_bytes": size, "digest": digest_of(body),
            "source_sha": sha}


# ── schema ───────────────────────────────────────────────────────────

async def ensure_schema(pool) -> dict:
    """Create and VERIFY. Same discipline as the journal's `open()`."""
    last_error = None
    for attempt in range(SCHEMA_ATTEMPTS):
        try:
            async with pool.acquire() as con:
                async with con.transaction():
                    await con.execute("SET LOCAL lock_timeout = '%dms'"
                                      % SCHEMA_LOCK_TIMEOUT_MS)
                    await con.execute("SELECT pg_advisory_xact_lock($1)",
                                      SCHEMA_LOCK_KEY)
                    await con.execute(DDL)
        except Exception as exc:                                # noqa: BLE001
            last_error = type(exc).__name__
            log.warning("bettor_evidence_store: schema attempt %d/%d "
                        "failed (%s)", attempt + 1, SCHEMA_ATTEMPTS,
                        last_error)
        try:
            cols = await pool.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = $1", TABLE)
            have = {r["column_name"] for r in cols}
            if set(REQUIRED_COLUMNS) <= have:
                return {"ok": True, "table": TABLE, "store": STORE_VERSION}
            last_error = "missing columns: %s" % sorted(
                set(REQUIRED_COLUMNS) - have)
        except Exception as exc:                                # noqa: BLE001
            last_error = type(exc).__name__
    return {"ok": False, "why": "EVIDENCE_SCHEMA_UNAVAILABLE",
            "detail": last_error}


# ── the write. NOT reachable from the API. ───────────────────────────

async def publish(pool, *, name, kind, source_sha, body,
                  content_type="text/plain", note=None,
                  attestation=None) -> dict:
    """Append one artifact version and supersede the previous one.

    IDEMPOTENT ON (name, digest). Re-publishing identical bytes returns
    the existing row rather than creating a second identical version,
    so a repeated CI step does not manufacture a version history that
    describes nothing.
    """
    v = validate(name=name, kind=kind, source_sha=source_sha, body=body,
                 content_type=content_type)
    if not v["ok"]:
        return v

    schema = await ensure_schema(pool)
    if not schema.get("ok"):
        return schema

    existing = await pool.fetchrow(
        "SELECT id, source_sha, published_at FROM " + TABLE +
        " WHERE name = $1 AND digest = $2", name, v["digest"])
    if existing:
        return {"ok": True, "unchanged": True, "id": existing["id"],
                "digest": v["digest"],
                "why": "these exact bytes are already stored under this "
                       "name; no new version was created"}

    async with pool.acquire() as con:
        async with con.transaction():
            row = await con.fetchrow(
                "INSERT INTO " + TABLE +
                " (name, kind, source_sha, content_type, digest,"
                "  size_bytes, body, published_at, note, attestation)"
                " VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb)"
                " RETURNING id",
                name, kind, v["source_sha"], content_type, v["digest"],
                v["size_bytes"], body, time.time(), note,
                json.dumps(attestation) if attestation else None)
            new_id = row["id"]
            # SUPERSEDE, do not delete. The old bytes stay readable and
            # now carry a pointer to what replaced them.
            await con.execute(
                "UPDATE " + TABLE + " SET superseded_by = $1"
                " WHERE name = $2 AND id <> $1 AND superseded_by IS NULL",
                new_id, name)
    return {"ok": True, "unchanged": False, "id": new_id,
            "digest": v["digest"], "size_bytes": v["size_bytes"],
            "source_sha": v["source_sha"]}


# ── the reads. SELECT only. ──────────────────────────────────────────

async def latest(pool, name: str) -> dict | None:
    """The current version of one artifact, or None."""
    try:
        row = await pool.fetchrow(
            "SELECT id, name, kind, source_sha, content_type, digest,"
            "       size_bytes, body, published_at, note, attestation,"
            "       superseded_by"
            " FROM " + TABLE +
            " WHERE name = $1 AND superseded_by IS NULL"
            " ORDER BY published_at DESC LIMIT 1", name)
    except Exception:                                           # noqa: BLE001
        raise
    return dict(row) if row else None


async def latest_many(pool, names: list) -> dict:
    """Every named artifact in one round trip. Missing names are absent."""
    if not names:
        return {}
    rows = await pool.fetch(
        "SELECT DISTINCT ON (name) id, name, kind, source_sha,"
        "       content_type, digest, size_bytes, body, published_at,"
        "       note, attestation, superseded_by"
        " FROM " + TABLE +
        " WHERE name = ANY($1::text[]) AND superseded_by IS NULL"
        " ORDER BY name, published_at DESC", list(names))
    return {r["name"]: dict(r) for r in rows}


async def history(pool, name: str, limit: int = 50) -> list:
    """Every version ever published under this name, newest first.

    The BODY IS NOT RETURNED here. A history is for auditing which
    commit produced which digest when, and returning fifty full XML
    documents to answer that would be a denial-of-service dressed as a
    feature.
    """
    rows = await pool.fetch(
        "SELECT id, name, kind, source_sha, content_type, digest,"
        "       size_bytes, published_at, note, attestation, superseded_by"
        " FROM " + TABLE +
        " WHERE name = $1 ORDER BY published_at DESC LIMIT $2",
        name, int(limit))
    return [dict(r) for r in rows]


async def inventory(pool) -> list:
    """What the store holds, without any bodies."""
    rows = await pool.fetch(
        "SELECT name, count(*) AS versions,"
        "       max(published_at) AS newest_at,"
        "       count(*) FILTER (WHERE superseded_by IS NULL) AS current"
        " FROM " + TABLE + " GROUP BY name ORDER BY name")
    return [dict(r) for r in rows]


# ── deployed identity ────────────────────────────────────────────────

IDENTITY_ENVS = ("RENDER_GIT_COMMIT", "GIT_COMMIT", "SOURCE_COMMIT",
                 "BETTOR_DEPLOYED_SHA")
BUILD_STAMP_PATHS = ("/app/BUILD_SHA", "BUILD_SHA")


def _read_stamp() -> tuple:
    for p in BUILD_STAMP_PATHS:
        try:
            with open(p, encoding="utf-8") as fh:
                val = fh.read().strip()
            if SHA_RE.match(val.lower()):
                return val.lower(), "build stamp %s" % p
        except Exception:                                       # noqa: BLE001
            continue
    return None, None


async def deployed_identity(pool=None) -> dict:
    """Which commit is ACTUALLY running, from every source that knows.

    THREE INDEPENDENT SOURCES, AND THE DISAGREEMENT IS THE POINT:

      1. the process environment, which the platform stamps;
      2. a build stamp baked into the image at build time;
      3. the newest source_sha in the evidence store, which says which
         commit last published evidence.

    (3) is NOT the running commit and is never reported as one -- a
    publish can happen from anywhere. It is carried so the page can say
    "the evidence on screen describes commit X, the process is running
    commit Y" when those differ, which is the case a single-source
    identity silently hides.

    It NEVER falls back to the working tree. A source checkout is not
    what is deployed, and guessing is how a dashboard ends up asserting
    a commit nobody shipped.
    """
    env_sha, env_src = None, None
    for var in IDENTITY_ENVS:
        val = (os.environ.get(var) or "").strip().lower()
        if SHA_RE.match(val):
            env_sha, env_src = val, "environment %s" % var
            break

    stamp_sha, stamp_src = _read_stamp()

    store_sha, store_at = None, None
    if pool is not None:
        try:
            row = await pool.fetchrow(
                "SELECT source_sha, published_at FROM " + TABLE +
                " ORDER BY published_at DESC LIMIT 1")
            if row:
                store_sha = row["source_sha"]
                store_at = row["published_at"]
        except Exception:                                       # noqa: BLE001
            store_sha, store_at = None, None

    running = env_sha or stamp_sha
    source = env_src or stamp_src
    agree = None
    if env_sha and stamp_sha:
        agree = (env_sha[:7] == stamp_sha[:7])

    return {
        "running_sha": running,
        "source": source,
        "why": None if running else
               "no deployment stamped a commit into this process's "
               "environment and no build stamp is present; the running "
               "SHA is UNKNOWN rather than guessed from the working "
               "tree, which is not what is deployed",
        "env_sha": env_sha,
        "build_stamp_sha": stamp_sha,
        "env_and_stamp_agree": agree,
        "evidence_published_from_sha": store_sha,
        "evidence_published_at": store_at,
        "evidence_matches_running": (
            None if not (store_sha and running)
            else store_sha[:7] == running[:7]),
        "note": "evidence_published_from_sha is the commit the ARTIFACTS "
                "describe. It is not the running commit and is never "
                "reported as one.",
    }


def describe() -> dict:
    return {
        "store": STORE_VERSION,
        "table": TABLE,
        "append_only": True,
        "superseded_rows": "kept, with superseded_by naming the replacement",
        "versioned_by": ["source_sha", "digest", "published_at"],
        "digest_proves": "content integrity only -- these are the bytes "
                         "that were published",
        "digest_does_not_prove": "that the bytes came from testing "
                                 "source_sha; that is a publisher claim",
        "provenance_classes": [P_RECORDED, P_ATTESTED],
        "attestation_fields": list(ATTESTATION_FIELDS),
        "idempotent_on": ["name", "digest"],
        "max_bytes": MAX_BYTES,
        "refuses": [
            "a name that is not a bare logical key",
            "an artifact with no source commit",
            "an empty artifact",
            "anything over the size ceiling",
            "credential-shaped content, by name, without echoing it",
        ],
        "identity_sources": list(IDENTITY_ENVS) + list(BUILD_STAMP_PATHS)
                            + ["evidence store source_sha (NOT the "
                               "running commit)"],
    }
