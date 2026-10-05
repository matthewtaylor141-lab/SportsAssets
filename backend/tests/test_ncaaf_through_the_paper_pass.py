"""P0 INCIDENT · NCAAF THROUGH THE REAL PAPER PASS (Postgres).

THE STARVATION. Every college-football money line was refused: the
completed-game policy (CG V3) by NO_COMPLETED_GAME_TERMS_FOR_THIS_SPORT and
PINNACLE_PROBABILITY_NOT_QUALIFIED_BY_THE_LANE, the strict policy by a bare
SETTLEMENT_NOT_SUPPORTED (production, research-sql run 37241503567, N8: 9 of
9 cfb decisions per strategy). This file drives the REAL paper pass
(paper_runtime.paper_pass, the canonical hooks installed as
execution_intent.start installs them) on cfb valuations shaped like the
collector's rows, carrying the venue's OWN cfb rules text (the captured
listing's wording, which every production cfb row carries: N2, 9 of 9), and
proves:

  1 a qualified NCAAF candidate ENTERS (one canonical decision intent, the
    SMALL_LIVE SHADOW adapter beside the PAPER one), its PAPER order is
    created and SIMULATED-filled from the observed book -- the probability is
    the book's own number, unchanged (a completed college game cannot end
    tied: the cited rule), with every premise of that recorded; the evening
    kickoff maps to the venue slug's America/New_York day, not the UTC day;
  2 the strict policy keeps refusing the same row -- SETTLEMENT_NOT_SUPPORTED
    with each cited payout difference named behind it, never a generic code;
  3 EACH UNPROVEN CLAUSE IS ITS OWN REFUSAL through the real pass: the venue
    text without the cited winner / overtime / tie-review / postponement /
    result-source clause, with a contradicting tie clause appended, with an
    extra uncited clause, or with no text at all; a draw-priced book line;
    the no-tie rule evidence withdrawn; the book capture withdrawn; a slug
    dated by the UTC day -- none places an order;
  4 the 30 s probability rule is unchanged (a stale reading refuses);
  5 Xavier measures a held NCAAF position on the same reading the entry used,
    and refuses a reading whose premises no longer hold.

SYNTHETIC: prices, books and Pinnacle odds. Nothing reaches a venue; every
order is a PAPER order and every fill a SIMULATED one.
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from sportsassets import bettor_external_shadow as ext
from sportsassets import bettor_ncaaf_settlement as NC
from sportsassets import bettor_pinnacle_devig as devig
from sportsassets import live_parity as LP
from sportsassets.agents import paper_benchmark as PB
from sportsassets.agents import paper_derek as PD
from tests import paper_harness as H
from tests import paper_live_fixture as PL
from tests.test_nfl_through_the_paper_pass import (  # noqa: F401
    FEE1, _fee0, _pass, _settlement, _setup, cg_on_with_parity, check,
    decision, forget_settled_entry, hold_the_memory_learner,
    release_the_memory_learner)

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
CG = PB.CG_STRATEGY
LONG = "ORDER_INTENT_BUY_LONG"
MARK = "inc-ncaaf-paper-pass-test"
ET = ZoneInfo("America/New_York")

#: THE VENUE'S OWN cfb WORDING (tests/fixtures/pmus_cfb_listing_2026_10_03
#: .json; the one wording on every production cfb row, research-sql run
#: 37241503567 N2), with the game and its date as the venue states them.
TEMPLATE = (
    "This market will settle to the winner of the {game} College Football "
    "game scheduled for {date}. Overtime is included if played. If a tied "
    "final score is reported and no official winner is declared, this market "
    "will not resolve automatically and will be reviewed against the "
    "official governing-body result. If the game is delayed, postponed, or "
    "suspended and not rescheduled to a date within two weeks of the "
    "originally scheduled date, the market will settle to the last fair "
    "market price. Outcome sourced from the relevant governing body.")
GAME = "Fresno State vs Washington State"
# the captured game's book (production row 4977's odds): Washington State
# the favourite
ODDS = {"Fresno State Bulldogs": 2.06, "Washington State Cougars": 1.84}
PICK = "Washington State Cougars"


def evening_kickoff(now: float) -> float:
    """An EVENING kickoff (01:30Z = 21:30 ET the day before) at least three
    hours after `now`: the case whose UTC date differs from the venue's
    America/New_York date, like the captured Fresno State game."""
    k = (int(now // 86400) + 1) * 86400 + 5400
    return float(k if k - now >= 3 * 3600 else k + 86400)


def et_day(kickoff: float):
    return datetime.fromtimestamp(float(kickoff), ET).date()


def utc_day(kickoff: float):
    return datetime.fromtimestamp(float(kickoff), timezone.utc).date()


def venue_text(day, game=GAME) -> str:
    return TEMPLATE.format(game=game, date="%s %d, %d" % (
        day.strftime("%b"), day.day, day.year))


def slug_for(abbr: str, day) -> str:
    return "aec-cfb-%s-%s" % (abbr, day.isoformat())


def _book_p(odds: dict, selection: str) -> float:
    names = sorted(odds)
    probs = devig.devig([odds[n] for n in names], devig.DEFAULT_METHOD)
    return dict(zip(names, probs))[selection]


async def cfb_valuation(conn, *, slug, odds=None, text, decided_at,
                        selection=PICK, pin_age_s=5.0, p=None) -> dict:
    """One entry-experiment valuation of a cfb money line, shaped like the
    collector's row (CALIBRATION_ONLY, the book's priced set, the venue's
    rules text and the real attest -> projection of it, the strict
    settlement refusals by name)."""
    odds = dict(odds or ODDS)
    at = float(decided_at)
    p = _book_p(odds, selection) if p is None else float(p)
    scmp, unmet = _settlement(text, list(odds))
    vid = await conn.fetchval(
        "INSERT INTO external_valuations (experiment_id, version, "
        " source_class, provider, book, devig_method, venue, condition_id, "
        " us_market_slug, contract_selection, sport_family, market, period, "
        " raw_odds, outcomes_priced, expected_outcomes, observed_at, "
        " received_at, probability, decision, admissible, refusals, why, "
        " payout_event, payout_is_complement, buy_intent, ladder_side, "
        " record_purpose, decided_at, event_key, settlement_comparison, "
        " calibration_only_evidence, settlement_rule, mapped_outcome) "
        "VALUES ($1,'PINNACLE_DEVIG_V1','EXTERNAL_BOOKMAKER_VALUATION',"
        " 'the-odds-api.com/v4','pinnacle','power','PMUS',NULL,$2,$3,"
        " 'football','h2h','FULL_GAME',$4::jsonb,$13,2,to_timestamp($5),"
        " to_timestamp($5 + 1),$6,'NO_TRADE',false,$7::text[],'synthetic',"
        " $3,false,$8,'ASK','CALIBRATION_ONLY',to_timestamp($9),$10,"
        " $11::jsonb,$12::jsonb,'FULL_GAME_INCLUDING_OVERTIME',$3) "
        # mapped_outcome (integration with inc-edge): the de-vig writes the
        # priced outcome's own name on every collector row
        # (bettor_pinnacle_devig.valuation, out["mapped_outcome"]); the
        # gross-edge input validation recomputes the probability from it
        # and refuses a row without it as unverifiable, so the fixture now
        # carries it as the collector's row does
        "RETURNING id",
        ext.EXPERIMENT_ID, slug, selection, json.dumps(odds),
        at - float(pin_age_s), float(p),
        ["VENUE_BOOK_CURRENCY_NOT_ESTABLISHED"] + unmet, LONG, at,
        "%s-%s" % (MARK, uuid.uuid4().hex[:8]),
        json.dumps(scmp, default=str),
        json.dumps({"usable_for_orders": False,
                    "venue_read_refusal": "VENUE_BOOK_CURRENCY_NOT_"
                                          "ESTABLISHED"}),
        len(odds))
    return {"valuation_id": vid, "slug": slug, "p_book": p}


async def premap(conn, slug, *, title, kickoff):
    """The venue catalogue row (us_premap), shaped like PRODUCTION's cfb rows
    (tests/fixtures/pmus_cfb_catalogue_rows_2026_10_04.json, research-sql run
    37241920748): team league 'cfb', the venue's own question with its UTC
    clock, the line the premap sweep stamps from that clock (the minutes),
    and the venue's scheduled kickoff instant."""
    k = datetime.fromtimestamp(float(kickoff), timezone.utc)
    clock = k.strftime("%I:%M %p").lstrip("0")
    question = ("Who will win in the upcoming football event %s scheduled "
                "for %s %d, %d at %s UTC?" % (title, k.strftime("%B"), k.day,
                                              k.year, clock))
    await conn.execute(
        "INSERT INTO us_premap (identifier, event_slug, event_title, "
        " market_slug, question, kind, side_norm, event_keys, sports_type, "
        " game_start, team_league, line, updated_at) VALUES ($1,$2,$3,$1,$4,"
        " 'side','long',ARRAY[$5],'football_team_full_game_winner',"
        " to_timestamp($6),'cfb',$7,now()) ON CONFLICT (identifier, "
        " side_norm) DO UPDATE SET game_start=EXCLUDED.game_start, "
        " event_keys=EXCLUDED.event_keys, question=EXCLUDED.question, "
        " line=EXCLUDED.line, updated_at=now()",
        slug, slug.split("aec-", 1)[1], title, question, MARK,
        float(kickoff), k.strftime("%M"))


async def purge(conn, vids):
    async with conn.transaction():
        await conn.execute("SET LOCAL session_replication_role = replica")
        await conn.execute("DELETE FROM external_valuations WHERE "
                           " id = ANY($1::bigint[])", list(vids))
        await conn.execute("DELETE FROM us_premap WHERE $1 = ANY(event_keys)",
                           MARK)


async def orders_of(conn, dids) -> int:
    return await conn.fetchval(
        "SELECT count(*) FROM paper_orders WHERE decision_id = ANY($1)",
        list(dids))


# ═════════════════════════════════════════════════════════════════════
# 1 · ENTER -> PAPER ORDER -> SIMULATED PAPER FILL  ·  2 · STRICT REFUSAL
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_ncaaf_enters_orders_and_fills_on_paper(cg_on_with_parity):
    conn = await H.connect()
    now = time.time() + 5.0
    vids = []
    acct = None
    held = await hold_the_memory_learner(conn, now + 10_000.0)
    try:
        acct = await _setup(conn, "ncaafenter", now)
        kick = evening_kickoff(now)
        day = et_day(kick)
        assert day != utc_day(kick)          # the discriminating case
        slug = slug_for("frest-washst", day)
        await premap(conn, slug, title="Fresno State vs. Washington State",
                     kickoff=kick)
        v = await cfb_valuation(conn, slug=slug, text=venue_text(day),
                                decided_at=now - 10)
        vids.append(v["valuation_id"])
        t = PL.Transport(now)
        t.set(slug, offers=[(0.45, 300)], bids=[(0.43, 300)])
        client = PL.client(t)
        p1 = await _pass(conn, acct, t, now, client)
        assert p1["ran"] and not p1["errors"], p1["errors"]
        d = await decision(conn, acct, v["valuation_id"])
        assert d is not None, "the CG pass recorded no NCAAF decision"
        assert d["verdict"] == "ENTER", (d["refusal"], d["refusals"])
        # THE OLD REFUSALS ARE GONE, FOR THE RIGHT REASONS
        for gone in (PB.R_FAMILY, PB.R_PROBABILITY_UNQUALIFIED,
                     PB.R_NO_PINNACLE):
            assert gone not in (d["refusals"] or []), d["refusals"]
        gp = check(d, "ordinary_completion_grading_period")
        assert gp["passed"] and gp["book"]["period"] == PB.GP_FOOTBALL_NCAAF
        assert check(d, "ncaaf_venue_text_is_exactly_the_cited_clauses")[
            "passed"]
        fd = check(d, "ncaaf_fixture_date_matches_the_venue_slug")
        assert fd["passed"] and fd["fixture_date"]["event_date"] == \
            day.isoformat()
        assert fd["fixture_date"]["kickoff_et_date"] == day.isoformat()
        nt = check(d, "ncaaf_no_tie_state_by_the_cited_rule")
        assert nt["passed"] and nt["no_tie_rule"]["run_id"] == 37241934626

        # THE PROBABILITY: the book's number, UNCHANGED, with why on record
        pin = H.j(d["pinnacle"])
        assert float(d["p_pinnacle"]) == pytest.approx(v["p_book"],
                                                       abs=1e-12)
        assert pin["p_book_conditional_no_tie"] == pytest.approx(v["p_book"])
        assert pin["p_is"] == NC.P_IS_EQUIVALENT
        assert pin["venue_conversion"]["tie_probability_completed"] == 0.0
        assert pin["venue_conversion"]["refusal"] is None
        ex = pin["contract_match"]["exceptional_terms"]
        assert ex["ncaaf_settlement_states"]["exceptional_probability"] == \
            "UNMEASURED"
        econ = H.j(d["economics"])
        assert 0 <= econ["probability_age_at_decision_s"] <= 30.0
        assert econ["probability_limit_s"] == 30.0

        # ONE CANONICAL DECISION INTENT; PAPER SIMULATED + SMALL_LIVE SHADOW
        it = await conn.fetchrow(
            "SELECT * FROM canonical_decision_intents WHERE decision_id=$1",
            d["decision_id"])
        assert it is not None, "the NCAAF ENTER recorded no canonical intent"
        assert LP.verify_intent(dict(it))
        assert it["strategy"] == CG and it["sleeve"] == "INVESTMENT"
        o = await conn.fetchrow("SELECT * FROM paper_orders WHERE "
                                " decision_id=$1 AND role='ENTRY'",
                                d["decision_id"])
        assert o is not None, "the NCAAF ENTER created no PAPER order"
        assert float(o["qty"]) == float(it["target_qty"])
        assert float(o["limit_price"]) == float(it["limit_price"])
        ex_rows = {r["adapter"]: r for r in await conn.fetch(
            "SELECT * FROM canonical_intent_executions WHERE intent_id=$1",
            it["intent_id"])}
        assert set(ex_rows) == {"PAPER", "SMALL_LIVE"}
        assert ex_rows["PAPER"]["mode"] == "SIMULATED"
        assert ex_rows["SMALL_LIVE"]["mode"] == "SHADOW"
        assert "venue_order_id" not in H.j(ex_rows["SMALL_LIVE"]["refs"])

        # THE SIMULATED PAPER FILL, from the observed book
        for k in (5, 70):
            p = await _pass(conn, acct, t, now + k, client)
            assert not p["errors"], p["errors"]
        fills = await conn.fetch("SELECT * FROM paper_fills WHERE "
                                 " order_id=$1", o["order_id"])
        filled = sum(float(f["qty"]) for f in fills)
        assert filled > 0, "the NCAAF PAPER order never filled"
        assert all(float(f["price"]) <= float(o["limit_price"]) + 1e-9
                   for f in fills)
        # nothing reached a venue; SMALL LIVE stayed SHADOW
        assert client.mutation_attempts == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM small_live_order_events") == 0

        # 2 · THE STRICT POLICY REFUSES THE SAME ROW, EACH CLAUSE BY NAME
        dk = await decision(conn, acct, v["valuation_id"], PD.STRATEGY)
        if dk is not None:
            assert dk["verdict"] == "REFUSE"
            refs = list(dk["refusals"] or [])
            i = refs.index("SETTLEMENT_NOT_SUPPORTED")
            assert refs[i + 1:i + 1 + len(NC.STRICT_CODES)] == \
                list(NC.STRICT_CODES), refs
    finally:
        try:
            await forget_settled_entry(conn, acct and acct["account_id"])
            await purge(conn, vids)
            await PL.purge_everything(conn)
        finally:
            await release_the_memory_learner(conn, held)
            await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 3 · EACH UNPROVEN CLAUSE IS ITS OWN REFUSAL, THROUGH THE REAL PASS
# ═════════════════════════════════════════════════════════════════════

def _clause_cases(day):
    text = venue_text(day)
    game = ("This market will settle to the winner of the %s College "
            "Football game scheduled for %s %d, %d." % (
                GAME, day.strftime("%b"), day.day, day.year))
    return [
        # (abbr, what is wrong, the text, codes that must be refused)
        ("nowin-a", "winner clause missing", text.replace(game + " ", ""),
         (NC.R_VENUE_WINNER, PB.R_GP_UNKNOWN)),
        ("nflg-b", "the winner clause names an NFL game",
         text.replace("College Football game", "NFL game"),
         (NC.R_VENUE_WINNER,)),
        ("noot-c", "overtime excluded",
         text.replace(NC.Q_VENUE_OVERTIME, "Overtime is not included."),
         (NC.R_VENUE_OVERTIME, PB.R_GP_MISMATCH)),
        ("notie-d", "tie review clause missing",
         text.replace(NC.Q_VENUE_TIE_REVIEW + " ", ""), (NC.R_VENUE_TIE,)),
        ("addtie-e", "a contradicting tie clause appended",
         text + " If the game ends in a tie after overtime, all positions "
         "resolve to No.", (NC.R_VENUE_TIE, NC.R_VENUE_UNCITED)),
        ("ppd-f", "the postponement window varied",
         text.replace("within two weeks", "within one week"),
         (NC.R_VENUE_POSTPONED,)),
        ("src-g", "the result source varied",
         text.replace(NC.Q_VENUE_SOURCE, "Outcome sourced from ESPN."),
         (NC.R_VENUE_SOURCE,)),
        ("xtra-h", "an extra uncited clause",
         text + " Forfeited games resolve to the team awarded the win.",
         (NC.R_VENUE_UNCITED,)),
        ("none-i", "no venue text at all", "",
         (NC.R_VENUE_TEXT_ABSENT, PB.R_GP_TEXT_ABSENT)),
    ]


@pg
async def test_each_unproven_venue_clause_is_refused_through_the_pass(
        cg_on_with_parity):
    conn = await H.connect()
    now = time.time() + 5.0
    vids = []
    try:
        acct = await _setup(conn, "ncaafclause", now)
        kick = evening_kickoff(now)
        day = et_day(kick)
        t = PL.Transport(now)
        cases = []
        for abbr, what, text, codes in _clause_cases(day):
            slug = slug_for(abbr, day)
            await premap(conn, slug, title=GAME, kickoff=kick)
            v = await cfb_valuation(conn, slug=slug, text=text,
                                    decided_at=now - 10)
            vids.append(v["valuation_id"])
            t.set(slug, offers=[(0.45, 300)], bids=[(0.43, 300)])
            cases.append((what, codes, v))
        client = PL.client(t)
        p1 = await _pass(conn, acct, t, now, client)
        assert not p1["errors"], p1["errors"]
        dids = []
        for what, codes, v in cases:
            d = await decision(conn, acct, v["valuation_id"])
            assert d is not None, what
            assert d["verdict"] == "REFUSE", (what, d["refusals"])
            for code in codes:
                assert code in d["refusals"], (what, code, d["refusals"])
            # never the generic family refusal: the clause is named
            assert PB.R_FAMILY not in d["refusals"], (what, d["refusals"])
            dids.append(d["decision_id"])
        assert await orders_of(conn, dids) == 0
        assert client.mutation_attempts == 0
    finally:
        await purge(conn, vids)
        await PL.purge_everything(conn)
        await conn.close()


@pg
async def test_a_draw_priced_line_and_a_utc_dated_slug_are_refused(
        cg_on_with_parity):
    conn = await H.connect()
    now = time.time() + 5.0
    vids = []
    try:
        acct = await _setup(conn, "ncaafdraw", now)
        kick = evening_kickoff(now)
        day = et_day(kick)
        t = PL.Transport(now)
        # a draw-priced book line: the regulation market, never the game line
        drawn = slug_for("drawn-a", day)
        await premap(conn, drawn, title=GAME, kickoff=kick)
        vd = await cfb_valuation(
            conn, slug=drawn, text=venue_text(day), decided_at=now - 10,
            odds={"Fresno State Bulldogs": 2.9,
                  "Washington State Cougars": 2.6, "Draw": 9.0})
        # a slug (and text) dated by the kickoff's UTC day: the venue dates
        # by the America/New_York day, so the date is not established
        uday = utc_day(kick)
        utc_slug = slug_for("utc-b", uday)
        await premap(conn, utc_slug, title=GAME, kickoff=kick)
        vu = await cfb_valuation(conn, slug=utc_slug, text=venue_text(uday),
                                 decided_at=now - 10)
        vids += [vd["valuation_id"], vu["valuation_id"]]
        for s in (drawn, utc_slug):
            t.set(s, offers=[(0.45, 300)], bids=[(0.43, 300)])
        client = PL.client(t)
        pz = await _pass(conn, acct, t, now, client)
        assert pz["ran"] and not pz["errors"], pz["errors"]
        d1 = await decision(conn, acct, vd["valuation_id"])
        assert d1["verdict"] == "REFUSE"
        assert NC.R_BOOK_PRICES_DRAW in d1["refusals"], d1["refusals"]
        d2 = await decision(conn, acct, vu["valuation_id"])
        assert d2["verdict"] == "REFUSE"
        assert NC.R_DATE_INCONSISTENT in d2["refusals"], d2["refusals"]
        assert await orders_of(conn, [d1["decision_id"],
                                      d2["decision_id"]]) == 0
    finally:
        await purge(conn, vids)
        await PL.purge_everything(conn)
        await conn.close()


@pg
@pytest.mark.parametrize("withdrawn", ["no_tie_rule", "book_capture"])
async def test_withdrawn_evidence_refuses_by_name_through_the_pass(
        cg_on_with_parity, monkeypatch, withdrawn):
    """The SAME valuation that entered in test 1, with one piece of cited
    evidence withdrawn: the no-tie rule (every conversion then refuses
    NCAAF_NO_TIE_RULE_EVIDENCE_NOT_HELD) or the Pinnacle capture
    (NCAAF_BOOK_RULES_CAPTURE_NOT_HELD). Nothing falls back to an
    assumption, and no order is placed."""
    if withdrawn == "no_tie_rule":
        monkeypatch.setattr(NC, "NO_TIE_RULE_EVIDENCE", {})
        code = NC.R_NO_TIE_RULE
    else:
        monkeypatch.setattr(NC, "PINNACLE_CAPTURES", ())
        code = NC.R_BOOK_RULES
    conn = await H.connect()
    now = time.time() + 5.0
    vids = []
    try:
        acct = await _setup(conn, "ncaafwd" + withdrawn[:4], now)
        kick = evening_kickoff(now)
        day = et_day(kick)
        slug = slug_for("frest-washst", day)
        await premap(conn, slug, title=GAME, kickoff=kick)
        v = await cfb_valuation(conn, slug=slug, text=venue_text(day),
                                decided_at=now - 10)
        vids.append(v["valuation_id"])
        t = PL.Transport(now)
        t.set(slug, offers=[(0.45, 300)], bids=[(0.43, 300)])
        client = PL.client(t)
        pz = await _pass(conn, acct, t, now, client)
        assert pz["ran"] and not pz["errors"], pz["errors"]
        d = await decision(conn, acct, v["valuation_id"])
        assert d["verdict"] == "REFUSE"
        assert code in d["refusals"], d["refusals"]
        assert not check(d, "ncaaf_no_tie_state_by_the_cited_rule")["passed"]
        assert await orders_of(conn, [d["decision_id"]]) == 0
    finally:
        await purge(conn, vids)
        await PL.purge_everything(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 4 · THE 30 s RULE, UNCHANGED
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_a_stale_ncaaf_probability_is_refused_by_the_unchanged_rule(
        cg_on_with_parity):
    conn = await H.connect()
    now = time.time() + 5.0
    vids = []
    try:
        acct = await _setup(conn, "ncaafstale", now)
        kick = evening_kickoff(now)
        day = et_day(kick)
        slug = slug_for("frest-washst", day)
        await premap(conn, slug, title=GAME, kickoff=kick)
        v = await cfb_valuation(conn, slug=slug, text=venue_text(day),
                                decided_at=now - 10, pin_age_s=25.0)
        vids.append(v["valuation_id"])
        t = PL.Transport(now)
        t.set(slug, offers=[(0.45, 300)], bids=[(0.43, 300)])
        client = PL.client(t)
        pz = await _pass(conn, acct, t, now, client)
        assert pz["ran"] and not pz["errors"], pz["errors"]
        d = await decision(conn, acct, v["valuation_id"])
        assert d["verdict"] == "REFUSE"
        assert PB.R_STALE in d["refusals"], d["refusals"]
        pin = H.j(d["pinnacle"])
        assert pin["age_s"] > 30.0 and pin["limit_s"] == 30.0
        assert await orders_of(conn, [d["decision_id"]]) == 0
    finally:
        await purge(conn, vids)
        await PL.purge_everything(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 5 · XAVIER: A HELD NCAAF POSITION, MEASURED ON THE ENTRY'S READING
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_xavier_measures_a_held_ncaaf_contract_on_the_same_reading(
        cg_on_with_parity, monkeypatch):
    conn = await H.connect()
    now = time.time() + 5.0
    vids = []
    try:
        acct = await _setup(conn, "ncaafxavier", now)
        kick = evening_kickoff(now)
        day = et_day(kick)
        slug = slug_for("frest-washst", day)
        await premap(conn, slug, title=GAME, kickoff=kick)
        v = await cfb_valuation(conn, slug=slug, text=venue_text(day),
                                decided_at=now - 10)
        vids.append(v["valuation_id"])
        t = PL.Transport(now)
        t.set(slug, offers=[(0.45, 2000)], bids=[(0.43, 2000)])
        pz = await _pass(conn, acct, t, now, PL.client(t))
        assert pz["ran"] and not pz["errors"], pz["errors"]
        d = await decision(conn, acct, v["valuation_id"])
        assert d["verdict"] == "ENTER", (d["refusal"], d["refusals"])
        o = await conn.fetchrow("SELECT * FROM paper_orders WHERE "
                                " decision_id=$1 AND role='ENTRY'",
                                d["decision_id"])
        pos = {"group_id": o["group_id"], "holding_side": o["holding_side"],
               "us_market_slug": slug}
        at = now + 120.0
        p_feed = 0.66

        async def feed(conn_, *, pos, at, max_age_s, payout_event,
                       payout_is_complement):
            return {"ok": True, "p": p_feed, "p_selection": p_feed,
                    "sport_id": 5, "feed_event_id": 88,
                    "designation": "home",
                    "provenance": {"source_change_ms": (at - 2.0) * 1000.0,
                                   "received_ms": (at - 1.9) * 1000.0,
                                   "quote_age_s": 2.0, "stream": "prematch"},
                    "devig": {"version": devig.VERSION},
                    "conditional_on": {"condition": "NO_TIE"}}
        ctx = {"now": at, "config": acct["config"]}
        m = await PB.xavier_measure(conn, ctx, pos=pos, strategy=CG,
                                    feed=feed)
        assert m["source"] == PB.SOURCE_FEED_CURRENT, m
        assert m["p"] == pytest.approx(p_feed, abs=1e-12)
        assert m["p_book_conditional_no_tie"] == pytest.approx(p_feed)
        assert m["p_is"] == NC.P_IS_EQUIVALENT
        # the premises withdrawn: the reading is refused, never used raw
        monkeypatch.setattr(NC, "NO_TIE_RULE_EVIDENCE", {})
        m2 = await PB.xavier_measure(conn, ctx, pos=pos, strategy=CG,
                                     feed=feed)
        assert m2["p"] is None and m2["stale"] is True, m2
        assert m2["venue_conversion"]["refusal"] == NC.R_NO_TIE_RULE
        assert NC.R_NO_TIE_RULE in m2["why"]
    finally:
        await purge(conn, vids)
        await PL.purge_everything(conn)
        await conn.close()
