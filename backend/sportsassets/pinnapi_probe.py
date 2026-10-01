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
SECRET_RX = re.compile(r"(key|token|secret|password|auth|signature|cookie)",
                       re.I)


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
