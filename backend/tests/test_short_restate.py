"""The short-history restatement: only rows provably written under the
long model are touched, exactly once.

The venue's receipts settled the cost model (short-truth 2026-08-25:
every BUY_SHORT booked ORDER_SIDE_SELL). fill_cash books new rows
correctly since the arm; settled pnl comes from the venue ledger either
way. What this lever fixes is STORED HISTORY — filled_usd inflated by
p/(1-p) on pre-arm short rows — and the predicate makes it idempotent:
a row is restated only when its stored value matches the LONG model to
the cent AND differs from the short model. Never falsify a displayed
number: this restatement makes displayed numbers TRUE.
"""

import asyncio
import inspect
import json
from pathlib import Path

import pytest

from sportsassets.api import app as app_mod


class _Pool:
    def __init__(self, rows):
        self.rows = rows
        self.updates = []
        self.state_writes = []

    async def fetch(self, sql, *a, **k):
        return self.rows

    async def execute(self, sql, *a, **k):
        if "ingestion_state" in sql:
            self.state_writes.append(a)
        else:
            self.updates.append(a)


def _row(id_, qty, px, booked):
    return {"id": id_, "us_market_slug": f"m{id_}", "qty": qty,
            "fill_px": px, "booked_usd": booked}


def _run(rows, monkeypatch):
    pool = _Pool(rows)

    async def _gp():
        return pool
    monkeypatch.setattr(app_mod, "get_pool", _gp)
    summary = asyncio.run(app_mod.api_short_restate())
    return pool, summary


def test_long_math_row_restated_exactly_once(monkeypatch):
    # 1136 @ 0.78: long $886.08 (stored), short $249.92 (truth)
    pool, s = _run([_row(1, 1136, 0.78, 886.08)], monkeypatch)
    assert s["restated"] == 1
    assert pool.updates == [(1, 249.92)]
    assert s["delta_usd"] == round(249.92 - 886.08, 2)

    # second run: the stored value is now the short model — untouched
    pool2, s2 = _run([_row(1, 1136, 0.78, 249.92)], monkeypatch)
    assert s2["restated"] == 0 and pool2.updates == [], \
        "idempotent: a corrected row can never be re-restated"


def test_correct_and_ambiguous_rows_untouched(monkeypatch):
    rows = [
        _row(2, 520, 0.55, 234.00),    # already short-model: correct
        _row(3, 520, 0.55, 240.00),    # matches NEITHER model: unknown
        _row(4, 0, 0.55, 100.00),      # zero shares: skip
        _row(5, 520, 0.0, 100.00),     # no price: skip
    ]
    pool, s = _run(rows, monkeypatch)
    assert s["restated"] == 0 and pool.updates == [], \
        "only rows PROVABLY written under long math may be restated"
    assert s["examined"] == 4


def test_delta_sums_and_summary_persisted(monkeypatch):
    rows = [_row(1, 1136, 0.78, 886.08), _row(2, 520, 0.55, 286.00)]
    pool, s = _run(rows, monkeypatch)
    assert s["restated"] == 2
    expect = round((249.92 - 886.08) + (234.00 - 286.00), 2)
    assert s["delta_usd"] == expect
    assert pool.state_writes and pool.state_writes[0][0] == "short_restate"
    stored = json.loads(pool.state_writes[0][1])
    assert stored["restated"] == 2


def test_pnl_is_never_touched():
    src = inspect.getsource(app_mod.api_short_restate)
    assert "SET filled_usd" in src
    assert "pnl" not in src.split('"""')[2], \
        "the lever restates filled_usd ONLY — settled pnl is venue-truth"


def test_probe_fires_only_on_sell_verdict():
    # app.py sits at <root>/backend/sportsassets/api/app.py, so the
    # repo root is parents[3] — the old parents[3].parent overshot by
    # one and only a machine-specific fallback path kept this passing
    # locally while CI failed on it.
    wf = Path(app_mod.__file__).parents[3] / \
        ".github/workflows/engine-diagnostic.yml"
    if not wf.exists():
        pytest.skip("workflow file not present in this checkout")
    src = wf.read_text()
    i = src.index("short-restate")
    guard = src[max(0, i - 600):i]
    assert 'test("SHORT IS A SELL")' in guard, \
        "the probe must gate the restatement on the venue's own verdict"
