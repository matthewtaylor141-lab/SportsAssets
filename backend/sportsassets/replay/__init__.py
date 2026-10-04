"""THE HISTORICAL R30 DECISION REPLAY (owner red-team acceptance addendum,
specs/HISTORICAL_REPLAY.md). RESEARCH / TOURNAMENT EVIDENCE, NO AUTHORITY.

For each historical opportunity (a paper decision) the replay rebuilds the
R30 chain with ONLY the evidence recorded by the replay clock -- provider
observation, BETTOR receipt, mapping, valuation, venue book, Derek's
decision, Karen's review state, Allie's allocation (allie_capital.allocate
on point-in-time inputs), Eddie's estimate, the canonical decision intent
(canonical_intent.build_decision_intent, pure), the PAPER execution,
Xavier's management (and the canonical management action,
canonical_intent.management_action, on the review's point-in-time inputs)
and the settlement / release timeline -- then prices the counterfactuals
(current action, R30 canonical action, ROI-only allocation, capital-hour
allocation, no-trade, hold-to-settlement, actual and canonical Xavier
management) and the Alpha Attribution V2 decomposition, per INDEPENDENT
event (profitability.validation.event_key).

  pit             THE ONE CHOKE-POINT READER: every row the replay uses goes
                  through it, and it returns only rows with
                  recorded_at <= the clock (versioned policy rows: effective
                  at the clock); mutable columns are revealed only when
                  their own stamp is <= the clock. Single-table reads only,
                  so no join can carry a future row past the filter.
  reconstruct     the components at the decision clock and the timeline
  counterfactuals the counterfactual actions and their P&L (pure)
  events         per independent event, the run summary, marked drawdown
  runner          one bounded run; store persists it append-only
  store           the only writer (r30_replay_* tables, migration 237)

EVERY OUTPUT IS LABELLED REPLAY_NOT_FORWARD_EVIDENCE. A historical input
that was never recorded (no Karen record at the clock, no event start, no
settlement-lag sample) makes its replayed component UNAVAILABLE with that
reason -- never invented. Nothing here changes ENTER, sizing, management,
a rail, a threshold, an order or capital; no replay result can promote
production policy (the database CHECKs the label, the authority and
production_effect = 'NONE').
"""
LABEL = "REPLAY_NOT_FORWARD_EVIDENCE"
AUTHORITY = "NO_AUTHORITY_RESEARCH_TOURNAMENT_EVIDENCE"
DISCLOSURE = (
    "REPLAY_NOT_FORWARD_EVIDENCE: a reconstruction of historical decisions "
    "with only the evidence recorded by each replay clock. Tournament "
    "evidence for research; never forward evidence, never activation "
    "evidence, and no replay result can promote production policy. Nothing "
    "here places, sizes, cancels or manages an order, or changes a rail, "
    "threshold or capital. Unrecorded inputs are UNAVAILABLE with a reason, "
    "never invented.")
