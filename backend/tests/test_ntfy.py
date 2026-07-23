"""ntfy alert channel: config gating, watch-list parsing, header safety."""

import pytest

from sportsassets.config import settings
from sportsassets.notifications import ntfy


@pytest.fixture
def cfg(monkeypatch):
    c = settings()
    monkeypatch.setattr(c, "ntfy_server", "https://ntfy.sh")
    monkeypatch.setattr(c, "ntfy_topic", "bt-whale-test-topic")
    monkeypatch.setattr(c, "ntfy_watch_addresses", "")
    return c


def test_enabled_requires_topic(cfg, monkeypatch):
    assert ntfy.enabled() is True
    monkeypatch.setattr(cfg, "ntfy_topic", "   ")
    assert ntfy.enabled() is False


def test_watch_addresses_lowercased(cfg, monkeypatch):
    monkeypatch.setattr(cfg, "ntfy_watch_addresses",
                        "0x4D4F13A12D943FAABC0D154FB2D546F649A9E5F3")
    assert ntfy.watch_addresses() == {"0x4d4f13a12d943faabc0d154fb2d546f649a9e5f3"}


async def test_publish_posts_title_header_and_body(cfg, monkeypatch):
    captured = {}

    class FakeResp:
        status_code = 200

        @staticmethod
        def json():
            return {"id": "msg1"}

    class FakeClient:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, content=None, headers=None):
            captured.update(url=url, content=content, headers=headers)
            return FakeResp()

    monkeypatch.setattr(ntfy.httpx, "AsyncClient", FakeClient)
    r = await ntfy.publish("swisstony BUY Red Sox @ 52¢", "$12,500 — Yankees vs. Red Sox")
    assert r["ok"] is True and r["id"] == "msg1"
    assert captured["url"] == "https://ntfy.sh/bt-whale-test-topic"
    # non-ASCII (¢) must be escaped in the header, body stays utf-8 bytes
    assert captured["headers"]["Title"].isascii()
    assert "swisstony BUY Red Sox" in captured["headers"]["Title"]
    assert "Yankees vs. Red Sox" in captured["content"].decode()
    assert captured["headers"]["Priority"] == "high"
