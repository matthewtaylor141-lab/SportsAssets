"""A SETTLEMENT THE VENUE CORRECTS IS SEEN, AND WHAT RESTED ON IT STOPS.

The gap (review of 98f9aef): a funded leg was closed once, on the venue's
reading at that instant, and never read again. `approved` and the scheduled
pass enforced "an approval is only as good as its records" -- but in
production nothing would ever change those records, so a venue correction
could not reach an approved model trained on the old label.

Migration 141 and `bettor_funded_management.recheck_settlements` re-read
recently settled legs through the same probe and bar the close used, and
record each re-read. Here, with the production functions and a substituted
probe (NO VENUE IS CONTACTED, NO ORDER IS SENT):

  * re-reads that agree change nothing; re-reads that establish nothing
    change nothing;
  * one disagreement removes that group from the labelled set, so the
    approved model's records no longer reproduce: `approved` refuses by name,
    servicing is untouched, and the scheduled withdrawal retires the model;
  * the booked settlement and the economics rows are NOT rewritten;
  * the account is refused new exposure while the leg's newest established
    re-read disagrees, and a venue that reverts clears it.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from sportsassets import bettor_funded_activation as FA
from sportsassets import bettor_funded_management as FM
from sportsassets import bettor_funded_model as FMD
from tests import test_the_funded_lane_actually_learns as LRN

DSN = LRN.DSN
pytestmark = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

ACCT = LRN.ACCT_LOW
DESK = "desk-settlement-recheck-test"
BASE = 5600


async def _purge_rechecks(conn):
    """Re-reads are append-only by trigger; a test removes its own with
    triggers suspended for ITS transaction only."""
    async with conn.transaction():
        await conn.execute("SET LOCAL session_replication_role = replica")
        await conn.execute(
            "DELETE FROM bettor_funded_settlement_rechecks WHERE intent_id "
            " LIKE 'fpi-learns-%'")


async def _clean(conn):
    await _purge_rechecks(conn)
    await conn.execute("DELETE FROM bettor_desk_accounts WHERE account_id=$1",
                       ACCT)
    await LRN._clean(conn)


def _probe_from(booked: dict, *, overrides=None, unreadable=()):
    """The probe's shape, answering from a slug -> long-side price map."""
    overrides = dict(overrides or {})

    def probe(client, slug):
        if slug in unreadable:
            return {"terminal_reading": "UNREADABLE", "why": "substituted"}
        px = overrides.get(slug, booked.get(slug))
        return {"terminal_reading": "REPORTED_SETTLEMENT",
                "authoritative_payout_present": True,
                "reader_verdict": {"status": "RESOLVED",
                                   "corroboration": "CORROBORATED",
                                   "settlement_price": px,
                                   "settlement_price_raw": str(px)}}
    return probe


@pytest.mark.asyncio
async def test_a_corrected_settlement_is_seen_withdraws_the_model_and_stops_new_exposure():
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        rows, _ = LRN._synthetic(50)
        t_fit = datetime.now(timezone.utc)
        fitted = await LRN._fit_on_records(
            conn, rows=rows, labels=[0.0] * 45 + [1.0] * 5, before=t_fit,
            base=BASE, account=ACCT, estimator="BASE_RATE")
        await FMD.register(conn, model_id="mdl:recheck", model_version="v-r",
                           fitted=fitted, fit_through=t_fit)
        await conn.execute(
            "UPDATE bettor_funded_models SET state='APPROVED', "
            "  approved_at=now(), approved_by='owner@test', "
            "  evaluation='{\"log_loss\": 0.5}'::jsonb WHERE model_id=$1",
            "mdl:recheck")
        await conn.execute(
            "INSERT INTO bettor_desk_accounts (account_id, desk_id, status, "
            " opening_balance, opened_at, note, provenance, paused, "
            " pause_reason, accounting_status, accounting_detail) VALUES "
            " ($1,$2,'ACTIVE',0,now(),'settlement re-read test','{}'::jsonb,"
            "  FALSE,NULL,'CLEAN','{}'::jsonb)", ACCT, DESK)
        assert (await FMD.approved(conn))["ok"] is True
        assert (await FA.account_selection(conn, ACCT))["ok"] is True

        booked = {r["us_market_slug"]: float(json.loads(r["settlement"])
                                              ["payout_price"])
                  for r in await conn.fetch(
                      "SELECT us_market_slug, settlement::text AS settlement "
                      "  FROM bettor_funded_intents WHERE account_id=$1",
                      ACCT)}
        settlement_before = {r["intent_id"]: r["s"] for r in await conn.fetch(
            "SELECT intent_id, settlement::text AS s FROM "
            " bettor_funded_intents WHERE account_id=$1", ACCT)}
        econ_before = await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_economics WHERE intent_id IN "
            "(SELECT intent_id FROM bettor_funded_intents WHERE account_id=$1)",
            ACCT)
        common = dict(account_id=ACCT, venue=LRN.VENUE,
                      now=t_fit.timestamp() + 60, per_pass=1000)

        # ── AGREEING AND UNESTABLISHED RE-READS CHANGE NOTHING ────────
        hedge = "slug-learns-%d-HEDGE" % BASE
        got = await FM.recheck_settlements(
            conn, probe=_probe_from(booked, unreadable={hedge}), **common)
        assert got["ok"] is True and got["disagreements"] == 0, got
        verdicts = {r["verdict"] for r in got["rechecked"]}
        assert verdicts == {FM.VERDICT_AGREES, FM.VERDICT_NOT_ESTABLISHED}
        assert len(got["rechecked"]) == 100
        assert (await FMD.approved(conn))["ok"] is True
        assert (await FA.account_selection(conn, ACCT))["ok"] is True

        # ── THE VENUE NOW PAYS THE HEDGE LEG IT FIRST SETTLED AS A LOSS ──
        assert booked[hedge] == 0.0
        got = await FM.recheck_settlements(
            conn, probe=_probe_from(booked, overrides={hedge: 1.0}), **common)
        assert got["disagreements"] == 1, got
        row = await conn.fetchrow(
            "SELECT * FROM bettor_funded_settlement_rechecks WHERE intent_id="
            "$1 AND verdict='DISAGREES'", "fpi-learns-%d-h" % BASE)
        assert float(row["booked_payout_price"]) == 0.0
        assert float(row["venue_payout_price"]) == 1.0

        # ── THE MODEL THAT RESTED ON IT STOPS PRICING, BY NAME ────────
        gone = await FMD.approved(conn)
        assert gone["ok"] is False
        assert gone["refusal"] == FMD.R_APPROVED_MODEL_EVIDENCE_INVALIDATED
        assert gone["verification"]["refusal"] == \
            FMD.R_TRAINING_RECORDS_DO_NOT_REPRODUCE
        w = await FMD.withdraw_invalidated(conn)
        assert w["withdrawn"] is True, w
        assert await conn.fetchval(
            "SELECT state FROM bettor_funded_models WHERE model_id=$1",
            "mdl:recheck") == "RETIRED"

        # ── THE BOOKED RECORD IS NOT REWRITTEN BY A RE-READ ───────────
        assert {r["intent_id"]: r["s"] for r in await conn.fetch(
            "SELECT intent_id, settlement::text AS s FROM "
            " bettor_funded_intents WHERE account_id=$1", ACCT)} == \
            settlement_before
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_economics WHERE intent_id IN "
            "(SELECT intent_id FROM bettor_funded_intents WHERE account_id=$1)",
            ACCT) == econ_before

        # ── NO NEW EXPOSURE ON THE ACCOUNT WHILE IT IS CONTESTED ──────
        sel = await FA.account_selection(conn, ACCT)
        assert sel["ok"] is False
        assert sel["refusal"] == FA.R_SETTLEMENT_CONTESTED
        assert [c["intent_id"] for c in sel["contested"]] == \
            ["fpi-learns-%d-h" % BASE]
        # a later re-read that establishes nothing does not clear it...
        await FM.recheck_settlements(
            conn, probe=_probe_from(booked, unreadable=set(booked)),
            **dict(common, now=common["now"] + 60))
        assert (await FA.account_selection(conn, ACCT))["refusal"] == \
            FA.R_SETTLEMENT_CONTESTED
        # ...and a venue that reverts to the booked reading does
        await FM.recheck_settlements(
            conn, probe=_probe_from(booked),
            **dict(common, now=common["now"] + 120))
        assert (await FA.account_selection(conn, ACCT))["ok"] is True

        # ── BUT THE LABEL STAYS OUT: it was contested once ─────────────
        lab = await FMD.labelled(conn, decision_ids=list(
            fitted["training_provenance"]["decision_ids"]))
        assert lab["n"] == 49
        # ── AND THE RECORD IS APPEND-ONLY ──────────────────────────────
        with pytest.raises(asyncpg.exceptions.RaiseError):
            await conn.execute(
                "UPDATE bettor_funded_settlement_rechecks SET verdict="
                "'AGREES' WHERE verdict='DISAGREES' AND intent_id=$1",
                "fpi-learns-%d-h" % BASE)
    finally:
        await _clean(conn)
        await conn.close()


def test_the_verdict_is_the_authoritative_bar_and_nothing_less():
    booked = {"terminal_reading": "REPORTED_SETTLEMENT", "payout_price": 1.0}
    same = {"terminal_reading": "REPORTED_SETTLEMENT",
            "reader_verdict": {"settlement_price": 1.0}}
    assert FM.recheck_verdict(booked, same)["verdict"] == FM.VERDICT_AGREES
    moved = {"terminal_reading": "REPORTED_SETTLEMENT",
             "reader_verdict": {"settlement_price": 0.0}}
    assert FM.recheck_verdict(booked, moved)["verdict"] == FM.VERDICT_DISAGREES
    void = {"terminal_reading": "EXPLICIT_VOID"}
    assert FM.recheck_verdict(booked, void)["verdict"] == FM.VERDICT_DISAGREES
    # OUR OWN INFERENCE, AN UNREADABLE READ, OR A PRICELESS REPORT: nothing
    for got in ({"terminal_reading": "CONVERGED_PRICE_INFERENCE"},
                {"terminal_reading": "UNREADABLE"}, {},
                {"terminal_reading": "REPORTED_SETTLEMENT",
                 "reader_verdict": {"settlement_price": None}}):
        assert FM.recheck_verdict(booked, got)["verdict"] == \
            FM.VERDICT_NOT_ESTABLISHED, got


def test_the_servicing_pass_re_reads_before_it_ranks_or_learns():
    """THE WIRING: `_funded_service` calls the re-read before the pair pass
    and the learning pass, so both see a correction the cycle it is found."""
    import inspect

    from sportsassets.workers import ext_pinnacle_loop as L
    src = inspect.getsource(L._funded_service)
    i = src.index("_FM.recheck_settlements(")
    assert i < src.index("_PC.pass_once(")
    assert i < src.index("scheduled_learning_pass")
