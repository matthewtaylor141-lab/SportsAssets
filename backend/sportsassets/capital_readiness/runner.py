"""Persistence helpers for the Capital Readiness Lab.

The caller supplies already-computed read-only evidence. These helpers only
append RESEARCH/SHADOW evidence to migration-310 tables. They never import or
call any venue, execution, funded, order, or authority module.
"""
from __future__ import annotations

import datetime as dt
import json

from . import agent_championship as AC
from . import readiness as R
from . import scale_twin as ST
from . import shadow_court as SC

VERSION = "CAPITAL_READINESS_RUNNER_V1"


def _ts(epoch=None):
    if epoch is None:
        return dt.datetime.now(dt.timezone.utc)
    if hasattr(epoch, "tzinfo"):
        return epoch
    return dt.datetime.fromtimestamp(float(epoch), dt.timezone.utc)


async def record_agent_observation(conn, *, agent, role_metric, subject_id,
                                   economic_alpha_usd, detail=None,
                                   observed_at=None):
    await conn.execute(
        "INSERT INTO capital_readiness_agent_economics "
        "(agent,role_metric,subject_id,economic_alpha_usd,detail,observed_at,version) "
        "VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7) ON CONFLICT DO NOTHING",
        str(agent).upper(), str(role_metric), str(subject_id),
        economic_alpha_usd, json.dumps(detail or {}), _ts(observed_at),
        AC.VERSION)


async def record_court(conn, court, *, decided_at=None, result=None):
    await conn.execute(
        "INSERT INTO capital_readiness_shadow_court "
        "(decision_id,chosen,shadow_winner,disagreement,alternatives,result,decided_at,scored_at,version) "
        "VALUES ($1,$2,$3,$4,$5::jsonb,$6::jsonb,$7,$8,$9)",
        str(court.get("decision_id")), court.get("chosen"),
        str(court.get("shadow_winner")), bool(court.get("disagreement")),
        json.dumps(court.get("alternatives") or []),
        None if result is None else json.dumps(result), _ts(decided_at),
        None if result is None else _ts(), SC.VERSION)


async def record_scale(conn, scope_key, twin, inputs, *, computed_at=None):
    at = _ts(computed_at)
    for row in twin.get("rows") or []:
        if row.get("status") != "MEASURED":
            continue
        await conn.execute(
            "INSERT INTO capital_readiness_scale_trials "
            "(scope_key,capital_usd,expected_net_usd,lower_bound_usd,effective_edge_bps,positive_lower_bound,inputs,computed_at,version) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9)",
            str(scope_key), row.get("capital_usd"), row.get("expected_net_usd"),
            row.get("lower_bound_usd"), row.get("effective_edge_bps"),
            bool(row.get("positive_lower_bound")), json.dumps(inputs or {}),
            at, ST.VERSION)


async def record_readiness(conn, verdict, *, source_sha=None, computed_at=None):
    await conn.execute(
        "INSERT INTO capital_readiness_runs "
        "(version,source_sha,status,readiness_status,readiness_score,recommended_capital_usd,hard_gates,blocking_gates,payload,computed_at) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8::jsonb,$9::jsonb,$10)",
        R.VERSION, source_sha, verdict.get("status", "OK"),
        verdict.get("readiness_status", "RED"), verdict.get("readiness_score"),
        verdict.get("recommended_capital_usd", 0),
        json.dumps(verdict.get("hard_gates") or {}),
        json.dumps(verdict.get("blocking_gates") or []),
        json.dumps(verdict), _ts(computed_at))
