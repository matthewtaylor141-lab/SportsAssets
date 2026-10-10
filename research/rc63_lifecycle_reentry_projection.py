"""RC6.3 lifecycle-proof lane: THE EARLIEST LAWFUL RE-ENTRY, computed with the
release-tree code itself on the SELECT-only production readbacks.

    python research/rc63_lifecycle_reentry_projection.py \
        --backend /path/to/release-tree/backend \
        --rb1 rb1.txt --rb2 rb2.txt --rb3 rb3.txt --rb4 rb4.txt

rbN.txt are the cleaned logs of research-sql runs of
research/rc63_lifecycle_proof_{1,2,3,4}.sql (gh run view <id> --log | cut -f3-).

Nothing here writes anywhere: it parses the readbacks, rebuilds each position
with bettor_paper_ledger._position_from (the POSITIONS_SQL shape), and asks the
release-tree functions -- bettor_strategy_lifecycle.metrics / rules_firing /
check_manual / automatic_transition, bettor_capital_authority.forward_verdict /
forward_paper_pnls, bettor_paper_profitability_stack.quarantine_triggers and
paper_xavier.outcome_for -- what they would decide at each instant. No
threshold is changed and no transition is performed. ASSUMPTION (stated): no
new paper position opens (none can while QUARANTINED) and no new shadow /
evaluation evidence arrives; every position of the three strategies is closed
in the readback (0 open), so none can still close into the window.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from decimal import Decimal

ACCOUNT = "paper_acct_main"
STRATEGIES = ("DEREK_ENTRY_POLICY_V2", "PINNACLE_COMPLETED_GAME_PAPER",
              "PINNACLE_EXPLORATION_PAPER")
DAY = 86400.0


def _iso(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _section(lines, head):
    i = next(k for k, l in enumerate(lines) if l.startswith(head))
    out = []
    for l in lines[i + 1:]:
        if l.startswith("== ") or l.startswith("psql exit"):
            break
        out.append(l)
    return out


def _json_rows(sec):
    return [json.loads(l.strip()) for l in sec if l.strip().startswith("[")]


def _readback_instant(lines):
    for l in lines:
        m = re.search(r" at (\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ), statement_", l)
        if m:
            return dt.datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%SZ"
                                        ).replace(tzinfo=dt.timezone.utc
                                                  ).timestamp()
    raise SystemExit("no readback instant")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", required=True)
    for k in ("rb1", "rb2", "rb3", "rb4"):
        ap.add_argument("--" + k, required=True)
    ap.add_argument("--days", type=int, default=30)
    a = ap.parse_args(argv)
    sys.path.insert(0, a.backend)
    from sportsassets import bettor_capital_authority as CA
    from sportsassets import bettor_paper_ledger as L
    from sportsassets import bettor_paper_profitability_stack as PS
    from sportsassets import bettor_strategy_lifecycle as LC
    from sportsassets.agents import paper_xavier as PX

    rb = {k: open(getattr(a, k)).read().split("\n")
          for k in ("rb1", "rb2", "rb3", "rb4")}
    as_of = _readback_instant(rb["rb2"])

    # ── the positions, in the POSITIONS_SQL shape ────────────────────
    def dec(v):
        return None if v is None else Decimal(v)
    pos = []
    for r in _json_rows(_section(rb["rb2"], "== E1")):
        (gid, slug, side, fixture, strat, bought, bg, bf, sold, sg, sf, ff,
         lf, sq, pay, outcome, ver, sat) = r
        pos.append(L._position_from(ACCOUNT, {
            "group_id": gid, "us_market_slug": slug, "holding_side": side,
            "fixture": fixture, "strategy": strat, "label": None,
            "bought": dec(bought), "buy_gross": dec(bg), "buy_fees": dec(bf),
            "sold": dec(sold), "sale_gross": dec(sg), "sale_fees": dec(sf),
            "first_fill_at": ff, "last_fill_at": lf, "settled_qty": dec(sq),
            "payout_usd": dec(pay), "settled": outcome,
            "settlement_version": ver, "settled_at": sat}))
    by = {s: [p for p in pos if LC.strategy_of(p, L.DEFAULT_STRATEGY) == s]
          for s in STRATEGIES}
    out = {"as_of": _iso(as_of), "positions": len(pos),
           "assumption": ("no new paper position, shadow outcome or "
                          "profitability evaluation after the readback"),
           "strategies": {}}

    # ── VALIDATION: reproduce the recorded automatic events ──────────
    ev = []
    for l in _section(rb["rb1"], "== A1"):
        c = [x.strip() for x in l.split("|")]
        if len(c) >= 8 and c[0].isdigit():
            ev.append(c)
    checks = []
    for c in ev:
        m = re.search(r'"realized_pnl_usd": (-?[0-9.]+)', c[7])
        if not m:
            continue
        t = dt.datetime.strptime(c[6], "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=dt.timezone.utc).timestamp()
        got = LC.metrics(by[c[1]], now=t + 0.999)["realized_pnl_usd"]
        checks.append({"event_id": int(c[0]), "strategy": c[1],
                       "rule": c[4], "at": c[6] + "Z",
                       "recorded_realized_pnl_usd": float(m.group(1)),
                       "recomputed_realized_pnl_usd": got,
                       "equal": got is not None and abs(
                           got - float(m.group(1))) < 1e-6})
    out["validation_against_recorded_events"] = checks

    # ── the profitability-stack inputs (the latest models it reads) ──
    cells = {}
    for l in _section(rb["rb1"], "== A4"):
        if "|*|*" in l and "{" in l:
            k, v = l.split("|*|*", 1)
            cells[k.strip() + "|*|*"] = json.loads(v[v.index("{"):])
    residual_model = {"cells": cells}
    exe = {}
    for l in _section(rb["rb1"], "== A5"):
        c = [x.strip() for x in l.split("|")]
        if len(c) == 6 and c[2].isdigit():
            s, style = c[0], c[1]
            exe["%s|%s" % (s, style)] = {
                "strategy": s, "style": style, "fills": int(c[2]),
                "markout_per_contract_raw": float(c[3]),
                "terminal_orders": int(c[4]),
                "fill_rate_raw": float(c[5])}
    execution_model = {"by_strategy_style": exe}
    tuples = {r[0]: r[1] for r in _json_rows(_section(rb["rb3"], "== G2"))}
    cal = {}
    for s, slug, side, p, mk, _t in _json_rows(_section(rb["rb3"], "== G1")):
        rows = [{"id": i, "outcome_basis": b, "outcome_known": k,
                 "outcome": o, "buy_intent": bi, "outcome_at": None}
                for i, (b, k, o, bi) in enumerate(tuples.get(slug) or [])]
        y = PX.outcome_for(rows, holding_side=side).get("outcome")
        yy = 1.0 if y == "WON" else 0.0 if y == "LOST" else None
        if yy is None:
            continue
        cal.setdefault(s, []).append({"p": float(p), "market": float(mk),
                                      "y": yy})
    evaluated = []
    sec = _section(rb["rb3"], "== G3")
    for s in STRATEGIES:
        if any(l.strip() == s for l in sec):
            evaluated.append(s)

    # ── the forward-economics rule's shadow half ─────────────────────
    shadow = {}
    for l in _section(rb["rb4"], "== H1"):
        c = [x.strip() for x in l.split("|")]
        if len(c) == 4 and c[1].isdigit():
            shadow[c[0]] = [float(x) for x in json.loads(c[3])]

    for s in STRATEGIES:
        ps = by[s]
        closes = sorted(LC.closed_at(p) for p in ps
                        if LC.closed_at(p) is not None)
        n_open = sum(1 for p in ps if LC.closed_at(p) is None)
        # every instant the 14-day rolling sample changes after as_of
        cand = sorted({as_of} | {c + LC.WINDOW_DAYS * DAY + 1e-6
                                 for c in closes
                                 if c + LC.WINDOW_DAYS * DAY > as_of})

        def at(t):
            m = LC.metrics(ps, now=t)
            fwd = LC.metrics(ps, now=t, window_days=3650.0,
                             since=LC.forward_since(t))
            fired = LC.rules_firing(m, current=LC.SHADOW_ONLY, forward=fwd)
            step = LC.check_manual(LC.SHADOW_ONLY, LC.REDUCED_SIZE,
                                   actor="person:projection", m=m,
                                   forward=fwd)
            first = LC.check_manual(LC.QUARANTINED, LC.SHADOW_ONLY,
                                    actor="person:projection", m=m,
                                    forward=fwd)
            blocking = [r["rule_id"] for r in fired
                        if LC.RANK[r["requires"]] > LC.RANK[LC.REDUCED_SIZE]]
            auto_sh = LC.automatic_transition(LC.SHADOW_ONLY, m, forward=fwd)
            auto_rs = LC.automatic_transition(LC.REDUCED_SIZE, m,
                                              forward=fwd)
            fired_rs = LC.rules_firing(m, current=LC.REDUCED_SIZE,
                                       forward=fwd)
            return {"at": _iso(t), "closed_in_window": m["closed_positions"],
                    "realized_pnl_usd": m["realized_pnl_usd"],
                    "max_drawdown_usd": m["max_drawdown_usd"],
                    "pnl_ci95_high": m["pnl_ci95_high"],
                    "drawdown_rate": m["drawdown_rate"],
                    "rules_firing": [r["rule_id"] for r in fired],
                    "quarantined_to_shadow_only_refusal": first,
                    "shadow_only_to_reduced_size_refusal": step,
                    "refused_by_rules": blocking,
                    "evaluator_retightens_shadow_only_to": (
                        auto_sh or {}).get("to_state"),
                    "evaluator_retightens_reduced_size_to": (
                        auto_rs or {}).get("to_state"),
                    "ledger_entry_rule_firing_refusal": (
                        CA.R_RULE_FIRING_AT_ENTRY if fired_rs else None),
                    "ledger_entry_rules_firing": [r["rule_id"]
                                                  for r in fired_rs]}
        series = [at(t) for t in cand]
        lawful = next((x for x in series
                       if x["shadow_only_to_reduced_size_refusal"] is None),
                      None)
        # durable: from there on the step stays lawful and the evaluator
        # never re-tightens REDUCED_SIZE (no new evidence assumed)
        durable = None
        for i, x in enumerate(series):
            if all(y["shadow_only_to_reduced_size_refusal"] is None
                   and y["evaluator_retightens_reduced_size_to"] is None
                   for y in series[i:]):
                durable = x
                break
        no_rule = next((x for x in series
                        if not x["ledger_entry_rules_firing"]), None)
        days = [at(as_of + d * DAY) for d in range(a.days + 1)]
        # forward economics (capital authority, fixed FORWARD_SINCE)
        fpp = CA.forward_paper_pnls(pos, s, default_strategy=L.DEFAULT_STRATEGY)
        fv = CA.forward_verdict(fpp, shadow.get(s, []))
        trig = PS.quarantine_triggers(s, calibration_rows=cal.get(s, []),
                                      residual_model=residual_model,
                                      execution_model=execution_model)
        cm = PS.calibration_metrics(cal.get(s, []))
        out["strategies"][s] = {
            "positions": len(ps), "open": n_open,
            "last_close": _iso(closes[-1]) if closes else None,
            "window_empty_from": (_iso(closes[-1] + LC.WINDOW_DAYS * DAY)
                                  if closes else None),
            "earliest_lawful_shadow_only_to_reduced_size": lawful,
            "earliest_durable_reduced_size": durable,
            "earliest_no_rule_firing_at_ledger_entry": no_rule,
            "forward_economics": {k: fv[k] for k in (
                "verdict", "why", "observations", "net_pnl_usd",
                "refusal")} | {"realized_paper": fv["realized_paper"],
                               "shadow": fv["shadow"]},
            "profitability_stack": {
                "evaluated_by_quarantine_step": s in evaluated,
                "triggers_firing_on_current_inputs": [
                    t["rule_id"] for t in trig],
                "residual_cell": cells.get("%s|*|*" % s),
                "calibration": cm,
                "execution_rows": [v for v in exe.values()
                                   if v["strategy"] == s]},
            "daily": days,
            "change_instants": series}
    print(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
