"""A SIMULATION OF A FIXTURE IS NOT THE FIXTURE.

THE DEFECT THESE TESTS PIN, AND IT WAS NEARLY MINE TO INTRODUCE. The live cycle
on build c3d0cfc mapped 0 of 20 EPL provider events onto the US venue, and I
went looking for the resolver bug. The authorized read (research-sql run 258)
says there is no resolver bug: the US venue lists ZERO contracts carrying the
token `epl`. What it lists is 420 rows across 70 events titled

    atc-ebfpl-ars-che-2026-09-27-dh3-ars   "eBattles: Arsenal vs. Chelsea"

classified `efootball_team_full_time_winner` -- video game matches between real
club names, 538 of them, kicking off every few minutes.

The repair I was about to write was a league alias `epl -> ebfpl`. It would
have bound a Pinnacle probability for the real Arsenal-Chelsea fixture to a
contract settling on a simulation of it, and EVERY OTHER CHECK THE LANE MAKES
WOULD HAVE PASSED: the clubs match, the date matches, the market type matches,
the money line exists, the rails are satisfied. A losing position with a
correct-looking audit trail.

So these tests assert the guard exists at the place the lane actually resolves
-- `resolve_venue_identity`, not the diagnostic route that already had the
concept -- and that it refuses on the venue's OWN words rather than on any
pattern in a league token.
"""

import pytest

from sportsassets import bettor_venue_realism as vreal
from sportsassets.workers import ext_pinnacle_loop as loop


# ── the venue's own classification ───────────────────────────────────

def test_the_efootball_classification_is_refused_by_name():
    """The measured case, verbatim from the production catalogue."""
    got = vreal.classify({
        "market_slug": "atc-ebfpl-ars-che-2026-09-27-dh3-ars",
        "event_slug": "ebfpl-ars-che-2026-09-27",
        "event_title": "eBattles: Arsenal vs. Chelsea",
        "question": "Will Arsenal win?",
        "sports_type": "efootball_team_full_time_winner"})
    assert got["verdict"] == vreal.SIMULATED
    assert got["refusal"] == vreal.R_SIMULATED_NOT_THE_REAL_FIXTURE
    # THE EVIDENCE IS THE PUBLISHER'S, and the field is named so an operator
    # can check the claim against the catalogue rather than trust the verdict.
    assert got["evidence"]["field"] == "sports_type"
    assert got["evidence"]["matched"] == "efootball_"


def test_the_prose_is_read_even_when_the_classification_looks_real():
    """A row labelled real soccer whose TITLE says eBattles is a simulation.

    This is the case a sports_type-only guard misses, and it is not
    hypothetical: the venue's classification and its prose are written by
    different parts of its pipeline and they are not always consistent.
    """
    got = vreal.classify({
        "market_slug": "atc-ebfpl-ars-liv-2026-09-27-dh2-ars",
        "event_title": "eBattles: Arsenal vs. Liverpool",
        "question": "Will Arsenal win?",
        "sports_type": "soccer_team_full_time_winner"})
    assert got["verdict"] == vreal.SIMULATED
    assert got["evidence"]["field"] == "event_title"
    assert got["evidence"]["matched"] == "ebattles"


def test_a_real_soccer_row_passes():
    got = vreal.classify({
        "market_slug": "atc-afcq-bdi-dza-2026-09-29-dza",
        "event_title": "Burundi vs. Algeria",
        "question": "Will Algeria win?",
        "sports_type": "soccer_team_full_time_winner"})
    assert got["verdict"] == vreal.REAL
    assert got["refusal"] is None


# ── the two ways realism can fail to be established ──────────────────

def test_an_absent_catalogue_row_is_unknown_not_real():
    got = vreal.classify(None)
    assert got["verdict"] == vreal.UNKNOWN
    assert got["refusal"] == vreal.R_REALISM_NOT_ESTABLISHED
    assert not vreal.is_real(None)


def test_a_row_with_neither_classification_nor_prose_is_unknown():
    got = vreal.classify({"market_slug": "", "event_title": "",
                          "question": "", "sports_type": None})
    assert got["verdict"] == vreal.UNKNOWN
    assert got["refusal"] == vreal.R_REALISM_NOT_ESTABLISHED


def test_the_verdict_is_never_decided_by_a_league_token():
    """`ebfpl` and the real `ebfsa`/`ebfwca` share a prefix.

    Inferring "simulated" from a token pattern is the guess this module
    exists to refuse, so a row whose SLUG carries `ebf` but whose own words
    say nothing about a simulation must not be refused on the token.
    """
    got = vreal.classify({
        "market_slug": "atc-ebfxx-aaa-bbb-2026-09-29-aaa",
        "event_title": "Aaa United vs. Bbb City",
        "question": "Will Aaa United win?",
        "sports_type": "soccer_team_full_time_winner"})
    assert got["verdict"] == vreal.REAL, (
        "a league token must not decide realism; only the venue's own words do")


def test_the_marker_list_has_one_definition_shared_with_the_route():
    """The concept existed in a diagnostic route and nowhere in the lane.

    One list, two consumers -- otherwise the route that reports the problem
    and the lane that must prevent it can disagree about what the venue
    called something.
    """
    from sportsassets.api import app as _app
    assert _app._SIMULATED_MARKERS is vreal.SIMULATED_MARKERS


def test_the_columns_it_reads_are_the_columns_its_query_selects():
    """Two statements in this area have already been lost to a guessed
    column name. The declared field list and the SQL must agree."""
    for col in vreal.CATALOGUE_FIELDS:
        assert col in vreal.CATALOGUE_SQL, col


# ── the guard is at the resolver, where the lane actually decides ────

class _Conn:
    """Enough of a connection for `resolve_venue_identity`'s one read."""

    def __init__(self, row, *, raise_on_read=False):
        self._row = row
        self._raise = raise_on_read
        self.queries: list = []

    async def fetchrow(self, sql, *args):
        self.queries.append((sql, args))
        if self._raise:
            raise RuntimeError("connection reset")
        return self._row

    async def fetchval(self, sql, *args):        # pragma: no cover - unused
        self.queries.append((sql, args))
        return None


def _resolver(slug, intent="ORDER_INTENT_BUY_LONG"):
    async def _resolve(conn, title, event_title, outcome, global_slug, **kw):
        return {"market_slug": slug, "intent": intent}
    return _resolve


@pytest.fixture
def _premap(monkeypatch):
    from sportsassets.workers import premap as _p
    return _p


async def test_the_lane_refuses_a_simulated_contract_before_the_intent(
        monkeypatch, _premap):
    """The order matters. Realism is settled BEFORE the intent is looked at,
    because a simulated contract with a perfectly good LONG intent is still
    the wrong event and the refusal must name the real reason."""
    monkeypatch.setattr(
        _premap, "resolve",
        _resolver("atc-ebfpl-ars-che-2026-09-27-dh3-ars"), raising=True)
    conn = _Conn({"market_slug": "atc-ebfpl-ars-che-2026-09-27-dh3-ars",
                  "event_slug": "ebfpl-ars-che-2026-09-27",
                  "event_title": "eBattles: Arsenal vs. Chelsea",
                  "question": "Will Arsenal win?",
                  "sports_type": "efootball_team_full_time_winner"})
    out = await loop.resolve_venue_identity(
        conn, market_row={"slug": "epl-ars-che-2026-09-27-ars",
                          "title": "Arsenal vs. Chelsea",
                          "event_title": "Arsenal vs. Chelsea",
                          "condition_id": "0xabc"},
        priced_outcome="Arsenal")
    assert out["ok"] is False
    assert out["refusal"] == vreal.R_SIMULATED_NOT_THE_REAL_FIXTURE
    assert out["realism"]["verdict"] == vreal.SIMULATED
    # AND THE INTENT WAS NEVER REPORTED AS THE REASON.
    assert out.get("intent") is None


async def test_the_lane_refuses_when_the_catalogue_cannot_be_read(
        monkeypatch, _premap):
    """An unreadable realism input is a refusal, exactly as an unreadable
    open book is. The venue said nothing, so nothing is assumed."""
    monkeypatch.setattr(_premap, "resolve", _resolver("some-slug"),
                        raising=True)
    conn = _Conn(None, raise_on_read=True)
    out = await loop.resolve_venue_identity(
        conn, market_row={"slug": "sea-fio-fro-2026-09-29-fio",
                          "title": "x", "event_title": "x vs y",
                          "condition_id": "0xabc"},
        priced_outcome="x")
    assert out["ok"] is False
    assert out["refusal"] == vreal.R_REALISM_NOT_ESTABLISHED
    assert out["realism"]["read_error"] == "RuntimeError"


async def test_a_real_contract_still_reaches_the_intent_check(
        monkeypatch, _premap):
    """The guard must not become a wall. A real fixture passes it and the
    lane goes on to the intent, which is what the next refusal is about."""
    monkeypatch.setattr(
        _premap, "resolve",
        _resolver("atc-afcq-bdi-dza-2026-09-29-dza",
                  intent="ORDER_INTENT_SOMETHING_ELSE"), raising=True)
    conn = _Conn({"market_slug": "atc-afcq-bdi-dza-2026-09-29-dza",
                  "event_slug": "afcq-bdi-dza-2026-09-29",
                  "event_title": "Burundi vs. Algeria",
                  "question": "Will Algeria win?",
                  "sports_type": "soccer_team_full_time_winner"})
    out = await loop.resolve_venue_identity(
        conn, market_row={"slug": "afcq-bdi-dza-2026-09-29-dza",
                          "title": "Burundi vs. Algeria",
                          "event_title": "Burundi vs. Algeria",
                          "condition_id": "0xabc"},
        priced_outcome="Algeria")
    assert out["realism"]["verdict"] == vreal.REAL
    assert out["refusal"] == loop.R_INTENT_NOT_LONG
    assert out["venue_event_key"] == "afcq-bdi-dza-2026-09-29"


async def test_one_round_trip_answers_both_questions(monkeypatch, _premap):
    """The event key and the realism verdict come from the SAME read.

    This read used to select `event_slug` alone and the realism check was
    added to it rather than beside it, because `venue_pace` is a
    process-wide SERIAL gate: a second round trip per candidate is a real
    cost paid by every candidate behind it.

    The assertion is about THIS read, not about the whole function -- a
    successful resolve goes on to read period metadata, which is a different
    question and rightly its own query. So: exactly one query selects the
    realism columns.
    """
    monkeypatch.setattr(
        _premap, "resolve",
        _resolver("atc-afcq-bdi-dza-2026-09-29-dza",
                  intent="ORDER_INTENT_SOMETHING_ELSE"), raising=True)
    conn = _Conn({"market_slug": "atc-afcq-bdi-dza-2026-09-29-dza",
                  "event_slug": "afcq-bdi-dza-2026-09-29",
                  "event_title": "Burundi vs. Algeria",
                  "question": "Will Algeria win?",
                  "sports_type": "soccer_team_full_time_winner"})
    out = await loop.resolve_venue_identity(
        conn, market_row={"slug": "afcq-bdi-dza-2026-09-29-dza",
                          "title": "Burundi vs. Algeria",
                          "event_title": "Burundi vs. Algeria",
                          "condition_id": "0xabc"},
        priced_outcome="Algeria")
    realism_reads = [q for q, _ in conn.queries
                     if "sports_type" in q and "event_slug" in q
                     and "sibling" not in q]
    assert len(realism_reads) == 1, conn.queries
    # AND THAT ONE READ SERVED BOTH ANSWERS.
    assert out["venue_event_key"] == "afcq-bdi-dza-2026-09-29"
    assert out["realism"]["verdict"] == vreal.REAL
