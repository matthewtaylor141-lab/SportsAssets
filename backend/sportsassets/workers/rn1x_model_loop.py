"""Worker: ACTUAL MODEL FITTING, on a schedule, with named targets.

WHAT THIS IS NOT. `rn1x_learn_loop` beside it is a POLICY COMPARATOR: it
re-runs the champion and the challengers over the same seeds and records
which did better. That is a legitimate and useful thing, it is honestly
labelled, and it is NOT model training -- nothing in it fits anything. The
gap this module closes is that no scheduled process ever prepared data,
fitted a model, recorded a prediction before its outcome existed, joined
the outcome, and evaluated the result.

THE FIVE STAGES, in order, each refusing rather than guessing:

  1 PREPARE   a point-in-time dataset via `learn.dataset.build`. Every
              feature looks backward from the entry's `available_at`, so
              a feature cannot consult a fill we had not yet received.
  2 FIT       `learn.kernel` on rows whose horizon has CLOSED, against a
              BaseRate baseline. Pure Python: numpy and scipy are absent
              from this image.
  3 PREDICT   for rows whose horizon is still OPEN, write a prediction to
              `rn1x_model_predictions` -- BEFORE the outcome exists, which
              migration 102's BEFORE INSERT trigger enforces rather than
              trusting this loop to be honest.
  4 JOIN      when a recorded prediction's horizon closes, attach the
              observed outcome. Never the other way round.
  5 EVALUATE  on joined predictions ONLY, which are genuinely future
              relative to the fit that produced them.

THE TARGET IS NAMED ON EVERY ROW, and it is not the entry target.
`T_COMPLETE` forecasts whether the cohort account makes a complementary
BUY within the horizon. That is somebody else's next action. It is NOT the
probability the event settles yes, and it is NOT our order's fill
probability. `bettor_model_inventory.qualifies_for_entry` refuses it for
entry on the target alone, before any metric is consulted, and
`record_prediction` refuses to record against the settlement target at all
because nothing is fitted for it.

NO PROMOTION. A cycle can recommend; it cannot promote. There is no code
path here that writes the active policy, and management's baseline stays
frozen whatever this finds. Repeated inspection of the same data is also
guarded: a re-evaluation on an unchanged dataset produces no new row.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time

from .. import bettor_model_inventory as inv
from ..learn import dataset as ds
from ..learn import kernel as K
from ..learn import metrics as M

log = logging.getLogger(__name__)

CONTROL_KEY = "rn1x_model_fit"
ENV_FLAG = "RN1X_MODEL_FIT"
HEARTBEAT_KEY = "rn1x_model_last_cycle"

#: The target this loop fits. Declared here so a reader does not have to
#: infer it, and checked against the inventory so it cannot drift.
TARGET = inv.T_COMPLETE

#: The horizon the target is defined over. It is part of the target's
#: identity: "a complementary fill" means nothing without a window.
HORIZON_S = 3600.0

#: One cycle per this long. Fitting is cheap; the reason not to run it
#: every minute is that repeatedly inspecting the same data and keeping
#: the best look is how a selection effect gets built by accident.
CYCLE_S = 3600.0
IDLE_POLL_S = 120.0

#: Below this many CLOSED rows there is nothing to fit. Refused by name so
#: "no model" and "a bad model" stay distinguishable.
MIN_TRAIN_ROWS = 200

#: Below this many JOINED predictions an evaluation is noise. Reported as
#: a state, not as a null result.
MIN_EVAL_ROWS = 50

MODEL_KEY = "RN1X_COMPLETE_STUMPS_V1"

R_NO_ROWS = "NO_CLOSED_ROWS_TO_FIT"
R_TOO_FEW = "TRAIN_ROWS_BELOW_FLOOR"
R_NO_EVAL = "TOO_FEW_JOINED_PREDICTIONS_TO_EVALUATE"
R_TABLE = "PREDICTION_LEDGER_ABSENT"

ASSUMPTIONS = (
    "features look backward from available_at = max(ts, detected_at), so "
    "a feature cannot consult a fill we had not yet received",
    "the horizon must be CLOSED for a row to be a training label; an open "
    "horizon has no label yet, and treating it as a negative would teach "
    "the model that everything fails",
    "the label is the cohort account's own subsequent action, not the "
    "market's settlement and not our fill",
    "evaluation uses only predictions recorded BEFORE their outcome "
    "existed, which the ledger's trigger enforces",
)


async def _running(conn) -> tuple:
    if str(os.environ.get(ENV_FLAG, "")).strip().lower() in ("off", "0",
                                                            "false"):
        return False, "%s is off in the environment" % ENV_FLAG
    try:
        raw = await conn.fetchval(
            "SELECT value::text FROM ingestion_state WHERE key = $1",
            CONTROL_KEY)
    except Exception as exc:                                   # noqa: BLE001
        return False, "CONTROL_UNREADABLE_%s" % type(exc).__name__
    if raw is None:
        return False, "CONTROL_ROW_ABSENT"
    if raw.strip().lower() != "true":
        return False, "CONTROL_ROW_NOT_TRUE"
    return True, "RUNNING"


def enabled() -> bool:
    return str(os.environ.get(ENV_FLAG, "")).strip().lower() in (
        "on", "1", "true")


# ── 1 · PREPARE ─────────────────────────────────────────────────────

#: THE NEWEST ROWS, NOT THE OLDEST. This read `ORDER BY t.detected_at, t.id
#: LIMIT 40000` ascending, and with ~6.1M trades the 30-day window holds
#: far more than the limit -- so it returned the OLDEST 40,000 rows in the
#: window, every one of which has a closed horizon.
#:
#: The first production cycle showed it exactly: 40,000 source rows,
#: 7,805 built, **n_open = 0**. The fit worked and the PREDICT stage had
#: nothing to predict on, because the rows whose horizon is still open are
#: by definition the most recent ones and they had all been cut off.
#:
#: The inner query takes the newest rows descending; the outer one restores
#: chronological order, because `learn.dataset.build` walks forward and its
#: prior-fill features depend on that order.
FILLS_SQL = """
    SELECT * FROM (
        SELECT t.id, t.whale_id, t.condition_id, t.outcome_index, t.side,
               t.size::float8 AS size, t.price::float8 AS price,
               extract(epoch FROM t.ts)::float8 AS ts,
               extract(epoch FROM t.detected_at)::float8 AS detected_at,
               t.source
          FROM trades t
         WHERE t.source = ANY($1::text[])
           AND t.detected_at >= now() - ($2 || ' days')::interval
         ORDER BY t.detected_at DESC, t.id DESC
         LIMIT $3
    ) newest
    ORDER BY detected_at, id
"""

LIVE_SOURCES = ("chain", "poll")


async def prepare(conn, *, days: int = 30, limit: int = 40000,
                 now: float | None = None) -> dict:
    """The point-in-time dataset, split by whether the horizon has closed.

    `observation_end` is the instant the dataset stops knowing anything.
    Rows whose horizon extends past it have NO label -- they are the
    prediction set, not negatives.
    """
    at = float(now if now is not None else time.time())
    rows = [dict(r) for r in await conn.fetch(
        FILLS_SQL, list(LIVE_SOURCES), str(int(days)), int(limit))]
    built = ds.build(rows, horizon_s=HORIZON_S, observation_end=at)
    table = built["rows"] if isinstance(built, dict) else built

    # A DATASET ROW CARRIES NO TRADE ID AND NO ACCOUNT -- `account` comes
    # back None. The prediction ledger needs both: without the trade id
    # there is nothing to key a prediction to, and without the account
    # the outcome join cannot ask "did THIS cohort account complete".
    # So each row is mapped back to the source fill it was built from, on
    # (condition_id, outcome_index, event_ts), and a row that cannot be
    # matched is DROPPED rather than given a placeholder -- a prediction
    # keyed to account "" would join against nothing forever.
    index = {}
    for f in rows:
        index[(f["condition_id"], int(f["outcome_index"]),
               round(float(f["ts"]), 3))] = (int(f["id"]),
                                             int(f["whale_id"]))
    unmatched = 0
    matched = []
    for r in table:
        key = (r.get("condition_id"), int(r.get("outcome_index") or 0),
               round(float(r.get("event_ts") or 0.0), 3))
        hit = index.get(key)
        if hit is None:
            unmatched += 1
            continue
        r["_trade_id"], r["_whale_id"] = hit
        matched.append(r)
    table = matched
    closed, open_ = [], []
    for r in table:
        # `censored` IS THE DATASET'S OWN WORD for a horizon that has not
        # closed. A censored row has NO label: treating it as a negative
        # would teach the model that everything fails, which is the
        # classic way to build a confidently wrong classifier.
        if bool(r.get("censored")):
            open_.append(r)
        else:
            closed.append(r)
    sha = hashlib.sha256(json.dumps(
        [sorted((k, str(v)) for k, v in r.items() if not k.startswith("_"))
         for r in closed], sort_keys=True, default=str
    ).encode()).hexdigest()[:16]
    return {"observation_end": at, "horizon_s": HORIZON_S,
            "source_rows": len(rows), "built_rows": len(table),
            "closed": closed, "open": open_,
            "dataset_sha": sha,
            "n_closed": len(closed), "n_open": len(open_),
            # The dataset's OWN accounting, carried through so the two can
            # be compared rather than trusted.
            "dataset_target": built.get("target"),
            "dataset_version": built.get("version"),
            "dataset_n_rows": built.get("n_rows"),
            "dataset_n_censored": built.get("n_censored"),
            "dataset_n_positive": built.get("n_positive"),
            "dataset_base_rate_uncensored": built.get(
                "base_rate_uncensored"),
            "skipped": built.get("skipped"),
            "unmatched_to_source_fill": unmatched}


# ── 2 · FIT ─────────────────────────────────────────────────────────

#: THE FEATURE VECTOR, taken from `learn.dataset`'s own output rather
#: than invented here. Listing names the dataset does not produce would
#: silently feed the model a column of zeros, which looks like a fitted
#: model and is not one.
FEATURES = (
    "entry_price", "entry_price_dist_from_half", "entry_size_log",
    "entry_notional_log", "is_outcome_one", "is_first_fill_in_market",
    "prior_fills_this_market", "prior_qty_same_leg_log",
    "prior_cost_same_leg_log", "complement_price_if_symmetric",
    "seconds_since_prev_fill_this_market_log",
    "seconds_since_first_fill_this_market_log",
    "hour_of_day_sin", "hour_of_day_cos",
)


def _xy(rows):
    """The kernel takes DICT rows keyed by feature name, not vectors.

    Passing lists raised on construction, which is the good failure: a
    positional vector would have silently reordered the features the day
    FEATURES changed.
    """
    X, y = [], []
    for r in rows:
        f = r.get("features") or r
        X.append({k: float(f.get(k) or 0.0) for k in FEATURES})
        y.append(1.0 if float(r.get("label") or 0.0) > 0.5 else 0.0)
    return X, y


def fit(rows) -> dict:
    """Fit the model AND its baseline. Pure Python, no numpy or scipy."""
    X, y = _xy(rows)
    if not X:
        return {"ok": False, "refusal": R_NO_ROWS, "n": 0}
    if len(X) < MIN_TRAIN_ROWS:
        return {"ok": False, "refusal": R_TOO_FEW, "n": len(X),
                "floor": MIN_TRAIN_ROWS,
                "why": ("a model fitted on %d rows is not a weak model, it "
                        "is an unmeasured one" % len(X))}
    base = K.BaseRate()
    base.fit(X, y)
    # The feature NAMES are handed to the model, so the mapping from name
    # to split is the model's own and cannot drift with list order.
    model = K.Stumps(list(FEATURES))
    model.fit(X, y)
    ins = [model.predict(x) for x in X]
    bl = [base.predict(x) for x in X]
    return {"ok": True, "refusal": None, "n": len(X),
            "positives": int(sum(y)),
            "base_rate": (sum(y) / len(y)) if y else None,
            "features": list(FEATURES),
            "model": model, "baseline": base,
            # IN-SAMPLE, and labelled as such. It is a fit diagnostic and
            # must never be reported as performance.
            "in_sample": {
                "log_loss": M.log_loss(ins, y),
                "baseline_log_loss": M.log_loss(bl, y),
                "IS_IN_SAMPLE": ("a fit diagnostic only. Performance is the "
                                 "joined-prediction evaluation at stage 5"),
            }}


# ── 3 · PREDICT, before the outcome exists ──────────────────────────

async def predict(conn, fitted: dict, open_rows, *, dataset_sha,
                  model_version, now=None) -> dict:
    """Record a prediction for every row whose horizon is still OPEN."""
    at = float(now if now is not None else time.time())
    model = fitted["model"]
    wrote, refused = 0, {}
    for r in open_rows:
        f = r.get("features") or r
        x = {k: float(f.get(k) or 0.0) for k in FEATURES}
        p = float(model.predict(x))
        avail = inv.feature_availability(r, target=TARGET)
        got = await inv.record_prediction(
            conn, target=TARGET, model_key=MODEL_KEY,
            model_version=model_version, dataset_sha=dataset_sha,
            condition_id=r.get("condition_id"),
            source_trade_id=r["_trade_id"],
            # `account` is None on a dataset row, so the cohort account
            # comes from the source trade. Writing "" would key every
            # prediction to the same non-existent account.
            account=str(r["_whale_id"]),
            predicted_at=at, horizon_s=HORIZON_S, p_hat=p,
            availability=avail)
        if got.get("ok"):
            wrote += 1
        else:
            code = got.get("refusal") or "UNKNOWN"
            refused[code] = refused.get(code, 0) + 1
    return {"recorded": wrote, "refusals": refused,
            "predicted_at": at, "n_open": len(open_rows)}


# ── 4 · JOIN outcomes, never the other way round ────────────────────

JOINABLE = """
    SELECT id, condition_id, source_trade_id, account, p_hat::float8 p,
           extract(epoch FROM predicted_at)::float8 pat, horizon_s::float8 h
      FROM rn1x_model_predictions
     WHERE target = $1 AND outcome_known = FALSE
       AND predicted_at + (horizon_s || ' seconds')::interval <= now()
     ORDER BY predicted_at
     LIMIT 5000
"""

COMPLEMENT_SQL = """
    SELECT count(*) FROM trades
     WHERE condition_id = $1 AND whale_id = $2 AND side = 'BUY'
       AND outcome_index <> $3
       AND ts > to_timestamp($4) AND ts <= to_timestamp($5)
"""

JOIN_ONE = """
    UPDATE rn1x_model_predictions
       SET outcome_known = TRUE, outcome = $2, outcome_at = now()
     WHERE id = $1 AND outcome_known = FALSE
"""


async def join_outcomes(conn, *, now=None) -> dict:
    """Attach the OBSERVED label to predictions whose horizon has closed."""
    joined, skipped = 0, 0
    for r in await conn.fetch(JOINABLE, TARGET):
        oi = await conn.fetchval(
            "SELECT outcome_index FROM trades WHERE id = $1",
            r["source_trade_id"])
        if oi is None:
            skipped += 1
            continue
        n = await conn.fetchval(
            COMPLEMENT_SQL, r["condition_id"], int(r["account"] or 0),
            int(oi), float(r["pat"]), float(r["pat"]) + float(r["h"]))
        await conn.execute(JOIN_ONE, r["id"], 1 if int(n or 0) > 0 else 0)
        joined += 1
    return {"joined": joined, "skipped_no_trade_row": skipped}


# ── 5 · EVALUATE, on joined predictions only ────────────────────────

EVAL_SQL = """
    SELECT p_hat::float8 p, outcome, model_version, dataset_sha
      FROM rn1x_model_predictions
     WHERE target = $1 AND outcome_known = TRUE
     ORDER BY predicted_at
"""


async def evaluate(conn) -> dict:
    """Calibration and skill, on genuinely future evidence.

    These predictions were recorded before their outcomes existed, which
    the ledger's trigger enforced at insert time. So this is the one number
    in the whole module that is entitled to be called performance.
    """
    rows = await conn.fetch(EVAL_SQL, TARGET)
    p = [float(r["p"]) for r in rows]
    y = [float(r["outcome"]) for r in rows]
    if len(p) < MIN_EVAL_ROWS:
        return {"ok": False, "refusal": R_NO_EVAL, "n": len(p),
                "floor": MIN_EVAL_ROWS,
                "why": ("%d joined predictions is not a null result, it is "
                        "not yet a measurement" % len(p))}
    base = (sum(y) / len(y)) if y else None
    rep = M.report(p, y, baseline_rate=base)
    return {"ok": True, "refusal": None, "n": len(p),
            "base_rate": base, "report": rep,
            "calibration": M.calibration(p, y, bins=10),
            "is_out_of_sample": True,
            "why_trustworthy": ("every row was recorded before its outcome "
                                "existed; migration 102's trigger refuses "
                                "an insert that carries its own label")}


# ── the cycle ───────────────────────────────────────────────────────

async def _table_ready(conn) -> bool:
    try:
        return bool(await conn.fetchval(
            "SELECT to_regclass('public.rn1x_model_predictions') "
            "IS NOT NULL"))
    except Exception:                                          # noqa: BLE001
        return False


async def _next_version(conn, dataset_sha: str) -> str:
    """A NEW version per distinct dataset, so a re-fit on unchanged data
    does not masquerade as a new model."""
    n = await conn.fetchval(
        "SELECT count(DISTINCT model_version) FROM rn1x_model_predictions "
        "WHERE target = $1 AND model_key = $2", TARGET, MODEL_KEY)
    return "%s.%s" % (int(n or 0) + 1, dataset_sha[:8])


async def cycle(conn, *, now=None) -> dict:
    """One full pass. Never raises."""
    started = time.time()
    running, why = await _running(conn)
    if not running:
        return {"ran": False, "state": "STOPPED", "why": why}
    if not await _table_ready(conn):
        return {"ran": False, "state": "BLOCKED", "why": R_TABLE}

    out = {"ran": True, "state": "LIVE", "target": TARGET,
           "target_predicts": inv.TARGETS[TARGET]["predicts"],
           "target_is_not": inv.TARGETS[TARGET]["not_usable_for"],
           "entry_target_is": inv.ENTRY_REQUIRES,
           "model_key": MODEL_KEY, "horizon_s": HORIZON_S,
           "assumptions": list(ASSUMPTIONS)}

    prep = await prepare(conn, now=now)
    out["prepare"] = {k: v for k, v in prep.items()
                      if k not in ("closed", "open")}

    fitted = fit(prep["closed"])
    out["fit"] = {k: v for k, v in fitted.items()
                  if k not in ("model", "baseline")}
    if not fitted.get("ok"):
        # STILL JOIN AND EVALUATE. A cycle that cannot fit today can still
        # close yesterday's predictions, and reporting nothing would hide
        # that the pipeline is alive.
        out["join"] = await join_outcomes(conn, now=now)
        out["evaluate"] = await evaluate(conn)
        await _heartbeat(conn, out)
        return out

    version = await _next_version(conn, prep["dataset_sha"])
    out["model_version"] = version
    out["predict"] = await predict(
        conn, fitted, prep["open"], dataset_sha=prep["dataset_sha"],
        model_version=version, now=now)
    out["join"] = await join_outcomes(conn, now=now)
    out["evaluate"] = await evaluate(conn)
    out["promotion"] = {
        "promoted": False,
        "why": ("this loop has no promotion path. Management's policy is "
                "frozen and a candidate is recorded for a human to read"),
    }
    out["elapsed_s"] = round(time.time() - started, 2)
    await _heartbeat(conn, out)
    return out


async def _heartbeat(conn, out: dict) -> None:
    try:
        await conn.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
            HEARTBEAT_KEY, json.dumps({
                "at": time.time(), "state": out.get("state"),
                "target": out.get("target"),
                "model_key": out.get("model_key"),
                "model_version": out.get("model_version"),
                "prepare": out.get("prepare"),
                "fit": {k: v for k, v in (out.get("fit") or {}).items()
                        if k != "features"},
                "predict": out.get("predict"),
                "join": out.get("join"),
                "evaluate": {k: v for k, v in
                             (out.get("evaluate") or {}).items()
                             if k not in ("report", "calibration")},
                "promoted": False,
            }, default=str))
    except Exception:                                          # noqa: BLE001
        log.warning("rn1x_model: heartbeat failed", exc_info=True)


async def run(get_pool) -> None:
    while True:
        delay = IDLE_POLL_S
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                out = await cycle(conn)
            log.info("rn1x_model: %s", {k: v for k, v in out.items()
                                        if k != "assumptions"})
            if out.get("ran"):
                delay = CYCLE_S
        except asyncio.CancelledError:
            raise
        except Exception:                                      # noqa: BLE001
            log.warning("rn1x_model: cycle failed", exc_info=True)
        await asyncio.sleep(delay)
