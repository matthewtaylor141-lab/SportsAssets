"""EXECUTION-MIRROR ACCOUNT PROBE (read-only, admin-only).

The 1:1,000 execution mirror trades a FRESH Polymarket US account whose
credential lives only in the API service environment, under two names of
its own:

    PMUS_EXECMIRROR_KEY_ID       the account's API key id
    PMUS_EXECMIRROR_SECRET_KEY   its Ed25519 secret key (base64)

They are deliberately not `PMUS_KEY_ID` / `PMUS_SECRET_KEY` (the funded
account) and not `PMUS_MIRROR*` (the legacy whale-copy lane's switches).

This module answers, before anything can trade, "which account is this and
what does it hold?" with READS ONLY:

  keys      which of the two names are present (booleans), and whether the
            key id differs from the funded account's (a boolean from a
            hash comparison -- never a value, length or prefix).
  account   balances (allowlisted fields), every position, every open
            order and the recent activity counts; plus a short, stable
            fingerprint of the key id so later reads can prove they talk
            to the same account.
  markets   public market records for given slugs, reduced to the fields
            that constrain an order (tick, increments, minimums, state).

THE INTERFACE CANNOT MUTATE. `_Reader` holds the SDK client privately and
exposes an ALLOWLIST of five reads; it has no order-placing, cancelling,
amending, position-closing or previewing attribute at all, and
`tests/test_execmirror_probe.py` walks this file's AST to keep it that way.
Nothing here writes to our database. The secret is passed only to the SDK
constructor; no response field whose name looks secret is returned.
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from typing import Any

VERSION = "EXECMIRROR_PROBE_V1"
KEY_ID_ENV = "PMUS_EXECMIRROR_KEY_ID"
SECRET_ENV = "PMUS_EXECMIRROR_SECRET_KEY"
FUNDED_KEY_ID_ENV = "PMUS_KEY_ID"
TIMEOUT_S = 15.0
GAP_S = 0.4                    # between reads: a handful of GETs, paced
MAX_POSITION_PAGES = 10
MAX_MARKETS = 8
SECRET_RX = re.compile(r"(key|token|secret|password|signature|auth|cookie"
                       r"|email|phone|address|ssn|tax|bank)", re.I)
RULE_RX = re.compile(r"(tick|incr|min|max|size|precision|step|lot|quantity"
                     r"|state|active|closed|tradable|status)", re.I)
BALANCE_FIELDS = ("currency", "currentBalance", "buyingPower", "assetNotional",
                  "assetAvailable", "pendingCredit", "openOrders",
                  "unsettledFunds", "marginRequirement", "balanceReservation",
                  "lastUpdated")
ORDER_FIELDS = ("id", "marketSlug", "side", "intent", "type", "price",
                "quantity", "cumQuantity", "leavesQuantity", "tif",
                "goodTillTime", "state", "avgPx", "createTime", "insertTime")
POSITION_FIELDS = ("netPosition", "qtyBought", "qtySold", "qtyAvailable",
                   "cost", "realized", "cashValue", "bodPosition", "expired",
                   "updateTime")


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def fingerprint(key_id: str) -> str | None:
    """A short stable identifier of the key id (sha256, 12 hex): enough to
    prove two reads used the same account, useless for authenticating."""
    return hashlib.sha256(key_id.encode()).hexdigest()[:12] if key_id else None


def keys_present() -> dict:
    kid, sec, funded = _env(KEY_ID_ENV), _env(SECRET_ENV), _env(FUNDED_KEY_ID_ENV)
    return {"version": VERSION,
            "names": {KEY_ID_ENV: bool(kid), SECRET_ENV: bool(sec)},
            "complete": bool(kid and sec),
            "distinct_from_funded_key": (None if not (kid and funded)
                                         else kid != funded),
            "funded_key_present_in_this_service": bool(funded),
            "key_fingerprint": fingerprint(kid),
            "market_data": market_data_key()}


#: THE INSTITUTIONAL MARKET-DATA IDENTITY ($0 balance, market data only) --
#: never the retail execution key, never the funded key.
MD_KEY_ID_ENV = "PMUS_MD_KEY_ID"
MD_SECRET_ENV = "PMUS_MD_SECRET_KEY"


def market_data_key() -> dict:
    """Presence and fingerprint of the market-data key, and that it is a
    DIFFERENT identity from both execution keys. Names and fingerprints
    only; no value leaves the process."""
    from . import market_data_identity as mdi

    md, msec = _env(MD_KEY_ID_ENV), _env(MD_SECRET_ENV)
    kid, funded = _env(KEY_ID_ENV), _env(FUNDED_KEY_ID_ENV)
    return {"names": {MD_KEY_ID_ENV: bool(md), MD_SECRET_ENV: bool(msec)},
            "complete": bool(md and msec),
            "key_fingerprint": fingerprint(md),
            "distinct_from_execution_mirror_key": (None if not (md and kid)
                                                   else md != kid),
            "distinct_from_funded_key": (None if not (md and funded)
                                         else md != funded),
            # EVERY candidate market-data credential in this process -- the
            # institutional PMX client as well as the PMUS_MD key -- with its
            # type, presence, identifier fingerprints, verifiable scopes and
            # distinctness from the retail execution and funded keys.
            "inventory": mdi.inventory()}


def _amount(v: Any) -> Any:
    if isinstance(v, dict) and "value" in v:
        return v.get("value")
    return v


def _pick(d: dict, fields) -> dict:
    return {f: _amount(d.get(f)) for f in fields if f in d}


def _meta(d: dict) -> dict:
    m = d.get("marketMetadata") or {}
    return {k: m.get(k) for k in ("slug", "title", "outcome", "eventSlug")
            if m.get(k) is not None}


TOKENISH_RX = re.compile(r"[A-Za-z0-9+/=_-]{20,}")


def _error(exc: Exception) -> dict:
    msg = str(exc) or type(exc).__name__
    for v in (_env(KEY_ID_ENV), _env(SECRET_ENV)):
        if v:
            msg = msg.replace(v, "[redacted]")
    msg = TOKENISH_RX.sub("[redacted]", msg)
    msg = SECRET_RX.sub("[redacted]", msg)[:240]
    return {"error": type(exc).__name__,
            "status": getattr(exc, "status_code", None), "detail": msg}


class _Reader:
    """An ALLOWLIST of reads over a client built from the execution-mirror
    credential. No mutation name exists on this object."""

    __slots__ = ("_c",)

    def __init__(self, key_id: str, secret_key: str):
        from polymarket_us import PolymarketUS
        object.__setattr__(self, "_c", PolymarketUS(
            key_id=key_id, secret_key=secret_key, timeout=TIMEOUT_S,
            max_retries=1))

    def balances(self):
        return self._c.account.balances()

    def positions(self, cursor: str | None = None):
        p = {"limit": 100}
        if cursor:
            p["cursor"] = cursor
        return self._c.portfolio.positions(p)

    def open_orders(self):
        return self._c.orders.list()

    def activities(self):
        return self._c.portfolio.activities({"limit": 50})

    def market(self, slug: str):
        return self._c.markets.retrieve_by_slug(slug)


def _reader() -> _Reader | None:
    kid, sec = _env(KEY_ID_ENV), _env(SECRET_ENV)
    if not (kid and sec):
        return None
    return _Reader(kid, sec)


def account_snapshot(reader: _Reader | None = None, *, sleep=time.sleep) -> dict:
    """Synchronous; the route runs it in a worker thread."""
    out: dict = {"version": VERSION, "read_only": True,
                 "at": time.time(), **{"keys": keys_present()}}
    r = reader or _reader()
    if r is None:
        out["state"] = "CREDENTIAL_ABSENT"
        return out
    timings = {}

    def timed(name, fn):
        t0 = time.monotonic()
        try:
            return fn(), None
        except Exception as exc:                              # noqa: BLE001
            return None, _error(exc)
        finally:
            timings[name] = round(time.monotonic() - t0, 3)

    bal, err = timed("balances", r.balances)
    if err:
        out["state"] = ("AUTHENTICATION_FAILED"
                        if err.get("status") in (401, 403) else "READ_FAILED")
        out["balances_error"] = err
        out["timings_s"] = timings
        return out
    out["authenticated"] = True
    out["balances"] = [dict(_pick(b, BALANCE_FIELDS),
                            pending_withdrawals=len(b.get("pendingWithdrawals") or []))
                       for b in (bal or {}).get("balances") or []]
    sleep(GAP_S)

    positions, cursor, pages, perr = [], None, 0, None
    while pages < MAX_POSITION_PAGES:
        res, perr = timed("positions_p%d" % pages, lambda: r.positions(cursor))
        if perr:
            break
        pages += 1
        for slug, p in ((res or {}).get("positions") or {}).items():
            positions.append(dict(slug=slug, **_pick(p, POSITION_FIELDS),
                                  market=_meta(p)))
        cursor = (res or {}).get("nextCursor")
        if (res or {}).get("eof", True) or not cursor:
            break
        sleep(GAP_S)
    out["positions"] = positions
    out["positions_complete"] = perr is None and pages < MAX_POSITION_PAGES
    if perr:
        out["positions_error"] = perr
    out["open_positions"] = sum(1 for p in positions
                                if str(p.get("netPosition") or "0") not in ("0", "0.0", ""))
    sleep(GAP_S)

    oo, oerr = timed("open_orders", r.open_orders)
    out["open_orders"] = [dict(_pick(o, ORDER_FIELDS), market=_meta(o))
                          for o in (oo or {}).get("orders") or []]
    if oerr:
        out["open_orders_error"] = oerr
    sleep(GAP_S)

    act, aerr = timed("activities", r.activities)
    acts = (act or {}).get("activities") or []
    kinds: dict = {}
    for a in acts:
        k = str(a.get("type") or "?")
        kinds[k] = kinds.get(k, 0) + 1
    out["activity"] = {"recent_count": len(acts), "by_type": kinds,
                       "newest": max((str(a.get("createTime") or a.get("time") or "")
                                      for a in acts), default=None)}
    if aerr:
        out["activity_error"] = aerr

    out["timings_s"] = timings
    out["state"] = ("OK" if not (perr or oerr or aerr) else "PARTIAL")
    out["fresh_account"] = (not positions and not out["open_orders"]
                            and not acts and not (perr or oerr or aerr))
    return out


def _rules(rec: Any, prefix: str = "", depth: int = 0, out=None) -> dict:
    """Fields of a market record that constrain an order, flattened."""
    out = {} if out is None else out
    if depth > 3 or len(out) > 60:
        return out
    if isinstance(rec, dict):
        for k, v in rec.items():
            name = prefix + str(k)
            if SECRET_RX.search(str(k)):
                continue
            if isinstance(v, (dict, list)):
                _rules(v, name + ".", depth + 1, out)
            elif RULE_RX.search(str(k)):
                out[name] = v
    elif isinstance(rec, list):
        for i, v in enumerate(rec[:4]):
            _rules(v, "%s%d." % (prefix, i), depth + 1, out)
    return out


def market_rules(slugs: list, reader: _Reader | None = None, *,
                 sleep=time.sleep) -> dict:
    slugs = [str(s).strip() for s in slugs if str(s).strip()][:MAX_MARKETS]
    r = reader or _reader()
    if r is None:
        return {"version": VERSION, "state": "CREDENTIAL_ABSENT"}
    out = {"version": VERSION, "markets": {}}
    for i, s in enumerate(slugs):
        if i:
            sleep(GAP_S)
        try:
            rec = r.market(s)
            m = (rec or {}).get("market", rec) if isinstance(rec, dict) else rec
            out["markets"][s] = {"keys": sorted(m.keys())[:80] if isinstance(m, dict) else None,
                                 "rules": _rules(m)}
        except Exception as exc:                              # noqa: BLE001
            out["markets"][s] = _error(exc)
    return out
