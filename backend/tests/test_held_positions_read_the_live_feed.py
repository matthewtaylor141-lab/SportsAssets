"""XAVIER'S HELD POSITIONS READ THE LIVE PINNAPI FEED WHEN THE VALUATION IS
STALE, AND NOTHING ELSE CHANGES.

  FRESH      no fresh external valuation, the in-process feed has the held
             contract's full-game moneyline changed 5 s ago -> a fresh
             PINNAPI_FEED_CURRENT measure, de-vigged over the COMPLETE set
             (3-way soccer, 2-way baseball), with the feed's provenance.
  OLD        the same quote 31 s old -> the stale measure exactly as before,
             feed_refusal FEED_QUOTE_OLDER_THAN_LIMIT.
  NO OWNER   no feed owner in this process -> stale, FEED_OWNERSHIP_NOT_HELD,
             and not even a catalogue read.
  UNMATCHED  no provider event for the held contract / sport out of scope ->
             stale, with the census's own state as the reason.
  GUARD      a stale measure (feed refused) still blocks a discretionary EXIT
             on the paper book; a fresh feed measure lets the same book rank
             it (the guard is unchanged, it just sees fresher evidence).

Pure: a real FeedCache behind a fake owner, a fake connection. The GUARD
proof uses the paper harness (needs RN1X_TEST_DSN), as the existing
stale-guard test does.
"""
from __future__ import annotations

import datetime as dt
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_simulator as SIM
from sportsassets import bettor_pinnacle_devig as devig
from sportsassets import pinnapi_census as C
from sportsassets import pinnapi_feed as F
from sportsassets import pinnapi_feed_runtime as R
from sportsassets.agents import paper_benchmark as PB
from sportsassets.agents import paper_xavier as PX

from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")

AT = 1_790_600_000.0
START = AT + 3600.0
EID = 501
ML = F.FULL_GAME_MONEYLINE_KEY
SOCCER = [{"designation": "home", "price": 300},
          {"designation": "draw", "price": 250},
          {"designation": "away", "price": -125}]
SOCCER_MOVED = [{"designation": "home", "price": 310},
                {"designation": "draw", "price": 255},
                {"designation": "away", "price": -130}]
BASEBALL = [{"designation": "home", "price": -150},
            {"designation": "away", "price": 135}]
BASEBALL_MOVED = [{"designation": "home", "price": -155},
                  {"designation": "away", "price": 140}]
CTX = {"now": AT, "config": {"entry": {"valuation_lookback_s": 3600.0,
                                       "pinnacle_max_age_s": 30.0}}}
POS = {"group_id": "g1", "holding_side": "LONG",
       "us_market_slug": "atc-brb-fla-pal-2026-10-02-fla"}


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


def _market(prices):
    return {"key": ML, "type": "moneyline", "period": 0, "status": "open",
            "prices": prices}


def _cache(*, sport=1, home="Flamengo", away="Palmeiras", prices=SOCCER,
           moved=SOCCER_MOVED, changed_at=AT - 5.0):
    """A synced cache whose held event's moneyline last CHANGED at
    `changed_at` (a snapshot alone never stamps a change)."""
    c = F.FeedCache()
    ep = c.new_connection([("live", sport)])
    ev = {"id": EID, "startTime": _iso(START),
          "participants": [{"name": home, "alignment": "home"},
                           {"name": away, "alignment": "away"}],
          "markets": [_market(prices)]}
    c.apply({"type": "snapshot", "stream": "live", "sport_id": sport,
             "ts": (changed_at - 60) * 1000, "events": [ev]}, epoch=ep,
            received_ms=(changed_at - 60) * 1000)
    if moved is not None:
        c.apply({"type": "live", "sport_id": sport, "op": "upd",
                 "ts": changed_at * 1000,
                 "rec": {"id": EID, "markets": [_market(moved)]}},
                epoch=ep, received_ms=changed_at * 1000 + 40)
    return c


def _own(monkeypatch, cache, sport_ids=(1, 6)):
    monkeypatch.setitem(R._STATE, "owner", SimpleNamespace(
        cache=cache, sport_ids=sorted(sport_ids)))


class _Conn:
    """The benchmark entry, its contract, an optional (stale) valuation and
    the held contract's catalogue row."""

    def __init__(self, *, reading_age=900.0, payout="Flamengo", comp=False,
                 title="Flamengo vs Palmeiras", sports_type="soccer_team_full_time_winner",
                 teams=("Flamengo", "Palmeiras")):
        self.reading_age, self.payout, self.comp = reading_age, payout, comp
        self.title, self.sports_type, self.teams = title, sports_type, teams
        self.queries = []

    def _premap(self, team):
        # the venue's structured team record (census identity), one per side
        return {"identifier": "c-%s" % team, "side_norm": None,
                "event_slug": "ev-held", "event_title": self.title,
                "kind": "moneyline", "team_name": team, "team_id": team,
                "team_league": None, "question": None, "signed": None,
                "line": None, "sports_type": self.sports_type,
                "game_start": START}

    async def fetch(self, sql, *a):
        self.queries.append(sql)
        if "FROM us_premap" in sql:
            return [self._premap(t) for t in self.teams]
        return []

    async def fetchrow(self, sql, *a):
        self.queries.append(sql)
        if "FROM paper_decisions d JOIN paper_orders" in sql:
            return {"decision_id": "d1", "p_pinnacle": 0.21,
                    "decided_at": dt.datetime.fromtimestamp(
                        AT - 7200, dt.timezone.utc), "valuation_id": 7}
        if "SELECT payout_event" in sql:
            return {"payout_event": self.payout,
                    "payout_is_complement": self.comp}
        if "SELECT id, probability, observed_at" in sql:
            if self.reading_age is None:
                return None
            return {"id": 8, "probability": 0.2,
                    "observed_at": dt.datetime.fromtimestamp(
                        AT - self.reading_age, dt.timezone.utc)}
        if "FROM us_premap" in sql:
            return self._premap(self.teams[0])
        return None


async def _measure(conn, ctx=CTX, pos=POS):
    return await PB.xavier_measure(conn, ctx, pos=pos, feed=PX._held_feed)


def _devigged(prices):
    names = sorted(p["designation"] for p in prices)
    by = {p["designation"]: F.american_to_decimal(p["price"]) for p in prices}
    return dict(zip(names, devig.devig([by[n] for n in names])))


# ── FRESH ────────────────────────────────────────────────────────────
async def test_a_fresh_feed_quote_makes_a_fresh_measure_with_provenance(
        monkeypatch):
    _own(monkeypatch, _cache())
    out = await _measure(_Conn())
    assert out["stale"] is False and out["source"] == PB.SOURCE_FEED_CURRENT
    want = _devigged(SOCCER_MOVED)
    assert set(want) == {"home", "draw", "away"}, "3-way, draw included"
    assert out["p"] == pytest.approx(want["home"], abs=1e-9)
    fd = out["feed"]
    assert fd["quote_age_s"] == pytest.approx(5.0)
    assert fd["source_change_ms"] == pytest.approx((AT - 5.0) * 1000)
    assert fd["epoch"] == 1 and fd["parser"] == F.PARSER_VERSION
    assert fd["feed_event_id"] == EID and fd["market_key"] == ML
    assert fd["designation"] == "home" and fd["sport_id"] == 1
    assert fd["devig"]["outcomes"] == 3
    assert out["pinnacle_limit_s"] == 30.0
    assert out["replaced_stale_source"] == "PINNACLE_ONLY_LATEST"
    assert "feed_refusal" not in out


async def test_a_complement_on_a_3way_book_is_the_other_two_together(
        monkeypatch):
    _own(monkeypatch, _cache())
    out = await _measure(_Conn(payout="NOT(Flamengo)", comp=True))
    want = _devigged(SOCCER_MOVED)
    assert out["stale"] is False
    assert out["p"] == pytest.approx(want["away"] + want["draw"], abs=1e-9)


async def test_baseball_is_a_2way_devig_and_orientation_is_by_team(
        monkeypatch):
    _own(monkeypatch, _cache(sport=6, home="New York Yankees",
                             away="Tampa Bay Rays", prices=BASEBALL,
                             moved=BASEBALL_MOVED))
    out = await _measure(_Conn(payout="Tampa Bay Rays",
                               title="Rays vs Yankees",
                               sports_type="baseball_team_full_game_winner",
                               teams=("Tampa Bay Rays", "New York Yankees")))
    assert out["stale"] is False
    assert out["feed"]["designation"] == "away"
    assert out["p"] == pytest.approx(_devigged(BASEBALL_MOVED)["away"])


async def test_a_fresh_external_valuation_never_consults_the_feed(
        monkeypatch):
    _own(monkeypatch, _cache())
    conn = _Conn(reading_age=4.0)
    out = await _measure(conn)
    assert out["source"] == "PINNACLE_ONLY_CURRENT" and out["stale"] is False
    assert not any("us_premap" in q for q in conn.queries)


# ── OLD / NO OWNER / UNMATCHED: today's stale measure, reason named ──
async def test_a_31s_old_feed_quote_stays_stale_with_the_feed_refusal(
        monkeypatch):
    _own(monkeypatch, _cache(changed_at=AT - 31.0))
    out = await _measure(_Conn())
    assert out["stale"] is True and out["source"] == "PINNACLE_ONLY_LATEST"
    assert out["p"] == 0.2 and out["pinnacle_age_s"] == 900.0
    assert out["feed_refusal"] == F.R_STALE == "FEED_QUOTE_OLDER_THAN_LIMIT"
    assert out["feed_detail"]["provenance"]["quote_age_s"] == \
        pytest.approx(31.0)


async def test_a_snapshot_price_with_no_observed_change_is_never_fresh(
        monkeypatch):
    _own(monkeypatch, _cache(moved=None))
    out = await _measure(_Conn(reading_age=None))
    assert out["stale"] is True and out["source"] == "ENTRY_TIME_MEASURE"
    assert out["p"] == 0.21
    assert out["feed_refusal"] == F.R_NO_CHANGE_TIME


async def test_no_owner_in_this_process_stays_stale_and_reads_nothing(
        monkeypatch):
    monkeypatch.setitem(R._STATE, "owner", None)
    conn = _Conn()
    out = await _measure(conn)
    assert out["stale"] is True and out["source"] == "PINNACLE_ONLY_LATEST"
    assert out["feed_refusal"] == "FEED_OWNERSHIP_NOT_HELD"
    assert not any("us_premap" in q for q in conn.queries)
    # and without a feed nothing changes at all
    plain = await PB.xavier_measure(_Conn(), CTX, pos=POS)
    assert "feed_refusal" not in plain
    assert {k: v for k, v in out.items()
            if k not in ("feed_refusal", "feed_detail")} == plain


async def test_an_unmatched_market_stays_stale_with_the_census_reason(
        monkeypatch):
    _own(monkeypatch, _cache(home="Santos", away="Corinthians"))
    out = await _measure(_Conn())
    assert out["stale"] is True
    assert out["feed_refusal"] == C.S_NO_FEED_EVENT
    _own(monkeypatch, _cache(), sport_ids=(6,))
    out = await _measure(_Conn())
    assert out["stale"] is True
    assert out["feed_refusal"] == C.S_OUT_OF_SCOPE
    _own(monkeypatch, _cache())
    out = await _measure(_Conn(payout="Gremio"))
    assert out["stale"] is True
    assert out["feed_refusal"] == R.R_OUTCOME_UNMAPPED


def test_the_held_match_is_the_census_match():
    """One matcher: the held read's contract_match gives the same state the
    census counts for that contract, from the same structured team records
    (identity is the two venue team names, never the display title)."""
    view = C.feed_event_view(_cache())
    for teams, want in ((("Flamengo", "Palmeiras"), C.S_SUPPORTED),
                        (("Santos", "Corinthians"), C.S_NO_FEED_EVENT),
                        (("Flamengo",), "STRUCTURED_PARTICIPANTS_NOT_TWO")):
        rows = [{"event_title": " vs ".join(teams), "kind": "moneyline",
                 "line": None, "sports_type": "soccer_team_full_time_winner",
                 "game_start": START, "event_slug": "ev-" + teams[0],
                 "team_name": t, "team_league": None} for t in teams]
        st, _, _ = C.contract_match(rows[0], rows, view,
                                    subscribed_sports={1}, synced=True)
        got = C.census(rows, view, subscribed_sports={1}, synced=True,
                       now=AT)
        assert st == want and got["states"] == {want: len(rows)}


# ── GUARD: the stale-price guard is unchanged ────────────────────────
def _ctx(a, now):
    return {"account_id": a["account_id"], "session_id": a["session_id"],
            "config": a["config"], "now": now, "clock": lambda: now,
            "session": {"session_id": a["session_id"], "config": a["config"],
                        "reporting_tz": "America/New_York"},
            "fee_fn": H.zero_fee, "deadline": 1e18}


@pg
@pytest.mark.parametrize("age,expect", [(31.0, "HOLD"), (5.0, "EXIT")])
async def test_a_feed_refused_stale_measure_still_blocks_a_discretionary_exit(
        monkeypatch, age, expect):
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "feedstale")
        s = "%s:m" % a["account_id"]
        g = "paper_g_%s_feedstale" % a["account_id"][-10:]
        e = H.order(a, key="e", qty=100, limit=0.40, slug=s, at=H.T0,
                    group_id=g)
        ge = await L.submit_order(conn, e, fee_fn=H.zero_fee, now=H.T0)
        await H.observe(conn, s, H.T0 + 3, offers=[(0.40, 100)],
                        bids=[(0.38, 100)])
        await SIM.simulate_order(conn, ge["order"]["order_id"], now=H.T0 + 4,
                                 fee_fn=H.zero_fee)
        await PX.step_handoff(conn, _ctx(a, H.T0 + 5))
        # a book whose bid makes selling worth more than holding at p ~ 0.2
        await H.observe(conn, s, H.T0 + 6, offers=[(0.82, 100)],
                        bids=[(0.80, 100)])
        at = H.T0 + 7
        _own(monkeypatch, _cache(changed_at=at - age))
        seen = {}

        async def measure(conn_, ctx_, *, pos, levels_buy):
            ctx2 = dict(CTX, now=at)
            seen["m"] = await PB.xavier_measure(_Conn(), ctx2, pos=pos,
                                                feed=PX._held_feed)
            return seen["m"]
        monkeypatch.setattr(PX, "_measure", measure)
        await PX.review_group(conn, _ctx(a, at), g,
                              trigger="SCHEDULED_BACKSTOP")
        rv = await conn.fetchrow("SELECT * FROM paper_xavier_reviews WHERE "
                                 " group_id=$1 ORDER BY reviewed_at DESC "
                                 " LIMIT 1", g)
        assert rv["recommendation"] == expect, seen["m"]
        sales = await conn.fetchval(
            "SELECT count(*) FROM paper_orders WHERE group_id=$1 AND role "
            " IN ('EXIT', 'REDUCE')", g)
        alts = H.j(rv["alternatives"])
        blocked = [x for x in alts["not_rankable"]
                   if x.get("blocker") == PX.B_STALE_MEASURE]
        if expect == "EXIT":
            assert seen["m"]["source"] == PB.SOURCE_FEED_CURRENT
            assert sales == 1 and not blocked
        else:
            assert seen["m"]["stale"] is True
            assert seen["m"]["feed_refusal"] == F.R_STALE
            assert sales == 0, "no sale on a stale measure"
            assert blocked and {x["action"] for x in blocked} <= {
                "EXIT", "REDUCE"}
    finally:
        await conn.close()
