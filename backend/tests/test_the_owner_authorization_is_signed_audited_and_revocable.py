"""THE OWNER'S AUTHORIZATION: SIGNED THROUGH TWO FACTORS, AUDITED, BOUNDED,
REVOCABLE, AND VOID WHEN ITS SCOPE MOVES.

EVERY ACCOUNT, KEY, OPERATOR AND STATEMENT HERE IS SYNTHETIC TEST EVIDENCE.
The accounts are fixture rows in `bettor_desk_accounts`; the "factors" are
dicts saying which factors a route verified; the resolution key used by the
route tests is a literal test string, never a production value. Nothing here
authorises any real account, and no test sends an order.

WHAT IS PINNED:

  * THE WRITER accepts only a request that repeats the account id, states in
    at least 20 characters which account and venue it authorises, names the
    CURRENT approved effective digest (computed exactly as `authorize()`
    computes it), for the exact bound account and venue, with both factors
    verified and the operator the key authenticates; every other request is
    refused by its own name. An identical retry is ALREADY_RECORDED; a
    conflicting one while a record is active is refused; after revocation a
    new record is written.
  * THE AUDIT holds a row for every attempt -- accepted, idempotent, refused,
    raised -- and refuses UPDATE and DELETE.
  * REVOCATION marks the owner record AND the system authorization issued on
    it revoked, so `EX.authorize_submission` refuses at once.
  * INVALIDATION follows every scope write -- a different binding, any new
    limits proposal, an approval with a different digest -- and does NOT
    follow a write that leaves the scope as it was.
  * THE CONSUMER refuses revoked, invalidated, expired and unauthenticated
    records by name, AFTER the existing account/venue/limits refusals, whose
    order is unchanged.
  * THE ROUTES need the admin token (401), a configured resolution key (503)
    and the right one (401), and record the operator from settings.
  * THE ROUND TRIP: on a synthetic FUNDED-class account with every other gate
    as it is, the owner record is necessary and sufficient for the owner step,
    and the execution boundary still refuses (exposure / code constant).
"""
from __future__ import annotations

import json
import os
import time

import pytest

from sportsassets import bettor_desk_controls as CTL
from sportsassets import bettor_entry_execution as EX
from sportsassets import bettor_funded_activation as FA
from sportsassets import bettor_owner_authorization as OA
from sportsassets import config
from tests import test_funded_activation_is_a_real_path as ACT

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="RN1X_TEST_DSN is not set")

#: SYNTHETIC: a clean registry fixture account at the FUNDED-class venue PMUS.
ACCT = ACT.CLEAN
OTHER = "acct-pilot-test-002"
VENUE = "PMUS"
OPERATOR = "synthetic-test-owner"
AUTH = {"admin_token_verified": True, "resolution_key_verified": True,
        "operator": OPERATOR, "route": "test"}
STATEMENT = ("SYNTHETIC TEST: I authorise %s at %s under the approved limits"
             % (ACCT, VENUE))

KEYS = [FA.LIMITS_KEY, FA.ACCOUNT_KEY, FA.AUTHORIZATION_KEY,
        FA.OWNER_AUTH_KEY, "ext_pinnacle_last_cycle"]


# ════════════════════════════════════════════════════════════════════
# 1 · PURE
# ════════════════════════════════════════════════════════════════════

def test_the_scope_sha_binds_account_venue_and_digest():
    import hashlib
    want = hashlib.sha256(b"a-1|pmus|d" + b"0" * 63).hexdigest()
    assert OA.scope_sha("a-1", "PMUS", "d" + "0" * 63) == want
    # the venue is compared case-insensitively, account and digest are not
    assert OA.scope_sha("a-1", "pmus", "x") == OA.scope_sha("a-1", "PMUS", "x")
    assert OA.scope_sha("a-1", "PMUS", "x") != OA.scope_sha("a-2", "PMUS", "x")
    assert OA.scope_sha("a-1", "PMUS", "x") != OA.scope_sha("a-1", "PMUS", "y")


def _good_record(**over):
    now = time.time()
    rec = {"authorization_id": "foa-test", "account_id": ACCT, "venue": VENUE,
           "effective_digest": "d" * 64, "by": OPERATOR, "at": now,
           "statement": STATEMENT, "expires_at": now + 3600.0,
           "revoked": False, "invalidated": False,
           "authenticated_by": dict(AUTH),
           "scope_sha": OA.scope_sha(ACCT, VENUE, "d" * 64)}
    rec.update(over)
    return rec


def test_the_consumer_check_names_each_state_in_order():
    now = time.time()
    assert OA.consumer_check(_good_record(), now=now) == {"ok": True}
    # REVOKED FIRST, even when it is also invalidated, malformed and expired
    r = OA.consumer_check(_good_record(revoked=True, revoked_at=now,
                                       invalidated=True, expires_at=1.0,
                                       authenticated_by=None), now=now)
    assert r["refusal"] == FA.R_OWNER_AUTH_REVOKED
    # then INVALIDATED, before malformed and expired
    r = OA.consumer_check(_good_record(invalidated=True, expires_at=1.0,
                                       authorization_id=""), now=now)
    assert r["refusal"] == FA.R_OWNER_AUTH_INVALIDATED
    # then UNAUTHENTICATED, before expired
    for bad in ({"authorization_id": ""}, {"authenticated_by": None},
                {"authenticated_by": dict(AUTH,
                                          resolution_key_verified=False)},
                {"authenticated_by": dict(AUTH, operator="")},
                {"scope_sha": "0" * 64}, {"revoked": None},
                {"invalidated": None}, {"expires_at": None},
                {"expires_at": True}, {"expires_at": float("nan")},
                {"expires_at": float("inf")}, {"expires_at": "soon"}):
        r = OA.consumer_check(_good_record(**bad), now=now)
        assert r["refusal"] == FA.R_OWNER_AUTH_UNAUTHENTICATED, (bad, r)
    r = OA.consumer_check(_good_record(authorization_id="", expires_at=1.0),
                          now=now)
    assert r["refusal"] == FA.R_OWNER_AUTH_UNAUTHENTICATED
    # finally EXPIRED
    r = OA.consumer_check(_good_record(expires_at=now - 1.0), now=now)
    assert r["refusal"] == FA.R_OWNER_AUTH_EXPIRED
    # a record without a single writer field: the bare legacy fixture shape
    bare = {"account_id": ACCT, "venue": VENUE, "effective_digest": "d" * 64,
            "by": "owner", "at": 1.0, "statement": "x"}
    assert OA.consumer_check(bare, now=now)["refusal"] == \
        FA.R_OWNER_AUTH_UNAUTHENTICATED
    assert OA.status_of(None, now=now) == OA.S_ABSENT
    assert OA.status_of(bare, now=now) == OA.S_MALFORMED
    assert OA.status_of(_good_record(), now=now) == OA.S_ACTIVE


def test_describe_lists_every_owner_refusal():
    d = FA.describe()
    for r in (FA.R_OWNER_AUTH, FA.R_OWNER_AUTH_ACCOUNT, FA.R_OWNER_AUTH_VENUE,
              FA.R_OWNER_AUTH_LIMITS, FA.R_OWNER_AUTH_REVOKED,
              FA.R_OWNER_AUTH_INVALIDATED, FA.R_OWNER_AUTH_EXPIRED,
              FA.R_OWNER_AUTH_UNAUTHENTICATED, FA.R_OWNER_AUTH_CHANGED):
        assert r in d["refusals"], r
    assert len(set(OA.REFUSALS)) == len(OA.REFUSALS)
    assert OA.MAX_LIFETIME_DAYS <= 30.0 and OA.DEFAULT_LIFETIME_DAYS > 0
    assert OA.describe()["authorises_capital"] is False


def test_the_owner_credentials_are_reported_by_name_and_never_counted(
        monkeypatch):
    """Named in the posture with what refuses without them; not counted in
    `all_configured` (healthz), and no value, length or hash is returned."""
    class _S:
        admin_token = "a"
        desk_password = "b"
        operator_password = "c"
        funded_resolution_key = "super-secret-resolution-key-value"
        funded_resolution_operator = ""
    monkeypatch.setattr(config, "settings", lambda: _S())
    p = config.credential_posture()
    assert p["all_configured"] is True, "owner credentials must not count"
    assert set(p["owner_credentials"]) == {"funded_resolution_key",
                                           "funded_resolution_operator"}
    assert p["owner_not_configured"] == ["funded_resolution_operator"]
    assert "503" in p["owner_credentials"]["funded_resolution_key"][
        "if_absent"]
    for row in p["owner_credentials"].values():
        assert set(row) == {"configured", "state", "if_absent"}
    assert "super-secret" not in repr(p)
    assert "funded_resolution_key" not in config.CREDENTIAL_CONSEQUENCES


def test_no_credential_value_is_in_the_provisioning_document():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2]
    doc = (root / "research" / "RESOLUTION_CREDENTIAL_PROVISIONING.md")
    txt = doc.read_text()
    for must in ("FUNDED_RESOLUTION_KEY", "FUNDED_RESOLUTION_OPERATOR",
                 "deploy-api-commit", "autoDeploy", "env-set",
                 "resolution_key_configured", "401", "503", "Rotation"):
        assert must in txt, must
    env = (root / ".env.example").read_text()
    assert "FUNDED_RESOLUTION_KEY=\n" in env
    assert "FUNDED_RESOLUTION_OPERATOR=\n" in env


# ════════════════════════════════════════════════════════════════════
# 2 · THE ROUTES
# ════════════════════════════════════════════════════════════════════

class _Cfg:
    admin_token = "admin-secret-for-the-owner-auth-test"
    desk_password = "desk"
    operator_password = "operator"
    command_read_password = "desk"
    funded_resolution_key = ""
    funded_resolution_operator = ""


@pytest.fixture()
def client(monkeypatch):
    starlette = pytest.importorskip("starlette.testclient")
    from sportsassets.api import app as A
    cfg = _Cfg()
    monkeypatch.setattr(A, "settings", lambda: cfg, raising=False)
    return starlette.TestClient(A.app, raise_server_exceptions=False), cfg


ROUTES = ("/api/admin/funded-owner-authorization",
          "/api/admin/funded-owner-authorization/revoke")


def test_the_write_routes_need_both_factors(client):
    c, cfg = client
    admin = {"X-Admin-Token": cfg.admin_token}
    for route in ROUTES:
        assert c.post(route, json={}).status_code == 401
        r = c.post(route, json={}, headers=admin)
        assert r.status_code == 503, route
        assert r.json()["detail"]["reason"] == \
            "FUNDED_RESOLUTION_KEY_NOT_CONFIGURED"
    cfg.funded_resolution_key = "synthetic-test-resolution-key"
    for route in ROUTES:
        # a key with no operator bound to it is not configured either
        assert c.post(route, json={}, headers=admin).status_code == 503
    cfg.funded_resolution_operator = OPERATOR
    for route in ROUTES:
        r = c.post(route, json={}, headers=dict(
            admin, **{"X-Resolution-Key": "a-guess"}))
        assert r.status_code == 401, route
        # the key alone, without the admin token
        r = c.post(route, json={}, headers={
            "X-Resolution-Key": cfg.funded_resolution_key})
        assert r.status_code == 401, route
    # the read is admin-only
    assert c.get(ROUTES[0]).status_code == 401


def test_the_routes_take_the_operator_from_settings_not_the_body():
    import inspect

    from sportsassets.api import app as A
    for fn in (A.admin_record_funded_owner_authorization,
               A.admin_revoke_funded_owner_authorization):
        src = inspect.getsource(fn)
        assert "_owner_auth_factors(" in src
        assert 'operator=auth["operator"]' in src
        assert 'b.get("operator")' not in src and 'b.get("by")' not in src
    assert '"funded_resolution_operator"' in inspect.getsource(
        A._owner_auth_factors)
    # both write routes carry both dependencies; the read carries admin only
    for path, deps in (("/api/admin/funded-owner-authorization", 2),
                       ("/api/admin/funded-owner-authorization/revoke", 2)):
        route = next(r for r in A.app.routes
                     if getattr(r, "path", None) == path
                     and "POST" in getattr(r, "methods", ()))
        names = {d.call.__name__ for d in route.dependant.dependencies}
        assert {"require_admin", "require_resolution_key"} <= names, names
    get = next(r for r in A.app.routes
               if getattr(r, "path", None) == ROUTES[0]
               and "GET" in getattr(r, "methods", ()))
    assert {d.call.__name__ for d in get.dependant.dependencies} == \
        {"require_admin"}


# ════════════════════════════════════════════════════════════════════
# 3 · AGAINST A REAL DATABASE
# ════════════════════════════════════════════════════════════════════

async def _fresh(conn):
    await ACT._seed_accounts(conn)
    await conn.execute(
        "INSERT INTO bettor_desk_accounts (account_id, desk_id, status, "
        " opening_balance, opened_at, note, provenance, paused, "
        " accounting_status, accounting_detail) VALUES ($1,'desk-4','ACTIVE',"
        " 0, now(), 'a second synthetic test account', '{}'::jsonb, FALSE, "
        " 'CLEAN', '{}'::jsonb)", OTHER)
    await conn.execute("DELETE FROM ingestion_state WHERE key = ANY($1)",
                       KEYS + [ACT._ON.RECONCILIATION_KEY])


async def _bind(conn, account=ACCT, venue=VENUE):
    got = await CTL.set_account(conn, by="test", account={
        "account_id": account, "name": "synthetic", "venue": venue})
    assert got["ok"], got
    return got


async def _approve(conn, limits=None):
    """Approve the recorded set the way the admin route does."""
    raw = await conn.fetchval(
        "SELECT value FROM ingestion_state WHERE key=$1", FA.LIMITS_KEY)
    rec = json.loads(raw)
    eff = EX.effective_limits(FA.normalise_limit_keys(rec["proposed"]))
    rec.update(approved=True, approved_by="OWNER", enforced=True,
               effective_digest=eff["effective_digest"])

    async def _w():
        await conn.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1,$2::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value=$2::jsonb",
            FA.LIMITS_KEY, json.dumps(rec))
    moved = await OA.apply_scope_change(
        conn, reason=OA.SCOPE_LIMITS_APPROVED, write=_w, by="OWNER")
    assert moved["ok"], moved
    return eff["effective_digest"], moved


async def _limits(conn, limits=None):
    got = await CTL.set_limits(conn, by="test",
                               proposed=dict(limits or ACT._LIMITS))
    assert got["ok"], got
    return got


async def _scope(conn):
    await _bind(conn)
    await _limits(conn)
    digest, _ = await _approve(conn)
    return digest


async def _sign(conn, digest, **over):
    kw = dict(account_id=ACCT, venue=VENUE, effective_digest=digest,
              statement=STATEMENT, confirm=ACCT, operator=OPERATOR,
              auth=dict(AUTH))
    kw.update(over)
    return await OA.record_owner_authorization(conn, **kw)


async def _rows(conn, since):
    return [dict(r) for r in await conn.fetch(
        "SELECT * FROM bettor_funded_owner_authorization_audit "
        " WHERE audit_id > $1 ORDER BY audit_id", since)]


async def _mark(conn):
    return int(await conn.fetchval(
        "SELECT coalesce(max(audit_id), 0) FROM "
        "bettor_funded_owner_authorization_audit"))


async def _owner(conn):
    return FA._obj(await FA._state(conn, FA.OWNER_AUTH_KEY))


async def _system(conn):
    return FA._obj(await FA._state(conn, FA.AUTHORIZATION_KEY))


@pytest.fixture()
async def db():
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _fresh(conn)
        yield conn
    finally:
        await conn.execute("DELETE FROM ingestion_state WHERE key = ANY($1)",
                           KEYS + [ACT._ON.RECONCILIATION_KEY])
        await conn.execute("DELETE FROM external_valuations "
                           "WHERE experiment_id = $1", ACT.ext.EXPERIMENT_ID)
        await conn.close()


@pg
async def test_the_writer_accepts_the_exact_current_scope_and_audits_it(db):
    conn = db
    digest = await _scope(conn)
    mark = await _mark(conn)
    got = await _sign(conn, digest, lifetime_days=3)
    assert got["ok"] is True, got
    rec = await _owner(conn)
    assert rec["authorization_id"].startswith("foa-")
    assert rec["account_id"] == ACCT and rec["venue"] == VENUE
    assert rec["effective_digest"] == digest
    # the digest the writer bound is the one the consumer computes
    stored = FA._obj(await FA._state(conn, FA.LIMITS_KEY))
    assert digest == EX.effective_limits(FA.normalise_limit_keys(
        stored["proposed"]))["effective_digest"]
    assert rec["scope_sha"] == OA.scope_sha(ACCT, VENUE, digest)
    assert rec["revoked"] is False and rec["invalidated"] is False
    assert abs(rec["expires_at"] - rec["at"] - 3 * 86400.0) < 1e-6
    assert rec["by"] == OPERATOR
    # WHICH factors were verified -- never a factor
    assert rec["authenticated_by"] == AUTH
    rows = await _rows(conn, mark)
    assert [(r["action"], r["outcome"]) for r in rows] == [
        ("RECORD", "ACCEPTED")]
    assert rows[0]["audit_id"] == rec["audit_id"] == got["audit_id"]
    assert rows[0]["authorization_id"] == rec["authorization_id"]
    assert rows[0]["scope_sha"] == rec["scope_sha"]
    assert rows[0]["operator"] == OPERATOR
    # the default lifetime, when none is stated, is bounded
    assert OA._lifetime_days(None) == OA.DEFAULT_LIFETIME_DAYS


@pg
async def test_every_refusal_is_named_and_audited(db):
    conn = db
    # WITH NOTHING BOUND OR APPROVED
    mark = await _mark(conn)
    got = await _sign(conn, "d" * 64)
    assert got["refusal"] == OA.R_NO_BINDING, got
    await _bind(conn)
    assert (await _sign(conn, "d" * 64))["refusal"] == OA.R_LIMITS_MISSING
    await _limits(conn)
    assert (await _sign(conn, "d" * 64))["refusal"] == \
        OA.R_LIMITS_NOT_APPROVED
    digest, _ = await _approve(conn)
    cases = [
        (dict(auth=dict(AUTH, resolution_key_verified=False)),
         OA.R_BOTH_FACTORS),
        (dict(auth=dict(AUTH, admin_token_verified=False)),
         OA.R_BOTH_FACTORS),
        (dict(auth=None), OA.R_BOTH_FACTORS),
        (dict(operator=""), OA.R_NO_OPERATOR),
        (dict(operator="someone-else"), OA.R_OPERATOR_NOT_AUTHENTICATED),
        (dict(confirm="nope"), OA.R_CONFIRM),
        (dict(confirm=None), OA.R_CONFIRM),
        (dict(statement="too short"), OA.R_STATEMENT),
        (dict(statement="I authorise it all, every bit of it, " * 2),
         OA.R_STATEMENT_SCOPE),
        (dict(statement="I authorise %s everywhere, on any venue" % ACCT),
         OA.R_STATEMENT_SCOPE),
        (dict(venue="PMUS_TEST",
              statement="SYNTHETIC TEST: %s at PMUS_TEST ok" % ACCT),
         OA.R_NOT_FUNDED),
        (dict(lifetime_days=0), OA.R_LIFETIME),
        (dict(lifetime_days=31), OA.R_LIFETIME),
        (dict(lifetime_days=float("inf")), OA.R_LIFETIME),
        (dict(lifetime_days="forever"), OA.R_LIFETIME),
        (dict(lifetime_days=True), OA.R_LIFETIME),
        (dict(account_id=OTHER, confirm=OTHER,
              statement="SYNTHETIC TEST: %s at PMUS ok" % OTHER),
         OA.R_BINDING_ACCOUNT),
        (dict(venue="POLYMARKET",
              statement="SYNTHETIC TEST: %s at POLYMARKET" % ACCT),
         OA.R_BINDING_VENUE),
        (dict(effective_digest=""), OA.R_DIGEST_MISSING),
        (dict(effective_digest="0" * 64), OA.R_DIGEST_NOT_CURRENT),
    ]
    for over, want in cases:
        got = await _sign(conn, digest, **over)
        assert got["ok"] is False and got["refusal"] == want, (over, got)
        assert got["audit_id"]
    # an account the registry no longer knows, bound before it vanished
    await conn.execute("DELETE FROM bettor_desk_accounts WHERE account_id=$1",
                       ACCT)
    assert (await _sign(conn, digest))["refusal"] == OA.R_ACCOUNT_UNKNOWN
    rows = await _rows(conn, mark)
    # every attempt is a row, each REFUSED under the name it was refused by
    assert len(rows) == 3 + len(cases) + 1
    assert all(r["action"] == "RECORD" and r["outcome"] == "REFUSED"
               for r in rows)
    assert [r["refusal"] for r in rows][3:3 + len(cases)] == \
        [w for _, w in cases]
    assert await _owner(conn) is None, "no refusal wrote a record"


@pg
async def test_an_identical_retry_is_idempotent_and_a_conflict_is_refused(db):
    conn = db
    digest = await _scope(conn)
    first = await _sign(conn, digest)
    assert first["ok"], first
    mark = await _mark(conn)
    again = await _sign(conn, digest)
    assert again["ok"] is True and again["already"] is True
    assert again["record"]["authorization_id"] == \
        first["record"]["authorization_id"]
    # the SAME scope in DIFFERENT words is not the same signature
    other_words = await _sign(
        conn, digest, statement=STATEMENT + ", restated differently")
    assert other_words["refusal"] == OA.R_CONFLICTING_OWNER_AUTHORIZATION
    # a DIFFERENT scope while the record is active: the binding is moved
    # WITHOUT going through the writers that invalidate (as a direct write
    # would), so the old record is still active when the new scope is signed
    await conn.execute(
        "UPDATE ingestion_state SET value = $2::jsonb WHERE key = $1",
        FA.ACCOUNT_KEY, json.dumps({"account_id": OTHER, "venue": VENUE}))
    conflict = await _sign(conn, digest, account_id=OTHER, confirm=OTHER,
                           statement="SYNTHETIC TEST: %s at PMUS" % OTHER)
    assert conflict["refusal"] == OA.R_CONFLICTING_OWNER_AUTHORIZATION
    assert conflict["detail"]["active_authorization"]["account_id"] == ACCT
    rows = await _rows(conn, mark)
    assert [(r["outcome"], r["refusal"]) for r in rows] == [
        ("ALREADY_RECORDED", None),
        ("REFUSED", OA.R_CONFLICTING_OWNER_AUTHORIZATION),
        ("REFUSED", OA.R_CONFLICTING_OWNER_AUTHORIZATION)]
    assert (await _owner(conn))["authorization_id"] == \
        first["record"]["authorization_id"]
    # AFTER REVOCATION a new record is written for the new scope
    rv = await OA.revoke_owner_authorization(
        conn, authorization_id=first["record"]["authorization_id"],
        confirm=first["record"]["authorization_id"],
        reason="synthetic test: re-signing for another account",
        operator=OPERATOR, auth=dict(AUTH))
    assert rv["ok"], rv
    fresh = await _sign(conn, digest, account_id=OTHER, confirm=OTHER,
                        statement="SYNTHETIC TEST: %s at PMUS" % OTHER)
    assert fresh["ok"] is True, fresh
    assert fresh["record"]["authorization_id"] != \
        first["record"]["authorization_id"]
    assert fresh["superseded"] == {
        "authorization_id": first["record"]["authorization_id"],
        "status": OA.S_REVOKED}


@pg
async def test_the_audit_is_append_only(db):
    asyncpg = pytest.importorskip("asyncpg")
    conn = db
    digest = await _scope(conn)
    got = await _sign(conn, digest)
    aid = got["audit_id"]
    with pytest.raises(asyncpg.RaiseError):
        await conn.execute(
            "UPDATE bettor_funded_owner_authorization_audit SET outcome="
            "'REFUSED', refusal='X' WHERE audit_id=$1", aid)
    with pytest.raises(asyncpg.RaiseError):
        await conn.execute(
            "DELETE FROM bettor_funded_owner_authorization_audit "
            "WHERE audit_id=$1", aid)
    # AN ACCEPTED RECORD ROW CANNOT EXIST WITHOUT ITS SCOPE
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute(
            "INSERT INTO bettor_funded_owner_authorization_audit "
            "(action, outcome) VALUES ('RECORD', 'ACCEPTED')")


@pg
async def test_a_raise_rolls_back_and_is_audited_outside(db, monkeypatch):
    conn = db
    digest = await _scope(conn)
    real = OA._write

    async def _boom(c, key, value):
        if key == FA.OWNER_AUTH_KEY:
            raise RuntimeError("synthetic failure writing the record")
        return await real(c, key, value)
    monkeypatch.setattr(OA, "_write", _boom)
    mark = await _mark(conn)
    got = await _sign(conn, digest)
    assert got["ok"] is False and got["refusal"] == OA.R_RAISED
    assert got["rolled_back"] is True
    assert await _owner(conn) is None
    rows = await _rows(conn, mark)
    # the reserved-but-rolled-back ACCEPTED row is gone; the refusal stands
    assert [(r["outcome"], r["refusal"]) for r in rows] == [
        ("REFUSED", OA.R_RAISED)]
    assert "synthetic failure" in json.loads(rows[0]["effect"])["error"]


@pg
async def test_revocation_revokes_the_system_authorization_too(db):
    conn = db
    digest = await _scope(conn)
    await ACT._seed_evidence(conn, waived=False)
    signed = await _sign(conn, digest)
    aid = signed["record"]["authorization_id"]
    ok = await FA.authorize(conn, account_id=ACCT, venue=VENUE, by="test")
    assert ok["ok"] is True, ok
    sysrec = await _system(conn)
    assert sysrec["owner_authorization_id"] == aid
    assert sysrec["expires_at"] <= signed["record"]["expires_at"]
    before = EX.authorize_submission(
        account_id=ACCT, venue=VENUE, authorization=sysrec,
        approved_limits=ACT._LIMITS,
        account_exposure=ACT.MEASURED_EMPTY(ACCT), proposed_cost_usd=0.0)
    assert before["authorization_consumed"] is True
    assert before["refusal"] == EX.R_SUBMISSION_DISABLED

    # refusals first: wrong confirm, no reason, another id, one factor
    for kw, want in ((dict(confirm="x"), OA.R_REVOKE_CONFIRM),
                     (dict(reason=""), OA.R_REVOKE_REASON),
                     (dict(authorization_id="foa-other",
                           confirm="foa-other"), OA.R_NO_SUCH_AUTHORIZATION),
                     (dict(auth=dict(AUTH, admin_token_verified=False)),
                      OA.R_BOTH_FACTORS),
                     (dict(operator="someone-else"),
                      OA.R_OPERATOR_NOT_AUTHENTICATED)):
        args = dict(authorization_id=aid, confirm=aid,
                    reason="synthetic test revocation", operator=OPERATOR,
                    auth=dict(AUTH))
        args.update(kw)
        got = await OA.revoke_owner_authorization(conn, **args)
        assert got["refusal"] == want, (kw, got)
    assert (await _system(conn)).get("revoked") is False

    mark = await _mark(conn)
    rv = await OA.revoke_owner_authorization(
        conn, authorization_id=aid, confirm=aid,
        reason="synthetic test revocation", operator=OPERATOR,
        auth=dict(AUTH))
    assert rv["ok"] is True and rv["applied"] == {
        "owner_authorization_revoked": True,
        "system_authorization_revoked": True}
    owner = await _owner(conn)
    assert owner["revoked"] is True and owner["revoked_by"] == OPERATOR
    sysrec = await _system(conn)
    assert sysrec["revoked"] is True
    after = EX.authorize_submission(
        account_id=ACCT, venue=VENUE, authorization=sysrec,
        approved_limits=ACT._LIMITS,
        account_exposure=ACT.MEASURED_EMPTY(ACCT), proposed_cost_usd=0.0)
    assert after["ok"] is False and after["refusal"] == EX.R_AUTH_REVOKED
    # and the activation path refuses on the owner record by name
    again = await FA.authorize(conn, account_id=ACCT, venue=VENUE, by="test")
    assert again["refusal"] == FA.R_OWNER_AUTH_REVOKED
    # a retried revocation is idempotent and audited as such
    rv2 = await OA.revoke_owner_authorization(
        conn, authorization_id=aid, confirm=aid, reason="again",
        operator=OPERATOR, auth=dict(AUTH))
    assert rv2["ok"] is True and rv2["already"] is True
    rows = await _rows(conn, mark)
    assert [(r["action"], r["outcome"]) for r in rows] == [
        ("REVOKE", "ACCEPTED"), ("REVOKE", "ALREADY_REVOKED")]


@pg
async def test_each_scope_change_invalidates_and_an_unchanged_one_does_not(
        db, client, monkeypatch):
    conn = db
    digest = await _scope(conn)
    await ACT._seed_evidence(conn, waived=False)

    async def _signed_and_authorized():
        # the next signature needs no active record in the way
        cur = await _owner(conn)
        if cur and OA.status_of(cur, now=time.time()) == OA.S_ACTIVE:
            await OA.revoke_owner_authorization(
                conn, authorization_id=cur["authorization_id"],
                confirm=cur["authorization_id"], reason="reset",
                operator=OPERATOR, auth=dict(AUTH))
        d = (await OA.current_scope(conn))["effective_digest"]
        s = await _sign(conn, d)
        assert s["ok"], s
        a = await FA.authorize(conn, account_id=ACCT, venue=VENUE, by="t")
        assert a["ok"], a
        return s["record"]["authorization_id"]

    # ── UNCHANGED: the same account and venue bound again ─────────────
    aid = await _signed_and_authorized()
    mark = await _mark(conn)
    same = await _bind(conn)
    assert same["owner_authorization"]["invalidated"] is False
    assert (await _owner(conn))["invalidated"] is False
    assert await _rows(conn, mark) == []
    # ── UNCHANGED: the same digest approved again ──────────────────────
    _, moved = await _approve(conn)
    assert moved["owner_authorization"]["invalidated"] is False
    assert (await _system(conn)).get("revoked") is False
    assert await _rows(conn, mark) == []

    # ── CHANGED: a different account bound ─────────────────────────────
    await ACT._record_reconciliation_evidence(conn, account_id=OTHER)
    moved = await _bind(conn, account=OTHER)
    inv = moved["owner_authorization"]
    assert inv["invalidated"] is True and inv["authorization_id"] == aid
    assert inv["differs_on"] == ["account_id"]
    owner = await _owner(conn)
    assert owner["invalidated"] is True
    assert owner["invalidated_reason"] == OA.SCOPE_ACCOUNT_BINDING
    assert (await _system(conn))["revoked"] is True
    rows = await _rows(conn, mark)
    assert [(r["action"], r["outcome"], r["reason"]) for r in rows] == [
        ("INVALIDATE", "ACCEPTED", OA.SCOPE_ACCOUNT_BINDING)]
    # IDEMPOTENT: asked again, nothing further happens or is audited
    again = await OA.invalidate_owner_authorization_if_scope_changed(
        conn, reason="again")
    assert again["invalidated"] is False
    assert len(await _rows(conn, mark)) == 1
    # and the consumer names it
    await _bind(conn)                      # back to ACCT; still invalidated
    await ACT._record_reconciliation_evidence(conn, account_id=ACCT)
    r = await FA.authorize(conn, account_id=ACCT, venue=VENUE, by="t")
    assert r["refusal"] == FA.R_OWNER_AUTH_INVALIDATED

    # ── CHANGED: a new limits proposal (it resets approval) ────────────
    aid = None
    await _approve(conn)
    aid = await _signed_and_authorized()
    mark = await _mark(conn)
    got = await _limits(conn)
    assert got["owner_authorization"]["invalidated"] is True
    assert (await _owner(conn))["invalidated_reason"] == \
        OA.SCOPE_LIMITS_PROPOSED
    assert (await _system(conn))["revoked"] is True
    rows = await _rows(conn, mark)
    assert [(r["action"], r["reason"]) for r in rows] == [
        ("INVALIDATE", OA.SCOPE_LIMITS_PROPOSED)]

    # ── CHANGED: re-approved with a DIFFERENT digest, through the route ──
    await _approve(conn)
    aid = await _signed_and_authorized()
    tighter = dict(ACT._LIMITS, per_order_usd=20)
    # recorded directly (as a proposal the owner then approves) so that only
    # the APPROVAL moves the digest in this step
    raw = FA._obj(await FA._state(conn, FA.LIMITS_KEY))
    raw["proposed"] = tighter
    await conn.execute("UPDATE ingestion_state SET value=$2::jsonb "
                       "WHERE key=$1", FA.LIMITS_KEY, json.dumps(raw))
    c, cfg = client
    monkeypatch.setattr("sportsassets.db.get_pool", _pool_factory())
    mark = await _mark(conn)
    r = c.post("/api/admin/funded-limits/approve",
               json={"confirm": "20", "by": "OWNER"},
               headers={"X-Admin-Token": cfg.admin_token})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["owner_authorization"]["invalidated"] is True
    assert body["owner_authorization"]["differs_on"] == ["effective_digest"]
    assert (await _owner(conn))["invalidated_reason"] == \
        OA.SCOPE_LIMITS_APPROVED
    assert (await _system(conn))["revoked"] is True
    # the SAME digest approved again through the route invalidates nothing
    aid = await _signed_and_authorized()
    mark = await _mark(conn)
    r = c.post("/api/admin/funded-limits/approve",
               json={"confirm": "20", "by": "OWNER"},
               headers={"X-Admin-Token": cfg.admin_token})
    assert r.status_code == 200, r.text
    assert r.json()["owner_authorization"]["invalidated"] is False
    assert (await _owner(conn))["authorization_id"] == aid
    assert (await _owner(conn))["invalidated"] is False
    assert [x for x in await _rows(conn, mark)
            if x["action"] == "INVALIDATE"] == []


@pg
async def test_a_failed_invalidation_leaves_the_scope_unmoved(db, monkeypatch):
    conn = db
    digest = await _scope(conn)
    assert (await _sign(conn, digest))["ok"]
    await ACT._record_reconciliation_evidence(conn, account_id=OTHER)

    async def _boom(*a, **k):
        raise RuntimeError("synthetic invalidation failure")
    monkeypatch.setattr(OA, "invalidate_owner_authorization_if_scope_changed",
                        _boom)
    mark = await _mark(conn)
    got = await CTL.set_account(conn, by="test", account={
        "account_id": OTHER, "venue": VENUE})
    assert got["ok"] is False and got["applied"] is None
    assert got["refusal"] == OA.R_SCOPE_CHANGE_RAISED
    # the binding did NOT move, so the signature still matches it
    assert FA._obj(await FA._state(conn, FA.ACCOUNT_KEY))["account_id"] == \
        ACCT
    rows = await _rows(conn, mark)
    assert [(r["action"], r["outcome"], r["refusal"]) for r in rows] == [
        ("INVALIDATE", "REFUSED", OA.R_SCOPE_CHANGE_RAISED)]


@pg
async def test_the_consumer_keeps_the_existing_order_then_the_new_names(db):
    conn = db
    digest = await _scope(conn)
    await ACT._seed_evidence(conn, waived=False)

    async def _put(rec):
        await conn.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1,$2::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value=$2::jsonb",
            FA.OWNER_AUTH_KEY, json.dumps(rec))

    async def _refusal():
        return (await FA.authorize(conn, account_id=ACCT, venue=VENUE,
                                   by="t"))["refusal"]

    # EXPIRED: a signature made eight days ago with a seven-day lifetime
    old = await _sign(conn, digest, now=time.time() - 8 * 86400.0)
    assert old["ok"], old
    assert await _refusal() == FA.R_OWNER_AUTH_EXPIRED
    good = await _owner(conn)
    # the EXISTING refusals still come first, even on a revoked record
    await _put(dict(good, account_id=OTHER, revoked=True))
    assert await _refusal() == FA.R_OWNER_AUTH_ACCOUNT
    await _put(dict(good, venue="POLYMARKET", revoked=True))
    assert await _refusal() == FA.R_OWNER_AUTH_VENUE
    await _put(dict(good, effective_digest="0" * 64, invalidated=True))
    assert await _refusal() == FA.R_OWNER_AUTH_LIMITS
    # then revoked, invalidated, unauthenticated, expired
    await _put(dict(good, revoked=True, revoked_at=time.time()))
    assert await _refusal() == FA.R_OWNER_AUTH_REVOKED
    await _put(dict(good, invalidated=True, invalidated_at=time.time()))
    assert await _refusal() == FA.R_OWNER_AUTH_INVALIDATED
    await _put({k: v for k, v in good.items() if k != "authenticated_by"})
    assert await _refusal() == FA.R_OWNER_AUTH_UNAUTHENTICATED
    # A FORGERY WITH EVERY FIELD RIGHT but no ACCEPTED audit row behind it
    forged = dict(good, authorization_id="foa-forged",
                  expires_at=time.time() + 3600.0)
    await _put(forged)
    assert await _refusal() == FA.R_OWNER_AUTH_UNAUTHENTICATED
    # THE REAL RECORD, RE-DATED BY HAND past the expiry it was signed with:
    # its id and scope match an ACCEPTED row, but that row recorded the
    # original expiry, so the extension is refused as unauthenticated
    await _put(dict(good, expires_at=time.time() + 3600.0))
    assert await _refusal() == FA.R_OWNER_AUTH_UNAUTHENTICATED
    await _put(good)
    assert await _refusal() == FA.R_OWNER_AUTH_EXPIRED


@pg
async def test_a_revocation_racing_the_authorization_issues_nothing(
        db, monkeypatch):
    """The owner record is re-read UNDER ITS LOCK before the system
    authorization is written. A revocation landing between the checks and
    the write is seen, and no system authorization is issued."""
    conn = db
    digest = await _scope(conn)
    await ACT._seed_evidence(conn, waived=False)
    signed = await _sign(conn, digest)
    aid = signed["record"]["authorization_id"]
    real = OA.accepted_row

    async def _then_revoke(c, owner):
        got = await real(c, owner)
        rv = await OA.revoke_owner_authorization(
            c, authorization_id=aid, confirm=aid, reason="the race",
            operator=OPERATOR, auth=dict(AUTH))
        assert rv["ok"], rv
        return got
    monkeypatch.setattr(OA, "accepted_row", _then_revoke)
    got = await FA.authorize(conn, account_id=ACCT, venue=VENUE, by="t")
    assert got["ok"] is False
    assert got["refusal"] == FA.R_OWNER_AUTH_REVOKED
    assert await _system(conn) is None, "a system authorization was issued"


@pg
async def test_the_owner_record_is_necessary_and_sufficient_for_the_owner_step(
        db):
    """THE ROUND TRIP on a synthetic FUNDED-class account (PMUS). Every other
    gate is as it is: readiness is met by the activation suite's own fixture
    rows, and the execution boundary still refuses."""
    conn = db
    digest = await _scope(conn)
    await ACT._seed_evidence(conn, waived=False)
    without = await FA.authorize(conn, account_id=ACCT, venue=VENUE, by="t")
    assert without["ok"] is False and without["refusal"] == FA.R_OWNER_AUTH
    assert without["readiness"]["ready"] is True, without["unmet"]
    assert await _system(conn) is None
    assert (await _sign(conn, digest))["ok"]
    got = await FA.authorize(conn, account_id=ACCT, venue=VENUE, by="t")
    assert got["ok"] is True, got
    assert got["owner_authorization_validated"] is True
    assert got["verdict"] == ("AUTHORIZED_FOR_A_FUNDED_VENUE_"
                              "SUBMISSION_STILL_DISABLED_IN_CODE")
    assert got["authorises_capital"] is False
    assert got["execution_boundary"]["authorization_consumed"] is True
    assert got["execution_boundary"]["ok"] is False
    assert got["submission_would_be"] == EX.R_ACCOUNT_EXPOSURE_UNREADABLE
    assert got["submission_is_also_disabled_in_code"] is True
    assert EX.REAL_ORDER_SUBMISSION_ENABLED is False
    # the system authorization names the owner record it was issued on and
    # never outlives it (7-day default > 24 h, so not capped here)
    owner = await _owner(conn)
    sysrec = await _system(conn)
    assert sysrec["owner_authorization_id"] == owner["authorization_id"]
    assert sysrec["owner_scope_sha"] == owner["scope_sha"]
    assert sysrec["owner_expires_at"] == owner["expires_at"]
    assert sysrec["expires_at"] <= owner["expires_at"]
    assert sysrec["capped_by_owner_expiry"] is False
    # an owner lifetime SHORTER than the system's 24 h caps the system record
    rv = await OA.revoke_owner_authorization(
        conn, authorization_id=owner["authorization_id"],
        confirm=owner["authorization_id"], reason="re-sign shorter (test)",
        operator=OPERATOR, auth=dict(AUTH))
    assert rv["ok"], rv
    assert (await _sign(conn, digest, lifetime_days=0.25))["ok"]
    short = await FA.authorize(conn, account_id=ACCT, venue=VENUE, by="t")
    assert short["ok"] is True, short
    owner = await _owner(conn)
    sysrec = await _system(conn)
    assert sysrec["capped_by_owner_expiry"] is True
    assert sysrec["expires_at"] == owner["expires_at"]
    assert sysrec["expires_at"] < sysrec["at"] + EX.AUTHORIZATION_TTL_S
    # the status read agrees and shows what was signed
    st = await OA.status(conn)
    assert st["valid_against_current_scope"] is True
    assert st["record_status"] == OA.S_ACTIVE
    assert st["to_sign"]["effective_digest"] == digest
    assert st["to_sign"]["account_id"] == ACCT
    assert st["audit_tail"][0]["action"] == "RECORD"


# ════════════════════════════════════════════════════════════════════
# 4 · THE ROUTES AGAINST THE DATABASE
# ════════════════════════════════════════════════════════════════════

def _pool_factory():
    """A stand-in for `db.get_pool` that opens connections to the TEST DSN
    inside whatever loop the test client runs the route on."""
    import asyncpg

    class _Acq:
        async def __aenter__(self):
            self.c = await asyncpg.connect(DSN)
            return self.c

        async def __aexit__(self, *exc):
            await self.c.close()

    class _Pool:
        def acquire(self):
            return _Acq()

        async def fetchval(self, *a):
            c = await asyncpg.connect(DSN)
            try:
                return await c.fetchval(*a)
            finally:
                await c.close()

        async def execute(self, *a):
            c = await asyncpg.connect(DSN)
            try:
                return await c.execute(*a)
            finally:
                await c.close()

    async def get_pool():
        return _Pool()
    return get_pool


@pg
async def test_the_route_records_the_configured_operator(db, client,
                                                         monkeypatch):
    conn = db
    digest = await _scope(conn)
    c, cfg = client
    cfg.funded_resolution_key = "synthetic-test-resolution-key"
    cfg.funded_resolution_operator = OPERATOR
    monkeypatch.setattr("sportsassets.db.get_pool", _pool_factory())
    both = {"X-Admin-Token": cfg.admin_token,
            "X-Resolution-Key": cfg.funded_resolution_key}
    view = c.get("/api/admin/funded-owner-authorization",
                 headers={"X-Admin-Token": cfg.admin_token})
    assert view.status_code == 200, view.text
    v = view.json()
    assert v["owner_key_configured"] is True
    assert v["to_sign"]["account_id"] == ACCT
    assert v["to_sign"]["venue"] == VENUE
    assert v["to_sign"]["effective_digest"] == digest
    assert v["to_sign"]["ready_to_sign"] is True, v["to_sign"]
    assert "synthetic-test-resolution-key" not in view.text
    mark = await _mark(conn)
    r = c.post("/api/admin/funded-owner-authorization", headers=both, json={
        "account_id": ACCT, "venue": VENUE,
        "effective_digest": v["to_sign"]["effective_digest"],
        "statement": STATEMENT, "confirm": ACCT,
        # A NAME TYPED INTO THE BODY IS IGNORED
        "operator": "an-impostor", "by": "an-impostor"})
    assert r.status_code == 200, r.text
    rec = await _owner(conn)
    assert rec["by"] == OPERATOR
    assert rec["authenticated_by"]["operator"] == OPERATOR
    assert rec["authenticated_by"]["resolution_key_verified"] is True
    assert "synthetic-test-resolution-key" not in json.dumps(rec)
    rows = await _rows(conn, mark)
    assert rows[-1]["operator"] == OPERATOR
    assert "synthetic-test-resolution-key" not in json.dumps(
        [dict(x, at=None) for x in rows], default=str)
    # a refused signature answers 409 and is audited
    bad = c.post("/api/admin/funded-owner-authorization", headers=both,
                 json={"account_id": ACCT, "venue": VENUE,
                       "effective_digest": "0" * 64,
                       "statement": STATEMENT, "confirm": ACCT})
    assert bad.status_code == 409
    assert bad.json()["refusal"] == OA.R_DIGEST_NOT_CURRENT
    # the revoke route
    rv = c.post("/api/admin/funded-owner-authorization/revoke", headers=both,
                json={"authorization_id": rec["authorization_id"],
                      "confirm": rec["authorization_id"],
                      "reason": "synthetic route test"})
    assert rv.status_code == 200, rv.text
    assert (await _owner(conn))["revoked_by"] == OPERATOR
    view = c.get("/api/admin/funded-owner-authorization",
                 headers={"X-Admin-Token": cfg.admin_token}).json()
    assert view["record_status"] == OA.S_REVOKED
    assert view["valid_against_current_scope"] is False
    assert [x["action"] for x in view["audit_tail"][:2]] == ["REVOKE",
                                                             "RECORD"]
