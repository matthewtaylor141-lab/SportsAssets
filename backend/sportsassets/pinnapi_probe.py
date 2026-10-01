"""PINNAPI READINESS PROBE (read-only, admin-only), run INSIDE the service
where the owner installed the key (`pinnapi_key`, case-sensitive).

Why it lives in the API: the key is a Render environment variable, never in
GitHub or the repository; the only way to test it without copying it is to
run the request where it already is.

What it does, nothing more:
  keys   which PinnAPI-looking environment variable NAMES are present in this
         process (booleans only -- never a value, a length or a prefix).
  docs   GET the provider's PUBLIC documentation (`/llms-full.txt`) WITHOUT
         the key, and return the lines matching a bounded pattern, so the
         integration is written from the current documentation.
  account  ONE authenticated GET of the account record (`/panel/api/me`),
         reduced to an allowlist of entitlement fields (plan, features,
         limits, expiry such as `ws_addon_until`); email, referral, key and
         token fields are never returned -- only the withheld key NAMES.
  rest   up to MAX_CALLS authenticated GETs to relative provider paths, at
         least GAP_S apart; returns each status, timing and a SANITIZED
         structure (counts, keys, short values; any field whose name looks
         secret is redacted; long strings truncated). The key travels only
         in the provider's auth header; redirects are never followed (the
         header would go to another origin).

It writes nothing, subscribes to nothing, opens no stream, and places no
order. Streaming entitlement is NOT tested here -- an extra stream consumer
could displace the production connection and needs the single ingestion
owner's coordination.
"""
from __future__ import annotations

import os
import re
import time
from collections import Counter
from typing import Any

BASE = "https://pinnapi.com"
KEY_ENV = "pinnapi_key"
AUTH_HEADER = "x-portal-apikey"
MAX_CALLS = 6
GAP_S = 1.0
TIMEOUT_S = 15.0
MAX_BYTES = 4 * 1024 * 1024
MAX_DOC_LINES = 400
PATH_RX = re.compile(r"^/(?!/)[A-Za-z0-9_./-]{0,200}(\?[A-Za-z0-9_=&.,:%-]{0,300})?$")
SECRET_RX = re.compile(r"(key|token|secret|password|auth|signature|cookie"
                       r"|email|referr)", re.I)
# The account record (`/panel/api/me`) is read ONLY through `account()`,
# which keeps an allowlist of entitlement fields; the generic `rest` refuses
# every other /panel/ path. Public plan list and the add-on quote are reads.
ACCOUNT_PATH = "/panel/api/me"
PANEL_REST_ALLOWED = ("/panel/api/plans", "/panel/api/addon/ws/quote")
ENTITLEMENT_RX = re.compile(r"(plan|tier|feature|limit|rate|quota|until|"
                            r"expir|status|sse|ws|rest|period|cap|renew|"
                            r"active|addon|add_on|interval|per_)", re.I)


def keys_present() -> dict:
    names = sorted(n for n in os.environ if "pinn" in n.lower())
    return {"probe": "PINNAPI_KEYS_V1", "expected_name": KEY_ENV,
            "expected_present": bool(os.environ.get(KEY_ENV)),
            "pinnapi_like_names_present": names,
            "values": "NEVER RETURNED"}


def sanitize(v: Any, depth: int = 0, *, max_list: int = 3) -> Any:
    """The SHAPE of a payload with short values: dict keys kept (secret-
    looking ones redacted), lists summarized by length plus their first few
    items, strings cut to 120 characters."""
    if depth > 6:
        return "…"
    if isinstance(v, dict):
        out = {}
        for k, x in list(v.items())[:60]:
            out[k] = "[REDACTED]" if SECRET_RX.search(str(k)) else sanitize(
                x, depth + 1, max_list=max_list)
        if len(v) > 60:
            out["…keys"] = len(v)
        return out
    if isinstance(v, list):
        return {"__len__": len(v),
                "first": [sanitize(x, depth + 1, max_list=max_list)
                          for x in v[:max_list]]}
    if isinstance(v, str):
        return v if len(v) <= 120 else v[:120] + "…"
    return v


def market_inventory(payload) -> dict:
    """Counts only (the shape Codex's probe reads): events, live/prematch,
    periods, periods carrying each market family. Not tradable contracts."""
    if not isinstance(payload, dict) or not isinstance(payload.get("events"),
                                                       list):
        return {"schema_valid": False,
                "top_level_type": type(payload).__name__,
                "top_level_keys": (sorted(payload)[:30]
                                   if isinstance(payload, dict) else None)}
    events = payload["events"]
    fam, phase, periods, with_state = Counter(), Counter(), 0, 0
    for e in events:
        if not isinstance(e, dict):
            continue
        ph = e.get("event_type")
        phase[ph if ph in ("live", "prematch") else "other"] += 1
        with_state += isinstance(e.get("state"), dict)
        tree = e.get("periods") or {}
        if isinstance(tree, dict):
            for p in tree.values():
                if isinstance(p, dict):
                    periods += 1
                    for f in ("money_line", "spreads", "totals",
                              "team_total", "team_totals"):
                        if p.get(f):
                            fam[f] += 1
    return {"schema_valid": True, "event_count": len(events),
            "phase_counts": dict(phase), "period_count": periods,
            "events_with_state": with_state,
            "periods_containing_family": dict(fam)}


async def docs(pattern: str | None = None, *, client=None) -> dict:
    import httpx
    rx = None
    if pattern:
        try:
            rx = re.compile(pattern[:200], re.I)
        except re.error:
            return {"ok": False, "reason": "BAD_PATTERN"}
    own = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT_S,
                                         follow_redirects=False)
    try:
        r = await client.get(BASE + "/llms-full.txt")
        if r.status_code != 200:
            return {"ok": False, "http": r.status_code}
        lines = r.text.splitlines()
        hits = [(i + 1, ln[:300]) for i, ln in enumerate(lines)
                if rx is None or rx.search(ln)]
        return {"ok": True, "http": 200, "total_lines": len(lines),
                "matched": len(hits),
                "lines": [{"n": n, "text": t} for n, t in hits[:MAX_DOC_LINES]],
                "truncated": len(hits) > MAX_DOC_LINES,
                "source": BASE + "/llms-full.txt (public, no key sent)"}
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "reason": type(exc).__name__}
    finally:
        if own:
            await client.aclose()


def entitlement_view(v: Any, depth: int = 0) -> Any:
    """ONLY the entitlement fields of the account record: a key is kept when
    its name looks like plan / feature / limit / expiry / status (and not
    secret or personal); scalars are kept, nested records filtered the same
    way, lists summarized by length. Every other key is listed by NAME only
    so the report says what was withheld."""
    if not isinstance(v, dict) or depth > 4:
        return None
    kept, withheld = {}, []
    for k, x in v.items():
        name = str(k)
        if SECRET_RX.search(name) or not ENTITLEMENT_RX.search(name):
            if isinstance(x, dict) and not SECRET_RX.search(name):
                sub = entitlement_view(x, depth + 1)
                if sub and sub.get("kept"):
                    kept[name] = sub
                    continue
            withheld.append(name)
            continue
        if isinstance(x, dict):
            kept[name] = entitlement_view(x, depth + 1)
        elif isinstance(x, list):
            kept[name] = {"__len__": len(x)}
        elif isinstance(x, str):
            kept[name] = x[:80]
        else:
            kept[name] = x
    return {"kept": kept, "withheld_keys": sorted(withheld)}


async def account(*, client=None) -> dict:
    """The account's PLAN, FEATURES and LIMITS from `/panel/api/me`, read
    privately: one authenticated GET, the entitlement allowlist above, no
    email, referral, key or token, nothing written."""
    import httpx
    key = os.environ.get(KEY_ENV)
    out: dict = {"probe": "PINNAPI_ACCOUNT_ENTITLEMENT_V1",
                 "key_present": bool(key), "path": ACCOUNT_PATH}
    if not key:
        out.update(ok=False, reason="KEY_NOT_PRESENT_IN_THIS_SERVICE")
        return out
    own = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT_S,
                                         follow_redirects=False)
    try:
        t0 = time.monotonic()
        r = await client.get(BASE + ACCOUNT_PATH, headers={
            AUTH_HEADER: key, "Accept": "application/json"})
        out.update(http=r.status_code,
                   elapsed_ms=round((time.monotonic() - t0) * 1000))
        if r.status_code != 200:
            out["ok"] = False
            return out
        try:
            payload = r.json()
        except Exception:                                       # noqa: BLE001
            out.update(ok=False, reason="BODY_NOT_JSON")
            return out
        out["entitlement"] = entitlement_view(payload)
        out["ok"] = True
        return out
    except Exception as exc:                                    # noqa: BLE001
        out.update(ok=False, reason=type(exc).__name__)
        return out
    finally:
        if own:
            await client.aclose()


async def rest(paths: list, *, client=None, sleep=None) -> dict:
    import asyncio
    import httpx
    key = os.environ.get(KEY_ENV)
    report: dict = {"probe": "PINNAPI_REST_READINESS_V2", "key_env": KEY_ENV,
                    "key_present": bool(key), "reads": [],
                    "stream_entitlement": "NOT_TESTED"}
    if not key:
        report["ok"] = False
        report["reason"] = "KEY_NOT_PRESENT_IN_THIS_SERVICE"
        return report
    clean = []
    for p in (paths or [])[:MAX_CALLS]:
        p = str(p)
        if not PATH_RX.match(p) or ".." in p:
            report["reads"].append({"path": p[:80], "ok": False,
                                    "reason": "PATH_REFUSED"})
            continue
        if p.startswith("/panel/") and p.split("?")[0] not in \
                PANEL_REST_ALLOWED:
            report["reads"].append({"path": p[:80], "ok": False,
                                    "reason": "ACCOUNT_PATHS_USE_THE_ACCOUNT"
                                              "_ACTION"})
            continue
        clean.append(p)
    own = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT_S,
                                         follow_redirects=False)
    sleep = sleep or asyncio.sleep
    try:
        for i, p in enumerate(clean):
            if i:
                await sleep(GAP_S)
            t0 = time.monotonic()
            row: dict = {"path": p}
            try:
                r = await client.get(BASE + p, headers={
                    AUTH_HEADER: key, "Accept": "application/json"})
                row.update(http=r.status_code,
                           elapsed_ms=round((time.monotonic() - t0) * 1000),
                           content_type=r.headers.get("content-type"),
                           rate_limit_headers={
                               k: v for k, v in r.headers.items()
                               if "ratelimit" in k.lower()
                               or "quota" in k.lower()
                               or k.lower() == "retry-after"})
                if 300 <= r.status_code < 400:
                    row["redirect_not_followed"] = True
                body = r.content[:MAX_BYTES + 1]
                if len(body) > MAX_BYTES:
                    row["reason"] = "RESPONSE_EXCEEDS_PROBE_LIMIT"
                else:
                    try:
                        payload = r.json()
                        row["structure"] = sanitize(payload)
                        if "/markets" in p:
                            row["inventory"] = market_inventory(payload)
                    except Exception:                           # noqa: BLE001
                        row["body_is_json"] = False
                row["ok"] = 200 <= r.status_code < 300
            except Exception as exc:                            # noqa: BLE001
                row.update(ok=False, reason=type(exc).__name__)
            report["reads"].append(row)
        report["ok"] = bool(report["reads"]) and all(
            x.get("ok") for x in report["reads"])
        return report
    finally:
        if own:
            await client.aclose()
