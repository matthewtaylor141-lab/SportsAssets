"""CAPITAL-CRITICAL: A HELD CONTRACT ENTERED ON THE METERED PROVIDER IS READ
ON THE PINNAPI FIXTURE THE PINNAPI MATCHER ALREADY PROVED FOR IT (RC6
xavier-records).

Production (read-only research, 2026-10-09 01:30-01:37Z): the held PAPER
position atc-brb-csc-cri-2026-10-08-csc (Ceara SC vs Criciuma EC, LONG 60)
answered 1,939 held PinnAPI reads NO_FEED_EVENT over its life. Its venue
names ("ceara sc" / "criciuma ec") are not Pinnacle's and its entry
valuation was the metered provider's (event key 87416e0f...), so the held
read had no exact-name match and no entry-proven fixture -- while 25
valuations of the SAME contract had been written from the PinnAPI feed,
each recording the matched fixture (reference_input.feed_event_id
1637577754). Identity, not coverage. Over 14 days, 74 held groups (19,462
reviews) were in that state.

Pinned here: the held read (benchmark and Derek measures), the exploration
entry check and the held watch are handed the matcher's recorded fixture
for a metered entry key; a PinnAPI entry key is never overridden; the read's
own checks (the outcome maps to one designation, the start agrees) still
refuse a wrong fixture; and the lookup runs on the real schema. The 30 s
rule, every limit and every order path are unchanged.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import time

import pytest

from sportsassets import pinnapi_census as C
from sportsassets import pinnapi_feed_runtime as R
from sportsassets import pinnapi_held as PH
from sportsassets import xavier_held_fixture as XHF
from sportsassets import xavier_measure_refresh as MR
from sportsassets.agents import paper_benchmark as PB
from sportsassets.agents import paper_explore as PEX
from sportsassets.agents import paper_xavier as PX
from tests import paper_harness as H
from tests import paper_live_fixture as PL
from tests import test_exploration_entry_xavier_manageability as EXM
from tests import test_held_positions_read_the_live_feed as LF
from tests import test_rc6_held_watch_identity as HW
from tests.test_r30a_line_market_family import FID, nfl_cache

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")

METERED = "87416e0fc86513494d5993fb8bb340fe"


class _Tx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _matched_row(slug, fid, *, vid=99, at=None):
    return {"slug": slug, "id": vid,
            "decided_at": (at if at is not None else LF.AT - 3600.0),
            "feed_event_id": str(fid),
            "fixture_match": json.dumps({"name_match": "EXACT_FOLDED_NAMES",
                                         "rules": []})}


class _MeasureConn(LF._Conn):
    """The benchmark measure's reads (test_held_positions_read_the_live_
    feed) with the entry valuation's event key and the PinnAPI matcher's
    recorded fixture of the contract (CONTRACT_FIXTURES_SQL)."""

    def __init__(self, *, entry_key=METERED, matched=None, start=None, **kw):
        super().__init__(**kw)
        self.entry_key, self.matched, self.start = entry_key, matched, start

    def transaction(self):
        return _Tx()

    def _premap(self, team):
        d = super()._premap(team)
        if self.start is not None:
            d["game_start"] = self.start
        return d

    async def fetch(self, sql, *a):
        if "reference_input" in sql:
            self.queries.append(sql)
            return ([] if self.matched is None
                    else [_matched_row(a[0][0], self.matched)])
        return await super().fetch(sql, *a)

    async def fetchrow(self, sql, *a):
        if "SELECT payout_event" in sql:
            self.queries.append(sql)
            return {"payout_event": self.payout,
                    "payout_is_complement": self.comp,
                    "event_key": self.entry_key}
        return await super().fetchrow(sql, *a)


async def _measure(conn):
    return await PB.xavier_measure(conn, LF.CTX, pos=LF.POS,
                                   feed=PX._held_feed)


# ═════════════════════════════════════════════════════════════════════
# 1 · THE RULE (pure)
# ═════════════════════════════════════════════════════════════════════

def test_a_pinnapi_entry_key_stands_and_a_metered_one_takes_the_match():
    got = XHF.resolve("pinnapi:7", {"feed_event_id": "8", "id": 1})
    assert got["event_key"] == "pinnapi:7"
    assert got["basis"] == XHF.B_ENTRY_PINNAPI
    got = XHF.resolve(METERED, {"feed_event_id": "1637577754", "id": 41139,
                                "decided_at": 1.0})
    assert got["event_key"] == "pinnapi:1637577754"
    assert got["basis"] == XHF.B_MATCHED
    assert got["entry_event_key"] == METERED
    assert got["matched_valuation_id"] == 41139
    got = XHF.resolve(METERED, None)
    assert got == {"event_key": METERED, "basis": XHF.B_NONE,
                   "entry_event_key": METERED, "version": XHF.VERSION}
    assert XHF.is_pinnapi_key("pinnapi:1") and \
        not XHF.is_pinnapi_key("pinnapi:") and not XHF.is_pinnapi_key(None)


# ═════════════════════════════════════════════════════════════════════
# 2 · THE CSC SHAPE THROUGH THE BENCHMARK MEASURE (real FeedCache)
# ═════════════════════════════════════════════════════════════════════

async def test_the_csc_shape_is_read_on_the_matchers_fixture(monkeypatch):
    """Venue "Ceara SC" / "Criciuma EC", feed "Ceara" / "Criciuma", a
    metered entry key. BASE: NO_FEED_EVENT on every read, whatever the
    matcher had recorded. NOW: the recorded fixture is handed to the read,
    which prices it fresh -- through its own checks."""
    LF._own(monkeypatch, LF._cache(home="Ceara", away="Criciuma"))
    teams = ("Ceara SC", "Criciuma EC")
    # the matcher recorded the fixture of this contract (BASE: stale,
    # NO_FEED_EVENT -- the recorded fixture was never consulted)
    conn = _MeasureConn(teams=teams, payout="Ceara", matched=LF.EID)
    out = await _measure(conn)
    assert out["stale"] is False, out.get("feed_refusal")
    assert out["source"] == PB.SOURCE_FEED_CURRENT
    assert out["feed"]["feed_event_id"] == LF.EID
    assert out["feed"]["identity_basis"] == R.IDENTITY_ENTRY_FIXTURE
    hf = out["feed"]["held_fixture"]
    assert hf["basis"] == XHF.B_MATCHED
    assert hf["event_key"] == "pinnapi:%d" % LF.EID
    assert hf["entry_event_key"] == METERED
    assert hf["matched_valuation_id"] == 99
    # the unchanged 30 s rule: the quote changed 5 s before the review
    assert out["feed"]["quote_age_s"] == pytest.approx(5.0)
    # no recorded match: exactly the production answer, its basis named
    out = await _measure(_MeasureConn(teams=teams, payout="Ceara"))
    assert out["stale"] is True
    assert out["feed_refusal"] == C.S_NO_FEED_EVENT
    assert out["feed_detail"]["held_fixture"]["basis"] == XHF.B_NONE
    assert any("reference_input" in q for q in conn.queries)


async def test_a_pinnapi_entry_key_is_never_overridden(monkeypatch):
    LF._own(monkeypatch, LF._cache(home="Ceara", away="Criciuma"))
    conn = _MeasureConn(teams=("Ceara SC", "Criciuma EC"), payout="Ceara",
                        entry_key="pinnapi:%d" % LF.EID, matched=999)
    out = await _measure(conn)
    assert out["stale"] is False
    assert out["feed"]["held_fixture"]["basis"] == XHF.B_ENTRY_PINNAPI
    assert out["feed"]["feed_event_id"] == LF.EID
    # the entry named the fixture: the matcher's record is not even read
    assert not any("reference_input" in q for q in conn.queries)


async def test_a_wrong_recorded_fixture_still_refuses_by_the_reads_checks(
        monkeypatch):
    """The read re-checks what it is handed: a recorded fixture whose teams
    do not carry the held outcome refuses (outcome unmapped), and one whose
    start disagrees with the venue's refuses (fixture time not proved)."""
    LF._own(monkeypatch, LF._cache(home="Santos", away="Corinthians"))
    out = await _measure(_MeasureConn(teams=("Ceara SC", "Criciuma EC"),
                                      payout="Ceara", matched=LF.EID))
    assert out["stale"] is True
    assert out["feed_refusal"] == R.R_OUTCOME_UNMAPPED
    LF._own(monkeypatch, LF._cache(home="Ceara", away="Criciuma"))
    out = await _measure(_MeasureConn(teams=("Ceara SC", "Criciuma EC"),
                                      payout="Ceara", matched=LF.EID,
                                      start=LF.START + 6 * 3600))
    assert out["stale"] is True
    assert out["feed_refusal"] == R.R_HELD_TIME_UNPROVED


async def test_an_unreadable_record_resolves_nothing_and_says_so(monkeypatch):
    class _Broken(_MeasureConn):
        async def fetch(self, sql, *a):
            if "reference_input" in sql:
                raise RuntimeError("statement timeout")
            return await super().fetch(sql, *a)

    LF._own(monkeypatch, LF._cache(home="Ceara", away="Criciuma"))
    out = await _measure(_Broken(teams=("Ceara SC", "Criciuma EC"),
                                 payout="Ceara", matched=LF.EID))
    assert out["stale"] is True
    assert out["feed_refusal"] == C.S_NO_FEED_EVENT
    hf = out["feed_detail"]["held_fixture"]
    assert hf["basis"] == XHF.B_UNREAD and "RuntimeError" in hf["why"]
    assert hf["event_key"] == METERED


# ═════════════════════════════════════════════════════════════════════
# 3 · THE DEREK MEASURE HANDS THE READ THE SAME FIXTURE
# ═════════════════════════════════════════════════════════════════════

async def test_the_derek_refresh_hands_the_held_read_the_resolved_key():
    seen = []

    async def spy(conn, *, pos, payout_event, payout_is_complement, at,
                  max_age_s):
        seen.append(pos.get("entry_event_key"))
        return {"ok": False, "reason": "FEED_QUOTE_OLDER_THAN_LIMIT"}

    contract = {"id": 1, "us_market_slug": "s", "payout_event": "A",
                "payout_is_complement": False, "event_key": METERED,
                "market": "h2h", "line": None,
                "held_event_key": "pinnapi:%d" % LF.EID}
    kw = {"pos": {"us_market_slug": "s", "holding_side": "LONG"},
          "stored": None, "model": {"ok": True},
          "levels_buy": [{"price": 0.4}], "at": LF.AT, "max_age_s": 30.0,
          "score": lambda m, **k: {"ok": True, "p": 0.4},
          "blend": lambda a, b: (a + b) / 2, "held_feed": spy}
    await MR.refresh(None, contract=contract, **kw)
    assert seen == ["pinnapi:%d" % LF.EID]
    # no resolved key: the entry's own, exactly as before
    entry_only = {k: v for k, v in contract.items() if k != "held_event_key"}
    await MR.refresh(None, contract=entry_only, **kw)
    assert seen[-1] == METERED


class _DerekConn:
    """paper_xavier._measure's reads for a Derek (two-model) position: the
    entry contract (a metered key), no stored reading, the entry decision's
    blend -- and the matcher's recorded fixture of the contract."""

    def __init__(self, *, matched):
        self.matched, self.queries = matched, []
        self.obs = dt.datetime.fromtimestamp(LF.AT - 7300, dt.timezone.utc)
        self.rcv = dt.datetime.fromtimestamp(LF.AT - 7299, dt.timezone.utc)

    def transaction(self):
        return _Tx()

    async def fetch(self, sql, *a):
        self.queries.append(sql)
        if "reference_input" in sql:
            return [_matched_row(a[0][0], self.matched)]
        return []

    async def fetchrow(self, sql, *a):
        self.queries.append(sql)
        if "SELECT v.id,v.us_market_slug" in sql:
            return {"id": 6594, "us_market_slug": LF.POS["us_market_slug"],
                    "payout_event": "Ceara", "payout_is_complement": False,
                    "event_key": METERED, "market": "h2h", "line": None,
                    "observed_at": self.obs, "received_at": self.rcv}
        if "SELECT d.p_blended" in sql:
            return {"p_blended": 0.53, "decided_at": dt.datetime.fromtimestamp(
                LF.AT - 7200, dt.timezone.utc)}
        return None


async def test_the_derek_measure_resolves_the_fixture_and_keeps_its_stamps(
        monkeypatch):
    """Through paper_xavier._measure: the held read is handed the matcher's
    fixture (BASE: the metered entry key), and the stale ENTRY_TIME_MEASURE
    now carries the entry reading's own source and receipt stamps (BASE:
    none -- production TB-DAL source_at NOT_RECORDED on every stale review),
    beside the decision instant, never instead of it."""
    from sportsassets.agents import paper_benchmark as PBM
    seen = {}

    async def spy_refresh(conn, *, contract, **kw):
        seen["contract"] = dict(contract)
        return {"ok": False, "stale": True, "p": None,
                "why": "CURRENT_HELD_PROBABILITY_UNAVAILABLE",
                "feed_refusal": "FEED_OWNERSHIP_NOT_HELD",
                "feed_detail": {}}

    async def derek(conn, group_id):
        return PBM.TWO_MODEL_STRATEGY

    monkeypatch.setattr(PBM, "group_strategy", derek)
    monkeypatch.setattr(MR, "refresh", spy_refresh)
    ctx = {"account_id": "paper_acct_t", "now": LF.AT,
           "config": {"entry": {"valuation_lookback_s": 3600.0,
                                "pinnacle_max_age_s": 30.0}},
           "derek": {"model": {"ok": True}}}
    pos = dict(LF.POS, position_key="pk")
    m = await PX._measure(_DerekConn(matched=LF.EID), ctx, pos=pos,
                          levels_buy=[{"price": 0.5}])
    assert seen["contract"]["held_event_key"] == "pinnapi:%d" % LF.EID
    assert seen["contract"]["event_key"] == METERED   # the stored lookup's
    assert m["source"] == "ENTRY_TIME_MEASURE" and m["stale"] is True
    assert m["entry_pinnacle_at"] == pytest.approx(LF.AT - 7300)
    assert m["entry_pinnacle_received_at"] == pytest.approx(LF.AT - 7299)
    assert m["at"] == pytest.approx(LF.AT - 7200)      # the decision instant
    assert m["feed_detail"]["held_fixture"]["basis"] == XHF.B_MATCHED
    ev = PX.probability_evidence(m, at=LF.AT, limit_s=30.0, qty=10)
    assert ev["evidence_state"] == PX.E_STALE           # never made fresh
    assert ev["probability_source_at"] == pytest.approx(LF.AT - 7300)
    assert ev["probability_received_at"] == pytest.approx(LF.AT - 7299)


# ═════════════════════════════════════════════════════════════════════
# 4 · THE EXPLORATION ENTRY CHECK ASKS THE SAME READ THE SAME QUESTION
# ═════════════════════════════════════════════════════════════════════

class _ExploreConn(EXM._Conn):
    def __init__(self, teams, *, matched=None, **kw):
        super().__init__(teams, **kw)
        self.matched = matched

    def transaction(self):
        tx = super().transaction()

        class _Both:
            async def start(self):
                await tx.start()

            async def rollback(self):
                await tx.rollback()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False
        return _Both()

    async def fetch(self, sql, *a):
        if "reference_input" in sql:
            self.queries.append(sql)
            return ([] if self.matched is None
                    else [_matched_row(a[0][0], self.matched)])
        return await super().fetch(sql, *a)


async def test_the_entry_check_resolves_the_matchers_fixture(monkeypatch):
    """BASE: the CSC-shaped candidate is refused NO_FEED_EVENT even when the
    matcher has recorded its fixture -- an entry Xavier will in fact manage.
    NOW: manageable, on the same fixture management will read."""
    EXM._own(monkeypatch, EXM._cache(home="Ceara", away="Criciuma",
                                     changed_at=EXM.AT - 5.0))
    cand = EXM._cand(payout="Ceara", event_key=METERED)
    got = await PEX.xavier_can_price(
        _ExploreConn(("Ceara SC", "Criciuma EC")), at=EXM.AT,
        max_age_s=30.0, cand=cand)
    assert got["manageable"] is False
    assert got["held_read_reason"] == C.S_NO_FEED_EVENT
    conn = _ExploreConn(("Ceara SC", "Criciuma EC"), matched=EXM.EID)
    got = await PEX.xavier_can_price(conn, at=EXM.AT, max_age_s=30.0,
                                     cand=cand)
    assert got["manageable"] is True, got
    assert got["held_read_reason"] is None
    assert got["held_read"]["feed_event_id"] == EXM.EID
    assert got["held_read"]["identity_basis"] == R.IDENTITY_ENTRY_FIXTURE
    assert got["held_fixture"]["basis"] == XHF.B_MATCHED
    # the held read's own savepoint is still opened once and rolled back
    assert conn.tx == ["SAVEPOINT", "ROLLBACK TO SAVEPOINT"]


# ═════════════════════════════════════════════════════════════════════
# 5 · THE HELD WATCH TARGETS THE SAME FIXTURE
# ═════════════════════════════════════════════════════════════════════

class _WatchCatalogue(HW._Catalogue):
    def __init__(self, row, event_rows, *, matched=None, **kw):
        super().__init__(row, event_rows, **kw)
        self.matched = matched

    def transaction(self):
        return _Tx()

    async def fetch(self, sql, *args):
        if "reference_input" in sql:
            self.sql.append(sql)
            return ([] if self.matched is None
                    else [_matched_row(args[0][0], self.matched)])
        return await super().fetch(sql, *args)


def test_the_held_watch_targets_the_matchers_fixture(monkeypatch):
    cache = nfl_cache()
    HW._own(monkeypatch, cache)
    lone = HW._row(HW.SPREAD, slug=HW.SLUG)
    # a metered entry key and no recorded match: not a target (as before)
    w0 = PH.HeldWatch(clock=time.time)
    asyncio.run(PH.refresh(_WatchCatalogue(lone, [lone],
                                           entry="e-%s" % HW.SLUG),
                           watch=w0))
    assert not w0.is_held(FID)
    # the matcher recorded the contract's fixture: the watch targets it
    w1 = PH.HeldWatch(clock=time.time)
    out = asyncio.run(PH.refresh(
        _WatchCatalogue(lone, [lone], entry="e-%s" % HW.SLUG, matched=FID),
        watch=w1))
    assert out["ok"], out
    assert w1.is_held(FID) and w1.slug_event == {HW.SLUG: FID}


# ═════════════════════════════════════════════════════════════════════
# 6 · THE LOOKUP ON THE REAL SCHEMA (Postgres)
# ═════════════════════════════════════════════════════════════════════

async def _pinnapi_row(conn, slug, *, fid, decided_at, provider=XHF.PROVIDER,
                       ref_provider=XHF.PROVIDER):
    v = await PL.valuation(conn, slug=slug, decided_at=decided_at)
    await conn.execute(
        "UPDATE external_valuations SET provider=$2, settlement_comparison="
        " jsonb_build_object('reference_input', jsonb_build_object("
        " 'provider', $3::text, 'feed_event_id', $4::text, 'fixture_match',"
        " jsonb_build_object('name_match', 'EXACT_FOLDED_NAMES'))) "
        " WHERE id=$1", v["valuation_id"], provider, ref_provider, fid)
    return v["valuation_id"]


@pg
async def test_the_contract_fixture_read_runs_on_the_real_schema():
    import uuid
    conn = await H.connect()
    slug = "xhf-%s" % uuid.uuid4().hex[:10]
    other = "xhf-%s" % uuid.uuid4().hex[:10]
    now = time.time()
    try:
        old = await _pinnapi_row(conn, slug, fid="1001", decided_at=now - 900)
        new = await _pinnapi_row(conn, slug, fid="1002", decided_at=now - 60)
        # a metered row of the same contract carrying a reference input of
        # another provider is never a PinnAPI fixture
        await _pinnapi_row(conn, slug, fid="9999", decided_at=now - 5,
                           provider="the-odds-api.com/v4",
                           ref_provider="the-odds-api.com/v4")
        # outside the lookback: not used
        await _pinnapi_row(conn, other, fid="2001",
                           decided_at=now - XHF.LOOKBACK_S - 600)
        got = await XHF.matched_fixtures(conn, [slug, other], at=now)
        assert set(got) == {slug}
        assert got[slug]["feed_event_id"] == "1002"
        assert got[slug]["id"] == new and got[slug]["id"] != old
        key = await XHF.held_key(conn, us_market_slug=slug,
                                 entry_event_key="e-%s" % slug, at=now)
        assert key["event_key"] == "pinnapi:1002"
        assert key["basis"] == XHF.B_MATCHED
        key = await XHF.held_key(conn, us_market_slug=other,
                                 entry_event_key="e-x", at=now)
        assert key["basis"] == XHF.B_NONE and key["event_key"] == "e-x"
        # inside a caller's transaction the read keeps its own savepoint
        async with conn.transaction():
            got = await XHF.matched_fixtures(conn, [slug], at=now)
            assert got[slug]["feed_event_id"] == "1002"
            assert await conn.fetchval("SELECT 1") == 1
    finally:
        async with conn.transaction():
            await conn.execute("SET LOCAL session_replication_role = replica")
            await conn.execute("DELETE FROM external_valuations WHERE "
                               " us_market_slug = ANY($1::text[])",
                               [slug, other])
        await conn.close()


def test_the_held_fixture_module_imports_only_the_standard_library():
    """Pinned for the benchmark's import allow-list
    (tests/test_pinnacle_only_paper_benchmark.ALLOWED_IMPORTS): no venue,
    order, execution, funded or paper module is reachable through it."""
    import ast
    import inspect
    names = set()
    for node in ast.walk(ast.parse(inspect.getsource(XHF))):
        if isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
    assert names <= {"__future__", "annotations", "time"}, names


def test_no_threshold_moves():
    """The held read's limits are the existing ones: nothing here reads or
    sets a freshness, start or budget bound."""
    import inspect
    src = inspect.getsource(XHF)
    for name in ("max_age_s", "START_TOLERANCE_S", "MARK_STALE_AFTER_S",
                 "HELD_ON_DEMAND_BUDGET_S"):
        assert name not in src, name
