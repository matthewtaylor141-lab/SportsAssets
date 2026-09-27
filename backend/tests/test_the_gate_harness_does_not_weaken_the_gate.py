"""The harness satisfies the gate. It must not be able to bypass it.

`tests/gate_harness.authorized_gate` exists so that 55 tests in
tests/test_pmus_post_only.py stop failing on `authorization_unavailable`
and start covering the params dict and refusal semantics they were
written to pin. That is only a repair if the gate still refuses whenever
a control says it should -- otherwise it is a bypass wearing a fixture's
name, and the 55 passes would be worth less than the failures they
replaced.

So every control is driven THROUGH the harness and asserted to deny:
the kill switch, the venue, the copy halt, the loss stop and the
overspend halt. The gate's own `read_state` and `_decide` run unmodified
in all of them; nothing here patches `authorize`.
"""

import pytest

from sportsassets import execution_gate as eg

from tests.gate_harness import PoolDouble, authorized_gate


# ═════════════════════════════════════════════════════════════════════
# THE UNBOUND STATE STILL DENIES
# ═════════════════════════════════════════════════════════════════════

def test_without_the_harness_the_gate_refuses():
    """The baseline the 55 failures were reporting. It has not changed."""
    eg.unbind()
    with pytest.raises(eg.Denied) as e:
        eg.authorize("submit", lane="mirror")
    assert e.value.reason == "authorization_unavailable"


def test_after_the_harness_exits_the_gate_refuses_again(monkeypatch):
    """An escaped binding would authorize later tests. It must not escape."""
    with authorized_gate(monkeypatch):
        eg.authorize("submit", lane="mirror")
    with pytest.raises(eg.Denied) as e:
        eg.authorize("submit", lane="mirror")
    assert e.value.reason == "authorization_unavailable"


def test_the_harness_unbinds_even_when_the_body_raises(monkeypatch):
    with pytest.raises(RuntimeError):
        with authorized_gate(monkeypatch):
            raise RuntimeError("boom")
    with pytest.raises(eg.Denied):
        eg.authorize("submit", lane="mirror")


# ═════════════════════════════════════════════════════════════════════
# EVERY CONTROL STILL DENIES, DRIVEN THROUGH THE HARNESS
# ═════════════════════════════════════════════════════════════════════

def test_the_default_harness_authorizes(monkeypatch):
    with authorized_gate(monkeypatch):
        snap = eg.authorize("submit", lane="mirror")
    assert snap.ok is True


def test_the_kill_switch_still_denies(monkeypatch):
    with authorized_gate(monkeypatch, paused=True):
        with pytest.raises(eg.Denied) as e:
            eg.authorize("submit", lane="mirror")
    assert e.value.reason == "live_trading_paused"


def test_no_active_venue_still_denies(monkeypatch):
    with authorized_gate(monkeypatch, venue=None):
        with pytest.raises(eg.Denied) as e:
            eg.authorize("submit", lane="mirror")
    assert e.value.reason == "no_active_venue"


def test_the_copy_halt_still_denies(monkeypatch):
    with authorized_gate(monkeypatch, copy_halted=True):
        with pytest.raises(eg.Denied) as e:
            eg.authorize("submit", lane="mirror")
    assert e.value.reason == "copy_halted"


def test_the_loss_stop_still_denies(monkeypatch):
    with authorized_gate(monkeypatch, loss_stop=True):
        with pytest.raises(eg.Denied) as e:
            eg.authorize("submit", lane="mirror")
    assert e.value.reason == "mirror_loss_stop"


def test_the_overspend_halt_still_denies(monkeypatch):
    with authorized_gate(monkeypatch, overspend=True):
        with pytest.raises(eg.Denied) as e:
            eg.authorize("submit", lane="mirror")
    assert e.value.reason is not None


def test_an_unknown_lane_gets_the_copy_controls_too(monkeypatch):
    """A route that never declared itself must not be the one that escapes."""
    with authorized_gate(monkeypatch, copy_halted=True):
        with pytest.raises(eg.Denied):
            eg.authorize("submit")            # lane defaults to 'unknown'


# ═════════════════════════════════════════════════════════════════════
# THE GATE ACTUALLY READS THE DOUBLE
# ═════════════════════════════════════════════════════════════════════

def test_the_gate_reads_the_kill_switch_on_every_authorization(monkeypatch):
    """A harness that authorized without a read would mean the gate had
    stopped consulting its controls -- the fail-open this repository has
    already been bitten by twice."""
    with authorized_gate(monkeypatch) as pool:
        eg.authorize("submit", lane="mirror")
        first = len(pool.queries)
        eg.authorize("submit", lane="mirror")
        second = len(pool.queries)
    assert first > 0
    assert second > first, "the second authorization reused a cached read"


def test_the_switch_is_parsed_from_real_json_text_not_a_bool(monkeypatch):
    """`_parse_switch` does its real work: the double stores JSON text."""
    p = PoolDouble(paused=False)
    assert p.pause_value == "false"
    assert isinstance(p.pause_value, str)
    p2 = PoolDouble(paused=True)
    assert p2.pause_value == "true"


@pytest.mark.parametrize("bad,cause", [
    ("0", "kill_switch_not_boolean"),
    ("[]", "kill_switch_not_boolean"),
    ("{}", "kill_switch_not_boolean"),
    ("null", "kill_switch_not_boolean"),
    ("not json", "kill_switch_malformed"),
])
def test_a_non_boolean_switch_value_still_denies(monkeypatch, bad, cause):
    """The 2026-09-21 defect: truthiness is not JSON's boolean.

    `read_state` returns early with ok=False on a malformed switch, so
    `_decide` reports `authorization_unavailable` and carries the cause
    in the detail. The cause is what is asserted -- the point is that
    none of these five values releases the switch.
    """
    with authorized_gate(monkeypatch) as pool:
        pool.pause_value = bad
        with pytest.raises(eg.Denied) as e:
            eg.authorize("submit", lane="mirror")
    assert cause in str(e.value), str(e.value)


def test_an_absent_kill_switch_row_is_not_permission(monkeypatch):
    with authorized_gate(monkeypatch) as pool:
        pool.pause_value = None
        with pytest.raises(eg.Denied) as e:
            eg.authorize("submit", lane="mirror")
    assert "absence is not" in str(e.value), str(e.value)


def test_a_query_the_double_does_not_model_fails_loudly(monkeypatch):
    """A new read added to read_state must not silently return None."""
    p = PoolDouble()
    import asyncio
    with pytest.raises(AssertionError) as e:
        asyncio.run(p.fetchval("SELECT value FROM ingestion_state "
                               "WHERE key='something_new'"))
    assert "does not model" in str(e.value)


# ═════════════════════════════════════════════════════════════════════
# THE HARNESS CANNOT REACH PRODUCTION
# ═════════════════════════════════════════════════════════════════════

def test_the_harness_is_not_in_the_shipped_package():
    import pathlib

    import sportsassets
    pkg = pathlib.Path(sportsassets.__file__).parent
    assert not (pkg / "gate_harness.py").exists()
    assert list(pkg.rglob("gate_harness*")) == []


def test_no_production_module_imports_the_harness():
    import pathlib

    import sportsassets
    pkg = pathlib.Path(sportsassets.__file__).parent
    offenders = [p for p in pkg.rglob("*.py")
                 if "gate_harness" in p.read_text()]
    assert offenders == [], [str(p) for p in offenders]


def test_the_harness_never_touches_live_trading_enabled_or_a_credential():
    """Point 2 of its docstring, checked rather than trusted."""
    import inspect

    from tests import gate_harness
    src = inspect.getsource(gate_harness)
    body = src.split('"""', 2)[2]          # past the module docstring
    for forbidden in ("live_trading_enabled", "pmus_key_id",
                      "pmus_secret_key", "pm_private_key", "admin_token"):
        assert forbidden not in body, (
            "%s appears in the harness body; it must substitute "
            "active_venue instead of arming the real config" % forbidden)


def test_the_harness_does_not_patch_authorize_or_decide():
    import inspect

    from tests import gate_harness
    src = inspect.getsource(gate_harness)
    body = src.split('"""', 2)[2]
    for forbidden in ("authorize", "_decide", "_authorize_read",
                      "read_state"):
        assert "setattr(eg, %r" % forbidden not in body
        assert 'setattr(eg, "%s"' % forbidden not in body


def test_the_harness_binds_a_real_loop_on_another_thread(monkeypatch):
    """Which is how production reaches the gate, via asyncio.to_thread."""
    import asyncio
    with authorized_gate(monkeypatch):
        loop = eg._B.loop
        assert isinstance(loop, asyncio.AbstractEventLoop)
        assert loop.is_running()
        with pytest.raises(RuntimeError):
            asyncio.get_running_loop()      # this thread has no loop
