"""The mirror lane's cell verdict (2026-09-06 20:0xZ): the copy lane's
soccer/esports price floor is lifted BY NAME for a mirror book (owner
order: "everything he takes ... never force us to decline"), counted
`soccer_floor_lifted`; every other cell clause refuses as before; and the
increase recheck reads the tick's own level, not a column the open-books
query never selects (books 20, 23-26 were refused
`cell_gate_soccer_price_floor` on every tick)."""
from __future__ import annotations

import asyncio
import inspect

from sportsassets import copy_sports
from sportsassets.workers import mirror_live as ml


def _census():
    ml._current_stats = {"census": {}}
    return ml._current_stats["census"]


def test_the_soccer_floor_is_lifted_by_name_and_counted(monkeypatch):
    c = _census()
    calls = []

    def _cv(w, slug, price=None):
        calls.append((w, slug, price))
        return "soccer_price_floor"

    monkeypatch.setattr(copy_sports, "copy_verdict", _cv)
    assert ml._mirror_cell("rn1", "sea-juv-mil-2026-09-06-juv", 0.3) is None
    assert c.get("soccer_floor_lifted") == 1
    assert calls == [("rn1", "sea-juv-mil-2026-09-06-juv", 0.3)]
    # the unreadable-price spelling of the same floor is lifted too
    monkeypatch.setattr(copy_sports, "copy_verdict", lambda *a, **k: "soccer_price_unreadable")
    assert ml._mirror_cell("rn1", "cs2-hero-1win-2026-09-06", None) is None
    assert c.get("soccer_floor_lifted") == 2
    ml._current_stats = None


def test_every_other_cell_clause_still_refuses(monkeypatch):
    c = _census()
    for clause in ("no_whale", "whale_paused", "sport_halted", "market_type_blocked",
                   "no_cells_for_whale", "sport_unknown", "cell_not_allowed",
                   "band_needs_price", "band_price_unreadable", "outside_entry_band"):
        monkeypatch.setattr(copy_sports, "copy_verdict", (lambda _c: lambda *a, **k: _c)(clause))
        assert ml._mirror_cell("rn1", "sea-juv-mil-2026-09-06-juv", 0.7) == clause, clause
    assert "soccer_floor_lifted" not in c
    # the real gate: a soccer pick under the floor passes the mirror, a halted sport does not
    monkeypatch.undo()
    _census()
    assert copy_sports.copy_verdict("rn1", "sea-juv-mil-2026-09-06-juv", price=0.1) == "soccer_price_floor"
    assert ml._mirror_cell("rn1", "sea-juv-mil-2026-09-06-juv", 0.1) is None
    ml._current_stats = None


def test_the_increase_recheck_reads_the_ticks_level_not_a_missing_column(monkeypatch):
    src = inspect.getsource(ml._increase_recheck)
    assert 'book.get("his_level")' not in src
    assert "_mirror_cell(r.whale, his_slug, his_px)" in src
    # the caller computes his level BEFORE the recheck and hands it over
    tick = inspect.getsource(ml._tick_book)
    assert tick.index("his_px = _his_level(") < tick.index("_increase_recheck(t, book, r, his_px)")
    # the open-books query does not carry his_level (the column the old
    # recheck read): pinned so the fix is not undone by the same mistake
    assert "his_level" not in ml._SQL_BOOK_COLS
    # and the candidate admission goes through the same verdict
    assert "_mirror_cell(w, his_slug, his_px)" in inspect.getsource(ml._tick_candidate)
    assert "soccer_floor_lifted" in ml.CENSUS_KEYS

    # the recheck passes the level it is handed to the cell verdict
    seen = []

    async def _admit(*a, **k):
        return True, None

    monkeypatch.setattr(ml, "_admit_source", _admit)
    monkeypatch.setattr(ml.edge_gate, "verdict", lambda w: (True, None))
    monkeypatch.setattr(ml.le, "per_fill_usd", lambda *a, **k: 250.0)
    monkeypatch.setattr(copy_sports, "copy_verdict",
                        lambda w, slug, price=None: seen.append((slug, price)) or None)

    class _R:
        whale, slug = "rn1", "tsc-sea-juv-mil-2026-09-06-0pt5"
        fills = [{"market_slug": "sea-juv-mil-2026-09-06-total-0pt5"}]

    _census()
    assert asyncio.run(ml._increase_recheck(None, {"map_source": "exact"}, _R(), 0.7)) is None
    assert seen == [("sea-juv-mil-2026-09-06-total-0pt5", 0.7)]
    ml._current_stats = None
