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
    slug dated by the UTC day is refused by name;
  7 the Pro Bowl is never traded.

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
                        pin_age_s=5.0) -> dict:
    """One entry-experiment valuation of an NFL money line, shaped like the
    collector's row (CALIBRATION_ONLY, the book's priced set, the venue's
    rules text, the strict settlement refusals by name)."""
    at = float(decided_at)
    p = _book_p(odds, selection)
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
    """The venue catalogue row for the contract (us_premap), with the
    venue's own scheduled kickoff instant."""
    await conn.execute(
        "INSERT INTO us_premap (identifier, event_slug, event_title, "
        " market_slug, question, kind, side_norm, event_keys, sports_type, "
        " game_start, team_league, updated_at) VALUES ($1,$2,$3,$1,$4,"
        " 'side','long',ARRAY[$5],'football_team_full_game_winner',"
        " to_timestamp($6),'nfl',now()) ON CONFLICT (identifier, side_norm) "
        " DO UPDATE SET game_start=EXCLUDED.game_start, "
        " event_keys=EXCLUDED.event_keys, updated_at=now()",
        slug, "nfl-" + slug.split("aec-nfl-", 1)[1], title,
        "Who will win %s?" % title, MARK, float(kickoff))


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
        conv = NFL.venue_value(v["p_book"])
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
        conv = NFL.venue_value(v["p_book"])
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
        cases = []
        for slug, text in (("aec-nfl-ind-was-2026-10-04", no_ot),
                           ("aec-nfl-lar-phi-2026-10-04", tie_no)):
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
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_orders WHERE decision_id = ANY($1)",
            [d1["decision_id"], d2["decision_id"]]) == 0
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
        # an underdog's worst case is the LOWEST rate (0 while the phase is
        # not established as regular season): no tie credit is taken
        dog = NFL.venue_value(1.0 - p_book)
        assert dog["tie_rate_end_used"] == "LOWEST"
        assert dog["p"] == pytest.approx(1.0 - p_book)

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
        assert ev["settlement_state"] == "TIE_AFTER_OVERTIME"
        assert ev["quote"] == NFL.Q_VENUE_TIE
        b = await L.balances(conn, acct["account_id"], now=now + 201)
        assert b["ledger_consistent"] is True
    finally:
        await forget_settled_entry(conn, acct and acct["account_id"])
        await purge(conn, vids)
        await PL.purge_everything(conn)
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
        assert f["kickoff_utc_date"] == f["kickoff_et_date"] == "2026-10-04"
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
        assert await conn.fetchval("SELECT count(*) FROM paper_orders WHERE "
                                   " decision_id=$1", d["decision_id"]) == 0
    finally:
        await purge(conn, vids)
        await PL.purge_everything(conn)
        await conn.close()
