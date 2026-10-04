"""THE PROFITABILITY OPERATING SYSTEM, PART 1 (migration 216). RESEARCH.

NORTH STAR: repeatable forward net economic profit per unit of risk and
capital-time -- not trade count, win rate or headline EV.

Pure computation modules, one bounded loader, one writer and one scheduled
runner in the API process. EVERYTHING HERE IS RESEARCH / SHADOW: it reads
the existing records, computes, persists ONLY to its own append-only pos_*
tables and displays (GET /api/command/profitability/*). It has NO venue,
order, sizing, limit, threshold or capital authority.

  economics   capital-hour economics per position (PAPER, ACTUAL, and the
              COUNTERFACTUAL hold-to-settlement of a managed position) and
              the portfolio's capital: locked, release schedule, utilization,
              idle, waiting for settlement
  warehouse   the source-record lineage of every position, stage by stage,
              with its named reproducibility gaps
  capacity    per candidate, from the recorded book: depth, impact, edge at
              size, capacity ceiling, theoretical vs executable opportunity
  metrics     the five north-star metrics with sample, CI, status, trend and
              freshness
  forecast    the predictable monthly revenue engine (block bootstrap of
              forward realized outcomes), UNPROVEN until its own forecasts
              score well; every forecast persisted and scored later
  reads       the only loader (SELECT only, bounded)
  store       the only writer (pos_* tables only)
  runner      the bounded, failure-isolated, advisory-locked cycle
              (POS_ECON=off is the kill switch)

PAPER, ACTUAL and COUNTERFACTUAL are never summed. An unmeasured value is
None with a named reason, never 0. The import whitelist and the write
targets are pinned by tests/test_profitability_is_research_only.py.
"""
