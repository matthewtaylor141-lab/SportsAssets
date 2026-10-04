"""THE INDEPENDENT RED-TEAM KIT'S REFERENCE MODELS, VENDORED UNCHANGED (R30
acceptance addendum, owner-supplied 2026-10-04). RESEARCH ONLY.

  marginal_capital_value   the marginal / tranche capital allocator (best
                           next use of the next dollar: expected net x fill x
                           confidence x risk x correlation per capital-hour,
                           greedy under a reserve and a hurdle)
  system_truth             the conjunctive System Truth hard gates (any
                           false / unavailable / missing hard gate =>
                           NOT_READY; no weighted average can hide a zero)

WHY VENDORED BYTE FOR BYTE. The kit is independent of the implementation
streams; its value as an acceptance reference is that nobody here tuned it.
tests/test_research_ref_models.py pins each file's sha256 to the kit's own
file, runs the kit's tests (imports adapted only), and pins that these
modules import only the standard library.

NO AUTHORITY. Nothing on the decision, sizing, order, management, capital or
rail path imports this package (pinned by the same test: the only importers
are the research replay and its read-only endpoint). The historical replay
uses `marginal_capital_value.allocate` as a SHADOW tranche allocator to price
the CAPITAL_HOUR_RANKING counterfactual; no result of it sizes, gates or
promotes anything.
"""
