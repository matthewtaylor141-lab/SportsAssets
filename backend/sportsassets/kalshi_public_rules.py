"""Credential-free Kalshi market-rule reader. GET only; no order path.

BASE URL (integration note): the package shipped
`https://external-api.kalshi.com/trade-api/v2`. The repo's Kalshi adapter
already documents and uses `https://api.elections.kalshi.com/trade-api/v2`
(kalshi_venue.BASE_URLS["prod"], read from edge-engine kalshi.py:25), so this
reader uses that same base -- pinned equal by
tests/test_settlement_rule_registry_integration.py. This module does NOT import
kalshi_venue (which holds the client's submit/cancel) or kalshi_orders.
"""
from __future__ import annotations
from dataclasses import dataclass
from . import settlement_rule_registry as SRR

VERSION = "KALSHI_PUBLIC_RULE_READER_V1"
BASE = "https://api.elections.kalshi.com/trade-api/v2"
TIMEOUT_S = 10.0


@dataclass(frozen=True)
class Result:
    ok: bool
    ticker: str
    market: dict | None = None
    evidence: dict | None = None
    status: int | None = None
    error: str | None = None
    sent: bool = True


class RequestsGet:
    """The real transport: GET only, no credential, no headers beyond the
    library defaults. It has no other method."""

    def get(self, url: str, *, timeout: float, params=None):
        import requests
        return requests.get(url, timeout=timeout, params=params)


def read(ticker: str, *, transport=None, timeout_s: float = TIMEOUT_S) -> Result:
    t = str(ticker or "").strip()
    if not t:
        return Result(False, t, error="TICKER_ABSENT", sent=False)
    tx = transport or RequestsGet()
    url = BASE + "/markets/" + t
    try:
        r = tx.get(url, timeout=float(timeout_s))
    except Exception as exc:                                    # noqa: BLE001
        return Result(False, t, error=type(exc).__name__)
    status = getattr(r, "status_code", None)
    if status != 200:
        return Result(False, t, status=status, error="HTTP_%s" % status)
    try:
        body = r.json()
    except Exception:                                           # noqa: BLE001
        return Result(False, t, status=status, error="JSON_UNREADABLE")
    market = body.get("market") if isinstance(body, dict) else None
    if not isinstance(market, dict):
        return Result(False, t, status=status, error="MARKET_OBJECT_ABSENT")
    return Result(True, t, market=market, evidence=SRR.kalshi_rule_evidence(market),
                  status=status)


def describe() -> dict:
    return {"version": VERSION, "endpoint": BASE + "/markets/{ticker}", "method": "GET",
            "credentials": "NONE", "orders": False, "mutations": False}
