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


# ── 1 · THE FULL-GAME CONTRACTS, WITH THE CATALOGUE'S OWN FIELDS ─────
#
# THE CORRECTION THIS FILE NOW PINS. The first rule was "everything after
# the trailing date is empty or exactly the matched side". That is
# necessary and nowhere near sufficient -- it admitted NHL conference
# futures, a Stanley Cup trophy market and an unsupported kind prefix, all
# of which have an empty residual. A token count would have been a second
# guess. The period is now established from the venue's own structured
# metadata: a confirmed money-line `kind`, an exact decomposition into its
# `event_slug` and `side_norm`, and a sibling count that says two
# participants rather than a field of entrants.

def _ok(slug, *, kind, event_slug, side, siblings=2):
    return V.period_of_venue_slug(slug, kind=kind, event_slug=event_slug,
                                  side=side, sibling_markets=siblings)


@pytest.mark.parametrize("slug,kind,ev,side", [
    ("aec-mlb-lad-sf-2026-09-25",        "aec", "mlb-lad-sf-2026-09-25",   None),
    ("aec-mlb-hou-ath-2026-09-25",       "aec", "mlb-hou-ath-2026-09-25",  None),
    ("aec-mlb-tb-phi-2026-09-25",        "aec", "mlb-tb-phi-2026-09-25",   None),
    ("aec-mlb-cin-tor-2026-09-25",       "aec", "mlb-cin-tor-2026-09-25",  None),
    ("aec-mlb-az-sd-2026-09-25",         "aec", "mlb-az-sd-2026-09-25",    None),
    ("aec-cfb-clmsn-cah-2026-09-25",     "aec", "cfb-clmsn-cah-2026-09-25", None),
    ("aec-atp-danmed-valroy-2026-09-23", "aec", "atp-danmed-valroy-2026-09-23", None),
    ("aec-cs2-100t-ast-2026-09-26",      "aec", "cs2-100t-ast-2026-09-26", None),
])
def test_a_bare_dated_moneyline_on_a_two_participant_event_is_full_match(
        slug, kind, ev, side):
    got = _ok(slug, kind=kind, event_slug=ev, side=side)
    assert got["period"] == V.FULL_MATCH, got
    assert not got["refusals"]


def test_the_side_may_follow_the_event_when_the_catalogue_names_it():
    got = _ok("lmx-aft-cmf-2026-09-25-cmf", kind="lmx",
              event_slug="aft-cmf-2026-09-25", side="cmf")
    # `lmx` is not a confirmed money-line KIND, so this refuses on kind --
    # which is the point: a league token is not a market family.
    assert got["period"] is None
    assert V.R_PERIOD_KIND in got["refusals"]


def test_a_three_way_event_is_a_match_too():
    got = _ok("atc-lmx-aft-cmf-2026-09-25-cmf", kind="atc",
              event_slug="lmx-aft-cmf-2026-09-25", side="cmf", siblings=3)
    assert got["period"] == V.FULL_MATCH, got


# ── 2 · THE COUNTEREXAMPLES, ALL RETAINED ────────────────────────────

def test_an_inning_segment_is_refused_on_the_decomposition():
    """The slug run 71 found inside the money-line family."""
    got = _ok("atc-mlb-atl-mia-2026-09-25-i6-draw", kind="atc",
              event_slug="mlb-atl-mia-2026-09-25", side="draw")
    assert got["period"] is None
    assert V.R_PERIOD_SHAPE in got["refusals"]
    assert "i6" in got["market_slug"]


def test_a_quarter_winner_is_refused_on_the_decomposition():
    """From the live board on build 51f20e7, inside the money-line family."""
    got = _ok("atc-cfb-clmsn-cah-2026-09-25-winner-2q-clmsn", kind="atc",
              event_slug="cfb-clmsn-cah-2026-09-25", side="clmsn")
    assert got["period"] is None
    assert V.R_PERIOD_SHAPE in got["refusals"]


def test_conference_futures_are_refused_on_the_kind():
    """`aqc` is not a confirmed money-line kind."""
    got = _ok("aqc-nhl-eastconf-2027-05-19-finalq-bos", kind="aqc",
              event_slug="nhl-eastconf-2027-05-19", side="bos")
    assert got["period"] is None
    assert V.R_PERIOD_KIND in got["refusals"]


def test_futures_with_no_suffix_are_still_refused():
    """THE HOLE THE FIRST RULE HAD: an empty residual is not a match."""
    got = _ok("aqc-nhl-eastconf-2027-05-19", kind="aqc",
              event_slug="nhl-eastconf-2027-05-19", side=None)
    assert got["period"] is None
    assert V.R_PERIOD_KIND in got["refusals"]


def test_a_trophy_on_a_supported_kind_is_refused_by_the_sibling_count():
    """A Stanley Cup market decomposes cleanly and is not a fixture.

    Sixteen entrants, sixteen contracts. This is the case a token count
    could never have caught, and it is why the count is what decides.
    """
    got = _ok("aec-nhl-stanley-2027-06-01", kind="aec",
              event_slug="nhl-stanley-2027-06-01", side=None, siblings=16)
    assert got["period"] is None
    assert V.R_PERIOD_NOT_A_MATCH in got["refusals"]
    assert "one per entrant" in got["why"]


def test_an_unsupported_kind_prefix_is_refused():
    got = _ok("zzz-mlb-lad-sf-2026-09-25", kind="zzz",
              event_slug="mlb-lad-sf-2026-09-25", side=None)
    assert got["period"] is None
    assert V.R_PERIOD_KIND in got["refusals"]


@pytest.mark.parametrize("slug,ev,side", [
    ("atc-ebfcwc-bjo-paris-2026-09-26-dh1-bjo", "ebfcwc-bjo-paris-2026-09-26", "bjo"),
    ("atc-ebfwcb-bel-ger-2026-09-26-dh2",       "ebfwcb-bel-ger-2026-09-26",   "ger"),
])
def test_a_token_between_the_event_and_the_side_is_refused(slug, ev, side):
    got = _ok(slug, kind="atc", event_slug=ev, side=side)
    assert got["period"] is None, got
    assert V.R_PERIOD_SHAPE in got["refusals"]


def test_no_trailing_date_establishes_nothing():
    for slug in ("aec-mlb-lad-sf", "", None, "some-futures-market"):
        got = _ok(slug, kind="aec", event_slug="x", side=None)
        assert got["period"] is None, slug
        assert V.R_PERIOD_UNKNOWN in got["refusals"], slug


# ── 3 · MISSING METADATA FAILS CLOSED ────────────────────────────────

def test_a_missing_kind_is_not_established():
    got = V.period_of_venue_slug("aec-mlb-lad-sf-2026-09-25", side=None,
                                 event_slug="mlb-lad-sf-2026-09-25",
                                 kind=None, sibling_markets=2)
    assert got["period"] is None
    assert V.R_PERIOD_UNKNOWN in got["refusals"]
    assert "kind" in got["why"]


def test_a_missing_event_slug_is_not_established():
    got = V.period_of_venue_slug("aec-mlb-lad-sf-2026-09-25", side=None,
                                 event_slug=None, kind="aec",
                                 sibling_markets=2)
    assert got["period"] is None
    assert V.R_PERIOD_UNKNOWN in got["refusals"]
    assert "event_slug" in got["why"]


def test_an_uncounted_event_is_not_established():
    got = V.period_of_venue_slug("aec-mlb-lad-sf-2026-09-25", side=None,
                                 event_slug="mlb-lad-sf-2026-09-25",
                                 kind="aec", sibling_markets=None)
    assert got["period"] is None
    assert V.R_PERIOD_UNKNOWN in got["refusals"]
    assert "not counted" in got["why"]


def test_the_old_token_shape_rule_alone_no_longer_admits_anything():
    """No call without the catalogue's fields can reach FULL_MATCH."""
    for slug in ("aec-mlb-lad-sf-2026-09-25", "aqc-nhl-eastconf-2027-05-19",
                 "aec-nhl-stanley-2027-06-01"):
        got = V.period_of_venue_slug(slug, side=None)
        assert got["period"] is None, slug


# ── 4 · THE REFUSALS ARE DECLARED AND ATTRIBUTED ─────────────────────

def test_every_period_refusal_is_declared_and_maps_to_identity():
    from sportsassets import bettor_external_shadow as ext
    for code in (V.R_PERIOD_UNKNOWN, V.R_PERIOD_KIND, V.R_PERIOD_SHAPE,
                 V.R_PERIOD_NOT_A_MATCH):
        assert code in V.REFUSALS, code
        assert ext.STAGE_OF[code] == "3_IDENTITY", code


def test_the_confirmed_moneyline_kinds_exclude_the_futures_kind():
    assert "aqc" not in V.MONEYLINE_KINDS
    assert set(V.MONEYLINE_KINDS) == {"aec", "atc"}
    assert set(V.MATCH_SIBLING_COUNTS) == {2, 3}
