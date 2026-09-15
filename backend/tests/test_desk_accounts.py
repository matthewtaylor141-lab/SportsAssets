"""Desk accounts card (owner directive 2026-08-22): the Kalshi side is
built from the engine heartbeat's kalshi_account export, and degrades
— visibly flagged — to the account-link balance string plus the open
book at cost when an older engine hasn't published the export yet."""

from sportsassets.api.app import kalshi_accounts_view

NOW = 1_766_000_000.0


def test_full_export_shape():
    detail = {"kalshi_account": {
        "balance_usd": 512.34, "at": NOW - 60.0, "resting": 2,
        "exposure_usd": 88.0,
        "positions": [
            {"ticker": "KXMLBGAME-A", "qty": 10, "cost_usd": 4.0,
             "mark_bid": 0.55, "value_usd": 5.5},
            {"ticker": "KXMLBGAME-B", "qty": 3, "cost_usd": 1.5,
             "mark_bid": None, "value_usd": None},
        ]}}
    out = kalshi_accounts_view(detail, NOW)
    assert out["configured"] is True
    assert "degraded" not in out
    assert out["balance_usd"] == 512.34
    assert out["stale_s"] == 60.0
    assert out["resting"] == 2 and out["exposure_usd"] == 88.0
    p0, p1 = out["positions"]
    assert p0["unrealized"] == 1.5              # value - cost
    assert p1["unrealized"] is None             # unknown mark: no guess


def test_degraded_fallback_parses_balance_and_open_book():
    detail = {
        "account_link": {"kalshi": {"ok": True,
                                    "detail": "balance $1,234.50"}},
        "kalshi_open": {"n": 1, "cost": 4.2,
                        "rows": [{"ticker": "KXWTAMATCH-X", "qty": 6,
                                  "avg_cost": 0.7, "cost": 4.2}]},
    }
    out = kalshi_accounts_view(detail, NOW)
    assert out["degraded"] is True
    assert out["configured"] is True
    assert out["balance_usd"] == 1234.50
    assert out["exposure_usd"] == 4.2
    assert out["positions"] == [{"ticker": "KXWTAMATCH-X", "qty": 6,
                                 "cost_usd": 4.2, "mark_bid": None,
                                 "value_usd": None, "unrealized": None}]


def test_empty_heartbeat_is_unconfigured_not_a_crash():
    out = kalshi_accounts_view({}, NOW)
    assert out["configured"] is False
    assert out["degraded"] is True
    assert out["balance_usd"] is None
    assert out["positions"] == []


# ── Committed capital (option 2, owner directive 2026-08-22) ─────────
# The composite is ALWAYS labeled (committed_usd rides beside it); desk
# sessions lose the raw cash fields; the admin sees everything; and no
# field named cash/balance ever carries the padded figure.


def test_trading_capital_is_composite_and_labeled():
    import inspect

    from sportsassets.api import app as app_mod

    src = inspect.getsource(app_mod.api_desk_accounts)
    assert 'pm["trading_capital"] = round(pm["cash"] + committed, 2)' in src
    assert 'pm["committed_usd"]' in src, \
        "the composite must carry its own label field"
    # Desk sessions get the composite only — raw fields stripped.
    assert '"cash", "buying_power", "account_value"' in src
    assert 'role != "admin"' in src
    # The padded figure is never assigned into a cash field.
    assert 'pm["cash"] = round' not in src


def test_require_desk_returns_roles():
    import inspect

    from sportsassets.api.app import require_desk

    src = inspect.getsource(require_desk)
    assert 'return "admin"' in src and 'return "desk"' in src


def test_external_positions_partitioned_and_admin_only():
    import inspect

    from sportsassets.api import app as app_mod

    src = inspect.getsource(app_mod.api_desk_accounts)
    # Venue positions on markets the platform never ordered are split out
    assert "pm_external" in src and "our_slugs" in src
    # ...ride admin-only (never silently dropped, never shown to team)
    assert 'pm["external_positions"] = pm_external' in src
    assert src.index('if role == "admin"') < src.index(
        'pm["external_positions"]')
    # Open value sums PLATFORM positions, not the account-wide notional
    assert "if not pm_external" in src
