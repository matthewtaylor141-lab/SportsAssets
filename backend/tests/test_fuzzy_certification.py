"""Measure the fuzzy class before deciding anything about it.

The owner's goal, stated 2026-08-26: "fix the fuzziness so that the
answer really is #1, no trades missed because of fuzziness because all
trades are verified."

Tonight's production probe showed why that matters. Of the mappings the
quarantine refused in one four-second window, 20 of 21 were src=fuzzy.
Admitting `exact` unlocks roughly a twentieth of the held flow; the rest
is fuzzy.

And there is NO evidence about fuzzy either way. The side echo -- which
re-derives a mapping from LIVE venue rows through an independent matcher
and requires exact identifier AND intent agreement -- produced
691 ok / 0 mismatch for premap, and that streak is what justified the
`exact` resume. It has never once been pointed at fuzzy. "We do not
trust fuzzy" rests on the 2026-08-23 incident: six orders, all of them
ORDER_INTENT_BUY_SHORT, on a branch that is now refused outright.

So the class the owner wants back is the one class nobody has measured.
This spends no money and moves no gate: a fuzzy mapping is still
refused, then certified, under its OWN counter. In a few hours that is a
number, which is the only honest basis for widening the lane or leaving
it shut.
"""

from __future__ import annotations

import inspect

from sportsassets import live_executor as le


class TestFuzzyIsCertifiedNow:
    def test_a_refused_fuzzy_mapping_spawns_an_echo(self):
        src = inspect.getsource(le.maybe_execute)
        assert 'elif mapping_src == "fuzzy":' in src
        block = src[src.index('elif mapping_src == "fuzzy":'):]
        block = block[:block.index("log.warning")]
        assert "_spawn_echo(" in block

    def test_it_runs_in_SHADOW_mode(self):
        """No money rode, so a mismatch here must not trip the circuit.
        _side_echo_verify's auto-requarantine is gated on `not shadow`,
        and a pre-trade refusal reaching it would re-arm the total
        quarantine and drop premap-live -- killing the `exact` lane the
        owner just opened, on evidence about a class that never
        traded."""
        src = inspect.getsource(le.maybe_execute)
        block = src[src.index('elif mapping_src == "fuzzy":'):]
        block = block[:block.index("log.warning")]
        assert "shadow=True" in block

    def test_the_requarantine_is_still_gated_on_a_real_fill(self):
        src = inspect.getsource(le._side_echo_verify)
        assert 'if verdict == "mismatch" and not shadow:' in src

    def test_it_carries_the_mapping_source(self):
        """_independent_check takes mapping_src. Certifying fuzzy while
        telling the verifier nothing about the class would produce a
        verdict about a different resolution than the one under test --
        the recurring failure here is an instrument reading different
        inputs than production."""
        src = inspect.getsource(le.maybe_execute)
        block = src[src.index('elif mapping_src == "fuzzy":'):]
        block = block[:block.index("log.warning")]
        assert "mapping_src=mapping_src" in block


class TestTheStreaksDoNotContaminateEachOther:
    def test_fuzzy_has_its_own_key(self):
        assert le.FUZZY_CERT_KEY == "side_echo_fuzzy"
        assert le.FUZZY_CERT_KEY != "side_echo_shadow"

    def test_the_fuzzy_echo_passes_that_key(self):
        src = inspect.getsource(le.maybe_execute)
        block = src[src.index('elif mapping_src == "fuzzy":'):]
        block = block[:block.index("log.warning")]
        assert "state_key=FUZZY_CERT_KEY" in block

    def test_the_admitted_classes_still_use_the_ORIGINAL_key(self):
        """691/0 justified a decision. Diluting that counter with a
        different question makes the number that was relied on
        unreadable afterwards. (The slice ends at the FIRST elif —
        the yes/no lane's arm sits between this one and fuzzy since
        2026-08-30 and rightly carries its OWN key, which is this
        pin's spirit, not a violation of it.)"""
        src = inspect.getsource(le.maybe_execute)
        block = src[src.index("if mapping_src in QUARANTINE_RESUME_SRC:"):]
        block = block[:block.index("elif mapping_src ==")]
        assert "state_key" not in block

    def test_the_yesno_lane_has_its_own_key_too(self):
        assert le.YESNO_CERT_KEY == "side_echo_yesno"
        assert le.YESNO_CERT_KEY != le.FUZZY_CERT_KEY
        src = inspect.getsource(le.maybe_execute)
        block = src[src.index('elif mapping_src == "yesno_exact":'):]
        block = block[:block.index('elif mapping_src == "fuzzy":')]
        assert "state_key=YESNO_CERT_KEY" in block

    def test_the_default_key_is_unchanged_for_every_old_caller(self):
        src = inspect.getsource(le._side_echo_verify)
        assert 'state_key or ("side_echo_shadow" if shadow' in src
        assert '"side_echo_last"' in src

    def test_the_fuzzy_streak_is_readable(self):
        from sportsassets.api import app as A

        src = inspect.getsource(A)
        assert '("side_echo_fuzzy", "side_echo_fuzzy")' in src, \
            "a measurement nobody can read is not a measurement"


class TestTheVerdictIsReachable:
    def test_the_verifier_returns_it(self):
        """It returned None, so the verdict -- the whole product of the
        function -- could only be read back out of the database. A
        future pre-trade gate on verified-fuzzy needs it in hand."""
        src = inspect.getsource(le._side_echo_verify)
        assert src.rstrip().endswith("return verdict")

    def test_the_signature_says_so(self):
        assert inspect.signature(
            le._side_echo_verify).return_annotation == "str"

    def test_nothing_has_started_trading_on_it_yet(self):
        """This change certifies; it does not admit. If fuzzy ever
        enters the resume set it must be a separate, deliberate edit
        with the streak to justify it."""
        assert "fuzzy" not in le.QUARANTINE_RESUME_SRC


class TestTheEvidenceBarIsNotLowered:
    """The verifier's strictness is the reason its verdict means
    anything. These are the properties that make a 'ok' worth acting
    on, and certifying a new class must not have relaxed any of them."""

    def test_containment_is_not_proof(self):
        src = inspect.getsource(le._side_echo_verify)
        assert "CONTAINMENT IS NOT IDENTITY" in src
        assert "only \"\n                              f\"partially matches" in src \
            or "partially matches" in src

    def test_a_venue_outage_is_unverified_not_ok(self):
        src = inspect.getsource(le._side_echo_verify)
        assert 'detail = "venue unreachable"' in src
        i = src.index('detail = "venue unreachable"')
        assert 'verdict = "ok"' not in src[i:i + 200]

    def test_intent_disagreement_is_a_mismatch(self):
        """Identifier equality is not side equality -- both sides of a
        two-sided market share one identifier."""
        src = inspect.getsource(le._side_echo_verify)
        assert "IDENTIFIER EQUALITY IS NOT SIDE EQUALITY" in src

    def test_a_second_independent_resolver_still_votes(self):
        src = inspect.getsource(le._side_echo_verify)
        assert "_independent_check(" in src
