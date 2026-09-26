"""A pair of club names is not a fixture, and a fixture is not a competition.

THE DEFECT THIS DIAGNOSTIC EXISTS TO SEPARATE. Run 75's resolver reached
CONCACAF Nations League rows (Guatemala vs El Salvador, Jamaica vs
Guatemala) for our Guatemalan league fixture `gtm-mrq-adm-2026-09-26`,
because the key intersection hit on the shared country token `gtm` and
nothing disambiguated it. `NO_VENUE_CONTRACT_FOR_EVENT` then collapsed four
unrelated situations into one counter.

FOUR FACTS, FOUR CHECKS, and none of them inferred from another:
participants (both sides in the venue event's own title), DATE (the same
YYYY-MM-DD on both slugs), COMPETITION, and the PAYOUT EVENT.

THE COMPETITION TEST IS READ OUT OF THE SCHEDULED RESOLVER rather than
invented. `premap._yn_alias_pick` is not a league-alias table: it takes our
`{a}-{b}-{date}-{t}` suffix, finds catalogue identifiers ending in that
suffix under exactly one DIFFERENT league code, and requires a witness from
the venue's own event title. This checks the structural half and says
plainly that the witness half is the resolver's.

AND A MATCH HERE IS NOT A BINDING. The route admits nothing and changes no
resolver; an ALL_PRESENT row is a lead to check against the resolver's own
step name.
"""

from sportsassets.api import app as A


def _est(ours, our_token, venue_event, venue_token):
    rec = {"global_slug": ours, "our_league_token": our_token}
    got = A._competition_established(rec, {"event": venue_event,
                                           "token": venue_token})
    return got, rec


def test_an_identical_league_token_establishes_the_competition():
    got, rec = _est("mls-phi-orl-2026-09-26-total-3pt5", "mls",
                    "mls-phi-orl-2026-09-26", "mls")
    assert got == "ESTABLISHED"
    assert rec["competition_basis"] == "LEAGUE_TOKEN_IDENTICAL"


def test_the_real_liga_mx_pair_is_a_CANDIDATE_LEAD_and_not_an_identity():
    """`mex` and `lmx` differ and the remainder agrees -- A LEAD, NOT A MATCH.

    A suffix agreement across different league codes is consistent with one
    fixture listed twice AND with two different competitions. Establishing
    it needs what the resolver needs: exactly one candidate league code
    across the catalogue and a witness from the venue's own event title.
    Neither is checked here, so it never reaches an identity.
    """
    for ours, ev in (("mex-caz-tol-2026-09-26-exact-score-2-1",
                      "lmx-caz-tol-2026-09-26"),
                     ("mex-gua-que-2026-09-26-gua",
                      "lmx-gua-que-2026-09-26")):
        got, rec = _est(ours, "mex", ev, "lmx")
        assert got == "CANDIDATE_LEAD", ours
        assert rec["competition_basis"].startswith("CANDIDATE_LEAD"), ours
        assert "not an identity" in rec["competition_not_established_because"]


def test_a_candidate_lead_never_reaches_all_present():
    import inspect
    src = inspect.getsource(A.api_venue_fixture_crossing)
    assert "CANDIDATE_LEAD__COMPETITION_IDENTITY_NOT_" in src
    # and the lead branch sits BEFORE the contract/payout checks
    assert src.index("CANDIDATE_LEAD__COMPETITION_IDENTITY_NOT_") < \
        src.index("FIXTURE_PRESENT_BUT_NO_FULL_MATCH_")


def test_the_wrong_competition_the_resolver_reached_is_refused():
    """The CONCACAF row for a Guatemalan league fixture. This is the case."""
    got, rec = _est("gtm-mrq-adm-2026-09-26-mrq", "gtm",
                    "cnl-gtm-slv-2026-09-28", "cnl")
    assert got == "NOT_ESTABLISHED"
    assert rec["competition_basis"] == "NOT_ESTABLISHED"
    assert "same two clubs is not the same competition" in \
        rec["competition_why"]


def test_differing_team_codes_do_not_bridge_even_on_the_same_fixture():
    """`el1-wyc-rea` and `efl1-wyw-rea` ARE Wycombe vs Reading either way.

    The diagnostic still refuses, and that is the intended conservatism:
    the resolver's bridge is suffix IDENTITY, `wyc-rea` is not `wyw-rea`,
    and normalising one into the other is exactly what must not happen on
    a diagnostic's say-so.
    """
    got, rec = _est("el1-wyc-rea-2026-09-26-draw", "el1",
                    "efl1-wyw-rea-2026-09-26", "efl1")
    assert got == "NOT_ESTABLISHED"
    assert rec["competition_basis"] == "NOT_ESTABLISHED"


def test_the_date_is_read_from_both_slugs():
    assert A._DATED.search("mex-caz-tol-2026-09-26-exact-score-2-1"
                           ).group(1) == "2026-09-26"
    assert A._DATED.search("lmx-caz-tol-2026-09-26").group(1) == "2026-09-26"
    assert A._DATED.search("no-date-here") is None


def test_the_route_separates_date_participants_competition_and_payout():
    import inspect
    src = inspect.getsource(A.api_venue_fixture_crossing)
    for verdict in ("NO_MATCHING_COMPETITION_ON_THE_VENUE_BOARD",
                    "SAME_PARTICIPANTS_ON_A_DIFFERENT_DATE",
                    "COMPETITION_PRESENT_BUT_NO_MATCHING_FIXTURE",
                    "AMBIGUOUS__MORE_THAN_ONE_VENUE_FIXTURE_MATCHED",
                    "NOT_ESTABLISHED",
                    "FIXTURE_PRESENT_BUT_NO_FULL_MATCH_",
                    "DIFFERENT_PAYOUT_EVENT"):
        assert verdict in src, verdict
    # and the single-side near miss is reported, never adopted
    assert "single_side_only_near_misses" in src
    assert "both_sides_but_a_different_date" in src


def test_the_route_says_a_match_is_not_a_resolver_binding():
    import inspect
    src = inspect.getsource(A.api_venue_fixture_crossing)
    assert "a_match_here_is_not_a_resolver_binding" in src
    assert "does NOT mean the scheduled resolver" in src


def test_the_route_writes_nothing():
    import inspect
    src = inspect.getsource(A.api_venue_fixture_crossing)
    for banned in ("INSERT ", "UPDATE ", "DELETE "):
        assert banned not in src.upper(), banned
