"""SHADOW INSTITUTIONAL INVESTMENT-INTELLIGENCE LAYER (migration 208).

Pure computation modules plus one scheduled shadow runner in the API
process. EVERYTHING HERE IS SHADOW: it computes, persists (only to its own
`intel_*` tables) and displays (GET /api/command/intel/*). It has NO venue
authority, places and cancels nothing, changes no live sizing, limit or
threshold, and never modifies a production probability.

  calibration   D  PinnAPI-derived predictions joined to outcomes: Brier, log
                   loss, reliability, segmented, with Wilson / bootstrap CIs;
                   a frozen forward out-of-sample overlay protocol
  attribution   E  model edge vs executable edge vs fees vs slippage vs
                   management vs settlement, per position, reconciled to cash
  risk          J  exposure by game / team / conference / sport / market
                   family / live state / settlement class / venue, clusters,
                   liquidity-at-risk, daily loss, drawdown; PAPER and ACTUAL
                   separately, never summed (Audrey recomputes independently
                   in agents/audrey_intel_risk.py)
  regime        K  spread, liquidity, PinnAPI volatility, feed latency/gaps,
                   mapping, calibration drift, execution -> NORMAL / REDUCE /
                   NO_TRADE (a recommendation with no authority)
  sizing        I  evidence-quality shadow size beside the paper/actual size,
                   never applied
  allocator     A  ranked shadow allocation of the $1,000 notional sleeve
  store            the only writer (intel_* tables)
  runner           the bounded, failure-isolated scheduled cycle

The import whitelist is pinned by tests/test_intel_is_shadow_only.py: no
module here may import a venue, order, ledger-writing or execution module.
"""
