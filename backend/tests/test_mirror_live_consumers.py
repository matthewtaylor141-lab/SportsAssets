"""Mirror P1, step 6: the per-fill consumers keep the mirror book out.

Position mirroring (owner order 2026-09-02, "go for it, let's get this
working") holds ONE standing live_orders row per book: lane='mirror',
status='filled' from the INSERT, his_price an open-time level, every buy
and sale of the book's life folded onto that row. Settlement, the copy
record, the caps and the referees read it as the copy position it is
(spec 1d, INCLUDE: no change). The instruments in this file must not:
each one scores a FILL against HIS fill, or sells a 'filled' row whole,
or decides a whale's PER-FILL clip -- and a book is none of those. The
panel review's predicate audit (spec 1d, GAIN A CLAUSE) gives each of
them exactly one clause, COALESCE(lane,'') <> 'mirror', so a row placed
before migration 041 (lane NULL: every row that exists today) keeps its
path byte for byte and a book is simply absent.

Two things are pinned per site:

1. the SOURCE: the clause stands in that function's CODE (comment lines
   stripped, so a clause named only in prose cannot pass) and the
   pre-existing predicate text around it is unchanged;
2. the BEHAVIOUR: the consumer is driven against a fixture ledger that
   holds a lane='mirror' row beside a NULL-lane row, and its answer is
   the answer for the same ledger without the mirror row -- the book
   is excluded, the legacy row is counted exactly as before.

Postgres is not in the test tree. The fixture ledger evaluates the one
predicate this file is about the way Postgres would -- COALESCE(lane,'')
<> 'mirror' fails a 'mirror' lane and passes NULL, 'ioc' and 'rest' --
and only when that clause stands in the statement's code. It applies no
other predicate; every fixture row is built to satisfy the rest.
"""

from __future__ import annotations

import asyncio
import inspect
import re

import pytest

from sportsassets import live_executor as le
from sportsassets.analytics import impact as im
from sportsassets.analytics import price_fidelity as pf
from sportsassets.analytics import proof
from sportsassets.api import app as app_mod
from sportsassets.workers import price_path as pp_w
from sportsassets.workers import underdog as ud
from sportsassets.workers import whale_exits as we

# The one clause, in both spellings the sites use (bare on a single-table
# statement, lo.-qualified where the statement aliases live_orders).
_GUARD_RE = re.compile(r"COALESCE\((?:lo\.)?lane,\s*''\)\s*<>\s*'mirror'")
# The spelling that would DROP every NULL-lane row -- must never appear.
_BARE_RE = re.compile(r"(?<![\w.(])(?:lo\.)?lane\s*(?:<>|!=)\s*'mirror'")


def _code(src: str) -> str:
    """Comment lines removed: Python '#' lines and SQL '--' lines."""
    return "\n".join(ln for ln in src.splitlines()
                     if not ln.strip().startswith(("#", "--")))


def _src(fn) -> str:
    return _code(inspect.getsource(fn))


def _pos(text: str, needle: str) -> int:
    assert needle in text, f"not found: {needle!r}"
    return text.index(needle)


def _guarded(sql: str) -> bool:
    return bool(_GUARD_RE.search(_code(sql)))


# ───────────────────────────── the fixture ledger ─────────────────────────────

class _Row(dict):
    """asyncpg.Record's keys() is a method, and one consumer calls it."""

    def keys(self):
        return list(super().keys())


def _select(sql: str, rows: list) -> list:
    """What Postgres would hand back for the lane predicate alone."""
    if not _guarded(sql):
        return list(rows)
    return [r for r in rows if (r.get("lane") or "") != "mirror"]


# Anchored at the percentile expression: the statement carries six other
# FILTERs ahead of this one, and a pattern that starts at the first of
# them reads every aggregate as one predicate.
_SLIP_EXPR = "percentile_cont(0.5) WITHIN GROUP (ORDER BY (fill_price - his_price) * 100)"
_SLIP_RE = re.compile(re.escape(_SLIP_EXPR)
                      + r"\s*FILTER \(WHERE (.*?)\) AS live_slippage_p50", re.S)


def _slippage_p50(sql: str, rows: list):
    """percentile_cont(0.5) of (fill - his) cents over the rows the
    slippage FILTER admits; every atom of that predicate is evaluated,
    and an atom the ledger cannot evaluate fails loudly."""
    m = _SLIP_RE.search(_code(sql))
    assert m, "the live-status slippage FILTER is gone"
    atoms = [a.strip() for a in " ".join(m.group(1).split()).split(" AND ")]

    def holds(r) -> bool:
        for a in atoms:
            if a == "fill_price IS NOT NULL":
                if r.get("fill_price") is None:
                    return False
            elif _GUARD_RE.fullmatch(a):
                if (r.get("lane") or "") == "mirror":
                    return False
            else:
                raise AssertionError(f"the fixture ledger cannot evaluate {a!r}")
        return True

    vals = sorted((r["fill_price"] - r["his_price"]) * 100
                  for r in rows if holds(r))
    if not vals:
        return None
    n, mid = len(vals), len(vals) // 2
    return vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2


class _Ledger:
    def __init__(self, rows, *, fetch_serves=True, lane_missing=False):
        self.rows = [_Row(r) for r in rows]
        self.fetch_serves = fetch_serves
        self.lane_missing = lane_missing
        self.sqls: list[str] = []
        self.claimed: list = []

    async def fetch(self, sql, *a):
        self.sqls.append(sql)
        if self.lane_missing and "lo.lane" in sql:
            raise RuntimeError("column lo.lane does not exist")
        if not self.fetch_serves:
            return []
        return _select(sql, self.rows)

    async def fetchrow(self, sql, *a):
        self.sqls.append(sql)
        assert "live_slippage_p50" in sql, "only the live-status aggregate is served"
        # The clause sits inside ONE aggregate's FILTER, not in the
        # WHERE: every row reaches the statement, and only the slippage
        # median leaves the book out. The other columns count it.
        live = self.rows
        return _Row(orders=len(live),
                    fills=sum(1 for r in live if r.get("status") in ("filled", "settled", "merged")),
                    unfilled=0, unmapped=0, errors=0,
                    deployed=sum(float(r.get("filled_usd") or 0) for r in live),
                    deployed_24h=0.0, realized_pnl=0.0,
                    live_slippage_p50=_slippage_p50(sql, live))

    async def fetchval(self, sql, *a):
        self.sqls.append(sql)
        if "SET status='exiting'" in sql:
            self.claimed.append(a[0])
            return None            # another pass has the row: the sweep must skip
        return None

    async def execute(self, sql, *a):  # pragma: no cover - must not run
        raise AssertionError(f"a consumer tried to write: {sql[:60]}")


def _const(v):
    async def _f():
        return v
    return _f


class TestTheFixtureLedgerHasTeeth:
    """The fake is only worth anything if it honours exactly the clause
    under test, the way Postgres would."""

    ROWS = [_Row(id=1, lane=None), _Row(id=2, lane="ioc"),
            _Row(id=3, lane="rest"), _Row(id=4, lane="mirror")]

    def test_the_clause_drops_a_mirror_lane_and_keeps_every_other(self):
        out = _select("SELECT 1 FROM live_orders WHERE COALESCE(lane,'') <> 'mirror'",
                      self.ROWS)
        assert [r["id"] for r in out] == [1, 2, 3]
        out = _select("SELECT 1 FROM live_orders lo WHERE COALESCE(lo.lane,'') <> 'mirror'",
                      self.ROWS)
        assert [r["id"] for r in out] == [1, 2, 3]

    def test_without_the_clause_the_book_comes_back(self):
        out = _select("SELECT 1 FROM live_orders WHERE status = 'filled'", self.ROWS)
        assert [r["id"] for r in out] == [1, 2, 3, 4]

    def test_a_clause_in_a_comment_line_is_not_a_clause(self):
        sql = ("SELECT 1 FROM live_orders\n"
               " -- AND COALESCE(lane,'') <> 'mirror'\n"
               " WHERE status = 'filled'")
        assert [r["id"] for r in _select(sql, self.ROWS)] == [1, 2, 3, 4]

    def test_the_slippage_evaluator_refuses_an_atom_it_does_not_know(self):
        sql = "x FILTER (WHERE fill_price IS NOT NULL AND status = 'x') AS live_slippage_p50"
        with pytest.raises(AssertionError):
            _slippage_p50(sql, [_Row(fill_price=0.5, his_price=0.5)])


# ───────────────────────────── the sellers ─────────────────────────────

class TestTheCopyExitSweepNeverSellsABook:
    """SA_COPY_EXIT=1 claims 'exiting' and take-profit-sells every
    non-underdog, non-manual 'filled' row -- the whole book, on our own
    +20% rule, while the whale still holds his."""

    def test_the_selection_carries_the_clause_after_its_existing_predicates(self):
        s = _src(ud._copy_exit_sweep)
        a = _pos(s, "\"WHERE status = 'filled' AND us_market_slug IS NOT NULL \"")
        b = _pos(s, "\"AND whale_username NOT IN ('underdog', 'manual')\"")
        c = _pos(s, "AND COALESCE(lane,'') <> 'mirror'")
        assert a < b < c < _pos(s, "SET status='exiting'")
        assert len(_GUARD_RE.findall(s)) == 1

    def test_the_sweep_claims_the_legacy_row_and_never_the_book(self, monkeypatch):
        monkeypatch.setattr(ud, "COPY_EXIT_ENABLED", True)
        monkeypatch.setattr(le, "active_venue", lambda: "polymarket-us")

        async def _bid(cfg, asset):
            return 0.99            # far above the +20% trigger: every row qualifies
        monkeypatch.setattr(ud, "_best_bid", _bid)

        def _row(i, lane):
            return dict(id=i, asset=f"tok{i}", us_market_slug=f"slug-{i}",
                        whale_username="rn1", entry=0.50, qty=200.0,
                        intent="ORDER_INTENT_BUY_LONG", lane=lane)
        with_book = _Ledger([_row(7, None), _row(9, "mirror")])
        legacy_only = _Ledger([_row(7, None)])
        a = asyncio.run(ud._copy_exit_sweep(with_book))
        b = asyncio.run(ud._copy_exit_sweep(legacy_only))
        assert with_book.claimed == [7] == legacy_only.claimed
        assert a == b == {"copyexit_open": 1, "copyexit_cashed": 0}


class TestTheExitWorkerLeavesABooksVanishToTheBook:
    def test_the_ours_read_carries_the_clause_between_its_existing_lines(self):
        s = _src(we._cycle)
        blk = s[_pos(s, "if partial:"):_pos(s, "elif not_an_exit is None:")]
        order = ['"SELECT DISTINCT asset FROM live_orders "',
                 '"WHERE status = \'filled\' "',
                 '"AND COALESCE(lane,\'\') <> \'mirror\' "',
                 '"AND asset = ANY($1::text[])", live_gone)',
                 "if a not in held_here:"]
        positions = [_pos(blk, n) for n in order]
        assert positions == sorted(positions)
        assert len(_GUARD_RE.findall(s)) == 1

    def test_the_read_is_still_the_only_gate_on_the_confirm_read(self):
        """The mirror's flatten trigger is the same raw snapshot this
        cycle writes; that write is untouched."""
        s = _src(we._cycle)
        assert "to_save = dict(prev) if partial else {}" in s
        assert "_confirm_gone(" in s


# ───────────────────────────── the proof cohort ─────────────────────────────

def _proof_rows(with_book: bool):
    rows = [dict(whale_username="rn1", stake=100.0, pnl=10.0,
                 event_key="g1", lane=None)]
    if with_book:
        rows.append(dict(whale_username="rn1", stake=120.0, pnl=3.0,
                         event_key="g-book", lane="mirror"))
    return rows


class TestTheProofCohortExcludesTheBook:
    """roster_auto decides rn1's PER-FILL clip on this cohort."""

    def test_its_own_line_directly_after_the_pinned_status_line(self):
        lines = [ln.strip() for ln in _src(proof.cohort_assess).splitlines()
                 if ln.strip()]
        i = lines.index("AND lo.status IN ('settled', 'cashed_out')")
        assert lines[i + 1] == "AND COALESCE(lo.lane,'') <> 'mirror'"
        assert lines[i + 2] == \
            "AND COALESCE(lo.whale_username, '') NOT IN ('manual', 'underdog')"
        assert len(_GUARD_RE.findall(_src(proof.cohort_assess))) == 1

    def test_the_book_is_absent_and_the_legacy_row_counts_as_before(self):
        a = asyncio.run(proof.cohort_assess(_Ledger(_proof_rows(True)),
                                            "2026-08-26T00:00:00+00:00"))
        b = asyncio.run(proof.cohort_assess(_Ledger(_proof_rows(False)),
                                            "2026-08-26T00:00:00+00:00"))
        assert a == b
        assert a["overall"]["n"] == 1 and a["by_whale"]["rn1"]["n"] == 1

    def test_the_ledger_would_have_served_the_book_without_the_clause(self):
        pool = _Ledger(_proof_rows(True))
        asyncio.run(proof.cohort_assess(pool, "2026-08-26T00:00:00+00:00"))
        sql = pool.sqls[0]
        assert _guarded(sql)
        assert len(_select(_GUARD_RE.sub("TRUE", sql), pool.rows)) == 2


# ───────────────────────────── the impact ladder ─────────────────────────────

def _impact_rows(with_book: bool):
    rows = [dict(whale="rn1", his_price=0.50, fill_price=0.51, stake=100.0,
                 pnl=8.0, lane=None, intent="ORDER_INTENT_BUY_LONG", event_key="g1")]
    if with_book:
        rows.append(dict(whale="rn1", his_price=0.50, fill_price=0.505, stake=250.0,
                         pnl=40.0, lane="mirror", intent="ORDER_INTENT_BUY_LONG",
                         event_key="g-book"))
    return rows


class TestTheImpactLadderExcludesTheBook:
    def test_the_clause_is_conditional_on_the_lane_column(self):
        s = _src(im.cohort_impact)
        i = _pos(s, "COALESCE(lo.lane,'') <> 'mirror'")
        assert 'if with_lane else ""' in s[i:i + 120]
        for kept in ("lo.status IN ('settled', 'cashed_out')", "lo.pnl IS NOT NULL",
                     "'manual', 'underdog'", "COALESCE(lo.filled_usd, lo.requested_usd) > 0"):
            assert kept in s, kept

    def test_the_book_is_absent_and_the_legacy_row_counts_as_before(self):
        a = asyncio.run(im.cohort_impact(_Ledger(_impact_rows(True)),
                                         "2026-08-26T00:00:00+00:00"))
        b = asyncio.run(im.cohort_impact(_Ledger(_impact_rows(False)),
                                         "2026-08-26T00:00:00+00:00"))
        assert a == b
        assert a["n_settled"] == 1
        assert sum(x["n"] for x in a["buckets"].values()) == 1

    def test_the_served_query_is_guarded_and_the_retry_is_not_broken(self):
        pool = _Ledger(_impact_rows(True))
        asyncio.run(im.cohort_impact(pool, "2026-08-26T00:00:00+00:00"))
        assert _guarded(pool.sqls[0]) and "lo.lane AS lane" in pool.sqls[0]
        bare = _Ledger(_impact_rows(True), lane_missing=True)
        out = asyncio.run(im.cohort_impact(bare, "2026-08-26T00:00:00+00:00"))
        assert len(bare.sqls) == 2
        assert "lo.lane" not in bare.sqls[1] and not _guarded(bare.sqls[1])
        assert out["n_settled"] == 2      # no column, no book: nothing to exclude


# ───────────────────────────── price fidelity ─────────────────────────────

def _fidelity_rows(with_book: bool):
    rows = [dict(whale_username="rn1", his_price=0.50, fill_price=0.49,
                 filled_shares=100.0, intent="ORDER_INTENT_BUY_LONG", lane=None)]
    if with_book:
        rows.append(dict(whale_username="rn1", his_price=0.50, fill_price=0.55,
                         filled_shares=400.0, intent="ORDER_INTENT_BUY_LONG",
                         lane="mirror"))
    return rows


class TestPriceFidelityExcludesTheBook:
    def test_the_clause_follows_the_three_pinned_predicates(self):
        s = _src(pf.cohort_fidelity)
        order = ["AND fill_price IS NOT NULL",
                 "AND COALESCE(filled_shares, 0) > 0",
                 "AND COALESCE(whale_username, '') NOT IN ('manual', 'underdog')",
                 "AND COALESCE(lane,'') <> 'mirror'"]
        positions = [_pos(s, n) for n in order]
        assert positions == sorted(positions)
        assert len(_GUARD_RE.findall(s)) == 1

    def test_the_book_is_absent_and_the_legacy_row_counts_as_before(self, monkeypatch):
        monkeypatch.setattr(le, "short_model_confirmed", lambda: True)
        a = asyncio.run(pf.cohort_fidelity(_Ledger(_fidelity_rows(True)),
                                           "2026-08-26T00:00:00+00:00"))
        b = asyncio.run(pf.cohort_fidelity(_Ledger(_fidelity_rows(False)),
                                           "2026-08-26T00:00:00+00:00"))
        assert a == b
        assert a["overall"]["n"] == 1 and a["overall"]["at_or_better"] == 1


# ───────────────────────────── the price-path sampler ─────────────────────────────

def _path_rows(with_book: bool):
    rows = [dict(row_id=7, slug="aec-x-y-2026-09-01", placed_ts=1000.0,
                 intent="ORDER_INTENT_BUY_LONG", taken=[], lane=None)]
    if with_book:
        rows.append(dict(row_id=9, slug="aec-book-2026-09-01", placed_ts=1000.0,
                         intent="ORDER_INTENT_BUY_LONG", taken=[], lane="mirror"))
    return rows


class TestTheSamplerNeverThreadsABook:
    def test_the_clause_sits_inside_the_where_after_the_intent_filter(self):
        s = _src(pp_w._due_samples)
        order = ["ANY($2::text[])", "IN ('ORDER_INTENT_BUY_LONG',",
                 "AND COALESCE(lo.lane,'') <> 'mirror'", "GROUP BY lo.id"]
        positions = [_pos(s, n) for n in order]
        assert positions == sorted(positions)
        assert len(_GUARD_RE.findall(s)) == 1

    def test_the_book_is_absent_and_the_legacy_row_is_due_as_before(self, monkeypatch):
        monkeypatch.setattr(pp_w, "exitable_whales", lambda: {"rn1"})
        a = asyncio.run(pp_w._due_samples(_Ledger(_path_rows(True)), 1000.0 + 30))
        b = asyncio.run(pp_w._due_samples(_Ledger(_path_rows(False)), 1000.0 + 30))
        assert a == b
        assert [(d["row_id"], d["t_s"]) for d in a] == [(7, 30)]


# ───────────────────────────── the API ─────────────────────────────

def _fvm_rows(with_book: bool):
    rows = [dict(whale="rn1", status="settled", his_price=0.50, req_usd=100.0,
                 filled_usd=100.0, pnl=12.0, outcome_index=0,
                 resolved_prices=[1, 0], lane=None),
            dict(whale="rn1", status="unfilled", his_price=0.40, req_usd=100.0,
                 filled_usd=None, pnl=None, outcome_index=0,
                 resolved_prices=[1, 0], lane=None)]
    if with_book:
        rows.append(dict(whale="rn1", status="filled", his_price=0.50, req_usd=0.0,
                         filled_usd=250.0, pnl=None, outcome_index=0,
                         resolved_prices=None, lane="mirror"))
    return rows


class TestFillVsMissExcludesTheBook:
    def test_the_clause_follows_the_status_list(self):
        s = _src(app_mod.api_fill_vs_miss)
        order = ["NOT IN ('manual', 'underdog')",
                 "lo.status IN ('filled', 'settled', 'unfilled',",
                 "'cashed_out')",
                 "AND COALESCE(lo.lane,'') <> 'mirror'"]
        positions = [_pos(s, n) for n in order]
        assert positions == sorted(positions)
        assert len(_GUARD_RE.findall(s)) == 1

    def test_the_book_is_absent_and_the_legacy_rows_grade_as_before(self, monkeypatch):
        with_book, legacy = _Ledger(_fvm_rows(True)), _Ledger(_fvm_rows(False))
        monkeypatch.setattr(app_mod, "get_pool", _const(with_book))
        a = asyncio.run(app_mod.api_fill_vs_miss(days=7))
        monkeypatch.setattr(app_mod, "get_pool", _const(legacy))
        b = asyncio.run(app_mod.api_fill_vs_miss(days=7))
        assert a == b
        g = a["whales"]["rn1"]
        assert g["filled_n"] == 1 and g["filled_open"] == 0
        assert g["filled_staked_all"] == 100.0 and g["missed_n"] == 1


def _status_rows(with_book: bool):
    rows = [dict(status="settled", his_price=0.50, fill_price=0.51,
                 filled_usd=100.0, lane=None),
            dict(status="filled", his_price=0.40, fill_price=0.43,
                 filled_usd=100.0, lane="ioc")]
    if with_book:
        # a book's fill_price is a lifetime average against an open-time
        # his_price: 20 cents of "slippage" that never happened
        rows.append(dict(status="filled", his_price=0.30, fill_price=0.50,
                         filled_usd=250.0, lane="mirror"))
    return rows


class TestTheLiveStatusSlippageExcludesTheBook:
    def test_the_filter_gains_the_clause_and_keeps_its_expression(self):
        s = _src(app_mod._live_status_uncached)
        m = _SLIP_RE.search(s)
        assert m and " ".join(m.group(1).split()) == \
            "fill_price IS NOT NULL AND COALESCE(lane,'') <> 'mirror'"
        # the other aggregates of that statement are untouched
        assert len(_GUARD_RE.findall(s)) == 1
        assert "count(*) FILTER (WHERE status IN ('filled', 'settled', 'merged'))::int AS fills" in s

    def test_the_median_is_over_per_fill_rows_only(self, monkeypatch):
        with_book = _Ledger(_status_rows(True), fetch_serves=False)
        legacy = _Ledger(_status_rows(False), fetch_serves=False)
        monkeypatch.setattr(app_mod, "get_pool", _const(with_book))
        a = asyncio.run(app_mod._live_status_uncached())
        monkeypatch.setattr(app_mod, "get_pool", _const(legacy))
        b = asyncio.run(app_mod._live_status_uncached())
        assert a["summary"]["live_slippage_p50"] == b["summary"]["live_slippage_p50"]
        assert a["summary"]["live_slippage_p50"] == pytest.approx(2.0, abs=1e-6)
        # and the ledger WOULD have folded the book in without the clause
        sql = next(q for q in with_book.sqls if "live_slippage_p50" in q)
        assert _slippage_p50(_GUARD_RE.sub("fill_price IS NOT NULL", sql),
                             with_book.rows) == pytest.approx(3.0, abs=1e-6)


def _tol_rows(with_book: bool):
    rows = [dict(whale="rn1", his=0.48, fp=0.48, staked=100.0, pnl=5.0,
                 status="settled", intent="ORDER_INTENT_BUY_LONG",
                 lane=None, event_key="g1")]
    if with_book:
        rows.append(dict(whale="rn1", his=0.48, fp=0.48, staked=250.0, pnl=30.0,
                         status="settled", intent="ORDER_INTENT_BUY_LONG",
                         lane="mirror", event_key="g-book"))
    return rows


class TestTheToleranceGraderFilesTheBookAsItsOwnCohort:
    """Not excluded -- named. A book at his exact open-time level would
    otherwise read as parity, the cohort Option A is judged against."""

    def test_the_cohort_expression(self):
        s = _src(app_mod.api_copy_tolerance)
        i = _pos(s, 'cohort = ("mirror" if _lane == "mirror"')
        tail = s[i:i + 200]
        assert 'else "rest" if _lane == "rest"' in tail
        assert 'else tolerance_cohort(r["his"], r["fp"], r["intent"])' in tail

    def test_the_row_fetch_itself_is_not_guarded(self):
        """The book reaches the grader and is filed, not dropped."""
        pool = _Ledger(_tol_rows(True))
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(app_mod, "get_pool", _const(pool))
        try:
            asyncio.run(app_mod.api_copy_tolerance())
        finally:
            monkeypatch.undo()
        assert not _guarded(pool.sqls[0])

    def test_the_book_is_its_own_cohort_and_parity_reads_as_before(self, monkeypatch):
        monkeypatch.setattr(app_mod, "get_pool", _const(_Ledger(_tol_rows(True))))
        a = asyncio.run(app_mod.api_copy_tolerance())
        monkeypatch.setattr(app_mod, "get_pool", _const(_Ledger(_tol_rows(False))))
        b = asyncio.run(app_mod.api_copy_tolerance())
        ca = {d["cohort"]: d for d in a["rows"]}
        cb = {d["cohort"]: d for d in b["rows"]}
        assert set(ca) == {"parity", "mirror"} and set(cb) == {"parity"}
        assert ca["parity"] == cb["parity"]
        assert ca["mirror"]["n"] == 1 and ca["mirror"]["staked"] == 250.0
        assert a["by_whale"] == b["by_whale"]


# ───────────────────────────── the spelling ─────────────────────────────

class TestEveryGuardKeepsTheNullLaneOnTodaysPath:
    """`lane <> 'mirror'` is NULL for every row placed before 041 and a
    NULL predicate drops the row: the one spelling that would silently
    empty every instrument of its history."""

    @pytest.mark.parametrize("fn", [
        ud._copy_exit_sweep, we._cycle, proof.cohort_assess, im.cohort_impact,
        pf.cohort_fidelity, pp_w._due_samples, app_mod.api_fill_vs_miss,
        app_mod._live_status_uncached, app_mod.api_copy_tolerance,
    ])
    def test_no_bare_lane_comparison(self, fn):
        assert not _BARE_RE.search(_src(fn)), fn.__name__

    def test_no_bare_lane_comparison_anywhere_in_the_owned_modules(self):
        import sportsassets.analytics.impact
        import sportsassets.analytics.price_fidelity
        import sportsassets.analytics.proof
        import sportsassets.workers.price_path
        import sportsassets.workers.underdog
        import sportsassets.workers.whale_exits

        for mod in (sportsassets.workers.underdog, sportsassets.workers.whale_exits,
                    sportsassets.analytics.proof, sportsassets.analytics.impact,
                    sportsassets.analytics.price_fidelity,
                    sportsassets.workers.price_path, app_mod):
            assert not _BARE_RE.search(_code(inspect.getsource(mod))), mod.__name__
