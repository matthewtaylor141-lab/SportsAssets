"""THE COMPONENTS THAT ALREADY EXIST, READ FROM THEIR OWN RECORDED ROWS.
Pure; no I/O.

  regime          sportsassets/intel/regime.py -> intel_regime_states
  attribution     sportsassets/intel/attribution.py -> intel_attribution
                  (model edge vs execution vs fees vs management vs
                  settlement vs outcome variance, reconciled to cash)
  counterfactual  sportsassets/profitability/economics.py (book
                  COUNTERFACTUAL, HOLD_TO_SETTLEMENT) and the twin
                  (sportsassets/twin/engine.py -> twin_scenario_results).
                  REALIZED and COUNTERFACTUAL are separate keys; the
                  difference is labelled and never added to realized.
"""
from __future__ import annotations

from ..intel import attribution as IA
from . import common as C


def regime(inputs, *, now):
    rows = inputs.get("regime")
    if rows is None:
        return C.unread(["regime"], inputs, audit=C.EXISTS,
                        sources=["intel_regime_states"])
    if not rows:
        return C.section(C.EMPTY, "NO_REGIME_RUN_RECORDED", audit=C.EXISTS,
                         sources=["intel_regime_states"],
                         implemented_by="sportsassets/intel/regime.py")
    r = rows[0]
    sig = C.jload(r.get("signals")) or []
    return C.section(C.OK, None, audit=C.EXISTS,
                     sources=["intel_regime_states"],
                     implemented_by="sportsassets/intel/regime.py",
                     data={"recommendation": r.get("recommendation"),
                           "reasons": C.jload(r.get("reasons")),
                           "signals": sig if isinstance(sig, list) else sig,
                           "applied": r.get("applied"),
                           "computed_at": r.get("computed_at"),
                           "age_s": C.rnd(now - r["computed_at"], 1)
                           if r.get("computed_at") else None})


def attribution(inputs, *, now):
    rows = inputs.get("attribution")
    if rows is None:
        return C.unread(["attribution"], inputs, audit=C.EXISTS,
                        sources=["intel_attribution"])
    if not rows:
        return C.section(C.EMPTY, "NO_ATTRIBUTION_ROW_FOR_THIS_ACCOUNT",
                         audit=C.EXISTS, sources=["intel_attribution"],
                         implemented_by="sportsassets/intel/attribution.py")
    by: dict = {}
    for r in rows:
        by.setdefault(str(r.get("strategy") or "UNKNOWN"), []).append(r)
    return C.section(C.OK, None, audit=C.EXISTS,
                     sources=["intel_attribution"],
                     implemented_by="sportsassets/intel/attribution.py",
                     data={"totals": IA.summarize(rows),
                           "by_strategy": {s: IA.summarize(rs)["PAPER"]
                                           for s, rs in sorted(by.items())},
                           "identity": "model + execution + management + "
                                       "settlement + variance = realized "
                                       "P&L (fees inside execution)"})


def counterfactual(inputs, *, now):
    pos = inputs.get("positions")
    tw = inputs.get("twin")
    if pos is None and tw is None:
        return C.unread(["positions", "twin"], inputs, audit=C.EXISTS)
    data = {"label": "COUNTERFACTUAL -- never added to realized",
            "summed_with_realized": False}
    if pos is None:
        data["hold_to_settlement"] = {"status": C.UNAVAILABLE,
                                      "why": C.R_INPUT_NOT_READ}
    else:
        cf = [p for p in pos if p.get("book") == "COUNTERFACTUAL"]
        basis = {p["position_key"]: p for p in pos
                 if p.get("book") == "PAPER"}
        pairs = [(basis.get(c.get("basis_position_key")), c) for c in cf]
        pairs = [(b, c) for b, c in pairs if b is not None
                 and C.num(b.get("net_profit_usd")) is not None
                 and C.num(c.get("net_profit_usd")) is not None]
        data["hold_to_settlement"] = {
            "status": C.OK if cf else C.EMPTY,
            "why": None if cf else "NO_COUNTERFACTUAL_ROW",
            "counterfactual_positions": len(cf), "paired_with_realized":
                len(pairs),
            "REALIZED": {"net_usd": C.rnd(sum(
                float(b["net_profit_usd"]) for b, _ in pairs))},
            "COUNTERFACTUAL": {"net_usd": C.rnd(sum(
                float(c["net_profit_usd"]) for _, c in pairs))},
            "counterfactual_minus_realized_usd": C.rnd(sum(
                float(c["net_profit_usd"]) - float(b["net_profit_usd"])
                for b, c in pairs)) if pairs else None,
            "difference_label": "what holding to settlement would have "
                                "made versus what the exits made; a "
                                "counterfactual, never realized"}
    if tw is None:
        data["twin"] = {"status": C.UNAVAILABLE, "why": C.R_INPUT_NOT_READ}
    else:
        st: dict = {}
        for r in tw:
            st[r.get("status")] = st.get(r.get("status"), 0) + 1
        data["twin"] = {"status": C.OK if tw else C.EMPTY,
                        "why": None if tw else "NO_TWIN_RUN_RECORDED",
                        "scenarios": len(tw), "by_status": st,
                        "computed_at": max((r["computed_at"] for r in tw
                                            if r.get("computed_at")),
                                           default=None),
                        "research_only": all(r.get("research_only")
                                             for r in tw) if tw else None,
                        "results": [{k: r.get(k) for k in (
                            "scenario_id", "status", "unavailable_reason",
                            "basis_book")} for r in tw[:20]]}
    ok = any((data.get(k) or {}).get("status") == C.OK
             for k in ("hold_to_settlement", "twin"))
    return C.section(C.OK if ok else C.EMPTY,
                     None if ok else "NO_COUNTERFACTUAL_OR_TWIN_ROW",
                     audit=C.EXISTS, data=data,
                     sources=["pos_economics_latest", "twin_scenario_results"],
                     implemented_by="sportsassets/profitability/economics.py;"
                                    " sportsassets/twin/engine.py")
