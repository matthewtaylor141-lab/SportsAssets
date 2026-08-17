"""SSE fast lane (owner green-light 2026-08-17 evening, upgrade #1):
the wake payload becomes a synthetic identity row priced ahead of the
identities fetch, and the merge must never let a synthetic row overrule
the platform's real pmus_copied claim state."""

from edge.shadow.runner import _merge_fresh_identity_rows


def _row(asset, whale="rn1", copied=False, **kw):
    return {"asset": asset, "slug": f"mlb-a-b-2026-08-17",
            "outcome": "A", "price": 0.5, "whale": whale,
            "entered_ts": 1.0, "pmus_copied": copied, **kw}


def test_fresh_asset_moves_to_the_front():
    fresh = [_row("9")]
    rows = [_row("1"), _row("2")]
    merged = _merge_fresh_identity_rows(fresh, rows)
    assert [r["asset"] for r in merged] == ["9", "1", "2"]


def test_identities_row_wins_for_a_known_asset():
    """The synthetic row hard-codes pmus_copied=False; if PMUS already
    holds the claim (submitting/filled), pricing the synthetic row
    would buy the position on BOTH venues."""
    fresh = [_row("1", copied=False)]
    rows = [_row("1", copied=True), _row("2")]
    merged = _merge_fresh_identity_rows(fresh, rows)
    assert [r["asset"] for r in merged] == ["1", "2"]
    assert merged[0]["pmus_copied"] is True, \
        "the platform's claim state must survive the merge"


def test_duplicate_fresh_rows_collapse_to_one():
    fresh = [_row("1", price=0.5), _row("1", price=0.52)]
    merged = _merge_fresh_identity_rows(fresh, [])
    assert len(merged) == 1


def test_no_fresh_rows_is_identity():
    rows = [_row("1"), _row("2")]
    assert _merge_fresh_identity_rows([], rows) == rows
