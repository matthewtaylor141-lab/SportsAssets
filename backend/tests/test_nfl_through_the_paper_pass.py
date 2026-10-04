"""R30A · THE NFL THROUGH THE REAL PAPER PASS (Postgres).

THE P0. Every NFL market was refused: the completed-game policy (CG V3) by
NO_COMPLETED_GAME_TERMS_FOR_THIS_SPORT and PINNACLE_PROBABILITY_NOT_QUALIFIED_
BY_THE_LANE, the strict policy by SETTLEMENT_NOT_SUPPORTED. This file drives
the REAL paper pass (paper_runtime.paper_pass, the canonical hooks installed
as execution_intent.start installs them) on NFL valuations shaped like the
collector's rows, and proves:

  1 ENTER, with ONE canonical decision intent and the SMALL_LIVE SHADOW
    adapter row beside the PAPER one, the probability's age at the decision
    recorded, and the probability CONVERTED for the venue's tie payout;
  2 a below-edge refusal -- a book that clears the edge on the book's raw
    P(win | no tie) but not on the contract's value at the worst tie rate;
  3 a stale-probability refusal (the 30 s rule, unchanged);
  4 settlement-mismatch refusals from conflicting venue rule texts
    (overtime excluded; a tie payout that is not the cited $0.50);
  5 the tie arithmetic: the decision's p is (1 - t) p_book + 0.5 t at the
    worst end of the cited interval, the conditional EV is computed on it,
    and a tied game's settlement pays 0.50 per contract in the paper ledger;
  6 the London game (09:30 ET kickoff) and the Sunday-night game whose UTC
    date is Monday both map to the venue slug's America/New_York date, and a
    slug dated by the UTC day is refused by name (the Sunday-night game is
    the case that DISCRIMINATES a UTC mapping: a London kickoff falls on the
    same date in UTC, London and New York, so that test proves instead that
    the kickoff instant is read on the ET clock and is checked at all);
  7 the Pro Bowl is never traded, and -- R30A review -- neither is a
    preseason game worded like the regular season, nor a playoff-window
    game: the phase is ESTABLISHED from the cited season window;
  8 (R30A review) a contradicting tie clause APPENDED to the venue's text is
    refused; the maker's resting bid is re-checked on the converted scale
    and cancelled when the conversion cannot be made; Xavier's feed reading
    of a held NFL contract is converted the same way.

SYNTHETIC: prices, books and Pinnacle odds; the venue rules text is the
venue's own (captured listings), except where a test states it substitutes a
conflicting text on purpose. Nothing reaches a venue.
"""
from __future__ import annotations

import json
import time
import uuid

import pytest

from sportsassets import bettor_external_shadow as ext
from sportsassets import bettor_nfl_settlement as NFL
from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_pinnacle_devig as devig
from sportsassets import live_parity as LP
from sportsassets.agents import paper_benchmark as PB
from sportsassets.agents import paper_derek as PD
from sportsassets.agents import paper_runtime as PR
from tests import paper_harness as H
from tests import paper_live_fixture as PL

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
CG = PB.CG_STRATEGY
LONG = "ORDER_INTENT_BUY_LONG"
MARK = "r30a-nfl-paper-pass-test"

#: THE VENUE'S OWN TEXT (tests/fixtures/pmus_nfl_listing_2026_10_04.json)
LONDON_TEXT = (
    "This market will settle to the winner of the Indianapolis Colts vs "
    "Washington Commanders NFL game scheduled for Oct 4, 2026. Overtime is "
    "included if played. If the game ends in a tie, the market will settle to "
    "$0.50. If the game is delayed, postponed, or suspended and not "
    "rescheduled to a date within two weeks of the originally scheduled date, "
    "the market will settle to the last fair market price. Outcome sourced "
    "from NFL.")
#: tests/fixtures/pmus_nfl_listing_2026_10_04_snf_mnf.json
SNF_TEXT = (
    "This market will settle to the winner of the Detroit Lions vs Carolina "
    "Panthers NFL game scheduled for Oct 4, 2026. Overtime is included if "
    "played. If the game ends in a tie, the market will settle to $0.50. If "
    "the game is delayed, postponed, or suspended and not rescheduled to a "
    "date within two weeks of the originally scheduled date, the market will "
    "settle to the last fair market price. Outcome sourced from NFL.")
LONDON_KICKOFF = 1791120600.0     # 2026-10-04T13:30:00Z = 09:30 ET
SNF_KICKOFF = 1791159600.0        # 2026-10-05T00:20:00Z = 20:20 ET on 10-04


def _fee0(qty, price, at=None):
    return (0.0, H.ZERO_FEES_BASIS)


FEE1 = H.flat_fee(0.01)


async def _nosleep(_):
    return None


async def _pass(conn, acct, transport, now, client, fee=FEE1):
    transport.t = max(transport.t, float(now))
    return await PR.paper_pass(conn, now=now, account_id=acct["account_id"],
                               market_data=client, config=acct["config"],
                               force=True, fee_fn=fee, sleep=_nosleep)


def _book_p(odds: dict, selection: str) -> float:
    names = sorted(odds)
    probs = devig.devig([odds[n] for n in names], devig.DEFAULT_METHOD)
    return dict(zip(names, probs))[selection]


def _settlement(text: str, names: list) -> dict:
    """The row's settlement_comparison as the COLLECTOR writes it: the real
    attest -> projection path on the given venue text."""
    from sportsassets import bettor_venue_settlement as V
    from sportsassets.workers import ext_pinnacle_loop as X
    a = V.attest(sport_family="football",
                 venue_evidence={"rules_text": text,
                                 "rules_source": "TEST_FIXTURE"},
                 book_evidence={"outcome_names": list(names)})
    return dict(X._settlement_compatibility(a), venue_rules_read=True,
                venue_rules_text=text), list(a.get("unmet") or [])


async def nfl_valuation(conn, *, slug, selection, odds, text, decided_at,
                        pin_age_s=5.0, p=None) -> dict:
    """One entry-experiment valuation of an NFL money line, shaped like the
    collector's row (CALIBRATION_ONLY, the book's priced set, the venue's
    rules text, the strict settlement refusals by name). `p` overrides the
    stored probability (a newer reading for a re-check)."""
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
        " calibration_only_evidence, settlement_rule) "
        "VALUES ($1,'PINNACLE_DEVIG_V1','EXTERNAL_BOOKMAKER_VALUATION',"
        " 'the-odds-api.com/v4','pinnacle','power','PMUS',NULL,$2,$3,"
        " 'football','h2h','FULL_GAME',$4::jsonb,2,2,to_timestamp($5),"
        " to_timestamp($5 + 1),$6,'NO_TRADE',false,$7::text[],'synthetic',"
        " $3,false,$8,'ASK','CALIBRATION_ONLY',to_timestamp($9),$10,"
        " $11::jsonb,$12::jsonb,'FULL_GAME_INCLUDING_OVERTIME') RETURNING id",
        ext.EXPERIMENT_ID, slug, selection, json.dumps(odds),
        at - float(pin_age_s), float(p),
        ["VENUE_BOOK_CURRENCY_NOT_ESTABLISHED"] + unmet, LONG, at,
        "%s-%s" % (MARK, uuid.uuid4().hex[:8]),
        json.dumps(scmp, default=str),
        json.dumps({"usable_for_orders": False,
                    "venue_read_refusal": "VENUE_BOOK_CURRENCY_NOT_"
                                          "ESTABLISHED"}))
    return {"valuation_id": vid, "slug": slug, "p_book": p}


async def premap(conn, slug, *, title, kickoff):
    """The venue catalogue row for the contract (us_premap), shaped like
    PRODUCTION's (tests/fixtures/pmus_nfl_catalogue_rows_2026_10_04.json):
    the venue's own question with its UTC clock, and the line the premap
    sweep stamps from that clock (the minutes), with the venue's own
    scheduled kickoff instant."""
    from datetime import datetime, timezone
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
        " to_timestamp($6),'nfl',$7,now()) ON CONFLICT (identifier, "
        " side_norm) DO UPDATE SET game_start=EXCLUDED.game_start, "
        " event_keys=EXCLUDED.event_keys, question=EXCLUDED.question, "
        " line=EXCLUDED.line, updated_at=now()",
        slug, "nfl-" + slug.split("aec-nfl-", 1)[1], title, question, MARK,
        float(kickoff), k.strftime("%M"))


async def hold_the_memory_learner(conn, until: float):
    """THE MEMORY LEARNER IS HELD FOR A PROOF THAT SETTLES AND THEN REMOVES
    A SCRATCH ENTRY (R30A review). The paper pass runs agent_memory.step
    whenever it is due (every RUN_EVERY_S), and it derives an append-only
    lesson from every settled entry, citing the decision and settlement as
    evidence. This proof deletes its scratch account's records when it ends,
    so a lesson written mid-proof cited records that no longer existed and
    test_agent_identity_memory failed on the dangling evidence -- whether it
    did depended on when the learner last ran. Its watermark is set ahead of
    the proof's clock (NOT_DUE) and restored afterwards; nothing else about
    the learner changes. Returns the previous watermark value."""
    from sportsassets.agents import agent_memory as AM
    prev = await conn.fetchval("SELECT value::text FROM ingestion_state "
                               " WHERE key=$1", AM.WATERMARK_KEY)
    cur = json.loads(prev) if prev else {}
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
        AM.WATERMARK_KEY, json.dumps(dict(cur, at=float(until))))
    return prev


async def release_the_memory_learner(conn, prev) -> None:
    from sportsassets.agents import agent_memory as AM
    if prev is None:
        await conn.execute("DELETE FROM ingestion_state WHERE key=$1",
                           AM.WATERMARK_KEY)
    else:
        await conn.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
            AM.WATERMARK_KEY, prev)


async def purge(conn, vids):
    async with conn.transaction():
        await conn.execute("SET LOCAL session_replication_role = replica")
        await conn.execute("DELETE FROM external_valuations WHERE "
                           " id = ANY($1::bigint[])", list(vids))
        await conn.execute("DELETE FROM us_premap WHERE $1 = ANY(event_keys)",
                           MARK)


async def forget_settled_entry(conn, account_id) -> None:
    """The tie proof SETTLES a scratch account's entry. A settled ENTER is
    evidence to every global reader of the shared test database -- the
    agent-memory learner derives a lesson from each one and later checks
    that its evidence still exists, and the Xavier replay reads every
    non-demonstration decision -- so this account's settlement and Xavier
    records go when the proof ends, the append-only triggers bypassed for
    this transaction only. Every other account's records are untouched."""
    from tests._xavier_record_cleanup import purge_xavier_records
    if not account_id:
        return
    async with conn.transaction():
        await conn.execute("SET LOCAL session_replication_role = replica")
        await conn.execute("DELETE FROM paper_settlements WHERE "
                           " account_id=$1", account_id)
    await purge_xavier_records(conn, account_id)


async def decision(conn, acct, vid, strategy=CG):
    return await conn.fetchrow(
        "SELECT * FROM paper_decisions WHERE session_id=$1 AND "
        " valuation_id=$2 AND strategy=$3", acct["session_id"], vid,
        strategy)


def check(d, name):
    pin = H.j(d["pinnacle"])
    for c in (pin.get("contract_match") or {}).get("checks") or []:
        if c["check"] == name:
            return c
    raise AssertionError("no check %r in %s" % (name, pin.get(
        "contract_match")))


@pytest.fixture
def cg_on_with_parity(monkeypatch, new_strategies_off):
    monkeypatch.setenv(PB.ENV_FLAG, "on")
    monkeypatch.setenv(PL.S.ENV_FLAG, "on")
    PL.set_policy_control(PB.CG_POLICY["control_key"], True)
    PL.set_policy_control(PB.CONTROL_KEY, False)
    PB._CONTEXT_CACHE.clear()
    PD._CONTEXT_CACHE.clear()
    LP.install()
    yield
    LP.uninstall()
    PB._CONTEXT_CACHE.clear()


async def _setup(conn, tag, now):
    await PL.purge_everything(conn)
    ctl = await LP.control(conn)
    if ctl.get("halted"):
        await LP.clear_halt(conn, actor="test harness (human operator)",
                            reason="isolate this test")
    if await conn.fetchval("SELECT count(*) FROM execmirror_control") == 0:
        await conn.execute("INSERT INTO execmirror_control DEFAULT VALUES")
    await conn.execute(
        "INSERT INTO execmirror_snapshots (at, balances, positions, "
        " open_orders) VALUES (now(), $1::jsonb, '[]', 0)",
        json.dumps([{"currency": "USD", "buyingPower": 500}]))
    return await PL.new_account(conn, tag, now=now)


# Colts as the favourite on the London game (production row 5329's odds)
LONDON_ODDS = {"Indianapolis Colts": 1.48, "Washington Commanders": 2.80}


# ═════════════════════════════════════════════════════════════════════
# 1 · ENTER: canonical intent, SHADOW adapter, converted probability
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_nfl_enters_with_one_intent_and_the_shadow_adapter(
        cg_on_with_parity):
    conn = await H.connect()
    now = time.time() + 5.0
    vids = []
    try:
        acct = await _setup(conn, "nflenter", now)
        slug = "aec-nfl-ind-was-2026-10-04"
        await premap(conn, slug, title="IND Colts vs. WAS Commanders",
                     kickoff=LONDON_KICKOFF)
        v = await nfl_valuation(conn, slug=slug, selection="Indianapolis Colts",
                                odds=LONDON_ODDS, text=LONDON_TEXT,
                                decided_at=now - 10)
        vids.append(v["valuation_id"])
        t = PL.Transport(now)
        t.set(slug, offers=[(0.55, 2000)], bids=[(0.53, 2000)])
        client = PL.client(t)
        p1 = await _pass(conn, acct, t, now, client)
        assert p1["ran"] and not p1["errors"], p1["errors"]
        d = await decision(conn, acct, v["valuation_id"])
        assert d is not None, "the CG pass recorded no NFL decision"
        assert d["verdict"] == "ENTER", (d["refusal"], d["refusals"])
        # THE OLD REFUSALS ARE GONE, FOR THE RIGHT REASONS
        for gone in (PB.R_FAMILY, PB.R_PROBABILITY_UNQUALIFIED,
                     PB.R_NO_PINNACLE):
            assert gone not in (d["refusals"] or []), d["refusals"]
        gp = check(d, "ordinary_completion_grading_period")
        assert gp["passed"] and gp["book"]["period"] == PB.GP_FOOTBALL_NFL
        assert check(d, "nfl_tie_priced_from_cited_evidence")["passed"]

        # THE PROBABILITY: converted, with the book's own number beside it
        pin = H.j(d["pinnacle"])
        conv = NFL.venue_value(v["p_book"], phase=NFL.PHASE_REGULAR)
        assert pin["p_book_conditional_no_tie"] == pytest.approx(v["p_book"])
        assert float(d["p_pinnacle"]) == pytest.approx(conv["p"], abs=1e-12)
        assert conv["tie_rate_end_used"] == "HIGHEST"      # a favourite
        assert pin["venue_conversion"]["tie_rate_used"] == pytest.approx(
            NFL.tie_rate_interval()["hi"])
        # THE PROBABILITY'S AGE AT THE DECISION, under the unchanged 30 s rule
        econ = H.j(d["economics"])
        assert econ["probability_age_at_decision_s"] == pin["age_s"]
        assert 0 <= econ["probability_age_at_decision_s"] <= 30.0
        assert econ["probability_limit_s"] == 30.0
        assert H.j(d["policy_decision"])["probability_age_at_decision_s"] \
            == pin["age_s"]

        # ONE CANONICAL DECISION INTENT, sha-verified, carrying p_venue
        it = await conn.fetchrow(
            "SELECT * FROM canonical_decision_intents WHERE decision_id=$1",
            d["decision_id"])
        assert it is not None, "the NFL ENTER recorded no canonical intent"
        assert LP.verify_intent(dict(it))
        assert it["strategy"] == CG and it["sleeve"] == "INVESTMENT"
        o = await conn.fetchrow("SELECT * FROM paper_orders WHERE "
                                " decision_id=$1 AND role='ENTRY'",
                                d["decision_id"])
        assert float(o["qty"]) == float(it["target_qty"])
        assert float(o["limit_price"]) == float(it["limit_price"])
        # THE TWO ADAPTERS: PAPER SIMULATED and SMALL_LIVE SHADOW, one sha
        ex = {r["adapter"]: r for r in await conn.fetch(
            "SELECT * FROM canonical_intent_executions WHERE intent_id=$1",
            it["intent_id"])}
        assert set(ex) == {"PAPER", "SMALL_LIVE"}
        assert ex["PAPER"]["mode"] == "SIMULATED"
        assert ex["SMALL_LIVE"]["mode"] == "SHADOW"
        assert ex["SMALL_LIVE"]["state"] in ("SHADOW_PROPOSED",
                                             "SHADOW_EXCLUDED")
        assert {r["intent_sha"] for r in ex.values()} == {it["content_sha"]}
        assert "venue_order_id" not in H.j(ex["SMALL_LIVE"]["refs"])
        par = await conn.fetchrow(
            "SELECT * FROM live_parity_ledger WHERE intent_id=$1",
            it["intent_id"])
        assert par is not None
        assert par["parity_state"] != "LOGIC_DIVERGENCE", H.j(
            par["comparison"])
        assert client.mutation_attempts == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM small_live_order_events") == 0
        # the strict two-model policy keeps refusing the same row, by name
        dk = await decision(conn, acct, v["valuation_id"], PD.STRATEGY)
        if dk is not None:
            assert dk["verdict"] == "REFUSE"
    finally:
        await purge(conn, vids)
        await PL.purge_everything(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 2 · BELOW EDGE AT THE WORST TIE RATE  ·  3 · STALE PROBABILITY
# ═════════════════════════════════════════════════════════════════════

#: A heavy favourite: p_book ~ 0.8085. Against an offer of 0.80 the book's
#: raw P(win | no tie) clears the 0.5 pp threshold (~0.85 pp); the
#: contract's value at the worst tie rate does not (~0.22 pp).
FAV_ODDS = {"Kansas City Chiefs": 1.215, "Las Vegas Raiders": 4.55}


@pg
async def test_nfl_below_edge_at_the_worst_tie_rate(cg_on_with_parity):
    conn = await H.connect()
    now = time.time() + 5.0
    vids = []
    try:
        acct = await _setup(conn, "nflbelow", now)
        slug = "aec-nfl-kc-lv-2026-10-04"
        v = await nfl_valuation(
            conn, slug=slug, selection="Kansas City Chiefs", odds=FAV_ODDS,
            text=LONDON_TEXT.replace(
                "Indianapolis Colts vs Washington Commanders",
                "Kansas City Chiefs vs Las Vegas Raiders"),
            decided_at=now - 10)
        vids.append(v["valuation_id"])
        conv = NFL.venue_value(v["p_book"], phase=NFL.PHASE_REGULAR)
        price = 0.80
        min_edge = PB.CG_MIN_EDGE_PP_V2 / 100.0
        # the premise, computed rather than asserted: raw clears, converted
        # does not
        assert v["p_book"] - price >= min_edge, v["p_book"]
        assert conv["p"] - price < min_edge, conv
        t = PL.Transport(now)
        t.set(slug, offers=[(price, 2000)], bids=[(0.78, 2000)])
        client = PL.client(t)
        p1 = await _pass(conn, acct, t, now, client, fee=_fee0)
        assert not p1["errors"], p1["errors"]
        d = await decision(conn, acct, v["valuation_id"])
        assert d["verdict"] == "REFUSE"
        assert d["refusal"] == PB.R_EDGE, (d["refusal"], d["refusals"])
        pd = H.j(d["policy_decision"])
        assert pd["gross_edge_pp"] == pytest.approx(
            (conv["p"] - price) * 100.0, abs=1e-6)
        assert await conn.fetchval("SELECT count(*) FROM paper_orders WHERE "
                                   " decision_id=$1", d["decision_id"]) == 0
    finally:
        await purge(conn, vids)
        await PL.purge_everything(conn)
        await conn.close()


@pg
async def test_nfl_stale_probability_is_refused_by_the_unchanged_30s_rule(
        cg_on_with_parity):
    conn = await H.connect()
    now = time.time() + 5.0
    vids = []
    try:
        acct = await _setup(conn, "nflstale", now)
        slug = "aec-nfl-ind-was-2026-10-04"
        v = await nfl_valuation(conn, slug=slug,
                                selection="Indianapolis Colts",
                                odds=LONDON_ODDS, text=LONDON_TEXT,
                                decided_at=now - 10, pin_age_s=25.0)
        vids.append(v["valuation_id"])
        t = PL.Transport(now)
        t.set(slug, offers=[(0.55, 2000)], bids=[(0.53, 2000)])
        client = PL.client(t)
        await _pass(conn, acct, t, now, client)
        d = await decision(conn, acct, v["valuation_id"])
        assert d["verdict"] == "REFUSE"
        assert PB.R_STALE in d["refusals"], d["refusals"]
        pin = H.j(d["pinnacle"])
        assert pin["age_s"] > 30.0 and pin["limit_s"] == 30.0
        assert await conn.fetchval("SELECT count(*) FROM paper_orders WHERE "
                                   " decision_id=$1", d["decision_id"]) == 0
    finally:
        await purge(conn, vids)
        await PL.purge_everything(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 4 · CONFLICTING VENUE RULE TEXTS
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_nfl_conflicting_venue_rules_are_refused(cg_on_with_parity):
    conn = await H.connect()
    now = time.time() + 5.0
    vids = []
    try:
        acct = await _setup(conn, "nflrules", now)
        t = PL.Transport(now)
        no_ot = LONDON_TEXT.replace("Overtime is included if played.",
                                    "Overtime is not included.")
        tie_no = LONDON_TEXT.replace(
            "If the game ends in a tie, the market will settle to $0.50.",
            "If the game ends in a tie, the market will resolve to No.")
        # R30A review: the cited sentence KEPT, and a contradicting tie
        # clause appended after it -- this one ENTERED before
        appended = LONDON_TEXT + (" If the game ends in a tie after "
                                  "overtime, all positions resolve to No.")
        cases = []
        for slug, text in (("aec-nfl-ind-was-2026-10-04", no_ot),
                           ("aec-nfl-lar-phi-2026-10-04", tie_no),
                           ("aec-nfl-nyj-chi-2026-10-04", appended)):
            v = await nfl_valuation(conn, slug=slug,
                                    selection="Indianapolis Colts",
                                    odds=LONDON_ODDS, text=text,
                                    decided_at=now - 10)
            vids.append(v["valuation_id"])
            t.set(slug, offers=[(0.55, 2000)], bids=[(0.53, 2000)])
            cases.append(v)
        client = PL.client(t)
        await _pass(conn, acct, t, now, client)
        d1 = await decision(conn, acct, cases[0]["valuation_id"])
        assert d1["verdict"] == "REFUSE"
        assert PB.R_GP_MISMATCH in d1["refusals"], d1["refusals"]
        d2 = await decision(conn, acct, cases[1]["valuation_id"])
        assert d2["verdict"] == "REFUSE"
        assert PB.R_GP_UNKNOWN in d2["refusals"], d2["refusals"]
        assert NFL.R_VENUE_TIE_NOT_HALF in d2["refusals"], d2["refusals"]
        d3 = await decision(conn, acct, cases[2]["valuation_id"])
        assert d3["verdict"] == "REFUSE"
        assert NFL.R_VENUE_TIE_NOT_HALF in d3["refusals"], d3["refusals"]
        tie_check = check(d3, "nfl_tie_priced_from_cited_evidence")
        assert not tie_check["passed"]
        assert len(tie_check["venue_tie"]["tie_sentences"]) == 2
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_orders WHERE decision_id = ANY($1)",
            [d1["decision_id"], d2["decision_id"], d3["decision_id"]]) == 0
    finally:
        await purge(conn, vids)
        await PL.purge_everything(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 5 · THE TIE: EV AT THE WORST RATE, AND A TIE SETTLES AT 0.50
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_nfl_tie_arithmetic_and_a_tie_settles_at_half(
        cg_on_with_parity):
    conn = await H.connect()
    now = time.time() + 5.0
    vids = []
    acct = None
    held = await hold_the_memory_learner(conn, now + 10_000.0)
    try:
        acct = await _setup(conn, "nfltie", now)
        slug = "aec-nfl-ind-was-2026-10-04"
        v = await nfl_valuation(conn, slug=slug,
                                selection="Indianapolis Colts",
                                odds=LONDON_ODDS, text=LONDON_TEXT,
                                decided_at=now - 10)
        vids.append(v["valuation_id"])
        t = PL.Transport(now)
        t.set(slug, offers=[(0.55, 300)], bids=[(0.53, 300)])
        client = PL.client(t)
        await _pass(conn, acct, t, now, client)
        d = await decision(conn, acct, v["valuation_id"])
        assert d["verdict"] == "ENTER", (d["refusal"], d["refusals"])
        iv = NFL.tie_rate_interval()
        t_hi = iv["hi"]
        p_book = v["p_book"]
        p_venue = (1.0 - t_hi) * p_book + 0.5 * t_hi
        assert float(d["p_pinnacle"]) == pytest.approx(p_venue, abs=1e-12)
        # the WORST end: any other evidenced rate gives the favourite more
        assert p_venue < (1.0 - iv["lo"]) * p_book + 0.5 * iv["lo"]
        econ = H.j(d["economics"])["acquisition"]
        q = sum(float(w["take"]) for w in econ["walk"])
        gross = sum(float(w["take"]) * (p_venue - float(w["price"]))
                    for w in econ["walk"])
        assert econ["expected_net_profit_usd"] == pytest.approx(
            gross - float(econ["fees_usd"]), abs=1e-6)
        assert q > 0
        # an underdog's worst case is the LOWEST evidenced rate: the least
        # tie credit the evidence allows
        dog = NFL.venue_value(1.0 - p_book, phase=NFL.PHASE_REGULAR)
        assert dog["tie_rate_end_used"] == "LOWEST"
        assert dog["p"] == pytest.approx(
            (1.0 - iv["lo"]) * (1.0 - p_book) + 0.5 * iv["lo"])
        assert check(d, "nfl_regular_season_fixture_established")["passed"]

        # FILL, then the venue settles the TIED game at 0.50
        for k in (5, 70):
            p = await _pass(conn, acct, t, now + k, client)
            assert not p["errors"], p["errors"]
        o = await conn.fetchrow("SELECT * FROM paper_orders WHERE "
                                " decision_id=$1 AND role='ENTRY'",
                                d["decision_id"])
        filled = float(await conn.fetchval(
            "SELECT coalesce(sum(qty), 0) FROM paper_fills WHERE order_id=$1",
            o["order_id"]))
        assert filled > 0, "the NFL entry never filled"
        await conn.execute(
            "UPDATE external_valuations SET settlement_read='0.5', "
            " settlement_read_at=now() WHERE id=$1", v["valuation_id"])
        p = await _pass(conn, acct, t, now + 200, client)
        assert not p["errors"], p["errors"]
        s = await conn.fetchrow(
            "SELECT * FROM paper_settlements WHERE account_id=$1 AND "
            " us_market_slug=$2 ORDER BY version DESC LIMIT 1",
            acct["account_id"], slug)
        assert s is not None, "the tied game was not settled"
        assert s["outcome"] == "SETTLED_AT_VENUE_PRICE"
        assert float(s["payout_per_contract"]) == pytest.approx(0.5)
        assert float(s["payout_usd"]) == pytest.approx(0.5 * float(s["qty"]))
        ev = H.j(s["evidence"])
        # the venue's read is a PRICE: with the tie and the last-fair-price
        # clauses both stated, a 0.50 is a tie OR an exceptional price
        # settlement, and the record says so instead of claiming the tie
        assert ev["settlement_state"] == NFL.S_TIE_OR_LAST_FAIR_PRICE
        assert ev["state_class"] == NFL.AMBIGUOUS
        assert ev["quote"] == NFL.Q_VENUE_TIE
        b = await L.balances(conn, acct["account_id"], now=now + 201)
        assert b["ledger_consistent"] is True
        # the learner was held for the whole proof: no lesson cites this
        # scratch account's records
        assert await conn.fetchval(
            "SELECT count(*) FROM agent_memory_events WHERE "
            " evidence_refs::text LIKE '%' || $1 || '%'",
            d["decision_id"]) == 0
    finally:
        try:
            await forget_settled_entry(conn, acct and acct["account_id"])
            await purge(conn, vids)
            await PL.purge_everything(conn)
        finally:
            await release_the_memory_learner(conn, held)
            await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 6 · DATES: THE LONDON GAME AND THE SUNDAY-NIGHT GAME
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_the_london_game_maps_to_its_et_date(cg_on_with_parity):
    conn = await H.connect()
    now = time.time() + 5.0
    vids = []
    try:
        acct = await _setup(conn, "nfllondon", now)
        slug = "aec-nfl-ind-was-2026-10-04"
        await premap(conn, slug, title="IND Colts vs. WAS Commanders",
                     kickoff=LONDON_KICKOFF)
        v = await nfl_valuation(conn, slug=slug, selection="Indianapolis Colts",
                                odds=LONDON_ODDS, text=LONDON_TEXT,
                                decided_at=now - 10)
        vids.append(v["valuation_id"])
        t = PL.Transport(now)
        t.set(slug, offers=[(0.55, 2000)], bids=[(0.53, 2000)])
        await _pass(conn, acct, t, now, PL.client(t))
        d = await decision(conn, acct, v["valuation_id"])
        assert d["verdict"] == "ENTER", (d["refusal"], d["refusals"])
        fd = check(d, "nfl_fixture_date_matches_the_venue_slug")
        assert fd["passed"]
        f = fd["fixture_date"]
        assert f["event_date"] == "2026-10-04"
        assert f["prose_date"] == "2026-10-04"
        assert f["kickoff_et"] == "2026-10-04T09:30:00-04:00"
        # NOT A DATE DISCRIMINATOR: 13:30Z is 2026-10-04 in UTC, London and
        # New York alike (the Sunday-night test below is the one a UTC
        # mapping fails). What this proves is that the kickoff instant is
        # read on the ET clock and CHECKED: the same London contract against
        # a catalogue kickoff a day later is refused by name.
        assert f["kickoff_utc_date"] == f["kickoff_et_date"] == "2026-10-04"
        await premap(conn, slug, title="IND Colts vs. WAS Commanders",
                     kickoff=LONDON_KICKOFF + 86400.0)
        PB._CONTEXT_CACHE.clear()
        v2 = await nfl_valuation(conn, slug=slug,
                                 selection="Washington Commanders",
                                 odds=LONDON_ODDS, text=LONDON_TEXT,
                                 decided_at=now + 20)
        vids.append(v2["valuation_id"])
        await _pass(conn, acct, t, now + 25, PL.client(t))
        d2 = await decision(conn, acct, v2["valuation_id"])
        assert d2["verdict"] == "REFUSE"
        assert NFL.R_DATE_INCONSISTENT in d2["refusals"], d2["refusals"]
        f2 = check(d2, "nfl_fixture_date_matches_the_venue_slug")[
            "fixture_date"]
        assert f2["kickoff_et_date"] == "2026-10-05"
    finally:
        await purge(conn, vids)
        await PL.purge_everything(conn)
        await conn.close()


@pg
async def test_the_sunday_night_game_maps_to_the_slug_not_the_utc_day(
        cg_on_with_parity):
    conn = await H.connect()
    now = time.time() + 5.0
    vids = []
    try:
        acct = await _setup(conn, "nflsnf", now)
        odds = {"Detroit Lions": 1.50, "Carolina Panthers": 2.73}
        good = "aec-nfl-det-car-2026-10-04"            # the venue's own slug
        utc = "aec-nfl-det-car-2026-10-05"             # dated by the UTC day
        await premap(conn, good, title="DET Lions vs. CAR Panthers",
                     kickoff=SNF_KICKOFF)
        await premap(conn, utc, title="DET Lions vs. CAR Panthers",
                     kickoff=SNF_KICKOFF)
        t = PL.Transport(now)
        vs = []
        for slug in (good, utc):
            v = await nfl_valuation(conn, slug=slug, selection="Detroit Lions",
                                    odds=odds, text=SNF_TEXT,
                                    decided_at=now - 10)
            vids.append(v["valuation_id"])
            vs.append(v)
            t.set(slug, offers=[(0.55, 2000)], bids=[(0.53, 2000)])
        await _pass(conn, acct, t, now, PL.client(t))
        d = await decision(conn, acct, vs[0]["valuation_id"])
        assert d["verdict"] == "ENTER", (d["refusal"], d["refusals"])
        f = check(d, "nfl_fixture_date_matches_the_venue_slug")[
            "fixture_date"]
        assert f["event_date"] == "2026-10-04"
        assert f["kickoff_utc_date"] == "2026-10-05"      # Monday in UTC
        assert f["kickoff_et_date"] == "2026-10-04"       # Sunday night ET
        bad = await decision(conn, acct, vs[1]["valuation_id"])
        assert bad["verdict"] == "REFUSE"
        assert NFL.R_DATE_INCONSISTENT in bad["refusals"], bad["refusals"]
    finally:
        await purge(conn, vids)
        await PL.purge_everything(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 7 · THE PRO BOWL IS NEVER TRADED
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_the_pro_bowl_is_never_traded(cg_on_with_parity):
    conn = await H.connect()
    now = time.time() + 5.0
    vids = []
    try:
        acct = await _setup(conn, "nflprobowl", now)
        slug = "aec-nfl-afc-nfc-2027-02-02"
        # SYNTHETIC (no Pro Bowl listing exists yet): the venue's NFL wording
        # around an AFC vs NFC Pro Bowl game. Everything else would qualify.
        text = LONDON_TEXT.replace(
            "Indianapolis Colts vs Washington Commanders NFL game scheduled "
            "for Oct 4, 2026",
            "AFC vs NFC NFL Pro Bowl game scheduled for Feb 2, 2027")
        v = await nfl_valuation(conn, slug=slug, selection="AFC",
                                odds={"AFC": 1.80, "NFC": 2.05}, text=text,
                                decided_at=now - 10)
        vids.append(v["valuation_id"])
        t = PL.Transport(now)
        t.set(slug, offers=[(0.40, 2000)], bids=[(0.38, 2000)])
        await _pass(conn, acct, t, now, PL.client(t))
        d = await decision(conn, acct, v["valuation_id"])
        assert d["verdict"] == "REFUSE"
        assert NFL.R_EXHIBITION in d["refusals"], d["refusals"]
        # ...and refused WITHOUT the marker too: its day is after the cited
        # regular season, so its phase is not established
        assert NFL.R_PHASE_NOT_REGULAR in d["refusals"], d["refusals"]
        assert await conn.fetchval("SELECT count(*) FROM paper_orders WHERE "
                                   " decision_id=$1", d["decision_id"]) == 0
    finally:
        await purge(conn, vids)
        await PL.purge_everything(conn)
        await conn.close()


@pg
async def test_a_preseason_or_playoff_window_game_is_never_traded(
        cg_on_with_parity):
    """R30A review: the exclusion failed OPEN -- a preseason game whose text
    carries no 'preseason' word ENTERED, priced on the regular-season tie
    rate (preseason has no overtime). The venue's own NFL wording around an
    August date and a January playoff-window date, with venue catalogue rows
    and kickoffs that agree: every other check passes, and both are refused
    because their phase is not established. SYNTHETIC listings: no venue
    preseason or playoff listing has been captured."""
    conn = await H.connect()
    now = time.time() + 5.0
    vids = []
    try:
        acct = await _setup(conn, "nflphase", now)
        t = PL.Transport(now)
        cases = (("aec-nfl-ind-was-2026-08-15", "Aug 15, 2026",
                  1786813200.0),                 # 2026-08-15T17:00Z
                 ("aec-nfl-ind-was-2027-01-17", "Jan 17, 2027",
                  1800205200.0))                 # 2027-01-17T18:00Z
        got = []
        for slug, day, kickoff in cases:
            text = LONDON_TEXT.replace("Oct 4, 2026", day)
            assert NFL.exhibition_marker(slug, text) is None
            await premap(conn, slug, title="IND Colts vs. WAS Commanders",
                         kickoff=kickoff)
            v = await nfl_valuation(conn, slug=slug,
                                    selection="Indianapolis Colts",
                                    odds=LONDON_ODDS, text=text,
                                    decided_at=now - 10)
            vids.append(v["valuation_id"])
            t.set(slug, offers=[(0.55, 2000)], bids=[(0.53, 2000)])
            got.append(v)
        await _pass(conn, acct, t, now, PL.client(t))
        for v, (slug, _, _) in zip(got, cases):
            d = await decision(conn, acct, v["valuation_id"])
            assert d["verdict"] == "REFUSE", slug
            assert NFL.R_PHASE_NOT_REGULAR in d["refusals"], d["refusals"]
            assert NFL.R_EXHIBITION not in d["refusals"]
            # the date mapping itself was fine: the PHASE is what is missing
            assert check(d, "nfl_fixture_date_matches_the_venue_slug")[
                "passed"]
            ph = check(d, "nfl_regular_season_fixture_established")
            assert not ph["passed"]
            assert ph["season_phase"]["refusal"] == NFL.R_PHASE_NOT_REGULAR
            # never priced on the regular-season tie rate
            conv = H.j(d["pinnacle"]).get("venue_conversion") or {}
            assert conv.get("tie_rate_used") is None
            assert conv.get("refusal") == NFL.R_PHASE_NOT_REGULAR
            assert await conn.fetchval(
                "SELECT count(*) FROM paper_orders WHERE decision_id=$1",
                d["decision_id"]) == 0
    finally:
        await purge(conn, vids)
        await PL.purge_everything(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 8 · THE MAKER'S RESTING BID AND XAVIER'S MEASURE, ON THE CONVERTED SCALE
# ═════════════════════════════════════════════════════════════════════

@pytest.fixture
def maker_on(monkeypatch, new_strategies_off):
    """Only the maker-entry policy's row on (the other benchmark strategies
    off), as the maker proofs set it."""
    monkeypatch.setenv(PB.ENV_FLAG, "on")
    monkeypatch.setenv(PL.S.ENV_FLAG, "on")
    for k in (PB.CG_STRATEGY, PB.EXPLORE_STRATEGY, PB.CONTROL_KEY):
        PL.set_policy_control(k, False)
    PL.set_policy_control(PB.MAKER_STRATEGY, True)
    PB._CONTEXT_CACHE.clear()
    PD._CONTEXT_CACHE.clear()
    yield
    # back to the migrated launch selection: CG and exploration on, maker
    # and strict off
    PL.set_policy_control(PB.MAKER_STRATEGY, False)
    PL.set_policy_control(PB.CG_STRATEGY, True)
    PL.set_policy_control(PB.EXPLORE_STRATEGY, True)
    PB._CONTEXT_CACHE.clear()


async def _maker_entry(conn, tag, now, vids):
    """An NFL maker entry resting below the ask, on the converted scale
    (its valuation id goes on `vids` at once, so the caller purges it even
    when an assertion here fails)."""
    from sportsassets.agents import paper_maker as PMK
    acct = await _setup(conn, tag, now)
    slug = "aec-nfl-ind-was-2026-10-04"
    await premap(conn, slug, title="IND Colts vs. WAS Commanders",
                 kickoff=LONDON_KICKOFF)
    v = await nfl_valuation(conn, slug=slug, selection="Indianapolis Colts",
                            odds=LONDON_ODDS, text=LONDON_TEXT,
                            decided_at=now - 10)
    vids.append(v["valuation_id"])
    t = PL.Transport(now)
    t.set(slug, offers=[(0.66, 5000)], bids=[(0.60, 5000)])
    client = PL.client(t)
    p1 = await _pass(conn, acct, t, now, client, fee=None)
    assert not p1["errors"], p1["errors"]
    d = await decision(conn, acct, v["valuation_id"], PB.MAKER_STRATEGY)
    assert d is not None and d["verdict"] == "ENTER", (
        d and (d["refusal"], d["refusals"]))
    conv = NFL.venue_value(v["p_book"], phase=NFL.PHASE_REGULAR)
    assert float(d["p_pinnacle"]) == pytest.approx(conv["p"], abs=1e-12)
    o = await conn.fetchrow("SELECT * FROM paper_orders WHERE "
                            " decision_id=$1", d["decision_id"])
    assert o["order_type"] == "RESTING" and o["state"] == "RESTING"
    return acct, t, client, slug, v, d, o, PMK


@pg
async def test_the_maker_rechecks_an_nfl_bid_on_the_converted_scale(
        maker_on):
    """A newer reading whose RAW P(win | no tie) would keep the resting bid,
    but whose value as the venue contract (tie pays 0.50, worst evidenced
    tie rate) no longer clears it: the bid is cancelled, EDGE_GONE."""
    conn = await H.connect()
    now = time.time() + 5.0
    vids = []
    try:
        acct, t, client, slug, v, d, o, PMK = await _maker_entry(
            conn, "nflmaker1", now, vids)
        limit = float(o["limit_price"])
        params = await PB.cg_parameters(conn, {"now": now})
        min_edge = max(float(params["values"]["min_gross_edge_pp"]),
                       PB.CG_MIN_EDGE_PP_V2) / 100.0
        fee_pc = float(PB.fee_per_contract(None, limit, now + 30))

        def keeps(p):
            return not PMK.check_resting(
                limit=limit, p_new=p, reading_age_s=5.0, min_edge=min_edge,
                fee_pc=lambda px: fee_pc, enabled=True)["cancel"]
        # the newer book probability: the lowest raw value that still keeps
        # the bid, plus a hair -- its converted value cannot keep it
        lo, hi = limit, 1.0
        for _ in range(80):
            mid = 0.5 * (lo + hi)
            lo, hi = (lo, mid) if keeps(mid) else (mid, hi)
        p_raw = hi + 1e-6
        p_conv = NFL.venue_value(p_raw, phase=NFL.PHASE_REGULAR)["p"]
        assert keeps(p_raw) and not keeps(p_conv), (p_raw, p_conv, limit)
        v2 = await nfl_valuation(conn, slug=slug,
                                 selection="Indianapolis Colts",
                                 odds=LONDON_ODDS, text=LONDON_TEXT,
                                 decided_at=now + 25, p=p_raw)
        vids.append(v2["valuation_id"])
        p2 = await _pass(conn, acct, t, now + 30, client, fee=None)
        assert not p2["errors"], p2["errors"]
        mm = p2["steps"]["maker_maintain"]
        assert mm["cancel_requested"] >= 1, mm
        assert mm["by_condition"].get(PMK.C_EDGE_GONE, 0) >= 1, mm
        ev = await conn.fetch("SELECT kind, detail FROM paper_order_events "
                              " WHERE order_id=$1 ORDER BY event_id",
                              o["order_id"])
        cr = [H.j(e["detail"]) for e in ev if e["kind"] == "CANCEL_REQUESTED"]
        assert cr and cr[0]["reason"] == PMK.C_EDGE_GONE
        assert client.mutation_attempts == 0
    finally:
        await purge(conn, vids)
        await PL.purge_everything(conn)
        await conn.close()


@pg
async def test_the_maker_cancels_an_nfl_bid_whose_conversion_is_refused(
        maker_on, monkeypatch):
    """The same entry, then the evidence that established the game's phase
    is withdrawn (the cited season windows emptied): the re-check cannot
    convert the newer reading, holds no probability, and cancels the bid as
    UNVERIFIED -- it never falls back to the book's unconverted number."""
    conn = await H.connect()
    now = time.time() + 5.0
    vids = []
    try:
        acct, t, client, slug, v, d, o, PMK = await _maker_entry(
            conn, "nflmaker2", now, vids)
        v2 = await nfl_valuation(conn, slug=slug,
                                 selection="Indianapolis Colts",
                                 odds=LONDON_ODDS, text=LONDON_TEXT,
                                 decided_at=now + 25, p=0.80)  # raw edge huge
        vids.append(v2["valuation_id"])
        monkeypatch.setattr(NFL, "SEASON_WINDOWS", ())
        assert PB.held_nfl_conversion({
            "sport_family": "football", "us_market_slug": slug,
            "raw_odds": LONDON_ODDS, "venue_rules_text": LONDON_TEXT})[
            "venue_conversion"]["phase"] is None
        p2 = await _pass(conn, acct, t, now + 30, client, fee=None)
        assert not p2["errors"], p2["errors"]
        mm = p2["steps"]["maker_maintain"]
        assert mm["by_condition"].get(PMK.C_UNVERIFIED, 0) >= 1, mm
        ev = await conn.fetch("SELECT kind, detail FROM paper_order_events "
                              " WHERE order_id=$1 ORDER BY event_id",
                              o["order_id"])
        cr = [H.j(e["detail"]) for e in ev if e["kind"] == "CANCEL_REQUESTED"]
        assert cr and cr[0]["reason"] == PMK.C_UNVERIFIED
        assert client.mutation_attempts == 0
    finally:
        await purge(conn, vids)
        await PL.purge_everything(conn)
        await conn.close()


@pg
async def test_xavier_converts_a_fresh_feed_reading_of_a_held_nfl_contract(
        cg_on_with_parity):
    """Xavier's measure of a held NFL position from the in-process PinnAPI
    feed (the held read now answers for NFL rows): the feed's P(win | no
    tie) is converted to the contract's value exactly as the entry was."""
    conn = await H.connect()
    now = time.time() + 5.0
    vids = []
    try:
        acct = await _setup(conn, "nflxavier", now)
        slug = "aec-nfl-ind-was-2026-10-04"
        v = await nfl_valuation(conn, slug=slug, selection="Indianapolis Colts",
                                odds=LONDON_ODDS, text=LONDON_TEXT,
                                decided_at=now - 10)
        vids.append(v["valuation_id"])
        t = PL.Transport(now)
        t.set(slug, offers=[(0.55, 2000)], bids=[(0.53, 2000)])
        await _pass(conn, acct, t, now, PL.client(t))
        d = await decision(conn, acct, v["valuation_id"])
        assert d["verdict"] == "ENTER", (d["refusal"], d["refusals"])
        o = await conn.fetchrow("SELECT * FROM paper_orders WHERE "
                                " decision_id=$1 AND role='ENTRY'",
                                d["decision_id"])
        pos = {"group_id": o["group_id"], "holding_side": o["holding_side"],
               "us_market_slug": slug}
        at = now + 120.0          # the collector's reading is stale by now
        p_feed = 0.70

        async def feed(conn_, *, pos, at, max_age_s, payout_event,
                       payout_is_complement):
            return {"ok": True, "p": p_feed, "p_selection": p_feed,
                    "sport_id": 5, "feed_event_id": 77,
                    "designation": "away",
                    "provenance": {"source_change_ms": (at - 2.0) * 1000.0,
                                   "received_ms": (at - 1.9) * 1000.0,
                                   "quote_age_s": 2.0, "stream": "prematch"},
                    "devig": {"version": devig.VERSION},
                    "conditional_on": {"condition": "NO_TIE"}}
        ctx = {"now": at, "config": acct["config"]}
        m = await PB.xavier_measure(conn, ctx, pos=pos, strategy=CG,
                                    feed=feed)
        assert m["source"] == PB.SOURCE_FEED_CURRENT, m
        want = NFL.venue_value(p_feed, phase=NFL.PHASE_REGULAR)["p"]
        assert m["p"] == pytest.approx(want, abs=1e-12)
        assert m["p_book_conditional_no_tie"] == pytest.approx(p_feed)
        assert m["venue_conversion"]["tie_rate_end_used"] == "HIGHEST"
    finally:
        await purge(conn, vids)
        await PL.purge_everything(conn)
        await conn.close()
