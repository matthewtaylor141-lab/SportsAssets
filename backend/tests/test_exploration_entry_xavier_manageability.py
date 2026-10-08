"""EXPLORATION NEVER ENTERS WHAT XAVIER CANNOT MANAGE (xavier_complete RED,
production readback 2026-10-08 02:10Z, release 08828d04).

The capital-readiness gate xavier_complete read RED on three
PINNACLE_EXPLORATION_PAPER positions whose latest management packets were
incomplete. Two of the causes were decidable at entry with the code
management runs, and the entry now asks that code:

  PRICE    atc-brb-csc-cri-2026-10-08-csc LONG: every held read
           NO_FEED_EVENT. The venue's structured names ("Ceara SC",
           "Criciuma EC") are not the feed's ("Ceara", "Criciuma") and the
           entry's fixture key is the metered provider's, so Xavier's held
           read (pinnapi_feed_runtime.held_moneyline) never resolves the
           fixture: no review is ever fresh. The entry now runs that SAME
           reader (paper_xavier._held_feed) and refuses
           XAVIER_HELD_READ_CANNOT_PRICE_THIS_CONTRACT.
  PROTECT  atc-idnsl-pke-mau-2026-10-09-pke SHORT: 350 contracts bought at
           0.99 for 346.74 (fees 0.24). paper_xavier.protective_price has no
           cent <= 0.99 recovering cost + sale fees + the buffer, so the
           standing protection can never exist (NO_VALID_ACTIVE_PROTECTION
           for the position's life). The entry now refuses
           XAVIER_CANNOT_PROTECT_THIS_ENTRY_NO_PROTECTIVE_PRICE.

NOT REFUSED (and why): a contract the held read RESOLVES whose price is only
not current under the unchanged 30 s rule (the idnsl LONG / SHORT: exact
names, a snapshot price with no observed change) -- Xavier prices it the
moment the provider moves or confirms it; and a process with no feed owner
(nothing about the contract was learned). No threshold is read or moved.

Pure proofs use a real FeedCache behind a fake owner and a fake connection
(as tests/test_held_positions_read_the_live_feed.py); the paper-pass proofs
need RN1X_TEST_DSN.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from sportsassets import pinnapi_census as C
from sportsassets import pinnapi_feed as F
from sportsassets import pinnapi_feed_runtime as R
from sportsassets import refusal_taxonomy as RT
from sportsassets.agents import paper_benchmark as PB
from sportsassets.agents import paper_derek as PD
from sportsassets.agents import paper_explore as PEX
from sportsassets.agents import paper_runtime as PR
from sportsassets.agents import paper_xavier as PX

from tests import paper_harness as H
from tests import paper_live_fixture as PL

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")

AT = 1_791_426_393.0                      # the 02:26:33Z production review
START = AT + 72_000.0
EID = 1637593174
ML = F.FULL_GAME_MONEYLINE_KEY
PRICES = [{"designation": "home", "price": 130},
          {"designation": "draw", "price": 220},
          {"designation": "away", "price": 190}]
MOVED = [{"designation": "home", "price": 135},
         {"designation": "draw", "price": 220},
         {"designation": "away", "price": 185}]


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


def _market(prices):
    return {"key": ML, "type": "moneyline", "period": 0, "status": "open",
            "prices": prices}


def _cache(*, home, away, changed_at=None):
    """A synced soccer cache holding ONE fixture. `changed_at` None: the
    price was only ever seen in the subscribe snapshot (no observed change,
    no provider confirmation) -- the production idnsl state."""
    c = F.FeedCache()
    ep = c.new_connection([("prematch", 1)])
    ev = {"id": EID, "startTime": _iso(START),
          "participants": [{"name": home, "alignment": "home"},
                           {"name": away, "alignment": "away"}],
          "markets": [_market(PRICES)]}
    c.apply({"type": "snapshot", "stream": "prematch", "sport_id": 1,
             "ts": (AT - 2820.0) * 1000, "events": [ev]}, epoch=ep,
            received_ms=(AT - 2816.0) * 1000)
    if changed_at is not None:
        c.apply({"type": "live", "sport_id": 1, "op": "upd",
                 "ts": changed_at * 1000,
                 "rec": {"id": EID, "markets": [_market(MOVED)]}},
                epoch=ep, received_ms=changed_at * 1000 + 40)
    return c


def _own(monkeypatch, cache):
    monkeypatch.setitem(R._STATE, "owner", SimpleNamespace(
        cache=cache, sport_ids=[1, 6]))


class _Tx:
    """The read's savepoint: started, then ALWAYS rolled back."""

    def __init__(self, log):
        self.log = log

    async def start(self):
        self.log.append("SAVEPOINT")

    async def rollback(self):
        self.log.append("ROLLBACK TO SAVEPOINT")


class _Conn:
    """The held contract's catalogue rows (the venue's structured team
    records, one per side, as the census reads them)."""

    def __init__(self, teams, league="brb",
                 sports_type="soccer_team_full_time_winner"):
        self.teams, self.league, self.sports_type = teams, league, sports_type
        self.queries = []
        self.tx = []

    def transaction(self):
        return _Tx(self.tx)

    def _premap(self, team):
        return {"identifier": "atc-held-%s" % team, "side_norm": None,
                "event_slug": "held-ev", "event_title": " vs. ".join(
                    self.teams), "kind": "moneyline", "team_name": team,
                "team_id": team, "team_league": self.league,
                "question": None, "signed": None, "line": None,
                "sports_type": self.sports_type, "game_start": START}

    async def fetch(self, sql, *a):
        self.queries.append(sql)
        if "FROM us_premap" in sql:
            return [self._premap(t) for t in self.teams]
        return []

    async def fetchrow(self, sql, *a):
        self.queries.append(sql)
        if "FROM us_premap" in sql:
            return self._premap(self.teams[0])
        return None


def _cand(*, payout, complement=False, event_key, market="h2h", line=None):
    return {"us_market_slug": "atc-held-slug", "event_key": event_key,
            "payout_event": payout, "payout_is_complement": complement,
            "market": market, "line": line}


# ── PRICE ────────────────────────────────────────────────────────────
async def test_a_fixture_xaviers_held_read_cannot_identify_is_refused(
        monkeypatch):
    """The production brb case: venue "Ceara SC" / "Criciuma EC", feed
    "Ceara" / "Criciuma", a metered fixture key -> NO_FEED_EVENT, exactly
    what every held read of the position answered."""
    _own(monkeypatch, _cache(home="Ceara", away="Criciuma",
                             changed_at=AT - 5.0))
    got = await PEX.xavier_can_price(
        _Conn(("Ceara SC", "Criciuma EC")), at=AT, max_age_s=30.0,
        cand=_cand(payout="Ceara SC",
                   event_key="87416e0fc86513494d5993fb8bb340fe"))
    assert got["manageable"] is False
    assert got["held_read_reason"] == C.S_NO_FEED_EVENT
    assert got["reader"] == "paper_xavier._held_feed"


async def test_a_resolved_fixture_whose_price_is_only_quiet_is_not_refused(
        monkeypatch):
    """The production idnsl case: exact names, a snapshot price with no
    observed change and no provider confirmation -> the held read stops at
    the 30 s rule (FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE) AFTER resolving
    the fixture, market and outcome. Xavier can price it when the provider
    moves or confirms it: not an entry refusal (nor a looser rule)."""
    _own(monkeypatch, _cache(home="Persik Kediri", away="Madura United"))
    conn = _Conn(("Persik Kediri", "Madura United"), league="idnsl")
    for payout, comp in (("Persik Kediri", False),
                         ("NOT(Persik Kediri)", True)):
        got = await PEX.xavier_can_price(
            conn, at=AT, max_age_s=30.0,
            cand=_cand(payout=payout, complement=comp,
                       event_key="pinnapi:%d" % EID))
        assert got["held_read_reason"] == F.R_NO_CHANGE_TIME
        assert got["manageable"] is True
        assert got["held_read"]["feed_event_id"] == EID
        assert got["held_read"]["designation"] == "home"
    # the same fixture moved 5 s ago: the held read is fresh
    _own(monkeypatch, _cache(home="Persik Kediri", away="Madura United",
                             changed_at=AT - 5.0))
    got = await PEX.xavier_can_price(
        conn, at=AT, max_age_s=30.0,
        cand=_cand(payout="Persik Kediri", event_key="pinnapi:%d" % EID))
    assert got["manageable"] is True and got["held_read_reason"] is None


async def test_the_entry_proven_provider_fixture_still_counts(monkeypatch):
    """Venue names that are not the feed's, but an entry valuation keyed by
    the feed's own fixture (pinnapi:<id>): the held read's existing
    ENTRY_PROVEN_PROVIDER_FIXTURE fallback resolves it, so the entry is not
    refused -- the gate is the management path, no stricter."""
    _own(monkeypatch, _cache(home="Ceara", away="Criciuma",
                             changed_at=AT - 5.0))
    got = await PEX.xavier_can_price(
        _Conn(("Ceara SC", "Criciuma EC")), at=AT, max_age_s=30.0,
        cand=_cand(payout="Ceara", event_key="pinnapi:%d" % EID))
    assert got["manageable"] is True, got
    assert got["held_read"]["identity_basis"] == R.IDENTITY_ENTRY_FIXTURE


async def test_a_venue_type_the_held_read_does_not_manage_is_refused(
        monkeypatch):
    _own(monkeypatch, _cache(home="Persik Kediri", away="Madura United",
                             changed_at=AT - 5.0))
    got = await PEX.xavier_can_price(
        _Conn(("Persik Kediri", "Madura United"), league="idnsl",
              sports_type="soccer_team_first_half_winner"),
        at=AT, max_age_s=30.0,
        cand=_cand(payout="Persik Kediri", event_key="pinnapi:%d" % EID))
    assert got["manageable"] is False
    assert got["held_read_reason"] == R.R_HELD_TYPE_UNPROVED


async def test_no_feed_owner_here_is_not_evaluated_and_does_not_refuse(
        monkeypatch):
    monkeypatch.setitem(R._STATE, "owner", None)
    conn = _Conn(("Ceara SC", "Criciuma EC"))
    got = await PEX.xavier_can_price(
        conn, at=AT, max_age_s=30.0,
        cand=_cand(payout="Ceara SC", event_key="87416e0f"))
    assert got["held_read_reason"] == F.R_NO_AUTHORITY
    assert got["manageable"] is True
    assert not any("us_premap" in q for q in conn.queries)


async def test_the_entry_hands_the_held_read_the_identity_xavier_uses(
        monkeypatch):
    """paper_benchmark.xavier_measure hands the held reader the ENTRY
    valuation's payout outcome / complement, its event key and -- only for a
    line family -- its line; the entry check hands it exactly those."""
    seen = []

    async def spy(conn, *, pos, payout_event, payout_is_complement, at,
                  max_age_s):
        seen.append(dict(pos, payout_event=payout_event,
                         payout_is_complement=payout_is_complement,
                         at=at, max_age_s=max_age_s))
        return {"ok": False, "reason": F.R_STALE}

    monkeypatch.setattr(PX, "_held_feed", spy)
    await PEX.xavier_can_price(_Conn(("A", "B")), at=AT, max_age_s=30.0,
                               cand=_cand(payout="NOT(A)", complement=True,
                                          event_key="pinnapi:9", line=2.5))
    await PEX.xavier_can_price(_Conn(("A", "B")), at=AT, max_age_s=30.0,
                               cand=_cand(payout="A +2.5", event_key="e-1",
                                          market="spread", line=2.5))
    assert seen[0] == {"us_market_slug": "atc-held-slug",
                       "entry_event_key": "pinnapi:9", "entry_line": None,
                       "payout_event": "NOT(A)", "payout_is_complement": True,
                       "at": AT, "max_age_s": 30.0}
    assert seen[1]["entry_line"] == 2.5


async def test_a_raising_held_read_is_recorded_not_assumed(monkeypatch):
    async def boom(conn, **kw):
        raise RuntimeError("db gone")

    monkeypatch.setattr(PX, "_held_feed", boom)
    conn = _Conn(("A", "B"))
    got = await PEX.xavier_can_price(conn, at=AT, max_age_s=30.0,
                                     cand=_cand(payout="A", event_key="x"))
    assert got["held_read_reason"] == PEX.R_HELD_READ_FAILED
    assert got["held_read"]["error"] == "RuntimeError"
    # the read's savepoint is rolled back whatever happened in it
    assert conn.tx == ["SAVEPOINT", "ROLLBACK TO SAVEPOINT"]


async def test_the_held_read_savepoint_is_always_rolled_back(monkeypatch):
    _own(monkeypatch, _cache(home="Ceara", away="Criciuma",
                             changed_at=AT - 5.0))
    conn = _Conn(("Ceara SC", "Criciuma EC"))
    await PEX.xavier_can_price(conn, at=AT, max_age_s=30.0,
                               cand=_cand(payout="Ceara SC", event_key="e"))
    assert conn.tx == ["SAVEPOINT", "ROLLBACK TO SAVEPOINT"]


@pg
async def test_a_statement_the_budget_cut_never_aborts_the_callers_tx(
        monkeypatch):
    """The held read swallows its own 1 s budget (paper_xavier._held_feed ->
    HELD_ON_DEMAND_READ_OVER_BUDGET); a statement cut that way leaves its
    transaction in error. Inside the caller's transaction the check's
    savepoint is rolled back, so the caller carries on."""
    import asyncpg

    async def cut(conn, **kw):
        try:
            await conn.execute("SELECT 1/0")
        except asyncpg.PostgresError:
            pass
        return {"ok": False, "reason": R.R_ON_DEMAND_TIMEOUT}

    monkeypatch.setattr(PX, "_held_feed", cut)
    conn = await asyncpg.connect(H.DSN)
    try:
        async with conn.transaction():
            got = await PEX.xavier_can_price(
                conn, at=AT, max_age_s=30.0,
                cand=_cand(payout="A", event_key="x"))
            assert got["held_read_reason"] == R.R_ON_DEMAND_TIMEOUT
            assert got["manageable"] is True
            assert await conn.fetchval("SELECT 41 + 1") == 42
    finally:
        await conn.close()


# ── PROTECT ──────────────────────────────────────────────────────────
def test_the_production_short_at_99_cents_cannot_be_protected():
    """The exact production fills, on the production fee schedule: the
    SHORT (350 @ 0.99, fees 0.24 -> 346.74) has no protective price; the two
    LONGs reproduce the standing orders production placed (0.93, 0.46)."""
    at = 1_791_194_890.0
    short = PEX.xavier_can_protect(
        econ={"qty": 350, "acquisition_cost_usd": 346.5, "fees_usd": 0.24},
        fee_fn=None, at=at)
    assert short["protectable"] is False
    assert short["cost_basis_usd"] == pytest.approx(346.74)
    assert short["refusal"] == "NO_PROTECTIVE_PRICE_BELOW_ONE_DOLLAR"
    for qty, cost, fees, px in ((100, 90.0, 0.63, 0.93),
                                (60, 24.6, 1.01, 0.46)):
        got = PEX.xavier_can_protect(
            econ={"qty": qty, "acquisition_cost_usd": cost, "fees_usd": fees},
            fee_fn=None, at=at)
        assert got["protectable"] is True and got["protective_price"] == px


def test_both_refusals_are_classified():
    assert RT.lookup(PEX.R_XAVIER_CANNOT_PRICE) == (
        "SOFTWARE", "CAPABILITY", "RISK_ADMISSION")
    assert RT.lookup(PEX.R_XAVIER_CANNOT_PROTECT) == (
        "ECONOMIC", "PRICE", "RISK_ADMISSION")
    assert RT.lookup(PEX.R_HELD_READ_FAILED) is not None


# ── THROUGH THE REAL PAPER PASS ─────────────────────────────────────
async def _nosleep(_):
    return None


@pytest.fixture
def explore_only(monkeypatch):
    monkeypatch.setenv(PB.ENV_FLAG, "on")
    monkeypatch.setenv(PL.S.ENV_FLAG, "on")
    PB._CONTEXT_CACHE.clear()
    PD._CONTEXT_CACHE.clear()
    for k in (PB.CG_STRATEGY, PB.MAKER_STRATEGY, PB.EXPLORE_STRATEGY,
              PB.CONTROL_KEY):
        PL.set_policy_control(k, k == PB.EXPLORE_STRATEGY)
    yield
    for k in (PB.CG_STRATEGY, PB.MAKER_STRATEGY, PB.EXPLORE_STRATEGY,
              PB.CONTROL_KEY):
        PL.set_policy_control(k, k in (PB.CG_STRATEGY, PB.EXPLORE_STRATEGY))
    PB._CONTEXT_CACHE.clear()


async def _pass(conn, acct, t, now, client):
    t.t = max(t.t, float(now))
    return await PR.paper_pass(conn, now=now, account_id=acct["account_id"],
                               market_data=client, config=acct["config"],
                               force=True, sleep=_nosleep)


async def _dec(conn, acct, vid):
    return await conn.fetchrow(
        "SELECT * FROM paper_decisions WHERE session_id=$1 AND "
        " valuation_id=$2 AND strategy=$3", acct["session_id"], vid,
        PB.EXPLORE_STRATEGY)


async def _orders(conn, did):
    return await conn.fetchval(
        "SELECT count(*) FROM paper_orders WHERE decision_id=$1", did)


@pg
async def test_the_pass_refuses_an_entry_xavier_cannot_price(explore_only,
                                                             monkeypatch):
    """A candidate that ENTERS on every other check (3 pp gross, positive EV
    after fees) is REFUSED, before any book read and with no paper order,
    when Xavier's held read answers NO_FEED_EVENT; a held read refused only
    for currency (age unknown) does not stop the same entry."""
    answer = {"ok": False, "reason": C.S_NO_FEED_EVENT, "sport_id": 1}

    async def held(conn, **kw):
        return dict(answer)

    monkeypatch.setattr(PX, "_held_feed", held)
    conn = await H.connect()
    now = time.time() + 5.0
    try:
        await PL.purge_everything(conn)
        await PL.purge_research_models(conn)
        acct = await PL.new_account(conn, "xavmanage1", now=now)
        t = PL.Transport(now)
        client = PL.client(t)
        v = await PL.valuation(conn, decided_at=now - 10, p_pin=0.53,
                               compatibility="INCOMPATIBLE")
        t.set(v["slug"], offers=[(0.50, 5000)], bids=[(0.48, 5000)])
        p = await _pass(conn, acct, t, now, client)
        assert p["ran"] and not p["errors"], p["errors"]
        d = await _dec(conn, acct, v["valuation_id"])
        assert d is not None and d["verdict"] == "REFUSE", d and d["refusals"]
        assert d["refusal"] == PEX.R_XAVIER_CANNOT_PRICE
        assert d["book_obs_id"] is None
        assert await _orders(conn, d["decision_id"]) == 0
        cond = {c["condition"]: c for c in
                H.j(d["policy_decision"])["conditions"]}
        x = cond["xavier_held_read_can_price_this_contract"]
        assert x["passed"] is False and x["value"] == C.S_NO_FEED_EVENT
        econ = H.j(d["economics"])
        assert econ["xavier_management"]["price"]["held_read_reason"] == \
            C.S_NO_FEED_EVENT
        # currency only: the identical candidate enters
        answer.update(reason=F.R_NO_CHANGE_TIME, feed_event_id=EID)
        v2 = await PL.valuation(conn, decided_at=now - 9, p_pin=0.53,
                                compatibility="INCOMPATIBLE")
        t.set(v2["slug"], offers=[(0.50, 5000)], bids=[(0.48, 5000)])
        p = await _pass(conn, acct, t, now + 1, client)
        assert not p["errors"], p["errors"]
        d2 = await _dec(conn, acct, v2["valuation_id"])
        assert d2 is not None and d2["verdict"] == "ENTER", d2["refusals"]
        assert await _orders(conn, d2["decision_id"]) == 1
        assert client.mutation_attempts == 0
    finally:
        await PL.purge_everything(conn)
        await conn.close()


@pg
async def test_the_pass_refuses_an_entry_xavier_cannot_protect(explore_only):
    """Positive EV after fees (p 0.999 against a 0.98 ask) but no cent
    <= 0.99 recovers cost + sale fees + the buffer: REFUSED by name, no
    paper order. The same candidate at 0.97 is protectable and enters."""
    conn = await H.connect()
    now = time.time() + 5.0
    try:
        await PL.purge_everything(conn)
        await PL.purge_research_models(conn)
        acct = await PL.new_account(conn, "xavmanage2", now=now)
        t = PL.Transport(now)
        client = PL.client(t)
        v = await PL.valuation(conn, decided_at=now - 10, p_pin=0.999,
                               compatibility="INCOMPATIBLE")
        t.set(v["slug"], offers=[(0.98, 5000)], bids=[(0.96, 5000)])
        p = await _pass(conn, acct, t, now, client)
        assert p["ran"] and not p["errors"], p["errors"]
        d = await _dec(conn, acct, v["valuation_id"])
        assert d is not None and d["verdict"] == "REFUSE", d and d["refusals"]
        assert PEX.R_XAVIER_CANNOT_PROTECT in d["refusals"]
        assert d["refusal"] == PEX.R_XAVIER_CANNOT_PROTECT, d["refusals"]
        assert await _orders(conn, d["decision_id"]) == 0
        econ = H.j(d["economics"])
        prot = econ["xavier_management"]["protection"]
        assert prot["protectable"] is False
        assert prot["refusal"] == "NO_PROTECTIVE_PRICE_BELOW_ONE_DOLLAR"
        assert H.j(d["policy_decision"])["estimate"][
            "passes_positive_after_fees"] is True
        v2 = await PL.valuation(conn, decided_at=now - 9, p_pin=0.999,
                                compatibility="INCOMPATIBLE")
        t.set(v2["slug"], offers=[(0.97, 5000)], bids=[(0.95, 5000)])
        p = await _pass(conn, acct, t, now + 1, client)
        assert not p["errors"], p["errors"]
        d2 = await _dec(conn, acct, v2["valuation_id"])
        assert d2 is not None and d2["verdict"] == "ENTER", d2["refusals"]
        assert H.j(d2["economics"])["xavier_management"]["protection"][
            "protective_price"] == 0.99
        assert client.mutation_attempts == 0
    finally:
        await PL.purge_everything(conn)
        await conn.close()
