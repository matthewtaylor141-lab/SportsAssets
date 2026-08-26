"""The proof cohort counted still-open positions as settled.

cohort_assess selected on `lo.pnl IS NOT NULL` alone. That was a fair
proxy for "closed" until partial exits began accumulating P&L onto a row
that stays status='filled' -- mirror_exit's remainder branch writes
`status='filled', pnl=COALESCE(pnl,0)+$3` precisely so the residual
shares are not orphaned.

Such a row still HOLDS SHARES. It was entering the cohort at FULL stake
(COALESCE(filled_usd, requested_usd)) carrying only the realised part of
its P&L -- gain counted, remaining exposure not. The bias runs in the
optimistic direction, and this is the number reported as evidence of
whether the desk earns.
"""

from __future__ import annotations

import inspect

from sportsassets.analytics import proof


class TestOnlyClosedRowsCount:
    def test_the_query_filters_on_status(self):
        src = inspect.getsource(proof.cohort_assess)
        assert "lo.status IN ('settled', 'cashed_out')" in src

    def test_a_still_filled_row_is_excluded(self):
        """'filled' with a non-null pnl is a PARTIALLY exited position,
        not a closed one."""
        src = inspect.getsource(proof.cohort_assess)
        i = src.index("lo.status IN")
        stmt = src[i:src.index("\n", i)]
        assert "'filled'" not in stmt

    def test_pnl_not_null_alone_is_no_longer_the_test(self):
        src = inspect.getsource(proof.cohort_assess)
        assert "lo.pnl IS NOT NULL" in src, "still required, just not sufficient"
        assert src.index("lo.pnl IS NOT NULL") < src.index("lo.status IN")

    def test_cashed_out_is_included(self):
        """mirror_exit's full exit is genuinely terminal and its P&L is
        real -- excluding it would bias the other way."""
        src = inspect.getsource(proof.cohort_assess)
        assert "'cashed_out'" in src

    def test_the_reason_travels_with_the_filter(self):
        src = inspect.getsource(proof.cohort_assess)
        assert "still HOLDS SHARES" in src


class TestTheEstimatorItselfIsUnchanged:
    def test_a_clean_cohort_still_scores_the_same(self):
        rows = [{"stake": 100.0, "pnl": 10.0},
                {"stake": 100.0, "pnl": -5.0},
                {"stake": 100.0, "pnl": 20.0}]
        out = proof.assess(rows)
        assert out["roi"] is not None
        assert out["n"] == 3

    def test_an_empty_cohort_reports_NO_SAMPLE_not_zero(self):
        """Not "0% return" -- nothing settled is not a measurement."""
        out = proof.assess([])
        assert out["n"] == 0
        assert out["roi"] is None and out["ci95"] is None
        assert "NO SAMPLE" in out["verdict"]
