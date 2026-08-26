"""The resume lane admits `exact`, and still refuses `fuzzy`.

Owner decision 2026-08-26, taken on a production probe showing the
sleeve refusing every copy it had successfully mapped:

    SHADOW-CERT ok=691  MISMATCH=0  unverified=0
    QUARANTINE true  env_override=none  premap_live=true
    MAPA-Q rn1     pick=Washington Mystics  quarantined (src=exact)
    MAPA-Q rn1     pick=Anastasia Zakharova quarantined (src=fuzzy)
    MAPA-Q ferrari pick=Arizona Diamondbacks quarantined (src=fuzzy)

The quarantine's own release condition -- an independent resolver
re-deriving each refused mapping and voting -- had been satisfied 691
times with no dissent, and the vector it was raised for is separately
closed: all six wrong-side receipts were ORDER_INTENT_BUY_SHORT, and
that branch is refused outright.

The owner chose the NARROW widening deliberately: admit `exact`, keep
`fuzzy` quarantined. These tests exist to hold that line, because the
easy drift from here is to admit everything.
"""

from __future__ import annotations

import inspect

from sportsassets import live_executor as le


class TestWhatTheLaneAdmits:
    def test_exact_is_admitted(self):
        assert "exact" in le.QUARANTINE_RESUME_SRC

    def test_premap_is_still_admitted(self):
        assert "premap" in le.QUARANTINE_RESUME_SRC

    def test_FUZZY_IS_NOT(self):
        """The class the 2026-08-23 wrong-side incident came through.
        If this ever passes, the owner's decision was reversed by
        somebody who did not say so."""
        assert "fuzzy" not in le.QUARANTINE_RESUME_SRC

    def test_nothing_else_crept_in(self):
        assert set(le.QUARANTINE_RESUME_SRC) == {"premap", "exact"}

    def test_it_is_a_frozen_set(self):
        """A mutable module-level allowlist is one import away from
        being edited at runtime by something that is not this decision."""
        assert isinstance(le.QUARANTINE_RESUME_SRC, frozenset)


class TestTheClassesAreRealAndDistinct:
    """`exact` is not a nicer word for `fuzzy`. The mapping code assigns
    it only after grammar-to-grammar resolution corroborates, and says
    so; everything weaker falls through."""

    def test_exact_comes_from_a_corroborating_resolver(self):
        src = inspect.getsource(le.maybe_execute)
        assert "resolve_market_exact" in src
        assert "resolve_derivative_exact" in src
        i = src.index('mapping_src = "exact"')
        j = src.index('mapping_src = "fuzzy"')
        assert i < j, "exact must be decided before fuzzy is reached"

    def test_fuzzy_is_the_fallthrough(self):
        """fuzzy is whatever the UNQUALIFIED resolver returns once both
        corroborating resolvers have declined. Windowed from the exact
        assignment rather than a fixed character count -- a slice width
        guessed against today's formatting is a test that passes on the
        wrong text tomorrow."""
        src = inspect.getsource(le.maybe_execute)
        tail = src[src.index('mapping_src = "exact"'):
                   src.index('mapping_src = "fuzzy"')]
        assert "pmus.resolve_market," in tail, \
            "fuzzy should be the unqualified resolver's output"
        assert "resolve_market_exact" not in tail

    def test_the_distinction_is_stated_where_the_constant_lives(self):
        src = inspect.getsource(le)
        block = src[src.index("QUARANTINE_RESUME_SRC = ") - 2000:
                    src.index("QUARANTINE_RESUME_SRC = ")]
        assert "full corroboration" in block
        assert "691" in block, \
            "the evidence the decision rests on must travel with it"


class TestEveryOtherGateIsInherited:
    """The widening is one thing only. `exact` rides the SAME resume
    lane as `premap`: same whale allowlist, same operator switch."""

    def test_the_whale_allowlist_still_applies(self):
        src = inspect.getsource(le.maybe_execute)
        gate = src[src.index("QUARANTINE_RESUME_SRC"):]
        gate = gate[:gate.index("_q_on and not _premap_ok")]
        assert "username in _allowed" in gate

    def test_the_premap_live_switch_still_applies(self):
        src = inspect.getsource(le.maybe_execute)
        i = src.index("QUARANTINE_RESUME_SRC")
        after = src[i:i + 1400]
        assert "premap_live" in after
        assert 'LIVE_PREMAP' in after

    def test_an_unreadable_switch_still_REFUSES(self):
        """Fail-safe direction unchanged by the widening."""
        src = inspect.getsource(le.maybe_execute)
        i = src.index("QUARANTINE_RESUME_SRC")
        after = src[i:i + 1400]
        assert "_premap_ok = False" in after
        assert "fail safe: refuse" in after

    def test_the_short_branch_is_still_refused(self):
        """The actual vector of the incident the quarantine was raised
        for. Widening the mapping lane must not touch it."""
        src = inspect.getsource(le.maybe_execute)
        assert "short-branch-refused" in src

    def test_the_refusal_reason_names_the_real_class(self):
        """It hard-coded 'src=premap' in the allowlist refusal, so an
        `exact` mapping refused for the wrong whale would have reported
        a class it did not have."""
        src = inspect.getsource(le.maybe_execute)
        assert 'f"(src=premap, slug=' not in src
        assert 'f"(src={mapping_src}, ' in src


class TestTheCertificationStillCollectsEvidence:
    def test_shadow_echo_covers_every_admitted_class(self):
        """The streak the resume decision reads is produced by refused
        rows. If it only ever covered `premap`, a future decision about
        `exact` would have no evidence behind it."""
        src = inspect.getsource(le.maybe_execute)
        i = src.index("_spawn_echo(pool, row_id")
        head = src[i - 300:i]
        assert "QUARANTINE_RESUME_SRC" in head
        assert 'mapping_src == "premap"' not in head
