"""The held-whale statement, evaluated with ITS OWN precedence.

test_refused_exit_is_held.FakePool._row_is_held reads the statement
clause by clause and then combines the clauses in a fixed shape,
(status-list OR named-error) AND lane. That catches a dropped clause,
but not a change in how the clauses COMBINE: with the outer
parentheses gone, Postgres binds the lane clause to the error branch
alone and a 'filled' row on the mirror lane holds; an extra `lane IS
NULL` inside the named branch unholds a named row on a real lane; a
re-spelled interval reads as no horizon at all. Only the verbatim text
pin caught those in the U8 re-review (2026-09-05), and a pin is the
guard that gets retyped to match whatever the next editor writes.

This fake compiles the WHERE text into a Python boolean expression
token for token, parentheses kept where they stand, so precedence is
the statement's own (AND binds before OR, as in Python). Anything it
cannot translate raises: the fake never guesses.
"""
from __future__ import annotations

import json
import re

import pytest

from sportsassets.workers import whale_exits as we

from tests.test_refused_exit_is_held import FakePool
from tests.test_whale_exits_held_only import KEY, POSITION_ERROR

_UNIT_S = {"second": 1, "seconds": 1, "minute": 60, "minutes": 60,
           "hour": 3600, "hours": 3600, "day": 86400, "days": 86400}


def compile_where(sql: str) -> str:
    """The WHERE clause of `sql` as a Python expression over the names
    status, error, lane, age_s."""
    where = sql.split("WHERE", 1)[1]
    rules = [
        (r"status IN \(([^)]*)\)", lambda m: f"(status in ({m.group(1)},))"),
        (r"status = '([^']*)'", lambda m: f"(status == '{m.group(1)}')"),
        (r"error LIKE '([^%']*)%'", lambda m: f"((error or '').startswith('{m.group(1)}'))"),
        (r"placed_at > now\(\) - interval '(\d+) (\w+)'",
         lambda m: f"(age_s < {int(m.group(1)) * _UNIT_S[m.group(2)]})"),
        (r"COALESCE\(lane,''\) <> 'mirror'", lambda m: "((lane or '') != 'mirror')"),
        (r"lane IS NULL", lambda m: "(lane is None)"),
        (r"\bAND\b", lambda m: " and "),
        (r"\bOR\b", lambda m: " or "),
    ]
    out, pos = [], 0
    while pos < len(where):
        ch = where[pos]
        if ch in " \n\t":
            pos += 1
            continue
        if ch in "()":
            out.append(ch)
            pos += 1
            continue
        for pat, fn in rules:
            m = re.compile(pat).match(where, pos)
            if m:
                out.append(fn(m))
                pos = m.end()
                break
        else:
            raise AssertionError(f"untranslatable SQL at: {where[pos:pos + 40]!r}")
    return " ".join(out)


class PrecedencePool(FakePool):
    @staticmethod
    def _row_is_held(r: dict, sql: str) -> bool:
        expr = compile_where(sql)
        return bool(eval(expr, {"__builtins__": {}}, {  # noqa: S307 -- test-local, no builtins
            "status": r.get("status"), "error": r.get("error"),
            "lane": r.get("lane"), "age_s": float(r.get("age_s", 0))}))


class _Resp:
    def __init__(self, rows):
        self.status_code = 200
        self._rows = rows

    def raise_for_status(self):
        pass

    def json(self):
        return self._rows


class _Venue:
    def __init__(self):
        self.books = {}
        self.calls = []

    async def get(self, path, params=None):
        p = dict(params or {})
        self.calls.append((path, p))
        items = sorted(self.books.get(p.get("user"), {}).items())
        off, lim = int(p.get("offset", 0)), int(p.get("limit", 0))
        return _Resp([{"asset": a, "size": s} for a, s in items[off:off + lim]])

    def walks(self, address):
        return sum(1 for path, p in self.calls
                   if path == "/positions" and p.get("user") == address)


@pytest.fixture
def venue(monkeypatch):
    from sportsassets import live_executor as le

    async def _copy(payload):
        return "mx_SOLD"

    monkeypatch.setattr(le, "execute_copy", _copy)
    monkeypatch.setattr(we, "POSITIONS_PAGE", 2)
    monkeypatch.setattr("sportsassets.api.copies_record.COPY_WHALES",
                        {"rn1"}, raising=False)
    return _Venue()


def test_the_compiled_predicate_reads_the_statement():
    assert " ".join(compile_where(we._HELD_SQL).split()) == (
        "( (status in ('filled', 'submitting', 'exiting',)) or "
        "( (status == 'error') and ( ((error or '').startswith('venue holds a POSITION')) "
        "or ((error or '').startswith('venue has no record of order')) ) and "
        "(age_s < 172800) ) ) and ((lane or '') != 'mirror')")


def test_an_untranslatable_clause_raises_rather_than_guesses():
    with pytest.raises(AssertionError, match="untranslatable"):
        compile_where("SELECT 1 FROM t WHERE status IN ('filled') AND whale_username = 'x'")


@pytest.mark.parametrize("rows, held", [
    # with the outer parentheses gone the lane clause would bind only to
    # the error branch, and a 'filled' row on the mirror lane would hold
    ([{"whale_username": "rn1", "status": "filled", "lane": "mirror"}], False),
    # a named row on a real non-mirror lane is ours, held
    ([{"whale_username": "rn1", "status": "error", "lane": "ioc",
       "error": POSITION_ERROR}], True),
    # a named row well inside 48 hours but far past 30 minutes
    ([{"whale_username": "rn1", "status": "error", "lane": None,
       "error": POSITION_ERROR, "age_s": 47 * 3600}], True),
    # controls carried over from the behaviour table
    ([{"whale_username": "rn1", "status": "error", "lane": None,
       "error": POSITION_ERROR, "age_s": 48 * 3600 + 1}], False),
    ([{"whale_username": "rn1", "status": "error", "lane": None,
       "error": "rejected: insufficient balance"}], False),
    ([{"whale_username": "rn1", "status": "submitting", "lane": None}], True),
])
@pytest.mark.asyncio
async def test_which_rows_hold_him_by_precedence(venue, rows, held):
    pool = PrecedencePool(live_rows=rows)
    pool.state[KEY] = json.dumps({"tokA": 1.0})
    venue.books["0xrn1"] = {"tokA": 1.0}
    stats = await we._cycle(venue, pool)
    assert (venue.walks("0xrn1") == 1) is held
    assert stats["unheld_skipped"] == (0 if held else 1)
    assert (KEY in pool.state) is held
