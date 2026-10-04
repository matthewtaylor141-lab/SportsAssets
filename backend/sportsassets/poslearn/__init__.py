"""THE PROFITABILITY OPERATING SYSTEM, PART 3: EVALUATION AND LEARNING
(migration 218). SHADOW / RESEARCH ONLY.

  models           1  the model tournament: M0_PINNAPI_RAW (champion, the
                      approved PinnAPI policy) vs M1 calibrated, M2
                      microstructure, M3 Scout (AWAITING_FEATURES), M4
                      cross-market consensus (AWAITING_SOURCE), M5 sport-
                      specific (NFL, MLB, NCAAF, NBA, NHL, SOCCER, TENNIS)
  edge_confidence  2  the edge-confidence meta-model (Laplace-uncertain
                      logistic; the future sizing formula NOT active)
  avoidance        3  NORMAL / CAUTION / AVOID with AVOIDANCE_RISK and named
                      reasons, learned from where predicted EV failed
  agents           4  the agent tournament: XAVIER / DEREK / ALLOCATOR /
                      EDDIE, each V1 vs CHALLENGER_A / _B on the same
                      opportunities (EDDIE through a read-only interface,
                      AWAITING_INTERFACE until it exists)
  experiments      5  prospective, seeded, DB-verified randomized SHADOW
                      experiments with Karen design challenge and Audrey
                      randomization audit
  scoring             forward scores and the predeclared fixed-sample
                      verdict against the champion
  karen_review        Karen's promotion and design challenges (no authority)
  audrey_review       Audrey's independent recompute (imports none of the
                      runner's scoring code)
  features            the as-of feature contract (every brief feature,
                      available or named UNAVAILABLE)
  logistic            pure-Python ridge logistic + Laplace (no numpy here)
  reads / store       the bounded reader / the only writer (poslearn_*)
  runner           6  the bounded, failure-isolated cycle (POS_LEARN=off)

NO AUTHORITY: nothing here changes a production probability, threshold,
size, allowlist, order or capital. Promotion = predeclared criteria met ->
Karen challenge -> Audrey evaluation -> a HUMAN approval record that no
agent and no code path in this repository writes. The import closure is
pinned by tests/test_poslearn_authority.py.
"""
