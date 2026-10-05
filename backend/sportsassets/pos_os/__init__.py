"""THE PROFITABILITY OS VIEW: GET /api/command/profitability/os.
RESEARCH / SHADOW_NO_AUTHORITY.

ONE READ-ONLY PAGE OVER EVERY PROFITABILITY COMPONENT. Each component is a
SECTION answering OK / EMPTY / UNAVAILABLE with why. Components that already
exist elsewhere in the line (regime detection, post-trade attribution, the
counterfactual twin, the model tournament, experiment design) are READ from
their own recorded rows; the components built here are pure functions over
recorded rows:

  champion        strategy champion / challenger on realized return on
                  capital, with the model tournament's latest verdicts
  execution       execution-cost and adverse-selection learning from
                  recorded fills versus later recorded marks, and the
                  execution-policy league (realized cost per filled $)
  capital         the capital-hour optimizer (RECOMMENDATION ONLY) and the
                  expected-profit clock
  frontier        the capacity frontier: depth-limited size vs net $ per
                  strategy
  sentinel        the data-quality sentinel
  governance      autonomous experiment governance audit (predeclared
                  hypotheses, stopping rules, no peeking promotion)
  drift           model and economic drift
  correlation     scenario / exposure correlation by event, league and team
  autonomy        autonomy-health metrics
  release_twin    the release / incident digital twin (recorded before /
                  after replay, read only)
  existing        summaries of the components that already exist

WHAT THIS PACKAGE CANNOT DO, BY CONSTRUCTION. It imports no venue, order,
execution, ledger or paper module (tests/test_pos_os_authority.py walks the
transitive import closure); reads.py issues only SELECTs and no module here
holds a write statement; nothing reads a section to place, size, cap, gate
or allow anything. A recommendation is a number on a page with
`applied: false`.
"""
