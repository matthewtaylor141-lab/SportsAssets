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


def _code(fn) -> str:
    """The function's source with its comment lines removed. The
    mapping gate was factored out of maybe_execute into
    _mapping_admitted (mirror P1 step 1), and maybe_execute keeps a
    prose summary that repeats every phrase pinned below -- so a pin
    read off raw source would keep passing after the code it names
    was deleted. Every pin here matches CODE, in the function that
    runs it."""
    return "\n".join(ln for ln in inspect.getsource(fn).splitlines()
                     if not ln.lstrip().startswith("#"))


def _resume_lane() -> str:
    """The resume-lane block of _mapping_admitted's code: from the
    first QUARANTINE_RESUME_SRC test to the refusal it decides.
    Windowed by landmarks, never by a character count."""
    src = _code(le._mapping_admitted)
    lane = src[src.index("QUARANTINE_RESUME_SRC"):]
    return lane[:lane.index("_q_on and not _premap_ok")]


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
        src = _code(le.maybe_execute)
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
        src = _code(le.maybe_execute)
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
    lane as `premap`: same whale allowlist, same operator switch.

    The gates run in _mapping_admitted now; maybe_execute calls it and
    keeps only a prose summary, so every pin below reads the helper's
    CODE (comment lines stripped) and the first test holds the call."""

    def test_maybe_execute_runs_the_gate_through_the_helper(self):
        """The pins below are on _mapping_admitted. They mean nothing
        unless the copy lane still asks it, hands it the real class,
        and writes its refusal onto the row."""
        src = _code(le.maybe_execute)
        i = src.index("await _mapping_admitted(")
        j = src.index("if not _adm_ok:", i)
        assert "pool, username, mapping_src" in src[i:j]
        refusal = src[j:src.index("return", j)]
        assert "status='rejected'" in refusal
        assert "_q_reason" in refusal

    def test_the_whale_allowlist_still_applies(self):
        assert "username in _allowed" in _resume_lane()
        helper = _code(le._mapping_admitted)
        assert helper.index('_allowed = _whale_set("LIVE_PREMAP_WHALES")') \
            < helper.index("QUARANTINE_RESUME_SRC")

    def test_the_premap_live_switch_still_applies(self):
        lane = _resume_lane()
        assert '"premap_live"' in lane
        assert '"LIVE_PREMAP"' in lane

    def test_an_unreadable_switch_still_REFUSES(self):
        """Fail-safe direction unchanged by the widening: the lane
        starts refused, and the handler on the premap_live read puts
        it back to refused."""
        lane = _resume_lane()
        assert "_premap_ok = False" in lane
        assert "fail safe: refuse" in lane
        helper = _code(le._mapping_admitted)
        assert helper.index("_premap_ok = False") \
            < helper.index("QUARANTINE_RESUME_SRC"), \
            "the lane must start refused"
        i = lane.index('"premap_live"')
        handler = lane[lane.index("except Exception", i):].splitlines()
        assert handler[1].strip() == "_premap_ok = False", handler[:2]

    def test_the_short_branch_is_still_refused(self):
        """The actual vector of the incident the quarantine was raised
        for. Widening the mapping lane must not touch it."""
        src = _code(le.maybe_execute)
        assert "short-branch-refused" in src

    def test_the_refusal_reason_names_the_real_class(self):
        """It hard-coded 'src=premap' in the allowlist refusal, so an
        `exact` mapping refused for the wrong whale would have reported
        a class it did not have."""
        helper = _code(le._mapping_admitted)
        assert 'f"(src=premap, slug=' not in helper
        assert 'f"(src=premap, slug=' not in _code(le.maybe_execute)
        assert 'f"(src={mapping_src}, ' in helper


class TestTheCertificationStillCollectsEvidence:
    def test_shadow_echo_covers_every_admitted_class(self):
        """The streak the resume decision reads is produced by refused
        rows. If it only ever covered `premap`, a future decision about
        `exact` would have no evidence behind it."""
        src = _code(le.maybe_execute)
        i = src.index("_spawn_echo(pool, row_id")
        head = src[i - 300:i]
        assert "QUARANTINE_RESUME_SRC" in head
        assert 'mapping_src == "premap"' not in head
