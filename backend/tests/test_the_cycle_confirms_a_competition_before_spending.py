"""CONFIRM THE KEY BEFORE SPENDING THE CREDITS.

WHAT THIS COSTS AND WHY IT IS HERE. `soccer_epl` sat in this loop's SPORTS
tuple and was fetched every cycle at roughly 18-21 metered credits -- about
2,000 a day at a 15-minute cadence -- for twenty events that could never reach
a venue contract, because the US venue lists nothing carrying the token `epl`
(research-sql run 258). The spend was invisible because the report showed a
mapping refusal, which reads like a mapping problem.

TWO SEPARATE THINGS ARE PINNED.

  1. `soccer_epl` is gone and cannot come back through the fallback path. A
     catalogue-read failure must not be a licence to reinstate it.
  2. The provider's key names for the replacement competitions are MY
     CANDIDATES, not facts -- this container has no provider egress and no key,
     so I could not read `/v4/sports` to check them. `/v4/sports` is UNMETERED,
     so the cycle checks each one for free before making a metered call on it. A
     key I guessed wrong becomes a named refusal in the report instead of
     ~20 credits and an error.
"""

from sportsassets.workers import ext_pinnacle_loop as loop


def _catalogue(*keys, active=True, ok=True):
    return {"ok": ok,
            "sports": [{"key": k, "title": k.replace("_", " ").title(),
                        "group": "Soccer", "active": active} for k in keys]}


# ── the competition that was being paid for and could not be reached ──

def test_soccer_epl_is_not_in_any_set_this_module_can_produce():
    assert "soccer_epl" not in [k for k, _ in loop.SPORTS_CONFIRMED]
    assert "soccer_epl" not in [c["key"] for c in loop.SPORTS_CANDIDATES]
    assert "soccer_epl" not in [k for k, _ in loop.SPORTS]
    # AND NOT THROUGH THE FAILED-CONFIRMATION PATH EITHER, which is the one
    # that would have quietly restored the old tuple.
    got = loop.select_sports({"ok": False})
    assert "soccer_epl" not in [k for k, _ in got["sports"]]
    assert "soccer_epl" in got["never_requested"]


def test_the_confirmed_sport_runs_even_when_the_catalogue_cannot_be_read():
    """A catalogue outage must not stop the one sport that has always worked.
    All 1,126 valuation rows this lane ever wrote are `baseball`."""
    got = loop.select_sports({"ok": False})
    assert got["sports"] == list(loop.SPORTS_CONFIRMED)
    assert ("baseball_mlb", "baseball") in got["sports"]
    assert got["catalogue_read"] is False
    # AND NO CANDIDATE IS FETCHED on an unread catalogue.
    assert len(got["rejected"]) == len(loop.SPORTS_CANDIDATES)


def test_basketball_and_hockey_are_still_not_requested():
    """The older measured refusal (NBA 0/41, NHL 0/33) must survive this
    change: a new selection path is a new way to reintroduce them."""
    every = ([k for k, _ in loop.SPORTS_CONFIRMED]
             + [c["key"] for c in loop.SPORTS_CANDIDATES])
    assert not any("basketball" in k or "icehockey" in k for k in every)


# ── a guessed key is a refusal, not a metered call ────────────────────

def test_a_key_the_provider_does_not_list_is_refused_by_name():
    got = loop.select_sports(_catalogue("baseball_mlb", "soccer_usa_mls"))
    chosen = [k for k, _ in got["sports"]]
    assert "soccer_usa_mls" in chosen
    refused = {r["key"]: r["refusal"] for r in got["rejected"]}
    # Every other candidate was mine to guess and the provider did not list it.
    assert refused["soccer_uefa_nations_league"] == loop.R_PROVIDER_DOES_NOT_LIST
    assert all(v == loop.R_PROVIDER_DOES_NOT_LIST for v in refused.values())
    assert "soccer_uefa_nations_league" not in chosen


def test_an_inactive_competition_is_refused_separately():
    """Not listed and listed-but-inactive are different facts about the
    provider and collapsing them loses which one to act on."""
    cat = _catalogue("soccer_usa_mls", active=False)
    got = loop.select_sports(cat)
    refused = {r["key"]: r["refusal"] for r in got["rejected"]}
    assert refused["soccer_usa_mls"] == loop.R_PROVIDER_LISTS_IT_INACTIVE
    assert "soccer_usa_mls" not in [k for k, _ in got["sports"]]


# ── the spend has a stated ceiling, not an implied one ────────────────

def test_the_metered_budget_caps_the_cycle_and_says_what_it_dropped():
    """The old comment derived the spend from the length of a tuple, so
    appending a sport raised the bill silently. The cap is a number now, and
    what it drops is reported rather than inferred from a short list."""
    cat = _catalogue("baseball_mlb",
                     *[c["key"] for c in loop.SPORTS_CANDIDATES])
    got = loop.select_sports(cat, budget=3)
    assert len(got["sports"]) == 3
    # Confirmation happens for ALL of them -- the cap is applied after, so a
    # key the provider does not list cannot consume a slot.
    assert len(got["confirmed_by_provider"]) == len(loop.SPORTS_CANDIDATES)
    assert len(got["budget_dropped"]) == len(loop.SPORTS_CANDIDATES) - 2
    for d in got["budget_dropped"]:
        assert "budget" in d["why"]
        assert isinstance(d["venue_events"], int)


def test_the_order_is_measured_venue_coverage():
    """Which competitions to spend the budget on is decided by how many
    contracts the venue actually lists, not by the order I typed them."""
    cat = _catalogue("baseball_mlb",
                     *[c["key"] for c in loop.SPORTS_CANDIDATES])
    got = loop.select_sports(cat, budget=99)
    rows = got["confirmed_by_provider"]
    counts = [r["venue_events"] for r in rows]
    assert counts == sorted(counts, reverse=True), counts
    # The measured leader is the UEFA Nations League at 39 venue events.
    assert rows[0]["our_token"] == "unl"


def test_every_candidate_carries_the_evidence_for_its_place():
    """A candidate with no measured coverage is a guess wearing a number."""
    for c in loop.SPORTS_CANDIDATES:
        assert c["our_token"] and isinstance(c["venue_events"], int)
        assert c["venue_events"] > 0
        assert c["family"] in ("soccer", "baseball")


# ── the ordering evidence itself was wrong once; pin the correction ───

def test_no_candidate_rests_on_a_token_that_matched_a_team_abbreviation():
    """MY FIRST ORDERING WAS REFUTED BY THE NEXT MEASUREMENT.

    I ranked seven competitions by `us_premap` rows matching `'%-<token>-%'`:
    Serie A 1812, Conference League 1256, Primeira Liga 726, Turkey 570. Run 259
    asked for the same counts with the token in the slug's LEAGUE POSITION and
    six of the seven were ZERO -- `col` was Colorado, `sea` was Seattle, `por`
    and `tur` were Portugal and Turkey in the Nations League, `arg` was an
    eBattles fixture. A team abbreviation and a league token share a slug.

    So none of those tokens may appear as a candidate, and the query the
    selection now uses must read the league POSITION, not the whole slug.
    """
    refuted = {"sea", "col", "por", "tur", "bra", "arg", "kor"}
    assert not (refuted & {c["our_token"] for c in loop.SPORTS_CANDIDATES})
    assert "split_part(market_slug, '-', 2)" in loop.VENUE_SOCCER_BOARD_SQL
    # AND THE BOARD QUERY EXCLUDES THE SIMULATIONS, in SQL -- a competition of
    # 168 eBattles events must not outrank a real one.
    assert "ebattles" in loop.VENUE_SOCCER_BOARD_SQL.lower()


def test_the_board_is_read_at_cycle_time_because_the_real_board_is_seasonal():
    """The measured board is almost entirely national-team football -- unl 39
    events, intf 15, cnl 10, uwcl 9 -- against club competitions in single
    figures. Late September is an international window, so a hard-coded list of
    club competitions would have been wrong within a fortnight even if I HAD
    measured it correctly. The set is derived from the board instead."""
    board = dict(loop.VENUE_SOCCER_BOARD_MEASURED_2026_09_28)
    assert board["unl"] == 39
    cands = loop.candidates_from_board(loop.VENUE_SOCCER_BOARD_MEASURED_2026_09_28)
    assert cands[0]["our_token"] == "unl", cands[:3]
    assert cands[0]["key"] == "soccer_uefa_nations_league"


def test_a_board_token_with_no_provider_key_is_simply_not_a_candidate():
    """`par2`, `btla`, `lal2` and the rest are on the venue's board and have no
    entry in the map. They must drop out silently rather than produce a guessed
    provider key, which is the failure mode this whole change exists to stop."""
    cands = loop.candidates_from_board([("par2", 4), ("unl", 39), ("zzz", 99)])
    assert [c["our_token"] for c in cands] == ["unl"]


def test_international_friendlies_are_excluded_with_a_stated_reason():
    """15 events, second on the board, and deliberately not requested: the
    settlement and team-selection conventions are not established for this
    lane, so a candidate would reach a rule refusal rather than a trade."""
    assert "intf" in loop.VENUE_TOKENS_DELIBERATELY_EXCLUDED
    assert "intf" not in {c["our_token"] for c in loop.SPORTS_CANDIDATES}
    cands = loop.candidates_from_board([("intf", 15)])
    assert cands == []


async def test_a_read_board_is_used_in_its_own_order():
    class _Good:
        async def fetch(self, *a):
            return [{"token": "mls", "events": 7,
                     "titles": ["Columbus Crew vs. Inter Miami CF"],
                     "title_days": [
                         "Columbus Crew vs. Inter Miami CF\u00012026-09-26"]},
                    {"token": "unl", "events": 2,
                     "titles": ["Belgium vs. France"],
                     "title_days": ["Belgium vs. France\u00012026-10-01"]}]

    got = await loop.venue_soccer_competitions(_Good())
    assert got["read"] is True
    assert got["board"] == [("mls", 7), ("unl", 2)]
    cands = loop.candidates_from_board(got["board"], got["titles"],
                                       got["title_days"])
    assert [c["our_token"] for c in cands] == ["mls", "unl"]
    # THE VENUE'S FIXTURES TRAVEL WITH THE CANDIDATE, because the mapping is
    # confirmed against them and a candidate that arrives without them would be
    # admitted on key existence alone.
    assert cands[0]["venue_titles"] == ["Columbus Crew vs. Inter Miami CF"]
    # AND THE FIXTURE DATES, so the date check runs on the scheduled path and
    # not only in a test -- which was Codex's second point on identity.
    assert cands[0]["venue_title_days"] == {
        "Columbus Crew vs. Inter Miami CF": "2026-09-26"}


# ── EXISTENCE IS NOT IDENTITY ─────────────────────────────────────────

def test_a_key_that_exists_but_names_another_competition_is_refused():
    """THE HOLE CODEX REPRODUCED. `select_sports` confirmed that a key EXISTS
    and is ACTIVE and reported it under `confirmed_by_provider`. That is not
    identity: `soccer_england_league2` exists, is active, and is the fourth
    tier -- the venue's `engnl` is the fifth, with entirely different clubs.

    The provider's fixtures are what settle it, so they are compared against
    the venue's own.
    """
    england_league_two = [{"home_team": "Bradford City", "away_team": "Walsall"},
                          {"home_team": "Notts County",
                           "away_team": "Crewe Alexandra"}]
    venue_national_league = ["AFC Fylde vs. Carlisle United",
                             "Barrow AFC vs. Scunthorpe United",
                             "Gateshead FC vs. Altrincham FC"]
    got = loop.confirm_mapping_by_fixtures(
        provider_events=england_league_two,
        venue_event_titles=venue_national_league)
    assert got["ok"] is False
    assert got["refusal"] == loop.R_MAPPING_FIXTURES_DO_NOT_MATCH
    assert got["matches"] == 0


def test_the_same_competition_confirms_on_its_fixtures():
    got = loop.confirm_mapping_by_fixtures(
        provider_events=[
            {"home_team": "Columbus Crew", "away_team": "Inter Miami CF"},
            {"home_team": "Seattle Sounders FC",
             "away_team": "Sporting Kansas City"}],
        venue_event_titles=["Columbus Crew vs. Inter Miami CF",
                            "New York Red Bulls vs. St. Louis City SC",
                            "Seattle Sounders FC vs. Sporting Kansas City"])
    assert got["ok"] is True
    assert got["refusal"] is None
    # BOTH SIDES OF A FIXTURE, which is what confirms it. Counting matches
    # instead measured venue coverage: the provider's competition routinely
    # carries more fixtures than the venue lists, so a correctly mapped
    # competition with one listed fixture would have been refused forever.
    assert got["strong_matches"] >= loop.MIN_STRONG_FIXTURE_MATCHES
    assert any(m["strong"] for m in got["matched_fixtures"])


def test_no_provider_fixtures_is_unconfirmable_not_confirmed():
    """A competition between rounds legitimately has no fixtures, and so does a
    key that is not this competition. Admitting on that basis would be
    admitting on absence, which is the error this whole batch is correcting."""
    got = loop.confirm_mapping_by_fixtures(
        provider_events=[],
        venue_event_titles=["AFC Fylde vs. Carlisle United"])
    assert got["ok"] is False
    assert got["refusal"] == loop.R_MAPPING_UNCONFIRMABLE


def test_club_form_words_alone_do_not_confirm_a_mapping():
    """"United" and "City" appear in every English division. A mapping that
    confirmed on them would confirm every tier against every other."""
    got = loop.confirm_mapping_by_fixtures(
        provider_events=[{"home_team": "Manchester United",
                          "away_team": "Leeds United"}],
        venue_event_titles=["Carlisle United vs. Southend United"])
    assert got["ok"] is False, got
    assert got["refusal"] == loop.R_MAPPING_FIXTURES_DO_NOT_MATCH
    # "united" is dropped as club-form noise, so this does not even register as
    # a weak match -- and if it did, a weak match alone still cannot confirm.
    assert got["strong_matches"] == 0


def test_the_two_refuted_mappings_are_out_with_their_evidence():
    """I mapped `engnl` to League Two and `irl1` to the Irish Premier Division.
    The venue's fixtures say fifth tier and Irish FIRST Division. Codex
    challenged the first; the second was found by applying the same check to
    every entry rather than only the one questioned."""
    for token in ("engnl", "irl1"):
        assert token in loop.VENUE_TOKENS_WITH_A_REFUTED_MAPPING
        assert token not in loop.VENUE_TOKEN_TO_PROVIDER_KEY
        rec = loop.VENUE_TOKENS_WITH_A_REFUTED_MAPPING[token]
        for field in ("i_mapped_it_to", "the_venue_fixtures_are",
                      "which_competition_that_is", "why_it_is_out"):
            assert rec[field]
    # AND THEY CANNOT COME BACK THROUGH THE BOARD.
    assert loop.candidates_from_board([("engnl", 12), ("irl1", 5)]) == []


def test_the_metered_budget_change_is_stated_as_a_change():
    """Three fetches became four. That is a resource decision, so it is
    written with its arithmetic rather than appearing as a tuple of a
    different length."""
    chg = loop.METERED_BUDGET_CHANGE
    assert chg["before"]["keys"] == 3
    assert chg["after"]["keys"] == loop.MAX_METERED_SPORTS_PER_CYCLE == 4
    assert "soccer_epl" in chg["before"]["keys_named"]
    assert chg["before"]["of_which_unreachable"] == ["soccer_epl"]
    assert "1,900" in chg["net"]


def test_the_epl_exclusion_is_a_snapshot_not_a_standing_claim():
    """A catalogue snapshot does not justify a permanent claim about the
    venue. What makes the exclusion safe is that the soccer set is derived
    from the board each cycle, so a later appearance is picked up."""
    got = loop.select_sports({"ok": False})
    assert got["this_is_a_snapshot_not_a_standing_claim"] is True
    assert "SNAPSHOT" in got["why_epl_is_never_requested"]
    assert "DERIVED from the board" in got["why_epl_is_never_requested"]


def test_the_snapshot_epoch_is_the_date_it_claims():
    """I typed 1759017600 for "2026-09-28". That is 2025-09-28, so the snapshot
    was born a year old and the fallback reported EXPIRED on its first use --
    which would have silently removed every soccer competition the moment the
    board read failed. A hand-typed epoch is a guess like any other."""
    import datetime as _dt

    at = _dt.datetime.fromtimestamp(loop.VENUE_BOARD_SNAPSHOT_AT,
                                    _dt.timezone.utc)
    assert (at.year, at.month, at.day) == (2026, 9, 28), at.isoformat()


def test_the_rejected_list_names_the_venue_token():
    """It carried the provider key and a null token, which is the half an
    operator cannot act on -- the token is what they see on the board."""
    got = loop.select_sports({"ok": False})
    assert got["rejected"]
    assert all(r["our_token"] for r in got["rejected"])


async def test_the_catalogue_read_cannot_raise_out_of_the_cycle():
    """THE DEFECT MOVING THIS FUNCTION ONTO THE CYCLE'S PATH CREATED.

    `fetch_sport_catalogue` had no caller inside `pass_once`, so an unwrapped
    transport error was harmless. The moment the cycle started calling it every
    pass to confirm a competition, that error became "a provider outage kills
    the whole cycle" -- including the funded servicing, which does not use this
    provider at all. The first run against a proxied environment failed exactly
    that way: httpx.ProxyError out of three cycle tests.

    A transport failure is the same ANSWER as a non-200: the catalogue is
    unread. This exercises the real function against an unreachable host.
    """
    got = await loop.fetch_sport_catalogue(api_key="x" * 32, timeout=0.05)
    assert got["ok"] is False
    assert got["refusal"] == loop.R_PROVIDER_ERROR
    assert got["sports"] == []
    # AND THE SELECTION SURVIVES IT, running the confirmed set alone.
    sel = loop.select_sports(got)
    assert sel["sports"] == list(loop.SPORTS_CONFIRMED)


def test_the_selection_is_reported_so_a_narrow_funnel_is_not_a_mystery():
    """A cycle with one sport and a cycle with four look identical in the
    funnel unless the selection itself is in the report."""
    got = loop.select_sports(_catalogue("baseball_mlb"))
    for key in ("sports", "rejected", "confirmed_by_provider", "budget",
                "budget_dropped", "catalogue_read", "never_requested", "why"):
        assert key in got, key
