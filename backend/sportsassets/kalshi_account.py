"""KALSHI READ-ONLY ACCOUNT RECONCILIATION (pure functions over responses).

Before anything may be submitted on Kalshi the account must be READ and the
read recorded against the key's fingerprint: balance, positions, resting
orders, recent fills and settlements. This is execmirror_probe's account
snapshot for Kalshi: the same "which account is this, what does it hold?"
question, answered from venue responses only, with a verdict:

    EMPTY        no open position, no resting order, no fill, no settlement
    NOT_EMPTY    the account holds or has done something; submission then
                 additionally needs the operator to ACCEPT this snapshot as
                 the baseline (kalshi_smalllive_control / the gate)
    UNREADABLE   any part failed, was refused, or came back in a shape we do
                 not recognise -- never read as an empty account

Field dialects follow edge-engine exactly (kalshi.py): dollars first, cents
fallback (`{key}_dollars` else `{key}/100`, kalshi.py:632-635); contracts
from `position_fp` then `position` (kalshi.py:551-553); `count_fp` then
`count` (kalshi.py:585-586). An amount with no recognisable field is None
(unknown), never zero.

`fetch_responses` is the one function that talks to a client; everything
else takes recorded responses, so the whole verdict is testable offline.
"""
from __future__ import annotations

import time
from decimal import Decimal, InvalidOperation
from typing import Any

from . import kalshi_venue as KV

VERSION = "KALSHI_ACCOUNT_V1"
MAX_PAGES = 10
PARTS = ("balance", "positions", "orders", "fills", "settlements")


def _dec(v) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def usd(row: dict, key: str) -> Decimal | None:
    """Dollar dialect first, cents fallback (kalshi.py:632-635); None when
    neither field is present."""
    d = _dec(row.get(key + "_dollars"))
    if d is not None:
        return d
    c = _dec(row.get(key))
    return None if c is None else c / 100


def contracts(row: dict, *keys: str) -> Decimal | None:
    for k in keys:
        d = _dec(row.get(k))
        if d is not None:
            return d
    return None


# ── per-endpoint parsers ─────────────────────────────────────────────

def parse_balance(body: Any) -> dict:
    """kalshi.py:388: `balance` in cents."""
    if not isinstance(body, dict) or body.get("balance") is None:
        return {"ok": False, "error": "BALANCE_FIELD_ABSENT"}
    c = _dec(body.get("balance"))
    if c is None:
        return {"ok": False, "error": "BALANCE_UNREADABLE"}
    return {"ok": True, "balance_usd": c / 100}


def parse_position(r: dict) -> dict:
    q = contracts(r, "position_fp", "position")
    cost = usd(r, "market_exposure")
    if cost is None:
        cost = usd(r, "total_traded")
    return {"ticker": str(r.get("ticker") or ""),
            # Kalshi signs a NO position negative; edge-engine takes abs().
            # We keep the sign: this lane only ever buys YES legs, so a
            # negative position is itself a reconciliation finding.
            "position": q, "exposure_usd": cost}


def parse_order(o: dict) -> dict:
    return {"order_id": str(o.get("order_id") or o.get("id") or ""),
            "client_order_id": o.get("client_order_id"),
            "ticker": str(o.get("ticker") or ""),
            "status": o.get("status"), "action": o.get("action"),
            "side": o.get("side"),
            "fill_count": contracts(o, "fill_count_fp", "fill_count",
                                    "filled_count"),
            "remaining_count": contracts(o, "remaining_count_fp",
                                         "remaining_count"),
            "created_time": o.get("created_time")}


def parse_fill(f: dict) -> dict:
    """One venue fill (kalshi.py:780-784 key list). Price is the price of
    the leg named by `side` (yes -> yes price, no -> no price), dollars
    first. `fee_usd` None means the venue stated no fee -- unknown."""
    side = str(f.get("side") or "").lower()
    px = usd(f, "yes_price") if side != "no" else usd(f, "no_price")
    return {"trade_id": str(f.get("trade_id") or ""),
            "order_id": str(f.get("order_id") or ""),
            "ticker": str(f.get("ticker") or ""),
            "side": side or None, "action": str(f.get("action") or "").lower() or None,
            "count": contracts(f, "count_fp", "count"),
            "price": px, "is_taker": f.get("is_taker"),
            "fee_usd": usd(f, "fee"),
            "created_time": f.get("created_time"), "parsed": True}


def parse_settlement(r: dict) -> dict:
    yc, nc = usd(r, "yes_total_cost"), usd(r, "no_total_cost")
    return {"ticker": str(r.get("ticker") or ""),
            "market_result": r.get("market_result"),
            "cost_usd": None if yc is None or nc is None else yc + nc,
            "revenue_usd": usd(r, "revenue"),
            "settled_time": r.get("settled_time")}


def _rows(resp: Any, key: str) -> tuple[list | None, str | None]:
    """(rows, error). A Refusal, a non-200, a transport failure or a body
    without the list key is an ERROR, not an empty list."""
    if isinstance(resp, KV.Refusal):
        return None, resp.code
    if isinstance(resp, KV.Response):
        if resp.status != 200:
            return None, ("transport" if resp.status is None
                          else "http_%s" % resp.status)
        body = resp.body
    else:
        body = resp
    if not isinstance(body, dict) or not isinstance(body.get(key), list):
        return None, "SHAPE_%s_ABSENT" % key.upper()
    return body[key], None


def _pages(responses, key: str) -> tuple[list | None, str | None, bool]:
    """Concatenate pages; (rows, error, complete). A page cap reached with a
    cursor still set is INCOMPLETE (a walk that hit its cap is not a
    reading of the account)."""
    if not isinstance(responses, (list, tuple)):
        responses = [responses]
    out: list = []
    last_cursor = None
    for r in responses:
        rows, err = _rows(r, key)
        if err:
            return None, err, False
        out.extend(rows)
        body = r.body if isinstance(r, KV.Response) else r
        last_cursor = (body or {}).get("cursor") or None
    return out, None, not last_cursor


# ── the snapshot ─────────────────────────────────────────────────────

def snapshot(responses: dict, *, key_fingerprint: str | None,
             kalshi_env: str | None, at: float | None = None) -> dict:
    """responses: {balance: resp, positions: [pages], orders: [pages] (the
    RESTING listing), fills: [pages], settlements: [pages]}."""
    out: dict = {"version": VERSION, "read_only": True,
                 "at": time.time() if at is None else at,
                 "key_fingerprint": key_fingerprint, "kalshi_env": kalshi_env,
                 "errors": {}}
    if not key_fingerprint:
        out["errors"]["credential"] = KV.KALSHI_CREDENTIAL_ABSENT
    b = responses.get("balance")
    if b is None:
        err = "NOT_READ"
    elif isinstance(b, KV.Refusal):
        err = b.code
    elif isinstance(b, KV.Response) and b.status != 200:
        err = "transport" if b.status is None else "http_%s" % b.status
    else:
        pb = parse_balance(b.body if isinstance(b, KV.Response) else b)
        err = pb.get("error")
        out["balance_usd"] = pb.get("balance_usd")
    if err:
        out["errors"]["balance"] = err
    parsed: dict = {}
    for part, key, fn in (("positions", "market_positions", parse_position),
                          ("orders", "orders", parse_order),
                          ("fills", "fills", parse_fill),
                          ("settlements", "settlements", parse_settlement)):
        if responses.get(part) is None:
            out["errors"][part] = "NOT_READ"
            continue
        rows, err, complete = _pages(responses[part], key)
        if err:
            out["errors"][part] = err
            continue
        if not complete:
            out["errors"][part] = "PAGE_CAP_REACHED"
        parsed[part] = [fn(r) for r in rows]
    positions = parsed.get("positions") or []
    unreadable_pos = [p["ticker"] for p in positions if p["position"] is None]
    if unreadable_pos:
        out["errors"]["positions_shape"] = "POSITION_FIELD_ABSENT"
    open_pos = [p for p in positions if p["position"] not in (None, 0)]
    resting = [o for o in parsed.get("orders") or []
               if str(o.get("status") or "resting").lower() == "resting"]
    out.update({
        "positions": open_pos,
        "open_positions": len(open_pos),
        "negative_positions": [p["ticker"] for p in open_pos
                               if p["position"] is not None and p["position"] < 0],
        "exposure_usd": (None if any(p["exposure_usd"] is None for p in open_pos)
                         else sum((p["exposure_usd"] for p in open_pos), Decimal(0))),
        "resting_orders": resting,
        "fills_recent": len(parsed.get("fills") or []),
        "settlements_recent": len(parsed.get("settlements") or []),
    })
    out["complete"] = not out["errors"]
    if not out["complete"]:
        out["verdict"] = "UNREADABLE"
    elif open_pos or resting or out["fills_recent"] or out["settlements_recent"]:
        out["verdict"] = "NOT_EMPTY"
    else:
        out["verdict"] = "EMPTY"
    return out


def reconciliation_record(snap: dict, *, baseline_accepted: bool = False,
                          actor: str | None = None) -> dict:
    """The row written to kalshi_account_reconciliations. Only the snapshot's
    own verdict is recorded; acceptance of a NOT_EMPTY baseline is an
    explicit operator act."""
    def s(v):
        return None if v is None else str(v)
    return {"key_fingerprint": snap.get("key_fingerprint"),
            "kalshi_env": snap.get("kalshi_env"),
            "verdict": snap.get("verdict"),
            "complete": bool(snap.get("complete")),
            "balance_usd": s(snap.get("balance_usd")),
            "positions": [{"ticker": p["ticker"], "position": s(p["position"]),
                           "exposure_usd": s(p["exposure_usd"])}
                          for p in snap.get("positions") or []],
            "resting_orders": [{"order_id": o["order_id"], "ticker": o["ticker"],
                                "client_order_id": o.get("client_order_id")}
                               for o in snap.get("resting_orders") or []],
            "fills_recent": snap.get("fills_recent"),
            "settlements_recent": snap.get("settlements_recent"),
            "errors": snap.get("errors") or {},
            "baseline_accepted": bool(baseline_accepted
                                      and snap.get("verdict") == "NOT_EMPTY"),
            "actor": actor, "at": snap.get("at")}


def fetch_responses(client: KV.KalshiClient, *, max_pages: int = MAX_PAGES) -> dict:
    """READS ONLY: balance, positions, resting orders, fills, settlements.
    With no credential every read is a Refusal and nothing is sent."""
    out: dict = {"balance": client.balance()}

    def walk(fn, key, **kw):
        pages, cursor = [], None
        for _ in range(max_pages):
            r = fn(cursor=cursor, **kw)
            pages.append(r)
            if not isinstance(r, KV.Response) or r.status != 200:
                break
            cursor = (r.body or {}).get("cursor")
            if not cursor:
                break
        return pages

    out["positions"] = walk(client.positions, "market_positions")
    out["orders"] = walk(client.orders, "orders", status="resting")
    out["fills"] = walk(client.fills, "fills")
    out["settlements"] = walk(client.settlements, "settlements")
    return out


# ── the read-only client (rc6.3 kalshi-shadow) ───────────────────────
#
# The captured Kalshi API-key page documents no read-only key scope
# (research/kalshi_canonical_venue/REP_PRODUCTION_CONTRACT_2026-10-07/docs/
# getting_started_api_keys.md), so READ-ONLY is enforced HERE, in the
# transport, not trusted to the key or the caller: the client the SHADOW
# reconciliation writer reads the account with can send a GET and nothing
# else. A POST / DELETE / PUT / PATCH -- or any request carrying a body --
# raises ReadOnlyViolation before a byte leaves the process. It is NOT a
# kalshi_venue.TransportError, so the client does not turn it into an
# "ambiguous venue answer": it surfaces as the exception it is.

READ_ONLY_METHODS = ("GET",)


class ReadOnlyViolation(RuntimeError):
    """A non-read request reached the read-only Kalshi transport."""


class GetOnlyTransport:
    """kalshi_venue.Transport that forwards GETs (to `inner`, by default the
    real requests transport, built on the first GET) and refuses the rest."""

    def __init__(self, inner=None):
        self._inner = inner

    def send(self, method, url, *, headers, params, json_body, timeout):
        m = str(method or "").upper()
        if m not in READ_ONLY_METHODS or json_body is not None:
            raise ReadOnlyViolation(
                "%s refused: the Kalshi SHADOW client is read-only" % (m or "?"))
        if self._inner is None:
            self._inner = KV.RequestsTransport()
        return self._inner.send(m, url, headers=headers, params=params,
                                json_body=None, timeout=timeout)


def read_only_client(env=None, *, transport=None, clock=time.time,
                     timeout: float = KV.TIMEOUT_S) -> KV.KalshiClient:
    """A KalshiClient whose every request passes the GET-only transport.
    `transport` is the inner transport (tests inject a fake; production
    leaves it None for the real one)."""
    return KV.KalshiClient(GetOnlyTransport(transport), env=env, clock=clock,
                           timeout=timeout)


def read_only_reconciliation(client: KV.KalshiClient, *,
                             now: float | None = None) -> dict:
    cred = client.credential_state()
    if cred.get("state") != "PRESENT":
        return {"version": VERSION, "verdict": "UNREADABLE", "complete": False,
                "errors": {"credential": cred.get("state")},
                "key_fingerprint": cred.get("key_fingerprint"), "sent": False}
    return snapshot(fetch_responses(client),
                    key_fingerprint=cred.get("key_fingerprint"),
                    kalshi_env=cred.get("environment"), at=now)


# ── final-state reconciliation (our venue fills vs the account) ──────

def reconcile_positions(snap: dict, our_net: dict, *, baseline: dict | None = None,
                        settled: set | None = None) -> dict:
    """Compare the venue's positions with what this lane's venue fills say
    it should hold (plus an accepted baseline). Tickers settled at the venue
    are skipped when the venue no longer shows them (the live side settled).
    Any difference is reported; nothing is "corrected" here."""
    if snap.get("verdict") == "UNREADABLE":
        return {"reconciled": False, "reason": "SNAPSHOT_UNREADABLE",
                "differences": {}}
    venue = {p["ticker"]: p["position"] for p in snap.get("positions") or []}
    base = {k: Decimal(str(v)) for k, v in (baseline or {}).items()}
    settled = settled or set()
    diffs = {}
    for t in set(venue) | set(our_net) | set(base):
        v = venue.get(t, Decimal(0))
        expect = Decimal(str(our_net.get(t, 0))) + base.get(t, Decimal(0))
        if t in settled and not venue.get(t):
            continue
        if v != expect:
            diffs[t] = {"venue": str(v), "lane_fills_net": str(our_net.get(t, 0)),
                        "baseline": str(base.get(t, 0))}
    return {"reconciled": not diffs, "differences": diffs}
