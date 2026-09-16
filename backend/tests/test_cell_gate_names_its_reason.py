"""'The cell gate refused it' names a function, not a cause.

The first pre-INSERT census (2026-08-31) reported:

    COPYCENSUS cell_gate|rn1: 198

— the largest genuine block on the roster's only whale, since his other
530 refusals are exits being correctly classified as exits. And it was
not actionable, because copy_allowed has SIX independent ways to say no
and rn1 is in UNRESTRICTED, which rules four of them out. Reading the
constants said "almost certainly props". Almost certainly is not a
measurement, and this codebase's whole standard is that a number comes
from an instrument.

copy_verdict returns WHICH clause refused. copy_allowed is defined in
terms of it, so the boolean and the explanation cannot drift — a reason
that disagrees with the decision it explains is worse than no reason,
because it is believed.
"""
import inspect

import pytest

from sportsassets import copy_sports as cs
from sportsassets import live_executor as le


class TestTheVerdictAndTheBooleanCannotDisagree:
    def test_copy_allowed_delegates(self):
        src = inspect.getsource(cs.copy_allowed)
        assert "copy_verdict(" in src, (
            "copy_allowed reimplements the logic, so the reason can "
            "drift from the decision")

    @pytest.mark.parametrize("whale,slug,price", [
        ("rn1", "aec-atp-a-b-2026-09-01", 0.5),
        ("rn1", "tsc-mlb-nyy-bos-2026-09-01-prop-hr", 0.5),
        ("rn1", "soc-epl-ars-che-2026-09-01", 0.1),
        ("rn1", "soc-epl-ars-che-2026-09-01", None),
        ("", "anything", 0.5),
        ("nobody-at-all", "aec-atp-a-b-2026-09-01", 0.5),
        ("rn1", "", 0.5),
    ])
    def test_equivalence_across_shapes(self, whale, slug, price):
        assert cs.copy_allowed(whale, slug, price) == \
            (cs.copy_verdict(whale, slug, price) is None)


class TestTheReasonsAreDistinguishable:
    def test_an_allowed_copy_returns_none(self):
        assert cs.copy_verdict("rn1", "aec-atp-a-b-2026-09-01", 0.5) is None

    def test_a_blocked_market_type_says_so(self):
        """BLOCKED_TYPES = {"prop"}, and this is the hypothesis the
        census could not confirm on its own."""
        assert "prop" in cs.BLOCKED_TYPES
        slug = next((s for s in
                     ["tsc-mlb-nyy-bos-2026-09-01-prop",
                      "aec-atp-a-b-2026-09-01-prop"]
                     if cs.market_type_of(s) == "prop"), None)
        if slug is None:
            pytest.skip("no slug shape in reach parses as a prop")
        assert cs.copy_verdict("rn1", slug, 0.5) == "market_type_blocked"

    def test_an_empty_whale_is_its_own_reason(self):
        assert cs.copy_verdict("", "x", 0.5) == "no_whale"

    def test_an_unknown_whale_is_not_confused_with_a_blocked_market(self):
        """Both refuse; a shared bucket would hide which."""
        a = cs.copy_verdict("nobody-at-all", "aec-atp-a-b-2026-09-01", 0.5)
        assert a == "no_cells_for_whale"

    def test_every_reason_string_is_unique(self):
        src = inspect.getsource(cs.copy_verdict)
        import re
        names = re.findall(r'return "([a-z_]+)"', src)
        assert len(names) == len(set(names)), \
            f"duplicate verdict names: {names}"


class TestTheExecutorRecordsIt:
    def test_the_census_key_carries_the_clause(self):
        src = inspect.getsource(le.maybe_execute)
        assert '"cell_gate_" + _cv' in src, (
            "the executor still records a bare cell_gate, so the "
            "census cannot say which clause fired")

    def test_copy_allowed_remains_the_decision(self):
        """The first version of this change asked copy_verdict for the
        DECISION, which moved the seam and broke eleven stubs across
        three test files that patch copy_allowed to bypass this gate.
        Only the reason was ever needed."""
        src = inspect.getsource(le.maybe_execute)
        assert "if not copy_allowed(" in src, (
            "the decision moved off copy_allowed; every existing stub "
            "of that seam silently stops applying")

    def test_the_verdict_is_consulted_only_on_the_refusal_path(self):
        """A diagnostic that runs on the allow path can slow or, worse,
        alter one. Inside the refusal branch it cannot do either."""
        src = inspect.getsource(le.maybe_execute)
        d = src.index("if not copy_allowed(")
        v = src.index("copy_verdict(", d)
        between = src[d:v]
        assert "return _copy_stop" not in between, \
            "the verdict is read after the stop, so it never runs"
        assert between.count("\n") < 12, \
            "the verdict call is not inside the refusal branch"

    def test_a_none_verdict_cannot_become_a_bucket_name(self):
        """A stub can force the boolean False while the verdict says
        allowed. 'cell_gate_None' would be a bucket named after a bug
        in the diagnostic itself."""
        src = inspect.getsource(le.maybe_execute)
        assert 'or "unnamed"' in src
