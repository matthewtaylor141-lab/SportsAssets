"""BACKGROUND TOKEN REFRESH FOR THE MARKET-PLANE STREAM (completion readiness).

Venue guidance (2026-10-07): access tokens last 180 s; refresh in the
background and use the new token on the NEXT connect; do not cycle a healthy
stream just to re-authenticate. The stream reads its bearer token only when
it opens a connection (GrpcBidiTransport.run_once), so nothing here touches a
live stream: this keeps the client's cached token warm so a reconnect never
waits on a mint, and serialises mint/invalidate across the stream thread and
the refresher. Never logs or returns the token outside token().
"""
from __future__ import annotations

import threading
import time

VERSION = "PMX_TOKEN_KEEPER_V1"
MODE = "BACKGROUND_REFRESH_NO_STREAM_CYCLE"
#: well inside the 180 s life; the client re-mints only once its own reuse
#: window (expires_in - RENEW_MARGIN_S) has passed, so most ticks are no-ops
REFRESH_EVERY_S = 30.0


class TokenKeeper:
    def __init__(self, client, *, refresh_every_s: float = REFRESH_EVERY_S,
                 clock=time.time):
        self._client = client
        self._every = float(refresh_every_s)
        self._clock = clock
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self.refreshes = 0
        self.failures = 0
        self.invalidations = 0
        self.last_ok_at = None
        self.last_failure_at = None

    def token(self):
        with self._lock:
            tok = self._client.token()
        if tok:
            self.last_ok_at = self._clock()
        return tok

    def invalidate(self) -> None:
        with self._lock:
            self.invalidations += 1
            self._client.invalidate_token()

    def refresh_once(self) -> bool:
        try:
            ok = bool(self.token())
        except Exception:                                     # noqa: BLE001
            ok = False
        if ok:
            self.refreshes += 1
        else:
            self.failures += 1
            self.last_failure_at = self._clock()
        return ok

    def _run(self) -> None:
        while not self._stop.wait(self._every):
            self.refresh_once()

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True,
                                            name="pmx-token-keeper")
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def digest(self) -> dict:
        return {"version": VERSION, "mode": MODE,
                "refresh_every_s": self._every, "refreshes": self.refreshes,
                "failures": self.failures,
                "invalidations": self.invalidations,
                "last_ok_at": self.last_ok_at,
                "last_failure_at": self.last_failure_at,
                "stream_cycled_for_reauth": False}
