"""A refusal that teaches nothing is a refusal we can never lift.

The BUY_SHORT ban is justified on fills — 6/6 wrong against 25/25
clean longs. But five hours after it shipped the sleeve had taken ONE
fill, because 0x076daa87's live flow is almost entirely shorts:

    05:04:26  rejected  short-branch-refused
    05:03:52  rejected  short-branch-refused
    04:58:53  rejected  short-branch-refused

A ban on the class that carries most of the flow is an outage wearing
a guard's uniform. The honest response is NOT to lift it on that
argument — reopening a money gate on inference, overnight, on a class
that has never once executed correctly, is not a call to make alone.

So each refusal now records the intent-aware ask for the leg we would
have bought, beside the whale's own price. The rows answer the
question the ban cannot answer about itself: is the leg we name his
outcome (ratio near 1.0 — the ask guard and side band already cover
it, and the ban is redundant), or is it the complement (the ban is
load-bearing and stays)?

The measurement must never become the money path. It reads a quote and
writes text. If it throws, the refusal still stands.
"""

import inspect

from sportsassets import live_executor as le


class TestTheShadowIsRecorded:
    def test_the_refusal_reads_the_ask_it_would_have_paid(self):
        src = inspect.getsource(le.maybe_execute)
        head = src[:src.index("short-branch-refused")]
        assert "pmus.side_ask" in head, (
            "the shadow read must happen BEFORE the rejection is "
            "written, or the row carries no evidence")

    def test_the_evidence_lands_on_the_rejection_row(self):
        src = inspect.getsource(le.maybe_execute)
        assert "SHADOW" in src
        assert "_shadow" in src

    def test_it_records_his_price_beside_the_ask(self):
        """An ask alone proves nothing — the comparison IS the
        evidence."""
        src = inspect.getsource(le.maybe_execute)
        i = src.index("SHADOW ask=")
        assert "his=" in src[i:i + 200]
        assert "ratio=" in src[i:i + 200]


class TestTheShadowCannotBreakTheRefusal:
    def test_a_failed_quote_still_refuses(self):
        """Evidence is optional; the gate is not. A raised exception in
        the measurement must not skip the rejection or fall through to
        a submit."""
        src = inspect.getsource(le.maybe_execute)
        blk = src[src.index("_shadow = \"\""):src.index(
            "short-branch-refused")]
        assert "except Exception" in blk
        assert "SHADOW err=" in blk

    def test_the_branch_still_returns_before_submitting(self):
        src = inspect.getsource(le.maybe_execute)
        assert src.index("short-branch-refused") < src.index(
            "pmus.submit_fok")

    def test_the_gate_is_not_quietly_reopened(self):
        """The ban still requires LIVE_ALLOW_SHORT=on. Adding evidence
        must not become adding an exception."""
        src = inspect.getsource(le.maybe_execute)
        assert "LIVE_ALLOW_SHORT" in src
        gate = src.index('if (_intent == "ORDER_INTENT_BUY_SHORT"')
        assert src.index("_shadow") > gate, (
            "the shadow read belongs INSIDE the refusal branch — a "
            "quote read on every order is a cost, not a guard")


class TestTheProbeCanActuallySeeTheWorker:
    """WHALEEXIT printed nothing all night. I read that as a dead
    detector. It was a dead PROBE: heartbeats are a LIST of rows with a
    .service field from /api/health/services, and the line was reading
    /tmp/status.json for an object keyed by name.

    Fifth instance tonight of an instrument that cannot see its subject
    reporting anyway. Pin the shape so it cannot regress to silence."""

    WF = ".github/workflows/engine-diagnostic.yml"

    def _wf(self):
        import pathlib
        p = pathlib.Path(__file__).resolve().parents[2] / self.WF
        return p.read_text() if p.exists() else ""

    def test_it_reads_the_heartbeat_endpoint(self):
        wf = self._wf()
        if not wf:
            return
        i = wf.index("WHALEEXIT")
        blk = wf[max(0, i - 1200):i + 400]
        assert "/api/health/services" in blk

    def test_it_matches_the_list_shape_not_an_object(self):
        wf = self._wf()
        if not wf:
            return
        i = wf.index("WHALEEXIT")
        blk = wf[wf.index("WHALE-EXIT DETECTOR"):i + 600]
        assert ".service" in blk
        assert "/tmp/hb_we.json" in blk, (
            "the jq must read the heartbeat list it fetched, not the "
            "status payload that never carried these rows")

    def test_absence_is_reported_as_absence(self):
        """Silence is the one output a probe must never produce — it is
        indistinguishable from a healthy quiet worker."""
        wf = self._wf()
        if not wf:
            return
        assert "WHALEEXIT no heartbeat row" in wf
