"""THE REFRESH SIMULATION ON PRODUCTION'S OWN DISTRIBUTIONS (RC6 lane D1;
tools/freshness_refresh_sim.py).

  §1  its inputs are the research run's numbers, not tuned ones
  §2  no policy ever reads more than the venue's 12 GetOrderBook a minute
  §3  what it shows (seeded, the RC5 20:07Z denominator of 186): RC6 as
      merged (one read per minutes-long pass) is the no-refresh rate; the
      decoupled REST refresh lifts it but cannot hold 0.95; the fair order
      starves no member; one snapshot-only call a minute holds >= 0.97
"""
from __future__ import annotations

import importlib

import pytest


def S():
    return importlib.import_module("tools.freshness_refresh_sim")


def test_the_inputs_are_research_run_37870039455s_numbers():
    sim = S()
    gaps = sum(b[2] for b in sim.GAP_BUCKETS)
    assert gaps == 12352                       # C: 12,352 gaps, 182 symbols
    over = [b for b in sim.GAP_BUCKETS if b[0] >= 300.0]
    assert sum(b[2] for b in over) == 1641     # C: gaps over 300 s
    assert sum(b[3] - 300.0 * b[2] for b in over) == 438358
    assert sim.SCENARIOS == {"now_01_29Z": 86, "mean_6h": 134,
                             "rc5_20_07Z": 186, "max_24h": 306}
    assert (sim.BOUND, sim.PER_MIN, sim.MIN_GAP) == (300.0, 12, 1.0)


@pytest.mark.parametrize("policy", ["RC6_INLINE", "DECOUPLED_START",
                                    "DECOUPLED_FAIR", "FAIR_PLUS_SNAPSHOT"])
def test_no_policy_reads_more_than_the_venues_figure(policy):
    r = S().simulate(186, policy, hours=2.0, seed=3)
    assert r["rest_reads_per_min"] <= 12.0
    assert r["snapshot_calls_per_min"] <= 1.0


def test_what_the_simulation_shows_at_the_rc5_denominator():
    sim = S()
    got = {p: sim.simulate(186, p, hours=3.0, seed=7) for p in sim.POLICIES}
    none, inline = got["NONE"], got["RC6_INLINE"]
    start, fair = got["DECOUPLED_START"], got["DECOUPLED_FAIR"]
    snap = got["FAIR_PLUS_SNAPSHOT"]
    # RC6 as merged, one read a pass: about the no-refresh rate
    assert inline["rest_reads_per_min"] < 0.5
    assert abs(inline["fresh_rate"] - none["fresh_rate"]) < 0.03
    assert none["fresh_rate"] < 0.80
    # decoupled REST: much better, not 0.95 at this denominator
    assert 0.85 <= fair["fresh_rate"] < 0.95
    assert start["rest_reads_per_min"] == fair["rest_reads_per_min"] == 12.0
    # the fair order starves no member; start-first does
    assert fair["worst_member"] >= 0.5 > start["worst_member"]
    # one snapshot call a minute: >= 0.97, REST left with less to do
    assert snap["fresh_rate"] >= 0.97
    assert snap["rest_reads_per_min"] < fair["rest_reads_per_min"]
    assert snap["worst_member"] >= 0.9
