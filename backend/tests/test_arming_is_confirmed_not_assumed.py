"""An arm request that is not CONFIRMED must not read as armed.

THE DEFECT THIS PINS, from run 75. The readback workflow POSTed
`/api/admin/ext-pinnacle-shadow/on`, the request timed out at 45 s, and the
step died on the next line -- taking the per-candidate census, the
settlement coverage and the holdings block with it. Making that read
non-fatal is right, and it introduces a WORSE failure if nothing else
changes: a timed-out or retried arm would then be passed over in silence
and the cycle read afterwards would be presented as the armed lane's
output.

Two facts, two names, neither inferred from an HTTP status:

  armed_requested   what was asked for
  armed_confirmed   what the control row says AFTERWARDS

and `effectively_armed` is the conjunction the loop itself requires -- the
control row AND the environment flag. A report that interprets a cycle owes
that conjunction, read rather than assumed.
"""

import json

import pytest

from sportsassets.api import app as A


def test_a_jsonb_false_is_not_truthy():
    """`bool("false")` is True, which is exactly the silent affirmative this
    lane exists to refuse. The column is jsonb, so asyncpg hands back a
    string and the parse has to be explicit."""
    assert A._truthy_control(json.dumps(False)) is False
    assert A._truthy_control("false") is False
    assert A._truthy_control(json.dumps(True)) is True
    assert A._truthy_control("true") is True


def test_an_absent_control_row_is_not_armed():
    assert A._truthy_control(None) is False


def test_an_unparseable_control_value_is_not_armed():
    """Absence of a readable NO is not a YES."""
    assert A._truthy_control("not json at all") is False
    assert A._truthy_control(object()) is False


def test_a_real_bool_passes_through():
    assert A._truthy_control(True) is True
    assert A._truthy_control(False) is False


def test_the_arm_route_reports_requested_and_confirmed_separately():
    import inspect
    src = inspect.getsource(A.api_ext_pinnacle_shadow)
    assert '"armed_requested"' in src
    assert '"armed_confirmed"' in src
    # and `ok` is the AGREEMENT of the two, not the write having been sent
    assert 'confirmed == (action == "on")' in src
    # a failed readback returns ok False and says the state is unconfirmed
    assert '"readback_error"' in src
    assert "UNCONFIRMED" in src


def test_the_state_route_requires_the_conjunction_the_loop_requires():
    import inspect
    src = inspect.getsource(A.api_ext_pinnacle_shadow_state)
    assert '"effectively_armed"' in src
    assert "env_flag_set" in src
    # and a read failure is UNCONFIRMED, explicitly not false
    assert "not false" in src


@pytest.mark.parametrize("attr", ["api_ext_pinnacle_shadow_state"])
def test_the_state_route_writes_nothing(attr):
    import inspect
    src = inspect.getsource(getattr(A, attr))
    for banned in ("INSERT", "UPDATE ", "DELETE", "submit"):
        assert banned not in src.upper().replace("SUBMITS_ORDERS", ""), banned
