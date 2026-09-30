"""PROOF 14: A SETTLEMENT CORRECTION PRODUCES A VERSIONED AUDIT CHANGE AND
INVALIDATES WHAT RESTED ON IT -- THROUGH THE EXISTING PATH.

The ledger and the model are the ones `test_a_settlement_correction_is_
booked_by_an_audited_decision` builds (SYNTHETIC training data from a
stated rule; a substituted settlement probe; NO VENUE IS CONTACTED, NO
ORDER IS SENT). The re-reads go through `bettor_funded_management.
recheck_settlements` and the correction through `bettor_funded_corrections.
book_settlement_correction` -- the production functions.

  1. Audrey audits the day: v1, the corrected leg's position FINALIZED.
  2. The venue's re-read DISAGREES: the rerun writes v2 (it supersedes v1),
     the position is CONTESTED, the gap is named, and the approved model
     whose training records rested on the old label is RETIRED by
     `bettor_funded_model.withdraw_invalidated` -- the existing rule.
  3. The correction is booked: the rerun writes v3, the position CORRECTED,
     its realised figure carrying the booked delta, the contest gone.
  4. The same evidence again writes nothing; v1 and v2 are unchanged.
"""
from __future__ import annotations

import datetime as dt

import pytest

from sportsassets import bettor_funded_management as FM
from sportsassets import bettor_funded_model as FMD
from sportsassets.agents import audrey_audit as AA
from tests import audrey_helpers as H
from tests import test_a_settlement_correction_is_booked_by_an_audited_decision as SC  # noqa: E501
from tests import test_the_funded_lane_actually_learns as LRN

pg = H.pg
MODEL_ID = "mdl:audrey-correction"
NY = "America/New_York"


@pytest.fixture(autouse=True)
def _tz(monkeypatch):
    monkeypatch.setenv(AA.TZ_ENV, NY)


def _state_of(rep: dict, group: str) -> str:
    rows = {r["position"]: r for r in rep["positions"]["rows"]}
    return rows[group]["state"], rows[group]


@pg
async def test_proof14_a_correction_versions_the_audit_and_retires_the_model():
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(H.DSN)
    report_rid = None
    try:
        if not await AA.has_schema(conn):
            pytest.skip("migration 155 is not in this database")
        await H.ensure_core_tables(conn)
        await H.purge(conn)
        await SC._clean(conn)
        rows, _ = LRN._synthetic(50)
        t_fit = dt.datetime.now(dt.timezone.utc)
        fitted = await LRN._fit_on_records(
            conn, rows=rows, labels=[0.0] * 45 + [1.0] * 5, before=t_fit,
            base=SC.BASE, account=SC.ACCT, estimator="BASE_RATE")
        await FMD.register(conn, model_id=MODEL_ID, model_version="v-ac",
                           fitted=fitted, fit_through=t_fit)
        await conn.execute(
            "UPDATE bettor_funded_models SET state='APPROVED', "
            "  approved_at=now(), approved_by='owner@test', "
            "  evaluation='{\"log_loss\": 0.5}'::jsonb WHERE model_id=$1",
            MODEL_ID)
        await SC._account_row(conn)
        booked = await SC._as_closed_by_the_venue(conn, SC.HEDGE)
        assert booked["payout_price"] == 0.0
        group = await conn.fetchval(
            "SELECT portfolio_group_id FROM bettor_funded_intents "
            " WHERE intent_id=$1", SC.HEDGE)
        created = await conn.fetchval(
            "SELECT extract(epoch FROM created_at)::float8 FROM "
            " bettor_funded_intents WHERE intent_id=$1", SC.HEDGE)
        name, tz, _ = AA.audit_timezone()
        day = AA.local_day(created, tz)
        _, end = AA.day_bounds(day, tz)
        now = end + 3600.0
        report_rid = AA.report_id_for(day, name)
        await H.purge_report(conn, report_rid)

        # ── 1 · THE FIRST AUDIT ───────────────────────────────────────
        v1 = await AA.audit_day(conn, day=day, now=now)
        assert v1["written"] and v1["version"] == 1
        st, row = _state_of(v1["report"], group)
        assert st == AA.S_FINAL
        before_net = row["realised_net_usd"]
        models = {m["model_id"]: m for m in v1["report"]["versions"][
            "models"]}
        assert models[MODEL_ID]["state"] == "APPROVED"

        # ── 2 · THE VENUE DISAGREES ───────────────────────────────────
        t0 = t_fit.timestamp() + 60
        common = dict(account_id=SC.ACCT, venue=LRN.VENUE, per_pass=1000)
        prices = await SC._booked(conn)
        up = {SC.HEDGE_SLUG: 1.0}
        await FM.recheck_settlements(conn, now=t0, **common,
                                     probe=SC._probe(prices, overrides=up))
        r2 = (await SC._rechecks(conn))[-1]
        assert r2["verdict"] == "DISAGREES"
        v2 = await AA.audit_day(conn, day=day, now=now + 60)
        assert v2["written"] and v2["version"] == 2
        assert v2["change"]["supersedes"] == 1
        st, _ = _state_of(v2["report"], group)
        assert st == AA.S_CONTESTED
        gaps = {g["gap"] for g in v2["report"]["data_quality"]["gaps"]}
        assert "SETTLEMENT_CONTESTED_UNANSWERED" in gaps
        # THE EXISTING INVALIDATION PATH retired the approved model
        assert await conn.fetchval(
            "SELECT state FROM bettor_funded_models WHERE model_id=$1",
            MODEL_ID) == "RETIRED"
        reason = await conn.fetchval(
            "SELECT retired_reason FROM bettor_funded_models "
            " WHERE model_id=$1", MODEL_ID)
        assert reason.startswith(FMD.RETIRED_EVIDENCE_INVALIDATED)
        assert [w["model_id"] for w in v2["report"]["run"][
            "model_withdrawals"]] == [MODEL_ID]
        m2 = {m["model_id"]: m for m in v2["report"]["versions"]["models"]}
        assert m2[MODEL_ID]["state"] == "RETIRED"
        assert "versions" in v2["change"]["changed_sections"]

        # ── 3 · THE CORRECTION IS BOOKED ──────────────────────────────
        rid = int(r2["recheck_id"])
        sha = SC.FC.recheck_sha(r2)
        got = await SC._book(conn, recheck_id=rid, seen_recheck_sha=sha,
                             now=t0 + 240)
        assert got["ok"] is True, got
        v3 = await AA.audit_day(conn, day=day, now=now + 120)
        assert v3["written"] and v3["version"] == 3
        assert v3["change"]["supersedes"] == 2
        st, row = _state_of(v3["report"], group)
        assert st == AA.S_CORRECTED
        assert row["corrections"] == [SC.FC.correction_id_for(SC.HEDGE,
                                                               rid)]
        assert row["realised_net_usd"] == pytest.approx(before_net + 10.0)
        gaps = {g["gap"] for g in v3["report"]["data_quality"]["gaps"]}
        assert "SETTLEMENT_CONTESTED_UNANSWERED" not in gaps
        # nothing restores the retired model: it stays retired
        assert await conn.fetchval(
            "SELECT state FROM bettor_funded_models WHERE model_id=$1",
            MODEL_ID) == "RETIRED"

        # ── 4 · THE SAME EVIDENCE AGAIN, AND THE HISTORY INTACT ───────
        again = await AA.audit_day(conn, day=day, now=now + 180)
        assert again["written"] is False and again["version"] == 3
        hist = await conn.fetch(
            "SELECT version, supersedes_version FROM audrey_audit_reports "
            " WHERE report_id=$1 ORDER BY version", v1["report_id"])
        assert [(h["version"], h["supersedes_version"]) for h in hist] == [
            (1, None), (2, 1), (3, 2)]
        first = await AA.report(conn, v1["report_id"], version=1)
        assert first["evidence_sha"] == v1["evidence_sha"]
        st, _ = _state_of(first["report"], group)
        assert st == AA.S_FINAL
    finally:
        if report_rid:
            await H.purge_report(conn, report_rid)
        await SC._clean(conn)
        await conn.execute("DELETE FROM bettor_funded_models "
                           " WHERE model_id=$1", MODEL_ID)
        await H.purge(conn)
        await conn.close()
