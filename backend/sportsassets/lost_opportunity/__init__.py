"""THE LOST OPPORTUNITY LEDGER AND THE OPPORTUNITY SCORE (migration 220).
RESEARCH / SHADOW_NO_AUTHORITY.

  classify   pure: one settled REFUSE -> GOOD_REFUSAL / FALSE_REFUSAL /
             UNKNOWABLE, from the DECISION-TIME record only, plus the
             HYPOTHETICAL P&L at the decision-time executable price
  score      pure: the transparent Opportunity Score of one candidate
             (expected net executable EV x fill probability x capacity
             factor / capital-hours), null with a named reason
  reads      the only loader (SELECT only, bounded)
  store      the only writer (lol_* tables only, INSERT only)
  runner     the component the profitability cycle calls after FORECAST
             (bounded, failure-isolated, its own run log)

A HINDSIGHT WINNER IS NOT A MISSED OPPORTUNITY: the settlement outcome never
feeds the classification. Nothing here reaches a venue, an order, a size, a
limit, a threshold or capital; pinned by
tests/test_lost_opportunity_is_research_only.py.
"""
