"""STREAM C'S REPLAY STAGES: RANK AND ALLOCATE (control plane). Pure stages
plus one persistence step.

Stream E owns the deterministic PAPER REPLAY harness (control_plane/
replay.py, its stage registry, the dedicated replay paper account and the
point-in-time snapshots). E's module is not at this stream's base, so the
two stages are defined here with the stage signature every stream shares:

    stage(snapshots: list[dict], *, clock: float, state: dict) -> dict

  snapshots  the frozen decision-time snapshots of ONE replay batch (E's
             cp_decision_snapshots rows: each carries opportunity_id,
             snapshot_id and its `frozen` record)
  clock      the replay clock (every input is as of it)
  state      the harness's running state for the batch. Keys this stream
             reads: portfolio (allocator.portfolio_from_ledger over the
             REPLAY account at the clock), rails (readers_capital.
             approved_rails at the clock), prior_tape (readers_capital.
             qualified_tape, source PAPER_REPLAY), admission_k,
             probability_max_age_s, code_sha, meta_decisions ({snapshot_id:
             meta_decision_id}, stream B), ranker_config, allocator_config.
             Keys this stream writes: rank_run (RANK), allocation_batch
             (ALLOCATE).

Each stage returns {"stage", "records": {table: rows}, "state": {...}} --
the harness merges `state` and persists `records` (or calls
`persist(conn, outputs)` below, which writes only migration 255's tables).

ADAPTER NOTE (no harness change needed): E's registry registers
`STAGES` -- ("RANK", rank_stage) then ("ALLOCATE", allocate_stage) -- after
the meta-controller stage (B) and before the execution stage (D); D reads
state["allocation_batch"]["allocations"] (approved_size per snapshot).
"""
from __future__ import annotations

from . import allocator as AL
from . import rank_inputs as RI
from . import ranking as RK
from . import store_capital as ST


def candidate_of(snapshot: dict, *, state: dict) -> dict:
    """The rank / allocation candidate of one replay snapshot. A snapshot
    may carry the candidate ready-made (`rank_input`), or the canonical
    intent it froze (`canonical_intent`, at the top level or in `frozen`);
    the snapshot's own ids are authoritative either way."""
    frozen = snapshot.get("frozen") or {}
    if isinstance(snapshot.get("rank_input"), dict):
        cand = dict(snapshot["rank_input"])
    elif isinstance(frozen.get("rank_input"), dict):
        cand = dict(frozen["rank_input"])
    else:
        intent = snapshot.get("canonical_intent") or frozen.get(
            "canonical_intent")
        if not isinstance(intent, dict):
            raise ValueError("snapshot %s carries no rank input or canonical "
                             "intent" % snapshot.get("snapshot_id"))
        cand = RI.from_canonical_intent(
            intent, label=snapshot.get("label") or frozen.get("label"),
            probability_max_age_s=state.get("probability_max_age_s"),
            code_sha=state["code_sha"])
    for k in ("opportunity_id", "snapshot_id"):
        if snapshot.get(k):
            cand[k] = snapshot[k]
    md = (state.get("meta_decisions") or {}).get(cand.get("snapshot_id"))
    if md:
        cand["meta_decision_id"] = md
    return cand


def rank_stage(snapshots: list, *, clock: float, state: dict) -> dict:
    cands = [candidate_of(s, state=state) for s in snapshots]
    run = RK.rank_batch(cands, clock=clock, portfolio=state.get("portfolio")
                        or {}, admission_k=state.get("admission_k"),
                        cfg=state.get("ranker_config") or RK.DEFAULT_CONFIG,
                        code_sha=state["code_sha"],
                        source=state.get("source") or "PAPER_REPLAY")
    return {"stage": "RANK",
            "records": {"cp_opportunity_rankings": run["rows"]},
            "state": {"rank_run": run, "rank_candidates": cands}}


def allocate_stage(snapshots: list, *, clock: float, state: dict) -> dict:
    run = state.get("rank_run")
    if run is None or run.get("as_of") != float(clock):
        raise ValueError("ALLOCATE needs this batch's RANK output")
    cands = state.get("rank_candidates") or [
        candidate_of(s, state=state) for s in snapshots]
    batch = AL.allocate_batch(
        run, cands, portfolio=state.get("portfolio") or {},
        rails=state.get("rails") or {}, prior_tape=state.get("prior_tape"),
        cfg=state.get("allocator_config") or AL.DEFAULT_CONFIG)
    return {"stage": "ALLOCATE",
            "records": {"cp_capital_allocations": batch["allocations"]},
            "state": {"allocation_batch": batch}}


STAGES = (("RANK", rank_stage), ("ALLOCATE", allocate_stage))


async def persist(conn, state: dict) -> dict:
    """Write one batch's RANK and ALLOCATE outputs (migration 255 only)."""
    run, batch = state.get("rank_run"), state.get("allocation_batch")
    n_cfg = await ST.record_configs(
        conn, ranker_cfg=state.get("ranker_config") or RK.DEFAULT_CONFIG,
        allocator_cfg=state.get("allocator_config") or AL.DEFAULT_CONFIG,
        rails=state.get("rails"))
    n_rank = await ST.record_rank_run(conn, run) if run else 0
    n_alloc = await ST.record_allocations(conn, batch) if batch else 0
    return {"configs": n_cfg, "rankings": n_rank, "allocations": n_alloc}
