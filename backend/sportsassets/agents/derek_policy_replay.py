"""DEREK'S ENTRY POLICIES, REPLAYED ON THE INPUTS DEREK RECORDED -- RETROSPECTIVE.

WHAT IT DOES. Every stored Derek decision (`derek_entry_decisions`) carries
the inputs it was judged on: the de-vigged Pinnacle probability and whether
the lane's freshness rule qualified it, the approved internal model's
probability and version (NULL when no approved model scored it), the depth
walk and the fees (`evidence.economics`), the parameters, and every other
named check with its status. `replay_records` re-runs the COMBINATION RULE of
each policy -- `derek_policy.decide_entry`, the same function the live gate
uses -- on those recorded inputs under DEREK_ENTRY_POLICY_V1 and
DEREK_ENTRY_POLICY_V2, keeps every other check exactly as recorded, and
reports per policy: candidates considered, admitted, refused by reason, and
for resolved fixtures the net result at the recorded prices and fees.

WHAT IT IS NOT. It is RETROSPECTIVE: a replay of recorded inputs, not a
prospective evaluation, and a fill was never proven. In production no
recorded candidate has been admissible (the venue's book currency is not
established: VENUE_BOOK_CURRENCY_NOT_ESTABLISHED refuses before either
combination rule matters), so on production records the two policies differ
only in which combination condition they would name; the comparison is on
recorded inputs only. Nothing here decides, sends or writes.
"""

from __future__ import annotations

import json
from typing import Any

from . import derek_policy as DP

VERSION = "DEREK_POLICY_REPLAY_V1"
EVIDENCE = "RETROSPECTIVE"
NOTE = (
    "RETROSPECTIVE: re-evaluates the inputs Derek recorded at each decision "
    "under each policy's combination rule (derek_policy.decide_entry, the "
    "live function); every other check is kept exactly as recorded. Not a "
    "prospective evaluation; no fill is proven. With zero admissible entries "
    "in production (VENUE_BOOK_CURRENCY_NOT_ESTABLISHED), the comparison is "
    "on recorded inputs only.")
#: The checks the replay recomputes; every other check is read as recorded.
RECOMPUTED = (DP.C_AGREEMENT, DP.C_BLENDED, DP.C_NET_EV)
#: The verdict order `derek_policy.evaluate` applies.
ORDER = (DP.C_PURPOSE, DP.C_IDENTITY, DP.C_REAL, DP.C_SETTLEMENT,
         DP.C_PROBABILITY, DP.C_DEPTH, DP.C_TICK, DP.C_FEES, DP.C_CAPACITY,
         DP.C_MODEL, DP.C_AGREEMENT, DP.C_BLENDED, DP.C_NET_EV, DP.C_LANE)
R_NO_WALK = "NO_RECORDED_DEPTH_WALK"


def _j(v):
    if v is None or isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return None


def _f(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def econ_from_record(rec: dict) -> dict:
    """The recorded depth walk and fees, as `economics` would have returned
    them. Older records carry the walk only in `fee_basis`; a record with
    neither falls back to (executable_price, qty)."""
    ev = _j(rec.get("evidence")) or {}
    econ = dict(ev.get("economics") or {})
    fills = econ.get("fills")
    if not fills:
        fills = [[_f(b.get("price")), _f(b.get("qty"))]
                 for b in (econ.get("fee_basis") or [])
                 if isinstance(b, dict) and b.get("price") is not None
                 and b.get("qty") is not None]
    if not fills and _f(rec.get("executable_price")) is not None and \
            _f(rec.get("qty")):
        fills = [[_f(rec["executable_price"]), _f(rec["qty"])]]
    fills = [[px, q] for px, q in (fills or [])
             if px is not None and q is not None and q > 0]
    fees = econ.get("fees_usd")
    if fees is None:
        fees = _f(rec.get("fees_usd"))
    if not fills:
        return {"ok": False, "refusal": R_NO_WALK}
    qty = sum(q for _, q in fills)
    cost = sum(px * q for px, q in fills)
    return {"ok": True, "fills": fills, "qty": qty,
            "acquisition_cost_usd": round(cost, 9),
            "executable_price": round(cost / qty, 9),
            "executable_price_basis": "RECORDED_DEPTH_WALK",
            "fees_usd": fees, "fees_ok": fees is not None}


def inputs_from_record(rec: dict) -> dict:
    """The recorded inputs `decide_entry` needs, and the recorded checks."""
    checks = _j(rec.get("checks")) or []
    by = {c.get("check"): c for c in checks if isinstance(c, dict)}
    ev = _j(rec.get("evidence")) or {}
    prob = by.get(DP.C_PROBABILITY) or {}
    model = by.get(DP.C_MODEL) or {}
    pd = ev.get("policy_decision") or {}
    p_int = _f(rec.get("model_p"))
    return {
        "checks": checks,
        "internal": {"p": p_int, "qualified": p_int is not None,
                     "refusal": (None if p_int is not None else
                                 model.get("refusal") or DP.R_NO_MODEL),
                     "model_version": rec.get("model_version"),
                     "at": _f(rec.get("model_at"))},
        "pinnacle": {"p": _f(rec.get("pinnacle_p")),
                     "qualified": prob.get("status") == DP.PASS,
                     "qualification": rec.get("pinnacle_qualification"),
                     "refusal": prob.get("refusal"),
                     "why": prob.get("detail"),
                     "at": _f(rec.get("pinnacle_at"))},
        "econ": econ_from_record(rec),
        "params": dict(DP.DEFAULT_PARAMS, **{
            k: v for k, v in dict(ev.get("params") or {}).items()
            if k in DP.DEFAULT_PARAMS}),
        # V2 records store the void measure they were valued with.
        "void": ({"ok": True,
                  "rate": (pd.get("settlement_states") or {}).get("void_rate"),
                  "upper_95": (pd.get("settlement_states") or {}).get(
                      "void_upper_95")}
                 if (pd.get("settlement_states") or {}).get("applied")
                 else None),
        "void_refunds_price": True,
    }


def replay_one(rec: dict, policy: str) -> dict:
    """One recorded decision under `policy`. Pure."""
    inp = inputs_from_record(rec)
    pd = DP.decide_entry(policy, internal=inp["internal"],
                         pinnacle=inp["pinnacle"], econ=inp["econ"],
                         params=inp["params"], void=inp["void"],
                         void_refunds_price=inp["void_refunds_price"])
    checks = [c for c in inp["checks"] if c.get("check") not in RECOMPUTED]
    checks += [DP.combination_check(pd), DP.net_ev_check(pd)]
    by = {c["check"]: c for c in checks}
    failing = [by[n] for n in ORDER if n in by
               and by[n].get("status") != DP.PASS
               and by[n].get("blocks", DP.BLOCKS_POLICY) == DP.BLOCKS_POLICY]
    return {"policy": policy,
            "verdict": DP.ENTER if not failing else DP.REFUSE,
            "refusal": failing[0].get("refusal") if failing else None,
            "decision": pd}


def realised_net(rec: dict, pd: dict) -> float | None:
    """What an admitted entry would have settled to at its recorded walk and
    fees: sum q (outcome - price) - fees; a void refunds the price and keeps
    the fees. None when the outcome is not known."""
    econ = econ_from_record(rec)
    if not econ.get("ok") or econ.get("fees_usd") is None:
        return None
    basis = str(rec.get("outcome_basis") or "").upper()
    if rec.get("outcome_known") and "VOID" in basis:
        return round(-float(econ["fees_usd"]), 9)
    out = rec.get("outcome")
    if not rec.get("outcome_known") or out not in (0, 1):
        return None
    return round(sum(q * (float(out) - px) for px, q in econ["fills"])
                 - float(econ["fees_usd"]), 9)


def replay_records(records: list, *,
                   policies: tuple = (DP.POLICY_V1, DP.POLICY_V2)) -> dict:
    """RETROSPECTIVE REPLAY OF RECORDED DECISIONS UNDER EACH POLICY. Pure."""
    recs = [dict(r) for r in records or []]
    per: dict[str, Any] = {}
    changed = []
    verdicts: dict[str, dict] = {}
    for pol in policies:
        admitted, refused, fx_all, fx_adm = 0, {}, set(), set()
        res_n, res_fx, net, cap, exp_net = 0, set(), 0.0, 0.0, 0.0
        for r in recs:
            got = replay_one(r, pol)
            verdicts.setdefault(str(r.get("decision_id")), {})[pol] = got
            fx = str(r.get("fixture") or r.get("decision_id"))
            fx_all.add(fx)
            if got["verdict"] != DP.ENTER:
                k = str(got["refusal"])
                refused[k] = refused.get(k, 0) + 1
                continue
            admitted += 1
            fx_adm.add(fx)
            rn = realised_net(r, got["decision"])
            if rn is None:
                continue
            res_n += 1
            res_fx.add(fx)
            net += rn
            cap += float(got["decision"].get("total_cost_usd") or 0.0)
            exp_net += float(got["decision"].get("net_expected_profit_usd")
                             or 0.0)
        per[pol] = {
            "policy": pol, "combination_policy": DP.POLICY_RULES[pol],
            "active": pol == DP.ACTIVE_POLICY,
            "candidates_considered": len(recs),
            "fixtures_considered": len(fx_all),
            "admitted": admitted, "admitted_fixtures": len(fx_adm),
            "refused": len(recs) - admitted,
            "refused_by_reason": dict(sorted(refused.items())),
            "resolved": {
                "admitted_and_resolved": res_n,
                "resolved_fixtures": len(res_fx),
                "net_result_usd": round(net, 6),
                "capital_deployed_usd": round(cap, 6),
                "return_on_capital": (round(net / cap, 6) if cap > 0
                                      else None),
                "expected_net_on_the_same_entries_usd": round(exp_net, 6),
                "basis": ("recorded depth walk and fees; outcome from the "
                          "valuation's settlement join; a void refunds the "
                          "price and keeps the fees; fills NOT proven")}}
    for did, vs in verdicts.items():
        vals = {p: (v["verdict"], v["refusal"]) for p, v in vs.items()}
        if len({v[0] for v in vals.values()}) > 1:
            changed.append({"decision_id": did, **{
                p: {"verdict": v[0], "refusal": v[1]}
                for p, v in vals.items()}})
    return {"version": VERSION, "evidence": EVIDENCE, "note": NOTE,
            "policies": per,
            "verdict_changed": len(changed),
            "verdict_changed_sample": changed[:20],
            "recomputed_checks": list(RECOMPUTED),
            "kept_as_recorded": "every other named check"}


REPLAY_SQL = """
    SELECT DISTINCT ON (coalesce(d.valuation_id::text, d.decision_id))
           d.decision_id, d.valuation_id, d.fixture, d.policy_version,
           extract(epoch FROM d.decided_at)::float8 AS decided_at,
           d.pinnacle_p, extract(epoch FROM d.pinnacle_at)::float8
               AS pinnacle_at, d.pinnacle_qualification,
           d.model_p, d.model_version,
           extract(epoch FROM d.model_at)::float8 AS model_at,
           d.executable_price, d.qty::float8 AS qty, d.fees_usd,
           d.checks, d.evidence, d.verdict, d.refusal,
           v.outcome_known, v.outcome, v.outcome_basis
      FROM derek_entry_decisions d
      LEFT JOIN external_valuations v
        ON v.id = d.valuation_id AND v.record_purpose = 'ENTRY_DECISION'
     WHERE ($1::float8 IS NULL OR d.decided_at > to_timestamp($1))
       AND ($2::float8 IS NULL OR d.decided_at <= to_timestamp($2))
     ORDER BY coalesce(d.valuation_id::text, d.decision_id),
              d.decided_at DESC, d.decision_id
     LIMIT $3
"""


async def replay(conn, *, since: float | None = None,
                 until: float | None = None, limit: int = 20000) -> dict:
    """The stored decisions (one per valuation, the latest) replayed under
    V1 and V2. Read-only; never raises."""
    try:
        if not await conn.fetchval(
                "SELECT to_regclass('derek_entry_decisions') IS NOT NULL"):
            return {"ok": False, "refusal": "DEREK_TABLES_ABSENT",
                    "evidence": EVIDENCE, "note": NOTE}
        rows = [dict(r) for r in await conn.fetch(
            REPLAY_SQL, since, until, int(limit))]
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "refusal": "REPLAY_READ_FAILED",
                "error": type(exc).__name__, "evidence": EVIDENCE,
                "note": NOTE}
    out = replay_records(rows)
    out.update(ok=True, window={"since": since, "until": until},
               recorded_policies={str(DP.policy_of_record(r)): 0
                                  for r in rows})
    for r in rows:
        k = str(DP.policy_of_record(r))
        out["recorded_policies"][k] += 1
    return out


async def _main() -> None:                                     # pragma: no cover
    import os

    import asyncpg
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        print(json.dumps(await replay(conn), indent=2, default=str))
    finally:
        await conn.close()


if __name__ == "__main__":                                     # pragma: no cover
    import asyncio
    asyncio.run(_main())
