"""A direct slug identity match was being called a guess.

resolve_market's FIRST attempt is direct slug parity: the whale's market
slug looked up verbatim on the US venue, returning matched_by="slug".
Everything resolve_market returns was labelled `fuzzy` at the call site,
so an identity match was recorded as a guess -- and `fuzzy` is exactly
what the mapping quarantine refuses.

From the live refusal stream:

    MAPA-Q rn1 pick=Boris Butulija quarantined
      (src=fuzzy, slug=aec-itfme-marifer-borbut-2026-08-26)

`borbut` is Boris Butulija under the venue's own abbreviation grammar,
with the right league and the right date. Nothing about that is fuzzy.

Routed through resolve_market_exact rather than by promoting
matched_by=="slug", and that distinction is the safety of the change:
parity inside resolve_market carries no _has_sides guard and can return
a two-sided PARENT slug, and ordering a parent hands side selection to
the venue -- the 2026-08-23 wrong-side incident exactly.
"""

from __future__ import annotations

import inspect

from sportsassets import live_executor as le, pmus


def _src():
    return inspect.getsource(le.maybe_execute)


class TestTheIdentityAttemptExists:
    def test_his_own_slug_is_tried_through_the_exact_resolver(self):
        assert "[src_slug], ctx.get(\"outcome\")" in _src()

    def test_it_runs_BEFORE_the_fuzzy_fallback(self):
        s = _src()
        assert s.index("[src_slug], ctx.get(\"outcome\")") \
            < s.index('mapping_src = "fuzzy"')

    def test_it_is_labelled_exact_not_fuzzy(self):
        s = _src()
        assert s.index("[src_slug], ctx.get(\"outcome\")") \
            < s.index('mapping_src = "exact" if mapping is not None')

    def test_it_is_time_boxed_and_instrumented(self):
        s = _src()
        i = s.index("[src_slug], ctx.get(\"outcome\")")
        block = s[i - 500:i + 500]
        assert "_EXACT_BOX_S" in block and "_ex_diag" in block

    def test_the_first_candidate_is_recorded_for_the_diagnostic(self):
        assert "_ex_cands = _ex_cands + [src_slug]" in _src()


class TestTheDerivativeGuardIsUntouched:
    """A prior review closed this: the candidate grammar drops the line
    suffix, so a SPREAD resolved exactly lands on the game's MONEYLINE,
    and a spread outcome is a team name that passes the outcome floor."""

    def test_the_identity_attempt_is_moneyline_only(self):
        s = _src()
        i = s.index("[src_slug], ctx.get(\"outcome\")")
        guard = s[:i]
        assert 'mtype == "moneyline"' in guard[-900:], \
            "a derivative must not reach the exact path"

    def test_the_reason_travels_with_the_restriction(self):
        assert "buying a moneyline instead of a" in _src()


class TestItIsSaferThanWhatItReplaces:
    def test_the_exact_resolver_refuses_a_two_sided_parent(self):
        s = inspect.getsource(pmus.resolve_market_exact)
        assert "_has_sides" in s and "not _has_sides" in s

    def test_it_requires_exactly_one_side_over_the_floor(self):
        assert "second_sc < MATCH_FLOOR" in \
            inspect.getsource(pmus.resolve_market_exact)

    def test_resolve_market_parity_has_NO_sides_guard(self):
        """Stated as a test so the reason for the routing survives."""
        s = inspect.getsource(pmus.resolve_market)
        head = s[:s.index('"matched_by": "slug"')]
        assert "_has_sides" not in head


class TestTheQuarantineNowAdmitsIt:
    def test_exact_is_in_the_resume_lane(self):
        assert "exact" in le.QUARANTINE_RESUME_SRC

    def test_fuzzy_still_is_not(self):
        assert "fuzzy" not in le.QUARANTINE_RESUME_SRC

    def test_yesno_lane_is_not_either(self):
        """The per-team Yes/No lane (2026-08-30) certifies under its
        own shadow counter; going live is a later one-token reviewed
        change made on that counter plus the audit rows."""
        assert "yesno_exact" not in le.QUARANTINE_RESUME_SRC
