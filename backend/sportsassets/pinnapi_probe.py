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

  ws_sample  a BOUNDED sample of the raw WebSocket feed (`/ws/feed`, the
         paid add-on): at most WS_MAX_SECONDS, a few sports, then closed.
         The provider allows ONE socket per account and a second evicts the
         first, so the sample first takes the SAME Postgres advisory lock
         the ingestion owner holds for its socket's life (FEED_LOCK_KEY); if
         the owner holds it, the sample refuses rather than displacing the
         production connection. The key goes only in the upgrade header,
         redirects are never followed, and only frame SHAPES, counts and
         timings are returned.

It writes nothing in our database or at the provider and places no order.
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

# THE ONE PINNAPI SOCKET PER ACCOUNT. The ingestion owner holds this session
# advisory lock for as long as its socket is open (the writer-lock family of
# api/command_rn1x.WRITER_LOCKS); anything else that would open the socket --
# this sampler included -- must take it first and refuse when it cannot.
FEED_LOCK_KEY = 7723901544120036  # 'pinnapi-feed-owner'
WS_URL = "wss://pinnapi.com/ws/feed"
WS_MAX_SECONDS = 20
WS_MAX_SPORTS = 4
WS_MAX_FRAME_BYTES = 32 * 1024 * 1024
#: ── THE CLIENT KEEPALIVE MATCHES THE PROVIDER'S OWN LIVENESS RULE (RC6) ──
#: The connector passed no keepalive settings, so the websockets library's
#: defaults applied: a ping every 20 s, the connection FAILED BY THIS CLIENT
#: (1011 "keepalive ping timeout") when the pong is not processed within
#: 20 s. The pong is processed by the same event loop that runs the API; a
#: loop that cannot run for 20 s (the API's loop watchdog logged stalls at
#: 18:52:21Z and 18:55:04-18:56:07Z on 2026-10-08, before two of the three
#: closes that stopped the feed -- pinnapi_owner.R_CLIENT_CLOSED) kills a
#: socket the provider is serving perfectly well, and every reconnect is a
#: new epoch that reloads every subscribed snapshot. The provider's own
#: contract is an application ping every 30 s and a close "after ~75s of
#: silence" (docs, tests/fixtures/pinnapi_ws_subscription_docs_2026_10_04
#: .json); the owner already reconnects after SILENCE_S (75 s) without a
#: frame. So the client's pong deadline is that same 75 s: a dead socket is
#: still found within SILENCE_S (unchanged), a healthy one is not failed by
#: our own stall. Prices are unaffected: every quote is aged from its
#: provider stamp under the unchanged 30 s rule, so a late-processed frame
#: is never fresher than it is.
WS_PING_INTERVAL_S = 20.0
WS_PING_TIMEOUT_S = 75.0
WS_SAMPLES_PER_KIND = 2
# frames come from Pinnacle, not from an account: keep market `key`s (the
# merge key) visible and redact only credential/personal-looking names
FRAME_SECRET_RX = re.compile(r"(token|secret|password|auth|signature|cookie"
                             r"|api_?key|email)", re.I)


def keys_present() -> dict:
    names = sorted(n for n in os.environ if "pinn" in n.lower())
    return {"probe": "PINNAPI_KEYS_V1", "expected_name": KEY_ENV,
            "expected_present": bool(os.environ.get(KEY_ENV)),
            "pinnapi_like_names_present": names,
            "values": "NEVER RETURNED"}


def sanitize(v: Any, depth: int = 0, *, max_list: int = 3,
             secret_rx=None) -> Any:
    """The SHAPE of a payload with short values: dict keys kept (secret-
    looking ones redacted), lists summarized by length plus their first few
    items, strings cut to 120 characters."""
    rx = secret_rx or SECRET_RX
    if depth > 6:
        return "…"
    if isinstance(v, dict):
        out = {}
        for k, x in list(v.items())[:60]:
            out[k] = "[REDACTED]" if rx.search(str(k)) else sanitize(
                x, depth + 1, max_list=max_list, secret_rx=rx)
        if len(v) > 60:
            out["…keys"] = len(v)
        return out
    if isinstance(v, list):
        return {"__len__": len(v),
                "first": [sanitize(x, depth + 1, max_list=max_list,
                                   secret_rx=rx)
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


def _pctl(xs: list, q: float):
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


async def _feed_lease():
    """The ingestion owner's lease, tried once on a DEDICATED connection (not
    a pool slot). Returns (connection, held)."""
    import asyncpg
    from . import db
    conn = await asyncpg.connect(db._dsn())
    held = bool(await conn.fetchval("SELECT pg_try_advisory_lock($1)",
                                    FEED_LOCK_KEY))
    return conn, held


def _ws_connect(url: str, key: str):
    """websockets' connect, with redirects REFUSED (the auth header would be
    replayed to the redirect target) and the key only in the upgrade
    header."""
    from websockets.asyncio.client import connect

    class _NoRedirect(connect):
        def process_redirect(self, exc):                        # noqa: D401
            return exc
    return _NoRedirect(url, additional_headers={"x-api-key": key},
                       open_timeout=15, max_size=WS_MAX_FRAME_BYTES,
                       ping_interval=WS_PING_INTERVAL_S,
                       ping_timeout=WS_PING_TIMEOUT_S, proxy=None)


async def ws_sample(sport_ids, streams=("live", "prematch"),
                    seconds: float = 10.0, *, connect=None, lease=None,
                    now_ms=None) -> dict:
    """A bounded, lease-guarded sample of `/ws/feed`: frame kinds, counts,
    sizes, sanitized shapes, and the provider-stamp-to-receipt delay of each
    live frame (clock skew between the provider and this host is included
    in that figure and is NOT corrected)."""
    import asyncio
    import json as _json
    key = os.environ.get(KEY_ENV)
    out: dict = {"probe": "PINNAPI_WS_SAMPLE_V1", "key_present": bool(key),
                 "lease_key": FEED_LOCK_KEY}
    if not key:
        out.update(ok=False, reason="KEY_NOT_PRESENT_IN_THIS_SERVICE")
        return out
    try:
        sports = sorted({int(s) for s in (sport_ids or [])})
    except (TypeError, ValueError):
        sports = []
    sports = [s for s in sports if 1 <= s <= 12][:WS_MAX_SPORTS]
    strm = [s for s in (streams or []) if s in ("live", "prematch")] \
        or ["live"]
    secs = max(1.0, min(float(seconds or 10), WS_MAX_SECONDS))
    if not sports:
        out.update(ok=False, reason="SPORT_IDS_REQUIRED_1_TO_12")
        return out
    out.update(sport_ids=sports, streams=strm, seconds=secs)
    now_ms = now_ms or (lambda: time.time() * 1000.0)
    lease = lease or _feed_lease
    connect = connect or _ws_connect
    lconn, held = await lease()
    try:
        if not held:
            out.update(ok=False,
                       reason="INGESTION_OWNER_HOLDS_THE_FEED_LEASE",
                       note="the production socket is open; a sample would "
                            "evict it, so none was opened")
            return out
        kinds: Counter = Counter()
        topics: Counter = Counter()
        samples: dict = {}
        delays: list = []
        errors: list = []
        nbytes = frames = pongs = 0
        snap_events: Counter = Counter()
        t_open = time.monotonic()
        try:
            async with connect(WS_URL, key) as ws:
                out["connect_ms"] = round((time.monotonic() - t_open) * 1000)
                await ws.send(_json.dumps({"type": "subscribe",
                                           "streams": strm,
                                           "sport_ids": sports}))
                deadline = time.monotonic() + secs
                while True:
                    left = deadline - time.monotonic()
                    if left <= 0:
                        break
                    try:
                        raw = await asyncio.wait_for(ws.recv(), left)
                    except asyncio.TimeoutError:
                        break
                    recv = now_ms()
                    frames += 1
                    nbytes += len(raw)
                    try:
                        msg = _json.loads(raw)
                    except Exception:                           # noqa: BLE001
                        kinds["non_json"] += 1
                        continue
                    t = msg.get("type") if isinstance(msg, dict) else None
                    op = msg.get("op") if isinstance(msg, dict) else None
                    kind = "%s:%s" % (t, op) if op else str(t)
                    kinds[kind] += 1
                    if t == "ping":
                        await ws.send(_json.dumps({"type": "pong"}))
                        pongs += 1
                        continue
                    if t == "error":
                        errors.append(str(msg.get("code") or
                                          msg.get("error"))[:60])
                    if t == "snapshot":
                        snap_events["%s/%s" % (msg.get("stream"),
                                               msg.get("sport_id"))] += len(
                            msg.get("events") or [])
                    if t == "live":
                        tp = str(msg.get("topic") or "")
                        parts = tp.split("/")
                        topics["/".join(p if not p.isdigit() else "{n}"
                                        for p in parts)] += 1
                        ts = msg.get("ts")
                        if isinstance(ts, (int, float)):
                            delays.append(recv - float(ts))
                    if len(samples.setdefault(kind, [])) < \
                            WS_SAMPLES_PER_KIND:
                        samples[kind].append(sanitize(
                            msg, max_list=2, secret_rx=FRAME_SECRET_RX))
        except Exception as exc:                                # noqa: BLE001
            resp = getattr(exc, "response", None)
            out["connect_error"] = {
                "type": type(exc).__name__,
                "http": getattr(resp, "status_code", None),
                "body": (bytes(getattr(resp, "body", b"") or b"")[:200]
                         .decode("utf-8", "replace") if resp else None)}
        out.update(
            frames=frames, bytes=nbytes, pongs_sent=pongs,
            frame_kinds=dict(kinds), live_topics=dict(topics),
            snapshot_events=dict(snap_events), errors=errors,
            provider_stamp_to_receipt_ms={
                "n": len(delays),
                "p50": _pctl(delays, .50), "p95": _pctl(delays, .95),
                "p99": _pctl(delays, .99),
                "max": max(delays) if delays else None,
                "caveat": "includes provider/host clock skew; not "
                          "source-to-decision latency"},
            samples=samples)
        out["ok"] = frames > 0 and not errors and "connect_error" not in out
        return out
    finally:
        try:
            if held:
                await lconn.execute("SELECT pg_advisory_unlock($1)",
                                    FEED_LOCK_KEY)
        finally:
            await lconn.close()
