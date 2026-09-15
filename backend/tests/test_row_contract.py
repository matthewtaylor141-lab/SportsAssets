"""The matcher reads fields the query may not select, and every test
builds its rows BY HAND — so the whole class is invisible in CI.

This is the exact shape of the `signed` defect, in the coverage
fleet's own words:

    "The unit tests build rows in memory WITH the field, so the guard
     is green in CI and inert-then-blocking in production."

`signed` was computed by the sweep, read by the matcher, and had no
column, no INSERT entry and no SELECT entry. Every unit test passed
throughout, because every unit test hands match_side a dict it wrote
itself. Production handed it a dict the database wrote, and the field
was not there — so every signed spread refused, silently, for as long
as the feature had existed.

Failure mode (c), and STRUCTURAL rather than a one-off, because
nothing anywhere connected these four lists:

    what _market_rows PRODUCES
    what the INSERT persists
    what the SELECTs read back
    what match_side CONSUMES

This connects them. It is the guard against the NEXT `signed`.
"""

import inspect
import re

from sportsassets.workers import premap


def _fields_read_by(fn):
    src = inspect.getsource(fn)
    return set(re.findall(r'r(?:ow)?\.get\(\s*"([a-z_]+)"', src)) | \
        set(re.findall(r'r(?:ow)?\[\s*"([a-z_]+)"\s*\]', src))


def _selected_columns():
    out = set()
    for fn in (premap.resolve, premap.resolve_explain):
        src = inspect.getsource(fn)
        for chunk in re.findall(r'"SELECT ([^"]+)"', src):
            out |= {c.strip() for c in chunk.split(",")}
        for chunk in re.findall(r'"([a-z_, ]+) FROM us_premap', src):
            out |= {c.strip() for c in chunk.split(",")}
    return {c for c in out if c and " " not in c}


def _inserted_columns():
    m = re.search(r"INSERT INTO us_premap \(([^)]+)\)",
                  inspect.getsource(premap._upsert), re.S)
    assert m, "the upsert's column list is unrecognisable"
    return {c.strip() for c in m.group(1).split(",") if c.strip()}


def _table_columns():
    src = inspect.getsource(premap._ensure_table)
    cols = set(re.findall(r"([a-z_]+)\s+text", src))
    cols |= set(re.findall(r"ADD COLUMN IF NOT EXISTS ([a-z_]+)", src))
    return cols


class TestEveryFieldTheMatcherReadsSurvivesTheRoundTrip:
    """produced -> persisted -> selected -> consumed."""

    def test_the_extraction_still_works(self):
        read = _fields_read_by(premap.match_side)
        assert {"side_norm", "line", "question", "signed"} <= read, read

    def test_every_field_it_reads_is_a_real_column(self):
        missing = _fields_read_by(premap.match_side) - _table_columns()
        assert not missing, (
            f"match_side reads {sorted(missing)}, which us_premap does not "
            f"have — the `signed` defect exactly: green in CI, inert in "
            f"production")

    def test_every_field_it_reads_is_persisted(self):
        missing = _fields_read_by(premap.match_side) - _inserted_columns()
        assert not missing, (
            f"match_side reads {sorted(missing)}, which the upsert never "
            f"writes — the column would exist and always be NULL")

    def test_every_field_it_reads_is_selected_BACK(self):
        missing = _fields_read_by(premap.match_side) - _selected_columns()
        assert not missing, (
            f"match_side reads {sorted(missing)}, which the production "
            f"SELECT does not return — every row carries None for it, and "
            f"a hand-built test row hides that")

    def test_the_row_builder_produces_them(self):
        rows = premap._market_rows(
            {"slug": "asc-nfl-kc-buf-2026-08-25", "title": "KC vs BUF"},
            {"slug": "asc-nfl-kc-buf-2026-08-25", "question": "Spread",
             "marketSides": [
                 {"identifier": "asc-nfl-kc-buf-2026-08-25-kc",
                  "description": "Kansas City Chiefs -3.5", "long": True},
                 {"identifier": "asc-nfl-kc-buf-2026-08-25-buf",
                  "description": "Buffalo Bills +3.5", "long": False}]})
        assert rows
        missing = _fields_read_by(premap.match_side) - set(rows[0])
        assert not missing, (
            f"_market_rows never produces {sorted(missing)}, so the column "
            f"is written NULL on every sweep")

    def test_the_census_reads_the_same_row_as_production(self):
        """resolve is production, resolve_explain is the census. A
        census reading a narrower row attributes production's failures
        to a different question."""
        prod = re.findall(r'"([a-z_, ]+) FROM us_premap',
                          inspect.getsource(premap.resolve))
        cens = re.findall(r'"([a-z_, ]+) FROM us_premap',
                          inspect.getsource(premap.resolve_explain))
        assert prod and cens
        norm = lambda xs: {c.strip() for x in xs for c in x.split(",")}
        assert norm(prod) == norm(cens)


class TestTheGuardItselfCannotRot:
    def test_the_column_readers_find_something(self):
        assert _table_columns(), "table column extraction rotted"
        assert _inserted_columns(), "insert column extraction rotted"
        assert _selected_columns(), "select column extraction rotted"

    def test_a_new_matcher_field_must_be_added_everywhere(self):
        """The invariant, not a list: a new r.get() in match_side fails
        this file until the column, the INSERT, the SELECTs and
        _market_rows all learn about it."""
        read = _fields_read_by(premap.match_side)
        for name, cols in (("table", _table_columns()),
                           ("insert", _inserted_columns()),
                           ("select", _selected_columns())):
            assert read <= cols, (name, sorted(read - cols))
