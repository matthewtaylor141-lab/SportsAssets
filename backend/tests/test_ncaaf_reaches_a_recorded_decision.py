"""NCAAF REACHES A RECORDED DECISION; IT NEVER DISAPPEARS (cand22).

THE PRODUCTION LOSS (2026-10-03, a college-football Saturday; research-sql
cand22_ncaaf_stages.sql / cand22_ncaaf_names.sql on claude/command-center).
The venue listed 107 `cfb` events with a `football_team_full_game_winner`
contract on the America/New_York day. The scheduled cycle requested
`baseball_mlb`, `soccer_uefa_nations_league` and `soccer_brazil_serie_b` (one
of four metered slots unused), wrote 0 football valuations and 0 `cfb` paper
decisions, and recorded no refusal saying why: its competition set was derived
from the venue's SOCCER board alone, and the venue-native identity knew no
football winner type. NCAAF was absent, not refused.

THIS FILE DRIVES THE REAL PATH, end to end, against a migrated database:

    venue board (us_premap, production row shapes)
      -> competition selection (provider catalogue confirms the key)
      -> provider event (odds-API NCAAF payload shape) -> Pinnacle h2h
      -> fixture confirmation against the venue's own titles
      -> venue-native identity (school + nickname, league token `cfb`)
      -> venue book read (production refusal: book currency not established)
      -> settlement attestation -> valuation persisted
      -> Derek's paper decision, recorded in the cycle

and asserts every NCAAF provider event ends in a RECORDED, NAMED outcome:
a paper decision (ENTER, or REFUSE with its refusal) for each identity, and
an event-ledger row with a named refusal for anything that stops earlier.

WHAT IS SUBSTITUTED, AND ONLY AT TRANSPORT BOUNDARIES: the provider's
catalogue and odds responses, the venue's book and listing reads, and the
paper market-data transport. The us_premap rows are the production rows of
2026-10-03 (Ohio State vs. Iowa and Ohio vs. Kent State, BOTH 15:30 ET --
the pair a school-only match confuses), re-dated to a kickoff ahead of the
test clock. Prices and the book are SYNTHETIC. No gate, threshold or rule is
changed by the test: settlement for football is not established and the
book currency is not established, exactly as in production, and the
decisions record those refusals by name.
"""
from __future__ import annotations

import datetime as _dt
import time

import pytest

from sportsassets import bettor_paper_session as S
from sportsassets import bettor_venue_native_identity as V
from sportsassets.agents import paper_derek as PD
from sportsassets.agents import paper_runtime as PR
from sportsassets.agents import runtime as RT
from sportsassets.workers import ext_pinnacle_loop as loop

from tests import paper_harness as H
from tests import paper_live_fixture as PL

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")

NCAAF = "americanfootball_ncaaf"
LONG, SHORT = "ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT"


def _iso(epoch: float) -> str:
    return _dt.datetime.fromtimestamp(epoch, tz=_dt.timezone.utc) \
        .strftime("%Y-%m-%dT%H:%M:%SZ")


def _day(epoch: float) -> str:
    return _dt.datetime.fromtimestamp(epoch, tz=_dt.timezone.utc) \
        .strftime("%Y-%m-%d")


def _events(kickoff: float) -> list:
    """The venue's two 15:30 ET `cfb` events of 2026-10-03, row for row
    (team_name / side_norm / team_abbr / intent / line as production holds
    them), with the slug date and game_start moved to `kickoff`."""
    d = _day(kickoff)
    return [
        {"event_slug": "cfb-ohiost-iowa-%s" % d,
         "event_title": "Ohio State vs. Iowa",
         "sides": (("ohio state", "buckeyes", "ohiost", LONG),
                   ("iowa", "hawkeyes", "iowa", SHORT))},
        {"event_slug": "cfb-ohio-kentst-%s" % d,
         "event_title": "Ohio vs. Kent State",
         "sides": (("ohio", "bobcats", "ohio", LONG),
                   ("kent state", "golden flashes", "kentst", SHORT))},
    ]


async def _seed_venue(conn, kickoff: float) -> list:
    slugs = []
    for ev in _events(kickoff):
        slug = "aec-" + ev["event_slug"]
        slugs.append(slug)
        for team, nick, abbr, intent in ev["sides"]:
            await conn.execute(
                "INSERT INTO us_premap (identifier, event_slug, event_title, "
                " market_slug, question, kind, line, side_norm, intent, "
                " team_abbr, team_name, team_league, game_start, sports_type, "
                " updated_at) VALUES ($1,$2,$3,$1,$3,'side','00',$4,$5,$6,$7,"
                " 'cfb', to_timestamp($8), 'football_team_full_game_winner', "
                " now()) ON CONFLICT (identifier, side_norm) DO UPDATE SET "
                " updated_at = now(), game_start = EXCLUDED.game_start",
                slug, ev["event_slug"], ev["event_title"], nick, intent, abbr,
                team, float(kickoff))
        # A SEGMENT winner on the same event, as production lists it: it must
        # never be read as the full-game contract.
        await conn.execute(
            "INSERT INTO us_premap (identifier, event_slug, event_title, "
            " market_slug, question, kind, side_norm, intent, team_abbr, "
            " team_name, team_league, game_start, sports_type, updated_at) "
            "VALUES ($1,$2,$3,$1,$3,'side','yes',$4,$5,$6,'cfb',"
            " to_timestamp($7),'football_team_first_half_winner', now()) "
            "ON CONFLICT (identifier, side_norm) DO NOTHING",
            "atc-%s-winner-1h-%s" % (ev["event_slug"], ev["sides"][0][2]),
            ev["event_slug"], ev["event_title"], LONG, ev["sides"][0][2],
            ev["sides"][0][0], float(kickoff))
    return slugs


async def _cleanup(conn, kickoff: float) -> None:
    d = _day(kickoff)
    pat = "%%-cfb-ohio%%-%s%%" % d
    async with conn.transaction():
        await conn.execute("SET LOCAL session_replication_role = replica")
        for t in ("paper_fills", "paper_orders", "execution_intents",
                  "paper_decisions", "external_valuations"):
            try:
                await conn.execute(
                    "DELETE FROM %s WHERE us_market_slug LIKE $1" % t, pat)
            except Exception:                                  # noqa: BLE001
                pass
        await conn.execute("DELETE FROM us_premap WHERE event_slug LIKE $1",
                           "cfb-ohio%%-%s" % d)


def _provider_event(eid, home, away, kickoff, p_home, stamp):
    """The odds-API h2h shape for `americanfootball_ncaaf` (two outcomes,
    the provider's school + nickname names), Pinnacle plus one sharp book."""
    prices = [{"name": home, "price": round(1.0 / p_home, 4)},
              {"name": away, "price": round(1.0 / (1.0 - p_home), 4)}]
    return {"id": eid, "sport_key": NCAAF, "sport_title": "NCAAF",
            "commence_time": _iso(kickoff), "home_team": home,
            "away_team": away,
            "bookmakers": [
                {"key": "pinnacle", "title": "Pinnacle", "last_update": stamp,
                 "markets": [{"key": "h2h", "last_update": stamp,
                              "outcomes": prices}]},
                {"key": "betfair_ex_eu", "title": "Betfair", "last_update":
                 stamp, "markets": [{"key": "h2h", "last_update": stamp,
                                     "outcomes": prices}]}]}


def _book():
    lvl = lambda p, q: {"px": {"value": "%.2f" % p, "currency": "USD"},
                        "qty": str(q)}
    return {"marketData": {"offers": [lvl(0.55, 400), lvl(0.57, 300)],
                           "bids": [lvl(0.53, 400)],
                           "transactTime": _iso(time.time() - 2.0)}}


def _stub(monkeypatch, kickoff: float, *, with_catalogue=True):
    monkeypatch.setenv("EDGE_ODDS_API_KEY", "x" * 32)
    calls = {"odds": []}

    async def fake_catalogue(*, api_key, timeout=20.0):
        if not with_catalogue:
            return {"ok": False, "sports": [], "refusal": "TEST"}
        return {"ok": True, "status": 200, "metered": False,
                "sports": [
                    {"key": "baseball_mlb", "group": "Baseball",
                     "title": "MLB", "active": True},
                    {"key": NCAAF, "group": "American Football",
                     "title": "NCAAF", "active": True}],
                "received_at": time.time()}

    async def fake_odds(sport_key, *, api_key, timeout=20.0):
        calls["odds"].append(sport_key)
        t = time.time()
        if sport_key != NCAAF:
            return {"ok": True, "events": [], "received_at": t,
                    "credits_used": "1", "credits_remaining": "9"}
        stamp = _iso(t - 2.0)
        return {"ok": True, "received_at": t, "credits_used": "2",
                "credits_remaining": "8",
                "events": [
                    _provider_event("ncaaf-osu-iowa", "Ohio State Buckeyes",
                                    "Iowa Hawkeyes", kickoff, 0.78, stamp),
                    _provider_event("ncaaf-ohio-kent", "Ohio Bobcats",
                                    "Kent State Golden Flashes", kickoff,
                                    0.66, stamp),
                    # Not listed by the venue: must stop by NAME, not vanish.
                    _provider_event("ncaaf-ulm-sala", "UL Monroe Warhawks",
                                    "South Alabama Jaguars", kickoff + 3600,
                                    0.40, stamp)]}

    monkeypatch.setattr(loop, "fetch_sport_catalogue", fake_catalogue)
    monkeypatch.setattr(loop, "fetch_odds", fake_odds)
    monkeypatch.setattr(loop, "_read_book_blocking",
                        lambda _slug, **_k: _book())
    # THE VENUE'S OWN cfb LISTING PROSE, as captured from the public gateway
    # (tests/fixtures/pmus_cfb_listing_2026_10_03.json, cand22): overtime
    # included; postponed/suspended beyond two weeks settles to the last fair
    # market price -- which the book's void rule contradicts.
    import json as _json
    import pathlib as _pl
    _fx = _json.loads((_pl.Path(__file__).parent / "fixtures" /
                       "pmus_cfb_listing_2026_10_03.json").read_text())
    _prose = _fx["markets"][0]["description"]
    monkeypatch.setattr(loop, "_read_venue_rules_blocking",
                        lambda slug, **_k: {
                            "ok": True, "slug": slug,
                            "rules_text": _prose,
                            "rules_field": "description",
                            "tick_size": "0.005",
                            "tick_field": "orderPriceMinTickSize",
                            "read_at": time.time(), "from_cache": False,
                            "source": "pmus:/markets?slug=<slug>:rules_text"})
    loop.rules_cache_reset()
    return calls


def _wire_paper(monkeypatch, acct, transport):
    monkeypatch.setattr(PR, "DEFAULT_ACCOUNT_ID", acct["account_id"])
    monkeypatch.setitem(PR._CLIENT, "client", PL.client(transport))
    monkeypatch.setattr(RT, "paper_pass_hook",
                        lambda **kw: {"scheduled": False,
                                      "why": "RECORDED_BY_THE_TEST"})
    PD._CONTEXT_CACHE.clear()


# ── pure: the selection and the identity, on the measured shapes ─────────

def test_the_football_board_makes_ncaaf_a_candidate_within_the_budget():
    board = {"board": [("cfb", 92), ("nfl", 15)],
             "titles": {"cfb": ["Ohio State vs. Iowa"],
                        "nfl": ["ARI Cardinals vs. NY Giants"]},
             "title_days": {"cfb": {"Ohio State vs. Iowa": "2026-10-03"}}}
    fb = loop.football_candidates(board)
    # cand24: nfl is a candidate too, under its OWN key
    assert [(c["key"], c["family"], c["our_token"]) for c in fb] == \
        [(NCAAF, "football", "cfb"),
         ("americanfootball_nfl", "football", "nfl")]
    soccer = loop.candidates_from_board([("unl", 39), ("brb", 6)])
    merged = loop.merge_candidates(soccer, fb)
    assert [c["our_token"] for c in merged] == ["cfb", "unl", "nfl", "brb"]
    cat = {"ok": True, "sports": [{"key": k, "active": True} for k in (
        "baseball_mlb", NCAAF, "soccer_uefa_nations_league",
        "soccer_brazil_serie_b")]}
    sel = loop.select_sports(cat, candidates=merged)
    assert sel["sports"] == [("baseball_mlb", "baseball"), (NCAAF, "football"),
                             ("soccer_uefa_nations_league", "soccer"),
                             ("soccer_brazil_serie_b", "soccer")]
    # a key the provider's catalogue does not list is refused by name and
    # consumes no budget slot
    assert {"key": "americanfootball_nfl", "our_token": "nfl",
            "refusal": loop.R_PROVIDER_DOES_NOT_LIST} in [
        {k: r[k] for k in ("key", "our_token", "refusal")}
        for r in sel["rejected"]]
    # R30A: THE FIRST-COME TRUNCATION IS GONE (it starved NCAAF in 137 of 153
    # cycles with a venue cfb event in the next 24 h, research-sql run
    # 37233454453). select_sports confirms and drops nothing; which confirmed
    # competitions get the unchanged four calls is collector_coverage.plan's
    # decision, earliest deadline first, receipted. The pin on the budget's
    # VALUE stays: it is still four metered calls a cycle.
    assert loop.MAX_METERED_SPORTS_PER_CYCLE == 4
    assert sel["budget"] is None
    assert sel["budget_dropped"] == []
    # THE PRODUCTION STATE BEFORE THE FIX: the soccer board alone never
    # produced the key, and nothing recorded its absence.
    before = loop.select_sports(cat, candidates=soccer)
    assert NCAAF not in [k for k, _ in before["sports"]]
    assert NCAAF not in [r["key"] for r in before["rejected"]]
    assert loop.venue_league_tokens(NCAAF) == ("cfb",)
    assert NCAAF in loop.provider_keys_for_family("football")


def test_a_board_read_failure_requests_no_football():
    class _Broken:
        async def fetch(self, *a, **k):
            raise RuntimeError("down")
    import asyncio
    got = asyncio.run(loop.venue_football_competitions(_Broken()))
    assert got["board"] == [] and got["evidence"] == "READ_FAILED"
    assert loop.football_candidates(got) == []


def test_the_fixture_confirmation_matches_the_venue_cfb_titles():
    conf = loop.confirm_mapping_by_fixtures(
        provider_events=[{"home_team": "Ohio State Buckeyes",
                          "away_team": "Iowa Hawkeyes",
                          "commence_time": "2026-10-03T19:30:00Z"}],
        venue_event_titles=["Ohio State vs. Iowa"],
        venue_event_days={"Ohio State vs. Iowa": "2026-10-03"})
    assert conf["ok"], conf


def test_school_plus_nickname_separates_ohio_from_ohio_state():
    t = _dt.datetime(2026, 10, 3, 19, 30, tzinfo=_dt.timezone.utc)
    rows = []
    for ev in _events(t.timestamp()):
        for team, nick, abbr, intent in ev["sides"]:
            rows.append({"market_slug": "aec-" + ev["event_slug"],
                         "intent": intent, "event_slug": ev["event_slug"],
                         "event_title": ev["event_title"],
                         "question": ev["event_title"], "kind": "side",
                         "sports_type": "football_team_full_game_winner",
                         "team_abbr": abbr, "team_name": team,
                         "side_norm": nick, "line": "00", "game_start": t})
    cases = {("Ohio State Buckeyes", "Iowa Hawkeyes"):
             ("aec-cfb-ohiost-iowa-2026-10-03", LONG),
             ("Iowa Hawkeyes", "Ohio State Buckeyes"):
             ("aec-cfb-ohiost-iowa-2026-10-03", SHORT),
             ("Ohio Bobcats", "Kent State Golden Flashes"):
             ("aec-cfb-ohio-kentst-2026-10-03", LONG),
             ("Kent State Golden Flashes", "Ohio Bobcats"):
             ("aec-cfb-ohio-kentst-2026-10-03", SHORT)}
    for (home, away), (slug, intent) in cases.items():
        m = V.match_event(home=home, away=away,
                          commence_epoch=t.timestamp() + 300,
                          family="football", rows=rows, competition=NCAAF,
                          league_tokens=("cfb",))
        assert m["ok"], m
        assert (m["us_market_slug"], m["intent"]) == (slug, intent)
        # the school-only reading would have named a partial match here
        assert m["partial_matches"] == [], m["partial_matches"]
    # an nfl-only league token never reaches a cfb event
    m = V.match_event(home="Ohio Bobcats", away="Kent State Golden Flashes",
                      commence_epoch=t.timestamp(), family="football",
                      rows=rows, competition=NCAAF, league_tokens=("nfl",))
    assert not m["ok"] and m["refusal"] == V.R_NO_EVENT
    # baseball keeps reading team_name alone
    assert V.participant_name({"team_name": "new york", "side_norm": "yankees"},
                              "baseball") == "new york"


# ── the scheduled cycle, end to end, into Derek's recorded decision ─────

@pg
async def test_ncaaf_reaches_a_recorded_paper_decision_never_silence(
        monkeypatch):
    conn = await H.connect()
    t0 = time.time()
    kickoff = t0 + 3 * 3600.0
    try:
        await _cleanup(conn, kickoff)
        await conn.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1,'true') "
            "ON CONFLICT (key) DO UPDATE SET value = 'true'",
            loop.CONTROL_KEY)
        slugs = await _seed_venue(conn, kickoff)
        acct = await PL.new_account(conn, "ncaaf", now=t0)
        tr = PL.Transport(t0)
        for s in slugs:
            tr.set(s, offers=[(0.55, 400)], bids=[(0.53, 400)])
        _wire_paper(monkeypatch, acct, tr)
        monkeypatch.setenv(S.ENV_FLAG, "on")
        calls = _stub(monkeypatch, kickoff)

        out = await loop.cycle(conn)

        # 1 · the competition was requested (it never was in production)
        requested = [k for k, _ in out["sports_selection"]["sports"]]
        assert NCAAF in requested, out["sports_selection"]
        assert NCAAF in calls["odds"]
        # iterated after the sports the lane already valued (their bounds and
        # paced reads come first, as before)
        assert list(out["funnel_by_provider_sport"])[-1] == NCAAF
        step = out["funnel_by_provider_sport"][NCAAF]
        assert step["family"] == "football"
        assert step["provider_events"] == 3 and step["with_pinnacle_h2h"] == 3
        assert step["mapping_confirmation"]["ok"], step["mapping_confirmation"]
        assert step["mapped_by_venue_native"] == 2
        assert step["identity_resolved"] == 2, step

        # 2 · each identity became a persisted valuation on THE right contract
        d = _day(kickoff)
        vals = {r["us_market_slug"]: dict(r) for r in await conn.fetch(
            "SELECT * FROM external_valuations WHERE us_market_slug = "
            " ANY($1::text[]) AND decided_at >= to_timestamp($2)",
            slugs, t0 - 1)}
        assert set(vals) == set(slugs), (set(vals), step)
        osu = vals["aec-cfb-ohiost-iowa-%s" % d]
        ohio = vals["aec-cfb-ohio-kentst-%s" % d]
        assert (osu["payout_event"], osu["buy_intent"]) == \
            ("Ohio State Buckeyes", LONG)
        assert (ohio["payout_event"], ohio["buy_intent"]) == \
            ("Ohio Bobcats", LONG)
        for v in vals.values():
            assert v["sport_family"] == "football"
            # production's state: the book currency is not established, so
            # the valuation is sealed for calibration and refuses by name --
            # and the football gates that are not established yet (the
            # measured de-vig set, the overtime / void rules) refuse BY NAME
            # too. Nothing here is waived to reach a decision.
            assert v["record_purpose"] == "CALIBRATION_ONLY", v["refusals"]
            assert v["admissible"] is False
            for code in (loop.R_BOOK_CURRENCY_NOT_ESTABLISHED,
                         "MARKET_NOT_IN_SUPPORTED_SET",
                         # the captured documents CONFLICT on abandonment
                         "VOID_ABANDONMENT_RULE_CONFLICTS_WITH_BOOK_RULE"):
                assert code in v["refusals"], (code, v["refusals"])
            # ...and AGREE on overtime, so that rule is established
            assert "OVERTIME_RULE_NOT_ESTABLISHED" not in v["refusals"]
            # THE MEASUREMENT ACCRUES: the book's two-outcome set is on the
            # row although no probability is derived from it
            assert v["probability"] is None
            assert len(H.j(v["raw_odds"])) == 2 and v["outcomes_priced"] == 2

        # 3 · Derek recorded a decision for every one -- ENTER or NAMED REFUSE
        decs = [dict(r) for r in await conn.fetch(
            "SELECT * FROM paper_decisions WHERE session_id = $1 AND "
            " valuation_id = ANY($2::bigint[])", acct["session_id"],
            [v["id"] for v in vals.values()])]
        derek = [x for x in decs if x["strategy"] == PD.STRATEGY]
        assert sorted(x["valuation_id"] for x in derek) == \
            sorted(v["id"] for v in vals.values()), decs
        for x in decs:
            assert x["verdict"] in ("ENTER", "REFUSE"), x
            if x["verdict"] == "REFUSE":
                assert x["refusal"], x
            assert x["us_market_slug"] in slugs
            assert H.j(x["label"])["competition"] == "CFB"
        print("NCAAF_PAPER_DECISIONS",
              sorted((x["strategy"], x["us_market_slug"], x["verdict"],
                      x["refusal"]) for x in decs))

        # 4 · NO SILENT DROP: every provider event has exactly one ledger row
        # with a named outcome, and the ledger reconciles to the fetch
        rows = [r for r in out["event_ledger"] if r["sport_key"] == NCAAF]
        assert sorted(r["provider_event_id"] for r in rows) == \
            ["ncaaf-ohio-kent", "ncaaf-osu-iowa", "ncaaf-ulm-sala"]
        for r in rows:
            assert r["outcome"] and r["first_refusal"], r
        unlisted = next(r for r in rows
                        if r["provider_event_id"] == "ncaaf-ulm-sala")
        assert V.R_NO_EVENT in unlisted["codes"], unlisted
        per = out["candidate_outcomes"]["per_sport"][NCAAF]
        assert per == {"provider_events": 3, "rows": 3, "reconciles": True}
    finally:
        await PL.drop_today_run(conn, t0)
        await _cleanup(conn, kickoff)
        await conn.close()
