"""INTEGRATED ENGINEERING EVIDENCE -- NOT REAL-MONEY VERIFICATION.

WHAT THIS IS. An isolated migrated database, inventory established through the
REAL ingestion path (`record_intent` -> `record_acknowledgement` ->
`ingest_fills`), a catalogue built with the REAL premap DDL, and
`bettor_funded_pair_cycle.pass_once` run with the ACTUAL suppliers, the actual
builder, the actual ranking and the actual persistence.

WHAT IS SUBSTITUTED, AND IT IS ONLY THIS. The two external TRANSPORTS: the
venue's settlement-prose HTTP read and the venue's order-book read. Captured
responses are returned in their place. Nothing else is stood in for -- in
particular NO finished `Leg`, NO finished ranking and NO selected winner is
injected, because those are the things under test and injecting one would make
the test a description of itself.

WHAT THIS IS NOT. It is not evidence about real money, real fills, real
liquidity or real profitability. The funded book is empty in production and
`FUNDED_SUBMISSION_ENABLED` is off; this exercises ORCHESTRATION, which is a
different claim and a smaller one.
"""

import os
import time

import pytest

from sportsassets import bettor_funded_book as FB
from sportsassets import bettor_funded_hedge_supply as HS
from sportsassets import bettor_funded_pair_cycle as PC
from sportsassets import bettor_funded_execution as FX
from sportsassets import bettor_funded_activation as FA
from sportsassets import bettor_indirect_structures as IS
from sportsassets.workers import ext_pinnacle_loop as LOOP

import asyncpg
import contextlib

DSN = os.environ.get("RN1X_TEST_DSN", "")
pytestmark = pytest.mark.skipif(
    not DSN, reason="needs an isolated migrated database")


@contextlib.asynccontextmanager
async def _conn():
    c = await asyncpg.connect(DSN)
    try:
        yield c
    finally:
        await c.close()

ACCT = "acct-integ-hedge"
VENUE = "PMUS_INTEG_HEDGE"
EVENT = "npb-clm-nhf-2026-10-01"
HELD = "aec-npb-clm-nhf-2026-10-01"
SIB_SPREAD = "asc-npb-clm-nhf-2026-10-01-f5-neg-1pt5"
SIB_TOTAL = "tsc-npb-clm-nhf-2026-10-01-f5-3pt5"
SIB_PROP = "astatc-npb-clm-nhf-2026-10-01-yrfi"

NOW = 1790000000.0

#: CAPTURED VENUE PROSE. Shaped like what `read_rules_text` returns, with a
#: sentence that `bettor_venue_settlement.OVERTIME_PROSE['baseball']` matches
#: on a DECLARED pattern -- so the overtime rule is read, not asserted.
PROSE = ("This market resolves on the final score and includes any extra "
         "innings played. A tie resolves 50-50.")


def _prose_reader(text=PROSE, *, fail_for=()):
    async def read(slug):
        if slug in fail_for:
            return {"ok": False, "error": "VENUE_PUBLISHES_NO_RULES_TEXT",
                    "read_at": NOW}
        return {"ok": True, "rules_text": text, "rules_field": "description",
                "source": "pmus:/markets?slug=<slug>:rules_text",
                "read_at": NOW}
    return read


def _quoter(prices):
    """A captured book read per slug. Absent slug -> no price, as a real
    unpriced contract arrives."""
    async def quote(slug):
        p = prices.get(slug)
        if p is None:
            return {"ok": False, "refusal": "NOT_IN_THE_CAPTURED_BOOK"}
        return {"ok": True, "cost_per_share": p[0], "depth_qty": p[1],
                "available_qty": p[1]}
    return quote


async def _catalogue(conn):
    """THE VENUE CATALOGUE, through the real premap DDL and real column shapes.

    Every row is the shape run 264 returned: `kind` the literal 'side', `line`
    the start minute on the moneyline, `signed` only on the spread, `side_norm`
    the team name on the moneyline and yes/no on the spread.
    """
    # THIS MODULE DOES NOT DEFINE THE CATALOGUE SCHEMA, and the reason is a
    # latent inconsistency in the suite that my gate run surfaced.
    #
    # `premap._ensure_table` -- the PRODUCTION DDL -- puts the unique index on
    # (identifier, side_norm), because the aec- family's two sides share an
    # identifier. Several older test modules instead create `us_premap` for
    # themselves with `identifier` as a bare PRIMARY KEY and then insert with
    # `ON CONFLICT (identifier)`.
    #
    # Both shapes work for whoever creates the table first, and the loser gets
    # "no unique or exclusion constraint matching the ON CONFLICT
    # specification". When I called the production DDL here, this module ran
    # first in my gate and broke eleven tests in a file I had not touched --
    # not a code regression, a schema-ownership collision I introduced.
    #
    # So: use whatever `us_premap` exists, skip when none does, and insert
    # without a conflict target. The divergence between the suite's legacy
    # shape and production's is real and worth fixing, but it is a separate
    # change and not one to make silently from inside a new test.
    if await conn.fetchval("SELECT to_regclass('us_premap') IS NULL"):
        pytest.skip("no us_premap in this database; this module does not own "
                    "the catalogue schema")
    await conn.execute("DELETE FROM us_premap WHERE event_slug=$1", EVENT)
    rows = [
        # the held moneyline, BOTH sides -- one instrument, two rows
        (HELD + ":clm", HELD, "baseball_team_full_game_winner",
         "chiba lotte marines", "clm", "00", None,
         "ORDER_INTENT_BUY_LONG"),
        (HELD + ":nhf", HELD, "baseball_team_full_game_winner",
         "nippon ham fighters", "nhf", "00", None,
         "ORDER_INTENT_BUY_SHORT"),
        # a sibling SPREAD, both sides, mirror-image signed handicaps
        (SIB_SPREAD + ":clm", SIB_SPREAD, "baseball_team_full_game_spread",
         "yes", "clm", "1.5", "-1.5", "ORDER_INTENT_BUY_LONG"),
        (SIB_SPREAD + ":nhf", SIB_SPREAD, "baseball_team_full_game_spread",
         "no", "nhf", "1.5", "+1.5", "ORDER_INTENT_BUY_SHORT"),
        # a sibling TOTAL
        (SIB_TOTAL + ":over", SIB_TOTAL, "baseball_game_total_points",
         "over", None, "3.5", None, "ORDER_INTENT_BUY_LONG"),
        (SIB_TOTAL + ":under", SIB_TOTAL, "baseball_game_total_points",
         "under", None, "3.5", None, "ORDER_INTENT_BUY_SHORT"),
        # a sibling the module MUST refuse: a variable it does not represent
        (SIB_PROP + ":yes", SIB_PROP, "baseball_team_first_inning_run",
         "yes", "nhf", None, None, "ORDER_INTENT_BUY_LONG"),
    ]
    for ident, slug, st, side, abbr, line, signed, intent in rows:
        # A PLAIN INSERT, AFTER THE DELETE ABOVE. No ON CONFLICT clause:
        # `us_premap`'s unique key differs between what `premap._ensure_table`
        # builds in production -- (identifier, side_norm), because the aec-
        # sides share an identifier -- and what several older test modules
        # create for themselves, keyed on identifier alone. A conflict target
        # naming either one makes this module depend on which of them happened
        # to create the table first, and that dependency is exactly how my gate
        # database preparation produced eleven failures in a file I had not
        # touched. The delete makes the clause unnecessary.
        await conn.execute(
            "INSERT INTO us_premap (identifier, event_slug, event_title,"
            " market_slug, question, kind, line, side_norm, intent, signed,"
            " team_abbr, team_name, sports_type, game_start)"
            " VALUES ($1,$2,$3,$4,$5,'side',$6,$7,$8,$9,$10,$11,$12,"
            "         now() + interval '2 hours')",
            ident, EVENT, "Chiba Lotte Marines vs. Nippon Ham Fighters",
            slug, "Who will win?", line, side, intent, signed, abbr,
            (abbr or ""), st)


#: EVERY ROW THIS MODULE CREATES, SO IT CAN REMOVE EXACTLY THOSE.
#:
#: THE DEFECT THIS FIXES, AND IT WAS MINE. The first version of `_clean` ran
#: `DELETE FROM bettor_funded_intents` with no WHERE, at the START of each test
#: and never at the end. Two consequences, and the gate found both:
#:
#:   * it left this module's own intents live when the file finished. The funded
#:     book enforces ONE live intent at a time, so every later test in the
#:     session that recorded an intent was refused with
#:     ANOTHER_FUNDED_INTENT_IS_ALREADY_LIVE -- 41 new failing node ids across
#:     six files, none of which had anything to do with hedging;
#:   * an unscoped DELETE would also have wiped another module's setup had the
#:     order been different, which is the same bug pointing the other way.
#:
#: So the deletes are SCOPED to this module's own account and event, and they
#: run on teardown as well as setup. A test that cleans only before itself is
#: not isolated; it has just moved its mess onto whoever runs next.
async def _clean(conn):
    await conn.execute(
        "DELETE FROM bettor_funded_fills WHERE intent_id IN ("
        " SELECT intent_id FROM bettor_funded_intents WHERE account_id=$1)",
        ACCT)
    for t in ("bettor_funded_leg_reservations", "bettor_funded_decisions",
              "bettor_funded_economics"):
        try:
            await conn.execute(
                "DELETE FROM %s WHERE intent_id IN ("
                " SELECT intent_id FROM bettor_funded_intents"
                " WHERE account_id=$1)" % t, ACCT)
        except Exception:                                       # noqa: BLE001
            pass
    await conn.execute("DELETE FROM bettor_funded_intents WHERE account_id=$1",
                       ACCT)
    await conn.execute("DELETE FROM us_premap WHERE event_slug=$1", EVENT)


@pytest.fixture(autouse=True)
async def _leave_nothing_behind():
    """CLEAN AFTER, NOT ONLY BEFORE. This is the fixture whose absence cost 41
    new failing node ids on the gate for 3ac9b20."""
    yield
    if DSN:
        async with _conn() as c:
            await _clean(c)


async def _held_position(conn, *, qty=10, price=0.62, intent_id="fpi-integ"):
    """INVENTORY THROUGH THE REAL INGESTION PATH. Not an INSERT."""
    coll = FX.collateral_for(price, qty, FX.LONG)
    got = await FB.record_intent(
        conn, intent_id=intent_id, account_id=ACCT, venue=VENUE,
        venue_class=FA.VENUE_FUNDED, us_market_slug=HELD, event_key=EVENT,
        order_intent=FX.LONG, limit_price=price, quantity=qty,
        collateral_usd=coll, effective_digest="d-integ", held_is_long=True)
    assert got.get("ok"), got
    await FB.record_acknowledgement(conn, intent_id, venue_order_id="vo-integ",
                                   status="open")
    await FB.ingest_fills(conn, intent_id, [
        {"qty": float(qty), "price": price, "venue_fill_id": "vf-integ"}])
    return intent_id


# ═════════════════════════════════════════════════════════════════════
# 1 · THE SUPPLIERS REACH REAL Leg OBJECTS FROM REAL READS
# ═════════════════════════════════════════════════════════════════════

async def test_the_held_leg_is_built_from_the_catalogue_and_the_prose():
    """The claim the previous batch could not make: a `Leg` is CONSTRUCTED,
    by the production importer, from a catalogue read and a prose read."""
    async with _conn() as dbconn:
      await _clean(dbconn)
      await _catalogue(dbconn)
      iid = await _held_position(dbconn)
      pos = {"intent_id": iid, "us_market_slug": HELD, "residual_qty": 10,
             "avg_price": 0.62}
      got = await HS.held_leg_for(dbconn, position=pos,
                                 prose_reader=_prose_reader(), now=NOW)
      assert got["ok"], got
      leg = got["leg"]
      assert isinstance(leg, IS.Leg)
      assert leg.condition_id == HELD and leg.fixture_id == EVENT
      assert leg.kind == IS.KIND_MONEYLINE and leg.period == IS.PERIOD_FULL
      assert leg.overtime == IS.OT_INCLUDED       # READ from the prose
      assert leg.backs == "A"                     # clm is listed first
      assert leg.quantity == 10 and leg.cost_cents_per_unit == 62
      assert leg.missing_facts() == []
      # PROVENANCE TRAVELS WITH IT.
      for field in ("condition_id", "fixture_id", "kind", "period", "overtime",
                    "backs", "quantity", "cost_cents_per_unit"):
          assert got["built_from"].get(field), field


async def test_every_sibling_is_attempted_and_each_refusal_names_its_fact():
    """NOT "the first admitted contract". Every sibling on the fixture is
    attempted; the unpriced one and the ungradeable one are refused BY NAME,
    and the held instrument's own slug is excluded as netting."""
    async with _conn() as dbconn:
      await _clean(dbconn)
      await _catalogue(dbconn)
      held_row = {"market_slug": HELD, "event_slug": EVENT, "residual_qty": 10}
      got = await HS.candidate_legs_for(
          dbconn, held_row=held_row,
          quoter=_quoter({SIB_SPREAD: (0.45, 200), SIB_TOTAL: (0.51, 150)}),
          prose_reader=_prose_reader(), now=NOW)
      built = {c["market_slug"] for c in got["legs"]}
      assert SIB_SPREAD in built and SIB_TOTAL in built
      # THE HELD INSTRUMENT IS NOT A CANDIDATE. Both its sides share its slug.
      assert HELD not in built
      assert HELD not in {r["market_slug"] for r in got["refused"]}
      # THE UNPRICED ONE IS REFUSED, not priced off the held contract's ladder.
      refusals = {r["market_slug"]: r["refusal"] for r in got["refused"]}
      assert refusals.get(SIB_PROP) in (HS.R_CANDIDATE_NOT_PRICED,
                                        HS.R_TYPE_NOT_A_GRADED_VARIABLE)
      assert got["every_candidate_was_attempted"] is True
      # BOTH SIDES of the spread were offered, and their lines agree on A.
      spread_legs = [c["leg"] for c in got["legs"]
                     if c["market_slug"] == SIB_SPREAD]
      assert len(spread_legs) == 2
      assert spread_legs[0].line == spread_legs[1].line


async def test_an_ungradeable_sibling_is_refused_even_when_it_is_priced():
    """A price does not make a variable gradeable. The first-inning-run market
    has a book and is still refused, because VAR_MARGIN / VAR_TOTAL / VAR_WIN3
    do not represent it and pairing it would share a grading key it does not
    share."""
    async with _conn() as dbconn:
      await _clean(dbconn)
      await _catalogue(dbconn)
      got = await HS.candidate_legs_for(
          dbconn, held_row={"market_slug": HELD, "event_slug": EVENT},
          quoter=_quoter({SIB_PROP: (0.40, 300)}),
          prose_reader=_prose_reader(), now=NOW)
      assert SIB_PROP not in {c["market_slug"] for c in got["legs"]}
      assert {r["refusal"] for r in got["refused"]} >= {
          HS.R_TYPE_NOT_A_GRADED_VARIABLE}


async def test_unread_settlement_prose_refuses_the_leg_rather_than_defaulting():
    """The overtime treatment is part of the grading key. With no prose the
    leg refuses, and the refusal NAMES the missing rule."""
    async with _conn() as dbconn:
      await _clean(dbconn)
      await _catalogue(dbconn)
      iid = await _held_position(dbconn, intent_id="fpi-noprose")
      got = await HS.held_leg_for(
          dbconn, position={"intent_id": iid, "us_market_slug": HELD,
                            "residual_qty": 10, "avg_price": 0.62},
          prose_reader=_prose_reader(fail_for=(HELD,)), now=NOW)
      assert got["ok"] is False
      assert got["refusal"] == HS.R_OVERTIME_NOT_CAPTURED


# ═════════════════════════════════════════════════════════════════════
# 2 · THE SCHEDULED SUPPLIER HANDS THEM TO THE SCHEDULED PASS
# ═════════════════════════════════════════════════════════════════════

async def test_funded_pair_inputs_now_returns_real_legs_not_none():
    """THE DEFECT CODEX NAMED AT 939c7f1. This returned held_leg=None and
    candidate_legs=[] unconditionally, so `discover` was skipped every cycle."""
    async with _conn() as dbconn:
      await _clean(dbconn)
      await _catalogue(dbconn)
      iid = await _held_position(dbconn, intent_id="fpi-wired")
      pos = {"intent_id": iid, "us_market_slug": HELD, "residual_qty": 10,
             "avg_price": 0.62, "filled_qty": 10}
      got = await LOOP.funded_pair_inputs(
          dbconn, pos, at=NOW, account_id=ACCT, venue=VENUE,
          prose_reader=_prose_reader(),
          quoter=_quoter({SIB_SPREAD: (0.45, 200), SIB_TOTAL: (0.51, 150)}))
      assert got["ok"] is True
      assert isinstance(got["held_leg"], IS.Leg), got.get("held_leg_read")
      assert got["candidate_legs"], got["candidate_legs_read"]
      assert all(isinstance(c, IS.Leg) for c in got["candidate_legs"])
      # THE READINESS TABLE NO LONGER SAYS NOT WIRED.
      assert "NOT WIRED" not in got["readiness"]["held_leg"]
      assert "NOT WIRED" not in got["readiness"]["candidate_legs"]
      # AND THE REGISTRY STATE IS REPORTED RATHER THAN SUBSTITUTED FOR.
      assert got["region_probabilities"] is None
      assert got["region_probability_read"]["approved"] in (True, False)


async def test_a_catalogue_outage_names_a_refusal_and_keeps_the_exit():
    """MISSING HEDGE EVIDENCE PRESERVES A VALID EXIT. The supplier reports the
    refusal on `unavailable` and the HOLD/exit candidates are untouched."""
    async with _conn() as dbconn:
      await _clean(dbconn)
      await _catalogue(dbconn)
      iid = await _held_position(dbconn, intent_id="fpi-outage")
      pos = {"intent_id": iid, "us_market_slug": "aec-not-in-the-catalogue",
             "residual_qty": 10, "avg_price": 0.62, "filled_qty": 10}
      got = await LOOP.funded_pair_inputs(
          dbconn, pos, at=NOW, account_id=ACCT, venue=VENUE,
          prose_reader=_prose_reader(), quoter=_quoter({}))
      assert got["ok"] is True                    # the CYCLE is not taken out
      assert got["held_leg"] is None
      assert HS.R_NO_CATALOGUE_ROW in got["unavailable"]
      # The exit/hold ranking is still present and still rankable.
      assert "hold_ranking" in got


async def test_the_supplier_never_raises_out_of_the_cycle():
    """A transport that throws is a named refusal, not an exception on the
    decision path -- the shape that took out three cycles when
    `fetch_sport_catalogue` raised httpx.ProxyError."""
    async with _conn() as dbconn:
      await _clean(dbconn)
      await _catalogue(dbconn)
      iid = await _held_position(dbconn, intent_id="fpi-raise")

      async def boom(slug):
          raise RuntimeError("the venue transport exploded")

      got = await LOOP.funded_pair_inputs(
          dbconn, {"intent_id": iid, "us_market_slug": HELD,
                   "residual_qty": 10, "avg_price": 0.62, "filled_qty": 10},
          at=NOW, account_id=ACCT, venue=VENUE,
          prose_reader=boom, quoter=_quoter({}))
      assert got["ok"] is True
      assert got["held_leg"] is None
      assert got["unavailable"]
