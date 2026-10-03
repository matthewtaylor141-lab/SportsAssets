"""PINNAPI (WEBSOCKET) OUTCOME DEPTH, CORROBORATED -- NEVER COPIED, NEVER LOWERED.

A PinnAPI raw-websocket read is Pinnacle alone, so its valuation carries
outcome_books = 1 and `bettor_external_shadow` refuses it
OUTCOME_DEPTH_BELOW_FLOOR (MIN_OUTCOME_BOOKS = 2, unchanged). An independent
odds-API observation of the EXACT same event / outcome / period / line /
market / settlement, stamped within PINNACLE_MAX_AGE_S of the decision, may
satisfy that floor (sportsassets/valuation_corroboration.py), and is persisted
SEPARATELY (migration 195) with the PinnAPI read's own count still 1 beside it.

Pure checks drive the collector's own binding (`ext_pinnacle_loop.
_corroboration_assess`: `pinnacle_h2h` over SHARP_BOOKS, the floor, the age
bound); persistence and the schema's constraints run on real Postgres
(RN1X_TEST_DSN). ALL PRICES ARE SYNTHETIC.
"""
from __future__ import annotations

import copy
import datetime as _dt
import json
import pathlib
import uuid

import pytest

from sportsassets import bettor_external_shadow as ext
from sportsassets import bettor_venue_settlement as vset
from sportsassets import pinnapi_feed as F
from sportsassets import pinnapi_primary as P
from sportsassets import valuation_corroboration as corr
from sportsassets.agents import paper_benchmark as PB
from sportsassets.workers import ext_pinnacle_loop as loop

from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")

BACKEND = pathlib.Path(__file__).resolve().parents[1]
MIGRATION_195 = BACKEND / "migrations" / "195_valuation_corroboration.sql"
ROLLBACK_195 = (BACKEND / "migrations" / "rollback"
                / "195_valuation_corroboration.down.sql")

AT = 1_791_100_000.0
START = AT + 3 * 3600
HOME, AWAY = "Miami Marlins", "Colorado Rockies"
EID = "odds-corr-evt-1"
FEED_EID = 424242
RULE = vset.BOOK_SETTLEMENT["baseball"]


def iso(t):
    return _dt.datetime.fromtimestamp(t, _dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def quote():
    """The PinnAPI quote as `pinnapi_primary.select` builds it (synthetic)."""
    return {"prices": {HOME: 1.62, AWAY: 2.45}, "home": HOME, "away": AWAY,
            "event_id": EID, "observed_at": AT - 1.0,
            "received_at": AT - 0.99, "commence_time": iso(START),
            "depth": {HOME: 1, AWAY: 1},
            "reference_input": {
                "provider": P.PROVIDER, "version": P.VERSION,
                "feed_event_id": FEED_EID, "source_change_ms": (AT - 1) * 1000,
                "received_ms": (AT - 1) * 1000 + 10, "epoch": 3,
                "runtime_id": "rt-corr", "market_key": F.FULL_GAME_MONEYLINE_KEY,
                "raw_odds": {"home": -160, "away": 145},
                "discovery_event_id": EID, "discovery_home": HOME,
                "discovery_away": AWAY, "discovery_start": iso(START),
                "family": "baseball"}}


def contract(**over):
    out = {"event_key": EID, "selection": HOME, "period": "FULL_GAME",
           "line": None, "market": "h2h", "us_market_slug": "aec-mlb-mia-col",
           "settlement_rule": RULE}
    out.update(over)
    return out


def book(key, age_s, *, mkey="h2h", names=(HOME, AWAY), point=None):
    outs = [{"name": n, "price": 1.6 + i} for i, n in enumerate(names)]
    if point is not None:
        for o in outs:
            o["point"] = point
    return {"key": key, "last_update": iso(AT - age_s),
            "markets": [{"key": mkey, "last_update": iso(AT - age_s),
                         "outcomes": outs}]}


def book_now(key, age_s, **kw):
    """A book stamped `age_s` before the REAL clock (paths that read it)."""
    import time as _t
    return book(key, AT - _t.time() + age_s, **kw)


def event(*books, **over):
    ev = {"id": EID, "sport_key": "baseball_mlb", "home_team": HOME,
          "away_team": AWAY, "commence_time": iso(START),
          "bookmakers": list(books) or [book("pinnacle", 5),
                                        book("betfair_ex_eu", 6)]}
    ev.update(over)
    return ev


def obs(ev, *, provider=P.LEGACY_PROVIDER, source=corr.SRC_BOUNDED_READ):
    return {"provider": provider, "event": ev, "received_at": AT - 0.5,
            "sport_key": "baseball_mlb", "source": source}


def assess(ev=None, *, at=AT, observation=None, c=None, q=None):
    q = q or quote()
    return loop._corroboration_assess(
        q, observation if observation is not None else obs(ev or event()),
        c or contract(), at=at)


# ═════════════════════════════════════════════════════════════════════
# THE FLOOR AND THE BOUND ARE THE EXISTING CONSTANTS
# ═════════════════════════════════════════════════════════════════════

def test_the_floor_is_unchanged():
    assert ext.MIN_OUTCOME_BOOKS == 2
    assert ext.R_THIN_OUTCOME == "OUTCOME_DEPTH_BELOW_FLOOR"


def test_the_age_bound_is_pinnacle_max_age_s_by_name():
    v = assess()
    assert loop.PINNACLE_MAX_AGE_S == 30.0
    assert v["max_age_s"] == loop.PINNACLE_MAX_AGE_S
    assert v["max_age_basis"] == "PINNACLE_MAX_AGE_S"
    assert v["min_outcome_books"] == ext.MIN_OUTCOME_BOOKS


def test_the_count_is_the_collectors_own_odds_api_counter(monkeypatch):
    calls = []
    real = loop.pinnacle_h2h

    def spy(ev, *, received_at):
        calls.append(sorted(b["key"] for b in ev["bookmakers"]))
        return real(ev, received_at=received_at)
    monkeypatch.setattr(loop, "pinnacle_h2h", spy)
    v = assess()
    assert v["qualified"] is True, v["why"]
    assert calls and ["betfair_ex_eu", "pinnacle"] in calls
    assert v["corroborating"]["count_function"] == "ext_pinnacle_loop.pinnacle_h2h"
    # and it is exactly what that function says of the same read
    assert v["corroborating_outcome_books"] == \
        real(event(), received_at=AT)["depth"][HOME] == 2


def test_the_refusal_codes_are_probability_stage_codes():
    stage1 = dict(ext.STAGES)["1_PROBABILITY"]
    for code in corr.REFUSALS:
        assert code in stage1, code
        assert ext.STAGE_OF[code] == "1_PROBABILITY"


# ═════════════════════════════════════════════════════════════════════
# THE VERDICTS
# ═════════════════════════════════════════════════════════════════════

def test_exact_current_corroboration_qualifies_with_the_evidence_recorded():
    v = assess()
    assert v["qualified"] is True and v["refusal"] is None, v["why"]
    assert v["corroborating_outcome_books"] == 2
    assert v["pinnapi"]["outcome_books"] == 1
    cor = v["corroborating"]
    assert cor["provider"] == P.LEGACY_PROVIDER == "the-odds-api.com/v4"
    assert cor["books"] == ["betfair_ex_eu", "pinnacle"]
    # THE PROVIDER'S OWN OBSERVATION INSTANT (the oldest counted stamp), not
    # our receipt instant
    assert cor["observed_at"] == AT - 6 and cor["received_at"] == AT - 0.5
    assert cor["age_s"] == 6.0
    pin = v["pinnapi"]
    assert pin["source_change_ms"] == (AT - 1) * 1000
    assert pin["received_ms"] == (AT - 1) * 1000 + 10
    assert pin["epoch"] == 3 and pin["provider"] == P.PROVIDER
    idt = v["identity"]
    assert idt["matched"] is True and idt["mismatches"] == []
    assert idt["discovery_event_id"] == idt["corroborating_event_id"] == EID
    assert idt["pinnapi_feed_event_id"] == FEED_EID
    assert (idt["outcome"], idt["period"], idt["market"], idt["line"]) == \
        (HOME, "FULL_GAME", "h2h", None)
    assert idt["us_market_slug"] == "aec-mlb-mia-col"
    assert idt["settlement_rule"] == RULE and idt["how_matched"]


def test_the_bound_is_inclusive_and_one_second_past_it_is_not_current():
    ok = assess(event(book("pinnacle", 30), book("betfair_ex_eu", 30)))
    assert ok["qualified"] is True and ok["corroborating"]["age_s"] == 30.0
    stale = assess(event(book("pinnacle", 5), book("betfair_ex_eu", 31)))
    assert stale["qualified"] is False
    assert stale["refusal"] == corr.R_NOT_CURRENT == "CORROBORATION_NOT_CURRENT"
    # the same books, any age, WOULD have counted 2: it is the age that failed
    assert stale["corroborating"]["outcome_books_any_age"] == 2


def test_future_stamped_corroboration_is_refused():
    v = assess(event(book("pinnacle", 5), book("betfair_ex_eu", -2)))
    assert v["qualified"] is False
    assert v["refusal"] == corr.R_FUTURE == "CORROBORATION_FUTURE_STAMPED"


@pytest.mark.parametrize("element,mutate", [
    ("outcome", lambda ev, c, q: ev["bookmakers"][1]["markets"][0].update(
        outcomes=[{"name": HOME, "price": 1.6},
                  {"name": "Colorado Rockies (G2)", "price": 2.4}])),
    ("period", lambda ev, c, q: ev["bookmakers"][1]["markets"][0].update(
        key="h2h_1st_5_innings")),
    ("line", lambda ev, c, q: [o.update(point=-1.5) for o in
                               ev["bookmakers"][1]["markets"][0]["outcomes"]]),
    ("market", lambda ev, c, q: ev["bookmakers"][1]["markets"][0].update(
        key="spreads")),
    ("event", lambda ev, c, q: ev.update(id="odds-corr-OTHER-evt")),
    ("event", lambda ev, c, q: ev.update(commence_time=iso(START + 86400))),
    ("event", lambda ev, c, q: ev.update(home_team=AWAY, away_team=HOME)),
    ("settlement", lambda ev, c, q: c.update(settlement_rule="SOMETHING_ELSE")),
    ("settlement", lambda ev, c, q: ev["bookmakers"][1]["markets"][0].update(
        outcomes=[{"name": HOME, "price": 2.6}, {"name": AWAY, "price": 2.9},
                  {"name": "Draw", "price": 3.1}])),
    ("period", lambda ev, c, q: c.update(period="FIRST_FIVE_INNINGS")),
    ("period", lambda ev, c, q: q["reference_input"].update(market_key="s;1;m")),
    ("event", lambda ev, c, q: ev.update(sport_key="soccer_epl")),
])
def test_any_identity_mismatch_is_refused_by_name(element, mutate):
    ev, c, q = event(), contract(), quote()
    mutate(ev, c, q)
    v = assess(ev, c=c, q=q)
    assert v["qualified"] is False
    assert v["refusal"] == corr.R_IDENTITY == "CORROBORATION_IDENTITY_MISMATCH"
    assert element in {m["element"] for m in v["identity"]["mismatches"]}, v
    assert v["identity"]["matched"] is False


def test_single_book_corroboration_is_still_thin():
    # pinnacle plus a book the collector does not count (not in SHARP_BOOKS)
    v = assess(event(book("pinnacle", 5), book("draftkings", 5)))
    assert "draftkings" not in loop.SHARP_BOOKS
    assert v["qualified"] is False
    assert v["refusal"] == corr.R_BELOW_FLOOR
    assert v["corroborating"]["outcome_books_any_age"] == 1


def test_pinnacle_only_or_duplicated_pinnacle_is_the_same_source():
    for books in ([book("pinnacle", 5)],
                  [book("pinnacle", 5), book("pinnacle", 4), book("pinnacle", 3)]):
        v = assess(event(*books))
        assert v["qualified"] is False
        assert v["refusal"] == corr.R_SAME_SOURCE, v["why"]


def test_the_pinnapi_provider_cannot_corroborate_itself():
    v = assess(observation=obs(event(), provider=P.PROVIDER))
    assert v["qualified"] is False and v["refusal"] == corr.R_SAME_SOURCE


def test_no_observation_is_unavailable_never_manufactured():
    v = assess(observation={})
    assert v["qualified"] is False and v["refusal"] == corr.R_UNAVAILABLE
    assert v["corroborating_outcome_books"] is None


def test_a_duplicated_independent_book_counts_once():
    v = assess(event(book("pinnacle", 5), book("betfair_ex_eu", 5),
                     book("betfair_ex_eu", 4)))
    assert v["qualified"] is True and v["corroborating_outcome_books"] == 2


# ═════════════════════════════════════════════════════════════════════
# THE VALUATION: the floor met by the corroborated count, outcome_books 1
# ═════════════════════════════════════════════════════════════════════

def _evaluate(verdict, *, outcome_books=1):
    q = quote()
    return ext.evaluate(
        contract={"venue": "PMUS", "us_market_slug": "corr-%s" % uuid.uuid4().hex[:10],
                  "sport_family": "baseball", "market": "h2h",
                  "selection": HOME, "event_key": EID, "period": "FULL_GAME",
                  "line": None, "settlement_rule": RULE},
        quote={"book": "pinnacle", "outcomes": q["prices"],
               "observed_at": AT - 1, "received_at": AT - 0.99,
               "period": "FULL_GAME", "line": None, "event_key": EID,
               "settlement_rule": RULE},
        now=AT, market_state={"ask": .3, "readable": True, "depth": 100},
        execution_estimate={"p_fill": 1}, size=1,
        risk={"permitted": False}, fee_fn=lambda *a, **k: 0,
        outcome_books=outcome_books, armed=True, corroboration=verdict)


def _lane_probability_check(rec):
    got = PB.contract_match({"refusals": rec["refusals"]}, {})
    return next(c for c in got["checks"]
                if c["check"] == "probability_qualified_by_the_lane")


def test_valid_corroboration_lets_the_lane_qualify_the_probability():
    rec = _evaluate(assess())
    assert rec["probability"] is not None
    assert ext.R_THIN_OUTCOME not in rec["refusals"]
    assert not [c for c in rec["refusals"] if c.startswith("CORROBORATION_")]
    # THE RECORD CANNOT BE CONFUSED: this read's count stays 1, the
    # corroborating count is its own field, and the basis says which met it
    assert rec["outcome_books"] == 1
    d = rec["outcome_depth"]
    assert d["outcome_books"] == 1 and d["outcome_books_is"] == "THIS_READ_ONLY"
    assert d["corroborating_outcome_books"] == 2
    assert d["floor_satisfied_by"] == "INDEPENDENT_CORROBORATION"
    assert rec["corroboration"]["pinnapi"]["probability_of_selection"] == \
        rec["probability_of_selection"]
    chk = _lane_probability_check(rec)
    assert chk["passed"] is True, chk
    assert PB.R_PROBABILITY_UNQUALIFIED not in \
        PB.contract_match({"refusals": rec["refusals"]}, {})["refusals"]


@pytest.mark.parametrize("ev,code", [
    (event(book("pinnacle", 5), book("betfair_ex_eu", 45)), corr.R_NOT_CURRENT),
    (event(book("pinnacle", 5), book("betfair_ex_eu", -1)), corr.R_FUTURE),
    (event(book("pinnacle", 5)), corr.R_SAME_SOURCE),
    (event(book("pinnacle", 5), book("betfair_ex_eu", 5, mkey="h2h_h1")),
     corr.R_IDENTITY),
])
def test_refused_corroboration_keeps_the_thin_refusal_and_names_why(ev, code):
    rec = _evaluate(assess(ev))
    assert rec["outcome_books"] == 1
    assert ext.R_THIN_OUTCOME in rec["refusals"] and code in rec["refusals"]
    assert rec["refusals"].index(ext.R_THIN_OUTCOME) < rec["refusals"].index(code)
    assert rec["outcome_depth"]["floor_satisfied_by"] is None
    assert rec["admissible"] is False
    chk = _lane_probability_check(rec)
    assert chk["passed"] is False
    assert chk["refusal"] == PB.R_PROBABILITY_UNQUALIFIED
    assert set(chk["lane_refusals"]) >= {ext.R_THIN_OUTCOME, code}


def test_a_verdict_cannot_be_hand_edited_into_qualifying():
    for tamper in (lambda v: v.update(corroborating_outcome_books=1),
                   lambda v: v.update(corroborating_outcome_books=True),
                   lambda v: v.update(refusal=corr.R_NOT_CURRENT),
                   lambda v: v["identity"].update(matched=False),
                   lambda v: v["identity"].update(outcome=AWAY)):
        v = assess()
        tamper(v)
        rec = _evaluate(v)
        assert ext.R_THIN_OUTCOME in rec["refusals"], v


def test_without_corroboration_the_odds_api_record_is_unchanged():
    rec = _evaluate(None, outcome_books=2)
    assert ext.R_THIN_OUTCOME not in rec["refusals"]
    assert "corroboration" not in rec
    assert rec["outcome_depth"]["floor_satisfied_by"] == "THIS_READ"
    rec = _evaluate(None, outcome_books=1)
    assert ext.R_THIN_OUTCOME in rec["refusals"]


# ═════════════════════════════════════════════════════════════════════
# THE BOUNDED READ BUDGET
# ═════════════════════════════════════════════════════════════════════

def test_budget_one_read_per_event_per_minute_and_global_caps():
    t = [1000.0]
    b = corr.ReadBudget(clock=lambda: t[0])
    assert (b.per_event_s, b.per_minute, b.per_hour) == (60.0, 3, 30)
    assert b.acquire("e1")["ok"] is True
    again = b.acquire("e1")
    assert again["ok"] is False and again["refusal"] == corr.R_BUDGET
    assert again["limit"] == "PER_EVENT_MIN_INTERVAL_S"
    assert b.acquire("e2")["ok"] and b.acquire("e3")["ok"]
    minute = b.acquire("e4")
    assert minute["ok"] is False and minute["limit"] == "GLOBAL_PER_MINUTE"
    t[0] += 61
    assert b.acquire("e1")["ok"] is True          # the event's 60 s elapsed
    for i in range(40):
        t[0] += 61
        b.acquire("h%d" % i)
    hour = b.acquire("late")
    assert hour["ok"] is False and hour["limit"] == "GLOBAL_PER_HOUR"
    assert hour["reads_last_hour"] == 30


async def test_budget_exhausted_refuses_by_name_and_makes_no_read(monkeypatch):
    calls = []

    async def no_read(*a, **k):
        calls.append(a)
        raise AssertionError("a read was made with no budget")
    monkeypatch.setattr(loop, "fetch_event_odds", no_read)
    monkeypatch.setattr(corr, "BUDGET", corr.ReadBudget(per_minute=0))
    monkeypatch.delenv(loop.CORROBORATION_READS_ENV, raising=False)
    stats = loop._corroboration_stats()
    credits = {"used": None, "remaining": None}
    q = quote()
    stale = event(book_now("pinnacle", 400), book_now("betfair_ex_eu", 400))
    held, read, pre = await loop._corroboration_observation(
        q, stale, contract(), sport_key="baseball_mlb", received_at=AT - 400,
        stream=True, api_key="k" * 32, credits=credits, stats=stats)
    assert calls == [] and read["attempted"] is False
    assert read["refusal"] == corr.R_BUDGET
    assert read["budget"]["limit"] == "GLOBAL_PER_MINUTE"
    assert held["source"] == corr.SRC_STORED_DISCOVERY
    import time as _t
    fin = loop._corroboration_final(q, held, contract(), read, pre,
                                    at=_t.time(), stats=stats)
    assert fin["qualified"] is False
    assert fin["refusal"] == corr.R_BUDGET == "CORROBORATION_READ_BUDGET"
    assert fin["observation_refusal"] == corr.R_NOT_CURRENT
    assert stats["reads"]["budget_refused"] == 1
    assert stats["refusals"] == {corr.R_BUDGET: 1}
    rec = _evaluate(fin)
    assert {ext.R_THIN_OUTCOME, corr.R_BUDGET} <= set(rec["refusals"])


async def test_the_operator_switch_off_is_a_zero_budget(monkeypatch):
    async def no_read(*a, **k):
        raise AssertionError("a read was made while switched off")
    monkeypatch.setattr(loop, "fetch_event_odds", no_read)
    monkeypatch.setenv(loop.CORROBORATION_READS_ENV, "off")
    monkeypatch.setattr(corr, "BUDGET", corr.ReadBudget())
    stats = loop._corroboration_stats()
    _, read, _ = await loop._corroboration_observation(
        quote(), event(book_now("pinnacle", 400), book_now("betfair_ex_eu", 400)),
        contract(), sport_key="baseball_mlb", received_at=AT - 400,
        stream=True, api_key="k" * 32,
        credits={"used": None, "remaining": None}, stats=stats)
    assert read["refusal"] == corr.R_BUDGET
    assert read["budget"]["limit"] == "PINNAPI_CORROBORATION_READS_OFF"


async def test_the_periodic_path_never_reads_and_a_qualified_hold_never_reads(
        monkeypatch):
    async def no_read(*a, **k):
        raise AssertionError("no read expected")
    monkeypatch.setattr(loop, "fetch_event_odds", no_read)
    monkeypatch.setattr(corr, "BUDGET", corr.ReadBudget())
    import time as _t
    now = _t.time()
    fresh = event(book_now("pinnacle", 2), book_now("betfair_ex_eu", 3))
    stale = event(book_now("pinnacle", 400), book_now("betfair_ex_eu", 400))
    for ev, stream, why in ((stale, False, "PERIODIC_PATH_USES_ITS_OWN_READ"),
                            (fresh, True, "THE_HELD_OBSERVATION_QUALIFIED")):
        held, read, pre = await loop._corroboration_observation(
            quote(), ev, contract(), sport_key="baseball_mlb",
            received_at=now - 3, stream=stream, api_key="k" * 32,
            credits={"used": None, "remaining": None},
            stats=loop._corroboration_stats())
        assert read["attempted"] is False and read["why_not"] == why
    assert held["source"] == corr.SRC_STORED_DISCOVERY


async def test_a_bounded_read_is_measured_and_its_credits_recorded(monkeypatch):
    import time as _t
    asked = []

    async def one_event(sport_key, event_id, *, api_key, timeout=None):
        asked.append((sport_key, event_id))
        now = _t.time()
        return {"ok": True, "status": 200, "received_at": now,
                "event": event(book_now("pinnacle", 1),
                               book_now("smarkets", 2)),
                "credits_used": "120", "credits_remaining": "880",
                "credits_last": "3", "latency_ms": 41.5}
    monkeypatch.setattr(loop, "fetch_event_odds", one_event)
    monkeypatch.setattr(corr, "BUDGET", corr.ReadBudget())
    monkeypatch.delenv(loop.CORROBORATION_READS_ENV, raising=False)
    stats = loop._corroboration_stats()
    credits = {"used": None, "remaining": None}
    obs_, read, pre = await loop._corroboration_observation(
        quote(), event(book_now("pinnacle", 400), book_now("betfair_ex_eu", 400)),
        contract(), sport_key="baseball_mlb", received_at=AT - 400,
        stream=True, api_key="k" * 32, credits=credits, stats=stats)
    assert asked == [("baseball_mlb", EID)]
    assert pre["refusal"] == corr.R_NOT_CURRENT
    assert read["attempted"] is True and read["ok"] is True
    assert read["latency_ms"] == 41.5 and read["credits_last"] == "3"
    assert credits == {"used": "120", "remaining": "880"}
    assert obs_["source"] == corr.SRC_BOUNDED_READ
    fin = loop._corroboration_final(quote(), obs_, contract(), read, pre,
                                    at=_t.time(), stats=stats)
    assert fin["qualified"] is True, fin["why"]
    assert fin["corroborating"]["books"] == ["pinnacle", "smarkets"]
    assert stats["reads"] == {"attempted": 1, "ok": 1, "failed": 0,
                              "budget_refused": 0, "latency_ms": [41.5],
                              "credits_last": ["3"]}


async def test_the_single_event_read_never_raises(monkeypatch):
    import httpx

    class Boom:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            raise httpx.ConnectError("no route")

        async def __aexit__(self, *a):
            return False
    monkeypatch.setattr(httpx, "AsyncClient", Boom)
    got = await loop.fetch_event_odds("baseball_mlb", EID, api_key="k" * 32)
    assert got["ok"] is False and got["error"] == "ConnectError"
    assert got["refusal"] == loop.R_PROVIDER_ERROR
    assert "k" * 32 not in json.dumps(got)


# ═════════════════════════════════════════════════════════════════════
# PERSISTENCE (real Postgres)
# ═════════════════════════════════════════════════════════════════════

def test_migration_195_follows_the_conventions():
    names = sorted(p.name for p in (BACKEND / "migrations").glob("*.sql"))
    assert "195_valuation_corroboration.sql" in names
    assert sum(n.startswith("195_") for n in names) == 1
    i = names.index("195_valuation_corroboration.sql")
    assert names[i - 1].startswith("194_")
    assert ROLLBACK_195.exists()
    assert "DROP TABLE IF EXISTS valuation_corroboration" in ROLLBACK_195.read_text()
    sql = MIGRATION_195.read_text()
    assert sql.startswith("-- 195 · VALUATION CORROBORATION")
    assert "CHECK (pinnapi_outcome_books = 1)" in sql
    # the schema's floor is the code's floor
    assert "corroborating_outcome_books >= %d" % ext.MIN_OUTCOME_BOOKS in sql


async def _schema(conn):
    await conn.execute(MIGRATION_195.read_text())


@pg
async def test_the_persisted_corroboration_row_is_exactly_the_evidence():
    import time as _t
    conn = await H.connect()
    try:
        await _schema(conn)
        now = _t.time()
        # stamps relative to the real clock so the CHECK's age bound holds
        ev = event(book_now("pinnacle", 4), book_now("betfair_ex_eu", 7))
        verdict = assess(ev, at=now)
        assert verdict["qualified"] is True, verdict["why"]
        rec = _evaluate(verdict)
        slug = rec["contract"]["us_market_slug"]
        vid = await ext.persist(conn, rec)
        assert vid is not None
        v = await conn.fetchrow(
            "SELECT outcome_books, refusals, provider FROM external_valuations "
            " WHERE id=$1 AND us_market_slug=$2", vid, slug)
        assert v["outcome_books"] == 1                 # the PinnAPI read's own
        assert ext.R_THIN_OUTCOME not in v["refusals"]
        r = await conn.fetchrow(
            "SELECT * FROM valuation_corroboration WHERE valuation_id=$1", vid)
        assert r is not None
        assert r["qualified"] is True and r["refusal"] is None
        assert r["version"] == corr.VERSION
        assert r["pinnapi_provider"] == P.PROVIDER
        assert r["pinnapi_outcome_books"] == 1
        assert r["pinnapi_probability"] == pytest.approx(
            rec["probability_of_selection"])
        assert r["pinnapi_source_change_ms"] == (AT - 1) * 1000
        assert r["pinnapi_received_ms"] == (AT - 1) * 1000 + 10
        assert r["pinnapi_epoch"] == 3 and r["pinnapi_runtime_id"] == "rt-corr"
        assert r["corroborating_provider"] == "the-odds-api.com/v4"
        assert r["corroborating_source"] == corr.SRC_BOUNDED_READ
        # the PROVIDER's observation instant (the oldest counted stamp,
        # whole seconds as the provider states them), not our insert time
        assert r["corroborating_observed_at"].timestamp() == pytest.approx(
            verdict["corroborating"]["observed_at"])
        assert abs(r["corroborating_observed_at"].timestamp() - (now - 7)) < 1.01
        assert r["corroborating_received_at"].timestamp() == pytest.approx(AT - 0.5)
        assert r["corroborating_outcome_books"] == 2
        assert r["corroborating_books"] == ["betfair_ex_eu", "pinnacle"]
        assert r["corroboration_age_s"] == verdict["corroborating"]["age_s"]
        assert 0 <= r["corroboration_age_s"] <= 30
        assert r["max_age_s"] == loop.PINNACLE_MAX_AGE_S
        assert r["max_age_basis"] == "PINNACLE_MAX_AGE_S"
        assert r["decision_at"].timestamp() == pytest.approx(now)
        assert (r["discovery_event_id"], r["feed_event_id"],
                r["corroborating_event_id"]) == (EID, str(FEED_EID), EID)
        assert (r["outcome"], r["period"], r["market"], r["line"]) == \
            (HOME, "FULL_GAME", "h2h", None)
        assert r["us_market_slug"] == "aec-mlb-mia-col"
        assert r["settlement_rule"] == RULE
        idt = H.j(r["identity"])
        assert idt["matched"] is True and idt["how_matched"]
        det = H.j(r["detail"])
        assert det["outcome_books_basis"] == corr.OUTCOME_BOOKS_BASIS
        assert det["corroborating"]["book_stamps"]
    finally:
        await conn.execute("DELETE FROM valuation_corroboration WHERE "
                           "us_market_slug='aec-mlb-mia-col'")
        await conn.execute("DELETE FROM external_valuations WHERE "
                           "us_market_slug LIKE 'corr-%'")
        await conn.close()


@pg
async def test_a_refused_corroboration_is_persisted_with_its_refusal():
    conn = await H.connect()
    try:
        await _schema(conn)
        verdict = assess(event(book("pinnacle", 5), book("betfair_ex_eu", 90)))
        rec = _evaluate(verdict)
        vid = await ext.persist(conn, rec)
        r = await conn.fetchrow(
            "SELECT qualified, refusal, corroborating_outcome_books, "
            "       pinnapi_outcome_books FROM valuation_corroboration "
            " WHERE valuation_id=$1", vid)
        assert dict(r) == {"qualified": False, "refusal": corr.R_NOT_CURRENT,
                           "corroborating_outcome_books": None,
                           "pinnapi_outcome_books": 1}
        refusals = await conn.fetchval(
            "SELECT refusals FROM external_valuations WHERE id=$1", vid)
        assert ext.R_THIN_OUTCOME in refusals and corr.R_NOT_CURRENT in refusals
    finally:
        await conn.execute("DELETE FROM valuation_corroboration WHERE "
                           "us_market_slug='aec-mlb-mia-col'")
        await conn.execute("DELETE FROM external_valuations WHERE "
                           "us_market_slug LIKE 'corr-%'")
        await conn.close()


@pg
async def test_the_schema_refuses_a_confusable_or_unearned_row():
    import asyncpg
    conn = await H.connect()
    vid = -abs(hash(uuid.uuid4().hex)) % 10**12 - 1
    try:
        await _schema(conn)
        good = assess(event(book("pinnacle", 5), book("betfair_ex_eu", 6)))
        good["pinnapi"]["probability_of_selection"] = 0.6
        for tamper in (
                lambda v: v["pinnapi"].update(outcome_books=2),    # copied
                lambda v: v.update(corroborating_outcome_books=1) or
                v["corroborating"].update(outcome_books=1),       # thin
                lambda v: v["corroborating"].update(age_s=31.0),  # stale
                lambda v: v["corroborating"].update(provider=P.PROVIDER),
                lambda v: v.update(refusal=corr.R_NOT_CURRENT)):  # both
            v = copy.deepcopy(good)
            tamper(v)
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(corr.INSERT, *corr.insert_args(vid, v))
        await conn.execute(corr.INSERT, *corr.insert_args(vid, good))
    finally:
        await conn.execute(
            "DELETE FROM valuation_corroboration WHERE valuation_id=$1", vid)
        await conn.close()


@pg
async def test_a_failed_corroboration_write_leaves_no_valuation():
    """One transaction: no valuation row may exist without its evidence row."""
    conn = await H.connect()
    try:
        await _schema(conn)
        verdict = assess()
        verdict["pinnapi"]["outcome_books"] = 2      # the schema refuses this
        rec = _evaluate(verdict)
        slug = rec["contract"]["us_market_slug"]
        with pytest.raises(Exception):
            await ext.persist(conn, rec)
        assert await conn.fetchval(
            "SELECT count(*) FROM external_valuations WHERE us_market_slug=$1",
            slug) == 0
    finally:
        await conn.close()
