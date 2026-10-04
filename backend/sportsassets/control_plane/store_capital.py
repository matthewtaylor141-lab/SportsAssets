"""THE APPEND-ONLY RECORDS OF THE RANKER AND THE ALLOCATOR (migration 255).

INSERT only, into 255's own tables, idempotent by id (ON CONFLICT DO
NOTHING -- a replayed batch writes nothing twice and never updates). The
caller holds the transaction. Nothing reads these tables on a decision path.
"""
from __future__ import annotations

import json

from . import allocator as AL
from . import ranking as RK


def _j(v) -> str:
    return json.dumps(v, sort_keys=True, default=str)


async def record_configs(conn, *, ranker_cfg=RK.DEFAULT_CONFIG,
                         allocator_cfg=AL.DEFAULT_CONFIG,
                         rails: dict | None = None) -> int:
    rows = [("CP_RANKER_CONFIG", ranker_cfg.version, "RESEARCH",
             ranker_cfg.to_dict(), ranker_cfg.sha),
            ("CP_ALLOCATOR_CONFIG", allocator_cfg.version, "RESEARCH",
             allocator_cfg.to_dict(), allocator_cfg.sha)]
    if rails and rails.get("rails_sha"):
        rows.append(("CP_APPROVED_RAILS", "AS_READ", "APPROVED_RAILS_AS_READ",
                     {k: rails.get(k) for k in ("account_id", "paper", "live",
                                                "sources", "session_id")},
                     rails["rails_sha"]))
    n = 0
    for kind, version, label, cfg, sha in rows:
        got = await conn.fetchval(
            """INSERT INTO cp_ranking_allocation_configs (config_sha,
                 config_kind, version, label, config)
               VALUES ($1,$2,$3,$4,$5::jsonb)
               ON CONFLICT (config_sha) DO NOTHING RETURNING config_sha""",
            sha, kind, version, label, _j(cfg))
        n += got is not None
    return n


RANK_SQL = """
    INSERT INTO cp_opportunity_rankings (ranking_id, rank_run_id, as_of,
      opportunity_id, snapshot_id, meta_decision_id, strategy,
      strategy_class, agent, rank, tier, raw_ev, net_ev,
      confidence_component, execution_component, liquidity_component,
      capital_efficiency_component, risk_component, correlation_component,
      final_priority, components, gates_passed, gate_failures, admitted,
      admission_basis, starvation_credit, admission, ranker_version,
      config_sha, code_sha, model_versions, source, authority)
    VALUES ($1,$2,to_timestamp($3),$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,
      $16,$17,$18,$19,$20,$21::jsonb,$22,$23::text[],$24,$25,$26,$27::jsonb,
      $28,$29,$30,$31::jsonb,$32,'SHADOW')
    ON CONFLICT (ranking_id) DO NOTHING"""


async def record_rank_run(conn, run: dict) -> int:
    adm = _j(run["admission"])
    args = [(r["ranking_id"], r["rank_run_id"], r["as_of"],
             r["opportunity_id"], r["snapshot_id"], r.get("meta_decision_id"),
             r.get("strategy"), r["strategy_class"], r.get("agent"),
             r["rank"], r["tier"], r["raw_ev"], r["net_ev"],
             r["confidence_component"], r["execution_component"],
             r["liquidity_component"], r["capital_efficiency_component"],
             r["risk_component"], r["correlation_component"],
             r["final_priority"], _j(r["components"]), r["gates_passed"],
             list(r["gate_failures"]), r["admitted"], r["admission_basis"],
             r["starvation_credit"], adm, r["ranker_version"],
             r["config_sha"], r["code_sha"], _j(r["model_versions"]),
             r["source"]) for r in run["rows"]]
    if args:
        await conn.executemany(RANK_SQL, args)
    return len(args)


ALLOC_SQL = """
    INSERT INTO cp_capital_allocations (allocation_id, ranking_id,
      rank_run_id, as_of, opportunity_id, snapshot_id, meta_decision_id,
      strategy, rank, requested_size, approved_size, approved_qty,
      size_limiting_factor, size_limiting_kind, size_limiting_why, caps,
      portfolio_exposure_after, kelly_fraction_used, uncertainty_haircuts,
      capital_efficiency, expected_log_utility_gain, allie, allie_basis,
      live_lane, lane, allocator_version, config_sha, rails_sha, code_sha,
      model_versions, source, authority)
    VALUES ($1,$2,$3,to_timestamp($4),$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,
      $16::jsonb,$17::jsonb,$18,$19::jsonb,$20::jsonb,$21,$22::jsonb,$23,
      $24::jsonb,$25,$26,$27,$28,$29,$30::jsonb,$31,'SHADOW')
    ON CONFLICT (allocation_id) DO NOTHING"""


async def record_allocations(conn, batch: dict) -> int:
    args = [(a["allocation_id"], a["ranking_id"], a["rank_run_id"],
             a["as_of"], a["opportunity_id"], a["snapshot_id"],
             a.get("meta_decision_id"), a.get("strategy"), a["rank"],
             a["requested_size"], a["approved_size"], a["approved_qty"],
             a["size_limiting_factor"], a["size_limiting_kind"],
             a.get("size_limiting_why"), _j(a["caps"]),
             _j(a["portfolio_exposure_after"]), a["kelly_fraction_used"],
             _j(a["uncertainty_haircuts"]), _j(a["capital_efficiency"]),
             a["expected_log_utility_gain"], _j(a["allie"]),
             a["allie_basis"], _j(a["live_lane"]), a["lane"],
             a["allocator_version"], a["config_sha"], a.get("rails_sha"),
             a["code_sha"], _j(a["model_versions"]), a["source"])
            for a in batch["allocations"]]
    if args:
        await conn.executemany(ALLOC_SQL, args)
    return len(args)
