"""The account-link balance must never age silently.

The boot-time credential check was the ONLY balance read: `_ACCOUNT_LINK`
was populated once at startup and re-posted verbatim every cycle, so on a
quiet service the wire served a 17-hour-old balance as if it were live
(2026-08-13 — it read as a broken account link; it was only snapshot age).
These tests pin the refresh helper and the TTL plumbing that fixed it.
"""

import inspect
import time

from edge.shadow import runner


class _Adapter:
    name = "fake-venue"

    def __init__(self, auth=None, exc=None):
        self._auth = auth
        self._exc = exc

    def has_credentials(self):
        return True

    def check_auth(self):
        if self._exc:
            raise self._exc
        return self._auth


def test_check_returns_balance_and_timestamp():
    link = runner._check_account_link(
        [_Adapter(auth={"ok": True, "balance_usd": 48225.0})])
    entry = link["fake-venue"]
    assert entry["ok"] is True
    assert entry["detail"] == "balance $48225.00"
    assert abs(entry["checked_at"] - time.time()) < 5


def test_check_failure_is_captured_not_raised():
    link = runner._check_account_link([_Adapter(exc=RuntimeError("boom"))])
    entry = link["fake-venue"]
    assert entry["ok"] is False
    assert "RuntimeError" in entry["detail"]


def test_no_credentials_reported():
    class NoCreds(_Adapter):
        def has_credentials(self):
            return False

    link = runner._check_account_link([NoCreds()])
    assert link["fake-venue"] == {"ok": False, "detail": "no credentials set"}


def test_cycle_loop_refreshes_on_ttl():
    """The loop must re-check when the snapshot ages past the TTL, stamp
    the clock BEFORE the network call (no per-cycle hammer on a failing
    venue), and keep the last good reading alongside a refresh failure."""
    src = inspect.getsource(runner._main_impl)
    assert "_ACCOUNT_LINK_AT[\"ts\"] > _ACCOUNT_LINK_TTL_S" in src.replace("'", '"')
    # stamp precedes the check call in the refresh block
    refresh = src.split("_ACCOUNT_LINK_TTL_S", 1)[1]
    assert refresh.index("_ACCOUNT_LINK_AT[\"ts\"] = time.time()".replace('"', '"')) \
        < refresh.index("_check_account_link(adapters)")
    assert "last_ok" in refresh


def test_startup_stamps_refresh_clock():
    src = inspect.getsource(runner._main_impl)
    boot = src.split("_boot_beacon", 1)[0]
    assert "_check_account_link(adapters)" in boot
    assert '_ACCOUNT_LINK_AT["ts"] = time.time()' in boot
