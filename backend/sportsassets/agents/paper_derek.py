"""DEREK ON LIVE MARKETS, PAPER ONLY: V2 DECISIONS, PERSISTED BEFORE EXECUTION.

In each paper pass Derek evaluates the supported Polymarket US markets the
scheduled cycle valued (`external_valuations` rows of the entry experiment,
either purpose, decided within the lookback) under DEREK_ENTRY_POLICY_V2,
through the SAME pure functions the funded gate uses: `derek_policy.
economics`, `decide_entry`, `walk`, `blend`, `instrument_label`.

THE INTERNAL PROBABILITY comes from the Derek RESEARCH model path only: the
newest CANDIDATE `bettor_funded_model` row for KEY_ENTRY_PAYOUT fitted on
DEREK_RESEARCH_OBSERVATIONS, registered before the decision instant. In the
PAPER session only it is used as a qualified input, labelled
EXPERIMENTAL_RESEARCH_MODEL with its approval status (CANDIDATE, not
approved); it is never promoted. When no such model exists every decision
refuses BY NAME (NO_RESEARCH_MODEL_CANDIDATE_EXISTS) and the existing daily
run (`derek_research.daily_model_run`) is asked, in its NON-FITTING mode: the
paper path never fits inside a decision's time bound. It reports the day's
run when there is one, records the day's first run when no cohort has
enough labelled fixtures (a non-decisive INSUFFICIENT row, which does not
close the day), and when a cohort is ready returns
FIT_DEFERRED_TO_SCHEDULED_STEP and writes nothing. The fit itself is the
scheduled step's (`derek.model_run_step`, right after the collection step,
through the run instant on outcomes available by then -- never an outcome
unavailable at decision time), and the next paper context picks the
candidate up, inside the context cache's TTL too. Nothing is invented: no
internal probability is made up, Pinnacle is never duplicated into it, and
there is no Pinnacle-only fallback.

THE BOOK, NOT THE HEADLINE. The price is the observed book the paper
market-data client read at decision time: the consumed side's levels with
their sizes (`bettor_paper_simulator.levels_for`, cent grid). The limit is the
deepest level at which the blended probability still clears the minimum
gross edge; the quantity walks the depth up to that limit, the per-order cap
and the target order size.

QUALIFICATION GAPS are recorded on every decision and never hidden: model
approval, source calibration, quote timing (P5: book currency not
established; the price's age is known only from our own read), the
execution-model assumptions (PAPER_SIM_V1), and settlement interpretation.

PERSISTED BEFORE SIMULATION: the decision row (market / side / period /
fixture label inputs, both probabilities with versions and timestamps, the
observed book and depth, the proposed quantity and limit, EV and costs,
refusal reasons, policy version and decision id) is written first; only then
is an ENTER submitted as a paper order naming that decision.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
from typing import Any

from .. import bettor_paper_guard as G
from .. import bettor_paper_ledger as L
from .. import bettor_paper_simulator as SIM
from . import derek_policy as DP

VERSION = "PAPER_DEREK_V1"
MODEL_LABEL = "EXPERIMENTAL_RESEARCH_MODEL"

R_NO_RESEARCH_MODEL = "NO_RESEARCH_MODEL_CANDIDATE_EXISTS"
R_MODEL_UNVERIFIED = "RESEARCH_MODEL_PROVENANCE_NOT_VERIFIED"
R_MODEL_CANNOT_SCORE = "RESEARCH_MODEL_CANNOT_SCORE_THIS_CANDIDATE"
R_NO_BOOK = "THE_OBSERVED_BOOK_WAS_UNREADABLE_OR_EMPTY"
R_NOT_PMUS = "NOT_A_SUPPORTED_POLYMARKET_US_CONTRACT"
R_ORDER_REFUSED = "PAPER_RISK_REFUSED_THE_ORDER"
#: THE PER-STRATEGY ENTRY SWITCH (migration 182). This strategy keeps
#: deciding and recording every valuation, but while its entry row is off
#: (or absent) a decision that would ENTER is recorded REFUSE with this
#: named reason and places no paper order: only the PINNACLE_ONLY_PAPER_
#: BENCHMARK is enabled for new paper entries. The policy decision on the
#: record still shows what the policy concluded (admitted) and the switch.
R_ENTRIES_DISABLED = "STRATEGY_ENTRIES_DISABLED"
STRATEGY = "DEREK_ENTRY_POLICY_V2"
ENTRIES_CONTROL_KEY = "PAPER_ENTRIES:%s" % STRATEGY

#: WHERE A DECISION WAS FORMED. In the cycle, at the instant the valuation
#: was written (the lane's own inputs and instant): the primary path. By the
#: paper pass, later: the backstop for a valuation the in-cycle hook missed,
#: whose Pinnacle reading is re-aged at that later instant -- a STALE refusal
#: there is labelled with this basis and its lag.
DECIDED_VIA_CYCLE = "IN_CYCLE_AT_THE_VALUATION_INSTANT"
DECIDED_VIA_PASS = "PAPER_PASS_BACKSTOP"

GAP_MODEL = "MODEL_APPROVAL"
GAP_CALIBRATION = "SOURCE_CALIBRATION"
GAP_P5 = "QUOTE_TIMING_UNCERTAINTY_P5"
GAP_EXECUTION = "EXECUTION_MODEL_ASSUMPTIONS"
GAP_SETTLEMENT = "SETTLEMENT_INTERPRETATION"

#: THIS strategy's own decisions only (migration 182): the experimental
#: PINNACLE_ONLY_PAPER_BENCHMARK (agents/paper_benchmark.py) may decide the
#: same valuation separately; its record never stands in for this one's.
#: Every row this path writes takes the column default, DEREK_ENTRY_POLICY_V2.
CANDIDATES_SQL = """
    SELECT v.* FROM external_valuations v
     WHERE v.experiment_id = $1
       AND v.decided_at > to_timestamp($2) AND v.decided_at <= to_timestamp($3)
       AND v.us_market_slug IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM paper_decisions d
                        WHERE d.session_id = $4 AND d.valuation_id = v.id
                          AND d.strategy = 'DEREK_ENTRY_POLICY_V2')
     ORDER BY v.decided_at DESC, v.id DESC
     LIMIT $5
"""


def decision_id_for(session_id: str, valuation_id) -> str:
    return "paperdec:" + hashlib.sha256(
        ("%s:%s" % (session_id, valuation_id)).encode()).hexdigest()[:24]


def group_id_for(decision_id: str) -> str:
    return "papergrp:" + decision_id.split(":", 1)[1]


def holding_side_of(intent) -> str | None:
    s = str(intent or "")
    if s == DP.LONG:
        return "LONG"
    if s == DP.SHORT:
        return "SHORT"
    return None


# ═════════════════════════════════════════════════════════════════════
# THE RESEARCH MODEL (paper only)
# ═════════════════════════════════════════════════════════════════════

async def research_model(conn, *, at: float, verify: bool = True) -> dict:
    """THE NEWEST CANDIDATE RESEARCH MODEL registered before `at`, with its
    provenance verification. Never promotes; never raises."""
    from .. import bettor_funded_model as FM
    try:
        rows = await conn.fetch(
            "SELECT * FROM bettor_funded_models WHERE model_key = $1 "
            "   AND state = $2 AND created_at <= to_timestamp($3) "
            " ORDER BY created_at DESC LIMIT 10", FM.KEY_ENTRY_PAYOUT,
            FM.STATE_CANDIDATE, float(at))
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "refusal": R_NO_RESEARCH_MODEL,
                "why": "the model registry read failed: %s"
                       % type(exc).__name__}
    for r in rows:
        m = FM._row(r)
        if FM.source_of(m) != FM.SOURCE_RESEARCH_OBSERVATIONS:
            continue
        out = {"ok": True, "refusal": None, "model": m,
               "model_id": m["model_id"], "model_version": m["model_version"],
               "approval_status": m["state"], "label": MODEL_LABEL,
               "created_at": L._epoch(m.get("created_at")),
               "features": list(m.get("features") or []),
               "promoted": False,
               "never_promoted_here": ("the paper session only uses it; "
                                       "promotion is a named person's act")}
        if verify:
            try:
                chk = await FM.verify_provenance(conn, m)
            except Exception as exc:                            # noqa: BLE001
                chk = {"ok": False, "refusal": type(exc).__name__}
            out["provenance_verified"] = bool(chk.get("ok"))
            if not chk.get("ok"):
                return dict(out, ok=False, refusal=R_MODEL_UNVERIFIED,
                            why=chk.get("refusal"))
        return out
    return {"ok": False, "refusal": R_NO_RESEARCH_MODEL,
            "why": ("no CANDIDATE %s model fitted on %s is registered yet"
                    % (FM.KEY_ENTRY_PAYOUT, FM.SOURCE_RESEARCH_OBSERVATIONS))}


def score(model: dict, *, price: float, payout_is_complement: bool) -> dict:
    from .. import bettor_funded_model as FM
    feats = {"acquisition_price": round(float(price), 9),
             "payout_is_complement": 1.0 if payout_is_complement else 0.0}
    missing = [f for f in model.get("features") or [] if f not in feats]
    if missing:
        return {"ok": False, "refusal": R_MODEL_CANNOT_SCORE,
                "missing_features": missing, "features": feats}
    try:
        p = float(FM.load(model["model"]["params"]).predict(feats))
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "refusal": R_MODEL_CANNOT_SCORE,
                "error": type(exc).__name__, "features": feats}
    if not (0.0 <= p <= 1.0) or math.isnan(p):
        return {"ok": False, "refusal": R_MODEL_CANNOT_SCORE, "p": p}
    return {"ok": True, "p": p, "features": feats,
            "feature_sha": FM.feature_sha(feats),
            "feature_basis": ("acquisition_price = the best level of the "
                              "observed book in cost space at the decision")}


async def ensure_model_attempt(conn, *, at: float) -> dict:
    """NO RESEARCH MODEL: ask today's daily run WITHOUT FITTING
    (`daily_model_run(allow_fit=False)`). It reports the day's run when one
    exists; records the day's first, non-decisive INSUFFICIENT (or
    LABELS_UNREADABLE) row when no cohort is ready; and when a cohort IS
    ready returns the named deferral FIT_DEFERRED_TO_SCHEDULED_STEP and
    writes nothing -- the fit is `derek.model_run_step`'s, under the cycle
    hook's bound, never inside this per-valuation bound. It does not wait
    for another caller's lock on the day (DAILY_RUN_IN_PROGRESS_ELSEWHERE).
    Never promotes; never raises."""
    try:
        from . import derek_research as DR
        got = await DR.daily_model_run(conn, now=at, allow_fit=False)
    except Exception as exc:                                    # noqa: BLE001
        return {"ran": False, "refusal": "MODEL_RUN_RAISED:%s"
                % type(exc).__name__}
    return {k: got.get(k) for k in ("run_day", "ran", "already_ran",
                                    "run_id", "outcome", "counts",
                                    "attempted_model_ids", "refusal",
                                    "rerun_refusal", "deferral",
                                    "ready_cohorts", "promoted")} | {
        "cutoff": ("a fit (the scheduled step's) is through its run "
                   "instant, on labelled research observations whose "
                   "outcomes were available by then")}


# ═════════════════════════════════════════════════════════════════════
# THE DECISION (pure given its inputs)
# ═════════════════════════════════════════════════════════════════════

def size_and_limit(levels: list, *, p_blended: float, threshold: float,
                   target_usd: float, cap_usd: float, fee_per_contract_max,
                   ) -> dict:
    """The deepest level whose price still clears the edge sets the limit;
    the quantity walks the depth to that limit, capped so that qty x limit
    + max fees stays within min(target, per-order cap). Whole contracts."""
    ok = [lv for lv in levels
          if DP.clears(DP.gross_edge(p_blended, lv["price"]), threshold)]
    if not ok:
        return {"qty": 0, "limit": None, "why": "NO_LEVEL_CLEARS_THE_EDGE"}
    limit = max(lv["price"] for lv in ok)
    wire = next(lv["wire"] for lv in ok if lv["price"] == limit)
    depth = sum(float(lv["qty"]) for lv in ok)
    budget = float(target_usd) if cap_usd is None else min(float(target_usd), float(cap_usd))
    per = float(limit) + float(fee_per_contract_max)
    qty = math.floor(min(depth, budget / per if per > 0 else 0.0))
    return {"qty": int(max(qty, 0)), "limit": float(limit), "wire": wire,
            "depth_within_limit": depth, "budget_usd": budget}


def qualification_gaps(*, model: dict, calibration: dict, cand: dict,
                       void: dict | None) -> list:
    gaps = [{"gap": GAP_MODEL, "status": "OPEN",
             "detail": ("%s %s is a %s (%s), not an APPROVED model; used in "
                        "the paper session only" % (
                            MODEL_LABEL, model.get("model_id"),
                            model.get("approval_status"),
                            "provenance verified"
                            if model.get("provenance_verified")
                            else "provenance not verified"))
             if model.get("model_id") else
             "no research model exists: nothing is scored"}]
    cal = dict(calibration or {})
    gaps.append({"gap": GAP_CALIBRATION,
                 "status": "MEASURED" if cal.get("measured") else "OPEN",
                 "detail": cal.get("why") or cal.get("verdict")
                 or ("source calibration measured" if cal.get("measured")
                     else "not measured")})
    gaps.append({"gap": GAP_P5, "status": "OPEN",
                 "detail": ("book currency is not established by the venue "
                            "read (P5); the price's age is bounded only by "
                            "our own read instant; the valuation row is %s"
                            % cand.get("record_purpose"))})
    gaps.append({"gap": GAP_EXECUTION, "status": "SIMULATED",
                 "detail": ("PAPER_SIM_V1: %s" % ", ".join(
                     SIM.ASSUMPTIONS["marketable"]))})
    st = dict(cand.get("settlement") or {})
    states = DP.settlement_states(void, void_refunds_price=None)
    gaps.append({"gap": GAP_SETTLEMENT,
                 "status": ("COMPATIBLE" if st.get("compatibility") ==
                            "COMPATIBLE" else "OPEN"),
                 "detail": ("settlement comparison %s; void %s"
                            % (st.get("compatibility"),
                               states.get("void_status")))})
    return gaps


def _pinnacle(cand: dict, *, at: float, max_age: float) -> dict:
    pin = dict(cand.get("pinnacle") or {})
    p, obs = pin.get("p"), pin.get("observed_at")
    ref = pin.get("reference_input") or {}
    if (pin.get("provider") == "pinnapi.com/raw-websocket"
            or ref.get("provider") == "pinnapi.com/raw-websocket"):
        from .. import pinnapi_feed_runtime as feed
        from .. import pinnapi_primary as primary
        owner = feed._STATE.get("owner")
        try:
            check = (primary.validate(
                owner.cache if owner else None, {"reference_input": ref},
                at=at, max_age_s=max_age,
                runtime_id=feed._STATE.get("runtime_id")) if ref else
                {"ok": False, "reason": "PINNAPI_PRIMARY_PROVENANCE_MISSING"})
        except (KeyError, TypeError, ValueError):
            check = {"ok": False, "reason": "PINNAPI_PRIMARY_PROVENANCE_INVALID"}
        if not check.get("ok"):
            return {"p": None, "qualified": False,
                    "refusal": check.get("reason"), "qualification": "REFUSED",
                    "at": obs, "reference_input": ref, "source_check": check}
    if p is None:
        return {"p": None, "qualified": False, "refusal": DP.R_NO_PINNACLE,
                "qualification": "ABSENT", "at": obs}
    if obs is None:
        return {"p": p, "qualified": False,
                "refusal": DP.R_FRESHNESS_UNKNOWN,
                "qualification": "UNKNOWN", "at": None,
                "why": "the Pinnacle reading carries no observed_at"}
    age = float(at) - float(obs)
    if age < 0:
        # A STAMP AFTER THE DECISION INSTANT IS A CLOCK DISAGREEMENT, NOT
        # FRESHNESS (the rule bettor_hold_value and the devig gate apply).
        return {"p": float(p), "qualified": False,
                "refusal": DP.R_FRESHNESS_UNKNOWN,
                "qualification": "CLOCKS_DISAGREE", "at": float(obs),
                "age_s": round(age, 3), "limit_s": max_age,
                "received_at": pin.get("received_at"),
                "why": ("the Pinnacle reading is stamped %.1fs AFTER the "
                        "decision instant; a future stamp is not a fresh "
                        "one" % (-age,))}
    fresh = age <= float(max_age)
    return {"p": float(p), "qualified": fresh,
            "refusal": None if fresh else DP.R_STALE,
            "qualification": "FRESH" if fresh else "STALE",
            "at": float(obs), "age_s": round(age, 3), "limit_s": max_age,
            "received_at": pin.get("received_at"),
            "overround": pin.get("overround"), "method": pin.get("method"),
            "source_version": pin.get("source_version"),
            "provider": pin.get("provider"), "reference_input": ref,
            "why": ("re-aged at the paper decision instant: %.1fs %s %.1fs"
                    % (age, "<=" if fresh else ">", max_age))}


def recheck_primary_reference(cand, pin, ctx, refusals):
    """After awaited book/economics reads, before choosing ENTER.

    Re-age and check the same source version. Never replace probability
    without recomputing economics. Legacy rows preserve existing behavior.
    """
    source = cand.get("pinnacle") or {}
    ref = source.get("reference_input") or {}
    if (source.get("provider") != "pinnapi.com/raw-websocket"
            and ref.get("provider") != "pinnapi.com/raw-websocket"):
        return
    at = float(ctx["clock"]()) if ctx.get("clock") else float(ctx["now"])
    check = _pinnacle(cand, at=at,
                      max_age=float(ctx["config"]["entry"]["pinnacle_max_age_s"]))
    pin["final_reference_check"] = check
    if check.get("refusal") and check["refusal"] not in refusals:
        refusals.append(check["refusal"])


def _alternatives(md: dict | None, *, side: str, p_blended) -> dict:
    """What else was available at decision time, on the same measure."""
    out = {"NO_TRADE": {"expected_net_usd": 0.0}}
    other = "SHORT" if side == "LONG" else "LONG"
    lv = SIM.levels_for(md, direction="BUY", holding_side=other)
    if lv["levels"] and p_blended is not None:
        best = lv["levels"][0]
        out["OPPOSITE_SIDE_SAME_MARKET"] = {
            "holding_side": other, "best_price": best["price"],
            "displayed_qty": best["qty"],
            "gross_edge_pp": DP.gross_edge(1.0 - float(p_blended),
                                           best["price"]),
            "measure": "1 - p_blended (the complement on the same measure)"}
    else:
        out["OPPOSITE_SIDE_SAME_MARKET"] = {"status": "NO_LEVELS"}
    return out


# ═════════════════════════════════════════════════════════════════════
# THE STEP
# ═════════════════════════════════════════════════════════════════════

#: The per-valuation hook reuses one context for this long (the model read
#: verifies provenance, which re-reads its training records).
CONTEXT_TTL_S = 300.0
_CONTEXT_CACHE: dict = {}


async def _a_model_appeared(conn, cached: dict, *, at: float) -> bool:
    """A cached context that found NO research model is not served once a
    candidate exists (the scheduled step may have fitted one since): one
    registry read, no provenance check -- the recomputed context verifies
    it."""
    m = cached.get("model") or {}
    if m.get("ok") or m.get("refusal") != R_NO_RESEARCH_MODEL:
        return False
    probe = await research_model(conn, at=at, verify=False)
    return bool(probe.get("ok"))


async def _context(conn, ctx: dict) -> dict:
    """Once per pass (or per CONTEXT_TTL_S for the per-valuation hook): the
    research model, its daily attempt, the void measure and the source
    calibration. A cached context without a model is recomputed as soon as
    a candidate is registered."""
    if "derek" in ctx:
        return ctx["derek"]
    key = ctx.get("context_cache_key")
    if key is not None:
        hit = _CONTEXT_CACHE.get(key)
        if hit is not None and float(ctx["now"]) - hit["at"] < \
                CONTEXT_TTL_S and float(ctx["now"]) >= hit["at"] \
                and not await _a_model_appeared(conn, hit["derek"],
                                                at=float(ctx["now"])):
            ctx["derek"] = hit["derek"]
            return ctx["derek"]
    at = ctx["now"]
    model = await research_model(conn, at=at)
    attempt = None
    if not model.get("ok") and model.get("refusal") == R_NO_RESEARCH_MODEL:
        attempt = await ensure_model_attempt(conn, at=at)
        model = await research_model(conn, at=at)
    void = await DP.void_measure(conn, through=at)
    ctx["derek"] = {"model": model, "model_attempt": attempt, "void": void,
                    "calibration": {}}
    if key is not None:
        _CONTEXT_CACHE.clear()
        _CONTEXT_CACHE[key] = {"at": float(at), "derek": ctx["derek"]}
    return ctx["derek"]


async def _calibration(conn, dctx: dict, version) -> dict:
    key = str(version)
    if key not in dctx["calibration"]:
        try:
            from ..workers import ext_pinnacle_loop as LOOP
            dctx["calibration"][key] = await LOOP.source_calibration(
                conn, version)
        except Exception as exc:                                # noqa: BLE001
            dctx["calibration"][key] = {"measured": False,
                                        "why": type(exc).__name__}
    return dctx["calibration"][key]


async def decide_one(conn, ctx: dict, row: dict) -> dict:
    """ONE PAPER DECISION, persisted; an ENTER is then submitted."""
    cfg = ctx["config"]
    ent = cfg["entry"]
    sim_cfg = cfg["simulator"]
    at = float(ctx["clock"]()) if ctx.get("clock") else float(ctx["now"])
    dctx = await _context(conn, ctx)
    model = dctx["model"]
    cand = DP.candidate_from_row(row)
    side = holding_side_of(cand.get("side"))
    did = decision_id_for(ctx["session_id"], cand["valuation_id"])
    refusals: list = []
    cat = await DP.catalogue_row(conn, cand.get("us_market_slug"))
    fxr = await DP.fixture_row(conn, cand.get("condition_id"))
    label = DP.instrument_label(cand, catalogue_row=cat, fixture_row=fxr)
    if cand.get("venue") not in (None, "PMUS"):
        refusals.append(R_NOT_PMUS)
    missing = [k for k in ("us_market_slug", "payout_event", "fixture")
               if not cand.get(k)]
    if missing or side is None:
        refusals.append(DP.R_IDENTITY)
    if cand["settlement"].get("compatibility") == "INCOMPATIBLE":
        refusals.append(DP.R_SETTLEMENT)
    pin = _pinnacle(cand, at=at, max_age=float(ent["pinnacle_max_age_s"]))
    # WHERE AND WHEN THIS DECISION WAS FORMED (audits the freshness rule):
    # at the valuation instant inside the cycle, or later by the pass.
    pin["decided_via"] = ctx.get("decided_via") or DECIDED_VIA_PASS
    pin["decided_at"] = at
    pin["valuation_decided_at"] = cand.get("decided_at")
    pin["decision_lag_after_valuation_s"] = (
        None if cand.get("decided_at") is None
        else round(at - float(cand["decided_at"]), 3))
    if pin.get("refusal"):
        refusals.append(pin["refusal"])
    if not model.get("ok"):
        refusals.append(model.get("refusal") or R_NO_RESEARCH_MODEL)
    obs, md, levels, internal = None, None, [], None
    if not refusals:
        # THE BOOK IS READ ONLY FOR A CANDIDATE THAT COULD STILL ENTER.
        if ctx["books_read"] >= int(cfg["cadence"]["max_book_reads_per_pass"]):
            return {"deferred": True, "why": "BOOK_READ_BUDGET"}
        got = await read_book_within_deadline(ctx, cand["us_market_slug"])
        ctx["books_read"] += 1
        obs = await SIM.record_book(conn, slug=cand["us_market_slug"],
                                    read=got, source="PAPER_MARKET_DATA_"
                                    "CLIENT", read_basis="DEREK_DECISION")
        md = obs.get("market_data")
        lv = SIM.levels_for(md, direction="BUY", holding_side=side)
        levels = lv["levels"]
        if book_deadline_refusal(got):
            refusals.append(R_BOOK_DEADLINE)
        elif obs.get("error") or not levels:
            refusals.append(R_NO_BOOK)
        else:
            internal = score(model, price=levels[0]["price"],
                             payout_is_complement=bool(
                                 cand.get("payout_is_complement")))
            if not internal.get("ok"):
                refusals.append(internal["refusal"])
    # ── THE V2 COMBINATION, on the observed book ─────────────────────
    params = {"min_gross_edge_pp": float(ent["min_gross_edge_pp"]),
              "min_net_ev_usd": float(ent["min_net_ev_usd"])}
    pd, econ, sized = None, None, {"qty": 0, "limit": None}
    p_int = (internal or {}).get("p")
    p_blend = DP.blend(p_int, pin.get("p")) if p_int is not None else None
    fee_fn = ctx.get("fee_fn")
    if levels and p_blend is not None:
        maxfee1 = float(L.max_fee_for(1, 0.5, at=at, fee_fn=fee_fn))
        sized = size_and_limit(
            levels, p_blended=p_blend,
            threshold=params["min_gross_edge_pp"],
            target_usd=float(ent["target_order_usd"]),
            cap_usd=cfg["risk"].get("per_order_cap_usd"),
            fee_per_contract_max=maxfee1)
        walk_qty = sized["qty"] if sized["qty"] >= 1 else min(
            1.0, float(levels[0]["qty"]))
        fills = DP.walk([{"price": x["price"], "qty": x["qty"]}
                         for x in levels], walk_qty)

        fee_basis = ("bettor_funded_book.fee_for (deployed schedule)"
                     if fee_fn is None else "the session's injected fee fn")
        econ = DP.economics(p_pinnacle=pin.get("p"), p_model=p_int,
                            fills=fills, fee_fn=lambda q, px: (
                                float(L._fee(fee_fn, q, px, at)), fee_basis),
                            at=at, params=params, policy=DP.POLICY_V2)
        pd = DP.decide_entry(
            DP.POLICY_V2,
            internal={"p": p_int, "qualified": True, "refusal": None,
                      "model_id": model.get("model_id"),
                      "model_version": model.get("model_version"), "at": at},
            pinnacle={"p": pin.get("p"), "qualified": pin["qualified"],
                      "qualification": pin["qualification"],
                      "refusal": pin.get("refusal"), "why": pin.get("why"),
                      "at": pin.get("at")},
            econ=econ, params=params, void=dctx["void"],
            void_refunds_price=(None if cand["settlement"].get(
                "compatibility") is None else cand["settlement"].get(
                "compatibility") == "COMPATIBLE"),
            policy_version=DP.POLICY_V2)
        pd["instrument"] = label
        refusals.extend(r for r in pd["refusals"] if r not in refusals)
        if not pd["refusals"] and sized["qty"] < 1:
            refusals.append(DP.R_NO_QTY)
    recheck_primary_reference(cand, pin, ctx, refusals)
    verdict = DP.ENTER if not refusals else DP.REFUSE
    would_enter = verdict == DP.ENTER
    if would_enter:
        switch = await entries_switch(conn)
        if pd is not None:
            pd["entries_switch"] = switch
        if not switch["enabled"]:
            refusals.append(R_ENTRIES_DISABLED)
            verdict = DP.REFUSE
    cal = await _calibration(conn, dctx, cand["pinnacle"].get(
        "source_version"))
    gaps = qualification_gaps(model=model, calibration=cal, cand=cand,
                              void=dctx["void"])
    optimistic = (SIM.optimistic_fill(md, direction="BUY", holding_side=side,
                                      qty=sized["qty"], limit=sized["limit"])
                  if would_enter else None)
    internal_rec = {"p": p_int, "label": MODEL_LABEL,
                    "model_id": model.get("model_id"),
                    "model_version": model.get("model_version"),
                    "approval_status": model.get("approval_status"),
                    "provenance_verified": model.get("provenance_verified"),
                    "created_at": model.get("created_at"),
                    "at": at if p_int is not None else None,
                    "refusal": (None if p_int is not None else
                                (internal or {}).get("refusal")
                                or model.get("refusal")),
                    "why": model.get("why"),
                    "features": (internal or {}).get("features"),
                    "feature_basis": (internal or {}).get("feature_basis"),
                    "model_attempt": dctx.get("model_attempt"),
                    "promoted": False}
    book = None if obs is None else {
        "book_obs_id": obs["obs_id"], "observed_at": obs["observed_at"],
        "error": obs.get("error"), "side_consumed": SIM.side_consumed(
            "BUY", side or "LONG"),
        "levels": levels[:10], "depth_levels": len(levels),
        "displayed_depth": round(sum(float(x["qty"]) for x in levels), 6),
        "basis": "OBSERVED_BOOK_LEVELS_NOT_THE_HEADLINE_PRICE"}
    rec = {"decision_id": did, "verdict": verdict,
           "refusal": refusals[0] if refusals else None,
           "refusals": refusals}
    alts = _alternatives(md, side=side or "LONG", p_blended=p_blend)
    # THE LEARNING RECORD (migration 185): versions, the inputs as read with
    # their SHA-256, prices, fees, alternatives and a plain explanation, in
    # the same INSERT. Building it never stops the decision being recorded.
    provenance = decision_provenance(
        strategy=STRATEGY, code_version=VERSION, policy_version=DP.POLICY_V2,
        row=row, session=ctx.get("session"), verdict=verdict,
        refusals=refusals, policy_decision=pd, internal_model=internal_rec,
        pinnacle=pin, book=book, limit_price=sized.get("limit"),
        qty=sized.get("qty"), alternatives=alts, optimistic=optimistic,
        simulator_version=cfg["simulator_version"],
        decided_via=pin.get("decided_via"), at=at)
    inserted = await conn.fetchval(
        "INSERT INTO paper_decisions (decision_id, session_id, account_id, "
        " decided_at, valuation_id, us_market_slug, holding_side, intent, "
        " fixture, label, verdict, refusal, refusals, p_internal, "
        " internal_model, p_pinnacle, pinnacle, p_blended, book_obs_id, "
        " book, proposed_qty, limit_price, economics, qualification_gaps, "
        " policy_version, policy_decision, alternatives, optimistic, "
        " simulator_version, provenance) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,"
        " $10::jsonb,$11,$12,$13,$14,$15::jsonb,$16,$17::jsonb,$18,$19,"
        " $20::jsonb,$21,$22,$23::jsonb,$24::jsonb,$25,$26::jsonb,$27::jsonb,"
        " $28::jsonb,$29,$30::jsonb)"
        " ON CONFLICT DO NOTHING RETURNING decision_id",
        did, ctx["session_id"], ctx["account_id"], L._ts(at),
        cand["valuation_id"], cand.get("us_market_slug"), side,
        cand.get("side"), cand.get("fixture"),
        json.dumps(label, default=str), verdict, rec["refusal"], refusals,
        p_int, json.dumps(internal_rec, default=str), pin.get("p"),
        json.dumps(pin, default=str), p_blend,
        None if obs is None else obs["obs_id"],
        None if book is None else json.dumps(book, default=str),
        (None if not sized.get("qty") else L.D(sized["qty"])),
        (None if sized.get("limit") is None else L.D(sized["limit"])),
        None if econ is None else json.dumps(econ, default=str),
        json.dumps(gaps, default=str), DP.POLICY_V2,
        None if pd is None else json.dumps(pd, default=str),
        json.dumps(alts, default=str),
        None if optimistic is None else json.dumps(optimistic, default=str),
        cfg["simulator_version"], json.dumps(provenance, default=str))
    if inserted is None:
        # ANOTHER WRITER (the in-cycle hook or a racing pass) RECORDED THIS
        # VALUATION'S DECISION FIRST. Its record stands; nothing is sent on
        # this computation.
        return dict(rec, duplicate=True)
    if verdict != DP.ENTER:
        return rec
    # ── ONLY NOW, THE PAPER ORDER ─────────────────────────────────────
    delay = float(sim_cfg["decision_to_execution_delay_s"])
    order = {"idempotency_key": "%s:ENTRY" % did,
             "account_id": ctx["account_id"],
             "session_id": ctx["session_id"],
             "group_id": group_id_for(did), "role": "ENTRY",
             "direction": "BUY", "holding_side": side,
             "intent": cand.get("side"),
             "us_market_slug": cand["us_market_slug"],
             "fixture": cand.get("fixture"), "label": label,
             "order_type": ent["order_type"],
             "time_in_force": ent["time_in_force"],
             "allow_partial": bool(ent["allow_partial"]),
             "qty": sized["qty"], "limit_price": sized["limit"],
             "wire_price": sized["wire"], "decision_id": did,
             "decided_at": at, "eligible_at": at + delay,
             "expires_at": at + float(sim_cfg["marketable_ttl_s"]),
             "simulator_version": cfg["simulator_version"]}
    got = await L.submit_order(conn, order, caps=cfg["risk"],
                               fee_fn=fee_fn, now=at)
    rec["order"] = {k: got.get(k) for k in ("ok", "refusal", "duplicate")}
    if got.get("ok"):
        rec["order_id"] = got["order"]["order_id"]
        rec["eligible_at"] = at + delay
    else:
        rec["order_refusal"] = got.get("refusal")
        await _finding(conn, ctx, kind=R_ORDER_REFUSED, subject=did,
                       detail={"refusal": got.get("refusal"),
                               "decision_id": did, "at": at,
                               **{k: v for k, v in got.items()
                                  if k not in ("ok",)}})
    return rec


def decision_provenance(**kw) -> dict:
    """THE LEARNING RECORD OF ONE PAPER DECISION (migration 185), for this
    strategy and the benchmark policies alike: built by
    `paper_learning.safe_provenance` (loaded only when a decision is made).
    Never raises -- a failure is recorded on the decision instead."""
    try:
        from . import paper_learning as PLRN
    except Exception as exc:                                    # noqa: BLE001
        return {"error": "PAPER_LEARNING_UNAVAILABLE: %s"
                % type(exc).__name__}
    result = PLRN.safe_provenance(**kw)
    session = kw.get("session") or {}
    if session.get("capital_policy"):
        result["capital_policy"] = session["capital_policy"]
    return result


async def entries_switch(conn) -> dict:
    """This strategy's entry switch: its paper_control row, enabled. Absent
    or unreadable = OFF. Never raises."""
    try:
        row = await conn.fetchrow(
            "SELECT enabled, why, updated_by FROM paper_control "
            " WHERE control_key = $1", ENTRIES_CONTROL_KEY)
    except Exception as exc:                                    # noqa: BLE001
        return {"control_key": ENTRIES_CONTROL_KEY, "enabled": False,
                "why": "unreadable: %s" % type(exc).__name__}
    if row is None:
        return {"control_key": ENTRIES_CONTROL_KEY, "enabled": False,
                "why": "THE_ENTRY_SWITCH_ROW_IS_ABSENT"}
    return {"control_key": ENTRIES_CONTROL_KEY,
            "enabled": bool(row["enabled"]), "why": row["why"],
            "updated_by": row["updated_by"]}


async def _finding(conn, ctx, *, kind: str, subject: str, detail: dict,
                   severity: str = "INFO") -> None:
    fid = "paperfind:" + hashlib.sha256(
        ("%s:%s:%s" % (ctx["session_id"], kind, subject)).encode()
    ).hexdigest()[:24]
    await conn.execute(
        "INSERT INTO paper_audrey_findings (finding_id, session_id, "
        " account_id, found_at, kind, severity, subject, detail) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb) ON CONFLICT DO NOTHING",
        fid, ctx["session_id"], ctx["account_id"], L._ts(ctx["now"]), kind,
        severity, subject, json.dumps(detail, default=str))


async def step(conn, ctx: dict) -> dict:
    """DEREK'S PAPER STEP: this cycle's valuations, freshest first, bounded
    by the pass budget, the decision cap and the book-read cap."""
    from .. import bettor_external_shadow as ext
    cfg = ctx["config"]
    at = float(ctx["now"])
    out: dict[str, Any] = {"decisions_recorded": 0, "orders_submitted": 0,
                           "verdicts": {}, "refusals": {}, "deferred": 0}
    try:
        exists = await conn.fetchval(
            "SELECT to_regclass('external_valuations') IS NOT NULL")
    except Exception:                                           # noqa: BLE001
        exists = False
    if not exists:
        return dict(out, refusal="EXTERNAL_VALUATIONS_ABSENT")
    rows = [dict(r) for r in await conn.fetch(
        CANDIDATES_SQL, ext.EXPERIMENT_ID,
        at - float(cfg["entry"]["valuation_lookback_s"]), at + 1.0,
        ctx["session_id"], int(cfg["cadence"]["max_decisions_per_pass"]))]
    out["candidates"] = len(rows)
    dctx = await _context(conn, ctx)
    out["model"] = {k: dctx["model"].get(k) for k in (
        "ok", "refusal", "model_id", "approval_status", "label",
        "provenance_verified")}
    out["model_attempt"] = dctx.get("model_attempt")
    ctx.setdefault("pending_entries", [])
    for row in rows:
        if time.monotonic() > ctx["deadline"]:
            out["budget_exhausted"] = True
            break
        try:
            rec = await decide_one(conn, ctx, row)
        except Exception as exc:                                # noqa: BLE001
            k = "DECISION:%s" % type(exc).__name__
            out.setdefault("errors", {})[k] = str(exc)[:200]
            continue
        if rec.get("deferred"):
            out["deferred"] += 1
            continue
        if rec.get("duplicate"):
            out["already_recorded"] = out.get("already_recorded", 0) + 1
            continue
        out["decisions_recorded"] += 1
        out["verdicts"][rec["verdict"]] = out["verdicts"].get(
            rec["verdict"], 0) + 1
        if rec.get("refusal"):
            out["refusals"][rec["refusal"]] = out["refusals"].get(
                rec["refusal"], 0) + 1
        if rec.get("order_id"):
            out["orders_submitted"] += 1
            ctx["pending_entries"].append(rec)
    return out


async def step_after_delay(conn, ctx: dict) -> dict:
    """THE DELAY, HONOURED: wait (within the pass budget) until the new
    entries become eligible, observe their books again, and simulate. An
    entry whose eligible book is not observed this pass stays pending for
    the next one, or expires with no fill.

    R30A INCIDENT REPAIR. Two ways this step manufactured an unusable or a
    harmful observation are closed:
      * the read is made with `not_before` = the entry's eligible instant,
        so the 6 s shared-read cache can no longer answer it with the
        decision's own PRE-eligible receipt (recorded outside the order's
        window, the read was wasted -- 22 of the 60 filled entries in 7 d,
        research-sql run 37233864395, E2);
      * the step no longer reads when the pass has no time left to finish a
        read after the wait: it used to sleep to the eligible instant and
        then read with nothing left on the pass deadline, recording an
        ERRORED observation (PAPER_BOOK_READ_DEADLINE_EXCEEDED /
        VENUE_COOLDOWN_EXCEEDS_THE_DECISION_DEADLINE) INSIDE the order's
        window -- 99 such reads in 7 d (E3), the commonest first observation
        of the 100 entry orders that then expired unread. Such an entry now
        stays pending for the entry-fill read (`paper_runtime.
        schedule_entry_fill`) or the next pass's books step, within its
        unchanged TTL."""
    pend = list(ctx.get("pending_entries") or [])
    known = {p["order_id"] for p in pend}
    # ...and every open marketable order still waiting for a book observed
    # at or after its eligible instant (e.g. one the in-cycle hook submitted).
    for r in await conn.fetch(
            "SELECT o.order_id, o.eligible_at FROM paper_orders o "
            " WHERE o.account_id=$1 AND o.order_type='MARKETABLE' "
            "   AND o.state='PENDING_SIMULATION' AND NOT EXISTS (SELECT 1 "
            "   FROM paper_book_observations b WHERE b.us_market_slug = "
            "   o.us_market_slug AND b.observed_at >= o.eligible_at "
            "   AND b.error IS NULL)",
            ctx["account_id"]):
        if r["order_id"] not in known:
            pend.append({"order_id": r["order_id"],
                         "eligible_at": L._epoch(r["eligible_at"])})
    if not pend:
        return {"pending": 0}
    clock = ctx.get("clock") or (lambda: float(ctx["now"]))
    now0 = float(clock())
    elig = {p["order_id"]: float(p["eligible_at"]) for p in pend}
    wait = max(elig.values()) - now0
    left = ctx["deadline"] - time.monotonic()
    sleep = ctx.get("sleep") or asyncio.sleep
    room = left - BOOK_READ_RESERVE_S - AFTER_DELAY_MIN_READ_S
    waited = 0.0
    if 0 < wait < room:
        await sleep(wait)
        waited = wait
    # In production the clock has advanced by the wait already; with a
    # fixed test clock the wait is added explicitly.
    eff_now = max(float(clock()), now0 + waited)
    slug_of = {r["order_id"]: r["us_market_slug"] for r in await conn.fetch(
        "SELECT order_id, us_market_slug FROM paper_orders "
        " WHERE order_id = ANY($1)", [p["order_id"] for p in pend])}
    not_before: dict = {}
    for oid, e in elig.items():
        slug = slug_of.get(oid)
        if slug is not None and e <= eff_now + 1e-6:
            not_before[slug] = max(e, not_before.get(slug, 0.0))
    deferred = sum(1 for oid, e in elig.items() if e > eff_now + 1e-6)
    no_time = (ctx["deadline"] - time.monotonic()) <= (
        BOOK_READ_RESERVE_S + AFTER_DELAY_MIN_READ_S)
    got = {"read": 0, "obs": {}}
    if not_before and not no_time:
        from .paper_runtime import read_books
        got = await read_books(conn, ctx, sorted(not_before),
                               basis="ENTRY_AFTER_DELAY",
                               not_before=not_before)
    elif not_before:
        deferred += len(not_before)
    # A fill needs a READABLE book observed at or after the order's
    # eligible instant (the simulator's rule).
    sim_now = max([eff_now] + [float(o["observed_at"])
                               for o in got["obs"].values()])
    res = []
    for p in pend:
        r = await SIM.simulate_order(conn, p["order_id"], now=sim_now,
                                     fee_fn=ctx.get("fee_fn"))
        if r.get("first_fill"):
            ctx["first_fills"].append(p["order_id"])
        ctx["fills"] += sum(1 for x in r.get("fills") or []
                            if x.get("ok") and not x.get("duplicate"))
        res.append({k: r.get(k) for k in ("order_id", "state", "filled_qty",
                                          "refusal", "pending")})
    return {"pending": len(pend), "books": got["read"], "results": res,
            "deferred_past_the_pass_deadline": deferred}



# ═════════════════════════════════════════════════════════════════════
# THE BOOK READ, INSIDE THE DECISION'S OWN DEADLINE
# ═════════════════════════════════════════════════════════════════════
#
# A decision that runs out of time must still RECORD a refusal. The book read
# therefore stops this much before the decision's deadline, leaving room to
# persist the decision; a read that cannot finish in time returns a named
# refusal (PAPER_BOOK_READ_DEADLINE_EXCEEDED, or the venue request gate's own
# refusal, which it raises rather than queue past the deadline).

BOOK_READ_RESERVE_S = 1.5
#: R30A: the least time a book read is given; step_after_delay does not
#: start one with less left on the pass deadline (it would only record an
#: errored observation inside the order's window).
AFTER_DELAY_MIN_READ_S = 1.0
R_BOOK_DEADLINE = "BOOK_READ_DID_NOT_FINISH_INSIDE_THE_DECISION_DEADLINE"


async def read_book_within_deadline(ctx: dict, slug: str, *,
                                    not_before_epoch=None) -> dict:
    """`not_before_epoch` (R30A): the read must be RECEIVED at or after this
    instant (a pending entry's eligible instant) -- the shared-read cache may
    not answer with an older receipt. Passed only when given, so every
    existing caller and test stand-in is called exactly as before."""
    md = ctx["market_data"]
    dl = ctx.get("deadline")
    nb = ({} if not_before_epoch is None
          else {"not_before_epoch": float(not_before_epoch)})
    if dl is None:
        try:
            return await md.read_book(slug, **nb)
        except TypeError:
            return await md.read_book(slug)
    remaining = float(dl) - time.monotonic() - BOOK_READ_RESERVE_S
    if remaining <= 0.0:
        return {"marketData": None, "error": G.R_BOOK_READ_DEADLINE,
                "observed_at": time.time(), "timeout_s": round(remaining, 3),
                "why": "no time left inside the decision deadline"}
    try:
        return await md.read_book(slug, deadline_epoch_s=time.time()
                                  + remaining, timeout_s=remaining, **nb)
    except TypeError:
        # a market-data client without deadline support (a test stand-in)
        return await md.read_book(slug)


def book_deadline_refusal(got: dict) -> bool:
    """The read was cut by the decision deadline: our own timeout, or the
    venue request gate refusing to outlive the deadline it was given."""
    g = got or {}
    return (g.get("error") == G.R_BOOK_READ_DEADLINE
            or g.get("refused_by") == "OUR_REQUEST_GATE")
