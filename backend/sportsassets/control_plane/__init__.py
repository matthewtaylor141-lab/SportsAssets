"""THE BETTOR PROFITABILITY CONTROL PLANE (shared package, streams A-H).

Every layer of the control plane runs in production as SHADOW: it reads the
same immutable candidate state the canonical decision uses, records its
outputs append-only with authority 'SHADOW', and changes NO production order,
size, admission order or gate. Its online pieces are pure and bounded so
that, once promoted through stream G's gates AND explicitly approved by the
owner, the same code becomes components of the ONE canonical decision
(Scout/Derek -> Karen -> Allie -> Eddie -> canonical intent), never a second
decision path.

Stream E (this stream's modules) is the foundation the others build on:

  ids.py       the stable identifiers: opportunity_key / opportunity_id
               (equal to the canonical intent's), snapshot_id, code_sha(),
               config_sha()
  pit.py       THE ONE point-in-time accessor: every offline read filters by
               the row's own recorded stamp <= the clock; mutable columns are
               never selectable
  snapshot.py  build + freeze the decision-time state (cp_decision_snapshots)
               offline from the records, and `shadow_record`, the single
               function the CONTROL_PLANE_SHADOW decision hook slot will call
  observe.py   the offline post-trade pass (+10s .. settlement) over
               RECORDED evidence only (cp_post_trade_observations)
  store.py     the only writer of stream E's tables, and its bounded readers
  replay.py    the deterministic PAPER REPLAY harness: historical
               opportunities in time order through point-in-time inputs into
               a dedicated replay paper account (cp_replay_*), every stage
               pluggable (ranker, agents, meta, allocator, execution, risk,
               fill, post-trade, counterfactual, league table, promotion)

THE BOUNDARY. No module here imports an order, venue, execution, funded or
live module, and no decision path imports this package at this stage
(tests/test_cp_e_authority.py proves both directions). Integration into the
online chain happens later through a decision_hooks slot
(CONTROL_PLANE_SHADOW) installed by the executing process, exactly like
live_parity.install.
"""

AUTHORITY = "SHADOW"
#: the decision_hooks slot name the executing process will install
#: `snapshot.shadow_record` into (decision_hooks is not edited at this stage)
SHADOW_HOOK_SLOT = "CONTROL_PLANE_SHADOW"
