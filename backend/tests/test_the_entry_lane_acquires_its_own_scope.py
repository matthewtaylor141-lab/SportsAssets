"""THE ENTRY LANE ACQUIRES THE SCOPE EVIDENCE IT NEEDS, OR REFUSES BY NAME.

THE DEFECT THESE TESTS PIN, MEASURED IN PRODUCTION 2026-09-25.

Fifteen of fifteen candidate rows carried:

    settlement UNKNOWN   ctx -   phase -   fmt -   rules_read true

The VENUE side was read. OUR side was missing: with no `fixture_metadata`
row there is no competition phase, no game format and no reported event
state, so `book_context_for` refuses, `book_terms()` returns {} and every
condition reads as book-silent. The verdict was UNKNOWN for want of our own
evidence -- not a parser fault, and not the payoff conflict.

The row existed only because an admin route could be invoked by hand for one
acceptance position. The lane now acquires it for the candidates it is
actually evaluating, through the same parser and into the same row the
manager reads.

WHAT THESE TESTS REFUSE TO LET THROUGH.

  * A context inferred from a scheduled start. The league must have
    REPORTED a state.
  * A fixture selected without both team names and the official date.
  * An unreachable league host reading as "no fixture exists".
  * An unbounded number of schedule fetches per cycle.
"""

from __future__ import annotations

import asyncio

from sportsassets import bettor_fixture_metadata as FM
from sportsassets import bettor_fixture_store as FS
from sportsassets import bettor_settlement_terms as ST
from sportsassets.workers import ext_pinnacle_loop as LOOP

CID = "0x878147f1f774bd204499ac1ada93be86b522f2c8e128f8cdf138b59f1bc5234a"

#: The league's shape, as the shape probe records it.
GAME = {
    "gamePk": 824950, "gameType": "R", "scheduledInnings": 9,
    "doubleHeader": "N", "gameNumber": 1,
    "officialDate": "2026-09-25",
    "gameDate": "2026-09-26T01:40:00Z",
    "status": {"detailedState": "Scheduled", "abstractGameState": "Preview"},
    "teams": {"away": {"team": {"name": "Houston Astros"}},
              "home": {"team": {"name": "Athletics"}}},
}
PAYLOAD = {"totalGames": 1, "dates": [{"games": [GAME]}]}


class _Conn:
    """The two calls the store makes, and a row that starts absent."""

    def __init__(self, row=None, fail=False):
        self.row = row
        self.fail = fail
        self.writes = []

    async def fetchrow(self, sql, *args):
        if self.fail:
            raise RuntimeError("connection lost")
        return self.row

    async def execute(self, sql, *args):
        self.writes.append(args)
        # The row the lane re-reads is the row it wrote.
        self.row = {
            "phase": args[1], "phase_uncovered": args[2],
            "game_format": args[3], "scheduled_innings": args[4],
            "play_has_begun": args[5], "event_state_raw": args[6],
            "abstract_state": args[7], "start_evidence": args[9],
            "terminal_hint": args[10], "game_pk": args[11],
            "official_date": args[12], "home_team": args[13],
            "away_team": args[14], "source": args[17],
            "source_url": args[18], "reader_version": args[20],
            "retrieved_at": args[19], "retrieved_at_epoch": 1790359200.0,
            "actual_start_at": args[8],
        }


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        coro)


def _fetcher(payload=PAYLOAD, ok=True, error="URLError: refused"):
    calls = []

    def f(date_str):
        calls.append(date_str)
        if not ok:
            return {"ok": False, "url": FM.SOURCE_URL % date_str,
                    "error": error}
        return {"ok": True, "url": FM.SOURCE_URL % date_str,
                "payload": payload}

    f.calls = calls
    return f


def test_an_absent_row_is_acquired_and_persisted_with_its_provenance():
    conn = _Conn(row=None)
    f = _fetcher()
    got = _run(LOOP.acquire_fixture_scope(
        conn, condition_id=CID, home="Athletics", away="Houston Astros",
        commence_iso="2026-09-26T01:40:00Z", now=1790359200.0,
        cache={}, fetcher=f))
    assert got["acquisition"]["attempted"] is True
    assert got["acquisition"]["write"]["persisted"] is True
    assert got["read"] is True
    assert got["phase"] == ST.PHASE_REGULAR
    assert got["game_format"] == ST.FMT_NINE
    assert got["game_pk"] == 824950
    # THE PROVENANCE TRAVELS: source, URL and the retrieval instant.
    assert got["source"] == FM.SOURCE
    assert "statsapi" in got["source_url"]
    assert got["retrieved_at"].endswith("Z")
    assert got["reader_version"] == FM.VERSION
    # AND THE LANE TRIED THE UTC DATE FIRST, then the day before -- a 01:40Z
    # first pitch is the previous official date, which is the one that hits.
    assert f.calls[:2] == ["2026-09-26", "2026-09-25"]


def test_the_reported_state_decides_the_context_and_a_schedule_never_does():
    """`Scheduled` reports play NOT begun. A quote observed at or before that
    report is pre-game; one observed after it is NOT covered, and stays
    unknown rather than being placed by the clock."""
    # THE PARSED GAME, not the raw payload: `evidence_from` reads the
    # reader's own vocabulary, and handing it the league's raw keys is how a
    # declared state ('scheduled' -> not begun) reads as undeclared.
    parsed = FM.parse_games(PAYLOAD)
    assert parsed["ok"], parsed
    ev = FM.evidence_from(parsed["games"][0],
                          retrieved_at="2026-09-25T18:00:00Z",
                          source_url="u", condition_id=CID)
    assert ev["play_has_begun"] is False
    before = FM.context_for(ev, observed_at=1790359200.0 - 3600)
    after = FM.context_for(ev, observed_at=1790359200.0 + 3600)
    assert before["context"] == ST.CTX_PRE_GAME
    assert after["context"] is None
    assert after["refusal"] == ST.R_CONTEXT_UNKNOWN
    # AND WITH NO REPORTED STATE AT ALL, the scheduled start establishes
    # nothing -- the refusal names exactly that.
    none_state = FM.context_for({"play_has_begun": None},
                                observed_at=1790359200.0)
    assert none_state["context"] is None
    assert none_state["refusal"] in (ST.R_CONTEXT_UNKNOWN,
                                    ST.R_SCHEDULED_ONLY)


def test_an_unreachable_league_host_is_not_an_absent_fixture():
    conn = _Conn(row=None)
    f = _fetcher(ok=False)
    got = _run(LOOP.acquire_fixture_scope(
        conn, condition_id=CID, home="Athletics", away="Houston Astros",
        commence_iso="2026-09-26T01:40:00Z", now=1790359200.0,
        cache={}, fetcher=f))
    assert got["read"] is False
    acq = got["acquisition"]
    assert acq["attempted"] is True
    assert acq["fetch_errors"], acq
    assert acq["refusal"] == FM.R_NO_MATCH or acq.get("fetch_errors")
    assert conn.writes == []


def test_a_missing_team_pair_refuses_before_any_fetch():
    conn = _Conn(row=None)
    f = _fetcher()
    got = _run(LOOP.acquire_fixture_scope(
        conn, condition_id=CID, home="", away="Houston Astros",
        commence_iso="2026-09-26T01:40:00Z", now=1790359200.0,
        cache={}, fetcher=f))
    assert got["acquisition"]["refusal"] == "FIXTURE_BINDING_INCOMPLETE"
    assert f.calls == []
    assert conn.writes == []


def test_a_recent_row_with_a_reported_state_is_not_refetched():
    conn = _Conn(row={"read": True, "play_has_begun": False,
                      "retrieved_at_epoch": 1790359100.0,
                      "phase": ST.PHASE_REGULAR,
                      "game_format": ST.FMT_NINE})
    f = _fetcher()
    got = _run(LOOP.acquire_fixture_scope(
        conn, condition_id=CID, home="Athletics", away="Houston Astros",
        commence_iso="2026-09-26T01:40:00Z", now=1790359200.0,
        cache={}, fetcher=f))
    assert got["acquisition"]["attempted"] is False
    assert f.calls == []


def test_a_stale_row_is_refetched_because_an_event_state_moves():
    old = {"read": True, "play_has_begun": False,
           "retrieved_at_epoch": 1790359200.0 - (FS.MAX_ROW_AGE_S + 60)}
    need = FS.needs_acquisition(old, now=1790359200.0)
    assert need["acquire"] is True
    assert "moves" in need["why"]
    fresh = FS.needs_acquisition(
        {"read": True, "play_has_begun": True,
         "retrieved_at_epoch": 1790359200.0 - 10},
        now=1790359200.0)
    assert fresh["acquire"] is False


def test_a_failed_read_is_not_an_absent_row():
    conn = _Conn(fail=True)
    got = _run(FS.read(conn, CID))
    assert got["read"] is False
    assert got["refusal"] == FS.R_READ_FAILED
    assert "not\nevidence" in got["why"].replace(" ", "\n") or \
        "not evidence" in got["why"]


def test_the_fetch_budget_is_bounded_per_cycle():
    conn = _Conn(row=None)
    f = _fetcher(payload={"totalGames": 0, "dates": []})
    cache = {"2026-09-01": {"ok": True, "url": "u", "payload": PAYLOAD},
             "2026-09-02": {"ok": True, "url": "u", "payload": PAYLOAD},
             "2026-09-03": {"ok": True, "url": "u", "payload": PAYLOAD}}
    got = _run(LOOP.acquire_fixture_scope(
        conn, condition_id=CID, home="Athletics", away="Houston Astros",
        commence_iso="2026-09-26T01:40:00Z", now=1790359200.0,
        cache=cache, fetcher=f))
    assert got["acquisition"]["refusal"] == "FIXTURE_FETCH_BUDGET_SPENT"
    assert f.calls == []


def test_one_writer_one_reader_one_table():
    """The admin route and the entry lane must not hold two copies of the
    acquisition semantics -- that is how the lane ended up with a read and
    no writer."""
    import inspect

    from sportsassets.api import app as APP

    src = inspect.getsource(APP)
    assert "INSERT INTO fixture_metadata" not in src
    assert "FSTORE.upsert" in src
    loop_src = inspect.getsource(LOOP)
    assert "INSERT INTO fixture_metadata" not in loop_src
    assert "fstore.upsert" in loop_src
    assert "fstore.read" in loop_src


def test_the_two_clocks_are_separated_and_the_rule_is_unchanged():
    """Provider staleness and our processing delay are different faults with
    different remedies. The 30-second bound is untouched and governs the
    sum, exactly as before."""
    q = {"observed_at": "2026-09-25T18:00:00Z", "received_at": 1790359215.0}
    f = LOOP._entry_freshness(q, {"venue_ts": 1790359220.0, "age_s": 1.0},
                              1790359230.0)
    assert f["pinnacle_age_s"] == 30.0
    assert f["pinnacle_provider_lag_s"] == 15.0
    assert f["pinnacle_our_processing_s"] == 15.0
    assert (f["pinnacle_provider_lag_s"] + f["pinnacle_our_processing_s"]
            == f["pinnacle_age_s"])
    assert f["pinnacle_limit_s"] == LOOP.PINNACLE_MAX_AGE_S == 30.0
    assert f["fresh"] is True
    # One second more and it refuses, as it did before the split.
    f2 = LOOP._entry_freshness(q, {"venue_ts": 1790359220.0, "age_s": 1.0},
                               1790359231.0)
    assert f2["fresh"] is False


def test_the_store_says_what_it_will_not_do():
    d = FS.describe()
    assert d["writes"].startswith("ONE fixture_metadata row")
    assert any("scheduled start" in x for x in d["will_not"])
    assert any("without both team names" in x for x in d["will_not"])
    assert "workers.ext_pinnacle_loop (entry)" in d["consumers"]
    assert "workers.rn1x_shadow (management)" in d["consumers"]

# ── and the MANAGER acquires for its own subject ─────────────────────
#
# THE HOLE A SHARED READER LEAVES. Entry acquires for the candidates it
# evaluates. A position held outside that set -- the acceptance position --
# had nothing acquiring its fixture, so a shared reader would have read the
# same absent row forever. That is why
# SETTLEMENT_COMPATIBILITY_ESTABLISHED was the one missing acceptance
# requirement on 92 of Houston's first 110 decisions.

def test_the_manager_acquires_for_its_own_subject_not_only_for_candidates():
    import inspect

    from sportsassets.workers import rn1x_shadow as MGR

    src = inspect.getsource(MGR)
    assert "acquire_fixture_scope as _acq_scope" in src
    # BOUND ON THE PROVIDER'S OWN TEAM NAMES for this event, like entry.
    assert 'home=quote.get("home")' in src
    assert 'commence_iso=quote.get("commence_time")' in src
    # THE READ BELOW IT IS UNCHANGED and still the authority on what is
    # persisted -- acquisition only makes sure there is something to read.
    assert "conn.fetchrow(FIXTURE_META_SQL, condition_id)" in src
    # AND A FAULT IN IT IS NOT AN ABSENT FIXTURE.
    assert "AN ACQUISITION FAULT IS NOT AN ABSENT FIXTURE" in src


def test_the_managers_acquisition_is_reported_on_its_own_key():
    """So a reader can tell an acquired row from a row that was merely
    found, on the management side too."""
    import inspect

    from sportsassets.workers import rn1x_shadow as MGR

    src = inspect.getsource(MGR)
    assert 'out["fixture_acquisition"] = fmeta_acq' in src
    assert 'dict(fmeta, read=True, acquisition=fmeta_acq)' in src
