"""THE ONLY WRITER OF THE REPLAY (migration 237), and the endpoint's reads.

  r30_replay_runs       one row per run: run id, code sha, parameters (+
                        sha), clock range, the summary
  r30_replay_decisions  one row per replayed decision: its decision clock,
                        the latest recorded stamp its decision-step inputs
                        used (CHECKed <= the clock) and its outcome inputs
                        used (CHECKed <= the horizon), the payload
  r30_replay_events     one row per independent event of the run

All three are APPEND-ONLY (UPDATE / DELETE / TRUNCATE refused by trigger) and
CHECK the label REPLAY_NOT_FORWARD_EVIDENCE, the authority and
production_effect = 'NONE'. A run is written in ONE transaction (the run,
then its decisions and events), so a partial run never exists. Writes touch
r30_replay_* only (pinned by tests/test_r30_replay.py).
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
from decimal import Decimal

from . import LABEL

R_NO_SCHEMA = "MIGRATION_237_NOT_APPLIED"


def _ts(e):
    return None if e is None else _dt.datetime.fromtimestamp(
        float(e), _dt.timezone.utc)


def _clean(v):
    """JSON-safe: numbers stay numbers (a non-finite float is None),
    Decimals become floats, timestamps ISO strings, tuples/sets lists."""
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, Decimal):
        f = float(v)
        return f if math.isfinite(f) else None
    if isinstance(v, dict):
        return {str(k): _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [_clean(x) for x in v]
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def _j(v) -> str:
    return json.dumps(_clean(v), default=str, sort_keys=True)


def params_sha(params: dict) -> str:
    return hashlib.sha256(_j(params).encode()).hexdigest()


def run_id_for(*, code_sha: str, psha: str, start, end, started_at) -> str:
    return "r30rp_" + hashlib.sha256(
        ("%s|%s|%.6f|%.6f|%.6f" % (code_sha, psha, float(start), float(end),
                                   float(started_at))).encode()
    ).hexdigest()[:24]


async def schema(conn) -> bool:
    return bool(await conn.fetchval(
        "SELECT to_regclass('r30_replay_runs') IS NOT NULL"))


def decision_payload(item: dict) -> dict:
    rec, ev = item["rec"], item["eval"]
    pos = dict(ev["position"])
    pos.pop("own_settlement", None)
    return {"label": LABEL, "decision": {k: rec["decision"].get(k) for k in (
        "decision_id", "strategy", "verdict", "refusal", "us_market_slug",
        "holding_side", "fixture", "decided_at", "proposed_qty",
        "limit_price", "policy_version")},
        "clock": rec["clock"], "sleeve": rec["sleeve"],
        "economics_at_decision": rec["ev"], "components": rec["components"],
        "intent_parity": rec["intent_parity"],
        "qualification": rec.get("qualification"),
        "alternatives_n": len(rec["tape"]["alternatives"]),
        "tape": {k: v for k, v in rec["tape"].items()
                 if k != "alternatives"},
        "alternatives_input": {k: v for k, v in (rec.get(
            "alternatives_input") or {}).items() if k != "returns"},
        "hurdle": rec["hurdle"], "allocations": rec["allocations"],
        "rails": rec["rails"], "position": pos,
        "counterfactuals": ev["counterfactuals"],
        "management": ev["management"],
        "lost_opportunity": ev["lost_opportunity"],
        "capital_hours": ev["capital_hours"],
        "attribution_v2": item.get("v2"),
        "timeline": timeline(item), "evidence": rec["evidence"]}


def timeline(item: dict) -> list:
    """The settlement / release timeline of one opportunity."""
    rec, ev = item["rec"], item["eval"]
    c = rec["components"]
    out = []

    def add(t, kind, ref=None):
        if t is not None:
            out.append({"t": float(t), "kind": kind, "ref": ref})
    po = c.get("provider_observation") or {}
    add(po.get("observed_at"), "PROVIDER_OBSERVATION", po.get("valuation_id"))
    add((c.get("bettor_receipt") or {}).get("received_at"), "BETTOR_RECEIPT")
    add((c.get("venue_book") or {}).get("observed_at"), "VENUE_BOOK",
        (c.get("venue_book") or {}).get("obs_id"))
    add(rec["decision"].get("decided_at"), "DECISION",
        rec["decision"].get("decision_id"))
    add(rec["clock"], "DECISION_DURABLE_REPLAY_CLOCK")
    for o in rec["outcome"].get("orders") or []:
        add(o.get("created_at"), "ENTRY_ORDER", o.get("order_id"))
    for f in rec["outcome"].get("fills") or []:
        add(f.get("filled_at"), "FILL_%s_%s" % (f.get("role"),
                                                f.get("direction")),
            f.get("fill_id"))
    for r in rec["outcome"].get("reviews") or []:
        add(r.get("reviewed_at"), "XAVIER_REVIEW", r.get("review_id"))
    for s in rec["outcome"].get("settlements") or []:
        add(s.get("settled_at"), "SETTLEMENT_%s" % s.get("outcome"),
            s.get("settlement_id"))
    eco = ev["economics"]["actual"]
    add(eco.get("released_at"), "CAPITAL_RELEASED")
    out.sort(key=lambda x: x["t"])
    return out[:200]


async def record_run(conn, built: dict, *, code_sha: str,
                     triggered_by: str) -> dict:
    """Persist one run (one transaction). Returns {recorded, run_id}."""
    if not await schema(conn):
        return {"recorded": False, "refusal": R_NO_SCHEMA}
    meta = built["meta"]
    psha = params_sha(meta["parameters"])
    rid = run_id_for(code_sha=code_sha, psha=psha, start=meta["clock_start"],
                     end=meta["clock_end"], started_at=meta["started_at"])
    async with conn.transaction():
        await conn.execute(
            "INSERT INTO r30_replay_runs (run_id, run_version, code_sha, "
            " parameters, params_sha, clock_start, clock_end, started_at, "
            " finished_at, decisions_n, events_n, summary, triggered_by) "
            "VALUES ($1,$2,$3,$4::jsonb,$5,$6,$7,$8,$9,$10,$11,$12::jsonb,$13)",
            rid, meta["version"], code_sha, _j(meta["parameters"]), psha,
            _ts(meta["clock_start"]), _ts(meta["clock_end"]),
            _ts(meta["started_at"]), _ts(meta["finished_at"]),
            len(built["items"]), len(built["events"]),
            _j(dict(built["summary"], meta=meta)), triggered_by)
        for it in built["items"]:
            rec = it["rec"]
            await conn.execute(
                "INSERT INTO r30_replay_decisions (run_id, decision_id, "
                " event_key, sleeve, verdict, decision_clock, clock_end, "
                " decision_evidence_max_recorded_at, "
                " outcome_evidence_max_recorded_at, payload) VALUES "
                " ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb)",
                rid, rec["decision"]["decision_id"], it["event_key"],
                rec["sleeve"], rec["decision"].get("verdict"),
                _ts(rec["clock"]), _ts(meta["clock_end"]),
                _ts(rec["evidence"]["decision"]["max_recorded_at"]),
                _ts(rec["evidence"]["outcome"]["max_recorded_at"]),
                _j(decision_payload(it)))
        for e in built["events"]:
            await conn.execute(
                "INSERT INTO r30_replay_events (run_id, event_key, sleeves, "
                " investment_only, decisions_n, clock_end, "
                " evidence_max_recorded_at, payload) VALUES ($1,$2,$3,$4,$5,"
                " $6,$7,$8::jsonb)",
                rid, e["event_key"], e["sleeves"], e["investment_only"],
                e["decisions"], _ts(meta["clock_end"]),
                _ts(max([x for x in (
                    e["evidence"]["decision_step_max_recorded_at"],
                    e["evidence"]["outcome_max_recorded_at"])
                    if x is not None], default=None)), _j(e))
    return {"recorded": True, "run_id": rid, "params_sha": psha}


# ═════════════════════════════════════════════════════════════════════
# THE ENDPOINT'S READS (r30_replay_* only)
# ═════════════════════════════════════════════════════════════════════

def _row(r) -> dict:
    out = {}
    for k, v in dict(r).items():
        if hasattr(v, "isoformat"):
            v = v.isoformat()
        elif isinstance(v, str) and k in ("parameters", "summary",
                                          "payload"):
            try:
                v = json.loads(v)
            except ValueError:
                pass
        out[k] = v
    return out


async def read(conn, *, run_id: str | None, limit: int, decision_id=None,
               investment_only: bool = False) -> dict:
    runs = [_row(r) for r in await conn.fetch(
        "SELECT run_id, run_version, code_sha, params_sha, clock_start, "
        "       clock_end, started_at, finished_at, decisions_n, events_n, "
        "       triggered_by, label, authority FROM r30_replay_runs "
        " ORDER BY recorded_at DESC, run_id LIMIT 20")]
    if not runs:
        return {"status": "EMPTY", "why": "NO_REPLAY_RUN_RECORDED",
                "runs": [], "run": None, "events": []}
    rid = run_id or runs[0]["run_id"]
    run = await conn.fetchrow(
        "SELECT * FROM r30_replay_runs WHERE run_id = $1", rid)
    if run is None:
        return {"status": "NOT_FOUND", "why": "NO_SUCH_RUN", "runs": runs,
                "run": None, "events": []}
    events = [_row(r) for r in await conn.fetch(
        "SELECT event_key, sleeves, investment_only, decisions_n, payload "
        "  FROM r30_replay_events WHERE run_id = $1 "
        "   AND ($2::boolean IS FALSE OR investment_only) "
        " ORDER BY event_key LIMIT $3", rid, investment_only, int(limit))]
    out = {"status": "OK", "why": None, "runs": runs, "run": _row(run),
           "events": events}
    if decision_id:
        d = await conn.fetchrow(
            "SELECT * FROM r30_replay_decisions WHERE run_id = $1 "
            "   AND decision_id = $2", rid, decision_id)
        out["decision"] = None if d is None else _row(d)
    return out
