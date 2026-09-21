"""The execution gate, exercised through the real boundary functions.

WHAT THESE PROVE. Not that a helper returns False -- that a blocked
route reaches the venue adapter ZERO times, and that an authorized one
still works. The venue client is replaced by a recorder that counts
every call it receives; if a denial leaked, the count would be 1.

NO PRODUCTION FLAG IS CHANGED ANYWHERE IN THIS FILE. The gate's state is
supplied per test through the test-only snapshot hook, which refuses to
run outside pytest. The environment, the database and the live switches
are untouched, and no test here can submit an order: the adapter is a
mock and the gate denies by default.

THE DEFECTS THESE DESCEND FROM (audit 2026-09-21):

    mirror_exit             never called active_venue() at all
    _execute_manual_sell    called it, but neither _is_paused() nor
                            copy_halted() -- the documented kill switch
                            did not stop the manual sell
    whale_exits             armed by the ABSENCE of an env var
    live_trading_paused     row absent, so _is_paused returned False and
                            the kill switch had never been armed

Each was a different hole at a different caller. The gate exists so the
next route is covered before anyone notices it was written.
"""

import asyncio
import time

import pytest

from sportsassets import execution_gate as gate


# ── a venue that records instead of trading ──────────────────────────

class RecordingVenue:
    """Stands in for the SDK client. Counts everything."""

    def __init__(self):
        self.creates = []
        self.previews = []
        self.closes = []
        self.cancels = []

    # orders.*
    @property
    def orders(self):
        return self

    @property
    def portfolio(self):
        return self

    def create(self, params=None):
        self.creates.append(params)
        return {"id": "ord-1", "executions": []}

    def preview(self, params=None):
        """A preview faithful enough to get past the venue-cost guard.

        submit_fok refuses a preview that states no cost -- a real
        control from 2026-08-25, when _order_cost failed open and five
        fills took 1.15x to 3.87x the authorized clip. The mock has to
        answer like the venue or the authorized-path tests would pass
        for the wrong reason: the gate would look like it blocked
        something that a completely different guard stopped.
        """
        self.previews.append(params)
        # The SDK wraps the order under "request".
        o = (params or {}).get("request") or (params or {}).get("order") \
            or params or {}
        qty = float(o.get("quantity") or 0)
        p = o.get("price")
        px = float((p or {}).get("value") if isinstance(p, dict) else (p or 0))
        return {"order": {"cashOrderQty": {"value": qty * px},
                          "price": {"value": px},
                          "quantity": qty}}

    def close_position(self, params=None):
        self.closes.append(params)
        return {"id": "ord-2", "executions": []}

    def cancel(self, params=None):
        self.cancels.append(params)
        return {"ok": True}

    @property
    def submissions(self):
        return len(self.creates) + len(self.closes)


@pytest.fixture
def venue(monkeypatch):
    """Install the recorder in place of the real client, for every test
    in this file, so nothing here can reach a venue even by mistake."""
    from sportsassets import pmus
    v = RecordingVenue()
    monkeypatch.setattr(pmus, "_get_client", lambda: v)
    monkeypatch.setattr(pmus, "_client", v, raising=False)
    return v


@pytest.fixture(autouse=True)
def clean_gate():
    yield
    gate._restore_for_tests()


def _snap(**over):
    """An otherwise-permissive state, so each test changes ONE thing and
    the denial it gets can only have come from that thing."""
    base = dict(paused=False, venue="polymarket-us", copy_halted=False,
                loss_stop=False, overspend=False, read_at=time.time(),
                ok=True, why="test")
    base.update(over)
    return gate.Snapshot(**base)


def _arm(**over):
    gate._install_snapshot_for_tests(_snap(**over))


# ── 1. blocked routes make ZERO venue submissions ────────────────────

DENYING_STATES = [
    ("kill switch engaged", dict(paused=True), "live_trading_paused"),
    ("no active venue", dict(venue=None), "no_active_venue"),
    ("copy halted", dict(copy_halted=True), "copy_halted"),
    ("loss breaker tripped", dict(loss_stop=True), "mirror_loss_stop"),
    ("spend breaker tripped", dict(overspend=True), "copy_overspend_halt"),
    ("state unreadable", dict(ok=False, why="db down"),
     "authorization_unavailable"),
]


@pytest.mark.parametrize("label,over,reason", DENYING_STATES,
                         ids=[d[0] for d in DENYING_STATES])
def test_a_blocked_submit_never_reaches_the_venue(venue, label, over,
                                                  reason):
    from sportsassets import pmus
    _arm(**over)
    with pytest.raises(gate.Denied) as e:
        pmus.submit_fok("mkt-x", 0.50, 10, intent="ORDER_INTENT_BUY_LONG")
    assert e.value.reason == reason
    assert venue.submissions == 0, (
        "%s: %d submission(s) reached the venue" % (label,
                                                    venue.submissions))
    assert venue.previews == [], "even the preview must not be sent"


@pytest.mark.parametrize("label,over,reason", DENYING_STATES,
                         ids=[d[0] for d in DENYING_STATES])
def test_a_blocked_close_never_reaches_the_venue(venue, label, over,
                                                 reason):
    """close_position sends an UNPRICED market exit bounded only by
    slippage. It is the most powerful order here and gets the same
    gate."""
    from sportsassets import pmus
    _arm(**over)
    with pytest.raises(gate.Denied) as e:
        pmus.close_position("mkt-x", slippage_bips=300)
    assert e.value.reason == reason
    assert venue.submissions == 0


# ── 2. an authorized route keeps working ─────────────────────────────

def test_an_authorized_submit_still_submits(venue):
    """Without this the suite would pass against a gate that refuses
    everything, which is not a control -- it is an outage."""
    from sportsassets import pmus
    _arm()
    pmus.submit_fok("mkt-x", 0.50, 10, intent="ORDER_INTENT_BUY_LONG")
    assert len(venue.creates) == 1


def test_an_authorized_close_still_closes(venue):
    from sportsassets import pmus
    _arm()
    pmus.close_position("mkt-x", slippage_bips=300)
    assert len(venue.closes) == 1


# ── 3. missing, malformed and unreadable all deny ────────────────────

def test_an_unbound_gate_denies(venue):
    """A process that cannot read the kill switch is not allowed to
    trade. Not 'assume fine until told otherwise'."""
    from sportsassets import pmus
    gate._restore_for_tests()            # unbound: no loop, no pool
    with pytest.raises(gate.Denied) as e:
        pmus.submit_fok("mkt-x", 0.50, 10, intent="ORDER_INTENT_BUY_LONG")
    assert e.value.reason == "authorization_unavailable"
    assert venue.submissions == 0


def test_an_absent_switch_row_denies():
    """THE ORIGINAL DEFECT. _is_paused returned False when the row was
    missing, so the control documented as 'no further orders' had never
    been armed in the life of the system. Absence is not permission."""

    class P:
        async def fetchval(self, sql, *a):
            return None                  # the row does not exist

    snap = asyncio.run(gate.read_state(P()))
    assert snap.paused is True
    assert snap.ok is False
    assert "absent" in snap.why


def test_a_malformed_switch_value_denies():
    class P:
        async def fetchval(self, sql, *a):
            return "{not json"

    snap = asyncio.run(gate.read_state(P()))
    assert snap.ok is False


def test_an_unreadable_switch_denies():
    class P:
        async def fetchval(self, sql, *a):
            raise RuntimeError("connection reset")

    snap = asyncio.run(gate.read_state(P()))
    assert snap.ok is False
    assert snap.paused is True
    assert "unreadable" in snap.why


def test_a_denying_snapshot_is_the_default_shape():
    """Snapshot() with nothing set must be maximally restrictive, so a
    code path that forgets to populate one cannot open the gate."""
    s = gate.Snapshot()
    assert s.paused and s.copy_halted and s.loss_stop and s.overspend
    assert s.venue is None and s.ok is False


# ── 4. authorization cannot be captured before a pause ───────────────

def test_a_stale_snapshot_does_not_authorize(venue):
    """QUEUED AND RETRIED WORK. A task that checked, waited, then
    submitted must not ride the old answer. The gate takes no token, so
    there is nothing to carry -- and a snapshot past the staleness bound
    stops counting as knowledge."""
    from sportsassets import pmus
    _arm(read_at=time.time() - (gate.MAX_STALE_S + 60), ok=True)

    # the live read path is what decides; force it to fail so only the
    # stale snapshot is available
    gate._restore_for_tests()
    with pytest.raises(gate.Denied):
        pmus.submit_fok("mkt-x", 0.50, 10, intent="ORDER_INTENT_BUY_LONG")
    assert venue.submissions == 0


def test_the_gate_takes_no_caller_supplied_authorization():
    """Structural: authorize() must not accept anything that means
    'already approved'. If it did, a retry could present one."""
    import inspect
    sig = inspect.signature(gate.authorize)
    assert set(sig.parameters) == {"operation", "lane", "slug"}
    for p in ("token", "approved", "authorized", "allow", "force",
              "skip_gate", "bypass"):
        assert p not in sig.parameters, p


def test_pausing_between_check_and_submit_blocks_the_submit(venue):
    """The realistic race. A route authorizes, work is queued, the
    operator pauses, the queued work runs. It must fail."""
    from sportsassets import pmus
    _arm()
    gate.authorize("submit", lane="copy")       # the early check

    _arm(paused=True)                           # operator pauses

    with pytest.raises(gate.Denied) as e:
        pmus.submit_fok("mkt-x", 0.50, 10, intent="ORDER_INTENT_BUY_LONG")
    assert e.value.reason == "live_trading_paused"
    assert venue.submissions == 0


# ── 5. global vs copy controls ───────────────────────────────────────

def test_manual_lanes_face_every_global_control(venue):
    """_execute_manual_sell checked the venue and nothing else. A manual
    route is still an order route."""
    from sportsassets import pmus
    for over, reason in ((dict(paused=True), "live_trading_paused"),
                         (dict(venue=None), "no_active_venue")):
        _arm(**over)
        with gate.lane("manual"):
            with pytest.raises(gate.Denied) as e:
                pmus.submit_fok("mkt-x", 0.5, 10,
                                intent="ORDER_INTENT_BUY_LONG")
        assert e.value.reason == reason
    assert venue.submissions == 0


def test_a_manual_sell_is_stopped_by_the_kill_switch(venue):
    from sportsassets import pmus
    _arm(paused=True)
    with gate.lane("manual"):
        with pytest.raises(gate.Denied) as e:
            pmus.close_position("mkt-x", slippage_bips=300)
    assert e.value.reason == "live_trading_paused"
    assert venue.submissions == 0


def test_the_copy_loss_breaker_does_not_stop_a_manual_operator(venue):
    """A breaker on the copy sleeve must not prevent a human flattening
    a position by hand. Scope is a decision, and this is it."""
    from sportsassets import pmus
    _arm(loss_stop=True, copy_halted=True, overspend=True)
    with gate.lane("manual"):
        pmus.submit_fok("mkt-x", 0.5, 10, intent="ORDER_INTENT_BUY_LONG")
    assert len(venue.creates) == 1


def test_the_same_breaker_does_stop_the_copy_lane(venue):
    from sportsassets import pmus
    _arm(loss_stop=True)
    with gate.lane("copy"):
        with pytest.raises(gate.Denied) as e:
            pmus.submit_fok("mkt-x", 0.5, 10,
                            intent="ORDER_INTENT_BUY_LONG")
    assert e.value.reason == "mirror_loss_stop"
    assert venue.submissions == 0


def test_an_undeclared_lane_gets_the_strictest_treatment(venue):
    """THE SHAPE OF THE ORIGINAL BUG, IN REVERSE. An allowlist means a
    route that never declares itself is treated as a copy lane and faces
    everything. A blocklist would have let every new caller skip the
    breakers by saying nothing -- absence granting permission, again."""
    from sportsassets import pmus
    _arm(loss_stop=True)
    assert gate.current_lane() == "unknown"
    with pytest.raises(gate.Denied) as e:
        pmus.submit_fok("mkt-x", 0.5, 10, intent="ORDER_INTENT_BUY_LONG")
    assert e.value.reason == "mirror_loss_stop"
    assert venue.submissions == 0


def test_global_only_lanes_is_an_allowlist_not_a_blocklist():
    import inspect
    src = inspect.getsource(gate.authorize)
    assert "lane not in GLOBAL_ONLY_LANES" in src


# ── 6. cancellation stays available ──────────────────────────────────

def test_cancel_is_not_gated(venue, monkeypatch):
    """A kill switch that also blocks cancels is a trap: it freezes
    resting orders in the market at the moment you most want them
    gone. Cancellation reduces exposure and stays open."""
    import inspect

    from sportsassets import pmus
    src = inspect.getsource(pmus.cancel_order)
    assert "_gate.authorize" not in src, (
        "cancel_order must not be gated -- it reduces exposure")


def test_cancellation_is_a_separate_explicit_path():
    """And it is documented as a decision, so nobody 'fixes' it later
    by adding the gate for consistency."""
    import inspect
    src = inspect.getsource(gate)
    assert "cancel" in src.lower()
    assert "NOT gated" in gate.describe()["cancellation"]


# ── 7. the boundary is where we think it is ──────────────────────────

def test_both_venue_submitting_functions_are_gated():
    import inspect

    from sportsassets import pmus
    for fn in (pmus.submit_fok, pmus.close_position):
        src = inspect.getsource(fn)
        assert "_gate.authorize" in src, fn.__name__


def test_the_gate_runs_before_the_client_is_built():
    """Ordering matters: building the client is the first thing that
    touches credentials and the network."""
    import inspect

    from sportsassets import pmus
    for fn in (pmus.submit_fok, pmus.close_position):
        src = inspect.getsource(fn)
        assert src.index("_gate.authorize") < src.index("_get_client()"), \
            fn.__name__


def test_no_test_here_changed_a_production_flag():
    """The suite must not be able to pass by turning something off."""
    import inspect
    src = inspect.getsource(__import__(__name__, fromlist=["x"]))
    # Built at runtime so this check does not match its own source and
    # report itself as the violation.
    for bad in ("os." + "environ[", "monkeypatch." + "setenv",
                "LIVE_TRADING" + "_ENABLED", "LIVE_COPY" + "_HALT",
                "WHALE_EXIT" + "_ENABLED"):
        assert bad not in src, bad
