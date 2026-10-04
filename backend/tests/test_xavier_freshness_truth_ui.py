"""XAVIER FRESHNESS TRUTH -- the published position-room page.

The position room is served from the frontend branch (claude/session-njaewf);
in a checkout that carries frontend/public/command/position.js it must render
the gated recommendation state. Split out of test_xavier_freshness_truth.py
(which is on the capital-critical list and runs in the backend release gate)
because this file is only present on the frontend branch: in a backend-only
checkout it skips, and a critical test may not skip. Same pattern as
test_resting_is_not_protection_ui.py.
"""
from __future__ import annotations

import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1] / "sportsassets"


def test_the_frontend_position_room_handles_the_state_when_present():
    """The position room page is published from the frontend branch; when
    this checkout carries it, it must render the gated state."""
    p = ROOT.parents[1] / "frontend" / "public" / "command" / "position.js"
    if not p.exists():
        pytest.skip("frontend/public/command/position.js is on the frontend "
                    "branch, not in this checkout")
    js = p.read_text()
    assert "recommendation_state" in js
    for s in ("CURRENT", "STALE", "INVALID", "WAITING_FOR_FRESH_EVIDENCE"):
        assert s in js, s
