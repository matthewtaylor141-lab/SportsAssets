"""SMS alert channel: config parsing, watch-list scoping, text shaping."""

import pytest

from sportsassets.config import settings
from sportsassets.notifications import sms
from sportsassets.notifications.collapse import plan_deliveries


@pytest.fixture
def cfg(monkeypatch):
    c = settings()
    monkeypatch.setattr(c, "twilio_account_sid", "ACxxx")
    monkeypatch.setattr(c, "twilio_auth_token", "tok")
    monkeypatch.setattr(c, "twilio_from_number", "+15550001111")
    monkeypatch.setattr(c, "sms_to_numbers", "+15551234567, +15559876543")
    monkeypatch.setattr(c, "sms_watch_addresses", "")
    return c


def test_enabled_requires_all_creds_and_a_recipient(cfg, monkeypatch):
    assert sms.enabled() is True
    monkeypatch.setattr(cfg, "sms_to_numbers", "")
    assert sms.enabled() is False
    monkeypatch.setattr(cfg, "sms_to_numbers", "+15551234567")
    monkeypatch.setattr(cfg, "twilio_auth_token", "")
    assert sms.enabled() is False


def test_recipients_parsing_strips_and_skips_blanks(cfg):
    assert sms.recipients() == ["+15551234567", "+15559876543"]


def test_watch_addresses_lowercased(cfg, monkeypatch):
    monkeypatch.setattr(
        cfg, "sms_watch_addresses",
        "0x4D4F13A12D943FAABC0D154FB2D546F649A9E5F3, 0xabc",
    )
    assert sms.watch_addresses() == {"0x4d4f13a12d943faabc0d154fb2d546f649a9e5f3", "0xabc"}
    monkeypatch.setattr(cfg, "sms_watch_addresses", "")
    assert sms.watch_addresses() == set()  # empty = all whales


def _trade(i, whale_id=1, **kw):
    base = {"id": i, "whale_id": whale_id, "whale_username": "swisstony",
            "side": "BUY", "outcome": "Red Sox", "price": 0.52,
            "notional": 12500.0, "event_title": "Yankees vs. Red Sox",
            "sport": "MLB"}
    base.update(kw)
    return base


def test_single_trade_text_is_one_terse_line():
    d = plan_deliveries([_trade(1)], {}, threshold=5)[0]
    text = f"{d.title} — {d.body}"
    assert text == "swisstony BUY Red Sox @ 52¢ — $12,500 — Yankees vs. Red Sox"
    assert len(text) <= sms.MAX_SMS_CHARS


def test_burst_collapses_to_one_summary_text():
    trades = [_trade(i) for i in range(1, 8)]
    deliveries = plan_deliveries(trades, {}, threshold=5)
    assert len(deliveries) == 1
    assert deliveries[0].kind == "summary"
    assert "7 trades" in deliveries[0].title


async def test_broadcast_posts_to_each_recipient(cfg, monkeypatch):
    sent = []

    async def fake_send(to, body):
        sent.append((to, body))
        return {"ok": True, "to": to, "sid": "SMxx"}

    monkeypatch.setattr(sms, "send_one", fake_send)
    results = await sms.broadcast("hello")
    assert [r["to"] for r in results] == sms.recipients()
    assert all(r["ok"] for r in results)
    assert sent[0][1] == "hello"
