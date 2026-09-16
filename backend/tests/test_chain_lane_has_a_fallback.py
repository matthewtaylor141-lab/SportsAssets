"""Path A was a single endpoint, and it went down for a day.

2026-08-31 -> 2026-09-01, measured off trades.source:

    Aug 24-30   chain 81-86% of rn1's fills, detection lag -0.65s
    Aug 31      chain 201/7,386  =  2.7%,    lag 339.9s
    Sep 1       chain 0/818      =  0.0%,    lag 333.2s

    chain_listener: down — server rejected WebSocket connection: HTTP 429

POLYGON_WS_URL held ONE endpoint with no failover, so a throttled
provider took Path A out entirely and left the Data-API poller as the
only detection lane. That is not a partial degradation: the poller's
281s median IS the venue's publication lag and cannot be polled away,
and TRUEEDGE-FAST grades the same book at reaction <= 5s at +$9,148
against actual -$2,121. A throttled endpoint removes the edge.

Two fixes, both here:
  * rotate to another endpoint after a second consecutive failure
  * say "throttled" in the heartbeat, because a 429 and a dead host
    need different actions from a human

The list is comma-separated and a list of ONE must behave exactly as
before — nobody should have to reconfigure to keep today's behaviour.
"""
import inspect

from sportsassets.ingestion import chain as C


def _run_src():
    return inspect.getsource(C.ChainListener.run)


def _init_src():
    return inspect.getsource(C.ChainListener.__init__)


class TestTheEndpointListIsParsed:
    def test_it_splits_on_commas(self):
        assert 'split(",")' in _init_src()

    def test_blank_entries_are_dropped(self):
        """A trailing comma in an env var must not become an endpoint
        called "" that fails every rotation."""
        src = _init_src()
        assert "if u.strip()" in src

    def test_a_single_url_still_populates_the_scalar(self):
        """`self._ws_url` is what the disabled-check and the log line
        read; leaving it unset would disable Path A outright."""
        assert "self._ws_url = self._ws_urls[0] if self._ws_urls else \"\"" \
            in _init_src()


class TestRotationIsConservative:
    def test_it_does_not_rotate_on_the_first_failure(self):
        """A single blip is not a reason to abandon a healthy primary."""
        assert "self._fail_streak >= 2" in _run_src()

    def test_it_does_rotate_eventually(self):
        """Never rotating is what cost a day of edge."""
        assert "self._ws_idx += 1" in _run_src()

    def test_a_single_endpoint_deployment_is_unaffected(self):
        """The guard must require MORE than one URL, or a one-endpoint
        config 'rotates' onto itself and the log lies about failover."""
        assert "len(self._ws_urls) > 1" in _run_src()

    def test_the_index_wraps(self):
        """Rotating past the end must return to the primary, not index
        out of range and kill the reconnect loop entirely."""
        assert "% len(self._ws_urls)" in _run_src()

    def test_selection_happens_at_connect_time(self):
        """Choosing the URL once at startup would make rotation
        invisible to the connection that actually matters."""
        src = _run_src()
        i = src.index("websockets.connect")
        assert "self._ws_urls[self._ws_idx % len(self._ws_urls)]" in \
            src[max(0, i - 300):i]


class TestTheHeartbeatNamesTheCause:
    def test_throttling_is_distinguished_from_a_dead_host(self):
        """A 429 needs a quota check; a refused connection needs a
        different endpoint. One 'down' cannot ask for either."""
        assert '"throttled": "429" in str(exc)' in _run_src()

    def test_it_says_which_endpoint_of_how_many(self):
        src = _run_src()
        assert '"endpoint_index"' in src
        assert '"endpoints_configured"' in src

    def test_the_backoff_survived_the_change(self):
        """The exponential cap came from a prior 429 incident; a
        rotation that reset it to a tight retry would re-create the
        self-inflicted throttle it was written to stop."""
        assert "min(2 * (2 ** min(self._fail_streak - 1, 6)), 120)" in _run_src()


class TestWeStopSpendingCallsWhileBlocked:
    """The 429 lands on the SUBSCRIBE, and the catch-up runs BEFORE it.

    So while blocked, every 120s retry still spent one eth_blockNumber
    plus N eth_getLogs — about 30 rounds an hour of our own quota, on
    work whose only consumer is a connection we are about to be refused.
    Measured 2026-09-01: the block held ~28 hours and across a UTC
    midnight (fail streak 35 -> 96), so it is not a daily quota rolling
    over, and every call made while blocked can only slow recovery.

    Skipping is already known-safe, and the pre-existing comment says
    why: "The poller + reconciler own gap coverage; a skipped backfill
    costs nothing but duplicate-suppressed rows."
    """

    def test_the_catch_up_is_skipped_while_throttled(self):
        assert "_skip_catchup" in _run_src()
        assert 'getattr(self, "_last_throttled", False)' in _run_src()

    def test_the_flag_is_set_only_on_a_429(self):
        """A dead host is not a throttle. Skipping the catch-up on
        every failure would silently stop gap recovery for reasons that
        have nothing to do with quota."""
        assert 'self._last_throttled = "429" in str(exc)' in _run_src()

    def test_a_successful_subscribe_clears_it(self):
        """Without this the listener never backfills again after its
        first 429 — a permanent gap-recovery outage produced by the fix
        for a temporary one."""
        src = _run_src()
        assert "self._last_throttled = False" in src
        i = src.index("self._fail_streak = 0")
        assert "self._last_throttled = False" in src[i:i + 400], \
            "the flag is cleared somewhere other than the success path"

    def test_the_cursor_is_not_even_loaded_when_skipping(self):
        """Loading the cursor is a DB read, but the tip check and
        backfill that follow it are the RPC spend; short-circuiting at
        the cursor keeps the whole block off the wire."""
        assert "None if _skip_catchup else await self._load_cursor()" \
            in _run_src()

    def test_skipping_is_visible_in_the_log(self):
        """A silent skip looks identical to a listener that has quietly
        stopped recovering gaps."""
        src = _run_src()
        i = src.index("_skip_catchup")
        assert "catch-up skipped" in src[i:i + 900]
