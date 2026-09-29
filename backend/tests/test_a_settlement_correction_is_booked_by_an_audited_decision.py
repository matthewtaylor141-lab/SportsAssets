"""A SETTLEMENT THE VENUE CORRECTED CAN BE BOOKED -- BY ONE AUTHENTICATED,
AUDITED DECISION, AS ONE DELTA, NEVER A SECOND SETTLEMENT.

The gap (account map §3): a re-read that DISAGREES (migration 141) refused
the account new exposure for ever, because nothing could book the
correction. `bettor_funded_corrections.book_settlement_correction` books it.
These tests hold, with the production functions and a substituted settlement
probe (NO VENUE IS CONTACTED, NO ORDER IS SENT; the training data is the
SYNTHETIC set of `test_the_funded_lane_actually_learns`):

  * every refusal -- a wrong confirm, a short statement, a re-read that
    agrees, established nothing, is not the newest, or is not the one the
    operator saw, a caller whose name is not the authenticated one -- is
    refused by name AND audited;
  * the accepted correction books exactly one SETTLEMENT_CORRECTION event of
    the right delta under the deterministic id, leaves the original
    SETTLEMENT row alone, makes the corrected reading the booked one with the
    original kept in its history, and records a new outcome version;
  * the same request again returns the same correction; a different one for
    an answered re-read is refused;
  * the contest lifts, and a LATER re-read that disagrees with the corrected
    reading contests again;
  * the label is read from the corrected price, and a model fit on the old
    label no longer reproduces and is withdrawn by the existing rule;
  * the route needs the admin token AND the resolution key, and takes the
    operator from configuration.
"""
from __future__ import annotations

import inspect
import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from sportsassets import bettor_funded_activation as FA
from sportsassets import bettor_funded_corrections as FC
from sportsassets import bettor_funded_learning as FL
from sportsassets import bettor_funded_management as FM
from sportsassets import bettor_funded_model as FMD
from tests import test_the_funded_lane_actually_learns as LRN

DSN = LRN.DSN
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

ACCT = LRN.ACCT_LOW
DESK = "desk-settlement-correction-test"
BASE = 5700
OPERATOR = "owner-correction@test"
AUTH = {"admin_token_verified": True, "resolution_key_verified": True,
        "operator": OPERATOR, "route": "test"}
STATEMENT = ("checked the venue's settlement endpoint and its long side: the "
             "hedge contract now settles at 1.0")
HEDGE = "fpi-learns-%d-h" % BASE
HEDGE_SLUG = "slug-learns-%d-HEDGE" % BASE


async def _purge(conn):
    """Re-reads, corrections and their audit are append-only by trigger; a
    test removes its own with triggers suspended for ITS transaction only."""
    async with conn.transaction():
        await conn.execute("SET LOCAL session_replication_role = replica")
        for table in ("bettor_funded_settlement_rechecks",
                      "bettor_funded_settlement_corrections",
                      "bettor_funded_correction_audit"):
            await conn.execute(
                "DELETE FROM %s WHERE intent_id LIKE 'fpi-learns-%%'" % table)


async def _clean(conn):
    await _purge(conn)
    await conn.execute("DELETE FROM bettor_desk_accounts WHERE account_id=$1",
                       ACCT)
    await LRN._clean(conn)


async def _account_row(conn):
    await conn.execute(
        "INSERT INTO bettor_desk_accounts (account_id, desk_id, status, "
        " opening_balance, opened_at, note, provenance, paused, "
        " pause_reason, accounting_status, accounting_detail) VALUES "
        " ($1,$2,'ACTIVE',0,now(),'settlement correction test','{}'::jsonb,"
        "  FALSE,NULL,'CLEAN','{}'::jsonb)", ACCT, DESK)


async def _as_closed_by_the_venue(conn, intent_id):
    """THE CLOSE'S OWN SHAPE on a synthetic leg: the SETTLEMENT economics row
    `reconcile_settlement` books (qty = residual, amount = payout_usd, under
    its deterministic id) and the residual it leaves on the closed row."""
    s = json.loads(await conn.fetchval(
        "SELECT settlement::text FROM bettor_funded_intents "
        " WHERE intent_id=$1", intent_id))
    await conn.execute(
        "UPDATE bettor_funded_intents SET residual_qty=10 WHERE intent_id=$1",
        intent_id)
    await conn.execute(
        "INSERT INTO bettor_funded_economics (event_id, intent_id, at, kind, "
        " amount_usd, qty, basis, evidence) VALUES ($1,$2,to_timestamp($3),"
        " 'SETTLEMENT',$4,10,'synthetic close','{}'::jsonb)",
        "fev:%s:SETTLEMENT:%s" % (intent_id, s["terminal_reading"]),
        intent_id, float(s["at"]), float(s["payout_usd"]))
    return s


def _probe(booked, *, overrides=None, unreadable=(), void=()):
    overrides = dict(overrides or {})

    def probe(client, slug):
        if slug in unreadable:
            return {"terminal_reading": "UNREADABLE", "why": "substituted"}
        if slug in void:
            return {"terminal_reading": "EXPLICIT_VOID", "why": "substituted"}
        px = overrides.get(slug, booked.get(slug))
        return {"terminal_reading": "REPORTED_SETTLEMENT",
                "authoritative_payout_present": True,
                "reader_verdict": {"status": "RESOLVED",
                                   "corroboration": "CORROBORATED",
                                   "settlement_price": px,
                                   "settlement_price_raw": str(px)}}
    return probe


async def _booked(conn):
    return {r["us_market_slug"]: float(json.loads(r["s"])["payout_price"])
            for r in await conn.fetch(
                "SELECT us_market_slug, settlement::text AS s "
                "  FROM bettor_funded_intents WHERE account_id=$1", ACCT)}


async def _rechecks(conn, intent_id=HEDGE):
    return [dict(r) for r in await conn.fetch(
        "SELECT * FROM bettor_funded_settlement_rechecks WHERE intent_id=$1 "
        " ORDER BY read_at, recheck_id", intent_id)]


def _book(conn, **kw):
    args = dict(intent_id=HEDGE, operator=OPERATOR, statement=STATEMENT,
                confirm=HEDGE, auth=AUTH)
    args.update(kw)
    return FC.book_settlement_correction(conn, **args)


# ═════════════════════════════════════════════════════════════════════
# THE WHOLE PATH
# ═════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_a_correction_is_refused_audited_booked_and_then_contested_again():
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        rows, _ = LRN._synthetic(50)
        t_fit = datetime.now(timezone.utc)
        fitted = await LRN._fit_on_records(
            conn, rows=rows, labels=[0.0] * 45 + [1.0] * 5, before=t_fit,
            base=BASE, account=ACCT, estimator="BASE_RATE")
        await FMD.register(conn, model_id="mdl:correction",
                           model_version="v-c", fitted=fitted,
                           fit_through=t_fit)
        await conn.execute(
            "UPDATE bettor_funded_models SET state='APPROVED', "
            "  approved_at=now(), approved_by='owner@test', "
            "  evaluation='{\"log_loss\": 0.5}'::jsonb WHERE model_id=$1",
            "mdl:correction")
        await _account_row(conn)
        booked_hedge = await _as_closed_by_the_venue(conn, HEDGE)
        assert booked_hedge["payout_price"] == 0.0     # settled as a loss
        dec = "dec:learns-%d" % BASE
        v1 = await FL.join_realised(conn, decision_id=dec)
        assert v1["ok"] is True and v1["outcome_version"] == 1, v1
        assert (await FMD.approved(conn))["ok"] is True
        booked = await _booked(conn)
        t0 = t_fit.timestamp() + 60
        common = dict(account_id=ACCT, venue=LRN.VENUE, per_pass=1000)

        # ── A RE-READ THAT AGREES, then two that disagree ─────────────
        await FM.recheck_settlements(conn, probe=_probe(booked),
                                     now=t0, **common)
        up = {HEDGE_SLUG: 1.0}
        await FM.recheck_settlements(conn, now=t0 + 60, **common,
                                     probe=_probe(booked, overrides=up))
        await FM.recheck_settlements(conn, now=t0 + 120, **common,
                                     probe=_probe(booked, overrides=up))
        # ...and one that establishes nothing
        await FM.recheck_settlements(conn, now=t0 + 180, **common,
                                     probe=_probe(booked,
                                                  unreadable={HEDGE_SLUG}))
        agrees, r1, r2, unest = await _rechecks(conn)
        assert [agrees["verdict"], r1["verdict"], r2["verdict"],
                unest["verdict"]] == ["AGREES", "DISAGREES", "DISAGREES",
                                      "NOT_ESTABLISHED"]
        sel = await FA.account_selection(conn, ACCT)
        assert sel["refusal"] == FA.R_SETTLEMENT_CONTESTED
        # the model resting on the old label no longer reproduces
        assert (await FMD.approved(conn))["ok"] is False

        # ── THE OPERATOR'S VIEW NAMES THE RE-READ AND ITS SHA ─────────
        view = await FC.listing(conn, intent_id=HEDGE)
        assert [a["recheck_id"] for a in view["awaiting_correction"]] == \
            [int(r2["recheck_id"])]
        sha = view["awaiting_correction"][0]["recheck_sha"]
        assert sha == FC.recheck_sha(r2)
        rid = int(r2["recheck_id"])

        # ── EVERY REFUSAL, BY NAME, AND AUDITED ──────────────────────
        refusals = [
            (dict(recheck_id=rid, seen_recheck_sha=sha, confirm="nope"),
             FC.R_CONFIRM),
            (dict(recheck_id=rid, seen_recheck_sha=sha, statement="short"),
             FC.R_STATEMENT),
            (dict(recheck_id=rid, seen_recheck_sha=sha,
                  operator="somebody-else"), FC.R_OPERATOR_NOT_AUTHENTICATED),
            (dict(recheck_id=rid, seen_recheck_sha=sha,
                  auth=dict(AUTH, resolution_key_verified=False)),
             FC.R_BOTH_FACTORS),
            (dict(recheck_id=rid, seen_recheck_sha=""), FC.R_SEEN_SHA),
            (dict(recheck_id=int(agrees["recheck_id"]),
                  seen_recheck_sha=FC.recheck_sha(agrees)),
             FC.R_RECHECK_AGREES),
            (dict(recheck_id=int(unest["recheck_id"]),
                  seen_recheck_sha=FC.recheck_sha(unest)),
             FC.R_RECHECK_NOT_ESTABLISHED),
            # A STALE RE-READ: a newer established one exists
            (dict(recheck_id=int(r1["recheck_id"]),
                  seen_recheck_sha=FC.recheck_sha(r1)), FC.R_NOT_NEWEST),
            # the operator decided on something other than this re-read
            (dict(recheck_id=rid, seen_recheck_sha=FC.recheck_sha(r1)),
             FC.R_STALE_SHA),
            (dict(recheck_id=rid, seen_recheck_sha=sha,
                  intent_id="fpi-learns-%d-p" % BASE,
                  confirm="fpi-learns-%d-p" % BASE), FC.R_NO_SUCH_RECHECK),
        ]
        for kw, want in refusals:
            got = await _book(conn, **kw)
            assert got["ok"] is False and got["refusal"] == want, (kw, got)
            assert got["booked_anything"] is False
            assert got["audit_id"]
        audit = [dict(r) for r in await conn.fetch(
            "SELECT outcome, refusal, operator, authenticated_by "
            "  FROM bettor_funded_correction_audit "
            " WHERE intent_id LIKE 'fpi-learns-%' ORDER BY audit_id")]
        assert [a["refusal"] for a in audit] == [w for _, w in refusals]
        assert {a["outcome"] for a in audit} == {"REFUSED"}
        # WHICH factors were verified is recorded; never a factor itself
        assert set(json.loads(audit[0]["authenticated_by"])) == \
            set(FC.AUTH_FIELDS)
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_economics "
            " WHERE intent_id=$1 AND kind='SETTLEMENT_CORRECTION'",
            HEDGE) == 0

        # ── THE ACCEPTED CORRECTION ──────────────────────────────────
        t_corr = t0 + 240
        got = await _book(conn, recheck_id=rid, seen_recheck_sha=sha,
                          now=t_corr)
        assert got["ok"] is True and got["already"] is False, got
        cid, eid = FC.correction_id_for(HEDGE, rid), FC.event_id_for(HEDGE,
                                                                     rid)
        assert eid == "fev:%s:SETTLEMENT_CORRECTION:%d" % (HEDGE, rid)
        # THE DELTA: the corrected payout (10 long contracts at 1.0 pay 10.0)
        # minus the booked one (0.0)
        assert got["effect"]["delta_usd"] == pytest.approx(10.0)
        econ = [dict(r) for r in await conn.fetch(
            "SELECT event_id, kind, amount_usd::float8 AS amt "
            "  FROM bettor_funded_economics WHERE intent_id=$1 "
            " ORDER BY kind", HEDGE)]
        assert econ == [
            {"event_id": "fev:%s:SETTLEMENT:REPORTED_SETTLEMENT" % HEDGE,
             "kind": "SETTLEMENT", "amt": 0.0},
            {"event_id": eid, "kind": "SETTLEMENT_CORRECTION", "amt": 10.0}]
        row = dict(await conn.fetchrow(
            "SELECT * FROM bettor_funded_settlement_corrections "
            " WHERE correction_id=$1", cid))
        assert row["recheck_id"] == rid and row["operator"] == OPERATOR
        assert row["from_reading"] == "REPORTED_SETTLEMENT"
        assert float(row["from_price"]) == 0.0
        assert float(row["to_price"]) == 1.0
        assert row["seen_recheck_sha"] == sha
        # THE CORRECTED READING IS NOW THE BOOKED ONE, THE ORIGINAL KEPT
        s = json.loads(await conn.fetchval(
            "SELECT settlement::text FROM bettor_funded_intents "
            " WHERE intent_id=$1", HEDGE))
        assert s["payout_price"] == 1.0 and s["payout_usd"] == 10.0
        assert s["at"] == pytest.approx(t_corr)
        assert s["corrected_by"]["correction_id"] == cid
        assert s["history"][0]["payout_price"] == 0.0
        assert s["history"][0]["at"] == booked_hedge["at"]
        # THE AUDIT ROW, LAST, NAMES THE EFFECTS
        acc = dict(await conn.fetchrow(
            "SELECT * FROM bettor_funded_correction_audit "
            " WHERE audit_id=$1", got["audit_id"]))
        assert acc["outcome"] == "ACCEPTED" and acc["refusal"] is None
        assert json.loads(acc["effect"])["economics_event_id"] == eid
        assert row["audit_id"] == got["audit_id"]
        # A NEW OUTCOME VERSION, saying what it corrects
        ov = [dict(r) for r in await conn.fetch(
            "SELECT version, realised_net_usd::float8 AS net, "
            "       supersedes_version, correction_reason "
            "  FROM bettor_funded_decision_outcomes WHERE decision_id=$1 "
            " ORDER BY version", dec)]
        assert [o["version"] for o in ov] == [1, 2]
        assert ov[1]["net"] == pytest.approx(ov[0]["net"] + 10.0)
        assert ov[1]["supersedes_version"] == 1
        assert cid in ov[1]["correction_reason"]
        assert [o["outcome_version"] for o in
                got["effect"]["outcome_versions"]] == [2]

        # ── THE CONTEST LIFTS ────────────────────────────────────────
        assert (await FA.account_selection(conn, ACCT))["ok"] is True
        assert (await FC.listing(conn, intent_id=HEDGE))[
            "awaiting_correction"] == []

        # ── THE LABEL READS THE CORRECTED PRICE, and the old fit is gone ─
        lab = await FMD.labelled(conn, decision_ids=[dec])
        assert lab["n"] == 1
        assert lab["labels"] == [1.0]         # both legs' sides now won
        hedge_leg = [lg for lg in lab["leg_outcomes"][0]
                     if lg["leg_role"] == "HEDGE"][0]
        assert float(hedge_leg["payout_price"]) == 1.0
        # the corrected label became known at the correction, not the close
        assert lab["outcome_available_at"][0] == pytest.approx(t_corr)
        gone = await FMD.approved(conn)
        assert gone["ok"] is False
        assert gone["verification"]["refusal"] == \
            FMD.R_TRAINING_RECORDS_DO_NOT_REPRODUCE
        w = await FMD.withdraw_invalidated(conn)
        assert w["withdrawn"] is True, w
        assert await conn.fetchval(
            "SELECT state FROM bettor_funded_models WHERE model_id=$1",
            "mdl:correction") == "RETIRED"

        # ── THE SAME REQUEST AGAIN: THE SAME CORRECTION ──────────────
        again = await _book(conn, recheck_id=rid, seen_recheck_sha=sha,
                            now=t_corr + 5)
        assert again["ok"] is True and again["already"] is True, again
        assert again["correction"]["correction_id"] == cid
        assert again["booked_anything"] is False
        # ── A DIFFERENT ONE FOR THE ANSWERED RE-READ: REFUSED ────────
        other = await _book(conn, recheck_id=rid,
                            seen_recheck_sha=FC.recheck_sha(agrees))
        assert other["ok"] is False
        assert other["refusal"] == FC.R_CORRECTED_DIFFERENTLY
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_economics "
            " WHERE intent_id=$1 AND kind='SETTLEMENT_CORRECTION'",
            HEDGE) == 1
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_economics "
            " WHERE intent_id=$1 AND kind='SETTLEMENT'", HEDGE) == 1
        outcomes = [r["outcome"] for r in await conn.fetch(
            "SELECT outcome FROM bettor_funded_correction_audit "
            " WHERE intent_id=$1 ORDER BY audit_id DESC LIMIT 3", HEDGE)]
        assert outcomes == ["REFUSED", "ALREADY_CORRECTED", "ACCEPTED"]

        # ── A LATER RE-READ THAT DISAGREES WITH THE CORRECTION ───────
        # contests again: the venue now says 0.0, the corrected book says 1.0
        corrected = dict(booked, **{HEDGE_SLUG: 1.0})
        await FM.recheck_settlements(
            conn, now=t_corr + 60, **common,
            probe=_probe(corrected, overrides={HEDGE_SLUG: 0.0}))
        later = (await _rechecks(conn))[-1]
        assert later["verdict"] == "DISAGREES"
        assert float(later["booked_payout_price"]) == 1.0
        assert (await FA.account_selection(conn, ACCT))["refusal"] == \
            FA.R_SETTLEMENT_CONTESTED
        assert (await FMD.labelled(conn, decision_ids=[dec]))["n"] == 0

        # ── AND THE RECORDS ARE APPEND-ONLY ──────────────────────────
        with pytest.raises(asyncpg.exceptions.RaiseError):
            await conn.execute(
                "UPDATE bettor_funded_settlement_corrections SET delta_usd=0 "
                " WHERE correction_id=$1", cid)
        with pytest.raises(asyncpg.exceptions.RaiseError):
            await conn.execute(
                "DELETE FROM bettor_funded_settlement_corrections "
                " WHERE correction_id=$1", cid)
        with pytest.raises(asyncpg.exceptions.RaiseError):
            await conn.execute(
                "UPDATE bettor_funded_correction_audit SET outcome='ACCEPTED'"
                " WHERE intent_id=$1", HEDGE)
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_correction_to_a_void_returns_the_remaining_basis_and_drops_the_label():
    """THE CLOSE'S VOID RULE: the collateral on the contracts held is
    returned (FB.remaining_basis), and a void is no label."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    base = BASE + 200
    hedge = "fpi-learns-%d-h" % base
    slug = "slug-learns-%d-HEDGE" % base
    try:
        await _clean(conn)
        decided = datetime.now(timezone.utc).replace(microsecond=0)
        await LRN._resolved_decision(
            conn, i=base, decided_at=decided, middle_occurred=False,
            features=LRN._synthetic(1)[0][0], account=ACCT)
        await _account_row(conn)
        await _as_closed_by_the_venue(conn, hedge)
        # THE ENTRY FILL the remaining basis is computed from: 10 at 0.50
        await conn.execute(
            "INSERT INTO bettor_funded_fills (fill_id, intent_id, "
            " venue_order_id, venue_fill_id, at, qty, price, cash_usd, "
            " fee_usd, fee_basis, direction) VALUES ($1,$2,'vo-c','vf-c',"
            " now(),10,0.5,5.0,0,'TEST','ENTRY')", "fvf:vo-c:vf-c", hedge)
        booked = await _booked(conn)
        await FM.recheck_settlements(
            conn, account_id=ACCT, venue=LRN.VENUE, per_pass=1000,
            now=decided.timestamp() + 7200,
            probe=_probe(booked, void={slug}))
        rc = (await _rechecks(conn, hedge))[-1]
        assert rc["verdict"] == "DISAGREES" and \
            rc["venue_reading"] == "EXPLICIT_VOID"
        got = await FC.book_settlement_correction(
            conn, intent_id=hedge, recheck_id=int(rc["recheck_id"]),
            operator=OPERATOR, statement=STATEMENT, confirm=hedge,
            seen_recheck_sha=FC.recheck_sha(rc), auth=AUTH)
        assert got["ok"] is True, got
        assert got["effect"]["delta_usd"] == pytest.approx(5.0)
        assert got["effect"]["to"] == {"terminal_reading": "EXPLICIT_VOID",
                                       "payout_price": None,
                                       "payout_usd": 5.0}
        lab = await FMD.labelled(conn, decision_ids=["dec:learns-%d" % base])
        assert lab["n"] == 0             # a void is no observation
        assert (await FA.account_selection(conn, ACCT))["ok"] is True
    finally:
        await _clean(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# PURE PIECES
# ═════════════════════════════════════════════════════════════════════

def test_the_payout_is_side_aware_including_a_short_paid_at_zero():
    L, S = "ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT"
    assert FC.settlement_payout(10, 1.0, L) == pytest.approx(10.0)
    assert FC.settlement_payout(10, 0.0, L) == pytest.approx(0.0)
    assert FC.settlement_payout(10, 1.0, S) == pytest.approx(0.0)
    # `cash_for` answers 0 at a long-side price of 0 whatever the side; a
    # SHORT is then paid in full, and the correction books that
    assert FC.settlement_payout(10, 0.0, S) == pytest.approx(10.0)
    assert FC.settlement_payout(10, 0.5, S) == pytest.approx(5.0)
    for bad in (-0.1, 1.1, float("nan")):
        with pytest.raises(ValueError):
            FC.settlement_payout(10, bad, L)


def test_the_recheck_sha_is_the_same_from_the_database_or_a_listing():
    from datetime import timedelta
    at = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
    db = {"recheck_id": 7, "intent_id": "fpi-x", "read_at": at,
          "booked_reading": "REPORTED_SETTLEMENT",
          "booked_payout_price": Decimal("0"),
          "venue_reading": "REPORTED_SETTLEMENT",
          "venue_payout_price": Decimal("1"), "verdict": "DISAGREES"}
    listed = dict(db, read_at=at.timestamp(), booked_payout_price=0.0,
                  venue_payout_price=1.0)
    assert FC.recheck_sha(db) == FC.recheck_sha(listed)
    assert FC.recheck_sha(db) != FC.recheck_sha(
        dict(db, read_at=at + timedelta(seconds=1)))
    assert FC.recheck_sha(db) != FC.recheck_sha(
        dict(db, venue_payout_price=Decimal("0.5")))


def test_the_consumers_count_only_unanswered_disagreements():
    assert "bettor_funded_settlement_corrections" in FA.CONTESTED_SQL
    assert "c.recheck_id = newest.recheck_id" in FA.CONTESTED_SQL
    assert "bettor_funded_settlement_corrections" in FMD.LABEL_SQL


# ═════════════════════════════════════════════════════════════════════
# THE ROUTE: TWO FACTORS, AND THE CONFIGURED IDENTITY
# ═════════════════════════════════════════════════════════════════════

class _Cfg:
    admin_token = "admin-secret-for-the-correction-test"
    desk_password = "desk"
    operator_password = "operator"
    command_read_password = "desk"
    funded_resolution_key = ""
    funded_resolution_operator = ""


ROUTE = "/api/admin/funded-settlement-corrections/fpi-x"


@pytest.fixture()
def client(monkeypatch):
    starlette = pytest.importorskip("starlette.testclient")
    from sportsassets.api import app as A
    cfg = _Cfg()
    monkeypatch.setattr(A, "settings", lambda: cfg, raising=False)
    return starlette.TestClient(A.app, raise_server_exceptions=False), cfg


def test_the_route_refuses_without_both_factors(client):
    c, cfg = client
    body = {"recheck_id": 1, "confirm": "fpi-x", "seen_recheck_sha": "x",
            "statement": STATEMENT}
    assert c.post(ROUTE, json=body).status_code == 401
    admin = {"X-Admin-Token": cfg.admin_token}
    r = c.post(ROUTE, json=body, headers=admin)
    assert r.status_code == 503
    assert r.json()["detail"]["reason"] == \
        "FUNDED_RESOLUTION_KEY_NOT_CONFIGURED"
    cfg.funded_resolution_key = "the-owners-resolution-key"
    assert c.post(ROUTE, json=body, headers=admin).status_code == 503
    cfg.funded_resolution_operator = OPERATOR
    r = c.post(ROUTE, json=body, headers=dict(admin, **{
        "X-Resolution-Key": "a-guess"}))
    assert r.status_code == 401
    r = c.post(ROUTE, json=body, headers={
        "X-Resolution-Key": cfg.funded_resolution_key})
    assert r.status_code == 401
    # THE LISTING needs the admin token too
    assert c.get("/api/admin/funded-settlement-corrections"
                 ).status_code == 401


def test_the_route_takes_the_operator_from_configuration_not_the_body():
    from sportsassets.api import app as A
    route = [r for r in A.app.routes if getattr(r, "path", "") ==
             "/api/admin/funded-settlement-corrections/{intent_id}"][0]
    deps = {d.call.__name__ for d in route.dependant.dependencies}
    assert {"require_admin", "require_resolution_key"} <= deps
    src = inspect.getsource(A.admin_book_funded_settlement_correction)
    assert "operator=auth[\"operator\"]" in src
    assert 'b.get("operator")' not in src
    assert '"funded_resolution_operator"' in inspect.getsource(
        A._resolution_auth)
