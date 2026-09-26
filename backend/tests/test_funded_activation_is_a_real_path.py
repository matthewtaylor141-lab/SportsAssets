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


#: The one limit set these fixtures record and approve. Named, so the
#: authorization tests can digest exactly what was approved.
_LIMITS = {"capital_usd": 250, "per_order_usd": 25,
           "max_exposure_usd": 100, "daily_loss_stop_usd": 50}


async def _record_and_approve_limits(conn, *, approved=True):
    from sportsassets import bettor_desk_controls as CTL

    got = await CTL.set_limits(conn, by="test", proposed=dict(_LIMITS))
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


async def _seed_evidence(conn, *, waived: bool, basis: str | None = None,
                        age_at_read: float | None = 1.2,
                        settlement: str = "COMPATIBLE",
                        period: str = "FULL_GAME"):
    """The rows the derived checks read. Labelled as a fixture.

    THE BASIS TOKENS ARE THE LANE'S OWN. An earlier version of this fixture
    used `VENUE_TRANSACT_TIME_ESTABLISHED`, which no writer emits -- and the
    check passed on it, because it only looked for substrings it disliked.
    The supported tokens are asserted against the writer's source elsewhere
    in this file.
    """
    tok = basis or "VENUE_TRANSACT_TIME"
    # a cycle whose book reads recorded an ESTABLISHED age basis
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
        "ext_pinnacle_last_cycle", json.dumps({
            "at": 1_790_000_000.0, "cycle_label": "EVALUATED_1_CANDIDATE",
            "mapped_candidate_ledger": [
                {"us_market_slug": "aec-x-2026-09-26",
                 "venue_clock": {"basis": tok,
                                 "age_at_read_s": age_at_read}}]}))
    # THE WAIVER RECORD IS PRESENT EITHER WAY, because the lane writes it on
    # every verdict. What differs is whether anything was WAIVED.
    risk = {
        "research_waiver": {"authorised": bool(waived),
                            "waived": (["MODEL_TRUST_DRIFT"] if waived
                                       else [])},
        "freshness_evidence": {"venue_age_basis": tok,
                               "venue_age_at_read_s": age_at_read,
                               "venue_age_s": 3.4,
                               "fresh": True},
    }
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
                FALSE,$4,$5::jsonb,
                $3::jsonb, FALSE)
        """, ext.EXPERIMENT_ID, "0x" + ("11" * 32), json.dumps(risk),
             period, json.dumps({"verdict": settlement}))


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


@pg
async def test_every_refusal_still_names_what_is_missing():
    """THE QUESTION MUST BE ANSWERED WHICHEVER GATE STOPS THE REQUEST.

    The first version returned at the earliest failed gate, so an activation
    request with nothing bound came back naming only that gate -- and the
    production readback, whose whole job is to ask what still blocks funded
    activation, printed an EMPTY prerequisite list. Readiness is now computed
    up front and travels with every refusal.
    """
    asyncpg = pytest.importorskip("asyncpg")

    conn = await asyncpg.connect(DSN)
    try:
        await _seed_accounts(conn)
        await conn.execute("DELETE FROM ingestion_state WHERE key = ANY($1)",
                           [FA.LIMITS_KEY, FA.ACCOUNT_KEY,
                            FA.AUTHORIZATION_KEY])

        # NOTHING BOUND AT ALL -- the emptiest possible request.
        got = await FA.authorize(conn, account_id="", venue="", by="test")
        assert got["ok"] is False
        # the ACCOUNT is named, not the venue: with nothing bound that is
        # the useful answer
        assert got["refusal"] == FA.R_NO_ACCOUNT
        # and the prerequisites come with it
        assert got["failed"]["unmet"], got["failed"]
        assert len(got["readiness_checks"]) >= 8
        assert got["readiness"]["ready"] is False
        assert got["readiness"]["unmet_count"] >= 1
        # the checks this test's own setup determines, whatever market
        # evidence another test in this file happens to have left behind
        for must in ("account_selected_and_clean",
                     "limits_recorded_and_complete",
                     "limits_approved_by_the_owner"):
            assert must in got["failed"]["unmet"], (must,
                                                    got["failed"]["unmet"])

        # AND ON EVERY OTHER REFUSAL PATH TOO.
        for kwargs in ({"account_id": "made-up-1", "venue": "PMUS_TEST"},
                       {"account_id": PAUSED, "venue": "PMUS_TEST"},
                       {"account_id": CLOSED, "venue": "PMUS_TEST"},
                       {"account_id": CLEAN, "venue": "NOT_A_VENUE"},
                       {"account_id": CLEAN, "venue": "PMUS_TEST"}):
            r = await FA.authorize(conn, by="test", **kwargs)
            assert r["ok"] is False, kwargs
            assert r["applied"] is None
            assert "unmet" in r["failed"], (kwargs, r["failed"])
            assert r["readiness_checks"], kwargs
            assert r["funded_submission"] == "DISABLED"
            assert r["authorises_capital"] is False
    finally:
        await conn.execute("DELETE FROM ingestion_state WHERE key = ANY($1)",
                           [FA.LIMITS_KEY, FA.ACCOUNT_KEY,
                            FA.AUTHORIZATION_KEY])
        await conn.close()


@pg
async def test_the_account_registry_read_shows_identity_and_no_money():
    """The owner has to name an account_id this service will accept, so the
    registry has to be readable -- and it must carry no balance.

    IT DRIVES THE SQL AND THE PURE SUMMARY, not the route handler: the
    handler's only extra job is to build a connection pool, and doing that
    inside a full-suite run raced the loop and failed on a socket.
    """
    asyncpg = pytest.importorskip("asyncpg")

    conn = await asyncpg.connect(DSN)
    try:
        await _seed_accounts(conn)
        rows = [dict(r) for r in await conn.fetch(FA.REGISTRY_READ_SQL)]
    finally:
        await conn.close()

    got = FA.summarise_registry(rows)
    ids = {r["account_id"] for r in got["accounts"]}
    assert {PAUSED, CLEAN, CLOSED} <= ids
    # ELIGIBILITY IS COMPUTED FROM THE ROWS, and it is not an approval
    assert got["activation_eligible_by_their_rows"] == [CLEAN]
    assert PAUSED in got["paused_accounts"]
    assert "NOT an approval" in got["eligible_means"]
    # NO MONEY CROSSES THIS SURFACE
    assert got["holds_no_balances"] is True
    for r in got["accounts"]:
        for banned in FA.REGISTRY_FORBIDDEN_FIELDS:
            assert banned not in r, (banned, r)


def test_the_registry_summary_strips_money_even_if_the_table_grows():
    """The stripping is on the SUMMARY, not on the query, so a column added
    to the table tomorrow cannot leak through a `SELECT *` somewhere."""
    got = FA.summarise_registry([
        {"account_id": "a", "status": "ACTIVE", "paused": False,
         "accounting_status": "CLEAN", "opening_balance": 1234.5,
         "buying_power": 99.0, "provenance": {"x": 1}},
        {"account_id": "b", "status": "ACTIVE", "paused": True,
         "accounting_status": "UNCERTAIN"},
        {"account_id": "c", "status": "CLOSED_UNATTRIBUTABLE",
         "paused": False, "accounting_status": "CLEAN"},
    ])
    assert got["activation_eligible_by_their_rows"] == ["a"]
    assert got["paused_accounts"] == ["b"]
    assert all("opening_balance" not in r and "buying_power" not in r
               for r in got["accounts"])
    # AND THE ROUTE RETURNS EXACTLY THIS, plus ok
    import inspect

    from sportsassets.api import app as A

    src = inspect.getsource(A.admin_funded_account_registry)
    assert "FA.summarise_registry(rows)" in src
    assert "FA.REGISTRY_READ_SQL" in src
    assert "ACCOUNT_REGISTRY_UNREADABLE" in src


# ── THE COUNTEREXAMPLES, EACH PINNED ────────────────────────────────

@pg
async def test_a_venue_clock_that_was_never_provided_is_not_a_basis():
    """COUNTEREXAMPLE 1, reproduced and pinned.

    `VENUE_CLOCK_NOT_PROVIDED` is the token the lane writes when the venue
    sent no transact time at all -- the clearest possible statement that the
    age was never measured. The first check asked only whether the token
    contained "UNESTABLISHED", "NOT_RECORDED" or "UNKNOWN", so this PASSED.
    """
    asyncpg = pytest.importorskip("asyncpg")

    conn = await asyncpg.connect(DSN)
    try:
        for tok, want in (("VENUE_CLOCK_NOT_PROVIDED", False),
                          ("VENUE_CLOCK_UNPARSEABLE", False),
                          ("VENUE_TRANSACT_TIME", True),
                          # OUR OWN LATENCY IS NOT AN UPSTREAM AGE. It was
                          # briefly in the established list and it must not
                          # be: see the dedicated counterexample below.
                          ("OUR_REQUEST_RESPONSE_ROUND_TRIP", None),
                          ("OUR_TRANSPORT_LATENCY_NOT_AN_UPSTREAM_AGE",
                           None)):
            await conn.execute(
                "INSERT INTO ingestion_state (key, value) "
                "VALUES ($1, $2::jsonb) ON CONFLICT (key) DO UPDATE "
                "SET value = $2::jsonb", "ext_pinnacle_last_cycle",
                json.dumps({"at": 1_790_000_000.0, "cycle_label": "X",
                            "mapped_candidate_ledger": [
                                {"venue_clock": {"basis": tok,
                                                 "age_at_read_s": 1.0}}]}))
            got = await FA._freshness_check(conn)
            assert got["met"] is want, (tok, got["met"], got["detail"])

        # AN ESTABLISHED TOKEN WITH NO MEASURED AGE IS NOT EVIDENCE OF ONE.
        await conn.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
            "ext_pinnacle_last_cycle",
            json.dumps({"at": 1.0, "cycle_label": "X",
                        "mapped_candidate_ledger": [
                            {"venue_clock": {"basis": "VENUE_TRANSACT_TIME",
                                             "age_at_read_s": None}}]}))
        got = await FA._freshness_check(conn)
        assert got["met"] is None, got
        assert "evidence for it is missing" in got["detail"]

        # AND A TOKEN NOBODY WRITES IS UNKNOWN, NOT A PASS.
        await conn.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
            "ext_pinnacle_last_cycle",
            json.dumps({"at": 1.0, "cycle_label": "X",
                        "mapped_candidate_ledger": [
                            {"venue_clock": {
                                "basis": "VENUE_TRANSACT_TIME_ESTABLISHED",
                                "age_at_read_s": 1.0}}]}))
        got = await FA._freshness_check(conn)
        assert got["met"] is None, got
        assert "not in this check's vocabulary" in got["detail"]
    finally:
        await conn.execute("DELETE FROM ingestion_state WHERE key = $1",
                           "ext_pinnacle_last_cycle")
        await conn.close()


def test_the_basis_vocabulary_is_the_writers_own():
    """A vocabulary that drifts from the writer is a substring guess with
    extra steps. The tokens are asserted against the module that emits
    them."""
    import inspect

    from sportsassets.workers import ext_pinnacle_loop as LOOP

    src = inspect.getsource(LOOP)
    for tok in FA.FRESHNESS_BASIS_ESTABLISHED:
        assert '"%s"' % tok in src, tok
    for tok in FA.FRESHNESS_BASIS_UNESTABLISHED:
        if tok == "NOT_RECORDED":
            continue          # this check's own word for "no token at all"
        assert '"%s"' % tok in src, tok
    # and the token an older fixture invented is emitted by nobody
    assert '"VENUE_TRANSACT_TIME_ESTABLISHED"' not in src


@pg
async def test_one_incompatible_settlement_verdict_refuses_the_window():
    """COUNTEREXAMPLE 2, reproduced and pinned.

    The settlement module's word for a conflict is INCOMPATIBLE. The first
    check counted conflicts by looking for "CONFLICT", which INCOMPATIBLE
    does not contain -- so a window holding one COMPATIBLE and one
    INCOMPATIBLE row reported "1 compatible and none conflicted" and PASSED,
    on evidence that included a fixture whose payouts provably differ.
    """
    asyncpg = pytest.importorskip("asyncpg")

    from sportsassets import bettor_settlement_terms as ST

    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute("DELETE FROM external_valuations "
                           "WHERE experiment_id = $1", ext.EXPERIMENT_ID)
        # one COMPATIBLE alone: met
        await _seed_evidence(conn, waived=False, settlement=ST.COMPATIBLE)
        first = await FA._settlement_check(conn, ext.EXPERIMENT_ID, 168)
        assert first["met"] is True, first
        assert first["evidence"]["conflicting"] == 0

        # add ONE INCOMPATIBLE beside it: the window must refuse
        await _seed_evidence(conn, waived=False, settlement=ST.INCOMPATIBLE)
        got = await FA._settlement_check(conn, ext.EXPERIMENT_ID, 168)
        assert got["met"] is False, got
        assert got["evidence"]["conflicting"] == 1, got["evidence"]
        assert got["evidence"]["compatible"] == 1, got["evidence"]
        assert "INCOMPATIBLE" in got["detail"]
        assert "none conflicted" not in got["detail"]

        # a verdict outside the enum is UNKNOWN, not a pass and not a fail
        await conn.execute("DELETE FROM external_valuations "
                           "WHERE experiment_id = $1", ext.EXPERIMENT_ID)
        await _seed_evidence(conn, waived=False, settlement="PROBABLY_FINE")
        odd = await FA._settlement_check(conn, ext.EXPERIMENT_ID, 168)
        assert odd["met"] is None, odd
        assert "outside the enum" in odd["detail"]
    finally:
        await conn.execute("DELETE FROM external_valuations "
                           "WHERE experiment_id = $1", ext.EXPERIMENT_ID)
        await conn.close()


@pg
async def test_the_waiver_test_reads_the_waiver_and_not_the_json_text():
    """COUNTEREXAMPLE 3, of the same family, found while fixing the others.

    The lane writes the waiver RECORD on every verdict it persists, so
    `risk_verdict::text LIKE '%research_waiver%'` matched every row: every
    admission counted as waived and the check could never be satisfied by
    anything. It failed closed, so it granted nothing it should not have --
    it was simply unsatisfiable, and it said "0 admitted without a waiver"
    about rows where nothing had been waived.
    """
    asyncpg = pytest.importorskip("asyncpg")

    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute("DELETE FROM external_valuations "
                           "WHERE experiment_id = $1", ext.EXPERIMENT_ID)
        await _seed_evidence(conn, waived=False)
        got = await FA._admitted_check(conn, ext.EXPERIMENT_ID, 168,
                                      (inv.PROVENANCE,))
        assert got["met"] is True, got
        assert got["evidence"]["of_those_research_waived"] == 0, got["evidence"]

        # AND A CONSUMED WAIVER STILL DOES NOT QUALIFY.
        await conn.execute("DELETE FROM external_valuations "
                           "WHERE experiment_id = $1", ext.EXPERIMENT_ID)
        await _seed_evidence(conn, waived=True)
        waived = await FA._admitted_check(conn, ext.EXPERIMENT_ID, 168,
                                         (inv.PROVENANCE,))
        assert waived["met"] is False, waived
        assert waived["evidence"]["of_those_research_waived"] == 1
        assert waived["evidence"]["admitted_without_a_waiver"] == 0
    finally:
        await conn.execute("DELETE FROM external_valuations "
                           "WHERE experiment_id = $1", ext.EXPERIMENT_ID)
        await conn.close()


@pg
async def test_readiness_is_never_assembled_from_unrelated_rows():
    """FOUR CHECKS PASSING ON FOUR DIFFERENT MARKETS IS NOT AN OPPORTUNITY.

    Each per-kind check reads its own rows, so all four can be met while no
    single market carries the whole chain. The composite check is what
    gates, and it names the market when one exists.
    """
    asyncpg = pytest.importorskip("asyncpg")

    from sportsassets import bettor_settlement_terms as ST

    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute("DELETE FROM external_valuations "
                           "WHERE experiment_id = $1", ext.EXPERIMENT_ID)
        # MARKET A: admitted and unwaived, but its scope is a segment and
        # its settlement is UNKNOWN.
        await _seed_evidence(conn, waived=False, settlement=ST.UNKNOWN,
                             period="FIRST_HALF")
        # MARKET B: a COMPATIBLE settlement and a whole-fixture scope, but
        # its clock basis was never provided, and it is not admissible.
        await conn.execute(
            """
            INSERT INTO external_valuations
              (experiment_id, version, source_class, provider, book,
               devig_method, venue, contract_selection, sport_family, market,
               raw_odds, outcomes_priced, expected_outcomes, condition_id,
               us_market_slug, decision, admissible, refusals, why,
               decided_at, outcome_known, period, settlement_comparison,
               risk_verdict, order_submitted)
            VALUES ($1,'v','EXTERNAL_BOOKMAKER_VALUATION','PINNACLE',
                    'pinnacle','power','PMUS','B','baseball','MONEYLINE',
                    '{}'::jsonb, 2, 2, $2, 'market-b-2026-09-26',
                    'NO_TRADE', FALSE, ARRAY[]::text[], 'market B', now(),
                    FALSE, 'FULL_GAME', $3::jsonb, $4::jsonb, FALSE)
            """, ext.EXPERIMENT_ID, "0x" + ("22" * 32),
            json.dumps({"verdict": ST.COMPATIBLE}),
            json.dumps({"research_waiver": {"authorised": False,
                                            "waived": []},
                        "freshness_evidence": {
                            "venue_age_basis": "VENUE_CLOCK_NOT_PROVIDED",
                            "venue_age_at_read_s": None}}))

        comp = await FA._eligible_market_check(conn, ext.EXPERIMENT_ID, 168)
        assert comp["met"] is False, comp
        assert comp["evidence"]["eligible_markets"] == 0
        assert "do not together make an eligible market" in \
            comp["evidence"]["why_none"]

        # NOW ONE MARKET WITH THE WHOLE CHAIN: it is met, and NAMED.
        await _seed_evidence(conn, waived=False, settlement=ST.COMPATIBLE,
                             period="FULL_GAME")
        ok = await FA._eligible_market_check(conn, ext.EXPERIMENT_ID, 168)
        assert ok["met"] is True, ok
        assert ok["evidence"]["eligible_markets"] >= 1
        top = ok["evidence"]["markets"][0]
        assert top["us_market_slug"] == "aec-x-2026-09-26"
        assert top["settlement_verdict"] == ST.COMPATIBLE
        assert top["period"] == "FULL_GAME"
        assert top["venue_age_basis"] in FA.FRESHNESS_BASIS_ESTABLISHED
        assert top["venue_age_at_read_s"] is not None
        assert top["us_market_slug"] in ok["detail"]

        # and readiness carries it as the check that gates
        r = await FA.readiness(conn, account_id=CLEAN)
        names = {c["check"] for c in r["checks"]}
        assert r["the_eligible_market_check_is"] in names
        assert "unrelated rows" in r["what_ready_means"]
    finally:
        await conn.execute("DELETE FROM external_valuations "
                           "WHERE experiment_id = $1", ext.EXPERIMENT_ID)
        await conn.close()


# ── THE AUTHORIZATION IS CONSUMED BY THE EXECUTION BOUNDARY ─────────

def test_the_execution_boundary_consumes_the_authorization():
    """RECORDING ONE PROVES NOTHING. An authorization nothing reads is a note
    in a table, so the proof is that the EXECUTION side's answer CHANGES when
    the record is present and matching -- from "you are not authorised" to
    "you are authorised and submission is off in code".
    """
    rec = {"account_id": CLEAN, "venue": "PMUS_TEST", "venue_class": "TEST",
           "effective_digest": EX.effective_limits(_LIMITS)["effective_digest"]}

    # NO RECORD AT ALL
    none = EX.authorize_submission(account_id=CLEAN, venue="PMUS_TEST",
                                   authorization=None)
    assert none["ok"] is False
    assert none["refusal"] == EX.R_NO_AUTHORIZATION
    assert none["authorization_consumed"] is False
    assert none["submitted"] is False

    # A RECORD FOR ANOTHER ACCOUNT, AND FOR ANOTHER VENUE
    other = EX.authorize_submission(account_id="someone-else",
                                    venue="PMUS_TEST", authorization=rec)
    assert other["refusal"] == EX.R_AUTH_ACCOUNT
    assert other["authorization_consumed"] is False
    venue = EX.authorize_submission(account_id=CLEAN, venue="PMUS",
                                    authorization=rec)
    assert venue["refusal"] == EX.R_AUTH_VENUE
    assert venue["authorization_consumed"] is False

    # A RECORD GRANTED AGAINST DIFFERENT LIMITS
    moved = dict(_LIMITS, per_order_usd=999)
    stale = EX.authorize_submission(account_id=CLEAN, venue="PMUS_TEST",
                                    authorization=rec, approved_limits=moved)
    assert stale["refusal"] == EX.R_AUTH_LIMITS
    assert stale["authorization_consumed"] is False

    # A RECORD WITH NO DIGEST AT ALL
    nodig = EX.authorize_submission(
        account_id=CLEAN, venue="PMUS_TEST",
        authorization={k: v for k, v in rec.items()
                       if k != "effective_digest"})
    assert nodig["refusal"] == EX.R_AUTH_NO_DIGEST

    # AND THE MATCHING RECORD: CONSUMED, then refused on the CONSTANT.
    good = EX.authorize_submission(account_id=CLEAN, venue="PMUS_TEST",
                                   authorization=rec,
                                   approved_limits=_LIMITS)
    assert good["authorization_consumed"] is True, good
    assert good["ok"] is False
    assert good["refusal"] == EX.R_SUBMISSION_DISABLED
    assert good["submitted"] is False
    assert good["real_order_submission_enabled"] is False
    # the difference between the two answers IS the consumption
    assert none["refusal"] != good["refusal"]


def test_an_injected_test_venue_executor_still_submits_nothing():
    """AN INJECTED SUBMITTER, driven through the gate. It is handed a TEST
    venue and a valid authorization, and it must still send nothing --
    because the only sanctioned path to a submission ends at the constant."""
    sent = []

    def _submit_if_allowed(*, account_id, venue, authorization, limits):
        """The shape any future submitter must have: ask the gate FIRST."""
        gate = EX.authorize_submission(account_id=account_id, venue=venue,
                                       authorization=authorization,
                                       approved_limits=limits)
        if gate.get("ok"):                             # pragma: no cover
            sent.append({"venue": venue, "account_id": account_id})
        return gate

    rec = {"account_id": CLEAN, "venue": "PMUS_TEST", "venue_class": "TEST",
           "effective_digest": EX.effective_limits(_LIMITS)["effective_digest"]}
    got = _submit_if_allowed(account_id=CLEAN, venue="PMUS_TEST",
                             authorization=rec, limits=_LIMITS)
    assert got["authorization_consumed"] is True
    assert got["refusal"] == EX.R_SUBMISSION_DISABLED
    assert sent == [], "the injected submitter sent something"

    # AND THERE IS NO OTHER SUBMITTER IN THE TREE. `order_submitted` is
    # never written true anywhere, so this gate is not one path among many.
    import pathlib
    import re

    root = pathlib.Path(EX.__file__).resolve().parent
    offenders = []
    for f in root.rglob("*.py"):
        if "__pycache__" in str(f):
            continue
        txt = f.read_text(errors="ignore")
        for m in re.finditer(r"order_submitted\s*=\s*(True|true)", txt):
            offenders.append("%s: %s" % (f.name, m.group(0)))
    assert offenders == [], offenders


@pg
async def test_the_funded_branch_validates_the_owners_record():
    """IT NO LONGER REFUSES BLIND. The branch returned the same refusal
    whether or not the owner's authorisation existed, so the record could be
    present and correct and nothing would change. Now: absent refuses,
    mismatched refuses BY ITS OWN NAME, and matching is accepted -- with
    submission still off in code and the executor saying so.
    """
    asyncpg = pytest.importorskip("asyncpg")

    conn = await asyncpg.connect(DSN)
    try:
        await _seed_accounts(conn)
        await conn.execute("DELETE FROM ingestion_state WHERE key = ANY($1)",
                           [FA.LIMITS_KEY, FA.ACCOUNT_KEY,
                            FA.AUTHORIZATION_KEY, FA.OWNER_AUTH_KEY])
        await _record_and_approve_limits(conn, approved=True)
        await conn.execute("DELETE FROM external_valuations "
                           "WHERE experiment_id = $1", ext.EXPERIMENT_ID)
        await _seed_evidence(conn, waived=False)

        # 1 · ABSENT: the standing refusal, and it says the record is absent
        a = await FA.authorize(conn, account_id=CLEAN, venue="PMUS",
                               by="test")
        assert a["ok"] is False
        assert a["refusal"] == FA.R_OWNER_AUTH
        assert a["owner_authorization_present"] is False
        assert "no owner authorisation record exists" in a["failed"]["why"]

        eff = EX.effective_limits(_LIMITS)

        async def _owner(**kw):
            rec = {"account_id": CLEAN, "venue": "PMUS",
                   "effective_digest": eff["effective_digest"],
                   "by": "owner", "at": 1.0,
                   "statement": "a test fixture, not a real authorisation"}
            rec.update(kw)
            await conn.execute(
                "INSERT INTO ingestion_state (key, value) "
                "VALUES ($1, $2::jsonb) ON CONFLICT (key) DO UPDATE "
                "SET value = $2::jsonb", FA.OWNER_AUTH_KEY,
                json.dumps(rec))

        # 2 · PRESENT BUT FOR ANOTHER ACCOUNT / VENUE / LIMIT SET
        await _owner(account_id="someone-else")
        b = await FA.authorize(conn, account_id=CLEAN, venue="PMUS",
                               by="test")
        assert b["refusal"] == FA.R_OWNER_AUTH_ACCOUNT, b["refusal"]
        assert b["owner_authorization_present"] is True

        await _owner(venue="POLYMARKET")
        c = await FA.authorize(conn, account_id=CLEAN, venue="PMUS",
                               by="test")
        assert c["refusal"] == FA.R_OWNER_AUTH_VENUE, c["refusal"]

        await _owner(effective_digest="0" * 64)
        d = await FA.authorize(conn, account_id=CLEAN, venue="PMUS",
                               by="test")
        assert d["refusal"] == FA.R_OWNER_AUTH_LIMITS, d["refusal"]

        # 3 · MATCHING: accepted, and the EXECUTOR is what refuses next
        await _owner()
        e = await FA.authorize(conn, account_id=CLEAN, venue="PMUS",
                               by="test")
        assert e["ok"] is True, e
        assert e["owner_authorization_validated"] is True
        assert e["venue_class"] == FA.VENUE_FUNDED
        assert e["verdict"] == ("AUTHORIZED_FOR_A_FUNDED_VENUE_"
                               "SUBMISSION_STILL_DISABLED_IN_CODE")
        # NO CAPITAL IS ENABLED BY ANY OF IT
        assert e["funded_submission"] == "DISABLED"
        assert e["authorises_capital"] is False
        # AND THE EXECUTION BOUNDARY CONSUMED IT AND STILL REFUSED
        boundary = e["execution_boundary"]
        assert boundary["authorization_consumed"] is True, boundary
        assert boundary["ok"] is False
        assert boundary["refusal"] == EX.R_SUBMISSION_DISABLED
        assert boundary["submitted"] is False
        assert e["submission_would_be"] == EX.R_SUBMISSION_DISABLED
    finally:
        await conn.execute("DELETE FROM ingestion_state WHERE key = ANY($1)",
                           [FA.LIMITS_KEY, FA.ACCOUNT_KEY,
                            FA.AUTHORIZATION_KEY, FA.OWNER_AUTH_KEY,
                            "ext_pinnacle_last_cycle"])
        await conn.execute("DELETE FROM external_valuations "
                           "WHERE experiment_id = $1", ext.EXPERIMENT_ID)
        await conn.close()


def test_transport_latency_is_never_an_upstream_freshness_basis():
    """THE COUNTEREXAMPLE, and it killed a claim I had written into the gate.

    An earlier version added OUR_REQUEST_RESPONSE_ROUND_TRIP to the
    established bases, arguing that the venue answered after we asked so what
    we received could not be older than the round trip. FALSE. The round trip
    measures TRANSPORT LATENCY. A server can answer in 20 ms with a snapshot
    it cached minutes ago -- and the FASTER it answers, the stronger the false
    certificate of freshness.
    """
    assert "OUR_REQUEST_RESPONSE_ROUND_TRIP" not in \
        FA.FRESHNESS_BASIS_ESTABLISHED
    assert FA.OUR_TRANSPORT_LATENCY not in FA.FRESHNESS_BASIS_ESTABLISHED
    for tok in FA.OUR_OWN_TIMESTAMPS:
        assert tok not in FA.FRESHNESS_BASIS_ESTABLISHED, tok
    # THE ESTABLISHED SET IS THE VENUE'S OWN CLOCK, and only that.
    assert set(FA.FRESHNESS_BASIS_ESTABLISHED) == {
        "VENUE_TRANSACT_TIME", "VENUE_TRANSACT_TIME_REAGED_AT_THE_DECISION"}
    # AND THE POLICY SAYS SO, with the counterexample in it.
    pol = FA.FRESHNESS_ADMISSION_POLICY
    assert "cached minutes ago" in pol["why_transport_latency_is_not_an_age"]
    assert "UNMEASURED" in pol["when_the_venue_clock_is_absent_or_unparseable"]
    assert pol["upstream_age_is_established_only_by"] == \
        list(FA.FRESHNESS_BASIS_ESTABLISHED)


@pg
async def test_a_fast_response_with_an_old_snapshot_establishes_nothing():
    """THE COUNTEREXAMPLE, DRIVEN. A read that came back in 12 ms and carried
    no venue clock must leave the upstream age UNMEASURED -- the speed of the
    answer is not evidence about the book."""
    asyncpg = pytest.importorskip("asyncpg")

    conn = await asyncpg.connect(DSN)
    try:
        for label, clock in (
                ("fast answer, no venue clock", {
                    "basis": "VENUE_CLOCK_NOT_PROVIDED",
                    "age_at_read_s": None,
                    "our_transport_latency_s": 0.012}),
                ("fast answer, unparseable venue clock", {
                    "basis": "VENUE_CLOCK_UNPARSEABLE",
                    "age_at_read_s": None,
                    "our_transport_latency_s": 0.009})):
            await conn.execute(
                "INSERT INTO ingestion_state (key, value) "
                "VALUES ($1, $2::jsonb) ON CONFLICT (key) DO UPDATE "
                "SET value = $2::jsonb", "ext_pinnacle_last_cycle",
                json.dumps({"at": 1.0, "cycle_label": "X",
                            "mapped_candidate_ledger": [
                                {"venue_clock": clock}]}))
            got = await FA._freshness_check(conn)
            assert got["met"] is False, (label, got)
            ev = got["evidence"]
            assert ev["reads_established_AND_with_a_measured_age"] == 0
            # the latency is VISIBLE and it is not counted as a basis
            assert clock["our_transport_latency_s"] < 0.02
    finally:
        await conn.execute("DELETE FROM ingestion_state WHERE key = $1",
                           "ext_pinnacle_last_cycle")
        await conn.close()


def test_the_read_path_invents_no_basis_from_its_own_latency():
    """The fallback that assigned our latency as the age is gone from the
    reader, not merely unlisted in the checker."""
    import inspect

    from sportsassets.workers import ext_pinnacle_loop as LOOP

    src = inspect.getsource(LOOP)
    assert 'age_basis = "OUR_REQUEST_RESPONSE_ROUND_TRIP"' not in src
    assert "our_transport_latency_is_not_an_upstream_age" in src
    assert "NO FALLBACK" in src
