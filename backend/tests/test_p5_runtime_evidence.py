"""P5_LIVE_STREAM_BOOK_V1 AT RUNTIME: EVERY PREDICATE PROVEN OR NOT, AND WHY.

`p5_runtime` judges every requirement of the rule (C1..C13), the owner
approval (A1) and the same-book premise (S1) in the DECIDING process, on its
own state plus the stream evidence the workers record (migration 210).

  §1  the production shape of the API today (no PMX_*, stream not started
      here, no identity mapper, REST-priced decisions, artifact not
      approved, no samples): BLOCKED; the first blocking EXTERNAL predicate
      is C2 -- PMX_* absent from the deciding process -- with the exact
      owner action; C3..C11 are DEPENDENT on C2; every INTERNAL blocker is
      named; C13 is PROVEN (enforced by the actual lane)
  §2  C2's sub-checks select the exact external predicate in order: names
      absent -> flag off -> guard -> (internal) not started -> venue refused
  §3  LIVE_ADMISSIBLE end to end against the in-process gRPC venue: the
      stream runs in THIS process, an exact mapper, a stream-priced
      decision, an owner-approved matching artifact (rolled back) and 30
      agreeing same-book samples -- every predicate PROVEN; then each fact
      flipped ALONE blocks with the right predicate (approval, same-book
      contradicted, stale book, gap, market closed, mapper, PMX_*)
  §4  same-book status thresholds; the evidence tables absent fail closed
  §5  GET /api/command/p5/evidence answers the same, read-only, never
      echoing an env value
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import os
import time

import asyncpg
import grpc
import pytest

from sportsassets import institutional_same_book as SB
from sportsassets import institutional_stream as IS
from sportsassets import institutional_stream_evidence as SE
from sportsassets import live_book_currency as LBC
from sportsassets import live_book_evidence as LBE
from sportsassets import p5_runtime as P5R

try:
    from tests.test_institutional_contract_map import AEC, SLUG
    from tests.test_institutional_md_grpc_transport import (
        GOOD, Wait, ack, book, until, venue)  # noqa: F401  (fixture)
except ImportError:                                             # pragma: no cover
    from test_institutional_contract_map import AEC, SLUG  # type: ignore
    from test_institutional_md_grpc_transport import (  # type: ignore
        GOOD, Wait, ack, book, until, venue)  # noqa: F401

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

PMX_ENV = {"PMX_CLIENT_ID": "NOT-REAL-client", "PMX_PARTICIPANT_ID":
           "NOT-REAL-participant", "PMX_KEY_ID": "NOT-REAL-kid",
           "PMX_PRIVATE_KEY_B64": "NOT-REAL-key"}
ON_ENV = dict(PMX_ENV, INSTITUTIONAL_MD_STREAM="on")


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    from sportsassets import institutional_api_stream as IAS
    IS.reset()
    IAS.reset()
    monkeypatch.setattr(LBE, "IDENTITY_MAPPER", None)
    yield
    IS.reset()
    IAS.reset()


def by_id(res):
    return {p["predicate"]: p for p in res["predicates"]}


def facts(*, env=None, decisions=None, stream=None, same_book=None,
          artifact=None, at=None):
    at = time.time() if at is None else at
    return {"at": at, "ps": P5R.process_state(env if env is not None else {}),
            "stream": stream or {"status": "ABSENT", "symbols": {},
                                 "processes": []},
            "same_book": same_book or {"status": "ABSENT", "by_symbol": {}},
            "artifact": artifact or {"owner_approved": False},
            "decisions": decisions if decisions is not None else {
                SLUG: LBE.evaluate_for({}, {"us_market_slug": SLUG,
                                            "side": "yes"}, obs=None,
                                       now=at)}}


# ── §1 the API as it is today ───────────────────────────────────────────

def test_todays_api_is_blocked_first_externally_by_pmx_absent():
    res = P5R.assemble(facts(env={}))
    p = by_id(res)
    assert res["verdict"] == P5R.BLOCKED
    assert res["predicate_order"] == list(P5R.ORDER)
    assert [x["predicate"] for x in res["predicates"]] == list(P5R.ORDER)
    # the first blocking predicate of any kind is C1, and it is INTERNAL
    assert res["first_blocking"]["predicate"] == "C1_IDENTITY_EXACT"
    assert res["first_blocking"]["blocker"] == P5R.K_INTERNAL
    assert res["first_blocking"]["reason"] == P5R.I_NO_MAPPER
    # THE first blocking EXTERNAL predicate, exact
    ext = res["first_blocking_external"]
    assert ext["predicate"] == "C2_STREAM_RUNNING"
    assert ext["blocker"] == P5R.K_EXTERNAL
    assert ext["reason"] == P5R.X_PMX_ABSENT
    for name in ("PMX_CLIENT_ID", "PMX_PARTICIPANT_ID", "PMX_KEY_ID",
                 "PMX_PRIVATE_KEY_B64", "sportsassets-api", "Render"):
        assert name in ext["action"]
    c2 = p["C2_STREAM_RUNNING"]
    assert c2["status"] == P5R.NOT_PROVEN
    assert [c["check"] for c in c2["checks"] if not c["ok"]] == [
        "PMX_CREDENTIAL_NAMES_PRESENT", "INSTITUTIONAL_MD_STREAM_ON",
        "MARKET_DATA_IDENTITY_GUARD_PASSES", "STREAM_STARTED_IN_THIS_PROCESS",
        "STREAM_RUNNING_IN_THIS_PROCESS"]
    for cid in ("C3_CONNECTION_EPOCH_ALIVE", "C4_COMPLETE_BOOK_ON_THIS_EPOCH",
                "C6_VENUE_TS_PRESENT", "C7_VENUE_TS_MONOTONIC_IN_EPOCH",
                "C8_RECEIPT_AGE", "C9_VENUE_RECEIPT_SKEW", "C10_MARKET_OPEN",
                "C11_STREAM_BOOK_CURRENT"):
        assert p[cid]["status"] == P5R.NOT_PROVEN
        assert p[cid]["blocker"] == P5R.K_DEPENDENT
        assert p[cid]["depends_on"] == "C2_STREAM_RUNNING"
    assert p["C5_SEQUENCE_INTEGRITY"]["status"] == P5R.NOT_PROVEN
    assert p["C12_PRICED_FROM_THIS_BOOK"]["reason"] == P5R.I_REST_PRICED
    assert p["C12_PRICED_FROM_THIS_BOOK"]["blocker"] == P5R.K_INTERNAL
    assert p["C13_VERDICT_AGE_AT_SUBMIT"]["status"] == P5R.PROVEN
    assert p[P5R.A1]["reason"] == P5R.X_NOT_APPROVED
    assert p[P5R.S1]["reason"] == P5R.X_NO_SAMPLES
    assert "sportsassets-workers" in p[P5R.S1]["action"]
    internal = {(b["predicate"], b["reason"])
                for b in res["internal_blockers"]}
    assert internal == {("C1_IDENTITY_EXACT", P5R.I_NO_MAPPER),
                        ("C2_STREAM_RUNNING", P5R.I_NOT_STARTED),
                        ("C12_PRICED_FROM_THIS_BOOK", P5R.I_REST_PRICED)}
    external = [b["reason"] for b in res["external_blockers"]]
    assert external[0] == P5R.X_PMX_ABSENT
    assert P5R.X_FLAG_OFF in external and P5R.X_NOT_APPROVED in external
    assert res["owner_approved"] is False


async def test_after_c1_c2_c12_the_api_has_only_external_blockers(
        monkeypatch):
    """The API as this commit runs it with today's env (no PMX_*, flag
    off): the lifespan has installed the mapper and called start(), the
    actual lane has installed the stream-pricing hook. No INTERNAL blocker
    is left; the remaining ones are external and named exactly."""
    from sportsassets import decision_hooks as DH
    from sportsassets import institutional_api_stream as IAS
    out = await IAS.start(None, env={})
    assert out["state"] == IS.S_DISABLED
    monkeypatch.setattr(LBE, "IDENTITY_MAPPER", IAS.identity_mapper)
    monkeypatch.setattr(DH, "LIVE_BOOK_STREAM", LBE.observe)
    at = time.time()
    f = facts(env={}, at=at, decisions={SLUG: LBE.evaluate_decision(
        {}, {"us_market_slug": SLUG, "side": "ORDER_INTENT_BUY_LONG"},
        now=at)})
    res = P5R.assemble(f)
    p = by_id(res)
    assert res["verdict"] == P5R.BLOCKED
    assert res["internal_blockers"] == []
    assert res["first_blocking"]["predicate"] == "C1_IDENTITY_EXACT"
    assert res["first_blocking"]["blocker"] == P5R.K_DEPENDENT
    assert res["first_blocking"]["depends_on"] == "C2_STREAM_RUNNING"
    ext = res["first_blocking_external"]
    assert (ext["predicate"], ext["reason"]) == ("C2_STREAM_RUNNING",
                                                 P5R.X_PMX_ABSENT)
    reasons = [b["reason"] for b in res["external_blockers"]]
    assert reasons[0] == P5R.X_PMX_ABSENT
    assert P5R.X_FLAG_OFF in reasons
    assert P5R.X_NOT_APPROVED in reasons and P5R.X_NO_SAMPLES in reasons
    assert p["C12_PRICED_FROM_THIS_BOOK"]["blocker"] == P5R.K_DEPENDENT
    assert res["deciding_process"]["decision_price_source"] == \
        P5R.STREAM_WHEN_CURRENT
    assert res["deciding_process"]["identity_mapper_installed"] is True


def test_the_process_state_is_names_only():
    ps = P5R.process_state(dict(PMX_ENV, PMX_PRIVATE_KEY_B64="SECRET-VALUE"))
    assert "SECRET-VALUE" not in repr(ps)
    assert ps["pmx_missing"] == []
    assert ps["decision_price_source"] == "REST_PAPER_BOOK"
    assert ps["c13_enforced_by_actual_lane"] is True
    assert ps["identity_mapper_installed"] is False
    assert ps["stream_state"] == IS.S_NOT_STARTED


# ── §2 C2's exact external predicate ────────────────────────────────────

def test_c2_selects_the_next_exact_predicate_in_order(monkeypatch):
    res = P5R.assemble(facts(env=PMX_ENV))
    assert res["first_blocking_external"]["reason"] == P5R.X_FLAG_OFF
    assert "INSTITUTIONAL_MD_STREAM=on" in \
        res["first_blocking_external"]["action"]
    # names present and flag on, but nothing starts the stream here:
    # C2 becomes INTERNAL and the first EXTERNAL moves on to A1
    res = P5R.assemble(facts(env=ON_ENV))
    c2 = by_id(res)["C2_STREAM_RUNNING"]
    assert (c2["blocker"], c2["reason"]) == (P5R.K_INTERNAL,
                                             P5R.I_NOT_STARTED)
    assert res["first_blocking_external"]["predicate"] == P5R.A1
    # the identity guard refuses the execution key
    monkeypatch.setattr(P5R.MDI, "guard", lambda *a, **k:
                        "MARKET_DATA_CREDENTIAL_IS_THE_FUNDED_KEY")
    res = P5R.assemble(facts(env=ON_ENV))
    assert res["first_blocking_external"]["reason"].startswith(P5R.X_GUARD)
    monkeypatch.undo()
    monkeypatch.setattr(LBE, "IDENTITY_MAPPER", None)
    # started, and the venue refused the credential
    IS._START.update(state=IS.S_IDLE, why="started")
    IS.BOOKS.on_refused("PERMISSION_DENIED")
    res = P5R.assemble(facts(env=ON_ENV))
    assert res["first_blocking_external"]["predicate"] == "C2_STREAM_RUNNING"
    assert res["first_blocking_external"]["reason"] == P5R.X_VENUE_REFUSED


# ── §3 LIVE_ADMISSIBLE end to end, then each fact alone ─────────────────

def exact_mapper(slug, side):
    """The API's own mapper (institutional_api_stream.identity_mapper) over
    the refdata record the API process holds for SLUG."""
    from sportsassets import institutional_api_stream as IAS
    IAS.REFDATA[SLUG] = {"record": AEC, "at": time.time()}
    return IAS.identity_mapper(slug, side)


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    async def execute(self, sql, *a):
        return await self.conn.execute(sql, *a)


def start_stream_here(venue, steps):
    """THIS process runs the stream, fed by the in-process gRPC venue."""
    rec = SE.install(IS.BOOKS, service="api-test")
    gate = Wait()
    venue.scripts = [list(steps) + [gate]]

    def factory(books, token_fn):
        return IS.GrpcBidiTransport(
            books, token_fn, target=venue.target,
            channel_factory=lambda t: grpc.insecure_channel(t),
            sleep=lambda s: time.sleep(min(s, 0.05)))
    out = IS.start_default(env=ON_ENV, transport_factory=factory,
                           token_fn=lambda: GOOD,
                           available=lambda: (True, None))
    assert out["started"] is True
    IS.set_instrument(SLUG, AEC)
    IS.want([SLUG])
    until(lambda: IS.current(SLUG)["ok"])
    return rec, gate


APPROVE = ("UPDATE live_rule_artifacts SET status='APPROVED', "
           "owner_approval_actor='OWNER-TEST', owner_approved_at=now(), "
           "owner_approval_statement='test approval, rolled back' "
           "WHERE rule_id=$1 AND version=$2")


@pg
async def test_live_admissible_end_to_end_then_each_fact_alone(venue,
                                                              monkeypatch):
    # the deciding process as the API runs it: the exact mapper and the
    # decision path's stream pricing hook (execution_intent.start installs
    # decision_hooks.LIVE_BOOK_STREAM = live_book_evidence.observe)
    from sportsassets import decision_hooks as DH
    monkeypatch.setattr(LBE, "IDENTITY_MAPPER", exact_mapper)
    monkeypatch.setattr(DH, "LIVE_BOOK_STREAM", LBE.observe)
    rec, gate = start_stream_here(venue, [
        ack((SLUG,)), lambda: book(symbol=SLUG, bids=((450, 1000),),
                                   offers=((470, 500),))])
    conn = await asyncpg.connect(DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute(APPROVE, LBC.RULE_ID, LBC.VERSION)
        pool = _Pool(conn)
        rows = rec.rows(IS.BOOKS, identity_for=lambda s: {
            "ok": True, "institutional_symbol": s, "refusal": None})
        assert await SE.persist(pool, rows) == 2
        retail = {"ok": True, "error": None, "marketData": {
            "bids": [{"px": {"value": "0.45"}, "qty": "10"}],
            "offers": [{"px": {"value": "0.47"}, "qty": "5"}]}}
        samples = [SB.sample(SLUG, record=AEC, books_current=IS.current,
                             retail_read=lambda s: retail)
                   for _ in range(P5R.SAME_BOOK_MIN_COMPARABLE)]
        assert {s["verdict"] for s in samples} == {SB.V_AGREE}
        assert await SB.persist(pool, samples, process_id=rec.process_id,
                                service="api-test") == len(samples)

        f = await P5R.gather(conn, env=ON_ENV)
        res = P5R.assemble(f)
        p = by_id(res)
        assert res["verdict"] == P5R.LIVE_ADMISSIBLE, [
            (x["predicate"], x["reason"]) for x in res["predicates"]
            if x["status"] != P5R.PROVEN]
        assert all(x["status"] == P5R.PROVEN for x in res["predicates"])
        assert res["first_blocking"] is None
        assert res["first_blocking_external"] is None
        assert res["owner_approved"] is True and res["subject_symbol"] == SLUG
        assert res["runtime_evidence"]["stream"]["status"] == "LIVE"
        assert res["runtime_evidence"]["same_book"]["status"] == "SUPPORTED"
        assert res["deciding_process"]["decision_price_source"] == \
            P5R.STREAM_WHEN_CURRENT
        assert p["C12_PRICED_FROM_THIS_BOOK"]["deciding_process"][
            "priced_from"] == "STREAM"
        w = res["runtime_evidence"]["stream"]["symbols"][SLUG]["worker_p5"]
        assert w["stream_book_verdict"] == LBC.ESTABLISHED
        assert p["C3_CONNECTION_EPOCH_ALIVE"]["runtime_evidence"][
            "workers_stream"]["passed"] is True

        def blocked(f2):
            r = P5R.assemble(f2)
            assert r["verdict"] == P5R.BLOCKED
            return r

        # A1 alone: the approval
        f2 = copy.deepcopy(f)
        f2["artifact"]["owner_approved"] = False
        r = blocked(f2)
        assert r["first_blocking"]["predicate"] == P5R.A1
        assert r["first_blocking_external"]["reason"] == P5R.X_NOT_APPROVED
        # S1 alone: a disagreement while the stream book was stable
        f2 = copy.deepcopy(f)
        f2["same_book"]["status"] = "CONTRADICTED"
        r = blocked(f2)
        assert r["first_blocking_external"] == dict(
            r["first_blocking_external"], predicate=P5R.S1,
            reason=P5R.X_CONTRADICTED)
        # C8 alone: the same book evaluated 5 s later is STALE (RUNTIME)
        later = f["at"] + 5.0
        f2 = dict(copy.deepcopy(f), at=later, decisions={
            SLUG: LBE.evaluate_decision({}, {"us_market_slug": SLUG,
                                          "side": "ORDER_INTENT_BUY_LONG"},
                                     now=later)})
        r = blocked(f2)
        assert r["first_blocking"]["predicate"] == "C8_RECEIPT_AGE"
        assert r["first_blocking"]["blocker"] == P5R.K_RUNTIME
        assert r["first_blocking"]["reason"] == LBC.R_RECEIPT_OLD
        assert r["first_blocking_external"] is None
        # C1 alone: no mapper in the deciding process (INTERNAL)
        monkeypatch.setattr(LBE, "IDENTITY_MAPPER", None)
        f2 = await P5R.gather(conn, env=ON_ENV)
        r = blocked(f2)
        assert (r["first_blocking"]["predicate"],
                r["first_blocking"]["reason"]) == ("C1_IDENTITY_EXACT",
                                                   P5R.I_NO_MAPPER)
        assert by_id(r)["C3_CONNECTION_EPOCH_ALIVE"]["depends_on"] == \
            "C1_IDENTITY_EXACT"
        monkeypatch.setattr(LBE, "IDENTITY_MAPPER", exact_mapper)
        # C2 alone: PMX_* absent here (state from a process without it)
        f2 = copy.deepcopy(f)
        f2["ps"]["pmx_missing"] = list(P5R.PMX.CREDENTIALS_EXPECTED)
        f2["ps"]["stream_state"] = IS.S_CREDENTIAL
        r = blocked(f2)
        assert r["first_blocking_external"]["reason"] == P5R.X_PMX_ABSENT
        # C10 alone: the market is not open
        IS.BOOKS.on_update({"symbol": SLUG, "bids": [(450, 1000)],
                            "offers": [(470, 500)],
                            "state": "INSTRUMENT_STATE_HALTED",
                            "transact_time": time.time()})
        now = time.time()
        f2 = dict(copy.deepcopy(f), at=now, decisions={
            SLUG: LBE.evaluate_decision({}, {"us_market_slug": SLUG,
                                             "side": "ORDER_INTENT_BUY_LONG"},
                                        now=now)})
        r = blocked(f2)
        bad = {x["predicate"] for x in r["predicates"]
               if x["status"] != P5R.PROVEN}
        # a refused stream book is never priced from: C12 fails with them
        assert bad == {"C10_MARKET_OPEN", "C11_STREAM_BOOK_CURRENT",
                       "C12_PRICED_FROM_THIS_BOOK"}
        assert by_id(r)["C10_MARKET_OPEN"]["reason"] == LBC.R_NOT_OPEN
        # C3/C4 alone: the connection ends -> GAP
        IS.BOOKS.on_disconnected("test")
        now = time.time()
        f2 = dict(copy.deepcopy(f), at=now, decisions={
            SLUG: LBE.evaluate_decision({}, {"us_market_slug": SLUG,
                                             "side": "ORDER_INTENT_BUY_LONG"},
                                        now=now)})
        r = blocked(f2)
        assert by_id(r)["C3_CONNECTION_EPOCH_ALIVE"]["reason"] == \
            LBC.R_EPOCH_LOST
        assert by_id(r)["C4_COMPLETE_BOOK_ON_THIS_EPOCH"]["reason"] == \
            LBC.R_GAP_RECONNECT
        assert by_id(r)["C5_SEQUENCE_INTEGRITY"]["status"] == P5R.NOT_PROVEN
    finally:
        await tx.rollback()
        await conn.close()
        gate.set()


# ── §4 same-book thresholds; absent tables ──────────────────────────────

def test_same_book_status_thresholds():
    n = P5R.SAME_BOOK_MIN_COMPARABLE
    assert P5R.same_book_status({})[0] == "UNTESTED"
    assert P5R.same_book_status({"NOT_COMPARABLE": (9, 9)})[0] == "UNTESTED"
    assert P5R.same_book_status({"AGREE_TOP_N": (n, n)})[0] == "SUPPORTED"
    assert P5R.same_book_status({"AGREE_TOP_N": (n - 1, 0)})[0] == \
        "INCONCLUSIVE"
    assert P5R.same_book_status({"AGREE_TOUCH_ONLY": (n, 0)})[0] == \
        "SUPPORTED"
    # a disagreement inside a window where the stream book moved is a
    # possible race; enough of them still defeats SUPPORTED
    assert P5R.same_book_status({"AGREE_TOP_N": (n, 0),
                                 "DISAGREE": (5, 0)})[0] == "INCONCLUSIVE"
    # ONE disagreement while the stream book was stable contradicts
    st, d = P5R.same_book_status({"AGREE_TOP_N": (1000, 0),
                                  "DISAGREE": (1, 1)})
    assert st == "CONTRADICTED"
    assert d["disagree_with_stream_stable_in_window"] == 1


class _NoTables:
    async def fetchval(self, sql, *a):
        if "to_regclass" in sql:
            return False
        return None

    async def fetch(self, *a):
        raise AssertionError("no table, no read")

    async def fetchrow(self, *a):
        raise AssertionError("no table, no read")

    def is_in_transaction(self):
        return False


def test_absent_evidence_tables_fail_closed():
    res = asyncio.run(P5R.evaluate(_NoTables(), env={}))
    assert res["verdict"] == P5R.BLOCKED
    assert res["runtime_evidence"]["stream"]["status"] == "ABSENT"
    assert res["runtime_evidence"]["same_book"]["status"] == "ABSENT"
    assert res["owner_approved"] is False
    assert res["first_blocking_external"]["reason"] == P5R.X_PMX_ABSENT
    assert res["subject_symbol"] is None


# ── §5 the endpoint ─────────────────────────────────────────────────────

@pg
def test_the_endpoint_answers_read_only_and_never_echoes_a_value(
        monkeypatch):
    from fastapi.testclient import TestClient
    from sportsassets.api import app as app_mod
    for k in PMX_ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("PMX_CLIENT_ID", "SECRET-VALUE-NOT-REAL")
    monkeypatch.delenv("INSTITUTIONAL_MD_STREAM", raising=False)

    class Pool:
        @contextlib.asynccontextmanager
        async def acquire(self):
            c = await asyncpg.connect(DSN)
            try:
                yield c
            finally:
                await c.close()

    async def get_pool():
        return Pool()
    monkeypatch.setattr(app_mod, "get_pool", get_pool)
    app_mod.app.dependency_overrides[app_mod.require_command] = \
        lambda: "test"
    try:
        cli = TestClient(app_mod.app)
        r = cli.get("/api/command/p5/evidence")
        assert r.status_code == 200
        assert r.headers["cache-control"] == "no-store"
        body = r.json()
        assert "SECRET-VALUE-NOT-REAL" not in r.text
        assert body["verdict"] == "BLOCKED"
        assert body["rule"] == LBC.RULE_ID and body["rule_sha256"] == \
            LBC.SHA256
        ext = body["first_blocking_external"]
        assert ext["predicate"] == "C2_STREAM_RUNNING"
        assert ext["reason"] == P5R.X_PMX_ABSENT
        assert "PMX_CLIENT_ID" not in ext["action"].split("(")[1]
        assert body["owner_approved"] is False
        assert body["artifact"]["stored_status"] == \
            "READY_FOR_OWNER_APPROVAL"
        assert body["artifact"]["stored_sha256_matches_code"] is True
        assert [p["predicate"] for p in body["predicates"]] == \
            list(P5R.ORDER)
        assert cli.get("/api/command/p5/evidence",
                       params={"symbol": "../x"}).status_code == 422
        r = cli.get("/api/command/p5/evidence", params={"symbol": SLUG})
        assert r.status_code == 200 and r.json()["subject_symbol"] == SLUG
    finally:
        app_mod.app.dependency_overrides.pop(app_mod.require_command, None)
