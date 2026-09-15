"""Refuse, in ANY process, to resolve or connect to a real venue hostname.

Injected via PYTHONPATH so it loads automatically in every child process the
offline end-to-end test starts -- broker, fake endpoint, collector -- which a
test-process monkeypatch could not reach. Python imports `sitecustomize` at
interpreter start, before any test code runs.

WHY BOTH RESOLUTION AND CONNECTION ARE HOOKED. A hostname block alone misses a
literal IP; a connect block alone misses a resolution that a library performs
eagerly. Hooking `getaddrinfo`, `create_connection` and `socket.connect`
together means the first sign of a real venue address -- by name at any layer --
stops the process rather than reaching the network.

A refusal writes a marker file, because a subprocess that dies of an exception
in a library's retry loop can otherwise look like an ordinary timeout. The test
asserts the marker never appears.
"""
from __future__ import annotations

import os
import pathlib
import socket

# Substrings, not exact hosts: a subdomain, a regional alias or a CNAME would
# all still carry one of these.
BLOCKED_FRAGMENTS = (
    "polymarket",
    "clob.polymarket.com",
    "api.polymarket.us",
    "gateway.polymarket.us",
    "ws-subscriptions-clob.polymarket.com",
)

_MARKER = os.environ.get("RN1_HOSTGUARD_MARKER", "")


class RealVenueContactAttempted(RuntimeError):
    """A process tried to reach a real venue. Run 83.4 forbids any connection."""


def _check(host) -> None:
    if not isinstance(host, str):
        return
    low = host.lower()
    if any(frag in low for frag in BLOCKED_FRAGMENTS):
        if _MARKER:
            try:
                pathlib.Path(_MARKER).write_text(f"BLOCKED:{host}\n")
            except OSError:
                pass
        raise RealVenueContactAttempted(
            f"refusing to contact {host!r}: Run 83.4 is offline-only. No PMUS "
            "or CLOB connection is authorised."
        )


_real_getaddrinfo = socket.getaddrinfo
_real_create_connection = socket.create_connection
_real_connect = socket.socket.connect


def getaddrinfo(host, *a, **k):
    _check(host)
    return _real_getaddrinfo(host, *a, **k)


def create_connection(address, *a, **k):
    if isinstance(address, tuple) and address:
        _check(address[0])
    return _real_create_connection(address, *a, **k)


def connect(self, address):
    if isinstance(address, tuple) and address:
        _check(address[0])
    return _real_connect(self, address)


socket.getaddrinfo = getaddrinfo
socket.create_connection = create_connection
socket.socket.connect = connect
