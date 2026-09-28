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

# ── CODEX'S THREE COUNTEREXAMPLES, VERBATIM ──────────────────────────
#
# All three returned REAL_FIXTURE from the first version of `classify`. The
# function looked for simulation markers and, finding none, returned REAL -- so
# the absence of a marker WAS the evidence, which is the single error this
# module exists to prevent, written into the module that prevents it.
#
# They are kept as the first tests in the file, exactly as supplied, because
# each is a different way of having no answer: no classification at all, a
# classification nobody here recognises, and prose with nothing behind it. The
# middle one is the case that matters most -- it is how a NEW simulated family
# arrives, and the old code would have admitted it.

def test_counterexample_a_row_with_only_a_slug_is_unknown():
    got = vreal.classify({"market_slug": "unknown-contract"})
    assert got["verdict"] == vreal.UNKNOWN, got
    assert got["refusal"] == vreal.R_REALISM_NOT_ESTABLISHED
    assert not vreal.is_real({"market_slug": "unknown-contract"})


def test_counterexample_an_unrecognized_classification_is_unknown():
    """THE ONE THAT MATTERS MOST. A venue that introduces a new simulated
    family tomorrow, or renames `efootball_`, presents exactly this row."""
    got = vreal.classify({"sports_type": "future_unknown_type"})
    assert got["verdict"] == vreal.UNKNOWN, got
    assert got["refusal"] == vreal.R_REALISM_NOT_ESTABLISHED
    # AND IT SAYS WHICH ABSENCE IT IS: the column is present and carries a
    # value nobody recognises, which is not the same as a missing column.
    assert got["evidence"]["value"] == "future_unknown_type"
    assert got["evidence"]["matched"] is None
    assert "not a recognized real-competition family" in got["why"]


def test_counterexample_prose_alone_does_not_establish_a_real_fixture():
    """The eBattles rows carry real club names. "Arsenal vs Chelsea" as a
    title is precisely as consistent with the simulation as with the match."""
    got = vreal.classify({"event_title": "Arsenal vs Chelsea"})
    assert got["verdict"] == vreal.UNKNOWN, got
    assert got["refusal"] == vreal.R_REALISM_NOT_ESTABLISHED


def test_conflicting_publisher_statements_refuse_under_their_own_name():
    """A recognized REAL classification AND a simulation marker in the prose.
    Both statements are the venue's and they disagree, so this is neither an
    absence nor a simulation -- and refusing it as either would send an
    operator to the wrong place."""
    got = vreal.classify({
        "market_slug": "atc-ebfpl-ars-che-2026-09-27-dh3-ars",
        "event_title": "eBattles: Arsenal vs. Chelsea",
        "sports_type": "soccer_team_full_time_winner"})
    assert got["verdict"] == vreal.CONFLICTING, got
    assert got["refusal"] == vreal.R_REALISM_EVIDENCE_CONFLICTS
    assert got["evidence"]["real"]["matched"] == "soccer_"
    assert got["evidence"]["simulated"][0]["matched"] == "ebattles"


def test_a_futures_outright_is_neither_real_nor_simulated():
    """6,606 rows over 76 events carry the literal `futures`. It is a
    season-long outright, not a fixture between two sides, so the question
    this guard asks has no answer for it -- and a lane that prices
    head-to-head fixtures should not receive one."""
    got = vreal.classify({"sports_type": "futures",
                          "event_title": "2026 World Cup Winner"})
    assert got["verdict"] == vreal.UNKNOWN, got
    assert "outright" in got["why"]


# ── THE ALLOWLIST MUST MATCH THE VENUE'S MEASURED VOCABULARY ─────────

def test_every_measured_venue_family_classifies_without_a_guess():
    """THE GUARD AGAINST THE ALLOWLIST ITSELF BEING A GUESS.

    I wrote `REAL_SPORTS_TYPE_PREFIXES` from memory first. It invented twelve
    families the venue does not use (`mma_`, `boxing_`, `golf_`,
    `motorsport_`, `cycling_`, `rugby_`, `volleyball_`, `handball_`,
    `snooker_`, `badminton_`, `aussie_rules_`, `lacrosse_`) and omitted three
    it does (`ufc_`, `darts_`, `futures`). On a fail-closed allowlist an
    omission is a refusal, so the guessed list would have refused every UFC
    and darts contract on the board.

    These are the venue's own 14 families with their distinct-type counts,
    from research-sql run 260 reading every sports_type with no LIMIT. Each
    must reach a definite verdict -- and the UNKNOWN ones must be UNKNOWN for
    a stated reason rather than by omission.
    """
    measured = {
        # family sample type          expected verdict
        "baseball_team_full_game_winner": vreal.REAL,
        "football_team_full_game_winner": vreal.REAL,
        "soccer_team_full_time_winner": vreal.REAL,
        "tennis_match_winner": vreal.REAL,
        "table_tennis_match_winner": vreal.REAL,
        "hockey_team_full_game_winner": vreal.REAL,
        "basketball_team_full_game_winner": vreal.REAL,
        "darts_match_winner": vreal.REAL,
        "ufc_fight_winner": vreal.REAL,
        "cricket_match_winner": vreal.REAL,
        "efootball_team_full_time_winner": vreal.SIMULATED,
        "esports_match_winner": vreal.SIMULATED,
        "futures": vreal.UNKNOWN,
    }
    for sports_type, want in measured.items():
        got = vreal.classify({"sports_type": sports_type,
                              "event_title": "Side A vs. Side B"})
        assert got["verdict"] == want, (sports_type, got["verdict"], got["why"])
    # AND NOTHING IN THE LIST IS A FAMILY THE VENUE DOES NOT USE.
    invented = {"mma_", "boxing_", "golf_", "motorsport_", "cycling_",
                "rugby_", "volleyball_", "handball_", "snooker_", "badminton_",
                "aussie_rules_", "lacrosse_"}
    assert not (invented & set(vreal.REAL_SPORTS_TYPE_PREFIXES))


def test_table_tennis_is_reported_as_itself_not_as_tennis():
    """Longest match wins. `table_tennis_set_1_winner` starts with
    `table_tennis_`; reporting `tennis_` would name the wrong sport in the
    evidence an operator reads."""
    got = vreal.classify({"sports_type": "table_tennis_set_1_winner",
                          "event_title": "A vs. B"})
    assert got["verdict"] == vreal.REAL
    assert got["evidence"]["matched"] == "table_tennis_"


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
    """A row labelled real soccer whose TITLE says eBattles does not admit.

    This is the case a sports_type-only guard misses, and it is not
    hypothetical: the venue's classification and its prose are written by
    different parts of its pipeline and they are not always consistent.

    THE VERDICT IS NOW CONFLICTING, NOT SIMULATED, and the change is
    deliberate. Both statements are the publisher's own and they disagree, so
    calling it SIMULATED would assert that the venue said it was a simulation
    when what the venue did was contradict itself. Either way it REFUSES --
    which is the property that protects capital -- but the refusal name is the
    one an operator can act on.
    """
    got = vreal.classify({
        "market_slug": "atc-ebfpl-ars-liv-2026-09-27-dh2-ars",
        "event_title": "eBattles: Arsenal vs. Liverpool",
        "question": "Will Arsenal win?",
        "sports_type": "soccer_team_full_time_winner"})
    assert got["verdict"] == vreal.CONFLICTING
    assert got["refusal"] == vreal.R_REALISM_EVIDENCE_CONFLICTS
    assert got["evidence"]["simulated"][0]["field"] == "event_title"
    assert got["evidence"]["simulated"][0]["matched"] == "ebattles"
    # AND IT IS NOT REAL, which is the only thing the caller acts on.
    assert not vreal.is_real({
        "event_title": "eBattles: Arsenal vs. Liverpool",
        "sports_type": "soccer_team_full_time_winner"})


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
    assert "no sports_type" in got["why"]


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
    # AND IT REACHES REAL ON THE CLASSIFICATION, not by the absence of a
    # marker: the same row without a recognized sports_type is UNKNOWN, so
    # this test cannot pass for the new wrong reason either.
    bare = vreal.classify({
        "market_slug": "atc-ebfxx-aaa-bbb-2026-09-29-aaa",
        "event_title": "Aaa United vs. Bbb City"})
    assert bare["verdict"] == vreal.UNKNOWN


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
