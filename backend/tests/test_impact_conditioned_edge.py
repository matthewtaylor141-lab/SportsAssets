"""Edge conditioned on impact is the fill rule, if any bucket earns.

Proof at 95% needs n ~ (1.645*sigma/mu)^2 settled copies -- a function of
the achieved edge SQUARED, not of volume. His edge minus the impact of
capturing it rounds to zero on the whole book; the question is whether
the LOW-impact slice carries his edge. That is answerable on the ledger
we already have. These tests pin what makes the answer trustworthy:

  * the buckets partition every measurable copy exactly once
  * impact is in OUR cost space, leg to leg, positive = we paid more
  * every bucket carries a cluster-robust interval and a verdict
  * the proof horizon is reported only for a positive estimate
  * the reading names the cheapest bucket that earns, or says none does
  * the population is the proof cohort's: terminal rows only
"""
import asyncio

import pytest

from sportsassets.analytics import impact as im


def _r(his=0.50, fp=0.50, stake=100.0, pnl=0.0, intent="ORDER_INTENT_BUY_LONG",
       event_key=None):
    return {"his_price": his, "fill_price": fp, "stake": stake, "pnl": pnl,
            "intent": intent, "event_key": event_key}


# --------------------------------------------------------- the impact

def test_positive_impact_means_we_paid_more_than_him():
    assert im.impact_of(_r(his=0.50, fp=0.52)) == pytest.approx(0.02)
    assert im.impact_of(_r(his=0.50, fp=0.48)) == pytest.approx(-0.02)


def test_a_short_is_compared_leg_to_leg_not_across_the_complement():
    """cost_per_share converts a short's contract price to our cost on
    HIS leg. Comparing fill_price to his_price raw on a BUY_SHORT would
    report a ~0.5 'impact' on every short."""
    from sportsassets.live_executor import cost_per_share

    r = _r(his=0.30, fp=0.71, intent="ORDER_INTENT_BUY_SHORT")
    assert im.impact_of(r) == pytest.approx(
        cost_per_share(0.71, "ORDER_INTENT_BUY_SHORT") - 0.30)


def test_unknowable_prices_are_none_not_zero():
    """A pile of zeros drags every bucket toward 'we matched him'."""
    assert im.impact_of(_r(his=None, fp=0.5)) is None
    assert im.impact_of(_r(his=0.5, fp=0.0)) is None
    assert im.impact_of(_r(his=1.0, fp=0.5)) is None


# ------------------------------------------------------ the partition

def test_every_measurable_row_lands_in_exactly_one_bucket():
    fps = [0.40, 0.49, 0.495, 0.50, 0.505, 0.51, 0.515, 0.52, 0.525,
           0.53, 0.54, 0.60]
    rows = [_r(his=0.50, fp=fp, event_key=f"g{i}") for i, fp in enumerate(fps)]
    out = im.impact_buckets(rows)
    assert sum(b["n"] for b in out["buckets"].values()) == len(rows)
    assert out["unmeasurable"] == 0


def test_the_bucket_edges_are_one_tick_wide_and_half_open():
    """Exactly +1c belongs to (0,1c], not to (1c,2c]."""
    out = im.impact_buckets([_r(his=0.50, fp=0.51)])
    assert out["buckets"]["(0,1c]"]["n"] == 1
    assert out["buckets"]["(1c,2c]"]["n"] == 0


def test_exactly_his_price_is_the_zero_bucket_not_the_credit():
    out = im.impact_buckets([_r(his=0.50, fp=0.50)])
    assert out["buckets"]["(-1c,0]"]["n"] == 1
    assert out["buckets"]["<=-1c (cheaper)"]["n"] == 0


def test_unmeasurable_rows_are_counted_not_bucketed():
    out = im.impact_buckets([_r(his=None, fp=0.5), _r(his=0.5, fp=0.5)])
    assert out["unmeasurable"] == 1
    assert sum(b["n"] for b in out["buckets"].values()) == 1


# ---------------------------------------------------- the interval

def test_each_bucket_carries_an_interval_and_a_verdict():
    rows = [_r(his=0.5, fp=0.5, pnl=p, event_key=f"g{i}")
            for i, p in enumerate([20.0, -10.0, 15.0, -5.0, 25.0, 5.0])]
    b = im.impact_buckets(rows)["buckets"]["(-1c,0]"]
    assert b["ci95"] is not None
    # six games: the verdict is there, marked provisional
    assert b["verdict"].startswith("PROVISIONAL (games<30) — ")
    assert b["verdict"].split(" — ", 1)[1] in (
        "EARNS at 95%", "LOSES at 95%", "NOT DEMONSTRATED — contains zero")


def test_copies_on_one_event_cluster():
    rows = [_r(his=0.5, fp=0.5, pnl=5.0, event_key="one-match") for _ in range(3)]
    assert im.impact_buckets(rows)["buckets"]["(-1c,0]"]["clusters"] == 1


def test_a_single_copy_bucket_has_no_interval():
    b = im.impact_buckets([_r(his=0.5, fp=0.5, pnl=5.0)])["buckets"]["(-1c,0]"]
    assert b["ci95"] is None
    assert "NO INTERVAL" in b["verdict"]


# --------------------------------------------------- the proof horizon

def _forty(pnls):
    """Forty copies on forty games: above the MIN_PROOF_CLUSTERS floor."""
    return [_r(his=0.5, fp=0.5, stake=100.0, pnl=pnls[i % len(pnls)],
               event_key=f"g{i}") for i in range(40)]


def test_a_positive_estimate_gets_a_sample_size_and_a_horizon():
    rows = _forty([30.0, -20.0, 25.0, -15.0, 20.0, 10.0])
    b = im.impact_buckets(rows, flow_per_day=10.0)["buckets"]["(-1c,0]"]
    assert b["roi"] > 0
    assert b["n_needed_at_observed"] is not None
    assert b["n_still_needed"] == max(0, b["n_needed_at_observed"] - b["n"])
    assert b["days_to_proof_at_flow"] == pytest.approx(b["n_still_needed"] / 10.0, abs=0.1)


def test_a_non_positive_estimate_gets_no_horizon():
    """No sample size demonstrates profit from a negative estimate."""
    rows = _forty([-30.0, 20.0, -25.0, 15.0, -20.0, -10.0])
    b = im.impact_buckets(rows, flow_per_day=10.0)["buckets"]["(-1c,0]"]
    assert b["n_needed_at_observed"] is None
    assert b["days_to_proof_at_flow"] is None


def test_no_flow_means_no_horizon_even_for_a_positive_estimate():
    rows = _forty([30.0, -20.0, 25.0, -15.0, 20.0, 10.0])
    b = im.impact_buckets(rows)["buckets"]["(-1c,0]"]
    assert b["days_to_proof_at_flow"] is None


def test_six_copies_are_provisional_and_name_no_fill_rule():
    """ROUND THREE, reproduced: a 6-copy bucket at +30% read 'EARNS at
    95%', n_still_needed=0, days=0.0, and the reading named a fill rule.
    Sigma from six games carries ~32% relative error and required_n
    squares it; the 'interval' is not 95% either (t_5 = 2.57 vs z 1.96).
    Below the floor: PROVISIONAL, no horizon, no rule."""
    from sportsassets.analytics.proof import MIN_PROOF_CLUSTERS

    rows = [_r(his=0.5, fp=0.5, stake=100.0, pnl=30.0, event_key=f"g{i}")
            for i in range(6)]
    out = im.impact_buckets(rows, flow_per_day=50.0)
    b = out["buckets"]["(-1c,0]"]
    assert b["verdict"].startswith("PROVISIONAL")
    assert b["n_needed_at_observed"] is None
    assert b["n_still_needed"] is None and b["days_to_proof_at_flow"] is None
    assert "no bucket earns" in out["reading"]
    assert MIN_PROOF_CLUSTERS == 30 and out["min_proof_clusters"] == 30


# --------------------------------------------------------- the reading

def test_the_reading_names_an_earning_bucket():
    rows = _forty([20.0])
    out = im.impact_buckets(rows)
    assert "(-1c,0]" in out["reading"]
    assert "Cap impact" in out["reading"]


def test_a_rest_fill_is_its_own_population_and_never_sets_the_cap():
    """A rest fill lands at his exact price by construction (impact <= 0),
    so pooled into the ladder it sits in exactly the buckets whose EARNS
    verdict sets the IOC impact cap. Reported beside the ladder, never
    inside it, and never named by the reading."""
    rows = [dict(_r(his=0.5, fp=0.5, stake=100.0, pnl=20.0, event_key=f"g{i}"),
                 lane="rest") for i in range(40)]
    out = im.impact_buckets(rows)
    assert out["buckets"][im.REST_BUCKET]["n"] == 40
    assert out["buckets"]["(-1c,0]"]["n"] == 0
    assert out["buckets"][im.REST_BUCKET]["verdict"] == "EARNS at 95%"
    assert "Cap impact" not in out["reading"]
    assert im.REST_BUCKET not in out["reading"]
    assert sum(b["n"] for b in out["buckets"].values()) == 40


def test_the_reading_says_none_earns_when_none_does():
    rows = [_r(his=0.5, fp=0.5, stake=100.0, pnl=p, event_key=f"g{i}")
            for i, p in enumerate([50.0, -50.0, 40.0, -45.0])]
    out = im.impact_buckets(rows)
    assert "no bucket earns at 95%" in out["reading"]


# ------------------------------------------------------- the population

class _Pool:
    def __init__(self, lane_missing=False):
        self.sql = None
        self.args = None
        self.sqls: list[str] = []
        self.lane_missing = lane_missing

    async def fetch(self, sql, *a):
        self.sqls.append(sql)
        self.sql, self.args = sql, a
        if self.lane_missing and "lo.lane AS lane" in sql:
            raise RuntimeError('column lo.lane does not exist')
        return []


def test_the_cohort_is_terminal_rows_only_and_whale_is_bound():
    pool = _Pool()
    asyncio.run(im.cohort_impact(pool, "2026-08-25T14:00:00+00:00", whale="rn1"))
    assert "'settled', 'cashed_out'" in pool.sql
    assert "lo.pnl IS NOT NULL" in pool.sql
    assert "'manual', 'underdog'" in pool.sql
    assert "$2" in pool.sql and pool.args[1] == "rn1"
    assert "rn1" not in pool.sql
    assert "lo.lane AS lane" in pool.sql


def test_a_database_without_the_lane_column_still_answers():
    """Migration 041 may not have run where this is read."""
    pool = _Pool(lane_missing=True)
    out = asyncio.run(im.cohort_impact(pool, "2026-08-25T14:00:00+00:00", whale="rn1"))
    assert len(pool.sqls) == 2 and "NULL::text AS lane" in pool.sqls[1]
    assert out["n_settled"] == 0
