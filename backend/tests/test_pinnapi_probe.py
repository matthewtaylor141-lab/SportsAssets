"""THE PINNAPI READINESS PROBE NEVER DISCLOSES THE KEY.

  * `keys` reports names and presence only -- no value, length or prefix;
  * `docs` fetches the public documentation WITHOUT the auth header;
  * `rest` sends the key ONLY in the provider's auth header, ONLY to
    pinnapi.com, never follows a redirect, refuses absolute URLs and path
    traversal, caps the number of calls, and redacts secret-looking fields
    in what it returns.
No network: httpx.MockTransport.
"""
from __future__ import annotations

import json

import httpx
import pytest

from sportsassets import pinnapi_probe as PP

SECRET = "sk_test_THIS_MUST_NEVER_APPEAR_0123456789"


def test_keys_reports_presence_and_names_only(monkeypatch):
    monkeypatch.setenv("pinnapi_key", SECRET)
    out = PP.keys_present()
    assert out["expected_present"] is True
    assert "pinnapi_key" in out["pinnapi_like_names_present"]
    assert SECRET not in json.dumps(out)
    assert SECRET[:6] not in json.dumps(out)
    monkeypatch.delenv("pinnapi_key")
    assert PP.keys_present()["expected_present"] is False


async def test_rest_refuses_without_the_key(monkeypatch):
    monkeypatch.delenv("pinnapi_key", raising=False)
    out = await PP.rest(["/health"])
    assert out["ok"] is False
    assert out["reason"] == "KEY_NOT_PRESENT_IN_THIS_SERVICE"


async def test_rest_sends_the_key_only_in_the_header_and_returns_no_secret(
        monkeypatch):
    monkeypatch.setenv("pinnapi_key", SECRET)
    seen = []

    def handler(req: httpx.Request):
        seen.append(req)
        if req.url.path == "/redirect":
            return httpx.Response(302, headers={"location":
                                                "https://evil.example/x"})
        if req.url.path == "/kit/v1/markets":
            return httpx.Response(200, json={"events": [
                {"event_type": "live", "state": {"inning": 3},
                 "periods": {"0": {"money_line": {"home": 1.9},
                                   "totals": [{"points": 8.5}]}}},
                {"event_type": "prematch", "periods": {"0": {
                    "spreads": [{"hdp": -1.5}]}}}],
                "api_key": "echoed-by-a-careless-server"},
                headers={"x-ratelimit-remaining": "99"})
        return httpx.Response(200, json={"status": "ok",
                                         "plan": {"name": "base",
                                                  "token": "abc"}})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                               follow_redirects=False)
    slept = []

    async def nosleep(s):
        slept.append(s)
    out = await PP.rest(
        ["/health", "/kit/v1/markets?sport_id=3&event_type=live",
         "/redirect", "https://evil.example/steal", "/../etc", "/a", "/b",
         "/c", "/d"], client=client, sleep=nosleep)
    await client.aclose()
    blob = json.dumps(out)
    assert SECRET not in blob and SECRET[:8] not in blob
    # the key went only to pinnapi.com, only in the auth header
    for r in seen:
        assert r.url.host == "pinnapi.com"
        assert r.headers.get(PP.AUTH_HEADER) == SECRET
        assert SECRET not in str(r.url)
    # absolute URL and traversal refused; at most MAX_CALLS considered
    refused = [x for x in out["reads"] if x.get("reason") == "PATH_REFUSED"]
    assert len(refused) == 2
    assert len(seen) <= PP.MAX_CALLS
    # the redirect was not followed
    red = [x for x in out["reads"] if x["path"] == "/redirect"][0]
    assert red["http"] == 302 and red["redirect_not_followed"] is True
    assert not any(r.url.host == "evil.example" for r in seen)
    # secret-looking fields redacted; inventory counted
    mk = [x for x in out["reads"] if x["path"].startswith("/kit")][0]
    assert mk["structure"]["api_key"] == "[REDACTED]"
    assert mk["inventory"]["phase_counts"] == {"live": 1, "prematch": 1}
    assert mk["inventory"]["periods_containing_family"] == {
        "money_line": 1, "totals": 1, "spreads": 1}
    assert mk["rate_limit_headers"] == {"x-ratelimit-remaining": "99"}
    h = [x for x in out["reads"] if x["path"] == "/health"][0]
    assert h["structure"]["plan"]["token"] == "[REDACTED]"
    assert all(s >= PP.GAP_S for s in slept)


async def test_docs_never_send_the_key(monkeypatch):
    monkeypatch.setenv("pinnapi_key", SECRET)
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, text="# PinnAPI\nGET /kit/v1/markets\n"
                                        "sport_id baseball = 3\nother\n")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    out = await PP.docs("kit|sport_id", client=client)
    await client.aclose()
    assert out["ok"] and out["matched"] == 2
    assert all(PP.AUTH_HEADER not in r.headers for r in seen)
    assert (await PP.docs("(")) ["reason"] == "BAD_PATTERN"


async def test_account_returns_only_entitlement_fields(monkeypatch):
    monkeypatch.setenv("pinnapi_key", SECRET)
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json={
            "email": "owner@example.com", "api_key": SECRET,
            "sse_token": "tok-123", "user_id": 77,
            "plan_id": "scale_30d", "plan_until": 1790000000,
            "ws_addon_until": 0,
            "features": {"sse": True, "rest": True, "ws": False},
            "limits": {"per_second": 30},
            "referral": {"code": "aBc12XyZ", "rate_pct": 30,
                         "referrals": [{"email": "j***@gmail.com"}]}})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    out = await PP.account(client=client)
    await client.aclose()
    blob = json.dumps(out)
    for leaked in (SECRET, "owner@example.com", "tok-123", "aBc12XyZ",
                   "gmail"):
        assert leaked not in blob, leaked
    kept = out["entitlement"]["kept"]
    assert kept["plan_id"] == "scale_30d" and kept["ws_addon_until"] == 0
    assert kept["features"]["kept"] == {"sse": True, "rest": True,
                                        "ws": False}
    assert kept["limits"]["kept"] == {"per_second": 30}
    assert {"email", "api_key", "sse_token", "referral", "user_id"} <= set(
        out["entitlement"]["withheld_keys"])
    assert [r.url.path for r in seen] == ["/panel/api/me"]
    assert seen[0].headers.get(PP.AUTH_HEADER) == SECRET
    # the generic read refuses the account record and other panel paths
    r = await PP.rest(["/panel/api/me", "/panel/api/signup"])
    assert {x["reason"] for x in r["reads"]} == {
        "ACCOUNT_PATHS_USE_THE_ACCOUNT_ACTION"}


@pytest.mark.parametrize("bad", ["https://pinnapi.com/x", "//evil.example",
                                 "health", "/a b", "/x?y=<script>"])
def test_the_path_pattern_refuses_non_relative_paths(bad):
    assert not PP.PATH_RX.match(bad)


class _FakeWS:
    def __init__(self, frames, seen):
        self.frames, self.seen = list(frames), seen

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        self.seen["closed"] = True

    async def send(self, s):
        self.seen.setdefault("sent", []).append(json.loads(s))

    async def recv(self):
        import asyncio
        if self.frames:
            return self.frames.pop(0)
        await asyncio.sleep(30)


class _FakeLease:
    def __init__(self, held):
        self.held, self.calls = held, []

    async def __call__(self):
        return self, self.held

    async def execute(self, sql, *a):
        self.calls.append(sql)

    async def close(self):
        self.calls.append("close")


async def test_ws_sample_refuses_while_the_owner_holds_the_lease(monkeypatch):
    monkeypatch.setenv("pinnapi_key", SECRET)
    opened = []
    lease = _FakeLease(held=False)
    out = await PP.ws_sample([6], seconds=1, lease=lease,
                             connect=lambda u, k: opened.append(u))
    assert out["ok"] is False
    assert out["reason"] == "INGESTION_OWNER_HOLDS_THE_FEED_LEASE"
    assert opened == [], "no socket opened: it would evict the owner"
    assert lease.calls == ["close"], "nothing to unlock; connection closed"


async def test_ws_sample_is_bounded_answers_pings_and_reports_shapes(
        monkeypatch):
    monkeypatch.setenv("pinnapi_key", SECRET)
    seen, keys = {}, []
    frames = [
        json.dumps({"type": "subscribed", "stream": "live", "sport_id": 6}),
        json.dumps({"type": "snapshot", "stream": "live", "sport_id": 6,
                    "ts": 1000, "events": [{"id": 1}, {"id": 2}]}),
        json.dumps({"type": "live", "sport_id": 6, "op": "upd",
                    "topic": "matchups/reg/sp/3/live/ld", "ts": 1000,
                    "rec": {"id": 1, "markets": [
                        {"key": "s;0;m", "type": "moneyline",
                         "prices": [{"designation": "home",
                                     "price": -120}]}]}}),
        json.dumps({"type": "ping", "ts": 1001}),
    ]

    def connect(url, key):
        keys.append((url, key))
        return _FakeWS(frames, seen)
    lease = _FakeLease(held=True)
    out = await PP.ws_sample([6, 99, 1], streams=["live", "bogus"],
                             seconds=1, lease=lease, connect=connect,
                             now_ms=lambda: 1040.0)
    blob = json.dumps(out)
    assert SECRET not in blob
    assert keys == [(PP.WS_URL, SECRET)], "key passed to the header factory"
    assert SECRET not in PP.WS_URL
    assert seen["sent"][0] == {"type": "subscribe", "streams": ["live"],
                               "sport_ids": [1, 6]}
    assert seen["sent"][1] == {"type": "pong"} and out["pongs_sent"] == 1
    assert out["frame_kinds"] == {"subscribed": 1, "snapshot": 1,
                                  "live:upd": 1, "ping": 1}
    assert out["snapshot_events"] == {"live/6": 2}
    assert out["live_topics"] == {"matchups/reg/sp/{n}/live/ld": 1}
    assert out["provider_stamp_to_receipt_ms"]["p50"] == 40.0
    # the market merge key stays visible in the sanitized shape
    rec = out["samples"]["live:upd"][0]["rec"]
    assert rec["markets"]["first"][0]["key"] == "s;0;m"
    assert out["ok"] is True and seen["closed"]
    assert any("pg_advisory_unlock" in c for c in lease.calls)
    assert lease.calls[-1] == "close"


async def test_ws_sample_reports_a_refused_upgrade_without_the_key(
        monkeypatch):
    monkeypatch.setenv("pinnapi_key", SECRET)

    class Refused(Exception):
        class response:
            status_code = 403
            body = b'{"error":"plan_lacks_ws"}'

    class Conn:
        async def __aenter__(self):
            raise Refused("forbidden")

        async def __aexit__(self, *a):
            return False
    out = await PP.ws_sample([6], seconds=1, lease=_FakeLease(True),
                             connect=lambda u, k: Conn())
    assert out["ok"] is False
    assert out["connect_error"]["http"] == 403
    assert "plan_lacks_ws" in out["connect_error"]["body"]
    assert SECRET not in json.dumps(out)


def test_the_sampler_never_follows_a_redirect():
    c = PP._ws_connect(PP.WS_URL, SECRET)
    exc = RuntimeError("302")
    assert c.process_redirect(exc) is exc
