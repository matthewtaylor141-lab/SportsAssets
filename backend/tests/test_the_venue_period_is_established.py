"""The period a venue contract pays on is READ, never asserted.

THE DEFECT THESE PIN. Two places asserted it:

  * `bettor_venue_mapping.is_segment()` reads the TITLE only, while its
    sibling `is_line_market()` reads the title AND the slug -- and the
    venue puts the period in the slug.
  * `ext_pinnacle_loop` then set `contract["period"] = "FULL_GAME"` as a
    literal AND passed the same literal to the valuation, so both sides
    of the comparison agreed on a period neither had checked.

Run 71's board read is the evidence that this is reachable, not
theoretical: `atc-mlb-atl-mia-2026-09-25-i6-draw` (inning six) and
`atc-ebfcwc-bjo-paris-2026-09-26-dh1-bjo` both came back INSIDE the
money-line family, because `copy_sports.market_type_of` classifies the
venue grammar on the kind prefix alone and never inspects the suffix.

Every slug below is one the venue's own catalogue actually returned.
"""

import pytest

from sportsassets import bettor_venue_mapping as V


# ── 1 · FULL-GAME MONEY LINES, WITH THE CATALOGUE'S OWN FIELDS ───────
#
# TWO EARLIER RULES WERE WRONG AND PRODUCTION CAUGHT BOTH.
#
# v1 -- "the residual after the date is empty or exactly the matched side"
# admitted NHL conference futures, a trophy market and an unsupported
# prefix, all of which have an empty residual.
#
# v2 -- "a confirmed money-line `kind` from us_premap plus a sibling count
# of 2 or 3" rested on a vocabulary I had not read. The readback on build
# 1a97513 showed every catalogue row carries `kind = 'side'` -- a market
# TYPE, not the slug prefix -- so gating on ("aec","atc") refused
# everything and turned `mapped 3` into `evaluated 0` in one cycle. The
# sibling count came back 79 and 249, because counting distinct market
# slugs under an event counts its MARKETS, not its participants.
#
# v3 gates on three independent facts that HAVE been observed: the
# catalogue's market type, the slug's money-line family, and an exact
# decomposition against the catalogue's own event and side.

SIDE = "side"        # the only `kind` value observed in production


#: A two-participant event title. v4 requires one, so the default here is
#: a fixture; the trophy and futures cases pass their own.
MATCH_TITLE = "Home Team vs. Away Team"


#: The venue's OWN market type, which is the authority. `_ok` supplies a
#: full-match one so the diagnostics can be exercised; the tests that are
#: about the authority pass their own value, or none.
MONEYLINE_V2 = "SPORTS_MARKET_TYPE_MONEYLINE"


#: The venue's v1 type, which is where SCOPE lives. `_ok` supplies a
#: full-scope one so the other checks can be exercised.
FULL_V1 = "baseball_team_full_game_winner"


def _ok(slug, *, event_slug, side=None, kind=SIDE, siblings=249,
        event_title=MATCH_TITLE, witness=2, v2=MONEYLINE_V2, v1=FULL_V1):
    return V.period_of_venue_slug(slug, kind=kind, event_slug=event_slug,
                                  side=side, sibling_markets=siblings,
                                  event_title=event_title,
                                  participant_witness=witness,
                                  sports_market_type_v2=v2,
                                  sports_market_type=v1)


@pytest.mark.parametrize("slug,ev", [
    ("aec-mlb-lad-sf-2026-09-25",        "mlb-lad-sf-2026-09-25"),
    ("aec-mlb-hou-ath-2026-09-25",       "mlb-hou-ath-2026-09-25"),
    ("aec-mlb-tb-phi-2026-09-25",        "mlb-tb-phi-2026-09-25"),
    ("aec-mlb-cin-tor-2026-09-25",       "mlb-cin-tor-2026-09-25"),
    ("aec-mlb-az-sd-2026-09-25",         "mlb-az-sd-2026-09-25"),
    ("aec-cfb-clmsn-cah-2026-09-25",     "cfb-clmsn-cah-2026-09-25"),
    ("aec-atp-danmed-valroy-2026-09-23", "atp-danmed-valroy-2026-09-23"),
    ("aec-cs2-100t-ast-2026-09-26",      "cs2-100t-ast-2026-09-26"),
])
def test_a_bare_dated_moneyline_side_market_is_a_full_match(slug, ev):
    got = _ok(slug, event_slug=ev)
    assert got["period"] == V.FULL_MATCH, got
    assert not got["refusals"]


def test_a_three_way_leg_with_the_catalogues_side_token_is_a_full_match():
    """From the production readback: a CONCACAF Nations League draw leg."""
    got = _ok("atc-cnl-jam-gtm-2026-09-25-draw",
              event_slug="cnl-jam-gtm-2026-09-25", side="draw")
    assert got["period"] == V.FULL_MATCH, got


def test_the_sibling_count_gates_nothing():
    """It counted MARKETS per event -- 79 and 249 in production."""
    for n in (1, 2, 3, 79, 249, None):
        got = _ok("aec-mlb-az-sd-2026-09-25",
                  event_slug="mlb-az-sd-2026-09-25", siblings=n)
        assert got["period"] == V.FULL_MATCH, n
    assert "not its participants" in got["sibling_markets_gates_nothing"]


# ── 2 · EVERY COUNTEREXAMPLE, RETAINED ───────────────────────────────

def test_an_inning_segment_is_refused_on_the_decomposition():
    got = _ok("atc-mlb-atl-mia-2026-09-25-i6-draw",
              event_slug="mlb-atl-mia-2026-09-25", side="draw")
    assert got["period"] is None
    # the identifier disagreeing with the venue's own full scope is a
    # CONFLICT now; the identifier no longer refuses on its own
    assert V.R_SCOPE_CONFLICT in got["refusals"]
    assert got["slug_decomposes_cleanly"] is False


def test_a_quarter_winner_is_refused_on_the_decomposition():
    """From the live board on build 51f20e7, inside the money-line family."""
    got = _ok("atc-cfb-clmsn-cah-2026-09-25-winner-2q-clmsn",
              event_slug="cfb-clmsn-cah-2026-09-25", side="clmsn")
    assert got["period"] is None
    # the identifier disagreeing with the venue's own full scope is a
    # CONFLICT now; the identifier no longer refuses on its own
    assert V.R_SCOPE_CONFLICT in got["refusals"]
    assert got["slug_decomposes_cleanly"] is False


@pytest.mark.parametrize("slug,side", [
    ("aqc-nhl-eastconf-2027-05-19-finalq-bos", "bos"),
    ("aqc-nhl-eastconf-2027-05-19",            None),
])
def test_conference_futures_are_refused_on_the_family(slug, side):
    """THE HOLE v1 HAD: an empty residual is not a match."""
    got = _ok(slug, event_slug="nhl-eastconf-2027-05-19", side=side)
    assert got["period"] is None
    assert V.R_PERIOD_KIND in got["refusals"]


def test_an_unsupported_slug_prefix_is_refused():
    got = _ok("zzz-mlb-lad-sf-2026-09-25", event_slug="mlb-lad-sf-2026-09-25")
    assert got["period"] is None
    assert V.R_PERIOD_KIND in got["refusals"]


def test_a_market_type_outside_the_observed_vocabulary_is_refused():
    """A line or a total is not a side market, and is not assumed benign."""
    for kind in ("total", "spread", "prop", "unknown"):
        got = _ok("aec-mlb-lad-sf-2026-09-25",
                  event_slug="mlb-lad-sf-2026-09-25", kind=kind)
        assert got["period"] is None, kind
        assert V.R_PERIOD_KIND in got["refusals"], kind


@pytest.mark.parametrize("slug,ev,side", [
    ("atc-ebfcwc-bjo-paris-2026-09-26-dh1-bjo", "ebfcwc-bjo-paris-2026-09-26", "bjo"),
    ("atc-ebfwcb-bel-ger-2026-09-26-dh2",       "ebfwcb-bel-ger-2026-09-26",   "ger"),
])
def test_a_token_between_the_event_and_the_side_is_refused(slug, ev, side):
    got = _ok(slug, event_slug=ev, side=side)
    assert got["period"] is None, got
    # the identifier disagreeing with the venue's own full scope is a
    # CONFLICT now; the identifier no longer refuses on its own
    assert V.R_SCOPE_CONFLICT in got["refusals"]
    assert got["slug_decomposes_cleanly"] is False


def test_no_trailing_date_establishes_nothing():
    for slug in ("aec-mlb-lad-sf", "", None, "some-futures-market"):
        got = _ok(slug, event_slug="x")
        assert got["period"] is None, slug
        assert V.R_PERIOD_UNKNOWN in got["refusals"], slug


# ── 3 · MISSING METADATA FAILS CLOSED ────────────────────────────────

def test_a_missing_kind_is_not_established():
    got = V.period_of_venue_slug("aec-mlb-lad-sf-2026-09-25",
                                 event_slug="mlb-lad-sf-2026-09-25",
                                 kind=None)
    assert got["period"] is None
    assert V.R_PERIOD_UNKNOWN in got["refusals"]
    assert "kind" in got["why"]


def test_a_missing_event_slug_is_not_established():
    got = V.period_of_venue_slug("aec-mlb-lad-sf-2026-09-25",
                                 event_slug=None, kind=SIDE)
    assert got["period"] is None
    assert V.R_PERIOD_UNKNOWN in got["refusals"]
    assert "event_slug" in got["why"]


def test_nothing_reaches_full_match_without_the_catalogues_fields():
    for slug in ("aec-mlb-lad-sf-2026-09-25", "aqc-nhl-eastconf-2027-05-19"):
        assert V.period_of_venue_slug(slug)["period"] is None, slug


# ── 4 · WHAT IS STILL NOT ESTABLISHED, STATED IN THE RESULT ──────────

def test_the_trophy_hole_v3_named_is_closed_by_the_participant_test():
    """THE HOLE v3 NAMED, NOW CLOSED -- and this is the assertion that flipped.

    v3 admitted `aec-nhl-stanley-2027-06-01`: it is a `side` market, `aec`
    is a confirmed money-line family, and `aec-{event_slug}` decomposes
    exactly. Steps 1-3 cannot separate a trophy from a fixture, because a
    trophy decomposes identically.

    v4 reads the catalogue's own `event_title` with the SAME " vs " split
    `shadow-mapgap` already uses on our side of the crossing. "Stanley Cup
    Winner" names one side, so it refuses by name.
    """
    got = _ok("aec-nhl-stanley-2027-06-01",
              event_slug="nhl-stanley-2027-06-01",
              event_title="Stanley Cup Winner")
    assert got["period"] is None
    assert got["refusals"] == [V.R_PERIOD_NOT_A_MATCH]
    assert got["participants_named"] == 1


@pytest.mark.parametrize("slug,ev,title", [
    # every one of these is a real title from the venue's own board
    ("aec-nhl-stanley-2027-06-01", "nhl-stanley-2027-06-01",
     "Stanley Cup Winner"),
    ("aec-nhl-eastconf-2027-05-19", "nhl-eastconf-2027-05-19",
     "Eastern Conference Winner"),
    ("aec-f1-qaagp-2026-09-26-cons", "f1-qaagp-2026-09-26-cons",
     "Qatar Airways Azerbaijan Grand Prix Winning Constructor"),
    ("aec-pga-prescup-2026-09-26-rd3", "pga-prescup-2026-09-26-rd3",
     "Presidents Cup Round 3 Winner"),
    ("aec-nascar-hc4-2026-09-27-w", "nascar-hc4-2026-09-27-w",
     "Hollywood Casino 400 Winner"),
    ("aec-dota2-blastslam-2026-10-11-w", "dota2-blastslam-2026-10-11-w",
     "BLAST Slam VIII Winner"),
    # and the non-sport markets the desk bucket also carries
    ("aec-temp-nychigh-2026-09-26", "temp-nychigh-2026-09-26",
     "Highest temperature in NYC on September 26?"),
    ("aec-ntflx-1shwglbl-2026-09-29", "ntflx-1shwglbl-2026-09-29",
     "Top Global Netflix Show This Week?"),
])
def test_a_field_of_entrants_is_not_a_two_participant_match(slug, ev, title):
    """Not one trophy case -- the whole family of them, by their own titles."""
    got = _ok(slug, event_slug=ev, event_title=title)
    assert got["period"] is None, title
    assert got["refusals"] == [V.R_PERIOD_NOT_A_MATCH], title


def test_a_missing_event_title_fails_closed_under_its_own_name():
    """UNMEASURED IS NOT BENIGN, and it is not the generic unknown either.

    v2's `kind` mistake was diagnosed in one production cycle because the
    three ways it could fail had three names. An absent title gets its own.
    """
    got = _ok("aec-mlb-lad-sf-2026-09-25", event_slug="mlb-lad-sf-2026-09-25",
              event_title=None)
    assert got["period"] is None
    assert got["refusals"] == [V.R_PERIOD_TITLE]
    assert got["refusals"] != [V.R_PERIOD_NOT_A_MATCH]


def test_the_participant_witness_gates_nothing():
    """Carried for a later read to promote, never gated on before observed.

    The sibling count was gated on before it had been read and refused
    everything. The distinct-side_norm count is reported under the same
    discipline and nothing depends on it.
    """
    for w in (None, 0, 1, 2, 3, 79, 249):
        got = _ok("aec-mlb-lad-sf-2026-09-25",
                  event_slug="mlb-lad-sf-2026-09-25", witness=w)
        assert got["period"] == V.FULL_MATCH, w
        assert got["participant_witness"] == w


def test_a_real_two_participant_title_still_passes_in_every_observed_shape():
    """The gate must not have become a blanket refusal -- v2's failure mode."""
    for title in ("NYM Mets vs WSH Nationals",
                  "Arizona Diamondbacks vs. San Diego Padres",
                  "Jamaica vs Guatemala",
                  "Daniil Medvedev vs. Valentin Royer",
                  "100 Thieves vs Astralis",
                  "Alexis de la Cerda vs Cain Lewis"):
        got = _ok("aec-mlb-lad-sf-2026-09-25",
                  event_slug="mlb-lad-sf-2026-09-25", event_title=title)
        assert got["period"] == V.FULL_MATCH, title
        assert len(got["participants_parsed"]) == 2, title


def test_every_period_refusal_is_declared_and_maps_to_identity():
    from sportsassets import bettor_external_shadow as ext
    for code in (V.R_PERIOD_UNKNOWN, V.R_PERIOD_KIND, V.R_PERIOD_SHAPE,
                 V.R_PERIOD_NOT_A_MATCH, V.R_PERIOD_TITLE):
        # R_PERIOD_SHAPE stays DECLARED though the identifier no longer
        # refuses on its own: the code is still reachable through the
        # scope-conflict path's evidence and removing it would lose the name.
        assert code in V.REFUSALS, code
        assert ext.STAGE_OF[code] == "3_IDENTITY", code


def test_the_confirmed_vocabularies_are_the_observed_ones():
    assert set(V.MONEYLINE_PREFIXES) == {"aec", "atc"}
    assert "aqc" not in V.MONEYLINE_PREFIXES
    assert set(V.SIDE_MARKET_KINDS) == {"side"}


# ── 5 · THE TWO CHECKS ARE NOT INTERCHANGEABLE ───────────────────────

def test_the_participant_title_does_not_by_itself_establish_scope():
    """A REAL FIXTURE'S SEGMENT still refuses, title notwithstanding.

    The title test separates a fixture from a field of entrants. It says
    NOTHING about period: a two-participant event sells first halves,
    innings and quarters as well, and the exact decomposition is the only
    check that speaks to scope. Step 4 without step 3 would admit every
    segment of a real fixture.
    """
    got = _ok("atc-mlb-atl-mia-2026-09-25-i6-draw", side="draw",
              event_slug="mlb-atl-mia-2026-09-25",
              event_title="Atlanta Braves vs. Miami Marlins")
    assert got["period"] is None
    # THE VENUE'S OWN SCOPE IS WHAT REFUSES, and the identifier's
    # disagreement is named as the conflict it is.
    assert got["refusals"] == [V.R_SCOPE_CONFLICT]
    assert got["slug_decomposes_cleanly"] is False
    # and the title it did not need to read DOES name two participants
    assert len(V.participants_in_event_title(got["event_title"])) == 2, (
        "the title names two participants -- and scope still refused")


def test_the_decomposition_alone_would_admit_a_trophy():
    """And step 3 without step 4 admits every trophy -- the v3 hole.

    Stated as a pair with the test above so neither check can be dropped
    as redundant by a future reader.
    """
    trophy = _ok("aec-nhl-stanley-2027-06-01",
                 event_slug="nhl-stanley-2027-06-01",
                 event_title="Stanley Cup Winner")
    assert trophy["refusals"] == [V.R_PERIOD_NOT_A_MATCH]
    # the decomposition itself was satisfied -- that is exactly the point
    assert "aec-nhl-stanley-2027-06-01" in trophy["accepted_decompositions"]


def test_the_result_says_which_check_establishes_scope():
    got = _ok("aec-mlb-lad-sf-2026-09-25", event_slug="mlb-lad-sf-2026-09-25")
    w = got["what_each_check_establishes"]
    assert "THE SCOPE" in w["exact_decomposition"]
    assert "NOT independently prove full-match scope" in \
        w["two_participant_title"]
    assert "SCOPE" in got["does_not_establish"].upper()



# ── 6 · THE VENUE'S OWN MARKET TYPE IS THE AUTHORITY ─────────────────
#
# The venue publishes a Sports Schema and it directs consumers away from
# parsing identifiers. `sportsMarketTypeV2` answers what a market prices;
# the kind, the prefix, the decomposition and the title are DIAGNOSTICS.
# The values below are ones this repository has OBSERVED in live responses
# (research/run85_phase2e.classify, run85_trackbl_block.GAME_TYPES).

def test_the_authority_overrules_a_slug_that_looks_like_a_moneyline():
    """A slug can read as a full-game money line and be a spread.

    This is the whole reason the schema tells consumers not to parse
    identifiers, and it is the case no amount of slug grammar can catch.
    """
    for v2 in ("SPORTS_MARKET_TYPE_SPREAD", "SPORTS_MARKET_TYPE_TOTAL",
               "SPORTS_MARKET_TYPE_TOTALS", "SPORTS_MARKET_TYPE_PROP",
               "SPORTS_MARKET_TYPE_FUTURE"):
        got = _ok("aec-mlb-lad-sf-2026-09-25",
                  event_slug="mlb-lad-sf-2026-09-25", v2=v2)
        assert got["period"] is None, v2
        assert got["refusals"] == [V.R_TYPE_NOT_MONEYLINE], v2


def test_the_generic_value_is_not_a_licence_to_read_the_slug():
    got = _ok("aec-mlb-lad-sf-2026-09-25",
              event_slug="mlb-lad-sf-2026-09-25",
              v2="SPORTS_MARKET_TYPE_UNSPECIFIED")
    assert got["period"] is None
    assert got["refusals"] == [V.R_TYPE_UNSPECIFIED]


def test_an_unseen_type_value_is_not_assumed_benign():
    got = _ok("aec-mlb-lad-sf-2026-09-25",
              event_slug="mlb-lad-sf-2026-09-25",
              v2="SPORTS_MARKET_TYPE_SOMETHING_NEW")
    assert got["period"] is None
    assert got["refusals"] == [V.R_TYPE_NOT_MONEYLINE]


def test_a_missing_authority_is_an_explicit_unresolved_verdict():
    """BOTH fields absent -- NOT a pass on the diagnostics, and its own name.

    CORRECTED: an earlier version of this test required v2. v1 alone is
    sufficient and strictly more specific -- it carries sport, subject,
    SCOPE and structure where v2 carries structure alone -- so only the
    absence of BOTH leaves the type unestablished.
    """
    got = _ok("aec-mlb-lad-sf-2026-09-25",
              event_slug="mlb-lad-sf-2026-09-25", v2=None, v1=None)
    assert got["period"] is None
    assert got["refusals"] == [V.R_TYPE_NOT_RETAINED]
    assert got["market_type_authority"] == "NONE_RETAINED_BY_OUR_ADAPTER"
    # the diagnostics still RAN and are still reported -- that is the point
    # of putting the authority check last
    assert got["participants_named"] == 2
    assert "accepted_decompositions" in got


def test_a_v1_only_row_establishes_both_structure_and_scope():
    """v1 IS THE AUTHORITY WHEN v2 IS ABSENT, and this is the case that
    matters: v2 is not persisted anywhere the entry lane may read, and
    `us_premap.sports_type` -- which the copy lane's own writer stamps from
    `sportsMarketType` -- is. This repository already trusts v1 that way:
    `premap._C7_FULL_TIME` is the literal `soccer_team_full_time_winner`.
    """
    got = _ok("atc-cnl-jam-gtm-2026-09-25-draw", side="draw",
              event_slug="cnl-jam-gtm-2026-09-25",
              event_title="Jamaica vs Guatemala", v2=None,
              v1="soccer_team_full_time_winner")
    assert got["period"] == V.FULL_MATCH, got.get("why")
    assert got["market_type_authority"] == \
        "VENUE_SPORTS_SCHEMA_sportsMarketType_V1_STRUCTURE_AND_SCOPE"
    assert got["structure_from_v1"] == "WINNER"
    assert got["scope_evidence"]["scope_token"] == "full_time"


def test_a_v1_only_row_that_is_not_a_winner_still_refuses():
    got = _ok("aec-mlb-lad-sf-2026-09-25",
              event_slug="mlb-lad-sf-2026-09-25", v2=None,
              v1="baseball_team_full_game_spread")
    assert got["period"] is None
    assert got["refusals"] == [V.R_TYPE_NOT_MONEYLINE]


def test_a_v1_only_row_with_no_structure_token_refuses_as_unspecified():
    got = _ok("aec-mlb-lad-sf-2026-09-25",
              event_slug="mlb-lad-sf-2026-09-25", v2=None, v1="moneyline")
    assert got["period"] is None
    assert got["refusals"] == [V.R_TYPE_UNSPECIFIED]


def test_both_full_match_types_pass_and_only_those_two():
    for v2 in V.FULL_MATCH_TYPES_V2:
        got = _ok("aec-mlb-lad-sf-2026-09-25",
                  event_slug="mlb-lad-sf-2026-09-25", v2=v2)
        assert got["period"] == V.FULL_MATCH, v2
    assert set(V.FULL_MATCH_TYPES_V2) == {
        "SPORTS_MARKET_TYPE_MONEYLINE",
        "SPORTS_MARKET_TYPE_DRAWABLE_OUTCOME"}


def test_the_slug_and_title_are_labelled_diagnostics_in_the_result():
    got = _ok("aec-mlb-lad-sf-2026-09-25", event_slug="mlb-lad-sf-2026-09-25")
    assert "diagnostics" in got["slug_and_title_are_diagnostics_only"]
    assert got["market_type_authority"] == \
        "VENUE_SPORTS_SCHEMA_sportsMarketTypeV2"
    # and the PERIOD still rests on the decomposition, not on the type --
    # the venue sells half and inning money lines under the same type
    assert "SCOPE_FROM_THE_V1" in got["basis"]


def test_a_winner_structure_does_not_excuse_a_segment_identifier():
    """STRUCTURE from v2, SCOPE from v1, and a disagreement is a conflict."""
    got = _ok("atc-mlb-atl-mia-2026-09-25-i6-draw", side="draw",
              event_slug="mlb-atl-mia-2026-09-25",
              event_title="Atlanta Braves vs. Miami Marlins",
              v2="SPORTS_MARKET_TYPE_MONEYLINE")
    assert got["period"] is None
    assert got["refusals"] == [V.R_SCOPE_CONFLICT]


def test_every_new_refusal_is_declared_and_maps_to_identity():
    from sportsassets import bettor_external_shadow as ext
    for code in (V.R_TYPE_NOT_RETAINED, V.R_TYPE_UNSPECIFIED,
                 V.R_TYPE_NOT_MONEYLINE):
        assert code in V.REFUSALS, code
        assert ext.STAGE_OF[code] == "3_IDENTITY", code



# ── 7 · v2 IS STRUCTURE; SCOPE COMES FROM v1 ─────────────────────────
#
# THE OBSERVATION THAT SETTLES IT, from
# research/evidence/phasex1/PMUS_EQUIVALENCE_RECORDS.json -- four captured
# rows sharing ONE v2 value:
#
#   football_team_full_game_spread      SPORTS_MARKET_TYPE_SPREAD
#   baseball_team_full_game_spread      SPORTS_MARKET_TYPE_SPREAD
#   soccer_team_first_half_spread       SPORTS_MARKET_TYPE_SPREAD
#   football_team_second_half_spread    SPORTS_MARKET_TYPE_SPREAD
#
# A full game, a first half and a second half return the same v2. So v2
# cannot establish scope, and MONEYLINE / DRAWABLE_OUTCOME establish
# STRUCTURE only.

def test_the_captured_evidence_shows_v2_cannot_carry_scope():
    """The premise, asserted against the captured values themselves."""
    same_v2 = {
        "football_team_full_game_spread": "SPORTS_MARKET_TYPE_SPREAD",
        "baseball_team_full_game_spread": "SPORTS_MARKET_TYPE_SPREAD",
        "soccer_team_first_half_spread": "SPORTS_MARKET_TYPE_SPREAD",
        "football_team_second_half_spread": "SPORTS_MARKET_TYPE_SPREAD",
    }
    assert len(set(same_v2.values())) == 1
    scopes = {V.scope_from_v1(k)["scope"] for k in same_v2}
    assert scopes == {"FULL", "SEGMENT"}, (
        "one v2 value spans both scopes, which is the whole point")


@pytest.mark.parametrize("v1,scope,token", [
    ("football_team_full_game_winner", "FULL", "full_game"),
    ("soccer_team_full_time_winner", "FULL", "full_time"),
    ("baseball_team_full_game_spread", "FULL", "full_game"),
    ("soccer_team_first_half_spread", "SEGMENT", "first_half"),
    ("football_team_second_half_spread", "SEGMENT", "second_half"),
    ("baseball_team_first_five_innings_winner", "SEGMENT",
     "first_five_innings"),
    # no recognised token -> UNRESOLVED, never read as full
    ("tennis_match_winner", None, None),
    ("ufc_fight_winner", None, None),
    ("cricket_match_winner", None, None),
    ("moneyline", None, None),
    ("soccer_game_exact_score", None, None),
    ("football_team_total_first_downs", None, None),
    (None, None, None),
])
def test_the_scope_reader_on_every_captured_v1_value(v1, scope, token):
    got = V.scope_from_v1(v1)
    assert got["scope"] == scope, v1
    assert got["scope_token"] == token, v1


def test_a_whole_contest_word_is_not_read_as_full_game_scope():
    """`tennis_match_winner` plausibly covers the match. Plausibly is not
    established, and reading "match" as "full game" is identifier parsing
    under another name."""
    got = _ok("aec-atp-danmed-valroy-2026-09-23",
              event_slug="atp-danmed-valroy-2026-09-23",
              v1="tennis_match_winner")
    assert got["period"] is None
    assert got["refusals"] == [V.R_SCOPE_UNRESOLVED]


def test_a_winner_structure_with_a_segment_scope_refuses_on_scope():
    got = _ok("aec-mlb-lad-sf-2026-09-25", event_slug="mlb-lad-sf-2026-09-25",
              v1="baseball_team_first_five_innings_winner")
    assert got["refusals"] == [V.R_SCOPE_SEGMENT]


def test_a_v1_carrying_both_a_full_and_a_segment_token_is_conflicting():
    got = _ok("aec-mlb-lad-sf-2026-09-25", event_slug="mlb-lad-sf-2026-09-25",
              v1="baseball_team_full_game_first_half_winner")
    assert got["refusals"] == [V.R_SCOPE_CONFLICT]
    assert got["scope_evidence"]["scope"] == "CONFLICTING"


def test_a_missing_v1_leaves_scope_unresolved_even_with_a_winner_v2():
    got = _ok("aec-mlb-lad-sf-2026-09-25", event_slug="mlb-lad-sf-2026-09-25",
              v1=None)
    assert got["refusals"] == [V.R_SCOPE_UNRESOLVED]
    assert "cannot establish full-game scope" in got["why"]


def test_the_identifier_no_longer_decides_admission():
    """IT IS A DIAGNOSTIC, AND A DIAGNOSTIC DOES NOT DECIDE.

    The decomposition used to `return` on failure -- that is deciding
    admission, whatever it was called. It now records
    `slug_decomposes_cleanly` and the venue's own scope is what refuses.
    """
    import inspect
    import re
    src = inspect.getsource(V.period_of_venue_slug)
    # THE DECOMPOSITION'S OWN BRANCH must not return. Steps after it (the
    # participant title, which reads the catalogue's structured metadata)
    # legitimately do -- they are not the identifier.
    m = re.search(r"\n    if slug not in accepted:\n(.*?)\n    #",
                  src, re.S)
    assert m, "the decomposition branch moved; re-point this test"
    assert "return" not in m.group(1), (
        "the identifier check returns on its own, which makes it the "
        "authority again:\n" + m.group(1)[:400])
    # and what it does instead is record a diagnostic
    assert "slug_diagnostic_why" in m.group(1)


def test_a_full_scope_venue_type_still_refuses_a_disagreeing_identifier():
    """Conflict is not resolved by preferring the metadata either."""
    got = _ok("atc-mlb-atl-mia-2026-09-25-i6-draw", side="draw",
              event_slug="mlb-atl-mia-2026-09-25",
              event_title="Atlanta Braves vs. Miami Marlins",
              v1="baseball_team_full_game_winner")
    assert got["period"] is None
    assert got["refusals"] == [V.R_SCOPE_CONFLICT]
    assert got["slug_decomposes_cleanly"] is False


def test_the_basis_names_both_fields_and_what_each_settles():
    got = _ok("aec-mlb-lad-sf-2026-09-25", event_slug="mlb-lad-sf-2026-09-25")
    assert got["period"] == V.FULL_MATCH
    assert "STRUCTURE_FROM_V2" in got["basis"]
    assert "SCOPE_FROM_THE_V1" in got["basis"]
    assert got["scope"] == "FULL"


def test_the_three_scope_refusals_are_declared_and_map_to_identity():
    from sportsassets import bettor_external_shadow as ext
    for code in (V.R_SCOPE_UNRESOLVED, V.R_SCOPE_SEGMENT,
                 V.R_SCOPE_CONFLICT):
        assert code in V.REFUSALS, code
        assert ext.STAGE_OF[code] == "3_IDENTITY", code


def test_the_module_says_the_published_schema_is_still_outstanding():
    """Captured samples show which values occur, never what they cover.

    Older repository classifiers are not a substitute for the published
    schema and the module must not offer them as one.
    """
    import inspect
    src = inspect.getsource(V)
    assert "docs.polymarket.us" in src
    assert "PENDING that" in src or "PENDING" in src
    assert "not a substitute for the" in src
    assert "published" in src
