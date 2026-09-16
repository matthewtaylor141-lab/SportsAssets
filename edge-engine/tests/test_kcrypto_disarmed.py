"""The crypto copy leg cannot spend on whales the bar cannot measure.

Owner requirement, 2026-08-30: "I need you to always ensure
mathematical and statistical profit... at least 95% statistically
proven profitable."

This leg copies two whales at up to $250 an order. Both are
deliberately excluded from COPY_WHALES, so workers/analytics.py never
grades them and no edge_ci95 for them can exist — their edge is not
merely unproven, it is structurally unmeasurable. An adversarial
review of the 95% gate found this was the single money path that
bypassed every enforcement point, while defaulting to ARMED.

Production read at 2026-08-30T13:41Z was "seen 4979 placed 0 filled 0
deployed $0.0": armed, evaluating thousands of candidates, and one
qualifying candidate away from spending. Disarming cost nothing.
"""

import os
from unittest import mock


def _sweep_halted(env):
    from edge.shadow import kalshi_crypto as kcr

    kcr.funnel.clear()
    with mock.patch.dict(os.environ, env, clear=False):
        kcr.sweep(client=None, ledger=None, risk=None, candidates=[])
    return kcr.funnel.get("halted")


class TestTheLegIsOffUnlessSomeoneArmsIt:
    def test_absent_env_does_not_arm(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("EDGE_KCRYPTO", None)
            assert _sweep_halted({}) is not None, \
                "an unset flag must leave the leg disarmed"

    def test_zero_does_not_arm(self):
        assert _sweep_halted({"EDGE_KCRYPTO": "0"}) is not None

    def test_a_stray_value_does_not_arm(self):
        # The two arming checks used to disagree: the runner armed on
        # anything != "0" while the sweep halted on anything != "1", so
        # EDGE_KCRYPTO=2 started the loop and halted every pass. Both
        # now test == "1", so a typo fails closed rather than half-arming.
        assert _sweep_halted({"EDGE_KCRYPTO": "2"}) is not None
        assert _sweep_halted({"EDGE_KCRYPTO": "true"}) is not None
        assert _sweep_halted({"EDGE_KCRYPTO": ""}) is not None

    def test_only_an_explicit_one_arms_it(self):
        # Getting PAST the arm check is the property under test. With a
        # None ledger the very next call (live_blocked) raises, so the
        # raise IS the evidence it proceeded — whereas every disarmed
        # case above returns cleanly with `halted` set and never
        # touches the ledger at all. Asserting the raise keeps this
        # test honest without standing up a whole ledger fake, which
        # would be testing the fake rather than the gate.
        import pytest

        from edge.shadow import kalshi_crypto as kcr

        kcr.funnel.clear()
        with mock.patch.dict(os.environ, {"EDGE_KCRYPTO": "1"}, clear=False):
            with pytest.raises(AttributeError):
                kcr.sweep(client=None, ledger=None, risk=None, candidates=[])
        assert "not armed" not in str(kcr.funnel.get("halted"))


class TestBothArmingSitesAgree:
    def test_the_runner_arms_only_on_an_explicit_one(self):
        import inspect

        from edge.shadow import runner

        src = inspect.getsource(runner)
        assert 'os.environ.get("EDGE_KCRYPTO", "0") == "1"' in src, \
            "the runner must arm on an explicit 1 against a default of 0"
        assert 'os.environ.get("EDGE_KCRYPTO", "1")' not in src, \
            "a default of 1 arms a leg whose whales cannot be graded"

    def test_the_sweep_arms_only_on_an_explicit_one(self):
        import inspect

        from edge.shadow import kalshi_crypto as kcr

        src = inspect.getsource(kcr.sweep)
        assert 'os.environ.get("EDGE_KCRYPTO", "0") != "1"' in src


class TestTheWhalesReallyAreUngraded:
    def test_the_leg_whales_are_not_in_the_graded_copy_set(self):
        # This is WHY the leg has to be off: the bar is computed over
        # COPY_WHALES, so a whale outside that set has no interval and
        # can never be shown profitable at 95%.
        import sys
        from pathlib import Path

        backend = Path(__file__).resolve().parents[2] / "backend"
        if str(backend) not in sys.path:
            sys.path.insert(0, str(backend))
        from sportsassets.api.copies_record import COPY_WHALES

        from edge.shadow.kalshi_crypto import WHALES

        for w in WHALES:
            assert w.lower() not in {c.lower() for c in COPY_WHALES}, \
                (f"{w} is graded after all — if that changed, this leg "
                 f"can be brought under the 95% gate instead of held off")
