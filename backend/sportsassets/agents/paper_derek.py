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
import itertools
import json
import math
import time
from typing import Any

from .. import bettor_paper_guard as G
from .. import bettor_paper_ledger as L
from .. import bettor_paper_simulator as SIM
from .. import gross_edge_inputs as GEI
from . import derek_policy as DP

VERSION = "PAPER_DEREK_V1"
MODEL_LABEL = "EXPERIMENTAL_RESEARCH_MODEL"

R_NO_RESEARCH_MODEL = "NO_RESEARCH_MODEL_CANDIDATE_EXISTS"
R_MODEL_UNVERIFIED = "RESEARCH_MODEL_PROVENANCE_NOT_VERIFIED"
#: (RC6) why a model is unverified when the API's CPU lane cancelled the
#: provenance check's job before it ran (the lane was shut down): the check
#: never ran, so the model is refused, by name, and research_model keeps its
#: never-raises contract (a CancelledError is not an Exception).
R_PROVENANCE_CHECK_NOT_RUN = "THE_PROVENANCE_CHECK_DID_NOT_RUN_THE_CPU_LANE_STOPPED"
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
#: A valuation the in-cycle hook SKIPPED AS SUPERSEDED for this strategy
#: (paper_runtime.R_SUPERSEDED) is not re-decided by the backstop either.
CANDIDATES_SQL = """
    SELECT v.* FROM external_valuations v
     WHERE v.experiment_id = $1
       AND v.decided_at > to_timestamp($2) AND v.decided_at <= to_timestamp($3)
       AND v.us_market_slug IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM paper_decisions d
                        WHERE d.session_id = $4 AND d.valuation_id = v.id
                          AND d.strategy = 'DEREK_ENTRY_POLICY_V2')
       AND NOT EXISTS (SELECT 1 FROM paper_hook_failures h
                        WHERE h.session_id = $4 AND h.valuation_id = v.id
                          AND h.strategy = 'DEREK_ENTRY_POLICY_V2'
                          AND h.error IN ('PINNAPI_PRIMARY_VALUATION_SUPERSEDED_BY_A_NEWER_QUOTE',
                                          'PINNAPI_VALUATION_PRICED_BY_A_PREVIOUS_FEED_RUNTIME'))
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

#: (RC6) THE REGISTRY READ LEAVES THE TRAINING RECORDS IN THE REGISTRY.
#:
#: WHAT PRODUCTION SHOWED. research-sql rc6_api-responsive_workload_sizes.sql
#: (2026-10-09 02:0xZ): the newest research model names 28,303 training
#: decisions and its training_provenance is 16,095,248 characters, of which
#: the stored `records` are 12,395,003. `SELECT *` brought it -- and up to
#: nine older candidates -- to the API, and `_row` json-parsed the whole
#: member on the event loop, at C speed, holding the interpreter lock: four
#: of twenty API loop stalls of 2.3-2.5 s on 2026-10-09 were this context
#: (paper_derek._context -> research_model -> verify_provenance). Nothing on
#: this path reads the stored records except the mismatch diagnostic, which
#: now reads them from the registry on that path alone
#: (bettor_funded_model.verify_provenance). Every other member is read as
#: before; the decision ids come as a text array in their stored order.
#: verify_provenance still re-reads EVERY named record and re-hashes it --
#: the check is unchanged, only the copy of the records it compares against
#: is not shipped when it is not needed.
REGISTRY_COLUMNS = (
    "model_id", "model_key", "model_version", "state", "kernel", "estimator",
    "features", "params", "fit_through", "train_rows", "train_base_rate",
    "evaluation", "approved_at", "approved_by", "retired_at",
    "retired_reason", "superseded_by", "created_at", "trained_through",
    "outcomes_available_through")
REGISTRY_SQL = (
    "SELECT " + ", ".join(REGISTRY_COLUMNS) + ", "
    "       CASE WHEN jsonb_typeof(training_provenance) = 'object' "
    "            THEN training_provenance - 'records' - 'decision_ids' "
    "            ELSE training_provenance END AS _provenance_slim, "
    "       jsonb_typeof(training_provenance -> 'decision_ids') "
    "         AS _provenance_ids_type, "
    "       CASE WHEN jsonb_typeof(training_provenance -> 'decision_ids') "
    "                 = 'array' "
    "            THEN ARRAY(SELECT e FROM jsonb_array_elements_text("
    "                 training_provenance -> 'decision_ids') "
    "                 WITH ORDINALITY AS t(e, n) ORDER BY n) END "
    "         AS _provenance_ids, "
    "       CASE WHEN jsonb_typeof(training_provenance -> 'decision_ids') "
    "                 NOT IN ('array') "
    "            THEN training_provenance -> 'decision_ids' END "
    "         AS _provenance_ids_raw "
    "  FROM bettor_funded_models WHERE model_key = $1 "
    "   AND state = $2 AND created_at <= to_timestamp($3) "
    " ORDER BY created_at DESC LIMIT 10")


def _registry_row(r) -> dict:
    """A REGISTRY_SQL row as `bettor_funded_model._row` reads a full row:
    the provenance without its stored records, its decision ids restored
    in their stored order (or verbatim when not an array)."""
    d = dict(r)
    slim = d.pop("_provenance_slim", None)
    ids_type = d.pop("_provenance_ids_type", None)
    ids = d.pop("_provenance_ids", None)
    raw = d.pop("_provenance_ids_raw", None)
    if slim is None:
        d["training_provenance"] = None
        return d
    prov = json.loads(slim) if isinstance(slim, str) else slim
    if not isinstance(prov, dict):
        d["training_provenance"] = prov          # not an object: verbatim
        return d
    prov = dict(prov)
    if ids_type == "array":
        prov["decision_ids"] = list(ids or [])
    elif ids_type is not None:
        prov["decision_ids"] = (json.loads(raw) if isinstance(raw, str)
                                else raw)
    d["training_provenance"] = prov
    return d


async def research_model(conn, *, at: float, verify: bool = True) -> dict:
    """THE NEWEST CANDIDATE RESEARCH MODEL registered before `at`, with its
    provenance verification. Never promotes; never raises."""
    from .. import bettor_funded_model as FM
    try:
        rows = await conn.fetch(REGISTRY_SQL, FM.KEY_ENTRY_PAYOUT,
                                FM.STATE_CANDIDATE, float(at))
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "refusal": R_NO_RESEARCH_MODEL,
                "why": "the model registry read failed: %s"
                       % type(exc).__name__}
    for r in rows:
        m = FM._row(_registry_row(r))
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
            except asyncio.CancelledError:
                # THIS DECISION CANCELLED (its deadline): never swallowed.
                # OTHERWISE the CPU lane cancelled the check's job -- it was
                # shut down with the job queued (cpu_lane.shutdown) -- and
                # the check did not run: refused by name, never verified.
                me = asyncio.current_task()
                if me is None or me.cancelling():
                    raise
                chk = {"ok": False, "refusal": R_PROVENANCE_CHECK_NOT_RUN}
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
        from .. import bettor_market_family as MF
        from .. import pinnapi_feed_runtime as feed
        from .. import pinnapi_primary as primary
        owner = feed._STATE.get("owner")
        try:
            if not ref:
                check = {"ok": False,
                         "reason": "PINNAPI_PRIMARY_PROVENANCE_MISSING"}
            elif ref.get("version") == MF.VERSION:
                # A LINE VALUATION (spread / total / team total): the same
                # recheck on its own market -- same runtime, record, market,
                # epoch, change, prices and points, inside the 30 s rule
                check = MF.validate_reference(
                    owner.cache if owner else None, ref, at=at,
                    max_age_s=max_age,
                    runtime_id=feed._STATE.get("runtime_id"))
            else:
                check = primary.validate(
                    owner.cache if owner else None, {"reference_input": ref},
                    at=at, max_age_s=max_age,
                    runtime_id=feed._STATE.get("runtime_id"))
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
#: verifies provenance, which re-reads its training records) -- but a
#: verification is served from it only while the training set's change stamp
#: is the one read before that verification (below).
CONTEXT_TTL_S = 300.0
_CONTEXT_CACHE: dict = {}

# ── (RC6 provenance D2) NO VERIFICATION OUTLIVES A CHANGE TO WHAT IT READ ──
#
# THE DEFECT. A session's cached context served its research model, with
# `provenance_verified`, for up to CONTEXT_TTL_S after a training label was
# corrected: every PAPER decision of that session used a model whose
# training set no longer reproduced (test_rc6_provenance_open_defects, D2).
#
# THE RULE. A context is computed after reading the training set's CHANGE
# STAMP (migration 365: a counter every UPDATE / DELETE / TRUNCATE that can
# alter a verified training record, a label, a settlement or the stored
# digest moves, in the writer's own transaction, plus the catalog identity
# of the triggers that move it and of the tables they watch). The stamp is
# read BEFORE the verification reads the registry and the ledger. A
# decision is served a context it did not compute only when
#   (a) that context's reads began after the decision arrived (it joined a
#       verification in flight that had not yet read anything), or
#   (b) the stamp, read on the decision's own connection after it arrived,
#       equals the stamp read before that context's verification.
# Anything else -- a different stamp, an unreadable stamp (migration 365
# absent, a trigger disabled or dropped, the read failing) -- recomputes:
# the verification runs again. A stamp proves a change happened, never that
# none did, by its own content: the proof that an equal stamp means no
# change is the trigger coverage (migration 365's header), and every way to
# write the tables without those triggers firing changes the stamp too.
#
# WHY NOT A CONTENT FINGERPRINT. An md5 over the named records (the
# prototype, joined in SQL from the registry's ids) cost ~257 ms of
# Postgres per cache hit at production's 28,303 records -- 2.3 s of
# database time per second at production's 9 decisions in one second, and
# growing with every record -- and it hashed fewer columns than a training
# record is built from (fixture and decided_at were not in it). The stamp
# is one primary-key row and two catalog reads (LOCAL: well under 1 ms).
#: (table, trigger) pairs migration 365 arms; the counter row lists them
#: (watched_tables / watched_triggers), so the stamp names no table here.
TRAINING_SET_TRIGGERS = 6
TRAINING_SET_STAMP_SQL = """
    SELECT CASE WHEN a.n = %d
                THEN concat_ws('|', c.changes, c.xmin, c.ctid, a.triggers,
                               a.heaps)
           END
      FROM research_training_set_changes c
     CROSS JOIN LATERAL (
            SELECT count(*) FILTER (
                       WHERE t.tgenabled = 'A'
                         AND p.proname = 'research_training_set_changed')
                       AS n,
                   string_agg(concat_ws('/', w.rel::oid, w.name, t.oid,
                                        t.tgenabled, t.xmin, t.ctid, p.oid,
                                        p.xmin, p.ctid),
                              ',' ORDER BY w.name, w.rel::oid) AS triggers,
                   (SELECT string_agg(concat_ws('/', r.oid, r.relfilenode),
                                      ',' ORDER BY r.oid)
                      FROM pg_class r
                     WHERE r.oid = ANY(c.watched_tables::oid[])) AS heaps
              FROM unnest(c.watched_tables, c.watched_triggers)
                   AS w(rel, name)
              LEFT JOIN pg_trigger t ON t.tgrelid = w.rel
                                    AND t.tgname = w.name
              LEFT JOIN pg_proc p ON p.oid = t.tgfoid) a
     WHERE c.scope = 'DEREK_RESEARCH_TRAINING_SET'
""" % TRAINING_SET_TRIGGERS


async def _training_set_stamp(conn) -> str | None:
    """The research training set's change stamp, or None when it cannot be
    read or a trigger that moves it is missing or not ENABLE ALWAYS (then
    nothing is served from the cache). Never raises."""
    try:
        got = await conn.fetchval(TRAINING_SET_STAMP_SQL)
    except Exception:                                           # noqa: BLE001
        return None
    return got if isinstance(got, str) and got else None


# ── (RC6 provenance D3) ONE VERIFICATION IN FLIGHT PER CONTEXT KEY ──────
#
# THE DEFECT. Off the loop (the RC6.1 offload), every decision of a session
# that arrived while its cold context was being verified missed the cache
# too and queued ITS OWN full verification on the one CPU lane (8 of 8 in
# test_rc6_provenance_open_defects D3; 5-6 per expiry in the LOCAL arrivals
# bench, against 2 when the blocking loop made the first one an accidental
# single flight), so decisions queued toward the 8 s deadline.
#
# THE FLIGHT. The first decision of a key that misses leads: it registers a
# flight, computes on ITS OWN connection, stores the context in the cache
# and resolves the flight. A decision that misses while a flight of its key
# is running waits for it (shielded) and is then served under the rule
# above. A flight that fails or is cancelled (its leader's deadline)
# resolves to nothing: nothing is cached, and each waiter tries again -- the
# cache, a newer flight, or leading one itself -- so a failure is never
# served as a success. A waiter whose own deadline passes stops waiting
# (its CancelledError propagates: no model reaches it) and the flight runs
# on for the others. At most MAX_CONTEXT_FLIGHTS keys hold a flight at once
# (one entry per key, removed by its leader whatever happens); a key beyond
# that computes without one.
MAX_CONTEXT_FLIGHTS = 16
#: rounds a decision waits for flights before it computes on its own
#: (each round is one flight completing; normally one round is enough)
MAX_FLIGHT_ROUNDS = 3
_CONTEXT_FLIGHTS: dict = {}
_SEQ = itertools.count(1)


class _Flight:
    """One context computation in flight: its loop, its decision instant and
    the future its leader resolves (the cache entry, or None on failure)."""
    __slots__ = ("future", "loop", "at")

    def __init__(self, loop, at: float):
        self.loop = loop
        self.at = float(at)
        self.future = loop.create_future()


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


def _model_fits_an_earlier_instant(derek: dict, now: float) -> bool:
    """A context computed for a LATER instant than `now` names the model a
    context at `now` would name when that model was registered by `now`:
    it is the newest research candidate registered by the later instant, so
    none was registered between. Only a model the context named (verified,
    or refused for its provenance) qualifies."""
    m = derek.get("model") or {}
    created = m.get("created_at")
    if not m.get("model_id") or created is None or float(created) > now:
        return False
    return bool(m.get("ok") and m.get("provenance_verified")) or \
        m.get("refusal") == R_MODEL_UNVERIFIED


async def _serve(conn, entry: dict, *, now: float, arrived: int):
    """`entry` (a cached or just-flown context) for a decision at `now` that
    arrived at sequence point `arrived`, or None when it cannot be served.

    THE INSTANT. A context of an instant at or before `now`, within
    CONTEXT_TTL_S, is served whole (the cache's rule, unchanged). One of a
    LATER instant (one session's decisions reach their context out of
    order) lends only its model, and only when that model was registered by
    `now`; the void measure is read at `now` itself, never after it.
    THE VERIFICATION. Served only under the rule above: its reads began
    after this decision arrived, or the change stamp is unchanged."""
    at, derek = float(entry["at"]), entry["derek"]
    if 0.0 <= now - at < CONTEXT_TTL_S:
        whole = True
    elif 0.0 < at - now < CONTEXT_TTL_S and \
            _model_fits_an_earlier_instant(derek, now):
        whole = False
    else:
        return None
    if entry["read_seq"] < arrived:
        # read before this decision arrived: provably unchanged, or nothing
        if entry["stamp"] is None or \
                await _training_set_stamp(conn) != entry["stamp"]:
            return None
        if whole and await _a_model_appeared(conn, derek, at=now):
            return None
    if whole:
        return derek
    return {"model": derek["model"], "model_attempt": derek["model_attempt"],
            "void": await DP.void_measure(conn, through=now),
            "calibration": {}}


async def _compute(conn, ctx: dict, key) -> dict:
    """THE CONTEXT, computed on this decision's connection; with a key, the
    change stamp is read FIRST and the context is cached with it."""
    at = ctx["now"]
    read_seq = next(_SEQ)
    stamp = await _training_set_stamp(conn) if key is not None else None
    model = await research_model(conn, at=at)
    attempt = None
    if not model.get("ok") and model.get("refusal") == R_NO_RESEARCH_MODEL:
        attempt = await ensure_model_attempt(conn, at=at)
        model = await research_model(conn, at=at)
    void = await DP.void_measure(conn, through=at)
    ctx["derek"] = {"model": model, "model_attempt": attempt, "void": void,
                    "calibration": {}}
    entry = {"at": float(at), "derek": ctx["derek"], "stamp": stamp,
             "read_seq": read_seq}
    if key is not None:
        _CONTEXT_CACHE.clear()
        _CONTEXT_CACHE[key] = entry
    return entry


async def _lead(conn, ctx: dict, key) -> dict:
    """Compute as the key's flight (when the flight table has room), and
    resolve the flight however the computation ends."""
    loop = asyncio.get_running_loop()
    flight = None
    if key in _CONTEXT_FLIGHTS or len(_CONTEXT_FLIGHTS) < MAX_CONTEXT_FLIGHTS:
        flight = _Flight(loop, float(ctx["now"]))
        _CONTEXT_FLIGHTS[key] = flight
    entry = None
    try:
        entry = await _compute(conn, ctx, key)
        return entry["derek"]
    finally:
        if flight is not None:
            if _CONTEXT_FLIGHTS.get(key) is flight:
                del _CONTEXT_FLIGHTS[key]
            if not flight.future.done():
                # None when the computation raised or was cancelled: never
                # cached, never served
                flight.future.set_result(entry)


async def _context(conn, ctx: dict) -> dict:
    """Once per pass (or per CONTEXT_TTL_S for the per-valuation hook): the
    research model, its daily attempt, the void measure and the source
    calibration. A cached context without a model is recomputed as soon as
    a candidate is registered; a cached verification only while the
    training set's change stamp proves it unchanged (D2); one session's
    concurrent decisions share one computation (D3)."""
    if "derek" in ctx:
        return ctx["derek"]
    key = ctx.get("context_cache_key")
    if key is None:
        return (await _compute(conn, ctx, None))["derek"]
    now = float(ctx["now"])
    arrived = next(_SEQ)
    loop = asyncio.get_running_loop()
    refused = None
    for _round in range(MAX_FLIGHT_ROUNDS):
        hit = _CONTEXT_CACHE.get(key)
        if hit is not None and hit is not refused:
            got = await _serve(conn, hit, now=now, arrived=arrived)
            if got is not None:
                ctx["derek"] = got
                return got
            refused = hit
        flight = _CONTEXT_FLIGHTS.get(key)
        if flight is None or flight.loop is not loop or flight.future.done():
            return await _lead(conn, ctx, key)
        # this decision's own cancellation (its deadline) propagates here;
        # the flight is shielded from it and runs on for the others
        flown = await asyncio.shield(flight.future)
        if flown is None:
            continue            # failed or cut: nothing to serve; try again
        got = await _serve(conn, flown, now=now, arrived=arrived)
        if got is not None:
            ctx["derek"] = got
            return got
        refused = flown
    # flights kept failing or could not serve this decision: its own context
    return (await _compute(conn, ctx, key))["derek"]


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


def settlement_difference(cand: dict, row: dict, *, catalogue=None,
                          precise=()) -> dict:
    """THE PRICED SETTLEMENT-DIFFERENCE POLICY'S ELIGIBILITY for a candidate
    the strict settlement check refuses (an INCOMPATIBLE comparison or a
    cited NCAAF clause difference): bettor_settlement_difference_policy.
    eligibility on the candidate's recorded comparison, its settlement-stage
    lane refusals and the completed-game match (ordinary completion only).
    The match is kept: `apply_settlement_difference` takes its conversion.
    Pure apart from what it is handed."""
    from .. import bettor_settlement_difference_policy as SDP
    from . import paper_benchmark as PB
    try:
        match = PB.completed_game_match(cand, row, catalogue=catalogue)
    except Exception as exc:                                    # noqa: BLE001
        match = {"established": False,
                 "refusals": ["COMPLETED_GAME_MATCH_RAISED:%s"
                              % type(exc).__name__], "checks": []}
    scmp = DP._j(row.get("settlement_comparison")) or {}
    st = dict(cand.get("settlement") or {})
    st["per_condition"] = scmp.get("per_condition") or {}
    from .. import bettor_nfl_settlement as NFL
    league = NFL.league_of_slug(cand.get("us_market_slug"))
    el = SDP.eligibility(sport_family=cand.get("sport_family"),
                         market=cand.get("market"), league=league,
                         settlement=st,
                         lane_codes=list(st.get("unmet") or []),
                         precise_codes=list(precise or []), match=match)
    return dict(el, match=match, league=league)


def apply_settlement_difference(pin: dict, sd: dict, *, cand: dict) -> str | None:
    """THE PRICE, IN PLACE: `pin["p"]` (the stored book number for the event
    the contract pays on, already re-aged) becomes the policy's worst-case
    venue value; the book's own number stays as `p_book_conditional_no_tie`
    and the declaration as `venue_conversion` (version, formula, rate,
    basis), which gross_edge_inputs re-derives. The NFL tie (and the NCAAF
    identity) is converted first by the completed-game conversion. Never
    touches the age, the freshness verdict or the 30 s limit. Returns the
    refusal to append, or None."""
    from .. import bettor_settlement_difference_policy as SDP
    from . import paper_benchmark as PB
    if pin.get("p") is None:
        return None
    p_book = float(pin["p"])
    inner = None
    p_completed = p_book
    if (sd.get("match") or {}).get("venue_conversion"):
        probe = {"p": p_book}
        why = PB.apply_venue_conversion(probe, sd["match"])
        if why:
            pin["settlement_difference_policy"] = {
                "policy_id": SDP.POLICY_ID, "version": SDP.VERSION,
                "refusal": why, "completed_conversion":
                    probe.get("venue_conversion")}
            return why
        p_completed = float(probe["p"])
        vc = probe.get("venue_conversion") or {}
        if vc.get("tie_rate_interval") is not None:
            inner = {k: vc.get(k) for k in (
                "version", "tie_rate_interval", "tie_payout_per_contract",
                "tie_rate_used", "p", "formula")}
    priced = SDP.price(p_completed, sport_family=cand.get("sport_family"),
                       p_book=p_book, completed_conversion=inner)
    pin["settlement_difference_policy"] = priced
    if priced.get("refusal") or priced.get("p") is None:
        return priced.get("refusal") or SDP.R_NO_P
    if float(priced["p"]) <= 0.0:
        # PRICED, AT ZERO: no price above zero carries an edge; the book's
        # number stays what it was (never a probability of 0 handed on)
        return SDP.R_PRICED_AT_ZERO
    pin["p_book_conditional_no_tie"] = p_book
    pin["p"] = float(priced["p"])
    pin["p_is"] = SDP.P_IS
    pin["venue_conversion"] = {
        "applies": True, "version": SDP.VERSION, "policy_id": SDP.POLICY_ID,
        "sport_family": cand.get("sport_family"), "p": pin["p"],
        "p_book": p_book, "p_completed": p_completed,
        "q_hi": priced["q_hi"], "formula": SDP.FORMULA,
        "completed_conversion": inner,
        "rate_basis": {k: priced["rate"].get(k) for k in (
            "q_hi_is", "measured", "prior_floor_upper", "basis")}}
    return None


def _priced_settlement(priced: dict) -> dict:
    """The settlement the capital gate is handed for a contract the priced
    settlement-difference policy admitted: the policy's own marker, id and
    version (bettor_capital_eligibility.settlement_resolved checks them)."""
    from .. import bettor_settlement_difference_policy as SDP
    return {"compatibility": SDP.SETTLEMENT_PRICED,
            "policy_id": priced.get("policy_id"),
            "version": priced.get("version"),
            "p": priced.get("p"), "q_hi": priced.get("q_hi"),
            "basis": "bettor_settlement_difference_policy.price"}


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
    # THE PRECISE CLAUSES RIDE BEHIND THE CATEGORY (P0 incident, NCAAF):
    # where the venue and book texts are cited, the strict refusal names each
    # clause the strict policy lacks (paper_brief reports the first one after
    # the category); a cited payout difference refuses even if a row recorded
    # COMPATIBLE. Every other contract: unchanged.
    precise = DP.strict_settlement_reasons(cand)
    # THE PRICED SETTLEMENT-DIFFERENCE POLICY (P1 first-loss census): a
    # difference that lies only in the EXCEPTIONAL states (postponed,
    # abandoned, suspended ...: book void, venue last fair price) is priced
    # -- p_venue = max(0, p_completed - q_hi) -- and admitted ONLY through
    # that policy; anything it cannot price refuses SETTLEMENT_NOT_SUPPORTED
    # with the policy's exact code behind it.
    sdp = None
    if cand["settlement"].get("compatibility") == "INCOMPATIBLE" or precise:
        sdp = settlement_difference(cand, row, catalogue=cat,
                                    precise=precise)
        if not sdp.get("eligible"):
            refusals.append(DP.R_SETTLEMENT)
            refusals.extend(r for r in precise if r not in refusals)
            refusals.extend(r for r in sdp.get("refusals") or []
                            if r not in refusals)
    pin = _pinnacle(cand, at=at, max_age=float(ent["pinnacle_max_age_s"]))
    if sdp is not None and sdp.get("eligible"):
        why_sdp = apply_settlement_difference(pin, sdp, cand=cand)
        pin["settlement_difference_eligibility"] = {
            k: sdp.get(k) for k in ("eligible", "why", "league",
                                    "ordinary_completion")}
        if why_sdp:
            from .. import bettor_settlement_difference_policy as SDP
            if why_sdp != SDP.R_PRICED_AT_ZERO:
                refusals.append(DP.R_SETTLEMENT)
            if why_sdp not in refusals:
                refusals.append(why_sdp)
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
    gross_inputs: dict | None = None
    fee_fn = ctx.get("fee_fn")
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
            refusals.append(no_book_refusal(obs, lv))
        else:
            # THE GROSS EDGE'S INPUTS, VALIDATED BEFORE V2 JUDGES THE EDGE
            # (P0 incident; review of 7bd084b: only the completed-game
            # policy carried the receipt, so Derek still recorded
            # BELOW_MIN_GROSS_EDGE on inputs nothing had checked). The same
            # checks as every policy: the Pinnacle p is the held side's (the
            # de-vig recomputed from the row's own prices), the price is the
            # side a BUY consumes, the fee evaluates, the Pinnacle age is
            # inside its unchanged rule. This policy reads its own book
            # inside the decision and has no book-age bound; none is added
            # (book_max_age_s None: the age is recorded, an unknown receipt
            # instant fails). A failed input is a SOFTWARE refusal by name
            # and the V2 combination is not run on it -- never an economic
            # verdict on unvalidated numbers. No threshold moves.
            gross_inputs = GEI.validate(
                p=pin.get("p"), side=side, row=row, levels=levels,
                consumed_side=lv["side"], md=md,
                fee_per_contract=(lambda px: float(L._fee(fee_fn, 1, px,
                                                          at))),
                pin=pin, decided_at=at,
                edge_at=(float(ctx["clock"]()) if ctx.get("clock")
                         else at),
                book_observed_at=obs.get("observed_at"),
                book_max_age_s=None,
                threshold_edge_pp=round(float(ent["min_gross_edge_pp"])
                                        * 100.0, 9))
            pin["gross_edge_inputs"] = gross_inputs
            if not gross_inputs["ok"]:
                refusals.extend(r for r in gross_inputs["refusals"]
                                if r not in refusals)
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
    if levels and p_blend is not None and (gross_inputs or {}).get("ok"):
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
        pd["gross_edge_inputs"] = GEI.summary(gross_inputs)
        refusals.extend(r for r in pd["refusals"] if r not in refusals)
        if not pd["refusals"] and sized["qty"] < 1:
            refusals.append(DP.R_NO_QTY)
    recheck_primary_reference(cand, pin, ctx, refusals)
    ce = None
    if not refusals:
        # CAPITAL ELIGIBILITY + THE STRATEGY LIFECYCLE (migration 290) + THE
        # CAPITAL AUTHORITY (migration 305): an admitted ENTER gets paper
        # capital only with executable depth, resolved identity and
        # settlement terms, a positive total executable EV, no stopping rule
        # firing now and POSITIVE forward economics; otherwise CASH/WAIT is
        # the recorded decision (and a shadow counterfactual when only the
        # capital authority was missing).
        priced = pin.get("settlement_difference_policy") or {}
        ce = await capital_gate(
            conn, ctx, strategy=STRATEGY, p=p_blend, levels=levels,
            sized=sized, cand=cand, side=side, at=at, fee_fn=fee_fn,
            decision_id=did,
            # A CONTRACT ADMITTED THROUGH THE PRICED SETTLEMENT-DIFFERENCE
            # POLICY is resolved by THAT policy, named as such (never
            # COMPATIBLE); every other contract keeps its recorded verdict
            settlement=(_priced_settlement(priced)
                        if priced.get("p") is not None else None),
            threshold_edge_pp=float(ent["min_gross_edge_pp"]) * 100.0,
            book=(None if obs is None else {
                "book_obs_id": obs.get("obs_id"),
                "observed_at": obs.get("observed_at")}),
            p_observed_at=pin.get("at"))
        if econ is not None:
            econ["capital_eligibility"] = ce
        if pd is not None:
            pd["capital_eligibility"] = {k: ce.get(k) for k in (
                "decision", "capital_eligible", "qty", "allocation_usd",
                "total_executable_ev_usd", "refusals")}
        if ce.get("capital_eligible"):
            sized = dict(sized, qty=int(ce["qty"]))
        else:
            refusals.extend(r for r in ce["refusals"] if r not in refusals)
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

    async def insert_row():
        # THE DECISION ROW. For an ENTER it is the FIRST step of the owed
        # order sequence (`owed_order(record=...)`, RC6.2 enter-integrity
        # rework): a deadline that falls while it waits on the account row
        # lock can no longer leave a committed ENTER that nothing owes.
        return await conn.fetchval(
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

    from .. import bettor_capital_authority as CA

    def capital_evidence():
        return CA.capital_evidence(
            ce, p=p_blend, limit=sized.get("limit"),
            threshold_edge_pp=float(ent["min_gross_edge_pp"]) * 100.0,
            basis="DEREK_CAPITAL_GATE", levels=levels,
            book_obs_id=None if obs is None else obs.get("obs_id"),
            book_observed_at=None if obs is None else obs.get("observed_at"))
    if verdict != DP.ENTER:
        if await insert_row() is None:
            # ANOTHER WRITER (the in-cycle hook or a racing pass) RECORDED
            # THIS VALUATION'S DECISION FIRST. Its record stands; nothing is
            # sent on this computation.
            return dict(rec, duplicate=True)
        cevidence = capital_evidence()
        # THE ENTRY-REFUSAL CENSUS (migration 305): evidence only.
        await CA.record_refusal(
            conn, account_id=ctx["account_id"], strategy=STRATEGY,
            stage="DECISION", refusal=rec["refusal"], refusals=refusals,
            decision_id=did, slug=cand.get("us_market_slug"),
            holding_side=side, fixture=cand.get("fixture"),
            line=cand.get("line"), scope=cand.get("scope"), p=p_blend,
            best_price=(levels[0]["price"] if levels else None),
            threshold_edge_pp=float(ent["min_gross_edge_pp"]) * 100.0,
            evidence=cevidence,
            expected_fees_usd=(econ or {}).get("fees_usd"),
            executable_ev_usd=(econ or {}).get("expected_net_profit_usd"),
            qty=sized.get("qty"), limit_price=sized.get("limit"), at=at)
        return rec
    # AN ENTER: ITS ROW AND ITS ORDER ARE OWED, whatever the decision
    # deadline (`bounded_decision`) or a cancellation of the caller
    # (`owed_order`, which runs `insert_row` as the sequence's first step;
    # the backstop names what no code path can).
    delay = float(sim_cfg["decision_to_execution_delay_s"])

    async def sequence(progress: dict) -> dict:
        # ── ONLY NOW, THE PAPER ORDER (built inside the owed sequence: a
        # raise here is named, never left to the backstop) ──────────────
        progress["stage"] = "PAPER_ORDER_BUILDING"
        cevidence = capital_evidence()
        order = {"idempotency_key": "%s:ENTRY" % did,
                 "account_id": ctx["account_id"],
                 "session_id": ctx["session_id"],
                 "group_id": group_id_for(did), "role": "ENTRY",
                 "strategy": STRATEGY,
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
                 "simulator_version": cfg["simulator_version"],
                 # the decision's executable-EV evidence, re-checked by the
                 # ledger's capital authority under the account lock
                 "capital_evidence": cevidence}
        progress["stage"] = "PAPER_ORDER_SUBMITTING"
        got = await L.submit_order(conn, order, caps=cfg["risk"],
                                   fee_fn=fee_fn, now=at)
        rec["order"] = {k: got.get(k) for k in ("ok", "refusal",
                                                "duplicate")}
        if got.get("ok"):
            progress["outcome"] = "ORDER"
            rec["order_id"] = got["order"]["order_id"]
            rec["eligible_at"] = at + delay
        else:
            progress.update(stage="ORDER_REFUSED_NOT_YET_NAMED",
                            order_refusal=got.get("refusal"))
            rec["order_refusal"] = got.get("refusal")
            await _finding(conn, ctx, kind=R_ORDER_REFUSED, subject=did,
                           detail={"refusal": got.get("refusal"),
                                   "decision_id": did, "at": at,
                                   **{k: v for k, v in got.items()
                                      if k not in ("ok",)}})
            progress["outcome"] = "ORDER_REFUSED"
        return rec

    # A CANCELLATION OF THE CALLER NO LONGER CUTS IT (`owed_order`).
    return await owed_order(conn, ctx, decision_id=did, strategy=STRATEGY,
                            record=insert_row,
                            duplicate=lambda: dict(rec, duplicate=True),
                            sequence=sequence)


async def lifecycle_gate(conn, ctx: dict, *, strategy: str, at: float) -> dict:
    """THE DECISION-STAGE LIFECYCLE READ, shared by every paper policy
    (bettor_strategy_lifecycle.decision_gate): the strategy's state, its
    event id, its size factor and -- for a no-entry state (SHADOW_ONLY /
    QUARANTINED / RETIRED) -- the refusal that state carries. Fail-closed:
    an unreadable state, or a read that raised, refuses
    STRATEGY_LIFECYCLE_STATE_UNREADABLE. Never raises.

    `capital_gate` (Derek, the completed-game and strict benchmark policies)
    reads it first. The exploration and maker policies read it as their LAST
    entry condition, exactly where their verdict would otherwise be ENTER
    (enter-integrity, production 2026-10-06 .. 10-09: a QUARANTINED
    exploration strategy recorded ~2,700 ENTER verdicts that only the
    ledger's entry gate refused). The ledger's entry gate is unchanged and
    stays the final guard."""
    from .. import bettor_strategy_lifecycle as LC
    try:
        return await LC.decision_gate(conn, account_id=ctx["account_id"],
                                      strategy=strategy, at=at)
    except asyncio.CancelledError:
        raise
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "refusal": LC.R_LIFECYCLE_UNREADABLE,
                "size_factor": 0.0, "why": type(exc).__name__}


def lifecycle_condition(lc: dict | None) -> dict:
    """The decision record's condition row for the lifecycle read (None:
    not evaluated -- an earlier condition already refused)."""
    lc = lc or {}
    return {"condition": "strategy_lifecycle_allows_a_paper_entry",
            "passed": None if not lc else not lc.get("refusal"),
            "value": lc.get("state"),
            "lifecycle_event_id": lc.get("lifecycle_event_id"),
            "size_factor": lc.get("size_factor"),
            "refusal": lc.get("refusal"),
            "reader": "bettor_strategy_lifecycle.decision_gate"}


async def capital_gate(conn, ctx: dict, *, strategy: str, p, levels, sized,
                       cand: dict, side, at: float, fee_fn,
                       settlement: dict | None = None, decision_id=None,
                       threshold_edge_pp=None, book: dict | None = None,
                       p_observed_at=None) -> dict:
    """THE PAPER PATH'S CAPITAL GATE for one admitted ENTER: the strategy's
    lifecycle state (bettor_strategy_lifecycle.decision_gate: a no-entry
    state refuses by name; REDUCED_SIZE halves the size), then
    bettor_capital_eligibility.evaluate on the observed ladder, then the
    PAPER CAPITAL AUTHORITY (bettor_capital_authority.authority: no stopping
    rule firing NOW, forward economics POSITIVE). Shared by Derek and the
    benchmark policies. `settlement` (default: the candidate's recorded
    comparison) lets a policy pass the settlement verdict its own contract
    match established. Never raises: a failure refuses.

    ZERO-CAPITAL SHADOW LEARNING (migration 305): a decision refused ONLY for
    want of capital authority (a no-entry lifecycle state, a firing rule,
    forward economics UNKNOWN / NEGATIVE) whose capital-eligibility
    evaluation at its intended size passes is recorded as a
    SHADOW_COUNTERFACTUAL -- never an order, never cash, never exposure.

    THE PROFITABILITY BIND (migration 309, bettor_paper_profitability_bind): an
    eligible decision is then bound -- calibrated probability, all-in
    executable EV with the learned execution costs and residual haircut,
    churn control, capacity / capital-hour / correlation size (refuse or
    shrink only) -- BEFORE the capital authority, which adds the
    absolute-positive champion rule and the regime authority for the
    decision's regime. A decision the bind's economics refuse is CASH and
    never a shadow; the shadow of a decision without capital authority is
    recorded at the bound size. Every evaluation is recorded."""
    from .. import bettor_capital_authority as CA
    from .. import bettor_capital_eligibility as CE
    from .. import bettor_paper_profitability_bind as PBIND
    lc = await lifecycle_gate(conn, ctx, strategy=strategy, at=at)
    kw = dict(p=p, levels=levels, qty=sized.get("qty"),
              limit=sized.get("limit"),
              fee_fn=lambda q, px: float(L._fee(fee_fn, q, px, at)),
              settlement=(cand.get("settlement") if settlement is None
                          else settlement),
              identity={"us_market_slug": cand.get("us_market_slug"),
                        "payout_event": cand.get("payout_event"),
                        "fixture": cand.get("fixture"), "holding_side": side})

    async def bind(ce_full: dict) -> dict:
        ev = CA.capital_evidence(ce_full, p=p, limit=sized.get("limit"),
                                 threshold_edge_pp=threshold_edge_pp,
                                 basis="DECISION_CAPITAL_GATE", levels=levels)
        return await PBIND.entry_bind(
            conn, account_id=ctx["account_id"], strategy=strategy,
            evidence=ev, qty_in=ce_full.get("qty"),
            slug=cand.get("us_market_slug"), side=side,
            fixture=cand.get("fixture"),
            order_type=((ctx.get("config") or {}).get("entry") or {}).get(
                "order_type"), at=at, fee_fn=fee_fn,
            # the decision's freshness / settlement inputs, carried to the
            # ledger on the evidence so it re-derives the same size
            inputs={"evaluated_at": at, "p_observed_at": p_observed_at,
                    "book_observed_at": (book or {}).get("observed_at"),
                    "settlement": {k: (kw["settlement"] or {}).get(k) for k in (
                        "compatibility", "policy_id", "version", "p",
                        "q_hi")}})

    def bound(ce_full: dict, b: dict) -> dict:
        """The capital-eligibility result restated at the bind's size."""
        fin = b.get("all_in")
        if not fin:
            return dict(ce_full, profitability_bind=PBIND.summary(b))
        q = int(b.get("qty") or 0)
        left, fills = float(q), []
        for px, fq in ce_full.get("fills") or []:
            if left <= 1e-9:
                break
            t = min(float(fq), left)
            fills.append([px, t])
            left -= t
        return dict(ce_full, qty=q, filled_qty=float(q), fills=fills,
                    cost_usd=fin.get("cost_usd"), fees_usd=fin.get("fees_usd"),
                    adverse_selection_usd=fin.get("adverse_selection_usd"),
                    total_executable_ev_usd=fin.get("ev_given_fill_usd"),
                    allocation_usd=fin.get("capital_usd"),
                    bind_inputs=b.get("bind_inputs"),
                    pre_bind={"qty": ce_full.get("qty"),
                              "fills": ce_full.get("fills"),
                              "adverse_selection_usd": ce_full.get(
                                  "adverse_selection_usd"),
                              "total_executable_ev_usd": ce_full.get(
                                  "total_executable_ev_usd")},
                    profitability_bind=PBIND.summary(b))

    async def record(b: dict, refusal) -> None:
        await PBIND.record_evaluation(
            conn, account_id=ctx["account_id"], strategy=strategy,
            stage="DECISION", b=b, refusal=refusal, decision_id=decision_id,
            slug=cand.get("us_market_slug"), side=side,
            fixture=cand.get("fixture"), at=at)

    async def shadow(ce_full: dict, refusal: str) -> dict:
        try:
            return await CA.shadow_from_decision(
                conn, ctx, strategy=strategy, decision_id=decision_id,
                cand=cand, side=side, ce=ce_full, p=p,
                limit=sized.get("limit"), levels=levels, at=at,
                refusal=refusal, lifecycle_state=lc.get("state"),
                threshold_edge_pp=threshold_edge_pp, book=book)
        except Exception as exc:                                # noqa: BLE001
            return {"recorded": False, "why": type(exc).__name__}

    if lc.get("refusal"):
        out = {"version": CE.VERSION, "capital_eligible": False,
               "decision": CE.CASH_WAIT, "allocation_usd": 0.0, "qty": 0,
               "refusals": [lc["refusal"]], "lifecycle": lc}
        if lc["refusal"] in CA.NO_CAPITAL_AUTHORITY:
            # the counterfactual at the policy's INTENDED size (no lifecycle
            # factor: the state allows no size), for evidence only -- under
            # the same profitability bind
            full = CE.evaluate(**kw, size_factor=1.0)
            if full.get("capital_eligible"):
                b = await bind(full)
                out["profitability_bind"] = PBIND.summary(b)
                await record(b, lc["refusal"])
                if b.get("refusal"):
                    full = dict(full, capital_eligible=False,
                                refusals=[b["refusal"]])
                else:
                    full = bound(full, b)
            out["shadow_evaluation"] = {k: full.get(k) for k in (
                "capital_eligible", "qty", "total_executable_ev_usd",
                "fees_usd", "adverse_selection_usd", "refusals")}
            out["shadow"] = await shadow(full, lc["refusal"])
        return out
    ce = CE.evaluate(**kw, size_factor=lc.get("size_factor", 0.0))
    ce["lifecycle"] = lc
    b = None
    if ce.get("capital_eligible"):
        b = await bind(ce)
        if b.get("refusal"):
            await record(b, b["refusal"])
            return dict(ce, capital_eligible=False, decision=CE.CASH_WAIT,
                        allocation_usd=0.0, qty=0, refusals=[b["refusal"]],
                        profitability_bind=PBIND.summary(b),
                        why="profitability bind: %s" % b["refusal"])
        ce = bound(ce, b)
    if ce.get("capital_eligible"):
        try:
            auth = await CA.authority(conn, account_id=ctx["account_id"],
                                      strategy=strategy, now=at,
                                      context=(b or {}).get("descriptor"))
        except Exception as exc:                                # noqa: BLE001
            auth = {"refusal": CA.R_FORWARD_UNREADABLE,
                    "why": type(exc).__name__}
        ce["capital_authority"] = {
            "refusal": auth.get("refusal"),
            "forward_verdict": (auth.get("forward") or {}).get("verdict"),
            "forward_observations": (auth.get("forward") or {}).get(
                "observations"),
            "rules_firing": [r["rule_id"] for r in (
                auth.get("rules") or {}).get("firing") or []]}
        ce["capital_authority"]["champion"] = ((auth.get(
            "bind_authority") or {}).get("champion") or {}).get("champion")
        ce["capital_authority"]["regime"] = ((auth.get(
            "bind_authority") or {}).get("regime") or {}).get("regime")
        await record(b or {}, auth.get("refusal"))
        if auth.get("refusal"):
            sh = (await shadow(ce, auth["refusal"])
                  if auth["refusal"] in CA.NO_CAPITAL_AUTHORITY else None)
            ce = dict(ce, capital_eligible=False, decision=CE.CASH_WAIT,
                      allocation_usd=0.0, qty=0, refusals=[auth["refusal"]],
                      shadow=sh, why=("no capital authority: %s"
                                      % auth["refusal"]))
    return ce


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
        # PRICED BY A FEED RUNTIME THAT NO LONGER EXISTS (red-team closeout,
        # R_PREVIOUS_RUNTIME): its recheck could only refuse
        # FEED_OWNERSHIP_NOT_HELD; left undecided, recorded
        prev = previous_feed_runtime(row)
        if prev is not None:
            await record_previous_runtime(
                conn, ctx=ctx, valuation_id=row.get("id"),
                strategy="DEREK_ENTRY_POLICY_V2", prev=prev)
            out["deferred"] += 1
            out["previous_runtime"] = out.get("previous_runtime", 0) + 1
            continue
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
    # ...and every open marketable order still waiting for a READABLE book
    # observed at or after its eligible instant (e.g. one the in-cycle hook
    # submitted). An errored observation holds no book and the simulator
    # skips it (bettor_paper_simulator.R_NO_READABLE_BOOK), so an order that
    # has met only those is still waiting and is read again here.
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


#: ── A VALUATION PRICED BY A FEED RUNTIME THAT NO LONGER EXISTS ──────────
#:
#: (red-team closeout, FEED_OWNERSHIP_NOT_HELD.) A PinnAPI-priced valuation
#: carries the feed runtime that priced it (`reference_input.runtime_id`, one
#: per feed start: `pinnapi_feed_runtime.start_default`). Its recheck at the
#: decision instant (`pinnapi_primary.validate`, `bettor_market_family.
#: validate_reference`) refuses FEED_OWNERSHIP_NOT_HELD whenever the
#: deciding process's runtime is another one -- correctly: a restart is a new
#: cache and epoch, and no price of the old one is ever served again. But the
#: paper-pass backstop selects EVERY undecided valuation of the last
#: `valuation_lookback_s` (1800 s), so after each restart (every deploy) it
#: decided the previous runtime's last undecided valuations -- the ones whose
#: in-cycle decision the shutdown cut -- and recorded, for each, a refusal
#: that was certain before it was made and says nothing about the market.
#: In the coverage census such a decision is the event's FIRST loss (MODEL
#: stage, before every later stage), whatever the current runtime's own
#: decisions on the same event said. Production 2026-10-08 24 h census: one
#: event, Vanderbilt v Ole Miss, first loss FEED_OWNERSHIP_NOT_HELD beside
#: 66 BELOW_MIN_GROSS_EDGE decisions on it (ncaaf-funnel readback); the
#: readback does not carry that decision's instant or runtime ids, so this
#: is the mechanism the code admits, not a measured attribution of it.
#:
#: So the pass leaves it undecided, recorded: a DEFERRED `paper_hook_failures`
#: row (stage PAPER_PASS, why = R_PREVIOUS_RUNTIME, with both runtime ids)
#: that the candidates queries read so it is not re-selected (defined here:
#: the benchmark module may import only paper_derek). Only when THIS process
#: runs a feed (a current runtime id exists) and it is a different one: with no feed here the decision is made and refuses exactly as before
#: (that is the truth: no feed here). Nothing is valued on any price; no
#: threshold, clock or the 30 s rule moves.
R_PREVIOUS_RUNTIME = "PINNAPI_VALUATION_PRICED_BY_A_PREVIOUS_FEED_RUNTIME"
PREVIOUS_RUNTIME_COUNTS: dict = {"skipped": 0}


def previous_feed_runtime(row: dict) -> dict | None:
    """None, or the evidence that this valuation was priced by a PinnAPI
    feed runtime other than this process's current one (R_PREVIOUS_RUNTIME).
    Pure but for reading the in-process feed state; never raises."""
    try:
        from .. import pinnapi_feed_runtime as feed
        from .. import pinnapi_primary as primary
        ref = _row_reference(row)
        if ref.get("provider") != primary.PROVIDER:
            return None
        cur = feed._STATE.get("runtime_id")
        was = ref.get("runtime_id")
        if not (isinstance(cur, str) and cur and isinstance(was, str)
                and was) or cur == was:
            return None
        return {"why": R_PREVIOUS_RUNTIME, "valuation_runtime_id": was,
                "current_runtime_id": cur,
                "certain_refusal": "FEED_OWNERSHIP_NOT_HELD"}
    except Exception:                                           # noqa: BLE001
        return None


async def record_previous_runtime(conn, *, ctx: dict, valuation_id,
                                  strategy: str, prev: dict) -> dict:
    """The DEFERRED row for a valuation priced by a previous feed runtime
    (R_PREVIOUS_RUNTIME), and the step's result for it. Never raises."""
    PREVIOUS_RUNTIME_COUNTS["skipped"] += 1
    res = {"deferred": True, "why": R_PREVIOUS_RUNTIME,
           "previous_runtime": dict(prev)}
    try:
        await conn.execute(
            "INSERT INTO paper_hook_failures (session_id, account_id, "
            " valuation_id, strategy, stage, outcome, elapsed_s, error, "
            " detail) VALUES ($1,$2,$3,$4,'PAPER_PASS','DEFERRED',NULL,$5,"
            " $6::jsonb)", ctx.get("session_id"), ctx.get("account_id"),
            int(valuation_id), str(strategy), R_PREVIOUS_RUNTIME,
            json.dumps(dict(prev, deferred=True), default=str))
    except Exception:
        import logging
        logging.getLogger(__name__).warning(
            "previous-runtime valuation not recorded (valuation %s, %s)",
            valuation_id, strategy, exc_info=True)
    return res


def _row_reference(row: dict) -> dict:
    """The valuation row's persisted `settlement_comparison.reference_input`
    (the price's provenance), or {}. Pure."""
    scmp = (row or {}).get("settlement_comparison")
    if isinstance(scmp, str):
        try:
            scmp = json.loads(scmp)
        except ValueError:
            scmp = None
    return dict((scmp or {}).get("reference_input") or {}) \
        if isinstance(scmp, dict) else {}


#: ── A READ BOOK WITH NOBODY ON THE SIDE BOUGHT (software census closure) ──
#:
#: THE_OBSERVED_BOOK_WAS_UNREADABLE_OR_EMPTY joined two facts: a read that
#: FAILED (an error on the observation -- not a book at all) and a read that
#: SUCCEEDED and returned a valid book whose side we would buy from carries
#: no level (`bettor_book_snapshot.acquisition_ladder` book_was
#: VALID_BUT_EMPTY: the side is published, well formed, and empty). The
#: second is the market's own state -- no executable depth -- so it is its
#: own code, ECONOMIC / DEPTH, on the evidence recorded with the decision
#: (the paper_book_observations row: no error, the published empty side).
#: A failed read, a malformed side, a side absent from the payload, or
#: levels our cent grid excluded keep THE_OBSERVED_BOOK_WAS_UNREADABLE_OR_
#: EMPTY (ours). No threshold or gate changes.
R_SIDE_EMPTY_ON_A_READ_BOOK = "THE_OBSERVED_BOOK_HAS_NO_LEVEL_ON_THE_SIDE_BOUGHT"


def no_book_refusal(obs: dict | None, lv: dict | None) -> str:
    """The refusal for an observation that gave no usable level: the
    ECONOMIC empty-side code only for a read that succeeded on a valid,
    empty side; else THE_OBSERVED_BOOK_WAS_UNREADABLE_OR_EMPTY. Pure."""
    o = obs if isinstance(obs, dict) else {}
    v = lv if isinstance(lv, dict) else {}
    if (not o.get("error") and isinstance(o.get("market_data"), dict)
            and v.get("book_was") == "VALID_BUT_EMPTY"
            and not v.get("excluded_off_cent_grid")
            and not (v.get("levels") or [])):
        return R_SIDE_EMPTY_ON_A_READ_BOOK
    return R_NO_BOOK


#: ── A READ OUR OWN MARKET-DATA OWNER NEVER SENT (red-team closeout) ──────
#:
#: THE REGRESSION. Since the shared paper market-data owner (221ce6b9,
#: `paper_market_data`) every paper book read goes through it, and it
#: refuses three reads BEFORE any venue request, by name: a DISCOVERY read
#: while the venue's Retry-After hold is in force (PAPER_DISCOVERY_READ_
#: DEFERRED_DURING_VENUE_HOLD -- production telemetry 2026-10-08 02:29Z:
#: 220 in one process lifetime), a read whose turn in the shared queue
#: would come after the caller's deadline, and a coalesced read whose leader
#: did not answer inside it. Before the owner, the same hold reached the
#: venue request gate, which refuses as OUR_REQUEST_GATE -- recognised here
#: as a cut read, eligible for the one bounded book retry. The owner's
#: refusals were not: each fell through to `no_book_refusal` and was
#: recorded THE_OBSERVED_BOOK_WAS_UNREADABLE_OR_EMPTY -- a book that was
#: never read, named as one that was read and found unreadable -- and the
#: retry that exists for exactly this cooldown was never scheduled.
#:
#: Spelled here (this module does not import the owner); pinned equal to
#: `paper_market_data`'s constants by tests/test_sw_book_family.py. Nothing
#: else changes: the deadline, the hold, the retry's 30 s bound and every
#: other read failure keep their meaning.
OWNER_CUT_REFUSALS = frozenset((
    "PAPER_MARKET_DATA_QUEUE_WAIT_EXCEEDED_THE_DEADLINE",
    "PAPER_DISCOVERY_READ_DEFERRED_DURING_VENUE_HOLD",
    "PAPER_COALESCED_READ_DEADLINE_EXCEEDED"))


def book_deadline_refusal(got: dict) -> bool:
    """The read was cut by the decision deadline: our own timeout, the
    venue request gate refusing to outlive the deadline it was given, or
    the paper market-data owner refusing to send it inside the deadline or
    the venue's hold (OWNER_CUT_REFUSALS) -- in no case a book that was
    read."""
    g = got or {}
    return (g.get("error") == G.R_BOOK_READ_DEADLINE
            or g.get("refused_by") == "OUR_REQUEST_GATE"
            or g.get("error") in OWNER_CUT_REFUSALS)


# ═════════════════════════════════════════════════════════════════════
# A RECORDED ENTER ALWAYS GETS ITS ORDER (P0 incident 2026-10-04)
# ═════════════════════════════════════════════════════════════════════
#
# THE DEFECT, measured in production on 191b299. The in-cycle hook bounded
# the WHOLE decision with asyncio.wait_for(VALUATION_HOOK_TIMEOUT_S = 8 s). A
# decision INSERTs its row first and only then -- for an ENTER -- runs the
# execution-intent hook, builds the canonical intent and submits the paper
# order. A deadline that fell after the INSERT cancelled the order sequence
# and left a recorded ENTER with no paper order and no PAPER_RISK_REFUSED
# finding: about one a day, 4% of the completed-game policy's ENTERs.
#
# THE RULE. The decision deadline bounds the DECISION: everything up to the
# INSERT of its row -- for an ENTER, up to the instant that INSERT starts
# (`enter_recorded`, called by `owed_order` just before it; RC6.2 rework).
# From then on the deadline no longer applies and the INSERT and the order
# sequence -- execution hook, canonical intent,
# paper order, in that canonical order, the paper order's fields read from
# the intent -- runs to completion, bounded only by ENTER_ORDER_GRACE_S so a
# wedged hook cannot hold the cycle for ever. Nothing is left running in the
# background: the decision's connection is never used by two coroutines at
# once, and a decision cut BEFORE its INSERT is cancelled exactly as before
# (it recorded nothing, so nothing is owed).
#
# THE BACKSTOP. Whatever still leaves a recorded ENTER without an order -- the
# grace exceeded, an outer cancellation (the reactive evaluation's own
# deadline), a process restart between the INSERT and the order -- becomes a
# named finding ENTER_WITHOUT_ORDER once the ENTER is ENTER_WITHOUT_ORDER_
# AFTER_S old (`step_enter_backstop`, every paper pass). SINCE RC6.2
# enter-integrity (`owed_order`, below) an outer cancellation no longer cuts
# the order sequence, and a sequence that cannot finish is named at once
# (ENTER_ORDER_ABANDONED, with its cause); the backstop is left with what no
# code path can name -- a process killed in that window. An ENTER whose order
# the paper risk check refused already carries its own finding
# (PAPER_RISK_REFUSED_THE_ORDER) and is not one of these. The backstop names;
# it never places a late order on a decision whose book and price are gone.

CTX_ENTER_RECORDED = "enter_recorded"
#: How long a RECORDED ENTER's order sequence may run past the decision
#: deadline. Above the measured tail (hook + intent + submit) by a wide
#: margin; below the paper pass budget's multiple that would wedge a cycle.
ENTER_ORDER_GRACE_S = 15.0
#: An ENTER this old with no order and no order refusal is a finding. Above
#: VALUATION_HOOK_TIMEOUT_S + ENTER_ORDER_GRACE_S, so an order still being
#: submitted is never named.
ENTER_WITHOUT_ORDER_AFTER_S = 60.0
ENTER_BACKSTOP_LOOKBACK_S = 6 * 3600.0
ENTER_BACKSTOP_MAX_PER_PASS = 50
F_ENTER_WITHOUT_ORDER = "ENTER_WITHOUT_ORDER"


class EnterOrderGraceExceeded(asyncio.TimeoutError):
    """A RECORDED ENTER whose order sequence outran ENTER_ORDER_GRACE_S after
    the decision deadline. A TimeoutError, so every caller that handled the
    old timeout still does; the backstop names the ENTER afterwards."""

    def __init__(self, decision_id, *, timeout_s, grace_s):
        super().__init__(
            "ENTER %s was owed but its row and paper order did not complete "
            "within %.1f s after the %.1f s decision deadline"
            % (decision_id, float(grace_s), float(timeout_s)))
        self.decision_id = decision_id
        self.timeout_s = float(timeout_s)
        self.grace_s = float(grace_s)


def enter_recorded(ctx: dict, decision_id: str) -> None:
    """FROM HERE THE ENTER'S ORDER IS OWED: `bounded_decision`'s decision
    deadline no longer cuts the decision. `owed_order` calls it as the ENTER
    row's INSERT is about to START (RC6.2 enter-integrity rework: the INSERT
    is the first step of the owed sequence, so a deadline that falls while
    it waits -- on the account row lock a concurrent ledger submit holds --
    cannot leave a committed ENTER that nothing owes an order). A no-op when
    the decision does not run under `bounded_decision` (the pass steps)."""
    ev = ctx.get(CTX_ENTER_RECORDED)
    if ev is not None:
        ctx["enter_recorded_decision_id"] = decision_id
        ev.set()


# ── THE OUTER CANCELLATION (enter-integrity, production 2026-10-06 .. 10-09)
#
# THE DEFECT, measured (research-sql rc62_enter_integrity_cut, run
# 37962750532): 13 PINNACLE_EXPLORATION_PAPER ENTERs (2026-10-06 20:04:21Z ..
# 2026-10-09 16:11:38Z) with neither a paper order nor a refusal, each named
# ENTER_WITHOUT_ORDER by the backstop. All 13 were decided in cycle; none has
# an evaluation attempt or a hook-failure row (the caller's bookkeeping never
# ran: a CancelledError went past it); 12 of 13 valuations were carried by a
# pinnapi reactive evaluation that ended TIMEOUT at its 12 s deadline
# (12.007 .. 12.937 s), 0.2 .. 10.2 s after the exploration decision began;
# 10 of 13 had written their execution intent (0.010 .. 0.083 s after the
# ENTER row), 3 had not. The reactive scheduler's `asyncio.timeout(deadline)`
# cancelled the evaluation; the CancelledError reached `bounded_decision`,
# whose `finally` cancelled the decision task in the middle of its order
# sequence -- the residual the backstop note above names ("an outer
# cancellation (the reactive evaluation's own deadline)"). The same cut
# applies to the paper pass's own hard timeout, and to every policy whose
# ENTER row is written before its order.
#
# THE RULE, completed. `owed_order` runs an ENTER's order sequence in its own
# task on the decision's connection and awaits it SHIELDED: a cancellation
# of the caller -- any caller, any deadline -- no longer cuts it. The caller
# waits (on the same connection, so nothing ever uses it twice) until the
# sequence finishes or ENTER_ORDER_GRACE_S since the ENTER became owed has
# passed, and then re-raises the cancellation: the caller is still
# cancelled, the order (or its named refusal) is recorded. A sequence that
# cannot finish -- the grace ran out, a second cancellation, an exception
# inside it -- is cancelled, waited for (bounded) and the ENTER is named AT
# ONCE with its cause: finding ENTER_ORDER_ABANDONED (subject = the decision,
# detail = cause, the stage the sequence reached, a ledger refusal it saw,
# elapsed). The backstop does not name it twice; it still names what no code
# path can (a process killed between the INSERT and the order). Nothing is
# placed late beyond the grace; no price, size, threshold or rule moves.
#
# THE REWORK (review of 952e9cb7, two defects reproduced on real Postgres):
#
#  * THE ENTER ROW'S OWN INSERT WAS OUTSIDE THE PROTECTED REGION. The INSERT
#    takes FOR KEY SHARE on paper_accounts (the FK), which waits behind the
#    ledger's `_lock` (SELECT ... FOR UPDATE) that every concurrent submit or
#    pass step holds. A deadline that fell during that wait cancelled the
#    client at once while the server went on and COMMITTED the row; the
#    decision id never reached owed_order, so only the 60 s backstop named
#    it (12 of 13 runs of the reviewer's lock-wait reproduction). NOW the
#    policy hands its INSERT to owed_order (`record`): the INSERT is the
#    FIRST step of the owed sequence, the decision deadline stops cutting as
#    it STARTS (`enter_recorded`), a duplicate is an outcome, and an INSERT
#    still cut (grace, second cancellation) is resolved on the same
#    connection -- whose next statement runs only once the server has
#    finished the cut one -- before the ENTER is named (an INSERT the server
#    did not commit owes nothing and is counted, not named).
#  * A SECOND CANCELLATION LET bounded_decision (and _abandon) RETURN WHILE
#    THE SEQUENCE STILL RAN ON THE CONNECTION: the caller's completion
#    audit, then its pool release, ran on a connection in use (InterfaceError
#    'another operation is in progress'; the pool terminated it mid-order;
#    the abandonment could not be written). NOW nothing returns while a task
#    it started still runs on the connection: every wait absorbs the
#    caller's further cancellations (and re-raises CancelledError once the
#    connection is free); a repeat cancellation reaching bounded_decision is
#    forwarded to owed_order, which abandons and names the ENTER, bounded by
#    ENTER_ABANDON_WAIT_S; a task that outlives even that has its connection
#    TERMINATED -- never handed back to the caller or its pool mid-statement
#    -- and the backstop names the ENTER.
F_ENTER_ORDER_ABANDONED = "ENTER_ORDER_ABANDONED"
C_GRACE_EXCEEDED = "CANCELLED_AND_THE_ORDER_GRACE_RAN_OUT"
C_CANCELLED_AGAIN = "CANCELLED_AGAIN_WHILE_THE_ORDER_WAS_OWED"
C_RAISED = "THE_ORDER_SEQUENCE_RAISED"
C_SEQUENCE_CANCELLED = "THE_ORDER_SEQUENCE_ITSELF_WAS_CANCELLED"
#: the owed sequence's first stage when owed_order runs the ENTER's INSERT
ST_ENTER_ROW_INSERTING = "ENTER_ROW_INSERTING"
#: How long a cancelled sequence (or the abandonment record) is waited for
#: before the next step: asyncpg completes a cancelled statement's cancel
#: request, a transaction rolls back. A task still running past it, and past
#: a second wait after its own cancellation, has its connection terminated.
ENTER_ABANDON_WAIT_S = 5.0
#: After a connection is terminated a statement on it fails at once; a task
#: still running past this is no longer on the connection (nothing can
#: collide with it) and is left to finish on its own, its result retrieved.
ENTER_TERMINATED_WAIT_S = 1.0
#: process counters. abandoned: owed ENTERs stopped before their outcome was
#: durable (named ENTER_ORDER_ABANDONED, or -- abandoned_unrecorded -- left
#: to the backstop); cut_before_the_enter_row: the INSERT itself was cut and
#: the server did not commit the row (nothing owed); outcome_durable_when_
#: cut: the order or its refusal was already durable (nothing to name).
OWED_ORDER_COUNTS: dict = {"completed_after_cancellation": 0,
                           "abandoned": 0, "abandoned_unrecorded": 0,
                           "cut_before_the_enter_row": 0,
                           "outcome_durable_when_cut": 0,
                           "connections_terminated": 0}


def owed_enter_overrun_bound_s() -> float:
    """THE MOST AN OWED ENTER CAN HOLD ITS CALLER PAST THE CALLER'S OWN
    CANCELLATION, every bound spent in full: the rest of the grace
    (ENTER_ORDER_GRACE_S, measured from the instant the ENTER became owed,
    which precedes the cancellation), the sequence's settle after its
    cancellation (wait, terminate, wait) and the abandonment record's
    (wait, cancel and wait, terminate, wait):
    15 + (5 + 1) + (5 + 5 + 1) = 32 s with the defaults. Measured ends are
    milliseconds; this is the pathology bound. A caller's own published
    worst case does not include it -- pinnapi_reactive.worst_case_job_s
    (24 s) and the paper pass's HARD_TIMEOUT_S can each be exceeded by up
    to this much while an owed ENTER is in flight."""
    return (ENTER_ORDER_GRACE_S + 3 * ENTER_ABANDON_WAIT_S
            + 2 * ENTER_TERMINATED_WAIT_S)


def _retrieve(task) -> None:
    """A finished task's exception is read, so it is never logged unread."""
    if task.done() and not task.cancelled():
        task.exception()


async def _wait_out(fut, timeout_s: float | None) -> int:
    """Wait for `fut` to finish -- never cancelling it -- for at most
    `timeout_s` seconds (None: until it finishes), ABSORBING the caller's
    cancellations meanwhile. Returns how many were absorbed: the caller
    re-raises CancelledError once the connection is no longer in use."""
    end = None if timeout_s is None else time.monotonic() + float(timeout_s)
    absorbed = 0
    while not fut.done():
        left = None if end is None else end - time.monotonic()
        if left is not None and left <= 0:
            break
        try:
            await asyncio.wait({fut}, timeout=left)
        except asyncio.CancelledError:
            absorbed += 1
    return absorbed


def _terminate(conn) -> bool:
    """Close the connection at once (asyncpg Connection.terminate): a task
    still running on it fails, and no later user -- the caller, its pool --
    can collide with it. Never raises."""
    fn = getattr(conn, "terminate", None)
    if fn is None:
        return False
    try:
        fn()
    except Exception:                                           # noqa: BLE001
        return False
    OWED_ORDER_COUNTS["connections_terminated"] += 1
    return True


async def _settle(conn, task, *, cancel_first: bool) -> tuple[int, bool]:
    """LET `task` (a coroutine on `conn`) FINISH BEFORE ANYONE ELSE USES THE
    CONNECTION, bounded. cancel_first: cancel it, then wait; otherwise wait,
    then cancel and wait again. Each wait is ENTER_ABANDON_WAIT_S with the
    caller's cancellations absorbed. A task still running after that has its
    connection terminated (and is waited for ENTER_TERMINATED_WAIT_S more).
    Returns (the cancellations absorbed, whether the connection was
    terminated). Its total is part of `owed_enter_overrun_bound_s`."""
    absorbed = 0
    if cancel_first and not task.done():
        task.cancel()
    absorbed += await _wait_out(task, ENTER_ABANDON_WAIT_S)
    if not task.done() and not cancel_first:
        task.cancel()
        absorbed += await _wait_out(task, ENTER_ABANDON_WAIT_S)
    terminated = False
    if not task.done():
        terminated = _terminate(conn)
        absorbed += await _wait_out(task, ENTER_TERMINATED_WAIT_S)
    if task.done():
        _retrieve(task)
    else:
        task.add_done_callback(_retrieve)
    return absorbed, terminated


async def owed_order(conn, ctx: dict, *, decision_id: str, strategy: str,
                     sequence, record=None, duplicate=None,
                     grace_s: float | None = None) -> dict:
    """RUN AN ENTER'S ORDER SEQUENCE SO THAT IT ENDS IN AN ORDER, A NAMED
    REFUSAL OR A NAMED ABANDONMENT -- never in nothing.

    `record` (every paper policy passes it): `async def () -> id | None`,
    the ENTER row's INSERT ... ON CONFLICT DO NOTHING RETURNING. It is the
    FIRST step of the owed sequence: the ENTER is owed (`enter_recorded`)
    from the instant its INSERT starts, and None (another writer recorded
    this decision first) returns `duplicate()` with nothing owed. Without
    `record` the caller has already written the row.

    `sequence` is `async def (progress) -> rec`; it sets progress["stage"]
    as it goes and progress["outcome"] once the order or its named refusal
    is durable ("ORDER" / "ORDER_REFUSED"). Returns the sequence's result;
    re-raises the caller's cancellation (after the sequence finished or was
    abandoned) and the sequence's own exception (after the abandonment
    record). Never returns or raises while the sequence still runs on
    `conn` (see `_settle`)."""
    grace = ENTER_ORDER_GRACE_S if grace_s is None else float(grace_s)
    t0 = time.monotonic()
    progress: dict = {"stage": ("ENTER_RECORDED" if record is None
                                else ST_ENTER_ROW_INSERTING),
                      "outcome": None, "enter_row": record is None}
    # THE DECISION DEADLINE STOPS CUTTING HERE: before the INSERT starts
    enter_recorded(ctx, decision_id)

    async def run():
        if record is not None:
            if await record() is None:
                progress.update(stage="DUPLICATE", outcome="DUPLICATE")
                return (duplicate() if duplicate is not None else
                        {"decision_id": decision_id, "duplicate": True})
            progress.update(stage="ENTER_RECORDED", enter_row=True)
        return await sequence(progress)

    task = asyncio.ensure_future(run())
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        cause = None
        if not task.done():
            left = grace - (time.monotonic() - t0)
            if left > 0:
                try:
                    await asyncio.wait({task}, timeout=left)
                except asyncio.CancelledError:
                    cause = C_CANCELLED_AGAIN
        if task.done() and not task.cancelled() and \
                task.exception() is None:
            # THE ORDER (or its named refusal) IS RECORDED; the caller's
            # cancellation stands.
            OWED_ORDER_COUNTS["completed_after_cancellation"] += 1
            raise
        err = None
        if task.done() and not task.cancelled():
            err = type(task.exception()).__name__
        if cause is None:
            cause = (C_GRACE_EXCEEDED if not task.done() else
                     C_SEQUENCE_CANCELLED if task.cancelled() else C_RAISED)
        await _abandon(conn, ctx, task, progress, decision_id=decision_id,
                       strategy=strategy, t0=t0, grace=grace, error=err,
                       cause=cause)
        raise
    except Exception as exc:
        absorbed = await _abandon(
            conn, ctx, task, progress, decision_id=decision_id,
            strategy=strategy, t0=t0, grace=grace, cause=C_RAISED,
            error=type(exc).__name__)
        if absorbed:
            # cancelled while the abandonment was written: the caller's
            # cancellation, now that the connection is free
            raise asyncio.CancelledError() from exc
        raise


async def _abandon(conn, ctx: dict, task, progress: dict, *, decision_id,
                   strategy, t0: float, grace: float, cause: str,
                   error=None) -> int:
    """Stop the sequence and NAME the ENTER: one finding
    ENTER_ORDER_ABANDONED with its cause, unless the sequence already made
    its outcome durable or its INSERT never committed. Returns only once
    neither the sequence nor the record is running on `conn` (bounded;
    `_settle`), with the number of the caller's cancellations it absorbed.
    Never raises."""
    absorbed, terminated = await _settle(conn, task, cancel_first=True)
    if not task.done() or terminated:
        # the sequence outlived its cancellation: its connection is closed,
        # nothing can be written on it; the backstop names the ENTER
        OWED_ORDER_COUNTS["abandoned"] += 1
        OWED_ORDER_COUNTS["abandoned_unrecorded"] += 1
        return absorbed
    if progress.get("outcome") is not None:
        OWED_ORDER_COUNTS["outcome_durable_when_cut"] += 1
        return absorbed

    async def name_it() -> str:
        # THE ENTER'S INSERT WAS CUT (enter_row unset). On this connection
        # the next statement runs only once the server has finished the cut
        # one (asyncpg waits out its cancel request): the row is committed,
        # or it never will be.
        if not progress.get("enter_row") and await conn.fetchval(
                "SELECT verdict FROM paper_decisions WHERE decision_id=$1",
                decision_id) is None:
            return "NO_ENTER_ROW"
        await _finding(conn, ctx, kind=F_ENTER_ORDER_ABANDONED,
                       subject=decision_id, severity="WARNING", detail={
                           "decision_id": decision_id, "strategy": strategy,
                           "cause": cause, "error": error,
                           "stage_reached": progress.get("stage"),
                           "order_refusal": progress.get("order_refusal"),
                           "elapsed_since_enter_recorded_s": round(
                               time.monotonic() - t0, 3),
                           "grace_s": grace,
                           "why": ("a recorded ENTER's order sequence could "
                                   "not finish: it was cancelled and named "
                                   "at once. No order was placed late."),
                           "named_by": "paper_derek.owed_order"})
        return "NAMED"

    write = asyncio.ensure_future(name_it())
    got, terminated = await _settle(conn, write, cancel_first=False)
    absorbed += got
    outcome = (write.result() if write.done() and not write.cancelled()
               and write.exception() is None else None)
    if outcome == "NO_ENTER_ROW":
        OWED_ORDER_COUNTS["cut_before_the_enter_row"] += 1
        return absorbed
    OWED_ORDER_COUNTS["abandoned"] += 1
    if outcome != "NAMED":
        OWED_ORDER_COUNTS["abandoned_unrecorded"] += 1
    return absorbed


async def bounded_decision(make, ctx: dict, *, timeout_s: float,
                           grace_s: float | None = None) -> dict:
    """RUN `make(ctx)` -- one decision -- WITH ITS DEADLINE ON THE DECISION
    ONLY. Before its ENTER is owed (`enter_recorded`, as the ENTER row's
    INSERT starts) the decision is cancelled at `timeout_s` and
    asyncio.TimeoutError is raised, as before. After it, the INSERT and the
    order sequence complete, up to `grace_s` more (EnterOrderGraceExceeded
    beyond that). `make` receives a shallow copy of `ctx` carrying the
    signal; shared sub-dicts (books_by_slug) stay shared.

    NEVER RETURNS OR RAISES WHILE THE DECISION STILL RUNS (on the caller's
    connection): a cancellation of the caller cancels the decision and
    waits for it; a REPEAT cancellation is absorbed and -- once the ENTER is
    owed -- forwarded, so owed_order abandons and names it (bounded by
    ENTER_ABANDON_WAIT_S); CancelledError is re-raised once it is done."""
    grace = ENTER_ORDER_GRACE_S if grace_s is None else float(grace_s)
    ev = asyncio.Event()
    run_ctx = dict(ctx)
    run_ctx[CTX_ENTER_RECORDED] = ev
    t0 = time.monotonic()
    task = asyncio.ensure_future(make(run_ctx))
    absorbed = 0
    try:
        try:
            return await asyncio.wait_for(asyncio.shield(task),
                                          float(timeout_s))
        except asyncio.TimeoutError:
            if task.done():
                # finished (or failed -- including with its own TimeoutError)
                # at the deadline: its own outcome, exactly as before
                return task.result()
            if not ev.is_set():
                raise                   # cut before its INSERT: nothing owed
        # THE ENTER IS OWED: its INSERT and order complete. Same task, same
        # connection, no second coroutine on it.
        did = run_ctx.get("enter_recorded_decision_id")
        try:
            rec = await asyncio.wait_for(asyncio.shield(task), grace)
        except asyncio.TimeoutError:
            raise EnterOrderGraceExceeded(did, timeout_s=timeout_s,
                                          grace_s=grace) from None
        if rec.get("duplicate"):
            return rec
        return dict(rec, order_after_decision_deadline={
            "decision_id": did, "decision_deadline_s": float(timeout_s),
            "grace_s": grace,
            "elapsed_s": round(time.monotonic() - t0, 3),
            "why": ("the ENTER was owed before the decision deadline; its "
                    "row and order sequence were allowed to complete")})
    finally:
        if not task.done():
            task.cancel()
            while not task.done():
                try:
                    await asyncio.wait({task})
                except asyncio.CancelledError:
                    absorbed += 1
                    if ev.is_set():
                        # forwarded: owed_order stops waiting out its grace,
                        # abandons and names the ENTER on the connection it
                        # still owns (bounded). Before the ENTER is owed the
                        # decision is only waited for: its own cleanup (a
                        # savepoint's rollback) is never cut.
                        task.cancel()
        _retrieve(task)
        if absorbed:
            raise asyncio.CancelledError()


ENTER_BACKSTOP_SQL = """
    SELECT d.decision_id, d.session_id, d.strategy, d.valuation_id,
           d.us_market_slug, d.fixture, d.policy_version,
           extract(epoch FROM d.decided_at)::float8 AS decided_at
      FROM paper_decisions d
     WHERE d.account_id = $1 AND d.verdict = 'ENTER'
       AND d.decided_at <= to_timestamp($2)
       AND d.decided_at > to_timestamp($3)
       AND NOT EXISTS (SELECT 1 FROM paper_orders o
                        WHERE o.decision_id = d.decision_id)
       AND NOT EXISTS (SELECT 1 FROM paper_audrey_findings f
                        WHERE f.account_id = d.account_id
                          AND f.kind = ANY($4::text[])
                          AND f.subject = d.decision_id)
     ORDER BY d.decided_at
     LIMIT $5
"""


async def step_enter_backstop(conn, ctx: dict) -> dict:
    """EVERY RECORDED ENTER HAS AN ORDER, OR A NAMED FINDING. Each ENTER of
    this account at least ENTER_WITHOUT_ORDER_AFTER_S old (within the
    lookback) with no paper order naming it and no order refusal becomes ONE
    finding ENTER_WITHOUT_ORDER (idempotent per decision). Records only:
    no order is placed, cancelled or changed."""
    at = float(ctx["clock"]()) if ctx.get("clock") else float(ctx["now"])
    rows = await conn.fetch(
        ENTER_BACKSTOP_SQL, ctx["account_id"],
        at - ENTER_WITHOUT_ORDER_AFTER_S, at - ENTER_BACKSTOP_LOOKBACK_S,
        # an ENTER owed_order already named (ENTER_ORDER_ABANDONED, with
        # its cause) is not named twice
        [R_ORDER_REFUSED, F_ENTER_WITHOUT_ORDER, F_ENTER_ORDER_ABANDONED],
        ENTER_BACKSTOP_MAX_PER_PASS)
    named = []
    for r in rows:
        did = r["decision_id"]
        fid = "paperfind:" + hashlib.sha256(
            ("%s:%s:%s" % (r["session_id"], F_ENTER_WITHOUT_ORDER, did))
            .encode()).hexdigest()[:24]
        detail = {
            "decision_id": did, "strategy": r["strategy"],
            "policy_version": r["policy_version"],
            "valuation_id": r["valuation_id"],
            "us_market_slug": r["us_market_slug"], "fixture": r["fixture"],
            "decided_at": r["decided_at"],
            "age_at_detection_s": round(at - float(r["decided_at"]), 3),
            "threshold_s": ENTER_WITHOUT_ORDER_AFTER_S,
            "order_refusal_recorded": False,
            "why": ("a recorded ENTER has no paper order naming it and no "
                    "order refusal: its order sequence was cut after the "
                    "decision row was written (grace exceeded, an outer "
                    "cancellation or a process restart). Nothing is placed "
                    "late: the decision's book and price are gone"),
            "named_by": "paper_derek.step_enter_backstop"}
        got = await conn.fetchval(
            "INSERT INTO paper_audrey_findings (finding_id, session_id, "
            " account_id, found_at, kind, severity, subject, detail) "
            "VALUES ($1,$2,$3,$4,$5,'WARNING',$6,$7::jsonb) "
            "ON CONFLICT DO NOTHING RETURNING finding_id",
            fid, r["session_id"], ctx["account_id"], L._ts(at),
            F_ENTER_WITHOUT_ORDER, did, json.dumps(detail, default=str))
        if got is not None:
            named.append(did)
    return {"enter_without_order": len(named), "decision_ids": named[:10],
            "examined": len(rows),
            "threshold_s": ENTER_WITHOUT_ORDER_AFTER_S}
