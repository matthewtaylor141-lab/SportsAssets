"""THE ECONOMIC DIGITAL TWIN AND THE PROFITABILITY EVIDENCE LAYER
(migration 219). RESEARCH ONLY.

It replays the REAL recorded stream (decisions, books, probabilities,
timestamps, fees, fills, management actions, Karen blocks, risk states)
through frozen, versioned alternate worlds, and measures what BETTOR's
evidence actually supports. It computes, persists (only to its own
`twin_*` tables) and displays (GET-only /api/command/{twin,research,
profitability,evals}). It has NO authority: it places, cancels, sizes and
activates nothing; a kill-switch criterion only writes a RECOMMEND_PAUSE
record. Outputs are labelled ACTUAL / PAPER / COUNTERFACTUAL and never
summed; an unmeasured quantity is null with a reason, never 0.

  common      labels, envelope, statistics, the metric shape
  reads       bounded, read-only loaders of the recorded stream (+ the
              optional pos_iface_* interface views of streams 216-218)
  engine      the time-indexed view (no future information) and the worlds
  scenarios   the frozen, versioned scenario catalog
  transfer    cross-sport transfer research (preregistered, forward only)
  scorecards  one financial scorecard per agent (economic contribution)
  ladder      the profitability evidence ladder and confidence
  killswitch  economic kill-switch criteria -> RECOMMEND_PAUSE records
  evals       per-agent, handoff and macro evaluations over persisted rows
  store       the only writer (twin_* tables)
  runner      the bounded, failure-isolated, advisory-locked cycle

The import closure is pinned by tests/test_twin_authority.py: no module here
may import a venue, order, ledger-writing, paper or execution module.
"""
