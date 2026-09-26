"""THE ACTIVATION PATH IS COMPLETE, AND CAPITAL IS STILL OFF.

WHAT WAS WRONG, AND IT WAS A FAIR CRITICISM OF MY OWN WORK. `activate()`
refused unconditionally; three readiness checks were hard-coded `met: False`;
and the account and limit submissions were proposals no enforcement path
read. A panel that cannot be wrong establishes nothing.

WHAT IS ASSERTED HERE:

  THE POSITIVE PATH EXISTS AND IS REACHED. With the evidence present, a
  clean canonical account, an owner-approved limit set inside the frozen
  rails and a TEST-class venue, `authorize` returns
  AUTHORIZED_FOR_A_TEST_VENUE -- and funded submission is still disabled.

  EVERY NEGATIVE PATH IS REACHED BY ITS OWN NAME: unknown venue, unknown
  account, closed account, PAUSED account, uncertain accounting, missing
  limits, unapproved limits, unmet readiness, and -- with everything met --
  a FUNDED venue still refusing for want of the owner's authorisation.

  NO CHECK IS A CONSTANT. Each of the four that used to be hard-coded is
  driven from rows: with no rows it is UNKNOWN (and UNKNOWN blocks), with
  refusing rows it is FALSE, with qualifying rows it is TRUE. All three
  states are exercised.

  THE PAUSED ACCOUNT IS PROTECTED BY ITS ID, not its display name: it is
  renamed mid-test and stays refused.

  RESEARCH-WAIVED AND DEMONSTRATION ACTIVITY DO NOT QUALIFY: an admission
  carrying the calibration waiver leaves the check FALSE, and only an
  unwaived admission turns it TRUE.

  APPROVED LIMITS ARE ENFORCED, NOT STORED: they tighten the rails the
  sizing path actually reads, and a looser number is ignored.
"""

from __future__ import annotations

import json
import os

import pytest

from sportsassets import bettor_entry_execution as EX
from sportsassets import bettor_entry_inventory as inv
from sportsassets import bettor_external_shadow as ext
from sportsassets import bettor_funded_activation as FA

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="RN1X_TEST_DSN is not set")

PAUSED = "acct-paused-accounting-uncertain"
CLEAN = "acct-pilot-test-001"
CLOSED = "acct-closed-001"


# ── pure: the enforced-limit rule ───────────────────────────────────

def test_an_approval_can_only_tighten_a_rail():
    tight = EX.effective_limits({"capital_usd": 250, "per_order_usd": 25,
                                 "max_exposure_usd": 100,
                                 "daily_loss_stop_usd": 50})
    assert tight["effective"]["MAX_MARKET_EXPOSURE"] == 25.0
    assert tight["frozen"]["MAX_MARKET_EXPOSURE"] == 1000.0
    assert tight["effective_digest"] != tight["frozen_digest"]
    assert set(tight["tightened"]) == {"MAX_CAPITAL_DEPLOYED",
                                      "MAX_MARKET_EXPOSURE",
                                      "MAX_CORRELATED_EXPOSURE",
                                      "MAX_DRAWDOWN"}
    loose = EX.effective_limits({"capital_usd": 9e9, "per_order_usd": 9e9,
                                 "max_exposure_usd": 9e9,
                                 "daily_loss_stop_usd": 9e9})
    assert loose["effective"] == loose["frozen"], "an approval never raises"
    assert set(loose["ignored_because_looser"]) == set(FA.REQUIRED_LIMITS)
    assert EX.effective_limits()["effective"] == dict(EX.PREDECLARED_LIMITS)
    # AND THE FROZEN DIGEST IS UNMOVED BY ANY OF IT.
    assert loose["frozen_digest"] == EX.LIMITS_SHA == tight["frozen_digest"]


def test_the_approved_limits_reach_the_sizing_path():
    """THE ENFORCEMENT EVIDENCE. The same measurement function the lane
    sizes against must return the tightened headroom, so an approved
    per-order limit actually caps a position."""
    frozen = EX.headroom_from_rows([], condition_id="0xabc",
                                   event_key="e-1", now=1_790_000_000.0)
    approved = EX.headroom_from_rows(
        [], condition_id="0xabc", event_key="e-1", now=1_790_000_000.0,
        approved_limits={"capital_usd": 250, "per_order_usd": 25,
                         "max_exposure_usd": 100,
                         "daily_loss_stop_usd": 50})
    assert frozen["ok"] and approved["ok"]
    assert frozen["headroom"]["MAX_MARKET_EXPOSURE"] == 1000.0
    assert approved["headroom"]["MAX_MARKET_EXPOSURE"] == 25.0
    assert approved["frozen_limits"]["MAX_MARKET_EXPOSURE"] == 1000.0
    # AND THE QUANTITY CAP FALLS WITH IT -- the number that sizes a trade.
    # THE WHOLE MEASUREMENT GOES IN, not its inner dict: the sizing
    # function refuses a headroom it was not told is `ok`.
    cap_frozen = EX.qty_cap_from_headroom(
        frozen, worst_case_cost_per_contract=0.62)
    cap_appr = EX.qty_cap_from_headroom(
        approved, worst_case_cost_per_contract=0.62)
    assert cap_frozen["ok"] and cap_appr["ok"], (cap_frozen, cap_appr)
    assert cap_frozen["qty_cap"] > cap_appr["qty_cap"] > 0
    assert cap_appr["qty_cap"] <= 25.0 / 0.62 + 1e-6


def test_the_contract_says_what_it_cannot_do():
    d = FA.describe()
    assert d["authorises_capital"] is False
    assert d["research_waived_does_not_qualify"] is True
    assert d["demonstration_does_not_qualify"] is True
    assert d["approvals_can_only_tighten"] is True
    assert d["venue_classes"]["PMUS"] == FA.VENUE_FUNDED
    assert d["venue_classes"]["PMUS_TEST"] == FA.VENUE_TEST
    assert "CANONICAL account_id" in d["account_identity"]


# ── against a real database ─────────────────────────────────────────

async def _seed_accounts(conn):
    await conn.execute("DELETE FROM bettor_desk_accounts")
    await conn.execute(
        """
        INSERT INTO bettor_desk_accounts
          (account_id, desk_id, status, opening_balance, opened_at, note,
           provenance, paused, pause_reason, accounting_status,
           accounting_detail)
        VALUES
          ($1,'desk-1','ACTIVE',0, now(),'the accounting-uncertain account',
           '{}'::jsonb, TRUE,'accounting recovery defect','UNCERTAIN',
           '{}'::jsonb),
          ($2,'desk-2','ACTIVE',0, now(),'a clean test-venue pilot',
           '{}'::jsonb, FALSE, NULL,'CLEAN','{}'::jsonb),
          ($3,'desk-3','CLOSED_UNATTRIBUTABLE',0, now(),'a closed period',
           '{}'::jsonb, FALSE, NULL,'CLEAN','{}'::jsonb)
        """, PAUSED, CLEAN, CLOSED)


async def _record_and_approve_limits(conn, *, approved=True):
    from sportsassets import bettor_desk_controls as CTL

    got = await CTL.set_limits(conn, by="test", proposed={
        "capital_usd": 250, "per_order_usd": 25,
        "max_exposure_usd": 100, "daily_loss_stop_usd": 50})
    assert got["ok"], got
    if not approved:
        return got
    raw = await conn.fetchval(
        "SELECT value FROM ingestion_state WHERE key = $1", FA.LIMITS_KEY)
    rec = json.loads(raw)
    eff = EX.effective_limits(rec["proposed"])
    rec.update(approved=True, approved_by="OWNER", enforced=True,
               effective_when_approved=eff["effective"])
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
        FA.LIMITS_KEY, json.dumps(rec))
    return got


async def _seed_evidence(conn, *, waived: bool):
    """The rows the four derived checks read. Labelled as a fixture."""
    # a cycle whose book reads recorded an ESTABLISHED age basis
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
        "ext_pinnacle_last_cycle", json.dumps({
            "at": 1_790_000_000.0, "cycle_label": "EVALUATED_1_CANDIDATE",
            "mapped_candidate_ledger": [
                {"us_market_slug": "aec-x-2026-09-26",
                 "venue_clock": {"basis": "VENUE_TRANSACT_TIME_ESTABLISHED",
                                 "age_at_read_s": 1.2}}]}))
    risk = ({"research_waiver": {"authorised": True,
                                 "waived": ["MODEL_TRUST_DRIFT"]}}
            if waived else {"rails_failed": []})
    # AN ADMISSIBLE ROW IS COMPLETE, and the table's own CHECK says so:
    # probability, executable price, cost, edge, mapped outcome and an
    # observation instant, with decision = 'BUY'. The fixture supplies them
    # rather than working around the constraint.
    await conn.execute(
        """
        INSERT INTO external_valuations
          (experiment_id, version, source_class, provider, book, devig_method,
           venue, contract_selection, sport_family, market, raw_odds,
           outcomes_priced, expected_outcomes, condition_id, us_market_slug,
           probability, executable_price, cost_per_contract,
           estimated_edge_per_contract, mapped_outcome, observed_at,
           decision, admissible, refusals, why, decided_at, outcome_known,
           period, settlement_comparison, risk_verdict, order_submitted)
        VALUES ($1,'v','EXTERNAL_BOOKMAKER_VALUATION','PINNACLE','pinnacle','power','PMUS',
                'THE_TEST_FIXTURES_CHOSEN_CONTRACT','baseball','MONEYLINE',
                '{}'::jsonb, 2, 2, $2,'aec-x-2026-09-26',
                0.70, 0.62, 0.636, 0.064,'Chosen Side A', now(),
                'BUY', TRUE, ARRAY[]::text[],'a seeded fixture row', now(),
                FALSE,'FULL_GAME','{"verdict":"COMPATIBLE"}'::jsonb,
                $3::jsonb, FALSE)
        """, ext.EXPERIMENT_ID, "0x" + ("11" * 32), json.dumps(risk))


@pg
async def test_every_negative_path_is_reached_by_its_own_name():
    asyncpg = pytest.importorskip("asyncpg")

    conn = await asyncpg.connect(DSN)
    try:
        await _seed_accounts(conn)
        await conn.execute("DELETE FROM ingestion_state WHERE key = ANY($1)",
                           [FA.LIMITS_KEY, FA.ACCOUNT_KEY])

        # THE ACCOUNT, from the canonical registry
        assert (await FA.account_selection(conn, ""))["refusal"] == \
            FA.R_NO_ACCOUNT
        assert (await FA.account_selection(conn, "nope"))["refusal"] == \
            FA.R_ACCOUNT_UNKNOWN
        assert (await FA.account_selection(conn, CLOSED))["refusal"] == \
            FA.R_ACCOUNT_NOT_ACTIVE
        assert (await FA.account_selection(conn, PAUSED))["refusal"] == \
            FA.R_ACCOUNT_PAUSED
        assert (await FA.account_selection(conn, CLEAN))["ok"] is True

        # THE PAUSE IS ON THE ROW: renaming it changes nothing.
        await conn.execute(
            "UPDATE bettor_desk_accounts SET note = $2 WHERE account_id = $1",
            PAUSED, "Renamed Pilot Account")
        assert (await FA.account_selection(conn, PAUSED))["refusal"] == \
            FA.R_ACCOUNT_PAUSED
        # AND UNCERTAIN ACCOUNTING REFUSES EVEN WHEN NOT PAUSED.
        await conn.execute(
            "UPDATE bettor_desk_accounts SET paused = FALSE "
            "WHERE account_id = $1", PAUSED)
        assert (await FA.account_selection(conn, PAUSED))["refusal"] == \
            FA.R_ACCOUNTING_UNCERTAIN
        await conn.execute(
            "UPDATE bettor_desk_accounts SET paused = TRUE "
            "WHERE account_id = $1", PAUSED)

        # THE VENUE CLASS
        got = await FA.authorize(conn, account_id=CLEAN, venue="NOWHERE",
                                 by="test")
        assert got["refusal"] == FA.R_VENUE_UNKNOWN

        # THE LIMITS: absent, then recorded but unapproved
        got = await FA.authorize(conn, account_id=CLEAN, venue="PMUS_TEST",
                                 by="test")
        assert got["refusal"] == FA.R_LIMITS_MISSING
        await _record_and_approve_limits(conn, approved=False)
        got = await FA.authorize(conn, account_id=CLEAN, venue="PMUS_TEST",
                                 by="test")
        assert got["refusal"] == FA.R_LIMITS_NOT_APPROVED

        # THE READINESS, with no evidence at all: UNKNOWN, and it blocks
        await _record_and_approve_limits(conn, approved=True)
        await conn.execute("DELETE FROM ingestion_state WHERE key = $1",
                           "ext_pinnacle_last_cycle")
        await conn.execute("DELETE FROM external_valuations "
                           "WHERE experiment_id = $1", ext.EXPERIMENT_ID)
        r = await FA.readiness(conn, account_id=CLEAN)
        by = {c["check"]: c for c in r["checks"]}
        assert by["venue_book_freshness_basis"]["met"] is None
        assert by["settlement_compatibility"]["met"] is None
        assert by["market_scope_metadata"]["met"] is None
        assert r["unknown_count"] >= 3
        assert r["ready"] is False
        got = await FA.authorize(conn, account_id=CLEAN, venue="PMUS_TEST",
                                 by="test")
        assert got["refusal"] == FA.R_READINESS_UNMET

        # A WAIVED ADMISSION DOES NOT QUALIFY: the row exists, the check is
        # still FALSE, and the reason names the waiver.
        await _seed_evidence(conn, waived=True)
        r = await FA.readiness(conn, account_id=CLEAN)
        by = {c["check"]: c for c in r["checks"]}
        adm = by["an_autonomous_entry_was_admitted_unwaived"]
        assert adm["met"] is False, adm
        assert adm["evidence"]["admitted"] == 1
        assert adm["evidence"]["of_those_research_waived"] == 1
        assert adm["evidence"]["admitted_without_a_waiver"] == 0
        # and the other three DID turn true on the same evidence
        assert by["venue_book_freshness_basis"]["met"] is True
        assert by["settlement_compatibility"]["met"] is True
        assert by["market_scope_metadata"]["met"] is True
    finally:
        await conn.execute("DELETE FROM external_valuations "
                           "WHERE experiment_id = $1", ext.EXPERIMENT_ID)
        await conn.execute("DELETE FROM ingestion_state WHERE key = ANY($1)",
                           [FA.LIMITS_KEY, FA.ACCOUNT_KEY,
                            FA.AUTHORIZATION_KEY,
                            "ext_pinnacle_last_cycle"])
        await conn.close()


@pg
async def test_the_positive_path_authorises_a_test_venue_and_no_capital():
    asyncpg = pytest.importorskip("asyncpg")

    conn = await asyncpg.connect(DSN)
    try:
        await _seed_accounts(conn)
        await _record_and_approve_limits(conn, approved=True)
        await _seed_evidence(conn, waived=False)

        r = await FA.readiness(conn, account_id=CLEAN)
        assert r["ready"] is True, [c for c in r["unmet"]]
        for c in r["checks"]:
            assert c["met"] is True, c
            assert c["evidence"] is not None

        got = await FA.authorize(conn, account_id=CLEAN, venue="PMUS_TEST",
                                 by="test")
        assert got["ok"] is True, got
        assert got["verdict"] == "AUTHORIZED_FOR_A_TEST_VENUE"
        assert got["venue_class"] == FA.VENUE_TEST
        # CAPITAL IS NOT ENABLED BY ANY OF IT.
        assert got["funded_submission"] == "DISABLED"
        assert got["authorises_capital"] is False
        assert EX.REAL_ORDER_SUBMISSION_ENABLED is False
        assert EX.REAL_CAPITAL_AT_RISK == 0
        # THE EFFECTIVE LIMITS WERE RECORDED, tightened, with their digest.
        eff = got["applied"]["effective_limits"]
        assert eff["MAX_MARKET_EXPOSURE"] == 25.0
        assert got["applied"]["account_id"] == CLEAN
        # RECORDED UNDER ITS OWN KEY, not over the binding it read.
        back = await conn.fetchval(
            "SELECT value FROM ingestion_state WHERE key = $1",
            FA.AUTHORIZATION_KEY)
        assert json.loads(back)["venue_class"] == FA.VENUE_TEST
        assert got["recorded_under"] == FA.AUTHORIZATION_KEY

        # AND A FUNDED VENUE STILL REFUSES, with everything else met.
        funded = await FA.authorize(conn, account_id=CLEAN, venue="PMUS",
                                    by="test")
        assert funded["ok"] is False
        assert funded["refusal"] == FA.R_OWNER_AUTH
        assert funded["venue_class"] == FA.VENUE_FUNDED
        assert funded["readiness"]["ready"] is True, (
            "the refusal must be the owner's authorisation, not a "
            "readiness failure")
    finally:
        await conn.execute("DELETE FROM external_valuations "
                           "WHERE experiment_id = $1", ext.EXPERIMENT_ID)
        await conn.execute("DELETE FROM ingestion_state WHERE key = ANY($1)",
                           [FA.LIMITS_KEY, FA.ACCOUNT_KEY,
                            FA.AUTHORIZATION_KEY,
                            "ext_pinnacle_last_cycle"])
        await conn.close()


@pg
async def test_the_demonstration_book_cannot_qualify_an_activation():
    """A demonstration entry clears no market gate: its inputs are chosen.
    It must not turn the admission check true."""
    asyncpg = pytest.importorskip("asyncpg")

    from sportsassets import bettor_demonstration as DEMO

    conn = await asyncpg.connect(DSN)
    try:
        await _seed_accounts(conn)
        await _record_and_approve_limits(conn, approved=True)
        await conn.execute("DELETE FROM external_valuations "
                           "WHERE experiment_id = ANY($1)",
                           [ext.EXPERIMENT_ID, DEMO.EXPERIMENT])
        await DEMO.run_full(conn)
        r = await FA.readiness(conn, account_id=CLEAN)
        adm = {c["check"]: c
               for c in r["checks"]}["an_autonomous_entry_was_admitted_unwaived"]
        assert adm["met"] is False, adm
        assert adm["evidence"]["admitted"] == 0
        assert "the controlled demonstration" in adm["evidence"]["excluded"]
        assert r["ready"] is False
        # and the qualifying provenance is the calibrated lane's, only
        assert adm["evidence"]["qualifying_provenance"] == [inv.PROVENANCE]
    finally:
        await conn.execute(
            "DELETE FROM rn1x_fills WHERE order_id IN (SELECT order_id FROM "
            "rn1x_orders o JOIN rn1x_positions p ON p.position_id = "
            "o.position_id WHERE p.experiment_id = $1)", DEMO.EXPERIMENT)
        for t in ("rn1x_orders", "rn1x_decisions", "rn1x_outcomes"):
            await conn.execute(
                "DELETE FROM " + t + " WHERE position_id LIKE $1",
                DEMO.EXPERIMENT + "%")
        await conn.execute(
            "DELETE FROM rn1x_positions WHERE experiment_id = $1",
            DEMO.EXPERIMENT)
        await conn.execute("DELETE FROM ingestion_state WHERE key = ANY($1)",
                           [FA.LIMITS_KEY, FA.ACCOUNT_KEY])
        await conn.close()


# ── THE ROUTE'S STATUS CODES FOLLOW THE VERDICT ─────────────────────

def test_the_activation_route_answers_200_when_it_authorises():
    """A COMPLETE PATH MUST BE OBSERVABLE AS A SUCCESS.

    The first wiring raised 409 unconditionally, so an AUTHORIZED test
    venue came back as a refusal with the authorisation buried in the
    error body -- indistinguishable, to any caller reading the status,
    from the blocked case. The refusal is still 409; the authorisation is
    200.
    """
    import inspect

    from sportsassets.api import app as A

    src = inspect.getsource(A.bettor_control_act)
    i = src.index('elif act == "activate"')
    block = src[i:i + 1400]
    assert 'if not got.get("ok"):' in block
    assert "status_code=409" in block
    # and the 409 is INSIDE the not-ok branch, not before it
    assert block.index('if not got.get("ok"):') < block.index("status_code=409")


def test_the_route_still_refuses_a_funded_resume_by_name():
    """The asymmetry that matters: a path that REMOVES authority is open,
    one that GRANTS it on a funded lane is refused at the surface."""
    import inspect

    from sportsassets.api import app as A

    src = inspect.getsource(A.bettor_control_act)
    assert "R_FUNDED_RESUME" in src
    assert "status_code=409" in src


@pg
async def test_authorising_does_not_destroy_the_binding_it_read():
    """TWO RECORDS, TWO KEYS.

    The first version wrote the authorisation over the ACCOUNT binding it
    had just been computed from, so the desk showed "no account bound"
    immediately after authorising one and a second activation had nothing
    to read. The binding is the operator's intent; the authorisation is the
    verdict about it.
    """
    asyncpg = pytest.importorskip("asyncpg")

    from sportsassets import bettor_desk_controls as CTL

    conn = await asyncpg.connect(DSN)
    try:
        await _seed_accounts(conn)
        await conn.execute("DELETE FROM ingestion_state WHERE key = ANY($1)",
                           [FA.LIMITS_KEY, FA.ACCOUNT_KEY,
                            FA.AUTHORIZATION_KEY])
        assert FA.AUTHORIZATION_KEY != FA.ACCOUNT_KEY
        await _record_and_approve_limits(conn, approved=True)
        await _seed_evidence(conn, waived=False)
        bind = await CTL.set_account(conn, by="test", account={
            "account_id": CLEAN, "name": "Pilot One", "venue": "PMUS_TEST"})
        assert bind["ok"] is True

        got = await CTL.activate(conn, by="test")
        assert got["ok"] is True, got
        assert got["verdict"] == "AUTHORIZED_FOR_A_TEST_VENUE"
        assert got["recorded_under"] == FA.AUTHORIZATION_KEY

        # THE BINDING IS STILL THERE, unchanged, with its display name.
        left = await CTL._read_state(conn, FA.ACCOUNT_KEY)
        left = json.loads(left) if isinstance(left, str) else left
        assert left["account_id"] == CLEAN
        assert left["name"] == "Pilot One"
        assert left["venue_class"] == "TEST"

        # AND SO IS THE AUTHORISATION, under its own key.
        auth = await CTL._read_state(conn, FA.AUTHORIZATION_KEY)
        auth = json.loads(auth) if isinstance(auth, str) else auth
        assert auth["venue_class"] == "TEST"
        assert auth["funded_submission"] == "DISABLED"

        # ACTIVATING TWICE IS IDEMPOTENT rather than self-destructive.
        again = await CTL.activate(conn, by="test")
        assert again["ok"] is True, again

        # and the control state reports both, separately
        st = await CTL.state(conn)
        assert st["account_proposal"]["account_id"] == CLEAN
        assert st["authorization"]["venue_class"] == "TEST"
        assert st["funded_submission"] == "DISABLED"
    finally:
        await conn.execute("DELETE FROM ingestion_state WHERE key = ANY($1)",
                           [FA.LIMITS_KEY, FA.ACCOUNT_KEY,
                            FA.AUTHORIZATION_KEY])
        await conn.close()
