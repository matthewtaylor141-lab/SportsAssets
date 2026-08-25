"""The deterministic lane was structurally dead, on both sides at once.

event_keys_for runs on BOTH sides — venue slugs at sweep time, whale
slugs at copy time — and the two grammars differ by one token. The
venue names the market TYPE in a leading prefix (aec/atc/asc/tsc/
astatc); the whale's feed does not:

    whale  efl-don-mid-2026-08-25-spread-away-1pt5
             -> key  efl-don-mid-2026-08-25
    venue  asc-efl-don-mid-2026-08-25-away-1pt5
             -> key  asc-efl-don-mid-2026-08-25

Same game, same date, same teams, keys that can never intersect.

And resolve compounded it. For a DATED signal it admitted only keys
carrying an "@" stamp or starting with a kind prefix — so the whale's
one deterministic slug key, which has neither, was discarded on every
dated trade. Every trade is dated. The lane could only ever match on
title strings.

Measured: the unmapped census over 400 sampled rows put
no_key_intersection at 207 (51.8%) — the largest single cause in the
funnel — and its first printed example was exactly this pair.

GAME AGREEMENT IS UNTOUCHED, which is the only thing that matters
here: a key ending in the date is exactly as strong a guarantee as one
carrying an "@" stamp, because it IS the date. Market-type agreement is
also untouched — resolve still applies PREFIX_FOR_TYPE to whatever the
key returns.
"""

from sportsassets.workers.premap import (_dated_admissible, dated_keys,
                                         event_keys_for)

WHALE = "efl-don-mid-2026-08-25-spread-away-1pt5"
VENUE = "asc-efl-don-mid-2026-08-25-away-1pt5"
D = "2026-08-25"


class TestTheTwoGrammarsNowMeet:
    def test_the_venue_slug_also_emits_the_kindless_form(self):
        keys = set(event_keys_for(None, VENUE))
        assert "asc-efl-don-mid-2026-08-25" in keys
        assert "efl-don-mid-2026-08-25" in keys

    def test_the_whale_and_venue_keys_intersect(self):
        assert set(event_keys_for(None, WHALE)) & set(
            event_keys_for(None, VENUE))

    def test_they_did_not_before(self):
        """The bug, stated as arithmetic: the whale side has no kind
        prefix to strip, so without the venue emitting the kindless
        form there is nothing to meet on."""
        whale = set(event_keys_for(None, WHALE))
        assert "asc-efl-don-mid-2026-08-25" not in whale

    def test_every_venue_family_bridges(self):
        for pre in ("aec", "atc", "asc", "tsc", "astatc"):
            keys = set(event_keys_for(None, f"{pre}-nba-lal-bos-{D}-x"))
            assert f"nba-lal-bos-{D}" in keys, pre

    def test_a_slug_with_no_kind_prefix_is_unchanged(self):
        keys = set(event_keys_for(None, WHALE))
        assert keys == {"efl-don-mid-2026-08-25"}


class TestTheReadSideAdmitsIt:
    def test_a_slug_key_ending_in_the_date_is_admissible(self):
        keys = set(event_keys_for("Doncaster vs Middlesbrough", WHALE))
        assert "efl-don-mid-2026-08-25" in _dated_admissible(keys, D)

    def test_the_old_filter_would_have_dropped_it(self):
        """Neither an '@' stamp nor a kind prefix — the two things the
        old rule admitted."""
        k = "efl-don-mid-2026-08-25"
        assert "@" not in k
        assert not k.startswith(("aec-", "atc-", "asc-", "tsc-", "astatc-"))

    def test_stamped_title_keys_are_still_admissible(self):
        keys = set(event_keys_for("Doncaster vs Middlesbrough", WHALE))
        adm = _dated_admissible(keys, D)
        assert any("@" in k for k in adm)
        assert set(dated_keys(keys)) <= adm


class TestGameAgreementSurvives:
    """The whole reason the filter exists. A dated signal must never
    match another day's game."""

    def test_a_different_day_cannot_match(self):
        adm = _dated_admissible(
            set(event_keys_for("Doncaster vs Middlesbrough", WHALE)), D)
        other = set(event_keys_for(
            None, "asc-efl-don-mid-2026-08-27-away-1pt5"))
        assert not (adm & other)

    def test_a_bare_undated_title_key_is_not_admitted(self):
        """'doncaster vs middlesbrough' with no date would match every
        meeting of those clubs — the 2026-08-24 incident."""
        adm = _dated_admissible(
            set(event_keys_for("Doncaster vs Middlesbrough", WHALE)), D)
        assert "doncaster vs middlesbrough" not in adm

    def test_every_admitted_key_carries_the_date(self):
        adm = _dated_admissible(
            set(event_keys_for("Doncaster vs Middlesbrough", WHALE)), D)
        for k in adm:
            assert D in k, k

    def test_a_different_fixture_on_the_same_day_does_not_match(self):
        adm = _dated_admissible(
            set(event_keys_for("Doncaster vs Middlesbrough", WHALE)), D)
        other = set(event_keys_for(None, f"asc-efl-ips-nor-{D}-away-1pt5"))
        assert not (adm & other)
