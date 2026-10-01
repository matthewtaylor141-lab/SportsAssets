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


@pytest.mark.parametrize("bad", ["https://pinnapi.com/x", "//evil.example",
                                 "health", "/a b", "/x?y=<script>"])
def test_the_path_pattern_refuses_non_relative_paths(bad):
    assert not PP.PATH_RX.match(bad)
