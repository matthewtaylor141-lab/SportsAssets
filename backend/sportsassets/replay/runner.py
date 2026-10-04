"""ONE BOUNDED REPLAY RUN: decisions in [start, end] -> components at each
decision clock -> outcome at the horizon -> counterfactuals -> per
independent event -> (optionally) persisted append-only.

Bounded: at most params["max_decisions"] decisions (MAX_DECISIONS hard
ceiling), a window of at most MAX_WINDOW_S, every read LIMITed by the choke
point. Read only until `store.record_run`, which writes the r30_replay_*
tables only. Nothing here changes ENTER, sizing, management, a rail, a
threshold, an order or capital.
"""
from __future__ import annotations

import time

from .. import allie_capital as AC
from .. import canonical_components as CC
from .. import canonical_intent as CI
from ..intel import common as IC
from . import counterfactuals as CF
from . import events as EV
from . import pit as P
from . import reconstruct as RC

VERSION = "R30_HISTORICAL_REPLAY_V2"
MAX_DECISIONS = 1000
MAX_WINDOW_S = 14 * 86400.0
num = IC.num
jl = IC.jload


class ReplayRefused(ValueError):
    pass


def _slim_eddie(c: dict) -> dict:
    return {k: v for k, v in c.items() if k not in ("dimensions", "inputs")}


async def replay_one(ctx: RC.RunContext, dec: dict) -> dict:
    R = ctx.R
    did = dec["decision_id"]
    c = RC.decision_clock(dec)
    sd, so = did + ":DECISION", did + ":OUTCOME"
    ev = RC.econ_view(dec)
    qual = RC.qualify(dec, ev)
    sleeve = CI.sleeve_of(dec.get("strategy"))
    econ = jl(dec.get("economics")) or {}
    vc = await RC.valuation_chain(ctx, dec, c, sd)
    vrow = vc.pop("_row", None)
    bk = await RC.venue_book(ctx, dec, c, sd)
    derek = CC.derek_component(
        verdict=dec.get("verdict"), policy_version=dec.get("policy_version"),
        policy_decision=jl(dec.get("policy_decision")),
        refusals=dec.get("refusals"),
        economics=(econ.get("acquisition") if isinstance(
            econ.get("acquisition"), dict) else {}),
        gross_edge_pp=econ.get("best_level_edge_pp"), probability=ev["p"])
    karen = await RC.karen_at(ctx, dec, c, sd)
    eddie = _slim_eddie(await RC.eddie_at(ctx, dec, c, bk["row"], sd))
    view = ctx.view(c, sd)
    hours, hours_why, start, lag, lag_n = view.hours_to_release(dec)
    st_basis = view.event_start(dec.get("us_market_slug"))[1]
    cfg = await ctx.session_config(dec.get("session_id"), c, sd)
    caps, caps_src = RC.caps_at(dec, cfg)
    orders_c = await RC.entry_orders(ctx, dec, c, sd)
    own = {o["group_id"] for o in orders_c}
    book = RC.book_state_at(ctx, dec, c, sd, own)
    ledger = await RC.ledger_at(ctx, dec, c, sd, own)
    rails = await RC.rails_at(ctx, dec, c, sd, caps=caps, source=caps_src,
                              ledger=ledger, book=book, own_groups=own)
    idle = rails.idle
    idle_why = (rails.why if idle is None else None)
    hs = RC.allie_hurdle_sample(ctx, c, sd)
    e_ok = eddie if eddie.get("status") == "MEASURED" else {}
    if book.get("status") != "MEASURED":
        # Allie reads open exposure as 0 when it is None: a missing input is
        # never handed to her as a zero
        allie = RC.un("ALLIE_INPUT_UNMEASURED: %s" % book.get("why"),
                      authority="SHADOW_PENDING_OWNER_APPROVAL")
    else:
        allie = AC.allocate(
            eddie_ev_usd=e_ok.get("expected_executable_ev_usd"),
            modelled_net_usd=ev["net"],
            capital_required_usd=ev["capital_required"],
            event_start_at=start, decided_at=float(dec["decided_at"]),
            median_lag_s=lag, lag_n=lag_n,
            eddie_max_qty=e_ok.get("max_executable_qty"),
            limit_price=dec.get("limit_price"),
            displayed_depth_qty=econ.get("depth_within_limit"),
            fixture_open_groups=book["fixture_open_groups"],
            fixture_open_usd=book["fixture_open_usd"],
            book_open_usd=book["book_open_usd"], idle_capital_usd=idle,
            recent_adjusted_ppch=hs,
            paper_rail_usd=(rails.per_order() if rails.status == "MEASURED"
                            else None),
            live_rail_usd=None, live_scale=None,
            order_cost_usd=ev["capital_required"])
    allie["replay_inputs"] = {
        "event_start_basis": st_basis, "hours_basis": hours_why,
        "settlement_lag_samples": lag_n, "exposure_basis": book.get("basis")
        or book.get("why"), "idle_capital_basis": (ledger.get("basis")
                                                   or ledger.get("why")),
        "rails": rails.status, "caps_source": caps_src,
        "hurdle_sample_basis": "canonical INVESTMENT intents recorded by "
        "the clock (Allie's own rule: selected intents)",
        "live_rail": "UNAVAILABLE: execmirror_control is rewritten in place "
                     "(no point-in-time record); informational only"}
    opp = RC.opportunity_at(dec, ev, eddie, idle, idle_why, view.lag_samples,
                            start, c)
    comps = dict(vc, venue_book=bk["component"], derek=derek, karen=karen,
                 eddie=eddie, allie=allie, opportunity_score=opp,
                 rails=rails.as_dict())
    ci = RC.canonical_intent_at(dec, ev=ev, comps=comps, cfg=cfg,
                                orders=orders_c, clock=c)
    comps["canonical_intent"] = ci
    parity = RC.compare_intents(ci, await RC.recorded_intent(ctx, dec, c,
                                                             sd))
    tape = RC.tape_at(ctx, dec, c, sd)
    hurdle = RC.hurdle_at(ctx, tape, c, sd)
    allocs = RC.allocation_benchmarks(ctx, dec, ev, qual, tape, rails=rails,
                                      eddie=eddie, allie=allie, hours=hours,
                                      hurdle=hurdle, clock=c, scope=sd)
    cpc = ev.get("capital_per_contract")
    cap_q = num(eddie.get("max_executable_qty")) \
        if eddie.get("status") == "MEASURED" else None
    v2_rails = rails.for_opportunity(
        dec.get("us_market_slug"), dec.get("fixture"),
        None if cap_q is None or cpc is None else cap_q * cpc)
    out = await RC.outcome_at_horizon(ctx, dec, so,
                                      review_fn=CF.canonical_review)
    contract, contract_why = RC.contract_settlement(
        ctx, dec.get("us_market_slug"), dec.get("holding_side"), so)
    rec = {"decision": {k: dec.get(k) for k in RC.DEC_COLS
                        if k != "provenance"},
           "clock": c, "horizon": ctx.end, "sleeve": sleeve, "ev": {
               k: v for k, v in ev.items() if k != "levels"},
           "qualification": qual, "components": comps,
           "intent_parity": parity, "valuation_row": vrow,
           "tape": {"status": tape.get("status"), "why": tape.get("why"),
                    "alternatives": tape["alternatives"],
                    "window_counts": tape.get("window_counts"),
                    "unrecorded_alternatives": tape.get(
                        "unrecorded_alternatives")},
           "alternatives_input": RC.alternatives_input(
               tape, ctx.params["alternatives_window_s"]),
           "hurdle": hurdle, "allocations": allocs, "rails": v2_rails,
           "outcome": out, "contract_settlement": contract,
           "contract_settlement_why": contract_why,
           "event_start_at": start, "event_start_basis": st_basis,
           "lag_samples": view.lag_samples, "params": ctx.params}
    evaluated = CF.evaluate(rec)
    eco = evaluated["economics"]["actual"]
    mk, mk_cut = [], False
    if evaluated["position"]["entry"]:
        mk, mk_cut = await RC.marks(ctx, evaluated["position"]["slug"],
                                    eco.get("first_fill_at"),
                                    eco.get("released_at"), so)
    equity = EV.equity_path(evaluated["position"], mk,
                            released_at=eco.get("released_at"),
                            realized=evaluated["realized_pnl_usd"],
                            marks_truncated=mk_cut)
    rec["evidence"] = {"decision": R.scope(sd), "outcome": R.scope(so)}
    return {"rec": rec, "eval": evaluated, "equity": equity}


def horizon_tape(ctx: RC.RunContext) -> dict:
    """The qualified (and the admissible-but-unrecorded) opportunities on
    the tape recorded by the horizon -- or by the last stamp a bounded tape
    covers -- for the marginal-opportunity count while capital was held."""
    rows, unrec = [], []
    snap = ctx.tape
    for r in snap.at_covered(scope="_horizon_tape"):
        if r.get("decided_at") is None:
            continue
        e = ctx.econ(r)
        q = RC.qualify(r, e)
        x = {"t": r["decided_at"], "slug": r.get("us_market_slug"),
             "sleeve": CI.sleeve_of(r.get("strategy")),
             "decision_id": r["decision_id"], "net": e["net"]}
        if q["state"] == RC.QUALIFIED:
            rows.append(x)
        elif q["state"] == RC.UNRECORDED:
            unrec.append(x)
    return {"rows": rows, "unrecorded": unrec,
            "complete_until": min(ctx.end, snap.complete_until)}


async def run(conn, *, start: float, end: float, params: dict | None = None,
              account_id: str | None = None, now: float | None = None,
              strategies=None, enter_only: bool = False) -> dict:
    """ONE REPLAY (read only). Returns {events, items, summary, meta}."""
    now = float(now if now is not None else time.time())
    start, end = float(start), float(end)
    if not start <= end:
        raise ReplayRefused("CLOCK_START_AFTER_CLOCK_END")
    if end > now + 1e-6:
        raise ReplayRefused("THE_HORIZON_IS_IN_THE_FUTURE: a replay reads "
                            "only what was recorded")
    if end - start > MAX_WINDOW_S:
        raise ReplayRefused("WINDOW_ABOVE_%d_DAYS" % (MAX_WINDOW_S / 86400))
    prm = dict(RC.DEFAULT_PARAMS, **(params or {}))
    prm["max_decisions"] = int(min(MAX_DECISIONS, prm["max_decisions"]))
    t0 = time.time()
    R = P.Reader(conn, horizon=end)
    decisions = await RC.load_decisions(R, start=start, end=end,
                                        limit=prm["max_decisions"],
                                        account_id=account_id,
                                        strategies=strategies,
                                        enter_only=enter_only)
    ctx = RC.RunContext(R, start=start, end=end, params=prm,
                        account_id=account_id)
    await ctx.prepare(decisions)
    # FAILURE ISOLATION: one decision whose records cannot be replayed is
    # named with its error and left out (nothing is invented for it); a
    # HindsightViolation is never isolated -- it stops the run
    items, failed = [], []
    for d in decisions:
        try:
            items.append(await replay_one(ctx, d))
        except P.HindsightViolation:
            raise
        except Exception as exc:                            # noqa: BLE001
            failed.append({"decision_id": d.get("decision_id"),
                           "error": "%s: %s" % (type(exc).__name__,
                                                str(exc)[:200])})
    if failed:
        ctx.notes["DECISIONS_NOT_REPLAYED"] = failed[:50]
    if len(decisions) >= prm["max_decisions"]:
        ctx.notes["DECISIONS_TRUNCATED"] = (
            "the window holds more than %d decisions; the first %d (oldest) "
            "were replayed" % (prm["max_decisions"], prm["max_decisions"]))
    built = EV.build(items, horizon=end, tape_h=horizon_tape(ctx),
                     notes=ctx.notes, params=prm)
    built["summary"]["decisions_not_replayed"] = len(failed)
    built["meta"] = {"version": VERSION, "clock_start": start,
                     "clock_end": end, "started_at": now,
                     "finished_at": max(now, now + (time.time() - t0)),
                     "elapsed_s": round(time.time() - t0, 3),
                     "reads": R.reads, "account_id": account_id,
                     "parameters": dict(prm, account_id=account_id,
                                        strategies=sorted(strategies or []),
                                        enter_only=bool(enter_only))}
    return built
