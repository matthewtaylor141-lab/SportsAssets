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


def _ok(slug, *, event_slug, side=None, kind=SIDE, siblings=249):
    return V.period_of_venue_slug(slug, kind=kind, event_slug=event_slug,
                                  side=side, sibling_markets=siblings)


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
    assert V.R_PERIOD_SHAPE in got["refusals"]


def test_a_quarter_winner_is_refused_on_the_decomposition():
    """From the live board on build 51f20e7, inside the money-line family."""
    got = _ok("atc-cfb-clmsn-cah-2026-09-25-winner-2q-clmsn",
              event_slug="cfb-clmsn-cah-2026-09-25", side="clmsn")
    assert got["period"] is None
    assert V.R_PERIOD_SHAPE in got["refusals"]


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
    assert V.R_PERIOD_SHAPE in got["refusals"]


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

def test_a_clean_trophy_market_still_passes_and_the_result_says_so():
    """THE RESIDUAL HOLE, NAMED RATHER THAN CLAIMED CLOSED.

    v2 tried to catch this with a sibling count and the count turned out
    to measure markets per event, not participants. A trophy market whose
    catalogue event decomposes cleanly under a money-line prefix therefore
    still passes. The result says so in `does_not_establish`, and the next
    step is to identify a field that carries a participant count -- not to
    guess a third rule.
    """
    got = _ok("aec-nhl-stanley-2027-06-01", event_slug="nhl-stanley-2027-06-01")
    assert got["period"] == V.FULL_MATCH
    assert "trophy" in got["does_not_establish"]
    assert "participant count" in got["does_not_establish"]


def test_every_period_refusal_is_declared_and_maps_to_identity():
    from sportsassets import bettor_external_shadow as ext
    for code in (V.R_PERIOD_UNKNOWN, V.R_PERIOD_KIND, V.R_PERIOD_SHAPE,
                 V.R_PERIOD_NOT_A_MATCH):
        assert code in V.REFUSALS, code
        assert ext.STAGE_OF[code] == "3_IDENTITY", code


def test_the_confirmed_vocabularies_are_the_observed_ones():
    assert set(V.MONEYLINE_PREFIXES) == {"aec", "atc"}
    assert "aqc" not in V.MONEYLINE_PREFIXES
    assert set(V.SIDE_MARKET_KINDS) == {"side"}
