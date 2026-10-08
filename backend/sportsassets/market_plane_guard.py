"""THE DEDICATED MARKET PLANE IS ORDERLESS BY STRUCTURE (closeout 2026-10-08).

Kalshi documents no read-only API key class: an API key is account-wide. So
the boundary that lets the existing Kalshi key authenticate the WebSocket
order-book feed is the PROCESS, not the key. sportsassets-market-plane
(`python -m sportsassets.workers.universal_market_plane`) installs this guard
before anything else runs:

  1. execution_gate.lock_process: every venue write (submit, close, cancel)
     from this process is refused, at the gate and at the transport
     (venue_request_gate.check_write_lock refuses any non-GET);
  2. an import blocker: the modules that hold a venue order client
     (ORDER_MODULES) cannot be imported in this process at all -- a lazy
     import on some future code path fails closed with ImportError instead
     of reaching an order endpoint;
  3. a credential census (names only, never values): the plane may hold
     DATABASE_URL, the Kalshi WebSocket key and the PMX market-data values;
     any order-capable credential or live switch (FORBIDDEN_ENV) present
     makes the plane refuse to start its runtimes (it idles and says why,
     and is not restart-churned).

The provisioned plane (ops/render_market_plane_provision.json, mirroring the
owner's ops/render_market_plane_service.yaml) holds DATABASE_URL, the three
PMX market-data values and the two Kalshi values -- nothing else. With
UNIVERSAL_MARKET_PLANE=off it is KALSHI_WS_ONLY (the Kalshi runtime alone).
"""
from __future__ import annotations

import importlib.abc
import logging
import os
import sys

log = logging.getLogger(__name__)

LOCK_REASON = ("DEDICATED_READ_ONLY market plane (market_plane_guard): "
               "market data only, no venue writes")

#: the modules holding a venue ORDER client (place / cancel / close /
#: fund). Never importable in the market plane.
ORDER_MODULES = frozenset({
    "sportsassets.kalshi_venue", "sportsassets.kalshi_orders",
    "sportsassets.live_executor", "sportsassets.pmus", "sportsassets.pmx",
    "sportsassets.bettor_funded_execution",
    "sportsassets.bettor_funded_management",
    "sportsassets.submission_surface", "sportsassets.calibration_execute",
    "sportsassets.workers.mirror_live", "sportsassets.workers.bettor_live_loop",
})

#: order-capable credentials and live switches: their presence on the
#: plane is a misconfiguration the plane refuses to run with.
FORBIDDEN_ENV = (
    "PMUS_KEY_ID", "PMUS_SECRET_KEY", "PMUS_EXECMIRROR_KEY_ID",
    "PMUS_EXECMIRROR_SECRET_KEY", "EDGE_PMUS_KEY_ID", "EDGE_PMUS_SECRET_KEY",
    "EDGE_KALSHI_KEY_ID", "EDGE_KALSHI_PRIVATE_KEY", "PMX_PARTICIPANT_ID",
    "LIVE_TRADING_ENABLED", "POLYMARKET_PRIVATE_KEY", "POLYGON_PRIVATE_KEY",
    "ADMIN_TOKEN",
)

#: what the plane may hold (names; reported, never the values)
ALLOWED_SECRET_ENV = ("DATABASE_URL", "KALSHI_API_KEY_ID",
                      "KALSHI_PRIVATE_KEY_PEM", "PMX_CLIENT_ID",
                      "PMX_KEY_ID", "PMX_PRIVATE_KEY_B64")

R_FORBIDDEN_ENV = "ORDER_CAPABLE_CREDENTIAL_PRESENT_ON_MARKET_PLANE"


class OrderModuleBlocked(ImportError):
    pass


class _Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in ORDER_MODULES:
            raise OrderModuleBlocked(
                "%s refused: the dedicated market plane imports no order "
                "client (market_plane_guard)" % fullname)
        return None


def forbidden_env_present(env=None) -> list:
    env = os.environ if env is None else env
    return sorted(k for k in FORBIDDEN_ENV
                  if str(env.get(k) or "").strip()
                  and str(env.get(k)).strip().lower()
                  not in ("0", "false", "off", "no"))


def blocker_installed() -> bool:
    return any(isinstance(f, _Blocker) for f in sys.meta_path)


def install(env=None) -> dict:
    """Lock the process, block the order modules, census the credentials.
    Idempotent. Returns the report the plane's boot record carries."""
    from . import execution_gate as EG
    env = os.environ if env is None else env
    EG.lock_process(LOCK_REASON)
    if not blocker_installed():
        sys.meta_path.insert(0, _Blocker())
    already = sorted(m for m in ORDER_MODULES if m in sys.modules)
    bad = forbidden_env_present(env)
    rep = {"process_locked": EG.process_lock() is not None,
           "lock_reason": EG.process_lock(),
           "order_modules_blocked": sorted(ORDER_MODULES),
           "order_modules_already_loaded": already,
           "forbidden_env_present": bad,
           "allowed_secret_env_present": sorted(
               k for k in ALLOWED_SECRET_ENV if str(env.get(k) or "").strip()),
           "mode": ("KALSHI_WS_ONLY" if str(env.get(
               "UNIVERSAL_MARKET_PLANE") or "").strip().lower() in
               ("0", "false", "off", "no") else "UMP_AND_KALSHI_WS"),
           "refused": R_FORBIDDEN_ENV if (bad or already) else None,
           "authority": "MARKET_DATA_READ_ONLY_NO_ORDER_AUTHORITY"}
    if rep["refused"]:
        log.error("market plane REFUSED to run: %s (names: %s %s)",
                  R_FORBIDDEN_ENV, bad, already)
    else:
        log.warning("market plane guard installed: process locked, %d order "
                    "modules blocked, mode %s, secrets present (names) %s",
                    len(ORDER_MODULES), rep["mode"],
                    rep["allowed_secret_env_present"])
    return rep
